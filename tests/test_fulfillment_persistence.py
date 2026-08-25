"""Persistent auto-fulfillment attempts and batch-card reservations."""

import os
from pathlib import Path
import tempfile
import unittest

from db_manager import DBManager


class FulfillmentPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.db_path = self.root / "fulfillment.db"
        self.previous_env = {}
        for name in (
            "ACCOUNT_CREDENTIAL_KEY_FILE",
            "SYSTEM_SECRET_KEY_FILE",
            "AI_PROVIDER_KEY_FILE",
        ):
            self.previous_env[name] = os.environ.get(name)
            os.environ[name] = str(self.root / f".{name.lower()}")

        self.db = DBManager(str(self.db_path))
        self.assertTrue(
            self.db.create_user(
                "seller-one", "seller-one@example.test", "Strong-pass-2026!"
            )
        )
        self.assertTrue(
            self.db.create_user(
                "seller-two", "seller-two@example.test", "Strong-pass-2026!"
            )
        )
        self.user_one = self.db.get_user_by_username("seller-one")
        self.user_two = self.db.get_user_by_username("seller-two")
        with self.db.lock:
            self.db.conn.executemany(
                "INSERT INTO cookies (id, value, user_id) VALUES (?, ?, ?)",
                (
                    ("account-one", "synthetic-cookie-one", self.user_one["id"]),
                    ("account-two", "synthetic-cookie-two", self.user_two["id"]),
                ),
            )
            self.db.conn.execute(
                "INSERT INTO cards "
                "(name, type, data_content, enabled, user_id) VALUES (?, 'data', ?, 1, ?)",
                ("batch-one", "code-a\ncode-b\ncode-c", self.user_one["id"]),
            )
            self.card_one = self.db.conn.execute(
                "SELECT last_insert_rowid()"
            ).fetchone()[0]
            self.db.conn.execute(
                "INSERT INTO cards "
                "(name, type, data_content, enabled, user_id) VALUES (?, 'data', ?, 1, ?)",
                ("batch-two", "foreign-code", self.user_two["id"]),
            )
            self.card_two = self.db.conn.execute(
                "SELECT last_insert_rowid()"
            ).fetchone()[0]
            self.db.conn.commit()

    def tearDown(self):
        self.db.close()
        for name, value in self.previous_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        self.tempdir.cleanup()

    def _begin(self, order_id="order-one", quantity=1):
        if self.db.get_order_by_id(order_id) is None:
            self.assertTrue(
                self.db.insert_or_update_order(
                    order_id=order_id,
                    cookie_id="account-one",
                    quantity=str(quantity),
                    order_status="pending_ship",
                )
            )
        return self.db.begin_fulfillment_attempt(
            order_id=order_id,
            cookie_id="account-one",
            expected_quantity=quantity,
        )

    def _card_lines(self, card_id=None):
        row = self.db.conn.execute(
            "SELECT data_content FROM cards WHERE id = ?",
            (card_id or self.card_one,),
        ).fetchone()
        return [line for line in str(row[0] or "").splitlines() if line]

    def test_begin_is_single_process_exclusive_and_restart_recoverable(self):
        first = self._begin(quantity=2)
        self.assertEqual(first["outcome"], "acquired")
        self.assertEqual(self._begin(quantity=2)["outcome"], "busy")

        attempt_id = first["attempt_id"]
        self.db.close()
        self.db = DBManager(str(self.db_path))
        recovered = self._begin(quantity=2)
        self.assertEqual(recovered["outcome"], "acquired")
        self.assertEqual(recovered["attempt_id"], attempt_id)

    def test_batch_reservation_is_atomic_idempotent_and_releasable(self):
        attempt = self._begin(quantity=2)
        attempt_id = attempt["attempt_id"]

        values = self.db.reserve_batch_card_data(attempt_id, self.card_one, 2)
        self.assertEqual(values, ["code-a", "code-b"])
        self.assertEqual(self._card_lines(), ["code-c"])
        self.assertEqual(
            self.db.reserve_batch_card_data(attempt_id, self.card_one, 2),
            values,
        )
        self.assertEqual(self._card_lines(), ["code-c"])

        self.assertTrue(
            self.db.release_fulfillment_attempt(attempt_id, "platform_confirm_failed")
        )
        self.assertEqual(self._card_lines(), ["code-a", "code-b", "code-c"])
        self.assertEqual(
            self.db.get_fulfillment_attempt(attempt_id)["state"], "released"
        )

        retry = self._begin(quantity=2)
        self.assertEqual(retry["outcome"], "acquired")
        self.assertEqual(retry["attempt_id"], attempt_id)
        self.assertEqual(
            self.db.reserve_batch_card_data(attempt_id, self.card_one, 2),
            ["code-a", "code-b"],
        )

    def test_insufficient_or_foreign_inventory_never_mutates_card(self):
        attempt_id = self._begin(quantity=4)["attempt_id"]
        self.assertIsNone(
            self.db.reserve_batch_card_data(attempt_id, self.card_one, 4)
        )
        self.assertEqual(self._card_lines(), ["code-a", "code-b", "code-c"])

        self.assertIsNone(
            self.db.reserve_batch_card_data(attempt_id, self.card_two, 1)
        )
        self.assertEqual(self._card_lines(self.card_two), ["foreign-code"])

    def test_restart_after_sending_enters_manual_review_without_release(self):
        attempt_id = self._begin()["attempt_id"]
        self.assertEqual(
            self.db.reserve_batch_card_data(attempt_id, self.card_one, 1),
            ["code-a"],
        )
        self.assertTrue(self.db.mark_fulfillment_sending(attempt_id))
        self.assertEqual(self._card_lines(), ["code-b", "code-c"])

        self.db.close()
        self.db = DBManager(str(self.db_path))
        recovered = self._begin()
        self.assertEqual(recovered["outcome"], "manual_review")
        self.assertEqual(
            self.db.get_fulfillment_attempt(attempt_id)["state"], "manual_review"
        )
        self.assertEqual(self._card_lines(), ["code-b", "code-c"])
        self.assertFalse(
            self.db.release_fulfillment_attempt(attempt_id, "unsafe_release")
        )

    def test_commit_is_idempotent_and_blocks_duplicate_delivery(self):
        attempt_id = self._begin()["attempt_id"]
        self.assertEqual(
            self.db.reserve_batch_card_data(attempt_id, self.card_one, 1),
            ["code-a"],
        )
        self.assertTrue(self.db.mark_fulfillment_sending(attempt_id))
        self.assertTrue(self.db.commit_fulfillment_attempt(attempt_id, 1))
        self.assertTrue(self.db.commit_fulfillment_attempt(attempt_id, 1))
        self.assertEqual(self._begin()["outcome"], "already_completed")
        self.assertEqual(self._card_lines(), ["code-b", "code-c"])
        committed_order = self.db.get_order_by_id("order-one")
        self.assertTrue(committed_order["system_shipped"])
        self.assertEqual(committed_order["order_status"], "shipped")
        self.assertEqual(committed_order["status_source"], "system_fulfillment")

    def test_partial_or_uncertain_send_stays_quarantined(self):
        attempt_id = self._begin(quantity=2)["attempt_id"]
        self.assertEqual(
            self.db.reserve_batch_card_data(attempt_id, self.card_one, 2),
            ["code-a", "code-b"],
        )
        self.assertTrue(self.db.mark_fulfillment_sending(attempt_id))
        self.assertTrue(
            self.db.mark_fulfillment_manual_review(
                attempt_id, "send_receipt_uncertain", sent_count=1
            )
        )
        state = self.db.get_fulfillment_attempt(attempt_id)
        self.assertEqual(state["state"], "manual_review")
        self.assertEqual(state["sent_count"], 1)
        self.assertEqual(self._card_lines(), ["code-c"])

    def test_commit_rolls_back_when_owned_order_row_disappears(self):
        attempt_id = self._begin()["attempt_id"]
        self.assertEqual(
            self.db.reserve_batch_card_data(attempt_id, self.card_one, 1),
            ["code-a"],
        )
        self.assertTrue(self.db.mark_fulfillment_sending(attempt_id))
        with self.db.lock:
            self.db.conn.execute(
                "DELETE FROM orders WHERE order_id = ? AND cookie_id = ?",
                ("order-one", "account-one"),
            )
            self.db.conn.commit()

        self.assertFalse(self.db.commit_fulfillment_attempt(attempt_id, 1))
        state = self.db.get_fulfillment_attempt(attempt_id)
        self.assertEqual(state["state"], "sending")
        self.assertEqual(state["reservation_values"], ["code-a"])
        self.assertEqual(self._card_lines(), ["code-b", "code-c"])

    def test_any_fulfillment_history_keeps_resource_auditable_after_release(self):
        attempt_id = self._begin()["attempt_id"]
        self.assertEqual(
            self.db.reserve_batch_card_data(attempt_id, self.card_one, 1),
            ["code-a"],
        )
        self.assertFalse(
            self.db.delete_card(self.card_one, user_id=self.user_one["id"])
        )
        self.assertTrue(
            self.db.release_fulfillment_attempt(attempt_id, "pre_send_cancelled")
        )
        self.assertFalse(
            self.db.delete_card(self.card_one, user_id=self.user_one["id"])
        )

    def test_system_backup_contract_contains_durable_fulfillment_tables(self):
        attempt_id = self._begin()["attempt_id"]
        self.assertEqual(
            self.db.reserve_batch_card_data(attempt_id, self.card_one, 1),
            ["code-a"],
        )
        backup = self.db.export_backup()
        self.assertIn("fulfillment_attempts", backup["data"])
        self.assertIn("fulfillment_card_reservations", backup["data"])

        self.assertTrue(
            self.db.release_fulfillment_attempt(attempt_id, "mutate_before_restore")
        )
        self.assertEqual(self._card_lines(), ["code-a", "code-b", "code-c"])
        self.assertTrue(self.db.import_backup(backup))

        restored = self.db.get_fulfillment_attempt(attempt_id)
        self.assertEqual(restored["state"], "prepared")
        self.assertEqual(restored["reservation_values"], ["code-a"])
        self.assertEqual(self._card_lines(), ["code-b", "code-c"])


if __name__ == "__main__":
    unittest.main()
