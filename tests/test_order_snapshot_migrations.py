"""订单身份快照迁移（2026072601/2026072602）双路径测试。

“生产旧库”路径用 tests/fixtures/orders_schema_2026072301.sql 固件构造：
原生 sqlite3 建库 + 11 行迁移账本，绕开 DBManager 的即席 ALTER 轨道，
确保被测对象是版本化迁移本身，而不是开发库“被提前加列”的假象。
"""

import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

from db_manager import DBManager
from schema_migrations import MIGRATIONS, Migration, MigrationRunner

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "orders_schema_2026072301.sql"

SNAPSHOT_COLUMNS = {
    "ordered_at_utc",
    "ordered_at_source",
    "paid_amount_fen",
    "item_title",
    "item_image",
    "item_image_cache_key",
    "item_snapshot_source",
    "item_snapshot_at",
    "buyer_nickname",
    "buyer_avatar_url",
    "buyer_snapshot_source",
    "buyer_snapshot_at",
}


def create_production_2026072301_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(FIXTURE_PATH.read_text(encoding="utf-8"))
    connection.execute(
        "INSERT INTO users (username, email, password_hash) VALUES (?, ?, ?)",
        ("seller", "seller@example.com", "synthetic-hash"),
    )
    connection.executemany(
        "INSERT INTO cookies (id, value, user_id) VALUES (?, ?, ?)",
        (("acct-a", "unb=1", 1), ("acct-b", "unb=2", 1)),
    )
    connection.executemany(
        "INSERT INTO orders (order_id, item_id, buyer_id, quantity, amount, order_status, cookie_id)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            ("order-1", "item-1", "buyer-1", "1", "¥12.50", "pending_ship", "acct-a"),
            ("order-2", "item-2", "buyer-2", "1", "", "unknown", "acct-a"),
            ("order-3", "item-1", "buyer-1", "2", "￥1,234.56", "completed", "acct-b"),
        ),
    )
    connection.execute(
        "INSERT INTO item_info (cookie_id, item_id, item_title, item_image) VALUES (?, ?, ?, ?)",
        ("acct-a", "item-1", "测试商品", "https://img.example.test/item-1.jpg"),
    )
    connection.commit()
    connection.close()


def order_columns(connection: sqlite3.Connection) -> set:
    return {row[1] for row in connection.execute("PRAGMA table_info(orders)").fetchall()}


class IsolatedKeysTestCase(unittest.TestCase):
    """所有用例隔离三把密钥的环境变量，避免污染真实密钥文件。"""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self._env_backup = {}
        for name in (
            "ACCOUNT_CREDENTIAL_KEY_FILE",
            "SYSTEM_SECRET_KEY_FILE",
            "AI_PROVIDER_KEY_FILE",
        ):
            self._env_backup[name] = os.environ.get(name)
            os.environ[name] = str(self.root / f".{name.lower()}")
        self._system_secret_backup = os.environ.pop("SYSTEM_SECRET_ENCRYPTION_KEY", None)

    def tearDown(self):
        for name, value in self._env_backup.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        if self._system_secret_backup is not None:
            os.environ["SYSTEM_SECRET_ENCRYPTION_KEY"] = self._system_secret_backup
        self.tempdir.cleanup()


class ProductionLedgerMigrationTests(IsolatedKeysTestCase):
    def setUp(self):
        super().setUp()
        self.db_path = self.root / "prod-like.db"
        create_production_2026072301_database(self.db_path)

    def test_applies_only_snapshot_migrations_and_is_idempotent(self):
        connection = sqlite3.connect(self.db_path)
        runner = MigrationRunner(connection, str(self.db_path))
        self.assertEqual(runner.run(), ["2026072601", "2026072602"])

        columns = order_columns(connection)
        self.assertTrue(SNAPSHOT_COLUMNS.issubset(columns))
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        self.assertIn("customer_profiles", tables)
        indexes = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
        self.assertIn("idx_orders_cookie_buyer", indexes)
        self.assertIn("idx_orders_cookie_ordered_at", indexes)
        self.assertIn("idx_customer_profiles_last_observed", indexes)
        self.assertEqual(
            connection.execute("PRAGMA quick_check").fetchone()[0], "ok"
        )

        # 幂等：二次运行零 pending
        self.assertEqual(runner.run(), [])
        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0],
            13,
        )
        connection.close()

    def test_backup_contains_database_and_keys(self):
        connection = sqlite3.connect(self.db_path)
        runner = MigrationRunner(connection, str(self.db_path))
        runner.run()
        self.assertIsNotNone(runner.last_backup_dir)
        self.assertTrue((runner.last_backup_dir / self.db_path.name).exists())
        backup_names = {entry.name for entry in runner.last_backup_dir.iterdir()}
        # runner 确保存在的两把密钥与库同备份（AI provider 密钥按需生成，存在才复制）
        for name in (".account_credential_key_file", ".system_secret_key_file"):
            self.assertIn(name, backup_names)
        connection.close()

    def test_data_is_preserved_and_migration_writes_no_values(self):
        connection = sqlite3.connect(self.db_path)
        before = connection.execute(
            "SELECT order_id, amount, order_status FROM orders ORDER BY order_id"
        ).fetchall()
        MigrationRunner(connection, str(self.db_path)).run()
        after = connection.execute(
            "SELECT order_id, amount, order_status FROM orders ORDER BY order_id"
        ).fetchall()
        self.assertEqual(before, after)
        # 迁移零数据写入：解析类字段一律留默认值，回填走独立脚本
        self.assertEqual(
            connection.execute(
                "SELECT COUNT(*) FROM orders WHERE ordered_at_utc IS NOT NULL"
                " OR paid_amount_fen IS NOT NULL OR item_title <> ''"
                " OR item_snapshot_source <> ''"
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM customer_profiles").fetchone()[0],
            0,
        )
        connection.close()

    def test_existing_item_image_from_legacy_inline_track_is_preserved(self):
        # 模拟开发库形态：item_image 曾被旧的即席 ALTER 轨道提前加列并写值
        connection = sqlite3.connect(self.db_path)
        connection.execute("ALTER TABLE orders ADD COLUMN item_image TEXT DEFAULT ''")
        connection.execute(
            "UPDATE orders SET item_image = 'https://img.example.test/snap.jpg'"
            " WHERE order_id = 'order-1'"
        )
        connection.commit()
        MigrationRunner(connection, str(self.db_path)).run()
        self.assertEqual(
            connection.execute(
                "SELECT item_image FROM orders WHERE order_id = 'order-1'"
            ).fetchone()[0],
            "https://img.example.test/snap.jpg",
        )
        connection.close()

    def test_failed_migration_rolls_back_everything(self):
        connection = sqlite3.connect(self.db_path)

        def fail(cursor, _db_path):
            raise RuntimeError("planned failure")

        runner = MigrationRunner(
            connection,
            str(self.db_path),
            migrations=[*MIGRATIONS, Migration("2099010101", "broken", fail)],
        )
        with self.assertRaisesRegex(RuntimeError, "planned failure"):
            runner.run()
        # 全部 pending 共用一个事务：失败后账本仍是 11 行、无半套新列
        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0],
            11,
        )
        self.assertFalse(SNAPSHOT_COLUMNS & order_columns(connection))
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        self.assertNotIn("customer_profiles", tables)
        connection.close()

    def test_old_code_insert_without_snapshot_columns_still_works(self):
        # 回滚预案 A 档依据：旧代码（不写任何快照列）可继续写新库
        connection = sqlite3.connect(self.db_path)
        MigrationRunner(connection, str(self.db_path)).run()
        connection.execute(
            "INSERT INTO orders (order_id, item_id, buyer_id, quantity, amount,"
            " order_status, cookie_id) VALUES ('order-old-code', 'item-9', 'buyer-9',"
            " '1', '9.90', 'pending_ship', 'acct-a')"
        )
        connection.commit()
        row = connection.execute(
            "SELECT item_title, item_image, buyer_nickname, ordered_at_utc, paid_amount_fen"
            " FROM orders WHERE order_id = 'order-old-code'"
        ).fetchone()
        self.assertEqual(row, ("", "", "", None, None))
        connection.close()

    def test_dbmanager_init_converges_with_pure_migration_path(self):
        # 双轨收敛：DBManager 完整初始化（即席 ALTER + 迁移）与纯迁移路径 schema 一致
        pure_path = self.root / "pure.db"
        create_production_2026072301_database(pure_path)
        pure_connection = sqlite3.connect(pure_path)
        MigrationRunner(pure_connection, str(pure_path)).run()
        pure_columns = order_columns(pure_connection)
        pure_connection.close()

        manager = DBManager(str(self.db_path))
        try:
            managed_columns = {
                row[1]
                for row in manager.conn.execute("PRAGMA table_info(orders)").fetchall()
            }
            self.assertEqual(pure_columns, managed_columns)
            versions = {
                row[0]
                for row in manager.conn.execute(
                    "SELECT version FROM schema_migrations"
                ).fetchall()
            }
            self.assertIn("2026072601", versions)
            self.assertIn("2026072602", versions)
        finally:
            manager.close()


class FreshDatabaseTests(IsolatedKeysTestCase):
    def test_empty_database_first_build_contains_snapshot_schema(self):
        manager = DBManager(str(self.root / "fresh.db"))
        try:
            columns = {
                row[1]
                for row in manager.conn.execute("PRAGMA table_info(orders)").fetchall()
            }
            self.assertTrue(SNAPSHOT_COLUMNS.issubset(columns))
            tables = {
                row[0]
                for row in manager.conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            self.assertIn("customer_profiles", tables)
            versions = {
                row[0]
                for row in manager.conn.execute(
                    "SELECT version FROM schema_migrations"
                ).fetchall()
            }
            self.assertEqual(
                versions, {migration.version for migration in MIGRATIONS}
            )
        finally:
            manager.close()


if __name__ == "__main__":
    unittest.main()
