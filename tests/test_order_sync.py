import os
import tempfile
import time
import unittest

from db_manager import DBManager
from utils.order_fetcher_optimized import OrderFetcherOptimized
from order_sync_service import (
    OrderSyncCoordinator,
    XianyuOrderListClient,
    choose_order_status,
    classify_platform_error,
    extract_order_list,
    normalize_order_status,
    normalize_order_record,
    parse_order_api_payload,
)
from utils.browser_pool import cookie_fingerprint
from order_status_handler import extract_order_event_identity


class OrderStatusNormalizationTests(unittest.TestCase):
    def test_status_text_takes_priority_over_numeric_code(self):
        self.assertEqual(normalize_order_status(3, "买家已签收，交易成功"), "completed")
        self.assertEqual(normalize_order_status(6, "退款成功，钱款已原路退返"), "refunded")
        self.assertEqual(normalize_order_status(10, "买家撤销退款申请"), "refund_cancelled")

    def test_numeric_and_english_statuses_are_normalized(self):
        self.assertEqual(normalize_order_status(2, ""), "pending_ship")
        self.assertEqual(normalize_order_status("8", ""), "refunded")
        self.assertEqual(normalize_order_status("WAIT_BUYER_CONFIRM_GOODS", ""), "shipped")
        self.assertEqual(normalize_order_status("TRADE_FINISHED", ""), "completed")

    def test_unknown_never_replaces_a_reliable_status(self):
        self.assertEqual(choose_order_status("shipped", "unknown"), "shipped")
        self.assertEqual(choose_order_status("completed", "refunding"), "refunding")
        self.assertEqual(choose_order_status("completed", "refunded"), "refunded")

    def test_session_expired_is_a_blocking_platform_error(self):
        result = classify_platform_error(["FAIL_SYS_SESSION_EXPIRED::Session过期"])
        self.assertEqual(result["code"], "session_expired")
        self.assertTrue(result["requires_login"])

    def test_api_payload_preserves_session_failure_instead_of_returning_unknown(self):
        result = parse_order_api_payload({"ret": ["FAIL_SYS_SESSION_EXPIRED::Session过期"]})

        self.assertFalse(result["success"])
        self.assertEqual(result["error_code"], "session_expired")
        self.assertTrue(result["requires_login"])

    def test_recent_order_list_payload_is_extracted_and_normalized(self):
        payload = {
            "ret": ["SUCCESS::调用成功"],
            "data": {
                "orderList": [
                    {
                        "bizOrderId": "order-1",
                        "itemId": "item-1",
                        "buyerId": "buyer-1",
                        "title": "测试商品",
                        "payAmount": "29.9",
                        "statusText": "买家已签收，交易成功",
                        "createTime": "2026-07-01 10:00:00",
                    }
                ]
            },
        }

        rows = extract_order_list(payload)
        order = normalize_order_record(rows[0], "account-1")

        self.assertEqual(order["order_id"], "order-1")
        self.assertEqual(order["order_status"], "completed")
        self.assertEqual(order["cookie_id"], "account-1")

    def test_merchant_sold_payload_is_extracted_and_normalized(self):
        payload = {
            "ret": ["SUCCESS::调用成功"],
            "data": {
                "module": {
                    "nextPage": "false",
                    "items": [{
                        "commonData": {
                            "orderId": "order-merchant",
                            "itemId": "item-merchant",
                            "orderStatus": "交易成功",
                            "createTime": "2026-07-02 12:00:00",
                        },
                        "buyerInfoVO": {"buyerId": "buyer-merchant"},
                        "priceVO": {"totalPrice": "¥35.00"},
                    }],
                }
            },
        }

        order = normalize_order_record(extract_order_list(payload)[0], "account-1")

        self.assertEqual(order["order_id"], "order-merchant")
        self.assertEqual(order["item_id"], "item-merchant")
        self.assertEqual(order["buyer_id"], "buyer-merchant")
        self.assertEqual(order["order_status"], "completed")
        self.assertEqual(order["amount"], "35.00")

    def test_detail_fetcher_uses_shared_refund_mapping(self):
        fetcher = OrderFetcherOptimized("account-1", "unb=account-1")

        result = fetcher._parse_api_response({
            "status": 8,
            "utArgs": {"orderStatusName": "退款成功，钱款已原路退返"},
            "components": [],
        })

        self.assertEqual(result["order_status"], "refunded")

    def test_cookie_fingerprint_changes_when_login_cookie_changes(self):
        first = cookie_fingerprint("unb=account-1; cookie2=old")
        same = cookie_fingerprint("unb=account-1; cookie2=old")
        updated = cookie_fingerprint("unb=account-1; cookie2=new")

        self.assertEqual(first, same)
        self.assertNotEqual(first, updated)

    def test_status_event_identity_is_extracted_without_fifo_guessing(self):
        identity = extract_order_event_identity({
            "reminderUrl": "fleamarket://message_chat?itemId=123456789&peerUserId=987654321&chatId=chat-12345",
            "targetUrl": "https://www.goofish.com/order_detail?id=123456789012345678",
        })

        self.assertEqual(identity["order_id"], "123456789012345678")
        self.assertEqual(identity["item_id"], "123456789")
        self.assertEqual(identity["buyer_id"], "987654321")
        self.assertEqual(identity["chat_id"], "chat-12345")


class OrderStatusPersistenceTests(unittest.TestCase):
    def setUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.db = DBManager(self.db_path)
        with self.db.lock:
            self.db.conn.execute(
                "INSERT INTO cookies (id, value, user_id) VALUES (?, ?, ?)",
                ("account-1", "unb=account-1; cookie2=value", 1),
            )
            self.db.conn.commit()

    def tearDown(self):
        self.db.conn.close()
        os.unlink(self.db_path)

    def test_order_schema_contains_sync_metadata_and_event_table(self):
        with self.db.lock:
            columns = {
                row[1]
                for row in self.db.conn.execute("PRAGMA table_info(orders)").fetchall()
            }
            event_table = self.db.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='order_status_events'"
            ).fetchone()

        self.assertTrue(
            {
                "platform_status_code",
                "platform_status_text",
                "status_source",
                "status_synced_at",
                "last_sync_error",
            }.issubset(columns)
        )
        self.assertIsNotNone(event_table)

    def test_unmatched_refund_event_is_persisted_and_never_fifo_matches_another_order(self):
        self.db.insert_or_update_order(
            order_id="order-a",
            item_id="item-a",
            buyer_id="buyer-a",
            order_status="completed",
            cookie_id="account-1",
        )
        self.db.insert_or_update_order(
            order_id="order-b",
            item_id="item-b",
            buyer_id="buyer-b",
            order_status="completed",
            cookie_id="account-1",
        )
        event_id = self.db.record_order_status_event(
            cookie_id="account-1",
            normalized_status="refunded",
            raw_status="退款成功，钱款已原路退返",
            item_id="item-a",
            buyer_id="buyer-a",
            occurred_at=time.time(),
        )

        wrong_matches = self.db.reconcile_order_status_events(
            cookie_id="account-1",
            order_id="order-b",
            item_id="item-b",
            buyer_id="buyer-b",
        )
        right_matches = self.db.reconcile_order_status_events(
            cookie_id="account-1",
            order_id="order-a",
            item_id="item-a",
            buyer_id="buyer-a",
        )

        self.assertEqual(wrong_matches, [])
        self.assertEqual([entry["id"] for entry in right_matches], [event_id])
        self.assertEqual(self.db.get_order_by_id("order-a")["order_status"], "refunded")
        self.assertEqual(self.db.get_order_by_id("order-b")["order_status"], "completed")

    def test_unknown_sync_result_records_error_without_overwriting_known_status(self):
        self.db.insert_or_update_order(
            order_id="order-known",
            order_status="shipped",
            cookie_id="account-1",
        )

        result = self.db.apply_order_sync_update(
            order_id="order-known",
            cookie_id="account-1",
            incoming_status="unknown",
            platform_status_code="",
            platform_status_text="",
            status_source="order_detail",
            sync_error="无法确认平台订单状态",
        )
        order = self.db.get_order_by_id("order-known")

        self.assertFalse(result["status_changed"])
        self.assertEqual(order["order_status"], "shipped")
        self.assertEqual(order["last_sync_error"], "无法确认平台订单状态")

    def test_completed_order_can_move_to_refunded(self):
        self.db.insert_or_update_order(
            order_id="order-refund",
            order_status="completed",
            cookie_id="account-1",
        )

        result = self.db.apply_order_sync_update(
            order_id="order-refund",
            cookie_id="account-1",
            incoming_status="refunded",
            platform_status_code="8",
            platform_status_text="退款成功，钱款已原路退返",
            status_source="order_detail",
        )
        order = self.db.get_order_by_id("order-refund")

        self.assertTrue(result["status_changed"])
        self.assertEqual(order["order_status"], "refunded")
        self.assertEqual(order["platform_status_code"], "8")
        self.assertEqual(order["status_source"], "order_detail")


class OrderSyncCoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.db = DBManager(self.db_path)
        with self.db.lock:
            self.db.conn.execute(
                "INSERT INTO cookies (id, value, user_id) VALUES (?, ?, ?)",
                ("account-1", "unb=account-1; cookie2=value", 1),
            )
            self.db.conn.commit()

    async def asyncTearDown(self):
        self.db.conn.close()
        os.unlink(self.db_path)

    async def test_session_expired_stops_sync_without_changing_orders(self):
        self.db.insert_or_update_order(
            order_id="order-1",
            order_status="shipped",
            cookie_id="account-1",
        )

        async def discoverer(**_kwargs):
            return {
                "success": False,
                "error_code": "session_expired",
                "error": "登录状态已过期",
                "requires_login": True,
            }

        coordinator = OrderSyncCoordinator(self.db, discoverer=discoverer)
        result = await coordinator.sync_account(
            cookie_id="account-1",
            cookie_string="unb=account-1; cookie2=value",
            days=90,
        )

        self.assertFalse(result["success"])
        self.assertTrue(result["requires_login"])
        self.assertEqual(result["summary"]["status_updated"], 0)
        self.assertEqual(self.db.get_order_by_id("order-1")["order_status"], "shipped")

    async def test_discovery_inserts_missing_order_and_updates_existing_status(self):
        self.db.insert_or_update_order(
            order_id="order-existing",
            item_id="item-existing",
            buyer_id="buyer-existing",
            order_status="shipped",
            cookie_id="account-1",
        )

        async def discoverer(**_kwargs):
            return {
                "success": True,
                "orders": [
                    {
                        "order_id": "order-existing",
                        "item_id": "item-existing",
                        "buyer_id": "buyer-existing",
                        "order_status": "completed",
                        "platform_status_code": "4",
                        "platform_status_text": "买家已签收，交易成功",
                        "cookie_id": "account-1",
                    },
                    {
                        "order_id": "order-new",
                        "item_id": "item-new",
                        "buyer_id": "buyer-new",
                        "amount": "19.9",
                        "quantity": "1",
                        "order_status": "refunded",
                        "platform_status_code": "8",
                        "platform_status_text": "退款成功，钱款已原路退返",
                        "cookie_id": "account-1",
                    },
                ],
            }

        coordinator = OrderSyncCoordinator(self.db, discoverer=discoverer)
        result = await coordinator.sync_account(
            cookie_id="account-1",
            cookie_string="unb=account-1; cookie2=value",
            days=90,
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["summary"]["discovered"], 1)
        self.assertEqual(result["summary"]["status_updated"], 1)
        self.assertEqual(self.db.get_order_by_id("order-existing")["order_status"], "completed")
        self.assertEqual(self.db.get_order_by_id("order-new")["order_status"], "refunded")

    async def test_recent_order_client_paginates_and_stops_at_date_cutoff(self):
        requested_pages = []

        async def page_loader(**kwargs):
            requested_pages.append(kwargs["page_number"])
            if kwargs["page_number"] == 1:
                return {
                    "ret": ["SUCCESS::调用成功"],
                    "data": {
                        "orderList": [
                            {
                                "bizOrderId": "recent-order",
                                "statusText": "交易成功",
                                "createTime": "2026-07-01 10:00:00",
                            }
                        ]
                    },
                }
            return {
                "ret": ["SUCCESS::调用成功"],
                "data": {
                    "orderList": [
                        {
                            "bizOrderId": "old-order",
                            "statusText": "交易成功",
                            "createTime": "2026-01-01 10:00:00",
                        }
                    ]
                },
            }

        client = XianyuOrderListClient(
            page_loader=page_loader,
            now_fn=lambda: 1783180800.0,
            page_size=1,
        )
        result = await client.discover(
            cookie_id="account-1",
            cookie_string="unb=account-1; _m_h5_tk=token_value",
            days=90,
        )

        self.assertTrue(result["success"])
        self.assertEqual([row["order_id"] for row in result["orders"]], ["recent-order"])
        self.assertEqual(requested_pages, [1, 2])

    async def test_recent_order_client_surfaces_session_expiry(self):
        async def page_loader(**_kwargs):
            return {"ret": ["FAIL_SYS_SESSION_EXPIRED::Session过期"]}

        client = XianyuOrderListClient(page_loader=page_loader)
        result = await client.discover(
            cookie_id="account-1",
            cookie_string="unb=account-1; _m_h5_tk=expired_value",
            days=90,
        )

        self.assertFalse(result["success"])
        self.assertTrue(result["requires_login"])
        self.assertEqual(result["error_code"], "session_expired")

    async def test_detail_recheck_advances_shipped_and_completed_orders(self):
        self.db.insert_or_update_order(
            order_id="order-shipped",
            order_status="shipped",
            cookie_id="account-1",
            created_at="2026-07-01 10:00:00",
        )
        self.db.insert_or_update_order(
            order_id="order-completed",
            order_status="completed",
            cookie_id="account-1",
            created_at="2026-07-01 10:00:00",
        )
        self.db.insert_or_update_order(
            order_id="order-refunded",
            order_status="refunded",
            cookie_id="account-1",
            created_at="2026-07-01 10:00:00",
        )
        self.db.insert_or_update_order(
            order_id="order-legacy-closed",
            order_status="cancelled",
            cookie_id="account-1",
            created_at="2026-07-01 10:00:00",
        )
        requested_order_ids = []

        async def discoverer(**_kwargs):
            return {"success": True, "orders": []}

        async def detail_fetcher(**kwargs):
            requested_order_ids.extend(kwargs["order_ids"])
            return [
                {
                    "order_id": "order-shipped",
                    "order_status": "completed",
                    "status_text": "买家已签收，交易成功",
                },
                {
                    "order_id": "order-completed",
                    "order_status": "refunded",
                    "status_text": "退款成功，钱款已原路退返",
                },
                {
                    "order_id": "order-legacy-closed",
                    "order_status": "refunded",
                    "status_text": "退款成功，钱款已原路退返",
                },
            ]

        coordinator = OrderSyncCoordinator(
            self.db,
            discoverer=discoverer,
            detail_fetcher=detail_fetcher,
            now_fn=lambda: 1783180800.0,
        )
        result = await coordinator.sync_account(
            cookie_id="account-1",
            cookie_string="unb=account-1; cookie2=value",
            days=90,
        )

        self.assertCountEqual(requested_order_ids, ["order-shipped", "order-completed", "order-legacy-closed"])
        self.assertEqual(result["summary"]["status_updated"], 3)
        self.assertEqual(self.db.get_order_by_id("order-shipped")["order_status"], "completed")
        self.assertEqual(self.db.get_order_by_id("order-completed")["order_status"], "refunded")
        self.assertEqual(self.db.get_order_by_id("order-legacy-closed")["order_status"], "refunded")

    async def test_detail_session_expiry_is_reported_without_overwriting_status(self):
        self.db.insert_or_update_order(
            order_id="order-shipped",
            order_status="shipped",
            cookie_id="account-1",
            created_at="2026-07-01 10:00:00",
        )

        async def discoverer(**_kwargs):
            return {"success": True, "orders": []}

        async def detail_fetcher(**_kwargs):
            return [{
                "order_id": "order-shipped",
                "error": "闲鱼登录状态已过期",
                "error_code": "session_expired",
                "requires_login": True,
            }]

        coordinator = OrderSyncCoordinator(
            self.db,
            discoverer=discoverer,
            detail_fetcher=detail_fetcher,
            now_fn=lambda: 1783180800.0,
        )
        result = await coordinator.sync_account(
            cookie_id="account-1",
            cookie_string="unb=account-1; cookie2=value",
            days=90,
        )

        self.assertFalse(result["success"])
        self.assertTrue(result["requires_login"])
        self.assertEqual(self.db.get_order_by_id("order-shipped")["order_status"], "shipped")


if __name__ == "__main__":
    unittest.main()
