"""Backend-user authentication rules."""

from __future__ import annotations

import threading

from auth_registration_service import RegistrationError
from repositories.auth_repository import (
    AuthSessionRepository,
    UserRepository,
    public_user_view,
)
from security_utils import (
    PASSWORD_HASH_VERSION,
    hash_user_password,
    verify_legacy_sha256,
    verify_user_password_hash,
)


class AuthService:
    def __init__(
        self,
        users: UserRepository,
        sessions: AuthSessionRepository | None = None,
        lock: threading.RLock | None = None,
    ):
        self.users = users
        self.sessions = sessions or AuthSessionRepository(users.connection)
        self.lock = lock or threading.RLock()

    def verify_password(self, username: str, password: str) -> bool:
        connection = self.users.connection
        with self.lock:
            user = self.users.get_by_identifier(username)
            if not user or not user["is_active"]:
                return False
            if user["password_hash_v2"]:
                return verify_user_password_hash(password, user["password_hash_v2"])
            legacy_hash = user["password_hash"]
            if not verify_legacy_sha256(password, legacy_hash):
                return False

            owned_transaction = not connection.in_transaction
            try:
                updated = self.users.upgrade_password(
                    user["id"],
                    hash_user_password(password),
                    PASSWORD_HASH_VERSION,
                    expected_legacy_hash=legacy_hash,
                )
                if updated == 1:
                    if owned_transaction:
                        connection.commit()
                    return True
                if owned_transaction:
                    connection.rollback()
            except Exception:
                if owned_transaction:
                    connection.rollback()
                raise

            current = self.users.get_by_id(user["id"])
            return bool(
                current
                and current["is_active"]
                and current["password_hash_v2"]
                and verify_user_password_hash(
                    password,
                    current["password_hash_v2"],
                )
            )

    def set_user_active(self, user_id: int, is_active: bool) -> dict:
        connection = self.users.connection
        with self.lock:
            connection.execute("BEGIN IMMEDIATE")
            try:
                user = self.users.get_by_id(user_id)
                if user is None:
                    raise RegistrationError("USER_NOT_FOUND", "用户不存在")
                if not is_active and user["username_normalized"] == "admin":
                    raise RegistrationError(
                        "ADMIN_DEACTIVATION_FORBIDDEN",
                        "管理员账号不能停用",
                    )
                if self.users.set_active(user_id, is_active) != 1:
                    raise RegistrationError("USER_UPDATE_FAILED", "用户状态更新失败")
                if not is_active:
                    self.sessions.delete_by_user_id(user_id)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            updated = self.users.get_by_id(user_id)
        if updated is None:
            raise RegistrationError("USER_NOT_FOUND", "用户不存在")
        return public_user_view(updated)

    set_active = set_user_active
