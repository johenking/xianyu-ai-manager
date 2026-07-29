"""Public-only SMTP connections with DNS-pinned sockets and verified TLS."""

from __future__ import annotations

import smtplib
import socket
import ssl
from typing import Optional

from utils.outbound_http import OutboundRequestError, resolve_public_host_sync


class _PinnedSMTP(smtplib.SMTP):
    def __init__(
        self,
        logical_host: str,
        pinned_address: str,
        port: int,
        *,
        timeout: float,
    ) -> None:
        self._pinned_address = pinned_address
        super().__init__(logical_host, port, timeout=timeout)

    def _get_socket(self, host: str, port: int, timeout: float):
        del host
        return socket.create_connection(
            (self._pinned_address, port),
            timeout,
            self.source_address,
        )


class _PinnedSMTPSSL(smtplib.SMTP_SSL):
    def __init__(
        self,
        logical_host: str,
        pinned_address: str,
        port: int,
        *,
        timeout: float,
        context: ssl.SSLContext,
    ) -> None:
        self._pinned_address = pinned_address
        super().__init__(
            logical_host,
            port,
            timeout=timeout,
            context=context,
        )

    def _get_socket(self, host: str, port: int, timeout: float):
        del host
        raw_socket = socket.create_connection(
            (self._pinned_address, port),
            timeout,
            self.source_address,
        )
        try:
            return self.context.wrap_socket(
                raw_socket,
                server_hostname=self._host,
            )
        except Exception:
            raw_socket.close()
            raise


def open_public_smtp(
    host: str,
    port: int,
    *,
    use_ssl: bool,
    timeout_seconds: float = 20,
    tls_context: Optional[ssl.SSLContext] = None,
) -> smtplib.SMTP:
    """Open SMTP to one of the DNS-pinned public addresses for ``host``."""
    logical_host, addresses = resolve_public_host_sync(host, port)
    context = tls_context or ssl.create_default_context()
    last_error: Optional[Exception] = None
    for address in addresses:
        try:
            if use_ssl:
                return _PinnedSMTPSSL(
                    logical_host,
                    address,
                    int(port),
                    timeout=float(timeout_seconds),
                    context=context,
                )
            return _PinnedSMTP(
                logical_host,
                address,
                int(port),
                timeout=float(timeout_seconds),
            )
        except (OSError, smtplib.SMTPException, ssl.SSLError) as exc:
            last_error = exc
    raise OutboundRequestError(
        "smtp_connection_failed",
        "outbound SMTP connection failed",
    ) from last_error
