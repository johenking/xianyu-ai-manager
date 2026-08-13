"""把后端出站 DNS 改道到真实解析器，摆脱系统层 fake-IP 与对单一外部解析器的依赖。

背景：本机代理（Shadowrocket TUN）在系统 DNS 层把公网域名改写成 fake-IP
（198.18.0.0/15），导致后端 httpx/websockets/aiohttp 直连拿到假地址、连接失败
（闲鱼消息 Token 探测、AI provider 调用都因此中断）。项目在环境变量
`OUTBOUND_DNS_RESOLVER` 指向一个返回真实 IP 的本机解析器（sub2api 的 unbound
127.0.0.1:5053，转发公共 DNS），但代码从未真正使用它。

本模块在进程启动早期 monkeypatch `socket.getaddrinfo`，让**公网域名**解析按顺序
走一组真实解析器：环境变量里的解析器优先（生产的 5053），其后跟内置公共 DNS 兜底
（实测直查 223.5.5.5 / 1.1.1.1 / 8.8.8.8 不被本机代理劫持）。任一解析器超时/异常/
只返回 fake-IP 就短暂冷却并尝试下一个；全部失败时用未过期的陈旧真实 IP 兜底
（serve-stale），最后才回退系统解析。**因此 5053 被卸载或崩溃时，出站解析会自动落到
公共 DNS，而不是回退到系统 fake-IP。**

以下情况一律回退系统原始解析，确保不误伤本地回环（如 127.0.0.1:8081 邀请服务）：
- 目标已是字面 IP、`localhost`、`*.local`、单标签内网主机名；
- 请求的是 IPv6（AF_INET6）或端口为服务名（非数字）；
- 未配置 `OUTBOUND_DNS_RESOLVER`（保持原生行为，不改道）；
- 所有解析器都失败且无可用陈旧记录。

另提供 `neutralize_inherited_proxy_env()`：清理进程从 launchd 域继承的
HTTP(S)_PROXY（Shadowrocket 注入的 127.0.0.1:1082），让后端 httpx 出站直连真实 IP，
不依赖本机代理组件——与 DNS 改道同属"后端出站不依赖本机代理/外部组件"的加固。
"""

from __future__ import annotations

import ipaddress
import os
import random
import socket
import struct
import threading
import time
from typing import Dict, List, Optional, Tuple

Resolver = Tuple[str, int]

_ORIGINAL_GETADDRINFO = None
_RESOLVERS: List[Resolver] = []
_LOCK = threading.Lock()
# host -> (fresh_until, stale_until, ips)
_CACHE: Dict[str, Tuple[float, float, List[str]]] = {}
# resolver -> dead_until（冷却期内跳过该解析器）
_DEAD: Dict[Resolver, float] = {}

_FRESH_TTL_SECONDS = 30.0
_STALE_TTL_SECONDS = 600.0
_DEAD_COOLDOWN_SECONDS = 45.0
_QUERY_TIMEOUT_SECONDS = 2.5

# 实测（2026-08-13）直查这些公共 DNS 的 53 端口不被 Shadowrocket fake-IP 劫持；
# 223.5.5.5 在代理关闭时也可达，作为国内优先兜底。可用 OUTBOUND_DNS_FALLBACKS 覆盖。
_DEFAULT_FALLBACK_RESOLVERS: List[Resolver] = [
    ("223.5.5.5", 53),
    ("1.1.1.1", 53),
    ("8.8.8.8", 53),
]

_FAKE_IP_NETWORK = ipaddress.ip_network("198.18.0.0/15")

_PROXY_ENV_VARS = (
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
    "http_proxy", "https_proxy", "all_proxy",
)


def _parse_resolvers(value: Optional[str]) -> List[Resolver]:
    """解析逗号分隔的解析器列表（host 或 host:port，默认 53），忽略非法项。"""
    resolvers: List[Resolver] = []
    for token in str(value or "").split(","):
        text = token.strip()
        if not text:
            continue
        host, sep, port = text.rpartition(":")
        if not sep:
            host, port = text, "53"
        host = host.strip().strip("[]") or "127.0.0.1"
        try:
            port_num = int(port)
        except ValueError:
            continue
        if not 1 <= port_num <= 65535:
            continue
        resolvers.append((host, port_num))
    return resolvers


def _is_direct_host(host: str) -> bool:
    """无需改道、直接走系统解析的目标。"""
    normalized = str(host or "").strip().rstrip(".").lower()
    if not normalized:
        return True
    if normalized in ("localhost",) or normalized.endswith((".local", ".localhost")):
        return True
    try:
        ipaddress.ip_address(normalized.strip("[]"))
        return True
    except ValueError:
        pass
    if "." not in normalized:
        return True
    return False


def _is_fake_ip(address: str) -> bool:
    try:
        return ipaddress.ip_address(address) in _FAKE_IP_NETWORK
    except ValueError:
        return False


def _encode_qname(host: str) -> bytes:
    out = b""
    for label in host.rstrip(".").split("."):
        if not label:
            continue
        try:
            encoded = label.encode("idna")
        except Exception:
            encoded = label.encode("ascii", "ignore")
        out += bytes([len(encoded)]) + encoded
    return out + b"\x00"


def _query_a_records(host: str, resolver: Resolver) -> List[str]:
    """向指定解析器发一次 UDP DNS A 查询，返回 IPv4 列表（失败返回空）。"""
    transaction_id = random.randint(0, 0xFFFF)
    header = struct.pack(">HHHHHH", transaction_id, 0x0100, 1, 0, 0, 0)
    question = _encode_qname(host) + struct.pack(">HH", 1, 1)
    packet = header + question
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(_QUERY_TIMEOUT_SECONDS)
        sock.sendto(packet, resolver)
        data, _ = sock.recvfrom(2048)
    if len(data) < 12:
        return []
    answer_count = struct.unpack(">H", data[6:8])[0]
    index = 12 + len(question)
    addresses: List[str] = []
    for _ in range(answer_count):
        if index >= len(data):
            break
        if data[index] & 0xC0 == 0xC0:
            index += 2
        else:
            while index < len(data) and data[index] != 0:
                index += data[index] + 1
            index += 1
        if index + 10 > len(data):
            break
        record_type, _record_class, _ttl, rdlength = struct.unpack(
            ">HHIH", data[index:index + 10]
        )
        index += 10
        if record_type == 1 and rdlength == 4 and index + 4 <= len(data):
            addresses.append(socket.inet_ntoa(data[index:index + 4]))
        index += rdlength
    return addresses


def _resolve_via_resolver(host: str) -> List[str]:
    """按序尝试解析器；跳过冷却中的；过滤 fake-IP；全失败用陈旧真实 IP 兜底。"""
    now = time.time()
    cached = _CACHE.get(host)
    if cached and cached[0] > now:
        return cached[2]
    if not _RESOLVERS:
        return []

    for resolver in _RESOLVERS:
        if _DEAD.get(resolver, 0.0) > now:
            continue
        try:
            addresses = _query_a_records(host, resolver)
        except Exception:
            addresses = []
        addresses = [ip for ip in addresses if not _is_fake_ip(ip)]
        if addresses:
            _CACHE[host] = (now + _FRESH_TTL_SECONDS, now + _STALE_TTL_SECONDS, addresses)
            return addresses
        # 超时/异常/只返回 fake-IP：短暂冷却该解析器，尝试下一个。
        _DEAD[resolver] = now + _DEAD_COOLDOWN_SECONDS

    # 全部解析器失败：若有未过期的陈旧真实 IP，先用它兜底，最后才回退系统解析。
    if cached and cached[1] > now:
        return cached[2]
    return []


def _patched_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    if (
        _RESOLVERS
        and host
        and family in (0, socket.AF_UNSPEC, socket.AF_INET)
        and not _is_direct_host(host)
    ):
        numeric_port = port
        if isinstance(numeric_port, str):
            numeric_port = int(numeric_port) if numeric_port.isdigit() else None
        if numeric_port is not None:
            try:
                addresses = _resolve_via_resolver(host)
            except Exception:
                addresses = []
            if addresses:
                sock_type = type or socket.SOCK_STREAM
                return [
                    (socket.AF_INET, sock_type, proto, "", (address, int(numeric_port or 0)))
                    for address in addresses
                ]
    return _ORIGINAL_GETADDRINFO(host, port, family, type, proto, flags)


def install_outbound_dns_patch() -> bool:
    """安装出站 DNS 改道。未配置 OUTBOUND_DNS_RESOLVER 时保持原生行为（no-op）。

    配置后：解析器 = 环境变量解析器（优先）+ 内置公共 DNS 兜底（可用
    OUTBOUND_DNS_FALLBACKS 覆盖/置空）。已安装则幂等返回。
    """
    global _ORIGINAL_GETADDRINFO, _RESOLVERS
    with _LOCK:
        if _ORIGINAL_GETADDRINFO is not None:
            return True
        env_resolvers = _parse_resolvers(os.getenv("OUTBOUND_DNS_RESOLVER"))
        if not env_resolvers:
            return False
        fallbacks_env = os.getenv("OUTBOUND_DNS_FALLBACKS")
        fallbacks = (
            _parse_resolvers(fallbacks_env)
            if fallbacks_env is not None
            else list(_DEFAULT_FALLBACK_RESOLVERS)
        )
        _RESOLVERS = list(dict.fromkeys(env_resolvers + fallbacks))
        _ORIGINAL_GETADDRINFO = socket.getaddrinfo
        socket.getaddrinfo = _patched_getaddrinfo
        return True


def neutralize_inherited_proxy_env() -> List[str]:
    """清除进程从 launchd 域继承的 HTTP(S)_PROXY，让出站直连真实 IP。

    Shadowrocket 会向用户 launchd 域注入 HTTP_PROXY/HTTPS_PROXY=127.0.0.1:1082，
    被生产进程继承后，默认 trust_env 的 httpx 出站会走该代理；一旦 Shadowrocket
    退出，这些出站会连接拒绝。DNS 改道后后端已能直连真实 IP，无需该代理。
    返回被清除的变量名列表（供日志）。
    """
    removed: List[str] = []
    for name in _PROXY_ENV_VARS:
        if os.environ.pop(name, None) is not None:
            removed.append(name)
    return removed


def outbound_dns_resolver_label() -> str:
    """返回当前生效的解析器列表（host:port,...）供日志使用，未启用时返回空串。"""
    if not _RESOLVERS:
        return ""
    return ",".join(f"{host}:{port}" for host, port in _RESOLVERS)
