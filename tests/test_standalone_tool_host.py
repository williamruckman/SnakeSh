from __future__ import annotations

import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication, QDialog

from snakesh.core.tool_registry import TOOL_REGISTRY
from snakesh.services.network_inspector import InterfaceAddress, InterfaceInfo
from snakesh.services.settings_service import AppSettings
from snakesh.ui.standalone_tool_host import (
    StandaloneToolController,
    _activate_tool_window,
    _tool_app_user_model_id,
    _tool_qapplication_arguments,
    _tool_desktop_file_name,
    run_standalone_tool,
    supported_standalone_tool_keys,
)
from snakesh.ui.window_placement import WindowPlacement


class _StubSettingsService:
    def __init__(self, settings: AppSettings | None = None) -> None:
        self._settings = AppSettings.from_dict((settings or AppSettings.defaults()).to_dict())
        self.save_calls = 0

    def load(self) -> AppSettings:
        return AppSettings.from_dict(self._settings.to_dict())

    def save(self, settings: AppSettings) -> None:
        self.save_calls += 1
        self._settings = AppSettings.from_dict(settings.to_dict())


class _BridgeProbe:
    instances: list["_BridgeProbe"] = []

    def __init__(self, on_activate) -> None:
        self._on_activate = on_activate
        self.emitted: list[dict[str, object] | None] = []
        self.activation_requested = MagicMock()
        self.activation_requested.emit.side_effect = self._emit
        _BridgeProbe.instances.append(self)

    @classmethod
    def reset(cls) -> None:
        cls.instances = []

    def _emit(self, payload) -> None:
        self.emitted.append(payload if isinstance(payload, dict) else None)

    def dispatch_pending(self) -> None:
        pending = list(self.emitted)
        self.emitted.clear()
        for payload in pending:
            self._on_activate(payload)


class StandaloneToolHostTests(unittest.TestCase):
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
                    InterfaceAddress(family="IPv4", address="192.0.2.20"),
                    InterfaceAddress(family="IPv6", address="fe80::20"),
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

    def _cleanup_dialog(self, dialog) -> None:
        poll_timer = getattr(dialog, "_poll_timer", None)
        if poll_timer is not None:
            poll_timer.stop()
        dialog.close()
        dialog.deleteLater()
        QApplication.processEvents()

    def test_password_generator_dialog_loads_and_persists_settings(self) -> None:
        settings = AppSettings.defaults()
        settings.password_generator_length = 28
        settings.password_generator_count = 9
        settings.password_generator_complexity = "Maximum"
        settings.password_generator_include_symbols = False
        settings.password_generator_include_characters = "@_"
        settings.password_generator_exclude_characters = "O0Il"
        settings_service = _StubSettingsService(settings)
        controller = StandaloneToolController(settings_service=settings_service, settings=settings_service.load())

        dialog = controller.create_dialog("password_generator")
        try:
            self.assertEqual(dialog.length_input.value(), 28)
            self.assertEqual(dialog.count_input.value(), 9)
            self.assertEqual(dialog.complexity_input.currentText(), "Maximum")
            self.assertFalse(dialog.symbols_input.isChecked())
            self.assertEqual(dialog.include_characters_input.text(), "@_")
            self.assertEqual(dialog.exclude_characters_input.text(), "O0Il")

            dialog.length_input.setValue(31)
            dialog.exclude_characters_input.setText("xyz")
            QApplication.processEvents()

            self.assertEqual(settings_service._settings.password_generator_length, 31)
            self.assertEqual(settings_service._settings.password_generator_exclude_characters, "xyz")
            self.assertGreaterEqual(settings_service.save_calls, 2)
        finally:
            self._cleanup_dialog(dialog)

    def test_controller_preview_runtime_settings_do_not_mutate_persisted_base(self) -> None:
        settings = AppSettings.defaults()
        settings.accent_color = "#0f766e"
        controller = StandaloneToolController(settings_service=_StubSettingsService(settings), settings=settings)

        preview = AppSettings.from_dict(settings.to_dict())
        preview.accent_color = "#123456"

        effective = controller.apply_runtime_settings(preview, preview=True)

        self.assertEqual(effective.accent_color, "#123456")
        self.assertEqual(controller.settings.accent_color, "#123456")
        self.assertEqual(controller.persisted_settings.accent_color, "#0f766e")

    def test_controller_committed_runtime_settings_updates_persisted_base(self) -> None:
        settings = AppSettings.defaults()
        settings.accent_color = "#0f766e"
        controller = StandaloneToolController(settings_service=_StubSettingsService(settings), settings=settings)

        committed = AppSettings.from_dict(settings.to_dict())
        committed.accent_color = "#abcdef"

        effective = controller.apply_runtime_settings(committed, preview=False)

        self.assertEqual(effective.accent_color, "#abcdef")
        self.assertEqual(controller.settings.accent_color, "#abcdef")
        self.assertEqual(controller.persisted_settings.accent_color, "#abcdef")

    def test_controller_restores_saved_tool_window_placement_before_launch_placement(self) -> None:
        settings = AppSettings.defaults()
        settings.standalone_tool_window_placements = {
            "help": {
                "geometry_b64": "",
                "screen_name": "Saved Screen",
                "screen_serial": "SAVED",
                "frame_rect": [100, 120, 700, 500],
            }
        }
        controller = StandaloneToolController(settings_service=_StubSettingsService(settings), settings=settings)
        launch_placement = WindowPlacement(screen_name="Launch Screen", frame_rect=QRect(400, 400, 800, 600))

        with patch("snakesh.ui.standalone_tool_host.restore_or_defer_window_placement") as mock_restore:
            dialog = controller.create_dialog("help", launch_placement=launch_placement)
            try:
                placement = mock_restore.call_args.args[1]
                self.assertEqual(placement.screen_name, "Saved Screen")
                self.assertEqual(placement.screen_serial, "SAVED")
            finally:
                self._cleanup_dialog(dialog)

    def test_controller_saves_tool_window_placement_to_settings(self) -> None:
        settings_service = _StubSettingsService()
        controller = StandaloneToolController(settings_service=settings_service, settings=settings_service.load())
        dialog = QDialog()
        try:
            dialog.resize(640, 480)
            dialog.show()
            QApplication.processEvents()

            controller._save_tool_window_placement("help", dialog)

            self.assertIn("help", settings_service._settings.standalone_tool_window_placements)
            placement = settings_service._settings.standalone_tool_window_placements["help"]
            self.assertTrue(placement["geometry_b64"] or placement["frame_rect"])
        finally:
            dialog.close()
            dialog.deleteLater()
            QApplication.processEvents()

    def test_ping_dialog_prefill_is_applied_from_controller(self) -> None:
        controller = StandaloneToolController(settings_service=_StubSettingsService())

        dialog = controller.create_dialog("ping", ping_packet_size=1472, ping_ipv6=True)
        try:
            self.assertEqual(dialog.size_input.value(), 1472)
            self.assertTrue(dialog.ipv6_check.isChecked())
        finally:
            self._cleanup_dialog(dialog)

    def test_diff_dialog_uses_terminal_font_settings(self) -> None:
        fixed_pitch_families = [
            name for name in QFontDatabase.families() if QFontDatabase.isFixedPitch(name) and name.lower() != "fixedsys"
        ]
        if not fixed_pitch_families:
            self.skipTest("No fixed-pitch fonts available in test environment")

        settings = AppSettings.defaults()
        settings.terminal_font_family = fixed_pitch_families[0]
        settings.terminal_font_pt = 13
        controller = StandaloneToolController(settings_service=_StubSettingsService(settings), settings=settings)

        dialog = controller.create_dialog("diff")
        try:
            self.assertEqual(dialog._left_editor.font().family().lower(), settings.terminal_font_family.lower())
            self.assertEqual(dialog._right_editor.font().family().lower(), settings.terminal_font_family.lower())
            self.assertEqual(dialog._left_editor.font().pointSize(), 13)
            self.assertEqual(dialog._right_editor.font().pointSize(), 13)
        finally:
            self._cleanup_dialog(dialog)

    def test_mtu_send_to_ping_launches_detached_ping_with_prefill_args(self) -> None:
        controller = StandaloneToolController(settings_service=_StubSettingsService())

        with patch("snakesh.ui.standalone_tool_host.launch_standalone_tool") as mock_launch:
            dialog = controller.create_dialog("mtu_calculator")
            try:
                dialog.ip_version_input.setCurrentIndex(1)
                QApplication.processEvents()
                dialog.send_to_ping_btn.click()
                QApplication.processEvents()
            finally:
                self._cleanup_dialog(dialog)

        mock_launch.assert_called_once_with(
            "ping",
            arguments=["--packet-size", "1452", "--ipv6"],
        )

    def test_web_server_dialog_uses_settings_backed_profiles_and_splitter_state(self) -> None:
        settings = AppSettings.defaults()
        settings.web_server_profiles = [
            {
                "id": "web-1",
                "name": "Docs",
                "config": {
                    "bind_host": "127.0.0.1",
                    "port": 8080,
                    "mode": "static",
                    "document_root": "/tmp/docs",
                },
            }
        ]
        settings.last_web_server_profile_id = "web-1"
        settings_service = _StubSettingsService(settings)
        controller = StandaloneToolController(settings_service=settings_service, settings=settings_service.load())

        with tempfile.TemporaryDirectory() as tmp:
            instance_dir = Path(tmp) / "web-instance"
            instance_dir.mkdir()
            with (
                patch("snakesh.ui.web_server_dialog.create_web_server_instance_dir", return_value=instance_dir),
                patch(
                    "snakesh.ui.web_server_dialog.collect_interface_info",
                    return_value=self._interface_inventory(),
                ),
            ):
                dialog = controller.create_dialog("web_server")
                try:
                    dialog.show()
                    QApplication.processEvents()
                    self.assertEqual(dialog.profile_input.currentData(), "web-1")
                    self.assertEqual(dialog.bind_host_input.text(), "127.0.0.1")
                    self.assertEqual(dialog.port_input.value(), 8080)

                    dialog.port_input.setValue(8443)
                    dialog._update_selected_profile()
                    dialog._persist_splitter_state()

                    self.assertEqual(
                        settings_service._settings.web_server_profiles[0]["config"]["port"],
                        8443,
                    )
                    self.assertEqual(settings_service._settings.last_web_server_profile_id, "web-1")
                    self.assertTrue(settings_service._settings.web_server_dialog_splitter_b64)
                finally:
                    self._cleanup_dialog(dialog)

    def test_syslog_snmp_monitor_dialog_uses_settings_backed_profiles_and_splitter_state(self) -> None:
        settings = AppSettings.defaults()
        settings.syslog_snmp_monitor_profiles = [
            {
                "id": "monitor-1",
                "name": "SOC",
                "config": {
                    "bind_host": "127.0.0.1",
                    "syslog_udp_enabled": True,
                    "syslog_udp_port": 1514,
                },
            }
        ]
        settings.last_syslog_snmp_monitor_profile_id = "monitor-1"
        settings_service = _StubSettingsService(settings)
        controller = StandaloneToolController(settings_service=settings_service, settings=settings_service.load())

        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch("snakesh.services.syslog_snmp_monitor.data_dir", return_value=Path(tmp)),
                patch(
                    "snakesh.ui.syslog_snmp_monitor_dialog.collect_interface_info",
                    return_value=self._interface_inventory(),
                ),
            ):
                dialog = controller.create_dialog("syslog_snmp_monitor")
                try:
                    dialog.show()
                    QApplication.processEvents()
                    self.assertEqual(dialog.profile_input.currentData(), "monitor-1")
                    self.assertEqual(dialog.bind_host_input.text(), "127.0.0.1")
                    self.assertEqual(dialog.syslog_udp_port_input.value(), 1514)

                    dialog.syslog_udp_port_input.setValue(2514)
                    dialog._update_selected_profile()
                    dialog._persist_splitter_state()

                    self.assertEqual(
                        settings_service._settings.syslog_snmp_monitor_profiles[0]["config"]["syslog_udp_port"],
                        2514,
                    )
                    self.assertEqual(settings_service._settings.last_syslog_snmp_monitor_profile_id, "monitor-1")
                    self.assertTrue(settings_service._settings.syslog_snmp_monitor_dialog_splitter_b64)
                finally:
                    self._cleanup_dialog(dialog)

    def test_run_standalone_tool_applies_theme_and_honors_master_password_gate(self) -> None:
        fake_settings = AppSettings.defaults()
        fake_service = _StubSettingsService(fake_settings)
        fake_app = MagicMock()
        fake_icon = MagicMock()
        fake_icon.isNull.return_value = True
        fake_lease = MagicMock()

        with (
            patch("snakesh.ui.standalone_tool_host.SettingsService", return_value=fake_service),
            patch("snakesh.ui.standalone_tool_host.QApplication", return_value=fake_app),
            patch("snakesh.ui.standalone_tool_host._maybe_initialize_fault_handler") as mock_fault_handler,
            patch("snakesh.ui.standalone_tool_host._load_app_icon", return_value=fake_icon),
            patch("snakesh.ui.standalone_tool_host.apply_theme") as mock_apply_theme,
            patch(
                "snakesh.ui.standalone_tool_host.claim_tool_instance",
                return_value=MagicMock(activated_existing=False, lease=fake_lease),
            ),
            patch("snakesh.ui.standalone_tool_host._unlock_with_master_password", return_value=False) as mock_unlock,
            patch("snakesh.ui.standalone_tool_host.StandaloneToolController") as mock_controller,
        ):
            exit_code = run_standalone_tool("ping", ping_packet_size=1472, ping_ipv6=True)

        self.assertEqual(exit_code, 0)
        mock_fault_handler.assert_called_once_with(fake_settings)
        mock_apply_theme.assert_called_once_with(fake_app, fake_settings)
        mock_unlock.assert_called_once_with(fake_settings, tool_launch=True)
        mock_controller.assert_not_called()
        fake_lease.release.assert_called_once_with()
        fake_app.exec.assert_not_called()

    def test_run_standalone_tool_starts_diagnostics_with_debug_options_and_releases_lease(self) -> None:
        fake_settings = AppSettings.defaults()
        fake_service = _StubSettingsService(fake_settings)
        fake_app = MagicMock()
        fake_icon = MagicMock()
        fake_icon.isNull.return_value = True
        fake_lease = MagicMock()
        fake_debug_session = MagicMock()
        fake_debug_session.log_path = Path("/tmp/resource-monitor-debug.log")

        with (
            patch("snakesh.ui.standalone_tool_host.SettingsService", return_value=fake_service),
            patch("snakesh.ui.standalone_tool_host.QApplication", return_value=fake_app),
            patch("snakesh.ui.standalone_tool_host._start_debug_session", return_value=fake_debug_session) as mock_start_debug,
            patch("snakesh.ui.standalone_tool_host._stop_debug_session") as mock_stop_debug,
            patch("snakesh.ui.standalone_tool_host._start_ui_hang_watchdog") as mock_start_watchdog,
            patch("snakesh.ui.standalone_tool_host._stop_ui_hang_watchdog") as mock_stop_watchdog,
            patch("snakesh.ui.standalone_tool_host._close_fault_log_handle") as mock_close_fault_log,
            patch("snakesh.ui.standalone_tool_host._maybe_initialize_fault_handler") as mock_fault_handler,
            patch("snakesh.ui.standalone_tool_host._load_app_icon", return_value=fake_icon),
            patch("snakesh.ui.standalone_tool_host.apply_theme"),
            patch(
                "snakesh.ui.standalone_tool_host.claim_tool_instance",
                return_value=MagicMock(activated_existing=False, lease=fake_lease),
            ),
            patch("snakesh.ui.standalone_tool_host._unlock_with_master_password", return_value=False),
            patch("snakesh.ui.standalone_tool_host.StandaloneToolController") as mock_controller,
        ):
            exit_code = run_standalone_tool(
                "resource_monitor",
                debug_level="debug",
                debug_log_file="/tmp/resource-monitor-debug.log",
            )

        self.assertEqual(exit_code, 0)
        mock_start_debug.assert_called_once_with("debug", "/tmp/resource-monitor-debug.log")
        mock_fault_handler.assert_called_once_with(fake_settings, debug_log_path=fake_debug_session.log_path)
        fake_debug_session.install_qt_message_handler.assert_called_once_with()
        mock_start_watchdog.assert_called_once_with(fake_app)
        mock_controller.assert_not_called()
        fake_lease.release.assert_called_once_with()
        mock_stop_watchdog.assert_called_once_with()
        mock_close_fault_log.assert_called_once_with()
        mock_stop_debug.assert_called_once_with(fake_debug_session)

    def test_run_standalone_tool_shows_dialog_and_executes_when_unlocked(self) -> None:
        fake_settings = AppSettings.defaults()
        fake_service = _StubSettingsService(fake_settings)
        fake_app = MagicMock()
        fake_app.exec.return_value = 23
        fake_icon = MagicMock()
        fake_icon.isNull.return_value = True
        fake_dialog = MagicMock()
        fake_controller = MagicMock()
        fake_controller.create_dialog.return_value = fake_dialog
        fake_lease = MagicMock()

        with (
            patch("snakesh.ui.standalone_tool_host.SettingsService", return_value=fake_service),
            patch("snakesh.ui.standalone_tool_host.QApplication", return_value=fake_app) as mock_qapplication,
            patch("snakesh.ui.standalone_tool_host._maybe_initialize_fault_handler"),
            patch("snakesh.ui.standalone_tool_host._load_app_icon", return_value=fake_icon),
            patch("snakesh.ui.standalone_tool_host.apply_theme"),
            patch(
                "snakesh.ui.standalone_tool_host.claim_tool_instance",
                return_value=MagicMock(activated_existing=False, lease=fake_lease),
            ),
            patch("snakesh.ui.standalone_tool_host._unlock_with_master_password", return_value=True),
            patch("snakesh.ui.standalone_tool_host.StandaloneToolController", return_value=fake_controller),
            patch("snakesh.ui.standalone_tool_host._set_macos_tool_dock_icon") as mock_dock_icon,
        ):
            exit_code = run_standalone_tool("ping", ping_packet_size=1400, ping_ipv6=True)

        self.assertEqual(exit_code, 23)
        self.assertEqual(mock_qapplication.call_args.args[0][0], "snakesh-tool-ping")
        fake_app.setApplicationName.assert_called_once_with("snakesh-tool-ping")
        fake_app.setDesktopFileName.assert_called_once_with("snakesh-tool-ping")
        fake_controller.create_dialog.assert_called_once_with(
            "ping",
            ping_packet_size=1400,
            ping_ipv6=True,
            launch_placement=None,
        )
        fake_dialog.setModal.assert_called_once_with(False)
        fake_dialog.show.assert_called_once_with()
        fake_lease.release.assert_called_once_with()
        fake_app.exec.assert_called_once_with()
        mock_dock_icon.assert_called_once_with("ping")

    def test_tool_process_identity_names_are_per_tool(self) -> None:
        self.assertEqual(_tool_desktop_file_name("ping"), "snakesh-tool-ping")
        self.assertEqual(_tool_qapplication_arguments("ping")[0], "snakesh-tool-ping")
        self.assertEqual(_tool_app_user_model_id("ping"), "com.snakesh.tool.ping")

    def test_run_standalone_tool_applies_settings_sync_without_activating_window(self) -> None:
        fake_settings = AppSettings.defaults()
        fake_service = _StubSettingsService(fake_settings)
        fake_app = MagicMock()
        fake_icon = MagicMock()
        fake_icon.isNull.return_value = True
        fake_dialog = MagicMock()
        fake_controller = MagicMock()
        fake_controller.create_dialog.return_value = fake_dialog
        fake_controller.apply_runtime_settings.side_effect = lambda settings, preview: settings
        fake_lease = MagicMock()
        preview = AppSettings.from_dict(fake_settings.to_dict())
        preview.accent_color = "#123456"
        captured_on_activate = {}

        def _claim_tool_instance(*_args, **kwargs):
            captured_on_activate["callback"] = kwargs["on_activate"]
            return MagicMock(activated_existing=False, lease=fake_lease)

        def _exec() -> int:
            activated = captured_on_activate["callback"](
                {
                    "kind": "settings_sync",
                    "preview": True,
                    "settings": preview.to_dict(),
                }
            )
            self.assertTrue(activated)
            return 0

        fake_app.exec.side_effect = _exec

        with (
            patch("snakesh.ui.standalone_tool_host.SettingsService", return_value=fake_service),
            patch("snakesh.ui.standalone_tool_host.QApplication", return_value=fake_app),
            patch("snakesh.ui.standalone_tool_host._maybe_initialize_fault_handler"),
            patch("snakesh.ui.standalone_tool_host._load_app_icon", return_value=fake_icon),
            patch("snakesh.ui.standalone_tool_host.apply_theme") as mock_apply_theme,
            patch("snakesh.ui.standalone_tool_host.claim_tool_instance", side_effect=_claim_tool_instance),
            patch("snakesh.ui.standalone_tool_host._unlock_with_master_password", return_value=True),
            patch("snakesh.ui.standalone_tool_host.StandaloneToolController", return_value=fake_controller),
            patch("snakesh.ui.standalone_tool_host._activate_tool_window") as mock_activate,
        ):
            exit_code = run_standalone_tool("ping", ping_packet_size=1400, ping_ipv6=True)

        self.assertEqual(exit_code, 0)
        mock_activate.assert_not_called()
        fake_controller.apply_runtime_settings.assert_called_once()
        fake_dialog.apply_runtime_settings.assert_called_once_with(preview)
        self.assertEqual(
            mock_apply_theme.call_args_list[-1].args,
            (fake_app, preview),
        )
        fake_lease.release.assert_called_once_with()

    def test_run_standalone_tool_routes_settings_sync_from_background_thread_to_bridge(self) -> None:
        fake_settings = AppSettings.defaults()
        fake_service = _StubSettingsService(fake_settings)
        fake_app = MagicMock()
        fake_icon = MagicMock()
        fake_icon.isNull.return_value = True
        fake_dialog = MagicMock()
        fake_controller = MagicMock()
        fake_controller.create_dialog.return_value = fake_dialog
        fake_controller.apply_runtime_settings.side_effect = lambda settings, preview: settings
        fake_lease = MagicMock()
        preview = AppSettings.from_dict(fake_settings.to_dict())
        preview.accent_color = "#123456"
        captured_on_activate = {}
        _BridgeProbe.reset()

        def _claim_tool_instance(*_args, **kwargs):
            captured_on_activate["callback"] = kwargs["on_activate"]
            return MagicMock(activated_existing=False, lease=fake_lease)

        def _exec() -> int:
            bridge = _BridgeProbe.instances[-1]
            worker_result: list[bool] = []

            def _deliver_settings_sync() -> None:
                worker_result.append(
                    captured_on_activate["callback"](
                        {
                            "kind": "settings_sync",
                            "preview": True,
                            "settings": preview.to_dict(),
                        }
                    )
                )

            worker = threading.Thread(target=_deliver_settings_sync)
            worker.start()
            worker.join(timeout=3.0)

            self.assertEqual(worker_result, [True])
            fake_controller.apply_runtime_settings.assert_not_called()
            self.assertEqual(len(bridge.emitted), 1)

            bridge.dispatch_pending()
            self.assertEqual(fake_controller.apply_runtime_settings.call_count, 1)
            return 0

        fake_app.exec.side_effect = _exec

        with (
            patch("snakesh.ui.standalone_tool_host.SettingsService", return_value=fake_service),
            patch("snakesh.ui.standalone_tool_host.QApplication", return_value=fake_app),
            patch("snakesh.ui.standalone_tool_host._maybe_initialize_fault_handler"),
            patch("snakesh.ui.standalone_tool_host._load_app_icon", return_value=fake_icon),
            patch("snakesh.ui.standalone_tool_host._ToolActivationBridge", side_effect=_BridgeProbe),
            patch("snakesh.ui.standalone_tool_host.apply_theme") as mock_apply_theme,
            patch("snakesh.ui.standalone_tool_host.claim_tool_instance", side_effect=_claim_tool_instance),
            patch("snakesh.ui.standalone_tool_host._unlock_with_master_password", return_value=True),
            patch("snakesh.ui.standalone_tool_host.StandaloneToolController", return_value=fake_controller),
            patch("snakesh.ui.standalone_tool_host._activate_tool_window") as mock_activate,
        ):
            exit_code = run_standalone_tool("diff")

        self.assertEqual(exit_code, 0)
        mock_activate.assert_not_called()
        fake_dialog.apply_runtime_settings.assert_called_once_with(preview)
        self.assertEqual(mock_apply_theme.call_args_list[-1].args, (fake_app, preview))
        fake_lease.release.assert_called_once_with()

    def test_run_standalone_tool_returns_when_existing_ping_instance_was_activated(self) -> None:
        fake_settings = AppSettings.defaults()
        fake_service = _StubSettingsService(fake_settings)
        fake_app = MagicMock()
        fake_icon = MagicMock()
        fake_icon.isNull.return_value = True

        with (
            patch("snakesh.ui.standalone_tool_host.SettingsService", return_value=fake_service),
            patch("snakesh.ui.standalone_tool_host.QApplication", return_value=fake_app),
            patch("snakesh.ui.standalone_tool_host._maybe_initialize_fault_handler"),
            patch("snakesh.ui.standalone_tool_host._load_app_icon", return_value=fake_icon),
            patch("snakesh.ui.standalone_tool_host.apply_theme"),
            patch(
                "snakesh.ui.standalone_tool_host.claim_tool_instance",
                return_value=MagicMock(activated_existing=True, lease=None),
            ) as mock_claim,
            patch("snakesh.ui.standalone_tool_host._unlock_with_master_password") as mock_unlock,
            patch("snakesh.ui.standalone_tool_host.StandaloneToolController") as mock_controller,
        ):
            exit_code = run_standalone_tool("ping", ping_packet_size=1400, ping_ipv6=True)

        self.assertEqual(exit_code, 0)
        mock_claim.assert_called_once()
        mock_unlock.assert_not_called()
        mock_controller.assert_not_called()
        fake_app.exec.assert_not_called()

    def test_activate_tool_window_restores_minimized_target_and_focuses_it(self) -> None:
        target = MagicMock()
        target.isMinimized.return_value = True
        handle = MagicMock()
        target.windowHandle.return_value = handle

        activated = _activate_tool_window(target)

        self.assertTrue(activated)
        target.showNormal.assert_called_once_with()
        target.raise_.assert_called_once_with()
        target.activateWindow.assert_called_once_with()
        handle.requestActivate.assert_called_once_with()

    def test_supported_standalone_tool_keys_match_registry_order(self) -> None:
        self.assertEqual(
            supported_standalone_tool_keys(),
            [entry.key for entry in TOOL_REGISTRY],
        )

    def test_controller_can_build_every_registered_tool(self) -> None:
        settings = AppSettings.defaults()
        settings.web_server_profiles = [
            {
                "id": "web-1",
                "name": "Docs",
                "config": {
                    "bind_host": "127.0.0.1",
                    "port": 8080,
                    "mode": "static",
                    "document_root": "/tmp/docs",
                },
            }
        ]
        settings.last_web_server_profile_id = "web-1"
        settings.syslog_snmp_monitor_profiles = [
            {
                "id": "monitor-1",
                "name": "SOC",
                "config": {
                    "bind_host": "127.0.0.1",
                    "syslog_udp_enabled": True,
                    "syslog_udp_port": 1514,
                },
            }
        ]
        settings.last_syslog_snmp_monitor_profile_id = "monitor-1"
        controller = StandaloneToolController(
            settings_service=_StubSettingsService(settings),
            settings=settings,
        )

        with tempfile.TemporaryDirectory() as tmp:
            instance_dir = Path(tmp) / "web-instance"
            instance_dir.mkdir()
            with (
                patch("snakesh.ui.resource_monitor_dialog.QTimer.singleShot"),
                patch("snakesh.ui.network_inspector_dialog.QTimer.singleShot"),
                patch("snakesh.ui.web_server_dialog.create_web_server_instance_dir", return_value=instance_dir),
                patch(
                    "snakesh.ui.web_server_dialog.collect_interface_info",
                    return_value=self._interface_inventory(),
                ),
                patch("snakesh.services.syslog_snmp_monitor.data_dir", return_value=Path(tmp)),
                patch(
                    "snakesh.ui.syslog_snmp_monitor_dialog.collect_interface_info",
                    return_value=self._interface_inventory(),
                ),
            ):
                for entry in TOOL_REGISTRY:
                    with self.subTest(tool_key=entry.key):
                        dialog = controller.create_dialog(entry.key)
                        try:
                            self.assertIsNotNone(dialog)
                            flags = dialog.windowFlags()
                            self.assertEqual(dialog.windowType(), Qt.WindowType.Window)
                            self.assertTrue(bool(flags & Qt.WindowType.WindowSystemMenuHint))
                            self.assertTrue(bool(flags & Qt.WindowType.WindowMinimizeButtonHint))
                            self.assertTrue(bool(flags & Qt.WindowType.WindowMaximizeButtonHint))
                            self.assertTrue(bool(flags & Qt.WindowType.WindowCloseButtonHint))
                        finally:
                            self._cleanup_dialog(dialog)


if __name__ == "__main__":
    unittest.main()
