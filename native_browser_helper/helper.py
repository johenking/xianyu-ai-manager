"""Native browser login coordinator.

The coordinator runs on the user's computer.  It never receives the console
authentication token and it only sends a device-proofed, platform-validated
cookie snapshot to the existing server protocol.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from .cdp import BrowserLauncher, CDPClient, CDPError, is_official_url
from .keystore import IdentityStore, default_state_dir
from .protocol import DeviceIdentity


DEFAULT_SERVER_ORIGINS = {
    "https://xianyu.cxywjx.top",
    "http://127.0.0.1:8091",
    "http://localhost:8091",
}
DEFAULT_LOGIN_URL = "https://www.goofish.com/login"
ALLOWED_COOKIE_SUFFIXES = ("goofish.com", "taobao.com")
RETRYABLE_IMPORT_CODES = {
    "probe_timeout",
    "probe_network_error",
    "token_probe_exception",
    "token_probe_failed",
    "token_probe_retry_exception",
    "probe_retryable_error",
    "session_probe_retryable",
    "session_validation_failed",
    "cookie_persist_failed",
    "client_login_failed",
}


class NativeHelperError(RuntimeError):
    """Expected helper/API failure with a user-visible, non-secret message."""

    def __init__(self, message: str, *, code: str = "helper_error", status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = int(status)


def _origin(value: str) -> str:
    parsed = urllib.parse.urlsplit(str(value or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise NativeHelperError("监控台地址无效", code="invalid_server_origin")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise NativeHelperError("监控台地址无效", code="invalid_server_origin")
    host = parsed.hostname.lower().rstrip(".")
    port = parsed.port
    if parsed.scheme == "https" and port == 443:
        port = None
    if parsed.scheme == "http" and port == 80:
        port = None
    return f"{parsed.scheme}://{host}{f':{port}' if port else ''}"


def _safe_error(response: Any) -> tuple[str, str]:
    if isinstance(response, Mapping):
        detail = response.get("detail")
        if isinstance(detail, Mapping):
            return str(detail.get("message") or "平台请求未完成"), str(
                detail.get("code") or "server_error"
            )
        if detail:
            return str(detail), "server_error"
    return "平台请求未完成", "server_error"


def request_json(
    method: str,
    url: str,
    payload: Optional[Mapping[str, Any]] = None,
    *,
    timeout: float = 12,
    opener: Optional[Callable[..., Any]] = None,
) -> dict[str, Any]:
    body = None
    headers = {"Accept": "application/json", "User-Agent": "XianyuNativeHelper/1.0"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method.upper())
    open_fn = opener or urllib.request.urlopen
    try:
        with open_fn(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            value = json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        try:
            value = json.loads(exc.read().decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            value = {}
        message, code = _safe_error(value)
        raise NativeHelperError(message, code=code, status=exc.code) from exc
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        raise NativeHelperError("平台连接暂时异常，请稍后重试", code="network_error") from exc
    if not isinstance(value, dict):
        raise NativeHelperError("平台响应格式无效", code="invalid_server_response")
    return value


def _cookies_for_import(cookies: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for cookie in cookies:
        name = str(cookie.get("name") or "").strip()
        domain = str(cookie.get("domain") or "").strip()
        normalized_domain = domain.lower().lstrip(".").rstrip(".")
        allowed_domain = any(
            normalized_domain == suffix or normalized_domain.endswith(f".{suffix}")
            for suffix in ALLOWED_COOKIE_SUFFIXES
        )
        if not name or not domain or not allowed_domain:
            continue
        item: dict[str, Any] = {
            "name": name[:256],
            "value": str(cookie.get("value") or "")[:8192],
            "domain": domain[:255],
            "path": str(cookie.get("path") or "/")[:1024],
            "secure": bool(cookie.get("secure")),
            "httpOnly": bool(cookie.get("httpOnly")),
        }
        same_site = cookie.get("sameSite")
        if same_site in {"Strict", "Lax", "None"}:
            item["sameSite"] = same_site
        expires = cookie.get("expires")
        if expires is not None:
            try:
                item["expirationDate"] = float(expires)
            except (TypeError, ValueError):
                pass
        if cookie.get("storeId") is not None:
            item["storeId"] = str(cookie["storeId"])[:128]
        result.append(item)
    return result


def _cookie_value(cookies: list[Mapping[str, Any]], name: str) -> str:
    for cookie in cookies:
        if str(cookie.get("name") or "") == name:
            return str(cookie.get("value") or "").strip()
    return ""


@dataclass
class LoginAttempt:
    session_id: str
    device_id: str
    mode: str
    server_origin: str
    official_url: str
    expires_at: float
    state: str = "opening_browser"
    message: str = "正在打开本机 Chrome"
    error_code: str = ""
    account_id: str = ""
    started_at: float = field(default_factory=time.time)
    client: Optional[CDPClient] = None
    cancel_event: threading.Event = field(default_factory=threading.Event)
    close_requested: bool = False
    thread: Optional[threading.Thread] = None

    def safe_status(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "device_id": self.device_id,
            "state": self.state,
            "message": self.message,
            "error_code": self.error_code,
            "account_id": self.account_id,
            "expires_at": self.expires_at,
        }


class NativeBrowserHelper:
    """Coordinate one or more short-lived login attempts on loopback."""

    def __init__(
        self,
        *,
        browser_family: str = "chrome",
        state_dir: Optional[Path] = None,
        allowed_origins: Optional[set[str]] = None,
        launcher: Optional[BrowserLauncher] = None,
        identity_store: Optional[IdentityStore] = None,
        request: Callable[..., dict[str, Any]] = request_json,
        clock: Callable[[], float] = time.time,
    ):
        family = str(browser_family or "chrome").strip().lower()
        if family not in {"chrome", "edge"}:
            raise NativeHelperError("仅支持 Chrome 或 Edge", code="unsupported_browser")
        self.browser_family = family
        self.state_dir = Path(state_dir or default_state_dir())
        self.identity_store = identity_store or IdentityStore(self.state_dir)
        self.identity: DeviceIdentity = self.identity_store.load_or_create(family)
        configured = allowed_origins or self._configured_origins()
        self.allowed_origins = {_origin(item) for item in configured}
        self.launcher = launcher or BrowserLauncher(self.state_dir)
        self.request = request
        self.clock = clock
        self._attempts: dict[str, LoginAttempt] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _configured_origins() -> set[str]:
        raw = os.environ.get("XMC_ALLOWED_SERVER_ORIGINS", "")
        configured = {item.strip() for item in raw.split(",") if item.strip()}
        return configured or set(DEFAULT_SERVER_ORIGINS)

    def device_record(self) -> dict[str, Any]:
        return self.identity.public_record()

    def start(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        session_id = str(payload.get("session_id") or "").strip()
        device_id = str(payload.get("device_id") or "").strip()
        mode = str(payload.get("mode") or "qr").strip().lower()
        if len(session_id) < 8:
            raise NativeHelperError("登录会话无效", code="invalid_session_id")
        if device_id != self.identity.device_id:
            raise NativeHelperError("设备身份不匹配", code="device_mismatch", status=403)
        if mode not in {"qr", "sms", "password"}:
            raise NativeHelperError("登录方式无效", code="invalid_login_mode")
        origin = _origin(str(payload.get("server_origin") or ""))
        if origin not in self.allowed_origins:
            raise NativeHelperError("监控台地址不在允许列表", code="origin_not_allowed", status=403)
        try:
            expires_at = float(payload.get("expires_at"))
        except (TypeError, ValueError):
            expires_at = self.clock() + 300
        if expires_at <= self.clock():
            raise NativeHelperError("登录会话已过期", code="client_login_expired", status=410)
        official_url = str(payload.get("official_url") or DEFAULT_LOGIN_URL).strip()
        if not is_official_url(official_url):
            raise NativeHelperError("官方登录地址无效", code="invalid_official_url")
        with self._lock:
            existing = self._attempts.get(session_id)
            if existing and existing.state not in {"failed", "cancelled", "expired", "success"}:
                return existing.safe_status()
            attempt = LoginAttempt(
                session_id=session_id,
                device_id=device_id,
                mode=mode,
                server_origin=origin,
                official_url=official_url,
                expires_at=expires_at,
            )
            self._attempts[session_id] = attempt
            thread = threading.Thread(
                target=self._run_attempt,
                args=(attempt,),
                name=f"xmc-native-login-{session_id[:8]}",
                daemon=True,
            )
            attempt.thread = thread
            thread.start()
            return attempt.safe_status()

    def status(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            attempt = self._attempts.get(str(session_id or ""))
            if not attempt:
                raise NativeHelperError("登录会话不存在", code="login_not_found", status=404)
            if attempt.state not in {"success", "failed", "cancelled", "expired"} and attempt.expires_at <= self.clock():
                attempt.state = "expired"
                attempt.message = "本机登录会话已过期"
                attempt.cancel_event.set()
            return attempt.safe_status()

    def cancel(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            attempt = self._attempts.get(str(session_id or ""))
            if not attempt:
                raise NativeHelperError("登录会话不存在", code="login_not_found", status=404)
            attempt.cancel_event.set()
            if attempt.state not in {"success", "failed", "cancelled", "expired"}:
                attempt.state = "cancelled"
                attempt.message = "已取消本机浏览器登录"
            self._close_attempt_locked(attempt)
            return attempt.safe_status()

    def close(self, session_id: str, *, account_id: str = "") -> dict[str, Any]:
        with self._lock:
            attempt = self._attempts.get(str(session_id or ""))
            if not attempt:
                raise NativeHelperError("登录会话不存在", code="login_not_found", status=404)
            if account_id and attempt.account_id and str(account_id) != attempt.account_id:
                raise NativeHelperError("账号确认与登录结果不匹配", code="account_mismatch", status=409)
            attempt.close_requested = True
            if attempt.state == "awaiting_confirmation":
                attempt.state = "success"
                attempt.message = "本机 Chrome 登录成功"
            self._close_attempt_locked(attempt)
            return attempt.safe_status()

    def _run_attempt(self, attempt: LoginAttempt) -> None:
        client: Optional[CDPClient] = None
        try:
            with self._lock:
                if attempt.cancel_event.is_set():
                    return
                attempt.state = "opening_browser"
                attempt.message = "正在打开本机 Chrome 官方页面"
            client = self.launcher.open(self.browser_family, attempt.official_url)
            with self._lock:
                attempt.client = client
                attempt.state = "waiting_user"
                attempt.message = "官方页面已打开，请在本机完成登录和验证"
            self._poll_attempt(attempt, client)
        except (CDPError, NativeHelperError) as exc:
            with self._lock:
                if attempt.state not in {"cancelled", "expired", "success"}:
                    attempt.state = "failed"
                    attempt.error_code = getattr(exc, "code", "browser_error")
                    attempt.message = str(exc)[:200]
        except Exception as exc:  # pragma: no cover - last-resort worker guard
            with self._lock:
                if attempt.state not in {"cancelled", "expired", "success"}:
                    attempt.state = "failed"
                    attempt.error_code = "helper_worker_error"
                    attempt.message = "本机浏览器连接失败，请重试"
            _ = exc
        finally:
            with self._lock:
                if attempt.state in {"failed", "cancelled", "expired"}:
                    self._close_attempt_locked(attempt)

    def _poll_attempt(self, attempt: LoginAttempt, client: CDPClient) -> None:
        last_submit = 0.0
        while not attempt.cancel_event.is_set():
            now = self.clock()
            if now >= attempt.expires_at:
                with self._lock:
                    attempt.state = "expired"
                    attempt.message = "本机登录会话已过期"
                return
            with self._lock:
                if attempt.state in {"cancelled", "success", "failed"}:
                    return
            try:
                location = client.location()
                if not is_official_url(location):
                    time.sleep(0.8)
                    continue
                cookies = _cookies_for_import(client.cookies())
                unb = _cookie_value(cookies, "unb")
                if not unb or now - last_submit < 1.5:
                    time.sleep(0.8)
                    continue
                last_submit = now
                with self._lock:
                    attempt.state = "validating"
                    attempt.message = "正在验证本机浏览器登录态"
                challenge_response = self.request(
                    "POST",
                    f"{attempt.server_origin}/api/client-browser/sessions/{attempt.session_id}/challenge",
                    {"device_id": attempt.device_id, "mode": attempt.mode},
                )
                challenge = challenge_response.get("data") or {}
                binding = {
                    "session_id": attempt.session_id,
                    "mode": attempt.mode,
                    "device_id": attempt.device_id,
                }
                signature = self.identity.sign_proof(challenge, binding)
                import_response = self.request(
                    "POST",
                    f"{attempt.server_origin}/api/client-browser/import",
                    {
                        "session_id": attempt.session_id,
                        "device_id": attempt.device_id,
                        "mode": attempt.mode,
                        "challenge_id": challenge.get("challenge_id"),
                        "signature": signature,
                        "cookies": cookies,
                        "user_agent": client.user_agent(),
                    },
                )
                status = import_response.get("data") or {}
                with self._lock:
                    attempt.state = "awaiting_confirmation"
                    attempt.account_id = str(status.get("account_id") or "")
                    attempt.message = "账号已验证并落库，请回到监控台确认账号"
                    attempt.error_code = ""
                return
            except NativeHelperError as exc:
                if exc.code in RETRYABLE_IMPORT_CODES or exc.status in {408, 425, 429, 500, 502, 503, 504}:
                    with self._lock:
                        attempt.state = "waiting_user"
                        attempt.error_code = exc.code
                        attempt.message = "平台连接暂时异常，保持页面开启并自动重试"
                    time.sleep(2.0)
                    continue
                raise
            except (CDPError, OSError) as exc:
                with self._lock:
                    attempt.state = "waiting_user"
                    attempt.error_code = "browser_probe_retryable"
                    attempt.message = "正在等待本机浏览器响应"
                _ = exc
                time.sleep(1.0)

    def _close_attempt_locked(self, attempt: LoginAttempt) -> None:
        client = attempt.client
        attempt.client = None
        if client:
            self.launcher.close(client)


__all__ = [
    "DEFAULT_LOGIN_URL",
    "NativeBrowserHelper",
    "NativeHelperError",
    "RETRYABLE_IMPORT_CODES",
    "request_json",
]
