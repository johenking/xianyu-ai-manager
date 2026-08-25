"""自动发货来源优先级测试。

商品级绑定是卖家的明确指定，必须压过按标题模糊匹配的关键词规则；未绑定的商品
继续走关键词兜底（用户选择保留两套并存）。绑定卡密停用时不得回落到别的卡密，
否则会把错误内容发给买家。
"""

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock

from db_manager import DBManager


class AutoDeliveryBindingPriorityTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.previous_key_file = os.environ.get("SYSTEM_SECRET_KEY_FILE")
        os.environ["SYSTEM_SECRET_KEY_FILE"] = str(self.root / ".system-key")
        self.db = DBManager(str(self.root / "delivery-priority.db"))

        self.assertTrue(self.db.create_user("seller", "s@example.test", "Strong-pass-2026!"))
        self.user = self.db.get_user_by_username("seller")
        with self.db.lock:
            self.db.conn.execute(
                "INSERT INTO cookies (id, value, user_id) VALUES (?, ?, ?)",
                ("acct-one", "unb=1; cookie2=x", self.user["id"]),
            )
            self.db.conn.commit()
        self.db.save_item_basic_info(
            "acct-one", "item-1", item_title="Claude 代充", item_detail="官网代充"
        )

        self.bound_card_id = self.db.create_card(
            "绑定卡密", "text", text_content="BOUND-CODE", user_id=self.user["id"]
        )
        self.keyword_card_id = self.db.create_card(
            "关键词卡密", "text", text_content="KEYWORD-CODE", user_id=self.user["id"]
        )
        self.db.create_delivery_rule(
            keyword="Claude",
            card_id=self.keyword_card_id,
            delivery_count=1,
            enabled=True,
            description="标题关键词兜底",
            user_id=self.user["id"],
        )

    def tearDown(self):
        self.db.close()
        if self.previous_key_file is None:
            os.environ.pop("SYSTEM_SECRET_KEY_FILE", None)
        else:
            os.environ["SYSTEM_SECRET_KEY_FILE"] = self.previous_key_file
        self.tempdir.cleanup()

    def _live(self):
        from XianyuAutoAsync import XianyuLive

        live = object.__new__(XianyuLive)
        live.cookie_id = "acct-one"
        live.order_status_handler = None
        live.save_item_info_to_db = AsyncMock()
        live.fetch_item_detail_from_api = AsyncMock(return_value="")
        live.save_item_detail_only = AsyncMock()
        live._safe_str = Mock(side_effect=lambda value: str(value))
        return live

    def _retitle(self, title, detail):
        # save_item_basic_info 是保守 upsert（不覆盖已有值），改标题要直接写库
        with self.db.lock:
            self.db.conn.execute(
                "UPDATE item_info SET item_title = ?, item_detail = ? "
                "WHERE cookie_id = ? AND item_id = ?",
                (title, detail, "acct-one", "item-1"),
            )
            self.db.conn.commit()

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

    def test_item_binding_wins_over_title_keyword_rule(self):
        self.db.set_item_delivery_card("acct-one", "item-1", self.bound_card_id, self.user["id"])
        self.assertEqual(self._deliver(), "BOUND-CODE")

    def test_unbound_item_still_falls_back_to_keyword_rule(self):
        self.assertEqual(self._deliver(), "KEYWORD-CODE")

    def test_disabled_bound_card_never_falls_back_to_another_resource(self):
        # 显式绑定失效时必须停住，避免按标题误发另一份资源。
        self.db.set_item_delivery_card("acct-one", "item-1", self.bound_card_id, self.user["id"])
        self.db.update_card(self.bound_card_id, enabled=False, user_id=self.user["id"])
        self.assertIsNone(self._deliver())

    def test_binding_delivers_even_when_no_keyword_rule_matches(self):
        # 商品标题改成与任何关键词都不匹配：这正是旧模糊匹配失灵的场景
        self._retitle("全新标题不含任何关键词", "详情")
        self.db.set_item_delivery_card("acct-one", "item-1", self.bound_card_id, self.user["id"])
        self.assertEqual(self._deliver(), "BOUND-CODE")

    def test_unbound_item_with_no_matching_keyword_delivers_nothing(self):
        self._retitle("全新标题不含任何关键词", "详情")
        self.assertIsNone(self._deliver())


if __name__ == "__main__":
    unittest.main()
