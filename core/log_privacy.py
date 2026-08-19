"""
全局日志 PII 脱敏。

医疗对话里用户可能随口留下手机号、身份证、邮箱；Agent Loop / Coordinator
会把 question、tool arguments 打进 loguru。这里只做正则掩码，不截断整句，
避免把调试信息滤没。

规则与 validation.safety_log 共用（JSONL 持久化走同一套 mask_pii）。
"""
from __future__ import annotations

import re
from typing import Any, Dict

from loguru import logger

# 与 T3.2 safety_log 保持一致：11 位大陆手机、18 位身份证、常见邮箱
_PHONE_RE = re.compile(r"1[3-9]\d{9}")
_ID_RE = re.compile(r"\d{17}[\dXx]")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

_INSTALLED = False


def mask_pii(text: str) -> str:
    """掩码手机号、18 位身份证、邮箱。空值 / None 原样返回。"""
    if not text:
        return text
    if not isinstance(text, str):
        text = str(text)
    # 身份证必须先于手机号：18 位数字里常嵌 1[3-9]xxxxxxxxx，后处理会把证件号切碎
    text = _ID_RE.sub(lambda m: m.group(0)[:4] + "*" * 10 + m.group(0)[-4:], text)
    text = _PHONE_RE.sub(lambda m: m.group(0)[0] + "*" * 10, text)
    text = _EMAIL_RE.sub(lambda m: m.group(0)[0] + "***@***", text)
    return text


def patch_log_record(record: Dict[str, Any]) -> None:
    """loguru patcher：改已格式化的 message，以及 extra 里的字符串。"""
    message = record.get("message")
    if isinstance(message, str):
        record["message"] = mask_pii(message)
    extra = record.get("extra")
    if isinstance(extra, dict):
        for key, value in extra.items():
            if isinstance(value, str) and key != "trace":
                extra[key] = mask_pii(value)


def _combined_patcher(record: Dict[str, Any]) -> None:
    """PII 脱敏之后再写入 trace_id，避免把 12 位 hex 误伤，也避免明文进 extra。"""
    patch_log_record(record)
    try:
        from core.tracing import patch_log_trace

        patch_log_trace(record)
    except Exception:
        extra = record.get("extra")
        if isinstance(extra, dict):
            extra.setdefault("trace", "-")


def install_log_privacy() -> None:
    """在进程入口挂上全局 patcher。幂等，不撤掉已有 handler。

    extra['trace'] 必须预先声明：loguru 不允许 patcher 新增未配置的 extra 键，
    否则 `{extra[trace]}` 会 KeyError。无当前 Trace 时保持 '-'。
    """
    global _INSTALLED
    if _INSTALLED:
        return
    logger.configure(extra={"trace": "-"}, patcher=_combined_patcher)
    _INSTALLED = True
