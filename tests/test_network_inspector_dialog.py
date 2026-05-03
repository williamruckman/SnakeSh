from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from snakesh.services.network_inspector import (
    ArpEntry,
    DNSConfig,
    InterfaceAddress,
    InterfaceInfo,
    ListeningPortEntry,
    NetworkInspectorSnapshot,
    RouteEntry,
)
from snakesh.ui.network_inspector_dialog import NetworkInspectorDialog


class NetworkInspectorDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls._app = QApplication.instance() or QApplication([])

    def _build_snapshot(self) -> NetworkInspectorSnapshot:
        return NetworkInspectorSnapshot(
            interfaces=[
                InterfaceInfo(
                    name="eth0",
                    is_up=True,
                    mtu=1500,
                    speed_mbps=1000,
                    duplex="Full",
                    mac_address="aa:bb:cc:dd:ee:ff",
                    addresses=[
                        InterfaceAddress(
                            family="IPv4",
                            address="192.0.2.20",
                            netmask="255.255.255.0",
                            broadcast="192.0.2.255",
                        )
                    ],
                )
            ],
            routes=[
                RouteEntry(
                    family="IPv4",
                    destination="default",
                    gateway="192.0.2.1",
                    interface="eth0",
                    metric="100",
                    flags="UG",
                )
            ],
            arp_entries=[
                ArpEntry(
                    ip_address="192.0.2.1",
                    mac_address="aa:bb:cc:dd:ee:ff",
                    interface="eth0",
                    state="REACHABLE",
                    vendor="Vendor Example",
                )
            ],
            listening_ports=[
                ListeningPortEntry(
                    family="IPv4",
                    protocol="TCP",
                    local_address="127.0.0.1:8080",
                    pid=1234,
                    process_name="python",
                )
            ],
            dns_config=DNSConfig(
                host_name="host-a",
                fqdn="host-a.lab.example",
                nameservers=["1.1.1.1", "8.8.8.8"],
                search_domains=["lab.example"],
                notes=["Loaded from resolver"],
            ),
        )

    def test_copy_selected_copies_current_tab_rows_with_headers(self) -> None:
        with patch("snakesh.ui.network_inspector_dialog.QTimer.singleShot"):
            dialog = NetworkInspectorDialog()
        try:
            dialog._on_snapshot_ready(self._build_snapshot())
            dialog.tabs.setCurrentWidget(dialog.routing_tree)
            item = dialog.routing_tree.topLevelItem(0)
            item.setSelected(True)
            QApplication.processEvents()

            dialog._copy_selected_rows()

            clipboard_text = QApplication.clipboard().text()
            self.assertIn("Family\tDestination\tGateway\tInterface\tMetric\tFlags", clipboard_text)
            self.assertIn("IPv4\tdefault\t192.0.2.1\teth0\t100\tUG", clipboard_text)
            self.assertEqual(dialog.status_label.text(), "Copied 1 row from Routing.")
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_copy_all_copies_full_current_tab(self) -> None:
        with patch("snakesh.ui.network_inspector_dialog.QTimer.singleShot"):
            dialog = NetworkInspectorDialog()
        try:
            dialog._on_snapshot_ready(self._build_snapshot())
            dialog.tabs.setCurrentWidget(dialog.dns_tree)
            QApplication.processEvents()

            dialog._copy_all_rows()

            clipboard_text = QApplication.clipboard().text()
            self.assertIn("Type\tValue", clipboard_text)
            self.assertIn("Host Name\thost-a", clipboard_text)
            self.assertIn("Nameserver\t1.1.1.1", clipboard_text)
            self.assertIn("Note\tLoaded from resolver", clipboard_text)
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_auto_refresh_timer_enables_disables_and_restarts_with_new_interval(self) -> None:
        with patch("snakesh.ui.network_inspector_dialog.QTimer.singleShot"):
            dialog = NetworkInspectorDialog()
        try:
            self.assertFalse(dialog.auto_refresh_input.isChecked())
            self.assertFalse(dialog._auto_refresh_timer.isActive())
            self.assertFalse(dialog.auto_refresh_seconds_input.isEnabled())

            dialog.auto_refresh_input.setChecked(True)
            QApplication.processEvents()
            self.assertTrue(dialog._auto_refresh_timer.isActive())
            self.assertTrue(dialog.auto_refresh_seconds_input.isEnabled())
            self.assertEqual(dialog._auto_refresh_timer.interval(), 5000)

            dialog.auto_refresh_seconds_input.setValue(9)
            QApplication.processEvents()
            self.assertEqual(dialog._auto_refresh_timer.interval(), 9000)

            dialog.auto_refresh_input.setChecked(False)
            QApplication.processEvents()
            self.assertFalse(dialog._auto_refresh_timer.isActive())
            self.assertFalse(dialog.auto_refresh_seconds_input.isEnabled())
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_refresh_ignores_overlapping_request_when_worker_is_running(self) -> None:
        with patch("snakesh.ui.network_inspector_dialog.QTimer.singleShot"):
            dialog = NetworkInspectorDialog()
        try:
            dialog._thread = object()  # type: ignore[assignment]
            self.assertFalse(dialog._start_refresh(manual=False))
        finally:
            dialog._thread = None
            dialog.deleteLater()
            QApplication.processEvents()

    def test_privileged_ports_refresh_routes_only_ports_collection_through_helper(self) -> None:
        with patch("snakesh.ui.network_inspector_dialog.QTimer.singleShot"):
            dialog = NetworkInspectorDialog()
        try:
            fake_session = object()
            dialog.privileged_ports_input.setChecked(True)
            with (
                patch.object(dialog, "_current_privileged_ports_session", return_value=fake_session),
                patch(
                    "snakesh.ui.network_inspector_dialog.collect_network_snapshot",
                    return_value=self._build_snapshot(),
                ) as mock_collect,
            ):
                dialog._collect_snapshot(manual=False)

            kwargs = mock_collect.call_args.kwargs
            self.assertTrue(kwargs["use_privileged_ports"])
            self.assertIs(kwargs["privileged_ports_session"], fake_session)
            self.assertTrue(kwargs["allow_privileged_ports_launch"])
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_helper_start_failure_blocks_auto_retry_until_manual_refresh(self) -> None:
        class _FakeSession:
            def __init__(self) -> None:
                self.is_ready = False
                self.last_start_failed = False

        with patch("snakesh.ui.network_inspector_dialog.QTimer.singleShot"):
            dialog = NetworkInspectorDialog()
        try:
            dialog.privileged_ports_input.setChecked(True)
            dialog._privileged_ports_session = _FakeSession()  # type: ignore[assignment]
            dialog._last_refresh_used_privileged_ports = True

            dialog._privileged_ports_session.last_start_failed = True  # type: ignore[union-attr]
            dialog._update_privileged_ports_retry_state()
            self.assertTrue(dialog._privileged_ports_auto_retry_blocked)

            with patch(
                "snakesh.ui.network_inspector_dialog.collect_network_snapshot",
                return_value=self._build_snapshot(),
            ) as mock_collect:
                dialog._collect_snapshot(manual=False)
                auto_kwargs = mock_collect.call_args.kwargs
                dialog._collect_snapshot(manual=True)
                manual_kwargs = mock_collect.call_args.kwargs

            self.assertFalse(auto_kwargs["allow_privileged_ports_launch"])
            self.assertTrue(manual_kwargs["allow_privileged_ports_launch"])

            dialog._privileged_ports_session.is_ready = True  # type: ignore[union-attr]
            dialog._privileged_ports_session.last_start_failed = False  # type: ignore[union-attr]
            dialog._update_privileged_ports_retry_state()
            self.assertFalse(dialog._privileged_ports_auto_retry_blocked)
        finally:
            dialog.deleteLater()
            QApplication.processEvents()


if __name__ == "__main__":
    unittest.main()
