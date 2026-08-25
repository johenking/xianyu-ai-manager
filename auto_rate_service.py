"""Opt-in seller reviews, scheduled and submitted serially."""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import time
from typing import Any, Awaitable, Callable, Dict, Optional

import aiohttp
from loguru import logger

from db_manager import db_manager
from order_sync_service import (
    DEFAULT_ORDER_USER_AGENT,
    XianyuOrderListClient,
    _parse_order_timestamp,
    classify_platform_error,
    get_order_sync_lock,
)


FALLBACK_POSITIVE_REVIEWS = (
    "交易顺利，感谢您的支持，祝您生活愉快！",
    "感谢您的支持，本次交易顺利完成，五星好评！",
    "本次交易顺利，感谢支持，祝您一切顺心！",
)


def _opaque_ref(value: Any) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()[:12]


def _response_cookie_updates(response: aiohttp.ClientResponse) -> Dict[str, str]:
    try:
        values = response.headers.getall("Set-Cookie", [])
    except Exception:
        values = []
    updates: Dict[str, str] = {}
    for raw_cookie in values:
        segment = str(raw_cookie).split(";", 1)[0]
        if "=" not in segment:
            continue
        name, value = segment.split("=", 1)
        if name.strip():
            updates[name.strip()] = value.strip()
    return updates


def _build_rate_payload(order_id: str, feedback: str) -> Dict[str, Any]:
    return {
        "tradeIdList": [order_id],
        "feedback": feedback,
        "rate": 1,
        "imageUrls": [],
        "anonymous": False,
    }


def parse_rate_response(payload: Any, order_id: str) -> Dict[str, Any]:
    """Classify one-order MTOP response without treating schema uncertainty as success."""
    if not isinstance(payload, dict):
        return {
            "state": "needs_reconcile",
            "result_code": "invalid_response_schema",
            "error": "评价接口返回格式无法确认",
            "response": {},
        }
    ret_values = payload.get("ret") or []
    if isinstance(ret_values, (str, bytes)):
        ret_values = [ret_values]
    if not any(str(value).upper().startswith("SUCCESS") for value in ret_values):
        classified = classify_platform_error(ret_values)
        return {
            "state": "needs_reconcile" if classified.get("retryable") else "failed",
            "result_code": str(classified.get("code") or "platform_error"),
            "error": str(classified.get("message") or "评价提交失败")[:500],
            "response": {},
        }

    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    module = data.get("module") if isinstance(data.get("module"), dict) else {}
    if not module:
        return {
            "state": "needs_reconcile",
            "result_code": "missing_result_module",
            "error": "评价接口未返回可确认的结果模块",
            "response": {},
        }
    raw_module_success = module.get("success")
    module_success = raw_module_success is True or str(raw_module_success).strip().lower() in {
        "true",
        "1",
    }
    success_ids = {
        str(value)
        for value in (module.get("successOrderIds") or [])
        if str(value or "")
    }
    failed: Dict[str, str] = {}
    for value in module.get("failOrderInfos") or []:
        if not isinstance(value, dict):
            continue
        failed_id = str(
            value.get("orderId") or value.get("order_id") or value.get("bizOrderId") or ""
        )
        if failed_id:
            failed[failed_id] = str(
                value.get("failReason")
                or value.get("errorMsg")
                or value.get("message")
                or value.get("errorCode")
                or "评价提交失败"
            )[:500]
    safe_response = {
        "module_success": module_success,
        "success_order_ids": sorted(success_ids),
        "failed_order_ids": sorted(failed),
    }
    target = str(order_id)
    if module_success and target in success_ids and target not in failed:
        return {
            "state": "succeeded",
            "result_code": "success",
            "error": "",
            "response": safe_response,
        }
    if target in failed and target not in success_ids:
        return {
            "state": "failed",
            "result_code": "platform_rejected",
            "error": failed[target],
            "response": safe_response,
        }
    if raw_module_success is not None and not module_success and target not in success_ids:
        return {
            "state": "failed",
            "result_code": "module_rejected",
            "error": str(module.get("message") or module.get("errorMsg") or "评价提交失败")[:500],
            "response": safe_response,
        }
    return {
        "state": "needs_reconcile",
        "result_code": "inconclusive_response",
        "error": "评价接口未明确返回该订单的成功或失败结果",
        "response": safe_response,
    }


async def submit_xianyu_seller_rate(
    *,
    cookie_id: str,
    cookie_string: str,
    order_id: str,
    feedback: str,
    user_agent: str = "",
) -> Dict[str, Any]:
    """Submit one seller review; credentials are never returned or logged."""
    del cookie_id
    from utils.xianyu_utils import generate_sign, trans_cookies

    normalized_order_id = str(order_id or "").strip()
    normalized_feedback = str(feedback or "").strip()[:200]
    if not normalized_order_id or not normalized_feedback:
        return {
            "state": "failed",
            "result_code": "invalid_request",
            "error": "订单或评价内容为空",
            "response": {},
        }
    cookies = trans_cookies(cookie_string)
    token = str(cookies.get("_m_h5_tk") or "").split("_", 1)[0]
    if not token:
        return {
            "state": "failed",
            "result_code": "session_expired",
            "error": "闲鱼登录状态已过期，请先更新登录状态",
            "response": {},
        }

    timestamp = str(int(time.time() * 1000))
    request_data = _build_rate_payload(normalized_order_id, normalized_feedback)
    data_value = json.dumps(request_data, ensure_ascii=False, separators=(",", ":"))
    params = {
        "jsv": "2.7.2",
        "appKey": "34839810",
        "t": timestamp,
        "sign": generate_sign(timestamp, token, data_value),
        "v": "1.0",
        "type": "json",
        "accountSite": "xianyu",
        "dataType": "json",
        "api": "mtop.taobao.idle.merchant.rate.create",
        "valueType": "string",
        "sessionOption": "AutoLoginOnly",
    }
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
        "Cookie": cookie_string,
        "idle_site_biz_code": "COMMONPRO",
        "Origin": "https://seller.goofish.com",
        "Referer": "https://seller.goofish.com/?site=COMMONPRO#/seller-trade/order-manage",
        "User-Agent": str(user_agent or DEFAULT_ORDER_USER_AGENT),
    }
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=20),
            cookie_jar=aiohttp.DummyCookieJar(),
        ) as session:
            async with session.post(
                "https://h5api.m.goofish.com/h5/mtop.taobao.idle.merchant.rate.create/1.0/",
                params=params,
                data={"data": data_value},
                headers=headers,
            ) as response:
                updates = _response_cookie_updates(response)
                if response.status >= 400:
                    result = parse_rate_response(
                        {"ret": [f"HTTP_{response.status}::评价接口请求失败"]},
                        normalized_order_id,
                    )
                else:
                    result = parse_rate_response(
                        await response.json(content_type=None),
                        normalized_order_id,
                    )
                if updates:
                    result["updated_cookie_string"] = XianyuOrderListClient._merge_cookie_updates(
                        cookie_string,
                        updates,
                    )
                return result
    except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
        return parse_rate_response(
            {"ret": [f"NETWORK_ERROR::{type(exc).__name__}"]},
            normalized_order_id,
        )


class AutoRateScheduler:
    def __init__(
        self,
        *,
        db=db_manager,
        client_factory: Callable[[], XianyuOrderListClient] = lambda: XianyuOrderListClient(
            max_pages=20,
            max_orders=400,
        ),
        submitter: Callable[..., Awaitable[Dict[str, Any]]] = submit_xianyu_seller_rate,
        now_fn: Callable[[], float] = time.time,
        jitter_fn: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self.db = db
        self.client_factory = client_factory
        self.submitter = submitter
        self.now_fn = now_fn
        self.jitter_fn = jitter_fn
        self._task: Optional[asyncio.Task[None]] = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        interrupted = self.db.reconcile_interrupted_auto_rate_tasks(now=self.now_fn())
        if interrupted:
            logger.warning(f"已有 {interrupted} 条自动好评任务转为人工核对")
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="seller-auto-rate")

    async def stop(self) -> None:
        self._stop.set()
        task = self._task
        self._task = None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.scan_once()
            except Exception as exc:
                logger.error(f"自动好评循环异常: {type(exc).__name__}")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=60)
            except asyncio.TimeoutError:
                continue

    def _persist_cookie_update(
        self,
        account: Dict[str, Any],
        updated_cookie_string: str,
    ) -> bool:
        if not updated_cookie_string or updated_cookie_string == account["cookie_string"]:
            return True
        result = self.db.compare_and_swap_cookie_session(
            account["cookie_id"],
            user_id=account["user_id"],
            expected_xianyu_unb=account["xianyu_unb"],
            expected_revision=account["cookie_revision"],
            cookie_value=updated_cookie_string,
            browser_user_agent=account["browser_user_agent"],
        )
        return result.get("state") in {"updated", "unchanged"}

    async def _discover_account(self, account: Dict[str, Any]) -> None:
        now = self.now_fn()
        days = max(1, min(90, int((now - account["enabled_at"]) / 86400) + 1))
        async with get_order_sync_lock(account["cookie_id"]):
            discovery = await self.client_factory().discover(
                cookie_id=account["cookie_id"],
                cookie_string=account["cookie_string"],
                days=days,
                user_agent=account["browser_user_agent"],
            )
        if not discovery.get("success"):
            logger.warning(
                "自动好评订单发现失败: account_ref={} code={}",
                _opaque_ref(account["cookie_id"]),
                discovery.get("error_code") or "unknown",
            )
            return
        updated_cookie = str(discovery.get("updated_cookie_string") or "")
        if updated_cookie and not self._persist_cookie_update(account, updated_cookie):
            logger.warning(
                "自动好评停止本轮排程: account_ref={} reason=cookie_revision_conflict",
                _opaque_ref(account["cookie_id"]),
            )
            return
        for order in discovery.get("orders") or []:
            created_at = _parse_order_timestamp(order.get("created_at"))
            order_id = str(order.get("order_id") or "").strip()
            if (
                not order_id
                or not order.get("can_rate")
                or created_at is None
                or created_at < account["enabled_at"]
            ):
                continue
            self.db.schedule_auto_rate_task(
                user_id=account["user_id"],
                cookie_id=account["cookie_id"],
                order_id=order_id,
                item_title=str(order.get("item_title") or ""),
                order_created_at=created_at,
                due_at=now + self.jitter_fn(300, 900),
                now=now,
            )

    @staticmethod
    def _generate_feedback(cookie_id: str) -> str:
        from ai_reply_engine import ai_reply_engine

        return ai_reply_engine.generate_positive_review(cookie_id) or random.choice(
            FALLBACK_POSITIVE_REVIEWS
        )

    async def _submit_due_task(self) -> None:
        task = self.db.claim_due_auto_rate_task(now=self.now_fn())
        if not task:
            return
        settings = self.db.get_auto_rate_settings(task["cookie_id"], task["user_id"])
        details = self.db.get_cookie_details(task["cookie_id"]) or {}
        if not settings or not settings["enabled"] or details.get("user_id") != task["user_id"]:
            self.db.finish_auto_rate_task(
                task["id"],
                state="failed",
                result_code="account_disabled_before_submit",
                error="账号已关闭自动好评或归属发生变化",
                now=self.now_fn(),
            )
            return
        try:
            feedback = task["feedback"] or await asyncio.to_thread(
                self._generate_feedback,
                task["cookie_id"],
            )
        except Exception as exc:
            logger.warning(f"自动好评文案生成失败，使用固定文案: {type(exc).__name__}")
            feedback = random.choice(FALLBACK_POSITIVE_REVIEWS)
        if not self.db.set_auto_rate_feedback(task["id"], feedback):
            self.db.finish_auto_rate_task(
                task["id"],
                state="failed",
                result_code="feedback_persist_failed",
                error="评价内容保存失败，未向平台提交",
                now=self.now_fn(),
            )
            return
        settings = self.db.get_auto_rate_settings(task["cookie_id"], task["user_id"])
        if not settings or not settings["enabled"]:
            self.db.finish_auto_rate_task(
                task["id"],
                state="failed",
                result_code="account_disabled_before_submit",
                error="账号已关闭自动好评，未向平台提交",
                now=self.now_fn(),
            )
            return
        account = {
            "cookie_id": task["cookie_id"],
            "cookie_string": str(details.get("value") or ""),
            "user_id": task["user_id"],
            "xianyu_unb": str(details.get("xianyu_unb") or ""),
            "browser_user_agent": str(details.get("browser_user_agent") or ""),
            "cookie_revision": int(details.get("cookie_revision") or 0),
        }
        if not self.db.mark_auto_rate_submission_started(task["id"], now=self.now_fn()):
            self.db.finish_auto_rate_task(
                task["id"],
                state="failed",
                result_code="submission_marker_failed",
                error="评价提交标记保存失败，未向平台提交",
                now=self.now_fn(),
            )
            return
        try:
            async with get_order_sync_lock(task["cookie_id"]):
                result = await self.submitter(
                    cookie_id=task["cookie_id"],
                    cookie_string=account["cookie_string"],
                    order_id=task["order_id"],
                    feedback=feedback,
                    user_agent=account["browser_user_agent"],
                )
            if not isinstance(result, dict):
                raise TypeError("rate submitter returned a non-object result")
        except Exception as exc:
            result = {
                "state": "needs_reconcile",
                "result_code": "submitter_exception",
                "error": f"评价提交结果无法确认: {type(exc).__name__}",
                "response": {},
            }
        updated_cookie = str(result.pop("updated_cookie_string", "") or "")
        if updated_cookie and not self._persist_cookie_update(account, updated_cookie):
            logger.warning(
                "评价接口 Cookie 更新发生版本冲突: account_ref={}",
                _opaque_ref(task["cookie_id"]),
            )
        state = str(result.get("state") or "needs_reconcile")
        if state not in {"succeeded", "failed", "needs_reconcile"}:
            state = "needs_reconcile"
        self.db.finish_auto_rate_task(
            task["id"],
            state=state,
            result_code=str(result.get("result_code") or "unknown"),
            error=str(result.get("error") or ""),
            response=result.get("response") if isinstance(result.get("response"), dict) else {},
            now=self.now_fn(),
        )

    async def scan_once(self) -> None:
        for account in self.db.get_auto_rate_enabled_accounts():
            await self._discover_account(account)
        # One submission per pass keeps platform writes strictly serial and spaced.
        await self._submit_due_task()


auto_rate_scheduler = AutoRateScheduler()
