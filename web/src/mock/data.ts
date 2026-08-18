import type {
  AnswerPayload,
  FixRecord,
  KnowledgeDoc,
  MemorySession,
  SimilarCase,
  TimelineStep,
} from "../types";

export const DEFAULT_QUESTION =
  "我父亲 65 岁，有高血压和糖尿病史，最近经常头晕、乏力，晚上睡不好，应该怎么办？";

export const FOLLOWUP_HINT = "继续追问，例如：晚上睡不好和血压有关系吗？";

export const SESSION_ID = "20260817-2136";

export const SWARM_ANSWER: Omit<AnswerPayload, "elapsed"> = {
  body:
    "头晕乏力在高血压合并糖尿病的老年患者中，常与血压波动、夜间睡眠差或血糖控制不稳有关。建议先在家规律监测晨起与睡前血压、空腹血糖，并记录头晕发作的时间与诱因。夜间睡眠差会升高交感活性、影响次日血压波动；若伴打鼾或憋醒，还需警惕睡眠呼吸暂停。",
  alert: "重要提醒：老年人头晕若伴随胸痛、言语不清或一侧肢体无力，请立即就医。",
  alertNote: "该提醒由安全护栏（AutoFixer）自动附加",
  suggestions: [
    "连续一周早晚测量血压并记录，就诊时供医生参考；",
    "排查夜间睡眠问题（如打鼾憋醒），必要时做睡眠监测；",
    "一周内前往心内科或老年科门诊做系统评估。",
  ],
  sources: [
    "依据 · 《中国高血压防治指南》",
    "知识库 · 语义检索 2 条",
    "相似历史案例 · Mem0 ×2",
  ],
  disclaimer: "以上分析基于多个专业 Agent 的协作，仅供参考，不能替代医生诊断。",
  agentCount: 3,
};

export const FOLLOWUP_ANSWER: Omit<AnswerPayload, "elapsed"> = {
  body:
    "有关系。睡眠不足或片段化睡眠会抬高次日晨起血压，并可能加重白天乏力。建议先记录一周睡眠时长、是否打鼾憋醒，以及对应的晨起血压；若差值明显，就诊时一并告知医生。",
  suggestions: [
    "固定作息，睡前避免浓茶、咖啡与过度补液；",
    "若存在明显打鼾或憋醒，优先排查睡眠呼吸暂停；",
    "继续规律测压，不要自行调整降压药。",
  ],
  sources: ["依据 · 《中国高血压防治指南》", "知识库 · 语义检索 1 条"],
  disclaimer: "以上分析仅供参考，不能替代医生诊断。",
  agentCount: 1,
};

export const SIMPLE_ANSWER: Omit<AnswerPayload, "elapsed"> = {
  body:
    "适量饮水有助于维持血容量、促进代谢与预防便秘。对大多数成人，可把「口渴再喝、全天分散饮用」作为原则，不必一次大量灌水。若有心肾功能不全，饮水量应遵医嘱。",
  suggestions: [
    "以温水为主，避免用含糖饮料代替白水；",
    "运动或天气炎热时可适当增加；",
    "夜尿多者减少睡前大量饮水。",
  ],
  sources: ["知识库 · 生活方式建议"],
  disclaimer: "以上分析仅供参考，不能替代医生诊断。",
  agentCount: 1,
};

export function swarmSteps(): TimelineStep[] {
  return [
    {
      id: "decompose",
      title: "任务分解",
      agentLabel: "LeadAgent",
      status: "pending",
      desc: "识别为复杂问题，发布 3 个子任务",
      skills: [],
    },
    {
      id: "consultation",
      title: "健康咨询",
      agentLabel: "ConsultationAgent",
      status: "pending",
      desc: "生活方式与随访建议已产出",
      skills: [
        { name: "search_knowledge", active: false },
        { name: "recommend_lifestyle", active: false },
      ],
    },
    {
      id: "diagnostic",
      title: "症状诊断",
      agentLabel: "DiagnosticAgent",
      status: "pending",
      desc: "风险评估完成：中危，建议一周内就诊",
      skills: [
        { name: "assess_risk", active: false },
        { name: "disease_code", active: false },
      ],
    },
    {
      id: "research",
      title: "医学研究",
      agentLabel: "ResearchAgent",
      status: "pending",
      desc: "正在检索临床指南……",
      skills: [{ name: "clinical_guideline", active: false }],
    },
    {
      id: "summarize",
      title: "结果汇总",
      agentLabel: "LeadAgent",
      status: "pending",
      desc: "等待全部子任务完成",
      skills: [],
    },
  ];
}

export function singleSteps(): TimelineStep[] {
  return [
    {
      id: "route",
      title: "智能路由",
      agentLabel: "LeadAgent",
      status: "pending",
      desc: "判定为简单问题，交由单一 Agent",
      skills: [],
    },
    {
      id: "consultation",
      title: "健康咨询",
      agentLabel: "ConsultationAgent",
      status: "pending",
      desc: "正在生成生活方式建议……",
      skills: [{ name: "recommend_lifestyle", active: false }],
    },
    {
      id: "summarize",
      title: "结果汇总",
      agentLabel: "LeadAgent",
      status: "pending",
      desc: "整理回答与免责声明",
      skills: [],
    },
  ];
}

export const DASHBOARD = {
  todaySessions: 12,
  weekSessions: 86,
  swarmShare: 41,
  avgLatency: "18.6s",
  swarmLatency: "32.4s",
  singleLatency: "9.8s",
  autoFix: 7,
  disclaimerFix: 5,
  emergencyFix: 2,
  bars: [
    { day: "周一", value: 8, height: 42 },
    { day: "周二", value: 12, height: 64 },
    { day: "周三", value: 9, height: 48 },
    { day: "周四", value: 15, height: 80, peak: true },
    { day: "周五", value: 11, height: 58 },
    { day: "周六", value: 6, height: 32 },
    { day: "周日", value: 12, height: 64 },
  ],
};

export const MEMORY_SESSIONS: MemorySession[] = [
  {
    id: "s1",
    time: "08-17 21:36",
    question: "父亲头晕乏力，高血压糖尿病史……",
    mode: "Swarm",
    elapsed: "24.8s",
    summary: "复杂咨询，分解为健康咨询 / 症状诊断 / 医学研究三个子任务。中危，建议一周内就诊并规律测压。",
    agent_count: 3,
    agents: ["consultation_agent", "diagnostic_agent", "research_agent"],
  },
  {
    id: "s2",
    time: "08-17 19:02",
    question: "多喝水对健康有什么好处？",
    mode: "单 Agent",
    elapsed: "9.7s",
    summary: "单 Agent 快速应答：适量饮水原则，心肾功能不全需遵医嘱。",
    agent_count: 1,
    agents: ["consultation_agent"],
  },
  {
    id: "s3",
    time: "08-16 10:24",
    question: "高血压患者的饮食建议",
    mode: "单 Agent",
    elapsed: "11.2s",
    summary: "低盐、补钾、控制饱和脂肪；引用生活方式知识库条目。",
    agent_count: 1,
    agents: ["consultation_agent"],
  },
];

export const SIMILAR_CASES: SimilarCase[] = [
  {
    score: 0.82,
    text: "高血压患者头晕随访建议：晨起测压、排查体位性低血压",
  },
  {
    score: 0.76,
    text: "老年糖尿病患者乏力咨询：关注夜间低血糖与睡眠质量",
  },
];

export const KNOWLEDGE_DOCS: KnowledgeDoc[] = [
  {
    id: "01",
    title: "高血压生活方式建议",
    type: "lifestyle",
    typeLabel: "lifestyle",
    snippet: "低盐饮食（每日钠摄入 < 2g），增加钾摄入，控制饱和脂肪，规律有氧运动与睡眠管理。",
    score: 0.91,
  },
  {
    id: "02",
    title: "糖尿病生活方式建议",
    type: "lifestyle",
    typeLabel: "lifestyle",
    snippet: "选择低 GI 主食，增加膳食纤维，规律监测空腹与餐后血糖，避免夜间低血糖。",
    score: 0.88,
  },
  {
    id: "03",
    title: "感冒生活方式建议",
    type: "lifestyle",
    typeLabel: "lifestyle",
    snippet: "休息、补液、对症处理；高热或呼吸困难需及时就医。",
    score: 0.54,
  },
  {
    id: "04",
    title: "一般健康生活方式",
    type: "lifestyle",
    typeLabel: "lifestyle",
    snippet: "均衡饮食、规律作息、适量运动与戒烟限酒的通用建议。",
    score: 0.61,
  },
  {
    id: "10",
    title: "循环系统疾病 ICD-10 编码",
    type: "icd10",
    typeLabel: "ICD-10",
    snippet: "高血压 I10、冠心病 I25 等第九章（I00–I99）循环系统疾病编码。",
    score: 0.79,
  },
  {
    id: "11",
    title: "内分泌疾病 ICD-10 编码",
    type: "icd10",
    typeLabel: "ICD-10",
    snippet: "2 型糖尿病 E11 等内分泌、营养和代谢疾病编码。",
    score: 0.73,
  },
  {
    id: "12",
    title: "传染病 ICD-10 编码",
    type: "icd10",
    typeLabel: "ICD-10",
    snippet: "第一章某些传染病和寄生虫病的常用编码摘录。",
    score: 0.41,
  },
  {
    id: "20",
    title: "中国高血压防治指南（2023 年修订版）",
    type: "guideline",
    typeLabel: "指南",
    snippet: "诊室血压 ≥140/90 mmHg；强调家庭血压监测与特殊人群（老年人、糖尿病）治疗目标。",
    score: 0.94,
  },
  {
    id: "21",
    title: "中国 2 型糖尿病防治指南",
    type: "guideline",
    typeLabel: "指南",
    snippet: "综合管理血糖、血压与血脂，个体化降糖方案与并发症筛查。",
    score: 0.86,
  },
  {
    id: "05",
    title: "急症症状识别要点",
    type: "guideline",
    typeLabel: "指南",
    snippet: "胸痛、言语不清、一侧肢体无力、呼吸困难等需立即就医的警示症状。",
    score: 0.7,
  },
];

export const FIX_RECORDS: FixRecord[] = [
  {
    time: "21:36",
    kind: "就医提醒",
    detail: "检出高危关键词「头晕」，自动附加急诊提示",
  },
  {
    time: "19:03",
    kind: "免责声明",
    detail: "输出缺少免责声明，已自动补全",
  },
  {
    time: "10:25",
    kind: "免责声明",
    detail: "输出缺少免责声明，已自动补全",
  },
];

export const SAFETY = {
  testsPassed: 24,
  testsTotal: 26,
  assertionRate: "100%",
};
