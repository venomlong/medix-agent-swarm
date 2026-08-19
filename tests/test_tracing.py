"""请求级 tracing：ContextVar 隔离、span 结构、usage/cost、JSONL 脱敏。

不打付费 LLM，不依赖 FastAPI / Coordinator 初始化。
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.tracing import (
    GLOBAL_USAGE,
    PRICING_DEFAULT,
    add_span,
    end_trace,
    get_trace,
    logger_ctx,
    patch_log_trace,
    record_llm_usage,
    record_span,
    save_trace,
    start_trace,
    trace_path,
)
from webapi.bridge import map_answer_done


class _UsageGuard:
    def setUp(self):
        self._usage_snap = dict(GLOBAL_USAGE)

    def tearDown(self):
        GLOBAL_USAGE.clear()
        GLOBAL_USAGE.update(self._usage_snap)


class SpanStructureTests(_UsageGuard, unittest.TestCase):
    def test_add_span_without_trace_is_silent(self):
        add_span("llm_call", "llm", 1.0, 1.2, {"agent": "x"})
        self.assertIsNone(get_trace())

    def test_span_fields_and_duration(self):
        token = start_trace("sess-span", trace_id="abcdef123456")
        try:
            add_span(
                "llm_call",
                "llm",
                10.0,
                10.25,
                {"agent": "consultation_agent", "iteration": 1},
            )
            add_span("skill:search_knowledge", "skill", 10.25, 10.40, {"ok": True})
            trace = get_trace()
            self.assertIsNotNone(trace)
            self.assertEqual(trace.trace_id, "abcdef123456")
            self.assertEqual(trace.session_id, "sess-span")
            self.assertEqual(len(trace.spans), 2)
            first = trace.spans[0].to_dict()
            self.assertEqual(first["name"], "llm_call")
            self.assertEqual(first["kind"], "llm")
            self.assertEqual(first["start"], 10.0)
            self.assertEqual(first["end"], 10.25)
            self.assertAlmostEqual(first["duration_ms"], 250.0)
            self.assertEqual(first["meta"]["agent"], "consultation_agent")
            self.assertEqual(trace.spans[1].name, "skill:search_knowledge")
            self.assertEqual(trace.spans[1].kind, "skill")
        finally:
            end_trace(token)
        self.assertIsNone(get_trace())

    def test_record_span_context_manager_records_failure(self):
        token = start_trace("sess-cm")
        try:
            with self.assertRaises(RuntimeError):
                with record_span("skill:boom", "skill", agent="diagnostic_agent"):
                    raise RuntimeError("nope")
            span = get_trace().spans[0]
            self.assertEqual(span.name, "skill:boom")
            self.assertFalse(span.meta.get("ok"))
        finally:
            end_trace(token)

    def test_start_trace_generates_12_char_id(self):
        token = start_trace("sess-gen")
        try:
            tid = get_trace().trace_id
            self.assertEqual(len(tid), 12)
            self.assertTrue(all(c in "0123456789abcdef" for c in tid))
        finally:
            end_trace(token)

    def test_end_trace_none_is_safe(self):
        end_trace(None)


class UsageAndCostTests(_UsageGuard, unittest.TestCase):
    def test_add_usage_and_default_cost(self):
        token = start_trace("sess-cost")
        try:
            with patch(
                "core.tracing._pricing",
                return_value={"input": 2.0, "output": 8.0},
            ):
                trace = get_trace()
                trace.add_usage(1_000_000, 500_000)
                self.assertEqual(trace.prompt_tokens, 1_000_000)
                self.assertEqual(trace.completion_tokens, 500_000)
                self.assertEqual(trace.llm_calls, 1)
                self.assertAlmostEqual(trace.cost(), 6.0)
                summary = trace.summary()
                self.assertEqual(summary["trace_id"], trace.trace_id)
                self.assertEqual(summary["total_tokens"], 1_500_000)
                self.assertEqual(summary["span_count"], 0)
                self.assertIn("elapsed", summary)
                self.assertAlmostEqual(summary["cost"], 6.0)
        finally:
            end_trace(token)

    def test_record_llm_usage_writes_trace_and_global(self):
        before_calls = GLOBAL_USAGE["llm_calls"]
        token = start_trace("sess-usage")
        try:
            with patch(
                "core.tracing._pricing",
                return_value=dict(PRICING_DEFAULT),
            ):
                record_llm_usage(100, 50)
                record_llm_usage(10, 5)
            trace = get_trace()
            self.assertEqual(trace.prompt_tokens, 110)
            self.assertEqual(trace.completion_tokens, 55)
            self.assertEqual(trace.llm_calls, 2)
            self.assertEqual(GLOBAL_USAGE["llm_calls"], before_calls + 2)
            self.assertGreaterEqual(GLOBAL_USAGE["prompt_tokens"], 110)
        finally:
            end_trace(token)

    def test_record_llm_usage_without_trace_still_counts_global(self):
        before = GLOBAL_USAGE["llm_calls"]
        record_llm_usage(1, 1)
        self.assertEqual(GLOBAL_USAGE["llm_calls"], before + 1)
        self.assertIsNone(get_trace())


class IsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_tasks_do_not_leak(self):
        async def worker(label: str):
            token = start_trace(f"sess-{label}", trace_id=label.ljust(12, "0")[:12])
            try:
                add_span(label, "phase", 1.0, 1.1, {"id": label})
                await asyncio.sleep(0.01)
                trace = get_trace()
                return trace.trace_id, [s.name for s in trace.spans]
            finally:
                end_trace(token)

        a, b = await asyncio.gather(worker("A"), worker("B"))
        self.assertEqual(a[0], "A00000000000")
        self.assertEqual(b[0], "B00000000000")
        self.assertEqual(a[1], ["A"])
        self.assertEqual(b[1], ["B"])

    async def test_child_tasks_share_request_trace(self):
        token = start_trace("sess-parent", trace_id="parenttrace1")
        try:

            async def skill(name: str):
                add_span(f"skill:{name}", "skill", 1.0, 1.2)

            await asyncio.gather(skill("search_knowledge"), skill("assess_risk"))
            names = {s.name for s in get_trace().spans}
            self.assertEqual(names, {"skill:search_knowledge", "skill:assess_risk"})
            self.assertEqual(get_trace().trace_id, "parenttrace1")
        finally:
            end_trace(token)


class SaveTraceTests(_UsageGuard, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self._old = os.environ.get("MEDIX_TRACES_DIR")
        os.environ["MEDIX_TRACES_DIR"] = self.tmp.name

    def tearDown(self):
        if self._old is None:
            os.environ.pop("MEDIX_TRACES_DIR", None)
        else:
            os.environ["MEDIX_TRACES_DIR"] = self._old
        self.tmp.cleanup()
        super().tearDown()

    def test_save_appends_jsonl_and_masks_pii_in_meta(self):
        token = start_trace("sess-jsonl")
        try:
            add_span(
                "llm_call",
                "llm",
                1.0,
                1.2,
                {"note": "患者手机13812345678，邮箱foo@bar.com"},
            )
            save_trace()
        finally:
            end_trace(token)

        path = Path(self.tmp.name) / "sess-jsonl.jsonl"
        self.assertTrue(path.exists())
        line = path.read_text(encoding="utf-8").strip()
        self.assertNotIn("13812345678", line)
        self.assertNotIn("foo@bar.com", line)
        self.assertIn("1**********", line)
        self.assertIn("f***@***", line)
        self.assertIn("llm_call", line)
        self.assertNotIn("我胸口痛得要命", line)

    def test_save_without_trace_is_silent(self):
        save_trace()
        self.assertEqual(list(Path(self.tmp.name).glob("*.jsonl")), [])

    def test_trace_path_sanitizes_session_id(self):
        path = trace_path(r"..\evil/sess")
        self.assertEqual(path.parent, Path(self.tmp.name))
        self.assertNotIn("..", path.name)


class AnswerDonePassthroughTests(unittest.TestCase):
    def test_map_answer_done_reads_trace_summary(self):
        payload = map_answer_done(
            {
                "answer": "多喝水，仅供参考",
                "swarm_enabled": False,
                "sources": [],
                "trace": {
                    "trace_id": "cafe1234beef",
                    "total_tokens": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "llm_calls": 0,
                    "cost": 0.0,
                    "span_count": 2,
                    "elapsed": 0.8,
                },
            },
            "sess-ans",
            0.8,
        )
        self.assertEqual(payload["trace_id"], "cafe1234beef")
        self.assertEqual(payload["usage"]["total_tokens"], 0)
        self.assertEqual(payload["usage"]["llm_calls"], 0)
        self.assertEqual(payload["sources"], [])

    def test_map_answer_done_trace_id_none_when_missing(self):
        payload = map_answer_done(
            {"answer": "ok", "emergency": True, "swarm_enabled": False},
            "sess-em",
            0.2,
        )
        self.assertIsNone(payload["trace_id"])
        self.assertEqual(payload["usage"]["total_tokens"], 0)


class LoggerTraceCtxTests(unittest.TestCase):
    def test_logger_ctx_dash_without_trace(self):
        self.assertIsNone(get_trace())
        self.assertEqual(logger_ctx(), {"trace": "-"})

    def test_logger_ctx_uses_current_trace_id(self):
        token = start_trace("sess-log", trace_id="cafebabeface")
        try:
            self.assertEqual(logger_ctx(), {"trace": "cafebabeface"})
            record = {"extra": {"trace": "-"}}
            patch_log_trace(record)
            self.assertEqual(record["extra"]["trace"], "cafebabeface")
        finally:
            end_trace(token)
        self.assertEqual(logger_ctx(), {"trace": "-"})

    def test_log_line_carries_trace_and_masks_pii(self):
        from loguru import logger

        from core.log_privacy import install_log_privacy

        install_log_privacy()
        captured = []
        hid = logger.add(
            lambda message: captured.append(str(message).strip()),
            format="{extra[trace]} | {message}",
            level="INFO",
        )
        token = start_trace("sess-pii", trace_id="trace12token")
        try:
            logger.info("Processing question (session=s1): 手机13812345678 感冒了")
        finally:
            end_trace(token)
            logger.remove(hid)

        self.assertEqual(len(captured), 1)
        line = captured[0]
        self.assertTrue(line.startswith("trace12token |"))
        self.assertNotIn("13812345678", line)
        self.assertIn("1**********", line)
        self.assertIn("感冒了", line)

    def test_log_without_trace_uses_dash(self):
        from loguru import logger

        from core.log_privacy import install_log_privacy

        install_log_privacy()
        self.assertIsNone(get_trace())
        captured = []
        hid = logger.add(
            lambda message: captured.append(str(message).strip()),
            format="{extra[trace]} | {message}",
            level="INFO",
        )
        try:
            logger.info("idle")
        finally:
            logger.remove(hid)
        self.assertEqual(captured[0], "- | idle")


if __name__ == "__main__":
    unittest.main()
