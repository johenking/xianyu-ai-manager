"""Shared Playwright Chromium runtime policy for headed login sessions."""

from __future__ import annotations

import os
import re
from typing import Any, Mapping, Optional
from urllib.parse import quote, unquote, urlparse


_PROFILE_LOCK_MARKERS = ("processsingleton", "singletonlock")

# Chromium 只支持 HTTP(S) 代理的用户名/密码认证；SOCKS5 的账密认证会被静默忽略。
# 因此账号级住宅代理必须是 http/https；其余协议一律当作"未配置"回退到直连。
_SUPPORTED_PROXY_SCHEMES = ("http", "https")
_IP_PATTERN = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_DEFAULT_IP_ECHO_URL = "https://myip.ipip.net"


def chromium_sandbox_enabled() -> bool:
    """Keep Chromium's sandbox unless the process runs as root.

    Chromium refuses to start as root with its sandbox enabled. Containers run
    the application as root today, while normal macOS/Linux installs retain the
    safer default.
    """

    get_effective_uid = getattr(os, "geteuid", None)
    return not callable(get_effective_uid) or get_effective_uid() != 0


def _coerce_proxy_mapping(raw: Mapping[str, Any]) -> dict[str, str]:
    server = str(raw.get("server") or raw.get("proxy_server") or "").strip()
    username = str(raw.get("username") or raw.get("proxy_username") or "").strip()
    password = str(raw.get("password") or raw.get("proxy_password") or "")
    bypass = str(raw.get("bypass") or raw.get("proxy_bypass") or "").strip()
    return {"server": server, "username": username, "password": password, "bypass": bypass}


def _parse_proxy_string(raw: str) -> dict[str, str]:
    text = str(raw or "").strip()
    empty = {"server": "", "username": "", "password": "", "bypass": ""}
    if not text:
        return empty
    if "://" not in text:
        # 短格式：host:port:user:pass 或 host:port（视为 http 代理）。
        parts = text.split(":")
        if len(parts) >= 4:
            host, port, user = parts[0], parts[1], parts[2]
            password = ":".join(parts[3:])
            return {
                "server": f"http://{host}:{port}",
                "username": user,
                "password": password,
                "bypass": "",
            }
        return {"server": f"http://{text}", "username": "", "password": "", "bypass": ""}
    parsed = urlparse(text)
    scheme = (parsed.scheme or "http").lower()
    host = parsed.hostname or ""
    if not host:
        return empty
    port = f":{parsed.port}" if parsed.port else ""
    return {
        "server": f"{scheme}://{host}{port}",
        "username": unquote(parsed.username) if parsed.username else "",
        "password": unquote(parsed.password) if parsed.password else "",
        "bypass": "",
    }


def normalize_proxy_config(raw: Any) -> Optional[dict[str, str]]:
    """Return a Playwright proxy dict, or None when no usable proxy is configured.

    未配置（None / 空 / 无 server）一律返回 None —— 调用方据此保持原有直连行为，
    字节级不变。只接受 http/https 代理；socks/socks5 等视为无效（Chromium 不支持其
    账密认证）并返回 None，避免"配了个用不了的代理却以为生效"。
    """
    if raw is None:
        return None
    if isinstance(raw, Mapping):
        fields = _coerce_proxy_mapping(raw)
    elif isinstance(raw, str):
        fields = _parse_proxy_string(raw)
    else:
        return None

    server = fields.get("server", "").strip()
    if not server:
        return None
    if "://" not in server:
        server = f"http://{server}"
    scheme = server.split("://", 1)[0].lower()
    if scheme not in _SUPPORTED_PROXY_SCHEMES:
        return None

    proxy: dict[str, str] = {"server": server}
    if fields.get("username"):
        proxy["username"] = fields["username"]
    if fields.get("password"):
        proxy["password"] = fields["password"]
    if fields.get("bypass"):
        proxy["bypass"] = fields["bypass"]
    return proxy


def _raw_proxy_server(raw: Any) -> str:
    """提取原始配置里的 server 串（不做协议过滤，供协议判定用）。"""
    if raw is None:
        return ""
    if isinstance(raw, Mapping):
        fields = _coerce_proxy_mapping(raw)
    elif isinstance(raw, str):
        fields = _parse_proxy_string(raw)
    else:
        return ""
    return fields.get("server", "").strip()


def proxy_config_status(raw: Any) -> dict[str, str]:
    """区分"没配代理"与"配了但协议不支持"（如 SOCKS5）。

    `normalize_proxy_config` 对 SOCKS5 和未配置都返回 None，调用方无从分辨"用户根本
    没配"还是"配了个 Chromium 用不了的 SOCKS5"——后者会被静默当成直连，用户以为代理
    生效实则从机房 IP 直出。本函数保留协议信息，返回
    `{"status": "not_configured"|"unsupported_scheme"|"supported", "scheme": "..."}`，
    让测试端点/健康门禁能明确报"这是 SOCKS5、请换 HTTP"而不是静默直连。
    """
    server = _raw_proxy_server(raw)
    if not server:
        return {"status": "not_configured", "scheme": ""}
    if "://" not in server:
        server = f"http://{server}"
    scheme = server.split("://", 1)[0].lower()
    if scheme in _SUPPORTED_PROXY_SCHEMES:
        return {"status": "supported", "scheme": scheme}
    return {"status": "unsupported_scheme", "scheme": scheme}


def chromium_runtime_options(proxy: Any = None) -> dict[str, Any]:
    """Return the channel/sandbox (and optional proxy) options for headed Chromium.

    `proxy` 省略或归一化后为空时，返回结果**不含** proxy 键——与接入代理前完全一致，
    保证"没配代理时行为不变"。配置了合法 http(s) 代理时追加 Playwright 的 `proxy` 字段
    （凭据放独立的 username/password，Chromium 会忽略内联在 URL 里的账密）。
    """

    options: dict[str, Any] = {
        "channel": os.getenv("XIANYU_BROWSER_CHANNEL") or None,
        "chromium_sandbox": chromium_sandbox_enabled(),
    }
    normalized = normalize_proxy_config(proxy)
    if normalized:
        options["proxy"] = normalized
    return options


def proxy_url_with_auth(normalized: Mapping[str, str]) -> str:
    """Build an inline-auth proxy URL for httpx (which, unlike Chromium, honors it)."""
    server = str(normalized.get("server") or "")
    username = str(normalized.get("username") or "")
    password = str(normalized.get("password") or "")
    if not server or not username:
        return server
    scheme, _, rest = server.partition("://")
    return f"{scheme}://{quote(username, safe='')}:{quote(password, safe='')}@{rest}"


def httpx_proxy_url(raw: Any) -> Optional[str]:
    """Normalize any proxy input into an httpx inline-auth URL, or None if unset.

    httpx 客户端接受单个 proxy URL（支持内联账密）；未配置/非法时返回 None，
    调用方把它原样传给 `httpx.Client(proxy=...)` 即等于直连（原行为）。
    """
    normalized = normalize_proxy_config(raw)
    if not normalized:
        return None
    return proxy_url_with_auth(normalized)


def _extract_ip(text: str) -> str:
    match = _IP_PATTERN.search(str(text or ""))
    return match.group(0) if match else ""


def probe_proxy_egress(
    proxy: Any,
    *,
    timeout: float = 15.0,
    ip_echo_url: Optional[str] = None,
) -> dict[str, Any]:
    """Verify a proxy is reachable and report its egress IP (pre-launch self-check).

    返回 `{"ok", "ip", "status", "error"}`。未配置代理返回 status=not_configured；
    配了 SOCKS5 等 Chromium 不支持的协议返回 status=unsupported_scheme + 明确提示
    （而非静默当成未配置），避免"买错协议还以为代理生效"。
    只做连通性 + 出口 IP 回显，不在此判定是否住宅 IP（住宅判定交由上层/人工核对）。
    """
    scheme_status = proxy_config_status(proxy)
    if scheme_status["status"] == "unsupported_scheme":
        bad = scheme_status["scheme"].upper()
        return {
            "ok": False,
            "ip": "",
            "status": "unsupported_scheme",
            "error": f"Chromium 不支持 {bad} 代理的账密认证，请向服务商索要 HTTP/HTTPS 端口",
        }
    normalized = normalize_proxy_config(proxy)
    if not normalized:
        return {"ok": False, "ip": "", "status": "not_configured", "error": "未配置可用代理"}

    import httpx

    echo = str(
        ip_echo_url
        or os.getenv("XIANYU_PROXY_IP_ECHO_URL")
        or _DEFAULT_IP_ECHO_URL
    ).strip()
    proxy_url = proxy_url_with_auth(normalized)
    try:
        with httpx.Client(timeout=timeout, proxy=proxy_url, follow_redirects=True) as client:
            response = client.get(echo)
        ip = _extract_ip(response.text)
        if ip:
            return {"ok": True, "ip": ip, "status": "ok", "error": ""}
        return {"ok": False, "ip": "", "status": "no_ip", "error": response.text.strip()[:200]}
    except Exception as exc:  # noqa: BLE001 - self-check must never raise into launch path
        return {"ok": False, "ip": "", "status": "error", "error": type(exc).__name__}


def classify_browser_launch_error(error: BaseException) -> str:
    """Classify launch failures without treating generic profile text as a lock."""

    text = str(error).lower()
    if any(marker in text for marker in _PROFILE_LOCK_MARKERS):
        return "profile_in_use"
    return "browser_error"
