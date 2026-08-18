"""把事件名 + JSON 编成 W3C SSE 帧。"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, Optional


def utc_now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def format_sse(event: str, data: Dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {payload}\n\n"


def with_common(
    data: Optional[Dict[str, Any]],
    session_id: str,
    ts: Optional[str] = None,
) -> Dict[str, Any]:
    body = dict(data or {})
    body.setdefault("ts", ts or utc_now_iso())
    body.setdefault("session_id", session_id)
    return body
