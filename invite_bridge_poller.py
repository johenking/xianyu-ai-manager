"""Opt-in poller that turns trusted pending_ship rows into invite events."""

from __future__ import annotations

import asyncio
import hashlib
import os
import time
from typing import Any, Dict

from loguru import logger

from db_manager import db_manager
from invite_bridge import _allowed_item_ids, _send_order_event_to_invite, bridge_enabled
from order_sync_service import (
    XianyuOrderListClient,
    get_order_sync_lock,
    parse_amount_fen,
    parse_order_time_utc,
)
from session_registry import sanitize_runtime_error


def _exception_summary(exc: BaseException) -> str:
    """Keep bridge failures actionable without exposing payloads or credentials."""
    status = getattr(exc, "status_code", None)
    detail = getattr(exc, "detail", None)
    detail = sanitize_runtime_error(detail if detail is not None else str(exc))
    detail = " ".join(detail.split())[:160]
    if status is not None:
        return f"status={status} detail={detail or 'unknown'}"
    return f"type={type(exc).__name__} detail={detail or 'unknown'}"


def _message_operation_exists(order_id: str, cookie_id: str) -> bool:
    """Return whether the invite service already called back for this order."""
    with db_manager.lock:
        row = db_manager.conn.execute(
            "SELECT 1 FROM invite_bridge_operations "
            "WHERE order_id = ? AND cookie_id = ? AND operation_type = 'message' "
            "LIMIT 1",
            (order_id, cookie_id),
        ).fetchone()
    return row is not None


class InviteBridgePoller:
    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._seen: set[str] = set()
        self._last_discovery_at: dict[str, float] = {}
        self._scan_lock = asyncio.Lock()

    async def start(self) -> None:
        if not bridge_enabled() or not os.getenv("XIANYU_INVITE_BASE_URL", "").strip():
            return
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="xianyu-invite-bridge")
        logger.info("邀请桥付款订单轮询已启动")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        interval = max(3.0, float(os.getenv("XIANYU_INVITE_POLL_INTERVAL_SECONDS", "10")))
        while not self._stop.is_set():
            try:
                await self.scan_once()
            except Exception as exc:
                logger.warning("邀请桥订单轮询失败: {}", _exception_summary(exc))
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                continue

    @staticmethod
    def _chat_reference(cookie_id: str, order_id: str, buyer_id: str, supplied: str = "") -> str:
        supplied = str(supplied or "").strip()
        if supplied:
            return supplied
        existing = db_manager.find_chat_id_by_buyer(cookie_id, buyer_id)
        return str(existing or f"direct:{order_id}")

    def stage_order(
        self,
        *,
        cookie_id: str,
        order_id: str,
        item_id: str,
        buyer_id: str,
        amount: Any = None,
        quantity: Any = None,
        item_title: str = "",
        created_at: Any = None,
        chat_id: str = "",
        is_bargain: bool = False,
    ) -> bool:
        """Persist one verified invite order without touching legacy card stock."""
        if item_id not in _allowed_item_ids(cookie_id):
            return False
        existing = db_manager.get_order_by_id(order_id)
        if existing and (
            str(existing.get("order_status") or existing.get("status") or "")
            in {"shipped", "completed"}
            or bool(existing.get("system_shipped"))
        ):
            return False
        supplied_chat = str(chat_id or "").strip()
        stored_chat = str((existing or {}).get("chat_id") or "").strip()
        # A verified IM cid is stronger than a discovery-time direct:* fallback.
        # Keep it across repeated platform discovery writes.
        if stored_chat and (
            not supplied_chat or supplied_chat.startswith("direct:")
        ):
            supplied_chat = stored_chat
        resolved_chat = self._chat_reference(
            cookie_id, order_id, buyer_id, supplied_chat
        )
        values: Dict[str, Any] = {
            "order_id": order_id,
            "item_id": item_id,
            "buyer_id": buyer_id,
            "quantity": quantity,
            "amount": amount,
            "order_status": "pending_ship",
            "cookie_id": cookie_id,
            "created_at": created_at,
            "chat_id": resolved_chat,
        }
        if item_title:
            values["spec_value"] = item_title
        if is_bargain:
            values["is_bargain"] = True
        stored = bool(db_manager.insert_or_update_order(**values))
        if not stored:
            return False
        db_manager.apply_order_sync_update(
            order_id=order_id,
            cookie_id=cookie_id,
            incoming_status="pending_ship",
            status_source="order_list",
            ordered_at=parse_order_time_utc(created_at),
            paid_amount_fen=parse_amount_fen(amount),
        )
        return True

    async def _discover_platform_orders(self) -> None:
        """Reconcile platform truth before scanning local pending_ship rows."""
        now = time.time()
        discovery_interval = max(
            15.0,
            float(os.getenv("XIANYU_INVITE_DISCOVERY_INTERVAL_SECONDS", "30")),
        )
        accounts = db_manager.get_all_cookies()
        for cookie_id, cookie_string in accounts.items():
            cookie_id = str(cookie_id)
            if not _allowed_item_ids(cookie_id):
                continue
            if now - self._last_discovery_at.get(cookie_id, 0.0) < discovery_interval:
                continue
            self._last_discovery_at[cookie_id] = now
            details = db_manager.get_cookie_details(cookie_id) or {}
            client = XianyuOrderListClient(
                max_pages=5,
                max_orders=100,
                request_interval=0.2,
            )
            try:
                async with get_order_sync_lock(cookie_id):
                    discovery = await client.discover(
                        cookie_id=cookie_id,
                        cookie_string=str(cookie_string or ""),
                        days=7,
                        user_agent=str(details.get("browser_user_agent") or ""),
                    )
                if not discovery.get("success"):
                    logger.warning(
                        "邀请桥平台订单发现失败: error_code={}",
                        discovery.get("error_code") or "unknown",
                    )
                    continue
                staged = 0
                for discovered in discovery.get("orders") or []:
                    item_id = str(discovered.get("item_id") or "")
                    order_id = str(discovered.get("order_id") or "")
                    buyer_id = str(discovered.get("buyer_id") or "")
                    discovered_status = str(discovered.get("order_status") or "")
                    if (
                        not order_id
                        or not buyer_id
                        or item_id not in _allowed_item_ids(cookie_id)
                    ):
                        continue
                    if discovered_status != "pending_ship":
                        local_order = db_manager.get_order_by_id(order_id)
                        local_status = str(
                            (local_order or {}).get("order_status") or ""
                        )
                        if (
                            local_order
                            and discovered_status in {"shipped", "completed", "cancelled", "refunded"}
                            and local_status in {"pending_ship", discovered_status}
                        ):
                            db_manager.apply_order_sync_update(
                                order_id=order_id,
                                cookie_id=cookie_id,
                                incoming_status=discovered_status,
                                platform_status_code=str(discovered.get("platform_status_code") or ""),
                                platform_status_text=str(discovered.get("platform_status_text") or ""),
                                status_source="order_list",
                                ordered_at=parse_order_time_utc(
                                    discovered.get("created_at")
                                ),
                                paid_amount_fen=parse_amount_fen(
                                    discovered.get("amount")
                                ),
                            )
                        continue
                    if self.stage_order(
                        cookie_id=cookie_id,
                        order_id=order_id,
                        item_id=item_id,
                        buyer_id=buyer_id,
                        amount=discovered.get("amount"),
                        quantity=discovered.get("quantity") or "1",
                        item_title=str(discovered.get("item_title") or ""),
                        created_at=discovered.get("created_at"),
                        is_bargain=bool(discovered.get("is_bargain")),
                    ):
                        staged += 1
                if staged:
                    logger.info(
                        "邀请桥平台订单已补入本地: staged={}",
                        staged,
                    )
            except Exception as exc:
                logger.warning(
                    "邀请桥平台订单发现异常: {}",
                    _exception_summary(exc),
                )

    async def _scan_once_unlocked(self) -> int:
        sent = 0
        await self._discover_platform_orders()
        for cookie_id in db_manager.get_all_cookies():
            allowed_item_ids = _allowed_item_ids(str(cookie_id))
            for candidate in db_manager.get_orders_by_cookie(cookie_id, limit=200):
                candidate_status = str(
                    candidate.get("order_status") or candidate.get("status") or ""
                )
                if candidate_status not in {"pending_ship", "shipped", "completed"}:
                    continue
                order_id = str(candidate.get("order_id") or "")
                if not order_id:
                    continue
                detail = db_manager.get_order_by_id(order_id)
                if not detail:
                    continue
                item_id = str(detail.get("item_id") or "")
                if item_id not in allowed_item_ids:
                    continue
                if candidate_status in {"shipped", "completed"}:
                    parsed_time = parse_order_time_utc(detail.get("created_at"))
                    order_epoch = detail.get("ordered_at_utc") or parsed_time[0]
                    if order_epoch is None or float(order_epoch) < time.time() - 7 * 86400:
                        continue
                    paid_amount_fen = (
                        parse_amount_fen(detail.get("amount"))
                        if detail.get("paid_amount_fen") is None
                        else None
                    )
                    ordered_at = (
                        parsed_time if detail.get("ordered_at_utc") is None else None
                    )
                    if paid_amount_fen is not None or (
                        ordered_at is not None and ordered_at[0] is not None
                    ):
                        db_manager.apply_order_sync_update(
                            order_id=order_id,
                            cookie_id=str(cookie_id),
                            incoming_status=candidate_status,
                            status_source=str(
                                detail.get("status_source") or "order_list"
                            ),
                            ordered_at=ordered_at,
                            paid_amount_fen=paid_amount_fen,
                        )
                    continue
                event_id = "xianyu:" + hashlib.sha256(
                    f"{cookie_id}:{order_id}:paid".encode("utf-8")
                ).hexdigest()
                if event_id in self._seen:
                    continue
                if not detail or detail.get("system_shipped"):
                    continue
                buyer_id = str(detail.get("buyer_id") or "")
                chat_id = self._chat_reference(
                    str(cookie_id),
                    order_id,
                    buyer_id,
                    str(detail.get("chat_id") or ""),
                )
                if not buyer_id or not chat_id:
                    continue
                from XianyuAutoAsync import XianyuLive
                try:
                    if _message_operation_exists(order_id, str(cookie_id)):
                        self._seen.add(event_id)
                        order_ref = hashlib.sha256(
                            order_id.encode("utf-8")
                        ).hexdigest()[:12]
                        logger.info(
                            "邀请桥已有下游消息操作，跳过订单事件重投: order_ref={}",
                            order_ref,
                        )
                        continue
                    live_instance = XianyuLive.get_instance(str(cookie_id))
                    if not live_instance or not live_instance.ws or live_instance.ws.closed:
                        continue
                    payment_check = await live_instance._verify_paid_order_for_delivery(
                        order_id=order_id,
                        item_id=item_id,
                        buyer_id=buyer_id,
                    )
                    if not payment_check.get("allowed"):
                        continue
                    amount_cents = parse_amount_fen(detail.get("amount"))
                    if amount_cents is None:
                        continue
                    payload: Dict[str, Any] = {
                        "schemaVersion": "1",
                        "eventId": event_id,
                        "orderId": order_id,
                        "cookieId": str(cookie_id),
                        "chatId": chat_id,
                        "toUserId": buyer_id,
                        "itemId": item_id,
                        "sku": item_id,
                        "productName": str(detail.get("item_title") or detail.get("spec_value") or "Codex invitation"),
                        "amountCents": int(amount_cents),
                        "quantity": max(1, int(detail.get("quantity") or 1)),
                        "platformStatus": "pending_ship",
                        "observedAt": str(detail.get("status_synced_at") or detail.get("updated_at") or ""),
                    }
                    await _send_order_event_to_invite(payload)
                    self._seen.add(event_id)
                    sent += 1
                except Exception as exc:
                    order_ref = hashlib.sha256(order_id.encode("utf-8")).hexdigest()[:12]
                    logger.warning(
                        "邀请桥单笔订单处理失败: order_ref={} {}",
                        order_ref,
                        _exception_summary(exc),
                    )
        if len(self._seen) > 10_000:
            self._seen = set(list(self._seen)[-5_000:])
        return sent

    async def scan_once(self) -> int:
        async with self._scan_lock:
            return await self._scan_once_unlocked()


invite_bridge_poller = InviteBridgePoller()
