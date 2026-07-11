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


class InvitationServiceTests(unittest.TestCase):
    def setUp(self):
        if RegistrationService is None:
            self.fail("RegistrationService must implement invitation persistence")
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.previous_key_file = os.environ.get("SYSTEM_SECRET_KEY_FILE")
        os.environ["SYSTEM_SECRET_KEY_FILE"] = str(self.root / ".system-key")
        self.db_path = self.root / "registration.db"
        self.connection = create_registration_database(self.db_path)
        self.lock = threading.RLock()
        self.now = 1_800_000_000.0
        self.service = RegistrationService(
            self.connection,
            str(self.db_path),
            lock=self.lock,
            clock=lambda: self.now,
        )

    def tearDown(self):
        if hasattr(self, "connection"):
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
        self.assertTrue(raised.exception.message)

    def test_create_and_list_invites_never_leaks_raw_or_digest(self):
        created = self.service.create_invites(
            count=2,
            valid_days=30,
            note="synthetic onboarding batch",
            created_by_user_id=1,
        )

        self.assertEqual(len(created), 2)
        raw_codes = [item["code"] for item in created]
        self.assertEqual(len(set(raw_codes)), 2)
        for item in created:
            self.assertEqual(item["status"], "active")
            self.assertGreaterEqual(len(item["code"]), 24)
            self.assertFalse(set(item["code"]) & set("0O1IL"))
            self.assertNotIn("digest", repr(item).lower())

        listed = self.service.list_invites()
        self.assertEqual(len(listed), 2)
        for item in listed:
            self.assertNotIn("code", item)
            self.assertNotIn("code_digest", item)
            self.assertNotIn("digest", repr(item).lower())
        for code in raw_codes:
            self.assertNotIn(code, repr(listed))
            stored = self.connection.execute(
                "SELECT code_digest FROM registration_invites WHERE code_hint = ?",
                (created[raw_codes.index(code)]["hint"],),
            ).fetchone()[0]
            self.assertNotEqual(stored, code)
            self.assertEqual(len(stored), 64)

    def test_invite_batch_and_metadata_boundaries_have_stable_errors(self):
        cases = (
            ("INVITE_COUNT_INVALID", lambda: self.service.create_invites(count=0)),
            ("INVITE_COUNT_INVALID", lambda: self.service.create_invites(count=21)),
            ("INVITE_VALID_DAYS_INVALID", lambda: self.service.create_invites(valid_days=0)),
            ("INVITE_VALID_DAYS_INVALID", lambda: self.service.create_invites(valid_days=366)),
            ("INVITE_NOTE_TOO_LONG", lambda: self.service.create_invites(note="n" * 201)),
        )
        for code, callback in cases:
            with self.subTest(code=code):
                self.assert_error_code(code, callback)

    def test_expired_and_revoked_invites_are_not_active(self):
        expiring = self.service.create_invites(valid_days=1)[0]
        revoked = self.service.create_invites(valid_days=30)[0]

        self.assertTrue(self.service.active_invite_exists(expiring["code"]))
        self.service.revoke_invite(revoked["id"])
        self.assertFalse(self.service.active_invite_exists(revoked["code"]))
        self.assert_error_code(
            "INVITE_REVOKED",
            lambda: self.service.revoke_invite(revoked["id"]),
        )

        self.now += 86_401
        self.assertFalse(self.service.active_invite_exists(expiring["code"]))
        statuses = {item["id"]: item["status"] for item in self.service.list_invites()}
        self.assertEqual(statuses[expiring["id"]], "expired")
        self.assertEqual(statuses[revoked["id"]], "revoked")


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
        invite = self.service.create_invites()[0]
        email = " Mixed.User@Example.COM "
        secret = "246810"

        challenge = self.service.create_challenge(
            purpose="register_email",
            subject=email,
            context=invite["code"],
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
        self.assertNotIn(invite["code"], stored_text)
        self.assertNotIn(secret, stored_text)
        invite_digest = self.connection.execute(
            "SELECT code_digest FROM registration_invites WHERE id = ?",
            (invite["id"],),
        ).fetchone()[0]
        self.assertEqual(row[2], invite_digest)

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
            context=self.service.create_invites()[0]["code"],
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

    def tearDown(self):
        self.connection.close()
        if self.previous_key_file is None:
            os.environ.pop("SYSTEM_SECRET_KEY_FILE", None)
        else:
            os.environ["SYSTEM_SECRET_KEY_FILE"] = self.previous_key_file
        self.tempdir.cleanup()

    def issue_registration_challenge(self, email, *, invite=None, secret="482615"):
        invite = invite or self.service.create_invites()[0]
        challenge = self.service.create_challenge(
            purpose="register_email",
            subject=email,
            context=invite["code"],
            secret=secret,
        )
        return invite, challenge, secret

    def register(self, *, username, email, invite, challenge, secret, terms="v1"):
        return self.service.register_user(
            username=username,
            email=email,
            password="Strong-pass-2026!",
            invite_code=invite["code"],
            challenge_id=challenge["challenge_id"],
            verification_code=secret,
            terms_version=terms,
        )

    def assert_error_code(self, code, callback):
        with self.assertRaises(RegistrationError) as raised:
            callback()
        self.assertEqual(raised.exception.code, code)

    def assert_unconsumed(self, invite_id, challenge_id):
        invite = self.connection.execute(
            "SELECT used_at, used_by_user_id FROM registration_invites WHERE id = ?",
            (invite_id,),
        ).fetchone()
        challenge = self.connection.execute(
            "SELECT consumed_at FROM auth_challenges WHERE challenge_id = ?",
            (challenge_id,),
        ).fetchone()
        self.assertEqual(invite, (None, None))
        self.assertIsNone(challenge[0])


class RegistrationInviteAvailabilityTests(RegistrationServiceFixture):
    def test_active_invite_availability_ignores_revoked_and_expired_rows(self):
        self.assertFalse(self.service.has_active_invites())
        active = self.service.create_invites(count=1, valid_days=7)[0]
        self.assertTrue(self.service.has_active_invites())

        self.service.revoke_invite(active["id"])
        self.assertFalse(self.service.has_active_invites())

        expired = self.service.create_invites(count=1, valid_days=1)[0]
        self.connection.execute(
            "UPDATE registration_invites SET expires_at = ? WHERE id = ?",
            (self.now - 1, expired["id"]),
        )
        self.connection.commit()
        self.assertFalse(self.service.has_active_invites())


class RegistrationTransactionTests(RegistrationServiceFixture):
    def test_invalid_registration_credentials_do_not_run_bcrypt(self):
        invite, challenge, _secret = self.issue_registration_challenge(
            "no-hash@example.com"
        )

        with patch.object(
            registration,
            "hash_user_password",
            side_effect=AssertionError("bcrypt must not run"),
        ) as password_hasher:
            self.assert_error_code(
                "INVITE_INVALID",
                lambda: self.service.register_user(
                    username="no-hash-user",
                    email="no-hash@example.com",
                    password="Strong-pass-2026!",
                    invite_code="REG-NOT-A-REAL-INVITE",
                    challenge_id=challenge["challenge_id"],
                    verification_code="482615",
                    terms_version="v1",
                ),
            )
            self.assert_error_code(
                "CHALLENGE_SECRET_INVALID",
                lambda: self.service.register_user(
                    username="no-hash-user",
                    email="no-hash@example.com",
                    password="Strong-pass-2026!",
                    invite_code=invite["code"],
                    challenge_id=challenge["challenge_id"],
                    verification_code="000000",
                    terms_version="v1",
                ),
            )

        password_hasher.assert_not_called()

    def test_registration_commits_user_invite_and_challenge_together(self):
        self.assertTrue(
            hasattr(self.service, "register_user"),
            "RegistrationService must implement registration transaction",
        )
        email = " New.User@Example.COM "
        invite, challenge, secret = self.issue_registration_challenge(email)

        user = self.register(
            username="Ａlice_用户",
            email=email,
            invite=invite,
            challenge=challenge,
            secret=secret,
        )

        self.assertEqual(user["username"], "Alice_用户")
        self.assertEqual(user["username_normalized"], "alice_用户")
        self.assertEqual(user["email"], "new.user@example.com")
        self.assertEqual(user["email_normalized"], "new.user@example.com")
        self.assertTrue(user["is_active"])
        self.assertEqual(user["terms_version"], "v1")
        self.assertEqual(user["terms_accepted_at"], self.now)
        self.assertFalse(any("password" in key or "hash" in key for key in user))
        stored = self.connection.execute(
            "SELECT password_hash, password_hash_v2, password_hash_version "
            "FROM users WHERE id = ?",
            (user["id"],),
        ).fetchone()
        self.assertEqual(stored[0], "")
        self.assertTrue(verify_user_password_hash("Strong-pass-2026!", stored[1]))
        self.assertEqual(stored[2], 2)
        used_at, used_by = self.connection.execute(
            "SELECT used_at, used_by_user_id FROM registration_invites WHERE id = ?",
            (invite["id"],),
        ).fetchone()
        consumed_at = self.connection.execute(
            "SELECT consumed_at FROM auth_challenges WHERE challenge_id = ?",
            (challenge["challenge_id"],),
        ).fetchone()[0]
        self.assertEqual((used_at, used_by, consumed_at), (self.now, user["id"], self.now))

    def test_identity_conflicts_and_terms_change_roll_back_all_objects(self):
        first_invite, first_challenge, first_secret = self.issue_registration_challenge(
            "first@example.com"
        )
        self.register(
            username="Straße",
            email="first@example.com",
            invite=first_invite,
            challenge=first_challenge,
            secret=first_secret,
        )

        username_invite, username_challenge, username_secret = (
            self.issue_registration_challenge("second@example.com")
        )
        self.assert_error_code(
            "USERNAME_TAKEN",
            lambda: self.register(
                username="STRASSE",
                email="second@example.com",
                invite=username_invite,
                challenge=username_challenge,
                secret=username_secret,
            ),
        )
        self.assert_unconsumed(username_invite["id"], username_challenge["challenge_id"])

        email_invite, email_challenge, email_secret = self.issue_registration_challenge(
            "FIRST@EXAMPLE.COM"
        )
        self.assert_error_code(
            "EMAIL_TAKEN",
            lambda: self.register(
                username="different-user",
                email="FIRST@EXAMPLE.COM",
                invite=email_invite,
                challenge=email_challenge,
                secret=email_secret,
            ),
        )
        self.assert_unconsumed(email_invite["id"], email_challenge["challenge_id"])

        terms_invite, terms_challenge, terms_secret = self.issue_registration_challenge(
            "terms@example.com"
        )
        self.connection.execute(
            "UPDATE system_settings SET value = 'v2' WHERE key = 'terms_version'"
        )
        self.connection.commit()
        self.assert_error_code(
            "TERMS_VERSION_MISMATCH",
            lambda: self.register(
                username="terms-user",
                email="terms@example.com",
                invite=terms_invite,
                challenge=terms_challenge,
                secret=terms_secret,
                terms="v1",
            ),
        )
        self.assert_unconsumed(terms_invite["id"], terms_challenge["challenge_id"])
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM users WHERE username_normalized = 'terms-user'"
            ).fetchone()[0],
            0,
        )

    def test_wrong_registration_code_only_persists_attempt_audit(self):
        invite, challenge, _secret = self.issue_registration_challenge(
            "wrong-code@example.com"
        )
        self.assert_error_code(
            "CHALLENGE_SECRET_INVALID",
            lambda: self.register(
                username="wrong-code-user",
                email="wrong-code@example.com",
                invite=invite,
                challenge=challenge,
                secret="000000",
            ),
        )

        self.assert_unconsumed(invite["id"], challenge["challenge_id"])
        attempts = self.connection.execute(
            "SELECT attempt_count FROM auth_challenges WHERE challenge_id = ?",
            (challenge["challenge_id"],),
        ).fetchone()[0]
        self.assertEqual(attempts, 1)
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM users WHERE username_normalized = 'wrong-code-user'"
            ).fetchone()[0],
            0,
        )

    def test_expired_revoked_and_reused_invites_are_rejected(self):
        expired, expired_challenge, expired_secret = self.issue_registration_challenge(
            "expired@example.com"
        )
        self.now = expired["expires_at"] + 1
        self.assert_error_code(
            "INVITE_EXPIRED",
            lambda: self.register(
                username="expired-user",
                email="expired@example.com",
                invite=expired,
                challenge=expired_challenge,
                secret=expired_secret,
            ),
        )

        self.now = 1_800_200_000.0
        revoked, revoked_challenge, revoked_secret = self.issue_registration_challenge(
            "revoked@example.com"
        )
        self.service.revoke_invite(revoked["id"])
        self.assert_error_code(
            "INVITE_REVOKED",
            lambda: self.register(
                username="revoked-user",
                email="revoked@example.com",
                invite=revoked,
                challenge=revoked_challenge,
                secret=revoked_secret,
            ),
        )

        shared = self.service.create_invites()[0]
        one = self.issue_registration_challenge("one@example.com", invite=shared)
        two = self.issue_registration_challenge("two@example.com", invite=shared)
        self.register(
            username="invite-user-one",
            email="one@example.com",
            invite=one[0],
            challenge=one[1],
            secret=one[2],
        )
        self.assert_error_code(
            "INVITE_ALREADY_USED",
            lambda: self.register(
                username="invite-user-two",
                email="two@example.com",
                invite=two[0],
                challenge=two[1],
                secret=two[2],
            ),
        )
        self.assertIsNone(
            self.connection.execute(
                "SELECT consumed_at FROM auth_challenges WHERE challenge_id = ?",
                (two[1]["challenge_id"],),
            ).fetchone()[0]
        )

    def test_concurrent_registration_with_one_invite_has_one_winner(self):
        shared = self.service.create_invites()[0]
        first = self.issue_registration_challenge("race-one@example.com", invite=shared)
        second = self.issue_registration_challenge("race-two@example.com", invite=shared)
        second_connection = sqlite3.connect(
            self.db_path, check_same_thread=False, timeout=10
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
                    invite_code=shared["code"],
                    challenge_id=challenge["challenge_id"],
                    verification_code=secret,
                    terms_version="v1",
                )
                return ("ok", user["id"])
            except RegistrationError as exc:
                return ("error", exc.code)

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
                                first[1],
                                first[2],
                            ),
                            (
                                second_service,
                                "race-user-two",
                                "race-two@example.com",
                                second[1],
                                second[2],
                            ),
                        ),
                    )
                )
        finally:
            second_connection.close()

        self.assertEqual([result[0] for result in results].count("ok"), 1)
        self.assertEqual([result[0] for result in results].count("error"), 1)
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM users WHERE username_normalized LIKE 'race-user-%'"
            ).fetchone()[0],
            1,
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
        self.assertEqual(users.get_by_id(user["id"])["terms_version"], "v1")
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
