import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from db_manager import DBManager

try:
    from auth_email_service import (
        SMTPConfiguration,
        SMTPConfigurationError,
        SMTPDeliveryError,
        SMTPEmailSender,
        registration_readiness,
        smtp_configuration_fingerprint,
    )
except ImportError:
    SMTPConfiguration = None
    SMTPConfigurationError = None
    SMTPDeliveryError = None
    SMTPEmailSender = None
    registration_readiness = None
    smtp_configuration_fingerprint = None


SMTP_SETTINGS = {
    "smtp_server": "smtp.example.test",
    "smtp_port": "587",
    "smtp_user": "sender@example.test",
    "smtp_password": "synthetic-smtp-secret",
    "smtp_from": "Xianyu Manager",
    "smtp_use_tls": "true",
    "smtp_use_ssl": "false",
    "support_email": "support@example.test",
}


class FakeSMTP:
    def __init__(self):
        self.ehlo_calls = 0
        self.starttls_calls = 0
        self.login_args = None
        self.message = None
        self.quit_calls = 0

    def ehlo(self):
        self.ehlo_calls += 1

    def starttls(self, *, context):
        self.starttls_calls += 1
        self.tls_context = context

    def login(self, username, password):
        self.login_args = (username, password)

    def send_message(self, message):
        self.message = message

    def quit(self):
        self.quit_calls += 1


class SMTPEmailServiceTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(
            SMTPEmailSender,
            "auth_email_service must provide fail-closed SMTP delivery",
        )

    def test_sender_uses_saved_smtp_only_and_sends_a_real_message(self):
        fake = FakeSMTP()
        with patch("auth_email_service.smtplib.SMTP", return_value=fake) as factory:
            SMTPEmailSender(timeout_seconds=7).send(
                SMTP_SETTINGS,
                recipient="recipient@example.test",
                subject="SMTP verification",
                text="This is a delivery test.",
            )

        factory.assert_called_once_with("smtp.example.test", 587, timeout=7)
        self.assertEqual(fake.starttls_calls, 1)
        self.assertEqual(
            fake.login_args,
            ("sender@example.test", "synthetic-smtp-secret"),
        )
        self.assertEqual(fake.message["To"], "recipient@example.test")
        self.assertIn("sender@example.test", fake.message["From"])
        self.assertEqual(fake.quit_calls, 1)

    def test_delivery_failure_is_generic_and_has_no_fallback(self):
        network_error = OSError(
            "recipient@example.test synthetic-smtp-secret refused"
        )
        with patch(
            "auth_email_service.smtplib.SMTP",
            side_effect=network_error,
        ) as factory:
            with self.assertRaises(SMTPDeliveryError) as raised:
                SMTPEmailSender().send(
                    SMTP_SETTINGS,
                    recipient="recipient@example.test",
                    subject="Verification code 123456",
                    text="Code 123456",
                )

        factory.assert_called_once()
        message = str(raised.exception)
        self.assertNotIn("recipient@example.test", message)
        self.assertNotIn("synthetic-smtp-secret", message)
        self.assertNotIn("123456", message)

    def test_configuration_rejects_conflicting_tls_modes(self):
        with self.assertRaises(SMTPConfigurationError):
            SMTPConfiguration.from_settings(
                {**SMTP_SETTINGS, "smtp_use_ssl": "true"}
            )


class SMTPVerificationPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(
            smtp_configuration_fingerprint,
            "auth_email_service must provide SMTP fingerprints",
        )
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.db_path = self.root / "manager.db"
        self.previous_key_file = os.environ.get("SYSTEM_SECRET_KEY_FILE")
        os.environ["SYSTEM_SECRET_KEY_FILE"] = str(self.root / ".system-key")
        self.db = DBManager(str(self.db_path))

    def tearDown(self):
        if hasattr(self, "db"):
            self.db.close()
        if self.previous_key_file is None:
            os.environ.pop("SYSTEM_SECRET_KEY_FILE", None)
        else:
            os.environ["SYSTEM_SECRET_KEY_FILE"] = self.previous_key_file
        self.tempdir.cleanup()

    def test_smtp_change_invalidates_verification_but_identical_save_does_not(self):
        fingerprint = smtp_configuration_fingerprint(
            SMTP_SETTINGS,
            db_path=str(self.db_path),
        )
        self.assertTrue(
            self.db.save_verified_smtp_settings(
                SMTP_SETTINGS,
                fingerprint=fingerprint,
                verified_at="2026-07-11T10:00:00+08:00",
            )
        )

        self.assertTrue(self.db.save_system_settings_section(dict(SMTP_SETTINGS)))
        self.assertEqual(
            self.db.get_system_setting("smtp_verified_fingerprint"),
            fingerprint,
        )

        changed = {**SMTP_SETTINGS, "smtp_server": "smtp2.example.test"}
        self.assertTrue(self.db.save_system_settings_section(changed))
        self.assertEqual(
            self.db.get_system_setting("smtp_verified_fingerprint"),
            "",
        )
        self.assertEqual(self.db.get_system_setting("smtp_verified_at"), "")

    def test_single_setting_change_invalidates_verification(self):
        fingerprint = smtp_configuration_fingerprint(
            SMTP_SETTINGS,
            db_path=str(self.db_path),
        )
        self.assertTrue(
            self.db.save_verified_smtp_settings(
                SMTP_SETTINGS,
                fingerprint=fingerprint,
                verified_at="2026-07-11T10:00:00+08:00",
            )
        )

        self.assertTrue(self.db.set_system_setting("smtp_port", "465"))
        self.assertEqual(
            self.db.get_system_setting("smtp_verified_fingerprint"),
            "",
        )

    def test_verification_result_cannot_overwrite_a_concurrent_smtp_change(self):
        self.assertTrue(self.db.save_system_settings_section(dict(SMTP_SETTINGS)))
        baseline = self.db.get_all_system_settings()
        self.assertTrue(
            self.db.set_system_setting("smtp_server", "new.smtp.example.test")
        )
        old_fingerprint = smtp_configuration_fingerprint(
            SMTP_SETTINGS,
            db_path=str(self.db_path),
        )

        saved = self.db.save_verified_smtp_settings(
            SMTP_SETTINGS,
            fingerprint=old_fingerprint,
            verified_at="2026-07-11T10:00:00+08:00",
            expected_settings=baseline,
        )

        self.assertFalse(saved)
        self.assertEqual(
            self.db.get_system_setting("smtp_server"),
            "new.smtp.example.test",
        )
        self.assertEqual(
            self.db.get_system_setting("smtp_verified_fingerprint"),
            "",
        )

    def test_readiness_requires_switch_verified_smtp_and_an_invite(self):
        settings = {
            **SMTP_SETTINGS,
            "registration_enabled": "true",
            "terms_version": "v1",
        }
        fingerprint = smtp_configuration_fingerprint(
            settings,
            db_path=str(self.db_path),
        )
        settings["smtp_verified_fingerprint"] = fingerprint

        ready = registration_readiness(
            settings,
            db_path=str(self.db_path),
            active_invite_available=True,
        )
        self.assertTrue(ready["enabled"])
        self.assertTrue(ready["ready"])

        no_invite = registration_readiness(
            settings,
            db_path=str(self.db_path),
            active_invite_available=False,
        )
        self.assertFalse(no_invite["enabled"])
        self.assertFalse(no_invite["ready"])

        stale = registration_readiness(
            {**settings, "smtp_port": "465"},
            db_path=str(self.db_path),
            active_invite_available=True,
        )
        self.assertFalse(stale["enabled"])
        self.assertFalse(stale["smtp_verified"])


if __name__ == "__main__":
    unittest.main()
