"""L3 浏览器记忆：扫码落档、免密续签与 CDP 失败关闭。"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from utils.qr_login import QRLoginManager, QRLoginSession
from utils.xianyu_l3_memory import (
    L3MemoryService,
    PASSWORDLESS_MANUAL_REAUTH_ERROR_CODES,
    PASSWORDLESS_RETRYABLE_ERROR_CODES,
)
from utils.xianyu_session_probe import (
    PROBE_EXPIRED,
    PROBE_VERIFICATION_REQUIRED,
    SessionProbeResult,
    classify_probe_response,
)


class _FakeLocator:
    def __init__(self, owner, *, present=True):
        self.owner = owner
        self.present = present
        self.first = self

    def wait_for(self, state="visible", timeout=5000):
        del state, timeout
        if not self.present:
            raise RuntimeError("Timeout 5000ms exceeded waiting for locator")

    def click(self, timeout=5000):
        del timeout
        if not self.present:
            raise RuntimeError("Timeout 5000ms exceeded during click")
        self.owner.clicked = True


class _FakeFrame:
    def __init__(self, *, clickable=True, button_present=None):
        self.clickable = clickable
        # button_present=False 模拟真实 Playwright：get_by_text 正常返回 locator，
        # 但按钮不在 DOM 里，wait_for/click 都会超时。
        self.button_present = clickable if button_present is None else button_present
        self.clicked = False

    def wait_for_load_state(self, *_args, **_kwargs):
        return None

    def get_by_text(self, text, exact=True):
        del exact
        if text != "快速进入" or not self.clickable:
            raise RuntimeError("locator not found")
        return _FakeLocator(self, present=self.button_present)


class _FakeIframe:
    def __init__(self, frame):
        self._frame = frame

    def content_frame(self):
        return self._frame


class _FakePage:
    def __init__(self, *, iframe=None, user_agent="FakeUA"):
        self.iframe = iframe
        self.user_agent = user_agent
        self.goto_calls = []
        self.wait_calls = []

    def goto(self, url, **_kwargs):
        self.goto_calls.append(url)

    def wait_for_timeout(self, milliseconds):
        self.wait_calls.append(milliseconds)

    def query_selector(self, selector):
        if selector == "#alibaba-login-box":
            return self.iframe
        return None

    def wait_for_selector(self, selector, **_kwargs):
        del selector
        return None

    def evaluate(self, _script):
        return self.user_agent


class _FakeContext:
    def __init__(self, page, cookies, *, baseline=None):
        self.pages = [page]
        self.fresh = dict(cookies)
        # 进入浏览器时（goto 之前）收集到的基线 Cookie；默认与 fresh 相同，
        # 模拟「浏览器没有换发任何新会话」的场景。
        self.baseline = dict(baseline) if baseline is not None else dict(cookies)
        self.collect_count = 0
        self.seeded = ""
        self.closed = False
        self.init_scripts = []

    def add_init_script(self, script):
        self.init_scripts.append(script)

    def add_cookies(self, _cookies):
        return None

    def cookies(self, *_args, **_kwargs):
        return [
            {"name": name, "value": value, "domain": ".goofish.com"}
            for name, value in self.fresh.items()
        ]

    def new_page(self):
        page = _FakePage()
        self.pages.append(page)
        return page

    def close(self):
        self.closed = True


class _VerifiableContext(_FakeContext):
    """支持建档就地验证的上下文：可按名删 Cookie，删除后登录框才会弹出。"""

    def __init__(self, page, cookies, *, login_iframe=None, baseline=None):
        super().__init__(page, cookies, baseline=baseline)
        self._page = page
        self._login_iframe = login_iframe
        self.cleared_names = []
        self.restored = False

    def clear_cookies(self, *, name=None, domain=None, path=None):
        del domain, path
        if name:
            self.cleared_names.append(name)
        # 会话 Cookie 被删后，下次访问首页登录框（若有）就会出现
        self._page.iframe = self._login_iframe

    def add_cookies(self, cookies):
        # 建档验证失败/无法验证时会用备份还原会话
        if cookies:
            self.restored = True
        return None


class _FakeChromium:
    def __init__(
        self,
        context,
        *,
        fail_connect=False,
        connect_cookies=None,
        fail_launch_message="",
    ):
        self.context = context
        self.fail_connect = fail_connect
        self.connect_cookies = connect_cookies or {}
        self.fail_launch_message = fail_launch_message
        self.connected_endpoint = None
        self.launch_kwargs = []

    def launch_persistent_context(self, profile_path, **kwargs):
        self.launch_kwargs.append(kwargs)
        if self.fail_launch_message:
            raise RuntimeError(self.fail_launch_message)
        Path(profile_path).mkdir(parents=True, exist_ok=True)
        (Path(profile_path) / "Default").mkdir(exist_ok=True)
        return self.context

    def connect_over_cdp(self, endpoint):
        if self.fail_connect:
            raise RuntimeError("cdp unavailable")
        self.connected_endpoint = endpoint
        page = _FakePage()
        context = _FakeContext(page, self.connect_cookies)
        return SimpleNamespace(contexts=[context], close=lambda: None)


class _FakePlaywright:
    def __init__(self, chromium):
        self.chromium = chromium
        self.stopped = False

    def start(self):
        return self

    def stop(self):
        self.stopped = True


class _FakeOfficial:
    def __init__(self, root: Path):
        self.root = root

    def profile_path(self, unb: str) -> Path:
        return self.root / f"user_{unb}"

    def _seed_cookie_if_needed(self, context, cookie_string):
        context.seeded = cookie_string

    def _collect_relevant_cookies(self, context):
        context.collect_count += 1
        if context.collect_count == 1:
            return dict(context.baseline)
        return dict(context.fresh)

    def _browser_user_agent(self, page):
        return getattr(page, "user_agent", "FakeUA")


FRESH_COOKIES = {
    "unb": "9988",
    "cookie2": "session",
    "_m_h5_tk": "token",
    "_m_h5_tk_enc": "enc",
}
STALE_COOKIES = {
    "unb": "9988",
    "cookie2": "stale-session",
    "_m_h5_tk": "stale-token",
}


class L3MemoryServiceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self):
        self.tempdir.cleanup()

    def _service(self, context, *, chromium=None):
        chromium = chromium or _FakeChromium(context)
        return L3MemoryService(
            profile_root=self.root,
            playwright_factory=lambda: _FakePlaywright(chromium),
            official_login=_FakeOfficial(self.root),
            settle_seconds=0,
        )

    def test_seed_profile_from_cookies_writes_nonempty_profile(self):
        page = _FakePage()
        context = _FakeContext(page, FRESH_COOKIES)
        service = self._service(context)

        result = service.seed_profile_from_cookies("9988", FRESH_COOKIES, settle_seconds=0)

        self.assertTrue(result.succeeded)
        self.assertTrue(result.has_l3_memory)
        profile = self.root / "user_9988"
        self.assertTrue(profile.is_dir())
        self.assertTrue(any(profile.iterdir()))
        self.assertIn("https://www.goofish.com/bought", page.goto_calls)
        self.assertEqual(result.unb, "9988")

    def test_root_launch_uses_bundled_chromium_without_sandbox(self):
        page = _FakePage()
        context = _FakeContext(page, FRESH_COOKIES)
        chromium = _FakeChromium(context)
        service = self._service(context, chromium=chromium)

        with (
            patch("utils.browser_runtime.os.geteuid", return_value=0),
            patch.dict(os.environ, {"XIANYU_BROWSER_CHANNEL": ""}),
        ):
            result = service.seed_profile_from_cookies(
                "9988",
                FRESH_COOKIES,
                settle_seconds=0,
            )

        self.assertTrue(result.succeeded)
        self.assertIsNone(chromium.launch_kwargs[0]["channel"])
        self.assertFalse(chromium.launch_kwargs[0]["chromium_sandbox"])

    def test_no_proxy_keeps_launch_options_proxy_free(self):
        # 未配代理时启动选项不得出现 proxy 键（与接入前一致）。
        page = _FakePage()
        context = _FakeContext(page, FRESH_COOKIES)
        chromium = _FakeChromium(context)
        service = self._service(context, chromium=chromium)
        service.seed_profile_from_cookies("9988", FRESH_COOKIES, settle_seconds=0)
        self.assertNotIn("proxy", chromium.launch_kwargs[0])

    def test_proxy_config_reaches_launch_options(self):
        # 配了代理时，账号浏览器启动必须带上归一化后的 proxy（账密拆分、内联被丢弃）。
        page = _FakePage()
        context = _FakeContext(page, FRESH_COOKIES)
        chromium = _FakeChromium(context)
        service = L3MemoryService(
            profile_root=self.root,
            playwright_factory=lambda: _FakePlaywright(chromium),
            official_login=_FakeOfficial(self.root),
            settle_seconds=0,
            proxy="http://u:p@1.2.3.4:8000",
        )
        result = service.seed_profile_from_cookies("9988", FRESH_COOKIES, settle_seconds=0)
        self.assertTrue(result.succeeded)
        self.assertEqual(
            chromium.launch_kwargs[0]["proxy"],
            {"server": "http://1.2.3.4:8000", "username": "u", "password": "p"},
        )

    def test_fingerprint_disabled_keeps_launch_options_unchanged(self):
        # 指纹关闭（默认）时：启动选项不含 screen/color_scheme，viewport 维持原值，
        # 且不注入任何 init 脚本——与接入指纹前字节级一致。
        page = _FakePage()
        context = _FakeContext(page, FRESH_COOKIES)
        chromium = _FakeChromium(context)
        service = self._service(context, chromium=chromium)
        with patch.dict(os.environ, {"XIANYU_ACCOUNT_FINGERPRINT": "0"}):
            service.seed_profile_from_cookies("9988", FRESH_COOKIES, settle_seconds=0)
        kwargs = chromium.launch_kwargs[0]
        self.assertEqual(kwargs["viewport"], {"width": 1440, "height": 960})
        self.assertNotIn("screen", kwargs)
        self.assertNotIn("color_scheme", kwargs)
        self.assertNotIn("timezone_id", kwargs)
        self.assertEqual(context.init_scripts, [])

    def test_fingerprint_enabled_injects_context_options_and_script(self):
        # 指纹开启时：账号稳定指纹进入 launch 选项（screen/timezone/色彩），并注入 init 脚本。
        from utils.account_fingerprint import derive_fingerprint

        page = _FakePage()
        context = _FakeContext(page, FRESH_COOKIES)
        chromium = _FakeChromium(context)
        service = self._service(context, chromium=chromium)
        with patch.dict(os.environ, {"XIANYU_ACCOUNT_FINGERPRINT": "1"}):
            service.seed_profile_from_cookies("9988", FRESH_COOKIES, settle_seconds=0)
        kwargs = chromium.launch_kwargs[0]
        expected = derive_fingerprint("9988", os_family="linux")
        self.assertIn("screen", kwargs)
        self.assertEqual(kwargs["screen"]["width"], expected.screen_width)
        self.assertEqual(kwargs["timezone_id"], expected.timezone_id)
        self.assertIn("color_scheme", kwargs)
        self.assertEqual(kwargs["viewport"]["width"], expected.viewport_width)
        self.assertNotIn("user_agent", kwargs)  # 真实浏览器路径保留真实 UA
        self.assertEqual(len(context.init_scripts), 1)
        self.assertIn(expected.webgl_renderer, context.init_scripts[0])

    def test_seed_verification_confirms_quick_entry(self):
        """建档就地验证：删会话后登录框给出「快速进入」→ 记忆实测可用。"""
        frame = _FakeFrame(clickable=True)
        page = _FakePage()
        context = _VerifiableContext(
            page,
            FRESH_COOKIES,
            login_iframe=_FakeIframe(frame),
            baseline=STALE_COOKIES,
        )
        service = self._service(context)

        result = service.seed_profile_from_cookies("9988", STALE_COOKIES, settle_seconds=0)

        self.assertTrue(result.succeeded)
        self.assertIs(result.quick_entry_verified, True)
        self.assertTrue(frame.clicked)
        self.assertIn("cookie2", context.cleared_names)
        # 快速进入换发的新会话必须成为结果（旧 cookie2 可能已被轮换）
        self.assertEqual(result.cookies["cookie2"], "session")
        self.assertTrue((self.root / "user_9988" / ".l3_ready").exists())

    def test_seed_verification_detects_dead_memory(self):
        """登录框弹出但没有「快速进入」→ 如实标记无记忆，还原备份会话。"""
        frame = _FakeFrame(clickable=True, button_present=False)
        page = _FakePage()
        context = _VerifiableContext(
            page, STALE_COOKIES, login_iframe=_FakeIframe(frame)
        )
        service = self._service(context)

        result = service.seed_profile_from_cookies("9988", STALE_COOKIES, settle_seconds=0)

        self.assertEqual(result.status, "success")
        self.assertFalse(result.has_l3_memory)
        self.assertFalse(result.succeeded)
        self.assertIs(result.quick_entry_verified, False)
        self.assertEqual(result.error_code, "quick_entry_unverified")
        # 会话本身仍有效：验证前的备份必须被还原
        self.assertTrue(context.restored)
        self.assertFalse((self.root / "user_9988" / ".l3_ready").exists())

    def test_seed_verification_inconclusive_stays_optimistic(self):
        """删了会话但登录框根本没弹出来 → 无法下结论，保持乐观语义并还原。"""
        page = _FakePage()
        context = _VerifiableContext(page, STALE_COOKIES, login_iframe=None)
        service = self._service(context)

        result = service.seed_profile_from_cookies("9988", STALE_COOKIES, settle_seconds=0)

        self.assertTrue(result.succeeded)
        self.assertIsNone(result.quick_entry_verified)
        self.assertTrue(context.restored)
        self.assertTrue((self.root / "user_9988" / ".l3_ready").exists())

    def test_seed_without_verification_keeps_legacy_behavior(self):
        """verify_quick_entry=False（CDP 来源）不得动会话 Cookie。"""
        page = _FakePage()
        context = _VerifiableContext(page, FRESH_COOKIES, login_iframe=None)
        service = self._service(context)

        result = service.seed_profile_from_cookies(
            "9988", FRESH_COOKIES, settle_seconds=0, verify_quick_entry=False
        )

        self.assertTrue(result.succeeded)
        self.assertIsNone(result.quick_entry_verified)
        self.assertEqual(context.cleared_names, [])

    def test_passwordless_refresh_clicks_quick_enter_and_requires_cookie2(self):
        frame = _FakeFrame(clickable=True)
        page = _FakePage(iframe=_FakeIframe(frame))
        context = _FakeContext(page, FRESH_COOKIES, baseline=STALE_COOKIES)
        (self.root / "user_9988" / "Default").mkdir(parents=True)
        service = self._service(context)

        result = service.passwordless_refresh("9988", STALE_COOKIES, settle_seconds=0)

        self.assertTrue(result.succeeded)
        self.assertTrue(frame.clicked)
        self.assertEqual(result.cookies["cookie2"], "session")

    def test_passwordless_refresh_unchanged_session_is_retryable(self):
        """断网等场景旧 Cookie 原样收回时，必须判可重试而不是假成功。"""
        page = _FakePage()
        context = _FakeContext(page, STALE_COOKIES, baseline=STALE_COOKIES)
        (self.root / "user_9988" / "Default").mkdir(parents=True)
        service = self._service(context)

        result = service.passwordless_refresh("9988", STALE_COOKIES, settle_seconds=0)

        self.assertFalse(result.succeeded)
        self.assertEqual(result.error_code, "session_not_renewed")
        self.assertIn(result.error_code, PASSWORDLESS_RETRYABLE_ERROR_CODES)
        self.assertFalse(result.requires_manual_reauth)

    def test_quick_enter_button_missing_is_manual(self):
        """iframe 已加载但没有「快速进入」按钮 = 记忆真失效，判单向人工重登。"""
        frame = _FakeFrame(clickable=True, button_present=False)
        page = _FakePage(iframe=_FakeIframe(frame))
        context = _FakeContext(page, FRESH_COOKIES, baseline=STALE_COOKIES)
        (self.root / "user_9988" / "Default").mkdir(parents=True)
        service = self._service(context)

        result = service.passwordless_refresh("9988", STALE_COOKIES, settle_seconds=0)

        self.assertFalse(result.succeeded)
        self.assertEqual(result.error_code, "fast_entry_unavailable")
        self.assertTrue(result.requires_manual_reauth)
        self.assertFalse(frame.clicked)

    def test_passwordless_refresh_without_quick_enter_is_manual(self):
        frame = _FakeFrame(clickable=False)
        page = _FakePage(iframe=_FakeIframe(frame))
        context = _FakeContext(page, FRESH_COOKIES)
        (self.root / "user_9988" / "Default").mkdir(parents=True)
        service = self._service(context)

        result = service.passwordless_refresh("9988", FRESH_COOKIES, settle_seconds=0)

        self.assertFalse(result.succeeded)
        self.assertTrue(result.requires_manual_reauth)
        self.assertEqual(result.error_code, "fast_entry_unavailable")
        self.assertIn(result.error_code, PASSWORDLESS_MANUAL_REAUTH_ERROR_CODES)

    def test_missing_profile_is_retryable(self):
        page = _FakePage()
        context = _FakeContext(page, FRESH_COOKIES)
        service = self._service(context)

        result = service.passwordless_refresh("9988", FRESH_COOKIES, settle_seconds=0)

        self.assertEqual(result.error_code, "profile_missing")
        self.assertIn(result.error_code, PASSWORDLESS_RETRYABLE_ERROR_CODES)
        self.assertFalse(result.requires_manual_reauth)

    def test_cdp_without_endpoint_fails_closed(self):
        page = _FakePage()
        context = _FakeContext(page, FRESH_COOKIES)
        service = self._service(context)
        with patch.dict(os.environ, {"XIANYU_CHROME_CDP_ENDPOINT": ""}, clear=False):
            result = service.import_from_cdp(endpoint="", persist_profile=False)
        self.assertFalse(result.succeeded)
        self.assertEqual(result.error_code, "cdp_endpoint_missing")

    def test_cdp_identity_mismatch_fails_closed(self):
        page = _FakePage()
        context = _FakeContext(page, FRESH_COOKIES)
        chromium = _FakeChromium(
            context,
            connect_cookies={"unb": "other", "cookie2": "c2", "_m_h5_tk": "t"},
        )
        service = self._service(context, chromium=chromium)

        result = service.import_from_cdp(
            endpoint="http://127.0.0.1:9222",
            expected_unb="9988",
            persist_profile=False,
        )

        self.assertFalse(result.succeeded)
        self.assertEqual(result.error_code, "cdp_identity_mismatch")
        self.assertTrue(result.requires_manual_reauth)

    def test_cdp_connect_failure_is_retryable(self):
        page = _FakePage()
        context = _FakeContext(page, FRESH_COOKIES)
        chromium = _FakeChromium(context, fail_connect=True)
        service = self._service(context, chromium=chromium)

        result = service.import_from_cdp(
            endpoint="http://127.0.0.1:9222",
            persist_profile=False,
        )

        self.assertEqual(result.error_code, "cdp_connect_failed")
        self.assertIn(result.error_code, PASSWORDLESS_RETRYABLE_ERROR_CODES)

    def test_cdp_success_without_profile_does_not_claim_l3(self):
        """persist_profile=False 时 Cookie 有效但不得虚标 L3 记忆。"""
        page = _FakePage()
        context = _FakeContext(page, FRESH_COOKIES)
        chromium = _FakeChromium(context, connect_cookies=dict(FRESH_COOKIES))
        service = self._service(context, chromium=chromium)

        result = service.import_from_cdp(
            endpoint="http://127.0.0.1:9222",
            expected_unb="9988",
            persist_profile=False,
        )

        self.assertEqual(result.status, "success")
        self.assertFalse(result.has_l3_memory)
        self.assertFalse(result.succeeded)
        self.assertEqual(result.cookies["cookie2"], "session")
        self.assertFalse((self.root / "user_9988").exists())

    def test_cdp_seed_failure_keeps_cookies_but_not_l3(self):
        """CDP 会话有效而建档失败时，登录态可用、has_l3_memory 必须为 False。"""
        page = _FakePage()
        context = _FakeContext(page, FRESH_COOKIES)
        chromium = _FakeChromium(
            context,
            connect_cookies=dict(FRESH_COOKIES),
            fail_launch_message="ProcessSingleton lock is held by another process",
        )
        service = self._service(context, chromium=chromium)

        result = service.import_from_cdp(
            endpoint="http://127.0.0.1:9222",
            expected_unb="9988",
            persist_profile=True,
        )

        self.assertEqual(result.status, "success")
        self.assertFalse(result.has_l3_memory)
        self.assertEqual(result.cookies["unb"], "9988")
        profile = self.root / "user_9988"
        marker = profile / ".l3_ready"
        self.assertFalse(marker.exists())

    def test_launch_failure_classification_is_narrow(self):
        page = _FakePage()
        context = _FakeContext(page, FRESH_COOKIES)
        service = self._service(context)

        in_use = service._launch_failure(RuntimeError("ProcessSingleton lock held"))
        singleton = service._launch_failure(RuntimeError("SingletonLock exists"))
        corrupt = service._launch_failure(
            RuntimeError("Failed to create a ProfileDirectory: data corrupt")
        )
        generic = service._launch_failure(RuntimeError("Target page crashed"))
        profile_word = service._launch_failure(
            RuntimeError("browser profile version too new")
        )

        self.assertEqual(in_use.error_code, "profile_in_use")
        self.assertEqual(singleton.error_code, "profile_in_use")
        self.assertEqual(corrupt.error_code, "profile_corrupt")
        self.assertEqual(generic.error_code, "browser_error")
        self.assertEqual(profile_word.error_code, "browser_error")


class QRLoginL3SeedTests(unittest.IsolatedAsyncioTestCase):
    async def test_qr_success_seeds_profile_and_marks_session(self):
        seeded = []

        def seeder(unb, cookies):
            seeded.append((unb, dict(cookies)))
            return SimpleNamespace(succeeded=True, error_code="")

        manager = QRLoginManager(
            verification_browser=Mock(),
            session_validator=AsyncMock(
                return_value=SessionProbeResult(
                    status="success",
                    cookies=FRESH_COOKIES,
                    access_token="token",
                )
            ),
            l3_seeder=seeder,
        )
        session = QRLoginSession("qr-session")
        session.unb = "9988"
        session.cookies = dict(FRESH_COOKIES)

        ok = await manager._validate_candidate_session(session)

        self.assertTrue(ok)
        self.assertTrue(session.has_l3_memory)
        self.assertEqual(seeded[0][0], "9988")
        manager.sessions[session.session_id] = session
        payload = manager.get_session_cookies(session.session_id)
        self.assertTrue(payload["has_l3_memory"])


class ProbeLayeringTests(unittest.TestCase):
    def test_illegal_access_is_manual_not_expired(self):
        result = classify_probe_response(
            {"ret": ["FAIL_SYS_ILLEGAL_ACCESS::非法访问"], "data": {}},
            {"unb": "9988"},
        )
        self.assertEqual(result.status, PROBE_VERIFICATION_REQUIRED)
        self.assertEqual(result.error_code, "illegal_access")

    def test_session_expired_stays_expired_for_passwordless_path(self):
        result = classify_probe_response(
            {"ret": ["FAIL_SYS_SESSION_EXPIRED::Session过期"], "data": {}},
            {"unb": "9988"},
        )
        self.assertEqual(result.status, PROBE_EXPIRED)
        self.assertEqual(result.error_code, "session_expired")

    def test_token_expired_is_distinct_from_session_expired(self):
        result = classify_probe_response(
            {"ret": ["FAIL_SYS_TOKEN_EXOIRED::令牌过期"], "data": {}},
            {"unb": "9988"},
        )
        self.assertEqual(result.status, PROBE_EXPIRED)
        self.assertEqual(result.error_code, "token_expired")


if __name__ == "__main__":
    unittest.main()
