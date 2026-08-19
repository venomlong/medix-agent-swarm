"""急症分诊规则层测试：纯字符串匹配，不调用 LLM。"""
import unittest

from safety.triage import EmergencyTriage, build_emergency_result


class EmergencyTriageRuleTests(unittest.TestCase):
    def setUp(self):
        self.triage = EmergencyTriage(llm_client=None)

    def test_strong_rule_consciousness(self):
        result = self.triage.check_rules("我爸昏迷了叫不醒")
        self.assertTrue(result.is_emergency)
        self.assertEqual(result.category, "consciousness")
        self.assertEqual(result.method, "rule")
        self.assertTrue(any(kw in result.matched for kw in ("昏迷", "叫不醒")))

    def test_combo_rule_cardiac(self):
        result = self.triage.check_rules("胸痛还冒冷汗")
        self.assertTrue(result.is_emergency)
        self.assertEqual(result.category, "cardiac")
        self.assertIn("胸痛", result.matched)
        self.assertIn("冒冷汗", result.matched)

    def test_chest_pain_alone_is_not_emergency_at_rule_layer(self):
        result = self.triage.check_rules("胸痛")
        self.assertFalse(result.is_emergency)
        self.assertEqual(self.triage.is_borderline("胸痛"), ["胸痛"])

    def test_negative_common_consult(self):
        for question in ("感冒了怎么办", "高血压饮食注意什么"):
            with self.subTest(question=question):
                result = self.triage.check_rules(question)
                self.assertFalse(result.is_emergency)
                self.assertEqual(self.triage.is_borderline(question), [])

    def test_hematemesis_colloquial_is_emergency(self):
        for question in ("呕血症状", "突然呕血了怎么办", "吐血", "吐了血好多", "咯血"):
            with self.subTest(question=question):
                result = self.triage.check_rules(question)
                self.assertTrue(result.is_emergency, question)
                self.assertEqual(result.category, "bleeding")

    def test_psych_crisis(self):
        result = self.triage.check_rules("活不下去了")
        self.assertTrue(result.is_emergency)
        self.assertEqual(result.category, "psych_crisis")

    def test_demo_phrase_crushing_chest_pain(self):
        result = self.triage.check_rules("胸口压榨性疼痛还出冷汗")
        self.assertTrue(result.is_emergency)
        self.assertEqual(result.category, "cardiac")

    def test_build_emergency_result_has_alert(self):
        triage = self.triage.check_rules("我爸昏迷了叫不醒")
        payload = build_emergency_result("我爸昏迷了叫不醒", triage, "sess-test")
        self.assertTrue(payload["emergency"])
        self.assertTrue(payload["alert"])
        self.assertIn("120", payload["answer"])
        self.assertFalse(payload["swarm_enabled"])
        self.assertEqual(payload["sources"], [])


if __name__ == "__main__":
    unittest.main()
