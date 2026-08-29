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
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from db_manager import db_manager
from delivery_stage_metrics import (
    STAGE_CONFIRMATION,
    STAGE_FULFILLMENT,
    STAGE_SHIPPED,
    record_stage as record_delivery_stage,
)


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


def _is_provisional_chat(chat_id: str) -> bool:
    """Synthetic direct references are placeholders until a real IM cid exists."""
    return str(chat_id or "").strip().startswith("direct:")


def _opaque_ref(value: Any) -> str:
    """Return a short log reference without exposing an account or order id."""
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()[:12]


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


def _has_succeeded_fulfillment_message(cookie_id: str, order_id: str) -> bool:
    """兑换码是否已确认送达买家。

    判据：存在一条 operation_type='message'、operation_key 以
    'fulfillment-message-' 开头、status='succeeded' 的记录（这正是发码消息
    成功送达时的落库形态，见 send_invite_message 成功分支）。确认消息
    （confirmation-message-）成功不计入，ambiguous/failed 也不计入。

    这是「已履约」的权威判据——对账兜底据此只补平台发货、绝不重发码。
    查询异常一律返回 False：宁可漏一次兜底，也不可误判成已发码而漏发。
    """
    try:
        with db_manager.lock:
            row = db_manager.conn.execute(
                "SELECT 1 FROM invite_bridge_operations "
                "WHERE cookie_id = ? AND order_id = ? "
                "AND operation_type = 'message' "
                "AND operation_key LIKE 'fulfillment-message-%' "
                "AND status = 'succeeded' LIMIT 1",
                (cookie_id, order_id),
            ).fetchone()
        return row is not None
    except Exception as exc:
        logger.warning("查询履约消息状态失败: {}", type(exc).__name__)
        return False


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


async def _fetch_platform_order_status(cookie_id: str, order_id: str, cookies: str) -> Dict[str, Any]:
    """回查平台订单真实状态；任何不确定一律按查询失败返回（fail-closed）。

    本地 order_status / system_shipped 只是缓存，不是平台事实：护栏与对账
    都必须以这里的平台详情为准来决定要不要补发货。
    """
    if not str(order_id or "").isdigit():
        return {"success": False, "error": "order id is not a platform order number"}
    from order_sync_service import fetch_xianyu_order_detail, parse_order_detail_payload

    user_agent = ""
    try:
        details = db_manager.get_cookie_details(cookie_id) or {}
        user_agent = str(details.get("browser_user_agent") or "")
    except Exception:
        user_agent = ""
    try:
        parsed = parse_order_detail_payload(
            await fetch_xianyu_order_detail(
                cookie_id=cookie_id,
                cookie_string=cookies,
                order_id=str(order_id),
                user_agent=user_agent,
            ),
            cookie_id,
        )
    except Exception as exc:
        return {"success": False, "error": f"platform detail fetch failed: {type(exc).__name__}"}
    if not parsed.get("success"):
        return {"success": False, "error": str(parsed.get("error_code") or "platform_error")}
    row = next(
        (
            item
            for item in parsed.get("orders") or []
            if str(item.get("order_id") or "") == str(order_id)
        ),
        None,
    )
    if not row:
        return {"success": False, "error": "order missing from platform detail"}
    return {"success": True, "status": str(row.get("order_status") or "")}


async def _execute_platform_ship(
    *,
    cookie_id: str,
    order_id: str,
    item_id: str,
    buyer_id: str,
    is_bargain: bool,
    cookies: str,
) -> Dict[str, Any]:
    """对平台执行「免拼成团 + 虚拟发货」；两个动作平台侧幂等。

    「免拼」（groupon freeshipping）只是拼团成团动作：成团后平台订单仍是
    待发货，只有虚拟发货（consign.dummy，即 _do_confirm）才能把订单推进到
    已发货。因此不论哪种订单，最终都必须成功调用一次 _do_confirm；小刀/
    拼团单需要先免拼成团（「已免拼/已成团」按幂等通过）再发货。免拼报
    unknown_failure 多半是拼单标记误判（其实是普通单），直接尝试发货；
    会话失效/风控/限流耗尽等已知失败立即失败关闭，不盲目连打平台。
    """

    async def _do_freeshipping():
        from XianyuAutoAsync import XianyuLive

        live_instance = XianyuLive.get_instance(cookie_id)
        if live_instance:
            return await asyncio.wait_for(
                live_instance.auto_freeshipping(order_id, item_id, buyer_id),
                timeout=35,
            )
        from secure_freeshipping_decrypted import SecureFreeshipping

        async with aiohttp.ClientSession(
            headers={"cookie": cookies},
            timeout=aiohttp.ClientTimeout(total=30),
        ) as session:
            return await asyncio.wait_for(
                SecureFreeshipping(session, cookies, cookie_id).auto_freeshipping(
                    order_id, item_id, buyer_id
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
                SecureConfirm(session, cookies, cookie_id, None).auto_confirm(
                    order_id, item_id
                ),
                timeout=35,
            )

    freeshipping_done = False
    freeshipping_attempted = False
    if is_bargain:
        freeshipping_attempted = True
        freeshipping_result = await _do_freeshipping()
        if freeshipping_result and freeshipping_result.get("success"):
            freeshipping_done = True
        elif str((freeshipping_result or {}).get("category") or "") != "unknown_failure":
            return {"success": False, "error": "platform free-shipping confirmation failed"}

    result = await _do_confirm()
    if (
        (not result or not result.get("success"))
        and not freeshipping_attempted
        and str((result or {}).get("category") or "") == "unknown_failure"
    ):
        # 确认发货报未知业务失败且本次还未免拼过：拼单标记可能在轮询补单时
        # 丢失（平台订单列表不返回该标记），未成团的拼团单无法直接发货。
        # 补一次免拼后重试真发货。
        freeshipping_attempted = True
        freeshipping_result = await _do_freeshipping()
        if freeshipping_result and freeshipping_result.get("success"):
            freeshipping_done = True
            result = await _do_confirm()

    delivery_mode = "free_shipping_then_status_only" if freeshipping_done else "status_only"
    if not result or not result.get("success"):
        return {"success": False, "error": "platform status_only confirmation failed"}
    return {"success": True, "delivery_mode": delivery_mode}


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
        retryable_prewrite_failure = (
            operation["status"] == "failed"
            and operation.get("last_error") == "direct_conversation_not_submitted"
        )
        retryable_identity_transition = (
            body.operation_key.startswith("fulfillment-message-")
            and operation["status"] == "needs_review"
            and operation.get("last_error") == "chat identity mismatch"
            and _is_provisional_chat(body.chat_id)
        )
        if retryable_prewrite_failure or retryable_identity_transition:
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
    expected_chat = str(order.get("chat_id") or "").strip()
    effective_chat = body.chat_id
    if _is_provisional_chat(body.chat_id) and expected_chat and not _is_provisional_chat(expected_chat):
        effective_chat = expected_chat
    if expected_chat and not _is_provisional_chat(expected_chat) and expected_chat != effective_chat:
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
        if _is_provisional_chat(effective_chat):
            message_response = await asyncio.wait_for(
                live_instance.send_msg_once(
                    body.to_user_id,
                    str(order.get("item_id") or ""),
                    body.text,
                    wait_for_response=True,
                ),
                timeout=20,
            )
            conversation_mode = "direct_create"
        else:
            message_response = await asyncio.wait_for(
                live_instance.send_msg(
                    live_instance.ws,
                    effective_chat,
                    body.to_user_id,
                    body.text,
                    wait_for_response=True,
                ),
                timeout=20,
            )
            conversation_mode = "existing"
        if not isinstance(message_response, dict):
            return _set_operation(
                body.operation_key,
                "ambiguous",
                provider_ref=body.operation_key,
                error="platform message response missing",
            )
        response_summary = XianyuLive._direct_frame_error_summary(message_response)
        response_code = response_summary.get("code")
        if response_code not in (None, "200"):
            return _set_operation(
                body.operation_key,
                "failed",
                error=f"platform message rejected: code={response_code}",
            )
        operation_response = _set_operation(
            body.operation_key,
            "succeeded",
            provider_ref=body.operation_key,
            response={
                "messageAccepted": True,
                "platformAcknowledged": True,
                "conversationMode": conversation_mode,
                "chatCanonicalized": effective_chat != body.chat_id,
            },
        )
        if body.operation_key.startswith("confirmation-message-"):
            record_delivery_stage(body.order_id, body.cookie_id, STAGE_CONFIRMATION)
        elif is_fulfillment_message:
            record_delivery_stage(body.order_id, body.cookie_id, STAGE_FULFILLMENT)
        return operation_response
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
    local_status = str(order.get("order_status") or "")
    locally_shipped = local_status in {"shipped", "completed"} or bool(order.get("system_shipped"))
    if not locally_shipped and local_status != "pending_ship":
        return _set_operation(body.operation_key, "needs_review", error="order is not pending_ship")
    cookies = db_manager.get_cookie(body.cookie_id)
    if not cookies:
        return _set_operation(body.operation_key, "needs_review", error="account cookie is unavailable")
    if locally_shipped:
        # 本地已发标记只是缓存，不是平台事实。以前这里直接按本地标记返回
        # succeeded，导致「码已发、平台仍待发货」的订单被幂等护栏永久吞掉
        # （2026-08 实测 10 笔活跃账号卡单）。现在回查平台真实状态：平台
        # 确认已推进才算成功；平台仍待发货就继续走补发货；查询失败一律
        # needs_review 关闭，不盲发。
        platform = await _fetch_platform_order_status(body.cookie_id, body.order_id, cookies)
        if not platform.get("success"):
            return _set_operation(
                body.operation_key,
                "needs_review",
                error=f"platform status recheck failed: {platform.get('error')}",
            )
        platform_status = str(platform.get("status") or "")
        if platform_status != "pending_ship":
            return _set_operation(
                body.operation_key,
                "succeeded",
                provider_ref=body.order_id,
                response={"platformStatus": platform_status or order.get("order_status")},
            )
        logger.warning(
            "邀请桥本地已发但平台仍待发货，执行补发货: order_ref={} account_ref={}",
            _opaque_ref(body.order_id),
            _opaque_ref(body.cookie_id),
        )
    try:
        is_bargain = str(order.get("is_bargain") or "").strip().lower() in {"1", "true", "yes", "on"}
        buyer_id = str(order.get("buyer_id") or "")
        ship = await _execute_platform_ship(
            cookie_id=body.cookie_id,
            order_id=body.order_id,
            item_id=body.item_id,
            buyer_id=buyer_id,
            is_bargain=is_bargain,
            cookies=cookies,
        )
        if not ship.get("success"):
            return _set_operation(
                body.operation_key,
                "failed",
                error=str(ship.get("error") or "platform confirmation failed"),
            )
        if not db_manager.insert_or_update_order(order_id=body.order_id, cookie_id=body.cookie_id, order_status="shipped", system_shipped=True):
            return _set_operation(body.operation_key, "needs_review", error="local order state update failed")
        operation_response = _set_operation(
            body.operation_key,
            "succeeded",
            provider_ref=body.order_id,
            response={"platformStatus": "shipped", "deliveryMode": ship.get("delivery_mode")},
        )
        record_delivery_stage(body.order_id, body.cookie_id, STAGE_SHIPPED)
        return operation_response
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
