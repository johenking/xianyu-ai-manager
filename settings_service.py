from typing import Any, Dict


USER_BASIC_SETTING_KEYS = (
    "item_sync_enabled",
    "item_sync_interval",
    "item_sync_max_pages",
)


BOOLEAN_SETTING_KEYS = {
    "registration_enabled",
    "show_default_login_info",
    "login_captcha_enabled",
    "item_sync_enabled",
    "smtp_use_tls",
    "smtp_use_ssl",
}

INTEGER_SETTING_KEYS = {
    "item_sync_interval",
    "item_sync_max_pages",
    "smtp_port",
}

SECRET_SETTING_KEYS = {
    "ai_api_key",
    "smtp_password",
}

SETTINGS_SECTION_KEYS = {
    "basic": {
        "registration_enabled",
        "show_default_login_info",
        "login_captcha_enabled",
        "item_sync_enabled",
        "item_sync_interval",
        "item_sync_max_pages",
    },
    "ai": {"ai_api_url", "ai_model", "default_reply", "ai_api_key"},
    "smtp": {
        "smtp_server",
        "smtp_port",
        "smtp_user",
        "smtp_password",
        "smtp_from",
        "smtp_use_tls",
        "smtp_use_ssl",
        "support_email",
    },
}

INTERNAL_SETTING_KEYS = {
    "admin_password_hash",
    "smtp_verified_fingerprint",
    "auth_trusted_proxies",
}


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def mask_secret(value: str) -> str:
    value = str(value or "")
    if not value:
        return ""
    return f"****{value[-4:]}"


def normalize_system_settings(raw: Dict[str, Any]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in (raw or {}).items():
        if key in SECRET_SETTING_KEYS:
            result[f"{key}_configured"] = bool(value)
            result[f"{key}_masked"] = mask_secret(str(value or ""))
        elif key in BOOLEAN_SETTING_KEYS:
            result[key] = _as_bool(value)
        elif key in INTEGER_SETTING_KEYS:
            result[key] = _as_int(value)
        elif key not in INTERNAL_SETTING_KEYS:
            result[key] = value

    for key in SECRET_SETTING_KEYS:
        result.setdefault(f"{key}_configured", False)
        result.setdefault(f"{key}_masked", "")
    return result


def resolve_user_basic_settings(
    global_settings: Dict[str, Any],
    user_settings: Dict[str, Any],
) -> Dict[str, Any]:
    """Resolve personal item-sync settings with global values as defaults."""
    values: Dict[str, Any] = {}
    sources: Dict[str, str] = {}
    defaults = {
        "item_sync_enabled": True,
        "item_sync_interval": 600,
        "item_sync_max_pages": 5,
    }
    for key in USER_BASIC_SETTING_KEYS:
        user_entry = (user_settings or {}).get(key)
        if isinstance(user_entry, dict):
            user_value = user_entry.get("value")
        else:
            user_value = user_entry
        has_user_value = user_entry is not None
        global_value = (global_settings or {}).get(key)
        if global_value is None:
            global_value = defaults[key]
        raw_value = user_value if has_user_value else global_value
        values[key] = _as_bool(raw_value) if key == "item_sync_enabled" else _as_int(
            raw_value, defaults[key]
        )
        sources[key] = "user" if has_user_value else "global"
    return {
        "settings": values,
        "sources": sources,
        "inherited": all(source == "global" for source in sources.values()),
    }


def apply_secret_action(existing: str, action: str, value: str) -> str:
    action = str(action or "keep").strip().lower()
    if action == "keep":
        return str(existing or "")
    if action == "clear":
        return ""
    if action == "set":
        value = str(value or "").strip()
        if not value:
            raise ValueError("新密钥不能为空")
        return value
    raise ValueError("不支持的密钥操作")


def validate_skill_monitor_features(*, notify_enabled: bool, ai_filter: str) -> None:
    # AI筛选和通知已经由技能中心运行时根据真实配置处理：
    # - AI不可用时任务运行会返回明确错误/跳过原因；
    # - 通知无渠道时任务运行会标记 skipped_no_channel。
    # 保留该函数作为旧调用点的兼容验证入口。
    return None
