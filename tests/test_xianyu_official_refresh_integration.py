import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from account_session_refresh import active_refresh_registry
from XianyuAutoAsync import XianyuLive
from utils.xianyu_official_login import OfficialLoginResult


class FakeRefreshDatabase:
    def __init__(self):
        self.updates = []

    def get_account_session_refresh(self, cookie_id):
        del cookie_id
        return {
            "state": "idle",
            "verification_image_url": "",
        }

    def update_account_session_refresh(self, cookie_id, **kwargs):
        self.updates.append((cookie_id, kwargs))
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
            "unb=9988; cookie2=renewed; _m_h5_tk=token"
        )
        self.assertEqual(database.updates[-1][1]["state"], "success")

    async def test_scheduled_refresh_calls_the_same_official_refresh_path(self):
        live = object.__new__(XianyuLive)
        live.cookie_id = "account-1"
        live.cookie_refresh_lock = asyncio.Lock()
        live.last_cookie_refresh_time = 0
        live.last_message_received_time = 10
        live._format_cookie_refresh_interval = lambda: "24小时"
        live._try_password_login_refresh = AsyncMock(return_value=True)

        await live._execute_cookie_refresh(1234)

        live._try_password_login_refresh.assert_awaited_once_with(
            "定时 Cookie 刷新（每24小时）"
        )
        self.assertEqual(live.last_cookie_refresh_time, 1234)
        self.assertEqual(live.last_message_received_time, 0)


if __name__ == "__main__":
    unittest.main()
