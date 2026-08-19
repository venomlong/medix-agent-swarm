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

    def test_map_answer_done_emergency_fills_default_alert(self):
        payload = map_answer_done(
            {
                "answer": "立即拨打 120",
                "emergency": True,
                "swarm_enabled": False,
            },
            "sess-em-fallback",
            0.2,
        )
        self.assertTrue(payload["emergency"])
        self.assertTrue(payload["alert"])
        self.assertIn("急症", payload["alert"])
        self.assertEqual(payload["alert_note"], "急症分诊已短路常规 Swarm")


class HarmfulLiveListenerTests(unittest.TestCase):
    def test_harmful_blocked_sets_live_and_emits_routing(self):
        emitted = []
        flags = {"live": False}

        def emit(name, data):
            emitted.append((name, data))

        listener = attach_live_listener(emit, "sess-h", flags)
        listener(
            Event(
                type=EventType.HARMFUL_BLOCKED,
                source_agent="harm_filter",
                data={
                    "is_harmful": True,
                    "category": "jailbreak",
                    "matched": ["忽略以上指令"],
                    "reason": "命中jailbreak短语",
                    "method": "rule",
                },
            )
        )

        self.assertTrue(flags["live"])
        self.assertEqual([name for name, _ in emitted], ["routing", "harmful_blocked"])
        routing = emitted[0][1]
        self.assertEqual(routing["mode"], "blocked")
        self.assertEqual(routing["subtask_count"], 0)
        self.assertEqual(emitted[1][1]["category"], "jailbreak")

    def test_map_answer_done_passthrough_blocked(self):
        payload = map_answer_done(
            {
                "answer": "本系统无法回答该请求。",
                "blocked": True,
                "alert": "检测到敏感内容",
                "suggestions": ["用健康问题重新提问"],
                "disclaimer": "不构成医疗建议",
                "swarm_enabled": False,
            },
            "sess-h",
            0.2,
        )
        self.assertTrue(payload["blocked"])
        self.assertFalse(payload["emergency"])
        self.assertEqual(payload["alert"], "检测到敏感内容")
        self.assertFalse(payload["swarm_enabled"])


if __name__ == "__main__":
    unittest.main()
