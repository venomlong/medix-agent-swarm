"""医疗安全模块：输入侧急症分诊 + 有害内容拦截（fail-fast）"""
from .triage import EmergencyTriage, TriageResult, build_emergency_result
from .harm_filter import HarmfulContentFilter, HarmVerdict, build_blocked_result

__all__ = [
    "EmergencyTriage",
    "TriageResult",
    "build_emergency_result",
    "HarmfulContentFilter",
    "HarmVerdict",
    "build_blocked_result",
]
