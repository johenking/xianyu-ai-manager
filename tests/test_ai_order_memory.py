import asyncio
import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

import XianyuAutoAsync as live_module
import ai_reply_engine as ai_module
from XianyuAutoAsync import XianyuLive
from ai_reply_engine import AIReplyEngine
from db_manager import DBManager
from schema_migrations import _ai_order_scoped_conversations_v1


class AIOrderMemoryTests(unittest.TestCase):
    def setUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.db = DBManager(self.db_path)
        admin = self.db.get_user_by_username("admin")
        self.assertTrue(
            self.db.save_cookie(
                "account-1",
                "unb=account-1; cookie2=fixture-session",
                admin["id"],
            )
        )
        self.original_db = ai_module.db_manager
        ai_module.db_manager = self.db
        self.engine = AIReplyEngine()

    def tearDown(self):
        ai_module.db_manager = self.original_db
        self.db.conn.close()
        os.unlink(self.db_path)

    def _insert_order(self, order_id, buyer_id="buyer-1", chat_id="chat-1"):
        with self.db.lock:
            self.db.conn.execute(
                """
                INSERT INTO orders (
                    order_id, cookie_id, item_id, buyer_id, chat_id,
                    order_status, quantity, paid_amount_fen, receiver_name
                ) VALUES (?, 'account-1', 'item-1', ?, ?, 'pending_ship', '1', 9900, 'private')
                """,
                (order_id, buyer_id, chat_id),
            )
            self.db.conn.commit()

    def test_scope_requires_owner_and_never_uses_polluted_conversation(self):
        self.engine.save_conversation(
            "chat-1", "account-1", "buyer-1", "item-1", "buyer", "polluted",
            order_id="order-a", order_scope="exact", source="buyer",
            delivery_state="received",
        )
        polluted_only = self.engine.resolve_order_scope(
            "chat-1", "account-1", "item-1", user_id="buyer-1"
        )
        self._insert_order("order-a")

        wrong = self.engine.resolve_order_scope(
            "chat-1", "account-1", "item-1", "order-a", user_id="other-buyer"
        )
        right = self.engine.resolve_order_scope(
            "chat-1", "account-1", "item-1", "order-a", user_id="buyer-1"
        )

        self.assertEqual(polluted_only["scope"], "none")
        self.assertEqual(wrong["scope"], "none")
        self.assertEqual(right["scope"], "exact")

    def test_unique_ambiguous_and_none_scope(self):
        self.assertEqual(
            self.engine.resolve_order_scope(
                "chat-1", "account-1", "item-1", user_id="buyer-1"
            )["scope"],
            "none",
        )
        self._insert_order("order-a")
        unique = self.engine.resolve_order_scope(
            "chat-1", "account-1", "item-1", user_id="buyer-1"
        )
        self._insert_order("order-b")
        ambiguous = self.engine.resolve_order_scope(
            "chat-1", "account-1", "item-1", user_id="buyer-1"
        )

        self.assertEqual((unique["scope"], unique["order_id"]), ("unique", "order-a"))
        self.assertEqual(ambiguous["scope"], "ambiguous")
        self.assertEqual(ambiguous["order_id"], "")

    def test_order_history_isolated_and_drafts_are_untrusted(self):
        for order_id, content in (("order-a", "history-a"), ("order-b", "history-b")):
            self.engine.save_conversation(
                "chat-1", "account-1", "buyer-1", "item-1", "buyer", content,
                order_id=order_id, order_scope="exact", source="buyer",
                delivery_state="received",
            )
        self.engine.save_conversation(
            "chat-1", "account-1", "buyer-1", "item-1", "assistant", "draft-a",
            order_id="order-a", order_scope="exact", source="assistant_generated",
            delivery_state="draft",
        )
        self.engine.save_conversation(
            "chat-1", "account-1", "buyer-1", "item-1", "assistant", "sent-a",
            order_id="order-a", order_scope="exact", source="assistant_generated",
            delivery_state="succeeded",
        )

        context = self.engine.get_conversation_context(
            "chat-1", "account-1", "item-1", order_id="order-a",
            order_scope="exact", include_metadata=True, trusted_only=True,
        )
        text = " ".join(row["content"] for row in context)

        self.assertIn("history-a", text)
        self.assertIn("sent-a", text)
        self.assertNotIn("history-b", text)
        self.assertNotIn("draft-a", text)

    def test_current_question_removed_from_history_everywhere(self):
        context = [
            {"role": "buyer", "content": "same question"},
            {"role": "assistant", "content": "older answer"},
            {"role": "user", "content": "  same   question  "},
        ]

        result = self.engine._drop_current_message_from_context(context, "same question")

        self.assertEqual(result, [{"role": "assistant", "content": "older answer"}])

    def test_recent_limit_restores_stable_chronological_order(self):
        for index in range(3):
            self.db.insert_ai_conversation(
                "account-1", "chat-1", "buyer-1", "item-1", "buyer", f"m{index}",
                order_id="order-a", source="buyer", delivery_state="received",
            )
        rows = self.db.get_ai_conversations(
            "account-1", "chat-1", "item-1", order_id="order-a", limit=2
        )

        self.assertEqual([row["content"] for row in rows], ["m1", "m2"])
        self.assertEqual([row["id"] for row in rows], sorted(row["id"] for row in rows))

    def test_human_message_and_verified_order_summary_are_scoped(self):
        self._insert_order("order-a")
        self.engine.save_conversation(
            "chat-1", "account-1", "seller-1", "item-1", "seller_human", "human example",
            order_id="order-a", order_scope="exact", source="seller_human",
            delivery_state="succeeded",
        )
        summary = self.engine._get_verified_order_summary(
            "exact", "order-a", "account-1", "item-1", "buyer-1"
        )
        context = self.engine.get_conversation_context(
            "chat-1", "account-1", "item-1", order_id="order-a",
            order_scope="exact", include_metadata=True, trusted_only=True,
        )

        self.assertEqual(context[0]["source"], "seller_human")
        self.assertIn('"order_status": "pending_ship"', summary)
        self.assertNotIn("private", summary)
        self.assertNotIn("receiver", summary)

    def test_migration_is_idempotent_and_preserves_legacy_rows(self):
        connection = sqlite3.connect(":memory:")
        connection.execute(
            """
            CREATE TABLE ai_conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cookie_id TEXT NOT NULL, chat_id TEXT NOT NULL,
                user_id TEXT NOT NULL, item_id TEXT NOT NULL,
                role TEXT NOT NULL, content TEXT NOT NULL,
                intent TEXT, bargain_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            "INSERT INTO ai_conversations (cookie_id, chat_id, user_id, item_id, role, content) "
            "VALUES ('a', 'c', 'u', 'i', 'user', 'legacy row')"
        )

        for _ in range(2):
            _ai_order_scoped_conversations_v1(connection.cursor(), ":memory:")
            connection.commit()

        columns = {row[1] for row in connection.execute("PRAGMA table_info(ai_conversations)")}
        row = connection.execute(
            "SELECT content, order_id, source, delivery_state FROM ai_conversations"
        ).fetchone()
        indexes = {
            value[0]
            for value in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND name LIKE 'idx_ai_conversations_%'"
            )
        }
        connection.close()

        self.assertTrue({"order_id", "source", "delivery_state"} <= columns)
        self.assertEqual(row, ("legacy row", None, "legacy", "legacy"))
        self.assertIn("idx_ai_conversations_order_scope", indexes)

    def test_formal_reply_never_enters_shadow_resolution(self):
        with (
            patch.object(self.engine, "_generate_reply_legacy", return_value="legacy") as legacy,
            patch.object(self.engine, "resolve_order_scope") as resolver,
        ):
            reply = self.engine.generate_reply(
                "question", {}, "chat-1", "account-1", "buyer-1", "item-1",
                skip_wait=True, order_id="wrong-order", order_scope="none",
            )

        self.assertEqual(reply, "legacy")
        legacy.assert_called_once()
        resolver.assert_not_called()

    def test_shadow_model_call_budget_is_two(self):
        calls = []
        responses = [
            "candidate",
            json.dumps({
                "results": [{"rule_id": 1, "status": "violated", "reason": "fixture"}],
                "conflicts": [],
            }),
        ]

        def fake_call(*_args, **_kwargs):
            self.engine._record_model_call()
            calls.append(1)
            return responses.pop(0)

        self.engine._reset_model_call_count()
        self.engine._set_model_call_limit(2)
        try:
            with patch.object(self.engine, "_call_configured_model", side_effect=fake_call):
                result = self.engine.generate_rule_checked_reply(
                    settings={}, cookie_id="account-1",
                    messages=[{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
                    buyer_message="u", rules=[{"id": 1, "text": "reply briefly"}],
                    knowledge_text="", max_tokens=20, temperature=0.1,
                )
        finally:
            self.engine._clear_model_call_limit()

        self.assertEqual(len(calls), 2)
        self.assertFalse(result["regenerated"])


class AIOrderMemoryAsyncTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _live():
        live = object.__new__(XianyuLive)
        live.cookie_id = "account-1"
        live.myid = "seller-1"
        return live

    async def test_formal_call_shape_does_not_include_order_scope(self):
        live = self._live()
        generate = AsyncMock(return_value="legacy reply")
        with (
            patch.object(ai_module.ai_reply_engine, "is_ai_enabled", return_value=True),
            patch.object(ai_module.ai_reply_engine, "generate_reply_async", generate),
            patch.object(
                live_module.db_manager,
                "get_item_info",
                return_value={"item_title": "item", "item_price": "9.9", "item_detail": "desc"},
            ),
        ):
            reply = await live.get_ai_reply(
                "buyer", "buyer-1", "question", "item-1", "chat-1",
                order_id="order-a", order_scope="exact",
            )

        self.assertEqual(reply, "legacy reply")
        kwargs = generate.await_args.kwargs
        self.assertNotIn("order_id", kwargs)
        self.assertNotIn("order_scope", kwargs)
        self.assertNotIn("shadow", kwargs)

    async def test_shadow_timeout_keeps_concurrency_gate_until_provider_finishes(self):
        live = self._live()
        second_live = self._live()
        tasks = []
        started = asyncio.Event()
        release = asyncio.Event()

        def track(coroutine):
            task = asyncio.create_task(coroutine)
            tasks.append(task)
            return task

        async def slow_provider(**_kwargs):
            started.set()
            await release.wait()
            return "candidate"

        for target in (live, second_live):
            target._create_tracked_task = track
            target._resolve_ai_order_context = AsyncMock(return_value=("order-a", "exact"))
            target._ai_item_info = Mock(
                return_value={"title": "item", "price": 1, "desc": "desc"}
            )
        shadow = AsyncMock(side_effect=slow_provider)
        save = Mock(return_value="2026-08-18 00:00:00")

        with (
            patch.object(live_module, "AI_REPLY_SHADOW_ENABLED", True),
            patch.object(live_module, "AI_REPLY_SHADOW_TIMEOUT_SECONDS", 0.01),
            patch.object(live_module.db_manager, "get_item_info", return_value={}),
            patch.object(ai_module.ai_reply_engine, "generate_shadow_reply_async", shadow),
            patch.object(ai_module.ai_reply_engine, "save_conversation", save),
        ):
            live._schedule_ai_shadow_reply(
                "buyer-1", "question", "item-1", "chat-1",
                sent_reply="legacy reply", reply_source="AI",
            )
            await asyncio.wait_for(started.wait(), 1)
            await asyncio.sleep(0.03)
            self.assertTrue(live._ai_reply_shadow_semaphore.locked())

            second_live._schedule_ai_shadow_reply(
                "buyer-1", "second", "item-1", "chat-1",
                sent_reply="keyword reply", reply_source="关键词",
            )
            await asyncio.sleep(0.02)
            self.assertEqual(shadow.await_count, 1)
            self.assertIs(
                live._ai_reply_shadow_semaphore,
                second_live._ai_reply_shadow_semaphore,
            )

            release.set()
            await asyncio.sleep(0.03)
            await asyncio.gather(*tasks, return_exceptions=True)

        self.assertFalse(live._ai_reply_shadow_semaphore.locked())
        outbound = [call for call in save.call_args_list if call.kwargs.get("role") == "assistant"]
        self.assertEqual(
            {call.kwargs["content"] for call in outbound},
            {"legacy reply", "keyword reply"},
        )
        self.assertTrue(all(call.kwargs["delivery_state"] == "ambiguous" for call in outbound))

    async def test_shadow_does_not_block_formal_chat_lock(self):
        engine = AIReplyEngine()
        lock = engine._get_chat_lock("chat-1")
        lock.acquire()
        try:
            with (
                patch.object(engine, "is_ai_enabled", return_value=True),
                patch.object(engine, "resolve_order_scope", return_value={"scope": "legacy"}),
                patch.object(engine, "detect_intent", return_value="default"),
                patch.object(engine, "_get_recent_user_messages", return_value=[]),
                patch.object(engine, "get_conversation_context", return_value=[]),
                patch.object(engine, "get_bargain_count", return_value=0),
                patch.object(
                    engine,
                    "build_product_reply_context",
                    return_value={
                        "system_prompt": "system",
                        "rule_context": {"applied_rules": []},
                        "knowledge_text": "",
                    },
                ),
                patch.object(
                    engine,
                    "generate_rule_checked_reply",
                    return_value={"reply": "candidate", "regenerated": False},
                ),
            ):
                task = asyncio.create_task(
                    asyncio.to_thread(
                        engine.generate_reply,
                        message="question",
                        item_info={"title": "item", "price": 1, "desc": "desc"},
                        chat_id="chat-1",
                        cookie_id="account-1",
                        user_id="buyer-1",
                        item_id="item-1",
                        skip_wait=True,
                        shadow=True,
                    )
                )
                result = await asyncio.wait_for(asyncio.shield(task), timeout=0.5)
                self.assertEqual(result, "candidate")
        finally:
            lock.release()

    async def test_observed_human_reply_is_saved_only_when_order_is_unique(self):
        live = self._live()
        live._resolve_ai_order_context = AsyncMock(return_value=("order-a", "unique"))
        save = Mock(return_value="2026-08-18 00:00:00")
        with patch.object(ai_module.ai_reply_engine, "save_conversation", save):
            await live._record_seller_human_message("chat-1", "item-1", "human reply")

        self.assertEqual(save.call_args.kwargs["source"], "seller_human")
        self.assertEqual(save.call_args.kwargs["delivery_state"], "succeeded")
        self.assertEqual(save.call_args.kwargs["order_id"], "order-a")

    async def test_ai_send_failures_mark_draft_ambiguous(self):
        for reply, image_failure in (
            ("text reply", False),
            ("__IMAGE_SEND__https://example.com/image.png", True),
        ):
            with self.subTest(image_failure=image_failure):
                live = self._live()
                live.last_ai_result = "generated"
                live.get_keyword_reply = AsyncMock(return_value=None)
                live.get_ai_reply = AsyncMock(return_value=reply)
                live.get_default_reply = AsyncMock(return_value=None)
                live._mark_ai_reply_delivery = AsyncMock()
                live._schedule_ai_shadow_reply = Mock()
                live.send_msg = AsyncMock(
                    return_value=True,
                    side_effect=None if image_failure else ConnectionError("send failed"),
                )
                live.send_image_msg = AsyncMock(
                    side_effect=ConnectionError("image failed") if image_failure else None,
                )

                with (
                    patch.object(live_module, "AUTO_REPLY", {"enabled": True}),
                    patch.object(live_module.pause_manager, "is_chat_paused", return_value=False),
                ):
                    await live._process_chat_message_reply(
                        {}, object(), "buyer", "buyer-1", "question",
                        "item-1", "chat-1", "now", order_id="order-a",
                    )

                live._mark_ai_reply_delivery.assert_awaited()
                self.assertEqual(
                    live._mark_ai_reply_delivery.await_args.args,
                    ("chat-1", "item-1", reply, "ambiguous"),
                )
                live._schedule_ai_shadow_reply.assert_called_once()


if __name__ == "__main__":
    unittest.main()
