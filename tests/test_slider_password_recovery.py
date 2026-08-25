"""账密 + 滑块隐身后台自愈重登的单元测试。

扫码 Cookie 约 10 小时过期、且服务端 CfT 续期撞滑块 0 成功，导致自动续期被硬关、
账号只能人工重扫。移植 XianyuSliderStealth 隐身登录栈后，_recover_via_slider_password_login
让有账密的账号在后台自动重登。本测试锁住其纯逻辑（cookie 拼接、无效判定、冷却、异常
兜底）与门禁重启用；"能否真正过滑块"属运行时行为，由真机验证覆盖。
"""

import asyncio
import unittest
from unittest.mock import MagicMock, patch

from XianyuAutoAsync import XianyuLive
from account_session_refresh import supports_automatic_refresh


class SliderPasswordRecoveryTests(unittest.TestCase):
    def _live(self):
        live = object.__new__(XianyuLive)
        live.cookie_id = "acct-test"
        XianyuLive._last_password_login_time.pop("acct-test", None)
        return live

    def _patch_slider(self, return_value=None, side_effect=None):
        slider = MagicMock()
        slider.login_with_password_playwright.return_value = return_value
        target = patch(
            "utils.xianyu_slider_stealth.XianyuSliderStealth",
            side_effect=side_effect,
            return_value=slider,
        )
        return target, slider

    def test_returns_cookie_string_on_success(self):
        live = self._live()
        target, _slider = self._patch_slider({"unb": "123", "cookie2": "c2"})
        with target:
            result = asyncio.run(live._recover_via_slider_password_login("user", "pass"))
        self.assertIn("unb=123", result)
        self.assertIn("cookie2=c2", result)

    def test_empty_when_cookie_has_no_unb(self):
        # 没有 unb 说明登录未真正落地（可能只到滑块页），不能当成功
        live = self._live()
        target, _slider = self._patch_slider({"cookie2": "c2"})
        with target:
            self.assertEqual(asyncio.run(live._recover_via_slider_password_login("user", "pass")), "")

    def test_empty_when_login_returns_none(self):
        live = self._live()
        target, _slider = self._patch_slider(None)
        with target:
            self.assertEqual(asyncio.run(live._recover_via_slider_password_login("user", "pass")), "")

    def test_cooldown_skips_second_attempt(self):
        live = self._live()
        target, _slider = self._patch_slider({"unb": "123"})
        with target as slider_cls:
            first = asyncio.run(live._recover_via_slider_password_login("user", "pass"))
            second = asyncio.run(live._recover_via_slider_password_login("user", "pass"))
        self.assertIn("unb=123", first)
        self.assertEqual(second, "")  # 冷却期内直接跳过
        self.assertEqual(slider_cls.call_count, 1)  # 第二次没有再拉起浏览器

    def test_exception_returns_empty_and_is_silent(self):
        live = self._live()
        target, _slider = self._patch_slider(side_effect=RuntimeError("boom"))
        with target:
            self.assertEqual(asyncio.run(live._recover_via_slider_password_login("user", "pass")), "")

    def test_supports_automatic_refresh_reenabled_for_password_accounts(self):
        self.assertTrue(supports_automatic_refresh("password", "13800138000", True))
        self.assertTrue(supports_automatic_refresh("qr", "seller@example.com", True))
        self.assertFalse(supports_automatic_refresh("password", "13800138000", False))  # 无密码
        self.assertTrue(supports_automatic_refresh("qr", "", False, True))  # 有 L3 记忆
        self.assertFalse(supports_automatic_refresh("qr", "", False, False))
        self.assertFalse(supports_automatic_refresh("password", "", True))  # 用户名空
        self.assertFalse(supports_automatic_refresh("password", "https://evil", True))  # 误粘 URL


if __name__ == "__main__":
    unittest.main()
