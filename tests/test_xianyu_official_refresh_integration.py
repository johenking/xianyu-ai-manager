import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from account_session_refresh import active_refresh_registry
from cookie_manager import CookieManager
from XianyuAutoAsync import XianyuLive
from utils.xianyu_official_login import OfficialLoginResult
from utils.xianyu_session_probe import (
    PROBE_SUCCESS,
    PROBE_VERIFICATION_REQUIRED,
    SessionProbeResult,
)


class FakeRefreshDatabase:
    def __init__(self):
        self.updates = []
        self.status = {
            "state": "idle",
            "verification_image_url": "",
        }

    def get_account_session_refresh(self, cookie_id):
        del cookie_id
        return dict(self.status)

    def update_account_session_refresh(self, cookie_id, **kwargs):
        self.updates.append((cookie_id, kwargs))
        self.status.update(kwargs)
        return True

    def get_cookie_details(self, cookie_id):
        del cookie_id
        return {
            "value": "unb=9988; cookie2=old",
            "xianyu_unb": "9988",
            "username": "",
            "password": "",
            "show_browser": False,
        }


class FakeOfficialRefreshService:
    calls = []

    def refresh_session(self, **kwargs):
        self.calls.append(kwargs)
        return OfficialLoginResult(
            status="success",
            cookies={
                "unb": "9988",
                "cookie2": "renewed",
                "_m_h5_tk": "token",
            },
            unb="9988",
            used_password=False,
        )

    @staticmethod
    def cookies_to_string(cookies):
        return "; ".join(f"{name}={value}" for name, value in cookies.items())


class XianyuOfficialRefreshIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        active_refresh_registry.unregister("account-1")
        FakeOfficialRefreshService.calls.clear()

    async def asyncTearDown(self):
        active_refresh_registry.unregister("account-1")
        active_refresh_registry.consume_cancelled("account-1")

    async def test_refresh_uses_persistent_profile_even_without_saved_credentials(self):
        live = object.__new__(XianyuLive)
        live.cookie_id = "account-1"
        live.cookies_str = "unb=9988; cookie2=old"
        live.cookies = {"unb": "9988", "cookie2": "old"}
        live.pending_verification_url = ""
        live._update_cookies_and_restart = AsyncMock(return_value=True)
        live.send_token_refresh_notification = AsyncMock()
        database = FakeRefreshDatabase()

        with (
            patch("db_manager.db_manager", database),
            patch(
                "utils.xianyu_official_login.XianyuOfficialLoginService",
                FakeOfficialRefreshService,
            ),
        ):
            success = await live._try_password_login_refresh("手动立即刷新")

        self.assertTrue(success)
        self.assertEqual(len(FakeOfficialRefreshService.calls), 1)
        refresh_call = FakeOfficialRefreshService.calls[0]
        self.assertEqual(refresh_call["profile_unb"], "9988")
        self.assertEqual(refresh_call["account"], "")
        self.assertEqual(refresh_call["password"], "")
        live._update_cookies_and_restart.assert_awaited_once_with(
            "unb=9988; cookie2=renewed; _m_h5_tk=token",
            browser_user_agent="",
            access_token="",
        )
        self.assertEqual(database.updates[-1][1]["state"], "success")

    async def test_token_failure_does_not_open_browser_when_auto_refresh_is_disabled(self):
        live = object.__new__(XianyuLive)
        live.cookie_id = "account-1"
        live.cookie_refresh_enabled = False
        live.cookies = {"unb": "9988", "cookie2": "old"}
        live.last_token_refresh_status = ""
        live.send_token_refresh_notification = AsyncMock()
        database = FakeRefreshDatabase()

        with (
            patch("db_manager.db_manager", database),
            patch(
                "utils.xianyu_official_login.XianyuOfficialLoginService",
                FakeOfficialRefreshService,
            ),
        ):
            first = await live._try_password_login_refresh("令牌/Session过期")
            second = await live._try_password_login_refresh("连续连接失败5次")

        self.assertFalse(first)
        self.assertFalse(second)
        self.assertEqual(FakeOfficialRefreshService.calls, [])
        self.assertEqual(
            database.updates[-1][1]["error_code"],
            "manual_verification_required",
        )
        self.assertEqual(database.updates[-1][1]["state"], "action_required")
        live.send_token_refresh_notification.assert_awaited_once()

    async def test_message_token_probe_uses_persisted_browser_ua_and_never_starts_browser(self):
        live = object.__new__(XianyuLive)
        live.cookie_id = "account-1"
        live.cookies_str = "unb=9988; cookie2=old; _m_h5_tk=token_1"
        live.cookies = {"unb": "9988", "cookie2": "old", "_m_h5_tk": "token_1"}
        live.myid = "9988"
        live.user_id = 7
        live.browser_user_agent = "Mozilla/5.0 Synthetic Chrome/150.0.0.0"
        live.last_message_received_time = 0
        live.message_cookie_refresh_cooldown = 300
        live.last_token_refresh_status = ""
        live.send_token_refresh_notification = AsyncMock()
        database = FakeRefreshDatabase()
        details = database.get_cookie_details("account-1")
        details["browser_user_agent"] = live.browser_user_agent
        database.get_cookie_details = lambda _cookie_id: dict(details)
        database.update_cookie_account_info = unittest.mock.Mock(return_value=True)
        probe = AsyncMock(return_value=SessionProbeResult(
            status=PROBE_VERIFICATION_REQUIRED,
            cookies=dict(live.cookies),
            verification_url="https://passport.goofish.com/iv/check",
            error_code="human_verification_required",
        ))

        with (
            patch("db_manager.db_manager", database),
            patch("XianyuAutoAsync.probe_message_session_async", probe),
            patch(
                "utils.xianyu_official_login.XianyuOfficialLoginService",
                FakeOfficialRefreshService,
            ),
        ):
            token = await live.refresh_token()

        self.assertIsNone(token)
        probe.assert_awaited_once_with(live.cookies_str, live.browser_user_agent)
        self.assertEqual(database.status["state"], "action_required")
        self.assertEqual(
            live.pending_verification_url,
            "https://passport.goofish.com/iv/check",
        )
        self.assertEqual(FakeOfficialRefreshService.calls, [])

    async def test_successful_probe_sets_token_without_a_second_probe_or_browser(self):
        live = object.__new__(XianyuLive)
        live.cookie_id = "account-1"
        live.cookies_str = "unb=9988; cookie2=old; _m_h5_tk=token_1"
        live.cookies = {"unb": "9988", "cookie2": "old", "_m_h5_tk": "token_1"}
        live.myid = "9988"
        live.user_id = 7
        live.browser_user_agent = "Mozilla/5.0 Synthetic Chrome/150.0.0.0"
        live.last_message_received_time = 0
        live.message_cookie_refresh_cooldown = 300
        live.last_token_refresh_status = ""
        live.current_token = None
        database = FakeRefreshDatabase()
        details = database.get_cookie_details("account-1")
        details["browser_user_agent"] = live.browser_user_agent
        database.get_cookie_details = lambda _cookie_id: dict(details)
        database.update_cookie_account_info = unittest.mock.Mock(return_value=True)
        probe = AsyncMock(return_value=SessionProbeResult(
            status=PROBE_SUCCESS,
            cookies={"unb": "9988", "cookie2": "renewed", "_m_h5_tk": "token_2"},
            access_token="message-access-token",
        ))

        with (
            patch("db_manager.db_manager", database),
            patch("XianyuAutoAsync.probe_message_session_async", probe),
        ):
            token = await live.refresh_token()

        self.assertEqual(token, "message-access-token")
        self.assertEqual(live.current_token, "message-access-token")
        self.assertEqual(probe.await_count, 1)
        database.update_cookie_account_info.assert_called_once()

    async def test_validated_cookie_ua_and_token_install_one_listener_generation(self):
        live = object.__new__(XianyuLive)
        live.cookie_id = "account-1"
        live.cookies_str = "unb=9988; cookie2=old"
        live.cookies = {"unb": "9988", "cookie2": "old"}
        live.myid = "9988"
        live.user_id = 7
        live.browser_user_agent = "Mozilla/5.0 Old Chrome/149.0.0.0"
        live.current_token = "old-token"
        live.last_token_refresh_time = 100.0
        stored = {
            "value": live.cookies_str,
            "browser_user_agent": live.browser_user_agent,
        }

        def update_cookie_account_info(_cookie_id, **kwargs):
            stored.update({
                "value": kwargs.get("cookie_value", stored["value"]),
                "browser_user_agent": kwargs.get(
                    "browser_user_agent",
                    stored["browser_user_agent"],
                ),
            })
            return True

        database = SimpleNamespace(
            get_cookie_details=lambda _cookie_id: dict(stored),
            update_cookie_account_info=unittest.mock.Mock(
                side_effect=update_cookie_account_info
            ),
        )
        manager = SimpleNamespace(
            replace_cookie=AsyncMock(
                return_value={"status": "restarted", "cookie_id": "account-1"}
            )
        )
        user_agent = "Mozilla/5.0 Synthetic Chrome/150.0.0.0"

        with (
            patch("db_manager.db_manager", database),
            patch("cookie_manager.manager", manager),
            patch("XianyuAutoAsync.time.time", return_value=5000.0),
        ):
            updated = await live._update_cookies_and_restart(
                "unb=9988; cookie2=renewed; _m_h5_tk=token_2",
                browser_user_agent=user_agent,
                access_token="message-access-token",
            )

        self.assertTrue(updated)
        manager.replace_cookie.assert_awaited_once()
        handoff = manager.replace_cookie.await_args.kwargs["runtime_state"]
        self.assertEqual(handoff["current_token"], "message-access-token")
        self.assertEqual(handoff["browser_user_agent"], user_agent)
        self.assertEqual(stored["browser_user_agent"], user_agent)

    async def test_scheduled_refresh_calls_the_same_official_refresh_path(self):
        live = object.__new__(XianyuLive)
        live.cookie_id = "account-1"
        live.cookie_refresh_enabled = True
        live.cookie_refresh_lock = asyncio.Lock()
        live.last_cookie_refresh_time = 0
        live.last_message_received_time = 10
        live._format_cookie_refresh_interval = lambda: "24小时"
        live._try_password_login_refresh = AsyncMock(return_value=True)
        live.refresh_cookie_refresh_settings_from_db = lambda: None

        await live._execute_cookie_refresh(1234)

        live._try_password_login_refresh.assert_awaited_once_with(
            "定时 Cookie 刷新（每24小时）"
        )
        self.assertEqual(live.last_cookie_refresh_time, 1234)
        self.assertEqual(live.last_message_received_time, 0)

    async def test_scheduled_refresh_rechecks_the_database_switch_before_running(self):
        live = object.__new__(XianyuLive)
        live.cookie_id = "account-1"
        live.cookie_refresh_enabled = True
        live.cookie_refresh_lock = asyncio.Lock()
        live.last_cookie_refresh_time = 0
        live.last_message_received_time = 10
        live._format_cookie_refresh_interval = lambda: "24小时"
        live._try_password_login_refresh = AsyncMock(return_value=True)

        def disable_from_database():
            live.cookie_refresh_enabled = False

        live.refresh_cookie_refresh_settings_from_db = disable_from_database

        await live._execute_cookie_refresh(1234)

        live._try_password_login_refresh.assert_not_awaited()
        self.assertEqual(live.last_cookie_refresh_time, 0)

    async def test_schedule_reserves_the_next_interval_before_creating_task(self):
        live = object.__new__(XianyuLive)
        live.cookie_id = "account-1"
        live.cookie_refresh_enabled = True
        live.cookie_refresh_interval = 60
        live.last_cookie_refresh_time = 0
        live.last_message_received_time = 0
        live.message_cookie_refresh_cooldown = 0
        live.cookie_refresh_lock = asyncio.Lock()
        live.refresh_cookie_refresh_settings_from_db = lambda: None
        live._format_cookie_refresh_interval = lambda: "1分钟"
        scheduled = []

        async def stop_after_first_iteration(_seconds):
            raise asyncio.CancelledError

        def capture_task(coroutine):
            scheduled.append(coroutine)
            coroutine.close()
            return SimpleNamespace()

        live._interruptible_sleep = stop_after_first_iteration

        with (
            patch("cookie_manager.manager", SimpleNamespace(get_cookie_status=lambda _cookie_id: True)),
            patch("XianyuAutoAsync.time.time", return_value=1234.0),
            patch("XianyuAutoAsync.asyncio.create_task", side_effect=capture_task),
        ):
            with self.assertRaises(asyncio.CancelledError):
                await live.cookie_refresh_loop()

        self.assertEqual(len(scheduled), 1)
        self.assertEqual(live.last_cookie_refresh_time, 1234.0)

    def test_enabling_scheduled_refresh_starts_a_fresh_interval(self):
        live = object.__new__(XianyuLive)
        live.cookie_id = "account-1"
        live.cookie_refresh_enabled = False
        live.cookie_refresh_interval = 86400
        live.last_cookie_refresh_time = 0

        with patch("XianyuAutoAsync.time.time", return_value=4321.0):
            live.configure_cookie_refresh(True, 360)

        self.assertEqual(live.cookie_refresh_interval, 21600)
        self.assertEqual(live.last_cookie_refresh_time, 4321.0)

    async def test_refresh_restart_passes_cooldown_anchors_to_cookie_manager(self):
        class ImmediateThread:
            def __init__(self, target, daemon):
                del daemon
                self.target = target

            def start(self):
                self.target()

        manager = SimpleNamespace(update_cookie=unittest.mock.Mock())
        live = object.__new__(XianyuLive)
        live.cookie_id = "account-1"
        live.cookies_str = "unb=9988; cookie2=renewed"

        with (
            patch("cookie_manager.manager", manager),
            patch("threading.Thread", ImmediateThread),
            patch("time.sleep"),
            patch("XianyuAutoAsync.time.time", return_value=5000.0),
        ):
            await live._restart_instance()

        manager.update_cookie.assert_called_once_with(
            "account-1",
            "unb=9988; cookie2=renewed",
            save_to_db=False,
            runtime_state={
                "cookie_refresh_anchor": 5000.0,
                "item_sync_anchor": 5000.0,
            },
        )

    async def test_cookie_manager_applies_restart_runtime_state_before_main(self):
        created = []

        class FakeLive:
            def __init__(self, cookies, cookie_id, user_id, runtime_state=None):
                created.append((cookies, cookie_id, user_id, runtime_state))

            async def main(self):
                return None

        manager = object.__new__(CookieManager)
        manager.task_status = {}

        with patch("XianyuAutoAsync.XianyuLive", FakeLive):
            await manager._run_xianyu(
                "account-1",
                "unb=9988; cookie2=renewed",
                7,
                runtime_state={
                    "cookie_refresh_anchor": 5000.0,
                    "item_sync_anchor": 5000.0,
                },
            )

        self.assertEqual(
            created,
            [(
                "unb=9988; cookie2=renewed",
                "account-1",
                7,
                {
                    "cookie_refresh_anchor": 5000.0,
                    "item_sync_anchor": 5000.0,
                },
            )],
        )

    async def test_restart_item_sync_anchor_prevents_immediate_browser_work(self):
        live = object.__new__(XianyuLive)
        live.cookie_id = "account-1"
        live.user_id = 7
        live.last_item_sync_time = 5000.0
        live.item_sync_lock = asyncio.Lock()
        live.get_all_items = AsyncMock()
        live._interruptible_sleep = AsyncMock(side_effect=asyncio.CancelledError)
        database = SimpleNamespace(
            get_system_setting=lambda key: {
                "item_sync_enabled": True,
                "item_sync_interval": 3600,
                "item_sync_max_pages": 5,
            }[key],
            get_user_settings=lambda _user_id: {},
        )

        with (
            patch("cookie_manager.manager", SimpleNamespace(get_cookie_status=lambda _cookie_id: True)),
            patch("db_manager.db_manager", database),
            patch("XianyuAutoAsync.time.time", return_value=5001.0),
        ):
            with self.assertRaises(asyncio.CancelledError):
                await live.item_sync_loop()

        live.get_all_items.assert_not_awaited()

    async def test_item_sync_still_runs_after_restart_interval_elapses(self):
        live = object.__new__(XianyuLive)
        live.cookie_id = "account-1"
        live.user_id = 7
        live.last_item_sync_time = 1000.0
        live.item_sync_lock = asyncio.Lock()
        live.get_all_items = AsyncMock(
            return_value={"success": True, "total_count": 2, "total_saved": 2}
        )
        live._interruptible_sleep = AsyncMock(side_effect=asyncio.CancelledError)
        database = SimpleNamespace(
            get_system_setting=lambda key: {
                "item_sync_enabled": True,
                "item_sync_interval": 3600,
                "item_sync_max_pages": 5,
            }[key],
            get_user_settings=lambda _user_id: {},
        )

        with (
            patch("cookie_manager.manager", SimpleNamespace(get_cookie_status=lambda _cookie_id: True)),
            patch("db_manager.db_manager", database),
            patch("XianyuAutoAsync.time.time", return_value=5001.0),
        ):
            with self.assertRaises(asyncio.CancelledError):
                await live.item_sync_loop()

        live.get_all_items.assert_awaited_once_with(page_size=20, max_pages=5)
        self.assertEqual(live.last_item_sync_time, 5001.0)


if __name__ == "__main__":
    unittest.main()
