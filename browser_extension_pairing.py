"""In-memory, single-use pairing for the local Chrome Cookie importer."""

from __future__ import annotations

import hashlib
import ipaddress
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional


PAIRING_TTL_SECONDS = 300
PAIRING_MAX_ATTEMPTS = 5
PAIRING_CREATE_LIMIT_PER_MINUTE = 10
MAX_COOKIE_COUNT = 200
MAX_COOKIE_NAME_LENGTH = 256
MAX_COOKIE_VALUE_LENGTH = 8192
MAX_USER_AGENT_LENGTH = 512
ALLOWED_COOKIE_SUFFIXES = ("goofish.com", "taobao.com")


class PairingError(ValueError):
    def __init__(self, message: str, *, error_code: str, http_status: int = 400):
        super().__init__(message)
        self.error_code = error_code
        self.http_status = http_status


@dataclass
class PairingRecord:
    pairing_id: str
    owner_user_id: int
    code_digest: str = field(repr=False)
    status: str = "waiting"
    message: str = "等待 Chrome 扩展导入"
    error_code: str = ""
    account_id: str = ""
    attempts: int = 0
    created_at: float = field(default_factory=time.time)
    expires_at: float = field(default_factory=lambda: time.time() + PAIRING_TTL_SECONDS)
    consumed_at: Optional[float] = None


def _digest_code(code: str) -> str:
    return hashlib.sha256(str(code or "").encode("utf-8")).hexdigest()


def is_loopback_host(host: str) -> bool:
    normalized = str(host or "").strip().strip("[]")
    if normalized.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def is_allowed_cookie_domain(domain: str) -> bool:
    normalized = str(domain or "").strip().lower().lstrip(".").rstrip(".")
    return bool(
        normalized
        and any(
            normalized == suffix or normalized.endswith(f".{suffix}")
            for suffix in ALLOWED_COOKIE_SUFFIXES
        )
    )


def normalize_structured_cookies(
    records: Iterable[Mapping[str, Any]],
    *,
    now: Optional[float] = None,
) -> dict[str, str]:
    """Filter imported Chrome records and choose one safe value per name."""
    items = list(records)
    if not items or len(items) > MAX_COOKIE_COUNT:
        raise PairingError(
            "Cookie 数量不符合要求",
            error_code="invalid_cookie_count",
        )

    current_time = time.time() if now is None else float(now)
    selected: dict[str, tuple[int, str]] = {}
    for item in items:
        name = str(item.get("name") or "").strip()
        value = str(item.get("value") or "")
        domain = str(item.get("domain") or "").strip().lower()
        path = str(item.get("path") or "/")
        if not name or len(name) > MAX_COOKIE_NAME_LENGTH:
            raise PairingError("Cookie 名称无效", error_code="invalid_cookie_name")
        if len(value) > MAX_COOKIE_VALUE_LENGTH:
            raise PairingError("Cookie 值过大", error_code="cookie_value_too_large")
        if not is_allowed_cookie_domain(domain):
            raise PairingError("Cookie 域名不在允许范围内", error_code="cookie_domain_rejected")
        if not path.startswith("/"):
            raise PairingError("Cookie 路径无效", error_code="invalid_cookie_path")

        expiration = item.get("expirationDate")
        if expiration not in (None, -1, 0, ""):
            try:
                if float(expiration) <= current_time:
                    continue
            except (TypeError, ValueError) as exc:
                raise PairingError(
                    "Cookie 过期时间无效",
                    error_code="invalid_cookie_expiration",
                ) from exc

        normalized_domain = domain.lstrip(".")
        score = 3 if normalized_domain.endswith("goofish.com") else 2
        if name not in selected or score >= selected[name][0]:
            selected[name] = (score, value)

    cookies = {name: value for name, (_, value) in selected.items() if value}
    if not cookies.get("unb"):
        raise PairingError("Cookie 缺少账号身份", error_code="account_identity_missing")
    if not any(cookies.get(name) for name in ("cookie2", "_m_h5_tk", "sgcookie", "t")):
        raise PairingError("Cookie 缺少核心会话字段", error_code="core_cookies_missing")
    return cookies


class BrowserExtensionPairingManager:
    def __init__(
        self,
        *,
        ttl_seconds: float = PAIRING_TTL_SECONDS,
        max_attempts: int = PAIRING_MAX_ATTEMPTS,
        create_limit_per_minute: int = PAIRING_CREATE_LIMIT_PER_MINUTE,
    ) -> None:
        self.ttl_seconds = max(1.0, float(ttl_seconds))
        self.max_attempts = max(1, int(max_attempts))
        self.create_limit_per_minute = max(1, int(create_limit_per_minute))
        self._records: dict[str, PairingRecord] = {}
        self._creation_times: dict[int, list[float]] = {}
        self._lock = threading.RLock()

    def create(self, owner_user_id: int) -> tuple[dict[str, Any], str]:
        pairing_id = secrets.token_urlsafe(18)
        pairing_code = secrets.token_hex(4).upper()
        now = time.time()
        record = PairingRecord(
            pairing_id=pairing_id,
            owner_user_id=int(owner_user_id),
            code_digest=_digest_code(pairing_code),
            created_at=now,
            expires_at=now + self.ttl_seconds,
        )
        with self._lock:
            self._cleanup_locked(now)
            recent = [
                created_at
                for created_at in self._creation_times.get(int(owner_user_id), [])
                if created_at > now - 60
            ]
            if len(recent) >= self.create_limit_per_minute:
                raise PairingError(
                    "创建配对过于频繁，请稍后再试",
                    error_code="pairing_rate_limited",
                    http_status=429,
                )
            recent.append(now)
            self._creation_times[int(owner_user_id)] = recent
            self._records[pairing_id] = record
        return self._safe_status(record), pairing_code

    def get(self, pairing_id: str, owner_user_id: int) -> dict[str, Any]:
        with self._lock:
            record = self._get_locked(pairing_id)
            if record.owner_user_id != int(owner_user_id):
                raise PairingError("配对不存在", error_code="pairing_not_found", http_status=404)
            self._expire_locked(record)
            return self._safe_status(record)

    def consume(
        self,
        pairing_id: str,
        pairing_code: str,
        *,
        remote_host: str,
    ) -> PairingRecord:
        if not is_loopback_host(remote_host):
            raise PairingError(
                "扩展导入仅接受本机回环请求",
                error_code="non_loopback_request",
                http_status=403,
            )
        with self._lock:
            record = self._get_locked(pairing_id)
            self._expire_locked(record)
            if record.status == "expired":
                raise PairingError("配对已过期", error_code="pairing_expired", http_status=410)
            if record.consumed_at is not None or record.status not in {"waiting", "received"}:
                raise PairingError("配对已使用", error_code="pairing_already_used", http_status=409)

            supplied_digest = _digest_code(pairing_code)
            if not secrets.compare_digest(record.code_digest, supplied_digest):
                record.attempts += 1
                if record.attempts >= self.max_attempts:
                    record.status = "failed"
                    record.error_code = "pairing_attempts_exceeded"
                    record.message = "配对尝试次数过多"
                raise PairingError("配对码错误", error_code="pairing_code_invalid", http_status=403)

            record.status = "received"
            record.message = "已收到本机 Chrome 登录状态"
            record.consumed_at = time.time()
            record.code_digest = ""
            return record

    def mark_validating(self, pairing_id: str) -> dict[str, Any]:
        with self._lock:
            record = self._get_locked(pairing_id)
            if record.consumed_at is None or record.status != "received":
                raise PairingError("配对状态无效", error_code="pairing_state_invalid", http_status=409)
            record.status = "validating"
            record.message = "正在验证闲鱼登录状态"
            return self._safe_status(record)

    def succeed(self, pairing_id: str, *, account_id: str) -> dict[str, Any]:
        with self._lock:
            record = self._get_locked(pairing_id)
            record.status = "success"
            record.message = "Chrome 登录状态已导入"
            record.error_code = ""
            record.account_id = str(account_id or "")
            return self._safe_status(record)

    def fail(self, pairing_id: str, *, message: str, error_code: str) -> dict[str, Any]:
        with self._lock:
            record = self._get_locked(pairing_id)
            record.status = "failed"
            record.message = str(message or "导入失败")[:200]
            record.error_code = str(error_code or "import_failed")[:80]
            return self._safe_status(record)

    def clear(self) -> None:
        with self._lock:
            self._records.clear()
            self._creation_times.clear()

    def _get_locked(self, pairing_id: str) -> PairingRecord:
        record = self._records.get(str(pairing_id or ""))
        if record is None:
            raise PairingError("配对不存在", error_code="pairing_not_found", http_status=404)
        return record

    def _expire_locked(self, record: PairingRecord) -> None:
        if record.status in {"waiting", "received"} and record.expires_at <= time.time():
            record.status = "expired"
            record.message = "配对已过期"
            record.error_code = "pairing_expired"
            record.code_digest = ""

    def _cleanup_locked(self, now: float) -> None:
        retention = self.ttl_seconds * 2
        expired_ids = [
            pairing_id
            for pairing_id, record in self._records.items()
            if record.expires_at + retention <= now
        ]
        for pairing_id in expired_ids:
            self._records.pop(pairing_id, None)

    @staticmethod
    def _safe_status(record: PairingRecord) -> dict[str, Any]:
        return {
            "pairing_id": record.pairing_id,
            "status": record.status,
            "message": record.message,
            "error_code": record.error_code,
            "account_id": record.account_id,
            "expires_at": record.expires_at,
        }


browser_extension_pairings = BrowserExtensionPairingManager()


__all__ = [
    "ALLOWED_COOKIE_SUFFIXES",
    "MAX_COOKIE_COUNT",
    "MAX_USER_AGENT_LENGTH",
    "BrowserExtensionPairingManager",
    "PairingError",
    "browser_extension_pairings",
    "is_allowed_cookie_domain",
    "is_loopback_host",
    "normalize_structured_cookies",
]
