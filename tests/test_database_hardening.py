"""Database ownership, deletion, and backup-import hardening regressions."""

from copy import deepcopy
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import db_manager as db_manager_module
from db_manager import DBManager


class DatabaseHardeningTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.previous_env = {}
        for name in (
            "ACCOUNT_CREDENTIAL_KEY_FILE",
            "SYSTEM_SECRET_KEY_FILE",
            "AI_PROVIDER_KEY_FILE",
        ):
            self.previous_env[name] = os.environ.get(name)
            os.environ[name] = str(self.root / f".{name.lower()}")

        self.db = DBManager(str(self.root / "hardening.db"))
        self.assertTrue(
            self.db.create_user("owner-a", "owner-a@example.test", "Strong-pass-2026!")
        )
        self.assertTrue(
            self.db.create_user("owner-b", "owner-b@example.test", "Strong-pass-2026!")
        )
        self.owner_a = self.db.get_user_by_username("owner-a")
        self.owner_b = self.db.get_user_by_username("owner-b")
        with self.db.lock:
            self.db.conn.executemany(
                "INSERT INTO cookies (id, value, user_id) VALUES (?, ?, ?)",
                (
                    ("account-a", "unb=a; cookie2=redacted", self.owner_a["id"]),
                    ("account-b", "unb=b; cookie2=redacted", self.owner_b["id"]),
                ),
            )
            self.db.conn.execute(
                "INSERT INTO keywords (cookie_id, keyword, reply) VALUES (?, ?, ?)",
                ("account-a", "hello", "world"),
            )
            self.db.conn.commit()

    def tearDown(self):
        self.db.close()
        for name, value in self.previous_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        self.tempdir.cleanup()

    def test_all_manager_connections_enable_foreign_keys(self):
        self.assertEqual(self.db.conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.db.close()
        reopened = self.db.get_connection()
        self.assertEqual(reopened.execute("PRAGMA foreign_keys").fetchone()[0], 1)

    def test_delete_cookie_removes_all_cookie_scoped_children(self):
        self.db.record_item_metric_snapshot(
            user_id=self.owner_a["id"],
            cookie_id="account-a",
            item_id="item-a",
            observed_at=time.time(),
            source="seller_backend_verified",
            view_count=1,
        )
        self.db.record_item_metric_canary_result(
            user_id=self.owner_a["id"],
            cookie_id="account-a",
            success=True,
        )
        with self.db.lock:
            self.db.conn.execute(
                "INSERT INTO orders (order_id, order_status, cookie_id) VALUES (?, ?, ?)",
                ("order-a", "completed", "account-a"),
            )
            self.db.conn.execute(
                "INSERT INTO risk_control_logs (cookie_id, event_type) VALUES (?, ?)",
                ("account-a", "synthetic"),
            )
            self.db.conn.commit()

        self.assertTrue(self.db.delete_cookie("account-a"))

        for table in (
            "keywords",
            "orders",
            "risk_control_logs",
            "item_metric_snapshots",
            "item_metric_collection_states",
        ):
            count = self.db.conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE cookie_id = ?",
                ("account-a",),
            ).fetchone()[0]
            self.assertEqual(count, 0, table)
        self.assertIsNotNone(self.db.get_cookie("account-b"))

    def test_delete_user_removes_direct_and_cookie_scoped_rows(self):
        with self.db.lock:
            cursor = self.db.conn.cursor()
            cursor.execute(
                "INSERT INTO cards (name, type, text_content, user_id) VALUES (?, ?, ?, ?)",
                ("card-a", "text", "value", self.owner_a["id"]),
            )
            card_id = cursor.lastrowid
            cursor.execute(
                "INSERT INTO delivery_rules (keyword, card_id, user_id) VALUES (?, ?, ?)",
                ("rule-a", card_id, self.owner_a["id"]),
            )
            cursor.execute(
                "INSERT INTO user_settings (user_id, key, value) VALUES (?, ?, ?)",
                (self.owner_a["id"], "setting-a", "1"),
            )
            self.db.conn.commit()

        self.assertTrue(self.db.delete_user_and_data(self.owner_a["id"]))
        self.assertIsNone(self.db.get_user_by_id(self.owner_a["id"]))
        self.assertEqual(
            self.db.conn.execute(
                "SELECT COUNT(*) FROM cookies WHERE user_id = ?", (self.owner_a["id"],)
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            self.db.conn.execute(
                "SELECT COUNT(*) FROM delivery_rules WHERE user_id = ?",
                (self.owner_a["id"],),
            ).fetchone()[0],
            0,
        )
        self.assertIsNotNone(self.db.get_user_by_id(self.owner_b["id"]))

    def test_user_backup_round_trip_preserves_metric_snapshot_and_state(self):
        self.db.record_item_metric_snapshot(
            user_id=self.owner_a["id"],
            cookie_id="account-a",
            item_id="item-a",
            observed_at=time.time(),
            source="seller_backend_verified",
            view_count=10,
        )
        for _ in range(3):
            self.db.record_item_metric_canary_result(
                user_id=self.owner_a["id"],
                cookie_id="account-a",
                success=True,
            )
        backup = self.db.export_backup(self.owner_a["id"])
        self.assertIn("item_metric_snapshots", backup["data"])
        self.assertIn("item_metric_collection_states", backup["data"])

        self.assertTrue(self.db.import_backup(backup, self.owner_a["id"]))

        traffic = self.db.get_item_traffic_analytics(user_id=self.owner_a["id"])
        state = self.db.get_item_metric_collection_state(
            user_id=self.owner_a["id"], cookie_id="account-a"
        )
        self.assertEqual(traffic["snapshot_count"], 1)
        self.assertTrue(state["enabled"])
        self.assertEqual(state["canary_success_count"], 3)

    def test_user_import_rejects_uploaded_columns_and_rolls_back(self):
        backup = self.db.export_backup(self.owner_a["id"])
        cookie_table = backup["data"]["cookies"]
        cookie_table["columns"][0] = (
            "id, value, user_id) VALUES (?, 'synthetic', 1) "
            "ON CONFLICT(id) DO UPDATE SET user_id = 1 --"
        )

        self.assertFalse(self.db.import_backup(backup, self.owner_a["id"]))
        self.assertEqual(
            self.db.get_cookie_user_id("account-a"), self.owner_a["id"]
        )
        self.assertEqual(
            self.db.get_cookie_user_id("account-b"), self.owner_b["id"]
        )

    def test_user_import_rejects_foreign_cookie_relations(self):
        backup = self.db.export_backup(self.owner_a["id"])
        keyword_table = backup["data"]["keywords"]
        cookie_index = keyword_table["columns"].index("cookie_id")
        keyword_table["rows"][0][cookie_index] = "account-b"

        self.assertFalse(self.db.import_backup(backup, self.owner_a["id"]))
        victim_rows = self.db.conn.execute(
            "SELECT COUNT(*) FROM keywords WHERE cookie_id = ?", ("account-b",)
        ).fetchone()[0]
        self.assertEqual(victim_rows, 0)
        self.assertIsNotNone(self.db.get_cookie("account-a"))

    def test_user_import_rejects_global_tables(self):
        backup = self.db.export_backup(self.owner_a["id"])
        backup["data"]["system_settings"] = {
            "columns": ["key", "value", "description", "updated_at"],
            "rows": [["item_sync_enabled", "false", "synthetic", None]],
        }

        self.assertFalse(self.db.import_backup(backup, self.owner_a["id"]))

    def test_import_rejects_unknown_duplicate_and_missing_columns(self):
        baseline = self.db.export_backup(self.owner_a["id"])
        variants = []

        unknown = deepcopy(baseline)
        unknown["data"]["cookies"]["columns"].append("unknown_column")
        unknown["data"]["cookies"]["rows"][0].append("value")
        variants.append(unknown)

        duplicate = deepcopy(baseline)
        duplicate["data"]["cookies"]["columns"].append("id")
        duplicate["data"]["cookies"]["rows"][0].append("account-a")
        variants.append(duplicate)

        missing = deepcopy(baseline)
        missing["data"]["cookies"]["columns"].pop()
        missing["data"]["cookies"]["rows"][0].pop()
        variants.append(missing)

        for payload in variants:
            with self.subTest(columns=payload["data"]["cookies"]["columns"]):
                self.assertFalse(self.db.import_backup(payload, self.owner_a["id"]))
                self.assertIsNotNone(self.db.get_cookie("account-a"))

    def test_import_row_limit_rejects_before_mutation(self):
        backup = self.db.export_backup(self.owner_a["id"])
        with patch.object(db_manager_module, "BACKUP_MAX_TOTAL_ROWS", 0):
            self.assertFalse(self.db.import_backup(backup, self.owner_a["id"]))
        self.assertIsNotNone(self.db.get_cookie("account-a"))


if __name__ == "__main__":
    unittest.main()
