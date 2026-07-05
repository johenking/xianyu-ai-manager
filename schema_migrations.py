"""Ordered, transactional SQLite schema migrations with preflight backups."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import os
import shutil
import sqlite3
from typing import Callable, Iterable, Optional, Sequence

from security_utils import (
    ACCOUNT_PASSWORD_ENCRYPTION_VERSION,
    AccountCredentialCipher,
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


MIGRATIONS: Sequence[Migration] = (
    Migration("2026070501", "security_credentials_v1", _security_credentials_v1),
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

        # Ensure the local key exists before the backup so DB and key can be restored together.
        AccountCredentialCipher(self.db_path)
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
