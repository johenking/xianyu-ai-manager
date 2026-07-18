"""Fail-closed, offline-testable MTop search adapter for Skill Center.

The runtime path is intentionally disconnected from monitor execution until a
dedicated test account is approved and shadow acceptance is complete. The
adapter never persists or logs full responses, Cookies, signatures, or raw
account identifiers.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import timezone
from email.utils import parsedate_to_datetime
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional, Protocol, Sequence
from urllib.parse import urlparse

import requests


MTOP_SEARCH_API = "mtop.taobao.idlemtopsearch.pc.search"
MTOP_SEARCH_VERSION = "1.0"
MTOP_SEARCH_URL = (
    "https://h5api.m.goofish.com/h5/"
    "mtop.taobao.idlemtopsearch.pc.search/1.0/"
)
MTOP_NETWORK_ENV = "SKILL_MONITOR_MTOP_NETWORK_ALLOWED"

TOKEN_EXPIRED_MARKERS = (
    "FAIL_SYS_TOKEN_EXOIRED",
    "FAIL_SYS_TOKEN_EXPIRED",
    "FAIL_SYS_TOKEN_EMPTY",
)
RISK_CONTROL_MARKERS = (
    "FAIL_SYS_USER_VALIDATE",
    "FAIL_SYS_ILLEGAL_ACCESS",
    "FAIL_BIZ_WUA_IS_MACHINE",
    "WUA_IS_MACHINE",
    "RGV587",
    "PUNISH",
    "CAPTCHA",
    "VALIDATE",
    "挤爆",
)
SESSION_EXPIRED_MARKERS = (
    "FAIL_SYS_SESSION_EXPIRED",
    "SESSION_EXPIRED",
)

CANARY_QUERY = {
    "keyword": "iPhone 15 Pro",
    "sort": "latest",
    "region": "",
    "min_price": None,
    "max_price": None,
    "pages": 1,
    "verification": "unverified",
}


class MTopAdapterError(RuntimeError):
    """Safe adapter error carrying only an allowlisted error code."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retry_after: float = 0.0,
        action_required: bool = False,
    ) -> None:
        self.code = str(code or "mtop_error")[:80]
        self.safe_message = str(message or "MTop 搜索失败")[:300]
        self.retry_after = max(0.0, float(retry_after or 0.0))
        self.action_required = bool(action_required)
        super().__init__(self.safe_message)


@dataclass(frozen=True)
class MTopAdapterLimits:
    page_size: int = 30
    max_pages: int = 3
    max_results: int = 90
    max_runtime_seconds: float = 45.0
    request_timeout_seconds: float = 15.0
    max_attempts_per_page: int = 3
    max_response_bytes: int = 1_000_000
    global_requests_per_window: int = 30
    account_requests_per_window: int = 6
    budget_window_seconds: int = 60
    base_backoff_seconds: float = 0.5
    max_backoff_seconds: float = 10.0
    failure_threshold: int = 3
    failure_cooldown_seconds: int = 3600
    probe_lease_seconds: int = 60

    def normalized(self) -> "MTopAdapterLimits":
        return MTopAdapterLimits(
            page_size=max(1, min(int(self.page_size), 50)),
            max_pages=max(1, min(int(self.max_pages), 10)),
            max_results=max(1, min(int(self.max_results), 500)),
            max_runtime_seconds=max(1.0, min(float(self.max_runtime_seconds), 300.0)),
            request_timeout_seconds=max(1.0, min(float(self.request_timeout_seconds), 60.0)),
            max_attempts_per_page=max(1, min(int(self.max_attempts_per_page), 4)),
            max_response_bytes=max(1024, min(int(self.max_response_bytes), 5_000_000)),
            global_requests_per_window=max(1, min(int(self.global_requests_per_window), 1000)),
            account_requests_per_window=max(1, min(int(self.account_requests_per_window), 1000)),
            budget_window_seconds=max(1, min(int(self.budget_window_seconds), 3600)),
            base_backoff_seconds=max(0.0, min(float(self.base_backoff_seconds), 10.0)),
            max_backoff_seconds=max(0.1, min(float(self.max_backoff_seconds), 60.0)),
            failure_threshold=max(1, min(int(self.failure_threshold), 20)),
            failure_cooldown_seconds=max(
                60, min(int(self.failure_cooldown_seconds), 86400)
            ),
            probe_lease_seconds=max(15, min(int(self.probe_lease_seconds), 600)),
        )


@dataclass(frozen=True)
class MTopSearchQuery:
    keyword: str
    sort: str = "latest"
    region: str = ""
    min_price: Optional[float] = None
    max_price: Optional[float] = None
    start_page: int = 1
    pages: int = 1

    def normalized(self, limits: MTopAdapterLimits) -> "MTopSearchQuery":
        keyword = " ".join(
            unicodedata.normalize("NFKC", str(self.keyword or "")).split()
        )
        if not keyword or len(keyword) > 120:
            raise MTopAdapterError("invalid_query", "搜索关键词长度必须为 1-120 个字符")
        sort = str(self.sort or "latest").strip().lower()
        if sort not in {"latest", "relevance", "price_asc", "price_desc"}:
            raise MTopAdapterError("invalid_query", "搜索排序字段不受支持")
        region = " ".join(
            unicodedata.normalize("NFKC", str(self.region or "")).split()
        )[:100]
        try:
            min_price = None if self.min_price is None else round(float(self.min_price), 2)
            max_price = None if self.max_price is None else round(float(self.max_price), 2)
            start_page = int(self.start_page)
            pages = int(self.pages)
        except (TypeError, ValueError) as exc:
            raise MTopAdapterError("invalid_query", "搜索分页或价格参数无效") from exc
        if min_price is not None and min_price < 0:
            raise MTopAdapterError("invalid_query", "最低价格不能小于 0")
        if max_price is not None and max_price < 0:
            raise MTopAdapterError("invalid_query", "最高价格不能小于 0")
        if min_price is not None and max_price is not None and min_price > max_price:
            raise MTopAdapterError("invalid_query", "最低价格不能高于最高价格")
        if start_page < 1 or pages < 1 or pages > limits.max_pages:
            raise MTopAdapterError("invalid_query", "搜索页数超过安全上限")
        return MTopSearchQuery(
            keyword=keyword,
            sort=sort,
            region=region,
            min_price=min_price,
            max_price=max_price,
            start_page=start_page,
            pages=pages,
        )


@dataclass(frozen=True)
class MTopTransportRequest:
    url: str
    params: Mapping[str, str] = field(repr=False)
    form_data: Mapping[str, str] = field(repr=False)
    headers: Mapping[str, str] = field(repr=False)
    cookie_value: str = field(repr=False)
    timeout_seconds: float = 15.0
    max_response_bytes: int = 1_000_000


@dataclass(frozen=True)
class MTopTransportResponse:
    status_code: int
    body: bytes = field(repr=False)
    headers: Mapping[str, str] = field(default_factory=dict)
    refreshed_cookie: str = field(default="", repr=False)
    network_observed: bool = False


@dataclass(frozen=True)
class NormalizedMonitorItem:
    item_id: str
    title: str
    price: Optional[float]
    region: str
    item_url: str
    item_image: str
    seller_name: str
    publish_time: str
    published_at_ms: Optional[int]
    want_count: str
    source_rank: int

    def public_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MTopSearchResult:
    items: Sequence[NormalizedMonitorItem]
    pages_requested: int
    raw_item_count: int
    legal_empty: bool
    stopped_reason: str
    source: str = "mtop"
    is_real_data: bool = False

    def public_dict(self) -> Dict[str, Any]:
        return {
            "items": [item.public_dict() for item in self.items],
            "pages_requested": self.pages_requested,
            "raw_item_count": self.raw_item_count,
            "legal_empty": self.legal_empty,
            "stopped_reason": self.stopped_reason,
            "source": self.source,
            "is_real_data": self.is_real_data,
        }


class MTopTransport(Protocol):
    async def send(self, request: MTopTransportRequest) -> MTopTransportResponse:
        ...


class MTopRuntimeStore(Protocol):
    def get_owned_cookie_search_context(self, user_id: int, cookie_id: str) -> Dict[str, Any]:
        ...

    def compare_and_swap_cookie_session(self, cookie_id: str, **kwargs: Any) -> Dict[str, Any]:
        ...

    def claim_skill_monitor_request_budget(self, user_id: int, account_id: str, **kwargs: Any) -> Dict[str, Any]:
        ...

    def claim_skill_monitor_mtop_circuit_probe(self, user_id: int, account_id: str, **kwargs: Any) -> Dict[str, Any]:
        ...

    def record_skill_monitor_mtop_circuit_outcome(self, user_id: int, account_id: str, **kwargs: Any) -> Dict[str, Any]:
        ...


class RequestsMTopTransport:
    """Fixed-endpoint transport. Construction alone never performs I/O."""

    async def send(self, request: MTopTransportRequest) -> MTopTransportResponse:
        if request.url != MTOP_SEARCH_URL:
            raise MTopAdapterError("endpoint_rejected", "MTop 请求目标不在允许清单")
        return await asyncio.to_thread(self._send_sync, request)

    @staticmethod
    def _send_sync(request: MTopTransportRequest) -> MTopTransportResponse:
        response = requests.post(
            request.url,
            params=dict(request.params),
            data=dict(request.form_data),
            headers=dict(request.headers),
            timeout=request.timeout_seconds,
            allow_redirects=False,
            stream=True,
        )
        try:
            chunks: List[bytes] = []
            total = 0
            for chunk in response.iter_content(chunk_size=65536):
                if not chunk:
                    continue
                total += len(chunk)
                if total > request.max_response_bytes:
                    raise MTopAdapterError("response_too_large", "MTop 响应超过安全上限")
                chunks.append(chunk)
            updates = response.cookies.get_dict()
            refreshed_cookie = ""
            if updates:
                from utils.xianyu_utils import trans_cookies

                merged = trans_cookies(request.cookie_value)
                merged.update({str(key): str(value) for key, value in updates.items()})
                refreshed_cookie = "; ".join(
                    f"{key}={value}" for key, value in merged.items()
                )
            safe_headers = {
                key.lower(): value
                for key, value in response.headers.items()
                if key.lower() in {"retry-after", "content-type"}
            }
            return MTopTransportResponse(
                status_code=int(response.status_code),
                body=b"".join(chunks),
                headers=safe_headers,
                refreshed_cookie=refreshed_cookie,
                network_observed=True,
            )
        finally:
            response.close()


def _env_true(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def runtime_mtop_gate_state(
    database: Optional[Any] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    from skill_monitor_features import get_skill_monitor_feature_state

    feature_state = get_skill_monitor_feature_state(database)
    environment = environ if environ is not None else os.environ
    network_allowed = _env_true(environment.get(MTOP_NETWORK_ENV))
    master_enabled = bool(feature_state["effective"].get("skill_monitor_enabled"))
    mtop_enabled = bool(feature_state["effective"].get("skill_monitor_mtop_enabled"))
    return {
        "master_enabled": master_enabled,
        "mtop_enabled": mtop_enabled,
        "network_allowed": network_allowed,
        "executable": bool(master_enabled and mtop_enabled and network_allowed),
        "fail_closed": True,
    }


def get_mtop_offline_contract_status(
    database: Optional[Any] = None,
    environ: Optional[Mapping[str, str]] = None,
    limits: Optional[MTopAdapterLimits] = None,
) -> Dict[str, Any]:
    normalized_limits = (limits or MTopAdapterLimits()).normalized()
    return {
        "contract_version": "stage-c-offline-v1",
        "code_present": True,
        "gate": runtime_mtop_gate_state(database, environ),
        "limits": asdict(normalized_limits),
        "canary": dict(CANARY_QUERY),
        "real_acceptance": {
            "state": "blocked",
            "blocker_code": "dedicated_test_account_required",
            "shadow_verified": False,
            "value_verified": False,
        },
        "evidence_scope": "code_and_configuration_only",
    }


def _parse_retry_after(value: Any, *, now: float, maximum: float) -> Optional[float]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return max(0.0, min(float(text), maximum))
    except ValueError:
        pass
    try:
        parsed = parsedate_to_datetime(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0.0, min(parsed.timestamp() - now, maximum))
    except (TypeError, ValueError, OverflowError):
        return None


def _price_value(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    text = str(value).replace("¥", "").replace("￥", "").replace(",", "").strip()
    multiplier = 10000.0 if text.endswith("万") else 1.0
    if multiplier != 1.0:
        text = text[:-1].strip()
    try:
        return round(float(text) * multiplier, 2)
    except (TypeError, ValueError):
        return None


def _safe_image_url(value: Any) -> str:
    url = str(value or "").strip()[:2000]
    if url.startswith("//"):
        url = f"https:{url}"
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return url


def _extract_item(entry: Any, source_rank: int) -> Optional[NormalizedMonitorItem]:
    if not isinstance(entry, dict):
        raise MTopAdapterError("response_schema_invalid", "MTop 商品卡片结构无效")
    data = entry.get("data")
    item = data.get("item") if isinstance(data, dict) else None
    main = item.get("main") if isinstance(item, dict) else None
    if not isinstance(main, dict):
        return None
    content = main.get("exContent")
    click_param = main.get("clickParam")
    click_args = click_param.get("args") if isinstance(click_param, dict) else None
    if not isinstance(content, dict):
        return None
    if not isinstance(click_args, dict):
        click_args = {}
    detail_params = content.get("detailParams")
    if not isinstance(detail_params, dict):
        detail_params = {}

    item_id = str(
        content.get("itemId")
        or click_args.get("item_id")
        or click_args.get("id")
        or ""
    ).strip()[:256]
    if not item_id:
        return None
    title = str(content.get("title") or detail_params.get("title") or "").strip()[:500]
    if not title:
        return None
    raw_price = (
        click_args.get("price")
        or click_args.get("displayPrice")
        or detail_params.get("soldPrice")
    )
    if raw_price in (None, ""):
        segments = content.get("price")
        if isinstance(segments, list):
            raw_price = "".join(
                str(segment.get("text") or "")
                for segment in segments
                if isinstance(segment, dict)
            )
        elif isinstance(segments, (str, int, float)):
            raw_price = segments
    publish_time = str(click_args.get("publishTime") or "").strip()[:80]
    published_at_ms: Optional[int] = None
    if publish_time.isdigit():
        number = int(publish_time)
        published_at_ms = number if number > 10_000_000_000 else number * 1000
    return NormalizedMonitorItem(
        item_id=item_id,
        title=title,
        price=_price_value(raw_price),
        region=str(content.get("area") or "").strip()[:200],
        item_url=f"https://www.goofish.com/item?id={item_id}",
        item_image=_safe_image_url(content.get("picUrl")),
        seller_name=str(
            content.get("userNickName") or detail_params.get("userNick") or ""
        ).strip()[:300],
        publish_time=publish_time,
        published_at_ms=published_at_ms,
        want_count=str(click_args.get("wantNum") or "").strip()[:80],
        source_rank=source_rank,
    )


def _normalize_items(
    items: Sequence[NormalizedMonitorItem],
    query: MTopSearchQuery,
    max_results: int,
) -> List[NormalizedMonitorItem]:
    region_filter = unicodedata.normalize("NFKC", query.region).casefold()
    accepted: List[NormalizedMonitorItem] = []
    seen: set[str] = set()
    for item in items:
        if item.item_id in seen:
            continue
        if query.min_price is not None and (
            item.price is None or item.price < query.min_price
        ):
            continue
        if query.max_price is not None and (
            item.price is None or item.price > query.max_price
        ):
            continue
        if region_filter and region_filter not in unicodedata.normalize(
            "NFKC", item.region
        ).casefold():
            continue
        seen.add(item.item_id)
        accepted.append(item)

    if query.sort == "latest":
        accepted.sort(
            key=lambda item: (
                item.published_at_ms is None,
                -(item.published_at_ms or 0),
                item.source_rank,
            )
        )
    elif query.sort == "price_asc":
        accepted.sort(
            key=lambda item: (item.price is None, item.price or 0.0, item.source_rank)
        )
    elif query.sort == "price_desc":
        accepted.sort(
            key=lambda item: (item.price is None, -(item.price or 0.0), item.source_rank)
        )
    else:
        accepted.sort(key=lambda item: item.source_rank)
    return accepted[:max_results]


def _search_filter(query: MTopSearchQuery) -> str:
    parts: List[str] = []
    if query.min_price is not None or query.max_price is not None:
        low = "" if query.min_price is None else f"{query.min_price:g}"
        high = "undefined" if query.max_price is None else f"{query.max_price:g}"
        parts.append(f"priceRange:{low},{high};")
    return "".join(parts)


def _sort_fields(sort: str) -> tuple[str, str]:
    return {
        "latest": ("create", "desc"),
        "price_asc": ("price", "asc"),
        "price_desc": ("price", "desc"),
        "relevance": ("", ""),
    }[sort]


class MTopSearchAdapter:
    def __init__(
        self,
        *,
        store: Optional[MTopRuntimeStore] = None,
        transport: Optional[MTopTransport] = None,
        gate_provider: Callable[[], Mapping[str, Any]] = runtime_mtop_gate_state,
        limits: Optional[MTopAdapterLimits] = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[float], float] = lambda base: random.uniform(0.0, min(1.0, base * 0.25)),
        wall_time: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if store is None:
            from db_manager import db_manager

            store = db_manager
        self.store = store
        self.transport = transport
        self.gate_provider = gate_provider
        self.limits = (limits or MTopAdapterLimits()).normalized()
        self.sleep = sleep
        self.jitter = jitter
        self.wall_time = wall_time
        self.monotonic = monotonic

    def _assert_gate(self) -> None:
        state = dict(self.gate_provider() or {})
        if not state.get("master_enabled") or not state.get("mtop_enabled"):
            raise MTopAdapterError("kill_switch_disabled", "MTop 搜索开关关闭")
        if not state.get("network_allowed"):
            raise MTopAdapterError("network_not_allowed", "MTop 网络许可关闭")
        if not state.get("executable"):
            raise MTopAdapterError("gate_unavailable", "MTop 执行门槛未满足")

    def _context(self, user_id: int, account_id: str) -> Dict[str, Any]:
        context = self.store.get_owned_cookie_search_context(user_id, account_id)
        if context.get("state") != "ready":
            state = str(context.get("state") or "action_required")
            code = state if state in {
                "ownership_mismatch",
                "not_found",
                "revision_conflict",
            } else "action_required"
            raise MTopAdapterError(
                code,
                "MTop 搜索账号身份未就绪",
                action_required=True,
            )
        if (
            int(context.get("user_id") or 0) != int(user_id)
            or str(context.get("account_id") or "") != str(account_id)
            or not str(context.get("xianyu_unb") or "").strip()
            or not str(context.get("value") or "").strip()
        ):
            raise MTopAdapterError(
                "action_required",
                "MTop 搜索账号身份不完整",
                action_required=True,
            )
        return dict(context)

    def _consume_budget(self, user_id: int, account_id: str) -> None:
        result = self.store.claim_skill_monitor_request_budget(
            user_id,
            account_id,
            global_limit=self.limits.global_requests_per_window,
            account_limit=self.limits.account_requests_per_window,
            window_seconds=self.limits.budget_window_seconds,
            now=self.wall_time(),
        )
        if not result.get("allowed"):
            raise MTopAdapterError(
                str(result.get("reason") or "request_budget_unavailable"),
                "MTop 请求预算已耗尽或不可用",
                retry_after=float(result.get("retry_after") or 0.0),
            )

    def _build_request(
        self,
        context: Mapping[str, Any],
        query: MTopSearchQuery,
        page_number: int,
    ) -> MTopTransportRequest:
        from utils.xianyu_utils import generate_sign, trans_cookies

        cookie_value = str(context.get("value") or "")
        token_source = str(trans_cookies(cookie_value).get("_m_h5_tk") or "")
        token = token_source.split("_", 1)[0]
        if not token:
            raise MTopAdapterError(
                "action_required",
                "MTop 搜索账号缺少签名令牌",
                action_required=True,
            )
        sort_field, sort_value = _sort_fields(query.sort)
        search_filter = _search_filter(query)
        payload = {
            "pageNumber": page_number,
            "keyword": query.keyword,
            "fromFilter": bool(sort_field or search_filter),
            "rowsPerPage": self.limits.page_size,
            "sortValue": sort_value,
            "sortField": sort_field,
            "customDistance": "",
            "gps": "",
            "propValueStr": {"searchFilter": search_filter},
            "customGps": "",
            "searchReqFromPage": "pcSearch",
            "extraFilterValue": "{}",
            "userPositionJson": "{}",
        }
        data_value = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        timestamp = str(int(self.wall_time() * 1000))
        params = {
            "jsv": "2.7.2",
            "appKey": "34839810",
            "t": timestamp,
            "sign": generate_sign(timestamp, token, data_value),
            "v": MTOP_SEARCH_VERSION,
            "type": "originaljson",
            "accountSite": "xianyu",
            "dataType": "json",
            "timeout": "20000",
            "api": MTOP_SEARCH_API,
            "sessionOption": "AutoLoginOnly",
            "spm_cnt": "a21ybx.search.0.0",
            "spm_pre": "a21ybx.home.searchInput.0",
        }
        user_agent = str(context.get("browser_user_agent") or "").strip()
        if not user_agent:
            user_agent = (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            )
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://www.goofish.com",
            "Referer": "https://www.goofish.com/",
            "User-Agent": user_agent,
            "Cookie": cookie_value,
        }
        return MTopTransportRequest(
            url=MTOP_SEARCH_URL,
            params=params,
            form_data={"data": data_value},
            headers=headers,
            cookie_value=cookie_value,
            timeout_seconds=self.limits.request_timeout_seconds,
            max_response_bytes=self.limits.max_response_bytes,
        )

    def _accept_response_cookie(
        self,
        context: Mapping[str, Any],
        refreshed_cookie: str,
    ) -> None:
        result = self.store.compare_and_swap_cookie_session(
            str(context["account_id"]),
            user_id=int(context["user_id"]),
            expected_xianyu_unb=str(context["xianyu_unb"]),
            expected_revision=int(context.get("cookie_revision") or 0),
            cookie_value=refreshed_cookie,
            browser_user_agent=str(context.get("browser_user_agent") or ""),
        )
        if result.get("state") not in {"updated", "unchanged"}:
            code = str(result.get("state") or "action_required")
            if code not in {"ownership_mismatch", "not_found", "revision_conflict"}:
                code = "action_required"
            raise MTopAdapterError(
                code,
                "MTop 响应 Cookie 未通过身份与版本校验",
                action_required=True,
            )

    def _assert_response_revision(
        self,
        user_id: int,
        account_id: str,
        context: Mapping[str, Any],
    ) -> None:
        current = self._context(user_id, account_id)
        if (
            str(current.get("xianyu_unb") or "")
            != str(context.get("xianyu_unb") or "")
            or int(current.get("cookie_revision") or 0)
            != int(context.get("cookie_revision") or 0)
        ):
            raise MTopAdapterError(
                "revision_conflict",
                "MTop 请求期间账号登录态已变化",
                action_required=True,
            )

    def _decode_payload(self, response: MTopTransportResponse) -> Dict[str, Any]:
        if len(response.body) > self.limits.max_response_bytes:
            raise MTopAdapterError("response_too_large", "MTop 响应超过安全上限")
        try:
            payload = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MTopAdapterError("response_json_invalid", "MTop 响应不是有效 JSON") from exc
        if not isinstance(payload, dict):
            raise MTopAdapterError("response_schema_invalid", "MTop 响应结构无效")
        return payload

    @staticmethod
    def _ret_message(payload: Mapping[str, Any]) -> str:
        values = payload.get("ret")
        if not isinstance(values, list) or len(values) > 20:
            raise MTopAdapterError("response_schema_invalid", "MTop 响应缺少 ret 数组")
        if not all(isinstance(value, str) and len(value) <= 500 for value in values):
            raise MTopAdapterError("response_schema_invalid", "MTop ret 字段结构无效")
        return " ".join(values)

    def _parse_items(
        self,
        payload: Mapping[str, Any],
        *,
        rank_offset: int,
    ) -> tuple[List[NormalizedMonitorItem], int, bool]:
        data = payload.get("data")
        if not isinstance(data, dict):
            raise MTopAdapterError("response_schema_invalid", "MTop 响应缺少 data 对象")
        result_list = data.get("resultList")
        if not isinstance(result_list, list):
            raise MTopAdapterError("response_schema_invalid", "MTop 响应缺少 resultList 数组")
        if len(result_list) > self.limits.page_size * 2:
            raise MTopAdapterError("response_too_many_items", "MTop 单页结果超过安全上限")
        parsed = [
            item
            for index, entry in enumerate(result_list)
            if (item := _extract_item(entry, rank_offset + index)) is not None
        ]
        if result_list and not parsed:
            raise MTopAdapterError("response_items_invalid", "MTop 返回了无法识别的商品卡片")
        result_info = data.get("resultInfo")
        if result_info is None:
            has_next = len(result_list) >= self.limits.page_size
        elif isinstance(result_info, dict):
            has_next = bool(result_info.get("hasNextPage"))
        else:
            raise MTopAdapterError("response_schema_invalid", "MTop resultInfo 结构无效")
        return parsed, len(result_list), has_next

    async def _backoff(self, attempt: int, response: Optional[MTopTransportResponse]) -> None:
        retry_after = None
        if response is not None:
            retry_after = _parse_retry_after(
                response.headers.get("retry-after"),
                now=self.wall_time(),
                maximum=self.limits.max_backoff_seconds,
            )
        if retry_after is None:
            base = min(
                self.limits.base_backoff_seconds * (2**attempt),
                self.limits.max_backoff_seconds,
            )
            retry_after = min(
                self.limits.max_backoff_seconds,
                base + max(0.0, float(self.jitter(base))),
            )
        await self.sleep(retry_after)

    async def search(
        self,
        *,
        user_id: int,
        account_id: str,
        query: MTopSearchQuery,
    ) -> MTopSearchResult:
        if self.transport is None:
            raise MTopAdapterError("transport_not_configured", "MTop transport 未配置")
        normalized_query = query.normalized(self.limits)
        self._assert_gate()
        breaker = self.store.claim_skill_monitor_mtop_circuit_probe(
            user_id,
            account_id,
            probe_lease_seconds=self.limits.probe_lease_seconds,
            now=self.wall_time(),
        )
        if not breaker.get("allowed"):
            raise MTopAdapterError(
                "circuit_open",
                "MTop 账号熔断器处于暂停或探针占用状态",
                retry_after=float(breaker.get("retry_after") or 0.0),
            )
        probe_token = str(breaker.get("probe_token") or "")
        countable_errors = {
            "network_error",
            "remote_throttled",
            "http_error",
            "response_too_large",
            "response_json_invalid",
            "response_schema_invalid",
            "response_too_many_items",
            "response_items_invalid",
            "token_refresh_failed",
            "remote_rejected",
            "runtime_limit",
            "page_incomplete",
            "risk_control",
            "action_required",
            "ownership_mismatch",
            "not_found",
        }
        force_open_errors = {
            "risk_control",
            "action_required",
            "ownership_mismatch",
            "not_found",
        }
        try:
            result = await self._search_impl(
                user_id=user_id,
                account_id=account_id,
                query=normalized_query,
            )
        except MTopAdapterError as exc:
            if exc.code in countable_errors:
                self.store.record_skill_monitor_mtop_circuit_outcome(
                    user_id,
                    account_id,
                    success=False,
                    error_code=exc.code,
                    failure_threshold=self.limits.failure_threshold,
                    cooldown_seconds=self.limits.failure_cooldown_seconds,
                    probe_token=probe_token,
                    force_open=exc.code in force_open_errors,
                    now=self.wall_time(),
                )
            raise
        outcome = self.store.record_skill_monitor_mtop_circuit_outcome(
            user_id,
            account_id,
            success=True,
            failure_threshold=self.limits.failure_threshold,
            cooldown_seconds=self.limits.failure_cooldown_seconds,
            probe_token=probe_token,
            now=self.wall_time(),
        )
        if not outcome.get("recorded"):
            raise MTopAdapterError(
                "breaker_unavailable",
                "MTop 熔断结果无法安全提交",
            )
        return result

    async def _search_impl(
        self,
        *,
        user_id: int,
        account_id: str,
        query: MTopSearchQuery,
    ) -> MTopSearchResult:
        if self.transport is None:
            raise MTopAdapterError("transport_not_configured", "MTop transport 未配置")
        normalized_query = query.normalized(self.limits)
        started = self.monotonic()
        all_items: List[NormalizedMonitorItem] = []
        raw_item_count = 0
        pages_requested = 0
        legal_empty = False
        stopped_reason = "page_limit"
        all_network_observed = True

        for page_number in range(
            normalized_query.start_page,
            normalized_query.start_page + normalized_query.pages,
        ):
            page_complete = False
            for attempt in range(self.limits.max_attempts_per_page):
                if self.monotonic() - started >= self.limits.max_runtime_seconds:
                    raise MTopAdapterError("runtime_limit", "MTop 搜索超过运行时长上限")
                self._assert_gate()
                context = self._context(user_id, account_id)
                self._consume_budget(user_id, account_id)
                request = self._build_request(context, normalized_query, page_number)
                pages_requested += 1
                response: Optional[MTopTransportResponse] = None
                try:
                    response = await asyncio.wait_for(
                        self.transport.send(request),
                        timeout=self.limits.request_timeout_seconds,
                    )
                except MTopAdapterError:
                    raise
                except (asyncio.TimeoutError, TimeoutError, requests.RequestException):
                    if attempt + 1 >= self.limits.max_attempts_per_page:
                        raise MTopAdapterError("network_error", "MTop 请求结果未知")
                    self._assert_gate()
                    await self._backoff(attempt, None)
                    continue

                self._assert_gate()
                all_network_observed = (
                    all_network_observed and bool(response.network_observed)
                )
                if response.refreshed_cookie:
                    self._accept_response_cookie(context, response.refreshed_cookie)
                else:
                    self._assert_response_revision(user_id, account_id, context)

                if response.status_code in {429, 503}:
                    if attempt + 1 >= self.limits.max_attempts_per_page:
                        retry_after = _parse_retry_after(
                            response.headers.get("retry-after"),
                            now=self.wall_time(),
                            maximum=self.limits.max_backoff_seconds,
                        )
                        raise MTopAdapterError(
                            "remote_throttled",
                            "MTop 服务暂时限流",
                            retry_after=retry_after or 0.0,
                        )
                    self._assert_gate()
                    await self._backoff(attempt, response)
                    continue
                if response.status_code < 200 or response.status_code >= 300:
                    raise MTopAdapterError("http_error", "MTop 返回非成功状态")

                payload = self._decode_payload(response)
                ret_message = self._ret_message(payload)
                upper_ret = ret_message.upper()
                if "SUCCESS::" not in upper_ret:
                    if any(marker in upper_ret for marker in TOKEN_EXPIRED_MARKERS):
                        if not response.refreshed_cookie:
                            raise MTopAdapterError(
                                "action_required",
                                "MTop 令牌失效且没有可验证的新 Cookie",
                                action_required=True,
                            )
                        if attempt + 1 >= self.limits.max_attempts_per_page:
                            raise MTopAdapterError("token_refresh_failed", "MTop 令牌刷新重试失败")
                        self._assert_gate()
                        await self._backoff(attempt, response)
                        continue
                    if any(marker in upper_ret for marker in SESSION_EXPIRED_MARKERS):
                        raise MTopAdapterError(
                            "action_required",
                            "MTop 登录会话已失效",
                            action_required=True,
                        )
                    if any(marker in upper_ret for marker in RISK_CONTROL_MARKERS):
                        raise MTopAdapterError(
                            "risk_control",
                            "MTop 触发平台验证或风控，已停止",
                            action_required=True,
                        )
                    raise MTopAdapterError("remote_rejected", "MTop 搜索请求被拒绝")

                page_items, page_raw_count, has_next = self._parse_items(
                    payload,
                    rank_offset=raw_item_count,
                )
                raw_item_count += page_raw_count
                all_items.extend(page_items)
                if page_raw_count == 0:
                    legal_empty = raw_item_count == 0
                    stopped_reason = "legal_empty"
                    page_complete = True
                    break
                if len(all_items) >= self.limits.max_results:
                    stopped_reason = "result_limit"
                    page_complete = True
                    break
                if not has_next:
                    stopped_reason = "no_next_page"
                    page_complete = True
                    break
                page_complete = True
                stopped_reason = "page_limit"
                break
            if not page_complete:
                raise MTopAdapterError("page_incomplete", "MTop 分页未完成")
            if stopped_reason in {"legal_empty", "result_limit", "no_next_page"}:
                break

        normalized_items = _normalize_items(
            all_items,
            normalized_query,
            self.limits.max_results,
        )
        return MTopSearchResult(
            items=normalized_items,
            pages_requested=pages_requested,
            raw_item_count=raw_item_count,
            legal_empty=legal_empty,
            stopped_reason=stopped_reason,
            is_real_data=all_network_observed,
        )


__all__ = [
    "CANARY_QUERY",
    "MTOP_NETWORK_ENV",
    "MTOP_SEARCH_URL",
    "MTopAdapterError",
    "MTopAdapterLimits",
    "MTopSearchAdapter",
    "MTopSearchQuery",
    "MTopSearchResult",
    "MTopTransportRequest",
    "MTopTransportResponse",
    "NormalizedMonitorItem",
    "RequestsMTopTransport",
    "get_mtop_offline_contract_status",
    "runtime_mtop_gate_state",
]
