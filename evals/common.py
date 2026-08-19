"""
评测脚本公共工具：仓库根定位、JSONL 加载、结果落盘、进度打印。

路径一律 pathlib，不依赖当前工作目录，因此
`python evals/run_*.py` 与 `cd evals; python run_*.py` 均可。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


def evals_dir() -> Path:
    return Path(__file__).resolve().parent


def repo_root() -> Path:
    return evals_dir().parent


def ensure_import_paths() -> Path:
    """把仓库根加入 sys.path，以便 import safety / swarm / knowledge。"""
    root = repo_root()
    root_s = str(root)
    if root_s not in sys.path:
        sys.path.insert(0, root_s)
    return root


def datasets_dir() -> Path:
    return evals_dir() / "datasets"


def results_dir() -> Path:
    path = evals_dir() / "results"
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    """加载 UTF-8 JSONL；跳过空行。"""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"评测集不存在: {path}")
    items: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, raw in enumerate(f, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path} 第 {line_no} 行不是合法 JSON: {exc}") from exc
            if not isinstance(obj, dict):
                raise ValueError(f"{path} 第 {line_no} 行不是 JSON 对象")
            items.append(obj)
    return items


def default_dataset(name: str) -> Path:
    return datasets_dir() / name


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def iso_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def save_result(name: str, payload: Dict[str, Any]) -> Path:
    """写入 evals/results/{name}_{YYYYMMDD_HHMMSS}.json，返回路径。"""
    out = results_dir() / f"{name}_{now_stamp()}.json"
    payload = dict(payload)
    payload.setdefault("name", name)
    payload.setdefault("generated_at", iso_now())
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def print_progress(index: int, total: int, extra: str = "") -> None:
    suffix = f" {extra}" if extra else ""
    print(f"[{index}/{total}]{suffix}", flush=True)


def pct(numer: float, denom: float) -> float:
    if denom <= 0:
        return 0.0
    return numer / denom


def fmt_pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def print_metrics(title: str, metrics: Dict[str, Any], keys: Optional[Iterable[str]] = None) -> None:
    print()
    print(f"=== {title} ===")
    items = keys if keys is not None else metrics.keys()
    for key in items:
        if key not in metrics:
            continue
        val = metrics[key]
        if isinstance(val, float) and 0.0 <= val <= 1.0 and not key.endswith("_count"):
            print(f"  {key}: {fmt_pct(val)} ({val:.4f})")
        else:
            print(f"  {key}: {val}")
    print()
