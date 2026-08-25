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
    def __init__(self, owner):
        self.owner = owner
        self.first = self

    def click(self, timeout=5000):
        del timeout
        self.owner.clicked = True


class _FakeFrame:
    def __init__(self, *, clickable=True):
        self.clickable = clickable
        self.clicked = False

    def wait_for_load_state(self, *_args, **_kwargs):
        return None

    def get_by_text(self, text, exact=True):
        del exact
        if text != "快速进入" or not self.clickable:
            raise RuntimeError("locator not found")
        return _FakeLocator(self)


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
    def __init__(self, page, cookies):
        self.pages = [page]
        self.fresh = dict(cookies)
        self.seeded = ""
        self.closed = False

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


class _FakeChromium:
    def __init__(self, context, *, fail_connect=False, connect_cookies=None):
        self.context = context
        self.fail_connect = fail_connect
        self.connect_cookies = connect_cookies or {}
        self.connected_endpoint = None

    def launch_persistent_context(self, profile_path, **_kwargs):
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
        return dict(context.fresh)

    def _browser_user_agent(self, page):
        return getattr(page, "user_agent", "FakeUA")


FRESH_COOKIES = {
    "unb": "9988",
    "cookie2": "session",
    "_m_h5_tk": "token",
    "_m_h5_tk_enc": "enc",
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

    def test_passwordless_refresh_clicks_quick_enter_and_requires_cookie2(self):
        frame = _FakeFrame(clickable=True)
        page = _FakePage(iframe=_FakeIframe(frame))
        context = _FakeContext(page, FRESH_COOKIES)
        (self.root / "user_9988" / "Default").mkdir(parents=True)
        service = self._service(context)

        result = service.passwordless_refresh("9988", FRESH_COOKIES, settle_seconds=0)

        self.assertTrue(result.succeeded)
        self.assertTrue(frame.clicked)
        self.assertEqual(result.cookies["cookie2"], "session")

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
