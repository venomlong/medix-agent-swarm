"""
请求级 Trace / Span。

模式同 source_collector：ContextVar 保存当前 Trace。
SwarmCoordinator.process() 里 start_trace / save_trace / end_trace；
AgentLoop 用 time.monotonic() 手工记 llm_call 与 skill span。

不引入 OpenTelemetry。LLMClient 调用 record_llm_usage() 写入当前 Trace 与 GLOBAL_USAGE。
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from loguru import logger

from core.log_privacy import mask_pii

# 元/百万 token；可被 LLM_CONFIG["pricing"] = {"input": x, "output": y} 覆盖
PRICING_DEFAULT: Dict[str, float] = {"input": 2.0, "output": 8.0}

# 跨线程读简单数字即可；/api/stats（T2.4）再挂出去
GLOBAL_USAGE: Dict[str, Any] = {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0,
    "llm_calls": 0,
    "cost": 0.0,
}

_current: ContextVar[Optional["Trace"]] = ContextVar("medix_trace", default=None)
_WRITE_LOCK = threading.Lock()
_UNSAFE_SESSION_RE = re.compile(r"[^\w.\-]+", re.ASCII)


@dataclass
class Span:
    name: str
    kind: str
    start: float
    end: float
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def duration_ms(self) -> float:
        return round((self.end - self.start) * 1000.0, 3)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "start": self.start,
            "end": self.end,
            "duration_ms": self.duration_ms,
            "meta": dict(self.meta or {}),
        }


@dataclass
class Trace:
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    session_id: str = ""
    started_at: str = field(
        default_factory=lambda: datetime.now().isoformat(timespec="milliseconds")
    )
    spans: List[Span] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    llm_calls: int = 0
    _t0: float = field(default_factory=time.monotonic, repr=False)

    def add_span(
        self,
        name: str,
        kind: str,
        start: float,
        end: float,
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.spans.append(
            Span(
                name=str(name or ""),
                kind=str(kind or "phase"),
                start=float(start),
                end=float(end),
                meta=dict(meta or {}),
            )
        )

    def add_usage(self, prompt: int = 0, completion: int = 0) -> None:
        self.prompt_tokens += max(0, int(prompt or 0))
        self.completion_tokens += max(0, int(completion or 0))
        self.llm_calls += 1

    def cost(self) -> float:
        pricing = _pricing()
        return round(
            (
                self.prompt_tokens * pricing["input"]
                + self.completion_tokens * pricing["output"]
            )
            / 1_000_000.0,
            6,
        )

    def elapsed(self) -> float:
        return round(time.monotonic() - self._t0, 4)

    def summary(self) -> Dict[str, Any]:
        """给 answer_done / result['trace'] 用的短摘要。"""
        return {
            "trace_id": self.trace_id,
            "total_tokens": self.prompt_tokens + self.completion_tokens,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "llm_calls": self.llm_calls,
            "cost": self.cost(),
            "span_count": len(self.spans),
            "elapsed": self.elapsed(),
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "session_id": self.session_id,
            "started_at": self.started_at,
            "ended_at": datetime.now().isoformat(timespec="milliseconds"),
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.prompt_tokens + self.completion_tokens,
            "llm_calls": self.llm_calls,
            "cost": self.cost(),
            "elapsed": self.elapsed(),
            "span_count": len(self.spans),
            "spans": [span.to_dict() for span in self.spans],
        }


def _pricing() -> Dict[str, float]:
    pricing = dict(PRICING_DEFAULT)
    try:
        import sys

        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        if root not in sys.path:
            sys.path.insert(0, root)
        from config import LLM_CONFIG  # type: ignore

        custom = LLM_CONFIG.get("pricing") if isinstance(LLM_CONFIG, dict) else None
        if isinstance(custom, dict):
            if "input" in custom:
                pricing["input"] = float(custom["input"])
            if "output" in custom:
                pricing["output"] = float(custom["output"])
    except Exception:
        pass
    return pricing


def start_trace(session_id: str, trace_id: Optional[str] = None) -> Token:
    """在 SwarmCoordinator.process() 开头调用。可传入 webapi 生成的 trace_id。"""
    tid = (trace_id or "").strip() or uuid.uuid4().hex[:12]
    trace = Trace(trace_id=tid[:12], session_id=str(session_id or ""))
    return _current.set(trace)


def end_trace(token: Optional[Token]) -> None:
    if token is None:
        return
    try:
        _current.reset(token)
    except (ValueError, LookupError):
        pass


def get_trace() -> Optional[Trace]:
    return _current.get()


def add_span(
    name: str,
    kind: str,
    start: float,
    end: float,
    meta: Optional[Dict[str, Any]] = None,
) -> None:
    """无当前 Trace 时静默跳过（CLI 单测直接跑 Loop 不炸）。"""
    trace = get_trace()
    if trace is None:
        return
    trace.add_span(name, kind, start, end, meta)


def record_llm_usage(prompt_tokens: int = 0, completion_tokens: int = 0) -> None:
    """写当前 trace + GLOBAL_USAGE。T2.2 从 LLMClient 调用；usage 缺失时不要调。"""
    pt = max(0, int(prompt_tokens or 0))
    ct = max(0, int(completion_tokens or 0))
    GLOBAL_USAGE["prompt_tokens"] += pt
    GLOBAL_USAGE["completion_tokens"] += ct
    GLOBAL_USAGE["llm_calls"] += 1
    GLOBAL_USAGE["total_tokens"] = (
        GLOBAL_USAGE["prompt_tokens"] + GLOBAL_USAGE["completion_tokens"]
    )
    pricing = _pricing()
    GLOBAL_USAGE["cost"] = float(GLOBAL_USAGE.get("cost") or 0.0) + (
        pt * pricing["input"] + ct * pricing["output"]
    ) / 1_000_000.0

    trace = get_trace()
    if trace is not None:
        trace.add_usage(pt, ct)


@contextmanager
def record_span(name: str, kind: str, **meta: Any) -> Iterator[None]:
    """可选计时器。AgentLoop 按计划用手写 monotonic，Coordinator 后续埋点可用。"""
    start = time.monotonic()
    ok = True
    try:
        yield
    except Exception:
        ok = False
        raise
    finally:
        payload = dict(meta)
        payload.setdefault("ok", ok)
        add_span(name, kind, start, time.monotonic(), payload)


def get_traces_dir() -> Path:
    override = os.environ.get("MEDIX_TRACES_DIR", "").strip()
    if override:
        return Path(override)
    return (
        Path(__file__).resolve().parent.parent / "memory" / "swarm" / "traces"
    )


def _safe_session_id(session_id: str) -> str:
    text = (session_id or "").strip() or "unknown"
    cleaned = _UNSAFE_SESSION_RE.sub("_", text).strip("._") or "unknown"
    return cleaned[:80]


def trace_path(session_id: str) -> Path:
    return get_traces_dir() / f"{_safe_session_id(session_id)}.jsonl"


def _mask_value(value: Any) -> Any:
    if isinstance(value, str):
        return mask_pii(value)
    if isinstance(value, dict):
        return {k: _mask_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_mask_value(v) for v in value]
    return value


def _mask_trace_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """落盘前脱敏。trace_id / session_id 保持原样，否则 T2.4 按会话查文件会对不上。"""
    out = dict(payload)
    spans = []
    for raw in payload.get("spans") or []:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        if isinstance(item.get("name"), str):
            item["name"] = mask_pii(item["name"])
        if isinstance(item.get("kind"), str):
            item["kind"] = mask_pii(item["kind"])
        item["meta"] = _mask_value(item.get("meta") or {})
        spans.append(item)
    out["spans"] = spans
    return out


def save_trace(trace: Optional[Trace] = None) -> None:
    """一行一个 to_dict()。写失败只 warn 不抛。"""
    target = trace if trace is not None else get_trace()
    if target is None:
        return
    try:
        path = trace_path(target.session_id)
        payload = _mask_trace_payload(target.to_dict())
        line = json.dumps(payload, ensure_ascii=False)
        with _WRITE_LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
    except Exception as exc:
        logger.warning(f"Failed to persist trace: {exc}")
