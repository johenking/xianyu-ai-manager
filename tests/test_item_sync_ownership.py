"""POST /items/get-all-from-account 归属校验测试。

该端点原先只做 require_auth，不校验 cookie_id 归属：任意登录用户
传他人 cookie_id 即可用他人的登录凭证向闲鱼发起请求（凭证滥用）。
要求：非本人账号一律 403，且绝不触发 XianyuLive 实例化。
"""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from db_manager import DBManager
import XianyuAutoAsync
import reply_server


class ItemSyncOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.previous_key_file = os.environ.get("SYSTEM_SECRET_KEY_FILE")
        os.environ["SYSTEM_SECRET_KEY_FILE"] = str(self.root / ".system-key")
        self.db = DBManager(str(self.root / "item-sync.db"))
        self.assertTrue(
            self.db.create_user("seller-one", "seller-one@example.test", "Strong-pass-2026!")
        )
        self.assertTrue(
            self.db.create_user("seller-two", "seller-two@example.test", "Strong-pass-2026!")
        )
        self.user_one = self.db.get_user_by_username("seller-one")
        self.user_two = self.db.get_user_by_username("seller-two")
        with self.db.lock:
            cursor = self.db.conn.cursor()
            cursor.execute(
                "INSERT INTO cookies (id, value, user_id) VALUES ('acct-one', 'unb=111; c=1', ?)",
                (self.user_one["id"],),
            )
            self.db.conn.commit()
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

    def headers_for(self, user):
        token, _ = reply_server.create_login_session(user)
        return {"Authorization": f"Bearer {token}"}

    def test_other_tenant_cookie_rejected_without_touching_xianyu(self):
        with patch("XianyuAutoAsync.XianyuLive") as live_cls:
            resp = self.client.post(
                "/items/get-all-from-account",
                headers=self.headers_for(self.user_two),
                json={"cookie_id": "acct-one"},
            )
        self.assertEqual(resp.status_code, 403, resp.text)
        live_cls.assert_not_called()

    def test_owner_can_sync_own_account(self):
        instance = MagicMock()
        instance.get_all_items = AsyncMock(
            return_value={"total_count": 0, "total_pages": 1, "total_saved": 0}
        )
        instance.close_session = AsyncMock()
        with patch("XianyuAutoAsync.XianyuLive", return_value=instance) as live_cls:
            resp = self.client.post(
                "/items/get-all-from-account",
                headers=self.headers_for(self.user_one),
                json={"cookie_id": "acct-one"},
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertTrue(resp.json().get("success"))
        live_cls.assert_called_once_with(
            "unb=111; c=1",
            "acct-one",
            register_instance=False,
        )

    def test_non_listener_instance_does_not_replace_registered_listener(self):
        sentinel = object()
        previous = XianyuAutoAsync.XianyuLive._instances.get("acct-temp")
        XianyuAutoAsync.XianyuLive._instances["acct-temp"] = sentinel
        fake_db = MagicMock()
        fake_db.get_cookie_details.return_value = {}
        fake_db.get_cookie_refresh_settings.return_value = {
            "enabled": False,
            "interval_minutes": 1440,
        }
        fake_db.get_account_session_refresh.return_value = {}
        try:
            with patch.object(XianyuAutoAsync, "db_manager", fake_db), patch.object(
                XianyuAutoAsync.XianyuLive, "_register_instance"
            ) as register:
                XianyuAutoAsync.XianyuLive(
                    "unb=111; c=1",
                    "acct-temp",
                    register_instance=False,
                )
            register.assert_not_called()
            self.assertIs(XianyuAutoAsync.XianyuLive.get_instance("acct-temp"), sentinel)
        finally:
            if previous is None:
                XianyuAutoAsync.XianyuLive._instances.pop("acct-temp", None)
            else:
                XianyuAutoAsync.XianyuLive._instances["acct-temp"] = previous


if __name__ == "__main__":
    unittest.main()
