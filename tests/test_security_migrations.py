import hashlib
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

from loguru import logger

from db_manager import DBManager
from schema_migrations import Migration, MigrationRunner
from security_utils import (
    SYSTEM_SECRET_PREFIX,
    AccountCredentialCipher,
    SystemSecretCipher,
    token_digest,
)


def create_legacy_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE cookies (
            id TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            password TEXT DEFAULT ''
        );
        CREATE TABLE auth_sessions (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            username TEXT NOT NULL,
            is_admin INTEGER DEFAULT 0,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            last_seen_at REAL
        );
        CREATE TABLE system_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            description TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    connection.execute(
        "INSERT INTO users (username, email, password_hash) VALUES (?, ?, ?)",
        ("legacy", "legacy@example.com", hashlib.sha256(b"legacy-pass").hexdigest()),
    )
    connection.execute(
        "INSERT INTO cookies (id, value, user_id, password) VALUES (?, ?, ?, ?)",
        ("account-1", "unb=account-1", 1, "xianyu-secret"),
    )
    connection.execute(
        "INSERT INTO auth_sessions VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("legacy-token", 1, "legacy", 0, 1.0, 9_999_999_999.0, 1.0),
    )
    connection.executemany(
        "INSERT INTO system_settings (key, value) VALUES (?, ?)",
        (("registration_enabled", "true"), ("smtp_password", "")),
    )
    connection.commit()
    connection.close()


class SchemaMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.db_path = self.root / "legacy.db"
        self.key_path = self.root / ".account-key"
        self.previous_key_file = os.environ.get("ACCOUNT_CREDENTIAL_KEY_FILE")
        self.previous_system_key_file = os.environ.get("SYSTEM_SECRET_KEY_FILE")
        self.previous_system_key = os.environ.get("SYSTEM_SECRET_ENCRYPTION_KEY")
        os.environ["ACCOUNT_CREDENTIAL_KEY_FILE"] = str(self.key_path)
        os.environ["SYSTEM_SECRET_KEY_FILE"] = str(self.root / ".system-key")
        os.environ.pop("SYSTEM_SECRET_ENCRYPTION_KEY", None)
        create_legacy_database(self.db_path)

    def tearDown(self):
        if self.previous_key_file is None:
            os.environ.pop("ACCOUNT_CREDENTIAL_KEY_FILE", None)
        else:
            os.environ["ACCOUNT_CREDENTIAL_KEY_FILE"] = self.previous_key_file
        if self.previous_system_key_file is None:
            os.environ.pop("SYSTEM_SECRET_KEY_FILE", None)
        else:
            os.environ["SYSTEM_SECRET_KEY_FILE"] = self.previous_system_key_file
        if self.previous_system_key is None:
            os.environ.pop("SYSTEM_SECRET_ENCRYPTION_KEY", None)
        else:
            os.environ["SYSTEM_SECRET_ENCRYPTION_KEY"] = self.previous_system_key
        self.tempdir.cleanup()

    def test_migration_is_backed_up_idempotent_and_removes_plaintext_credentials(self):
        connection = sqlite3.connect(self.db_path)
        runner = MigrationRunner(connection, str(self.db_path))
        self.assertEqual(
            runner.run(),
            ["2026070501", "2026070502", "2026071101", "2026071102"],
        )
        self.assertIsNotNone(runner.last_backup_dir)
        self.assertTrue((runner.last_backup_dir / self.db_path.name).exists())
        self.assertTrue((runner.last_backup_dir / self.key_path.name).exists())
        self.assertEqual(runner.run(), [])
        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0],
            4,
        )

        password, encrypted, version = connection.execute(
            "SELECT password, password_encrypted, password_encryption_version FROM cookies"
        ).fetchone()
        self.assertEqual(password, "")
        self.assertNotIn("xianyu-secret", encrypted)
        self.assertEqual(AccountCredentialCipher(str(self.db_path)).decrypt(encrypted), "xianyu-secret")
        self.assertEqual(version, 1)

        stored_token, stored_digest = connection.execute(
            "SELECT token, token_digest FROM auth_sessions"
        ).fetchone()
        self.assertEqual(stored_token, "legacy-token")
        self.assertEqual(stored_digest, token_digest("legacy-token"))
        connection.close()

    def test_failed_migration_rolls_back_all_schema_changes(self):
        connection = sqlite3.connect(self.db_path)

        def fail(cursor, _db_path):
            cursor.execute("CREATE TABLE should_rollback (id INTEGER)")
            raise RuntimeError("planned failure")

        runner = MigrationRunner(
            connection,
            str(self.db_path),
            migrations=[Migration("broken", "broken", fail)],
        )
        with self.assertRaisesRegex(RuntimeError, "planned failure"):
            runner.run()
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        self.assertNotIn("should_rollback", tables)
        self.assertNotIn("schema_migrations", tables)
        connection.close()


class CredentialCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.db_path = self.root / "manager.db"
        self.previous_key_file = os.environ.get("ACCOUNT_CREDENTIAL_KEY_FILE")
        self.previous_system_key_file = os.environ.get("SYSTEM_SECRET_KEY_FILE")
        self.previous_system_key = os.environ.get("SYSTEM_SECRET_ENCRYPTION_KEY")
        os.environ["ACCOUNT_CREDENTIAL_KEY_FILE"] = str(self.root / ".account-key")
        os.environ["SYSTEM_SECRET_KEY_FILE"] = str(self.root / ".system-key")
        os.environ.pop("SYSTEM_SECRET_ENCRYPTION_KEY", None)
        self.db = DBManager(str(self.db_path))

    def tearDown(self):
        self.db.close()
        if self.previous_key_file is None:
            os.environ.pop("ACCOUNT_CREDENTIAL_KEY_FILE", None)
        else:
            os.environ["ACCOUNT_CREDENTIAL_KEY_FILE"] = self.previous_key_file
        if self.previous_system_key_file is None:
            os.environ.pop("SYSTEM_SECRET_KEY_FILE", None)
        else:
            os.environ["SYSTEM_SECRET_KEY_FILE"] = self.previous_system_key_file
        if self.previous_system_key is None:
            os.environ.pop("SYSTEM_SECRET_ENCRYPTION_KEY", None)
        else:
            os.environ["SYSTEM_SECRET_ENCRYPTION_KEY"] = self.previous_system_key
        self.tempdir.cleanup()

    def import_system_settings(self, settings: list[tuple[str, str]]) -> bool:
        rows = [
            [key, value, f"Imported {key}", None]
            for key, value in settings
        ]
        return self.db.import_backup(
            {
                "version": "synthetic-test",
                "data": {
                    "system_settings": {
                        "columns": ["key", "value", "description", "updated_at"],
                        "rows": rows,
                    }
                },
            }
        )

    def test_legacy_user_login_upgrades_to_bcrypt(self):
        legacy_hash = hashlib.sha256(b"legacy-pass").hexdigest()
        with self.db.lock:
            self.db.conn.execute(
                "INSERT INTO users (username, email, password_hash) VALUES (?, ?, ?)",
                ("legacy-user", "legacy-user@example.com", legacy_hash),
            )
            self.db.conn.commit()

        self.assertTrue(self.db.verify_user_password("legacy-user", "legacy-pass"))
        legacy, upgraded, version = self.db.conn.execute(
            "SELECT password_hash, password_hash_v2, password_hash_version "
            "FROM users WHERE username = 'legacy-user'"
        ).fetchone()
        self.assertEqual(legacy, "")
        self.assertTrue(upgraded.startswith("$2"))
        self.assertEqual(version, 2)

    def test_new_password_cookie_password_and_session_token_are_not_plaintext(self):
        self.assertTrue(self.db.create_user("secure-user", "secure@example.com", "admin-secret"))
        legacy_hash, bcrypt_hash = self.db.conn.execute(
            "SELECT password_hash, password_hash_v2 FROM users WHERE username = 'secure-user'"
        ).fetchone()
        self.assertEqual(legacy_hash, "")
        self.assertNotIn("admin-secret", bcrypt_hash)

        self.assertTrue(
            self.db.update_cookie_account_info(
                "account-1",
                cookie_value="unb=account-1",
                password="xianyu-secret",
                user_id=1,
            )
        )
        legacy_password, encrypted = self.db.conn.execute(
            "SELECT password, password_encrypted FROM cookies WHERE id = 'account-1'"
        ).fetchone()
        self.assertEqual(legacy_password, "")
        self.assertNotIn("xianyu-secret", encrypted)
        self.assertEqual(self.db.get_cookie_details("account-1")["password"], "xianyu-secret")

        self.assertTrue(self.db.save_auth_session("session-secret", 1, "admin", True, 9_999_999_999.0))
        stored_token, stored_digest = self.db.conn.execute(
            "SELECT token, token_digest FROM auth_sessions WHERE token_digest = ?",
            (token_digest("session-secret"),),
        ).fetchone()
        self.assertNotEqual(stored_token, "session-secret")
        self.assertEqual(stored_digest, token_digest("session-secret"))
        self.assertIsNotNone(self.db.get_auth_session("session-secret"))

    def test_smtp_password_is_encrypted_at_rest_and_decrypted_for_internal_reads(self):
        def stored_password() -> str:
            return self.db.conn.execute(
                "SELECT value FROM system_settings WHERE key = 'smtp_password'"
            ).fetchone()[0]

        self.assertTrue(
            self.db.set_system_setting(
                "smtp_password",
                "synthetic-smtp-secret",
                "SMTP credential",
            )
        )
        first_ciphertext = stored_password()
        self.assertTrue(first_ciphertext.startswith(SYSTEM_SECRET_PREFIX))
        self.assertNotIn("synthetic-smtp-secret", first_ciphertext)
        self.assertEqual(
            self.db.get_system_setting("smtp_password"),
            "synthetic-smtp-secret",
        )
        self.assertEqual(
            self.db.get_all_system_settings()["smtp_password"],
            "synthetic-smtp-secret",
        )

        self.assertTrue(
            self.db.save_system_settings_section(
                {
                    "smtp_server": "smtp.example.test",
                    "smtp_password": self.db.get_system_setting("smtp_password"),
                }
            )
        )
        self.assertEqual(stored_password(), first_ciphertext)

        self.assertTrue(
            self.db.set_system_setting("smtp_password", first_ciphertext)
        )
        self.assertEqual(stored_password(), first_ciphertext)
        self.assertEqual(
            self.db.get_system_setting("smtp_password"),
            "synthetic-smtp-secret",
        )

        self.assertTrue(
            self.db.save_system_settings_section(
                {"smtp_password": "replacement-smtp-secret"}
            )
        )
        replacement_ciphertext = stored_password()
        self.assertTrue(replacement_ciphertext.startswith(SYSTEM_SECRET_PREFIX))
        self.assertNotEqual(replacement_ciphertext, first_ciphertext)
        self.assertEqual(
            self.db.get_all_system_settings()["smtp_password"],
            "replacement-smtp-secret",
        )

    def test_create_user_persists_normalized_identity_and_rejects_casefold_conflicts(self):
        self.assertTrue(
            self.db.create_user(
                "Alice",
                " Mixed.Email@Example.COM ",
                "synthetic-password",
            )
        )
        identity = self.db.conn.execute(
            "SELECT username, email, username_normalized, email_normalized "
            "FROM users WHERE username = 'Alice'"
        ).fetchone()
        self.assertEqual(
            identity,
            (
                "Alice",
                "mixed.email@example.com",
                "alice",
                "mixed.email@example.com",
            ),
        )
        self.assertFalse(
            self.db.create_user(
                "alice",
                "different@example.com",
                "synthetic-password",
            )
        )
        self.assertFalse(
            self.db.create_user(
                "different-user",
                "MIXED.EMAIL@example.com",
                "synthetic-password",
            )
        )

        self.assertTrue(
            self.db.create_user(
                "Straße",
                "street-one@example.com",
                "synthetic-password",
            )
        )
        self.assertFalse(
            self.db.create_user(
                "STRASSE",
                "street-two@example.com",
                "synthetic-password",
            )
        )
        self.assertEqual(
            self.db.conn.execute(
                "SELECT COUNT(*) FROM users "
                "WHERE username_normalized IS NULL OR email_normalized IS NULL"
            ).fetchone()[0],
            0,
        )

    def test_registration_security_tables_redact_sql_parameters(self):
        messages = []
        sink_id = logger.add(messages.append, format="{message}", level="DEBUG")
        try:
            for table in (
                "auth_challenges",
                "registration_invites",
                "auth_rate_events",
            ):
                marker = f"synthetic-{table}-digest"
                self.db._log_sql(
                    f"INSERT INTO {table} (secret_digest) VALUES (?)",
                    (marker,),
                )
        finally:
            logger.remove(sink_id)

        output = "".join(str(message) for message in messages)
        self.assertNotIn("synthetic-auth_challenges-digest", output)
        self.assertNotIn("synthetic-registration_invites-digest", output)
        self.assertNotIn("synthetic-auth_rate_events-digest", output)
        self.assertGreaterEqual(output.count("[REDACTED]"), 3)

    def test_imported_smtp_password_is_normalized_without_plaintext_at_rest(self):
        cipher = SystemSecretCipher(str(self.db_path))
        current_ciphertext = cipher.encrypt("current-instance-smtp-secret")
        cases = (
            ("empty", "", "", ""),
            (
                "current ciphertext",
                current_ciphertext,
                current_ciphertext,
                "current-instance-smtp-secret",
            ),
            (
                "legacy plaintext",
                "legacy-smtp-secret",
                None,
                "legacy-smtp-secret",
            ),
            (
                "legacy prefix plaintext",
                "fernet:v0:legacy-smtp-secret",
                None,
                "fernet:v0:legacy-smtp-secret",
            ),
            (
                "current prefix plaintext",
                SYSTEM_SECRET_PREFIX + "legacy-smtp-secret",
                None,
                SYSTEM_SECRET_PREFIX + "legacy-smtp-secret",
            ),
        )

        for label, imported_value, exact_storage, expected_plaintext in cases:
            with self.subTest(label=label):
                self.assertTrue(
                    self.import_system_settings(
                        [
                            ("smtp_server", "smtp.example.test"),
                            ("smtp_password", imported_value),
                            ("smtp_verified_fingerprint", "stale-fingerprint"),
                            ("smtp_verified_at", "2000-01-01T00:00:00"),
                        ]
                    )
                )
                stored = dict(
                    self.db.conn.execute(
                        "SELECT key, value FROM system_settings"
                    ).fetchall()
                )
                if exact_storage is None:
                    self.assertTrue(
                        stored["smtp_password"].startswith(SYSTEM_SECRET_PREFIX)
                    )
                    self.assertNotEqual(stored["smtp_password"], imported_value)
                else:
                    self.assertEqual(stored["smtp_password"], exact_storage)
                self.assertEqual(
                    self.db.get_system_setting("smtp_password"),
                    expected_plaintext,
                )
                self.assertEqual(stored["smtp_verified_fingerprint"], "")
                self.assertEqual(stored["smtp_verified_at"], "")

    def test_imported_foreign_smtp_ciphertext_is_cleared_and_requires_reconfiguration(self):
        foreign_cipher = SystemSecretCipher(
            str(self.root / "foreign.db"),
            secret="synthetic-foreign-instance-key",
        )
        foreign_plaintext = "foreign-instance-smtp-secret"
        foreign_values = (
            ("current prefix", foreign_cipher.encrypt(foreign_plaintext)),
            (
                "legacy prefix",
                "fernet:v0:"
                + foreign_cipher.fernet.encrypt(
                    foreign_plaintext.encode("utf-8")
                ).decode("ascii"),
            ),
        )
        for label, foreign_ciphertext in foreign_values:
            with self.subTest(label=label):
                messages = []
                sink_id = logger.add(
                    messages.append,
                    format="{message}",
                    level="WARNING",
                )
                try:
                    imported = self.import_system_settings(
                        [
                            ("smtp_password", foreign_ciphertext),
                            ("smtp_verified_fingerprint", "foreign-fingerprint"),
                            ("smtp_verified_at", "2000-01-01T00:00:00"),
                        ]
                    )
                finally:
                    logger.remove(sink_id)

                self.assertTrue(imported)
                stored = dict(
                    self.db.conn.execute(
                        "SELECT key, value FROM system_settings"
                    ).fetchall()
                )
                self.assertEqual(stored["smtp_password"], "")
                self.assertEqual(self.db.get_system_setting("smtp_password"), "")
                self.assertEqual(stored["smtp_verified_fingerprint"], "")
                self.assertEqual(stored["smtp_verified_at"], "")
                output = "".join(str(message) for message in messages)
                self.assertIn("重新配置", output)
                self.assertNotIn(foreign_plaintext, output)
                self.assertNotIn(foreign_ciphertext, output)


if __name__ == "__main__":
    unittest.main()
