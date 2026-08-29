"""L3 主动保活的调度与安全护栏测试。

背景（2026-08-29 诊断）：L3 免密续签此前只在会话死亡后被动触发，而「快速进入」
恰恰要求 cookie2 仍然有效——于是线上全部账号 fast_entry_unavailable，L3 形同虚设。
主动保活让有 L3 记忆的账号趁会话有效期内提前续签。

这里锁死的行为：
1. `_l3_keepalive_due` 纯调度语义：开关关闭/间隔非法/未到点一律 False；
2. `_execute_l3_keepalive` 的安全铁律——任何失败路径都不得打扰现有会话：
   不清记忆标记、不标过期、不触发人工重登；只有真正拿到新 Cookie 才交接监听。
"""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from XianyuAutoAsync import XianyuLive


def _make_live(**overrides):
    """构造只带必要属性的实例，避开真实构造函数的登录与网络依赖。"""
    live = object.__new__(XianyuLive)
    live.cookie_id = "acct-l3"
    live.cookies_str = "unb=123; cookie2=abc"
    live.browser_user_agent = "Mozilla/5.0 test"
    live.l3_keepalive_lock = asyncio.Lock()
    live.cookie_refresh_lock = asyncio.Lock()
    live._recover_via_passwordless_refresh = AsyncMock(return_value="")
    live._update_cookies_and_restart = AsyncMock(return_value=True)
    for key, value in overrides.items():
        setattr(live, key, value)
    return live


def _fake_db(has_l3_memory=1, unb="123", proxy=None):
    db = Mock()
    db.get_cookie_details = Mock(
        return_value={"has_l3_memory": has_l3_memory, "xianyu_unb": unb}
    )
    db.mark_cookie_validated = Mock(return_value=True)
    # 默认无代理：健康门禁必须直接放行（与接入代理前行为一致）。
    db.get_account_proxy_config = Mock(return_value=proxy)
    db.record_proxy_probe = Mock(return_value=True)
    return db


def _fake_registry(active=False):
    registry = Mock()
    registry.is_active = Mock(return_value=active)
    return registry


class KeepaliveDueTests(unittest.TestCase):
    def test_disabled_never_due(self):
        self.assertFalse(
            XianyuLive._l3_keepalive_due(10_000, 0, 60, enabled=False)
        )

    def test_non_positive_interval_never_due(self):
        for interval in (0, -1):
            with self.subTest(interval=interval):
                self.assertFalse(
                    XianyuLive._l3_keepalive_due(10_000, 0, interval, enabled=True)
                )

    def test_not_yet_due(self):
        self.assertFalse(
            XianyuLive._l3_keepalive_due(100, 90, 60, enabled=True)
        )

    def test_due_after_interval(self):
        self.assertTrue(
            XianyuLive._l3_keepalive_due(151, 90, 60, enabled=True)
        )

    def test_never_ran_treated_as_epoch(self):
        self.assertTrue(
            XianyuLive._l3_keepalive_due(10_000, None, 60, enabled=True)
        )


class ExecuteKeepaliveTests(unittest.IsolatedAsyncioTestCase):
    async def test_global_switch_off_is_hard_noop(self):
        live = _make_live()
        db = _fake_db()
        with patch("XianyuAutoAsync.L3_KEEPALIVE_ENABLED", False), \
                patch("db_manager.db_manager", db):
            await live._execute_l3_keepalive()
        db.get_cookie_details.assert_not_called()
        live._recover_via_passwordless_refresh.assert_not_called()

    async def test_skips_account_without_l3_memory(self):
        live = _make_live()
        db = _fake_db(has_l3_memory=0)
        with patch("XianyuAutoAsync.L3_KEEPALIVE_ENABLED", True), \
                patch("db_manager.db_manager", db), \
                patch("account_session_refresh.active_refresh_registry", _fake_registry()):
            await live._execute_l3_keepalive()
        live._recover_via_passwordless_refresh.assert_not_called()

    async def test_skips_when_manual_refresh_in_progress(self):
        live = _make_live()
        db = _fake_db()
        with patch("XianyuAutoAsync.L3_KEEPALIVE_ENABLED", True), \
                patch("db_manager.db_manager", db), \
                patch("account_session_refresh.active_refresh_registry", _fake_registry(active=True)):
            await live._execute_l3_keepalive()
        db.get_cookie_details.assert_not_called()
        live._recover_via_passwordless_refresh.assert_not_called()

    async def test_skips_while_cookie_refresh_lock_held(self):
        live = _make_live()
        db = _fake_db()
        await live.cookie_refresh_lock.acquire()
        try:
            with patch("XianyuAutoAsync.L3_KEEPALIVE_ENABLED", True), \
                    patch("db_manager.db_manager", db), \
                    patch("account_session_refresh.active_refresh_registry", _fake_registry()):
                await live._execute_l3_keepalive()
        finally:
            live.cookie_refresh_lock.release()
        live._recover_via_passwordless_refresh.assert_not_called()

    async def test_success_hands_over_listener_and_marks_validated(self):
        live = _make_live()
        live._recover_via_passwordless_refresh = AsyncMock(return_value="unb=123; cookie2=new")
        db = _fake_db(unb="123")
        with patch("XianyuAutoAsync.L3_KEEPALIVE_ENABLED", True), \
                patch("db_manager.db_manager", db), \
                patch("account_session_refresh.active_refresh_registry", _fake_registry()):
            await live._execute_l3_keepalive()

        live._recover_via_passwordless_refresh.assert_awaited_once_with(
            "123", live.cookies_str, "L3主动保活"
        )
        live._update_cookies_and_restart.assert_awaited_once()
        kwargs = live._update_cookies_and_restart.await_args.kwargs
        self.assertEqual(kwargs["expected_xianyu_unb"], "123")
        db.mark_cookie_validated.assert_called_once_with("acct-l3")

    async def test_refresh_miss_leaves_session_untouched(self):
        live = _make_live()
        live._recover_via_passwordless_refresh = AsyncMock(return_value="")
        live._last_l3_error_code = "session_probe_retryable"
        live._reseed_l3_memory = AsyncMock()
        db = _fake_db()
        with patch("XianyuAutoAsync.L3_KEEPALIVE_ENABLED", True), \
                patch("db_manager.db_manager", db), \
                patch("account_session_refresh.active_refresh_registry", _fake_registry()):
            await live._execute_l3_keepalive()

        live._update_cookies_and_restart.assert_not_awaited()
        live._reseed_l3_memory.assert_not_awaited()
        db.mark_cookie_validated.assert_not_called()
        db.mark_cookie_expired.assert_not_called()

    async def test_dead_memory_triggers_reseed_while_session_alive(self):
        """fast_entry_unavailable = 记忆死了但会话还活着 → 趁活重建档案。"""
        live = _make_live()
        live._recover_via_passwordless_refresh = AsyncMock(return_value="")
        live._last_l3_error_code = "fast_entry_unavailable"
        live._reseed_l3_memory = AsyncMock()
        db = _fake_db(unb="123")
        with patch("XianyuAutoAsync.L3_KEEPALIVE_ENABLED", True), \
                patch("db_manager.db_manager", db), \
                patch("account_session_refresh.active_refresh_registry", _fake_registry()):
            await live._execute_l3_keepalive()

        live._reseed_l3_memory.assert_awaited_once_with("123")
        live._update_cookies_and_restart.assert_not_awaited()

    async def test_refresh_exception_is_swallowed(self):
        live = _make_live()
        live._recover_via_passwordless_refresh = AsyncMock(side_effect=RuntimeError("boom"))
        live._safe_str = staticmethod(str)
        db = _fake_db()
        with patch("XianyuAutoAsync.L3_KEEPALIVE_ENABLED", True), \
                patch("db_manager.db_manager", db), \
                patch("account_session_refresh.active_refresh_registry", _fake_registry()):
            await live._execute_l3_keepalive()

        live._update_cookies_and_restart.assert_not_awaited()
        db.mark_cookie_validated.assert_not_called()

    async def test_unhealthy_proxy_blocks_keepalive_before_browser(self):
        """配了坏代理（SOCKS5）→ 保活直接跳过，绝不带着机房直连去打 passport。"""
        live = _make_live()
        db = _fake_db(proxy={"server": "socks5://u:p@h:9", "username": "u", "password": "p"})
        with patch("XianyuAutoAsync.L3_KEEPALIVE_ENABLED", True), \
                patch("db_manager.db_manager", db), \
                patch("account_session_refresh.active_refresh_registry", _fake_registry()):
            await live._execute_l3_keepalive()

        live._recover_via_passwordless_refresh.assert_not_awaited()
        live._update_cookies_and_restart.assert_not_awaited()
        db.record_proxy_probe.assert_called_once_with(
            "acct-l3", ip="", status="unsupported_scheme"
        )


class ProxyPreflightTests(unittest.IsolatedAsyncioTestCase):
    """代理健康门禁：无代理零打扰放行；坏代理拒绝放行且落库状态。"""

    async def test_no_proxy_passes_without_any_probe(self):
        live = _make_live()
        db = _fake_db(proxy=None)
        with patch("db_manager.db_manager", db), \
                patch("utils.browser_runtime.probe_proxy_egress") as probe:
            self.assertTrue(await live._proxy_preflight_ok("测试"))
        probe.assert_not_called()
        db.record_proxy_probe.assert_not_called()

    async def test_unsupported_scheme_blocks_without_network(self):
        live = _make_live()
        db = _fake_db(proxy={"server": "socks5://h:9"})
        with patch("db_manager.db_manager", db), \
                patch("utils.browser_runtime.probe_proxy_egress") as probe:
            self.assertFalse(await live._proxy_preflight_ok("测试"))
        probe.assert_not_called()
        db.record_proxy_probe.assert_called_once_with(
            "acct-l3", ip="", status="unsupported_scheme"
        )

    async def test_probe_failure_blocks_and_records_status(self):
        live = _make_live()
        db = _fake_db(proxy={"server": "http://h:9"})
        failed = {"ok": False, "ip": "", "status": "proxy_error", "error": "x"}
        with patch("db_manager.db_manager", db), \
                patch("utils.browser_runtime.probe_proxy_egress", return_value=failed):
            self.assertFalse(await live._proxy_preflight_ok("测试"))
        db.record_proxy_probe.assert_called_once_with(
            "acct-l3", ip="", status="proxy_error"
        )

    async def test_healthy_proxy_passes_and_records_egress_ip(self):
        live = _make_live()
        db = _fake_db(proxy={"server": "http://h:9"})
        ok = {"ok": True, "ip": "1.2.3.4", "status": "ok", "error": ""}
        with patch("db_manager.db_manager", db), \
                patch("utils.browser_runtime.probe_proxy_egress", return_value=ok):
            self.assertTrue(await live._proxy_preflight_ok("测试"))
        db.record_proxy_probe.assert_called_once_with(
            "acct-l3", ip="1.2.3.4", status="ok"
        )


class ReseedTests(unittest.IsolatedAsyncioTestCase):
    """记忆重建：验证结论如实回写 DB，换发过会话必须交接监听。"""

    def _seed_result(self, **overrides):
        base = dict(
            status="success",
            has_l3_memory=True,
            quick_entry_verified=True,
            cookies={"unb": "123", "cookie2": "renewed", "_m_h5_tk": "tk"},
            browser_user_agent="UA-seed",
            error_code="",
        )
        base.update(overrides)
        return SimpleNamespace(**base)

    async def test_verified_reseed_marks_ready_and_hands_over(self):
        live = _make_live()
        db = _fake_db()
        db.get_account_proxy_config = Mock(return_value=None)
        db.mark_l3_memory = Mock(return_value=True)
        seeded = self._seed_result()
        with patch("db_manager.db_manager", db), \
                patch("utils.xianyu_l3_memory.seed_profile_from_cookies", Mock(return_value=seeded)) as seed:
            await live._reseed_l3_memory("123")

        seed.assert_called_once_with("123", live.cookies_str, proxy=None)
        db.mark_l3_memory.assert_called_once_with("acct-l3", ready=True)
        live._update_cookies_and_restart.assert_awaited_once()
        kwargs = live._update_cookies_and_restart.await_args.kwargs
        self.assertEqual(kwargs["expected_xianyu_unb"], "123")
        db.mark_cookie_validated.assert_called_once_with("acct-l3")

    async def test_failed_reseed_marks_memory_dead_without_handover(self):
        live = _make_live()
        db = _fake_db()
        db.get_account_proxy_config = Mock(return_value=None)
        db.mark_l3_memory = Mock(return_value=True)
        seeded = self._seed_result(
            status="success",
            has_l3_memory=False,
            quick_entry_verified=False,
            error_code="quick_entry_unverified",
        )
        with patch("db_manager.db_manager", db), \
                patch("utils.xianyu_l3_memory.seed_profile_from_cookies", Mock(return_value=seeded)):
            await live._reseed_l3_memory("123")

        db.mark_l3_memory.assert_called_once_with("acct-l3", ready=False)
        live._update_cookies_and_restart.assert_not_awaited()
        db.mark_cookie_validated.assert_not_called()

    async def test_unverified_reseed_marks_ready_but_never_touches_listener(self):
        """验证结论不明（None）：标记记忆可用，但绝不交接监听（会话没换发）。"""
        live = _make_live()
        db = _fake_db()
        db.get_account_proxy_config = Mock(return_value=None)
        db.mark_l3_memory = Mock(return_value=True)
        seeded = self._seed_result(quick_entry_verified=None)
        with patch("db_manager.db_manager", db), \
                patch("utils.xianyu_l3_memory.seed_profile_from_cookies", Mock(return_value=seeded)):
            await live._reseed_l3_memory("123")

        db.mark_l3_memory.assert_called_once_with("acct-l3", ready=True)
        live._update_cookies_and_restart.assert_not_awaited()

    async def test_reseed_exception_is_swallowed_and_flag_untouched(self):
        live = _make_live()
        live._safe_str = staticmethod(str)
        db = _fake_db()
        db.get_account_proxy_config = Mock(return_value=None)
        db.mark_l3_memory = Mock(return_value=True)
        with patch("db_manager.db_manager", db), \
                patch(
                    "utils.xianyu_l3_memory.seed_profile_from_cookies",
                    Mock(side_effect=RuntimeError("browser down")),
                ):
            await live._reseed_l3_memory("123")

        db.mark_l3_memory.assert_not_called()
        live._update_cookies_and_restart.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
