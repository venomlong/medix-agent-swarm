"""
BM25 关键词检索 + Reciprocal Rank Fusion（RRF）。

RRF：score(d) = sum_i 1 / (k + rank_i(d))，默认 k=60。
融合后的 `score` 是把 raw RRF 除以理论最大值 n_lists/(k+1) 得到的 0–1 值，
便于前端按百分比展示；原始融合分在 `rrf_score`。
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence

logger = logging.getLogger(__name__)

RRF_K_DEFAULT = 60
BM25_TOP_N_DEFAULT = 20
VECTOR_TOP_N_DEFAULT = 20
RETRIEVAL_MODES = ("hybrid", "vector", "bm25")

_PUNCT_RE = re.compile(r"^[\s\W_]+$", re.UNICODE)
_STOP = {
    "的", "了", "和", "与", "或", "及", "在", "是", "有", "为", "等",
    "对", "中", "上", "下", "也", "就", "都", "而", "其", "被",
}
_JIEBA_READY = False
_MEDICAL_WORDS = (
    "二甲双胍", "高血压", "糖尿病", "心肌梗死", "冠心病", "胰岛素",
    "降压", "降糖", "ICD-10", "生活方式",
)


def _ensure_jieba():
    global _JIEBA_READY
    import jieba

    if not _JIEBA_READY:
        for word in _MEDICAL_WORDS:
            jieba.add_word(word)
        _JIEBA_READY = True
    return jieba


def tokenize(text: str) -> List[str]:
    """中文医学文本分词（jieba search 模式）。空串返回空列表。"""
    if not text or not str(text).strip():
        return []
    try:
        jieba = _ensure_jieba()
    except ImportError:  # pragma: no cover - 依赖在 requirements 中
        logger.warning("jieba not installed; BM25 tokenize falls back to character bigrams")
        stripped = str(text).strip()
        if len(stripped) < 2:
            return [stripped]
        return [stripped[i : i + 2] for i in range(len(stripped) - 1)]

    tokens: List[str] = []
    for raw in jieba.lcut_for_search(str(text)):
        token = raw.strip().lower()
        if not token or token in _STOP or _PUNCT_RE.match(token):
            continue
        tokens.append(token)
    return tokens


class BM25Index:
    """进程内 BM25 倒排。chunk `id` 必须与向量库主键对齐。"""

    def __init__(self) -> None:
        self._docs: List[Dict[str, Any]] = []
        self._tokenized: List[List[str]] = []
        self._engine: Any = None

    def __len__(self) -> int:
        return len(self._docs)

    def is_empty(self) -> bool:
        return self._engine is None or not self._docs

    def rebuild(self, docs: Optional[Sequence[Dict[str, Any]]]) -> None:
        self._docs = []
        self._tokenized = []
        self._engine = None
        for doc in docs or []:
            if not isinstance(doc, dict):
                continue
            content = str(doc.get("content") or "")
            metadata = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
            tokens = tokenize(content) or ["_empty_"]
            self._docs.append({
                "id": doc.get("id"),
                "content": content,
                "metadata": metadata,
            })
            self._tokenized.append(tokens)
        if not self._docs:
            return
        from rank_bm25 import BM25Okapi

        self._engine = BM25Okapi(self._tokenized)

    def search(
        self,
        query: str,
        top_n: int = BM25_TOP_N_DEFAULT,
        filter_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        if self.is_empty() or top_n <= 0:
            return []
        query_tokens = tokenize(query)
        if not query_tokens:
            return []
        try:
            scores = self._engine.get_scores(query_tokens)
        except Exception as exc:
            logger.warning("BM25 scoring failed: %s", exc)
            return []

        ranked: List[tuple] = []
        for idx, doc in enumerate(self._docs):
            if filter_type:
                meta = doc.get("metadata") or {}
                if str(meta.get("type") or "") != str(filter_type):
                    continue
            score = float(scores[idx])
            if score <= 0.0:
                continue
            ranked.append((score, doc))
        ranked.sort(key=lambda item: item[0], reverse=True)

        hits: List[Dict[str, Any]] = []
        for score, doc in ranked[:top_n]:
            hits.append({
                "id": doc["id"],
                "content": doc["content"],
                "metadata": dict(doc["metadata"]),
                "score": score,
                "bm25_score": score,
            })
        return hits


def rrf_fuse(
    vector_hits: Sequence[Dict[str, Any]],
    bm25_hits: Sequence[Dict[str, Any]],
    *,
    k: int = RRF_K_DEFAULT,
    top_k: int = 5,
) -> List[Dict[str, Any]]:
    """
    Reciprocal Rank Fusion。

    score(d) = Σ 1/(k + rank_i(d))，rank 从 1 起。
    返回列表按 raw RRF 降序，截断到 top_k。
    """
    if top_k <= 0:
        return []
    rrf_k = k if k and k > 0 else RRF_K_DEFAULT

    raw_scores: Dict[Any, float] = {}
    payloads: Dict[Any, Dict[str, Any]] = {}
    vector_scores: Dict[Any, float] = {}
    bm25_scores: Dict[Any, float] = {}

    def _consume(ranked: Sequence[Dict[str, Any]], kind: str) -> None:
        for rank, hit in enumerate(ranked, start=1):
            if not isinstance(hit, dict) or hit.get("id") is None:
                continue
            doc_id = hit["id"]
            raw_scores[doc_id] = raw_scores.get(doc_id, 0.0) + 1.0 / (rrf_k + rank)
            if doc_id not in payloads:
                payloads[doc_id] = dict(hit)
            orig = hit.get("score")
            try:
                orig_f = float(orig) if orig is not None else None
            except (TypeError, ValueError):
                orig_f = None
            if kind == "vector" and orig_f is not None:
                vector_scores[doc_id] = orig_f
            if kind == "bm25" and orig_f is not None:
                bm25_scores[doc_id] = orig_f

    _consume(vector_hits or [], "vector")
    _consume(bm25_hits or [], "bm25")
    if not raw_scores:
        return []

    n_lists = int(bool(vector_hits)) + int(bool(bm25_hits))
    max_possible = n_lists / (rrf_k + 1.0) if n_lists else 1.0

    fused: List[Dict[str, Any]] = []
    for doc_id, rrf in sorted(raw_scores.items(), key=lambda kv: kv[1], reverse=True):
        doc = dict(payloads[doc_id])
        doc["id"] = doc_id
        doc["rrf_score"] = rrf
        doc["score"] = (rrf / max_possible) if max_possible else rrf
        if doc_id in vector_scores:
            doc["vector_score"] = vector_scores[doc_id]
        if doc_id in bm25_scores:
            doc["bm25_score"] = bm25_scores[doc_id]
        fused.append(doc)
        if len(fused) >= top_k:
            break
    return fused


def fuse_or_fallback(
    vector_hits: Sequence[Dict[str, Any]],
    bm25_hits: Sequence[Dict[str, Any]],
    *,
    mode: str = "hybrid",
    rrf_k: int = RRF_K_DEFAULT,
    top_k: int = 5,
) -> List[Dict[str, Any]]:
    """
    按 mode 返回检索结果。BM25 为空或失败时 hybrid/bm25 回退到向量结果。
    """
    resolved = mode if mode in RETRIEVAL_MODES else "hybrid"
    vector_list = list(vector_hits or [])
    bm25_list = list(bm25_hits or [])

    if resolved == "vector":
        return vector_list[:top_k]
    if resolved == "bm25":
        return bm25_list[:top_k] if bm25_list else vector_list[:top_k]
    if not bm25_list:
        return vector_list[:top_k]
    if not vector_list:
        return bm25_list[:top_k]
    return rrf_fuse(vector_list, bm25_list, k=rrf_k, top_k=top_k)


def annotate_collection(
    hits: Iterable[Dict[str, Any]],
    collection: Optional[str],
) -> List[Dict[str, Any]]:
    if not collection:
        return list(hits)
    out: List[Dict[str, Any]] = []
    for hit in hits:
        item = dict(hit)
        item.setdefault("collection", collection)
        out.append(item)
    return out
