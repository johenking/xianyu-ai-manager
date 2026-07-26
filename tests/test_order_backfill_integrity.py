"""历史订单回填的只读兼容、孤儿行与并发事务边界。"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import backfill_order_snapshots
from db_manager import DBManager


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OLD_SCHEMA_FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "orders_schema_2026072301.sql"


class BackfillIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.db_path = self.root / "backfill-integrity.db"
        self.key_paths = {
            "ACCOUNT_CREDENTIAL_KEY_FILE": str(self.root / "account.key"),
            "SYSTEM_SECRET_KEY_FILE": str(self.root / "system.key"),
            "AI_PROVIDER_KEY_FILE": str(self.root / "ai.key"),
        }

    def tearDown(self):
        self.tempdir.cleanup()

    def _run_script(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = dict(
            os.environ,
            DB_PATH=str(self.db_path),
            SQL_LOG_ENABLED="false",
            **self.key_paths,
        )
        return subprocess.run(
            [
                sys.executable,
                str(PROJECT_ROOT / "backfill_order_snapshots.py"),
                *args,
            ],
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )

    def _create_old_schema_database(self) -> None:
        connection = sqlite3.connect(self.db_path)
        try:
            connection.executescript(OLD_SCHEMA_FIXTURE.read_text(encoding="utf-8"))
            connection.execute(
                """
                INSERT INTO users (
                    username, email, password_hash, password_hash_v2,
                    username_normalized, email_normalized
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "legacy-owner",
                    "legacy-owner@example.test",
                    "legacy-hash",
                    "legacy-hash",
                    "legacy-owner",
                    "legacy-owner@example.test",
                ),
            )
            user_id = connection.execute(
                "SELECT id FROM users WHERE username = 'legacy-owner'"
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO cookies (id, value, user_id) VALUES (?, ?, ?)",
                ("legacy-acct", "unb=1; cookie2=x", user_id),
            )
            connection.execute(
                """
                INSERT INTO orders (
                    order_id, item_id, buyer_id, amount, order_status,
                    cookie_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "legacy-order",
                    "legacy-item",
                    "legacy-buyer",
                    "¥12.50",
                    "completed",
                    "legacy-acct",
                    "2026-07-20 08:00:00",
                ),
            )
            connection.execute(
                """
                INSERT INTO item_info (
                    cookie_id, item_id, item_title, item_image
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    "legacy-acct",
                    "legacy-item",
                    "旧库目录标题",
                    "https://img.alicdn.com/legacy.jpg",
                ),
            )
            connection.commit()
        finally:
            connection.close()

    def _create_current_database(self) -> DBManager:
        previous_key_file = os.environ.get("SYSTEM_SECRET_KEY_FILE")
        os.environ["SYSTEM_SECRET_KEY_FILE"] = self.key_paths[
            "SYSTEM_SECRET_KEY_FILE"
        ]
        try:
            database = DBManager(str(self.db_path))
        finally:
            if previous_key_file is None:
                os.environ.pop("SYSTEM_SECRET_KEY_FILE", None)
            else:
                os.environ["SYSTEM_SECRET_KEY_FILE"] = previous_key_file
        return database

    def test_dry_run_on_pre_snapshot_schema_is_physically_read_only(self):
        self._create_old_schema_database()
        before_bytes = self.db_path.read_bytes()
        before_hash = hashlib.sha256(before_bytes).hexdigest()
        before_stat = self.db_path.stat()
        connection = sqlite3.connect(self.db_path)
        try:
            before_columns = connection.execute(
                "PRAGMA table_info(orders)"
            ).fetchall()
            before_ledger = connection.execute(
                "SELECT version, name FROM schema_migrations ORDER BY version"
            ).fetchall()
        finally:
            connection.close()

        result = self._run_script()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("只读演练", result.stdout)
        self.assertIn("订单总数: 1", result.stdout)
        self.assertEqual(
            hashlib.sha256(self.db_path.read_bytes()).hexdigest(), before_hash
        )
        self.assertEqual(self.db_path.stat().st_mtime_ns, before_stat.st_mtime_ns)
        connection = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(
                connection.execute("PRAGMA table_info(orders)").fetchall(),
                before_columns,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT version, name FROM schema_migrations ORDER BY version"
                ).fetchall(),
                before_ledger,
            )
        finally:
            connection.close()
        self.assertTrue(
            all(not Path(path).exists() for path in self.key_paths.values())
        )

    def test_apply_backfills_null_cookie_order_locally_and_counts_skips(self):
        database = self._create_current_database()
        with database.lock:
            database.conn.execute(
                """
                INSERT INTO orders (
                    order_id, item_id, buyer_id, amount, order_status,
                    cookie_id, created_at
                ) VALUES (?, ?, ?, ?, ?, NULL, ?)
                """,
                (
                    "orphan-order",
                    "orphan-item",
                    "orphan-buyer",
                    "¥12.50",
                    "completed",
                    "2026-07-20 08:00:00",
                ),
            )
            database.conn.commit()
        database.close()

        result = self._run_script("--apply")

        self.assertEqual(result.returncode, 0, result.stderr)
        connection = sqlite3.connect(self.db_path)
        try:
            row = connection.execute(
                """
                SELECT paid_amount_fen, ordered_at_utc, ordered_at_source,
                       item_title, item_image
                FROM orders WHERE order_id = 'orphan-order'
                """
            ).fetchone()
            profile_count = connection.execute(
                "SELECT COUNT(*) FROM customer_profiles"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(row[0], 1250)
        self.assertEqual(row[1], 1784505600.0)
        self.assertEqual(row[2], "backfill_cst_assumed")
        self.assertEqual((row[3] or "", row[4] or ""), ("", ""))
        self.assertEqual(profile_count, 0)
        self.assertIn("无账号孤儿订单: 1", result.stdout)
        self.assertIn("目录回填跳过(无账号): 1", result.stdout)
        self.assertIn("客户档案播种跳过(无账号): 1", result.stdout)

    def test_apply_holds_immediate_write_lock_from_scan_through_write(self):
        database = self._create_current_database()
        with database.lock:
            owner_id = database.conn.execute(
                "SELECT id FROM users ORDER BY id LIMIT 1"
            ).fetchone()[0]
            database.conn.execute(
                "INSERT INTO cookies (id, value, user_id) VALUES (?, ?, ?)",
                ("acct-concurrent", "unb=3; cookie2=x", owner_id),
            )
            database.conn.commit()
        self.assertTrue(
            database.insert_or_update_order(
                order_id="concurrent-order",
                item_id="concurrent-item",
                buyer_id="concurrent-buyer",
                amount="¥12.50",
                order_status="completed",
                cookie_id="acct-concurrent",
                created_at="2026-07-20 08:00:00",
            )
        )
        self.assertTrue(
            hasattr(backfill_order_snapshots, "run_backfill"),
            "回填核心需可直接验证事务边界",
        )
        self.assertTrue(
            hasattr(backfill_order_snapshots, "_plan_backfill"),
            "扫描/计划阶段需保持在同一事务内",
        )

        entered_plan = threading.Event()
        release_plan = threading.Event()
        transaction_state: list[bool] = []
        worker_errors: list[BaseException] = []
        original_plan = backfill_order_snapshots._plan_backfill

        def blocking_plan(*args, **kwargs):
            transaction_state.append(bool(database.conn.in_transaction))
            entered_plan.set()
            if not release_plan.wait(timeout=5):
                raise TimeoutError("test did not release backfill planner")
            return original_plan(*args, **kwargs)

        def run_worker() -> None:
            try:
                backfill_order_snapshots.run_backfill(
                    database,
                    apply=True,
                    emit=lambda _message: None,
                )
            except BaseException as exc:  # pragma: no cover - asserted below
                worker_errors.append(exc)

        with patch.object(
            backfill_order_snapshots, "_plan_backfill", side_effect=blocking_plan
        ):
            worker = threading.Thread(target=run_worker, daemon=True)
            worker.start()
            self.assertTrue(entered_plan.wait(timeout=5))
            contender = sqlite3.connect(self.db_path, timeout=0)
            try:
                contender.execute("PRAGMA busy_timeout = 0")
                with self.assertRaisesRegex(sqlite3.OperationalError, "locked"):
                    contender.execute(
                        "UPDATE orders SET amount = ? WHERE order_id = ?",
                        ("¥99.00", "concurrent-order"),
                    )
            finally:
                contender.close()
                release_plan.set()
            worker.join(timeout=10)

        self.assertFalse(worker.is_alive())
        self.assertEqual(worker_errors, [])
        self.assertEqual(transaction_state, [True])
        database.close()


if __name__ == "__main__":
    unittest.main()
