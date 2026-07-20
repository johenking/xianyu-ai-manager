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
        task = self.db.get_skill_monitor_task(task_id, 1)
        self.assertFalse(task["schedule_enabled"])
        self.assertIsNone(task["next_run_at"])
        self.assertEqual(self.db.list_due_skill_monitor_tasks(), [])

    def test_action_required_finish_pauses_schedule_atomically(self):
        task_id = self._task()
        claim = self.db.claim_skill_monitor_run(
            task_id,
            1,
            trigger_type="scheduled",
            now=100,
        )
        self.assertTrue(claim["claimed"])
        self.assertTrue(
            self.db.finish_skill_monitor_run(
                claim["run_id"],
                claim["claim_token"],
                status="action_required",
                error_code="risk_control",
                error_message="平台要求人工处理",
                next_run_at="2099-01-01 00:00:00",
                now=101,
            )
        )
        task = self.db.get_skill_monitor_task(task_id, 1)
        self.assertFalse(task["schedule_enabled"])
        self.assertIsNone(task["next_run_at"])

    def test_expired_monitor_records_are_cleaned_without_touching_future_rows(self):
        task_id = self._task(scheduled=False)
        expired_delivery = self._delivery(task_id, suffix="expired")
        future_delivery = self._delivery(task_id, suffix="future")
        self.db.conn.executescript(
            """
            UPDATE skill_monitor_results
            SET retention_until = CASE
                WHEN item_url LIKE '%%expired%%' THEN 0 ELSE 9999 END;
            UPDATE skill_monitor_events
            SET retention_until = CASE
                WHEN result_id IN (
                    SELECT id FROM skill_monitor_results WHERE item_url LIKE '%%expired%%'
                ) THEN 0 ELSE 9999 END;
            UPDATE skill_monitor_deliveries
            SET retention_until = CASE
                WHEN id = %d THEN 0 ELSE 9999 END,
                status = CASE WHEN id = %d THEN 'sent' ELSE status END;
            """ % (expired_delivery, expired_delivery)
        )
        self.db.conn.commit()

        result = self.db.cleanup_expired_skill_monitor_records(now=1000)

        self.assertGreater(result["deliveries"], 0)
        self.assertEqual(
            self.db.conn.execute(
                "SELECT COUNT(*) FROM skill_monitor_deliveries WHERE id = ?",
                (expired_delivery,),
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            self.db.conn.execute(
                "SELECT COUNT(*) FROM skill_monitor_deliveries WHERE id = ?",
                (future_delivery,),
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            self.db.conn.execute(
                "SELECT COUNT(*) FROM skill_monitor_results WHERE item_url LIKE '%expired%'"
            ).fetchone()[0],
            0,
        )
        repeated = self.db.cleanup_expired_skill_monitor_records(now=1000)
        self.assertEqual(repeated["deliveries"], 0)
        self.assertEqual(repeated["events"], 0)
        self.assertEqual(repeated["results"], 0)
        self.assertEqual(
            self.db.conn.execute("PRAGMA integrity_check").fetchone()[0],
            "ok",
        )
        self.assertEqual(
            self.db.conn.execute("PRAGMA foreign_key_check").fetchall(),
            [],
        )

    def test_cleanup_preserves_active_leases_and_legacy_null_retention(self):
        task_id = self._task(scheduled=False)
        run = self.db.claim_skill_monitor_run(
            task_id,
            1,
            trigger_type="manual",
            lease_seconds=60,
            now=100,
        )
        delivery_id = self._delivery(task_id, suffix="active")
        delivery = self.db.claim_skill_monitor_delivery(
            delivery_id=delivery_id,
            lease_seconds=60,
            now=100,
        )
        legacy_result_id = self.db.create_skill_monitor_result({
            "task_id": task_id,
            "user_id": 1,
            "title": "legacy-retention",
            "item_url": "https://example.test/legacy-retention",
        })
        self.db.conn.execute(
            "UPDATE skill_monitor_runs SET retention_until = 0 WHERE id = ?",
            (run["run_id"],),
        )
        self.db.conn.execute(
            "UPDATE skill_monitor_deliveries SET retention_until = 0 WHERE id = ?",
            (delivery_id,),
        )
        self.db.conn.execute(
            """
            INSERT INTO skill_monitor_request_budgets (
                scope_type, scope_digest, window_started_at, window_seconds,
                request_count, retention_until, updated_at
            ) VALUES ('global', 'expired-budget', 0, 60, 1, 0, 0)
            """
        )
        self.db.conn.execute(
            """
            INSERT INTO skill_monitor_mtop_breakers (
                scope_digest, state, consecutive_failures,
                retention_until, updated_at
            ) VALUES ('expired-breaker', 'closed', 0, 0, 0)
            """
        )
        self.db.conn.commit()

        result = self.db.cleanup_expired_skill_monitor_records(now=120)

        self.assertEqual(result["request_budgets"], 1)
        self.assertEqual(result["mtop_breakers"], 1)
        self.assertIsNotNone(self.db.get_skill_monitor_run(run["run_id"]))
        self.assertEqual(
            self.db.conn.execute(
                "SELECT status FROM skill_monitor_deliveries WHERE id = ?",
                (delivery_id,),
            ).fetchone()[0],
            "sending",
        )
        self.assertEqual(
            self.db.conn.execute(
                "SELECT retention_until FROM skill_monitor_results WHERE id = ?",
                (legacy_result_id,),
            ).fetchone()[0],
            None,
        )
        self.assertTrue(delivery["claim_token"])

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
