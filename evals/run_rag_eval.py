"""
RAG 检索评测（本地，不调 LLM）。

对每条问句 MedicalKnowledgeBase.search(top_k=5)，取 hit.metadata.doc_id，
计算 recall@1/3/5 与 MRR。默认 hybrid（向量 + BM25 RRF）。
首次加载 embedding 可能需要数十秒。

PowerShell（仓库根）:
  .venv\\Scripts\\python.exe evals\\run_rag_eval.py
  .venv\\Scripts\\python.exe evals\\run_rag_eval.py --mode vector
  .venv\\Scripts\\python.exe evals\\run_rag_eval.py --compare
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

_EVALS_DIR = Path(__file__).resolve().parent
if str(_EVALS_DIR) not in sys.path:
    sys.path.insert(0, str(_EVALS_DIR))

from common import (  # noqa: E402
    default_dataset,
    ensure_import_paths,
    fmt_pct,
    load_jsonl,
    pct,
    print_metrics,
    print_progress,
    repo_root,
    save_result,
)

ensure_import_paths()


def unique_doc_ids(hits: Sequence[Dict[str, Any]]) -> List[str]:
    """按检索顺序去重 doc_id（同一文档多 chunk 只保留第一次出现）。"""
    ordered: List[str] = []
    seen = set()
    for hit in hits:
        meta = hit.get("metadata") or {}
        if not isinstance(meta, dict):
            continue
        doc_id = meta.get("doc_id")
        if not doc_id:
            continue
        doc_id = str(doc_id)
        if doc_id in seen:
            continue
        seen.add(doc_id)
        ordered.append(doc_id)
    return ordered


def recall_at_k(retrieved: Sequence[str], expected: Sequence[str], k: int) -> float:
    if not expected:
        return 0.0
    top = set(retrieved[:k])
    hit = sum(1 for doc_id in expected if doc_id in top)
    return hit / len(expected)


def mrr_score(retrieved: Sequence[str], expected: Sequence[str]) -> float:
    expected_set = set(expected)
    for rank, doc_id in enumerate(retrieved, start=1):
        if doc_id in expected_set:
            return 1.0 / rank
    return 0.0


def _eval_one(
    item: Dict[str, Any],
    retrieved: List[str],
    raw_hits: List[Dict[str, Any]],
) -> Dict[str, Any]:
    expected = [str(x) for x in (item.get("expected_doc_ids") or [])]
    r1 = recall_at_k(retrieved, expected, 1)
    r3 = recall_at_k(retrieved, expected, 3)
    r5 = recall_at_k(retrieved, expected, 5)
    mrr = mrr_score(retrieved, expected)
    return {
        "id": item.get("id"),
        "question": item.get("question"),
        "note": item.get("note"),
        "expected_doc_ids": expected,
        "retrieved_doc_ids": retrieved,
        "hit_scores": [
            {
                "doc_id": (h.get("metadata") or {}).get("doc_id"),
                "score": h.get("score"),
            }
            for h in raw_hits
        ],
        "recall@1": r1,
        "recall@3": r3,
        "recall@5": r5,
        "mrr": mrr,
        "correct@1": r1 >= 1.0,
        "correct@5": r5 >= 1.0,
    }


def _summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(rows)
    return {
        "n": n,
        "recall_at_1": pct(sum(r["recall@1"] for r in rows), n),
        "recall_at_3": pct(sum(r["recall@3"] for r in rows), n),
        "recall_at_5": pct(sum(r["recall@5"] for r in rows), n),
        "mrr": pct(sum(r["mrr"] for r in rows), n),
        "fail_at_5_ids": [r["id"] for r in rows if r["recall@5"] < 1.0],
        "fail_at_1_ids": [r["id"] for r in rows if r["recall@1"] < 1.0],
    }


def _default_eval_db() -> Path:
    cache = _EVALS_DIR / ".cache"
    cache.mkdir(parents=True, exist_ok=True)
    return cache / "milvus_lite.db"


def _ensure_kb(db_path: Path):
    """
    打开评测用知识库。默认用 evals/.cache 独立库，避免与正在运行的
    Web 服务抢 knowledge/data/milvus_lite.db 的文件锁；库空则从 txt 导入。
    """
    from knowledge.milvus_kb import MedicalKnowledgeBase
    from knowledge.scripts.import_hardcoded_data import load_documents_from_directory

    kb = MedicalKnowledgeBase(db_path=str(db_path))
    n = kb.count_documents()
    probe = kb.search("高血压饮食", top_k=1, mode="vector")
    if n > 0 or probe:
        print(f"知识库已有数据（count≈{n}，BM25={kb.bm25_size()}），跳过导入")
        return kb

    doc_dir = repo_root() / "knowledge" / "data" / "documents"
    print(f"评测库为空，从 {doc_dir} 导入语料…")
    docs = load_documents_from_directory(doc_dir)
    if not docs:
        raise RuntimeError(f"未找到知识库文档: {doc_dir}")
    added = kb.add_documents(docs)
    print(f"已导入 {added} 个 chunk（{len(docs)} 篇文档）")
    return kb


def _eval_dataset(kb, items: List[Dict[str, Any]], top_k: int, mode: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    total = len(items)
    for i, item in enumerate(items, start=1):
        qid = str(item.get("id") or "")
        print_progress(i, total, f"{mode} {qid}")
        question = str(item.get("question") or "")
        hits = kb.search(question, top_k=top_k, mode=mode) or []
        retrieved = unique_doc_ids(hits)
        rows.append(_eval_one(item, retrieved, hits))
    return rows


def run(
    dataset: Path,
    top_k: int,
    db_path: Optional[Path] = None,
    mode: str = "hybrid",
    compare: bool = False,
) -> Dict[str, Any]:
    items = load_jsonl(dataset)
    db_path = Path(db_path) if db_path else _default_eval_db()
    print(f"加载知识库: {db_path}")
    print("提示: 首次加载 embedding 模型（bge-small-zh-v1.5）可能需要数十秒…", flush=True)

    kb = _ensure_kb(db_path)
    retrieval_mode = mode if mode in ("hybrid", "vector", "bm25") else "hybrid"

    if compare:
        print("对比 vector-only vs hybrid …", flush=True)
        vector_rows = _eval_dataset(kb, items, top_k, "vector")
        hybrid_rows = _eval_dataset(kb, items, top_k, "hybrid")
        vector_metrics = _summarize(vector_rows)
        hybrid_metrics = _summarize(hybrid_rows)
        return {
            "name": "rag",
            "mode": "local_milvus_hybrid",
            "retrieval_mode": "hybrid",
            "dataset": str(dataset),
            "top_k": top_k,
            "db_path": str(db_path),
            "metrics": hybrid_metrics,
            "baseline_vector": {
                "retrieval_mode": "vector",
                "metrics": vector_metrics,
            },
            "items": hybrid_rows,
            "vector_items": vector_rows,
        }

    rows = _eval_dataset(kb, items, top_k, retrieval_mode)
    metrics = _summarize(rows)
    mode_label = f"local_milvus_{retrieval_mode}"
    return {
        "name": "rag",
        "mode": mode_label,
        "retrieval_mode": retrieval_mode,
        "dataset": str(dataset),
        "top_k": top_k,
        "db_path": str(db_path),
        "metrics": metrics,
        "items": rows,
    }


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RAG 检索评测（本地 Milvus，不调 LLM）")
    parser.add_argument("--dataset", type=Path, default=default_dataset("rag_qa.jsonl"))
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help="Milvus Lite 路径（默认 evals/.cache/milvus_lite.db，避免与运行中服务抢锁）",
    )
    parser.add_argument(
        "--mode",
        choices=["hybrid", "vector", "bm25"],
        default="hybrid",
        help="检索模式：hybrid=向量+BM25 RRF（默认），vector=仅向量，bm25=仅关键词",
    )
    parser.add_argument(
        "--compare",
        action="store_true",
        help="同一次加载下对比 vector-only 与 hybrid，主指标为 hybrid",
    )
    return parser.parse_args(argv)


def _print_rag_metrics(title: str, metrics: Dict[str, Any]) -> None:
    print_metrics(
        title,
        metrics,
        keys=["n", "recall_at_1", "recall_at_3", "recall_at_5", "mrr"],
    )
    print(f"recall@1: {fmt_pct(metrics['recall_at_1'])}")
    print(f"recall@3: {fmt_pct(metrics['recall_at_3'])}")
    print(f"recall@5: {fmt_pct(metrics['recall_at_5'])}")
    print(f"MRR: {metrics['mrr']:.4f}")


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    payload = run(args.dataset, args.top_k, args.db_path, args.mode, args.compare)
    out = save_result("rag", payload)
    metrics = payload["metrics"]
    retrieval_mode = payload.get("retrieval_mode") or args.mode
    _print_rag_metrics(f"RAG 检索评测（{retrieval_mode}）", metrics)
    baseline = payload.get("baseline_vector") or {}
    if baseline.get("metrics"):
        _print_rag_metrics("RAG 检索评测（vector baseline）", baseline["metrics"])
    print(f"结果已写入: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
