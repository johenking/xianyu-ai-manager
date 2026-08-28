import asyncio
import json
import sqlite3
import threading
import hashlib
import hmac
import time

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import invite_bridge
from schema_migrations import (
    _invite_auto_fulfillment_v1,
    _invite_bridge_operations_v1,
)


class _DatabaseStub:
    def __init__(self, connection: sqlite3.Connection):
        self.conn = connection
        self.lock = threading.RLock()
        self.enabled_items: set[tuple[str, str]] = set()

    def get_invite_auto_fulfillment_item_ids(self, cookie_id=None):
        return {
            item_id
            for account_id, item_id in self.enabled_items
            if cookie_id is None or account_id == cookie_id
        }

    def is_invite_auto_fulfillment_enabled(self, cookie_id, item_id):
        return (cookie_id, item_id) in self.enabled_items


def test_bridge_canonical_json_matches_node_signing_vector():
    canonical = invite_bridge._canonical({"z": 1, "a": {"y": 2, "b": 3}})
    digest = hmac.new(
        b"secret",
        f"1770000000.nonce.{canonical}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    assert canonical == '{"a":{"b":3,"y":2},"z":1}'
    assert digest == "1336d5790cf482483d113e76fc065bf047275e2c420fdf270941a2ebb727bcc0"


def test_poller_exception_summary_keeps_status_and_redacts_sensitive_detail():
    import invite_bridge_poller as poller_module

    http_error = HTTPException(
        status_code=502,
        detail="invite order event returned 403; token=private-token",
    )
    summary = poller_module._exception_summary(http_error)
    assert summary.startswith("status=502 detail=invite order event returned 403")
    assert "private-token" not in summary
    assert "REDACTED" in summary

    plain_error = RuntimeError("socket closed")
    assert poller_module._exception_summary(plain_error) == (
        "type=RuntimeError detail=socket closed"
    )


def test_poller_manual_reauth_skips_discovery_and_local_delivery(monkeypatch):
    import invite_bridge_poller as poller_module

    class _Database:
        def get_all_cookies(self):
            return {"account-1": "fixture-cookie"}

        def get_account_session_refresh(self, cookie_id):
            assert cookie_id == "account-1"
            return {"state": "manual_reauth_required"}

        def get_orders_by_cookie(self, *_args, **_kwargs):
            raise AssertionError("manual reauth account reached local delivery scan")

    class _UnexpectedOrderClient:
        def __init__(self, **_kwargs):
            raise AssertionError("manual reauth account created an order client")

    monkeypatch.setattr(poller_module, "db_manager", _Database())
    monkeypatch.setattr(
        poller_module,
        "_allowed_item_ids",
        lambda _cookie_id: {"item-1"},
    )
    monkeypatch.setattr(
        poller_module,
        "XianyuOrderListClient",
        _UnexpectedOrderClient,
    )

    assert asyncio.run(poller_module.InviteBridgePoller().scan_once()) == 0


def test_poller_persists_first_platform_session_expiry(monkeypatch):
    import invite_bridge_poller as poller_module

    class _Database:
        def __init__(self):
            self.state = "success"
            self.updated = []

        def get_all_cookies(self):
            return {"account-1": "fixture-cookie"}

        def get_cookie_details(self, _cookie_id):
            return {"browser_user_agent": "fixture-agent"}

        def get_account_session_refresh(self, _cookie_id):
            return {"state": self.state}

        def update_account_session_refresh(self, cookie_id, **values):
            self.updated.append((cookie_id, values))
            self.state = values["state"]
            return True

        def get_orders_by_cookie(self, *_args, **_kwargs):
            raise AssertionError("expired account reached local delivery scan")

    class _OrderClient:
        def __init__(self, **_kwargs):
            pass

        async def discover(self, **_kwargs):
            return {
                "success": False,
                "error_code": "session_expired",
                "error": "登录状态已过期",
            }

    database = _Database()
    monkeypatch.setattr(poller_module, "db_manager", database)
    monkeypatch.setattr(poller_module, "_allowed_item_ids", lambda _id: {"item-1"})
    monkeypatch.setattr(poller_module, "XianyuOrderListClient", _OrderClient)

    assert asyncio.run(poller_module.InviteBridgePoller().scan_once()) == 0
    assert database.updated[0][0] == "account-1"
    assert database.updated[0][1]["state"] == "manual_reauth_required"


@pytest.mark.parametrize(
    "status",
    ["pending", "submitted", "succeeded", "ambiguous", "needs_review", "failed"],
)
def test_poller_accepts_any_message_operation_state_as_downstream_evidence(
    monkeypatch, status
):
    import invite_bridge_poller as poller_module

    connection = sqlite3.connect(":memory:", check_same_thread=False)
    _invite_bridge_operations_v1(connection.cursor(), ":memory:")
    connection.execute(
        "INSERT INTO invite_bridge_operations "
        "(operation_key, operation_type, order_id, cookie_id, request_hash, status, "
        "created_at, updated_at) VALUES (?, 'message', ?, ?, 'hash', ?, 0, 0)",
        (f"message-{status}", "order-1", "account-1", status),
    )
    connection.commit()
    monkeypatch.setattr(poller_module, "db_manager", _DatabaseStub(connection))

    assert poller_module._message_operation_exists("order-1", "account-1") is True
    connection.close()


def test_poller_message_operation_evidence_is_scoped_to_order_and_account(monkeypatch):
    import invite_bridge_poller as poller_module

    connection = sqlite3.connect(":memory:", check_same_thread=False)
    _invite_bridge_operations_v1(connection.cursor(), ":memory:")
    connection.executemany(
        "INSERT INTO invite_bridge_operations "
        "(operation_key, operation_type, order_id, cookie_id, request_hash, status, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, 'hash', 'succeeded', 0, 0)",
        [
            ("fulfilled-same", "mark_fulfilled", "order-1", "account-1"),
            ("message-other-order", "message", "order-2", "account-1"),
            ("message-other-account", "message", "order-1", "account-2"),
        ],
    )
    connection.commit()
    monkeypatch.setattr(poller_module, "db_manager", _DatabaseStub(connection))

    assert poller_module._message_operation_exists("order-1", "account-1") is False
    connection.close()


@pytest.fixture()
def bridge_database(monkeypatch):
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    _invite_bridge_operations_v1(connection.cursor(), ":memory:")
    connection.commit()
    monkeypatch.setattr(invite_bridge, "db_manager", _DatabaseStub(connection))
    yield connection
    connection.close()


def test_bridge_operation_is_idempotent(bridge_database):
    body = {"operationKey": "operation-123", "orderId": "order-1"}
    first, created = invite_bridge._begin_operation(
        "operation-123", "message", "order-1", "account-1", body
    )
    replay, replay_created = invite_bridge._begin_operation(
        "operation-123", "message", "order-1", "account-1", body
    )

    assert created is True
    assert replay_created is False
    assert first["request_hash"] == replay["request_hash"]
    assert bridge_database.execute(
        "SELECT count(*) FROM invite_bridge_operations"
    ).fetchone()[0] == 1


def test_bridge_rejects_operation_key_payload_change(bridge_database):
    invite_bridge._begin_operation(
        "operation-123", "message", "order-1", "account-1", {"text": "first"}
    )

    with pytest.raises(HTTPException) as error:
        invite_bridge._begin_operation(
            "operation-123", "message", "order-1", "account-1", {"text": "different"}
        )

    assert error.value.status_code == 409


def test_bridge_migration_restricts_operation_states():
    connection = sqlite3.connect(":memory:")
    _invite_bridge_operations_v1(connection.cursor(), ":memory:")
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO invite_bridge_operations(operation_key,operation_type,order_id,cookie_id,request_hash,status,created_at,updated_at) VALUES ('k','message','o','c','h','unknown',0,0)"
        )
    connection.close()


def test_invite_selection_migration_defaults_existing_items_to_disabled():
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE item_info (cookie_id TEXT NOT NULL, item_id TEXT NOT NULL)"
    )
    connection.execute(
        "INSERT INTO item_info(cookie_id,item_id) VALUES ('account-1','item-1')"
    )
    _invite_auto_fulfillment_v1(connection.cursor(), ":memory:")

    assert connection.execute(
        "SELECT invite_auto_fulfillment FROM item_info"
    ).fetchone()[0] == 0
    connection.close()


def test_order_event_requires_valid_hmac_and_is_replay_protected(monkeypatch, bridge_database):
    monkeypatch.setenv("XIANYU_INVITE_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("XIANYU_INVITE_BRIDGE_SECRET", "bridge-test-secret")
    invite_bridge.db_manager.enabled_items.add(("account-1", "item-1"))
    invite_bridge._seen_nonces.clear()
    forwarded = []

    async def fake_forward(payload):
        forwarded.append(payload)
        return {"accepted": True}

    monkeypatch.setattr(invite_bridge, "_send_order_event_to_invite", fake_forward)
    payload = {
        "schemaVersion": "1",
        "eventId": "xianyu:account:order:paid",
        "orderId": "order-1",
        "cookieId": "account-1",
        "chatId": "chat-1",
        "toUserId": "buyer-1",
        "itemId": "item-1",
        "sku": "item-1",
        "productName": "Codex invitation",
        "amountCents": 100,
        "quantity": 1,
        "platformStatus": "pending_ship",
        "observedAt": "2026-08-09T00:00:00Z",
    }
    headers = invite_bridge._signature_headers(payload, "bridge-test-secret")
    app = FastAPI()
    app.include_router(invite_bridge.invite_bridge_router)

    with TestClient(app) as client:
        first = client.post("/internal/invite/order-events", json=payload, headers=headers)
        replay = client.post("/internal/invite/order-events", json=payload, headers=headers)

    assert first.status_code == 200
    assert first.json() == {"accepted": True}
    assert replay.status_code == 401
    assert len(forwarded) == 1


def test_order_event_rejects_item_outside_exact_allowlist(monkeypatch, bridge_database):
    monkeypatch.setenv("XIANYU_INVITE_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("XIANYU_INVITE_BRIDGE_SECRET", "bridge-test-secret")
    invite_bridge.db_manager.enabled_items.add(("account-1", "item-allowed"))
    invite_bridge._seen_nonces.clear()

    async def unexpected_forward(_payload):
        raise AssertionError("out-of-scope item was forwarded")

    monkeypatch.setattr(invite_bridge, "_send_order_event_to_invite", unexpected_forward)
    payload = {
        "schemaVersion": "1",
        "eventId": "xianyu:account:order:paid",
        "orderId": "order-1",
        "cookieId": "account-1",
        "chatId": "chat-1",
        "toUserId": "buyer-1",
        "itemId": "item-other",
        "sku": "item-other",
        "productName": "Other product",
        "amountCents": 100,
        "quantity": 1,
        "platformStatus": "pending_ship",
        "observedAt": "2026-08-09T00:00:00Z",
    }
    app = FastAPI()
    app.include_router(invite_bridge.invite_bridge_router)

    with TestClient(app) as client:
        response = client.post(
            "/internal/invite/order-events",
            json=payload,
            headers=invite_bridge._signature_headers(payload, "bridge-test-secret"),
        )

    assert response.status_code == 409
    assert response.json()["detail"] == "order item is not enabled for invite bridge"


def test_invite_item_is_excluded_from_legacy_card_fulfillment(monkeypatch, bridge_database):
    import XianyuAutoAsync

    monkeypatch.setenv("XIANYU_INVITE_BRIDGE_ENABLED", "true")
    bridge_database_stub = invite_bridge.db_manager
    bridge_database_stub.enabled_items.add(("account-1", "item-1"))
    monkeypatch.setattr(XianyuAutoAsync, "db_manager", bridge_database_stub)
    assert XianyuAutoAsync._invite_bridge_owns_item("account-1", "item-1") is True
    assert XianyuAutoAsync._invite_bridge_owns_item("account-1", "other") is False
    assert XianyuAutoAsync._invite_bridge_owns_item("account-2", "item-1") is False


def test_message_operation_succeeds_only_after_platform_ack(monkeypatch, bridge_database):
    import XianyuAutoAsync

    monkeypatch.setenv("XIANYU_INVITE_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("XIANYU_INVITE_BRIDGE_SECRET", "bridge-test-secret")
    invite_bridge.db_manager.enabled_items.add(("account-1", "item-1"))
    invite_bridge._seen_nonces.clear()
    database = invite_bridge.db_manager
    database.get_order_by_id = lambda _order_id: {
        "order_id": "order-1",
        "cookie_id": "account-1",
        "item_id": "item-1",
        "buyer_id": "buyer-1",
        "chat_id": "chat-1",
        "order_status": "pending_ship",
    }

    class _Socket:
        closed = False

    class _Live:
        ws = _Socket()
        sent = 0

        async def _verify_paid_order_for_delivery(self, **_kwargs):
            return {"allowed": True}

        async def send_msg(self, *_args, wait_for_response=False):
            assert wait_for_response is True
            self.sent += 1
            return {"code": 200}

    live = _Live()
    monkeypatch.setattr(XianyuAutoAsync.XianyuLive, "get_instance", staticmethod(lambda _cookie_id: live))
    payload = {
        "operationKey": "message-operation-1",
        "orderId": "order-1",
        "cookieId": "account-1",
        "chatId": "chat-1",
        "toUserId": "buyer-1",
        "text": "confirmation link",
        "requestId": "request-1",
    }
    app = FastAPI()
    app.include_router(invite_bridge.invite_bridge_router)

    with TestClient(app) as client:
        first = client.post(
            "/internal/invite/send-message",
            json=payload,
            headers=invite_bridge._signature_headers(payload, "bridge-test-secret"),
        )
        replay = client.post(
            "/internal/invite/send-message",
            json=payload,
            headers=invite_bridge._signature_headers(payload, "bridge-test-secret"),
        )

    assert first.status_code == 200
    assert first.json()["state"] == "succeeded"
    assert replay.json()["state"] == "succeeded"
    assert live.sent == 1
    assert first.json()["attempts"] == 1


def test_fulfillment_message_promotes_provisional_chat_to_verified_order_chat(
    monkeypatch, bridge_database
):
    import XianyuAutoAsync

    monkeypatch.setenv("XIANYU_INVITE_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("XIANYU_INVITE_BRIDGE_SECRET", "bridge-test-secret")
    invite_bridge.db_manager.enabled_items.add(("account-1", "item-1"))
    invite_bridge._seen_nonces.clear()
    database = invite_bridge.db_manager
    database.get_order_by_id = lambda _order_id: {
        "order_id": "order-canonical",
        "cookie_id": "account-1",
        "item_id": "item-1",
        "buyer_id": "buyer-1",
        "chat_id": "chat-real",
        "order_status": "pending_ship",
    }

    class _Socket:
        closed = False

    class _Live:
        ws = _Socket()

        def __init__(self):
            self.existing_calls = []

        async def _verify_paid_order_for_delivery(self, **_kwargs):
            return {"allowed": True}

        async def send_msg_once(self, *_args, **_kwargs):
            pytest.fail("a provisional chat must use the verified conversation")

        async def send_msg(
            self, websocket, chat_id, to_user_id, text, wait_for_response=False
        ):
            assert websocket is self.ws
            assert wait_for_response is True
            self.existing_calls.append((chat_id, to_user_id, text))
            return {"code": 200}

    live = _Live()
    monkeypatch.setattr(
        XianyuAutoAsync.XianyuLive,
        "get_instance",
        staticmethod(lambda _cookie_id: live),
    )
    payload = {
        "operationKey": "fulfillment-message-canonical-1",
        "orderId": "order-canonical",
        "cookieId": "account-1",
        "chatId": "direct:order-canonical",
        "toUserId": "buyer-1",
        "text": "redemption code",
        "requestId": "request-canonical-1",
    }
    app = FastAPI()
    app.include_router(invite_bridge.invite_bridge_router)

    with TestClient(app) as client:
        response = client.post(
            "/internal/invite/send-message",
            json=payload,
            headers=invite_bridge._signature_headers(payload, "bridge-test-secret"),
        )

    assert response.status_code == 200
    assert response.json()["state"] == "succeeded"
    assert response.json()["chatCanonicalized"] is True
    assert live.existing_calls == [("chat-real", "buyer-1", "redemption code")]


def test_fulfillment_identity_mismatch_can_reopen_same_operation_once(
    monkeypatch, bridge_database
):
    import XianyuAutoAsync

    monkeypatch.setenv("XIANYU_INVITE_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("XIANYU_INVITE_BRIDGE_SECRET", "bridge-test-secret")
    invite_bridge.db_manager.enabled_items.add(("account-1", "item-1"))
    invite_bridge._seen_nonces.clear()
    database = invite_bridge.db_manager
    database.get_order_by_id = lambda _order_id: {
        "order_id": "order-reconcile-chat",
        "cookie_id": "account-1",
        "item_id": "item-1",
        "buyer_id": "buyer-1",
        "chat_id": "chat-real",
        "order_status": "shipped",
    }

    class _Socket:
        closed = False

    class _Live:
        ws = _Socket()
        calls = 0

        async def send_msg(self, *_args, wait_for_response=False):
            assert wait_for_response is True
            self.calls += 1
            return {"code": 200}

    live = _Live()
    monkeypatch.setattr(
        XianyuAutoAsync.XianyuLive,
        "get_instance",
        staticmethod(lambda _cookie_id: live),
    )
    payload = {
        "operationKey": "fulfillment-message-reconcile-1",
        "orderId": "order-reconcile-chat",
        "cookieId": "account-1",
        "chatId": "direct:order-reconcile-chat",
        "toUserId": "buyer-1",
        "text": "redemption code",
        "requestId": "request-reconcile-1",
    }
    invite_bridge._begin_operation(
        payload["operationKey"],
        "message",
        payload["orderId"],
        payload["cookieId"],
        payload,
    )
    invite_bridge._set_operation(
        payload["operationKey"],
        "needs_review",
        error="chat identity mismatch",
    )
    app = FastAPI()
    app.include_router(invite_bridge.invite_bridge_router)

    with TestClient(app) as client:
        response = client.post(
            "/internal/invite/send-message",
            json=payload,
            headers=invite_bridge._signature_headers(payload, "bridge-test-secret"),
        )

    assert response.status_code == 200
    assert response.json()["state"] == "succeeded"
    assert response.json()["attempts"] == 2
    assert live.calls == 1


def test_message_operation_fails_closed_on_platform_rejection(monkeypatch, bridge_database):
    import XianyuAutoAsync

    monkeypatch.setenv("XIANYU_INVITE_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("XIANYU_INVITE_BRIDGE_SECRET", "bridge-test-secret")
    invite_bridge.db_manager.enabled_items.add(("account-1", "item-1"))
    invite_bridge._seen_nonces.clear()
    invite_bridge.db_manager.get_order_by_id = lambda _order_id: {
        "order_id": "order-1",
        "cookie_id": "account-1",
        "item_id": "item-1",
        "buyer_id": "buyer-1",
        "chat_id": "chat-1",
        "order_status": "pending_ship",
    }

    class _Socket:
        closed = False

    class _Live:
        ws = _Socket()

        async def _verify_paid_order_for_delivery(self, **_kwargs):
            return {"allowed": True}

        async def send_msg(self, *_args, wait_for_response=False):
            assert wait_for_response is True
            return {"code": 403, "reason": "rejected"}

    monkeypatch.setattr(
        XianyuAutoAsync.XianyuLive,
        "get_instance",
        staticmethod(lambda _cookie_id: _Live()),
    )
    payload = {
        "operationKey": "message-operation-rejected",
        "orderId": "order-1",
        "cookieId": "account-1",
        "chatId": "chat-1",
        "toUserId": "buyer-1",
        "text": "confirmation link",
        "requestId": "request-1",
    }
    app = FastAPI()
    app.include_router(invite_bridge.invite_bridge_router)

    with TestClient(app) as client:
        response = client.post(
            "/internal/invite/send-message",
            json=payload,
            headers=invite_bridge._signature_headers(payload, "bridge-test-secret"),
        )

    assert response.status_code == 200
    assert response.json()["state"] == "failed"
    assert response.json()["lastError"] == "platform message rejected: code=403"


@pytest.mark.parametrize("order_status", ["shipped", "completed"])
def test_fulfillment_message_succeeds_once_for_terminal_order(
    monkeypatch, bridge_database, order_status
):
    import XianyuAutoAsync

    monkeypatch.setenv("XIANYU_INVITE_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("XIANYU_INVITE_BRIDGE_SECRET", "bridge-test-secret")
    invite_bridge.db_manager.enabled_items.add(("account-1", "item-1"))
    invite_bridge._seen_nonces.clear()
    invite_bridge.db_manager.get_order_by_id = lambda _order_id: {
        "order_id": "order-1",
        "cookie_id": "account-1",
        "item_id": "item-1",
        "buyer_id": "buyer-1",
        "chat_id": "chat-1",
        "order_status": order_status,
    }

    class _Socket:
        closed = False

    class _Live:
        ws = _Socket()
        sent = 0

        async def _verify_paid_order_for_delivery(self, **_kwargs):
            pytest.fail("terminal fulfillment message rechecked pending payment")

        async def send_msg(self, *_args, wait_for_response=False):
            assert wait_for_response is True
            self.sent += 1
            return {"code": 200}

    live = _Live()
    monkeypatch.setattr(XianyuAutoAsync.XianyuLive, "get_instance", staticmethod(lambda _cookie_id: live))
    payload = {
        "operationKey": f"fulfillment-message-{order_status}",
        "orderId": "order-1",
        "cookieId": "account-1",
        "chatId": "chat-1",
        "toUserId": "buyer-1",
        "text": "redemption code",
        "requestId": "request-1",
    }
    app = FastAPI()
    app.include_router(invite_bridge.invite_bridge_router)

    with TestClient(app) as client:
        first = client.post(
            "/internal/invite/send-message",
            json=payload,
            headers=invite_bridge._signature_headers(payload, "bridge-test-secret"),
        )
        replay = client.post(
            "/internal/invite/send-message",
            json=payload,
            headers=invite_bridge._signature_headers(payload, "bridge-test-secret"),
        )

    assert first.json()["state"] == "succeeded"
    assert replay.json()["state"] == "succeeded"
    assert first.json()["attempts"] == 1
    assert live.sent == 1


@pytest.mark.parametrize(
    "operation_key", ["confirmation-message-order-1", "arbitrary-message-order-1"]
)
def test_terminal_order_rejects_non_fulfillment_message(
    monkeypatch, bridge_database, operation_key
):
    monkeypatch.setenv("XIANYU_INVITE_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("XIANYU_INVITE_BRIDGE_SECRET", "bridge-test-secret")
    invite_bridge.db_manager.enabled_items.add(("account-1", "item-1"))
    invite_bridge._seen_nonces.clear()
    invite_bridge.db_manager.get_order_by_id = lambda _order_id: {
        "order_id": "order-1",
        "cookie_id": "account-1",
        "item_id": "item-1",
        "buyer_id": "buyer-1",
        "chat_id": "chat-1",
        "order_status": "shipped",
    }
    payload = {
        "operationKey": operation_key,
        "orderId": "order-1",
        "cookieId": "account-1",
        "chatId": "chat-1",
        "toUserId": "buyer-1",
        "text": "confirmation link",
        "requestId": "request-1",
    }
    app = FastAPI()
    app.include_router(invite_bridge.invite_bridge_router)

    with TestClient(app) as client:
        response = client.post(
            "/internal/invite/send-message",
            json=payload,
            headers=invite_bridge._signature_headers(payload, "bridge-test-secret"),
        )

    assert response.json()["state"] == "needs_review"
    assert response.json()["lastError"] == "order is not pending_ship"


def _post_mark_fulfilled(payload):
    app = FastAPI()
    app.include_router(invite_bridge.invite_bridge_router)
    with TestClient(app) as client:
        return client.post(
            "/internal/invite/mark-fulfilled",
            json=payload,
            headers=invite_bridge._signature_headers(payload, "bridge-test-secret"),
        )


def _already_shipped_mark_fulfilled_setup(monkeypatch):
    """本地已发×平台状态未知的 mark-fulfilled 场景公共装配。

    旧契约（已废弃）：本地 system_shipped/shipped 直接返回 succeeded、
    禁止读取平台凭据——正是这个"假成功"把码已发、平台仍待发货的订单
    永久吞掉（2026-08 实测 10 笔活跃账号卡单）。新契约必须回查平台。
    """
    monkeypatch.setenv("XIANYU_INVITE_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("XIANYU_INVITE_BRIDGE_SECRET", "bridge-test-secret")
    invite_bridge.db_manager.enabled_items.add(("account-1", "item-1"))
    invite_bridge._seen_nonces.clear()
    order = {
        "order_id": "order-1",
        "cookie_id": "account-1",
        "item_id": "item-1",
        "buyer_id": "buyer-1",
        "order_status": "shipped",
        "system_shipped": 1,
    }
    invite_bridge.db_manager.get_order_by_id = lambda _order_id: dict(order)
    invite_bridge.db_manager.get_cookie = lambda _cookie_id: "fixture-cookie"
    return {
        "operationKey": "fulfillment-mark-already-shipped",
        "orderId": "order-1",
        "cookieId": "account-1",
        "itemId": "item-1",
        "requestId": "request-1",
    }


def test_mark_fulfilled_already_shipped_confirms_platform_progressed(
    monkeypatch, bridge_database
):
    payload = _already_shipped_mark_fulfilled_setup(monkeypatch)
    recheck_calls = []

    async def fake_status(cookie_id, order_id, cookies):
        recheck_calls.append((cookie_id, order_id, cookies))
        return {"success": True, "status": "shipped"}

    async def unexpected_ship(**_kwargs):
        raise AssertionError("platform already progressed but ship was re-executed")

    monkeypatch.setattr(invite_bridge, "_fetch_platform_order_status", fake_status)
    monkeypatch.setattr(invite_bridge, "_execute_platform_ship", unexpected_ship)

    response = _post_mark_fulfilled(payload)

    assert response.json()["state"] == "succeeded"
    assert response.json()["platformStatus"] == "shipped"
    assert recheck_calls == [("account-1", "order-1", "fixture-cookie")]


def test_mark_fulfilled_already_shipped_locally_reships_platform_pending(
    monkeypatch, bridge_database
):
    """反自锁核心用例：本地已发但平台仍待发货时必须补真实发货。"""
    payload = _already_shipped_mark_fulfilled_setup(monkeypatch)
    updates = []
    invite_bridge.db_manager.insert_or_update_order = lambda **values: (
        updates.append(values) or True
    )
    ship_calls = []

    async def fake_status(_cookie_id, _order_id, _cookies):
        return {"success": True, "status": "pending_ship"}

    async def fake_ship(**kwargs):
        ship_calls.append(kwargs)
        return {"success": True, "delivery_mode": "status_only"}

    monkeypatch.setattr(invite_bridge, "_fetch_platform_order_status", fake_status)
    monkeypatch.setattr(invite_bridge, "_execute_platform_ship", fake_ship)

    response = _post_mark_fulfilled(payload)

    assert response.json()["state"] == "succeeded"
    assert response.json()["platformStatus"] == "shipped"
    assert [call["order_id"] for call in ship_calls] == ["order-1"]
    assert updates[-1]["order_status"] == "shipped"
    assert updates[-1]["system_shipped"] is True


def test_mark_fulfilled_already_shipped_recheck_failure_fails_closed(
    monkeypatch, bridge_database
):
    payload = _already_shipped_mark_fulfilled_setup(monkeypatch)

    async def fake_status(_cookie_id, _order_id, _cookies):
        return {"success": False, "error": "platform detail fetch failed"}

    async def unexpected_ship(**_kwargs):
        raise AssertionError("platform state unknown but ship was executed blindly")

    monkeypatch.setattr(invite_bridge, "_fetch_platform_order_status", fake_status)
    monkeypatch.setattr(invite_bridge, "_execute_platform_ship", unexpected_ship)

    response = _post_mark_fulfilled(payload)

    assert response.json()["state"] == "needs_review"
    assert "platform status recheck failed" in response.json()["lastError"]


def test_mark_fulfilled_uses_status_only_once(monkeypatch, bridge_database):
    import secure_confirm_decrypted

    monkeypatch.setenv("XIANYU_INVITE_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("XIANYU_INVITE_BRIDGE_SECRET", "bridge-test-secret")
    invite_bridge.db_manager.enabled_items.add(("account-1", "item-1"))
    invite_bridge._seen_nonces.clear()
    database = invite_bridge.db_manager
    order = {
        "order_id": "order-1",
        "cookie_id": "account-1",
        "item_id": "item-1",
        "buyer_id": "buyer-1",
        "chat_id": "chat-1",
        "order_status": "pending_ship",
        "system_shipped": 0,
    }
    database.get_order_by_id = lambda _order_id: order
    database.get_cookie = lambda _cookie_id: "fixture-cookie"

    def update_order(**values):
        order["order_status"] = values["order_status"]
        order["system_shipped"] = int(values["system_shipped"])
        return True

    database.insert_or_update_order = update_order

    class _SecureConfirm:
        calls = 0

        def __init__(self, *_args):
            pass

        async def auto_confirm(self, order_id, item_id):
            assert order_id == "order-1"
            assert item_id == "item-1"
            self.__class__.calls += 1
            return {"success": True}

    monkeypatch.setattr(secure_confirm_decrypted, "SecureConfirm", _SecureConfirm)
    payload = {
        "operationKey": "fulfillment-operation-1",
        "orderId": "order-1",
        "cookieId": "account-1",
        "itemId": "item-1",
        "requestId": "request-1",
    }
    app = FastAPI()
    app.include_router(invite_bridge.invite_bridge_router)

    with TestClient(app) as client:
        first = client.post(
            "/internal/invite/mark-fulfilled",
            json=payload,
            headers=invite_bridge._signature_headers(payload, "bridge-test-secret"),
        )
        replay = client.post(
            "/internal/invite/mark-fulfilled",
            json=payload,
            headers=invite_bridge._signature_headers(payload, "bridge-test-secret"),
        )

    assert first.status_code == 200
    assert first.json()["state"] == "succeeded"
    assert first.json()["platformStatus"] == "shipped"
    assert first.json()["attempts"] == 1
    assert replay.json()["state"] == "succeeded"
    assert _SecureConfirm.calls == 1
    assert order["order_status"] == "shipped"


def test_direct_chat_message_uses_buyer_session_initializer_once(monkeypatch, bridge_database):
    import XianyuAutoAsync

    monkeypatch.setenv("XIANYU_INVITE_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("XIANYU_INVITE_BRIDGE_SECRET", "bridge-test-secret")
    invite_bridge.db_manager.enabled_items.add(("account-1", "item-1"))
    invite_bridge._seen_nonces.clear()
    database = invite_bridge.db_manager
    database.get_order_by_id = lambda _order_id: {
        "order_id": "order-direct",
        "cookie_id": "account-1",
        "item_id": "item-1",
        "buyer_id": "buyer-1",
        "chat_id": "direct:order-direct",
        "order_status": "pending_ship",
    }

    class _Socket:
        closed = False

    class _Live:
        ws = _Socket()

        def __init__(self):
            self.direct_calls = []
            self.existing_calls = 0

        async def _verify_paid_order_for_delivery(self, **_kwargs):
            return {"allowed": True}

        async def send_msg_once(
            self,
            to_user_id,
            item_id,
            text,
            wait_for_response=False,
        ):
            assert wait_for_response is True
            self.direct_calls.append((to_user_id, item_id, text))
            return {"code": 200}

        async def send_msg(self, *_args):
            self.existing_calls += 1

    live = _Live()
    monkeypatch.setattr(
        XianyuAutoAsync.XianyuLive,
        "get_instance",
        staticmethod(lambda _cookie_id: live),
    )
    payload = {
        "operationKey": "message-direct-1",
        "orderId": "order-direct",
        "cookieId": "account-1",
        "chatId": "direct:order-direct",
        "toUserId": "buyer-1",
        "text": "confirmation link",
        "requestId": "request-direct-1",
    }
    app = FastAPI()
    app.include_router(invite_bridge.invite_bridge_router)

    with TestClient(app) as client:
        first = client.post(
            "/internal/invite/send-message",
            json=payload,
            headers=invite_bridge._signature_headers(payload, "bridge-test-secret"),
        )
        replay = client.post(
            "/internal/invite/send-message",
            json=payload,
            headers=invite_bridge._signature_headers(payload, "bridge-test-secret"),
        )

    assert first.status_code == 200
    assert first.json()["state"] == "succeeded"
    assert replay.json()["state"] == "succeeded"
    assert live.direct_calls == [("buyer-1", "item-1", "confirmation link")]
    assert live.existing_calls == 0


def test_direct_conversation_parser_accepts_nested_platform_frames():
    import XianyuAutoAsync

    nested = {
        "headers": {"mid": "server-response"},
        "body": [{"data": {"conversationInfo": {"cid": "conversation-1@goofish"}}}],
    }
    string_body = {"body": json.dumps({"singleChatConversation": {"cid": "conversation-2@goofish"}})}

    assert XianyuAutoAsync.XianyuLive._extract_direct_conversation_cid(nested) == "conversation-1"
    assert XianyuAutoAsync.XianyuLive._extract_direct_conversation_cid(string_body) == "conversation-2"

    error_frame = {
        "body": {
            "code": "ERR1234",
            "reason": "conversation unavailable",
            "scope": "IM",
            "developerMessage": "private detail must not be logged",
        }
    }
    summary = XianyuAutoAsync.XianyuLive._direct_frame_error_summary(error_frame)
    assert summary == {"code": "ERR1234", "reason": "conversation_unavailable", "scope": "IM"}
    shape = XianyuAutoAsync.XianyuLive._direct_frame_shape(error_frame)
    assert shape == {
        "body": {
            "code": "str:7",
            "reason": "str:24",
            "scope": "str:2",
            "developerMessage": "str:33",
        }
    }
    assert "private detail" not in json.dumps(shape)

    existing = {
        "body": {
            "userConvs": [{
                "singleChatUserConversation": {
                    "singleChatConversation": {
                        "cid": "conversation-existing@goofish",
                        "pairFirst": "buyer-1@goofish",
                        "pairSecond": "seller-1@goofish",
                        "extension": json.dumps({"itemId": "item-1"}),
                    }
                }
            }]
        }
    }
    assert XianyuAutoAsync.XianyuLive._extract_existing_direct_conversation_cid(
        existing,
        "buyer-1",
        "seller-1",
        "item-1",
    ) == "conversation-existing"
    assert not XianyuAutoAsync.XianyuLive._extract_existing_direct_conversation_cid(
        existing,
        "other-buyer",
        "seller-1",
        "item-1",
    )

    session_sync = {
        "data": {
            "hasMore": False,
            "sessions": [
                {
                    "session": {
                        "sessionId": "conversation-session-sync",
                        "sessionType": 1,
                        "userInfo": {"userId": "buyer-1"},
                    }
                },
                {
                    "session": {
                        "sessionId": "conversation-other",
                        "sessionType": 1,
                        "userInfo": {"userId": "buyer-2"},
                    }
                },
            ],
        }
    }
    assert XianyuAutoAsync.XianyuLive._extract_session_sync_direct_conversation_cid(
        session_sync,
        "buyer-1",
        "item-1",
    ) == "conversation-session-sync"
    session_sync["data"]["sessions"].append({
        "session": {
            "sessionId": "conversation-session-sync-2",
            "sessionType": 1,
            "userInfo": {"userId": "buyer-1"},
        }
    })
    assert not XianyuAutoAsync.XianyuLive._extract_session_sync_direct_conversation_cid(
        session_sync,
        "buyer-1",
        "item-1",
    )


def test_message_send_waits_for_matching_platform_ack():
    import XianyuAutoAsync

    live = object.__new__(XianyuAutoAsync.XianyuLive)
    live.myid = "seller-1"
    live._direct_conversation_waiters = {}

    class _Socket:
        async def send(self, raw):
            request = json.loads(raw)
            assert request["lwp"] == "/r/MessageSend/sendByReceiverScope"
            response = {
                "headers": {"mid": request["headers"]["mid"]},
                "code": 200,
            }
            asyncio.get_running_loop().call_soon(
                live._resolve_direct_conversation_response,
                response,
            )

    response = asyncio.run(
        live.send_msg(
            _Socket(),
            "conversation-1",
            "buyer-1",
            "redemption code",
            wait_for_response=True,
        )
    )

    assert response["code"] == 200
    assert live._direct_conversation_waiters == {}


def test_direct_message_reuses_main_socket_and_existing_conversation(monkeypatch):
    import XianyuAutoAsync

    live = object.__new__(XianyuAutoAsync.XianyuLive)
    live.cookie_id = "account-1"
    live.myid = "seller-1"
    live.direct_message_lock = asyncio.Lock()
    live._direct_conversation_waiters = {}
    live.direct_send_init_error_count = 0
    submitted = []
    remembered = []

    class _Database:
        def get_recent_order_by_item_and_buyer(self, item_id, buyer_id):
            assert (item_id, buyer_id) == ("item-1", "buyer-1")
            return {"order_id": "order-1", "cookie_id": "account-1"}

        def get_order_by_id(self, order_id):
            assert order_id == "order-1"
            return {
                "order_id": "order-1",
                "cookie_id": "account-1",
                "item_id": "item-1",
                "buyer_id": "buyer-1",
                "chat_id": "direct:order-1",
            }

        def insert_or_update_order(self, **values):
            remembered.append(values)
            return True

    monkeypatch.setattr(XianyuAutoAsync, "db_manager", _Database())

    class _Socket:
        closed = False

        async def send(self, raw):
            request = json.loads(raw)
            if request.get("lwp") == "/r/SingleChatConversation/create":
                response = {"headers": {"mid": request["headers"]["mid"]}, "code": 400}
                asyncio.get_running_loop().call_soon(
                    live._resolve_direct_conversation_response,
                    response,
                )
            elif request.get("lwp") == "/r/Conversation/listNewestPagination":
                response = {
                    "headers": {"mid": request["headers"]["mid"]},
                    "code": 200,
                    "body": {
                        "userConvs": [{
                            "singleChatUserConversation": {
                                "singleChatConversation": {
                                    "cid": "conversation-existing@goofish",
                                    "pairFirst": "buyer-1@goofish",
                                    "pairSecond": "seller-1@goofish",
                                    "extension": json.dumps({"itemId": "item-1"}),
                                }
                            }
                        }]
                    },
                }
                asyncio.get_running_loop().call_soon(
                    live._resolve_direct_conversation_response,
                    response,
                )
            else:
                raise AssertionError(f"unexpected socket write: {request.get('lwp')}")

    live.ws = _Socket()

    async def record_send(ws, cid, toid, text):
        submitted.append((ws, cid, toid, text))

    live.send_msg = record_send

    assert asyncio.run(live.send_msg_once("buyer-1", "item-1", "confirmation link")) is True
    assert submitted == [(live.ws, "conversation-existing", "buyer-1", "confirmation link")]
    assert remembered == [{
        "order_id": "order-1",
        "cookie_id": "account-1",
        "chat_id": "conversation-existing",
    }]
    assert live._direct_conversation_waiters == {}


def test_socket_close_wakes_pending_direct_message_waiters():
    import XianyuAutoAsync

    live = object.__new__(XianyuAutoAsync.XianyuLive)
    live._direct_conversation_waiters = {}

    async def run():
        waiter = asyncio.get_running_loop().create_future()
        live._direct_conversation_waiters["request-mid"] = waiter
        live._fail_direct_conversation_waiters("account websocket closed")
        with pytest.raises(
            XianyuAutoAsync.DirectMessageNotSubmitted,
            match="account websocket closed",
        ):
            await waiter

    asyncio.run(run())
    assert live._direct_conversation_waiters == {}


def test_listener_registration_subscribes_before_sync_state_and_ack(monkeypatch):
    import XianyuAutoAsync

    live = object.__new__(XianyuAutoAsync.XianyuLive)
    live.cookie_id = "account-1"
    live.current_token = "fixture-token"
    live.last_token_refresh_time = time.time()
    live.token_refresh_interval = 3600
    live.browser_user_agent = "fixture-agent"
    live.device_id = "fixture-device"
    live._direct_conversation_waiters = {}
    live._websocket_bootstrap_active = True
    live._websocket_bootstrap_error = None
    live.message_ack_error_count = 0
    handled_pushes = []
    normal_messages = []
    normal_tasks = []
    server_state = {
        "pipeline": "sync",
        "topic": "sync",
        "pts": 123456789,
        "timestamp": 123456,
        "seq": 7,
    }

    class _Socket:
        def __init__(self):
            self.messages = []
            self.incoming = asyncio.Queue()
            self.reader_count = 0

        async def send(self, raw):
            message = json.loads(raw)
            self.messages.append(message)
            if message.get("lwp") == "/reg":
                await self.incoming.put(json.dumps({
                    "code": "200",
                    "headers": {"mid": message["headers"]["mid"]},
                    "body": {},
                }))
            elif message.get("lwp") == "/r/Conversation/listNewestPagination":
                await self.incoming.put(json.dumps({
                    "lwp": "/s/sync",
                    "headers": {
                        "mid": message["headers"]["mid"],
                        "sid": "sync-sid",
                    },
                    "body": {},
                }))
            elif message.get("lwp") == "/r/SyncStatus/getState":
                await self.incoming.put(json.dumps({
                    "lwp": "/s/vulcan",
                    "headers": {
                        "mid": message["headers"]["mid"],
                        "sid": "vulcan-sid",
                    },
                    "body": {},
                }))
                await self.incoming.put(json.dumps({
                    "code": 200,
                    "headers": {"mid": "list-response"},
                    "body": {},
                }))
                await self.incoming.put(json.dumps({
                    "code": 200,
                    "headers": {"mid": message["headers"]["mid"]},
                    "body": server_state,
                }))
            elif message.get("lwp") == "/r/SyncStatus/ackDiff":
                await self.incoming.put(json.dumps({
                    "code": 200,
                    "headers": {"mid": message["headers"]["mid"]},
                    "body": {},
                }))

        def __aiter__(self):
            self.reader_count += 1
            return self

        async def __anext__(self):
            message = await self.incoming.get()
            if message is None:
                raise StopAsyncIteration
            return message

    original_sleep = asyncio.sleep

    async def no_sleep(_seconds):
        await original_sleep(0)

    async def handle_startup_push(message, websocket, acknowledge=True):
        assert acknowledge is False
        handled_pushes.append(message["lwp"])

    async def handle_normal_message(message, _websocket):
        normal_messages.append(message["lwp"])

    def create_normal_task(coro):
        task = asyncio.create_task(coro)
        normal_tasks.append(task)
        return task

    monkeypatch.setattr(XianyuAutoAsync.asyncio, "sleep", no_sleep)
    live.handle_message = handle_startup_push
    socket = _Socket()

    async def run():
        reader = asyncio.create_task(live._websocket_reader_loop(socket))
        await live.init(socket)
        live._websocket_bootstrap_active = False
        live._handle_message_with_semaphore = handle_normal_message
        live._create_tracked_task = create_normal_task
        await socket.incoming.put(json.dumps({
            "lwp": "/s/ordinary",
            "headers": {"mid": "ordinary-push", "sid": "ordinary-sid"},
            "body": {},
        }))
        await socket.incoming.put(None)
        await reader
        await asyncio.gather(*normal_tasks)

    asyncio.run(run())

    requests = [message for message in socket.messages if message.get("lwp")]
    assert [message["lwp"] for message in requests] == [
        "/reg",
        "/r/Conversation/listNewestPagination",
        "/r/SyncStatus/getState",
        "/r/SyncStatus/ackDiff",
    ]
    assert requests[0]["headers"]["sync"] == "0,0;0;0;"
    assert requests[1]["body"] == [9007199254740991, 50]
    assert requests[2]["body"] == [{"topic": "sync"}]
    assert requests[3]["body"] == [server_state]
    assert all(message["headers"]["mid"] for message in requests)
    assert handled_pushes == ["/s/sync", "/s/vulcan"]
    assert socket.reader_count == 1

    get_state_index = socket.messages.index(requests[2])
    ack_indexes = [
        index
        for index, message in enumerate(socket.messages)
        if message.get("code") == 200
        and message.get("headers", {}).get("sid") in {"sync-sid", "vulcan-sid"}
    ]
    assert len(ack_indexes) == 2
    assert ack_indexes[0] < get_state_index < ack_indexes[1]
    assert normal_messages == ["/s/ordinary"]


def test_listener_bootstrap_allows_no_startup_pushes(monkeypatch):
    import XianyuAutoAsync

    live = object.__new__(XianyuAutoAsync.XianyuLive)
    live.cookie_id = "account-1"
    live.current_token = "fixture-token"
    live.last_token_refresh_time = time.time()
    live.token_refresh_interval = 3600
    live.browser_user_agent = "fixture-agent"
    live.device_id = "fixture-device"
    live._direct_conversation_waiters = {}
    live._websocket_bootstrap_active = True
    live._websocket_bootstrap_error = None
    live._websocket_bootstrap_sync_timeout = 0.01
    live.message_ack_error_count = 0
    server_state = {"topic": "sync", "pts": 42, "timestamp": 7}

    class _Socket:
        def __init__(self):
            self.messages = []
            self.incoming = asyncio.Queue()

        async def send(self, raw):
            message = json.loads(raw)
            self.messages.append(message)
            if message.get("lwp") == "/reg":
                await self.incoming.put(json.dumps({
                    "code": 200,
                    "headers": {"mid": message["headers"]["mid"]},
                    "body": {},
                }))
            elif message.get("lwp") == "/r/Conversation/listNewestPagination":
                return
            elif message.get("lwp") == "/r/SyncStatus/getState":
                await self.incoming.put(json.dumps({
                    "code": 200,
                    "headers": {"mid": message["headers"]["mid"]},
                    "body": server_state,
                }))
            elif message.get("lwp") == "/r/SyncStatus/ackDiff":
                await self.incoming.put(json.dumps({
                    "code": 200,
                    "headers": {"mid": message["headers"]["mid"]},
                    "body": {},
                }))

        def __aiter__(self):
            return self

        async def __anext__(self):
            message = await self.incoming.get()
            if message is None:
                raise StopAsyncIteration
            return message

    original_sleep = asyncio.sleep

    async def no_sleep(_seconds):
        await original_sleep(0)

    monkeypatch.setattr(XianyuAutoAsync.asyncio, "sleep", no_sleep)
    socket = _Socket()

    async def run():
        reader = asyncio.create_task(live._websocket_reader_loop(socket))
        await live.init(socket)
        await socket.incoming.put(None)
        await reader

    asyncio.run(run())

    requests = [message for message in socket.messages if message.get("lwp")]
    assert [message["lwp"] for message in requests] == [
        "/reg",
        "/r/Conversation/listNewestPagination",
        "/r/SyncStatus/getState",
        "/r/SyncStatus/ackDiff",
    ]
    assert requests[-1]["body"] == [server_state]


def test_listener_bootstrap_stops_when_push_ack_fails(monkeypatch):
    import XianyuAutoAsync

    live = object.__new__(XianyuAutoAsync.XianyuLive)
    live.cookie_id = "account-1"
    live.current_token = "fixture-token"
    live.last_token_refresh_time = time.time()
    live.token_refresh_interval = 3600
    live.browser_user_agent = "fixture-agent"
    live.device_id = "fixture-device"
    live._direct_conversation_waiters = {}
    live._websocket_bootstrap_active = True
    live._websocket_bootstrap_error = None
    live.message_ack_error_count = 0

    class _Socket:
        def __init__(self):
            self.messages = []
            self.incoming = asyncio.Queue()

        async def send(self, raw):
            message = json.loads(raw)
            self.messages.append(message)
            if message.get("lwp") == "/reg":
                await self.incoming.put(json.dumps({
                    "code": 200,
                    "headers": {"mid": message["headers"]["mid"]},
                    "body": {},
                }))
            elif message.get("lwp") == "/r/Conversation/listNewestPagination":
                await self.incoming.put(json.dumps({
                    "lwp": "/s/sync",
                    "headers": {
                        "mid": message["headers"]["mid"],
                        "sid": "sync-sid",
                    },
                    "body": {},
                }))
            elif message.get("code") == 200:
                raise OSError("fixture socket write failed")

        def __aiter__(self):
            return self

        async def __anext__(self):
            return await self.incoming.get()

    original_sleep = asyncio.sleep

    async def no_sleep(_seconds):
        await original_sleep(0)

    monkeypatch.setattr(XianyuAutoAsync.asyncio, "sleep", no_sleep)
    socket = _Socket()

    async def run():
        reader = asyncio.create_task(live._websocket_reader_loop(socket))
        with pytest.raises(ConnectionError, match="bootstrap ACK failed"):
            await live.init(socket)
        await reader

    asyncio.run(run())

    lwps = [message.get("lwp") for message in socket.messages if message.get("lwp")]
    assert lwps == ["/reg", "/r/Conversation/listNewestPagination"]
    assert live.message_ack_error_count == 1


def test_direct_message_uses_signed_session_list_after_protocol_400():
    import XianyuAutoAsync

    live = object.__new__(XianyuAutoAsync.XianyuLive)
    live.cookie_id = "account-1"
    live.myid = "seller-1"
    live.direct_message_lock = asyncio.Lock()
    live._direct_conversation_waiters = {}
    live.direct_send_init_error_count = 0
    socket_writes = []
    submitted = []
    remembered = []
    fallback_calls = []

    class _Socket:
        closed = False

        async def send(self, raw):
            request = json.loads(raw)
            socket_writes.append(request.get("lwp"))
            if request.get("lwp") not in {
                "/r/SingleChatConversation/create",
                "/r/Conversation/listNewestPagination",
            }:
                raise AssertionError(f"unexpected socket write: {request.get('lwp')}")
            response = {"headers": {"mid": request["headers"]["mid"]}, "code": 400}
            asyncio.get_running_loop().call_soon(
                live._resolve_direct_conversation_response,
                response,
            )

    async def find_via_session_sync(toid, item_id):
        fallback_calls.append((toid, item_id))
        return "conversation-session-sync"

    async def record_send(ws, cid, toid, text):
        submitted.append((ws, cid, toid, text))

    live.ws = _Socket()
    live._find_direct_conversation_via_session_sync = find_via_session_sync
    live.send_msg = record_send
    live._remember_direct_conversation = lambda toid, item_id, cid: remembered.append(
        (toid, item_id, cid)
    )

    assert asyncio.run(live.send_msg_once("buyer-1", "item-1", "confirmation link")) is True
    assert socket_writes == [
        "/r/SingleChatConversation/create",
        "/r/Conversation/listNewestPagination",
    ]
    assert fallback_calls == [("buyer-1", "item-1")]
    assert submitted == [(
        live.ws,
        "conversation-session-sync",
        "buyer-1",
        "confirmation link",
    )]
    assert remembered == [("buyer-1", "item-1", "conversation-session-sync")]
    assert live._direct_conversation_waiters == {}


def test_direct_message_retries_only_when_previous_attempt_never_submitted(monkeypatch, bridge_database):
    import XianyuAutoAsync

    monkeypatch.setenv("XIANYU_INVITE_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("XIANYU_INVITE_BRIDGE_SECRET", "bridge-test-secret")
    invite_bridge.db_manager.enabled_items.add(("account-1", "item-1"))
    invite_bridge._seen_nonces.clear()
    database = invite_bridge.db_manager
    database.get_order_by_id = lambda _order_id: {
        "order_id": "order-direct-retry",
        "cookie_id": "account-1",
        "item_id": "item-1",
        "buyer_id": "buyer-1",
        "chat_id": "direct:order-direct-retry",
        "order_status": "pending_ship",
    }

    class _Socket:
        closed = False

    class _Live:
        ws = _Socket()

        def __init__(self):
            self.calls = 0

        async def _verify_paid_order_for_delivery(self, **_kwargs):
            return {"allowed": True}

        async def send_msg_once(self, *_args, wait_for_response=False):
            assert wait_for_response is True
            self.calls += 1
            if self.calls == 1:
                raise XianyuAutoAsync.DirectMessageNotSubmitted("not created")
            return {"code": 200}

    live = _Live()
    monkeypatch.setattr(
        XianyuAutoAsync.XianyuLive,
        "get_instance",
        staticmethod(lambda _cookie_id: live),
    )
    payload = {
        "operationKey": "message-direct-retry-1",
        "orderId": "order-direct-retry",
        "cookieId": "account-1",
        "chatId": "direct:order-direct-retry",
        "toUserId": "buyer-1",
        "text": "confirmation link",
        "requestId": "request-direct-retry-1",
    }
    app = FastAPI()
    app.include_router(invite_bridge.invite_bridge_router)

    with TestClient(app) as client:
        first = client.post(
            "/internal/invite/send-message",
            json=payload,
            headers=invite_bridge._signature_headers(payload, "bridge-test-secret"),
        )
        second = client.post(
            "/internal/invite/send-message",
            json=payload,
            headers=invite_bridge._signature_headers(payload, "bridge-test-secret"),
        )

    assert first.json()["state"] == "failed"
    assert first.json()["lastError"] == "direct_conversation_not_submitted"
    assert second.json()["state"] == "succeeded"
    assert second.json()["attempts"] == 2
    assert live.calls == 2


def test_mark_fulfilled_bargain_runs_free_shipping_then_real_consign(monkeypatch, bridge_database):
    """小刀/拼团单必须两段式：免拼成团后仍要调真发货（consign.dummy）。"""
    import XianyuAutoAsync
    import secure_confirm_decrypted

    monkeypatch.setenv("XIANYU_INVITE_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("XIANYU_INVITE_BRIDGE_SECRET", "bridge-test-secret")
    invite_bridge.db_manager.enabled_items.add(("account-1", "item-1"))
    invite_bridge._seen_nonces.clear()
    database = invite_bridge.db_manager
    order = {
        "order_id": "order-bargain",
        "cookie_id": "account-1",
        "item_id": "item-1",
        "buyer_id": "buyer-1",
        "chat_id": "chat-1",
        "order_status": "pending_ship",
        "is_bargain": 1,
        "system_shipped": 0,
    }
    database.get_order_by_id = lambda _order_id: order
    database.get_cookie = lambda _cookie_id: "fixture-cookie"

    def update_order(**values):
        order["order_status"] = values["order_status"]
        order["system_shipped"] = int(values["system_shipped"])
        return True

    database.insert_or_update_order = update_order

    class _Socket:
        closed = False

    class _Live:
        ws = _Socket()

        def __init__(self):
            self.free_shipping_calls = []

        async def auto_freeshipping(self, order_id, item_id, buyer_id):
            self.free_shipping_calls.append((order_id, item_id, buyer_id))
            return {"success": True}

    live = _Live()
    monkeypatch.setattr(
        XianyuAutoAsync.XianyuLive,
        "get_instance",
        staticmethod(lambda _cookie_id: live),
    )

    class _SecureConfirm:
        calls = 0

        def __init__(self, *_args):
            pass

        async def auto_confirm(self, order_id, item_id):
            assert order_id == "order-bargain"
            assert item_id == "item-1"
            self.__class__.calls += 1
            return {"success": True}

    _SecureConfirm.calls = 0
    monkeypatch.setattr(secure_confirm_decrypted, "SecureConfirm", _SecureConfirm)
    payload = {
        "operationKey": "fulfillment-bargain-1",
        "orderId": "order-bargain",
        "cookieId": "account-1",
        "itemId": "item-1",
        "requestId": "request-bargain-1",
    }
    app = FastAPI()
    app.include_router(invite_bridge.invite_bridge_router)

    with TestClient(app) as client:
        response = client.post(
            "/internal/invite/mark-fulfilled",
            json=payload,
            headers=invite_bridge._signature_headers(payload, "bridge-test-secret"),
        )

    assert response.status_code == 200
    assert response.json()["state"] == "succeeded"
    assert response.json()["deliveryMode"] == "free_shipping_then_status_only"
    assert live.free_shipping_calls == [("order-bargain", "item-1", "buyer-1")]
    assert _SecureConfirm.calls == 1
    assert order["order_status"] == "shipped"


def test_mark_fulfilled_bargain_free_shipping_alone_is_not_fulfillment(monkeypatch, bridge_database):
    """免拼只是成团不是发货：真发货失败时整单必须失败，禁止误标已履约。"""
    import XianyuAutoAsync
    import secure_confirm_decrypted

    monkeypatch.setenv("XIANYU_INVITE_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("XIANYU_INVITE_BRIDGE_SECRET", "bridge-test-secret")
    invite_bridge.db_manager.enabled_items.add(("account-1", "item-1"))
    invite_bridge._seen_nonces.clear()
    database = invite_bridge.db_manager
    order = {
        "order_id": "order-bargain",
        "cookie_id": "account-1",
        "item_id": "item-1",
        "buyer_id": "buyer-1",
        "chat_id": "chat-1",
        "order_status": "pending_ship",
        "is_bargain": 1,
        "system_shipped": 0,
    }
    database.get_order_by_id = lambda _order_id: order
    database.get_cookie = lambda _cookie_id: "fixture-cookie"
    database.insert_or_update_order = lambda **_values: pytest.fail(
        "unshipped bargain order must not be stored as shipped"
    )

    class _Socket:
        closed = False

    class _Live:
        ws = _Socket()

        def __init__(self):
            self.free_shipping_calls = []

        async def auto_freeshipping(self, order_id, item_id, buyer_id):
            self.free_shipping_calls.append((order_id, item_id, buyer_id))
            return {"success": True, "already_shipped": True}

    live = _Live()
    monkeypatch.setattr(
        XianyuAutoAsync.XianyuLive,
        "get_instance",
        staticmethod(lambda _cookie_id: live),
    )

    class _SecureConfirm:
        calls = 0

        def __init__(self, *_args):
            pass

        async def auto_confirm(self, order_id, item_id):
            self.__class__.calls += 1
            return {"success": False, "category": "unknown_failure", "error": "发货失败"}

    _SecureConfirm.calls = 0
    monkeypatch.setattr(secure_confirm_decrypted, "SecureConfirm", _SecureConfirm)
    payload = {
        "operationKey": "fulfillment-bargain-noconsign",
        "orderId": "order-bargain",
        "cookieId": "account-1",
        "itemId": "item-1",
        "requestId": "request-bargain-2",
    }
    app = FastAPI()
    app.include_router(invite_bridge.invite_bridge_router)

    with TestClient(app) as client:
        response = client.post(
            "/internal/invite/mark-fulfilled",
            json=payload,
            headers=invite_bridge._signature_headers(payload, "bridge-test-secret"),
        )

    assert response.status_code == 200
    assert response.json()["state"] == "failed"
    assert live.free_shipping_calls == [("order-bargain", "item-1", "buyer-1")]
    assert _SecureConfirm.calls == 1
    assert order["order_status"] == "pending_ship"
    assert order["system_shipped"] == 0


def test_poller_recovers_platform_order_missing_from_local_database(monkeypatch):
    import XianyuAutoAsync
    import invite_bridge_poller as poller_module

    class _PollerDatabase:
        def __init__(self):
            self.orders = {}
            self.enabled = {("account-1", "item-1")}

        def get_all_cookies(self):
            return {"account-1": "fixture-cookie"}

        def get_cookie_details(self, _cookie_id):
            return {"browser_user_agent": "fixture-agent"}

        def get_order_by_id(self, order_id):
            return self.orders.get(order_id)

        def find_chat_id_by_buyer(self, _cookie_id, _buyer_id):
            return None

        def insert_or_update_order(self, **values):
            current = self.orders.setdefault(values["order_id"], {})
            current.update(values)
            current.setdefault("system_shipped", 0)
            return True

        def apply_order_sync_update(self, **values):
            current = self.orders[values["order_id"]]
            current["order_status"] = values["incoming_status"]
            current["paid_amount_fen"] = values["paid_amount_fen"]
            current["ordered_at_utc"] = values["ordered_at"][0]
            current["ordered_at_source"] = values["ordered_at"][1]
            return {"updated": True, "status_changed": False, "details_changed": True}

        def get_orders_by_cookie(self, cookie_id, limit=200):
            del limit
            return [row for row in self.orders.values() if row.get("cookie_id") == cookie_id]

    class _Lock:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    class _OrderClient:
        def __init__(self, **_kwargs):
            pass

        async def discover(self, **_kwargs):
            return {
                "success": True,
                "orders": [{
                    "order_id": "order-recovered",
                    "item_id": "item-1",
                    "buyer_id": "buyer-1",
                    "amount": "3.88",
                    "quantity": "1",
                    "item_title": "Codex invitation",
                    "order_status": "pending_ship",
                    "order_business_type": "ordinary",
                    "created_at": "2026-08-11 12:34:56",
                }],
            }

    class _Socket:
        closed = False

    class _Live:
        ws = _Socket()

        async def _verify_paid_order_for_delivery(self, **_kwargs):
            return {"allowed": True}

    database = _PollerDatabase()
    sent = []
    poller = poller_module.InviteBridgePoller()

    async def record_event(payload):
        sent.append(payload)

    monkeypatch.setattr(poller_module, "db_manager", database)
    monkeypatch.setattr(poller_module, "_allowed_item_ids", lambda _cookie_id: {"item-1"})
    monkeypatch.setattr(poller_module, "XianyuOrderListClient", _OrderClient)
    monkeypatch.setattr(poller_module, "get_order_sync_lock", lambda _cookie_id: _Lock())
    monkeypatch.setattr(poller_module, "_send_order_event_to_invite", record_event)
    monkeypatch.setattr(
        poller_module, "_message_operation_exists", lambda _order_id, _cookie_id: False
    )
    monkeypatch.setattr(XianyuAutoAsync.XianyuLive, "get_instance", staticmethod(lambda _cookie_id: _Live()))

    async def run():
        first = await poller.scan_once()
        second = await poller.scan_once()
        return first, second

    first, second = asyncio.run(run())

    assert first == 1
    assert second == 0
    assert sent[0]["chatId"] == "direct:order-recovered"
    assert database.orders["order-recovered"]["order_status"] == "pending_ship"
    assert database.orders["order-recovered"]["paid_amount_fen"] == 388
    assert database.orders["order-recovered"]["ordered_at_utc"] == pytest.approx(
        1786422896.0
    )
    assert database.orders["order-recovered"]["ordered_at_source"] == "cst_string"


def test_poller_discovers_accounts_in_bounded_parallel_and_times_from_completion(
    monkeypatch,
):
    import invite_bridge_poller as poller_module

    class _Database:
        def get_all_cookies(self):
            return {f"account-{index}": "fixture-cookie" for index in range(5)}

        def get_cookie_details(self, _cookie_id):
            return {}

    class _Lock:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    class _OrderClient:
        active = 0
        max_active = 0
        calls = []

        def __init__(self, **_kwargs):
            pass

        async def discover(self, **kwargs):
            type(self).active += 1
            type(self).max_active = max(type(self).max_active, type(self).active)
            type(self).calls.append(kwargs["cookie_id"])
            await asyncio.sleep(0.03)
            type(self).active -= 1
            return {"success": True, "orders": []}

    poller = poller_module.InviteBridgePoller()
    monkeypatch.setattr(poller_module, "db_manager", _Database())
    monkeypatch.setattr(poller_module, "_allowed_item_ids", lambda _id: {"item-1"})
    monkeypatch.setattr(poller_module, "XianyuOrderListClient", _OrderClient)
    monkeypatch.setattr(poller_module, "get_order_sync_lock", lambda _id: _Lock())

    started_at = time.time()
    asyncio.run(poller._discover_platform_orders())

    assert _OrderClient.max_active == 3
    assert len(_OrderClient.calls) == 5
    assert set(poller._last_discovery_at) == {
        f"account-{index}" for index in range(5)
    }
    assert min(poller._last_discovery_at.values()) >= started_at + 0.02

    asyncio.run(poller._discover_platform_orders())
    assert len(_OrderClient.calls) == 5


def test_poller_lead_discovery_never_stages_or_emits_event(monkeypatch):
    import XianyuAutoAsync
    import invite_bridge_poller as poller_module

    class _Database:
        def __init__(self):
            self.orders = {}
            self.writes = []

        def get_all_cookies(self):
            return {"account-1": "fixture-cookie"}

        def get_cookie_details(self, _cookie_id):
            return {"browser_user_agent": "fixture-agent"}

        def get_order_by_id(self, order_id):
            return self.orders.get(order_id)

        def find_chat_id_by_buyer(self, _cookie_id, _buyer_id):
            return None

        def insert_or_update_order(self, **values):
            self.writes.append(("insert", values["order_id"]))
            self.orders[values["order_id"]] = {**values, "system_shipped": 0}
            return True

        def apply_order_sync_update(self, **values):
            self.writes.append(("sync", values["order_id"]))
            self.orders[values["order_id"]]["order_status"] = values["incoming_status"]
            return {"updated": True}

        def get_orders_by_cookie(self, cookie_id, limit=200):
            del limit
            return [
                order
                for order in self.orders.values()
                if order.get("cookie_id") == cookie_id
            ]

    class _Lock:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    discovery_calls = []

    class _OrderClient:
        def __init__(self, **_kwargs):
            pass

        async def discover(self, **kwargs):
            discovery_calls.append(kwargs["cookie_id"])
            return {
                "success": True,
                "orders": [{
                    "order_id": "order-lead",
                    "item_id": "item-1",
                    "buyer_id": "buyer-1",
                    "amount": "0.00",
                    "quantity": "1",
                    "order_status": "pending_ship",
                    "order_business_type": "lead",
                }],
            }

    class _Socket:
        closed = False

    class _Live:
        ws = _Socket()

        async def _verify_paid_order_for_delivery(self, **_kwargs):
            return {"allowed": True}

    database = _Database()
    events = []
    poller = poller_module.InviteBridgePoller()

    async def record_event(payload):
        events.append(payload)

    monkeypatch.setattr(poller_module, "db_manager", database)
    monkeypatch.setattr(poller_module, "_allowed_item_ids", lambda _id: {"item-1"})
    monkeypatch.setattr(poller_module, "XianyuOrderListClient", _OrderClient)
    monkeypatch.setattr(poller_module, "get_order_sync_lock", lambda _id: _Lock())
    monkeypatch.setattr(poller_module, "_message_operation_exists", lambda *_args: False)
    monkeypatch.setattr(poller_module, "_send_order_event_to_invite", record_event)
    monkeypatch.setattr(
        XianyuAutoAsync.XianyuLive,
        "get_instance",
        staticmethod(lambda _cookie_id: _Live()),
    )

    assert asyncio.run(poller.scan_once()) == 0
    assert discovery_calls == ["account-1"]
    assert database.writes == []
    assert events == []
    assert poller._seen == set()


def test_poller_preserves_verified_chat_id_on_platform_rediscovery(monkeypatch):
    import invite_bridge_poller as poller_module

    class _Database:
        def __init__(self):
            self.order = {
                "order_id": "order-existing",
                "cookie_id": "account-1",
                "item_id": "item-1",
                "buyer_id": "buyer-1",
                "chat_id": "conversation-real",
                "order_status": "pending_ship",
            }

        def get_order_by_id(self, order_id):
            assert order_id == "order-existing"
            return dict(self.order)

        def find_chat_id_by_buyer(self, _cookie_id, _buyer_id):
            return None

        def insert_or_update_order(self, **values):
            self.order.update(values)
            return True

        def apply_order_sync_update(self, **_values):
            return {"updated": True, "status_changed": False, "details_changed": False}

    database = _Database()
    monkeypatch.setattr(poller_module, "db_manager", database)
    monkeypatch.setattr(
        poller_module, "_allowed_item_ids", lambda _cookie_id: {"item-1"}
    )

    poller = poller_module.InviteBridgePoller()
    assert poller.stage_order(
        cookie_id="account-1",
        order_id="order-existing",
        item_id="item-1",
        buyer_id="buyer-1",
        chat_id="direct:order-existing",
        order_business_type="ordinary",
    )

    assert database.order["chat_id"] == "conversation-real"


def test_poller_never_stages_lead_or_unconfirmed_order_types(monkeypatch):
    import invite_bridge_poller as poller_module

    class _UnexpectedDatabase:
        def __getattr__(self, name):
            pytest.fail(f"nonordinary order touched persistence: {name}")

    monkeypatch.setattr(poller_module, "db_manager", _UnexpectedDatabase())
    poller = poller_module.InviteBridgePoller()

    for business_type in ("lead", "unknown", ""):
        assert not poller.stage_order(
            cookie_id="account-1",
            order_id=f"order-{business_type or 'missing'}",
            item_id="item-1",
            buyer_id="buyer-1",
            order_business_type=business_type,
        )


def test_poller_prefers_buyer_conversation_over_provisional_chat(monkeypatch):
    import invite_bridge_poller as poller_module

    class _Database:
        def find_chat_id_by_buyer(self, _cookie_id, _buyer_id):
            return "conversation-real"

    monkeypatch.setattr(poller_module, "db_manager", _Database())

    assert poller_module.InviteBridgePoller._chat_reference(
        "account-1", "order-chat", "buyer-1", "direct:order-chat"
    ) == "conversation-real"


def test_poller_isolates_failed_order_and_continues(monkeypatch):
    import XianyuAutoAsync
    import invite_bridge_poller as poller_module

    class _Database:
        orders = {
            "order-failed": {
                "order_id": "order-failed", "cookie_id": "account-1", "item_id": "item-1",
                "buyer_id": "buyer-1", "chat_id": "chat-1", "order_status": "pending_ship",
                "amount": "3.88", "quantity": "1", "system_shipped": 0,
            },
            "order-ok": {
                "order_id": "order-ok", "cookie_id": "account-1", "item_id": "item-1",
                "buyer_id": "buyer-2", "chat_id": "chat-2", "order_status": "pending_ship",
                "amount": "3.88", "quantity": "1", "system_shipped": 0,
            },
        }

        def get_all_cookies(self):
            return {"account-1": "fixture-cookie"}

        def get_orders_by_cookie(self, _cookie_id, limit=200):
            del limit
            return list(self.orders.values())

        def get_order_by_id(self, order_id):
            return self.orders.get(order_id)

        def find_chat_id_by_buyer(self, _cookie_id, _buyer_id):
            return None

    class _Socket:
        closed = False

    class _Live:
        ws = _Socket()

        async def _verify_paid_order_for_delivery(self, **_kwargs):
            return {"allowed": True}

    database = _Database()
    poller = poller_module.InviteBridgePoller()
    sent = []

    async def send_event(payload):
        if payload["orderId"] == "order-failed":
            raise RuntimeError("fixture failure")
        sent.append(payload["orderId"])

    monkeypatch.setattr(poller, "_discover_platform_orders", lambda: asyncio.sleep(0))
    monkeypatch.setattr(poller_module, "db_manager", database)
    monkeypatch.setattr(poller_module, "_allowed_item_ids", lambda _cookie_id: {"item-1"})
    monkeypatch.setattr(poller_module, "_send_order_event_to_invite", send_event)
    monkeypatch.setattr(
        poller_module, "_message_operation_exists", lambda _order_id, _cookie_id: False
    )
    monkeypatch.setattr(XianyuAutoAsync.XianyuLive, "get_instance", staticmethod(lambda _cookie_id: _Live()))

    assert asyncio.run(poller.scan_once()) == 1
    assert sent == ["order-ok"]
    failed_event = "xianyu:" + hashlib.sha256(b"account-1:order-failed:paid").hexdigest()
    assert failed_event not in poller._seen


def test_poller_trusted_direct_scan_skips_discovery_and_payment_recheck(monkeypatch):
    import XianyuAutoAsync
    import invite_bridge_poller as poller_module

    class _Database:
        orders = {
            order_id: {
                "order_id": order_id,
                "cookie_id": "account-1",
                "item_id": "item-1",
                "buyer_id": buyer_id,
                "chat_id": chat_id,
                "order_status": "pending_ship",
                "amount": "3.88",
                "quantity": "1",
                "system_shipped": 0,
            }
            for order_id, buyer_id, chat_id in (
                ("order-other", "buyer-2", "chat-2"),
                ("order-trusted", "buyer-1", "chat-1"),
            )
        }

        def get_all_cookies(self):
            return {"account-1": "fixture-cookie"}

        def get_orders_by_cookie(self, _cookie_id, limit=200):
            del limit
            return list(self.orders.values())

        def get_order_by_id(self, order_id):
            return dict(self.orders[order_id])

        def find_chat_id_by_buyer(self, _cookie_id, _buyer_id):
            return None

    class _Socket:
        closed = False

    class _Live:
        ws = _Socket()

        def __init__(self):
            self.verify_calls = []

        async def _verify_paid_order_for_delivery(self, **kwargs):
            self.verify_calls.append(kwargs["order_id"])
            return {"allowed": False, "status": "unknown", "error_code": "retry"}

    database = _Database()
    live = _Live()
    poller = poller_module.InviteBridgePoller()
    sent = []

    async def unexpected_discovery():
        raise AssertionError("direct scan performed platform discovery")

    async def send_event(payload):
        sent.append(payload["orderId"])

    monkeypatch.setattr(poller, "_discover_platform_orders", unexpected_discovery)
    monkeypatch.setattr(poller_module, "db_manager", database)
    monkeypatch.setattr(poller_module, "_allowed_item_ids", lambda _cookie_id: {"item-1"})
    monkeypatch.setattr(poller_module, "_message_operation_exists", lambda *_args: False)
    monkeypatch.setattr(poller_module, "_send_order_event_to_invite", send_event)
    monkeypatch.setattr(
        XianyuAutoAsync.XianyuLive,
        "get_instance",
        staticmethod(lambda _cookie_id: live),
    )

    async def run():
        await poller._scan_lock.acquire()
        try:
            first = await asyncio.wait_for(
                poller.scan_once(
                    discover=False,
                    trusted_order_ids={"order-trusted"},
                ),
                timeout=0.2,
            )
        finally:
            poller._scan_lock.release()
        poller._seen.clear()
        second = await poller.scan_once(discover=False)
        return first, second

    first, second = asyncio.run(run())
    assert first == 1
    assert sent == ["order-trusted"]
    assert live.verify_calls == ["order-other", "order-trusted"]

    # Trust is scoped to the call; a later ordinary scan must re-check payment.
    assert second == 0
    assert "xianyu:" + hashlib.sha256(
        b"account-1:order-trusted:paid"
    ).hexdigest() not in poller._seen


def test_poller_verified_order_path_reads_only_target_order(monkeypatch):
    import XianyuAutoAsync
    import invite_bridge_poller as poller_module

    class _Database:
        def __init__(self):
            self.order = {
                "order_id": "order-target",
                "cookie_id": "account-1",
                "item_id": "item-1",
                "buyer_id": "buyer-1",
                "chat_id": "chat-1",
                "order_status": "pending_ship",
                "amount": "3.88",
                "quantity": "1",
                "system_shipped": 0,
            }
            self.detail_calls = 0

        def get_order_by_id(self, order_id):
            self.detail_calls += 1
            assert order_id == "order-target"
            return dict(self.order)

        def get_all_cookies(self):
            raise AssertionError("verified order path scanned all accounts")

        def get_orders_by_cookie(self, *_args, **_kwargs):
            raise AssertionError("verified order path scanned the order list")

        def find_chat_id_by_buyer(self, _cookie_id, _buyer_id):
            return None

    class _Socket:
        closed = False

    class _Live:
        ws = _Socket()

    database = _Database()
    poller = poller_module.InviteBridgePoller()
    sent = []

    async def send_event(payload):
        sent.append(payload)

    monkeypatch.setattr(poller_module, "db_manager", database)
    monkeypatch.setattr(poller_module, "_allowed_item_ids", lambda _id: {"item-1"})
    monkeypatch.setattr(poller_module, "_message_operation_exists", lambda *_args: False)
    monkeypatch.setattr(poller_module, "_send_order_event_to_invite", send_event)
    monkeypatch.setattr(
        XianyuAutoAsync.XianyuLive,
        "get_instance",
        staticmethod(lambda _cookie_id: _Live()),
    )

    sent_count = asyncio.run(
        poller.scan_trusted_order(
            cookie_id="account-1",
            order_id="order-target",
            item_id="item-1",
            buyer_id="buyer-1",
            chat_id="chat-1",
            payment_check={
                "allowed": True,
                "status": "pending_ship",
                "business_type": "ordinary",
            },
        )
    )

    assert sent_count == 1
    assert database.detail_calls == 1
    assert [payload["orderId"] for payload in sent] == ["order-target"]


def test_paid_invite_notice_calls_only_the_verified_order_path(monkeypatch):
    import XianyuAutoAsync
    import db_manager as db_module
    import invite_bridge_poller as poller_module

    class _Database:
        def get_item_info(self, cookie_id, item_id):
            assert (cookie_id, item_id) == ("account-1", "item-1")
            return {"item_id": item_id}

    class _Poller:
        def __init__(self):
            self.staged = []
            self.direct = []
            self.fanout = []

        def stage_order(self, **kwargs):
            self.staged.append(kwargs)
            return True

        async def scan_trusted_order(self, **kwargs):
            self.direct.append(kwargs)
            return 1

        async def scan_buyer_orders(self, **kwargs):
            self.fanout.append(kwargs)
            return 0

        async def scan_once(self, **_kwargs):
            raise AssertionError("paid invite notice entered the batch scanner")

    class _Live:
        cookie_id = "account-1"
        _extract_order_id = staticmethod(lambda _message: "order-1")

        async def _verify_paid_order_for_delivery(self, **_kwargs):
            return {
                "allowed": True,
                "status": "pending_ship",
                "business_type": "ordinary",
                "amount": "3.88",
                "quantity": 1,
            }

    poller = _Poller()
    monkeypatch.setattr(db_module, "db_manager", _Database())
    monkeypatch.setattr(XianyuAutoAsync, "_invite_bridge_owns_item", lambda *_args: True)
    monkeypatch.setattr(poller_module, "invite_bridge_poller", poller)

    asyncio.run(
        XianyuAutoAsync.XianyuLive._handle_auto_delivery(
            _Live(),
            websocket=object(),
            message={},
            send_user_name="buyer",
            send_user_id="buyer-1",
            item_id="item-1",
            chat_id="chat-1",
            msg_time="now",
        )
    )

    assert len(poller.staged) == 1
    assert [call["order_id"] for call in poller.direct] == ["order-1"]
    # 热路径完成可信投递后必须立刻发起同买家定向补发现，且排除本单。
    assert [call["buyer_id"] for call in poller.fanout] == ["buyer-1"]
    assert poller.fanout[0]["exclude_order_ids"] == {"order-1"}


@pytest.mark.parametrize(
    "case",
    (
        "account_mismatch",
        "item_mismatch",
        "buyer_mismatch",
        "chat_mismatch",
        "system_shipped",
        "status_not_pending",
        "payment_unconfirmed",
        "listener_offline",
        "message_operation_exists",
    ),
)
def test_poller_verified_order_path_fails_closed(monkeypatch, case):
    import XianyuAutoAsync
    import invite_bridge_poller as poller_module

    class _Database:
        def __init__(self):
            self.order = {
                "order_id": "order-guard",
                "cookie_id": "account-1",
                "item_id": "item-1",
                "buyer_id": "buyer-1",
                "chat_id": "chat-1",
                "order_status": "pending_ship",
                "amount": "3.88",
                "quantity": "1",
                "system_shipped": 0,
            }

        def get_order_by_id(self, _order_id):
            return dict(self.order)

        def find_chat_id_by_buyer(self, _cookie_id, _buyer_id):
            return None

    class _Socket:
        closed = False

    class _Live:
        ws = _Socket()

    database = _Database()
    payment_check = {
        "allowed": True,
        "status": "pending_ship",
        "business_type": "ordinary",
    }
    live = _Live()
    if case == "account_mismatch":
        database.order["cookie_id"] = "account-2"
    elif case == "item_mismatch":
        database.order["item_id"] = "item-2"
    elif case == "buyer_mismatch":
        database.order["buyer_id"] = "buyer-2"
    elif case == "chat_mismatch":
        database.order["chat_id"] = "chat-2"
    elif case == "system_shipped":
        database.order["system_shipped"] = 1
    elif case == "status_not_pending":
        database.order["order_status"] = "shipped"
    elif case == "payment_unconfirmed":
        payment_check = {"allowed": False, "status": "unknown", "business_type": "ordinary"}
    elif case == "listener_offline":
        live.ws.closed = True

    poller = poller_module.InviteBridgePoller()
    sent = []
    monkeypatch.setattr(poller_module, "db_manager", database)
    monkeypatch.setattr(poller_module, "_allowed_item_ids", lambda _id: {"item-1"})
    monkeypatch.setattr(
        poller_module,
        "_message_operation_exists",
        lambda *_args: case == "message_operation_exists",
    )
    monkeypatch.setattr(poller_module, "_send_order_event_to_invite", lambda payload: sent.append(payload))
    monkeypatch.setattr(
        XianyuAutoAsync.XianyuLive,
        "get_instance",
        staticmethod(lambda _cookie_id: live),
    )

    assert asyncio.run(
        poller.scan_trusted_order(
            cookie_id="account-1",
            order_id="order-guard",
            item_id="item-1",
            buyer_id="buyer-1",
            chat_id="chat-1",
            payment_check=payment_check,
        )
    ) == 0
    assert sent == []


def test_poller_discovery_failure_does_not_block_local_pending_order(monkeypatch):
    import XianyuAutoAsync
    import invite_bridge_poller as poller_module

    class _Database:
        order = {
            "order_id": "order-local",
            "cookie_id": "account-good",
            "item_id": "item-1",
            "buyer_id": "buyer-1",
            "chat_id": "chat-1",
            "order_status": "pending_ship",
            "amount": "3.88",
            "quantity": "1",
            "system_shipped": 0,
        }

        def get_all_cookies(self):
            return {
                "account-bad": "fixture-cookie-bad",
                "account-good": "fixture-cookie-good",
            }

        def get_cookie_details(self, _cookie_id):
            return {"browser_user_agent": "fixture-agent"}

        def get_orders_by_cookie(self, cookie_id, limit=200):
            del limit
            return [self.order] if cookie_id == "account-good" else []

        def get_order_by_id(self, _order_id):
            return dict(self.order)

        def find_chat_id_by_buyer(self, _cookie_id, _buyer_id):
            return None

    class _Lock:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    class _OrderClient:
        def __init__(self, **_kwargs):
            pass

        async def discover(self, *, cookie_id, **_kwargs):
            if cookie_id == "account-bad":
                raise RuntimeError("fixture discovery failure")
            return {"success": True, "orders": []}

    class _Socket:
        closed = False

    class _Live:
        ws = _Socket()

        async def _verify_paid_order_for_delivery(self, **_kwargs):
            return {"allowed": True}

    database = _Database()
    poller = poller_module.InviteBridgePoller()
    sent = []

    async def send_event(payload):
        sent.append(payload["orderId"])

    monkeypatch.setattr(poller_module, "db_manager", database)
    monkeypatch.setattr(poller_module, "_allowed_item_ids", lambda _cookie_id: {"item-1"})
    monkeypatch.setattr(poller_module, "XianyuOrderListClient", _OrderClient)
    monkeypatch.setattr(poller_module, "get_order_sync_lock", lambda _cookie_id: _Lock())
    monkeypatch.setattr(poller_module, "_message_operation_exists", lambda *_args: False)
    monkeypatch.setattr(poller_module, "_send_order_event_to_invite", send_event)
    monkeypatch.setattr(
        XianyuAutoAsync.XianyuLive,
        "get_instance",
        staticmethod(lambda _cookie_id: _Live()),
    )

    assert asyncio.run(poller.scan_once()) == 1
    assert sent == ["order-local"]


def test_poller_untrusted_payment_failure_remains_retryable(monkeypatch):
    import XianyuAutoAsync
    import invite_bridge_poller as poller_module

    class _Database:
        order = {
            "order_id": "order-unpaid",
            "cookie_id": "account-1",
            "item_id": "item-1",
            "buyer_id": "buyer-1",
            "chat_id": "chat-1",
            "order_status": "pending_ship",
            "amount": "3.88",
            "quantity": "1",
            "system_shipped": 0,
        }

        def get_all_cookies(self):
            return {"account-1": "fixture-cookie"}

        def get_orders_by_cookie(self, _cookie_id, limit=200):
            del limit
            return [self.order]

        def get_order_by_id(self, _order_id):
            return dict(self.order)

        def find_chat_id_by_buyer(self, _cookie_id, _buyer_id):
            return None

    class _Socket:
        closed = False

    class _Live:
        ws = _Socket()

        async def _verify_paid_order_for_delivery(self, **_kwargs):
            return {
                "allowed": False,
                "status": "pending",
                "error_code": "not_paid",
            }

    database = _Database()
    poller = poller_module.InviteBridgePoller()
    monkeypatch.setattr(poller, "_discover_platform_orders", lambda: asyncio.sleep(0))
    monkeypatch.setattr(poller_module, "db_manager", database)
    monkeypatch.setattr(poller_module, "_allowed_item_ids", lambda _cookie_id: {"item-1"})
    monkeypatch.setattr(poller_module, "_message_operation_exists", lambda *_args: False)
    monkeypatch.setattr(
        poller_module,
        "_send_order_event_to_invite",
        lambda _payload: pytest.fail("unpaid order was sent"),
    )
    monkeypatch.setattr(
        XianyuAutoAsync.XianyuLive,
        "get_instance",
        staticmethod(lambda _cookie_id: _Live()),
    )

    assert asyncio.run(poller.scan_once()) == 0
    event_id = "xianyu:" + hashlib.sha256(
        b"account-1:order-unpaid:paid"
    ).hexdigest()
    assert event_id not in poller._seen


def test_poller_skips_order_event_when_message_operation_proves_acceptance(
    monkeypatch,
):
    import XianyuAutoAsync
    import invite_bridge_poller as poller_module

    connection = sqlite3.connect(":memory:", check_same_thread=False)
    _invite_bridge_operations_v1(connection.cursor(), ":memory:")
    connection.execute(
        "INSERT INTO invite_bridge_operations "
        "(operation_key, operation_type, order_id, cookie_id, request_hash, status, "
        "created_at, updated_at) VALUES "
        "('message-existing', 'message', 'order-existing', 'account-1', 'hash', "
        "'submitted', 0, 0)"
    )
    connection.commit()
    database = _DatabaseStub(connection)
    order = {
        "order_id": "order-existing",
        "cookie_id": "account-1",
        "item_id": "item-1",
        "buyer_id": "buyer-1",
        "chat_id": "conversation-real",
        "order_status": "pending_ship",
        "amount": "3.88",
        "quantity": "1",
        "system_shipped": 0,
    }
    database.get_all_cookies = lambda: {"account-1": "fixture-cookie"}
    database.get_orders_by_cookie = lambda _cookie_id, limit=200: [order]
    database.get_order_by_id = lambda _order_id: order
    database.find_chat_id_by_buyer = lambda _cookie_id, _buyer_id: None

    poller = poller_module.InviteBridgePoller()

    async def unexpected_send(_payload):
        raise AssertionError("accepted order event was sent again")

    monkeypatch.setattr(poller, "_discover_platform_orders", lambda: asyncio.sleep(0))
    monkeypatch.setattr(poller_module, "db_manager", database)
    monkeypatch.setattr(poller_module, "_allowed_item_ids", lambda _cookie_id: {"item-1"})
    monkeypatch.setattr(poller_module, "_send_order_event_to_invite", unexpected_send)
    monkeypatch.setattr(
        XianyuAutoAsync.XianyuLive,
        "get_instance",
        staticmethod(lambda _cookie_id: pytest.fail("payment verification was reached")),
    )

    assert asyncio.run(poller.scan_once()) == 0
    event_id = "xianyu:" + hashlib.sha256(
        b"account-1:order-existing:paid"
    ).hexdigest()
    assert event_id in poller._seen
    connection.close()


def test_poller_enriches_terminal_order_without_changing_status(monkeypatch):
    import invite_bridge_poller as poller_module

    class _Database:
        order = {
            "order_id": "order-shipped", "cookie_id": "account-1", "item_id": "item-1",
            "buyer_id": "buyer-1", "order_status": "shipped", "system_shipped": 1,
            "paid_amount_fen": None, "ordered_at_utc": None,
        }

        def get_all_cookies(self):
            return {"account-1": "fixture-cookie"}

        def get_cookie_details(self, _cookie_id):
            return {}

        def get_order_by_id(self, _order_id):
            return self.order

        def apply_order_sync_update(self, **values):
            previous = self.order["order_status"]
            self.order["order_status"] = values["incoming_status"]
            self.order["paid_amount_fen"] = values["paid_amount_fen"]
            self.order["ordered_at_utc"] = values["ordered_at"][0]
            self.order["ordered_at_source"] = values["ordered_at"][1]
            return {
                "updated": True,
                "status_changed": previous != values["incoming_status"],
                "details_changed": True,
            }

    class _Lock:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    class _OrderClient:
        def __init__(self, **_kwargs):
            pass

        async def discover(self, **_kwargs):
            return {"success": True, "orders": [{
                "order_id": "order-shipped", "item_id": "item-1", "buyer_id": "buyer-1",
                "order_status": "shipped", "amount": "12.50",
                "created_at": "2026-08-10 09:30:00",
            }]}

    database = _Database()
    poller = poller_module.InviteBridgePoller()
    monkeypatch.setattr(poller_module, "db_manager", database)
    monkeypatch.setattr(poller_module, "_allowed_item_ids", lambda _cookie_id: {"item-1"})
    monkeypatch.setattr(poller_module, "XianyuOrderListClient", _OrderClient)
    monkeypatch.setattr(poller_module, "get_order_sync_lock", lambda _cookie_id: _Lock())

    asyncio.run(poller._discover_platform_orders())

    assert database.order["order_status"] == "shipped"
    assert database.order["paid_amount_fen"] == 1250
    assert database.order["ordered_at_utc"] == pytest.approx(1786325400.0)
    assert database.order["ordered_at_source"] == "cst_string"


def test_poller_enriches_recent_local_terminal_order_without_sending_event(
    monkeypatch,
):
    import invite_bridge_poller as poller_module

    observed_at = time.time() - 3600

    class _Database:
        def __init__(self):
            self.order = {
                "order_id": "order-local-shipped",
                "cookie_id": "account-1",
                "item_id": "item-1",
                "buyer_id": "buyer-1",
                "order_status": "shipped",
                "amount": "9.99",
                "created_at": observed_at,
                "paid_amount_fen": None,
                "ordered_at_utc": None,
                "ordered_at_source": "",
            }

        def get_all_cookies(self):
            return {"account-1": "fixture-cookie"}

        def get_orders_by_cookie(self, _cookie_id, limit=200):
            del limit
            return [self.order]

        def get_order_by_id(self, _order_id):
            return dict(self.order)

        def apply_order_sync_update(self, **values):
            self.order["order_status"] = values["incoming_status"]
            self.order["paid_amount_fen"] = values["paid_amount_fen"]
            self.order["ordered_at_utc"] = values["ordered_at"][0]
            self.order["ordered_at_source"] = values["ordered_at"][1]
            return {"updated": True, "status_changed": False, "details_changed": True}

    database = _Database()
    poller = poller_module.InviteBridgePoller()
    monkeypatch.setattr(
        poller, "_discover_platform_orders", lambda: asyncio.sleep(0)
    )
    monkeypatch.setattr(poller_module, "db_manager", database)
    monkeypatch.setattr(
        poller_module, "_allowed_item_ids", lambda _cookie_id: {"item-1"}
    )

    assert asyncio.run(poller.scan_once()) == 0
    assert database.order["order_status"] == "shipped"
    assert database.order["paid_amount_fen"] == 999
    assert database.order["ordered_at_utc"] == pytest.approx(observed_at)
    assert database.order["ordered_at_source"] == "epoch"


# ---------------------------------------------------------------------------
# 付款核验 order_not_observed 短退避重试（热路径提速修复）
# ---------------------------------------------------------------------------


def _hot_path_retry_setup(monkeypatch, verify_results):
    """搭一个只覆盖邀请热路径核验重试的最小环境，返回 (live, poller, sleeps)。"""
    import XianyuAutoAsync
    import db_manager as db_module
    import invite_bridge_poller as poller_module

    class _Database:
        def get_item_info(self, _cookie_id, item_id):
            return {"item_id": item_id}

    class _Poller:
        def __init__(self):
            self.staged = []
            self.direct = []
            self.fanout = []

        def stage_order(self, **kwargs):
            self.staged.append(kwargs)
            return True

        async def scan_trusted_order(self, **kwargs):
            self.direct.append(kwargs)
            return 1

        async def scan_buyer_orders(self, **kwargs):
            self.fanout.append(kwargs)
            return 0

        async def scan_once(self, **_kwargs):
            raise AssertionError("hot path entered the batch scanner")

    class _Live:
        cookie_id = "account-1"
        _extract_order_id = staticmethod(lambda _message: "order-1")

        def __init__(self):
            self.verify_calls = 0

        async def _verify_paid_order_for_delivery(self, **_kwargs):
            result = verify_results[min(self.verify_calls, len(verify_results) - 1)]
            self.verify_calls += 1
            return dict(result)

    sleeps = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    poller = _Poller()
    monkeypatch.setattr(db_module, "db_manager", _Database())
    monkeypatch.setattr(XianyuAutoAsync, "_invite_bridge_owns_item", lambda *_args: True)
    monkeypatch.setattr(poller_module, "invite_bridge_poller", poller)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    return _Live(), poller, sleeps


def _run_hot_path(live):
    import XianyuAutoAsync

    asyncio.run(
        XianyuAutoAsync.XianyuLive._handle_auto_delivery(
            live,
            websocket=object(),
            message={},
            send_user_name="buyer",
            send_user_id="buyer-1",
            item_id="item-1",
            chat_id="chat-1",
            msg_time="now",
        )
    )


def test_paid_invite_notice_retries_unobserved_order_then_delivers(monkeypatch):
    not_observed = {
        "allowed": False,
        "status": "unknown",
        "error_code": "order_not_observed",
    }
    allowed = {
        "allowed": True,
        "status": "pending_ship",
        "business_type": "ordinary",
        "amount": "3.88",
        "quantity": 1,
    }
    live, poller, sleeps = _hot_path_retry_setup(
        monkeypatch, [not_observed, not_observed, allowed]
    )

    _run_hot_path(live)

    # 平台列表滞后被 2s/4s 两次短退避吃掉，第三次核验通过后正常投递。
    assert live.verify_calls == 3
    assert sleeps == [2.0, 4.0]
    assert [call["order_id"] for call in poller.staged] == ["order-1"]
    assert [call["order_id"] for call in poller.direct] == ["order-1"]


def test_paid_invite_notice_gives_up_after_bounded_unobserved_retries(monkeypatch):
    not_observed = {
        "allowed": False,
        "status": "unknown",
        "error_code": "order_not_observed",
    }
    live, poller, sleeps = _hot_path_retry_setup(monkeypatch, [not_observed])

    _run_hot_path(live)

    # 1 次首查 + 3 次重试后交还 30 秒兜底轮询，绝不无界重试。
    assert live.verify_calls == 4
    assert sleeps == [2.0, 4.0, 8.0]
    assert poller.staged == []
    assert poller.direct == []
    assert poller.fanout == []


def test_paid_invite_notice_does_not_retry_terminal_verification_failures(monkeypatch):
    for error_code in ("lead_order_not_fulfillable", "requires_login", "not_paid"):
        terminal = {
            "allowed": False,
            "status": "unknown",
            "error_code": error_code,
        }
        live, poller, sleeps = _hot_path_retry_setup(monkeypatch, [terminal])

        _run_hot_path(live)

        # 只有 order_not_observed 值得重试；其它失败立即放弃，不拖热路径。
        assert live.verify_calls == 1, error_code
        assert sleeps == [], error_code
        assert poller.staged == []
        assert poller.direct == []


# ---------------------------------------------------------------------------
# 同买家多单定向发现（scan_buyer_orders fan-out）
# ---------------------------------------------------------------------------


def _pending_row(order_id, buyer_id, item_id="item-1"):
    """普通卖家 NOT_SHIP 待发货页的一行真实结构（ordinary + 待发货）。"""
    return {
        "bizOrderId": order_id,
        "auctionId": item_id,
        "buyerId": buyer_id,
        "buyAmount": "1",
        "totalFee": "3.88",
        "orderStatusMsg": "等待卖家发货",
        "idleBizCode": "6",
        "auctionTitle": "Fixture item",
        "createTime": "2026-08-28 10:00:00",
    }


class _FanoutDatabase:
    def __init__(self):
        self.orders = {}
        self.sync_updates = []

    def get_cookie(self, _cookie_id):
        return "unb=account-1; _m_h5_tk=token_value"

    def get_cookie_details(self, _cookie_id):
        return {"browser_user_agent": "fixture-agent"}

    def get_order_by_id(self, order_id):
        row = self.orders.get(order_id)
        return dict(row) if row else None

    def insert_or_update_order(self, **values):
        self.orders[values["order_id"]] = dict(values)
        return True

    def apply_order_sync_update(self, **values):
        self.sync_updates.append(values)
        return {"updated": True, "status_changed": False, "details_changed": False}

    def find_chat_id_by_buyer(self, _cookie_id, _buyer_id):
        return None


class _AsyncNullLock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


def _fanout_setup(monkeypatch, *, pending_payload, verify_result=None):
    """搭 scan_buyer_orders 的最小环境，返回 (poller, database, calls)。"""
    import XianyuAutoAsync
    import invite_bridge_poller as poller_module

    database = _FanoutDatabase()
    poller = poller_module.InviteBridgePoller()
    calls = {"page": 0, "verify": [], "sent": []}

    async def fake_pending_page(**_kwargs):
        calls["page"] += 1
        return pending_payload

    class _Socket:
        closed = False

    class _Live:
        ws = _Socket()

        async def _verify_paid_order_for_delivery(self, *, order_id, item_id, buyer_id):
            calls["verify"].append((order_id, item_id, buyer_id))
            if verify_result is not None:
                return dict(verify_result)
            return {
                "allowed": True,
                "status": "pending_ship",
                "business_type": "ordinary",
                "amount": "3.88",
                "quantity": 1,
                "item_title": "Fixture item",
                "created_at": "2026-08-28 10:00:00",
            }

    async def send_event(payload):
        calls["sent"].append(payload["orderId"])

    monkeypatch.setattr(poller_module, "db_manager", database)
    monkeypatch.setattr(poller_module, "_allowed_item_ids", lambda _cookie_id: {"item-1"})
    monkeypatch.setattr(poller_module, "fetch_xianyu_pending_order_page", fake_pending_page)
    monkeypatch.setattr(poller_module, "get_order_sync_lock", lambda _cookie_id: _AsyncNullLock())
    monkeypatch.setattr(poller_module, "_message_operation_exists", lambda *_args: False)
    monkeypatch.setattr(poller_module, "_send_order_event_to_invite", send_event)
    monkeypatch.setattr(
        XianyuAutoAsync.XianyuLive,
        "get_instance",
        staticmethod(lambda _cookie_id: _Live()),
    )
    return poller, database, calls


def test_scan_buyer_orders_delivers_sibling_paid_orders(monkeypatch):
    """同买家第 2 笔无独立付款消息时，fan-out 应立即核验并补投递。"""
    payload = {
        "ret": ["SUCCESS::调用成功"],
        "data": {
            "items": [
                _pending_row("9001", "buyer-1"),  # 触发单，必须被排除
                _pending_row("9002", "buyer-1"),  # 同买家第 2 笔，应补投
                _pending_row("9003", "buyer-2"),  # 其他买家，不属于 fan-out
                _pending_row("9004", "buyer-1", item_id="item-other"),  # 非邀请商品
            ]
        },
    }
    poller, database, calls = _fanout_setup(monkeypatch, pending_payload=payload)

    sent = asyncio.run(
        poller.scan_buyer_orders(
            cookie_id="account-1",
            buyer_id="buyer-1",
            chat_id="chat-1",
            exclude_order_ids={"9001"},
        )
    )

    assert sent == 1
    assert calls["page"] == 1
    # 只对同买家、未排除、邀请商品的候选做付款核验，绝不批量扫全量。
    assert calls["verify"] == [("9002", "item-1", "buyer-1")]
    assert calls["sent"] == ["9002"]
    assert database.orders["9002"]["order_status"] == "pending_ship"


def test_scan_buyer_orders_fails_closed_when_payment_unverified(monkeypatch):
    payload = {
        "ret": ["SUCCESS::调用成功"],
        "data": {"items": [_pending_row("9002", "buyer-1")]},
    }
    poller, database, calls = _fanout_setup(
        monkeypatch,
        pending_payload=payload,
        verify_result={
            "allowed": False,
            "status": "unknown",
            "error_code": "order_not_observed",
        },
    )

    sent = asyncio.run(
        poller.scan_buyer_orders(cookie_id="account-1", buyer_id="buyer-1")
    )

    # 核验门禁不放行就一单不发、一单不落库，静默交还兜底轮询。
    assert sent == 0
    assert calls["verify"] == [("9002", "item-1", "buyer-1")]
    assert calls["sent"] == []
    assert database.orders == {}


def test_scan_buyer_orders_cooldown_bounds_platform_requests(monkeypatch):
    payload = {"ret": ["SUCCESS::调用成功"], "data": {"items": []}}
    poller, _database, calls = _fanout_setup(monkeypatch, pending_payload=payload)

    first = asyncio.run(
        poller.scan_buyer_orders(cookie_id="account-1", buyer_id="buyer-1")
    )
    second = asyncio.run(
        poller.scan_buyer_orders(cookie_id="account-1", buyer_id="buyer-1")
    )

    # 冷却窗内同买家重复触发不再打平台，防付款消息风暴放大请求量。
    assert (first, second) == (0, 0)
    assert calls["page"] == 1


def test_scan_buyer_orders_marks_session_expired_and_hands_back(monkeypatch):
    import invite_bridge_poller as poller_module

    payload = {"ret": ["FAIL_SYS_SESSION_EXPIRED::Session过期"]}
    poller, _database, calls = _fanout_setup(monkeypatch, pending_payload=payload)
    expired = []
    monkeypatch.setattr(
        poller_module,
        "mark_order_session_expired",
        lambda _db, cookie_id: expired.append(cookie_id),
    )

    sent = asyncio.run(
        poller.scan_buyer_orders(cookie_id="account-1", buyer_id="buyer-1")
    )

    assert sent == 0
    assert calls["sent"] == []
    assert expired == ["account-1"]


# ---------------------------------------------------------------------------
# 对账重发器：本地已发 × 平台待发货 的漂移收敛
# ---------------------------------------------------------------------------


class _ReconcileDatabase:
    def __init__(self, orders):
        self.orders = {order["order_id"]: dict(order) for order in orders}
        self.updates = []

    def get_all_cookies(self):
        return {"account-1": "fixture-cookie"}

    def get_cookie(self, _cookie_id):
        return "fixture-cookie"

    def get_cookie_details(self, _cookie_id):
        return {"browser_user_agent": "fixture-agent"}

    def get_order_by_id(self, order_id):
        row = self.orders.get(order_id)
        return dict(row) if row else None

    def insert_or_update_order(self, **values):
        self.updates.append(values)
        self.orders.setdefault(values["order_id"], {}).update(values)
        return True


def test_ship_reconciler_repairs_local_shipped_platform_pending_drift(monkeypatch):
    """反向验证：制造「本地已发×平台待发货」样本，证明重发器补发货并告警。"""
    import invite_bridge_poller as poller_module
    from loguru import logger as loguru_logger

    drifted = {
        "order_id": "order-drift",
        "cookie_id": "account-1",
        "item_id": "item-1",
        "buyer_id": "buyer-1",
        "order_status": "shipped",
        "system_shipped": 1,
        "is_bargain": 1,
    }
    database = _ReconcileDatabase([drifted])
    poller = poller_module.InviteBridgePoller()

    class _OrderClient:
        def __init__(self, **_kwargs):
            pass

        async def discover(self, **_kwargs):
            return {
                "success": True,
                "orders": [
                    {
                        "order_id": "order-drift",
                        "item_id": "item-1",
                        "buyer_id": "buyer-1",
                        "order_status": "pending_ship",
                        "order_business_type": "ordinary",
                        "amount": "3.88",
                        "quantity": "1",
                    }
                ],
            }

    ship_calls = []

    async def fake_status(cookie_id, order_id, _cookies):
        assert (cookie_id, order_id) == ("account-1", "order-drift")
        return {"success": True, "status": "pending_ship"}

    async def fake_ship(**kwargs):
        ship_calls.append(kwargs)
        return {"success": True, "delivery_mode": "free_shipping_then_status_only"}

    monkeypatch.setattr(poller_module, "db_manager", database)
    monkeypatch.setattr(poller_module, "_allowed_item_ids", lambda _cookie_id: {"item-1"})
    monkeypatch.setattr(poller_module, "XianyuOrderListClient", _OrderClient)
    monkeypatch.setattr(poller_module, "get_order_sync_lock", lambda _cookie_id: _AsyncNullLock())
    monkeypatch.setattr(poller_module, "_fetch_platform_order_status", fake_status)
    monkeypatch.setattr(poller_module, "_execute_platform_ship", fake_ship)

    warnings = []
    sink_id = loguru_logger.add(
        lambda message: warnings.append(str(message)), level="WARNING"
    )
    try:

        async def run():
            await poller._discover_platform_orders()
            return await poller._reconcile_shipped_drift()

        repaired = asyncio.run(run())
    finally:
        loguru_logger.remove(sink_id)

    assert repaired == 1
    # 补发货动作带上了拼团标记与买家身份，且只执行一次。
    assert [
        (call["order_id"], call["is_bargain"], call["buyer_id"]) for call in ship_calls
    ] == [("order-drift", True, "buyer-1")]
    assert database.updates[-1]["order_status"] == "shipped"
    assert database.updates[-1]["system_shipped"] is True
    assert poller._ship_drift == {}
    assert any("平台状态漂移" in message for message in warnings)
    assert any("对账补发货成功" in message for message in warnings)


def test_ship_reconciler_self_heals_when_platform_already_progressed(monkeypatch):
    import invite_bridge_poller as poller_module

    database = _ReconcileDatabase(
        [
            {
                "order_id": "order-drift",
                "cookie_id": "account-1",
                "item_id": "item-1",
                "buyer_id": "buyer-1",
                "order_status": "shipped",
                "system_shipped": 1,
            }
        ]
    )
    poller = poller_module.InviteBridgePoller()
    poller._ship_drift[("account-1", "order-drift")] = {
        "cookie_id": "account-1",
        "order_id": "order-drift",
        "item_id": "item-1",
        "buyer_id": "buyer-1",
    }

    async def fake_status(_cookie_id, _order_id, _cookies):
        return {"success": True, "status": "shipped"}

    async def unexpected_ship(**_kwargs):
        raise AssertionError("platform already progressed but ship was re-executed")

    monkeypatch.setattr(poller_module, "db_manager", database)
    monkeypatch.setattr(poller_module, "_allowed_item_ids", lambda _cookie_id: {"item-1"})
    monkeypatch.setattr(poller_module, "_fetch_platform_order_status", fake_status)
    monkeypatch.setattr(poller_module, "_execute_platform_ship", unexpected_ship)

    assert asyncio.run(poller._reconcile_shipped_drift()) == 0
    assert poller._ship_drift == {}
    assert database.updates == []


def test_ship_reconciler_keeps_candidate_when_recheck_fails_closed(monkeypatch):
    import invite_bridge_poller as poller_module

    database = _ReconcileDatabase([])
    poller = poller_module.InviteBridgePoller()
    candidate = {
        "cookie_id": "account-1",
        "order_id": "order-drift",
        "item_id": "item-1",
        "buyer_id": "buyer-1",
    }
    poller._ship_drift[("account-1", "order-drift")] = dict(candidate)

    async def fake_status(_cookie_id, _order_id, _cookies):
        return {"success": False, "error": "platform detail fetch failed"}

    async def unexpected_ship(**_kwargs):
        raise AssertionError("platform state unknown but ship was executed blindly")

    monkeypatch.setattr(poller_module, "db_manager", database)
    monkeypatch.setattr(poller_module, "_allowed_item_ids", lambda _cookie_id: {"item-1"})
    monkeypatch.setattr(poller_module, "_fetch_platform_order_status", fake_status)
    monkeypatch.setattr(poller_module, "_execute_platform_ship", unexpected_ship)

    assert asyncio.run(poller._reconcile_shipped_drift()) == 0
    # 查不清平台状态就保留候选下轮再试，绝不盲发。
    assert ("account-1", "order-drift") in poller._ship_drift
    assert database.updates == []


def test_ship_reconciler_respects_per_account_budget_and_interval(monkeypatch):
    import invite_bridge_poller as poller_module

    database = _ReconcileDatabase([])
    poller = poller_module.InviteBridgePoller()
    for index in range(7):
        order_id = f"order-drift-{index}"
        poller._ship_drift[("account-1", order_id)] = {
            "cookie_id": "account-1",
            "order_id": order_id,
            "item_id": "item-1",
            "buyer_id": "buyer-1",
        }

    ship_calls = []

    async def fake_status(_cookie_id, _order_id, _cookies):
        return {"success": True, "status": "pending_ship"}

    async def fake_ship(**kwargs):
        ship_calls.append(kwargs["order_id"])
        return {"success": True, "delivery_mode": "status_only"}

    monkeypatch.setattr(poller_module, "db_manager", database)
    monkeypatch.setattr(poller_module, "_allowed_item_ids", lambda _cookie_id: {"item-1"})
    monkeypatch.setattr(poller_module, "_fetch_platform_order_status", fake_status)
    monkeypatch.setattr(poller_module, "_execute_platform_ship", fake_ship)

    repaired_first = asyncio.run(poller._reconcile_shipped_drift())
    repaired_second = asyncio.run(poller._reconcile_shipped_drift())

    # 每轮每账号最多补 5 笔；未到间隔的下一轮直接空转，剩余留给后续轮。
    assert repaired_first == poller_module.SHIP_RECONCILE_MAX_PER_ACCOUNT
    assert len(ship_calls) == poller_module.SHIP_RECONCILE_MAX_PER_ACCOUNT
    assert repaired_second == 0
    assert len(poller._ship_drift) == 7 - poller_module.SHIP_RECONCILE_MAX_PER_ACCOUNT
