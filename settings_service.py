from typing import Any, Dict


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
    },
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
        elif key != "admin_password_hash":
            result[key] = value

    for key in SECRET_SETTING_KEYS:
        result.setdefault(f"{key}_configured", False)
        result.setdefault(f"{key}_masked", "")
    return result


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
    if notify_enabled:
        raise ValueError("通知发送暂不可用，请关闭通知后重试")
    if str(ai_filter or "").strip():
        raise ValueError("AI筛选暂不可用，请清空AI筛选条件后重试")
