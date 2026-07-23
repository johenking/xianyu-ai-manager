"""Unified ownership and lifecycle for official Goofish browser login sessions."""

from __future__ import annotations

import asyncio
import inspect
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from account_session_refresh import (
    official_login_error_message,
    remove_verification_image,
)
from session_registry import get_session_registry, sanitize_runtime_error
from utils.xianyu_official_login import (
    OfficialLoginResult,
    OfficialLoginWorker,
    XianyuOfficialLoginService,
)


TERMINAL_STATES = {"success", "expired", "failed", "cancelled", "interrupted"}
CANCELLABLE_STATES = {"preparing", "waiting_user", "verification_required"}


@dataclass
class OfficialLoginSessionRecord:
    session_id: str
    owner_user_id: int
    mode: str
    account: str = ""
    expected_unb: str = ""
    show_browser: bool = False
    state: str = "preparing"
    message: str = "正在打开闲鱼官方登录页"
    error_code: str = ""
    image_path: str = ""
    account_id: str = ""
    is_new_account: bool = False
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    expires_at: float = field(default_factory=lambda: time.time() + 900)
    worker: Any = None
    task: Optional[asyncio.Task] = None
    expiry_task: Optional[asyncio.Task] = None
    cleanup_task: Optional[asyncio.Task] = None


CompletionHandler = Callable[
    [OfficialLoginSessionRecord, OfficialLoginResult, str, str],
    Awaitable[dict[str, Any]] | dict[str, Any] | None,
]


class OfficialLoginSessionCoordinator:
    def __init__(
        self,
        *,
        completion_handler: CompletionHandler,
        service_factory: Callable[[], Any] = XianyuOfficialLoginService,
        worker_factory: Callable[[], Any] = OfficialLoginWorker,
        registry: Any = None,
        session_ttl_seconds: float = 900.0,
        terminal_retention_seconds: float = 300.0,
    ) -> None:
        self.completion_handler = completion_handler
        self.service_factory = service_factory
        self.worker_factory = worker_factory
        self.registry = registry or get_session_registry()
        self.session_ttl_seconds = max(0.01, float(session_ttl_seconds))
        self.terminal_retention_seconds = max(0.01, float(terminal_retention_seconds))
        self._sessions: dict[str, OfficialLoginSessionRecord] = {}
        self._active_flights: dict[str, str] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _flight_key(owner_user_id: int, mode: str, account: str) -> str:
        account_key = account.strip().lower() if mode in {"password", "sms"} else "official-qr"
        return f"{owner_user_id}:{mode}:{account_key}"

    async def start(
        self,
        *,
        owner_user_id: int,
        mode: str,
        account: str = "",
        expected_unb: str = "",
        password: str = "",
        show_browser: bool = False,
    ) -> dict[str, Any]:
        if mode not in {"qr", "password", "sms"}:
            raise ValueError("登录方式仅支持 qr、password 或 sms")
        if mode == "password" and (not account.strip() or not password):
            raise ValueError("账号密码不能为空")

        normalized_account = account.strip()
        normalized_expected_unb = expected_unb.strip()
        flight_key = self._flight_key(owner_user_id, mode, normalized_account)
        async with self._lock:
            active_id = self._active_flights.get(flight_key)
            active = self._sessions.get(active_id or "")
            if (
                active is not None
                and active.state not in TERMINAL_STATES
                and active.expires_at > time.time()
            ):
                return self._safe_status(active)

            session_id = secrets.token_urlsafe(18)
            record = OfficialLoginSessionRecord(
                session_id=session_id,
                owner_user_id=owner_user_id,
                mode=mode,
                account=normalized_account,
                expected_unb=normalized_expected_unb,
                show_browser=True if mode == "sms" else show_browser,
                expires_at=time.time() + self.session_ttl_seconds,
                worker=self.worker_factory(),
            )
            self._sessions[session_id] = record
            self._active_flights[flight_key] = session_id
        self.registry.register(
            session_id,
            "official_login",
            owner_user_id,
            status="preparing",
            ttl_seconds=max(1, int(self.session_ttl_seconds)),
        )
        record.task = asyncio.create_task(
            self._run(
                record,
                password=password,
                show_browser=True if mode == "sms" else show_browser,
            ),
            name=f"official-login:{session_id}",
        )
        record.expiry_task = asyncio.create_task(
            self._expire(record),
            name=f"official-login-expiry:{session_id}",
        )
        return self._safe_status(record)

    async def _expire(self, record: OfficialLoginSessionRecord) -> None:
        try:
            await asyncio.sleep(max(0.0, record.expires_at - time.time()))
            if record.state not in CANCELLABLE_STATES:
                return
            self._set_state(
                record,
                "expired",
                "闲鱼官方登录会话已过期",
                error_code="session_expired",
            )
            await asyncio.to_thread(record.worker.close_browser)
        except asyncio.CancelledError:
            return

    async def _run(
        self,
        record: OfficialLoginSessionRecord,
        *,
        password: str,
        show_browser: bool,
    ) -> None:
        loop = asyncio.get_running_loop()
        completion_metadata: dict[str, Any] = {}
        completion_finished = False

        def on_status(result: OfficialLoginResult) -> None:
            loop.call_soon_threadsafe(self._apply_worker_status, record.session_id, result)

        def on_validated(result: OfficialLoginResult) -> bool:
            nonlocal completion_finished
            future = asyncio.run_coroutine_threadsafe(
                self._complete_validated_session(
                    record,
                    result,
                    account=record.account,
                    password=password,
                ),
                loop,
            )
            metadata = future.result(timeout=self.session_ttl_seconds)
            completion_metadata.update(metadata or {})
            completion_finished = True
            return True

        try:
            service = self.service_factory()
            if record.mode == "qr":
                result = await asyncio.to_thread(
                    service.login_with_qr,
                    show_browser=show_browser,
                    worker=record.worker,
                    on_status=on_status,
                    on_validated=on_validated,
                )
            elif record.mode == "password":
                result = await asyncio.to_thread(
                    service.login_with_password,
                    account=record.account,
                    password=password,
                    show_browser=show_browser,
                    worker=record.worker,
                    on_status=on_status,
                    on_validated=on_validated,
                )
            else:
                result = await asyncio.to_thread(
                    service.login_with_official_window,
                    account=record.account,
                    expected_unb=record.expected_unb,
                    timeout=self.session_ttl_seconds,
                    worker=record.worker,
                    on_status=on_status,
                    on_validated=on_validated,
                )

            if record.state in TERMINAL_STATES:
                return

            if result.succeeded:
                if completion_finished:
                    metadata = completion_metadata
                else:
                    metadata = await self._complete_validated_session(
                        record,
                        result,
                        account=record.account,
                        password=password,
                    )
                if record.state in TERMINAL_STATES:
                    return
                metadata = metadata or {}
                record.account_id = str(metadata.get("account_id") or result.unb)
                record.is_new_account = bool(metadata.get("is_new_account"))
                self._set_state(record, "success", "闲鱼官方登录成功")
                return

            state = result.status
            if state == "timeout":
                state = "expired"
            if state not in TERMINAL_STATES:
                state = "failed"
            self._set_state(
                record,
                state,
                official_login_error_message(
                    result.error_code,
                    fallback=(
                        "手机号验证码登录未完成，请重新发起"
                        if record.mode == "sms"
                        else "闲鱼官方登录未完成，请稍后重试"
                    ),
                ),
                error_code=result.error_code,
                image_path=result.verification_image_path,
            )
        except asyncio.CancelledError:
            if record.state not in TERMINAL_STATES:
                self._set_state(record, "cancelled", "登录会话已取消", error_code="cancelled")
            raise
        except Exception:
            if record.state not in TERMINAL_STATES:
                self._set_state(
                    record,
                    "failed",
                    "官方登录任务处理失败，请重新发起",
                    error_code="login_exception",
                )
        finally:
            flight_key = self._flight_key(record.owner_user_id, record.mode, record.account)
            async with self._lock:
                if self._active_flights.get(flight_key) == record.session_id:
                    self._active_flights.pop(flight_key, None)

    async def _complete_validated_session(
        self,
        record: OfficialLoginSessionRecord,
        result: OfficialLoginResult,
        *,
        account: str,
        password: str,
    ) -> dict[str, Any]:
        if record.state in TERMINAL_STATES:
            raise RuntimeError("官方登录会话已结束")
        self._set_state(record, "persisting", "正在保存账号登录状态")
        completion = self.completion_handler(record, result, account, password)
        metadata = await completion if inspect.isawaitable(completion) else completion
        metadata = metadata or {}
        record.account_id = str(metadata.get("account_id") or result.unb)
        record.is_new_account = bool(metadata.get("is_new_account"))
        return metadata

    def _apply_worker_status(self, session_id: str, result: OfficialLoginResult) -> None:
        record = self._sessions.get(session_id)
        if record is None or record.state in TERMINAL_STATES:
            return
        state = result.status
        if state not in {"waiting_user", "verification_required", "restarting_listener"}:
            return
        self._set_state(
            record,
            state,
            result.message,
            error_code=result.error_code,
            image_path=result.verification_image_path,
        )

    def _set_state(
        self,
        record: OfficialLoginSessionRecord,
        state: str,
        message: str,
        *,
        error_code: str = "",
        image_path: str = "",
    ) -> None:
        previous_image = record.image_path
        record.state = state
        record.message = sanitize_runtime_error(message)
        record.error_code = str(error_code or "")[:80]
        record.updated_at = time.time()
        if image_path:
            record.image_path = image_path
        if previous_image and previous_image != record.image_path:
            remove_verification_image(previous_image)
        if state in TERMINAL_STATES and record.image_path:
            remove_verification_image(record.image_path)
            record.image_path = ""
        if state in TERMINAL_STATES:
            if record.expiry_task and record.expiry_task is not asyncio.current_task():
                record.expiry_task.cancel()
            if record.cleanup_task is None:
                record.cleanup_task = asyncio.create_task(
                    self._forget_terminal_session(record),
                    name=f"official-login-cleanup:{record.session_id}",
                )
        self.registry.update(
            record.session_id,
            status=state,
            error_code=record.error_code,
            error_message=record.message if state in TERMINAL_STATES else "",
            ttl_seconds=max(1, int(record.expires_at - time.time())),
        )

    async def _forget_terminal_session(self, record: OfficialLoginSessionRecord) -> None:
        try:
            await asyncio.sleep(self.terminal_retention_seconds)
            async with self._lock:
                if self._sessions.get(record.session_id) is record:
                    self._sessions.pop(record.session_id, None)
        except asyncio.CancelledError:
            return

    async def get_status(self, session_id: str, owner_user_id: int) -> Optional[dict[str, Any]]:
        record = self._sessions.get(session_id)
        if record is None or record.owner_user_id != owner_user_id:
            return None
        return self._safe_status(record)

    async def wait_until_ready(
        self,
        session_id: str,
        owner_user_id: int,
        *,
        timeout: float,
    ) -> Optional[dict[str, Any]]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = await self.get_status(session_id, owner_user_id)
            if status is None or status["state"] != "preparing":
                return status
            await asyncio.sleep(0.02)
        return await self.get_status(session_id, owner_user_id)

    async def wait_for_terminal(
        self,
        session_id: str,
        owner_user_id: int,
        *,
        timeout: float,
    ) -> Optional[dict[str, Any]]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = await self.get_status(session_id, owner_user_id)
            if status is None or status["state"] in TERMINAL_STATES:
                return status
            await asyncio.sleep(0.02)
        return await self.get_status(session_id, owner_user_id)

    async def show_browser(self, session_id: str, owner_user_id: int) -> bool:
        record = self._sessions.get(session_id)
        if record is None or record.owner_user_id != owner_user_id or record.state in TERMINAL_STATES:
            return False
        request_visible = getattr(record.worker, "request_visible", None)
        if not callable(request_visible):
            return False
        await asyncio.to_thread(request_visible)
        return True

    def mark_restarting_listener(self, record: OfficialLoginSessionRecord) -> None:
        if record.state not in TERMINAL_STATES:
            self._set_state(record, "restarting_listener", "正在恢复账号监听")

    async def cancel(self, session_id: str, owner_user_id: int) -> bool:
        record = self._sessions.get(session_id)
        if (
            record is None
            or record.owner_user_id != owner_user_id
            or record.state not in CANCELLABLE_STATES
        ):
            return False
        self._set_state(record, "cancelled", "登录会话已取消", error_code="cancelled")
        await asyncio.to_thread(record.worker.close_browser)
        if record.task and record.task is not asyncio.current_task():
            await asyncio.wait({record.task}, timeout=2.0)
        return True

    @staticmethod
    def _public_image_path(path: str) -> str:
        normalized = str(path or "").lstrip("/")
        if normalized.startswith("static/uploads/images/"):
            return f"/{normalized}"
        return ""

    def _safe_status(self, record: OfficialLoginSessionRecord) -> dict[str, Any]:
        image_url = self._public_image_path(record.image_path)
        return {
            "session_id": record.session_id,
            "mode": record.mode,
            "state": record.state,
            "message": sanitize_runtime_error(record.message),
            "error_code": record.error_code,
            "qr_image_url": image_url if record.state == "waiting_user" else "",
            "verification_image_url": image_url if record.state == "verification_required" else "",
            "account_id": record.account_id,
            "is_new_account": record.is_new_account,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "expires_at": record.expires_at,
        }
