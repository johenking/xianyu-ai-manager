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

    def test_generic_webhook_receives_stable_idempotency_key(self):
        response = self._successful_response({"ok": True})
        with patch.object(reply_server.requests, "post", return_value=response) as post_mock:
            reply_server._send_skill_notification_to_channel(
                {"type": "webhook", "config": {"webhook_url": "https://example.test/hook"}},
                {"keyword": "iPhone"},
                {
                    "title": "iPhone 15",
                    "_delivery_idempotency_key": "delivery:v1:synthetic",
                },
            )

        kwargs = post_mock.call_args.kwargs
        self.assertEqual(
            kwargs["headers"]["Idempotency-Key"],
            "delivery:v1:synthetic",
        )
        self.assertEqual(
            kwargs["json"]["idempotency_key"],
            "delivery:v1:synthetic",
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


class SkillCapabilityMatrixTests(unittest.TestCase):
    def test_code_presence_does_not_claim_configuration_or_real_use(self):
        empty_evidence = {
            "last_real_search": None,
            "last_scheduled_run": None,
            "last_ai_decision": None,
            "last_real_delivery": None,
            "last_delivery_attempt": None,
        }
        with (
            patch.object(
                reply_server,
                "get_skill_monitor_feature_state",
                return_value={
                    "effective": {
                        "skill_monitor_enabled": False,
                        "skill_monitor_scheduler_enabled": False,
                        "skill_monitor_delivery_enabled": False,
                        "skill_monitor_mtop_enabled": False,
                    }
                },
            ),
            patch.object(
                reply_server.db_manager,
                "get_all_cookies",
                return_value={"account-1": "redacted"},
            ),
            patch.object(
                reply_server.db_manager,
                "get_owned_cookie_search_context",
                return_value={"state": "ready"},
            ),
            patch.object(
                reply_server.db_manager,
                "list_skill_monitor_tasks",
                return_value=[{
                    "id": 3,
                    "enabled": True,
                    "account_id": "account-1",
                }],
            ),
            patch.object(
                reply_server.db_manager,
                "get_skill_capability_evidence",
                return_value=empty_evidence,
            ),
            patch(
                "skill_monitor_mtop_adapter.get_mtop_offline_contract_status",
                return_value={
                    "contract_version": "stage-c-offline-v1",
                    "gate": {"executable": False},
                    "canary": {"verification": "unverified"},
                    "real_acceptance": {
                        "blocker_code": "dedicated_test_account_required"
                    },
                },
            ),
        ):
            data = reply_server.get_skill_capabilities({"user_id": 7})["data"]

        self.assertEqual(set(data), {
            "code_present",
            "config_ready",
            "last_real_search",
            "last_scheduled_run",
            "last_ai_decision",
            "last_real_delivery",
        })
        self.assertTrue(data["code_present"]["available"])
        self.assertFalse(data["config_ready"]["available"])
        self.assertEqual(data["last_real_search"]["state"], "never")
        self.assertEqual(data["last_real_delivery"]["label"], "从未确认")
        offline = data["code_present"]["evidence"]["offline_mtop_adapter"]
        self.assertFalse(offline["gate"]["executable"])
        self.assertEqual(offline["canary"]["verification"], "unverified")
        self.assertEqual(
            offline["real_acceptance"]["blocker_code"],
            "dedicated_test_account_required",
        )


class SkillMonitorExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_heartbeat_hard_kill_cancels_the_owner(self):
        stop_event = asyncio.Event()
        lease_lost = asyncio.Event()
        kill_switch_disabled = asyncio.Event()
        owner = Mock()
        owner.done.return_value = False

        with patch.object(
            reply_server,
            "skill_monitor_feature_enabled",
            return_value=False,
        ), patch.object(
            reply_server.db_manager,
            "heartbeat_skill_monitor_run",
        ) as heartbeat_mock:
            await reply_server._heartbeat_skill_monitor_run(
                41,
                "claim-token",
                stop_event,
                lease_lost,
                kill_switch_disabled,
                owner,
            )

        self.assertTrue(kill_switch_disabled.is_set())
        self.assertFalse(lease_lost.is_set())
        owner.cancel.assert_called_once_with()
        heartbeat_mock.assert_not_called()

    async def test_global_kill_switch_blocks_before_task_claim(self):
        task = {"id": 3, "keyword": "iPhone"}
        with patch.object(
            reply_server,
            "skill_monitor_feature_enabled",
            return_value=False,
        ), patch.object(
            reply_server.db_manager,
            "claim_skill_monitor_run",
        ) as claim_mock:
            with self.assertRaises(HTTPException) as raised:
                await reply_server.execute_skill_monitor_task(task, 7)

        self.assertEqual(raised.exception.status_code, 503)
        self.assertIn("全局开关关闭", raised.exception.detail)
        claim_mock.assert_not_called()

    async def test_scheduler_kill_switch_blocks_scheduled_run_before_claim(self):
        task = {"id": 3, "keyword": "iPhone"}

        def enabled(key):
            return key == "skill_monitor_enabled"

        with patch.object(
            reply_server,
            "skill_monitor_feature_enabled",
            side_effect=enabled,
        ), patch.object(
            reply_server.db_manager,
            "claim_skill_monitor_run",
        ) as claim_mock:
            with self.assertRaises(HTTPException) as raised:
                await reply_server.execute_skill_monitor_task(
                    task,
                    7,
                    scheduled_run=True,
                )

        self.assertEqual(raised.exception.status_code, 503)
        self.assertIn("调度开关关闭", raised.exception.detail)
        claim_mock.assert_not_called()

    async def test_existing_result_is_not_inserted_or_notified_again(self):
        search_result = {
            "is_real_data": True,
            "source": "playwright",
            "items": [{"item_id": "item-1", "title": "iPhone 15", "item_url": "https://example.test/item-1"}],
        }
        task = {
            "id": 3,
            "keyword": "iPhone",
            "account_id": "account-1",
            "notify_enabled": True,
        }

        with patch("utils.item_search.search_xianyu_items", new=AsyncMock(return_value=search_result)), patch.object(
            reply_server.db_manager,
            "skill_monitor_result_exists",
            return_value=True,
            create=True,
        ) as exists_mock, patch.object(
            reply_server.db_manager, "persist_skill_monitor_match"
        ) as persist_mock, patch.object(
            reply_server,
            "_send_skill_notification_to_channel",
        ) as send_mock:
            result_ids, raw_count, _ = await reply_server._run_real_skill_monitor(task, 7)

        self.assertEqual(result_ids, [])
        self.assertEqual(raw_count, 1)
        exists_mock.assert_called_once_with(3, 7, "https://example.test/item-1", "item-1")
        persist_mock.assert_not_called()
        send_mock.assert_not_called()

    async def test_new_result_uses_transactional_outbox_without_direct_send(self):
        search_result = {
            "is_real_data": True,
            "source": "playwright",
            "items": [
                {
                    "item_id": "item-1",
                    "title": "iPhone 15",
                    "item_url": "https://example.test/item-1",
                }
            ],
        }
        task = {
            "id": 3,
            "keyword": "iPhone",
            "account_id": "account-1",
            "notify_enabled": True,
        }
        persisted = {
            "state": "created",
            "created": True,
            "result_id": 11,
            "event_id": 12,
            "delivery_ids": [13],
            "notify_status": "queued",
        }
        with patch(
            "utils.item_search.search_xianyu_items",
            new=AsyncMock(return_value=search_result),
        ), patch.object(
            reply_server.db_manager,
            "skill_monitor_result_exists",
            return_value=False,
        ), patch.object(
            reply_server.db_manager,
            "persist_skill_monitor_match",
            return_value=persisted,
        ) as persist_mock, patch.object(
            reply_server,
            "_send_skill_notification_to_channel",
        ) as send_mock:
            result_ids, raw_count, _ = await reply_server._run_real_skill_monitor(
                task,
                7,
                run_id=41,
                claim_token="claim-token",
            )

        self.assertEqual(result_ids, [11])
        self.assertEqual(raw_count, 1)
        payload = persist_mock.call_args.args[0]
        self.assertEqual(payload["item_id"], "item-1")
        self.assertNotIn("keyword", payload["raw_data"])
        persist_mock.assert_called_once_with(
            payload,
            run_id=41,
            claim_token="claim-token",
        )
        send_mock.assert_not_called()

    async def test_rejected_ai_filter_still_records_a_lease_scoped_decision(self):
        search_result = {
            "is_real_data": True,
            "source": "playwright",
            "items": [{
                "item_id": "item-1",
                "title": "iPhone 15",
                "item_url": "https://example.test/item-1",
            }],
        }
        task = {
            "id": 3,
            "keyword": "iPhone",
            "account_id": "account-1",
            "ai_filter": "只保留低价商品",
            "notify_enabled": False,
        }
        with (
            patch(
                "utils.item_search.search_xianyu_items",
                new=AsyncMock(return_value=search_result),
            ),
            patch.object(
                reply_server,
                "_user_ai_cookie_settings",
                return_value=("account-1", {"model_name": "test"}),
            ),
            patch.object(
                reply_server,
                "_user_has_ai_configuration",
                return_value=True,
            ),
            patch.object(
                reply_server,
                "_run_skill_ai_filter_bounded",
                new=AsyncMock(return_value={
                    "recommended": False,
                    "score": 42,
                    "reason": "not recommended",
                }),
            ),
            patch.object(
                reply_server.db_manager,
                "skill_monitor_result_exists",
                return_value=False,
            ),
            patch.object(
                reply_server.db_manager,
                "record_skill_monitor_ai_decision",
                return_value={"state": "recorded", "recorded": True},
            ) as decision_mock,
            patch.object(
                reply_server.db_manager,
                "persist_skill_monitor_match",
            ) as persist_mock,
        ):
            result_ids, raw_count, _ = await reply_server._run_real_skill_monitor(
                task,
                7,
                run_id=41,
                claim_token="claim-token",
            )

        self.assertEqual(result_ids, [])
        self.assertEqual(raw_count, 1)
        decision_mock.assert_called_once_with(
            run_id=41,
            claim_token="claim-token",
            task_id=3,
            user_id=7,
            item_identity="item-1",
            recommended=False,
            score=42.0,
        )
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
            "state": "claimed",
            "claimed": True,
            "run_id": 41,
            "run_token": "run-token",
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

        kwargs = finish_mock.call_args.kwargs
        self.assertEqual(kwargs["status"], "failed")
        self.assertEqual(kwargs["error_message"], "搜索失败")
        self.assertEqual(kwargs["error_code"], "http_502")
        self.assertIsNotNone(kwargs["next_run_at"])

    async def test_cancelled_run_is_marked_interrupted(self):
        started = asyncio.Event()

        async def blocked_monitor(*_args, **_kwargs):
            started.set()
            await asyncio.Event().wait()

        claim = {
            "state": "claimed",
            "claimed": True,
            "run_id": 42,
            "run_token": "run-token",
            "claim_token": "claim-token",
            "account_id": "account-1",
        }
        task = {
            "id": 3,
            "user_id": 7,
            "keyword": "iPhone",
            "schedule_enabled": True,
            "schedule_interval_minutes": 30,
        }
        with patch.object(
            reply_server,
            "skill_monitor_feature_enabled",
            return_value=True,
        ), patch.object(
            reply_server.db_manager,
            "claim_skill_monitor_run",
            return_value=claim,
        ), patch.object(
            reply_server,
            "_run_real_skill_monitor",
            new=blocked_monitor,
        ), patch.object(
            reply_server.db_manager,
            "finish_skill_monitor_run",
            return_value=True,
        ) as finish_mock:
            execution = asyncio.create_task(
                reply_server.execute_skill_monitor_task(task, 7, scheduled_run=True)
            )
            await asyncio.wait_for(started.wait(), timeout=1)
            execution.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await execution

        kwargs = finish_mock.call_args.kwargs
        self.assertEqual(kwargs["status"], "interrupted")
        self.assertEqual(kwargs["error_code"], "shutdown_interrupted")


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
        ), patch.object(
            scheduler_module.db_manager, "list_due_skill_monitor_tasks", return_value=due
        ):
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
        ), patch.object(
            scheduler_module.db_manager,
            "list_due_skill_monitor_tasks",
            side_effect=[[{"id": 9, "user_id": 7}], []],
        ):
            await scheduler.start()
            await asyncio.wait_for(started.wait(), timeout=1)
            await scheduler.stop()

        self.assertTrue(cancelled.is_set())

    async def test_scheduler_stays_stopped_when_fail_closed_switch_is_off(self):
        scheduler = scheduler_module.SkillMonitorScheduler(poll_interval_seconds=3600)
        with patch.object(scheduler_module, "skill_monitor_feature_enabled", return_value=False), patch.object(
            scheduler_module.db_manager, "recover_stale_skill_monitor_runs"
        ) as recover_runs_mock, patch.object(
            scheduler_module.db_manager, "recover_stale_skill_monitor_deliveries"
        ) as recover_deliveries_mock, patch.object(
            scheduler_module.db_manager, "list_due_skill_monitor_tasks"
        ) as due_mock:
            await scheduler.start()
            self.assertFalse(scheduler.running)
            self.assertEqual(await scheduler.run_due_once(), 0)

        recover_runs_mock.assert_not_called()
        recover_deliveries_mock.assert_not_called()
        due_mock.assert_not_called()


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
