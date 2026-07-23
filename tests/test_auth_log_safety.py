import io
import tempfile
import unittest
from unittest.mock import patch

from loguru import logger

import reply_server
from db_manager import DBManager
from utils.xianyu_official_login import OfficialLoginResult, XianyuOfficialLoginService
from utils.qr_verification_browser import QRVerificationBrowser


class AuthLogSafetyTests(unittest.IsolatedAsyncioTestCase):
    def test_session_refresh_sql_logs_redact_stable_account_identity(self):
        stable_identity = "2219255254384"
        output = io.StringIO()
        sink_id = logger.add(output, level="DEBUG", format="{message}")
        database = object.__new__(DBManager)
        database.sql_log_enabled = True
        database.sql_log_level = "DEBUG"
        try:
            database._log_sql(
                "SELECT state FROM account_session_refresh_status "
                "WHERE cookie_id = ?",
                (stable_identity,),
            )
        finally:
            logger.remove(sink_id)

        logged = output.getvalue()
        self.assertNotIn(stable_identity, logged)
        self.assertIn("[REDACTED]", logged)

    def test_validated_hook_returns_fixed_message(self):
        secret = (
            "cookie2=COOKIE_SECRET token=TOKEN_SECRET "
            "https://passport.goofish.com/verify/VERIFY_SECRET"
        )
        output = io.StringIO()
        sink_id = logger.add(output, level="DEBUG", format="{message}")
        try:
            result = XianyuOfficialLoginService._apply_validated_hook(
                OfficialLoginResult(
                    status="success",
                    cookies={"unb": "9988", "cookie2": "session"},
                    unb="9988",
                ),
                lambda _result: (_ for _ in ()).throw(RuntimeError(secret)),
            )
        finally:
            logger.remove(sink_id)

        combined = f"{result.message} {output.getvalue()}"
        for marker in ("COOKIE_SECRET", "TOKEN_SECRET", "VERIFY_SECRET", "passport.goofish.com"):
            self.assertNotIn(marker, combined)
        self.assertEqual(result.error_code, "validated_handoff_failed")

    def test_qr_browser_exception_returns_fixed_message(self):
        secret = (
            "cookie2=COOKIE_SECRET token=TOKEN_SECRET "
            "https://passport.goofish.com/verify/VERIFY_SECRET"
        )
        output = io.StringIO()
        sink_id = logger.add(output, level="DEBUG", format="{message}")
        with tempfile.TemporaryDirectory() as temp_dir:
            browser = QRVerificationBrowser(profile_root=temp_dir)
            try:
                with patch(
                    "playwright.sync_api.sync_playwright",
                    side_effect=RuntimeError(secret),
                ):
                    result = browser.run(
                        "safe-session",
                        "https://passport.goofish.com/verify/input",
                    )
            finally:
                logger.remove(sink_id)

        combined = f"{result} {output.getvalue()}"
        for marker in ("COOKIE_SECRET", "TOKEN_SECRET", "VERIFY_SECRET"):
            self.assertNotIn(marker, combined)
        self.assertEqual(result["message"], "安全验证浏览器处理失败，请重新生成二维码")

    async def test_qr_api_returns_fixed_error_message(self):
        secret = (
            "cookie2=COOKIE_SECRET token=TOKEN_SECRET "
            "https://passport.goofish.com/verify/VERIFY_SECRET"
        )
        output = io.StringIO()
        sink_id = logger.add(output, level="DEBUG", format="{message}")
        try:
            with patch.object(
                reply_server.qr_login_manager,
                "get_session_status",
                side_effect=RuntimeError(secret),
            ):
                result = await reply_server.check_qr_code_status(
                    "safe-session",
                    current_user={"user_id": 7, "username": "operator"},
                )
        finally:
            logger.remove(sink_id)

        combined = f"{result} {output.getvalue()}"
        for marker in ("COOKIE_SECRET", "TOKEN_SECRET", "VERIFY_SECRET", "passport.goofish.com"):
            self.assertNotIn(marker, combined)
        self.assertEqual(result["message"], "扫码登录状态检查失败，请重新扫码")


if __name__ == "__main__":
    unittest.main()
