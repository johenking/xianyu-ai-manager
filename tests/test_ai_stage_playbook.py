"""订单感知转正开关、分阶段剧本、意图扩类与本地价格守护的回归测试。"""

import os
import tempfile
import unittest
from unittest.mock import patch

import ai_reply_engine as ai_module
from ai_reply_engine import AIReplyEngine
from db_manager import DBManager


class StagePlaybookTestBase(unittest.TestCase):
    def setUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.db = DBManager(self.db_path)
        admin = self.db.get_user_by_username("admin")
        self.assertTrue(
            self.db.save_cookie(
                "account-1",
                "unb=account-1; cookie2=fixture-session",
                admin["id"],
            )
        )
        self.assertTrue(
            self.db.save_ai_reply_settings(
                "account-1",
                {
                    "ai_enabled": True,
                    "model_name": "deepseek-chat",
                    "api_key": "fixture-key",
                    "base_url": "https://api.deepseek.com",
                },
            )
        )
        self.original_db = ai_module.db_manager
        ai_module.db_manager = self.db
        self.engine = AIReplyEngine()

    def tearDown(self):
        ai_module.db_manager = self.original_db
        self.db.conn.close()
        os.unlink(self.db_path)

    def _insert_order(self, order_id, order_status, buyer_id="buyer-1", chat_id="chat-1"):
        with self.db.lock:
            self.db.conn.execute(
                """
                INSERT INTO orders (
                    order_id, cookie_id, item_id, buyer_id, chat_id,
                    order_status, quantity, paid_amount_fen
                ) VALUES (?, 'account-1', 'item-1', ?, ?, ?, '1', 9900)
                """,
                (order_id, buyer_id, chat_id, order_status),
            )
            self.db.conn.commit()


class OrderAwareSwitchTests(StagePlaybookTestBase):
    def test_flag_defaults_off_and_reads_system_setting(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(AIReplyEngine.ORDER_AWARE_ENV, None)
            self.assertFalse(self.engine.order_aware_enabled())
            self.db.set_system_setting(AIReplyEngine.ORDER_AWARE_SETTING_KEY, "1")
            self.assertTrue(self.engine.order_aware_enabled())
            self.db.set_system_setting(AIReplyEngine.ORDER_AWARE_SETTING_KEY, "0")
            self.assertFalse(self.engine.order_aware_enabled())

    def test_env_overrides_system_setting(self):
        self.db.set_system_setting(AIReplyEngine.ORDER_AWARE_SETTING_KEY, "1")
        with patch.dict(os.environ, {AIReplyEngine.ORDER_AWARE_ENV: "off"}):
            self.assertFalse(self.engine.order_aware_enabled())
        self.db.set_system_setting(AIReplyEngine.ORDER_AWARE_SETTING_KEY, "0")
        with patch.dict(os.environ, {AIReplyEngine.ORDER_AWARE_ENV: "on"}):
            self.assertTrue(self.engine.order_aware_enabled())

    def test_generate_reply_routes_to_legacy_when_flag_off(self):
        with patch.object(self.engine, "order_aware_enabled", return_value=False), \
                patch.object(self.engine, "_generate_reply_legacy", return_value="legacy") as legacy:
            reply = self.engine.generate_reply(
                "在吗", {"title": "t"}, "chat-1", "account-1", "buyer-1", "item-1",
                skip_wait=True,
            )
        self.assertEqual(reply, "legacy")
        legacy.assert_called_once()

    def test_generate_reply_uses_order_aware_path_when_flag_on(self):
        captured = {}

        def fake_model(cookie_id, settings, messages, max_tokens, temperature):
            captured["system"] = messages[0]["content"]
            return "好的，稍等哦"

        self._insert_order("order-a", "pending_ship")
        with patch.object(self.engine, "order_aware_enabled", return_value=True), \
                patch.object(self.engine, "_generate_reply_legacy") as legacy, \
                patch.object(self.engine, "_call_configured_model", side_effect=fake_model):
            reply = self.engine.generate_reply(
                "发货了吗", {"title": "t", "price": "99", "desc": "d"},
                "chat-1", "account-1", "buyer-1", "item-1", skip_wait=True,
            )
        legacy.assert_not_called()
        self.assertEqual(reply, "好的，稍等哦")
        self.assertIn("已付款待发货", captured["system"])
        self.assertIn("阶段应对要求", captured["system"])

    def test_shadow_wrappers_short_circuit_when_flag_on(self):
        with patch.object(self.engine, "order_aware_enabled", return_value=True), \
                patch.object(self.engine, "generate_reply") as inner:
            self.assertIsNone(
                self.engine.generate_shadow_reply(
                    "在吗", {}, "chat-1", "account-1", "buyer-1", "item-1"
                )
            )
        inner.assert_not_called()


class TradeStageTests(StagePlaybookTestBase):
    def test_status_to_stage_mapping(self):
        cases = {
            "processing": "ordered_unpaid",
            "pending_ship": "paid_pending_ship",
            "shipped": "shipped_in_use",
            "completed": "completed",
            "refunding": "aftersale",
            "refunded": "aftersale",
            "refund_cancelled": "shipped_in_use",
            "cancelled": "closed",
        }
        for status, expected in cases.items():
            summary = '{"order_status": "%s"}' % status
            self.assertEqual(
                self.engine.resolve_trade_stage("unique", summary), expected, status
            )

    def test_scope_fallbacks(self):
        self.assertEqual(self.engine.resolve_trade_stage("none", ""), "presale")
        self.assertEqual(self.engine.resolve_trade_stage("ambiguous", ""), "multiple_orders")
        self.assertIsNone(self.engine.resolve_trade_stage("legacy", ""))
        self.assertEqual(self.engine.resolve_trade_stage("unique", "{}"), "unknown")
        self.assertEqual(
            self.engine.resolve_trade_stage("unique", '{"system_shipped": true}'),
            "shipped_in_use",
        )

    def test_ordered_unpaid_playbook_never_urges_payment(self):
        directive = self.engine._stage_directive("ordered_unpaid")
        self.assertIn("不要催促付款", directive)
        self.assertIn("是否遇到问题", directive)

    def test_stage_directive_empty_for_legacy(self):
        self.assertEqual(self.engine._stage_directive(None), "")


class IntentTaxonomyTests(StagePlaybookTestBase):
    def test_new_intents(self):
        cases = {
            "我要退款": "aftersale",
            "什么时候发货啊": "shipping",
            "我已经付款了": "payment",
            "能便宜点吗": "price",
            "这个怎么兑换": "tech",
            "在吗": "default",
        }
        for message, expected in cases.items():
            self.assertEqual(
                self.engine.detect_intent(message, "account-1"), expected, message
            )

    def test_new_intents_have_base_prompts(self):
        for intent in ("payment", "shipping", "aftersale"):
            self.assertIn(intent, self.engine.default_prompts)


class LocalRuleAuditTests(StagePlaybookTestBase):
    RULES = [{"id": 1, "text": "Pro 档位价格 100 元", "enabled": True}]

    def test_amounts_within_rules_pass_locally(self):
        price_rules = [dict(self.RULES[0])]
        audit = self.engine._local_rule_audit("好的 100元 拿去", self.RULES, price_rules)
        self.assertIsNotNone(audit)
        self.assertEqual(audit["violation_count"], 0)
        self.assertEqual(audit["results"][0]["status"], "followed")

    def test_foreign_amount_escalates(self):
        price_rules = [dict(self.RULES[0])]
        self.assertIsNone(
            self.engine._local_rule_audit("给你 80元 吧", self.RULES, price_rules)
        )

    def test_reply_without_amounts_passes(self):
        price_rules = [dict(self.RULES[0])]
        audit = self.engine._local_rule_audit("稍等我看看哦", self.RULES, price_rules)
        self.assertIsNotNone(audit)
        self.assertEqual(audit["violation_count"], 0)

    def test_non_price_rules_marked_unknown(self):
        rules = [{"id": 2, "text": "不要发外部链接", "enabled": True}]
        audit = self.engine._local_rule_audit("好的亲", rules, [])
        self.assertIsNotNone(audit)
        self.assertEqual(audit["results"][0]["status"], "unknown")
        self.assertEqual(audit["violation_count"], 0)

    def test_local_audit_mode_skips_llm_audit(self):
        with patch.object(self.engine, "_call_configured_model", return_value="好的 100元") as model, \
                patch.object(self.engine, "_audit_reply_against_rules") as llm_audit:
            checked = self.engine.generate_rule_checked_reply(
                settings={"model_name": "deepseek-chat", "base_url": "https://api.deepseek.com",
                          "api_key": "k"},
                cookie_id="account-1",
                messages=[{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
                buyer_message="多少钱",
                rules=self.RULES,
                knowledge_text="",
                max_tokens=100,
                temperature=0.7,
                audit_mode="local",
            )
        self.assertEqual(checked["reply"], "好的 100元")
        self.assertEqual(model.call_count, 1)
        llm_audit.assert_not_called()


class TrustedContextTests(StagePlaybookTestBase):
    def test_non_text_placeholder_returns_fixed_guidance(self):
        for message in ("[卡片消息]", "[图片]", "[语音]", "[视频]", "[图片][卡片消息]"):
            self.assertEqual(
                self.engine._non_text_guidance_reply(message, has_image_parts=False),
                AIReplyEngine.NON_TEXT_GUIDANCE_REPLY,
                message,
            )

    def test_pure_image_with_vision_goes_to_model(self):
        self.assertIsNone(
            self.engine._non_text_guidance_reply("[图片]", has_image_parts=True)
        )
        # 图片外还混有不可见内容时仍走固定引导。
        self.assertEqual(
            self.engine._non_text_guidance_reply("[图片][卡片消息]", has_image_parts=True),
            AIReplyEngine.NON_TEXT_GUIDANCE_REPLY,
        )

    def test_normal_text_never_intercepted(self):
        for message in ("发货了吗", "[图片]发货了吗", "我发了图片你看下", "[我发起了退款申请]"):
            self.assertIsNone(
                self.engine._non_text_guidance_reply(message, has_image_parts=False),
                message,
            )

    def test_generate_reply_guides_card_message_without_model_call(self):
        self._insert_order("order-a", "pending_ship")
        with patch.object(self.engine, "order_aware_enabled", return_value=True), \
                patch.object(self.engine, "_call_configured_model") as model:
            reply = self.engine.generate_reply(
                "[卡片消息]", {"title": "t", "price": "99", "desc": "d"},
                "chat-1", "account-1", "buyer-1", "item-1", skip_wait=True,
            )
        model.assert_not_called()
        self.assertEqual(reply, AIReplyEngine.NON_TEXT_GUIDANCE_REPLY)

    def test_ambiguous_clarifies_twice_then_escalates(self):
        self._insert_order("order-a", "pending_ship")
        self._insert_order("order-b", "shipped")
        replies = []
        with patch.object(self.engine, "order_aware_enabled", return_value=True), \
                patch.object(self.engine, "_call_configured_model") as model:
            for _ in range(3):
                replies.append(
                    self.engine.generate_reply(
                        "发货了吗", {"title": "t", "price": "99", "desc": "d"},
                        "chat-1", "account-1", "buyer-1", "item-1", skip_wait=True,
                    )
                )
        model.assert_not_called()
        self.assertEqual(
            replies,
            [
                AIReplyEngine.AMBIGUOUS_CLARIFY_REPLY,
                AIReplyEngine.AMBIGUOUS_CLARIFY_REPLY,
                AIReplyEngine.AMBIGUOUS_ESCALATE_REPLY,
            ],
        )

    def test_ambiguous_clarify_count_ignores_old_records(self):
        with self.db.lock:
            self.db.conn.execute(
                """
                INSERT INTO ai_conversations (
                    cookie_id, chat_id, user_id, item_id, role, content,
                    created_at
                ) VALUES ('account-1', 'chat-1', 'seller', 'item-1', 'assistant', ?,
                          datetime('now', '-3 days'))
                """,
                (AIReplyEngine.AMBIGUOUS_CLARIFY_REPLY,),
            )
            self.db.conn.commit()
        self.assertEqual(
            self.engine._ambiguous_clarify_count("chat-1", "account-1", "item-1"), 0
        )

    def test_ambiguous_assistant_replies_stay_in_trusted_context(self):
        self.engine.save_conversation(
            "chat-1", "account-1", "buyer-1", "item-1", "user", "发货了吗",
            source="buyer", delivery_state="received",
        )
        self.engine.save_conversation(
            "chat-1", "account-1", "buyer-1", "item-1", "assistant", "已经发您了哦",
            source="assistant_generated", delivery_state="ambiguous",
        )
        self.engine.save_conversation(
            "chat-1", "account-1", "buyer-1", "item-1", "assistant", "从未发出的草稿",
            source="assistant_generated", delivery_state="draft",
        )
        context = self.engine.get_conversation_context(
            "chat-1", "account-1", "item-1", include_metadata=True, trusted_only=True,
        )
        contents = [value["content"] for value in context]
        self.assertIn("已经发您了哦", contents)
        self.assertNotIn("从未发出的草稿", contents)


if __name__ == "__main__":
    unittest.main()
