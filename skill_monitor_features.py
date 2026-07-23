"""Fail-closed feature switches for Skill Center monitoring."""

from __future__ import annotations

from typing import Any, Dict, Optional


SKILL_MONITOR_FEATURE_DEFAULTS: Dict[str, bool] = {
    "skill_monitor_enabled": False,
    "skill_monitor_scheduler_enabled": False,
    "skill_monitor_delivery_enabled": False,
    "skill_monitor_mtop_enabled": False,
}


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def get_skill_monitor_feature_state(database: Optional[Any] = None) -> Dict[str, Any]:
    """Return configured and effective flags; lookup failures stay disabled."""
    if database is None:
        from db_manager import db_manager as database

    configured: Dict[str, bool] = {}
    for key, default in SKILL_MONITOR_FEATURE_DEFAULTS.items():
        try:
            raw_value = database.get_system_setting(key)
        except Exception:
            raw_value = None
        configured[key] = default if raw_value is None else _as_bool(raw_value)

    master_enabled = configured["skill_monitor_enabled"]
    effective = {
        key: (value if key == "skill_monitor_enabled" else master_enabled and value)
        for key, value in configured.items()
    }
    return {
        "configured": configured,
        "effective": effective,
        "fail_closed": True,
    }


def skill_monitor_feature_enabled(key: str, database: Optional[Any] = None) -> bool:
    if key not in SKILL_MONITOR_FEATURE_DEFAULTS:
        return False
    state = get_skill_monitor_feature_state(database)
    return bool(state["effective"].get(key, False))
