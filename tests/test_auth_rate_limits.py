import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor

import auth_registration_service as registration


def _missing_behavior(*_args, **_kwargs):
    raise AssertionError("auth rate-limit behavior is not implemented")


AuthRateLimiter = getattr(registration, "AuthRateLimiter", None)
RegistrationError = registration.RegistrationError
resolve_client_ip = getattr(registration, "resolve_client_ip", _missing_behavior)


def create_rate_database(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, check_same_thread=False, timeout=10)
    connection.executescript(
        """
        CREATE TABLE auth_rate_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            ip_digest TEXT NOT NULL DEFAULT '',
            email_digest TEXT NOT NULL DEFAULT '',
            account_digest TEXT NOT NULL DEFAULT '',
            success INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL
        );
        CREATE INDEX idx_auth_rate_events_ip
            ON auth_rate_events(event_type, ip_digest, created_at);
        CREATE INDEX idx_auth_rate_events_email
            ON auth_rate_events(event_type, email_digest, created_at);
        CREATE INDEX idx_auth_rate_events_account
            ON auth_rate_events(event_type, account_digest, created_at);
        """
    )
    connection.commit()
    return connection


class ClientIpResolutionTests(unittest.TestCase):
    def test_untrusted_peer_ignores_all_forwarding_headers(self):
        headers = {
            "CF-Connecting-IP": "198.51.100.10",
            "X-Forwarded-For": "198.51.100.11, 10.0.0.2",
            "X-Real-IP": "198.51.100.12",
        }

        self.assertEqual(
            resolve_client_ip("203.0.113.9", headers, ["127.0.0.1", "10.0.0.0/8"]),
            "203.0.113.9",
        )

    def test_trusted_exact_ip_prefers_cf_then_xff_then_real_ip(self):
        trusted = ["127.0.0.1"]
        headers = {
            "cf-connecting-ip": "198.51.100.10",
            "x-forwarded-for": "198.51.100.11, 10.0.0.2",
            "x-real-ip": "198.51.100.12",
        }
        self.assertEqual(resolve_client_ip("127.0.0.1", headers, trusted), "198.51.100.10")

        headers["cf-connecting-ip"] = "invalid-ip"
        self.assertEqual(resolve_client_ip("127.0.0.1", headers, trusted), "198.51.100.11")

        headers["x-forwarded-for"] = "also-invalid, 198.51.100.30"
        self.assertEqual(resolve_client_ip("127.0.0.1", headers, trusted), "198.51.100.12")

    def test_trusted_cidr_accepts_ipv6_and_invalid_headers_fall_back_to_peer(self):
        trusted = ["2001:db8:abcd::/48", "broken-config"]
        self.assertEqual(
            resolve_client_ip(
                "2001:db8:abcd::9",
                {"CF-Connecting-IP": "2001:db8::42"},
                trusted,
            ),
            "2001:db8::42",
        )
        self.assertEqual(
            resolve_client_ip(
                "2001:db8:abcd::9",
                {
                    "CF-Connecting-IP": "bad",
                    "X-Forwarded-For": "bad, 198.51.100.1",
                    "X-Real-IP": "bad",
                },
                trusted,
            ),
            "2001:db8:abcd::9",
        )


class AuthRateLimiterTests(unittest.TestCase):
    def setUp(self):
        if AuthRateLimiter is None:
            self.fail("AuthRateLimiter must implement persisted auth gates")
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.previous_key_file = os.environ.get("SYSTEM_SECRET_KEY_FILE")
        os.environ["SYSTEM_SECRET_KEY_FILE"] = str(self.root / ".system-key")
        self.db_path = self.root / "rate.db"
        self.connection = create_rate_database(self.db_path)
        self.now = 1_800_300_000.0
        self.limiter = AuthRateLimiter(
            self.connection,
            str(self.db_path),
            lock=threading.RLock(),
            clock=lambda: self.now,
        )

    def tearDown(self):
        if hasattr(self, "connection"):
            self.connection.close()
        if self.previous_key_file is None:
            os.environ.pop("SYSTEM_SECRET_KEY_FILE", None)
        else:
            os.environ["SYSTEM_SECRET_KEY_FILE"] = self.previous_key_file
        self.tempdir.cleanup()

    def assert_rate_error(self, code, retry_after, callback):
        with self.assertRaises(RegistrationError) as raised:
            callback()
        self.assertEqual(raised.exception.code, code)
        self.assertEqual(raised.exception.http_status, 429)
        self.assertEqual(raised.exception.status_code, 429)
        self.assertEqual(raised.exception.retry_after, retry_after)
        self.assertTrue(raised.exception.message)

    def test_events_store_only_purpose_isolated_hmac_digests_and_cleanup(self):
        ip = "203.0.113.20"
        email = "Rate.User@Example.COM"
        account = "Synthetic-Account"
        self.limiter.record_event(
            "synthetic",
            ip=ip,
            email=email,
            account=account,
            success=False,
        )

        row = self.connection.execute(
            "SELECT event_type, ip_digest, email_digest, account_digest, success "
            "FROM auth_rate_events"
        ).fetchone()
        self.assertEqual((row[0], row[4]), ("synthetic", 0))
        self.assertTrue(all(len(value) == 64 for value in row[1:4]))
        self.assertEqual(len(set(row[1:4])), 3)
        stored = repr(row)
        self.assertNotIn(ip, stored)
        self.assertNotIn(email.lower(), stored.lower())
        self.assertNotIn(account.lower(), stored.lower())
        self.assertEqual(
            self.limiter.count_events(
                "synthetic", window_seconds=60, ip=ip, success=False
            ),
            1,
        )
        invalid_ip = "not-a-client-ip"
        with self.assertRaises(RegistrationError) as raised:
            self.limiter.record_event("synthetic", ip=invalid_ip)
        self.assertEqual(raised.exception.code, "CLIENT_IP_INVALID")
        self.assertNotIn(invalid_ip, raised.exception.message)
        self.assertIsNone(raised.exception.__cause__)
        self.assertTrue(raised.exception.__suppress_context__)

        self.now += 7 * 86_400 + 1
        self.assertEqual(self.limiter.cleanup_events(), 1)
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM auth_rate_events").fetchone()[0],
            0,
        )

    def test_event_writes_cleanup_week_old_rows_at_most_once_per_hour(self):
        def insert_stale(event_type):
            self.connection.execute(
                "INSERT INTO auth_rate_events (event_type, created_at) VALUES (?, ?)",
                (event_type, int(self.now - 7 * 86_400 - 1)),
            )
            self.connection.commit()

        insert_stale("stale-before-first-write")
        self.limiter.record_event(
            "synthetic-write",
            ip="203.0.113.21",
            success=True,
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM auth_rate_events "
                "WHERE event_type = 'stale-before-first-write'"
            ).fetchone()[0],
            0,
        )

        insert_stale("stale-within-throttle")
        self.now += 3599
        self.limiter.enforce_captcha("203.0.113.22")
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM auth_rate_events "
                "WHERE event_type = 'stale-within-throttle'"
            ).fetchone()[0],
            1,
        )

        self.now += 1
        self.limiter.record_registration_failure("203.0.113.23")
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM auth_rate_events "
                "WHERE event_type = 'stale-within-throttle'"
            ).fetchone()[0],
            0,
        )

    def test_captcha_gate_allows_30_per_ip_per_hour(self):
        for _ in range(30):
            self.limiter.enforce_captcha("203.0.113.30")

        self.assert_rate_error(
            "RATE_LIMIT_CAPTCHA_IP",
            3600,
            lambda: self.limiter.enforce_captcha("203.0.113.30"),
        )
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM auth_rate_events").fetchone()[0],
            30,
        )

    def test_email_gate_enforces_cooldown_hourly_email_and_hourly_ip(self):
        email = "mail-limit@example.com"
        self.limiter.enforce_email_send("203.0.113.40", email)
        self.assert_rate_error(
            "RATE_LIMIT_EMAIL_COOLDOWN",
            60,
            lambda: self.limiter.enforce_email_send("203.0.113.41", email),
        )

        for _ in range(4):
            self.now += 60
            self.limiter.enforce_email_send("203.0.113.40", email)
        self.now += 60
        self.assert_rate_error(
            "RATE_LIMIT_EMAIL_HOURLY",
            3300,
            lambda: self.limiter.enforce_email_send("203.0.113.42", email),
        )

        other_ip = "203.0.113.50"
        for index in range(20):
            self.limiter.enforce_email_send(
                other_ip,
                f"mail-{index}@example.com",
            )
        self.assert_rate_error(
            "RATE_LIMIT_EMAIL_IP",
            3600,
            lambda: self.limiter.enforce_email_send(
                other_ip,
                "mail-overflow@example.com",
            ),
        )

    def test_login_fifth_failure_starts_account_or_ip_cooldown(self):
        for index in range(4):
            self.limiter.record_login_result(
                ip=f"203.0.113.{60 + index}",
                account="same-account",
                success=False,
            )
        self.assert_rate_error(
            "RATE_LIMIT_LOGIN_ACCOUNT",
            900,
            lambda: self.limiter.record_login_result(
                ip="203.0.113.70",
                account="same-account",
                success=False,
            ),
        )
        self.assert_rate_error(
            "RATE_LIMIT_LOGIN_ACCOUNT",
            900,
            lambda: self.limiter.check_login_limit(
                ip="203.0.113.71",
                account="same-account",
            ),
        )

        shared_ip = "203.0.113.80"
        self.limiter.record_login_result(
            ip=shared_ip,
            account="successful-account",
            success=True,
        )
        for index in range(4):
            self.limiter.record_login_result(
                ip=shared_ip,
                account=f"failed-{index}",
                success=False,
            )
        self.assert_rate_error(
            "RATE_LIMIT_LOGIN_IP",
            900,
            lambda: self.limiter.record_login_result(
                ip=shared_ip,
                account="failed-final",
                success=False,
            ),
        )

    def test_login_lockout_runs_for_15_minutes_from_spread_fifth_failure(self):
        account = "spread-account"
        base_time = self.now
        for index, offset in enumerate((0, 200, 400, 600)):
            self.now = base_time + offset
            self.limiter.record_login_result(
                ip=f"203.0.113.{100 + index}",
                account=account,
                success=False,
            )

        self.now = base_time + 899
        self.assert_rate_error(
            "RATE_LIMIT_LOGIN_ACCOUNT",
            900,
            lambda: self.limiter.record_login_result(
                ip="203.0.113.110",
                account=account,
                success=False,
            ),
        )
        lockout = self.connection.execute(
            "SELECT account_digest, ip_digest, created_at "
            "FROM auth_rate_events WHERE event_type = 'login_lockout'"
        ).fetchone()
        self.assertIsNotNone(lockout)
        self.assertTrue(lockout[0])
        self.assertEqual(lockout[1], "")
        self.assertEqual(lockout[2], self.now)

        self.now += 899
        self.assert_rate_error(
            "RATE_LIMIT_LOGIN_ACCOUNT",
            1,
            lambda: self.limiter.check_login_limit(
                ip="203.0.113.111",
                account=account,
            ),
        )
        self.now += 1
        self.limiter.check_login_limit(
            ip="203.0.113.111",
            account=account,
        )

    def test_fractional_login_lockout_preserves_full_15_minute_cooldown(self):
        self.now += 0.25
        account = "fractional-account"
        for index in range(4):
            self.limiter.record_login_result(
                ip=f"198.51.100.{10 + index}",
                account=account,
                success=False,
            )

        lockout_at = self.now
        with self.assertRaises(RegistrationError) as raised:
            self.limiter.record_login_result(
                ip="198.51.100.14",
                account=account,
                success=False,
            )
        self.assertEqual(raised.exception.code, "RATE_LIMIT_LOGIN_ACCOUNT")
        self.assertGreaterEqual(raised.exception.retry_after, 900)
        stored_at, storage_type = self.connection.execute(
            "SELECT created_at, typeof(created_at) FROM auth_rate_events "
            "WHERE event_type = 'login_lockout'"
        ).fetchone()
        self.assertEqual(stored_at, lockout_at)
        self.assertEqual(storage_type, "real")

        self.now = lockout_at + 899.9
        self.assert_rate_error(
            "RATE_LIMIT_LOGIN_ACCOUNT",
            1,
            lambda: self.limiter.check_login_limit(
                ip="198.51.100.15",
                account=account,
            ),
        )
        self.now = lockout_at + 900
        self.limiter.check_login_limit(
            ip="198.51.100.15",
            account=account,
        )

    def test_account_lockout_does_not_lock_the_last_source_ip(self):
        account = "target-account"
        final_ip = "198.51.100.25"
        for index in range(4):
            self.limiter.record_login_result(
                ip=f"198.51.100.{20 + index}",
                account=account,
                success=False,
            )
        self.assert_rate_error(
            "RATE_LIMIT_LOGIN_ACCOUNT",
            900,
            lambda: self.limiter.record_login_result(
                ip=final_ip,
                account=account,
                success=False,
            ),
        )

        lockouts = self.connection.execute(
            "SELECT ip_digest, account_digest FROM auth_rate_events "
            "WHERE event_type = 'login_lockout'"
        ).fetchall()
        self.assertEqual(len(lockouts), 1)
        self.assertEqual(lockouts[0][0], "")
        self.assertTrue(lockouts[0][1])
        self.limiter.check_login_limit(
            ip=final_ip,
            account="unrelated-account",
        )

    def test_ip_lockout_does_not_lock_the_last_target_account(self):
        shared_ip = "198.51.100.30"
        final_account = "target-final"
        for index in range(4):
            self.limiter.record_login_result(
                ip=shared_ip,
                account=f"target-{index}",
                success=False,
            )
        self.assert_rate_error(
            "RATE_LIMIT_LOGIN_IP",
            900,
            lambda: self.limiter.record_login_result(
                ip=shared_ip,
                account=final_account,
                success=False,
            ),
        )

        lockouts = self.connection.execute(
            "SELECT ip_digest, account_digest FROM auth_rate_events "
            "WHERE event_type = 'login_lockout'"
        ).fetchall()
        self.assertEqual(len(lockouts), 1)
        self.assertTrue(lockouts[0][0])
        self.assertEqual(lockouts[0][1], "")
        self.limiter.check_login_limit(
            ip="198.51.100.31",
            account=final_account,
        )

    def test_same_account_and_ip_thresholds_create_separate_lockouts(self):
        ip = "198.51.100.40"
        account = "same-target"
        for _ in range(4):
            self.limiter.record_login_result(
                ip=ip,
                account=account,
                success=False,
            )
        self.assert_rate_error(
            "RATE_LIMIT_LOGIN_ACCOUNT",
            900,
            lambda: self.limiter.record_login_result(
                ip=ip,
                account=account,
                success=False,
            ),
        )

        lockouts = self.connection.execute(
            "SELECT ip_digest, account_digest FROM auth_rate_events "
            "WHERE event_type = 'login_lockout' ORDER BY id"
        ).fetchall()
        self.assertEqual(len(lockouts), 2)
        self.assertEqual(
            {(bool(ip_digest), bool(account_digest)) for ip_digest, account_digest in lockouts},
            {(False, True), (True, False)},
        )

    def test_concurrent_failures_are_serialized_without_lost_events(self):
        ip = "198.51.100.41"
        account = "concurrent-target"
        barrier = threading.Barrier(5)

        def record_failure(_index):
            connection = sqlite3.connect(
                self.db_path,
                check_same_thread=False,
                timeout=10,
            )
            limiter = AuthRateLimiter(
                connection,
                str(self.db_path),
                lock=threading.RLock(),
                clock=lambda: self.now,
            )
            try:
                barrier.wait(timeout=10)
                try:
                    limiter.record_login_result(
                        ip=ip,
                        account=account,
                        success=False,
                    )
                    return "recorded"
                except RegistrationError as exc:
                    return exc.code
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=5) as pool:
            results = list(pool.map(record_failure, range(5)))

        self.assertEqual(results.count("recorded"), 4)
        self.assertEqual(results.count("RATE_LIMIT_LOGIN_ACCOUNT"), 1)
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM auth_rate_events "
                "WHERE event_type = 'login' AND success = 0"
            ).fetchone()[0],
            5,
        )
        lockouts = self.connection.execute(
            "SELECT ip_digest, account_digest FROM auth_rate_events "
            "WHERE event_type = 'login_lockout' ORDER BY id"
        ).fetchall()
        self.assertEqual(
            {(bool(ip_digest), bool(account_digest)) for ip_digest, account_digest in lockouts},
            {(False, True), (True, False)},
        )
        self.assertEqual(len(lockouts), 2)

    def test_tenth_registration_failure_starts_ip_cooldown(self):
        ip = "203.0.113.90"
        for _ in range(9):
            self.limiter.record_registration_failure(ip)

        self.assert_rate_error(
            "RATE_LIMIT_REGISTRATION_IP",
            3600,
            lambda: self.limiter.record_registration_failure(ip),
        )
        self.assert_rate_error(
            "RATE_LIMIT_REGISTRATION_IP",
            3600,
            lambda: self.limiter.check_registration_limit(ip),
        )


if __name__ == "__main__":
    unittest.main()
