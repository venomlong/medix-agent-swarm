import type { RoutingMode, SkillTag, TimelineStep } from "../types";

type AgentMeta = {
  id: string;
  title: string;
  agentLabel: string;
  skills: string[];
  defaultDesc: string;
};

const AGENT_META: Record<string, AgentMeta> = {
  consultation_agent: {
    id: "consultation",
    title: "健康咨询",
    agentLabel: "ConsultationAgent",
    skills: ["search_knowledge", "recommend_lifestyle"],
    defaultDesc: "生活方式与随访建议",
  },
  diagnostic_agent: {
    id: "diagnostic",
    title: "症状诊断",
    agentLabel: "DiagnosticAgent",
    skills: ["assess_risk", "disease_code"],
    defaultDesc: "风险评估与鉴别",
  },
  research_agent: {
    id: "research",
    title: "医学研究",
    agentLabel: "ResearchAgent",
    skills: ["clinical_guideline"],
    defaultDesc: "检索临床指南与证据",
  },
};

function idleSkills(names: string[]): SkillTag[] {
  return names.map((name) => ({ name, active: false }));
}

function metaFor(assigned: string): AgentMeta {
  return (
    AGENT_META[assigned] ?? {
      id: assigned,
      title: assigned,
      agentLabel: assigned,
      skills: [],
      defaultDesc: "",
    }
  );
}

function patchById(steps: TimelineStep[], id: string, patch: Partial<TimelineStep>): TimelineStep[] {
  return steps.map((s) => (s.id === id ? { ...s, ...patch } : s));
}

function upsertAgentStep(
  steps: TimelineStep[],
  assigned: string,
  patch: Partial<TimelineStep>
): TimelineStep[] {
  const meta = metaFor(assigned);
  const existing = steps.find((s) => s.id === meta.id);
  if (!existing) {
    const next: TimelineStep = {
      id: meta.id,
      title: meta.title,
      agentLabel: meta.agentLabel,
      status: "pending",
      desc: meta.defaultDesc,
      skills: idleSkills(meta.skills),
      ...patch,
    };
    const summarizeIdx = steps.findIndex((s) => s.id === "summarize");
    if (summarizeIdx >= 0) {
      return [...steps.slice(0, summarizeIdx), next, ...steps.slice(summarizeIdx)];
    }
    return [...steps, next];
  }
  return steps.map((s) => (s.id === meta.id ? { ...s, ...patch } : s));
}

export function initialSteps(
  mode: Exclude<RoutingMode, "idle" | "pending">,
  subtaskCount?: number
): TimelineStep[] {
  if (mode === "emergency") {
    return [
      {
        id: "triage",
        title: "急症分诊",
        agentLabel: "EmergencyTriage",
        status: "done",
        desc: "命中急症规则，已短路常规 Swarm 流程",
        skills: [],
      },
    ];
  }

  if (mode === "blocked") {
    return [
      {
        id: "harm-filter",
        title: "内容拦截",
        agentLabel: "HarmFilter",
        status: "done",
        desc: "命中敏感/有害内容规则，已短路常规 Swarm 流程",
        skills: [],
      },
    ];
  }

  if (mode === "single") {
    return [
      {
        id: "route",
        title: "智能路由",
        agentLabel: "LeadAgent",
        status: "running",
        desc: "判定为简单问题，交由单一 Agent",
        skills: [],
      },
      {
        id: "consultation",
        title: "健康咨询",
        agentLabel: "ConsultationAgent",
        status: "pending",
        desc: "正在生成回答……",
        skills: idleSkills(["recommend_lifestyle"]),
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

  return [
    {
      id: "decompose",
      title: "任务分解",
      agentLabel: "LeadAgent",
      status: "running",
      desc: subtaskCount
        ? `识别为复杂问题，发布 ${subtaskCount} 个子任务`
        : "识别为复杂问题，正在分解任务",
      skills: [],
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

function formatDuration(seconds?: number): string | undefined {
  if (seconds == null || Number.isNaN(Number(seconds))) return undefined;
  return `${Number(seconds).toFixed(1)}s`;
}

function activateSkillTag(steps: TimelineStep[], assigned: string, skillName: string): TimelineStep[] {
  const name = skillName.trim();
  if (!name) return steps;
  let next = steps.length === 0 ? initialSteps("single") : steps;
  const agentKey = assigned || "consultation_agent";
  const meta = metaFor(agentKey);
  if (!next.find((s) => s.id === meta.id)) {
    next = upsertAgentStep(next, agentKey, { status: "running" });
  }
  return next.map((s) => {
    if (s.id !== meta.id) return s;
    const skills = [...s.skills];
    const idx = skills.findIndex((sk) => sk.name === name);
    if (idx >= 0) {
      skills[idx] = { ...skills[idx], active: true };
    } else {
      skills.push({ name, active: true });
    }
    return { ...s, skills };
  });
}

export function applyTimelineEvent(
  steps: TimelineStep[],
  eventName: string,
  data: Record<string, unknown>
): TimelineStep[] {
  const assigned = String(data.assigned_agent ?? data.agent ?? data.source_agent ?? "");

  if (eventName === "emergency_triggered") {
    if (steps.length === 0 || steps.every((s) => s.id !== "triage")) {
      return initialSteps("emergency");
    }
    return patchById(steps, "triage", {
      status: "done",
      desc: String(data.reason || "命中急症规则，已短路常规 Swarm 流程"),
    });
  }

  if (eventName === "harmful_blocked") {
    if (steps.length === 0 || steps.every((s) => s.id !== "harm-filter")) {
      return initialSteps("blocked");
    }
    return patchById(steps, "harm-filter", {
      status: "done",
      desc: String(data.reason || "命中敏感/有害内容规则，已短路常规 Swarm 流程"),
    });
  }

  if (eventName === "swarm_started") {
    const count = Number(data.num_subtasks ?? 0);
    if (steps.length === 0) {
      return initialSteps(count <= 1 ? "single" : "swarm", count);
    }
    if (steps.some((s) => s.id === "decompose")) {
      return patchById(steps, "decompose", {
        status: "running",
        desc: count ? `识别为复杂问题，发布 ${count} 个子任务` : steps.find((s) => s.id === "decompose")?.desc,
      });
    }
    return steps;
  }

  if (eventName === "task_decomposed") {
    let next = steps;
    if (next.length === 0) {
      next = initialSteps("swarm");
    }
    if (next.some((s) => s.id === "decompose")) {
      next = patchById(next, "decompose", { status: "done", duration: next.find((s) => s.id === "decompose")?.duration ?? "—" });
    }
    if (next.some((s) => s.id === "route")) {
      next = patchById(next, "route", { status: "done", duration: next.find((s) => s.id === "route")?.duration ?? "—" });
    }
    if (assigned) {
      next = upsertAgentStep(next, assigned, {
        status: "pending",
        desc: String(data.description ?? metaFor(assigned).defaultDesc),
      });
    }
    return next;
  }

  if (eventName === "subtask_started" && assigned) {
    let next = steps;
    if (next.some((s) => s.id === "route" && s.status !== "done")) {
      next = patchById(next, "route", { status: "done" });
    }
    return upsertAgentStep(next, assigned, {
      status: "running",
      desc: `${metaFor(assigned).defaultDesc}（进行中）`,
    });
  }

  if (eventName === "subtask_completed" && assigned) {
    const duration = formatDuration(data.duration_s as number | undefined);
    const summary = data.result_summary ? String(data.result_summary).slice(0, 48) : `${metaFor(assigned).title}已完成`;
    return upsertAgentStep(steps, assigned, {
      status: "done",
      duration,
      desc: summary,
    });
  }

  if (eventName === "timeout_occurred") {
    return steps.map((s) =>
      s.status === "running" || s.status === "pending"
        ? { ...s, status: "timeout" as const, desc: s.id === "summarize" ? "部分模块超时，正在汇总已完成结果" : s.desc }
        : s
    );
  }

  if (eventName === "skill_started" || eventName === "skill_completed" || eventName === "skill_called") {
    const skillName = String(data.name ?? data.skill_name ?? "").trim();
    const agentId = assigned || String(data.agent ?? data.agent_id ?? "");
    return activateSkillTag(steps, agentId, skillName);
  }

  if (eventName === "answer_delta") {
    if (steps.length === 0) return steps;
    if (steps.some((s) => s.id === "summarize")) {
      return patchById(steps, "summarize", { status: "running", desc: "正在生成回答……" });
    }
    return steps;
  }

  if (eventName === "swarm_completed" || eventName === "answer_done") {
    const duration = formatDuration(data.duration as number | undefined) ?? (typeof data.elapsed === "string" ? data.elapsed : undefined);
    let next = steps;
    if (next.some((s) => s.id === "triage") || Boolean(data.emergency)) {
      if (next.length === 0) next = initialSteps("emergency");
      return patchById(next, "triage", {
        status: "done",
        duration,
        desc: "命中急症规则，已返回急救指引",
      });
    }
    if (next.some((s) => s.id === "harm-filter") || Boolean(data.blocked)) {
      if (next.length === 0) next = initialSteps("blocked");
      return patchById(next, "harm-filter", {
        status: "done",
        duration,
        desc: "命中敏感/有害内容规则，已拒绝回答",
      });
    }
    if (next.length === 0) {
      const swarm = Boolean(data.swarm_enabled) || Number(data.agent_count ?? data.agents_count ?? 0) > 1;
      next = initialSteps(swarm ? "swarm" : "single", Number(data.agent_count ?? data.num_subtasks) || 1);
    }
    next = next.map((s) =>
      s.status === "running" && s.id !== "summarize" ? { ...s, status: "done" as const } : s
    );
    if (next.some((s) => s.id === "route" && s.status !== "done")) {
      next = patchById(next, "route", { status: "done" });
    }
    if (next.some((s) => s.id === "decompose" && s.status !== "done")) {
      next = patchById(next, "decompose", { status: "done" });
    }
    return patchById(next, "summarize", {
      status: "done",
      duration,
      desc: data.timeout_occurred || data.timed_out ? "已汇总部分结果" : "回答已生成",
    });
  }

  return steps;
}

export function shortAgentName(assigned: string): string {
  return metaFor(assigned).id;
}
