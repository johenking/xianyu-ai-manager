from __future__ import annotations

import hashlib
import inspect
import os
import re
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from loguru import logger
from utils.xianyu_session_probe import (
    PROBE_EXPIRED,
    PROBE_RETRYABLE_ERROR,
    PROBE_VERIFICATION_REQUIRED,
    SessionProbeResult,
    detect_default_browser_user_agent,
    is_allowed_verification_url,
    probe_message_session_sync,
)


GOOFISH_IM_URL = "https://www.goofish.com/im"
GOOFISH_LOGIN_URL = "https://www.goofish.com/login"
PASSPORT_LOGIN_URL = "https://passport.goofish.com/mini_login.htm"
COOKIE_URLS = (
    GOOFISH_IM_URL,
    PASSPORT_LOGIN_URL,
    "https://h5api.m.goofish.com/",
    "https://h5api.m.taobao.com/",
)
SESSION_COOKIE_NAMES = {
    "_m_h5_tk",
    "_m_h5_tk_enc",
    "cookie2",
    "sgcookie",
    "t",
}


@dataclass
class OfficialLoginResult:
    status: str
    cookies: dict[str, str] = field(default_factory=dict)
    unb: str = ""
    error_code: str = ""
    message: str = ""
    verification_image_path: str = ""
    verification_url: str = field(default="", repr=False)
    requires_manual_action: bool = False
    used_password: bool = False
    browser_user_agent: str = ""
    access_token: str = field(default="", repr=False)

    @property
    def succeeded(self) -> bool:
        return self.status == "success"


class OfficialLoginWorker:
    """Thread-safe cancellation handle for a single Playwright login session."""

    def __init__(self) -> None:
        self.cancel_event = threading.Event()
        self.show_event = threading.Event()
        self._resource_lock = threading.RLock()
        self._context: Any = None
        self._playwright: Any = None

    def attach(self, context: Any, playwright: Any) -> None:
        with self._resource_lock:
            self._context = context
            self._playwright = playwright

    def detach(self, context: Any = None) -> None:
        with self._resource_lock:
            if context is None or self._context is context:
                self._context = None
                self._playwright = None

    def close_browser(self) -> None:
        self.cancel_event.set()
        with self._resource_lock:
            context = self._context
        if context is not None:
            try:
                context.close()
            except Exception:
                # Playwright objects are owned by the login thread. The event is
                # authoritative; closing here is only a best-effort wake-up.
                pass

    def request_visible(self) -> None:
        self.show_event.set()

    def browser_active(self) -> bool:
        with self._resource_lock:
            return self._context is not None


class XianyuOfficialLoginService:
    LOGIN_FORM_SELECTORS = (
        "#fm-login-id",
        "input[name='fm-login-id']",
        "#fm-login-password",
        "input[name='fm-login-password']",
        "a.password-login-tab-item",
        ".password-login-tab-item",
        "button.password-login",
        "input[type='tel']",
        "input[autocomplete='tel']",
        ".qrcode",
        ".qrcode-img",
        "[class*='qrcode']",
    )
    QR_IMAGE_SELECTORS = (
        ".qrcode-img",
        ".qrcode img",
        "[class*='qrcode'] img",
        "img[alt*='二维码']",
        "canvas",
    )
    ACCOUNT_INPUT_SELECTORS = (
        "#fm-login-id",
        "input[name='fm-login-id']",
        "input[autocomplete='username']",
    )
    PASSWORD_INPUT_SELECTORS = (
        "#fm-login-password",
        "input[name='fm-login-password']",
        "input[autocomplete='current-password']",
        "input[type='password']",
    )
    PASSWORD_TAB_SELECTORS = (
        "a.password-login-tab-item",
        ".password-login-tab-item",
        "[data-spm*='password']",
    )
    AGREEMENT_SELECTORS = (
        "#fm-agreement-checkbox",
        "input[name='agreement']",
        ".fm-agreement input[type='checkbox']",
        ".agreement-checkbox input[type='checkbox']",
        "input[type='checkbox']",
    )
    LOGIN_BUTTON_SELECTORS = (
        "button.password-login",
        ".fm-button.fm-submit.password-login",
        "button[type='submit']",
    )
    SECURITY_SELECTORS = (
        "#nc_1_n1z",
        ".nc-container",
        ".nc_scale",
        "#nocaptcha",
        "#baxia-dialog-content",
        "iframe[src*='punish']",
        "iframe[src*='verify']",
        "iframe[src*='captcha']",
        "[class*='face-verify']",
        "[class*='security-verify']",
    )
    LOGIN_ERROR_SELECTORS = (
        ".fm-error",
        ".login-error",
        ".error-msg",
        "#J_Message",
        "[class*='login-error']",
    )
    SECURITY_URL_MARKERS = (
        "/iv/",
        "punish",
        "captcha",
        "secverify",
        "faceverify",
        "verifycenter",
    )

    _profile_locks_guard = threading.Lock()
    _profile_locks: dict[str, threading.Lock] = {}

    def __init__(
        self,
        *,
        profile_root: Path | str = "browser_data",
        verification_root: Path | str = "static/uploads/images",
        playwright_factory: Optional[Callable[[], Any]] = None,
        verification_timeout: float = 900.0,
        login_timeout: float = 60.0,
        poll_interval: float = 1.0,
        probe_interval: float = 5.0,
        session_validator: Optional[
            Callable[[str, str], SessionProbeResult]
        ] = probe_message_session_sync,
    ) -> None:
        self.profile_root = Path(profile_root)
        self.verification_root = Path(verification_root)
        self.playwright_factory = playwright_factory or self._default_playwright_factory
        self.verification_timeout = verification_timeout
        self.login_timeout = login_timeout
        self.poll_interval = poll_interval
        self.probe_interval = max(self.poll_interval, float(probe_interval))
        self.session_validator = session_validator

    @staticmethod
    def _default_playwright_factory() -> Any:
        from playwright.sync_api import sync_playwright

        return sync_playwright()

    @classmethod
    def _lock_for(cls, key: str) -> threading.Lock:
        with cls._profile_locks_guard:
            return cls._profile_locks.setdefault(key, threading.Lock())

    @staticmethod
    def cookies_to_string(cookies: dict[str, str]) -> str:
        return "; ".join(f"{name}={value}" for name, value in cookies.items())

    @staticmethod
    def parse_cookie_string(cookie_string: str) -> dict[str, str]:
        parsed: dict[str, str] = {}
        for part in (cookie_string or "").split(";"):
            if "=" not in part:
                continue
            name, value = part.split("=", 1)
            name = name.strip()
            if name:
                parsed[name] = value.strip()
        return parsed

    @staticmethod
    def _safe_profile_key(value: str) -> str:
        raw = str(value or "").strip()
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", raw).strip("._")
        if not safe:
            raise ValueError("缺少有效的闲鱼账号标识")
        if safe != raw:
            digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]
            safe = f"{safe}_{digest}"
        return safe

    def profile_path(self, unb: str) -> Path:
        return self.profile_root / f"user_{self._safe_profile_key(unb)}"

    def login_with_password(
        self,
        *,
        account: str,
        password: str,
        show_browser: bool,
        worker: Optional[OfficialLoginWorker] = None,
        on_status: Optional[Callable[[OfficialLoginResult], None]] = None,
        on_validated: Optional[Callable[[OfficialLoginResult], Any]] = None,
    ) -> OfficialLoginResult:
        worker = worker or OfficialLoginWorker()
        self.profile_root.mkdir(parents=True, exist_ok=True)
        temporary_profile = self.profile_root / f".login_{uuid.uuid4().hex}"
        lock_key = f"login:{account.strip().lower()}"

        with self._lock_for(lock_key):
            try:
                result = self._run_profile(
                    mode="password",
                    profile_path=temporary_profile,
                    account=account,
                    password=password,
                    show_browser=show_browser,
                    current_cookie="",
                    expected_unb="",
                    allow_password=True,
                    worker=worker,
                    on_status=on_status,
                    on_validated=on_validated,
                )
                if not result.succeeded:
                    return result

                try:
                    self._promote_profile(temporary_profile, result.unb)
                except Exception as exc:
                    logger.error(f"归档闲鱼官方浏览器档案失败: {type(exc).__name__}")
                    return OfficialLoginResult(
                        status="failed",
                        error_code="profile_promotion_failed",
                        message="登录成功，但浏览器档案归档失败",
                        used_password=result.used_password,
                    )
                return result
            finally:
                if temporary_profile.exists():
                    shutil.rmtree(temporary_profile, ignore_errors=True)

    def login_with_qr(
        self,
        *,
        show_browser: bool = False,
        worker: Optional[OfficialLoginWorker] = None,
        on_status: Optional[Callable[[OfficialLoginResult], None]] = None,
        on_validated: Optional[Callable[[OfficialLoginResult], Any]] = None,
    ) -> OfficialLoginResult:
        worker = worker or OfficialLoginWorker()
        self.profile_root.mkdir(parents=True, exist_ok=True)
        temporary_profile = self.profile_root / f".login_{uuid.uuid4().hex}"

        try:
            result = self._run_profile(
                mode="qr",
                profile_path=temporary_profile,
                account="",
                password="",
                show_browser=show_browser,
                current_cookie="",
                expected_unb="",
                allow_password=False,
                worker=worker,
                on_status=on_status,
                on_validated=on_validated,
            )
            if not result.succeeded:
                return result
            try:
                self._promote_profile(temporary_profile, result.unb)
            except Exception as exc:
                logger.error(f"归档闲鱼官方浏览器档案失败: {type(exc).__name__}")
                return OfficialLoginResult(
                    status="failed",
                    error_code="profile_promotion_failed",
                    message="登录成功，但浏览器档案归档失败",
                )
            return result
        finally:
            if temporary_profile.exists():
                shutil.rmtree(temporary_profile, ignore_errors=True)

    def login_with_official_window(
        self,
        *,
        account: str = "",
        expected_unb: str = "",
        timeout: float = 900.0,
        worker: Optional[OfficialLoginWorker] = None,
        on_status: Optional[Callable[[OfficialLoginResult], None]] = None,
        on_validated: Optional[Callable[[OfficialLoginResult], Any]] = None,
    ) -> OfficialLoginResult:
        """Wait for a user-completed SMS login in a visible official Chrome window."""
        worker = worker or OfficialLoginWorker()
        self.profile_root.mkdir(parents=True, exist_ok=True)
        temporary_profile = self.profile_root / f".window_{uuid.uuid4().hex}"
        lock_key = expected_unb or account.strip().lower() or temporary_profile.name

        with self._lock_for(f"window:{lock_key}"):
            try:
                result = self._run_profile(
                    mode="sms",
                    profile_path=temporary_profile,
                    account=account,
                    password="",
                    show_browser=True,
                    current_cookie="",
                    expected_unb=expected_unb,
                    allow_password=False,
                    worker=worker,
                    on_status=on_status,
                    on_validated=on_validated,
                    login_wait_timeout=max(0.001, float(timeout)),
                )
                if not result.succeeded:
                    return result
                try:
                    self._promote_profile(temporary_profile, result.unb)
                except Exception as exc:
                    logger.error(
                        "归档闲鱼官方窗口浏览器档案失败: {}",
                        type(exc).__name__,
                    )
                    return OfficialLoginResult(
                        status="failed",
                        error_code="profile_promotion_failed",
                        message="登录已完成，但专用浏览器档案保存失败",
                    )
                return result
            finally:
                if temporary_profile.exists():
                    shutil.rmtree(temporary_profile, ignore_errors=True)

    def refresh_session(
        self,
        *,
        profile_unb: str,
        current_cookie: str,
        account: str = "",
        password: str = "",
        show_browser: bool = False,
        allow_password: bool = False,
        worker: Optional[OfficialLoginWorker] = None,
        on_status: Optional[Callable[[OfficialLoginResult], None]] = None,
        on_validated: Optional[Callable[[OfficialLoginResult], Any]] = None,
        initial_verification_url: str = "",
    ) -> OfficialLoginResult:
        worker = worker or OfficialLoginWorker()
        expected_unb = str(profile_unb or "").strip()
        if not expected_unb:
            return OfficialLoginResult(
                status="failed",
                error_code="account_identity_missing",
                message="账号缺少真实 unb，无法定位官方浏览器档案",
            )

        profile_path = self.profile_path(expected_unb)
        with self._lock_for(f"profile:{expected_unb}"):
            return self._run_profile(
                mode="refresh",
                profile_path=profile_path,
                account=account,
                password=password,
                show_browser=show_browser,
                current_cookie=current_cookie,
                expected_unb=expected_unb,
                allow_password=allow_password,
                worker=worker,
                on_status=on_status,
                on_validated=on_validated,
                initial_verification_url=initial_verification_url,
            )

    def _run_profile(
        self,
        *,
        mode: str,
        profile_path: Path,
        account: str,
        password: str,
        show_browser: bool,
        current_cookie: str,
        expected_unb: str,
        allow_password: bool,
        worker: OfficialLoginWorker,
        on_status: Optional[Callable[[OfficialLoginResult], None]],
        on_validated: Optional[Callable[[OfficialLoginResult], Any]],
        initial_verification_url: str = "",
        login_wait_timeout: Optional[float] = None,
    ) -> OfficialLoginResult:
        profile_path.mkdir(parents=True, exist_ok=True)
        background_window = not show_browser
        used_password = False
        verification_image_path = ""
        active_verification_url = (
            initial_verification_url
            if is_allowed_verification_url(initial_verification_url)
            else ""
        )

        for launch_number in range(2):
            if worker.cancel_event.is_set():
                return self._cancelled_result(used_password)

            playwright = None
            context = None
            try:
                browser_args = ["--lang=zh-CN", "--password-store=basic"]
                if background_window:
                    browser_args.extend([
                        "--window-position=-32000,-32000",
                        "--window-size=1440,960",
                    ])
                playwright = self.playwright_factory().start()
                context = playwright.chromium.launch_persistent_context(
                    str(profile_path),
                    # Goofish currently rejects Chromium headless mode as an
                    # illegal browser. Background renewals use a normal browser
                    # window positioned off-screen instead.
                    headless=False,
                    channel=os.getenv("XIANYU_BROWSER_CHANNEL", "chrome"),
                    chromium_sandbox=True,
                    args=browser_args,
                    viewport={"width": 1440, "height": 960},
                    locale="zh-CN",
                    accept_downloads=False,
                )
                worker.attach(context, playwright)
                self._seed_cookie_if_needed(context, current_cookie)

                pages = list(getattr(context, "pages", []) or [])
                page = pages[0] if pages else context.new_page()
                self._install_official_message_listener(page)
                entry_url = (
                    active_verification_url
                    or (GOOFISH_LOGIN_URL if mode in {"qr", "password", "sms"} else GOOFISH_IM_URL)
                )
                page.goto(entry_url, wait_until="domcontentloaded", timeout=60000)
                try:
                    page.wait_for_timeout(1500)
                except Exception:
                    worker.cancel_event.wait(1.0)

                browser_user_agent = self._browser_user_agent(page)
                immediate = self._successful_result(
                    page,
                    context,
                    expected_unb,
                    used_password,
                    browser_user_agent,
                )
                manual_status_emitted = False
                if immediate and immediate.status in {"success", "failed"}:
                    return self._apply_validated_hook(immediate, on_validated)
                if immediate and immediate.requires_manual_action:
                    if (
                        immediate.verification_url
                        and immediate.verification_url != active_verification_url
                    ):
                        active_verification_url = immediate.verification_url
                        page.goto(
                            active_verification_url,
                            wait_until="domcontentloaded",
                            timeout=60000,
                        )
                    verification_image_path = self._save_verification_screenshot(
                        page,
                        expected_unb or account or "verify",
                    )
                    immediate.verification_image_path = verification_image_path
                    immediate.browser_user_agent = browser_user_agent
                    self._emit_status(on_status, immediate)
                    manual_status_emitted = True

                if self._has_active_login_form(page):
                    if mode == "sms" and account:
                        account_input = self._find_visible(page, self.ACCOUNT_INPUT_SELECTORS)
                        if account_input is not None:
                            try:
                                account_input.fill(account)
                            except Exception:
                                pass
                    if mode == "qr":
                        verification_image_path = self._save_login_screenshot(page, "qr")
                        self._emit_status(
                            on_status,
                            OfficialLoginResult(
                                status="waiting_user",
                                message="请使用闲鱼 App 扫码，或在本机官方窗口完成登录",
                                verification_image_path=verification_image_path,
                                requires_manual_action=True,
                            ),
                        )
                    elif mode == "sms" or (mode == "refresh" and not allow_password):
                        verification_image_path = self._save_login_screenshot(
                            page,
                            expected_unb or "reauth",
                        )
                        status = OfficialLoginResult(
                            status="verification_required",
                            error_code="reauth_required",
                            message=(
                                "请在官方窗口完成手机号验证码登录"
                                if mode == "sms"
                                else "官方登录态已失效，请手动重新登录"
                            ),
                            verification_image_path=verification_image_path,
                            requires_manual_action=True,
                            used_password=False,
                        )
                        self._emit_status(on_status, status)
                    if (
                        mode == "password"
                        or (mode == "refresh" and allow_password)
                    ) and (not account or not password):
                        return OfficialLoginResult(
                            status="failed",
                            error_code="no_credentials",
                            message="账号密码登录参数不完整",
                            used_password=used_password,
                        )
                    if mode == "password" or (mode == "refresh" and allow_password):
                        submit_result = self._submit_password_login(page, account, password, worker)
                        if submit_result is not None:
                            submit_result.used_password = used_password
                            return submit_result
                        used_password = True

                monitor_result = self._monitor_login(
                    page=page,
                    context=context,
                    expected_unb=expected_unb,
                    profile_key=expected_unb or account,
                    background_window=background_window,
                    used_password=used_password,
                    worker=worker,
                    on_status=on_status,
                    verification_image_path=verification_image_path,
                    wait_for_login_form=mode in {"qr", "refresh", "sms"},
                    browser_user_agent=browser_user_agent,
                    verification_status_emitted=manual_status_emitted,
                    active_verification_url=active_verification_url,
                    login_wait_timeout=login_wait_timeout,
                )
                if monitor_result.error_code != "reopen_visible":
                    return self._apply_validated_hook(monitor_result, on_validated)

                if launch_number == 0:
                    verification_image_path = monitor_result.verification_image_path
                    background_window = False
                    continue
                return OfficialLoginResult(
                    status="failed",
                    error_code="verification_browser_failed",
                    message="无法保持可见浏览器等待身份验证",
                    used_password=used_password,
                )
            except Exception as exc:
                if worker.cancel_event.is_set():
                    return self._cancelled_result(used_password)
                exception_text = str(exc)
                error_code = (
                    "profile_in_use"
                    if "ProcessSingleton" in exception_text
                    or "profile" in exception_text.lower()
                    else "browser_error"
                )
                logger.error(f"闲鱼官方浏览器会话执行失败: {type(exc).__name__}")
                return OfficialLoginResult(
                    status="failed",
                    error_code=error_code,
                    message=(
                        "闲鱼官方浏览器档案正在使用，请关闭对应窗口后重试"
                        if error_code == "profile_in_use"
                        else "闲鱼官方浏览器启动失败，请稍后重试"
                    ),
                    used_password=used_password,
                )
            finally:
                worker.detach(context)
                if context is not None:
                    try:
                        context.close()
                    except Exception:
                        pass
                if playwright is not None:
                    try:
                        playwright.stop()
                    except Exception:
                        pass

        return OfficialLoginResult(
            status="failed",
            error_code="browser_error",
            message="官方浏览器会话未能完成",
            used_password=used_password,
        )

    def _submit_password_login(
        self,
        page: Any,
        account: str,
        password: str,
        worker: OfficialLoginWorker,
    ) -> Optional[OfficialLoginResult]:
        surface = self._find_login_surface(page)
        if surface is None:
            return OfficialLoginResult(
                status="failed",
                error_code="login_form_missing",
                message="未找到闲鱼官方登录框",
            )

        password_input = self._find_visible(surface, self.PASSWORD_INPUT_SELECTORS)
        if password_input is None:
            password_tab = self._find_visible(surface, self.PASSWORD_TAB_SELECTORS)
            if password_tab is None:
                password_tab = self._find_text(surface, ("密码登录",))
            if password_tab is None:
                return OfficialLoginResult(
                    status="failed",
                    error_code="password_mode_missing",
                    message="官方登录页未提供密码登录入口",
                )
            password_tab.click()
            worker.cancel_event.wait(0.3)

        account_input = self._find_visible(surface, self.ACCOUNT_INPUT_SELECTORS)
        password_input = self._find_visible(surface, self.PASSWORD_INPUT_SELECTORS)
        if account_input is None or password_input is None:
            return OfficialLoginResult(
                status="failed",
                error_code="credential_inputs_missing",
                message="未找到闲鱼官方账号或密码输入框",
            )

        account_input.fill(account)
        password_input.fill(password)
        agreement = self._find_visible(surface, self.AGREEMENT_SELECTORS)
        if agreement is None:
            agreement = self._find_text(surface, ("已阅读并同意", "同意服务协议", "同意"))
        if agreement is None:
            return OfficialLoginResult(
                status="failed",
                error_code="agreement_missing",
                message="未找到闲鱼官方用户协议勾选项",
            )
        try:
            checked = bool(agreement.is_checked())
        except Exception:
            checked = False
        if not checked:
            agreement.click()

        # Depending on the official page version this control can appear either
        # beside the password form or on the confirmation step.
        self._confirm_keep_login(page)

        login_button = self._find_visible(surface, self.LOGIN_BUTTON_SELECTORS)
        if login_button is None:
            return OfficialLoginResult(
                status="failed",
                error_code="login_submit_missing",
                message="未找到闲鱼官方密码登录按钮",
            )
        login_button.click()
        return None

    def _monitor_login(
        self,
        *,
        page: Any,
        context: Any,
        expected_unb: str,
        profile_key: str,
        background_window: bool,
        used_password: bool,
        worker: OfficialLoginWorker,
        on_status: Optional[Callable[[OfficialLoginResult], None]],
        verification_image_path: str,
        wait_for_login_form: bool = False,
        browser_user_agent: str = "",
        verification_status_emitted: bool = False,
        active_verification_url: str = "",
        login_wait_timeout: Optional[float] = None,
    ) -> OfficialLoginResult:
        wait_seconds = (
            float(login_wait_timeout)
            if login_wait_timeout is not None
            else (self.verification_timeout if wait_for_login_form else self.login_timeout)
        )
        login_deadline = time.monotonic() + max(0.001, wait_seconds)
        verification_deadline: Optional[float] = None
        keep_login_confirmed = False
        official_result_observed = False
        probe_manual_action_active = bool(verification_status_emitted)
        last_probe_at = 0.0

        while True:
            if worker.cancel_event.is_set():
                return self._cancelled_result(used_password)
            if background_window and worker.show_event.is_set():
                worker.show_event.clear()
                if self._show_existing_window(context, page):
                    background_window = False
                else:
                    self._emit_status(
                        on_status,
                        OfficialLoginResult(
                            status=(
                                "verification_required"
                                if self._has_security_verification(page)
                                else "waiting_user"
                            ),
                            error_code="show_browser_failed",
                            message="同一官方窗口暂未移到桌面，后台检测仍在继续",
                            verification_image_path=verification_image_path,
                            requires_manual_action=True,
                            used_password=used_password,
                            browser_user_agent=browser_user_agent,
                        ),
                    )

            if not keep_login_confirmed and self._confirm_keep_login(page):
                keep_login_confirmed = True
                worker.cancel_event.wait(0.3)

            security_verification_active = self._has_security_verification(page)
            if (
                not official_result_observed
                and not security_verification_active
                and self._official_login_result_received(page)
            ):
                official_result_observed = True
                self._emit_status(
                    on_status,
                    OfficialLoginResult(
                        status="waiting_user",
                        message="已收到官方登录结果，正在确认会话 Cookie",
                        verification_image_path=verification_image_path,
                        requires_manual_action=True,
                        used_password=used_password,
                    ),
                )

            inspection = None
            now = time.monotonic()
            if now - last_probe_at >= self.probe_interval:
                inspection = self._successful_result(
                    page,
                    context,
                    expected_unb,
                    used_password,
                    browser_user_agent,
                )
                last_probe_at = now
            if inspection and inspection.status in {"success", "failed"}:
                return inspection
            if inspection and inspection.requires_manual_action:
                probe_manual_action_active = True
                if (
                    inspection.verification_url
                    and inspection.verification_url != active_verification_url
                ):
                    active_verification_url = inspection.verification_url
                    page.goto(
                        active_verification_url,
                        wait_until="domcontentloaded",
                        timeout=60000,
                    )
                    security_verification_active = True
                if not verification_status_emitted:
                    latest_image_path = self._save_verification_screenshot(page, profile_key)
                    if latest_image_path:
                        verification_image_path = latest_image_path
                    inspection.verification_image_path = verification_image_path
                    inspection.browser_user_agent = browser_user_agent
                    self._emit_status(on_status, inspection)
                    verification_status_emitted = True

            login_error = self._detect_login_error(page)
            if login_error:
                return OfficialLoginResult(
                    status="failed",
                    error_code="invalid_credentials",
                    message=login_error,
                    used_password=used_password,
                )

            if security_verification_active or probe_manual_action_active:
                if not verification_status_emitted:
                    latest_image_path = self._save_verification_screenshot(page, profile_key)
                    if latest_image_path:
                        verification_image_path = latest_image_path
                    status = OfficialLoginResult(
                        status="verification_required",
                        error_code="verification_required",
                        message="需要完成闲鱼身份验证，验证后系统会自动继续",
                        verification_image_path=verification_image_path,
                        requires_manual_action=True,
                        used_password=used_password,
                        browser_user_agent=browser_user_agent,
                    )
                    self._emit_status(on_status, status)
                    verification_status_emitted = True
                if verification_deadline is None:
                    verification_deadline = time.monotonic() + max(0.001, wait_seconds)
                if time.monotonic() >= verification_deadline:
                    return OfficialLoginResult(
                        status="timeout",
                        error_code="verification_timeout",
                        message="身份验证等待超时",
                        verification_image_path=verification_image_path,
                        requires_manual_action=True,
                        used_password=used_password,
                    )
            elif time.monotonic() >= login_deadline:
                return OfficialLoginResult(
                    status="timeout" if wait_for_login_form else "failed",
                    error_code="login_timeout" if wait_for_login_form else "login_state_unknown",
                    message="官方登录会话已过期" if wait_for_login_form else "官方登录页未能确认登录成功",
                    verification_image_path=verification_image_path,
                    requires_manual_action=wait_for_login_form,
                    used_password=used_password,
                )

            worker.cancel_event.wait(self.poll_interval)

    @staticmethod
    def _show_existing_window(context: Any, page: Any) -> bool:
        new_cdp_session = getattr(context, "new_cdp_session", None)
        if not callable(new_cdp_session):
            return False
        session = None
        try:
            session = new_cdp_session(page)
            window = session.send("Browser.getWindowForTarget")
            window_id = window.get("windowId")
            if window_id is None:
                return False
            session.send(
                "Browser.setWindowBounds",
                {
                    "windowId": window_id,
                    "bounds": {
                        "left": 80,
                        "top": 80,
                        "width": 1440,
                        "height": 960,
                        "windowState": "normal",
                    },
                },
            )
            return True
        except Exception:
            return False
        finally:
            if session is not None:
                try:
                    session.detach()
                except Exception:
                    pass

    def _successful_result(
        self,
        page: Any,
        context: Any,
        expected_unb: str,
        used_password: bool,
        browser_user_agent: str,
    ) -> Optional[OfficialLoginResult]:
        cookies = self._collect_relevant_cookies(context)
        unb = str(cookies.get("unb") or "").strip()
        if not self._has_authenticated_cookies(cookies):
            return None
        if self._has_active_login_form(page) or self._has_security_verification(page) or self._has_keep_login_prompt(page):
            return None
        if expected_unb and unb != expected_unb:
            return OfficialLoginResult(
                status="failed",
                cookies=cookies,
                unb=unb,
                error_code="account_mismatch",
                message="官方浏览器档案登录了其他闲鱼账号，已拒绝覆盖当前账号",
                used_password=used_password,
                browser_user_agent=browser_user_agent,
            )
        if self.session_validator is not None:
            probe = self.session_validator(
                self.cookies_to_string(cookies),
                browser_user_agent,
            )
            probe_unb = str(probe.cookies.get("unb") or unb).strip()
            if expected_unb and probe_unb and probe_unb != expected_unb:
                return OfficialLoginResult(
                    status="failed",
                    cookies=probe.cookies,
                    unb=probe_unb,
                    error_code="account_mismatch",
                    message="消息会话返回了其他闲鱼账号，已拒绝覆盖当前账号",
                    used_password=used_password,
                    browser_user_agent=browser_user_agent,
                )
            if probe.succeeded:
                return OfficialLoginResult(
                    status="success",
                    cookies=probe.cookies,
                    unb=probe_unb,
                    message="闲鱼官方登录态和消息 Token 已验证",
                    used_password=used_password,
                    browser_user_agent=browser_user_agent,
                    access_token=probe.access_token,
                )

            if probe.status == PROBE_VERIFICATION_REQUIRED:
                message = "需要完成闲鱼身份验证，后台会持续检测"
            elif probe.status == PROBE_EXPIRED:
                message = "官方登录态已过期，请在同一窗口重新登录"
            elif probe.status == PROBE_RETRYABLE_ERROR:
                message = "消息 Token 尚未验证成功，请在同一官方窗口确认登录状态"
            else:
                message = probe.message or "消息 Token 尚未验证成功"
            return OfficialLoginResult(
                status="verification_required",
                cookies=probe.cookies or cookies,
                unb=probe_unb,
                error_code=probe.error_code or "message_token_unverified",
                message=message,
                verification_url=probe.verification_url,
                requires_manual_action=True,
                used_password=used_password,
                browser_user_agent=browser_user_agent,
            )
        return OfficialLoginResult(
            status="success",
            cookies=cookies,
            unb=unb,
            message="闲鱼官方登录态已更新",
            used_password=used_password,
            browser_user_agent=browser_user_agent,
        )

    @staticmethod
    def _browser_user_agent(page: Any) -> str:
        evaluate = getattr(page, "evaluate", None)
        if callable(evaluate):
            try:
                value = str(evaluate("navigator.userAgent") or "").strip()
                if value:
                    return value
            except Exception:
                pass
        return detect_default_browser_user_agent()

    @staticmethod
    def _apply_validated_hook(
        result: OfficialLoginResult,
        callback: Optional[Callable[[OfficialLoginResult], Any]],
    ) -> OfficialLoginResult:
        """Finish persistence/listener handoff while the validated browser stays open."""
        if not result.succeeded or callback is None:
            return result
        try:
            outcome = callback(result)
            if inspect.isawaitable(outcome):
                raise TypeError("validated callback must be synchronous")
            if outcome is False:
                raise RuntimeError("validated handoff rejected")
            return result
        except Exception as exc:
            logger.error(f"闲鱼登录态交接失败: {type(exc).__name__}")
            return OfficialLoginResult(
                status="failed",
                error_code="validated_handoff_failed",
                message="消息 Token 已验证，但保存或监听交接失败",
                used_password=result.used_password,
                browser_user_agent=result.browser_user_agent,
            )

    def _seed_cookie_if_needed(self, context: Any, current_cookie: str) -> None:
        existing = self._collect_relevant_cookies(context)
        if self._has_authenticated_cookies(existing):
            return
        parsed = self.parse_cookie_string(current_cookie)
        if not parsed:
            return

        seed_cookies = []
        for url in COOKIE_URLS:
            for name, value in parsed.items():
                seed_cookies.append({"name": name, "value": value, "url": url})
        try:
            context.add_cookies(seed_cookies)
        except Exception as exc:
            logger.warning(f"向官方浏览器档案写入现有 Cookie 失败: {type(exc).__name__}")

    def _collect_relevant_cookies(self, context: Any) -> dict[str, str]:
        try:
            raw_cookies = context.cookies(list(COOKIE_URLS))
        except TypeError:
            raw_cookies = context.cookies()
        except Exception:
            return {}

        now = time.time()
        selected: dict[str, tuple[int, str]] = {}
        for cookie in raw_cookies or []:
            name = str(cookie.get("name") or "").strip()
            value = str(cookie.get("value") or "")
            if not name or not value:
                continue
            expires = cookie.get("expires")
            if expires not in (None, -1, 0):
                try:
                    if float(expires) <= now:
                        continue
                except (TypeError, ValueError):
                    pass
            domain = str(cookie.get("domain") or "")
            if domain and not (
                domain.endswith("goofish.com")
                or domain.endswith("taobao.com")
            ):
                continue
            score = 3 if domain.endswith("goofish.com") else 2 if domain.endswith("taobao.com") else 1
            if name not in selected or score >= selected[name][0]:
                selected[name] = (score, value)
        return {name: value for name, (_, value) in selected.items()}

    @staticmethod
    def _has_authenticated_cookies(cookies: dict[str, str]) -> bool:
        return bool(cookies.get("unb")) and any(cookies.get(name) for name in SESSION_COOKIE_NAMES)

    def _find_login_surface(self, page: Any) -> Any:
        for surface in self._surfaces(page):
            if self._find_visible(surface, self.LOGIN_FORM_SELECTORS) is not None:
                return surface
        return None

    def _has_active_login_form(self, page: Any) -> bool:
        return self._find_login_surface(page) is not None

    def _has_security_verification(self, page: Any) -> bool:
        for surface in self._surfaces(page):
            if self._find_visible(surface, self.SECURITY_SELECTORS) is not None:
                return True
            surface_url = str(getattr(surface, "url", "") or "").lower()
            if not any(marker in surface_url for marker in self.SECURITY_URL_MARKERS):
                continue
            if surface is page:
                return True
            try:
                if surface.frame_element().is_visible():
                    return True
            except Exception:
                continue
        return False

    def _has_keep_login_prompt(self, page: Any) -> bool:
        for surface in self._surfaces(page):
            if self._find_text(surface, ("保持登录", "保持登录状态")) is not None:
                return True
        return False

    def _confirm_keep_login(self, page: Any) -> bool:
        for surface in self._surfaces(page):
            element = self._find_text(surface, ("保持登录", "保持登录状态"))
            if element is None:
                continue
            try:
                try:
                    checked = bool(element.is_checked())
                except Exception:
                    checked = False
                if not checked:
                    element.click()
                return True
            except Exception:
                continue
        return False

    def _detect_login_error(self, page: Any) -> str:
        for surface in self._surfaces(page):
            element = self._find_visible(surface, self.LOGIN_ERROR_SELECTORS)
            if element is None:
                continue
            try:
                text = str(element.inner_text() or "").strip()
            except Exception:
                text = ""
            if text:
                return text
        return ""

    @staticmethod
    def _surfaces(page: Any) -> list[Any]:
        surfaces = [page]
        try:
            for frame in page.frames:
                if frame not in surfaces:
                    surfaces.append(frame)
        except Exception:
            pass
        return surfaces

    @staticmethod
    def _find_visible(surface: Any, selectors: tuple[str, ...]) -> Any:
        for selector in selectors:
            try:
                element = surface.query_selector(selector)
                if element is not None and element.is_visible():
                    return element
            except Exception:
                continue
        return None

    @staticmethod
    def _find_text(surface: Any, texts: tuple[str, ...]) -> Any:
        get_by_text = getattr(surface, "get_by_text", None)
        if not callable(get_by_text):
            return None
        for text in texts:
            try:
                locator = get_by_text(text, exact=True)
                count = locator.count()
                if count and locator.first.is_visible():
                    return locator.first
            except Exception:
                continue
        return None

    def _save_verification_screenshot(self, page: Any, profile_key: str) -> str:
        self.verification_root.mkdir(parents=True, exist_ok=True)
        safe_key = self._safe_screenshot_key(profile_key)
        path = self.verification_root / f"xianyu_verify_{safe_key}_{int(time.time())}_{uuid.uuid4().hex[:8]}.png"
        try:
            verification_element = None
            for surface in self._surfaces(page):
                verification_element = self._find_visible(surface, self.SECURITY_SELECTORS)
                if verification_element is not None:
                    break
            if verification_element is not None and callable(
                getattr(verification_element, "screenshot", None)
            ):
                verification_element.screenshot(path=str(path))
            else:
                page.screenshot(path=str(path), full_page=False)
            return str(path)
        except Exception as exc:
            logger.warning(f"保存闲鱼身份验证截图失败: {type(exc).__name__}")
            return ""

    def _save_login_screenshot(self, page: Any, profile_key: str) -> str:
        self.verification_root.mkdir(parents=True, exist_ok=True)
        safe_key = self._safe_screenshot_key(profile_key)
        path = self.verification_root / f"xianyu_login_{safe_key}_{int(time.time())}_{uuid.uuid4().hex[:8]}.png"
        try:
            login_surface = self._find_login_surface(page)
            qr_image = self._find_visible(login_surface, self.QR_IMAGE_SELECTORS) if login_surface else None
            if qr_image is not None and callable(getattr(qr_image, "screenshot", None)):
                qr_image.screenshot(path=str(path))
            else:
                page.screenshot(path=str(path), full_page=False)
            return str(path)
        except Exception as exc:
            logger.warning(f"保存闲鱼官方登录截图失败: {type(exc).__name__}")
            return ""

    @staticmethod
    def _safe_screenshot_key(value: str) -> str:
        raw = str(value or "login").encode("utf-8")
        return hashlib.sha256(raw).hexdigest()[:12]

    @staticmethod
    def _install_official_message_listener(page: Any) -> None:
        add_init_script = getattr(page, "add_init_script", None)
        if not callable(add_init_script):
            return
        try:
            add_init_script(
                r"""
                window.__xianyuOfficialLoginEvents = [];
                window.addEventListener('message', (event) => {
                  try {
                    const hostname = new URL(event.origin).hostname;
                    if (!/(^|\.)((goo)?fish|taobao)\.com$/i.test(hostname)) return;
                    const value = typeof event.data === 'string' ? event.data : JSON.stringify(event.data || {});
                    if (value.includes('loginResult') || value.includes('hasLoginResult')) {
                      window.__xianyuOfficialLoginEvents.push(true);
                    }
                  } catch (_) {}
                });
                """
            )
        except Exception:
            return

    @staticmethod
    def _official_login_result_received(page: Any) -> bool:
        evaluate = getattr(page, "evaluate", None)
        if not callable(evaluate):
            return False
        try:
            return bool(evaluate("Boolean(window.__xianyuOfficialLoginEvents?.length)"))
        except Exception:
            return False

    def _promote_profile(self, temporary_profile: Path, unb: str) -> Path:
        target_profile = self.profile_path(unb)
        target_profile.parent.mkdir(parents=True, exist_ok=True)
        backup_profile = target_profile.with_name(f"{target_profile.name}.backup-{uuid.uuid4().hex}")

        with self._lock_for(f"profile:{unb}"):
            moved_existing = False
            try:
                if target_profile.exists():
                    os.replace(target_profile, backup_profile)
                    moved_existing = True
                os.replace(temporary_profile, target_profile)
            except Exception:
                if target_profile.exists():
                    shutil.rmtree(target_profile, ignore_errors=True)
                if moved_existing and backup_profile.exists():
                    os.replace(backup_profile, target_profile)
                raise
            else:
                if backup_profile.exists():
                    shutil.rmtree(backup_profile, ignore_errors=True)
        return target_profile

    @staticmethod
    def _emit_status(
        callback: Optional[Callable[[OfficialLoginResult], None]],
        result: OfficialLoginResult,
    ) -> None:
        if callback is None:
            return
        try:
            callback_result = callback(result)
            if inspect.isawaitable(callback_result):
                logger.warning("官方登录状态回调必须是同步函数，异步结果已忽略")
        except Exception as exc:
            logger.error(f"发送闲鱼官方登录状态失败: {type(exc).__name__}")

    @staticmethod
    def _cancelled_result(used_password: bool) -> OfficialLoginResult:
        return OfficialLoginResult(
            status="cancelled",
            error_code="cancelled",
            message="闲鱼官方登录会话已取消",
            used_password=used_password,
        )


__all__ = [
    "GOOFISH_LOGIN_URL",
    "OfficialLoginResult",
    "OfficialLoginWorker",
    "XianyuOfficialLoginService",
]
