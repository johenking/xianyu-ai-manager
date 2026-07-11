import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from auth_email_service import (
    SMTPDeliveryError,
    smtp_configuration_fingerprint,
)
from db_manager import DBManager
import reply_server


SMTP_SETTINGS = {
    "smtp_server": "smtp.example.test",
    "smtp_port": "587",
    "smtp_user": "sender@example.test",
    "smtp_password": "synthetic-smtp-secret",
    "smtp_from": "Xianyu Manager",
    "smtp_use_tls": "true",
    "smtp_use_ssl": "false",
    "support_email": "support@example.test",
}


class RegistrationAPIFixture(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.db_path = self.root / "api.db"
        self.previous_key_file = os.environ.get("SYSTEM_SECRET_KEY_FILE")
        os.environ["SYSTEM_SECRET_KEY_FILE"] = str(self.root / ".system-key")
        self.db = DBManager(str(self.db_path))
        self.original_db = reply_server.db_manager
        reply_server.db_manager = self.db
        reply_server.SESSION_TOKENS.clear()
        self.client = TestClient(
            reply_server.app,
            raise_server_exceptions=False,
            client=("127.0.0.1", 50000),
        )

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

    def admin_headers(self):
        admin = self.db.get_user_by_username("admin")
        token, _ = reply_server.create_login_session(admin)
        return {"Authorization": f"Bearer {token}"}

    def mark_smtp_verified(self):
        fingerprint = smtp_configuration_fingerprint(
            SMTP_SETTINGS,
            db_path=str(self.db_path),
        )
        self.assertTrue(
            self.db.save_verified_smtp_settings(
                SMTP_SETTINGS,
                fingerprint=fingerprint,
                verified_at="2026-07-11T11:00:00+08:00",
            )
        )

    def make_registration_ready(self):
        self.mark_smtp_verified()
        admin = self.db.get_user_by_username("admin")
        invite = self.db.registration_service.create_invites(
            count=1,
            valid_days=7,
            created_by_user_id=admin["id"],
        )[0]
        self.assertTrue(
            self.db.set_system_setting("registration_enabled", "true")
        )
        return invite

    def captcha(self, code="AB12"):
        with patch.object(
            self.db,
            "generate_captcha",
            return_value=(code, "data:image/png;base64,c3ludGhldGlj"),
        ):
            response = self.client.post("/api/auth/captcha", json={})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def email_code(
        self,
        *,
        purpose,
        email,
        captcha,
        captcha_code="AB12",
        invite_code="",
    ):
        with patch.object(
            reply_server.SMTPEmailSender,
            "send",
            autospec=True,
        ) as sender:
            response = self.client.post(
                "/api/auth/email-code",
                json={
                    "purpose": purpose,
                    "email": email,
                    "invite_code": invite_code,
                    "captcha_challenge_id": captcha["challenge_id"],
                    "captcha_code": captcha_code,
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        body = sender.call_args.kwargs["text"]
        code_match = re.search(r"\b(\d{6})\b", body)
        self.assertIsNotNone(code_match)
        return response.json(), code_match.group(1)


class PublicRegistrationAPITests(RegistrationAPIFixture):
    def test_validation_errors_do_not_echo_password_code_or_invite(self):
        secrets_in_request = {
            "invite_code": "REG-SYNTHETIC-PRIVATE-INVITE",
            "password": "Synthetic-private-password-2026!",
            "verification_code": "654321",
            "terms_accepted": True,
        }
        response = self.client.post("/register", json=secrets_in_request)

        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.json()["code"],
            "REQUEST_VALIDATION_FAILED",
        )
        for secret in secrets_in_request.values():
            if isinstance(secret, str):
                self.assertNotIn(secret, response.text)

    def test_registration_config_fails_closed_without_internal_details(self):
        response = self.client.get("/api/auth/registration-config")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["enabled"])
        self.assertFalse(payload["ready"])
        self.assertTrue(payload["invite_required"])
        self.assertEqual(payload["terms_version"], "v1")
        self.assertEqual(payload["terms_url"], "/terms")
        self.assertEqual(payload["privacy_url"], "/privacy")
        self.assertEqual(payload["support_email"], "")
        serialized = response.text.casefold()
        self.assertNotIn("smtp", serialized)
        self.assertNotIn("invite_count", serialized)

        with patch.object(
            self.db,
            "get_all_system_settings",
            side_effect=RuntimeError("database unavailable"),
        ):
            failed = self.client.get("/api/auth/registration-config")
        self.assertEqual(failed.status_code, 200)
        self.assertFalse(failed.json()["enabled"])
        self.assertFalse(failed.json()["ready"])

    def test_captcha_email_registration_and_automatic_login(self):
        invite = self.make_registration_ready()
        captcha = self.captcha()
        stored_secret = self.db.conn.execute(
            "SELECT secret_digest FROM auth_challenges WHERE challenge_id = ?",
            (captcha["challenge_id"],),
        ).fetchone()[0]
        self.assertNotIn("AB12", stored_secret)

        email_result, email_code = self.email_code(
            purpose="register",
            email="New.User@Example.com",
            captcha=captcha,
            invite_code=invite["code"],
        )
        self.assertNotIn(email_code, str(email_result))
        self.assertNotIn(invite["code"], str(email_result))

        registered = self.client.post(
            "/register",
            json={
                "invite_code": invite["code"],
                "email": "New.User@Example.com",
                "challenge_id": email_result["challenge_id"],
                "verification_code": email_code,
                "username": "新用户_01",
                "password": "Safe-pass-2026!",
                "terms_version": "v1",
                "terms_accepted": True,
            },
        )
        self.assertEqual(registered.status_code, 200, registered.text)
        payload = registered.json()
        self.assertTrue(payload["success"])
        self.assertTrue(payload["token"])
        verified = self.client.get(
            "/verify",
            headers={"Authorization": f"Bearer {payload['token']}"},
        )
        self.assertTrue(verified.json()["authenticated"])

        self.db.registration_service.create_invites(count=1, valid_days=7)

        reused = self.client.post(
            "/register",
            json={
                "invite_code": invite["code"],
                "email": "second@example.com",
                "challenge_id": email_result["challenge_id"],
                "verification_code": email_code,
                "username": "second-user",
                "password": "Second-pass-2026!",
                "terms_version": "v1",
                "terms_accepted": True,
            },
        )
        self.assertEqual(reused.status_code, 400)
        self.assertIn(
            reused.json()["code"],
            {"INVITE_ALREADY_USED", "CHALLENGE_CONSUMED"},
        )

    def test_smtp_failure_does_not_create_a_usable_email_challenge(self):
        invite = self.make_registration_ready()
        captcha = self.captcha()
        before = self.db.conn.execute(
            "SELECT COUNT(*) FROM auth_challenges WHERE purpose = 'register_email'"
        ).fetchone()[0]

        with patch.object(
            reply_server.SMTPEmailSender,
            "send",
            side_effect=SMTPDeliveryError("SMTP failed"),
        ):
            response = self.client.post(
                "/api/auth/email-code",
                json={
                    "purpose": "register",
                    "email": "recipient@example.com",
                    "invite_code": invite["code"],
                    "captcha_challenge_id": captcha["challenge_id"],
                    "captcha_code": "AB12",
                },
            )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["code"], "EMAIL_SEND_FAILED")
        self.assertNotIn("recipient@example.com", response.text)
        after = self.db.conn.execute(
            "SELECT COUNT(*) FROM auth_challenges WHERE purpose = 'register_email'"
        ).fetchone()[0]
        self.assertEqual(after, before)

    def test_legacy_email_code_endpoint_is_gone(self):
        response = self.client.post(
            "/send-verification-code",
            json={"email": "legacy@example.com", "type": "register"},
        )
        self.assertEqual(response.status_code, 410)
        self.assertEqual(response.json()["code"], "LEGACY_AUTH_ENDPOINT_REMOVED")
        self.assertIn("/api/auth/email-code", response.json()["message"])


class LoginAndPasswordResetAPITests(RegistrationAPIFixture):
    def setUp(self):
        super().setUp()
        self.mark_smtp_verified()
        self.assertTrue(
            self.db.create_user(
                "ordinary-user",
                "ordinary@example.com",
                "Original-pass-2026!",
            )
        )

    def test_login_accepts_username_or_email_and_inactive_tokens_stop_working(self):
        username_login = self.client.post(
            "/login",
            json={
                "identifier": "ordinary-user",
                "password": "Original-pass-2026!",
            },
        )
        self.assertEqual(username_login.status_code, 200, username_login.text)
        token = username_login.json()["token"]

        email_login = self.client.post(
            "/login",
            json={
                "identifier": "ORDINARY@EXAMPLE.COM",
                "password": "Original-pass-2026!",
            },
        )
        self.assertEqual(email_login.status_code, 200, email_login.text)

        user = self.db.get_user_by_email("ordinary@example.com")
        self.db.auth_service.set_user_active(user["id"], False)
        denied = self.client.get(
            "/verify",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertFalse(denied.json()["authenticated"])
        inactive_login = self.client.post(
            "/login",
            json={
                "identifier": "ordinary@example.com",
                "password": "Original-pass-2026!",
            },
        )
        self.assertEqual(inactive_login.status_code, 401)
        self.assertEqual(inactive_login.json()["code"], "INVALID_CREDENTIALS")

    def test_password_reset_revokes_all_old_sessions_and_new_password_works(self):
        old_login = self.client.post(
            "/login",
            json={
                "identifier": "ordinary-user",
                "password": "Original-pass-2026!",
            },
        ).json()
        captcha = self.captcha(code="ZX90")
        email_result, email_code = self.email_code(
            purpose="password_reset",
            email="ordinary@example.com",
            captcha=captcha,
            captcha_code="ZX90",
        )

        reset = self.client.post(
            "/api/auth/password-reset",
            json={
                "email": "ordinary@example.com",
                "challenge_id": email_result["challenge_id"],
                "verification_code": email_code,
                "new_password": "Changed-pass-2026!",
            },
        )
        self.assertEqual(reset.status_code, 200, reset.text)
        self.assertTrue(reset.json()["success"])

        old_verify = self.client.get(
            "/verify",
            headers={"Authorization": f"Bearer {old_login['token']}"},
        )
        self.assertFalse(old_verify.json()["authenticated"])
        old_password = self.client.post(
            "/login",
            json={
                "identifier": "ordinary-user",
                "password": "Original-pass-2026!",
            },
        )
        self.assertEqual(old_password.status_code, 401)
        new_password = self.client.post(
            "/login",
            json={
                "identifier": "ordinary@example.com",
                "password": "Changed-pass-2026!",
            },
        )
        self.assertEqual(new_password.status_code, 200, new_password.text)


class RegistrationAdminAPITests(RegistrationAPIFixture):
    def test_settings_summary_never_exposes_smtp_secret_or_fingerprint(self):
        self.mark_smtp_verified()
        fingerprint = self.db.get_system_setting("smtp_verified_fingerprint")
        response = self.client.get(
            "/api/settings/summary",
            headers=self.admin_headers(),
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn("synthetic-smtp-secret", response.text)
        self.assertNotIn(fingerprint, response.text)
        self.assertTrue(response.json()["sections"]["smtp"]["verified"])

    def test_admin_can_create_list_revoke_invites_and_toggle_users(self):
        headers = self.admin_headers()
        created = self.client.post(
            "/api/admin/registration/invites",
            headers=headers,
            json={"count": 2, "valid_days": 7, "note": "pilot"},
        )
        self.assertEqual(created.status_code, 200, created.text)
        codes = [invite["code"] for invite in created.json()["invites"]]
        self.assertEqual(len(codes), 2)

        listed = self.client.get(
            "/api/admin/registration/invites",
            headers=headers,
        )
        self.assertEqual(listed.status_code, 200)
        self.assertNotIn(codes[0], listed.text)
        invite_id = listed.json()["invites"][0]["id"]
        revoked = self.client.delete(
            f"/api/admin/registration/invites/{invite_id}",
            headers=headers,
        )
        self.assertEqual(revoked.status_code, 200, revoked.text)
        self.assertEqual(revoked.json()["invite"]["status"], "revoked")

        self.assertTrue(
            self.db.create_user(
                "pilot-user",
                "pilot@example.com",
                "Pilot-pass-2026!",
            )
        )
        users = self.client.get(
            "/api/admin/registration/users",
            headers=headers,
        )
        user = next(
            item for item in users.json()["users"] if item["username"] == "pilot-user"
        )
        disabled = self.client.put(
            f"/api/admin/registration/users/{user['id']}",
            headers=headers,
            json={"is_active": False},
        )
        self.assertEqual(disabled.status_code, 200, disabled.text)
        self.assertFalse(disabled.json()["user"]["is_active"])

    def test_registration_cannot_be_enabled_until_smtp_and_invite_are_ready(self):
        headers = self.admin_headers()
        blocked = self.client.put(
            "/api/admin/registration/enabled",
            headers=headers,
            json={"enabled": True},
        )
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.json()["code"], "REGISTRATION_NOT_READY")

        self.mark_smtp_verified()
        still_blocked = self.client.put(
            "/api/admin/registration/enabled",
            headers=headers,
            json={"enabled": True},
        )
        self.assertEqual(still_blocked.status_code, 409)

        self.client.post(
            "/api/admin/registration/invites",
            headers=headers,
            json={"count": 1, "valid_days": 7, "note": "pilot"},
        )
        enabled = self.client.put(
            "/api/admin/registration/enabled",
            headers=headers,
            json={"enabled": True},
        )
        self.assertEqual(enabled.status_code, 200, enabled.text)
        self.assertTrue(enabled.json()["enabled"])

    def test_smtp_verification_sends_email_and_marks_exact_settings_ready(self):
        headers = self.admin_headers()
        with patch.object(
            reply_server.SMTPEmailSender,
            "send",
            autospec=True,
        ) as sender:
            response = self.client.post(
                "/api/settings/verify/smtp",
                headers=headers,
                json={
                    "settings": SMTP_SETTINGS,
                    "secret_actions": {"smtp_password": "set"},
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        sender.assert_called_once()
        self.assertEqual(
            sender.call_args.kwargs["recipient"],
            "support@example.test",
        )
        self.assertTrue(
            self.db.get_system_setting("smtp_verified_fingerprint")
        )
        self.assertNotIn("synthetic-smtp-secret", response.text)

    def test_legacy_system_setting_mutation_requires_admin(self):
        self.assertTrue(
            self.db.create_user(
                "normal-user",
                "normal@example.com",
                "Normal-pass-2026!",
            )
        )
        login = self.client.post(
            "/login",
            json={
                "identifier": "normal-user",
                "password": "Normal-pass-2026!",
            },
        ).json()
        response = self.client.put(
            "/system-settings/show_default_login_info",
            headers={"Authorization": f"Bearer {login['token']}"},
            json={"value": "false"},
        )
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
