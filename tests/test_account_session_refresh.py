import os
import tempfile
import time
import unittest

from account_session_refresh import is_runtime_event_active, is_valid_account_login_username
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

    def test_ai_api_url_is_not_accepted_as_xianyu_login_username(self):
        self.assertFalse(is_valid_account_login_username("https://api.deepseek.com"))
        self.assertFalse(is_valid_account_login_username("http://localhost:3000/v1"))
        self.assertTrue(is_valid_account_login_username("13800138000"))
        self.assertTrue(is_valid_account_login_username("buyer@example.com"))


if __name__ == "__main__":
    unittest.main()
