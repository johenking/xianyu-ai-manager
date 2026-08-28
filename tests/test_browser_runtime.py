import os
import unittest
from unittest.mock import patch

from utils.browser_runtime import (
    chromium_runtime_options,
    chromium_sandbox_enabled,
    classify_browser_launch_error,
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


if __name__ == "__main__":
    unittest.main()
