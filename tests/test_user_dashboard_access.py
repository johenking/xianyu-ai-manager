import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from db_manager import DBManager
import reply_server
from session_registry import get_session_registry


class UserDashboardAccessTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.db_path = self.root / "access.db"
        self.previous_key_file = os.environ.get("SYSTEM_SECRET_KEY_FILE")
        os.environ["SYSTEM_SECRET_KEY_FILE"] = str(self.root / ".system-key")
        self.db = DBManager(str(self.db_path))
        self.assertTrue(
            self.db.create_user(
                "ordinary-one",
                "ordinary-one@example.test",
                "Strong-pass-2026!",
            )
        )
        self.assertTrue(
            self.db.create_user(
                "ordinary-two",
                "ordinary-two@example.test",
                "Strong-pass-2026!",
            )
        )
        self.user_one = self.db.get_user_by_username("ordinary-one")
        self.user_two = self.db.get_user_by_username("ordinary-two")
        self.original_db = reply_server.db_manager
        reply_server.db_manager = self.db
        reply_server.SESSION_TOKENS.clear()
        self.client = TestClient(reply_server.app, raise_server_exceptions=False)
        self._seed_business_data()

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

    def _seed_business_data(self):
        with self.db.lock:
            cursor = self.db.conn.cursor()
            for cookie_id, user_id, enabled in (
                ("one-active", self.user_one["id"], 1),
                ("one-paused", self.user_one["id"], 0),
                ("two-active", self.user_two["id"], 1),
            ):
                cursor.execute(
                    "INSERT INTO cookies (id, value, user_id, xianyu_unb) VALUES (?, ?, ?, ?)",
                    (cookie_id, f"unb={cookie_id}; cookie2=session", user_id, cookie_id),
                )
                cursor.execute(
                    "INSERT INTO cookie_status (cookie_id, enabled) VALUES (?, ?)",
                    (cookie_id, enabled),
                )
            cursor.executemany(
                "INSERT INTO orders (order_id, item_id, buyer_id, amount, order_status, cookie_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    ("order-one", "item-one", "buyer-one", "12.50", "completed", "one-active", "2026-07-10 10:00:00"),
                    ("order-two", "item-two", "buyer-two", "99.00", "completed", "two-active", "2026-07-10 11:00:00"),
                ),
            )
            cursor.executemany(
                "INSERT INTO item_info (cookie_id, item_id, item_title) VALUES (?, ?, ?)",
                (
                    ("one-active", "item-one", "用户一商品"),
                    ("one-active", "unused-item", "未进入当前排行的商品"),
                    ("two-active", "item-two", "用户二商品"),
                ),
            )
            cursor.executemany(
                "INSERT INTO cards (name, type, user_id) VALUES (?, 'text', ?)",
                (("card-one", self.user_one["id"]), ("card-two", self.user_two["id"])),
            )
            cursor.executemany(
                "INSERT INTO keywords (cookie_id, keyword, reply) VALUES (?, ?, 'reply')",
                (("one-active", "one-keyword"), ("two-active", "two-keyword")),
            )
            self.db.conn.commit()

    def test_user_basic_settings_inherit_global_defaults_and_remain_isolated(self):
        self.db.set_system_setting("item_sync_enabled", "true")
        self.db.set_system_setting("item_sync_interval", "900")
        self.db.set_system_setting("item_sync_max_pages", "8")
        headers_one = self.headers_for(self.user_one)
        headers_two = self.headers_for(self.user_two)

        inherited = self.client.get(
            "/api/settings/user-summary",
            headers=headers_one,
        )
        self.assertEqual(inherited.status_code, 200, inherited.text)
        self.assertEqual(
            inherited.json()["settings"],
            {
                "item_sync_enabled": True,
                "item_sync_interval": 900,
                "item_sync_max_pages": 8,
            },
        )
        self.assertTrue(inherited.json()["inherited"])

        saved = self.client.put(
            "/api/settings/user-basic",
            headers=headers_one,
            json={
                "item_sync_enabled": False,
                "item_sync_interval": 120,
                "item_sync_max_pages": 3,
            },
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertFalse(saved.json()["inherited"])
        self.assertEqual(saved.json()["settings"]["item_sync_interval"], 120)

        other_user = self.client.get(
            "/api/settings/user-summary",
            headers=headers_two,
        )
        self.assertEqual(other_user.status_code, 200, other_user.text)
        self.assertEqual(other_user.json()["settings"]["item_sync_interval"], 900)
        self.assertTrue(other_user.json()["inherited"])

        global_settings = self.client.get(
            "/system-settings",
            headers=headers_one,
        )
        self.assertEqual(global_settings.status_code, 403, global_settings.text)

    def test_user_basic_settings_validate_interval_and_page_limits(self):
        headers = self.headers_for(self.user_one)
        too_fast = self.client.put(
            "/api/settings/user-basic",
            headers=headers,
            json={
                "item_sync_enabled": True,
                "item_sync_interval": 59,
                "item_sync_max_pages": 5,
            },
        )
        self.assertEqual(too_fast.status_code, 422, too_fast.text)
        too_many_pages = self.client.put(
            "/api/settings/user-basic",
            headers=headers,
            json={
                "item_sync_enabled": True,
                "item_sync_interval": 60,
                "item_sync_max_pages": 51,
            },
        )
        self.assertEqual(too_many_pages.status_code, 422, too_many_pages.text)

    def test_partial_user_setting_update_preserves_other_global_inheritance(self):
        self.db.set_system_setting("item_sync_interval", "900")
        response = self.client.put(
            "/api/settings/user-basic",
            headers=self.headers_for(self.user_one),
            json={"item_sync_interval": 1800},
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["settings"]["item_sync_interval"], 1800)
        self.assertEqual(payload["sources"]["item_sync_interval"], "user")
        self.assertEqual(payload["sources"]["item_sync_enabled"], "global")
        self.assertEqual(payload["sources"]["item_sync_max_pages"], "global")

    def test_dashboard_summary_is_user_scoped_and_admin_is_system_scoped(self):
        ordinary = self.client.get(
            "/api/dashboard/summary",
            params={"range": "7days", "end_date": "2026-07-11"},
            headers=self.headers_for(self.user_one),
        )
        self.assertEqual(ordinary.status_code, 200, ordinary.text)
        ordinary_payload = ordinary.json()
        self.assertEqual(ordinary_payload["scope"], "user")
        self.assertEqual(ordinary_payload["stats"]["total_cookies"], 2)
        self.assertEqual(ordinary_payload["stats"]["active_cookies"], 1)
        self.assertEqual(ordinary_payload["stats"]["total_cards"], 1)
        self.assertEqual(ordinary_payload["stats"]["total_keywords"], 1)
        self.assertEqual(ordinary_payload["current"]["revenue_stats"]["total_orders"], 1)
        self.assertEqual(ordinary_payload["current"]["revenue_stats"]["total_amount"], 12.5)
        self.assertEqual(ordinary_payload["item_names"], {"item-one": "用户一商品"})
        self.assertNotIn("item-two", ordinary.text)

        admin = self.db.get_user_by_username("admin")
        system = self.client.get(
            "/api/dashboard/summary",
            params={"range": "7days", "end_date": "2026-07-11"},
            headers=self.headers_for(admin),
        )
        self.assertEqual(system.status_code, 200, system.text)
        system_payload = system.json()
        self.assertEqual(system_payload["scope"], "system")
        self.assertEqual(system_payload["stats"]["total_cookies"], 3)
        self.assertEqual(system_payload["current"]["revenue_stats"]["total_orders"], 2)
        self.assertEqual(system_payload["current"]["revenue_stats"]["total_amount"], 111.5)

    def test_ai_reply_test_rejects_another_users_account(self):
        manager = type("Manager", (), {"cookies": {"two-active": "secret"}})()
        with patch.object(reply_server.cookie_manager, "manager", manager):
            response = self.client.post(
                "/ai-reply-test/two-active",
                headers=self.headers_for(self.user_one),
                json={"message": "test"},
            )
        self.assertEqual(response.status_code, 403, response.text)

    def test_login_sessions_cannot_be_polled_by_another_user(self):
        registry = get_session_registry()
        registry.register(
            "other-password-session",
            "password_login",
            self.user_two["id"],
            status="processing",
        )
        registry.register(
            "other-qr-session",
            "qr_login",
            self.user_two["id"],
            status="processing",
        )
        headers = self.headers_for(self.user_one)

        password = self.client.get(
            "/password-login/check/other-password-session",
            headers=headers,
        )
        qr = self.client.get(
            "/qr-login/check/other-qr-session",
            headers=headers,
        )

        self.assertEqual(password.status_code, 403, password.text)
        self.assertEqual(qr.status_code, 403, qr.text)


if __name__ == "__main__":
    unittest.main()
