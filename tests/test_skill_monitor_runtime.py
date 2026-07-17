import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException

import reply_server
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

    def test_notification_attempts_every_supported_channel_and_records_partial(self):
        channels = [
            {"id": 1, "name": "Webhook", "type": "webhook"},
            {"id": 2, "name": "Bark", "type": "bark"},
            {"id": 3, "name": "Telegram", "type": "telegram"},
        ]
        with patch.object(reply_server, "_enabled_notification_channels", return_value=channels), patch.object(
            reply_server,
            "_send_skill_notification_to_channel",
            side_effect=[None, ValueError("bad key"), None],
        ) as send_mock, patch.object(
            reply_server.db_manager,
            "update_skill_monitor_result_notification",
            return_value=True,
        ) as update_mock:
            status, error = reply_server._notify_skill_monitor_result(
                {"notify_enabled": True, "keyword": "iPhone"},
                7,
                11,
                {"title": "iPhone 15"},
            )

        self.assertEqual(send_mock.call_count, 3)
        self.assertEqual(status, "partial")
        self.assertIn("Bark: bad key", error)
        update_mock.assert_called_once_with(11, 7, "partial", error)

    def test_notification_records_sent_only_after_all_channels_succeed(self):
        channels = [
            {"id": 1, "name": "Webhook", "type": "webhook"},
            {"id": 2, "name": "Bark", "type": "bark"},
        ]
        with patch.object(reply_server, "_enabled_notification_channels", return_value=channels), patch.object(
            reply_server, "_send_skill_notification_to_channel"
        ) as send_mock, patch.object(
            reply_server.db_manager,
            "update_skill_monitor_result_notification",
            return_value=True,
        ) as update_mock:
            status, error = reply_server._notify_skill_monitor_result(
                {"notify_enabled": True, "keyword": "iPhone"},
                7,
                11,
                {"title": "iPhone 15"},
            )

        self.assertEqual(send_mock.call_count, 2)
        self.assertEqual((status, error), ("sent", ""))
        update_mock.assert_called_once_with(11, 7, "sent", "")

    def test_notification_records_failed_after_every_channel_fails(self):
        channels = [
            {"id": 1, "name": "Webhook", "type": "webhook"},
            {"id": 2, "name": "Bark", "type": "bark"},
        ]
        with patch.object(reply_server, "_enabled_notification_channels", return_value=channels), patch.object(
            reply_server,
            "_send_skill_notification_to_channel",
            side_effect=[ValueError("bad url"), ValueError("bad key")],
        ) as send_mock, patch.object(
            reply_server.db_manager,
            "update_skill_monitor_result_notification",
            return_value=True,
        ) as update_mock:
            status, error = reply_server._notify_skill_monitor_result(
                {"notify_enabled": True, "keyword": "iPhone"},
                7,
                11,
                {"title": "iPhone 15"},
            )

        self.assertEqual(send_mock.call_count, 2)
        self.assertEqual(status, "failed")
        self.assertIn("Webhook: bad url", error)
        self.assertIn("Bark: bad key", error)
        update_mock.assert_called_once_with(11, 7, "failed", error)

    def test_notification_error_redacts_webhook_urls(self):
        channels = [{"id": 1, "name": "Webhook", "type": "webhook"}]
        secret_url = "https://example.test/hooks/private-token"
        with patch.object(reply_server, "_enabled_notification_channels", return_value=channels), patch.object(
            reply_server,
            "_send_skill_notification_to_channel",
            side_effect=ValueError(f"403 Client Error for url: {secret_url}"),
        ), patch.object(
            reply_server.db_manager,
            "update_skill_monitor_result_notification",
            return_value=True,
        ):
            status, error = reply_server._notify_skill_monitor_result(
                {"notify_enabled": True, "keyword": "iPhone"},
                7,
                11,
                {"title": "iPhone 15"},
            )

        self.assertEqual(status, "failed")
        self.assertNotIn(secret_url, error)
        self.assertIn("[redacted-url]", error)

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
        task = {"id": 3, "keyword": "iPhone", "notify_enabled": True}

        with patch("utils.item_search.search_xianyu_items", new=AsyncMock(return_value=search_result)), patch.object(
            reply_server.db_manager,
            "skill_monitor_result_exists",
            return_value=True,
            create=True,
        ) as exists_mock, patch.object(
            reply_server.db_manager, "create_skill_monitor_result"
        ) as create_mock, patch.object(reply_server, "_notify_skill_monitor_result") as notify_mock:
            result_ids, raw_count, _ = await reply_server._run_real_skill_monitor(task, 7)

        self.assertEqual(result_ids, [])
        self.assertEqual(raw_count, 1)
        exists_mock.assert_called_once_with(3, 7, "https://example.test/item-1", "item-1")
        create_mock.assert_not_called()
        notify_mock.assert_not_called()

    async def test_failed_scheduled_run_is_rescheduled_and_records_error(self):
        task = {
            "id": 3,
            "user_id": 7,
            "keyword": "iPhone",
            "schedule_enabled": True,
            "schedule_interval_minutes": 30,
        }
        with patch.object(reply_server.db_manager, "mark_skill_monitor_task_running", return_value=True), patch.object(
            reply_server, "_run_real_skill_monitor", new=AsyncMock(side_effect=HTTPException(502, "搜索失败"))
        ), patch.object(
            reply_server.db_manager, "update_skill_monitor_task_run", return_value=True
        ) as update_mock:
            with self.assertRaises(HTTPException):
                await reply_server.execute_skill_monitor_task(task, 7, scheduled_run=True)

        kwargs = update_mock.call_args.kwargs
        self.assertEqual(kwargs["status"], "failed")
        self.assertEqual(kwargs["error"], "搜索失败")
        self.assertIsNotNone(kwargs["next_run_at"])


class SkillMonitorSchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_and_stop_own_the_polling_task(self):
        scheduler = scheduler_module.SkillMonitorScheduler(poll_interval_seconds=3600)
        with patch.object(scheduler_module.db_manager, "reset_running_skill_monitor_tasks", return_value=0), patch.object(
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
        with patch.object(scheduler_module.db_manager, "list_due_skill_monitor_tasks", return_value=due):
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
        with patch.object(scheduler_module.db_manager, "reset_running_skill_monitor_tasks", return_value=0), patch.object(
            scheduler_module.db_manager,
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


if __name__ == "__main__":
    unittest.main()
