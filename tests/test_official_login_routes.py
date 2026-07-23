import unittest
from unittest.mock import AsyncMock, patch

import reply_server


class FakeCoordinator:
    def __init__(self):
        self.start_calls = []
        self.status = {
            "session_id": "session-1",
            "mode": "qr",
            "state": "waiting_user",
            "message": "等待扫码",
            "error_code": "",
            "qr_image_url": "/static/uploads/images/login.png",
            "verification_image_url": "",
            "account_id": "",
            "is_new_account": False,
            "expires_at": 999,
        }

    async def start(self, **kwargs):
        self.start_calls.append(kwargs)
        result = dict(self.status)
        result["mode"] = kwargs["mode"]
        return result

    async def get_status(self, session_id, owner_user_id):
        if session_id != "session-1" or owner_user_id != 7:
            return None
        return dict(self.status)

    async def wait_until_ready(self, session_id, owner_user_id, timeout):
        del timeout
        return await self.get_status(session_id, owner_user_id)

    async def show_browser(self, session_id, owner_user_id):
        return session_id == "session-1" and owner_user_id == 7

    async def cancel(self, session_id, owner_user_id):
        return session_id == "session-1" and owner_user_id == 7


class OfficialLoginRouteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.coordinator = FakeCoordinator()
        self.user = {"user_id": 7, "username": "operator"}

    async def test_unified_session_routes_return_only_safe_state(self):
        with patch.object(reply_server, "official_login_coordinator", self.coordinator):
            created = await reply_server.create_official_login_session(
                {"mode": "qr"},
                current_user=self.user,
            )
            status = await reply_server.get_official_login_session(
                "session-1",
                current_user=self.user,
            )
            shown = await reply_server.show_official_login_browser(
                "session-1",
                current_user=self.user,
            )
            cancelled = await reply_server.cancel_official_login_session(
                "session-1",
                current_user=self.user,
            )

        self.assertTrue(created["success"])
        self.assertEqual(status["state"], "waiting_user")
        self.assertTrue(shown["success"])
        self.assertTrue(cancelled["success"])
        self.assertNotIn("cookies", status)
        self.assertNotIn("password", status)

    async def test_password_delegates_to_coordinator_and_default_qr_uses_api_manager(self):
        class FakeQrManager:
            sessions = {"api-qr-session": object()}

            async def generate_qr_code(self):
                return {
                    "success": True,
                    "session_id": "api-qr-session",
                    "qr_code_url": "data:image/png;base64,api-qr",
                }

            def cleanup_expired_sessions(self):
                return None

            def get_session_status(self, session_id):
                return {"status": "waiting", "session_id": session_id}

        with (
            patch.object(reply_server, "official_login_coordinator", self.coordinator),
            patch.object(reply_server, "qr_login_manager", FakeQrManager()),
        ):
            password = await reply_server.password_login(
                {
                    "account": "seller@example.com",
                    "password": "secret",
                    "show_browser": True,
                },
                current_user=self.user,
            )
            qr = await reply_server.generate_qr_code(current_user=self.user)
            qr_status = await reply_server.check_qr_code_status(
                "api-qr-session",
                current_user=self.user,
            )

        self.assertTrue(password["success"])
        self.assertEqual(password["session_id"], "session-1")
        self.assertTrue(qr["success"])
        self.assertEqual(qr["qr_code_url"], "data:image/png;base64,api-qr")
        self.assertEqual(qr_status["status"], "waiting")
        self.assertEqual(
            [call["mode"] for call in self.coordinator.start_calls],
            ["password"],
        )

    async def test_sms_window_binds_existing_owned_account_identity(self):
        details = {
            "xianyu_unb": "stable-unb",
            "username": "13800138000",
        }
        database = unittest.mock.Mock()
        database.get_all_cookies.return_value = {"existing-row": "redacted"}
        database.get_cookie_details.return_value = details

        with (
            patch.object(reply_server, "official_login_coordinator", self.coordinator),
            patch.object(reply_server, "db_manager", database),
        ):
            result = await reply_server.official_window_login(
                reply_server.OfficialWindowLoginIn(
                    mode="sms",
                    account="13800138000",
                ),
                current_user=self.user,
            )

        self.assertTrue(result["success"])
        self.assertEqual(
            self.coordinator.start_calls[-1]["expected_unb"],
            "stable-unb",
        )
