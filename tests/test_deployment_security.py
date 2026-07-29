import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DeploymentSecurityTests(unittest.TestCase):
    def test_application_port_is_loopback_bound_by_default(self):
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

        self.assertIn('127.0.0.1:${WEB_PORT:-8080}:8080', compose)
        self.assertNotIn('- "${WEB_PORT:-8080}:8080"', compose)

    def test_nginx_redirects_http_and_serves_management_over_tls(self):
        nginx = (ROOT / "nginx" / "nginx.conf").read_text(encoding="utf-8")

        self.assertIn("listen 80 default_server;", nginx)
        self.assertIn("listen 443 ssl default_server;", nginx)
        self.assertIn("server_name xianyu.cxywjx.top;", nginx)
        self.assertIn("return 308 https://xianyu.cxywjx.top$request_uri;", nginx)
        self.assertNotIn("https://$host", nginx)
        self.assertGreaterEqual(nginx.count("return 444;"), 2)
        self.assertIn("listen 443 ssl", nginx)
        self.assertIn("ssl_certificate /etc/nginx/ssl/cert.pem;", nginx)
        self.assertIn("ssl_certificate_key /etc/nginx/ssl/key.pem;", nginx)
        self.assertEqual(nginx.count("proxy_pass http://xianyu_backend;"), 1)


if __name__ == "__main__":
    unittest.main()
