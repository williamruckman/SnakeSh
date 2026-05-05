from __future__ import annotations

from dataclasses import replace
import os
import threading
import time
import unittest
from unittest.mock import patch

from PySide6.QtCharts import QSplineSeries
from PySide6.QtCore import QPoint, QThread, Qt
from PySide6.QtGui import QCloseEvent, QPainter
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QSizePolicy

from snakesh.services.resource_monitor import (
    DiskDeviceSample,
    FilesystemEntry,
    GpuAdapterSample,
    GpuSample,
    InterfaceBandwidthEntry,
    ProcessActionResult,
    ProcessEntry,
    ProcessInventorySnapshot,
    ResourceMonitorSample,
    ResourceMonitorSnapshot,
)
from snakesh.services.settings_service import AppSettings
from snakesh.ui import resource_monitor_dialog as resource_monitor_dialog_module
from snakesh.ui.resource_monitor_dialog import (
    ResourceMonitorDialog,
    _gpu_adapter_memory_percent,
    _gpu_adapter_telemetry_status_text,
)


class ResourceMonitorDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls._app = QApplication.instance() or QApplication([])

    def _build_snapshot(self, *, gpu_available: bool = False) -> ResourceMonitorSnapshot:
        sample = ResourceMonitorSample(
            timestamp_monotonic=10.0,
            cpu_percent=32.0,
            logical_cpu_count=16,
            memory_used_bytes=12_000_000_000,
            memory_total_bytes=32_000_000_000,
            memory_percent=37.5,
            swap_used_bytes=1_000_000_000,
            swap_total_bytes=8_000_000_000,
            swap_percent=12.5,
            disk_mountpoint="/home",
            disk_used_bytes=400_000_000_000,
            disk_total_bytes=1_000_000_000_000,
            disk_free_bytes=600_000_000_000,
            disk_percent=40.0,
            disk_read_bytes_per_sec=2_000_000.0,
            disk_write_bytes_per_sec=1_000_000.0,
            disk_read_bytes_since_open=9_000_000,
            disk_write_bytes_since_open=4_000_000,
            network_recv_bytes_per_sec=4_000_000.0,
            network_sent_bytes_per_sec=500_000.0,
            network_recv_bytes_since_open=10_000_000,
            network_sent_bytes_since_open=2_000_000,
            process_count=245,
            thread_count=1820,
            cpu_temperature_c=67.0,
            cpu_per_core_percentages=(12.5, 24.0, 31.0, 48.5),
        )
        gpu = (
            GpuSample(
                available=True,
                detected=True,
                name="RTX Test",
                gpu_count=1,
                utilization_percent=58.0,
                memory_used_bytes=2_000_000_000,
                memory_total_bytes=8_000_000_000,
                memory_percent=25.0,
                temperature_c=61.0,
                has_utilization=True,
                has_memory=True,
                has_temperature=True,
                adapters=[
                    GpuAdapterSample(
                        id="0000:01:00.0",
                        vendor="NVIDIA",
                        name="RTX Test",
                        adapter_index=0,
                        backend="nvidia-smi",
                        utilization_percent=58.0,
                        memory_used_bytes=2_000_000_000,
                        memory_total_bytes=8_000_000_000,
                        temperature_c=61.0,
                    )
                ],
            )
            if gpu_available
            else GpuSample(message="GPU telemetry is unavailable.")
        )
        return ResourceMonitorSnapshot(
            sample=sample,
            filesystems=[
                FilesystemEntry(
                    device="/dev/home",
                    mountpoint="/home",
                    filesystem_type="ext4",
                    used_bytes=sample.disk_used_bytes,
                    total_bytes=sample.disk_total_bytes,
                    free_bytes=sample.disk_free_bytes,
                    usage_percent=sample.disk_percent,
                    is_home=True,
                    disk_device_key="disk-home",
                )
            ],
            disk_devices=[
                DiskDeviceSample(
                    key="disk-home",
                    display_label="nvme0n1p5",
                    read_bytes_per_sec=sample.disk_read_bytes_per_sec,
                    write_bytes_per_sec=sample.disk_write_bytes_per_sec,
                    read_bytes_since_open=sample.disk_read_bytes_since_open,
                    write_bytes_since_open=sample.disk_write_bytes_since_open,
                )
            ],
            interfaces=[
                InterfaceBandwidthEntry(
                    name="eth0",
                    ipv4_address="192.0.2.20",
                    ipv6_address="2001:db8::20",
                    is_up=True,
                    speed_mbps=1000,
                    recv_bytes_per_sec=sample.network_recv_bytes_per_sec,
                    sent_bytes_per_sec=sample.network_sent_bytes_per_sec,
                    recv_bytes_total=22_000_000,
                    sent_bytes_total=8_000_000,
                )
            ],
            gpu=gpu,
        )

    def _build_multi_gpu_snapshot(self) -> ResourceMonitorSnapshot:
        snapshot = self._build_snapshot(gpu_available=True)
        gpu = GpuSample(
            available=True,
            detected=True,
            name="2 GPUs",
            gpu_count=2,
            utilization_percent=29.0,
            memory_used_bytes=2_500_000_000,
            memory_total_bytes=12_000_000_000,
            memory_percent=(2_500_000_000 / 12_000_000_000) * 100.0,
            temperature_c=61.0,
            has_utilization=True,
            has_memory=True,
            has_temperature=True,
            adapters=[
                GpuAdapterSample(
                    id="gpu-intel-0",
                    vendor="Intel",
                    name="Intel(R) UHD Graphics",
                    adapter_index=0,
                    backend="windows-counters",
                    utilization_percent=12.0,
                    memory_used_bytes=500_000_000,
                    memory_total_bytes=4_000_000_000,
                    temperature_c=None,
                ),
                GpuAdapterSample(
                    id="gpu-nvidia-1",
                    vendor="NVIDIA",
                    name="NVIDIA GeForce RTX 3050 Ti Laptop GPU",
                    adapter_index=1,
                    backend="nvidia-smi",
                    utilization_percent=46.0,
                    memory_used_bytes=2_000_000_000,
                    memory_total_bytes=8_000_000_000,
                    temperature_c=61.0,
                ),
            ],
            message="Some GPU metrics are unavailable on this system.",
        )
        return ResourceMonitorSnapshot(
            sample=snapshot.sample,
            filesystems=snapshot.filesystems,
            disk_devices=snapshot.disk_devices,
            interfaces=snapshot.interfaces,
            gpu=gpu,
        )

    def _build_multi_device_snapshot(self) -> ResourceMonitorSnapshot:
        snapshot = self._build_multi_gpu_snapshot()
        return ResourceMonitorSnapshot(
            sample=snapshot.sample,
            filesystems=[
                FilesystemEntry(
                    device="/dev/home",
                    mountpoint="/home",
                    filesystem_type="ext4",
                    used_bytes=400_000_000_000,
                    total_bytes=1_000_000_000_000,
                    free_bytes=600_000_000_000,
                    usage_percent=40.0,
                    is_home=True,
                    disk_device_key="disk-home",
                ),
                FilesystemEntry(
                    device="/dev/data",
                    mountpoint="/data",
                    filesystem_type="xfs",
                    used_bytes=200_000_000_000,
                    total_bytes=500_000_000_000,
                    free_bytes=300_000_000_000,
                    usage_percent=40.0,
                    disk_device_key="disk-data",
                ),
            ],
            disk_devices=[
                DiskDeviceSample(
                    key="disk-home",
                    display_label="nvme0n1p5",
                    read_bytes_per_sec=2_000_000.0,
                    write_bytes_per_sec=1_000_000.0,
                    read_bytes_since_open=9_000_000,
                    write_bytes_since_open=4_000_000,
                ),
                DiskDeviceSample(
                    key="disk-data",
                    display_label="sdb1",
                    read_bytes_per_sec=250_000.0,
                    write_bytes_per_sec=128_000.0,
                    read_bytes_since_open=1_500_000,
                    write_bytes_since_open=750_000,
                ),
            ],
            interfaces=[
                InterfaceBandwidthEntry(
                    name="eth0",
                    ipv4_address="192.0.2.20",
                    ipv6_address="2001:db8::20",
                    is_up=True,
                    speed_mbps=1000,
                    recv_bytes_per_sec=4_000_000.0,
                    sent_bytes_per_sec=500_000.0,
                    recv_bytes_total=22_000_000,
                    sent_bytes_total=8_000_000,
                ),
                InterfaceBandwidthEntry(
                    name="wlan0",
                    ipv4_address="192.0.2.55",
                    ipv6_address="2001:db8::55",
                    is_up=True,
                    speed_mbps=866,
                    recv_bytes_per_sec=64_000.0,
                    sent_bytes_per_sec=32_000.0,
                    recv_bytes_total=1_200_000,
                    sent_bytes_total=700_000,
                ),
            ],
            gpu=snapshot.gpu,
        )

    def _build_snapshot_with_offline_interface(self) -> ResourceMonitorSnapshot:
        snapshot = self._build_snapshot(gpu_available=False)
        return ResourceMonitorSnapshot(
            sample=snapshot.sample,
            filesystems=snapshot.filesystems,
            disk_devices=snapshot.disk_devices,
            interfaces=[
                snapshot.interfaces[0],
                InterfaceBandwidthEntry(
                    name="wlan0",
                    ipv4_address="Unassigned",
                    ipv6_address="Unassigned",
                    is_up=False,
                    speed_mbps=866,
                    recv_bytes_per_sec=0.0,
                    sent_bytes_per_sec=0.0,
                    recv_bytes_total=0,
                    sent_bytes_total=0,
                ),
            ],
            gpu=snapshot.gpu,
        )

    def _build_process_snapshot(self) -> ProcessInventorySnapshot:
        return ProcessInventorySnapshot(
            entries=[
                ProcessEntry(
                    pid=100,
                    name="python",
                    cpu_percent=21.5,
                    memory_rss_bytes=700_000_000,
                    threads=10,
                    user="alice",
                    status="running",
                    started_at=1_700_000_000.0,
                    command="python app.py",
                ),
                ProcessEntry(
                    pid=200,
                    name="sshd",
                    cpu_percent=0.4,
                    memory_rss_bytes=20_000_000,
                    threads=2,
                    user="root",
                    status="sleeping",
                    started_at=1_699_000_000.0,
                    command="sshd -D",
                ),
            ],
            total_threads=12,
            collected_at=12.0,
        )

    def _chart_axis_title(self, dialog: ResourceMonitorDialog, view) -> str:
        state = dialog._chart_states.get(view, {})
        axis = state.get("axis_y")
        if axis is None:
            return ""
        return str(axis.titleText())

    def _series_y_values(self, dialog: ResourceMonitorDialog, view, series_index: int) -> list[float]:
        state = dialog._chart_states.get(view, {})
        series_objects = state.get("series")
        if not isinstance(series_objects, list) or series_index >= len(series_objects):
            return []
        series = series_objects[series_index]
        return [series.at(index).y() for index in range(series.count())]

    def _chart_axis_max(self, dialog: ResourceMonitorDialog, view) -> float:
        state = dialog._chart_states.get(view, {})
        axis = state.get("axis_y")
        if axis is None:
            return 0.0
        return float(axis.max())

    def _chart_x_axis_max(self, dialog: ResourceMonitorDialog, view) -> float:
        state = dialog._chart_states.get(view, {})
        axis = state.get("axis_x")
        if axis is None:
            return 0.0
        return float(axis.max())

    def _series_x_values(self, dialog: ResourceMonitorDialog, view, series_index: int) -> list[float]:
        state = dialog._chart_states.get(view, {})
        series_objects = state.get("series")
        if not isinstance(series_objects, list) or series_index >= len(series_objects):
            return []
        series = series_objects[series_index]
        return [series.at(index).x() for index in range(series.count())]

    def _series_names(self, dialog: ResourceMonitorDialog, view) -> list[str]:
        state = dialog._chart_states.get(view, {})
        series_objects = state.get("series")
        if not isinstance(series_objects, list):
            return []
        return [str(series.name()) for series in series_objects]

    def _filesystem_item_by_mount(self, dialog: ResourceMonitorDialog, mountpoint: str):
        tree = dialog.filesystems_tree
        for index in range(tree.topLevelItemCount()):
            item = tree.topLevelItem(index)
            if item.text(0).startswith(mountpoint):
                return item
        return None

    def _interface_item_by_name(self, tree, name: str):
        for index in range(tree.topLevelItemCount()):
            item = tree.topLevelItem(index)
            if item.text(0) == name:
                return item
        return None

    def _gpu_section(self, dialog: ResourceMonitorDialog, adapter_id: str):
        return dialog._gpu_adapter_sections[adapter_id]

    def _disk_section(self, dialog: ResourceMonitorDialog, device_key: str):
        return dialog._disk_device_sections[device_key]

    def _network_section(self, dialog: ResourceMonitorDialog, adapter_name: str):
        return dialog._network_adapter_sections[adapter_name]

    def _custom_theme_settings(self) -> AppSettings:
        settings = AppSettings.defaults()
        settings.field_bg = "#f4efe6"
        settings.app_bg_start = "#faf6ee"
        settings.app_bg_end = "#e6ddd0"
        settings.text_color = "#1f2937"
        settings.field_border = "#887a67"
        settings.accent_color = "#0f766e"
        settings.tab_active_bg = "#0f766e"
        settings.tab_active_fg = "#f0fdfa"
        settings.tab_inactive_bg = "#ebe3d6"
        settings.tab_inactive_fg = "#4b5563"
        return settings

    def test_dialog_builds_expected_tabs(self) -> None:
        with patch("snakesh.ui.resource_monitor_dialog.QTimer.singleShot"):
            dialog = ResourceMonitorDialog()
        try:
            QApplication.processEvents()
            self.assertEqual(
                [dialog.tabs.tabText(index) for index in range(dialog.tabs.count())],
                ["Overview", "CPU", "RAM", "Disks", "Network", "GPU", "Processes", "Settings"],
            )
            self.assertIn("cpu", dialog._cards)
            self.assertIn("processes", dialog._cards)
            self.assertEqual(dialog.process_tree.columnCount(), 9)
            self.assertEqual(dialog.filesystems_tree.columnCount(), 9)
            self.assertEqual(dialog.filesystems_tree.headerItem().text(6), "Read")
            self.assertEqual(dialog.filesystems_tree.headerItem().text(7), "Write")
            self.assertEqual(dialog.interfaces_tree.columnCount(), 9)
            self.assertEqual(dialog.interfaces_tree.headerItem().text(1), "IPv4")
            self.assertEqual(dialog.interfaces_tree.headerItem().text(2), "IPv6")
            self.assertEqual(dialog.network_interfaces_tree.columnCount(), 9)
            self.assertIn("at a glance", dialog.overview_intro_label.text().lower())
            self.assertEqual(dialog.disk_home_usage_group.title(), "Home Volume Usage")
            self.assertEqual(dialog.disk_throughput_group.title(), "Disk Throughput")
            self.assertEqual(dialog.network_bandwidth_group.title(), "Network Bandwidth")
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_dialog_restores_and_persists_geometry(self) -> None:
        mock_settings = unittest.mock.Mock()
        mock_settings.value.return_value = None
        with (
            patch("snakesh.ui.resource_monitor_dialog.QSettings", return_value=mock_settings),
            patch("snakesh.ui.resource_monitor_dialog.QTimer.singleShot"),
        ):
            dialog = ResourceMonitorDialog()
            try:
                QApplication.processEvents()
                mock_settings.value.assert_called_once_with("resource_monitor/geometry")

                event = QCloseEvent()
                dialog.closeEvent(event)

                self.assertTrue(mock_settings.setValue.called)
                self.assertEqual(mock_settings.setValue.call_args.args[0], "resource_monitor/geometry")
            finally:
                dialog.deleteLater()
                QApplication.processEvents()

    def test_sample_snapshot_updates_cards_tables_and_gpu_empty_state(self) -> None:
        with patch("snakesh.ui.resource_monitor_dialog.QTimer.singleShot"):
            dialog = ResourceMonitorDialog()
        try:
            dialog._on_sample_ready(self._build_snapshot(gpu_available=False))
            QApplication.processEvents()

            self.assertEqual(dialog._cards["cpu"].value_label.text(), "32%")
            self.assertIn("245", dialog._cards["processes"].value_label.text())
            self.assertEqual(dialog.filesystems_tree.topLevelItemCount(), 1)
            self.assertEqual(dialog.interfaces_tree.topLevelItemCount(), 1)
            self.assertEqual(dialog._cards["network"].value_label.text(), "1 Adapter")
            self.assertEqual(dialog._cards["disk_io"].value_label.text(), "1 Disk")
            self.assertEqual(
                dialog._cards["network"].detail_label.text(),
                "eth0\n Down 32.0 Mbps Up 4.00 Mbps",
            )
            self.assertEqual(
                dialog._cards["disk_io"].detail_label.text(),
                "nvme0n1p5\n Read 1.9 MB/s Write 976.6 KB/s",
            )
            self.assertEqual(self._chart_axis_title(dialog, dialog.network_chart), "Mbps")
            self.assertEqual(dialog._cards["gpu"].value_label.text(), "Unavailable")
            self.assertIn("unavailable", dialog.gpu_chart.chart().title().lower())
            filesystem_row = dialog.filesystems_tree.topLevelItem(0)
            self.assertEqual(filesystem_row.text(6), "1.9 MB/s")
            self.assertEqual(filesystem_row.text(7), "976.6 KB/s")
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_interfaces_render_dual_stack_and_loopback_addresses(self) -> None:
        with patch("snakesh.ui.resource_monitor_dialog.QTimer.singleShot"):
            dialog = ResourceMonitorDialog()
        try:
            snapshot = self._build_snapshot(gpu_available=False)
            snapshot = ResourceMonitorSnapshot(
                sample=snapshot.sample,
                filesystems=snapshot.filesystems,
                disk_devices=snapshot.disk_devices,
                interfaces=[
                    InterfaceBandwidthEntry(
                        name="lo",
                        ipv4_address="127.0.0.1",
                        ipv6_address="::1",
                        is_up=True,
                        speed_mbps=0,
                        recv_bytes_per_sec=0.0,
                        sent_bytes_per_sec=0.0,
                        recv_bytes_total=0,
                        sent_bytes_total=0,
                    )
                ],
                gpu=snapshot.gpu,
            )

            dialog._on_sample_ready(snapshot)
            dialog.tabs.setCurrentWidget(dialog.network_page)
            QApplication.processEvents()

            overview_row = dialog.interfaces_tree.topLevelItem(0)
            network_row = dialog.network_interfaces_tree.topLevelItem(0)
            self.assertEqual(overview_row.text(1), "127.0.0.1")
            self.assertEqual(overview_row.text(2), "::1")
            self.assertEqual(network_row.text(1), "127.0.0.1")
            self.assertEqual(network_row.text(2), "::1")
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_offline_adapters_hide_by_default_and_can_be_shown(self) -> None:
        with patch("snakesh.ui.resource_monitor_dialog.QTimer.singleShot"):
            dialog = ResourceMonitorDialog()
        try:
            dialog._on_sample_ready(self._build_snapshot_with_offline_interface())
            QApplication.processEvents()

            self.assertEqual(dialog._cards["network"].value_label.text(), "1 Adapter")
            self.assertNotIn("wlan0", dialog._cards["network"].detail_label.text())
            self.assertIsNone(self._interface_item_by_name(dialog.interfaces_tree, "wlan0"))

            dialog.tabs.setCurrentWidget(dialog.network_page)
            QApplication.processEvents()

            self.assertIsNone(self._interface_item_by_name(dialog.network_interfaces_tree, "wlan0"))
            self.assertNotIn("wlan0", dialog._network_adapter_sections)

            dialog.show_offline_adapters_check.setChecked(True)
            QApplication.processEvents()

            network_row = self._interface_item_by_name(dialog.network_interfaces_tree, "wlan0")
            assert network_row is not None
            self.assertEqual(network_row.text(3), "Offline")
            self.assertEqual(network_row.text(5), "0 bps")
            offline_section = self._network_section(dialog, "wlan0")
            self.assertEqual(offline_section.cards["receive"].value_label.text(), "0 bps")
            self.assertEqual(offline_section.cards["receive"].detail_label.text(), "Adapter is currently offline")
            self.assertEqual(offline_section.cards["send"].detail_label.text(), "Adapter is currently offline")
            self.assertEqual(offline_section.charts["bandwidth_history"].chart().title(), "Network Bandwidth")
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_settings_tab_persists_resource_monitor_display_and_refresh_preferences(self) -> None:
        saved: list[AppSettings] = []
        settings = AppSettings.defaults()
        with patch("snakesh.ui.resource_monitor_dialog.QTimer.singleShot"):
            dialog = ResourceMonitorDialog(settings=settings, on_settings_changed=saved.append)
        try:
            dialog.tabs.setCurrentWidget(dialog.settings_page)
            QApplication.processEvents()

            dialog.show_offline_adapters_check.setChecked(True)
            dialog.zoom_spin.setValue(125)
            dialog.sample_refresh_spin.setValue(2000)
            dialog.process_refresh_spin.setValue(6000)
            dialog.history_minutes_spin.setValue(5)
            QApplication.processEvents()

            self.assertTrue(saved)
            self.assertTrue(saved[-1].resource_monitor_show_offline_adapters)
            self.assertEqual(saved[-1].resource_monitor_zoom_percent, 125)
            self.assertEqual(saved[-1].resource_monitor_sample_refresh_ms, 2000)
            self.assertEqual(saved[-1].resource_monitor_process_refresh_ms, 6000)
            self.assertEqual(saved[-1].resource_monitor_history_minutes, 5)
            self.assertEqual(dialog._sample_timer.interval(), 2000)
            self.assertEqual(dialog._max_history, 150)
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_control_mouse_wheel_updates_resource_monitor_zoom_setting(self) -> None:
        class _WheelProbe:
            def __init__(self) -> None:
                self.accepted = False

            def type(self):
                return resource_monitor_dialog_module.QEvent.Type.Wheel

            def modifiers(self):
                return Qt.ControlModifier

            def angleDelta(self):
                return QPoint(0, 120)

            def accept(self) -> None:
                self.accepted = True

        saved: list[AppSettings] = []
        with patch("snakesh.ui.resource_monitor_dialog.QTimer.singleShot"):
            dialog = ResourceMonitorDialog(on_settings_changed=saved.append)
        try:
            event = _WheelProbe()
            dialog.wheelEvent(event)
            QApplication.processEvents()

            self.assertTrue(event.accepted)
            self.assertEqual(dialog._settings.resource_monitor_zoom_percent, 105)
            self.assertEqual(saved[-1].resource_monitor_zoom_percent, 105)
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_control_mouse_wheel_over_child_is_consumed_without_scrolling(self) -> None:
        class _WheelProbe:
            def __init__(self) -> None:
                self.accepted = False

            def type(self):
                return resource_monitor_dialog_module.QEvent.Type.Wheel

            def modifiers(self):
                return Qt.ControlModifier

            def angleDelta(self):
                return QPoint(0, -120)

            def accept(self) -> None:
                self.accepted = True

        saved: list[AppSettings] = []
        with patch("snakesh.ui.resource_monitor_dialog.QTimer.singleShot"):
            dialog = ResourceMonitorDialog(on_settings_changed=saved.append)
        try:
            dialog.tabs.setCurrentWidget(dialog.network_page)
            QApplication.processEvents()

            event = _WheelProbe()
            handled = dialog.eventFilter(dialog.network_detail_chart, event)

            self.assertTrue(handled)
            self.assertTrue(event.accepted)
            self.assertEqual(dialog._settings.resource_monitor_zoom_percent, 95)
            self.assertEqual(saved[-1].resource_monitor_zoom_percent, 95)
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_resource_monitor_zoom_scales_card_and_chart_geometry(self) -> None:
        settings = AppSettings.defaults()
        settings.resource_monitor_zoom_percent = 75
        with patch("snakesh.ui.resource_monitor_dialog.QTimer.singleShot"):
            dialog = ResourceMonitorDialog(settings=settings)
        try:
            dialog.resize(1200, 900)
            dialog.show()
            QApplication.processEvents()

            card = dialog._cards["cpu"]
            self.assertEqual(
                card.minimumHeight(),
                round(resource_monitor_dialog_module._METRIC_CARD_MIN_HEIGHT * 0.75),
            )
            self.assertEqual(
                card.value_label.minimumWidth(),
                round(resource_monitor_dialog_module._METRIC_CARD_VALUE_MIN_WIDTH * 0.75),
            )
            self.assertEqual(
                card.detail_label.minimumWidth(),
                round(resource_monitor_dialog_module._METRIC_CARD_VALUE_MIN_WIDTH * 0.75),
            )
            self.assertEqual(
                card.maximumWidth(),
                round(resource_monitor_dialog_module._METRIC_CARD_BASE_WIDTH * 0.75),
            )
            self.assertEqual(card.maximumHeight(), resource_monitor_dialog_module._QT_WIDGET_MAX_SIZE)
            self.assertEqual(dialog.cpu_chart.minimumHeight(), round(260 * 0.75))
            self.assertEqual(dialog.cpu_chart.maximumWidth(), resource_monitor_dialog_module._QT_WIDGET_MAX_SIZE)
            self.assertLess(dialog.cpu_chart.maximumHeight(), resource_monitor_dialog_module._QT_WIDGET_MAX_SIZE)
            small_chart_hint = dialog.cpu_chart.sizeHint()

            dialog.zoom_spin.setValue(150)
            QApplication.processEvents()

            self.assertEqual(
                card.minimumHeight(),
                round(resource_monitor_dialog_module._METRIC_CARD_MIN_HEIGHT * 1.5),
            )
            self.assertEqual(
                card.value_label.minimumWidth(),
                round(resource_monitor_dialog_module._METRIC_CARD_VALUE_MIN_WIDTH * 1.5),
            )
            self.assertEqual(card.maximumWidth(), resource_monitor_dialog_module._QT_WIDGET_MAX_SIZE)
            self.assertEqual(card.maximumHeight(), resource_monitor_dialog_module._QT_WIDGET_MAX_SIZE)
            self.assertEqual(dialog.cpu_chart.minimumHeight(), round(260 * 1.5))
            self.assertEqual(dialog.cpu_chart.maximumWidth(), resource_monitor_dialog_module._QT_WIDGET_MAX_SIZE)
            self.assertEqual(dialog.cpu_chart.maximumHeight(), resource_monitor_dialog_module._QT_WIDGET_MAX_SIZE)
            self.assertGreaterEqual(dialog.cpu_chart.sizeHint().width(), small_chart_hint.width())
            self.assertGreater(dialog.cpu_chart.sizeHint().height(), small_chart_hint.height())
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_zoomed_overview_network_card_can_grow_for_all_adapter_lines(self) -> None:
        settings = AppSettings.defaults()
        settings.resource_monitor_zoom_percent = 75
        with patch("snakesh.ui.resource_monitor_dialog.QTimer.singleShot"):
            dialog = ResourceMonitorDialog(settings=settings)
        try:
            snapshot = self._build_snapshot(gpu_available=False)
            snapshot = ResourceMonitorSnapshot(
                sample=snapshot.sample,
                filesystems=snapshot.filesystems,
                disk_devices=snapshot.disk_devices,
                interfaces=[
                    InterfaceBandwidthEntry(
                        name="eth0",
                        is_up=True,
                        recv_bytes_per_sec=1000.0,
                        sent_bytes_per_sec=500.0,
                        recv_bytes_total=1_000_000,
                        sent_bytes_total=500_000,
                        ipv4_address="192.0.2.2",
                        ipv6_address="",
                        speed_mbps=1000,
                    ),
                    InterfaceBandwidthEntry(
                        name="wlan0",
                        is_up=True,
                        recv_bytes_per_sec=2000.0,
                        sent_bytes_per_sec=750.0,
                        recv_bytes_total=2_000_000,
                        sent_bytes_total=750_000,
                        ipv4_address="192.0.2.3",
                        ipv6_address="",
                        speed_mbps=1000,
                    ),
                    InterfaceBandwidthEntry(
                        name="lo",
                        is_up=True,
                        recv_bytes_per_sec=0.0,
                        sent_bytes_per_sec=0.0,
                        recv_bytes_total=100,
                        sent_bytes_total=100,
                        ipv4_address="127.0.0.1",
                        ipv6_address="::1",
                        speed_mbps=0,
                    ),
                ],
                gpu=snapshot.gpu,
                errors=snapshot.errors,
            )

            dialog._update_cards(snapshot)
            QApplication.processEvents()

            card = dialog._cards["network"]
            detail = card.detail_label.text()
            self.assertIn("eth0", detail)
            self.assertIn("wlan0", detail)
            self.assertIn("lo", detail)
            self.assertEqual(card.maximumHeight(), resource_monitor_dialog_module._QT_WIDGET_MAX_SIZE)
            self.assertGreater(card.sizeHint().height(), card.minimumHeight())
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_resource_monitor_zoom_scales_process_table_and_keeps_sections_compact(self) -> None:
        settings = AppSettings.defaults()
        settings.resource_monitor_zoom_percent = 75
        with patch("snakesh.ui.resource_monitor_dialog.QTimer.singleShot"):
            dialog = ResourceMonitorDialog(settings=settings)
        try:
            app_font_size = QApplication.font().pointSizeF()
            if app_font_size > 0:
                self.assertLess(dialog.process_tree.font().pointSizeF(), app_font_size)
                self.assertLess(dialog.process_tree.header().font().pointSizeF(), app_font_size)
            self.assertEqual(dialog.cpu_device_section.group.sizePolicy().verticalPolicy(), QSizePolicy.Maximum)
            self.assertEqual(dialog.ram_section.group.sizePolicy().verticalPolicy(), QSizePolicy.Maximum)
            self.assertIs(dialog.ram_usage_chart, dialog.ram_section.charts["usage"])
            self.assertIs(dialog.ram_bytes_chart, dialog.ram_section.charts["bytes"])
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_network_tab_moves_loopback_interfaces_to_bottom(self) -> None:
        settings = AppSettings.defaults()
        settings.resource_monitor_show_offline_adapters = True
        with patch("snakesh.ui.resource_monitor_dialog.QTimer.singleShot"):
            dialog = ResourceMonitorDialog(settings=settings)
        try:
            snapshot = self._build_snapshot(gpu_available=False)
            snapshot = ResourceMonitorSnapshot(
                sample=snapshot.sample,
                filesystems=snapshot.filesystems,
                disk_devices=snapshot.disk_devices,
                interfaces=[
                    InterfaceBandwidthEntry(
                        name="lo",
                        ipv4_address="127.0.0.1",
                        ipv6_address="::1",
                        is_up=True,
                        speed_mbps=0,
                        recv_bytes_per_sec=0.0,
                        sent_bytes_per_sec=0.0,
                        recv_bytes_total=0,
                        sent_bytes_total=0,
                    ),
                    snapshot.interfaces[0],
                    InterfaceBandwidthEntry(
                        name="wlan0",
                        ipv4_address="192.0.2.55",
                        ipv6_address="2001:db8::55",
                        is_up=False,
                        speed_mbps=866,
                        recv_bytes_per_sec=0.0,
                        sent_bytes_per_sec=0.0,
                        recv_bytes_total=0,
                        sent_bytes_total=0,
                    ),
                ],
                gpu=snapshot.gpu,
            )

            dialog.tabs.setCurrentWidget(dialog.network_page)
            QApplication.processEvents()
            dialog._on_sample_ready(snapshot)
            QApplication.processEvents()

            self.assertEqual(dialog._network_adapter_section_order, ["eth0", "lo", "wlan0"])
            self.assertEqual(dialog.network_interfaces_tree.topLevelItem(0).text(0), "eth0")
            self.assertEqual(dialog.network_interfaces_tree.topLevelItem(1).text(0), "lo")
            self.assertEqual(dialog.network_interfaces_tree.topLevelItem(2).text(0), "wlan0")
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_gpu_cards_render_detected_state_when_metrics_are_partial(self) -> None:
        with patch("snakesh.ui.resource_monitor_dialog.QTimer.singleShot"):
            dialog = ResourceMonitorDialog()
        try:
            snapshot = self._build_snapshot(gpu_available=True)
            partial_gpu = replace(
                snapshot.gpu,
                name="2 GPUs",
                gpu_count=2,
                memory_used_bytes=None,
                memory_total_bytes=None,
                memory_percent=None,
                temperature_c=None,
                has_memory=False,
                has_temperature=False,
                adapters=[
                    GpuAdapterSample(
                        id="0000:01:00.0",
                        vendor="NVIDIA",
                        name="RTX Test",
                        adapter_index=0,
                        utilization_percent=58.0,
                    ),
                    GpuAdapterSample(
                        id="0000:02:00.0",
                        vendor="AMD",
                        name="RX Test",
                        adapter_index=1,
                    ),
                ],
                message="Some GPU metrics are unavailable on this system.",
            )
            snapshot = ResourceMonitorSnapshot(
                sample=snapshot.sample,
                filesystems=snapshot.filesystems,
                disk_devices=snapshot.disk_devices,
                interfaces=snapshot.interfaces,
                gpu=partial_gpu,
            )

            dialog._on_sample_ready(snapshot)
            dialog.tabs.setCurrentWidget(dialog.gpu_page)
            QApplication.processEvents()

            self.assertEqual(dialog._cards["gpu"].value_label.text(), "2 GPUs")
            self.assertEqual(
                dialog._cards["gpu"].detail_label.text(),
                "RTX Test\n Util 58% VRAM -- Temp --\nAMD RX Test\n Util -- VRAM -- Temp --",
            )
            self.assertEqual(len(dialog._gpu_adapter_sections), 2)
            first_section = self._gpu_section(dialog, "0000:01:00.0")
            second_section = self._gpu_section(dialog, "0000:02:00.0")
            self.assertEqual(first_section.cards["usage"].value_label.text(), "58.0%")
            self.assertEqual(first_section.cards["memory"].value_label.text(), "--")
            self.assertEqual(first_section.cards["temperature"].value_label.text(), "--")
            self.assertEqual(first_section.cards["adapter"].value_label.text(), "Partial")
            self.assertEqual(second_section.cards["usage"].value_label.text(), "--")
            self.assertEqual(second_section.cards["adapter"].value_label.text(), "Detected")
            self.assertEqual(first_section.cards["memory"].detail_label.text(), "VRAM telemetry unavailable.")
            self.assertEqual(first_section.activity_chart.chart().title(), "GPU Activity")
            self.assertIn("temperature telemetry is unavailable", first_section.status_label.text().lower())
            self.assertIn("telemetry are unavailable", second_section.activity_chart.chart().title().lower())
            self.assertIn("temperature telemetry is unavailable", second_section.temperature_chart.chart().title().lower())
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_multi_gpu_snapshot_renders_one_gpu_section_per_adapter(self) -> None:
        with patch("snakesh.ui.resource_monitor_dialog.QTimer.singleShot"):
            dialog = ResourceMonitorDialog()
        try:
            dialog.tabs.setCurrentWidget(dialog.gpu_page)
            QApplication.processEvents()

            dialog._on_sample_ready(self._build_multi_gpu_snapshot())
            QApplication.processEvents()

            self.assertEqual(list(dialog._gpu_adapter_sections), ["gpu-intel-0", "gpu-nvidia-1"])
            intel_section = self._gpu_section(dialog, "gpu-intel-0")
            nvidia_section = self._gpu_section(dialog, "gpu-nvidia-1")
            self.assertEqual(intel_section.group.title(), "Intel(R) UHD Graphics")
            self.assertEqual(nvidia_section.group.title(), "NVIDIA GeForce RTX 3050 Ti Laptop GPU")
            self.assertEqual(intel_section.cards["usage"].value_label.text(), "12.0%")
            self.assertEqual(intel_section.cards["adapter"].value_label.text(), "Partial")
            self.assertEqual(nvidia_section.cards["memory"].detail_label.text(), "25.0% used")
            self.assertEqual(nvidia_section.cards["adapter"].value_label.text(), "Ready")
            self.assertEqual(intel_section.activity_chart.chart().title(), "GPU Activity")
            self.assertEqual(nvidia_section.activity_chart.chart().title(), "GPU Activity")
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_disks_and_network_tabs_render_one_section_per_device(self) -> None:
        with patch("snakesh.ui.resource_monitor_dialog.QTimer.singleShot"):
            dialog = ResourceMonitorDialog()
        try:
            snapshot = self._build_multi_device_snapshot()
            dialog._on_sample_ready(snapshot)
            dialog.tabs.setCurrentWidget(dialog.disks_page)
            QApplication.processEvents()

            self.assertEqual(list(dialog._disk_device_sections), ["disk-home", "disk-data"])
            home_disk = self._disk_section(dialog, "disk-home")
            data_disk = self._disk_section(dialog, "disk-data")
            self.assertEqual(home_disk.group.title(), "nvme0n1p5")
            self.assertEqual(data_disk.group.title(), "sdb1")
            self.assertEqual(home_disk.cards["volume"].value_label.text(), "372.5 GB / 931.3 GB")
            self.assertEqual(data_disk.cards["read"].value_label.text(), "244.1 KB/s")
            self.assertEqual(data_disk.cards["write"].detail_label.text(), "Since open 732.4 KB")
            self.assertEqual(home_disk.charts["usage_history"].chart().title(), "Volume Usage")
            self.assertEqual(data_disk.charts["throughput_history"].chart().title(), "Disk Throughput")
            self.assertEqual(self._chart_axis_title(dialog, data_disk.charts["throughput_history"]), "KB/s")

            dialog.tabs.setCurrentWidget(dialog.network_page)
            QApplication.processEvents()

            self.assertEqual(list(dialog._network_adapter_sections), ["eth0", "wlan0"])
            eth0 = self._network_section(dialog, "eth0")
            wlan0 = self._network_section(dialog, "wlan0")
            self.assertEqual(eth0.group.title(), "eth0 (192.0.2.20)")
            self.assertEqual(eth0.cards["receive"].value_label.text(), "32.0 Mbps")
            self.assertEqual(wlan0.cards["receive"].value_label.text(), "512 Kbps")
            self.assertEqual(wlan0.cards["total_send"].value_label.text(), "683.6 KB")
            self.assertEqual(eth0.charts["bandwidth_history"].chart().title(), "Network Bandwidth")
            self.assertEqual(self._chart_axis_title(dialog, eth0.charts["bandwidth_history"]), "Mbps")
            self.assertEqual(self._chart_axis_title(dialog, wlan0.charts["bandwidth_history"]), "Kbps")
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_disks_tab_preserves_scroll_position_across_refreshes_with_many_disks(self) -> None:
        with patch("snakesh.ui.resource_monitor_dialog.QTimer.singleShot"):
            dialog = ResourceMonitorDialog()
        try:
            dialog.resize(1180, 520)
            dialog.show()
            dialog.tabs.setCurrentWidget(dialog.disks_page)
            QApplication.processEvents()

            base_snapshot = self._build_snapshot(gpu_available=False)
            filesystems = [
                FilesystemEntry(
                    device=f"/dev/disk{index}",
                    mountpoint=f"/mnt/disk{index}",
                    filesystem_type="ext4",
                    used_bytes=100_000_000_000 + (index * 1_000_000_000),
                    total_bytes=500_000_000_000,
                    free_bytes=400_000_000_000 - (index * 1_000_000_000),
                    usage_percent=20.0 + index,
                    is_home=index == 0,
                    disk_device_key=f"disk-{index}",
                )
                for index in range(7)
            ]
            disk_devices = [
                DiskDeviceSample(
                    key=f"disk-{index}",
                    display_label=f"sd{chr(ord('a') + index)}",
                    read_bytes_per_sec=100_000.0 + (index * 10_000.0),
                    write_bytes_per_sec=50_000.0 + (index * 5_000.0),
                    read_bytes_since_open=1_000_000 + index,
                    write_bytes_since_open=500_000 + index,
                )
                for index in range(7)
            ]
            first_snapshot = ResourceMonitorSnapshot(
                sample=base_snapshot.sample,
                filesystems=filesystems,
                disk_devices=disk_devices,
                interfaces=base_snapshot.interfaces,
                gpu=base_snapshot.gpu,
            )
            second_snapshot = ResourceMonitorSnapshot(
                sample=replace(base_snapshot.sample, timestamp_monotonic=base_snapshot.sample.timestamp_monotonic + 1.0),
                filesystems=filesystems,
                disk_devices=[
                    replace(
                        disk_device,
                        read_bytes_per_sec=disk_device.read_bytes_per_sec + 8_192.0,
                        write_bytes_per_sec=disk_device.write_bytes_per_sec + 4_096.0,
                    )
                    for disk_device in disk_devices
                ],
                interfaces=base_snapshot.interfaces,
                gpu=base_snapshot.gpu,
            )

            dialog._on_sample_ready(first_snapshot)
            QApplication.processEvents()
            scrollbar = dialog.disks_scroll_area.verticalScrollBar()
            self.assertGreater(scrollbar.maximum(), 0)
            scrollbar.setValue(max(1, scrollbar.maximum() // 2))
            scroll_value = scrollbar.value()

            dialog._on_sample_ready(second_snapshot)
            QApplication.processEvents()

            self.assertEqual(scrollbar.value(), scroll_value)
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_disks_tab_keeps_existing_section_order_when_sample_order_changes(self) -> None:
        with patch("snakesh.ui.resource_monitor_dialog.QTimer.singleShot"):
            dialog = ResourceMonitorDialog()
        try:
            snapshot = self._build_multi_device_snapshot()
            dialog.tabs.setCurrentWidget(dialog.disks_page)
            QApplication.processEvents()

            dialog._on_sample_ready(snapshot)
            QApplication.processEvents()
            first_order = list(dialog._disk_device_section_order)

            reordered_snapshot = ResourceMonitorSnapshot(
                sample=replace(snapshot.sample, timestamp_monotonic=snapshot.sample.timestamp_monotonic + 1.0),
                filesystems=snapshot.filesystems,
                disk_devices=list(reversed(snapshot.disk_devices)),
                interfaces=snapshot.interfaces,
                gpu=snapshot.gpu,
            )
            dialog._on_sample_ready(reordered_snapshot)
            QApplication.processEvents()

            self.assertEqual(dialog._disk_device_section_order, first_order)
            self.assertEqual(list(dialog._disk_device_sections), first_order)
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_overview_device_cards_render_indented_device_blocks(self) -> None:
        with patch("snakesh.ui.resource_monitor_dialog.QTimer.singleShot"):
            dialog = ResourceMonitorDialog()
        try:
            dialog._on_sample_ready(self._build_multi_device_snapshot())
            QApplication.processEvents()

            self.assertEqual(dialog._cards["disk"].value_label.text(), "2 Disks")
            self.assertIn("nvme0n1p5\n 40.0% used, 372.5 GB / 931.3 GB, Free 558.8 GB", dialog._cards["disk"].detail_label.text())
            self.assertIn("sdb1\n 40.0% used, 186.3 GB / 465.7 GB, Free 279.4 GB", dialog._cards["disk"].detail_label.text())
            self.assertEqual(dialog._cards["network"].value_label.text(), "2 Adapters")
            self.assertIn("eth0\n Down 32.0 Mbps Up 4.00 Mbps", dialog._cards["network"].detail_label.text())
            self.assertIn("wlan0\n Down 512 Kbps Up 256 Kbps", dialog._cards["network"].detail_label.text())
            self.assertEqual(dialog._cards["gpu"].value_label.text(), "2 GPUs")
            self.assertIn("Intel(R) UHD Graphics\n Util 12% VRAM 12% Temp --", dialog._cards["gpu"].detail_label.text())
            self.assertIn(
                "NVIDIA GeForce RTX 3050 Ti Laptop GPU\n Util 46% VRAM 25% Temp 61 C",
                dialog._cards["gpu"].detail_label.text(),
            )
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_gpu_adapter_charts_are_reused_for_stable_adapter_ids(self) -> None:
        with patch("snakesh.ui.resource_monitor_dialog.QTimer.singleShot"):
            dialog = ResourceMonitorDialog()
        try:
            dialog.tabs.setCurrentWidget(dialog.gpu_page)
            QApplication.processEvents()

            first_snapshot = self._build_multi_gpu_snapshot()
            second_gpu = replace(
                first_snapshot.gpu,
                adapters=[
                    replace(first_snapshot.gpu.adapters[0], utilization_percent=18.0),
                    replace(first_snapshot.gpu.adapters[1], utilization_percent=61.0, temperature_c=64.0),
                ],
            )
            second_snapshot = ResourceMonitorSnapshot(
                sample=first_snapshot.sample,
                filesystems=first_snapshot.filesystems,
                disk_devices=first_snapshot.disk_devices,
                interfaces=first_snapshot.interfaces,
                gpu=second_gpu,
            )

            dialog._on_sample_ready(first_snapshot)
            QApplication.processEvents()
            intel_chart = self._gpu_section(dialog, "gpu-intel-0").activity_chart.chart()
            nvidia_chart = self._gpu_section(dialog, "gpu-nvidia-1").activity_chart.chart()

            dialog._on_sample_ready(second_snapshot)
            QApplication.processEvents()

            self.assertIs(self._gpu_section(dialog, "gpu-intel-0").activity_chart.chart(), intel_chart)
            self.assertIs(self._gpu_section(dialog, "gpu-nvidia-1").activity_chart.chart(), nvidia_chart)
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_overview_gpu_chart_uses_per_adapter_series(self) -> None:
        with patch("snakesh.ui.resource_monitor_dialog.QTimer.singleShot"):
            dialog = ResourceMonitorDialog()
        try:
            dialog._on_sample_ready(self._build_multi_gpu_snapshot())
            QApplication.processEvents()

            self.assertEqual(
                self._series_names(dialog, dialog.gpu_chart),
                [
                    "Intel(R) UHD Graphics Util",
                    "Intel(R) UHD Graphics VRAM",
                    "NVIDIA GeForce RTX 3050 Ti Laptop GPU Util",
                    "NVIDIA GeForce RTX 3050 Ti Laptop GPU VRAM",
                ],
            )
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_linux_intel_gpu_usage_renders_when_temperature_is_unavailable(self) -> None:
        with patch("snakesh.ui.resource_monitor_dialog.QTimer.singleShot"):
            dialog = ResourceMonitorDialog()
        try:
            snapshot = self._build_snapshot(gpu_available=False)
            gpu = GpuSample(
                available=True,
                detected=True,
                name="2 GPUs",
                gpu_count=2,
                utilization_percent=35.5,
                memory_used_bytes=2_000_000_000,
                memory_total_bytes=8_000_000_000,
                memory_percent=25.0,
                temperature_c=61.0,
                has_utilization=True,
                has_memory=True,
                has_temperature=True,
                adapters=[
                    GpuAdapterSample(
                        id="0000:00:02.0",
                        vendor="Intel",
                        name="Intel UHD Graphics",
                        adapter_index=0,
                        backend="intel_gpu_top",
                        utilization_percent=25.0,
                    ),
                    GpuAdapterSample(
                        id="0000:01:00.0",
                        vendor="NVIDIA",
                        name="NVIDIA GeForce RTX 3050 Ti",
                        adapter_index=1,
                        backend="nvidia-smi",
                        utilization_percent=46.0,
                        memory_used_bytes=2_000_000_000,
                        memory_total_bytes=8_000_000_000,
                        temperature_c=61.0,
                    ),
                ],
                message="Some GPU metrics are unavailable on this system.",
            )
            snapshot = ResourceMonitorSnapshot(
                sample=snapshot.sample,
                filesystems=snapshot.filesystems,
                disk_devices=snapshot.disk_devices,
                interfaces=snapshot.interfaces,
                gpu=gpu,
            )

            dialog._on_sample_ready(snapshot)
            dialog.tabs.setCurrentWidget(dialog.gpu_page)
            QApplication.processEvents()

            intel_section = self._gpu_section(dialog, "0000:00:02.0")
            self.assertEqual(intel_section.cards["usage"].value_label.text(), "25.0%")
            self.assertEqual(intel_section.cards["temperature"].value_label.text(), "--")
            self.assertEqual(intel_section.activity_chart.chart().title(), "GPU Activity")
            self.assertIn("temperature telemetry is unavailable", intel_section.status_label.text().lower())
            self.assertIn("Intel UHD Graphics\n Util 25% VRAM -- Temp --", dialog._cards["gpu"].detail_label.text())
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_linux_intel_fdinfo_shared_memory_does_not_render_as_vram_percent(self) -> None:
        with patch("snakesh.ui.resource_monitor_dialog.QTimer.singleShot"):
            dialog = ResourceMonitorDialog()
        try:
            snapshot = self._build_snapshot(gpu_available=False)
            shared_bytes = 304 * 1024 * 1024
            gpu = GpuSample(
                available=True,
                detected=True,
                name="Intel UHD Graphics",
                gpu_count=1,
                utilization_percent=12.0,
                memory_used_bytes=shared_bytes,
                memory_total_bytes=None,
                memory_percent=None,
                has_utilization=True,
                has_memory=True,
                adapters=[
                    GpuAdapterSample(
                        id="0000:00:02.0",
                        vendor="Intel",
                        name="Intel UHD Graphics",
                        adapter_index=0,
                        backend="linux-drm-fdinfo",
                        utilization_percent=12.0,
                        memory_used_bytes=shared_bytes,
                        memory_total_bytes=shared_bytes,
                        memory_total_is_capacity=False,
                        memory_kind="shared",
                    )
                ],
                message="Some GPU metrics are unavailable on this system.",
            )
            snapshot = ResourceMonitorSnapshot(
                sample=snapshot.sample,
                filesystems=snapshot.filesystems,
                disk_devices=snapshot.disk_devices,
                interfaces=snapshot.interfaces,
                gpu=gpu,
            )

            dialog._on_sample_ready(snapshot)
            dialog.tabs.setCurrentWidget(dialog.gpu_page)
            QApplication.processEvents()

            intel_section = self._gpu_section(dialog, "0000:00:02.0")
            self.assertIsNone(_gpu_adapter_memory_percent(gpu.adapters[0]))
            self.assertEqual(intel_section.cards["memory"].title_label.text(), "Shared Memory")
            self.assertEqual(intel_section.cards["memory"].value_label.text(), "304.0 MB")
            self.assertIn("capacity unavailable", intel_section.cards["memory"].detail_label.text())
            self.assertIn("Intel UHD Graphics\n Util 12% Shared 304.0 MB Temp --", dialog._cards["gpu"].detail_label.text())
            self.assertEqual(self._series_names(dialog, intel_section.activity_chart), ["GPU"])
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_gpu_adapter_detail_text_avoids_conflicting_vendor_prefixes(self) -> None:
        with patch("snakesh.ui.resource_monitor_dialog.QTimer.singleShot"):
            dialog = ResourceMonitorDialog()
        try:
            snapshot = self._build_snapshot(gpu_available=True)
            conflicting_gpu = replace(
                snapshot.gpu,
                name="2 GPUs",
                gpu_count=2,
                adapters=[
                    GpuAdapterSample(
                        id="gpu-intel-0",
                        vendor="AMD",
                        name="Intel(R) UHD Graphics",
                        adapter_index=0,
                    ),
                    GpuAdapterSample(
                        id="gpu-nvidia-1",
                        vendor="NVIDIA",
                        name="NVIDIA GeForce RTX 3050 Ti Laptop GPU",
                        adapter_index=1,
                    ),
                ],
                message="Some GPU metrics are unavailable on this system.",
            )
            conflicting_snapshot = ResourceMonitorSnapshot(
                sample=snapshot.sample,
                filesystems=snapshot.filesystems,
                disk_devices=snapshot.disk_devices,
                interfaces=snapshot.interfaces,
                gpu=conflicting_gpu,
            )

            dialog._on_sample_ready(conflicting_snapshot)
            dialog.tabs.setCurrentWidget(dialog.gpu_page)
            QApplication.processEvents()

            detail_text = dialog._cards["gpu"].detail_label.text()
            self.assertIn("Intel(R) UHD Graphics", detail_text)
            self.assertNotIn("AMD Intel", detail_text)
            self.assertEqual(self._gpu_section(dialog, "gpu-intel-0").group.title(), "Intel(R) UHD Graphics")
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_process_snapshot_updates_tree_and_search_filter(self) -> None:
        with patch("snakesh.ui.resource_monitor_dialog.QTimer.singleShot"):
            dialog = ResourceMonitorDialog()
        try:
            dialog.tabs.setCurrentWidget(dialog.processes_page)
            QApplication.processEvents()
            dialog._on_process_snapshot_ready(self._build_process_snapshot())
            QApplication.processEvents()

            self.assertEqual(dialog.process_tree.topLevelItemCount(), 2)
            dialog.process_search_input.setText("sshd")
            QApplication.processEvents()
            self.assertTrue(dialog.process_tree.topLevelItem(0).isHidden() or dialog.process_tree.topLevelItem(1).isHidden())
            visible_names = [
                dialog.process_tree.topLevelItem(index).text(0)
                for index in range(dialog.process_tree.topLevelItemCount())
                if not dialog.process_tree.topLevelItem(index).isHidden()
            ]
            self.assertEqual(visible_names, ["sshd"])
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_process_snapshot_defaults_to_cpu_descending_sort(self) -> None:
        with patch("snakesh.ui.resource_monitor_dialog.QTimer.singleShot"):
            dialog = ResourceMonitorDialog()
        try:
            dialog.tabs.setCurrentWidget(dialog.processes_page)
            QApplication.processEvents()
            snapshot = ProcessInventorySnapshot(
                entries=[
                    ProcessEntry(
                        pid=200,
                        name="sshd",
                        cpu_percent=0.4,
                        memory_rss_bytes=20_000_000,
                        threads=2,
                        user="root",
                        status="sleeping",
                        started_at=1_699_000_000.0,
                        command="sshd -D",
                    ),
                    ProcessEntry(
                        pid=100,
                        name="python",
                        cpu_percent=21.5,
                        memory_rss_bytes=700_000_000,
                        threads=10,
                        user="alice",
                        status="running",
                        started_at=1_700_000_000.0,
                        command="python app.py",
                    ),
                ],
                total_threads=12,
                collected_at=12.0,
            )

            dialog._on_process_snapshot_ready(snapshot)
            QApplication.processEvents()

            self.assertEqual(dialog.process_tree.sortColumn(), 2)
            self.assertEqual(dialog.process_tree.header().sortIndicatorOrder(), Qt.DescendingOrder)
            self.assertEqual(dialog.process_tree.topLevelItem(0).text(0), "python")
            self.assertEqual(dialog.process_tree.topLevelItem(1).text(0), "sshd")
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_process_snapshot_preserves_memory_descending_sort(self) -> None:
        with patch("snakesh.ui.resource_monitor_dialog.QTimer.singleShot"):
            dialog = ResourceMonitorDialog()
        try:
            dialog.tabs.setCurrentWidget(dialog.processes_page)
            QApplication.processEvents()
            dialog._on_process_snapshot_ready(self._build_process_snapshot())
            QApplication.processEvents()
            dialog.process_tree.sortItems(3, Qt.DescendingOrder)

            snapshot = ProcessInventorySnapshot(
                entries=[
                    ProcessEntry(
                        pid=300,
                        name="tiny",
                        cpu_percent=50.0,
                        memory_rss_bytes=1_000_000,
                        threads=1,
                        user="alice",
                        status="running",
                        started_at=1_700_000_100.0,
                        command="tiny",
                    ),
                    ProcessEntry(
                        pid=400,
                        name="large",
                        cpu_percent=0.1,
                        memory_rss_bytes=900_000_000,
                        threads=4,
                        user="alice",
                        status="sleeping",
                        started_at=1_700_000_200.0,
                        command="large",
                    ),
                ],
                total_threads=5,
                collected_at=13.0,
            )

            dialog._on_process_snapshot_ready(snapshot)
            QApplication.processEvents()

            self.assertEqual(dialog.process_tree.sortColumn(), 3)
            self.assertEqual(dialog.process_tree.header().sortIndicatorOrder(), Qt.DescendingOrder)
            self.assertEqual(dialog.process_tree.topLevelItem(0).text(0), "large")
            self.assertEqual(dialog.process_tree.topLevelItem(1).text(0), "tiny")
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_runtime_settings_sync_exception_is_logged_and_shown(self) -> None:
        with patch("snakesh.ui.resource_monitor_dialog.QTimer.singleShot"):
            dialog = ResourceMonitorDialog()
        try:
            updated = AppSettings.from_dict(dialog._settings.to_dict())
            updated.resource_monitor_zoom_percent = 125
            with (
                self.assertLogs("snakesh.ui.resource_monitor_dialog", level="ERROR"),
                patch.object(dialog, "_apply_resource_monitor_preferences", side_effect=RuntimeError("boom")),
            ):
                dialog.apply_runtime_settings(updated)

            self.assertIn("Resource Monitor UI error", dialog.settings_status_label.text())
            self.assertIn("boom", dialog.settings_status_label.text())
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_process_refresh_interval_is_throttled_when_processes_tab_is_hidden(self) -> None:
        with patch("snakesh.ui.resource_monitor_dialog.QTimer.singleShot"):
            dialog = ResourceMonitorDialog()
        try:
            self.assertEqual(dialog._process_timer.interval(), 5000)

            dialog.tabs.setCurrentWidget(dialog.processes_page)
            QApplication.processEvents()
            self.assertEqual(dialog._process_timer.interval(), 4000)

            dialog.tabs.setCurrentWidget(dialog.overview_page)
            QApplication.processEvents()
            self.assertEqual(dialog._process_timer.interval(), 5000)
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_trigger_process_refresh_uses_counts_when_processes_tab_is_hidden(self) -> None:
        with patch("snakesh.ui.resource_monitor_dialog.QTimer.singleShot"):
            dialog = ResourceMonitorDialog()
        try:
            with (
                patch.object(dialog, "_start_process_count_refresh", return_value=True) as mock_counts,
                patch.object(dialog, "_start_process_refresh", return_value=True) as mock_full,
            ):
                dialog.tabs.setCurrentWidget(dialog.overview_page)
                dialog._trigger_process_refresh()
                mock_counts.assert_called_once_with()
                mock_full.assert_not_called()

                mock_counts.reset_mock()
                dialog.tabs.setCurrentWidget(dialog.processes_page)
                QApplication.processEvents()
                mock_full.reset_mock()
                dialog._trigger_process_refresh()
                mock_full.assert_called_once_with()
                mock_counts.assert_not_called()
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_hidden_overview_updates_are_deferred_until_tab_is_visible(self) -> None:
        with patch("snakesh.ui.resource_monitor_dialog.QTimer.singleShot"):
            dialog = ResourceMonitorDialog()
        try:
            dialog.tabs.setCurrentWidget(dialog.processes_page)
            QApplication.processEvents()

            snapshot = self._build_snapshot(gpu_available=False)
            with patch.dict(
                dialog._sample_page_updaters,
                {dialog.overview_page: unittest.mock.Mock()},
            ) as patched_updaters:
                mock_apply = patched_updaters[dialog.overview_page]
                dialog._on_sample_ready(snapshot)
                QApplication.processEvents()
                mock_apply.assert_not_called()
                self.assertTrue(dialog._overview_dirty)

                dialog.tabs.setCurrentWidget(dialog.overview_page)
                QApplication.processEvents()

            mock_apply.assert_called_once_with(snapshot)
            self.assertFalse(dialog._overview_dirty)
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_filesystem_tree_reconciles_rows_in_place_without_rebuilding_items(self) -> None:
        with patch("snakesh.ui.resource_monitor_dialog.QTimer.singleShot"):
            dialog = ResourceMonitorDialog()
        try:
            initial_entries = [
                FilesystemEntry(
                    device="C:",
                    mountpoint="C:\\",
                    filesystem_type="ntfs",
                    used_bytes=400_000,
                    total_bytes=1_000_000,
                    free_bytes=600_000,
                    usage_percent=40.0,
                    is_home=True,
                    disk_device_key="physicaldrive0",
                ),
                FilesystemEntry(
                    device="D:",
                    mountpoint="D:\\",
                    filesystem_type="ntfs",
                    used_bytes=250_000,
                    total_bytes=2_000_000,
                    free_bytes=1_750_000,
                    usage_percent=12.5,
                    disk_device_key="physicaldrive1",
                ),
            ]
            initial_disk_devices = [
                DiskDeviceSample(
                    key="physicaldrive0",
                    display_label="PhysicalDrive0",
                    read_bytes_per_sec=1_200.0,
                    write_bytes_per_sec=600.0,
                    read_bytes_since_open=12_000,
                    write_bytes_since_open=6_000,
                ),
                DiskDeviceSample(
                    key="physicaldrive1",
                    display_label="PhysicalDrive1",
                    read_bytes_per_sec=240.0,
                    write_bytes_per_sec=120.0,
                    read_bytes_since_open=2_400,
                    write_bytes_since_open=1_200,
                ),
            ]
            dialog._populate_filesystems(dialog.filesystems_tree, initial_entries, initial_disk_devices)

            marker_role = Qt.UserRole + 999
            home_item = self._filesystem_item_by_mount(dialog, "C:\\")
            assert home_item is not None
            home_item.setData(0, marker_role, "preserved")

            updated_entries = [
                FilesystemEntry(
                    device="C:",
                    mountpoint="C:\\",
                    filesystem_type="ntfs",
                    used_bytes=700_000,
                    total_bytes=1_000_000,
                    free_bytes=300_000,
                    usage_percent=70.0,
                    is_home=True,
                    disk_device_key="physicaldrive0",
                ),
                FilesystemEntry(
                    device="E:",
                    mountpoint="E:\\",
                    filesystem_type="ntfs",
                    used_bytes=900_000,
                    total_bytes=3_000_000,
                    free_bytes=2_100_000,
                    usage_percent=30.0,
                    disk_device_key="physicaldrive2",
                ),
            ]
            updated_disk_devices = [
                DiskDeviceSample(
                    key="physicaldrive0",
                    display_label="PhysicalDrive0",
                    read_bytes_per_sec=7_000.0,
                    write_bytes_per_sec=3_500.0,
                    read_bytes_since_open=70_000,
                    write_bytes_since_open=35_000,
                ),
                DiskDeviceSample(
                    key="physicaldrive2",
                    display_label="PhysicalDrive2",
                    read_bytes_per_sec=9_000.0,
                    write_bytes_per_sec=4_500.0,
                    read_bytes_since_open=90_000,
                    write_bytes_since_open=45_000,
                ),
            ]

            dialog._populate_filesystems(dialog.filesystems_tree, updated_entries, updated_disk_devices)
            QApplication.processEvents()

            updated_home_item = self._filesystem_item_by_mount(dialog, "C:\\")
            assert updated_home_item is not None
            self.assertEqual(dialog.filesystems_tree.topLevelItemCount(), 2)
            self.assertEqual(updated_home_item.data(0, marker_role), "preserved")
            self.assertEqual(
                updated_home_item.data(3, resource_monitor_dialog_module._SORT_ROLE),
                700_000,
            )
            self.assertEqual(updated_home_item.text(6), "6.8 KB/s")
            self.assertEqual(updated_home_item.text(7), "3.4 KB/s")
            self.assertEqual(
                updated_home_item.data(6, resource_monitor_dialog_module._SORT_ROLE),
                7_000.0,
            )
            self.assertEqual(
                updated_home_item.data(7, resource_monitor_dialog_module._SORT_ROLE),
                3_500.0,
            )
            self.assertIsNone(self._filesystem_item_by_mount(dialog, "D:\\"))
            self.assertIsNotNone(self._filesystem_item_by_mount(dialog, "E:\\"))
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_interface_tree_reconciles_rows_in_place_and_preserves_active_sort(self) -> None:
        with patch("snakesh.ui.resource_monitor_dialog.QTimer.singleShot"):
            dialog = ResourceMonitorDialog()
        try:
            tree = dialog.network_interfaces_tree
            initial_entries = [
                InterfaceBandwidthEntry(
                    name="eth0",
                    ipv4_address="192.0.2.20",
                    ipv6_address="2001:db8::20",
                    is_up=True,
                    speed_mbps=1000,
                    recv_bytes_per_sec=10.0,
                    sent_bytes_per_sec=5.0,
                    recv_bytes_total=2_000,
                    sent_bytes_total=1_000,
                ),
                InterfaceBandwidthEntry(
                    name="wlan0",
                    ipv4_address="192.0.2.30",
                    ipv6_address="2001:db8::30",
                    is_up=True,
                    speed_mbps=866,
                    recv_bytes_per_sec=6.0,
                    sent_bytes_per_sec=2.0,
                    recv_bytes_total=1_000,
                    sent_bytes_total=400,
                ),
            ]
            dialog._populate_interfaces(tree, initial_entries)
            tree.sortItems(7, Qt.DescendingOrder)
            QApplication.processEvents()

            marker_role = Qt.UserRole + 998
            eth0_item = self._interface_item_by_name(tree, "eth0")
            assert eth0_item is not None
            eth0_item.setData(0, marker_role, "keep")

            updated_entries = [
                InterfaceBandwidthEntry(
                    name="eth0",
                    ipv4_address="192.0.2.20",
                    ipv6_address="2001:db8::20",
                    is_up=True,
                    speed_mbps=1000,
                    recv_bytes_per_sec=8.0,
                    sent_bytes_per_sec=4.0,
                    recv_bytes_total=1_500,
                    sent_bytes_total=900,
                ),
                InterfaceBandwidthEntry(
                    name="lan1",
                    ipv4_address="198.51.100.10",
                    ipv6_address="",
                    is_up=True,
                    speed_mbps=1000,
                    recv_bytes_per_sec=14.0,
                    sent_bytes_per_sec=6.0,
                    recv_bytes_total=5_000,
                    sent_bytes_total=2_000,
                ),
            ]
            dialog._populate_interfaces(tree, updated_entries)
            QApplication.processEvents()

            updated_eth0_item = self._interface_item_by_name(tree, "eth0")
            assert updated_eth0_item is not None
            self.assertEqual(updated_eth0_item.data(0, marker_role), "keep")
            self.assertEqual(tree.sortColumn(), 7)
            self.assertEqual(tree.header().sortIndicatorOrder(), Qt.DescendingOrder)
            self.assertEqual(tree.topLevelItem(0).text(0), "lan1")
            self.assertIsNone(self._interface_item_by_name(tree, "wlan0"))
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_ui_apply_logs_tree_context_for_overview_updates(self) -> None:
        with patch("snakesh.ui.resource_monitor_dialog.QTimer.singleShot"):
            dialog = ResourceMonitorDialog()
        try:
            snapshot = self._build_snapshot(gpu_available=False)
            with patch.object(resource_monitor_dialog_module._LOGGER, "debug") as mock_debug:
                dialog._apply_overview_snapshot(snapshot)

            messages = [str(call.args[0]) for call in mock_debug.call_args_list]
            self.assertTrue(any("stage=populate-filesystems" in message for message in messages))
            self.assertTrue(any("tree=overview-filesystems" in message for message in messages))
            self.assertTrue(any("sort_column=" in message and "sort_order=" in message for message in messages))
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_slow_ui_apply_stage_emits_warning_log(self) -> None:
        with patch("snakesh.ui.resource_monitor_dialog.QTimer.singleShot"):
            dialog = ResourceMonitorDialog()
        try:
            snapshot = self._build_snapshot(gpu_available=False)
            original_update_cards = dialog._update_cards

            def slow_update_cards(payload: ResourceMonitorSnapshot) -> None:
                time.sleep(0.3)
                original_update_cards(payload)

            dialog._update_cards = slow_update_cards  # type: ignore[method-assign]
            with patch.object(resource_monitor_dialog_module._LOGGER, "warning") as mock_warning:
                dialog._on_sample_ready(snapshot)

            messages = [str(call.args[0]) for call in mock_warning.call_args_list]
            self.assertTrue(any("stage=update-cards" in message for message in messages))
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_drain_main_thread_calls_emits_stage_logs(self) -> None:
        with patch("snakesh.ui.resource_monitor_dialog.QTimer.singleShot"):
            dialog = ResourceMonitorDialog()
        try:
            dialog._enqueue_main_thread_call("_on_sample_failed", "boom")
            with patch.object(resource_monitor_dialog_module._LOGGER, "debug") as mock_debug:
                dialog._drain_main_thread_calls()

            messages = [str(call.args[0]) for call in mock_debug.call_args_list]
            self.assertTrue(any("stage=drain-main-thread-calls" in message for message in messages))
            self.assertTrue(any("stage=dispatch-main-thread-call" in message for message in messages))
            self.assertEqual(dialog.overview_status_label.text(), "boom")
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_cpu_tab_renders_logical_cores_and_temperature_history(self) -> None:
        with patch("snakesh.ui.resource_monitor_dialog.QTimer.singleShot"):
            dialog = ResourceMonitorDialog()
        try:
            dialog.tabs.setCurrentWidget(dialog.cpu_page)
            QApplication.processEvents()

            dialog._on_sample_ready(self._build_snapshot(gpu_available=False))
            QApplication.processEvents()

            self.assertEqual(dialog.cpu_cores_tree.topLevelItemCount(), 4)
            self.assertEqual(dialog.cpu_cores_tree.topLevelItem(0).text(0), "CPU 0")
            self.assertEqual(dialog.cpu_detail_cards["temperature"].value_label.text(), "67 C")
            self.assertIs(dialog.cpu_device_section.charts["usage_history"], dialog.cpu_detail_chart)
            self.assertIs(dialog.cpu_device_section.charts["temperature_history"], dialog.cpu_temp_chart)
            self.assertEqual(dialog.cpu_temp_chart.chart().title(), "CPU Temperature")
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_cpu_values_above_100_render_in_cards_and_core_table(self) -> None:
        with patch("snakesh.ui.resource_monitor_dialog.QTimer.singleShot"):
            dialog = ResourceMonitorDialog()
        try:
            dialog.tabs.setCurrentWidget(dialog.cpu_page)
            QApplication.processEvents()

            snapshot = self._build_snapshot(gpu_available=False)
            sample = replace(
                snapshot.sample,
                cpu_percent=132.0,
                cpu_per_core_percentages=(145.0, 118.5, 102.25, 99.0),
            )
            snapshot = ResourceMonitorSnapshot(
                sample=sample,
                filesystems=snapshot.filesystems,
                disk_devices=snapshot.disk_devices,
                interfaces=snapshot.interfaces,
                gpu=snapshot.gpu,
            )

            dialog._on_sample_ready(snapshot)
            QApplication.processEvents()

            self.assertEqual(dialog._cards["cpu"].value_label.text(), "132%")
            self.assertEqual(dialog.cpu_detail_cards["usage"].value_label.text(), "132.0%")
            self.assertEqual(dialog.cpu_cores_tree.topLevelItem(0).text(1), "145.0%")
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_cpu_charts_switch_to_dynamic_axis_when_history_exceeds_100_percent(self) -> None:
        with patch("snakesh.ui.resource_monitor_dialog.QTimer.singleShot"):
            dialog = ResourceMonitorDialog()
        try:
            first_snapshot = self._build_snapshot(gpu_available=False)
            second_sample = replace(
                first_snapshot.sample,
                cpu_percent=132.0,
                cpu_per_core_percentages=(145.0, 118.5, 102.25, 99.0),
            )
            second_snapshot = ResourceMonitorSnapshot(
                sample=second_sample,
                filesystems=first_snapshot.filesystems,
                disk_devices=first_snapshot.disk_devices,
                interfaces=first_snapshot.interfaces,
                gpu=first_snapshot.gpu,
            )

            dialog._on_sample_ready(first_snapshot)
            dialog._on_sample_ready(second_snapshot)
            QApplication.processEvents()

            self.assertGreater(self._chart_axis_max(dialog, dialog.cpu_chart), 100.0)

            dialog.tabs.setCurrentWidget(dialog.cpu_page)
            QApplication.processEvents()

            self.assertGreater(self._chart_axis_max(dialog, dialog.cpu_detail_chart), 100.0)
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_windows_cpu_temperature_detail_text_is_generic_best_effort(self) -> None:
        with (
            patch("snakesh.ui.resource_monitor_dialog.QTimer.singleShot"),
            patch("snakesh.ui.resource_monitor_dialog.platform.system", return_value="Windows"),
        ):
            dialog = ResourceMonitorDialog()
            try:
                dialog.tabs.setCurrentWidget(dialog.cpu_page)
                QApplication.processEvents()

                dialog._on_sample_ready(self._build_snapshot(gpu_available=False))
                QApplication.processEvents()

                self.assertEqual(
                    dialog.cpu_detail_cards["temperature"].detail_label.text(),
                    "Highest available Windows CPU temperature source",
                )
            finally:
                dialog.deleteLater()
                QApplication.processEvents()

    def test_ram_used_memory_chart_uses_gb_units(self) -> None:
        with patch("snakesh.ui.resource_monitor_dialog.QTimer.singleShot"):
            dialog = ResourceMonitorDialog()
        try:
            dialog.tabs.setCurrentWidget(dialog.ram_page)
            QApplication.processEvents()

            snapshot = self._build_snapshot(gpu_available=False)
            dialog._on_sample_ready(snapshot)
            QApplication.processEvents()

            self.assertEqual(self._chart_axis_title(dialog, dialog.ram_bytes_chart), "GB")
            memory_values = self._series_y_values(dialog, dialog.ram_bytes_chart, 0)
            swap_values = self._series_y_values(dialog, dialog.ram_bytes_chart, 1)
            self.assertEqual(len(memory_values), 1)
            self.assertEqual(len(swap_values), 1)
            self.assertAlmostEqual(memory_values[0], snapshot.sample.memory_used_bytes / (1024.0**3), places=4)
            self.assertAlmostEqual(swap_values[0], snapshot.sample.swap_used_bytes / (1024.0**3), places=4)
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_disk_throughput_charts_scale_to_dynamic_byte_units(self) -> None:
        with patch("snakesh.ui.resource_monitor_dialog.QTimer.singleShot"):
            dialog = ResourceMonitorDialog()
        try:
            snapshot = self._build_snapshot(gpu_available=False)
            dialog._on_sample_ready(snapshot)
            QApplication.processEvents()
            self.assertEqual(self._chart_axis_title(dialog, dialog.disk_chart), "MB/s")
            self.assertEqual(
                self._series_names(dialog, dialog.disk_chart),
                ["nvme0n1p5 Read", "nvme0n1p5 Write"],
            )

            dialog.tabs.setCurrentWidget(dialog.disks_page)
            QApplication.processEvents()
            self.assertEqual(self._chart_axis_title(dialog, dialog.disk_io_history_chart), "MB/s")
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_resource_monitor_line_charts_use_spline_series_with_antialiasing(self) -> None:
        with patch("snakesh.ui.resource_monitor_dialog.QTimer.singleShot"):
            dialog = ResourceMonitorDialog()
        try:
            first_snapshot = self._build_multi_device_snapshot()
            second_snapshot = ResourceMonitorSnapshot(
                sample=replace(
                    first_snapshot.sample,
                    timestamp_monotonic=first_snapshot.sample.timestamp_monotonic + 1.0,
                    network_recv_bytes_per_sec=1_000.0,
                    network_sent_bytes_per_sec=500.0,
                ),
                filesystems=first_snapshot.filesystems,
                disk_devices=first_snapshot.disk_devices,
                interfaces=[
                    replace(first_snapshot.interfaces[0], recv_bytes_per_sec=1_000.0, sent_bytes_per_sec=500.0),
                    first_snapshot.interfaces[1],
                ],
                gpu=first_snapshot.gpu,
            )

            dialog._on_sample_ready(first_snapshot)
            dialog._on_sample_ready(second_snapshot)
            QApplication.processEvents()

            cpu_series = dialog._chart_states[dialog.cpu_chart]["series"][0]
            disk_series = dialog._chart_states[dialog.disk_chart]["series"][0]
            network_values = self._series_y_values(dialog, dialog.network_chart, 0)

            self.assertIsInstance(cpu_series, QSplineSeries)
            self.assertIsInstance(disk_series, QSplineSeries)
            self.assertTrue(dialog.cpu_chart.renderHints() & QPainter.RenderHint.Antialiasing)
            self.assertEqual(self._series_y_values(dialog, dialog.cpu_chart, 0), [32.0, 32.0])
            self.assertAlmostEqual(network_values[0], 32.0, places=4)
            self.assertAlmostEqual(network_values[1], 0.008, places=4)
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_overview_metric_cards_reserve_width_for_dynamic_rates(self) -> None:
        with patch("snakesh.ui.resource_monitor_dialog.QTimer.singleShot"):
            dialog = ResourceMonitorDialog()
        try:
            self.assertGreaterEqual(dialog._cards["network"].value_label.minimumWidth(), 280)
            self.assertGreaterEqual(dialog._cards["disk_io"].value_label.minimumWidth(), 280)
            self.assertEqual(dialog._cards["network"].value_label.sizePolicy().horizontalPolicy(), QSizePolicy.Ignored)
            self.assertEqual(dialog._cards["disk_io"].value_label.sizePolicy().horizontalPolicy(), QSizePolicy.Ignored)
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_overview_metric_cards_render_bold_values_with_one_decimal_place(self) -> None:
        with patch("snakesh.ui.resource_monitor_dialog.QTimer.singleShot"):
            dialog = ResourceMonitorDialog()
        try:
            dialog._on_sample_ready(self._build_snapshot(gpu_available=False))
            QApplication.processEvents()

            self.assertEqual(dialog._cards["memory"].value_label.text(), "11.2 GB / 29.8 GB")
            self.assertEqual(dialog._cards["disk"].value_label.text(), "1 Disk")
            self.assertEqual(
                dialog._cards["disk"].detail_label.text(),
                "nvme0n1p5\n 40.0% used, 372.5 GB / 931.3 GB, Free 558.8 GB",
            )
            self.assertEqual(dialog._cards["disk_io"].value_label.text(), "1 Disk")
            self.assertEqual(
                dialog._cards["disk_io"].detail_label.text(),
                "nvme0n1p5\n Read 1.9 MB/s Write 976.6 KB/s",
            )
            self.assertEqual(dialog._cards["network"].value_label.text(), "1 Adapter")
            self.assertEqual(
                dialog._cards["network"].detail_label.text(),
                "eth0\n Down 32.0 Mbps Up 4.00 Mbps",
            )
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_overview_metric_card_fonts_do_not_grow_on_shorter_live_values(self) -> None:
        with patch("snakesh.ui.resource_monitor_dialog.QTimer.singleShot"):
            dialog = ResourceMonitorDialog()
        try:
            base_snapshot = self._build_snapshot(gpu_available=False)
            first_sample = replace(
                base_snapshot.sample,
                cpu_percent=132.0,
                memory_used_bytes=550_000_000_000,
                memory_total_bytes=1_200_000_000_000,
                memory_percent=(550_000_000_000 / 1_200_000_000_000) * 100.0,
            )
            first_snapshot = ResourceMonitorSnapshot(
                sample=first_sample,
                filesystems=base_snapshot.filesystems,
                disk_devices=base_snapshot.disk_devices,
                interfaces=base_snapshot.interfaces,
                gpu=base_snapshot.gpu,
            )
            dialog._on_sample_ready(first_snapshot)
            QApplication.processEvents()

            original_cpu_font = dialog._cards["cpu"].value_label.font().pointSizeF()
            original_memory_font = dialog._cards["memory"].value_label.font().pointSizeF()

            dialog._on_sample_ready(base_snapshot)
            QApplication.processEvents()

            self.assertEqual(dialog._cards["cpu"].value_label.text(), "32%")
            self.assertEqual(dialog._cards["memory"].value_label.text(), "11.2 GB / 29.8 GB")
            self.assertLessEqual(dialog._cards["cpu"].value_label.font().pointSizeF(), original_cpu_font)
            self.assertLessEqual(dialog._cards["memory"].value_label.font().pointSizeF(), original_memory_font)
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_dialog_inherits_parent_theme_colors(self) -> None:
        parent = QDialog()
        parent._settings = self._custom_theme_settings()
        with patch("snakesh.ui.resource_monitor_dialog.QTimer.singleShot"):
            dialog = ResourceMonitorDialog(parent=parent)
        try:
            chart = dialog.memory_chart.chart()
            self.assertEqual(chart.backgroundBrush().color().name(), parent._settings.field_bg)
            self.assertEqual(chart.backgroundPen().color().name(), parent._settings.field_border)
            self.assertIn(parent._settings.accent_color, dialog.styleSheet())
            self.assertIn(parent._settings.field_bg, dialog.styleSheet())
        finally:
            dialog.deleteLater()
            parent.deleteLater()
            QApplication.processEvents()

    def test_refresh_theme_updates_existing_cards_and_charts(self) -> None:
        parent = QDialog()
        parent._settings = AppSettings.defaults()
        with patch("snakesh.ui.resource_monitor_dialog.QTimer.singleShot"):
            dialog = ResourceMonitorDialog(parent=parent)
        try:
            dialog.tabs.setCurrentWidget(dialog.ram_page)
            QApplication.processEvents()
            dialog._on_sample_ready(self._build_snapshot(gpu_available=False))
            QApplication.processEvents()

            original_chart_bg = dialog.memory_chart.chart().backgroundBrush().color().name()
            original_series_color = dialog._chart_states[dialog.ram_bytes_chart]["series"][0].pen().color().name()

            parent._settings = self._custom_theme_settings()
            dialog.refresh_theme()
            QApplication.processEvents()

            self.assertEqual(dialog.memory_chart.chart().backgroundBrush().color().name(), parent._settings.field_bg)
            self.assertEqual(
                dialog._chart_states[dialog.ram_bytes_chart]["series"][0].pen().color().name(),
                parent._settings.accent_color,
            )
            self.assertIn(parent._settings.accent_color, dialog.styleSheet())
            self.assertNotEqual(original_chart_bg, dialog.memory_chart.chart().backgroundBrush().color().name())
            self.assertNotEqual(
                original_series_color,
                dialog._chart_states[dialog.ram_bytes_chart]["series"][0].pen().color().name(),
            )
        finally:
            dialog.deleteLater()
            parent.deleteLater()
            QApplication.processEvents()

    def test_apply_runtime_settings_updates_internal_theme_and_existing_charts(self) -> None:
        with patch("snakesh.ui.resource_monitor_dialog.QTimer.singleShot"):
            dialog = ResourceMonitorDialog()
        try:
            dialog.tabs.setCurrentWidget(dialog.ram_page)
            QApplication.processEvents()
            dialog._on_sample_ready(self._build_snapshot(gpu_available=False))
            QApplication.processEvents()

            settings = self._custom_theme_settings()
            dialog.apply_runtime_settings(settings)
            QApplication.processEvents()

            self.assertIsNotNone(dialog._settings)
            assert dialog._settings is not None
            self.assertEqual(dialog._settings.accent_color, settings.accent_color)
            self.assertEqual(dialog.memory_chart.chart().backgroundBrush().color().name(), settings.field_bg)
            self.assertEqual(
                dialog._chart_states[dialog.ram_bytes_chart]["series"][0].pen().color().name(),
                settings.accent_color,
            )
            self.assertIn(settings.accent_color, dialog.styleSheet())
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_dialog_stylesheet_includes_theme_aware_selection_focus_menu_and_scrollbars(self) -> None:
        parent = QDialog()
        parent._settings = self._custom_theme_settings()
        with patch("snakesh.ui.resource_monitor_dialog.QTimer.singleShot"):
            dialog = ResourceMonitorDialog(parent=parent)
        try:
            style = dialog.styleSheet()
            self.assertIn("QTreeWidget#resourceDetailTree::item:selected", style)
            self.assertIn("QLineEdit#processSearch:focus", style)
            self.assertIn("QMenu::item:selected", style)
            self.assertIn("QScrollBar::handle:vertical", style)
            self.assertIn(parent._settings.tab_active_bg, style)
            self.assertIn(parent._settings.tab_active_fg, style)
            self.assertIn(parent._settings.accent_color, style)
        finally:
            dialog.deleteLater()
            parent.deleteLater()
            QApplication.processEvents()

    def test_network_tab_reuses_chart_objects_across_updates(self) -> None:
        with patch("snakesh.ui.resource_monitor_dialog.QTimer.singleShot"):
            dialog = ResourceMonitorDialog()
        try:
            dialog.tabs.setCurrentWidget(dialog.network_page)
            QApplication.processEvents()

            first_snapshot = self._build_snapshot(gpu_available=False)
            second_sample = replace(first_snapshot.sample, network_recv_bytes_per_sec=512.0)
            second_snapshot = ResourceMonitorSnapshot(
                sample=second_sample,
                filesystems=first_snapshot.filesystems,
                disk_devices=first_snapshot.disk_devices,
                interfaces=[
                    replace(
                        first_snapshot.interfaces[0],
                        recv_bytes_per_sec=512.0,
                    )
                ],
                gpu=first_snapshot.gpu,
            )

            dialog._on_sample_ready(first_snapshot)
            QApplication.processEvents()
            first_bandwidth_chart = dialog.network_detail_chart.chart()

            dialog._on_sample_ready(second_snapshot)
            QApplication.processEvents()

            self.assertIs(dialog.network_detail_chart.chart(), first_bandwidth_chart)
            self.assertEqual(self._network_section(dialog, "eth0").cards["receive"].value_label.text(), "4.10 Kbps")
            self.assertEqual(self._chart_axis_title(dialog, dialog.network_detail_chart), "Mbps")
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_offline_network_adapter_reuses_tree_row_and_section_when_it_comes_online(self) -> None:
        settings = AppSettings.defaults()
        settings.resource_monitor_show_offline_adapters = True
        with patch("snakesh.ui.resource_monitor_dialog.QTimer.singleShot"):
            dialog = ResourceMonitorDialog(settings=settings)
        try:
            dialog.tabs.setCurrentWidget(dialog.network_page)
            QApplication.processEvents()

            first_snapshot = self._build_snapshot_with_offline_interface()
            dialog._on_sample_ready(first_snapshot)
            QApplication.processEvents()

            marker_role = Qt.UserRole + 997
            first_row = self._interface_item_by_name(dialog.network_interfaces_tree, "wlan0")
            assert first_row is not None
            first_row.setData(0, marker_role, "keep")
            first_group = self._network_section(dialog, "wlan0").group

            second_sample = replace(
                first_snapshot.sample,
                timestamp_monotonic=first_snapshot.sample.timestamp_monotonic + 1.0,
                network_recv_bytes_per_sec=4_064_000.0,
                network_sent_bytes_per_sec=532_000.0,
            )
            second_snapshot = ResourceMonitorSnapshot(
                sample=second_sample,
                filesystems=first_snapshot.filesystems,
                disk_devices=first_snapshot.disk_devices,
                interfaces=[
                    first_snapshot.interfaces[0],
                    replace(
                        first_snapshot.interfaces[1],
                        ipv4_address="192.0.2.55",
                        ipv6_address="2001:db8::55",
                        is_up=True,
                        recv_bytes_per_sec=64_000.0,
                        sent_bytes_per_sec=32_000.0,
                        recv_bytes_total=1_200_000,
                        sent_bytes_total=700_000,
                    ),
                ],
                gpu=first_snapshot.gpu,
            )

            dialog._on_sample_ready(second_snapshot)
            QApplication.processEvents()

            updated_row = self._interface_item_by_name(dialog.network_interfaces_tree, "wlan0")
            assert updated_row is not None
            self.assertEqual(updated_row.data(0, marker_role), "keep")
            self.assertEqual(updated_row.text(3), "Up")
            self.assertIs(self._network_section(dialog, "wlan0").group, first_group)
            self.assertEqual(self._network_section(dialog, "wlan0").charts["bandwidth_history"].chart().title(), "Network Bandwidth")
            self.assertEqual(self._network_section(dialog, "wlan0").cards["receive"].detail_label.text(), "Current receive bandwidth")
            self.assertEqual(self._network_section(dialog, "wlan0").cards["receive"].value_label.text(), "512 Kbps")
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_empty_gpu_chart_object_is_reused_across_updates(self) -> None:
        with patch("snakesh.ui.resource_monitor_dialog.QTimer.singleShot"):
            dialog = ResourceMonitorDialog()
        try:
            snapshot = self._build_snapshot(gpu_available=False)
            dialog._on_sample_ready(snapshot)
            QApplication.processEvents()
            first_chart = dialog.gpu_chart.chart()

            dialog._on_sample_ready(snapshot)
            QApplication.processEvents()

            self.assertIs(dialog.gpu_chart.chart(), first_chart)
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_network_charts_scale_axis_unit_from_history_maximum(self) -> None:
        with patch("snakesh.ui.resource_monitor_dialog.QTimer.singleShot"):
            dialog = ResourceMonitorDialog()
        try:
            low_rate_snapshot = self._build_snapshot(gpu_available=False)
            low_rate_sample = replace(
                low_rate_snapshot.sample,
                network_recv_bytes_per_sec=512.0,
                network_sent_bytes_per_sec=128.0,
            )
            low_rate_snapshot = ResourceMonitorSnapshot(
                sample=low_rate_sample,
                filesystems=low_rate_snapshot.filesystems,
                disk_devices=low_rate_snapshot.disk_devices,
                interfaces=low_rate_snapshot.interfaces,
                gpu=low_rate_snapshot.gpu,
            )

            dialog._on_sample_ready(low_rate_snapshot)
            QApplication.processEvents()
            self.assertEqual(self._chart_axis_title(dialog, dialog.network_chart), "Kbps")

            dialog.tabs.setCurrentWidget(dialog.network_page)
            QApplication.processEvents()
            self.assertEqual(self._chart_axis_title(dialog, dialog.network_detail_chart), "Kbps")
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_history_charts_keep_a_fixed_time_window_from_first_sample(self) -> None:
        with patch("snakesh.ui.resource_monitor_dialog.QTimer.singleShot"):
            dialog = ResourceMonitorDialog()
        try:
            snapshot = self._build_multi_device_snapshot()
            dialog._on_sample_ready(snapshot)
            QApplication.processEvents()

            self.assertEqual(self._chart_x_axis_max(dialog, dialog.network_chart), float(dialog._max_history - 1))
            self.assertEqual(self._series_x_values(dialog, dialog.network_chart, 0), [float(dialog._max_history - 1)])

            dialog.tabs.setCurrentWidget(dialog.network_page)
            QApplication.processEvents()

            eth0 = self._network_section(dialog, "eth0")
            self.assertEqual(
                self._chart_x_axis_max(dialog, eth0.charts["bandwidth_history"]),
                float(dialog._max_history - 1),
            )
            self.assertEqual(
                self._series_x_values(dialog, eth0.charts["bandwidth_history"], 0),
                [float(dialog._max_history - 1)],
            )

            dialog.tabs.setCurrentWidget(dialog.overview_page)
            QApplication.processEvents()

            second_sample = replace(
                snapshot.sample,
                timestamp_monotonic=snapshot.sample.timestamp_monotonic + 1.0,
                network_recv_bytes_per_sec=5_000_000.0,
                network_sent_bytes_per_sec=750_000.0,
            )
            second_snapshot = ResourceMonitorSnapshot(
                sample=second_sample,
                filesystems=snapshot.filesystems,
                disk_devices=snapshot.disk_devices,
                interfaces=[
                    replace(
                        snapshot.interfaces[0],
                        recv_bytes_per_sec=5_000_000.0,
                        sent_bytes_per_sec=750_000.0,
                    ),
                    snapshot.interfaces[1],
                ],
                gpu=snapshot.gpu,
            )
            dialog._on_sample_ready(second_snapshot)
            QApplication.processEvents()

            self.assertEqual(
                self._series_x_values(dialog, dialog.cpu_chart, 0),
                [float(dialog._max_history - 2), float(dialog._max_history - 1)],
            )
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_requires_elevation_is_retried_after_action_thread_finishes(self) -> None:
        with patch("snakesh.ui.resource_monitor_dialog.QTimer.singleShot"):
            dialog = ResourceMonitorDialog()
        try:
            dialog._active_action_pid = 200
            dialog._active_action_force = False
            with patch("snakesh.ui.resource_monitor_dialog.QMessageBox.question", return_value=QMessageBox.Yes):
                dialog._on_process_action_ready(
                    ProcessActionResult(
                        success=False,
                        message="Administrative privileges are required.",
                        pid=200,
                        action="terminate",
                        requires_elevation=True,
                    )
                )

            self.assertEqual(dialog._pending_elevated_action, (200, False))
            with patch.object(dialog, "_launch_process_action", return_value=True) as mock_launch:
                dialog._on_action_finished()
            mock_launch.assert_called_once_with(pid=200, force=False, allow_elevation=True)
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_close_event_waits_for_running_threads(self) -> None:
        with patch("snakesh.ui.resource_monitor_dialog.QTimer.singleShot"):
            dialog = ResourceMonitorDialog()
        try:
            running_thread = unittest.mock.Mock()
            running_thread.isRunning.return_value = True
            dialog._sample_thread = running_thread

            event = QCloseEvent()
            dialog.closeEvent(event)

            self.assertFalse(event.isAccepted())
            self.assertTrue(dialog._close_pending)
            running_thread.requestInterruption.assert_called_once_with()
            running_thread.quit.assert_called_once_with()
            self.assertTrue(dialog._main_thread_dispatch_timer.isActive())

            dialog._sample_thread = None
            with (
                patch("snakesh.ui.resource_monitor_dialog.QTimer.singleShot", side_effect=lambda _msec, callback: callback()),
                patch.object(dialog, "close") as mock_close,
            ):
                dialog._maybe_finish_close()
            mock_close.assert_called_once_with()
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_force_close_threads_waits_gracefully_without_terminate(self) -> None:
        with patch("snakesh.ui.resource_monitor_dialog.QTimer.singleShot"):
            dialog = ResourceMonitorDialog()
        try:
            running_thread = unittest.mock.Mock()
            running_thread.isRunning.return_value = True
            dialog._sample_thread = running_thread

            dialog._force_close_threads()

            running_thread.wait.assert_called_once_with(250)
            running_thread.terminate.assert_not_called()
            self.assertFalse(dialog._close_force_timer.isActive())
        finally:
            dialog._close_force_timer.stop()
            dialog.deleteLater()
            QApplication.processEvents()

    def test_worker_results_are_dispatched_to_main_thread(self) -> None:
        app = QApplication.instance() or QApplication([])
        main_thread = app.thread()

        class ProbeDialog(ResourceMonitorDialog):
            def __init__(self) -> None:
                self.sample_on_main_thread: bool | None = None
                self.process_on_main_thread: bool | None = None
                super().__init__()

            def _on_sample_ready(self, payload: object) -> None:
                self.sample_on_main_thread = QThread.currentThread() is main_thread
                super()._on_sample_ready(payload)

            def _on_process_snapshot_ready(self, payload: object) -> None:
                self.process_on_main_thread = QThread.currentThread() is main_thread
                super()._on_process_snapshot_ready(payload)

        with patch("snakesh.ui.resource_monitor_dialog.QTimer.singleShot"):
            dialog = ProbeDialog()
        try:
            dialog._overview_collector.collect_fast = lambda **_kwargs: self._build_snapshot(gpu_available=False)  # type: ignore[method-assign]
            dialog._process_collector.collect = lambda: self._build_process_snapshot()  # type: ignore[method-assign]

            dialog._start_sample_refresh()
            dialog._start_process_refresh()

            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                QApplication.processEvents()
                if dialog.sample_on_main_thread is not None and dialog.process_on_main_thread is not None:
                    break
                time.sleep(0.01)

            self.assertIs(dialog.sample_on_main_thread, True)
            self.assertIs(dialog.process_on_main_thread, True)
        finally:
            dialog.close()
            dialog.deleteLater()
            QApplication.processEvents()

    def test_slow_detail_refresh_queues_fast_refresh_and_runs_it_after_completion(self) -> None:
        with patch("snakesh.ui.resource_monitor_dialog.QTimer.singleShot"):
            dialog = ResourceMonitorDialog()
        try:
            dialog._slow_details_enabled = True
            calls: list[str] = []
            slow_started = threading.Event()
            slow_release = threading.Event()

            def slow_snapshot() -> ResourceMonitorSnapshot:
                calls.append("slow")
                slow_started.set()
                self.assertTrue(slow_release.wait(1.0))
                return self._build_snapshot(gpu_available=False)

            def fast_snapshot() -> ResourceMonitorSnapshot:
                calls.append("fast")
                return self._build_snapshot(gpu_available=False)

            dialog._refresh_slow_details_snapshot = slow_snapshot  # type: ignore[method-assign]
            dialog._collect_fast_snapshot = fast_snapshot  # type: ignore[method-assign]

            self.assertTrue(dialog._start_slow_detail_refresh())
            self.assertTrue(slow_started.wait(1.0))
            self.assertTrue(dialog._start_sample_refresh())
            self.assertEqual(calls, ["slow"])
            self.assertTrue(dialog._overview_refresh_pending)
            self.assertFalse(dialog._overview_slow_refresh_pending)
            slow_release.set()

            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                QApplication.processEvents()
                if dialog._sample_thread is None and calls == ["slow", "fast"]:
                    break
                time.sleep(0.01)

            self.assertIsNone(dialog._sample_thread)
            self.assertEqual(calls, ["slow", "fast"])
            self.assertFalse(dialog._overview_refresh_pending)
            self.assertFalse(dialog._overview_slow_refresh_pending)
        finally:
            slow_release.set()
            dialog.close()
            dialog.deleteLater()
            QApplication.processEvents()

    def test_linux_dialog_enables_slow_detail_refresh_by_default(self) -> None:
        with (
            patch("snakesh.ui.resource_monitor_dialog.QTimer.singleShot"),
            patch("snakesh.ui.resource_monitor_dialog.ResourceMonitorOverviewCollector") as mock_collector_cls,
        ):
            collector = mock_collector_cls.return_value
            collector.platform_name = "linux"
            dialog = ResourceMonitorDialog()
        try:
            self.assertTrue(dialog._slow_details_enabled)
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_worker_failures_log_tracebacks(self) -> None:
        with patch("snakesh.ui.resource_monitor_dialog.QTimer.singleShot"):
            dialog = ResourceMonitorDialog()
        try:
            def broken_fast_snapshot() -> ResourceMonitorSnapshot:
                raise RuntimeError("boom")

            dialog._collect_fast_snapshot = broken_fast_snapshot  # type: ignore[method-assign]

            with patch("snakesh.ui.resource_monitor_dialog.log_worker_failed") as mock_log_failed:
                self.assertTrue(dialog._start_sample_refresh())

                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline:
                    QApplication.processEvents()
                    if dialog._sample_thread is None:
                        break
                    time.sleep(0.01)

            mock_log_failed.assert_called_once()
            self.assertIn("boom", dialog.overview_status_label.text())
        finally:
            dialog.close()
            dialog.deleteLater()
            QApplication.processEvents()

    def test_self_pid_protection_rejects_process_action(self) -> None:
        with patch("snakesh.ui.resource_monitor_dialog.QTimer.singleShot"):
            dialog = ResourceMonitorDialog()
        try:
            dialog.tabs.setCurrentWidget(dialog.processes_page)
            QApplication.processEvents()
            current = os.getpid()
            dialog._on_process_snapshot_ready(
                ProcessInventorySnapshot(
                    entries=[
                        ProcessEntry(
                            pid=current,
                            name="SnakeSh",
                            cpu_percent=1.0,
                            memory_rss_bytes=10_000_000,
                            threads=8,
                            user="tester",
                            status="running",
                            started_at=1_700_000_000.0,
                            command="python -m snakesh",
                        )
                    ],
                    total_threads=8,
                    collected_at=10.0,
                )
            )
            item = dialog.process_tree.topLevelItem(0)
            item.setSelected(True)
            QApplication.processEvents()

            with patch("snakesh.ui.resource_monitor_dialog.QMessageBox.warning") as mock_warning:
                dialog._start_process_action(force=False)

            mock_warning.assert_called_once()
            self.assertIn("SnakeSh process", dialog.process_status_label.text())
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_gpu_adapter_status_text_explains_windows_counter_only_telemetry(self) -> None:
        adapter = GpuAdapterSample(
            id="intel-0",
            vendor="Intel",
            name="Intel Arc",
            adapter_index=0,
            backend="windows-counters",
            utilization_percent=27.5,
            memory_used_bytes=None,
            memory_total_bytes=None,
            temperature_c=None,
        )

        message = _gpu_adapter_telemetry_status_text(adapter)

        self.assertIn("Windows GPU counters", message)
        self.assertIn("best-effort", message)

    def test_gpu_adapter_status_text_explains_shared_gpu_memory_allocation(self) -> None:
        adapter = GpuAdapterSample(
            id="0000:00:02.0",
            vendor="Intel",
            name="Intel HD Graphics 5500",
            adapter_index=0,
            backend="linux-drm-fdinfo",
            utilization_percent=27.5,
            memory_used_bytes=304 * 1024 * 1024,
            memory_total_bytes=304 * 1024 * 1024,
            memory_total_is_capacity=False,
            memory_kind="shared",
            temperature_c=None,
        )

        message = _gpu_adapter_telemetry_status_text(adapter)

        self.assertIn("shared allocation", message)
        self.assertIn("dedicated VRAM capacity", message)


if __name__ == "__main__":
    unittest.main()
