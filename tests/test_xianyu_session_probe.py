import unittest

from utils.xianyu_session_probe import (
    PROBE_EXPIRED,
    PROBE_RETRYABLE_ERROR,
    PROBE_SUCCESS,
    PROBE_VERIFICATION_REQUIRED,
    build_probe_request,
    classify_probe_response,
)


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


if __name__ == "__main__":
    unittest.main()
