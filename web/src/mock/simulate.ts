import {
  EMERGENCY_ANSWER,
  FOLLOWUP_ANSWER,
  SIMPLE_ANSWER,
  SWARM_ANSWER,
  singleSteps,
  swarmSteps,
} from "./data";
import type {
  AnswerPayload,
  RoutingMode,
  StreamEvent,
  TimelineStep,
} from "../types";

export interface SimulateHandlers {
  onRouting: (mode: Exclude<RoutingMode, "idle">, subtaskCount?: number) => void;
  onSteps: (steps: TimelineStep[]) => void;
  onEvent: (event: StreamEvent) => void;
  onAnswerStart: (draft: AnswerPayload) => void;
  onAnswerDelta: (text: string) => void;
  onReveal: (key: "alert" | "suggestions" | "sources" | "disclaimer") => void;
  onAnswerDone: (payload: AnswerPayload) => void;
  onDone: () => void;
}

function nowTs(): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function isEmergency(question: string): boolean {
  const text = question || "";
  if (["压榨性疼痛", "压榨性胸痛", "昏迷", "大出血", "自杀", "活不下去", "喘不上气"].some((k) => text.includes(k))) {
    return true;
  }
  const chest = text.includes("胸痛") || text.includes("胸口疼") || text.includes("胸口痛");
  const sweat = text.includes("冷汗") || text.includes("冒冷汗") || text.includes("大汗");
  return chest && sweat;
}

function isComplex(question: string): boolean {
  const keys = ["头晕", "乏力", "高血压", "糖尿病", "父亲", "母亲", "胸痛", "睡不好"];
  return keys.some((k) => question.includes(k));
}

function isFollowup(question: string): boolean {
  return question.includes("睡不好") && question.includes("血压") && !question.includes("65");
}

function patchStep(
  steps: TimelineStep[],
  id: string,
  patch: Partial<TimelineStep>
): TimelineStep[] {
  return steps.map((s) => (s.id === id ? { ...s, ...patch } : s));
}

function activateSkills(steps: TimelineStep[], id: string, count: number): TimelineStep[] {
  return steps.map((s) => {
    if (s.id !== id) return s;
    return {
      ...s,
      skills: s.skills.map((sk, i) => ({ ...sk, active: i < count })),
    };
  });
}

/**
 * 用 setTimeout / setInterval 模拟 SSE：
 * 路由提示 → 时间线灰→绿→深绿 → 答案逐字打出。
 * 返回取消函数。
 */
export function simulateConsultation(question: string, h: SimulateHandlers): () => void {
  const timers: number[] = [];
  let cancelled = false;
  const started = Date.now();

  const later = (ms: number, fn: () => void) => {
    const id = window.setTimeout(() => {
      if (!cancelled) fn();
    }, ms);
    timers.push(id);
  };

  const formatElapsed = () => `${((Date.now() - started) / 1000).toFixed(1)}s`;

  const streamText = (full: string, onEnd: () => void) => {
    let i = 0;
    const id = window.setInterval(() => {
      if (cancelled) {
        window.clearInterval(id);
        return;
      }
      i += 1;
      h.onAnswerDelta(full.slice(0, i));
      if (i >= full.length) {
        window.clearInterval(id);
        onEnd();
      }
    }, 18);
    timers.push(id);
  };

  const emergency = isEmergency(question);
  const complex = !emergency && isComplex(question) && !isFollowup(question);
  let steps = emergency
    ? [
        {
          id: "triage",
          title: "急症分诊",
          agentLabel: "EmergencyTriage",
          status: "running" as const,
          desc: "正在进行急症分诊……",
          skills: [],
        },
      ]
    : complex
      ? swarmSteps()
      : singleSteps();

  h.onRouting("pending");
  h.onSteps(steps);
  if (!emergency) {
    h.onEvent({ ts: nowTs(), name: "swarm_started" });
  }

  later(700, () => {
    if (isEmergency(question)) {
      h.onRouting("emergency", 0);
      h.onEvent({ ts: nowTs(), name: "emergency_triggered · cardiac" });
      runEmergency();
    } else if (complex) {
      h.onRouting("swarm", 3);
      h.onEvent({ ts: nowTs(), name: "task_decomposed ×3" });
      runSwarm();
    } else {
      h.onRouting("single", 0);
      h.onEvent({ ts: nowTs(), name: "task_decomposed ×1" });
      runSingle();
    }
  });

  function runEmergency() {
    later(0, () => {
      h.onSteps([
        {
          id: "triage",
          title: "急症分诊",
          agentLabel: "EmergencyTriage",
          status: "done",
          desc: "命中急症规则，已短路常规 Swarm 流程",
          skills: [],
        },
      ]);
    });
    later(240, () => {
      startAnswer(EMERGENCY_ANSWER, 1);
    });
  }

  function runSwarm() {
    later(0, () => {
      steps = patchStep(steps, "decompose", { status: "running" });
      h.onSteps(steps);
    });
    later(500, () => {
      steps = patchStep(steps, "decompose", { status: "done", duration: "0.6s" });
      h.onSteps(steps);
      h.onEvent({ ts: nowTs(), name: "subtask_started ×3" });
    });

    later(600, () => {
      steps = patchStep(steps, "consultation", { status: "running" });
      h.onSteps(steps);
    });
    later(900, () => {
      steps = activateSkills(steps, "consultation", 1);
      h.onSteps(steps);
    });
    later(1300, () => {
      steps = activateSkills(steps, "consultation", 2);
      h.onSteps(steps);
    });
    later(1800, () => {
      steps = patchStep(steps, "consultation", { status: "done", duration: "12.4s" });
      h.onSteps(steps);
      h.onEvent({ ts: nowTs(), name: "subtask_completed · consultation" });
    });

    later(1900, () => {
      steps = patchStep(steps, "diagnostic", { status: "running" });
      h.onSteps(steps);
    });
    later(2300, () => {
      steps = activateSkills(steps, "diagnostic", 1);
      h.onSteps(steps);
    });
    later(2700, () => {
      steps = activateSkills(steps, "diagnostic", 2);
      h.onSteps(steps);
    });
    later(3200, () => {
      steps = patchStep(steps, "diagnostic", { status: "done", duration: "15.2s" });
      h.onSteps(steps);
      h.onEvent({ ts: nowTs(), name: "subtask_completed · diagnostic" });
    });

    later(3300, () => {
      steps = patchStep(steps, "research", {
        status: "running",
        desc: "正在检索临床指南……",
      });
      h.onSteps(steps);
    });
    later(3700, () => {
      steps = activateSkills(steps, "research", 1);
      h.onSteps(steps);
    });
    later(4300, () => {
      steps = patchStep(steps, "research", {
        status: "done",
        duration: "18.1s",
        desc: "已引用《中国高血压防治指南》要点",
      });
      h.onSteps(steps);
      h.onEvent({ ts: nowTs(), name: "subtask_completed · research" });
    });

    later(4400, () => {
      steps = patchStep(steps, "summarize", {
        status: "running",
        desc: "正在汇总三方结果……",
      });
      h.onSteps(steps);
      startAnswer(SWARM_ANSWER, 3);
    });
    later(4800, () => {
      steps = patchStep(steps, "summarize", {
        status: "done",
        duration: "1.2s",
        desc: "多 Agent 结果已汇总",
      });
      h.onSteps(steps);
    });
  }

  function runSingle() {
    later(0, () => {
      steps = patchStep(steps, "route", { status: "running" });
      h.onSteps(steps);
    });
    later(400, () => {
      steps = patchStep(steps, "route", { status: "done", duration: "0.4s" });
      h.onSteps(steps);
      h.onEvent({ ts: nowTs(), name: "subtask_started ×1" });
    });
    later(500, () => {
      steps = patchStep(steps, "consultation", { status: "running" });
      h.onSteps(steps);
    });
    later(900, () => {
      steps = activateSkills(steps, "consultation", 1);
      h.onSteps(steps);
    });
    later(1600, () => {
      steps = patchStep(steps, "consultation", {
        status: "done",
        duration: "8.1s",
        desc: "生活方式建议已产出",
      });
      h.onSteps(steps);
      h.onEvent({ ts: nowTs(), name: "subtask_completed · consultation" });
    });
    later(1700, () => {
      steps = patchStep(steps, "summarize", { status: "running", desc: "整理回答……" });
      h.onSteps(steps);
      const payload = isFollowup(question) ? FOLLOWUP_ANSWER : SIMPLE_ANSWER;
      startAnswer(payload, 1);
    });
    later(2100, () => {
      steps = patchStep(steps, "summarize", {
        status: "done",
        duration: "0.8s",
        desc: "回答已生成",
      });
      h.onSteps(steps);
    });
  }

  function startAnswer(base: Omit<AnswerPayload, "elapsed">, agentCount: number) {
    h.onAnswerStart({ ...base, body: "", elapsed: "…", agentCount });
    streamText(base.body, () => {
      later(160, () => h.onReveal("alert"));
      later(320, () => h.onReveal("suggestions"));
      later(480, () => h.onReveal("sources"));
      later(640, () => {
        h.onReveal("disclaimer");
        const payload: AnswerPayload = { ...base, elapsed: formatElapsed(), agentCount };
        h.onAnswerDone(payload);
        h.onEvent({ ts: nowTs(), name: "swarm_completed" });
        h.onDone();
      });
    });
  }

  return () => {
    cancelled = true;
    timers.forEach((id) => {
      window.clearTimeout(id);
      window.clearInterval(id);
    });
  };
}
