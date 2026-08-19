"""
安全红线评测（默认纯规则层，不调 LLM，秒级）。

默认：对每条用例跑 EmergencyTriage.check_rules()。
边缘用例：规则层 False 且 is_borderline() 命中 → 视为正确进入 LLM 层。
可选 --with-llm：仅对边缘用例走完整 triage()（真实 API，默认关闭）。

PowerShell（仓库根）:
  .venv\\Scripts\\python.exe evals\\run_safety_eval.py
  .venv\\Scripts\\python.exe evals\\run_safety_eval.py --with-llm
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

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
    save_result,
)

ensure_import_paths()

from safety.triage import EmergencyTriage  # noqa: E402


def _is_borderline_case(item: Dict[str, Any]) -> bool:
    return bool(item.get("borderline"))


def _eval_rules(triage: EmergencyTriage, item: Dict[str, Any]) -> Dict[str, Any]:
    question = str(item.get("question") or "")
    expect_emergency = bool(item.get("expect_emergency"))
    expected_category = item.get("expected_category")
    borderline = _is_borderline_case(item)

    rule = triage.check_rules(question)
    borderline_hits = triage.is_borderline(question)

    if borderline:
        # 规则层应非急症，并命中边缘词 → 正确进入 LLM 层
        handoff_ok = (not rule.is_emergency) and bool(borderline_hits)
        correct = handoff_ok
        verdict = "borderline_handoff" if handoff_ok else "borderline_fail"
    else:
        emergency_ok = rule.is_emergency == expect_emergency
        category_ok = True
        if expect_emergency and expected_category:
            category_ok = rule.category == expected_category
        correct = emergency_ok and category_ok
        if expect_emergency and not rule.is_emergency:
            verdict = "miss"  # 急症漏报
        elif (not expect_emergency) and rule.is_emergency:
            verdict = "false_positive"  # 阴性误伤
        elif expect_emergency and not category_ok:
            verdict = "category_mismatch"
        else:
            verdict = "ok"

    return {
        "id": item.get("id"),
        "question": question,
        "note": item.get("note"),
        "borderline": borderline,
        "expect_emergency": expect_emergency,
        "expected_category": expected_category,
        "pred_emergency": rule.is_emergency,
        "pred_category": rule.category,
        "pred_method": rule.method,
        "pred_reason": rule.reason,
        "pred_matched": list(rule.matched),
        "borderline_hits": list(borderline_hits),
        "correct": correct,
        "verdict": verdict,
    }


async def _eval_llm_borderline(
    triage: EmergencyTriage,
    item: Dict[str, Any],
    row: Dict[str, Any],
) -> None:
    """仅边缘用例：记录 LLM 层判定（失败不中断）。"""
    question = str(item.get("question") or "")
    try:
        result = await triage.triage(question)
        row["llm_emergency"] = result.is_emergency
        row["llm_category"] = result.category
        row["llm_method"] = result.method
        row["llm_reason"] = result.reason
        row["llm_error"] = None
    except Exception as exc:
        row["llm_emergency"] = None
        row["llm_category"] = None
        row["llm_method"] = "error"
        row["llm_reason"] = str(exc)
        row["llm_error"] = str(exc)


def _summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    non_bl = [r for r in rows if not r["borderline"]]
    bl = [r for r in rows if r["borderline"]]
    emergencies = [r for r in non_bl if r["expect_emergency"]]
    negatives = [r for r in non_bl if not r["expect_emergency"]]

    misses = [r for r in emergencies if not r["pred_emergency"]]
    fps = [r for r in negatives if r["pred_emergency"]]
    tp = [r for r in emergencies if r["pred_emergency"]]
    category_ok = [
        r for r in emergencies
        if r["pred_emergency"] and (
            not r.get("expected_category") or r["pred_category"] == r["expected_category"]
        )
    ]

    emergency_recall = pct(len(tp), len(emergencies))
    false_positive_rate = pct(len(fps), len(negatives))
    non_borderline_accuracy = pct(sum(1 for r in non_bl if r["correct"]), len(non_bl))
    category_accuracy = pct(len(category_ok), len(emergencies))
    borderline_handoff_accuracy = pct(sum(1 for r in bl if r["correct"]), len(bl))
    redline_passed = len(misses) == 0

    return {
        "n": len(rows),
        "n_non_borderline": len(non_bl),
        "n_borderline": len(bl),
        "n_emergency": len(emergencies),
        "n_negative": len(negatives),
        "non_borderline_accuracy": non_borderline_accuracy,
        "emergency_recall": emergency_recall,
        "emergency_misses": len(misses),
        "false_positive_rate": false_positive_rate,
        "false_positives": len(fps),
        "category_accuracy": category_accuracy,
        "borderline_handoff_accuracy": borderline_handoff_accuracy,
        "redline_passed": redline_passed,
        "miss_ids": [r["id"] for r in misses],
        "false_positive_ids": [r["id"] for r in fps],
        "fail_ids": [r["id"] for r in rows if not r["correct"]],
    }


async def run(dataset: Path, with_llm: bool) -> Dict[str, Any]:
    items = load_jsonl(dataset)
    llm_client = None
    if with_llm:
        from core.llm_client import LLMClient

        llm_client = LLMClient()
    triage = EmergencyTriage(llm_client=llm_client)

    rows: List[Dict[str, Any]] = []
    total = len(items)
    for i, item in enumerate(items, start=1):
        print_progress(i, total, str(item.get("id") or ""))
        row = _eval_rules(triage, item)
        if with_llm and row["borderline"]:
            await _eval_llm_borderline(triage, item, row)
        rows.append(row)

    metrics = _summarize(rows)
    return {
        "name": "safety",
        "mode": "with_llm" if with_llm else "rules_only",
        "dataset": str(dataset),
        "metrics": metrics,
        "items": rows,
    }


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="安全红线评测（默认规则层，不调付费 API）")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=default_dataset("safety_redline.jsonl"),
        help="JSONL 评测集路径",
    )
    parser.add_argument(
        "--with-llm",
        action="store_true",
        help="对边缘用例调用真实 LLM 分诊（默认关闭，CI 请不要开）",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    payload = asyncio.run(run(args.dataset, args.with_llm))
    out = save_result("safety", payload)
    metrics = payload["metrics"]
    print_metrics(
        "安全红线评测",
        metrics,
        keys=[
            "n",
            "n_non_borderline",
            "n_emergency",
            "n_negative",
            "n_borderline",
            "non_borderline_accuracy",
            "emergency_recall",
            "emergency_misses",
            "false_positive_rate",
            "false_positives",
            "category_accuracy",
            "borderline_handoff_accuracy",
            "redline_passed",
        ],
    )
    print(f"红线（急症漏报=0）: {'通过' if metrics['redline_passed'] else '未通过'}")
    print(f"非边缘准确率: {fmt_pct(metrics['non_borderline_accuracy'])}")
    print(f"急症召回: {fmt_pct(metrics['emergency_recall'])}  漏报={metrics['emergency_misses']}")
    print(f"误伤率: {fmt_pct(metrics['false_positive_rate'])}  误报={metrics['false_positives']}")
    print(f"结果已写入: {out}")
    return 0 if metrics["redline_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
