import os
import json
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from ai_provider_service import (
    ProviderTestTokenStore,
    discover_provider_models,
    extract_gemini_models,
    extract_openai_models,
    test_provider_reply as run_provider_reply_test,
)
from ai_reply_engine import AIReplyEngine
from db_manager import DBManager
from XianyuAutoAsync import XianyuLive
import reply_server
from utils.outbound_http import OutboundRequestError, PublicHTTPResponse


def _response(payload, status=200):
    return PublicHTTPResponse(
        status=status,
        headers={"Content-Type": "application/json"},
        body=json.dumps(payload).encode("utf-8"),
    )


class AIProviderDatabaseTests(unittest.TestCase):
    def setUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.db = DBManager(self.db_path)
        with self.db.lock:
            self.db.conn.execute(
                "INSERT OR IGNORE INTO users (id, username, email, password_hash) VALUES (2, 'other', 'other@example.com', 'x')"
            )
            self.db.conn.execute(
                "INSERT INTO cookies (id, value, user_id, remark) VALUES ('account-1', 'cookie-value', 1, '主账号')"
            )
            self.db.conn.commit()

    def tearDown(self):
        self.db.conn.close()
        os.unlink(self.db_path)

    def test_provider_profile_crud_is_user_scoped_and_key_is_masked(self):
        profile_id = self.db.create_ai_provider_profile(1, {
            "name": "OpenRouter",
            "provider_type": "openai_compatible",
            "preset": "openrouter",
            "base_url": "https://openrouter.ai/api/v1",
            "api_key": "sk-secret-value",
            "default_model": "openai/gpt-4.1-mini",
        })

        public_profile = self.db.get_ai_provider_profile(profile_id, 1)
        private_profile = self.db.get_ai_provider_profile(profile_id, 1, include_secret=True)

        self.assertNotIn("sk-secret-value", str(public_profile))
        self.assertTrue(public_profile["api_key_configured"])
        self.assertTrue(public_profile["api_key_masked"].endswith("alue"))
        self.assertEqual(private_profile["api_key"], "sk-secret-value")
        self.assertIsNone(self.db.get_ai_provider_profile(profile_id, 2))
        self.assertEqual(self.db.list_ai_provider_profiles(2), [])

    def test_referenced_provider_cannot_be_deleted(self):
        profile_id = self.db.create_ai_provider_profile(1, {
            "name": "DeepSeek",
            "provider_type": "openai_compatible",
            "preset": "deepseek",
            "base_url": "https://api.deepseek.com",
            "api_key": "sk-existing",
            "default_model": "deepseek-v4-flash",
        })
        self.db.save_ai_reply_settings("account-1", {
            "ai_enabled": True,
            "provider_profile_id": profile_id,
            "model_name": "deepseek-v4-flash",
        })

        with self.assertRaisesRegex(ValueError, "正在被账号使用"):
            self.db.delete_ai_provider_profile(profile_id, 1)

    def test_legacy_migration_preserves_effective_account_configuration(self):
        self.db.set_system_setting("ai_api_key", "sk-legacy")
        self.db.set_system_setting("ai_api_url", "https://api.deepseek.com")
        self.db.set_system_setting("ai_model", "deepseek-v4-flash")
        self.db.save_ai_reply_settings("account-1", {
            "ai_enabled": True,
            "model_name": "deepseek-v4-flash",
            "api_key": "",
            "base_url": "https://api.deepseek.com",
        })
        before = self.db.get_ai_reply_settings("account-1")

        migrated = self.db.ensure_legacy_ai_provider_profiles(1)
        after = self.db.get_ai_reply_settings("account-1")

        self.assertEqual(migrated, 1)
        self.assertIsNotNone(after["provider_profile_id"])
        self.assertEqual(after["provider_type"], "openai_compatible")
        self.assertEqual(after["api_key"], before["api_key"])
        self.assertEqual(after["base_url"], before["base_url"])
        self.assertEqual(after["model_name"], before["model_name"])


class AIProviderServiceTests(unittest.TestCase):
    def test_openai_model_list_is_normalized_and_sorted(self):
        result = extract_openai_models({"data": [{"id": "z-model"}, {"id": "a-model"}, {"id": "a-model"}]})
        self.assertEqual(result, ["a-model", "z-model"])

    def test_gemini_model_list_keeps_only_generate_content_models(self):
        result = extract_gemini_models({
            "models": [
                {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]},
                {"name": "models/text-embedding-004", "supportedGenerationMethods": ["embedContent"]},
            ]
        })
        self.assertEqual(result, ["gemini-2.5-flash"])

    def test_test_token_is_bound_to_user_profile_and_model(self):
        store = ProviderTestTokenStore(ttl_seconds=60)
        token = store.issue(user_id=1, profile_id=9, model_name="deepseek-chat")

        self.assertTrue(store.consume(token, user_id=1, profile_id=9, model_name="deepseek-chat"))
        self.assertFalse(store.consume(token, user_id=1, profile_id=9, model_name="deepseek-reasoner"))
        other = store.issue(user_id=1, profile_id=9, model_name="deepseek-chat")
        self.assertFalse(store.consume(other, user_id=2, profile_id=9, model_name="deepseek-chat"))

    def test_model_discovery_and_reply_test_use_guarded_public_requests(self):
        profile = {
            "provider_type": "openai_compatible",
            "preset": "custom",
            "base_url": "https://provider.example.test/v1",
            "api_key": "sk-secret",
        }
        with patch(
            "ai_provider_service.request_public_http_sync",
            side_effect=[
                _response({"data": [{"id": "model-b"}, {"id": "model-a"}]}),
                _response({"choices": [{"message": {"content": "连接成功"}}]}),
            ],
        ) as request_mock:
            models = discover_provider_models(profile)
            reply = run_provider_reply_test(profile, "model-a")

        self.assertEqual(models, ["model-a", "model-b"])
        self.assertEqual(reply, "连接成功")
        self.assertEqual(request_mock.call_count, 2)
        self.assertEqual(
            request_mock.call_args_list[0].kwargs["headers"]["Authorization"],
            "Bearer sk-secret",
        )
        self.assertTrue(
            all(call.kwargs["require_https"] for call in request_mock.call_args_list)
        )

    def test_plaintext_provider_target_is_rejected_before_resolution(self):
        with self.assertRaises(OutboundRequestError) as raised:
            discover_provider_models({
                "provider_type": "openai_compatible",
                "base_url": "http://127.0.0.1/v1",
                "api_key": "sk-secret",
            })
        self.assertEqual(raised.exception.code, "insecure_transport_denied")

    def test_provider_profile_requires_clean_https_base_url(self):
        base = {
            "name": "Custom",
            "preset": "custom",
            "provider_type": "openai_compatible",
            "default_model": "model-a",
        }
        for url in (
            "http://api.example.test/v1",
            "https://user:secret@api.example.test/v1",
            "https://api.example.test/v1?token=secret",
        ):
            with self.subTest(url=url), self.assertRaises(reply_server.HTTPException):
                reply_server._normalize_provider_payload({**base, "base_url": url})


class AIReplyOutboundTests(unittest.TestCase):
    def test_runtime_openai_compatible_call_uses_guarded_request(self):
        engine = AIReplyEngine()
        settings = {
            "model_name": "custom-model",
            "base_url": "https://provider.example.test/v1",
            "api_key": "sk-secret",
        }
        with patch(
            "ai_reply_engine.request_public_http_sync",
            return_value=_response({"choices": [{"message": {"content": "回复内容"}}]}),
        ) as request_mock:
            result = engine._call_openai_api(
                object(),
                settings,
                [{"role": "user", "content": "你好"}],
            )

        self.assertEqual(result, "回复内容")
        self.assertEqual(
            request_mock.call_args.kwargs["headers"]["Authorization"],
            "Bearer sk-secret",
        )
        self.assertTrue(request_mock.call_args.kwargs["require_https"])
        self.assertNotIn("client", request_mock.call_args.kwargs)


class LiveAIReplyTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_reply_awaits_the_async_engine_wrapper(self):
        live = object.__new__(XianyuLive)
        live.cookie_id = "account-1"
        engine = Mock()
        engine.is_ai_enabled.return_value = True
        engine.generate_reply = Mock(
            side_effect=AssertionError("synchronous AI generation entered the event loop")
        )
        engine.generate_reply_async = AsyncMock(return_value="异步回复")

        with (
            patch("ai_reply_engine.ai_reply_engine", engine),
            patch("db_manager.db_manager.get_item_info", return_value=None),
        ):
            result = await live.get_ai_reply(
                "buyer",
                "buyer-1",
                "hello",
                "item-1",
                "chat-1",
            )

        self.assertEqual(result, "异步回复")
        engine.generate_reply_async.assert_awaited_once()
        engine.generate_reply.assert_not_called()

    async def test_live_message_reply_pipeline_uses_async_ai_generation(self):
        live = object.__new__(XianyuLive)
        live.cookie_id = "account-1"
        live.get_keyword_reply = AsyncMock(return_value=None)
        live.get_default_reply = AsyncMock(return_value=None)
        live.send_msg = AsyncMock()
        live.send_image_msg = AsyncMock()

        engine = Mock()
        engine.is_ai_enabled.return_value = True
        engine.generate_reply = Mock(
            side_effect=AssertionError("synchronous AI generation entered the event loop")
        )
        engine.generate_reply_async = AsyncMock(return_value="实时异步回复")
        websocket = object()

        with (
            patch("ai_reply_engine.ai_reply_engine", engine),
            patch("db_manager.db_manager.get_item_info", return_value=None),
            patch("XianyuAutoAsync.pause_manager.is_chat_paused", return_value=False),
            patch.dict("XianyuAutoAsync.AUTO_REPLY", {"enabled": True}),
        ):
            await live._process_chat_message_reply(
                {},
                websocket,
                "buyer",
                "buyer-1",
                "hello",
                "item-1",
                "chat-1",
                "2026-07-28 11:00:00",
            )

        engine.generate_reply_async.assert_awaited_once_with(
            message="hello",
            item_info={
                "title": "商品信息获取失败",
                "price": 0,
                "desc": "暂无商品描述",
            },
            chat_id="chat-1",
            cookie_id="account-1",
            user_id="buyer-1",
            item_id="item-1",
            skip_wait=True,
        )
        engine.generate_reply.assert_not_called()
        live.get_default_reply.assert_not_awaited()
        live.send_msg.assert_awaited_once_with(
            websocket,
            "chat-1",
            "buyer-1",
            "实时异步回复",
        )


if __name__ == "__main__":
    unittest.main()
