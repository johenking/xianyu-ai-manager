import unittest

from skill_monitor_features import (
    get_skill_monitor_feature_state,
    skill_monitor_feature_enabled,
)


class _SettingsDatabase:
    def __init__(self, values=None, *, fail=False):
        self.values = values or {}
        self.fail = fail

    def get_system_setting(self, key):
        if self.fail:
            raise RuntimeError("synthetic lookup failure")
        return self.values.get(key)


class SkillMonitorFeatureTests(unittest.TestCase):
    def test_missing_or_failed_lookups_are_fail_closed(self):
        self.assertFalse(
            skill_monitor_feature_enabled(
                "skill_monitor_enabled",
                _SettingsDatabase(),
            )
        )
        self.assertFalse(
            skill_monitor_feature_enabled(
                "skill_monitor_enabled",
                _SettingsDatabase(fail=True),
            )
        )
        self.assertFalse(
            skill_monitor_feature_enabled("unknown_feature", _SettingsDatabase())
        )

    def test_master_switch_controls_every_effective_subfeature(self):
        database = _SettingsDatabase(
            {
                "skill_monitor_enabled": "false",
                "skill_monitor_scheduler_enabled": "true",
                "skill_monitor_delivery_enabled": "true",
                "skill_monitor_mtop_enabled": "true",
            }
        )
        state = get_skill_monitor_feature_state(database)
        self.assertTrue(state["configured"]["skill_monitor_scheduler_enabled"])
        self.assertFalse(state["effective"]["skill_monitor_scheduler_enabled"])
        self.assertFalse(state["effective"]["skill_monitor_delivery_enabled"])
        self.assertFalse(state["effective"]["skill_monitor_mtop_enabled"])

        database.values["skill_monitor_enabled"] = "true"
        state = get_skill_monitor_feature_state(database)
        self.assertTrue(state["effective"]["skill_monitor_scheduler_enabled"])
        self.assertTrue(state["effective"]["skill_monitor_delivery_enabled"])
        self.assertTrue(state["effective"]["skill_monitor_mtop_enabled"])


if __name__ == "__main__":
    unittest.main()
