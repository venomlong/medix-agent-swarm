"""AutoFixer 规则层：免责声明、高危就医提醒。不打 LLM。"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from validation.auto_fixer import AutoFixer


class AutoFixerRuleTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(
            prefix="auto_fixer_", suffix=".jsonl", delete=False
        )
        self._tmp.close()
        self._old = os.environ.get("MEDIX_SAFETY_LOG_PATH")
        os.environ["MEDIX_SAFETY_LOG_PATH"] = self._tmp.name
        self.fixer = AutoFixer()

    def tearDown(self):
        if self._old is None:
            os.environ.pop("MEDIX_SAFETY_LOG_PATH", None)
        else:
            os.environ["MEDIX_SAFETY_LOG_PATH"] = self._old
        Path(self._tmp.name).unlink(missing_ok=True)

    def test_adds_disclaimer_when_missing(self):
        raw = "高血压需要低盐饮食、适量运动。"
        fixed = self.fixer.fix_missing_disclaimer(raw)
        self.assertTrue("免责声明" in fixed or "仅供参考" in fixed)
        self.assertTrue(fixed.startswith(raw))

    def test_skips_disclaimer_if_already_present(self):
        raw = "多喝水休息。以上信息仅供参考，不能替代就医。"
        self.assertEqual(self.fixer.fix_missing_disclaimer(raw), raw)

    def test_adds_high_risk_warning_for_chest_pain(self):
        raw = "您的胸痛可能是心绞痛。"
        fixed = self.fixer.fix_high_risk_warning(raw)
        self.assertNotEqual(fixed, raw)
        self.assertTrue("就医" in fixed or "120" in fixed or "急诊" in fixed)
        self.assertTrue(fixed.endswith(raw) or raw in fixed)

    def test_skips_high_risk_warning_if_already_advises_care(self):
        raw = "出现胸痛应立即就医或去急诊。"
        self.assertEqual(self.fixer.fix_high_risk_warning(raw), raw)

    def test_skips_high_risk_warning_without_keywords(self):
        raw = "普通感冒可以多休息。"
        self.assertEqual(self.fixer.fix_high_risk_warning(raw), raw)

    def test_fix_output_applies_disclaimer_and_emergency_warning(self):
        raw = "胸痛持续存在，注意休息。"
        fixed = self.fixer.fix_output(
            raw, ["add_emergency_warning", "add_disclaimer"]
        )
        self.assertTrue("就医" in fixed or "120" in fixed or "急诊" in fixed)
        self.assertTrue("免责" in fixed or "仅供参考" in fixed)

    def test_remove_diagnosis_statements(self):
        raw = "您患有高血压，确诊为2级，肯定是需要吃药。"
        fixed = self.fixer.remove_diagnosis_statements(raw)
        self.assertNotIn("您患有", fixed)
        self.assertNotIn("确诊为", fixed)
        self.assertNotIn("肯定是", fixed)
        self.assertIn("可能存在", fixed)


if __name__ == "__main__":
    unittest.main()
