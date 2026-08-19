"""
汇总最新评测 JSON，生成 markdown 报告。

扫描 evals/results/{safety,routing,rag}_*.json 各取最新一份，
写入 evals/results/{YYYYMMDD}_report.md。

PowerShell（仓库根）:
  .venv\\Scripts\\python.exe evals\\report.py
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_EVALS_DIR = Path(__file__).resolve().parent
if str(_EVALS_DIR) not in sys.path:
    sys.path.insert(0, str(_EVALS_DIR))

from common import fmt_pct, results_dir  # noqa: E402


PREFIXES = ("safety", "routing", "rag")


def _latest_json(prefix: str, directory: Path) -> Optional[Path]:
    files = sorted(
        directory.glob(f"{prefix}_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return files[0] if files else None


def _load(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _metric(metrics: Dict[str, Any], key: str, as_pct: bool = True) -> str:
    if key not in metrics:
        return "N/A"
    val = metrics[key]
    if isinstance(val, bool):
        return "是" if val else "否"
    if isinstance(val, float) and as_pct and 0.0 <= val <= 1.0:
        return fmt_pct(val)
    return str(val)


def _fail_rows(items: List[Dict[str, Any]], pred) -> List[Dict[str, Any]]:
    return [it for it in items if pred(it)]


def _render_failures(title: str, rows: List[Dict[str, Any]], cols: List[Tuple[str, str]]) -> List[str]:
    lines = [f"### {title}", ""]
    if not rows:
        lines.append("无失败用例。")
        lines.append("")
        return lines
    header = "| " + " | ".join(c[0] for c in cols) + " |"
    sep = "| " + " | ".join("---" for _ in cols) + " |"
    lines.extend([header, sep])
    for row in rows:
        cells = []
        for _, key in cols:
            val = row.get(key, "")
            if isinstance(val, list):
                val = ", ".join(str(x) for x in val)
            text = str(val).replace("|", "\\|").replace("\n", " ")
            if len(text) > 80:
                text = text[:77] + "..."
            cells.append(text)
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    return lines


def build_markdown(payloads: Dict[str, Tuple[Path, Dict[str, Any]]]) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    lines: List[str] = [
        f"# MediX 评测报告（{today}）",
        "",
        "本报告由 `evals/report.py` 根据 `evals/results/` 下各任务最新 JSON 汇总。",
        "安全与 RAG 默认为离线；路由若 `mode=offline_heuristic` 则不是真实 LLM 数字。",
        "",
        "## 总表",
        "",
        "| 任务 | 模式 | 样本量 | 关键指标 |",
        "| --- | --- | --- | --- |",
    ]

    safety = payloads.get("safety")
    routing = payloads.get("routing")
    rag = payloads.get("rag")

    if safety:
        m = safety[1].get("metrics") or {}
        lines.append(
            f"| 安全红线 | {safety[1].get('mode', '')} | {m.get('n', '')} | "
            f"急症召回 {_metric(m, 'emergency_recall')}，"
            f"漏报 {m.get('emergency_misses', 'N/A')}，"
            f"误伤率 {_metric(m, 'false_positive_rate')}，"
            f"非边缘准确率 {_metric(m, 'non_borderline_accuracy')}，"
            f"红线通过 {_metric(m, 'redline_passed')} |"
        )
    else:
        lines.append("| 安全红线 | — | — | 尚未跑 `run_safety_eval.py` |")

    if routing:
        m = routing[1].get("metrics") or {}
        lines.append(
            f"| 路由 | {routing[1].get('mode', '')} | {m.get('n', '')} | "
            f"模式准确率 {_metric(m, 'mode_accuracy')}，"
            f"Agent 全匹配 {_metric(m, 'agent_exact_match_rate')}，"
            f"Agent 部分匹配 {_metric(m, 'agent_partial_match_rate')} |"
        )
    else:
        lines.append("| 路由 | — | — | 尚未跑 `run_routing_eval.py` |")

    if rag:
        m = rag[1].get("metrics") or {}
        mrr_val = m.get("mrr")
        mrr_cell = f"{mrr_val:.4f}" if isinstance(mrr_val, float) else str(mrr_val or "N/A")
        lines.append(
            f"| RAG | {rag[1].get('mode', '')} | {m.get('n', '')} | "
            f"recall@1 {_metric(m, 'recall_at_1')}，"
            f"recall@3 {_metric(m, 'recall_at_3')}，"
            f"recall@5 {_metric(m, 'recall_at_5')}，"
            f"MRR {mrr_cell} |"
        )
    else:
        lines.append("| RAG | — | — | 尚未跑 `run_rag_eval.py` |")

    lines.extend(["", "## 数据来源", ""])
    for name in PREFIXES:
        pair = payloads.get(name)
        if not pair:
            lines.append(f"- `{name}`: 缺失")
            continue
        path, data = pair
        lines.append(
            f"- `{name}`: `{path.name}`（generated_at={data.get('generated_at', 'N/A')}）"
        )
    lines.append("")

    if safety:
        data = safety[1]
        m = data.get("metrics") or {}
        lines.extend(
            [
                "## 安全红线明细",
                "",
                f"- 模式: `{data.get('mode')}`",
                f"- 非边缘准确率: {_metric(m, 'non_borderline_accuracy')}",
                f"- 急症召回: {_metric(m, 'emergency_recall')}（漏报 {m.get('emergency_misses')}）",
                f"- 误伤率: {_metric(m, 'false_positive_rate')}（误报 {m.get('false_positives')}）",
                f"- 类别准确率: {_metric(m, 'category_accuracy')}",
                f"- 边缘词 LLM 交接准确率: {_metric(m, 'borderline_handoff_accuracy')}",
                f"- 红线通过（漏报=0）: {_metric(m, 'redline_passed')}",
                "",
            ]
        )
        fails = _fail_rows(data.get("items") or [], lambda it: not it.get("correct"))
        lines.extend(
            _render_failures(
                "失败用例",
                fails,
                [
                    ("id", "id"),
                    ("verdict", "verdict"),
                    ("expect", "expect_emergency"),
                    ("pred", "pred_emergency"),
                    ("question", "question"),
                ],
            )
        )

    if routing:
        data = routing[1]
        m = data.get("metrics") or {}
        lines.extend(
            [
                "## 路由明细",
                "",
                f"- 模式: `{data.get('mode')}`",
                f"- 说明: {data.get('note', '')}",
                f"- 模式准确率: {_metric(m, 'mode_accuracy')}",
                f"- Agent 完全匹配: {_metric(m, 'agent_exact_match_rate')}",
                f"- Agent 部分匹配: {_metric(m, 'agent_partial_match_rate')}",
                f"- 全对（模式+Agent）: {_metric(m, 'full_match_rate')}",
                f"- 错误条数: {m.get('n_error', 0)}",
                "",
            ]
        )
        fails = _fail_rows(data.get("items") or [], lambda it: not it.get("correct"))
        lines.extend(
            _render_failures(
                "失败用例",
                fails,
                [
                    ("id", "id"),
                    ("verdict", "verdict"),
                    ("expected_mode", "expected_mode"),
                    ("pred_mode", "pred_mode"),
                    ("expected_agents", "expected_agents"),
                    ("pred_agents", "pred_agents"),
                    ("question", "question"),
                ],
            )
        )

    if rag:
        data = rag[1]
        m = data.get("metrics") or {}
        mrr_val = m.get("mrr")
        mrr_s = f"{mrr_val:.4f}" if isinstance(mrr_val, float) else str(mrr_val)
        rag_lines = [
            "## RAG 明细",
            "",
            f"- 模式: `{data.get('mode')}`",
        ]
        if data.get("retrieval_mode"):
            rag_lines.append(f"- 检索: `{data.get('retrieval_mode')}`（hybrid=向量+BM25 RRF）")
        rag_lines.extend(
            [
                f"- recall@1: {_metric(m, 'recall_at_1')}",
                f"- recall@3: {_metric(m, 'recall_at_3')}",
                f"- recall@5: {_metric(m, 'recall_at_5')}",
                f"- MRR: {mrr_s}",
                "",
            ]
        )
        baseline = data.get("baseline_vector") or {}
        bm = baseline.get("metrics") or {}
        if bm:
            b_mrr = bm.get("mrr")
            b_mrr_s = f"{b_mrr:.4f}" if isinstance(b_mrr, float) else str(b_mrr)
            rag_lines.extend(
                [
                    "### vector-only 对照",
                    "",
                    f"- recall@1: {_metric(bm, 'recall_at_1')}",
                    f"- recall@3: {_metric(bm, 'recall_at_3')}",
                    f"- recall@5: {_metric(bm, 'recall_at_5')}",
                    f"- MRR: {b_mrr_s}",
                    "",
                ]
            )
        lines.extend(rag_lines)
        fails = _fail_rows(data.get("items") or [], lambda it: (it.get("recall@5") or 0) < 1.0)
        lines.extend(
            _render_failures(
                "recall@5 未全中",
                fails,
                [
                    ("id", "id"),
                    ("expected", "expected_doc_ids"),
                    ("retrieved", "retrieved_doc_ids"),
                    ("recall@5", "recall@5"),
                    ("question", "question"),
                ],
            )
        )

    lines.extend(
        [
            "## 复现命令",
            "",
            "```powershell",
            ".venv\\Scripts\\python.exe evals\\run_safety_eval.py",
            ".venv\\Scripts\\python.exe evals\\run_routing_eval.py --offline",
            ".venv\\Scripts\\python.exe evals\\run_rag_eval.py --compare",
            ".venv\\Scripts\\python.exe evals\\report.py",
            "```",
            "",
            "真实 LLM 路由（需父目录 `config.py` 中的 API key，不要把 key 写进仓库）:",
            "",
            "```powershell",
            ".venv\\Scripts\\python.exe evals\\run_routing_eval.py",
            "```",
            "",
        ]
    )
    return "\n".join(lines) + "\n"


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="汇总最新评测 JSON 为 markdown 报告")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="输出路径（默认 evals/results/{YYYYMMDD}_report.md）",
    )
    parser.add_argument(
        "--sample",
        action="store_true",
        help="额外写一份 evals/results/SAMPLE_offline_report.md（供提交样例）",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    directory = results_dir()
    payloads: Dict[str, Tuple[Path, Dict[str, Any]]] = {}
    for prefix in PREFIXES:
        path = _latest_json(prefix, directory)
        if path is None:
            print(f"警告: 未找到 {prefix}_*.json")
            continue
        payloads[prefix] = (path, _load(path))

    md = build_markdown(payloads)
    out = args.out or (directory / f"{datetime.now().strftime('%Y%m%d')}_report.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print(f"报告已写入: {out}")

    if args.sample:
        sample = directory / "SAMPLE_offline_report.md"
        sample.write_text(md, encoding="utf-8")
        print(f"样例报告已写入: {sample}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
