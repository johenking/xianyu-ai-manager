import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException

import reply_server
from db_manager import DBManager


class FakeCookieManager:
    def __init__(self):
        self.cookies = {"legacy-account": "unb=stable-unb; cookie2=old"}
        self.add_calls = []
        self.update_calls = []

    def add_cookie(self, account_id, value, user_id=None):
        self.cookies[account_id] = value
        self.add_calls.append((account_id, value, user_id))

    def update_cookie(self, account_id, value, save_to_db=True):
        self.cookies[account_id] = value
        self.update_calls.append((account_id, value, save_to_db))


class ManualCookieIdentityTests(unittest.TestCase):
    def setUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.db = DBManager(self.db_path)
        self.manager = FakeCookieManager()
        self.user = {"user_id": 1, "username": "admin"}
        self.db.update_cookie_account_info(
            "legacy-account",
            cookie_value="unb=stable-unb; cookie2=old",
            user_id=1,
            login_method="qr",
            login_validated=True,
        )
        with self.db.lock:
            self.db.conn.execute(
                "INSERT INTO keywords (cookie_id, keyword, reply) VALUES (?, ?, ?)",
                ("legacy-account", "价格", "保留关联数据"),
            )
            self.db.conn.commit()

    def tearDown(self):
        self.db.close()
        os.unlink(self.db_path)

    def _patch_runtime(self):
        return (
            patch.object(reply_server, "db_manager", self.db),
            patch("db_manager.db_manager", self.db),
            patch.object(reply_server.cookie_manager, "manager", self.manager),
        )

    def test_post_ignores_legacy_id_and_merges_by_real_unb(self):
        patches = self._patch_runtime()
        with patches[0], patches[1], patches[2]:
            response = reply_server.add_cookie(
                reply_server.CookieIn(
                    id="client-supplied-wrong-id",
                    value="unb=stable-unb; cookie2=replacement",
                ),
                current_user=self.user,
            )

        self.assertEqual(response["account_id"], "legacy-account")
        self.assertTrue(response["matched_existing"])
        self.assertIsNone(self.db.get_cookie_details("client-supplied-wrong-id"))
        self.assertEqual(
            self.db.get_cookie_details("legacy-account")["value"],
            "unb=stable-unb; cookie2=replacement",
        )

    def test_post_derives_new_account_id_from_unb_without_client_id(self):
        patches = self._patch_runtime()
        with patches[0], patches[1], patches[2]:
            response = reply_server.add_cookie(
                reply_server.CookieIn(value="unb=new-unb; _m_h5_tk=token"),
                current_user=self.user,
            )

        self.assertEqual(response["account_id"], "new-unb")
        self.assertEqual(self.db.get_cookie_details("new-unb")["xianyu_unb"], "new-unb")

    def test_post_rejects_missing_identity_or_core_session_cookie(self):
        for value in ("cookie2=session-only", "unb=identity-only"):
            patches = self._patch_runtime()
            with patches[0], patches[1], patches[2]:
                with self.assertRaises(HTTPException) as raised:
                    reply_server.add_cookie(
                        reply_server.CookieIn(value=value),
                        current_user=self.user,
                    )
            self.assertEqual(raised.exception.status_code, 400)
            self.assertEqual(raised.exception.detail["code"], "invalid_cookie")

    def test_put_identity_mismatch_is_atomic_and_returns_409(self):
        self.db.mark_cookie_expired("legacy-account")
        self.db.update_account_session_refresh(
            "legacy-account",
            state="manual_reauth_required",
            trigger="expired",
            message="请重新扫码",
            error_code="manual_reauth_required",
        )
        before = self.db.get_cookie_details("legacy-account")
        before_status = self.db.get_account_session_refresh("legacy-account")

        patches = self._patch_runtime()
        with patches[0], patches[1], patches[2]:
            with self.assertRaises(HTTPException) as raised:
                reply_server.update_cookie(
                    "legacy-account",
                    reply_server.CookieIn(value="unb=other-unb; cookie2=attacker"),
                    current_user=self.user,
                )

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.detail["code"], "account_identity_mismatch")
        after = self.db.get_cookie_details("legacy-account")
        after_status = self.db.get_account_session_refresh("legacy-account")
        self.assertEqual(after["value"], before["value"])
        self.assertEqual(after["xianyu_unb"], before["xianyu_unb"])
        self.assertEqual(after["login_method"], before["login_method"])
        self.assertEqual(after["last_expired_at"], before["last_expired_at"])
        self.assertEqual(after_status["updated_at"], before_status["updated_at"])
        self.assertEqual(self.manager.update_calls, [])
        with self.db.lock:
            count = self.db.conn.execute(
                "SELECT COUNT(*) FROM keywords WHERE cookie_id = ?",
                ("legacy-account",),
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_combined_account_update_rejects_identity_mismatch_before_other_fields(self):
        patches = self._patch_runtime()
        with patches[0], patches[1], patches[2]:
            with self.assertRaises(HTTPException) as raised:
                reply_server.update_cookie_account_info(
                    "legacy-account",
                    reply_server.CookieAccountInfo(
                        value="unb=other-unb; cookie2=attacker",
                        username="should-not-save",
                    ),
                    current_user=self.user,
                )

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.detail["code"], "account_identity_mismatch")
        self.assertNotEqual(
            self.db.get_cookie_details("legacy-account")["username"],
            "should-not-save",
        )


if __name__ == "__main__":
    unittest.main()
