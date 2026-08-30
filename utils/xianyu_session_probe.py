"""Side-effect-free validation for a Xianyu message session.

This module is the common validation boundary for QR login, browser-extension
imports, and official-browser renewal.  It only returns normalized state and
merged response cookies; callers decide whether persistence is appropriate.
"""

from __future__ import annotations

import os
import platform
import plistlib
import re
import socket
import ssl
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import urlparse, urlunparse

import httpx

from config import API_ENDPOINTS
from utils.browser_runtime import httpx_proxy_url
from utils.xianyu_utils import generate_device_id, generate_sign


PROBE_SUCCESS = "success"
PROBE_VERIFICATION_REQUIRED = "verification_required"
PROBE_EXPIRED = "expired"
PROBE_RETRYABLE_ERROR = "retryable_error"

DEFAULT_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Safari/537.36"
)
SESSION_COOKIE_NAMES = {
    "_m_h5_tk",
    "_m_h5_tk_enc",
    "cookie2",
    "sgcookie",
    "t",
}
_VERIFICATION_MARKERS = (
    "FAIL_SYS_USER_VALIDATE",
    "RGV587_ERROR",
    "punish?x5secdata",
    "captcha",
    "ILLEGAL_ACCESS",
)
_EXPIRED_MARKERS = (
    "令牌过期",
    "Session过期",
    "SESSION_EXPIRED",
    "TOKEN_EXPIRED",
)
_TOKEN_EXPIRED_MARKERS = (
    "FAIL_SYS_TOKEN_EXOIRED",
    "令牌过期",
    "TOKEN_EXPIRED",
)
_ALLOWED_VERIFICATION_HOSTS = ("goofish.com", "taobao.com")
_H5_API_HOST_OK_CACHE: dict[str, bool] = {}

# 隧道代理空闲一段时间后会掐掉下一个 CONNECT，换新连接立刻重试即通；
# 超时不在此列——它已经烧掉整个 timeout 预算，重试只会让探测翻倍卡住。
_TRANSPORT_RETRYABLE_ERRORS = (
    httpx.NetworkError,
    httpx.ProxyError,
    httpx.RemoteProtocolError,
)
_TRANSPORT_ATTEMPTS = 2


@dataclass
class SessionProbeResult:
    status: str
    cookies: dict[str, str] = field(default_factory=dict)
    access_token: str = field(default="", repr=False)
    verification_url: str = field(default="", repr=False)
    error_code: str = ""
    message: str = ""

    @property
    def succeeded(self) -> bool:
        return self.status == PROBE_SUCCESS and bool(self.access_token)


def parse_cookie_string(cookie_string: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for part in (cookie_string or "").split(";"):
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        name = name.strip()
        if name:
            parsed[name] = value.strip()
    return parsed


def cookies_to_string(cookies: Mapping[str, str]) -> str:
    return "; ".join(f"{name}={value}" for name, value in cookies.items())


def has_core_session_cookies(cookies: Mapping[str, str]) -> bool:
    return bool(str(cookies.get("unb") or "").strip()) and any(
        bool(cookies.get(name)) for name in SESSION_COOKIE_NAMES
    )


@lru_cache(maxsize=1)
def detect_default_browser_user_agent() -> str:
    """Build a truthful Chrome UA from the installed browser version."""
    configured = os.getenv("XIANYU_BROWSER_USER_AGENT", "").strip()
    if configured:
        return configured

    candidates = (
        Path("/Applications/Google Chrome.app/Contents/Info.plist"),
        Path.home() / "Applications/Google Chrome.app/Contents/Info.plist",
    )
    for plist_path in candidates:
        try:
            with plist_path.open("rb") as handle:
                info = plistlib.load(handle)
            version = str(info.get("CFBundleShortVersionString") or "").strip()
            if not re.fullmatch(r"\d+(?:\.\d+){1,3}", version):
                continue
            os_token = (platform.mac_ver()[0] or "10.15.7").replace(".", "_")
            return (
                f"Mozilla/5.0 (Macintosh; Intel Mac OS X {os_token}) "
                f"AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{version} "
                "Safari/537.36"
            )
        except Exception:
            continue
    return DEFAULT_BROWSER_USER_AGENT


def is_allowed_verification_url(value: str) -> bool:
    try:
        parsed = urlparse(str(value or ""))
    except ValueError:
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    return bool(
        parsed.scheme == "https"
        and host
        and any(
            host == allowed or host.endswith(f".{allowed}")
            for allowed in _ALLOWED_VERIFICATION_HOSTS
        )
    )


def _safe_verification_url(payload: Mapping[str, Any]) -> str:
    data = payload.get("data")
    if not isinstance(data, Mapping):
        return ""
    value = str(data.get("url") or "").strip()
    return value if is_allowed_verification_url(value) else ""


def _merge_set_cookie_headers(
    cookies: Mapping[str, str],
    set_cookie_headers: Iterable[str],
) -> dict[str, str]:
    merged = dict(cookies)
    for header in set_cookie_headers:
        first_part = str(header or "").split(";", 1)[0]
        if "=" not in first_part:
            continue
        name, value = first_part.split("=", 1)
        name = name.strip()
        if name:
            merged[name] = value.strip()
    return merged


def classify_probe_response(
    payload: Mapping[str, Any],
    cookies: Mapping[str, str],
    *,
    set_cookie_headers: Iterable[str] = (),
) -> SessionProbeResult:
    merged_cookies = _merge_set_cookie_headers(cookies, set_cookie_headers)
    ret = payload.get("ret")
    ret_values = ret if isinstance(ret, list) else []
    ret_text = " ".join(str(value) for value in ret_values)
    data = payload.get("data") if isinstance(payload.get("data"), Mapping) else {}
    access_token = str(data.get("accessToken") or "")

    if access_token and any("SUCCESS::调用成功" in str(value) for value in ret_values):
        return SessionProbeResult(
            status=PROBE_SUCCESS,
            cookies=merged_cookies,
            access_token=access_token,
            message="消息 Token 验证成功",
        )

    verification_url = _safe_verification_url(payload)
    if verification_url or any(marker in ret_text for marker in _VERIFICATION_MARKERS):
        risk_code = (
            "illegal_access"
            if "ILLEGAL_ACCESS" in ret_text
            else "human_verification_required"
        )
        return SessionProbeResult(
            status=PROBE_VERIFICATION_REQUIRED,
            cookies=merged_cookies,
            verification_url=verification_url,
            error_code=risk_code,
            message=(
                "平台判定当前访问非法，已停止自动续签"
                if risk_code == "illegal_access"
                else "需要在闲鱼官方页面完成人工验证"
            ),
        )

    if any(marker in ret_text for marker in _TOKEN_EXPIRED_MARKERS) and not any(
        marker in ret_text for marker in ("SESSION_EXPIRED", "Session过期")
    ):
        return SessionProbeResult(
            status=PROBE_EXPIRED,
            cookies=merged_cookies,
            error_code="token_expired",
            message="闲鱼签名令牌已过期",
        )

    if any(marker in ret_text for marker in _EXPIRED_MARKERS):
        return SessionProbeResult(
            status=PROBE_EXPIRED,
            cookies=merged_cookies,
            error_code="session_expired",
            message="闲鱼官方登录态已过期",
        )

    return SessionProbeResult(
        status=PROBE_RETRYABLE_ERROR,
        cookies=merged_cookies,
        error_code="token_probe_failed",
        message="消息 Token 验证尚未通过",
    )


def _is_h5_api_host_reachable(host: str, timeout: float = 3.0) -> bool:
    cached = _H5_API_HOST_OK_CACHE.get(host)
    if cached is not None:
        return cached
    try:
        context = ssl.create_default_context()
        with socket.create_connection((host, 443), timeout=timeout) as sock:
            with context.wrap_socket(sock, server_hostname=host):
                pass
        _H5_API_HOST_OK_CACHE[host] = True
    except Exception:
        _H5_API_HOST_OK_CACHE[host] = False
    return _H5_API_HOST_OK_CACHE[host]


def resolve_h5_api_url(api_url: str) -> str:
    if not api_url:
        return api_url
    preferred_host = os.getenv("XIANYU_H5_API_HOST", "").strip()
    parsed = urlparse(api_url)
    if preferred_host:
        return urlunparse(parsed._replace(netloc=preferred_host))
    if parsed.netloc == "h5api.m.goofish.com" and not _is_h5_api_host_reachable(parsed.netloc):
        return urlunparse(parsed._replace(netloc="h5api.m.taobao.com"))
    return api_url


def build_probe_request(
    cookie_string: str,
    browser_user_agent: str,
    *,
    device_id: Optional[str] = None,
    timestamp_ms: Optional[int] = None,
) -> tuple[str, dict[str, str], dict[str, str], dict[str, str]]:
    cookies = parse_cookie_string(cookie_string)
    unb = str(cookies.get("unb") or "").strip()
    request_device_id = str(device_id or "").strip() or generate_device_id(unb)
    timestamp = str(timestamp_ms or int(time.time() * 1000))
    data_value = (
        '{"appKey":"444e9908a51d1cb236a27862abc769c9","deviceId":"'
        + request_device_id
        + '"}'
    )
    token = str(cookies.get("_m_h5_tk") or "").split("_", 1)[0]
    params = {
        "jsv": "2.7.2",
        "appKey": "34839810",
        "t": timestamp,
        "sign": generate_sign(timestamp, token, data_value),
        "v": "1.0",
        "type": "originaljson",
        "accountSite": "xianyu",
        "dataType": "json",
        "timeout": "20000",
        "api": "mtop.taobao.idlemessage.pc.login.token",
        "sessionOption": "AutoLoginOnly",
        "dangerouslySetWindvaneParams": "%5Bobject%20Object%5D",
        "smToken": "token",
        "queryToken": "sm",
        "sm": "sm",
        "spm_cnt": "a21ybx.im.0.0",
        "spm_pre": "a21ybx.home.sidebar.1.4c053da6vYwnmf",
        "log_id": "4c053da6vYwnmf",
    }
    headers = {
        "accept": "application/json",
        "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
        "cache-control": "no-cache",
        "content-type": "application/x-www-form-urlencoded",
        "pragma": "no-cache",
        "user-agent": (browser_user_agent or detect_default_browser_user_agent()).strip(),
        "referer": "https://www.goofish.com/",
        "origin": "https://www.goofish.com",
        "cookie": cookie_string,
    }
    return (
        resolve_h5_api_url(API_ENDPOINTS.get("token", "")),
        params,
        {"data": data_value},
        headers,
    )


def _response_set_cookie_headers(response: httpx.Response) -> list[str]:
    try:
        return response.headers.get_list("set-cookie")
    except Exception:
        value = response.headers.get("set-cookie")
        return [value] if value else []


def _preflight_cookies(cookie_string: str) -> Optional[SessionProbeResult]:
    cookies = parse_cookie_string(cookie_string)
    if not has_core_session_cookies(cookies):
        return SessionProbeResult(
            status=PROBE_EXPIRED,
            cookies=cookies,
            error_code="core_cookies_missing",
            message="Cookie 缺少账号身份或会话字段",
        )
    return None


def _payload_ret_text(payload: Mapping[str, Any]) -> str:
    ret = payload.get("ret")
    values = ret if isinstance(ret, list) else []
    return " ".join(str(value) for value in values)


def _should_retry_with_fresh_h5_token(
    payload: Mapping[str, Any],
    previous_cookies: Mapping[str, str],
    merged_cookies: Mapping[str, str],
) -> bool:
    ret_text = _payload_ret_text(payload)
    if (
        not any(marker in ret_text for marker in _TOKEN_EXPIRED_MARKERS)
        or _safe_verification_url(payload)
        or any(marker in ret_text for marker in _VERIFICATION_MARKERS)
    ):
        return False
    previous_token = str(previous_cookies.get("_m_h5_tk") or "").strip()
    fresh_token = str(merged_cookies.get("_m_h5_tk") or "").strip()
    return bool(fresh_token and fresh_token != previous_token)


def _classify_probe_http_response(
    response: httpx.Response,
    cookies: Mapping[str, str],
) -> tuple[Mapping[str, Any], SessionProbeResult]:
    payload = response.json()
    if not isinstance(payload, Mapping):
        raise ValueError("unexpected token response")
    return payload, classify_probe_response(
        payload,
        cookies,
        set_cookie_headers=_response_set_cookie_headers(response),
    )


def _next_probe_timestamp_ms(previous_params: Mapping[str, str]) -> int:
    try:
        previous_timestamp = int(previous_params.get("t") or 0)
    except (TypeError, ValueError):
        previous_timestamp = 0
    return max(int(time.time() * 1000), previous_timestamp + 1)


def _probe_message_session_with_sync_client(
    client: Any,
    cookie_string: str,
    browser_user_agent: str,
    device_id: str,
) -> SessionProbeResult:
    cookies = parse_cookie_string(cookie_string)
    url, params, data, headers = build_probe_request(
        cookie_string,
        browser_user_agent,
        device_id=device_id,
    )
    response = client.post(url, params=params, data=data, headers=headers)
    payload, result = _classify_probe_http_response(response, cookies)
    if not _should_retry_with_fresh_h5_token(payload, cookies, result.cookies):
        return result

    refreshed_cookie_string = cookies_to_string(result.cookies)
    retry_url, retry_params, retry_data, retry_headers = build_probe_request(
        refreshed_cookie_string,
        browser_user_agent,
        device_id=device_id,
        timestamp_ms=_next_probe_timestamp_ms(params),
    )
    try:
        retry_response = client.post(
            retry_url,
            params=retry_params,
            data=retry_data,
            headers=retry_headers,
        )
        _, retry_result = _classify_probe_http_response(retry_response, result.cookies)
        return retry_result
    except Exception:
        return SessionProbeResult(
            status=PROBE_RETRYABLE_ERROR,
            cookies=dict(result.cookies),
            error_code="token_probe_retry_exception",
            message="消息 Token 重签探测出现临时异常",
        )


async def _probe_message_session_with_async_client(
    client: Any,
    cookie_string: str,
    browser_user_agent: str,
    device_id: str,
) -> SessionProbeResult:
    cookies = parse_cookie_string(cookie_string)
    url, params, data, headers = build_probe_request(
        cookie_string,
        browser_user_agent,
        device_id=device_id,
    )
    response = await client.post(url, params=params, data=data, headers=headers)
    payload, result = _classify_probe_http_response(response, cookies)
    if not _should_retry_with_fresh_h5_token(payload, cookies, result.cookies):
        return result

    refreshed_cookie_string = cookies_to_string(result.cookies)
    retry_url, retry_params, retry_data, retry_headers = build_probe_request(
        refreshed_cookie_string,
        browser_user_agent,
        device_id=device_id,
        timestamp_ms=_next_probe_timestamp_ms(params),
    )
    try:
        retry_response = await client.post(
            retry_url,
            params=retry_params,
            data=retry_data,
            headers=retry_headers,
        )
        _, retry_result = _classify_probe_http_response(retry_response, result.cookies)
        return retry_result
    except Exception:
        return SessionProbeResult(
            status=PROBE_RETRYABLE_ERROR,
            cookies=dict(result.cookies),
            error_code="token_probe_retry_exception",
            message="消息 Token 重签探测出现临时异常",
        )


def probe_message_session_sync(
    cookie_string: str,
    browser_user_agent: str,
    *,
    device_id: Optional[str] = None,
    timeout: float = 25.0,
    client_factory: Optional[Callable[[], Any]] = None,
    proxy: Any = None,
) -> SessionProbeResult:
    preflight = _preflight_cookies(cookie_string)
    if preflight:
        return preflight
    cookies = parse_cookie_string(cookie_string)
    probe_device_id = str(device_id or "").strip() or generate_device_id(
        str(cookies.get("unb") or "").strip()
    )
    proxy_url = httpx_proxy_url(proxy)
    for attempt in range(_TRANSPORT_ATTEMPTS):
        try:
            if client_factory is None:
                with httpx.Client(
                    timeout=timeout, follow_redirects=False, proxy=proxy_url
                ) as client:
                    return _probe_message_session_with_sync_client(
                        client,
                        cookie_string,
                        browser_user_agent,
                        probe_device_id,
                    )
            return _probe_message_session_with_sync_client(
                client_factory(),
                cookie_string,
                browser_user_agent,
                probe_device_id,
            )
        except _TRANSPORT_RETRYABLE_ERRORS:
            if attempt + 1 >= _TRANSPORT_ATTEMPTS:
                break
        except Exception:
            break
    return SessionProbeResult(
        status=PROBE_RETRYABLE_ERROR,
        cookies=cookies,
        error_code="token_probe_exception",
        message="消息 Token 探测出现临时异常",
    )


async def probe_message_session_async(
    cookie_string: str,
    browser_user_agent: str,
    *,
    device_id: Optional[str] = None,
    timeout: float = 25.0,
    client_factory: Optional[Callable[[], Any]] = None,
    proxy: Any = None,
) -> SessionProbeResult:
    preflight = _preflight_cookies(cookie_string)
    if preflight:
        return preflight
    cookies = parse_cookie_string(cookie_string)
    probe_device_id = str(device_id or "").strip() or generate_device_id(
        str(cookies.get("unb") or "").strip()
    )
    proxy_url = httpx_proxy_url(proxy)
    for attempt in range(_TRANSPORT_ATTEMPTS):
        try:
            if client_factory is None:
                async with httpx.AsyncClient(
                    timeout=timeout, follow_redirects=False, proxy=proxy_url
                ) as client:
                    return await _probe_message_session_with_async_client(
                        client,
                        cookie_string,
                        browser_user_agent,
                        probe_device_id,
                    )
            return await _probe_message_session_with_async_client(
                client_factory(),
                cookie_string,
                browser_user_agent,
                probe_device_id,
            )
        except _TRANSPORT_RETRYABLE_ERRORS:
            if attempt + 1 >= _TRANSPORT_ATTEMPTS:
                break
        except Exception:
            break
    return SessionProbeResult(
        status=PROBE_RETRYABLE_ERROR,
        cookies=cookies,
        error_code="token_probe_exception",
        message="消息 Token 探测出现临时异常",
    )


__all__ = [
    "PROBE_EXPIRED",
    "PROBE_RETRYABLE_ERROR",
    "PROBE_SUCCESS",
    "PROBE_VERIFICATION_REQUIRED",
    "SessionProbeResult",
    "build_probe_request",
    "classify_probe_response",
    "cookies_to_string",
    "detect_default_browser_user_agent",
    "has_core_session_cookies",
    "is_allowed_verification_url",
    "parse_cookie_string",
    "probe_message_session_async",
    "probe_message_session_sync",
]
