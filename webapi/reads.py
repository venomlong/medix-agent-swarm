"""只读适配：SessionSummary 文件、Mem0、Milvus。不编造持久库。"""
from __future__ import annotations

from datetime import datetime
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


def get_short_term_messages(
    session_id: str,
    coordinator: Any = None,
    limit: int = 100,
) -> Dict[str, Any]:
    """按 session_id 读取短期记忆（Redis/内存），供工作台回填聊天框。"""
    stm = getattr(coordinator, "short_term_memory", None) if coordinator is not None else None
    if stm is None:
        from memory.short_term import ShortTermMemory

        stm = ShortTermMemory()

    history = stm.get_session(session_id) if session_id else None
    messages: List[Dict[str, Any]] = []
    if history:
        for msg in history.messages:
            role = str(msg.get("role") or "")
            if role not in ("user", "assistant"):
                continue
            messages.append(
                {
                    "role": role,
                    "content": str(msg.get("content") or ""),
                    "timestamp": msg.get("timestamp"),
                }
            )
        if limit > 0:
            messages = messages[-limit:]

    return {
        "session_id": session_id,
        "messages": messages,
        "count": len(messages),
        "source": getattr(stm, "storage_type", "unknown"),
    }


def delete_session_data(session_id: str, coordinator: Any = None) -> Dict[str, Any]:
    """删除会话级数据：短期记忆 + SessionSummary 文件。不碰 Mem0。"""
    sid = (session_id or "").strip()
    cleared = {
        "short_term": False,
        "session_summary": False,
    }
    warnings: List[str] = []

    if sid:
        stm = getattr(coordinator, "short_term_memory", None) if coordinator is not None else None
        if stm is None:
            from memory.short_term import ShortTermMemory

            stm = ShortTermMemory()
        try:
            stm.clear_session(sid)
            cleared["short_term"] = True
        except Exception as exc:
            logger.error(f"Failed to clear short-term session {sid}: {exc}")
            warnings.append(f"short_term: {exc}")

        try:
            from memory.session_summary import SessionSummaryManager

            mgr = SessionSummaryManager()
            cleared["session_summary"] = bool(mgr.delete_summary(sid))
        except Exception as exc:
            logger.error(f"Failed to delete session summary {sid}: {exc}")
            warnings.append(f"session_summary: {exc}")

    payload: Dict[str, Any] = {
        "ok": True,
        "session_id": sid,
        "cleared": cleared,
        "mem0": "not_deleted",
        "mem0_reason": "LongTermMemory 没有按 session_id 删除的可靠 API，未改 Mem0。",
    }
    if warnings:
        payload["warnings"] = warnings
    return payload


def _last_user_and_assistant(messages: List[Dict[str, Any]]):
    last_user = ""
    last_assistant = ""
    for msg in messages or []:
        role = str(msg.get("role") or "")
        content = str(msg.get("content") or "")
        if role == "user" and content.strip():
            last_user = content
        elif role == "assistant" and content.strip():
            last_assistant = content
    return last_user, last_assistant


def _markdown_from_turn(session_id: str, question: str, answer: str, time_label: str = "") -> str:
    lines = [f"# Session Summary: {session_id}", ""]
    if time_label:
        lines.extend([f"**时间**: {time_label}", ""])
    lines.extend(
        [
            "## 问题",
            question or session_id,
            "",
            "## 最终答案",
            "",
            answer or "",
            "",
        ]
    )
    return "\n".join(lines)


def _detail_from_short_term(session_id: str, coordinator: Any = None) -> Optional[Dict[str, Any]]:
    """单 Agent 可能没有 SessionSummary 文件：用短期记忆最后一条 assistant 作为完整最终回答。"""
    payload = get_short_term_messages(session_id, coordinator=coordinator, limit=0)
    messages = payload.get("messages") or []
    question, answer = _last_user_and_assistant(messages)
    if not answer.strip():
        return None
    time_raw = ""
    for msg in reversed(messages):
        if str(msg.get("role") or "") == "assistant" and msg.get("timestamp"):
            time_raw = str(msg.get("timestamp") or "")
            break
    time_label = ""
    try:
        dt = datetime.fromisoformat(time_raw.replace("Z", "+00:00"))
        time_label = dt.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        time_label = time_raw
    text = _markdown_from_turn(session_id, question, answer, time_label)
    from memory.session_summary import _extract_summary_sections, _parse_summary_markdown

    parsed = _parse_summary_markdown(session_id, text, 0.0)
    sections = _extract_summary_sections(text)
    parsed["markdown"] = text
    parsed["sections"] = sections
    parsed["question_full"] = sections.get("问题") or question or parsed.get("question")
    parsed["final_answer"] = sections.get("最终答案") or answer
    parsed["source"] = "short_term"
    return parsed


def get_session_detail(session_id: str, coordinator: Any = None) -> Optional[Dict[str, Any]]:
    from memory.session_summary import SessionSummaryManager

    mgr = SessionSummaryManager()
    text = mgr.read_markdown(session_id)
    if text is None:
        return _detail_from_short_term(session_id, coordinator=coordinator)
    path = mgr._resolve_path(session_id)
    mtime = path.stat().st_mtime if path else 0.0
    from memory.session_summary import _extract_summary_sections, _parse_summary_markdown

    parsed = _parse_summary_markdown(session_id, text, mtime)
    sections = _extract_summary_sections(text)
    parsed["markdown"] = text
    parsed["sections"] = sections
    parsed["question_full"] = sections.get("问题") or parsed.get("question")
    parsed["final_answer"] = sections.get("最终答案") or ""
    parsed["source"] = "session_summary"
    if not (parsed["final_answer"] or "").strip():
        hydrated = _detail_from_short_term(session_id, coordinator=coordinator)
        if hydrated and (hydrated.get("final_answer") or "").strip():
            parsed["final_answer"] = hydrated["final_answer"]
            parsed.setdefault("sections", {})["最终答案"] = hydrated["final_answer"]
            parsed["source"] = "session_summary+short_term"
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
