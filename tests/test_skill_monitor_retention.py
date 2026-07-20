import asyncio
import unittest
from unittest.mock import Mock

from skill_monitor_retention_janitor import SkillMonitorRetentionJanitor


class SkillMonitorRetentionJanitorTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_runs_local_cleanup_once_and_stop_releases_task(self):
        database = Mock()
        database.cleanup_expired_skill_monitor_records.return_value = {
            "deliveries": 1,
            "events": 1,
            "result_identities": 0,
            "results": 1,
            "runs": 0,
            "request_budgets": 0,
            "mtop_breakers": 0,
            "recovered_runs": 0,
            "recovered_deliveries": 0,
        }
        janitor = SkillMonitorRetentionJanitor(
            interval_seconds=3600,
            database=database,
        )

        await janitor.start()

        self.assertTrue(janitor.running)
        database.cleanup_expired_skill_monitor_records.assert_called_once_with()

        await janitor.stop()
        self.assertFalse(janitor.running)

    async def test_cleanup_failure_does_not_prevent_lifecycle_start(self):
        database = Mock()
        database.cleanup_expired_skill_monitor_records.side_effect = RuntimeError(
            "synthetic cleanup failure"
        )
        janitor = SkillMonitorRetentionJanitor(
            interval_seconds=3600,
            database=database,
        )

        await janitor.start()
        await asyncio.sleep(0)

        self.assertTrue(janitor.running)
        database.cleanup_expired_skill_monitor_records.assert_called_once_with()

        await janitor.stop()
        self.assertFalse(janitor.running)


if __name__ == "__main__":
    unittest.main()
