"""阶段B「经营驾驶舱」分析能力测试。

覆盖两块可量化经营辅助分析（仅行为层面，不刻画客户类型/画像标签）：

1. 时段流量分析 get_traffic_analytics / GET /analytics/traffic
   - 用真实成交时间 ordered_at_utc（UTC 秒级 epoch）按东八区分桶。
   - 时间边界沿用 created_at（与既有 get_order_analytics 口径一致，作覆盖率分母）；
     只有 ordered_at_utc IS NOT NULL 的订单进入时段分桶，并回报覆盖率。

2. 买家行为分析 get_buyer_behavior_analytics / GET /analytics/buyers
   - 复购次数、下单频次分布、买家贡献榜；只做订单能直接得出的行为量。
   - 时间边界用 created_at，覆盖旧订单不丢数据。

所有查询强制按登录用户隔离（JOIN cookies WHERE c.user_id = ?），
user_id=None 必须失败关闭（抛 ValueError）。
"""

import calendar
import os
from datetime import datetime
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

from db_manager import DBManager
import reply_server


def _epoch_cst(year, month, day, hour, minute):
    """把东八区(CST)墙上时间换算成 UTC 秒级 epoch。

    ordered_at_utc 存的是 UTC epoch，东八区小时 = UTC 小时 + 8，
    因此这里先减 8 小时得到 UTC 时刻，再用 timegm 转 epoch。
    """
    return float(calendar.timegm((year, month, day, hour - 8, minute, 0, 0, 0, 0)))


class BusinessAnalyticsTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.db_path = self.root / "analytics.db"
        self.previous_key_file = os.environ.get("SYSTEM_SECRET_KEY_FILE")
        os.environ["SYSTEM_SECRET_KEY_FILE"] = str(self.root / ".system-key")
        self.db = DBManager(str(self.db_path))
        self.assertTrue(
            self.db.create_user("owner-one", "owner-one@example.test", "Strong-pass-2026!")
        )
        self.assertTrue(
            self.db.create_user("owner-two", "owner-two@example.test", "Strong-pass-2026!")
        )
        self.user_one = self.db.get_user_by_username("owner-one")
        self.user_two = self.db.get_user_by_username("owner-two")
        self.original_db = reply_server.db_manager
        reply_server.db_manager = self.db
        reply_server.SESSION_TOKENS.clear()
        self.client = TestClient(reply_server.app, raise_server_exceptions=False)
        self._seed_orders()

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

    def headers_for(self, user):
        token, _ = reply_server.create_login_session(user)
        return {"Authorization": f"Bearer {token}"}

    def _seed_orders(self):
        with self.db.lock:
            cursor = self.db.conn.cursor()
            for cookie_id, user_id in (
                ("one-active", self.user_one["id"]),
                ("two-active", self.user_two["id"]),
            ):
                cursor.execute(
                    "INSERT INTO cookies (id, value, user_id, xianyu_unb) VALUES (?, ?, ?, ?)",
                    (cookie_id, f"unb={cookie_id}; cookie2=session", user_id, cookie_id),
                )
                cursor.execute(
                    "INSERT INTO cookie_status (cookie_id, enabled) VALUES (?, 1)",
                    (cookie_id,),
                )
            # user_one 名下 4 单（都在 2026-07-10，都 completed）：
            #  a/b: buyer-x 复购，东八区 14 点；c: buyer-y 15 点；d: buyer-z 无成交时间
            rows = (
                ("order-a", "item-1", "buyer-x", "买家X", "¥10.00", "completed",
                 "one-active", "2026-07-10 09:00:00", _epoch_cst(2026, 7, 10, 14, 30)),
                ("order-b", "item-1", "buyer-x", "买家X", "¥22.50", "completed",
                 "one-active", "2026-07-10 09:05:00", _epoch_cst(2026, 7, 10, 14, 50)),
                ("order-c", "item-2", "buyer-y", "买家Y", "¥5.00", "completed",
                 "one-active", "2026-07-10 09:10:00", _epoch_cst(2026, 7, 10, 15, 10)),
                ("order-d", "item-3", "buyer-z", "买家Z", "¥8.00", "completed",
                 "one-active", "2026-07-10 09:15:00", None),
                # user_two 名下 1 单：租户隔离验证，不得泄漏到 user_one 的结果
                ("order-e", "item-9", "buyer-w", "买家W", "¥99.00", "completed",
                 "two-active", "2026-07-10 09:20:00", _epoch_cst(2026, 7, 10, 14, 0)),
            )
            cursor.executemany(
                "INSERT INTO orders (order_id, item_id, buyer_id, buyer_nickname, amount, "
                "order_status, cookie_id, created_at, ordered_at_utc) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            self.db.conn.commit()

    # -------------------- 时段流量分析（DB 层） --------------------
    def test_traffic_analytics_buckets_by_cst_hour_with_coverage(self):
        result = self.db.get_traffic_analytics(
            start_date="2026-07-10",
            end_date="2026-07-10",
            user_id=self.user_one["id"],
            include_statuses=["pending_ship", "shipped", "completed"],
        )
        coverage = result["coverage"]
        # 有效订单 4 单，其中 3 单有成交时间
        self.assertEqual(coverage["total_orders"], 4)
        self.assertEqual(coverage["with_ordered_at"], 3)
        self.assertAlmostEqual(coverage["coverage_rate"], 0.75, places=4)

        # 东八区小时桶：14 点 2 单、15 点 1 单
        hourly = {row["hour"]: row["order_count"] for row in result["hourly"]}
        self.assertEqual(hourly.get(14), 2)
        self.assertEqual(hourly.get(15), 1)
        self.assertNotIn(6, hourly)  # 不得按 UTC 小时(06/07)分桶

        # 东八区 2026-07-10 是周五：strftime('%w') 周日=0，周五=5
        expected_dow = datetime(2026, 7, 10).strftime("%w")
        weekday = {row["weekday"]: row["order_count"] for row in result["weekday"]}
        self.assertEqual(weekday.get(expected_dow), 3)

    def test_traffic_analytics_isolated_per_user(self):
        result = self.db.get_traffic_analytics(
            start_date="2026-07-10",
            end_date="2026-07-10",
            user_id=self.user_two["id"],
            include_statuses=["pending_ship", "shipped", "completed"],
        )
        self.assertEqual(result["coverage"]["total_orders"], 1)
        hourly = {row["hour"]: row["order_count"] for row in result["hourly"]}
        self.assertEqual(hourly.get(14), 1)  # buyer-w 的 order-e

    def test_traffic_analytics_rejects_missing_user_id(self):
        with self.assertRaises(ValueError):
            self.db.get_traffic_analytics(user_id=None)

    # -------------------- 买家行为分析（DB 层） --------------------
    def test_buyer_behavior_repeat_and_frequency(self):
        result = self.db.get_buyer_behavior_analytics(
            start_date="2026-07-10",
            end_date="2026-07-10",
            user_id=self.user_one["id"],
            include_statuses=["pending_ship", "shipped", "completed"],
        )
        summary = result["summary"]
        self.assertEqual(summary["total_buyers"], 3)       # x, y, z
        self.assertEqual(summary["repeat_buyers"], 1)      # 仅 x 下单≥2
        self.assertAlmostEqual(summary["repeat_rate"], 1 / 3, places=4)

        # 频次分布：下 1 单的买家 2 个（y,z），下 2 单的买家 1 个（x）
        freq = {row["order_count"]: row["buyer_count"] for row in result["frequency"]}
        self.assertEqual(freq.get(1), 2)
        self.assertEqual(freq.get(2), 1)

    def test_buyer_behavior_contribution_ranking(self):
        result = self.db.get_buyer_behavior_analytics(
            start_date="2026-07-10",
            end_date="2026-07-10",
            user_id=self.user_one["id"],
            include_statuses=["pending_ship", "shipped", "completed"],
        )
        top = result["top_buyers"]
        # 按贡献金额降序：x(32.50) > z(8.00) > y(5.00)
        self.assertEqual(top[0]["buyer_id"], "buyer-x")
        self.assertAlmostEqual(top[0]["total_amount"], 32.5, places=2)
        self.assertEqual(top[0]["order_count"], 2)
        self.assertEqual([row["buyer_id"] for row in top[:3]],
                         ["buyer-x", "buyer-z", "buyer-y"])

    def test_buyer_behavior_isolated_per_user(self):
        result = self.db.get_buyer_behavior_analytics(
            start_date="2026-07-10",
            end_date="2026-07-10",
            user_id=self.user_two["id"],
            include_statuses=["pending_ship", "shipped", "completed"],
        )
        self.assertEqual(result["summary"]["total_buyers"], 1)
        self.assertNotIn("buyer-x", [row["buyer_id"] for row in result["top_buyers"]])

    def test_buyer_behavior_rejects_missing_user_id(self):
        with self.assertRaises(ValueError):
            self.db.get_buyer_behavior_analytics(user_id=None)

    # -------------------- 端点层 --------------------
    def test_traffic_endpoint_returns_scoped_payload(self):
        response = self.client.get(
            "/analytics/traffic",
            params={"start_date": "2026-07-10", "end_date": "2026-07-10"},
            headers=self.headers_for(self.user_one),
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["coverage"]["total_orders"], 4)
        self.assertEqual(payload["coverage"]["with_ordered_at"], 3)
        hourly = {row["hour"]: row["order_count"] for row in payload["hourly"]}
        self.assertEqual(hourly.get(14), 2)

    def test_buyers_endpoint_returns_scoped_payload(self):
        response = self.client.get(
            "/analytics/buyers",
            params={"start_date": "2026-07-10", "end_date": "2026-07-10"},
            headers=self.headers_for(self.user_one),
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["summary"]["total_buyers"], 3)
        self.assertEqual(payload["top_buyers"][0]["buyer_id"], "buyer-x")

    def test_analytics_endpoints_are_isolated_across_users(self):
        # user_two 查买家榜，不得看到 user_one 名下买家
        response = self.client.get(
            "/analytics/buyers",
            params={"start_date": "2026-07-10", "end_date": "2026-07-10"},
            headers=self.headers_for(self.user_two),
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn("buyer-x", response.text)
        self.assertNotIn("买家X", response.text)


if __name__ == "__main__":
    unittest.main()
