"""SecureConfirm._get_real_item_id 租户隔离测试。

原实现在当前账号没有商品时兜底调用 get_all_items()，
拿其他租户的商品 ID 去请求闲鱼接口——跨租户数据滥用。
要求：只允许使用当前账号自己的商品，没有就返回 None。
"""

import asyncio
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from db_manager import DBManager
from secure_confirm_decrypted import SecureConfirm


class SecureConfirmItemScopeTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.previous_key_file = os.environ.get("SYSTEM_SECRET_KEY_FILE")
        os.environ["SYSTEM_SECRET_KEY_FILE"] = str(self.root / ".system-key")
        self.db = DBManager(str(self.root / "confirm.db"))
        self.assertTrue(
            self.db.create_user("seller-one", "seller-one@example.test", "Strong-pass-2026!")
        )
        self.assertTrue(
            self.db.create_user("seller-two", "seller-two@example.test", "Strong-pass-2026!")
        )
        user_one = self.db.get_user_by_username("seller-one")
        user_two = self.db.get_user_by_username("seller-two")
        with self.db.lock:
            cursor = self.db.conn.cursor()
            cursor.execute(
                "INSERT INTO cookies (id, value, user_id) VALUES ('acct-one', 'cookie-1', ?)",
                (user_one["id"],),
            )
            cursor.execute(
                "INSERT INTO cookies (id, value, user_id) VALUES ('acct-two', 'cookie-2', ?)",
                (user_two["id"],),
            )
            # 只有 acct-two（他租户）有商品
            cursor.execute(
                "INSERT INTO item_info (cookie_id, item_id, item_title) "
                "VALUES ('acct-two', 'item-belongs-to-two', '他人商品')",
            )
            self.db.conn.commit()

    def tearDown(self):
        self.db.close()
        if self.previous_key_file is None:
            os.environ.pop("SYSTEM_SECRET_KEY_FILE", None)
        else:
            os.environ["SYSTEM_SECRET_KEY_FILE"] = self.previous_key_file
        self.tempdir.cleanup()

    def _run(self, coro):
        return asyncio.run(coro)

    def test_no_fallback_to_other_tenant_items(self):
        confirm = SecureConfirm(session=None, cookies_str="", cookie_id="acct-one")
        with patch("db_manager.db_manager", self.db):
            item_id = self._run(confirm._get_real_item_id())
        self.assertIsNone(item_id)

    def test_uses_own_item_when_available(self):
        with self.db.lock:
            self.db.conn.execute(
                "INSERT INTO item_info (cookie_id, item_id, item_title) "
                "VALUES ('acct-one', 'item-belongs-to-one', '自己的商品')",
            )
            self.db.conn.commit()
        confirm = SecureConfirm(session=None, cookies_str="", cookie_id="acct-one")
        with patch("db_manager.db_manager", self.db):
            item_id = self._run(confirm._get_real_item_id())
        self.assertEqual(item_id, "item-belongs-to-one")


if __name__ == "__main__":
    unittest.main()
