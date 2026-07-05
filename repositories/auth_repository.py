"""Persistence boundary for backend users and login sessions."""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, Optional


USER_COLUMNS = (
    "id, username, email, password_hash, is_active, created_at, updated_at, "
    "password_hash_v2, password_hash_version"
)


def _user_from_row(row: sqlite3.Row | tuple | None) -> Optional[Dict[str, Any]]:
    if not row:
        return None
    return {
        "id": row[0],
        "username": row[1],
        "email": row[2],
        "password_hash": row[3] or "",
        "is_active": bool(row[4]),
        "created_at": row[5],
        "updated_at": row[6],
        "password_hash_v2": row[7] or "",
        "password_hash_version": row[8] or 1,
    }


class UserRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def get_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        row = self.connection.execute(
            f"SELECT {USER_COLUMNS} FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        return _user_from_row(row)

    def get_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        row = self.connection.execute(
            f"SELECT {USER_COLUMNS} FROM users WHERE email = ?",
            (email,),
        ).fetchone()
        return _user_from_row(row)

    def create(self, username: str, email: str, password_hash_v2: str, version: int) -> None:
        self.connection.execute(
            "INSERT INTO users (username, email, password_hash, password_hash_v2, password_hash_version) "
            "VALUES (?, ?, '', ?, ?)",
            (username, email, password_hash_v2, version),
        )

    def set_password(self, username: str, password_hash_v2: str, version: int) -> int:
        cursor = self.connection.execute(
            "UPDATE users SET password_hash = '', password_hash_v2 = ?, password_hash_version = ?, "
            "updated_at = CURRENT_TIMESTAMP WHERE username = ?",
            (password_hash_v2, version, username),
        )
        return cursor.rowcount

    def upgrade_password(self, user_id: int, password_hash_v2: str, version: int) -> None:
        self.connection.execute(
            "UPDATE users SET password_hash_v2 = ?, password_hash_version = ?, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (password_hash_v2, version, user_id),
        )


class AuthSessionRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def save(
        self,
        storage_id: str,
        digest: str,
        user_id: int,
        username: str,
        is_admin: bool,
        now: float,
        expires_at: float,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO auth_sessions
            (token, token_digest, user_id, username, is_admin, created_at, expires_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(token) DO UPDATE SET
                token_digest = excluded.token_digest,
                user_id = excluded.user_id,
                username = excluded.username,
                is_admin = excluded.is_admin,
                expires_at = excluded.expires_at,
                last_seen_at = excluded.last_seen_at
            """,
            (storage_id, digest, user_id, username, int(bool(is_admin)), now, expires_at, now),
        )

    def get(self, digest: str, legacy_token: str) -> Optional[tuple]:
        return self.connection.execute(
            """
            SELECT token, user_id, username, is_admin, created_at, expires_at, last_seen_at, token_digest
            FROM auth_sessions
            WHERE token_digest = ? OR token = ?
            LIMIT 1
            """,
            (digest, legacy_token),
        ).fetchone()

    def touch(self, digest: str, legacy_token: str, now: float) -> None:
        self.connection.execute(
            "UPDATE auth_sessions SET last_seen_at = ? WHERE token_digest = ? OR token = ?",
            (now, digest, legacy_token),
        )

    def delete(self, digest: str, legacy_token: str) -> None:
        self.connection.execute(
            "DELETE FROM auth_sessions WHERE token_digest = ? OR token = ?",
            (digest, legacy_token),
        )

    def cleanup_expired(self, now: float) -> None:
        self.connection.execute("DELETE FROM auth_sessions WHERE expires_at <= ?", (now,))

