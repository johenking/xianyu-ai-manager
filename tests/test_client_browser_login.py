import json
import os
import tempfile
import time
import unittest

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

from client_browser_login import (
    ClientBrowserError,
    ClientLoginSessionManager,
    DeviceChallengeManager,
    b64url_encode,
    canonical_json,
    public_jwk_from_key,
)
from db_manager import DBManager
from schema_migrations import MIGRATIONS


def _raw_signature(private_key, payload):
    from cryptography.hazmat.primitives.asymmetric.utils import (
        decode_dss_signature,
    )

    der = private_key.sign(canonical_json(payload), ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    return b64url_encode(r.to_bytes(32, "big") + s.to_bytes(32, "big"))


class ClientLoginSessionTests(unittest.TestCase):
    def test_session_is_bound_to_device_mode_and_single_import(self):
        manager = ClientLoginSessionManager(ttl_seconds=300)
        status = manager.create(
            owner_user_id=7,
            device_id="device_fixture_1234",
            mode="password",
        )

        with self.assertRaises(ClientBrowserError) as wrong_device:
            manager.consume_for_import(
                session_id=status["session_id"],
                device_id="other_device_12345",
                mode="password",
            )
        self.assertEqual(wrong_device.exception.http_status, 403)

        manager.consume_for_import(
            session_id=status["session_id"],
            device_id="device_fixture_1234",
            mode="password",
        )
        with self.assertRaises(ClientBrowserError) as replay:
            manager.consume_for_import(
                session_id=status["session_id"],
                device_id="device_fixture_1234",
                mode="password",
            )
        self.assertEqual(replay.exception.http_status, 409)

    def test_success_requires_persisted_account_and_frontend_confirmation(self):
        manager = ClientLoginSessionManager()
        status = manager.create(
            owner_user_id=7,
            device_id="device_fixture_1234",
            mode="qr",
        )
        manager.consume_for_import(
            session_id=status["session_id"],
            device_id="device_fixture_1234", mode="qr",
        )
        persisted = manager.persisted(status["session_id"], account_id="account-1")
        self.assertEqual(persisted["state"], "awaiting_confirmation")
        with self.assertRaises(ClientBrowserError):
            manager.confirm(
                session_id=status["session_id"],
                owner_user_id=7,
                account_id="other-account",
            )
        completed = manager.confirm(
            session_id=status["session_id"],
            owner_user_id=7,
            account_id="account-1",
        )
        self.assertEqual(completed["state"], "success")

    def test_session_rejects_transport_type_confusion(self):
        manager = ClientLoginSessionManager()
        status = manager.create(
            owner_user_id=7,
            device_id="device_fixture_1234",
            mode="qr",
            client_type="native_helper",
        )
        self.assertEqual(status["client_type"], "native_helper")
        with self.assertRaises(ClientBrowserError) as mismatch:
            manager.get_for_device(
                session_id=status["session_id"],
                device_id="device_fixture_1234",
                mode="qr",
                client_type="extension",
            )
        self.assertEqual(mismatch.exception.error_code, "client_login_binding_mismatch")


class ClientDeviceProofTests(unittest.TestCase):
    def test_challenge_is_signed_bound_and_not_replayable(self):
        private_key = ec.generate_private_key(ec.SECP256R1())
        public_jwk = public_jwk_from_key(private_key.public_key())
        manager = DeviceChallengeManager(ttl_seconds=60)
        challenge = manager.create(
            device_id="device_fixture_1234",
            owner_user_id=7,
            purpose="login_import",
        )
        binding = {"session_id": "session-1", "mode": "qr"}
        payload = manager.proof_payload(challenge, binding)
        signature = _raw_signature(private_key, payload)

        manager.verify(
            challenge_id=challenge["challenge_id"],
            device_id="device_fixture_1234",
            purpose="login_import",
            public_jwk=public_jwk,
            signature=signature,
            binding=binding,
        )
        with self.assertRaises(ClientBrowserError) as replay:
            manager.verify(
                challenge_id=challenge["challenge_id"],
                device_id="device_fixture_1234",
                purpose="login_import",
                public_jwk=public_jwk,
                signature=signature,
                binding=binding,
            )
        self.assertEqual(replay.exception.http_status, 409)


class ClientRenewalDatabaseTests(unittest.TestCase):
    def setUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.db = DBManager(self.db_path)
        self.signing = ec.generate_private_key(ec.SECP256R1())
        self.encryption = ec.generate_private_key(ec.SECP256R1())
        with self.db.lock:
            self.db.conn.execute(
                "INSERT INTO cookies (id, value, user_id, xianyu_unb) "
                "VALUES ('account-1', 'unb=account-1; cookie2=session', 1, 'account-1')"
            )
            self.db.conn.commit()
        self.db.register_client_browser_device(
            user_id=1,
            device_id="device_fixture_1234",
            browser_family="chrome",
            display_name="Fixture Chrome",
            signing_public_jwk=public_jwk_from_key(self.signing.public_key()),
            encryption_public_jwk=public_jwk_from_key(self.encryption.public_key()),
        )

    def tearDown(self):
        self.db.conn.close()
        os.unlink(self.db_path)

    def test_unapproved_password_does_not_change_credentials_or_refresh_flag(self):
        details = self.db.get_cookie_details("account-1")
        self.assertEqual(details["password"], "")
        self.assertFalse(details["cookie_refresh_enabled"])
        with self.db.lock:
            encrypted, enabled, count = self.db.conn.execute(
                "SELECT password_encrypted, cookie_refresh_enabled, "
                "(SELECT COUNT(*) FROM account_renewal_bindings) "
                "FROM cookies WHERE id = 'account-1'"
            ).fetchone()
        self.assertEqual(encrypted, "")
        self.assertEqual(enabled, 0)
        self.assertEqual(count, 0)
        self.assertEqual(MIGRATIONS[-1].version, "2026080902")

    def test_registered_device_keys_are_immutable(self):
        replacement = ec.generate_private_key(ec.SECP256R1())
        with self.assertRaises(ClientBrowserError) as mismatch:
            self.db.register_client_browser_device(
                user_id=1,
                device_id="device_fixture_1234",
                browser_family="chrome",
                display_name="Fixture Chrome",
                signing_public_jwk=public_jwk_from_key(replacement.public_key()),
                encryption_public_jwk=public_jwk_from_key(self.encryption.public_key()),
            )
        self.assertEqual(mismatch.exception.http_status, 409)
        self.assertEqual(mismatch.exception.error_code, "device_key_mismatch")
        with self.assertRaises(ClientBrowserError) as transport_mismatch:
            self.db.register_client_browser_device(
                user_id=1,
                device_id="device_fixture_1234",
                browser_family="chrome",
                client_type="native_helper",
                display_name="Fixture Chrome",
                signing_public_jwk=public_jwk_from_key(self.signing.public_key()),
                encryption_public_jwk=public_jwk_from_key(self.encryption.public_key()),
            )
        self.assertEqual(transport_mismatch.exception.error_code, "device_type_mismatch")

    def test_device_transport_type_is_persisted_and_old_devices_default_to_extension(self):
        native = ec.generate_private_key(ec.SECP256R1())
        native_encryption = ec.generate_private_key(ec.SECP256R1())
        registered = self.db.register_client_browser_device(
            user_id=1,
            device_id="native_helper_fixture_1",
            browser_family="chrome",
            client_type="native_helper",
            display_name="Native helper",
            signing_public_jwk=public_jwk_from_key(native.public_key()),
            encryption_public_jwk=public_jwk_from_key(native_encryption.public_key()),
        )
        self.assertEqual(registered["client_type"], "native_helper")
        self.assertEqual(
            self.db.get_client_browser_device(
                user_id=1, device_id="native_helper_fixture_1"
            )["client_type"],
            "native_helper",
        )
        self.assertEqual(
            self.db.get_client_browser_device(
                user_id=1, device_id="device_fixture_1234"
            )["client_type"],
            "extension",
        )
        with self.db.lock, self.assertRaises(Exception):
            self.db.conn.execute(
                "UPDATE client_browser_devices SET client_type = 'invalid' "
                "WHERE device_id = 'native_helper_fixture_1'"
            )
        self.db.conn.rollback()

    def test_native_helper_is_isolated_from_extension_renewal_credentials(self):
        native = ec.generate_private_key(ec.SECP256R1())
        native_encryption = ec.generate_private_key(ec.SECP256R1())
        self.db.register_client_browser_device(
            user_id=1, device_id="native_helper_fixture_2",
            browser_family="chrome", client_type="native_helper",
            display_name="Native helper",
            signing_public_jwk=public_jwk_from_key(native.public_key()),
            encryption_public_jwk=public_jwk_from_key(native_encryption.public_key()),
        )
        with self.assertRaises(ClientBrowserError) as isolated:
            self.db.bind_account_renewal_device(
                user_id=1, cookie_id="account-1",
                device_id="native_helper_fixture_2",
                username="seller@example.com", password="secret",
                authorized_at=time.time(),
            )
        self.assertEqual(isolated.exception.error_code, "renewal_binding_mismatch")

    def test_task_is_device_bound_expires_and_ciphertext_is_single_claim(self):
        authorized_at = time.time()
        self.db.bind_account_renewal_device(
            user_id=1, cookie_id="account-1",
            device_id="device_fixture_1234",
            username="seller@example.com", password="secret",
            authorized_at=authorized_at,
        )
        task = self.db.create_client_renewal_task(
            user_id=1, cookie_id="account-1", trigger="test",
        )

        with self.assertRaises(ClientBrowserError) as wrong_device:
            self.db.claim_client_renewal_task(
                user_id=1, device_id="other_device_12345", task_id=task["task_id"],
            )
        self.assertEqual(wrong_device.exception.http_status, 404)
        claimed = self.db.claim_client_renewal_task(
            user_id=1, device_id="device_fixture_1234", task_id=task["task_id"],
        )
        serialized = json.dumps(claimed)
        self.assertNotIn("seller@example.com", serialized)
        self.assertNotIn("secret", serialized)
        with self.db.lock:
            encrypted_payload = self.db.conn.execute(
                "SELECT encrypted_payload_json FROM client_renewal_tasks WHERE task_id = ?",
                (task["task_id"],),
            ).fetchone()[0]
        self.assertEqual(encrypted_payload, "")
        with self.assertRaises(ClientBrowserError) as replay:
            self.db.claim_client_renewal_task(
                user_id=1, device_id="device_fixture_1234", task_id=task["task_id"],
            )
        self.assertEqual(replay.exception.http_status, 409)

        self.assertTrue(self.db.set_client_renewal_task_state(
            user_id=1,
            device_id="device_fixture_1234",
            task_id=task["task_id"],
            expected_state="claimed",
            state="failed",
            error_code="fixture_complete",
        ))
        expired = self.db.create_client_renewal_task(
            user_id=1, cookie_id="account-1", trigger="expired", now=100.0,
        )
        with self.assertRaises(ClientBrowserError) as expiry:
            self.db.claim_client_renewal_task(
                user_id=1, device_id="device_fixture_1234",
                task_id=expired["task_id"], now=161.0,
            )
        self.assertEqual(expiry.exception.http_status, 410)


if __name__ == "__main__":
    unittest.main()
