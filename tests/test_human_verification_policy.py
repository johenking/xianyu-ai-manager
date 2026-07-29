import asyncio
import inspect
from collections import defaultdict
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, Mock, patch

from db_manager import DBManager
from cookie_manager import CookieManager
from order_status_handler import OrderStatusHandler
from XianyuAutoAsync import (
    AUTO_DELIVERY_SOURCE_BARGAIN_FREESHIPPING,
    AUTO_DELIVERY_SOURCE_PAID_NOTICE,
    XianyuLive,
    _delivery_identity_is_confirmed,
    _mask_account_ids_in_log,
    log_captcha_event,
)


class HumanVerificationPolicyTests(unittest.TestCase):
    def test_token_refresh_does_not_invoke_automatic_slider_solver(self):
        source = inspect.getsource(XianyuLive.refresh_token)

        self.assertNotIn("_handle_captcha_verification", source)
        self.assertNotIn("XianyuSliderStealth", source)
        self.assertNotIn("Token刷新失败: {res_json}", source)
        self.assertNotIn("滑块验证重试", source)

    def test_verification_log_uses_a_digest_instead_of_the_raw_account_id(self):
        source = inspect.getsource(log_captcha_event)

        self.assertIn("hashlib.sha256", source)
        self.assertNotIn("【{cookie_id}】", source)

    def test_runtime_log_patcher_masks_stable_account_identifiers(self):
        record = {
            "message": (
                "【2219255254384】正在重启账号监听；"
                "更新账号 2219255254384 信息成功；"
                "用户ID: 2219255254384；"
                "'cookie_id': '2219255254384'"
            )
        }

        _mask_account_ids_in_log(record)

        self.assertNotIn("2219255254384", record["message"])
        self.assertRegex(record["message"], r"^【account_[0-9a-f]{10}】")

    def test_runtime_log_patcher_masks_order_and_buyer_identifiers(self):
        record = {
            "message": "订单 8837155265489 的买家 2219255254384 触发自动发货"
        }

        _mask_account_ids_in_log(record)

        self.assertNotIn("8837155265489", record["message"])
        self.assertNotIn("2219255254384", record["message"])
        self.assertEqual(record["message"].count("ref_"), 2)

    def test_runtime_log_patcher_preserves_operational_numbers(self):
        record = {
            "message": (
                "migration_version=2026072703 processed=487 duration_ms=84092; "
                "order_id=8837155265489 buyer_id=2219255254384 "
                "session_id=3141592653589"
            )
        }

        _mask_account_ids_in_log(record)

        self.assertIn("migration_version=2026072703", record["message"])
        self.assertIn("processed=487", record["message"])
        self.assertIn("duration_ms=84092", record["message"])
        self.assertNotIn("8837155265489", record["message"])
        self.assertNotIn("2219255254384", record["message"])
        self.assertNotIn("3141592653589", record["message"])
        self.assertEqual(record["message"].count("ref_"), 3)

    def test_cookie_manager_logs_mask_accounts_and_omit_exception_details(self):
        source = Path("cookie_manager.py").read_text(encoding="utf-8")
        logging_lines = "\n".join(
            line for line in source.splitlines() if "logger." in line
        )

        self.assertNotIn("traceback.format_exc", source)
        self.assertNotIn("【{cookie_id}】", logging_lines)
        self.assertNotIn(": {cookie_id}", logging_lines)
        self.assertNotIn(": {e}", logging_lines)
        self.assertNotIn(", {e}", logging_lines)

    def test_listener_bootstrap_masks_account_id_before_xianyu_import(self):
        source = inspect.getsource(CookieManager._run_xianyu)
        before_xianyu_import = source.split("from XianyuAutoAsync", 1)[0]

        self.assertIn("_mask_cookie_id(cookie_id)", before_xianyu_import)
        self.assertNotIn("【{cookie_id}】", before_xianyu_import)

    def test_item_sync_logs_only_a_bounded_response_summary(self):
        source = inspect.getsource(XianyuLive.get_item_list_info)

        self.assertNotIn("商品信息获取响应: {res_json}", source)
        self.assertNotIn("获取商品信息失败: {res_json}", source)
        self.assertNotIn("已从Cookie读取_m_h5_tk token", source)
        self.assertNotIn('print(f"📦 账号 {self.myid}', source)
        self.assertNotIn("完整信息", source)
        self.assertIn("商品列表响应摘要", source)

    def test_item_lookup_does_not_log_the_complete_database_record(self):
        source = inspect.getsource(DBManager.get_item_info)

        self.assertNotIn("item_info: {item_info}", source)
        self.assertIn("已读取商品信息摘要", source)

    def test_item_detail_request_logs_only_a_bounded_response_summary(self):
        source = inspect.getsource(XianyuLive.get_item_info)

        self.assertNotIn("商品信息获取成功: {res_json}", source)
        self.assertNotIn("商品信息API返回格式异常: {res_json}", source)
        self.assertNotIn("已从Cookie读取_m_h5_tk token", source)
        self.assertIn("商品详情响应摘要", source)

    def test_runtime_logs_do_not_embed_customer_messages_or_identity(self):
        source = Path("XianyuAutoAsync.py").read_text(encoding="utf-8")
        logging_lines = "\n".join(
            line for line in source.splitlines() if "logger." in line
        )

        for raw_value in (
            "{message}",
            "{send_message}",
            "{send_user_name}",
            "{send_user_id}",
            "{param_mapping}",
            "{search_text",
        ):
            with self.subTest(raw_value=raw_value):
                self.assertNotIn(raw_value, logging_lines)

    def test_supporting_modules_log_only_bounded_response_metadata(self):
        checks = {
            "order_status_handler.py": ("{send_message}",),
            "ai_reply_engine.py": ("{message}", "response.text", "{result}"),
            "utils/qr_login.py": ("获取会话状态: {result}",),
            "utils/refresh_util.py": ("{res_json}", "{token_result}"),
            "secure_confirm_decrypted.py": ("自动确认发货响应: {res_json}",),
            "secure_freeshipping_decrypted.py": (
                "请求参数: data_val",
                "参数详情 - order_id",
                "自动免拼发货响应: {res_json}",
            ),
            "utils/image_uploader.py": (
                "上传响应: {response_text}",
                "{response_text[:",
                "{response_data}",
            ),
        }
        for source_path, forbidden_values in checks.items():
            source = Path(source_path).read_text(encoding="utf-8")
            logging_lines = "\n".join(
                line for line in source.splitlines() if "logger." in line
            )
            for raw_value in forbidden_values:
                with self.subTest(source=source_path, raw_value=raw_value):
                    self.assertNotIn(raw_value, logging_lines)

    def test_api_and_ai_logs_do_not_embed_message_content_or_secret_urls(self):
        checks = {
            "reply_server.py": ("内容: {cleaned_message",),
            "ai_reply_engine.py": ("请求URL: {e.response.url}", "请求URL: {e.request.url}"),
            "XianyuAutoAsync.py": (
                "{formatted_reply}",
                "AI回复生成成功: {reply}",
                "响应内容: {response_text}",
                "响应: {response_text}",
                "其他类型消息: {content}",
            ),
        }
        for source_path, forbidden_values in checks.items():
            source = Path(source_path).read_text(encoding="utf-8")
            logging_lines = "\n".join(
                line for line in source.splitlines() if "logger." in line
            )
            for raw_value in forbidden_values:
                with self.subTest(source=source_path, raw_value=raw_value):
                    self.assertNotIn(raw_value, logging_lines)

    def test_all_auto_delivery_sources_require_realtime_payment_gate(self):
        source = inspect.getsource(XianyuLive._handle_auto_delivery)

        self.assertIn("_verify_paid_order_for_delivery", source)
        self.assertNotIn("if delivery_source !=", source)
        self.assertNotIn("fetch_order_detail_info", source)

    def test_delivery_identity_requires_both_message_and_platform_identifiers(self):
        self.assertFalse(_delivery_identity_is_confirmed("", "buyer", "item", "buyer"))
        self.assertFalse(_delivery_identity_is_confirmed("item", "", "item", "buyer"))
        self.assertFalse(_delivery_identity_is_confirmed("item", "buyer", "", "buyer"))
        self.assertFalse(_delivery_identity_is_confirmed("item", "buyer", "item", ""))
        self.assertFalse(_delivery_identity_is_confirmed("item", "buyer", "other", "buyer"))
        self.assertFalse(_delivery_identity_is_confirmed("item", "未知买家", "item", "buyer"))
        self.assertFalse(_delivery_identity_is_confirmed("item", "buyer", "item", "未知用户"))
        self.assertTrue(_delivery_identity_is_confirmed("item", "buyer", "item", "buyer"))

    def test_auto_delivery_requires_a_nonempty_order_id(self):
        live = object.__new__(XianyuLive)
        live.last_delivery_time = {}
        live.delivery_cooldown = 600

        self.assertFalse(live.can_auto_delivery(""))
        self.assertFalse(live.can_auto_delivery(None))

    def test_red_reminder_without_identity_is_not_persisted(self):
        handler = OrderStatusHandler()
        database = Mock()

        with patch("db_manager.db_manager", database):
            handled = handler.handle_red_reminder_message(
                message={},
                red_reminder="交易关闭",
                user_id="",
                cookie_id="account-for-test",
                msg_time="now",
            )

        self.assertFalse(handled)
        database.record_order_status_event.assert_not_called()

    def test_realtime_delivery_check_rejects_missing_platform_identity(self):
        async def verify(returned_item_id, returned_buyer_id):
            live = object.__new__(XianyuLive)
            live.cookie_id = "account-for-test"
            live.cookies_str = "unb=account-for-test"
            live.browser_user_agent = "test-agent"
            with patch("order_sync_service.XianyuOrderListClient") as client_class:
                client_class.return_value.discover = AsyncMock(return_value={
                    "success": True,
                    "orders": [{
                        "order_id": "order-for-test",
                        "item_id": returned_item_id,
                        "buyer_id": returned_buyer_id,
                        "order_status": "pending_ship",
                    }],
                })
                return await live._verify_paid_order_for_delivery(
                    "order-for-test", "item-for-test", "buyer-for-test"
                )

        for item_id, buyer_id in (("", "buyer-for-test"), ("item-for-test", "")):
            with self.subTest(item_id=bool(item_id), buyer_id=bool(buyer_id)):
                result = asyncio.run(verify(item_id, buyer_id))
                self.assertFalse(result["allowed"])
                self.assertEqual(result["status"], "identity_unconfirmed")

    def test_bargain_freeshipping_runs_only_after_the_shared_payment_gate(self):
        handler_source = inspect.getsource(XianyuLive._handle_auto_delivery)
        workflow_source = inspect.getsource(XianyuLive._execute_fulfillment_attempt)

        self.assertLess(
            handler_source.index("payment_check = await"),
            handler_source.index("await self._execute_fulfillment_attempt"),
        )
        self.assertIn("await self.auto_freeshipping", workflow_source)

    def _make_fulfillment_live(self, delivery_content="card-value"):
        live = object.__new__(XianyuLive)
        live.cookie_id = "account-for-test"
        live.last_delivery_time = {}
        live.delivery_cooldown = 0
        live._order_locks = defaultdict(asyncio.Lock)
        live._lock_usage_times = {}
        live._lock_hold_info = {}
        live.confirmed_orders = {}
        live.is_lock_held = Mock(return_value=False)
        live._extract_order_id = Mock(return_value="order-for-test")
        live._verify_paid_order_for_delivery = AsyncMock(return_value={
            "allowed": True,
            "quantity": 1,
        })
        live._auto_delivery = AsyncMock(return_value=delivery_content)
        live.send_msg = AsyncMock()
        live.send_image_msg = AsyncMock()
        live.send_delivery_failure_notification = AsyncMock()
        live.auto_confirm = AsyncMock(return_value={"success": True})
        live.auto_freeshipping = AsyncMock(return_value={"success": True})
        live.is_auto_confirm_enabled = Mock(return_value=True)
        live.mark_delivery_sent = Mock()
        return live

    @staticmethod
    def _discard_delayed_lock(coro):
        coro.close()
        return Mock()

    def _run_fulfillment(
        self,
        live,
        database,
        delivery_source=AUTO_DELIVERY_SOURCE_PAID_NOTICE,
    ):
        with patch("db_manager.db_manager", database), patch(
            "XianyuAutoAsync.asyncio.create_task",
            side_effect=self._discard_delayed_lock,
        ), patch("XianyuAutoAsync.asyncio.sleep", new=AsyncMock()):
            asyncio.run(live._handle_auto_delivery(
                websocket=Mock(),
                message={},
                send_user_name="buyer",
                send_user_id="buyer-for-test",
                item_id="item-for-test",
                chat_id="chat-for-test",
                msg_time="now",
                delivery_source=delivery_source,
            ))

    @staticmethod
    def _fulfillment_database():
        database = Mock()
        database.get_item_info.return_value = {"item_id": "item-for-test"}
        database.get_item_multi_quantity_delivery_status.return_value = False
        database.get_order_by_id.return_value = {
            "order_id": "order-for-test",
            "cookie_id": "account-for-test",
        }
        database.begin_fulfillment_attempt.return_value = {
            "outcome": "acquired",
            "attempt_id": 73,
        }
        database.mark_fulfillment_sending.return_value = True
        database.commit_fulfillment_attempt.return_value = True
        database.release_fulfillment_attempt.return_value = True
        database.mark_fulfillment_manual_review.return_value = True
        return database

    def test_fulfillment_marks_sending_before_platform_confirmation_and_message(self):
        live = self._make_fulfillment_live()
        database = self._fulfillment_database()
        events = []
        database.mark_fulfillment_sending.side_effect = lambda *_: events.append("sending") or True
        live.auto_confirm.side_effect = lambda *_: events.append("confirm") or {"success": True}
        live.send_msg.side_effect = lambda *_: events.append("message")
        database.commit_fulfillment_attempt.side_effect = lambda *_args, **_kwargs: events.append("commit") or True

        self._run_fulfillment(live, database)

        self.assertEqual(events, ["sending", "confirm", "message", "commit"])
        database.release_fulfillment_attempt.assert_not_called()
        live.mark_delivery_sent.assert_called_once_with("order-for-test")

    def test_sending_transition_failure_releases_without_platform_or_message_side_effects(self):
        live = self._make_fulfillment_live()
        database = self._fulfillment_database()
        database.mark_fulfillment_sending.return_value = False

        self._run_fulfillment(live, database)

        database.release_fulfillment_attempt.assert_called_once_with(73, "sending_transition_failed")
        live.auto_confirm.assert_not_awaited()
        live.send_msg.assert_not_awaited()
        database.commit_fulfillment_attempt.assert_not_called()

    def test_unconfirmed_platform_response_enters_manual_review_without_delivery(self):
        live = self._make_fulfillment_live()
        live.auto_confirm.return_value = {"success": False}
        database = self._fulfillment_database()

        self._run_fulfillment(live, database)

        database.mark_fulfillment_manual_review.assert_called_once_with(
            73, "platform_confirmation_unconfirmed", sent_count=0
        )
        live.send_msg.assert_not_awaited()
        database.release_fulfillment_attempt.assert_not_called()
        database.commit_fulfillment_attempt.assert_not_called()

    def test_regular_platform_exception_enters_manual_review_without_release(self):
        live = self._make_fulfillment_live()
        live.auto_confirm.side_effect = RuntimeError("confirmation transport failed")
        database = self._fulfillment_database()

        self._run_fulfillment(live, database)

        database.mark_fulfillment_manual_review.assert_called_once_with(
            73, "platform_confirmation_error", sent_count=0
        )
        database.release_fulfillment_attempt.assert_not_called()
        live.send_msg.assert_not_awaited()
        database.commit_fulfillment_attempt.assert_not_called()

    def test_bargain_platform_exception_enters_manual_review_without_release(self):
        live = self._make_fulfillment_live()
        live.auto_freeshipping.side_effect = RuntimeError("free-shipping transport failed")
        database = self._fulfillment_database()

        self._run_fulfillment(
            live,
            database,
            delivery_source=AUTO_DELIVERY_SOURCE_BARGAIN_FREESHIPPING,
        )

        database.mark_fulfillment_manual_review.assert_called_once_with(
            73, "platform_confirmation_error", sent_count=0
        )
        database.release_fulfillment_attempt.assert_not_called()
        live.auto_confirm.assert_not_awaited()
        live.send_msg.assert_not_awaited()
        database.commit_fulfillment_attempt.assert_not_called()

    def test_partial_buyer_delivery_enters_manual_review_without_commit(self):
        live = self._make_fulfillment_live()
        live.send_msg.side_effect = RuntimeError("network failure")
        database = self._fulfillment_database()

        self._run_fulfillment(live, database)

        database.mark_fulfillment_manual_review.assert_called_once_with(
            73, "buyer_message_failed", sent_count=0
        )
        database.release_fulfillment_attempt.assert_not_called()
        database.commit_fulfillment_attempt.assert_not_called()

    def test_one_of_two_buyer_messages_enters_manual_review_without_release(self):
        live = self._make_fulfillment_live()
        live._verify_paid_order_for_delivery.return_value = {
            "allowed": True,
            "quantity": 2,
        }
        live._auto_delivery.side_effect = ["card-one", "card-two"]
        live.send_msg.side_effect = [None, RuntimeError("second send failed")]
        database = self._fulfillment_database()
        database.get_item_multi_quantity_delivery_status.return_value = True

        self._run_fulfillment(live, database)

        database.mark_fulfillment_manual_review.assert_called_once_with(
            73, "buyer_message_failed", sent_count=1
        )
        database.release_fulfillment_attempt.assert_not_called()
        database.commit_fulfillment_attempt.assert_not_called()
        self.assertEqual(live.send_msg.await_count, 2)

    def test_batch_cards_require_persistent_reservation_and_api_cards_fail_closed(self):
        source = inspect.getsource(XianyuLive._auto_delivery)

        self.assertIn("reserve_batch_card_data", source)
        self.assertNotIn("consume_batch_data", source)
        self.assertIn("api_card_requires_manual_review", source)

    def test_fulfillment_state_machine_has_no_platform_action_before_sending_transition(self):
        source = inspect.getsource(XianyuLive._execute_fulfillment_attempt)

        sending_transition = source.index("mark_fulfillment_sending")
        self.assertLess(sending_transition, source.index("await self.auto_confirm"))
        self.assertLess(sending_transition, source.index("await self.auto_freeshipping"))
        self.assertLess(sending_transition, source.index("await self.send_msg"))

    def test_mark_delivery_sent_updates_the_cooldown_timestamp(self):
        live = object.__new__(XianyuLive)
        live.last_delivery_time = {}
        live.order_status_handler = None
        live.cookie_id = "account-for-test"

        before = __import__("time").time()
        live.mark_delivery_sent("order-for-test")

        self.assertGreaterEqual(live.last_delivery_time["order-for-test"], before)

    def test_fulfillment_status_update_does_not_log_a_traceback(self):
        source = inspect.getsource(XianyuLive.mark_delivery_sent)

        self.assertNotIn("traceback.format_exc", source)

    def test_delivery_requires_all_contents_before_local_shipped_marker(self):
        source = inspect.getsource(XianyuLive._execute_fulfillment_attempt)
        self.assertLess(
            source.index("if not delivery_content"),
            source.index("mark_fulfillment_sending"),
        )
        self.assertIn("sent_count != expected_quantity", source)
        self.assertIn("commit_fulfillment_attempt", source)
        self.assertNotIn("system_shipped=True", source)

    def test_status_event_handler_has_no_unreachable_fifo_reconciliation(self):
        source = inspect.getsource(OrderStatusHandler.on_order_id_extracted)
        self.assertNotIn("使用FIFO", source)
        self.assertIn("reconcile_order_status_events", source)

    def test_dynamic_delivery_params_do_not_read_a_foreign_order(self):
        live = object.__new__(XianyuLive)
        live.cookie_id = "account-one"
        database = Mock()
        database.get_order_by_id.return_value = {
            "order_id": "foreign-order",
            "cookie_id": "account-two",
            "amount": "999.00",
            "quantity": "9",
        }
        database.get_item_info.return_value = None

        with patch("db_manager.db_manager", database):
            result = asyncio.run(live._replace_api_dynamic_params(
                {"amount": "{order_amount}", "quantity": "{order_quantity}"},
                order_id="foreign-order",
            ))

        self.assertEqual(result["amount"], "{order_amount}")
        self.assertEqual(result["quantity"], "{order_quantity}")
