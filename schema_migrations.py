"""Ordered, transactional SQLite schema migrations with preflight backups."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import os
import shutil
import sqlite3
from typing import Callable, Iterable, Optional, Sequence
import unicodedata

from security_utils import (
    ACCOUNT_PASSWORD_ENCRYPTION_VERSION,
    AccountCredentialCipher,
    SystemSecretCipher,
    token_digest,
)


MigrationCallable = Callable[[sqlite3.Cursor, str], None]


@dataclass(frozen=True)
class Migration:
    version: str
    name: str
    apply: MigrationCallable


def _columns(cursor: sqlite3.Cursor, table: str) -> set[str]:
    return {str(row[1]) for row in cursor.execute(f"PRAGMA table_info({table})").fetchall()}


def _add_column(cursor: sqlite3.Cursor, table: str, definition: str) -> None:
    name = definition.split()[0]
    if name not in _columns(cursor, table):
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")


def _security_credentials_v1(cursor: sqlite3.Cursor, db_path: str) -> None:
    _add_column(cursor, "users", "password_hash_v2 TEXT")
    _add_column(cursor, "users", "password_hash_version INTEGER NOT NULL DEFAULT 1")
    _add_column(cursor, "cookies", "password_encrypted TEXT NOT NULL DEFAULT ''")
    _add_column(cursor, "cookies", "password_encryption_version INTEGER NOT NULL DEFAULT 0")
    _add_column(cursor, "auth_sessions", "token_digest TEXT")
    cursor.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_auth_sessions_token_digest "
        "ON auth_sessions(token_digest) WHERE token_digest IS NOT NULL"
    )

    cipher = AccountCredentialCipher(db_path)
    rows = cursor.execute(
        "SELECT id, password, password_encrypted FROM cookies "
        "WHERE COALESCE(password, '') <> '' OR COALESCE(password_encrypted, '') <> ''"
    ).fetchall()
    for cookie_id, legacy_password, encrypted_password in rows:
        encrypted_password = str(encrypted_password or "")
        legacy_password = str(legacy_password or "")
        if not encrypted_password and legacy_password:
            encrypted_password = cipher.encrypt(legacy_password)
        if encrypted_password:
            # Verify before removing the legacy plaintext value.
            plaintext = cipher.decrypt(encrypted_password)
            if legacy_password and plaintext != legacy_password:
                raise ValueError(f"账号 {cookie_id} 的凭据迁移校验失败")
            cursor.execute(
                "UPDATE cookies SET password_encrypted = ?, password_encryption_version = ?, password = '' "
                "WHERE id = ?",
                (encrypted_password, ACCOUNT_PASSWORD_ENCRYPTION_VERSION, cookie_id),
            )

    for legacy_token, existing_digest in cursor.execute(
        "SELECT token, token_digest FROM auth_sessions"
    ).fetchall():
        if not existing_digest and legacy_token:
            cursor.execute(
                "UPDATE auth_sessions SET token_digest = ? WHERE token = ?",
                (token_digest(str(legacy_token)), legacy_token),
            )


def _runtime_sessions_v1(cursor: sqlite3.Cursor, _db_path: str) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS runtime_sessions (
            session_id TEXT PRIMARY KEY,
            session_type TEXT NOT NULL,
            owner_user_id INTEGER,
            account_id TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL,
            error_code TEXT NOT NULL DEFAULT '',
            error_message TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            expires_at REAL NOT NULL
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_runtime_sessions_owner "
        "ON runtime_sessions(owner_user_id, session_type, updated_at)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_runtime_sessions_expiry "
        "ON runtime_sessions(expires_at)"
    )


def _normalized_users(cursor: sqlite3.Cursor) -> list[tuple[int, str, str]]:
    normalized_rows: list[tuple[int, str, str]] = []
    usernames: dict[str, int] = {}
    emails: dict[str, int] = {}
    for user_id, username, email in cursor.execute(
        "SELECT id, username, email FROM users ORDER BY id"
    ).fetchall():
        username_normalized = unicodedata.normalize(
            "NFKC", str(username)
        ).casefold()
        email_normalized = unicodedata.normalize(
            "NFKC", str(email)
        ).strip().casefold()
        if username_normalized in usernames:
            raise ValueError(
                "normalized username conflict between user IDs "
                f"{usernames[username_normalized]} and {user_id}"
            )
        if email_normalized in emails:
            raise ValueError(
                "normalized email conflict between user IDs "
                f"{emails[email_normalized]} and {user_id}"
            )
        usernames[username_normalized] = int(user_id)
        emails[email_normalized] = int(user_id)
        normalized_rows.append(
            (int(user_id), username_normalized, email_normalized)
        )
    return normalized_rows


def _registration_security_v1(cursor: sqlite3.Cursor, db_path: str) -> None:
    normalized_users = _normalized_users(cursor)

    _add_column(cursor, "users", "username_normalized TEXT")
    _add_column(cursor, "users", "email_normalized TEXT")
    _add_column(cursor, "users", "terms_version TEXT")
    _add_column(cursor, "users", "terms_accepted_at REAL")
    for user_id, username_normalized, email_normalized in normalized_users:
        cursor.execute(
            "UPDATE users SET username_normalized = ?, email_normalized = ? WHERE id = ?",
            (username_normalized, email_normalized, user_id),
        )
    cursor.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username_normalized "
        "ON users(username_normalized)"
    )
    cursor.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_normalized "
        "ON users(email_normalized)"
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS registration_invites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code_digest TEXT NOT NULL,
            code_hint TEXT NOT NULL DEFAULT '',
            note TEXT NOT NULL DEFAULT '',
            expires_at REAL NOT NULL,
            used_at REAL,
            used_by_user_id INTEGER,
            revoked_at REAL,
            created_by_user_id INTEGER,
            created_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
            FOREIGN KEY (used_by_user_id) REFERENCES users(id) ON DELETE SET NULL,
            FOREIGN KEY (created_by_user_id) REFERENCES users(id) ON DELETE SET NULL
        )
        """
    )
    cursor.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_registration_invites_code_digest "
        "ON registration_invites(code_digest)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_registration_invites_lookup "
        "ON registration_invites(revoked_at, used_at, expires_at)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_registration_invites_creator "
        "ON registration_invites(created_by_user_id, created_at)"
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS auth_challenges (
            challenge_id TEXT PRIMARY KEY,
            purpose TEXT NOT NULL,
            subject_digest TEXT NOT NULL,
            context_digest TEXT NOT NULL DEFAULT '',
            secret_digest TEXT NOT NULL,
            attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
            max_attempts INTEGER NOT NULL CHECK (max_attempts > 0),
            expires_at REAL NOT NULL,
            consumed_at REAL,
            created_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_auth_challenges_subject "
        "ON auth_challenges(purpose, subject_digest, expires_at)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_auth_challenges_expiry "
        "ON auth_challenges(expires_at, consumed_at)"
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS auth_rate_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            ip_digest TEXT NOT NULL DEFAULT '',
            email_digest TEXT NOT NULL DEFAULT '',
            account_digest TEXT NOT NULL DEFAULT '',
            success INTEGER NOT NULL DEFAULT 0 CHECK (success IN (0, 1)),
            created_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_auth_rate_events_ip "
        "ON auth_rate_events(event_type, ip_digest, created_at)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_auth_rate_events_email "
        "ON auth_rate_events(event_type, email_digest, created_at)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_auth_rate_events_account "
        "ON auth_rate_events(event_type, account_digest, created_at)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_auth_rate_events_created_at "
        "ON auth_rate_events(created_at)"
    )

    cursor.execute(
        "INSERT OR IGNORE INTO system_settings (key, value, description) "
        "VALUES ('registration_enabled', 'false', 'Whether public registration is enabled')"
    )
    cursor.execute(
        "UPDATE system_settings SET value = 'false' WHERE key = 'registration_enabled'"
    )
    cursor.executemany(
        "INSERT OR IGNORE INTO system_settings (key, value, description) VALUES (?, ?, ?)",
        (
            ("terms_version", "v1", "Current registration terms version"),
            ("support_email", "", "Public support email"),
            ("smtp_verified_fingerprint", "", "Fingerprint of verified SMTP settings"),
            ("smtp_verified_at", "", "Time the SMTP settings were verified"),
            ("auth_trusted_proxies", "", "Trusted proxies used for client IP resolution"),
        ),
    )

    smtp_password_row = cursor.execute(
        "SELECT value FROM system_settings WHERE key = 'smtp_password'"
    ).fetchone()
    if smtp_password_row and smtp_password_row[0]:
        cipher = SystemSecretCipher(db_path)
        encrypted_password = cipher.encrypt(str(smtp_password_row[0]))
        if encrypted_password != smtp_password_row[0]:
            cursor.execute(
                "UPDATE system_settings SET value = ? WHERE key = 'smtp_password'",
                (encrypted_password,),
            )


def _registration_identity_nfkc_v2(
    cursor: sqlite3.Cursor,
    _db_path: str,
) -> None:
    normalized_users = _normalized_users(cursor)
    cursor.execute(
        "UPDATE users SET username_normalized = NULL, email_normalized = NULL"
    )
    cursor.executemany(
        "UPDATE users SET username_normalized = ?, email_normalized = ? WHERE id = ?",
        (
            (username_normalized, email_normalized, user_id)
            for user_id, username_normalized, email_normalized in normalized_users
        ),
    )


MIGRATIONS: Sequence[Migration] = (
    Migration("2026070501", "security_credentials_v1", _security_credentials_v1),
    Migration("2026070502", "runtime_sessions_v1", _runtime_sessions_v1),
    Migration("2026071101", "registration_security_v1", _registration_security_v1),
    Migration(
        "2026071102",
        "registration_identity_nfkc_v2",
        _registration_identity_nfkc_v2,
    ),
)


class MigrationRunner:
    def __init__(
        self,
        connection: sqlite3.Connection,
        db_path: str,
        migrations: Optional[Iterable[Migration]] = None,
        backup_enabled: bool = True,
    ):
        self.connection = connection
        self.db_path = str(db_path)
        self.migrations = tuple(migrations or MIGRATIONS)
        self.backup_enabled = backup_enabled
        self.last_backup_dir: Optional[Path] = None

    def _applied_versions(self) -> set[str]:
        exists = self.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        ).fetchone()
        if not exists:
            return set()
        return {
            str(row[0])
            for row in self.connection.execute("SELECT version FROM schema_migrations").fetchall()
        }

    def _create_backup(self) -> Path:
        db_path = Path(self.db_path)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup_dir = db_path.parent / "backups" / f"pre-schema-{stamp}"
        backup_dir.mkdir(parents=True, mode=0o700, exist_ok=False)
        backup_db = backup_dir / db_path.name
        backup_connection = sqlite3.connect(str(backup_db))
        try:
            self.connection.backup(backup_connection)
        finally:
            backup_connection.close()

        candidate_keys = {
            Path(os.getenv("ACCOUNT_CREDENTIAL_KEY_FILE", str(db_path.parent / ".account_credential_key"))),
            Path(os.getenv("AI_PROVIDER_KEY_FILE", str(db_path.parent / ".ai_provider_key"))),
            Path(os.getenv("SYSTEM_SECRET_KEY_FILE", str(db_path.parent / ".system_secret_key"))),
        }
        for key_path in candidate_keys:
            if key_path.exists() and key_path.is_file():
                destination = backup_dir / key_path.name
                shutil.copy2(key_path, destination)
                os.chmod(destination, 0o600)
        self.last_backup_dir = backup_dir
        return backup_dir

    def run(self) -> list[str]:
        applied = self._applied_versions()
        pending = [migration for migration in self.migrations if migration.version not in applied]
        if not pending:
            return []

        # Ensure local keys exist before the backup so DB and keys can be restored together.
        AccountCredentialCipher(self.db_path)
        SystemSecretCipher(self.db_path)
        if self.backup_enabled:
            self._create_backup()
        cursor = self.connection.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            for migration in pending:
                migration.apply(cursor, self.db_path)
                cursor.execute(
                    "INSERT INTO schema_migrations (version, name) VALUES (?, ?)",
                    (migration.version, migration.name),
                )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return [migration.version for migration in pending]


def get_schema_version(connection: sqlite3.Connection) -> str:
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    ).fetchone()
    if not exists:
        return "legacy"
    row = connection.execute("SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1").fetchone()
    return str(row[0]) if row else "legacy"
