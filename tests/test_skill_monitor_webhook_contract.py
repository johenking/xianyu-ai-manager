import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests

import reply_server


class _IdempotentReceiver(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    calls = []
    accepted = set()
    fail_before_accept = 1

    def log_message(self, _format, *_args):
        return

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        key = self.headers.get("Idempotency-Key") or ""
        type(self).calls.append({
            "path": self.path,
            "idempotency_key": key,
            "payload_key": payload.get("idempotency_key"),
        })
        if type(self).fail_before_accept > 0:
            type(self).fail_before_accept -= 1
            body = b'{"ok":false,"synthetic":"retry"}'
            self.send_response(503)
            self.send_header("Retry-After", "0")
        else:
            duplicate = key in type(self).accepted
            type(self).accepted.add(key)
            body = json.dumps({"ok": True, "duplicate": duplicate}).encode()
            self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class SkillMonitorLocalWebhookContractTests(unittest.TestCase):
    def setUp(self):
        _IdempotentReceiver.calls = []
        _IdempotentReceiver.accepted = set()
        _IdempotentReceiver.fail_before_accept = 1
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _IdempotentReceiver)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def _send(self):
        reply_server._send_skill_notification_to_channel(
            {
                "type": "webhook",
                "config": {
                    "webhook_url": (
                        f"http://127.0.0.1:{self.server.server_port}/synthetic-hook"
                    )
                },
            },
            {"id": 3, "keyword": "synthetic-keyword"},
            {
                "id": 9,
                "title": "synthetic-item",
                "item_url": "https://example.test/synthetic-item",
                "_delivery_idempotency_key": "delivery:v1:synthetic-contract",
            },
        )

    def test_retry_and_duplicate_delivery_reuse_the_stable_key(self):
        with self.assertRaises(requests.HTTPError):
            self._send()
        self._send()
        self._send()

        self.assertEqual(len(_IdempotentReceiver.calls), 3)
        self.assertEqual(
            {call["idempotency_key"] for call in _IdempotentReceiver.calls},
            {"delivery:v1:synthetic-contract"},
        )
        self.assertEqual(
            {call["payload_key"] for call in _IdempotentReceiver.calls},
            {"delivery:v1:synthetic-contract"},
        )
        self.assertEqual(
            _IdempotentReceiver.accepted,
            {"delivery:v1:synthetic-contract"},
        )


if __name__ == "__main__":
    unittest.main()
