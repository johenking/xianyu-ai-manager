import base64
from io import BytesIO
import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

from PIL import Image

from ai_reply_engine import AIReplyEngine
from XianyuAutoAsync import XianyuLive
from utils.outbound_http import PublicHTTPResponse
from utils.xianyu_message import (
    IMAGE_PLACEHOLDER,
    ImageReference,
    extract_inbound_content,
    message_has_content,
    normalize_operation_message,
)


def _png_bytes() -> bytes:
    output = BytesIO()
    Image.new('RGB', (2, 3), 'white').save(output, format='PNG')
    return output.getvalue()


class XianyuMessageImageTests(unittest.TestCase):
    def test_custom_wrapped_image_is_extracted(self):
        inner = {
            'contentType': 2,
            'image': {
                'pics': [{
                    'url': 'https://gw.alicdn.com/test/image.png',
                    'width': 640,
                    'height': 480,
                }],
            },
        }
        wrapper = {
            'contentType': 101,
            'custom': {
                'data': base64.b64encode(json.dumps(inner).encode()).decode(),
            },
        }
        message = {
            '1': {
                '6': {'3': {'5': json.dumps(wrapper)}},
                '10': {'senderUserId': 'buyer-1', 'reminderContent': ''},
            },
        }

        content = extract_inbound_content(message)

        self.assertEqual(content.content_type, 2)
        self.assertEqual(content.images, (
            ImageReference('https://gw.alicdn.com/test/image.png', 640, 480),
        ))
        self.assertTrue(message_has_content(message))

    def test_operation_image_without_chat_type_is_normalized(self):
        payload = {
            'sessionId': 'chat-1',
            'timestamp': 1786860000000,
            'operation': {
                'senderInfo': {'senderUserId': 'buyer-1'},
                'content': {
                    'contentType': 2,
                    'image': {
                        'pics': [{
                            'url': 'https://img.alicdn.com/test/photo.jpg',
                            'width': 800,
                            'height': 600,
                        }],
                    },
                    'reminder': {'reminderTitle': '买家'},
                },
            },
        }

        normalized = normalize_operation_message(payload)

        self.assertIsNotNone(normalized)
        self.assertEqual(normalized['1']['2'], 'chat-1@goofish')
        self.assertEqual(normalized['1']['10']['reminderContent'], IMAGE_PLACEHOLDER)
        self.assertEqual(extract_inbound_content(normalized).images[0].width, 800)


class AIImageRequestTests(unittest.TestCase):
    def setUp(self):
        self.engine = AIReplyEngine()
        self.settings = {
            'provider_type': 'openai_compatible',
            'model_name': 'vision-model',
            'base_url': 'https://relay.example.test/v1',
            'api_key': 'test-key',
        }

    def test_cdn_image_is_verified_and_embedded(self):
        response = PublicHTTPResponse(
            status=200,
            headers={'Content-Type': 'image/png'},
            body=_png_bytes(),
        )
        with patch('ai_reply_engine.request_public_http_sync', return_value=response) as request_mock:
            parts = self.engine._prepare_image_parts(
                self.settings,
                [ImageReference('https://gw.alicdn.com/test/image.png')],
            )

        self.assertEqual(parts[0]['type'], 'image_url')
        self.assertTrue(parts[0]['image_url']['url'].startswith('data:image/png;base64,'))
        self.assertNotIn('gw.alicdn.com', parts[0]['image_url']['url'])
        self.assertEqual(request_mock.call_args.args[:2], ('GET', 'https://gw.alicdn.com/test/image.png'))
        self.assertTrue(request_mock.call_args.kwargs['require_https'])

    def test_non_cdn_image_is_rejected_before_download(self):
        with (
            patch('ai_reply_engine.request_public_http_sync') as request_mock,
            self.assertRaises(ValueError),
        ):
            self.engine._prepare_image_parts(
                self.settings,
                [{'url': 'https://example.test/image.png'}],
            )
        request_mock.assert_not_called()

    def test_image_failure_degrades_to_guidance_reply_on_order_aware_path(self):
        """图片校验失败时主路径降级为无图：纯图片消息返回固定引导而非静默失败。"""
        checked_reply = Mock(return_value={'reply': '不应走到模型', 'regenerated': False})
        save_record = Mock(return_value={'id': 1, 'created_at': '2026-08-29 10:00:00'})
        with (
            patch.object(self.engine, 'order_aware_enabled', return_value=True),
            patch.object(self.engine, 'is_ai_enabled', return_value=True),
            patch.object(
                self.engine, 'resolve_order_scope',
                return_value={'scope': 'legacy', 'order_id': None},
            ),
            patch.object(self.engine, 'detect_intent', return_value='default'),
            patch.object(self.engine, '_save_conversation_record', save_record),
            patch.object(self.engine, '_get_recent_user_messages', return_value=[]),
            patch.object(
                self.engine, '_prepare_image_parts',
                side_effect=ValueError('入站图片下载失败: status=403'),
            ),
            patch.object(self.engine, 'generate_rule_checked_reply', checked_reply),
            patch.object(self.engine, '_record_shadow_metric'),
            patch('ai_reply_engine.db_manager.get_ai_reply_settings', return_value=self.settings),
        ):
            result = self.engine.generate_reply(
                message=IMAGE_PLACEHOLDER,
                item_info={'title': 'item', 'price': 1, 'desc': 'desc'},
                chat_id='chat-1',
                cookie_id='account-1',
                user_id='buyer-1',
                item_id='item-1',
                skip_wait=True,
                image_refs=[ImageReference('https://gw.alicdn.com/test/broken.png')],
            )

        self.assertEqual(result, self.engine.NON_TEXT_GUIDANCE_REPLY)
        checked_reply.assert_not_called()
        saved_replies = [
            call.args for call in save_record.call_args_list
            if len(call.args) >= 6 and call.args[4] == 'assistant'
        ]
        self.assertEqual(len(saved_replies), 1)
        self.assertEqual(saved_replies[0][5], self.engine.NON_TEXT_GUIDANCE_REPLY)

    def test_image_failure_degrades_to_text_only_generation_on_legacy_path(self):
        """图片校验失败时 legacy 路径同样降级为无图生成，不再让整次回复失败。"""
        checked_reply = Mock(return_value={'reply': '还在的哦', 'regenerated': False})
        with (
            patch.object(self.engine, 'order_aware_enabled', return_value=False),
            patch.object(self.engine, 'is_ai_enabled', return_value=True),
            patch.object(self.engine, 'detect_intent', return_value='default'),
            patch.object(self.engine, 'save_conversation', return_value='created'),
            patch.object(self.engine, '_get_recent_user_messages', return_value=[]),
            patch.object(self.engine, 'get_conversation_context', return_value=[]),
            patch.object(self.engine, 'get_bargain_count', return_value=0),
            patch.object(
                self.engine, '_prepare_image_parts',
                side_effect=ValueError('入站图片超出尺寸限制'),
            ),
            patch.object(self.engine, 'build_product_reply_context', return_value={
                'system_prompt': 'system',
                'rule_context': {'applied_rules': []},
                'knowledge_text': '',
            }),
            patch.object(self.engine, 'generate_rule_checked_reply', checked_reply),
            patch('ai_reply_engine.db_manager.get_ai_reply_settings', return_value=self.settings),
        ):
            result = self.engine.generate_reply(
                message=f'这个还在吗{IMAGE_PLACEHOLDER}',
                item_info={'title': 'item', 'price': 1, 'desc': 'desc'},
                chat_id='chat-1',
                cookie_id='account-1',
                user_id='buyer-1',
                item_id='item-1',
                skip_wait=True,
                image_refs=[ImageReference('https://gw.alicdn.com/test/broken.png')],
            )

        self.assertEqual(result, '还在的哦')
        user_content = checked_reply.call_args.kwargs['messages'][1]['content']
        self.assertIsInstance(user_content, str)

    def test_generate_reply_builds_multimodal_user_content(self):
        image_part = {
            'type': 'image_url',
            'image_url': {'url': 'data:image/png;base64,AAAA'},
        }
        checked_reply = Mock(return_value={'reply': '看到了', 'regenerated': False})
        with (
            patch.object(self.engine, 'is_ai_enabled', return_value=True),
            patch.object(self.engine, 'detect_intent', return_value='default'),
            patch.object(self.engine, 'save_conversation', return_value='created'),
            patch.object(self.engine, '_get_recent_user_messages', return_value=[]),
            patch.object(self.engine, 'get_conversation_context', return_value=[]),
            patch.object(self.engine, 'get_bargain_count', return_value=0),
            patch.object(self.engine, '_prepare_image_parts', return_value=[image_part]),
            patch.object(self.engine, 'build_product_reply_context', return_value={
                'system_prompt': 'system',
                'rule_context': {'applied_rules': []},
                'knowledge_text': '',
            }),
            patch.object(self.engine, 'generate_rule_checked_reply', checked_reply),
            patch('ai_reply_engine.db_manager.get_ai_reply_settings', return_value=self.settings),
        ):
            result = self.engine.generate_reply(
                message=IMAGE_PLACEHOLDER,
                item_info={'title': 'item', 'price': 1, 'desc': 'desc'},
                chat_id='chat-1',
                cookie_id='account-1',
                user_id='buyer-1',
                item_id='item-1',
                skip_wait=True,
                image_refs=[ImageReference('https://gw.alicdn.com/test/image.png')],
            )

        user_content = checked_reply.call_args.kwargs['messages'][1]['content']
        self.assertEqual(result, '看到了')
        self.assertEqual(user_content[-1], image_part)
        self.assertEqual(user_content[0]['type'], 'text')


class LiveImageForwardingTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_forwards_images_to_async_ai(self):
        live = object.__new__(XianyuLive)
        live.cookie_id = 'account-1'
        engine = Mock()
        engine.is_ai_enabled.return_value = True
        engine.generate_reply_async = AsyncMock(return_value='看到了')
        image_refs = (ImageReference('https://gw.alicdn.com/test/image.png'),)

        with (
            patch('ai_reply_engine.ai_reply_engine', engine),
            patch('db_manager.db_manager.get_item_info', return_value=None),
        ):
            result = await live.get_ai_reply(
                'buyer', 'buyer-1', IMAGE_PLACEHOLDER, 'item-1', 'chat-1',
                image_refs=image_refs,
            )

        self.assertEqual(result, '看到了')
        self.assertEqual(
            engine.generate_reply_async.call_args.kwargs['image_refs'],
            image_refs,
        )


if __name__ == '__main__':
    unittest.main()
