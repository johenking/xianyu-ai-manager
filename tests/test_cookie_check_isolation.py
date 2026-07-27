"""/cookies/check 租户隔离测试。

该端点原先调用 get_all_cookies() 不带 user_id，把全站所有租户的
账号数量（totalCount/enabledCount/validCount）泄露给任意登录用户，
且允许匿名访问。要求：
- 登录用户只统计自己名下的账号
- 匿名访问一律返回 0，不触达数据库
"""

import os
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

from db_manager import DBManager
import cookie_manager
import reply_server


class _StubCookieManager:
    """最小桩：所有账号视为启用"""

    def get_cookie_status(self, cookie_id: str) -> bool:
        return True


class CookieCheckIsolationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.previous_key_file = os.environ.get("SYSTEM_SECRET_KEY_FILE")
        os.environ["SYSTEM_SECRET_KEY_FILE"] = str(self.root / ".system-key")
        self.db = DBManager(str(self.root / "cookie-check.db"))
        self.assertTrue(
            self.db.create_user("seller-one", "seller-one@example.test", "Strong-pass-2026!")
        )
        self.assertTrue(
            self.db.create_user("seller-two", "seller-two@example.test", "Strong-pass-2026!")
        )
        self.user_one = self.db.get_user_by_username("seller-one")
        self.user_two = self.db.get_user_by_username("seller-two")

        long_value = "c" * 80  # 长度 > 50 视为有效
        with self.db.lock:
            cursor = self.db.conn.cursor()
            cursor.execute(
                "INSERT INTO cookies (id, value, user_id) VALUES ('acct-one', ?, ?)",
                (long_value, self.user_one["id"]),
            )
            cursor.execute(
                "INSERT INTO cookies (id, value, user_id) VALUES ('acct-two-a', ?, ?)",
                (long_value, self.user_two["id"]),
            )
            cursor.execute(
                "INSERT INTO cookies (id, value, user_id) VALUES ('acct-two-b', ?, ?)",
                (long_value, self.user_two["id"]),
            )
            self.db.conn.commit()

        self.original_db = reply_server.db_manager
        reply_server.db_manager = self.db
        reply_server.SESSION_TOKENS.clear()
        self.original_manager = cookie_manager.manager
        cookie_manager.manager = _StubCookieManager()
        self.client = TestClient(reply_server.app, raise_server_exceptions=False)

    def tearDown(self):
        self.client.close()
        cookie_manager.manager = self.original_manager
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

    def test_counts_scoped_to_current_user(self):
        resp = self.client.get("/cookies/check", headers=self.headers_for(self.user_one))
        self.assertEqual(resp.status_code, 200, resp.text)
        payload = resp.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["totalCount"], 1)
        self.assertEqual(payload["enabledCount"], 1)
        self.assertEqual(payload["validCount"], 1)
        self.assertTrue(payload["hasValidCookies"])

        resp_two = self.client.get("/cookies/check", headers=self.headers_for(self.user_two))
        payload_two = resp_two.json()
        self.assertEqual(payload_two["totalCount"], 2)
        self.assertEqual(payload_two["validCount"], 2)

    def test_anonymous_gets_zero_counts(self):
        resp = self.client.get("/cookies/check")
        self.assertEqual(resp.status_code, 200, resp.text)
        payload = resp.json()
        self.assertTrue(payload["success"])
        self.assertFalse(payload["hasValidCookies"])
        self.assertEqual(payload["totalCount"], 0)
        self.assertEqual(payload["enabledCount"], 0)
        self.assertEqual(payload["validCount"], 0)


if __name__ == "__main__":
    unittest.main()
