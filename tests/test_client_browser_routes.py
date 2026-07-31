import unittest
import time
from unittest.mock import AsyncMock, Mock, patch

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from fastapi import HTTPException

import reply_server
from client_browser_login import (
    ClientLoginSessionManager,
    DeviceChallengeManager,
    b64url_encode,
    canonical_json,
    public_jwk_from_key,
)
from utils.xianyu_session_probe import SessionProbeResult


DEVICE_ID = "device_fixture_1234"
OTHER_DEVICE_ID = "other_device_12345"


def _raw_signature(private_key, payload):
    der = private_key.sign(canonical_json(payload), ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    return b64url_encode(r.to_bytes(32, "big") + s.to_bytes(32, "big"))


def _cookies(unb="account-1"):
    return [
        {
            "name": "unb",
            "value": unb,
            "domain": ".goofish.com",
            "path": "/",
        },
        {
            "name": "cookie2",
            "value": "session-cookie",
            "domain": ".goofish.com",
            "path": "/",
        },
    ]


class FakeClientBrowserDatabase:
    def __init__(self, signing_public_jwk):
        self.signing_public_jwk = signing_public_jwk
        self.touch_calls = []

    def get_client_browser_device(
        self, *, user_id, device_id, include_public_keys=False
    ):
        if int(user_id) != 7 or device_id != DEVICE_ID:
            return None
        device = {
            "device_id": DEVICE_ID,
            "browser_family": "chrome",
            "revoked": False,
        }
        if include_public_keys:
            device["signing_public_jwk"] = self.signing_public_jwk
        return device

    def touch_client_browser_device(self, *, user_id, device_id):
        self.touch_calls.append((int(user_id), device_id))
        return True


class ClientBrowserRouteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.user = {"user_id": 7, "username": "operator"}
        self.private_key = ec.generate_private_key(ec.SECP256R1())
        self.sessions = ClientLoginSessionManager()
        self.challenges = DeviceChallengeManager()
        self.database = FakeClientBrowserDatabase(
            public_jwk_from_key(self.private_key.public_key())
        )

    def _create_session(self, mode="qr"):
        with (
            patch.object(reply_server, "client_login_sessions", self.sessions),
            patch.object(reply_server, "db_manager", self.database),
        ):
            return reply_server.create_client_browser_login_session(
                reply_server.ClientBrowserSessionIn(
                    device_id=DEVICE_ID,
                    mode=mode,
                ),
                current_user=self.user,
            )["data"]

    def _signed_payload(self, session, *, private_key=None, unb="account-1"):
        with (
            patch.object(reply_server, "client_login_sessions", self.sessions),
            patch.object(reply_server, "device_challenges", self.challenges),
            patch.object(reply_server, "db_manager", self.database),
        ):
            challenge = reply_server.create_client_browser_login_challenge(
                session["session_id"],
                reply_server.ClientBrowserSessionAuthorizeIn(
                    device_id=DEVICE_ID,
                    mode=session["mode"],
                ),
            )["data"]
        binding = {
            "session_id": session["session_id"],
            "mode": session["mode"],
            "device_id": DEVICE_ID,
        }
        proof = self.challenges.proof_payload(challenge, binding)
        signature = _raw_signature(private_key or self.private_key, proof)
        return reply_server.ClientBrowserLoginImportIn(
            session_id=session["session_id"],
            device_id=DEVICE_ID,
            mode=session["mode"],
            challenge_id=challenge["challenge_id"],
            signature=signature,
            cookies=_cookies(unb),
            user_agent="Chrome Route Fixture",
        )

    async def _import(self, payload, *, probe, persist):
        with (
            patch.object(reply_server, "client_login_sessions", self.sessions),
            patch.object(reply_server, "device_challenges", self.challenges),
            patch.object(reply_server, "db_manager", self.database),
            patch.object(
                reply_server,
                "probe_message_session_async",
                AsyncMock(return_value=probe),
            ),
            patch.object(reply_server, "_persist_validated_account_login", persist),
        ):
            return await reply_server.import_client_browser_login(payload)

    async def test_wrong_device_and_signature_are_rejected_before_probe_or_write(self):
        session = self._create_session("password")
        with (
            patch.object(reply_server, "client_login_sessions", self.sessions),
            patch.object(reply_server, "device_challenges", self.challenges),
            patch.object(reply_server, "db_manager", self.database),
        ):
            with self.assertRaises(HTTPException) as wrong_device:
                reply_server.create_client_browser_login_challenge(
                    session["session_id"],
                    reply_server.ClientBrowserSessionAuthorizeIn(
                        device_id=OTHER_DEVICE_ID,
                        mode="password",
                    ),
                )
        self.assertEqual(wrong_device.exception.status_code, 403)

        payload = self._signed_payload(
            session,
            private_key=ec.generate_private_key(ec.SECP256R1()),
        )
        probe = AsyncMock()
        persist = AsyncMock()
        with (
            patch.object(reply_server, "client_login_sessions", self.sessions),
            patch.object(reply_server, "device_challenges", self.challenges),
            patch.object(reply_server, "db_manager", self.database),
            patch.object(reply_server, "probe_message_session_async", probe),
            patch.object(reply_server, "_persist_validated_account_login", persist),
        ):
            with self.assertRaises(HTTPException) as wrong_signature:
                await reply_server.import_client_browser_login(payload)
        self.assertEqual(wrong_signature.exception.status_code, 403)
        probe.assert_not_awaited()
        persist.assert_not_awaited()
        self.assertEqual(
            self.sessions.get_for_owner(session["session_id"], 7)["state"],
            "waiting_user",
        )

    async def test_expired_and_replayed_challenges_never_repeat_persistence(self):
        expired_session = self._create_session("sms")
        expired_payload = self._signed_payload(expired_session)
        self.challenges._records[expired_payload.challenge_id].expires_at = 0
        persist = AsyncMock()
        with self.assertRaises(HTTPException) as expired:
            await self._import(
                expired_payload,
                probe=SessionProbeResult(
                    status="success",
                    cookies={"unb": "account-1", "cookie2": "session-cookie"},
                    access_token="validated-token",
                ),
                persist=persist,
            )
        self.assertEqual(expired.exception.status_code, 410)
        persist.assert_not_awaited()

        session = self._create_session("qr")
        payload = self._signed_payload(session)
        persist = AsyncMock(
            return_value={"account_id": "account-1", "is_new_account": True}
        )
        imported = await self._import(
            payload,
            probe=SessionProbeResult(
                status="success",
                cookies={"unb": "account-1", "cookie2": "session-cookie"},
                access_token="validated-token",
            ),
            persist=persist,
        )
        self.assertEqual(imported["data"]["state"], "awaiting_confirmation")
        with self.assertRaises(HTTPException) as replay:
            await self._import(
                payload,
                probe=SessionProbeResult(
                    status="success",
                    cookies={"unb": "account-1", "cookie2": "session-cookie"},
                    access_token="validated-token",
                ),
                persist=persist,
            )
        self.assertEqual(replay.exception.status_code, 409)
        persist.assert_awaited_once()
        self.assertNotEqual(imported["data"]["state"], "success")

    async def test_token_identity_and_persistence_failures_never_report_success(self):
        scenarios = (
            (
                "token",
                SessionProbeResult(
                    status="verification_required",
                    error_code="human_verification_required",
                    message="需要继续验证",
                ),
                "account-1",
                None,
                "waiting_user",
            ),
            (
                "identity",
                SessionProbeResult(
                    status="success",
                    cookies={"unb": "other-account", "cookie2": "session-cookie"},
                    access_token="validated-token",
                ),
                "account-1",
                None,
                "waiting_user",
            ),
            (
                "persist",
                SessionProbeResult(
                    status="success",
                    cookies={"unb": "account-1", "cookie2": "session-cookie"},
                    access_token="validated-token",
                ),
                "account-1",
                RuntimeError("fixture persistence failure"),
                "failed",
            ),
        )
        for mode, probe, unb, persist_error, expected_state in scenarios:
            with self.subTest(mode=mode):
                self.sessions.clear()
                self.challenges.clear()
                session = self._create_session("password")
                payload = self._signed_payload(session, unb=unb)
                persist = AsyncMock(
                    side_effect=persist_error,
                    return_value={"account_id": "account-1", "is_new_account": False},
                )
                with self.assertRaises(HTTPException):
                    await self._import(payload, probe=probe, persist=persist)
                status = self.sessions.get_for_owner(session["session_id"], 7)
                self.assertEqual(status["state"], expected_state)
                self.assertNotEqual(status["state"], "success")
                if persist_error is None:
                    persist.assert_not_awaited()
                else:
                    persist.assert_awaited_once()


class ClientRenewalRouteTests(unittest.IsolatedAsyncioTestCase):
    def test_renewal_binding_requires_one_confirmed_password_session(self):
        sessions = ClientLoginSessionManager()
        password_session = sessions.create(
            owner_user_id=7, device_id=DEVICE_ID, mode="password"
        )
        sessions.consume_for_import(
            session_id=password_session["session_id"],
            device_id=DEVICE_ID,
            mode="password",
        )
        sessions.persisted(password_session["session_id"], account_id="account-1")
        sessions.confirm(
            session_id=password_session["session_id"],
            owner_user_id=7,
            account_id="account-1",
        )
        database = Mock()
        database.bind_account_renewal_device.return_value = {
            "account_id": "account-1",
            "device_id": DEVICE_ID,
        }
        payload = reply_server.AccountRenewalBindingIn(
            login_session_id=password_session["session_id"],
            device_id=DEVICE_ID,
            username="seller@example.com",
            password="fixture-password",
            authorized=True,
            authorized_at=time.time(),
        )
        with (
            patch.object(reply_server, "client_login_sessions", sessions),
            patch.object(reply_server, "db_manager", database),
            patch.object(reply_server, "_require_owned_cookie"),
        ):
            result = reply_server.bind_account_renewal_device(
                "account-1", payload, current_user={"user_id": 7}
            )
            self.assertEqual(result["data"]["account_id"], "account-1")
            with self.assertRaises(HTTPException) as replay:
                reply_server.bind_account_renewal_device(
                    "account-1", payload, current_user={"user_id": 7}
                )
        self.assertEqual(replay.exception.status_code, 409)
        database.bind_account_renewal_device.assert_called_once()

        unconfirmed = ClientLoginSessionManager()
        qr_session = unconfirmed.create(
            owner_user_id=7, device_id=DEVICE_ID, mode="qr"
        )
        qr_payload = payload.model_copy(update={"login_session_id": qr_session["session_id"]})
        with (
            patch.object(reply_server, "client_login_sessions", unconfirmed),
            patch.object(reply_server, "db_manager", database),
            patch.object(reply_server, "_require_owned_cookie"),
        ):
            with self.assertRaises(HTTPException) as wrong_session:
                reply_server.bind_account_renewal_device(
                    "account-1", qr_payload, current_user={"user_id": 7}
                )
        self.assertEqual(wrong_session.exception.status_code, 409)

    async def test_action_required_pauses_once_without_token_probe_or_persistence(self):
        private_key = ec.generate_private_key(ec.SECP256R1())
        public_jwk = public_jwk_from_key(private_key.public_key())
        challenges = DeviceChallengeManager()
        task_id = "renewal-task-1"
        database = Mock()
        database.find_active_client_browser_device.return_value = {
            "user_id": 7,
            "device_id": DEVICE_ID,
            "signing_public_jwk": public_jwk,
        }
        database.get_client_renewal_task.return_value = {
            "task_id": task_id,
            "account_id": "account-1",
            "state": "claimed",
        }
        database.set_client_renewal_task_state.return_value = True
        challenge = challenges.create(
            device_id=DEVICE_ID,
            owner_user_id=7,
            purpose="renewal_action_required",
        )
        binding = {
            "device_id": DEVICE_ID,
            "task_id": task_id,
            "account_id": "account-1",
            "outcome": "action_required",
        }
        signature = _raw_signature(
            private_key,
            challenges.proof_payload(challenge, binding),
        )
        payload = reply_server.ClientRenewalResultIn(
            device_id=DEVICE_ID,
            challenge_id=challenge["challenge_id"],
            signature=signature,
            outcome="action_required",
            error_code="human_verification_required",
        )
        probe = AsyncMock()
        persist = AsyncMock()
        with (
            patch.object(reply_server, "device_challenges", challenges),
            patch.object(reply_server, "db_manager", database),
            patch.object(reply_server, "probe_message_session_async", probe),
            patch.object(reply_server, "_persist_validated_account_login", persist),
        ):
            result = await reply_server.complete_client_renewal_task(task_id, payload)
            with self.assertRaises(HTTPException) as replay:
                await reply_server.complete_client_renewal_task(task_id, payload)
        self.assertEqual(result["data"]["state"], "action_required")
        self.assertEqual(replay.exception.status_code, 409)
        database.set_client_renewal_task_state.assert_called_once()
        database.update_account_session_refresh.assert_called_once()
        probe.assert_not_awaited()
        persist.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
