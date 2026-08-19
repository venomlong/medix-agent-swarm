"""RAG 引用收集器：ContextVar 隔离、去重、无收集器不炸、结构与透传。

不打付费 LLM，不依赖真实 Milvus。
"""
from __future__ import annotations

import asyncio
import unittest

from core.source_collector import (
    add_source,
    get_sources,
    register_hits,
    source_from_hit,
    start_collect,
    stop_collect,
)
from validation.guardrail import OutputGuardrail, apply_guardrail_to_result
from webapi.bridge import map_answer_done


def _hit(pk, *, score=0.8, disease="高血压", source="高血压_生活方式.txt", doc_type="lifestyle", content=None):
    text = content or (
        "高血压患者的生活方式干预是药物治疗的基础。"
        "建议低盐饮食，每日钠摄入控制在 2g 以内，增加富钾食物。"
    )
    return {
        "id": pk,
        "content": text,
        "metadata": {
            "disease": disease,
            "source": source,
            "type": doc_type,
        },
        "score": score,
    }


class SourceFromHitTests(unittest.TestCase):
    def test_id_is_stringified_and_snippet_truncated(self):
        long_content = "钠" * 200
        src = source_from_hit(_hit(12345, score=0.83, content=long_content))
        self.assertEqual(src["id"], "12345")
        self.assertEqual(src["title"], "高血压")
        self.assertEqual(src["source"], "高血压_生活方式.txt")
        self.assertEqual(src["type"], "lifestyle")
        self.assertAlmostEqual(src["score"], 0.83)
        self.assertEqual(len(src["snippet"]), 120)

    def test_title_falls_back_to_source(self):
        src = source_from_hit({
            "id": "x",
            "content": "abc",
            "metadata": {"source": "某指南.txt", "type": "clinical_guideline"},
            "score": 0.5,
        })
        self.assertEqual(src["title"], "某指南.txt")
        self.assertEqual(src["source"], "某指南.txt")


class CollectorLifecycleTests(unittest.TestCase):
    def test_add_source_without_collector_is_silent(self):
        add_source({"id": "nope", "score": 1})
        self.assertEqual(get_sources(), [])

    def test_empty_hits_do_not_fabricate(self):
        token = start_collect()
        try:
            register_hits([])
            register_hits(None)
            self.assertEqual(get_sources(), [])
        finally:
            stop_collect(token)

    def test_dedup_keeps_higher_score_and_caps_at_8(self):
        token = start_collect()
        try:
            register_hits([_hit(1, score=0.4), _hit(1, score=0.9)])
            for i in range(2, 12):
                register_hits([_hit(i, score=0.1 * i, disease=f"病{i}")])
            sources = get_sources()
            ids = [s["id"] for s in sources]
            self.assertEqual(len(sources), 8)
            self.assertEqual(ids[0], "11")
            self.assertIn("1", ids)
            dup = [s for s in sources if s["id"] == "1"]
            self.assertEqual(len(dup), 1)
            self.assertAlmostEqual(dup[0]["score"], 0.9)
        finally:
            stop_collect(token)

    def test_stop_collect_restores_empty(self):
        token = start_collect()
        register_hits([_hit(7)])
        self.assertEqual(len(get_sources()), 1)
        stop_collect(token)
        self.assertEqual(get_sources(), [])
        add_source({"id": "after", "score": 1})
        self.assertEqual(get_sources(), [])


class CollectorIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_tasks_do_not_leak(self):
        async def worker(pk: str):
            token = start_collect()
            try:
                register_hits([_hit(pk, score=0.7, disease=pk)])
                await asyncio.sleep(0.01)
                return [s["id"] for s in get_sources()]
            finally:
                stop_collect(token)

        a, b = await asyncio.gather(worker("A"), worker("B"))
        self.assertEqual(a, ["A"])
        self.assertEqual(b, ["B"])

    async def test_child_tasks_share_request_collector(self):
        token = start_collect()
        try:
            async def skill(pk: str):
                register_hits([_hit(pk, score=0.6)])

            await asyncio.gather(skill("s1"), skill("s2"))
            ids = {s["id"] for s in get_sources()}
            self.assertEqual(ids, {"s1", "s2"})
        finally:
            stop_collect(token)


class SourcePassthroughTests(unittest.IsolatedAsyncioTestCase):
    async def test_mock_hits_flow_to_answer_done(self):
        token = start_collect()
        try:
            register_hits([
                _hit(42, score=0.83),
                _hit(42, score=0.40),
            ])
            sources = get_sources()
            result = {
                "answer": "高血压患者建议低盐饮食。以上信息仅供参考，请及时就医。",
                "sources": sources,
                "swarm_enabled": False,
            }
            await apply_guardrail_to_result(
                OutputGuardrail(llm_client=None),
                "高血压怎么吃",
                result,
                session_id="sess-src",
            )
            self.assertEqual(len(result["sources"]), 1)
            self.assertEqual(result["sources"][0]["id"], "42")
            self.assertAlmostEqual(result["sources"][0]["score"], 0.83)

            payload = map_answer_done(result, "sess-src", 1.2)
            self.assertEqual(len(payload["sources"]), 1)
            src = payload["sources"][0]
            self.assertEqual(src["id"], "42")
            self.assertEqual(src["title"], "高血压")
            self.assertEqual(src["source"], "高血压_生活方式.txt")
            self.assertEqual(src["type"], "lifestyle")
            self.assertIn("snippet", src)
        finally:
            stop_collect(token)

    async def test_guardrail_rewrite_does_not_drop_sources(self):
        token = start_collect()
        try:
            register_hits([_hit("g1", score=0.77)])
            result = {
                "answer": "根据症状，你得的是心肌炎，需要休息。以上信息仅供参考。",
                "sources": get_sources(),
            }
            await apply_guardrail_to_result(
                OutputGuardrail(llm_client=None),
                "胸口不适",
                result,
                session_id="sess-g",
            )
            self.assertTrue((result.get("guardrail") or {}).get("rewritten"))
            self.assertEqual(result["sources"][0]["id"], "g1")
        finally:
            stop_collect(token)

    def test_map_answer_done_empty_sources_default(self):
        payload = map_answer_done(
            {"answer": "感冒多喝水", "emergency": True, "swarm_enabled": False},
            "sess-em",
            0.3,
        )
        self.assertEqual(payload["sources"], [])


if __name__ == "__main__":
    unittest.main()
