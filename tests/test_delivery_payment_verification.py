"""发货前付款核验的平台压力护栏测试。

该核验在账号订单同步锁内运行，扫描页数直接决定锁占用时长与对平台的请求量。
这里锁定两件事：核验路径不再按二十页扫描，以及翻页间隔带抖动。
"""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import order_sync_service
from order_sync_service import XianyuOrderListClient
from XianyuAutoAsync import DELIVERY_VERIFY_MAX_PAGES, XianyuLive


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
                                "platform_status_text": "等待卖家发货",
                            }
                        ],
                    }
                )

        live = self._make_live()
        with patch.object(order_sync_service, "XianyuOrderListClient", RecordingClient):
            result = await live._verify_paid_order_for_delivery(
                order_id="ORDER-1",
                item_id="ITEM-1",
                buyer_id="BUYER-1",
            )

        self.assertTrue(result["allowed"])
        self.assertEqual(captured.get("max_pages"), DELIVERY_VERIFY_MAX_PAGES)
        # 上限必须明显小于全量同步的二十页，否则锁占用回到原状
        self.assertLessEqual(DELIVERY_VERIFY_MAX_PAGES, 5)

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
