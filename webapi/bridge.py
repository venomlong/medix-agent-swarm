"""
订阅 SharedContext 事件，并在 process 结束后补齐前端需要的帧。

单 Agent 路径不创建 SharedContext：在此合成最小时间线事件。
不改 Coordinator 路由 / 协作算法。
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any, Callable, Dict, List, Optional, Tuple

from loguru import logger

from .sse import with_common

EmitFn = Callable[[str, Dict[str, Any]], None]


def _skip_event_types():
    from swarm.events import EventType

    return {
        EventType.CONTEXT_UPDATED,
        EventType.AGENT_QUESTION,
        EventType.AGENT_ANSWER,
    }


class CoordinatorRunner:
    """
    在独立线程的事件循环里持有 SwarmCoordinator 单例。

    FastAPI 主循环只负责 SSE；process() 在工作线程跑，
    订阅回调用 call_soon_threadsafe 写入 asyncio.Queue。
    """

    def __init__(self) -> None:
        self.coordinator: Any = None
        self.error: Optional[BaseException] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._lock: Optional[asyncio.Lock] = None
        self._ready = threading.Event()

    def start(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="medix-coordinator",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(timeout=5):
            self.error = RuntimeError("协调器工作线程未能启动")
            return
        asyncio.run_coroutine_threadsafe(self._init(), self._loop)

    async def _init(self) -> None:
        try:
            from swarm.swarm_coordinator import SwarmCoordinator

            self._lock = asyncio.Lock()
            self.coordinator = SwarmCoordinator()
            logger.info("SwarmCoordinator 单例已在工作线程创建")
        except Exception as exc:
            self.error = exc
            logger.error(f"SwarmCoordinator 初始化失败: {exc}")

    def _run_loop(self) -> None:
        assert self._loop is not None
        asyncio.set_event_loop(self._loop)
        self._loop.call_soon(self._ready.set)
        self._loop.run_forever()

    def stop(self) -> None:
        loop = self._loop
        if loop is None:
            return
        loop.call_soon_threadsafe(loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._loop = None

    def submit_process(
        self,
        question: str,
        session_id: str,
        listener: Callable[[Any], None],
        on_delta: Optional[Callable[[str], None]] = None,
    ):
        if self._loop is None:
            raise RuntimeError("CoordinatorRunner 未启动")
        if self.coordinator is None:
            raise RuntimeError(self._format_init_error())

        async def _job():
            from swarm.shared_context import (
                reset_answer_delta_listener,
                reset_event_listener,
                set_answer_delta_listener,
                set_event_listener,
            )

            assert self._lock is not None
            assert self.coordinator is not None
            async with self._lock:
                token = set_event_listener(listener)
                delta_token = set_answer_delta_listener(on_delta)
                try:
                    return await self.coordinator.process(
                        question, session_id=session_id
                    )
                finally:
                    reset_answer_delta_listener(delta_token)
                    reset_event_listener(token)

        return asyncio.run_coroutine_threadsafe(_job(), self._loop)

    def submit_sync(self, fn, *args, **kwargs):
        """在协调器线程里跑同步函数（Mem0 / Milvus），避免占住 FastAPI 循环。"""
        if self._loop is None:
            raise RuntimeError("CoordinatorRunner 未启动")

        async def _job():
            if self._lock is not None:
                async with self._lock:
                    return await asyncio.to_thread(fn, *args, **kwargs)
            return await asyncio.to_thread(fn, *args, **kwargs)

        return asyncio.run_coroutine_threadsafe(_job(), self._loop)

    def _format_init_error(self) -> str:
        if self.error is None:
            return "协调器尚未就绪"
        return f"协调器初始化失败：{self.error}"


def map_shared_event(
    event: Any, session_id: str
) -> Optional[Tuple[str, Dict[str, Any]]]:
    from swarm.events import EventType

    if event.type in _skip_event_types():
        return None

    data = with_common(dict(event.data or {}), session_id, event.timestamp.isoformat())
    if event.source_agent:
        data.setdefault("source_agent", event.source_agent)

    if event.type in (EventType.SUBTASK_STARTED, EventType.SUBTASK_COMPLETED):
        data.setdefault("assigned_agent", event.source_agent)
    if event.type in (EventType.SKILL_STARTED, EventType.SKILL_COMPLETED):
        data.setdefault("assigned_agent", event.source_agent)
        data.setdefault("agent", event.source_agent)
        data.setdefault("name", data.get("skill_name") or data.get("name") or "")
        data.setdefault("ok", data.get("ok", True))
    if event.type == EventType.TASK_DECOMPOSED:
        data.setdefault("description", data.get("type") or "")

    return event.type.value, data


def map_answer_done(
    result: Dict[str, Any],
    session_id: str,
    elapsed_s: float,
) -> Dict[str, Any]:
    swarm_enabled = bool(result.get("swarm_enabled"))
    agents = result.get("agents_involved") or []
    if swarm_enabled:
        agent_count = len(agents) or int(result.get("subtasks_completed") or 0) or 1
    else:
        agent_count = 1

    total = result.get("total_time")
    if total is None:
        total = elapsed_s

    return with_common(
        {
            "body": result.get("answer") or "",
            "suggestions": result.get("suggestions") or [],
            "disclaimer": result.get("disclaimer") or "",
            "elapsed": f"{float(total):.1f}s",
            "agent_count": agent_count,
            "timed_out": bool(result.get("timeout_occurred")),
            "alert": None,
            "alert_note": None,
            "sources": [],
            "swarm_enabled": swarm_enabled,
            "session_id": session_id,
        },
        session_id,
    )


def classify_error(exc: BaseException) -> Tuple[str, str]:
    text = str(exc) or exc.__class__.__name__
    low = text.lower()
    if any(k in low for k in ("api_key", "authentication", "unauthorized", "401")):
        return (
            "llm_error",
            "模型服务认证失败，请检查仓库父目录 config.py 中的密钥配置。",
        )
    if "timeout" in low or isinstance(exc, TimeoutError):
        return "timeout", "处理超时，请稍后重试或简化问题。"
    if "no module named" in low:
        return (
            "internal",
            f"后端依赖未就绪（{text.strip()[:80]}）。请在已安装 requirements.txt 的环境中启动。",
        )
    snippet = text.replace("\n", " ").strip()[:180]
    return "internal", f"处理失败：{snippet}" if snippet else "处理失败，请稍后重试。"


def synthesize_single_agent(
    emit: EmitFn,
    question: str,
    session_id: str,
    result: Dict[str, Any],
    elapsed_s: float,
) -> None:
    """单 Agent 无 SharedContext：合成最小时间线，避免右侧空白。"""
    agent_id = result.get("agent_id") or "consultation_agent"
    reason = result.get("route_reason") or "单 Agent 快速应答"
    emit(
        "routing",
        with_common(
            {"mode": "single", "subtask_count": 1, "reason": reason},
            session_id,
        ),
    )
    emit(
        "swarm_started",
        with_common(
            {"question": question, "num_subtasks": 1},
            session_id,
        ),
    )
    emit(
        "task_decomposed",
        with_common(
            {
                "subtask_id": "single-1",
                "type": "consultation",
                "assigned_agent": agent_id,
                "description": question[:80],
            },
            session_id,
        ),
    )
    emit(
        "subtask_started",
        with_common(
            {"subtask_id": "single-1", "assigned_agent": agent_id},
            session_id,
        ),
    )
    emit(
        "subtask_completed",
        with_common(
            {
                "subtask_id": "single-1",
                "assigned_agent": agent_id,
                "duration_s": round(elapsed_s, 1),
                "result_summary": (result.get("answer") or "")[:200],
            },
            session_id,
        ),
    )
    emit(
        "swarm_completed",
        with_common(
            {
                "duration": round(elapsed_s, 1),
                "agents_count": 1,
                "timeout_occurred": False,
            },
            session_id,
        ),
    )


def emit_timeout_if_needed(emit: EmitFn, result: Dict[str, Any], session_id: str) -> None:
    if not result.get("timeout_occurred"):
        return
    completed: List[str] = list(result.get("agents_involved") or [])
    emit(
        "timeout_occurred",
        with_common(
            {
                "completed_agents": completed,
                "pending_agents": [],
            },
            session_id,
        ),
    )


def attach_live_listener(
    emit: EmitFn,
    session_id: str,
    flags: Dict[str, Any],
) -> Callable[[Any], None]:
    started_at: Dict[str, float] = {}

    def listener(event: Any) -> None:
        import time as _time

        from swarm.events import EventType

        mapped = map_shared_event(event, session_id)
        if mapped is None:
            return
        name, data = mapped
        if event.type == EventType.SWARM_STARTED:
            flags["live"] = True
            emit(
                "routing",
                with_common(
                    {
                        "mode": "swarm",
                        "subtask_count": event.data.get("num_subtasks", 0),
                    },
                    session_id,
                    event.timestamp.isoformat(),
                ),
            )
        if event.type == EventType.SUBTASK_STARTED:
            sid = str(data.get("subtask_id") or "")
            if sid:
                started_at[sid] = _time.monotonic()
        if event.type == EventType.SUBTASK_COMPLETED:
            sid = str(data.get("subtask_id") or "")
            t0 = started_at.get(sid)
            if t0 is not None:
                data.setdefault("duration_s", round(_time.monotonic() - t0, 1))
        emit(name, data)

    return listener
