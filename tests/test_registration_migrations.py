from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import threading
import unittest
from unittest import mock

from schema_migrations import MigrationRunner
import security_utils


OLD_MIGRATIONS = (
    ("2026070501", "security_credentials_v1"),
    ("2026070502", "runtime_sessions_v1"),
)


def create_v150_database(
    path: Path,
    *,
    users: list[tuple[str, str]] | None = None,
) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            password_hash_v2 TEXT,
            password_hash_version INTEGER NOT NULL DEFAULT 1,
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE system_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            description TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE schema_migrations (
            version TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    connection.executemany(
        "INSERT INTO users (username, email, password_hash) VALUES (?, ?, 'legacy-hash')",
        users or [("StraßeAdmin", " Mixed.Case@Example.COM ")],
    )
    connection.executemany(
        "INSERT INTO system_settings (key, value, description) VALUES (?, ?, ?)",
        (
            ("registration_enabled", "true", "legacy registration setting"),
            ("smtp_password", "synthetic-smtp-password", "legacy SMTP password"),
            ("smtp_server", "smtp.example.test", "custom SMTP server"),
            ("theme_color", "green", "custom theme"),
        ),
    )
    connection.executemany(
        "INSERT INTO schema_migrations (version, name) VALUES (?, ?)",
        OLD_MIGRATIONS,
    )
    connection.commit()
    connection.close()


def table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}


def table_indexes(
    connection: sqlite3.Connection,
    table: str,
) -> dict[str, tuple[bool, tuple[str, ...]]]:
    indexes: dict[str, tuple[bool, tuple[str, ...]]] = {}
    for row in connection.execute(f"PRAGMA index_list({table})"):
        name = str(row[1])
        columns = tuple(
            str(info[2])
            for info in connection.execute(f'PRAGMA index_info("{name}")')
        )
        indexes[name] = (bool(row[2]), columns)
    return indexes


class RegistrationMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.db_path = self.root / "xianyu_data.db"
        self.account_key_path = self.root / ".account_credential_key"
        self.ai_key_path = self.root / ".ai_provider_key"
        self.system_key_path = self.root / ".system_secret_key"
        self.environment_keys = (
            "ACCOUNT_CREDENTIAL_KEY_FILE",
            "ACCOUNT_CREDENTIAL_ENCRYPTION_KEY",
            "AI_PROVIDER_KEY_FILE",
            "SYSTEM_SECRET_KEY_FILE",
            "SYSTEM_SECRET_ENCRYPTION_KEY",
        )
        self.previous_environment = {
            key: os.environ.get(key) for key in self.environment_keys
        }
        os.environ["ACCOUNT_CREDENTIAL_KEY_FILE"] = str(self.account_key_path)
        os.environ["AI_PROVIDER_KEY_FILE"] = str(self.ai_key_path)
        os.environ["SYSTEM_SECRET_KEY_FILE"] = str(self.system_key_path)
        os.environ.pop("ACCOUNT_CREDENTIAL_ENCRYPTION_KEY", None)
        os.environ.pop("SYSTEM_SECRET_ENCRYPTION_KEY", None)
        self.ai_key_path.write_text("synthetic-ai-provider-key", encoding="ascii")
        create_v150_database(self.db_path)

    def tearDown(self):
        for key, previous in self.previous_environment.items():
            if previous is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous
        self.tempdir.cleanup()

    def test_system_secret_cipher_is_idempotent_and_purpose_isolated(self):
        self.assertTrue(
            hasattr(security_utils, "SystemSecretCipher"),
            "SystemSecretCipher must be implemented",
        )
        cipher = security_utils.SystemSecretCipher(str(self.db_path))

        self.assertEqual(stat.S_IMODE(self.system_key_path.stat().st_mode), 0o600)
        encrypted = cipher.encrypt("synthetic-secret")
        self.assertNotIn("synthetic-secret", encrypted)
        self.assertTrue(encrypted.startswith(security_utils.SYSTEM_SECRET_PREFIX))
        self.assertEqual(cipher.encrypt(encrypted), encrypted)
        self.assertEqual(cipher.decrypt(encrypted), "synthetic-secret")
        self.assertEqual(cipher.decrypt("legacy-plaintext"), "legacy-plaintext")

        invite_digest = cipher.digest("shared-value", purpose="invite-code")
        verification_digest = cipher.digest("shared-value", purpose="verification-code")
        self.assertNotEqual(invite_digest, verification_digest)
        self.assertTrue(
            cipher.compare_digest(
                "shared-value",
                invite_digest,
                purpose="invite-code",
            )
        )
        self.assertFalse(
            cipher.compare_digest(
                "wrong-value",
                invite_digest,
                purpose="invite-code",
            )
        )
        self.assertFalse(
            cipher.compare_digest(
                "shared-value",
                invite_digest,
                purpose="verification-code",
            )
        )

    def test_system_secret_cipher_encrypts_plaintext_with_ciphertext_prefix(self):
        cipher = security_utils.SystemSecretCipher(str(self.db_path))
        plaintext = security_utils.SYSTEM_SECRET_PREFIX + "not-a-valid-fernet-token"

        encrypted = cipher.encrypt(plaintext)

        self.assertNotEqual(encrypted, plaintext)
        self.assertTrue(encrypted.startswith(security_utils.SYSTEM_SECRET_PREFIX))
        self.assertEqual(cipher.decrypt(encrypted), plaintext)

    def test_system_secret_key_creation_is_atomic_across_threads(self):
        real_open = os.open
        first_opened = threading.Event()
        release_first = threading.Event()
        interception_lock = threading.Lock()
        intercepted = False

        def delay_first_exclusive_open(path, flags, mode=0o777, *, dir_fd=None):
            nonlocal intercepted
            if dir_fd is None:
                descriptor = real_open(path, flags, mode)
            else:
                descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
            should_wait = False
            if flags & os.O_EXCL and Path(path).parent == self.root:
                with interception_lock:
                    if not intercepted:
                        intercepted = True
                        should_wait = True
            if should_wait:
                first_opened.set()
                release_first.wait(timeout=5)
            return descriptor

        def create_cipher():
            try:
                return security_utils.SystemSecretCipher(str(self.db_path))
            except Exception as exc:  # Returned so the assertion reports both threads.
                return exc

        with mock.patch.object(security_utils.os, "open", delay_first_exclusive_open):
            with ThreadPoolExecutor(max_workers=2) as executor:
                first_future = executor.submit(create_cipher)
                self.assertTrue(first_opened.wait(timeout=2))
                second_future = executor.submit(create_cipher)
                try:
                    second_result = second_future.result(timeout=2)
                finally:
                    release_first.set()
                first_result = first_future.result(timeout=2)

        results = (first_result, second_result)
        self.assertTrue(
            all(isinstance(result, security_utils.SystemSecretCipher) for result in results),
            [type(result).__name__ for result in results],
        )
        first_cipher, second_cipher = results
        first_token = first_cipher.encrypt("first-thread-secret")
        second_token = second_cipher.encrypt("second-thread-secret")
        self.assertEqual(second_cipher.decrypt(first_token), "first-thread-secret")
        self.assertEqual(first_cipher.decrypt(second_token), "second-thread-secret")
        self.assertTrue(self.system_key_path.read_text(encoding="ascii"))
        self.assertEqual(stat.S_IMODE(self.system_key_path.stat().st_mode), 0o600)
        self.assertEqual(
            list(self.root.glob(f"{self.system_key_path.name}.tmp-*")),
            [],
        )

    def test_v150_upgrade_adds_registration_schema_defaults_and_secure_backup(self):
        connection = sqlite3.connect(self.db_path)
        runner = MigrationRunner(connection, str(self.db_path))

        self.assertEqual(runner.run(), ["2026071101"])
        self.assertEqual(
            table_columns(connection, "users")
            & {
                "username_normalized",
                "email_normalized",
                "terms_version",
                "terms_accepted_at",
            },
            {
                "username_normalized",
                "email_normalized",
                "terms_version",
                "terms_accepted_at",
            },
        )
        self.assertEqual(
            connection.execute(
                "SELECT username_normalized, email_normalized FROM users"
            ).fetchone(),
            ("strasseadmin", "mixed.case@example.com"),
        )

        user_indexes = table_indexes(connection, "users")
        self.assertEqual(
            user_indexes["idx_users_username_normalized"],
            (True, ("username_normalized",)),
        )
        self.assertEqual(
            user_indexes["idx_users_email_normalized"],
            (True, ("email_normalized",)),
        )

        expected_columns = {
            "registration_invites": {
                "code_digest",
                "code_hint",
                "note",
                "expires_at",
                "used_at",
                "used_by_user_id",
                "revoked_at",
                "created_by_user_id",
                "created_at",
            },
            "auth_challenges": {
                "challenge_id",
                "purpose",
                "subject_digest",
                "context_digest",
                "secret_digest",
                "attempt_count",
                "max_attempts",
                "expires_at",
                "consumed_at",
                "created_at",
            },
            "auth_rate_events": {
                "event_type",
                "ip_digest",
                "email_digest",
                "account_digest",
                "success",
                "created_at",
            },
        }
        for table, columns in expected_columns.items():
            with self.subTest(table=table):
                self.assertTrue(columns.issubset(table_columns(connection, table)))

        invite_indexes = table_indexes(connection, "registration_invites")
        self.assertEqual(
            invite_indexes["idx_registration_invites_code_digest"],
            (True, ("code_digest",)),
        )
        self.assertEqual(
            invite_indexes["idx_registration_invites_lookup"][1],
            ("revoked_at", "used_at", "expires_at"),
        )
        challenge_indexes = table_indexes(connection, "auth_challenges")
        self.assertEqual(
            challenge_indexes["idx_auth_challenges_subject"][1],
            ("purpose", "subject_digest", "expires_at"),
        )
        rate_indexes = table_indexes(connection, "auth_rate_events")
        self.assertEqual(
            rate_indexes["idx_auth_rate_events_ip"][1],
            ("event_type", "ip_digest", "created_at"),
        )
        self.assertEqual(
            rate_indexes["idx_auth_rate_events_email"][1],
            ("event_type", "email_digest", "created_at"),
        )
        self.assertEqual(
            rate_indexes["idx_auth_rate_events_account"][1],
            ("event_type", "account_digest", "created_at"),
        )

        settings = dict(connection.execute("SELECT key, value FROM system_settings"))
        self.assertEqual(settings["registration_enabled"], "false")
        self.assertEqual(settings["terms_version"], "v1")
        self.assertEqual(settings["support_email"], "")
        self.assertEqual(settings["smtp_verified_fingerprint"], "")
        self.assertEqual(settings["smtp_verified_at"], "")
        self.assertEqual(settings["auth_trusted_proxies"], "")
        self.assertEqual(settings["smtp_server"], "smtp.example.test")
        self.assertEqual(settings["theme_color"], "green")

        encrypted_smtp_password = settings["smtp_password"]
        self.assertNotIn("synthetic-smtp-password", encrypted_smtp_password)
        self.assertTrue(
            encrypted_smtp_password.startswith(security_utils.SYSTEM_SECRET_PREFIX)
        )
        cipher = security_utils.SystemSecretCipher(str(self.db_path))
        self.assertEqual(
            cipher.decrypt(encrypted_smtp_password),
            "synthetic-smtp-password",
        )

        self.assertIsNotNone(runner.last_backup_dir)
        backup_dir = runner.last_backup_dir
        self.assertTrue((backup_dir / self.db_path.name).is_file())
        for key_path in (
            self.account_key_path,
            self.ai_key_path,
            self.system_key_path,
        ):
            with self.subTest(key=key_path.name):
                backup_key = backup_dir / key_path.name
                self.assertTrue(backup_key.is_file())
                self.assertEqual(backup_key.read_bytes(), key_path.read_bytes())
                self.assertEqual(stat.S_IMODE(backup_key.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.system_key_path.stat().st_mode), 0o600)

        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO users "
                "(username, username_normalized, email, email_normalized, password_hash) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    "another-user",
                    "strasseadmin",
                    "another@example.com",
                    "another@example.com",
                    "hash",
                ),
            )
        connection.rollback()

        before_second_run = "\n".join(connection.iterdump())
        backup_count = len(list((self.root / "backups").iterdir()))
        self.assertEqual(runner.run(), [])
        self.assertEqual("\n".join(connection.iterdump()), before_second_run)
        self.assertEqual(len(list((self.root / "backups").iterdir())), backup_count)
        connection.close()


class RegistrationConflictTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.db_path = self.root / "conflict.db"
        self.environment_keys = (
            "ACCOUNT_CREDENTIAL_KEY_FILE",
            "ACCOUNT_CREDENTIAL_ENCRYPTION_KEY",
            "SYSTEM_SECRET_KEY_FILE",
            "SYSTEM_SECRET_ENCRYPTION_KEY",
        )
        self.previous_environment = {
            key: os.environ.get(key) for key in self.environment_keys
        }
        os.environ["ACCOUNT_CREDENTIAL_KEY_FILE"] = str(
            self.root / ".account_credential_key"
        )
        os.environ["SYSTEM_SECRET_KEY_FILE"] = str(self.root / ".system_secret_key")
        os.environ.pop("ACCOUNT_CREDENTIAL_ENCRYPTION_KEY", None)
        os.environ.pop("SYSTEM_SECRET_ENCRYPTION_KEY", None)

    def tearDown(self):
        for key, previous in self.previous_environment.items():
            if previous is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous
        self.tempdir.cleanup()

    def assert_conflict_rolls_back(
        self,
        users: list[tuple[str, str]],
        expected_field: str,
        forbidden_values: tuple[str, ...] = (),
    ) -> None:
        create_v150_database(self.db_path, users=users)
        connection = sqlite3.connect(self.db_path)
        runner = MigrationRunner(connection, str(self.db_path))

        with self.assertRaises(ValueError) as raised:
            runner.run()
        message = str(raised.exception)
        self.assertIn(expected_field, message.lower())
        for value in forbidden_values:
            self.assertNotIn(value, message)

        self.assertFalse(
            {
                "username_normalized",
                "email_normalized",
                "terms_version",
                "terms_accepted_at",
            }
            & table_columns(connection, "users")
        )
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        self.assertFalse(
            {"registration_invites", "auth_challenges", "auth_rate_events"} & tables
        )
        self.assertEqual(
            connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall(),
            [(version,) for version, _name in OLD_MIGRATIONS],
        )
        self.assertEqual(
            connection.execute(
                "SELECT value FROM system_settings WHERE key = 'registration_enabled'"
            ).fetchone()[0],
            "true",
        )
        self.assertEqual(
            connection.execute(
                "SELECT value FROM system_settings WHERE key = 'smtp_password'"
            ).fetchone()[0],
            "synthetic-smtp-password",
        )
        connection.close()

    def test_ascii_casefold_username_conflict_rolls_back(self):
        self.assert_conflict_rolls_back(
            [("Alice", "alice-one@example.com"), ("alice", "alice-two@example.com")],
            "username",
        )

    def test_unicode_casefold_username_conflict_rolls_back(self):
        self.assert_conflict_rolls_back(
            [
                ("Straße", "street-one@example.com"),
                ("STRASSE", "street-two@example.com"),
            ],
            "username",
        )

    def test_nfkc_compatibility_username_conflict_rolls_back(self):
        fullwidth_username = "Ａｌｉｃｅ"
        ascii_username = "Alice"
        self.assert_conflict_rolls_back(
            [
                (fullwidth_username, "nfkc-one@example.com"),
                (ascii_username, "nfkc-two@example.com"),
            ],
            "username",
            (fullwidth_username, ascii_username),
        )

    def test_normalized_email_conflict_is_redacted_and_rolls_back(self):
        email_one = "Secret.Address@Example.com"
        email_two = " secret.address@example.COM "
        self.assert_conflict_rolls_back(
            [("first-user", email_one), ("second-user", email_two)],
            "email",
            (email_one, email_two, email_one.casefold()),
        )

    def test_nfkc_compatibility_email_conflict_is_redacted_and_rolls_back(self):
        compatibility_email = "Ｓecret＠Example.com"
        ascii_email = "secret@example.COM"
        self.assert_conflict_rolls_back(
            [
                ("nfkc-email-one", compatibility_email),
                ("nfkc-email-two", ascii_email),
            ],
            "email",
            (
                compatibility_email,
                ascii_email,
                "secret@example.com",
            ),
        )


if __name__ == "__main__":
    unittest.main()
