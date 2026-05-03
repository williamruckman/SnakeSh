from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import sys
from typing import Callable

from PySide6.QtCore import QObject, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QDialog, QWidget

from snakesh.app import (
    APP_NAME,
    APP_ORGANIZATION,
    _close_fault_log_handle,
    _load_app_icon,
    _maybe_initialize_fault_handler,
    _start_debug_session,
    _start_ui_hang_watchdog,
    _stop_debug_session,
    _stop_ui_hang_watchdog,
    _unlock_with_master_password,
)
from snakesh.core.tool_icons import tool_icon_path
from snakesh.core.tool_registry import TOOL_REGISTRY, TOOL_REGISTRY_BY_KEY
from snakesh.services.settings_service import AppSettings, SettingsService
from snakesh.services.tool_instance_service import ToolInstanceClaimResult, claim_tool_instance, tool_activation_payload
from snakesh.services.tool_process_service import launch_standalone_tool, ping_tool_arguments
from snakesh.ui.diff_tool_dialog import DiffToolDialog
from snakesh.ui.file_hash_dialog import FileHashDialog
from snakesh.ui.help_dialog import HelpDialog
from snakesh.ui.mtu_calculator_dialog import MTUCalculatorDialog
from snakesh.ui.network_inspector_dialog import NetworkInspectorDialog
from snakesh.ui.network_tools_dialog import (
    ASNLookupDialog,
    DigToolDialog,
    IPScanDialog,
    PingToolDialog,
    TracerouteToolDialog,
    WhoisToolDialog,
)
from snakesh.ui.oui_lookup_dialog import OUILookupDialog
from snakesh.ui.password_generator_dialog import PasswordGeneratorDialog, PasswordOptions
from snakesh.ui.resource_monitor_dialog import ResourceMonitorDialog
from snakesh.ui.subnet_calculator_dialog import SubnetCalculatorDialog
from snakesh.ui.syslog_snmp_monitor_dialog import SyslogSnmpMonitorDialog
from snakesh.ui.theme import apply_theme
from snakesh.ui.web_server_dialog import WebServerDialog
from snakesh.ui.window_placement import (
    WindowPlacement,
    apply_pending_window_placement,
    capture_window_placement,
    placement_from_payload,
    placement_to_payload,
    restore_or_defer_window_placement,
)


_TEST_TOOL_READY_FILE_ENV = "SNAKESH_TEST_TOOL_READY_FILE"
_TEST_TOOL_AUTO_CLOSE_MS_ENV = "SNAKESH_TEST_TOOL_AUTO_CLOSE_MS"
_TOOL_LAUNCH_PLACEMENT_ENV = "SNAKESH_TOOL_LAUNCH_PLACEMENT"
_LOGGER = logging.getLogger(__name__)
StandaloneToolFactory = Callable[["StandaloneToolController", int | None, bool], QDialog]


class _ToolActivationBridge(QObject):
    activation_requested = Signal(object)

    def __init__(self, on_activate: Callable[[dict[str, object] | None], None]) -> None:
        super().__init__()
        self._on_activate = on_activate
        self.activation_requested.connect(self._dispatch_activation)

    @Slot(object)
    def _dispatch_activation(self, payload: object) -> None:
        request_payload = payload if isinstance(payload, dict) else None
        self._on_activate(request_payload)


class StandaloneToolController:
    def __init__(
        self,
        *,
        settings_service: SettingsService | None = None,
        settings: AppSettings | None = None,
    ) -> None:
        self._settings_service = settings_service or SettingsService()
        loaded = AppSettings.from_dict((settings or self._settings_service.load()).to_dict())
        self._persisted_settings = AppSettings.from_dict(loaded.to_dict())
        self._settings = AppSettings.from_dict(loaded.to_dict())

    @property
    def settings(self) -> AppSettings:
        return self._settings

    @property
    def persisted_settings(self) -> AppSettings:
        return self._persisted_settings

    def create_dialog(
        self,
        tool_key: str,
        *,
        ping_packet_size: int | None = None,
        ping_ipv6: bool = False,
        launch_placement: WindowPlacement | None = None,
    ) -> QDialog:
        if tool_key not in TOOL_REGISTRY_BY_KEY:
            raise KeyError(tool_key)
        _assert_standalone_factory_registry_parity()
        factory = _STANDALONE_TOOL_FACTORIES.get(tool_key)
        if factory is None:  # pragma: no cover - parity check above guards this
            raise KeyError(tool_key)
        dialog = factory(self, ping_packet_size, ping_ipv6)
        apply_runtime_settings = getattr(dialog, "apply_runtime_settings", None)
        if callable(apply_runtime_settings):
            apply_runtime_settings(self._settings)
        _configure_standalone_tool_window(dialog)

        restore_saved_geometry = getattr(dialog, "restore_saved_geometry", None)
        if callable(restore_saved_geometry):
            restore_saved_geometry()
        placement = self._tool_window_placement(tool_key) or launch_placement
        restore_or_defer_window_placement(dialog, placement)
        return dialog

    def _save_settings(self) -> None:
        self._settings_service.save(self._persisted_settings)

    def _tool_window_placement(self, tool_key: str) -> WindowPlacement | None:
        raw = self._settings.standalone_tool_window_placements.get(tool_key)
        return placement_from_payload(raw)

    def _save_tool_window_placement(self, tool_key: str, dialog: QDialog | None) -> None:
        if dialog is None:
            return
        placement = capture_window_placement(dialog)
        if not placement.has_data():
            return
        payload = placement_to_payload(placement)
        for target in (self._persisted_settings, self._settings):
            placements = dict(target.standalone_tool_window_placements)
            placements[tool_key] = payload
            target.standalone_tool_window_placements = placements
        self._save_settings()

    def _save_resource_monitor_settings(self, settings: AppSettings) -> None:
        for target in (self._persisted_settings, self._settings):
            target.resource_monitor_show_offline_adapters = settings.resource_monitor_show_offline_adapters
            target.resource_monitor_zoom_percent = settings.resource_monitor_zoom_percent
            target.resource_monitor_sample_refresh_ms = settings.resource_monitor_sample_refresh_ms
            target.resource_monitor_process_refresh_ms = settings.resource_monitor_process_refresh_ms
            target.resource_monitor_history_minutes = settings.resource_monitor_history_minutes
        self._save_settings()

    def apply_runtime_settings(self, settings: AppSettings, *, preview: bool) -> AppSettings:
        runtime_settings = AppSettings.from_dict(settings.to_dict())
        self._settings = runtime_settings
        if not preview:
            self._persisted_settings = AppSettings.from_dict(runtime_settings.to_dict())
        return self._settings

    def _password_generator_options_from_settings(self) -> PasswordOptions:
        return PasswordOptions(
            length=self._settings.password_generator_length,
            count=self._settings.password_generator_count,
            complexity=self._settings.password_generator_complexity,
            include_lower=self._settings.password_generator_include_lower,
            include_upper=self._settings.password_generator_include_upper,
            include_digits=self._settings.password_generator_include_digits,
            include_symbols=self._settings.password_generator_include_symbols,
            include_characters=self._settings.password_generator_include_characters,
            exclude_characters=self._settings.password_generator_exclude_characters,
        )

    def _save_password_generator_options(self, options: PasswordOptions) -> None:
        for target in (self._persisted_settings, self._settings):
            target.password_generator_length = options.length
            target.password_generator_count = options.count
            target.password_generator_complexity = options.complexity
            target.password_generator_include_lower = options.include_lower
            target.password_generator_include_upper = options.include_upper
            target.password_generator_include_digits = options.include_digits
            target.password_generator_include_symbols = options.include_symbols
            target.password_generator_include_characters = options.include_characters
            target.password_generator_exclude_characters = options.exclude_characters
        self._save_settings()

    def _web_server_profile_entries(self) -> list[dict[str, object]]:
        entries: list[dict[str, object]] = []
        for raw in self._settings.web_server_profiles:
            if not isinstance(raw, dict):
                continue
            profile_id = str(raw.get("id", "")).strip()
            name = str(raw.get("name", "")).strip()
            config = raw.get("config")
            if not profile_id or not name or not isinstance(config, dict):
                continue
            entries.append(
                {
                    "id": profile_id,
                    "name": name,
                    "config": SettingsService._sanitize_web_server_profile_config(config),
                }
            )
        return entries

    def _save_web_server_profiles(self, profiles: list[dict[str, object]], selected_profile_id: str) -> None:
        cleaned: list[dict[str, object]] = []
        seen_ids: set[str] = set()
        for profile in profiles:
            if not isinstance(profile, dict):
                continue
            profile_id = str(profile.get("id", "")).strip()
            name = str(profile.get("name", "")).strip()
            config = profile.get("config")
            if not profile_id or not name or not isinstance(config, dict):
                continue
            if profile_id in seen_ids:
                continue
            seen_ids.add(profile_id)
            cleaned.append(
                {
                    "id": profile_id,
                    "name": name,
                    "config": SettingsService._sanitize_web_server_profile_config(config),
                }
            )
        selected_id = selected_profile_id.strip() if selected_profile_id in seen_ids else ""
        for target in (self._persisted_settings, self._settings):
            target.web_server_profiles = list(cleaned)
            target.last_web_server_profile_id = selected_id
        self._save_settings()

    def _save_web_server_dialog_splitter_state(self, splitter_state_b64: str) -> None:
        encoded = splitter_state_b64.strip()
        if encoded == self._persisted_settings.web_server_dialog_splitter_b64:
            return
        self._persisted_settings.web_server_dialog_splitter_b64 = encoded
        self._settings.web_server_dialog_splitter_b64 = encoded
        self._save_settings()

    def _syslog_snmp_monitor_profile_entries(self) -> list[dict[str, object]]:
        entries: list[dict[str, object]] = []
        for raw in self._settings.syslog_snmp_monitor_profiles:
            if not isinstance(raw, dict):
                continue
            profile_id = str(raw.get("id", "")).strip()
            name = str(raw.get("name", "")).strip()
            config = raw.get("config")
            if not profile_id or not name or not isinstance(config, dict):
                continue
            entries.append(
                {
                    "id": profile_id,
                    "name": name,
                    "config": SettingsService._sanitize_syslog_snmp_monitor_profile_config(config),
                }
            )
        return entries

    def _save_syslog_snmp_monitor_profiles(
        self,
        profiles: list[dict[str, object]],
        selected_profile_id: str,
    ) -> None:
        cleaned: list[dict[str, object]] = []
        seen_ids: set[str] = set()
        for profile in profiles:
            if not isinstance(profile, dict):
                continue
            profile_id = str(profile.get("id", "")).strip()
            name = str(profile.get("name", "")).strip()
            config = profile.get("config")
            if not profile_id or not name or not isinstance(config, dict):
                continue
            if profile_id in seen_ids:
                continue
            seen_ids.add(profile_id)
            cleaned.append(
                {
                    "id": profile_id,
                    "name": name,
                    "config": SettingsService._sanitize_syslog_snmp_monitor_profile_config(config),
                }
            )
        selected_id = selected_profile_id.strip() if selected_profile_id in seen_ids else ""
        for target in (self._persisted_settings, self._settings):
            target.syslog_snmp_monitor_profiles = list(cleaned)
            target.last_syslog_snmp_monitor_profile_id = selected_id
        self._save_settings()

    def _save_syslog_snmp_monitor_dialog_splitter_state(self, splitter_state_b64: str) -> None:
        encoded = splitter_state_b64.strip()
        if encoded == self._persisted_settings.syslog_snmp_monitor_dialog_splitter_b64:
            return
        self._persisted_settings.syslog_snmp_monitor_dialog_splitter_b64 = encoded
        self._settings.syslog_snmp_monitor_dialog_splitter_b64 = encoded
        self._save_settings()

    def _launch_ping_from_mtu(self, packet_size: int, ipv6: bool) -> None:
        launch_standalone_tool(
            "ping",
            arguments=ping_tool_arguments(packet_size=packet_size, ipv6=ipv6),
        )


def _build_resource_monitor_dialog(
    controller: StandaloneToolController,
    _ping_packet_size: int | None,
    _ping_ipv6: bool,
) -> QDialog:
    return ResourceMonitorDialog(
        settings=controller.settings,
        on_settings_changed=controller._save_resource_monitor_settings,
    )


def _build_network_inspector_dialog(
    _controller: StandaloneToolController,
    _ping_packet_size: int | None,
    _ping_ipv6: bool,
) -> QDialog:
    return NetworkInspectorDialog()


def _build_whois_dialog(
    _controller: StandaloneToolController,
    _ping_packet_size: int | None,
    _ping_ipv6: bool,
) -> QDialog:
    return WhoisToolDialog()


def _build_asn_lookup_dialog(
    _controller: StandaloneToolController,
    _ping_packet_size: int | None,
    _ping_ipv6: bool,
) -> QDialog:
    return ASNLookupDialog()


def _build_dig_dialog(
    _controller: StandaloneToolController,
    _ping_packet_size: int | None,
    _ping_ipv6: bool,
) -> QDialog:
    return DigToolDialog()


def _build_traceroute_dialog(
    _controller: StandaloneToolController,
    _ping_packet_size: int | None,
    _ping_ipv6: bool,
) -> QDialog:
    return TracerouteToolDialog()


def _build_ping_dialog(
    _controller: StandaloneToolController,
    ping_packet_size: int | None,
    ping_ipv6: bool,
) -> QDialog:
    dialog = PingToolDialog()
    dialog.apply_prefill(packet_size=ping_packet_size, ipv6=ping_ipv6)
    return dialog


def _build_ip_scan_dialog(
    _controller: StandaloneToolController,
    _ping_packet_size: int | None,
    _ping_ipv6: bool,
) -> QDialog:
    return IPScanDialog()


def _build_mtu_calculator_dialog(
    controller: StandaloneToolController,
    _ping_packet_size: int | None,
    _ping_ipv6: bool,
) -> QDialog:
    return MTUCalculatorDialog(on_send_to_ping=controller._launch_ping_from_mtu)


def _build_file_hash_dialog(
    _controller: StandaloneToolController,
    _ping_packet_size: int | None,
    _ping_ipv6: bool,
) -> QDialog:
    return FileHashDialog()


def _build_oui_lookup_dialog(
    _controller: StandaloneToolController,
    _ping_packet_size: int | None,
    _ping_ipv6: bool,
) -> QDialog:
    return OUILookupDialog()


def _build_web_server_dialog(
    controller: StandaloneToolController,
    _ping_packet_size: int | None,
    _ping_ipv6: bool,
) -> QDialog:
    return WebServerDialog(
        profiles=controller._web_server_profile_entries(),
        selected_profile_id=controller.settings.last_web_server_profile_id,
        on_profiles_changed=controller._save_web_server_profiles,
        log_cleanup_enabled=controller.settings.web_server_log_cleanup_enabled,
        log_retention_days=controller.settings.web_server_log_retention_days,
        splitter_state_b64=controller.settings.web_server_dialog_splitter_b64,
        on_splitter_state_changed=controller._save_web_server_dialog_splitter_state,
    )


def _build_syslog_snmp_monitor_dialog(
    controller: StandaloneToolController,
    _ping_packet_size: int | None,
    _ping_ipv6: bool,
) -> QDialog:
    return SyslogSnmpMonitorDialog(
        profiles=controller._syslog_snmp_monitor_profile_entries(),
        selected_profile_id=controller.settings.last_syslog_snmp_monitor_profile_id,
        on_profiles_changed=controller._save_syslog_snmp_monitor_profiles,
        splitter_state_b64=controller.settings.syslog_snmp_monitor_dialog_splitter_b64,
        on_splitter_state_changed=controller._save_syslog_snmp_monitor_dialog_splitter_state,
        settings=controller.settings,
    )


def _build_subnet_calculator_dialog(
    _controller: StandaloneToolController,
    _ping_packet_size: int | None,
    _ping_ipv6: bool,
) -> QDialog:
    return SubnetCalculatorDialog()


def _build_password_generator_dialog(
    controller: StandaloneToolController,
    _ping_packet_size: int | None,
    _ping_ipv6: bool,
) -> QDialog:
    return PasswordGeneratorDialog(
        initial_options=controller._password_generator_options_from_settings(),
        on_options_changed=controller._save_password_generator_options,
    )


def _build_diff_tool_dialog(
    controller: StandaloneToolController,
    _ping_packet_size: int | None,
    _ping_ipv6: bool,
) -> QDialog:
    return DiffToolDialog(settings=controller.settings)


def _build_help_dialog(
    _controller: StandaloneToolController,
    _ping_packet_size: int | None,
    _ping_ipv6: bool,
) -> QDialog:
    return HelpDialog()


_STANDALONE_TOOL_FACTORIES: dict[str, StandaloneToolFactory] = {
    "resource_monitor": _build_resource_monitor_dialog,
    "network_inspector": _build_network_inspector_dialog,
    "whois": _build_whois_dialog,
    "asn_lookup": _build_asn_lookup_dialog,
    "dig": _build_dig_dialog,
    "traceroute": _build_traceroute_dialog,
    "ping": _build_ping_dialog,
    "ip_scan": _build_ip_scan_dialog,
    "mtu_calculator": _build_mtu_calculator_dialog,
    "file_hash": _build_file_hash_dialog,
    "oui_lookup": _build_oui_lookup_dialog,
    "web_server": _build_web_server_dialog,
    "syslog_snmp_monitor": _build_syslog_snmp_monitor_dialog,
    "subnet_calculator": _build_subnet_calculator_dialog,
    "password_generator": _build_password_generator_dialog,
    "diff": _build_diff_tool_dialog,
    "help": _build_help_dialog,
}


def _assert_standalone_factory_registry_parity() -> None:
    registry_keys = [entry.key for entry in TOOL_REGISTRY]
    factory_keys = list(_STANDALONE_TOOL_FACTORIES)
    if registry_keys == factory_keys:
        return

    registry_key_set = set(registry_keys)
    factory_key_set = set(factory_keys)
    missing = [key for key in registry_keys if key not in factory_key_set]
    extra = [key for key in factory_keys if key not in registry_key_set]
    details: list[str] = []
    if missing:
        details.append(f"missing: {', '.join(missing)}")
    if extra:
        details.append(f"extra: {', '.join(extra)}")
    if not details:
        details.append("ordering differs")
    raise RuntimeError(f"Standalone tool factories do not match TOOL_REGISTRY ({'; '.join(details)})")


def supported_standalone_tool_keys() -> list[str]:
    _assert_standalone_factory_registry_parity()
    return [entry.key for entry in TOOL_REGISTRY]


def _configure_standalone_tool_window(dialog: QDialog) -> None:
    dialog.setWindowFlag(Qt.WindowType.Tool, False)
    dialog.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, False)
    dialog.setWindowFlag(Qt.WindowType.Window, True)
    dialog.setWindowFlag(Qt.WindowType.WindowSystemMenuHint, True)
    dialog.setWindowFlag(Qt.WindowType.WindowMinimizeButtonHint, True)
    dialog.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
    dialog.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, True)
    dialog.setModal(False)
    dialog.setWindowModality(Qt.WindowModality.NonModal)


def _activate_tool_window(target: QWidget | None) -> bool:
    if target is None:
        return False
    try:
        if target.isMinimized():
            target.showNormal()
        else:
            target.show()
        target.raise_()
        target.activateWindow()
        handle = target.windowHandle()
        if handle is not None:
            handle.requestActivate()
        _activate_windows_native_tool_window(target)
    except RuntimeError:
        return False
    return True


def _activate_windows_native_tool_window(target: QWidget) -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        window_handle = int(target.winId())
        if window_handle <= 0:
            return
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        sw_restore = 9
        user32.ShowWindow(window_handle, sw_restore)
        user32.BringWindowToTop(window_handle)
        user32.SetForegroundWindow(window_handle)
    except Exception:
        return


def _current_tool_focus_target(app: QApplication, dialog: QDialog | None) -> QWidget | None:
    if dialog is not None:
        return dialog
    modal = app.activeModalWidget()
    if modal is not None:
        return modal
    active = app.activeWindow()
    if active is not None:
        return active
    top_levels = [widget for widget in app.topLevelWidgets() if widget.isVisible()]
    if top_levels:
        return top_levels[-1]
    return None


def _record_tool_ready_for_tests(tool_key: str) -> None:
    raw_path = os.environ.get(_TEST_TOOL_READY_FILE_ENV, "").strip()
    if not raw_path:
        return
    try:
        path = Path(raw_path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"pid": os.getpid(), "tool_key": tool_key}),
            encoding="utf-8",
        )
    except Exception:
        return


def _test_tool_auto_close_delay_ms() -> int | None:
    raw_value = os.environ.get(_TEST_TOOL_AUTO_CLOSE_MS_ENV, "").strip()
    if not raw_value:
        return None
    try:
        delay_ms = int(raw_value)
    except ValueError:
        return None
    if delay_ms <= 0:
        return None
    return delay_ms


def _schedule_standalone_tool_test_hooks(dialog: QDialog, tool_key: str) -> None:
    QTimer.singleShot(0, lambda key=tool_key: _record_tool_ready_for_tests(key))
    auto_close_delay_ms = _test_tool_auto_close_delay_ms()
    if auto_close_delay_ms is not None:
        QTimer.singleShot(auto_close_delay_ms, dialog.close)


def _launch_placement_from_environment() -> WindowPlacement | None:
    raw = os.environ.get(_TOOL_LAUNCH_PLACEMENT_ENV, "").strip()
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except Exception:
        return None
    return placement_from_payload(payload)


def run_standalone_tool(
    tool_key: str,
    *,
    ping_packet_size: int | None = None,
    ping_ipv6: bool = False,
    debug_level: str | None = None,
    debug_log_file: str | None = None,
) -> int:
    if tool_key not in TOOL_REGISTRY_BY_KEY:
        raise KeyError(tool_key)
    _assert_standalone_factory_registry_parity()

    settings_service = SettingsService()
    settings = settings_service.load()
    debug_session = _start_debug_session(debug_level, debug_log_file)
    if debug_session is not None:
        _maybe_initialize_fault_handler(settings, debug_log_path=debug_session.log_path)
    else:
        _maybe_initialize_fault_handler(settings)
    _set_platform_tool_process_identity(tool_key)

    app_identity = _tool_desktop_file_name(tool_key)
    try:
        app = QApplication(_tool_qapplication_arguments(tool_key))
        app.setApplicationName(app_identity)
        app.setOrganizationName(APP_ORGANIZATION)
        app.setApplicationDisplayName(_tool_launcher_display_name(tool_key))
        if hasattr(app, "setDesktopFileName"):
            app.setDesktopFileName(app_identity)
        if debug_session is not None:
            debug_session.install_qt_message_handler()
            _start_ui_hang_watchdog(app)

        icon = _load_tool_window_icon(tool_key)
        if not icon.isNull():
            app.setWindowIcon(icon)
        apply_theme(app, settings)
        dialog: QDialog | None = None
        controller: StandaloneToolController | None = None
        pending_runtime_settings: tuple[AppSettings, bool] | None = None

        def _apply_settings_sync(runtime_settings: AppSettings, *, preview: bool) -> None:
            nonlocal pending_runtime_settings
            pending_runtime_settings = (runtime_settings, preview)
            if controller is not None and runtime_settings.to_dict() == controller.settings.to_dict():
                return
            if controller is not None:
                effective_settings = controller.apply_runtime_settings(runtime_settings, preview=preview)
                apply_theme(app, effective_settings)
                apply_runtime_settings = getattr(dialog, "apply_runtime_settings", None)
                if callable(apply_runtime_settings):
                    apply_runtime_settings(effective_settings)
            else:
                if runtime_settings.to_dict() == settings.to_dict():
                    return
                apply_theme(app, runtime_settings)

        def _dispatch_activation(payload: dict[str, object] | None) -> None:
            if isinstance(payload, dict) and str(payload.get("kind", "")).strip().lower() == "settings_sync":
                raw_settings = payload.get("settings")
                if not isinstance(raw_settings, dict):
                    return
                try:
                    _apply_settings_sync(AppSettings.from_dict(raw_settings), preview=bool(payload.get("preview", False)))
                except Exception:
                    _LOGGER.exception("Standalone tool settings sync failed for tool_key=%s", tool_key)
                return
            _activate_tool_window(_current_tool_focus_target(app, dialog))

        bridge = _ToolActivationBridge(_dispatch_activation)

        def _handle_activation(payload: dict[str, object] | None) -> bool:
            if isinstance(payload, dict) and str(payload.get("kind", "")).strip().lower() == "settings_sync":
                raw_settings = payload.get("settings")
                if not isinstance(raw_settings, dict):
                    return False
            bridge.activation_requested.emit(payload)
            return True

        claim_result: ToolInstanceClaimResult = claim_tool_instance(
            tool_key,
            on_activate=_handle_activation,
            activation_payload=tool_activation_payload(
                tool_key,
                arguments=(
                    ping_tool_arguments(packet_size=ping_packet_size, ipv6=ping_ipv6)
                    if tool_key == "ping"
                    else None
                ),
            ),
        )
        if claim_result.activated_existing:
            return 0

        lease = claim_result.lease
        if lease is None:  # pragma: no cover - claim result is always one path or the other
            return 0

        try:
            if not _unlock_with_master_password(settings, tool_launch=True):
                return 0

            controller = StandaloneToolController(settings_service=settings_service, settings=settings)
            dialog = controller.create_dialog(
                tool_key,
                ping_packet_size=ping_packet_size,
                ping_ipv6=ping_ipv6,
                launch_placement=_launch_placement_from_environment(),
            )
            dialog.finished.connect(
                lambda _result=0, key=tool_key, target=dialog: controller._save_tool_window_placement(key, target)
            )
            app.aboutToQuit.connect(
                lambda key=tool_key, target=dialog: controller._save_tool_window_placement(key, target)
            )
            if pending_runtime_settings is not None:
                runtime_settings, preview = pending_runtime_settings
                try:
                    _apply_settings_sync(runtime_settings, preview=preview)
                except Exception:
                    _LOGGER.exception("Standalone tool pending settings sync failed for tool_key=%s", tool_key)
            if not icon.isNull():
                dialog.setWindowIcon(icon)
            dialog.setModal(False)
            dialog.show()
            QTimer.singleShot(0, lambda target=dialog: apply_pending_window_placement(target))
            _schedule_standalone_tool_test_hooks(dialog, tool_key)
            return app.exec()
        finally:
            lease.release()
    finally:
        _stop_ui_hang_watchdog()
        _close_fault_log_handle()
        _stop_debug_session(debug_session)


def _tool_launcher_display_name(tool_key: str) -> str:
    return f"{APP_NAME} - {TOOL_REGISTRY_BY_KEY[tool_key].label}"


def _tool_desktop_file_name(tool_key: str) -> str:
    return f"snakesh-tool-{tool_key}"


def _tool_qapplication_arguments(tool_key: str) -> list[str]:
    return [_tool_desktop_file_name(tool_key), *(str(argument) for argument in sys.argv[1:])]


def _tool_app_user_model_id(tool_key: str) -> str:
    return f"com.snakesh.tool.{tool_key}"


def _set_platform_tool_process_identity(tool_key: str) -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        windll = getattr(ctypes, "windll", None)
        shell32 = getattr(windll, "shell32", None)
        setter = getattr(shell32, "SetCurrentProcessExplicitAppUserModelID", None)
        if setter is not None:
            setter(_tool_app_user_model_id(tool_key))
    except Exception:
        return


def _load_tool_window_icon(tool_key: str) -> QIcon:
    icon = QIcon(str(tool_icon_path(tool_key, "png")))
    if not icon.isNull():
        return icon
    return _load_app_icon()
