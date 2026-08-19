"""急症 SSE 适配：EMERGENCY_TRIGGERED → routing(emergency) + 原事件帧。"""
import unittest

from swarm.events import Event, EventType
from webapi.bridge import attach_live_listener, map_answer_done


class EmergencyLiveListenerTests(unittest.TestCase):
    def test_emergency_triggered_sets_live_and_emits_routing(self):
        emitted = []
        flags = {"live": False}

        def emit(name, data):
            emitted.append((name, data))

        listener = attach_live_listener(emit, "sess-em", flags)
        listener(
            Event(
                type=EventType.EMERGENCY_TRIGGERED,
                source_agent="emergency_triage",
                data={
                    "is_emergency": True,
                    "category": "cardiac",
                    "matched": ["胸痛", "冒冷汗"],
                    "reason": "命中组合规则",
                    "method": "rule",
                },
            )
        )

        self.assertTrue(flags["live"])
        self.assertEqual([name for name, _ in emitted], ["routing", "emergency_triggered"])
        routing = emitted[0][1]
        self.assertEqual(routing["mode"], "emergency")
        self.assertEqual(routing["subtask_count"], 0)
        self.assertEqual(routing["session_id"], "sess-em")
        triggered = emitted[1][1]
        self.assertEqual(triggered["category"], "cardiac")
        self.assertEqual(triggered["source_agent"], "emergency_triage")

    def test_map_answer_done_passthrough_emergency(self):
        payload = map_answer_done(
            {
                "answer": "立即拨打 120",
                "emergency": True,
                "alert": "检测到疑似急症",
                "suggestions": ["立即拨打 120 急救电话"],
                "disclaimer": "仅为应急参考",
                "swarm_enabled": False,
            },
            "sess-em",
            0.4,
        )
        self.assertTrue(payload["emergency"])
        self.assertEqual(payload["alert"], "检测到疑似急症")
        self.assertEqual(payload["body"], "立即拨打 120")
        self.assertFalse(payload["swarm_enabled"])


if __name__ == "__main__":
    unittest.main()
