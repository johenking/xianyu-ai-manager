"""发货链路阶段观测埋点契约：DELIVERY_STAGE / DELIVERY_STAGE_SUMMARY。

P0 观测需求（2026-08-28）：量化"付款到发货"各段耗时（本系统门禁 vs wo-f 内部）。
埋点必须幂等（重复回调不重复记录）、缺段可降级（差值标 na）、任何异常不外泄。
"""

import unittest

from loguru import logger

import delivery_stage_metrics as dsm


class DeliveryStageMetricsTests(unittest.TestCase):
    def setUp(self):
        dsm._orders.clear()
        self.records = []
        self.handler_id = logger.add(
            lambda message: self.records.append(str(message)),
            format="{level}|{message}",
            level="DEBUG",
        )

    def tearDown(self):
        logger.remove(self.handler_id)
        dsm._orders.clear()

    def _lines(self, marker):
        return [line for line in self.records if marker in line]

    def test_full_sequence_emits_stage_lines_and_summary(self):
        base = 1_700_000_000.0
        offsets = {
            dsm.STAGE_PAID: 0.0,
            dsm.STAGE_GATE: 1.0,
            dsm.STAGE_HANDOFF: 1.5,
            dsm.STAGE_CONFIRMATION: 9.5,
            dsm.STAGE_FULFILLMENT: 60.0,
            dsm.STAGE_SHIPPED: 64.0,
        }
        for stage in dsm.STAGE_SEQUENCE:
            dsm.record_stage("order-1", "acct-1", stage, now=base + offsets[stage])

        stage_lines = self._lines("DELIVERY_STAGE ")
        self.assertEqual(len(stage_lines), len(dsm.STAGE_SEQUENCE))
        self.assertIn("stage=paid_detected since_prev_ms=na since_paid_ms=0", stage_lines[0])
        self.assertIn("stage=gate_passed since_prev_ms=1000 since_paid_ms=1000", stage_lines[1])
        self.assertIn("stage=shipped since_prev_ms=4000 since_paid_ms=64000", stage_lines[5])

        summary_lines = self._lines("DELIVERY_STAGE_SUMMARY")
        self.assertEqual(len(summary_lines), 1)
        summary = summary_lines[0]
        self.assertIn("paid_detected=+0ms", summary)
        self.assertIn("gate_passed=+1000ms", summary)
        self.assertIn("wof_confirmation=+8000ms", summary)
        self.assertIn("shipped=+4000ms", summary)
        self.assertIn("total_ms=64000", summary)
        # 终态后登记表应清空，避免内存泄漏
        self.assertNotIn("order-1", dsm._orders)

    def test_duplicate_stage_recorded_once(self):
        dsm.record_stage("order-2", "acct-1", dsm.STAGE_PAID, now=100.0)
        dsm.record_stage("order-2", "acct-1", dsm.STAGE_PAID, now=200.0)
        self.assertEqual(len(self._lines("stage=paid_detected")), 1)

    def test_missing_paid_stage_degrades_to_na(self):
        dsm.record_stage("order-3", "acct-1", dsm.STAGE_CONFIRMATION, now=50.0)
        dsm.record_stage("order-3", "acct-1", dsm.STAGE_SHIPPED, now=60.0)
        confirmation_line = self._lines("stage=wof_confirmation")[0]
        self.assertIn("since_prev_ms=na since_paid_ms=na", confirmation_line)
        summary = self._lines("DELIVERY_STAGE_SUMMARY")[0]
        self.assertIn("paid_detected=na", summary)
        self.assertIn("wof_confirmation=+0ms", summary)
        self.assertIn("shipped=+10000ms", summary)
        self.assertIn("total_ms=10000", summary)

    def test_invalid_input_never_raises(self):
        dsm.record_stage(None, None, dsm.STAGE_PAID)
        dsm.record_stage("order-4", "acct-1", "not-a-stage")
        dsm.record_stage("", "acct-1", dsm.STAGE_PAID)
        self.assertEqual(self._lines("DELIVERY_STAGE "), [])

    def test_tracker_capacity_is_bounded(self):
        for index in range(dsm._MAX_TRACKED_ORDERS + 50):
            dsm.record_stage(f"order-cap-{index}", "acct-1", dsm.STAGE_PAID, now=1.0)
        self.assertLessEqual(len(dsm._orders), dsm._MAX_TRACKED_ORDERS)


if __name__ == "__main__":
    unittest.main()
