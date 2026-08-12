"""Guarded HTTP requests for user-configured outbound integrations.

The resolver pins only addresses that were verified as globally routable. Every
redirect is validated before the next request, so URL validation cannot be
bypassed by automatic redirects or a DNS answer changing between checks.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import math
import os
import re
import socket
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence
from urllib.parse import urljoin, urlsplit

import aiohttp


OUTBOUND_HTTP_MAX_REDIRECTS = 3
OUTBOUND_HTTP_MAX_RESPONSE_BYTES = 1024 * 1024
OUTBOUND_HTTP_MAX_URL_LENGTH = 4096
OUTBOUND_HTTP_MAX_HEADER_COUNT = 32
OUTBOUND_HTTP_MAX_HEADER_BYTES = 32 * 1024
OUTBOUND_HTTP_MAX_TIMEOUT_SECONDS = 30.0

_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_HEADER_NAME_RE = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_BLOCKED_REQUEST_HEADERS = {
    "connection",
    "content-length",
    "host",
    "proxy-authenticate",
    "proxy-authorization",
    "proxy-connection",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}
_CROSS_ORIGIN_SENSITIVE_HEADERS = {
    "authorization",
    "cookie",
}


class OutboundRequestError(ValueError):
    """A safe, classified failure for an outbound integration request."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = str(code or "outbound_request_failed")


@dataclass(frozen=True)
class ValidatedPublicURL:
    url: str
    scheme: str
    host: str
    port: int
    addresses: tuple[str, ...]

    @property
    def origin(self) -> tuple[str, str, int]:
        return self.scheme, self.host, self.port


@dataclass(frozen=True)
class PublicHTTPResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self) -> Any:
        return json.loads(self.text)

    def raise_for_status(self) -> None:
        if 200 <= self.status < 300:
            return
        raise OutboundRequestError(
            "http_error",
            f"outbound endpoint returned HTTP {self.status}",
        )


def _normalize_host(host: str) -> str:
    normalized = str(host or "").strip().rstrip(".").lower()
    if not normalized or "%" in normalized:
        raise OutboundRequestError("invalid_url", "outbound URL host is invalid")
    try:
        return normalized.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise OutboundRequestError(
            "invalid_url",
            "outbound URL host is invalid",
        ) from exc


def parse_public_http_url(url: Any) -> tuple[str, str, int]:
    """Validate URL syntax without performing DNS resolution."""
    value = str(url or "").strip()
    if not value or len(value) > OUTBOUND_HTTP_MAX_URL_LENGTH:
        raise OutboundRequestError("invalid_url", "outbound URL is invalid")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise OutboundRequestError("invalid_url", "outbound URL is invalid")

    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise OutboundRequestError("invalid_url", "outbound URL is invalid") from exc

    scheme = str(parsed.scheme or "").lower()
    if scheme not in {"http", "https"}:
        raise OutboundRequestError(
            "unsupported_scheme",
            "outbound URL must use http or https",
        )
    if parsed.username is not None or parsed.password is not None:
        raise OutboundRequestError(
            "credentialed_url_denied",
            "credentials are not allowed in outbound URLs",
        )
    if parsed.fragment:
        raise OutboundRequestError(
            "invalid_url",
            "URL fragments are not allowed for outbound requests",
        )

    host = _normalize_host(parsed.hostname or "")
    resolved_port = int(port or (443 if scheme == "https" else 80))
    if not 1 <= resolved_port <= 65535:
        raise OutboundRequestError("invalid_url", "outbound URL port is invalid")
    return scheme, host, resolved_port


def _require_global_address(address: str) -> str:
    try:
        parsed = ipaddress.ip_address(str(address).split("%", 1)[0])
    except ValueError as exc:
        raise OutboundRequestError(
            "dns_resolution_failed",
            "outbound host returned an invalid address",
        ) from exc
    if not parsed.is_global:
        raise OutboundRequestError(
            "non_public_address_denied",
            "outbound host resolved to a non-public address",
        )
    return str(parsed)


def _configured_dns_endpoint() -> Optional[tuple[str, int]]:
    raw = str(os.environ.get("OUTBOUND_DNS_RESOLVER") or "").strip()
    if not raw:
        return None
    try:
        parsed = urlsplit(f"//{raw}")
        host = str(parsed.hostname or "")
        port = 53 if parsed.port is None else int(parsed.port)
        address = ipaddress.ip_address(host)
    except (TypeError, ValueError) as exc:
        raise OutboundRequestError(
            "dns_configuration_invalid",
            "outbound DNS resolver configuration is invalid",
        ) from exc
    if (
        parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or not address.is_loopback
        or not 1 <= port <= 65535
    ):
        raise OutboundRequestError(
            "dns_configuration_invalid",
            "outbound DNS resolver must be a loopback IP and valid port",
        )
    return str(address), port


def _resolve_with_configured_dns_sync(host: str) -> tuple[str, ...]:
    endpoint = _configured_dns_endpoint()
    if endpoint is None:
        raise OutboundRequestError(
            "dns_configuration_invalid",
            "outbound DNS resolver is not configured",
        )
    try:
        import dns.exception
        import dns.resolver
    except ImportError as exc:
        raise OutboundRequestError(
            "dns_configuration_invalid",
            "configured outbound DNS support is unavailable",
        ) from exc

    resolver = dns.resolver.Resolver(configure=False)
    resolver.nameservers = [endpoint[0]]
    resolver.port = endpoint[1]
    resolver.timeout = 2.0
    resolver.lifetime = 4.0
    addresses: list[str] = []
    for record_type in ("A", "AAAA"):
        try:
            answer = resolver.resolve(
                host,
                record_type,
                search=False,
                raise_on_no_answer=False,
            )
        except dns.resolver.NXDOMAIN as exc:
            raise OutboundRequestError(
                "dns_resolution_failed",
                "outbound host could not be resolved",
            ) from exc
        except (
            dns.exception.Timeout,
            dns.resolver.NoAnswer,
            dns.resolver.NoNameservers,
        ):
            continue
        except dns.exception.DNSException as exc:
            raise OutboundRequestError(
                "dns_resolution_failed",
                "outbound host could not be resolved",
            ) from exc
        if answer.rrset is None:
            continue
        addresses.extend(
            str(getattr(record, "address", record)).strip()
            for record in answer
        )
    unique_addresses = tuple(dict.fromkeys(value for value in addresses if value))
    if not unique_addresses:
        raise OutboundRequestError(
            "dns_resolution_failed",
            "outbound host could not be resolved",
        )
    return unique_addresses


def resolve_public_host_sync(host: Any, port: Any) -> tuple[str, tuple[str, ...]]:
    """Resolve a bare host for a non-HTTP protocol and pin public addresses."""
    normalized = _normalize_host(host)
    try:
        resolved_port = int(port)
    except (TypeError, ValueError) as exc:
        raise OutboundRequestError(
            "invalid_port",
            "outbound port is invalid",
        ) from exc
    if not 1 <= resolved_port <= 65535:
        raise OutboundRequestError("invalid_port", "outbound port is invalid")

    try:
        literal = ipaddress.ip_address(normalized)
    except ValueError:
        try:
            records = socket.getaddrinfo(
                normalized,
                resolved_port,
                type=socket.SOCK_STREAM,
            )
        except OSError as exc:
            raise OutboundRequestError(
                "dns_resolution_failed",
                "outbound host could not be resolved",
            ) from exc
        addresses = tuple(
            dict.fromkeys(
                _require_global_address(record[4][0])
                for record in records
            )
        )
    else:
        addresses = (_require_global_address(str(literal)),)
    if not addresses:
        raise OutboundRequestError(
            "dns_resolution_failed",
            "outbound host could not be resolved",
        )
    return normalized, addresses


async def resolve_public_http_url(url: Any) -> ValidatedPublicURL:
    """Resolve a URL and reject the whole answer if any address is non-public."""
    value = str(url or "").strip()
    scheme, host, port = parse_public_http_url(value)
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        if _configured_dns_endpoint() is not None:
            resolved_addresses = await asyncio.to_thread(
                _resolve_with_configured_dns_sync,
                host,
            )
            addresses = tuple(
                dict.fromkeys(
                    _require_global_address(address)
                    for address in resolved_addresses
                )
            )
        else:
            try:
                records = await asyncio.get_running_loop().getaddrinfo(
                    host,
                    port,
                    type=socket.SOCK_STREAM,
                )
            except OSError as exc:
                raise OutboundRequestError(
                    "dns_resolution_failed",
                    "outbound host could not be resolved",
                ) from exc
            addresses = tuple(
                dict.fromkeys(
                    _require_global_address(record[4][0])
                    for record in records
                )
            )
    else:
        addresses = (_require_global_address(str(literal)),)
    if not addresses:
        raise OutboundRequestError(
            "dns_resolution_failed",
            "outbound host could not be resolved",
        )
    return ValidatedPublicURL(value, scheme, host, port, addresses)


class PinnedPublicResolver:
    """aiohttp resolver that serves only pre-validated, pinned DNS records."""

    def __init__(self) -> None:
        self._records: dict[str, tuple[str, ...]] = {}

    def pin(self, target: ValidatedPublicURL) -> None:
        self._records[_normalize_host(target.host)] = tuple(target.addresses)

    async def resolve(
        self,
        host: str,
        port: int = 0,
        family: int = socket.AF_UNSPEC,
    ) -> list[dict[str, Any]]:
        del family
        normalized = _normalize_host(host)
        addresses = self._records.get(normalized)
        if not addresses:
            raise OSError("outbound host was not validated")
        return [
            {
                "hostname": host,
                "host": address,
                "port": port,
                "family": socket.AF_INET6 if ":" in address else socket.AF_INET,
                "proto": 0,
                "flags": 0,
            }
            for address in addresses
        ]

    async def close(self) -> None:
        self._records.clear()


def sanitize_outbound_headers(headers: Optional[Mapping[str, Any]]) -> dict[str, str]:
    if headers is None:
        return {}
    if not isinstance(headers, Mapping):
        raise OutboundRequestError(
            "invalid_headers",
            "outbound request headers must be an object",
        )
    if len(headers) > OUTBOUND_HTTP_MAX_HEADER_COUNT:
        raise OutboundRequestError(
            "invalid_headers",
            "outbound request has too many headers",
        )

    sanitized: dict[str, str] = {}
    total_bytes = 0
    for raw_name, raw_value in headers.items():
        name = str(raw_name or "").strip()
        value = str(raw_value or "")
        lowered = name.lower()
        if (
            not name
            or not _HEADER_NAME_RE.fullmatch(name)
            or lowered in _BLOCKED_REQUEST_HEADERS
            or lowered.startswith("proxy-")
            or any(character in value for character in ("\r", "\n", "\x00"))
        ):
            raise OutboundRequestError(
                "invalid_headers",
                "outbound request contains a disallowed header",
            )
        total_bytes += len(name.encode("utf-8")) + len(value.encode("utf-8"))
        if total_bytes > OUTBOUND_HTTP_MAX_HEADER_BYTES:
            raise OutboundRequestError(
                "invalid_headers",
                "outbound request headers are too large",
            )
        sanitized[name] = value
    return sanitized


def _bounded_timeout(timeout_seconds: Any) -> float:
    try:
        timeout = float(timeout_seconds)
    except (TypeError, ValueError) as exc:
        raise OutboundRequestError(
            "invalid_timeout",
            "outbound request timeout is invalid",
        ) from exc
    if not math.isfinite(timeout) or timeout <= 0:
        raise OutboundRequestError(
            "invalid_timeout",
            "outbound request timeout is invalid",
        )
    return min(timeout, OUTBOUND_HTTP_MAX_TIMEOUT_SECONDS)


def _without_cross_origin_secrets(headers: Mapping[str, str]) -> dict[str, str]:
    return {
        name: value
        for name, value in headers.items()
        if name.lower() not in _CROSS_ORIGIN_SENSITIVE_HEADERS
    }


async def request_public_http(
    method: str,
    url: Any,
    *,
    headers: Optional[Mapping[str, Any]] = None,
    params: Optional[Mapping[str, Any]] = None,
    json_body: Any = None,
    timeout_seconds: Any = 10,
    max_redirects: int = OUTBOUND_HTTP_MAX_REDIRECTS,
    max_response_bytes: int = OUTBOUND_HTTP_MAX_RESPONSE_BYTES,
    allowed_methods: Sequence[str] = ("GET", "POST", "PUT"),
    require_https: bool = False,
) -> PublicHTTPResponse:
    """Send a public-only HTTP request with pinned DNS and checked redirects."""
    request_method = str(method or "").strip().upper()
    permitted_methods = {str(value).upper() for value in allowed_methods}
    if request_method not in permitted_methods:
        raise OutboundRequestError(
            "method_not_allowed",
            "outbound HTTP method is not allowed",
        )
    try:
        redirect_limit = max(0, min(int(max_redirects), 5))
        response_limit = max(1, min(int(max_response_bytes), 4 * 1024 * 1024))
    except (TypeError, ValueError) as exc:
        raise OutboundRequestError(
            "invalid_request_limits",
            "outbound request limits are invalid",
        ) from exc

    current_url = str(url or "").strip()
    current_headers = sanitize_outbound_headers(headers)
    current_method = request_method
    current_params = params
    current_json = json_body
    previous_origin: Optional[tuple[str, str, int]] = None
    resolver = PinnedPublicResolver()
    timeout = aiohttp.ClientTimeout(total=_bounded_timeout(timeout_seconds))
    connector = aiohttp.TCPConnector(
        resolver=resolver,
        use_dns_cache=False,
        force_close=True,
    )

    try:
        async with aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            trust_env=False,
        ) as session:
            for redirect_count in range(redirect_limit + 1):
                scheme, _, _ = parse_public_http_url(current_url)
                if require_https and scheme != "https":
                    raise OutboundRequestError(
                        "insecure_transport_denied",
                        "outbound endpoint must use https",
                    )
                target = await resolve_public_http_url(current_url)
                resolver.pin(target)
                if previous_origin is not None and target.origin != previous_origin:
                    current_headers = _without_cross_origin_secrets(current_headers)

                try:
                    async with session.request(
                        current_method,
                        current_url,
                        headers=current_headers or None,
                        params=current_params,
                        json=current_json,
                        allow_redirects=False,
                    ) as response:
                        if response.status in _REDIRECT_STATUSES:
                            if redirect_count >= redirect_limit:
                                raise OutboundRequestError(
                                    "too_many_redirects",
                                    "outbound endpoint redirected too many times",
                                )
                            location = str(response.headers.get("Location") or "").strip()
                            if not location:
                                raise OutboundRequestError(
                                    "invalid_redirect",
                                    "outbound endpoint returned an invalid redirect",
                                )
                            previous_origin = target.origin
                            current_url = urljoin(current_url, location)
                            parse_public_http_url(current_url)
                            current_params = None
                            if response.status in {301, 302, 303} and current_method != "GET":
                                current_method = "GET"
                                current_json = None
                            continue

                        body = await response.content.read(response_limit + 1)
                        if len(body) > response_limit:
                            raise OutboundRequestError(
                                "response_too_large",
                                "outbound endpoint response is too large",
                            )
                        return PublicHTTPResponse(
                            status=int(response.status),
                            headers=dict(response.headers),
                            body=body,
                        )
                except OutboundRequestError:
                    raise
                except (aiohttp.ClientError, asyncio.TimeoutError, TimeoutError) as exc:
                    raise OutboundRequestError(
                        "network_error",
                        "outbound endpoint request failed",
                    ) from exc
    finally:
        if not connector.closed:
            await connector.close()

    raise OutboundRequestError(
        "outbound_request_failed",
        "outbound endpoint request failed",
    )


def request_public_http_sync(*args: Any, **kwargs: Any) -> PublicHTTPResponse:
    """Synchronous wrapper for worker threads used by the delivery outbox."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(request_public_http(*args, **kwargs))
    raise OutboundRequestError(
        "sync_request_in_event_loop",
        "synchronous outbound request must run in a worker thread",
    )


def outbound_target_label(url: Any) -> str:
    """Return a log-safe target label without path, query, or credentials."""
    try:
        scheme, host, port = parse_public_http_url(url)
    except OutboundRequestError:
        return "invalid-target"
    default_port = 443 if scheme == "https" else 80
    return host if port == default_port else f"{host}:{port}"
