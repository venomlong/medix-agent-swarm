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

    def test_extract_keeps_markdown_table_after_separator(self):
        table = (
            "| 项目 | 信息 |\n"
            "| ------ | --- |\n"
            "| 姓名 | 文龙 |\n"
            "| 年龄 | 24岁 |\n"
            "| 爱好 | 羽毛球 |\n"
        )
        text = (
            "# Session Summary: 20260819-092757-6354f225\n\n"
            "## 问题\n我的年龄是24岁\n\n"
            "## 最终答案\n【回答】\n"
            "好的，文龙！\n"
            "**您的健康档案（更新）**\n"
            f"{table}\n"
            "后面还有说明\n\n"
            "---\n\n"
            "【核心建议】\n1. 测血压\n\n"
            "## 性能指标\n- 总耗时：18.50 秒\n"
        )
        sections = _extract_summary_sections(text)
        answer = sections["最终答案"]
        self.assertIn("| ------ | --- |", answer)
        self.assertIn("| 姓名 | 文龙 |", answer)
        self.assertIn("| 年龄 | 24岁 |", answer)
        self.assertIn("后面还有说明", answer)
        self.assertIn("【核心建议】", answer)
        self.assertNotIn("总耗时", answer)
        parsed = _parse_summary_markdown("20260819-092757-6354f225", text, 0)
        self.assertIn("| 姓名 | 文龙 |", parsed["summary"] + answer)

    def test_extract_ignores_unknown_heading_inside_answer(self):
        text = (
            "## 最终答案\n"
            "开头\n"
            "## 注意事项\n"
            "| 项目 | 信息 |\n"
            "| --- | --- |\n"
            "| 年龄 | 24岁 |\n\n"
            "## 性能指标\n- 总耗时：1 秒\n"
        )
        sections = _extract_summary_sections(text)
        self.assertIn("| 年龄 | 24岁 |", sections["最终答案"])
        self.assertIn("## 注意事项", sections["最终答案"])


TABLE_ANSWER = (
    "【回答】\n\n"
    "好的，文龙！我记住了，**您的年龄是24岁**。"
    "结合您之前询问过的\"高血压\"问题，以及您喜欢打羽毛球、对青霉素过敏这些信息，"
    "我可以为您综合梳理一下：\n\n"
    "**您的健康档案（更新）**\n"
    "| 项目 | 信息 |\n"
    "|------|------|\n"
    "| 姓名 | 文龙 |\n"
    "| 年龄 | 24岁 |\n"
    "| 爱好 | 羽毛球 |\n"
    "| 过敏史 | 青霉素过敏 |\n\n"
    "**针对24岁年轻人的健康提醒：**\n\n"
    "1. **关于高血压**：请多次测量血压确认。\n"
)


class ShortTermDetailFallbackTests(unittest.TestCase):
    def setUp(self):
        from memory.short_term import ShortTermMemory

        ShortTermMemory._instance = None
        self.ShortTermMemory = ShortTermMemory

    def tearDown(self):
        self.ShortTermMemory._instance = None

    def test_process_preview_cuts_table_but_detail_keeps_rows(self):
        preview = TABLE_ANSWER[:120]
        self.assertIn("|------|", preview)
        self.assertNotIn("| 姓名 | 文龙 |", preview)

        sid = "20260819-092757-6354f225"
        stm = self.ShortTermMemory(storage_type="memory")
        stm.add_message(sid, "user", "我的年龄是24岁")
        stm.add_message(sid, "assistant", TABLE_ANSWER)
        coord = type("Coord", (), {"short_term_memory": stm})()
        with tempfile.TemporaryDirectory() as tmp:
            mgr = SessionSummaryManager(base_dir=tmp)
            with patch("memory.session_summary.SessionSummaryManager", return_value=mgr):
                detail = get_session_detail(sid, coordinator=coord)
        self.assertIsNotNone(detail)
        assert detail is not None
        self.assertEqual(detail["source"], "short_term")
        self.assertIn("| 姓名 | 文龙 |", detail["final_answer"])
        self.assertIn("| 年龄 | 24岁 |", detail["sections"]["最终答案"])
        self.assertIn("| 过敏史 | 青霉素过敏 |", detail["markdown"])
        self.assertGreater(len(detail["final_answer"]), 120)

    def test_single_agent_summary_writes_full_table(self):
        summary = SessionSummary.from_single_agent(
            session_id="20260819-092800-single01",
            question="我的年龄是24岁",
            final_answer=TABLE_ANSWER,
            agent_id="consultation_agent",
            start_time=datetime(2026, 8, 19, 9, 29, 0),
            end_time=datetime(2026, 8, 19, 9, 29, 18),
        )
        md = summary.to_markdown()
        self.assertIn("| 姓名 | 文龙 |", md)
        sections = _extract_summary_sections(md)
        self.assertIn("| 年龄 | 24岁 |", sections["最终答案"])


if __name__ == "__main__":
    unittest.main()
