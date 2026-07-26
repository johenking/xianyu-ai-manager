"""Shared order discovery, status normalization and sync coordination."""

import json
import inspect
import time
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

import aiohttp


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
    raw_status = common_data.get("orderStatusCode") or raw.get("status") or raw.get("orderStatus") or raw.get("statusCode") or common_data.get("orderStatus") or ""
    status_text = common_data.get("orderStatus") or raw.get("status_text") or raw.get("statusText") or raw.get("status_desc") or raw.get("statusDesc") or ""
    if str(common_data.get("inRefund") or raw.get("inRefund") or "").lower() == "true":
        status_text = status_text if "退款" in str(status_text) else f"退款中 {status_text}".strip()
    raw_amount = raw.get("amount") or raw.get("payAmount") or raw.get("actualFee") or raw.get("price") or price_info.get("totalPrice") or price_info.get("confirmFee") or price_info.get("auctionPrice") or ""
    amount = str(raw_amount).replace("¥", "").replace("￥", "").replace(",", "").strip()
    return {
        "order_id": str(order_id or ""),
        "item_id": str(common_data.get("itemId") or raw.get("item_id") or raw.get("itemId") or raw.get("auctionId") or ""),
        "buyer_id": str(buyer_info.get("buyerId") or raw.get("buyer_id") or raw.get("buyerId") or raw.get("buyerUserId") or ""),
        "item_title": str(common_data.get("itemTitle") or raw.get("title") or raw.get("itemTitle") or raw.get("subject") or ""),
        # 买家昵称/头像候选链：以 probe_order_buyer_fields.py 对真实响应的探测结果定稿；
        # 全部落空时留空串，写入守卫对空值免疫，不会产生垃圾快照
        "buyer_nickname": str(buyer_info.get("nick") or buyer_info.get("buyerNick") or buyer_info.get("userNick") or raw.get("buyerNick") or ""),
        "buyer_avatar_url": str(buyer_info.get("avatar") or buyer_info.get("headPicUrl") or buyer_info.get("portraitUrl") or buyer_info.get("headPic") or ""),
        "amount": amount,
        "quantity": str(price_info.get("buyNum") or raw.get("quantity") or raw.get("itemNum") or "1"),
        "order_status": normalize_order_status(raw_status, status_text),
        "platform_status_code": str(raw_status or ""),
        "platform_status_text": str(status_text or ""),
        "created_at": common_data.get("createTime") or raw.get("createTime") or raw.get("created_at") or raw.get("gmtCreate"),
        "cookie_id": cookie_id,
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
    if amount < 0 or amount > Decimal("10000000"):
        return None
    return int((amount * 100).to_integral_value(rounding=ROUND_HALF_UP))


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
        max_pages: int = 50,
    ):
        self.page_loader = page_loader
        self.now_fn = now_fn
        self.page_size = max(1, min(int(page_size), 100))
        self.max_pages = max(1, min(int(max_pages), 100))

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

        for page_number in range(1, self.max_pages + 1):
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

        result = {
            "success": True,
            "requires_login": False,
            "orders": orders,
            "pages_scanned": pages_scanned,
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
            summary["total_seen"] += 1
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
                    "source": "order_list" if record_title else "catalog",
                }
            buyer_nickname = str(order.get("buyer_nickname") or "").strip()
            buyer_avatar = str(order.get("buyer_avatar_url") or "").strip()
            buyer_snapshot = None
            if buyer_nickname or buyer_avatar:
                buyer_snapshot = {
                    "buyer_nickname": buyer_nickname,
                    "buyer_avatar_url": buyer_avatar,
                    "source": "order_list",
                }
            ordered_at = parse_order_time_utc(order.get("created_at"))
            update_result = self.db.apply_order_sync_update(
                order_id=order_id,
                cookie_id=cookie_id,
                incoming_status=order.get("order_status") or "unknown",
                platform_status_code=order.get("platform_status_code") or "",
                platform_status_text=order.get("platform_status_text") or "",
                status_source="order_list",
                item_snapshot=item_snapshot,
                buyer_snapshot=buyer_snapshot,
                ordered_at=ordered_at,
                paid_amount_fen=parse_amount_fen(order.get("amount")),
                item_id=order.get("item_id"),
                buyer_id=order.get("buyer_id"),
                quantity=order.get("quantity"),
                amount=order.get("amount"),
                created_at=order.get("created_at"),
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
                    # 详情为最高级快照来源：报文里带什么就升级什么，缺省字段自动跳过
                    detail_item_snapshot = None
                    detail_title = str(detail.get("item_title") or "").strip()
                    detail_image = str(detail.get("item_image") or detail.get("item_pic") or "").strip()
                    if detail_title or detail_image:
                        detail_item_snapshot = {
                            "item_title": detail_title,
                            "item_image": detail_image,
                            "source": "order_detail",
                        }
                    detail_nickname = str(detail.get("buyer_nickname") or detail.get("buyer_nick") or "").strip()
                    detail_avatar = str(detail.get("buyer_avatar_url") or detail.get("buyer_avatar") or "").strip()
                    detail_buyer_snapshot = None
                    if detail_nickname or detail_avatar:
                        detail_buyer_snapshot = {
                            "buyer_nickname": detail_nickname,
                            "buyer_avatar_url": detail_avatar,
                            "source": "order_detail",
                        }
                    detail_ordered_at = parse_order_time_utc(detail.get("order_time"))
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
                        paid_amount_fen=parse_amount_fen(detail.get("amount")),
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

        return {
            "success": summary["failed"] == 0,
            "partial": summary["failed"] > 0 and summary["total_seen"] > summary["failed"],
            "requires_login": False,
            "message": "订单同步完成" if summary["failed"] == 0 else "订单同步部分完成",
            "summary": summary,
            "errors": errors,
        }
