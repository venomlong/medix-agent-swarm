"""BM25 + RRF 混合检索：内存语料，不打 LLM、不连 Milvus。"""
from __future__ import annotations

import unittest

from knowledge.hybrid_retriever import (
    BM25Index,
    fuse_or_fallback,
    rrf_fuse,
    tokenize,
)


def _doc(pk, content, doc_id, doc_type="lifestyle"):
    return {
        "id": pk,
        "content": content,
        "metadata": {"doc_id": doc_id, "type": doc_type},
    }


CORPUS = [
    _doc(
        1,
        "二甲双胍是2型糖尿病的一线降糖药物，常见副作用包括胃肠道反应。",
        "dm_metformin",
        "clinical_guideline",
    ),
    _doc(
        2,
        "高血压患者应限制钠盐摄入，每日食盐不超过5克，并坚持有氧运动。",
        "htn_salt",
        "lifestyle",
    ),
    _doc(
        3,
        "急性心肌梗死典型表现为持续胸骨后压榨性疼痛，可放射至左臂。",
        "mi_pain",
        "emergency",
    ),
    _doc(
        4,
        "糖尿病患者的生活方式干预包括控制体重、合理膳食与规律运动。",
        "dm_life",
        "lifestyle",
    ),
]


class TokenizeTests(unittest.TestCase):
    def test_empty_query_is_empty_list(self):
        self.assertEqual(tokenize(""), [])
        self.assertEqual(tokenize("   "), [])

    def test_chinese_keeps_drug_name(self):
        tokens = tokenize("二甲双胍怎么吃")
        self.assertTrue(tokens)
        self.assertTrue(any(t in tokens for t in ("二甲双胍", "二甲", "双胍", "胍")))


class BM25IndexTests(unittest.TestCase):
    def setUp(self):
        self.index = BM25Index()
        self.index.rebuild(CORPUS)

    def test_empty_corpus_returns_empty(self):
        empty = BM25Index()
        self.assertTrue(empty.is_empty())
        self.assertEqual(empty.search("二甲双胍"), [])

    def test_empty_query_returns_empty(self):
        self.assertEqual(self.index.search(""), [])
        self.assertEqual(self.index.search("   "), [])

    def test_keyword_hit_ranks_exact_term_first(self):
        hits = self.index.search("二甲双胍", top_n=3)
        self.assertTrue(hits)
        self.assertEqual(hits[0]["id"], 1)
        self.assertEqual(hits[0]["metadata"]["doc_id"], "dm_metformin")
        self.assertGreater(hits[0]["score"], 0)

    def test_filter_type_excludes_other_types(self):
        hits = self.index.search("合理膳食", top_n=5, filter_type="lifestyle")
        ids = [h["id"] for h in hits]
        self.assertIn(4, ids)
        self.assertNotIn(1, ids)


class RRFTests(unittest.TestCase):
    def test_doc_in_both_lists_outranks_single_list_leaders(self):
        vector_hits = [
            {"id": "A", "content": "a", "metadata": {}, "score": 0.99},
            {"id": "B", "content": "b", "metadata": {}, "score": 0.90},
            {"id": "C", "content": "c", "metadata": {}, "score": 0.80},
        ]
        bm25_hits = [
            {"id": "B", "content": "b", "metadata": {}, "score": 12.0},
            {"id": "D", "content": "d", "metadata": {}, "score": 8.0},
            {"id": "E", "content": "e", "metadata": {}, "score": 3.0},
        ]
        fused = rrf_fuse(vector_hits, bm25_hits, k=60, top_k=5)
        self.assertEqual(fused[0]["id"], "B")
        self.assertIn("rrf_score", fused[0])
        self.assertGreater(fused[0]["score"], fused[1]["score"])
        ids = [h["id"] for h in fused]
        self.assertEqual(set(ids), {"A", "B", "C", "D", "E"})

    def test_rrf_score_formula_for_overlap(self):
        vector_hits = [{"id": "X", "content": "x", "metadata": {}, "score": 0.5}]
        bm25_hits = [{"id": "X", "content": "x", "metadata": {}, "score": 1.0}]
        fused = rrf_fuse(vector_hits, bm25_hits, k=60, top_k=1)
        expected_raw = 1.0 / (60 + 1) + 1.0 / (60 + 1)
        self.assertAlmostEqual(fused[0]["rrf_score"], expected_raw)
        self.assertAlmostEqual(fused[0]["score"], 1.0)

    def test_preserves_chunk_fields(self):
        vector_hits = [{
            "id": 42,
            "content": "指南原文片段",
            "metadata": {"doc_id": "g1", "type": "clinical_guideline"},
            "score": 0.77,
            "collection": "medical_knowledge",
        }]
        fused = rrf_fuse(vector_hits, [], k=60, top_k=1)
        self.assertEqual(fused[0]["id"], 42)
        self.assertEqual(fused[0]["content"], "指南原文片段")
        self.assertEqual(fused[0]["metadata"]["doc_id"], "g1")
        self.assertEqual(fused[0]["collection"], "medical_knowledge")


class FuseOrFallbackTests(unittest.TestCase):
    def test_hybrid_includes_bm25_keyword_hit_vector_missed(self):
        vector_hits = [
            {"id": 2, "content": "限盐", "metadata": {"doc_id": "htn_salt"}, "score": 0.88},
            {"id": 4, "content": "运动", "metadata": {"doc_id": "dm_life"}, "score": 0.81},
        ]
        index = BM25Index()
        index.rebuild(CORPUS)
        bm25_hits = index.search("二甲双胍", top_n=5)
        fused = fuse_or_fallback(vector_hits, bm25_hits, mode="hybrid", top_k=3)
        ids = [h["id"] for h in fused]
        self.assertIn(1, ids)

    def test_vector_only_mode_ignores_bm25(self):
        vector_hits = [
            {"id": 2, "content": "限盐", "metadata": {}, "score": 0.88},
        ]
        bm25_hits = [
            {"id": 1, "content": "二甲双胍", "metadata": {}, "score": 9.0},
        ]
        out = fuse_or_fallback(vector_hits, bm25_hits, mode="vector", top_k=5)
        self.assertEqual([h["id"] for h in out], [2])

    def test_empty_bm25_falls_back_to_vector(self):
        vector_hits = [
            {"id": 2, "content": "限盐", "metadata": {}, "score": 0.88},
            {"id": 3, "content": "胸痛", "metadata": {}, "score": 0.70},
        ]
        out = fuse_or_fallback(vector_hits, [], mode="hybrid", top_k=2)
        self.assertEqual([h["id"] for h in out], [2, 3])

    def test_bm25_mode_falls_back_to_vector_when_index_empty(self):
        vector_hits = [{"id": 9, "content": "v", "metadata": {}, "score": 0.5}]
        out = fuse_or_fallback(vector_hits, [], mode="bm25", top_k=1)
        self.assertEqual(out[0]["id"], 9)


if __name__ == "__main__":
    unittest.main()
