from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QTreeWidgetItem

from snakesh.services.mtr_trace import MTRHopSnapshot, MTRProbeSample, MTRTraceSnapshot, mtr_helper_session_paths
from snakesh.services.settings_service import AppSettings
from snakesh.services.network_tools import ASNLookupResult, IPScanHostResult, IPScanPortResult, IPScanProgress, IPScanResult
from snakesh.ui.network_tools_dialog import ASNLookupDialog, IPScanDialog, TracerouteToolDialog


class NetworkToolsDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls._app = QApplication.instance() or QApplication([])

    def _assert_runtime_badge(self, dialog: IPScanDialog, expected_text: str, expected_color: str) -> None:
        self.assertEqual(dialog.runtime_badge.text(), expected_text)
        self.assertIn(expected_color, dialog.runtime_badge.styleSheet())

    @staticmethod
    def _sample_ip_scan_result(*, canceled: bool) -> IPScanResult:
        return IPScanResult(
            target="192.0.2.10",
            hosts=[
                IPScanHostResult(
                    host="192.0.2.10",
                    status="Open Ports Found",
                    resolved_name="server-a",
                    open_port_count=2,
                    elapsed_ms=12.5,
                )
            ],
            open_ports=[
                IPScanPortResult(host="192.0.2.10", resolved_name="server-a", port=22, service_name="ssh"),
                IPScanPortResult(host="192.0.2.10", resolved_name="server-a", port=443, service_name="https"),
            ],
            total_hosts=1,
            scanned_hosts=1,
            total_probes=20,
            scanned_probes=20,
            canceled=canceled,
            elapsed_ms=44.1,
        )

    def test_asn_lookup_dialog_populates_summary_and_raw_output(self) -> None:
        dialog = ASNLookupDialog()
        try:
            dialog._on_lookup_success(
                ASNLookupResult(
                    query="AS15169",
                    normalized_asn="AS15169",
                    as_name="GOOGLE",
                    organization="Google LLC",
                    description="Google global network",
                    country="US",
                    registry_server="whois.arin.net",
                    remarks=["Anycast", "Global backbone"],
                    sections=[("whois.arin.net", "ASNumber: AS15169\nOrgName: Google LLC\n")],
                )
            )

            self.assertEqual(dialog.asn_label.text(), "AS15169")
            self.assertEqual(dialog.as_name_label.text(), "GOOGLE")
            self.assertEqual(dialog.organization_label.text(), "Google LLC")
            self.assertIn("Anycast", dialog.remarks_label.text())
            self.assertIn("whois.arin.net", dialog.output.toPlainText())
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_asn_lookup_runtime_settings_update_output_font_without_restyling_form_fields(self) -> None:
        dialog = ASNLookupDialog()
        try:
            before_query_font = dialog.query_input.font().pointSize()
            before_output_font = dialog.output.font().pointSize()

            settings = AppSettings.defaults()
            settings.terminal_font_pt = max(before_output_font + 4, 12)

            dialog.apply_runtime_settings(settings)
            QApplication.processEvents()

            self.assertEqual(dialog.output.font().pointSize(), settings.terminal_font_pt)
            self.assertEqual(dialog.query_input.font().pointSize(), before_query_font)
            self.assertGreater(dialog.output.font().pointSize(), before_output_font)
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_ip_scan_dialog_running_state_and_result_population(self) -> None:
        dialog = IPScanDialog()
        try:
            dialog._set_running(True)
            self.assertFalse(dialog.scan_btn.isEnabled())
            self.assertTrue(dialog.stop_btn.isEnabled())

            dialog._on_scan_success(
                IPScanResult(
                    target="192.0.2.10",
                    hosts=[
                        IPScanHostResult(
                            host="192.0.2.10",
                            status="Open Ports Found",
                            resolved_name="server-a",
                            open_port_count=2,
                            elapsed_ms=12.5,
                        )
                    ],
                    open_ports=[
                        IPScanPortResult(host="192.0.2.10", resolved_name="server-a", port=22, service_name="ssh"),
                        IPScanPortResult(host="192.0.2.10", resolved_name="server-a", port=443, service_name="https"),
                    ],
                    total_hosts=1,
                    scanned_hosts=1,
                    total_probes=20,
                    scanned_probes=20,
                    canceled=False,
                    elapsed_ms=44.1,
                )
            )

            self.assertEqual(dialog.hosts_tree.topLevelItemCount(), 1)
            self.assertEqual(dialog.open_ports_tree.topLevelItemCount(), 2)
            self.assertIn("Scan complete", dialog.status_label.text())

            dialog._set_running(False)
            self.assertTrue(dialog.scan_btn.isEnabled())
            self.assertFalse(dialog.stop_btn.isEnabled())
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_ip_scan_dialog_runtime_badge_tracks_cancel_lifecycle(self) -> None:
        dialog = IPScanDialog()
        try:
            self._assert_runtime_badge(dialog, "Stopped", "#b91c1c")

            with patch("snakesh.ui.network_tools_dialog.QThread.start", autospec=True):
                dialog._start_scan()

            self._assert_runtime_badge(dialog, "Starting", "#92400e")
            self.assertFalse(dialog.scan_btn.isEnabled())
            self.assertTrue(dialog.stop_btn.isEnabled())

            dialog._on_scan_progress(
                IPScanProgress(
                    total_hosts=1,
                    completed_hosts=0,
                    total_probes=20,
                    completed_probes=1,
                    current_host="192.0.2.10",
                    current_port=22,
                    open_ports_found=1,
                )
            )
            self._assert_runtime_badge(dialog, "Running", "#166534")

            dialog._stop_scan()
            self._assert_runtime_badge(dialog, "Stopping", "#92400e")
            self.assertFalse(dialog.stop_btn.isEnabled())

            dialog._on_scan_progress(
                IPScanProgress(
                    total_hosts=1,
                    completed_hosts=0,
                    total_probes=20,
                    completed_probes=2,
                    current_host="192.0.2.10",
                    current_port=443,
                    open_ports_found=1,
                )
            )
            self._assert_runtime_badge(dialog, "Stopping", "#92400e")

            dialog._on_scan_success(self._sample_ip_scan_result(canceled=True))
            self.assertIn("Scan canceled", dialog.status_label.text())
            self._assert_runtime_badge(dialog, "Stopping", "#92400e")

            dialog._on_scan_finished()
            self._assert_runtime_badge(dialog, "Stopped", "#b91c1c")
            self.assertTrue(dialog.scan_btn.isEnabled())
            self.assertFalse(dialog.stop_btn.isEnabled())
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_ip_scan_dialog_runtime_badge_returns_to_stopped_after_success_and_failure(self) -> None:
        dialog = IPScanDialog()
        try:
            with patch("snakesh.ui.network_tools_dialog.QThread.start", autospec=True):
                dialog._start_scan()
            dialog._on_scan_progress(
                IPScanProgress(
                    total_hosts=1,
                    completed_hosts=0,
                    total_probes=20,
                    completed_probes=1,
                    current_host="192.0.2.10",
                    current_port=22,
                    open_ports_found=1,
                )
            )
            self._assert_runtime_badge(dialog, "Running", "#166534")

            dialog._on_scan_success(self._sample_ip_scan_result(canceled=False))
            self.assertIn("Scan complete", dialog.status_label.text())
            dialog._on_scan_finished()
            self._assert_runtime_badge(dialog, "Stopped", "#b91c1c")

            with patch("snakesh.ui.network_tools_dialog.QThread.start", autospec=True):
                dialog._start_scan()
            self._assert_runtime_badge(dialog, "Starting", "#92400e")

            dialog._on_scan_failure("scan failed")
            self.assertEqual(dialog.status_label.text(), "scan failed")
            dialog._on_scan_finished()
            self._assert_runtime_badge(dialog, "Stopped", "#b91c1c")

            dialog._clear_results()
            self._assert_runtime_badge(dialog, "Stopped", "#b91c1c")
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_ip_scan_dialog_filters_and_host_drilldown(self) -> None:
        dialog = IPScanDialog()
        try:
            dialog._on_scan_success(
                IPScanResult(
                    target="192.0.2.0/30",
                    hosts=[
                        IPScanHostResult(
                            host="192.0.2.10",
                            status="Open Ports Found",
                            resolved_name="server-a",
                            open_port_count=2,
                            elapsed_ms=12.5,
                        ),
                        IPScanHostResult(
                            host="192.0.2.11",
                            status="No Open TCP Ports",
                            resolved_name="server-b",
                            open_port_count=0,
                            elapsed_ms=15.0,
                        ),
                    ],
                    open_ports=[
                        IPScanPortResult(host="192.0.2.10", resolved_name="server-a", port=22, service_name="ssh"),
                        IPScanPortResult(host="192.0.2.10", resolved_name="server-a", port=443, service_name="https"),
                        IPScanPortResult(host="192.0.2.11", resolved_name="server-b", port=8080, service_name="http-alt"),
                    ],
                    total_hosts=2,
                    scanned_hosts=2,
                    total_probes=40,
                    scanned_probes=40,
                    canceled=False,
                    elapsed_ms=44.1,
                )
            )

            dialog.hosts_filter_input.setText("server-a")
            QApplication.processEvents()
            self.assertFalse(dialog.hosts_tree.topLevelItem(0).isHidden())
            self.assertTrue(dialog.hosts_tree.topLevelItem(1).isHidden())

            dialog.hosts_filter_input.clear()
            dialog.open_ports_filter_input.setText("https")
            QApplication.processEvents()
            self.assertTrue(dialog.open_ports_tree.topLevelItem(0).isHidden())
            self.assertFalse(dialog.open_ports_tree.topLevelItem(1).isHidden())
            self.assertTrue(dialog.open_ports_tree.topLevelItem(2).isHidden())

            dialog.open_ports_filter_input.clear()
            dialog._on_hosts_item_clicked(dialog.hosts_tree.topLevelItem(0), 0)
            QApplication.processEvents()
            self.assertIs(dialog.tabs.currentWidget(), dialog.open_ports_page)
            self.assertEqual(dialog._open_ports_host_filter, "192.0.2.10")
            self.assertFalse(dialog.open_ports_tree.topLevelItem(0).isHidden())
            self.assertFalse(dialog.open_ports_tree.topLevelItem(1).isHidden())
            self.assertTrue(dialog.open_ports_tree.topLevelItem(2).isHidden())

            dialog._clear_open_ports_host_filter()
            QApplication.processEvents()
            self.assertEqual(dialog._open_ports_host_filter, "")
            self.assertFalse(dialog.open_ports_tree.topLevelItem(2).isHidden())
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_traceroute_dialog_populates_snapshot_and_copies_report(self) -> None:
        dialog = TracerouteToolDialog()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                dialog._session_dir = Path(tmp)
                dialog._set_running(True)
                paths = mtr_helper_session_paths(tmp)
                paths.ready_path.write_text(json.dumps({"pid": 1}), encoding="utf-8")
                snapshot = MTRTraceSnapshot(
                    state="running",
                    message="Cycle 1: probing hop 1/30.",
                    cycle=1,
                    target="8.8.8.8",
                    protocol="ICMP",
                    ipv6=False,
                    hops=[
                        MTRHopSnapshot(
                            hop=1,
                            host="router-a",
                            address="192.0.2.1",
                            sent=1,
                            received=1,
                            loss_percent=0.0,
                            last_ms=1.2,
                            avg_ms=1.2,
                            best_ms=1.2,
                            worst_ms=1.2,
                            stdev_ms=None,
                            reached_destination=False,
                        )
                    ],
                )
                paths.state_path.write_text(json.dumps(snapshot.to_dict()), encoding="utf-8")

                dialog._poll_helper_state()
                QApplication.processEvents()

                self.assertEqual(dialog.trace_tree.topLevelItemCount(), 1)
                self.assertEqual(dialog.trace_tree.topLevelItem(0).text(1), "router-a")
                self.assertIn("Cycle 1", dialog.status_label.text())

                dialog._copy_report()
                QApplication.processEvents()
                self.assertIn("Traceroute report for 8.8.8.8", QApplication.clipboard().text())
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_traceroute_dialog_defaults_to_hop_ascending_sort(self) -> None:
        dialog = TracerouteToolDialog()
        try:
            self.assertEqual(dialog.trace_tree.header().sortIndicatorSection(), 0)
            self.assertEqual(dialog.trace_tree.header().sortIndicatorOrder(), Qt.AscendingOrder)

            with tempfile.TemporaryDirectory() as tmp:
                dialog._session_dir = Path(tmp)
                dialog._set_running(True)
                paths = mtr_helper_session_paths(tmp)
                paths.ready_path.write_text(json.dumps({"pid": 1}), encoding="utf-8")
                snapshot = MTRTraceSnapshot(
                    state="running",
                    message="Cycle 1: probing.",
                    cycle=1,
                    target="8.8.8.8",
                    protocol="ICMP",
                    ipv6=False,
                    hops=[
                        MTRHopSnapshot(
                            hop=1,
                            host="hop-1",
                            address="192.0.2.1",
                            sent=1,
                            received=1,
                            loss_percent=0.0,
                            last_ms=1.0,
                            avg_ms=1.0,
                            best_ms=1.0,
                            worst_ms=1.0,
                            stdev_ms=None,
                            reached_destination=False,
                        ),
                        MTRHopSnapshot(
                            hop=3,
                            host="hop-3",
                            address="192.0.2.3",
                            sent=1,
                            received=1,
                            loss_percent=0.0,
                            last_ms=3.0,
                            avg_ms=3.0,
                            best_ms=3.0,
                            worst_ms=3.0,
                            stdev_ms=None,
                            reached_destination=False,
                        ),
                        MTRHopSnapshot(
                            hop=2,
                            host="hop-2",
                            address="192.0.2.2",
                            sent=1,
                            received=1,
                            loss_percent=0.0,
                            last_ms=2.0,
                            avg_ms=2.0,
                            best_ms=2.0,
                            worst_ms=2.0,
                            stdev_ms=None,
                            reached_destination=False,
                        ),
                    ],
                )
                paths.state_path.write_text(json.dumps(snapshot.to_dict()), encoding="utf-8")

                dialog._poll_helper_state()
                QApplication.processEvents()

                self.assertEqual(dialog.trace_tree.topLevelItem(0).text(0), "1")
                self.assertEqual(dialog.trace_tree.topLevelItem(1).text(0), "2")
                self.assertEqual(dialog.trace_tree.topLevelItem(2).text(0), "3")
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_traceroute_dialog_cycles_uses_presets_and_custom_input(self) -> None:
        dialog = TracerouteToolDialog()
        try:
            self.assertEqual(dialog.protocol_input.currentText(), "Auto")
            self.assertEqual(dialog.cycles_choice_input.currentText(), "Run until stopped")
            self.assertTrue(dialog.cycles_custom_input.isHidden())
            self.assertEqual(dialog._selected_cycles_value(), 0)
            self.assertEqual(dialog.fast_mode_check.text(), "Fast native probing (requires escalation)")

            dialog.cycles_choice_input.setCurrentText("5")
            QApplication.processEvents()
            self.assertEqual(dialog._selected_cycles_value(), 5)
            self.assertTrue(dialog.cycles_custom_input.isHidden())

            dialog.cycles_choice_input.setCurrentText("Custom")
            dialog.cycles_custom_input.setValue(42)
            QApplication.processEvents()
            self.assertFalse(dialog.cycles_custom_input.isHidden())
            self.assertEqual(dialog._selected_cycles_value(), 42)
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_traceroute_dialog_disables_fast_mode_on_windows(self) -> None:
        with patch("snakesh.ui.network_tools_dialog.supports_mtr_fast_mode", return_value=False):
            dialog = TracerouteToolDialog()
        try:
            self.assertFalse(dialog.fast_mode_check.isEnabled())
            self.assertEqual(dialog.fast_mode_check.text(), "Fast native probing (not available on Windows)")
            dialog.fast_mode_check.setChecked(True)
            self.assertFalse(dialog._build_request().fast_mode)
            dialog._set_running(False)
            self.assertFalse(dialog.fast_mode_check.isEnabled())
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_traceroute_dialog_keeps_last_and_avg_visible_with_tooltips(self) -> None:
        dialog = TracerouteToolDialog()
        try:
            dialog.show()
            QApplication.processEvents()
            long_host = "very-long-router-name-for-visibility-check.example.net"
            long_address = "2001:db8:ffff:eeee:dddd:cccc:bbbb:aaaa"
            dialog._populate_snapshot(
                MTRTraceSnapshot(
                    state="running",
                    message="Cycle 1",
                    cycle=1,
                    target="8.8.8.8",
                    protocol="ICMP",
                    ipv6=False,
                    hops=[
                        MTRHopSnapshot(
                            hop=1,
                            host=long_host,
                            address=long_address,
                            sent=2,
                            received=2,
                            loss_percent=0.0,
                            last_ms=4.2,
                            avg_ms=3.7,
                            best_ms=3.2,
                            worst_ms=4.2,
                            stdev_ms=0.5,
                            reached_destination=False,
                        )
                    ],
                )
            )
            QApplication.processEvents()

            header = dialog.trace_tree.header()
            viewport_width = dialog.trace_tree.viewport().width()
            self.assertLess(header.sectionViewportPosition(6), viewport_width)
            self.assertLess(header.sectionViewportPosition(7), viewport_width)
            self.assertEqual(dialog.trace_tree.columnWidth(1), 200)
            self.assertEqual(dialog.trace_tree.columnWidth(2), 200)
            item = dialog.trace_tree.topLevelItem(0)
            self.assertEqual(item.toolTip(1), long_host)
            self.assertEqual(item.toolTip(2), long_address)
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_traceroute_dialog_column_resize_persists_across_snapshot_refreshes(self) -> None:
        dialog = TracerouteToolDialog()
        try:
            snapshot = MTRTraceSnapshot(
                state="running",
                message="Cycle 1",
                cycle=1,
                target="8.8.8.8",
                protocol="ICMP",
                ipv6=False,
                hops=[
                    MTRHopSnapshot(
                        hop=1,
                        host="router-a",
                        address="192.0.2.1",
                        sent=1,
                        received=1,
                        loss_percent=0.0,
                        last_ms=1.5,
                        avg_ms=1.5,
                        best_ms=1.5,
                        worst_ms=1.5,
                        stdev_ms=None,
                        reached_destination=False,
                    )
                ],
            )

            dialog.trace_tree.setColumnWidth(1, 333)
            dialog._populate_snapshot(snapshot)
            QApplication.processEvents()
            self.assertEqual(dialog.trace_tree.columnWidth(1), 333)

            dialog._populate_snapshot(snapshot)
            QApplication.processEvents()
            self.assertEqual(dialog.trace_tree.columnWidth(1), 333)
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_traceroute_dialog_copy_helpers_copy_cell_row_selected_rows_and_column(self) -> None:
        dialog = TracerouteToolDialog()
        try:
            dialog._populate_snapshot(
                MTRTraceSnapshot(
                    state="running",
                    message="Cycle 1",
                    cycle=1,
                    target="8.8.8.8",
                    protocol="ICMP",
                    ipv6=False,
                    hops=[
                        MTRHopSnapshot(
                            hop=1,
                            host="hop-1",
                            address="192.0.2.1",
                            sent=1,
                            received=1,
                            loss_percent=0.0,
                            last_ms=1.0,
                            avg_ms=1.0,
                            best_ms=1.0,
                            worst_ms=1.0,
                            stdev_ms=None,
                            reached_destination=False,
                        ),
                        MTRHopSnapshot(
                            hop=2,
                            host="hop-2",
                            address="192.0.2.2",
                            sent=2,
                            received=1,
                            loss_percent=50.0,
                            last_ms=2.0,
                            avg_ms=2.0,
                            best_ms=2.0,
                            worst_ms=2.0,
                            stdev_ms=None,
                            reached_destination=False,
                        ),
                    ],
                )
            )
            item_one = dialog.trace_tree.topLevelItem(0)
            item_two = dialog.trace_tree.topLevelItem(1)

            dialog._copy_trace_cell(item_one, 1)
            self.assertEqual(QApplication.clipboard().text(), "hop-1")

            dialog._copy_trace_row(item_one)
            self.assertEqual(
                QApplication.clipboard().text(),
                "Hop\tHost\tAddress\tLoss%\tSent\tRecv\tLast\tAvg\tBest\tWorst\tStDev\n"
                "1\thop-1\t192.0.2.1\t0.0\t1\t1\t1.0\t1.0\t1.0\t1.0\t",
            )

            item_one.setSelected(True)
            item_two.setSelected(True)
            QApplication.processEvents()
            dialog._copy_trace_selected_rows()
            self.assertEqual(
                QApplication.clipboard().text(),
                "Hop\tHost\tAddress\tLoss%\tSent\tRecv\tLast\tAvg\tBest\tWorst\tStDev\n"
                "1\thop-1\t192.0.2.1\t0.0\t1\t1\t1.0\t1.0\t1.0\t1.0\t\n"
                "2\thop-2\t192.0.2.2\t50.0\t2\t1\t2.0\t2.0\t2.0\t2.0\t",
            )

            dialog._copy_trace_column(1)
            self.assertEqual(QApplication.clipboard().text(), "Host\nhop-1\nhop-2")
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_traceroute_dialog_context_menu_actions_enable_for_valid_targets(self) -> None:
        dialog = TracerouteToolDialog()
        try:
            menu, actions = dialog._build_trace_tree_context_menu(None, -1)
            self.assertFalse(actions["copy_cell"].isEnabled())
            self.assertFalse(actions["copy_row"].isEnabled())
            self.assertFalse(actions["copy_selected_rows"].isEnabled())
            self.assertFalse(actions["copy_column"].isEnabled())
            menu.deleteLater()

            dialog._populate_snapshot(
                MTRTraceSnapshot(
                    state="running",
                    message="Cycle 1",
                    cycle=1,
                    target="8.8.8.8",
                    protocol="ICMP",
                    ipv6=False,
                    hops=[
                        MTRHopSnapshot(
                            hop=1,
                            host="hop-1",
                            address="192.0.2.1",
                            sent=1,
                            received=1,
                            loss_percent=0.0,
                            last_ms=1.0,
                            avg_ms=1.0,
                            best_ms=1.0,
                            worst_ms=1.0,
                            stdev_ms=None,
                            reached_destination=False,
                        )
                    ],
                )
            )
            item = dialog.trace_tree.topLevelItem(0)
            menu, actions = dialog._build_trace_tree_context_menu(item, 1)
            self.assertTrue(actions["copy_cell"].isEnabled())
            self.assertTrue(actions["copy_row"].isEnabled())
            self.assertFalse(actions["copy_selected_rows"].isEnabled())
            self.assertTrue(actions["copy_column"].isEnabled())
            menu.deleteLater()

            item.setSelected(True)
            QApplication.processEvents()
            menu, actions = dialog._build_trace_tree_context_menu(item, 1)
            self.assertTrue(actions["copy_selected_rows"].isEnabled())
            menu.deleteLater()

            menu, actions = dialog._build_trace_header_context_menu(-1)
            self.assertFalse(actions["copy_column"].isEnabled())
            menu.deleteLater()

            menu, actions = dialog._build_trace_header_context_menu(1)
            self.assertTrue(actions["copy_column"].isEnabled())
            menu.deleteLater()
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_traceroute_dialog_filters_probe_samples_beyond_earliest_destination_hop(self) -> None:
        dialog = TracerouteToolDialog()
        try:
            snapshot = MTRTraceSnapshot(
                state="completed",
                message="Trace complete.",
                cycle=1,
                target="1.1.1.1",
                protocol="UDP",
                ipv6=False,
                hops=[
                    MTRHopSnapshot(
                        hop=1,
                        host="hop-1",
                        address="192.0.2.1",
                        sent=1,
                        received=1,
                        loss_percent=0.0,
                        last_ms=1.0,
                        avg_ms=1.0,
                        best_ms=1.0,
                        worst_ms=1.0,
                        stdev_ms=None,
                        reached_destination=False,
                    ),
                    MTRHopSnapshot(
                        hop=10,
                        host="one.one.one.one",
                        address="1.1.1.1",
                        sent=1,
                        received=1,
                        loss_percent=0.0,
                        last_ms=10.0,
                        avg_ms=10.0,
                        best_ms=10.0,
                        worst_ms=10.0,
                        stdev_ms=None,
                        reached_destination=True,
                    ),
                ],
            )
            samples = [
                MTRProbeSample(
                    sample_index=1,
                    timestamp_ms=100,
                    cycle=1,
                    hop=10,
                    host="one.one.one.one",
                    address="1.1.1.1",
                    success=True,
                    timeout=False,
                    rtt_ms=10.0,
                    reached_destination=True,
                ),
                MTRProbeSample(
                    sample_index=2,
                    timestamp_ms=101,
                    cycle=1,
                    hop=18,
                    host="one.one.one.one",
                    address="1.1.1.1",
                    success=True,
                    timeout=False,
                    rtt_ms=18.0,
                    reached_destination=True,
                ),
            ]

            filtered = dialog._filter_probe_samples_for_snapshot(snapshot, samples)

            self.assertEqual([sample.hop for sample in filtered], [10])
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_traceroute_dialog_enables_graph_button_and_reuses_graph_dialog(self) -> None:
        dialog = TracerouteToolDialog()
        try:
            sample = MTRProbeSample(
                sample_index=1,
                timestamp_ms=123,
                cycle=1,
                hop=1,
                host="router-a",
                address="192.0.2.1",
                success=True,
                timeout=False,
                rtt_ms=1.5,
                reached_destination=False,
            )
            snapshot = MTRTraceSnapshot(
                state="running",
                message="Cycle 1",
                cycle=1,
                target="8.8.8.8",
                protocol="ICMP",
                ipv6=False,
                hops=[
                    MTRHopSnapshot(
                        hop=1,
                        host="router-a",
                        address="192.0.2.1",
                        sent=1,
                        received=1,
                        loss_percent=0.0,
                        last_ms=1.5,
                        avg_ms=1.5,
                        best_ms=1.5,
                        worst_ms=1.5,
                        stdev_ms=None,
                        reached_destination=False,
                    )
                ],
            )
            with tempfile.TemporaryDirectory() as tmp:
                dialog._session_dir = Path(tmp)
                dialog._set_running(True)
                paths = mtr_helper_session_paths(tmp)
                paths.root.mkdir(parents=True, exist_ok=True)
                paths.ready_path.write_text(json.dumps({"pid": 1}), encoding="utf-8")
                paths.state_path.write_text(json.dumps(snapshot.to_dict()), encoding="utf-8")
                paths.samples_path.write_text(json.dumps(sample.to_dict()) + "\n", encoding="utf-8")

                dialog._poll_helper_state()
                QApplication.processEvents()

                self.assertTrue(dialog.graph_btn.isEnabled())
                dialog._show_graph_dialog()
                QApplication.processEvents()
                self.assertIsNotNone(dialog._graph_dialog)
                graph_dialog = dialog._graph_dialog
                assert graph_dialog is not None
                self.assertEqual(graph_dialog.current_sample_count(), 1)

                dialog._show_graph_dialog()
                QApplication.processEvents()
                self.assertIs(dialog._graph_dialog, graph_dialog)

                dialog._set_running(False)
                dialog._reset_trace()
                QApplication.processEvents()
                self.assertFalse(dialog.graph_btn.isEnabled())
                self.assertEqual(graph_dialog.current_sample_count(), 0)
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_traceroute_dialog_reset_clears_results(self) -> None:
        dialog = TracerouteToolDialog()
        try:
            dialog.trace_tree.addTopLevelItem(QTreeWidgetItem(["1"]))
            dialog._last_snapshot = MTRTraceSnapshot(
                state="completed",
                message="done",
                cycle=1,
                target="8.8.8.8",
                protocol="ICMP",
                ipv6=False,
                hops=[],
            )
            dialog.copy_report_btn.setEnabled(True)

            dialog._reset_trace()

            self.assertEqual(dialog.trace_tree.topLevelItemCount(), 0)
            self.assertIsNone(dialog._last_snapshot)
            self.assertEqual(dialog.status_label.text(), "Ready.")
            self.assertFalse(dialog.copy_report_btn.isEnabled())
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_traceroute_dialog_start_reports_launch_errors(self) -> None:
        dialog = TracerouteToolDialog()
        try:
            with patch("snakesh.ui.network_tools_dialog.launch_mtr_helper", side_effect=ValueError("launch failed")):
                dialog._start_trace()
            self.assertEqual(dialog.status_label.text(), "launch failed")
            self.assertEqual(dialog.trace_tree.topLevelItemCount(), 0)
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_traceroute_dialog_start_uses_elevated_helper_for_fast_mode(self) -> None:
        dialog = TracerouteToolDialog()
        try:
            dialog.fast_mode_check.setChecked(True)
            with (
                patch.object(dialog, "_poll_helper_state"),
                patch.object(dialog._poll_timer, "start"),
                patch("snakesh.ui.network_tools_dialog.needs_mtr_helper_elevation", return_value=True),
                patch("snakesh.ui.network_tools_dialog.launch_mtr_helper_elevated") as mock_elevated,
                patch("snakesh.ui.network_tools_dialog.launch_mtr_helper") as mock_plain,
            ):
                dialog._start_trace()
            mock_elevated.assert_called_once()
            mock_plain.assert_not_called()
        finally:
            dialog._clear_trace_data()
            dialog.deleteLater()
            QApplication.processEvents()

    def test_traceroute_dialog_reuses_ready_elevated_helper_without_reprompting(self) -> None:
        dialog = TracerouteToolDialog()
        try:
            dialog.fast_mode_check.setChecked(True)
            with (
                patch.object(dialog, "_poll_helper_state"),
                patch.object(dialog._poll_timer, "start"),
                patch("snakesh.ui.network_tools_dialog.needs_mtr_helper_elevation", return_value=True),
                patch("snakesh.ui.network_tools_dialog.launch_mtr_helper_elevated") as mock_elevated,
            ):
                dialog._start_trace()
                self.assertIsNotNone(dialog._session_dir)
                assert dialog._session_dir is not None
                paths = mtr_helper_session_paths(dialog._session_dir)
                paths.ready_path.write_text(json.dumps({"pid": 1}), encoding="utf-8")

                dialog._set_running(False)
                dialog._start_trace()

            mock_elevated.assert_called_once()
        finally:
            dialog._clear_trace_data()
            dialog.deleteLater()
            QApplication.processEvents()

    def test_traceroute_dialog_waits_for_helper_startup_before_declaring_failure(self) -> None:
        dialog = TracerouteToolDialog()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                dialog._session_dir = Path(tmp)
                dialog._set_running(True)
                dialog._helper_launch_started_at = time.monotonic()

                dialog._poll_helper_state()

                self.assertTrue(dialog._running)
                self.assertNotEqual(dialog.status_label.text(), "Traceroute helper exited without reporting state.")
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_traceroute_dialog_stops_running_when_persistent_helper_reports_final_snapshot(self) -> None:
        dialog = TracerouteToolDialog()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                dialog._session_dir = Path(tmp)
                dialog._set_running(True)
                paths = mtr_helper_session_paths(tmp)
                paths.root.mkdir(parents=True, exist_ok=True)
                paths.ready_path.write_text(json.dumps({"pid": 1}), encoding="utf-8")
                snapshot = MTRTraceSnapshot(
                    state="completed",
                    message="Trace complete after 1 cycle(s).",
                    cycle=1,
                    target="8.8.8.8",
                    protocol="ICMP",
                    ipv6=False,
                    hops=[
                        MTRHopSnapshot(
                            hop=1,
                            host="router-a",
                            address="192.0.2.1",
                            sent=1,
                            received=1,
                            loss_percent=0.0,
                            last_ms=1.0,
                            avg_ms=1.0,
                            best_ms=1.0,
                            worst_ms=1.0,
                            stdev_ms=None,
                            reached_destination=False,
                        )
                    ],
                )
                paths.state_path.write_text(json.dumps(snapshot.to_dict()), encoding="utf-8")

                dialog._poll_helper_state()

                self.assertFalse(dialog._running)
                self.assertEqual(dialog.status_label.text(), "Trace complete after 1 cycle(s).")
                self.assertTrue(paths.ready_path.exists())
        finally:
            dialog.deleteLater()
            QApplication.processEvents()


if __name__ == "__main__":
    unittest.main()
