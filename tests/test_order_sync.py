import asyncio
import json
import os
import tempfile
import time
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import order_sync_service
from db_manager import DBManager
from order_sync_service import (
    _parse_order_timestamp,
    OrderSyncCoordinator,
    XianyuOrderListClient,
    choose_order_status,
    classify_platform_error,
    classify_order_business_type,
    extract_order_list,
    fetch_xianyu_pending_order_page,
    normalize_order_status,
    normalize_order_record,
    normalize_pending_order_record,
    parse_amount_fen,
    parse_order_detail_payload,
    parse_order_api_payload,
    parse_pending_order_api_payload,
    fetch_xianyu_order_list_page,
    session_refresh_blocks_order_requests,
)
from utils.browser_pool import cookie_fingerprint
from order_status_handler import extract_order_event_identity


class OrderStatusNormalizationTests(unittest.TestCase):
    def test_status_text_takes_priority_over_numeric_code(self):
        self.assertEqual(normalize_order_status(3, "买家已签收，交易成功"), "completed")
        self.assertEqual(normalize_order_status(6, "退款成功，钱款已原路退返"), "refunded")
        self.assertEqual(normalize_order_status(10, "买家撤销退款申请"), "refund_cancelled")

    def test_waiting_for_buyer_confirmation_is_not_completed(self):
        self.assertEqual(
            normalize_order_status("", "待买家确认收货"),
            "shipped",
        )

    def test_numeric_and_english_statuses_are_normalized(self):
        self.assertEqual(normalize_order_status(2, ""), "pending_ship")
        self.assertEqual(normalize_order_status("8", ""), "refunded")
        self.assertEqual(normalize_order_status("WAIT_BUYER_CONFIRM_GOODS", ""), "shipped")
        self.assertEqual(normalize_order_status("TRADE_FINISHED", ""), "completed")

    def test_unknown_never_replaces_a_reliable_status(self):
        self.assertEqual(choose_order_status("shipped", "unknown"), "shipped")
        self.assertEqual(choose_order_status("completed", "refunding"), "refunding")
        self.assertEqual(choose_order_status("completed", "refunded"), "refunded")

    def test_fulfillment_stages_never_regress(self):
        # 迟到的「我已付款」等前置阶段回波不得把已发货/已完成刷回待发货
        self.assertEqual(choose_order_status("shipped", "pending_ship"), "shipped")
        self.assertEqual(choose_order_status("shipped", "processing"), "shipped")
        self.assertEqual(choose_order_status("completed", "pending_ship"), "completed")
        self.assertEqual(choose_order_status("completed", "shipped"), "completed")
        self.assertEqual(choose_order_status("pending_ship", "processing"), "pending_ship")

    def test_forward_progress_and_refund_family_still_apply(self):
        self.assertEqual(choose_order_status("pending_ship", "shipped"), "shipped")
        self.assertEqual(choose_order_status("shipped", "completed"), "completed")
        self.assertEqual(choose_order_status("shipped", "refunding"), "refunding")
        self.assertEqual(choose_order_status("shipped", "cancelled"), "cancelled")
        self.assertEqual(choose_order_status("pending_ship", "refunded"), "refunded")
        # 退款撤销/驳回后回到履约态仍放行（refunding 不在推进链内）
        self.assertEqual(choose_order_status("refunding", "shipped"), "shipped")

    def test_session_expired_is_a_blocking_platform_error(self):
        result = classify_platform_error(["FAIL_SYS_SESSION_EXPIRED::Session过期"])
        self.assertEqual(result["code"], "session_expired")
        self.assertTrue(result["requires_login"])

    def test_platform_token_error_variants_require_login(self):
        for value in (
            "FAIL_SYS_TOKEN_EXPIRED::令牌过期",
            "FAIL_SYS_TOKEN_EXOIRED::Token expired",
            "FAIL_SYS_USER_VALIDATE::mini_login",
        ):
            with self.subTest(value=value):
                result = classify_platform_error([value])
                self.assertEqual(result["code"], "session_expired")
                self.assertTrue(result["requires_login"])

    def test_platform_permission_exception_is_not_misclassified_as_login_expiry(self):
        result = classify_platform_error([
            "PERMISSION_EXCEPTION::seller order permission denied"
        ])

        self.assertEqual(result["code"], "platform_permission_denied")
        self.assertFalse(result["requires_login"])

    def test_api_payload_preserves_session_failure_instead_of_returning_unknown(self):
        result = parse_order_api_payload({"ret": ["FAIL_SYS_SESSION_EXPIRED::Session过期"]})

        self.assertFalse(result["success"])
        self.assertEqual(result["error_code"], "session_expired")
        self.assertTrue(result["requires_login"])

    def test_success_payload_without_data_is_invalid_schema(self):
        result = parse_order_api_payload({"ret": ["SUCCESS::调用成功"]})

        self.assertFalse(result["success"])
        self.assertEqual(result["error_code"], "invalid_response_schema")

    def test_rate_limit_and_server_errors_are_retryable(self):
        rate_limited = classify_platform_error(["HTTP_429::订单接口请求失败"])
        unavailable = classify_platform_error(["HTTP_503::订单接口请求失败"])

        self.assertEqual(rate_limited["code"], "rate_limited")
        self.assertTrue(rate_limited["retryable"])
        self.assertEqual(unavailable["code"], "platform_unavailable")
        self.assertTrue(unavailable["retryable"])

    def test_order_request_gate_matches_listener_human_action_states(self):
        self.assertTrue(session_refresh_blocks_order_requests({
            "state": "manual_reauth_required",
        }))
        self.assertTrue(session_refresh_blocks_order_requests({
            "state": "verification_required",
        }))
        self.assertFalse(session_refresh_blocks_order_requests({
            "state": "action_required",
            "error_code": "connection_failures",
        }))

    def test_pending_order_payload_filters_status_and_requires_fulfillment_fields(self):
        result = parse_pending_order_api_payload({
            "ret": ["SUCCESS::调用成功"],
            "data": {
                "items": [
                    {
                        "bizOrderId": "order-pending",
                        "auctionId": "item-pending",
                        "buyerId": "buyer-pending",
                        "auctionTitle": "测试商品",
                        "totalFee": "12.50",
                        "buyAmount": 2,
                        "orderStatus": "2",
                        "idleBizCode": "6",
                    },
                    {
                        "bizOrderId": "order-shipped",
                        "auctionId": "item-shipped",
                        "orderStatus": "3",
                    },
                    {
                        "bizOrderId": "order-invalid",
                        "orderStatusMsg": "等待卖家发货",
                    },
                ],
            },
        }, "account-1")

        self.assertTrue(result["success"])
        self.assertEqual(result["invalid_records"], 1)
        self.assertEqual(len(result["orders"]), 1)
        order = result["orders"][0]
        self.assertEqual(order["order_id"], "order-pending")
        self.assertEqual(order["item_id"], "item-pending")
        self.assertEqual(order["buyer_id"], "buyer-pending")
        self.assertEqual(order["amount"], "12.50")
        self.assertEqual(order["quantity"], "2")
        self.assertEqual(order["order_status"], "pending_ship")
        self.assertEqual(order["order_business_type"], "ordinary")

    def test_pending_order_payload_rejects_an_all_invalid_pending_page(self):
        result = parse_pending_order_api_payload({
            "ret": ["SUCCESS::调用成功"],
            "data": {"items": [{"orderStatus": "2"}]},
        }, "account-1")

        self.assertFalse(result["success"])
        self.assertEqual(result["error_code"], "invalid_response_schema")

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
                        "priceVO": {"totalPrice": "¥35.00", "buyNum": "3"},
                        "rightVO": {
                            "btnList": [{"tradeAction": "LOGISTICS_SEND"}],
                        },
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
        self.assertEqual(order["quantity"], "3")
        self.assertEqual(order["order_business_type"], "ordinary")

    def test_order_business_type_uses_positive_markers_and_fails_closed(self):
        lead_detail = {
            "ret": ["SUCCESS::调用成功"],
            "data": {
                "orderId": "order-lead",
                "itemId": "item-lead",
                "peerUserId": "buyer-lead",
                "status": "2",
                "utArgs": {
                    "xGlobalBizCode": "commer|leadReservation|onlineService",
                    "globalBizCode": "autotrade",
                    "idleBizCode": "7000",
                    "orderStatusName": "买家已付款，请尽快发货",
                },
                "components": [{
                    "render": "leadReservationPhoneInfoVO",
                    "data": {
                        "leadId": "lead-fixture",
                        "orderStatusInfo": {"title": "买家已付款，请尽快发货"},
                        "itemInfo": {"buyAmount": "1", "title": "Fixture"},
                        "priceInfo": {"amount": {"value": "0.00"}},
                    },
                }],
            },
        }
        ordinary_detail = {
            "ret": ["SUCCESS::调用成功"],
            "data": {
                "orderId": "order-ordinary",
                "itemId": "item-ordinary",
                "peerUserId": "buyer-ordinary",
                "status": "2",
                "utArgs": {
                    "idleBizCode": "6",
                    "orderStatusName": "买家已付款，请尽快发货",
                },
                "components": [{
                    "data": {
                        "orderStatusInfo": {"title": "买家已付款，请尽快发货"},
                        "itemInfo": {"buyAmount": "1", "title": "Fixture"},
                        "priceInfo": {"amount": {"value": "3.00"}},
                    },
                }],
            },
        }
        unknown_detail = {
            "ret": ["SUCCESS::调用成功"],
            "data": {
                "orderId": "order-unknown",
                "itemId": "item-unknown",
                "peerUserId": "buyer-unknown",
                "status": "2",
                "utArgs": {"orderStatusName": "买家已付款，请尽快发货"},
                "components": [{
                    "data": {
                        "orderStatusInfo": {"title": "买家已付款，请尽快发货"},
                        "itemInfo": {"buyAmount": "1", "title": "Fixture"},
                        "priceInfo": {"amount": {"value": "0.00"}},
                    },
                }],
            },
        }

        for payload, expected in (
            (lead_detail, "lead"),
            (ordinary_detail, "ordinary"),
            (unknown_detail, "unknown"),
        ):
            with self.subTest(expected=expected):
                parsed = parse_order_detail_payload(payload, "account-1")
                self.assertTrue(parsed["success"])
                self.assertEqual(parsed["orders"][0]["order_business_type"], expected)

        self.assertEqual(
            classify_order_business_type({
                "commonData": {"orderStatus": "待发货"},
                "rightVO": {"btnList": [{
                    "tradeAction": "CLOSE_ORDER",
                    "name": "取消预约",
                }]},
            }),
            "lead",
        )
        self.assertEqual(
            classify_order_business_type({
                "commonData": {"orderStatus": "待发货"},
                "rightVO": {"btnList": [{"tradeAction": "LOGISTICS_SEND"}]},
            }),
            "ordinary",
        )
        self.assertEqual(
            normalize_pending_order_record({
                "bizOrderId": "order-lead-list",
                "auctionId": "item-lead",
                "buyerId": "buyer-lead",
                "totalFee": "0.00",
                "buyAmount": 1,
                "orderStatus": "2",
                "idleBizCode": "7000",
            }, "account-1")["order_business_type"],
            "lead",
        )

    def test_order_list_does_not_infer_unverified_buyer_identity_fields(self):
        order = normalize_order_record({
            "commonData": {"orderId": "order-private"},
            "buyerInfoVO": {
                "buyerId": "buyer-private",
                "nick": "unverified-nickname",
                "avatar": "https://example.test/unverified-avatar.jpg",
            },
        }, "account-1")

        self.assertEqual(order["buyer_id"], "buyer-private")
        self.assertEqual(order["buyer_nickname"], "")
        self.assertEqual(order["buyer_avatar_url"], "")

    def test_order_list_does_not_default_missing_quantity_to_one(self):
        order = normalize_order_record({
            "commonData": {"orderId": "order-no-quantity"},
        }, "account-1")

        self.assertEqual(order["quantity"], "")

    def test_zero_amount_is_preserved_as_a_known_value(self):
        order = normalize_order_record({
            "commonData": {"orderId": "order-zero-amount"},
            "payAmount": 0,
        }, "account-1")

        self.assertEqual(order["amount"], "0")
        self.assertEqual(parse_amount_fen(order["amount"]), 0)

    @unittest.skipUnless(hasattr(time, "tzset"), "requires time.tzset")
    def test_platform_clock_is_parsed_as_shanghai_time_on_any_host_timezone(self):
        original_tz = os.environ.get("TZ")
        try:
            os.environ["TZ"] = "UTC"
            time.tzset()
            expected = datetime(
                2026, 7, 1, 10, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai")
            ).timestamp()
            self.assertEqual(
                _parse_order_timestamp("2026-07-01 10:00:00"),
                expected,
            )
        finally:
            if original_tz is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = original_tz
            time.tzset()

    def test_merchant_refund_flag_overrides_non_refund_status(self):
        order = normalize_order_record({
            "commonData": {
                "orderId": "order-refund",
                "orderStatus": "已发货",
                "inRefund": "true",
            },
            "priceVO": {"buyNum": 2},
        }, "account-1")

        self.assertEqual(order["order_status"], "refunding")
        self.assertEqual(order["quantity"], "2")

    def test_trusted_order_quantity_accepts_only_bounded_positive_integers(self):
        parser = getattr(order_sync_service, "parse_trusted_order_quantity", None)
        self.assertTrue(callable(parser))
        self.assertEqual(parser(1), 1)
        self.assertEqual(parser("3"), 3)
        for value in (None, "", True, 0, -1, "2.5", "lots", 101):
            with self.subTest(value=value):
                self.assertIsNone(parser(value))

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

    def test_stale_payment_event_does_not_regress_shipped_order(self):
        self.db.insert_or_update_order(
            order_id="order-shipped",
            item_id="item-s",
            buyer_id="buyer-s",
            order_status="shipped",
            cookie_id="account-1",
            system_shipped=True,
        )
        event_id = self.db.record_order_status_event(
            cookie_id="account-1",
            normalized_status="pending_ship",
            raw_status="我已付款，等待卖家发货",
            order_id="order-shipped",
            occurred_at=time.time(),
        )

        matches = self.db.reconcile_order_status_events(
            cookie_id="account-1",
            order_id="order-shipped",
            item_id="item-s",
            buyer_id="buyer-s",
        )

        # 事件仍要被消费掉（避免反复重放），但状态不得回退
        self.assertEqual([entry["id"] for entry in matches], [event_id])
        order = self.db.get_order_by_id("order-shipped")
        self.assertEqual(order["order_status"], "shipped")
        self.assertTrue(order["system_shipped"])

    def test_sync_echo_does_not_regress_shipped_order(self):
        self.db.insert_or_update_order(
            order_id="order-echo",
            order_status="shipped",
            cookie_id="account-1",
            system_shipped=True,
        )

        result = self.db.apply_order_sync_update(
            order_id="order-echo",
            cookie_id="account-1",
            incoming_status="pending_ship",
            platform_status_code="2",
            platform_status_text="等待卖家发货",
            status_source="order_list",
        )
        order = self.db.get_order_by_id("order-echo")

        self.assertFalse(result["status_changed"])
        self.assertEqual(order["order_status"], "shipped")

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

        self.assertTrue(result["success"])
        self.assertTrue(result["skipped"])
        self.assertFalse(result["requires_login"])
        self.assertEqual(result["error_code"], "skipped_reauth")
        self.assertEqual(result["summary"]["status_updated"], 0)
        self.assertEqual(self.db.get_order_by_id("order-1")["order_status"], "shipped")
        self.assertEqual(
            self.db.get_account_session_refresh("account-1")["state"],
            "manual_reauth_required",
        )

    async def test_manual_reauth_state_skips_discovery_without_changing_orders(self):
        self.db.insert_or_update_order(
            order_id="order-1",
            order_status="shipped",
            cookie_id="account-1",
        )
        self.db.update_account_session_refresh(
            "account-1",
            state="manual_reauth_required",
            trigger="test",
            error_code="session_expired",
        )
        discoverer = AsyncMock()

        result = await OrderSyncCoordinator(
            self.db,
            discoverer=discoverer,
        ).sync_account(
            cookie_id="account-1",
            cookie_string="unb=account-1; cookie2=value",
        )

        self.assertTrue(result["success"])
        self.assertTrue(result["skipped"])
        self.assertEqual(result["skip_reason"], "skipped_reauth")
        self.assertEqual(result["coverage"], "none")
        discoverer.assert_not_awaited()
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

    async def test_unknown_status_is_partial_failure_even_when_reliable_fields_are_saved(self):
        self.db.insert_or_update_order(
            order_id="order-known",
            order_status="shipped",
            cookie_id="account-1",
        )

        async def discoverer(**_kwargs):
            return {
                "success": True,
                "orders": [{
                    "order_id": "order-known",
                    "item_id": "item-1",
                    "amount": "19.90",
                    "order_status": "unknown",
                    "platform_status_text": "",
                }],
            }

        result = await OrderSyncCoordinator(
            self.db,
            discoverer=discoverer,
        ).sync_account(
            cookie_id="account-1",
            cookie_string="unb=account-1; cookie2=value",
        )

        self.assertFalse(result["success"])
        self.assertTrue(result["partial"])
        self.assertEqual(result["error_code"], "status_unconfirmed")
        self.assertEqual(result["summary"]["status_unconfirmed"], 1)
        self.assertEqual(result["summary"]["failed"], 1)
        self.assertEqual(self.db.get_order_by_id("order-known")["order_status"], "shipped")
        self.assertEqual(self.db.get_order_by_id("order-known")["paid_amount_fen"], 1990)
        self.assertEqual(result["fields_obtained"], ["amount"])

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

    async def test_recent_order_client_does_not_stop_on_one_old_row_in_a_mixed_page(self):
        requested_pages = []

        async def page_loader(**kwargs):
            page_number = kwargs["page_number"]
            requested_pages.append(page_number)
            if page_number == 1:
                return {
                    "ret": ["SUCCESS::调用成功"],
                    "data": {
                        "module": {
                            "nextPage": "true",
                            "items": [
                                {
                                    "commonData": {
                                        "orderId": "pinned-old-order",
                                        "orderStatus": "交易成功",
                                        "createTime": "2026-01-01 10:00:00",
                                    }
                                },
                                {
                                    "commonData": {
                                        "orderId": "recent-order",
                                        "orderStatus": "交易成功",
                                        "createTime": "2026-07-01 10:00:00",
                                    }
                                },
                            ],
                        }
                    },
                }
            return {
                "ret": ["SUCCESS::调用成功"],
                "data": {
                    "module": {
                        "nextPage": "false",
                        "items": [{
                            "commonData": {
                                "orderId": "later-recent-order",
                                "orderStatus": "待发货",
                                "createTime": "2026-06-30 10:00:00",
                            }
                        }],
                    }
                },
            }

        result = await XianyuOrderListClient(
            page_loader=page_loader,
            now_fn=lambda: 1783180800.0,
            page_size=2,
        ).discover(
            cookie_id="account-1",
            cookie_string="unb=account-1; _m_h5_tk=token_value",
            days=90,
        )

        self.assertTrue(result["success"])
        self.assertEqual(requested_pages, [1, 2])
        self.assertEqual(
            [row["order_id"] for row in result["orders"]],
            ["recent-order", "later-recent-order"],
        )

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

    async def test_permission_denied_falls_back_to_pending_orders_only(self):
        merchant_loader = AsyncMock(return_value={
            "ret": ["PERMISSION_EXCEPTION::seller order permission denied"],
        })
        pending_loader = AsyncMock(return_value={
            "ret": ["SUCCESS::调用成功"],
            "data": {
                "items": [{
                    "bizOrderId": "order-pending",
                    "auctionId": "item-pending",
                    "buyerId": "buyer-pending",
                    "auctionTitle": "测试商品",
                    "totalFee": "9.90",
                    "buyAmount": 1,
                    "orderStatus": "2",
                }],
            },
        })

        result = await XianyuOrderListClient(
            page_loader=merchant_loader,
            pending_page_loader=pending_loader,
        ).discover(
            cookie_id="account-1",
            cookie_string="unb=account-1; _m_h5_tk=token_value",
        )

        self.assertTrue(result["success"])
        self.assertTrue(result["partial"])
        self.assertEqual(result["coverage"], "pending_only")
        self.assertEqual(result["orders"][0]["order_id"], "order-pending")
        merchant_loader.assert_awaited_once()
        pending_loader.assert_awaited_once()

    async def test_non_permission_failures_never_use_pending_fallback(self):
        pending_loader = AsyncMock()
        result = await XianyuOrderListClient(
            page_loader=AsyncMock(return_value={
                "ret": ["FAIL_SYS_SESSION_EXPIRED::Session过期"],
            }),
            pending_page_loader=pending_loader,
        ).discover(
            cookie_id="account-1",
            cookie_string="unb=account-1; _m_h5_tk=token_value",
        )

        self.assertFalse(result["success"])
        self.assertEqual(result["error_code"], "session_expired")
        pending_loader.assert_not_awaited()

    async def test_recent_order_client_merges_set_cookie_and_retries_once(self):
        cookie_values = []

        async def page_loader(**kwargs):
            cookie_values.append(kwargs["cookie_string"])
            if len(cookie_values) == 1:
                return {
                    "ret": ["FAIL_SYS_TOKEN_EXPIRED::令牌过期"],
                    "_cookie_updates": {"_m_h5_tk": "fresh_token_suffix"},
                }
            return {
                "ret": ["SUCCESS::调用成功"],
                "data": {"module": {"items": [], "nextPage": "false"}},
            }

        client = XianyuOrderListClient(page_loader=page_loader)
        result = await client.discover(
            cookie_id="account-1",
            cookie_string="unb=account-1; _m_h5_tk=expired_token_suffix",
            user_agent="Synthetic-UA",
        )

        self.assertTrue(result["success"])
        self.assertEqual(len(cookie_values), 2)
        self.assertIn("_m_h5_tk=fresh_token_suffix", cookie_values[1])
        self.assertIn("_m_h5_tk=fresh_token_suffix", result["updated_cookie_string"])

    async def test_recent_order_client_rejects_cookie_identity_change(self):
        async def page_loader(**_kwargs):
            return {
                "ret": ["FAIL_SYS_TOKEN_EXPIRED::令牌过期"],
                "_cookie_updates": {"unb": "account-2"},
            }

        result = await XianyuOrderListClient(page_loader=page_loader).discover(
            cookie_id="account-1",
            cookie_string="unb=account-1; _m_h5_tk=expired_token_suffix",
        )

        self.assertFalse(result["success"])
        self.assertEqual(result["error_code"], "account_identity_mismatch")

    async def test_recent_order_client_rejects_success_without_list_container(self):
        async def page_loader(**_kwargs):
            return {
                "ret": ["SUCCESS::调用成功"],
                "data": {"module": {"nextPage": "false"}},
            }

        result = await XianyuOrderListClient(page_loader=page_loader).discover(
            cookie_id="account-1",
            cookie_string="unb=account-1; _m_h5_tk=token_value",
        )

        self.assertFalse(result["success"])
        self.assertEqual(result["error_code"], "invalid_response_schema")

    async def test_recent_order_client_retries_transient_failure_with_backoff(self):
        calls = 0
        sleep = AsyncMock()

        async def page_loader(**_kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return {"ret": ["HTTP_429::订单接口请求失败"]}
            return {
                "ret": ["SUCCESS::调用成功"],
                "data": {"module": {"items": [], "nextPage": "false"}},
            }

        result = await XianyuOrderListClient(
            page_loader=page_loader,
            sleep_fn=sleep,
            jitter_fn=lambda _start, _end: 0.0,
        ).discover(
            cookie_id="account-1",
            cookie_string="unb=account-1; _m_h5_tk=token_value",
        )

        self.assertTrue(result["success"])
        self.assertEqual(calls, 2)
        sleep.assert_awaited_once_with(0.75)

    async def test_recent_order_client_retries_server_errors_with_backoff(self):
        calls = 0
        sleep = AsyncMock()

        async def page_loader(**_kwargs):
            nonlocal calls
            calls += 1
            if calls <= 2:
                return {"ret": ["HTTP_503::订单接口请求失败"]}
            return {
                "ret": ["SUCCESS::调用成功"],
                "data": {"module": {"items": [], "nextPage": "false"}},
            }

        result = await XianyuOrderListClient(
            page_loader=page_loader,
            max_retries=2,
            sleep_fn=sleep,
            jitter_fn=lambda _start, _end: 0.0,
        ).discover(
            cookie_id="account-server-retry",
            cookie_string="unb=account-server-retry; _m_h5_tk=token_value",
        )

        self.assertTrue(result["success"])
        self.assertEqual(calls, 3)
        self.assertEqual(
            [call.args[0] for call in sleep.await_args_list],
            [0.75, 1.5],
        )

    async def test_recent_order_client_stops_after_retry_limit(self):
        calls = 0
        sleep = AsyncMock()

        async def page_loader(**_kwargs):
            nonlocal calls
            calls += 1
            return {"ret": ["HTTP_503::订单接口请求失败"]}

        result = await XianyuOrderListClient(
            page_loader=page_loader,
            max_retries=2,
            sleep_fn=sleep,
            jitter_fn=lambda _start, _end: 0.0,
        ).discover(
            cookie_id="account-retry-limit",
            cookie_string="unb=account-retry-limit; _m_h5_tk=token_value",
        )

        self.assertFalse(result["success"])
        self.assertEqual(result["error_code"], "platform_unavailable")
        self.assertEqual(calls, 3)
        self.assertEqual(sleep.await_count, 2)

    async def test_production_transport_has_total_timeout_and_maps_timeout(self):
        captured = {}

        class TimeoutResponse:
            async def __aenter__(self):
                raise asyncio.TimeoutError

            async def __aexit__(self, *_exc):
                return False

        class TimeoutSession:
            def __init__(self, *args, **kwargs):
                captured.update(kwargs)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc):
                return False

            def post(self, *_args, **_kwargs):
                return TimeoutResponse()

        with patch(
            "order_sync_service.aiohttp.ClientSession",
            TimeoutSession,
        ), patch("utils.xianyu_utils.generate_sign", return_value="test-sign"):
            result = await fetch_xianyu_order_list_page(
                cookie_id="account-timeout",
                cookie_string="unb=account-timeout; _m_h5_tk=token_suffix",
                page_number=1,
                page_size=20,
                user_id="account-timeout",
            )

        self.assertEqual(result, {"ret": ["NETWORK_ERROR::TimeoutError"]})
        self.assertEqual(captured["timeout"].total, 20)

    async def test_pending_transport_uses_verified_api_shape(self):
        captured = {}

        class Response:
            status = 200
            headers = {}

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc):
                return False

            async def json(self, **_kwargs):
                return {"ret": ["SUCCESS::调用成功"], "data": {"items": []}}

        class Session:
            def __init__(self, **_kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc):
                return False

            def post(self, url, **kwargs):
                captured["url"] = url
                captured.update(kwargs)
                return Response()

        with patch(
            "order_sync_service.aiohttp.ClientSession",
            Session,
        ), patch("utils.xianyu_utils.generate_sign", return_value="test-sign"):
            result = await fetch_xianyu_pending_order_page(
                cookie_id="account-1",
                cookie_string="unb=account-1; _m_h5_tk=token_suffix",
                page_number=9,
                page_size=99,
                user_id="account-1",
                user_agent="Synthetic-UA",
            )

        self.assertEqual(result["data"]["items"], [])
        self.assertTrue(captured["url"].endswith(
            "/mtop.taobao.idle.trade.sold.get/5.0/"
        ))
        self.assertEqual(captured["params"]["api"], "mtop.taobao.idle.trade.sold.get")
        self.assertEqual(captured["params"]["v"], "5.0")
        self.assertEqual(captured["params"]["type"], "originaljson")
        self.assertEqual(captured["params"]["valueType"], "original")
        self.assertEqual(
            json.loads(captured["data"]["data"]),
            {"pageNumber": 1, "orderStatus": "NOT_SHIP", "offsetRow": 0},
        )
        self.assertEqual(captured["headers"]["Origin"], "https://h5.m.goofish.com")

    async def test_recent_order_client_stops_when_target_order_is_found(self):
        requested_pages = []

        async def page_loader(**kwargs):
            requested_pages.append(kwargs["page_number"])
            return {
                "ret": ["SUCCESS::调用成功"],
                "data": {
                    "module": {
                        "items": [{
                            "commonData": {
                                "orderId": "target-order",
                                "orderStatus": "待发货",
                            }
                        }],
                        "nextPage": "true",
                    }
                },
            }

        result = await XianyuOrderListClient(
            page_loader=page_loader,
            page_size=1,
        ).discover(
            cookie_id="account-1",
            cookie_string="unb=account-1; _m_h5_tk=token_value",
            target_order_id="target-order",
        )

        self.assertTrue(result["success"])
        self.assertEqual(requested_pages, [1])
        self.assertEqual(result["orders"][0]["order_id"], "target-order")

    async def test_recent_order_client_rejects_non_object_order_rows(self):
        async def page_loader(**_kwargs):
            return {
                "ret": ["SUCCESS::调用成功"],
                "data": {"module": {"items": ["invalid-row"], "nextPage": "false"}},
            }

        result = await XianyuOrderListClient(page_loader=page_loader).discover(
            cookie_id="account-1",
            cookie_string="unb=account-1; _m_h5_tk=token_value",
        )

        self.assertFalse(result["success"])
        self.assertEqual(result["error_code"], "invalid_response_schema")

    async def test_recent_order_client_marks_page_limit_as_truncated(self):
        requested_pages = []

        async def page_loader(**kwargs):
            page_number = kwargs["page_number"]
            requested_pages.append(page_number)
            return {
                "ret": ["SUCCESS::调用成功"],
                "data": {
                    "module": {
                        "items": [{
                            "commonData": {
                                "orderId": f"order-{page_number}",
                                "orderStatus": "待发货",
                            }
                        }],
                        "nextPage": "true",
                    }
                },
            }

        result = await XianyuOrderListClient(
            page_loader=page_loader,
            page_size=1,
            max_pages=2,
            max_orders=10,
        ).discover(
            cookie_id="account-1",
            cookie_string="unb=account-1; _m_h5_tk=token_value",
        )

        self.assertTrue(result["success"])
        self.assertTrue(result["truncated"])
        self.assertEqual(result["pages_scanned"], 2)
        self.assertEqual(requested_pages, [1, 2])

    async def test_recent_order_client_marks_order_limit_as_truncated(self):
        async def page_loader(**_kwargs):
            return {
                "ret": ["SUCCESS::调用成功"],
                "data": {
                    "module": {
                        "items": [
                            {
                                "commonData": {
                                    "orderId": "order-1",
                                    "orderStatus": "待发货",
                                }
                            },
                            {
                                "commonData": {
                                    "orderId": "order-2",
                                    "orderStatus": "待发货",
                                }
                            },
                        ],
                        "nextPage": "true",
                    }
                },
            }

        result = await XianyuOrderListClient(
            page_loader=page_loader,
            page_size=2,
            max_pages=10,
            max_orders=1,
        ).discover(
            cookie_id="account-1",
            cookie_string="unb=account-1; _m_h5_tk=token_value",
        )

        self.assertTrue(result["success"])
        self.assertTrue(result["truncated"])
        self.assertEqual(len(result["orders"]), 1)

    async def test_coordinator_reports_sync_limit_reached(self):
        async def discoverer(**_kwargs):
            return {
                "success": True,
                "truncated": True,
                "orders": [{
                    "order_id": "order-limit",
                    "order_status": "pending_ship",
                    "platform_status_text": "待发货",
                    "quantity": "1",
                }],
            }

        result = await OrderSyncCoordinator(
            self.db,
            discoverer=discoverer,
        ).sync_account(
            cookie_id="account-1",
            cookie_string="unb=account-1; cookie2=value",
        )

        self.assertFalse(result["success"])
        self.assertTrue(result["partial"])
        self.assertEqual(result["error_code"], "sync_limit_reached")

    async def test_account_syncs_are_serialized_for_the_same_account(self):
        entered = asyncio.Event()
        release = asyncio.Event()
        active = 0
        max_active = 0

        async def discoverer(**_kwargs):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            entered.set()
            await release.wait()
            active -= 1
            return {"success": True, "orders": []}

        coordinator = OrderSyncCoordinator(self.db, discoverer=discoverer)
        first = asyncio.create_task(coordinator.sync_account(
            cookie_id="account-1",
            cookie_string="unb=account-1; cookie2=value",
        ))
        await entered.wait()
        second = asyncio.create_task(coordinator.sync_account(
            cookie_id="account-1",
            cookie_string="unb=account-1; cookie2=value",
        ))
        await asyncio.sleep(0)
        self.assertEqual(max_active, 1)
        release.set()
        await asyncio.gather(first, second)
        self.assertEqual(max_active, 1)

    async def test_account_syncs_for_different_accounts_can_run_in_parallel(self):
        both_entered = asyncio.Event()
        release = asyncio.Event()
        active = 0
        max_active = 0

        async def discoverer(**_kwargs):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            if active == 2:
                both_entered.set()
            await release.wait()
            active -= 1
            return {"success": True, "orders": []}

        coordinator = OrderSyncCoordinator(self.db, discoverer=discoverer)
        first = asyncio.create_task(coordinator.sync_account(
            cookie_id="parallel-account-1",
            cookie_string="unb=parallel-account-1; cookie2=value",
        ))
        second = asyncio.create_task(coordinator.sync_account(
            cookie_id="parallel-account-2",
            cookie_string="unb=parallel-account-2; cookie2=value",
        ))
        try:
            await asyncio.wait_for(both_entered.wait(), timeout=1)
            self.assertEqual(max_active, 2)
        finally:
            release.set()
            await asyncio.gather(first, second)

    async def test_coordinator_persists_refreshed_cookie_without_returning_it(self):
        updates = []

        async def discoverer(**_kwargs):
            return {
                "success": True,
                "orders": [],
                "updated_cookie_string": "unb=account-1; cookie2=fresh",
            }

        async def cookie_updater(cookie_id, cookie_string):
            updates.append((cookie_id, cookie_string))
            return True

        result = await OrderSyncCoordinator(
            self.db,
            discoverer=discoverer,
            cookie_updater=cookie_updater,
        ).sync_account(
            cookie_id="account-1",
            cookie_string="unb=account-1; cookie2=old",
        )

        self.assertTrue(result["success"])
        self.assertEqual(updates, [("account-1", "unb=account-1; cookie2=fresh")])
        self.assertNotIn("updated_cookie_string", result)

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

        self.assertCountEqual(requested_order_ids, [
            "order-shipped", "order-completed", "order-refunded", "order-legacy-closed",
        ])
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

    async def test_detail_unknown_status_counts_as_failure_and_preserves_known_status(self):
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
                "order_status": "unknown",
                "amount": "28.00",
            }]

        result = await OrderSyncCoordinator(
            self.db,
            discoverer=discoverer,
            detail_fetcher=detail_fetcher,
            now_fn=lambda: 1783180800.0,
        ).sync_account(
            cookie_id="account-1",
            cookie_string="unb=account-1; cookie2=value",
            days=90,
        )

        self.assertFalse(result["success"])
        self.assertTrue(result["partial"])
        self.assertEqual(result["error_code"], "status_unconfirmed")
        self.assertEqual(result["summary"]["status_unconfirmed"], 1)
        stored = self.db.get_order_by_id("order-shipped")
        self.assertEqual(stored["order_status"], "shipped")
        self.assertEqual(stored["paid_amount_fen"], 2800)


if __name__ == "__main__":
    unittest.main()
