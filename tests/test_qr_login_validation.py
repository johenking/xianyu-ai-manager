import asyncio
import io
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import httpx
from loguru import logger

from utils.qr_login import QRLoginManager, QRLoginSession


class FakeVerificationBrowser:
    def discard_profile(self, session_id):
        del session_id


class QRLoginValidationTests(unittest.IsolatedAsyncioTestCase):
    async def _generate_qr_with_response(self, response):
        manager = QRLoginManager(
            verification_browser=FakeVerificationBrowser(),
            session_validator=AsyncMock(),
        )
        manager._get_mh5tk = AsyncMock()
        manager._get_login_params = AsyncMock(return_value={})
        manager._make_qr_data_url = Mock(return_value="data:image/png;base64,safe")
        manager._monitor_qr_status = AsyncMock()
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.__aexit__.return_value = None
        client.get.return_value = response
        output = io.StringIO()
        sink_id = logger.add(output, level="DEBUG", format="{message}")
        try:
            with patch("utils.qr_login.httpx.AsyncClient", return_value=client):
                result = await manager.generate_qr_code()
                await asyncio.sleep(0)
        finally:
            logger.remove(sink_id)
        return result, output.getvalue()

    async def test_qr_generation_logs_only_safe_response_summary(self):
        qr_url = "https://passport.goofish.com/login?token=QR_SECRET"
        response = httpx.Response(
            200,
            json={
                "content": {
                    "success": True,
                    "data": {
                        "t": "POLL_SECRET",
                        "ck": "COOKIE_KEY_SECRET",
                        "codeContent": qr_url,
                    },
                }
            },
            request=httpx.Request("GET", "https://passport.goofish.com/qrcode"),
        )

        result, logs = await self._generate_qr_with_response(response)

        self.assertTrue(result["success"])
        self.assertIn("has_code_content=True", logs)
        for secret in (qr_url, "QR_SECRET", "POLL_SECRET", "COOKIE_KEY_SECRET"):
            self.assertNotIn(secret, logs)

    async def test_invalid_qr_body_is_not_logged_or_returned(self):
        private_body = (
            "cookie2=COOKIE_SECRET token=TOKEN_SECRET "
            "https://passport.goofish.com/verify/VERIFY_SECRET"
        )
        response = httpx.Response(
            502,
            text=private_body,
            headers={"content-type": "text/html"},
            request=httpx.Request("GET", "https://passport.goofish.com/qrcode"),
        )

        result, logs = await self._generate_qr_with_response(response)

        self.assertFalse(result["success"])
        for secret in (private_body, "COOKIE_SECRET", "TOKEN_SECRET", "VERIFY_SECRET"):
            self.assertNotIn(secret, logs)
            self.assertNotIn(secret, result["message"])

    async def test_expired_terminal_state_is_stable_until_retention_ends(self):
        manager = QRLoginManager(
            verification_browser=FakeVerificationBrowser(),
            session_validator=AsyncMock(),
            terminal_retention_seconds=300,
        )
        session = QRLoginSession("expired-session")
        session.created_time = time.time() - session.expire_time - 1
        with tempfile.TemporaryDirectory() as temp_dir:
            screenshot = Path(temp_dir) / "verification.png"
            screenshot.write_bytes(b"private screenshot")
            session.verification_screenshot_path = (
                "/static/uploads/images/verification.png"
            )
            manager.sessions[session.session_id] = session

            with patch("utils.qr_verification_browser.UPLOAD_DIR", temp_dir):
                manager.cleanup_expired_sessions()
                first = manager.get_session_status(session.session_id)
                second = manager.get_session_status(session.session_id)

            self.assertEqual(first, second)
            self.assertEqual(first["status"], "expired")
            self.assertEqual(first["message"], "二维码已过期，请重新扫码")
            self.assertFalse(screenshot.exists())
            terminal_at = manager.sessions[session.session_id].terminal_at

            manager.cleanup_expired_sessions(now=terminal_at + 299)
            self.assertIn(session.session_id, manager.sessions)
            manager.cleanup_expired_sessions(now=terminal_at + 301)
            self.assertNotIn(session.session_id, manager.sessions)


if __name__ == "__main__":
    unittest.main()
