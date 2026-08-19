"""
输出侧护栏：规则层常开，LLM 只在违规时介入重写。

与现有模块的分工（互补，不替代）：
- ConstraintValidator：过程约束（工具白名单、免责声明、高危就医提醒）
- AutoFixer：字符串级修补（补免责声明、补就医提醒）
- OutputGuardrail：深层违规（确定性诊断、具体剂量、替代就医），必要时 LLM 改写

急症短路路径不过本模块，避免拖慢 fail-fast。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from loguru import logger

from swarm.events import Event, EventType
from swarm.shared_context import emit_live_event
from .auto_fixer import AutoFixer
from . import safety_log

# 相对具体的病名，避免「就是感冒」这类口语误伤
_DISEASE = (
    r"心肌炎|心肌梗死|心梗|冠心病|高血压|糖尿病|肺炎|癌症|恶性肿瘤|肿瘤|"
    r"肝炎|肾炎|脑梗|脑出血|白血病|艾滋病|甲亢|痛风|抑郁症|哮喘|胃炎|"
    r"肺结核|肝硬化|中风|癫痫|尿毒症|心力衰竭"
)

_CERTAINTY_RE = re.compile(
    rf"(?:你(?:得的?|患的?)是|您患有|确诊为|肯定是|一定是|就是得了)"
    rf"\s*[：:为]?\s*.{{0,8}}({_DISEASE})"
)

# 具体剂量指令，且邻近「服用/口服/吃」
_DOSAGE_RE = re.compile(
    r"(?:(?:服用|口服|吃).{0,20}(?:每次|每日|每天|一天)\s*\d+\s*(?:mg|毫克|g|克|片|粒|毫升|ml)"
    r"|(?:每次|每日|每天|一天)\s*\d+\s*(?:mg|毫克|g|克|片|粒|毫升|ml).{0,12}(?:服用|口服|吃))",
    re.IGNORECASE,
)

_DANGEROUS_PATTERNS = [
    ("不用就医", "不用就医"),
    ("不需要看医生", "不需要看医生"),
    ("自行停药", "自行停药"),
    ("加倍剂量", "加倍剂量"),
]

_REWRITE_SYSTEM = (
    "你是医疗输出安全编辑。根据违规原因改写回答。"
    "要求：保留有用医学信息；把确定性诊断改为可能性表述（如「可能提示」「需由医生鉴别」）；"
    "删除具体用药剂量，改为「遵医嘱」；不要建议不去就医、自行停药或加倍剂量；"
    "若原文有免责声明则保留。只输出改写后的正文，不要解释。"
)


@dataclass
class GuardrailVerdict:
    passed: bool
    violations: List[Dict[str, str]] = field(default_factory=list)
    rewritten: bool = False
    final_answer: str = ""
    action: str = "pass"  # pass / rewrite / regex_fallback


class OutputGuardrail:
    def __init__(self, llm_client: Any = None):
        self.llm_client = llm_client
        self._fixer = AutoFixer()

    def check_rules(self, answer: str) -> List[Dict[str, str]]:
        """确定性规则检测，毫秒级。"""
        text = answer or ""
        violations: List[Dict[str, str]] = []

        for match in _CERTAINTY_RE.finditer(text):
            violations.append({
                "type": "certainty_diagnosis",
                "evidence": match.group(0),
            })

        for match in _DOSAGE_RE.finditer(text):
            violations.append({
                "type": "dosage_instruction",
                "evidence": match.group(0),
            })

        for keyword, evidence in _DANGEROUS_PATTERNS:
            if keyword in text:
                violations.append({
                    "type": "dangerous_advice",
                    "evidence": evidence,
                })

        return violations

    async def review_and_fix(
        self,
        question: str,
        answer: str,
        session_id: str = "",
    ) -> GuardrailVerdict:
        """
        1. 无违规 → 直接通过（零额外延迟）
        2. 有违规且 llm 可用 → 带着违规原因重写一次
        3. 重写后再跑规则；仍违规或 LLM 失败 → 正则保守替换兜底
        4. 全程写入 safety_log
        """
        original = answer or ""
        violations = self.check_rules(original)
        if not violations:
            # 未命中不落盘，避免把每次正常回答都写进安全页
            return GuardrailVerdict(
                passed=True,
                violations=[],
                rewritten=False,
                final_answer=original,
                action="pass",
            )

        summary = "；".join(
            f"{v.get('type')}:{v.get('evidence')}" for v in violations
        )
        logger.warning(f"🛡️ Output guardrail hit: {summary[:180]}")

        rewritten_text: Optional[str] = None
        if self._llm_available():
            try:
                rewritten_text = await self._rewrite_with_llm(
                    question, original, violations
                )
            except Exception as exc:
                logger.warning(f"Guardrail LLM rewrite failed, fallback to regex: {exc}")
                rewritten_text = None

        action = "regex_fallback"
        final = original
        rewritten = False

        if rewritten_text:
            remaining = self.check_rules(rewritten_text)
            if not remaining:
                final = rewritten_text
                rewritten = True
                action = "rewrite"
            else:
                logger.warning("Guardrail rewrite still violating, applying regex fallback")
                final = self._regex_fallback(rewritten_text)
                rewritten = final != original
                action = "regex_fallback"
        else:
            final = self._regex_fallback(original)
            rewritten = final != original
            action = "regex_fallback"

        # 重写可能丢掉免责声明；用 AutoFixer 补回，不重复发明补丁逻辑
        patched = self._fixer.fix_missing_disclaimer(final)
        if patched != final:
            final = patched
            rewritten = True

        remaining = self.check_rules(final)
        verdict = GuardrailVerdict(
            passed=len(remaining) == 0,
            violations=violations,
            rewritten=rewritten,
            final_answer=final,
            action=action,
        )
        safety_log.record(
            kind=violations[0]["type"],
            detail=summary,
            session_id=session_id,
            source="guardrail",
            extra={
                "violations": violations,
                "rewritten": rewritten,
                "action": action,
                "passed": verdict.passed,
            },
        )
        return verdict

    def _llm_available(self) -> bool:
        return self.llm_client is not None and hasattr(self.llm_client, "chat")

    async def _rewrite_with_llm(
        self,
        question: str,
        answer: str,
        violations: List[Dict[str, str]],
    ) -> str:
        bullets = "\n".join(
            f"- {v.get('type')}: {v.get('evidence')}" for v in violations
        )
        user = (
            f"用户问题：\n{question}\n\n"
            f"原回答：\n{answer}\n\n"
            f"违规项：\n{bullets}"
        )
        text = await self.llm_client.chat(
            [
                {"role": "system", "content": _REWRITE_SYSTEM},
                {"role": "user", "content": user},
            ],
            temperature=0,
        )
        cleaned = (text or "").strip()
        if not cleaned:
            raise ValueError("empty rewrite from LLM")
        return cleaned

    def _regex_fallback(self, output: str) -> str:
        """沿用 AutoFixer.remove_diagnosis_statements，并补剂量/危险建议替换。"""
        text = self._fixer.remove_diagnosis_statements(output)
        text = re.sub(r"你(?:得的?|患的?)是", "你的症状可能与", text)
        text = re.sub(r"就是得了", "可能与", text)
        text = re.sub(r"一定是", "可能是", text)
        text = re.sub(
            r"(?:每次|每日|每天|一天)\s*\d+\s*(?:mg|毫克|g|克|片|粒|毫升|ml)",
            "遵医嘱剂量",
            text,
            flags=re.IGNORECASE,
        )
        text = text.replace("不用就医", "建议及时就医")
        text = text.replace("不需要看医生", "建议及时就医")
        text = text.replace("自行停药", "不要自行停药，请咨询医生")
        text = text.replace("加倍剂量", "不要自行调整剂量，请遵医嘱")
        return text


async def apply_guardrail_to_result(
    guardrail: OutputGuardrail,
    question: str,
    result: Dict[str, Any],
    session_id: str = "",
    shared_context: Any = None,
) -> GuardrailVerdict:
    """
    对 Coordinator 最终答案跑护栏：覆盖 result['answer']，
    命中时写入 result['guardrail'] 并发布 GUARDRAIL_TRIGGERED。
    """
    answer = result.get("answer") or ""
    verdict = await guardrail.review_and_fix(question, answer, session_id=session_id)
    result["answer"] = verdict.final_answer

    if not verdict.violations:
        return verdict

    payload = {
        "triggered": True,
        "violations": verdict.violations,
        "rewritten": verdict.rewritten,
        "action": verdict.action,
    }
    result["guardrail"] = payload

    emit_live_event(
        Event(
            type=EventType.GUARDRAIL_TRIGGERED,
            source_agent="output_guardrail",
            data={
                "violations": [
                    {"type": v.get("type"), "evidence": v.get("evidence")}
                    for v in verdict.violations
                ],
                "rewritten": verdict.rewritten,
                "action": verdict.action,
            },
        ),
        shared_context,
    )
    return verdict
