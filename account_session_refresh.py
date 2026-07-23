import os
import threading
import time
from typing import Any, Optional


LOGIN_METHODS = {
    "qr", "password", "sms_window", "chrome_extension",
    "manual_cookie", "unknown",
}
LOGIN_METHOD_LABELS = {
    "qr": "扫码登录",
    "password": "账号密码",
    "sms_window": "手机号验证码",
    "chrome_extension": "本机 Chrome",
    "manual_cookie": "手填 Cookie",
    "unknown": "历史登录",
}
REAUTH_ACTIONS = {
    "qr": "qr_login",
    "password": "password_login",
    "sms_window": "sms_login",
    "chrome_extension": "chrome_extension_import",
    "manual_cookie": "manual_cookie",
    "unknown": "choose_login",
}
REAUTH_MESSAGES = {
    "qr": "当前登录态需要重新扫码",
    "password": "账号密码续期未完成，请重新登录",
    "sms_window": "当前登录态需要重新完成手机号验证码登录",
    "chrome_extension": "当前登录态需要从本机 Chrome 重新导入",
    "manual_cookie": "当前登录态需要重新填写 Cookie",
    "unknown": "当前登录态需要重新登录",
}
PASSWORD_MANUAL_REAUTH_ERROR_CODES = {
    "invalid_credentials", "no_credentials", "account_mismatch",
    "account_identity_mismatch", "account_identity_missing",
    "verification_timeout", "login_timeout", "login_form_missing",
    "password_mode_missing",
    "credential_inputs_missing", "agreement_missing",
    "login_submit_missing", "login_state_unknown",
}
OFFICIAL_LOGIN_ERROR_MESSAGES = {
    "invalid_credentials": "闲鱼账号或密码错误，请重新登录",
    "no_credentials": "未保存可用于续期的闲鱼账号密码，请重新登录",
    "account_mismatch": "官方浏览器登录账号与当前账号不一致，请重新登录",
    "account_identity_mismatch": "官方浏览器登录账号与当前账号不一致，请重新登录",
    "account_identity_missing": "账号缺少稳定身份，请重新登录",
    "verification_timeout": "身份验证等待超时，请重新登录",
    "login_timeout": "闲鱼官方登录等待超时，请重新登录",
    "official_window_timeout": "官方窗口登录等待超时，请重新发起",
    "login_form_missing": "闲鱼官方登录页面已变化，请重新登录",
    "password_mode_missing": "闲鱼官方登录页面已变化，请重新登录",
    "credential_inputs_missing": "闲鱼官方登录页面已变化，请重新登录",
    "agreement_missing": "闲鱼官方登录页面已变化，请重新登录",
    "login_submit_missing": "闲鱼官方登录页面已变化，请重新登录",
    "login_state_unknown": "闲鱼官方登录未能确认成功，请重新登录",
    "profile_in_use": "闲鱼官方浏览器档案正在使用，请关闭对应窗口后重试",
    "browser_error": "闲鱼官方浏览器启动失败，请稍后重试",
    "profile_promotion_failed": "登录已完成，但专用浏览器档案保存失败",
    "verification_browser_failed": "身份验证窗口未能保持打开，请重新登录",
    "session_probe_retryable": "平台状态检查出现临时异常，请稍后重试",
    "cancelled": "闲鱼官方登录会话已取消",
}

ACTIVE_STATES = {"refreshing", "verification_required"}
PASSIVE_STATES = {"action_required"}
TERMINAL_STATES = {
    "idle", "success", "failed", "timeout", "cancelled",
    "manual_reauth_required",
}


def normalize_login_method(login_method: Optional[str]) -> str:
    value = str(login_method or "").strip().lower()
    return value if value in LOGIN_METHODS else "unknown"


def login_method_label(login_method: Optional[str]) -> str:
    return LOGIN_METHOD_LABELS[normalize_login_method(login_method)]


def reauth_action_for(login_method: Optional[str]) -> str:
    return REAUTH_ACTIONS[normalize_login_method(login_method)]


def reauth_message_for(login_method: Optional[str]) -> str:
    return REAUTH_MESSAGES[normalize_login_method(login_method)]


def is_valid_account_login_username(username: Optional[str]) -> bool:
    value = (username or '').strip().lower()
    if not value:
        return False
    return not value.startswith(('http://', 'https://'))


def supports_automatic_refresh(
    login_method: Optional[str],
    username: Optional[str],
    has_password: bool,
) -> bool:
    return (
        normalize_login_method(login_method) == "password"
        and bool(has_password)
        and is_valid_account_login_username(username)
    )


def password_refresh_requires_manual_reauth(error_code: Optional[str]) -> bool:
    return str(error_code or "").strip() in PASSWORD_MANUAL_REAUTH_ERROR_CODES


def official_login_error_message(
    error_code: Optional[str],
    *,
    fallback: str = "闲鱼官方登录未完成，请稍后重试",
) -> str:
    return OFFICIAL_LOGIN_ERROR_MESSAGES.get(str(error_code or "").strip(), fallback)


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
        cancelled = False
        with self._lock:
            if cookie_id not in self._workers:
                return False
            self._workers[cookie_id] = worker
            cancelled = cookie_id in self._cancelled
        if cancelled:
            close = getattr(worker, "close_browser", None)
            if callable(close):
                close()
        return True

    def is_active(self, cookie_id: str) -> bool:
        with self._lock:
            return cookie_id in self._workers

    def browser_active(self, cookie_id: str) -> bool:
        with self._lock:
            worker = self._workers.get(cookie_id)
        active = getattr(worker, "browser_active", None)
        if not callable(active):
            return False
        try:
            return bool(active())
        except Exception:
            return False

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

    def show_browser(self, cookie_id: str) -> bool:
        with self._lock:
            worker = self._workers.get(cookie_id)
        active = getattr(worker, "browser_active", None)
        request_visible = getattr(worker, "request_visible", None)
        try:
            browser_active = bool(active()) if callable(active) else False
        except Exception:
            browser_active = False
        if not browser_active:
            return False
        if not callable(request_visible):
            return False
        request_visible()
        return True

    def consume_cancelled(self, cookie_id: str) -> bool:
        with self._lock:
            if cookie_id not in self._cancelled:
                return False
            self._cancelled.remove(cookie_id)
            return True


active_refresh_registry = ActiveRefreshRegistry()
