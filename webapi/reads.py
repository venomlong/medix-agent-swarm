"""只读适配：SessionSummary 文件、Mem0、Milvus。不编造持久库。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger

KB_TYPE_TO_UI = {
    "lifestyle": ("lifestyle", "lifestyle"),
    "disease_classification": ("icd10", "ICD-10"),
    "clinical_guideline": ("guideline", "指南"),
}

UI_TYPE_TO_KB = {
    "lifestyle": "lifestyle",
    "icd10": "disease_classification",
    "guideline": "clinical_guideline",
}


def list_session_rows(limit: int = 40) -> List[Dict[str, Any]]:
    from memory.session_summary import SessionSummaryManager

    mgr = SessionSummaryManager()
    rows = mgr.list_summaries(limit=limit)
    for row in rows:
        row.setdefault("source", "session_summary")
    return rows


def get_session_detail(session_id: str) -> Optional[Dict[str, Any]]:
    from memory.session_summary import SessionSummaryManager

    mgr = SessionSummaryManager()
    text = mgr.read_markdown(session_id)
    if text is None:
        return None
    path = mgr._resolve_path(session_id)
    mtime = path.stat().st_mtime if path else 0.0
    from memory.session_summary import _parse_summary_markdown

    parsed = _parse_summary_markdown(session_id, text, mtime)
    parsed["markdown"] = text
    parsed["source"] = "session_summary"
    return parsed


def search_mem0_similar(coordinator: Any, query: str, limit: int = 5) -> List[Dict[str, Any]]:
    if coordinator is None:
        return []
    ltm = getattr(coordinator, "long_term_memory", None)
    if ltm is None or not getattr(ltm, "enabled", False):
        return []
    try:
        raw = ltm.search_similar_sessions(query, limit=limit)
    except Exception as exc:
        logger.error(f"Mem0 search failed: {exc}")
        return []
    cases = []
    for item in raw or []:
        cases.append(
            {
                "score": float(item.get("score") or 0.0),
                "text": str(item.get("content") or ""),
                "session_id": (item.get("metadata") or {}).get("session_id"),
                "memory_id": item.get("memory_id"),
            }
        )
    return cases


def search_knowledge(query: str, doc_type: Optional[str], top_k: int = 8) -> List[Dict[str, Any]]:
    from knowledge.milvus_kb import MedicalKnowledgeBase

    kb = MedicalKnowledgeBase()
    filter_type = UI_TYPE_TO_KB.get(doc_type or "", doc_type or None)
    if filter_type in ("all", "", None):
        filter_type = None
    hits = kb.search(query=query, top_k=top_k, filter_type=filter_type)
    docs: List[Dict[str, Any]] = []
    for hit in hits:
        meta = hit.get("metadata") or {}
        kb_type = str(meta.get("type") or "")
        ui_type, type_label = KB_TYPE_TO_UI.get(kb_type, (kb_type or "lifestyle", kb_type or "其他"))
        title = (
            str(meta.get("disease") or "").replace("_", " ").strip()
            or str(meta.get("filename") or "").replace(".txt", "")
            or f"文档 {hit.get('id')}"
        )
        content = str(hit.get("content") or "").replace("\n", " ").strip()
        docs.append(
            {
                "id": str(hit.get("id")),
                "title": title[:80],
                "type": ui_type,
                "typeLabel": type_label,
                "snippet": content[:180] + ("…" if len(content) > 180 else ""),
                "score": float(hit.get("score") or 0.0),
            }
        )
    return docs
