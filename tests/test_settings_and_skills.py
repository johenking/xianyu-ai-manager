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

    def test_monitor_features_are_runtime_validated(self):
        self.assertIsNone(validate_skill_monitor_features(notify_enabled=True, ai_filter="低风险卖家"))

    def test_skill_monitor_task_scheduler_fields_round_trip(self):
        task_id = self.db.create_skill_monitor_task(1, {
            "name": "测试任务",
            "keyword": "iPhone",
            "notify_enabled": True,
            "ai_filter": "只保留低价商品",
            "schedule_enabled": True,
            "schedule_interval_minutes": 5,
            "next_run_at": "2000-01-01 00:00:00",
        })

        task = self.db.get_skill_monitor_task(task_id, 1)
        self.assertTrue(task["notify_enabled"])
        self.assertEqual(task["ai_filter"], "只保留低价商品")
        self.assertTrue(task["schedule_enabled"])
        self.assertEqual(task["schedule_interval_minutes"], 15)
        self.assertEqual(task["last_status"], "idle")

        due = self.db.list_due_skill_monitor_tasks()
        self.assertEqual([item["id"] for item in due], [task_id])

        self.assertTrue(self.db.mark_skill_monitor_task_running(task_id, 1))
        self.assertFalse(self.db.mark_skill_monitor_task_running(task_id, 1))
        self.assertEqual(self.db.reset_running_skill_monitor_tasks(), 1)

        self.assertTrue(self.db.update_skill_monitor_task(task_id, 1, {
            "schedule_interval_minutes": 30,
            "schedule_enabled": False,
        }))
        task = self.db.get_skill_monitor_task(task_id, 1)
        self.assertFalse(task["schedule_enabled"])
        self.assertEqual(task["schedule_interval_minutes"], 30)

    def test_skill_monitor_result_deduplicates_by_url_then_item_id(self):
        task_id = self.db.create_skill_monitor_task(1, {
            "name": "测试任务",
            "keyword": "iPhone",
        })
        self.db.create_skill_monitor_result({
            "task_id": task_id,
            "user_id": 1,
            "title": "iPhone 15",
            "item_url": "https://example.test/item-1",
            "raw_data": {"item_id": "item-1"},
        })

        self.assertTrue(self.db.skill_monitor_result_exists(
            task_id, 1, "https://example.test/item-1", "different-item"
        ))
        self.assertTrue(self.db.skill_monitor_result_exists(
            task_id, 1, "", "item-1"
        ))
        self.assertFalse(self.db.skill_monitor_result_exists(
            task_id, 1, "https://example.test/item-2", "item-2"
        ))

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
