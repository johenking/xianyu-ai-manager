"""官方登录浏览器会话的住宅代理注入。

背景：每账号住宅代理已接入 mtop 探测 / 滑块自愈 / 自动续签 / L3 保活，唯独没接
人工扫码与官方登录——而滑块恰恰在登录这一步弹。没接代理时人工重登仍从机房 IP
出去，等于代理白买。utils.xianyu_official_login.XianyuOfficialLoginService 本来
就支持 proxy 参数，只是 official_login_sessions 建服务时没往下传。

这里锁死：能按 expected_unb 解析出账号代理时必须注入；解析不出（新账号首登）
时保持原行为，一个多余参数都不传。
"""

import unittest
from unittest.mock import Mock, patch

from official_login_sessions import OfficialLoginSessionCoordinator


class _FakeResult:
    succeeded = False
    status = "failed"
    error_code = "test_stop"
    verification_image_path = ""
    unb = ""


class OfficialLoginProxyTests(unittest.IsolatedAsyncioTestCase):
    async def _run_qr(self, factory, expected_unb, proxy_config, cookie_id="acct-1"):
        coordinator = OfficialLoginSessionCoordinator(
            completion_handler=Mock(return_value={}),
            service_factory=factory,
            registry=Mock(),
        )
        db = Mock()
        db.find_cookie_id_by_unb = Mock(return_value=cookie_id)
        db.get_account_proxy_config = Mock(return_value=proxy_config)
        with patch("official_login_sessions.db_manager", db):
            status = await coordinator.start(
                owner_user_id=7, mode="qr", expected_unb=expected_unb
            )
            record = coordinator._sessions[status["session_id"]]
            await record.task
            record.expiry_task.cancel()
        return db

    async def test_proxy_is_injected_when_account_resolvable(self):
        seen = {}

        def factory(**kwargs):
            seen.update(kwargs)
            service = Mock()
            service.login_with_qr = Mock(return_value=_FakeResult())
            return service

        proxy = {"server": "http://tunpool.example:26860", "username": "u", "password": "p"}
        db = await self._run_qr(factory, "123456", proxy)

        db.find_cookie_id_by_unb.assert_called_once_with(7, "123456")
        db.get_account_proxy_config.assert_called_once_with("acct-1")
        self.assertEqual(seen.get("proxy"), proxy)

    async def test_unknown_account_keeps_original_call_shape(self):
        """新账号首登拿不到 unb：保持无代理原行为，一个多余参数都不传。"""
        seen = {"called_with": None}

        def factory(**kwargs):
            seen["called_with"] = kwargs
            service = Mock()
            service.login_with_qr = Mock(return_value=_FakeResult())
            return service

        db = await self._run_qr(factory, "", None)

        db.find_cookie_id_by_unb.assert_not_called()
        self.assertEqual(seen["called_with"], {})

    async def test_account_without_proxy_keeps_original_call_shape(self):
        seen = {"called_with": None}

        def factory(**kwargs):
            seen["called_with"] = kwargs
            service = Mock()
            service.login_with_qr = Mock(return_value=_FakeResult())
            return service

        await self._run_qr(factory, "123456", None)

        self.assertEqual(seen["called_with"], {})

    async def test_proxy_lookup_failure_never_blocks_login(self):
        """代理解析炸了也必须让用户能登进去，绝不因此挡住登录。"""
        seen = {"called_with": None}

        def factory(**kwargs):
            seen["called_with"] = kwargs
            service = Mock()
            service.login_with_qr = Mock(return_value=_FakeResult())
            return service

        coordinator = OfficialLoginSessionCoordinator(
            completion_handler=Mock(return_value={}),
            service_factory=factory,
            registry=Mock(),
        )
        db = Mock()
        db.find_cookie_id_by_unb = Mock(side_effect=RuntimeError("db down"))
        with patch("official_login_sessions.db_manager", db):
            status = await coordinator.start(
                owner_user_id=7, mode="qr", expected_unb="123456"
            )
            record = coordinator._sessions[status["session_id"]]
            await record.task
            record.expiry_task.cancel()

        self.assertEqual(seen["called_with"], {})
        self.assertEqual(record.state, "failed")


if __name__ == "__main__":
    unittest.main()
