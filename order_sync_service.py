"""Shared order discovery, status normalization and sync coordination."""

import json
import time
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional

import aiohttp


ORDER_STATUSES = {
    "unknown",
    "processing",
    "pending_ship",
    "shipped",
    "completed",
    "refunding",
    "refunded",
    "refund_cancelled",
    "cancelled",
}

STATUS_CODE_MAP = {
    "1": "processing",
    "2": "pending_ship",
    "3": "shipped",
    "4": "completed",
    "5": "refunding",
    "6": "cancelled",
    "7": "refunding",
    "8": "refunded",
    "9": "refunding",
    "10": "refund_cancelled",
    "11": "completed",
    "12": "cancelled",
}

PLATFORM_STATUS_MAP = {
    "WAIT_SELLER_SEND_GOODS": "pending_ship",
    "WAIT_BUYER_CONFIRM_GOODS": "shipped",
    "TRADE_FINISHED": "completed",
    "TRADE_CLOSED": "cancelled",
    "TRADE_CLOSED_BY_TAOBAO": "cancelled",
    "REFUNDING": "refunding",
    "REFUND_SUCCESS": "refunded",
    "REFUND_CLOSED": "refund_cancelled",
}


def normalize_order_status(raw_status: Any, status_text: str = "") -> str:
    text = str(status_text or "").strip().lower()

    if any(value in text for value in ("撤销退款", "退款撤销", "退款关闭", "关闭退款")):
        return "refund_cancelled"
    if any(value in text for value in ("退款成功", "已退款", "钱款已原路退返", "钱款退回", "退款完成")):
        return "refunded"
    if any(value in text for value in ("退款中", "申请退款", "退款申请", "退货中", "退款协商")):
        return "refunding"
    if any(value in text for value in ("确认收货", "已签收", "交易成功", "交易完成", "订单完成")):
        return "completed"
    if any(value in text for value in ("待买家确认收货", "卖家已发货", "已发货")):
        return "shipped"
    if any(value in text for value in ("待发货", "等待卖家发货", "买家已付款")):
        return "pending_ship"
    if any(value in text for value in ("交易关闭", "订单已关闭", "取消了订单", "订单取消", "超时关闭")):
        return "cancelled"

    raw = str(raw_status or "").strip()
    if raw in ORDER_STATUSES:
        return raw
    if raw in STATUS_CODE_MAP:
        return STATUS_CODE_MAP[raw]
    return PLATFORM_STATUS_MAP.get(raw.upper(), "unknown")


def choose_order_status(current_status: Any, incoming_status: Any) -> str:
    current = normalize_order_status(current_status)
    incoming = normalize_order_status(incoming_status)
    if incoming == "unknown":
        return current
    if current in {"refunded", "cancelled"} and incoming not in {"refunded", "cancelled"}:
        return current
    return incoming


def classify_platform_error(ret_values: Iterable[Any] | Any) -> Dict[str, Any]:
    if isinstance(ret_values, (str, bytes)):
        values = [ret_values]
    else:
        values = list(ret_values or [])
    message = " | ".join(str(value) for value in values)
    lowered = message.lower()
    if "session_expired" in lowered or "session过期" in lowered or "token过期" in lowered:
        return {
            "code": "session_expired",
            "message": "闲鱼登录状态已过期，请先更新登录状态",
            "requires_login": True,
        }
    return {
        "code": "platform_error",
        "message": message or "闲鱼订单接口返回未知错误",
        "requires_login": False,
    }


def parse_order_api_payload(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {
            "success": False,
            "error_code": "invalid_response",
            "error": "闲鱼订单接口返回了无法解析的数据",
            "requires_login": False,
        }
    ret_values = payload.get("ret") or []
    if ret_values and not str(ret_values[0]).startswith("SUCCESS"):
        error = classify_platform_error(ret_values)
        return {
            "success": False,
            "error_code": error["code"],
            "error": error["message"],
            "requires_login": error["requires_login"],
        }
    return {"success": True, "data": payload.get("data") or {}}


def extract_order_list(payload: Any) -> List[Dict[str, Any]]:
    parsed = parse_order_api_payload(payload)
    if not parsed.get("success"):
        return []
    data = parsed.get("data") or {}
    module = data.get("module") if isinstance(data.get("module"), dict) else {}
    candidates = [
        module.get("items"),
        data.get("orders"),
        data.get("orderList"),
        data.get("order_list"),
        data.get("list"),
        data.get("cardList"),
    ]
    for candidate in candidates:
        if isinstance(candidate, list):
            return [row for row in candidate if isinstance(row, dict)]
    return []


def normalize_order_record(raw: Dict[str, Any], cookie_id: str) -> Dict[str, Any]:
    common_data = raw.get("commonData") if isinstance(raw.get("commonData"), dict) else {}
    buyer_info = raw.get("buyerInfoVO") if isinstance(raw.get("buyerInfoVO"), dict) else {}
    price_info = raw.get("priceVO") if isinstance(raw.get("priceVO"), dict) else {}
    order_id = common_data.get("orderId") or raw.get("order_id") or raw.get("orderId") or raw.get("bizOrderId") or raw.get("mainOrderId") or raw.get("id")
    raw_status = raw.get("status") or raw.get("orderStatus") or raw.get("statusCode") or common_data.get("orderStatusCode") or ""
    status_text = raw.get("status_text") or raw.get("statusText") or raw.get("status_desc") or raw.get("statusDesc") or common_data.get("orderStatus") or ""
    raw_amount = raw.get("amount") or raw.get("payAmount") or raw.get("actualFee") or raw.get("price") or price_info.get("totalPrice") or price_info.get("confirmFee") or price_info.get("auctionPrice") or ""
    amount = str(raw_amount).replace("¥", "").replace("￥", "").replace(",", "").strip()
    return {
        "order_id": str(order_id or ""),
        "item_id": str(common_data.get("itemId") or raw.get("item_id") or raw.get("itemId") or raw.get("auctionId") or ""),
        "buyer_id": str(buyer_info.get("buyerId") or raw.get("buyer_id") or raw.get("buyerId") or raw.get("buyerUserId") or ""),
        "item_title": str(raw.get("title") or raw.get("itemTitle") or raw.get("subject") or ""),
        "amount": amount,
        "quantity": str(raw.get("quantity") or raw.get("itemNum") or "1"),
        "order_status": normalize_order_status(raw_status, status_text),
        "platform_status_code": str(raw_status or ""),
        "platform_status_text": str(status_text or ""),
        "created_at": common_data.get("createTime") or raw.get("createTime") or raw.get("created_at") or raw.get("gmtCreate"),
        "cookie_id": cookie_id,
    }


def _parse_order_timestamp(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number / 1000 if number > 10_000_000_000 else number
    text = str(value).strip()
    if text.isdigit():
        number = float(text)
        return number / 1000 if number > 10_000_000_000 else number
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(text, pattern).timestamp()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


async def fetch_xianyu_order_list_page(
    *,
    cookie_id: str,
    cookie_string: str,
    page_number: int,
    page_size: int,
    user_id: str,
) -> Dict[str, Any]:
    """Fetch one seller-order page without persisting or logging credentials."""
    del cookie_id, user_id
    from utils.xianyu_utils import generate_sign, trans_cookies

    cookies = trans_cookies(cookie_string)
    token_cookie = cookies.get("_m_h5_tk", "")
    token = token_cookie.split("_", 1)[0]
    if not token:
        return {"ret": ["FAIL_SYS_SESSION_EXPIRED::Session过期"]}

    timestamp = str(int(time.time() * 1000))
    request_data = {
        "pageNumber": page_number,
        "rowsPerPage": page_size,
        "orderIds": "",
        "queryCode": "ALL",
        "orderSearchParam": "{}",
    }
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
        "api": "mtop.taobao.idle.trade.merchant.sold.get",
        "valueType": "string",
        "sessionOption": "AutoLoginOnly",
        "spm_cnt": "a21107h.42831410.0.0",
    }
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
        "Cookie": cookie_string,
        "idle_site_biz_code": "COMMONPRO",
        "idle_user_group_member_id": "",
        "Origin": "https://seller.goofish.com",
        "Referer": "https://seller.goofish.com/?site=COMMONPRO#/seller-trade/order-manage",
    }
    timeout = aiohttp.ClientTimeout(total=20)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                "https://h5api.m.goofish.com/h5/mtop.taobao.idle.trade.merchant.sold.get/1.0/",
                params=params,
                data={"data": data_value},
                headers=headers,
            ) as response:
                if response.status >= 400:
                    return {"ret": [f"HTTP_{response.status}::订单接口请求失败"]}
                return await response.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
        return {"ret": [f"NETWORK_ERROR::{type(exc).__name__}"]}


class XianyuOrderListClient:
    """Discover recent seller orders through the paginated platform endpoint."""

    def __init__(
        self,
        page_loader: Callable[..., Awaitable[Dict[str, Any]]] = fetch_xianyu_order_list_page,
        now_fn: Callable[[], float] = time.time,
        page_size: int = 20,
        max_pages: int = 50,
    ):
        self.page_loader = page_loader
        self.now_fn = now_fn
        self.page_size = max(1, min(int(page_size), 100))
        self.max_pages = max(1, min(int(max_pages), 100))

    async def discover(self, *, cookie_id: str, cookie_string: str, days: int = 90) -> Dict[str, Any]:
        try:
            from utils.xianyu_utils import trans_cookies

            user_id = str(trans_cookies(cookie_string).get("unb") or "")
        except (ValueError, AttributeError):
            user_id = ""
        if not user_id:
            return {
                "success": False,
                "error_code": "session_expired",
                "error": "闲鱼登录状态缺少账号身份，请先更新登录状态",
                "requires_login": True,
            }

        cutoff = self.now_fn() - max(1, min(int(days or 90), 365)) * 86400
        orders: List[Dict[str, Any]] = []
        seen_order_ids = set()
        pages_scanned = 0

        for page_number in range(1, self.max_pages + 1):
            payload = await self.page_loader(
                cookie_id=cookie_id,
                cookie_string=cookie_string,
                page_number=page_number,
                page_size=self.page_size,
                user_id=user_id,
            )
            parsed = parse_order_api_payload(payload)
            if not parsed.get("success"):
                return {
                    "success": False,
                    "error_code": parsed.get("error_code") or "platform_error",
                    "error": parsed.get("error") or "订单列表获取失败",
                    "requires_login": bool(parsed.get("requires_login")),
                    "orders": orders,
                    "pages_scanned": pages_scanned,
                }

            pages_scanned += 1
            raw_orders = extract_order_list(payload)
            response_data = parsed.get("data") or {}
            response_module = response_data.get("module") if isinstance(response_data.get("module"), dict) else {}
            next_page_value = response_module.get("nextPage")
            has_next_page = (
                str(next_page_value).lower() == "true"
                if next_page_value is not None
                else len(raw_orders) >= self.page_size
            )
            reached_cutoff = False
            for raw_order in raw_orders:
                order = normalize_order_record(raw_order, cookie_id)
                created_timestamp = _parse_order_timestamp(order.get("created_at"))
                if created_timestamp is not None and created_timestamp < cutoff:
                    reached_cutoff = True
                    continue
                order_id = order.get("order_id")
                if not order_id or order_id in seen_order_ids:
                    continue
                seen_order_ids.add(order_id)
                orders.append(order)

            if reached_cutoff or not has_next_page or not raw_orders:
                break

        return {
            "success": True,
            "requires_login": False,
            "orders": orders,
            "pages_scanned": pages_scanned,
        }


class OrderSyncCoordinator:
    """Apply recent platform order discovery with truthful per-account summaries."""

    def __init__(self, db, discoverer: Callable[..., Awaitable[Dict[str, Any]]],
                 detail_fetcher: Optional[Callable[..., Awaitable[List[Dict[str, Any]]]]] = None,
                 now_fn: Callable[[], float] = time.time):
        self.db = db
        self.discoverer = discoverer
        self.detail_fetcher = detail_fetcher
        self.now_fn = now_fn

    async def sync_account(self, cookie_id: str, cookie_string: str, days: int = 90) -> Dict[str, Any]:
        summary = {
            "total_seen": 0,
            "discovered": 0,
            "status_updated": 0,
            "details_updated": 0,
            "unchanged": 0,
            "failed": 0,
        }
        discovery = await self.discoverer(
            cookie_id=cookie_id,
            cookie_string=cookie_string,
            days=max(1, min(int(days or 90), 365)),
        )
        if not discovery.get("success"):
            return {
                "success": False,
                "partial": False,
                "requires_login": bool(discovery.get("requires_login")),
                "error_code": discovery.get("error_code") or "discovery_failed",
                "message": discovery.get("error") or "订单发现失败",
                "summary": summary,
                "errors": [discovery.get("error") or "订单发现失败"],
            }

        errors = []
        for discovered_order in discovery.get("orders") or []:
            order = dict(discovered_order)
            order_id = str(order.get("order_id") or "")
            if not order_id:
                summary["failed"] += 1
                errors.append("订单列表包含缺少订单号的记录")
                continue
            summary["total_seen"] += 1
            existing = self.db.get_order_by_id(order_id)
            if not existing:
                inserted = self.db.insert_or_update_order(
                    order_id=order_id,
                    item_id=order.get("item_id") or None,
                    buyer_id=order.get("buyer_id") or None,
                    quantity=order.get("quantity") or None,
                    amount=order.get("amount") or None,
                    order_status=order.get("order_status") or "unknown",
                    cookie_id=cookie_id,
                    created_at=order.get("created_at") or None,
                )
                if not inserted:
                    summary["failed"] += 1
                    errors.append("订单写入失败")
                    continue
                summary["discovered"] += 1

            update_result = self.db.apply_order_sync_update(
                order_id=order_id,
                cookie_id=cookie_id,
                incoming_status=order.get("order_status") or "unknown",
                platform_status_code=order.get("platform_status_code") or "",
                platform_status_text=order.get("platform_status_text") or "",
                status_source="order_list",
                item_id=order.get("item_id"),
                buyer_id=order.get("buyer_id"),
                quantity=order.get("quantity"),
                amount=order.get("amount"),
                created_at=order.get("created_at"),
            )
            if existing and update_result.get("status_changed"):
                summary["status_updated"] += 1
            if update_result.get("details_changed"):
                summary["details_updated"] += 1
            if existing and not update_result.get("status_changed") and not update_result.get("details_changed"):
                summary["unchanged"] += 1
            self.db.reconcile_order_status_events(
                cookie_id=cookie_id,
                order_id=order_id,
                item_id=str(order.get("item_id") or ""),
                buyer_id=str(order.get("buyer_id") or ""),
                chat_id=str(order.get("chat_id") or ""),
            )

        if self.detail_fetcher:
            cutoff = self.now_fn() - max(1, min(int(days or 90), 365)) * 86400
            detail_order_ids = []
            for order in self.db.get_orders_by_cookie(cookie_id, limit=5000):
                status = normalize_order_status(order.get("status"))
                if status == "refunded":
                    continue
                if status == "cancelled" and order.get("status_source") == "order_detail" and not order.get("last_sync_error"):
                    continue
                created_timestamp = _parse_order_timestamp(order.get("created_at"))
                if created_timestamp is not None and created_timestamp < cutoff:
                    continue
                order_id = str(order.get("order_id") or "")
                if order_id:
                    detail_order_ids.append(order_id)

            if detail_order_ids:
                detail_results = await self.detail_fetcher(
                    order_ids=detail_order_ids,
                    cookie_id=cookie_id,
                    cookie_string=cookie_string,
                )
                for detail in detail_results or []:
                    order_id = str(detail.get("order_id") or "")
                    if detail.get("requires_login") or detail.get("error_code") == "session_expired":
                        return {
                            "success": False,
                            "partial": summary["discovered"] > 0 or summary["status_updated"] > 0,
                            "requires_login": True,
                            "error_code": "session_expired",
                            "message": detail.get("error") or "闲鱼登录状态已过期，请先更新登录状态",
                            "summary": summary,
                            "errors": errors + [detail.get("error") or "闲鱼登录状态已过期"],
                        }
                    if not order_id:
                        summary["failed"] += 1
                        errors.append(detail.get("error") or "订单详情缺少订单号")
                        continue
                    if detail.get("error"):
                        summary["failed"] += 1
                        errors.append(f"订单 {order_id}：{detail['error']}")
                        self.db.apply_order_sync_update(
                            order_id=order_id,
                            cookie_id=cookie_id,
                            incoming_status="unknown",
                            status_source="order_detail",
                            sync_error=detail["error"],
                        )
                        continue

                    incoming_status = normalize_order_status(
                        detail.get("order_status"),
                        detail.get("status_text") or "",
                    )
                    update_result = self.db.apply_order_sync_update(
                        order_id=order_id,
                        cookie_id=cookie_id,
                        incoming_status=incoming_status,
                        platform_status_code=str(detail.get("api_status") or detail.get("order_status") or ""),
                        platform_status_text=str(detail.get("status_text") or ""),
                        status_source="order_detail",
                        sync_error="" if incoming_status != "unknown" else "无法确认平台订单状态",
                        item_id=detail.get("item_id"),
                        buyer_id=detail.get("buyer_id"),
                        spec_name=detail.get("spec_name"),
                        spec_value=detail.get("spec_value"),
                        quantity=detail.get("quantity"),
                        amount=detail.get("amount"),
                        created_at=detail.get("order_time"),
                        receiver_name=detail.get("receiver_name"),
                        receiver_phone=detail.get("receiver_phone"),
                        receiver_address=detail.get("receiver_address"),
                        receiver_city=detail.get("receiver_city"),
                    )
                    if update_result.get("status_changed"):
                        summary["status_updated"] += 1
                    if update_result.get("details_changed"):
                        summary["details_updated"] += 1
                    if not update_result.get("status_changed") and not update_result.get("details_changed"):
                        summary["unchanged"] += 1
                    self.db.reconcile_order_status_events(
                        cookie_id=cookie_id,
                        order_id=order_id,
                        item_id=str(detail.get("item_id") or ""),
                        buyer_id=str(detail.get("buyer_id") or ""),
                        chat_id=str(detail.get("chat_id") or ""),
                    )

        return {
            "success": summary["failed"] == 0,
            "partial": summary["failed"] > 0 and summary["total_seen"] > summary["failed"],
            "requires_login": False,
            "message": "订单同步完成" if summary["failed"] == 0 else "订单同步部分完成",
            "summary": summary,
            "errors": errors,
        }
