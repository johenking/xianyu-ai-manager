import asyncio
import io
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import httpx
from loguru import logger

from utils.qr_login import QRLoginManager, QRLoginSession
from utils.xianyu_session_probe import SessionProbeResult


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
            session.verification_screenshot_path = str(screenshot)
            manager.sessions[session.session_id] = session

            with patch.dict(
                os.environ,
                {"XIANYU_PRIVATE_VERIFICATION_DIR": temp_dir},
            ):
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

    async def test_verification_probe_starts_renderer_and_propagates_action_state(self):
        validator = AsyncMock(return_value=SessionProbeResult(
            status="verification_required",
            cookies={"unb": "account-1", "cookie2": "session"},
            verification_url="https://passport.goofish.com/verify",
            error_code="human_verification_required",
        ))
        manager = QRLoginManager(
            verification_browser=FakeVerificationBrowser(),
            session_validator=validator,
        )
        session = QRLoginSession("verification-session")
        session.cookies = {"unb": "account-1", "cookie2": "session"}
        session.unb = "account-1"
        manager.sessions[session.session_id] = session
        manager._ensure_verification_browser = Mock()

        validated = await manager._validate_candidate_session(session)

        self.assertFalse(validated)
        self.assertEqual(session.status, "verification_required")
        self.assertEqual(session.required_action, "render_verification")
        manager._ensure_verification_browser.assert_called_once_with(session.session_id)

        pending = asyncio.create_task(asyncio.sleep(60))
        session.verification_task = pending
        manager._apply_verification_browser_update(session.session_id, {
            "verification_browser_status": "waiting",
            "verification_screenshot_path": "/static/uploads/images/verification.png",
            "verification_kind": "interactive",
            "required_action": "use_local_chrome",
        })
        status = manager.get_session_status(session.session_id)
        self.assertEqual(status["verification_kind"], "interactive")
        self.assertEqual(status["required_action"], "use_local_chrome")
        self.assertTrue(status["browser_active"])
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)

    async def test_success_status_without_access_token_is_not_accepted(self):
        validator = AsyncMock(return_value=SessionProbeResult(
            status="success",
            cookies={"unb": "account-1", "cookie2": "session"},
        ))
        manager = QRLoginManager(
            verification_browser=FakeVerificationBrowser(),
            session_validator=validator,
        )
        session = QRLoginSession("tokenless-session")
        session.cookies = {"unb": "account-1", "cookie2": "session"}
        session.unb = "account-1"
        manager.sessions[session.session_id] = session

        validated = await manager._validate_candidate_session(session)

        self.assertFalse(validated)
        self.assertFalse(session.validated)
        self.assertEqual(session.status, "error")
        self.assertEqual(session.ended_by, "validation_failed")

    async def test_switching_to_extension_ends_qr_session_explicitly(self):
        manager = QRLoginManager(
            verification_browser=FakeVerificationBrowser(),
            session_validator=AsyncMock(),
        )
        session = QRLoginSession("switch-session")
        session.status = "verification_required"
        session.verification_url = "https://passport.goofish.com/verify"
        manager.sessions[session.session_id] = session

        status = manager.cancel_session(
            session.session_id,
            ended_by="switched_to_extension",
        )

        self.assertEqual(status["status"], "cancelled")
        self.assertEqual(status["ended_by"], "switched_to_extension")
        self.assertFalse(status["browser_active"])

    async def test_cancelled_renderer_removes_its_late_screenshot(self):
        class CancelledVerificationBrowser(FakeVerificationBrowser):
            screenshot_path = ""

            def run(self, *_args, **_kwargs):
                return {
                    "status": "cancelled",
                    "screenshot_path": self.screenshot_path,
                }

        verification_browser = CancelledVerificationBrowser()
        manager = QRLoginManager(
            verification_browser=verification_browser,
            session_validator=AsyncMock(),
        )
        session = QRLoginSession("cancelled-renderer-session")
        session.status = "verification_required"
        session.verification_url = "https://passport.goofish.com/verify"
        manager.sessions[session.session_id] = session

        with tempfile.TemporaryDirectory() as temp_dir:
            screenshot = Path(temp_dir) / "late-cancelled.png"
            screenshot.write_bytes(b"late private screenshot")
            verification_browser.screenshot_path = str(screenshot)
            with patch.dict(
                os.environ,
                {"XIANYU_PRIVATE_VERIFICATION_DIR": temp_dir},
            ):
                await manager._run_verification_browser(session.session_id)

            self.assertFalse(screenshot.exists())
            self.assertEqual(session.verification_browser_status, "cancelled")


if __name__ == "__main__":
    unittest.main()
