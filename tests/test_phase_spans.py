"""Coordinator 阶段 span + AgentLoop llm_call/skill span。全程 mock，不打付费 LLM。"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from core.agent_loop import AgentLoop
from core.llm_client import LLMResponse, ToolCall
from core.tracing import end_trace, get_trace, start_trace
from safety.triage import EmergencyTriage
from swarm.swarm_coordinator import SwarmCoordinator
from validation.guardrail import OutputGuardrail


CLEAN_ANSWER = "多休息多喝水。以上信息仅供参考，如有疑虑请及时就医。"


def _load_saved_trace(session_id: str) -> dict:
    path = Path(os.environ["MEDIX_TRACES_DIR"]) / f"{session_id}.jsonl"
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    return json.loads(lines[-1])


def _bare_coordinator() -> SwarmCoordinator:
    """跳过 Redis / Mem0 / 真实 LLM，只保留 process() 需要的属性。"""
    with patch.object(SwarmCoordinator, "__init__", lambda self, *a, **k: None):
        coord = SwarmCoordinator()
    coord.enable_swarm = True
    coord.triage = EmergencyTriage(llm_client=None)
    coord.guardrail = OutputGuardrail(llm_client=None)
    coord.lead_agent = MagicMock()
    coord.consultation_agent = MagicMock()
    coord.consultation_agent.agent_id = "consultation_agent"
    coord.consultation_agent.process = AsyncMock(
        return_value={
            "answer": CLEAN_ANSWER,
            "agent_id": "consultation_agent",
            "disclaimer": "仅供参考",
            "suggestions": [],
        }
    )
    coord.diagnostic_agent = MagicMock()
    coord.diagnostic_agent.agent_id = "diagnostic_agent"
    coord.research_agent = MagicMock()
    coord.research_agent.agent_id = "research_agent"
    coord.worker_pool = [
        coord.consultation_agent,
        coord.diagnostic_agent,
        coord.research_agent,
    ]
    for worker in coord.worker_pool:
        worker.attach_shared_context = MagicMock()
        worker.process_subtask = AsyncMock(return_value={"answer": "ok"})
    coord.session_manager = MagicMock()
    coord.short_term_memory = MagicMock()
    coord.long_term_memory = MagicMock()
    coord.long_term_memory.search_similar_sessions = MagicMock(return_value=[])
    coord.long_term_memory.add_session_summary = MagicMock()
    return coord


class CoordinatorPhaseSpanTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._old = os.environ.get("MEDIX_TRACES_DIR")
        os.environ["MEDIX_TRACES_DIR"] = self.tmp.name

    async def asyncTearDown(self):
        if self._old is None:
            os.environ.pop("MEDIX_TRACES_DIR", None)
        else:
            os.environ["MEDIX_TRACES_DIR"] = self._old
        self.tmp.cleanup()

    async def test_emergency_fail_fast_only_has_triage_span(self):
        coord = _bare_coordinator()
        result = await coord.process(
            "我爸昏迷了叫不醒",
            session_id="sess-em-span",
            trace_id="emergenc0123",
        )
        self.assertTrue(result.get("emergency"))
        self.assertFalse(result.get("swarm_enabled"))
        self.assertEqual(result["trace"]["trace_id"], "emergenc0123")
        self.assertEqual(result["trace"]["span_count"], 1)
        # 急症不过护栏、不走 Mem0 / 分解
        coord.lead_agent.assess_and_decompose.assert_not_called()
        coord.long_term_memory.search_similar_sessions.assert_not_called()
        coord.consultation_agent.process.assert_not_called()

        saved = _load_saved_trace("sess-em-span")
        names = [span["name"] for span in saved["spans"]]
        self.assertEqual(names, ["triage"])
        self.assertEqual(saved["spans"][0]["kind"], "phase")
        self.assertTrue(saved["spans"][0]["meta"]["emergency"])
        self.assertEqual(saved["spans"][0]["meta"]["category"], "consciousness")

    async def test_single_agent_records_triage_mem0_decompose_guardrail(self):
        coord = _bare_coordinator()
        coord.lead_agent.assess_and_decompose = AsyncMock(
            return_value={
                "subtasks": [{"assigned_agent": "consultation_agent", "type": "consult"}]
            }
        )
        result = await coord.process(
            "感冒了怎么办",
            session_id="sess-single-span",
            trace_id="single12span",
        )
        self.assertFalse(result.get("emergency"))
        self.assertFalse(result.get("swarm_enabled"))
        self.assertEqual(result["answer"], CLEAN_ANSWER)
        self.assertEqual(result["sources"], [])
        self.assertIn("trace", result)

        saved = _load_saved_trace("sess-single-span")
        names = [span["name"] for span in saved["spans"]]
        self.assertEqual(names, ["triage", "mem0_search", "decompose", "guardrail"])
        self.assertTrue(all(span["kind"] == "phase" for span in saved["spans"]))
        self.assertFalse(saved["spans"][0]["meta"]["emergency"])
        self.assertEqual(saved["spans"][1]["meta"]["hits"], 0)
        self.assertEqual(saved["spans"][2]["meta"]["subtasks"], 1)
        self.assertFalse(saved["spans"][3]["meta"]["rewritten"])
        coord.consultation_agent.process.assert_awaited()

    async def test_swarm_records_synthesize_and_guardrail(self):
        coord = _bare_coordinator()
        coord.lead_agent.assess_and_decompose = AsyncMock(
            return_value={
                "subtasks": [
                    {"assigned_agent": "diagnostic_agent", "type": "dx"},
                    {"assigned_agent": "consultation_agent", "type": "consult"},
                ]
            }
        )
        coord.lead_agent.create_subtasks = MagicMock(return_value=[])
        coord.lead_agent.synthesize_results = AsyncMock(return_value=CLEAN_ANSWER)

        result = await coord.process(
            "头痛一周还恶心，需要就医吗？",
            session_id="sess-swarm-span",
            trace_id="swarm12spanx",
        )
        self.assertTrue(result.get("swarm_enabled"))
        self.assertEqual(result["answer"], CLEAN_ANSWER)
        coord.lead_agent.synthesize_results.assert_awaited()

        saved = _load_saved_trace("sess-swarm-span")
        names = [span["name"] for span in saved["spans"]]
        self.assertEqual(
            names,
            ["triage", "mem0_search", "decompose", "synthesize", "guardrail"],
        )
        self.assertEqual(saved["spans"][2]["meta"]["subtasks"], 2)


class FakeLoopLLM:
    def __init__(self):
        self.calls = 0

    async def chat_with_tools(self, messages, tools=None, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="search_knowledge",
                        arguments={"query": "感冒"},
                    )
                ],
                finish_reason="tool_calls",
            )
        return LLMResponse(
            content=CLEAN_ANSWER,
            tool_calls=[],
            finish_reason="stop",
        )

    def create_tool_message(self, tool_call_id, tool_name, result):
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": tool_name,
            "content": str(result),
        }


class FakeAgent:
    agent_id = "consultation_agent"
    config = {"temperature": 0.0}

    def __init__(self):
        self.llm_client = FakeLoopLLM()

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
        return {"success": True, "tool": tool_name, "arguments": arguments}


class AgentLoopSpanTests(unittest.IsolatedAsyncioTestCase):
    async def test_records_llm_call_and_skill_spans(self):
        loop = AgentLoop(max_iterations=5, max_tool_calls=2)
        agent = FakeAgent()
        token = start_trace("sess-loop-span", trace_id="looptoken12a")
        try:
            result = await loop.run(
                agent,
                {"question": "感冒了怎么办"},
                session_id="sess-loop-span",
                record_memory=False,
            )
            spans = list(get_trace().spans)
        finally:
            end_trace(token)

        self.assertEqual(result.get("answer"), CLEAN_ANSWER)
        names = [span.name for span in spans]
        kinds = [span.kind for span in spans]
        self.assertEqual(names, ["llm_call", "skill:search_knowledge", "llm_call"])
        self.assertEqual(kinds, ["llm", "skill", "llm"])
        self.assertEqual(spans[0].meta.get("agent"), "consultation_agent")
        self.assertEqual(spans[0].meta.get("iteration"), 1)
        self.assertTrue(spans[1].meta.get("ok"))
        self.assertEqual(spans[2].meta.get("iteration"), 2)


if __name__ == "__main__":
    unittest.main()
