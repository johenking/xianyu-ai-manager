import unittest
import tempfile
from unittest.mock import Mock, patch

from utils.qr_verification_browser import QRVerificationBrowser
from utils.xianyu_session_probe import SessionProbeResult


class _FakePage:
    frames = []

    def goto(self, *_args, **_kwargs):
        return None


class _FakeContext:
    def __init__(self, cookies):
        self.pages = [_FakePage()]
        self._cookies = cookies

    def add_cookies(self, _cookies):
        return None

    def cookies(self):
        return list(self._cookies)


class _FakeChromium:
    def __init__(self, context):
        self.context = context

    def launch_persistent_context(self, *_args, **_kwargs):
        return self.context


class _FakePlaywright:
    def __init__(self, context):
        self.chromium = _FakeChromium(context)


class _FakePlaywrightManager:
    def __init__(self, context):
        self.playwright = _FakePlaywright(context)

    def __enter__(self):
        return self.playwright

    def __exit__(self, *_args):
        return None


class _TestBrowser(QRVerificationBrowser):
    def _wait_for_verification_content(self, *_args, **_kwargs):
        return True

    def _capture_screenshot(self, *_args, **_kwargs):
        return None

    def _has_success_hint(self, *_args, **_kwargs):
        return False

    def _classify_verification(self, *_args, **_kwargs):
        return "interactive"

    def _browser_user_agent(self, *_args, **_kwargs):
        return "Chrome Test UA"


class QRVerificationBrowserTests(unittest.TestCase):
    cookies = [
        {"name": "unb", "value": "account-1"},
        {"name": "cookie2", "value": "session"},
    ]

    def test_existing_unb_does_not_finish_while_probe_requires_verification(self):
        validator = Mock(return_value=SessionProbeResult(
            status="verification_required",
            cookies={"unb": "account-1", "cookie2": "session"},
            error_code="human_verification_required",
        ))
        context = _FakeContext(self.cookies)
        stop_checks = iter([False, True])
        with tempfile.TemporaryDirectory() as profile_root:
            browser = _TestBrowser(
                profile_root=profile_root,
                playwright_factory=lambda: _FakePlaywrightManager(context),
                session_validator=validator,
            )
            with patch("utils.qr_verification_browser.time.sleep", return_value=None):
                result = browser.run(
                    "session-verification",
                    "https://passport.goofish.com/verify",
                    should_stop=lambda: next(stop_checks),
                )

        self.assertEqual(result["status"], "cancelled")
        validator.assert_called_once()

    def test_probe_access_token_is_required_before_success(self):
        validator = Mock(return_value=SessionProbeResult(
            status="success",
            cookies={"unb": "account-1", "cookie2": "session"},
            access_token="verified-token",
        ))
        context = _FakeContext(self.cookies)
        with tempfile.TemporaryDirectory() as profile_root:
            browser = _TestBrowser(
                profile_root=profile_root,
                playwright_factory=lambda: _FakePlaywrightManager(context),
                session_validator=validator,
            )
            with patch("utils.qr_verification_browser.time.sleep", return_value=None):
                result = browser.run(
                    "session-success",
                    "https://passport.goofish.com/verify",
                    should_stop=lambda: False,
                )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["unb"], "account-1")
        self.assertEqual(result["access_token"], "verified-token")


if __name__ == "__main__":
    unittest.main()
