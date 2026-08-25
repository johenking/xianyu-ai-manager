import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from backfill_auto_rates import (
    ReadOnlyDatabase,
    _public_report,
    discover_historical_candidates,
)


class BackfillAutoRateTests(unittest.IsolatedAsyncioTestCase):
    async def test_scan_counts_rateable_tracked_and_new_without_order_ids_in_report(self):
        account = {
            "cookie_id": "account-secret",
            "cookie_string": "unb=100; _m_h5_tk=token_suffix",
            "user_id": 7,
            "xianyu_unb": "100",
            "browser_user_agent": "test-agent",
            "cookie_revision": 1,
            "enabled_at": 2_000,
        }

        class FakeClient:
            async def discover(self, **_kwargs):
                return {
                    "success": True,
                    "coverage": "full_recent",
                    "pages_scanned": 3,
                    "truncated": False,
                    "orders": [
                        {
                            "order_id": "new-order-secret",
                            "item_title": "测试商品",
                            "created_at": 1_000,
                            "can_rate": True,
                        },
                        {
                            "order_id": "tracked-order-secret",
                            "created_at": 1_001,
                            "can_rate": True,
                        },
                        {
                            "order_id": "not-rateable",
                            "created_at": 1_002,
                            "can_rate": False,
                        },
                    ],
                }

        scan = await discover_historical_candidates(
            [account],
            {(7, "account-secret", "tracked-order-secret")},
            client_factory=FakeClient,
        )
        report = _public_report(scan, mode="dry_run")

        self.assertEqual((scan["rateable"], scan["tracked"], scan["new_candidates"]), (2, 1, 1))
        self.assertTrue(report["apply_allowed"])
        rendered = json.dumps(report, ensure_ascii=False)
        self.assertNotIn("account-secret", rendered)
        self.assertNotIn("new-order-secret", rendered)

    async def test_incomplete_coverage_is_a_hard_apply_gate(self):
        account = {
            "cookie_id": "account-1",
            "cookie_string": "unb=100; _m_h5_tk=token_suffix",
            "user_id": 7,
            "xianyu_unb": "100",
            "browser_user_agent": "test-agent",
            "cookie_revision": 1,
            "enabled_at": 2_000,
        }

        class FakeClient:
            async def discover(self, **_kwargs):
                return {
                    "success": True,
                    "coverage": "pending_only",
                    "pages_scanned": 1,
                    "truncated": False,
                    "orders": [],
                }

        scan = await discover_historical_candidates(
            [account], set(), client_factory=FakeClient
        )

        self.assertTrue(scan["incomplete"])
        self.assertFalse(_public_report(scan, mode="dry_run")["apply_allowed"])

    def test_read_only_database_rejects_writes(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "database.db"
            connection = sqlite3.connect(path)
            connection.executescript(
                """
                CREATE TABLE cookies (
                    id TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    xianyu_unb TEXT,
                    browser_user_agent TEXT,
                    cookie_revision INTEGER,
                    auto_rate_enabled INTEGER NOT NULL,
                    auto_rate_enabled_at REAL
                );
                CREATE TABLE order_auto_ratings (
                    user_id INTEGER NOT NULL,
                    cookie_id TEXT NOT NULL,
                    order_id TEXT NOT NULL
                );
                INSERT INTO cookies VALUES
                    ('account-1', 'unb=100', 7, '100', 'test-agent', 1, 1, 2000);
                """
            )
            connection.commit()
            connection.close()

            with ReadOnlyDatabase(str(path)) as readonly:
                self.assertEqual(readonly.conn.execute("PRAGMA query_only").fetchone()[0], 1)
                with self.assertRaises(sqlite3.OperationalError):
                    readonly.conn.execute(
                        "INSERT INTO order_auto_ratings VALUES (7, 'account-1', 'x')"
                    )


if __name__ == "__main__":
    unittest.main()
