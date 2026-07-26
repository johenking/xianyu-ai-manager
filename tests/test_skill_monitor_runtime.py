import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException

import reply_server
import skill_monitor_delivery_dispatcher as delivery_module
import skill_monitor_scheduler as scheduler_module


class SkillNotificationTests(unittest.TestCase):
    @staticmethod
    def _successful_response(payload):
        response = Mock()
        response.json.return_value = payload
        response.raise_for_status.return_value = None
        return response

    def test_enabled_channels_only_returns_supported_types(self):
        channels = [
            {"id": 1, "type": "webhook", "enabled": True},
            {"id": 2, "type": "email", "enabled": True},
            {"id": 3, "type": "qq", "enabled": True},
            {"id": 4, "type": "ding_talk", "enabled": True},
            {"id": 5, "type": "telegram", "enabled": False},
        ]

        with patch.object(reply_server.db_manager, "get_notification_channels", return_value=channels):
            result = reply_server._enabled_notification_channels(7)

        self.assertEqual([channel["id"] for channel in result], [1, 4])

    def test_platform_webhooks_use_their_native_payloads(self):
        cases = [
            (
                "wechat",
                {"webhook_url": "https://example.test/wechat"},
                {"errcode": 0},
                {"msgtype": "text", "text": {"content": unittest.mock.ANY}},
            ),
            (
                "dingtalk",
                {"webhook_url": "https://example.test/dingtalk"},
                {"errcode": 0},
                {"msgtype": "markdown", "markdown": {"title": unittest.mock.ANY, "text": unittest.mock.ANY}},
            ),
            (
                "feishu",
                {"webhook_url": "https://example.test/feishu"},
                {"code": 0},
                {"msg_type": "text", "content": {"text": unittest.mock.ANY}},
            ),
        ]
        task = {"keyword": "iPhone"}
        result = {"title": "iPhone 15"}

        for channel_type, config, response_payload, expected_payload in cases:
            with self.subTest(channel_type=channel_type), patch.object(
                reply_server.requests,
                "post",
                return_value=self._successful_response(response_payload),
            ) as post_mock:
                reply_server._send_skill_notification_to_channel(
                    {"type": channel_type, "config": config}, task, result
                )

            self.assertEqual(post_mock.call_args.kwargs["json"], expected_payload)

    def test_platform_webhook_business_error_is_not_recorded_as_success(self):
        response = self._successful_response({"errcode": 40013, "errmsg": "invalid webhook"})
        with patch.object(reply_server.requests, "post", return_value=response):
            with self.assertRaisesRegex(ValueError, "invalid webhook"):
                reply_server._send_skill_notification_to_channel(
                    {"type": "wechat", "config": {"webhook_url": "https://example.test/wechat"}},
                    {"keyword": "iPhone"},
                    {"title": "iPhone 15"},
                )


class SkillDeliveryDispatcherTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _delivery():
        return {
            "id": 17,
            "claim_token": "claim-token",
            "channel_type": "webhook",
            "idempotency_key": "delivery-key",
        }

    @staticmethod
    def _context():
        return {
            "channel": {"id": 1, "type": "webhook", "enabled": True},
            "task": {"id": 3, "notify_enabled": True, "keyword": "iPhone"},
            "result": {"id": 11, "title": "iPhone 15"},
        }

    async def test_successful_delivery_finishes_as_sent(self):
        dispatcher = delivery_module.SkillMonitorDeliveryDispatcher()
        with patch.object(delivery_module, "skill_monitor_feature_enabled", return_value=True), patch.object(
            delivery_module.db_manager,
            "get_skill_monitor_delivery_context",
            return_value=self._context(),
        ), patch.object(
            reply_server, "_send_skill_notification_to_channel"
        ) as send_mock, patch.object(
            delivery_module.db_manager,
            "finish_skill_monitor_delivery",
            return_value=True,
        ) as finish_mock:
            await dispatcher._execute(self._delivery())

        send_mock.assert_called_once()
        finish_mock.assert_called_once_with(17, "claim-token", status="sent")

    async def test_delivery_error_is_redacted_and_finishes_failed(self):
        dispatcher = delivery_module.SkillMonitorDeliveryDispatcher()
        secret_url = "https://example.test/hooks/private-token"
        with patch.object(delivery_module, "skill_monitor_feature_enabled", return_value=True), patch.object(
            delivery_module.db_manager,
            "get_skill_monitor_delivery_context",
            return_value=self._context(),
        ), patch.object(
            reply_server,
            "_send_skill_notification_to_channel",
            side_effect=ValueError(f"403 Client Error for url: {secret_url}"),
        ), patch.object(
            delivery_module.db_manager,
            "finish_skill_monitor_delivery",
            return_value=True,
        ) as finish_mock:
            await dispatcher._execute(self._delivery())

        kwargs = finish_mock.call_args.kwargs
        self.assertEqual(kwargs["status"], "failed")
        self.assertNotIn(secret_url, kwargs["error_message"])
        self.assertIn("[redacted-url]", kwargs["error_message"])


class SkillAiFilterTests(unittest.TestCase):
    def test_ai_filter_accepts_only_recommended_scores_at_least_fifty(self):
        settings = {"model_name": "test-model"}
        with patch.object(reply_server, "_user_ai_cookie_settings", return_value=("cookie-1", settings)), patch.object(
            reply_server.ai_reply_engine, "_create_openai_client", return_value=object()
        ), patch.object(
            reply_server.ai_reply_engine,
            "_call_openai_api",
            side_effect=[
                '{"recommended": true, "score": 85, "reason": "价格合适"}',
                '{"recommended": true, "score": 49, "reason": "优势不足"}',
            ],
        ):
            accepted = reply_server._run_skill_ai_filter(
                {"title": "iPhone 15", "price": "3000"},
                {"ai_filter": "只保留低价商品"},
                7,
            )
            rejected = reply_server._run_skill_ai_filter(
                {"title": "iPhone 15", "price": "5000"},
                {"ai_filter": "只保留低价商品"},
                7,
            )

        self.assertEqual(accepted, {"recommended": True, "score": 85, "reason": "价格合适"})
        self.assertEqual(rejected, {"recommended": False, "score": 49, "reason": "优势不足"})

    def test_ai_filter_requires_an_enabled_account_configuration(self):
        with patch.object(reply_server, "_user_ai_cookie_settings", return_value=(None, None)):
            with self.assertRaises(HTTPException) as raised:
                reply_server._run_skill_ai_filter(
                    {"title": "iPhone 15"},
                    {"ai_filter": "只保留低价商品"},
                    7,
                )

        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("配置并启用AI", raised.exception.detail)


class SkillMonitorExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_result_is_not_inserted_or_notified_again(self):
        search_result = {
            "is_real_data": True,
            "source": "playwright",
            "items": [{"item_id": "item-1", "title": "iPhone 15", "item_url": "https://example.test/item-1"}],
        }
        task = {
            "id": 3,
            "keyword": "iPhone",
            "notify_enabled": True,
            "account_id": "account-1",
        }

        with patch("utils.item_search.search_xianyu_items", new=AsyncMock(return_value=search_result)), patch.object(
            reply_server.db_manager,
            "skill_monitor_result_exists",
            return_value=True,
            create=True,
        ) as exists_mock, patch.object(
            reply_server.db_manager, "persist_skill_monitor_match"
        ) as persist_mock:
            result_ids, raw_count, _ = await reply_server._run_real_skill_monitor(task, 7)

        self.assertEqual(result_ids, [])
        self.assertEqual(raw_count, 1)
        exists_mock.assert_called_once_with(3, 7, "https://example.test/item-1", "item-1")
        persist_mock.assert_not_called()

    async def test_failed_scheduled_run_is_rescheduled_and_records_error(self):
        task = {
            "id": 3,
            "user_id": 7,
            "keyword": "iPhone",
            "schedule_enabled": True,
            "schedule_interval_minutes": 30,
        }
        claim = {
            "claimed": True,
            "run_id": 41,
            "claim_token": "claim-token",
            "account_id": "account-1",
        }
        with patch.object(reply_server, "skill_monitor_feature_enabled", return_value=True), patch.object(
            reply_server.db_manager, "claim_skill_monitor_run", return_value=claim
        ), patch.object(
            reply_server, "_run_real_skill_monitor", new=AsyncMock(side_effect=HTTPException(502, "搜索失败"))
        ), patch.object(
            reply_server.db_manager, "finish_skill_monitor_run", return_value=True
        ) as finish_mock:
            with self.assertRaises(HTTPException):
                await reply_server.execute_skill_monitor_task(task, 7, scheduled_run=True)

        args = finish_mock.call_args.args
        kwargs = finish_mock.call_args.kwargs
        self.assertEqual(args, (41, "claim-token"))
        self.assertEqual(kwargs["status"], "failed")
        self.assertEqual(kwargs["error_code"], "http_502")
        self.assertEqual(kwargs["error_message"], "搜索失败")
        self.assertIsNotNone(kwargs["next_run_at"])


class SkillMonitorSchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_and_stop_own_the_polling_task(self):
        scheduler = scheduler_module.SkillMonitorScheduler(poll_interval_seconds=3600)
        with patch.object(scheduler_module, "skill_monitor_feature_enabled", return_value=True), patch.object(
            scheduler_module.db_manager, "recover_stale_skill_monitor_runs", return_value=0
        ), patch.object(
            scheduler_module.db_manager, "recover_stale_skill_monitor_deliveries", return_value=0
        ), patch.object(
            scheduler_module.db_manager, "list_due_skill_monitor_tasks", return_value=[]
        ):
            await scheduler.start()
            await asyncio.sleep(0)
            self.assertTrue(scheduler.running)
            await scheduler.stop()

        self.assertFalse(scheduler.running)

    async def test_due_poll_does_not_start_the_same_task_twice(self):
        gate = asyncio.Event()

        class BlockingScheduler(scheduler_module.SkillMonitorScheduler):
            async def _execute(self, task: dict) -> None:
                try:
                    await gate.wait()
                finally:
                    self._running_task_ids.discard(int(task["id"]))

        scheduler = BlockingScheduler()
        due = [{"id": 9, "user_id": 7}]
        with patch.object(scheduler_module, "skill_monitor_feature_enabled", return_value=True), patch.object(
            scheduler_module.db_manager, "recover_stale_skill_monitor_runs", return_value=0
        ), patch.object(scheduler_module.db_manager, "list_due_skill_monitor_tasks", return_value=due):
            self.assertEqual(await scheduler.run_due_once(), 1)
            await asyncio.sleep(0)
            self.assertEqual(await scheduler.run_due_once(), 0)
            gate.set()
            await asyncio.sleep(0)

    async def test_stop_cancels_in_flight_monitor_tasks(self):
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def blocking_execute(_task):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        scheduler = scheduler_module.SkillMonitorScheduler(poll_interval_seconds=3600)
        scheduler._execute = blocking_execute
        with patch.object(scheduler_module, "skill_monitor_feature_enabled", return_value=True), patch.object(
            scheduler_module.db_manager, "recover_stale_skill_monitor_runs", return_value=0
        ), patch.object(
            scheduler_module.db_manager, "recover_stale_skill_monitor_deliveries", return_value=0
        ), patch.object(scheduler_module.db_manager,
            "list_due_skill_monitor_tasks",
            side_effect=[[{"id": 9, "user_id": 7}], []],
        ):
            await scheduler.start()
            await asyncio.wait_for(started.wait(), timeout=1)
            await scheduler.stop()

        self.assertTrue(cancelled.is_set())


class SkillMonitorApiValidationTests(unittest.TestCase):
    def test_update_rejects_interval_below_fifteen_minutes(self):
        task = reply_server.SkillMonitorTaskUpdate(schedule_interval_minutes=5)
        with patch.object(
            reply_server.db_manager,
            "get_skill_monitor_task",
            return_value={"id": 3, "schedule_enabled": True, "schedule_interval_minutes": 60},
        ):
            with self.assertRaises(HTTPException) as raised:
                reply_server.update_skill_monitor_task(3, task, {"user_id": 7})

        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("不能少于15分钟", raised.exception.detail)

    def test_create_rejects_task_without_bound_account(self):
        """创建任务未绑定账号时应被拒绝（强制绑号）"""
        task = reply_server.SkillMonitorTaskIn(keyword="iPhone", account_id="")
        with self.assertRaises(HTTPException) as raised:
            reply_server.create_skill_monitor_task(task, {"user_id": 7})

        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("绑定", raised.exception.detail)

    def test_create_rejects_account_not_owned_by_user(self):
        """创建任务绑定越权账号时应被拒绝"""
        task = reply_server.SkillMonitorTaskIn(keyword="iPhone", account_id="account-x")
        with patch.object(
            reply_server.db_manager,
            "get_owned_cookie_search_context",
            return_value={"state": "ownership_mismatch"},
        ):
            with self.assertRaises(HTTPException) as raised:
                reply_server.create_skill_monitor_task(task, {"user_id": 7})

        self.assertEqual(raised.exception.status_code, 403)

    def test_create_accepts_task_with_ready_account(self):
        """创建任务绑定身份完整的自有账号时应成功"""
        task = reply_server.SkillMonitorTaskIn(keyword="iPhone", account_id="account-1")
        with patch.object(
            reply_server.db_manager,
            "get_owned_cookie_search_context",
            return_value={"state": "ready", "cookie_id": "account-1"},
        ), patch.object(
            reply_server.db_manager,
            "create_skill_monitor_task",
            return_value=123,
        ), patch.object(
            reply_server.db_manager,
            "log_skill_event",
            return_value=None,
        ):
            result = reply_server.create_skill_monitor_task(task, {"user_id": 7})

        self.assertTrue(result["success"])
        self.assertEqual(result["id"], 123)

    def test_list_tasks_attaches_readiness(self):
        """列表路由为每条任务附加 readiness，复用账号 context"""
        tasks = [
            {"id": 1, "keyword": "iPhone", "account_id": ""},
            {"id": 2, "keyword": "iPhone", "account_id": "account-ready"},
            {"id": 3, "keyword": "iPhone", "account_id": "account-broken"},
        ]
        context_by_account = {
            "account-ready": {"state": "ready"},
            "account-broken": {"state": "manual_reauth_required"},
        }
        with patch.object(
            reply_server.db_manager, "list_skill_monitor_tasks", return_value=tasks,
        ), patch.object(
            reply_server.db_manager,
            "get_owned_cookie_search_context",
            side_effect=lambda uid, acc: context_by_account.get(acc, {"state": "not_found"}),
        ), patch.object(
            reply_server, "get_skill_monitor_feature_state",
            return_value={"effective": {"skill_monitor_enabled": False}},
        ):
            result = reply_server.list_skill_monitor_tasks({"user_id": 7})

        data = {t["id"]: t["readiness"] for t in result["data"]}
        self.assertFalse(data[1]["configured"])
        self.assertEqual(data[1]["blockers"], ["未绑定闲鱼账号"])
        self.assertTrue(data[2]["configured"])
        self.assertFalse(data[2]["runnable"])
        self.assertIn("真实搜索总开关关闭", data[2]["blockers"][0])
        self.assertFalse(data[3]["configured"])
        self.assertFalse(data[3]["runnable"])
        self.assertTrue(len(data[3]["blockers"]) > 0)

    def test_capability_configuration_is_separate_from_preview_operation_gates(self):
        tasks = [{"id": 9, "keyword": "iPhone", "enabled": True, "account_id": "account-ready"}]
        flags = {
            "skill_monitor_enabled": False,
            "skill_monitor_scheduler_enabled": False,
            "skill_monitor_delivery_enabled": False,
            "skill_monitor_mtop_enabled": False,
        }
        with patch.object(
            reply_server, "get_skill_monitor_feature_state",
            return_value={"configured": flags, "effective": flags},
        ), patch.object(
            reply_server.db_manager, "get_all_cookies", return_value={"account-ready": "secret"},
        ), patch.object(
            reply_server.db_manager, "get_owned_cookie_search_context", return_value={"state": "ready"},
        ), patch.object(
            reply_server.db_manager, "list_skill_monitor_tasks", return_value=tasks,
        ), patch.object(
            reply_server.db_manager, "get_skill_capability_evidence", return_value={},
        ):
            result = reply_server.get_skill_capabilities({"user_id": 7})

        self.assertEqual(result["runtime_mode"], "preview")
        self.assertTrue(result["data"]["config_ready"]["available"])
        self.assertEqual(result["data"]["config_ready"]["label"], "配置完整")
        self.assertFalse(result["operation_gates"]["manual_run"]["enabled"])
        self.assertFalse(result["operation_gates"]["schedule_activation"]["enabled"])
        self.assertFalse(result["operation_gates"]["delivery"]["enabled"])
        self.assertFalse(result["operation_gates"]["mtop"]["enabled"])
        self.assertEqual(
            [item["reason_code"] for item in result["operation_gates"]["schedule_activation"]["blockers"]],
            ["monitor_disabled", "scheduler_disabled"],
        )
        self.assertIn("定时调度开关关闭", result["operation_gates"]["schedule_activation"]["message"])
        self.assertEqual(
            [item["reason_code"] for item in result["operation_gates"]["delivery"]["blockers"]],
            ["monitor_disabled", "delivery_disabled"],
        )
        self.assertIn("结果通知开关关闭", result["operation_gates"]["delivery"]["message"])
        self.assertEqual(
            [item["reason_code"] for item in result["operation_gates"]["mtop"]["blockers"]],
            ["monitor_disabled", "mtop_disabled"],
        )


class ReplyStrategyApiTests(unittest.TestCase):
    """高级回复策略 /api/ai/reply-strategies（归并自 AI 专家客服）"""

    def _stored_prompts(self):
        # 模拟库中已有 8 条中的相关 4 条（含被隐藏的 classify）
        return {
            "classify": {"prompt_type": "classify", "title": "意图分类专家", "content": "c", "enabled": True},
            "price": {"prompt_type": "price", "title": "议价专家", "content": "p", "enabled": True},
            "tech": {"prompt_type": "tech", "title": "技术专家", "content": "t", "enabled": True},
            "default": {"prompt_type": "default", "title": "默认客服", "content": "d", "enabled": True},
        }

    def test_get_returns_three_strategies_without_classify(self):
        with patch.object(
            reply_server.db_manager, "get_skill_agent_prompts", return_value=self._stored_prompts(),
        ):
            result = reply_server.get_ai_reply_strategies({"user_id": 7})

        types = [item["prompt_type"] for item in result["data"]]
        self.assertEqual(types, ["price", "tech", "default"])
        self.assertNotIn("classify", types)
        self.assertEqual(result["shared_scope"], "user")

    def test_put_rejects_unknown_type(self):
        with self.assertRaises(HTTPException) as raised:
            reply_server.update_ai_reply_strategy(
                "classify", reply_server.ReplyStrategyIn(content="x"), {"user_id": 7},
            )
        self.assertEqual(raised.exception.status_code, 400)

    def test_put_rejects_empty_content(self):
        with self.assertRaises(HTTPException) as raised:
            reply_server.update_ai_reply_strategy(
                "price", reply_server.ReplyStrategyIn(content="   "), {"user_id": 7},
            )
        self.assertEqual(raised.exception.status_code, 400)

    def test_put_saves_valid_strategy(self):
        with patch.object(
            reply_server.db_manager, "get_skill_agent_prompts", return_value=self._stored_prompts(),
        ), patch.object(
            reply_server.db_manager, "upsert_skill_agent_prompt", return_value=True,
        ) as upsert, patch.object(
            reply_server.db_manager, "log_skill_event", return_value=None,
        ):
            result = reply_server.update_ai_reply_strategy(
                "price", reply_server.ReplyStrategyIn(content="更克制的议价话术", enabled=True), {"user_id": 7},
            )

        self.assertTrue(result["success"])
        # 确认写入的是 price 类型
        self.assertEqual(upsert.call_args[0][1], "price")

    def test_collection_put_saves_all_three_strategies_transactionally(self):
        payload = reply_server.ReplyStrategiesUpdateIn(
            price=reply_server.ReplyStrategyIn(content="议价策略", enabled=True),
            tech=reply_server.ReplyStrategyIn(content="技术策略", enabled=False),
            default=reply_server.ReplyStrategyIn(content="默认策略", enabled=True),
        )
        with patch.object(
            reply_server.db_manager, "get_skill_agent_prompts", return_value=self._stored_prompts(),
        ), patch.object(
            reply_server.db_manager, "upsert_skill_agent_prompts_transaction", return_value=True,
        ) as save_all, patch.object(
            reply_server.db_manager, "log_skill_event", return_value=True,
        ):
            result = reply_server.update_ai_reply_strategies(payload, {"user_id": 7})

        self.assertTrue(result["success"])
        saved = save_all.call_args.args[1]
        self.assertEqual(set(saved), {"price", "tech", "default"})
        self.assertFalse(saved["tech"]["enabled"])


class SkillOpsDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        reply_server._browser_probe_cache.clear()
        reply_server._browser_probe_checking = False

    def tearDown(self):
        reply_server._browser_probe_cache.clear()
        reply_server._browser_probe_checking = False

    def test_concurrent_browser_status_reads_start_only_one_probe(self):
        with patch.object(reply_server.threading, "Thread") as thread_class:
            first = reply_server._get_browser_probe_snapshot()
            second = reply_server._get_browser_probe_snapshot()

        self.assertTrue(first["checking"])
        self.assertTrue(second["checking"])
        self.assertEqual(thread_class.call_count, 1)
        thread_class.return_value.start.assert_called_once()

    def test_expired_browser_probe_returns_stale_result_while_refreshing(self):
        reply_server._browser_probe_cache.update({
            "status": "ready",
            "playwright_importable": True,
            "playwright_launchable": True,
            "browser_path": "chromium",
            "observed_at": 1.0,
            "playwright_error": "",
        })
        with patch.object(reply_server.time, "time", return_value=10000.0), patch.object(
            reply_server.threading, "Thread"
        ) as thread_class:
            result = reply_server._get_browser_probe_snapshot()

        self.assertEqual(result["status"], "ready")
        self.assertTrue(result["stale"])
        self.assertTrue(result["checking"])
        thread_class.return_value.start.assert_called_once()


if __name__ == "__main__":
    unittest.main()
