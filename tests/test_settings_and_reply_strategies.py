import os
import tempfile
import unittest

from ai_reply_engine import AIReplyEngine
from db_manager import DBManager
from settings_service import (
    apply_secret_action,
    normalize_system_settings,
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


    def test_reply_strategies_are_saved_in_one_transaction(self):
        initial = {
            "price": {"title": "议价专家", "content": "旧议价", "enabled": True},
            "tech": {"title": "技术专家", "content": "旧技术", "enabled": True},
            "default": {"title": "默认客服", "content": "旧默认", "enabled": True},
        }
        self.assertTrue(self.db.upsert_skill_agent_prompts_transaction(1, initial))

        cursor = self.db.conn.cursor()
        cursor.execute("""
            CREATE TRIGGER fail_tech_strategy
            BEFORE UPDATE ON skill_agent_prompts
            WHEN NEW.user_id = 1 AND NEW.prompt_type = 'tech'
            BEGIN
                SELECT RAISE(ABORT, 'forced strategy failure');
            END
        """)
        self.db.conn.commit()

        changed = {
            "price": {"title": "议价专家", "content": "新议价", "enabled": False},
            "tech": {"title": "技术专家", "content": "新技术", "enabled": False},
            "default": {"title": "默认客服", "content": "新默认", "enabled": False},
        }
        self.assertFalse(self.db.upsert_skill_agent_prompts_transaction(1, changed))
        stored = self.db.get_skill_agent_prompts(1)
        self.assertEqual(stored["price"]["content"], "旧议价")
        self.assertEqual(stored["tech"]["content"], "旧技术")
        self.assertEqual(stored["default"]["content"], "旧默认")

    def test_system_settings_section_is_saved_in_one_transaction(self):
        saved = self.db.save_system_settings_section({
            "registration_enabled": False,
            "item_sync_interval": 900,
        })

        self.assertTrue(saved)
        self.assertEqual(self.db.get_system_setting("registration_enabled"), "false")
        self.assertEqual(self.db.get_system_setting("item_sync_interval"), "900")


class ReplyStrategyPromptTests(unittest.TestCase):
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
