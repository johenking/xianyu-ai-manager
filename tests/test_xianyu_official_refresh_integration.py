import asyncio
import io
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from loguru import logger

from account_session_refresh import active_refresh_registry
from cookie_manager import CookieManager
from XianyuAutoAsync import XianyuLive
from utils.xianyu_official_login import OfficialLoginResult
from utils.xianyu_session_probe import (
    PROBE_EXPIRED,
    PROBE_RETRYABLE_ERROR,
    PROBE_SUCCESS,
    PROBE_VERIFICATION_REQUIRED,
    SessionProbeResult,
)


class FakeRefreshDatabase:
    def __init__(self, *, login_method="password", username="seller@example.com", password="secret"):
        self.updates = []
        self.status = {
            "state": "idle",
            "verification_image_url": "",
        }
        self.expired_calls = 0
        self.validated_calls = 0
        self.cas_calls = 0
        self.details = {
            "value": "unb=9988; cookie2=old",
            "xianyu_unb": "9988",
            "username": username,
            "password": password,
            "show_browser": False,
            "user_id": 7,
            "cookie_revision": 3,
            "login_method": login_method,
            "browser_user_agent": "",
            "has_l3_memory": False,
        }
        self.l3_marks = []

    def get_account_session_refresh(self, cookie_id):
        del cookie_id
        return dict(self.status)

    def update_account_session_refresh(self, cookie_id, **kwargs):
        self.updates.append((cookie_id, kwargs))
        self.status.update(kwargs)
        return True

    def get_cookie_details(self, cookie_id):
        del cookie_id
        return dict(self.details)

    def get_account_proxy_config(self, cookie_id):
        del cookie_id
        return None

    def mark_cookie_expired(self, cookie_id):
        del cookie_id
        self.expired_calls += 1
        return True

    def mark_cookie_validated(self, cookie_id):
        del cookie_id
        self.validated_calls += 1
        return True

    def mark_l3_memory(self, cookie_id, *, ready):
        self.l3_marks.append((cookie_id, ready))
        self.details["has_l3_memory"] = bool(ready)
        return True

    def compare_and_swap_cookie_session(self, cookie_id, **kwargs):
        del cookie_id
        self.cas_calls += 1
        self.details["value"] = kwargs["cookie_value"]
        self.details["browser_user_agent"] = kwargs.get("browser_user_agent", "")
        self.details["cookie_revision"] += 1
        return {
            "state": "updated",
            "cookie_revision": self.details["cookie_revision"],
        }


class FakeOfficialRefreshService:
    calls = []

    def __init__(self, **kwargs):
        # 生产代码会以 proxy= 等关键字实例化官方登录服务；fake 一律忽略。
        del kwargs

    # 模块首次导入若发生在本 fake 的 patch 生效期间，L3MemoryService 单例
    # 构造会引用这个类属性；给个空工厂避免 AttributeError。
    @staticmethod
    def _default_playwright_factory():
        return None

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


class FailingOfficialRefreshService(FakeOfficialRefreshService):
    error_code = "invalid_credentials"
    status = "failed"

    def refresh_session(self, **kwargs):
        self.calls.append(kwargs)
        return OfficialLoginResult(
            status=self.status,
            error_code=self.error_code,
            message="sensitive provider message",
        )


class XianyuOfficialRefreshIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_manual_reauth_state_waits_without_opening_websocket(self):
        live = object.__new__(XianyuLive)
        live.cookie_id = "account-1"
        live.cookies_str = "unb=9988; cookie2=expired"
        live.browser_user_agent = "Mozilla/5.0 Synthetic Chrome/150.0.0.0"
        live.base_url = "wss://example.invalid"
        live.current_token = None
        live.heartbeat_task = None
        live.token_refresh_task = None
        live.cleanup_task = None
        live.cookie_refresh_task = None
        live.background_tasks = set()
        live.create_session = AsyncMock()
        live._interruptible_sleep = AsyncMock(side_effect=asyncio.CancelledError)
        live._create_websocket_connection = AsyncMock(
            side_effect=asyncio.CancelledError
        )
        live._set_connection_state = unittest.mock.Mock()
        live.close_session = AsyncMock()
        live._unregister_instance = unittest.mock.Mock()
        database = SimpleNamespace(
            get_account_session_refresh=lambda _cookie_id: {
                "state": "manual_reauth_required"
            }
        )

        with (
            patch(
                "cookie_manager.manager",
                SimpleNamespace(get_cookie_status=lambda _cookie_id: True),
            ),
            patch("XianyuAutoAsync.db_manager", database),
        ):
            with self.assertRaises(asyncio.CancelledError):
                await live.main()

        live._interruptible_sleep.assert_awaited_once_with(30)
        live._create_websocket_connection.assert_not_awaited()

    def test_expected_websocket_disconnect_avoids_error_and_traceback_logs(self):
        output = io.StringIO()
        sink_id = logger.add(output, level="DEBUG", format="{level}|{message}")
        try:
            expected = XianyuLive._log_websocket_connection_failure(
                "2219255254384",
                error_type="ConnectionClosedError",
                error_message=(
                    "no close frame received or sent "
                    "cookie2=COOKIE_SECRET "
                    "https://passport.goofish.com/verify/VERIFY_SECRET"
                ),
                failure_count=1,
                max_failures=5,
            )
        finally:
            logger.remove(sink_id)

        logged = output.getvalue()
        self.assertTrue(expected)
        self.assertIn("WARNING|", logged)
        self.assertNotIn("ERROR|", logged)
        self.assertNotIn("Traceback", logged)
        self.assertNotIn("COOKIE_SECRET", logged)
        self.assertNotIn("VERIFY_SECRET", logged)

    def test_unexpected_websocket_failure_keeps_redacted_error_summary(self):
        output = io.StringIO()
        sink_id = logger.add(output, level="DEBUG", format="{level}|{message}")
        try:
            expected = XianyuLive._log_websocket_connection_failure(
                "2219255254384",
                error_type="RuntimeError",
                error_message=(
                    "provider failed cookie2=COOKIE_SECRET "
                    "https://passport.goofish.com/verify/VERIFY_SECRET"
                ),
                failure_count=1,
                max_failures=5,
            )
        finally:
            logger.remove(sink_id)

        logged = output.getvalue()
        self.assertFalse(expected)
        self.assertIn("ERROR|", logged)
        self.assertIn("RuntimeError", logged)
        self.assertNotIn("COOKIE_SECRET", logged)
        self.assertNotIn("VERIFY_SECRET", logged)
        self.assertNotIn("Traceback", logged)

    async def asyncSetUp(self):
        active_refresh_registry.unregister("account-1")
        FakeOfficialRefreshService.calls.clear()

    async def asyncTearDown(self):
        active_refresh_registry.unregister("account-1")
        active_refresh_registry.consume_cancelled("account-1")

    async def test_saved_password_probe_expired_triggers_slider_self_recovery(self):
        # 2026-08-14 语义反转：有账密账号 probe 判定过期后，先走滑块隐身密码自愈；
        # 滑块成功即完成续期，不再进入人工重登，也不启动官方验证会话。
        live = object.__new__(XianyuLive)
        live.cookie_id = "account-1"
        live.user_id = 7
        live.cookies_str = "unb=9988; cookie2=old"
        live.cookies = {"unb": "9988", "cookie2": "old"}
        live.pending_verification_url = ""
        live._update_cookies_and_restart = AsyncMock(return_value=True)
        live._recover_via_slider_password_login = AsyncMock(
            return_value="unb=9988; cookie2=slider-renewed"
        )
        live.send_token_refresh_notification = AsyncMock()
        database = FakeRefreshDatabase()
        probe = AsyncMock(return_value=SessionProbeResult(
            status=PROBE_EXPIRED,
            cookies={"unb": "9988", "cookie2": "old"},
        ))

        with (
            patch("db_manager.db_manager", database),
            patch(
                "utils.xianyu_official_login.XianyuOfficialLoginService",
                FakeOfficialRefreshService,
            ),
            patch("XianyuAutoAsync.probe_message_session_async", probe),
        ):
            success = await live._try_password_login_refresh("手动立即刷新")

        self.assertTrue(success)
        probe.assert_awaited_once()
        live._recover_via_slider_password_login.assert_awaited_once()
        self.assertEqual(
            live._recover_via_slider_password_login.await_args.args[:2],
            ("seller@example.com", "secret"),
        )
        live._update_cookies_and_restart.assert_awaited_once()
        # 滑块自愈成功后不应再启动官方验证会话（服务端浏览器）
        self.assertEqual(FakeOfficialRefreshService.calls, [])
        self.assertEqual(database.status["state"], "success")
        self.assertEqual(database.validated_calls, 1)

    async def test_non_password_login_freezes_without_probe_or_browser(self):
        live = object.__new__(XianyuLive)
        live.cookie_id = "account-1"
        live.user_id = 7
        live.cookie_refresh_enabled = False
        live.cookies = {"unb": "9988", "cookie2": "old"}
        live.last_token_refresh_status = ""
        live.send_token_refresh_notification = AsyncMock()
        database = FakeRefreshDatabase(login_method="qr", username="", password="")
        probe = AsyncMock()

        with (
            patch("db_manager.db_manager", database),
            patch(
                "utils.xianyu_official_login.XianyuOfficialLoginService",
                FakeOfficialRefreshService,
            ),
            patch("XianyuAutoAsync.probe_message_session_async", probe),
        ):
            first = await live._try_password_login_refresh("令牌/Session过期")
            second = await live._try_password_login_refresh("连续连接失败5次")

        self.assertFalse(first)
        self.assertFalse(second)
        self.assertEqual(FakeOfficialRefreshService.calls, [])
        self.assertEqual(
            database.updates[-1][1]["error_code"],
            "manual_reauth_required",
        )
        self.assertEqual(database.updates[-1][1]["state"], "manual_reauth_required")
        self.assertEqual(database.expired_calls, 1)
        probe.assert_not_awaited()
        # 首次冻结必须告警一次，否则账号会静默停摆；第二次触发不得重复骚扰
        live.send_token_refresh_notification.assert_awaited_once()
        self.assertIn("重新扫码", live.send_token_refresh_notification.await_args.args[0])

    async def test_invalid_credentials_enter_manual_reauth_once_without_retry_storm(self):
        # 2026-08-14 语义反转：有账密账号 probe 过期后允许一次完整自愈（滑块失败 ->
        # 官方验证会话）。凭据类失败（invalid_credentials）落入 manual_reauth_required
        # 单向态；第二次触发必须被人工态护栏拦下，不得再拉起滑块或官方浏览器。
        live = object.__new__(XianyuLive)
        live.cookie_id = "account-1"
        live.user_id = 7
        live.cookies_str = "unb=9988; cookie2=old"
        live.cookies = {"unb": "9988", "cookie2": "old"}
        live.pending_verification_url = ""
        live._recover_via_slider_password_login = AsyncMock(return_value="")
        live.send_token_refresh_notification = AsyncMock()
        database = FakeRefreshDatabase()
        probe = AsyncMock(return_value=SessionProbeResult(
            status=PROBE_EXPIRED,
            cookies=dict(live.cookies),
        ))
        FailingOfficialRefreshService.calls.clear()

        with (
            patch("db_manager.db_manager", database),
            patch(
                "utils.xianyu_official_login.XianyuOfficialLoginService",
                FailingOfficialRefreshService,
            ),
            patch("XianyuAutoAsync.probe_message_session_async", probe),
        ):
            first = await live._try_password_login_refresh("令牌/Session过期")
            second = await live._try_password_login_refresh("定时 Cookie 刷新（每24小时）")

        self.assertFalse(first)
        self.assertFalse(second)
        # 第一次允许尝试一次官方验证会话；第二次被 manual_reauth_required 护栏拦截
        self.assertEqual(len(FailingOfficialRefreshService.calls), 1)
        live._recover_via_slider_password_login.assert_awaited_once()
        self.assertEqual(database.status["state"], "manual_reauth_required")
        self.assertEqual(database.status["error_code"], "manual_reauth_required")
        self.assertEqual(database.expired_calls, 1)

    async def test_transient_official_failure_stays_retryable_for_password_accounts(self):
        # 2026-08-14 语义反转：有账密账号的临时性失败（profile_in_use）保持可重试的
        # failed 态，不落入 manual_reauth_required 单向门，后续触发允许再次自愈。
        live = object.__new__(XianyuLive)
        live.cookie_id = "account-1"
        live.user_id = 7
        live.cookies_str = "unb=9988; cookie2=old"
        live.cookies = {"unb": "9988", "cookie2": "old"}
        live.pending_verification_url = ""
        live._recover_via_slider_password_login = AsyncMock(return_value="")
        live.send_token_refresh_notification = AsyncMock()
        database = FakeRefreshDatabase()
        probe = AsyncMock(return_value=SessionProbeResult(
            status=PROBE_EXPIRED,
            cookies=dict(live.cookies),
        ))
        FailingOfficialRefreshService.calls.clear()
        FailingOfficialRefreshService.error_code = "profile_in_use"

        try:
            with (
                patch("db_manager.db_manager", database),
                patch(
                    "utils.xianyu_official_login.XianyuOfficialLoginService",
                    FailingOfficialRefreshService,
                ),
                patch("XianyuAutoAsync.probe_message_session_async", probe),
            ):
                first = await live._try_password_login_refresh("令牌/Session过期")
                second = await live._try_password_login_refresh("令牌/Session过期")
        finally:
            FailingOfficialRefreshService.error_code = "invalid_credentials"

        self.assertFalse(first)
        self.assertFalse(second)
        # 临时失败不冻结账号：两次触发都完整走到官方验证会话
        self.assertEqual(len(FailingOfficialRefreshService.calls), 2)
        self.assertEqual(database.status["state"], "failed")
        self.assertEqual(database.status["error_code"], "profile_in_use")
        self.assertEqual(database.expired_calls, 0)

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
        database = FakeRefreshDatabase(login_method="qr", username="", password="")
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
        probe.assert_awaited_once_with(live.cookies_str, live.browser_user_agent, proxy=None)
        self.assertEqual(database.status["state"], "manual_reauth_required")
        self.assertFalse(getattr(live, "pending_verification_url", ""))
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
            cookies={
                "unb": "9988",
                "cookie2": "renewed",
                "_m_h5_tk": "token_2",
                "x5sec": "verification-cookie",
            },
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
        self.assertEqual(database.cas_calls, 1)
        self.assertIn("x5sec=verification-cookie", database.details["value"])

    async def test_message_token_probe_passes_the_listener_device_id_through(self):
        # 探测与 WS 注册必须共用 listener 的 device_id，避免平台
        # device_id_or_appkey_is_not_equal 401。
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
        live.device_id = "listener-device-9988"
        database = FakeRefreshDatabase()
        details = database.get_cookie_details("account-1")
        details["browser_user_agent"] = live.browser_user_agent
        database.get_cookie_details = lambda _cookie_id: dict(details)
        database.update_cookie_account_info = unittest.mock.Mock(return_value=True)
        probe = AsyncMock(return_value=SessionProbeResult(
            status=PROBE_SUCCESS,
            cookies=dict(live.cookies),
            access_token="message-access-token",
        ))

        with (
            patch("db_manager.db_manager", database),
            patch("XianyuAutoAsync.probe_message_session_async", probe),
        ):
            token = await live.refresh_token()

        self.assertEqual(token, "message-access-token")
        self.assertEqual(probe.await_count, 1)
        self.assertEqual(
            probe.await_args.kwargs,
            {"device_id": "listener-device-9988", "proxy": None},
        )

    async def test_transient_message_token_probe_failure_is_retryable_and_does_not_require_human_action(self):
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
        live.send_token_refresh_notification = AsyncMock()
        database = FakeRefreshDatabase(login_method="qr", username="", password="")
        details = database.get_cookie_details("account-1")
        details["browser_user_agent"] = live.browser_user_agent
        database.get_cookie_details = lambda _cookie_id: dict(details)
        database.update_cookie_account_info = unittest.mock.Mock(return_value=True)
        transient = SessionProbeResult(
            status=PROBE_RETRYABLE_ERROR,
            cookies=dict(live.cookies),
            error_code="token_probe_exception",
            message="消息 Token 探测出现临时异常",
        )
        recovered = SessionProbeResult(
            status=PROBE_SUCCESS,
            cookies={
                "unb": "9988",
                "cookie2": "renewed",
                "_m_h5_tk": "token_2",
            },
            access_token="message-access-token",
        )
        probe = AsyncMock(side_effect=[transient, recovered])

        with patch("db_manager.db_manager", database), patch(
            "XianyuAutoAsync.probe_message_session_async", probe
        ):
            first = await live.refresh_token()
            self.assertIsNone(first)
            self.assertEqual(database.status["state"], "failed")
            self.assertEqual(database.status["error_code"], "token_probe_exception")
            self.assertEqual(live.last_token_refresh_status, "retryable_error")
            live.current_token = None
            second = await live.refresh_token()

        self.assertEqual(second, "message-access-token")
        self.assertEqual(probe.await_count, 2)
        live.send_token_refresh_notification.assert_not_awaited()

    async def test_message_token_probe_exception_keeps_session_retryable(self):
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
        database = FakeRefreshDatabase(login_method="qr", username="", password="")
        details = database.get_cookie_details("account-1")
        details["browser_user_agent"] = live.browser_user_agent
        database.get_cookie_details = lambda _cookie_id: dict(details)
        probe = AsyncMock(side_effect=RuntimeError("synthetic network failure"))

        with patch("db_manager.db_manager", database), patch(
            "XianyuAutoAsync.probe_message_session_async", probe
        ):
            token = await live.refresh_token()

        self.assertIsNone(token)
        self.assertEqual(database.status["state"], "failed")
        self.assertEqual(database.status["error_code"], "token_probe_exception")
        self.assertEqual(live.last_token_refresh_status, "retryable_error")
        live.send_token_refresh_notification.assert_not_awaited()

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
            "user_id": 7,
            "cookie_revision": 3,
            "xianyu_unb": "9988",
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

        def compare_and_swap_cookie_session(_cookie_id, **kwargs):
            stored["value"] = kwargs["cookie_value"]
            stored["browser_user_agent"] = kwargs.get(
                "browser_user_agent", stored["browser_user_agent"]
            )
            stored["cookie_revision"] += 1
            return {"state": "updated", "cookie_revision": stored["cookie_revision"]}

        database = SimpleNamespace(
            get_cookie_details=lambda _cookie_id: dict(stored),
            update_cookie_account_info=unittest.mock.Mock(side_effect=update_cookie_account_info),
            update_account_session_refresh=unittest.mock.Mock(return_value=True),
            compare_and_swap_cookie_session=unittest.mock.Mock(
                side_effect=compare_and_swap_cookie_session
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

    async def test_qr_account_with_l3_memory_recovers_without_password(self):
        live = object.__new__(XianyuLive)
        live.cookie_id = "account-1"
        live.user_id = 7
        live.cookies_str = "unb=9988; cookie2=old; _m_h5_tk=old"
        live.cookies = {"unb": "9988", "cookie2": "old", "_m_h5_tk": "old"}
        live._update_cookies_and_restart = AsyncMock(return_value=True)
        live._recover_via_passwordless_refresh = AsyncMock(
            return_value="unb=9988; cookie2=fresh; _m_h5_tk=fresh"
        )
        live._recover_via_slider_password_login = AsyncMock(return_value="")
        live.send_token_refresh_notification = AsyncMock()
        database = FakeRefreshDatabase(login_method="qr", username="", password="")
        database.details["has_l3_memory"] = True
        database.details["value"] = "unb=9988; cookie2=old; _m_h5_tk=old"
        probe = AsyncMock(return_value=SessionProbeResult(
            status=PROBE_EXPIRED,
            cookies={"unb": "9988", "cookie2": "old"},
            error_code="session_expired",
        ))

        with (
            patch("db_manager.db_manager", database),
            patch(
                "utils.xianyu_official_login.XianyuOfficialLoginService",
                FakeOfficialRefreshService,
            ),
            patch("XianyuAutoAsync.probe_message_session_async", probe),
        ):
            success = await live._try_password_login_refresh("令牌/Session过期")

        self.assertTrue(success)
        live._recover_via_passwordless_refresh.assert_awaited_once()
        live._recover_via_slider_password_login.assert_not_awaited()
        live._update_cookies_and_restart.assert_awaited_once()
        self.assertEqual(FakeOfficialRefreshService.calls, [])
        self.assertEqual(database.status["state"], "success")
        self.assertEqual(database.validated_calls, 1)

    async def test_passwordless_fast_entry_unavailable_is_manual_and_retryable_profile_stays_failed(self):
        live = object.__new__(XianyuLive)
        live.cookie_id = "account-1"
        live.user_id = 7
        live.cookies_str = "unb=9988; cookie2=old; _m_h5_tk=old"
        live.cookies = {"unb": "9988", "cookie2": "old"}
        live._last_l3_error_code = "fast_entry_unavailable"
        live._update_cookies_and_restart = AsyncMock(return_value=True)
        live._recover_via_passwordless_refresh = AsyncMock(return_value="")
        live.send_token_refresh_notification = AsyncMock()
        database = FakeRefreshDatabase(login_method="qr", username="", password="")
        database.details["has_l3_memory"] = True
        database.details["value"] = "unb=9988; cookie2=old; _m_h5_tk=old"
        probe = AsyncMock(return_value=SessionProbeResult(
            status=PROBE_EXPIRED,
            error_code="session_expired",
        ))

        async def fake_passwordless(*_args, **_kwargs):
            live._last_l3_error_code = "fast_entry_unavailable"
            return ""

        live._recover_via_passwordless_refresh = AsyncMock(side_effect=fake_passwordless)

        with (
            patch("db_manager.db_manager", database),
            patch(
                "utils.xianyu_official_login.XianyuOfficialLoginService",
                FakeOfficialRefreshService,
            ),
            patch("XianyuAutoAsync.probe_message_session_async", probe),
        ):
            success = await live._try_password_login_refresh("令牌/Session过期")

        self.assertFalse(success)
        self.assertEqual(database.status["state"], "manual_reauth_required")
        self.assertEqual(database.l3_marks, [("account-1", False)])
        self.assertEqual(FakeOfficialRefreshService.calls, [])

    async def test_corrupt_l3_profile_stays_retryable_failed(self):
        live = object.__new__(XianyuLive)
        live.cookie_id = "account-1"
        live.user_id = 7
        live.cookies_str = "unb=9988; cookie2=old; _m_h5_tk=old"
        live.cookies = {"unb": "9988", "cookie2": "old"}
        live._update_cookies_and_restart = AsyncMock()
        live.send_token_refresh_notification = AsyncMock()
        database = FakeRefreshDatabase(login_method="qr", username="", password="")
        database.details["has_l3_memory"] = True
        database.details["value"] = "unb=9988; cookie2=old; _m_h5_tk=old"
        probe = AsyncMock(return_value=SessionProbeResult(
            status=PROBE_EXPIRED,
            error_code="session_expired",
        ))

        async def fake_passwordless(*_args, **_kwargs):
            live._last_l3_error_code = "profile_corrupt"
            return ""

        live._recover_via_passwordless_refresh = AsyncMock(side_effect=fake_passwordless)

        with (
            patch("db_manager.db_manager", database),
            patch(
                "utils.xianyu_official_login.XianyuOfficialLoginService",
                FakeOfficialRefreshService,
            ),
            patch("XianyuAutoAsync.probe_message_session_async", probe),
        ):
            success = await live._try_password_login_refresh("令牌/Session过期")

        self.assertFalse(success)
        self.assertEqual(database.status["state"], "failed")
        self.assertEqual(database.status["error_code"], "profile_corrupt")
        self.assertEqual(database.l3_marks, [])
        live._update_cookies_and_restart.assert_not_awaited()
        self.assertEqual(FakeOfficialRefreshService.calls, [])


if __name__ == "__main__":
    unittest.main()
