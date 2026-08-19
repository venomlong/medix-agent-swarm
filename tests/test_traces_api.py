"""T2.4：GET /api/traces/{session_id} 与 /api/stats 累计 token/cost。

临时目录 mock JSONL，不打付费 LLM，不启动 Coordinator。
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.tracing import (
    GLOBAL_USAGE,
    add_span,
    end_trace,
    global_usage_snapshot,
    load_traces,
    save_trace,
    start_trace,
)
from webapi.app import session_traces, stats


class _UsageGuard:
    def setUp(self):
        self._usage_snap = dict(GLOBAL_USAGE)

    def tearDown(self):
        GLOBAL_USAGE.clear()
        GLOBAL_USAGE.update(self._usage_snap)


class _TracesDirGuard(_UsageGuard):
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

    def _write_jsonl(self, session_id: str, rows: list) -> Path:
        path = Path(self.tmp.name) / f"{session_id}.jsonl"
        with path.open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        return path


class LoadTracesTests(_TracesDirGuard, unittest.TestCase):
    def test_missing_file_returns_empty(self):
        self.assertEqual(load_traces("no-such-session"), [])

    def test_empty_session_id_returns_empty(self):
        self.assertEqual(load_traces(""), [])
        self.assertEqual(load_traces("   "), [])

    def test_reads_jsonl_in_append_order(self):
        self._write_jsonl(
            "sess-a",
            [
                {
                    "trace_id": "aaaaaaaaaaaa",
                    "session_id": "sess-a",
                    "spans": [{"name": "triage", "kind": "phase"}],
                    "total_tokens": 10,
                },
                {
                    "trace_id": "bbbbbbbbbbbb",
                    "session_id": "sess-a",
                    "spans": [{"name": "decompose", "kind": "phase"}],
                    "total_tokens": 20,
                },
            ],
        )
        traces = load_traces("sess-a")
        self.assertEqual(len(traces), 2)
        self.assertEqual(traces[0]["trace_id"], "aaaaaaaaaaaa")
        self.assertEqual(traces[1]["trace_id"], "bbbbbbbbbbbb")
        self.assertEqual(traces[0]["spans"][0]["name"], "triage")

    def test_skips_malformed_and_non_object_lines(self):
        path = Path(self.tmp.name) / "sess-bad.jsonl"
        path.write_text(
            "\n".join(
                [
                    '{"trace_id": "okokokokokok"}',
                    "not-json",
                    "[1, 2]",
                    '{"trace_id": "okokokokok02"}',
                    "",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        traces = load_traces("sess-bad")
        self.assertEqual([t["trace_id"] for t in traces], ["okokokokokok", "okokokokok02"])

    def test_saved_trace_roundtrip_keeps_masked_pii(self):
        token = start_trace("sess-pii")
        try:
            add_span(
                "llm_call",
                "llm",
                1.0,
                1.2,
                {"note": "手机13812345678"},
            )
            save_trace()
        finally:
            end_trace(token)

        traces = load_traces("sess-pii")
        self.assertEqual(len(traces), 1)
        blob = json.dumps(traces[0], ensure_ascii=False)
        self.assertNotIn("13812345678", blob)
        self.assertIn("1**********", blob)
        self.assertEqual(traces[0]["spans"][0]["name"], "llm_call")


class SessionTracesEndpointTests(_TracesDirGuard, unittest.IsolatedAsyncioTestCase):
    async def test_returns_traces_and_count(self):
        self._write_jsonl(
            "sess-api",
            [{"trace_id": "cafebabeface", "spans": [{"name": "triage", "kind": "phase"}]}],
        )
        payload = await session_traces("sess-api")
        self.assertEqual(payload["session_id"], "sess-api")
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["traces"][0]["trace_id"], "cafebabeface")
        self.assertEqual(payload["traces"][0]["spans"][0]["name"], "triage")

    async def test_missing_session_is_empty_list_not_404(self):
        payload = await session_traces("missing-session")
        self.assertEqual(payload, {"session_id": "missing-session", "traces": [], "count": 0})

    async def test_blank_session_is_empty_list(self):
        payload = await session_traces("   ")
        self.assertEqual(payload, {"session_id": "", "traces": [], "count": 0})


class StatsUsageTests(_UsageGuard, unittest.IsolatedAsyncioTestCase):
    async def test_stats_exposes_global_usage(self):
        GLOBAL_USAGE.update(
            {
                "prompt_tokens": 1000,
                "completion_tokens": 234,
                "total_tokens": 1234,
                "llm_calls": 7,
                "cost": 0.0123456,
            }
        )
        with patch("validation.safety_log.get_records", return_value=[]), patch(
            "validation.auto_fixer.get_fix_records", return_value=[]
        ):
            payload = await stats()
        self.assertEqual(payload["total_tokens"], 1234)
        self.assertAlmostEqual(payload["total_cost"], 0.012346, places=6)
        self.assertEqual(payload["llm_calls"], 7)
        self.assertIn("chat_count", payload)

    def test_snapshot_helper_rounds_cost(self):
        GLOBAL_USAGE.update(
            {
                "total_tokens": 3,
                "llm_calls": 1,
                "cost": 0.0000004,
            }
        )
        snap = global_usage_snapshot()
        self.assertEqual(snap["total_tokens"], 3)
        self.assertEqual(snap["llm_calls"], 1)
        self.assertAlmostEqual(snap["total_cost"], 0.0, places=6)


class TracesHttpTests(_TracesDirGuard, unittest.TestCase):
    """HTTP 契约：200 + 空列表；lifespan=off 避免拉起 Coordinator / 真实 LLM。"""

    def test_get_traces_http_200_with_mock_jsonl(self):
        self._write_jsonl(
            "http-sess",
            [{"trace_id": "deadbeefcafe", "spans": []}],
        )
        try:
            from fastapi.testclient import TestClient

            from webapi.app import app
        except Exception as exc:  # pragma: no cover
            self.skipTest(f"TestClient unavailable: {exc}")

        try:
            client = TestClient(app, lifespan="off")
        except TypeError:
            client = TestClient(app)

        response = client.get("/api/traces/http-sess")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["session_id"], "http-sess")
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["traces"][0]["trace_id"], "deadbeefcafe")

        missing = client.get("/api/traces/does-not-exist")
        self.assertEqual(missing.status_code, 200)
        self.assertEqual(missing.json()["traces"], [])
        self.assertEqual(missing.json()["count"], 0)

    def test_get_stats_http_includes_usage_fields(self):
        GLOBAL_USAGE.update(
            {
                "total_tokens": 42,
                "llm_calls": 3,
                "cost": 0.001,
            }
        )
        try:
            from fastapi.testclient import TestClient

            from webapi.app import app
        except Exception as exc:  # pragma: no cover
            self.skipTest(f"TestClient unavailable: {exc}")

        try:
            client = TestClient(app, lifespan="off")
        except TypeError:
            client = TestClient(app)

        with patch("validation.safety_log.get_records", return_value=[]), patch(
            "validation.auto_fixer.get_fix_records", return_value=[]
        ):
            response = client.get("/api/stats")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["total_tokens"], 42)
        self.assertEqual(body["llm_calls"], 3)
        self.assertAlmostEqual(body["total_cost"], 0.001, places=6)


if __name__ == "__main__":
    unittest.main()
