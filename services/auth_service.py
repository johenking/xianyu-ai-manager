"""Backend-user authentication rules."""

from __future__ import annotations

from repositories.auth_repository import UserRepository
from security_utils import (
    PASSWORD_HASH_VERSION,
    hash_user_password,
    verify_legacy_sha256,
    verify_user_password_hash,
)


class AuthService:
    def __init__(self, users: UserRepository):
        self.users = users

    def verify_password(self, username: str, password: str) -> bool:
        user = self.users.get_by_username(username)
        if not user or not user["is_active"]:
            return False
        if user["password_hash_v2"]:
            return verify_user_password_hash(password, user["password_hash_v2"])
        if not verify_legacy_sha256(password, user["password_hash"]):
            return False
        self.users.upgrade_password(
            user["id"],
            hash_user_password(password),
            PASSWORD_HASH_VERSION,
        )
        self.users.connection.commit()
        return True

