import base64
import inspect
import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

from XianyuAutoAsync import XianyuLive


class NotificationPolicyTests(unittest.IsolatedAsyncioTestCase):
    def test_customer_chat_path_does_not_dispatch_generic_notifications(self):
        source = inspect.getsource(XianyuLive.handle_message)

        self.assertNotIn("send_notification(", source)
        self.assertIn('last_inbound_kind = "customer_chat"', source)

    def test_successful_delivery_path_is_silent_but_failure_path_remains(self):
        source = inspect.getsource(XianyuLive._handle_auto_delivery)

        self.assertNotIn("多数量发货成功", source)
        self.assertNotIn('"发货成功"', source)
        self.assertIn("send_delivery_failure_notification", source)
        self.assertIn("自动发货处理异常", source)

    async def test_lead_promo_card_never_enters_customer_or_reply_paths(self):
        notice = "开通留资卡功能   建联更安全"
        message = {
            "1": {
                "2": "chat-fixture@goofish",
                "5": 1787100000000,
                "6": {"3": {"1": 1, "2": notice, "5": json.dumps({
                    "contentType": 1,
                    "text": {"text": notice},
                }, ensure_ascii=False)}},
                "10": {
                    "senderNick": "buyer",
                    "senderUserId": "buyer-fixture",
                    "reminderContent": notice,
                    "reminderUrl": "fleamarket://message_chat?itemId=item-fixture",
                },
            },
        }
        frame = {
            "body": {"syncPushPackage": {"data": [{
                "data": base64.b64encode(
                    json.dumps(message, ensure_ascii=False).encode()
                ).decode(),
            }]}},
        }
        live = object.__new__(XianyuLive)
        live.cookie_id = "account-fixture"
        live.myid = "seller-fixture"
        live.order_status_handler = None
        live.is_sync_package = Mock(return_value=True)
        live._extract_order_id = Mock(return_value=None)
        live.extract_item_id_from_message = Mock(return_value="item-fixture")
        live.is_chat_message = Mock(return_value=True)
        live._safe_str = str
        live._schedule_debounced_reply = AsyncMock()
        live._handle_auto_delivery = AsyncMock()
        manager = Mock()
        manager.get_cookie_status.return_value = True

        with (
            patch("cookie_manager.manager", manager),
            patch("XianyuAutoAsync._upsert_realtime_customer_profile") as profile,
        ):
            await live.handle_message(frame, AsyncMock(), acknowledge=False)

        profile.assert_not_called()
        live._schedule_debounced_reply.assert_not_awaited()
        live._handle_auto_delivery.assert_not_awaited()

    async def test_delivery_failure_still_dispatches_an_enabled_email_channel(self):
        live = object.__new__(XianyuLive)
        live.cookie_id = "account-fixture"
        live._send_email_notification = AsyncMock(return_value=True)

        database = Mock()
        database.get_account_notifications.return_value = [{
            "enabled": True,
            "channel_type": "email",
            "channel_config": json.dumps({"recipient_email": "fixture@example.test"}),
        }]

        with patch("db_manager.db_manager", database):
            await live.send_delivery_failure_notification(
                "buyer-fixture",
                "buyer-id-fixture",
                "item-fixture",
                "delivery failed",
                "chat-fixture",
            )

        live._send_email_notification.assert_awaited_once()
        self.assertIn("delivery failed", live._send_email_notification.await_args.args[1])

    async def test_manual_reauth_transition_keeps_single_alert(self):
        live = object.__new__(XianyuLive)
        live.cookie_id = "account-fixture"
        live.last_token_refresh_status = None
        live.send_token_refresh_notification = AsyncMock()

        states = [{"state": "failed"}, {"state": "manual_reauth_required"}]
        database = Mock()
        database.get_account_session_refresh.side_effect = (
            lambda _cookie_id: states.pop(0) if len(states) > 1 else states[0]
        )
        database.update_account_session_refresh.return_value = True

        with patch("db_manager.db_manager", database):
            await live._enter_manual_reauth_required(
                trigger="fixture-session-expired",
                message="fixture account requires reauthentication",
            )

        live.send_token_refresh_notification.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
