"""需人工重登状态的护栏测试。

覆盖一次真实故障：账号 Cookie 过期后被正确判定为 `manual_reauth_required`，
但 WebSocket 连续失败计数随后用伪造的 `connection_failures` 结果把状态改写成
`failed`，导致监听器闸门失效——账号连续 8.5 小时每 17 秒重连一次，
后台却始终不显示"需重新登录"。

因此这里锁死三件事：
1. 自动路径不得把人工态降级为可重试失败；
2. 人工态必须阻塞监听器建连，而普通可重试失败仍允许重连；
3. 久拖不决的连续失败要抬高退避下限，不再高频打平台。
"""

import unittest
from unittest.mock import AsyncMock, Mock, patch

from XianyuAutoAsync import HUMAN_ACTION_SESSION_STATES, XianyuLive


def _make_live(**overrides):
    """构造一个只带必要属性的实例，避开真实构造函数的登录与网络依赖。"""
    live = object.__new__(XianyuLive)
    live.cookie_id = "account-test"
    live.connection_failures = 1
    live.last_token_refresh_status = None
    live.send_token_refresh_notification = AsyncMock()
    for key, value in overrides.items():
        setattr(live, key, value)
    return live


def _fake_db(state: str, landed_state: str = None):
    """模拟会话状态表：写入前读到 state，写入后读到 landed_state。"""
    db = Mock()
    reads = [{"state": state}]
    if landed_state is not None:
        reads.append({"state": landed_state})
    db.get_account_session_refresh = Mock(side_effect=lambda _cookie_id: reads.pop(0) if len(reads) > 1 else reads[0])
    db.update_account_session_refresh = Mock(return_value=True)
    return db


class RetryableFailureDowngradeTests(unittest.IsolatedAsyncioTestCase):
    async def test_connection_failures_must_not_overwrite_human_action_states(self):
        for state in sorted(HUMAN_ACTION_SESSION_STATES):
            with self.subTest(state=state):
                live = _make_live()
                db = _fake_db(state)
                probe = Mock(error_code="connection_failures", message="连续连接失败")

                with patch("db_manager.db_manager", db):
                    await live._mark_retryable_token_probe_failure(
                        probe,
                        trigger="连续连接失败5次",
                    )

                db.update_account_session_refresh.assert_not_called()
                self.assertEqual(live.last_token_refresh_status, state)

    async def test_genuine_transient_failure_is_still_recorded(self):
        live = _make_live()
        db = _fake_db("idle")
        probe = Mock(error_code="token_probe_failed", message="消息 Token 验证尚未通过")

        with patch("db_manager.db_manager", db):
            await live._mark_retryable_token_probe_failure(probe, trigger="消息 Token 探测")

        db.update_account_session_refresh.assert_called_once()
        self.assertEqual(
            db.update_account_session_refresh.call_args.kwargs["state"],
            "failed",
        )
        self.assertEqual(live.last_token_refresh_status, "retryable_error")


class ListenerGateTests(unittest.TestCase):
    def test_human_action_states_block_the_listener(self):
        for state in sorted(HUMAN_ACTION_SESSION_STATES):
            with self.subTest(state=state):
                self.assertTrue(
                    XianyuLive._session_refresh_blocks_listener({"state": state})
                )

    def test_plain_retryable_failure_still_reconnects(self):
        self.assertFalse(
            XianyuLive._session_refresh_blocks_listener(
                {"state": "failed", "error_code": "connection_failures"}
            )
        )
        self.assertFalse(XianyuLive._session_refresh_blocks_listener({"state": "idle"}))


class ProlongedFailureBackoffTests(unittest.TestCase):
    def test_short_outage_keeps_the_original_fast_retry(self):
        live = _make_live(connection_failures=3)
        # 3 次失败仍走原曲线：min(5*3, 30) = 15 秒，抖动 ±30%
        self.assertLessEqual(live._calculate_retry_delay("some unknown failure"), 15 * 1.3 + 0.01)

    def test_prolonged_outage_escalates_to_minutes(self):
        live = _make_live(connection_failures=25)
        self.assertGreaterEqual(live._calculate_retry_delay("some unknown failure"), 300 * 0.7 - 0.01)

    def test_very_prolonged_outage_escalates_further(self):
        live = _make_live(connection_failures=60)
        self.assertGreaterEqual(live._calculate_retry_delay("some unknown failure"), 1800 * 0.7 - 0.01)


class ManualReauthAlertTests(unittest.IsolatedAsyncioTestCase):
    async def test_alert_fires_once_on_transition(self):
        live = _make_live()
        db = _fake_db("failed", landed_state="manual_reauth_required")

        with patch("db_manager.db_manager", db):
            await live._enter_manual_reauth_required(
                trigger="运行时登录态失效",
                message="当前登录态需要重新扫码",
            )

        self.assertEqual(
            db.update_account_session_refresh.call_args.kwargs["state"],
            "manual_reauth_required",
        )
        live.send_token_refresh_notification.assert_awaited_once()
        self.assertIn("重新扫码", live.send_token_refresh_notification.await_args.args[0])

    async def test_no_repeat_alert_while_already_waiting(self):
        live = _make_live()
        db = _fake_db("manual_reauth_required")

        with patch("db_manager.db_manager", db):
            await live._enter_manual_reauth_required(
                trigger="运行时登录态失效",
                message="当前登录态需要重新扫码",
            )

        live.send_token_refresh_notification.assert_not_awaited()

    async def test_device_renewal_takeover_does_not_nag_for_manual_login(self):
        # 绑定了续期设备时写入会被落成 refreshing，此时不该催人工登录
        live = _make_live()
        db = _fake_db("failed", landed_state="refreshing")

        with patch("db_manager.db_manager", db):
            await live._enter_manual_reauth_required(
                trigger="运行时登录态失效",
                message="当前登录态需要重新扫码",
            )

        live.send_token_refresh_notification.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
