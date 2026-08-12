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
            observed_at=time.time(),
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
        observed_at = time.time()
        for index in range(3):
            self.db.record_item_metric_canary_result(
                user_id=self.owner_a["id"],
                cookie_id="account-a",
                success=True,
                observed_at=observed_at + index,
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

    def test_user_import_clears_traversal_image_references(self):
        with self.db.lock:
            self.db.conn.execute(
                "UPDATE keywords SET type = 'image', image_url = ? "
                "WHERE cookie_id = ?",
                ("static/uploads/images/../../../private.txt", "account-a"),
            )
            self.db.conn.execute(
                "INSERT INTO default_replies "
                "(cookie_id, enabled, reply_image_url) VALUES (?, ?, ?)",
                (
                    "account-a",
                    1,
                    "static/uploads/images/../../../../etc/passwd",
                ),
            )
            self.db.conn.commit()

        backup = self.db.export_backup(self.owner_a["id"])
        self.assertTrue(self.db.import_backup(backup, self.owner_a["id"]))

        keyword_path = self.db.conn.execute(
            "SELECT image_url FROM keywords WHERE cookie_id = ?",
            ("account-a",),
        ).fetchone()[0]
        default_path = self.db.conn.execute(
            "SELECT reply_image_url FROM default_replies WHERE cookie_id = ?",
            ("account-a",),
        ).fetchone()[0]
        self.assertEqual(keyword_path, "")
        self.assertEqual(default_path, "")

    def test_system_backup_card_image_traversal_is_cleaned(self):
        prepared = {
            "cards": {
                "columns": ["id", "image_url"],
                "rows": [
                    [1, "static/uploads/images/../../../private.txt"],
                    [2, "https://gw.alicdn.com/synthetic.jpg"],
                ],
            }
        }

        self.db._sanitize_imported_image_references(prepared)

        self.assertEqual(prepared["cards"]["rows"][0][1], "")
        self.assertEqual(
            prepared["cards"]["rows"][1][1],
            "https://gw.alicdn.com/synthetic.jpg",
        )

    def test_zero_row_delete_order_does_not_leak_transaction(self):
        with self.db.lock:
            self.db.conn.execute(
                "INSERT INTO orders (order_id, order_status, cookie_id) VALUES (?, ?, ?)",
                ("order-a", "completed", "account-a"),
            )
            self.db.conn.commit()

        # 跨租户删除命中 0 行，是归属下推后的常规失败路径
        self.assertFalse(self.db.delete_order("order-a", cookie_ids=["account-b"]))
        # 关键断言：0 行 DML 后共享连接不能停留在打开的隐式事务里
        self.assertFalse(
            self.db.conn.in_transaction,
            "delete_order 0 行后连接仍停留在打开的事务里",
        )
        # 目标订单未被误删
        self.assertIsNotNone(
            self.db.get_order_by_id("order-a", cookie_ids=["account-a"])
        )
        # 端到端：随后走显式 BEGIN IMMEDIATE 的写入不被悬挂事务击穿
        self.assertTrue(self.db.delete_cookie("account-b"))

    def test_zero_row_dml_methods_leave_no_open_transaction(self):
        # 这些 0 行分支历史上会把共享连接停在打开的隐式事务里，
        # 击穿后续其它方法的显式 BEGIN IMMEDIATE。逐一断言事务已收尾。
        cases = (
            (
                "update_item_multi_spec_status",
                lambda: self.db.update_item_multi_spec_status(
                    "account-a", "ghost-item", True
                ),
            ),
            (
                "update_item_multi_quantity_delivery_status",
                lambda: self.db.update_item_multi_quantity_delivery_status(
                    "account-a", "ghost-item", True
                ),
            ),
            (
                "update_item_invite_auto_fulfillment_status",
                lambda: self.db.update_item_invite_auto_fulfillment_status(
                    "account-a", "ghost-item", True
                ),
            ),
            (
                "update_item_detail",
                lambda: self.db.update_item_detail("account-a", "ghost-item", "{}"),
            ),
            (
                "delete_item_info",
                lambda: self.db.delete_item_info("account-a", "ghost-item"),
            ),
            (
                "delete_order",
                lambda: self.db.delete_order("ghost-order", cookie_ids=["account-a"]),
            ),
            (
                "delete_table_record",
                lambda: self.db.delete_table_record("orders", "ghost-order"),
            ),
        )
        for name, action in cases:
            with self.subTest(method=name):
                self.assertFalse(action())
                self.assertFalse(
                    self.db.conn.in_transaction,
                    f"{name} 0 行后连接仍停留在打开的事务里",
                )

    def test_admin_table_export_redacts_sensitive_columns(self):
        # cookies：平台会话明文与账号密码不得经 /admin/data 导出
        data, columns = self.db.get_table_data("cookies")
        for hidden in ("value", "xianyu_unb", "password", "password_encrypted"):
            self.assertNotIn(hidden, columns)
        self.assertTrue(data)
        for row in data:
            self.assertNotIn("value", row)
            self.assertNotIn("password", row)
        # 非敏感列保留，接口仍可用
        self.assertIn("id", columns)
        self.assertIn("user_id", columns)

        # ai_reply_settings：AI Key 明文不得导出
        with self.db.lock:
            self.db.conn.execute(
                "INSERT INTO ai_reply_settings (cookie_id, api_key) VALUES (?, ?)",
                ("account-a", "synthetic-key-should-not-leak"),
            )
            self.db.conn.commit()
        ai_data, ai_columns = self.db.get_table_data("ai_reply_settings")
        self.assertNotIn("api_key", ai_columns)
        for row in ai_data:
            self.assertNotIn("api_key", row)

        # orders：买家 PII 不得导出
        with self.db.lock:
            self.db.conn.execute(
                "INSERT INTO orders "
                "(order_id, cookie_id, receiver_name, receiver_phone, receiver_address) "
                "VALUES (?, ?, ?, ?, ?)",
                ("order-pii", "account-a", "synthetic-name", "13800000000", "synthetic-addr"),
            )
            self.db.conn.commit()
        order_data, order_columns = self.db.get_table_data("orders")
        for hidden in ("receiver_name", "receiver_phone", "receiver_address"):
            self.assertNotIn(hidden, order_columns)
        for row in order_data:
            self.assertNotIn("receiver_phone", row)

        # users：口令哈希不得导出
        _, user_columns = self.db.get_table_data("users")
        self.assertNotIn("password_hash", user_columns)


if __name__ == "__main__":
    unittest.main()
