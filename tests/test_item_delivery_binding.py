"""商品级自动发货绑定测试。

历史实现只能靠"商品标题模糊匹配关键词规则"决定发什么卡密：改标题、多个商品共用
同一个词都会让匹配失灵或命中多条而整单跳过。现在商品可以直接绑定一张卡密，
绑定优先于关键词匹配，未绑定的商品仍回落关键词兜底；绑定卡密与邀请重置互斥。
"""

import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from db_manager import DBManager
import reply_server


class ItemDeliveryBindingTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.previous_key_file = os.environ.get("SYSTEM_SECRET_KEY_FILE")
        os.environ["SYSTEM_SECRET_KEY_FILE"] = str(self.root / ".system-key")
        self.db = DBManager(str(self.root / "delivery-binding.db"))

        self.assertTrue(self.db.create_user("seller", "s@example.test", "Strong-pass-2026!"))
        self.assertTrue(self.db.create_user("rival", "r@example.test", "Strong-pass-2026!"))
        self.user = self.db.get_user_by_username("seller")
        self.other_user = self.db.get_user_by_username("rival")

        with self.db.lock:
            self.db.conn.execute(
                "INSERT INTO cookies (id, value, user_id) VALUES (?, ?, ?)",
                ("acct-one", "unb=1; cookie2=x", self.user["id"]),
            )
            self.db.conn.commit()
        self.db.save_item_basic_info("acct-one", "item-1", item_title="Claude 代充")
        self.db.save_item_basic_info("acct-one", "item-2", item_title="Claude 代充")

        self.card_id = self.db.create_card(
            "会员卡密", "text", text_content="CODE-123456", user_id=self.user["id"]
        )
        self.foreign_card_id = self.db.create_card(
            "别人的卡密", "text", text_content="CODE-OTHER", user_id=self.other_user["id"]
        )

        self.original_db = reply_server.db_manager
        reply_server.db_manager = self.db
        reply_server.SESSION_TOKENS.clear()
        self.client = TestClient(reply_server.app, raise_server_exceptions=False)

    def tearDown(self):
        self.client.close()
        reply_server.SESSION_TOKENS.clear()
        reply_server.db_manager = self.original_db
        self.db.close()
        if self.previous_key_file is None:
            os.environ.pop("SYSTEM_SECRET_KEY_FILE", None)
        else:
            os.environ["SYSTEM_SECRET_KEY_FILE"] = self.previous_key_file
        self.tempdir.cleanup()

    def _headers(self):
        token, _ = reply_server.create_login_session(self.user)
        return {"Authorization": f"Bearer {token}"}

    def _bound_card_id(self, item_id):
        with self.db.lock:
            return self.db.conn.execute(
                "SELECT delivery_card_id FROM item_info WHERE cookie_id = ? AND item_id = ?",
                ("acct-one", item_id),
            ).fetchone()[0]

    def test_binding_returns_rule_shaped_like_keyword_match(self):
        self.assertTrue(
            self.db.set_item_delivery_card("acct-one", "item-1", self.card_id, self.user["id"])
        )
        rule = self.db.get_item_bound_delivery_rule("acct-one", "item-1", user_id=self.user["id"])
        self.assertIsNotNone(rule)
        self.assertEqual(rule["card_id"], self.card_id)
        self.assertEqual(rule["card_type"], "text")
        self.assertEqual(rule["text_content"], "CODE-123456")
        self.assertEqual(rule["source"], "item_binding")
        # 引擎按同一形状消费两种来源，绑定没有 delivery_rules 行
        self.assertIsNone(rule["id"])
        self.assertTrue(rule["card_enabled"])

    def test_binding_is_scoped_to_the_owning_user(self):
        self.assertFalse(
            self.db.set_item_delivery_card("acct-one", "item-1", self.foreign_card_id, self.user["id"])
        )
        self.assertIsNone(self._bound_card_id("item-1"))
        # 即便库里被写坏，读取也按归属过滤
        self.db.set_item_delivery_card("acct-one", "item-1", self.card_id, self.user["id"])
        self.assertIsNone(
            self.db.get_item_bound_delivery_rule(
                "acct-one", "item-1", user_id=self.other_user["id"]
            )
        )

    def test_disabled_card_is_not_delivered(self):
        self.db.set_item_delivery_card("acct-one", "item-1", self.card_id, self.user["id"])
        self.db.update_card(self.card_id, enabled=False, user_id=self.user["id"])
        self.assertIsNone(
            self.db.get_item_bound_delivery_rule("acct-one", "item-1", user_id=self.user["id"])
        )

    def test_binding_a_card_turns_off_invite_fulfillment(self):
        self.assertTrue(
            self.db.update_item_invite_auto_fulfillment_status("acct-one", "item-1", True)
        )
        self.assertTrue(
            self.db.set_item_delivery_card("acct-one", "item-1", self.card_id, self.user["id"])
        )
        self.assertFalse(
            self.db.is_invite_auto_fulfillment_enabled("acct-one", "item-1")
        )

    def test_clearing_the_binding_keeps_invite_choice_untouched(self):
        self.db.update_item_invite_auto_fulfillment_status("acct-one", "item-1", True)
        self.assertTrue(
            self.db.set_item_delivery_card("acct-one", "item-1", None, self.user["id"])
        )
        self.assertIsNone(self._bound_card_id("item-1"))
        self.assertTrue(self.db.is_invite_auto_fulfillment_enabled("acct-one", "item-1"))

    def test_api_binds_clears_and_rejects_foreign_account(self):
        headers = self._headers()
        bind = self.client.put(
            "/items/acct-one/item-1/delivery-binding",
            json={"card_id": self.card_id},
            headers=headers,
        )
        self.assertEqual(bind.status_code, 200, bind.text)
        self.assertEqual(self._bound_card_id("item-1"), self.card_id)

        clear = self.client.put(
            "/items/acct-one/item-1/delivery-binding",
            json={"card_id": None},
            headers=headers,
        )
        self.assertEqual(clear.status_code, 200, clear.text)
        self.assertIsNone(self._bound_card_id("item-1"))

        foreign = self.client.put(
            "/items/not-my-account/item-1/delivery-binding",
            json={"card_id": self.card_id},
            headers=headers,
        )
        self.assertEqual(foreign.status_code, 403)

    def test_api_rejects_a_card_owned_by_another_user(self):
        response = self.client.put(
            "/items/acct-one/item-1/delivery-binding",
            json={"card_id": self.foreign_card_id},
            headers=self._headers(),
        )
        self.assertEqual(response.status_code, 404)
        self.assertIsNone(self._bound_card_id("item-1"))

    def test_batch_binds_several_items_at_once(self):
        response = self.client.post(
            "/items/delivery-bindings/batch",
            json={
                "cookie_id": "acct-one",
                "item_ids": ["item-1", "item-2", "missing-item"],
                "card_id": self.card_id,
            },
            headers=self._headers(),
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["updated"], 2)
        self.assertEqual(payload["failed"], ["missing-item"])
        self.assertEqual(self._bound_card_id("item-1"), self.card_id)
        self.assertEqual(self._bound_card_id("item-2"), self.card_id)

    def test_batch_requires_at_least_one_item(self):
        response = self.client.post(
            "/items/delivery-bindings/batch",
            json={"cookie_id": "acct-one", "item_ids": [], "card_id": self.card_id},
            headers=self._headers(),
        )
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
