"""扫码账号的 L3 浏览器记忆：持久 profile、免密续签、CDP 导入。

闲鱼登录态分三层。L1（`_m_h5_tk`）由 mtop Set-Cookie 续；L2（`cookie2`/`unb`）
约半天失效；L3 是浏览器 profile 里的免密记忆。扫码走纯 httpx 时只有 L1+L2，
所以本模块在扫码成功后把 Cookie 注入 `browser_data/user_<unb>`，并在 L2 失效时
打开该档案、点击 passport「快速进入」、再访问 `/bought` 拿完整会话。
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from loguru import logger

from utils.xianyu_official_login import (
    COOKIE_URLS,
    XianyuOfficialLoginService,
)
from utils.xianyu_session_probe import (
    detect_default_browser_user_agent,
    has_core_session_cookies,
    parse_cookie_string,
)


GOOFISH_HOME_URL = "https://www.goofish.com"
GOOFISH_BOUGHT_URL = "https://www.goofish.com/bought"
L3_READY_MARKER = ".l3_ready"
REQUIRED_FRESH_COOKIE_NAMES = ("_m_h5_tk", "unb", "cookie2")
DEFAULT_CDP_ENDPOINT = "http://127.0.0.1:9222"

PASSWORDLESS_RETRYABLE_ERROR_CODES = {
    "profile_missing",
    "profile_corrupt",
    "profile_in_use",
    "browser_error",
    "fast_entry_timeout",
    "session_not_renewed",
    "session_probe_retryable",
    "cdp_connect_failed",
}
# 未换新判定只看这两个字段：cookie2 是 L2 会话身份，_m_h5_tk 随访问刷新；
# 二者同时与进入浏览器前的基线完全一致，说明本次会话没有发生任何真实续签。
SESSION_RENEWAL_COOKIE_NAMES = ("cookie2", "_m_h5_tk")
PASSWORDLESS_MANUAL_REAUTH_ERROR_CODES = {
    "fast_entry_unavailable",
    "account_mismatch",
    "account_identity_missing",
    "account_identity_mismatch",
    "human_verification_required",
    "illegal_access",
    "login_state_unknown",
    "cdp_identity_mismatch",
}


@dataclass
class L3MemoryResult:
    status: str
    cookies: dict[str, str] = field(default_factory=dict)
    unb: str = ""
    error_code: str = ""
    message: str = ""
    browser_user_agent: str = ""
    has_l3_memory: bool = False

    @property
    def succeeded(self) -> bool:
        return self.status == "success" and self.has_l3_memory

    @property
    def requires_manual_reauth(self) -> bool:
        return self.status == "manual_reauth_required" or (
            self.error_code in PASSWORDLESS_MANUAL_REAUTH_ERROR_CODES
        )


def cookies_to_string(cookies: Mapping[str, str]) -> str:
    return "; ".join(
        f"{name}={value}"
        for name, value in cookies.items()
        if name and value
    )


def normalize_cookie_map(cookies: Mapping[str, str] | str | None) -> dict[str, str]:
    if isinstance(cookies, str):
        return parse_cookie_string(cookies)
    return {
        str(name).strip(): str(value)
        for name, value in dict(cookies or {}).items()
        if str(name).strip() and value
    }


def default_cdp_endpoint() -> str:
    return str(os.getenv("XIANYU_CHROME_CDP_ENDPOINT") or "").strip()


def l3_settle_seconds(explicit: Optional[float] = None) -> float:
    if explicit is not None:
        return max(0.0, float(explicit))
    raw = str(os.getenv("XIANYU_L3_SETTLE_SECONDS") or "2").strip()
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return 2.0


class L3MemoryService:
    """Persistent-profile L3 memory for QR accounts."""

    def __init__(
        self,
        profile_root: Path | str = "browser_data",
        *,
        playwright_factory: Optional[Callable[[], Any]] = None,
        official_login: Optional[XianyuOfficialLoginService] = None,
        settle_seconds: Optional[float] = None,
    ) -> None:
        self.profile_root = Path(profile_root)
        self.playwright_factory = (
            playwright_factory
            or XianyuOfficialLoginService._default_playwright_factory
        )
        self._official = official_login
        self.settle_seconds = settle_seconds

    @property
    def official(self) -> XianyuOfficialLoginService:
        if self._official is None:
            self._official = XianyuOfficialLoginService(
                profile_root=self.profile_root,
                playwright_factory=self.playwright_factory,
            )
        return self._official

    def profile_path(self, unb: str) -> Path:
        return self.official.profile_path(unb)

    def has_usable_profile(self, unb: str) -> bool:
        path = self.profile_path(unb)
        return self.profile_is_usable(path)

    @staticmethod
    def profile_is_usable(path: Path) -> bool:
        if not path.is_dir():
            return False
        try:
            next(path.iterdir())
        except StopIteration:
            return False
        except OSError:
            return False
        return True

    def mark_profile_ready(self, unb: str) -> Path:
        path = self.profile_path(unb)
        path.mkdir(parents=True, exist_ok=True)
        marker = path / L3_READY_MARKER
        marker.write_text("1", encoding="utf-8")
        return path

    def seed_profile_from_cookies(
        self,
        unb: str,
        cookies: Mapping[str, str] | str,
        *,
        settle_seconds: Optional[float] = None,
    ) -> L3MemoryResult:
        """Inject a validated QR session into `browser_data/user_<unb>`."""
        expected_unb = str(unb or "").strip()
        cookie_map = normalize_cookie_map(cookies)
        cookie_unb = str(cookie_map.get("unb") or expected_unb).strip()
        if not expected_unb:
            return self._failed(
                "account_identity_missing",
                "扫码结果缺少稳定账号身份，无法建立浏览器记忆",
                manual=True,
            )
        if cookie_unb and cookie_unb != expected_unb:
            return self._failed(
                "account_identity_mismatch",
                "扫码 Cookie 与账号身份不一致，未写入浏览器记忆",
                manual=True,
            )
        if not has_core_session_cookies(cookie_map):
            return self._failed(
                "login_state_unknown",
                "扫码 Cookie 缺少核心会话字段，未写入浏览器记忆",
                manual=True,
            )

        profile_path = self.profile_path(expected_unb)
        with XianyuOfficialLoginService._lock_for(f"profile:{expected_unb}"):
            try:
                _baseline, collected, user_agent = self._run_browser_session(
                    profile_path=profile_path,
                    cookies=cookie_map,
                    expected_unb=expected_unb,
                    require_quick_enter=False,
                    settle_seconds=settle_seconds,
                )
            except Exception as exc:
                return self._launch_failure(exc)

        return self._validated_fresh_cookies(
            collected,
            expected_unb=expected_unb,
            browser_user_agent=user_agent,
            missing_code="login_state_unknown",
            missing_message="浏览器记忆已写入，但未拿到完整会话 Cookie",
        )

    def passwordless_refresh(
        self,
        unb: str,
        current_cookie: Mapping[str, str] | str = "",
        *,
        settle_seconds: Optional[float] = None,
    ) -> L3MemoryResult:
        """Re-issue L2 session cookies from an existing L3 profile without a password."""
        expected_unb = str(unb or "").strip()
        if not expected_unb:
            return self._failed(
                "account_identity_missing",
                "账号缺少真实 unb，无法使用浏览器记忆续签",
                manual=True,
            )
        profile_path = self.profile_path(expected_unb)
        if not self.profile_is_usable(profile_path):
            return self._failed(
                "profile_missing",
                "账号尚未建立浏览器登录记忆，无法免密续签",
            )

        cookie_map = normalize_cookie_map(current_cookie)
        with XianyuOfficialLoginService._lock_for(f"profile:{expected_unb}"):
            try:
                baseline, collected, user_agent = self._run_browser_session(
                    profile_path=profile_path,
                    cookies=cookie_map,
                    expected_unb=expected_unb,
                    require_quick_enter=True,
                    settle_seconds=settle_seconds,
                )
            except _QuickEnterUnavailable:
                return self._failed(
                    "fast_entry_unavailable",
                    "浏览器免密记忆已失效，需要重新扫码",
                    manual=True,
                )
            except _QuickEnterTimeout:
                return self._failed(
                    "fast_entry_timeout",
                    "免密续签等待超时，请稍后重试",
                )
            except Exception as exc:
                return self._launch_failure(exc)

        if self._session_not_renewed(baseline, collected):
            logger.warning("免密续签收集到的会话与进入前完全一致，判定未续签")
            return self._failed(
                "session_not_renewed",
                "免密续签未取得新会话（可能是网络异常），将稍后重试",
            )

        return self._validated_fresh_cookies(
            collected,
            expected_unb=expected_unb,
            browser_user_agent=user_agent,
            missing_code="login_state_unknown",
            missing_message="免密续签未拿到完整会话 Cookie",
        )

    def import_from_cdp(
        self,
        *,
        endpoint: Optional[str] = None,
        expected_unb: str = "",
        persist_profile: bool = True,
    ) -> L3MemoryResult:
        """Read a live Chrome session over CDP and fail closed on mismatch."""
        cdp_endpoint = str(endpoint if endpoint is not None else default_cdp_endpoint()).strip()
        if not cdp_endpoint:
            return self._failed(
                "cdp_endpoint_missing",
                "未配置本机 Chrome 调试端口，无法接管真实浏览器",
            )

        playwright = None
        browser = None
        try:
            playwright = self.playwright_factory()
            started = playwright.start() if hasattr(playwright, "start") else playwright
            chromium = started.chromium
            browser = chromium.connect_over_cdp(cdp_endpoint)
            cookies = self._collect_cdp_cookies(browser)
            user_agent = self._cdp_user_agent(browser)
        except Exception as exc:
            logger.warning("CDP 接管本机 Chrome 失败: {}", type(exc).__name__)
            return self._failed(
                "cdp_connect_failed",
                "无法连接本机已开启调试端口的 Chrome",
            )
        finally:
            self._close_quietly(browser)
            self._stop_quietly(playwright)

        cookie_unb = str(cookies.get("unb") or "").strip()
        wanted_unb = str(expected_unb or "").strip()
        if wanted_unb and cookie_unb and cookie_unb != wanted_unb:
            return self._failed(
                "cdp_identity_mismatch",
                "本机 Chrome 登录账号与当前账号不一致",
                manual=True,
            )
        if not cookie_unb:
            return self._failed(
                "account_identity_missing",
                "本机 Chrome 未读到闲鱼账号身份",
                manual=True,
            )
        if not has_core_session_cookies(cookies):
            return self._failed(
                "login_state_unknown",
                "本机 Chrome 未读到完整闲鱼会话",
                manual=True,
            )

        if persist_profile:
            seeded = self.seed_profile_from_cookies(
                cookie_unb,
                cookies,
                settle_seconds=0,
            )
            if seeded.succeeded:
                return seeded
            logger.warning(
                "CDP 会话有效，但写入浏览器记忆失败: {}",
                seeded.error_code or "seed_failed",
            )

        # CDP 读到的登录态本身有效，但没有真实建档时不得虚标 L3 记忆：
        # marker-only 空档案会让后续免密续签打开一个没有登录记忆的浏览器。
        return L3MemoryResult(
            status="success",
            cookies=cookies,
            unb=cookie_unb,
            browser_user_agent=user_agent or detect_default_browser_user_agent(),
            has_l3_memory=False,
        )

    def _run_browser_session(
        self,
        *,
        profile_path: Path,
        cookies: Mapping[str, str],
        expected_unb: str,
        require_quick_enter: bool,
        settle_seconds: Optional[float],
    ) -> tuple[dict[str, str], dict[str, str], str]:
        profile_path.mkdir(parents=True, exist_ok=True)
        wait_seconds = l3_settle_seconds(
            settle_seconds if settle_seconds is not None else self.settle_seconds
        )
        playwright = None
        context = None
        try:
            playwright = self.playwright_factory()
            started = playwright.start() if hasattr(playwright, "start") else playwright
            context = started.chromium.launch_persistent_context(
                str(profile_path),
                headless=False,
                channel=os.getenv("XIANYU_BROWSER_CHANNEL") or None,
                chromium_sandbox=True,
                args=[
                    "--lang=zh-CN",
                    "--password-store=basic",
                    "--window-position=-32000,-32000",
                    "--window-size=1440,960",
                ],
                viewport={"width": 1440, "height": 960},
                locale="zh-CN",
                accept_downloads=False,
            )
            if cookies:
                self.official._seed_cookie_if_needed(
                    context,
                    cookies_to_string(cookies),
                )
            baseline = dict(self.official._collect_relevant_cookies(context) or {})
            page = context.pages[0] if getattr(context, "pages", None) else context.new_page()
            self._safe_goto(page, GOOFISH_HOME_URL)
            self._wait(page, min(1.5, wait_seconds if wait_seconds else 0.2))
            entered = self.try_quick_enter(page)
            if require_quick_enter and entered is False:
                raise _QuickEnterUnavailable()
            self._safe_goto(page, GOOFISH_BOUGHT_URL)
            self._wait(page, wait_seconds)
            collected = self.official._collect_relevant_cookies(context)
            user_agent = self.official._browser_user_agent(page)
            return baseline, collected, user_agent or detect_default_browser_user_agent()
        finally:
            self._close_quietly(context)
            self._stop_quietly(playwright)

    @staticmethod
    def _session_not_renewed(
        baseline: Mapping[str, str],
        collected: Mapping[str, str],
    ) -> bool:
        """True when every renewal-sensitive cookie is unchanged from the baseline.

        进入浏览器前收集到的 Cookie（注入的旧值 + 档案持久层）与离开时完全一致，
        说明本次没有发生任何真实续签——典型场景是断网时页面根本没加载，
        `about:blank` 无登录 iframe 又会被解读为「已在登录态」。此时旧 Cookie
        原样收回，不能当成续签结果交给监听。基线缺字段时不做该判定，交由
        完整性校验兜底。
        """
        if not baseline or not collected:
            return False
        for name in SESSION_RENEWAL_COOKIE_NAMES:
            old_value = str(baseline.get(name) or "")
            if not old_value:
                return False
            if str(collected.get(name) or "") != old_value:
                return False
        return True

    def try_quick_enter(self, page: Any) -> Optional[bool]:
        """Click passport 快速进入. True=ok/already in; False=unavailable; None=no iframe."""
        iframe_el = self._query_selector(page, "#alibaba-login-box")
        if iframe_el is None:
            return None
        frame = None
        content_frame = getattr(iframe_el, "content_frame", None)
        if callable(content_frame):
            try:
                frame = content_frame()
            except Exception:
                frame = None
        if frame is None:
            logger.debug("免密续签：登录框 iframe 未就绪")
            return False
        try:
            wait_for_load = getattr(frame, "wait_for_load_state", None)
            if callable(wait_for_load):
                wait_for_load("domcontentloaded", timeout=5000)
            self._wait(page, 0.8)
            clicked = self._click_quick_enter(frame)
            if not clicked:
                return False
            wait_for_selector = getattr(page, "wait_for_selector", None)
            if callable(wait_for_selector):
                wait_for_selector("#alibaba-login-box", state="hidden", timeout=10000)
            return True
        except Exception as exc:
            message = str(exc).lower()
            if "timeout" in message:
                raise _QuickEnterTimeout() from exc
            logger.warning("免密续签：快速进入不可用: {}", type(exc).__name__)
            return False

    @staticmethod
    def _click_quick_enter(frame: Any) -> bool:
        get_by_text = getattr(frame, "get_by_text", None)
        if callable(get_by_text):
            locator = get_by_text("快速进入", exact=True)
            first = locator.first if hasattr(locator, "first") else locator
            # 先确认按钮真的存在：iframe 已加载但没有「快速进入」正是记忆失效
            # 的形态，必须返回 False 走 fast_entry_unavailable（引导重新扫码），
            # 而不能让 click 的 TimeoutError 被外层误判成可重试的等待超时。
            wait_for = getattr(first, "wait_for", None)
            if callable(wait_for):
                try:
                    wait_for(state="visible", timeout=5000)
                except Exception:
                    return False
            first.click(timeout=5000)
            return True
        query_selector = getattr(frame, "query_selector", None)
        if callable(query_selector):
            button = query_selector("text=快速进入")
            if button is None:
                return False
            button.click(timeout=5000)
            return True
        return False

    def _validated_fresh_cookies(
        self,
        collected: Mapping[str, str],
        *,
        expected_unb: str,
        browser_user_agent: str,
        missing_code: str,
        missing_message: str,
    ) -> L3MemoryResult:
        cookies = dict(collected or {})
        cookie_unb = str(cookies.get("unb") or "").strip()
        if cookie_unb and cookie_unb != expected_unb:
            return self._failed(
                "account_mismatch",
                "浏览器记忆登录账号与当前账号不一致",
                manual=True,
            )
        missing = [name for name in REQUIRED_FRESH_COOKIE_NAMES if not cookies.get(name)]
        if missing:
            logger.warning("L3 会话缺少关键 Cookie: {}", ",".join(missing))
            return self._failed(missing_code, missing_message, manual=True)
        self.mark_profile_ready(expected_unb)
        return L3MemoryResult(
            status="success",
            cookies=cookies,
            unb=expected_unb,
            browser_user_agent=browser_user_agent,
            has_l3_memory=True,
        )

    def _launch_failure(self, exc: Exception) -> L3MemoryResult:
        text = str(exc)
        error_code = (
            "profile_in_use"
            if "ProcessSingleton" in text or "SingletonLock" in text
            else "profile_corrupt"
            if "corrupt" in text.lower() or "failed to create" in text.lower()
            else "browser_error"
        )
        logger.error("L3 浏览器会话失败: {}", type(exc).__name__)
        return self._failed(
            error_code,
            "闲鱼官方浏览器档案正在使用，请关闭对应窗口后重试"
            if error_code == "profile_in_use"
            else "浏览器登录记忆档案损坏，请稍后重试"
            if error_code == "profile_corrupt"
            else "闲鱼官方浏览器启动失败，请稍后重试",
        )

    @staticmethod
    def _failed(error_code: str, message: str, *, manual: bool = False) -> L3MemoryResult:
        return L3MemoryResult(
            status="manual_reauth_required" if manual else "failed",
            error_code=error_code,
            message=message,
            has_l3_memory=False,
        )

    @staticmethod
    def _query_selector(page: Any, selector: str) -> Any:
        query_selector = getattr(page, "query_selector", None)
        if not callable(query_selector):
            return None
        try:
            return query_selector(selector)
        except Exception:
            return None

    @staticmethod
    def _safe_goto(page: Any, url: str) -> None:
        goto = getattr(page, "goto", None)
        if not callable(goto):
            return
        try:
            goto(url, wait_until="domcontentloaded", timeout=15000)
        except Exception:
            logger.debug("L3 打开页面超时，继续等待: {}", url)

    @staticmethod
    def _wait(page: Any, seconds: float) -> None:
        if seconds <= 0:
            return
        wait_for_timeout = getattr(page, "wait_for_timeout", None)
        if callable(wait_for_timeout):
            try:
                wait_for_timeout(int(seconds * 1000))
                return
            except Exception:
                pass
        time.sleep(seconds)

    @staticmethod
    def _collect_cdp_cookies(browser: Any) -> dict[str, str]:
        selected: dict[str, str] = {}
        contexts = list(getattr(browser, "contexts", None) or [])
        for context in contexts:
            try:
                raw_cookies = context.cookies(list(COOKIE_URLS))
            except TypeError:
                raw_cookies = context.cookies()
            except Exception:
                continue
            for cookie in raw_cookies or []:
                name = str(cookie.get("name") or "").strip()
                value = str(cookie.get("value") or "")
                domain = str(cookie.get("domain") or "")
                if not name or not value:
                    continue
                if domain and not (
                    domain.endswith("goofish.com") or domain.endswith("taobao.com")
                ):
                    continue
                selected[name] = value
        return selected

    @staticmethod
    def _cdp_user_agent(browser: Any) -> str:
        for context in list(getattr(browser, "contexts", None) or []):
            for page in list(getattr(context, "pages", None) or []):
                evaluate = getattr(page, "evaluate", None)
                if not callable(evaluate):
                    continue
                try:
                    value = str(evaluate("navigator.userAgent") or "").strip()
                except Exception:
                    continue
                if value:
                    return value
        return detect_default_browser_user_agent()

    @staticmethod
    def _close_quietly(resource: Any) -> None:
        if resource is None:
            return
        closer = getattr(resource, "close", None)
        if callable(closer):
            try:
                closer()
            except Exception:
                return

    @staticmethod
    def _stop_quietly(playwright: Any) -> None:
        if playwright is None:
            return
        for name in ("stop", "__exit__"):
            method = getattr(playwright, name, None)
            if callable(method):
                try:
                    if name == "__exit__":
                        method(None, None, None)
                    else:
                        method()
                except Exception:
                    return
                return


class _QuickEnterUnavailable(RuntimeError):
    """Passport iframe was present but 快速进入 could not complete."""


class _QuickEnterTimeout(RuntimeError):
    """Waiting for 快速进入 exceeded the bounded timeout."""


l3_memory_service = L3MemoryService()


def seed_profile_from_cookies(
    unb: str,
    cookies: Mapping[str, str] | str,
    *,
    settle_seconds: Optional[float] = None,
) -> L3MemoryResult:
    return l3_memory_service.seed_profile_from_cookies(
        unb,
        cookies,
        settle_seconds=settle_seconds,
    )


def passwordless_refresh(
    unb: str,
    current_cookie: Mapping[str, str] | str = "",
    *,
    settle_seconds: Optional[float] = None,
) -> L3MemoryResult:
    return l3_memory_service.passwordless_refresh(
        unb,
        current_cookie,
        settle_seconds=settle_seconds,
    )


def import_from_cdp(
    *,
    endpoint: Optional[str] = None,
    expected_unb: str = "",
    persist_profile: bool = True,
) -> L3MemoryResult:
    return l3_memory_service.import_from_cdp(
        endpoint=endpoint,
        expected_unb=expected_unb,
        persist_profile=persist_profile,
    )


__all__ = [
    "GOOFISH_BOUGHT_URL",
    "GOOFISH_HOME_URL",
    "L3MemoryResult",
    "L3MemoryService",
    "PASSWORDLESS_MANUAL_REAUTH_ERROR_CODES",
    "PASSWORDLESS_RETRYABLE_ERROR_CODES",
    "SESSION_RENEWAL_COOKIE_NAMES",
    "cookies_to_string",
    "default_cdp_endpoint",
    "import_from_cdp",
    "l3_memory_service",
    "passwordless_refresh",
    "seed_profile_from_cookies",
]
