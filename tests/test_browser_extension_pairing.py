import unittest

from browser_extension_pairing import BrowserExtensionPairingManager, PairingError


class BrowserExtensionPairingTests(unittest.TestCase):
    def setUp(self):
        self.manager = BrowserExtensionPairingManager(
            ttl_seconds=300,
            max_attempts=5,
            create_limit_per_minute=10,
        )

    def test_v2_pairing_uses_a_high_entropy_single_use_token_for_remote_import(self):
        status, pairing_token = self.manager.create(7)

        self.assertEqual(status["protocol_version"], 2)
        self.assertGreaterEqual(len(pairing_token), 32)
        self.assertNotIn("pairing_token", status)

        record = self.manager.consume(
            status["pairing_id"],
            pairing_token,
            protocol_version=2,
            remote_host="203.0.113.20",
        )
        self.assertEqual(record.owner_user_id, 7)

        with self.assertRaises(PairingError) as replay:
            self.manager.consume(
                status["pairing_id"],
                pairing_token,
                protocol_version=2,
                remote_host="203.0.113.20",
            )
        self.assertEqual(replay.exception.error_code, "pairing_already_used")

    def test_v1_compatibility_remains_loopback_only(self):
        status, pairing_token = self.manager.create(7)

        with self.assertRaises(PairingError) as remote:
            self.manager.consume(
                status["pairing_id"],
                pairing_token,
                protocol_version=1,
                remote_host="203.0.113.20",
            )
        self.assertEqual(remote.exception.error_code, "non_loopback_request")

        record = self.manager.consume(
            status["pairing_id"],
            pairing_token,
            protocol_version=1,
            remote_host="127.0.0.1",
        )
        self.assertEqual(record.owner_user_id, 7)

    def test_terminal_status_reports_how_pairing_ended_without_secret_material(self):
        status, pairing_token = self.manager.create(7)
        self.manager.consume(
            status["pairing_id"],
            pairing_token,
            protocol_version=2,
            remote_host="203.0.113.20",
        )
        self.manager.mark_validating(status["pairing_id"])
        completed = self.manager.succeed(status["pairing_id"], account_id="account-1")

        self.assertEqual(completed["ended_by"], "validated_and_persisted")
        self.assertNotIn("pairing_token", completed)
        self.assertNotIn(pairing_token, str(completed))

    def test_retryable_validation_restores_waiting_pairing(self):
        status, pairing_token = self.manager.create(7)
        locked = self.manager.begin_validation(
            status["pairing_id"],
            pairing_token,
            protocol_version=2,
            remote_host="203.0.113.20",
        )
        self.assertEqual(locked.status, "validating")
        restored = self.manager.restore_waiting(status["pairing_id"])
        self.assertEqual(restored["status"], "waiting")
        again = self.manager.begin_validation(
            status["pairing_id"],
            pairing_token,
            protocol_version=2,
            remote_host="203.0.113.20",
        )
        self.assertEqual(again.owner_user_id, 7)
        self.assertEqual(again.status, "validating")


if __name__ == "__main__":
    unittest.main()
