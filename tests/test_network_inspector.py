from __future__ import annotations

import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import snakesh.services.network_inspector as network_inspector
from snakesh.services.network_inspector import (
    DNSConfig,
    ListeningPortEntry,
    ListeningPortsResult,
    NetworkInspectorSnapshot,
    PrivilegedPortsHelperSession,
    parse_linux_ip_neigh,
    parse_linux_ip_route,
    parse_linux_resolv_conf_dns,
    parse_macos_arp_table,
    parse_macos_lsof_listening_ports,
    parse_macos_netstat_routes,
    parse_macos_scutil_dns,
    parse_windows_arp_table,
    parse_windows_ipconfig_dns,
    parse_windows_route_print,
)
from snakesh.services.oui_service import OUILookupService, OUIRecord


class NetworkInspectorParserTests(unittest.TestCase):
    def test_parse_linux_ip_route(self) -> None:
        output = (
            "default via 192.0.2.1 dev eno1 proto dhcp metric 100\n"
            "192.0.2.0/24 dev eno1 proto kernel scope link src 192.0.2.22 metric 100\n"
        )

        routes = parse_linux_ip_route(output, family="IPv4")

        self.assertEqual(routes[0].destination, "default")
        self.assertEqual(routes[0].gateway, "192.0.2.1")
        self.assertEqual(routes[1].source, "192.0.2.22")

    def test_parse_windows_route_print(self) -> None:
        output = """
IPv4 Route Table
===========================================================================
Active Routes:
Network Destination        Netmask          Gateway       Interface  Metric
          0.0.0.0          0.0.0.0     192.0.2.1    192.0.2.22     25

IPv6 Route Table
===========================================================================
Active Routes:
 If Metric Network Destination      Gateway
 12    25 ::/0                      fe80::1
"""

        routes = parse_windows_route_print(output)

        self.assertEqual(routes[0].destination, "0.0.0.0/0")
        self.assertEqual(routes[1].family, "IPv6")
        self.assertEqual(routes[1].gateway, "fe80::1")

    def test_parse_macos_netstat_routes(self) -> None:
        output = """
Internet:
Destination        Gateway            Flags        Netif Expire
default            192.0.2.1        UGSc           en0
192.0.2/24       link#4             UCS            en0

Internet6:
Destination                             Gateway                         Flags         Netif Expire
default                                 fe80::1%en0                    UGcIg          en0
"""

        routes = parse_macos_netstat_routes(output)

        self.assertEqual(routes[0].family, "IPv4")
        self.assertEqual(routes[2].family, "IPv6")
        self.assertEqual(routes[2].gateway, "fe80::1%en0")

    def test_parse_arp_tables(self) -> None:
        linux_entries = parse_linux_ip_neigh(
            "192.0.2.1 dev eno1 lladdr aa:bb:cc:dd:ee:ff REACHABLE\n"
        )
        windows_entries = parse_windows_arp_table(
            "Interface: 192.0.2.22 --- 0x6\n"
            "  Internet Address      Physical Address      Type\n"
            "  192.0.2.1           aa-bb-cc-dd-ee-ff     dynamic\n"
        )
        macos_entries = parse_macos_arp_table(
            "? (192.0.2.1) at aa:bb:cc:dd:ee:ff on en0 ifscope [ethernet]\n"
        )

        self.assertEqual(linux_entries[0].mac_address, "aa:bb:cc:dd:ee:ff")
        self.assertEqual(windows_entries[0].interface, "192.0.2.22")
        self.assertEqual(macos_entries[0].interface, "en0")

    def test_parse_dns_sources(self) -> None:
        linux_dns = parse_linux_resolv_conf_dns(
            "nameserver 1.1.1.1\nsearch lab.example corp.example\n",
            host_name="host-a",
            fqdn="host-a.lab.example",
        )
        windows_dns = parse_windows_ipconfig_dns(
            "Host Name . . . . . . . . . . . . : WINBOX\n"
            "Primary Dns Suffix  . . . . . . . : lab.example\n"
            "DNS Servers . . . . . . . . . . . : 8.8.8.8\n"
            "                                       8.8.4.4\n"
            "                                       NetBIOS over Tcpip. . . . . . . . : Enabled\n"
            "DNS Suffix Search List. . . . . . : lab.example, corp.example\n",
            host_name="WINBOX",
            fqdn="WINBOX",
        )
        macos_dns = parse_macos_scutil_dns(
            "resolver #1\n  search domain[0] : lab.example\n  nameserver[0] : 9.9.9.9\n",
            host_name="mac-mini",
            fqdn="mac-mini.local",
        )

        self.assertEqual(linux_dns.search_domains, ["lab.example", "corp.example"])
        self.assertEqual(windows_dns.nameservers, ["8.8.8.8", "8.8.4.4"])
        self.assertEqual(macos_dns.search_domains, ["lab.example"])

    def test_parse_macos_lsof_listening_ports(self) -> None:
        output = (
            "p123\n"
            "cpython\n"
            "PTCP\n"
            "n127.0.0.1:8443\n"
            "TST=LISTEN\n"
            "p124\n"
            "cmDNSResponder\n"
            "PUDP\n"
            "n*:5353\n"
            "p125\n"
            "cclient\n"
            "PTCP\n"
            "n127.0.0.1:50000->127.0.0.1:443\n"
            "TST=ESTABLISHED\n"
        )

        entries = parse_macos_lsof_listening_ports(output)

        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].protocol, "TCP")
        self.assertEqual(entries[0].local_address, "127.0.0.1:8443")
        self.assertEqual(entries[0].pid, 123)
        self.assertEqual(entries[0].process_name, "python")
        self.assertEqual(entries[1].protocol, "UDP")
        self.assertEqual(entries[1].local_address, "*:5353")
        self.assertEqual(entries[1].process_name, "mDNSResponder")

    def test_collect_listening_ports_darwin_falls_back_to_lsof_when_psutil_denied(self) -> None:
        fallback_entries = [
            ListeningPortEntry(
                family="IPv4",
                protocol="TCP",
                local_address="127.0.0.1:8443",
                pid=4321,
                process_name="python",
            )
        ]

        with (
            patch("snakesh.services.network_inspector._collect_listening_ports_psutil", side_effect=RuntimeError("denied")),
            patch("snakesh.services.network_inspector.collect_macos_lsof_listening_ports", return_value=fallback_entries),
        ):
            entries = network_inspector.collect_listening_ports(platform_name="darwin")

        self.assertEqual(entries, fallback_entries)

    def test_collect_listening_ports_darwin_falls_back_to_lsof_when_psutil_empty(self) -> None:
        fallback_entries = [
            ListeningPortEntry(
                family="IPv4",
                protocol="UDP",
                local_address="*:5353",
                pid=124,
                process_name="mDNSResponder",
            )
        ]

        with (
            patch("snakesh.services.network_inspector._collect_listening_ports_psutil", return_value=[]),
            patch("snakesh.services.network_inspector.collect_macos_lsof_listening_ports", return_value=fallback_entries),
        ):
            entries = network_inspector.collect_listening_ports(platform_name="darwin")

        self.assertEqual(entries, fallback_entries)

    def test_run_capture_hides_windows_console(self) -> None:
        completed = unittest.mock.Mock(returncode=0, stdout="ok\n", stderr="")
        with (
            patch("snakesh.services.network_inspector.subprocess.run", return_value=completed) as mock_run,
            patch("snakesh.services.network_inspector._platform_name", return_value="windows"),
            patch("snakesh.services.network_inspector.subprocess.CREATE_NO_WINDOW", 0x08000000, create=True),
        ):
            output = network_inspector._run_capture(["ipconfig", "/all"])

        self.assertEqual(output, "ok")
        self.assertEqual(mock_run.call_args.kwargs["creationflags"], 0x08000000)

    def test_collect_arp_entries_applies_oui_lookup(self) -> None:
        service = OUILookupService([OUIRecord(prefix="AABBCC", bits=24, vendor="Vendor Example")])
        with patch(
            "snakesh.services.network_inspector._run_capture",
            return_value="192.0.2.1 dev eno1 lladdr aa:bb:cc:dd:ee:ff REACHABLE\n",
        ):
            from snakesh.services.network_inspector import collect_arp_entries

            entries = collect_arp_entries(service, platform_name="linux")

        self.assertEqual(entries[0].vendor, "Vendor Example")

    def test_privileged_ports_helper_session_request_response_flow_returns_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = PrivilegedPortsHelperSession(session_dir=Path(tmp) / "session", parent_pid=os.getpid())
            helper_threads: list[threading.Thread] = []

            def fake_launch(_session_dir: str | Path, *, platform_name: str | None = None) -> list[str]:
                _ = platform_name
                thread = threading.Thread(
                    target=network_inspector.run_network_inspector_ports_helper,
                    args=(_session_dir,),
                    daemon=True,
                )
                thread.start()
                helper_threads.append(thread)
                return ["helper"]

            with (
                patch(
                    "snakesh.services.network_inspector.launch_network_inspector_ports_helper_elevated",
                    side_effect=fake_launch,
                ),
                patch(
                    "snakesh.services.network_inspector.collect_listening_ports",
                    return_value=[
                        ListeningPortEntry(
                            family="IPv4",
                            protocol="TCP",
                            local_address="127.0.0.1:8443",
                            pid=4321,
                            process_name="python",
                        )
                    ],
                ),
                patch("snakesh.services.network_inspector._is_effectively_elevated", return_value=False),
            ):
                result = session.collect_ports(allow_start=True)

            try:
                self.assertEqual(result.warning, "")
                self.assertEqual(len(result.entries), 1)
                self.assertEqual(result.entries[0].local_address, "127.0.0.1:8443")
                self.assertTrue(session.is_ready)
            finally:
                session.close()
                for thread in helper_threads:
                    thread.join(timeout=2)
                    self.assertFalse(thread.is_alive())

    def test_network_inspector_ports_helper_exits_when_stop_requested_or_parent_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = PrivilegedPortsHelperSession(session_dir=Path(tmp) / "session-stop", parent_pid=os.getpid())
            stop_thread = threading.Thread(
                target=network_inspector.run_network_inspector_ports_helper,
                args=(session.session_dir,),
                daemon=True,
            )
            stop_thread.start()
            self.assertTrue(session.wait_until_ready(timeout=2))
            session.close()
            stop_thread.join(timeout=2)
            self.assertFalse(stop_thread.is_alive())

            orphaned = PrivilegedPortsHelperSession(
                session_dir=Path(tmp) / "session-orphan",
                parent_pid=os.getpid() + 1_000_000,
            )
            orphan_thread = threading.Thread(
                target=network_inspector.run_network_inspector_ports_helper,
                args=(orphaned.session_dir,),
                daemon=True,
            )
            orphan_thread.start()
            orphan_thread.join(timeout=2)
            self.assertFalse(orphan_thread.is_alive())
            orphaned.close()

    def test_collect_network_snapshot_surfaces_privileged_ports_warning(self) -> None:
        class _FakeSession:
            def collect_ports(self, *, allow_start: bool) -> ListeningPortsResult:
                self.allow_start = allow_start
                return ListeningPortsResult(
                    entries=[
                        ListeningPortEntry(
                            family="IPv4",
                            protocol="TCP",
                            local_address="127.0.0.1:8080",
                            pid=1234,
                            process_name="python",
                        )
                    ],
                    warning="Privileged ports helper timed out. Showing standard visibility instead.",
                )

        fake_session = _FakeSession()
        with (
            patch("snakesh.services.network_inspector.collect_interface_info", return_value=[]),
            patch("snakesh.services.network_inspector.collect_routes", return_value=[]),
            patch("snakesh.services.network_inspector.collect_arp_entries", return_value=[]),
            patch(
                "snakesh.services.network_inspector.collect_dns_config",
                return_value=DNSConfig(host_name="host", fqdn="host", nameservers=[], search_domains=[]),
            ),
        ):
            snapshot = network_inspector.collect_network_snapshot(
                use_privileged_ports=True,
                privileged_ports_session=fake_session,  # type: ignore[arg-type]
                allow_privileged_ports_launch=False,
            )

        self.assertIsInstance(snapshot, NetworkInspectorSnapshot)
        self.assertEqual(snapshot.listening_ports[0].local_address, "127.0.0.1:8080")
        self.assertIn("Ports: Privileged ports helper timed out.", snapshot.errors[0])


if __name__ == "__main__":
    unittest.main()
