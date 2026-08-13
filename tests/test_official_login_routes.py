import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from starlette.requests import Request

import reply_server


def _request(*, client_host: str, host: str) -> Request:
    return Request({
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http" if host.startswith(("127.", "localhost")) else "https",
        "path": "/api/official-login/sessions",
        "raw_path": b"/api/official-login/sessions",
        "query_string": b"",
        "headers": [(b"host", host.encode("ascii"))],
        "client": (client_host, 45678),
        "server": (host.split(":", 1)[0], 8091),
    })


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
            "verification_kind": "mobile_scan",
            "required_action": "scan_image",
            "browser_active": True,
            "ended_by": "",
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

    async def interact(self, session_id, owner_user_id, payload):
        if session_id != "session-1":
            raise KeyError(session_id)
        if owner_user_id != 7:
            raise PermissionError(session_id)
        self.last_interaction = payload
        return {"accepted": True, "frame_revision": payload["frame_revision"]}


class OfficialLoginRouteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.coordinator = FakeCoordinator()
        self.user = {"user_id": 7, "username": "operator"}
        self.admin = {"user_id": 7, "username": "admin", "is_admin": True}
        self.local_request = _request(client_host="127.0.0.1", host="127.0.0.1:8091")
        # 正式控制台域名经本机隧道回流：Host 是公网域名、client 地址仍为回环，
        # 与回环同等视为“本机”。
        self.console_request = _request(
            client_host="127.0.0.1",
            host="xianyu.cxywjx.top",
        )
        # 真正的陌生域名（伪造 Host 的探测）必须继续被拒绝。
        self.public_request = _request(
            client_host="127.0.0.1",
            host="evil.example.com",
        )

    async def test_unified_session_routes_return_only_safe_state(self):
        with patch.object(reply_server, "official_login_coordinator", self.coordinator):
            created = await reply_server.create_official_login_session(
                {"mode": "qr"},
                http_request=self.local_request,
                current_user=self.admin,
            )
            status = await reply_server.get_official_login_session(
                "session-1",
                current_user=self.user,
            )
            shown = await reply_server.show_official_login_browser(
                "session-1",
                http_request=self.local_request,
                current_user=self.admin,
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

    async def test_public_legacy_password_requires_client_browser_and_default_qr_uses_api_manager(self):
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
                    "show_browser": False,
                },
                http_request=self.public_request,
                current_user=self.user,
            )
            qr = await reply_server.generate_qr_code(current_user=self.user)
            qr_status = await reply_server.check_qr_code_status(
                "api-qr-session",
                current_user=self.user,
            )

        # 单机自用：旧兼容密码入口对任何已登录来源同样放行服务端官方登录。
        self.assertTrue(password["success"])
        self.assertEqual(self.coordinator.start_calls[-1]["mode"], "password")
        self.assertTrue(qr["success"])
        self.assertEqual(qr["qr_code_url"], "data:image/png;base64,api-qr")
        self.assertEqual(qr_status["status"], "waiting")

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
                http_request=self.local_request,
                current_user=self.admin,
            )

        self.assertTrue(result["success"])
        self.assertEqual(
            self.coordinator.start_calls[-1]["expected_unb"],
            "stable-unb",
        )

    async def test_server_chrome_entry_allows_any_authenticated_console(self):
        # 单机自用：安全边界是控制台登录态（未登录在上游 401）。任何 Host 来源的
        # 已登录请求都放行——经隧道回流的 Host 头由远端 ingress 决定、不可控，
        # 非白名单来源只记观测日志，不再拒绝。
        with patch.object(reply_server, "official_login_coordinator", self.coordinator):
            local_user_session = await reply_server.create_official_login_session(
                {"mode": "qr", "show_browser": True},
                http_request=self.local_request,
                current_user=self.user,
            )
            console_user_session = await reply_server.create_official_login_session(
                {"mode": "qr", "show_browser": True},
                http_request=self.console_request,
                current_user=self.user,
            )
            public_user_session = await reply_server.create_official_login_session(
                {"mode": "qr", "show_browser": True},
                http_request=self.public_request,
                current_user=self.user,
            )
            shown = await reply_server.show_official_login_browser(
                "session-1",
                http_request=self.public_request,
                current_user=self.admin,
            )

        self.assertTrue(local_user_session["success"])
        self.assertTrue(console_user_session["success"])
        self.assertTrue(public_user_session["success"])
        self.assertTrue(shown["success"])

    async def test_authenticated_user_all_modes_start_server_browser_from_any_origin(self):
        # 单机自用：已登录用户从任何来源都能启动全部模式；未登录由上游鉴权 401 拦截。
        with patch.object(reply_server, "official_login_coordinator", self.coordinator):
            for mode in ("qr", "sms", "password"):
                for show_browser in (False, True):
                    with self.subTest(mode=mode, show_browser=show_browser):
                        created = await reply_server.create_official_login_session(
                            {
                                "mode": mode,
                                "account": "fixture-account",
                                "password": "fixture-password",
                                "show_browser": show_browser,
                            },
                            http_request=self.public_request,
                            current_user=self.user,
                        )
                        self.assertTrue(created["success"])

        self.assertEqual(len(self.coordinator.start_calls), 6)

    async def test_owner_scoped_interaction_route_accepts_no_secret_echo(self):
        endpoint = getattr(reply_server, "interact_with_official_login_session", None)
        self.assertIsNotNone(endpoint, "官方登录交互端点尚未实现")
        payload_type = getattr(reply_server, "BrowserInteractionIn", None)
        self.assertIsNotNone(payload_type, "交互请求模型尚未实现")
        payload = payload_type(
            kind="key",
            frame_revision=3,
            key="Enter",
        )

        with patch.object(reply_server, "official_login_coordinator", self.coordinator):
            result = await endpoint(
                "session-1",
                payload,
                current_user=self.user,
            )

        self.assertTrue(result["success"])
        self.assertEqual(result["frame_revision"], 3)
        self.assertNotIn("key", result)

    async def test_qr_interaction_route_is_owner_scoped_and_echoes_no_text(self):
        registry = unittest.mock.Mock()
        registry.get.return_value = {
            "session_id": "qr-session",
            "owner_user_id": 7,
            "expires_at": 9_999_999_999,
        }
        manager = unittest.mock.Mock()
        payload = reply_server.BrowserInteractionIn(
            kind="text",
            frame_revision=4,
            text="482615",
        )

        with (
            patch.object(reply_server, "get_session_registry", return_value=registry),
            patch.object(reply_server, "qr_login_manager", manager),
        ):
            result = await reply_server.interact_with_qr_login_session(
                "qr-session",
                payload,
                current_user=self.user,
            )
            with self.assertRaises(HTTPException) as forbidden:
                await reply_server.interact_with_qr_login_session(
                    "qr-session",
                    payload,
                    current_user={"user_id": 8, "username": "other"},
                )

        self.assertTrue(result["success"])
        self.assertEqual(result["frame_revision"], 4)
        self.assertNotIn("text", result)
        self.assertEqual(forbidden.exception.status_code, 403)
        manager.submit_interaction.assert_called_once()
