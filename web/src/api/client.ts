/**
 * POST /api/chat — FastAPI SSE
 *
 * VITE_API_BASE 默认空字符串，请求走相对路径 /api（开发时由 Vite 反代到 :8000）。
 */

import type {
  AnswerPayload,
  ChatMessage,
  FixRecord,
  KnowledgeDoc,
  DeleteSessionResult,
  MemorySession,
  MemorySessionDetail,
  RoutingMode,
  RuntimeStatsPayload,
  ShortTermChatMessage,
  SimilarCase,
  SourceRef,
  StreamEvent,
  TimelineStep,
} from "../types";
import { applyTimelineEvent, initialSteps, shortAgentName } from "./timeline";

export const API_BASE = import.meta.env.VITE_API_BASE ?? "";
export const USE_MOCK = import.meta.env.VITE_USE_MOCK === "true";

export interface ChatHandlers {
  onRouting: (mode: Exclude<RoutingMode, "idle">, subtaskCount?: number) => void;
  onSteps: (steps: TimelineStep[]) => void;
  onEvent: (event: StreamEvent) => void;
  onAnswerStart: (draft: AnswerPayload) => void;
  onAnswerDelta: (text: string) => void;
  onReveal: (key: "alert" | "suggestions" | "sources" | "disclaimer") => void;
  onAnswerDone: (payload: AnswerPayload) => void;
  onDone: () => void;
  onSession?: (sessionId: string) => void;
}

type SseFrame = { event: string; data: Record<string, unknown> };

const OFFLINE_HINT =
  "无法连接后端服务。请先在项目根启动：python -m uvicorn webapi.app:app --host 127.0.0.1 --port 8000，然后再刷新本页发送。";

function nowTs(): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function isoToTs(iso?: unknown): string {
  if (typeof iso !== "string" || !iso) return nowTs();
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return nowTs();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

export function createSessionId(): string {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, "0");
  const stamp = `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
  const rand = crypto.randomUUID().replace(/-/g, "").slice(0, 8);
  return `${stamp}-${rand}`;
}

const SESSION_STORAGE_KEY = "medix.session_id";

export function loadStoredSessionId(): string {
  try {
    return sessionStorage.getItem(SESSION_STORAGE_KEY) ?? "";
  } catch {
    return "";
  }
}

export function storeSessionId(id: string): void {
  try {
    if (id) sessionStorage.setItem(SESSION_STORAGE_KEY, id);
    else sessionStorage.removeItem(SESSION_STORAGE_KEY);
  } catch {
    /* ignore quota / private mode */
  }
}

export function clearStoredSessionId(): void {
  try {
    sessionStorage.removeItem(SESSION_STORAGE_KEY);
  } catch {
    /* ignore */
  }
}

function chatUrl(): string {
  const base = API_BASE.replace(/\/$/, "");
  return `${base}/api/chat`;
}

function eventLabel(name: string, data: Record<string, unknown>): string {
  const assigned = String(data.assigned_agent ?? data.agent ?? data.source_agent ?? "");
  if ((name === "subtask_completed" || name === "task_decomposed" || name === "subtask_started") && assigned) {
    return `${name} · ${shortAgentName(assigned)}`;
  }
  if (name === "skill_started" || name === "skill_completed" || name === "skill_called") {
    const skill = String(data.name ?? data.skill_name ?? "");
    const bits = [name];
    if (assigned) bits.push(shortAgentName(assigned));
    if (skill) bits.push(skill);
    return bits.join(" · ");
  }
  if (name === "emergency_triggered") {
    const cat = String(data.category ?? "");
    return cat ? `emergency_triggered · ${cat}` : "emergency_triggered";
  }
  return name;
}

function parseRoutingMode(mode: unknown): Exclude<RoutingMode, "idle" | "pending"> {
  if (mode === "single" || mode === "emergency") return mode;
  return "swarm";
}

function extractAlert(body: string): Pick<AnswerPayload, "alert" | "alertNote"> {
  if (/重要提醒|立即就医|拨打急救|急诊/.test(body)) {
    const m = body.match(/重要提醒[：:]\s*([^\n]+)/);
    return {
      alert: m ? `重要提醒：${m[1].trim()}` : "重要提醒：如症状加重或出现胸痛、言语不清等，请立即就医。",
      alertNote: "该提醒由答案正文启发式识别（结构化拆分见 M1.5）",
    };
  }
  return {};
}

function toSourceRef(raw: unknown): SourceRef | null {
  if (!raw || typeof raw !== "object") {
    return null;
  }
  const s = raw as Record<string, unknown>;
  const id = String(s.id ?? "").trim();
  const title = String(s.title ?? s.source ?? "").trim();
  if (!id && !title) {
    return null;
  }
  return {
    id: id || title,
    title: title || "医学知识库条目",
    source: String(s.source ?? "医学知识库"),
    type: s.type != null && String(s.type) ? String(s.type) : undefined,
    score: Number(s.score ?? 0) || 0,
    snippet: String(s.snippet ?? ""),
  };
}

function toAnswerPayload(data: Record<string, unknown>): AnswerPayload {
  const body = String(data.body ?? data.answer ?? "");
  const heuristic = extractAlert(body);
  const suggestions = Array.isArray(data.suggestions)
    ? data.suggestions.map((s) => String(s))
    : [];
  const sources = Array.isArray(data.sources)
    ? data.sources.map(toSourceRef).filter((s): s is SourceRef => s != null)
    : [];
  return {
    body,
    suggestions,
    sources,
    disclaimer: String(data.disclaimer ?? "以上信息仅供参考，不能替代专业医生的诊断和治疗。"),
    elapsed: String(data.elapsed ?? "—"),
    agentCount: Number(data.agent_count ?? data.agentCount ?? 1) || 1,
    timedOut: Boolean(data.timed_out ?? data.timedOut),
    alert: (data.alert as string | undefined) ?? heuristic.alert,
    alertNote: (data.alert_note as string | undefined) ?? heuristic.alertNote,
    emergency: Boolean(data.emergency),
  };
}

function errorAnswer(message: string): AnswerPayload {
  return {
    body: message,
    suggestions: [],
    sources: [],
    disclaimer: "此为连接或服务错误提示，并非模型医疗建议。",
    elapsed: "—",
    agentCount: 0,
  };
}

function parseSseBlock(block: string): SseFrame | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const rawLine of block.split(/\r?\n/)) {
    const line = rawLine.trimEnd();
    if (!line || line.startsWith(":")) continue;
    if (line.startsWith("event:")) {
      event = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trim());
    }
  }
  if (dataLines.length === 0) return null;
  const raw = dataLines.join("\n");
  let data: Record<string, unknown> = {};
  try {
    data = JSON.parse(raw) as Record<string, unknown>;
  } catch {
    data = { message: raw };
  }
  return { event, data };
}

function pushEvent(h: ChatHandlers, name: string, data: Record<string, unknown>) {
  h.onEvent({ ts: isoToTs(data.ts), name: eventLabel(name, data) });
}

function typewrite(full: string, onDelta: (text: string) => void, signal: AbortSignal): Promise<void> {
  if (!full) {
    onDelta("");
    return Promise.resolve();
  }
  const chunk = Math.max(8, Math.ceil(full.length / 80));
  return new Promise((resolve) => {
    let i = 0;
    const id = window.setInterval(() => {
      if (signal.aborted) {
        window.clearInterval(id);
        resolve();
        return;
      }
      i = Math.min(full.length, i + chunk);
      onDelta(full.slice(0, i));
      if (i >= full.length) {
        window.clearInterval(id);
        resolve();
      }
    }, 16);
  });
}

async function finishAnswer(h: ChatHandlers, payload: AnswerPayload, signal: AbortSignal) {
  h.onAnswerStart({ ...payload, body: "" });
  await typewrite(payload.body, h.onAnswerDelta, signal);
  if (signal.aborted) return;
  if (payload.alert) h.onReveal("alert");
  h.onReveal("suggestions");
  h.onReveal("sources");
  h.onReveal("disclaimer");
  h.onAnswerDone(payload);
}

/**
 * 向 /api/chat 发消息并解析 SSE。返回取消函数（中止 fetch / 打字）。
 */
export function sendChat(message: string, sessionId: string, h: ChatHandlers): () => void {
  const ac = new AbortController();

  void (async () => {
    let steps: TimelineStep[] = [];
    let answered = false;
    let streamed = false;
    let streamedText = "";

    const fail = async (text: string) => {
      pushEvent(h, "error", { message: text });
      const payload = errorAnswer(text);
      if (streamed) {
        h.onAnswerDone(payload);
      } else {
        await finishAnswer(h, payload, ac.signal);
      }
    };

    try {
      h.onRouting("pending");
      const res = await fetch(chatUrl(), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "text/event-stream",
        },
        body: JSON.stringify({ message, session_id: sessionId }),
        signal: ac.signal,
      });

      if (!res.ok) {
        let detail = `服务器返回 HTTP ${res.status}`;
        try {
          const body = (await res.json()) as { detail?: unknown };
          if (typeof body.detail === "string") detail = body.detail;
          else if (Array.isArray(body.detail)) detail = "请求无效：请输入非空问题。";
        } catch {
          /* ignore */
        }
        if (res.status === 404) {
          detail = "未找到 /api/chat。请确认后端已启动且 Vite 已把 /api 反代到 127.0.0.1:8000。";
        }
        await fail(detail);
        return;
      }

      if (!res.body) {
        await fail("浏览器未获得响应流，请检查网络或代理设置。");
        return;
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      const handleFrame = async (frame: SseFrame): Promise<"stop" | "cont"> => {
        const { event, data } = frame;

        if (event === "session" && typeof data.session_id === "string") {
          h.onSession?.(data.session_id);
          pushEvent(h, "session", data);
          return "cont";
        }

        if (event === "routing") {
          const mode = parseRoutingMode(data.mode);
          const count = Number(data.subtask_count ?? 0);
          h.onRouting(mode, count);
          if (steps.length === 0) {
            steps = initialSteps(mode, count);
          }
          h.onSteps(steps);
          pushEvent(h, "routing", data);
          return "cont";
        }

        if (event === "error") {
          const msg = String(data.message ?? "后端处理出错，请稍后重试。");
          await fail(msg);
          answered = true;
          return "stop";
        }

        if (event === "answer_delta") {
          const delta = typeof data.delta === "string" ? data.delta : "";
          const nextText =
            typeof data.text === "string" ? data.text : streamedText + delta;
          streamedText = nextText;
          if (!streamed) {
            streamed = true;
            h.onAnswerStart({
              body: "",
              suggestions: [],
              sources: [],
              disclaimer: "",
              elapsed: "—",
              agentCount: Number(data.agent_count ?? 1) || 1,
            });
            pushEvent(h, "answer_delta", { message: "开始流出" });
          }
          h.onAnswerDelta(nextText);
          steps = applyTimelineEvent(steps, event, data);
          h.onSteps(steps);
          return "cont";
        }

        if (event === "answer_done") {
          steps = applyTimelineEvent(steps, event, data);
          h.onSteps(steps);
          pushEvent(h, "answer_done", data);
          const payload = toAnswerPayload(data);
          if (streamed) {
            if (payload.alert) h.onReveal("alert");
            h.onReveal("suggestions");
            h.onReveal("sources");
            h.onReveal("disclaimer");
            h.onAnswerDone(payload);
          } else {
            await finishAnswer(h, payload, ac.signal);
          }
          answered = true;
          return "stop";
        }

        steps = applyTimelineEvent(steps, event, data);
        h.onSteps(steps);
        pushEvent(h, event, data);
        return "cont";
      };

      let stopped = false;
      while (!ac.signal.aborted && !stopped) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        buffer = buffer.replace(/\r\n/g, "\n");
        let idx = buffer.indexOf("\n\n");
        while (idx >= 0) {
          const block = buffer.slice(0, idx);
          buffer = buffer.slice(idx + 2);
          const frame = parseSseBlock(block);
          if (frame) {
            const next = await handleFrame(frame);
            if (next === "stop") {
              stopped = true;
              break;
            }
          }
          idx = buffer.indexOf("\n\n");
        }
      }

      if (!answered && !ac.signal.aborted) {
        await fail("连接已结束，但未收到完整答案。请确认后端仍在运行后重试。");
      }
    } catch (err) {
      if (ac.signal.aborted) return;
      const isNetwork = err instanceof TypeError || (err instanceof DOMException && err.name === "AbortError");
      if (err instanceof DOMException && err.name === "AbortError") return;
      const msg =
        err instanceof TypeError || isNetwork
          ? OFFLINE_HINT
          : `请求失败：${err instanceof Error ? err.message : String(err)}`;
      await fail(msg);
    } finally {
      if (!ac.signal.aborted) h.onDone();
    }
  })();

  return () => ac.abort();
}

function apiUrl(path: string): string {
  const base = API_BASE.replace(/\/$/, "");
  const p = path.startsWith("/") ? path : `/${path}`;
  return `${base}${p}`;
}

export async function fetchJson<T>(path: string): Promise<T> {
  const res = await fetch(apiUrl(path), { headers: { Accept: "application/json" } });
  if (!res.ok) {
    throw new Error(`服务器返回 HTTP ${res.status}`);
  }
  return (await res.json()) as T;
}

export function getRuntimeStats() {
  return fetchJson<RuntimeStatsPayload>("/api/stats");
}

export function getSessions() {
  return fetchJson<{ sessions: MemorySession[]; source?: string }>("/api/sessions");
}

export function getSessionDetail(id: string) {
  return fetchJson<MemorySessionDetail>(`/api/sessions/${encodeURIComponent(id)}`);
}

export async function deleteSession(id: string): Promise<DeleteSessionResult> {
  const res = await fetch(apiUrl(`/api/sessions/${encodeURIComponent(id)}`), {
    method: "DELETE",
    headers: { Accept: "application/json" },
  });
  if (!res.ok) {
    throw new Error(`服务器返回 HTTP ${res.status}`);
  }
  if (res.status === 204) {
    return { ok: true, session_id: id };
  }
  try {
    const data = (await res.json()) as DeleteSessionResult;
    return { ...data, ok: true, session_id: data.session_id ?? id };
  } catch {
    return { ok: true, session_id: id };
  }
}

export function getSessionMessages(sessionId: string) {
  return fetchJson<{
    session_id: string;
    messages: ShortTermChatMessage[];
    count?: number;
    source?: string;
  }>(`/api/sessions/${encodeURIComponent(sessionId)}/messages`);
}

export function formatHistoryTimeLabel(iso?: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  const now = new Date();
  const sameDay =
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate();
  const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  if (sameDay) return `今天 ${hm}`;
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${hm}`;
}

export function mapHistoryToChatMessages(items: ShortTermChatMessage[]): ChatMessage[] {
  const out: ChatMessage[] = [];
  items.forEach((item, idx) => {
    const role = item.role === "assistant" ? "assistant" : item.role === "user" ? "user" : null;
    if (!role) return;
    const text = String(item.content ?? "");
    const timeLabel = formatHistoryTimeLabel(item.timestamp) || undefined;
    const id = `hist-${idx}-${role}`;
    if (role === "user") {
      out.push({ id, role, text, timeLabel });
      return;
    }
    out.push({
      id,
      role,
      text,
      timeLabel,
      answer: {
        body: text,
        suggestions: [],
        sources: [],
        disclaimer: "",
        elapsed: "—",
        agentCount: 1,
      },
      reveal: { alert: false, suggestions: false, sources: false, disclaimer: false },
    });
  });
  return out;
}

export function getSimilarCases(sessionId: string) {
  return fetchJson<{ cases: SimilarCase[]; source?: string; mem0_enabled?: boolean }>(
    `/api/sessions/${encodeURIComponent(sessionId)}/similar`
  );
}

export function searchKnowledge(q: string, type?: string) {
  const params = new URLSearchParams();
  params.set("q", q);
  if (type && type !== "all") params.set("type", type);
  return fetchJson<{ hits: KnowledgeDoc[]; source?: string; message?: string; error?: string }>(
    `/api/kb/search?${params.toString()}`
  );
}

export function getKnowledgeChunk(id: string) {
  return fetchJson<KnowledgeDoc>(`/api/kb/chunks/${encodeURIComponent(id)}`);
}

export function getSafetyFixes() {
  return fetchJson<{
    records: FixRecord[];
    count: number;
    label?: string;
    assertions?: string[];
  }>("/api/safety/fixes");
}
