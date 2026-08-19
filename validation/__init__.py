"""
输出验证和自动修复
Harness Engineering 核心：运行时验证 + 自动修复 + 输出护栏
"""
from .auto_fixer import AutoFixer, get_fix_records
from .guardrail import GuardrailVerdict, OutputGuardrail

__all__ = ['AutoFixer', 'get_fix_records', 'GuardrailVerdict', 'OutputGuardrail']
