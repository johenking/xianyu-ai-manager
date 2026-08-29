"""任务：告警按归属路由 + 自助重登归属安全（代理多租户 · 任务 4）。

锁定两条多租户语义：
1. 账号告警只会送达归属人自己的通知渠道——发送侧 JOIN 防线 + 绑定路由校验；
2. 代理自助重新扫码只会更新/新建自己名下的账号，unb 撞车绝不覆盖其他租户。
"""

import asyncio
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from db_manager import DBManager
import reply_server


class AccountOwnershipRoutingTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.previous_key_file = os.environ.get("SYSTEM_SECRET_KEY_FILE")
        os.environ["SYSTEM_SECRET_KEY_FILE"] = str(self.root / ".system-key")
        self.db = DBManager(str(self.root / "ownership.db"))
        for username in ("agent-a", "agent-b"):
            self.assertTrue(
                self.db.create_user(
                    username, f"{username}@example.test", "Strong-pass-2026!"
                )
            )
        self.user_a = self.db.get_user_by_username("agent-a")
        self.user_b = self.db.get_user_by_username("agent-b")
        self.original_db = reply_server.db_manager
        reply_server.db_manager = self.db
        reply_server.SESSION_TOKENS.clear()
        self.client = TestClient(reply_server.app, raise_server_exceptions=False)
        self._seed_accounts_and_channels()

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

    def _seed_accounts_and_channels(self):
        with self.db.lock:
            cursor = self.db.conn.cursor()
            cursor.executemany(
                "INSERT INTO cookies (id, value, user_id, xianyu_unb) VALUES (?, ?, ?, ?)",
                (
                    ("fish-a", "unb=111001; cookie2=session-a", self.user_a["id"], "111001"),
                    ("fish-b", "unb=222002; cookie2=session-b", self.user_b["id"], "222002"),
                ),
            )
            cursor.executemany(
                "INSERT INTO notification_channels (name, type, config, enabled, user_id) "
                "VALUES (?, 'email', '{\"email\":\"x@example.test\"}', 1, ?)",
                (
                    ("channel-of-a", self.user_a["id"]),
                    ("channel-of-b", self.user_b["id"]),
                ),
            )
            cursor.execute(
                "SELECT id, user_id FROM notification_channels ORDER BY id"
            )
            rows = cursor.fetchall()
            self.channel_a = next(r[0] for r in rows if r[1] == self.user_a["id"])
            self.channel_b = next(r[0] for r in rows if r[1] == self.user_b["id"])
            self.db.conn.commit()

    def test_alert_channels_are_owner_scoped_even_with_cross_tenant_rows(self):
        with self.db.lock:
            cursor = self.db.conn.cursor()
            # 正常绑定：A 的账号 → A 的渠道
            cursor.execute(
                "INSERT INTO message_notifications (cookie_id, channel_id, enabled) VALUES (?, ?, 1)",
                ("fish-a", self.channel_a),
            )
            # 脏数据（模拟历史越权/构造攻击）：A 的账号 → B 的渠道
            cursor.execute(
                "INSERT INTO message_notifications (cookie_id, channel_id, enabled) VALUES (?, ?, 1)",
                ("fish-a", self.channel_b),
            )
            self.db.conn.commit()

        notifications = self.db.get_account_notifications("fish-a")
        # 发送侧 JOIN（c.user_id = nc.user_id）必须滤掉跨租户渠道：
        # A 账号的掉线告警只会送到 A 自己的渠道，绝不会送到 B 的渠道
        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0]["channel_id"], self.channel_a)
        self.assertEqual(notifications[0]["channel_name"], "channel-of-a")

    def test_notification_binding_rejects_cross_tenant_cookie_and_channel(self):
        # B 想把 A 的账号绑到自己的渠道（窃听 A 的告警）→ 403
        stolen = self.client.post(
            "/message-notifications/fish-a",
            json={"channel_id": self.channel_b, "enabled": True},
            headers=self.headers_for(self.user_b),
        )
        self.assertEqual(stolen.status_code, 403, stolen.text)

        # A 想把自己的账号绑到 B 的渠道（把告警外送）→ 404（他人渠道不可见）
        outbound = self.client.post(
            "/message-notifications/fish-a",
            json={"channel_id": self.channel_b, "enabled": True},
            headers=self.headers_for(self.user_a),
        )
        self.assertEqual(outbound.status_code, 404, outbound.text)

        # 正常绑定自己的渠道成功
        allowed = self.client.post(
            "/message-notifications/fish-a",
            json={"channel_id": self.channel_a, "enabled": True},
            headers=self.headers_for(self.user_a),
        )
        self.assertEqual(allowed.status_code, 200, allowed.text)

    def test_relogin_with_conflicting_unb_never_touches_other_tenant(self):
        # B 名下已有 cookie_id 恰好等于 A 新扫码账号的 unb（历史命名撞车）
        with self.db.lock:
            cursor = self.db.conn.cursor()
            cursor.execute(
                "INSERT INTO cookies (id, value, user_id, xianyu_unb) VALUES (?, ?, ?, ?)",
                ("333003", "unb=999888; cookie2=owned-by-b", self.user_b["id"], "999888"),
            )
            self.db.conn.commit()

        update_manager = AsyncMock()
        with patch.object(
            reply_server, "_update_cookie_manager_after_official_login", update_manager
        ):
            account_info = asyncio.run(
                reply_server._persist_validated_account_login(
                    user_id=self.user_a["id"],
                    cookies_str="unb=333003; cookie2=fresh-login-a",
                    validated_unb="333003",
                    login_method="qr",
                )
            )

        # A 拿到带后缀的新账号，B 的记录一个字节都不动
        self.assertEqual(account_info["account_id"], "333003_1")
        self.assertTrue(account_info["is_new_account"])
        with self.db.lock:
            cursor = self.db.conn.cursor()
            cursor.execute(
                "SELECT user_id, value FROM cookies WHERE id = '333003'"
            )
            owner_id, value = cursor.fetchone()
            self.assertEqual(owner_id, self.user_b["id"])
            self.assertEqual(value, "unb=999888; cookie2=owned-by-b")
            cursor.execute(
                "SELECT user_id FROM cookies WHERE id = '333003_1'"
            )
            self.assertEqual(cursor.fetchone()[0], self.user_a["id"])
        update_manager.assert_awaited_once()

    def test_relogin_updates_own_account_in_place(self):
        update_manager = AsyncMock()
        with patch.object(
            reply_server, "_update_cookie_manager_after_official_login", update_manager
        ):
            account_info = asyncio.run(
                reply_server._persist_validated_account_login(
                    user_id=self.user_a["id"],
                    cookies_str="unb=111001; cookie2=renewed-session-a",
                    validated_unb="111001",
                    login_method="qr",
                )
            )

        # 同 unb 命中自己名下既有账号：原地更新，不新建
        self.assertEqual(account_info["account_id"], "fish-a")
        self.assertFalse(account_info["is_new_account"])
        with self.db.lock:
            cursor = self.db.conn.cursor()
            cursor.execute("SELECT value, user_id FROM cookies WHERE id = 'fish-a'")
            value, owner_id = cursor.fetchone()
            self.assertIn("renewed-session-a", value)
            self.assertEqual(owner_id, self.user_a["id"])
            cursor.execute(
                "SELECT COUNT(*) FROM cookies WHERE xianyu_unb = '111001'"
            )
            self.assertEqual(cursor.fetchone()[0], 1)
        refresh = self.db.get_account_session_refresh("fish-a")
        self.assertEqual(refresh.get("state"), "success")


if __name__ == "__main__":
    unittest.main()
