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


def _direct_registration_v1(cursor: sqlite3.Cursor, _db_path: str) -> None:
    cursor.executemany(
        """
        INSERT INTO system_settings (key, value, description)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (
            (
                "registration_user_limit",
                "20",
                "Maximum number of non-admin registered users",
            ),
            ("terms_version", "v2", "Current registration terms version"),
            (
                "registration_enabled",
                "false",
                "Whether public registration is enabled",
            ),
        ),
    )
    cursor.execute(
        """
        UPDATE auth_challenges
        SET consumed_at = CAST(strftime('%s', 'now') AS REAL)
        WHERE purpose = 'register_email' AND consumed_at IS NULL
        """
    )


def _order_analysis_indexes_v1(cursor: sqlite3.Cursor, _db_path: str) -> None:
    orders_exists = cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'orders'"
    ).fetchone()
    if not orders_exists:
        return
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_orders_cookie_created_at "
        "ON orders(cookie_id, created_at)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_orders_status_created_at "
        "ON orders(order_status, created_at)"
    )


def _official_session_identity_v1(cursor: sqlite3.Cursor, _db_path: str) -> None:
    """Persist the real official-browser UA used by token and listener traffic."""
    cookies_exists = cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'cookies'"
    ).fetchone()
    if not cookies_exists:
        return
    _add_column(
        cursor,
        "cookies",
        "browser_user_agent TEXT NOT NULL DEFAULT ''",
    )


def _item_catalog_state_v1(cursor: sqlite3.Cursor, _db_path: str) -> None:
    """Separate seller-catalog state from product knowledge/detail text."""
    item_info_exists = cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'item_info'"
    ).fetchone()
    if not item_info_exists:
        return

    _add_column(cursor, "item_info", "item_image TEXT NOT NULL DEFAULT ''")
    _add_column(cursor, "item_info", "platform_item_status INTEGER")
    _add_column(
        cursor,
        "item_info",
        "catalog_active BOOLEAN NOT NULL DEFAULT FALSE",
    )
    _add_column(cursor, "item_info", "catalog_last_seen_at TIMESTAMP")
    _add_column(
        cursor,
        "item_info",
        "catalog_metadata TEXT NOT NULL DEFAULT '{}'",
    )

    cursor.execute(
        """
        UPDATE item_info
        SET item_image = CASE
                WHEN json_valid(item_detail) THEN
                    CASE
                        WHEN COALESCE(json_extract(item_detail, '$.pic_info.picUrl'), '') LIKE 'http://%'
                        THEN 'https://' || substr(json_extract(item_detail, '$.pic_info.picUrl'), 8)
                        WHEN COALESCE(json_extract(item_detail, '$.pic_info.picUrl'), '') LIKE '//%'
                        THEN 'https:' || json_extract(item_detail, '$.pic_info.picUrl')
                        ELSE COALESCE(
                            json_extract(item_detail, '$.pic_info.picUrl'),
                            json_extract(item_detail, '$.detail_params.picUrl'),
                            ''
                        )
                    END
                ELSE ''
            END,
            platform_item_status = CASE
                WHEN json_valid(item_detail)
                THEN json_extract(item_detail, '$.item_status')
                ELSE NULL
            END,
            catalog_active = CASE
                WHEN json_valid(item_detail)
                     AND CAST(json_extract(item_detail, '$.item_status') AS TEXT) = '0'
                THEN TRUE
                ELSE FALSE
            END,
            catalog_last_seen_at = CASE
                WHEN json_valid(item_detail)
                     AND CAST(json_extract(item_detail, '$.item_status') AS TEXT) = '0'
                THEN updated_at
                ELSE NULL
            END,
            catalog_metadata = CASE
                WHEN json_valid(item_detail) THEN item_detail
                ELSE '{}'
            END
        """
    )
    cursor.execute(
        "UPDATE item_info SET item_image = 'https://' || substr(item_image, 8) "
        "WHERE item_image LIKE 'http://%'"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_item_info_catalog_active "
        "ON item_info(cookie_id, catalog_active, updated_at DESC)"
    )


def _skill_monitor_durable_workflows_v1(
    cursor: sqlite3.Cursor,
    _db_path: str,
) -> None:
    """Add fail-closed monitor controls and durable workflow state.

    This migration is deliberately expand-only. Existing task/result rows are
    retained; a separate identity map gives new writes deterministic
    deduplication without deleting legacy duplicates.
    """
    cursor.executemany(
        """
        INSERT INTO system_settings (key, value, description)
        VALUES (?, 'false', ?)
        ON CONFLICT(key) DO UPDATE SET
            value = 'false',
            description = excluded.description,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            (
                "skill_monitor_enabled",
                "Global fail-closed kill switch for all monitor execution",
            ),
            (
                "skill_monitor_scheduler_enabled",
                "Allow scheduled monitor runs when the global switch is enabled",
            ),
            (
                "skill_monitor_delivery_enabled",
                "Allow monitor outbox delivery when the global switch is enabled",
            ),
            (
                "skill_monitor_mtop_enabled",
                "Allow the experimental MTop search adapter when the global switch is enabled",
            ),
        ),
    )

    cookies_exists = cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'cookies'"
    ).fetchone()
    if cookies_exists:
        _add_column(
            cursor,
            "cookies",
            "cookie_revision INTEGER NOT NULL DEFAULT 0",
        )

    tasks_exists = cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'skill_monitor_tasks'"
    ).fetchone()
    results_exists = cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'skill_monitor_results'"
    ).fetchone()
    if results_exists:
        _add_column(cursor, "skill_monitor_results", "item_id TEXT NOT NULL DEFAULT ''")
        _add_column(cursor, "skill_monitor_results", "run_id INTEGER")
        _add_column(
            cursor,
            "skill_monitor_results",
            "source_adapter TEXT NOT NULL DEFAULT ''",
        )
        _add_column(cursor, "skill_monitor_results", "first_seen_at REAL")
        _add_column(cursor, "skill_monitor_results", "retention_until REAL")
        cursor.execute(
            """
            UPDATE skill_monitor_results
            SET item_id = CASE
                    WHEN json_valid(raw_data)
                    THEN COALESCE(json_extract(raw_data, '$.item_id'), '')
                    ELSE ''
                END,
                first_seen_at = COALESCE(
                    first_seen_at,
                    CAST(strftime('%s', created_at) AS REAL)
                )
            WHERE COALESCE(item_id, '') = '' OR first_seen_at IS NULL
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_skill_monitor_results_item_identity "
            "ON skill_monitor_results(task_id, user_id, item_id)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_skill_monitor_results_run "
            "ON skill_monitor_results(run_id, id)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_skill_monitor_results_retention "
            "ON skill_monitor_results(retention_until)"
        )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS skill_monitor_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_token TEXT NOT NULL UNIQUE,
            task_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            account_id TEXT NOT NULL DEFAULT '',
            trigger_type TEXT NOT NULL DEFAULT 'manual',
            source_adapter TEXT NOT NULL DEFAULT 'playwright',
            status TEXT NOT NULL DEFAULT 'pending',
            claim_token TEXT NOT NULL DEFAULT '',
            lease_expires_at REAL,
            heartbeat_at REAL,
            attempt INTEGER NOT NULL DEFAULT 0 CHECK (attempt >= 0),
            started_at REAL,
            finished_at REAL,
            interrupted_at REAL,
            recovered_from_run_id INTEGER,
            raw_result_count INTEGER NOT NULL DEFAULT 0 CHECK (raw_result_count >= 0),
            accepted_result_count INTEGER NOT NULL DEFAULT 0 CHECK (accepted_result_count >= 0),
            error_code TEXT NOT NULL DEFAULT '',
            error_message TEXT NOT NULL DEFAULT '',
            retention_until REAL,
            created_at REAL NOT NULL DEFAULT (CAST(strftime('%s', 'now') AS REAL)),
            updated_at REAL NOT NULL DEFAULT (CAST(strftime('%s', 'now') AS REAL)),
            FOREIGN KEY (task_id) REFERENCES skill_monitor_tasks(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (recovered_from_run_id) REFERENCES skill_monitor_runs(id) ON DELETE SET NULL
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_skill_monitor_runs_claimable "
        "ON skill_monitor_runs(status, lease_expires_at, created_at, id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_skill_monitor_runs_task_history "
        "ON skill_monitor_runs(task_id, user_id, created_at DESC, id DESC)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_skill_monitor_runs_retention "
        "ON skill_monitor_runs(retention_until)"
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS skill_monitor_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_token TEXT NOT NULL UNIQUE,
            idempotency_key TEXT NOT NULL UNIQUE,
            event_type TEXT NOT NULL,
            run_id INTEGER,
            result_id INTEGER,
            task_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            account_id TEXT NOT NULL DEFAULT '',
            payload_json TEXT NOT NULL DEFAULT '{}',
            retention_until REAL,
            created_at REAL NOT NULL DEFAULT (CAST(strftime('%s', 'now') AS REAL)),
            FOREIGN KEY (run_id) REFERENCES skill_monitor_runs(id) ON DELETE SET NULL,
            FOREIGN KEY (result_id) REFERENCES skill_monitor_results(id) ON DELETE CASCADE,
            FOREIGN KEY (task_id) REFERENCES skill_monitor_tasks(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_skill_monitor_events_run "
        "ON skill_monitor_events(run_id, created_at, id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_skill_monitor_events_result "
        "ON skill_monitor_events(result_id, created_at, id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_skill_monitor_events_retention "
        "ON skill_monitor_events(retention_until)"
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS skill_monitor_result_identities (
            task_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            identity_type TEXT NOT NULL,
            identity_value TEXT NOT NULL,
            result_id INTEGER NOT NULL,
            created_at REAL NOT NULL DEFAULT (CAST(strftime('%s', 'now') AS REAL)),
            PRIMARY KEY (task_id, user_id, identity_type, identity_value),
            FOREIGN KEY (task_id) REFERENCES skill_monitor_tasks(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (result_id) REFERENCES skill_monitor_results(id) ON DELETE CASCADE
        ) WITHOUT ROWID
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_skill_monitor_result_identities_result "
        "ON skill_monitor_result_identities(result_id)"
    )
    if results_exists:
        cursor.execute(
            """
            INSERT OR IGNORE INTO skill_monitor_result_identities (
                task_id, user_id, identity_type, identity_value, result_id
            )
            SELECT task_id, user_id, 'item_url', TRIM(item_url), id
            FROM skill_monitor_results
            WHERE TRIM(COALESCE(item_url, '')) <> ''
            ORDER BY id ASC
            """
        )
        cursor.execute(
            """
            INSERT OR IGNORE INTO skill_monitor_result_identities (
                task_id, user_id, identity_type, identity_value, result_id
            )
            SELECT task_id, user_id, 'item_id', TRIM(item_id), id
            FROM skill_monitor_results
            WHERE TRIM(COALESCE(item_id, '')) <> ''
            ORDER BY id ASC
            """
        )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS skill_monitor_deliveries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            idempotency_key TEXT NOT NULL UNIQUE,
            event_id INTEGER NOT NULL,
            result_id INTEGER NOT NULL,
            task_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            channel_id INTEGER,
            channel_type TEXT NOT NULL DEFAULT '',
            destination_digest TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            claim_token TEXT NOT NULL DEFAULT '',
            lease_expires_at REAL,
            heartbeat_at REAL,
            attempt INTEGER NOT NULL DEFAULT 0 CHECK (attempt >= 0),
            next_attempt_at REAL,
            send_started_at REAL,
            sent_at REAL,
            confirmed_at REAL,
            error_code TEXT NOT NULL DEFAULT '',
            error_message TEXT NOT NULL DEFAULT '',
            retention_until REAL,
            created_at REAL NOT NULL DEFAULT (CAST(strftime('%s', 'now') AS REAL)),
            updated_at REAL NOT NULL DEFAULT (CAST(strftime('%s', 'now') AS REAL)),
            FOREIGN KEY (event_id) REFERENCES skill_monitor_events(id) ON DELETE CASCADE,
            FOREIGN KEY (result_id) REFERENCES skill_monitor_results(id) ON DELETE CASCADE,
            FOREIGN KEY (task_id) REFERENCES skill_monitor_tasks(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (channel_id) REFERENCES notification_channels(id) ON DELETE SET NULL
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_skill_monitor_deliveries_claimable "
        "ON skill_monitor_deliveries(status, next_attempt_at, lease_expires_at, id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_skill_monitor_deliveries_claim_token "
        "ON skill_monitor_deliveries(claim_token)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_skill_monitor_deliveries_user_history "
        "ON skill_monitor_deliveries(user_id, created_at DESC, id DESC)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_skill_monitor_deliveries_retention "
        "ON skill_monitor_deliveries(retention_until)"
    )

    if tasks_exists:
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_skill_monitor_tasks_owner_account "
            "ON skill_monitor_tasks(user_id, account_id, id)"
        )


def _skill_monitor_mtop_offline_v1(
    cursor: sqlite3.Cursor,
    _db_path: str,
) -> None:
    """Add the fixed-window request budget used by the disabled MTop adapter.

    The migration is expand-only. It does not alter existing task, result,
    account, run, event, or delivery rows, so code from the prior release can
    continue to use an expanded database.
    """
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS skill_monitor_request_budgets (
            scope_type TEXT NOT NULL CHECK (scope_type IN ('global', 'account')),
            scope_digest TEXT NOT NULL,
            window_started_at REAL NOT NULL,
            window_seconds INTEGER NOT NULL CHECK (window_seconds > 0),
            request_count INTEGER NOT NULL DEFAULT 0 CHECK (request_count >= 0),
            retention_until REAL NOT NULL,
            updated_at REAL NOT NULL,
            PRIMARY KEY (scope_type, scope_digest)
        ) WITHOUT ROWID
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_skill_monitor_request_budgets_retention "
        "ON skill_monitor_request_budgets(retention_until)"
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS skill_monitor_mtop_breakers (
            scope_digest TEXT PRIMARY KEY,
            state TEXT NOT NULL DEFAULT 'closed'
                CHECK (state IN ('closed', 'open', 'half_open')),
            consecutive_failures INTEGER NOT NULL DEFAULT 0
                CHECK (consecutive_failures >= 0),
            opened_until REAL,
            probe_token TEXT NOT NULL DEFAULT '',
            probe_lease_expires_at REAL,
            last_error_code TEXT NOT NULL DEFAULT '',
            last_failure_at REAL,
            last_success_at REAL,
            retention_until REAL NOT NULL,
            updated_at REAL NOT NULL
        ) WITHOUT ROWID
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_skill_monitor_mtop_breakers_retention "
        "ON skill_monitor_mtop_breakers(retention_until)"
    )


def _account_login_metadata_v1(cursor: sqlite3.Cursor, _db_path: str) -> None:
    cookies_exists = cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'cookies'"
    ).fetchone()
    if not cookies_exists:
        return
    _add_column(cursor, "cookies", "login_method TEXT NOT NULL DEFAULT 'unknown'")
    _add_column(cursor, "cookies", "last_login_at REAL")
    _add_column(cursor, "cookies", "last_validated_at REAL")
    _add_column(cursor, "cookies", "last_expired_at REAL")
    cursor.execute(
        "UPDATE cookies SET login_method = 'unknown' "
        "WHERE login_method IS NULL OR login_method = ''"
    )


def _order_identity_snapshots_v1(cursor: sqlite3.Cursor, _db_path: str) -> None:
    # 订单可信化：成交时身份快照列。快照语义为“只填空值 + 来源等级棘轮”，
    # 商品后续改图/改标题/下架不影响已成交订单的展示；写入守卫在 db_manager。
    # 本迁移只做 DDL，历史数据解析与回填一律走 backfill_order_snapshots.py。
    # 开发库可能已被早期即席 ALTER 提前加过 item_image，_add_column 幂等兼容；
    # 空库（无 orders 表）直接跳过。旧 WIP 已占用 2026072601 的环境由
    # 2026072603 修复迁移收敛，历史 schema_migrations 账本保持不变。
    orders_exists = cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'orders'"
    ).fetchone()
    if not orders_exists:
        return
    # 规范化时间与金额（NULL = 未解析成功，绝不用 0 冒充缺失）
    _add_column(cursor, "orders", "ordered_at_utc REAL")
    _add_column(cursor, "orders", "ordered_at_source TEXT NOT NULL DEFAULT ''")
    _add_column(cursor, "orders", "paid_amount_fen INTEGER")
    # 商品成交快照
    _add_column(cursor, "orders", "item_title TEXT NOT NULL DEFAULT ''")
    _add_column(cursor, "orders", "item_image TEXT DEFAULT ''")
    _add_column(cursor, "orders", "item_image_cache_key TEXT NOT NULL DEFAULT ''")
    _add_column(cursor, "orders", "item_snapshot_source TEXT NOT NULL DEFAULT ''")
    _add_column(cursor, "orders", "item_snapshot_at REAL")
    # 买家成交快照
    _add_column(cursor, "orders", "buyer_nickname TEXT NOT NULL DEFAULT ''")
    _add_column(cursor, "orders", "buyer_avatar_url TEXT NOT NULL DEFAULT ''")
    _add_column(cursor, "orders", "buyer_snapshot_source TEXT NOT NULL DEFAULT ''")
    _add_column(cursor, "orders", "buyer_snapshot_at REAL")


def _business_observability_v1(cursor: sqlite3.Cursor, _db_path: str) -> None:
    # 经营驾驶舱基础：客户身份档案 + 聚合查询索引。
    # customer_profiles 只存“当前可用身份”（昵称/头像/观察时间），
    # 行为计数（有效单量/退款量）一律查询时从 orders 现算，避免增量计数器漂移。
    # 账号删除路径显式清理本表（SQLite 外键未必启用，不依赖 CASCADE）。
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS customer_profiles (
            cookie_id TEXT NOT NULL,
            buyer_id TEXT NOT NULL,
            display_name TEXT NOT NULL DEFAULT '',
            avatar_url TEXT NOT NULL DEFAULT '',
            profile_source TEXT NOT NULL DEFAULT '',
            first_observed_at REAL NOT NULL,
            last_observed_at REAL NOT NULL,
            observation_count INTEGER NOT NULL DEFAULT 1,
            created_at REAL NOT NULL DEFAULT (CAST(strftime('%s','now') AS REAL)),
            updated_at REAL NOT NULL DEFAULT (CAST(strftime('%s','now') AS REAL)),
            PRIMARY KEY (cookie_id, buyer_id),
            FOREIGN KEY (cookie_id) REFERENCES cookies(id) ON DELETE CASCADE
        ) WITHOUT ROWID
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_customer_profiles_last_observed "
        "ON customer_profiles(cookie_id, last_observed_at DESC)"
    )
    orders_exists = cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'orders'"
    ).fetchone()
    if not orders_exists:
        return
    # 索引列逐一防卫：buyer_id 属基础建表列，但存量测试/极简库可能没有；
    # ordered_at_utc 由同批 2026072601 保证，防卫代价为零，口径统一。
    order_columns = _columns(cursor, "orders")
    if {"cookie_id", "buyer_id"} <= order_columns:
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_orders_cookie_buyer ON orders(cookie_id, buyer_id)"
        )
    if {"cookie_id", "ordered_at_utc"} <= order_columns:
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_orders_cookie_ordered_at ON orders(cookie_id, ordered_at_utc)"
        )


def _order_identity_schema_repair_v1(cursor: sqlite3.Cursor, db_path: str) -> None:
    """收敛曾把 2026072601 仅用于 item_image 的旧 WIP 数据库。

    两个被复用的迁移体均为幂等 DDL；新版本只补缺失列、customer_profiles 与索引，
    不删除、不改写任何历史迁移账本记录，也不写订单业务数据。
    """
    _order_identity_snapshots_v1(cursor, db_path)
    _business_observability_v1(cursor, db_path)


def _order_item_field_sources_v1(cursor: sqlite3.Cursor, _db_path: str) -> None:
    """为商品标题与图片分别记录真实来源，兼容保留旧组级来源。"""
    if not cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'orders'"
    ).fetchone():
        return
    _add_column(cursor, "orders", "item_title_source TEXT NOT NULL DEFAULT ''")
    _add_column(cursor, "orders", "item_image_source TEXT NOT NULL DEFAULT ''")
    cursor.execute(
        "UPDATE orders SET item_title_source = item_snapshot_source"
        " WHERE item_title != '' AND item_title_source = ''"
    )
    item_info_exists = cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'item_info'"
    ).fetchone()
    if item_info_exists:
        cursor.execute(
            "UPDATE orders SET item_image_source = CASE"
            " WHEN item_snapshot_source = 'order_list' AND EXISTS ("
            "   SELECT 1 FROM item_info ci"
            "   WHERE ci.cookie_id = orders.cookie_id AND ci.item_id = orders.item_id"
            "     AND ci.item_image = orders.item_image"
            " ) THEN 'catalog'"
            " WHEN item_snapshot_source = 'catalog_metadata' THEN 'catalog_backfill'"
            " WHEN item_snapshot_source IN ('', 'history_unsaved') THEN 'catalog_backfill'"
            " ELSE item_snapshot_source END"
            " WHERE item_image != '' AND item_image_source = ''"
        )
    else:
        cursor.execute(
            "UPDATE orders SET item_image_source = CASE"
            " WHEN item_snapshot_source IN ('', 'history_unsaved') THEN 'catalog_backfill'"
            " ELSE item_snapshot_source END"
            " WHERE item_image != '' AND item_image_source = ''"
        )


def _customer_profile_field_sources_v1(cursor: sqlite3.Cursor, _db_path: str) -> None:
    """把历史聚合来源拆成昵称/头像字段级来源，同时保留兼容列。"""
    if not cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'customer_profiles'"
    ).fetchone():
        return
    _add_column(
        cursor,
        "customer_profiles",
        "display_name_source TEXT NOT NULL DEFAULT ''",
    )
    _add_column(
        cursor,
        "customer_profiles",
        "avatar_source TEXT NOT NULL DEFAULT ''",
    )
    cursor.execute(
        "UPDATE customer_profiles SET display_name_source = profile_source"
        " WHERE display_name != '' AND display_name_source = ''"
    )
    cursor.execute(
        "UPDATE customer_profiles SET avatar_source = profile_source"
        " WHERE avatar_url != '' AND avatar_source = ''"
    )


def _order_buyer_field_sources_v1(cursor: sqlite3.Cursor, _db_path: str) -> None:
    """拆分订单买家昵称/头像来源，并修复旧 WIP 历史图片来源。"""
    if not cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'orders'"
    ).fetchone():
        return
    _add_column(
        cursor,
        "orders",
        "buyer_nickname_source TEXT NOT NULL DEFAULT ''",
    )
    _add_column(
        cursor,
        "orders",
        "buyer_avatar_source TEXT NOT NULL DEFAULT ''",
    )
    cursor.execute(
        "UPDATE orders SET buyer_nickname_source = buyer_snapshot_source"
        " WHERE buyer_nickname != '' AND buyer_nickname_source = ''"
    )
    cursor.execute(
        "UPDATE orders SET buyer_avatar_source = buyer_snapshot_source"
        " WHERE buyer_avatar_url != '' AND buyer_avatar_source = ''"
    )

    # 早期 WIP 把 2026072601 仅用于 item_image；这类非空图片是历史目录
    # 回填，而不是由当前 item_info URL 是否仍相等来决定来源。
    old_image_wip = cursor.execute(
        "SELECT 1 FROM schema_migrations"
        " WHERE version = '2026072601' AND name = 'order_item_image_v1'"
    ).fetchone()
    if old_image_wip:
        cursor.execute(
            "UPDATE orders SET item_image_source = 'catalog_backfill',"
            " item_snapshot_source = CASE"
            "   WHEN item_snapshot_source IN ('', 'history_unsaved', 'catalog_metadata')"
            "   THEN 'catalog_backfill' ELSE item_snapshot_source END"
            " WHERE item_image != '' AND item_image_source = ''"
        )


def _order_query_covering_index_v1(
    cursor: sqlite3.Cursor,
    _db_path: str,
) -> None:
    """支持账号分组、标准化成交时间倒序与稳定 order_id 次序。"""
    if not cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'orders'"
    ).fetchone():
        return
    if {"cookie_id", "ordered_at_utc", "order_id"} <= _columns(cursor, "orders"):
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_orders_cookie_ordered_order"
            " ON orders(cookie_id, ordered_at_utc DESC, order_id DESC)"
        )


def _tenant_isolation_hardening_v1(
    cursor: sqlite3.Cursor,
    _db_path: str,
) -> None:
    """租户隔离加固：修正发货规则归属并补租户过滤索引。

    历史 legacy 启动逻辑会把 user_id IS NULL 的规则批量划给 admin，
    可能留下「规则归属 ≠ 所绑卡券归属」的脏数据；这类规则在匹配时
    会把他人卡券内容发出去。以卡券归属为准修正，卡券主人不受损。
    """
    tables = {
        str(row[0])
        for row in cursor.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    rules_ready = (
        {"delivery_rules", "cards"} <= tables
        and "user_id" in _columns(cursor, "delivery_rules")
        and "user_id" in _columns(cursor, "cards")
    )
    if rules_ready:
        cursor.execute(
            "UPDATE delivery_rules SET user_id = ("
            "  SELECT user_id FROM cards WHERE cards.id = delivery_rules.card_id"
            ") WHERE card_id IS NOT NULL AND EXISTS ("
            "  SELECT 1 FROM cards"
            "  WHERE cards.id = delivery_rules.card_id"
            "    AND cards.user_id IS NOT NULL"
            "    AND (delivery_rules.user_id IS NULL"
            "         OR cards.user_id != delivery_rules.user_id)"
            ")"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_delivery_rules_user"
            " ON delivery_rules(user_id)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_cards_user ON cards(user_id)"
        )
    if "cookies" in tables and "user_id" in _columns(cursor, "cookies"):
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_cookies_user ON cookies(user_id)"
        )


def _order_snapshot_source_repair_v1(
    cursor: sqlite3.Cursor,
    _db_path: str,
) -> None:
    """收口两项来源审查遗留：头像来源高估与 history_unsaved 图片来源。

    遗留一（头像来源保守降档）：早期 _customer_profile_field_sources_v1 /
    _order_buyer_field_sources_v1 把聚合级 profile_source/buyer_snapshot_source
    整段套给头像字段，可能把「实际只到过昵称」的记录也标成头像有真实来源。
    对「头像来源仍等于当初套用的组级来源」这类未经字段级订正的记录，把头像
    来源降回空串（宁可少标来源，也不虚报可信度）；若头像来源已被后续字段级
    写入改成与组级不同的值（例如实时消息独立刷新），说明确有独立来源，保留。

    遗留二（history_unsaved 图片来源收敛）：非空图片一旦落库即属历史目录
    回填，绝不该带 history_unsaved 这一「快照未保存」语义。将这类图片来源
    统一收敛为 catalog_backfill；标题来源不在本次收口范围，保持不动。
    """
    tables = {
        str(row[0])
        for row in cursor.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }

    # 遗留一：客户档案头像来源保守降档
    if "customer_profiles" in tables:
        profile_cols = _columns(cursor, "customer_profiles")
        if {"avatar_source", "profile_source"} <= profile_cols:
            cursor.execute(
                "UPDATE customer_profiles SET avatar_source = ''"
                " WHERE avatar_source != ''"
                "   AND avatar_source = profile_source"
            )

    # 遗留一：订单买家头像来源保守降档
    if "orders" in tables:
        order_cols = _columns(cursor, "orders")
        if {"buyer_avatar_source", "buyer_snapshot_source"} <= order_cols:
            cursor.execute(
                "UPDATE orders SET buyer_avatar_source = ''"
                " WHERE buyer_avatar_source != ''"
                "   AND buyer_avatar_source = buyer_snapshot_source"
            )

        # 遗留二：非空图片的 history_unsaved 来源收敛为 catalog_backfill
        if "item_image_source" in order_cols:
            cursor.execute(
                "UPDATE orders SET item_image_source = 'catalog_backfill'"
                " WHERE item_image != ''"
                "   AND item_image_source = 'history_unsaved'"
            )


def _item_metric_snapshots_v1(
    cursor: sqlite3.Cursor,
    _db_path: str,
) -> None:
    """Verified seller counters, tenant scoped and hour-bucket deduplicated."""
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS item_metric_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            cookie_id TEXT NOT NULL,
            item_id TEXT NOT NULL,
            observed_hour INTEGER NOT NULL,
            observed_at REAL NOT NULL,
            exposure_count INTEGER CHECK (exposure_count IS NULL OR exposure_count >= 0),
            view_count INTEGER CHECK (view_count IS NULL OR view_count >= 0),
            want_count INTEGER CHECK (want_count IS NULL OR want_count >= 0),
            exposure_delta INTEGER CHECK (exposure_delta IS NULL OR exposure_delta >= 0),
            view_delta INTEGER CHECK (view_delta IS NULL OR view_delta >= 0),
            want_delta INTEGER CHECK (want_delta IS NULL OR want_delta >= 0),
            counter_reset INTEGER NOT NULL DEFAULT 0 CHECK (counter_reset IN (0, 1)),
            source TEXT NOT NULL,
            created_at REAL NOT NULL DEFAULT (CAST(strftime('%s','now') AS REAL)),
            UNIQUE(cookie_id, item_id, observed_hour, source),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(cookie_id) REFERENCES cookies(id) ON DELETE CASCADE
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_item_metrics_tenant_time "
        "ON item_metric_snapshots(user_id, observed_at)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_item_metrics_item_time "
        "ON item_metric_snapshots(cookie_id, item_id, observed_at)"
    )
def _item_metric_collection_states_v1(
    cursor: sqlite3.Cursor,
    _db_path: str,
) -> None:
    """Per-account canary state; one tenant can never enable another account."""
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS item_metric_collection_states (
            user_id INTEGER NOT NULL,
            cookie_id TEXT NOT NULL,
            canary_success_count INTEGER NOT NULL DEFAULT 0
                CHECK (canary_success_count BETWEEN 0 AND 3),
            enabled INTEGER NOT NULL DEFAULT 0
                CHECK (
                    enabled IN (0, 1)
                    AND (enabled = 0 OR canary_success_count >= 3)
                ),
            last_attempt_at REAL,
            last_success_at REAL,
            last_canary_observed_at REAL,
            last_error_code TEXT NOT NULL DEFAULT '',
            updated_at REAL NOT NULL DEFAULT (CAST(strftime('%s','now') AS REAL)),
            PRIMARY KEY(user_id, cookie_id),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(cookie_id) REFERENCES cookies(id) ON DELETE CASCADE
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_item_metric_collection_enabled "
        "ON item_metric_collection_states(enabled, user_id, cookie_id)"
    )
    has_system_settings = cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'system_settings'"
    ).fetchone()
    if has_system_settings:
        cursor.execute(
            "DELETE FROM system_settings WHERE key IN (?, ?, ?)",
            (
                "item_metric_collection_enabled",
                "item_metric_canary_success_count",
                "item_metric_schedule_hours",
            ),
        )


def _item_metric_tenant_ownership_v2(
    cursor: sqlite3.Cursor,
    _db_path: str,
) -> None:
    """Bind metric rows to account owners and add durable fulfillment state."""
    tables = {
        str(row[0])
        for row in cursor.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    if "cookies" not in tables:
        return

    cursor.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_cookies_id_user "
        "ON cookies(id, user_id)"
    )
    if "cards" in tables:
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_cards_id_user "
            "ON cards(id, user_id)"
        )

    if "item_metric_snapshots" in tables:
        cursor.execute(
            """
            CREATE TABLE item_metric_snapshots_v2 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                cookie_id TEXT NOT NULL,
                item_id TEXT NOT NULL,
                observed_hour INTEGER NOT NULL,
                observed_at REAL NOT NULL,
                exposure_count INTEGER CHECK (exposure_count IS NULL OR exposure_count >= 0),
                view_count INTEGER CHECK (view_count IS NULL OR view_count >= 0),
                want_count INTEGER CHECK (want_count IS NULL OR want_count >= 0),
                exposure_delta INTEGER CHECK (exposure_delta IS NULL OR exposure_delta >= 0),
                view_delta INTEGER CHECK (view_delta IS NULL OR view_delta >= 0),
                want_delta INTEGER CHECK (want_delta IS NULL OR want_delta >= 0),
                counter_reset INTEGER NOT NULL DEFAULT 0 CHECK (counter_reset IN (0, 1)),
                source TEXT NOT NULL,
                created_at REAL NOT NULL DEFAULT (CAST(strftime('%s','now') AS REAL)),
                UNIQUE(cookie_id, item_id, observed_hour, source),
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY(cookie_id, user_id)
                    REFERENCES cookies(id, user_id) ON DELETE CASCADE
            )
            """
        )
        cursor.execute(
            """
            INSERT INTO item_metric_snapshots_v2 (
                id, user_id, cookie_id, item_id, observed_hour, observed_at,
                exposure_count, view_count, want_count,
                exposure_delta, view_delta, want_delta,
                counter_reset, source, created_at
            )
            SELECT
                s.id, s.user_id, s.cookie_id, s.item_id, s.observed_hour,
                s.observed_at, s.exposure_count, s.view_count, s.want_count,
                s.exposure_delta, s.view_delta, s.want_delta,
                s.counter_reset, s.source, s.created_at
            FROM item_metric_snapshots AS s
            JOIN cookies AS c
              ON c.id = s.cookie_id AND c.user_id = s.user_id
            """
        )
        cursor.execute("DROP TABLE item_metric_snapshots")
        cursor.execute(
            "ALTER TABLE item_metric_snapshots_v2 RENAME TO item_metric_snapshots"
        )
        cursor.execute(
            "CREATE INDEX idx_item_metrics_tenant_time "
            "ON item_metric_snapshots(user_id, observed_at)"
        )
        cursor.execute(
            "CREATE INDEX idx_item_metrics_item_time "
            "ON item_metric_snapshots(cookie_id, item_id, observed_at)"
        )

    if "item_metric_collection_states" in tables:
        state_columns = _columns(cursor, "item_metric_collection_states")
        last_canary_observed_at_expr = (
            "s.last_canary_observed_at"
            if "last_canary_observed_at" in state_columns
            else "NULL"
        )
        cursor.execute(
            """
            CREATE TABLE item_metric_collection_states_v2 (
                user_id INTEGER NOT NULL,
                cookie_id TEXT NOT NULL,
                canary_success_count INTEGER NOT NULL DEFAULT 0
                    CHECK (canary_success_count BETWEEN 0 AND 3),
                enabled INTEGER NOT NULL DEFAULT 0
                    CHECK (
                        enabled IN (0, 1)
                        AND (enabled = 0 OR canary_success_count >= 3)
                    ),
                last_attempt_at REAL,
                last_success_at REAL,
                last_canary_observed_at REAL,
                last_error_code TEXT NOT NULL DEFAULT '',
                updated_at REAL NOT NULL DEFAULT (CAST(strftime('%s','now') AS REAL)),
                PRIMARY KEY(user_id, cookie_id),
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY(cookie_id, user_id)
                    REFERENCES cookies(id, user_id) ON DELETE CASCADE
            )
            """
        )
        cursor.execute(
            f"""
            INSERT INTO item_metric_collection_states_v2 (
                user_id, cookie_id, canary_success_count, enabled,
                last_attempt_at, last_success_at, last_canary_observed_at,
                last_error_code, updated_at
            )
            SELECT
                s.user_id, s.cookie_id, s.canary_success_count,
                CASE
                    WHEN s.enabled = 1 AND s.canary_success_count >= 3 THEN 1
                    ELSE 0
                END,
                s.last_attempt_at, s.last_success_at,
                {last_canary_observed_at_expr}, s.last_error_code, s.updated_at
            FROM item_metric_collection_states AS s
            JOIN cookies AS c
              ON c.id = s.cookie_id AND c.user_id = s.user_id
            """
        )
        cursor.execute("DROP TABLE item_metric_collection_states")
        cursor.execute(
            "ALTER TABLE item_metric_collection_states_v2 "
            "RENAME TO item_metric_collection_states"
        )
        cursor.execute(
            "CREATE INDEX idx_item_metric_collection_enabled "
            "ON item_metric_collection_states(enabled, user_id, cookie_id)"
        )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS fulfillment_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT NOT NULL,
            cookie_id TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            expected_quantity INTEGER NOT NULL
                CHECK (expected_quantity BETWEEN 1 AND 100),
            state TEXT NOT NULL DEFAULT 'prepared'
                CHECK (state IN (
                    'prepared', 'sending', 'committed', 'released', 'manual_review'
                )),
            owner_token TEXT NOT NULL DEFAULT '',
            lease_expires_at REAL NOT NULL DEFAULT 0,
            reason_code TEXT NOT NULL DEFAULT '',
            sent_count INTEGER NOT NULL DEFAULT 0
                CHECK (sent_count >= 0 AND sent_count <= expected_quantity),
            delivered_count INTEGER NOT NULL DEFAULT 0
                CHECK (delivered_count >= 0 AND delivered_count <= expected_quantity),
            attempt_count INTEGER NOT NULL DEFAULT 1 CHECK (attempt_count >= 1),
            sending_at REAL,
            completed_at REAL,
            created_at REAL NOT NULL DEFAULT (CAST(strftime('%s','now') AS REAL)),
            updated_at REAL NOT NULL DEFAULT (CAST(strftime('%s','now') AS REAL)),
            UNIQUE(cookie_id, order_id),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(cookie_id, user_id)
                REFERENCES cookies(id, user_id) ON DELETE CASCADE
        )
        """
    )
    cursor.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_fulfillment_attempt_id_user "
        "ON fulfillment_attempts(id, user_id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_fulfillment_attempt_state "
        "ON fulfillment_attempts(state, updated_at)"
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS fulfillment_card_reservations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            attempt_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            card_id INTEGER,
            ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
            value TEXT NOT NULL CHECK (value <> ''),
            state TEXT NOT NULL DEFAULT 'reserved'
                CHECK (state IN ('reserved', 'committed', 'released', 'manual_review')),
            created_at REAL NOT NULL DEFAULT (CAST(strftime('%s','now') AS REAL)),
            updated_at REAL NOT NULL DEFAULT (CAST(strftime('%s','now') AS REAL)),
            UNIQUE(attempt_id, ordinal),
            FOREIGN KEY(attempt_id, user_id)
                REFERENCES fulfillment_attempts(id, user_id) ON DELETE CASCADE,
            FOREIGN KEY(card_id) REFERENCES cards(id) ON DELETE SET NULL
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_fulfillment_reservation_card_state "
        "ON fulfillment_card_reservations(card_id, state)"
    )
    cursor.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_fulfillment_reservation_card_owner_insert
        BEFORE INSERT ON fulfillment_card_reservations
        WHEN NEW.card_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM cards
            WHERE id = NEW.card_id AND user_id = NEW.user_id
        )
        BEGIN
            SELECT RAISE(ABORT, 'fulfillment reservation card owner mismatch');
        END
        """
    )
    cursor.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_fulfillment_reservation_card_owner_update
        BEFORE UPDATE OF card_id, user_id ON fulfillment_card_reservations
        WHEN NEW.card_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM cards
            WHERE id = NEW.card_id AND user_id = NEW.user_id
        )
        BEGIN
            SELECT RAISE(ABORT, 'fulfillment reservation card owner mismatch');
        END
        """
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
    Migration("2026071103", "direct_registration_v1", _direct_registration_v1),
    Migration("2026071104", "order_analysis_indexes_v1", _order_analysis_indexes_v1),
    Migration(
        "2026071701",
        "official_session_identity_v1",
        _official_session_identity_v1,
    ),
    Migration(
        "2026071801",
        "skill_monitor_durable_workflows_v1",
        _skill_monitor_durable_workflows_v1,
    ),
    Migration(
        "2026071802",
        "skill_monitor_mtop_offline_v1",
        _skill_monitor_mtop_offline_v1,
    ),
    Migration("2026072001", "item_catalog_state_v1", _item_catalog_state_v1),
    Migration(
        "2026072301",
        "account_login_metadata_v1",
        _account_login_metadata_v1,
    ),
    Migration(
        "2026072601",
        "order_identity_snapshots_v1",
        _order_identity_snapshots_v1,
    ),
    Migration(
        "2026072602",
        "business_observability_v1",
        _business_observability_v1,
    ),
    Migration(
        "2026072603",
        "order_identity_schema_repair_v1",
        _order_identity_schema_repair_v1,
    ),
    Migration(
        "2026072604",
        "order_item_field_sources_v1",
        _order_item_field_sources_v1,
    ),
    Migration(
        "2026072605",
        "customer_profile_field_sources_v1",
        _customer_profile_field_sources_v1,
    ),
    Migration(
        "2026072606",
        "order_buyer_field_sources_v1",
        _order_buyer_field_sources_v1,
    ),
    Migration(
        "2026072607",
        "order_query_covering_index_v1",
        _order_query_covering_index_v1,
    ),
    Migration(
        "2026072608",
        "tenant_isolation_hardening_v1",
        _tenant_isolation_hardening_v1,
    ),
    Migration(
        "2026072609",
        "order_snapshot_source_repair_v1",
        _order_snapshot_source_repair_v1,
    ),
    Migration(
        "2026072701",
        "item_metric_snapshots_v1",
        _item_metric_snapshots_v1,
    ),
    Migration(
        "2026072702",
        "item_metric_collection_states_v1",
        _item_metric_collection_states_v1,
    ),
    Migration(
        "2026072703",
        "item_metric_tenant_ownership_v2",
        _item_metric_tenant_ownership_v2,
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
        self.connection.execute("PRAGMA foreign_keys = ON")
        if self.connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            raise RuntimeError("SQLite 外键约束未启用")
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
            backup_connection.execute("PRAGMA foreign_keys = ON")
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
