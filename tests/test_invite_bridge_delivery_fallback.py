"""mark-fulfilled 确认发货失败自愈测试。

拼单标记可能在轮询补单时丢失（平台订单列表不返回该标记），导致把拼单当普通单、
或把普通单当拼单，用错发货接口而漏发货。新逻辑：主发货接口返回"未知业务失败"
（unknown_failure，最可能就是订单类型不匹配）时，回退到另一种发货接口再试一次；
会话失效/风控/限流等已知失败不回退（换接口也无益或加重风控）。
"""

import sqlite3
import threading

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import invite_bridge
import secure_confirm_decrypted
import secure_freeshipping_decrypted
import XianyuAutoAsync
from schema_migrations import _invite_bridge_operations_v1


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


@pytest.fixture()
def bridge_database(monkeypatch):
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    _invite_bridge_operations_v1(connection.cursor(), ":memory:")
    connection.commit()
    monkeypatch.setattr(invite_bridge, "db_manager", _DatabaseStub(connection))
    yield connection
    connection.close()


def _setup_env(monkeypatch):
    monkeypatch.setenv("XIANYU_INVITE_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("XIANYU_INVITE_BRIDGE_SECRET", "bridge-test-secret")
    invite_bridge.db_manager.enabled_items.add(("account-1", "item-1"))
    invite_bridge._seen_nonces.clear()


def _bind_order(order):
    database = invite_bridge.db_manager
    database.get_order_by_id = lambda _order_id: order
    database.get_cookie = lambda _cookie_id: "fixture-cookie"

    def update_order(**values):
        order["order_status"] = values["order_status"]
        order["system_shipped"] = int(values["system_shipped"])
        return True

    database.insert_or_update_order = update_order


def _post_mark_fulfilled(operation_key):
    payload = {
        "operationKey": operation_key,
        "orderId": "order-1",
        "cookieId": "account-1",
        "itemId": "item-1",
        "requestId": "request-1",
    }
    app = FastAPI()
    app.include_router(invite_bridge.invite_bridge_router)
    with TestClient(app) as client:
        return client.post(
            "/internal/invite/mark-fulfilled",
            json=payload,
            headers=invite_bridge._signature_headers(payload, "bridge-test-secret"),
        )


def test_confirm_unknown_failure_falls_back_to_freeshipping(monkeypatch, bridge_database):
    _setup_env(monkeypatch)
    order = {
        "order_id": "order-1",
        "cookie_id": "account-1",
        "item_id": "item-1",
        "buyer_id": "buyer-1",
        "chat_id": "chat-1",
        "order_status": "pending_ship",
        "system_shipped": 0,
    }
    _bind_order(order)
    # 没有在线实例，免拼发货走直连 SecureFreeshipping
    monkeypatch.setattr(
        XianyuAutoAsync.XianyuLive, "get_instance", staticmethod(lambda _c: None)
    )

    class _Confirm:
        calls = 0

        def __init__(self, *_args):
            pass

        async def auto_confirm(self, order_id, item_id):
            self.__class__.calls += 1
            return {"success": False, "category": "unknown_failure", "error": "订单类型不匹配"}

    class _Freeship:
        calls = 0

        def __init__(self, *_args):
            pass

        async def auto_freeshipping(self, order_id, item_id, buyer_id):
            self.__class__.calls += 1
            return {"success": True}

    monkeypatch.setattr(secure_confirm_decrypted, "SecureConfirm", _Confirm)
    monkeypatch.setattr(secure_freeshipping_decrypted, "SecureFreeshipping", _Freeship)

    body = _post_mark_fulfilled("fulfillment-fallback-1").json()
    assert body["state"] == "succeeded"
    assert body["deliveryMode"] == "free_shipping"
    assert _Confirm.calls == 1
    assert _Freeship.calls == 1
    assert order["order_status"] == "shipped"


def test_freeshipping_unknown_failure_falls_back_to_confirm(monkeypatch, bridge_database):
    _setup_env(monkeypatch)
    order = {
        "order_id": "order-1",
        "cookie_id": "account-1",
        "item_id": "item-1",
        "buyer_id": "buyer-1",
        "chat_id": "chat-1",
        "order_status": "pending_ship",
        "is_bargain": 1,
        "system_shipped": 0,
    }
    _bind_order(order)
    monkeypatch.setattr(
        XianyuAutoAsync.XianyuLive, "get_instance", staticmethod(lambda _c: None)
    )

    class _Freeship:
        calls = 0

        def __init__(self, *_args):
            pass

        async def auto_freeshipping(self, order_id, item_id, buyer_id):
            self.__class__.calls += 1
            return {"success": False, "category": "unknown_failure"}

    class _Confirm:
        calls = 0

        def __init__(self, *_args):
            pass

        async def auto_confirm(self, order_id, item_id):
            self.__class__.calls += 1
            return {"success": True}

    monkeypatch.setattr(secure_freeshipping_decrypted, "SecureFreeshipping", _Freeship)
    monkeypatch.setattr(secure_confirm_decrypted, "SecureConfirm", _Confirm)

    body = _post_mark_fulfilled("fulfillment-fallback-2").json()
    assert body["state"] == "succeeded"
    assert body["deliveryMode"] == "status_only"
    assert _Freeship.calls == 1
    assert _Confirm.calls == 1
    assert order["order_status"] == "shipped"


def test_session_invalid_does_not_fall_back(monkeypatch, bridge_database):
    _setup_env(monkeypatch)
    order = {
        "order_id": "order-1",
        "cookie_id": "account-1",
        "item_id": "item-1",
        "buyer_id": "buyer-1",
        "chat_id": "chat-1",
        "order_status": "pending_ship",
        "system_shipped": 0,
    }
    _bind_order(order)
    monkeypatch.setattr(
        XianyuAutoAsync.XianyuLive, "get_instance", staticmethod(lambda _c: None)
    )

    class _Confirm:
        calls = 0

        def __init__(self, *_args):
            pass

        async def auto_confirm(self, order_id, item_id):
            self.__class__.calls += 1
            return {"success": False, "category": "session_invalid", "error": "FAIL_SYS_SESSION_EXPIRED"}

    class _Freeship:
        calls = 0

        def __init__(self, *_args):
            pass

        async def auto_freeshipping(self, order_id, item_id, buyer_id):
            self.__class__.calls += 1
            return {"success": True}

    monkeypatch.setattr(secure_confirm_decrypted, "SecureConfirm", _Confirm)
    monkeypatch.setattr(secure_freeshipping_decrypted, "SecureFreeshipping", _Freeship)

    body = _post_mark_fulfilled("fulfillment-fallback-3").json()
    assert body["state"] == "failed"
    assert _Confirm.calls == 1
    assert _Freeship.calls == 0
    assert order["order_status"] != "shipped"
