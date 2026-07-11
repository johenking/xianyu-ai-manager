import hashlib
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
import subprocess
import sys
import textwrap
from unittest.mock import patch

import auth_registration_service as registration
from auth_email_service import smtp_configuration_fingerprint
from repositories.auth_repository import AuthSessionRepository, UserRepository
from security_utils import hash_user_password, verify_user_password_hash
from services.auth_service import AuthService


def _missing_behavior(*_args, **_kwargs):
    raise AssertionError("registration validation behavior is not implemented")


RegistrationError = getattr(registration, "RegistrationError", Exception)
mask_email_for_log = getattr(registration, "mask_email_for_log", _missing_behavior)
normalize_email = getattr(registration, "normalize_email", _missing_behavior)
normalize_username = registration.normalize_username
validate_password = getattr(registration, "validate_password", _missing_behavior)
RegistrationService = getattr(registration, "RegistrationService", None)


def create_registration_database(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, check_same_thread=False, timeout=10)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL DEFAULT '',
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            password_hash_v2 TEXT,
            password_hash_version INTEGER NOT NULL DEFAULT 1,
            username_normalized TEXT,
            email_normalized TEXT,
            terms_version TEXT,
            terms_accepted_at REAL
        );
        CREATE UNIQUE INDEX idx_users_username_normalized
            ON users(username_normalized);
        CREATE UNIQUE INDEX idx_users_email_normalized
            ON users(email_normalized);
        CREATE TABLE auth_sessions (
            token TEXT PRIMARY KEY,
            token_digest TEXT,
            user_id INTEGER NOT NULL,
            username TEXT NOT NULL,
            is_admin INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            last_seen_at REAL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE registration_invites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code_digest TEXT NOT NULL UNIQUE,
            code_hint TEXT NOT NULL DEFAULT '',
            note TEXT NOT NULL DEFAULT '',
            expires_at REAL NOT NULL,
            used_at REAL,
            used_by_user_id INTEGER,
            revoked_at REAL,
            created_by_user_id INTEGER,
            created_at INTEGER NOT NULL,
            FOREIGN KEY (used_by_user_id) REFERENCES users(id) ON DELETE SET NULL,
            FOREIGN KEY (created_by_user_id) REFERENCES users(id) ON DELETE SET NULL
        );
        CREATE TABLE auth_challenges (
            challenge_id TEXT PRIMARY KEY,
            purpose TEXT NOT NULL,
            subject_digest TEXT NOT NULL,
            context_digest TEXT NOT NULL DEFAULT '',
            secret_digest TEXT NOT NULL,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL,
            expires_at REAL NOT NULL,
            consumed_at REAL,
            created_at INTEGER NOT NULL
        );
        CREATE INDEX idx_auth_challenges_expiry
            ON auth_challenges(expires_at, consumed_at);
        CREATE TABLE auth_rate_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            ip_digest TEXT NOT NULL DEFAULT '',
            email_digest TEXT NOT NULL DEFAULT '',
            account_digest TEXT NOT NULL DEFAULT '',
            success INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE system_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            description TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO system_settings (key, value) VALUES ('terms_version', 'v1');
        INSERT INTO users (
            username, email, password_hash, username_normalized, email_normalized
        ) VALUES ('admin', 'admin@localhost', 'legacy', 'admin', 'admin@localhost');
        """
    )
    connection.commit()
    return connection


class IdentityValidationTests(unittest.TestCase):
    def test_username_uses_nfkc_display_value_and_casefold_key(self):
        identity = normalize_username("Ａlice")

        self.assertEqual(identity.value, "Alice")
        self.assertEqual(identity.normalized, "alice")

    def test_username_accepts_unicode_letters_digits_underscore_and_hyphen(self):
        for username in ("用户_01", "Δelta-9", "Straße", "名_字-3"):
            with self.subTest(username=username):
                identity = normalize_username(username)
                self.assertEqual(identity.value.casefold(), identity.normalized)

    def test_username_rejects_invalid_length_whitespace_and_symbols(self):
        cases = (
            ("ab", "USERNAME_INVALID_LENGTH"),
            ("a" * 25, "USERNAME_INVALID_LENGTH"),
            ("has space", "USERNAME_INVALID_CHARACTERS"),
            ("user.name", "USERNAME_INVALID_CHARACTERS"),
            ("用户!", "USERNAME_INVALID_CHARACTERS"),
        )
        for username, code in cases:
            with self.subTest(username=username):
                with self.assertRaises(RegistrationError) as raised:
                    normalize_username(username)
                self.assertEqual(raised.exception.code, code)
                self.assertTrue(raised.exception.message)
                self.assertNotIn(username, raised.exception.message)

    def test_email_is_validated_without_dns_and_lowercased(self):
        identity = normalize_email(" Mixed.User@Example.COM ")

        self.assertEqual(identity.value, "mixed.user@example.com")
        self.assertEqual(identity.normalized, "mixed.user@example.com")

    def test_invalid_email_has_stable_redacted_error(self):
        email = "not-an-email"
        with self.assertRaises(RegistrationError) as raised:
            normalize_email(email)

        self.assertEqual(raised.exception.code, "EMAIL_INVALID")
        self.assertNotIn(email, raised.exception.message)
        self.assertIsNone(raised.exception.__cause__)
        self.assertTrue(raised.exception.__suppress_context__)

    def test_email_log_mask_never_returns_full_address(self):
        email = "synthetic.user@example.test"
        masked = mask_email_for_log(email)

        self.assertNotEqual(masked, email)
        self.assertNotIn("synthetic.user", masked)
        self.assertIn("@", masked)


class PasswordPolicyTests(unittest.TestCase):
    def test_password_length_counts_unicode_characters_not_utf8_bytes(self):
        seven_characters = "安全口令甲乙丙"
        self.assertEqual(len(seven_characters), 7)
        self.assertGreaterEqual(len(seven_characters.encode("utf-8")), 8)
        with self.assertRaises(RegistrationError) as raised:
            validate_password(
                seven_characters,
                username_normalized="different-user",
            )
        self.assertEqual(raised.exception.code, "PASSWORD_TOO_SHORT")

        eight_characters = "安全口令甲乙丙丁"
        self.assertEqual(len(eight_characters), 8)
        self.assertLessEqual(len(eight_characters.encode("utf-8")), 72)
        validate_password(
            eight_characters,
            username_normalized="different-user",
        )

    def test_password_accepts_strong_value_at_bcrypt_limit(self):
        password = "安全Pass-" + ("x" * 61)
        self.assertEqual(len(password.encode("utf-8")), 72)

        validate_password(password, username_normalized="different-user")

    def test_password_rejects_more_than_72_utf8_bytes(self):
        password = "密" * 25
        with self.assertRaises(RegistrationError) as raised:
            validate_password(password, username_normalized="user")

        self.assertEqual(raised.exception.code, "PASSWORD_TOO_LONG")
        self.assertNotIn(password, raised.exception.message)

    def test_password_rejects_short_common_repeated_and_simple_values(self):
        cases = (
            ("短Pass1", "PASSWORD_TOO_SHORT"),
            ("password", "PASSWORD_TOO_WEAK"),
            ("aaaaaaaa", "PASSWORD_TOO_WEAK"),
            ("12345678", "PASSWORD_TOO_WEAK"),
            ("87654321", "PASSWORD_TOO_WEAK"),
            ("abcdefgh", "PASSWORD_TOO_WEAK"),
            ("abcabcabc", "PASSWORD_TOO_WEAK"),
        )
        for password, code in cases:
            with self.subTest(code=code):
                with self.assertRaises(RegistrationError) as raised:
                    validate_password(password, username_normalized="unrelated")
                self.assertEqual(raised.exception.code, code)
                self.assertNotIn(password, raised.exception.message)

    def test_password_rejects_nfkc_casefolded_username(self):
        with self.assertRaises(RegistrationError) as raised:
            validate_password(
                "Prefix-STRASSE-Suffix9",
                username_normalized=normalize_username("Straße").normalized,
            )

        self.assertEqual(raised.exception.code, "PASSWORD_CONTAINS_USERNAME")


class ChallengeServiceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.previous_key_file = os.environ.get("SYSTEM_SECRET_KEY_FILE")
        os.environ["SYSTEM_SECRET_KEY_FILE"] = str(self.root / ".system-key")
        self.db_path = self.root / "challenge.db"
        self.connection = create_registration_database(self.db_path)
        self.now = 1_800_100_000.0
        self.service = RegistrationService(
            self.connection,
            str(self.db_path),
            lock=threading.RLock(),
            clock=lambda: self.now,
        )

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

    def test_register_email_challenge_stores_only_bound_digests(self):
        self.assertTrue(
            hasattr(self.service, "create_challenge"),
            "RegistrationService must implement challenge persistence",
        )
        email = " Mixed.User@Example.COM "
        secret = "246810"

        challenge = self.service.create_challenge(
            purpose="register_email",
            subject=email,
            context="",
            secret=secret,
        )

        self.assertEqual(challenge["purpose"], "register_email")
        self.assertEqual(challenge["expires_at"], self.now + 600)
        self.assertEqual(challenge["max_attempts"], 5)
        self.assertNotIn("secret", repr(challenge).lower())
        self.assertNotIn("digest", repr(challenge).lower())
        row = self.connection.execute(
            "SELECT purpose, subject_digest, context_digest, secret_digest, "
            "attempt_count, max_attempts, expires_at FROM auth_challenges "
            "WHERE challenge_id = ?",
            (challenge["challenge_id"],),
        ).fetchone()
        self.assertEqual(row[0], "register_email")
        self.assertEqual(row[4:7], (0, 5, self.now + 600))
        stored_text = repr(row)
        self.assertNotIn(email.strip().lower(), stored_text.lower())
        self.assertNotIn(secret, stored_text)
        self.assertEqual(row[2], "")

    def test_challenge_enforces_purpose_subject_context_expiry_and_one_time_use(self):
        challenge = self.service.create_challenge(
            purpose="captcha",
            subject="synthetic-session",
            context="login",
            secret="A7K9",
        )
        consume = lambda **changes: self.service.consume_challenge(
            challenge_id=challenge["challenge_id"],
            purpose=changes.get("purpose", "captcha"),
            subject=changes.get("subject", "synthetic-session"),
            context=changes.get("context", "login"),
            secret=changes.get("secret", "A7K9"),
        )

        self.assert_error_code(
            "CHALLENGE_PURPOSE_MISMATCH",
            lambda: consume(purpose="register_email"),
        )
        self.assert_error_code(
            "CHALLENGE_SUBJECT_MISMATCH",
            lambda: consume(subject="other-session"),
        )
        self.assert_error_code(
            "CHALLENGE_CONTEXT_MISMATCH",
            lambda: consume(context="register"),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT attempt_count FROM auth_challenges WHERE challenge_id = ?",
                (challenge["challenge_id"],),
            ).fetchone()[0],
            0,
        )

        self.assertTrue(consume())
        self.assert_error_code("CHALLENGE_CONSUMED", consume)

        expiring = self.service.create_challenge(
            purpose="captcha",
            subject="expiring-session",
            secret="Q2W4",
        )
        self.now += 601
        self.assert_error_code(
            "CHALLENGE_EXPIRED",
            lambda: self.service.consume_challenge(
                challenge_id=expiring["challenge_id"],
                purpose="captcha",
                subject="expiring-session",
                secret="Q2W4",
            ),
        )

    def test_challenge_rejects_non_default_ttl(self):
        for ttl_seconds in (599, 601):
            with self.subTest(ttl_seconds=ttl_seconds):
                self.assert_error_code(
                    "CHALLENGE_TTL_INVALID",
                    lambda: self.service.create_challenge(
                        purpose="captcha",
                        subject="fixed-ttl-session",
                        secret="Q2W4",
                        ttl_seconds=ttl_seconds,
                    ),
                )

    def test_fifth_wrong_secret_locks_challenge_without_consuming_it(self):
        challenge = self.service.create_challenge(
            purpose="password_reset_email",
            subject="reset@example.com",
            secret="731905",
        )
        attempt = lambda: self.service.consume_challenge(
            challenge_id=challenge["challenge_id"],
            purpose="password_reset_email",
            subject="reset@example.com",
            secret="000000",
        )

        for expected_attempt in range(1, 5):
            self.assert_error_code("CHALLENGE_SECRET_INVALID", attempt)
            count = self.connection.execute(
                "SELECT attempt_count FROM auth_challenges WHERE challenge_id = ?",
                (challenge["challenge_id"],),
            ).fetchone()[0]
            self.assertEqual(count, expected_attempt)
        self.assert_error_code("CHALLENGE_LOCKED", attempt)
        self.assert_error_code("CHALLENGE_LOCKED", attempt)
        attempts, consumed_at = self.connection.execute(
            "SELECT attempt_count, consumed_at FROM auth_challenges WHERE challenge_id = ?",
            (challenge["challenge_id"],),
        ).fetchone()
        self.assertEqual(attempts, 5)
        self.assertIsNone(consumed_at)

    def test_challenge_digests_are_isolated_by_purpose(self):
        first = self.service.create_challenge(
            purpose="register_email",
            subject="isolated@example.com",
            context="",
            secret="314159",
        )
        second = self.service.create_challenge(
            purpose="password_reset_email",
            subject="isolated@example.com",
            secret="314159",
        )
        rows = self.connection.execute(
            "SELECT subject_digest, secret_digest FROM auth_challenges "
            "WHERE challenge_id IN (?, ?) ORDER BY challenge_id",
            (first["challenge_id"], second["challenge_id"]),
        ).fetchall()

        self.assertNotEqual(rows[0][0], rows[1][0])
        self.assertNotEqual(rows[0][1], rows[1][1])
        self.assert_error_code(
            "CHALLENGE_PURPOSE_INVALID",
            lambda: self.service.create_challenge(
                purpose="other",
                subject="synthetic",
                secret="synthetic-secret",
            ),
        )
        self.assert_error_code(
            "CHALLENGE_MAX_ATTEMPTS_INVALID",
            lambda: self.service.create_challenge(
                purpose="captcha",
                subject="synthetic-session",
                secret="A1B2",
                max_attempts=6,
            ),
        )

    def test_challenge_creation_cleans_only_old_records_in_a_bounded_batch(self):
        cutoff = self.now - 86_400
        batch_size = 100
        old_rows = [
            (
                f"old-expired-{index}",
                "captcha",
                f"subject-{index}",
                "",
                f"secret-{index}",
                0,
                5,
                cutoff - index - 1,
                None,
                int(cutoff - index - 601),
            )
            for index in range(batch_size + 2)
        ]
        retained_rows = (
            (
                "old-consumed",
                "captcha",
                "old-consumed-subject",
                "",
                "old-consumed-secret",
                0,
                5,
                self.now + 600,
                cutoff - 1,
                int(cutoff - 700),
            ),
            (
                "recent-expired",
                "captcha",
                "recent-expired-subject",
                "",
                "recent-expired-secret",
                0,
                5,
                cutoff + 1,
                None,
                int(cutoff - 599),
            ),
            (
                "recent-consumed",
                "captcha",
                "recent-consumed-subject",
                "",
                "recent-consumed-secret",
                0,
                5,
                self.now + 600,
                self.now - 1,
                int(self.now - 10),
            ),
        )
        self.connection.executemany(
            "INSERT INTO auth_challenges ("
            "challenge_id, purpose, subject_digest, context_digest, secret_digest, "
            "attempt_count, max_attempts, expires_at, consumed_at, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (*old_rows, *retained_rows),
        )
        self.connection.commit()

        created = self.service.create_challenge(
            purpose="captcha",
            subject="current-session",
            secret="N4W6",
        )

        remaining_old = self.connection.execute(
            "SELECT COUNT(*) FROM auth_challenges WHERE challenge_id LIKE 'old-expired-%'"
        ).fetchone()[0]
        self.assertEqual(remaining_old, 2)
        self.assertIsNotNone(
            self.connection.execute(
                "SELECT 1 FROM auth_challenges WHERE challenge_id = 'old-consumed'"
            ).fetchone()
        )

        second = self.service.create_challenge(
            purpose="captcha",
            subject="second-current-session",
            secret="P5X7",
        )

        self.assertIsNone(
            self.connection.execute(
                "SELECT 1 FROM auth_challenges WHERE challenge_id = 'old-consumed'"
            ).fetchone()
        )
        retained = {
            row[0]
            for row in self.connection.execute(
                "SELECT challenge_id FROM auth_challenges"
            ).fetchall()
        }
        self.assertIn("recent-expired", retained)
        self.assertIn("recent-consumed", retained)
        self.assertIn(created["challenge_id"], retained)
        self.assertIn(second["challenge_id"], retained)


class RegistrationServiceFixture(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.previous_key_file = os.environ.get("SYSTEM_SECRET_KEY_FILE")
        os.environ["SYSTEM_SECRET_KEY_FILE"] = str(self.root / ".system-key")
        self.db_path = self.root / "transactions.db"
        self.connection = create_registration_database(self.db_path)
        self.now = 1_800_200_000.0
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

    def issue_registration_challenge(self, email, *, invite=None, secret="482615"):
        challenge = self.service.create_challenge(
            purpose="register_email",
            subject=email,
            context="",
            secret=secret,
        )
        return invite, challenge, secret

    def register(self, *, username, email, invite, challenge, secret, terms="v2"):
        return self.service.register_user(
            username=username,
            email=email,
            password="Strong-pass-2026!",
            invite_code="ignored" if invite is None else str(invite),
            challenge_id=challenge["challenge_id"],
            verification_code=secret,
            terms_version=terms,
        )

    def assert_error_code(self, code, callback):
        with self.assertRaises(RegistrationError) as raised:
            callback()
        self.assertEqual(raised.exception.code, code)


class SMTPConfirmationTransactionTests(RegistrationServiceFixture):
    def issue_confirmation(self, secret="538204"):
        self.connection.execute(
            "UPDATE system_settings SET value = 'support@example.com' "
            "WHERE key = 'support_email'"
        )
        self.connection.commit()
        settings = {
            row[0]: row[1]
            for row in self.connection.execute(
                "SELECT key, value FROM system_settings"
            ).fetchall()
        }
        fingerprint = smtp_configuration_fingerprint(
            settings,
            db_path=str(self.db_path),
        )
        self.connection.execute(
            "UPDATE system_settings SET value = '' "
            "WHERE key = 'smtp_verified_fingerprint'"
        )
        self.connection.commit()
        challenge = self.service.create_challenge(
            purpose="smtp_verify_email",
            subject=settings["support_email"],
            context=fingerprint,
            secret=secret,
        )
        return challenge, secret, fingerprint

    def test_concurrent_smtp_change_rolls_back_confirmation_and_allows_retry(self):
        challenge, secret, original_fingerprint = self.issue_confirmation()
        second_connection = sqlite3.connect(
            self.db_path,
            check_same_thread=False,
            timeout=10,
        )
        second_connection.execute("BEGIN IMMEDIATE")
        second_connection.execute(
            "UPDATE system_settings SET value = ? WHERE key = 'smtp_server'",
            ("changed.smtp.example.test",),
        )

        def confirm():
            return self.service.confirm_smtp_verification(
                challenge_id=challenge["challenge_id"],
                verification_code=secret,
                verified_at="2026-07-11T16:30:00+08:00",
            )

        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(confirm)
                second_connection.commit()
                with self.assertRaises(RegistrationError) as raised:
                    future.result()
            self.assertEqual(
                raised.exception.code,
                "CHALLENGE_CONTEXT_MISMATCH",
            )
            consumed_at = self.connection.execute(
                "SELECT consumed_at FROM auth_challenges WHERE challenge_id = ?",
                (challenge["challenge_id"],),
            ).fetchone()[0]
            self.assertIsNone(consumed_at)
            self.assertNotEqual(
                self.connection.execute(
                    "SELECT value FROM system_settings "
                    "WHERE key = 'smtp_verified_fingerprint'"
                ).fetchone()[0],
                original_fingerprint,
            )

            second_connection.execute(
                "UPDATE system_settings SET value = ? WHERE key = 'smtp_server'",
                ("smtp.example.test",),
            )
            second_connection.commit()
            result = confirm()
            self.assertEqual(result["fingerprint"], original_fingerprint)
        finally:
            second_connection.close()

    def test_wrong_smtp_codes_persist_attempts_and_lock_at_five(self):
        challenge, _, _ = self.issue_confirmation()

        def confirm_wrong():
            self.service.confirm_smtp_verification(
                challenge_id=challenge["challenge_id"],
                verification_code="000000",
                verified_at="2026-07-11T16:31:00+08:00",
            )

        for expected_attempt in range(1, 5):
            self.assert_error_code("CHALLENGE_SECRET_INVALID", confirm_wrong)
            attempt_count = self.connection.execute(
                "SELECT attempt_count FROM auth_challenges WHERE challenge_id = ?",
                (challenge["challenge_id"],),
            ).fetchone()[0]
            self.assertEqual(attempt_count, expected_attempt)
        self.assert_error_code("CHALLENGE_LOCKED", confirm_wrong)
        self.assert_error_code("CHALLENGE_LOCKED", confirm_wrong)
        attempt_count, consumed_at = self.connection.execute(
            "SELECT attempt_count, consumed_at FROM auth_challenges "
            "WHERE challenge_id = ?",
            (challenge["challenge_id"],),
        ).fetchone()
        self.assertEqual(attempt_count, 5)
        self.assertIsNone(consumed_at)


class DirectRegistrationTransactionTests(RegistrationServiceFixture):
    smtp_settings = {
        "smtp_server": "smtp.example.test",
        "smtp_port": "587",
        "smtp_user": "sender@example.test",
        "smtp_password": "synthetic-smtp-secret",
        "smtp_from": "Xianyu Manager",
        "smtp_use_tls": "true",
        "smtp_use_ssl": "false",
        "support_email": "support@example.test",
    }

    def setUp(self):
        super().setUp()
        settings = {
            **self.smtp_settings,
            "registration_enabled": "true",
            "registration_user_limit": "20",
            "terms_version": "v2",
        }
        fingerprint = smtp_configuration_fingerprint(
            settings,
            db_path=str(self.db_path),
        )
        settings["smtp_verified_fingerprint"] = fingerprint
        self.connection.executemany(
            "INSERT INTO system_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            settings.items(),
        )
        self.connection.commit()

    def issue_direct_challenge(self, email, secret="482615"):
        challenge = self.service.create_challenge(
            purpose="register_email",
            subject=email,
            context="",
            secret=secret,
        )
        return challenge, secret

    def direct_register(self, username, email, challenge, secret, invite_code=""):
        return self.service.register_user(
            username=username,
            email=email,
            password="Strong-pass-2026!",
            invite_code=invite_code,
            challenge_id=challenge["challenge_id"],
            verification_code=secret,
            terms_version="v2",
        )

    def test_direct_registration_ignores_legacy_invite_field_and_consumes_empty_context_code(self):
        challenge, secret = self.issue_direct_challenge("direct@example.com")

        user = self.direct_register(
            "direct-user",
            "direct@example.com",
            challenge,
            secret,
            invite_code="legacy-client-value",
        )

        self.assertEqual(user["terms_version"], "v2")
        self.assertEqual(
            self.connection.execute(
                "SELECT consumed_at FROM auth_challenges WHERE challenge_id = ?",
                (challenge["challenge_id"],),
            ).fetchone()[0],
            self.now,
        )
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM registration_invites").fetchone()[0],
            0,
        )

    def test_capacity_excludes_admin_and_includes_disabled_users(self):
        self.connection.execute(
            "INSERT INTO users "
            "(username, email, password_hash, username_normalized, email_normalized, is_active) "
            "VALUES ('disabled-user', 'disabled@example.test', 'legacy', "
            "'disabled-user', 'disabled@example.test', 0)"
        )
        self.connection.commit()

        capacity = self.service.registration_capacity()

        self.assertEqual(capacity["user_count"], 1)
        self.assertEqual(capacity["user_limit"], 20)
        self.assertEqual(capacity["remaining_slots"], 19)

    def test_lowering_limit_closes_registration_and_raising_never_reopens_it(self):
        for invalid_limit in (0, 1001, True):
            with self.subTest(invalid_limit=invalid_limit):
                with self.assertRaises(RegistrationError) as raised:
                    self.service.update_registration_limit(invalid_limit)
                self.assertEqual(
                    raised.exception.code,
                    "REGISTRATION_USER_LIMIT_INVALID",
                )
        self.connection.execute(
            "INSERT INTO users "
            "(username, email, password_hash, username_normalized, email_normalized) "
            "VALUES ('existing-user', 'existing@example.test', 'legacy', "
            "'existing-user', 'existing@example.test')"
        )
        self.connection.commit()

        lowered = self.service.update_registration_limit(1)
        self.assertEqual(lowered["remaining_slots"], 0)
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM users").fetchone()[0],
            2,
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT value FROM system_settings WHERE key = 'registration_enabled'"
            ).fetchone()[0],
            "false",
        )
        raised_limit = self.service.update_registration_limit(2)
        self.assertEqual(raised_limit["remaining_slots"], 1)
        self.assertEqual(
            self.connection.execute(
                "SELECT value FROM system_settings WHERE key = 'registration_enabled'"
            ).fetchone()[0],
            "false",
        )

    def test_direct_registration_requires_terms_v2_even_if_setting_regresses(self):
        self.connection.execute(
            "UPDATE system_settings SET value = 'v1' WHERE key = 'terms_version'"
        )
        self.connection.commit()
        challenge, secret = self.issue_direct_challenge("terms-v2@example.com")

        with self.assertRaises(RegistrationError) as raised:
            self.service.register_user(
                username="terms-v2-user",
                email="terms-v2@example.com",
                password="Strong-pass-2026!",
                challenge_id=challenge["challenge_id"],
                verification_code=secret,
                terms_version="v1",
            )

        self.assertEqual(raised.exception.code, "REGISTRATION_CLOSED")
        self.assertIsNone(
            self.connection.execute(
                "SELECT consumed_at FROM auth_challenges WHERE challenge_id = ?",
                (challenge["challenge_id"],),
            ).fetchone()[0]
        )

    def test_concurrent_last_slot_allows_exactly_one_registration(self):
        self.service.update_registration_limit(1)
        self.connection.execute(
            "UPDATE system_settings SET value = 'true' WHERE key = 'registration_enabled'"
        )
        self.connection.commit()
        first = self.issue_direct_challenge("race-one@example.com")
        second = self.issue_direct_challenge("race-two@example.com")
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
        barrier = threading.Barrier(2)

        def attempt(service, username, email, challenge, secret):
            barrier.wait()
            try:
                user = service.register_user(
                    username=username,
                    email=email,
                    password="Race-safe-pass-2026!",
                    invite_code="ignored",
                    challenge_id=challenge["challenge_id"],
                    verification_code=secret,
                    terms_version="v2",
                )
                return "ok", user["id"]
            except RegistrationError as exc:
                return "error", exc.code

        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(
                    pool.map(
                        lambda args: attempt(*args),
                        (
                            (
                                self.service,
                                "race-user-one",
                                "race-one@example.com",
                                first[0],
                                first[1],
                            ),
                            (
                                second_service,
                                "race-user-two",
                                "race-two@example.com",
                                second[0],
                                second[1],
                            ),
                        ),
                    )
                )
        finally:
            second_connection.close()

        self.assertEqual([result[0] for result in results].count("ok"), 1)
        self.assertEqual([result[0] for result in results].count("error"), 1)
        self.assertEqual(
            [result[1] for result in results if result[0] == "error"],
            ["REGISTRATION_CLOSED"],
        )
        self.assertEqual(self.service.registration_capacity()["user_count"], 1)
        self.assertEqual(
            self.connection.execute(
                "SELECT value FROM system_settings WHERE key = 'registration_enabled'"
            ).fetchone()[0],
            "false",
        )


class PasswordResetAndAccountStateTests(RegistrationServiceFixture):
    def create_user(self, username="reset-user", email="reset@example.com"):
        invite, challenge, secret = self.issue_registration_challenge(email)
        return self.register(
            username=username,
            email=email,
            invite=invite,
            challenge=challenge,
            secret=secret,
        )

    def test_password_reset_updates_hash_consumes_challenge_and_revokes_sessions(self):
        user = self.create_user()
        self.connection.execute(
            "INSERT INTO auth_sessions "
            "(token, token_digest, user_id, username, created_at, expires_at) "
            "VALUES ('digest:one', 'one', ?, ?, ?, ?)",
            (user["id"], user["username"], self.now, self.now + 3600),
        )
        self.connection.commit()
        challenge = self.service.create_challenge(
            purpose="password_reset_email",
            subject="RESET@EXAMPLE.COM",
            secret="930517",
        )

        user_id = self.service.reset_password(
            email="reset@example.com",
            new_password="New-reset-pass-2026!",
            challenge_id=challenge["challenge_id"],
            verification_code="930517",
        )

        self.assertEqual(user_id, user["id"])
        password_hash = self.connection.execute(
            "SELECT password_hash_v2 FROM users WHERE id = ?", (user["id"],)
        ).fetchone()[0]
        self.assertTrue(verify_user_password_hash("New-reset-pass-2026!", password_hash))
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM auth_sessions WHERE user_id = ?", (user["id"],)
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT consumed_at FROM auth_challenges WHERE challenge_id = ?",
                (challenge["challenge_id"],),
            ).fetchone()[0],
            self.now,
        )

    def test_password_reset_grant_is_digest_only_one_time_and_revokes_on_reset(self):
        user = self.create_user()
        old_hash = self.connection.execute(
            "SELECT password_hash_v2 FROM users WHERE id = ?", (user["id"],)
        ).fetchone()[0]
        self.connection.execute(
            "INSERT INTO auth_sessions "
            "(token, token_digest, user_id, username, created_at, expires_at) "
            "VALUES ('digest:grant-session', 'grant-session', ?, ?, ?, ?)",
            (user["id"], user["username"], self.now, self.now + 3600),
        )
        self.connection.commit()
        email_challenge = self.service.create_challenge(
            purpose="password_reset_email",
            subject="reset@example.com",
            secret="930517",
        )

        grant = self.service.verify_password_reset_code(
            email="RESET@EXAMPLE.COM",
            challenge_id=email_challenge["challenge_id"],
            verification_code="930517",
        )

        self.assertEqual(grant["expires_at"], self.now + 600)
        self.assertNotIn(grant["grant_token"], repr(grant["grant_id"]))
        stored_grant = self.connection.execute(
            "SELECT purpose, secret_digest, consumed_at FROM auth_challenges "
            "WHERE challenge_id = ?",
            (grant["grant_id"],),
        ).fetchone()
        self.assertEqual(stored_grant[0], "password_reset_grant")
        self.assertNotIn(grant["grant_token"], stored_grant[1])
        self.assertIsNone(stored_grant[2])
        self.assertEqual(
            self.connection.execute(
                "SELECT consumed_at FROM auth_challenges WHERE challenge_id = ?",
                (email_challenge["challenge_id"],),
            ).fetchone()[0],
            self.now,
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT password_hash_v2 FROM users WHERE id = ?", (user["id"],)
            ).fetchone()[0],
            old_hash,
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM auth_sessions WHERE user_id = ?", (user["id"],)
            ).fetchone()[0],
            1,
        )

        user_id = self.service.reset_password_with_grant(
            email="reset@example.com",
            new_password="New-reset-pass-2026!",
            grant_id=grant["grant_id"],
            grant_token=grant["grant_token"],
        )

        self.assertEqual(user_id, user["id"])
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM auth_sessions WHERE user_id = ?", (user["id"],)
            ).fetchone()[0],
            0,
        )
        self.assertTrue(
            verify_user_password_hash(
                "New-reset-pass-2026!",
                self.connection.execute(
                    "SELECT password_hash_v2 FROM users WHERE id = ?", (user["id"],)
                ).fetchone()[0],
            )
        )
        self.assert_error_code(
            "CHALLENGE_CONSUMED",
            lambda: self.service.reset_password_with_grant(
                email="reset@example.com",
                new_password="Another-reset-pass-2026!",
                grant_id=grant["grant_id"],
                grant_token=grant["grant_token"],
            ),
        )

    def test_weak_password_does_not_consume_reset_grant(self):
        self.create_user()
        email_challenge = self.service.create_challenge(
            purpose="password_reset_email",
            subject="reset@example.com",
            secret="930517",
        )
        grant = self.service.verify_password_reset_code(
            email="reset@example.com",
            challenge_id=email_challenge["challenge_id"],
            verification_code="930517",
        )

        self.assert_error_code(
            "PASSWORD_TOO_WEAK",
            lambda: self.service.reset_password_with_grant(
                email="reset@example.com",
                new_password="password",
                grant_id=grant["grant_id"],
                grant_token=grant["grant_token"],
            ),
        )
        self.assertIsNone(
            self.connection.execute(
                "SELECT consumed_at FROM auth_challenges WHERE challenge_id = ?",
                (grant["grant_id"],),
            ).fetchone()[0]
        )

        self.service.reset_password_with_grant(
            email="reset@example.com",
            new_password="Valid-reset-pass-2026!",
            grant_id=grant["grant_id"],
            grant_token=grant["grant_token"],
        )

    def test_reset_code_can_issue_only_one_grant_concurrently(self):
        self.create_user()
        challenge = self.service.create_challenge(
            purpose="password_reset_email",
            subject="reset@example.com",
            secret="930517",
        )

        def verify():
            try:
                self.service.verify_password_reset_code(
                    email="reset@example.com",
                    challenge_id=challenge["challenge_id"],
                    verification_code="930517",
                )
                return "ok"
            except RegistrationError as exc:
                return exc.code

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: verify(), range(2)))

        self.assertEqual(results.count("ok"), 1)
        self.assertEqual(results.count("CHALLENGE_CONSUMED"), 1)
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM auth_challenges "
                "WHERE purpose = 'password_reset_grant'"
            ).fetchone()[0],
            1,
        )

    def test_reset_code_verification_enforces_attempt_expiry_and_purpose(self):
        self.create_user()
        challenge = self.service.create_challenge(
            purpose="password_reset_email",
            subject="reset@example.com",
            secret="930517",
        )

        for expected_attempt in range(1, 5):
            self.assert_error_code(
                "CHALLENGE_SECRET_INVALID",
                lambda: self.service.verify_password_reset_code(
                    email="reset@example.com",
                    challenge_id=challenge["challenge_id"],
                    verification_code="000000",
                ),
            )
            attempts = self.connection.execute(
                "SELECT attempt_count FROM auth_challenges WHERE challenge_id = ?",
                (challenge["challenge_id"],),
            ).fetchone()[0]
            self.assertEqual(attempts, expected_attempt)

        self.assert_error_code(
            "CHALLENGE_LOCKED",
            lambda: self.service.verify_password_reset_code(
                email="reset@example.com",
                challenge_id=challenge["challenge_id"],
                verification_code="000000",
            ),
        )
        self.assert_error_code(
            "CHALLENGE_LOCKED",
            lambda: self.service.verify_password_reset_code(
                email="reset@example.com",
                challenge_id=challenge["challenge_id"],
                verification_code="930517",
            ),
        )

        expired = self.service.create_challenge(
            purpose="password_reset_email",
            subject="reset@example.com",
            secret="135790",
        )
        self.connection.execute(
            "UPDATE auth_challenges SET expires_at = ? WHERE challenge_id = ?",
            (self.now - 1, expired["challenge_id"]),
        )
        self.connection.commit()
        self.assert_error_code(
            "CHALLENGE_EXPIRED",
            lambda: self.service.verify_password_reset_code(
                email="reset@example.com",
                challenge_id=expired["challenge_id"],
                verification_code="135790",
            ),
        )

        wrong_purpose = self.service.create_challenge(
            purpose="register_email",
            subject="reset@example.com",
            secret="246802",
        )
        self.assert_error_code(
            "CHALLENGE_PURPOSE_MISMATCH",
            lambda: self.service.verify_password_reset_code(
                email="reset@example.com",
                challenge_id=wrong_purpose["challenge_id"],
                verification_code="246802",
            ),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM auth_challenges "
                "WHERE purpose = 'password_reset_grant'"
            ).fetchone()[0],
            0,
        )

    def test_reset_grant_is_email_bound_and_expires(self):
        self.create_user()
        challenge = self.service.create_challenge(
            purpose="password_reset_email",
            subject="reset@example.com",
            secret="930517",
        )
        grant = self.service.verify_password_reset_code(
            email="reset@example.com",
            challenge_id=challenge["challenge_id"],
            verification_code="930517",
        )

        self.assert_error_code(
            "CHALLENGE_SUBJECT_MISMATCH",
            lambda: self.service.reset_password_with_grant(
                email="other@example.com",
                new_password="Valid-reset-pass-2026!",
                grant_id=grant["grant_id"],
                grant_token=grant["grant_token"],
            ),
        )
        self.now += 601
        self.assert_error_code(
            "CHALLENGE_EXPIRED",
            lambda: self.service.reset_password_with_grant(
                email="reset@example.com",
                new_password="Valid-reset-pass-2026!",
                grant_id=grant["grant_id"],
                grant_token=grant["grant_token"],
            ),
        )

    def test_failed_password_reset_only_records_wrong_attempt(self):
        user = self.create_user()
        old_hash = self.connection.execute(
            "SELECT password_hash_v2 FROM users WHERE id = ?", (user["id"],)
        ).fetchone()[0]
        self.connection.execute(
            "INSERT INTO auth_sessions "
            "(token, token_digest, user_id, username, created_at, expires_at) "
            "VALUES ('digest:old', 'old', ?, ?, ?, ?)",
            (user["id"], user["username"], self.now, self.now + 3600),
        )
        self.connection.commit()
        challenge = self.service.create_challenge(
            purpose="password_reset_email",
            subject="reset@example.com",
            secret="930517",
        )

        self.assert_error_code(
            "CHALLENGE_SECRET_INVALID",
            lambda: self.service.reset_password(
                email="reset@example.com",
                new_password="Another-pass-2026!",
                challenge_id=challenge["challenge_id"],
                verification_code="000000",
            ),
        )

        current_hash = self.connection.execute(
            "SELECT password_hash_v2 FROM users WHERE id = ?", (user["id"],)
        ).fetchone()[0]
        attempts, consumed_at = self.connection.execute(
            "SELECT attempt_count, consumed_at FROM auth_challenges WHERE challenge_id = ?",
            (challenge["challenge_id"],),
        ).fetchone()
        self.assertEqual(current_hash, old_hash)
        self.assertEqual((attempts, consumed_at), (1, None))
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM auth_sessions WHERE user_id = ?", (user["id"],)
            ).fetchone()[0],
            1,
        )

    def test_invalid_reset_code_does_not_run_bcrypt(self):
        self.create_user()
        challenge = self.service.create_challenge(
            purpose="password_reset_email",
            subject="reset@example.com",
            secret="930517",
        )

        with patch.object(
            registration,
            "hash_user_password",
            side_effect=AssertionError("bcrypt must not run"),
        ) as password_hasher:
            self.assert_error_code(
                "CHALLENGE_SECRET_INVALID",
                lambda: self.service.reset_password(
                    email="reset@example.com",
                    new_password="Another-pass-2026!",
                    challenge_id=challenge["challenge_id"],
                    verification_code="000000",
                ),
            )

        password_hasher.assert_not_called()

    def test_legacy_upgrade_cannot_overwrite_a_concurrent_password_reset(self):
        legacy_password = "legacy-pass"
        legacy_hash = hashlib.sha256(legacy_password.encode("utf-8")).hexdigest()
        self.connection.execute(
            "INSERT INTO users ("
            "username, username_normalized, email, email_normalized, password_hash"
            ") VALUES (?, ?, ?, ?, ?)",
            (
                "legacy-race",
                "legacy-race",
                "legacy-race@example.com",
                "legacy-race@example.com",
                legacy_hash,
            ),
        )
        self.connection.commit()
        reset_hash = hash_user_password("Reset-pass-2026!")
        candidate_upgrade = hash_user_password(legacy_password)
        second_connection = sqlite3.connect(self.db_path, timeout=10)
        auth = AuthService(
            UserRepository(self.connection),
            AuthSessionRepository(self.connection),
            lock=threading.RLock(),
        )

        def reset_before_upgrade(_password):
            second_connection.execute(
                "UPDATE users SET password_hash = '', password_hash_v2 = ?, "
                "password_hash_version = 2 WHERE username_normalized = ?",
                (reset_hash, "legacy-race"),
            )
            second_connection.commit()
            return candidate_upgrade

        try:
            with patch(
                "services.auth_service.hash_user_password",
                side_effect=reset_before_upgrade,
            ):
                self.assertFalse(
                    auth.verify_password("legacy-race", legacy_password)
                )
        finally:
            second_connection.close()

        stored_legacy, stored_v2 = self.connection.execute(
            "SELECT password_hash, password_hash_v2 FROM users "
            "WHERE username_normalized = 'legacy-race'"
        ).fetchone()
        self.assertEqual(stored_legacy, "")
        self.assertEqual(stored_v2, reset_hash)
        self.assertTrue(verify_user_password_hash("Reset-pass-2026!", stored_v2))

    def test_account_service_deactivation_revokes_sessions_and_protects_admin(self):
        user = self.create_user(username="ordinary-user", email="ordinary@example.com")
        self.connection.executemany(
            "INSERT INTO auth_sessions "
            "(token, token_digest, user_id, username, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                ("digest:u", "u", user["id"], user["username"], self.now, self.now + 60),
                ("digest:a", "a", 1, "admin", self.now, self.now + 60),
            ),
        )
        self.connection.commit()
        users = UserRepository(self.connection)
        sessions = AuthSessionRepository(self.connection)
        auth = AuthService(users, sessions)

        updated = auth.set_user_active(user["id"], False)

        self.assertFalse(updated["is_active"])
        self.assertFalse(any("password" in key or "hash" in key for key in updated))
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM auth_sessions WHERE user_id = ?", (user["id"],)
            ).fetchone()[0],
            0,
        )
        self.assert_error_code(
            "ADMIN_DEACTIVATION_FORBIDDEN",
            lambda: auth.set_user_active(1, False),
        )
        self.assertTrue(users.get_by_id(1)["is_active"])
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM auth_sessions WHERE user_id = 1"
            ).fetchone()[0],
            1,
        )

    def test_repository_identifier_password_active_and_recent_methods(self):
        user = self.create_user(username="Straße-user", email="repo@example.com")
        users = UserRepository(self.connection)
        sessions = AuthSessionRepository(self.connection)
        auth = AuthService(users, sessions)

        self.assertEqual(users.get_by_identifier("STRASSE-USER")["id"], user["id"])
        self.assertEqual(users.get_by_identifier("REPO@EXAMPLE.COM")["id"], user["id"])
        self.assertTrue(
            auth.verify_password("REPO@EXAMPLE.COM", "Strong-pass-2026!")
        )
        self.assertEqual(users.get_by_id(user["id"])["terms_version"], "v2")
        self.assertEqual(users.list_recent(limit=1)[0]["id"], user["id"])
        self.assertEqual(users.set_password_by_id(user["id"], "synthetic-hash", 2), 1)
        self.assertEqual(users.set_active(user["id"], False), 1)
        self.assertFalse(auth.verify_password("Straße-user", "Strong-pass-2026!"))
        sessions.delete_by_user_id(user["id"])
        self.connection.commit()

    def test_repository_nfkc_fallback_finds_legacy_normalized_rows(self):
        self.connection.execute(
            "INSERT INTO users ("
            "username, username_normalized, email, email_normalized, password_hash"
            ") VALUES (?, ?, ?, ?, '')",
            (
                "Ｆｏｏ_User",
                "ｆｏｏ_user",
                "legacy-nfkc@example.com",
                "legacy-nfkc@example.com",
            ),
        )
        self.connection.commit()
        users = UserRepository(self.connection)

        user = users.get_by_identifier("Foo_User")

        self.assertIsNotNone(user)
        self.assertEqual(user["username_normalized"], "foo_user")
        with self.assertRaises(sqlite3.IntegrityError):
            users.create(
                "Foo_User",
                "new-nfkc@example.com",
                "synthetic-hash",
                2,
            )


class DBManagerIntegrationTests(unittest.TestCase):
    def test_db_manager_exposes_registration_and_rate_limit_services(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            environment = os.environ.copy()
            environment["DB_PATH"] = str(root / "global.db")
            environment["SYSTEM_SECRET_KEY_FILE"] = str(root / ".system-key")
            script = textwrap.dedent(
                """
                import db_manager

                manager = db_manager.db_manager
                assert hasattr(manager, "registration_service")
                assert hasattr(manager, "auth_rate_limiter")
                assert manager.registration_service.connection is manager.conn
                assert manager.auth_rate_limiter.connection is manager.conn
                assert manager.auth_service.sessions is manager.auth_session_repository
                assert manager.auth_service.lock is manager.lock
                expected = {
                    "is_active", "username_normalized", "email_normalized",
                    "terms_version", "terms_accepted_at",
                }
                assert expected <= manager.get_user_by_id(1).keys()
                assert expected <= manager.get_all_users()[0].keys()
                assert not any(
                    "password" in key or "hash" in key
                    for key in manager.get_user_by_id(1)
                )
                assert not any(
                    "password" in key or "hash" in key
                    for key in manager.get_all_users()[0]
                )
                assert manager.get_user_by_username("ADMIN")["id"] == 1
                assert manager.get_user_by_email("ADMIN@LOCALHOST")["id"] == 1
                manager.close()
                """
            )

            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=Path(__file__).resolve().parents[1],
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
