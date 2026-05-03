from __future__ import annotations

import unittest

from snakesh.services.mtu_tools import MtuCalculationRequest, calculate_mtu, format_mtu_summary


class MtuToolsTests(unittest.TestCase):
    def test_calculate_mtu_ipv4_defaults(self) -> None:
        result = calculate_mtu(MtuCalculationRequest(outer_mtu=1500))

        self.assertEqual(result.effective_mtu, 1500)
        self.assertEqual(result.max_ping_payload, 1472)
        self.assertEqual(result.max_udp_payload, 1472)
        self.assertEqual(result.tcp_mss, 1460)
        self.assertEqual(result.advisories, ())

    def test_calculate_mtu_ipv6_values(self) -> None:
        result = calculate_mtu(MtuCalculationRequest(outer_mtu=1500, ip_version=6))

        self.assertEqual(result.effective_mtu, 1500)
        self.assertEqual(result.max_ping_payload, 1452)
        self.assertEqual(result.max_udp_payload, 1452)
        self.assertEqual(result.tcp_mss, 1440)

    def test_calculate_mtu_applies_fixed_preset(self) -> None:
        result = calculate_mtu(MtuCalculationRequest(outer_mtu=1500, overhead_preset_id="pppoe_vlan"))

        self.assertEqual(result.extra_overhead_bytes, 12)
        self.assertEqual(result.effective_mtu, 1488)
        self.assertEqual(result.max_ping_payload, 1460)
        self.assertEqual(result.tcp_mss, 1448)

    def test_calculate_mtu_applies_custom_overhead(self) -> None:
        result = calculate_mtu(
            MtuCalculationRequest(
                outer_mtu=9000,
                overhead_preset_id="custom",
                custom_overhead=32,
            )
        )

        self.assertEqual(result.extra_overhead_bytes, 32)
        self.assertEqual(result.effective_mtu, 8968)

    def test_calculate_mtu_adds_ipv6_minimum_advisory(self) -> None:
        result = calculate_mtu(MtuCalculationRequest(outer_mtu=1200, ip_version=6))

        self.assertEqual(result.effective_mtu, 1200)
        self.assertEqual(result.advisories, ("Effective MTU is below the IPv6 minimum MTU of 1280 bytes.",))

    def test_calculate_mtu_uses_na_for_non_positive_tcp_mss(self) -> None:
        result = calculate_mtu(MtuCalculationRequest(outer_mtu=40))

        self.assertEqual(result.max_ping_payload, 12)
        self.assertIsNone(result.tcp_mss)
        self.assertIn("TCP MSS (no options): N/A", format_mtu_summary(result))

    def test_calculate_mtu_rejects_too_small_effective_mtu(self) -> None:
        with self.assertRaises(ValueError):
            calculate_mtu(MtuCalculationRequest(outer_mtu=28))


if __name__ == "__main__":
    unittest.main()
