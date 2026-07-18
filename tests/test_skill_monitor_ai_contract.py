import asyncio
import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException

import reply_server
from skill_monitor_ai_contract import (
    SkillMonitorAIError,
    evaluate_skill_monitor_ai_decision,
    parse_skill_monitor_ai_decision,
)


class SkillMonitorAIContractTests(unittest.IsolatedAsyncioTestCase):
    def test_strict_structured_output_accepts_only_exact_schema(self):
        decision = parse_skill_monitor_ai_decision(
            '{"recommended":true,"score":85,"reason":"synthetic reason"}'
        )
        self.assertEqual(decision.public_dict(), {
            "recommended": True,
            "score": 85,
            "reason": "synthetic reason",
        })

        invalid_values = (
            "I recommend it",
            '```json\n{"recommended":true,"score":80,"reason":"ok"}\n```',
            '{"recommended":"true","score":80,"reason":"ok"}',
            '{"recommended":true,"score":80,"reason":"ok","action":"send"}',
            '{"recommended":true,"score":80.5,"reason":"ok"}',
        )
        for raw in invalid_values:
            with self.subTest(raw=raw):
                with self.assertRaises(SkillMonitorAIError):
                    parse_skill_monitor_ai_decision(raw)

    async def test_provider_timeout_and_refusal_fail_closed(self):
        async def slow_provider():
            await asyncio.sleep(0.05)
            return '{"recommended":true,"score":90,"reason":"late"}'

        with self.assertRaises(SkillMonitorAIError) as timeout:
            await evaluate_skill_monitor_ai_decision(
                slow_provider,
                lambda: True,
                timeout_seconds=0.001,
            )
        self.assertEqual(timeout.exception.code, "ai_timeout")

        async def refusal():
            return "I cannot provide that decision"

        with self.assertRaises(SkillMonitorAIError) as non_json:
            await evaluate_skill_monitor_ai_decision(refusal, lambda: True)
        self.assertEqual(non_json.exception.code, "ai_non_json")

    async def test_lease_is_required_before_and_after_provider(self):
        provider = Mock()

        async def provider_call():
            provider()
            return '{"recommended":false,"score":20,"reason":"synthetic"}'

        with self.assertRaises(SkillMonitorAIError) as before:
            await evaluate_skill_monitor_ai_decision(provider_call, lambda: False)
        self.assertEqual(before.exception.code, "ai_lease_lost")
        provider.assert_not_called()

        lease_states = iter((True, False))
        with self.assertRaises(SkillMonitorAIError) as after:
            await evaluate_skill_monitor_ai_decision(
                provider_call,
                lambda: next(lease_states),
            )
        self.assertEqual(after.exception.code, "ai_lease_lost")
        provider.assert_called_once()

    async def test_valid_provider_output_survives_lease_checks(self):
        async def provider_call():
            return '{"recommended":true,"score":75,"reason":"synthetic"}'

        decision = await evaluate_skill_monitor_ai_decision(
            provider_call,
            lambda: True,
        )
        self.assertTrue(decision.recommended)
        self.assertEqual(decision.score, 75)


class SkillMonitorAIReplyServerIntegrationTests(unittest.TestCase):
    def test_non_json_provider_text_cannot_become_an_actionable_decision(self):
        with (
            patch.object(
                reply_server,
                "_user_ai_cookie_settings",
                return_value=("synthetic-account", {"model_name": "synthetic-model"}),
            ),
            patch.object(
                reply_server.ai_reply_engine,
                "_create_openai_client",
                return_value=object(),
            ),
            patch.object(
                reply_server.ai_reply_engine,
                "_call_openai_api",
                return_value="推荐，值得购买",
            ),
        ):
            with self.assertRaises(HTTPException) as raised:
                reply_server._run_skill_ai_filter(
                    {"title": "synthetic"},
                    {"ai_filter": "synthetic filter"},
                    7,
                )
        self.assertEqual(raised.exception.status_code, 502)

    def test_expired_run_lease_stops_before_provider_call(self):
        provider = Mock(return_value="unused")
        with (
            patch.object(
                reply_server,
                "_user_ai_cookie_settings",
                return_value=("synthetic-account", {"model_name": "synthetic-model"}),
            ),
            patch.object(
                reply_server.ai_reply_engine,
                "_create_openai_client",
                return_value=object(),
            ),
            patch.object(
                reply_server.ai_reply_engine,
                "_call_openai_api",
                provider,
            ),
            patch.object(
                reply_server.db_manager,
                "skill_monitor_run_claim_is_current",
                return_value=False,
            ),
        ):
            with self.assertRaises(HTTPException) as raised:
                reply_server._run_skill_ai_filter(
                    {"title": "synthetic"},
                    {"ai_filter": "synthetic filter"},
                    7,
                    run_id=41,
                    claim_token="synthetic-claim",
                )
        self.assertEqual(raised.exception.status_code, 409)
        provider.assert_not_called()


class SkillMonitorAIBoundedRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_bounded_runtime_maps_timeout_and_discards_provider_text(self):
        async def synthetic_timeout(*_args, **_kwargs):
            raise SkillMonitorAIError("ai_timeout", "AI Provider 调用超时")

        with (
            patch.object(
                reply_server,
                "_prepare_skill_ai_filter_request",
                return_value=(object(), {"model_name": "synthetic"}, []),
            ),
            patch(
                "skill_monitor_ai_contract.evaluate_skill_monitor_ai_decision",
                new=synthetic_timeout,
            ),
        ):
            with self.assertRaises(HTTPException) as raised:
                await reply_server._run_skill_ai_filter_bounded(
                    {"title": "synthetic"},
                    {"ai_filter": "synthetic filter"},
                    7,
                    run_id=41,
                    claim_token="synthetic-claim",
                    timeout_seconds=0.01,
                )
        self.assertEqual(raised.exception.status_code, 504)


if __name__ == "__main__":
    unittest.main()
