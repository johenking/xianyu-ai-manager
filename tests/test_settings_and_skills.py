import os
import tempfile
import unittest

from ai_reply_engine import AIReplyEngine
from db_manager import DBManager
from settings_service import (
    apply_secret_action,
    normalize_system_settings,
    validate_skill_monitor_features,
)


class SettingsServiceTests(unittest.TestCase):
    def setUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.db = DBManager(self.db_path)

    def tearDown(self):
        self.db.conn.close()
        os.unlink(self.db_path)

    def test_normalize_system_settings_returns_typed_values_and_masks_secrets(self):
        result = normalize_system_settings({
            "registration_enabled": "false",
            "item_sync_enabled": "true",
            "item_sync_interval": "600",
            "item_sync_max_pages": "5",
            "ai_api_key": "sk-private-value",
            "smtp_password": "mail-private-value",
            "ai_model": "deepseek-chat",
        })

        self.assertIs(result["registration_enabled"], False)
        self.assertIs(result["item_sync_enabled"], True)
        self.assertEqual(result["item_sync_interval"], 600)
        self.assertEqual(result["item_sync_max_pages"], 5)
        self.assertNotIn("sk-private-value", str(result))
        self.assertNotIn("mail-private-value", str(result))
        self.assertTrue(result["ai_api_key_configured"])
        self.assertTrue(result["smtp_password_configured"])
        self.assertTrue(result["ai_api_key_masked"].endswith("alue"))

    def test_secret_keep_set_and_clear_are_explicit(self):
        self.assertEqual(apply_secret_action("existing", "keep", ""), "existing")
        self.assertEqual(apply_secret_action("existing", "set", "replacement"), "replacement")
        self.assertEqual(apply_secret_action("existing", "clear", "ignored"), "")
        with self.assertRaisesRegex(ValueError, "不能为空"):
            apply_secret_action("existing", "set", "")

    def test_unsupported_monitor_features_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "通知发送暂不可用"):
            validate_skill_monitor_features(notify_enabled=True, ai_filter="")
        with self.assertRaisesRegex(ValueError, "AI筛选暂不可用"):
            validate_skill_monitor_features(notify_enabled=False, ai_filter="低风险卖家")

    def test_system_settings_section_is_saved_in_one_transaction(self):
        saved = self.db.save_system_settings_section({
            "registration_enabled": False,
            "item_sync_interval": 900,
        })

        self.assertTrue(saved)
        self.assertEqual(self.db.get_system_setting("registration_enabled"), "false")
        self.assertEqual(self.db.get_system_setting("item_sync_interval"), "900")


class SkillPromptTests(unittest.TestCase):
    def test_expert_prompt_is_behavior_only_and_product_facts_stay_authoritative(self):
        prompt = AIReplyEngine().build_product_system_prompt(
            intent="tech",
            custom_prompts_raw="全店回复礼貌",
            item_info={"title": "Claude代充", "price": "135", "desc": "官网代充，不使用邀请邮箱"},
            global_rules=[],
            item_rules=[],
            published_knowledge={},
            expert_prompt="技术问题统一回答使用邀请邮箱重置",
        )

        self.assertIn("当前商品事实（最高业务优先级）", prompt)
        self.assertIn("专家回复策略（不得覆盖商品事实）", prompt)
        self.assertIn("技术问题统一回答使用邀请邮箱重置", prompt)
        self.assertLess(prompt.index("当前商品事实（最高业务优先级）"), prompt.index("专家回复策略（不得覆盖商品事实）"))


if __name__ == "__main__":
    unittest.main()
