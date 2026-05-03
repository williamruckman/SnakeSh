from __future__ import annotations

import threading
import unittest
from unittest.mock import patch

from snakesh.services import network_tools
from snakesh.services.network_tools import (
    ASNLookupRequest,
    IPScanRequest,
    DNSLookupResult,
    DNSLookupRequest,
    PingRequest,
    WhoisLookupRequest,
    WhoisLookupResult,
    build_ping_command,
    expand_ip_scan_targets,
    format_dns_result,
    format_whois_result,
    normalize_asn_query,
    parse_ip_scan_ports,
    perform_asn_lookup,
    perform_ip_scan,
    perform_whois_lookup,
)


class NetworkToolsTests(unittest.TestCase):
    def test_normalize_asn_query_accepts_prefixed_and_bare_values(self) -> None:
        self.assertEqual(normalize_asn_query("15169"), "AS15169")
        self.assertEqual(normalize_asn_query("as13335"), "AS13335")

    def test_normalize_asn_query_rejects_invalid_values(self) -> None:
        with self.assertRaises(ValueError):
            normalize_asn_query("AS0")
        with self.assertRaises(ValueError):
            normalize_asn_query("ASABC")

    def test_build_ping_command_windows_ipv6(self) -> None:
        command = build_ping_command(
            PingRequest(target="2001:4860:4860::8888", count=2, timeout_ms=1200, packet_size=64, ipv6=True),
            platform_name="windows",
        )
        self.assertEqual(
            command,
            ["ping", "-6", "-n", "2", "-w", "1200", "-l", "64", "2001:4860:4860::8888"],
        )

    def test_build_ping_command_linux(self) -> None:
        command = build_ping_command(
            PingRequest(target="1.1.1.1", count=3, timeout_ms=2500, packet_size=120),
            platform_name="linux",
        )
        self.assertEqual(command, ["ping", "-c", "3", "-W", "3", "-s", "120", "1.1.1.1"])

    def test_perform_whois_lookup_follows_referral(self) -> None:
        def fake_query(server: str, query: str, timeout_seconds: float) -> str:
            self.assertEqual(query, "example.com")
            self.assertGreater(timeout_seconds, 0)
            if server == "whois.iana.org":
                return "refer: whois.example-registry.test\n"
            if server == "whois.example-registry.test":
                return "domain: EXAMPLE.COM\nstatus: active\n"
            return ""

        with patch("snakesh.services.network_tools._query_whois_server", side_effect=fake_query):
            result = perform_whois_lookup(
                WhoisLookupRequest(query="example.com", follow_referral=True, max_referrals=2)
            )
        self.assertEqual(
            [server for server, _payload in result.sections],
            ["whois.iana.org", "whois.example-registry.test"],
        )

    def test_perform_whois_lookup_with_explicit_server_skips_referral(self) -> None:
        with patch(
            "snakesh.services.network_tools._query_whois_server",
            return_value="refer: whois.another-server.test\n",
        ):
            result = perform_whois_lookup(
                WhoisLookupRequest(
                    query="example.com",
                    server="whois.verisign-grs.com",
                    follow_referral=True,
                    max_referrals=5,
                )
            )
        self.assertEqual([server for server, _payload in result.sections], ["whois.verisign-grs.com"])

    def test_format_dns_result_contains_all_sections(self) -> None:
        report = format_dns_result(
            DNSLookupResult(
                query="example.com",
                record_type="A",
                resolver="8.8.8.8",
                status="NOERROR",
                elapsed_ms=14.2,
                answer_lines=["example.com.\t300\tIN\tA\t93.184.216.34"],
                authority_lines=[],
                additional_lines=[],
            )
        )
        self.assertIn("ANSWER SECTION", report)
        self.assertIn("AUTHORITY SECTION", report)
        self.assertIn("ADDITIONAL SECTION", report)
        self.assertIn("93.184.216.34", report)

    def test_format_whois_result_includes_server_headers(self) -> None:
        report = format_whois_result(
            WhoisLookupResult(
                query="example.com",
                sections=[("whois.iana.org", "refer: whois.verisign-grs.com\n")],
            )
        )
        self.assertIn("Response", report)
        self.assertIn("whois.iana.org", report)

    def test_perform_asn_lookup_parses_common_fields(self) -> None:
        with patch(
            "snakesh.services.network_tools.perform_whois_lookup",
            return_value=WhoisLookupResult(
                query="AS64500",
                sections=[
                    ("whois.iana.org", "refer: whois.example.net\n"),
                    (
                        "whois.example.net",
                        "\n".join(
                            [
                                "aut-num: AS64500",
                                "as-name: EXAMPLE-AS",
                                "org-name: Example Networks",
                                "descr: Example backbone",
                                "country: US",
                                "remarks: Transit only",
                            ]
                        ),
                    ),
                ],
            ),
        ):
            result = perform_asn_lookup(ASNLookupRequest(query="64500"))

        self.assertEqual(result.normalized_asn, "AS64500")
        self.assertEqual(result.as_name, "EXAMPLE-AS")
        self.assertEqual(result.organization, "Example Networks")
        self.assertEqual(result.description, "Example backbone")
        self.assertEqual(result.country, "US")
        self.assertEqual(result.registry_server, "whois.example.net")
        self.assertEqual(result.remarks, ["Transit only"])

    def test_expand_ip_scan_targets_resolves_cidr_and_hostname(self) -> None:
        self.assertEqual(
            expand_ip_scan_targets("192.0.2.0/30"),
            ["192.0.2.1", "192.0.2.2"],
        )
        with patch(
            "snakesh.services.network_tools.socket.getaddrinfo",
            return_value=[
                (network_tools.socket.AF_INET, network_tools.socket.SOCK_STREAM, 6, "", ("198.51.100.10", 0)),
                (network_tools.socket.AF_INET, network_tools.socket.SOCK_STREAM, 6, "", ("198.51.100.11", 0)),
            ],
        ):
            self.assertEqual(
                expand_ip_scan_targets("scanner.example"),
                ["198.51.100.10", "198.51.100.11"],
            )

    def test_parse_ip_scan_ports_accepts_ranges_and_rejects_invalid_input(self) -> None:
        self.assertEqual(parse_ip_scan_ports("22,80,443,8000-8002"), [22, 80, 443, 8000, 8001, 8002])
        with self.assertRaises(ValueError):
            parse_ip_scan_ports("22-20")

    def test_perform_ip_scan_reports_open_ports(self) -> None:
        request = IPScanRequest(
            target="192.0.2.10",
            port_preset=network_tools.IP_SCAN_PRESET_CUSTOM,
            custom_ports="22,80,443",
            concurrency=1,
            resolve_names=False,
        )
        with (
            patch(
                "snakesh.services.network_tools._probe_ip_scan_port",
                side_effect=lambda target, port, timeout_ms: port in {22, 443},
            ),
            patch(
                "snakesh.services.network_tools._service_name_for_port",
                side_effect=lambda port: {22: "ssh", 443: "https"}.get(port, ""),
            ),
        ):
            result = perform_ip_scan(request)

        self.assertFalse(result.canceled)
        self.assertEqual(result.total_hosts, 1)
        self.assertEqual(result.scanned_hosts, 1)
        self.assertEqual(result.hosts[0].status, "Open Ports Found")
        self.assertEqual(result.hosts[0].open_port_count, 2)
        self.assertEqual([(entry.port, entry.service_name) for entry in result.open_ports], [(22, "ssh"), (443, "https")])

    def test_perform_ip_scan_honors_cancellation(self) -> None:
        request = IPScanRequest(
            target="192.0.2.10",
            port_preset=network_tools.IP_SCAN_PRESET_CUSTOM,
            custom_ports="22,23,24",
            concurrency=1,
            resolve_names=False,
        )
        cancel_event = threading.Event()

        def _cancel_after_first(progress) -> None:  # noqa: ANN001
            if progress.completed_probes >= 1:
                cancel_event.set()

        with patch("snakesh.services.network_tools._probe_ip_scan_port", return_value=False):
            result = perform_ip_scan(request, progress_callback=_cancel_after_first, cancel_event=cancel_event)

        self.assertTrue(result.canceled)
        self.assertLess(result.scanned_probes, result.total_probes)

    def test_dns_nslookup_fallback_parses_success_output(self) -> None:
        with patch("snakesh.services.network_tools.shutil.which", return_value="/usr/bin/nslookup"):
            with patch(
                "snakesh.services.network_tools.subprocess.run",
                return_value=network_tools.subprocess.CompletedProcess(
                    args=["nslookup"],
                    returncode=0,
                    stdout="Name:\texample.com\nAddress:\t93.184.216.34\n",
                    stderr="",
                ),
            ):
                result = network_tools._perform_dns_lookup_with_nslookup(
                    DNSLookupRequest(query="example.com", record_type="A"),
                    import_error=Exception("missing module"),
                )
        self.assertEqual(result.status, "NOERROR")
        self.assertTrue(result.answer_lines)


if __name__ == "__main__":
    unittest.main()
