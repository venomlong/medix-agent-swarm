"""输出侧护栏：规则层 + mock LLM 重写流程，不打真实 API。"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from swarm.events import EventType
from swarm.shared_context import reset_event_listener, set_event_listener
from validation.auto_fixer import AutoFixer
from validation.guardrail import (
    OutputGuardrail,
    apply_guardrail_to_result,
)
from validation import safety_log
from webapi.bridge import map_answer_done


class FakeLLM:
    def __init__(self, replies=None, error=None):
        self.replies = list(replies or [])
        self.error = error
        self.calls = []

    async def chat(self, messages, **kwargs):
        self.calls.append({"messages": messages, "kwargs": kwargs})
        if self.error is not None:
            raise self.error
        if not self.replies:
            raise RuntimeError("FakeLLM has no remaining replies")
        return self.replies.pop(0)


class GuardrailRuleTests(unittest.TestCase):
    def setUp(self):
        self.guardrail = OutputGuardrail(llm_client=None)

    def test_certainty_diagnosis_hit(self):
        hits = self.guardrail.check_rules("根据症状，你得的是心肌炎，需要休息。")
        types = [v["type"] for v in hits]
        self.assertIn("certainty_diagnosis", types)
        self.assertTrue(any("心肌炎" in v["evidence"] for v in hits))

    def test_dosage_instruction_hit(self):
        hits = self.guardrail.check_rules("请口服阿司匹林，每次100mg，连用三天。")
        self.assertTrue(any(v["type"] == "dosage_instruction" for v in hits))

    def test_dangerous_advice_hit(self):
        hits = self.guardrail.check_rules("这种情况不用就医，在家观察即可。")
        self.assertTrue(any(v["type"] == "dangerous_advice" for v in hits))

    def test_clean_answer_miss(self):
        hits = self.guardrail.check_rules(
            "感冒期间多休息、多喝水。以上信息仅供参考，如有疑虑请及时就医。"
        )
        self.assertEqual(hits, [])

    def test_just_a_cold_is_not_certainty_diagnosis(self):
        # 「就是感冒」是口语，不应被确定性诊断规则误伤
        hits = self.guardrail.check_rules("听起来就是感冒，多喝水就好。")
        self.assertFalse(any(v["type"] == "certainty_diagnosis" for v in hits))


class GuardrailReviewTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(
            prefix="safety_log_", suffix=".jsonl", delete=False
        )
        self._tmp.close()
        self._old = os.environ.get("MEDIX_SAFETY_LOG_PATH")
        os.environ["MEDIX_SAFETY_LOG_PATH"] = self._tmp.name

    def tearDown(self):
        if self._old is None:
            os.environ.pop("MEDIX_SAFETY_LOG_PATH", None)
        else:
            os.environ["MEDIX_SAFETY_LOG_PATH"] = self._old
        Path(self._tmp.name).unlink(missing_ok=True)

    async def test_clean_answer_skips_llm(self):
        fake = FakeLLM(replies=["should not be used"])
        guardrail = OutputGuardrail(llm_client=fake)
        verdict = await guardrail.review_and_fix(
            "感冒了怎么办",
            "多休息，多喝水。仅供参考。",
            session_id="s-clean",
        )
        self.assertTrue(verdict.passed)
        self.assertFalse(verdict.rewritten)
        self.assertEqual(verdict.action, "pass")
        self.assertEqual(fake.calls, [])

    async def test_hit_then_llm_rewrite(self):
        fake = FakeLLM(
            replies=["症状可能提示心肌炎，需由医生鉴别。以上仅供参考，请及时就医。"]
        )
        guardrail = OutputGuardrail(llm_client=fake)
        verdict = await guardrail.review_and_fix(
            "胸口不舒服",
            "你得的是心肌炎",
            session_id="s-rewrite",
        )
        self.assertTrue(verdict.passed)
        self.assertTrue(verdict.rewritten)
        self.assertEqual(verdict.action, "rewrite")
        self.assertEqual(len(fake.calls), 1)
        self.assertNotIn("你得的是心肌炎", verdict.final_answer)

    async def test_rewrite_still_violating_uses_regex_fallback(self):
        fake = FakeLLM(replies=["你得的是心肌炎，肯定是这个病。"])
        guardrail = OutputGuardrail(llm_client=fake)
        verdict = await guardrail.review_and_fix(
            "胸口不舒服",
            "你得的是心肌炎",
            session_id="s-fallback",
        )
        self.assertEqual(verdict.action, "regex_fallback")
        self.assertTrue(verdict.rewritten)
        self.assertFalse(any(
            v["type"] == "certainty_diagnosis"
            for v in guardrail.check_rules(verdict.final_answer)
        ))

    async def test_llm_error_uses_regex_fallback(self):
        fake = FakeLLM(error=RuntimeError("network down"))
        guardrail = OutputGuardrail(llm_client=fake)
        verdict = await guardrail.review_and_fix(
            "要不要吃药",
            "请口服阿司匹林每次100mg，不用就医。",
            session_id="s-error",
        )
        self.assertEqual(verdict.action, "regex_fallback")
        self.assertTrue(verdict.rewritten)
        self.assertNotIn("每次100mg", verdict.final_answer)
        self.assertNotIn("不用就医", verdict.final_answer)

    async def test_no_llm_client_regex_fallback(self):
        guardrail = OutputGuardrail(llm_client=None)
        verdict = await guardrail.review_and_fix(
            "确诊了吗",
            "确诊为高血压，肯定是这个病。",
            session_id="s-nolm",
        )
        self.assertEqual(verdict.action, "regex_fallback")
        self.assertNotIn("确诊为", verdict.final_answer)
        self.assertNotIn("肯定是", verdict.final_answer)

    async def test_apply_emits_event_and_fills_result(self):
        captured = []
        token = set_event_listener(lambda event: captured.append(event))
        try:
            fake = FakeLLM(replies=["可能与心肌炎有关，请就医。仅供参考。"])
            guardrail = OutputGuardrail(llm_client=fake)
            result = {"answer": "你得的是心肌炎"}
            verdict = await apply_guardrail_to_result(
                guardrail, "胸口痛", result, session_id="s-evt"
            )
            self.assertTrue(result["guardrail"]["triggered"])
            self.assertEqual(result["guardrail"]["action"], verdict.action)
            self.assertEqual(result["answer"], verdict.final_answer)
            self.assertEqual(len(captured), 1)
            self.assertEqual(captured[0].type, EventType.GUARDRAIL_TRIGGERED)
            self.assertTrue(captured[0].data.get("violations"))
        finally:
            reset_event_listener(token)

    async def test_apply_clean_does_not_emit(self):
        captured = []
        token = set_event_listener(lambda event: captured.append(event))
        try:
            guardrail = OutputGuardrail(llm_client=AsyncMock())
            result = {"answer": "多喝水，仅供参考。"}
            await apply_guardrail_to_result(
                guardrail, "感冒了", result, session_id="s-silent"
            )
            self.assertNotIn("guardrail", result)
            self.assertEqual(captured, [])
            guardrail.llm_client.chat.assert_not_called()
        finally:
            reset_event_listener(token)


class SafetyLogTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(
            prefix="safety_log_", suffix=".jsonl", delete=False
        )
        self._tmp.close()
        self._old = os.environ.get("MEDIX_SAFETY_LOG_PATH")
        os.environ["MEDIX_SAFETY_LOG_PATH"] = self._tmp.name

    def tearDown(self):
        if self._old is None:
            os.environ.pop("MEDIX_SAFETY_LOG_PATH", None)
        else:
            os.environ["MEDIX_SAFETY_LOG_PATH"] = self._old
        Path(self._tmp.name).unlink(missing_ok=True)

    def test_record_and_get_records_newest_first(self):
        safety_log.record("免责声明", "补了免责", session_id="a", source="auto_fixer")
        safety_log.record("certainty_diagnosis", "命中诊断断言", session_id="b", source="guardrail")
        rows = safety_log.get_records(limit=10)
        self.assertGreaterEqual(len(rows), 2)
        self.assertEqual(rows[0]["kind"], "certainty_diagnosis")
        self.assertEqual(rows[0]["source"], "guardrail")
        self.assertEqual(rows[1]["source"], "auto_fixer")

    def test_pii_masked_in_log(self):
        safety_log.record(
            kind="note",
            detail="患者手机 13812345678，身份证 110101199001011234，邮箱 a@b.com",
            session_id="s-pii",
            source="guardrail",
        )
        rows = safety_log.get_records(limit=1)
        self.assertEqual(len(rows), 1)
        detail = rows[0]["detail"]
        self.assertNotIn("13812345678", detail)
        self.assertNotIn("110101199001011234", detail)
        self.assertNotIn("a@b.com", detail)
        self.assertIn("1**********", detail)

    def test_auto_fixer_also_persists(self):
        fixer = AutoFixer()
        out = fixer.fix_output("今天有点不舒服", ["add_disclaimer"])
        self.assertIn("免责", out)
        rows = safety_log.get_records(limit=5)
        self.assertTrue(any(r.get("kind") == "免责声明" for r in rows))
        self.assertTrue(any(r.get("source") == "auto_fixer" for r in rows))


class GuardrailBridgeTests(unittest.TestCase):
    def test_map_answer_done_passthrough_guardrail(self):
        payload = map_answer_done(
            {
                "answer": "可能提示心肌炎，请就医。",
                "guardrail": {
                    "triggered": True,
                    "violations": [{"type": "certainty_diagnosis", "evidence": "你得的是心肌炎"}],
                    "rewritten": True,
                    "action": "rewrite",
                },
                "swarm_enabled": False,
            },
            "sess-g",
            1.2,
        )
        self.assertTrue(payload["guardrail"]["triggered"])
        self.assertEqual(payload["guardrail"]["action"], "rewrite")
        self.assertEqual(payload["body"], "可能提示心肌炎，请就医。")


if __name__ == "__main__":
    unittest.main()
