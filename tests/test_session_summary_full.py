"""SessionSummary 落盘写全文；详情 API 不截断 markdown。"""
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

from memory.session_summary import (
    AgentParticipation,
    PerformanceMetrics,
    SessionSummary,
    SessionSummaryManager,
    _extract_summary_sections,
    _parse_summary_markdown,
)
from webapi.reads import get_session_detail


def _summary(final_answer: str, question: str = "问什么？") -> SessionSummary:
    return SessionSummary(
        session_id="20260818-210000-fullansw",
        question=question,
        context={},
        timestamp=datetime(2026, 8, 18, 21, 0, 0),
        agents_participated=[
            AgentParticipation(
                agent_id="consultation_agent",
                role="worker",
                subtasks_handled=["t1"],
                tool_calls=1,
                execution_time=1.0,
            )
        ],
        subtasks_created=1,
        subtasks_completed=1,
        events_count=1,
        final_answer=final_answer,
        key_findings=[],
        lessons_learned=[],
        performance=PerformanceMetrics(
            total_time=1.0,
            agent_count=1,
            parallel_efficiency=0.8,
            information_coverage=0.9,
            redundancy=0.1,
        ),
    )


class SessionSummaryFullAnswerTests(unittest.TestCase):
    def test_to_markdown_keeps_full_final_answer(self):
        answer = "这段最终答案需要超过五百字。" * 40
        self.assertGreater(len(answer), 500)
        md = _summary(answer).to_markdown()
        section = md.split("## 最终答案", 1)[1].split("##", 1)[0]
        self.assertIn(answer, section)
        self.assertGreaterEqual(len(section.strip()), len(answer))

    def test_save_and_detail_roundtrip_full_markdown(self):
        answer = "完整回答正文。" * 80
        self.assertGreater(len(answer), 500)
        summary = _summary(answer, question="多行问题\n第二行")
        with tempfile.TemporaryDirectory() as tmp:
            mgr = SessionSummaryManager(base_dir=tmp)
            mgr.save_summary(summary)
            disk = mgr.read_markdown(summary.session_id)
            self.assertIsNotNone(disk)
            self.assertIn(answer, disk or "")
            with patch("memory.session_summary.SessionSummaryManager", return_value=mgr):
                detail = get_session_detail(summary.session_id)
            self.assertIsNotNone(detail)
            assert detail is not None
            self.assertEqual(detail["markdown"], disk)
            self.assertEqual(detail["sections"]["最终答案"].strip(), answer)
            self.assertGreaterEqual(len(detail["final_answer"]), len(answer))
            listed = _parse_summary_markdown(summary.session_id, disk or "", 0)
            self.assertLessEqual(len(listed["summary"].rstrip("…")), 120)

    def test_extract_sections_keeps_full_bodies(self):
        text = (
            "# Session Summary: x\n\n"
            "## 问题\n第一行\n第二行\n\n"
            "## 最终答案\n" + ("答案" * 200) + "\n\n"
            "## 性能指标\n- 总耗时：1 秒\n"
        )
        sections = _extract_summary_sections(text)
        self.assertIn("第二行", sections["问题"])
        self.assertEqual(len(sections["最终答案"]), 400)


if __name__ == "__main__":
    unittest.main()
