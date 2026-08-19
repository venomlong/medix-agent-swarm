"""全局日志 PII 脱敏：掩码规则 + loguru patcher。不打付费 LLM。"""
from __future__ import annotations

import unittest

from loguru import logger

from core.log_privacy import install_log_privacy, mask_pii, patch_log_record
from validation.safety_log import mask_pii as safety_mask_pii


class MaskPiiTests(unittest.TestCase):
    def test_phone_keeps_first_digit(self):
        self.assertEqual(mask_pii("13812345678"), "1**********")
        self.assertEqual(mask_pii("联系我 13900001111"), "联系我 1**********")

    def test_id_keeps_head_and_tail(self):
        raw = "110101199001011234"
        masked = mask_pii(raw)
        self.assertEqual(masked, "1101**********1234")
        self.assertNotIn("19900101", masked)

    def test_id_checksum_x(self):
        raw = "11010119900101123X"
        masked = mask_pii(raw)
        self.assertEqual(masked[:4], "1101")
        self.assertEqual(masked[-4:], "123X")
        self.assertNotIn("19900101", masked)

    def test_email_keeps_first_char(self):
        self.assertEqual(mask_pii("alice@example.com"), "a***@***")
        self.assertEqual(mask_pii("邮箱：Bob.Li+1@mail.co"), "邮箱：B***@***")

    def test_mixed_sentence_masks_all_kinds(self):
        text = "患者13812345678，身份证110101199001011234，邮箱foo@bar.com，主诉感冒"
        out = mask_pii(text)
        self.assertIn("1**********", out)
        self.assertIn("1101**********1234", out)
        self.assertIn("f***@***", out)
        self.assertIn("主诉感冒", out)
        self.assertNotIn("13812345678", out)
        self.assertNotIn("110101199001011234", out)
        self.assertNotIn("foo@bar.com", out)

    def test_non_pii_medical_text_untouched(self):
        text = "胸口压榨性疼痛还出冷汗，需要立刻就医吗？"
        self.assertEqual(mask_pii(text), text)

    def test_empty_and_none(self):
        self.assertEqual(mask_pii(""), "")
        self.assertIsNone(mask_pii(None))

    def test_does_not_match_short_numbers(self):
        self.assertEqual(mask_pii("剂量 100mg，电话 12345"), "剂量 100mg，电话 12345")

    def test_safety_log_reuses_same_rules(self):
        sample = "手机13812345678 证110101199001011234 mail=a@b.co"
        self.assertEqual(mask_pii(sample), safety_mask_pii(sample))
        self.assertIs(mask_pii, safety_mask_pii)


class LoguruPatcherTests(unittest.TestCase):
    def setUp(self):
        install_log_privacy()
        self.captured = []
        self._hid = logger.add(
            lambda message: self.captured.append(message.record["message"]),
            format="{message}",
            level="DEBUG",
        )

    def tearDown(self):
        logger.remove(self._hid)

    def test_fstring_question_is_masked(self):
        question = "我的手机是13812345678，帮我看看感冒怎么办"
        logger.info(f"Processing question (session=s1): {question[:50]}...")
        self.assertEqual(len(self.captured), 1)
        line = self.captured[0]
        self.assertNotIn("13812345678", line)
        self.assertIn("1**********", line)
        self.assertIn("感冒怎么办", line)

    def test_format_args_are_masked(self):
        logger.info("user mail={}", "alice@example.com")
        self.assertEqual(self.captured[0], "user mail=a***@***")

    def test_install_is_idempotent(self):
        install_log_privacy()
        install_log_privacy()
        logger.info("id=110101199001011234")
        self.assertEqual(self.captured[0], "id=1101**********1234")


class PatchRecordUnitTests(unittest.TestCase):
    def test_patch_mutates_message_and_extra(self):
        record = {
            "message": "call 13900001111",
            "extra": {"mail": "z@z.cn", "n": 3, "trace": "deadbeefcafe"},
        }
        patch_log_record(record)
        self.assertEqual(record["message"], "call 1**********")
        self.assertEqual(record["extra"]["mail"], "z***@***")
        self.assertEqual(record["extra"]["n"], 3)
        # trace_id 保持原样，避免 12 位 hex 被当成 PII
        self.assertEqual(record["extra"]["trace"], "deadbeefcafe")


if __name__ == "__main__":
    unittest.main()
