"""session-refresh 端点的会话状态安全护栏。

背景（2026-08-29 线上事故）：账号「寻艺服务」连续两次扫码成功后仍显示「登录状态
已过期」，监听器与订单同步被全部跳过。根因是本端点把「缺续期设备绑定」当成「会话
过期」写进了 account_session_refresh_status，覆盖掉刚扫码成功的 success，并触发
全系统的人工重登闸门。事后用 mtop 直连探测证实该账号会话一直是活的。

前端把「立即刷新 Cookie」点亮的依据是 auto_refresh_supported，而它认的是
has_l3_memory；后端却只认浏览器插件设备绑定。契约错配导致按钮只在健康号上出现、
一点就把健康号打死。

这里锁死两条：
1. 缺设备绑定只能返回错误，绝不允许改写账号会话状态；
2. 有 L3 浏览器记忆时必须改走免密续签，而不是报错了事。
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

import reply_server
from client_browser_login import ClientBrowserError


def _call(cookie_id="acct-1", user_id=7):
    return asyncio.run(
        reply_server.refresh_account_session(
            cookie_id, current_user={"user_id": user_id}
        )
    )


class SessionRefreshBindingTests(unittest.TestCase):
    def setUp(self):
        self.db = Mock()
        self.db.create_client_renewal_task = Mock(
            side_effect=ClientBrowserError(
                "账号尚未绑定可用续期设备",
                error_code="client_device_binding_required",
                http_status=409,
            )
        )
        self.db.get_cookie_details = Mock(return_value={"has_l3_memory": 0})
        self.db.update_account_session_refresh = Mock(return_value=True)
        self.db.get_account_session_refresh = Mock(return_value={"state": "success"})

    def test_missing_binding_never_overwrites_session_state(self):
        with patch.object(reply_server, "db_manager", self.db), \
                patch.object(reply_server, "_require_owned_cookie", Mock()), \
                patch.object(
                    reply_server, "_current_session_refresh_status",
                    Mock(return_value={"state": "success"}),
                ):
            result = _call()

        self.db.update_account_session_refresh.assert_not_called()
        self.assertFalse(result["success"])
        self.assertEqual(result["status"], "client_device_binding_required")

    def test_l3_account_is_routed_to_passwordless_refresh(self):
        self.db.get_cookie_details = Mock(return_value={"has_l3_memory": 1})
        started = AsyncMock(return_value={"success": True, "status": "l3_renewed"})
        with patch.object(reply_server, "db_manager", self.db), \
                patch.object(reply_server, "_require_owned_cookie", Mock()), \
                patch.object(reply_server, "_start_l3_passwordless_refresh", started), \
                patch.object(
                    reply_server, "_current_session_refresh_status",
                    Mock(return_value={"state": "success"}),
                ):
            result = _call()

        started.assert_awaited_once_with("acct-1")
        self.db.update_account_session_refresh.assert_not_called()
        self.assertTrue(result["success"])


if __name__ == "__main__":
    unittest.main()
