"""订单详情真实响应身份字段解析回归。"""

import unittest

from utils.order_fetcher_optimized import OrderFetcherOptimized


class OrderFetcherIdentityTests(unittest.TestCase):
    def setUp(self):
        self.fetcher = OrderFetcherOptimized("acct", "unb=1", use_pool=False)

    def test_nested_order_info_extracts_verified_item_and_buyer_identity(self):
        parsed = self.fetcher._parse_api_response({
            "status": 2,
            "utArgs": {"orderStatusName": "待发货"},
            "components": [{
                "render": "orderInfoVO",
                "data": {
                    "itemInfo": {
                        "itemId": "item-1",
                        "title": "详情商品标题",
                        "picUrl": "https://img.alicdn.com/detail.jpg",
                    },
                    "buyerInfo": {
                        "userId": "buyer-1",
                        "nick": "详情买家",
                        "avatar": "https://img.alicdn.com/avatar.jpg",
                    },
                },
            }],
        })
        self.assertEqual(parsed["item_image"], "https://img.alicdn.com/detail.jpg")
        self.assertEqual(parsed["buyer_nickname"], "详情买家")
        self.assertEqual(parsed["buyer_avatar_url"], "https://img.alicdn.com/avatar.jpg")

    def test_unverified_identity_fields_remain_empty(self):
        parsed = self.fetcher._parse_api_response({
            "status": 2,
            "components": [{"render": "orderInfoVO", "data": {"itemInfo": {}}}],
        })
        self.assertEqual(parsed.get("item_image", ""), "")
        self.assertEqual(parsed.get("buyer_nickname", ""), "")
        self.assertEqual(parsed.get("buyer_avatar_url", ""), "")

    def test_nested_candidate_objects_are_followed_without_inference(self):
        parsed = self.fetcher._parse_api_response({
            "status": 2,
            "components": [{
                "render": "orderInfoVO",
                "data": {
                    "itemInfo": {
                        "itemPic": {"url": "https://img.alicdn.com/nested.jpg"},
                    },
                    "buyerInfo": {
                        "userBaseInfo": {
                            "nickName": "嵌套买家",
                            "avatarUrl": "https://img.alicdn.com/nested-avatar.jpg",
                        },
                    },
                },
            }],
        })
        self.assertEqual(parsed["item_image"], "https://img.alicdn.com/nested.jpg")
        self.assertEqual(parsed["buyer_nickname"], "嵌套买家")
        self.assertEqual(
            parsed["buyer_avatar_url"],
            "https://img.alicdn.com/nested-avatar.jpg",
        )


if __name__ == "__main__":
    unittest.main()
