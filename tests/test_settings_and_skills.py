import os
import tempfile
import unittest
from pathlib import Path

from ai_reply_engine import AIReplyEngine
from db_manager import DBManager
from settings_service import (
    apply_secret_action,
    normalize_system_settings,
    validate_skill_monitor_features,
)
import reply_server


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
            "account_id": "account-1",
            "schedule_enabled": True,
            "schedule_interval_minutes": 5,
            "next_run_at": "2000-01-01 00:00:00",
        })

        task = self.db.get_skill_monitor_task(task_id, 1)
        self.assertTrue(task["notify_enabled"])
        self.assertEqual(task["ai_filter"], "只保留低价商品")
        self.assertEqual(task["account_id"], "account-1")
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

    def test_list_tasks_without_runs_has_null_evidence(self):
        """无运行记录的任务，latest_run_evidence 为 None"""
        task_id = self.db.create_skill_monitor_task(1, {
            "name": "未跑过的任务",
            "keyword": "iPhone",
            "account_id": "account-1",
        })
        tasks = self.db.list_skill_monitor_tasks(1)
        target = next(t for t in tasks if t["id"] == task_id)
        self.assertIn("latest_run_evidence", target)
        self.assertIsNone(target["latest_run_evidence"])

    def test_list_tasks_surfaces_latest_run_evidence(self):
        """已运行过的任务，list 返回最近一次真实运行证据"""
        task_id = self.db.create_skill_monitor_task(1, {
            "name": "跑过的任务",
            "keyword": "iPhone",
            "account_id": "account-1",
            "enabled": True,
        })
        claim = self.db.claim_skill_monitor_run(
            task_id, 1, trigger_type="manual", source_adapter="playwright",
        )
        self.assertTrue(claim.get("claimed"))
        self.assertTrue(self.db.finish_skill_monitor_run(
            claim["run_id"], claim["claim_token"],
            status="success", raw_result_count=7, accepted_result_count=3,
        ))

        tasks = self.db.list_skill_monitor_tasks(1)
        target = next(t for t in tasks if t["id"] == task_id)
        evidence = target["latest_run_evidence"]
        self.assertIsNotNone(evidence)
        self.assertEqual(evidence["status"], "success")
        self.assertEqual(evidence["trigger_type"], "manual")
        self.assertEqual(evidence["source_adapter"], "playwright")
        self.assertEqual(evidence["raw_result_count"], 7)
        self.assertEqual(evidence["accepted_result_count"], 3)
        self.assertIsNotNone(evidence["observed_at"])

    def test_mock_runs_do_not_count_as_real_run_evidence(self):
        task_id = self.db.create_skill_monitor_task(1, {
            "name": "模拟运行任务",
            "keyword": "iPhone",
            "account_id": "account-1",
            "enabled": True,
        })
        claim = self.db.claim_skill_monitor_run(
            task_id, 1, trigger_type="manual", source_adapter="mock",
        )
        self.assertTrue(self.db.finish_skill_monitor_run(
            claim["run_id"], claim["claim_token"],
            status="success", raw_result_count=9, accepted_result_count=4,
        ))

        task = next(item for item in self.db.list_skill_monitor_tasks(1) if item["id"] == task_id)
        self.assertIsNone(task["latest_run_evidence"])

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

    def test_database_write_probe_rolls_back_and_is_cached_per_database(self):
        reply_server._ops_db_probe_cache.clear()
        first = reply_server._get_database_write_probe(Path(self.db_path))
        second = reply_server._get_database_write_probe(Path(self.db_path))

        self.assertEqual(first["status"], "ok")
        self.assertTrue(first["writable"])
        self.assertEqual(second["observed_at"], first["observed_at"])
        row = self.db.conn.execute(
            "SELECT 1 FROM system_settings WHERE key = '__ops_write_probe__'"
        ).fetchone()
        self.assertIsNone(row)
        reply_server._ops_db_probe_cache.clear()


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
