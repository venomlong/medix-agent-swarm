"""AgentLoop：max_tool_calls 截断、迭代异常回滚、工具失败错误传播。全程 FakeLLMClient。"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

# unittest discover 把 tests/ 放进 sys.path；pytest 默认不一定。
_TESTS_DIR = str(Path(__file__).resolve().parent)
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

from conftest import FakeLLMClient  # noqa: E402

from core.agent_loop import AgentLoop  # noqa: E402

FINAL_ANSWER = "根据已检索信息，建议多休息多喝水。以上信息仅供参考。"


class FakeAgent:
    agent_id = "consultation_agent"
    config = {"temperature": 0.0}

    def __init__(self, llm_client: FakeLLMClient, tool_error: Exception | None = None):
        self.llm_client = llm_client
        self.tool_error = tool_error
        self.tool_runs: list[dict] = []

    def get_tools_for_llm(self):
        return [
            {
                "type": "function",
                "function": {"name": "search_knowledge", "parameters": {}},
            }
        ]

    def get_system_prompt(self):
        return "你是咨询助手。"

    def format_user_input(self, input_data):
        return input_data.get("question") or ""

    async def execute_tool(self, tool_name, arguments):
        self.tool_runs.append({"name": tool_name, "arguments": arguments})
        if self.tool_error is not None:
            raise self.tool_error
        return {"success": True, "tool": tool_name, "arguments": arguments}


class AgentLoopMockTests(unittest.IsolatedAsyncioTestCase):
    async def test_max_tool_calls_forces_tool_choice_none(self):
        """两轮 tool_calls 后撞上限，下一轮必须以 tool_choice='none' 出最终答案。"""
        fake = FakeLLMClient(
            [
                FakeLLMClient.tool_calls(call_id="c1"),
                FakeLLMClient.tool_calls(call_id="c2"),
                FakeLLMClient.text(FINAL_ANSWER),
            ]
        )
        loop = AgentLoop(max_iterations=10, max_tool_calls=1)
        agent = FakeAgent(fake)

        result = await loop.run(
            agent,
            {"question": "感冒了怎么办"},
            session_id="sess-loop-cap",
            record_memory=False,
        )

        self.assertEqual(result.get("answer"), FINAL_ANSWER)
        self.assertEqual(len(fake.calls), 3)
        self.assertEqual(fake.calls[0]["tool_choice"], "auto")
        self.assertEqual(fake.calls[1]["tool_choice"], "auto")
        self.assertEqual(fake.calls[2]["tool_choice"], "none")
        # 第二轮 tool_calls 只用来触发上限，不应再执行 Skill
        self.assertEqual(len(agent.tool_runs), 1)
        last_roles = [m.get("role") for m in fake.calls[2]["messages"]]
        self.assertEqual(last_roles[-1], "user")
        self.assertIn("已完成 1 次信息检索", fake.calls[2]["messages"][-1]["content"])
        self.assertEqual(fake.remaining(), 0)

    async def test_iteration_exception_rolls_back_messages(self):
        """第二次 LLM 调用抛错后回滚；重试时 messages 长度与失败当轮快照一致。"""
        fake = FakeLLMClient(
            [
                FakeLLMClient.tool_calls(call_id="c1"),
                RuntimeError("boom on second llm call"),
                FakeLLMClient.text(FINAL_ANSWER),
            ]
        )
        loop = AgentLoop(max_iterations=6, max_tool_calls=2)
        agent = FakeAgent(fake)

        result = await loop.run(
            agent,
            {"question": "咳嗽怎么办"},
            session_id="sess-loop-rollback",
            record_memory=False,
        )

        self.assertEqual(result.get("answer"), FINAL_ANSWER)
        self.assertEqual(len(fake.calls), 3)
        failed = fake.calls[1]
        retried = fake.calls[2]
        self.assertEqual(failed["message_count"], retried["message_count"])
        self.assertEqual(
            [m.get("role") for m in failed["messages"]],
            [m.get("role") for m in retried["messages"]],
        )
        # 失败当轮没有残留不完整的 assistant(tool_calls)
        roles = [m.get("role") for m in retried["messages"]]
        if "assistant" in roles:
            assistant_idx = roles.index("assistant")
            self.assertLess(assistant_idx + 1, len(roles))
            self.assertEqual(roles[assistant_idx + 1], "tool")

    async def test_tool_failure_propagates_as_tool_message(self):
        """Skill 抛错不得拆协议：必须回填 tool 消息并把 error 交给下一轮 LLM。"""
        fake = FakeLLMClient(
            [
                FakeLLMClient.tool_calls(call_id="c-fail"),
                FakeLLMClient.text(FINAL_ANSWER),
            ]
        )
        loop = AgentLoop(max_iterations=5, max_tool_calls=2)
        agent = FakeAgent(fake, tool_error=RuntimeError("kb down"))

        result = await loop.run(
            agent,
            {"question": "高血压饮食"},
            session_id="sess-loop-tool-err",
            record_memory=False,
        )

        self.assertEqual(result.get("answer"), FINAL_ANSWER)
        self.assertEqual(len(agent.tool_runs), 1)
        followup = fake.calls[1]["messages"]
        tool_msgs = [m for m in followup if m.get("role") == "tool"]
        self.assertEqual(len(tool_msgs), 1)
        payload = json.loads(tool_msgs[0]["content"])
        self.assertFalse(payload.get("success"))
        self.assertIn("kb down", payload.get("error", ""))

    async def test_max_iterations_forces_final_answer(self):
        """打满迭代仍无最终文本时，禁用 tools 再要一次总结，并标 max_iterations_reached。"""
        fake = FakeLLMClient(
            [
                FakeLLMClient.tool_calls(call_id="c1"),
                FakeLLMClient.tool_calls(call_id="c2"),
                FakeLLMClient.text("迭代用尽后的总结。仅供参考。"),
            ]
        )
        loop = AgentLoop(max_iterations=2, max_tool_calls=10)
        agent = FakeAgent(fake)

        result = await loop.run(
            agent,
            {"question": "需要分步检索"},
            session_id="sess-loop-max-iter",
            record_memory=False,
        )

        self.assertEqual(result.get("warning"), "max_iterations_reached")
        self.assertEqual(result.get("answer"), "迭代用尽后的总结。仅供参考。")
        self.assertEqual(len(fake.calls), 3)
        self.assertIsNone(fake.calls[2]["tools"])
        self.assertEqual(fake.calls[2]["messages"][-1]["role"], "user")
        self.assertIn("最终的答复", fake.calls[2]["messages"][-1]["content"])


if __name__ == "__main__":
    unittest.main()
