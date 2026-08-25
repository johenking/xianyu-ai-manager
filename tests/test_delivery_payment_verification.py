"""发货前付款核验的平台压力护栏测试。"""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import order_sync_service
from order_sync_service import XianyuOrderListClient
from XianyuAutoAsync import DELIVERY_VERIFY_MAX_PAGES, XianyuLive


def _detail_payload(*, buyer_id="BUYER-1", business_type="ordinary"):
    ut_args = {"orderStatusName": "买家已付款，请尽快发货"}
    component = {
        "data": {
            "orderStatusInfo": {"title": "买家已付款，请尽快发货"},
            "itemInfo": {"buyAmount": "1", "title": "Fixture item"},
            "priceInfo": {"amount": {"value": "3.00"}},
        },
    }
    if business_type == "ordinary":
        ut_args["idleBizCode"] = "6"
    elif business_type == "lead":
        ut_args.update({
            "xGlobalBizCode": "commer|leadReservation|onlineService",
            "globalBizCode": "autotrade",
            "idleBizCode": "7000",
        })
        component["render"] = "leadReservationPhoneInfoVO"
        component["data"]["leadId"] = "lead-fixture"
        component["data"]["priceInfo"]["amount"]["value"] = "0.00"
    return {
        "ret": ["SUCCESS::调用成功"],
        "data": {
            "orderId": "123456789012345678",
            "itemId": "ITEM-1",
            "peerUserId": buyer_id,
            "status": "2",
            "utArgs": ut_args,
            "commonInfo": {"createTime": "2026-08-15 15:22:11"},
            "components": [component],
        },
    }


class DeliveryVerificationScanBoundTests(unittest.IsolatedAsyncioTestCase):
    def _make_live(self):
        live = object.__new__(XianyuLive)
        live.cookie_id = "account-test"
        live.cookies_str = "unb=account-test; _m_h5_tk=token_value"
        live.browser_user_agent = "Mozilla/5.0 Synthetic Chrome/150.0.0.0"
        live._safe_str = lambda value: str(value)
        return live

    async def test_verification_bounds_pages_to_protect_the_account_lock(self):
        captured = {}

        class RecordingClient:
            def __init__(self, **kwargs):
                captured.update(kwargs)
                self.discover = AsyncMock(
                    return_value={
                        "success": True,
                        "orders": [
                            {
                                "order_id": "ORDER-1",
                                "item_id": "ITEM-1",
                                "buyer_id": "BUYER-1",
                                "order_status": "pending_ship",
                                "order_business_type": "ordinary",
                                "platform_status_text": "等待卖家发货",
                            }
                        ],
                    }
                )

        detail_fetch = AsyncMock(side_effect=AssertionError("unexpected detail fallback"))
        live = self._make_live()
        with patch.object(
            order_sync_service, "XianyuOrderListClient", RecordingClient
        ), patch.object(order_sync_service, "fetch_xianyu_order_detail", detail_fetch):
            result = await live._verify_paid_order_for_delivery(
                order_id="ORDER-1",
                item_id="ITEM-1",
                buyer_id="BUYER-1",
            )

        self.assertTrue(result["allowed"])
        self.assertEqual(captured.get("max_pages"), DELIVERY_VERIFY_MAX_PAGES)
        # 上限必须明显小于全量同步的二十页，否则锁占用回到原状
        self.assertLessEqual(DELIVERY_VERIFY_MAX_PAGES, 5)
        detail_fetch.assert_not_awaited()

    async def test_numeric_order_prefers_trusted_detail_without_waiting_for_list(self):
        class UnexpectedClient:
            def __init__(self, **_kwargs):
                self.discover = AsyncMock(
                    side_effect=AssertionError("unexpected order-list fallback")
                )

        detail_fetch = AsyncMock(return_value=_detail_payload())
        live = self._make_live()
        with patch.object(
            order_sync_service, "XianyuOrderListClient", UnexpectedClient
        ), patch.object(order_sync_service, "fetch_xianyu_order_detail", detail_fetch):
            result = await live._verify_paid_order_for_delivery(
                order_id="123456789012345678",
                item_id="ITEM-1",
                buyer_id="BUYER-1",
            )

        self.assertTrue(result["allowed"])
        self.assertEqual(result["business_type"], "ordinary")
        detail_fetch.assert_awaited_once()

    async def test_lead_and_unknown_orders_fail_closed_without_list_fallback(self):
        class UnexpectedClient:
            def __init__(self, **_kwargs):
                self.discover = AsyncMock(
                    side_effect=AssertionError("unexpected order-list fallback")
                )

        live = self._make_live()
        detail_fetch = AsyncMock()
        with patch.object(
            order_sync_service, "XianyuOrderListClient", UnexpectedClient
        ), patch.object(order_sync_service, "fetch_xianyu_order_detail", detail_fetch):
            for business_type, error_code in (
                ("lead", "lead_order_not_fulfillable"),
                ("unknown", "order_business_type_unconfirmed"),
            ):
                with self.subTest(business_type=business_type):
                    detail_fetch.return_value = _detail_payload(
                        business_type=business_type
                    )
                    result = await live._verify_paid_order_for_delivery(
                        order_id="123456789012345678",
                        item_id="ITEM-1",
                        buyer_id="BUYER-1",
                    )
                    self.assertFalse(result["allowed"])
                    self.assertEqual(result["business_type"], business_type)
                    self.assertEqual(result["error_code"], error_code)

    async def test_verification_still_fails_closed_when_order_is_absent(self):
        class EmptyClient:
            def __init__(self, **_kwargs):
                self.discover = AsyncMock(return_value={"success": True, "orders": []})

        live = self._make_live()
        with patch.object(order_sync_service, "XianyuOrderListClient", EmptyClient):
            result = await live._verify_paid_order_for_delivery(
                order_id="ORDER-MISSING",
                item_id="ITEM-1",
                buyer_id="BUYER-1",
            )

        self.assertFalse(result["allowed"])
        self.assertEqual(result["error_code"], "order_not_observed")

    async def test_trusted_detail_requires_matching_identity_and_list_fallback_fails_closed(self):
        class FailedClient:
            error_code = "platform_permission_denied"

            def __init__(self, **_kwargs):
                self.discover = AsyncMock(return_value={
                    "success": False,
                    "error_code": self.error_code,
                    "error": "fixture failure",
                    "requires_login": False,
                })

        detail_fetch = AsyncMock(return_value=_detail_payload())
        live = self._make_live()
        with patch.object(
            order_sync_service, "XianyuOrderListClient", FailedClient
        ), patch.object(order_sync_service, "fetch_xianyu_order_detail", detail_fetch):
            result = await live._verify_paid_order_for_delivery(
                order_id="123456789012345678",
                item_id="ITEM-1",
                buyer_id="BUYER-1",
            )
            self.assertTrue(result["allowed"])
            self.assertEqual(result["quantity"], 1)

            detail_fetch.return_value = _detail_payload(buyer_id="OTHER-BUYER")
            result = await live._verify_paid_order_for_delivery(
                order_id="123456789012345678",
                item_id="ITEM-1",
                buyer_id="BUYER-1",
            )
            self.assertFalse(result["allowed"])

            FailedClient.error_code = "platform_error"
            detail_fetch.return_value = {"ret": ["FAIL::fixture"]}
            result = await live._verify_paid_order_for_delivery(
                order_id="123456789012345678",
                item_id="ITEM-1",
                buyer_id="BUYER-1",
            )
            self.assertFalse(result["allowed"])


def _order_page(pages, order_prefix):
    """构造一页真实结构的订单，且第一页声明还有下一页，好让翻页真正发生。"""
    pages["n"] += 1
    index = pages["n"]
    return {
        "ret": ["SUCCESS::调用成功"],
        "data": {
            "module": {
                "items": [
                    {
                        "commonData": {
                            "orderId": f"{order_prefix}-{index}",
                            "itemId": f"item-{index}",
                            "orderStatus": "等待卖家发货",
                            "createTime": "2026-08-12 10:00:00",
                        },
                        "buyerInfoVO": {"buyerId": f"buyer-{index}"},
                        "priceVO": {"totalPrice": "¥10.00", "buyNum": "1"},
                    }
                ],
                "nextPage": "true" if index == 1 else "false",
            }
        },
    }


class PageIntervalJitterTests(unittest.IsolatedAsyncioTestCase):
    async def test_inter_page_delay_carries_jitter(self):
        pages = {"n": 0}

        async def page_loader(**_kwargs):
            return _order_page(pages, "order")

        sleep = AsyncMock()
        result = await XianyuOrderListClient(
            page_loader=page_loader,
            request_interval=0.8,
            jitter_fn=lambda _start, _end: 0.25,
            sleep_fn=sleep,
            max_pages=3,
        ).discover(
            cookie_id="account-jitter",
            cookie_string="unb=account-jitter; _m_h5_tk=token_value",
        )

        self.assertTrue(result["success"])
        self.assertEqual(pages["n"], 2)
        # 翻到第二页前的等待必须是基础间隔加抖动，而不是固定节拍
        sleep.assert_awaited_once_with(0.8 + 0.25)

    async def test_jitter_never_shortens_below_base_interval(self):
        pages = {"n": 0}

        async def page_loader(**_kwargs):
            return _order_page(pages, "order-floor")

        sleep = AsyncMock()
        await XianyuOrderListClient(
            page_loader=page_loader,
            request_interval=0.8,
            # 抖动函数返回负值时也不能把间隔压到基础值以下
            jitter_fn=lambda _start, _end: -5.0,
            sleep_fn=sleep,
            max_pages=3,
        ).discover(
            cookie_id="account-jitter-floor",
            cookie_string="unb=account-jitter-floor; _m_h5_tk=token_value",
        )

        self.assertEqual(sleep.await_args.args[0], 0.8)


if __name__ == "__main__":
    unittest.main()
