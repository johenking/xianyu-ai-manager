"""代理自动发货回退主站共享规则测试。

产品决策（2026-08-29）：代理（子账号）没配发货规则时，自动匹配主站
（admin）的发货规则并消耗主站卡密库存，代理零配置开箱即用；
代理自有规则永远优先；商品显式选择（绑定/关闭）不回退。
"""

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock

from db_manager import DBManager


class DeliverySiteFallbackTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.previous_key_file = os.environ.get("SYSTEM_SECRET_KEY_FILE")
        os.environ["SYSTEM_SECRET_KEY_FILE"] = str(self.root / ".system-key")
        self.db = DBManager(str(self.root / "site-fallback.db"))

        # user 1 是 DBManager 初始化自动创建的 admin（站级共享属主）
        self.admin_id = self.db.get_site_admin_user_id()
        self.assertIsNotNone(self.admin_id)
        self.assertTrue(self.db.create_user("agent", "agent@example.test", "Strong-pass-2026!"))
        self.agent = self.db.get_user_by_username("agent")
        with self.db.lock:
            self.db.conn.execute(
                "INSERT INTO cookies (id, value, user_id) VALUES (?, ?, ?)",
                ("agent-acct", "unb=1; cookie2=x", self.agent["id"]),
            )
            self.db.conn.commit()
        self.db.save_item_basic_info(
            "agent-acct", "item-1", item_title="Claude 代充", item_detail="官网代充"
        )

        self.admin_card_id = self.db.create_card(
            "主站卡密", "text", text_content="SITE-CODE", user_id=self.admin_id
        )

    def tearDown(self):
        self.db.close()
        if self.previous_key_file is None:
            os.environ.pop("SYSTEM_SECRET_KEY_FILE", None)
        else:
            os.environ["SYSTEM_SECRET_KEY_FILE"] = self.previous_key_file
        self.tempdir.cleanup()

    def _seed_admin_rule(self, keyword="Claude", enabled=True):
        return self.db.create_delivery_rule(
            keyword=keyword,
            card_id=self.admin_card_id,
            delivery_count=1,
            enabled=enabled,
            description="主站共享规则",
            user_id=self.admin_id,
        )

    def _live(self):
        from XianyuAutoAsync import XianyuLive

        live = object.__new__(XianyuLive)
        live.cookie_id = "agent-acct"
        live.order_status_handler = None
        live.save_item_info_to_db = AsyncMock()
        live.fetch_item_detail_from_api = AsyncMock(return_value="")
        live.save_item_detail_only = AsyncMock()
        live._safe_str = Mock(side_effect=lambda value: str(value))
        return live

    def _deliver(self):
        live = self._live()
        return asyncio.run(
            live._auto_delivery(
                "item-1",
                item_title="Claude 代充",
                order_id="order-1",
                send_user_id="buyer-1",
                database=self.db,
            )
        )

    def test_agent_without_rules_falls_back_to_site_rule(self):
        self._seed_admin_rule()
        self.assertEqual(self._deliver(), "SITE-CODE")

    def test_site_rule_delivery_increments_admin_rule_counter(self):
        rule_id = self._seed_admin_rule()
        self.assertEqual(self._deliver(), "SITE-CODE")
        rule = self.db.get_delivery_rule_by_id(rule_id, self.admin_id)
        self.assertEqual(rule["delivery_times"], 1)

    def test_agent_own_rule_wins_over_site_rule(self):
        self._seed_admin_rule()
        own_card = self.db.create_card(
            "代理卡密", "text", text_content="AGENT-CODE", user_id=self.agent["id"]
        )
        self.db.create_delivery_rule(
            keyword="Claude",
            card_id=own_card,
            delivery_count=1,
            enabled=True,
            description="代理自有规则",
            user_id=self.agent["id"],
        )
        self.assertEqual(self._deliver(), "AGENT-CODE")

    def test_no_rules_anywhere_returns_none(self):
        self.assertIsNone(self._deliver())

    def test_disabled_site_rule_is_not_used(self):
        self._seed_admin_rule(enabled=False)
        self.assertIsNone(self._deliver())

    def test_admin_own_account_does_not_double_query(self):
        # admin 自己的账号无规则时不应再按 admin 查一遍（无限自回退无意义）
        calls = []
        original = self.db.get_delivery_rules_by_keyword

        def counting(keyword, user_id=None):
            calls.append(user_id)
            return original(keyword, user_id=user_id)

        with self.db.lock:
            self.db.conn.execute(
                "INSERT INTO cookies (id, value, user_id) VALUES (?, ?, ?)",
                ("admin-acct", "unb=2; cookie2=y", self.admin_id),
            )
            self.db.conn.commit()
        self.db.save_item_basic_info(
            "admin-acct", "item-9", item_title="Claude 代充", item_detail="官网代充"
        )
        live = self._live()
        live.cookie_id = "admin-acct"
        self.db.get_delivery_rules_by_keyword = counting
        try:
            result = asyncio.run(
                live._auto_delivery(
                    "item-9",
                    item_title="Claude 代充",
                    order_id="order-9",
                    send_user_id="buyer-9",
                    database=self.db,
                )
            )
        finally:
            self.db.get_delivery_rules_by_keyword = original
        self.assertIsNone(result)
        self.assertEqual(calls, [self.admin_id])

    def test_explicit_off_item_does_not_fall_back(self):
        # 代理商品显式关闭自动发货：主站规则命中也必须尊重显式选择
        self._seed_admin_rule()
        outcome = self.db.set_item_delivery_mode(
            "agent-acct", "item-1", "off", self.agent["id"]
        )
        self.assertNotEqual(outcome.get("outcome"), "failed")
        self.assertIsNone(self._deliver())

    def test_get_site_admin_user_id_matches_admin(self):
        self.assertEqual(self.db.get_site_admin_user_id(), 1)


if __name__ == "__main__":
    unittest.main()
