"""医疗安全模块：输入侧急症分诊（fail-fast）"""
from .triage import EmergencyTriage, TriageResult, build_emergency_result

__all__ = ["EmergencyTriage", "TriageResult", "build_emergency_result"]
