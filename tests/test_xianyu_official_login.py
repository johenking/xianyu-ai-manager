import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from loguru import logger

from utils.xianyu_official_login import (
    GOOFISH_LOGIN_URL,
    OfficialLoginWorker,
    XianyuOfficialLoginService,
)
from utils.xianyu_session_probe import (
    PROBE_SUCCESS,
    PROBE_VERIFICATION_REQUIRED,
    SessionProbeResult,
    parse_cookie_string,
)


def authenticated_cookies(unb: str = "123456"):
    return [
        {"name": "unb", "value": unb, "domain": ".goofish.com", "path": "/"},
        {"name": "cookie2", "value": "session-cookie", "domain": ".goofish.com", "path": "/"},
        {"name": "_m_h5_tk", "value": "token", "domain": ".goofish.com", "path": "/"},
    ]


class FakeElement:
    def __init__(self, *, text="", checked=None, on_click=None, on_screenshot=None):
        self.text = text
        self.checked = checked
        self.on_click = on_click
        self.on_screenshot = on_screenshot
        self.clicked = 0
        self.filled = []
        self.visible = True

    def is_visible(self):
        return self.visible

    def click(self):
        self.clicked += 1
        if self.checked is not None:
            self.checked = not self.checked
        if self.on_click:
            self.on_click()

    def fill(self, value):
        self.filled.append(value)

    def is_checked(self):
        if self.checked is None:
            raise RuntimeError("not a checkbox")
        return self.checked

    def inner_text(self):
        return self.text

    def screenshot(self, path, **kwargs):
        del kwargs
        Path(path).write_bytes(b"element")
        if self.on_screenshot:
            self.on_screenshot()


class FakeLocator:
    def __init__(self, element=None):
        self.element = element

    def count(self):
        return 1 if self.element is not None else 0

    @property
    def first(self):
        return self.element


class FakePage:
    def __init__(self):
        self.selectors = {}
        self.texts = {}
        self.frames = []
        self.url = "https://www.goofish.com/im"
        self.goto_calls = []
        self.user_agent = "Mozilla/5.0 Synthetic Chrome/150.0.0.0 Safari/537.36"
        self.closed = False
        self.init_scripts = []
        self.viewport_size = {"width": 1440, "height": 960}

    def _ensure_open(self):
        if self.closed:
            raise RuntimeError("Target page, context or browser has been closed")

    def is_closed(self):
        return self.closed

    def query_selector(self, selector):
        self._ensure_open()
        return self.selectors.get(selector)

    def get_by_text(self, text, exact=True):
        self._ensure_open()
        del exact
        return FakeLocator(self.texts.get(text))

    def goto(self, url, **kwargs):
        self._ensure_open()
        self.goto_calls.append((url, kwargs))

    def wait_for_timeout(self, timeout):
        self._ensure_open()
        del timeout

    def screenshot(self, path=None, **kwargs):
        self._ensure_open()
        del kwargs
        if path is None:
            # 画面流截图（interaction_channel.capture）不带 path，直接返回字节。
            return b"fake-frame-png"
        Path(path).write_bytes(b"verification")

    def add_init_script(self, script):
        self._ensure_open()
        self.init_scripts.append(script)

    def evaluate(self, expression):
        self._ensure_open()
        if expression == "navigator.userAgent":
            return self.user_agent
        return False


class FakeContext:
    def __init__(self, page, cookies=None, cdp_session=None):
        self.pages = [page]
        self.cookies_data = list(cookies or [])
        self.added_cookies = []
        self.closed = False
        self.cdp_session = cdp_session

    def cookies(self, urls=None):
        del urls
        return list(self.cookies_data)

    def add_cookies(self, cookies):
        self.added_cookies.extend(cookies)

    def new_page(self):
        page = FakePage()
        self.pages.append(page)
        return page

    def close(self):
        self.closed = True

    def new_cdp_session(self, page):
        del page
        if self.cdp_session is None:
            raise RuntimeError("CDP unavailable")
        return self.cdp_session


class FakeCDPSession:
    def __init__(self):
        self.calls = []
        self.detached = False

    def send(self, method, params=None):
        self.calls.append((method, params))
        if method == "Browser.getWindowForTarget":
            return {"windowId": 17}
        return {}

    def detach(self):
        self.detached = True


class FakeChromium:
    def __init__(self, factory, context):
        self.factory = factory
        self.context = context

    def launch_persistent_context(self, user_data_dir, **kwargs):
        self.factory.launches.append((Path(user_data_dir), kwargs))
        return self.context


class FakePlaywright:
    def __init__(self, factory, context):
        self.chromium = FakeChromium(factory, context)
        self.stopped = False

    def stop(self):
        self.stopped = True


class FakeStarter:
    def __init__(self, factory, context):
        self.factory = factory
        self.context = context

    def start(self):
        return FakePlaywright(self.factory, self.context)


class SequencePlaywrightFactory:
    def __init__(self, contexts):
        self.contexts = list(contexts)
        self.launches = []

    def __call__(self):
        if not self.contexts:
            raise RuntimeError("no fake context left")
        return FakeStarter(self, self.contexts.pop(0))


def make_password_context(*, unb="123456", error_message="", security=False):
    page = FakePage()
    context = FakeContext(page)
    account_input = FakeElement()
    password_input = FakeElement()
    agreement = FakeElement(checked=False)
    keep_login = FakeElement()

    def switch_to_password():
        page.selectors["#fm-login-password"] = password_input

    password_tab = FakeElement(on_click=switch_to_password)

    def submit_login():
        for selector in (
            "#fm-login-id",
            "#fm-login-password",
            "a.password-login-tab-item",
            "input[type='checkbox']",
            "button.password-login",
        ):
            page.selectors.pop(selector, None)
        if error_message:
            page.selectors[".fm-error"] = FakeElement(text=error_message)
        elif security:
            page.selectors[".nc-container"] = FakeElement()
        else:
            def confirm_keep_login():
                context.cookies_data = authenticated_cookies(unb)
                page.texts.pop("保持登录", None)

            keep_login.on_click = confirm_keep_login
            page.texts["保持登录"] = keep_login

    login_button = FakeElement(on_click=submit_login)
    page.selectors.update({
        "#fm-login-id": account_input,
        "a.password-login-tab-item": password_tab,
        "input[type='checkbox']": agreement,
        "button.password-login": login_button,
    })
    return context, {
        "account_input": account_input,
        "password_input": password_input,
        "password_tab": password_tab,
        "agreement": agreement,
        "keep_login": keep_login,
        "login_button": login_button,
    }


class XianyuOfficialLoginTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.profile_root = self.root / "browser_data"
        self.verification_root = self.root / "static" / "uploads" / "images"

    def tearDown(self):
        self.temp_dir.cleanup()

    def make_service(self, factory, **kwargs):
        def successful_probe(cookie_string, _browser_user_agent):
            return SessionProbeResult(
                status=PROBE_SUCCESS,
                cookies=parse_cookie_string(cookie_string),
                access_token="synthetic-access-token",
            )

        return XianyuOfficialLoginService(
            profile_root=self.profile_root,
            verification_root=self.verification_root,
            playwright_factory=factory,
            poll_interval=kwargs.pop("poll_interval", 0.001),
            login_timeout=kwargs.pop("login_timeout", 0.05),
            verification_timeout=kwargs.pop("verification_timeout", 0.02),
            probe_interval=kwargs.pop("probe_interval", 0.001),
            session_validator=kwargs.pop("session_validator", successful_probe),
            **kwargs,
        )

    def test_local_visible_login_disables_remote_interaction_stream(self):
        # 本机可见窗口（show_browser=True）不发布画面帧、不转发手势——否则
        # Playwright 截图的 Page.bringToFront 会每秒抢焦点（窗口闪烁、滑块被打断）。
        context, _elements = make_password_context(unb="9988")
        factory = SequencePlaywrightFactory([context])
        service = self.make_service(factory)
        worker = OfficialLoginWorker()
        live_frames = []

        result = service.login_with_password(
            account="13800138000",
            password="secret",
            show_browser=True,
            worker=worker,
            on_validated=lambda _v: live_frames.append(
                worker.interaction_channel.latest_frame()
            ),
        )

        self.assertTrue(result.succeeded)
        self.assertFalse(worker.remote_interaction_enabled)
        # 会话存活期间也从未发布过画面帧。
        self.assertEqual(live_frames, [None])

    def test_background_refresh_keeps_remote_interaction_stream(self):
        # 后台自动续期（show_browser=False）保留画面流：远程查看验证画面的刚需。
        page = FakePage()
        context = FakeContext(page, authenticated_cookies("9988"))
        service = self.make_service(SequencePlaywrightFactory([context]))
        worker = OfficialLoginWorker()
        live_frames = []

        result = service.refresh_session(
            profile_unb="9988",
            current_cookie="unb=9988; cookie2=old",
            worker=worker,
            on_validated=lambda _v: live_frames.append(
                worker.interaction_channel.latest_frame()
            ),
        )

        self.assertTrue(result.succeeded)
        self.assertTrue(worker.remote_interaction_enabled)
        # 会话存活期间发布过真实画面帧（会话结束时通道才被关闭清空）。
        self.assertEqual(len(live_frames), 1)
        self.assertIsNotNone(live_frames[0])

    def test_show_event_brings_visible_window_to_front(self):
        # 可见窗口被其他窗口挡住时，"重新显示 Chrome 窗口"也应把窗口拉回前台。
        context, _elements = make_password_context(unb="9988")
        context.cdp_session = FakeCDPSession()
        factory = SequencePlaywrightFactory([context])
        service = self.make_service(factory)
        worker = OfficialLoginWorker()
        worker.request_visible()

        result = service.login_with_password(
            account="13800138000",
            password="secret",
            show_browser=True,
            worker=worker,
        )

        self.assertTrue(result.succeeded)
        self.assertIn(
            "Browser.setWindowBounds",
            [method for method, _params in context.cdp_session.calls],
        )

    def test_disabled_worker_skips_capture_and_drain(self):
        worker = OfficialLoginWorker()
        worker.interaction_channel.publish_frame(
            b"png",
            viewport_width=100,
            viewport_height=100,
            surface_key="k",
        )
        worker.submit_interaction({
            "kind": "key",
            "key": "Enter",
            "frame_revision": worker.interaction_channel.snapshot()["frame_revision"],
        })
        worker.remote_interaction_enabled = False
        self.assertFalse(worker.capture_frame(object()))
        self.assertEqual(worker.drain_interactions(object()), 0)
        self.assertEqual(worker.interaction_channel.pending_count, 1)

    def test_active_page_returns_none_when_user_closed_all_pages_and_reopen_disabled(self):
        # 用户关闭全部登录页面时，监控循环不应"关窗复活"重弹，而是拿到 None 后结束会话。
        service = self.make_service(lambda: None)
        closed_page = FakePage()
        closed_page.closed = True
        context = FakeContext(closed_page)
        self.assertIsNone(service._active_page(context, allow_reopen=False))

    def test_active_page_returns_open_page_even_when_reopen_disabled(self):
        # 仍有打开的页面时，禁止重开不影响正常返回当前页面。
        service = self.make_service(lambda: None)
        open_page = FakePage()
        context = FakeContext(open_page)
        self.assertIs(service._active_page(context, open_page, allow_reopen=False), open_page)

    def test_initial_login_switches_from_sms_and_confirms_agreement_and_keep_login(self):
        context, elements = make_password_context(unb="9988")
        factory = SequencePlaywrightFactory([context])
        service = self.make_service(factory)

        result = service.login_with_password(
            account="13800138000",
            password="secret",
            show_browser=True,
        )

        self.assertTrue(result.succeeded)
        self.assertEqual(result.unb, "9988")
        self.assertTrue(result.used_password)
        self.assertEqual(elements["password_tab"].clicked, 1)
        self.assertEqual(elements["account_input"].filled, ["13800138000"])
        self.assertEqual(elements["password_input"].filled, ["secret"])
        self.assertTrue(elements["agreement"].checked)
        self.assertEqual(elements["keep_login"].clicked, 1)
        self.assertEqual(factory.launches[0][0].parent, self.profile_root)
        self.assertTrue(factory.launches[0][0].name.startswith(".login_"))
        self.assertTrue((self.profile_root / "user_9988").is_dir())
        self.assertEqual(list(self.profile_root.glob(".login_*")), [])

        launch_options = factory.launches[0][1]
        # 未配置 XIANYU_BROWSER_CHANNEL 时使用 Playwright 自带 Chromium（零安装，不依赖系统 Chrome）。
        self.assertIsNone(launch_options["channel"])
        self.assertNotIn("user_agent", launch_options)
        self.assertNotIn("--disable-blink-features=AutomationControlled", launch_options["args"])
        self.assertNotIn("--disable-web-security", launch_options["args"])
        self.assertEqual(context.pages[0].goto_calls[0][0], GOOFISH_LOGIN_URL)

    def test_refresh_reuses_canonical_profile_without_password(self):
        page = FakePage()
        context = FakeContext(page, authenticated_cookies("9988"))
        factory = SequencePlaywrightFactory([context])
        service = self.make_service(factory)

        result = service.refresh_session(
            profile_unb="9988",
            current_cookie="unb=9988; cookie2=old",
        )

        self.assertTrue(result.succeeded)
        self.assertFalse(result.used_password)
        self.assertEqual(factory.launches[0][0], self.profile_root / "user_9988")

    def test_validated_handoff_finishes_before_the_browser_is_closed(self):
        page = FakePage()
        context = FakeContext(page, authenticated_cookies("9988"))
        service = self.make_service(SequencePlaywrightFactory([context]))
        observed = []

        result = service.refresh_session(
            profile_unb="9988",
            current_cookie="unb=9988; cookie2=old",
            on_validated=lambda validated: observed.append(
                (validated.access_token, validated.browser_user_agent, context.closed)
            ),
        )

        self.assertTrue(result.succeeded)
        self.assertEqual(
            observed,
            [(
                "synthetic-access-token",
                page.user_agent,
                False,
            )],
        )
        self.assertTrue(context.closed)

    def test_token_verification_keeps_one_context_and_opens_each_verification_url_once(self):
        page = FakePage()
        context = FakeContext(page, authenticated_cookies("9988"))
        calls = 0
        verification_url = "https://passport.goofish.com/iv/check"

        def probe(cookie_string, _browser_user_agent):
            nonlocal calls
            calls += 1
            cookies = parse_cookie_string(cookie_string)
            if calls < 3:
                return SessionProbeResult(
                    status=PROBE_VERIFICATION_REQUIRED,
                    cookies=cookies,
                    verification_url=verification_url,
                    error_code="human_verification_required",
                )
            return SessionProbeResult(
                status=PROBE_SUCCESS,
                cookies=cookies,
                access_token="validated-token",
            )

        service = self.make_service(
            SequencePlaywrightFactory([context]),
            session_validator=probe,
            verification_timeout=0.05,
        )
        statuses = []

        result = service.refresh_session(
            profile_unb="9988",
            current_cookie="unb=9988; cookie2=old",
            on_status=statuses.append,
        )

        self.assertTrue(result.succeeded)
        self.assertEqual(result.access_token, "validated-token")
        self.assertEqual(
            [url for url, _options in page.goto_calls],
            ["https://www.goofish.com/im", verification_url],
        )
        self.assertEqual(len(statuses), 1)
        self.assertEqual(statuses[0].status, "verification_required")
        self.assertTrue(context.closed)

    def test_authenticated_cookies_do_not_override_a_visible_login_form(self):
        context, _ = make_password_context(unb="9988")
        context.cookies_data = authenticated_cookies("9988")
        service = self.make_service(SequencePlaywrightFactory([context]))
        statuses = []

        result = service.refresh_session(
            profile_unb="9988",
            current_cookie="unb=9988; cookie2=old",
            on_status=statuses.append,
        )

        self.assertEqual(result.status, "timeout")
        self.assertEqual(result.error_code, "login_timeout")
        self.assertEqual(statuses[0].status, "verification_required")
        self.assertEqual(statuses[0].error_code, "reauth_required")

    def test_refresh_rejects_a_profile_logged_into_another_unb(self):
        page = FakePage()
        context = FakeContext(page, authenticated_cookies("other-unb"))
        service = self.make_service(SequencePlaywrightFactory([context]))

        result = service.refresh_session(
            profile_unb="expected-unb",
            current_cookie="unb=expected-unb; cookie2=old",
        )

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_code, "account_mismatch")
        self.assertEqual(result.unb, "other-unb")

    def test_refresh_waits_for_manual_login_without_using_saved_credentials(self):
        context, elements = make_password_context(unb="9988")
        factory = SequencePlaywrightFactory([context])
        service = self.make_service(factory)
        statuses = []

        result = service.refresh_session(
            profile_unb="9988",
            current_cookie="unb=9988; cookie2=expired",
            account="seller@example.com",
            password="secret",
            allow_password=False,
            on_status=statuses.append,
        )

        self.assertEqual(result.status, "timeout")
        self.assertEqual(result.error_code, "login_timeout")
        self.assertEqual(statuses[0].status, "verification_required")
        self.assertEqual(statuses[0].error_code, "reauth_required")
        self.assertFalse(result.used_password)
        self.assertEqual(elements["password_tab"].clicked, 0)
        self.assertEqual(factory.launches[0][0], self.profile_root / "user_9988")

    def test_wrong_password_returns_official_error(self):
        context, _ = make_password_context(error_message="账号或密码错误")
        service = self.make_service(SequencePlaywrightFactory([context]))

        result = service.login_with_password(
            account="seller@example.com",
            password="wrong",
            show_browser=True,
        )

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_code, "invalid_credentials")
        self.assertEqual(result.message, "账号或密码错误")

    def test_verification_reopens_visible_browser_only_after_user_request(self):
        first_context, _ = make_password_context(security=True)
        cdp_session = FakeCDPSession()
        first_context.cdp_session = cdp_session
        factory = SequencePlaywrightFactory([first_context])
        statuses = []
        service = self.make_service(factory)
        worker = OfficialLoginWorker()
        worker.request_visible()

        result = service.login_with_password(
            account="seller@example.com",
            password="secret",
            show_browser=False,
            worker=worker,
            on_status=statuses.append,
        )

        self.assertEqual(result.status, "timeout")
        self.assertEqual(result.error_code, "verification_timeout")
        self.assertTrue(Path(result.verification_image_path).is_file())
        self.assertNotIn("seller", Path(result.verification_image_path).name)
        self.assertEqual([launch[1]["headless"] for launch in factory.launches], [False])
        self.assertIn("--window-position=-32000,-32000", factory.launches[0][1]["args"])
        self.assertEqual(
            [method for method, _ in cdp_session.calls],
            ["Browser.getWindowForTarget", "Browser.setWindowBounds"],
        )
        self.assertTrue(cdp_session.detached)
        self.assertEqual(len(statuses), 1)
        self.assertEqual(statuses[0].status, "verification_required")

    def test_qr_session_upgrades_to_verification_when_official_page_changes(self):
        page = FakePage()
        context = FakeContext(page)

        def switch_to_security():
            page.selectors.pop(".qrcode-img", None)
            page.selectors[".nc-container"] = FakeElement()

        page.selectors[".qrcode-img"] = FakeElement(on_screenshot=switch_to_security)
        statuses = []
        service = self.make_service(SequencePlaywrightFactory([context]))

        result = service.login_with_qr(
            show_browser=False,
            on_status=statuses.append,
        )

        self.assertEqual(result.status, "timeout")
        self.assertEqual(result.error_code, "verification_timeout")
        self.assertEqual(
            [status.status for status in statuses],
            ["waiting_user", "verification_required"],
        )
        self.assertTrue(Path(result.verification_image_path).is_file())

    def test_qr_session_rebinds_to_replacement_page_until_token_validation(self):
        first_page = FakePage()
        replacement_page = FakePage()
        context = FakeContext(first_page)
        verification_url = "https://passport.goofish.com/iv/check"
        probe_calls = 0

        def replace_page():
            first_page.closed = True
            context.pages = [replacement_page]
            context.cookies_data = authenticated_cookies("9988")

        first_page.selectors[".qrcode-img"] = FakeElement(
            on_screenshot=replace_page,
        )

        def probe(cookie_string, _browser_user_agent):
            nonlocal probe_calls
            probe_calls += 1
            cookies = parse_cookie_string(cookie_string)
            if probe_calls == 1:
                return SessionProbeResult(
                    status=PROBE_VERIFICATION_REQUIRED,
                    cookies=cookies,
                    verification_url=verification_url,
                    error_code="human_verification_required",
                )
            return SessionProbeResult(
                status=PROBE_SUCCESS,
                cookies=cookies,
                access_token="validated-token",
            )

        service = self.make_service(
            SequencePlaywrightFactory([context]),
            session_validator=probe,
            verification_timeout=0.08,
        )

        result = service.login_with_qr(show_browser=False)

        self.assertTrue(result.succeeded)
        self.assertEqual(result.unb, "9988")
        self.assertEqual(result.access_token, "validated-token")
        self.assertEqual(
            [url for url, _options in replacement_page.goto_calls],
            [verification_url],
        )
        self.assertGreaterEqual(len(replacement_page.init_scripts), 1)
        self.assertTrue(context.closed)

    def test_pre_cancelled_worker_stops_before_browser_launch(self):
        factory = SequencePlaywrightFactory([])
        service = self.make_service(factory)
        worker = OfficialLoginWorker()
        worker.close_browser()

        result = service.refresh_session(
            profile_unb="9988",
            current_cookie="unb=9988; cookie2=old",
            worker=worker,
        )

        self.assertEqual(result.status, "cancelled")
        self.assertEqual(factory.launches, [])

    def test_worker_cancel_never_closes_playwright_from_the_caller_thread(self):
        page = FakePage()
        context = FakeContext(page)
        worker = OfficialLoginWorker()
        worker.attach(context, object())
        worker.interaction_channel.publish_frame(
            b"frame",
            viewport_width=1440,
            viewport_height=960,
        )

        worker.close_browser()

        self.assertTrue(worker.cancel_event.is_set())
        self.assertFalse(context.closed)
        self.assertFalse(worker.interaction_snapshot()["interaction_supported"])

    def test_profile_promotion_restores_backup_when_replacement_fails(self):
        service = self.make_service(SequencePlaywrightFactory([]))
        temporary = self.profile_root / ".login_temp"
        target = self.profile_root / "user_9988"
        temporary.mkdir(parents=True)
        target.mkdir(parents=True)
        (temporary / "new.txt").write_text("new", encoding="utf-8")
        (target / "old.txt").write_text("old", encoding="utf-8")
        real_replace = os.replace

        def flaky_replace(source, destination):
            if Path(source) == temporary and Path(destination) == target:
                raise OSError("promotion failed")
            return real_replace(source, destination)

        with patch("utils.xianyu_official_login.os.replace", side_effect=flaky_replace):
            with self.assertRaises(OSError):
                service._promote_profile(temporary, "9988")

        self.assertEqual((target / "old.txt").read_text(encoding="utf-8"), "old")
        self.assertTrue((temporary / "new.txt").is_file())
        self.assertEqual(list(self.profile_root.glob("user_9988.backup-*")), [])

    def test_initialization_cleans_only_aged_temporary_and_backup_profiles(self):
        self.profile_root.mkdir(parents=True, exist_ok=True)
        stale_login = self.profile_root / ".login_stale"
        stale_window = self.profile_root / ".window_stale"
        stale_backup = self.profile_root / "user_9988.backup-stale"
        canonical = self.profile_root / "user_9988"
        unknown = self.profile_root / "legacy_profile"
        fresh_login = self.profile_root / ".login_active"
        fresh_window = self.profile_root / ".window_active"
        for path in (
            stale_login,
            stale_window,
            stale_backup,
            canonical,
            unknown,
            fresh_login,
            fresh_window,
        ):
            path.mkdir()
        ignored_file = self.profile_root / "user_9988.backup-file"
        ignored_file.write_text("keep", encoding="utf-8")

        old_mtime = time.time() - (7 * 3600)
        for path in (stale_login, stale_window, stale_backup, ignored_file):
            os.utime(path, (old_mtime, old_mtime))

        self.make_service(SequencePlaywrightFactory([]))

        self.assertFalse(stale_login.exists())
        self.assertFalse(stale_window.exists())
        self.assertFalse(stale_backup.exists())
        self.assertTrue(canonical.is_dir())
        self.assertTrue(unknown.is_dir())
        self.assertTrue(fresh_login.is_dir())
        self.assertTrue(fresh_window.is_dir())
        self.assertTrue(ignored_file.is_file())

    def test_browser_failures_do_not_log_or_return_sensitive_material(self):
        class SensitiveFailureFactory:
            def __call__(self):
                raise RuntimeError(
                    "cookies={'unb': 'COOKIE_IDENTITY', 'cookie2': 'COOKIE_SECRET'} "
                    "token='TOKEN_SECRET' password='PASSWORD_SECRET' "
                    "https://passport.goofish.com/verify?id=VERIFY_SECRET"
                )

        messages = []
        sink_id = logger.add(lambda message: messages.append(str(message)), format="{message}")
        try:
            result = self.make_service(SensitiveFailureFactory()).login_with_qr()
        finally:
            logger.remove(sink_id)

        combined = f"{result} {' '.join(messages)}"
        for secret in (
            "COOKIE_IDENTITY",
            "COOKIE_SECRET",
            "TOKEN_SECRET",
            "PASSWORD_SECRET",
            "VERIFY_SECRET",
            "passport.goofish.com",
        ):
            self.assertNotIn(secret, combined)


if __name__ == "__main__":
    unittest.main()
