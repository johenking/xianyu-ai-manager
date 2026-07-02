import os
import tempfile
import unittest

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


if __name__ == "__main__":
    unittest.main()
