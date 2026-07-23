import asyncio
import os
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import reply_server
from db_manager import DBManager
from official_login_sessions import OfficialLoginSessionRecord
from utils.xianyu_official_login import OfficialLoginResult
from account_session_refresh import active_refresh_registry


class FakeCookieManager:
    def __init__(self):
        self.replace_calls = []
        self.add_calls = []

    async def replace_cookie(
        self,
        account_id,
        cookies,
        save_to_db=True,
        runtime_state=None,
    ):
        self.replace_calls.append((account_id, cookies, save_to_db, runtime_state))
        return {"status": "restarted", "cookie_id": account_id}

    def add_cookie(self, account_id, cookies, user_id=None, runtime_state=None):
        self.add_calls.append((account_id, cookies, user_id, runtime_state))


class OfficialPasswordLoginBackendTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.db = DBManager(self.db_path)
        with self.db.lock:
            self.db.conn.execute(
                """
                INSERT INTO cookies (
                    id, value, user_id, auto_confirm, remark, pause_duration,
                    username, show_browser, xianyu_unb
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "legacy-account",
                    "unb=stable-unb; cookie2=old-cookie",
                    1,
                    0,
                    "保留备注",
                    23,
                    "old-login",
                    0,
                    "stable-unb",
                ),
            )
            self.db.conn.execute(
                "INSERT INTO keywords (cookie_id, keyword, reply) VALUES (?, ?, ?)",
                ("legacy-account", "价格", "详情页价格为准"),
            )
            self.db.conn.commit()

    def tearDown(self):
        self.db.conn.close()
        os.unlink(self.db_path)

    async def asyncTearDown(self):
        active_refresh_registry.unregister("legacy-account")
        active_refresh_registry.consume_cancelled("legacy-account")

    async def test_legacy_account_id_is_accepted_but_not_used_or_stored(self):
        start_login = AsyncMock(return_value={
            "session_id": "official-session",
            "state": "preparing",
            "message": "正在打开闲鱼官方登录页",
        })
        with patch.object(reply_server.official_login_coordinator, "start", start_login):
            response = await reply_server.password_login(
                {
                    "account_id": "client-supplied-wrong-id",
                    "account": "seller@example.com",
                    "password": "secret",
                    "show_browser": True,
                },
                current_user={"user_id": 1, "username": "admin"},
            )

        self.assertTrue(response["success"])
        self.assertEqual(response["session_id"], "official-session")
        start_login.assert_awaited_once_with(
            owner_user_id=1,
            mode="password",
            account="seller@example.com",
            password="secret",
            show_browser=True,
        )
        self.assertNotIn(
            "client-supplied-wrong-id",
            repr(start_login.await_args),
        )

    async def test_same_unb_preserves_account_data_encrypts_password_and_restarts_once(self):
        manager = FakeCookieManager()
        record = OfficialLoginSessionRecord(
            session_id="login-session",
            owner_user_id=1,
            mode="password",
            account="seller@example.com",
            show_browser=False,
        )
        result = OfficialLoginResult(
            status="success",
            cookies={
                "unb": "stable-unb",
                "cookie2": "new-cookie",
                "_m_h5_tk": "new-token",
            },
            unb="stable-unb",
            used_password=True,
            browser_user_agent="Mozilla/5.0 Synthetic Chrome/150.0.0.0",
            access_token="synthetic-access-token",
        )

        with (
            patch.object(reply_server, "db_manager", self.db),
            patch.object(reply_server.cookie_manager, "manager", manager),
        ):
            metadata = await reply_server._complete_official_login_session(
                record,
                result,
                "seller@example.com",
                "new-secret",
            )

        self.assertEqual(metadata["account_id"], "legacy-account")
        self.assertFalse(metadata["is_new_account"])
        self.assertEqual(
            manager.replace_calls,
            [(
                "legacy-account",
                "unb=stable-unb; cookie2=new-cookie; _m_h5_tk=new-token",
                False,
                unittest.mock.ANY,
            )],
        )
        self.assertEqual(manager.add_calls, [])
        runtime_state = manager.replace_calls[0][3]
        self.assertEqual(runtime_state["current_token"], "synthetic-access-token")
        self.assertEqual(
            runtime_state["browser_user_agent"],
            "Mozilla/5.0 Synthetic Chrome/150.0.0.0",
        )

        details = self.db.get_cookie_details("legacy-account")
        self.assertEqual(details["remark"], "保留备注")
        self.assertEqual(details["pause_duration"], 23)
        self.assertEqual(details["username"], "seller@example.com")
        self.assertEqual(details["password"], "new-secret")
        self.assertEqual(
            details["browser_user_agent"],
            "Mozilla/5.0 Synthetic Chrome/150.0.0.0",
        )
        with self.db.lock:
            password, encrypted, keyword_count = self.db.conn.execute(
                """
                SELECT c.password, c.password_encrypted,
                       (SELECT COUNT(*) FROM keywords k WHERE k.cookie_id = c.id)
                FROM cookies c WHERE c.id = ?
                """,
                ("legacy-account",),
            ).fetchone()
        self.assertEqual(password, "")
        self.assertTrue(encrypted)
        self.assertNotIn("new-secret", encrypted)
        self.assertEqual(keyword_count, 1)

    async def test_account_details_api_never_returns_password_material(self):
        self.db.update_cookie_account_info("legacy-account", password="new-secret")
        with (
            patch.object(reply_server, "db_manager", self.db),
            patch("db_manager.db_manager", self.db),
        ):
            result = reply_server.get_cookie_account_details(
                "legacy-account",
                current_user={"user_id": 1, "username": "admin"},
            )

        self.assertTrue(result["has_login_password"])
        self.assertNotIn("password", result)
        self.assertNotIn("password_encrypted", result)

    async def test_orphaned_verification_state_normalizes_to_action_required(self):
        self.db.update_account_session_refresh(
            "legacy-account",
            state="verification_required",
            trigger="scheduled",
            message="等待验证",
            expires_at=time.time() + 900,
        )
        active_refresh_registry.unregister("legacy-account")

        with patch.object(reply_server, "db_manager", self.db):
            status = reply_server._current_session_refresh_status("legacy-account")

        self.assertEqual(status["state"], "action_required")
        self.assertFalse(status["browser_active"])
        self.assertEqual(status["error_code"], "browser_session_missing")

    async def test_verification_status_reports_a_live_browser_worker(self):
        class BrowserWorker:
            def browser_active(self):
                return True

            def request_visible(self):
                return None

        self.db.update_account_session_refresh(
            "legacy-account",
            state="verification_required",
            trigger="manual",
            message="后台正在检测",
            expires_at=time.time() + 900,
        )
        worker = BrowserWorker()
        active_refresh_registry.register("legacy-account", worker)
        try:
            with patch.object(reply_server, "db_manager", self.db):
                status = reply_server._current_session_refresh_status(
                    "legacy-account"
                )
        finally:
            active_refresh_registry.unregister("legacy-account", worker)

        self.assertEqual(status["state"], "verification_required")
        self.assertTrue(status["browser_active"])

    async def test_same_unb_completion_is_serialized_before_listener_handoff(self):
        class ConcurrentCompletionDatabase:
            def __init__(self):
                self.exists = False
                self.active_updates = 0
                self.max_active_updates = 0
                self.lock = threading.Lock()

            def find_cookie_id_by_unb(self, user_id, unb):
                del user_id
                return unb if self.exists else None

            def get_all_cookies(self, user_id):
                del user_id
                return {"race-unb": "saved"} if self.exists else {}

            def update_cookie_account_info(self, account_id, **kwargs):
                del account_id, kwargs
                with self.lock:
                    self.active_updates += 1
                    self.max_active_updates = max(
                        self.max_active_updates,
                        self.active_updates,
                    )
                time.sleep(0.03)
                self.exists = True
                with self.lock:
                    self.active_updates -= 1
                return True

            def update_account_session_refresh(self, account_id, **kwargs):
                del account_id, kwargs
                return True

        database = ConcurrentCompletionDatabase()
        manager = FakeCookieManager()
        result = OfficialLoginResult(
            status="success",
            cookies={"unb": "race-unb", "cookie2": "session"},
            unb="race-unb",
        )
        records = [
            OfficialLoginSessionRecord(
                session_id=f"race-{index}",
                owner_user_id=1,
                mode="qr",
            )
            for index in range(2)
        ]

        with (
            patch.object(reply_server, "db_manager", database),
            patch.object(reply_server.cookie_manager, "manager", manager),
        ):
            metadata = await asyncio.gather(*(
                reply_server._complete_official_login_session(record, result, "", "")
                for record in records
            ))

        self.assertEqual(database.max_active_updates, 1)
        self.assertEqual([item["is_new_account"] for item in metadata], [True, False])
        self.assertEqual(len(manager.add_calls), 1)
        self.assertEqual(len(manager.replace_calls), 1)

    async def test_manual_session_refresh_reserves_the_account_before_scheduling(self):
        started = asyncio.Event()
        release = asyncio.Event()

        async def run_refresh(*args, **kwargs):
            del args, kwargs
            started.set()
            await release.wait()
            return True

        live = SimpleNamespace(
            _try_password_login_refresh=AsyncMock(side_effect=run_refresh),
        )
        fake_db = SimpleNamespace(
            get_all_cookies=lambda user_id: {"legacy-account": "unb=stable-unb"},
            get_cookie_details=lambda cookie_id: {
                "login_method": "password",
                "username": "seller@example.com",
                "password": "secret",
            },
            get_account_session_refresh=lambda cookie_id: {
                "state": "idle",
                "trigger": "",
                "message": "",
                "error_code": "",
                "verification_image_url": "",
                "started_at": None,
                "last_attempt_at": None,
                "last_success_at": None,
                "expires_at": None,
                "updated_at": None,
            },
        )

        with (
            patch.object(reply_server, "db_manager", fake_db),
            patch("XianyuAutoAsync.XianyuLive.get_instance", return_value=live),
            patch.object(reply_server.cookie_manager, "manager", SimpleNamespace(loop=asyncio.get_running_loop())),
        ):
            first = await reply_server.refresh_account_session(
                "legacy-account",
                current_user={"user_id": 1, "username": "admin"},
            )
            await started.wait()
            second = await reply_server.refresh_account_session(
                "legacy-account",
                current_user={"user_id": 1, "username": "admin"},
            )

        self.assertEqual(first["message"], "已开始一次验证")
        self.assertEqual(second["message"], "Cookie 刷新已经在进行中")
        live._try_password_login_refresh.assert_awaited_once_with(
            "手动立即刷新",
            reuse_active_registration=True,
        )
        release.set()
        await asyncio.sleep(0)


if __name__ == "__main__":
    unittest.main()
