"""
安全记录 JSONL 持久化。

AutoFixer 原先只写进程内 deque，重启后安全页为空。
这里追加写入 memory/swarm/safety_log.jsonl，供 /api/safety/fixes 重启后仍可读。
落盘内容做轻量 PII 掩码（手机号/身份证/邮箱），避免病历里的联系方式进日志。
掩码规则复用 core.log_privacy.mask_pii，与全局日志脱敏保持一致。
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from core.log_privacy import mask_pii

_LOCK = threading.Lock()


def get_log_path() -> Path:
    override = os.environ.get("MEDIX_SAFETY_LOG_PATH", "").strip()
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent / "memory" / "swarm" / "safety_log.jsonl"


def _mask_value(value: Any) -> Any:
    if isinstance(value, str):
        return mask_pii(value)
    if isinstance(value, dict):
        return {k: _mask_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_mask_value(v) for v in value]
    return value


def record(
    kind: str,
    detail: str,
    session_id: str = "",
    source: str = "",
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """追加一条安全记录。写失败只 warn，不打断主流程。"""
    payload: Dict[str, Any] = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "kind": mask_pii(str(kind or "")),
        "detail": mask_pii(str(detail or "")),
        "session_id": mask_pii(str(session_id or "")),
        "source": source or "",
    }
    if extra:
        payload["extra"] = _mask_value(extra)

    path = get_log_path()
    line = json.dumps(payload, ensure_ascii=False)
    try:
        with _LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
    except Exception as exc:
        logger.warning(f"Failed to persist safety log: {exc}")


def get_records(limit: int = 200) -> List[Dict[str, Any]]:
    """读文件尾部，倒序（最新在前）。文件不存在或损坏行跳过。"""
    path = get_log_path()
    if not path.exists():
        return []
    try:
        with _LOCK:
            raw = path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.warning(f"Failed to read safety log: {exc}")
        return []

    rows: List[Dict[str, Any]] = []
    for line in raw.splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            item = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    rows.reverse()
    if limit and limit > 0:
        return rows[:limit]
    return rows
