import hashlib
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

from db_manager import DBManager
from schema_migrations import Migration, MigrationRunner
from security_utils import AccountCredentialCipher, token_digest


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
    connection.commit()
    connection.close()


class SchemaMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.db_path = self.root / "legacy.db"
        self.key_path = self.root / ".account-key"
        self.previous_key_file = os.environ.get("ACCOUNT_CREDENTIAL_KEY_FILE")
        os.environ["ACCOUNT_CREDENTIAL_KEY_FILE"] = str(self.key_path)
        create_legacy_database(self.db_path)

    def tearDown(self):
        if self.previous_key_file is None:
            os.environ.pop("ACCOUNT_CREDENTIAL_KEY_FILE", None)
        else:
            os.environ["ACCOUNT_CREDENTIAL_KEY_FILE"] = self.previous_key_file
        self.tempdir.cleanup()

    def test_migration_is_backed_up_idempotent_and_removes_plaintext_credentials(self):
        connection = sqlite3.connect(self.db_path)
        runner = MigrationRunner(connection, str(self.db_path))
        self.assertEqual(runner.run(), ["2026070501", "2026070502"])
        self.assertIsNotNone(runner.last_backup_dir)
        self.assertTrue((runner.last_backup_dir / self.db_path.name).exists())
        self.assertTrue((runner.last_backup_dir / self.key_path.name).exists())
        self.assertEqual(runner.run(), [])
        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0],
            2,
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
        os.environ["ACCOUNT_CREDENTIAL_KEY_FILE"] = str(self.root / ".account-key")
        self.db = DBManager(str(self.db_path))

    def tearDown(self):
        self.db.close()
        if self.previous_key_file is None:
            os.environ.pop("ACCOUNT_CREDENTIAL_KEY_FILE", None)
        else:
            os.environ["ACCOUNT_CREDENTIAL_KEY_FILE"] = self.previous_key_file
        self.tempdir.cleanup()

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
        self.assertEqual(legacy, legacy_hash)
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


if __name__ == "__main__":
    unittest.main()
