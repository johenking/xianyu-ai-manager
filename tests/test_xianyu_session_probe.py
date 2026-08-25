import json
import unittest

import httpx

from utils.xianyu_session_probe import (
    PROBE_EXPIRED,
    PROBE_RETRYABLE_ERROR,
    PROBE_SUCCESS,
    PROBE_VERIFICATION_REQUIRED,
    build_probe_request,
    classify_probe_response,
    probe_message_session_async,
    probe_message_session_sync,
)


def probe_response(payload, *set_cookie_headers):
    return httpx.Response(
        200,
        json=payload,
        headers=[("set-cookie", value) for value in set_cookie_headers],
        request=httpx.Request("POST", "https://h5api.m.goofish.com/"),
    )


class ScriptedSyncClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, *, params, data, headers):
        self.calls.append({
            "url": url,
            "params": dict(params),
            "data": dict(data),
            "headers": dict(headers),
        })
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class ScriptedAsyncClient(ScriptedSyncClient):
    async def post(self, url, *, params, data, headers):
        return super().post(url, params=params, data=data, headers=headers)


class XianyuSessionProbeTests(unittest.TestCase):
    def test_success_requires_a_real_access_token_and_merges_response_cookies(self):
        result = classify_probe_response(
            {
                "ret": ["SUCCESS::调用成功"],
                "data": {"accessToken": "synthetic-token"},
            },
            {"unb": "9988", "cookie2": "old"},
            set_cookie_headers=("cookie2=renewed; Path=/; Secure",),
        )

        self.assertEqual(result.status, PROBE_SUCCESS)
        self.assertTrue(result.succeeded)
        self.assertEqual(result.cookies["cookie2"], "renewed")

    def test_success_text_without_access_token_remains_unverified(self):
        result = classify_probe_response(
            {"ret": ["SUCCESS::调用成功"], "data": {}},
            {"unb": "9988", "cookie2": "old"},
        )

        self.assertEqual(result.status, PROBE_RETRYABLE_ERROR)
        self.assertFalse(result.succeeded)

    def test_user_validate_keeps_only_an_allowlisted_internal_url(self):
        allowed = classify_probe_response(
            {
                "ret": ["FAIL_SYS_USER_VALIDATE::需要验证"],
                "data": {"url": "https://passport.goofish.com/iv/check"},
            },
            {"unb": "9988"},
        )
        blocked = classify_probe_response(
            {
                "ret": ["FAIL_SYS_USER_VALIDATE::需要验证"],
                "data": {"url": "https://example.invalid/steal"},
            },
            {"unb": "9988"},
        )

        self.assertEqual(allowed.status, PROBE_VERIFICATION_REQUIRED)
        self.assertEqual(allowed.verification_url, "https://passport.goofish.com/iv/check")
        self.assertEqual(blocked.status, PROBE_VERIFICATION_REQUIRED)
        self.assertEqual(blocked.verification_url, "")

    def test_expired_session_is_distinct_from_human_verification(self):
        result = classify_probe_response(
            {"ret": ["FAIL_SYS_SESSION_EXPIRED::Session过期"], "data": {}},
            {"unb": "9988"},
        )

        self.assertEqual(result.status, PROBE_EXPIRED)

    def test_probe_headers_use_the_official_browser_ua_without_fixed_client_hints(self):
        user_agent = "Mozilla/5.0 Synthetic Chrome/150.0.0.0 Safari/537.36"
        _, _, _, headers = build_probe_request(
            "unb=9988; _m_h5_tk=token_123; cookie2=session",
            user_agent,
            timestamp_ms=1_700_000_000_000,
        )

        self.assertEqual(headers["user-agent"], user_agent)
        self.assertNotIn("sec-ch-ua", headers)
        self.assertNotIn("sec-ch-ua-platform", headers)

    def test_probe_payload_prefers_the_listener_device_id_over_derived_one(self):
        # 探测必须与 WebSocket 注册使用同一 device_id，否则平台返回
        # device_id_or_appkey_is_not_equal 拒绝注册。
        cookie_string = "unb=9988; _m_h5_tk=token_123; cookie2=session"
        user_agent = "Mozilla/5.0 Synthetic Chrome/150.0.0.0 Safari/537.36"
        _, _, explicit_data, _ = build_probe_request(
            cookie_string,
            user_agent,
            device_id="listener-device-9988",
            timestamp_ms=1_700_000_000_000,
        )
        _, _, derived_data, _ = build_probe_request(
            cookie_string,
            user_agent,
            timestamp_ms=1_700_000_000_000,
        )
        _, _, blank_data, _ = build_probe_request(
            cookie_string,
            user_agent,
            device_id="   ",
            timestamp_ms=1_700_000_000_000,
        )

        def payload_device_id(data: dict) -> str:
            return json.loads(data["data"])["deviceId"]

        self.assertEqual(
            payload_device_id(explicit_data), "listener-device-9988"
        )
        # 未显式传入或传空白时回退为按 unb 派生（随机 UUID + unb 后缀）。
        self.assertTrue(payload_device_id(derived_data).endswith("-9988"))
        self.assertNotEqual(
            payload_device_id(derived_data), "listener-device-9988"
        )
        self.assertTrue(payload_device_id(blank_data).endswith("-9988"))
        self.assertNotEqual(
            payload_device_id(blank_data), "listener-device-9988"
        )

    def test_sync_probe_resigns_once_with_fresh_h5_token_and_keeps_all_cookies(self):
        client = ScriptedSyncClient([
            probe_response(
                {"ret": ["FAIL_SYS_TOKEN_EXOIRED::令牌过期"], "data": {}},
                "_m_h5_tk=fresh-token_2000000000000; Path=/; Secure",
                "x5sec=verification-cookie; Path=/; Secure",
            ),
            probe_response(
                {
                    "ret": ["SUCCESS::调用成功"],
                    "data": {"accessToken": "message-token"},
                },
                "cookie2=renewed-session; Path=/; Secure",
            ),
        ])

        result = probe_message_session_sync(
            "unb=9988; cookie2=old; _m_h5_tk=stale-token_1000000000000",
            "Mozilla/5.0 Synthetic Chrome/150.0.0.0 Safari/537.36",
            client_factory=lambda: client,
        )

        self.assertTrue(result.succeeded)
        self.assertEqual(len(client.calls), 2)
        self.assertNotEqual(
            client.calls[0]["params"]["sign"],
            client.calls[1]["params"]["sign"],
        )
        self.assertGreater(
            int(client.calls[1]["params"]["t"]),
            int(client.calls[0]["params"]["t"]),
        )
        self.assertIn("_m_h5_tk=fresh-token_2000000000000", client.calls[1]["headers"]["cookie"])
        self.assertIn("x5sec=verification-cookie", client.calls[1]["headers"]["cookie"])
        self.assertEqual(result.cookies["_m_h5_tk"], "fresh-token_2000000000000")
        self.assertEqual(result.cookies["x5sec"], "verification-cookie")
        self.assertEqual(result.cookies["cookie2"], "renewed-session")

    def test_sync_probe_does_not_retry_without_a_new_h5_token(self):
        client = ScriptedSyncClient([
            probe_response(
                {"ret": ["FAIL_SYS_TOKEN_EXOIRED::令牌过期"], "data": {}},
                "x5sec=verification-cookie; Path=/; Secure",
            ),
        ])

        result = probe_message_session_sync(
            "unb=9988; cookie2=old; _m_h5_tk=stale-token_1000000000000",
            "Mozilla/5.0 Synthetic Chrome/150.0.0.0 Safari/537.36",
            client_factory=lambda: client,
        )

        self.assertEqual(result.status, PROBE_EXPIRED)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(result.cookies["x5sec"], "verification-cookie")

    def test_sync_probe_does_not_retry_human_verification(self):
        client = ScriptedSyncClient([
            probe_response(
                {
                    "ret": ["FAIL_SYS_USER_VALIDATE::需要验证"],
                    "data": {"url": "https://passport.goofish.com/iv/check"},
                },
                "_m_h5_tk=fresh-token_2000000000000; Path=/; Secure",
            ),
        ])

        result = probe_message_session_sync(
            "unb=9988; cookie2=old; _m_h5_tk=stale-token_1000000000000",
            "Mozilla/5.0 Synthetic Chrome/150.0.0.0 Safari/537.36",
            client_factory=lambda: client,
        )

        self.assertEqual(result.status, PROBE_VERIFICATION_REQUIRED)
        self.assertEqual(len(client.calls), 1)

    def test_sync_probe_does_not_retry_combined_token_expiry_and_verification(self):
        client = ScriptedSyncClient([
            probe_response(
                {
                    "ret": [
                        "FAIL_SYS_TOKEN_EXOIRED::令牌过期",
                        "FAIL_SYS_USER_VALIDATE::需要验证",
                    ],
                    "data": {"url": "https://passport.goofish.com/iv/check"},
                },
                "_m_h5_tk=fresh-token_2000000000000; Path=/; Secure",
            ),
        ])

        result = probe_message_session_sync(
            "unb=9988; cookie2=old; _m_h5_tk=stale-token_1000000000000",
            "Mozilla/5.0 Synthetic Chrome/150.0.0.0 Safari/537.36",
            client_factory=lambda: client,
        )

        self.assertEqual(result.status, PROBE_VERIFICATION_REQUIRED)
        self.assertEqual(len(client.calls), 1)

    def test_sync_probe_retries_at_most_once(self):
        client = ScriptedSyncClient([
            probe_response(
                {"ret": ["FAIL_SYS_TOKEN_EXOIRED::令牌过期"], "data": {}},
                "_m_h5_tk=fresh-token_2000000000000; Path=/; Secure",
            ),
            probe_response(
                {"ret": ["FAIL_SYS_TOKEN_EXOIRED::令牌过期"], "data": {}},
                "_m_h5_tk=newer-token_3000000000000; Path=/; Secure",
            ),
        ])

        result = probe_message_session_sync(
            "unb=9988; cookie2=old; _m_h5_tk=stale-token_1000000000000",
            "Mozilla/5.0 Synthetic Chrome/150.0.0.0 Safari/537.36",
            client_factory=lambda: client,
        )

        self.assertEqual(result.status, PROBE_EXPIRED)
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(result.cookies["_m_h5_tk"], "newer-token_3000000000000")

    def test_sync_probe_does_not_retry_other_expiry_or_temporary_errors(self):
        for ret_value in (
            "FAIL_SYS_SESSION_EXPIRED::Session过期",
            "FAIL_SYS_TRAFFIC_LIMIT::系统繁忙",
        ):
            with self.subTest(ret_value=ret_value):
                client = ScriptedSyncClient([
                    probe_response(
                        {"ret": [ret_value], "data": {}},
                        "_m_h5_tk=fresh-token_2000000000000; Path=/; Secure",
                    ),
                ])

                result = probe_message_session_sync(
                    "unb=9988; cookie2=old; _m_h5_tk=stale-token_1000000000000",
                    "Mozilla/5.0 Synthetic Chrome/150.0.0.0 Safari/537.36",
                    client_factory=lambda: client,
                )

                self.assertEqual(len(client.calls), 1)
                self.assertEqual(
                    result.status,
                    PROBE_EXPIRED if "SESSION_EXPIRED" in ret_value else PROBE_RETRYABLE_ERROR,
                )

    def test_sync_probe_keeps_first_response_cookies_when_retry_raises(self):
        client = ScriptedSyncClient([
            probe_response(
                {"ret": ["FAIL_SYS_TOKEN_EXOIRED::令牌过期"], "data": {}},
                "_m_h5_tk=fresh-token_2000000000000; Path=/; Secure",
                "x5sec=verification-cookie; Path=/; Secure",
            ),
            httpx.ConnectError("synthetic retry failure"),
        ])

        result = probe_message_session_sync(
            "unb=9988; cookie2=old; _m_h5_tk=stale-token_1000000000000",
            "Mozilla/5.0 Synthetic Chrome/150.0.0.0 Safari/537.36",
            client_factory=lambda: client,
        )

        self.assertEqual(result.status, PROBE_RETRYABLE_ERROR)
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(result.cookies["_m_h5_tk"], "fresh-token_2000000000000")
        self.assertEqual(result.cookies["x5sec"], "verification-cookie")


class XianyuSessionProbeAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_probe_resigns_once_with_fresh_h5_token(self):
        client = ScriptedAsyncClient([
            probe_response(
                {"ret": ["FAIL_SYS_TOKEN_EXOIRED::令牌过期"], "data": {}},
                "_m_h5_tk=fresh-token_2000000000000; Path=/; Secure",
                "x5sec=verification-cookie; Path=/; Secure",
            ),
            probe_response(
                {
                    "ret": ["SUCCESS::调用成功"],
                    "data": {"accessToken": "message-token"},
                },
                "cookie2=renewed-session; Path=/; Secure",
            ),
        ])

        result = await probe_message_session_async(
            "unb=9988; cookie2=old; _m_h5_tk=stale-token_1000000000000",
            "Mozilla/5.0 Synthetic Chrome/150.0.0.0 Safari/537.36",
            client_factory=lambda: client,
        )

        self.assertTrue(result.succeeded)
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(result.cookies["_m_h5_tk"], "fresh-token_2000000000000")
        self.assertEqual(result.cookies["x5sec"], "verification-cookie")
        self.assertEqual(result.cookies["cookie2"], "renewed-session")

    async def test_async_probe_keeps_first_response_cookies_when_retry_raises(self):
        client = ScriptedAsyncClient([
            probe_response(
                {"ret": ["FAIL_SYS_TOKEN_EXOIRED::令牌过期"], "data": {}},
                "_m_h5_tk=fresh-token_2000000000000; Path=/; Secure",
                "x5sec=verification-cookie; Path=/; Secure",
            ),
            httpx.ConnectError("synthetic retry failure"),
        ])

        result = await probe_message_session_async(
            "unb=9988; cookie2=old; _m_h5_tk=stale-token_1000000000000",
            "Mozilla/5.0 Synthetic Chrome/150.0.0.0 Safari/537.36",
            client_factory=lambda: client,
        )

        self.assertEqual(result.status, PROBE_RETRYABLE_ERROR)
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(result.cookies["_m_h5_tk"], "fresh-token_2000000000000")
        self.assertEqual(result.cookies["x5sec"], "verification-cookie")


if __name__ == "__main__":
    unittest.main()
