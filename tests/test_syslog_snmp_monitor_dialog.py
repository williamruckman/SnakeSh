from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from snakesh.services.network_inspector import InterfaceAddress, InterfaceInfo
from snakesh.services.settings_service import AppSettings
from snakesh.services.syslog_snmp_monitor import (
    MonitorRetentionPolicy,
    SyslogSnmpMonitorStatus,
    archive_monitor_events,
    insert_monitor_event,
)
from snakesh.ui.bind_host_selector import CUSTOM_BIND_HOST_VALUE
from snakesh.ui.syslog_snmp_monitor_dialog import SyslogSnmpMonitorDialog


class SyslogSnmpMonitorDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls._app = QApplication.instance() or QApplication([])

    @staticmethod
    def _interface_inventory() -> list[InterfaceInfo]:
        return [
            InterfaceInfo(
                name="eth0",
                is_up=True,
                mtu=1500,
                speed_mbps=1000,
                duplex="Full",
                mac_address="",
                addresses=[
                    InterfaceAddress(family="IPv4", address="192.0.2.22"),
                    InterfaceAddress(family="IPv6", address="fe80::1234"),
                ],
            ),
            InterfaceInfo(
                name="lo",
                is_up=True,
                mtu=65536,
                speed_mbps=0,
                duplex="Unknown",
                mac_address="",
                addresses=[
                    InterfaceAddress(family="IPv4", address="127.0.0.1"),
                    InterfaceAddress(family="IPv6", address="::1"),
                ],
            ),
        ]

    def _create_dialog(
        self,
        *,
        tmp_root: Path,
        profiles: list[dict[str, object]] | None = None,
        selected_profile_id: str = "",
        on_profiles_changed=None,
        interface_info: list[InterfaceInfo] | None = None,
        parent=None,
    ) -> SyslogSnmpMonitorDialog:
        patchers = [
            patch("snakesh.services.syslog_snmp_monitor.data_dir", return_value=tmp_root),
            patch(
                "snakesh.ui.syslog_snmp_monitor_dialog.collect_interface_info",
                return_value=interface_info if interface_info is not None else self._interface_inventory(),
            ),
        ]
        for active_patch in patchers:
            active_patch.start()
        try:
            dialog = SyslogSnmpMonitorDialog(
                parent=parent,
                profiles=profiles,
                selected_profile_id=selected_profile_id,
                on_profiles_changed=on_profiles_changed,
            )
        except Exception:
            for active_patch in reversed(patchers):
                active_patch.stop()
            raise

        def _cleanup_patches(*_args) -> None:
            for active_patch in reversed(patchers):
                active_patch.stop()

        dialog.destroyed.connect(_cleanup_patches)
        dialog._poll_timer.stop()
        return dialog

    def test_dialog_restores_profile_and_bind_host_preset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dialog = self._create_dialog(
                tmp_root=Path(tmp),
                profiles=[
                    {
                        "id": "monitor-a",
                        "name": "Ops Monitor",
                        "config": {
                            "bind_host": "192.0.2.22",
                            "syslog_udp_enabled": True,
                            "syslog_udp_port": 1514,
                            "syslog_tcp_enabled": False,
                            "snmp_enabled": True,
                            "snmp_port": 1162,
                            "retention": {
                                "hot_retention_days": 30,
                                "archive_retention_days": 180,
                                "max_archive_size_mb": 8192,
                                "archive_rotation_mb": 128,
                            },
                            "filter_state": {
                                "text": "error",
                                "data_scope": "all",
                                "app_name": "sshd",
                            },
                            "visible_columns": ["received_ts", "source", "message_text"],
                        },
                    }
                ],
                selected_profile_id="monitor-a",
            )
            try:
                QApplication.processEvents()
                self.assertEqual(dialog.profile_input.currentData(), "monitor-a")
                self.assertEqual(dialog.bind_host_input.text(), "192.0.2.22")
                self.assertEqual(dialog.bind_host_preset_input.currentData(), "192.0.2.22")
                self.assertTrue(dialog.bind_host_custom_input.isHidden())
                self.assertEqual(dialog.hot_retention_days_input.value(), 30)
                self.assertEqual(dialog.archive_retention_days_input.value(), 180)
                self.assertEqual(dialog.search_input.text(), "error")
                self.assertEqual(dialog.app_name_input.text(), "sshd")
                self.assertEqual(dialog.data_scope_input.currentData(), "all")
                self.assertFalse(dialog.events_tree.isColumnHidden(0))
                self.assertFalse(dialog.events_tree.isColumnHidden(2))
                self.assertFalse(dialog.events_tree.isColumnHidden(12))
            finally:
                dialog.deleteLater()
                QApplication.processEvents()

    def test_dialog_uses_custom_bind_host_entry_when_address_is_not_discovered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dialog = self._create_dialog(
                tmp_root=Path(tmp),
                profiles=[
                    {
                        "id": "monitor-custom",
                        "name": "Custom Bind",
                        "config": {"bind_host": "198.51.100.7"},
                    }
                ],
                selected_profile_id="monitor-custom",
            )
            try:
                QApplication.processEvents()
                self.assertEqual(dialog.bind_host_input.text(), "198.51.100.7")
                self.assertEqual(dialog.bind_host_preset_input.currentData(), CUSTOM_BIND_HOST_VALUE)
                self.assertFalse(dialog.bind_host_custom_input.isHidden())
                self.assertEqual(dialog.bind_host_custom_input.text(), "198.51.100.7")
            finally:
                dialog.deleteLater()
                QApplication.processEvents()

    def test_search_tab_contains_filters_and_results_while_monitor_tab_keeps_runtime_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dialog = self._create_dialog(tmp_root=Path(tmp))
            try:
                QApplication.processEvents()
                self.assertEqual(
                    [dialog.tabs.tabText(index) for index in range(dialog.tabs.count())],
                    ["Settings", "Monitor", "Alerts", "Dashboard", "Archive / Retention"],
                )
                self.assertIs(dialog.tabs.currentWidget(), dialog.monitor_page)
                self.assertTrue(dialog.monitor_page.isAncestorOf(dialog.listener_group))
                self.assertTrue(dialog.search_page.isAncestorOf(dialog.filter_group))
                self.assertTrue(dialog.search_page.isAncestorOf(dialog.events_tree))
                self.assertFalse(dialog.monitor_page.isAncestorOf(dialog.filter_group))
                self.assertFalse(dialog.monitor_page.isAncestorOf(dialog.events_tree))
                self.assertFalse(dialog.search_page.isAncestorOf(dialog.listener_group))
                self.assertEqual(dialog._current_layout_state()["tab_id"], "monitor")
            finally:
                dialog.deleteLater()
                QApplication.processEvents()

    def test_dialog_restores_saved_display_timezone_and_invalid_values_fall_back_to_utc(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dialog = self._create_dialog(
                tmp_root=Path(tmp),
                profiles=[
                    {
                        "id": "monitor-timezone",
                        "name": "Timezone Monitor",
                        "config": {
                            "layout_state": {"display_timezone": "America/New_York"},
                        },
                    }
                ],
                selected_profile_id="monitor-timezone",
            )
            try:
                QApplication.processEvents()
                self.assertEqual(dialog.display_timezone_input.currentData(), "America/New_York")
            finally:
                dialog.deleteLater()
                QApplication.processEvents()

        with tempfile.TemporaryDirectory() as tmp:
            dialog = self._create_dialog(
                tmp_root=Path(tmp),
                profiles=[
                    {
                        "id": "monitor-bad-timezone",
                        "name": "Invalid Timezone",
                        "config": {
                            "layout_state": {"display_timezone": "Mars/Olympus"},
                        },
                    }
                ],
                selected_profile_id="monitor-bad-timezone",
            )
            try:
                QApplication.processEvents()
                self.assertEqual(dialog.display_timezone_input.currentData(), "utc")
            finally:
                dialog.deleteLater()
                QApplication.processEvents()

    def test_dialog_restores_saved_tab_id_and_legacy_tab_indexes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dialog = self._create_dialog(
                tmp_root=Path(tmp),
                profiles=[
                    {
                        "id": "monitor-search-tab",
                        "name": "Search Tab Monitor",
                        "config": {
                            "layout_state": {"tab_id": "search"},
                        },
                    }
                ],
                selected_profile_id="monitor-search-tab",
            )
            try:
                QApplication.processEvents()
                self.assertIs(dialog.tabs.currentWidget(), dialog.search_page)
                self.assertEqual(dialog._current_layout_state()["tab_id"], "search")
            finally:
                dialog.deleteLater()
                QApplication.processEvents()

        legacy_cases = [
            (0, "Settings"),
            (1, "Alerts"),
            (2, "Dashboard"),
            (3, "Archive / Retention"),
        ]
        for legacy_index, expected_tab in legacy_cases:
            with self.subTest(tab_index=legacy_index):
                with tempfile.TemporaryDirectory() as tmp:
                    dialog = self._create_dialog(
                        tmp_root=Path(tmp),
                        profiles=[
                            {
                                "id": f"monitor-legacy-{legacy_index}",
                                "name": f"Legacy {legacy_index}",
                                "config": {
                                    "layout_state": {"tab_index": legacy_index},
                                },
                            }
                        ],
                        selected_profile_id=f"monitor-legacy-{legacy_index}",
                    )
                    try:
                        QApplication.processEvents()
                        self.assertEqual(dialog.tabs.tabText(dialog.tabs.currentIndex()), expected_tab)
                    finally:
                        dialog.deleteLater()
                        QApplication.processEvents()

    def test_runtime_badge_reflects_polled_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dialog = self._create_dialog(tmp_root=Path(tmp))
            try:
                cases = [
                    (SyslogSnmpMonitorStatus(state="running", message="Listening for events."), "Running", "#166534"),
                    (SyslogSnmpMonitorStatus(state="starting", message="Collector starting."), "Starting", "#92400e"),
                    (SyslogSnmpMonitorStatus(state="stopped", message="Collector stopped."), "Stopped", "#b91c1c"),
                    (SyslogSnmpMonitorStatus(state="error", error="Bind failed."), "Error", "#b91c1c"),
                ]
                for status, expected_text, expected_color in cases:
                    with (
                        self.subTest(state=status.state),
                        patch("snakesh.ui.syslog_snmp_monitor_dialog.read_syslog_snmp_monitor_status", return_value=status),
                        patch.object(dialog, "_reload_events"),
                        patch.object(dialog, "_refresh_storage_stats"),
                        patch.object(dialog, "_show_notifications"),
                    ):
                        dialog._poll_runtime_state()
                        QApplication.processEvents()
                        self.assertEqual(dialog.runtime_badge.text(), expected_text)
                        self.assertIn(expected_color, dialog.runtime_badge.styleSheet())
            finally:
                dialog.deleteLater()
                QApplication.processEvents()

    def test_start_monitor_writes_starting_status_and_returns_without_modal_wait(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dialog = self._create_dialog(
                tmp_root=Path(tmp),
                profiles=[{"id": "monitor-start", "name": "Start Monitor", "config": {}}],
                selected_profile_id="monitor-start",
            )
            try:
                with (
                    patch(
                        "snakesh.ui.syslog_snmp_monitor_dialog.needs_syslog_snmp_monitor_gui_elevation",
                        return_value=False,
                    ),
                    patch("snakesh.ui.syslog_snmp_monitor_dialog.launch_syslog_snmp_monitor_helper") as mock_launch,
                    patch("snakesh.ui.syslog_snmp_monitor_dialog.write_syslog_snmp_monitor_status") as mock_write_status,
                    patch("snakesh.ui.syslog_snmp_monitor_dialog.QMessageBox.warning") as mock_warning,
                    patch.object(dialog, "_poll_runtime_state") as mock_poll,
                ):
                    dialog._start_monitor()

                self.assertEqual(mock_launch.call_count, 1)
                self.assertEqual(mock_write_status.call_count, 1)
                status = mock_write_status.call_args.args[1]
                self.assertEqual(status.state, "starting")
                self.assertEqual(status.bind_host, "0.0.0.0")
                mock_warning.assert_not_called()
                mock_poll.assert_called_once()
            finally:
                dialog.deleteLater()
                QApplication.processEvents()

    def test_poll_runtime_state_allows_delayed_start_to_reach_running_without_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dialog = self._create_dialog(
                tmp_root=Path(tmp),
                profiles=[{"id": "monitor-start", "name": "Start Monitor", "config": {}}],
                selected_profile_id="monitor-start",
            )
            try:
                dialog._pending_runtime_start_profile_id = "monitor-start"
                dialog._pending_runtime_start_since = 10.0
                statuses = iter(
                    [
                        SyslogSnmpMonitorStatus(state="starting", message="Launching collector..."),
                        SyslogSnmpMonitorStatus(
                            state="running",
                            message="Listening for events.",
                            bind_host="0.0.0.0",
                            listeners=["Syslog UDP 1514"],
                        ),
                    ]
                )
                with (
                    patch("snakesh.ui.syslog_snmp_monitor_dialog.read_syslog_snmp_monitor_status", side_effect=lambda *_: next(statuses)),
                    patch.object(dialog, "_reload_events"),
                    patch.object(dialog, "_refresh_storage_stats"),
                    patch.object(dialog, "_show_notifications"),
                    patch("snakesh.ui.syslog_snmp_monitor_dialog.QMessageBox.warning") as mock_warning,
                ):
                    dialog._poll_runtime_state()
                    dialog._poll_runtime_state()

                mock_warning.assert_not_called()
                self.assertEqual(dialog.runtime_badge.text(), "Running")
                self.assertEqual(dialog._pending_runtime_start_profile_id, "")
            finally:
                dialog.deleteLater()
                QApplication.processEvents()

    def test_poll_runtime_state_surfaces_start_error_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dialog = self._create_dialog(
                tmp_root=Path(tmp),
                profiles=[{"id": "monitor-start", "name": "Start Monitor", "config": {}}],
                selected_profile_id="monitor-start",
            )
            try:
                dialog._pending_runtime_start_profile_id = "monitor-start"
                dialog._pending_runtime_start_since = 10.0
                status = SyslogSnmpMonitorStatus(state="error", message="Bind failed.")
                with (
                    patch("snakesh.ui.syslog_snmp_monitor_dialog.read_syslog_snmp_monitor_status", return_value=status),
                    patch.object(dialog, "_reload_events"),
                    patch.object(dialog, "_refresh_storage_stats"),
                    patch.object(dialog, "_show_notifications"),
                    patch("snakesh.ui.syslog_snmp_monitor_dialog.QMessageBox.warning") as mock_warning,
                ):
                    dialog._poll_runtime_state()
                    dialog._poll_runtime_state()

                mock_warning.assert_called_once_with(dialog, "Start Monitor", "Bind failed.")
                self.assertEqual(dialog.runtime_badge.text(), "Error")
            finally:
                dialog.deleteLater()
                QApplication.processEvents()

    def test_poll_runtime_state_shows_windows_start_hint_after_stale_delay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dialog = self._create_dialog(
                tmp_root=Path(tmp),
                profiles=[{"id": "monitor-start", "name": "Start Monitor", "config": {}}],
                selected_profile_id="monitor-start",
            )
            try:
                dialog._pending_runtime_start_profile_id = "monitor-start"
                dialog._pending_runtime_start_since = 5.0
                with (
                    patch("snakesh.ui.syslog_snmp_monitor_dialog.platform.system", return_value="Windows"),
                    patch("snakesh.ui.syslog_snmp_monitor_dialog.time.monotonic", return_value=40.0),
                    patch(
                        "snakesh.ui.syslog_snmp_monitor_dialog.read_syslog_snmp_monitor_status",
                        return_value=SyslogSnmpMonitorStatus(state="starting", message="Launching collector..."),
                    ),
                    patch.object(dialog, "_reload_events"),
                    patch.object(dialog, "_refresh_storage_stats"),
                    patch.object(dialog, "_show_notifications"),
                ):
                    dialog._poll_runtime_state()

                self.assertIn("firewall prompt", dialog.status_label.text().lower())
            finally:
                dialog.deleteLater()
                QApplication.processEvents()

    def test_copy_actions_and_dashboard_use_current_filtered_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dialog = self._create_dialog(
                tmp_root=Path(tmp),
                profiles=[
                    {
                        "id": "monitor-live",
                        "name": "Live Monitor",
                        "config": {"bind_host": "0.0.0.0"},
                    }
                ],
                selected_profile_id="monitor-live",
            )
            try:
                with patch("snakesh.services.syslog_snmp_monitor.data_dir", return_value=Path(tmp)):
                    insert_monitor_event(
                        "monitor-live",
                        {
                            "received_ts": "2026-03-31T15:00:00+00:00",
                            "source_ip": "192.0.2.10",
                            "listener": "syslog-udp",
                            "protocol": "syslog",
                            "transport": "udp",
                            "severity": 3,
                            "severity_name": "Error",
                            "facility": 4,
                            "facility_name": "auth",
                            "syslog_hostname": "edge01",
                            "app_name": "sshd",
                            "message_text": "Login failed",
                            "alerted": True,
                            "raw_payload": "raw-1",
                        },
                    )
                    insert_monitor_event(
                        "monitor-live",
                        {
                            "received_ts": "2026-03-31T15:02:00+00:00",
                            "source_ip": "192.0.2.11",
                            "listener": "snmp",
                            "protocol": "snmp",
                            "transport": "udp",
                            "snmp_version": "v2c",
                            "snmp_security_name": "public",
                            "notification_oid": "1.3.6.1.4.1.9.9.41.2.0.1",
                            "varbind_summary": "ifOperStatus=down",
                            "raw_payload": "raw-2",
                        },
                    )

                with patch("snakesh.services.syslog_snmp_monitor.data_dir", return_value=Path(tmp)):
                    dialog._reload_events()
                    QApplication.processEvents()

                self.assertEqual(dialog.events_tree.topLevelItemCount(), 2)
                target = None
                for index in range(dialog.events_tree.topLevelItemCount()):
                    item = dialog.events_tree.topLevelItem(index)
                    if "Login failed" in item.text(12):
                        target = item
                        break
                self.assertIsNotNone(target)
                assert target is not None
                dialog.events_tree.setCurrentItem(target)
                target.setSelected(True)
                QApplication.processEvents()

                dialog._copy_current_row()
                row_clipboard = QApplication.clipboard().text()
                self.assertIn("Received", row_clipboard)
                self.assertIn("Login failed", row_clipboard)

                dialog._set_current_event_column(2)
                dialog._copy_current_column()
                column_clipboard = QApplication.clipboard().text()
                self.assertIn("Source", column_clipboard)
                self.assertIn("192.0.2.10", column_clipboard)

                self.assertGreater(len(dialog.event_rate_chart.chart().series()), 0)
                self.assertGreater(len(dialog.top_sources_chart.chart().series()), 0)
                self.assertGreater(len(dialog.top_traps_chart.chart().series()), 0)
            finally:
                dialog.deleteLater()
                QApplication.processEvents()

    def test_dashboard_charts_follow_parent_theme_colors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            parent = QDialog()
            parent._settings = AppSettings.defaults()
            parent._settings.field_bg = "#f4efe6"
            parent._settings.app_bg_end = "#e6ddd0"
            parent._settings.text_color = "#1f2937"
            parent._settings.field_border = "#887a67"
            parent._settings.accent_color = "#0f766e"
            dialog = self._create_dialog(
                tmp_root=Path(tmp),
                profiles=[
                    {
                        "id": "monitor-theme",
                        "name": "Theme Monitor",
                        "config": {},
                    }
                ],
                selected_profile_id="monitor-theme",
                parent=parent,
            )
            try:
                with patch("snakesh.services.syslog_snmp_monitor.data_dir", return_value=Path(tmp)):
                    insert_monitor_event(
                        "monitor-theme",
                        {
                            "received_ts": "2026-03-31T15:00:00+00:00",
                            "source_ip": "192.0.2.10",
                            "listener": "syslog-udp",
                            "protocol": "syslog",
                            "transport": "udp",
                            "message_text": "theme event",
                        },
                    )
                    dialog._reload_events()
                    QApplication.processEvents()

                chart = dialog.event_rate_chart.chart()
                self.assertEqual(chart.backgroundBrush().color().name(), "#f4efe6")
                series = chart.series()[0]
                bar_set = series.barSets()[0]
                self.assertEqual(bar_set.color().name(), "#0f766e")
            finally:
                dialog.deleteLater()
                parent.deleteLater()
                QApplication.processEvents()

    def test_apply_runtime_settings_refreshes_dashboard_and_open_alert_window_styles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dialog = self._create_dialog(tmp_root=Path(tmp))
            try:
                alert_window = dialog._current_alert_notifications_window()
                alert_window.body_text.setPlainText("router02 rejected login for admin")
                before_summary_font = dialog.event_summary_text.font().pointSize()
                before_alert_font = alert_window.body_text.font().pointSize()

                settings = AppSettings.defaults()
                settings.field_bg = "#f4efe6"
                settings.field_border = "#887a67"
                settings.accent_color = "#0f766e"
                settings.terminal_font_pt = max(before_alert_font + 4, 12)

                dialog.apply_runtime_settings(settings)
                QApplication.processEvents()

                self.assertIsNotNone(dialog._settings)
                assert dialog._settings is not None
                self.assertEqual(dialog._settings.accent_color, settings.accent_color)
                self.assertEqual(dialog.event_rate_chart.chart().backgroundBrush().color().name(), settings.field_bg)
                self.assertEqual(dialog.event_summary_text.font().pointSize(), settings.terminal_font_pt)
                self.assertEqual(alert_window.body_text.font().pointSize(), settings.terminal_font_pt)
                self.assertGreater(dialog.event_summary_text.font().pointSize(), before_summary_font)
                self.assertGreater(alert_window.body_text.font().pointSize(), before_alert_font)
            finally:
                dialog.deleteLater()
                QApplication.processEvents()

    def test_display_timezone_updates_event_table_and_storage_labels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dialog = self._create_dialog(
                tmp_root=Path(tmp),
                profiles=[
                    {
                        "id": "monitor-tz-display",
                        "name": "Timezone Display",
                        "config": {},
                    }
                ],
                selected_profile_id="monitor-tz-display",
            )
            try:
                with patch("snakesh.services.syslog_snmp_monitor.data_dir", return_value=Path(tmp)):
                    insert_monitor_event(
                        "monitor-tz-display",
                        {
                            "received_ts": "2026-03-31T15:00:00+00:00",
                            "event_ts": "2026-03-31T15:01:00+00:00",
                            "source_ip": "192.0.2.10",
                            "listener": "syslog-udp",
                            "protocol": "syslog",
                            "transport": "udp",
                            "message_text": "timezone event",
                        },
                    )
                    dialog._reload_events()
                    dialog._refresh_storage_stats()
                    QApplication.processEvents()

                with patch("snakesh.services.syslog_snmp_monitor.data_dir", return_value=Path(tmp)):
                    index = dialog.display_timezone_input.findData("America/New_York")
                    self.assertGreaterEqual(index, 0)
                    dialog.display_timezone_input.setCurrentIndex(index)
                    QApplication.processEvents()

                item = dialog.events_tree.topLevelItem(0)
                self.assertIn("2026-03-31 11:00:00-04:00", item.text(0))
                self.assertIn("2026-03-31 11:01:00-04:00", dialog.event_summary_text.toPlainText())
                self.assertEqual(dialog.oldest_live_label.text(), "2026-03-31 11:00:00-04:00")
                self.assertEqual(dialog.newest_live_label.text(), "2026-03-31 11:00:00-04:00")
            finally:
                dialog.deleteLater()
                QApplication.processEvents()

    def test_double_click_opens_event_popup_for_live_and_archived_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dialog = self._create_dialog(
                tmp_root=Path(tmp),
                profiles=[
                    {
                        "id": "monitor-popup",
                        "name": "Popup Monitor",
                        "config": {},
                    }
                ],
                selected_profile_id="monitor-popup",
            )
            try:
                with patch("snakesh.services.syslog_snmp_monitor.data_dir", return_value=Path(tmp)):
                    insert_monitor_event(
                        "monitor-popup",
                        {
                            "received_ts": "2026-03-31T15:00:00+00:00",
                            "source_ip": "192.0.2.10",
                            "listener": "syslog-udp",
                            "protocol": "syslog",
                            "transport": "udp",
                            "message_text": "live popup event",
                        },
                    )
                    dialog._reload_events()
                    QApplication.processEvents()

                live_item = dialog.events_tree.topLevelItem(0)
                dialog.events_tree.itemDoubleClicked.emit(live_item, 0)
                QApplication.processEvents()
                live_popup = dialog._event_detail_windows[-1]
                self.assertIn("live popup event", live_popup.summary_text.toPlainText())
                self.assertTrue(bool(live_popup.windowFlags() & Qt.WindowType.WindowMaximizeButtonHint))
                live_popup.close()
                QApplication.processEvents()

                with patch("snakesh.services.syslog_snmp_monitor.data_dir", return_value=Path(tmp)):
                    insert_monitor_event(
                        "monitor-popup",
                        {
                            "received_ts": "2026-03-20T15:00:00+00:00",
                            "source_ip": "192.0.2.11",
                            "listener": "syslog-udp",
                            "protocol": "syslog",
                            "transport": "udp",
                            "message_text": "archived popup event",
                        },
                    )
                    archive_monitor_events(
                        "monitor-popup",
                        retention=MonitorRetentionPolicy(
                            hot_retention_days=1,
                            archive_retention_days=90,
                            max_archive_size_mb=4096,
                            archive_rotation_mb=64,
                        ),
                    )
                    dialog.data_scope_input.setCurrentIndex(dialog.data_scope_input.findData("archived"))
                    dialog._reload_events()
                    QApplication.processEvents()

                archived_item = None
                for index in range(dialog.events_tree.topLevelItemCount()):
                    item = dialog.events_tree.topLevelItem(index)
                    if "archived popup event" in item.text(12):
                        archived_item = item
                        break
                self.assertIsNotNone(archived_item)
                assert archived_item is not None
                dialog.events_tree.itemDoubleClicked.emit(archived_item, 0)
                QApplication.processEvents()
                archived_popup = dialog._event_detail_windows[-1]
                self.assertIn("archived popup event", archived_popup.summary_text.toPlainText())
                archived_popup.close()
                QApplication.processEvents()
            finally:
                dialog.deleteLater()
                QApplication.processEvents()

    def test_close_warns_and_stops_running_collector_before_hiding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dialog = self._create_dialog(tmp_root=Path(tmp))
            try:
                dialog.show()
                QApplication.processEvents()
                poll_count = {"value": 0}

                def _status(*_args, **_kwargs) -> SyslogSnmpMonitorStatus:
                    poll_count["value"] += 1
                    if poll_count["value"] < 3:
                        return SyslogSnmpMonitorStatus(state="running", message="Listening for events.")
                    return SyslogSnmpMonitorStatus(state="stopped", message="Collector stopped.")

                with (
                    patch("snakesh.ui.syslog_snmp_monitor_dialog.read_syslog_snmp_monitor_status", side_effect=_status),
                    patch("snakesh.ui.syslog_snmp_monitor_dialog.request_syslog_snmp_monitor_stop") as mock_stop,
                    patch("snakesh.ui.syslog_snmp_monitor_dialog.QMessageBox.question", return_value=QMessageBox.Yes),
                ):
                    closed = dialog.close()
                    QApplication.processEvents()

                self.assertTrue(closed)
                self.assertEqual(mock_stop.call_count, 1)
                self.assertFalse(dialog.isVisible())
            finally:
                dialog.deleteLater()
                QApplication.processEvents()

    def test_close_cancel_keeps_dialog_open_when_collector_is_running(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dialog = self._create_dialog(tmp_root=Path(tmp))
            try:
                dialog.show()
                QApplication.processEvents()
                with (
                    patch(
                        "snakesh.ui.syslog_snmp_monitor_dialog.read_syslog_snmp_monitor_status",
                        return_value=SyslogSnmpMonitorStatus(state="running", message="Listening for events."),
                    ),
                    patch("snakesh.ui.syslog_snmp_monitor_dialog.QMessageBox.question", return_value=QMessageBox.No),
                ):
                    closed = dialog.close()
                    QApplication.processEvents()

                self.assertFalse(closed)
                self.assertTrue(dialog.isVisible())
            finally:
                dialog.deleteLater()
                QApplication.processEvents()

    def test_notifications_use_live_alert_dialog_and_bell_sound(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dialog = self._create_dialog(
                tmp_root=Path(tmp),
                profiles=[
                    {
                        "id": "monitor-alerts",
                        "name": "Alert Monitor",
                        "config": {},
                    }
                ],
                selected_profile_id="monitor-alerts",
            )
            try:
                notifications = [
                    {
                        "id": 101,
                        "event_id": 501,
                        "created_ts": "2026-03-31T15:00:00+00:00",
                        "title": "Interface Down",
                        "body": "edge01 Gi0/1 changed to down",
                        "play_sound": True,
                    },
                    {
                        "id": 102,
                        "event_id": 502,
                        "created_ts": "2026-03-31T15:01:00+00:00",
                        "title": "Authentication Failure",
                        "body": "router02 rejected login for admin",
                        "play_sound": True,
                    },
                ]
                with (
                    patch("snakesh.ui.syslog_snmp_monitor_dialog.fetch_unshown_notifications", return_value=notifications),
                    patch("snakesh.ui.syslog_snmp_monitor_dialog.mark_notifications_shown") as mock_mark,
                    patch.object(dialog._alert_sound_player, "play") as mock_play,
                ):
                    dialog._show_notifications()
                    QApplication.processEvents()

                alert_window = dialog._alert_notifications_window
                self.assertIsNotNone(alert_window)
                assert alert_window is not None
                self.assertTrue(alert_window.isVisible())
                self.assertEqual(alert_window.alerts_tree.topLevelItemCount(), 2)
                self.assertIn("2 current alert", alert_window.summary_label.text())
                self.assertIn("edge01 Gi0/1 changed to down", alert_window.body_text.toPlainText())
                mock_play.assert_called_once()
                mock_mark.assert_called_once_with("monitor-alerts", [101, 102])

                alert_window.dismiss_btn.click()
                QApplication.processEvents()
                self.assertFalse(alert_window.isVisible())
                self.assertEqual(alert_window.alerts_tree.topLevelItemCount(), 0)
                self.assertEqual(alert_window.body_text.toPlainText(), "")
            finally:
                dialog.deleteLater()
                QApplication.processEvents()

    def test_clear_database_is_stopped_only_and_clears_profile_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dialog = self._create_dialog(
                tmp_root=Path(tmp),
                profiles=[
                    {
                        "id": "monitor-clear",
                        "name": "Clear Monitor",
                        "config": {},
                    }
                ],
                selected_profile_id="monitor-clear",
            )
            try:
                with patch(
                    "snakesh.ui.syslog_snmp_monitor_dialog.read_syslog_snmp_monitor_status",
                    return_value=SyslogSnmpMonitorStatus(state="running"),
                ), patch("snakesh.ui.syslog_snmp_monitor_dialog.QMessageBox.warning") as mock_warning:
                    dialog._clear_database()
                self.assertIn("only available while the collector is stopped", mock_warning.call_args.args[2])

                with patch("snakesh.services.syslog_snmp_monitor.data_dir", return_value=Path(tmp)):
                    insert_monitor_event(
                        "monitor-clear",
                        {
                            "received_ts": "2026-03-31T15:00:00+00:00",
                            "source_ip": "192.0.2.10",
                            "listener": "syslog-udp",
                            "protocol": "syslog",
                            "transport": "udp",
                            "message_text": "clear live event",
                        },
                    )
                    insert_monitor_event(
                        "monitor-clear",
                        {
                            "received_ts": "2026-03-20T15:00:00+00:00",
                            "source_ip": "192.0.2.11",
                            "listener": "syslog-udp",
                            "protocol": "syslog",
                            "transport": "udp",
                            "message_text": "clear archived event",
                        },
                    )
                    archive_monitor_events(
                        "monitor-clear",
                        retention=MonitorRetentionPolicy(
                            hot_retention_days=1,
                            archive_retention_days=90,
                            max_archive_size_mb=4096,
                            archive_rotation_mb=64,
                        ),
                    )
                    dialog._reload_events()
                    dialog._refresh_storage_stats()
                    QApplication.processEvents()

                with (
                    patch("snakesh.services.syslog_snmp_monitor.data_dir", return_value=Path(tmp)),
                    patch("snakesh.ui.syslog_snmp_monitor_dialog.QMessageBox.warning", return_value=QMessageBox.Yes),
                ):
                    dialog._clear_database()
                    QApplication.processEvents()

                self.assertEqual(dialog.events_tree.topLevelItemCount(), 0)
                self.assertEqual(dialog.live_event_count_label.text(), "0")
                self.assertEqual(dialog.archive_file_count_label.text(), "0")
                self.assertIn("Cleared", dialog.status_label.text())
            finally:
                dialog.deleteLater()
                QApplication.processEvents()

    def test_monitor_window_requests_maximize_button(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dialog = self._create_dialog(tmp_root=Path(tmp))
            try:
                self.assertTrue(bool(dialog.windowFlags() & Qt.WindowType.WindowMaximizeButtonHint))
            finally:
                dialog.deleteLater()
                QApplication.processEvents()

    def test_profile_save_rename_and_delete_are_gui_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            changes: list[tuple[list[dict[str, object]], str]] = []
            dialog = self._create_dialog(
                tmp_root=Path(tmp),
                profiles=[
                    {
                        "id": "monitor-base",
                        "name": "Base Monitor",
                        "config": {"bind_host": "0.0.0.0"},
                    }
                ],
                selected_profile_id="monitor-base",
                on_profiles_changed=lambda profiles, selected: changes.append((profiles, selected)),
            )
            try:
                with patch("snakesh.ui.syslog_snmp_monitor_dialog.QInputDialog.getText", return_value=("Branch Monitor", True)):
                    dialog._save_current_profile_as()
                self.assertEqual(len(dialog._profiles), 2)
                self.assertEqual(dialog._selected_profile_name(), "Branch Monitor")

                with patch("snakesh.ui.syslog_snmp_monitor_dialog.QInputDialog.getText", return_value=("Renamed Branch", True)):
                    dialog._rename_selected_profile()
                self.assertEqual(dialog._selected_profile_name(), "Renamed Branch")

                with patch("snakesh.ui.syslog_snmp_monitor_dialog.QMessageBox.question", return_value=QMessageBox.Yes):
                    dialog._delete_selected_profile()
                self.assertEqual(len(dialog._profiles), 1)
                self.assertEqual(dialog._selected_profile_name(), "Base Monitor")
                self.assertTrue(changes)
            finally:
                dialog.deleteLater()
                QApplication.processEvents()

    def test_open_profile_folder_uses_local_path_launcher(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dialog = self._create_dialog(
                tmp_root=Path(tmp),
                profiles=[
                    {
                        "id": "monitor-open-folder",
                        "name": "Open Folder",
                        "config": {},
                    }
                ],
                selected_profile_id="monitor-open-folder",
            )
            try:
                with (
                    patch("snakesh.services.syslog_snmp_monitor.data_dir", return_value=Path(tmp)),
                    patch("snakesh.ui.syslog_snmp_monitor_dialog.open_local_path", return_value=True) as mock_open,
                ):
                    dialog._open_profile_folder()
                self.assertEqual(
                    mock_open.call_args.args[0],
                    Path(tmp) / "syslog-snmp-monitor" / "profiles" / "monitor-open-folder",
                )
            finally:
                dialog.deleteLater()
                QApplication.processEvents()


if __name__ == "__main__":
    unittest.main()
