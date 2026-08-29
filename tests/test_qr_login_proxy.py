"""扫码登录按账号注入住宅代理：per-session 代理绑定与直连原行为回归。

死号重登的前提：重登已有账号时二维码全流程（h5 令牌、登录参数、二维码
生成、状态轮询、安全验证浏览器）都必须从该账号的住宅代理出口发出，
避免机房 IP 触发风控；未配置代理 / 新增账号则保持字节级直连原行为。
"""

import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx

import reply_server
from utils.qr_login import QRLoginManager, QRLoginSession

PROXY_CONFIG = {
    "server": "tunpool-q6keg.qg.net:27349",
    "username": "user-a",
    "password": "p@ss word",
    "bypass": "",
    "region": "泉州",
}
EXPECTED_PROXY_URL = "http://user-a:p%40ss%20word@tunpool-q6keg.qg.net:27349"


class FakeVerificationBrowser:
    def discard_profile(self, session_id):
        del session_id


def _qr_response():
    return httpx.Response(
        200,
        json={
            "content": {
                "success": True,
                "data": {
                    "t": "t",
                    "ck": "ck",
                    "codeContent": "https://qr.example/login",
                },
            }
        },
        request=httpx.Request("GET", "https://passport.goofish.com/qrcode"),
    )


class _ClientFactory:
    """记录每次 httpx.AsyncClient(...) 收到的 proxy 参数。"""

    def __init__(self):
        self.proxies = []

    def __call__(self, **kwargs):
        self.proxies.append(kwargs.get("proxy"))
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.__aexit__.return_value = None
        client.get.return_value = _qr_response()
        client.post.return_value = _qr_response()
        return client


class QRLoginPerSessionProxyTests(unittest.IsolatedAsyncioTestCase):
    def _manager(self, **kwargs):
        manager = QRLoginManager(
            verification_browser=FakeVerificationBrowser(),
            session_validator=AsyncMock(),
            **kwargs,
        )
        manager._get_mh5tk = AsyncMock()
        manager._get_login_params = AsyncMock(return_value={})
        manager._make_qr_data_url = Mock(return_value="data:image/png;base64,safe")
        manager._monitor_qr_status = AsyncMock()
        return manager

    async def test_account_proxy_binds_session_and_all_httpx_clients(self):
        manager = self._manager()
        factory = _ClientFactory()
        with patch("utils.qr_login.httpx.AsyncClient", side_effect=factory):
            result = await manager.generate_qr_code(proxy=PROXY_CONFIG)
            self.assertTrue(result["success"])
            session = manager.sessions[result["session_id"]]
            await manager._poll_qrcode_status(session)

        self.assertEqual(session.proxy, EXPECTED_PROXY_URL)
        self.assertEqual(session.proxy_config, PROXY_CONFIG)
        # 二维码生成阶段 + 状态轮询阶段的每个 client 都走账号代理出口
        self.assertEqual(factory.proxies, [EXPECTED_PROXY_URL, EXPECTED_PROXY_URL])

    async def test_no_proxy_keeps_direct_connection(self):
        manager = self._manager()
        factory = _ClientFactory()
        with patch("utils.qr_login.httpx.AsyncClient", side_effect=factory):
            result = await manager.generate_qr_code()

        self.assertTrue(result["success"])
        session = manager.sessions[result["session_id"]]
        self.assertIsNone(session.proxy)
        self.assertIsNone(session.proxy_config)
        self.assertEqual(factory.proxies, [None])

    async def test_invalid_proxy_never_blocks_login(self):
        manager = self._manager()
        factory = _ClientFactory()
        with patch("utils.qr_login.httpx.AsyncClient", side_effect=factory):
            result = await manager.generate_qr_code(
                proxy={"server": "socks5://unsupported.example:1080"}
            )

        self.assertTrue(result["success"])
        session = manager.sessions[result["session_id"]]
        self.assertIsNone(session.proxy)
        self.assertIsNone(session.proxy_config)
        self.assertEqual(factory.proxies, [None])

    async def test_manager_level_proxy_still_applies_without_session_proxy(self):
        manager = self._manager(proxy=PROXY_CONFIG)
        factory = _ClientFactory()
        with patch("utils.qr_login.httpx.AsyncClient", side_effect=factory):
            result = await manager.generate_qr_code()

        self.assertTrue(result["success"])
        session = manager.sessions[result["session_id"]]
        self.assertEqual(session.proxy, EXPECTED_PROXY_URL)
        self.assertEqual(factory.proxies, [EXPECTED_PROXY_URL])

    async def test_verification_browser_receives_session_proxy_config(self):
        browser = Mock()
        browser.run.return_value = {"status": "cancelled", "screenshot_path": ""}
        manager = QRLoginManager(
            verification_browser=browser,
            session_validator=AsyncMock(),
        )
        session = QRLoginSession(
            "verify-session",
            proxy=EXPECTED_PROXY_URL,
            proxy_config=PROXY_CONFIG,
        )
        session.verification_url = "https://www.goofish.com/im"
        manager.sessions["verify-session"] = session

        await manager._run_verification_browser("verify-session")

        self.assertEqual(browser.run.call_count, 1)
        self.assertEqual(browser.run.call_args.kwargs.get("proxy"), PROXY_CONFIG)


class QRGenerateEndpointProxyTests(unittest.IsolatedAsyncioTestCase):
    """重登带 cid 时端点必须先做归属校验，再把账号代理注入扫码会话。"""

    async def test_generate_with_cid_injects_owned_account_proxy(self):
        received = {}

        class FakeQrManager:
            sessions = {"qr-session": object()}

            async def generate_qr_code(self, **kwargs):
                received.update(kwargs)
                return {
                    "success": True,
                    "session_id": "qr-session",
                    "qr_code_url": "data:image/png;base64,x",
                }

        user = {"user_id": 7, "username": "operator"}
        with (
            patch.object(reply_server, "qr_login_manager", FakeQrManager()),
            patch.object(reply_server, "_require_owned_cookie") as require_owned,
            patch.object(
                reply_server.db_manager,
                "get_account_proxy_config",
                return_value=PROXY_CONFIG,
            ) as get_cfg,
        ):
            result = await reply_server.generate_qr_code(
                payload=reply_server.QRLoginGenerateIn(cid="acct-1"),
                current_user=user,
            )

        self.assertTrue(result["success"])
        require_owned.assert_called_once_with("acct-1", 7)
        get_cfg.assert_called_once_with("acct-1")
        self.assertEqual(received.get("proxy"), PROXY_CONFIG)

    async def test_generate_without_cid_keeps_legacy_manager_call(self):
        class FakeQrManager:
            sessions = {"qr-session": object()}

            async def generate_qr_code(self):  # 老签名：不接受任何参数
                return {
                    "success": True,
                    "session_id": "qr-session",
                    "qr_code_url": "data:image/png;base64,x",
                }

        user = {"user_id": 7, "username": "operator"}
        with patch.object(reply_server, "qr_login_manager", FakeQrManager()):
            result = await reply_server.generate_qr_code(current_user=user)

        self.assertTrue(result["success"])
