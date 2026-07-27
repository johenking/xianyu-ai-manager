"""发货规则租户隔离测试。

覆盖：
- get_cookie_user_id：cookie → 归属用户
- get_delivery_rules_by_keyword / get_delivery_rules_by_keyword_and_spec
  必须按 user_id 过滤，缺省 user_id 直接失败（fail-closed），
  防止用户 A 的商品匹配到用户 B 的规则并把 B 的卡券内容发出去
- create_delivery_rule / update_delivery_rule 绑定卡券时校验卡券归属
- POST/PUT /delivery-rules 端点在绑定他人卡券时返回 400
"""

import os
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

from db_manager import DBManager
import reply_server


class DeliveryRuleIsolationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.db_path = self.root / "delivery.db"
        self.previous_key_file = os.environ.get("SYSTEM_SECRET_KEY_FILE")
        os.environ["SYSTEM_SECRET_KEY_FILE"] = str(self.root / ".system-key")
        self.db = DBManager(str(self.db_path))
        self.assertTrue(
            self.db.create_user("seller-one", "seller-one@example.test", "Strong-pass-2026!")
        )
        self.assertTrue(
            self.db.create_user("seller-two", "seller-two@example.test", "Strong-pass-2026!")
        )
        self.user_one = self.db.get_user_by_username("seller-one")
        self.user_two = self.db.get_user_by_username("seller-two")
        self.original_db = reply_server.db_manager
        reply_server.db_manager = self.db
        reply_server.SESSION_TOKENS.clear()
        self.client = TestClient(reply_server.app, raise_server_exceptions=False)

        with self.db.lock:
            cursor = self.db.conn.cursor()
            cursor.execute(
                "INSERT INTO cookies (id, value, user_id) VALUES ('account-1', 'cookie-1', ?)",
                (self.user_one["id"],),
            )
            cursor.execute(
                "INSERT INTO cookies (id, value, user_id) VALUES ('account-2', 'cookie-2', ?)",
                (self.user_two["id"],),
            )
            self.db.conn.commit()

        # 两个用户各一张普通卡券 + 各一条命中同一关键字的规则
        self.card_one = self._seed_card("卡券一", self.user_one["id"])
        self.card_two = self._seed_card("卡券二", self.user_two["id"])
        self.rule_one = self._seed_rule("激活码", self.card_one, self.user_one["id"])
        self.rule_two = self._seed_rule("激活码", self.card_two, self.user_two["id"])
        # 用户二独有的多规格卡券与规则
        self.spec_card_two = self._seed_card(
            "多规格卡券二", self.user_two["id"], is_multi_spec=1,
            spec_name="版本", spec_value="旗舰版",
        )
        self.spec_rule_two = self._seed_rule("激活码", self.spec_card_two, self.user_two["id"])

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

    def headers_for(self, user):
        token, _ = reply_server.create_login_session(user)
        return {"Authorization": f"Bearer {token}"}

    def _seed_card(self, name, user_id, is_multi_spec=0, spec_name=None, spec_value=None):
        with self.db.lock:
            cursor = self.db.conn.cursor()
            cursor.execute(
                """
                INSERT INTO cards (name, type, text_content, enabled, user_id,
                                   is_multi_spec, spec_name, spec_value)
                VALUES (?, 'text', ?, 1, ?, ?, ?, ?)
                """,
                (name, f"{name}的内容", user_id, is_multi_spec, spec_name, spec_value),
            )
            self.db.conn.commit()
            return cursor.lastrowid

    def _seed_rule(self, keyword, card_id, user_id):
        with self.db.lock:
            cursor = self.db.conn.cursor()
            cursor.execute(
                """
                INSERT INTO delivery_rules (keyword, card_id, enabled, user_id)
                VALUES (?, ?, 1, ?)
                """,
                (keyword, card_id, user_id),
            )
            self.db.conn.commit()
            return cursor.lastrowid

    # ---------------- get_cookie_user_id ----------------

    def test_get_cookie_user_id_returns_owner(self):
        self.assertEqual(self.db.get_cookie_user_id("account-1"), self.user_one["id"])
        self.assertEqual(self.db.get_cookie_user_id("account-2"), self.user_two["id"])

    def test_get_cookie_user_id_missing_cookie_returns_none(self):
        self.assertIsNone(self.db.get_cookie_user_id("ghost-account"))

    # ---------------- 关键字匹配隔离 ----------------

    def test_keyword_match_requires_user_id(self):
        with self.assertRaises(ValueError):
            self.db.get_delivery_rules_by_keyword("正版激活码秒发")

    def test_keyword_match_scoped_to_user(self):
        rules = self.db.get_delivery_rules_by_keyword(
            "正版激活码秒发", user_id=self.user_one["id"]
        )
        self.assertTrue(rules)
        self.assertEqual({r["card_id"] for r in rules}, {self.card_one})

    def test_keyword_and_spec_match_requires_user_id(self):
        with self.assertRaises(ValueError):
            self.db.get_delivery_rules_by_keyword_and_spec("正版激活码秒发", "版本", "旗舰版")

    def test_keyword_and_spec_match_scoped_to_user(self):
        # 用户一没有多规格卡券：多规格分支不应命中用户二的规则，
        # 兜底分支也只能命中用户一自己的普通规则
        rules = self.db.get_delivery_rules_by_keyword_and_spec(
            "正版激活码秒发", "版本", "旗舰版", user_id=self.user_one["id"]
        )
        self.assertEqual({r["card_id"] for r in rules}, {self.card_one})

        # 用户二应命中自己的多规格规则
        rules_two = self.db.get_delivery_rules_by_keyword_and_spec(
            "正版激活码秒发", "版本", "旗舰版", user_id=self.user_two["id"]
        )
        self.assertTrue(rules_two)
        self.assertEqual({r["card_id"] for r in rules_two}, {self.spec_card_two})

    # ---------------- 卡券绑定归属校验 ----------------

    def test_create_rule_rejects_cross_tenant_card(self):
        with self.assertRaises(ValueError):
            self.db.create_delivery_rule(
                keyword="越权绑定", card_id=self.card_two, user_id=self.user_one["id"]
            )

    def test_update_rule_rejects_cross_tenant_card(self):
        with self.assertRaises(ValueError):
            self.db.update_delivery_rule(
                rule_id=self.rule_one, card_id=self.card_two, user_id=self.user_one["id"]
            )

    def test_post_rule_endpoint_blocks_cross_tenant_card(self):
        resp = self.client.post(
            "/delivery-rules",
            headers=self.headers_for(self.user_one),
            json={"keyword": "端点越权", "card_id": self.card_two},
        )
        self.assertEqual(resp.status_code, 400, resp.text)

    def test_put_rule_endpoint_blocks_cross_tenant_card(self):
        resp = self.client.put(
            f"/delivery-rules/{self.rule_one}",
            headers=self.headers_for(self.user_one),
            json={"keyword": "激活码", "card_id": self.card_two},
        )
        self.assertEqual(resp.status_code, 400, resp.text)


if __name__ == "__main__":
    unittest.main()
