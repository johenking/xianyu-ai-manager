import os
import tempfile
import unittest
from unittest.mock import patch

import ai_reply_engine as ai_module
from ai_reply_engine import AIReplyEngine
from db_manager import DBManager


class AIProductScopingTests(unittest.TestCase):
    def setUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.db = DBManager(self.db_path)
        self.original_db = ai_module.db_manager
        ai_module.db_manager = self.db
        self.engine = AIReplyEngine()

    def tearDown(self):
        ai_module.db_manager = self.original_db
        self.db.conn.close()
        os.unlink(self.db_path)

    def test_training_rules_are_isolated_by_item(self):
        self.db.save_ai_training_rules(
            "account-1",
            "item-a",
            [
                {"scope": "global", "text": "回复保持简短"},
                {"scope": "item", "text": "这个商品使用邀请邮箱"},
            ],
        )
        self.db.save_ai_training_rules(
            "account-1",
            "item-b",
            [{"scope": "item", "text": "这个商品是官方直充"}],
        )

        item_a = self.db.get_ai_training_rules("account-1", "item-a")
        item_b = self.db.get_ai_training_rules("account-1", "item-b")

        self.assertEqual([rule["text"] for rule in item_a["global_rules"]], ["回复保持简短"])
        self.assertEqual([rule["text"] for rule in item_a["item_rules"]], ["这个商品使用邀请邮箱"])
        self.assertEqual([rule["text"] for rule in item_b["item_rules"]], ["这个商品是官方直充"])
        self.assertNotIn("邀请邮箱", " ".join(rule["text"] for rule in item_b["item_rules"]))

    def test_rule_context_reports_every_applicable_excluded_and_disabled_rule(self):
        self.db.save_ai_training_rules(
            "account-1",
            "item-a",
            [
                {"scope": "global", "text": "回复保持简短"},
                {"scope": "item", "text": "当前商品使用组织ID"},
            ],
        )
        self.db.save_ai_training_rules(
            "account-1",
            "item-b",
            [{"scope": "item", "text": "其他商品使用邀请邮箱"}],
        )
        other_rule = self.db.get_ai_training_rules("account-1", "item-b")["item_rules"][0]
        self.db.set_ai_training_rule_enabled("account-1", other_rule["id"], False)

        context = self.db.get_ai_training_rule_context("account-1", "item-a")

        self.assertEqual(
            [rule["text"] for rule in context["applied_rules"]],
            ["回复保持简短", "当前商品使用组织ID"],
        )
        self.assertEqual(context["applied_count"], 2)
        self.assertEqual(context["disabled_count"], 1)
        self.assertEqual(context["excluded_count"], 0)
        self.assertEqual(context["total_count"], 3)

    def test_rule_context_excludes_enabled_rules_bound_to_other_items(self):
        self.db.save_ai_training_rules(
            "account-1",
            "item-a",
            [{"scope": "item", "text": "A商品规则"}],
        )
        self.db.save_ai_training_rules(
            "account-1",
            "item-b",
            [{"scope": "item", "text": "B商品规则"}],
        )

        context = self.db.get_ai_training_rule_context("account-1", "item-a")

        self.assertEqual([rule["text"] for rule in context["applied_rules"]], ["A商品规则"])
        self.assertEqual([rule["text"] for rule in context["excluded_rules"]], ["B商品规则"])
        self.assertEqual(context["excluded_rules"][0]["reason"], "other_item")

    def test_system_prompt_marks_current_item_as_authoritative(self):
        prompt = self.engine.build_product_system_prompt(
            intent="default",
            custom_prompts_raw="全店回复要礼貌",
            item_info={"title": "Claude代充", "price": "135", "desc": "官网直充，不使用邀请邮箱"},
            global_rules=["回复保持简短"],
            item_rules=["只回答Claude商品信息"],
        )

        self.assertIn("当前商品事实（最高业务优先级）", prompt)
        self.assertIn("Claude代充", prompt)
        self.assertIn("全店通用规则", prompt)
        self.assertIn("当前商品专属规则", prompt)
        self.assertLess(prompt.index("全店通用规则"), prompt.index("当前商品事实（最高业务优先级）"))

    def test_conversation_context_is_isolated_by_item(self):
        self.engine.save_conversation("chat-1", "account-1", "buyer-1", "item-a", "user", "邀请怎么做")
        self.engine.save_conversation("chat-1", "account-1", "buyer-1", "item-b", "user", "Claude怎么充值")

        context = self.engine.get_conversation_context("chat-1", "account-1", "item-b")

        self.assertEqual(context, [{"role": "user", "content": "Claude怎么充值"}])

    def test_bargain_count_is_isolated_by_item(self):
        self.engine.save_conversation("chat-1", "account-1", "buyer-1", "item-a", "user", "便宜点", "price")
        self.engine.save_conversation("chat-1", "account-1", "buyer-1", "item-b", "user", "能优惠吗", "price")

        self.assertEqual(self.engine.get_bargain_count("chat-1", "account-1", "item-a"), 1)
        self.assertEqual(self.engine.get_bargain_count("chat-1", "account-1", "item-b"), 1)

    def _insert_item(self, item_id="item-a", title="Claude代充"):
        with self.db.lock:
            self.db.conn.execute(
                "INSERT INTO item_info (cookie_id, item_id, item_title, item_price, item_detail) VALUES (?, ?, ?, ?, ?)",
                ("account-1", item_id, title, "135", "官网代充，提供一个月订阅质保"),
            )
            self.db.conn.commit()

    @staticmethod
    def _knowledge(text: str, status: str = "confirmed"):
        return {
            "overview": {"text": text, "source": "user", "status": status},
            "pricing": [],
            "process": [],
            "after_sales": [],
            "forbidden": [],
            "faqs": [],
            "notes": [],
        }

    def test_item_knowledge_draft_is_saved_without_becoming_published(self):
        self._insert_item()
        draft = self._knowledge("这是草稿内容")

        self.db.save_ai_item_knowledge_draft("account-1", "item-a", draft, "detail-hash")
        profile = self.db.get_ai_item_knowledge_profile("account-1", "item-a")

        self.assertEqual(profile["draft"]["overview"]["text"], "这是草稿内容")
        self.assertEqual(profile["published"], {})
        self.assertEqual(profile["source_detail_hash"], "detail-hash")

    def test_pending_ai_knowledge_cannot_be_published(self):
        self._insert_item()
        self.db.save_ai_item_knowledge_draft(
            "account-1", "item-a", self._knowledge("AI推测", status="pending"), "hash"
        )

        with self.assertRaisesRegex(ValueError, "待确认"):
            self.db.publish_ai_item_knowledge("account-1", "item-a")

    def test_publish_creates_version_and_rollback_creates_a_new_version(self):
        self._insert_item()
        self.db.save_ai_item_knowledge_draft("account-1", "item-a", self._knowledge("第一版"), "hash-1")
        first = self.db.publish_ai_item_knowledge("account-1", "item-a")
        self.db.save_ai_item_knowledge_draft("account-1", "item-a", self._knowledge("第二版"), "hash-2")
        second = self.db.publish_ai_item_knowledge("account-1", "item-a")

        rolled_back = self.db.rollback_ai_item_knowledge("account-1", "item-a", first["version"])
        versions = self.db.get_ai_item_knowledge_versions("account-1", "item-a")

        self.assertEqual(second["version"], 2)
        self.assertEqual(rolled_back["version"], 3)
        self.assertEqual(rolled_back["published"]["overview"]["text"], "第一版")
        self.assertEqual([entry["version"] for entry in versions], [3, 2, 1])

    def test_published_knowledge_is_item_scoped_and_included_in_prompt(self):
        self._insert_item("item-a", "Claude代充")
        self._insert_item("item-b", "板写约字")
        self.db.save_ai_item_knowledge_draft("account-1", "item-a", self._knowledge("只用于Claude商品"), "hash")
        self.db.publish_ai_item_knowledge("account-1", "item-a")

        profile_a = self.db.get_ai_item_knowledge_profile("account-1", "item-a")
        profile_b = self.db.get_ai_item_knowledge_profile("account-1", "item-b")
        prompt = self.engine.build_product_system_prompt(
            intent="default",
            custom_prompts_raw="",
            item_info={"title": "Claude代充", "price": "135", "desc": "官网代充"},
            global_rules=[],
            item_rules=[],
            published_knowledge=profile_a["published"],
        )

        self.assertIn("只用于Claude商品", prompt)
        self.assertEqual(profile_b["published"], {})

    def test_ai_generated_knowledge_is_always_pending(self):
        raw = '''```json
        {
          "overview": {"text": "官网代充"},
          "pricing": [{"label": "Pro 5x", "amount": "750元"}],
          "process": [{"text": "先确认档位"}],
          "faqs": [{"question": "有售后吗", "answer": "有一个月质保"}]
        }
        ```'''

        draft = self.engine.parse_item_knowledge_draft(raw)

        self.assertEqual(draft["overview"]["source"], "ai")
        self.assertEqual(draft["overview"]["status"], "pending")
        self.assertEqual(draft["pricing"][0]["status"], "pending")
        self.assertEqual(draft["process"][0]["status"], "pending")
        self.assertEqual(draft["faqs"][0]["status"], "pending")
        self.assertEqual(draft["after_sales"], [])

    def test_seed_overview_is_authoritative_and_manual_entries_survive_generation(self):
        seed = self._knowledge("这是卖家亲自填写的商品概览")
        seed["process"] = [{
            "id": "manual-process",
            "text": "人工确认的交付流程",
            "source": "user",
            "status": "confirmed",
        }]
        seed["notes"] = [{
            "id": "old-ai-note",
            "text": "旧的未确认AI内容",
            "source": "ai",
            "status": "pending",
        }]
        generated = self._knowledge("模型擅自改写的概览", status="pending")
        generated["pricing"] = [{
            "id": "new-price",
            "label": "Pro",
            "amount": "145元",
            "source": "ai",
            "status": "pending",
        }]

        merged = self.engine.merge_generated_knowledge_with_seed(seed, generated)

        self.assertEqual(merged["overview"]["text"], "这是卖家亲自填写的商品概览")
        self.assertEqual(merged["overview"]["source"], "user")
        self.assertEqual(merged["overview"]["status"], "confirmed")
        self.assertEqual(merged["process"][0]["text"], "人工确认的交付流程")
        self.assertEqual(merged["pricing"][0]["text"] if "text" in merged["pricing"][0] else merged["pricing"][0]["label"], "Pro")
        self.assertEqual(merged["notes"], [])

    def test_generation_prompt_contains_seller_overview(self):
        self._insert_item()
        with patch.object(self.engine, "is_ai_enabled", return_value=True), \
             patch.object(self.db, "get_ai_reply_settings", return_value={
                 "api_key": "test-key",
                 "base_url": "https://api.example.com/v1",
                 "model_name": "test-model",
             }), \
             patch.object(self.engine, "_create_openai_client", return_value=object()), \
             patch.object(self.engine, "_call_openai_api", return_value='{"overview":{"text":"AI概览"}}') as call:
            self.engine.generate_item_knowledge_draft(
                {"title": "Claude代充", "price": "135", "desc": "商品详情"},
                "account-1",
                seller_overview="卖家说明：这是官网代充，不是礼品卡",
            )

        messages = call.call_args.args[2]
        self.assertIn("卖家说明：这是官网代充，不是礼品卡", messages[1]["content"])

    def test_copy_knowledge_creates_target_draft_without_publishing_it(self):
        self._insert_item("item-a", "Claude商品A")
        self._insert_item("item-b", "Claude商品B")
        source = self._knowledge("同款Claude代充服务")
        self.db.save_ai_item_knowledge_draft("account-1", "item-a", source, "source-hash")
        self.db.publish_ai_item_knowledge("account-1", "item-a")

        result = self.db.copy_ai_item_knowledge_draft(
            "account-1", "item-a", ["item-b"], overwrite=False
        )
        target = self.db.get_ai_item_knowledge_profile("account-1", "item-b")

        self.assertEqual(result["copied_item_ids"], ["item-b"])
        self.assertEqual(result["source_kind"], "draft")
        self.assertEqual(result["copied_count"], 1)
        self.assertEqual(result["skipped_count"], 0)
        self.assertEqual(target["draft"]["overview"]["text"], "同款Claude代充服务")
        self.assertEqual(target["published"], {})

    def test_copy_knowledge_does_not_overwrite_existing_target_without_confirmation(self):
        self._insert_item("item-a", "源商品")
        self._insert_item("item-b", "目标商品")
        self.db.save_ai_item_knowledge_draft("account-1", "item-a", self._knowledge("源档案"), "hash-a")
        self.db.save_ai_item_knowledge_draft("account-1", "item-b", self._knowledge("目标原档案"), "hash-b")

        result = self.db.copy_ai_item_knowledge_draft(
            "account-1", "item-a", ["item-b"], overwrite=False
        )
        target = self.db.get_ai_item_knowledge_profile("account-1", "item-b")

        self.assertEqual(result["copied_item_ids"], [])
        self.assertEqual(result["skipped_item_ids"], ["item-b"])
        self.assertEqual(result["skipped_count"], 1)
        self.assertEqual(result["skipped_reasons"]["item-b"], "目标已有草稿或已发布知识档案，未开启覆盖")
        self.assertEqual(target["draft"]["overview"]["text"], "目标原档案")

    def test_copy_knowledge_falls_back_to_published_when_source_has_no_draft(self):
        self._insert_item("item-a", "源商品")
        self._insert_item("item-b", "目标商品")
        self.db.save_ai_item_knowledge_draft("account-1", "item-a", self._knowledge("已发布档案"), "hash-a")
        self.db.publish_ai_item_knowledge("account-1", "item-a")
        with self.db.lock:
            self.db.conn.execute(
                "UPDATE ai_item_knowledge_profiles SET draft_json = '{}' WHERE cookie_id = ? AND item_id = ?",
                ("account-1", "item-a"),
            )
            self.db.conn.commit()

        result = self.db.copy_ai_item_knowledge_draft("account-1", "item-a", ["item-b"], overwrite=False)
        target = self.db.get_ai_item_knowledge_profile("account-1", "item-b")

        self.assertEqual(result["source_kind"], "published")
        self.assertEqual(target["draft"]["overview"]["text"], "已发布档案")

    def test_rule_audit_parser_maps_every_rule_and_flags_violations(self):
        rules = [
            {"id": 3, "text": "不要说这是礼品卡"},
            {"id": 8, "text": "只回答当前问题"},
        ]
        raw = '''{
          "results": [
            {"rule_id": 3, "status": "violated", "reason": "回复说成了礼品卡"},
            {"rule_id": 8, "status": "followed", "reason": "回复简短"}
          ],
          "conflicts": ["规则与商品概览冲突"]
        }'''

        audit = self.engine.parse_rule_audit(raw, rules)

        self.assertEqual(audit["results"][0]["status"], "violated")
        self.assertEqual(audit["results"][1]["status"], "followed")
        self.assertEqual(audit["violation_count"], 1)
        self.assertEqual(audit["conflicts"], ["规则与商品概览冲突"])

    def test_rule_checked_reply_regenerates_once_after_a_violation(self):
        rules = [{"id": 3, "text": "不要说这是礼品卡"}]
        messages = [
            {"role": "system", "content": "当前商品是官网代充"},
            {"role": "user", "content": "这是礼品卡吗？"},
        ]
        with patch.object(self.engine, "_call_configured_model", side_effect=[
            "这是礼品卡。",
            '{"results":[{"rule_id":3,"status":"violated","reason":"错误说成礼品卡"}],"conflicts":[]}',
            "不是礼品卡，这是官网代充服务。",
            '{"results":[{"rule_id":3,"status":"followed","reason":"已否认礼品卡"}],"conflicts":[]}',
        ]) as call:
            result = self.engine.generate_rule_checked_reply(
                settings={"model_name": "test-model"},
                cookie_id="account-1",
                messages=messages,
                buyer_message="这是礼品卡吗？",
                rules=rules,
                knowledge_text="商品概览：官网代充服务",
                max_tokens=160,
                temperature=0.5,
            )

        self.assertEqual(result["reply"], "不是礼品卡，这是官网代充服务。")
        self.assertTrue(result["regenerated"])
        self.assertEqual(result["audit"]["violation_count"], 0)
        self.assertEqual(call.call_count, 4)

    def test_price_rule_violation_is_guarded_after_retry(self):
        rules = [{"id": 11, "text": "Pro无质保145元，有质保155元"}]
        messages = [
            {"role": "system", "content": "当前商品价格是135"},
            {"role": "user", "content": "Pro多少钱？"},
        ]
        with patch.object(self.engine, "_call_configured_model", side_effect=[
            "Pro是135元。",
            '{"results":[{"rule_id":11,"status":"violated","reason":"回复价格135不符合145/155"}],"conflicts":[]}',
            "Pro还是135元。",
            '{"results":[{"rule_id":11,"status":"violated","reason":"仍然回复135"}],"conflicts":[]}',
        ]):
            result = self.engine.generate_rule_checked_reply(
                settings={"model_name": "test-model"},
                cookie_id="account-1",
                messages=messages,
                buyer_message="Pro多少钱？",
                rules=rules,
                knowledge_text="规格与价格：Pro 145元/155元",
                max_tokens=160,
                temperature=0.5,
            )

        self.assertTrue(result["guarded_by_rule"])
        self.assertEqual(result["guard_reason"], "price_rule_violation")
        self.assertIn(11, result["guarded_rule_ids"])
        self.assertIn("Pro无质保145元", result["reply"])
        self.assertNotEqual(result["reply"], "Pro还是135元。")

    def test_conflicting_price_rules_do_not_call_model(self):
        rules = [
            {"id": 11, "text": "Pro价格145元"},
            {"id": 12, "text": "Pro价格135元"},
        ]
        messages = [
            {"role": "system", "content": "当前商品价格是135"},
            {"role": "user", "content": "Pro多少钱？"},
        ]
        with patch.object(self.engine, "_call_configured_model") as call:
            result = self.engine.generate_rule_checked_reply(
                settings={"model_name": "test-model"},
                cookie_id="account-1",
                messages=messages,
                buyer_message="Pro多少钱？",
                rules=rules,
                knowledge_text="",
                max_tokens=160,
                temperature=0.5,
            )

        call.assert_not_called()
        self.assertTrue(result["guarded_by_rule"])
        self.assertEqual(result["guard_reason"], "price_rule_conflict")
        self.assertIn("价格规则存在冲突", result["reply"])

    def test_overlapping_price_rules_are_not_treated_as_conflicts(self):
        rules = [
            {"id": 11, "text": "Pro无质保145元，有质保155元"},
            {"id": 12, "text": "Pro无质保145元"},
        ]

        self.assertEqual(self.engine._detect_price_rule_conflicts(rules), [])


if __name__ == "__main__":
    unittest.main()
