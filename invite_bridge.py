"""Private HMAC bridge between the Xianyu monitor and the invite service.

The bridge deliberately keeps Xianyu cookies and WebSocket sessions on this
side.  The invite service receives only order context and fulfillment results.
It is disabled unless the operator opts in with ``XIANYU_INVITE_BRIDGE_ENABLED``.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any, Dict, Optional, Tuple

import aiohttp
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from db_manager import db_manager


invite_bridge_router = APIRouter(tags=["invite-bridge"])
_seen_nonces: Dict[str, float] = {}


def _truthy(value: str) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def bridge_enabled() -> bool:
    return _truthy(os.getenv("XIANYU_INVITE_BRIDGE_ENABLED", "false"))


def _bridge_secret() -> str:
    return os.getenv("XIANYU_INVITE_BRIDGE_SECRET", "").strip()


def _allowed_item_ids(cookie_id: str | None = None) -> set[str]:
    return db_manager.get_invite_auto_fulfillment_item_ids(cookie_id)


def _invite_item_enabled(cookie_id: str, item_id: str) -> bool:
    return db_manager.is_invite_auto_fulfillment_enabled(cookie_id, item_id)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _signature_headers(body: Dict[str, Any], secret: str, operation_key: str = "") -> Dict[str, str]:
    timestamp = str(int(time.time()))
    nonce = secrets.token_hex(16)
    digest = hmac.new(
        secret.encode("utf-8"),
        f"{timestamp}.{nonce}.{_canonical(body)}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {
        "accept": "application/json",
        "content-type": "application/json",
        "X-Bridge-Timestamp": timestamp,
        "X-Bridge-Nonce": nonce,
        "X-Bridge-Signature": f"sha256={digest}",
        **({"Idempotency-Key": operation_key} if operation_key else {}),
    }


async def _verify_request(request: Request, body: Dict[str, Any]) -> None:
    if not bridge_enabled() or not _bridge_secret():
        raise HTTPException(status_code=404, detail="invite bridge disabled")
    timestamp = request.headers.get("x-bridge-timestamp", "")
    nonce = request.headers.get("x-bridge-nonce", "")
    supplied = request.headers.get("x-bridge-signature", "").removeprefix("sha256=")
    try:
        timestamp_value = int(timestamp)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="invalid bridge timestamp") from exc
    if not nonce or abs(time.time() - timestamp_value) > 300:
        raise HTTPException(status_code=401, detail="stale bridge request")
    now = time.time()
    for key, expires_at in list(_seen_nonces.items()):
        if expires_at <= now:
            _seen_nonces.pop(key, None)
    if nonce in _seen_nonces:
        raise HTTPException(status_code=401, detail="replayed bridge request")
    expected = hmac.new(
        _bridge_secret().encode("utf-8"),
        f"{timestamp}.{nonce}.{_canonical(body)}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="invalid bridge signature")
    _seen_nonces[nonce] = now + 300


class BridgeOrderEvent(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    schema_version: str = Field(default="1", alias="schemaVersion")
    event_id: str = Field(alias="eventId", min_length=8, max_length=200)
    order_id: str = Field(alias="orderId", min_length=1, max_length=200)
    cookie_id: str = Field(alias="cookieId", min_length=1, max_length=200)
    chat_id: str = Field(default="", alias="chatId", max_length=200)
    to_user_id: str = Field(alias="toUserId", min_length=1, max_length=200)
    item_id: str = Field(alias="itemId", min_length=1, max_length=200)
    sku: str = Field(default="", max_length=200)
    product_name: str = Field(default="Codex invitation", alias="productName", max_length=300)
    amount_cents: int = Field(alias="amountCents", ge=0, le=10_000_000)
    quantity: int = Field(default=1, ge=1, le=100)
    platform_status: str = Field(default="pending_ship", alias="platformStatus", max_length=80)
    observed_at: str = Field(default="", alias="observedAt", max_length=80)


class SendMessageRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    operation_key: str = Field(alias="operationKey", min_length=8, max_length=200)
    order_id: str = Field(alias="orderId", min_length=1, max_length=200)
    cookie_id: str = Field(alias="cookieId", min_length=1, max_length=200)
    chat_id: str = Field(alias="chatId", min_length=1, max_length=200)
    to_user_id: str = Field(alias="toUserId", min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=20_000)
    request_id: str = Field(default="", alias="requestId", max_length=200)


class MarkFulfilledRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    operation_key: str = Field(alias="operationKey", min_length=8, max_length=200)
    order_id: str = Field(alias="orderId", min_length=1, max_length=200)
    cookie_id: str = Field(alias="cookieId", min_length=1, max_length=200)
    item_id: str = Field(alias="itemId", min_length=1, max_length=200)
    request_id: str = Field(default="", alias="requestId", max_length=200)


def _operation_response(row: Dict[str, Any]) -> Dict[str, Any]:
    try:
        response = json.loads(row.get("response_json") or "{}")
    except (TypeError, ValueError):
        response = {}
    return {
        "operationKey": row["operation_key"],
        "operationType": row["operation_type"],
        "orderId": row["order_id"],
        "status": row["status"],
        "state": row["status"],
        "providerRef": row.get("provider_ref") or None,
        "attempts": int(row.get("attempts") or 0),
        "lastError": row.get("last_error") or None,
        **response,
    }


def _load_operation(operation_key: str) -> Optional[Dict[str, Any]]:
    with db_manager.lock:
        row = db_manager.conn.execute(
            "SELECT operation_key,operation_type,order_id,cookie_id,request_hash,status,provider_ref,response_json,last_error,attempts,created_at,updated_at "
            "FROM invite_bridge_operations WHERE operation_key = ?",
            (operation_key,),
        ).fetchone()
        if not row:
            return None
        columns = [description[0] for description in db_manager.conn.execute("SELECT operation_key,operation_type,order_id,cookie_id,request_hash,status,provider_ref,response_json,last_error,attempts,created_at,updated_at FROM invite_bridge_operations LIMIT 0").description]
        return dict(zip(columns, row))


def _begin_operation(operation_key: str, operation_type: str, order_id: str, cookie_id: str, request_body: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
    request_hash = hashlib.sha256(_canonical(request_body).encode("utf-8")).hexdigest()
    now = time.time()
    with db_manager.lock:
        existing = _load_operation(operation_key)
        if existing:
            if existing["request_hash"] != request_hash or existing["operation_type"] != operation_type:
                raise HTTPException(status_code=409, detail="operation key was reused with different data")
            return existing, False
        db_manager.conn.execute(
            "INSERT INTO invite_bridge_operations(operation_key,operation_type,order_id,cookie_id,request_hash,status,response_json,attempts,created_at,updated_at) VALUES (?, ?, ?, ?, ?, 'pending', '{}', 0, ?, ?)",
            (operation_key, operation_type, order_id, cookie_id, request_hash, now, now),
        )
        db_manager.conn.commit()
        created = _load_operation(operation_key)
        if not created:
            raise HTTPException(status_code=503, detail="operation ledger unavailable")
        return created, True


def _set_operation(operation_key: str, status: str, provider_ref: str = "", response: Optional[Dict[str, Any]] = None, error: str = "") -> Dict[str, Any]:
    now = time.time()
    with db_manager.lock:
        db_manager.conn.execute(
            "UPDATE invite_bridge_operations SET status = ?, provider_ref = ?, response_json = ?, last_error = ?, updated_at = ?, attempts = attempts + 1 WHERE operation_key = ?",
            (status, provider_ref, _canonical(response or {}), error[:500], now, operation_key),
        )
        db_manager.conn.commit()
    row = _load_operation(operation_key)
    if not row:
        raise HTTPException(status_code=503, detail="operation ledger unavailable")
    return _operation_response(row)


def _order_or_error(order_id: str, cookie_id: str, item_id: str = "") -> Dict[str, Any]:
    order = db_manager.get_order_by_id(order_id)
    if not order or str(order.get("cookie_id") or "") != cookie_id:
        raise HTTPException(status_code=404, detail="order context not found")
    if not _invite_item_enabled(cookie_id, str(order.get("item_id") or "")):
        raise HTTPException(status_code=409, detail="order item is not enabled for invite bridge")
    if item_id and str(order.get("item_id") or "") != item_id:
        raise HTTPException(status_code=409, detail="order item mismatch")
    return order


async def _send_order_event_to_invite(payload: Dict[str, Any]) -> Dict[str, Any]:
    base_url = os.getenv("XIANYU_INVITE_BASE_URL", "").strip()
    secret = _bridge_secret()
    if not base_url or not secret:
        raise HTTPException(status_code=503, detail="invite event destination is not configured")
    body = {
        "eventId": payload["eventId"],
        "orderId": payload["orderId"],
        "amountCents": payload["amountCents"],
        "productName": str(payload["productName"])[:300],
        "sku": str(payload.get("sku") or payload["itemId"])[:120],
        "quantity": payload["quantity"],
        "buyerRef": payload["toUserId"],
        "eventType": "order.paid",
        "platformStatus": "paid",
        "platformContext": {
            "accountId": payload["cookieId"],
            "chatId": payload["chatId"],
            "toUserId": payload["toUserId"],
            "itemId": payload["itemId"],
            "sku": payload.get("sku", ""),
            "source": "xianyu_bridge",
        },
    }
    url = base_url.rstrip("/") + "/api/order-events"
    timeout = aiohttp.ClientTimeout(total=float(os.getenv("XIANYU_INVITE_TIMEOUT_SECONDS", "20")))
    async with aiohttp.ClientSession(timeout=timeout) as session:
        try:
            async with session.post(url, headers=_signature_headers(body, secret), data=_canonical(body)) as response:
                text = await response.text()
                try:
                    result = json.loads(text) if text else {}
                except ValueError:
                    result = {"message": text[:200]}
                if response.status >= 400:
                    raise HTTPException(status_code=502, detail=f"invite order event returned {response.status}")
                return result
        except HTTPException:
            raise
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise HTTPException(status_code=502, detail="invite order event request failed") from exc


@invite_bridge_router.post("/internal/invite/order-events")
async def receive_order_event(request: Request, body: BridgeOrderEvent):
    payload = body.model_dump(by_alias=True)
    await _verify_request(request, payload)
    if body.platform_status != "pending_ship":
        raise HTTPException(status_code=409, detail="only pending_ship orders can enter invite flow")
    if not _invite_item_enabled(body.cookie_id, body.item_id):
        raise HTTPException(status_code=409, detail="order item is not enabled for invite bridge")
    return await _send_order_event_to_invite(payload)


@invite_bridge_router.post("/internal/invite/send-message")
async def send_invite_message(request: Request, body: SendMessageRequest):
    payload = body.model_dump(by_alias=True)
    await _verify_request(request, payload)
    operation, created = _begin_operation(body.operation_key, "message", body.order_id, body.cookie_id, payload)
    if not created:
        if (
            operation["status"] == "failed"
            and operation.get("last_error") == "direct_conversation_not_submitted"
        ):
            with db_manager.lock:
                db_manager.conn.execute(
                    "UPDATE invite_bridge_operations SET status = 'pending', provider_ref = '', "
                    "response_json = '{}', last_error = '', updated_at = ? WHERE operation_key = ?",
                    (time.time(), body.operation_key),
                )
                db_manager.conn.commit()
            created = True
        if operation["status"] == "pending":
            return _set_operation(body.operation_key, "needs_review", error="previous message attempt was interrupted")
        if not created:
            return _operation_response(operation)
    order = _order_or_error(body.order_id, body.cookie_id)
    order_status = str(order.get("order_status") or "")
    is_fulfillment_message = body.operation_key.startswith("fulfillment-message-")
    if order_status != "pending_ship" and not (is_fulfillment_message and order_status in {"shipped", "completed"}):
        return _set_operation(body.operation_key, "needs_review", error="order is not pending_ship")
    expected_buyer = str(order.get("buyer_id") or "")
    if expected_buyer and expected_buyer != body.to_user_id:
        return _set_operation(body.operation_key, "needs_review", error="buyer identity mismatch")
    expected_chat = str(order.get("chat_id") or "")
    if expected_chat and expected_chat != body.chat_id:
        return _set_operation(body.operation_key, "needs_review", error="chat identity mismatch")
    try:
        from XianyuAutoAsync import XianyuLive

        live_instance = XianyuLive.get_instance(body.cookie_id)
        if not live_instance or not live_instance.ws or live_instance.ws.closed:
            return _set_operation(body.operation_key, "needs_review", error="account websocket is offline")
        if order_status == "pending_ship":
            payment_check = await live_instance._verify_paid_order_for_delivery(
                order_id=body.order_id,
                item_id=str(order.get("item_id") or ""),
                buyer_id=str(order.get("buyer_id") or ""),
            )
            if not payment_check.get("allowed"):
                state = "needs_review" if payment_check.get("requires_login") else "failed"
                return _set_operation(
                    body.operation_key,
                    state,
                    error=str(payment_check.get("error_code") or "payment state is not pending_ship"),
                )
        if body.chat_id.startswith("direct:"):
            await asyncio.wait_for(
                live_instance.send_msg_once(body.to_user_id, str(order.get("item_id") or ""), body.text),
                timeout=20,
            )
            conversation_mode = "direct_create"
        else:
            await asyncio.wait_for(
                live_instance.send_msg(live_instance.ws, body.chat_id, body.to_user_id, body.text),
                timeout=20,
            )
            conversation_mode = "existing"
        return _set_operation(
            body.operation_key,
            "submitted",
            provider_ref=body.operation_key,
            response={"messageAccepted": True, "conversationMode": conversation_mode},
        )
    except Exception as exc:
        from XianyuAutoAsync import DirectMessageNotSubmitted

        if isinstance(exc, DirectMessageNotSubmitted):
            return _set_operation(
                body.operation_key,
                "failed",
                error="direct_conversation_not_submitted",
            )
        return _set_operation(body.operation_key, "ambiguous", provider_ref=body.operation_key, error=f"message write outcome unknown: {type(exc).__name__}")


@invite_bridge_router.post("/internal/invite/mark-fulfilled")
async def mark_invite_fulfilled(request: Request, body: MarkFulfilledRequest):
    payload = body.model_dump(by_alias=True)
    await _verify_request(request, payload)
    operation, created = _begin_operation(body.operation_key, "mark_fulfilled", body.order_id, body.cookie_id, payload)
    if not created:
        if operation["status"] == "pending":
            return _set_operation(body.operation_key, "needs_review", error="previous fulfillment attempt was interrupted")
        return _operation_response(operation)
    order = _order_or_error(body.order_id, body.cookie_id, body.item_id)
    if str(order.get("order_status") or "") in {"shipped", "completed"} or bool(order.get("system_shipped")):
        return _set_operation(body.operation_key, "succeeded", provider_ref=body.order_id, response={"platformStatus": order.get("order_status")})
    if str(order.get("order_status") or "") != "pending_ship":
        return _set_operation(body.operation_key, "needs_review", error="order is not pending_ship")
    cookies = db_manager.get_cookie(body.cookie_id)
    if not cookies:
        return _set_operation(body.operation_key, "needs_review", error="account cookie is unavailable")
    try:
        is_bargain = str(order.get("is_bargain") or "").strip().lower() in {"1", "true", "yes", "on"}
        buyer_id = str(order.get("buyer_id") or "")

        async def _do_freeshipping():
            from XianyuAutoAsync import XianyuLive

            live_instance = XianyuLive.get_instance(body.cookie_id)
            if live_instance:
                return await asyncio.wait_for(
                    live_instance.auto_freeshipping(body.order_id, body.item_id, buyer_id),
                    timeout=35,
                )
            from secure_freeshipping_decrypted import SecureFreeshipping

            async with aiohttp.ClientSession(
                headers={"cookie": cookies},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as session:
                return await asyncio.wait_for(
                    SecureFreeshipping(session, cookies, body.cookie_id).auto_freeshipping(
                        body.order_id, body.item_id, buyer_id
                    ),
                    timeout=35,
                )

        async def _do_confirm():
            from secure_confirm_decrypted import SecureConfirm

            async with aiohttp.ClientSession(
                headers={"cookie": cookies},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as session:
                return await asyncio.wait_for(
                    SecureConfirm(session, cookies, body.cookie_id, None).auto_confirm(
                        body.order_id, body.item_id
                    ),
                    timeout=35,
                )

        # 免拼发货与确认发货互为回退：拼单标记可能在轮询补单时丢失（平台订单列表
        # 不返回该标记），主接口若因"订单类型不匹配"等未知业务失败，回退到另一种
        # 发货接口再试一次，避免用错接口导致漏发货。会话失效/风控/限流等已知失败
        # 不回退（换接口也无益或加重风控）。
        if is_bargain:
            primary, fallback = _do_freeshipping, _do_confirm
            primary_mode, fallback_mode = "free_shipping", "status_only"
        else:
            primary, fallback = _do_confirm, _do_freeshipping
            primary_mode, fallback_mode = "status_only", "free_shipping"

        result = await primary()
        delivery_mode = primary_mode
        if (
            (not result or not result.get("success"))
            and str((result or {}).get("category") or "") == "unknown_failure"
        ):
            fallback_result = await fallback()
            if fallback_result and fallback_result.get("success"):
                result = fallback_result
                delivery_mode = fallback_mode

        if not result or not result.get("success"):
            error = "platform free-shipping confirmation failed" if is_bargain else "platform status_only confirmation failed"
            return _set_operation(body.operation_key, "failed", error=error)
        if not db_manager.insert_or_update_order(order_id=body.order_id, cookie_id=body.cookie_id, order_status="shipped", system_shipped=True):
            return _set_operation(body.operation_key, "needs_review", error="local order state update failed")
        return _set_operation(
            body.operation_key,
            "succeeded",
            provider_ref=body.order_id,
            response={"platformStatus": "shipped", "deliveryMode": delivery_mode},
        )
    except Exception as exc:
        return _set_operation(body.operation_key, "needs_review", error=f"platform confirmation outcome unknown: {type(exc).__name__}")


@invite_bridge_router.get("/internal/invite/operations/{operation_key}")
async def get_invite_operation(request: Request, operation_key: str):
    body = {"operationKey": operation_key}
    await _verify_request(request, body)
    row = _load_operation(operation_key)
    if not row:
        return {"operationKey": operation_key, "status": "not_found", "state": "not_found"}
    return _operation_response(row)
