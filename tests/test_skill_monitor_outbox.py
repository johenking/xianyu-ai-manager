import asyncio
import json
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests

from db_manager import DBManager
import reply_server
import skill_monitor_delivery_dispatcher as dispatcher_module


class SkillMonitorOutboxPersistenceTests(unittest.TestCase):
    def setUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.db = DBManager(self.db_path)

    def tearDown(self):
        self.db.conn.close()
        os.unlink(self.db_path)

    def _task_and_claim(self, *, notify_enabled=True):
        task_id = self.db.create_skill_monitor_task(
            1,
            {
                "name": "outbox-test",
                "keyword": "synthetic-keyword",
                "account_id": "account-1",
                "notify_enabled": notify_enabled,
            },
        )
        claim = self.db.claim_skill_monitor_run(
            task_id,
            1,
            trigger_type="manual",
            now=100,
        )
        return task_id, claim

    def _result(self, task_id, *, suffix="1"):
        return {
            "task_id": task_id,
            "user_id": 1,
            "title": f"synthetic-{suffix}",
            "price": 99.5,
            "region": "synthetic-region",
            "item_url": f"https://example.test/item-{suffix}",
            "item_id": f"item-{suffix}",
            "item_image": "https://example.test/image.jpg",
            "seller_name": "synthetic-seller",
            "ai_score": 80,
            "ai_reason": "synthetic-reason",
            "raw_data": {
                "source": "playwright",
                "is_real_data": True,
                "item_id": f"item-{suffix}",
                "full_response": {"private": "must-not-persist"},
            },
        }

    def _channel(self, name, channel_type):
        return self.db.create_notification_channel(
            name,
            channel_type,
            json.dumps({"url": "https://example.test/webhook"}),
            user_id=1,
        )

    def test_result_event_and_each_delivery_commit_in_one_transaction(self):
        self._channel("webhook", "webhook")
        self._channel("bark", "bark")
        task_id, claim = self._task_and_claim()
        result = self.db.persist_skill_monitor_match(
            self._result(task_id),
            run_id=claim["run_id"],
            claim_token=claim["claim_token"],
            now=110,
        )
        self.assertTrue(result["created"])
        self.assertEqual(result["notify_status"], "queued")
        self.assertEqual(len(result["delivery_ids"]), 2)
        self.assertEqual(
            self.db.conn.execute(
                "SELECT COUNT(*) FROM skill_monitor_result_identities"
            ).fetchone()[0],
            2,
        )
        self.assertEqual(
            self.db.conn.execute(
                "SELECT COUNT(*) FROM skill_monitor_events WHERE event_type = 'result_first_seen'"
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            self.db.conn.execute(
                "SELECT COUNT(*) FROM skill_monitor_deliveries"
            ).fetchone()[0],
            2,
        )
        raw_data = json.loads(
            self.db.conn.execute(
                "SELECT raw_data FROM skill_monitor_results WHERE id = ?",
                (result["result_id"],),
            ).fetchone()[0]
        )
        self.assertNotIn("full_response", raw_data)
        self.assertEqual(raw_data["item_id"], "item-1")

        duplicate = self.db.persist_skill_monitor_match(
            self._result(task_id),
            run_id=claim["run_id"],
            claim_token=claim["claim_token"],
            now=111,
        )
        self.assertEqual(duplicate["state"], "duplicate")
        self.assertEqual(
            self.db.conn.execute(
                "SELECT COUNT(*) FROM skill_monitor_results"
            ).fetchone()[0],
            1,
        )

    def test_outbox_insert_failure_rolls_back_result_event_and_identities(self):
        self._channel("webhook", "webhook")
        task_id, claim = self._task_and_claim()
        self.db.conn.execute(
            """
            CREATE TRIGGER synthetic_outbox_failure
            BEFORE INSERT ON skill_monitor_deliveries
            BEGIN
                SELECT RAISE(ABORT, 'synthetic outbox failure');
            END
            """
        )
        self.db.conn.commit()
        result = self.db.persist_skill_monitor_match(
            self._result(task_id),
            run_id=claim["run_id"],
            claim_token=claim["claim_token"],
            now=110,
        )
        self.assertEqual(result["state"], "error")
        self.assertEqual(
            self.db.conn.execute(
                "SELECT COUNT(*) FROM skill_monitor_results"
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            self.db.conn.execute(
                "SELECT COUNT(*) FROM skill_monitor_result_identities"
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            self.db.conn.execute(
                "SELECT COUNT(*) FROM skill_monitor_events WHERE event_type = 'result_first_seen'"
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            self.db.conn.execute(
                "SELECT COUNT(*) FROM skill_monitor_deliveries"
            ).fetchone()[0],
            0,
        )

    def test_wrong_run_claim_cannot_persist_and_notifications_can_be_disabled(self):
        task_id, claim = self._task_and_claim(notify_enabled=False)
        rejected = self.db.persist_skill_monitor_match(
            self._result(task_id),
            run_id=claim["run_id"],
            claim_token="wrong-token",
            now=110,
        )
        self.assertEqual(rejected["state"], "lease_lost")
        created = self.db.persist_skill_monitor_match(
            self._result(task_id),
            run_id=claim["run_id"],
            claim_token=claim["claim_token"],
            now=110,
        )
        self.assertEqual(created["notify_status"], "disabled")
        self.assertEqual(created["delivery_ids"], [])


class SkillMonitorDeliveryDispatcherTests(unittest.IsolatedAsyncioTestCase):
    def _delivery(self):
        return {
            "id": 11,
            "idempotency_key": "delivery:v1:synthetic",
            "claim_token": "claim-token",
            "channel_type": "webhook",
        }

    def _context(self):
        return {
            "delivery": {"id": 11},
            "task": {
                "id": 3,
                "name": "synthetic-task",
                "keyword": "synthetic-keyword",
                "notify_enabled": True,
            },
            "result": {
                "id": 7,
                "title": "synthetic-item",
                "item_url": "https://example.test/item",
            },
            "channel": {
                "id": 5,
                "type": "webhook",
                "enabled": True,
                "config": {"url": "https://example.test/webhook"},
            },
        }

    async def test_successful_dispatch_uses_stable_idempotency_key_and_marks_sent(self):
        dispatcher = dispatcher_module.SkillMonitorDeliveryDispatcher()
        with patch.object(
            dispatcher_module,
            "skill_monitor_feature_enabled",
            return_value=True,
        ), patch.object(
            dispatcher_module.db_manager,
            "get_skill_monitor_delivery_context",
            return_value=self._context(),
        ), patch.object(
            dispatcher_module.db_manager,
            "finish_skill_monitor_delivery",
            return_value=True,
        ) as finish_mock, patch.object(
            reply_server,
            "_send_skill_notification_to_channel",
        ) as send_mock:
            await dispatcher._execute(self._delivery())

        result_payload = send_mock.call_args.args[2]
        self.assertEqual(
            result_payload["_delivery_idempotency_key"],
            "delivery:v1:synthetic",
        )
        self.assertEqual(finish_mock.call_args.kwargs["status"], "sent")

    async def test_network_timeout_is_unknown_and_never_auto_retried(self):
        dispatcher = dispatcher_module.SkillMonitorDeliveryDispatcher()
        with patch.object(
            dispatcher_module,
            "skill_monitor_feature_enabled",
            return_value=True,
        ), patch.object(
            dispatcher_module.db_manager,
            "get_skill_monitor_delivery_context",
            return_value=self._context(),
        ), patch.object(
            dispatcher_module.db_manager,
            "finish_skill_monitor_delivery",
            return_value=True,
        ) as finish_mock, patch.object(
            reply_server,
            "_send_skill_notification_to_channel",
            side_effect=requests.Timeout("synthetic timeout"),
        ):
            await dispatcher._execute(self._delivery())

        kwargs = finish_mock.call_args.kwargs
        self.assertEqual(kwargs["status"], "unknown")
        self.assertEqual(kwargs["error_code"], "send_outcome_unknown")
        self.assertNotIn("next_attempt_at", kwargs)

    async def test_missing_channel_fails_before_any_send(self):
        dispatcher = dispatcher_module.SkillMonitorDeliveryDispatcher()
        context = self._context()
        context["channel"] = None
        with patch.object(
            dispatcher_module,
            "skill_monitor_feature_enabled",
            return_value=True,
        ), patch.object(
            dispatcher_module.db_manager,
            "get_skill_monitor_delivery_context",
            return_value=context,
        ), patch.object(
            dispatcher_module.db_manager,
            "finish_skill_monitor_delivery",
            return_value=True,
        ) as finish_mock, patch.object(
            reply_server,
            "_send_skill_notification_to_channel",
        ) as send_mock:
            await dispatcher._execute(self._delivery())

        send_mock.assert_not_called()
        self.assertEqual(finish_mock.call_args.kwargs["status"], "failed")
        self.assertEqual(
            finish_mock.call_args.kwargs["error_code"],
            "channel_unavailable",
        )

    async def test_dispatcher_stays_stopped_when_delivery_switch_is_off(self):
        dispatcher = dispatcher_module.SkillMonitorDeliveryDispatcher()
        with patch.object(
            dispatcher_module,
            "skill_monitor_feature_enabled",
            return_value=False,
        ), patch.object(
            dispatcher_module.db_manager,
            "recover_stale_skill_monitor_deliveries",
        ) as recover_mock:
            await dispatcher.start()
            self.assertFalse(dispatcher.running)
            self.assertEqual(await dispatcher.run_once(), 0)

        recover_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
