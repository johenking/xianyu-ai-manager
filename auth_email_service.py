"""Fail-closed SMTP delivery and registration readiness checks."""

from __future__ import annotations

from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr
import hmac
import json
import re
import smtplib
import ssl
from typing import Any, Mapping

from security_utils import SystemSecretCipher


SMTP_CONFIGURATION_KEYS = frozenset(
    {
        "smtp_server",
        "smtp_port",
        "smtp_user",
        "smtp_password",
        "smtp_from",
        "smtp_use_tls",
        "smtp_use_ssl",
        "support_email",
    }
)
SMTP_FINGERPRINT_PURPOSE = "smtp-configuration-fingerprint"
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class SMTPConfigurationError(ValueError):
    """SMTP settings are incomplete or internally inconsistent."""


class SMTPDeliveryError(RuntimeError):
    """SMTP delivery failed without exposing provider or recipient details."""


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


def _clean_header(value: Any, *, label: str, required: bool = False) -> str:
    result = str(value or "").strip()
    if required and not result:
        raise SMTPConfigurationError(f"{label}未配置")
    if "\r" in result or "\n" in result:
        raise SMTPConfigurationError(f"{label}格式无效")
    return result


def _clean_email(value: Any, *, label: str, required: bool = True) -> str:
    result = _clean_header(value, label=label, required=required)
    if result and not _EMAIL_PATTERN.fullmatch(result):
        raise SMTPConfigurationError(f"{label}格式无效")
    return result


def canonical_smtp_setting_value(key: str, value: Any) -> str:
    if key in {"smtp_use_tls", "smtp_use_ssl"}:
        return "true" if _as_bool(value) else "false"
    if key == "smtp_port":
        try:
            return str(int(value))
        except (TypeError, ValueError):
            return str(value or "").strip()
    result = str(value if value is not None else "")
    if key in {"smtp_server", "smtp_user", "support_email"}:
        return result.strip().casefold()
    if key == "smtp_from":
        return result.strip()
    return result


@dataclass(frozen=True)
class SMTPConfiguration:
    server: str
    port: int
    username: str
    password: str
    from_name: str
    use_tls: bool
    use_ssl: bool

    @classmethod
    def from_settings(cls, settings: Mapping[str, Any]) -> "SMTPConfiguration":
        server = _clean_header(
            settings.get("smtp_server"),
            label="SMTP服务器",
            required=True,
        )
        username = _clean_email(settings.get("smtp_user"), label="发件邮箱")
        password = str(settings.get("smtp_password") or "")
        if not password:
            raise SMTPConfigurationError("SMTP授权码未配置")
        try:
            port = int(settings.get("smtp_port") or 587)
        except (TypeError, ValueError):
            raise SMTPConfigurationError("SMTP端口无效") from None
        if not 1 <= port <= 65535:
            raise SMTPConfigurationError("SMTP端口无效")
        use_tls = _as_bool(settings.get("smtp_use_tls", True))
        use_ssl = _as_bool(settings.get("smtp_use_ssl", False))
        if use_tls and use_ssl:
            raise SMTPConfigurationError("STARTTLS 与 SSL 不能同时启用")
        from_name = _clean_header(
            settings.get("smtp_from"),
            label="发件人名称",
        )
        return cls(
            server=server,
            port=port,
            username=username,
            password=password,
            from_name=from_name,
            use_tls=use_tls,
            use_ssl=use_ssl,
        )

    def fingerprint_payload(self, support_email: str = "") -> str:
        payload = {
            "server": self.server.casefold(),
            "port": self.port,
            "username": self.username.casefold(),
            "password": self.password,
            "from_name": self.from_name,
            "use_tls": self.use_tls,
            "use_ssl": self.use_ssl,
            "support_email": str(support_email or "").strip().casefold(),
        }
        return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


class SMTPEmailSender:
    def __init__(self, *, timeout_seconds: int = 12) -> None:
        self.timeout_seconds = timeout_seconds

    def send(
        self,
        settings: Mapping[str, Any],
        *,
        recipient: str,
        subject: str,
        text: str,
    ) -> None:
        configuration = SMTPConfiguration.from_settings(settings)
        clean_recipient = _clean_email(recipient, label="收件邮箱")
        clean_subject = _clean_header(subject, label="邮件主题", required=True)

        message = EmailMessage()
        message["Subject"] = clean_subject
        message["From"] = formataddr(
            (configuration.from_name, configuration.username)
        )
        message["To"] = clean_recipient
        message.set_content(str(text), charset="utf-8")

        connection = None
        try:
            if configuration.use_ssl:
                connection = smtplib.SMTP_SSL(
                    configuration.server,
                    configuration.port,
                    timeout=self.timeout_seconds,
                    context=ssl.create_default_context(),
                )
            else:
                connection = smtplib.SMTP(
                    configuration.server,
                    configuration.port,
                    timeout=self.timeout_seconds,
                )
            connection.ehlo()
            if configuration.use_tls:
                connection.starttls(context=ssl.create_default_context())
                connection.ehlo()
            connection.login(configuration.username, configuration.password)
            connection.send_message(message)
        except Exception as exc:
            raise SMTPDeliveryError("SMTP 邮件发送失败，请检查配置后重试") from exc
        finally:
            if connection is not None:
                try:
                    connection.quit()
                except Exception:
                    pass


def smtp_configuration_fingerprint(
    settings: Mapping[str, Any],
    *,
    db_path: str,
) -> str:
    configuration = SMTPConfiguration.from_settings(settings)
    support_email = str(settings.get("support_email") or "").strip()
    if support_email:
        support_email = _clean_email(
            support_email,
            label="支持邮箱",
        ).casefold()
    return SystemSecretCipher(db_path).digest(
        configuration.fingerprint_payload(support_email),
        purpose=SMTP_FINGERPRINT_PURPOSE,
    )


def smtp_configuration_status(
    settings: Mapping[str, Any],
    *,
    db_path: str,
) -> dict[str, bool]:
    try:
        fingerprint = smtp_configuration_fingerprint(settings, db_path=db_path)
    except SMTPConfigurationError:
        return {"smtp_configured": False, "smtp_verified": False}
    stored = str(settings.get("smtp_verified_fingerprint") or "")
    return {
        "smtp_configured": True,
        "smtp_verified": bool(stored and hmac.compare_digest(fingerprint, stored)),
    }


def registration_readiness(
    settings: Mapping[str, Any],
    *,
    db_path: str,
    user_count: int,
) -> dict[str, Any]:
    smtp_status = smtp_configuration_status(settings, db_path=db_path)
    terms_version = str(settings.get("terms_version") or "").strip()
    requested = _as_bool(settings.get("registration_enabled"))
    try:
        user_limit = int(settings.get("registration_user_limit") or 0)
    except (TypeError, ValueError):
        user_limit = 0
    valid_limit = 1 <= user_limit <= 1000
    normalized_user_count = max(0, int(user_count))
    remaining_slots = max(0, user_limit - normalized_user_count) if valid_limit else 0
    try:
        support_email_valid = bool(
            _clean_email(settings.get("support_email"), label="支持邮箱")
        )
    except SMTPConfigurationError:
        support_email_valid = False
    ready = bool(
        smtp_status["smtp_verified"]
        and support_email_valid
        and valid_limit
        and remaining_slots > 0
        and terms_version == "v2"
    )
    return {
        "enabled": requested and ready,
        "ready": ready,
        "requested": requested,
        "invite_required": False,
        "terms_version": terms_version,
        "user_limit": user_limit,
        "user_count": normalized_user_count,
        "remaining_slots": remaining_slots,
        **smtp_status,
    }
