"""LLMClient 采集 response.usage（流式 + 非流式），写入 trace 与 answer_done。

全程 mock，不打付费 LLM。
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any, List
from unittest.mock import patch

from core.llm_client import LLMClient
from core.tracing import (
    GLOBAL_USAGE,
    PRICING_DEFAULT,
    end_trace,
    get_trace,
    start_trace,
)
from webapi.bridge import map_answer_done


def _usage(prompt: int, completion: int) -> SimpleNamespace:
    return SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion)


def _response(
    content: str = "ok",
    prompt: int | None = 10,
    completion: int | None = 4,
    tool_calls: Any = None,
    finish_reason: str = "stop",
) -> SimpleNamespace:
    usage = None
    if prompt is not None or completion is not None:
        usage = _usage(prompt or 0, completion or 0)
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    choice = SimpleNamespace(message=message, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], usage=usage)


def _chunk(
    content: str = "",
    finish_reason: str | None = None,
    usage: Any = None,
    empty_choices: bool = False,
    tool_calls: Any = None,
) -> SimpleNamespace:
    if empty_choices:
        return SimpleNamespace(choices=[], usage=usage)
    delta = SimpleNamespace(content=content, tool_calls=tool_calls)
    choice = SimpleNamespace(delta=delta, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], usage=usage)


class _AsyncList:
    def __init__(self, items: List[Any]):
        self._items = list(items)

    def __aiter__(self):
        self._iter = iter(self._items)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class LLMUsageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._usage_snap = dict(GLOBAL_USAGE)
        GLOBAL_USAGE.update(
            {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "llm_calls": 0,
                "cost": 0.0,
            }
        )
        self.llm = LLMClient()
        self.captured: List[dict] = []
        self._trace_token = start_trace("sess-llm-usage", trace_id="usage12token")

    async def asyncTearDown(self):
        end_trace(self._trace_token)
        GLOBAL_USAGE.clear()
        GLOBAL_USAGE.update(self._usage_snap)

    def _install(self, handler):
        async def create(**kwargs):
            self.captured.append(dict(kwargs))
            return await handler(**kwargs)

        self.llm.client.chat.completions.create = create

    async def test_nonstream_chat_records_usage_and_cost(self):
        async def handler(**kwargs):
            self.assertNotIn("stream_options", kwargs)
            self.assertTrue(not kwargs.get("stream"))
            return _response("你好", prompt=1000, completion=500)

        self._install(handler)
        with patch(
            "core.tracing._pricing",
            return_value=dict(PRICING_DEFAULT),
        ):
            text = await self.llm.chat(
                [{"role": "user", "content": "hi"}],
                stream=False,
            )

        self.assertEqual(text, "你好")
        trace = get_trace()
        self.assertEqual(trace.prompt_tokens, 1000)
        self.assertEqual(trace.completion_tokens, 500)
        self.assertEqual(trace.llm_calls, 1)
        # (1000 * 2 + 500 * 8) / 1e6 = 0.006
        self.assertAlmostEqual(trace.cost(), 0.006)
        self.assertEqual(GLOBAL_USAGE["llm_calls"], 1)
        self.assertEqual(GLOBAL_USAGE["total_tokens"], 1500)
        self.assertAlmostEqual(GLOBAL_USAGE["cost"], 0.006)

        payload = map_answer_done(
            {"answer": text, "swarm_enabled": False, "trace": trace.summary()},
            "sess-llm-usage",
            0.5,
        )
        self.assertEqual(payload["trace_id"], "usage12token")
        self.assertEqual(payload["usage"]["prompt_tokens"], 1000)
        self.assertEqual(payload["usage"]["completion_tokens"], 500)
        self.assertEqual(payload["usage"]["total_tokens"], 1500)
        self.assertEqual(payload["usage"]["llm_calls"], 1)
        self.assertAlmostEqual(payload["usage"]["cost"], 0.006)

    async def test_nonstream_chat_skips_when_usage_missing(self):
        async def handler(**kwargs):
            return _response("no-usage", prompt=None, completion=None)

        self._install(handler)
        await self.llm.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(get_trace().llm_calls, 0)
        self.assertEqual(GLOBAL_USAGE["llm_calls"], 0)

    async def test_stream_chat_reads_usage_from_last_empty_chunk(self):
        async def handler(**kwargs):
            self.assertTrue(kwargs.get("stream"))
            self.assertEqual(kwargs.get("stream_options"), {"include_usage": True})
            return _AsyncList(
                [
                    _chunk(content="急"),
                    _chunk(content="诊", finish_reason="stop"),
                    _chunk(
                        empty_choices=True,
                        usage=_usage(20, 6),
                    ),
                ]
            )

        self._install(handler)
        with patch(
            "core.tracing._pricing",
            return_value={"input": 2.0, "output": 8.0},
        ):
            text = await self.llm.chat(
                [{"role": "user", "content": "hi"}],
                stream=True,
            )

        self.assertEqual(text, "急诊")
        trace = get_trace()
        self.assertEqual(trace.prompt_tokens, 20)
        self.assertEqual(trace.completion_tokens, 6)
        self.assertEqual(trace.llm_calls, 1)
        self.assertAlmostEqual(trace.cost(), (20 * 2.0 + 6 * 8.0) / 1_000_000.0)

    async def test_stream_failure_fallback_still_records_usage(self):
        async def handler(**kwargs):
            if kwargs.get("stream"):
                raise RuntimeError("gateway rejected stream_options")
            return _response("fallback", prompt=7, completion=3)

        self._install(handler)
        text = await self.llm.chat(
            [{"role": "user", "content": "hi"}],
            stream=True,
        )
        self.assertEqual(text, "fallback")
        self.assertEqual(get_trace().prompt_tokens, 7)
        self.assertEqual(get_trace().completion_tokens, 3)
        self.assertEqual(get_trace().llm_calls, 1)
        # 回退路径不应带上 stream_options
        nonstream = [c for c in self.captured if not c.get("stream")]
        self.assertTrue(nonstream)
        self.assertNotIn("stream_options", nonstream[-1])

    async def test_nonstream_chat_with_tools_records_usage(self):
        async def handler(**kwargs):
            self.assertNotIn("stream_options", kwargs)
            return _response("最终答案", prompt=40, completion=12)

        self._install(handler)
        resp = await self.llm.chat_with_tools(
            [{"role": "user", "content": "hi"}],
            tools=[],
            stream=False,
        )
        self.assertEqual(resp.content, "最终答案")
        self.assertEqual(get_trace().prompt_tokens, 40)
        self.assertEqual(get_trace().completion_tokens, 12)
        self.assertEqual(get_trace().llm_calls, 1)

    async def test_stream_chat_with_tools_records_usage(self):
        async def handler(**kwargs):
            self.assertEqual(kwargs.get("stream_options"), {"include_usage": True})
            return _AsyncList(
                [
                    _chunk(content="参"),
                    _chunk(content="考", finish_reason="stop"),
                    _chunk(empty_choices=True, usage=_usage(11, 2)),
                ]
            )

        self._install(handler)
        resp = await self.llm.chat_with_tools(
            [{"role": "user", "content": "hi"}],
            tools=[{"type": "function", "function": {"name": "search_knowledge"}}],
            stream=True,
        )
        self.assertEqual(resp.content, "参考")
        self.assertFalse(resp.has_tool_calls())
        self.assertEqual(get_trace().prompt_tokens + get_trace().completion_tokens, 13)
        self.assertEqual(get_trace().llm_calls, 1)

    async def test_trace_summary_feeds_answer_done_usage(self):
        get_trace().add_usage(100, 20)
        result = {
            "answer": "多喝水",
            "swarm_enabled": False,
            "trace": get_trace().summary(),
        }
        payload = map_answer_done(result, "sess-llm-usage", 0.3)
        self.assertEqual(payload["usage"]["prompt_tokens"], 100)
        self.assertEqual(payload["usage"]["completion_tokens"], 20)
        self.assertEqual(payload["usage"]["total_tokens"], 120)
        self.assertEqual(payload["usage"]["llm_calls"], 1)
        self.assertEqual(payload["trace_id"], "usage12token")


if __name__ == "__main__":
    unittest.main()
