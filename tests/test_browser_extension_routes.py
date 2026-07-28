import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from starlette.requests import Request

import reply_server
from browser_extension_pairing import BrowserExtensionPairingManager
from utils.xianyu_session_probe import SessionProbeResult


def _request(*, client_host: str, host: str = "xianyu.cxywjx.top") -> Request:
    return Request({
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "path": "/api/browser-extension/import",
        "raw_path": b"/api/browser-extension/import",
        "query_string": b"",
        "headers": [
            (b"host", host.encode("ascii")),
            (b"content-length", b"1024"),
        ],
        "client": (client_host, 44321),
        "server": (host, 443),
    })


def _cookies():
    return [
        {
            "name": "unb",
            "value": "account-1",
            "domain": ".goofish.com",
            "path": "/",
        },
        {
            "name": "cookie2",
            "value": "session-cookie",
            "domain": ".goofish.com",
            "path": "/",
        },
    ]


class BrowserExtensionRouteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.manager = BrowserExtensionPairingManager()
        self.owner = {"user_id": 7, "username": "operator"}

    async def test_v2_remote_import_is_owner_bound_and_single_use(self):
        with patch.object(reply_server, "browser_extension_pairings", self.manager):
            created = reply_server.create_browser_extension_pairing(
                current_user=self.owner,
            )["data"]

        self.assertEqual(created["protocol_version"], 2)
        self.assertEqual(
            created["import_url"],
            "https://xianyu.cxywjx.top/api/browser-extension/import",
        )
        self.assertEqual(created["console_origin"], "https://xianyu.cxywjx.top")
        self.assertGreaterEqual(len(created["pairing_token"]), 32)

        payload = reply_server.BrowserExtensionImportIn(
            protocol_version=2,
            pairing_id=created["pairing_id"],
            pairing_token=created["pairing_token"],
            cookies=_cookies(),
            user_agent="Chrome Route Test",
        )
        probe = SessionProbeResult(
            status="success",
            cookies={"unb": "account-1", "cookie2": "session-cookie"},
            access_token="validated-access-token",
        )
        persist = AsyncMock(return_value={
            "account_id": "owned-row",
            "is_new_account": True,
        })

        with (
            patch.object(reply_server, "browser_extension_pairings", self.manager),
            patch.object(reply_server, "probe_message_session_async", AsyncMock(return_value=probe)),
            patch.object(reply_server, "_persist_validated_account_login", persist),
        ):
            imported = await reply_server.import_browser_extension_cookies(
                payload,
                _request(client_host="203.0.113.20"),
            )
            with self.assertRaises(HTTPException) as replay:
                await reply_server.import_browser_extension_cookies(
                    payload,
                    _request(client_host="203.0.113.20"),
                )

        self.assertTrue(imported["success"])
        self.assertEqual(imported["data"]["account_id"], "owned-row")
        self.assertEqual(imported["data"]["ended_by"], "validated_and_persisted")
        self.assertEqual(persist.await_args.kwargs["user_id"], 7)
        self.assertEqual(replay.exception.status_code, 409)
        with self.assertRaises(HTTPException) as other_owner:
            with patch.object(reply_server, "browser_extension_pairings", self.manager):
                reply_server.get_browser_extension_pairing(
                    created["pairing_id"],
                    current_user={"user_id": 8, "username": "other"},
                )
        self.assertEqual(other_owner.exception.status_code, 404)

    async def test_v1_compatibility_remains_loopback_only(self):
        with patch.object(reply_server, "browser_extension_pairings", self.manager):
            created = reply_server.create_browser_extension_pairing(
                current_user=self.owner,
            )["data"]

        payload = reply_server.BrowserExtensionImportIn(
            pairing_id=created["pairing_id"],
            pairing_code=created["pairing_code"],
            cookies=_cookies(),
            user_agent="Legacy Chrome Route Test",
        )
        probe = SessionProbeResult(
            status="success",
            cookies={"unb": "account-1", "cookie2": "session-cookie"},
            access_token="validated-access-token",
        )

        with (
            patch.object(reply_server, "browser_extension_pairings", self.manager),
            patch.object(reply_server, "probe_message_session_async", AsyncMock(return_value=probe)),
            patch.object(
                reply_server,
                "_persist_validated_account_login",
                AsyncMock(return_value={"account_id": "legacy-row", "is_new_account": False}),
            ),
        ):
            with self.assertRaises(HTTPException) as remote:
                await reply_server.import_browser_extension_cookies(
                    payload,
                    _request(client_host="203.0.113.20"),
                )
            with self.assertRaises(HTTPException) as proxied_remote:
                await reply_server.import_browser_extension_cookies(
                    payload,
                    _request(client_host="127.0.0.1"),
                )
            imported = await reply_server.import_browser_extension_cookies(
                payload,
                _request(client_host="127.0.0.1", host="127.0.0.1:8091"),
            )

        self.assertEqual(remote.exception.status_code, 403)
        self.assertEqual(proxied_remote.exception.status_code, 403)
        self.assertEqual(imported["data"]["account_id"], "legacy-row")


if __name__ == "__main__":
    unittest.main()
