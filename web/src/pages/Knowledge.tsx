import { useEffect, useMemo, useState } from "react";
import { searchKnowledge, USE_MOCK } from "../api/client";
import { KNOWLEDGE_DOCS } from "../mock/data";
import type { DocType, KnowledgeDoc } from "../types";

const FILTERS: { id: "all" | DocType; label: string }[] = [
  { id: "all", label: "全部" },
  { id: "lifestyle", label: "lifestyle" },
  { id: "icd10", label: "ICD-10" },
  { id: "guideline", label: "指南" },
];

export function Knowledge() {
  const mock = USE_MOCK;
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<"all" | DocType>("all");
  const [hits, setHits] = useState<KnowledgeDoc[]>([]);
  const [hint, setHint] = useState(mock ? "" : "输入关键词后检索 Milvus。");
  const [loading, setLoading] = useState(false);

  const mockDocs = useMemo(() => {
    const q = query.trim().toLowerCase();
    return KNOWLEDGE_DOCS.filter((d) => {
      if (filter !== "all" && d.type !== filter) return false;
      if (!q) return true;
      return (
        d.title.toLowerCase().includes(q) ||
        d.snippet.toLowerCase().includes(q) ||
        d.typeLabel.toLowerCase().includes(q)
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

  const docs = mock ? mockDocs : hits;

  return (
    <main className="page">
      <div className="page-head">
        <h1>知识库</h1>
        <span className="demo-tag">{mock ? "示意数据" : "Milvus 检索"}</span>
      </div>
      <p className="page-lede">
        {mock
          ? "Milvus 检索调试台。当前为前端过滤 mock 文档。"
          : "直接调用知识库 search。空查询不检索，避免无意义的向量计算。"}
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
            <article className="card doc-card" key={d.id}>
              <div className="doc-meta">
                <span className="pill wood" style={{ fontSize: 10 }}>
                  {d.typeLabel}
                </span>
                <span className="mono muted" style={{ fontSize: 11 }}>
                  score {Number(d.score).toFixed(2)}
                </span>
              </div>
              <h3>{d.title}</h3>
              <p>{d.snippet}</p>
            </article>
          ))}
        </div>
      )}
      <p className="caption">
        共 {docs.length} 条 · {mock ? "示意数据 · 分数为前端演示值" : loading ? "检索中" : "Milvus COSINE 相似度"}
      </p>
    </main>
  );
}
