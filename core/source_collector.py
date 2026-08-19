"""
请求级 RAG 引用来源收集器。

模式仿照 swarm/shared_context.py 的 _event_listener：
Skill 在 kb.search() 命中后调用 add_source()；Coordinator 在 process()
开头 start_collect()、返回前 get_sources()。无收集器时静默跳过，
避免 CLI / 单测直接跑 Skill 时炸。
"""
from contextvars import ContextVar, Token
from typing import Any, Dict, List, Optional

_sources: ContextVar[Optional[List[Dict[str, Any]]]] = ContextVar(
    "collected_sources", default=None
)

_MAX_SOURCES = 8
_SNIPPET_LEN = 120


def start_collect() -> Token:
    """在 SwarmCoordinator.process() 开头 set([])。"""
    return _sources.set([])


def stop_collect(token: Optional[Token]) -> None:
    if token is None:
        return
    try:
        _sources.reset(token)
    except (ValueError, LookupError):
        # ContextVar token 不属于当前 context 时忽略，避免 finally 二次爆炸
        pass


def add_source(source: dict) -> None:
    """Skill 内部调用；无收集器时静默跳过。"""
    bucket = _sources.get()
    if bucket is None or not isinstance(source, dict):
        return
    bucket.append(dict(source))


def get_sources() -> List[dict]:
    """去重（按 id），按 score 降序，最多 8 条。无收集器时返回空列表。"""
    bucket = _sources.get()
    if not bucket:
        return []

    best: Dict[str, Dict[str, Any]] = {}
    for item in bucket:
        sid = str(item.get("id", "")).strip()
        if not sid:
            continue
        prev = best.get(sid)
        score = _as_score(item.get("score"))
        if prev is None or score > _as_score(prev.get("score")):
            copied = dict(item)
            copied["id"] = sid
            copied["score"] = score
            best[sid] = copied

    ranked = sorted(best.values(), key=lambda x: _as_score(x.get("score")), reverse=True)
    return ranked[:_MAX_SOURCES]


def source_from_hit(doc: Dict[str, Any]) -> Dict[str, Any]:
    """把 Milvus search() 的一条 hit 转成前后端契约中的 source。"""
    metadata = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
    content = str(doc.get("content") or "").replace("\n", " ").strip()
    disease = metadata.get("disease")
    source_name = metadata.get("source")
    title = disease or source_name or "医学知识库条目"
    return {
        "id": str(doc.get("id", "")),
        "title": str(title),
        "source": str(source_name or "医学知识库"),
        "type": str(metadata.get("type") or ""),
        "score": _as_score(doc.get("score")),
        "snippet": content[:_SNIPPET_LEN],
    }


def register_hits(docs: Optional[List[Dict[str, Any]]]) -> None:
    """把实际用于回答的检索命中登记为引用来源。无命中则不登记（不伪造）。"""
    if not docs:
        return
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        add_source(source_from_hit(doc))


def _as_score(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
