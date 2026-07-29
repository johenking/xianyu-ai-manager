"""商品指标快照、观测窗口、重置处理和租户隔离。"""

import asyncio
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import inspect
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from db_manager import DBManager
from item_metric_scheduler import ItemMetricScheduler
from item_metric_service import collect_item_metrics_once, register_item_metric_collector
import item_metric_scheduler as item_metric_scheduler_module
import item_metric_service as item_metric_service_module
import reply_server


class ItemMetricSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.previous_key_file = os.environ.get("SYSTEM_SECRET_KEY_FILE")
        os.environ["SYSTEM_SECRET_KEY_FILE"] = str(self.root / ".system-key")
        self.db = DBManager(str(self.root / "metrics.db"))
        self.assertTrue(self.db.create_user("owner-one", "one@example.test", "Strong-pass-2026!"))
        self.assertTrue(self.db.create_user("owner-two", "two@example.test", "Strong-pass-2026!"))
        self.owner_one = self.db.get_user_by_username("owner-one")
        self.owner_two = self.db.get_user_by_username("owner-two")
        with self.db.lock:
            self.db.conn.executemany(
                "INSERT INTO cookies (id, value, user_id) VALUES (?, ?, ?)",
                (
                    ("account-one", "unb=one; cookie2=x", self.owner_one["id"]),
                    ("account-two", "unb=two; cookie2=x", self.owner_two["id"]),
                ),
            )
            self.db.conn.commit()

    def tearDown(self):
        register_item_metric_collector(None)
        self.db.close()
        if self.previous_key_file is None:
            os.environ.pop("SYSTEM_SECRET_KEY_FILE", None)
        else:
            os.environ["SYSTEM_SECRET_KEY_FILE"] = self.previous_key_file
        self.tempdir.cleanup()

    def test_observation_hour_bucket_is_idempotent_and_tenant_scoped(self):
        first = self.db.record_item_metric_snapshot(
            user_id=self.owner_one["id"],
            cookie_id="account-one",
            item_id="item-1",
            observed_at=1785114000.0,
            source="seller_backend_verified",
            exposure_count=100,
            view_count=40,
            want_count=8,
        )
        duplicate = self.db.record_item_metric_snapshot(
            user_id=self.owner_one["id"],
            cookie_id="account-one",
            item_id="item-1",
            observed_at=1785115200.0,
            source="seller_backend_verified",
            exposure_count=100,
            view_count=40,
            want_count=8,
        )

        self.assertTrue(first["inserted"])
        self.assertFalse(duplicate["inserted"])
        own = self.db.get_item_traffic_analytics(user_id=self.owner_one["id"])
        other = self.db.get_item_traffic_analytics(user_id=self.owner_two["id"])
        self.assertEqual(own["snapshot_count"], 1)
        self.assertEqual(other["snapshot_count"], 0)

        with self.assertRaises(PermissionError):
            self.db.record_item_metric_snapshot(
                user_id=self.owner_two["id"],
                cookie_id="account-one",
                item_id="item-1",
                observed_at=1785114000.0,
                source="seller_backend_verified",
                exposure_count=100,
            )

    def test_same_hour_conflicting_snapshot_is_rejected(self):
        self.db.record_item_metric_snapshot(
            user_id=self.owner_one["id"],
            cookie_id="account-one",
            item_id="item-1",
            observed_at=1785114000.0,
            source="seller_backend_verified",
            view_count=40,
        )

        with self.assertRaisesRegex(ValueError, "同一观测时间桶"):
            self.db.record_item_metric_snapshot(
                user_id=self.owner_one["id"],
                cookie_id="account-one",
                item_id="item-1",
                observed_at=1785115200.0,
                source="seller_backend_verified",
                view_count=41,
            )

    def test_traffic_delta_is_reported_as_a_four_hour_observation_window(self):
        start = datetime(2026, 7, 1, 8, 10, tzinfo=timezone.utc).timestamp()
        end = start + 4 * 60 * 60 + 10 * 60
        self.db.record_item_metric_snapshot(
            user_id=self.owner_one["id"],
            cookie_id="account-one",
            item_id="item-window",
            observed_at=start,
            source="seller_backend_verified",
            exposure_count=100,
            view_count=40,
            want_count=8,
        )
        self.db.record_item_metric_snapshot(
            user_id=self.owner_one["id"],
            cookie_id="account-one",
            item_id="item-window",
            observed_at=end,
            source="seller_backend_verified",
            exposure_count=160,
            view_count=64,
            want_count=11,
        )

        traffic = self.db.get_item_traffic_analytics(user_id=self.owner_one["id"])

        self.assertEqual(traffic["time_precision"], "observation_window")
        self.assertEqual(
            traffic["aggregation_semantics"],
            "counter_delta_between_consecutive_snapshots",
        )
        self.assertEqual(traffic["valid_observation_window_count"], 1)
        self.assertEqual(traffic["recommendation_window_count"], 1)
        self.assertEqual(traffic["irregular_window_count"], 0)
        self.assertEqual(len(traffic["observation_windows"]), 1)
        window = traffic["observation_windows"][0]
        self.assertEqual(window["start_hour"], 16)
        self.assertEqual(window["end_hour"], 20)
        self.assertEqual(window["day_span"], 0)
        self.assertEqual(window["window_count"], 1)
        self.assertAlmostEqual(window["average_duration_hours"], 4.17, places=2)
        self.assertEqual(window["view_delta"], 24)
        self.assertEqual(
            traffic["hourly_semantics"],
            "legacy_observation_window_end_hour",
        )
        self.assertEqual(traffic["hourly"][0]["window_start_hour"], 16)
        self.assertEqual(traffic["hourly"][0]["hour"], 20)

    def test_date_filter_excludes_a_window_that_started_before_the_range(self):
        start = datetime(2026, 7, 1, 15, 0, tzinfo=timezone.utc).timestamp()
        self.db.record_item_metric_snapshot(
            user_id=self.owner_one["id"],
            cookie_id="account-one",
            item_id="item-boundary",
            observed_at=start,
            source="seller_backend_verified",
            view_count=10,
        )
        self.db.record_item_metric_snapshot(
            user_id=self.owner_one["id"],
            cookie_id="account-one",
            item_id="item-boundary",
            observed_at=start + 4 * 60 * 60,
            source="seller_backend_verified",
            view_count=20,
        )

        traffic = self.db.get_item_traffic_analytics(
            user_id=self.owner_one["id"],
            start_date="2026-07-02",
            end_date="2026-07-02",
        )

        self.assertEqual(traffic["snapshot_count"], 1)
        self.assertEqual(traffic["valid_observation_window_count"], 0)
        self.assertEqual(traffic["totals"]["view_delta"], 0)
        self.assertEqual(traffic["observation_windows"], [])

    def test_recommendation_preserves_four_hour_sampling_precision(self):
        base = datetime(2026, 7, 1, 8, 0, tzinfo=timezone.utc)
        for day in range(14):
            for variant in range(2):
                item_id = f"item-{day}-{variant}"
                observed_at = (base + timedelta(days=day)).timestamp()
                self.db.record_item_metric_snapshot(
                    user_id=self.owner_one["id"],
                    cookie_id="account-one",
                    item_id=item_id,
                    observed_at=observed_at,
                    source="seller_backend_verified",
                    exposure_count=100,
                    view_count=40,
                )
                self.db.record_item_metric_snapshot(
                    user_id=self.owner_one["id"],
                    cookie_id="account-one",
                    item_id=item_id,
                    observed_at=observed_at + 4 * 60 * 60,
                    source="seller_backend_verified",
                    exposure_count=150 + variant,
                    view_count=60 + variant,
                )

        traffic = self.db.get_item_traffic_analytics(user_id=self.owner_one["id"])

        self.assertTrue(traffic["sufficient_data"])
        self.assertEqual(traffic["valid_observation_window_count"], 28)
        self.assertEqual(traffic["recommendation_window_count"], 28)
        self.assertEqual(traffic["recommendation_distinct_days"], 14)
        recommendation = traffic["recommendation"]
        self.assertEqual(recommendation["type"], "timing")
        self.assertEqual(recommendation["semantics"], "observation_window")
        self.assertEqual(recommendation["hour"], 16)
        self.assertEqual(recommendation["start_hour"], 16)
        self.assertEqual(recommendation["end_hour"], 20)
        self.assertEqual(
            recommendation["precision"],
            "approximate_observation_window",
        )
        self.assertIn("不能细分到某一小时", recommendation["message"])
        self.assertNotIn("20:00-21:00", recommendation["message"])

    def test_future_snapshot_is_rejected_before_it_can_poison_ordering(self):
        with self.assertRaisesRegex(ValueError, "未来"):
            self.db.record_item_metric_snapshot(
                user_id=self.owner_one["id"],
                cookie_id="account-one",
                item_id="item-future",
                observed_at=time.time() + 3600,
                source="seller_backend_verified",
                view_count=1,
            )

    def test_counter_reset_does_not_create_negative_traffic(self):
        self.db.record_item_metric_snapshot(
            user_id=self.owner_one["id"], cookie_id="account-one", item_id="item-1",
            observed_at=1785114000.0, source="seller_backend_verified",
            exposure_count=100, view_count=40, want_count=8,
        )
        second = self.db.record_item_metric_snapshot(
            user_id=self.owner_one["id"], cookie_id="account-one", item_id="item-1",
            observed_at=1785117600.0, source="seller_backend_verified",
            exposure_count=10, view_count=5, want_count=1,
        )

        self.assertTrue(second["counter_reset"])
        traffic = self.db.get_item_traffic_analytics(user_id=self.owner_one["id"])
        self.assertEqual(traffic["totals"]["exposure_delta"], 0)
        self.assertEqual(traffic["totals"]["view_delta"], 0)
        self.assertEqual(traffic["totals"]["want_delta"], 0)
        self.assertFalse(traffic["sufficient_data"])
        self.assertIsNone(traffic["recommendation"])

    def test_scheduler_collects_owned_accounts_serially(self):
        calls = []

        async def collector(*, cookie_id, cookie_string):
            calls.append((cookie_id, cookie_string))
            return [{
                "item_id": f"item-{cookie_id}",
                "observed_at": 1785114000.0,
                "source": "seller_backend_verified",
                "view_count": 10,
            }]

        register_item_metric_collector(collector)
        for cookie_id, user_id in (
            ("account-one", self.owner_one["id"]),
            ("account-two", self.owner_two["id"]),
        ):
            for index in range(3):
                self.db.record_item_metric_canary_result(
                    user_id=user_id,
                    cookie_id=cookie_id,
                    success=True,
                    observed_at=1785106800.0 + index * 3600,
                )
        scheduler = ItemMetricScheduler()
        with patch.object(item_metric_scheduler_module, "db_manager", self.db), patch.object(
            item_metric_scheduler_module.asyncio,
            "sleep",
            new=AsyncMock(),
        ):
            asyncio.run(scheduler._collect_all_accounts())

        self.assertEqual(
            [cookie_id for cookie_id, _ in calls],
            ["account-one", "account-two"],
        )
        self.assertEqual(
            self.db.get_item_traffic_analytics(user_id=self.owner_one["id"])[
                "snapshot_count"
            ],
            1,
        )
        self.assertEqual(
            self.db.get_item_traffic_analytics(user_id=self.owner_two["id"])[
                "snapshot_count"
            ],
            1,
        )

    def test_scheduler_uses_fixed_four_hour_interval_without_global_enable_settings(self):
        source = inspect.getsource(ItemMetricScheduler._run)

        self.assertNotIn("get_system_setting", source)
        self.assertIn("ITEM_METRIC_SCHEDULE_SECONDS", source)
        self.assertEqual(
            item_metric_scheduler_module.ITEM_METRIC_SCHEDULE_SECONDS,
            4 * 60 * 60,
        )

    def test_scheduler_rechecks_canary_count_even_when_enabled_flag_is_true(self):
        collector = AsyncMock(return_value=[])
        register_item_metric_collector(collector)
        scheduler = ItemMetricScheduler()
        with patch.object(
            item_metric_scheduler_module,
            "db_manager",
            self.db,
        ), patch.object(
            self.db,
            "get_item_metric_collection_state",
            return_value={"enabled": True, "canary_success_count": 2},
        ):
            asyncio.run(scheduler._collect_all_accounts())

        collector.assert_not_awaited()

    def test_canary_state_is_account_scoped(self):
        for index in range(3):
            state = self.db.record_item_metric_canary_result(
                user_id=self.owner_one["id"],
                cookie_id="account-one",
                success=True,
                observed_at=1785106800.0 + index * 3600,
            )

        other = self.db.get_item_metric_collection_state(
            user_id=self.owner_two["id"],
            cookie_id="account-two",
        )
        self.assertTrue(state["enabled"])
        self.assertEqual(state["canary_success_count"], 3)
        self.assertFalse(other["enabled"])
        self.assertEqual(other["canary_success_count"], 0)

    def test_database_rejects_enabled_state_before_three_canaries(self):
        with self.assertRaisesRegex(sqlite3.IntegrityError, "CHECK constraint"):
            with self.db.lock:
                self.db.conn.execute(
                    "INSERT INTO item_metric_collection_states "
                    "(user_id, cookie_id, canary_success_count, enabled) "
                    "VALUES (?, ?, 2, 1)",
                    (self.owner_one["id"], "account-one"),
                )
        self.db.conn.rollback()

    def test_metric_batch_rolls_back_when_any_row_is_invalid(self):
        async def collector(**_kwargs):
            return [
                {
                    "item_id": "item-valid",
                    "observed_at": 1785114000.0,
                    "source": "seller_backend_verified",
                    "view_count": 10,
                },
                {
                    "item_id": "item-invalid",
                    "observed_at": 1785114000.0,
                    "source": "unverified_source",
                    "view_count": 12,
                },
            ]

        register_item_metric_collector(collector)
        result = asyncio.run(collect_item_metrics_once(
            self.db,
            user_id=self.owner_one["id"],
            cookie_id="account-one",
            cookie_string="unb=one; cookie2=x",
            canary=True,
        ))

        self.assertFalse(result["success"])
        self.assertEqual(result["inserted"], 0)
        self.assertEqual(
            self.db.get_item_traffic_analytics(user_id=self.owner_one["id"])[
                "snapshot_count"
            ],
            0,
        )

    def test_out_of_order_snapshot_is_rejected(self):
        self.db.record_item_metric_snapshot(
            user_id=self.owner_one["id"],
            cookie_id="account-one",
            item_id="item-1",
            observed_at=1785117600.0,
            source="seller_backend_verified",
            view_count=10,
        )
        with self.assertRaisesRegex(ValueError, "早于"):
            self.db.record_item_metric_snapshot(
                user_id=self.owner_one["id"],
                cookie_id="account-one",
                item_id="item-1",
                observed_at=1785114000.0,
                source="seller_backend_verified",
                view_count=8,
            )

    def test_adapter_timeout_resets_canary_without_writing_rows(self):
        async def collector(**_kwargs):
            await asyncio.sleep(0.05)
            return []

        register_item_metric_collector(collector)
        with patch.object(
            item_metric_service_module,
            "ITEM_METRIC_ADAPTER_TIMEOUT_SECONDS",
            0.001,
        ):
            result = asyncio.run(collect_item_metrics_once(
                self.db,
                user_id=self.owner_one["id"],
                cookie_id="account-one",
                cookie_string="unb=one; cookie2=x",
                canary=True,
            ))

        self.assertFalse(result["success"])
        self.assertEqual(result["error_code"], "metric_adapter_timeout")
        state = self.db.get_item_metric_collection_state(
            user_id=self.owner_one["id"],
            cookie_id="account-one",
        )
        self.assertEqual(state["canary_success_count"], 0)
        self.assertFalse(state["enabled"])

    def test_sync_adapter_is_rejected_without_starting_background_work(self):
        called = False

        def collector(**_kwargs):
            nonlocal called
            called = True
            time.sleep(0.1)
            return [{
                "item_id": "item-sync-timeout",
                "observed_at": time.time(),
                "source": "seller_backend_verified",
                "view_count": 1,
            }]

        async def run_once():
            started = time.monotonic()
            result = await collect_item_metrics_once(
                self.db,
                user_id=self.owner_one["id"],
                cookie_id="account-one",
                cookie_string="unb=one; cookie2=x",
                canary=True,
            )
            return result, time.monotonic() - started

        register_item_metric_collector(collector)
        with patch.object(
            item_metric_service_module,
            "ITEM_METRIC_ADAPTER_TIMEOUT_SECONDS",
            0.01,
        ):
            result, elapsed = asyncio.run(run_once())

        self.assertFalse(result["success"])
        self.assertEqual(result["error_code"], "metric_adapter_must_be_async")
        self.assertLess(elapsed, 0.08)
        self.assertFalse(called)

    def test_same_account_collections_share_one_async_lock(self):
        active = 0
        max_active = 0

        async def collector(**_kwargs):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.02)
            active -= 1
            return [{
                "item_id": "item-locked",
                "observed_at": time.time(),
                "source": "seller_backend_verified",
                "view_count": 1,
            }]

        async def run_both():
            return await asyncio.gather(
                collect_item_metrics_once(
                    self.db,
                    user_id=self.owner_one["id"],
                    cookie_id="account-one",
                    cookie_string="unb=one; cookie2=x",
                ),
                collect_item_metrics_once(
                    self.db,
                    user_id=self.owner_one["id"],
                    cookie_id="account-one",
                    cookie_string="unb=one; cookie2=x",
                ),
            )

        register_item_metric_collector(collector)
        results = asyncio.run(run_both())

        self.assertEqual(max_active, 1)
        self.assertTrue(all(result["success"] for result in results))

    def test_adapter_row_limit_rejects_entire_batch(self):
        async def collector(**_kwargs):
            return [
                {
                    "item_id": f"item-{index}",
                    "observed_at": 1785114000.0,
                    "source": "seller_backend_verified",
                    "view_count": index,
                }
                for index in range(201)
            ]

        register_item_metric_collector(collector)
        result = asyncio.run(collect_item_metrics_once(
            self.db,
            user_id=self.owner_one["id"],
            cookie_id="account-one",
            cookie_string="unb=one; cookie2=x",
            canary=True,
        ))
        self.assertFalse(result["success"])
        self.assertEqual(result["error_code"], "metric_adapter_result_not_bounded")
        self.assertEqual(
            self.db.get_item_traffic_analytics(user_id=self.owner_one["id"])[
                "snapshot_count"
            ],
            0,
        )

    def test_unbounded_adapter_iterable_is_rejected_without_consumption(self):
        consumed = False

        def rows():
            nonlocal consumed
            consumed = True
            yield {
                "item_id": "item-unbounded",
                "observed_at": 1785114000.0,
                "source": "seller_backend_verified",
                "view_count": 1,
            }

        async def collector(**_kwargs):
            return rows()

        register_item_metric_collector(collector)
        result = asyncio.run(collect_item_metrics_once(
            self.db,
            user_id=self.owner_one["id"],
            cookie_id="account-one",
            cookie_string="unb=one; cookie2=x",
            canary=True,
        ))

        self.assertFalse(result["success"])
        self.assertEqual(result["error_code"], "metric_adapter_result_not_bounded")
        self.assertFalse(consumed)

    def test_db_batch_checks_sequence_length_before_indexing(self):
        class OversizedSequence(Sequence):
            def __len__(self):
                return 201

            def __getitem__(self, _index):
                raise AssertionError("oversized result must not be indexed")

        with self.assertRaisesRegex(ValueError, "最多保存 200"):
            self.db.record_item_metric_snapshots(
                user_id=self.owner_one["id"],
                cookie_id="account-one",
                rows=OversizedSequence(),
            )

    def test_duplicate_observation_does_not_advance_canary(self):
        calls = 0

        async def collector(**_kwargs):
            nonlocal calls
            observed_at = 1785114000.0 + calls * 600
            calls += 1
            return [{
                "item_id": "item-canary",
                "observed_at": observed_at,
                "source": "seller_backend_verified",
                "view_count": 10,
            }]

        register_item_metric_collector(collector)
        results = [
            asyncio.run(collect_item_metrics_once(
                self.db,
                user_id=self.owner_one["id"],
                cookie_id="account-one",
                cookie_string="unb=one; cookie2=x",
                canary=True,
            ))
            for _ in range(3)
        ]

        self.assertEqual(
            [result["canary_successes"] for result in results],
            [1, 1, 1],
        )
        self.assertEqual(
            [result["canary_advanced"] for result in results],
            [True, False, False],
        )
        self.assertFalse(results[-1]["collection_enabled"])

    def test_cross_connection_duplicate_canary_is_atomic(self):
        other = DBManager(self.db.db_path)
        barrier = threading.Barrier(2)

        def record(manager):
            barrier.wait(timeout=5)
            return manager.record_item_metric_canary_result(
                user_id=self.owner_one["id"],
                cookie_id="account-one",
                success=True,
                observed_at=1785114000.0,
            )

        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                states = list(executor.map(record, (self.db, other)))
            final_state = self.db.get_item_metric_collection_state(
                user_id=self.owner_one["id"],
                cookie_id="account-one",
            )
        finally:
            other.close()

        self.assertEqual(final_state["canary_success_count"], 1)
        self.assertEqual(
            sum(int(state["canary_advanced"]) for state in states),
            1,
        )

    def test_collection_failure_does_not_expose_adapter_error_details(self):
        async def collector(**_kwargs):
            raise RuntimeError("cookie2=private-session-value")

        register_item_metric_collector(collector)
        result = asyncio.run(collect_item_metrics_once(
            self.db,
            user_id=self.owner_one["id"],
            cookie_id="account-one",
            cookie_string="unb=one; cookie2=x",
            canary=True,
        ))

        self.assertFalse(result["success"])
        self.assertEqual(result["error_code"], "metric_collection_failed")
        self.assertEqual(result["error_type"], "RuntimeError")
        self.assertNotIn("private-session-value", result["message"])


class ItemMetricApiTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.previous_key_file = os.environ.get("SYSTEM_SECRET_KEY_FILE")
        os.environ["SYSTEM_SECRET_KEY_FILE"] = str(self.root / ".system-key")
        self.db = DBManager(str(self.root / "metrics-api.db"))
        self.assertTrue(
            self.db.create_user("metric-one", "metric-one@example.test", "Strong-pass-2026!")
        )
        self.assertTrue(
            self.db.create_user("metric-two", "metric-two@example.test", "Strong-pass-2026!")
        )
        self.owner_one = self.db.get_user_by_username("metric-one")
        self.owner_two = self.db.get_user_by_username("metric-two")
        with self.db.lock:
            self.db.conn.executemany(
                "INSERT INTO cookies (id, value, user_id) VALUES (?, ?, ?)",
                (
                    ("metric-account-one", "unb=one; cookie2=x", self.owner_one["id"]),
                    ("metric-account-two", "unb=two; cookie2=x", self.owner_two["id"]),
                ),
            )
            self.db.conn.executemany(
                "INSERT INTO orders (order_id, item_id, item_title, order_status, cookie_id,"
                " created_at, paid_amount_fen) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    ("metric-order-1", "item-1", "商品一", "completed", "metric-account-one", "2026-07-20 10:00:00", 1250),
                    ("metric-order-2", "item-2", "商品二", "completed", "metric-account-one", "2026-07-20 11:00:00", None),
                    ("metric-order-3", "item-9", "其他租户商品", "completed", "metric-account-two", "2026-07-20 12:00:00", 9900),
                ),
            )
            self.db.conn.commit()
        self.original_db = reply_server.db_manager
        reply_server.db_manager = self.db
        reply_server.SESSION_TOKENS.clear()
        self.client = TestClient(reply_server.app, raise_server_exceptions=False)

    def tearDown(self):
        register_item_metric_collector(None)
        self.client.close()
        reply_server.SESSION_TOKENS.clear()
        reply_server.db_manager = self.original_db
        self.db.close()
        if self.previous_key_file is None:
            os.environ.pop("SYSTEM_SECRET_KEY_FILE", None)
        else:
            os.environ["SYSTEM_SECRET_KEY_FILE"] = self.previous_key_file
        self.tempdir.cleanup()

    def headers_for(self, user):
        token, _ = reply_server.create_login_session(user)
        return {"Authorization": f"Bearer {token}"}

    def test_item_analytics_routes_require_authentication(self):
        self.assertEqual(self.client.get("/analytics/items/performance").status_code, 401)
        self.assertEqual(self.client.get("/analytics/items/traffic").status_code, 401)
        self.assertEqual(
            self.client.post(
                "/analytics/items/metrics/sync",
                json={"cookie_id": "metric-account-one"},
            ).status_code,
            401,
        )
        self.assertEqual(
            self.client.get("/analytics/items/metrics/status").status_code,
            401,
        )

    def test_performance_endpoint_counts_empty_amount_and_isolates_tenant(self):
        response = self.client.get(
            "/analytics/items/performance",
            headers=self.headers_for(self.owner_one),
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["metric_source"], "order_transactions")
        self.assertEqual(payload["amount_coverage"]["total_orders"], 2)
        self.assertEqual(payload["amount_coverage"]["with_amount"], 1)
        self.assertEqual(
            {row["item_id"] for row in payload["items"]},
            {"item-1", "item-2"},
        )
        self.assertNotIn("item-9", response.text)
        self.assertNotIn("其他租户商品", response.text)

    def test_traffic_endpoint_is_scoped_and_foreign_account_fails_closed(self):
        self.db.record_item_metric_snapshot(
            user_id=self.owner_one["id"],
            cookie_id="metric-account-one",
            item_id="item-1",
            observed_at=1785114000.0,
            source="seller_backend_verified",
            view_count=10,
        )
        self.db.record_item_metric_snapshot(
            user_id=self.owner_two["id"],
            cookie_id="metric-account-two",
            item_id="item-9",
            observed_at=1785114000.0,
            source="seller_backend_verified",
            view_count=99,
        )
        headers = self.headers_for(self.owner_one)
        response = self.client.get("/analytics/items/traffic", headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["snapshot_count"], 1)
        self.assertNotIn("item-9", response.text)

        foreign = self.client.get(
            "/analytics/items/traffic",
            params={"cookie_id": "metric-account-two"},
            headers=headers,
        )
        self.assertEqual(foreign.status_code, 404, foreign.text)

    def test_manual_canary_requires_adapter_and_three_successes(self):
        headers = self.headers_for(self.owner_one)
        unavailable = self.client.post(
            "/analytics/items/metrics/sync",
            json={"cookie_id": "metric-account-one"},
            headers=headers,
        )
        self.assertEqual(unavailable.status_code, 409, unavailable.text)
        self.assertEqual(unavailable.json()["error_code"], "metric_adapter_unavailable")

        calls = []

        async def collector(*, cookie_id, cookie_string):
            calls.append(cookie_id)
            return [{
                "item_id": "item-1",
                "observed_at": 1785110400.0 + len(calls) * 3600,
                "source": "seller_backend_verified",
                "view_count": 9 + len(calls),
            }]

        register_item_metric_collector(collector)
        with patch.object(
            item_metric_scheduler_module.item_metric_scheduler,
            "start",
            new=AsyncMock(return_value=True),
        ) as start:
            results = [
                self.client.post(
                    "/analytics/items/metrics/sync",
                    json={"cookie_id": "metric-account-one"},
                    headers=headers,
                )
                for _ in range(3)
            ]

        self.assertTrue(all(response.status_code == 200 for response in results))
        self.assertEqual(
            [response.json()["canary_successes"] for response in results],
            [1, 2, 3],
        )
        self.assertEqual(
            [response.json()["collection_enabled"] for response in results],
            [False, False, True],
        )
        self.assertEqual(calls, ["metric-account-one"] * 3)
        start.assert_awaited_once()

    def test_manual_canary_rejects_foreign_account_without_calling_adapter(self):
        collector = AsyncMock(return_value=[])
        register_item_metric_collector(collector)
        response = self.client.post(
            "/analytics/items/metrics/sync",
            json={"cookie_id": "metric-account-one"},
            headers=self.headers_for(self.owner_two),
        )
        self.assertEqual(response.status_code, 404, response.text)
        collector.assert_not_awaited()

    def test_metric_status_reports_only_current_users_accounts(self):
        for index in range(3):
            self.db.record_item_metric_canary_result(
                user_id=self.owner_one["id"],
                cookie_id="metric-account-one",
                success=True,
                observed_at=1785106800.0 + index * 3600,
            )
        response = self.client.get(
            "/analytics/items/metrics/status",
            headers=self.headers_for(self.owner_one),
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["enabled_accounts"], 1)
        self.assertEqual(len(payload["accounts"]), 1)
        self.assertEqual(payload["accounts"][0]["cookie_id"], "metric-account-one")
        self.assertNotIn("metric-account-two", response.text)


if __name__ == "__main__":
    unittest.main()
