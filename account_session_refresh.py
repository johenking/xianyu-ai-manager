import os
import threading
import time
from typing import Any, Optional


ACTIVE_STATES = {"refreshing", "verification_required"}
TERMINAL_STATES = {"idle", "success", "failed", "timeout", "cancelled"}


def is_valid_account_login_username(username: Optional[str]) -> bool:
    value = (username or '').strip().lower()
    if not value:
        return False
    return not value.startswith(('http://', 'https://'))


def is_runtime_event_active(
    event_at: Optional[float],
    last_success_at: Optional[float] = None,
    *,
    now: Optional[float] = None,
    max_age_seconds: int = 600,
) -> bool:
    if not event_at:
        return False
    current_time = time.time() if now is None else now
    if current_time - float(event_at) > max_age_seconds:
        return False
    if last_success_at and float(event_at) <= float(last_success_at):
        return False
    return True


def resolve_refresh_schedule_anchor(
    status: Optional[dict[str, Any]],
    *,
    now: Optional[float] = None,
) -> float:
    """Return the newest persisted refresh timestamp, or start from now."""
    current_time = time.time() if now is None else float(now)
    candidates = []
    for key in ("last_attempt_at", "last_success_at"):
        try:
            value = float((status or {}).get(key) or 0)
        except (TypeError, ValueError):
            continue
        if value > 0:
            candidates.append(value)
    if not candidates:
        return current_time
    return min(max(candidates), current_time)


def remove_verification_image(path: Optional[str]) -> None:
    if not path:
        return
    normalized = os.path.normpath(path)
    allowed_root = os.path.normpath("static/uploads/images")
    if normalized == allowed_root or not normalized.startswith(allowed_root + os.sep):
        return
    try:
        if os.path.isfile(normalized):
            os.remove(normalized)
    except OSError:
        pass


class ActiveRefreshRegistry:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._workers: dict[str, Any] = {}
        self._cancelled: set[str] = set()

    def register(self, cookie_id: str, worker: Any) -> bool:
        with self._lock:
            if cookie_id in self._workers:
                return False
            self._cancelled.discard(cookie_id)
            self._workers[cookie_id] = worker
            return True

    def unregister(self, cookie_id: str, worker: Any = None) -> None:
        with self._lock:
            current = self._workers.get(cookie_id)
            if worker is None or current is worker:
                self._workers.pop(cookie_id, None)

    def set_worker(self, cookie_id: str, worker: Any) -> bool:
        with self._lock:
            if cookie_id not in self._workers:
                return False
            self._workers[cookie_id] = worker
            return True

    def is_active(self, cookie_id: str) -> bool:
        with self._lock:
            return cookie_id in self._workers

    def cancel(self, cookie_id: str) -> bool:
        with self._lock:
            worker = self._workers.get(cookie_id)
        if worker is None:
            return False
        with self._lock:
            self._cancelled.add(cookie_id)
        close = getattr(worker, "close_browser", None)
        if callable(close):
            close()
        return True

    def consume_cancelled(self, cookie_id: str) -> bool:
        with self._lock:
            if cookie_id not in self._cancelled:
                return False
            self._cancelled.remove(cookie_id)
            return True


active_refresh_registry = ActiveRefreshRegistry()
