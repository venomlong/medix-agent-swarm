export type StepStatus = "pending" | "running" | "done" | "failed" | "timeout";

export type RoutingMode = "idle" | "pending" | "swarm" | "single";

export interface SkillTag {
  name: string;
  active: boolean;
}

export interface TimelineStep {
  id: string;
  title: string;
  agentLabel: string;
  status: StepStatus;
  desc: string;
  duration?: string;
  skills: SkillTag[];
}

export interface StreamEvent {
  ts: string;
  name: string;
}

export interface AnswerPayload {
  body: string;
  alert?: string;
  alertNote?: string;
  suggestions: string[];
  sources: string[];
  disclaimer: string;
  elapsed: string;
  agentCount: number;
  timedOut?: boolean;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  timeLabel?: string;
  routing?: Exclude<RoutingMode, "idle" | "pending">;
  subtaskCount?: number;
  answer?: AnswerPayload;
  streaming?: boolean;
  reveal?: AnswerReveal;
}

export interface AnswerReveal {
  alert: boolean;
  suggestions: boolean;
  sources: boolean;
  disclaimer: boolean;
}

export type DocType = "lifestyle" | "icd10" | "guideline";

export interface KnowledgeDoc {
  id: string;
  title: string;
  type: DocType;
  typeLabel: string;
  snippet: string;
  score: number;
}

export interface MemorySession {
  id: string;
  time: string;
  question: string;
  mode: "Swarm" | "单 Agent";
  elapsed: string;
  summary: string;
}

export interface SimilarCase {
  score: number;
  text: string;
}

export interface FixRecord {
  time: string;
  kind: "就医提醒" | "免责声明";
  detail: string;
}

export interface RuntimeStatsPayload {
  scope: string;
  label: string;
  started_at: string;
  uptime_s: number;
  chat_count: number;
  swarm_count: number;
  single_count: number;
  error_count: number;
  timeout_count: number;
  swarm_share: number;
  avg_latency: string;
  swarm_latency: string;
  single_latency: string;
  auto_fix: number;
  disclaimer_fix: number;
  emergency_fix: number;
}
