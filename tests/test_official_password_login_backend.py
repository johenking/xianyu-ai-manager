import asyncio
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import reply_server
from db_manager import DBManager
from utils.xianyu_official_login import OfficialLoginResult


class FakeCookieManager:
    def __init__(self):
        self.update_calls = []
        self.add_calls = []

    def update_cookie(self, account_id, cookies, save_to_db=True):
        self.update_calls.append((account_id, cookies, save_to_db))

    def add_cookie(self, account_id, cookies, user_id=None):
        self.add_calls.append((account_id, cookies, user_id))


class SuccessfulOfficialLoginService:
    def login_with_password(self, **kwargs):
        del kwargs
        return OfficialLoginResult(
            status="success",
            cookies={
                "unb": "stable-unb",
                "cookie2": "new-cookie",
                "_m_h5_tk": "new-token",
            },
            unb="stable-unb",
            used_password=True,
        )

    @staticmethod
    def cookies_to_string(cookies):
        return "; ".join(f"{name}={value}" for name, value in cookies.items())


class OfficialPasswordLoginBackendTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.db = DBManager(self.db_path)
        with self.db.lock:
            self.db.conn.execute(
                """
                INSERT INTO cookies (
                    id, value, user_id, auto_confirm, remark, pause_duration,
                    username, show_browser, xianyu_unb
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "legacy-account",
                    "unb=stable-unb; cookie2=old-cookie",
                    1,
                    0,
                    "保留备注",
                    23,
                    "old-login",
                    0,
                    "stable-unb",
                ),
            )
            self.db.conn.execute(
                "INSERT INTO keywords (cookie_id, keyword, reply) VALUES (?, ?, ?)",
                ("legacy-account", "价格", "详情页价格为准"),
            )
            self.db.conn.commit()
        reply_server.password_login_sessions.clear()

    def tearDown(self):
        reply_server.password_login_sessions.clear()
        self.db.conn.close()
        os.unlink(self.db_path)

    async def test_legacy_account_id_is_accepted_but_not_used_or_stored(self):
        execute_login = AsyncMock(return_value=None)
        with patch.object(reply_server, "_execute_password_login", execute_login):
            response = await reply_server.password_login(
                {
                    "account_id": "client-supplied-wrong-id",
                    "account": "seller@example.com",
                    "password": "secret",
                    "show_browser": True,
                },
                current_user={"user_id": 1, "username": "admin"},
            )
            await asyncio.sleep(0)

        self.assertTrue(response["success"])
        session = reply_server.password_login_sessions[response["session_id"]]
        self.assertNotIn("account_id", session)
        self.assertNotIn("password", session)
        execute_login.assert_awaited_once()
        call_args = execute_login.await_args.args
        self.assertEqual(call_args[1], "seller@example.com")
        self.assertEqual(call_args[2], "secret")
        self.assertNotIn("client-supplied-wrong-id", call_args)

    async def test_same_unb_preserves_account_data_encrypts_password_and_restarts_once(self):
        session_id = "login-session"
        reply_server.password_login_sessions[session_id] = {
            "account": "seller@example.com",
            "show_browser": False,
            "status": "processing",
            "screenshot_path": None,
            "worker": None,
            "task": None,
            "timestamp": 1,
            "user_id": 1,
            "error_code": "",
        }
        manager = FakeCookieManager()

        with (
            patch.object(reply_server, "db_manager", self.db),
            patch.object(reply_server.cookie_manager, "manager", manager),
            patch(
                "utils.xianyu_official_login.XianyuOfficialLoginService",
                SuccessfulOfficialLoginService,
            ),
        ):
            await reply_server._execute_password_login(
                session_id,
                "seller@example.com",
                "new-secret",
                False,
                1,
                {"user_id": 1, "username": "admin"},
            )

        session = reply_server.password_login_sessions[session_id]
        self.assertEqual(session["status"], "success")
        self.assertEqual(session["account_id"], "legacy-account")
        self.assertFalse(session["is_new_account"])
        self.assertEqual(len(manager.update_calls), 1)
        self.assertEqual(manager.add_calls, [])

        details = self.db.get_cookie_details("legacy-account")
        self.assertEqual(details["remark"], "保留备注")
        self.assertEqual(details["pause_duration"], 23)
        self.assertEqual(details["username"], "seller@example.com")
        self.assertEqual(details["password"], "new-secret")
        with self.db.lock:
            password, encrypted, keyword_count = self.db.conn.execute(
                """
                SELECT c.password, c.password_encrypted,
                       (SELECT COUNT(*) FROM keywords k WHERE k.cookie_id = c.id)
                FROM cookies c WHERE c.id = ?
                """,
                ("legacy-account",),
            ).fetchone()
        self.assertEqual(password, "")
        self.assertTrue(encrypted)
        self.assertNotIn("new-secret", encrypted)
        self.assertEqual(keyword_count, 1)

    async def test_account_details_api_never_returns_password_material(self):
        self.db.update_cookie_account_info("legacy-account", password="new-secret")
        with (
            patch.object(reply_server, "db_manager", self.db),
            patch("db_manager.db_manager", self.db),
        ):
            result = reply_server.get_cookie_account_details(
                "legacy-account",
                current_user={"user_id": 1, "username": "admin"},
            )

        self.assertTrue(result["has_login_password"])
        self.assertNotIn("password", result)
        self.assertNotIn("password_encrypted", result)


if __name__ == "__main__":
    unittest.main()
