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
    "support_email": "support@example.com",
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
        self.assertTrue(
            self.db.set_system_setting("registration_enabled", "true")
        )

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
        sender.assert_called_once()
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
        self.assertFalse(payload["invite_required"])
        self.assertEqual(payload["terms_version"], "v2")
        self.assertEqual(payload["terms_url"], "/terms")
        self.assertEqual(payload["privacy_url"], "/privacy")
        self.assertEqual(payload["support_email"], "")
        serialized = response.text.casefold()
        self.assertNotIn("smtp", serialized)
        self.assertNotIn("invite_count", serialized)
        self.assertNotIn("remaining_slots", serialized)
        self.assertNotIn("user_limit", serialized)

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
        self.make_registration_ready()
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
            invite_code="ignored-legacy-field",
        )
        self.assertNotIn(email_code, str(email_result))

        registered = self.client.post(
            "/register",
            json={
                "invite_code": "ignored-legacy-field",
                "email": "New.User@Example.com",
                "challenge_id": email_result["challenge_id"],
                "verification_code": email_code,
                "username": "新用户_01",
                "password": "Safe-pass-2026!",
                "terms_version": "v2",
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

        no_invite_captcha = self.captcha(code="CD34")
        no_invite_email, no_invite_code = self.email_code(
            purpose="register",
            email="no-invite@example.com",
            captcha=no_invite_captcha,
            captcha_code="CD34",
        )
        no_invite_registration = self.client.post(
            "/register",
            json={
                "email": "no-invite@example.com",
                "challenge_id": no_invite_email["challenge_id"],
                "verification_code": no_invite_code,
                "username": "no-invite-user",
                "password": "No-invite-pass-2026!",
                "terms_version": "v2",
                "terms_accepted": True,
            },
        )
        self.assertEqual(
            no_invite_registration.status_code,
            200,
            no_invite_registration.text,
        )

        reused = self.client.post(
            "/register",
            json={
                "invite_code": "different-ignored-value",
                "email": "New.User@Example.com",
                "challenge_id": email_result["challenge_id"],
                "verification_code": email_code,
                "username": "second-user",
                "password": "Second-pass-2026!",
                "terms_version": "v2",
                "terms_accepted": True,
            },
        )
        self.assertEqual(reused.status_code, 400)
        self.assertEqual(reused.json()["code"], "CHALLENGE_CONSUMED")

    def test_smtp_failure_does_not_create_a_usable_email_challenge(self):
        self.make_registration_ready()
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
                    "invite_code": "ignored",
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

    def test_existing_email_has_same_public_success_shape_and_sends(self):
        self.make_registration_ready()
        self.assertTrue(
            self.db.create_user(
                "existing-user",
                "existing@example.com",
                "Existing-pass-2026!",
            )
        )
        existing_captcha = self.captcha(code="AA11")
        fresh_captcha = self.captcha(code="BB22")

        with patch.object(
            reply_server.SMTPEmailSender,
            "send",
            autospec=True,
        ) as existing_sender:
            existing = self.client.post(
                "/api/auth/email-code",
                json={
                    "purpose": "register",
                    "email": "existing@example.com",
                    "invite_code": "ignored",
                    "captcha_challenge_id": existing_captcha["challenge_id"],
                    "captcha_code": "AA11",
                },
            )
        with patch.object(
            reply_server.SMTPEmailSender,
            "send",
            autospec=True,
        ) as fresh_sender:
            fresh = self.client.post(
                "/api/auth/email-code",
                json={
                    "purpose": "register",
                    "email": "fresh@example.com",
                    "captcha_challenge_id": fresh_captcha["challenge_id"],
                    "captcha_code": "BB22",
                },
            )

        self.assertEqual(existing.status_code, 200, existing.text)
        self.assertEqual(fresh.status_code, 200, fresh.text)
        self.assertEqual(set(existing.json()), set(fresh.json()))
        self.assertEqual(existing.json()["expires_in"], fresh.json()["expires_in"])
        self.assertEqual(
            existing.json()["cooldown_seconds"],
            fresh.json()["cooldown_seconds"],
        )
        self.assertEqual(existing.json()["message"], fresh.json()["message"])
        existing_sender.assert_called_once()
        fresh_sender.assert_called_once()
        row = self.db.conn.execute(
            "SELECT purpose, context_digest, secret_digest FROM auth_challenges "
            "WHERE challenge_id = ?",
            (existing.json()["challenge_id"],),
        ).fetchone()
        self.assertEqual(row[0], "register_email")
        self.assertEqual(row[1], "")
        self.assertNotIn("existing@example.com", " ".join(map(str, row)))

    def test_registration_email_checks_captcha_and_rate_limit_before_account_lookup(self):
        self.make_registration_ready()
        captcha = self.captcha(code="CC33")
        events = []
        original_consume = self.db.registration_service.consume_challenge
        original_enforce = self.db.auth_rate_limiter.enforce_email_send
        original_lookup = self.db.get_user_by_email_for_public_auth

        def consume_first(**kwargs):
            events.append("captcha")
            return original_consume(**kwargs)

        def rate_second(ip, email):
            events.append("rate")
            return original_enforce(ip, email)

        def lookup_last(email):
            self.assertEqual(events, ["captcha", "rate"])
            events.append("lookup")
            return original_lookup(email)

        with (
            patch.object(
                self.db.registration_service,
                "consume_challenge",
                side_effect=consume_first,
            ),
            patch.object(
                self.db.auth_rate_limiter,
                "enforce_email_send",
                side_effect=rate_second,
            ),
            patch.object(
                self.db,
                "get_user_by_email_for_public_auth",
                side_effect=lookup_last,
            ),
            patch.object(reply_server.SMTPEmailSender, "send", autospec=True),
        ):
            response = self.client.post(
                "/api/auth/email-code",
                json={
                    "purpose": "register",
                    "email": "ordered@example.com",
                    "captcha_challenge_id": captcha["challenge_id"],
                    "captcha_code": "CC33",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(events, ["captcha", "rate", "lookup"])

    def test_legacy_email_code_endpoint_is_gone(self):
        response = self.client.post(
            "/send-verification-code",
            json={"email": "legacy@example.com", "type": "register"},
        )
        self.assertEqual(response.status_code, 410)
        self.assertEqual(response.json()["code"], "LEGACY_AUTH_ENDPOINT_REMOVED")
        self.assertIn("/api/auth/email-code", response.json()["message"])


class EmailEnumerationAPITests(RegistrationAPIFixture):
    def setUp(self):
        super().setUp()
        self.make_registration_ready()
        self.assertTrue(
            self.db.create_user(
                "existing-target",
                "existing-target@example.com",
                "Existing-target-pass-2026!",
            )
        )
        self.assertTrue(
            self.db.create_user(
                "inactive-target",
                "inactive-target@example.com",
                "Inactive-target-pass-2026!",
            )
        )
        inactive = self.db.get_user_by_email("inactive-target@example.com")
        self.db.auth_service.set_user_active(inactive["id"], False)

    def request_code(self, *, purpose, email, captcha_code, send_error=None):
        captcha = self.captcha(code=captcha_code)
        with patch.object(
            reply_server.SMTPEmailSender,
            "send",
            autospec=True,
            side_effect=send_error,
        ) as sender:
            response = self.client.post(
                "/api/auth/email-code",
                json={
                    "purpose": purpose,
                    "email": email,
                    "captcha_challenge_id": captcha["challenge_id"],
                    "captcha_code": captcha_code,
                },
            )
        return response, sender

    @staticmethod
    def stable_success_payload(response):
        return {
            key: value
            for key, value in response.json().items()
            if key != "challenge_id"
        }

    def test_all_target_states_send_once_and_return_identical_success(self):
        cases = (
            ("register", "fresh-target@example.com", "A101"),
            ("register", "existing-target@example.com", "A102"),
            ("password_reset", "existing-target@example.com", "A103"),
            ("password_reset", "missing-target@example.com", "A104"),
            ("password_reset", "inactive-target@example.com", "A105"),
        )
        responses = []
        with patch.object(
            self.db.auth_rate_limiter,
            "enforce_email_send",
        ):
            for purpose, email, captcha_code in cases:
                with self.subTest(purpose=purpose, target=email.split("@", 1)[0]):
                    response, sender = self.request_code(
                        purpose=purpose,
                        email=email,
                        captcha_code=captcha_code,
                    )
                    self.assertEqual(response.status_code, 200, response.text)
                    sender.assert_called_once()
                    sent_text = sender.call_args.kwargs["text"]
                    self.assertRegex(sent_text, r"\b\d{6}\b")
                    responses.append(self.stable_success_payload(response))

        self.assertTrue(responses)
        self.assertTrue(all(payload == responses[0] for payload in responses))

    def test_smtp_failure_is_identical_for_all_target_states(self):
        cases = (
            ("register", "fresh-failure@example.com", "B201"),
            ("register", "existing-target@example.com", "B202"),
            ("password_reset", "existing-target@example.com", "B203"),
            ("password_reset", "missing-failure@example.com", "B204"),
            ("password_reset", "inactive-target@example.com", "B205"),
        )
        failures = []
        before = self.db.conn.execute(
            "SELECT COUNT(*) FROM auth_challenges WHERE purpose IN "
            "('register_email', 'password_reset_email')"
        ).fetchone()[0]
        with patch.object(
            self.db.auth_rate_limiter,
            "enforce_email_send",
        ):
            for purpose, email, captcha_code in cases:
                with self.subTest(purpose=purpose, target=email.split("@", 1)[0]):
                    response, sender = self.request_code(
                        purpose=purpose,
                        email=email,
                        captcha_code=captcha_code,
                        send_error=SMTPDeliveryError("synthetic failure"),
                    )
                    sender.assert_called_once()
                    failures.append(
                        (response.status_code, response.json()["code"], response.json()["message"])
                    )

        self.assertTrue(all(failure == failures[0] for failure in failures))
        self.assertEqual(failures[0][0:2], (502, "EMAIL_SEND_FAILED"))
        after = self.db.conn.execute(
            "SELECT COUNT(*) FROM auth_challenges WHERE purpose IN "
            "('register_email', 'password_reset_email')"
        ).fetchone()[0]
        self.assertEqual(after, before)

    def test_mailed_codes_cannot_consume_decoy_challenges_or_mutate_state(self):
        inactive = self.db.get_user_by_email("inactive-target@example.com")
        self.assertTrue(
            self.db.save_auth_session(
                token="synthetic-inactive-session",
                user_id=inactive["id"],
                username=inactive["username"],
                is_admin=False,
                expires_at=2_000_000_000,
            )
        )
        inactive_hash = self.db.conn.execute(
            "SELECT password_hash_v2 FROM users WHERE id = ?",
            (inactive["id"],),
        ).fetchone()[0]
        user_count = self.db.registration_service.registration_capacity()["user_count"]
        cases = (
            ("register", "existing-target@example.com", "C301"),
            ("password_reset", "missing-decoy@example.com", "C302"),
            ("password_reset", "inactive-target@example.com", "C303"),
        )
        issued = []
        with patch.object(
            self.db.auth_rate_limiter,
            "enforce_email_send",
        ):
            for purpose, email, captcha_code in cases:
                response, sender = self.request_code(
                    purpose=purpose,
                    email=email,
                    captcha_code=captcha_code,
                )
                self.assertEqual(response.status_code, 200, response.text)
                mailed_code = re.search(
                    r"\b(\d{6})\b",
                    sender.call_args.kwargs["text"],
                ).group(1)
                issued.append((purpose, email, response.json()["challenge_id"], mailed_code))

        errors = []
        for purpose, email, challenge_id, mailed_code in issued:
            if purpose == "register":
                response = self.client.post(
                    "/register",
                    json={
                        "email": email,
                        "challenge_id": challenge_id,
                        "verification_code": mailed_code,
                        "username": "decoy-register-user",
                        "password": "Decoy-register-pass-2026!",
                        "terms_version": "v2",
                        "terms_accepted": True,
                    },
                )
            else:
                response = self.client.post(
                    "/api/auth/password-reset",
                    json={
                        "email": email,
                        "challenge_id": challenge_id,
                        "verification_code": mailed_code,
                        "new_password": "Decoy-reset-pass-2026!",
                    },
                )
            errors.append(
                (response.status_code, response.json()["code"], response.json()["message"])
            )

        self.assertTrue(all(error == errors[0] for error in errors))
        self.assertEqual(errors[0][0:2], (400, "CHALLENGE_SECRET_INVALID"))
        self.assertEqual(
            self.db.registration_service.registration_capacity()["user_count"],
            user_count,
        )
        self.assertEqual(
            self.db.conn.execute(
                "SELECT password_hash_v2 FROM users WHERE id = ?",
                (inactive["id"],),
            ).fetchone()[0],
            inactive_hash,
        )
        self.assertIsNotNone(
            self.db.get_auth_session("synthetic-inactive-session")
        )

    def test_public_email_lookup_uses_one_indexed_query_for_hits_and_misses(self):
        cases = (
            ("small-hit", "existing-target@example.com", "D401"),
            ("small-miss", "small-missing@example.com", "D402"),
            ("large-hit", "bulk-user-199@example.com", "D403"),
            ("large-miss", "large-missing@example.com", "D404"),
        )
        query_shapes = []

        with patch.object(
            self.db.auth_rate_limiter,
            "enforce_email_send",
        ):
            for label, email, captcha_code in cases:
                if label == "large-hit":
                    self.db.conn.executemany(
                        "INSERT INTO users ("
                        "username, email, password_hash, username_normalized, "
                        "email_normalized) VALUES (?, ?, '', ?, ?)",
                        (
                            (
                                f"bulk-user-{index:03d}",
                                f"bulk-user-{index:03d}@example.com",
                                f"bulk-user-{index:03d}",
                                f"bulk-user-{index:03d}@example.com",
                            )
                            for index in range(200)
                        ),
                    )
                    self.db.conn.commit()
                statements = []
                self.db.conn.set_trace_callback(statements.append)
                try:
                    response, sender = self.request_code(
                        purpose="password_reset",
                        email=email,
                        captcha_code=captcha_code,
                    )
                finally:
                    self.db.conn.set_trace_callback(None)
                with self.subTest(label=label):
                    self.assertEqual(response.status_code, 200, response.text)
                    sender.assert_called_once()
                    user_selects = [
                        " ".join(statement.split())
                        for statement in statements
                        if statement.lstrip().upper().startswith("SELECT ID, USERNAME")
                        and " FROM users" in statement
                    ]
                    self.assertEqual(len(user_selects), 1)
                    self.assertIn(
                        "INDEXED BY idx_users_email_normalized",
                        user_selects[0],
                    )
                    self.assertNotIn(" ORDER BY id", user_selects[0])
                    query_shapes.append(
                        re.sub(
                            r"email_normalized = '[^']*'",
                            "email_normalized = ?",
                            user_selects[0],
                        )
                    )

        self.assertTrue(all(shape == query_shapes[0] for shape in query_shapes))


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
    def test_admin_status_keeps_missing_support_email_empty(self):
        response = self.client.get(
            "/api/admin/registration/status",
            headers=self.admin_headers(),
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["smtp"]["support_email"], "")

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

    def test_legacy_invite_routes_return_gone_and_user_management_remains(self):
        headers = self.admin_headers()
        created = self.client.post(
            "/api/admin/registration/invites",
            headers=headers,
            json={"count": 2, "valid_days": 7, "note": "pilot"},
        )
        self.assertEqual(created.status_code, 410, created.text)
        self.assertEqual(
            created.json()["code"],
            "INVITATION_REGISTRATION_REMOVED",
        )

        listed = self.client.get(
            "/api/admin/registration/invites",
            headers=headers,
        )
        self.assertEqual(listed.status_code, 410)
        revoked = self.client.delete(
            "/api/admin/registration/invites/1",
            headers=headers,
        )
        self.assertEqual(revoked.status_code, 410, revoked.text)

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
        self.assertNotIn(
            "admin",
            [item["username"].casefold() for item in users.json()["users"]],
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

    def test_registration_can_be_enabled_after_smtp_confirmation_without_invites(self):
        headers = self.admin_headers()
        blocked = self.client.put(
            "/api/admin/registration/enabled",
            headers=headers,
            json={"enabled": True},
        )
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.json()["code"], "REGISTRATION_NOT_READY")

        self.mark_smtp_verified()
        enabled = self.client.put(
            "/api/admin/registration/enabled",
            headers=headers,
            json={"enabled": True},
        )
        self.assertEqual(enabled.status_code, 200, enabled.text)
        self.assertTrue(enabled.json()["enabled"])

    def test_smtp_verification_is_pending_until_code_confirmation(self):
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
            "support@example.com",
        )
        self.assertEqual(response.json()["state"], "pending")
        self.assertEqual(response.json()["expires_in"], 600)
        self.assertEqual(response.json()["masked_recipient"], "s***@e***.com")
        self.assertEqual(self.db.get_system_setting("smtp_verified_fingerprint"), "")
        self.assertNotIn("synthetic-smtp-secret", response.text)
        code = re.search(r"\b(\d{6})\b", sender.call_args.kwargs["text"]).group(1)

        confirmed = self.client.post(
            "/api/settings/verify/smtp/confirm",
            headers=headers,
            json={
                "challenge_id": response.json()["challenge_id"],
                "verification_code": code,
            },
        )

        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        self.assertEqual(confirmed.json()["state"], "ready")
        self.assertTrue(self.db.get_system_setting("smtp_verified_fingerprint"))

    def test_smtp_confirmation_write_failure_rolls_back_consumption_for_retry(self):
        headers = self.admin_headers()
        with patch.object(
            reply_server.SMTPEmailSender,
            "send",
            autospec=True,
        ) as sender:
            pending = self.client.post(
                "/api/settings/verify/smtp",
                headers=headers,
                json={
                    "settings": SMTP_SETTINGS,
                    "secret_actions": {"smtp_password": "set"},
                },
            )
        self.assertEqual(pending.status_code, 200, pending.text)
        code = re.search(r"\b(\d{6})\b", sender.call_args.kwargs["text"]).group(1)
        challenge_id = pending.json()["challenge_id"]
        self.db.conn.executescript(
            """
            CREATE TRIGGER fail_smtp_verification_state_write
            BEFORE UPDATE OF value ON system_settings
            WHEN OLD.key = 'smtp_verified_fingerprint' AND NEW.value <> ''
            BEGIN
                SELECT RAISE(ABORT, 'synthetic-state-write-failure');
            END;
            """
        )

        failed = self.client.post(
            "/api/settings/verify/smtp/confirm",
            headers=headers,
            json={
                "challenge_id": challenge_id,
                "verification_code": code,
            },
        )

        self.assertEqual(failed.status_code, 503, failed.text)
        self.assertEqual(failed.json()["code"], "SMTP_VERIFICATION_SAVE_FAILED")
        self.assertNotIn("synthetic-state-write-failure", failed.text)
        self.assertIsNone(
            self.db.conn.execute(
                "SELECT consumed_at FROM auth_challenges WHERE challenge_id = ?",
                (challenge_id,),
            ).fetchone()[0]
        )
        self.assertEqual(self.db.get_system_setting("smtp_verified_fingerprint"), "")

        self.db.conn.execute("DROP TRIGGER fail_smtp_verification_state_write")
        self.db.conn.commit()
        retried = self.client.post(
            "/api/settings/verify/smtp/confirm",
            headers=headers,
            json={
                "challenge_id": challenge_id,
                "verification_code": code,
            },
        )

        self.assertEqual(retried.status_code, 200, retried.text)
        self.assertEqual(retried.json()["state"], "ready")

    def test_smtp_confirmation_rejects_wrong_expired_and_changed_configuration(self):
        headers = self.admin_headers()

        def pending():
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
            code = re.search(r"\b(\d{6})\b", sender.call_args.kwargs["text"]).group(1)
            return response.json(), code

        wrong_pending, _ = pending()
        wrong = self.client.post(
            "/api/settings/verify/smtp/confirm",
            headers=headers,
            json={
                "challenge_id": wrong_pending["challenge_id"],
                "verification_code": "not-the-code",
            },
        )
        self.assertEqual(wrong.status_code, 400)
        self.assertEqual(wrong.json()["code"], "CHALLENGE_SECRET_INVALID")

        expired_pending, expired_code = pending()
        self.db.conn.execute(
            "UPDATE auth_challenges SET expires_at = 0 WHERE challenge_id = ?",
            (expired_pending["challenge_id"],),
        )
        self.db.conn.commit()
        expired = self.client.post(
            "/api/settings/verify/smtp/confirm",
            headers=headers,
            json={
                "challenge_id": expired_pending["challenge_id"],
                "verification_code": expired_code,
            },
        )
        self.assertEqual(expired.status_code, 400)
        self.assertEqual(expired.json()["code"], "CHALLENGE_EXPIRED")

        changed_pending, changed_code = pending()
        self.assertTrue(
            self.db.set_system_setting("smtp_server", "changed.smtp.example.test")
        )
        changed = self.client.post(
            "/api/settings/verify/smtp/confirm",
            headers=headers,
            json={
                "challenge_id": changed_pending["challenge_id"],
                "verification_code": changed_code,
            },
        )
        self.assertEqual(changed.status_code, 400)
        self.assertIn(
            changed.json()["code"],
            {"CHALLENGE_CONSUMED", "CHALLENGE_CONTEXT_MISMATCH"},
        )
        self.assertIsNotNone(
            self.db.conn.execute(
                "SELECT consumed_at FROM auth_challenges WHERE challenge_id = ?",
                (changed_pending["challenge_id"],),
            ).fetchone()[0]
        )
        self.assertEqual(self.db.get_system_setting("smtp_verified_fingerprint"), "")

    def test_smtp_verification_send_failure_leaves_no_usable_challenge(self):
        headers = self.admin_headers()
        before = self.db.conn.execute(
            "SELECT COUNT(*) FROM auth_challenges WHERE purpose = 'smtp_verify_email' "
            "AND consumed_at IS NULL"
        ).fetchone()[0]
        with patch.object(
            reply_server.SMTPEmailSender,
            "send",
            side_effect=SMTPDeliveryError("synthetic failure"),
        ):
            response = self.client.post(
                "/api/settings/verify/smtp",
                headers=headers,
                json={
                    "settings": SMTP_SETTINGS,
                    "secret_actions": {"smtp_password": "set"},
                },
            )

        self.assertEqual(response.status_code, 400, response.text)
        after = self.db.conn.execute(
            "SELECT COUNT(*) FROM auth_challenges WHERE purpose = 'smtp_verify_email' "
            "AND consumed_at IS NULL"
        ).fetchone()[0]
        self.assertEqual(after, before)
        self.assertEqual(self.db.get_system_setting("registration_enabled"), "false")
        self.assertEqual(self.db.get_system_setting("smtp_verified_fingerprint"), "")

    def test_admin_limit_status_and_lowering_closure(self):
        headers = self.admin_headers()
        self.make_registration_ready()
        self.assertTrue(
            self.db.create_user(
                "disabled-capacity-user",
                "disabled-capacity@example.test",
                "Capacity-pass-2026!",
            )
        )
        user = self.db.get_user_by_username("disabled-capacity-user")
        self.db.auth_service.set_user_active(user["id"], False)

        lowered = self.client.put(
            "/api/admin/registration/limit",
            headers=headers,
            json={"limit": 1},
        )
        self.assertEqual(lowered.status_code, 200, lowered.text)
        status = self.client.get(
            "/api/admin/registration/status",
            headers=headers,
        )
        payload = status.json()
        self.assertEqual(payload["user_limit"], 1)
        self.assertEqual(payload["user_count"], 1)
        self.assertEqual(payload["remaining_slots"], 0)
        self.assertNotIn("invites", payload)
        self.assertFalse(payload["registration"]["requested"])

        raised = self.client.put(
            "/api/admin/registration/limit",
            headers=headers,
            json={"limit": 2},
        )
        self.assertEqual(raised.status_code, 200, raised.text)
        self.assertFalse(
            self.client.get(
                "/api/admin/registration/status",
                headers=headers,
            ).json()["registration"]["requested"]
        )

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
