"""Reliable fulfillment API v1 and committed-payload resend regressions."""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from XianyuAutoAsync import XianyuLive


class FakeFulfillmentDatabase:
    def __init__(self) -> None:
        self.operation = None
        self.payload = None
        self.manual_review_reasons: list[str] = []
        self.recorded_states: list[str] = []
        self.resend_states: list[str] = []

    def create_fulfillment_api_operation(self, **values):
        if self.operation is None:
            self.operation = {
                "id": 41,
                "attempt_id": values["attempt_id"],
                "idempotency_key": values["idempotency_key"],
                "state": "prepared",
                "attempt_count": 0,
                "external_operation_id": "",
            }
            return {"outcome": "created", "operation": dict(self.operation)}
        return {"outcome": "existing", "operation": dict(self.operation)}

    def get_fulfillment_api_operation(self, **_filters):
        return dict(self.operation) if self.operation else None

    def record_fulfillment_api_attempt(self, operation_id, **values):
        assert operation_id == 41
        self.operation["attempt_count"] += 1
        self.operation["state"] = values["state"]
        self.operation["external_operation_id"] = values.get(
            "external_operation_id", ""
        )
        if values.get("response_items") is not None:
            self.operation["response_items"] = list(values["response_items"])
        self.recorded_states.append(values["state"])
        return dict(self.operation)

    def commit_fulfillment_delivery_payload(self, **values):
        if self.payload is None:
            self.payload = {
                "id": 77,
                "attempt_id": values["attempt_id"],
                "payloads": list(values["payloads"]),
                "source_type": values["source_type"],
                "source_operation_id": values.get("source_operation_id"),
            }
            return {"outcome": "created", "payload": dict(self.payload)}
        if self.payload["payloads"] != list(values["payloads"]):
            return {"outcome": "conflict", "payload": dict(self.payload)}
        return {"outcome": "existing", "payload": dict(self.payload)}

    def get_fulfillment_delivery_payload(self, **_filters):
        return dict(self.payload) if self.payload else None

    def mark_fulfillment_manual_review(self, _attempt_id, reason, **_values):
        self.manual_review_reasons.append(reason)
        return True

    def record_fulfillment_resend_event(self, **values):
        self.resend_states.append(values["status"])
        return {"id": len(self.resend_states), **values}


def make_live() -> XianyuLive:
    live = object.__new__(XianyuLive)
    live.cookie_id = "account-one"
    live.ws = object()
    live.myid = "seller-one"
    live._safe_str = lambda value: str(value)
    return live


def api_rule(**config_overrides):
    config = {
        "protocol": "fulfillment_api_v1",
        "url": "https://provider.example/v1/allocate",
        "token": "provider-secret",
        "timeout": 3,
        "spec": {"sku": "gold"},
    }
    config.update(config_overrides)
    return {
        "card_id": 9,
        "card_type": "api",
        "card_name": "provider",
        "api_config": config,
    }


async def _case_api_v1_posts_minimal_body_with_stable_key_and_persists_before_use():
    live = make_live()
    database = FakeFulfillmentDatabase()
    response = SimpleNamespace(
        status=200,
        text=json.dumps(
            {
                "status": "succeeded",
                "operation_id": "provider-op-1",
                "items": ["CODE-A", "CODE-B"],
            }
        ),
    )

    with patch(
        "XianyuAutoAsync.request_public_http", AsyncMock(return_value=response)
    ) as request:
        first = await live._prepare_fulfillment_api_v1_payloads(
            rule=api_rule(),
            order_id="order-one",
            item_id="item-one",
            expected_quantity=2,
            fulfillment_attempt_id=31,
            spec_name="套餐",
            spec_value="黄金",
            database=database,
        )
        second = await live._prepare_fulfillment_api_v1_payloads(
            rule=api_rule(),
            order_id="order-one",
            item_id="item-one",
            expected_quantity=2,
            fulfillment_attempt_id=31,
            spec_name="套餐",
            spec_value="黄金",
            database=database,
        )

    assert first == ["CODE-A", "CODE-B"]
    assert second == first
    assert database.payload["payloads"] == first
    assert request.await_count == 1
    call = request.await_args
    assert call.args[:2] == ("POST", "https://provider.example/v1/allocate")
    assert set(call.kwargs["json_body"]) == {
        "action",
        "idempotency_key",
        "order_id",
        "item_id",
        "quantity",
        "spec",
    }
    assert call.kwargs["json_body"]["action"] == "allocate"
    assert call.kwargs["json_body"]["quantity"] == 2
    assert "buyer" not in json.dumps(call.kwargs["json_body"])
    assert "cookie" not in json.dumps(call.kwargs["json_body"])
    assert "provider-secret" not in json.dumps(call.kwargs["json_body"])
    assert (
        call.kwargs["headers"]["Idempotency-Key"]
        == call.kwargs["json_body"]["idempotency_key"]
        == database.operation["idempotency_key"]
    )


async def _case_api_v1_pending_retries_four_times_with_the_same_key_then_reviews():
    live = make_live()
    database = FakeFulfillmentDatabase()
    response = SimpleNamespace(
        status=200,
        text=json.dumps(
            {"status": "pending", "operation_id": "provider-op-1", "items": []}
        ),
    )
    request = AsyncMock(return_value=response)

    with (
        patch("XianyuAutoAsync.request_public_http", request),
        patch("XianyuAutoAsync.asyncio.sleep", AsyncMock()),
    ):
        result = await live._prepare_fulfillment_api_v1_payloads(
            rule=api_rule(),
            order_id="order-one",
            item_id="item-one",
            expected_quantity=1,
            fulfillment_attempt_id=31,
            spec_name=None,
            spec_value=None,
            database=database,
        )

    assert result is None
    assert request.await_count == 4
    assert {
        call.kwargs["headers"]["Idempotency-Key"]
        for call in request.await_args_list
    } == {database.operation["idempotency_key"]}
    assert database.recorded_states == ["pending"] * 4
    assert database.manual_review_reasons == ["api_v1_retry_exhausted"]


async def _case_api_v1_success_quantity_mismatch_is_quarantined_without_payload():
    live = make_live()
    database = FakeFulfillmentDatabase()
    response = SimpleNamespace(
        status=200,
        text=json.dumps(
            {
                "status": "succeeded",
                "operation_id": "provider-op-1",
                "items": ["ONLY-ONE"],
            }
        ),
    )

    with patch(
        "XianyuAutoAsync.request_public_http", AsyncMock(return_value=response)
    ):
        result = await live._prepare_fulfillment_api_v1_payloads(
            rule=api_rule(),
            order_id="order-one",
            item_id="item-one",
            expected_quantity=2,
            fulfillment_attempt_id=31,
            spec_name=None,
            spec_value=None,
            database=database,
        )

    assert result is None
    assert database.payload is None
    assert database.manual_review_reasons == ["api_v1_quantity_mismatch"]


async def _case_legacy_arbitrary_api_configuration_stays_manual():
    live = make_live()
    database = FakeFulfillmentDatabase()
    result = await live._prepare_fulfillment_api_v1_payloads(
        rule=api_rule(
            protocol=None,
            method="GET",
            headers={"X-Legacy": "value"},
            params={"buyer": "{buyer_id}"},
        ),
        order_id="order-one",
        item_id="item-one",
        expected_quantity=1,
        fulfillment_attempt_id=31,
        spec_name=None,
        spec_value=None,
        database=database,
    )
    assert result is None
    assert database.operation is None
    assert database.manual_review_reasons == ["legacy_api_requires_manual_review"]


async def _case_explicit_invalid_binding_never_falls_back_to_keyword_rule():
    class BindingDatabase(FakeFulfillmentDatabase):
        def get_item_info(self, _cookie_id, _item_id):
            return {"item_title": "matching title", "item_detail": ""}

        def get_item_multi_spec_status(self, _cookie_id, _item_id):
            return False

        def get_cookie_user_id(self, _cookie_id):
            return 7

        def get_item_delivery_binding_status(self, *_args, **_kwargs):
            return {
                "binding_explicit": True,
                "delivery_card_id": 9,
                "resource_status": "disabled",
                "rule": None,
            }

        def get_delivery_rules_by_keyword(self, *_args, **_kwargs):
            raise AssertionError("explicit invalid binding must not use keyword fallback")

    live = make_live()
    live.fetch_item_detail_from_api = AsyncMock(return_value="")
    live.save_item_detail_only = AsyncMock()
    database = BindingDatabase()

    result = await live._auto_delivery(
        "item-one",
        item_title="matching title",
        order_id="order-one",
        send_user_id="buyer-one",
        fulfillment_attempt_id=31,
        database=database,
    )

    assert result is None
    assert database.manual_review_reasons == ["bound_resource_disabled"]


async def _case_resend_reads_only_committed_payload_and_uses_message_ack():
    class ResendDatabase(FakeFulfillmentDatabase):
        def __init__(self):
            super().__init__()
            self.payload = {
                "id": 77,
                "attempt_id": 31,
                "payloads": ["ORIGINAL-CODE"],
                "source_type": "api_v1",
            }

        def get_fulfillment_attempt(self, attempt_id):
            assert attempt_id == 31
            return {
                "attempt_id": 31,
                "state": "committed",
                "order_id": "order-one",
                "cookie_id": "account-one",
                "user_id": 7,
            }

        def get_order_by_id(self, order_id):
            assert order_id == "order-one"
            return {
                "order_id": order_id,
                "cookie_id": "account-one",
                "buyer_id": "buyer-one",
                "chat_id": "chat-one",
                "item_id": "item-one",
            }

        def reserve_batch_card_data(self, *_args, **_kwargs):
            raise AssertionError("resend must not reserve inventory")

        def create_fulfillment_api_operation(self, **_kwargs):
            raise AssertionError("resend must not call provider API")

    live = make_live()
    live.send_msg = AsyncMock(
        return_value={"headers": {"mid": "mid-1"}, "body": {"code": 200}}
    )
    database = ResendDatabase()

    result = await live.resend_fulfillment_payload(
        payload_id=77,
        user_id=7,
        database=database,
    )

    assert result["status"] == "succeeded"
    live.send_msg.assert_awaited_once_with(
        live.ws,
        "chat-one",
        "buyer-one",
        "ORIGINAL-CODE",
        wait_for_response=True,
    )
    assert database.resend_states == ["prepared", "succeeded"]


class DeliveryApiV1Tests(unittest.IsolatedAsyncioTestCase):
    async def test_posts_minimal_body_with_stable_key_and_persists_before_use(self):
        await _case_api_v1_posts_minimal_body_with_stable_key_and_persists_before_use()

    async def test_pending_retries_four_times_with_the_same_key_then_reviews(self):
        await _case_api_v1_pending_retries_four_times_with_the_same_key_then_reviews()

    async def test_success_quantity_mismatch_is_quarantined_without_payload(self):
        await _case_api_v1_success_quantity_mismatch_is_quarantined_without_payload()

    async def test_legacy_arbitrary_configuration_stays_manual(self):
        await _case_legacy_arbitrary_api_configuration_stays_manual()

    async def test_explicit_invalid_binding_never_falls_back_to_keyword_rule(self):
        await _case_explicit_invalid_binding_never_falls_back_to_keyword_rule()

    async def test_resend_reads_only_committed_payload_and_uses_message_ack(self):
        await _case_resend_reads_only_committed_payload_and_uses_message_ack()
