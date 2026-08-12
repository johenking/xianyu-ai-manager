"""AI 回复设置批量读取：SQL 层归属过滤、不返回明文密钥、查询数不随账号数增长。"""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from db_manager import DBManager
import reply_server


class AiReplySettingsBulkScopeTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.previous_key_file = os.environ.get("SYSTEM_SECRET_KEY_FILE")
        os.environ["SYSTEM_SECRET_KEY_FILE"] = str(self.root / ".system-key")
        self.db = DBManager(str(self.root / "ai-settings.db"))
        self.assertTrue(
            self.db.create_user("ai-one", "one@example.test", "Strong-pass-2026!")
        )
        self.assertTrue(
            self.db.create_user("ai-two", "two@example.test", "Strong-pass-2026!")
        )
        self.user_one = self.db.get_user_by_username("ai-one")
        self.user_two = self.db.get_user_by_username("ai-two")
        with self.db.lock:
            self.db.conn.executemany(
                "INSERT INTO cookies (id, value, user_id) VALUES (?, ?, ?)",
                (
                    ("one-a", "unb=1; cookie2=x", self.user_one["id"]),
                    ("two-a", "unb=2; cookie2=x", self.user_two["id"]),
                    ("two-b", "unb=3; cookie2=x", self.user_two["id"]),
                    ("two-c", "unb=4; cookie2=x", self.user_two["id"]),
                ),
            )
            self.db.conn.commit()
        self.owner_secret = "sk-owner-one-secret-value"
        self.foreign_secret = "sk-foreign-two-secret-value"
        self.db.save_ai_reply_settings("one-a", {
            "ai_enabled": True,
            "model_name": "deepseek-v4-flash",
            "api_key": self.owner_secret,
            "base_url": "https://api.deepseek.com",
        })
        for cookie_id in ("two-a", "two-b", "two-c"):
            self.db.save_ai_reply_settings(cookie_id, {
                "ai_enabled": False,
                "model_name": "deepseek-v4-flash",
                "api_key": self.foreign_secret,
                "base_url": "https://api.deepseek.com",
            })

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

    def _count_statements(self, action):
        statements = []
        self.db.conn.set_trace_callback(statements.append)
        try:
            action()
        finally:
            self.db.conn.set_trace_callback(None)
        return len(statements)

    # ---------------- SQL 层归属过滤与密钥不外泄 ----------------

    def test_raw_rows_are_filtered_by_user_in_sql(self):
        scoped = self.db.get_all_ai_reply_settings(self.user_one["id"])
        self.assertEqual(set(scoped), {"one-a"})
        self.assertEqual(
            set(self.db.get_all_ai_reply_settings(self.user_two["id"])),
            {"two-a", "two-b", "two-c"},
        )

    def test_raw_rows_never_carry_plaintext_api_key(self):
        scoped = self.db.get_all_ai_reply_settings(self.user_one["id"])
        row = scoped["one-a"]
        self.assertNotIn("api_key", row)
        self.assertTrue(row["api_key_configured"])
        self.assertNotIn(self.owner_secret, json.dumps(scoped))

    def test_unscoped_raw_rows_still_hide_plaintext_api_key(self):
        every = self.db.get_all_ai_reply_settings()
        self.assertEqual(set(every), {"one-a", "two-a", "two-b", "two-c"})
        self.assertNotIn(self.owner_secret, json.dumps(every))
        self.assertNotIn(self.foreign_secret, json.dumps(every))

    def test_bulk_resolver_masks_account_key_and_reports_source(self):
        resolved = self.db.get_ai_reply_settings_for_user(self.user_one["id"])
        settings = resolved["one-a"]
        self.assertEqual(settings["api_key"], "")
        self.assertEqual(settings["api_key_source"], "account")
        self.assertTrue(settings["has_effective_api_key"])
        self.assertEqual(
            settings["api_key_masked"],
            reply_server._mask_secret(self.owner_secret),
        )
        self.assertNotIn(self.owner_secret, json.dumps(resolved))

    def test_bulk_resolver_falls_back_to_global_key(self):
        self.db.save_ai_reply_settings("one-a", {
            "ai_enabled": True,
            "model_name": "deepseek-v4-flash",
            "api_key": "",
            "base_url": "https://api.deepseek.com",
        })
        self.db.set_system_setting("ai_api_key", "sk-global-fallback-key")

        settings = self.db.get_ai_reply_settings_for_user(self.user_one["id"])["one-a"]

        self.assertEqual(settings["api_key_source"], "global")
        self.assertTrue(settings["has_effective_api_key"])
        self.assertEqual(
            settings["api_key_masked"],
            reply_server._mask_secret("sk-global-fallback-key"),
        )

    def test_bulk_resolver_reports_missing_key(self):
        self.db.save_ai_reply_settings("one-a", {
            "ai_enabled": False,
            "model_name": "deepseek-v4-flash",
            "api_key": "",
            "base_url": "https://api.deepseek.com",
        })

        settings = self.db.get_ai_reply_settings_for_user(self.user_one["id"])["one-a"]

        self.assertEqual(settings["api_key_source"], "missing")
        self.assertFalse(settings["has_effective_api_key"])
        self.assertEqual(settings["api_key_masked"], "")

    # ---------------- N+1 消除 ----------------

    def test_bulk_resolver_query_count_does_not_grow_with_account_count(self):
        one_account = self._count_statements(
            lambda: self.db.get_ai_reply_settings_for_user(self.user_one["id"])
        )
        three_accounts = self._count_statements(
            lambda: self.db.get_ai_reply_settings_for_user(self.user_two["id"])
        )

        self.assertEqual(one_account, three_accounts)

    def test_route_does_not_resolve_settings_account_by_account(self):
        headers = self.headers_for(self.user_two)
        # 首次调用完成历史配置迁移，之后账号都已绑定平台配置
        warmup = self.client.get("/ai-reply-settings", headers=headers)
        self.assertEqual(warmup.status_code, 200, warmup.text)

        per_account = Mock(side_effect=AssertionError("逐账号解析 AI 设置"))
        with patch.object(self.db, "get_ai_reply_settings", per_account):
            response = self.client.get("/ai-reply-settings", headers=headers)

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(set(response.json()), {"two-a", "two-b", "two-c"})
        per_account.assert_not_called()

    def test_route_only_returns_the_callers_accounts_without_plaintext(self):
        response = self.client.get(
            "/ai-reply-settings", headers=self.headers_for(self.user_one)
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(set(payload), {"one-a"})
        self.assertEqual(payload["one-a"]["api_key"], "")
        self.assertNotIn(self.owner_secret, response.text)
        self.assertNotIn(self.foreign_secret, response.text)
        for field in (
            "ai_enabled", "model_name", "base_url", "max_discount_percent",
            "max_discount_amount", "max_bargain_rounds", "custom_prompts",
            "api_key_source", "api_key_masked", "has_effective_api_key",
        ):
            self.assertIn(field, payload["one-a"])


if __name__ == "__main__":
    unittest.main()
