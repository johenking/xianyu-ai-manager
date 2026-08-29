import asyncio
import io
import unittest
from unittest.mock import Mock, patch

import httpx
from loguru import logger

from app_factory import create_app
from cookie_manager import CookieManager


class CookieManagerHandoffTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _runtime_database(state):
        database = Mock()
        database.get_all_cookies.side_effect = lambda: dict(state["cookies"])
        database.get_all_keywords.side_effect = lambda: {
            cookie_id: list(values)
            for cookie_id, values in state.get("keywords", {}).items()
        }
        database.get_all_cookie_status.side_effect = lambda: dict(
            state.get("statuses", {})
        )
        database.get_auto_confirm.side_effect = lambda cookie_id: bool(
            state.get("auto_confirm", {}).get(cookie_id, True)
        )
        database.get_cookie_details.side_effect = lambda cookie_id: {
            "user_id": state.get("owners", {}).get(cookie_id),
            "auto_confirm": bool(
                state.get("auto_confirm", {}).get(cookie_id, True)
            ),
        }
        database.get_inactive_user_ids.side_effect = lambda: set(
            state.get("inactive_users", set())
        )
        return database

    async def test_runtime_reconcile_stops_listener_removed_from_database(self):
        loop = asyncio.get_running_loop()
        state = {
            "cookies": {"account-removed": "unb=removed; cookie2=old"},
            "statuses": {"account-removed": True},
            "owners": {"account-removed": 7},
        }
        database = self._runtime_database(state)

        with patch("cookie_manager.db_manager", database):
            manager = CookieManager(loop)
            listener_stopped = asyncio.Event()

            async def old_listener():
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    listener_stopped.set()
                    raise

            manager.tasks["account-removed"] = loop.create_task(old_listener())
            await asyncio.sleep(0)
            state["cookies"] = {}
            state["statuses"] = {}
            state["owners"] = {}

            self.assertTrue(
                hasattr(manager, "reconcile_from_db"),
                "CookieManager must reconcile database changes with listener tasks",
            )
            result = await manager.reconcile_from_db(shutdown_timeout=0.2)

        self.assertTrue(result["success"])
        self.assertEqual(result["removed"], 1)
        self.assertTrue(listener_stopped.is_set())
        self.assertNotIn("account-removed", manager.tasks)
        self.assertNotIn("account-removed", manager.cookies)

    async def test_runtime_reconcile_stops_listener_when_owner_deactivated(self):
        """归属用户被停用后，reconcile 必须把其账号 listener 停掉且不再重启。"""
        loop = asyncio.get_running_loop()
        state = {
            "cookies": {"account-owned": "unb=owned; cookie2=live"},
            "statuses": {"account-owned": True},
            "owners": {"account-owned": 7},
        }
        database = self._runtime_database(state)

        with patch("cookie_manager.db_manager", database):
            manager = CookieManager(loop)
            listener_stopped = asyncio.Event()

            async def old_listener():
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    listener_stopped.set()
                    raise

            manager.tasks["account-owned"] = loop.create_task(old_listener())
            await asyncio.sleep(0)
            state["inactive_users"] = {7}

            result = await manager.reconcile_from_db(shutdown_timeout=0.2)

        self.assertTrue(result["success"])
        self.assertEqual(result["stopped"], 1)
        self.assertTrue(listener_stopped.is_set())
        self.assertNotIn("account-owned", manager.tasks)
        # 账号记录保留（数据不删），仅监听下线、状态判定为禁用
        self.assertIn("account-owned", manager.cookies)
        self.assertFalse(manager.cookie_status["account-owned"])

    async def test_enable_account_rejected_when_owner_deactivated(self):
        """归属用户停用期间，任何人不得重新启用其账号监听。"""
        loop = asyncio.get_running_loop()
        state = {
            "cookies": {"account-owned": "unb=owned; cookie2=live"},
            "statuses": {"account-owned": False},
            "owners": {"account-owned": 7},
            "inactive_users": {7},
        }
        database = self._runtime_database(state)

        with patch("cookie_manager.db_manager", database):
            manager = CookieManager(loop)
            with self.assertRaises(ValueError):
                manager.update_cookie_status("account-owned", True)
            self.assertFalse(manager.cookie_status["account-owned"])
            database.save_cookie_status.assert_not_called()

    async def test_runtime_reconcile_restarts_changed_listener_and_starts_new_one(self):
        loop = asyncio.get_running_loop()
        state = {
            "cookies": {"account-changed": "unb=changed; cookie2=old"},
            "statuses": {"account-changed": True},
            "owners": {"account-changed": 7},
        }
        database = self._runtime_database(state)

        with patch("cookie_manager.db_manager", database):
            manager = CookieManager(loop)
            old_listener_stopped = asyncio.Event()
            started = []

            async def old_listener():
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    old_listener_stopped.set()
                    raise

            async def replacement_listener(cookie_id, cookie_value, user_id, **kwargs):
                del kwargs
                started.append((cookie_id, cookie_value, user_id))
                await asyncio.Event().wait()

            old_task = loop.create_task(old_listener())
            manager.tasks["account-changed"] = old_task
            manager._run_xianyu = replacement_listener
            await asyncio.sleep(0)
            state["cookies"] = {
                "account-changed": "unb=changed; cookie2=new",
                "account-added": "unb=added; cookie2=new",
            }
            state["statuses"] = {
                "account-changed": True,
                "account-added": True,
            }
            state["owners"] = {"account-changed": 7, "account-added": 7}

            self.assertTrue(
                hasattr(manager, "reconcile_from_db"),
                "CookieManager must reconcile database changes with listener tasks",
            )
            result = await manager.reconcile_from_db(shutdown_timeout=0.2)
            await asyncio.sleep(0)

            self.assertTrue(result["success"])
            self.assertEqual(result["restarted"], 1)
            self.assertEqual(result["started"], 1)
            self.assertTrue(old_listener_stopped.is_set())
            self.assertEqual(
                set(started),
                {
                    ("account-changed", "unb=changed; cookie2=new", 7),
                    ("account-added", "unb=added; cookie2=new", 7),
                },
            )
            self.assertIsNot(manager.tasks["account-changed"], old_task)
            await manager.shutdown()

    async def test_listener_failure_logs_only_masked_account_and_error_type(self):
        raw_account_id = "seller-private-987654321"
        private_error = "cookie2=private-cookie-value"

        class FakeLive:
            def __init__(self, *args, **kwargs):
                del args, kwargs
                raise RuntimeError(private_error)

        manager = object.__new__(CookieManager)
        manager.task_status = {}
        output = io.StringIO()
        sink_id = logger.add(output, level="DEBUG", format="{message}")
        try:
            with patch("XianyuAutoAsync.XianyuLive", FakeLive):
                await manager._run_xianyu(
                    raw_account_id,
                    "unb=private; cookie2=private-cookie-value",
                    7,
                )
        finally:
            logger.remove(sink_id)

        logs = output.getvalue()
        self.assertNotIn(raw_account_id, logs)
        self.assertNotIn(private_error, logs)
        self.assertNotIn("Traceback", logs)
        self.assertIn("error_type=RuntimeError", logs)
        self.assertEqual(
            manager.task_status[raw_account_id]["last_error"],
            "runtime_error:RuntimeError",
        )

    async def test_replace_cookie_waits_for_old_task_outside_account_lock(self):
        loop = asyncio.get_running_loop()
        manager = CookieManager(loop)
        account_id = "account-1"
        manager.cookies[account_id] = "unb=account-1; cookie2=old"
        manager.cookie_status[account_id] = True
        manager.keywords[account_id] = []
        manager._task_locks[account_id] = asyncio.Lock()

        cleanup_acquired_lock = asyncio.Event()

        async def old_listener():
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                async with manager._task_locks[account_id]:
                    cleanup_acquired_lock.set()
                raise

        async def replacement_listener(cookie_id, cookie_value, user_id, **kwargs):
            del cookie_id, cookie_value, user_id, kwargs
            await asyncio.Event().wait()

        old_task = loop.create_task(old_listener())
        manager.tasks[account_id] = old_task
        manager._run_xianyu = replacement_listener
        await asyncio.sleep(0)

        result = await asyncio.wait_for(
            manager.replace_cookie(
                account_id,
                "unb=account-1; cookie2=new",
                save_to_db=False,
                shutdown_timeout=0.2,
            ),
            timeout=1,
        )

        self.assertTrue(cleanup_acquired_lock.is_set())
        self.assertEqual(result["status"], "restarted")
        self.assertEqual(manager.cookies[account_id], "unb=account-1; cookie2=new")

        replacement_task = manager.tasks.pop(account_id)
        replacement_task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await replacement_task

    async def test_latest_concurrent_cookie_replacement_wins(self):
        loop = asyncio.get_running_loop()
        manager = CookieManager(loop)
        account_id = "account-1"
        manager.cookies[account_id] = "unb=account-1; cookie2=old"
        manager.cookie_status[account_id] = True
        manager.keywords[account_id] = []

        async def replacement_listener(cookie_id, cookie_value, user_id, **kwargs):
            del cookie_id, cookie_value, user_id, kwargs
            await asyncio.Event().wait()

        manager._run_xianyu = replacement_listener

        first, second = await asyncio.gather(
            manager.replace_cookie(account_id, "unb=account-1; cookie2=first", save_to_db=False),
            manager.replace_cookie(account_id, "unb=account-1; cookie2=second", save_to_db=False),
        )

        self.assertIn(first["status"], {"restarted", "superseded"})
        self.assertEqual(second["status"], "restarted")
        self.assertEqual(manager.cookies[account_id], "unb=account-1; cookie2=second")

        replacement_task = manager.tasks.pop(account_id)
        replacement_task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await replacement_task

    async def test_stubborn_old_listener_blocks_replacement_after_shutdown_timeout(self):
        loop = asyncio.get_running_loop()
        manager = CookieManager(loop)
        account_id = "account-1"
        manager.cookies[account_id] = "unb=account-1; cookie2=old"
        manager.cookie_status[account_id] = True
        manager.keywords[account_id] = []
        release_old_listener = asyncio.Event()

        async def stubborn_old_listener():
            while not release_old_listener.is_set():
                try:
                    await release_old_listener.wait()
                except asyncio.CancelledError:
                    continue

        replacement_started = asyncio.Event()

        async def replacement_listener(cookie_id, cookie_value, user_id, **kwargs):
            del cookie_id, cookie_value, user_id, kwargs
            replacement_started.set()
            await asyncio.Event().wait()

        old_task = loop.create_task(stubborn_old_listener())
        manager.tasks[account_id] = old_task
        manager._run_xianyu = replacement_listener
        await asyncio.sleep(0)

        replacement_call = loop.create_task(
            manager.replace_cookie(
                account_id,
                "unb=account-1; cookie2=new",
                save_to_db=False,
                shutdown_timeout=0.02,
            )
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app()),
            base_url="http://testserver",
        ) as client:
            health_response = await asyncio.wait_for(
                client.get("/health/live"),
                timeout=0.5,
            )
        done, _ = await asyncio.wait({replacement_call}, timeout=0.2)
        completed_in_time = replacement_call in done
        if not completed_in_time:
            release_old_listener.set()
        result = await replacement_call

        self.assertTrue(completed_in_time)
        self.assertEqual(health_response.status_code, 200)
        self.assertEqual(health_response.json()["status"], "alive")
        self.assertEqual(result["status"], "shutdown_timeout")
        self.assertFalse(replacement_started.is_set())
        self.assertIs(manager.tasks[account_id], old_task)
        self.assertEqual(manager.cookies[account_id], "unb=account-1; cookie2=old")
        release_old_listener.set()
        await old_task
        manager.tasks.pop(account_id, None)
