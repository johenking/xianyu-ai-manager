"""Safe persistence for runtime session metadata."""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional


class RuntimeSessionRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def upsert(self, record: Dict[str, Any]) -> None:
        self.connection.execute(
            """
            INSERT INTO runtime_sessions (
                session_id, session_type, owner_user_id, account_id, status,
                error_code, error_message, created_at, updated_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                session_type = excluded.session_type,
                owner_user_id = excluded.owner_user_id,
                account_id = excluded.account_id,
                status = excluded.status,
                error_code = excluded.error_code,
                error_message = excluded.error_message,
                updated_at = excluded.updated_at,
                expires_at = excluded.expires_at
            """,
            (
                record["session_id"],
                record["session_type"],
                record.get("owner_user_id"),
                record.get("account_id", ""),
                record["status"],
                record.get("error_code", ""),
                record.get("error_message", ""),
                record["created_at"],
                record["updated_at"],
                record["expires_at"],
            ),
        )

    def get(self, session_id: str) -> Optional[Dict[str, Any]]:
        row = self.connection.execute(
            """
            SELECT session_id, session_type, owner_user_id, account_id, status,
                   error_code, error_message, created_at, updated_at, expires_at
            FROM runtime_sessions WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "session_id": row[0],
            "session_type": row[1],
            "owner_user_id": row[2],
            "account_id": row[3] or "",
            "status": row[4],
            "error_code": row[5] or "",
            "error_message": row[6] or "",
            "created_at": float(row[7]),
            "updated_at": float(row[8]),
            "expires_at": float(row[9]),
        }

    def mark_active_interrupted(self, now: float, message: str) -> int:
        cursor = self.connection.execute(
            """
            UPDATE runtime_sessions
            SET status = 'interrupted', error_code = 'service_restarted',
                error_message = ?, updated_at = ?
            WHERE status IN ('created', 'processing', 'running', 'refreshing',
                             'verification_required', 'verification_checking')
            """,
            (message, now),
        )
        return cursor.rowcount

    def delete_expired(self, now: float) -> int:
        cursor = self.connection.execute(
            "DELETE FROM runtime_sessions WHERE expires_at <= ?",
            (now,),
        )
        return cursor.rowcount

    def summary(self, now: float) -> Dict[str, int]:
        rows = self.connection.execute(
            "SELECT status, COUNT(*) FROM runtime_sessions WHERE expires_at > ? GROUP BY status",
            (now,),
        ).fetchall()
        result = {str(status): int(count) for status, count in rows}
        result["total"] = sum(result.values())
        return result

