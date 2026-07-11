"""Direct registration, credential recovery, and auth abuse controls."""

from __future__ import annotations

from dataclasses import dataclass
import hmac
import ipaddress
import math
import secrets
import sqlite3
import threading
import time
import unicodedata
from typing import Any, Callable, Iterable, Mapping

from email_validator import EmailNotValidError, validate_email

from repositories.auth_repository import (
    AuthSessionRepository,
    UserRepository,
    public_user_view,
)
from security_utils import (
    PASSWORD_HASH_VERSION,
    SystemSecretCipher,
    hash_user_password,
)


COMMON_WEAK_PASSWORDS = frozenset(
    {
        "12345678",
        "123456789",
        "1234567890",
        "abcdefgh",
        "admin123",
        "iloveyou",
        "letmein123",
        "password",
        "password1",
        "qwerty123",
        "qwertyui",
        "welcome1",
    }
)
CHALLENGE_PURPOSES = frozenset(
    {
        "captcha",
        "register_email",
        "password_reset_email",
        "smtp_verify_email",
    }
)
DEFAULT_CHALLENGE_TTL_SECONDS = 600
DEFAULT_CHALLENGE_MAX_ATTEMPTS = 5
RATE_IP_DIGEST_PURPOSE = "auth-rate-ip"
RATE_EMAIL_DIGEST_PURPOSE = "auth-rate-email"
RATE_ACCOUNT_DIGEST_PURPOSE = "auth-rate-account"
RATE_EVENT_RETENTION_SECONDS = 7 * 86_400
RATE_CLEANUP_INTERVAL_SECONDS = 3600


class RegistrationError(Exception):
    """Stable, transport-neutral error returned by the auth domain."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        http_status: int = 400,
        retry_after: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.status_code = http_status
        self.retry_after = retry_after


class _WrongChallengeSecret(Exception):
    pass


@dataclass(frozen=True)
class NormalizedIdentity:
    value: str
    normalized: str


def normalize_username(username: str) -> NormalizedIdentity:
    value = unicodedata.normalize("NFKC", str(username))
    if not 3 <= len(value) <= 24:
        raise RegistrationError(
            "USERNAME_INVALID_LENGTH",
            "用户名长度必须为 3 至 24 个字符",
        )
    if any(not (character.isalnum() or character in "_-") for character in value):
        raise RegistrationError(
            "USERNAME_INVALID_CHARACTERS",
            "用户名只能包含字母、数字、下划线或连字符",
        )
    return NormalizedIdentity(value=value, normalized=value.casefold())


def normalize_email(email: str) -> NormalizedIdentity:
    try:
        result = validate_email(str(email).strip(), check_deliverability=False)
    except EmailNotValidError:
        raise RegistrationError("EMAIL_INVALID", "请输入有效的邮箱地址") from None
    value = result.normalized.lower()
    return NormalizedIdentity(value=value, normalized=value)


def mask_email_for_log(email: str) -> str:
    """Return a stable log-safe hint without retaining the full local part."""

    local, separator, domain = str(email).partition("@")
    if not separator or not local or not domain:
        return "[无效邮箱]"
    domain_name, dot, suffix = domain.partition(".")
    masked_domain = f"{domain_name[:1]}***"
    if dot:
        masked_domain = f"{masked_domain}.{suffix}"
    return f"{local[:1]}***@{masked_domain}"


redact_email = mask_email_for_log


def validate_password(password: str, *, username_normalized: str = "") -> None:
    value = str(password)
    if len(value) < 8:
        raise RegistrationError("PASSWORD_TOO_SHORT", "密码至少需要 8 个字符")
    if len(value.encode("utf-8")) > 72:
        raise RegistrationError(
            "PASSWORD_TOO_LONG",
            "密码的 UTF-8 编码不能超过 72 字节",
        )

    comparable = unicodedata.normalize("NFKC", value).casefold()
    normalized_username = unicodedata.normalize(
        "NFKC", str(username_normalized)
    ).casefold()
    if normalized_username and normalized_username in comparable:
        raise RegistrationError(
            "PASSWORD_CONTAINS_USERNAME",
            "密码不能包含用户名",
        )
    if _is_simple_password(comparable):
        raise RegistrationError(
            "PASSWORD_TOO_WEAK",
            "密码过于常见或简单，请更换更强的密码",
        )


def _is_simple_password(value: str) -> bool:
    if value in COMMON_WEAK_PASSWORDS or len(set(value)) == 1:
        return True
    sequences = (
        "0123456789",
        "abcdefghijklmnopqrstuvwxyz",
        "qwertyuiop",
        "asdfghjkl",
        "zxcvbnm",
    )
    if any(value in sequence or value in sequence[::-1] for sequence in sequences):
        return True
    for period in range(2, min(4, len(value) // 2) + 1):
        if len(value) % period == 0 and value == value[:period] * (len(value) // period):
            return True
    return False


def resolve_client_ip(
    peer_ip: str,
    headers: Mapping[str, Any] | None,
    trusted_proxies: Iterable[str] | str | None,
) -> str:
    peer_text = str(peer_ip or "").strip()
    try:
        peer = ipaddress.ip_address(peer_text)
    except ValueError:
        return peer_text

    if isinstance(trusted_proxies, str):
        configured = trusted_proxies.split(",")
    else:
        configured = trusted_proxies or ()
    trusted = False
    for value in configured:
        try:
            network = ipaddress.ip_network(str(value).strip(), strict=False)
        except ValueError:
            continue
        if peer.version == network.version and peer in network:
            trusted = True
            break
    if not trusted:
        return peer.compressed

    normalized_headers = {
        str(key).casefold(): str(value).strip()
        for key, value in (headers or {}).items()
    }
    candidates = (
        normalized_headers.get("cf-connecting-ip", ""),
        normalized_headers.get("x-forwarded-for", "").split(",", 1)[0].strip(),
        normalized_headers.get("x-real-ip", ""),
    )
    for candidate in candidates:
        try:
            return ipaddress.ip_address(candidate).compressed
        except ValueError:
            continue
    return peer.compressed


class AuthRateLimiter:
    """Persisted auth gates that store only purpose-isolated HMAC digests."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        db_path: str,
        *,
        lock: threading.RLock | None = None,
        clock: Callable[[], float] | None = None,
        cipher: SystemSecretCipher | None = None,
    ) -> None:
        self.connection = connection
        self.lock = lock or threading.RLock()
        self.clock = clock or time.time
        self.cipher = cipher or SystemSecretCipher(db_path)
        self._last_cleanup_at: float | None = None

    def record_event(
        self,
        event_type: str,
        *,
        ip: str = "",
        email: str = "",
        account: str = "",
        success: bool = False,
    ) -> int:
        normalized_type = str(event_type or "").strip()
        if not normalized_type:
            raise RegistrationError("RATE_EVENT_TYPE_REQUIRED", "认证事件类型不能为空")
        digests = self._event_digests(ip=ip, email=email, account=account)
        self._opportunistic_cleanup()
        with self.lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                event_id = self._insert_event(
                    normalized_type,
                    digests,
                    success=success,
                )
                self.connection.commit()
            except Exception:
                self.connection.rollback()
                raise
        return event_id

    def count_events(
        self,
        event_type: str,
        *,
        window_seconds: int,
        ip: str = "",
        email: str = "",
        account: str = "",
        success: bool | None = None,
    ) -> int:
        if window_seconds <= 0:
            raise RegistrationError("RATE_WINDOW_INVALID", "认证事件查询窗口无效")
        digests = self._event_digests(ip=ip, email=email, account=account)
        clauses = ["event_type = ?", "created_at > ?"]
        params: list[Any] = [str(event_type), self.clock() - window_seconds]
        for column, digest in zip(
            ("ip_digest", "email_digest", "account_digest"),
            digests,
        ):
            if digest:
                clauses.append(f"{column} = ?")
                params.append(digest)
        if success is not None:
            clauses.append("success = ?")
            params.append(int(bool(success)))
        with self.lock:
            row = self.connection.execute(
                f"SELECT COUNT(*) FROM auth_rate_events WHERE {' AND '.join(clauses)}",
                params,
            ).fetchone()
        return int(row[0])

    def cleanup_events(self, *, retention_days: int = 7) -> int:
        if retention_days <= 0:
            raise RegistrationError("RATE_RETENTION_INVALID", "认证事件保留时间无效")
        now = self.clock()
        cutoff = now - retention_days * 86_400
        with self.lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = self.connection.execute(
                    "DELETE FROM auth_rate_events WHERE created_at <= ?",
                    (cutoff,),
                )
                self.connection.commit()
            except Exception:
                self.connection.rollback()
                raise
            if retention_days <= 7:
                self._last_cleanup_at = now
        return cursor.rowcount

    def enforce_captcha(self, ip: str) -> None:
        ip_digest = self._ip_digest(ip)
        self._opportunistic_cleanup()
        with self.lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                error = self._limit_error(
                    event_type="captcha",
                    column="ip_digest",
                    digest=ip_digest,
                    window_seconds=3600,
                    limit=30,
                    code="RATE_LIMIT_CAPTCHA_IP",
                    message="图形验证码请求过于频繁，请稍后再试",
                )
                if error is not None:
                    raise error
                self._insert_event("captcha", (ip_digest, "", ""), success=True)
                self.connection.commit()
            except Exception:
                self.connection.rollback()
                raise

    def enforce_email_send(self, ip: str, email: str) -> None:
        ip_digest = self._ip_digest(ip)
        email_digest = self._email_digest(email)
        self._opportunistic_cleanup()
        with self.lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                error = self._email_limit_error(ip_digest, email_digest)
                if error is not None:
                    raise error
                self._insert_event(
                    "email_send",
                    (ip_digest, email_digest, ""),
                    success=True,
                )
                self.connection.commit()
            except Exception:
                self.connection.rollback()
                raise

    def check_login_limit(self, *, ip: str, account: str) -> None:
        ip_digest = self._ip_digest(ip)
        account_digest = self._account_digest(account)
        with self.lock:
            error = self._login_lockout_error(ip_digest, account_digest)
        if error is not None:
            raise error

    def record_login_result(
        self,
        *,
        ip: str,
        account: str,
        success: bool,
    ) -> None:
        ip_digest = self._ip_digest(ip)
        account_digest = self._account_digest(account)
        deferred_error: RegistrationError | None = None
        self._opportunistic_cleanup()
        with self.lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                if not success:
                    existing_error = self._login_lockout_error(
                        ip_digest,
                        account_digest,
                    )
                    if existing_error is not None:
                        raise existing_error
                self._insert_event(
                    "login",
                    (ip_digest, "", account_digest),
                    success=success,
                )
                if not success:
                    account_failures = self._login_failure_count(
                        "account_digest",
                        account_digest,
                    )
                    ip_failures = self._login_failure_count(
                        "ip_digest",
                        ip_digest,
                    )
                    account_locked = account_failures >= 5
                    ip_locked = ip_failures >= 5
                    if account_locked or ip_locked:
                        lockout_at = self.clock()
                        if account_locked:
                            self._insert_event(
                                "login_lockout",
                                ("", "", account_digest),
                                success=False,
                                created_at=lockout_at,
                            )
                        if ip_locked:
                            self._insert_event(
                                "login_lockout",
                                (ip_digest, "", ""),
                                success=False,
                                created_at=lockout_at,
                            )
                        if account_locked:
                            code = "RATE_LIMIT_LOGIN_ACCOUNT"
                            message = "登录失败次数过多，请稍后再试"
                        else:
                            code = "RATE_LIMIT_LOGIN_IP"
                            message = "当前网络登录失败次数过多，请稍后再试"
                        deferred_error = self._rate_error(
                            code,
                            message,
                            lockout_at,
                            900,
                        )
                self.connection.commit()
            except Exception:
                self.connection.rollback()
                raise
        if deferred_error is not None:
            raise deferred_error

    def check_registration_limit(self, ip: str) -> None:
        ip_digest = self._ip_digest(ip)
        with self.lock:
            error = self._limit_error(
                event_type="registration_failure",
                column="ip_digest",
                digest=ip_digest,
                window_seconds=3600,
                limit=10,
                code="RATE_LIMIT_REGISTRATION_IP",
                message="注册或邀请码验证失败次数过多，请稍后再试",
                success=False,
            )
        if error is not None:
            raise error

    def record_registration_failure(self, ip: str) -> None:
        ip_digest = self._ip_digest(ip)
        deferred_error: RegistrationError | None = None
        self._opportunistic_cleanup()
        with self.lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                existing_error = self._limit_error(
                    event_type="registration_failure",
                    column="ip_digest",
                    digest=ip_digest,
                    window_seconds=3600,
                    limit=10,
                    code="RATE_LIMIT_REGISTRATION_IP",
                    message="注册或邀请码验证失败次数过多，请稍后再试",
                    success=False,
                )
                if existing_error is not None:
                    raise existing_error
                self._insert_event(
                    "registration_failure",
                    (ip_digest, "", ""),
                    success=False,
                )
                deferred_error = self._limit_error(
                    event_type="registration_failure",
                    column="ip_digest",
                    digest=ip_digest,
                    window_seconds=3600,
                    limit=10,
                    code="RATE_LIMIT_REGISTRATION_IP",
                    message="注册或邀请码验证失败次数过多，请稍后再试",
                    success=False,
                )
                self.connection.commit()
            except Exception:
                self.connection.rollback()
                raise
        if deferred_error is not None:
            raise deferred_error

    def _opportunistic_cleanup(self) -> int:
        now = self.clock()
        with self.lock:
            if (
                self._last_cleanup_at is not None
                and now - self._last_cleanup_at < RATE_CLEANUP_INTERVAL_SECONDS
            ):
                return 0
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = self.connection.execute(
                    "DELETE FROM auth_rate_events WHERE created_at <= ?",
                    (now - RATE_EVENT_RETENTION_SECONDS,),
                )
                self.connection.commit()
            except Exception:
                self.connection.rollback()
                raise
            self._last_cleanup_at = now
        return cursor.rowcount

    def _event_digests(
        self,
        *,
        ip: str,
        email: str,
        account: str,
    ) -> tuple[str, str, str]:
        return (
            self._ip_digest(ip) if ip else "",
            self._email_digest(email) if email else "",
            self._account_digest(account) if account else "",
        )

    def _ip_digest(self, ip: str) -> str:
        try:
            value = ipaddress.ip_address(str(ip).strip()).compressed
        except ValueError:
            raise RegistrationError("CLIENT_IP_INVALID", "客户端 IP 地址无效") from None
        return self.cipher.digest(value, purpose=RATE_IP_DIGEST_PURPOSE)

    def _email_digest(self, email: str) -> str:
        value = normalize_email(email).normalized
        return self.cipher.digest(value, purpose=RATE_EMAIL_DIGEST_PURPOSE)

    def _account_digest(self, account: str) -> str:
        value = unicodedata.normalize("NFKC", str(account)).strip().casefold()
        if not value:
            raise RegistrationError("ACCOUNT_REQUIRED", "登录账号不能为空")
        return self.cipher.digest(value, purpose=RATE_ACCOUNT_DIGEST_PURPOSE)

    def _insert_event(
        self,
        event_type: str,
        digests: tuple[str, str, str],
        *,
        success: bool,
        created_at: float | None = None,
    ) -> int:
        cursor = self.connection.execute(
            """
            INSERT INTO auth_rate_events (
                event_type, ip_digest, email_digest, account_digest,
                success, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                event_type,
                digests[0],
                digests[1],
                digests[2],
                int(bool(success)),
                self.clock() if created_at is None else created_at,
            ),
        )
        return int(cursor.lastrowid)

    def _email_limit_error(
        self,
        ip_digest: str,
        email_digest: str,
    ) -> RegistrationError | None:
        latest = self.connection.execute(
            """
            SELECT MAX(created_at) FROM auth_rate_events
            WHERE event_type = 'email_send' AND email_digest = ?
              AND success = 1 AND created_at > ?
            """,
            (email_digest, self.clock() - 60),
        ).fetchone()[0]
        if latest is not None:
            return self._rate_error(
                "RATE_LIMIT_EMAIL_COOLDOWN",
                "同一邮箱发送过于频繁，请稍后再试",
                float(latest),
                60,
            )
        email_error = self._limit_error(
            event_type="email_send",
            column="email_digest",
            digest=email_digest,
            window_seconds=3600,
            limit=5,
            code="RATE_LIMIT_EMAIL_HOURLY",
            message="同一邮箱本小时发送次数已达上限",
            success=True,
        )
        if email_error is not None:
            return email_error
        return self._limit_error(
            event_type="email_send",
            column="ip_digest",
            digest=ip_digest,
            window_seconds=3600,
            limit=20,
            code="RATE_LIMIT_EMAIL_IP",
            message="当前网络本小时发送次数已达上限",
            success=True,
        )

    def _login_lockout_error(
        self,
        ip_digest: str,
        account_digest: str,
    ) -> RegistrationError | None:
        for column, digest, code, message in (
            (
                "account_digest",
                account_digest,
                "RATE_LIMIT_LOGIN_ACCOUNT",
                "登录失败次数过多，请稍后再试",
            ),
            (
                "ip_digest",
                ip_digest,
                "RATE_LIMIT_LOGIN_IP",
                "当前网络登录失败次数过多，请稍后再试",
            ),
        ):
            row = self.connection.execute(
                "SELECT MAX(created_at) FROM auth_rate_events "
                f"WHERE event_type = 'login_lockout' AND {column} = ? "
                "AND created_at > ?",
                (digest, self.clock() - 900),
            ).fetchone()
            if row[0] is not None:
                return self._rate_error(
                    code,
                    message,
                    float(row[0]),
                    900,
                )
        return None

    def _login_failure_count(self, column: str, digest: str) -> int:
        if column not in {"ip_digest", "account_digest"}:
            raise ValueError("unsupported login failure dimension")
        row = self.connection.execute(
            "SELECT COUNT(*) FROM auth_rate_events "
            f"WHERE event_type = 'login' AND {column} = ? "
            "AND success = 0 AND created_at > ?",
            (digest, self.clock() - 900),
        ).fetchone()
        return int(row[0])

    def _limit_error(
        self,
        *,
        event_type: str,
        column: str,
        digest: str,
        window_seconds: int,
        limit: int,
        code: str,
        message: str,
        success: bool | None = None,
    ) -> RegistrationError | None:
        if column not in {"ip_digest", "email_digest", "account_digest"}:
            raise ValueError("unsupported rate-limit dimension")
        clauses = [
            "event_type = ?",
            f"{column} = ?",
            "created_at > ?",
        ]
        params: list[Any] = [event_type, digest, self.clock() - window_seconds]
        if success is not None:
            clauses.append("success = ?")
            params.append(int(bool(success)))
        row = self.connection.execute(
            "SELECT COUNT(*), MIN(created_at) FROM auth_rate_events "
            f"WHERE {' AND '.join(clauses)}",
            params,
        ).fetchone()
        if int(row[0]) < limit:
            return None
        return self._rate_error(
            code,
            message,
            float(row[1]),
            window_seconds,
        )

    def _rate_error(
        self,
        code: str,
        message: str,
        first_event_at: float,
        window_seconds: int,
    ) -> RegistrationError:
        retry_after = max(
            1,
            math.ceil(first_event_at + window_seconds - self.clock()),
        )
        return RegistrationError(
            code,
            message,
            http_status=429,
            retry_after=retry_after,
        )


class RegistrationService:
    def __init__(
        self,
        connection: sqlite3.Connection,
        db_path: str,
        *,
        lock: threading.RLock | None = None,
        clock: Callable[[], float] | None = None,
        cipher: SystemSecretCipher | None = None,
    ) -> None:
        self.connection = connection
        self.db_path = db_path
        self.lock = lock or threading.RLock()
        self.clock = clock or time.time
        self.cipher = cipher or SystemSecretCipher(db_path)
        self.users = UserRepository(connection)
        self.sessions = AuthSessionRepository(connection)

    def registration_capacity(self) -> dict[str, int]:
        with self.lock:
            return self._registration_capacity_locked()

    def update_registration_limit(self, limit: int) -> dict[str, int]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise RegistrationError(
                "REGISTRATION_USER_LIMIT_INVALID",
                "注册用户上限必须为 1 至 1000",
            )
        with self.lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                self.connection.execute(
                    """
                    INSERT INTO system_settings (key, value, description)
                    VALUES ('registration_user_limit', ?, 'Maximum number of non-admin registered users')
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value
                    """,
                    (str(limit),),
                )
                capacity = self._registration_capacity_locked()
                if capacity["user_count"] >= limit:
                    self._set_registration_enabled_locked(False)
                self.connection.commit()
            except Exception:
                self.connection.rollback()
                raise
        return capacity

    def _registration_capacity_locked(self) -> dict[str, int]:
        row = self.connection.execute(
            "SELECT value FROM system_settings WHERE key = 'registration_user_limit'"
        ).fetchone()
        try:
            user_limit = int(row[0]) if row is not None else 0
        except (TypeError, ValueError):
            user_limit = 0
        user_count = int(
            self.connection.execute(
                """
                SELECT COUNT(*) FROM users
                WHERE COALESCE(username_normalized, lower(username)) <> 'admin'
                """
            ).fetchone()[0]
        )
        remaining_slots = (
            max(0, user_limit - user_count) if 1 <= user_limit <= 1000 else 0
        )
        return {
            "user_limit": user_limit,
            "user_count": user_count,
            "remaining_slots": remaining_slots,
        }

    def _registration_state_locked(self) -> dict[str, Any]:
        from auth_email_service import registration_readiness

        settings = {
            str(key): str(value or "")
            for key, value in self.connection.execute(
                "SELECT key, value FROM system_settings"
            ).fetchall()
        }
        if settings.get("smtp_password"):
            settings["smtp_password"] = self.cipher.decrypt(settings["smtp_password"])
        capacity = self._registration_capacity_locked()
        return registration_readiness(
            settings,
            db_path=self.db_path,
            user_count=capacity["user_count"],
        )

    def _set_registration_enabled_locked(self, enabled: bool) -> None:
        self.connection.execute(
            """
            INSERT INTO system_settings (key, value, description)
            VALUES ('registration_enabled', ?, 'Whether public registration is enabled')
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            ("true" if enabled else "false",),
        )

    def create_challenge(
        self,
        *,
        purpose: str,
        subject: str,
        secret: str,
        context: str = "",
        ttl_seconds: int = DEFAULT_CHALLENGE_TTL_SECONDS,
        max_attempts: int = DEFAULT_CHALLENGE_MAX_ATTEMPTS,
    ) -> dict[str, Any]:
        normalized_purpose = str(purpose or "")
        if normalized_purpose not in CHALLENGE_PURPOSES:
            raise RegistrationError(
                "CHALLENGE_PURPOSE_INVALID",
                "验证码用途无效",
            )
        if ttl_seconds != DEFAULT_CHALLENGE_TTL_SECONDS:
            raise RegistrationError(
                "CHALLENGE_TTL_INVALID",
                "验证码有效期固定为 600 秒",
            )
        if max_attempts != DEFAULT_CHALLENGE_MAX_ATTEMPTS:
            raise RegistrationError(
                "CHALLENGE_MAX_ATTEMPTS_INVALID",
                "验证码最大尝试次数无效",
            )
        if not str(secret):
            raise RegistrationError("CHALLENGE_SECRET_REQUIRED", "验证码不能为空")

        subject_digest = self._challenge_subject_digest(
            normalized_purpose,
            subject,
        )
        context_digest = self._challenge_context_digest(
            normalized_purpose,
            context,
        )
        if normalized_purpose == "register_email" and context:
            raise RegistrationError(
                "CHALLENGE_CONTEXT_INVALID",
                "注册邮件验证码不接受附加场景",
            )
        secret_digest = self.cipher.digest(
            str(secret),
            purpose=self._challenge_secret_purpose(normalized_purpose),
        )
        challenge_id = secrets.token_urlsafe(24)
        now = self.clock()
        expires_at = now + ttl_seconds
        with self.lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                self.connection.execute(
                    """
                    INSERT INTO auth_challenges (
                        challenge_id, purpose, subject_digest, context_digest,
                        secret_digest, attempt_count, max_attempts,
                        expires_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?)
                    """,
                    (
                        challenge_id,
                        normalized_purpose,
                        subject_digest,
                        context_digest,
                        secret_digest,
                        max_attempts,
                        expires_at,
                        int(now),
                    ),
                )
                self.connection.commit()
            except Exception:
                self.connection.rollback()
                raise
        return {
            "challenge_id": challenge_id,
            "purpose": normalized_purpose,
            "expires_at": expires_at,
            "max_attempts": max_attempts,
        }

    def consume_challenge(
        self,
        *,
        challenge_id: str,
        purpose: str,
        subject: str,
        secret: str,
        context: str = "",
    ) -> bool:
        now = self.clock()
        deferred_error: RegistrationError | None = None
        with self.lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._get_challenge(challenge_id)
                self._validate_challenge_binding(
                    row,
                    purpose=purpose,
                    subject=subject,
                    context=context,
                    now=now,
                )
                if not self.cipher.compare_digest(
                    str(secret),
                    row[4],
                    purpose=self._challenge_secret_purpose(row[1]),
                ):
                    next_attempt = min(int(row[5]) + 1, int(row[6]))
                    self.connection.execute(
                        """
                        UPDATE auth_challenges
                        SET attempt_count = attempt_count + 1
                        WHERE challenge_id = ? AND consumed_at IS NULL
                          AND attempt_count < max_attempts
                        """,
                        (challenge_id,),
                    )
                    self.connection.commit()
                    if next_attempt >= int(row[6]):
                        deferred_error = RegistrationError(
                            "CHALLENGE_LOCKED",
                            "验证码尝试次数过多，请重新获取",
                        )
                    else:
                        deferred_error = RegistrationError(
                            "CHALLENGE_SECRET_INVALID",
                            "验证码错误",
                        )
                else:
                    cursor = self.connection.execute(
                        """
                        UPDATE auth_challenges SET consumed_at = ?
                        WHERE challenge_id = ? AND consumed_at IS NULL
                          AND attempt_count < max_attempts AND expires_at > ?
                        """,
                        (now, challenge_id, now),
                    )
                    if cursor.rowcount != 1:
                        raise RegistrationError(
                            "CHALLENGE_UNAVAILABLE",
                            "验证码已不可用",
                        )
                    self.connection.commit()
            except Exception:
                if deferred_error is None:
                    self.connection.rollback()
                raise
        if deferred_error is not None:
            raise deferred_error
        return True

    def verify_challenge(
        self,
        *,
        challenge_id: str,
        purpose: str,
        subject: str,
        secret: str,
        context: str = "",
    ) -> bool:
        return self.consume_challenge(
            challenge_id=challenge_id,
            purpose=purpose,
            subject=subject,
            secret=secret,
            context=context,
        )

    def register_user(
        self,
        *,
        username: str,
        email: str,
        password: str,
        challenge_id: str,
        verification_code: str,
        terms_version: str,
        invite_code: str = "",
    ) -> dict[str, Any]:
        now = self.clock()
        failed_challenge: RegistrationError | None = None

        with self.lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                state = self._registration_state_locked()
                if not state["enabled"]:
                    raise RegistrationError(
                        "REGISTRATION_CLOSED",
                        "注册暂未开放",
                        http_status=403,
                    )
                username_identity = normalize_username(username)
                email_identity = normalize_email(email)
                challenge = self._get_challenge(challenge_id)
                self._validate_challenge_binding(
                    challenge,
                    purpose="register_email",
                    subject=email_identity.normalized,
                    context="",
                    now=now,
                )
                if not self._challenge_secret_matches(
                    challenge,
                    verification_code,
                ):
                    raise _WrongChallengeSecret

                current_terms = self._current_terms_version()
                if str(terms_version) != current_terms:
                    raise RegistrationError(
                        "TERMS_VERSION_MISMATCH",
                        "注册条款已更新，请重新确认",
                    )
                if self.users.get_by_username(username_identity.value) is not None:
                    raise RegistrationError("USERNAME_TAKEN", "用户名已被使用")
                if self.users.get_by_email(email_identity.normalized) is not None:
                    raise RegistrationError("EMAIL_TAKEN", "邮箱已被使用")
                validate_password(
                    password,
                    username_normalized=username_identity.normalized,
                )

                password_hash = hash_user_password(password)
                user_id = self.users.create(
                    username_identity.value,
                    email_identity.value,
                    password_hash,
                    PASSWORD_HASH_VERSION,
                    username_normalized=username_identity.normalized,
                    email_normalized=email_identity.normalized,
                    terms_version=current_terms,
                    terms_accepted_at=now,
                    is_active=True,
                )
                challenge_cursor = self.connection.execute(
                    """
                    UPDATE auth_challenges SET consumed_at = ?
                    WHERE challenge_id = ? AND consumed_at IS NULL
                      AND attempt_count < max_attempts AND expires_at > ?
                    """,
                    (now, challenge_id, now),
                )
                if challenge_cursor.rowcount != 1:
                    raise RegistrationError(
                        "CHALLENGE_UNAVAILABLE",
                        "验证码已不可用",
                    )
                capacity = self._registration_capacity_locked()
                if capacity["user_count"] >= capacity["user_limit"]:
                    self._set_registration_enabled_locked(False)
                user = self.users.get_by_id(user_id)
                self.connection.commit()
            except _WrongChallengeSecret:
                self.connection.rollback()
                failed_challenge = self._record_failed_challenge_attempt(challenge_id)
            except sqlite3.IntegrityError as exc:
                self.connection.rollback()
                raise RegistrationError(
                    "REGISTRATION_CONFLICT",
                    "用户名或邮箱已被使用",
                ) from exc
            except Exception:
                self.connection.rollback()
                raise
        if failed_challenge is not None:
            raise failed_challenge
        if user is None:
            raise RegistrationError("REGISTRATION_FAILED", "注册失败，请稍后重试")
        return public_user_view(user)

    register = register_user

    def reset_password(
        self,
        *,
        email: str,
        new_password: str,
        challenge_id: str,
        verification_code: str,
    ) -> int:
        email_identity = normalize_email(email)
        now = self.clock()
        failed_challenge: RegistrationError | None = None
        user_id: int | None = None

        with self.lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                challenge = self._get_challenge(challenge_id)
                self._validate_challenge_binding(
                    challenge,
                    purpose="password_reset_email",
                    subject=email_identity.normalized,
                    context="",
                    now=now,
                )
                if not self._challenge_secret_matches(
                    challenge,
                    verification_code,
                ):
                    raise _WrongChallengeSecret

                user = self.users.get_by_email(email_identity.normalized)
                if not user or not user["is_active"]:
                    raise RegistrationError(
                        "PASSWORD_RESET_UNAVAILABLE",
                        "密码重置请求不可用",
                    )
                validate_password(
                    new_password,
                    username_normalized=user["username_normalized"],
                )
                password_hash = hash_user_password(new_password)
                user_id = int(user["id"])
                if (
                    self.users.set_password_by_id(
                        user_id,
                        password_hash,
                        PASSWORD_HASH_VERSION,
                    )
                    != 1
                ):
                    raise RegistrationError(
                        "PASSWORD_RESET_FAILED",
                        "密码重置失败，请稍后重试",
                    )
                self.sessions.delete_by_user_id(user_id)
                challenge_cursor = self.connection.execute(
                    """
                    UPDATE auth_challenges SET consumed_at = ?
                    WHERE challenge_id = ? AND consumed_at IS NULL
                      AND attempt_count < max_attempts AND expires_at > ?
                    """,
                    (now, challenge_id, now),
                )
                if challenge_cursor.rowcount != 1:
                    raise RegistrationError(
                        "CHALLENGE_UNAVAILABLE",
                        "验证码已不可用",
                    )
                self.connection.commit()
            except _WrongChallengeSecret:
                self.connection.rollback()
                failed_challenge = self._record_failed_challenge_attempt(challenge_id)
            except Exception:
                self.connection.rollback()
                raise
        if failed_challenge is not None:
            raise failed_challenge
        if user_id is None:
            raise RegistrationError(
                "PASSWORD_RESET_FAILED",
                "密码重置失败，请稍后重试",
            )
        return user_id

    def _get_challenge(self, challenge_id: str) -> Any:
        row = self.connection.execute(
            """
            SELECT challenge_id, purpose, subject_digest, context_digest,
                   secret_digest, attempt_count, max_attempts,
                   expires_at, consumed_at
            FROM auth_challenges WHERE challenge_id = ?
            """,
            (str(challenge_id),),
        ).fetchone()
        if row is None:
            raise RegistrationError("CHALLENGE_NOT_FOUND", "验证码不存在")
        return row

    def _record_failed_challenge_attempt(
        self,
        challenge_id: str,
    ) -> RegistrationError:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self._get_challenge(challenge_id)
            if row[8] is not None:
                error = RegistrationError("CHALLENGE_CONSUMED", "验证码已使用")
            elif int(row[5]) >= int(row[6]):
                error = RegistrationError(
                    "CHALLENGE_LOCKED",
                    "验证码尝试次数过多，请重新获取",
                )
            else:
                next_attempt = int(row[5]) + 1
                self.connection.execute(
                    """
                    UPDATE auth_challenges SET attempt_count = attempt_count + 1
                    WHERE challenge_id = ? AND consumed_at IS NULL
                      AND attempt_count < max_attempts
                    """,
                    (challenge_id,),
                )
                if next_attempt >= int(row[6]):
                    error = RegistrationError(
                        "CHALLENGE_LOCKED",
                        "验证码尝试次数过多，请重新获取",
                    )
                else:
                    error = RegistrationError(
                        "CHALLENGE_SECRET_INVALID",
                        "验证码错误",
                    )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return error

    def _challenge_secret_matches(self, row: Any, secret: str) -> bool:
        return self.cipher.compare_digest(
            str(secret),
            row[4],
            purpose=self._challenge_secret_purpose(row[1]),
        )

    def _current_terms_version(self) -> str:
        row = self.connection.execute(
            "SELECT value FROM system_settings WHERE key = 'terms_version'"
        ).fetchone()
        if row is None or str(row[0]) != "v2":
            raise RegistrationError(
                "TERMS_VERSION_UNAVAILABLE",
                "当前注册条款不可用",
            )
        return "v2"

    def _validate_challenge_binding(
        self,
        row: Any,
        *,
        purpose: str,
        subject: str,
        context: str,
        now: float,
    ) -> None:
        if str(purpose) != row[1]:
            raise RegistrationError(
                "CHALLENGE_PURPOSE_MISMATCH",
                "验证码用途不匹配",
            )
        expected_subject = self._challenge_subject_digest(row[1], subject)
        if not hmac.compare_digest(expected_subject, str(row[2])):
            raise RegistrationError(
                "CHALLENGE_SUBJECT_MISMATCH",
                "验证码绑定对象不匹配",
            )
        expected_context = self._challenge_context_digest(row[1], context)
        if not hmac.compare_digest(expected_context, str(row[3])):
            raise RegistrationError(
                "CHALLENGE_CONTEXT_MISMATCH",
                "验证码绑定场景不匹配",
            )
        if row[8] is not None:
            raise RegistrationError("CHALLENGE_CONSUMED", "验证码已使用")
        if float(row[7]) <= now:
            raise RegistrationError("CHALLENGE_EXPIRED", "验证码已过期")
        if int(row[5]) >= int(row[6]):
            raise RegistrationError(
                "CHALLENGE_LOCKED",
                "验证码尝试次数过多，请重新获取",
            )

    def _challenge_subject_digest(self, purpose: str, subject: str) -> str:
        if purpose in {
            "register_email",
            "password_reset_email",
            "smtp_verify_email",
        }:
            value = normalize_email(subject).normalized
        else:
            value = str(subject)
            if not value:
                raise RegistrationError(
                    "CHALLENGE_SUBJECT_REQUIRED",
                    "验证码绑定对象不能为空",
                )
        return self.cipher.digest(
            value,
            purpose=f"auth-challenge-subject:{purpose}",
        )

    def _challenge_context_digest(self, purpose: str, context: str) -> str:
        value = str(context or "")
        if not value:
            return ""
        return self.cipher.digest(
            value,
            purpose=f"auth-challenge-context:{purpose}",
        )

    @staticmethod
    def _challenge_secret_purpose(purpose: str) -> str:
        return f"auth-challenge-secret:{purpose}"
