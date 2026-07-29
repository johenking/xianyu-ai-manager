import json
import io
import unittest
from unittest.mock import AsyncMock, Mock, patch

from XianyuAutoAsync import XianyuLive
from PIL import Image
from utils.outbound_http import PublicHTTPResponse


def _response(status=200, payload=None, body=None):
    if body is None:
        body = json.dumps(payload or {}).encode("utf-8")
    return PublicHTTPResponse(status=status, headers={}, body=body)


class ApiCardOutboundTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.live = object.__new__(XianyuLive)
        self.live.cookie_id = "account-test"
        self.live.session = Mock()
        self.live._replace_api_dynamic_params = AsyncMock(
            return_value={"order": "order-1"}
        )

    async def test_api_card_uses_guarded_dedicated_request_without_platform_session(self):
        with patch(
            "XianyuAutoAsync.request_public_http",
            new=AsyncMock(return_value=_response(payload={"content": "CARD-123"})),
        ) as request_mock:
            result = await self.live._get_api_card_content(
                {
                    "id": 1,
                    "card_name": "API card",
                    "api_config": {
                        "url": "https://cards.example.test/next",
                        "method": "POST",
                        "headers": {"Authorization": "Bearer card-token"},
                        "params": {"order": "{order_id}"},
                        "timeout": 90,
                    },
                },
                order_id="order-1",
            )

        self.assertEqual(result, "CARD-123")
        self.live.session.get.assert_not_called()
        self.live.session.post.assert_not_called()
        kwargs = request_mock.call_args.kwargs
        self.assertEqual(kwargs["json_body"], {"order": "order-1"})
        self.assertEqual(kwargs["allowed_methods"], ("GET", "POST"))
        self.assertTrue(kwargs["require_https"])

    async def test_api_card_private_target_is_rejected(self):
        result = await self.live._get_api_card_content(
            {
                "id": 1,
                "card_name": "private",
                "api_config": {
                    "url": "http://169.254.169.254/latest/meta-data",
                    "method": "GET",
                    "headers": {},
                    "params": {},
                },
            }
        )
        self.assertIsNone(result)
        self.live.session.get.assert_not_called()
        self.live.session.post.assert_not_called()

    async def test_api_card_dangerous_host_header_is_rejected(self):
        result = await self.live._get_api_card_content(
            {
                "id": 1,
                "card_name": "headers",
                "api_config": {
                    "url": "https://8.8.8.8/card",
                    "method": "GET",
                    "headers": {"Host": "127.0.0.1"},
                    "params": {},
                },
            }
        )
        self.assertIsNone(result)
        self.live.session.get.assert_not_called()
        self.live.session.post.assert_not_called()


class RuntimeNotificationOutboundTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.live = object.__new__(XianyuLive)
        self.live.cookie_id = "account-test"
        self.live.browser_user_agent = "Test Browser"

    async def test_webhook_uses_guarded_request_and_rejects_host_override(self):
        with patch(
            "XianyuAutoAsync.request_public_http",
            new=AsyncMock(return_value=_response(status=204, body=b"")),
        ) as request_mock:
            sent = await self.live._send_webhook_notification(
                {
                    "webhook_url": "https://hooks.example.test/event",
                    "http_method": "POST",
                    "headers": json.dumps({"X-Api-Key": "key"}),
                },
                "message",
            )
        self.assertTrue(sent)
        self.assertEqual(request_mock.call_args.kwargs["headers"]["X-Api-Key"], "key")
        self.assertTrue(request_mock.call_args.kwargs["require_https"])

        rejected = await self.live._send_webhook_notification(
            {
                "webhook_url": "https://8.8.8.8/event",
                "http_method": "POST",
                "headers": json.dumps({"Host": "127.0.0.1"}),
            },
            "message",
        )
        self.assertFalse(rejected)

    async def test_secret_bearing_notification_targets_reject_plaintext_http(self):
        cases = (
            (
                "dingtalk",
                self.live._send_dingtalk_notification,
                {"webhook_url": "http://8.8.8.8/hook", "secret": "signing-secret"},
            ),
            (
                "feishu",
                self.live._send_feishu_notification,
                {"webhook_url": "http://8.8.8.8/hook", "secret": "signing-secret"},
            ),
            (
                "bark",
                self.live._send_bark_notification,
                {"server_url": "http://8.8.8.8", "device_key": "device-key"},
            ),
            (
                "webhook",
                self.live._send_webhook_notification,
                {"webhook_url": "http://8.8.8.8/hook", "http_method": "POST"},
            ),
            (
                "wechat",
                self.live._send_wechat_notification,
                {"webhook_url": "http://8.8.8.8/hook"},
            ),
            (
                "telegram",
                self.live._send_telegram_notification,
                {
                    "api_base_url": "http://8.8.8.8",
                    "bot_token": "bot-token",
                    "chat_id": "chat-id",
                },
            ),
        )
        for label, sender, config in cases:
            with self.subTest(channel=label):
                self.assertFalse(await sender(config, "message"))

    async def test_email_notification_uses_public_pinned_smtp_and_tls(self):
        server = Mock()
        with patch(
            "XianyuAutoAsync.open_public_smtp",
            return_value=server,
        ) as smtp_factory:
            sent = await self.live._send_email_notification(
                {
                    "smtp_server": "smtp.example.test",
                    "smtp_port": 587,
                    "smtp_use_tls": True,
                    "email_user": "sender@example.test",
                    "email_password": "secret",
                    "recipient_email": "receiver@example.test",
                },
                "message",
            )

        self.assertTrue(sent)
        self.assertEqual(smtp_factory.call_args.args[:2], ("smtp.example.test", 587))
        server.starttls.assert_called_once()
        server.login.assert_called_once_with("sender@example.test", "secret")
        server.send_message.assert_called_once()
        server.quit.assert_called_once()

    async def test_email_notification_rejects_private_smtp_and_plaintext_login(self):
        private = await self.live._send_email_notification(
            {
                "smtp_server": "127.0.0.1",
                "smtp_port": 587,
                "smtp_use_tls": True,
                "email_user": "sender@example.test",
                "email_password": "secret",
                "recipient_email": "receiver@example.test",
            },
            "message",
        )
        plaintext = await self.live._send_email_notification(
            {
                "smtp_server": "8.8.8.8",
                "smtp_port": 25,
                "smtp_use_tls": False,
                "email_user": "sender@example.test",
                "email_password": "secret",
                "recipient_email": "receiver@example.test",
            },
            "message",
        )
        self.assertFalse(private)
        self.assertFalse(plaintext)

    async def test_remote_image_dimensions_use_public_guard(self):
        image_buffer = io.BytesIO()
        Image.new("RGB", (32, 24), color=(10, 20, 30)).save(image_buffer, format="PNG")
        with patch(
            "XianyuAutoAsync.request_public_http",
            new=AsyncMock(return_value=PublicHTTPResponse(
                status=200,
                headers={"Content-Type": "image/png"},
                body=image_buffer.getvalue(),
            )),
        ) as request_mock:
            size = await self.live._get_image_size_from_url(
                "https://images.example.test/reply.png"
            )

        self.assertEqual(size, (32, 24))
        self.assertEqual(request_mock.call_args.kwargs["allowed_methods"], ("GET",))
        self.assertNotIn("require_https", request_mock.call_args.kwargs)

        private_size = await self.live._get_image_size_from_url(
            "http://127.0.0.1/private.png"
        )
        self.assertEqual(private_size, (None, None))

    def test_cdn_detection_uses_hostname_boundaries(self):
        self.assertTrue(self.live._is_cdn_url("https://img.alicdn.com/image.jpg"))
        self.assertFalse(self.live._is_cdn_url(
            "https://evil.example/image.jpg?next=img.alicdn.com"
        ))
