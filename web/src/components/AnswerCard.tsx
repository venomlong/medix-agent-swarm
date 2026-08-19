import type { AnswerPayload, AnswerReveal } from "../types";
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

export function AnswerCard({ answer, streaming, reveal }: Props) {
  const r = reveal ?? DEFAULT_REVEAL;
  const emergency = Boolean(answer.emergency);
  return (
    <article className={`answer${emergency ? " emergency" : ""}`}>
      <div className="answer-head">
        <AgentAvatarGroup count={emergency ? 1 : answer.agentCount} />
        <span className="muted" style={{ fontSize: 12 }}>
          {emergency ? "急症分诊 · 已短路常规协作流程" : `${answer.agentCount} 个专业 Agent 协作回答`}
        </span>
        <span style={{ flex: 1 }} />
        <span className="mono muted" style={{ fontSize: 12 }}>
          {answer.elapsed}
        </span>
      </div>

      {r.alert && answer.alert ? (
        <div className={`alert-bar${emergency ? " critical" : ""}`}>
          {answer.alert}
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

      {r.sources && answer.sources.length > 0 ? (
        <div className="pills">
          {answer.sources.map((src) => (
            <span key={src} className="pill wood">
              {src}
            </span>
          ))}
        </div>
      ) : null}

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
