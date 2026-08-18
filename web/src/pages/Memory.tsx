import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { deleteSession, getSessionDetail, getSessions, getSimilarCases, USE_MOCK } from "../api/client";
import { MEMORY_SESSIONS, SIMILAR_CASES } from "../mock/data";
import type { MemorySession, MemorySessionDetail, SimilarCase } from "../types";

const SECTION_ORDER = [
  "问题",
  "最终答案",
  "参与 Agent",
  "协作过程",
  "关键发现",
  "经验教训",
  "性能指标",
  "背景",
];

const SECTION_TITLE: Record<string, string> = {
  最终答案: "最终回答",
};

function formatAgentLabel(id: string): string {
  return id.replace(/_agent$/, "").replace(/_/g, " ");
}

function agentsLabel(s: MemorySession): string {
  if (s.agents && s.agents.length) {
    return s.agents.map(formatAgentLabel).join("、");
  }
  if (s.agent_count && s.agent_count > 0) {
    return `${s.agent_count} 个`;
  }
  return s.mode === "Swarm" ? "多个" : "1 个";
}

function parseMarkdownSections(md: string): Record<string, string> {
  const sections: Record<string, string> = {};
  const parts = md.split(/^## /m);
  for (const part of parts.slice(1)) {
    const nl = part.indexOf("\n");
    const heading = (nl === -1 ? part : part.slice(0, nl)).trim();
    const body = (nl === -1 ? "" : part.slice(nl + 1)).trim();
    if (heading) sections[heading] = body;
  }
  return sections;
}

function sectionsFromDetail(d: MemorySessionDetail): { key: string; title: string; body: string }[] {
  const map: Record<string, string> = { ...(d.sections || {}) };
  if (d.markdown) {
    const parsed = parseMarkdownSections(d.markdown);
    for (const [key, body] of Object.entries(parsed)) {
      const cur = (map[key] || "").trim();
      if (!cur || body.trim().length > cur.length) map[key] = body;
    }
  }
  if (!map["问题"] && (d.question_full || d.question)) {
    map["问题"] = (d.question_full || d.question).trim();
  }
  if (!map["最终答案"] && d.final_answer) {
    map["最终答案"] = d.final_answer.trim();
  }
  if (!map["最终答案"] && !d.markdown && d.summary) {
    map["最终答案"] = d.summary.trim();
  }
  const seen = new Set<string>();
  const out: { key: string; title: string; body: string }[] = [];
  for (const key of SECTION_ORDER) {
    const body = (map[key] || "").trim();
    if (!body) continue;
    seen.add(key);
    out.push({ key, title: SECTION_TITLE[key] || key, body });
  }
  for (const [key, raw] of Object.entries(map)) {
    const body = raw.trim();
    if (seen.has(key) || !body) continue;
    out.push({ key, title: key, body });
  }
  return out;
}

function parseLooksIncomplete(
  sections: { body: string }[],
  markdown: string
): boolean {
  const md = markdown.trim();
  if (!md) return false;
  if (!sections.length) return true;
  const extracted = sections.reduce((n, s) => n + s.body.length, 0);
  return extracted < md.length * 0.4;
}

function mockDetail(session: MemorySession): MemorySessionDetail {
  return {
    ...session,
    source: "mock",
    question_full: session.question,
    final_answer: session.summary,
    sections: {
      问题: session.question,
      最终答案: session.summary,
      "参与 Agent": session.agents?.length
        ? session.agents.map((id) => `### ${id}`).join("\n")
        : `${session.agent_count ?? 1} 个`,
    },
  };
}

export function Memory() {
  const mock = USE_MOCK;
  const navigate = useNavigate();
  const [sessions, setSessions] = useState<MemorySession[]>(mock ? MEMORY_SESSIONS : []);
  const [active, setActive] = useState(mock ? MEMORY_SESSIONS[0]?.id ?? "" : "");
  const [cases, setCases] = useState<SimilarCase[]>(mock ? SIMILAR_CASES : []);
  const [error, setError] = useState("");
  const [caseHint, setCaseHint] = useState("");
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [detail, setDetail] = useState<MemorySessionDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");
  const [showRaw, setShowRaw] = useState(false);
  const detailSeq = useRef(0);
  const closeBtnRef = useRef<HTMLButtonElement>(null);

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

  useEffect(() => {
    if (!drawerOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeDrawer();
    };
    window.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    closeBtnRef.current?.focus();
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [drawerOpen]);

  function closeDrawer() {
    detailSeq.current += 1;
    setDrawerOpen(false);
    setDetailLoading(false);
    setDetailError("");
  }

  function openSession(s: MemorySession) {
    setActive(s.id);
    setDrawerOpen(true);
    setShowRaw(false);
    setDetailError("");
    if (mock) {
      setDetail(mockDetail(s));
      setDetailLoading(false);
      return;
    }
    const seq = ++detailSeq.current;
    setDetail(s);
    setDetailLoading(true);
    getSessionDetail(s.id)
      .then((data) => {
        if (seq !== detailSeq.current) return;
        setDetail({ ...s, ...data });
        if (data.error && data.error !== "not_found") {
          setDetailError(data.error);
        } else if (data.error === "not_found" && !data.markdown && !data.sections) {
          setDetailError("");
        }
      })
      .catch((err: unknown) => {
        if (seq !== detailSeq.current) return;
        setDetailError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (seq === detailSeq.current) setDetailLoading(false);
      });
  }

  function openChat(id: string) {
    navigate(`/?session=${encodeURIComponent(id)}`);
  }

  async function removeSession(id: string) {
    if (!id) return;
    if (!window.confirm("确定删除这条会话？")) return;
    if (!mock) {
      try {
        await deleteSession(id);
      } catch (err: unknown) {
        setError(err instanceof Error ? err.message : String(err));
        return;
      }
    }
    const next = sessions.filter((s) => s.id !== id);
    setSessions(next);
    if (active === id) {
      setActive(next[0]?.id || "");
      if (!next.length) {
        setCases([]);
        setCaseHint("");
      }
    }
    if (detail?.id === id) closeDrawer();
  }

  const drawerSections = detail ? sectionsFromDetail(detail) : [];
  const markdown = (detail?.markdown || "").trim();
  const parseIncomplete = detail ? parseLooksIncomplete(drawerSections, markdown) : false;
  const showMarkdown = Boolean(markdown) && (showRaw || parseIncomplete);

  return (
    <main className="page">
      <div className="page-head">
        <h1>会话与记忆</h1>
        <span className="demo-tag">{mock ? "示意数据" : "SessionSummary 文件"}</span>
      </div>
      <p className="page-lede">
        {mock
          ? "历史会话回看、短期记忆上下文，以及 Mem0 长期记忆中的相似案例。点击一条可在右侧查看完整摘要。"
          : "列表来自本地 SessionSummary markdown（Swarm 会话会落盘）。点击一条可在右侧查看完整摘要；「打开对话」会跳到工作台并加载该会话的短期记忆。相似案例走 Mem0。"}
      </p>
      {error ? <div className="card empty-hint">{error}</div> : null}

      {sessions.length === 0 && !error ? (
        <div className="card empty-hint">还没有会话摘要。先在工作台完成一次 Swarm 咨询。</div>
      ) : (
        <div className="card mem-list">
          {sessions.map((s) => (
            <div
              key={s.id}
              className={`row-item mem-row selectable${s.id === active ? " on" : ""}`}
            >
              <button type="button" className="mem-row-main" onClick={() => openSession(s)}>
                <span className="mem-row-top">
                  <span className="mono muted" style={{ fontSize: 11, flexShrink: 0 }}>
                    {s.time}
                  </span>
                  <span className="q">{s.question}</span>
                  <span className={`pill${s.mode === "Swarm" ? "" : " ghost"}`} style={{ fontSize: 10 }}>
                    {s.mode}
                  </span>
                </span>
                <span className="mem-row-meta">
                  <span className="mem-row-agents">参与 Agent：{agentsLabel(s)}</span>
                  <span className="mem-row-summary">{s.summary}</span>
                </span>
              </button>
              <button
                type="button"
                className="session-delete-btn mem-row-delete"
                aria-label={`删除会话 ${s.question || s.id}`}
                title="删除"
                onClick={() => {
                  void removeSession(s.id);
                }}
              >
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                  <path
                    d="M4 5.2h8M6.2 5.2V3.8h3.6v1.4M5.3 5.2v7.1c0 .5.4.9.9.9h3.6c.5 0 .9-.4.9-.9V5.2"
                    stroke="currentColor"
                    strokeWidth="1.4"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>
              </button>
            </div>
          ))}
        </div>
      )}

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

      {drawerOpen && detail ? (
        <>
          <button type="button" className="drawer-mask" aria-label="关闭详情" onClick={closeDrawer} />
          <aside className="session-drawer" role="dialog" aria-modal="true" aria-labelledby="session-drawer-title">
            <header className="session-drawer-head">
              <div className="session-drawer-kicker">
                <span className="mono muted">{detail.time}</span>
                <span className={`pill${detail.mode === "Swarm" ? "" : " ghost"}`} style={{ fontSize: 10 }}>
                  {detail.mode}
                </span>
                <span className="mono muted">{detail.elapsed}</span>
                <span className="session-drawer-actions">
                  <button
                    type="button"
                    className="pill ghost"
                    onClick={() => {
                      void removeSession(detail.id);
                    }}
                  >
                    删除
                  </button>
                  <button type="button" className="pill" onClick={() => openChat(detail.id)}>
                    打开对话
                  </button>
                  <button
                    type="button"
                    className="drawer-close"
                    aria-label="关闭"
                    ref={closeBtnRef}
                    onClick={closeDrawer}
                  >
                    ×
                  </button>
                </span>
              </div>
              <h2 id="session-drawer-title" className="session-drawer-title">
                {detail.question_full || detail.question}
              </h2>
            </header>
            <div className="session-drawer-body">
              {detailLoading ? <p className="muted">正在加载完整摘要…</p> : null}
              {detailError ? <p className="drawer-error">{detailError}</p> : null}
              {!detailLoading && !markdown && drawerSections.length === 0 ? (
                <p className="muted">这条会话还没有完整 SessionSummary 文件，仅有列表摘要。</p>
              ) : null}
              {!detailLoading && !parseIncomplete
                ? drawerSections.map((sec) => (
                    <section className="session-section" key={sec.key}>
                      <h3>{sec.title}</h3>
                      <div className="session-section-body">{sec.body}</div>
                    </section>
                  ))
                : null}
              {!detailLoading && markdown && !parseIncomplete ? (
                <button
                  type="button"
                  className="section-toggle"
                  onClick={() => setShowRaw((prev) => !prev)}
                >
                  {showRaw ? "收起原始 Markdown" : "查看原始 Markdown"}
                </button>
              ) : null}
              {!detailLoading && showMarkdown ? (
                <section className="session-section">
                  <h3>{parseIncomplete ? "完整摘要" : "原始 Markdown"}</h3>
                  <div className="session-section-body">{markdown}</div>
                </section>
              ) : null}
            </div>
          </aside>
        </>
      ) : null}
    </main>
  );
}
