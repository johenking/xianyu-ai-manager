"""Persistence boundary for backend users and login sessions."""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, Optional
import unicodedata


USER_COLUMNS = (
    "id, username, email, password_hash, is_active, created_at, updated_at, "
    "password_hash_v2, password_hash_version, username_normalized, "
    "email_normalized, terms_version, terms_accepted_at"
)
PUBLIC_USER_FIELDS = (
    "id",
    "username",
    "email",
    "is_active",
    "created_at",
    "updated_at",
    "username_normalized",
    "email_normalized",
    "terms_version",
    "terms_accepted_at",
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
        "username_normalized": _normalized_username(row[1]),
        "email_normalized": _normalized_email(row[2]),
        "terms_version": row[11],
        "terms_accepted_at": row[12],
    }


def _normalized_username(value: str) -> str:
    return unicodedata.normalize("NFKC", str(value)).casefold()


def _normalized_email(value: str) -> str:
    return unicodedata.normalize("NFKC", str(value)).strip().casefold()


def public_user_view(user: Dict[str, Any]) -> Dict[str, Any]:
    return {field: user.get(field) for field in PUBLIC_USER_FIELDS}


class UserRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def get_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        display = unicodedata.normalize("NFKC", str(username))
        normalized = _normalized_username(display)
        row = self.connection.execute(
            f"SELECT {USER_COLUMNS} FROM users "
            "WHERE username_normalized = ? "
            "OR (username_normalized IS NULL AND username = ?) LIMIT 1",
            (normalized, display),
        ).fetchone()
        if row is None:
            row = next(
                (
                    candidate
                    for candidate in self.connection.execute(
                        f"SELECT {USER_COLUMNS} FROM users ORDER BY id"
                    ).fetchall()
                    if _normalized_username(candidate[1]) == normalized
                ),
                None,
            )
        return _user_from_row(row)

    def get_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        normalized = _normalized_email(email)
        row = self.connection.execute(
            f"SELECT {USER_COLUMNS} FROM users "
            "WHERE email_normalized = ? "
            "OR (email_normalized IS NULL AND lower(trim(email)) = ?) LIMIT 1",
            (normalized, normalized),
        ).fetchone()
        if row is None:
            row = next(
                (
                    candidate
                    for candidate in self.connection.execute(
                        f"SELECT {USER_COLUMNS} FROM users ORDER BY id"
                    ).fetchall()
                    if _normalized_email(candidate[2]) == normalized
                ),
                None,
            )
        return _user_from_row(row)

    def get_by_identifier(self, identifier: str) -> Optional[Dict[str, Any]]:
        username_normalized = _normalized_username(identifier)
        email_normalized = _normalized_email(identifier)
        row = self.connection.execute(
            f"SELECT {USER_COLUMNS} FROM users "
            "WHERE username_normalized = ? OR email_normalized = ? "
            "OR (username_normalized IS NULL AND username = ?) "
            "OR (email_normalized IS NULL AND lower(trim(email)) = ?) LIMIT 1",
            (
                username_normalized,
                email_normalized,
                unicodedata.normalize("NFKC", str(identifier)),
                email_normalized,
            ),
        ).fetchone()
        if row is None:
            row = next(
                (
                    candidate
                    for candidate in self.connection.execute(
                        f"SELECT {USER_COLUMNS} FROM users ORDER BY id"
                    ).fetchall()
                    if _normalized_username(candidate[1]) == username_normalized
                    or _normalized_email(candidate[2]) == email_normalized
                ),
                None,
            )
        return _user_from_row(row)

    def get_by_id(self, user_id: int) -> Optional[Dict[str, Any]]:
        row = self.connection.execute(
            f"SELECT {USER_COLUMNS} FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        return _user_from_row(row)

    def create(
        self,
        username: str,
        email: str,
        password_hash_v2: str,
        version: int,
        *,
        username_normalized: str | None = None,
        email_normalized: str | None = None,
        terms_version: str | None = None,
        terms_accepted_at: float | None = None,
        is_active: bool = True,
    ) -> int:
        username = unicodedata.normalize("NFKC", str(username))
        email = _normalized_email(email)
        if self.get_by_username(username) is not None or self.get_by_email(email) is not None:
            raise sqlite3.IntegrityError("normalized user identity conflict")
        cursor = self.connection.execute(
            "INSERT INTO users "
            "(username, username_normalized, email, email_normalized, password_hash, "
            "password_hash_v2, password_hash_version, is_active, terms_version, "
            "terms_accepted_at) VALUES (?, ?, ?, ?, '', ?, ?, ?, ?, ?)",
            (
                username,
                username_normalized or _normalized_username(username),
                email,
                email_normalized or email,
                password_hash_v2,
                version,
                int(bool(is_active)),
                terms_version,
                terms_accepted_at,
            ),
        )
        return int(cursor.lastrowid)

    def set_password(self, username: str, password_hash_v2: str, version: int) -> int:
        cursor = self.connection.execute(
            "UPDATE users SET password_hash = '', password_hash_v2 = ?, password_hash_version = ?, "
            "updated_at = CURRENT_TIMESTAMP WHERE username_normalized = ?",
            (password_hash_v2, version, _normalized_username(username)),
        )
        return cursor.rowcount

    def set_password_by_id(self, user_id: int, password_hash_v2: str, version: int) -> int:
        cursor = self.connection.execute(
            "UPDATE users SET password_hash = '', password_hash_v2 = ?, "
            "password_hash_version = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (password_hash_v2, version, user_id),
        )
        return cursor.rowcount

    def set_active(self, user_id: int, is_active: bool) -> int:
        cursor = self.connection.execute(
            "UPDATE users SET is_active = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (int(bool(is_active)), user_id),
        )
        return cursor.rowcount

    def list_recent(self, *, limit: int = 50) -> list[Dict[str, Any]]:
        normalized_limit = max(1, min(int(limit), 200))
        rows = self.connection.execute(
            f"SELECT {USER_COLUMNS} FROM users "
            "ORDER BY created_at DESC, id DESC LIMIT ?",
            (normalized_limit,),
        ).fetchall()
        return [_user_from_row(row) for row in rows if row]

    get_recent_users = list_recent

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

    def delete_by_user_id(self, user_id: int) -> int:
        cursor = self.connection.execute(
            "DELETE FROM auth_sessions WHERE user_id = ?",
            (user_id,),
        )
        return cursor.rowcount

    def cleanup_expired(self, now: float) -> None:
        self.connection.execute("DELETE FROM auth_sessions WHERE expires_at <= ?", (now,))
