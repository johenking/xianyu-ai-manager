import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from utils.outbound_http import (
    OutboundRequestError,
    PinnedPublicResolver,
    ValidatedPublicURL,
    parse_public_http_url,
    request_public_http,
    resolve_public_http_url,
    sanitize_outbound_headers,
)


class _FakeContent:
    def __init__(self, body: bytes):
        self.body = body

    async def read(self, limit: int) -> bytes:
        return self.body[:limit]


class _FakeResponse:
    def __init__(self, status: int, *, headers=None, body: bytes = b""):
        self.status = status
        self.headers = headers or {}
        self.content = _FakeContent(body)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


class _FakeConnector:
    def __init__(self, *args, **kwargs):
        self.resolver = kwargs.get("resolver")
        self.closed = False

    async def close(self):
        self.closed = True


def _fake_session(responses, calls):
    class FakeSession:
        def __init__(self, *args, **kwargs):
            self.responses = iter(responses)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        def request(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
            return next(self.responses)

    return FakeSession


def _public_target(url: str, address: str = "8.8.8.8") -> ValidatedPublicURL:
    scheme, host, port = parse_public_http_url(url)
    return ValidatedPublicURL(url, scheme, host, port, (address,))


class OutboundUrlValidationTests(unittest.IsolatedAsyncioTestCase):
    async def test_private_literal_addresses_are_denied(self):
        for url in (
            "http://127.0.0.1/admin",
            "http://169.254.169.254/latest/meta-data",
            "http://[::1]/admin",
            "http://10.0.0.1/internal",
        ):
            with self.subTest(url=url), self.assertRaises(OutboundRequestError) as raised:
                await resolve_public_http_url(url)
            self.assertEqual(raised.exception.code, "non_public_address_denied")

    async def test_mixed_public_and_private_dns_answer_is_denied(self):
        loop = unittest.mock.Mock()
        loop.getaddrinfo = AsyncMock(
            return_value=[
                (2, 1, 6, "", ("8.8.8.8", 443)),
                (2, 1, 6, "", ("127.0.0.1", 443)),
            ]
        )
        with patch("utils.outbound_http.asyncio.get_running_loop", return_value=loop):
            with self.assertRaises(OutboundRequestError) as raised:
                await resolve_public_http_url("https://example.test/hook")
        self.assertEqual(raised.exception.code, "non_public_address_denied")

    async def test_pinned_resolver_refuses_unvalidated_hosts(self):
        resolver = PinnedPublicResolver()
        resolver.pin(_public_target("https://example.test/hook"))
        records = await resolver.resolve("example.test", 443)
        self.assertEqual(records[0]["host"], "8.8.8.8")
        with self.assertRaises(OSError):
            await resolver.resolve("other.test", 443)

    def test_credentials_fragments_and_non_http_schemes_are_denied(self):
        for url in (
            "https://user:secret@example.test/hook",
            "https://example.test/hook#fragment",
            "file:///etc/passwd",
        ):
            with self.subTest(url=url), self.assertRaises(OutboundRequestError):
                parse_public_http_url(url)

    def test_routing_and_smuggling_headers_are_denied(self):
        for headers in (
            {"Host": "127.0.0.1"},
            {"Proxy-Authorization": "secret"},
            {"X-Test": "ok\r\nHost: 127.0.0.1"},
        ):
            with self.subTest(headers=headers), self.assertRaises(OutboundRequestError):
                sanitize_outbound_headers(headers)


class OutboundRequestFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_https_requirement_rejects_plaintext_before_dns(self):
        calls = []
        resolution = AsyncMock()
        with patch("utils.outbound_http.aiohttp.TCPConnector", _FakeConnector), patch(
            "utils.outbound_http.aiohttp.ClientSession",
            _fake_session([], calls),
        ), patch("utils.outbound_http.resolve_public_http_url", resolution):
            with self.assertRaises(OutboundRequestError) as raised:
                await request_public_http(
                    "POST",
                    "http://example.test/hook",
                    require_https=True,
                )

        self.assertEqual(raised.exception.code, "insecure_transport_denied")
        resolution.assert_not_awaited()
        self.assertEqual(calls, [])

    async def test_https_requirement_rejects_redirect_downgrade(self):
        calls = []
        responses = [
            _FakeResponse(302, headers={"Location": "http://other.test/final"}),
        ]
        resolution = AsyncMock(
            return_value=_public_target("https://example.test/start")
        )
        with patch("utils.outbound_http.aiohttp.TCPConnector", _FakeConnector), patch(
            "utils.outbound_http.aiohttp.ClientSession",
            _fake_session(responses, calls),
        ), patch("utils.outbound_http.resolve_public_http_url", resolution):
            with self.assertRaises(OutboundRequestError) as raised:
                await request_public_http(
                    "POST",
                    "https://example.test/start",
                    require_https=True,
                )

        self.assertEqual(raised.exception.code, "insecure_transport_denied")
        self.assertEqual(resolution.await_count, 1)
        self.assertEqual(len(calls), 1)

    async def test_redirect_to_private_target_is_rejected_before_second_request(self):
        calls = []
        responses = [
            _FakeResponse(302, headers={"Location": "http://127.0.0.1/internal"}),
        ]
        resolution = AsyncMock(
            side_effect=[
                _public_target("https://example.test/start"),
                OutboundRequestError(
                    "non_public_address_denied",
                    "outbound host resolved to a non-public address",
                ),
            ]
        )
        with patch("utils.outbound_http.aiohttp.TCPConnector", _FakeConnector), patch(
            "utils.outbound_http.aiohttp.ClientSession",
            _fake_session(responses, calls),
        ), patch("utils.outbound_http.resolve_public_http_url", resolution):
            with self.assertRaises(OutboundRequestError) as raised:
                await request_public_http("GET", "https://example.test/start")

        self.assertEqual(raised.exception.code, "non_public_address_denied")
        self.assertEqual(len(calls), 1)
        self.assertFalse(calls[0][2]["allow_redirects"])

    async def test_cross_origin_redirect_strips_authorization_and_cookie(self):
        calls = []
        responses = [
            _FakeResponse(302, headers={"Location": "https://other.test/final"}),
            _FakeResponse(200, body=b"ok"),
        ]
        resolution = AsyncMock(
            side_effect=[
                _public_target("https://example.test/start"),
                _public_target("https://other.test/final", "1.1.1.1"),
            ]
        )
        with patch("utils.outbound_http.aiohttp.TCPConnector", _FakeConnector), patch(
            "utils.outbound_http.aiohttp.ClientSession",
            _fake_session(responses, calls),
        ), patch("utils.outbound_http.resolve_public_http_url", resolution):
            response = await request_public_http(
                "POST",
                "https://example.test/start",
                headers={
                    "Authorization": "Bearer secret",
                    "Cookie": "session=secret",
                    "X-Trace": "trace",
                },
                json_body={"value": 1},
            )

        self.assertEqual(response.body, b"ok")
        redirected_headers = calls[1][2]["headers"]
        self.assertNotIn("Authorization", redirected_headers)
        self.assertNotIn("Cookie", redirected_headers)
        self.assertEqual(redirected_headers["X-Trace"], "trace")
        self.assertEqual(calls[1][0], "GET")
        self.assertIsNone(calls[1][2]["json"])

    async def test_response_body_limit_is_enforced(self):
        calls = []
        responses = [_FakeResponse(200, body=b"12345")]
        with patch("utils.outbound_http.aiohttp.TCPConnector", _FakeConnector), patch(
            "utils.outbound_http.aiohttp.ClientSession",
            _fake_session(responses, calls),
        ), patch(
            "utils.outbound_http.resolve_public_http_url",
            AsyncMock(return_value=_public_target("https://example.test/data")),
        ):
            with self.assertRaises(OutboundRequestError) as raised:
                await request_public_http(
                    "GET",
                    "https://example.test/data",
                    max_response_bytes=4,
                )
        self.assertEqual(raised.exception.code, "response_too_large")
