"""
路由评测：只跑 LeadAgent.assess_and_decompose，不跑完整 Swarm。

默认走真实 LLM（并发 4，失败重试 1 次）。
无 API key 时用 --offline：按 Lead 提示词策略做规则启发式分解（不打付费 API）。

PowerShell（仓库根）:
  .venv\\Scripts\\python.exe evals\\run_routing_eval.py --offline
  .venv\\Scripts\\python.exe evals\\run_routing_eval.py
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set

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

VALID_AGENTS = ("consultation_agent", "diagnostic_agent", "research_agent")

# 对齐 lead_agent 系统提示中的策略关键词（启发式，不是读 golden 标签）
_GUIDELINE_KW = (
    "指南",
    "诊疗规范",
    "标准治疗",
    "防治指南",
    "临床诊疗",
    "按指南",
    "指南推荐",
    "指南怎么",
    "一线降糖",
    "规范治疗",
)
_TREATMENT_KW = ("如何治疗", "怎么治疗", "该吃什么药", "治疗方案")
_LIFESTYLE_KW = ("生活", "饮食", "注意什么", "日常生活", "日常建议", "给出日常")
_COMPLEX_KW = (
    "需要就医",
    "严重吗",
    "越来越",
    "加重",
    "该怎么评估",
    "可能是什么问题",
    "该怎么处理",
)
_DURATION_KW = ("一周", "两周", "三天", "一个月", "反复")
_SYMPTOM_KW = (
    "头痛", "胸闷", "气短", "咳嗽", "腹痛", "胃痛", "头晕", "恶心",
    "心慌", "手抖", "发热", "皮疹", "水肿", "黑便", "低烧", "视力模糊",
    "冒冷汗", "憋醒", "肿痛",
)


def heuristic_decompose(question: str) -> Dict[str, Any]:
    """
    离线启发式：复刻 LeadAgent 提示词里的三条策略，供无 API 时跑通 CI。
    不读取 expected_* 字段，因此准确率会低于真实 LLM，报告须标注 mode=offline_heuristic。
    """
    q = (question or "").strip()

    boundary_combo = (
        (("是什么" in q) or ("什么是" in q) or ("我感冒了" in q) or ("体检发现" in q))
        and any(k in q for k in ("吃什么药", "指南", "诊疗", "治疗比较标准", "生活上要注意"))
    )
    if boundary_combo or ("顺便问一下" in q and any(k in q for k in _GUIDELINE_KW)):
        agents = ["consultation_agent", "research_agent"]
        return _pack(agents, "offline:boundary")

    has_guideline = any(k in q for k in _GUIDELINE_KW) or any(k in q for k in _TREATMENT_KW)
    if has_guideline and not _looks_like_simple_cold_only(q):
        agents = ["research_agent"]
        wants_life = any(k in q for k in _LIFESTYLE_KW) or ("如何治疗" in q) or ("怎么治疗" in q) or ("怎么规范治疗" in q)
        # 提示词策略3示例：纯「最新诊疗指南是什么」→ 单 Research；「如何治疗」→ Research+Consultation
        if "最新诊疗指南是什么" in q or (q.endswith("指南是什么？") and "生活" not in q and "注意" not in q):
            wants_life = False
        if "标准治疗方案是什么" in q and "生活" not in q:
            wants_life = False
        if "诊疗规范是什么" in q and "生活" not in q:
            wants_life = False
        if wants_life and "consultation_agent" not in agents:
            agents.append("consultation_agent")
        return _pack(agents, "offline:guideline")

    has_complex = any(k in q for k in _COMPLEX_KW)
    if any(d in q for d in _DURATION_KW) and any(s in q for s in _SYMPTOM_KW):
        has_complex = True
    if ("既往" in q or "我有高血压" in q) and any(s in q for s in _SYMPTOM_KW):
        has_complex = True
    if q.count("还") >= 1 and sum(1 for s in _SYMPTOM_KW if s in q) >= 2:
        has_complex = True
    if "伴" in q and any(s in q for s in _SYMPTOM_KW):
        has_complex = True

    if has_complex:
        return _pack(["diagnostic_agent", "consultation_agent"], "offline:complex")

    return _pack(["consultation_agent"], "offline:simple")


def _looks_like_simple_cold_only(q: str) -> bool:
    """「普通感冒需要吃抗生素吗」这类常见病常识，不是指南检索。"""
    return ("普通感冒" in q and "抗生素" in q) or ("感冒了怎么办" in q and "指南" not in q)


def _pack(agents: Sequence[str], reason: str) -> Dict[str, Any]:
    return {
        "subtasks": [
            {"description": f"offline heuristic ({reason})", "assigned_agent": a}
            for a in agents
        ],
        "reason": reason,
    }


def _agents_from_subtasks(subtasks: Any) -> List[str]:
    if not isinstance(subtasks, list):
        return []
    agents: List[str] = []
    for st in subtasks:
        if not isinstance(st, dict):
            continue
        agent = st.get("assigned_agent")
        if isinstance(agent, str) and agent:
            agents.append(agent)
    return agents


def _score_item(
    item: Dict[str, Any],
    pred_agents: List[str],
    error: Optional[str],
    reason: str,
) -> Dict[str, Any]:
    expected_mode = item.get("expected_mode")
    expected_agents: Set[str] = set(item.get("expected_agents") or [])
    pred_set = set(pred_agents)
    n = len(pred_agents)

    if error or n == 0:
        pred_mode = None
        mode_ok = False
        exact = False
        partial = False
        verdict = "error"
    else:
        pred_mode = "single" if n == 1 else "swarm"
        mode_ok = pred_mode == expected_mode
        exact = pred_set == expected_agents
        partial = (not exact) and bool(pred_set & expected_agents)
        if mode_ok and exact:
            verdict = "ok"
        elif exact and not mode_ok:
            verdict = "mode_mismatch"
        elif partial:
            verdict = "partial"
        else:
            verdict = "mismatch"

    return {
        "id": item.get("id"),
        "question": item.get("question"),
        "bucket": item.get("bucket"),
        "note": item.get("note"),
        "expected_mode": expected_mode,
        "expected_agents": sorted(expected_agents),
        "pred_mode": pred_mode,
        "pred_agents": pred_agents,
        "mode_ok": mode_ok,
        "agent_exact": exact,
        "agent_partial": partial,
        "correct": mode_ok and exact,
        "verdict": verdict,
        "error": error,
        "reason": reason,
    }


async def _decompose_llm(lead: Any, question: str) -> Dict[str, Any]:
    last_exc: Optional[BaseException] = None
    for _attempt in range(2):
        try:
            result = await lead.assess_and_decompose(question)
            subtasks = (result or {}).get("subtasks") or []
            if subtasks:
                return result
            last_exc = RuntimeError((result or {}).get("reason") or "empty subtasks")
        except Exception as exc:
            last_exc = exc
    raise last_exc or RuntimeError("assess_and_decompose failed")


async def _eval_one(
    item: Dict[str, Any],
    lead: Any,
    offline: bool,
    sem: asyncio.Semaphore,
    index: int,
    total: int,
) -> Dict[str, Any]:
    async with sem:
        print_progress(index, total, str(item.get("id") or ""))
        question = str(item.get("question") or "")
        if offline:
            result = heuristic_decompose(question)
            agents = _agents_from_subtasks(result.get("subtasks"))
            return _score_item(item, agents, None, str(result.get("reason") or "offline"))
        try:
            result = await _decompose_llm(lead, question)
            agents = _agents_from_subtasks(result.get("subtasks"))
            return _score_item(item, agents, None, str((result or {}).get("reason") or "llm"))
        except Exception as exc:
            return _score_item(item, [], str(exc), "error")


def _summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(rows)
    n_error = sum(1 for r in rows if r["verdict"] == "error")
    scored = [r for r in rows if r["verdict"] != "error"]
    n_scored = len(scored) or 0
    mode_ok = sum(1 for r in scored if r["mode_ok"])
    exact = sum(1 for r in scored if r["agent_exact"])
    partial = sum(1 for r in scored if r["agent_partial"])
    full_ok = sum(1 for r in scored if r["correct"])
    return {
        "n": n,
        "n_scored": n_scored,
        "n_error": n_error,
        "mode_accuracy": pct(mode_ok, n_scored),
        "agent_exact_match_rate": pct(exact, n_scored),
        "agent_partial_match_rate": pct(partial, n_scored),
        "full_match_rate": pct(full_ok, n_scored),
        "fail_ids": [r["id"] for r in rows if not r["correct"]],
        "error_ids": [r["id"] for r in rows if r["verdict"] == "error"],
    }


async def run(dataset: Path, offline: bool, concurrency: int) -> Dict[str, Any]:
    items = load_jsonl(dataset)
    lead = None
    if not offline:
        from swarm.lead_agent import LeadAgent

        lead = LeadAgent()

    sem = asyncio.Semaphore(max(1, concurrency))
    total = len(items)
    tasks = [
        _eval_one(item, lead, offline, sem, i, total)
        for i, item in enumerate(items, start=1)
    ]
    rows = list(await asyncio.gather(*tasks))
    rows.sort(key=lambda r: str(r.get("id") or ""))
    metrics = _summarize(rows)
    return {
        "name": "routing",
        "mode": "offline_heuristic" if offline else "llm",
        "dataset": str(dataset),
        "concurrency": concurrency if not offline else 1,
        "metrics": metrics,
        "items": rows,
        "note": (
            "offline_heuristic 按 LeadAgent 提示词策略做规则分解，不调用 LLM；"
            "正式数字请去掉 --offline 跑真实分解。"
            if offline
            else "只调用 LeadAgent.assess_and_decompose，不跑完整 Swarm。"
        ),
    }


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="路由评测（默认真实 LLM；--offline 启发式）")
    parser.add_argument("--dataset", type=Path, default=default_dataset("routing_golden.jsonl"))
    parser.add_argument(
        "--offline",
        action="store_true",
        help="不调用 LLM，用规则启发式分解（无 API key / CI 用）",
    )
    parser.add_argument("--concurrency", type=int, default=4, help="真实 LLM 并发（Semaphore）")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    payload = asyncio.run(run(args.dataset, args.offline, args.concurrency))
    out = save_result("routing", payload)
    metrics = payload["metrics"]
    print_metrics(
        f"路由评测（{payload['mode']}）",
        metrics,
        keys=[
            "n",
            "n_scored",
            "n_error",
            "mode_accuracy",
            "agent_exact_match_rate",
            "agent_partial_match_rate",
            "full_match_rate",
        ],
    )
    print(f"模式准确率: {fmt_pct(metrics['mode_accuracy'])}")
    print(f"Agent 完全匹配: {fmt_pct(metrics['agent_exact_match_rate'])}")
    print(f"Agent 部分匹配: {fmt_pct(metrics['agent_partial_match_rate'])}")
    if payload["mode"] == "offline_heuristic":
        print("说明: 本次为 --offline 启发式，不是真实 LLM 路由数字。")
    print(f"结果已写入: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
