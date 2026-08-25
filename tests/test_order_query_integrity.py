"""订单查询完整性：错误语义、分页档案查询与可索引日期边界。"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from db_manager import DBManager
import reply_server


SHANGHAI = ZoneInfo("Asia/Shanghai")


class OrderQueryIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.previous_key_file = os.environ.get("SYSTEM_SECRET_KEY_FILE")
        os.environ["SYSTEM_SECRET_KEY_FILE"] = str(self.root / ".system-key")
        self.db = DBManager(str(self.root / "orders-query.db"))
        self.assertTrue(
            self.db.create_user(
                "query-owner", "query-owner@example.test", "Strong-pass-2026!"
            )
        )
        self.user = self.db.get_user_by_username("query-owner")
        with self.db.lock:
            self.db.conn.executemany(
                "INSERT INTO cookies (id, value, user_id) VALUES (?, ?, ?)",
                (
                    ("acct-a", "unb=1; cookie2=x", self.user["id"]),
                    ("acct-b", "unb=2; cookie2=x", self.user["id"]),
                ),
            )
            self.db.conn.commit()

    def tearDown(self):
        self.db.close()
        if self.previous_key_file is None:
            os.environ.pop("SYSTEM_SECRET_KEY_FILE", None)
        else:
            os.environ["SYSTEM_SECRET_KEY_FILE"] = self.previous_key_file
        self.tempdir.cleanup()

    def _insert_order(
        self,
        order_id: str,
        cookie_id: str = "acct-a",
        buyer_id: str = "buyer-current",
        created_at: str = "2026-07-20 00:00:00",
        ordered_at_utc: float | None = None,
    ) -> None:
        self.assertTrue(
            self.db.insert_or_update_order(
                order_id=order_id,
                item_id=f"item-{order_id}",
                buyer_id=buyer_id,
                amount="¥12.50",
                order_status="completed",
                cookie_id=cookie_id,
                created_at=created_at,
            )
        )
        if ordered_at_utc is not None:
            with self.db.lock:
                self.db.conn.execute(
                    "UPDATE orders SET ordered_at_utc = ?, ordered_at_source = ?"
                    " WHERE order_id = ?",
                    (ordered_at_utc, "order_detail", order_id),
                )
                self.db.conn.commit()

    def test_database_faults_are_not_reported_as_empty_or_not_found(self):
        self._insert_order("order-one")
        self.db.close()

        with self.assertRaises(RuntimeError):
            self.db.query_orders(["acct-a"])
        with self.assertRaises(RuntimeError):
            self.db.get_order_by_id("order-one")
        with self.assertRaises(RuntimeError):
            self.db.get_customer_profiles(["acct-a"])
        with self.assertRaises(RuntimeError):
            self.db.get_customer_profile("acct-a", "buyer-current")

    def test_missing_order_and_profile_remain_distinct_from_database_faults(self):
        self.assertIsNone(self.db.get_order_by_id("missing-order"))
        self.assertIsNone(
            self.db.get_customer_profile("acct-a", "missing-buyer")
        )

    def test_query_returns_only_paged_profiles_even_with_large_account_history(self):
        self._insert_order("order-current", buyer_id="buyer-current")
        with self.db.lock:
            self.db.conn.executemany(
                """
                INSERT INTO customer_profiles (
                    cookie_id, buyer_id, display_name, avatar_url, profile_source,
                    first_observed_at, last_observed_at, observation_count,
                    display_name_source, avatar_source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        "acct-a",
                        f"buyer-{index}",
                        f"历史买家{index}",
                        "",
                        "realtime_message",
                        1.0,
                        1.0,
                        1,
                        "realtime_message",
                        "",
                    )
                    for index in range(500)
                ]
                + [
                    (
                        "acct-a",
                        "buyer-current",
                        "当前页买家",
                        "https://img.alicdn.com/current.jpg",
                        "order_detail",
                        1.0,
                        2.0,
                        2,
                        "order_detail",
                        "realtime_message",
                    )
                ],
            )
            self.db.conn.commit()

        result = self.db.query_orders(["acct-a"], page=1, page_size=1)

        self.assertEqual(result["total"], 1)
        self.assertEqual(len(result["items"]), 1)
        row = result["items"][0]
        self.assertEqual(row["profile_display_name"], "当前页买家")
        self.assertEqual(
            row["profile_avatar_url"], "https://img.alicdn.com/current.jpg"
        )
        self.assertEqual(row["profile_display_name_source"], "order_detail")
        self.assertEqual(row["profile_avatar_source"], "realtime_message")

    def test_shanghai_date_bounds_are_half_open_and_keep_legacy_rows(self):
        day_start = datetime(2026, 7, 20, tzinfo=SHANGHAI).timestamp()
        next_day_start = datetime(2026, 7, 21, tzinfo=SHANGHAI).timestamp()
        self._insert_order("normalized-before", ordered_at_utc=day_start - 1)
        self._insert_order("normalized-start", ordered_at_utc=day_start)
        self._insert_order("normalized-last", ordered_at_utc=next_day_start - 1)
        self._insert_order("normalized-end", ordered_at_utc=next_day_start)
        self._insert_order(
            "legacy-inside",
            created_at=datetime.fromtimestamp(
                day_start + 3600, timezone.utc
            ).strftime("%Y-%m-%d %H:%M:%S"),
        )
        self._insert_order(
            "legacy-end",
            created_at=datetime.fromtimestamp(
                next_day_start, timezone.utc
            ).strftime("%Y-%m-%d %H:%M:%S"),
        )

        statements: list[str] = []
        self.db.conn.set_trace_callback(statements.append)
        result = self.db.query_orders(
            ["acct-a"], start_date="2026-07-20", end_date="2026-07-20"
        )
        self.db.conn.set_trace_callback(None)

        self.assertEqual(
            {row["order_id"] for row in result["items"]},
            {"normalized-start", "normalized-last", "legacy-inside"},
        )
        list_sql = next(
            statement
            for statement in reversed(statements)
            if " ORDER BY o.ordered_at_utc DESC" in statement
            and " LEFT JOIN item_info ci" in statement
            and " LEFT JOIN customer_profiles cp" in statement
        )
        with self.db.lock:
            plan = self.db.conn.execute(
                f"EXPLAIN QUERY PLAN {list_sql}"
            ).fetchall()
        plan_text = " ".join(str(row[3]) for row in plan)

        self.assertIn("idx_orders_cookie_ordered_order", plan_text)
        self.assertIn("ordered_at_utc>?", plan_text)
        self.assertIn("ordered_at_utc<?", plan_text)
        self.assertIn("UNION ALL", list_sql)
        self.assertNotIn("COALESCE(", list_sql)
        self.assertNotIn("date(", list_sql)
        self.assertIn("o.ordered_at_utc >=", list_sql)
        self.assertIn("o.ordered_at_utc <", list_sql)
        self.assertIn("o.ordered_at_utc IS NULL", list_sql)
        self.assertIn("o.created_at >=", list_sql)
        self.assertIn("o.created_at <", list_sql)

        start_only = self.db.query_orders(
            ["acct-a"], start_date="2026-07-20"
        )
        self.assertNotIn(
            "normalized-before",
            {row["order_id"] for row in start_only["items"]},
        )
        self.assertIn(
            "normalized-end",
            {row["order_id"] for row in start_only["items"]},
        )
        end_only = self.db.query_orders(["acct-a"], end_date="2026-07-20")
        end_only_ids = {row["order_id"] for row in end_only["items"]}
        self.assertIn("normalized-before", end_only_ids)
        self.assertNotIn("normalized-end", end_only_ids)
        self.assertNotIn("legacy-end", end_only_ids)

    def test_sort_and_covering_index_follow_global_time_order_contract(self):
        self._insert_order("z-a-old", "acct-a", ordered_at_utc=100.0)
        self._insert_order("a-a-new", "acct-a", ordered_at_utc=200.0)
        self._insert_order("z-b-new", "acct-b", ordered_at_utc=300.0)
        self._insert_order("a-b-old", "acct-b", ordered_at_utc=50.0)

        result = self.db.query_orders(["acct-b", "acct-a"], page_size=10)
        self.assertEqual(
            [row["order_id"] for row in result["items"]],
            ["z-b-new", "a-a-new", "z-a-old", "a-b-old"],
        )

        with self.db.lock:
            indexes = {
                row[1]
                for row in self.db.conn.execute("PRAGMA index_list(orders)").fetchall()
            }
        self.assertIn("idx_orders_cookie_ordered_order", indexes)

    def test_date_union_paginates_stably_across_accounts_and_time_ties(self):
        tied_epoch = datetime(2026, 7, 20, 12, tzinfo=SHANGHAI).timestamp()
        for cookie_id in ("acct-a", "acct-b"):
            for suffix in ("1", "2", "3"):
                self._insert_order(
                    f"order-{cookie_id[-1]}-{suffix}",
                    cookie_id,
                    ordered_at_utc=tied_epoch,
                )
            self._insert_order(
                f"legacy-{cookie_id[-1]}",
                cookie_id,
                created_at="2026-07-20 04:00:00",
            )

        pages = [
            self.db.query_orders(
                ["acct-b", "acct-a"],
                start_date="2026-07-20",
                end_date="2026-07-20",
                page=page,
                page_size=3,
            )
            for page in (1, 2, 3)
        ]

        self.assertEqual([page["total"] for page in pages], [8, 8, 8])
        self.assertEqual(
            [
                row["order_id"]
                for page in pages
                for row in page["items"]
            ],
            [
                "order-b-3",
                "order-b-2",
                "order-b-1",
                "order-a-3",
                "order-a-2",
                "order-a-1",
                "legacy-b",
                "legacy-a",
            ],
        )


class OrderQueryApiFailureTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.previous_key_file = os.environ.get("SYSTEM_SECRET_KEY_FILE")
        os.environ["SYSTEM_SECRET_KEY_FILE"] = str(self.root / ".system-key")
        self.db = DBManager(str(self.root / "orders-query-api.db"))
        self.assertTrue(
            self.db.create_user(
                "query-api-owner",
                "query-api-owner@example.test",
                "Strong-pass-2026!",
            )
        )
        self.user = self.db.get_user_by_username("query-api-owner")
        with self.db.lock:
            self.db.conn.execute(
                "INSERT INTO cookies (id, value, user_id) VALUES (?, ?, ?)",
                ("acct-a", "unb=1; cookie2=x", self.user["id"]),
            )
            self.db.conn.commit()
        self.assertTrue(
            self.db.insert_or_update_order(
                order_id="order-api",
                item_id="item-api",
                buyer_id="buyer-api",
                amount="¥12.50",
                order_status="completed",
                cookie_id="acct-a",
                created_at="2026-07-20 00:00:00",
            )
        )
        self.db.upsert_customer_observation(
            "acct-a",
            "buyer-api",
            "分页昵称",
            "https://img.alicdn.com/avatar.jpg",
            "order_detail",
            1000.0,
        )
        self.original_db = reply_server.db_manager
        reply_server.db_manager = self.db
        reply_server.SESSION_TOKENS.clear()
        self.client = TestClient(reply_server.app, raise_server_exceptions=False)

    def tearDown(self):
        self.client.close()
        reply_server.SESSION_TOKENS.clear()
        reply_server.db_manager = self.original_db
        self.db.close()
        if self.previous_key_file is None:
            os.environ.pop("SYSTEM_SECRET_KEY_FILE", None)
        else:
            os.environ["SYSTEM_SECRET_KEY_FILE"] = self.previous_key_file
        self.tempdir.cleanup()

    def _headers(self) -> dict[str, str]:
        token, _ = reply_server.create_login_session(self.user)
        return {"Authorization": f"Bearer {token}"}

    def test_list_database_error_is_structured_and_does_not_leak_sql(self):
        with patch.object(
            self.db,
            "query_orders",
            side_effect=RuntimeError("SELECT secret_value FROM private_table"),
        ):
            response = self.client.get("/api/orders", headers=self._headers())

        self.assertEqual(response.status_code, 500)
        self.assertEqual(
            response.json()["detail"],
            {
                "code": "order_query_failed",
                "message": "订单列表查询失败，请稍后重试",
            },
        )
        self.assertNotIn("secret_value", response.text)
        self.assertNotIn("private_table", response.text)

    def test_detail_database_error_is_structured_and_does_not_leak_sql(self):
        with patch.object(
            self.db,
            "get_order_by_id",
            side_effect=RuntimeError("SELECT secret_value FROM private_table"),
        ):
            response = self.client.get(
                "/api/orders/order-api", headers=self._headers()
            )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(
            response.json()["detail"],
            {
                "code": "order_detail_query_failed",
                "message": "订单详情查询失败，请稍后重试",
            },
        )
        self.assertNotIn("secret_value", response.text)
        self.assertNotIn("private_table", response.text)

    def test_list_composes_joined_profile_without_full_account_scan(self):
        with patch.object(
            self.db,
            "get_customer_profiles",
            side_effect=AssertionError("full profile scan"),
        ) as full_scan:
            response = self.client.get("/api/orders", headers=self._headers())

        self.assertEqual(response.status_code, 200)
        row = response.json()["data"][0]
        self.assertEqual(row["buyer_display_name"], "分页昵称")
        self.assertEqual(row["buyer_display_name_source"], "order_detail")
        self.assertEqual(
            row["buyer_avatar_url"], "https://img.alicdn.com/avatar.jpg"
        )
        self.assertNotIn("profile_display_name", row)
        full_scan.assert_not_called()

    def test_detail_fetches_exactly_one_account_buyer_profile(self):
        exact_profile = Mock(
            return_value={
                "display_name": "单买家昵称",
                "avatar_url": "https://img.alicdn.com/single.jpg",
                "profile_source": "order_detail",
                "display_name_source": "order_detail",
                "avatar_source": "realtime_message",
            }
        )
        self.db.get_customer_profile = exact_profile
        with patch.object(
            self.db,
            "get_customer_profiles",
            side_effect=AssertionError("full profile scan"),
        ) as full_scan:
            response = self.client.get(
                "/api/orders/order-api", headers=self._headers()
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["buyer_display_name"], "单买家昵称")
        exact_profile.assert_called_once_with("acct-a", "buyer-api")
        full_scan.assert_not_called()

    def test_list_rejects_invalid_calendar_date(self):
        response = self.client.get(
            "/api/orders",
            params={"start_date": "2026-02-30"},
            headers=self._headers(),
        )

        self.assertEqual(response.status_code, 422)

    def test_list_rejects_reversed_date_range(self):
        response = self.client.get(
            "/api/orders",
            params={
                "start_date": "2026-07-21",
                "end_date": "2026-07-20",
            },
            headers=self._headers(),
        )

        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
