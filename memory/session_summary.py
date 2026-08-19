"""
SessionSummary：会话总结和经验提取

每次 Swarm 协作后自动生成会话总结，记录：
- 问题和背景
- 参与的 Agent
- 协作过程
- 关键发现
- 经验教训
- 性能指标

这是群体智能"持续学习"的关键机制
"""
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
import json
import re
from loguru import logger

# 只按 SessionSummary 自身的 ## 标题分节。答案里的 ---、表格分隔行 | --- |、
# 以及模型输出的其它 ## 标题都不能当 section 边界，否则会吃掉表体。
KNOWN_SECTION_HEADINGS: Tuple[str, ...] = (
    "问题",
    "背景",
    "参与 Agent",
    "协作过程",
    "关键发现",
    "最终答案",
    "经验教训",
    "性能指标",
)
_HEADING_RE = re.compile(
    r"^## (" + "|".join(re.escape(h) for h in KNOWN_SECTION_HEADINGS) + r")\s*$"
)


def _extract_summary_sections(text: str) -> Dict[str, str]:
    """按已知 ## 标题拆出全文段落，不截断，不把 HR / 表格线当边界。"""
    sections: Dict[str, str] = {}
    current: Optional[str] = None
    buf: List[str] = []
    for line in (text or "").splitlines(keepends=True):
        m = _HEADING_RE.match(line.rstrip("\r\n"))
        if m:
            if current is not None:
                sections[current] = "".join(buf).strip()
            current = m.group(1)
            buf = []
            continue
        if current is not None:
            buf.append(line)
    if current is not None:
        sections[current] = "".join(buf).strip()
    return sections


def _section(text: str, heading: str) -> str:
    return _extract_summary_sections(text).get(heading, "")


def _parse_summary_markdown(session_id: str, text: str, mtime: float) -> Dict[str, Any]:
    time_m = re.search(r"\*\*时间\*\*:\s*(.+)", text)
    time_raw = (time_m.group(1).strip() if time_m else "")
    time_label = time_raw
    try:
        dt = datetime.strptime(time_raw, "%Y-%m-%d %H:%M:%S")
        time_label = dt.strftime("%m-%d %H:%M")
    except ValueError:
        if mtime:
            time_label = datetime.fromtimestamp(mtime).strftime("%m-%d %H:%M")

    question = _section(text, "问题").split("\n", 1)[0].strip()
    answer = _section(text, "最终答案")
    elapsed_m = re.search(r"总耗时：([\d.]+)\s*秒", text)
    agents_m = re.search(r"参与 Agent：(\d+)", text)
    subtasks_m = re.search(r"创建子任务：(\d+)", text)
    elapsed_s = float(elapsed_m.group(1)) if elapsed_m else None
    agent_names = re.findall(r"^###\s+([^\s(]+)", _section(text, "参与 Agent"), flags=re.M)
    agent_count = int(agents_m.group(1)) if agents_m else len(agent_names)
    subtasks = int(subtasks_m.group(1)) if subtasks_m else 0
    mode = "Swarm" if subtasks >= 2 or agent_count >= 2 else "单 Agent"
    summary = re.sub(r"\s+", " ", answer)[:120].strip()
    if len(answer) > 120:
        summary += "…"
    if not summary:
        summary = "（无最终答案摘要）"

    return {
        "id": session_id,
        "time": time_label,
        "question": question or session_id,
        "mode": mode,
        "elapsed": f"{elapsed_s:.1f}s" if elapsed_s is not None else "—",
        "elapsed_s": elapsed_s,
        "summary": summary,
        "agent_count": agent_count,
        "agents": agent_names,
        "subtasks_created": subtasks,
    }


@dataclass
class AgentParticipation:
    """Agent 参与记录"""
    agent_id: str
    role: str  # lead/worker
    subtasks_handled: List[str]
    tool_calls: int
    execution_time: float  # 秒
    contribution_quality: float = 1.0  # 0-1


@dataclass
class KeyFinding:
    """关键发现"""
    category: str  # diagnosis/risk/evidence/treatment
    finding: str
    source_agent: str
    confidence: float = 1.0


@dataclass
class Lesson:
    """经验教训"""
    agent_id: str
    lesson_type: str  # success/failure/improvement
    description: str
    actionable: str  # 可执行的改进措施


@dataclass
class PerformanceMetrics:
    """性能指标"""
    total_time: float  # 总耗时（秒）
    agent_count: int  # 参与 Agent 数量
    parallel_efficiency: float  # 并行效率（0-1）
    information_coverage: float  # 信息覆盖度（0-1）
    redundancy: float  # 信息冗余度（0-1）
    speedup_vs_single: float = 1.0  # 相比单 Agent 的加速比


@dataclass
class SessionSummary:
    """
    会话总结数据类

    记录一次完整的 Swarm 协作过程
    """
    session_id: str
    question: str
    context: Dict[str, Any]
    timestamp: datetime

    # 参与者
    agents_participated: List[AgentParticipation]

    # 过程
    subtasks_created: int
    subtasks_completed: int
    events_count: int

    # 结果
    final_answer: str
    key_findings: List[KeyFinding]

    # 学习
    lessons_learned: List[Lesson]

    # 性能
    performance: PerformanceMetrics

    # 元数据
    swarm_enabled: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_markdown(self) -> str:
        """转换为 Markdown 格式"""
        date_str = self.timestamp.strftime("%Y-%m-%d %H:%M:%S")

        lines = [
            f"# Session Summary: {self.session_id}",
            "",
            f"**时间**: {date_str}",
            "",
            "## 问题",
            self.question,
            ""
        ]

        if self.context:
            lines.extend([
                "## 背景",
                "```json",
                json.dumps(self.context, ensure_ascii=False, indent=2),
                "```",
                ""
            ])

        lines.extend([
            "## 参与 Agent",
            ""
        ])

        for agent in self.agents_participated:
            lines.append(f"### {agent.agent_id} ({agent.role})")
            lines.append(f"- 处理子任务：{len(agent.subtasks_handled)} 个")
            lines.append(f"- 工具调用：{agent.tool_calls} 次")
            lines.append(f"- 执行时间：{agent.execution_time:.2f} 秒")
            lines.append("")

        lines.extend([
            "## 协作过程",
            "",
            f"- 创建子任务：{self.subtasks_created} 个",
            f"- 完成子任务：{self.subtasks_completed} 个",
            f"- 发布事件：{self.events_count} 个",
            ""
        ])

        if self.key_findings:
            lines.extend([
                "## 关键发现",
                ""
            ])

            for finding in self.key_findings:
                lines.append(f"### {finding.category.upper()}")
                lines.append(f"**来源**: {finding.source_agent}")
                lines.append(f"**发现**: {finding.finding}")
                lines.append(f"**置信度**: {finding.confidence:.1%}")
                lines.append("")

        lines.extend([
            "## 最终答案",
            "",
            self.final_answer or "",
            ""
        ])

        if self.lessons_learned:
            lines.extend([
                "## 经验教训",
                ""
            ])

            for lesson in self.lessons_learned:
                emoji = "✅" if lesson.lesson_type == "success" else "⚠️" if lesson.lesson_type == "failure" else "💡"
                lines.append(f"### {emoji} {lesson.agent_id}")
                lines.append(f"**{lesson.lesson_type.upper()}**: {lesson.description}")
                if lesson.actionable:
                    lines.append(f"**改进措施**: {lesson.actionable}")
                lines.append("")

        lines.extend([
            "## 性能指标",
            "",
            f"- 总耗时：{self.performance.total_time:.2f} 秒",
            f"- 参与 Agent：{self.performance.agent_count} 个",
            f"- 并行效率：{self.performance.parallel_efficiency:.1%}",
            f"- 信息覆盖度：{self.performance.information_coverage:.1%}",
            f"- 信息冗余度：{self.performance.redundancy:.1%}",
            f"- 加速比：{self.performance.speedup_vs_single:.2f}x",
            ""
        ])

        return "\n".join(lines)

    @classmethod
    def from_shared_context(
        cls,
        session_id: str,
        question: str,
        shared_context: Any,
        final_answer: str,
        start_time: datetime,
        end_time: datetime
    ) -> "SessionSummary":
        """从 SharedContext 构建 SessionSummary"""

        # 计算性能指标
        total_time = (end_time - start_time).total_seconds()

        # 提取 Agent 参与信息
        agents_participated = []
        for agent_id, contributions in shared_context.agent_contributions.items():
            tool_calls = sum(
                1 for c in contributions
                if c.result.get('success', True)
            )
            agents_participated.append(AgentParticipation(
                agent_id=agent_id,
                role="worker",
                subtasks_handled=[c.subtask_id for c in contributions],
                tool_calls=tool_calls,
                execution_time=total_time / len(shared_context.agent_contributions)
            ))

        # 提取关键发现
        key_findings = []
        for contrib in shared_context.get_contributions():
            if "risk_level" in contrib.result:
                key_findings.append(KeyFinding(
                    category="risk",
                    finding=f"风险等级：{contrib.result['risk_level']}",
                    source_agent=contrib.agent_id,
                    confidence=contrib.confidence
                ))

        # 性能指标
        performance = PerformanceMetrics(
            total_time=total_time,
            agent_count=len(shared_context.agent_contributions),
            parallel_efficiency=0.8,  # TODO: 实际计算
            information_coverage=0.9,  # TODO: 实际计算
            redundancy=0.15  # TODO: 实际计算
        )

        return cls(
            session_id=session_id,
            question=question,
            context={},
            timestamp=start_time,
            agents_participated=agents_participated,
            subtasks_created=len(shared_context.task_decomposition),
            subtasks_completed=len(shared_context.get_all_completed_subtasks()),
            events_count=len(shared_context.events),
            final_answer=final_answer,
            key_findings=key_findings,
            lessons_learned=[],  # TODO: 从协作过程中提取
            performance=performance
        )

    @classmethod
    def from_single_agent(
        cls,
        session_id: str,
        question: str,
        final_answer: str,
        agent_id: str,
        start_time: datetime,
        end_time: datetime,
        tool_calls: int = 0,
        mode: str = "single_agent",
    ) -> "SessionSummary":
        """单 Agent / 降级路径也落一份完整 markdown，供记忆抽屉读取。"""
        total_time = (end_time - start_time).total_seconds()
        aid = (agent_id or "consultation_agent").strip() or "consultation_agent"
        return cls(
            session_id=session_id,
            question=question,
            context={},
            timestamp=start_time,
            agents_participated=[
                AgentParticipation(
                    agent_id=aid,
                    role="worker",
                    subtasks_handled=["single"],
                    tool_calls=tool_calls,
                    execution_time=total_time,
                )
            ],
            subtasks_created=1,
            subtasks_completed=1,
            events_count=0,
            final_answer=final_answer or "",
            key_findings=[],
            lessons_learned=[],
            performance=PerformanceMetrics(
                total_time=total_time,
                agent_count=1,
                parallel_efficiency=1.0,
                information_coverage=1.0,
                redundancy=0.0,
                speedup_vs_single=1.0,
            ),
            swarm_enabled=False,
            metadata={"mode": mode},
        )


class SessionSummaryManager:
    """
    会话总结管理器

    负责保存和检索会话总结
    """

    def __init__(self, base_dir: str = "memory/swarm/session_summaries"):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _get_summary_path(self, session_id: str) -> Path:
        """获取会话总结文件路径"""
        # 按日期组织
        date_str = session_id.split("-")[0] if "-" in session_id else "unknown"
        date_dir = self.base_dir / date_str
        date_dir.mkdir(parents=True, exist_ok=True)
        return date_dir / f"{session_id}.md"

    def save_summary(self, summary: SessionSummary):
        """保存会话总结"""
        summary_path = self._get_summary_path(summary.session_id)

        try:
            content = summary.to_markdown()
            summary_path.write_text(content, encoding="utf-8")
            logger.info(f"Saved session summary: {summary.session_id}")
        except Exception as e:
            logger.error(f"Error saving session summary: {e}")

    def load_summary(self, session_id: str) -> Optional[SessionSummary]:
        """加载会话总结（简化实现）"""
        summary_path = self._get_summary_path(session_id)

        if not summary_path.exists():
            return None

        try:
            # 这里可以实现从 Markdown 解析回 SessionSummary
            # 简化版直接返回 None
            return None
        except Exception as e:
            logger.error(f"Error loading session summary: {e}")
            return None

    def _resolve_path(self, session_id: str) -> Optional[Path]:
        """查找已有 markdown，不创建目录。"""
        expected = self.base_dir / (session_id.split("-")[0] if "-" in session_id else "unknown") / f"{session_id}.md"
        if expected.exists():
            return expected
        matches = list(self.base_dir.rglob(f"{session_id}.md"))
        return matches[0] if matches else None

    def delete_summary(self, session_id: str) -> bool:
        """按 session_id 删除 markdown。不存在返回 False，不抛 FileNotFoundError。"""
        sid = (session_id or "").strip()
        if not sid or "/" in sid or "\\" in sid or ".." in sid:
            return False
        path = self._resolve_path(sid)
        if path is None:
            return False
        try:
            path.unlink()
            parent = path.parent
            if parent != self.base_dir and parent.is_dir() and not any(parent.iterdir()):
                try:
                    parent.rmdir()
                except OSError:
                    pass
            logger.info(f"Deleted session summary: {sid}")
            return True
        except FileNotFoundError:
            return False
        except Exception as e:
            logger.error(f"Error deleting session summary: {e}")
            raise

    def read_markdown(self, session_id: str) -> Optional[str]:
        path = self._resolve_path(session_id)
        if path is None:
            return None
        try:
            return path.read_text(encoding="utf-8")
        except Exception as e:
            logger.error(f"Error reading session summary: {e}")
            return None

    def parse_markdown_file(self, path: Path) -> Optional[Dict[str, Any]]:
        """从已保存的 Markdown 抽出列表/详情所需字段（不假装有结构化库）。"""
        try:
            text = path.read_text(encoding="utf-8")
        except Exception as e:
            logger.error(f"Error parsing session summary {path}: {e}")
            return None
        return _parse_summary_markdown(path.stem, text, path.stat().st_mtime)

    def list_summaries(self, limit: int = 40) -> List[Dict[str, Any]]:
        if not self.base_dir.exists():
            return []
        files = [
            p
            for p in self.base_dir.rglob("*.md")
            if p.parent.name != "test" and not p.stem.startswith("test")
        ]
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        items: List[Dict[str, Any]] = []
        for path in files:
            parsed = self.parse_markdown_file(path)
            if parsed:
                items.append(parsed)
            if len(items) >= limit:
                break
        return items

    def search_similar_sessions(
        self,
        query: str,
        limit: int = 5
    ) -> List[Path]:
        """
        搜索相似的会话（简化实现）

        未来可以使用向量相似度搜索
        """
        # 简单实现：返回最近的会话
        all_summaries = list(self.base_dir.rglob("*.md"))
        all_summaries.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return all_summaries[:limit]
