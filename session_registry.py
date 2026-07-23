"""Unified ownership, TTL, and safe metadata for temporary runtime sessions."""

from __future__ import annotations

import re
import threading
import time
from typing import Any, Dict, Optional

from repositories.runtime_session_repository import RuntimeSessionRepository


_URL_PATTERN = re.compile(r"https?://\S+", re.IGNORECASE)
_SECRET_CONTAINER_PATTERN = re.compile(
    r"(?is)(?P<key>\b(?:cookies?|headers)\b)\s*[:=]\s*[\{\[][^\}\]]*[\}\]]"
)
_SECRET_PATTERN = re.compile(
    r"(?ix)"
    r"(?P<key>[\"']?(?:"
    r"cookie(?:2|s)?|unb|sgcookie|x5sec|_tb_token_|_m_h5_tk(?:_enc)?|"
    r"access[_ -]?token|token|password|authorization|api[_ -]?key"
    r")[\"']?)\s*[:=]\s*(?:[\"']?bearer\s+)?[\"']?[^\"'\s,;}\]]+[\"']?"
)


def sanitize_runtime_error(value: Any) -> str:
    text = str(value or "")
    text = _SECRET_CONTAINER_PATTERN.sub(
        lambda match: f"{match.group('key')}=REDACTED",
        text,
    )
    text = _URL_PATTERN.sub("[REDACTED_URL]", text)
    text = _SECRET_PATTERN.sub(
        lambda match: f"{match.group('key').strip(chr(34) + chr(39))}=[REDACTED]",
        text,
    )
    return text[:500]


class SessionRegistry:
    def __init__(self, repository: RuntimeSessionRepository, lock: threading.RLock):
        self.repository = repository
        self.lock = lock
        self._transient: Dict[str, Any] = {}

    def recover_after_restart(self) -> int:
        with self.lock:
            count = self.repository.mark_active_interrupted(
                time.time(), "服务已重启，请重新发起该操作"
            )
            self.repository.connection.commit()
            return count

    def register(
        self,
        session_id: str,
        session_type: str,
        owner_user_id: Optional[int],
        *,
        account_id: str = "",
        status: str = "created",
        ttl_seconds: int = 3600,
        transient: Any = None,
    ) -> Dict[str, Any]:
        now = time.time()
        existing = self.get(session_id)
        record = {
            "session_id": session_id,
            "session_type": session_type,
            "owner_user_id": owner_user_id,
            "account_id": str(account_id or ""),
            "status": status,
            "error_code": "",
            "error_message": "",
            "created_at": existing["created_at"] if existing else now,
            "updated_at": now,
            "expires_at": now + max(1, int(ttl_seconds)),
        }
        with self.lock:
            self.repository.upsert(record)
            self.repository.connection.commit()
            if transient is not None:
                self._transient[session_id] = transient
        return record

    def update(
        self,
        session_id: str,
        *,
        status: Optional[str] = None,
        error_code: str = "",
        error_message: Any = "",
        ttl_seconds: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        with self.lock:
            record = self.repository.get(session_id)
            if not record:
                return None
            now = time.time()
            if status:
                record["status"] = status
            record["error_code"] = str(error_code or "")[:80]
            record["error_message"] = sanitize_runtime_error(error_message)
            record["updated_at"] = now
            if ttl_seconds is not None:
                record["expires_at"] = now + max(1, int(ttl_seconds))
            self.repository.upsert(record)
            self.repository.connection.commit()
            return record

    def get(self, session_id: str, owner_user_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        with self.lock:
            record = self.repository.get(session_id)
        if not record:
            return None
        if owner_user_id is not None and record.get("owner_user_id") != owner_user_id:
            return None
        return record

    def transient(self, session_id: str, owner_user_id: Optional[int] = None) -> Any:
        if not self.get(session_id, owner_user_id):
            return None
        with self.lock:
            return self._transient.get(session_id)

    def cleanup(self) -> int:
        now = time.time()
        with self.lock:
            expired_ids = [
                session_id
                for session_id in self._transient
                if not (self.repository.get(session_id) or {}).get("expires_at", 0) > now
            ]
            for session_id in expired_ids:
                self._transient.pop(session_id, None)
            count = self.repository.delete_expired(now)
            self.repository.connection.commit()
            return count

    def summary(self) -> Dict[str, int]:
        with self.lock:
            return self.repository.summary(time.time())


_registry: Optional[SessionRegistry] = None
_registry_lock = threading.RLock()


def initialize_session_registry(db_manager: Any) -> SessionRegistry:
    global _registry
    with _registry_lock:
        if (
            _registry is None
            or _registry.repository.connection is not db_manager.conn
        ):
            repository = RuntimeSessionRepository(db_manager.conn)
            _registry = SessionRegistry(repository, db_manager.lock)
            _registry.recover_after_restart()
        return _registry


def get_session_registry() -> SessionRegistry:
    if _registry is None:
        from db_manager import db_manager

        return initialize_session_registry(db_manager)
    return _registry
