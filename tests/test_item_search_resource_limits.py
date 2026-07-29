import asyncio
import gc
import unittest
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError

import reply_server
import skill_monitor_scheduler
from utils import item_search


class ItemSearchRequestLimitTests(unittest.TestCase):
    def test_api_models_reject_oversized_or_extreme_search_work(self):
        invalid_requests = (
            lambda: reply_server.ItemSearchRequest(
                keyword="x" * (item_search.SEARCH_KEYWORD_MAX_CHARS + 1),
                account_id="account-1",
            ),
            lambda: reply_server.ItemSearchRequest(
                keyword="phone",
                account_id="account-1",
                page=item_search.SEARCH_PAGE_MAX + 1,
            ),
            lambda: reply_server.ItemSearchRequest(
                keyword="phone",
                account_id="account-1",
                page_size=item_search.SEARCH_PAGE_SIZE_MAX + 1,
            ),
            lambda: reply_server.ItemSearchMultipleRequest(
                keyword="phone",
                account_id="account-1",
                total_pages=item_search.SEARCH_TOTAL_PAGES_MAX + 1,
            ),
        )

        for build_request in invalid_requests:
            with self.subTest(build_request=build_request):
                with self.assertRaises(ValidationError):
                    build_request()

    def test_api_models_preserve_normal_search_inputs(self):
        single = reply_server.ItemSearchRequest(
            keyword="phone",
            account_id="account-1",
            page=2,
            page_size=20,
        )
        multiple = reply_server.ItemSearchMultipleRequest(
            keyword="phone",
            account_id="account-1",
            total_pages=3,
        )

        self.assertEqual(single.page, 2)
        self.assertEqual(single.page_size, 20)
        self.assertEqual(multiple.total_pages, 3)


class ItemSearchRuntimeLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_bottom_layer_rejects_invalid_work_before_browser_creation(self):
        with patch.object(item_search, "XianyuSearcher") as searcher:
            result = await item_search.search_multiple_pages_xianyu(
                "phone",
                user_id=7,
                account_id="account-1",
                total_pages=item_search.SEARCH_TOTAL_PAGES_MAX + 1,
            )

        self.assertEqual(result["error_code"], "invalid_search_request")
        searcher.assert_not_called()

    async def test_same_account_searches_are_serialized(self):
        active = 0
        max_active = 0
        first_entered = asyncio.Event()
        release = asyncio.Event()

        async def fake_impl(*_args, **_kwargs):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            first_entered.set()
            await release.wait()
            active -= 1
            return {"items": [], "total": 0, "is_real_data": True}

        with patch.object(item_search, "_search_xianyu_items_impl", new=fake_impl):
            first = asyncio.create_task(
                item_search.search_xianyu_items(
                    "phone", user_id=7, account_id="account-1"
                )
            )
            await asyncio.wait_for(first_entered.wait(), timeout=1)
            second = asyncio.create_task(
                item_search.search_xianyu_items(
                    "phone", user_id=7, account_id="account-1"
                )
            )
            await asyncio.sleep(0.02)
            self.assertEqual(active, 1)
            release.set()
            await asyncio.gather(first, second)

        self.assertEqual(max_active, 1)

    async def test_global_search_concurrency_is_bounded(self):
        active = 0
        max_active = 0
        enough_entered = asyncio.Event()
        release = asyncio.Event()

        async def fake_impl(*_args, **_kwargs):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            if active == item_search.SEARCH_GLOBAL_CONCURRENCY:
                enough_entered.set()
            await release.wait()
            active -= 1
            return {"items": [], "total": 0, "is_real_data": True}

        with patch.object(item_search, "_search_xianyu_items_impl", new=fake_impl):
            tasks = [
                asyncio.create_task(
                    item_search.search_xianyu_items(
                        "phone",
                        user_id=index + 1,
                        account_id=f"account-{index}",
                    )
                )
                for index in range(item_search.SEARCH_GLOBAL_CONCURRENCY + 2)
            ]
            await asyncio.wait_for(enough_entered.wait(), timeout=1)
            await asyncio.sleep(0.02)
            self.assertEqual(active, item_search.SEARCH_GLOBAL_CONCURRENCY)
            release.set()
            await asyncio.gather(*tasks)

        self.assertEqual(max_active, item_search.SEARCH_GLOBAL_CONCURRENCY)

    async def test_finished_unique_account_locks_do_not_accumulate(self):
        async def fake_impl(*_args, **_kwargs):
            return {"items": [], "total": 0, "is_real_data": True}

        budget = item_search._loop_search_budget()
        with patch.object(item_search, "_search_xianyu_items_impl", new=fake_impl):
            for index in range(50):
                await item_search.search_xianyu_items(
                    "phone",
                    user_id=index + 1,
                    account_id=f"unique-account-{index}",
                )

        gc.collect()
        self.assertEqual(len(budget.account_locks), 0)

    async def test_search_timeout_is_reported_and_operation_is_cancelled(self):
        cancelled = asyncio.Event()

        async def blocking_impl(*_args, **_kwargs):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        with (
            patch.object(item_search, "SEARCH_OPERATION_TIMEOUT_SECONDS", 0.01),
            patch.object(item_search, "_search_xianyu_items_impl", new=blocking_impl),
        ):
            result = await item_search.search_xianyu_items(
                "phone", user_id=7, account_id="account-timeout"
            )

        self.assertEqual(result["error_code"], "search_timeout")
        self.assertTrue(cancelled.is_set())

    async def test_cancelled_browser_attempt_runs_close_lifecycle(self):
        started = asyncio.Event()
        closed = asyncio.Event()

        class BlockingSearcher:
            def __init__(self, **_kwargs):
                pass

            async def search_items(self, *_args, **_kwargs):
                started.set()
                await asyncio.Event().wait()

            async def close_browser(self):
                closed.set()

        with (
            patch.object(item_search, "XianyuSearcher", BlockingSearcher),
            patch.object(item_search, "SEARCH_OPERATION_TIMEOUT_SECONDS", 0.01),
        ):
            result = await item_search.search_xianyu_items(
                "phone", user_id=7, account_id="account-close"
            )

        self.assertTrue(started.is_set())
        self.assertTrue(closed.is_set())
        self.assertEqual(result["error_code"], "search_timeout")


class ItemSearchRouteSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_routes_reject_unowned_account_before_runtime_budget(self):
        current_user = {"user_id": 7, "username": "tenant"}
        requests = (
            (
                reply_server.search_items,
                reply_server.ItemSearchRequest(
                    keyword="phone",
                    account_id="other-account",
                ),
                "utils.item_search.search_xianyu_items",
            ),
            (
                reply_server.search_multiple_pages,
                reply_server.ItemSearchMultipleRequest(
                    keyword="phone",
                    account_id="other-account",
                ),
                "utils.item_search.search_multiple_pages_xianyu",
            ),
        )

        for route, request, search_target in requests:
            with (
                self.subTest(route=route.__name__),
                patch.object(
                    reply_server.db_manager,
                    "get_owned_cookie_search_context",
                    return_value={"state": "ownership_mismatch"},
                ),
                patch(search_target, new_callable=AsyncMock) as search,
            ):
                with self.assertRaises(reply_server.HTTPException) as raised:
                    await route(request, current_user=current_user)

                self.assertEqual(raised.exception.status_code, 403)
                search.assert_not_awaited()

    async def test_route_does_not_echo_runtime_error_details(self):
        current_user = {"user_id": 7, "username": "tenant"}
        request = reply_server.ItemSearchRequest(
            keyword="phone",
            account_id="account-1",
        )
        with (
            patch.object(
                reply_server.db_manager,
                "get_owned_cookie_search_context",
                return_value={"state": "ready"},
            ),
            patch(
                "utils.item_search.search_xianyu_items",
                new_callable=AsyncMock,
                return_value={
                    "items": [],
                    "total": 0,
                    "error": "private provider response",
                    "error_code": "search_failed",
                },
            ),
        ):
            response = await reply_server.search_items(
                request,
                current_user=current_user,
            )

        self.assertFalse(response["success"])
        self.assertEqual(response["error_code"], "search_failed")
        self.assertEqual(response["error"], "商品搜索暂时不可用")
        self.assertNotIn("private provider response", str(response))

    async def test_route_exception_uses_generic_http_detail(self):
        current_user = {"user_id": 7, "username": "tenant"}
        request = reply_server.ItemSearchRequest(
            keyword="phone",
            account_id="account-1",
        )
        with (
            patch.object(
                reply_server.db_manager,
                "get_owned_cookie_search_context",
                return_value={"state": "ready"},
            ),
            patch(
                "utils.item_search.search_xianyu_items",
                new_callable=AsyncMock,
                side_effect=RuntimeError("private provider response"),
            ),
        ):
            with self.assertRaises(reply_server.HTTPException) as raised:
                await reply_server.search_items(
                    request,
                    current_user=current_user,
                )

        self.assertEqual(raised.exception.status_code, 500)
        self.assertEqual(raised.exception.detail, "商品搜索暂时不可用")
        self.assertNotIn("private provider response", str(raised.exception.detail))


class SkillMonitorSchedulerLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_scheduler_starts_at_most_two_distinct_accounts(self):
        scheduler = skill_monitor_scheduler.SkillMonitorScheduler(
            task_executor=AsyncMock(),
        )
        due = [
            {"id": 1, "user_id": 7, "account_id": "account-a"},
            {"id": 2, "user_id": 7, "account_id": "account-a"},
            {"id": 3, "user_id": 8, "account_id": "account-b"},
            {"id": 4, "user_id": 9, "account_id": "account-c"},
        ]

        with (
            patch("skill_monitor_scheduler.skill_monitor_feature_enabled", return_value=True),
            patch(
                "skill_monitor_scheduler.db_manager.recover_stale_skill_monitor_runs",
                return_value=0,
            ),
            patch(
                "skill_monitor_scheduler.db_manager.list_due_skill_monitor_tasks",
                return_value=due,
            ),
        ):
            started = await scheduler.run_due_once()

        self.assertEqual(started, 2)
        self.assertEqual(scheduler._running_account_ids, {"account-a", "account-b"})
        await scheduler.stop()
