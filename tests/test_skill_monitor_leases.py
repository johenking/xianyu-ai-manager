from concurrent.futures import ThreadPoolExecutor
import os
import tempfile
import unittest

from db_manager import DBManager


class SkillMonitorLeaseTests(unittest.TestCase):
    def setUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.db = DBManager(self.db_path)

    def tearDown(self):
        self.db.conn.close()
        os.unlink(self.db_path)

    def _task(self, *, account_id="account-1", scheduled=True):
        return self.db.create_skill_monitor_task(
            1,
            {
                "name": "lease-test",
                "keyword": "synthetic-keyword",
                "account_id": account_id,
                "schedule_enabled": scheduled,
                "next_run_at": "2000-01-01 00:00:00",
            },
        )

    def _delivery(self, task_id, *, suffix="1"):
        result_id = self.db.create_skill_monitor_result(
            {
                "task_id": task_id,
                "user_id": 1,
                "title": f"synthetic-{suffix}",
                "item_url": f"https://example.test/item-{suffix}",
                "raw_data": {"item_id": f"item-{suffix}"},
            }
        )
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            INSERT INTO skill_monitor_events (
                event_token, idempotency_key, event_type, result_id,
                task_id, user_id, payload_json
            ) VALUES (?, ?, 'result_first_seen', ?, ?, 1, '{}')
            """,
            (f"event-token-{suffix}", f"event-key-{suffix}", result_id, task_id),
        )
        event_id = cursor.lastrowid
        cursor.execute(
            """
            INSERT INTO skill_monitor_deliveries (
                idempotency_key, event_id, result_id, task_id, user_id,
                channel_type, status
            ) VALUES (?, ?, ?, ?, 1, 'webhook', 'pending')
            """,
            (f"delivery-key-{suffix}", event_id, result_id, task_id),
        )
        delivery_id = cursor.lastrowid
        self.db.conn.commit()
        return delivery_id

    def test_run_claim_heartbeat_and_finish_require_the_same_token(self):
        task_id = self._task()
        claim = self.db.claim_skill_monitor_run(
            task_id,
            1,
            trigger_type="scheduled",
            lease_seconds=60,
            now=100,
        )
        self.assertTrue(claim["claimed"])
        self.assertEqual(claim["attempt"], 1)
        self.assertEqual(
            self.db.claim_skill_monitor_run(
                task_id,
                1,
                trigger_type="manual",
                now=101,
            )["state"],
            "conflict",
        )
        self.assertFalse(
            self.db.heartbeat_skill_monitor_run(
                claim["run_id"],
                "wrong-token",
                now=120,
            )
        )
        self.assertTrue(
            self.db.heartbeat_skill_monitor_run(
                claim["run_id"],
                claim["claim_token"],
                lease_seconds=60,
                now=120,
            )
        )
        self.assertFalse(
            self.db.finish_skill_monitor_run(
                claim["run_id"],
                "wrong-token",
                status="success",
                now=121,
            )
        )
        self.assertTrue(
            self.db.finish_skill_monitor_run(
                claim["run_id"],
                claim["claim_token"],
                status="success",
                raw_result_count=5,
                accepted_result_count=2,
                now=121,
            )
        )
        run = self.db.get_skill_monitor_run(claim["run_id"])
        self.assertEqual(run["status"], "success")
        self.assertEqual(run["claim_token"], "")
        self.assertEqual(run["raw_result_count"], 5)
        self.assertEqual(run["accepted_result_count"], 2)

    def test_only_expired_run_is_recovered_and_next_attempt_is_linked(self):
        task_id = self._task()
        first = self.db.claim_skill_monitor_run(
            task_id,
            1,
            trigger_type="scheduled",
            lease_seconds=30,
            now=100,
        )
        self.assertEqual(self.db.recover_stale_skill_monitor_runs(now=129), 0)
        self.assertEqual(self.db.recover_stale_skill_monitor_runs(now=131), 1)
        interrupted = self.db.get_skill_monitor_run(first["run_id"])
        self.assertEqual(interrupted["status"], "interrupted")
        self.assertEqual(interrupted["error_code"], "lease_expired")

        second = self.db.claim_skill_monitor_run(
            task_id,
            1,
            trigger_type="scheduled",
            lease_seconds=30,
            now=132,
        )
        self.assertTrue(second["claimed"])
        self.assertEqual(second["attempt"], 2)
        recovered = self.db.get_skill_monitor_run(second["run_id"])
        self.assertEqual(recovered["recovered_from_run_id"], first["run_id"])

    def test_empty_account_is_action_required_and_cross_user_claim_is_hidden(self):
        task_id = self._task(account_id="")
        self.assertEqual(
            self.db.claim_skill_monitor_run(
                task_id,
                2,
                trigger_type="manual",
                now=100,
            )["state"],
            "not_found",
        )
        claim = self.db.claim_skill_monitor_run(
            task_id,
            1,
            trigger_type="manual",
            now=100,
        )
        self.assertEqual(claim["state"], "action_required")
        self.assertFalse(claim["claimed"])
        run = self.db.get_skill_monitor_run(claim["run_id"])
        self.assertEqual(run["status"], "action_required")
        self.assertEqual(run["error_code"], "account_required")

    def test_concurrent_run_claims_have_one_winner(self):
        task_id = self._task()

        def claim_once(_index):
            return self.db.claim_skill_monitor_run(
                task_id,
                1,
                trigger_type="manual",
                now=100,
            )["state"]

        with ThreadPoolExecutor(max_workers=4) as executor:
            states = list(executor.map(claim_once, range(4)))

        self.assertEqual(states.count("claimed"), 1)
        self.assertEqual(states.count("conflict"), 3)

    def test_delivery_claim_has_independent_lease_and_unknown_stale_state(self):
        task_id = self._task()
        delivery_id = self._delivery(task_id)
        claim = self.db.claim_skill_monitor_delivery(
            delivery_id=delivery_id,
            lease_seconds=60,
            now=100,
        )
        self.assertIsNotNone(claim)
        self.assertEqual(claim["attempt"], 1)
        self.assertIsNone(
            self.db.claim_skill_monitor_delivery(
                delivery_id=delivery_id,
                now=101,
            )
        )
        self.assertFalse(
            self.db.heartbeat_skill_monitor_delivery(
                delivery_id,
                "wrong-token",
                now=120,
            )
        )
        self.assertTrue(
            self.db.heartbeat_skill_monitor_delivery(
                delivery_id,
                claim["claim_token"],
                lease_seconds=60,
                now=120,
            )
        )
        self.assertEqual(
            self.db.recover_stale_skill_monitor_deliveries(now=181),
            1,
        )
        row = self.db.conn.execute(
            "SELECT status, claim_token, error_code FROM skill_monitor_deliveries WHERE id = ?",
            (delivery_id,),
        ).fetchone()
        self.assertEqual(row, ("unknown", "", "send_outcome_unknown"))
        self.assertEqual(
            self.db.conn.execute(
                """
                SELECT notify_status FROM skill_monitor_results
                WHERE id = (SELECT result_id FROM skill_monitor_deliveries WHERE id = ?)
                """,
                (delivery_id,),
            ).fetchone()[0],
            "unknown",
        )
        self.assertIsNone(
            self.db.claim_skill_monitor_delivery(
                delivery_id=delivery_id,
                now=182,
            )
        )

    def test_delivery_finish_rejects_wrong_token_and_records_confirmation(self):
        task_id = self._task()
        delivery_id = self._delivery(task_id, suffix="2")
        claim = self.db.claim_skill_monitor_delivery(
            delivery_id=delivery_id,
            lease_seconds=60,
            now=100,
        )
        self.assertFalse(
            self.db.finish_skill_monitor_delivery(
                delivery_id,
                "wrong-token",
                status="sent",
                now=110,
            )
        )
        self.assertTrue(
            self.db.finish_skill_monitor_delivery(
                delivery_id,
                claim["claim_token"],
                status="sent",
                now=110,
            )
        )
        row = self.db.conn.execute(
            "SELECT status, claim_token, sent_at, confirmed_at FROM skill_monitor_deliveries WHERE id = ?",
            (delivery_id,),
        ).fetchone()
        self.assertEqual(row[0], "sent")
        self.assertEqual(row[1], "")
        self.assertEqual(row[2], 110)
        self.assertEqual(row[3], 110)
        self.assertEqual(
            self.db.conn.execute(
                """
                SELECT notify_status FROM skill_monitor_results
                WHERE id = (SELECT result_id FROM skill_monitor_deliveries WHERE id = ?)
                """,
                (delivery_id,),
            ).fetchone()[0],
            "sent",
        )


if __name__ == "__main__":
    unittest.main()
