import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from auto_rate_service import AutoRateScheduler, _build_rate_payload, parse_rate_response
from ai_reply_engine import AIReplyEngine
from db_manager import DBManager
from order_sync_service import normalize_order_record


class AutoRateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self._env = {}
        for name in (
            "ACCOUNT_CREDENTIAL_KEY_FILE",
            "SYSTEM_SECRET_KEY_FILE",
            "AI_PROVIDER_KEY_FILE",
        ):
            self._env[name] = os.environ.get(name)
            os.environ[name] = str(root / f".{name.lower()}")
        self.db = DBManager(str(root / "auto-rate.db"))
        self.db.conn.execute(
            "INSERT INTO users (username, email, password_hash) VALUES (?, ?, ?)",
            ("seller", "seller@example.com", "synthetic-hash"),
        )
        self.db.conn.execute(
            "INSERT INTO cookies (id, value, user_id, xianyu_unb) VALUES (?, ?, ?, ?)",
            ("account-1", "unb=100; _m_h5_tk=token_suffix", 1, "100"),
        )
        self.db.conn.commit()

    def tearDown(self):
        self.db.close()
        for name, value in self._env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        self.tempdir.cleanup()

    def test_only_explicit_rate_action_is_eligible(self):
        eligible = normalize_order_record({
            "commonData": {"orderId": "new", "orderStatus": "交易成功"},
            "rightVO": {"btnList": [{"tradeAction": "RATE"}]},
        }, "account-1")
        completed_only = normalize_order_record({
            "commonData": {"orderId": "old", "orderStatus": "交易成功"},
        }, "account-1")

        self.assertTrue(eligible["can_rate"])
        self.assertFalse(completed_only["can_rate"])

    def test_opt_in_boundary_and_order_idempotency_are_enforced_by_sqlite(self):
        self.assertFalse(self.db.get_auto_rate_settings("account-1", 1)["enabled"])
        self.assertTrue(self.db.update_auto_rate("account-1", 1, True, now=1_000))
        self.assertFalse(self.db.schedule_auto_rate_task(
            user_id=1,
            cookie_id="account-1",
            order_id="before-enable",
            item_title="",
            order_created_at=999,
            due_at=1_300,
            now=1_000,
        ))
        self.assertTrue(self.db.schedule_auto_rate_task(
            user_id=1,
            cookie_id="account-1",
            order_id="after-enable",
            item_title="测试商品",
            order_created_at=1_001,
            due_at=1_300,
            now=1_000,
        ))
        self.assertFalse(self.db.schedule_auto_rate_task(
            user_id=1,
            cookie_id="account-1",
            order_id="after-enable",
            item_title="测试商品",
            order_created_at=1_001,
            due_at=1_400,
            now=1_100,
        ))
        self.assertEqual(
            self.db.conn.execute("SELECT COUNT(*) FROM order_auto_ratings").fetchone()[0],
            1,
        )

    def test_historical_opt_in_bypasses_only_the_enable_time_boundary(self):
        self.assertTrue(self.db.update_auto_rate("account-1", 1, True, now=1_000))
        self.assertTrue(self.db.schedule_auto_rate_task(
            user_id=1,
            cookie_id="account-1",
            order_id="before-enable",
            item_title="历史商品",
            order_created_at=999,
            due_at=1_300,
            now=1_000,
            allow_historical=True,
        ))
        self.assertFalse(self.db.schedule_auto_rate_task(
            user_id=1,
            cookie_id="account-1",
            order_id="before-enable",
            item_title="历史商品",
            order_created_at=999,
            due_at=1_400,
            now=1_100,
            allow_historical=True,
        ))

    def test_restart_replays_only_tasks_that_never_started_the_platform_post(self):
        self.assertTrue(self.db.update_auto_rate("account-1", 1, True, now=1_000))
        self.assertTrue(self.db.schedule_auto_rate_task(
            user_id=1,
            cookie_id="account-1",
            order_id="order-1",
            item_title="",
            order_created_at=1_001,
            due_at=1_002,
            now=1_000,
        ))
        first = self.db.claim_due_auto_rate_task(now=1_003)
        self.assertIsNotNone(first)
        self.assertEqual(self.db.reconcile_interrupted_auto_rate_tasks(now=1_004), 0)
        self.assertEqual(
            self.db.conn.execute("SELECT state FROM order_auto_ratings").fetchone()[0],
            "scheduled",
        )

        second = self.db.claim_due_auto_rate_task(now=1_005)
        self.assertTrue(self.db.mark_auto_rate_submission_started(second["id"], now=1_006))
        self.assertEqual(self.db.reconcile_interrupted_auto_rate_tasks(now=1_007), 1)
        self.assertEqual(
            self.db.conn.execute("SELECT state FROM order_auto_ratings").fetchone()[0],
            "needs_reconcile",
        )

    async def test_scheduler_delays_deduplicates_and_submits_one_verified_order(self):
        now = 2_000_000.0
        self.assertTrue(self.db.update_auto_rate("account-1", 1, True, now=now - 100))

        class FakeClient:
            async def discover(_self, **_kwargs):
                return {
                    "success": True,
                    "orders": [
                        {
                            "order_id": "rateable",
                            "item_title": "测试商品",
                            "created_at": now - 10,
                            "can_rate": True,
                        },
                        {
                            "order_id": "before-enable",
                            "created_at": now - 101,
                            "can_rate": True,
                        },
                        {
                            "order_id": "completed-but-no-rate",
                            "created_at": now - 5,
                            "can_rate": False,
                        },
                    ],
                }

        submitter = AsyncMock(return_value={
            "state": "succeeded",
            "result_code": "success",
            "error": "",
            "response": {"success_order_ids": ["rateable"]},
        })
        scheduler = AutoRateScheduler(
            db=self.db,
            client_factory=FakeClient,
            submitter=submitter,
            now_fn=lambda: now,
            jitter_fn=lambda low, high: 300,
        )
        await scheduler.scan_once()
        row = self.db.conn.execute(
            "SELECT order_id, due_at, state FROM order_auto_ratings"
        ).fetchone()
        self.assertEqual(row, ("rateable", now + 300, "scheduled"))
        submitter.assert_not_awaited()

        await scheduler.scan_once()
        self.assertEqual(
            self.db.conn.execute("SELECT COUNT(*) FROM order_auto_ratings").fetchone()[0],
            1,
        )
        self.db.conn.execute(
            "UPDATE order_auto_ratings SET due_at = ? WHERE order_id = ?",
            (now, "rateable"),
        )
        self.db.conn.commit()
        with patch.object(AutoRateScheduler, "_generate_feedback", return_value="交易顺利，感谢支持，五星好评！"):
            await scheduler.scan_once()

        submitter.assert_awaited_once()
        state, attempts, feedback, submitted_at = self.db.conn.execute(
            "SELECT state, attempt_count, feedback, submitted_at "
            "FROM order_auto_ratings WHERE order_id = ?",
            ("rateable",),
        ).fetchone()
        self.assertEqual((state, attempts), ("succeeded", 1))
        self.assertEqual(feedback, "交易顺利，感谢支持，五星好评！")
        self.assertEqual(submitted_at, now)

    def test_response_parser_requires_explicit_order_result(self):
        success = parse_rate_response({
            "ret": ["SUCCESS::调用成功"],
            "data": {"module": {"success": True, "successOrderIds": ["order-1"], "failOrderInfos": []}},
        }, "order-1")
        rejected = parse_rate_response({
            "ret": ["SUCCESS::调用成功"],
            "data": {"module": {"success": False, "successOrderIds": [], "failOrderInfos": [{"orderId": "order-1", "failReason": "已评价"}]}},
        }, "order-1")
        ambiguous = parse_rate_response({
            "ret": ["SUCCESS::调用成功"],
            "data": {"module": {"success": True}},
        }, "order-1")

        self.assertEqual(success["state"], "succeeded")
        self.assertEqual(rejected["state"], "failed")
        self.assertEqual(ambiguous["state"], "needs_reconcile")

    def test_rate_request_uses_verified_merchant_schema(self):
        self.assertEqual(
            _build_rate_payload("order-1", "交易顺利，感谢支持！"),
            {
                "tradeIdList": ["order-1"],
                "feedback": "交易顺利，感谢支持！",
                "rate": 1,
                "imageUrls": [],
                "anonymous": False,
            },
        )

    def test_ai_review_output_is_bounded_before_submission(self):
        self.assertEqual(
            AIReplyEngine._normalize_positive_review("好评：交易顺利，感谢支持，祝您生活愉快！"),
            "交易顺利，感谢支持，祝您生活愉快！",
        )
        self.assertIsNone(AIReplyEngine._normalize_positive_review("付款很快，沟通很好"))
        self.assertIsNone(AIReplyEngine._normalize_positive_review("交易不满意，申请退款"))


if __name__ == "__main__":
    unittest.main()
