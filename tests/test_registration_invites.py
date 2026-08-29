"""邀请码注册（多租户任务 1）服务层测试。

覆盖：邀请码创建/列表/吊销生命周期、digest-only 存储、
registration_invite_required 开关语义（开=必填校验+一次性消费，
关=保持 v1.7 直接注册且忽略 legacy invite_code 字段）、并发消费唯一性。
"""

import os
import sqlite3
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile

import auth_registration_service as registration
from auth_email_service import smtp_configuration_fingerprint
from tests.test_registration_service import create_registration_database

RegistrationError = registration.RegistrationError
RegistrationService = registration.RegistrationService


class InviteServiceFixture(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.previous_key_file = os.environ.get("SYSTEM_SECRET_KEY_FILE")
        os.environ["SYSTEM_SECRET_KEY_FILE"] = str(self.root / "system_secret.key")
        self.db_path = self.root / "invites.db"
        self.connection = create_registration_database(self.db_path)
        self.now = 1_800_300_000.0
        self.service = RegistrationService(
            self.connection,
            str(self.db_path),
            lock=threading.RLock(),
            clock=lambda: self.now,
        )
        settings = {
            "smtp_server": "smtp.example.test",
            "smtp_port": "587",
            "smtp_user": "sender@example.test",
            "smtp_password": "synthetic-smtp-secret",
            "smtp_from": "Xianyu Manager",
            "smtp_use_tls": "true",
            "smtp_use_ssl": "false",
            "support_email": "support@example.test",
            "registration_enabled": "true",
            "registration_user_limit": "20",
            "terms_version": "v2",
        }
        settings["smtp_verified_fingerprint"] = smtp_configuration_fingerprint(
            settings,
            db_path=str(self.db_path),
        )
        self.connection.executemany(
            "INSERT INTO system_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            settings.items(),
        )
        self.connection.commit()

    def tearDown(self):
        self.connection.close()
        if self.previous_key_file is None:
            os.environ.pop("SYSTEM_SECRET_KEY_FILE", None)
        else:
            os.environ["SYSTEM_SECRET_KEY_FILE"] = self.previous_key_file
        self.tempdir.cleanup()

    def assert_error_code(self, code, callback):
        with self.assertRaises(RegistrationError) as raised:
            callback()
        self.assertEqual(raised.exception.code, code)

    def issue_challenge(self, email, secret="482615"):
        return self.service.create_challenge(
            purpose="register_email",
            subject=email,
            context="",
            secret=secret,
        )

    def register(self, *, username, email, invite_code="", secret="482615"):
        challenge = self.issue_challenge(email, secret=secret)
        return self.service.register_user(
            username=username,
            email=email,
            password="Strong-pass-2026!",
            invite_code=invite_code,
            challenge_id=challenge["challenge_id"],
            verification_code=secret,
            terms_version="v2",
        )


class InviteLifecycleTests(InviteServiceFixture):
    def test_create_invites_returns_plaintext_once_and_stores_digest_only(self):
        invites = self.service.create_invites(count=2, valid_days=7, note="代理甲")
        self.assertEqual(len(invites), 2)
        for invite in invites:
            self.assertTrue(invite["code"].startswith("REG-"))
            self.assertEqual(len(invite["code"]), 28)
            self.assertEqual(invite["status"], "active")
            self.assertEqual(invite["expires_at"], self.now + 7 * 86_400)
            stored = self.connection.execute(
                "SELECT code_digest, code_hint, note FROM registration_invites"
                " WHERE id = ?",
                (invite["id"],),
            ).fetchone()
            self.assertNotEqual(stored[0], invite["code"])
            self.assertNotIn(invite["code"], repr(stored))
            self.assertEqual(stored[1], f"{invite['code'][:7]}...{invite['code'][-4:]}")
            self.assertEqual(stored[2], "代理甲")

        listed = self.service.list_invites()
        self.assertEqual(len(listed), 2)
        for item in listed:
            self.assertNotIn("code", item)

    def test_create_invites_validates_inputs(self):
        self.assert_error_code(
            "INVITE_COUNT_INVALID",
            lambda: self.service.create_invites(count=0),
        )
        self.assert_error_code(
            "INVITE_COUNT_INVALID",
            lambda: self.service.create_invites(count=21),
        )
        self.assert_error_code(
            "INVITE_VALID_DAYS_INVALID",
            lambda: self.service.create_invites(valid_days=0),
        )
        self.assert_error_code(
            "INVITE_VALID_DAYS_INVALID",
            lambda: self.service.create_invites(valid_days=366),
        )
        self.assert_error_code(
            "INVITE_NOTE_TOO_LONG",
            lambda: self.service.create_invites(note="x" * 201),
        )

    def test_invite_required_switch_roundtrip(self):
        self.assertFalse(self.service.invite_required())
        self.assertTrue(self.service.set_invite_required(True))
        self.assertTrue(self.service.invite_required())
        row = self.connection.execute(
            "SELECT value FROM system_settings"
            " WHERE key = 'registration_invite_required'"
        ).fetchone()
        self.assertEqual(row[0], "1")
        self.assertFalse(self.service.set_invite_required(False))
        self.assertFalse(self.service.invite_required())

    def test_register_requires_valid_invite_when_enabled(self):
        self.service.set_invite_required(True)
        invite = self.service.create_invites(count=1, valid_days=7)[0]

        self.assert_error_code(
            "INVITE_CODE_REQUIRED",
            lambda: self.register(username="agent-a", email="agent-a@example.com"),
        )
        self.assert_error_code(
            "INVITE_INVALID",
            lambda: self.register(
                username="agent-a",
                email="agent-a@example.com",
                invite_code="REG-BOGUSBOGUSBOGUSBOGUSBOG",
            ),
        )

        user = self.register(
            username="agent-a",
            email="agent-a@example.com",
            invite_code=invite["code"],
        )
        stored = self.connection.execute(
            "SELECT used_at, used_by_user_id FROM registration_invites WHERE id = ?",
            (invite["id"],),
        ).fetchone()
        self.assertEqual(stored[0], self.now)
        self.assertEqual(stored[1], user["id"])

        self.assert_error_code(
            "INVITE_ALREADY_USED",
            lambda: self.register(
                username="agent-b",
                email="agent-b@example.com",
                invite_code=invite["code"],
            ),
        )

    def test_register_ignores_invite_when_disabled(self):
        user = self.register(
            username="direct-user",
            email="direct@example.com",
            invite_code="ignored-legacy-field",
        )
        self.assertEqual(user["username"], "direct-user")
        used = self.connection.execute(
            "SELECT COUNT(*) FROM registration_invites WHERE used_at IS NOT NULL"
        ).fetchone()[0]
        self.assertEqual(used, 0)

    def test_revoked_and_expired_invites_rejected(self):
        self.service.set_invite_required(True)
        revoked = self.service.create_invites(count=1, valid_days=7)[0]
        self.service.revoke_invite(revoked["id"])
        self.assert_error_code(
            "INVITE_REVOKED",
            lambda: self.register(
                username="agent-c",
                email="agent-c@example.com",
                invite_code=revoked["code"],
            ),
        )

        expiring = self.service.create_invites(count=1, valid_days=1)[0]
        self.now += 2 * 86_400
        self.assert_error_code(
            "INVITE_EXPIRED",
            lambda: self.register(
                username="agent-d",
                email="agent-d@example.com",
                invite_code=expiring["code"],
            ),
        )

    def test_revoke_invite_lifecycle_guards(self):
        invite = self.service.create_invites(count=1, valid_days=7)[0]
        revoked = self.service.revoke_invite(invite["id"])
        self.assertEqual(revoked["status"], "revoked")
        self.assert_error_code(
            "INVITE_REVOKED",
            lambda: self.service.revoke_invite(invite["id"]),
        )
        self.assert_error_code(
            "INVITE_NOT_FOUND",
            lambda: self.service.revoke_invite(99_999),
        )

        self.service.set_invite_required(True)
        used = self.service.create_invites(count=1, valid_days=7)[0]
        self.register(
            username="agent-e",
            email="agent-e@example.com",
            invite_code=used["code"],
        )
        self.assert_error_code(
            "INVITE_ALREADY_USED",
            lambda: self.service.revoke_invite(used["id"]),
        )

    def test_concurrent_register_consumes_invite_exactly_once(self):
        self.service.set_invite_required(True)
        invite = self.service.create_invites(count=1, valid_days=7)[0]

        second_connection = sqlite3.connect(
            self.db_path,
            check_same_thread=False,
            timeout=10,
        )
        second_connection.execute("PRAGMA foreign_keys = ON")
        second_service = RegistrationService(
            second_connection,
            str(self.db_path),
            lock=threading.RLock(),
            clock=lambda: self.now,
        )
        first_challenge = self.issue_challenge("race-1@example.com", secret="111111")
        second_challenge = self.service.create_challenge(
            purpose="register_email",
            subject="race-2@example.com",
            context="",
            secret="222222",
        )
        barrier = threading.Barrier(2)

        def attempt(service, username, email, challenge, secret):
            barrier.wait()
            try:
                service.register_user(
                    username=username,
                    email=email,
                    password="Race-safe-pass-2026!",
                    invite_code=invite["code"],
                    challenge_id=challenge["challenge_id"],
                    verification_code=secret,
                    terms_version="v2",
                )
                return "ok"
            except RegistrationError as exc:
                return exc.code

        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(
                    pool.map(
                        lambda args: attempt(*args),
                        (
                            (
                                self.service,
                                "race-user-1",
                                "race-1@example.com",
                                first_challenge,
                                "111111",
                            ),
                            (
                                second_service,
                                "race-user-2",
                                "race-2@example.com",
                                second_challenge,
                                "222222",
                            ),
                        ),
                    )
                )
        finally:
            second_connection.close()

        self.assertEqual(results.count("ok"), 1)
        failure_codes = {code for code in results if code != "ok"}
        self.assertTrue(
            failure_codes <= {"INVITE_ALREADY_USED", "INVITE_UNAVAILABLE"},
            failure_codes,
        )
        used_rows = self.connection.execute(
            "SELECT COUNT(*) FROM registration_invites WHERE used_at IS NOT NULL"
        ).fetchone()[0]
        self.assertEqual(used_rows, 1)


if __name__ == "__main__":
    unittest.main()
