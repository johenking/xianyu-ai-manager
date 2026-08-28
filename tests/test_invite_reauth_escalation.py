"""邀请桥 skipped_reauth 升级告警契约（P2，2026-08-28）。

背景：6 个账号 session 过期后，兜底扫描每轮各刷一条 INFO（单日 3 万+ 行），
却没有升级信号，高产账号的兜底路径实际失效多日无人知晓。
契约：短期内保留逐轮 INFO；持续超过阈值后逐轮 INFO 静默，改为每账号
周期性 WARNING（自带持续分钟数）；恢复时输出一条恢复 INFO。
"""

import time
import unittest

from loguru import logger

import invite_bridge_poller as poller_module


class ReauthEscalationTests(unittest.TestCase):
    def setUp(self):
        self.poller = poller_module.InviteBridgePoller()
        self.records = []
        self.handler_id = logger.add(
            lambda message: self.records.append(str(message)),
            format="{level}|{message}",
            level="DEBUG",
        )

    def tearDown(self):
        logger.remove(self.handler_id)

    def _lines(self, marker):
        return [line for line in self.records if marker in line]

    def test_fresh_block_keeps_per_scan_info(self):
        self.poller._note_reauth_skip("acct-1", "待发货扫描")
        self.poller._note_reauth_skip("acct-1", "待发货扫描")
        info_lines = self._lines("reason=skipped_reauth")
        self.assertEqual(len(info_lines), 2)
        self.assertTrue(all(line.startswith("INFO|") for line in info_lines))
        self.assertEqual(self._lines("邀请桥兜底扫描持续失效"), [])

    def test_persistent_block_escalates_to_rate_limited_warning(self):
        now = time.time()
        self.poller._reauth_blocked_since["acct-2"] = (
            now - poller_module.REAUTH_ESCALATION_AFTER_SECONDS - 120
        )

        self.poller._note_reauth_skip("acct-2", "待发货扫描")
        warnings = self._lines("邀请桥兜底扫描持续失效")
        self.assertEqual(len(warnings), 1)
        self.assertTrue(warnings[0].startswith("WARNING|"))
        self.assertIn("blocked_minutes=", warnings[0])
        self.assertIn("需重新登录", warnings[0])

        # 告警间隔内不重复刷 WARNING，也不再刷逐轮 INFO
        self.poller._note_reauth_skip("acct-2", "待发货扫描")
        self.assertEqual(len(self._lines("邀请桥兜底扫描持续失效")), 1)
        self.assertEqual(
            len([l for l in self._lines("reason=skipped_reauth") if l.startswith("INFO|")]),
            0,
        )

        # 超过告警间隔后再次 WARNING
        self.poller._reauth_last_warned["acct-2"] = (
            now - poller_module.REAUTH_WARN_INTERVAL_SECONDS - 60
        )
        self.poller._note_reauth_skip("acct-2", "待发货扫描")
        self.assertEqual(len(self._lines("邀请桥兜底扫描持续失效")), 2)

    def test_detail_suffix_is_preserved(self):
        self.poller._note_reauth_skip("acct-3", "单订单直达", detail="order_ref=abc123")
        lines = self._lines("order_ref=abc123")
        self.assertEqual(len(lines), 1)
        self.assertIn("单订单直达", lines[0])

    def test_recovery_clears_state_and_logs_once(self):
        self.poller._note_reauth_skip("acct-4", "待发货扫描")
        self.poller._clear_reauth_skip("acct-4")
        recovered = self._lines("兜底扫描重新生效")
        self.assertEqual(len(recovered), 1)
        self.assertNotIn("acct-4", self.poller._reauth_blocked_since)
        self.assertNotIn("acct-4", self.poller._reauth_last_warned)

        # 未被拉黑的账号 clear 不输出日志
        self.poller._clear_reauth_skip("acct-never-blocked")
        self.assertEqual(len(self._lines("兜底扫描重新生效")), 1)


if __name__ == "__main__":
    unittest.main()
