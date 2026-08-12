"""Shared order discovery, status normalization and sync coordination."""

import asyncio
import json
import inspect
import random
import time
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

import aiohttp


_ACCOUNT_SYNC_LOCKS: Dict[str, asyncio.Lock] = {}


def get_order_sync_lock(cookie_id: Any) -> asyncio.Lock:
    """Return the process-local MTOP synchronization lock for one account."""
    return _ACCOUNT_SYNC_LOCKS.setdefault(str(cookie_id or ""), asyncio.Lock())


DEFAULT_ORDER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/138.0.0.0 Safari/537.36"
)


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

# 经营统计“有效订单”口径的唯一定义源：待发货/已发货/已完成。
# dashboard、analytics、驾驶舱等所有统计端点必须引用此常量，禁止再散落硬编码。
VALID_ORDER_STATUSES: Tuple[str, ...] = ("pending_ship", "shipped", "completed")

# 订单快照来源等级棘轮：空则写；非空仅当新来源等级“严格更高”才覆盖。
# 因此目录类来源永远冲不掉已有快照（商品改图/改标题不影响成交记录），
# order_detail 封顶；同级来源（含目录周期重扫）不产生覆盖。
SNAPSHOT_SOURCE_RANK: Dict[str, int] = {
    "": 0,
    "history_unsaved": 0,
    "catalog_metadata": 1,
    "catalog_backfill": 1,
    "catalog": 2,
    "realtime_message": 3,
    "order_list": 4,
    "import": 4,
    "order_detail": 5,
}


def snapshot_source_rank(source: Any) -> int:
    return SNAPSHOT_SOURCE_RANK.get(str(source or ""), 0)

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
    if any(value in text for value in ("待买家确认收货", "卖家已发货", "已发货")):
        return "shipped"
    if any(value in text for value in ("确认收货", "已签收", "交易成功", "交易完成", "订单完成")):
        return "completed"
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
    session_markers = (
        "session_expired",
        "session过期",
        "session expired",
        "token_expired",
        "token_expoired",
        "token_exoired",
        "token expired",
        "token过期",
        "令牌过期",
        "fail_sys_user_validate",
        "fail_sys_session_expired",
        "fail_sys_token_expired",
        "fail_sys_token_expoired",
        "passport.goofish.com",
        "mini_login",
    )
    if any(marker in lowered for marker in session_markers):
        return {
            "code": "session_expired",
            "message": "闲鱼登录状态已过期，请先更新登录状态",
            "requires_login": True,
            "retryable": False,
        }
    permission_markers = (
        "permission_exception",
        "permission denied",
        "access denied",
        "no permission",
        "无权限",
        "权限不足",
        "拒绝访问",
    )
    if any(marker in lowered for marker in permission_markers):
        return {
            "code": "platform_permission_denied",
            "message": "闲鱼订单接口拒绝访问，请在平台确认卖家订单权限",
            "requires_login": False,
            "retryable": False,
        }
    if any(marker in lowered for marker in ("http_429", "too many requests", "rate limit", "限流", "请求频繁")):
        return {
            "code": "rate_limited",
            "message": "闲鱼订单接口请求过于频繁，请稍后重试",
            "requires_login": False,
            "retryable": True,
        }
    if (
        "network_error" in lowered
        or any(f"http_{status}" in lowered for status in range(500, 600))
        or any(marker in lowered for marker in ("service unavailable", "网关", "服务繁忙"))
    ):
        return {
            "code": "platform_unavailable",
            "message": "闲鱼订单接口暂时不可用，请稍后重试",
            "requires_login": False,
            "retryable": True,
        }
    return {
        "code": "platform_error",
        "message": message or "闲鱼订单接口返回未知错误",
        "requires_login": False,
        "retryable": False,
    }


SYNC_COVERAGE_FIELDS: Tuple[str, ...] = (
    "status",
    "item_image",
    "buyer_nickname",
    "buyer_avatar",
    "amount",
    "time",
)


def new_order_sync_summary() -> Dict[str, Any]:
    return {
        "total_seen": 0,
        "discovered": 0,
        "status_updated": 0,
        "details_updated": 0,
        "unchanged": 0,
        "failed": 0,
        "status_unconfirmed": 0,
        "field_coverage": {
            field: {"covered": 0, "total": 0, "rate": 0.0}
            for field in SYNC_COVERAGE_FIELDS
        },
    }


def finalize_order_sync_summary(
    db: Any,
    summary: Dict[str, Any],
    order_ids: Iterable[str],
    unconfirmed_order_ids: Iterable[str],
) -> Dict[str, Any]:
    unique_order_ids = list(dict.fromkeys(str(value) for value in order_ids if value))
    unconfirmed = {str(value) for value in unconfirmed_order_ids if value}
    summary["status_unconfirmed"] = len(unconfirmed)
    summary["failed"] += len(unconfirmed)

    covered = {field: 0 for field in SYNC_COVERAGE_FIELDS}
    for order_id in unique_order_ids:
        row = db.get_order_by_id(order_id) or {}
        if normalize_order_status(row.get("order_status") or row.get("status")) != "unknown":
            covered["status"] += 1
        if str(row.get("item_image") or "").strip():
            covered["item_image"] += 1
        if str(row.get("buyer_nickname") or "").strip():
            covered["buyer_nickname"] += 1
        if str(row.get("buyer_avatar_url") or "").strip():
            covered["buyer_avatar"] += 1
        if row.get("paid_amount_fen") is not None:
            covered["amount"] += 1
        if row.get("ordered_at_utc") is not None:
            covered["time"] += 1

    total = len(unique_order_ids)
    summary["field_coverage"] = {
        field: {
            "covered": covered[field],
            "total": total,
            "rate": round(covered[field] / total, 4) if total else 0.0,
        }
        for field in SYNC_COVERAGE_FIELDS
    }
    return summary


def parse_order_api_payload(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {
            "success": False,
            "error_code": "invalid_response_schema",
            "error": "闲鱼订单接口返回了无法解析的数据",
            "requires_login": False,
            "retryable": False,
        }
    ret_values = payload.get("ret") or []
    if ret_values and not str(ret_values[0]).startswith("SUCCESS"):
        error = classify_platform_error(ret_values)
        return {
            "success": False,
            "error_code": error["code"],
            "error": error["message"],
            "requires_login": error["requires_login"],
            "retryable": bool(error.get("retryable")),
        }
    data = payload.get("data")
    if not isinstance(data, dict):
        return {
            "success": False,
            "error_code": "invalid_response_schema",
            "error": "闲鱼订单接口响应缺少订单数据结构",
            "requires_login": False,
            "retryable": False,
        }
    return {"success": True, "data": data}


def _find_order_list(data: Dict[str, Any]) -> Tuple[bool, List[Dict[str, Any]]]:
    module = data.get("module") if isinstance(data.get("module"), dict) else {}
    candidates = (
        module.get("items"),
        data.get("orders"),
        data.get("orderList"),
        data.get("order_list"),
        data.get("list"),
        data.get("cardList"),
    )
    for candidate in candidates:
        if isinstance(candidate, list):
            if any(not isinstance(row, dict) for row in candidate):
                return False, []
            return True, candidate
    return False, []


def extract_order_list(payload: Any) -> List[Dict[str, Any]]:
    parsed = parse_order_api_payload(payload)
    if not parsed.get("success"):
        return []
    return _find_order_list(parsed.get("data") or {})[1]


def _has_bargain_freeshipping_marker(raw: Dict[str, Any]) -> bool:
    """Read only explicit group-buy action markers from the order controls."""
    common_data = raw.get("commonData") if isinstance(raw.get("commonData"), dict) else {}
    right_vo = raw.get("rightVO") if isinstance(raw.get("rightVO"), dict) else {}
    candidates: List[Any] = []
    for container in (right_vo, common_data, raw):
        for key in ("btnList", "buttonList", "actions", "tags"):
            value = container.get(key) if isinstance(container, dict) else None
            if isinstance(value, list):
                candidates.extend(value)
            elif isinstance(value, (dict, str)):
                candidates.append(value)
    markers = ("免拼", "待刀成", "小刀", "groupon", "bargain_freeshipping")
    for candidate in candidates:
        values = candidate.values() if isinstance(candidate, dict) else (candidate,)
        for value in values:
            normalized = str(value or "").strip().lower()
            if any(marker in normalized for marker in markers):
                return True
    return False


def normalize_order_record(raw: Dict[str, Any], cookie_id: str) -> Dict[str, Any]:
    common_data = raw.get("commonData") if isinstance(raw.get("commonData"), dict) else {}
    buyer_info = raw.get("buyerInfoVO") if isinstance(raw.get("buyerInfoVO"), dict) else {}
    price_info = raw.get("priceVO") if isinstance(raw.get("priceVO"), dict) else {}
    order_id = common_data.get("orderId") or raw.get("order_id") or raw.get("orderId") or raw.get("bizOrderId") or raw.get("mainOrderId") or raw.get("id")
    raw_status = common_data.get("orderStatusCode") or raw.get("status") or raw.get("orderStatus") or raw.get("statusCode") or common_data.get("orderStatus") or ""
    status_text = common_data.get("orderStatus") or raw.get("status_text") or raw.get("statusText") or raw.get("status_desc") or raw.get("statusDesc") or ""
    if str(common_data.get("inRefund") or raw.get("inRefund") or "").lower() == "true":
        status_text = status_text if "退款" in str(status_text) else f"退款中 {status_text}".strip()
    raw_amount = next(
        (
            value
            for value in (
                raw.get("amount"),
                raw.get("payAmount"),
                raw.get("actualFee"),
                raw.get("price"),
                price_info.get("totalPrice"),
                price_info.get("confirmFee"),
                price_info.get("auctionPrice"),
            )
            if value is not None and (not isinstance(value, str) or value.strip())
        ),
        "",
    )
    amount = str(raw_amount).replace("¥", "").replace("￥", "").replace(",", "").strip()
    return {
        "order_id": str(order_id or ""),
        "item_id": str(common_data.get("itemId") or raw.get("item_id") or raw.get("itemId") or raw.get("auctionId") or ""),
        "buyer_id": str(buyer_info.get("buyerId") or raw.get("buyer_id") or raw.get("buyerId") or raw.get("buyerUserId") or ""),
        "item_title": str(common_data.get("itemTitle") or raw.get("title") or raw.get("itemTitle") or raw.get("subject") or ""),
        # 昵称和头像字段尚未通过真实账号响应验收；宁可留空，也不猜字段语义。
        "buyer_nickname": "",
        "buyer_avatar_url": "",
        "amount": amount,
        "quantity": str(price_info.get("buyNum") or raw.get("quantity") or raw.get("itemNum") or ""),
        "order_status": normalize_order_status(raw_status, status_text),
        "platform_status_code": str(raw_status or ""),
        "platform_status_text": str(status_text or ""),
        "created_at": common_data.get("createTime") or raw.get("createTime") or raw.get("created_at") or raw.get("gmtCreate"),
        "cookie_id": cookie_id,
        "is_bargain": _has_bargain_freeshipping_marker(raw),
    }


_AMOUNT_STRIP_TABLE = str.maketrans("", "", "¥￥$,， \t ")


def parse_amount_fen(value: Any) -> Optional[int]:
    """把平台金额文本解析为整数分。

    剥离半/全角货币符与千分位后用 Decimal 精确换算；
    空值、负数、非数字、超 1000 万元的垃圾值一律返回 None——
    绝不用 0 冒充缺失，避免“免费”与“未知”在统计里混淆。
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        text = repr(float(value))
    else:
        text = str(value)
    text = text.strip().translate(_AMOUNT_STRIP_TABLE)
    if not text:
        return None
    try:
        amount = Decimal(text)
    except InvalidOperation:
        return None
    if not amount.is_finite():
        return None
    if amount < 0 or amount > Decimal("10000000"):
        return None
    return int((amount * 100).to_integral_value(rounding=ROUND_HALF_UP))


def parse_trusted_order_quantity(value: Any) -> Optional[int]:
    """Accept only bounded positive integer quantities from verified order data."""
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text or not text.isascii() or not text.isdigit():
        return None
    quantity = int(text)
    return quantity if 1 <= quantity <= 100 else None


def parse_order_time_utc(
    value: Any, assume_tz: str = "Asia/Shanghai"
) -> Tuple[Optional[float], str]:
    """把平台订单时间解析为 UTC epoch 秒，返回 (epoch, 出处)。

    出处枚举：epoch（纯数字，>10^10 视为毫秒）/ cst_string（无时区字符串，
    按 assume_tz 解释——平台列表与详情返回的都是北京时间）/ iso_string
    （自带时区的 ISO）/ unparseable（解析失败，epoch 为 None）。
    历史回填对疑似 UTC 默认值的行使用 backfill_cst_assumed 单独标注。
    """
    if value in (None, ""):
        return None, ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        return (number / 1000 if number > 10_000_000_000 else number), "epoch"
    text = str(value).strip()
    if not text:
        return None, ""
    if text.isdigit():
        number = float(text)
        return (number / 1000 if number > 10_000_000_000 else number), "epoch"
    zone = ZoneInfo(assume_tz)
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S"):
        try:
            parsed = datetime.strptime(text, pattern)
        except ValueError:
            continue
        return parsed.replace(tzinfo=zone).timestamp(), "cst_string"
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None, "unparseable"
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=zone).timestamp(), "cst_string"
    return parsed.timestamp(), "iso_string"


def _parse_order_timestamp(value: Any) -> Optional[float]:
    return parse_order_time_utc(value)[0]


async def fetch_xianyu_order_list_page(
    *,
    cookie_id: str,
    cookie_string: str,
    page_number: int,
    page_size: int,
    user_id: str,
    user_agent: str = "",
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
        "User-Agent": str(user_agent or DEFAULT_ORDER_USER_AGENT),
    }
    timeout = aiohttp.ClientTimeout(total=20)
    try:
        async with aiohttp.ClientSession(
            timeout=timeout,
            cookie_jar=aiohttp.DummyCookieJar(),
        ) as session:
            async with session.post(
                "https://h5api.m.goofish.com/h5/mtop.taobao.idle.trade.merchant.sold.get/1.0/",
                params=params,
                data={"data": data_value},
                headers=headers,
            ) as response:
                cookie_updates = {}
                try:
                    set_cookie_values = response.headers.getall("Set-Cookie", [])
                except Exception:
                    set_cookie_values = []
                for raw_cookie in set_cookie_values:
                    first_segment = str(raw_cookie).split(";", 1)[0]
                    if "=" not in first_segment:
                        continue
                    name, value = first_segment.split("=", 1)
                    if name.strip():
                        cookie_updates[name.strip()] = value.strip()
                if response.status >= 400:
                    return {"ret": [f"HTTP_{response.status}::订单接口请求失败"]}
                payload = await response.json(content_type=None)
                if isinstance(payload, dict) and cookie_updates:
                    payload["_cookie_updates"] = cookie_updates
                return payload
    except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
        return {"ret": [f"NETWORK_ERROR::{type(exc).__name__}"]}


class XianyuOrderListClient:
    """Discover recent seller orders through the paginated platform endpoint."""

    def __init__(
        self,
        page_loader: Callable[..., Awaitable[Dict[str, Any]]] = fetch_xianyu_order_list_page,
        now_fn: Callable[[], float] = time.time,
        page_size: int = 20,
        max_pages: int = 20,
        max_orders: int = 400,
        request_interval: Optional[float] = None,
        max_retries: int = 2,
        sleep_fn: Callable[[float], Awaitable[Any]] = asyncio.sleep,
        jitter_fn: Callable[[float, float], float] = random.uniform,
    ):
        self.page_loader = page_loader
        self.now_fn = now_fn
        self.page_size = max(1, min(int(page_size), 100))
        self.max_pages = max(1, min(int(max_pages), 100))
        self.max_orders = max(1, min(int(max_orders), 2000))
        self.request_interval = max(
            0.0,
            float(
                0.8
                if request_interval is None and page_loader is fetch_xianyu_order_list_page
                else request_interval or 0.0
            ),
        )
        self.max_retries = max(0, min(int(max_retries), 4))
        self.sleep_fn = sleep_fn
        self.jitter_fn = jitter_fn

    @staticmethod
    def _merge_cookie_updates(cookie_string: str, updates: Dict[str, Any]) -> str:
        from utils.xianyu_utils import trans_cookies

        merged = dict(trans_cookies(cookie_string))
        for name, value in (updates or {}).items():
            normalized_name = str(name or '').strip()
            if not normalized_name:
                continue
            normalized_value = str(value or '')
            if normalized_value:
                merged[normalized_name] = normalized_value
            else:
                merged.pop(normalized_name, None)
        return '; '.join(f"{name}={value}" for name, value in merged.items())

    async def discover(
        self,
        *,
        cookie_id: str,
        cookie_string: str,
        days: int = 90,
        user_agent: str = "",
        target_order_id: str = "",
    ) -> Dict[str, Any]:
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
        current_cookie_string = cookie_string
        cookie_changed = False
        has_next_page = False

        for page_number in range(1, self.max_pages + 1):
            if page_number > 1 and self.request_interval:
                await self.sleep_fn(self.request_interval)
            retry_count = 0
            while True:
                payload = await self.page_loader(
                    cookie_id=cookie_id,
                    cookie_string=current_cookie_string,
                    page_number=page_number,
                    page_size=self.page_size,
                    user_id=user_id,
                    user_agent=user_agent,
                )
                cookie_updates = (
                    payload.get("_cookie_updates")
                    if isinstance(payload, dict) and isinstance(payload.get("_cookie_updates"), dict)
                    else {}
                )
                if cookie_updates:
                    merged_cookie_string = self._merge_cookie_updates(
                        current_cookie_string,
                        cookie_updates,
                    )
                    merged_user_id = str(trans_cookies(merged_cookie_string).get("unb") or "")
                    if merged_user_id != user_id:
                        return {
                            "success": False,
                            "error_code": "account_identity_mismatch",
                            "error": "订单接口返回的登录身份与当前账号不一致",
                            "requires_login": True,
                            "orders": orders,
                            "pages_scanned": pages_scanned,
                        }
                    if merged_cookie_string != current_cookie_string:
                        current_cookie_string = merged_cookie_string
                        cookie_changed = True

                parsed = parse_order_api_payload(payload)
                if parsed.get("success"):
                    break
                if parsed.get("requires_login") and cookie_updates and retry_count == 0:
                    retry_count += 1
                    continue
                if parsed.get("retryable") and retry_count < self.max_retries:
                    delay = min(8.0, 0.75 * (2 ** retry_count))
                    delay += max(0.0, float(self.jitter_fn(0.0, 0.35)))
                    retry_count += 1
                    await self.sleep_fn(delay)
                    continue
                return {
                    "success": False,
                    "error_code": parsed.get("error_code") or "platform_error",
                    "error": parsed.get("error") or "订单列表获取失败",
                    "requires_login": bool(parsed.get("requires_login")),
                    "orders": orders,
                    "pages_scanned": pages_scanned,
                }

            pages_scanned += 1
            response_data = parsed.get("data") or {}
            list_found, raw_orders = _find_order_list(response_data)
            if not list_found:
                return {
                    "success": False,
                    "error_code": "invalid_response_schema",
                    "error": "闲鱼订单接口响应缺少订单列表结构",
                    "requires_login": False,
                    "orders": orders,
                    "pages_scanned": pages_scanned,
                }
            response_module = response_data.get("module") if isinstance(response_data.get("module"), dict) else {}
            next_page_value = response_module.get("nextPage")
            has_next_page = (
                str(next_page_value).lower() == "true"
                if next_page_value is not None
                else len(raw_orders) >= self.page_size
            )
            page_timestamps: List[Optional[float]] = []
            for raw_order in raw_orders:
                order = normalize_order_record(raw_order, cookie_id)
                created_timestamp = _parse_order_timestamp(order.get("created_at"))
                page_timestamps.append(created_timestamp)
                if created_timestamp is not None and created_timestamp < cutoff:
                    continue
                order_id = order.get("order_id")
                if not order_id or order_id in seen_order_ids:
                    continue
                seen_order_ids.add(order_id)
                orders.append(order)
                if target_order_id and order_id == target_order_id:
                    break
                if len(orders) >= self.max_orders:
                    break

            page_entirely_before_cutoff = bool(page_timestamps) and all(
                timestamp is not None and timestamp < cutoff
                for timestamp in page_timestamps
            )
            if (
                (target_order_id and target_order_id in seen_order_ids)
                or len(orders) >= self.max_orders
                or page_entirely_before_cutoff
                or not has_next_page
                or not raw_orders
            ):
                break

        result = {
            "success": True,
            "requires_login": False,
            "orders": orders,
            "pages_scanned": pages_scanned,
            "truncated": bool(
                has_next_page
                and not (target_order_id and target_order_id in seen_order_ids)
                and (pages_scanned >= self.max_pages or len(orders) >= self.max_orders)
            ),
        }
        if cookie_changed:
            result["updated_cookie_string"] = current_cookie_string
        return result


class OrderSyncCoordinator:
    """Apply recent platform order discovery with truthful per-account summaries."""

    def __init__(self, db, discoverer: Callable[..., Awaitable[Dict[str, Any]]],
                 detail_fetcher: Optional[Callable[..., Awaitable[List[Dict[str, Any]]]]] = None,
                 cookie_updater: Optional[Callable[[str, str], Any]] = None,
                 now_fn: Callable[[], float] = time.time):
        self.db = db
        self.discoverer = discoverer
        self.detail_fetcher = detail_fetcher
        self.cookie_updater = cookie_updater
        self.now_fn = now_fn

    async def sync_account(
        self,
        cookie_id: str,
        cookie_string: str,
        days: int = 90,
        user_agent: str = "",
    ) -> Dict[str, Any]:
        lock = get_order_sync_lock(cookie_id)
        async with lock:
            return await self._sync_account_unlocked(
                cookie_id=cookie_id,
                cookie_string=cookie_string,
                days=days,
                user_agent=user_agent,
            )

    async def _sync_account_unlocked(
        self,
        cookie_id: str,
        cookie_string: str,
        days: int = 90,
        user_agent: str = "",
    ) -> Dict[str, Any]:
        summary = new_order_sync_summary()
        touched_order_ids: List[str] = []
        unconfirmed_order_ids: set[str] = set()
        obtained_fields: set[str] = set()
        discovery = await self.discoverer(
            cookie_id=cookie_id,
            cookie_string=cookie_string,
            days=max(1, min(int(days or 90), 365)),
            user_agent=user_agent,
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

        updated_cookie_string = str(discovery.pop("updated_cookie_string", "") or "")
        if updated_cookie_string and updated_cookie_string != cookie_string:
            if self.cookie_updater:
                try:
                    update_result = self.cookie_updater(cookie_id, updated_cookie_string)
                    if inspect.isawaitable(update_result):
                        update_result = await update_result
                    if update_result is False:
                        raise RuntimeError("Cookie 持久化返回失败")
                except Exception:
                    return {
                        "success": False,
                        "partial": False,
                        "requires_login": False,
                        "error_code": "cookie_update_failed",
                        "message": "订单接口刷新了登录状态，但本地保存失败",
                        "summary": summary,
                        "errors": ["订单同步 Cookie 保存失败"],
                    }
            cookie_string = updated_cookie_string

        errors = []
        sync_limit_reached = bool(discovery.get("truncated"))
        if sync_limit_reached:
            errors.append("订单同步达到本轮请求上限，请缩短时间范围后重试")
        # 成交时从商品目录快照主图，避免商品后续下架导致订单图片失联
        try:
            catalog_lookup = self.db.get_item_catalog_lookup([cookie_id])
        except Exception:
            catalog_lookup = {}
        for discovered_order in discovery.get("orders") or []:
            order = dict(discovered_order)
            order_id = str(order.get("order_id") or "")
            if not order_id:
                summary["failed"] += 1
                errors.append("订单列表包含缺少订单号的记录")
                continue
            touched_order_ids.append(order_id)
            summary["total_seen"] += 1
            incoming_list_status = normalize_order_status(
                order.get("order_status"),
                order.get("platform_status_text") or "",
            )
            if incoming_list_status == "unknown":
                unconfirmed_order_ids.add(order_id)
            else:
                unconfirmed_order_ids.discard(order_id)
                obtained_fields.add("status")
            # 用成交商品 ID 关联当前目录主图，取不到则留空（不覆盖已有快照）
            catalog_item = catalog_lookup.get((str(cookie_id), str(order.get("item_id") or "")))
            catalog_image = (catalog_item or {}).get("item_image") or None
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
                    is_bargain=bool(order.get("is_bargain")),
                    created_at=order.get("created_at") or None,
                    item_image=catalog_image,
                )
                if not inserted:
                    summary["failed"] += 1
                    errors.append("订单写入失败")
                    continue
                summary["discovered"] += 1

            # 成交快照与规范化字段：标题以订单报文优先（目录仅兜底），
            # 图片当前只有目录来源；组来源按主导字段定级，棘轮防目录周期重扫回冲
            record_title = str(order.get("item_title") or "").strip()
            catalog_title = str((catalog_item or {}).get("item_title") or "").strip()
            item_snapshot = None
            if record_title or catalog_title or catalog_image:
                item_snapshot = {
                    "item_title": record_title or catalog_title,
                    "item_image": catalog_image or "",
                    "item_title_source": "order_list" if record_title else "catalog",
                    "item_image_source": "catalog",
                }
            buyer_nickname = str(order.get("buyer_nickname") or "").strip()
            buyer_avatar = str(order.get("buyer_avatar_url") or "").strip()
            if buyer_nickname:
                obtained_fields.add("buyer_nickname")
            if buyer_avatar:
                obtained_fields.add("buyer_avatar")
            buyer_snapshot = None
            if buyer_nickname or buyer_avatar:
                buyer_snapshot = {
                    "buyer_nickname": buyer_nickname,
                    "buyer_avatar_url": buyer_avatar,
                    "source": "order_list",
                }
            ordered_at = parse_order_time_utc(order.get("created_at"))
            if ordered_at[0] is not None:
                obtained_fields.add("time")
            paid_amount_fen = parse_amount_fen(order.get("amount"))
            if paid_amount_fen is not None:
                obtained_fields.add("amount")
            update_result = self.db.apply_order_sync_update(
                order_id=order_id,
                cookie_id=cookie_id,
                incoming_status=order.get("order_status") or "unknown",
                platform_status_code=order.get("platform_status_code") or "",
                platform_status_text=order.get("platform_status_text") or "",
                status_source="order_list",
                sync_error=(
                    "无法确认平台订单状态"
                    if incoming_list_status == "unknown"
                    else ""
                ),
                item_snapshot=item_snapshot,
                buyer_snapshot=buyer_snapshot,
                ordered_at=ordered_at,
                paid_amount_fen=paid_amount_fen,
                item_id=order.get("item_id"),
                buyer_id=order.get("buyer_id"),
                quantity=order.get("quantity"),
                amount=order.get("amount"),
                created_at=order.get("created_at"),
            )
            if order.get("is_bargain"):
                self.db.insert_or_update_order(
                    order_id=order_id,
                    cookie_id=cookie_id,
                    is_bargain=True,
                )
            buyer_id = str(order.get("buyer_id") or "").strip()
            if buyer_id:
                self.db.upsert_customer_observation(
                    cookie_id=cookie_id,
                    buyer_id=buyer_id,
                    display_name=buyer_nickname,
                    avatar_url=buyer_avatar,
                    source="order_list",
                    observed_at=ordered_at[0] if ordered_at[0] is not None else self.now_fn(),
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
                status = normalize_order_status(
                    order.get("order_status") or order.get("status")
                )
                created_timestamp = _parse_order_timestamp(order.get("created_at"))
                if created_timestamp is not None and created_timestamp < cutoff:
                    continue
                needs_detail = any((
                    status == "unknown",
                    bool(str(order.get("last_sync_error") or "").strip()),
                    not str(order.get("item_image") or "").strip(),
                    not str(order.get("buyer_nickname") or "").strip(),
                    not str(order.get("buyer_avatar_url") or "").strip(),
                    order.get("paid_amount_fen") is None,
                    order.get("ordered_at_utc") is None,
                ))
                if not needs_detail:
                    continue
                order_id = str(order.get("order_id") or "")
                if order_id:
                    detail_order_ids.append(order_id)
                if len(detail_order_ids) >= 20:
                    break

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
                            "summary": finalize_order_sync_summary(
                                self.db,
                                summary,
                                touched_order_ids,
                                unconfirmed_order_ids,
                            ),
                            "fields_obtained": [
                                field
                                for field in SYNC_COVERAGE_FIELDS
                                if field in obtained_fields
                            ],
                            "errors": errors + [detail.get("error") or "闲鱼登录状态已过期"],
                        }
                    if not order_id:
                        summary["failed"] += 1
                        errors.append(detail.get("error") or "订单详情缺少订单号")
                        continue
                    touched_order_ids.append(order_id)
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
                    if incoming_status == "unknown":
                        unconfirmed_order_ids.add(order_id)
                    else:
                        unconfirmed_order_ids.discard(order_id)
                        obtained_fields.add("status")
                    # 详情为最高级快照来源：报文里带什么就升级什么，缺省字段自动跳过
                    detail_item_snapshot = None
                    detail_title = str(detail.get("item_title") or "").strip()
                    detail_image = str(detail.get("item_image") or detail.get("item_pic") or "").strip()
                    if detail_image:
                        obtained_fields.add("item_image")
                    if detail_title or detail_image:
                        detail_item_snapshot = {
                            "item_title": detail_title,
                            "item_image": detail_image,
                            "source": "order_detail",
                        }
                    detail_nickname = str(detail.get("buyer_nickname") or detail.get("buyer_nick") or "").strip()
                    detail_avatar = str(detail.get("buyer_avatar_url") or detail.get("buyer_avatar") or "").strip()
                    if detail_nickname:
                        obtained_fields.add("buyer_nickname")
                    if detail_avatar:
                        obtained_fields.add("buyer_avatar")
                    detail_buyer_snapshot = None
                    if detail_nickname or detail_avatar:
                        detail_buyer_snapshot = {
                            "buyer_nickname": detail_nickname,
                            "buyer_avatar_url": detail_avatar,
                            "source": "order_detail",
                        }
                    detail_ordered_at = parse_order_time_utc(detail.get("order_time"))
                    if detail_ordered_at[0] is not None:
                        obtained_fields.add("time")
                    detail_amount_fen = parse_amount_fen(detail.get("amount"))
                    if detail_amount_fen is not None:
                        obtained_fields.add("amount")
                    update_result = self.db.apply_order_sync_update(
                        order_id=order_id,
                        cookie_id=cookie_id,
                        incoming_status=incoming_status,
                        platform_status_code=str(detail.get("api_status") or detail.get("order_status") or ""),
                        platform_status_text=str(detail.get("status_text") or ""),
                        status_source="order_detail",
                        sync_error="" if incoming_status != "unknown" else "无法确认平台订单状态",
                        item_snapshot=detail_item_snapshot,
                        buyer_snapshot=detail_buyer_snapshot,
                        ordered_at=detail_ordered_at,
                        paid_amount_fen=detail_amount_fen,
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
                    detail_buyer_id = str(detail.get("buyer_id") or "").strip()
                    if detail_buyer_id and (detail_nickname or detail_avatar):
                        self.db.upsert_customer_observation(
                            cookie_id=cookie_id,
                            buyer_id=detail_buyer_id,
                            display_name=detail_nickname,
                            avatar_url=detail_avatar,
                            source="order_detail",
                            observed_at=detail_ordered_at[0]
                            if detail_ordered_at[0] is not None else self.now_fn(),
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

        finalize_order_sync_summary(
            self.db,
            summary,
            touched_order_ids,
            unconfirmed_order_ids,
        )
        success = summary["failed"] == 0 and not sync_limit_reached
        partial = not success and bool(touched_order_ids)
        return {
            "success": success,
            "partial": partial,
            "requires_login": False,
            "error_code": "status_unconfirmed"
            if summary["status_unconfirmed"]
            else "sync_limit_reached" if sync_limit_reached
            else "sync_partial_failure" if not success else "",
            "message": "订单同步完成" if success else "订单同步部分完成",
            "summary": summary,
            "fields_obtained": [
                field
                for field in SYNC_COVERAGE_FIELDS
                if field in obtained_fields
            ],
            "errors": errors,
        }
