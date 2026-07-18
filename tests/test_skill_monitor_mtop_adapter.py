import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from db_manager import DBManager
from skill_monitor_mtop_adapter import (
    MTopAdapterError,
    MTopAdapterLimits,
    MTopSearchAdapter,
    MTopSearchQuery,
    MTopTransportRequest,
    MTopTransportResponse,
    RequestsMTopTransport,
    get_mtop_offline_contract_status,
    runtime_mtop_gate_state,
)


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "skill_monitor_mtop"


def fixture_bytes(name):
    return (FIXTURE_ROOT / name).read_bytes()


class FakeStore:
    def __init__(self):
        self.context = {
            "state": "ready",
            "user_id": 7,
            "account_id": "synthetic-account",
            "xianyu_unb": "9988",
            "cookie_revision": 3,
            "value": "unb=9988; _m_h5_tk=synthetic-token_1; cookie2=synthetic",
            "browser_user_agent": "Synthetic Browser",
        }
        self.budget_calls = []
        self.cas_calls = []
        self.breaker_claims = []
        self.breaker_outcomes = []
        self.budget_result = {"allowed": True}

    def get_owned_cookie_search_context(self, user_id, cookie_id):
        if user_id != 7 or cookie_id != "synthetic-account":
            return {"state": "ownership_mismatch"}
        return dict(self.context)

    def compare_and_swap_cookie_session(self, cookie_id, **kwargs):
        self.cas_calls.append((cookie_id, kwargs))
        if (
            cookie_id != self.context["account_id"]
            or kwargs["user_id"] != self.context["user_id"]
            or kwargs["expected_xianyu_unb"] != self.context["xianyu_unb"]
            or kwargs["expected_revision"] != self.context["cookie_revision"]
            or "unb=9988" not in kwargs["cookie_value"]
        ):
            return {"state": "revision_conflict", "updated": False}
        changed = kwargs["cookie_value"] != self.context["value"]
        self.context["value"] = kwargs["cookie_value"]
        self.context["cookie_revision"] += int(changed)
        return {
            "state": "updated" if changed else "unchanged",
            "updated": changed,
            "cookie_revision": self.context["cookie_revision"],
        }

    def claim_skill_monitor_request_budget(self, user_id, account_id, **kwargs):
        self.budget_calls.append((user_id, account_id, kwargs))
        return dict(self.budget_result)

    def claim_skill_monitor_mtop_circuit_probe(self, user_id, account_id, **kwargs):
        self.breaker_claims.append((user_id, account_id, kwargs))
        return {
            "allowed": True,
            "state": "closed",
            "probe_token": "",
            "retry_after": 0.0,
        }

    def record_skill_monitor_mtop_circuit_outcome(self, user_id, account_id, **kwargs):
        self.breaker_outcomes.append((user_id, account_id, kwargs))
        return {
            "recorded": True,
            "state": "closed" if kwargs.get("success") else "open",
        }


class FakeTransport:
    def __init__(self, responses, on_send=None):
        self.responses = list(responses)
        self.requests = []
        self.on_send = on_send

    async def send(self, request):
        self.requests.append(request)
        if self.on_send:
            self.on_send(request)
        if not self.responses:
            raise AssertionError("missing synthetic transport response")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def enabled_gate():
    return {
        "master_enabled": True,
        "mtop_enabled": True,
        "network_allowed": True,
        "executable": True,
    }


def response(name, *, status=200, headers=None, refreshed_cookie=""):
    return MTopTransportResponse(
        status_code=status,
        body=fixture_bytes(name),
        headers=headers or {},
        refreshed_cookie=refreshed_cookie,
    )


class SkillMonitorMTopAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_gate_and_network_policy_are_fail_closed(self):
        class Settings:
            values = {
                "skill_monitor_enabled": "true",
                "skill_monitor_mtop_enabled": "true",
            }

            def get_system_setting(self, key):
                return self.values.get(key)

        state = runtime_mtop_gate_state(Settings(), environ={})
        self.assertTrue(state["master_enabled"])
        self.assertTrue(state["mtop_enabled"])
        self.assertFalse(state["network_allowed"])
        self.assertFalse(state["executable"])

        status = get_mtop_offline_contract_status(Settings(), environ={})
        self.assertEqual(status["canary"]["verification"], "unverified")
        self.assertEqual(
            status["real_acceptance"]["blocker_code"],
            "dedicated_test_account_required",
        )

    async def test_pagination_filter_sort_and_response_allowlist(self):
        store = FakeStore()
        transport = FakeTransport([response("page-1.json"), response("page-2.json")])
        adapter = MTopSearchAdapter(
            store=store,
            transport=transport,
            gate_provider=enabled_gate,
            limits=MTopAdapterLimits(max_pages=2, max_results=10),
        )

        result = await adapter.search(
            user_id=7,
            account_id="synthetic-account",
            query=MTopSearchQuery(
                keyword="  iPhone   15 Pro ",
                sort="latest",
                region="杭州",
                min_price=4400,
                max_price=5300,
                pages=2,
            ),
        )

        self.assertEqual([item.item_id for item in result.items], [
            "synthetic-item-3",
            "synthetic-item-1",
        ])
        self.assertEqual(result.raw_item_count, 3)
        self.assertEqual(result.stopped_reason, "no_next_page")
        self.assertFalse(result.is_real_data)
        public = result.public_dict()
        self.assertNotIn("fixture_only_unknown", str(public))
        self.assertNotIn("unknown_private_field", str(public))
        self.assertNotIn("synthetic-token", str(public))

        payloads = [
            json.loads(request.form_data["data"])
            for request in transport.requests
        ]
        self.assertEqual([payload["pageNumber"] for payload in payloads], [1, 2])
        self.assertEqual(payloads[0]["keyword"], "iPhone 15 Pro")
        self.assertEqual(payloads[0]["sortField"], "create")
        self.assertEqual(payloads[0]["sortValue"], "desc")
        self.assertEqual(
            payloads[0]["propValueStr"]["searchFilter"],
            "priceRange:4400,5300;",
        )

    async def test_legal_empty_is_distinct_from_invalid_schema(self):
        adapter = MTopSearchAdapter(
            store=FakeStore(),
            transport=FakeTransport([response("empty.json")]),
            gate_provider=enabled_gate,
        )
        result = await adapter.search(
            user_id=7,
            account_id="synthetic-account",
            query=MTopSearchQuery(keyword="synthetic-empty"),
        )
        self.assertTrue(result.legal_empty)
        self.assertEqual(result.stopped_reason, "legal_empty")
        self.assertEqual(result.items, [])

        invalid = MTopTransportResponse(
            status_code=200,
            body=b'{"ret":["SUCCESS::ok"],"data":{}}',
        )
        adapter = MTopSearchAdapter(
            store=FakeStore(),
            transport=FakeTransport([invalid]),
            gate_provider=enabled_gate,
        )
        with self.assertRaises(MTopAdapterError) as raised:
            await adapter.search(
                user_id=7,
                account_id="synthetic-account",
                query=MTopSearchQuery(keyword="synthetic-invalid"),
            )
        self.assertEqual(raised.exception.code, "response_schema_invalid")

    async def test_retry_after_is_honored_and_each_attempt_reclaims_budget(self):
        sleeps = []

        async def record_sleep(seconds):
            sleeps.append(seconds)

        store = FakeStore()
        transport = FakeTransport([
            MTopTransportResponse(
                status_code=429,
                body=b"{}",
                headers={"retry-after": "3"},
            ),
            response("empty.json"),
        ])
        adapter = MTopSearchAdapter(
            store=store,
            transport=transport,
            gate_provider=enabled_gate,
            sleep=record_sleep,
        )
        result = await adapter.search(
            user_id=7,
            account_id="synthetic-account",
            query=MTopSearchQuery(keyword="synthetic-retry"),
        )

        self.assertTrue(result.legal_empty)
        self.assertEqual(sleeps, [3.0])
        self.assertEqual(len(store.budget_calls), 2)

    async def test_risk_control_stops_without_retry(self):
        body = json.dumps({
            "ret": ["FAIL_SYS_USER_VALIDATE::synthetic"],
            "data": {"resultList": []},
        }).encode()
        transport = FakeTransport([MTopTransportResponse(status_code=200, body=body)])
        adapter = MTopSearchAdapter(
            store=FakeStore(),
            transport=transport,
            gate_provider=enabled_gate,
        )
        with self.assertRaises(MTopAdapterError) as raised:
            await adapter.search(
                user_id=7,
                account_id="synthetic-account",
                query=MTopSearchQuery(keyword="synthetic-risk"),
            )
        self.assertEqual(raised.exception.code, "risk_control")
        self.assertTrue(raised.exception.action_required)
        self.assertEqual(len(transport.requests), 1)
        self.assertTrue(adapter.store.breaker_outcomes[0][2]["force_open"])

    async def test_token_refresh_uses_cookie_cas_before_retry(self):
        expired = json.dumps({
            "ret": ["FAIL_SYS_TOKEN_EXOIRED::synthetic"],
            "data": {"resultList": []},
        }).encode()
        store = FakeStore()
        transport = FakeTransport([
            MTopTransportResponse(
                status_code=200,
                body=expired,
                refreshed_cookie=(
                    "unb=9988; _m_h5_tk=synthetic-refreshed_2; "
                    "cookie2=synthetic"
                ),
            ),
            response("empty.json"),
        ])
        adapter = MTopSearchAdapter(
            store=store,
            transport=transport,
            gate_provider=enabled_gate,
            sleep=lambda _seconds: _completed_sleep(),
            jitter=lambda _base: 0.0,
        )
        result = await adapter.search(
            user_id=7,
            account_id="synthetic-account",
            query=MTopSearchQuery(keyword="synthetic-refresh"),
        )
        self.assertTrue(result.legal_empty)
        self.assertEqual(len(store.cas_calls), 1)
        self.assertEqual(store.context["cookie_revision"], 4)
        self.assertIn("synthetic-refreshed", transport.requests[1].cookie_value)

    async def test_revision_change_during_request_discards_response(self):
        store = FakeStore()

        def bump_revision(_request):
            store.context["cookie_revision"] += 1

        transport = FakeTransport([response("empty.json")], on_send=bump_revision)
        adapter = MTopSearchAdapter(
            store=store,
            transport=transport,
            gate_provider=enabled_gate,
        )
        with self.assertRaises(MTopAdapterError) as raised:
            await adapter.search(
                user_id=7,
                account_id="synthetic-account",
                query=MTopSearchQuery(keyword="synthetic-stale"),
            )
        self.assertEqual(raised.exception.code, "revision_conflict")

    async def test_kill_switch_change_discards_response_before_cookie_cas(self):
        store = FakeStore()
        gate = enabled_gate()

        def disable_gate(_request):
            gate["master_enabled"] = False
            gate["mtop_enabled"] = False
            gate["executable"] = False

        transport = FakeTransport([
            MTopTransportResponse(
                status_code=200,
                body=fixture_bytes("empty.json"),
                refreshed_cookie=(
                    "unb=9988; _m_h5_tk=synthetic-refreshed_2; "
                    "cookie2=synthetic"
                ),
            )
        ], on_send=disable_gate)
        adapter = MTopSearchAdapter(
            store=store,
            transport=transport,
            gate_provider=lambda: dict(gate),
        )
        with self.assertRaises(MTopAdapterError) as raised:
            await adapter.search(
                user_id=7,
                account_id="synthetic-account",
                query=MTopSearchQuery(keyword="synthetic-kill-switch"),
            )
        self.assertEqual(raised.exception.code, "kill_switch_disabled")
        self.assertEqual(store.cas_calls, [])

    async def test_fixed_transport_rejects_arbitrary_endpoint_before_network(self):
        request = MTopTransportRequest(
            url="https://example.test/not-allowed",
            params={},
            form_data={},
            headers={},
            cookie_value="synthetic",
        )
        with patch("skill_monitor_mtop_adapter.requests.post") as post_mock:
            with self.assertRaises(MTopAdapterError) as raised:
                await RequestsMTopTransport().send(request)
        self.assertEqual(raised.exception.code, "endpoint_rejected")
        post_mock.assert_not_called()

    async def test_transport_repr_hides_cookie_headers_form_and_body(self):
        request = MTopTransportRequest(
            url="https://example.test/not-sent",
            params={"sign": "synthetic-sign"},
            form_data={"data": "synthetic-form-secret"},
            headers={"Cookie": "unb=private"},
            cookie_value="unb=private",
        )
        transport_response = MTopTransportResponse(
            status_code=200,
            body=b"synthetic-body-secret",
            refreshed_cookie="unb=private-new",
        )
        self.assertNotIn("private", repr(request))
        self.assertNotIn("synthetic-form-secret", repr(request))
        self.assertNotIn("synthetic-body-secret", repr(transport_response))
        self.assertNotIn("private-new", repr(transport_response))


async def _completed_sleep():
    return None


class SkillMonitorRequestBudgetTests(unittest.TestCase):
    def setUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.db = DBManager(self.db_path)

    def tearDown(self):
        self.db.conn.close()
        os.unlink(self.db_path)

    def test_global_and_account_budgets_are_atomic_and_store_only_digests(self):
        first = self.db.claim_skill_monitor_request_budget(
            7,
            "private-account-a",
            global_limit=2,
            account_limit=1,
            window_seconds=60,
            now=100,
        )
        same_account = self.db.claim_skill_monitor_request_budget(
            7,
            "private-account-a",
            global_limit=2,
            account_limit=1,
            window_seconds=60,
            now=101,
        )
        second_account = self.db.claim_skill_monitor_request_budget(
            7,
            "private-account-b",
            global_limit=2,
            account_limit=1,
            window_seconds=60,
            now=102,
        )
        global_blocked = self.db.claim_skill_monitor_request_budget(
            7,
            "private-account-c",
            global_limit=2,
            account_limit=1,
            window_seconds=60,
            now=103,
        )

        self.assertTrue(first["allowed"])
        self.assertFalse(same_account["allowed"])
        self.assertTrue(second_account["allowed"])
        self.assertFalse(global_blocked["allowed"])
        self.assertEqual(global_blocked["global_count"], 2)
        persisted = str(
            self.db.conn.execute(
                "SELECT scope_type, scope_digest, request_count "
                "FROM skill_monitor_request_budgets ORDER BY scope_type, scope_digest"
            ).fetchall()
        )
        self.assertNotIn("private-account", persisted)

    def test_circuit_breaker_opens_and_single_flights_half_open_probe(self):
        account_id = "private-breaker-account"
        for index in range(3):
            claim = self.db.claim_skill_monitor_mtop_circuit_probe(
                7,
                account_id,
                probe_lease_seconds=60,
                now=100 + index * 2,
            )
            self.assertTrue(claim["allowed"])
            outcome = self.db.record_skill_monitor_mtop_circuit_outcome(
                7,
                account_id,
                success=False,
                error_code="synthetic_remote_error",
                failure_threshold=3,
                cooldown_seconds=3600,
                probe_token=claim["probe_token"],
                now=101 + index * 2,
            )

        self.assertEqual(outcome["state"], "open")
        blocked = self.db.claim_skill_monitor_mtop_circuit_probe(
            7,
            account_id,
            probe_lease_seconds=60,
            now=106,
        )
        self.assertFalse(blocked["allowed"])
        self.assertEqual(blocked["state"], "open")

        probe = self.db.claim_skill_monitor_mtop_circuit_probe(
            7,
            account_id,
            probe_lease_seconds=60,
            now=3706,
        )
        self.assertTrue(probe["allowed"])
        self.assertEqual(probe["state"], "half_open")
        self.assertTrue(probe["probe_token"])
        duplicate_probe = self.db.claim_skill_monitor_mtop_circuit_probe(
            7,
            account_id,
            probe_lease_seconds=60,
            now=3707,
        )
        self.assertFalse(duplicate_probe["allowed"])
        self.assertEqual(duplicate_probe["state"], "half_open")

        closed = self.db.record_skill_monitor_mtop_circuit_outcome(
            7,
            account_id,
            success=True,
            failure_threshold=3,
            cooldown_seconds=3600,
            probe_token=probe["probe_token"],
            now=3708,
        )
        self.assertTrue(closed["recorded"])
        self.assertEqual(closed["state"], "closed")
        persisted = str(
            self.db.conn.execute(
                "SELECT scope_digest, state, last_error_code "
                "FROM skill_monitor_mtop_breakers"
            ).fetchall()
        )
        self.assertNotIn(account_id, persisted)

    def test_action_required_can_open_breaker_immediately(self):
        claim = self.db.claim_skill_monitor_mtop_circuit_probe(
            7,
            "private-action-account",
            probe_lease_seconds=60,
            now=100,
        )
        outcome = self.db.record_skill_monitor_mtop_circuit_outcome(
            7,
            "private-action-account",
            success=False,
            error_code="risk_control",
            failure_threshold=3,
            cooldown_seconds=3600,
            probe_token=claim["probe_token"],
            force_open=True,
            now=101,
        )
        self.assertEqual(outcome["state"], "open")
        blocked = self.db.claim_skill_monitor_mtop_circuit_probe(
            7,
            "private-action-account",
            probe_lease_seconds=60,
            now=102,
        )
        self.assertFalse(blocked["allowed"])


if __name__ == "__main__":
    unittest.main()
