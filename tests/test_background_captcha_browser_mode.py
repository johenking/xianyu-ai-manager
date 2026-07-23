import inspect
import unittest

from XianyuAutoAsync import XianyuLive


class BackgroundCaptchaBrowserModeTests(unittest.TestCase):
    def test_login_refresh_has_no_legacy_background_captcha_browser(self):
        self.assertFalse(hasattr(XianyuLive, "_handle_captcha_verification"))

        source = inspect.getsource(XianyuLive.refresh_token)
        self.assertNotIn("XianyuSliderStealth", source)
        self.assertNotIn("xianyu_slider_stealth", source)


if __name__ == "__main__":
    unittest.main()
