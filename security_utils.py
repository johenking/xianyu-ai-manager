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
