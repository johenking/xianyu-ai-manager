import asyncio
import unittest

import httpx

from app_factory import create_app
from cookie_manager import CookieManager


class CookieManagerHandoffTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_stubborn_old_listener_cannot_block_replacement_past_timeout(self):
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

        async def replacement_listener(cookie_id, cookie_value, user_id, **kwargs):
            del cookie_id, cookie_value, user_id, kwargs
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
        self.assertEqual(result["status"], "restarted")
        release_old_listener.set()
        await old_task
        replacement_task = manager.tasks.pop(account_id)
        replacement_task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await replacement_task
