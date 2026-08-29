import os
import unittest
from unittest.mock import MagicMock, patch

from utils.browser_runtime import (
    chromium_runtime_options,
    chromium_sandbox_enabled,
    classify_browser_launch_error,
    normalize_proxy_config,
    probe_proxy_egress,
    proxy_config_status,
    proxy_url_with_auth,
)


class BrowserRuntimeTests(unittest.TestCase):
    def test_root_disables_sandbox_and_non_root_keeps_it(self):
        with patch("utils.browser_runtime.os.geteuid", return_value=0):
            self.assertFalse(chromium_sandbox_enabled())
        with patch("utils.browser_runtime.os.geteuid", return_value=501):
            self.assertTrue(chromium_sandbox_enabled())

    def test_unconfigured_channel_uses_bundled_chromium(self):
        with patch.dict(os.environ, {"XIANYU_BROWSER_CHANNEL": ""}):
            self.assertIsNone(chromium_runtime_options()["channel"])
        with patch.dict(os.environ, {"XIANYU_BROWSER_CHANNEL": "chrome"}):
            self.assertEqual(chromium_runtime_options()["channel"], "chrome")

    def test_profile_lock_classification_is_narrow(self):
        self.assertEqual(
            classify_browser_launch_error(RuntimeError("ProcessSingleton lock held")),
            "profile_in_use",
        )
        self.assertEqual(
            classify_browser_launch_error(RuntimeError("SingletonLock exists")),
            "profile_in_use",
        )
        self.assertEqual(
            classify_browser_launch_error(
                RuntimeError("Running as root without --no-sandbox for profile /tmp/fresh")
            ),
            "browser_error",
        )
        self.assertEqual(
            classify_browser_launch_error(
                RuntimeError("Missing X server or $DISPLAY for browser profile")
            ),
            "browser_error",
        )


class ProxyConfigTests(unittest.TestCase):
    def test_unconfigured_proxy_keeps_original_launch_options(self):
        # 没配代理时 chromium_runtime_options 必须与接入前字节级一致：无 proxy 键。
        for value in (None, "", "   ", {}, {"server": ""}, 123, []):
            self.assertIsNone(normalize_proxy_config(value))
            self.assertNotIn("proxy", chromium_runtime_options(value))

    def test_http_proxy_string_and_short_form(self):
        self.assertEqual(
            normalize_proxy_config("http://1.2.3.4:8000"),
            {"server": "http://1.2.3.4:8000"},
        )
        # host:port:user:pass 短格式 → http + 拆出账密
        self.assertEqual(
            normalize_proxy_config("1.2.3.4:8000:alice:secret"),
            {"server": "http://1.2.3.4:8000", "username": "alice", "password": "secret"},
        )
        # 密码含冒号也能正确拆分（只按前三个冒号切）
        self.assertEqual(
            normalize_proxy_config("1.2.3.4:8000:alice:p:a:ss"),
            {"server": "http://1.2.3.4:8000", "username": "alice", "password": "p:a:ss"},
        )

    def test_mapping_and_inline_auth_are_normalized(self):
        self.assertEqual(
            normalize_proxy_config(
                {"server": "http://h:9", "username": "u", "password": "p", "bypass": "*.cdn.com"}
            ),
            {"server": "http://h:9", "username": "u", "password": "p", "bypass": "*.cdn.com"},
        )
        # DB 风格前缀键
        self.assertEqual(
            normalize_proxy_config({"proxy_server": "https://h:9", "proxy_username": "u"}),
            {"server": "https://h:9", "username": "u"},
        )
        # 内联账密 URL 被拆进 username/password（Chromium 会忽略内联账密）
        self.assertEqual(
            normalize_proxy_config("http://u:p@h:9"),
            {"server": "http://h:9", "username": "u", "password": "p"},
        )

    def test_socks_proxy_is_rejected(self):
        # Chromium 不支持 SOCKS5 账密认证：视为未配置，避免"配了却用不了"。
        self.assertIsNone(normalize_proxy_config("socks5://u:p@h:9"))
        self.assertIsNone(normalize_proxy_config({"server": "socks5://h:9"}))

    def test_proxy_config_status_distinguishes_socks_from_unset(self):
        # 未配置 vs 配了但协议不支持，必须能区分（否则 SOCKS5 被当成没配、静默直连）。
        self.assertEqual(proxy_config_status(None)["status"], "not_configured")
        self.assertEqual(proxy_config_status("")["status"], "not_configured")
        self.assertEqual(proxy_config_status({"server": ""})["status"], "not_configured")
        self.assertEqual(proxy_config_status("http://h:9")["status"], "supported")
        self.assertEqual(proxy_config_status("https://h:9")["status"], "supported")
        self.assertEqual(proxy_config_status("1.2.3.4:8000")["status"], "supported")  # 短格式默认 http
        socks = proxy_config_status("socks5://u:p@h:9")
        self.assertEqual(socks["status"], "unsupported_scheme")
        self.assertEqual(socks["scheme"], "socks5")
        self.assertEqual(proxy_config_status({"server": "socks4://h:9"})["scheme"], "socks4")

    def test_probe_egress_socks_returns_explicit_error_not_silent_direct(self):
        # SOCKS5 必须明确报错（而非静默 not_configured/直连），且绝不发起网络请求。
        with patch("httpx.Client", side_effect=AssertionError("SOCKS5 不应发起探测请求")):
            result = probe_proxy_egress("socks5://u:p@h:9")
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "unsupported_scheme")
        self.assertIn("SOCKS5", result["error"])
        self.assertIn("HTTP", result["error"])

    def test_proxy_url_with_auth_encodes_credentials(self):
        self.assertEqual(
            proxy_url_with_auth({"server": "http://h:9", "username": "u@x", "password": "p/w"}),
            "http://u%40x:p%2Fw@h:9",
        )
        # 无账密时原样返回 server
        self.assertEqual(proxy_url_with_auth({"server": "http://h:9"}), "http://h:9")

    def test_probe_egress_not_configured(self):
        result = probe_proxy_egress(None)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "not_configured")

    def test_probe_egress_reports_ip(self):
        fake_client = MagicMock()
        fake_client.__enter__.return_value.get.return_value = MagicMock(text="当前 IP：203.0.113.7 来自广东")
        with patch("httpx.Client", return_value=fake_client):
            result = probe_proxy_egress("http://u:p@h:9", ip_echo_url="https://echo.test")
        self.assertTrue(result["ok"])
        self.assertEqual(result["ip"], "203.0.113.7")

    def test_probe_egress_swallows_errors(self):
        with patch("httpx.Client", side_effect=RuntimeError("boom")):
            result = probe_proxy_egress("http://u:p@h:9")
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "error")


if __name__ == "__main__":
    unittest.main()
