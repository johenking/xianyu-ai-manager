import unittest
from unittest.mock import Mock, patch

from utils.outbound_http import OutboundRequestError, resolve_public_host_sync
from utils.outbound_smtp import open_public_smtp


class OutboundSmtpTests(unittest.TestCase):
    def test_private_smtp_host_is_denied(self):
        for host in ("127.0.0.1", "169.254.169.254", "::1", "10.0.0.1"):
            with self.subTest(host=host), self.assertRaises(OutboundRequestError) as raised:
                resolve_public_host_sync(host, 587)
            self.assertEqual(raised.exception.code, "non_public_address_denied")

    def test_mixed_smtp_dns_answer_is_denied(self):
        with patch(
            "utils.outbound_http.socket.getaddrinfo",
            return_value=[
                (2, 1, 6, "", ("8.8.8.8", 587)),
                (2, 1, 6, "", ("127.0.0.1", 587)),
            ],
        ):
            with self.assertRaises(OutboundRequestError) as raised:
                resolve_public_host_sync("smtp.example.test", 587)
        self.assertEqual(raised.exception.code, "non_public_address_denied")

    def test_open_smtp_passes_logical_host_and_pinned_address_separately(self):
        connection = Mock()
        with patch(
            "utils.outbound_smtp.resolve_public_host_sync",
            return_value=("smtp.example.test", ("8.8.8.8",)),
        ), patch(
            "utils.outbound_smtp._PinnedSMTP",
            return_value=connection,
        ) as smtp_factory:
            result = open_public_smtp(
                "smtp.example.test",
                587,
                use_ssl=False,
                timeout_seconds=12,
            )

        self.assertIs(result, connection)
        self.assertEqual(
            smtp_factory.call_args.args[:3],
            ("smtp.example.test", "8.8.8.8", 587),
        )
