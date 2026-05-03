from __future__ import annotations

import unittest

from snakesh.services.subnet_tools import split_network, summarize_cidr


class SubnetToolsTests(unittest.TestCase):
    def test_summarize_cidr_returns_expected_network_details(self) -> None:
        summary = summarize_cidr("198.51.100.55/24")
        self.assertEqual(summary.ip_address, "198.51.100.55")
        self.assertEqual(summary.network, "198.51.100.0")
        self.assertEqual(summary.prefix, 24)
        self.assertEqual(summary.netmask, "255.255.255.0")
        self.assertEqual(summary.wildcard, "0.0.0.255")
        self.assertEqual(summary.broadcast, "198.51.100.255")
        self.assertEqual(summary.first_host, "198.51.100.1")
        self.assertEqual(summary.last_host, "198.51.100.254")
        self.assertEqual(summary.usable_hosts, 254)

    def test_summarize_plain_ip_defaults_to_32(self) -> None:
        summary = summarize_cidr("203.0.113.3")
        self.assertEqual(summary.prefix, 32)
        self.assertEqual(summary.network, "203.0.113.3")
        self.assertEqual(summary.first_host, "203.0.113.3")
        self.assertEqual(summary.last_host, "203.0.113.3")
        self.assertEqual(summary.usable_hosts, 1)

    def test_summarize_31_has_two_usable_hosts(self) -> None:
        summary = summarize_cidr("203.0.113.8/31")
        self.assertEqual(summary.first_host, "203.0.113.8")
        self.assertEqual(summary.last_host, "203.0.113.9")
        self.assertEqual(summary.usable_hosts, 2)

    def test_split_network_creates_expected_subnets(self) -> None:
        subnets = split_network("198.51.100.0/24", new_prefix=26, max_results=10)
        self.assertEqual(len(subnets), 4)
        self.assertEqual(f"{subnets[0].subnet}/{subnets[0].prefix}", "198.51.100.0/26")
        self.assertEqual(f"{subnets[3].subnet}/{subnets[3].prefix}", "198.51.100.192/26")
        self.assertEqual(subnets[0].usable_hosts, 62)

    def test_split_network_respects_result_limit(self) -> None:
        subnets = split_network("198.18.0.0/16", new_prefix=24, max_results=5)
        self.assertEqual(len(subnets), 5)
        self.assertEqual(f"{subnets[0].subnet}/{subnets[0].prefix}", "198.18.0.0/24")
        self.assertEqual(f"{subnets[4].subnet}/{subnets[4].prefix}", "198.18.4.0/24")

    def test_split_network_rejects_broader_target_prefix(self) -> None:
        with self.assertRaises(ValueError):
            split_network("198.18.0.0/24", new_prefix=23, max_results=10)

    def test_summarize_ipv6_returns_expected_network_details(self) -> None:
        summary = summarize_cidr("2001:db8::1/64", ip_version=6)
        self.assertEqual(summary.ip_address, "2001:db8::1")
        self.assertEqual(summary.network, "2001:db8::")
        self.assertEqual(summary.prefix, 64)
        self.assertEqual(summary.first_host, "2001:db8::")
        self.assertEqual(summary.last_host, "2001:db8::ffff:ffff:ffff:ffff")
        self.assertEqual(summary.usable_hosts, 18446744073709551616)

    def test_split_ipv6_network_creates_expected_subnets(self) -> None:
        subnets = split_network("2001:db8::/126", new_prefix=127, max_results=10, ip_version=6)
        self.assertEqual(len(subnets), 2)
        self.assertEqual(f"{subnets[0].subnet}/{subnets[0].prefix}", "2001:db8::/127")
        self.assertEqual(f"{subnets[1].subnet}/{subnets[1].prefix}", "2001:db8::2/127")
        self.assertEqual(subnets[0].usable_hosts, 2)

    def test_summarize_rejects_wrong_ip_version_hint(self) -> None:
        with self.assertRaises(ValueError):
            summarize_cidr("2001:db8::1/64", ip_version=4)

    def test_split_rejects_wrong_ip_version_hint(self) -> None:
        with self.assertRaises(ValueError):
            split_network("198.18.0.0/24", new_prefix=25, max_results=10, ip_version=6)


if __name__ == "__main__":
    unittest.main()
