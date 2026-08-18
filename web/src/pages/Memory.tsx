import { useEffect, useState } from "react";
import { getSessions, getSimilarCases, USE_MOCK } from "../api/client";
import { MEMORY_SESSIONS, SIMILAR_CASES } from "../mock/data";
import type { MemorySession, SimilarCase } from "../types";

export function Memory() {
  const mock = USE_MOCK;
  const [sessions, setSessions] = useState<MemorySession[]>(mock ? MEMORY_SESSIONS : []);
  const [active, setActive] = useState(mock ? MEMORY_SESSIONS[0]?.id ?? "" : "");
  const [cases, setCases] = useState<SimilarCase[]>(mock ? SIMILAR_CASES : []);
  const [error, setError] = useState("");
  const [caseHint, setCaseHint] = useState("");

  useEffect(() => {
    if (mock) return;
    let cancelled = false;
    getSessions()
      .then((data) => {
        if (cancelled) return;
        const list = data.sessions ?? [];
        setSessions(list);
        setActive((prev) => prev || list[0]?.id || "");
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [mock]);

  useEffect(() => {
    if (mock || !active) return;
    let cancelled = false;
    getSimilarCases(active)
      .then((data) => {
        if (cancelled) return;
        setCases(data.cases ?? []);
        if (!data.mem0_enabled) setCaseHint("Mem0 未启用，相似案例为空。");
        else if (!(data.cases ?? []).length) setCaseHint("未检索到相似历史案例。");
        else setCaseHint("");
      })
      .catch((err: unknown) => {
        if (!cancelled) setCaseHint(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [mock, active]);

  const current = sessions.find((s) => s.id === active);

  return (
    <main className="page">
      <div className="page-head">
        <h1>会话与记忆</h1>
        <span className="demo-tag">{mock ? "示意数据" : "SessionSummary 文件"}</span>
      </div>
      <p className="page-lede">
        {mock
          ? "历史会话回看、短期记忆上下文，以及 Mem0 长期记忆中的相似案例。"
          : "列表来自本地 SessionSummary markdown（Swarm 会话会落盘）。单 Agent 仅在本次进程内可见。相似案例走 Mem0。"}
      </p>
      {error ? <div className="card empty-hint">{error}</div> : null}

      {sessions.length === 0 && !error ? (
        <div className="card empty-hint">还没有会话摘要。先在工作台完成一次 Swarm 咨询。</div>
      ) : (
        <div className="card mem-list">
          {sessions.map((s) => (
            <button
              type="button"
              key={s.id}
              className={`row-item selectable${s.id === active ? " on" : ""}`}
              onClick={() => setActive(s.id)}
            >
              <span className="mono muted" style={{ fontSize: 11, flexShrink: 0 }}>
                {s.time}
              </span>
              <span className="q">{s.question}</span>
              <span className={`pill${s.mode === "Swarm" ? "" : " ghost"}`} style={{ fontSize: 10 }}>
                {s.mode}
              </span>
              <span className="mono muted" style={{ fontSize: 11 }}>
                {s.elapsed}
              </span>
            </button>
          ))}
        </div>
      )}

      {current ? (
        <div className="card" style={{ padding: "16px 18px", marginTop: 16 }}>
          <div className="muted" style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>
            SessionSummary
          </div>
          <p style={{ fontSize: 13.5 }}>{current.summary}</p>
        </div>
      ) : null}

      <div className="muted" style={{ fontSize: 12, fontWeight: 600, margin: "18px 0 8px" }}>
        相似历史案例（Mem0 长期记忆）
      </div>
      {cases.length === 0 ? (
        <div className="card empty-hint">{caseHint || (mock ? "没有相似案例。" : "暂无相似案例。")}</div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {cases.map((c) => (
            <div className="card case-card" key={`${c.score}-${c.text.slice(0, 24)}`}>
              <span className="pill wood" style={{ fontSize: 10, flexShrink: 0 }}>
                相似度 {Number(c.score).toFixed(2)}
              </span>
              <span style={{ fontSize: 12.5 }}>{c.text}</span>
            </div>
          ))}
        </div>
      )}
    </main>
  );
}
