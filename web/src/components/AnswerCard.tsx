import { useState } from "react";
import type { AnswerPayload, AnswerReveal, AnswerUsage, GuardrailInfo, SourceRef } from "../types";
import { AgentAvatarGroup } from "./AgentAvatar";

type Props = {
  answer: AnswerPayload;
  streaming?: boolean;
  reveal?: AnswerReveal;
};

const DEFAULT_REVEAL: AnswerReveal = {
  alert: true,
  suggestions: true,
  sources: true,
  disclaimer: true,
};

const EMERGENCY_ALERT = "检测到疑似急症，系统已跳过常规分析流程，请优先执行急救指引。";

function scoreLabel(score: number): string {
  const pct = score <= 1 ? Math.round(score * 100) : Math.round(score);
  return `${pct}%`;
}

function formatYuan(cost: number): string {
  if (!Number.isFinite(cost) || cost <= 0) return "¥0";
  if (cost >= 1) return `¥${cost.toFixed(2)}`;
  if (cost >= 0.01) return `¥${cost.toFixed(3)}`;
  return `¥${cost.toFixed(6).replace(/0+$/, "").replace(/\.$/, "")}`;
}

function usageChipLabel(usage?: AnswerUsage): string {
  if (!usage) return "";
  const tokens = Math.max(0, Math.round(usage.totalTokens));
  const calls = Math.max(0, Math.round(usage.llmCalls));
  if (tokens <= 0 && calls <= 0) return "本轮未采到 usage";
  return `${tokens.toLocaleString("zh-CN")} tok · ${formatYuan(usage.cost)}`;
}

function guardrailLabel(info: GuardrailInfo): string {
  const bits = ["输出护栏已介入"];
  if (info.rewritten) bits.push("并改写答案");
  if (info.action) bits.push(info.action);
  const first = info.violations?.find((v) => v.type)?.type;
  if (first) bits.push(first);
  return bits.join(" · ");
}

function SourcePills({ sources }: { sources: SourceRef[] }) {
  const [openId, setOpenId] = useState<string | null>(null);
  const open = sources.find((s) => s.id === openId) ?? null;
  return (
    <div>
      <strong style={{ fontSize: 13.5 }}>参考来源</strong>
      <div className="pills" style={{ marginTop: 6 }}>
        {sources.map((src) => {
          const active = openId === src.id;
          return (
            <button
              key={src.id}
              type="button"
              className={`pill wood source${active ? " open" : ""}`}
              onClick={() => setOpenId(active ? null : src.id)}
              aria-expanded={active}
            >
              {src.title}
              {src.score > 0 ? ` · ${scoreLabel(src.score)}` : ""}
            </button>
          );
        })}
      </div>
      {open ? (
        <p className="source-snippet">
          {open.snippet || "（无摘录）"}
          {open.source ? <span className="muted"> · {open.source}</span> : null}
        </p>
      ) : null}
    </div>
  );
}

export function AnswerCard({ answer, streaming, reveal }: Props) {
  const r = reveal ?? DEFAULT_REVEAL;
  const emergency = Boolean(answer.emergency);
  const alertText = (answer.alert || "").trim() || (emergency ? EMERGENCY_ALERT : "");
  const usageText = usageChipLabel(answer.usage);
  const guardrail = answer.guardrail;
  return (
    <article className={`answer${emergency ? " emergency" : ""}`}>
      <div className="answer-head">
        <AgentAvatarGroup count={emergency ? 1 : answer.agentCount} />
        <span className="muted" style={{ fontSize: 12 }}>
          {emergency ? "急症分诊 · 已短路常规协作流程" : `${answer.agentCount} 个专业 Agent 协作回答`}
        </span>
        <span style={{ flex: 1 }} />
        <div className="answer-meta">
          <span className="mono muted" style={{ fontSize: 12, whiteSpace: "nowrap" }}>
            {answer.elapsed}
          </span>
          {usageText ? <span className="usage-chip">{usageText}</span> : null}
          {answer.traceId ? (
            <span className="trace-chip" title={answer.traceId}>
              trace {answer.traceId}
            </span>
          ) : null}
        </div>
      </div>

      {r.alert && alertText ? (
        <div className={`alert-bar${emergency ? " critical" : ""}`}>
          {alertText}
          {answer.alertNote ? (
            <div style={{ fontWeight: 400, fontSize: 11, marginTop: 2 }}>{answer.alertNote}</div>
          ) : null}
        </div>
      ) : null}

      <p className="answer-body">
        {answer.body}
        {streaming ? <span className="caret" /> : null}
      </p>

      {r.suggestions && answer.suggestions.length > 0 ? (
        <div>
          <strong style={{ fontSize: 13.5 }}>{emergency ? "【急救建议】" : "【核心建议】"}</strong>
          {answer.suggestions.map((s, i) => (
            <p key={s} style={{ fontSize: 13, marginTop: i === 0 ? 4 : 0 }}>
              {i + 1}. {s}
            </p>
          ))}
        </div>
      ) : null}

      {r.sources && answer.sources.length > 0 ? <SourcePills sources={answer.sources} /> : null}

      {guardrail?.triggered ? <p className="guardrail-hint">{guardrailLabel(guardrail)}</p> : null}

      {r.disclaimer ? (
        <div style={{ borderTop: "1px solid var(--line)", paddingTop: 8 }}>
          <p className="muted" style={{ fontSize: 11.5 }}>
            {answer.disclaimer}
          </p>
        </div>
      ) : null}
    </article>
  );
}
