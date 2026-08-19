"""
输入侧急症分诊（fail-fast）

设计（两层判定）：
1. 规则层（毫秒级、确定性）：
   - 强规则：单个关键词即可判定急症（如"昏迷""大出血""自杀"）
   - 组合规则：主症状 + 伴随症状同时出现才判定（如"胸痛"+"冒冷汗"）
2. LLM 层（仅边缘情况）：
   - 规则层命中"边缘关键词"（如单独的"胸痛"）但无法确定时，
     用 temperature=0 的小 prompt 让 LLM 做二分类
   - LLM 不可用/失败时保守降级为"非急症"——此时输出侧
     AutoFixer / Guardrail 仍会补就医提醒，形成纵深防御

判定为急症后由 Coordinator 短路：跳过任务分解和 Swarm，
直接返回结构化急救指引（秒级响应），并发布 emergency_triggered 事件。
"""
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from loguru import logger


@dataclass
class TriageResult:
    """分诊结果"""
    is_emergency: bool
    category: str = "none"      # cardiac / stroke / consciousness / bleeding / poisoning / psych_crisis / respiratory / allergy / general
    matched: List[str] = field(default_factory=list)
    reason: str = ""
    method: str = "rule"        # rule / llm / none

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_emergency": self.is_emergency,
            "category": self.category,
            "matched": self.matched,
            "reason": self.reason,
            "method": self.method,
        }


# ---------- 规则定义 ----------

def _normalize_question(text: str) -> str:
    """去掉空白/标点，并把「吐了血」收成「吐血」，避免口语漏匹配。"""
    t = (text or "").strip()
    t = re.sub(r"[\s\u3000，,。.!！？?、；;：:]+", "", t)
    t = (
        t.replace("吐了血", "吐血")
        .replace("呕了血", "呕血")
        .replace("咳了血", "咳血")
        .replace("咯了血", "咯血")
    )
    return t


# 强规则：出现即判定急症（category, 关键词列表）
STRONG_RULES: List[Dict[str, Any]] = [
    {
        "category": "consciousness",
        "keywords": ["昏迷", "意识不清", "叫不醒", "晕倒不醒", "抽搐", "癫痫发作", "神志不清"],
    },
    {
        "category": "bleeding",
        "keywords": [
            "大出血", "出血不止", "呕血", "吐血", "咯血", "咳血", "喷血",
            "咳血不止", "血流不止", "消化道出血",
        ],
    },
    {
        "category": "poisoning",
        "keywords": ["误服", "误吞", "农药中毒", "药物中毒", "煤气中毒", "一氧化碳中毒", "过量服药", "吞了电池"],
    },
    {
        "category": "psych_crisis",
        "keywords": ["自杀", "自残", "不想活", "轻生", "结束生命", "活不下去"],
    },
    {
        "category": "respiratory",
        "keywords": ["窒息", "喘不上气", "呼吸骤停", "卡住喉咙", "噎住"],
    },
    {
        "category": "cardiac",
        "keywords": ["压榨性胸痛", "压榨性疼痛", "心脏骤停", "心跳骤停"],
    },
    {
        "category": "allergy",
        "keywords": ["过敏性休克", "喉头水肿"],
    },
    {
        "category": "stroke",
        "keywords": ["嘴歪眼斜", "口眼歪斜", "半身不遂", "一侧肢体无力", "突然说话不清", "突然口齿不清"],
    },
]

# 组合规则：主症状 + 至少一个伴随症状
COMBO_RULES: List[Dict[str, Any]] = [
    {
        "category": "cardiac",
        "primary": ["胸痛", "胸口疼", "胸口痛", "心前区疼"],
        "secondary": ["呼吸困难", "冒冷汗", "大汗", "出冷汗", "放射", "左臂", "濒死感", "晕", "恶心"],
    },
    {
        "category": "stroke",
        "primary": ["剧烈头痛", "头痛欲裂", "这辈子最疼的头痛"],
        "secondary": ["呕吐", "视力模糊", "看不清", "说话不清", "肢体无力", "麻木", "突然"],
    },
    {
        "category": "respiratory",
        "primary": ["呼吸困难", "气喘"],
        "secondary": ["嘴唇发紫", "口唇发绀", "说不出话", "无法平躺", "越来越严重", "突然"],
    },
]

# 边缘关键词：单独出现时交给 LLM 判定（规则无法确定）
BORDERLINE_KEYWORDS: List[str] = [
    "胸痛", "胸口疼", "呼吸困难", "剧烈头痛", "剧烈腹痛", "高烧不退", "便血", "心悸",
]

_LLM_TRIAGE_SYSTEM = """你是急诊分诊助手。判断用户描述是否属于需要立即就医/拨打急救电话的急症。

急症标准（任一满足即为急症）：
- 提示心梗/脑卒中/大出血/中毒/窒息等危及生命的情况
- 症状突发且严重（如突发剧烈疼痛、意识改变）
- 存在自杀/自残风险

非急症：一般健康咨询、慢病管理、轻症（普通感冒、轻微头痛）、科普问题。

只输出 JSON：{"is_emergency": true/false, "category": "cardiac|stroke|bleeding|poisoning|psych_crisis|respiratory|general|none", "reason": "一句话理由"}"""


class EmergencyTriage:
    """急症分诊器：规则层 + 可选 LLM 层"""

    def __init__(self, llm_client: Optional[Any] = None):
        self.llm_client = llm_client

    def check_rules(self, question: str) -> TriageResult:
        """规则层：强规则 + 组合规则，确定性、毫秒级"""
        text = _normalize_question(question)
        if not text:
            return TriageResult(is_emergency=False, method="none")

        for rule in STRONG_RULES:
            matched = [kw for kw in rule["keywords"] if kw in text]
            if matched:
                return TriageResult(
                    is_emergency=True,
                    category=rule["category"],
                    matched=matched,
                    reason=f"命中强规则关键词：{'、'.join(matched)}",
                    method="rule",
                )

        for rule in COMBO_RULES:
            primary_hits = [kw for kw in rule["primary"] if kw in text]
            if not primary_hits:
                continue
            secondary_hits = [kw for kw in rule["secondary"] if kw in text]
            if secondary_hits:
                return TriageResult(
                    is_emergency=True,
                    category=rule["category"],
                    matched=primary_hits + secondary_hits,
                    reason=(
                        f"命中组合规则：主症状（{'、'.join(primary_hits)}）"
                        f"+ 伴随症状（{'、'.join(secondary_hits)}）"
                    ),
                    method="rule",
                )

        return TriageResult(is_emergency=False, method="rule")

    def is_borderline(self, question: str) -> List[str]:
        """是否命中边缘关键词（需要 LLM 进一步判定）"""
        text = _normalize_question(question)
        return [kw for kw in BORDERLINE_KEYWORDS if kw in text]

    async def triage(self, question: str) -> TriageResult:
        """
        完整分诊：规则层 → （边缘情况）LLM 层

        LLM 失败时保守返回非急症（输出侧护栏兜底），不阻塞主流程。
        """
        rule_result = self.check_rules(question)
        if rule_result.is_emergency:
            logger.warning(f"🚨 急症分诊命中（规则层）: {rule_result.reason}")
            return rule_result

        borderline = self.is_borderline(question)
        if not borderline or self.llm_client is None:
            return rule_result

        try:
            content = await self.llm_client.chat(
                messages=[
                    {"role": "system", "content": _LLM_TRIAGE_SYSTEM},
                    {"role": "user", "content": question},
                ],
                temperature=0,
                max_tokens=200,
            )
            match = re.search(r"\{.*\}", content or "", re.DOTALL)
            if not match:
                raise ValueError(f"无法从 LLM 响应中解析 JSON: {(content or '')[:100]}")
            verdict = json.loads(match.group())
            is_emergency = bool(verdict.get("is_emergency"))
            result = TriageResult(
                is_emergency=is_emergency,
                category=str(verdict.get("category") or ("general" if is_emergency else "none")),
                matched=borderline,
                reason=str(verdict.get("reason") or "LLM 分诊判定"),
                method="llm",
            )
            if is_emergency:
                logger.warning(f"🚨 急症分诊命中（LLM 层）: {result.reason}")
            return result
        except Exception as e:
            logger.warning(f"LLM 分诊失败，保守降级为非急症（输出侧护栏兜底）: {e}")
            return TriageResult(
                is_emergency=False,
                matched=borderline,
                reason=f"LLM 分诊失败：{e}",
                method="llm",
            )


# ---------- 急救指引 ----------

_CATEGORY_GUIDANCE: Dict[str, str] = {
    "cardiac": (
        "您描述的症状提示可能存在心脏急症（如心肌梗死）。\n\n"
        "请立即采取以下措施：\n"
        "1. **立即拨打 120 急救电话**，说明症状和所在位置\n"
        "2. 立即停止一切活动，原地坐下或半卧位休息，保持镇静\n"
        "3. 解开领口、腰带，保持呼吸通畅\n"
        "4. 如身边有硝酸甘油且既往医生开具过，可舌下含服 1 片\n"
        "5. **不要自行驾车或步行去医院**，等待救护车\n"
        "6. 如出现意识丧失，请身边人立即开始心肺复苏（CPR）"
    ),
    "stroke": (
        "您描述的症状提示可能存在脑卒中（中风）。脑卒中抢救每分钟都很关键。\n\n"
        "请立即采取以下措施：\n"
        "1. **立即拨打 120 急救电话**，告知怀疑中风及症状开始时间\n"
        "2. 让患者平卧，头偏向一侧，防止呕吐物误吸\n"
        "3. **不要喂水、喂药、喂食物**\n"
        "4. 记录症状开始的准确时间（溶栓治疗有时间窗）\n"
        "5. 不要等待症状自行缓解，即使症状短暂消失也必须就医"
    ),
    "consciousness": (
        "您描述的情况（意识障碍/抽搐）属于危急状况。\n\n"
        "请立即采取以下措施：\n"
        "1. **立即拨打 120 急救电话**\n"
        "2. 将患者置于侧卧位（复苏体位），保持呼吸道通畅\n"
        "3. 抽搐时不要强行按压肢体、不要往嘴里塞任何东西\n"
        "4. 观察并记录持续时间和表现，供急救人员参考\n"
        "5. 如呼吸心跳停止，立即开始心肺复苏"
    ),
    "bleeding": (
        "您描述的出血情况属于危急状况。\n\n"
        "请立即采取以下措施：\n"
        "1. **立即拨打 120 急救电话**\n"
        "2. 体表出血：用干净布料直接按压止血，持续加压不要松开\n"
        "3. 呕血/咳血：保持坐位或侧卧，头偏向一侧，禁食禁水\n"
        "4. 记录出血量和颜色（鲜红/暗红/咖啡色），供医生参考\n"
        "5. 不要自行服用任何止血药物"
    ),
    "poisoning": (
        "您描述的情况提示可能存在中毒/误服，属于危急状况。\n\n"
        "请立即采取以下措施：\n"
        "1. **立即拨打 120 急救电话**\n"
        "2. 保留误服物品的包装/瓶子，带去医院供医生识别\n"
        "3. **不要自行催吐**（强酸强碱/石油类误服催吐会造成二次伤害）\n"
        "4. 意识清醒者可少量清水漱口，不要大量喝水\n"
        "5. 煤气/一氧化碳中毒：立即开窗通风并转移到空气新鲜处"
    ),
    "psych_crisis": (
        "感谢您愿意说出来，您的感受很重要，您并不孤单。\n\n"
        "请立即寻求帮助：\n"
        "1. **全国心理援助热线：12356**（24 小时）\n"
        "2. 北京心理危机研究与干预中心热线：010-82951332\n"
        "3. 如有紧急危险，请立即拨打 **110 或 120**\n"
        "4. 请尽量不要独处，联系信任的家人或朋友陪伴您\n"
        "5. 这些痛苦的感受是可以被治疗和缓解的，专业帮助非常有效"
    ),
    "respiratory": (
        "您描述的呼吸问题属于危急状况。\n\n"
        "请立即采取以下措施：\n"
        "1. **立即拨打 120 急救电话**\n"
        "2. 保持坐位或半卧位（不要平躺），解开领口\n"
        "3. 异物卡喉且无法咳出：请身边人立即实施海姆立克急救法\n"
        "4. 有哮喘史者立即使用急救吸入剂（如沙丁胺醇）\n"
        "5. 保持环境通风，减少活动和讲话"
    ),
    "allergy": (
        "您描述的症状提示可能存在严重过敏反应（过敏性休克）。\n\n"
        "请立即采取以下措施：\n"
        "1. **立即拨打 120 急救电话**\n"
        "2. 立即脱离过敏原（食物、药物、蚊虫叮咬处等）\n"
        "3. 如随身携带肾上腺素自动注射笔，立即使用\n"
        "4. 平卧并抬高下肢；如呼吸困难则改半卧位\n"
        "5. 症状可能快速进展，即使暂时缓解也必须就医"
    ),
    "general": (
        "您描述的情况可能属于急症。\n\n"
        "请立即采取以下措施：\n"
        "1. **立即拨打 120 急救电话**或前往最近的急诊科\n"
        "2. 保持镇静，减少活动\n"
        "3. 记录症状开始时间和变化过程，供医生参考\n"
        "4. 携带正在服用的药物清单和既往病历"
    ),
}


def build_emergency_result(
    question: str,
    triage_result: TriageResult,
    session_id: str,
) -> Dict[str, Any]:
    """构建急症短路的最终结果（与正常 process() 返回结构兼容）"""
    guidance = _CATEGORY_GUIDANCE.get(triage_result.category) or _CATEGORY_GUIDANCE["general"]
    answer = f"🚨 **急症提醒**\n\n{guidance}"

    return {
        "answer": answer,
        "emergency": True,
        "triage": triage_result.to_dict(),
        "alert": "🚨 检测到疑似急症，系统已跳过常规分析流程，请优先执行急救指引。",
        "swarm_enabled": False,
        "session_id": session_id,
        "agent_id": "emergency_triage",
        "route_reason": f"急症分诊命中（{triage_result.method} 层）：{triage_result.reason}",
        "suggestions": [
            "立即拨打 120 急救电话",
            "在专业人员到达前，按上述指引进行现场处理",
            "本系统无法处理急症，请勿依赖在线咨询延误救治",
        ],
        "disclaimer": "本提醒由急症分诊规则自动生成，仅为应急参考，不能替代专业急救指导。请立即联系急救服务。",
        # 急症短路不走 RAG，明确给空列表，避免前端把缺字段当成待加载
        "sources": [],
    }
