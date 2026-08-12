"""WebSocket 可靠性护栏测试。

覆盖三件事：
1. 重连延迟带随机抖动，避免多账号在平台抖动后同时回冲；
2. 业务帧发送有超时上限，不会永久挂起并占住订单锁或履约租约；
3. `heartbeat_timeout` 真正生效——心跳只发不收（半开连接）时主动断开触发重连。
"""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from XianyuAutoAsync import XianyuLive


def _make_live(**overrides):
    """构造一个只带必要属性的实例，避开真实构造函数的登录与网络依赖。"""
    live = object.__new__(XianyuLive)
    live.cookie_id = "account-test"
    live.connection_failures = 1
    live.heartbeat_interval = 15
    live.heartbeat_timeout = 30
    live.last_heartbeat_response = 0.0
    live.last_heartbeat_time = 0.0
    live._safe_str = lambda value: str(value)
    for key, value in overrides.items():
        setattr(live, key, value)
    return live


class RetryDelayJitterTests(unittest.TestCase):
    def test_jitter_stays_within_ratio_and_respects_floor(self):
        self.assertEqual(XianyuLive._apply_jitter(0), 0.0)
        for _ in range(50):
            value = XianyuLive._apply_jitter(10, ratio=0.3)
            self.assertGreaterEqual(value, 7.0)
            self.assertLessEqual(value, 13.0)
        # 极小基数也不应退化成 0 秒空转重连
        self.assertGreaterEqual(XianyuLive._apply_jitter(0.1), 0.5)

    def test_each_error_class_keeps_its_scale_but_varies(self):
        live = _make_live(connection_failures=3)
        cases = {
            "no close frame received or sent": (3 * 3, 15),
            "Connection refused": (10 * 3, 60),
            "some unknown failure": (5 * 3, 30),
        }
        for error_msg, (raw_base, cap) in cases.items():
            base = min(raw_base, cap)
            samples = {live._calculate_retry_delay(error_msg) for _ in range(30)}
            self.assertGreater(len(samples), 1, f"{error_msg} 的延迟应有抖动而非固定值")
            for value in samples:
                self.assertGreaterEqual(value, base * 0.7 - 0.01)
                self.assertLessEqual(value, base * 1.3 + 0.01)


class GuardedSendTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_passes_payload_through(self):
        ws = SimpleNamespace(send=AsyncMock())
        live = _make_live()
        await live._ws_send_guarded(ws, {"lwp": "/ping"})
        ws.send.assert_awaited_once()
        self.assertIn("/ping", ws.send.await_args.args[0])

    async def test_hanging_send_raises_connection_error_instead_of_blocking(self):
        async def never_returns(_payload):
            await asyncio.sleep(3600)

        live = _make_live()
        ws = SimpleNamespace(send=never_returns)
        # 走真实超时路径，只把上限压到毫秒级，避免测试真的等待
        with patch("XianyuAutoAsync.WS_SEND_TIMEOUT", 0.05):
            with self.assertRaises(ConnectionError) as ctx:
                await live._ws_send_guarded(ws, {"lwp": "/!"})
        self.assertIn("判定连接不可用", str(ctx.exception))


class HeartbeatDeadConnectionTests(unittest.IsolatedAsyncioTestCase):
    def _patch_enabled_account(self):
        # 心跳循环内部会 import cookie_manager 判断账号是否启用
        return patch(
            "cookie_manager.manager",
            SimpleNamespace(get_cookie_status=lambda _cookie_id: True),
        )

    async def test_silent_peer_triggers_close_and_exit(self):
        live = _make_live()
        live.send_heartbeat = AsyncMock()
        live._close_dead_websocket = AsyncMock()

        async def sleep_without_any_response(_seconds):
            # 模拟对端始终不回：响应时间戳停留在很久以前
            live.last_heartbeat_response -= 999

        live._interruptible_sleep = AsyncMock(side_effect=sleep_without_any_response)
        ws = SimpleNamespace(closed=False)

        with self._patch_enabled_account():
            await live.heartbeat_loop(ws)

        live._close_dead_websocket.assert_awaited_once_with(ws)
        self.assertEqual(live.send_heartbeat.await_count, 1)

    async def test_responsive_peer_keeps_looping(self):
        live = _make_live()
        live.send_heartbeat = AsyncMock()
        live._close_dead_websocket = AsyncMock()
        rounds = {"n": 0}

        async def sleep_with_fresh_response(_seconds):
            # 模拟对端正常回心跳；跑够三轮后取消，验证期间不会误判假死
            live.last_heartbeat_response = __import__("time").time()
            rounds["n"] += 1
            if rounds["n"] >= 3:
                raise asyncio.CancelledError

        live._interruptible_sleep = AsyncMock(side_effect=sleep_with_fresh_response)
        ws = SimpleNamespace(closed=False)

        with self._patch_enabled_account():
            with self.assertRaises(asyncio.CancelledError):
                await live.heartbeat_loop(ws)

        live._close_dead_websocket.assert_not_awaited()
        self.assertEqual(live.send_heartbeat.await_count, 3)

    async def test_closed_socket_exits_without_declaring_dead(self):
        live = _make_live()
        live.send_heartbeat = AsyncMock()
        live._close_dead_websocket = AsyncMock()
        live._interruptible_sleep = AsyncMock()
        ws = SimpleNamespace(closed=True)

        with self._patch_enabled_account():
            await live.heartbeat_loop(ws)

        live.send_heartbeat.assert_not_awaited()
        live._close_dead_websocket.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
