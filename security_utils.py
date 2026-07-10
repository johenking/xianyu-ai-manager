"""Credential hashing, encryption, and token-digest helpers."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from pathlib import Path
import secrets
from typing import Optional

import bcrypt
from cryptography.fernet import Fernet, InvalidToken


PASSWORD_HASH_VERSION = 2
ACCOUNT_PASSWORD_ENCRYPTION_VERSION = 1
ACCOUNT_PASSWORD_PREFIX = "fernet:v1:"
SYSTEM_SECRET_ENCRYPTION_VERSION = 1
SYSTEM_SECRET_PREFIX = "fernet:system:v1:"


def hash_user_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("ascii")


def verify_user_password_hash(password: str, password_hash: str) -> bool:
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except (TypeError, ValueError):
        return False


def verify_legacy_sha256(password: str, password_hash: str) -> bool:
    candidate = hashlib.sha256(password.encode("utf-8")).hexdigest()
    return hmac.compare_digest(candidate, str(password_hash or ""))


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class AccountCredentialCipher:
    """Fernet wrapper using a key that is separate from AI provider secrets."""

    def __init__(self, db_path: str, secret: Optional[str] = None):
        self.db_path = Path(db_path)
        self.key_path = Path(
            os.getenv(
                "ACCOUNT_CREDENTIAL_KEY_FILE",
                str(self.db_path.parent / ".account_credential_key"),
            )
        )
        raw_secret = secret or os.getenv("ACCOUNT_CREDENTIAL_ENCRYPTION_KEY") or self._local_secret()
        key = base64.urlsafe_b64encode(hashlib.sha256(raw_secret.encode("utf-8")).digest())
        self.fernet = Fernet(key)

    def _local_secret(self) -> str:
        if self.key_path.exists():
            os.chmod(self.key_path, 0o600)
            return self.key_path.read_text(encoding="ascii").strip()

        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        secret = secrets.token_urlsafe(48)
        try:
            descriptor = os.open(str(self.key_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            os.chmod(self.key_path, 0o600)
            return self.key_path.read_text(encoding="ascii").strip()
        with os.fdopen(descriptor, "w", encoding="ascii") as handle:
            handle.write(secret)
        return secret

    def encrypt(self, value: str) -> str:
        if not value:
            return ""
        return ACCOUNT_PASSWORD_PREFIX + self.fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, value: str) -> str:
        if not value:
            return ""
        if not value.startswith(ACCOUNT_PASSWORD_PREFIX):
            return value
        try:
            token = value[len(ACCOUNT_PASSWORD_PREFIX):]
            return self.fernet.decrypt(token.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise ValueError("账号登录凭据无法解密，请重新保存登录密码") from exc


class SystemSecretCipher:
    """Encryption and purpose-isolated digests for server-side secrets."""

    def __init__(self, db_path: str, secret: Optional[str] = None):
        self.db_path = Path(db_path)
        self.key_path = Path(
            os.getenv(
                "SYSTEM_SECRET_KEY_FILE",
                str(self.db_path.parent / ".system_secret_key"),
            )
        )
        raw_secret = secret or os.getenv("SYSTEM_SECRET_ENCRYPTION_KEY") or self._local_secret()
        self._hmac_key = hashlib.sha256(
            b"xianyu-system-secret:hmac:v1\0" + raw_secret.encode("utf-8")
        ).digest()
        encryption_key = base64.urlsafe_b64encode(
            hashlib.sha256(
                b"xianyu-system-secret:fernet:v1\0" + raw_secret.encode("utf-8")
            ).digest()
        )
        self.fernet = Fernet(encryption_key)

    def _local_secret(self) -> str:
        if self.key_path.exists():
            os.chmod(self.key_path, 0o600)
            secret = self.key_path.read_text(encoding="ascii").strip()
            if not secret:
                raise ValueError("系统秘密密钥文件为空")
            return secret

        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        secret = secrets.token_urlsafe(48)
        try:
            descriptor = os.open(
                str(self.key_path),
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError:
            os.chmod(self.key_path, 0o600)
            existing_secret = self.key_path.read_text(encoding="ascii").strip()
            if not existing_secret:
                raise ValueError("系统秘密密钥文件为空")
            return existing_secret
        with os.fdopen(descriptor, "w", encoding="ascii") as handle:
            handle.write(secret)
        os.chmod(self.key_path, 0o600)
        return secret

    def encrypt(self, value: str) -> str:
        if not value or value.startswith(SYSTEM_SECRET_PREFIX):
            return value
        token = self.fernet.encrypt(value.encode("utf-8")).decode("ascii")
        return SYSTEM_SECRET_PREFIX + token

    def decrypt(self, value: str) -> str:
        if not value or not value.startswith(SYSTEM_SECRET_PREFIX):
            return value
        try:
            token = value[len(SYSTEM_SECRET_PREFIX):]
            return self.fernet.decrypt(token.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeError, ValueError) as exc:
            raise ValueError("系统秘密无法解密，请检查系统秘密密钥") from exc

    def digest(self, value: str, *, purpose: str) -> str:
        normalized_purpose = str(purpose or "").strip()
        if not normalized_purpose:
            raise ValueError("摘要 purpose 不能为空")
        purpose_key = hmac.new(
            self._hmac_key,
            normalized_purpose.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        return hmac.new(
            purpose_key,
            str(value).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def compare_digest(self, value: str, expected_digest: str, *, purpose: str) -> bool:
        candidate = self.digest(value, purpose=purpose)
        return hmac.compare_digest(candidate, str(expected_digest or ""))
