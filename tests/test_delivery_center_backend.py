"""Delivery-center resources, bindings, and immutable fulfillment records."""

from __future__ import annotations

import os
import asyncio
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from db_manager import DBManager
import reply_server
from security_utils import SYSTEM_SECRET_PREFIX


class DeliveryCenterBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.previous_key_file = os.environ.get("SYSTEM_SECRET_KEY_FILE")
        os.environ["SYSTEM_SECRET_KEY_FILE"] = str(self.root / ".system-key")
        self.db = DBManager(str(self.root / "delivery-center.db"))
        self.assertTrue(
            self.db.create_user("seller", "seller@example.test", "Strong-pass-2026!")
        )
        self.assertTrue(
            self.db.create_user("rival", "rival@example.test", "Strong-pass-2026!")
        )
        self.user = self.db.get_user_by_username("seller")
        self.rival = self.db.get_user_by_username("rival")
        with self.db.lock:
            self.db.conn.executemany(
                "INSERT INTO cookies (id, value, user_id) VALUES (?, ?, ?)",
                (
                    ("acct-one", "synthetic-cookie-one", self.user["id"]),
                    ("acct-two", "synthetic-cookie-two", self.rival["id"]),
                ),
            )
            self.db.conn.commit()
        self.db.save_item_basic_info("acct-one", "item-one", item_title="Resource one")
        self.db.save_item_basic_info("acct-one", "item-two", item_title="Resource two")
        self.data_card_id = self.db.create_card(
            "One-time codes",
            "data",
            data_content="stock-a\nstock-b",
            user_id=self.user["id"],
        )
        self.text_card_id = self.db.create_card(
            "Cloud link",
            "text",
            text_content="https://example.test/share code: 1234",
            user_id=self.user["id"],
        )

        self.original_db = reply_server.db_manager
        reply_server.db_manager = self.db
        reply_server.SESSION_TOKENS.clear()
        self.client = TestClient(reply_server.app, raise_server_exceptions=False)

    def tearDown(self) -> None:
        self.client.close()
        reply_server.SESSION_TOKENS.clear()
        reply_server.db_manager = self.original_db
        self.db.close()
        if self.previous_key_file is None:
            os.environ.pop("SYSTEM_SECRET_KEY_FILE", None)
        else:
            os.environ["SYSTEM_SECRET_KEY_FILE"] = self.previous_key_file
        self.tempdir.cleanup()

    def _headers(self, user=None) -> dict[str, str]:
        token, _ = reply_server.create_login_session(user or self.user)
        return {"Authorization": f"Bearer {token}"}

    def _begin_attempt(self, order_id: str = "order-one", quantity: int = 1) -> int:
        if self.db.get_order_by_id(order_id) is None:
            self.assertTrue(
                self.db.insert_or_update_order(
                    order_id=order_id,
                    cookie_id="acct-one",
                    item_id="item-one",
                    quantity=str(quantity),
                    order_status="pending_ship",
                )
            )
        attempt = self.db.begin_fulfillment_attempt(
            order_id=order_id,
            cookie_id="acct-one",
            expected_quantity=quantity,
        )
        self.assertEqual(attempt["outcome"], "acquired")
        return int(attempt["attempt_id"])

    def test_migration_adds_delivery_center_schema(self) -> None:
        columns = {
            row[1] for row in self.db.conn.execute("PRAGMA table_info(cards)").fetchall()
        }
        self.assertTrue(
            {
                "low_stock_threshold",
                "api_token_encrypted",
                "api_token_encryption_version",
                "api_validation_status",
            }
            <= columns
        )
        item_columns = {
            row[1]
            for row in self.db.conn.execute("PRAGMA table_info(item_info)").fetchall()
        }
        self.assertIn("delivery_mode", item_columns)
        tables = {
            row[0]
            for row in self.db.conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        self.assertTrue(
            {
                "fulfillment_api_operations",
                "fulfillment_delivery_payloads",
                "fulfillment_resend_events",
            }
            <= tables
        )
        self.assertEqual(self.db.schema_version, "2026082502")

    def test_api_token_is_encrypted_and_never_returned_by_public_card_reads(self) -> None:
        card_id = self.db.create_card(
            "Provider",
            "api",
            api_config={
                "protocol": "fulfillment_api_v1",
                "url": "https://provider.example.test/fulfill",
                "method": "POST",
                "spec": {"product": "pro"},
            },
            api_token="provider-secret",
            user_id=self.user["id"],
        )
        raw = self.db.conn.execute(
            "SELECT api_config, api_token_encrypted FROM cards WHERE id = ?",
            (card_id,),
        ).fetchone()
        self.assertNotIn("provider-secret", str(raw[0]))
        self.assertTrue(str(raw[1]).startswith(SYSTEM_SECRET_PREFIX))
        public = self.db.get_card_by_id(card_id, self.user["id"])
        self.assertTrue(public["api_token_configured"])
        self.assertNotIn("api_token", public["api_config"])
        self.assertEqual(public["api_config"]["method"], "POST")
        runtime = self.db.get_card_api_runtime_config(card_id, self.user["id"])
        self.assertEqual(runtime["api_token"], "provider-secret")
        self.assertEqual(runtime["method"], "POST")

        with self.assertRaisesRegex(ValueError, "只支持 POST"):
            self.db.create_card(
                "Unsafe provider",
                "api",
                api_config={
                    "protocol": "fulfillment_api_v1",
                    "url": "https://provider.example.test/fulfill",
                    "method": "GET",
                    "spec": {},
                },
                api_token="provider-secret",
                user_id=self.user["id"],
            )

    def test_create_route_maps_each_resource_type_without_generic_content_loss(self) -> None:
        fixtures = (
            (
                "fixed",
                {"name": "Cloud package", "type": "text", "content": "link + code"},
                "text_content",
                "link + code",
            ),
            (
                "stock",
                {"name": "Codes", "type": "data", "content": "A\nA\nB\n"},
                "data_content",
                "A\nB",
            ),
            (
                "picture",
                {"name": "Guide image", "type": "image", "content": "/uploads/guide.png"},
                "image_url",
                "/uploads/guide.png",
            ),
            (
                "api",
                {
                    "name": "Provider v1",
                    "type": "api",
                    "api_config": {
                        "protocol": "fulfillment_api_v1",
                        "url": "https://provider.example.test/allocate",
                        "method": "POST",
                        "timeout": 5,
                        "spec": {"sku": "pro"},
                    },
                    "api_token": "provider-secret",
                },
                "api_validation_status",
                "unvalidated",
            ),
        )
        for label, payload, field, expected in fixtures:
            with self.subTest(label=label):
                response = self.client.post(
                    "/cards", headers=self._headers(), json=payload
                )
                self.assertEqual(response.status_code, 200, response.text)
                card = self.db.get_card_by_id(response.json()["id"], self.user["id"])
                self.assertEqual(card[field], expected)
                self.assertNotIn("provider-secret", response.text)

        empty = self.client.post(
            "/cards",
            headers=self._headers(),
            json={"name": "Empty", "type": "text", "content": "  "},
        )
        self.assertEqual(empty.status_code, 400, empty.text)

    def test_stock_import_filters_blanks_and_deduplicates_current_and_history(self) -> None:
        attempt_id = self._begin_attempt()
        self.assertEqual(
            self.db.reserve_batch_card_data(attempt_id, self.data_card_id, 1),
            ["stock-a"],
        )
        self.assertTrue(
            self.db.release_fulfillment_attempt(attempt_id, "pre_send_cancelled")
        )
        result = self.db.import_card_stock(
            self.data_card_id,
            self.user["id"],
            ["", " stock-a ", "stock-b", "new-c", "new-c", "new-d"],
        )
        self.assertEqual(result["added"], 2)
        self.assertEqual(result["duplicates"], 3)
        self.assertEqual(result["blank"], 1)
        self.assertEqual(result["invalid"], 0)
        self.assertEqual(result["stats"]["available"], 4)
        self.assertEqual(result["stats"]["reserved"], 0)
        self.assertEqual(result["stats"]["used"], 0)
        self.assertEqual(result["stats"]["bound"], 0)

    def test_stock_import_route_parses_csv_secret_column_and_is_owner_scoped(self) -> None:
        response = self.client.post(
            f"/cards/{self.data_card_id}/stock/import",
            headers=self._headers(),
            json={
                "format": "csv",
                "content": "secret,note\ncsv-a,first\ncsv-b,second\n,blank\n",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["added"], 2)
        denied = self.client.post(
            f"/cards/{self.data_card_id}/stock/import",
            headers=self._headers(self.rival),
            json={"format": "lines", "content": "foreign"},
        )
        self.assertEqual(denied.status_code, 404)

    def test_delivery_modes_are_mutually_exclusive_and_off_is_explicit(self) -> None:
        resource = self.db.set_item_delivery_mode(
            "acct-one",
            "item-one",
            "resource",
            self.user["id"],
            card_id=self.text_card_id,
        )
        self.assertEqual(resource["outcome"], "updated")
        self.assertEqual(
            self.db.get_item_delivery_binding_status(
                "acct-one", "item-one", self.user["id"]
            )["status"],
            "active",
        )
        invite = self.db.set_item_delivery_mode(
            "acct-one", "item-one", "invite", self.user["id"]
        )
        self.assertEqual(invite["outcome"], "updated")
        item = self.db.get_item_info("acct-one", "item-one")
        self.assertTrue(item["invite_auto_fulfillment"])
        self.assertIsNone(item["delivery_card_id"])
        off = self.db.set_item_delivery_mode(
            "acct-one", "item-one", "off", self.user["id"]
        )
        self.assertEqual(off["outcome"], "updated")
        status = self.db.get_item_delivery_binding_status(
            "acct-one", "item-one", self.user["id"]
        )
        self.assertEqual(status["mode"], "off")
        self.assertEqual(status["status"], "explicit_off")

    def test_delivery_mode_batch_keeps_successes_and_reports_failures(self) -> None:
        result = self.db.set_item_delivery_modes_batch(
            "acct-one",
            ["item-one", "missing", "item-two"],
            "resource",
            self.user["id"],
            card_id=self.text_card_id,
        )
        self.assertEqual(result["updated"], ["item-one", "item-two"])
        self.assertEqual(result["failed"], [{"item_id": "missing", "error": "item_not_found"}])

    def test_delivery_mode_routes_use_atomic_contract_and_keep_partial_successes(self) -> None:
        single = self.client.put(
            "/items/acct-one/item-one/delivery-mode",
            headers=self._headers(),
            json={"mode": "resource", "card_id": self.text_card_id},
        )
        self.assertEqual(single.status_code, 200, single.text)
        self.assertEqual(single.json()["mode"], "resource")

        batch = self.client.post(
            "/items/delivery-modes/batch",
            headers=self._headers(),
            json={
                "cookie_id": "acct-one",
                "item_ids": ["item-one", "missing", "item-two"],
                "mode": "invite",
            },
        )
        self.assertEqual(batch.status_code, 200, batch.text)
        self.assertEqual(batch.json()["updated"], ["item-one", "item-two"])
        self.assertEqual(
            batch.json()["failed"],
            [{"item_id": "missing", "error": "item_not_found"}],
        )

    def test_api_validate_is_https_post_strict_and_never_echoes_token(self) -> None:
        card_id = self.db.create_card(
            "Validated provider",
            "api",
            api_config={
                "protocol": "fulfillment_api_v1",
                "url": "https://provider.example.test/allocate",
                "timeout": 5,
                "spec": {"sku": "pro"},
            },
            api_token="stored-secret",
            user_id=self.user["id"],
        )
        with patch(
            "reply_server.request_public_http_sync",
            return_value=SimpleNamespace(
                status=200,
                text='{"status":"validated"}',
            ),
        ) as request:
            response = self.client.post(
                f"/cards/{card_id}/api/validate",
                headers=self._headers(),
                json={"api_token": "fresh-secret"},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "validated")
        self.assertNotIn("fresh-secret", response.text)
        args, kwargs = request.call_args
        self.assertEqual(args[:2], ("POST", "https://provider.example.test/allocate"))
        self.assertEqual(kwargs["allowed_methods"], ("POST",))
        self.assertTrue(kwargs["require_https"])
        self.assertEqual(set(kwargs["json_body"]), {"action", "spec"})
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer fresh-secret")

        with patch(
            "reply_server.request_public_http_sync",
            return_value=SimpleNamespace(
                status=200,
                text='{"status":"validated","extra":true}',
            ),
        ):
            rejected = self.client.post(
                f"/cards/{card_id}/api/validate",
                headers=self._headers(),
                json={},
            )
        self.assertEqual(rejected.status_code, 502, rejected.text)

    def test_bound_or_historical_resource_cannot_be_hard_deleted(self) -> None:
        self.db.set_item_delivery_mode(
            "acct-one",
            "item-one",
            "resource",
            self.user["id"],
            card_id=self.data_card_id,
        )
        self.assertFalse(self.db.delete_card(self.data_card_id, self.user["id"]))
        self.db.set_item_delivery_mode(
            "acct-one", "item-one", "off", self.user["id"]
        )
        attempt_id = self._begin_attempt()
        self.assertEqual(
            self.db.reserve_batch_card_data(attempt_id, self.data_card_id, 1),
            ["stock-a"],
        )
        self.assertTrue(
            self.db.release_fulfillment_attempt(attempt_id, "pre_send_cancelled")
        )
        self.assertFalse(self.db.delete_card(self.data_card_id, self.user["id"]))

    def test_api_operation_and_payload_are_idempotent_and_tenant_scoped(self) -> None:
        api_card_id = self.db.create_card(
            "Provider",
            "api",
            api_config={
                "protocol": "fulfillment_api_v1",
                "url": "https://provider.example.test/fulfill",
                "spec": {"product": "pro"},
            },
            api_token="provider-secret",
            user_id=self.user["id"],
        )
        attempt_id = self._begin_attempt(quantity=2)
        created = self.db.create_fulfillment_api_operation(
            attempt_id=attempt_id,
            card_id=api_card_id,
            idempotency_key="stable-key",
            config_fingerprint="config-v1",
            request_spec={"product": "pro"},
        )
        self.assertEqual(created["outcome"], "created")
        repeated = self.db.create_fulfillment_api_operation(
            attempt_id=attempt_id,
            card_id=api_card_id,
            idempotency_key="stable-key",
            config_fingerprint="config-v1",
            request_spec={"product": "pro"},
        )
        self.assertEqual(repeated["outcome"], "existing")
        operation = self.db.record_fulfillment_api_attempt(
            created["operation"]["id"],
            state="pending",
            http_status=202,
            external_operation_id="provider-op",
        )
        self.assertEqual(operation["attempt_count"], 1)
        self.assertEqual(operation["state"], "pending")

        first = self.db.commit_fulfillment_delivery_payload(
            attempt_id,
            ["secret-one", "secret-two"],
            source_type="api_v1",
            source_operation_id=created["operation"]["id"],
        )
        self.assertEqual(first["outcome"], "committed")
        same = self.db.commit_fulfillment_delivery_payload(
            attempt_id,
            ["secret-one", "secret-two"],
            source_type="resource",
        )
        self.assertEqual(same["outcome"], "existing")
        conflict = self.db.commit_fulfillment_delivery_payload(
            attempt_id,
            ["different"],
            source_type="resource",
        )
        self.assertEqual(conflict["outcome"], "conflict")
        self.assertIsNone(
            self.db.get_fulfillment_delivery_payload(
                first["payload"]["id"], self.rival["id"]
            )
        )

    def test_fulfillment_record_route_masks_payload_and_resend_requires_owner(self) -> None:
        attempt_id = self._begin_attempt()
        payload = self.db.commit_fulfillment_delivery_payload(
            attempt_id,
            ["very-sensitive-code"],
            source_type="resource",
        )["payload"]
        records = self.client.get(
            "/fulfillment-records", headers=self._headers()
        )
        self.assertEqual(records.status_code, 200, records.text)
        item = records.json()["items"][0]
        self.assertEqual(item["id"], payload["id"])
        self.assertNotIn("very-sensitive-code", records.text)
        denied = self.client.post(
            f"/fulfillment-records/{payload['id']}/resend",
            headers=self._headers(self.rival),
        )
        self.assertEqual(denied.status_code, 404)

    def test_real_database_operation_payload_and_resend_event_close_the_loop(self) -> None:
        api_card_id = self.db.create_card(
            "Loop provider",
            "api",
            api_config={
                "protocol": "fulfillment_api_v1",
                "url": "https://provider.example.test/allocate",
                "spec": {},
            },
            api_token="provider-secret",
            user_id=self.user["id"],
        )
        attempt_id = self._begin_attempt(order_id="order-resend")
        with self.db.lock:
            self.db.conn.execute(
                "UPDATE orders SET buyer_id = ?, chat_id = ? WHERE order_id = ?",
                ("buyer-one", "chat-one", "order-resend"),
            )
            self.db.conn.commit()
        operation = self.db.create_fulfillment_api_operation(
            attempt_id=attempt_id,
            card_id=api_card_id,
            idempotency_key="stable-resend-key",
            config_fingerprint="stable-config",
            request_spec={},
        )["operation"]
        self.db.record_fulfillment_api_attempt(
            operation["id"],
            state="succeeded",
            http_status=200,
            external_operation_id="provider-op",
            response_items=["ORIGINAL-CODE"],
        )
        payload = self.db.commit_fulfillment_delivery_payload(
            attempt_id,
            ["ORIGINAL-CODE"],
            source_type="api_v1",
            source_operation_id=operation["id"],
            source_card_id=api_card_id,
        )["payload"]
        self.assertTrue(self.db.mark_fulfillment_sending(attempt_id))
        self.assertTrue(self.db.commit_fulfillment_attempt(attempt_id, 1))

        from XianyuAutoAsync import XianyuLive

        live = object.__new__(XianyuLive)
        live.cookie_id = "acct-one"
        live.ws = object()
        live.myid = "seller-one"
        live._safe_str = lambda value: str(value)
        live.send_msg = AsyncMock(
            return_value={"headers": {"mid": "mid-one"}, "body": {"code": 200}}
        )
        result = asyncio.run(
            live.resend_fulfillment_payload(
                payload_id=payload["id"],
                user_id=self.user["id"],
                database=self.db,
            )
        )
        self.assertEqual(result["status"], "succeeded")
        live.send_msg.assert_awaited_once_with(
            live.ws,
            "chat-one",
            "buyer-one",
            "ORIGINAL-CODE",
            wait_for_response=True,
        )
        states = [
            row[0]
            for row in self.db.conn.execute(
                "SELECT status FROM fulfillment_resend_events "
                "WHERE payload_id = ? ORDER BY id",
                (payload["id"],),
            ).fetchall()
        ]
        self.assertEqual(states, ["prepared", "succeeded"])


if __name__ == "__main__":
    unittest.main()
