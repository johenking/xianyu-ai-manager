import sys
import types
import unittest
from unittest.mock import AsyncMock, patch

from XianyuAutoAsync import ConnectionState, XianyuLive


class FakeSlider:
    headless_values = []

    def __init__(self, *, user_id, enable_learning, headless):
        del user_id, enable_learning
        self.headless_values.append(headless)

    def run(self, verification_url):
        del verification_url
        return False, {}


class FakeDatabase:
    def get_cookie_details(self, cookie_id):
        del cookie_id
        return {"show_browser": True}


class BackgroundCaptchaBrowserModeTests(unittest.IsolatedAsyncioTestCase):
    async def test_background_captcha_stays_headless_when_login_prefers_visible_browser(self):
        live = object.__new__(XianyuLive)
        live.cookie_id = "account-1"
        live.cookies = {}
        live.cookies_str = ""
        live.connection_state = ConnectionState.DISCONNECTED
        live.ws = None
        live.send_token_refresh_notification = AsyncMock()

        fake_slider_module = types.ModuleType("utils.xianyu_slider_stealth")
        fake_slider_module.XianyuSliderStealth = FakeSlider
        FakeSlider.headless_values.clear()

        with (
            patch("XianyuAutoAsync.db_manager", FakeDatabase()),
            patch("XianyuAutoAsync.log_captcha_event"),
            patch.dict(sys.modules, {"utils.xianyu_slider_stealth": fake_slider_module}),
        ):
            result = await live._handle_captcha_verification(
                {"data": {"url": "https://example.invalid/captcha"}}
            )

        self.assertIsNone(result)
        self.assertEqual(FakeSlider.headless_values, [True])
        live.send_token_refresh_notification.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
