"""`/send-message` 鉴权契约：登录态优先、旧秘钥必须绑定用户、发信只限本人账号。"""

import inspect
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import XianyuAutoAsync
from db_manager import DBManager
import reply_server


class _FakeSocket:
    closed = False


class _FakeLive:
    def __init__(self):
        self.ws = _FakeSocket()
        self.send_msg = AsyncMock()


class SendMessageScopeTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.previous_key_file = os.environ.get("SYSTEM_SECRET_KEY_FILE")
        os.environ["SYSTEM_SECRET_KEY_FILE"] = str(self.root / ".system-key")
        self.db = DBManager(str(self.root / "send-message.db"))
        self.assertTrue(
            self.db.create_user("seller-one", "one@example.test", "Strong-pass-2026!")
        )
        self.assertTrue(
            self.db.create_user("seller-two", "two@example.test", "Strong-pass-2026!")
        )
        self.user_one = self.db.get_user_by_username("seller-one")
        self.user_two = self.db.get_user_by_username("seller-two")
        with self.db.lock:
            self.db.conn.executemany(
                "INSERT INTO cookies (id, value, user_id) VALUES (?, ?, ?)",
                (
                    ("acct-one", "unb=1; cookie2=x", self.user_one["id"]),
                    ("acct-two", "unb=2; cookie2=x", self.user_two["id"]),
                ),
            )
            self.db.conn.commit()

        self.original_db = reply_server.db_manager
        reply_server.db_manager = self.db
        reply_server.SESSION_TOKENS.clear()

        self.live = _FakeLive()
        self._live_patch = patch.object(
            XianyuAutoAsync.XianyuLive,
            "get_instance",
            staticmethod(lambda _cookie_id: self.live),
        )
        self._live_patch.start()
        self.client = TestClient(reply_server.app, raise_server_exceptions=False)

    def tearDown(self):
        self.client.close()
        self._live_patch.stop()
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

    def payload(self, cookie_id, api_key=""):
        return {
            "api_key": api_key,
            "cookie_id": cookie_id,
            "chat_id": "chat-1",
            "to_user_id": "buyer-1",
            "message": "你好",
        }

    # ---------------- 登录态通道 ----------------

    def test_logged_in_owner_can_send_on_own_account(self):
        response = self.client.post(
            "/send-message",
            json=self.payload("acct-one"),
            headers=self.headers_for(self.user_one),
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["success"])
        self.live.send_msg.assert_awaited_once()

    def test_logged_in_user_cannot_send_on_foreign_account(self):
        response = self.client.post(
            "/send-message",
            json=self.payload("acct-two"),
            headers=self.headers_for(self.user_one),
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["success"])
        self.assertEqual(response.json()["message"], "账号不存在或无权操作")
        self.live.send_msg.assert_not_awaited()

    def test_anonymous_request_without_key_is_rejected(self):
        response = self.client.post("/send-message", json=self.payload("acct-one"))

        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["success"])
        self.live.send_msg.assert_not_awaited()

    # ---------------- 旧共享秘钥通道 ----------------

    def test_shared_secret_without_bound_user_is_rejected(self):
        self.db.set_system_setting("qq_reply_secret_key", "legacy-secret-2026")

        response = self.client.post(
            "/send-message",
            json=self.payload("acct-one", api_key="legacy-secret-2026"),
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["success"])
        self.assertEqual(response.json()["message"], "身份验证失败")
        self.live.send_msg.assert_not_awaited()

    def test_bound_shared_secret_sends_only_on_bound_user_accounts(self):
        self.db.set_system_setting("qq_reply_secret_key", "legacy-secret-2026")
        self.db.set_system_setting(
            "qq_reply_secret_user_id", str(self.user_one["id"])
        )

        allowed = self.client.post(
            "/send-message",
            json=self.payload("acct-one", api_key="legacy-secret-2026"),
        )
        self.assertTrue(allowed.json()["success"], allowed.text)
        self.live.send_msg.assert_awaited_once()

        self.live.send_msg.reset_mock()
        foreign = self.client.post(
            "/send-message",
            json=self.payload("acct-two", api_key="legacy-secret-2026"),
        )
        self.assertFalse(foreign.json()["success"])
        self.assertEqual(foreign.json()["message"], "账号不存在或无权操作")
        self.live.send_msg.assert_not_awaited()

    def test_shared_secret_bound_to_disabled_user_is_rejected(self):
        self.db.set_system_setting("qq_reply_secret_key", "legacy-secret-2026")
        self.db.set_system_setting(
            "qq_reply_secret_user_id", str(self.user_one["id"])
        )
        with self.db.lock:
            self.db.conn.execute(
                "UPDATE users SET is_active = 0 WHERE id = ?", (self.user_one["id"],)
            )
            self.db.conn.commit()

        response = self.client.post(
            "/send-message",
            json=self.payload("acct-one", api_key="legacy-secret-2026"),
        )

        self.assertFalse(response.json()["success"])
        self.assertEqual(response.json()["message"], "身份验证失败")
        self.live.send_msg.assert_not_awaited()

    def test_wrong_shared_secret_is_rejected(self):
        self.db.set_system_setting("qq_reply_secret_key", "legacy-secret-2026")
        self.db.set_system_setting(
            "qq_reply_secret_user_id", str(self.user_one["id"])
        )

        response = self.client.post(
            "/send-message",
            json=self.payload("acct-one", api_key="wrong-secret"),
        )

        self.assertFalse(response.json()["success"])
        self.live.send_msg.assert_not_awaited()

    # ---------------- 测试钥旁路与失败关闭 ----------------

    def test_legacy_test_key_bypass_is_removed(self):
        legacy_key = "zhinina" + "_test_key"
        self.assertNotIn(legacy_key, Path("reply_server.py").read_text(encoding="utf-8"))

        response = self.client.post(
            "/send-message", json=self.payload("acct-one", api_key=legacy_key)
        )

        self.assertFalse(response.json()["success"])
        self.live.send_msg.assert_not_awaited()

    def test_api_key_verification_fails_closed_on_error(self):
        with patch.object(
            self.db, "get_system_setting", side_effect=RuntimeError("db down")
        ):
            self.assertFalse(reply_server.verify_api_key("legacy-secret-2026"))

    def test_empty_configured_secret_never_authorizes(self):
        self.db.set_system_setting("qq_reply_secret_key", "")
        with patch.object(reply_server, "API_SECRET_KEY", ""):
            self.assertFalse(reply_server.verify_api_key(""))
            self.assertFalse(reply_server.verify_api_key("anything"))

    def test_offline_account_reply_hides_instance_details(self):
        self.live.ws = None

        response = self.client.post(
            "/send-message",
            json=self.payload("acct-one"),
            headers=self.headers_for(self.user_one),
        )

        body = response.json()
        self.assertFalse(body["success"])
        self.assertEqual(body["message"], "账号当前不可发送消息，请稍后重试")
        self.assertNotIn("WebSocket", body["message"])
        self.assertNotIn("实例", body["message"])

    def test_endpoint_resolves_caller_identity_before_touching_accounts(self):
        source = inspect.getsource(reply_server.send_message_api)
        self.assertIn("resolve_send_message_caller", source)
        self.assertLess(
            source.index("resolve_send_message_caller"),
            source.index("XianyuLive"),
        )


if __name__ == "__main__":
    unittest.main()
