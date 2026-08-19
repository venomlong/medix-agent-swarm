import type {
  AnswerPayload,
  FixRecord,
  KnowledgeDoc,
  MemorySession,
  SimilarCase,
  SourceRef,
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
    {
      id: "20",
      title: "中国高血压防治指南（2023 年修订版）",
      source: "中国高血压防治指南_2023.txt",
      type: "guideline",
      score: 0.94,
      snippet: "诊室血压 ≥140/90 mmHg 可诊断高血压。指南强调家庭血压监测与动态血压在诊断、疗效评估中的作用。",
    },
    {
      id: "01",
      title: "高血压生活方式建议",
      source: "高血压_生活方式.txt",
      type: "lifestyle",
      score: 0.91,
      snippet: "高血压患者的生活方式干预是药物治疗的基础。建议低盐饮食，每日钠摄入控制在 2g 以内。",
    },
  ] satisfies SourceRef[],
  disclaimer: "以上分析基于多个专业 Agent 的协作，仅供参考，不能替代医生诊断。",
  agentCount: 3,
  usage: { totalTokens: 1840, cost: 0.012, llmCalls: 4 },
  traceId: "demo12ab34cd",
};

export const FOLLOWUP_ANSWER: Omit<AnswerPayload, "elapsed"> = {
  body:
    "有关系。睡眠不足或片段化睡眠会抬高次日晨起血压，并可能加重白天乏力。建议先记录一周睡眠时长、是否打鼾憋醒，以及对应的晨起血压；若差值明显，就诊时一并告知医生。",
  suggestions: [
    "固定作息，睡前避免浓茶、咖啡与过度补液；",
    "若存在明显打鼾或憋醒，优先排查睡眠呼吸暂停；",
    "继续规律测压，不要自行调整降压药。",
  ],
  sources: [
    {
      id: "20",
      title: "中国高血压防治指南（2023 年修订版）",
      source: "中国高血压防治指南_2023.txt",
      type: "guideline",
      score: 0.94,
      snippet: "诊室血压 ≥140/90 mmHg 可诊断高血压。指南强调家庭血压监测与动态血压在诊断、疗效评估中的作用。",
    },
  ] satisfies SourceRef[],
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
  sources: [
    {
      id: "04",
      title: "一般健康生活方式",
      source: "一般健康_生活方式.txt",
      type: "lifestyle",
      score: 0.61,
      snippet: "均衡饮食、规律作息、适量运动与戒烟限酒是通用健康建议。每日蔬菜水果充足，限制加工食品与过量盐糖。",
    },
  ] satisfies SourceRef[],
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
  totalTokens: 18420,
  totalCost: 0.0864,
  llmCalls: 41,
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

function kbDoc(
  partial: Omit<KnowledgeDoc, "snippet" | "source"> & { content: string; source?: string }
): KnowledgeDoc {
  const preview = partial.content.replace(/\n/g, " ").trim();
  return {
    ...partial,
    source: partial.source || partial.filename || partial.title,
    snippet: preview.slice(0, 180) + (preview.length > 180 ? "…" : ""),
  };
}

export const KNOWLEDGE_DOCS: KnowledgeDoc[] = [
  kbDoc({
    id: "01",
    title: "高血压生活方式建议",
    type: "lifestyle",
    typeLabel: "lifestyle",
    source: "高血压_生活方式.txt",
    filename: "高血压_生活方式.txt",
    score: 0.91,
    content:
      "高血压患者的生活方式干预是药物治疗的基础。建议低盐饮食，每日钠摄入控制在 2g 以内，增加富钾食物如蔬菜、水果与低脂奶制品；减少饱和脂肪与反式脂肪，控制总热量以维持适宜体重。\n\n每周进行至少 150 分钟中等强度有氧运动（快走、骑行或游泳），避免突然剧烈运动。保证规律睡眠，限制饮酒，彻底戒烟。家庭血压监测有助于评估干预效果；若血压持续升高或出现胸痛、视物模糊、剧烈头痛，应及时就医。以上信息仅供参考，不能替代专业医生的诊断和治疗。",
  }),
  kbDoc({
    id: "02",
    title: "糖尿病生活方式建议",
    type: "lifestyle",
    typeLabel: "lifestyle",
    source: "糖尿病_生活方式.txt",
    filename: "糖尿病_生活方式.txt",
    score: 0.88,
    content:
      "2 型糖尿病的综合管理强调饮食、运动与血糖监测并重。主食优先选择低 GI 食物，增加全谷物与膳食纤维，控制添加糖与含糖饮料。正餐定时定量，避免一次进食过多导致餐后高血糖。\n\n建议每周 150 分钟以上中等强度有氧运动，并配合抗阻训练；运动前后注意监测血糖，预防低血糖。睡眠不足、压力过大会影响血糖波动。出现多饮多尿加重、意识改变或反复低血糖时需及时就医。以上信息仅供参考，不能替代专业医生的诊断和治疗。",
  }),
  kbDoc({
    id: "03",
    title: "感冒生活方式建议",
    type: "lifestyle",
    typeLabel: "lifestyle",
    source: "感冒_生活方式.txt",
    filename: "感冒_生活方式.txt",
    score: 0.54,
    content:
      "普通感冒多为自限性病毒感染。以休息、补液与对症处理为主：保证睡眠，适量饮水，可用温盐水漱口缓解咽痛。发热时可物理降温，必要时按说明书使用解热镇痛药。\n\n应避免不必要的抗生素。高热持续、呼吸困难、胸痛、意识改变或基础病患者症状加重时，需及时就医。以上信息仅供参考，不能替代专业医生的诊断和治疗。",
  }),
  kbDoc({
    id: "04",
    title: "一般健康生活方式",
    type: "lifestyle",
    typeLabel: "lifestyle",
    source: "一般健康_生活方式.txt",
    filename: "一般健康_生活方式.txt",
    score: 0.61,
    content:
      "均衡饮食、规律作息、适量运动与戒烟限酒是通用健康建议。每日蔬菜水果充足，限制加工食品与过量盐糖；每周累计 150 分钟中等强度运动，并减少久坐。\n\n保持规律睡眠与压力管理，定期体检有助于早期发现高血压、糖尿病等慢性病。出现胸痛、呼吸困难、昏厥等警示症状应立即就医。以上信息仅供参考，不能替代专业医生的诊断和治疗。",
  }),
  kbDoc({
    id: "10",
    title: "循环系统疾病 ICD-10 编码",
    type: "icd10",
    typeLabel: "ICD-10",
    source: "循环系统疾病_ICD10.txt",
    filename: "循环系统疾病_ICD10.txt",
    score: 0.79,
    content:
      "ICD-10 第九章（I00–I99）覆盖循环系统疾病。原发性高血压编码为 I10；高血压性心脏病为 I11；心绞痛为 I20；慢性缺血性心脏病（含冠心病）常见 I25。\n\n心力衰竭可见 I50，脑梗死为 I63，脑出血为 I61。编码仅用于疾病分类与统计，不能替代临床诊断。具体编码应以正式分类与病历为准。",
  }),
  kbDoc({
    id: "11",
    title: "内分泌疾病 ICD-10 编码",
    type: "icd10",
    typeLabel: "ICD-10",
    source: "内分泌疾病_ICD10.txt",
    filename: "内分泌疾病_ICD10.txt",
    score: 0.73,
    content:
      "内分泌、营养和代谢疾病位于 ICD-10 第四章（E00–E90）。1 型糖尿病为 E10，2 型糖尿病为 E11，营养不良相关糖尿病为 E12，其他特指糖尿病为 E13，未特指糖尿病为 E14。\n\n甲状腺功能亢进常见 E05，甲状腺功能减退常见 E03。编码需结合并发症附加码。以上摘录仅供检索示意，正式编码请以现行分类为准。",
  }),
  kbDoc({
    id: "12",
    title: "传染病 ICD-10 编码",
    type: "icd10",
    typeLabel: "ICD-10",
    source: "传染病_ICD10.txt",
    filename: "传染病_ICD10.txt",
    score: 0.41,
    content:
      "第一章某些传染病和寄生虫病（A00–B99）包含肠道感染、结核、病毒性肝炎等。甲型肝炎为 B15，乙型肝炎为 B16，流感常见 J09–J11（呼吸系统章节）。\n\n编码用于疫情统计与病历分类，不能替代病原学诊断。出现高热、呼吸困难或出血倾向等需及时就医。",
  }),
  kbDoc({
    id: "20",
    title: "中国高血压防治指南（2023 年修订版）",
    type: "guideline",
    typeLabel: "指南",
    source: "中国高血压防治指南_2023.txt",
    filename: "中国高血压防治指南_2023.txt",
    score: 0.94,
    content:
      "诊室血压 ≥140/90 mmHg 可诊断高血压。指南强调家庭血压监测与动态血压在诊断、疗效评估中的作用。一般人群降压目标多为 <140/90 mmHg，能耐受者可进一步降至 <130/80 mmHg。\n\n合并糖尿病、慢性肾脏病或心血管疾病时，治疗策略需个体化。生活方式干预应贯穿全程：限盐、减重、运动、限酒、戒烟。药物治疗需在医师指导下选择长效降压药并评估靶器官损害。出现高血压急症表现（剧烈头痛、视物模糊、胸痛、呼吸困难）应立即就医。以上摘录仅供学习参考，不能替代正式指南与临床决策。",
  }),
  kbDoc({
    id: "21",
    title: "中国 2 型糖尿病防治指南",
    type: "guideline",
    typeLabel: "指南",
    source: "中国2型糖尿病防治指南.txt",
    filename: "中国2型糖尿病防治指南.txt",
    score: 0.86,
    content:
      "2 型糖尿病管理强调血糖、血压与血脂综合达标，并筛查视网膜病变、肾病与神经病变等并发症。生活方式干预是基础，降糖方案需个体化，关注低血糖风险。\n\n老年或合并多种疾病者目标可适当放宽。胰岛素或新诊断高血糖的起始治疗应在医师指导下进行。出现酮症、高渗状态或严重感染时应急诊处理。以上摘录仅供参考，不能替代专业诊疗。",
  }),
  kbDoc({
    id: "05",
    title: "急症症状识别要点",
    type: "guideline",
    typeLabel: "指南",
    source: "急症症状识别.txt",
    filename: "急症症状识别.txt",
    score: 0.7,
    content:
      "胸痛、言语不清、一侧肢体无力、突发剧烈头痛、呼吸困难、昏厥或意识障碍，均为需立即就医的警示症状。怀疑急性冠脉综合征或脑卒中时，应尽快拨打急救电话，避免自行服药延误。\n\n高热伴皮疹、颈强直，或哮喘发作不能平卧，同样需要紧急评估。本条目用于知识库检索演示，不能替代现场急救判断。",
  }),
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
