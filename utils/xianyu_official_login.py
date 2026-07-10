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


GOOFISH_IM_URL = "https://www.goofish.com/im"
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
    requires_manual_action: bool = False
    used_password: bool = False

    @property
    def succeeded(self) -> bool:
        return self.status == "success"


class OfficialLoginWorker:
    """Thread-safe cancellation handle for a single Playwright login session."""

    def __init__(self) -> None:
        self.cancel_event = threading.Event()
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


class XianyuOfficialLoginService:
    LOGIN_FORM_SELECTORS = (
        "#fm-login-id",
        "input[name='fm-login-id']",
        "#fm-login-password",
        "input[name='fm-login-password']",
        "a.password-login-tab-item",
        ".password-login-tab-item",
        "button.password-login",
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
    ) -> None:
        self.profile_root = Path(profile_root)
        self.verification_root = Path(verification_root)
        self.playwright_factory = playwright_factory or self._default_playwright_factory
        self.verification_timeout = verification_timeout
        self.login_timeout = login_timeout
        self.poll_interval = poll_interval

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
    ) -> OfficialLoginResult:
        worker = worker or OfficialLoginWorker()
        self.profile_root.mkdir(parents=True, exist_ok=True)
        temporary_profile = self.profile_root / f".login_{uuid.uuid4().hex}"
        lock_key = f"login:{account.strip().lower()}"

        with self._lock_for(lock_key):
            try:
                result = self._run_profile(
                    profile_path=temporary_profile,
                    account=account,
                    password=password,
                    show_browser=show_browser,
                    current_cookie="",
                    expected_unb="",
                    allow_password=True,
                    worker=worker,
                    on_status=on_status,
                )
                if not result.succeeded:
                    return result

                try:
                    self._promote_profile(temporary_profile, result.unb)
                except Exception as exc:
                    logger.exception("归档闲鱼官方浏览器档案失败")
                    return OfficialLoginResult(
                        status="failed",
                        error_code="profile_promotion_failed",
                        message=f"登录成功，但浏览器档案归档失败: {exc}",
                        used_password=result.used_password,
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
        allow_password: bool = True,
        worker: Optional[OfficialLoginWorker] = None,
        on_status: Optional[Callable[[OfficialLoginResult], None]] = None,
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
                profile_path=profile_path,
                account=account,
                password=password,
                show_browser=show_browser,
                current_cookie=current_cookie,
                expected_unb=expected_unb,
                allow_password=allow_password,
                worker=worker,
                on_status=on_status,
            )

    def _run_profile(
        self,
        *,
        profile_path: Path,
        account: str,
        password: str,
        show_browser: bool,
        current_cookie: str,
        expected_unb: str,
        allow_password: bool,
        worker: OfficialLoginWorker,
        on_status: Optional[Callable[[OfficialLoginResult], None]],
    ) -> OfficialLoginResult:
        profile_path.mkdir(parents=True, exist_ok=True)
        background_window = not show_browser
        used_password = False
        verification_image_path = ""

        for launch_number in range(2):
            if worker.cancel_event.is_set():
                return self._cancelled_result(used_password)

            playwright = None
            context = None
            try:
                browser_args = [
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                    "--lang=zh-CN",
                    "--password-store=basic",
                ]
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
                    args=browser_args,
                    viewport={"width": 1440, "height": 960},
                    locale="zh-CN",
                    accept_downloads=False,
                    ignore_https_errors=True,
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/138.0.0.0 Safari/537.36"
                    ),
                    extra_http_headers={"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
                )
                worker.attach(context, playwright)
                self._seed_cookie_if_needed(context, current_cookie)

                pages = list(getattr(context, "pages", []) or [])
                page = pages[0] if pages else context.new_page()
                page.goto(GOOFISH_IM_URL, wait_until="domcontentloaded", timeout=60000)
                try:
                    page.wait_for_timeout(1500)
                except Exception:
                    worker.cancel_event.wait(1.0)

                immediate = self._successful_result(page, context, expected_unb, used_password)
                if immediate:
                    return immediate

                if self._has_active_login_form(page):
                    if not allow_password:
                        return OfficialLoginResult(
                            status="failed",
                            error_code="cooldown",
                            message="官方档案需要重新登录，请稍后再试",
                            used_password=used_password,
                        )
                    if not account or not password:
                        return OfficialLoginResult(
                            status="failed",
                            error_code="no_credentials",
                            message="官方登录态已失效，且未保存闲鱼账号密码",
                            used_password=used_password,
                        )
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
                )
                if monitor_result.error_code != "reopen_visible":
                    return monitor_result

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
                message = str(exc)
                error_code = "profile_in_use" if "ProcessSingleton" in message or "profile" in message.lower() else "browser_error"
                logger.exception("闲鱼官方浏览器会话执行失败")
                return OfficialLoginResult(
                    status="failed",
                    error_code=error_code,
                    message=f"官方浏览器会话失败: {message}",
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
    ) -> OfficialLoginResult:
        login_deadline = time.monotonic() + self.login_timeout
        verification_deadline: Optional[float] = None
        keep_login_confirmed = False

        while True:
            if worker.cancel_event.is_set():
                return self._cancelled_result(used_password)

            if not keep_login_confirmed and self._confirm_keep_login(page):
                keep_login_confirmed = True
                worker.cancel_event.wait(0.3)

            success = self._successful_result(page, context, expected_unb, used_password)
            if success:
                return success

            login_error = self._detect_login_error(page)
            if login_error:
                return OfficialLoginResult(
                    status="failed",
                    error_code="invalid_credentials",
                    message=login_error,
                    used_password=used_password,
                )

            if self._has_security_verification(page):
                if not verification_image_path:
                    verification_image_path = self._save_verification_screenshot(page, profile_key)
                    status = OfficialLoginResult(
                        status="verification_required",
                        error_code="verification_required",
                        message="需要完成闲鱼身份验证，验证后系统会自动继续",
                        verification_image_path=verification_image_path,
                        requires_manual_action=True,
                        used_password=used_password,
                    )
                    self._emit_status(on_status, status)
                if background_window:
                    return OfficialLoginResult(
                        status="verification_required",
                        error_code="reopen_visible",
                        message="正在切换到可见浏览器等待身份验证",
                        verification_image_path=verification_image_path,
                        requires_manual_action=True,
                        used_password=used_password,
                    )
                if verification_deadline is None:
                    verification_deadline = time.monotonic() + self.verification_timeout
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
                    status="failed",
                    error_code="login_state_unknown",
                    message="官方登录页未能确认登录成功",
                    used_password=used_password,
                )

            worker.cancel_event.wait(self.poll_interval)

    def _successful_result(
        self,
        page: Any,
        context: Any,
        expected_unb: str,
        used_password: bool,
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
            )
        return OfficialLoginResult(
            status="success",
            cookies=cookies,
            unb=unb,
            message="闲鱼官方登录态已更新",
            used_password=used_password,
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
            logger.warning(f"向官方浏览器档案写入现有 Cookie 失败，将继续尝试官方页面: {exc}")

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
        try:
            safe_key = self._safe_profile_key(profile_key or "login")
        except ValueError:
            safe_key = "login"
        path = self.verification_root / f"xianyu_verify_{safe_key}_{int(time.time())}_{uuid.uuid4().hex[:8]}.png"
        try:
            page.screenshot(path=str(path), full_page=True)
            return str(path)
        except Exception as exc:
            logger.warning(f"保存闲鱼身份验证截图失败: {exc}")
            return ""

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
        except Exception:
            logger.exception("发送闲鱼官方登录状态失败")

    @staticmethod
    def _cancelled_result(used_password: bool) -> OfficialLoginResult:
        return OfficialLoginResult(
            status="cancelled",
            error_code="cancelled",
            message="闲鱼官方登录会话已取消",
            used_password=used_password,
        )


__all__ = [
    "OfficialLoginResult",
    "OfficialLoginWorker",
    "XianyuOfficialLoginService",
]
