"""
FastAPI 入口：CORS + lifespan 单例 + POST /api/chat SSE + GET /api/health。

启动（项目根，确保能 import swarm）：
    python -m uvicorn webapi.app:app --host 127.0.0.1 --port 8000
"""
from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field, field_validator

from core.log_privacy import install_log_privacy

from .bridge import (
    CoordinatorRunner,
    attach_live_listener,
    classify_error,
    emit_timeout_if_needed,
    map_answer_done,
    synthesize_single_agent,
)
from .reads import (
    delete_session_data,
    get_knowledge_chunk,
    get_session_detail,
    get_short_term_messages,
    list_session_rows,
    search_knowledge,
    search_mem0_similar,
)
from .runtime import STATS
from .sse import format_sse, with_common

# uvicorn 加载本模块即挂上全局日志脱敏，覆盖 Coordinator / Agent Loop 的 question 日志
install_log_privacy()

SENTINEL: Tuple[Optional[str], Optional[Dict[str, Any]]] = (None, None)


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = Field(default=None)

    @field_validator("message")
    @classmethod
    def message_must_be_nonempty(cls, value: str) -> str:
        text = (value or "").strip()
        if not text:
            raise ValueError("message 不能为空")
        return text


def _new_session_id() -> str:
    now = datetime.now()
    return f"{now.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"


@asynccontextmanager
async def lifespan(app: FastAPI):
    runner = CoordinatorRunner()
    runner.start()
    app.state.runner = runner
    yield
    runner.stop()


app = FastAPI(title="MediX Web API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:4173",
        "http://localhost:4173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health(request: Request) -> Dict[str, Any]:
    runner: CoordinatorRunner = request.app.state.runner
    ready = runner.coordinator is not None
    payload: Dict[str, Any] = {
        "status": "ok" if ready else "degraded",
        "service": "medix-webapi",
        "coordinator": ready,
    }
    if not ready and runner.error is not None:
        payload["error"] = str(runner.error)[:200]
    return payload


@app.post("/api/chat")
async def chat(req: ChatRequest, request: Request):
    session_id = (req.session_id or "").strip() or _new_session_id()
    runner: CoordinatorRunner = request.app.state.runner

    async def event_stream() -> AsyncIterator[str]:
        queue: asyncio.Queue[Tuple[Optional[str], Optional[Dict[str, Any]]]] = (
            asyncio.Queue()
        )
        main_loop = asyncio.get_running_loop()

        def emit(name: str, data: Dict[str, Any]) -> None:
            main_loop.call_soon_threadsafe(queue.put_nowait, (name, data))

        def close_stream() -> None:
            main_loop.call_soon_threadsafe(queue.put_nowait, SENTINEL)

        emit("session", with_common({"session_id": session_id}, session_id))

        watcher: Optional[asyncio.Task] = None
        if runner.coordinator is None:
            if runner.error is not None:
                code, message = classify_error(runner.error)
            else:
                code, message = (
                    "internal",
                    "协调器正在启动，请数秒后重试。",
                )
            emit("error", with_common({"code": code, "message": message}, session_id))
            close_stream()
        else:
            flags: Dict[str, Any] = {"live": False}
            listener = attach_live_listener(emit, session_id, flags)
            delta_parts: List[str] = []

            def on_delta(piece: str) -> None:
                if not piece:
                    return
                delta_parts.append(piece)
                emit(
                    "answer_delta",
                    with_common(
                        {"delta": piece, "text": "".join(delta_parts)},
                        session_id,
                    ),
                )

            started = time.monotonic()
            try:
                fut = runner.submit_process(
                    req.message, session_id, listener, on_delta
                )
            except Exception as exc:
                code, message = classify_error(exc)
                emit("error", with_common({"code": code, "message": message}, session_id))
                close_stream()
            else:

                async def watch_result() -> None:
                    try:
                        result = await asyncio.wrap_future(fut)
                        elapsed = time.monotonic() - started
                        if not flags.get("live"):
                            synthesize_single_agent(
                                emit, req.message, session_id, result, elapsed
                            )
                        emit_timeout_if_needed(emit, result, session_id)
                        payload = map_answer_done(result, session_id, elapsed)
                        STATS.record_chat(
                            session_id=session_id,
                            question=req.message,
                            swarm_enabled=bool(payload.get("swarm_enabled")),
                            elapsed_s=float(result.get("total_time") or elapsed),
                            timed_out=bool(payload.get("timed_out")),
                            error=False,
                            agent_count=int(payload.get("agent_count") or 1),
                            summary=str(payload.get("body") or "")[:120],
                        )
                        emit("answer_done", payload)
                    except Exception as exc:
                        logger.exception("chat process failed")
                        elapsed = time.monotonic() - started
                        STATS.record_chat(
                            session_id=session_id,
                            question=req.message,
                            swarm_enabled=bool(flags.get("live")),
                            elapsed_s=elapsed,
                            timed_out=False,
                            error=True,
                            agent_count=0,
                            summary=str(exc)[:80],
                        )
                        code, message = classify_error(exc)
                        emit(
                            "error",
                            with_common({"code": code, "message": message}, session_id),
                        )
                    finally:
                        close_stream()

                watcher = asyncio.create_task(watch_result())

        try:
            while True:
                name, data = await queue.get()
                if name is None:
                    break
                yield format_sse(name, data or {})
        finally:
            if watcher is not None and not watcher.done():
                watcher.cancel()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _run_worker_sync(runner: CoordinatorRunner, fn, *args, **kwargs):
    fut = runner.submit_sync(fn, *args, **kwargs)
    return await asyncio.wrap_future(fut)


@app.get("/api/stats")
async def stats() -> Dict[str, Any]:
    from validation.auto_fixer import get_fix_records
    from validation.safety_log import get_records as get_safety_records

    records = get_safety_records(limit=200) or get_fix_records()
    disclaimer = sum(1 for r in records if r.get("kind") == "免责声明")
    emergency = sum(1 for r in records if r.get("kind") == "就医提醒")
    # 会话计数仍是进程内；安全修复次数改读 JSONL。不把整个 stats.scope
    # 改成 persistent，以免仪表盘把 chat_count 误读成跨重启累计。
    return STATS.snapshot(
        extra={
            "auto_fix": len(records),
            "disclaimer_fix": disclaimer,
            "emergency_fix": emergency,
            "auto_fix_scope": "persistent",
        }
    )


@app.get("/api/sessions")
async def sessions(limit: int = Query(default=40, ge=1, le=100)) -> Dict[str, Any]:
    rows = await asyncio.to_thread(list_session_rows, limit)
    seen = {r.get("id") for r in rows}
    merged = list(rows)
    for item in STATS.recent_sessions():
        if item.get("id") not in seen:
            merged.append(item)
            seen.add(item.get("id"))
    merged.sort(key=lambda r: r.get("time") or "", reverse=True)
    return {
        "sessions": merged[:limit],
        "source": "session_summaries+process",
        "scope": "local_markdown_and_current_process",
    }


@app.get("/api/sessions/{session_id}/messages")
async def session_messages(session_id: str, request: Request) -> Dict[str, Any]:
    runner: CoordinatorRunner = request.app.state.runner
    sid = (session_id or "").strip()
    if not sid:
        return {"session_id": "", "messages": [], "count": 0, "source": "none"}
    if runner.coordinator is not None:
        return await _run_worker_sync(
            runner, get_short_term_messages, sid, runner.coordinator
        )
    return await asyncio.to_thread(get_short_term_messages, sid)


@app.get("/api/sessions/{session_id}/similar")
async def session_similar(
    session_id: str,
    request: Request,
    limit: int = Query(default=5, ge=1, le=10),
) -> Dict[str, Any]:
    detail = await asyncio.to_thread(get_session_detail, session_id)
    query = (detail or {}).get("question") or session_id
    runner: CoordinatorRunner = request.app.state.runner
    cases: List[Dict[str, Any]] = []
    mem0_enabled = False
    if runner.coordinator is not None:
        cases = await _run_worker_sync(
            runner, search_mem0_similar, runner.coordinator, query, limit
        )
        ltm = getattr(runner.coordinator, "long_term_memory", None)
        mem0_enabled = bool(getattr(ltm, "enabled", False))
    return {
        "session_id": session_id,
        "query": query,
        "cases": cases,
        "source": "mem0" if mem0_enabled else "unavailable",
        "mem0_enabled": mem0_enabled,
    }


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str, request: Request) -> Dict[str, Any]:
    sid = (session_id or "").strip()
    runner: CoordinatorRunner = request.app.state.runner
    try:
        if runner.coordinator is not None:
            result = await _run_worker_sync(
                runner, delete_session_data, sid, runner.coordinator
            )
        else:
            result = await asyncio.to_thread(delete_session_data, sid)
    except Exception as exc:
        logger.exception("delete session failed")
        result = {
            "ok": True,
            "session_id": sid,
            "cleared": {"short_term": False, "session_summary": False},
            "mem0": "not_deleted",
            "mem0_reason": "LongTermMemory 没有按 session_id 删除的可靠 API，未改 Mem0。",
            "warnings": [str(exc)[:180]],
        }
    try:
        dropped = STATS.drop_session(sid)
    except Exception as exc:
        logger.error(f"Failed to drop process stats for {sid}: {exc}")
        dropped = False
        warnings = list(result.get("warnings") or [])
        warnings.append(f"process_stats: {exc}")
        result["warnings"] = warnings
    result.setdefault("cleared", {})["process_stats"] = dropped
    result["ok"] = True
    return result


@app.get("/api/sessions/{session_id}")
async def session_detail(session_id: str, request: Request) -> Dict[str, Any]:
    runner: CoordinatorRunner = request.app.state.runner
    sid = (session_id or "").strip()
    if runner.coordinator is not None:
        detail = await _run_worker_sync(
            runner, get_session_detail, sid, runner.coordinator
        )
    else:
        detail = await asyncio.to_thread(get_session_detail, sid)
    if detail is None:
        for item in STATS.recent_sessions():
            if item.get("id") == sid:
                return {**item, "markdown": None, "source": "process"}
        return {"id": sid, "error": "not_found", "markdown": None}
    for item in STATS.recent_sessions():
        if item.get("id") != sid:
            continue
        for key in ("time", "mode", "elapsed", "elapsed_s", "agent_count"):
            cur = detail.get(key)
            if item.get(key) not in (None, "", "—") and cur in (None, "", "—"):
                detail[key] = item[key]
        break
    return detail


@app.get("/api/kb/search")
async def kb_search(
    request: Request,
    q: str = Query(default=""),
    type: Optional[str] = Query(default=None),
    top_k: int = Query(default=8, ge=1, le=20),
) -> Dict[str, Any]:
    query = (q or "").strip()
    if not query:
        return {
            "hits": [],
            "query": "",
            "source": "milvus",
            "message": "请输入查询词后再检索。",
        }
    filter_type = type if type and type != "all" else None
    runner: CoordinatorRunner = request.app.state.runner
    try:
        if runner.coordinator is not None:
            hits = await _run_worker_sync(runner, search_knowledge, query, filter_type, top_k)
        else:
            hits = await asyncio.to_thread(search_knowledge, query, filter_type, top_k)
    except Exception as exc:
        logger.exception("kb search failed")
        return {
            "hits": [],
            "query": query,
            "source": "milvus",
            "error": str(exc)[:180],
        }
    return {"hits": hits, "query": query, "source": "milvus", "count": len(hits)}


@app.get("/api/kb/chunks/{chunk_id}")
async def kb_chunk_detail(chunk_id: str, request: Request) -> Dict[str, Any]:
    sid = (chunk_id or "").strip()
    if not sid:
        return {"id": sid, "error": "not_found", "content": ""}
    runner: CoordinatorRunner = request.app.state.runner
    try:
        if runner.coordinator is not None:
            detail = await _run_worker_sync(runner, get_knowledge_chunk, sid)
        else:
            detail = await asyncio.to_thread(get_knowledge_chunk, sid)
    except Exception as exc:
        logger.exception("kb chunk detail failed")
        return {"id": sid, "error": str(exc)[:180], "content": ""}
    if detail is None:
        return {"id": sid, "error": "not_found", "content": ""}
    return detail


@app.get("/api/safety/fixes")
def safety_fixes() -> Dict[str, Any]:
    from validation.auto_fixer import get_fix_records
    from validation.safety_log import get_records as get_safety_records

    records = get_safety_records(limit=200)
    if not records:
        records = get_fix_records()
    return {
        "records": records,
        "count": len(records),
        "scope": "persistent",
        "label": "持久化安全记录（JSONL，含 AutoFixer 与输出护栏；重启后仍可查询）",
        "assertions": [
            "输出须含免责声明（「免责」或「仅供参考」）",
            "检出胸痛、呼吸困难、昏厥等高危关键词时须附加就医提醒",
            "确定性诊断断言、具体用药剂量、替代就医等由输出护栏检出并改写",
        ],
    }
