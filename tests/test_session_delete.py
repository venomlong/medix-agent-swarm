import tempfile
import unittest
from collections import deque
from unittest.mock import patch

from memory.session_summary import SessionSummaryManager
from memory.short_term import ShortTermMemory
from webapi.runtime import RuntimeStats


class SessionSummaryDeleteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mgr = SessionSummaryManager(base_dir=self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, session_id: str, content: str = "# Session Summary\n"):
        path = self.mgr._get_summary_path(session_id)
        path.write_text(content, encoding="utf-8")
        return path

    def test_delete_existing_file(self):
        sid = "20260818-120000-abcd1234"
        path = self._write(sid)
        self.assertTrue(path.exists())
        self.assertTrue(self.mgr.delete_summary(sid))
        self.assertFalse(path.exists())

    def test_delete_missing_is_idempotent(self):
        self.assertFalse(self.mgr.delete_summary("20260818-120000-missing1"))

    def test_delete_does_not_touch_other_session(self):
        a = "20260818-120000-aaaa1111"
        b = "20260818-120000-bbbb2222"
        pa = self._write(a, "a")
        pb = self._write(b, "b")
        self.assertTrue(self.mgr.delete_summary(a))
        self.assertFalse(pa.exists())
        self.assertTrue(pb.exists())
        self.assertEqual(pb.read_text(encoding="utf-8"), "b")


class RuntimeStatsDropSessionTests(unittest.TestCase):
    def test_drop_session_removes_matching_rows(self):
        stats = RuntimeStats()
        stats.recent = deque(
            [
                {"id": "keep", "question": "k"},
                {"id": "gone", "question": "g1"},
                {"id": "gone", "question": "g2"},
            ],
            maxlen=40,
        )
        self.assertTrue(stats.drop_session("gone"))
        self.assertEqual([item["id"] for item in stats.recent], ["keep"])

    def test_drop_missing_is_idempotent(self):
        stats = RuntimeStats()
        stats.recent.appendleft({"id": "keep", "question": "k"})
        self.assertFalse(stats.drop_session("missing"))
        self.assertEqual(len(stats.recent), 1)


class DeleteSessionDataTests(unittest.TestCase):
    def setUp(self):
        ShortTermMemory._instance = None
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        ShortTermMemory._instance = None
        self.tmp.cleanup()

    def test_clears_short_term_and_summary_without_mem0(self):
        from webapi.reads import delete_session_data

        sid = "20260818-120000-abcd1234"
        stm = ShortTermMemory(storage_type="memory")
        stm.add_message(sid, "user", "hello")
        stm.add_message("other", "user", "keep me")

        mgr = SessionSummaryManager(base_dir=self.tmp.name)
        path = mgr._get_summary_path(sid)
        path.write_text("# Session Summary\n", encoding="utf-8")

        class _Coord:
            short_term_memory = stm

        with patch("memory.session_summary.SessionSummaryManager", return_value=mgr):
            result = delete_session_data(sid, coordinator=_Coord())

        self.assertTrue(result["ok"])
        self.assertEqual(result["mem0"], "not_deleted")
        self.assertTrue(result["cleared"]["short_term"])
        self.assertTrue(result["cleared"]["session_summary"])
        self.assertIsNone(stm.get_session(sid))
        self.assertIsNotNone(stm.get_session("other"))
        self.assertFalse(path.exists())

    def test_missing_session_still_ok(self):
        from webapi.reads import delete_session_data

        stm = ShortTermMemory(storage_type="memory")

        class _Coord:
            short_term_memory = stm

        mgr = SessionSummaryManager(base_dir=self.tmp.name)
        with patch("memory.session_summary.SessionSummaryManager", return_value=mgr):
            result = delete_session_data("20260818-120000-missing1", coordinator=_Coord())

        self.assertTrue(result["ok"])
        self.assertTrue(result["cleared"]["short_term"])
        self.assertFalse(result["cleared"]["session_summary"])
        self.assertEqual(result["mem0"], "not_deleted")


if __name__ == "__main__":
    unittest.main()
