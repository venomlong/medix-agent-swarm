import { useEffect, useMemo, useRef, useState } from "react";
import { getKnowledgeChunk, searchKnowledge, USE_MOCK } from "../api/client";
import { KNOWLEDGE_DOCS } from "../mock/data";
import type { DocType, KnowledgeDoc } from "../types";

const FILTERS: { id: "all" | DocType; label: string }[] = [
  { id: "all", label: "全部" },
  { id: "lifestyle", label: "lifestyle" },
  { id: "icd10", label: "ICD-10" },
  { id: "guideline", label: "指南" },
];

function previewText(d: KnowledgeDoc): string {
  const snippet = (d.snippet || "").trim();
  if (snippet) return snippet;
  const content = (d.content || "").replace(/\n/g, " ").trim();
  if (content.length <= 180) return content;
  return `${content.slice(0, 180)}…`;
}

function mockDetail(doc: KnowledgeDoc): KnowledgeDoc {
  const content = (doc.content || "").trim() || doc.snippet;
  return {
    ...doc,
    content,
    source: doc.source || doc.filename || doc.title,
  };
}

export function Knowledge() {
  const mock = USE_MOCK;
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<"all" | DocType>("all");
  const [hits, setHits] = useState<KnowledgeDoc[]>([]);
  const [hint, setHint] = useState(mock ? "" : "输入关键词后检索 Milvus。");
  const [loading, setLoading] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [detail, setDetail] = useState<KnowledgeDoc | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");
  const detailSeq = useRef(0);
  const closeBtnRef = useRef<HTMLButtonElement>(null);

  const mockDocs = useMemo(() => {
    const q = query.trim().toLowerCase();
    return KNOWLEDGE_DOCS.filter((d) => {
      if (filter !== "all" && d.type !== filter) return false;
      if (!q) return true;
      return (
        d.title.toLowerCase().includes(q) ||
        d.snippet.toLowerCase().includes(q) ||
        d.typeLabel.toLowerCase().includes(q) ||
        (d.content || "").toLowerCase().includes(q) ||
        (d.source || "").toLowerCase().includes(q)
      );
    }).sort((a, b) => b.score - a.score);
  }, [query, filter]);

  useEffect(() => {
    if (mock) return;
    const q = query.trim();
    if (!q) {
      setHits([]);
      setHint("输入关键词后检索 Milvus。");
      setLoading(false);
      return;
    }
    setLoading(true);
    const timer = window.setTimeout(() => {
      searchKnowledge(q, filter)
        .then((data) => {
          setHits(data.hits ?? []);
          setHint(data.error || data.message || (data.hits?.length ? "" : "没有匹配的文档。"));
        })
        .catch((err: unknown) => {
          setHits([]);
          setHint(err instanceof Error ? err.message : String(err));
        })
        .finally(() => setLoading(false));
    }, 280);
    return () => window.clearTimeout(timer);
  }, [mock, query, filter]);

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

  function openChunk(doc: KnowledgeDoc) {
    setDrawerOpen(true);
    setDetailError("");
    setDetail(doc);
    if (mock) {
      setDetail(mockDetail(doc));
      setDetailLoading(false);
      return;
    }
    const seq = ++detailSeq.current;
    setDetailLoading(true);
    getKnowledgeChunk(doc.id)
      .then((data) => {
        if (seq !== detailSeq.current) return;
        setDetail({
          ...doc,
          ...data,
          score: doc.score,
          snippet: doc.snippet || data.snippet,
        });
        if (data.error && data.error !== "not_found") {
          setDetailError(data.error);
        } else if (data.error === "not_found" && !(data.content || doc.content)) {
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

  const docs = mock ? mockDocs : hits;
  const fullText = (detail?.content || "").trim();
  const fallbackText = fullText || (detail?.snippet || "").trim();
  const sourceLabel = (detail?.source || detail?.filename || detail?.title || "").trim();

  return (
    <main className="page">
      <div className="page-head">
        <h1>知识库</h1>
        <span className="demo-tag">{mock ? "示意数据" : "Milvus 检索"}</span>
      </div>
      <p className="page-lede">
        {mock
          ? "Milvus 检索调试台。当前为前端过滤 mock 文档。点击卡片可在右侧查看完整 chunk。"
          : "直接调用知识库 search。空查询不检索，避免无意义的向量计算。点击卡片可在右侧查看完整 chunk。"}
      </p>

      <div className="kb-toolbar">
        <input
          className="search-input"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="输入查询，例如：高血压、糖尿病、指南……"
          aria-label="知识库检索"
        />
        <div className="pills">
          {FILTERS.map((f) => (
            <button
              type="button"
              key={f.id}
              className={`pill filter ${filter === f.id ? "on" : "ghost"}`}
              onClick={() => setFilter(f.id)}
            >
              {f.label}
            </button>
          ))}
        </div>
      </div>

      {docs.length === 0 ? (
        <div className="card empty-hint">{loading ? "检索中…" : hint || "没有匹配的文档，试试其他关键词或类型。"}</div>
      ) : (
        <div className="doc-list">
          {docs.map((d) => (
            <button
              type="button"
              className={`card doc-card${detail?.id === d.id && drawerOpen ? " on" : ""}`}
              key={d.id}
              onClick={() => openChunk(d)}
            >
              <div className="doc-meta">
                <span className="pill wood" style={{ fontSize: 10 }}>
                  {d.typeLabel}
                </span>
                <span className="mono muted" style={{ fontSize: 11 }}>
                  score {Number(d.score).toFixed(2)}
                </span>
              </div>
              <h3>{d.title}</h3>
              <p className="doc-snippet">{previewText(d)}</p>
            </button>
          ))}
        </div>
      )}
      <p className="caption">
        共 {docs.length} 条 · {mock ? "示意数据 · 分数为前端演示值" : loading ? "检索中" : "Milvus COSINE 相似度"}
      </p>

      {drawerOpen && detail ? (
        <>
          <button type="button" className="drawer-mask" aria-label="关闭详情" onClick={closeDrawer} />
          <aside className="session-drawer" role="dialog" aria-modal="true" aria-labelledby="kb-drawer-title">
            <header className="session-drawer-head">
              <div className="session-drawer-kicker">
                <span className="pill wood" style={{ fontSize: 10 }}>
                  {detail.typeLabel}
                </span>
                <span className="mono muted">score {Number(detail.score).toFixed(2)}</span>
                <span className="session-drawer-actions">
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
              <h2 id="kb-drawer-title" className="session-drawer-title">
                {detail.title}
              </h2>
            </header>
            <div className="session-drawer-body">
              {detailLoading ? <p className="muted">正在加载完整内容…</p> : null}
              {detailError ? <p className="drawer-error">{detailError}</p> : null}
              <section className="session-section">
                <h3>来源</h3>
                <div className="session-section-body">{sourceLabel || "未知文档"}</div>
              </section>
              <section className="session-section">
                <h3>Chunk ID</h3>
                <div className="session-section-body mono">{detail.id}</div>
              </section>
              <section className="session-section">
                <h3>完整正文</h3>
                {!detailLoading && !fallbackText ? (
                  <p className="muted">这条记录还没有完整正文，仅有列表预览。</p>
                ) : (
                  <div className="session-section-body" style={{ whiteSpace: "pre-wrap" }}>
                    {fallbackText}
                  </div>
                )}
              </section>
            </div>
          </aside>
        </>
      ) : null}
    </main>
  );
}
