"""进程内运行统计：无聚合库时给仪表盘用的最小真实计数。"""
from __future__ import annotations

from collections import deque
from datetime import datetime
from typing import Any, Dict, List, Optional


class RuntimeStats:
    def __init__(self) -> None:
        self.started_at = datetime.now()
        self.chat_count = 0
        self.swarm_count = 0
        self.single_count = 0
        self.error_count = 0
        self.timeout_count = 0
        self.elapsed_sum = 0.0
        self.swarm_elapsed_sum = 0.0
        self.single_elapsed_sum = 0.0
        self.recent: deque = deque(maxlen=40)

    def record_chat(
        self,
        *,
        session_id: str,
        question: str,
        swarm_enabled: bool,
        elapsed_s: float,
        timed_out: bool,
        error: bool,
        agent_count: int,
        summary: str = "",
    ) -> None:
        self.chat_count += 1
        self.elapsed_sum += elapsed_s
        if error:
            self.error_count += 1
        if timed_out:
            self.timeout_count += 1
        if swarm_enabled:
            self.swarm_count += 1
            self.swarm_elapsed_sum += elapsed_s
            mode = "Swarm"
        else:
            self.single_count += 1
            self.single_elapsed_sum += elapsed_s
            mode = "单 Agent"
        q = (question or "").replace("\n", " ").strip()
        self.recent.appendleft(
            {
                "id": session_id,
                "time": datetime.now().strftime("%m-%d %H:%M"),
                "question": q[:80] + ("…" if len(q) > 80 else ""),
                "mode": mode,
                "elapsed": f"{elapsed_s:.1f}s",
                "elapsed_s": round(elapsed_s, 2),
                "summary": (summary or q)[:120],
                "agent_count": agent_count,
                "source": "process",
            }
        )

    def snapshot(self, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        chats = self.chat_count
        swarm_share = round(100.0 * self.swarm_count / chats) if chats else 0
        avg = self.elapsed_sum / chats if chats else 0.0
        swarm_avg = (
            self.swarm_elapsed_sum / self.swarm_count if self.swarm_count else 0.0
        )
        single_avg = (
            self.single_elapsed_sum / self.single_count if self.single_count else 0.0
        )
        payload: Dict[str, Any] = {
            "scope": "current_process",
            "label": "本次服务启动后",
            "started_at": self.started_at.isoformat(timespec="seconds"),
            "uptime_s": round((datetime.now() - self.started_at).total_seconds(), 1),
            "chat_count": chats,
            "swarm_count": self.swarm_count,
            "single_count": self.single_count,
            "error_count": self.error_count,
            "timeout_count": self.timeout_count,
            "swarm_share": swarm_share,
            "avg_latency": f"{avg:.1f}s" if chats else "—",
            "swarm_latency": f"{swarm_avg:.1f}s" if self.swarm_count else "—",
            "single_latency": f"{single_avg:.1f}s" if self.single_count else "—",
        }
        if extra:
            payload.update(extra)
        return payload

    def recent_sessions(self) -> List[Dict[str, Any]]:
        return list(self.recent)

    def drop_session(self, session_id: str) -> bool:
        """从进程内最近会话里去掉该 id（可有多条）。不存在则 False。"""
        sid = (session_id or "").strip()
        if not sid:
            return False
        kept = [item for item in self.recent if item.get("id") != sid]
        dropped = len(kept) < len(self.recent)
        if dropped:
            self.recent = deque(kept, maxlen=40)
        return dropped


STATS = RuntimeStats()
