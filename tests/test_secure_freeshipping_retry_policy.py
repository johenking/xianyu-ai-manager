"""SecureFreeshipping.auto_freeshipping 重试策略测试。

原实现对任意非 SUCCESS 响应无间隔递归重试最多约4次，且不认"已发货"，
容易触发平台风控并把已发货订单反复重试。新策略与确认发货对齐，按平台 ret 分类：
- SUCCESS / 已发货（重复免拼）→ 成功返回，不重试；
- 会话失效 / 风控需人工 → 立即失败关闭，不重试；
- 限流 / 5xx / 网络异常 → 指数退避（带抖动）重试，且有总次数上限；
- 未知业务失败 → 默认失败关闭，不重试（供上层自愈回退到确认发货）；
- 关键参数缺失 → 直接失败关闭，不发网络。

测试中 monkeypatch 掉真实 asyncio.sleep 与网络请求，不真实等待、不联网。
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import secure_freeshipping_decrypted
from secure_freeshipping_decrypted import (
    BACKOFF_MAX_SECONDS,
    MAX_FREESHIPPING_ATTEMPTS,
    SecureFreeshipping,
    _compute_backoff_seconds,
    classify_freeshipping_ret,
)

# 含 _m_h5_tk 的最小可用 Cookie（伪造值，仅供本地测试签名计算）
COOKIES = "_m_h5_tk=faketoken123_1234567890; unb=42"


class _FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status = status
        self.headers = {}

    async def json(self):
        return self._payload


class _FakePostContext:
    def __init__(self, outcome):
        self._outcome = outcome

    async def __aenter__(self):
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeSession:
    """按顺序回放预设结果；结果耗尽后重复最后一个。"""

    def __init__(self, outcomes):
        self._outcomes = list(outcomes)
        self.post_calls = 0

    def post(self, url, params=None, data=None):
        index = min(self.post_calls, len(self._outcomes) - 1)
        self.post_calls += 1
        return _FakePostContext(self._outcomes[index])


def _ret_response(ret_value):
    return _FakeResponse({"ret": [ret_value]})


class ClassifyFreeshippingRetTests(unittest.TestCase):
    """分类器本身的行为。"""

    def test_success(self):
        self.assertEqual(
            classify_freeshipping_ret(["SUCCESS::调用成功"])[0], "success"
        )

    def test_already_shipped_variants(self):
        for ret in (
            "FAIL_BIZ_ORDER_CONSIGNED::该订单已发货，请勿重复操作",
            "FAIL_BIZ_DUPLICATE::重复确认发货",
            "FAIL_BIZ_GROUPON::已免拼",
        ):
            with self.subTest(ret=ret):
                self.assertEqual(
                    classify_freeshipping_ret([ret])[0], "already_shipped"
                )

    def test_session_and_token_expired(self):
        for ret in (
            "FAIL_SYS_SESSION_EXPIRED::Session过期",
            "FAIL_SYS_TOKEN_EXOIRED::令牌过期",
            "FAIL_SYS_TOKEN_EXPIRED::令牌过期",
        ):
            with self.subTest(ret=ret):
                self.assertEqual(
                    classify_freeshipping_ret([ret])[0], "session_invalid"
                )

    def test_risk_control(self):
        for ret in (
            "RGV587_ERROR::SM",
            "FAIL_SYS_USER_VALIDATE::需要验证",
        ):
            with self.subTest(ret=ret):
                self.assertEqual(
                    classify_freeshipping_ret([ret])[0], "human_intervention"
                )

    def test_rate_limited(self):
        for ret in (
            "FAIL_SYS_TRAFFIC_LIMIT::哎哟喂,被挤爆啦,请稍后重试",
            "HTTP_429::免拼发货接口HTTP状态异常",
        ):
            with self.subTest(ret=ret):
                self.assertEqual(
                    classify_freeshipping_ret([ret])[0], "rate_limited"
                )

    def test_http_5xx_is_retryable_platform_error(self):
        self.assertEqual(
            classify_freeshipping_ret(["HTTP_502::免拼发货接口HTTP状态异常"])[0],
            "platform_unavailable",
        )

    def test_unknown_and_empty_fail_closed(self):
        self.assertEqual(
            classify_freeshipping_ret(["FAIL_BIZ_NOT_GROUPON::非拼团订单"])[0],
            "unknown_failure",
        )
        self.assertEqual(classify_freeshipping_ret([])[0], "unknown_failure")
        self.assertEqual(classify_freeshipping_ret(None)[0], "unknown_failure")


class BackoffComputationTests(unittest.TestCase):
    """退避时间计算：指数增长、抖动区间、封顶。"""

    def test_exponential_growth_with_unit_jitter(self):
        with patch.object(
            secure_freeshipping_decrypted.random, "uniform", return_value=1.0
        ):
            self.assertEqual(_compute_backoff_seconds(0), 5.0)
            self.assertEqual(_compute_backoff_seconds(1), 10.0)
            self.assertEqual(_compute_backoff_seconds(2), 20.0)

    def test_backoff_is_capped(self):
        with patch.object(
            secure_freeshipping_decrypted.random, "uniform", return_value=1.0
        ):
            self.assertEqual(_compute_backoff_seconds(10), BACKOFF_MAX_SECONDS)

    def test_jitter_stays_in_range(self):
        for _ in range(20):
            delay = _compute_backoff_seconds(0)
            self.assertGreaterEqual(delay, 5.0 * 0.5)
            self.assertLess(delay, 5.0 * 1.5)


class AutoFreeshippingRetryPolicyTests(unittest.TestCase):
    """auto_freeshipping 主流程：分类决定重试与否，退避通过 mock sleep 断言。"""

    def _call(self, outcomes, order_id="order-1", item_id="100", buyer_id="200", **kwargs):
        session = _FakeSession(outcomes)
        freeship = SecureFreeshipping(
            session=session, cookies_str=COOKIES, cookie_id="acct-test"
        )
        sleep_mock = AsyncMock()
        with patch.object(
            secure_freeshipping_decrypted.asyncio, "sleep", sleep_mock
        ), patch.object(
            secure_freeshipping_decrypted.random, "uniform", return_value=1.0
        ):
            result = asyncio.run(
                freeship.auto_freeshipping(order_id, item_id, buyer_id, **kwargs)
            )
        return result, session, sleep_mock

    def test_success_returns_without_retry_or_sleep(self):
        result, session, sleep_mock = self._call(
            [_ret_response("SUCCESS::调用成功")]
        )
        self.assertIs(result.get("success"), True)
        self.assertEqual(result.get("order_id"), "order-1")
        self.assertEqual(session.post_calls, 1)
        sleep_mock.assert_not_awaited()

    def test_already_shipped_is_idempotent_success_without_retry(self):
        result, session, sleep_mock = self._call(
            [_ret_response("FAIL_BIZ_ORDER_CONSIGNED::该订单已发货，请勿重复操作")]
        )
        self.assertIs(result.get("success"), True)
        self.assertIs(result.get("already_shipped"), True)
        self.assertEqual(session.post_calls, 1)
        sleep_mock.assert_not_awaited()

    def test_session_expired_fails_closed_immediately(self):
        result, session, sleep_mock = self._call(
            [_ret_response("FAIL_SYS_SESSION_EXPIRED::Session过期")]
        )
        self.assertNotIn("success", result)
        self.assertEqual(result.get("category"), "session_invalid")
        self.assertEqual(session.post_calls, 1)
        sleep_mock.assert_not_awaited()

    def test_risk_control_fails_closed_immediately(self):
        result, session, sleep_mock = self._call([_ret_response("RGV587_ERROR::SM")])
        self.assertNotIn("success", result)
        self.assertEqual(result.get("category"), "human_intervention")
        self.assertEqual(session.post_calls, 1)
        sleep_mock.assert_not_awaited()

    def test_rate_limited_retries_with_backoff_until_cap(self):
        result, session, sleep_mock = self._call(
            [_ret_response("FAIL_SYS_TRAFFIC_LIMIT::请求频繁")]
        )
        self.assertNotIn("success", result)
        self.assertEqual(result.get("category"), "rate_limited")
        self.assertIs(result.get("retry_exhausted"), True)
        self.assertEqual(session.post_calls, MAX_FREESHIPPING_ATTEMPTS)
        self.assertEqual(sleep_mock.await_count, MAX_FREESHIPPING_ATTEMPTS - 1)
        delays = [call.args[0] for call in sleep_mock.await_args_list]
        self.assertEqual(delays, [5.0, 10.0, 20.0])

    def test_rate_limited_then_success_recovers(self):
        result, session, sleep_mock = self._call(
            [
                _ret_response("FAIL_SYS_TRAFFIC_LIMIT::请求频繁"),
                _ret_response("SUCCESS::调用成功"),
            ]
        )
        self.assertIs(result.get("success"), True)
        self.assertEqual(session.post_calls, 2)
        self.assertEqual(sleep_mock.await_count, 1)
        self.assertEqual(sleep_mock.await_args_list[0].args[0], 5.0)

    def test_http_5xx_retries_with_backoff_until_cap(self):
        result, session, sleep_mock = self._call([_FakeResponse(None, status=502)])
        self.assertNotIn("success", result)
        self.assertEqual(result.get("category"), "platform_unavailable")
        self.assertEqual(session.post_calls, MAX_FREESHIPPING_ATTEMPTS)
        self.assertEqual(sleep_mock.await_count, MAX_FREESHIPPING_ATTEMPTS - 1)

    def test_unknown_business_failure_fails_closed_without_retry(self):
        # 用错发货接口（如把普通单当拼单）的典型返回：未知失败、失败关闭，交上层自愈
        result, session, sleep_mock = self._call(
            [_ret_response("FAIL_BIZ_NOT_GROUPON::非拼团订单")]
        )
        self.assertNotIn("success", result)
        self.assertEqual(result.get("category"), "unknown_failure")
        self.assertEqual(session.post_calls, 1)
        sleep_mock.assert_not_awaited()

    def test_missing_ret_fails_closed_without_retry(self):
        result, session, sleep_mock = self._call([_FakeResponse({})])
        self.assertNotIn("success", result)
        self.assertEqual(result.get("category"), "unknown_failure")
        self.assertEqual(session.post_calls, 1)
        sleep_mock.assert_not_awaited()

    def test_network_exception_retries_with_backoff_and_cap(self):
        result, session, sleep_mock = self._call([OSError("boom")])
        self.assertNotIn("success", result)
        self.assertEqual(result.get("category"), "network_error")
        self.assertIn("网络异常", result.get("error", ""))
        self.assertEqual(session.post_calls, MAX_FREESHIPPING_ATTEMPTS)
        self.assertEqual(sleep_mock.await_count, MAX_FREESHIPPING_ATTEMPTS - 1)
        delays = [call.args[0] for call in sleep_mock.await_args_list]
        self.assertEqual(delays, [5.0, 10.0, 20.0])

    def test_retry_count_offset_is_respected(self):
        result, session, sleep_mock = self._call(
            [_ret_response("SUCCESS::调用成功")],
            retry_count=MAX_FREESHIPPING_ATTEMPTS,
        )
        self.assertNotIn("success", result)
        self.assertIs(result.get("retry_exhausted"), True)
        self.assertIn("重试次数过多", result.get("error", ""))
        self.assertEqual(session.post_calls, 0)
        sleep_mock.assert_not_awaited()

    def test_missing_params_fail_closed_without_network(self):
        for order_id, item_id, buyer_id in (
            ("", "100", "200"),
            ("order-1", "", "200"),
            ("order-1", "100", ""),
        ):
            with self.subTest(order_id=order_id, item_id=item_id, buyer_id=buyer_id):
                result, session, sleep_mock = self._call(
                    [_ret_response("SUCCESS::调用成功")],
                    order_id=order_id,
                    item_id=item_id,
                    buyer_id=buyer_id,
                )
                self.assertNotIn("success", result)
                self.assertEqual(result.get("category"), "invalid_request")
                self.assertEqual(session.post_calls, 0)
                sleep_mock.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
