from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
import json
from pathlib import Path
import platform
import time
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from PySide6.QtCharts import (
    QBarCategoryAxis,
    QBarSeries,
    QBarSet,
    QChart,
    QChartView,
    QPieSeries,
    QValueAxis,
)
from PySide6.QtCore import QByteArray, QTimer, Qt, Slot
from PySide6.QtGui import QAction, QBrush, QCloseEvent, QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from snakesh.services.network_inspector import collect_interface_info
from snakesh.services.settings_service import AppSettings
from snakesh.services.syslog_snmp_monitor import (
    DEFAULT_EVENT_COLUMNS,
    DEFAULT_PROFILE_NAME,
    DEFAULT_SNMP_PORT,
    DEFAULT_SYSLOG_TCP_PORT,
    DEFAULT_SYSLOG_TLS_PORT,
    DEFAULT_SYSLOG_UDP_PORT,
    MonitorAlertRule,
    MonitorQueryFilters,
    MonitorRetentionPolicy,
    MonitorSnmpV3User,
    SyslogSnmpMonitorConfig,
    SyslogSnmpMonitorStatus,
    archive_monitor_events,
    clear_monitor_profile_data,
    export_monitor_events_csv,
    export_monitor_events_json,
    fetch_monitor_event,
    fetch_monitor_events,
    fetch_unshown_notifications,
    launch_syslog_snmp_monitor_helper,
    launch_syslog_snmp_monitor_helper_elevated,
    mark_notifications_shown,
    monitor_storage_stats,
    needs_syslog_snmp_monitor_gui_elevation,
    purge_monitor_archives,
    read_syslog_snmp_monitor_status,
    request_syslog_snmp_monitor_stop,
    syslog_snmp_monitor_profile_paths,
    validate_syslog_snmp_monitor_config,
    write_syslog_snmp_monitor_config,
    write_syslog_snmp_monitor_status,
)
from snakesh.ui.alert_sound import BellSoundPlayer
from snakesh.ui.bind_host_selector import BindHostSelector
from snakesh.ui.desktop_open import open_local_path
from snakesh.ui.theme import apply_terminal_output_font, blend_colors, readable_foreground_color


_EVENT_COLUMNS: list[tuple[str, str]] = [
    ("received_ts", "Received"),
    ("event_ts", "Event Time"),
    ("source", "Source"),
    ("listener", "Listener"),
    ("protocol", "Protocol"),
    ("transport", "Transport"),
    ("severity_name", "Severity"),
    ("facility_name", "Facility"),
    ("syslog_hostname", "Hostname"),
    ("app_name", "App / Tag"),
    ("procid", "PID"),
    ("msgid", "MsgID"),
    ("message_text", "Message"),
    ("snmp_version", "SNMP Ver"),
    ("snmp_security_name", "Security"),
    ("snmp_community", "Community"),
    ("snmp_user", "User"),
    ("notification_oid", "Trap OID"),
    ("enterprise_oid", "Enterprise OID"),
    ("varbind_summary", "Varbinds"),
    ("alerted", "Alerted"),
]
_EVENT_COLUMN_INDEX = {key: index for index, (key, _label) in enumerate(_EVENT_COLUMNS)}
_SEVERITY_NAMES = [
    "",
    "Emergency",
    "Alert",
    "Critical",
    "Error",
    "Warning",
    "Notice",
    "Informational",
    "Debug",
]
_FACILITY_NAMES = [
    "",
    "kernel",
    "user",
    "mail",
    "daemon",
    "auth",
    "syslog",
    "lpr",
    "news",
    "uucp",
    "clock",
    "authpriv",
    "ftp",
    "ntp",
    "audit",
    "alert",
    "clock2",
    "local0",
    "local1",
    "local2",
    "local3",
    "local4",
    "local5",
    "local6",
    "local7",
]
_SNMP_AUTH_PROTOCOLS = ["MD5", "SHA", "SHA224", "SHA256", "SHA384", "SHA512"]
_SNMP_PRIV_PROTOCOLS = ["AES128", "AES192", "AES256", "AES", "DES", "3DES"]
_DISPLAY_TIMEZONE_UTC = "utc"
_DISPLAY_TIMEZONE_SYSTEM = "system"
_RUNTIME_TRANSITION_TIMEOUT_SECONDS = 5.0
_RUNTIME_START_STALE_HINT_SECONDS = 30.0
_TAB_MONITOR = "monitor"
_TAB_SEARCH = "search"
_TAB_ALERTS = "alerts"
_TAB_DASHBOARD = "dashboard"
_TAB_ARCHIVE = "archive"
_TAB_IDS_IN_ORDER = (
    _TAB_MONITOR,
    _TAB_SEARCH,
    _TAB_ALERTS,
    _TAB_DASHBOARD,
    _TAB_ARCHIVE,
)
_LEGACY_TAB_INDEX_TO_ID = {
    0: _TAB_MONITOR,
    1: _TAB_ALERTS,
    2: _TAB_DASHBOARD,
    3: _TAB_ARCHIVE,
}
_RUNTIME_BADGE_STYLES = {
    "running": ("Running", "#166534"),
    "starting": ("Starting", "#92400e"),
    "stopped": ("Stopped", "#b91c1c"),
    "error": ("Error", "#b91c1c"),
}


@dataclass(frozen=True, slots=True)
class _RuntimeState:
    running: bool
    starting: bool
    stopped: bool
    error: bool
    can_stop: bool


class _SnmpV3UserDialog(QDialog):
    def __init__(self, parent=None, *, user: MonitorSnmpV3User | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("SNMPv3 User")
        self.resize(440, 240)
        current = user or MonitorSnmpV3User()

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.username_input = QLineEdit(current.username, self)
        self.auth_protocol_input = QComboBox(self)
        for value in _SNMP_AUTH_PROTOCOLS:
            self.auth_protocol_input.addItem(value, value)
        self._set_combo_value(self.auth_protocol_input, current.auth_protocol)
        self.auth_password_input = QLineEdit(current.auth_password, self)
        self.auth_password_input.setEchoMode(QLineEdit.Password)
        self.priv_protocol_input = QComboBox(self)
        for value in _SNMP_PRIV_PROTOCOLS:
            self.priv_protocol_input.addItem(value, value)
        self._set_combo_value(self.priv_protocol_input, current.priv_protocol)
        self.priv_password_input = QLineEdit(current.priv_password, self)
        self.priv_password_input.setEchoMode(QLineEdit.Password)
        form.addRow("Username", self.username_input)
        form.addRow("Auth Protocol", self.auth_protocol_input)
        form.addRow("Auth Password", self.auth_password_input)
        form.addRow("Priv Protocol", self.priv_protocol_input)
        form.addRow("Priv Password", self.priv_password_input)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _set_combo_value(combo: QComboBox, value: str) -> None:
        index = combo.findData(value.strip().upper())
        if index >= 0:
            combo.setCurrentIndex(index)

    def build_user(self) -> MonitorSnmpV3User:
        return MonitorSnmpV3User(
            username=self.username_input.text().strip(),
            auth_protocol=str(self.auth_protocol_input.currentData() or "SHA"),
            auth_password=self.auth_password_input.text(),
            priv_protocol=str(self.priv_protocol_input.currentData() or "AES128"),
            priv_password=self.priv_password_input.text(),
        )

    def _accept(self) -> None:
        if not self.username_input.text().strip():
            QMessageBox.warning(self, "SNMPv3 User", "Username is required.")
            return
        self.accept()


class _AlertRuleDialog(QDialog):
    def __init__(self, parent=None, *, rule: MonitorAlertRule | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Alert Rule")
        self.resize(520, 360)
        current = rule or MonitorAlertRule(rule_id=uuid4().hex)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.name_input = QLineEdit(current.name, self)
        self.enabled_input = QCheckBox("Rule Enabled", self)
        self.enabled_input.setChecked(current.enabled)
        self.protocol_input = QComboBox(self)
        self.protocol_input.addItem("Any", "any")
        self.protocol_input.addItem("Syslog", "syslog")
        self.protocol_input.addItem("SNMP", "snmp")
        self._set_combo_value(self.protocol_input, current.protocol)
        self.severity_input = QComboBox(self)
        self.severity_input.addItem("(Any)", "")
        for value in _SEVERITY_NAMES[1:]:
            self.severity_input.addItem(value, value)
        self._set_combo_value(self.severity_input, current.severity_at_least)
        self.source_input = QLineEdit(current.source_contains, self)
        self.app_input = QLineEdit(current.app_contains, self)
        self.trap_oid_input = QLineEdit(current.trap_oid_contains, self)
        self.enterprise_oid_input = QLineEdit(current.enterprise_oid_contains, self)
        self.text_input = QLineEdit(current.text_contains, self)
        self.regex_input = QCheckBox("Treat text fields as regex", self)
        self.regex_input.setChecked(current.use_regex)
        self.popup_input = QCheckBox("Show popup", self)
        self.popup_input.setChecked(current.popup)
        self.sound_input = QCheckBox("Play sound", self)
        self.sound_input.setChecked(current.sound)

        form.addRow("Name", self.name_input)
        form.addRow("", self.enabled_input)
        form.addRow("Protocol", self.protocol_input)
        form.addRow("Severity At Least", self.severity_input)
        form.addRow("Source Contains", self.source_input)
        form.addRow("App / Tag Contains", self.app_input)
        form.addRow("Trap OID Contains", self.trap_oid_input)
        form.addRow("Enterprise OID Contains", self.enterprise_oid_input)
        form.addRow("Message / Varbind Text", self.text_input)
        form.addRow("", self.regex_input)
        form.addRow("", self.popup_input)
        form.addRow("", self.sound_input)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._rule_id = current.rule_id or uuid4().hex

    @staticmethod
    def _set_combo_value(combo: QComboBox, value: str) -> None:
        index = combo.findData(value.strip())
        if index >= 0:
            combo.setCurrentIndex(index)

    def build_rule(self) -> MonitorAlertRule:
        return MonitorAlertRule(
            rule_id=self._rule_id,
            name=self.name_input.text().strip() or "Alert Rule",
            enabled=self.enabled_input.isChecked(),
            protocol=str(self.protocol_input.currentData() or "any"),
            severity_at_least=str(self.severity_input.currentData() or ""),
            source_contains=self.source_input.text().strip(),
            app_contains=self.app_input.text().strip(),
            trap_oid_contains=self.trap_oid_input.text().strip(),
            enterprise_oid_contains=self.enterprise_oid_input.text().strip(),
            text_contains=self.text_input.text().strip(),
            use_regex=self.regex_input.isChecked(),
            popup=self.popup_input.isChecked(),
            sound=self.sound_input.isChecked(),
        )

    def _accept(self) -> None:
        if not self.name_input.text().strip():
            QMessageBox.warning(self, "Alert Rule", "Rule name is required.")
            return
        if not self.popup_input.isChecked() and not self.sound_input.isChecked():
            QMessageBox.warning(self, "Alert Rule", "Enable popup, sound, or both.")
            return
        self.accept()


class _EventDetailsDialog(QDialog):
    def __init__(
        self,
        parent=None,
        *,
        title: str,
        summary_text: str,
        raw_text: str,
        json_text: str,
        settings: AppSettings | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(False)
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.setWindowFlag(Qt.WindowType.WindowSystemMenuHint, True)
        self.setWindowFlag(Qt.WindowType.WindowMinimizeButtonHint, True)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, True)
        self.resize(980, 720)

        layout = QVBoxLayout(self)
        self.tabs = QTabWidget(self)
        self.summary_text = QPlainTextEdit(self)
        self.summary_text.setReadOnly(True)
        self.summary_text.setPlainText(summary_text)
        self.raw_text = QPlainTextEdit(self)
        self.raw_text.setReadOnly(True)
        self.raw_text.setPlainText(raw_text)
        self.json_text = QPlainTextEdit(self)
        self.json_text.setReadOnly(True)
        self.json_text.setPlainText(json_text)
        self.tabs.addTab(self.summary_text, "Fields")
        self.tabs.addTab(self.raw_text, "Raw Payload")
        self.tabs.addTab(self.json_text, "JSON")
        layout.addWidget(self.tabs, 1)
        if isinstance(settings, AppSettings):
            self.apply_runtime_settings(settings)

    def apply_runtime_settings(self, settings: AppSettings) -> None:
        for widget in (self.summary_text, self.raw_text, self.json_text):
            apply_terminal_output_font(widget, settings)


class _MonitorAlertsDialog(QDialog):
    def __init__(self, parent=None, *, settings: AppSettings | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Current Alerts")
        self.setModal(False)
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.setWindowFlag(Qt.WindowType.WindowSystemMenuHint, True)
        self.setWindowFlag(Qt.WindowType.WindowMinimizeButtonHint, True)
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, True)
        self.resize(980, 540)
        self._notification_ids: set[int] = set()
        self._open_event_callback = None

        layout = QVBoxLayout(self)
        self.summary_label = QLabel("No current alerts.", self)
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        self.alerts_tree = QTreeWidget(self)
        self.alerts_tree.setRootIsDecorated(False)
        self.alerts_tree.setAlternatingRowColors(True)
        self.alerts_tree.setUniformRowHeights(True)
        self.alerts_tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.alerts_tree.setHeaderLabels(["Received", "Title", "Event ID", "Sound"])
        self.alerts_tree.itemSelectionChanged.connect(self._on_selection_changed)
        self.alerts_tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        layout.addWidget(self.alerts_tree, 1)

        self.body_text = QPlainTextEdit(self)
        self.body_text.setReadOnly(True)
        self.body_text.setPlaceholderText("Select an alert to inspect its message body.")
        layout.addWidget(self.body_text, 1)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        self.dismiss_btn = QPushButton("Dismiss Alerts", self)
        self.dismiss_btn.clicked.connect(self.dismiss_alerts)
        button_row.addWidget(self.dismiss_btn, 0)
        layout.addLayout(button_row)
        if isinstance(settings, AppSettings):
            self.apply_runtime_settings(settings)

    def add_notifications(
        self,
        *,
        profile_name: str,
        notifications: list[dict[str, object]],
        format_timestamp,
        open_event_callback,
    ) -> None:
        self._open_event_callback = open_event_callback
        self.setWindowTitle(f"Current Alerts - {profile_name}")
        added = 0
        for notification in notifications:
            notification_id = int(notification.get("id", 0) or 0)
            if notification_id <= 0 or notification_id in self._notification_ids:
                continue
            self._notification_ids.add(notification_id)
            item = QTreeWidgetItem(
                [
                    format_timestamp(notification.get("created_ts", "")),
                    str(notification.get("title", "")).strip() or "Syslog / SNMP Alert",
                    str(int(notification.get("event_id", 0) or 0)) if int(notification.get("event_id", 0) or 0) > 0 else "-",
                    "Yes" if bool(notification.get("play_sound", False)) else "No",
                ]
            )
            item.setData(0, Qt.UserRole, dict(notification))
            self.alerts_tree.addTopLevelItem(item)
            added += 1
        if added:
            for index in range(self.alerts_tree.columnCount()):
                self.alerts_tree.resizeColumnToContents(index)
            if self.alerts_tree.currentItem() is None and self.alerts_tree.topLevelItemCount() > 0:
                self.alerts_tree.setCurrentItem(self.alerts_tree.topLevelItem(0))
        total = self.alerts_tree.topLevelItemCount()
        self.summary_label.setText(
            f"{total} current alert(s). Double-click an alert to open its event. Use Dismiss Alerts to clear this list."
            if total
            else "No current alerts."
        )
        if total and not self.isVisible():
            self.show()
        if total:
            self.raise_()
            self.activateWindow()

    def dismiss_alerts(self) -> None:
        self._notification_ids.clear()
        self.alerts_tree.clear()
        self.body_text.clear()
        self.summary_label.setText("No current alerts.")
        self.hide()

    def apply_runtime_settings(self, settings: AppSettings) -> None:
        apply_terminal_output_font(self.body_text, settings)

    @Slot()
    def _on_selection_changed(self) -> None:
        item = self.alerts_tree.currentItem()
        if item is None:
            self.body_text.clear()
            return
        raw = item.data(0, Qt.UserRole)
        if not isinstance(raw, dict):
            self.body_text.clear()
            return
        self.body_text.setPlainText(str(raw.get("body", "")).strip())

    @Slot(QTreeWidgetItem, int)
    def _on_item_double_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        raw = item.data(0, Qt.UserRole)
        if not isinstance(raw, dict) or self._open_event_callback is None:
            return
        event_id = int(raw.get("event_id", 0) or 0)
        if event_id > 0:
            self._open_event_callback(event_id)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self.dismiss_alerts()
        event.accept()


class SyslogSnmpMonitorDialog(QDialog):
    def __init__(
        self,
        parent=None,
        *,
        profiles: list[dict[str, object]] | None = None,
        selected_profile_id: str = "",
        on_profiles_changed=None,
        splitter_state_b64: str = "",
        on_splitter_state_changed=None,
        settings: AppSettings | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = AppSettings.from_dict(settings.to_dict()) if isinstance(settings, AppSettings) else None
        self.setWindowTitle("Syslog / SNMP Monitor")
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.resize(1440, 960)
        self._on_profiles_changed = on_profiles_changed
        self._on_splitter_state_changed = on_splitter_state_changed
        self._splitter_state_b64 = splitter_state_b64.strip()
        self._selected_profile_id = selected_profile_id.strip()
        self._profiles = self._normalize_profiles(profiles or [])
        if not self._profiles:
            default_profile = self._new_profile(DEFAULT_PROFILE_NAME, SyslogSnmpMonitorConfig())
            self._profiles = [default_profile]
            self._selected_profile_id = str(default_profile["id"])
        elif not self._selected_profile_id or self._profile_by_id(self._selected_profile_id) is None:
            self._selected_profile_id = str(self._profiles[0].get("id", ""))
        self._alert_rules: list[MonitorAlertRule] = []
        self._snmp_v3_users: list[MonitorSnmpV3User] = []
        self._current_rows: list[dict[str, object]] = []
        self._last_event_column = 0
        self._last_started_signature: tuple[object, ...] = ()
        self._suspend_profile_sync = False
        self._event_detail_windows: list[_EventDetailsDialog] = []
        self._alert_notifications_window: _MonitorAlertsDialog | None = None
        self._alert_sound_player = BellSoundPlayer(self)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        profile_row = QHBoxLayout()
        profile_row.addWidget(QLabel("Profile"), 0)
        self.profile_input = QComboBox(self)
        self.profile_input.setMinimumContentsLength(26)
        self.load_profile_btn = QPushButton("Load", self)
        self.save_profile_btn = QPushButton("Save As...", self)
        self.update_profile_btn = QPushButton("Update", self)
        self.rename_profile_btn = QPushButton("Rename...", self)
        self.delete_profile_btn = QPushButton("Delete", self)
        profile_row.addWidget(self.profile_input, 1)
        profile_row.addWidget(self.load_profile_btn, 0)
        profile_row.addWidget(self.save_profile_btn, 0)
        profile_row.addWidget(self.update_profile_btn, 0)
        profile_row.addWidget(self.rename_profile_btn, 0)
        profile_row.addWidget(self.delete_profile_btn, 0)
        root.addLayout(profile_row)

        action_row = QHBoxLayout()
        self.start_btn = QPushButton("Start", self)
        self.stop_btn = QPushButton("Stop", self)
        self.refresh_btn = QPushButton("Refresh", self)
        self.open_profile_folder_btn = QPushButton("Open Profile Folder", self)
        self.restart_required_label = QLabel("", self)
        self.restart_required_label.setWordWrap(True)
        self.runtime_badge = QLabel(self)
        self.runtime_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.runtime_badge.setMinimumWidth(104)
        action_row.addWidget(self.start_btn, 0)
        action_row.addWidget(self.stop_btn, 0)
        action_row.addWidget(self.refresh_btn, 0)
        action_row.addWidget(self.open_profile_folder_btn, 0)
        action_row.addWidget(self.restart_required_label, 1)
        action_row.addWidget(self.runtime_badge, 0)
        root.addLayout(action_row)
        self._set_runtime_badge("stopped")

        self.tabs = QTabWidget(self)
        root.addWidget(self.tabs, 1)

        self.monitor_page = QWidget(self)
        self.search_page = QWidget(self)
        self.alerts_page = QWidget(self)
        self.dashboard_page = QWidget(self)
        self.archive_page = QWidget(self)
        self.tabs.addTab(self.monitor_page, "Settings")
        self.tabs.addTab(self.search_page, "Monitor")
        self.tabs.addTab(self.alerts_page, "Alerts")
        self.tabs.addTab(self.dashboard_page, "Dashboard")
        self.tabs.addTab(self.archive_page, "Archive / Retention")

        self._build_monitor_tab()
        self._build_search_tab()
        self._build_alerts_tab()
        self._build_dashboard_tab()
        self._build_archive_tab()

        self.status_label = QLabel("Ready.", self)
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(2000)
        self._poll_timer.timeout.connect(self._poll_runtime_state)
        self._pending_runtime_start_profile_id = ""
        self._pending_runtime_start_since = 0.0
        self._pending_runtime_error_message = ""

        self.profile_input.currentIndexChanged.connect(self._on_profile_selection_changed)
        self.load_profile_btn.clicked.connect(self._load_selected_profile)
        self.save_profile_btn.clicked.connect(self._save_current_profile_as)
        self.update_profile_btn.clicked.connect(self._update_selected_profile)
        self.rename_profile_btn.clicked.connect(self._rename_selected_profile)
        self.delete_profile_btn.clicked.connect(self._delete_selected_profile)
        self.start_btn.clicked.connect(self._start_monitor)
        self.stop_btn.clicked.connect(self._stop_monitor)
        self.refresh_btn.clicked.connect(self._poll_runtime_state)
        self.open_profile_folder_btn.clicked.connect(self._open_profile_folder)
        self.archive_now_btn.clicked.connect(self._archive_now)
        self.purge_now_btn.clicked.connect(self._purge_now)
        self.clear_database_btn.clicked.connect(self._clear_database)
        self.clear_filters_btn.clicked.connect(self._clear_filters)
        self.apply_filters_btn.clicked.connect(self._reload_events)
        self.events_tree.itemSelectionChanged.connect(self._on_event_selection_changed)
        self.events_tree.itemClicked.connect(self._on_event_item_clicked)
        self.events_tree.itemDoubleClicked.connect(self._on_event_item_double_clicked)
        self.events_tree.customContextMenuRequested.connect(self._show_event_context_menu)
        self.events_tree.header().sectionClicked.connect(self._set_current_event_column)
        self.events_tree.header().setContextMenuPolicy(Qt.CustomContextMenu)
        self.events_tree.header().customContextMenuRequested.connect(self._show_event_header_context_menu)
        self.display_timezone_input.currentIndexChanged.connect(self._on_display_timezone_changed)
        self.copy_cell_btn.clicked.connect(self._copy_current_cell)
        self.copy_row_btn.clicked.connect(self._copy_current_row)
        self.copy_selected_btn.clicked.connect(self._copy_selected_rows)
        self.copy_column_btn.clicked.connect(self._copy_current_column)
        self.copy_all_btn.clicked.connect(self._copy_all_rows)
        self.export_csv_btn.clicked.connect(self._export_filtered_csv)
        self.export_json_btn.clicked.connect(self._export_filtered_json)
        self.export_selected_btn.clicked.connect(self._export_selected_rows)
        self.columns_btn.clicked.connect(self._show_columns_menu)
        self.add_user_btn.clicked.connect(self._add_snmp_v3_user)
        self.edit_user_btn.clicked.connect(self._edit_selected_snmp_v3_user)
        self.delete_user_btn.clicked.connect(self._delete_selected_snmp_v3_user)
        self.snmp_users_tree.itemSelectionChanged.connect(self._update_snmp_user_buttons)
        self.add_rule_btn.clicked.connect(self._add_alert_rule)
        self.edit_rule_btn.clicked.connect(self._edit_selected_alert_rule)
        self.duplicate_rule_btn.clicked.connect(self._duplicate_selected_alert_rule)
        self.delete_rule_btn.clicked.connect(self._delete_selected_alert_rule)
        self.alert_rules_tree.itemSelectionChanged.connect(self._update_alert_rule_buttons)
        self.tabs.currentChanged.connect(self._persist_ephemeral_profile_state)
        self.event_detail_splitter.splitterMoved.connect(self._persist_ephemeral_profile_state)
        self.search_splitter.splitterMoved.connect(self._persist_splitter_state)

        self._connect_config_signals()
        self._refresh_profile_combo()
        self._load_selected_profile()
        self._last_started_signature = self._runtime_config_signature(self._current_config_preview())
        self._persist_profiles()
        QTimer.singleShot(0, self._restore_search_splitter_state)
        self._poll_runtime_state()
        self._poll_timer.start()
        if isinstance(self._settings, AppSettings):
            self.apply_runtime_settings(self._settings)

    def _build_monitor_tab(self) -> None:
        layout = QVBoxLayout(self.monitor_page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        controls_container = QWidget(self.monitor_page)
        controls_layout = QVBoxLayout(controls_container)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(10)

        self.listener_group = QGroupBox("Listeners", controls_container)
        listener_layout = QGridLayout(self.listener_group)
        listener_layout.setColumnStretch(1, 1)
        listener_layout.setColumnStretch(3, 1)
        self.bind_host_widget = BindHostSelector(
            self.listener_group,
            initial_value="0.0.0.0",
            interface_info_provider=collect_interface_info,
        )
        self.bind_host_input = self.bind_host_widget.value_input
        self.bind_host_preset_input = self.bind_host_widget.preset_input
        self.bind_host_custom_input = self.bind_host_widget.custom_input
        self.syslog_udp_enabled_input = QCheckBox("Enable Syslog UDP", self.listener_group)
        self.syslog_udp_port_input = self._build_port_spinbox(DEFAULT_SYSLOG_UDP_PORT)
        self.syslog_tcp_enabled_input = QCheckBox("Enable Syslog TCP", self.listener_group)
        self.syslog_tcp_port_input = self._build_port_spinbox(DEFAULT_SYSLOG_TCP_PORT)
        self.syslog_tls_enabled_input = QCheckBox("Enable Syslog TLS", self.listener_group)
        self.syslog_tls_port_input = self._build_port_spinbox(DEFAULT_SYSLOG_TLS_PORT)
        self.snmp_enabled_input = QCheckBox("Enable SNMP Traps / Informs", self.listener_group)
        self.snmp_port_input = self._build_port_spinbox(DEFAULT_SNMP_PORT)
        listener_layout.addWidget(QLabel("Bind Address"), 0, 0)
        listener_layout.addWidget(self.bind_host_widget, 0, 1, 1, 3)
        listener_layout.addWidget(self.syslog_udp_enabled_input, 1, 0)
        listener_layout.addWidget(self.syslog_udp_port_input, 1, 1)
        listener_layout.addWidget(self.syslog_tcp_enabled_input, 1, 2)
        listener_layout.addWidget(self.syslog_tcp_port_input, 1, 3)
        listener_layout.addWidget(self.syslog_tls_enabled_input, 2, 0)
        listener_layout.addWidget(self.syslog_tls_port_input, 2, 1)
        listener_layout.addWidget(self.snmp_enabled_input, 2, 2)
        listener_layout.addWidget(self.snmp_port_input, 2, 3)
        controls_layout.addWidget(self.listener_group)

        self.tls_group = QGroupBox("Syslog TLS", controls_container)
        tls_form = QFormLayout(self.tls_group)
        self.syslog_tls_cert_widget, self.syslog_tls_cert_input = self._build_path_input(directory=False)
        self.syslog_tls_key_widget, self.syslog_tls_key_input = self._build_path_input(directory=False)
        self.syslog_tls_ca_widget, self.syslog_tls_ca_input = self._build_path_input(directory=False)
        tls_form.addRow("Certificate File", self.syslog_tls_cert_widget)
        tls_form.addRow("Key File", self.syslog_tls_key_widget)
        tls_form.addRow("CA / Chain File", self.syslog_tls_ca_widget)
        controls_layout.addWidget(self.tls_group)

        self.snmp_group = QGroupBox("SNMP", controls_container)
        snmp_layout = QVBoxLayout(self.snmp_group)
        snmp_flags = QHBoxLayout()
        self.snmp_v1_enabled_input = QCheckBox("v1", self.snmp_group)
        self.snmp_v2c_enabled_input = QCheckBox("v2c", self.snmp_group)
        self.snmp_v3_enabled_input = QCheckBox("v3 / USM", self.snmp_group)
        snmp_flags.addWidget(QLabel("Enable Versions"))
        snmp_flags.addWidget(self.snmp_v1_enabled_input)
        snmp_flags.addWidget(self.snmp_v2c_enabled_input)
        snmp_flags.addWidget(self.snmp_v3_enabled_input)
        snmp_flags.addStretch(1)
        snmp_layout.addLayout(snmp_flags)
        snmp_form = QFormLayout()
        self.snmp_communities_input = QLineEdit("public", self.snmp_group)
        self.snmp_communities_input.setPlaceholderText("public, traps, noc")
        snmp_form.addRow("Allowed Communities", self.snmp_communities_input)
        snmp_layout.addLayout(snmp_form)
        users_row = QHBoxLayout()
        users_row.addWidget(QLabel("SNMPv3 Users"), 0)
        self.add_user_btn = QPushButton("Add...", self.snmp_group)
        self.edit_user_btn = QPushButton("Edit...", self.snmp_group)
        self.delete_user_btn = QPushButton("Delete", self.snmp_group)
        users_row.addStretch(1)
        users_row.addWidget(self.add_user_btn, 0)
        users_row.addWidget(self.edit_user_btn, 0)
        users_row.addWidget(self.delete_user_btn, 0)
        snmp_layout.addLayout(users_row)
        self.snmp_users_tree = QTreeWidget(self.snmp_group)
        self.snmp_users_tree.setColumnCount(3)
        self.snmp_users_tree.setHeaderLabels(["Username", "Auth", "Privacy"])
        self.snmp_users_tree.setRootIsDecorated(False)
        self.snmp_users_tree.setSelectionMode(QAbstractItemView.SingleSelection)
        snmp_layout.addWidget(self.snmp_users_tree)
        controls_layout.addWidget(self.snmp_group)
        controls_layout.addStretch(1)

        controls_scroll = QScrollArea(self.monitor_page)
        controls_scroll.setWidgetResizable(True)
        controls_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        controls_scroll.setWidget(controls_container)
        layout.addWidget(controls_scroll, 1)

    def _build_search_tab(self) -> None:
        layout = QVBoxLayout(self.search_page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.search_splitter = QSplitter(Qt.Vertical, self.search_page)
        self.search_splitter.setChildrenCollapsible(False)
        layout.addWidget(self.search_splitter, 1)

        controls_container = QWidget(self.search_page)
        controls_layout = QVBoxLayout(controls_container)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(10)

        self.filter_group = QGroupBox("Search / Filters", controls_container)
        filter_layout = QGridLayout(self.filter_group)
        filter_layout.setColumnStretch(1, 1)
        filter_layout.setColumnStretch(3, 1)
        filter_layout.setColumnStretch(5, 1)
        self.search_input = QLineEdit(self.filter_group)
        self.search_input.setPlaceholderText("Message text, OID, varbind text, hostname, source...")
        self.regex_input = QCheckBox("Regex", self.filter_group)
        self.case_sensitive_input = QCheckBox("Case Sensitive", self.filter_group)
        self.start_ts_input = QLineEdit(self.filter_group)
        self.start_ts_input.setPlaceholderText("2026-03-31T15:30:00+00:00")
        self.end_ts_input = QLineEdit(self.filter_group)
        self.end_ts_input.setPlaceholderText("2026-03-31T16:30:00+00:00")
        self.source_input = QLineEdit(self.filter_group)
        self.listener_input = QComboBox(self.filter_group)
        self.listener_input.addItem("(Any)", "")
        for value in ("syslog-udp", "syslog-tcp", "syslog-tls", "snmp"):
            self.listener_input.addItem(value, value)
        self.protocol_input = QComboBox(self.filter_group)
        self.protocol_input.addItem("(Any)", "")
        self.protocol_input.addItem("syslog", "syslog")
        self.protocol_input.addItem("snmp", "snmp")
        self.transport_input = QComboBox(self.filter_group)
        self.transport_input.addItem("(Any)", "")
        for value in ("udp", "tcp", "tls"):
            self.transport_input.addItem(value, value)
        self.severity_input = QComboBox(self.filter_group)
        self.severity_input.addItem("(Any)", "")
        for value in _SEVERITY_NAMES[1:]:
            self.severity_input.addItem(value, value)
        self.facility_input = QComboBox(self.filter_group)
        self.facility_input.addItem("(Any)", "")
        for value in _FACILITY_NAMES[1:]:
            self.facility_input.addItem(value, value)
        self.syslog_hostname_input = QLineEdit(self.filter_group)
        self.app_name_input = QLineEdit(self.filter_group)
        self.procid_input = QLineEdit(self.filter_group)
        self.msgid_input = QLineEdit(self.filter_group)
        self.snmp_version_input = QComboBox(self.filter_group)
        self.snmp_version_input.addItem("(Any)", "")
        self.snmp_version_input.addItem("v1", "v1")
        self.snmp_version_input.addItem("v2c", "v2c")
        self.snmp_version_input.addItem("v3", "v3")
        self.snmp_security_name_input = QLineEdit(self.filter_group)
        self.notification_oid_input = QLineEdit(self.filter_group)
        self.enterprise_oid_input = QLineEdit(self.filter_group)
        self.varbind_text_input = QLineEdit(self.filter_group)
        self.alerted_only_input = QCheckBox("Alerted Only", self.filter_group)
        self.data_scope_input = QComboBox(self.filter_group)
        self.data_scope_input.addItem("Live", "live")
        self.data_scope_input.addItem("Archived", "archived")
        self.data_scope_input.addItem("All", "all")
        self.apply_filters_btn = QPushButton("Apply Filters", self.filter_group)
        self.clear_filters_btn = QPushButton("Clear", self.filter_group)
        filter_layout.addWidget(QLabel("Find"), 0, 0)
        filter_layout.addWidget(self.search_input, 0, 1, 1, 3)
        filter_layout.addWidget(self.regex_input, 0, 4)
        filter_layout.addWidget(self.case_sensitive_input, 0, 5)
        filter_layout.addWidget(QLabel("Start Time"), 1, 0)
        filter_layout.addWidget(self.start_ts_input, 1, 1)
        filter_layout.addWidget(QLabel("End Time"), 1, 2)
        filter_layout.addWidget(self.end_ts_input, 1, 3)
        filter_layout.addWidget(QLabel("Source"), 1, 4)
        filter_layout.addWidget(self.source_input, 1, 5)
        filter_layout.addWidget(QLabel("Listener"), 2, 0)
        filter_layout.addWidget(self.listener_input, 2, 1)
        filter_layout.addWidget(QLabel("Protocol"), 2, 2)
        filter_layout.addWidget(self.protocol_input, 2, 3)
        filter_layout.addWidget(QLabel("Transport"), 2, 4)
        filter_layout.addWidget(self.transport_input, 2, 5)
        filter_layout.addWidget(QLabel("Severity"), 3, 0)
        filter_layout.addWidget(self.severity_input, 3, 1)
        filter_layout.addWidget(QLabel("Facility"), 3, 2)
        filter_layout.addWidget(self.facility_input, 3, 3)
        filter_layout.addWidget(QLabel("Hostname"), 3, 4)
        filter_layout.addWidget(self.syslog_hostname_input, 3, 5)
        filter_layout.addWidget(QLabel("App / Tag"), 4, 0)
        filter_layout.addWidget(self.app_name_input, 4, 1)
        filter_layout.addWidget(QLabel("PID"), 4, 2)
        filter_layout.addWidget(self.procid_input, 4, 3)
        filter_layout.addWidget(QLabel("MsgID"), 4, 4)
        filter_layout.addWidget(self.msgid_input, 4, 5)
        filter_layout.addWidget(QLabel("SNMP Version"), 5, 0)
        filter_layout.addWidget(self.snmp_version_input, 5, 1)
        filter_layout.addWidget(QLabel("Security / User"), 5, 2)
        filter_layout.addWidget(self.snmp_security_name_input, 5, 3)
        filter_layout.addWidget(QLabel("Trap OID"), 5, 4)
        filter_layout.addWidget(self.notification_oid_input, 5, 5)
        filter_layout.addWidget(QLabel("Enterprise OID"), 6, 0)
        filter_layout.addWidget(self.enterprise_oid_input, 6, 1)
        filter_layout.addWidget(QLabel("Varbind Text"), 6, 2)
        filter_layout.addWidget(self.varbind_text_input, 6, 3)
        filter_layout.addWidget(QLabel("Data Scope"), 6, 4)
        filter_layout.addWidget(self.data_scope_input, 6, 5)
        filter_actions = QHBoxLayout()
        filter_actions.addWidget(self.alerted_only_input)
        filter_actions.addStretch(1)
        filter_actions.addWidget(self.apply_filters_btn, 0)
        filter_actions.addWidget(self.clear_filters_btn, 0)
        filter_layout.addLayout(filter_actions, 7, 0, 1, 6)
        controls_layout.addWidget(self.filter_group)
        controls_layout.addStretch(1)

        controls_scroll = QScrollArea(self.search_page)
        controls_scroll.setWidgetResizable(True)
        controls_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        controls_scroll.setWidget(controls_container)
        self.search_splitter.addWidget(controls_scroll)

        self.event_detail_splitter = QSplitter(Qt.Horizontal, self.search_page)
        self.event_detail_splitter.setChildrenCollapsible(False)
        self.search_splitter.addWidget(self.event_detail_splitter)

        events_panel = QWidget(self.search_page)
        events_layout = QVBoxLayout(events_panel)
        events_layout.setContentsMargins(0, 0, 0, 0)
        events_layout.setSpacing(8)
        event_actions = QHBoxLayout()
        self.copy_cell_btn = QPushButton("Copy Cell", events_panel)
        self.copy_row_btn = QPushButton("Copy Row", events_panel)
        self.copy_selected_btn = QPushButton("Copy Selected", events_panel)
        self.copy_column_btn = QPushButton("Copy Column", events_panel)
        self.copy_all_btn = QPushButton("Copy All Visible", events_panel)
        self.export_csv_btn = QPushButton("Export CSV...", events_panel)
        self.export_json_btn = QPushButton("Export JSON...", events_panel)
        self.export_selected_btn = QPushButton("Export Selected...", events_panel)
        self.columns_btn = QToolButton(events_panel)
        self.columns_btn.setText("Columns")
        self.display_timezone_label = QLabel("Display Timezone", events_panel)
        self.display_timezone_input = QComboBox(events_panel)
        self.display_timezone_input.setMinimumContentsLength(18)
        self._populate_display_timezone_input()
        event_actions.addWidget(self.copy_cell_btn, 0)
        event_actions.addWidget(self.copy_row_btn, 0)
        event_actions.addWidget(self.copy_selected_btn, 0)
        event_actions.addWidget(self.copy_column_btn, 0)
        event_actions.addWidget(self.copy_all_btn, 0)
        event_actions.addStretch(1)
        event_actions.addWidget(self.display_timezone_label, 0)
        event_actions.addWidget(self.display_timezone_input, 0)
        event_actions.addWidget(self.export_csv_btn, 0)
        event_actions.addWidget(self.export_json_btn, 0)
        event_actions.addWidget(self.export_selected_btn, 0)
        event_actions.addWidget(self.columns_btn, 0)
        events_layout.addLayout(event_actions)
        self.events_tree = QTreeWidget(events_panel)
        self.events_tree.setColumnCount(len(_EVENT_COLUMNS))
        self.events_tree.setHeaderLabels([label for _key, label in _EVENT_COLUMNS])
        self.events_tree.setRootIsDecorated(False)
        self.events_tree.setAlternatingRowColors(True)
        self.events_tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.events_tree.setUniformRowHeights(True)
        self.events_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        events_layout.addWidget(self.events_tree, 1)
        self.event_detail_splitter.addWidget(events_panel)

        details_panel = QWidget(self.search_page)
        details_layout = QVBoxLayout(details_panel)
        details_layout.setContentsMargins(0, 0, 0, 0)
        details_layout.setSpacing(8)
        self.event_details_tabs = QTabWidget(details_panel)
        self.event_summary_text = QPlainTextEdit(details_panel)
        self.event_summary_text.setReadOnly(True)
        self.event_raw_text = QPlainTextEdit(details_panel)
        self.event_raw_text.setReadOnly(True)
        self.event_json_text = QPlainTextEdit(details_panel)
        self.event_json_text.setReadOnly(True)
        self.event_details_tabs.addTab(self.event_summary_text, "Fields")
        self.event_details_tabs.addTab(self.event_raw_text, "Raw Payload")
        self.event_details_tabs.addTab(self.event_json_text, "JSON")
        details_layout.addWidget(self.event_details_tabs, 1)
        self.event_detail_splitter.addWidget(details_panel)

    def _build_alerts_tab(self) -> None:
        layout = QVBoxLayout(self.alerts_page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        actions = QHBoxLayout()
        self.add_rule_btn = QPushButton("Add Rule...", self.alerts_page)
        self.edit_rule_btn = QPushButton("Edit Rule...", self.alerts_page)
        self.duplicate_rule_btn = QPushButton("Duplicate", self.alerts_page)
        self.delete_rule_btn = QPushButton("Delete", self.alerts_page)
        actions.addWidget(self.add_rule_btn, 0)
        actions.addWidget(self.edit_rule_btn, 0)
        actions.addWidget(self.duplicate_rule_btn, 0)
        actions.addWidget(self.delete_rule_btn, 0)
        actions.addStretch(1)
        layout.addLayout(actions)
        self.alert_rules_tree = QTreeWidget(self.alerts_page)
        self.alert_rules_tree.setColumnCount(6)
        self.alert_rules_tree.setHeaderLabels(["Name", "Protocol", "Severity", "Popup", "Sound", "Match"])
        self.alert_rules_tree.setRootIsDecorated(False)
        self.alert_rules_tree.setSelectionMode(QAbstractItemView.SingleSelection)
        layout.addWidget(self.alert_rules_tree, 1)
        hint = QLabel(
            "Rules are evaluated by the hidden collector helper. Changes made while running are saved immediately in the GUI but take effect after stop / start when the collector is already active.",
            self.alerts_page,
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

    def _build_dashboard_tab(self) -> None:
        layout = QVBoxLayout(self.dashboard_page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        hint = QLabel(
            "Charts are built from the current filtered result set. Click severity slices or top-source / top-app / top-trap bars to drill the Monitor tab filters.",
            self.dashboard_page,
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        scroll = QScrollArea(self.dashboard_page)
        scroll.setWidgetResizable(True)
        container = QWidget(scroll)
        grid = QGridLayout(container)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(10)
        self.event_rate_chart = self._create_chart_view("Event Rate Over Time")
        self.severity_chart = self._create_chart_view("Syslog Severity Distribution")
        self.top_sources_chart = self._create_chart_view("Top Sources")
        self.top_apps_chart = self._create_chart_view("Top Apps / Tags")
        self.top_traps_chart = self._create_chart_view("Top Trap OIDs")
        self.alerts_chart = self._create_chart_view("Alerts Over Time")
        grid.addWidget(self.event_rate_chart, 0, 0)
        grid.addWidget(self.severity_chart, 0, 1)
        grid.addWidget(self.top_sources_chart, 1, 0)
        grid.addWidget(self.top_apps_chart, 1, 1)
        grid.addWidget(self.top_traps_chart, 2, 0)
        grid.addWidget(self.alerts_chart, 2, 1)
        scroll.setWidget(container)
        layout.addWidget(scroll, 1)

    def _build_archive_tab(self) -> None:
        layout = QVBoxLayout(self.archive_page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        retention_group = QGroupBox("Retention Policy", self.archive_page)
        retention_form = QFormLayout(retention_group)
        self.hot_retention_days_input = QSpinBox(retention_group)
        self.hot_retention_days_input.setRange(1, 3650)
        self.archive_retention_days_input = QSpinBox(retention_group)
        self.archive_retention_days_input.setRange(1, 3650)
        self.max_archive_size_mb_input = QSpinBox(retention_group)
        self.max_archive_size_mb_input.setRange(128, 1024 * 1024)
        self.archive_rotation_mb_input = QSpinBox(retention_group)
        self.archive_rotation_mb_input.setRange(1, 1024)
        retention_form.addRow("Hot Database Retention (days)", self.hot_retention_days_input)
        retention_form.addRow("Archive Retention (days)", self.archive_retention_days_input)
        retention_form.addRow("Maximum Archive Size (MB)", self.max_archive_size_mb_input)
        retention_form.addRow("Archive Rotation Threshold (MB)", self.archive_rotation_mb_input)
        layout.addWidget(retention_group)

        stats_group = QGroupBox("Storage", self.archive_page)
        stats_form = QFormLayout(stats_group)
        self.live_event_count_label = QLabel("0", stats_group)
        self.pending_alert_count_label = QLabel("0", stats_group)
        self.db_size_label = QLabel("0 B", stats_group)
        self.archive_file_count_label = QLabel("0", stats_group)
        self.archive_size_label = QLabel("0 B", stats_group)
        self.oldest_live_label = QLabel("-", stats_group)
        self.newest_live_label = QLabel("-", stats_group)
        stats_form.addRow("Live Events", self.live_event_count_label)
        stats_form.addRow("Pending Alerts", self.pending_alert_count_label)
        stats_form.addRow("Database Size", self.db_size_label)
        stats_form.addRow("Archive Files", self.archive_file_count_label)
        stats_form.addRow("Archive Size", self.archive_size_label)
        stats_form.addRow("Oldest Live Event", self.oldest_live_label)
        stats_form.addRow("Newest Live Event", self.newest_live_label)
        layout.addWidget(stats_group)

        actions = QHBoxLayout()
        self.archive_now_btn = QPushButton("Archive Now", self.archive_page)
        self.purge_now_btn = QPushButton("Purge Now", self.archive_page)
        self.clear_database_btn = QPushButton("Clear Database...", self.archive_page)
        actions.addWidget(self.archive_now_btn, 0)
        actions.addWidget(self.purge_now_btn, 0)
        actions.addWidget(self.clear_database_btn, 0)
        actions.addStretch(1)
        layout.addLayout(actions)

        self.retention_status_label = QLabel("", self.archive_page)
        self.retention_status_label.setWordWrap(True)
        layout.addWidget(self.retention_status_label)
        layout.addStretch(1)

    def _set_runtime_badge(self, state: str) -> None:
        label, color = _RUNTIME_BADGE_STYLES.get(state.strip().lower(), _RUNTIME_BADGE_STYLES["stopped"])
        self.runtime_badge.setText(label)
        self.runtime_badge.setStyleSheet(
            f"""
            QLabel {{
                background-color: {color};
                color: #ffffff;
                border-radius: 11px;
                padding: 4px 12px;
                font-weight: 700;
            }}
            """
        )

    def _apply_output_fonts(self, settings: AppSettings) -> None:
        for widget in (
            self.event_summary_text,
            self.event_raw_text,
            self.event_json_text,
        ):
            apply_terminal_output_font(widget, settings)

    def apply_runtime_settings(self, settings: AppSettings) -> None:
        self._settings = AppSettings.from_dict(settings.to_dict())
        self._apply_output_fonts(self._settings)
        self._refresh_dashboard()
        for dialog in list(self._event_detail_windows):
            dialog.apply_runtime_settings(self._settings)
        if self._alert_notifications_window is not None:
            self._alert_notifications_window.apply_runtime_settings(self._settings)

    def _current_tab_id(self) -> str:
        return _tab_id_from_index(self.tabs.currentIndex())

    def _set_current_tab_by_id(self, tab_id: str) -> None:
        self.tabs.setCurrentIndex(_tab_index_from_id(tab_id))

    def _connect_config_signals(self) -> None:
        widgets: list[object] = [
            self.bind_host_widget,
            self.syslog_udp_enabled_input,
            self.syslog_udp_port_input,
            self.syslog_tcp_enabled_input,
            self.syslog_tcp_port_input,
            self.syslog_tls_enabled_input,
            self.syslog_tls_port_input,
            self.snmp_enabled_input,
            self.snmp_port_input,
            self.snmp_v1_enabled_input,
            self.snmp_v2c_enabled_input,
            self.snmp_v3_enabled_input,
            self.snmp_communities_input,
            self.syslog_tls_cert_input,
            self.syslog_tls_key_input,
            self.syslog_tls_ca_input,
            self.hot_retention_days_input,
            self.archive_retention_days_input,
            self.max_archive_size_mb_input,
            self.archive_rotation_mb_input,
        ]
        for widget in widgets:
            if isinstance(widget, QCheckBox):
                widget.toggled.connect(self._on_runtime_config_changed)
            elif isinstance(widget, QSpinBox):
                widget.valueChanged.connect(self._on_runtime_config_changed)
            elif isinstance(widget, QLineEdit):
                widget.textChanged.connect(self._on_runtime_config_changed)
        self.bind_host_widget.value_changed.connect(self._on_runtime_config_changed)
        self.syslog_tls_enabled_input.toggled.connect(self._sync_runtime_controls)
        self.snmp_enabled_input.toggled.connect(self._sync_runtime_controls)
        self.snmp_v3_enabled_input.toggled.connect(self._sync_runtime_controls)

    @staticmethod
    def _build_port_spinbox(value: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(1, 65535)
        spin.setValue(value)
        return spin

    def _build_path_input(self, *, directory: bool) -> tuple[QWidget, QLineEdit]:
        container = QWidget(self)
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        field = QLineEdit(container)
        browse = QPushButton("Browse...", container)
        row.addWidget(field, 1)
        row.addWidget(browse, 0)
        if directory:
            browse.clicked.connect(lambda: self._browse_directory(field))
        else:
            browse.clicked.connect(lambda: self._browse_file(field))
        return container, field

    def _browse_directory(self, target: QLineEdit) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Select Folder", target.text().strip() or "")
        if directory:
            target.setText(directory)

    def _browse_file(self, target: QLineEdit) -> None:
        path, _selected = QFileDialog.getOpenFileName(self, "Select File", target.text().strip() or "")
        if path:
            target.setText(path)

    def _create_chart_view(self, title: str) -> QChartView:
        chart = QChart()
        chart.setTitle(title)
        chart.legend().setVisible(True)
        chart.setAnimationOptions(QChart.NoAnimation)
        self._apply_chart_theme(chart)
        view = QChartView(chart, self.dashboard_page)
        view.setRenderHint(QPainter.Antialiasing)
        view.setMinimumHeight(280)
        return view

    @staticmethod
    def _safe_qcolor(value: str, fallback: str) -> QColor:
        color = QColor(str(value).strip())
        if not color.isValid():
            color = QColor(fallback)
        return color

    def _theme_settings(self) -> AppSettings:
        if isinstance(self._settings, AppSettings):
            return self._settings
        for candidate in (
            getattr(self.parent(), "_settings", None),
            getattr(self.window(), "_settings", None),
        ):
            if isinstance(candidate, AppSettings):
                return candidate
        return AppSettings.defaults()

    def _chart_series_colors(self, accent: QColor, background: QColor, *, count: int) -> list[QColor]:
        base_hue = accent.hslHue() if accent.hslHue() >= 0 else 212
        base_saturation = max(120, accent.hslSaturation())
        dark_background = background.lightness() < 140
        base_lightness = 160 if dark_background else 110
        colors: list[QColor] = []
        for index in range(max(1, count)):
            hue = (base_hue + (index * 29)) % 360
            saturation = max(100, min(240, base_saturation + (12 if index % 3 == 0 else 0) - (8 if index % 4 == 0 else 0)))
            lightness = max(58, min(214, base_lightness + ((index % 4) - 1) * (12 if dark_background else 10)))
            colors.append(QColor.fromHsl(hue, saturation, lightness))
        if colors:
            colors[0] = accent
        return colors

    def _chart_theme(self, *, series_count: int = 8) -> dict[str, object]:
        settings = self._theme_settings()
        background = self._safe_qcolor(settings.field_bg, "#1a222d")
        plot_background = self._safe_qcolor(
            blend_colors(background.name(), settings.app_bg_end, 0.26),
            background.name(),
        )
        accent = self._safe_qcolor(settings.accent_color, "#2d6cdf")
        text = self._safe_qcolor(
            readable_foreground_color(settings.text_color, background.name(), minimum_ratio=4.5),
            "#ffffff",
        )
        title = self._safe_qcolor(
            readable_foreground_color(text.name(), background.name(), minimum_ratio=4.5),
            text.name(),
        )
        border = self._safe_qcolor(
            settings.field_border,
            blend_colors(background.name(), text.name(), 0.18),
        )
        grid = self._safe_qcolor(
            blend_colors(border.name(), text.name(), 0.18),
            border.name(),
        )
        return {
            "background": background,
            "plot_background": plot_background,
            "accent": accent,
            "text": text,
            "title": title,
            "border": border,
            "grid": grid,
            "series_colors": self._chart_series_colors(accent, background, count=series_count),
        }

    def _apply_chart_theme(self, chart: QChart, *, series_count: int = 8) -> dict[str, object]:
        theme = self._chart_theme(series_count=series_count)
        chart.setAnimationOptions(QChart.NoAnimation)
        chart.setBackgroundVisible(True)
        chart.setBackgroundRoundness(10.0)
        chart.setBackgroundBrush(QBrush(theme["background"]))
        chart.setBackgroundPen(QPen(theme["border"]))
        chart.setPlotAreaBackgroundVisible(True)
        chart.setPlotAreaBackgroundBrush(QBrush(theme["plot_background"]))
        chart.setPlotAreaBackgroundPen(QPen(theme["border"]))
        try:
            chart.setTitleBrush(QBrush(theme["title"]))
        except Exception:
            pass
        legend = chart.legend()
        try:
            legend.setLabelColor(theme["text"])
        except Exception:
            pass
        try:
            legend.setBrush(QBrush(Qt.GlobalColor.transparent))
        except Exception:
            pass
        try:
            legend.setPen(QPen(Qt.PenStyle.NoPen))
        except Exception:
            pass
        return theme

    def _style_chart_axis(self, axis, theme: dict[str, object]) -> None:
        try:
            axis.setLabelsColor(theme["text"])
        except Exception:
            pass
        try:
            axis.setGridLineColor(theme["grid"])
        except Exception:
            pass
        try:
            axis.setLinePen(QPen(theme["border"]))
        except Exception:
            pass
        try:
            axis.setLinePenColor(theme["border"])
        except Exception:
            pass
        try:
            axis.setShadesVisible(False)
        except Exception:
            pass

    def _populate_display_timezone_input(self) -> None:
        self.display_timezone_input.clear()
        self.display_timezone_input.addItem("UTC", _DISPLAY_TIMEZONE_UTC)
        self.display_timezone_input.addItem("System Local", _DISPLAY_TIMEZONE_SYSTEM)
        for timezone_id in _available_display_timezones():
            self.display_timezone_input.addItem(timezone_id, timezone_id)

    def _normalize_profiles(self, profiles: list[dict[str, object]]) -> list[dict[str, object]]:
        normalized: list[dict[str, object]] = []
        seen_ids: set[str] = set()
        for raw in profiles:
            if not isinstance(raw, dict):
                continue
            profile_id = str(raw.get("id", "")).strip()
            name = str(raw.get("name", "")).strip()
            config = raw.get("config")
            if not profile_id or not name or not isinstance(config, dict):
                continue
            if profile_id in seen_ids:
                continue
            seen_ids.add(profile_id)
            normalized.append(
                {
                    "id": profile_id,
                    "name": name,
                    "config": SyslogSnmpMonitorConfig.from_dict(config).to_dict(),
                }
            )
        return normalized

    def _new_profile(self, name: str, config: SyslogSnmpMonitorConfig) -> dict[str, object]:
        return {
            "id": str(uuid4()),
            "name": name.strip() or DEFAULT_PROFILE_NAME,
            "config": config.to_dict(),
        }

    def _profile_rows(self) -> list[dict[str, object]]:
        return [
            {
                "id": str(profile.get("id", "")).strip(),
                "name": str(profile.get("name", "")).strip(),
                "config": dict(profile.get("config", {})),
            }
            for profile in self._profiles
        ]

    def _refresh_profile_combo(self) -> None:
        self.profile_input.blockSignals(True)
        self.profile_input.clear()
        current_index = 0
        for index, profile in enumerate(self._profiles):
            profile_id = str(profile.get("id", "")).strip()
            name = str(profile.get("name", "")).strip() or DEFAULT_PROFILE_NAME
            self.profile_input.addItem(name, profile_id)
            if profile_id == self._selected_profile_id:
                current_index = index
        self.profile_input.setCurrentIndex(current_index)
        self.profile_input.blockSignals(False)
        self._update_profile_buttons()

    def _profile_by_id(self, profile_id: str) -> dict[str, object] | None:
        target = profile_id.strip()
        if not target:
            return None
        for profile in self._profiles:
            if str(profile.get("id", "")).strip() == target:
                return profile
        return None

    def _update_profile_buttons(self) -> None:
        has_profile = bool(self._selected_profile_id)
        self.load_profile_btn.setEnabled(has_profile)
        self.update_profile_btn.setEnabled(has_profile)
        self.rename_profile_btn.setEnabled(has_profile)
        self.delete_profile_btn.setEnabled(has_profile and len(self._profiles) > 1)

    def _persist_profiles(self) -> None:
        if self._on_profiles_changed is None:
            return
        self._on_profiles_changed(self._profile_rows(), self._selected_profile_id)

    def _persist_splitter_state(self, *_args: object) -> None:
        if self._on_splitter_state_changed is None:
            return
        encoded = self._encode_state(self.search_splitter.saveState())
        if encoded == self._splitter_state_b64:
            return
        self._splitter_state_b64 = encoded
        self._on_splitter_state_changed(encoded)

    def _restore_search_splitter_state(self) -> None:
        if self._splitter_state_b64:
            state = QByteArray.fromBase64(self._splitter_state_b64.encode("ascii"))
            if not state.isEmpty() and self.search_splitter.restoreState(state):
                return
        self.search_splitter.setSizes([280, 580])
        self.event_detail_splitter.setSizes([920, 420])

    @staticmethod
    def _encode_state(state: QByteArray) -> str:
        try:
            return bytes(state.toBase64()).decode("ascii").strip()
        except Exception:
            return ""

    def _apply_profile_config(self, config: SyslogSnmpMonitorConfig) -> None:
        self._suspend_profile_sync = True
        try:
            self.bind_host_widget.set_value(config.bind_host)
            self.syslog_udp_enabled_input.setChecked(config.syslog_udp_enabled)
            self.syslog_udp_port_input.setValue(config.syslog_udp_port)
            self.syslog_tcp_enabled_input.setChecked(config.syslog_tcp_enabled)
            self.syslog_tcp_port_input.setValue(config.syslog_tcp_port)
            self.syslog_tls_enabled_input.setChecked(config.syslog_tls_enabled)
            self.syslog_tls_port_input.setValue(config.syslog_tls_port)
            self.syslog_tls_cert_input.setText(config.syslog_tls_cert_file)
            self.syslog_tls_key_input.setText(config.syslog_tls_key_file)
            self.syslog_tls_ca_input.setText(config.syslog_tls_ca_file)
            self.snmp_enabled_input.setChecked(config.snmp_enabled)
            self.snmp_port_input.setValue(config.snmp_port)
            self.snmp_v1_enabled_input.setChecked(config.snmp_v1_enabled)
            self.snmp_v2c_enabled_input.setChecked(config.snmp_v2c_enabled)
            self.snmp_v3_enabled_input.setChecked(config.snmp_v3_enabled)
            self.snmp_communities_input.setText(config.snmp_communities)
            retention = MonitorRetentionPolicy.from_dict(config.retention)
            self.hot_retention_days_input.setValue(retention.hot_retention_days)
            self.archive_retention_days_input.setValue(retention.archive_retention_days)
            self.max_archive_size_mb_input.setValue(retention.max_archive_size_mb)
            self.archive_rotation_mb_input.setValue(retention.archive_rotation_mb)
            self._snmp_v3_users = [MonitorSnmpV3User.from_dict(item) for item in config.snmp_v3_users]
            self._alert_rules = [MonitorAlertRule.from_dict(item) for item in config.alert_rules]
            self._apply_filter_state(MonitorQueryFilters.from_dict(config.filter_state))
            self._apply_visible_columns(config.visible_columns)
            self._apply_layout_state(config.layout_state)
            self._refresh_snmp_users_tree()
            self._refresh_alert_rules_tree()
            self._sync_runtime_controls()
        finally:
            self._suspend_profile_sync = False

    def _apply_filter_state(self, filters: MonitorQueryFilters) -> None:
        self.search_input.setText(filters.text)
        self.regex_input.setChecked(filters.use_regex)
        self.case_sensitive_input.setChecked(filters.case_sensitive)
        self.start_ts_input.setText(filters.start_ts)
        self.end_ts_input.setText(filters.end_ts)
        self.source_input.setText(filters.source_contains)
        self._set_combo(self.listener_input, filters.listener)
        self._set_combo(self.protocol_input, filters.protocol)
        self._set_combo(self.transport_input, filters.transport)
        self._set_combo(self.severity_input, filters.severity_name)
        self._set_combo(self.facility_input, filters.facility_name)
        self.syslog_hostname_input.setText(filters.syslog_hostname)
        self.app_name_input.setText(filters.app_name)
        self.procid_input.setText(filters.procid)
        self.msgid_input.setText(filters.msgid)
        self._set_combo(self.snmp_version_input, filters.snmp_version)
        self.snmp_security_name_input.setText(filters.snmp_security_name)
        self.notification_oid_input.setText(filters.notification_oid)
        self.enterprise_oid_input.setText(filters.enterprise_oid)
        self.varbind_text_input.setText(filters.varbind_text)
        self.alerted_only_input.setChecked(filters.alerted_only)
        self._set_combo(self.data_scope_input, filters.data_scope)

    def _apply_visible_columns(self, columns: list[str]) -> None:
        visible = {key for key in columns if key in _EVENT_COLUMN_INDEX}
        if not visible:
            visible = set(DEFAULT_EVENT_COLUMNS)
        for key, _label in _EVENT_COLUMNS:
            self.events_tree.setColumnHidden(_EVENT_COLUMN_INDEX[key], key not in visible)
        for index in range(self.events_tree.columnCount()):
            self.events_tree.resizeColumnToContents(index)

    def _apply_layout_state(self, state: dict[str, object]) -> None:
        if not isinstance(state, dict):
            return
        self._set_display_timezone(_normalize_display_timezone_id(str(state.get("display_timezone", _DISPLAY_TIMEZONE_UTC))))
        raw_tab_id = str(state.get("tab_id", "")).strip()
        if raw_tab_id:
            self._set_current_tab_by_id(raw_tab_id)
        else:
            tab_index = state.get("tab_index")
            try:
                tab_value = int(tab_index)
            except (TypeError, ValueError):
                tab_value = 0
            self._set_current_tab_by_id(_LEGACY_TAB_INDEX_TO_ID.get(tab_value, _TAB_MONITOR))
        detail_state = str(state.get("detail_splitter_b64", "")).strip()
        if detail_state:
            try:
                restored = QByteArray.fromBase64(detail_state.encode("ascii"))
            except Exception:
                restored = QByteArray()
            if not restored.isEmpty():
                self.event_detail_splitter.restoreState(restored)

    def _current_filter_state(self) -> MonitorQueryFilters:
        return MonitorQueryFilters(
            text=self.search_input.text().strip(),
            use_regex=self.regex_input.isChecked(),
            case_sensitive=self.case_sensitive_input.isChecked(),
            start_ts=self.start_ts_input.text().strip(),
            end_ts=self.end_ts_input.text().strip(),
            source_contains=self.source_input.text().strip(),
            listener=str(self.listener_input.currentData() or ""),
            protocol=str(self.protocol_input.currentData() or ""),
            transport=str(self.transport_input.currentData() or ""),
            severity_name=str(self.severity_input.currentData() or ""),
            facility_name=str(self.facility_input.currentData() or ""),
            syslog_hostname=self.syslog_hostname_input.text().strip(),
            app_name=self.app_name_input.text().strip(),
            procid=self.procid_input.text().strip(),
            msgid=self.msgid_input.text().strip(),
            snmp_version=str(self.snmp_version_input.currentData() or ""),
            snmp_security_name=self.snmp_security_name_input.text().strip(),
            notification_oid=self.notification_oid_input.text().strip(),
            enterprise_oid=self.enterprise_oid_input.text().strip(),
            varbind_text=self.varbind_text_input.text().strip(),
            alerted_only=self.alerted_only_input.isChecked(),
            data_scope=str(self.data_scope_input.currentData() or "live"),
        )

    def _current_visible_columns(self) -> list[str]:
        columns = [
            key
            for key, _label in _EVENT_COLUMNS
            if not self.events_tree.isColumnHidden(_EVENT_COLUMN_INDEX[key])
        ]
        return columns or list(DEFAULT_EVENT_COLUMNS)

    def _current_layout_state(self) -> dict[str, object]:
        return {
            "display_timezone": self._display_timezone_id(),
            "tab_id": self._current_tab_id(),
            "tab_index": self.tabs.currentIndex(),
            "detail_splitter_b64": self._encode_state(self.event_detail_splitter.saveState()),
        }

    def _current_config_preview(self) -> SyslogSnmpMonitorConfig:
        return SyslogSnmpMonitorConfig(
            bind_host=self.bind_host_input.text(),
            syslog_udp_enabled=self.syslog_udp_enabled_input.isChecked(),
            syslog_udp_port=self.syslog_udp_port_input.value(),
            syslog_tcp_enabled=self.syslog_tcp_enabled_input.isChecked(),
            syslog_tcp_port=self.syslog_tcp_port_input.value(),
            syslog_tls_enabled=self.syslog_tls_enabled_input.isChecked(),
            syslog_tls_port=self.syslog_tls_port_input.value(),
            syslog_tls_cert_file=self.syslog_tls_cert_input.text(),
            syslog_tls_key_file=self.syslog_tls_key_input.text(),
            syslog_tls_ca_file=self.syslog_tls_ca_input.text(),
            snmp_enabled=self.snmp_enabled_input.isChecked(),
            snmp_port=self.snmp_port_input.value(),
            snmp_v1_enabled=self.snmp_v1_enabled_input.isChecked(),
            snmp_v2c_enabled=self.snmp_v2c_enabled_input.isChecked(),
            snmp_v3_enabled=self.snmp_v3_enabled_input.isChecked(),
            snmp_communities=self.snmp_communities_input.text(),
            snmp_v3_users=[user.to_dict() for user in self._snmp_v3_users],
            retention=self._current_retention_policy().to_dict(),
            alert_rules=[rule.to_dict() for rule in self._alert_rules],
            filter_state=self._current_filter_state().to_dict(),
            layout_state=self._current_layout_state(),
            visible_columns=self._current_visible_columns(),
        )

    def _current_config(self) -> SyslogSnmpMonitorConfig:
        return validate_syslog_snmp_monitor_config(self._current_config_preview())

    def _current_retention_policy(self) -> MonitorRetentionPolicy:
        return MonitorRetentionPolicy(
            hot_retention_days=self.hot_retention_days_input.value(),
            archive_retention_days=self.archive_retention_days_input.value(),
            max_archive_size_mb=self.max_archive_size_mb_input.value(),
            archive_rotation_mb=self.archive_rotation_mb_input.value(),
        )

    def _runtime_config_signature(self, config: SyslogSnmpMonitorConfig) -> tuple[object, ...]:
        normalized = SyslogSnmpMonitorConfig.from_dict(config.to_dict())
        return (
            normalized.bind_host,
            normalized.syslog_udp_enabled,
            normalized.syslog_udp_port,
            normalized.syslog_tcp_enabled,
            normalized.syslog_tcp_port,
            normalized.syslog_tls_enabled,
            normalized.syslog_tls_port,
            normalized.syslog_tls_cert_file,
            normalized.syslog_tls_key_file,
            normalized.syslog_tls_ca_file,
            normalized.snmp_enabled,
            normalized.snmp_port,
            normalized.snmp_v1_enabled,
            normalized.snmp_v2c_enabled,
            normalized.snmp_v3_enabled,
            normalized.snmp_communities,
            json.dumps(normalized.snmp_v3_users, sort_keys=True),
            json.dumps(normalized.retention, sort_keys=True),
            json.dumps(normalized.alert_rules, sort_keys=True),
        )

    def _runtime_state(self, status: SyslogSnmpMonitorStatus) -> _RuntimeState:
        state = status.state.strip().lower()
        running = state == "running"
        starting = state == "starting"
        stopped = state == "stopped" or not state
        error = state == "error"
        return _RuntimeState(
            running=running,
            starting=starting,
            stopped=stopped,
            error=error,
            can_stop=running or starting,
        )

    def _display_timezone_id(self) -> str:
        return _normalize_display_timezone_id(str(self.display_timezone_input.currentData() or _DISPLAY_TIMEZONE_UTC))

    def _set_display_timezone(self, value: str) -> None:
        normalized = _normalize_display_timezone_id(value)
        index = self.display_timezone_input.findData(normalized)
        self.display_timezone_input.setCurrentIndex(index if index >= 0 else 0)

    def _format_display_timestamp(self, value: object) -> str:
        return _format_timestamp_for_display(str(value or "").strip(), self._display_timezone_id())

    def _current_alert_notifications_window(self) -> _MonitorAlertsDialog:
        dialog = self._alert_notifications_window
        if dialog is not None:
            return dialog
        dialog = _MonitorAlertsDialog(self, settings=self._theme_settings())
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self._alert_notifications_window = dialog
        return dialog

    def _dismiss_alert_notifications_window(self) -> None:
        dialog = self._alert_notifications_window
        if dialog is None:
            return
        dialog.dismiss_alerts()

    def _open_alert_notification_event(self, event_id: int) -> None:
        row = fetch_monitor_event(self._selected_profile_id, int(event_id))
        if isinstance(row, dict):
            self._open_event_popup(row)

    def _refresh_snmp_users_tree(self) -> None:
        self.snmp_users_tree.clear()
        for user in self._snmp_v3_users:
            item = QTreeWidgetItem(
                [
                    user.username,
                    user.auth_protocol,
                    user.priv_protocol,
                ]
            )
            item.setData(0, Qt.UserRole, user.to_dict())
            self.snmp_users_tree.addTopLevelItem(item)
        for index in range(self.snmp_users_tree.columnCount()):
            self.snmp_users_tree.resizeColumnToContents(index)
        self._update_snmp_user_buttons()

    def _update_snmp_user_buttons(self) -> None:
        has_selection = self.snmp_users_tree.currentItem() is not None
        snmp_v3 = self.snmp_enabled_input.isChecked() and self.snmp_v3_enabled_input.isChecked()
        self.edit_user_btn.setEnabled(snmp_v3 and has_selection)
        self.delete_user_btn.setEnabled(snmp_v3 and has_selection)

    def _refresh_alert_rules_tree(self) -> None:
        self.alert_rules_tree.clear()
        for rule in self._alert_rules:
            match_parts = [
                token
                for token in (
                    rule.source_contains,
                    rule.app_contains,
                    rule.trap_oid_contains,
                    rule.enterprise_oid_contains,
                    rule.text_contains,
                )
                if token
            ]
            item = QTreeWidgetItem(
                [
                    rule.name,
                    rule.protocol,
                    rule.severity_at_least or "(Any)",
                    "Yes" if rule.popup else "No",
                    "Yes" if rule.sound else "No",
                    " | ".join(match_parts)[:180],
                ]
            )
            item.setData(0, Qt.UserRole, rule.to_dict())
            self.alert_rules_tree.addTopLevelItem(item)
        for index in range(self.alert_rules_tree.columnCount()):
            self.alert_rules_tree.resizeColumnToContents(index)
        self._update_alert_rule_buttons()

    def _update_alert_rule_buttons(self) -> None:
        has_selection = self.alert_rules_tree.currentItem() is not None
        self.edit_rule_btn.setEnabled(has_selection)
        self.duplicate_rule_btn.setEnabled(has_selection)
        self.delete_rule_btn.setEnabled(has_selection)

    def _selected_profile_name(self) -> str:
        profile = self._profile_by_id(self._selected_profile_id)
        if profile is None:
            return DEFAULT_PROFILE_NAME
        return str(profile.get("name", "")).strip() or DEFAULT_PROFILE_NAME

    @Slot()
    def _on_profile_selection_changed(self) -> None:
        selected = self.profile_input.currentData()
        self._selected_profile_id = str(selected).strip() if isinstance(selected, str) else ""
        self._dismiss_alert_notifications_window()
        self._update_profile_buttons()
        self._persist_profiles()

    def _load_selected_profile(self) -> None:
        profile = self._profile_by_id(self._selected_profile_id)
        if profile is None:
            return
        self._dismiss_alert_notifications_window()
        config = SyslogSnmpMonitorConfig.from_dict(profile.get("config", {}))
        self._apply_profile_config(config)
        self.status_label.setText(f"Loaded monitor profile {self._selected_profile_name()}.")
        self._poll_runtime_state()

    def _prompt_profile_name(self, *, title: str, prompt: str, default_name: str, exclude_profile_id: str = "") -> str | None:
        entered, ok = QInputDialog.getText(self, title, prompt, QLineEdit.Normal, default_name)
        if not ok:
            return None
        trimmed = entered.strip()
        if not trimmed:
            return None
        return self._unique_profile_name(trimmed, exclude_profile_id=exclude_profile_id)

    def _unique_profile_name(self, base_name: str, *, exclude_profile_id: str = "") -> str:
        base = base_name.strip() or DEFAULT_PROFILE_NAME
        taken = {
            str(profile.get("name", "")).strip().lower()
            for profile in self._profiles
            if str(profile.get("id", "")).strip() != exclude_profile_id.strip()
        }
        if base.lower() not in taken:
            return base
        suffix = 2
        while True:
            candidate = f"{base} {suffix}"
            if candidate.lower() not in taken:
                return candidate
            suffix += 1

    def _save_current_profile_as(self) -> None:
        name = self._prompt_profile_name(
            title="Save Monitor Profile",
            prompt="Profile name:",
            default_name=self._unique_profile_name(DEFAULT_PROFILE_NAME),
        )
        if not name:
            return
        try:
            config = self._current_config()
        except ValueError as exc:
            QMessageBox.warning(self, "Save Monitor Profile", str(exc))
            return
        profile = self._new_profile(name, config)
        self._profiles.append(profile)
        self._selected_profile_id = str(profile["id"])
        self._refresh_profile_combo()
        self._persist_profile_config_to_disk()
        self._persist_profiles()
        self.status_label.setText(f"Saved monitor profile {name}.")

    def _update_selected_profile(self) -> None:
        profile = self._profile_by_id(self._selected_profile_id)
        if profile is None:
            return
        try:
            config = self._current_config()
        except ValueError as exc:
            QMessageBox.warning(self, "Update Monitor Profile", str(exc))
            return
        profile["config"] = config.to_dict()
        self._persist_profile_config_to_disk()
        self._persist_profiles()
        self.status_label.setText(f"Updated monitor profile {self._selected_profile_name()}.")

    def _rename_selected_profile(self) -> None:
        profile = self._profile_by_id(self._selected_profile_id)
        if profile is None:
            return
        renamed = self._prompt_profile_name(
            title="Rename Monitor Profile",
            prompt="Profile name:",
            default_name=self._selected_profile_name(),
            exclude_profile_id=self._selected_profile_id,
        )
        if not renamed:
            return
        profile["name"] = renamed
        self._refresh_profile_combo()
        self._persist_profiles()
        self.status_label.setText(f"Renamed monitor profile to {renamed}.")

    def _delete_selected_profile(self) -> None:
        if len(self._profiles) <= 1:
            return
        name = self._selected_profile_name()
        answer = QMessageBox.question(
            self,
            "Delete Monitor Profile",
            f"Delete monitor profile {name}?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self._profiles = [
            profile
            for profile in self._profiles
            if str(profile.get("id", "")).strip() != self._selected_profile_id
        ]
        self._selected_profile_id = str(self._profiles[0].get("id", "")) if self._profiles else ""
        self._refresh_profile_combo()
        self._load_selected_profile()
        self._persist_profiles()
        self.status_label.setText(f"Deleted monitor profile {name}.")

    def _persist_ephemeral_profile_state(self, *_args: object) -> None:
        if self._suspend_profile_sync:
            return
        profile = self._profile_by_id(self._selected_profile_id)
        if profile is None:
            return
        base = SyslogSnmpMonitorConfig.from_dict(profile.get("config", {}))
        updated = SyslogSnmpMonitorConfig(
            bind_host=base.bind_host,
            syslog_udp_enabled=base.syslog_udp_enabled,
            syslog_udp_port=base.syslog_udp_port,
            syslog_tcp_enabled=base.syslog_tcp_enabled,
            syslog_tcp_port=base.syslog_tcp_port,
            syslog_tls_enabled=base.syslog_tls_enabled,
            syslog_tls_port=base.syslog_tls_port,
            syslog_tls_cert_file=base.syslog_tls_cert_file,
            syslog_tls_key_file=base.syslog_tls_key_file,
            syslog_tls_ca_file=base.syslog_tls_ca_file,
            snmp_enabled=base.snmp_enabled,
            snmp_port=base.snmp_port,
            snmp_v1_enabled=base.snmp_v1_enabled,
            snmp_v2c_enabled=base.snmp_v2c_enabled,
            snmp_v3_enabled=base.snmp_v3_enabled,
            snmp_communities=base.snmp_communities,
            snmp_v3_users=list(base.snmp_v3_users),
            retention=dict(base.retention),
            alert_rules=list(base.alert_rules),
            filter_state=self._current_filter_state().to_dict(),
            layout_state=self._current_layout_state(),
            visible_columns=self._current_visible_columns(),
        )
        profile["config"] = updated.to_dict()
        self._persist_profiles()

    def _persist_profile_config_to_disk(self) -> None:
        if not self._selected_profile_id:
            return
        try:
            config = self._current_config()
        except ValueError:
            return
        write_syslog_snmp_monitor_config(self._selected_profile_id, config)

    def _sync_runtime_controls(self, *_args: object) -> None:
        tls_enabled = self.syslog_tls_enabled_input.isChecked()
        self.tls_group.setEnabled(tls_enabled)
        snmp_enabled = self.snmp_enabled_input.isChecked()
        self.snmp_group.setEnabled(snmp_enabled)
        snmp_v3 = snmp_enabled and self.snmp_v3_enabled_input.isChecked()
        self.snmp_users_tree.setEnabled(snmp_v3)
        self.add_user_btn.setEnabled(snmp_v3)
        selected_user = self.snmp_users_tree.currentItem() is not None
        self.edit_user_btn.setEnabled(snmp_v3 and selected_user)
        self.delete_user_btn.setEnabled(snmp_v3 and selected_user)

    def _on_runtime_config_changed(self, *_args: object) -> None:
        if self._suspend_profile_sync:
            return
        self._update_restart_required_label()
        self._persist_ephemeral_profile_state()

    def _update_restart_required_label(self, status: SyslogSnmpMonitorStatus | None = None) -> None:
        status = status or read_syslog_snmp_monitor_status(self._selected_profile_id)
        runtime = self._runtime_state(status)
        current_signature = self._runtime_config_signature(self._current_config_preview())
        requires_restart = runtime.can_stop and current_signature != self._last_started_signature
        if requires_restart:
            self.restart_required_label.setText("Changes are staged in the GUI. Stop / start the collector to apply listener, alert, or retention changes.")
        else:
            self.restart_required_label.setText("")

    @staticmethod
    def _listener_labels_for_config(config: SyslogSnmpMonitorConfig) -> list[str]:
        listeners: list[str] = []
        if config.syslog_udp_enabled:
            listeners.append(f"Syslog UDP {config.syslog_udp_port}")
        if config.syslog_tcp_enabled:
            listeners.append(f"Syslog TCP {config.syslog_tcp_port}")
        if config.syslog_tls_enabled:
            listeners.append(f"Syslog TLS {config.syslog_tls_port}")
        if config.snmp_enabled:
            listeners.append(f"SNMP Trap {config.snmp_port}")
        return listeners

    def _begin_runtime_start_tracking(self) -> None:
        self._pending_runtime_start_profile_id = self._selected_profile_id
        self._pending_runtime_start_since = time.monotonic()
        self._pending_runtime_error_message = ""

    def _clear_runtime_start_tracking(self) -> None:
        self._pending_runtime_start_profile_id = ""
        self._pending_runtime_start_since = 0.0
        self._pending_runtime_error_message = ""

    def _wait_for_runtime_stop(self, *, timeout_seconds: float = _RUNTIME_TRANSITION_TIMEOUT_SECONDS) -> bool:
        if not self._selected_profile_id:
            return True
        deadline = time.monotonic() + max(0.5, timeout_seconds)
        while time.monotonic() < deadline:
            status = read_syslog_snmp_monitor_status(self._selected_profile_id)
            if not self._runtime_state(status).can_stop:
                self._poll_runtime_state()
                return True
            QApplication.processEvents()
            time.sleep(0.05)
        self._poll_runtime_state()
        status = read_syslog_snmp_monitor_status(self._selected_profile_id)
        return not self._runtime_state(status).can_stop

    def _start_monitor(self) -> None:
        try:
            config = self._current_config()
        except ValueError as exc:
            QMessageBox.warning(self, "Start Monitor", str(exc))
            return
        write_syslog_snmp_monitor_config(self._selected_profile_id, config)
        write_syslog_snmp_monitor_status(
            self._selected_profile_id,
            SyslogSnmpMonitorStatus(
                state="starting",
                pid=None,
                message="Launching collector...",
                bind_host=config.bind_host,
                listeners=self._listener_labels_for_config(config),
            ),
        )
        try:
            if needs_syslog_snmp_monitor_gui_elevation(config):
                launch_syslog_snmp_monitor_helper_elevated(self._selected_profile_id)
            else:
                launch_syslog_snmp_monitor_helper(self._selected_profile_id)
        except Exception as exc:
            write_syslog_snmp_monitor_status(
                self._selected_profile_id,
                SyslogSnmpMonitorStatus(
                    state="stopped",
                    pid=None,
                    message="Collector stopped.",
                    bind_host=config.bind_host,
                    listeners=[],
                    error="",
                ),
            )
            QMessageBox.warning(self, "Start Monitor", str(exc))
            return
        self._begin_runtime_start_tracking()
        self.status_label.setText("Starting syslog / SNMP collector...")
        self._set_runtime_badge("starting")
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self._last_started_signature = self._runtime_config_signature(config)
        self._poll_runtime_state()

    def _stop_monitor(self) -> None:
        if not self._selected_profile_id:
            return
        request_syslog_snmp_monitor_stop(self._selected_profile_id)
        self.status_label.setText("Stop requested for syslog / SNMP collector.")
        self._poll_runtime_state()

    def _confirm_close_stops_monitor(self) -> bool:
        if not self._selected_profile_id:
            return True
        status = read_syslog_snmp_monitor_status(self._selected_profile_id)
        if not self._runtime_state(status).can_stop:
            return True
        answer = QMessageBox.question(
            self,
            "Close Syslog / SNMP Monitor",
            (
                f"The collector for profile {self._selected_profile_name()} is still running.\n\n"
                "SnakeSh must stop it before this window closes.\n\n"
                "Stop the collector and close the monitor now?"
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if answer != QMessageBox.Yes:
            return False
        try:
            request_syslog_snmp_monitor_stop(self._selected_profile_id)
        except Exception as exc:
            QMessageBox.warning(self, "Close Syslog / SNMP Monitor", str(exc))
            return False
        self.status_label.setText("Stopping syslog / SNMP collector before closing...")
        QApplication.processEvents()
        if self._wait_for_runtime_stop():
            return True
        QMessageBox.warning(
            self,
            "Close Syslog / SNMP Monitor",
            "SnakeSh is still waiting for the collector to stop. The monitor window will remain open.",
        )
        return False

    def _open_profile_folder(self) -> None:
        paths = syslog_snmp_monitor_profile_paths(self._selected_profile_id)
        paths.root.mkdir(parents=True, exist_ok=True)
        if not open_local_path(paths.root):
            QMessageBox.warning(self, "Open Profile Folder", f"Unable to open:\n{paths.root}")

    def _archive_now(self) -> None:
        status = read_syslog_snmp_monitor_status(self._selected_profile_id)
        if self._runtime_state(status).can_stop:
            QMessageBox.warning(self, "Archive Now", "Archive Now is only available while the collector is stopped.")
            return
        archived = archive_monitor_events(self._selected_profile_id, retention=self._current_retention_policy())
        self.status_label.setText(f"Archived {archived} event(s).")
        self._poll_runtime_state()

    def _purge_now(self) -> None:
        status = read_syslog_snmp_monitor_status(self._selected_profile_id)
        if self._runtime_state(status).can_stop:
            QMessageBox.warning(self, "Purge Now", "Purge Now is only available while the collector is stopped.")
            return
        removed = purge_monitor_archives(self._selected_profile_id, retention=self._current_retention_policy())
        self.status_label.setText(f"Purged {removed} archive file(s).")
        self._poll_runtime_state()

    def _clear_database(self) -> None:
        if not self._selected_profile_id:
            return
        status = read_syslog_snmp_monitor_status(self._selected_profile_id)
        if self._runtime_state(status).can_stop:
            QMessageBox.warning(self, "Clear Database", "Clear Database is only available while the collector is stopped.")
            return
        answer = QMessageBox.warning(
            self,
            "Clear Database",
            (
                f"Clear all live events, all pending notifications, and all archived files for profile "
                f"{self._selected_profile_name()}?\n\nThis action cannot be undone."
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        result = clear_monitor_profile_data(self._selected_profile_id)
        self._dismiss_alert_notifications_window()
        self._reload_events()
        self._refresh_storage_stats()
        self._update_restart_required_label()
        self.status_label.setText(
            f"Cleared {result.live_event_count} live event(s), {result.notification_count} notification(s), "
            f"and {result.archive_file_count} archive file(s)."
        )

    def _poll_runtime_state(self) -> None:
        if not self._selected_profile_id:
            return
        status = read_syslog_snmp_monitor_status(self._selected_profile_id)
        runtime = self._runtime_state(status)
        start_pending = self._pending_runtime_start_profile_id == self._selected_profile_id
        stale_start = (
            start_pending
            and runtime.starting
            and self._pending_runtime_start_since > 0.0
            and (time.monotonic() - self._pending_runtime_start_since) >= _RUNTIME_START_STALE_HINT_SECONDS
            and platform.system().lower().startswith("win")
        )
        self.start_btn.setEnabled(not runtime.can_stop)
        self.stop_btn.setEnabled(runtime.can_stop)
        self.archive_now_btn.setEnabled(not runtime.can_stop)
        self.purge_now_btn.setEnabled(not runtime.can_stop)
        self.clear_database_btn.setEnabled(not runtime.can_stop)
        if runtime.error:
            error_message = status.message or status.error or "Collector reported an error."
            if start_pending and error_message != self._pending_runtime_error_message:
                self._pending_runtime_error_message = error_message
                QMessageBox.warning(self, "Start Monitor", error_message)
            self._clear_runtime_start_tracking()
            self._set_runtime_badge("error")
            self.status_label.setText(error_message)
        elif runtime.running:
            self._clear_runtime_start_tracking()
            self._set_runtime_badge("running")
            listeners = ", ".join(status.listeners) if status.listeners else "listeners active"
            self.status_label.setText(
                f"Running on {status.bind_host or self.bind_host_input.text()} with {listeners}. "
                f"Live events: {status.event_count}. Pending alerts: {status.alert_count}."
            )
        elif runtime.starting:
            self._set_runtime_badge("starting")
            if stale_start:
                self.status_label.setText(
                    "Collector is still starting. On Windows, confirm any firewall prompt and verify the listener "
                    "settings if the state does not change to Running."
                )
            else:
                self.status_label.setText(status.message or "Collector starting...")
        else:
            self._clear_runtime_start_tracking()
            self._set_runtime_badge("stopped")
            self.status_label.setText(status.message or "Collector stopped.")
        self._update_restart_required_label(status)
        self._reload_events()
        self._refresh_storage_stats()
        self._show_notifications()

    def _reload_events(self) -> None:
        if not self._selected_profile_id:
            return
        selected_id = self._selected_event_id()
        rows = fetch_monitor_events(self._selected_profile_id, self._current_filter_state(), limit=1000)
        self._current_rows = rows
        self.events_tree.clear()
        for row in rows:
            item = QTreeWidgetItem([self._display_value(row, key) for key, _label in _EVENT_COLUMNS])
            item.setData(0, Qt.UserRole, int(row.get("id", 0) or 0))
            item.setData(0, Qt.UserRole + 1, row)
            self.events_tree.addTopLevelItem(item)
        for index in range(self.events_tree.columnCount()):
            if not self.events_tree.isColumnHidden(index):
                self.events_tree.resizeColumnToContents(index)
        if selected_id:
            self._restore_selected_event(selected_id)
        if self.events_tree.currentItem() is None and self.events_tree.topLevelItemCount() > 0:
            self.events_tree.setCurrentItem(self.events_tree.topLevelItem(0))
        if self.events_tree.topLevelItemCount() == 0:
            self.event_summary_text.setPlainText("No events match the current filters.")
            self.event_raw_text.clear()
            self.event_json_text.clear()
        self._update_copy_buttons()
        self._refresh_dashboard()

    def _refresh_storage_stats(self) -> None:
        stats = monitor_storage_stats(self._selected_profile_id)
        self.live_event_count_label.setText(str(stats.live_event_count))
        self.pending_alert_count_label.setText(str(stats.notification_count))
        self.db_size_label.setText(self._format_bytes(stats.db_size_bytes))
        self.archive_file_count_label.setText(str(stats.archive_file_count))
        self.archive_size_label.setText(self._format_bytes(stats.archive_size_bytes))
        self.oldest_live_label.setText(self._format_display_timestamp(stats.oldest_live_event_at) or "-")
        self.newest_live_label.setText(self._format_display_timestamp(stats.newest_live_event_at) or "-")
        self.retention_status_label.setText(
            "Retention is editable at any time. If the collector is currently running, listener, alert, and retention changes are marked restart required and apply after stop / start. "
            "Archive Now, Purge Now, and Clear Database remain available only while stopped."
        )

    def _show_notifications(self) -> None:
        notifications = fetch_unshown_notifications(self._selected_profile_id, limit=20)
        if not notifications:
            return
        ids = [int(notification.get("id", 0) or 0) for notification in notifications if int(notification.get("id", 0) or 0) > 0]
        if any(bool(notification.get("play_sound", False)) for notification in notifications):
            self._alert_sound_player.play()
        window = self._current_alert_notifications_window()
        window.add_notifications(
            profile_name=self._selected_profile_name(),
            notifications=notifications,
            format_timestamp=self._format_display_timestamp,
            open_event_callback=self._open_alert_notification_event,
        )
        mark_notifications_shown(self._selected_profile_id, ids)

    def _selected_event_id(self) -> int:
        item = self.events_tree.currentItem()
        if item is None:
            return 0
        try:
            return int(item.data(0, Qt.UserRole) or 0)
        except (TypeError, ValueError):
            return 0

    def _restore_selected_event(self, event_id: int) -> None:
        for index in range(self.events_tree.topLevelItemCount()):
            item = self.events_tree.topLevelItem(index)
            try:
                item_id = int(item.data(0, Qt.UserRole) or 0)
            except (TypeError, ValueError):
                item_id = 0
            if item_id == event_id:
                self.events_tree.setCurrentItem(item)
                return

    @Slot()
    def _on_event_selection_changed(self) -> None:
        self._update_copy_buttons()
        item = self.events_tree.currentItem()
        if item is None:
            return
        row = self._event_row_from_item(item)
        if not isinstance(row, dict):
            return
        self.event_summary_text.setPlainText(self._event_summary_text(row))
        self.event_raw_text.setPlainText(str(row.get("raw_payload", "")))
        self.event_json_text.setPlainText(json.dumps(row, indent=2, ensure_ascii=False))

    @Slot(QTreeWidgetItem, int)
    def _on_event_item_clicked(self, _item: QTreeWidgetItem, column: int) -> None:
        self._last_event_column = max(0, int(column))

    @Slot(QTreeWidgetItem, int)
    def _on_event_item_double_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        self._last_event_column = max(0, int(column))
        row = self._event_row_from_item(item)
        if not isinstance(row, dict):
            return
        self._open_event_popup(row)

    def _set_current_event_column(self, section: int) -> None:
        self._last_event_column = max(0, int(section))

    def _update_copy_buttons(self) -> None:
        has_rows = self.events_tree.topLevelItemCount() > 0
        has_selection = bool(self.events_tree.selectedItems())
        has_current = self.events_tree.currentItem() is not None
        self.copy_cell_btn.setEnabled(has_current)
        self.copy_row_btn.setEnabled(has_current)
        self.copy_selected_btn.setEnabled(has_selection)
        self.copy_column_btn.setEnabled(has_rows)
        self.copy_all_btn.setEnabled(has_rows)
        self.export_selected_btn.setEnabled(has_selection)

    def _visible_column_indexes(self) -> list[int]:
        return [index for index in range(self.events_tree.columnCount()) if not self.events_tree.isColumnHidden(index)]

    def _copy_current_cell(self) -> None:
        item = self.events_tree.currentItem()
        if item is None:
            return
        text = item.text(self._last_event_column)
        QApplication.clipboard().setText(text)
        self.status_label.setText("Copied current cell.")

    def _copy_current_row(self) -> None:
        item = self.events_tree.currentItem()
        if item is None:
            return
        self._copy_rows([item], include_headers=True, noun="row")

    def _copy_selected_rows(self) -> None:
        items = self.events_tree.selectedItems()
        if not items:
            return
        self._copy_rows(items, include_headers=True, noun="row")

    def _copy_current_column(self) -> None:
        indexes = self._visible_column_indexes()
        if self._last_event_column not in indexes and indexes:
            self._last_event_column = indexes[0]
        label = self.events_tree.headerItem().text(self._last_event_column)
        lines = [label]
        for index in range(self.events_tree.topLevelItemCount()):
            lines.append(self.events_tree.topLevelItem(index).text(self._last_event_column))
        QApplication.clipboard().setText("\n".join(lines))
        self.status_label.setText(f"Copied column {label}.")

    def _copy_all_rows(self) -> None:
        items = [self.events_tree.topLevelItem(index) for index in range(self.events_tree.topLevelItemCount())]
        self._copy_rows(items, include_headers=True, noun="row")

    def _copy_rows(self, items: list[QTreeWidgetItem], *, include_headers: bool, noun: str) -> None:
        visible_indexes = self._visible_column_indexes()
        headers = [self.events_tree.headerItem().text(index) for index in visible_indexes]
        rows = [[item.text(index) for index in visible_indexes] for item in items]
        lines: list[str] = []
        if include_headers:
            lines.append("\t".join(headers))
        lines.extend("\t".join(row) for row in rows)
        QApplication.clipboard().setText("\n".join(lines))
        plural = noun if len(rows) == 1 else f"{noun}s"
        self.status_label.setText(f"Copied {len(rows)} {plural}.")

    def _show_event_context_menu(self, position) -> None:
        item = self.events_tree.itemAt(position)
        if item is not None:
            index = self.events_tree.indexAt(position)
            if index.isValid():
                self._last_event_column = index.column()
        menu = QMenu(self)
        copy_cell_action = menu.addAction("Copy Cell")
        copy_row_action = menu.addAction("Copy Row")
        copy_selected_action = menu.addAction("Copy Selected Rows")
        copy_all_action = menu.addAction("Copy All Visible Rows")
        menu.addSeparator()
        export_selected_action = menu.addAction("Export Selected...")
        chosen = menu.exec(self.events_tree.viewport().mapToGlobal(position))
        if chosen == copy_cell_action:
            self._copy_current_cell()
        elif chosen == copy_row_action:
            self._copy_current_row()
        elif chosen == copy_selected_action:
            self._copy_selected_rows()
        elif chosen == copy_all_action:
            self._copy_all_rows()
        elif chosen == export_selected_action:
            self._export_selected_rows()

    def _show_event_header_context_menu(self, position) -> None:
        section = self.events_tree.header().logicalIndexAt(position)
        if section >= 0:
            self._last_event_column = int(section)
        menu = QMenu(self)
        copy_column_action = menu.addAction("Copy Column")
        chosen = menu.exec(self.events_tree.header().mapToGlobal(position))
        if chosen == copy_column_action:
            self._copy_current_column()

    def _show_columns_menu(self) -> None:
        menu = QMenu(self)
        actions: list[QAction] = []
        for key, label in _EVENT_COLUMNS:
            action = QAction(label, menu)
            action.setCheckable(True)
            action.setChecked(not self.events_tree.isColumnHidden(_EVENT_COLUMN_INDEX[key]))
            menu.addAction(action)
            actions.append(action)
        chosen = menu.exec(self.columns_btn.mapToGlobal(self.columns_btn.rect().bottomLeft()))
        if chosen is None:
            return
        selected_label = chosen.text()
        toggled_key = next((key for key, label in _EVENT_COLUMNS if label == selected_label), "")
        if not toggled_key:
            return
        currently_visible = self._current_visible_columns()
        if toggled_key in currently_visible and len(currently_visible) == 1:
            return
        self.events_tree.setColumnHidden(_EVENT_COLUMN_INDEX[toggled_key], not chosen.isChecked())
        self._persist_ephemeral_profile_state()
        self._update_copy_buttons()

    def _export_filtered_csv(self) -> None:
        path, _selected = QFileDialog.getSaveFileName(self, "Export Filtered Events as CSV", "", "CSV Files (*.csv)")
        if not path:
            return
        payload = export_monitor_events_csv(self._selected_profile_id, self._current_filter_state(), limit=5000)
        if not payload.strip():
            QMessageBox.information(self, "Export CSV", "There are no events to export.")
            return
        Path(path).write_text(payload, encoding="utf-8")
        self.status_label.setText(f"Exported filtered CSV to {path}.")

    def _export_filtered_json(self) -> None:
        path, _selected = QFileDialog.getSaveFileName(self, "Export Filtered Events as JSON", "", "JSON Files (*.json)")
        if not path:
            return
        payload = export_monitor_events_json(self._selected_profile_id, self._current_filter_state(), limit=5000)
        if payload.strip() == "[]":
            QMessageBox.information(self, "Export JSON", "There are no events to export.")
            return
        Path(path).write_text(payload, encoding="utf-8")
        self.status_label.setText(f"Exported filtered JSON to {path}.")

    def _export_selected_rows(self) -> None:
        rows = self._selected_event_rows()
        if not rows:
            QMessageBox.information(self, "Export Selected", "Select one or more rows first.")
            return
        path, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Export Selected Rows",
            "",
            "CSV Files (*.csv);;JSON Files (*.json)",
        )
        if not path:
            return
        if selected_filter.startswith("JSON") or path.lower().endswith(".json"):
            Path(path).write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
        else:
            headers = [label for key, label in _EVENT_COLUMNS if key in self._current_visible_columns()]
            visible_keys = self._current_visible_columns()
            lines = [",".join(_csv_escape(header) for header in headers)]
            for row in rows:
                lines.append(",".join(_csv_escape(self._display_value(row, key)) for key in visible_keys))
            Path(path).write_text("\n".join(lines), encoding="utf-8")
        self.status_label.setText(f"Exported {len(rows)} selected event(s) to {path}.")

    def _selected_event_rows(self) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for item in self.events_tree.selectedItems():
            row = item.data(0, Qt.UserRole + 1)
            if isinstance(row, dict):
                rows.append(row)
        return rows

    def _clear_filters(self) -> None:
        self._apply_filter_state(MonitorQueryFilters())
        self._reload_events()
        self._persist_ephemeral_profile_state()

    def _on_display_timezone_changed(self, _index: int) -> None:
        if self._suspend_profile_sync:
            return
        self._reload_events()
        self._refresh_storage_stats()
        self._persist_ephemeral_profile_state()

    def _add_snmp_v3_user(self) -> None:
        dialog = _SnmpV3UserDialog(self)
        if not dialog.exec():
            return
        user = dialog.build_user()
        self._snmp_v3_users.append(user)
        self._refresh_snmp_users_tree()
        self._on_runtime_config_changed()

    def _selected_snmp_v3_user_index(self) -> int:
        item = self.snmp_users_tree.currentItem()
        if item is None:
            return -1
        username = item.text(0)
        for index, user in enumerate(self._snmp_v3_users):
            if user.username == username:
                return index
        return -1

    def _edit_selected_snmp_v3_user(self) -> None:
        index = self._selected_snmp_v3_user_index()
        if index < 0:
            return
        dialog = _SnmpV3UserDialog(self, user=self._snmp_v3_users[index])
        if not dialog.exec():
            return
        self._snmp_v3_users[index] = dialog.build_user()
        self._refresh_snmp_users_tree()
        self._on_runtime_config_changed()

    def _delete_selected_snmp_v3_user(self) -> None:
        index = self._selected_snmp_v3_user_index()
        if index < 0:
            return
        username = self._snmp_v3_users[index].username
        del self._snmp_v3_users[index]
        self._refresh_snmp_users_tree()
        self._on_runtime_config_changed()
        self.status_label.setText(f"Deleted SNMPv3 user {username}.")

    def _selected_alert_rule_index(self) -> int:
        item = self.alert_rules_tree.currentItem()
        if item is None:
            return -1
        raw = item.data(0, Qt.UserRole)
        if not isinstance(raw, dict):
            return -1
        rule_id = str(raw.get("id", "")).strip()
        for index, rule in enumerate(self._alert_rules):
            if rule.rule_id == rule_id:
                return index
        return -1

    def _add_alert_rule(self) -> None:
        dialog = _AlertRuleDialog(self)
        if not dialog.exec():
            return
        self._alert_rules.append(dialog.build_rule())
        self._refresh_alert_rules_tree()
        self._on_runtime_config_changed()

    def _edit_selected_alert_rule(self) -> None:
        index = self._selected_alert_rule_index()
        if index < 0:
            return
        dialog = _AlertRuleDialog(self, rule=self._alert_rules[index])
        if not dialog.exec():
            return
        self._alert_rules[index] = dialog.build_rule()
        self._refresh_alert_rules_tree()
        self._on_runtime_config_changed()

    def _duplicate_selected_alert_rule(self) -> None:
        index = self._selected_alert_rule_index()
        if index < 0:
            return
        original = self._alert_rules[index]
        duplicated = MonitorAlertRule.from_dict(
            {
                **original.to_dict(),
                "id": uuid4().hex,
                "name": self._unique_alert_rule_name(f"{original.name} Copy"),
            }
        )
        self._alert_rules.append(duplicated)
        self._refresh_alert_rules_tree()
        self._on_runtime_config_changed()

    def _delete_selected_alert_rule(self) -> None:
        index = self._selected_alert_rule_index()
        if index < 0:
            return
        name = self._alert_rules[index].name
        del self._alert_rules[index]
        self._refresh_alert_rules_tree()
        self._on_runtime_config_changed()
        self.status_label.setText(f"Deleted alert rule {name}.")

    def _unique_alert_rule_name(self, base_name: str) -> str:
        base = base_name.strip() or "Alert Rule"
        taken = {rule.name.strip().lower() for rule in self._alert_rules if rule.name.strip()}
        if base.lower() not in taken:
            return base
        suffix = 2
        while True:
            candidate = f"{base} {suffix}"
            if candidate.lower() not in taken:
                return candidate
            suffix += 1

    def _refresh_dashboard(self) -> None:
        rows = list(self._current_rows)
        self._set_bar_chart(self.event_rate_chart, "Event Rate Over Time", self._event_rate_counts(rows))
        self._set_pie_chart(
            self.severity_chart,
            "Syslog Severity Distribution",
            Counter(
                str(row.get("severity_name", "")).strip()
                for row in rows
                if str(row.get("severity_name", "")).strip()
            ),
            click_handler=lambda value: self._drilldown_set_combo(self.severity_input, value),
        )
        source_counts = Counter(_source_text(row) for row in rows if _source_text(row))
        app_counts = Counter(str(row.get("app_name", "")).strip() for row in rows if str(row.get("app_name", "")).strip())
        trap_counts = Counter(
            str(row.get("notification_oid", "")).strip()
            for row in rows
            if str(row.get("notification_oid", "")).strip()
        )
        alert_counts = self._event_rate_counts([row for row in rows if bool(row.get("alerted", False))])
        self._set_bar_chart(
            self.top_sources_chart,
            "Top Sources",
            source_counts,
            click_handler=lambda value: self._set_line_edit_filter(self.source_input, value),
        )
        self._set_bar_chart(
            self.top_apps_chart,
            "Top Apps / Tags",
            app_counts,
            click_handler=lambda value: self._set_line_edit_filter(self.app_name_input, value),
        )
        self._set_bar_chart(
            self.top_traps_chart,
            "Top Trap OIDs",
            trap_counts,
            click_handler=lambda value: self._set_line_edit_filter(self.notification_oid_input, value),
        )
        self._set_bar_chart(self.alerts_chart, "Alerts Over Time", alert_counts)

    def _set_bar_chart(self, view: QChartView, title: str, counts: Counter[str], click_handler=None) -> None:
        chart = QChart()
        chart.setTitle(title)
        chart.legend().setVisible(False)
        theme = self._apply_chart_theme(chart, series_count=1)
        items = counts.most_common(8)
        if not items:
            chart.setTitle(f"{title}\nNo data for the current filtered subset.")
            view.setChart(chart)
            return
        labels = [label for label, _value in items]
        values = [float(value) for _label, value in items]
        series = QBarSeries(chart)
        bar_set = QBarSet(title, chart)
        bar_color = theme["series_colors"][0]
        bar_set.setColor(bar_color)
        bar_set.setBorderColor(bar_color.darker(132))
        for value in values:
            bar_set.append(value)
        series.append(bar_set)
        chart.addSeries(series)
        axis_x = QBarCategoryAxis(chart)
        axis_x.append([_truncate_label(label) for label in labels])
        axis_y = QValueAxis(chart)
        axis_y.setRange(0, max(values) + max(1.0, max(values) * 0.2))
        axis_y.applyNiceNumbers()
        chart.addAxis(axis_x, Qt.AlignBottom)
        chart.addAxis(axis_y, Qt.AlignLeft)
        series.attachAxis(axis_x)
        series.attachAxis(axis_y)
        self._style_chart_axis(axis_x, theme)
        self._style_chart_axis(axis_y, theme)
        if click_handler is not None:
            bar_set.clicked.connect(lambda index, values=labels: click_handler(values[index]) if 0 <= index < len(values) else None)
        view.setChart(chart)

    def _set_pie_chart(self, view: QChartView, title: str, counts: Counter[str], click_handler=None) -> None:
        chart = QChart()
        chart.setTitle(title)
        items = counts.most_common()
        theme = self._apply_chart_theme(chart, series_count=max(1, len(items)))
        if not items:
            chart.setTitle(f"{title}\nNo data for the current filtered subset.")
            view.setChart(chart)
            return
        series = QPieSeries(chart)
        colors = theme["series_colors"]
        for index, (label, value) in enumerate(items):
            slice_obj = series.append(label, float(value))
            color = colors[index % len(colors)]
            slice_obj.setBrush(QBrush(color))
            slice_obj.setBorderColor(color.darker(132))
            if click_handler is not None:
                slice_obj.clicked.connect(lambda label_value=label: click_handler(label_value))
        chart.addSeries(series)
        chart.legend().setVisible(True)
        view.setChart(chart)

    def _event_rate_counts(self, rows: list[dict[str, object]]) -> Counter[str]:
        counts: Counter[str] = Counter()
        display_timezone = self._display_timezone_id()
        for row in rows:
            received = str(row.get("received_ts", "")).strip()
            if not received:
                continue
            counts[_time_bucket(received, display_timezone)] += 1
        return Counter(dict(sorted(counts.items())[-8:]))

    def _drilldown_set_combo(self, combo: QComboBox, value: str) -> None:
        self._set_combo(combo, value)
        self.tabs.setCurrentWidget(self.search_page)
        self._reload_events()
        self._persist_ephemeral_profile_state()

    def _set_line_edit_filter(self, field: QLineEdit, value: str) -> None:
        field.setText(value)
        self.tabs.setCurrentWidget(self.search_page)
        self._reload_events()
        self._persist_ephemeral_profile_state()

    def _event_summary_text(self, row: dict[str, object]) -> str:
        fields = [
            ("Received", self._format_display_timestamp(row.get("received_ts", ""))),
            ("Event Time", self._format_display_timestamp(row.get("event_ts", ""))),
            ("Source", _source_text(row)),
            ("Listener", row.get("listener", "")),
            ("Protocol", row.get("protocol", "")),
            ("Transport", row.get("transport", "")),
            ("Facility", row.get("facility_name", "")),
            ("Severity", row.get("severity_name", "")),
            ("Hostname", row.get("syslog_hostname", "")),
            ("App / Tag", row.get("app_name", "")),
            ("PID", row.get("procid", "")),
            ("MsgID", row.get("msgid", "")),
            ("Structured Data", row.get("structured_data", "")),
            ("Message", row.get("message_text", "")),
            ("SNMP Version", row.get("snmp_version", "")),
            ("SNMP Security", row.get("snmp_security_name", "")),
            ("Community", row.get("snmp_community", "")),
            ("User", row.get("snmp_user", "")),
            ("Engine ID", row.get("snmp_engine_id", "")),
            ("Context", row.get("snmp_context_name", "")),
            ("Notification OID", row.get("notification_oid", "")),
            ("Enterprise OID", row.get("enterprise_oid", "")),
            ("Uptime", row.get("snmp_uptime", "")),
            ("Varbind Summary", row.get("varbind_summary", "")),
            ("Alerted", "Yes" if bool(row.get("alerted", False)) else "No"),
        ]
        return "\n".join(f"{label}: {value}" for label, value in fields if str(value).strip())

    def _display_value(self, row: dict[str, object], key: str) -> str:
        if key == "source":
            return _source_text(row)
        if key == "alerted":
            return "Yes" if bool(row.get("alerted", False)) else "No"
        if key in {"received_ts", "event_ts"}:
            return self._format_display_timestamp(row.get(key, ""))
        value = row.get(key, "")
        return str(value).strip()

    def _event_row_from_item(self, item: QTreeWidgetItem | None) -> dict[str, object] | None:
        if item is None:
            return None
        row = item.data(0, Qt.UserRole + 1)
        if isinstance(row, dict):
            return row
        try:
            event_id = int(item.data(0, Qt.UserRole) or 0)
        except (TypeError, ValueError):
            event_id = 0
        return fetch_monitor_event(self._selected_profile_id, event_id) if event_id else None

    def _open_event_popup(self, row: dict[str, object]) -> _EventDetailsDialog:
        title_source = _source_text(row) or "Monitor Event"
        dialog = _EventDetailsDialog(
            self,
            title=f"Event Details - {title_source}",
            summary_text=self._event_summary_text(row),
            raw_text=str(row.get("raw_payload", "")),
            json_text=json.dumps(row, indent=2, ensure_ascii=False),
            settings=self._theme_settings(),
        )
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

        def _forget_popup(*_args: object, popup: _EventDetailsDialog = dialog) -> None:
            if popup in self._event_detail_windows:
                self._event_detail_windows.remove(popup)

        dialog.destroyed.connect(_forget_popup)
        self._event_detail_windows.append(dialog)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        return dialog

    @staticmethod
    def _set_combo(combo: QComboBox, value: str) -> None:
        target = value.strip()
        index = combo.findData(target)
        combo.setCurrentIndex(index if index >= 0 else 0)

    @staticmethod
    def _format_bytes(value: int) -> str:
        size = float(max(0, int(value)))
        units = ["B", "KB", "MB", "GB", "TB"]
        index = 0
        while size >= 1024.0 and index < len(units) - 1:
            size /= 1024.0
            index += 1
        if index == 0:
            return f"{int(size)} {units[index]}"
        return f"{size:.1f} {units[index]}"

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if not self._confirm_close_stops_monitor():
            event.ignore()
            return
        self._poll_timer.stop()
        self._dismiss_alert_notifications_window()
        self._persist_ephemeral_profile_state()
        self._persist_splitter_state()
        for popup in list(self._event_detail_windows):
            try:
                popup.close()
            except RuntimeError:
                continue
        super().closeEvent(event)


def _normalize_tab_id(value: str) -> str:
    cleaned = value.strip().lower()
    return cleaned if cleaned in _TAB_IDS_IN_ORDER else _TAB_MONITOR


def _tab_id_from_index(index: int) -> str:
    if 0 <= index < len(_TAB_IDS_IN_ORDER):
        return _TAB_IDS_IN_ORDER[index]
    return _TAB_MONITOR


def _tab_index_from_id(tab_id: str) -> int:
    normalized = _normalize_tab_id(tab_id)
    try:
        return _TAB_IDS_IN_ORDER.index(normalized)
    except ValueError:
        return 0


def _source_text(row: dict[str, object]) -> str:
    source_ip = str(row.get("source_ip", "")).strip()
    source_host = str(row.get("source_host", "")).strip()
    try:
        source_port = int(row.get("source_port", 0) or 0)
    except (TypeError, ValueError):
        source_port = 0
    base = source_host or source_ip
    if source_host and source_ip and source_host != source_ip:
        base = f"{source_host} ({source_ip})"
    return f"{base}:{source_port}" if source_port else base


@lru_cache(maxsize=1)
def _available_display_timezones() -> tuple[str, ...]:
    return tuple(sorted(available_timezones()))


def _normalize_display_timezone_id(value: str) -> str:
    cleaned = value.strip()
    if not cleaned or cleaned == _DISPLAY_TIMEZONE_UTC:
        return _DISPLAY_TIMEZONE_UTC
    if cleaned == _DISPLAY_TIMEZONE_SYSTEM:
        return _DISPLAY_TIMEZONE_SYSTEM
    return cleaned if cleaned in _available_display_timezones() else _DISPLAY_TIMEZONE_UTC


def _display_timezone_info(value: str):
    normalized = _normalize_display_timezone_id(value)
    if normalized == _DISPLAY_TIMEZONE_SYSTEM:
        return datetime.now().astimezone().tzinfo or UTC
    if normalized == _DISPLAY_TIMEZONE_UTC:
        return UTC
    try:
        return ZoneInfo(normalized)
    except ZoneInfoNotFoundError:
        return UTC


def _format_timestamp_for_display(value: str, display_timezone: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        return ""
    try:
        parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(_display_timezone_info(display_timezone)).isoformat(sep=" ", timespec="seconds")
    except Exception:
        return cleaned


def _time_bucket(value: str, display_timezone: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        return ""
    try:
        parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(_display_timezone_info(display_timezone)).strftime("%m-%d %H:%M")
    except Exception:
        return cleaned[:16]


def _truncate_label(value: str, *, limit: int = 22) -> str:
    cleaned = value.strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "..."


def _csv_escape(value: str) -> str:
    cleaned = value.replace('"', '""')
    if any(char in cleaned for char in [",", "\n", '"']):
        return f'"{cleaned}"'
    return cleaned
