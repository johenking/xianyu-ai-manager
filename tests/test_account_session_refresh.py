import os
import tempfile
import time
import unittest
from unittest.mock import patch

from account_session_refresh import (
    ActiveRefreshRegistry,
    is_runtime_event_active,
    is_valid_account_login_username,
    resolve_refresh_schedule_anchor,
)
from db_manager import DBManager


class AccountIdentityDatabaseTests(unittest.TestCase):
    def setUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.db = DBManager(self.db_path)
        with self.db.lock:
            self.db.conn.execute(
                "INSERT OR IGNORE INTO users (id, username, email, password_hash) "
                "VALUES (2, 'other', 'other@example.com', 'x')"
            )
            self.db.conn.execute(
                """
                INSERT INTO cookies (
                    id, value, user_id, auto_confirm, remark, pause_duration,
                    username, password, show_browser
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "legacy-account",
                    "unb=stable-unb; cookie2=old",
                    1,
                    0,
                    "保留备注",
                    23,
                    "login-user",
                    "login-password",
                    1,
                ),
            )
            self.db.conn.execute(
                "INSERT INTO keywords (cookie_id, keyword, reply) VALUES (?, ?, ?)",
                ("legacy-account", "价格", "详情页价格为准"),
            )
            self.db.conn.commit()

    def tearDown(self):
        self.db.conn.close()
        os.unlink(self.db_path)

    def test_cookie_upsert_only_updates_cookie_and_preserves_account_data(self):
        self.assertTrue(
            self.db.save_cookie(
                "legacy-account",
                "unb=stable-unb; cookie2=new",
                user_id=1,
            )
        )

        details = self.db.get_cookie_details("legacy-account")
        self.assertEqual(details["remark"], "保留备注")
        self.assertEqual(details["pause_duration"], 23)
        self.assertEqual(details["username"], "login-user")
        self.assertEqual(details["password"], "login-password")
        self.assertTrue(details["show_browser"])
        self.assertFalse(details["auto_confirm"])
        with self.db.lock:
            count = self.db.conn.execute(
                "SELECT COUNT(*) FROM keywords WHERE cookie_id = ?",
                ("legacy-account",),
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_same_unb_is_resolved_to_existing_account_with_user_isolation(self):
        self.db.backfill_cookie_identities()

        self.assertEqual(
            self.db.find_cookie_id_by_unb(1, "stable-unb"),
            "legacy-account",
        )
        self.assertIsNone(self.db.find_cookie_id_by_unb(2, "stable-unb"))


class AccountSessionRefreshDatabaseTests(unittest.TestCase):
    def setUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.db = DBManager(self.db_path)
        with self.db.lock:
            self.db.conn.execute(
                "INSERT INTO cookies (id, value, user_id) VALUES ('account-1', 'unb=account-1', 1)"
            )
            self.db.conn.commit()

    def tearDown(self):
        self.db.conn.close()
        os.unlink(self.db_path)

    def test_refresh_status_never_exposes_verification_url(self):
        self.db.update_account_session_refresh(
            "account-1",
            state="verification_required",
            trigger="token_expired",
            message="请完成身份验证",
            verification_image_path="static/uploads/images/face_verify_account-1.jpg",
            expires_at=time.time() + 300,
        )

        status = self.db.get_account_session_refresh("account-1")

        self.assertEqual(status["state"], "verification_required")
        self.assertEqual(
            status["verification_image_url"],
            "/static/uploads/images/face_verify_account-1.jpg",
        )
        self.assertNotIn("verification_url", status)

    def test_action_required_has_no_verification_image_or_expiry(self):
        self.db.update_account_session_refresh(
            "account-1",
            state="action_required",
            trigger="消息 Token 探测",
            message="请手动开始一次验证",
            error_code="human_verification_required",
        )

        status = self.db.get_account_session_refresh("account-1")

        self.assertEqual(status["state"], "action_required")
        self.assertEqual(status["verification_image_url"], "")
        self.assertIsNone(status["expires_at"])

    def test_old_runtime_events_do_not_override_a_newer_success(self):
        now = 1_000.0
        self.assertFalse(
            is_runtime_event_active(
                event_at=800.0,
                last_success_at=900.0,
                now=now,
                max_age_seconds=600,
            )
        )
        self.assertFalse(
            is_runtime_event_active(
                event_at=300.0,
                last_success_at=None,
                now=now,
                max_age_seconds=600,
            )
        )
        self.assertTrue(
            is_runtime_event_active(
                event_at=950.0,
                last_success_at=900.0,
                now=now,
                max_age_seconds=600,
            )
        )

    def test_refresh_schedule_anchor_uses_latest_persisted_attempt(self):
        self.assertEqual(
            resolve_refresh_schedule_anchor(
                {
                    "last_attempt_at": 920.0,
                    "last_success_at": 880.0,
                },
                now=1_000.0,
            ),
            920.0,
        )
        self.assertEqual(
            resolve_refresh_schedule_anchor({}, now=1_000.0),
            1_000.0,
        )

    def test_cookie_refresh_settings_default_to_disabled_and_can_be_updated(self):
        details = self.db.get_cookie_details("account-1")

        self.assertFalse(details["cookie_refresh_enabled"])
        self.assertEqual(details["cookie_refresh_interval_minutes"], 1440)
        with self.assertRaisesRegex(ValueError, "账号密码"):
            self.db.update_cookie_refresh_settings(
                "account-1",
                enabled=True,
                interval_minutes=360,
            )

        self.assertTrue(
            self.db.update_cookie_account_info(
                "account-1",
                username="13800138000",
                password="secret",
                login_method="password",
                login_validated=True,
            )
        )

        self.assertTrue(
            self.db.update_cookie_refresh_settings(
                "account-1",
                enabled=True,
                interval_minutes=360,
            )
        )

        updated = self.db.get_cookie_details("account-1")
        self.assertTrue(updated["cookie_refresh_enabled"])
        self.assertEqual(updated["cookie_refresh_interval_minutes"], 360)
        self.assertTrue(self.db.get_cookie_refresh_settings("account-1")["auto_refresh_supported"])

    def test_non_password_login_disables_existing_refresh_and_records_expiry_once(self):
        self.db.update_cookie_account_info(
            "account-1",
            username="13800138000",
            password="secret",
            login_method="password",
            login_validated=True,
        )
        self.db.update_cookie_refresh_settings(
            "account-1", enabled=True, interval_minutes=360
        )
        self.db.update_cookie_account_info(
            "account-1", login_method="qr", login_validated=True
        )

        details = self.db.get_cookie_details("account-1")
        self.assertEqual(details["login_method"], "qr")
        self.assertFalse(details["cookie_refresh_enabled"])
        self.assertFalse(self.db.get_cookie_refresh_settings("account-1")["enabled"])

        self.assertTrue(self.db.mark_cookie_expired("account-1"))
        first_expiry = self.db.get_cookie_details("account-1")["last_expired_at"]
        time.sleep(0.001)
        self.assertTrue(self.db.mark_cookie_expired("account-1"))
        self.assertEqual(
            self.db.get_cookie_details("account-1")["last_expired_at"],
            first_expiry,
        )

    def test_official_browser_user_agent_is_persisted_with_account_cookie(self):
        user_agent = "Mozilla/5.0 Synthetic Chrome/150.0.0.0 Safari/537.36"

        self.assertTrue(
            self.db.update_cookie_account_info(
                "account-1",
                cookie_value="unb=account-1; cookie2=renewed",
                browser_user_agent=user_agent,
            )
        )

        self.assertEqual(
            self.db.get_cookie_details("account-1")["browser_user_agent"],
            user_agent,
        )

    def test_cookie_refresh_interval_rejects_risky_short_schedules(self):
        with self.assertRaises(ValueError):
            self.db.update_cookie_refresh_settings(
                "account-1",
                enabled=True,
                interval_minutes=30,
            )

    def test_ai_api_url_is_not_accepted_as_xianyu_login_username(self):
        self.assertFalse(is_valid_account_login_username("https://api.deepseek.com"))
        self.assertFalse(is_valid_account_login_username("http://localhost:3000/v1"))
        self.assertTrue(is_valid_account_login_username("13800138000"))
        self.assertTrue(is_valid_account_login_username("buyer@example.com"))


class ActiveRefreshRegistryTests(unittest.TestCase):
    class Worker:
        def __init__(self, active=True):
            self.active = active
            self.closed = False
            self.shown = False

        def browser_active(self):
            return self.active and not self.closed

        def close_browser(self):
            self.closed = True

        def request_visible(self):
            self.shown = True

    def test_cancel_before_worker_install_is_propagated_to_real_worker(self):
        registry = ActiveRefreshRegistry()
        placeholder = object()
        worker = self.Worker()
        self.assertTrue(registry.register("account-1", placeholder))

        self.assertTrue(registry.cancel("account-1"))
        self.assertTrue(registry.set_worker("account-1", worker))

        self.assertTrue(worker.closed)

    def test_browser_actions_require_a_live_browser_worker(self):
        registry = ActiveRefreshRegistry()
        worker = self.Worker(active=False)
        registry.register("account-1", worker)

        self.assertFalse(registry.browser_active("account-1"))
        self.assertFalse(registry.show_browser("account-1"))
        worker.active = True
        self.assertTrue(registry.browser_active("account-1"))
        self.assertTrue(registry.show_browser("account-1"))
        self.assertTrue(worker.shown)


class RuntimeRefreshRecoveryTests(unittest.TestCase):
    def test_startup_normalizes_persisted_active_state_without_a_worker(self):
        import application_runtime

        updates = []
        database = type(
            "Database",
            (),
            {
                "get_all_cookies": lambda self: {"account-1": "unb=account-1"},
                "get_account_session_refresh": lambda self, _cookie_id: {
                    "state": "verification_required",
                    "trigger": "scheduled",
                    "verification_image_url": "",
                },
                "update_account_session_refresh": (
                    lambda self, cookie_id, **kwargs: updates.append(
                        (cookie_id, kwargs)
                    )
                ),
            },
        )()

        with patch.object(application_runtime, "db_manager", database):
            count = application_runtime._normalize_orphaned_refresh_states()

        self.assertEqual(count, 1)
        self.assertEqual(updates[0][1]["state"], "action_required")
        self.assertEqual(updates[0][1]["error_code"], "browser_session_missing")


if __name__ == "__main__":
    unittest.main()
