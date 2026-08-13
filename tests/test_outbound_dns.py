import socket
import unittest
from unittest.mock import patch

import utils.outbound_dns as outbound_dns


class ParseResolversTests(unittest.TestCase):
    def test_parses_single_host_and_port(self):
        self.assertEqual(outbound_dns._parse_resolvers("127.0.0.1:5053"), [("127.0.0.1", 5053)])

    def test_defaults_port_53(self):
        self.assertEqual(outbound_dns._parse_resolvers("10.0.0.1"), [("10.0.0.1", 53)])

    def test_parses_comma_separated_list(self):
        self.assertEqual(
            outbound_dns._parse_resolvers("127.0.0.1:5053, 223.5.5.5 ,1.1.1.1:53"),
            [("127.0.0.1", 5053), ("223.5.5.5", 53), ("1.1.1.1", 53)],
        )

    def test_blank_and_invalid_yield_empty_or_skip(self):
        self.assertEqual(outbound_dns._parse_resolvers(""), [])
        self.assertEqual(outbound_dns._parse_resolvers(None), [])
        self.assertEqual(outbound_dns._parse_resolvers("127.0.0.1:0, 8.8.8.8"), [("8.8.8.8", 53)])


class DirectHostTests(unittest.TestCase):
    def test_localhost_ip_singlelabel_are_direct(self):
        for host in ("localhost", "foo.local", "127.0.0.1", "8.8.8.8", "[::1]", "intranet-host"):
            self.assertTrue(outbound_dns._is_direct_host(host), host)

    def test_public_domain_is_not_direct(self):
        for host in ("h5api.m.goofish.com", "api.deepseek.com", "wss-goofish.dingtalk.com"):
            self.assertFalse(outbound_dns._is_direct_host(host), host)


class FakeIpTests(unittest.TestCase):
    def test_fake_ip_detection(self):
        self.assertTrue(outbound_dns._is_fake_ip("198.18.0.24"))
        self.assertTrue(outbound_dns._is_fake_ip("198.19.255.1"))
        self.assertFalse(outbound_dns._is_fake_ip("59.82.121.58"))
        self.assertFalse(outbound_dns._is_fake_ip("not-an-ip"))


class ResolveViaResolverTests(unittest.TestCase):
    def setUp(self):
        self._saved = (outbound_dns._RESOLVERS, dict(outbound_dns._CACHE), dict(outbound_dns._DEAD))
        outbound_dns._RESOLVERS = [("127.0.0.1", 5053), ("223.5.5.5", 53), ("1.1.1.1", 53)]
        outbound_dns._CACHE.clear()
        outbound_dns._DEAD.clear()

    def tearDown(self):
        outbound_dns._RESOLVERS, cache, dead = self._saved
        outbound_dns._CACHE.clear()
        outbound_dns._CACHE.update(cache)
        outbound_dns._DEAD.clear()
        outbound_dns._DEAD.update(dead)

    def test_first_resolver_success_is_used_and_cached(self):
        with patch.object(outbound_dns, "_query_a_records", return_value=["59.82.113.36"]) as spy:
            first = outbound_dns._resolve_via_resolver("h5api.m.goofish.com")
            second = outbound_dns._resolve_via_resolver("h5api.m.goofish.com")
        self.assertEqual(first, ["59.82.113.36"])
        self.assertEqual(second, ["59.82.113.36"])
        # 第二次命中缓存，不再查询。
        self.assertEqual(spy.call_count, 1)

    def test_failover_to_next_resolver_and_marks_dead(self):
        calls = []

        def fake_query(host, resolver):
            calls.append(resolver)
            if resolver == ("127.0.0.1", 5053):
                raise TimeoutError("5053 down")
            return ["203.119.252.50"]

        with patch.object(outbound_dns, "_query_a_records", side_effect=fake_query):
            result = outbound_dns._resolve_via_resolver("passport.goofish.com")
        self.assertEqual(result, ["203.119.252.50"])
        self.assertIn(("127.0.0.1", 5053), calls)
        self.assertIn(("223.5.5.5", 53), calls)
        # 失败的 5053 被冷却，后续跳过。
        self.assertGreater(outbound_dns._DEAD.get(("127.0.0.1", 5053), 0), 0)

    def test_fake_ip_answers_are_discarded_and_resolver_cooled(self):
        def fake_query(host, resolver):
            if resolver == ("127.0.0.1", 5053):
                return ["198.18.0.24"]  # 被污染，应丢弃并冷却
            return ["59.82.121.58"]

        with patch.object(outbound_dns, "_query_a_records", side_effect=fake_query):
            result = outbound_dns._resolve_via_resolver("h5api.m.goofish.com")
        self.assertEqual(result, ["59.82.121.58"])
        self.assertGreater(outbound_dns._DEAD.get(("127.0.0.1", 5053), 0), 0)

    def test_all_fail_serves_stale_before_system_fallback(self):
        # 先成功缓存一次，制造陈旧记录。
        with patch.object(outbound_dns, "_query_a_records", return_value=["59.82.113.36"]):
            outbound_dns._resolve_via_resolver("h5api.m.goofish.com")
        # 让缓存新鲜期过期但陈旧期仍有效。
        fresh_until, stale_until, ips = outbound_dns._CACHE["h5api.m.goofish.com"]
        import time
        outbound_dns._CACHE["h5api.m.goofish.com"] = (time.time() - 1, stale_until, ips)
        outbound_dns._DEAD.clear()
        with patch.object(outbound_dns, "_query_a_records", return_value=[]):
            result = outbound_dns._resolve_via_resolver("h5api.m.goofish.com")
        self.assertEqual(result, ["59.82.113.36"])  # serve-stale

    def test_all_fail_no_stale_returns_empty(self):
        with patch.object(outbound_dns, "_query_a_records", return_value=[]):
            result = outbound_dns._resolve_via_resolver("brand-new.example.com")
        self.assertEqual(result, [])


class PatchedGetaddrinfoTests(unittest.TestCase):
    def setUp(self):
        self._saved_resolvers = outbound_dns._RESOLVERS
        self._saved_original = outbound_dns._ORIGINAL_GETADDRINFO
        outbound_dns._RESOLVERS = [("127.0.0.1", 5053)]
        outbound_dns._ORIGINAL_GETADDRINFO = self._fake_original
        outbound_dns._CACHE.clear()
        outbound_dns._DEAD.clear()

    def tearDown(self):
        outbound_dns._RESOLVERS = self._saved_resolvers
        outbound_dns._ORIGINAL_GETADDRINFO = self._saved_original
        outbound_dns._CACHE.clear()
        outbound_dns._DEAD.clear()

    def _fake_original(self, host, port, family=0, type=0, proto=0, flags=0):
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("198.18.0.1", port or 0))]

    def test_public_domain_uses_resolver(self):
        with patch.object(outbound_dns, "_query_a_records", return_value=["59.82.113.36"]):
            result = outbound_dns._patched_getaddrinfo("h5api.m.goofish.com", 443, type=socket.SOCK_STREAM)
        self.assertEqual([entry[4][0] for entry in result], ["59.82.113.36"])

    def test_literal_ip_localhost_fall_back_to_system(self):
        with patch.object(outbound_dns, "_query_a_records", return_value=["1.2.3.4"]) as spy:
            ip_result = outbound_dns._patched_getaddrinfo("127.0.0.1", 8081, type=socket.SOCK_STREAM)
            local_result = outbound_dns._patched_getaddrinfo("localhost", 8091)
        spy.assert_not_called()
        self.assertEqual(ip_result[0][4][0], "198.18.0.1")
        self.assertEqual(local_result[0][4][0], "198.18.0.1")

    def test_ipv6_and_service_port_fall_back_to_system(self):
        with patch.object(outbound_dns, "_query_a_records", return_value=["59.82.113.36"]) as spy:
            v6 = outbound_dns._patched_getaddrinfo("h5api.m.goofish.com", 443, family=socket.AF_INET6)
            svc = outbound_dns._patched_getaddrinfo("h5api.m.goofish.com", "https")
        spy.assert_not_called()
        self.assertEqual(v6[0][4][0], "198.18.0.1")
        self.assertEqual(svc[0][4][0], "198.18.0.1")

    def test_all_resolvers_fail_falls_back_to_system(self):
        with patch.object(outbound_dns, "_query_a_records", return_value=[]):
            result = outbound_dns._patched_getaddrinfo("api.deepseek.com", 443)
        self.assertEqual(result[0][4][0], "198.18.0.1")


class ProxyEnvTests(unittest.TestCase):
    def test_neutralize_removes_proxy_vars_and_reports(self):
        import os
        with patch.dict("os.environ", {
            "HTTP_PROXY": "http://127.0.0.1:1082",
            "HTTPS_PROXY": "http://127.0.0.1:1082",
            "NO_PROXY": "127.0.0.1,localhost",
        }, clear=False):
            removed = outbound_dns.neutralize_inherited_proxy_env()
            self.assertIn("HTTP_PROXY", removed)
            self.assertIn("HTTPS_PROXY", removed)
            self.assertNotIn("HTTP_PROXY", os.environ)
            self.assertNotIn("HTTPS_PROXY", os.environ)
            # NO_PROXY 保留（无害）。
            self.assertEqual(os.environ.get("NO_PROXY"), "127.0.0.1,localhost")


class InstallTests(unittest.TestCase):
    def setUp(self):
        self._saved_original = outbound_dns._ORIGINAL_GETADDRINFO
        self._saved_resolvers = outbound_dns._RESOLVERS
        self._saved_socket = socket.getaddrinfo

    def tearDown(self):
        socket.getaddrinfo = self._saved_socket
        outbound_dns._ORIGINAL_GETADDRINFO = self._saved_original
        outbound_dns._RESOLVERS = self._saved_resolvers

    def test_no_resolver_env_is_noop(self):
        import os
        outbound_dns._ORIGINAL_GETADDRINFO = None
        outbound_dns._RESOLVERS = []
        with patch.dict("os.environ", {}, clear=False):
            os.environ.pop("OUTBOUND_DNS_RESOLVER", None)
            self.assertFalse(outbound_dns.install_outbound_dns_patch())
        self.assertIs(socket.getaddrinfo, self._saved_socket)

    def test_install_combines_env_and_fallbacks(self):
        outbound_dns._ORIGINAL_GETADDRINFO = None
        outbound_dns._RESOLVERS = []
        env = {"OUTBOUND_DNS_RESOLVER": "127.0.0.1:5053"}
        env.pop("OUTBOUND_DNS_FALLBACKS", None)
        with patch.dict("os.environ", env, clear=False):
            import os
            os.environ.pop("OUTBOUND_DNS_FALLBACKS", None)
            self.assertTrue(outbound_dns.install_outbound_dns_patch())
        self.assertEqual(outbound_dns._RESOLVERS[0], ("127.0.0.1", 5053))
        self.assertIn(("223.5.5.5", 53), outbound_dns._RESOLVERS)
        self.assertIs(socket.getaddrinfo, outbound_dns._patched_getaddrinfo)

    def test_install_is_idempotent(self):
        outbound_dns._ORIGINAL_GETADDRINFO = None
        outbound_dns._RESOLVERS = []
        with patch.dict("os.environ", {"OUTBOUND_DNS_RESOLVER": "127.0.0.1:5053"}, clear=False):
            self.assertTrue(outbound_dns.install_outbound_dns_patch())
            first_original = outbound_dns._ORIGINAL_GETADDRINFO
            self.assertTrue(outbound_dns.install_outbound_dns_patch())
            self.assertIs(outbound_dns._ORIGINAL_GETADDRINFO, first_original)

    def test_custom_fallbacks_override(self):
        outbound_dns._ORIGINAL_GETADDRINFO = None
        outbound_dns._RESOLVERS = []
        with patch.dict("os.environ", {
            "OUTBOUND_DNS_RESOLVER": "127.0.0.1:5053",
            "OUTBOUND_DNS_FALLBACKS": "9.9.9.9",
        }, clear=False):
            self.assertTrue(outbound_dns.install_outbound_dns_patch())
        self.assertIn(("9.9.9.9", 53), outbound_dns._RESOLVERS)
        self.assertNotIn(("223.5.5.5", 53), outbound_dns._RESOLVERS)


if __name__ == "__main__":
    unittest.main()
