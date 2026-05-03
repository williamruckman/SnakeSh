from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import socket
import shutil
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QByteArray, QTimer, Qt, QUrl
from PySide6.QtGui import QCloseEvent, QDesktopServices, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from snakesh.services.network_inspector import collect_interface_info
from snakesh.services.settings_service import AppSettings
from snakesh.services.web_server_service import (
    WebServerConfig,
    WebServerStatus,
    create_web_server_archived_log_path,
    create_web_server_instance_dir,
    is_web_server_running,
    launch_web_server_helper,
    launch_web_server_helper_elevated,
    needs_gui_elevation,
    prune_web_server_log_files,
    read_web_server_config,
    read_web_server_status,
    request_web_server_stop,
    validate_web_server_config,
    web_server_instance_paths,
    web_server_logs_root,
    write_web_server_config,
    write_web_server_status,
)
from snakesh.ui.bind_host_selector import BindHostSelector
from snakesh.ui.desktop_open import open_local_path
from snakesh.ui.theme import apply_terminal_output_font


_RUNTIME_BADGE_STYLES = {
    "running": ("Running", "#166534"),
    "starting": ("Starting", "#92400e"),
    "stopping": ("Stopping", "#92400e"),
    "stopped": ("Stopped", "#b91c1c"),
    "error": ("Error", "#b91c1c"),
}


@dataclass(frozen=True, slots=True)
class _WebServerRuntimeState:
    status: WebServerStatus
    status_unavailable: bool
    listener_active: bool
    starting_without_status: bool
    running: bool
    can_request_stop: bool
    stop_requested: bool


class WebServerDialog(QDialog):
    def __init__(
        self,
        parent=None,
        *,
        profiles: list[dict[str, object]] | None = None,
        selected_profile_id: str = "",
        on_profiles_changed=None,
        log_cleanup_enabled: bool = True,
        log_retention_days: int = 7,
        splitter_state_b64: str = "",
        on_splitter_state_changed=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Web Server")
        self.resize(980, 860)
        self._last_log_size = 0
        self._current_log_path = ""
        self._profiles = self._normalize_profiles(profiles or [])
        self._selected_profile_id = selected_profile_id.strip()
        self._on_profiles_changed = on_profiles_changed
        self._on_splitter_state_changed = on_splitter_state_changed
        self._splitter_state_b64 = splitter_state_b64.strip()
        self._log_cleanup_enabled = bool(log_cleanup_enabled)
        self._log_retention_days = max(1, int(log_retention_days))
        self._updating_recommended_port = False
        self._port_follows_recommendation = True
        self._last_recommended_port = 8000
        self._launch_requested = False
        self._instance_dir = create_web_server_instance_dir()
        self._paths = web_server_instance_paths(self._instance_dir)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        profile_row = QHBoxLayout()
        profile_row.addWidget(QLabel("Profile"), 0)
        self.profile_input = QComboBox()
        self.profile_input.setMinimumContentsLength(24)
        self.load_profile_btn = QPushButton("Load")
        self.save_profile_btn = QPushButton("Save As...")
        self.update_profile_btn = QPushButton("Update")
        self.rename_profile_btn = QPushButton("Rename...")
        self.delete_profile_btn = QPushButton("Delete")
        profile_row.addWidget(self.profile_input, 1)
        profile_row.addWidget(self.load_profile_btn, 0)
        profile_row.addWidget(self.save_profile_btn, 0)
        profile_row.addWidget(self.update_profile_btn, 0)
        profile_row.addWidget(self.rename_profile_btn, 0)
        profile_row.addWidget(self.delete_profile_btn, 0)
        root.addLayout(profile_row)

        self._main_splitter = QSplitter(Qt.Vertical, self)
        self._main_splitter.setChildrenCollapsible(False)
        self._main_splitter.setHandleWidth(10)
        root.addWidget(self._main_splitter, 1)

        top_panel = QWidget(self)
        top_layout = QVBoxLayout(top_panel)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(0)

        self._scroll_area = QScrollArea(self)
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll_container = QWidget(self)
        scroll_layout = QVBoxLayout(scroll_container)
        scroll_layout.setContentsMargins(0, 0, 0, 0)
        scroll_layout.setSpacing(10)

        self.listener_group = QGroupBox("Listener", self)
        listener_form = QFormLayout(self.listener_group)
        self.bind_host_widget = BindHostSelector(
            self,
            initial_value="127.0.0.1",
            interface_info_provider=collect_interface_info,
        )
        self.bind_host_input = self.bind_host_widget.value_input
        self.bind_host_preset_input = self.bind_host_widget.preset_input
        self.bind_host_custom_input = self.bind_host_widget.custom_input
        self.port_input = QSpinBox()
        self.port_input.setRange(1, 65535)
        self.port_input.setValue(8000)
        self.mode_input = QComboBox()
        self.mode_input.addItem("Static Files", "static")
        self.mode_input.addItem("Reverse Proxy", "reverse_proxy")
        listener_form.addRow("Bind Address", self.bind_host_widget)
        listener_form.addRow("Port", self.port_input)
        listener_form.addRow("Mode", self.mode_input)
        scroll_layout.addWidget(self.listener_group)

        self.static_group = QGroupBox("Static Files", self)
        static_form = QFormLayout(self.static_group)
        self.document_root_widget, self.document_root_input = self._build_path_input(directory=True)
        self.index_page_widget, self.index_page_input = self._build_path_input(directory=False)
        self.allow_directory_listing = QCheckBox("Enable directory listing fallback")
        static_form.addRow("Document Root", self.document_root_widget)
        static_form.addRow("Index Page", self.index_page_widget)
        static_form.addRow("Directory Listing", self.allow_directory_listing)
        scroll_layout.addWidget(self.static_group)

        self.proxy_group = QGroupBox("Reverse Proxy", self)
        proxy_form = QFormLayout(self.proxy_group)
        self.upstream_url_input = QLineEdit("")
        self.upstream_url_input.setPlaceholderText("https://127.0.0.1:3000")
        self.proxy_path_prefix_input = QLineEdit("/")
        self.proxy_strip_prefix = QCheckBox("Strip the matched path prefix before proxying")
        self.proxy_preserve_host = QCheckBox("Preserve the original Host header")
        self.proxy_preserve_host.setChecked(True)
        self.proxy_send_x_forwarded = QCheckBox("Send X-Forwarded-* headers")
        self.proxy_send_x_forwarded.setChecked(True)
        self.proxy_verify_upstream_tls = QCheckBox("Verify upstream TLS certificate")
        self.proxy_verify_upstream_tls.setChecked(True)
        self.proxy_enable_websocket = QCheckBox("Enable WebSocket proxying")
        self.proxy_enable_websocket.setChecked(True)
        self.proxy_connect_timeout = QSpinBox()
        self.proxy_connect_timeout.setRange(1, 3600)
        self.proxy_connect_timeout.setValue(30)
        self.proxy_read_timeout = QSpinBox()
        self.proxy_read_timeout.setRange(1, 3600)
        self.proxy_read_timeout.setValue(60)
        self.proxy_extra_headers = QPlainTextEdit(self)
        self.proxy_extra_headers.setPlaceholderText("Header-Name: value")
        self.proxy_extra_headers.setFixedHeight(86)
        proxy_form.addRow("Upstream URL", self.upstream_url_input)
        proxy_form.addRow("Path Prefix", self.proxy_path_prefix_input)
        proxy_form.addRow("Path Handling", self.proxy_strip_prefix)
        proxy_form.addRow("Host Header", self.proxy_preserve_host)
        proxy_form.addRow("Forwarded Headers", self.proxy_send_x_forwarded)
        proxy_form.addRow("Upstream TLS", self.proxy_verify_upstream_tls)
        proxy_form.addRow("WebSocket Support", self.proxy_enable_websocket)
        proxy_form.addRow("Connect Timeout (s)", self.proxy_connect_timeout)
        proxy_form.addRow("Read Timeout (s)", self.proxy_read_timeout)
        proxy_form.addRow("Extra Request Headers", self.proxy_extra_headers)
        scroll_layout.addWidget(self.proxy_group)

        self.tls_group = QGroupBox("TLS", self)
        tls_layout = QVBoxLayout(self.tls_group)
        tls_layout.setContentsMargins(10, 10, 10, 10)
        tls_layout.setSpacing(8)
        tls_mode_form = QFormLayout()
        self.tls_mode_input = QComboBox()
        self.tls_mode_input.addItem("HTTP Only", "none")
        self.tls_mode_input.addItem("Manual Certificate", "manual")
        self.tls_mode_input.addItem("Self-Signed Certificate", "self_signed")
        self.tls_mode_input.addItem("Certbot (HTTP-01)", "certbot")
        tls_mode_form.addRow("TLS Mode", self.tls_mode_input)
        tls_layout.addLayout(tls_mode_form)

        self.manual_tls_widget = QWidget(self)
        manual_tls_form = QFormLayout(self.manual_tls_widget)
        self.cert_widget, self.cert_input = self._build_path_input(directory=False)
        self.key_widget, self.key_input = self._build_path_input(directory=False)
        self.chain_widget, self.chain_input = self._build_path_input(directory=False)
        manual_tls_form.addRow("Certificate File", self.cert_widget)
        manual_tls_form.addRow("Key File", self.key_widget)
        manual_tls_form.addRow("Intermediate / Chain File", self.chain_widget)
        tls_layout.addWidget(self.manual_tls_widget)

        self.certbot_widget = QWidget(self)
        certbot_form = QFormLayout(self.certbot_widget)
        self.certbot_executable_widget, self.certbot_executable_input = self._build_path_input(directory=False)
        self.certbot_executable_input.setText("certbot")
        self.certbot_primary_domain_input = QLineEdit("")
        self.certbot_additional_domains_input = QLineEdit("")
        self.certbot_additional_domains_input.setPlaceholderText("example.com, www.example.com")
        self.certbot_email_input = QLineEdit("")
        self.certbot_challenge_port = QSpinBox()
        self.certbot_challenge_port.setRange(1, 65535)
        self.certbot_challenge_port.setValue(80)
        self.certbot_staging = QCheckBox("Use Let's Encrypt staging")
        certbot_form.addRow("Certbot Executable", self.certbot_executable_widget)
        certbot_form.addRow("Primary Domain", self.certbot_primary_domain_input)
        certbot_form.addRow("Additional Domains", self.certbot_additional_domains_input)
        certbot_form.addRow("Contact Email", self.certbot_email_input)
        certbot_form.addRow("HTTP-01 Port", self.certbot_challenge_port)
        certbot_form.addRow("Staging", self.certbot_staging)
        tls_layout.addWidget(self.certbot_widget)
        scroll_layout.addWidget(self.tls_group)

        self.logging_group = QGroupBox("Logging", self)
        logging_layout = QHBoxLayout(self.logging_group)
        logging_layout.setContentsMargins(10, 10, 10, 10)
        logging_layout.setSpacing(6)
        self.open_current_log_btn = QPushButton("Open Current Log")
        self.open_log_folder_btn = QPushButton("Open Log Folder")
        logging_layout.addWidget(self.open_current_log_btn, 0)
        logging_layout.addWidget(self.open_log_folder_btn, 0)
        logging_layout.addStretch(1)
        scroll_layout.addWidget(self.logging_group)
        scroll_layout.addStretch(1)

        self._scroll_area.setWidget(scroll_container)
        top_layout.addWidget(self._scroll_area, 1)
        self._main_splitter.addWidget(top_panel)

        bottom_panel = QWidget(self)
        bottom_layout = QVBoxLayout(bottom_panel)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(10)

        button_row = QHBoxLayout()
        self.start_btn = QPushButton("Start")
        self.stop_btn = QPushButton("Stop")
        self.open_btn = QPushButton("Open in Browser")
        self.clear_log_btn = QPushButton("Clear Log View")
        self.stop_btn.setEnabled(False)
        self.open_btn.setEnabled(False)
        self.open_current_log_btn.setEnabled(False)
        button_row.addWidget(self.start_btn)
        button_row.addWidget(self.stop_btn)
        button_row.addWidget(self.open_btn)
        button_row.addWidget(self.clear_log_btn)
        button_row.addStretch(1)
        self.runtime_badge = QLabel(self)
        self.runtime_badge.setAlignment(Qt.AlignCenter)
        self.runtime_badge.setMinimumWidth(104)
        button_row.addWidget(self.runtime_badge)
        bottom_layout.addLayout(button_row)
        self._set_runtime_badge("stopped")

        self.url_label = QLabel("URL: (not running)")
        self.url_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        bottom_layout.addWidget(self.url_label)

        self.log_output = QPlainTextEdit(self)
        self.log_output.setReadOnly(True)
        bottom_layout.addWidget(self.log_output, 1)

        self.status_label = QLabel("Ready.")
        self.status_label.setWordWrap(True)
        bottom_layout.addWidget(self.status_label)
        self._main_splitter.addWidget(bottom_panel)
        self._main_splitter.setStretchFactor(0, 1)
        self._main_splitter.setStretchFactor(1, 1)

        self.start_btn.clicked.connect(self._start_server)
        self.stop_btn.clicked.connect(self._stop_server)
        self.open_btn.clicked.connect(self._open_server_url)
        self.open_current_log_btn.clicked.connect(self._open_current_log)
        self.open_log_folder_btn.clicked.connect(self._open_log_folder)
        self.clear_log_btn.clicked.connect(self.log_output.clear)
        self.mode_input.currentIndexChanged.connect(self._on_mode_or_tls_changed)
        self.tls_mode_input.currentIndexChanged.connect(self._on_mode_or_tls_changed)
        self.port_input.valueChanged.connect(self._on_port_value_changed)
        self.profile_input.currentIndexChanged.connect(self._on_profile_selection_changed)
        self.load_profile_btn.clicked.connect(self._load_selected_profile)
        self.save_profile_btn.clicked.connect(self._save_current_profile_as)
        self.update_profile_btn.clicked.connect(self._update_selected_profile)
        self.rename_profile_btn.clicked.connect(self._rename_selected_profile)
        self.delete_profile_btn.clicked.connect(self._delete_selected_profile)
        self._main_splitter.splitterMoved.connect(self._persist_splitter_state)

        self.bind_host_widget.reload_choices()
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(800)
        self._poll_timer.timeout.connect(self._poll_runtime_state)
        self._poll_timer.start()
        self._refresh_profile_combo()
        self._sync_form_state(force_port=True)
        self._restore_last_selected_profile()
        QTimer.singleShot(0, self._restore_or_initialize_splitter_state)
        self._poll_runtime_state()

    def apply_runtime_settings(self, settings: AppSettings) -> None:
        apply_terminal_output_font(self.log_output, settings)

    def _build_path_input(self, *, directory: bool) -> tuple[QWidget, QLineEdit]:
        container = QWidget(self)
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        field = QLineEdit()
        browse = QPushButton("Browse...")
        row.addWidget(field, 1)
        row.addWidget(browse, 0)
        if directory:
            browse.clicked.connect(lambda: self._browse_directory(field))
        else:
            browse.clicked.connect(lambda: self._browse_file(field))
        return container, field

    def _set_bind_host_value(self, value: str) -> None:
        self.bind_host_widget.set_value(value)

    def _browse_directory(self, target: QLineEdit) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Select Folder", target.text().strip() or "")
        if directory:
            target.setText(directory)

    def _browse_file(self, target: QLineEdit) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select File", target.text().strip() or "")
        if path:
            target.setText(path)

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
                    "config": WebServerConfig.from_dict(config).to_dict(),
                }
            )
        return normalized

    def _profile_rows(self) -> list[dict[str, object]]:
        return [
            {
                "id": str(profile.get("id", "")).strip(),
                "name": str(profile.get("name", "")).strip(),
                "config": dict(profile.get("config", {})),
            }
            for profile in self._profiles
            if isinstance(profile, dict)
        ]

    def _refresh_profile_combo(self) -> None:
        self.profile_input.blockSignals(True)
        self.profile_input.clear()
        self.profile_input.addItem("(No Saved Profile)", "")
        current_index = 0
        for index, profile in enumerate(self._profiles, start=1):
            profile_id = str(profile.get("id", "")).strip()
            name = str(profile.get("name", "")).strip() or "Web Server Profile"
            self.profile_input.addItem(name, profile_id)
            if profile_id == self._selected_profile_id:
                current_index = index
        self.profile_input.setCurrentIndex(current_index)
        self.profile_input.blockSignals(False)
        self._update_profile_buttons()

    def _restore_last_selected_profile(self) -> None:
        profile = self._profile_by_id(self._selected_profile_id)
        if profile is None:
            self._selected_profile_id = ""
            self._refresh_profile_combo()
            return
        self._apply_profile_config(WebServerConfig.from_dict(profile.get("config", {})))
        self.status_label.setText(f"Loaded web server profile {profile.get('name', 'Profile')}.")

    def _selected_profile_id_from_combo(self) -> str:
        value = self.profile_input.currentData()
        if isinstance(value, str):
            return value.strip()
        return ""

    def _selected_profile_name(self) -> str:
        profile = self._profile_by_id(self._selected_profile_id)
        if profile is None:
            return "Web Server Profile"
        return str(profile.get("name", "")).strip() or "Web Server Profile"

    def _profile_by_id(self, profile_id: str) -> dict[str, object] | None:
        target = profile_id.strip()
        if not target:
            return None
        for profile in self._profiles:
            if str(profile.get("id", "")).strip() == target:
                return profile
        return None

    def _persist_profiles(self) -> None:
        if self._on_profiles_changed is None:
            return
        self._on_profiles_changed(self._profile_rows(), self._selected_profile_id)

    def _on_profile_selection_changed(self, *_args: object) -> None:
        self._selected_profile_id = self._selected_profile_id_from_combo()
        self._update_profile_buttons()
        self._persist_profiles()

    def _update_profile_buttons(self) -> None:
        has_selection = bool(self._selected_profile_id)
        self.load_profile_btn.setEnabled(has_selection)
        self.update_profile_btn.setEnabled(has_selection)
        self.rename_profile_btn.setEnabled(has_selection)
        self.delete_profile_btn.setEnabled(has_selection)

    def _apply_profile_config(self, config: WebServerConfig) -> None:
        for widget in (self.mode_input, self.tls_mode_input, self.port_input):
            widget.blockSignals(True)
        self._set_bind_host_value(config.bind_host)
        self.port_input.setValue(config.port)
        self._set_mode(config.mode)
        self.document_root_input.setText(config.document_root)
        self.index_page_input.setText(config.index_page)
        self.allow_directory_listing.setChecked(config.allow_directory_listing)
        self.upstream_url_input.setText(config.upstream_url)
        self.proxy_path_prefix_input.setText(config.proxy_path_prefix)
        self.proxy_strip_prefix.setChecked(config.proxy_strip_prefix)
        self.proxy_preserve_host.setChecked(config.proxy_preserve_host)
        self.proxy_send_x_forwarded.setChecked(config.proxy_send_x_forwarded)
        self.proxy_verify_upstream_tls.setChecked(config.proxy_verify_upstream_tls)
        self.proxy_enable_websocket.setChecked(config.proxy_enable_websocket)
        self.proxy_connect_timeout.setValue(config.proxy_connect_timeout)
        self.proxy_read_timeout.setValue(config.proxy_read_timeout)
        self.proxy_extra_headers.setPlainText(config.proxy_extra_headers)
        self._set_tls_mode(config.tls_mode)
        self.cert_input.setText(config.cert_file)
        self.key_input.setText(config.key_file)
        self.chain_input.setText(config.chain_file)
        self.certbot_executable_input.setText(config.certbot_executable)
        self.certbot_primary_domain_input.setText(config.certbot_primary_domain)
        self.certbot_additional_domains_input.setText(config.certbot_additional_domains)
        self.certbot_email_input.setText(config.certbot_email)
        self.certbot_challenge_port.setValue(config.certbot_challenge_port)
        self.certbot_staging.setChecked(config.certbot_staging)
        for widget in (self.mode_input, self.tls_mode_input, self.port_input):
            widget.blockSignals(False)
        self._last_recommended_port = self._recommended_port()
        self._port_follows_recommendation = self.port_input.value() == self._last_recommended_port
        self._sync_form_state(force_port=False)

    def _set_mode(self, mode: str) -> None:
        target = mode.strip().lower() or "static"
        for index in range(self.mode_input.count()):
            value = self.mode_input.itemData(index)
            if isinstance(value, str) and value.strip().lower() == target:
                self.mode_input.setCurrentIndex(index)
                return
        self.mode_input.setCurrentIndex(0)

    def _set_tls_mode(self, tls_mode: str) -> None:
        target = tls_mode.strip().lower() or "none"
        for index in range(self.tls_mode_input.count()):
            value = self.tls_mode_input.itemData(index)
            if isinstance(value, str) and value.strip().lower() == target:
                self.tls_mode_input.setCurrentIndex(index)
                return
        self.tls_mode_input.setCurrentIndex(0)

    def _current_profile_config(self) -> WebServerConfig:
        return WebServerConfig.from_dict(self._current_config().to_dict())

    def _unique_profile_name(self, base_name: str, *, exclude_profile_id: str = "") -> str:
        base = base_name.strip() or "Web Server Profile"
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

    def _next_profile_name(self) -> str:
        return self._unique_profile_name("Web Server Profile")

    def _prompt_profile_name(
        self,
        *,
        title: str,
        prompt: str,
        default_name: str,
        exclude_profile_id: str = "",
    ) -> str | None:
        entered, ok = QInputDialog.getText(self, title, prompt, QLineEdit.Normal, default_name)
        if not ok:
            return None
        trimmed = entered.strip()
        if not trimmed:
            return None
        return self._unique_profile_name(trimmed, exclude_profile_id=exclude_profile_id)

    def _load_selected_profile(self) -> None:
        profile = self._profile_by_id(self._selected_profile_id)
        if profile is None:
            return
        config = WebServerConfig.from_dict(profile.get("config", {}))
        name = str(profile.get("name", "")).strip() or "Profile"
        if self._should_warn_before_loading_profile(config):
            answer = QMessageBox.question(
                self,
                "Load Web Server Profile",
                (
                    f"Loading web server profile {name} will stop the currently running web server.\n\n"
                    "Continue?"
                ),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if answer != QMessageBox.Yes:
                return
            self._create_fresh_instance_dir()
            self._apply_profile_config(config)
            self.status_label.setText(f"Stopped the current web server and loaded web server profile {name}.")
            return
        self._apply_profile_config(config)
        self.status_label.setText(f"Loaded web server profile {name}.")

    def _save_current_profile_as(self) -> None:
        name = self._prompt_profile_name(
            title="Save Web Server Profile",
            prompt="Profile name:",
            default_name=self._next_profile_name(),
        )
        if not name:
            return
        profile_id = str(uuid4())
        self._profiles.append(
            {
                "id": profile_id,
                "name": name,
                "config": self._current_profile_config().to_dict(),
            }
        )
        self._selected_profile_id = profile_id
        self._refresh_profile_combo()
        self._persist_profiles()
        self.status_label.setText(f"Saved web server profile {name}.")

    def _update_selected_profile(self) -> None:
        profile = self._profile_by_id(self._selected_profile_id)
        if profile is None:
            return
        profile["config"] = self._current_profile_config().to_dict()
        self._persist_profiles()
        self.status_label.setText(f"Updated web server profile {profile.get('name', 'Profile')}.")

    def _rename_selected_profile(self) -> None:
        profile = self._profile_by_id(self._selected_profile_id)
        if profile is None:
            return
        renamed = self._prompt_profile_name(
            title="Rename Web Server Profile",
            prompt="Profile name:",
            default_name=str(profile.get("name", "")).strip() or "Web Server Profile",
            exclude_profile_id=self._selected_profile_id,
        )
        if not renamed:
            return
        profile["name"] = renamed
        self._refresh_profile_combo()
        self._persist_profiles()
        self.status_label.setText(f"Renamed web server profile to {renamed}.")

    def _delete_selected_profile(self) -> None:
        profile = self._profile_by_id(self._selected_profile_id)
        if profile is None:
            return
        name = str(profile.get("name", "")).strip() or "Web Server Profile"
        answer = QMessageBox.question(
            self,
            "Delete Web Server Profile",
            f"Delete web server profile {name}?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self._profiles = [
            entry
            for entry in self._profiles
            if str(entry.get("id", "")).strip() != self._selected_profile_id
        ]
        self._selected_profile_id = ""
        self._refresh_profile_combo()
        self._persist_profiles()
        self.status_label.setText(f"Deleted web server profile {name}.")

    def _current_config(self) -> WebServerConfig:
        return WebServerConfig(
            bind_host=self.bind_host_input.text(),
            port=self.port_input.value(),
            mode=str(self.mode_input.currentData() or "static"),
            document_root=self.document_root_input.text(),
            index_page=self.index_page_input.text(),
            tls_mode=str(self.tls_mode_input.currentData() or "none"),
            cert_file=self.cert_input.text(),
            key_file=self.key_input.text(),
            chain_file=self.chain_input.text(),
            allow_directory_listing=self.allow_directory_listing.isChecked(),
            upstream_url=self.upstream_url_input.text(),
            proxy_path_prefix=self.proxy_path_prefix_input.text(),
            proxy_strip_prefix=self.proxy_strip_prefix.isChecked(),
            proxy_preserve_host=self.proxy_preserve_host.isChecked(),
            proxy_send_x_forwarded=self.proxy_send_x_forwarded.isChecked(),
            proxy_verify_upstream_tls=self.proxy_verify_upstream_tls.isChecked(),
            proxy_enable_websocket=self.proxy_enable_websocket.isChecked(),
            proxy_connect_timeout=self.proxy_connect_timeout.value(),
            proxy_read_timeout=self.proxy_read_timeout.value(),
            proxy_extra_headers=self.proxy_extra_headers.toPlainText(),
            certbot_executable=self.certbot_executable_input.text(),
            certbot_primary_domain=self.certbot_primary_domain_input.text(),
            certbot_additional_domains=self.certbot_additional_domains_input.text(),
            certbot_email=self.certbot_email_input.text(),
            certbot_challenge_port=self.certbot_challenge_port.value(),
            certbot_staging=self.certbot_staging.isChecked(),
        )

    def _should_warn_before_loading_profile(self, config: WebServerConfig) -> bool:
        if config == self._current_profile_config():
            return False
        return self._runtime_state().can_request_stop

    def _recommended_port(self) -> int:
        mode = str(self.mode_input.currentData() or "static")
        tls_mode = str(self.tls_mode_input.currentData() or "none")
        if mode == "reverse_proxy":
            return 443 if tls_mode != "none" else 80
        return 8000

    def _on_port_value_changed(self, value: int) -> None:
        if self._updating_recommended_port:
            return
        self._port_follows_recommendation = value == self._recommended_port()

    def _on_mode_or_tls_changed(self, *_args: object) -> None:
        self._sync_form_state(force_port=False)

    def _sync_form_state(self, *, force_port: bool) -> None:
        mode = str(self.mode_input.currentData() or "static")
        tls_mode = str(self.tls_mode_input.currentData() or "none")
        self.static_group.setVisible(mode == "static")
        self.proxy_group.setVisible(mode == "reverse_proxy")
        self.manual_tls_widget.setVisible(tls_mode == "manual")
        self.certbot_widget.setVisible(tls_mode == "certbot")

        recommended_port = self._recommended_port()
        should_update_port = force_port or self._port_follows_recommendation or self.port_input.value() == self._last_recommended_port
        self._last_recommended_port = recommended_port
        if should_update_port:
            self._updating_recommended_port = True
            self.port_input.setValue(recommended_port)
            self._updating_recommended_port = False
            self._port_follows_recommendation = True

    def _create_fresh_instance_dir(self) -> None:
        previous_instance_dir = getattr(self, "_instance_dir", None)
        previous_launch_requested = self._launch_requested
        self._instance_dir = create_web_server_instance_dir()
        self._paths = web_server_instance_paths(self._instance_dir)
        self._last_log_size = 0
        self._current_log_path = ""
        self._launch_requested = False
        self.log_output.clear()
        if previous_instance_dir:
            previous_paths = web_server_instance_paths(previous_instance_dir)
            if previous_launch_requested or previous_paths.config_path.exists():
                try:
                    request_web_server_stop(previous_instance_dir)
                except Exception:
                    pass
            else:
                self._remove_instance_dir(previous_instance_dir)

    def _remove_instance_dir(self, instance_dir: str | Path) -> None:
        try:
            if is_web_server_running(instance_dir):
                return
        except Exception:
            return
        shutil.rmtree(Path(instance_dir), ignore_errors=True)

    def _restore_or_initialize_splitter_state(self) -> None:
        if self._splitter_state_b64:
            try:
                state = QByteArray.fromBase64(self._splitter_state_b64.encode("ascii"))
            except Exception:
                state = QByteArray()
            if not state.isEmpty() and self._main_splitter.restoreState(state):
                return
        self._apply_default_splitter_sizes()

    def _apply_default_splitter_sizes(self) -> None:
        content_height = 0
        content_widget = self._scroll_area.widget()
        if content_widget is not None:
            try:
                content_height = max(0, content_widget.sizeHint().height())
            except Exception:
                content_height = 0
        available = max(420, self.height() - 24)
        bottom_target = min(260, max(180, available // 4))
        top_target = max(220, available - bottom_target)
        if content_height > 0:
            top_target = min(max(content_height + 24, top_target), max(220, available - 140))
            bottom_target = max(140, available - top_target)
        self._main_splitter.setSizes([top_target, bottom_target])

    def _persist_splitter_state(self, *_args: object) -> None:
        if self._on_splitter_state_changed is None:
            return
        try:
            encoded = bytes(self._main_splitter.saveState().toBase64()).decode("ascii")
        except Exception:
            return
        self._splitter_state_b64 = encoded
        self._on_splitter_state_changed(encoded)

    def _current_log_label(self, config: WebServerConfig) -> str:
        if self._selected_profile_id:
            return self._selected_profile_name()
        if config.mode == "reverse_proxy":
            return "Ad Hoc Reverse Proxy"
        return "Ad Hoc Static Server"

    def _prune_logs_if_needed(self) -> None:
        if not self._log_cleanup_enabled:
            return
        try:
            removed = prune_web_server_log_files(self._log_retention_days)
        except Exception:
            return
        if removed > 0:
            self.status_label.setText(f"Web server log cleanup removed {removed} old file(s).")

    def _start_server(self) -> None:
        try:
            config = validate_web_server_config(self._current_config())
        except ValueError as exc:
            QMessageBox.warning(self, "Web Server", str(exc))
            return

        self._prune_logs_if_needed()
        self._create_fresh_instance_dir()
        log_path = str(create_web_server_archived_log_path(self._current_log_label(config)))
        target_description = config.document_root if config.mode == "static" else config.upstream_url
        try:
            write_web_server_config(
                self._instance_dir,
                config,
                log_path=log_path,
                log_label=self._current_log_label(config),
            )
            write_web_server_status(
                self._instance_dir,
                WebServerStatus(
                    state="starting",
                    message="Launching web server helper...",
                    bind_host=config.bind_host,
                    port=config.port,
                    protocol=config.protocol,
                    document_root=target_description,
                    log_path=log_path,
                ),
            )
            if needs_gui_elevation(config):
                launch_web_server_helper_elevated(self._instance_dir)
            else:
                launch_web_server_helper(self._instance_dir)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Web Server", str(exc))
            return

        self._launch_requested = True
        self.status_label.setText("Starting web server...")
        self._poll_runtime_state()

    def _stop_server(self) -> None:
        request_web_server_stop(self._instance_dir)
        self.status_label.setText("Stop requested. Waiting for the web server to exit.")
        self._set_runtime_badge("stopping")
        self._poll_runtime_state()

    @staticmethod
    def _is_blank_status(status: WebServerStatus) -> bool:
        return (
            status.state == "stopped"
            and status.pid is None
            and not status.url
            and not status.message
            and not status.bind_host
            and status.port == 0
            and status.protocol == "http"
            and not status.document_root
            and not status.started_at
            and not status.log_path
        )

    def _listener_looks_active(self) -> bool:
        try:
            config = read_web_server_config(self._instance_dir)
        except Exception:
            return False

        host = config.bind_host.strip() or "127.0.0.1"
        if host == "0.0.0.0":
            host = "127.0.0.1"
        elif host == "::":
            host = "::1"

        try:
            with socket.create_connection((host, int(config.port)), timeout=0.25):
                return True
        except OSError:
            return False

    def _runtime_state(self) -> _WebServerRuntimeState:
        status = read_web_server_status(self._instance_dir)
        status_file_exists = self._paths.status_path.exists()
        status_unavailable = status_file_exists and self._is_blank_status(status)
        listener_active = status_unavailable and self._listener_looks_active()
        starting_without_status = (
            status_unavailable
            and self._launch_requested
            and not listener_active
            and not self._paths.stop_path.exists()
        )
        running = (status.state == "running" and is_web_server_running(self._instance_dir)) or listener_active
        can_request_stop = status.state in {"starting", "running"} or starting_without_status or listener_active
        stop_requested = self._launch_requested and self._paths.stop_path.exists()
        return _WebServerRuntimeState(
            status=status,
            status_unavailable=status_unavailable,
            listener_active=listener_active,
            starting_without_status=starting_without_status,
            running=running,
            can_request_stop=can_request_stop,
            stop_requested=stop_requested,
        )

    def _open_server_url(self) -> None:
        status = read_web_server_status(self._instance_dir)
        if not status.url:
            return
        QDesktopServices.openUrl(QUrl(status.url))

    def _open_current_log(self) -> None:
        status = read_web_server_status(self._instance_dir)
        target = status.log_path.strip() or self._current_log_path
        if not target:
            return
        open_local_path(Path(target).expanduser())

    def _open_log_folder(self) -> None:
        status = read_web_server_status(self._instance_dir)
        target = status.log_path.strip() or self._current_log_path
        if target:
            folder = Path(target).expanduser().parent
        else:
            folder = web_server_logs_root()
        open_local_path(folder)

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

    def _runtime_badge_state(self, runtime_state: _WebServerRuntimeState) -> str:
        status = runtime_state.status
        if status.state == "error":
            return "error"
        if runtime_state.stop_requested:
            return "stopping"
        if runtime_state.running:
            return "running"
        if runtime_state.starting_without_status or status.state == "starting":
            return "starting"
        return "stopped"

    def _poll_runtime_state(self) -> None:
        runtime_state = self._runtime_state()
        status = runtime_state.status
        if runtime_state.can_request_stop:
            self._launch_requested = True
        elif status.state in {"stopped", "error"} or (
            runtime_state.status_unavailable and not self._paths.stop_path.exists() and not runtime_state.listener_active
        ):
            self._launch_requested = False

        self.start_btn.setEnabled(
            not runtime_state.running
            and not runtime_state.starting_without_status
            and status.state != "starting"
            and not runtime_state.stop_requested
        )
        self.stop_btn.setEnabled(runtime_state.can_request_stop)
        self.open_btn.setEnabled(runtime_state.running and bool(status.url))
        has_log_path = bool(status.log_path or self._current_log_path)
        self.open_current_log_btn.setEnabled(has_log_path)
        self.open_log_folder_btn.setEnabled(True)
        self.url_label.setText(f"URL: {status.url or '(not running)'}")
        self._set_runtime_badge(self._runtime_badge_state(runtime_state))
        message = status.message or status.state.capitalize()
        if status.state == "error":
            message = status.message or status.state.capitalize()
        elif runtime_state.stop_requested:
            message = "Stop requested. Waiting for the web server to exit."
        elif runtime_state.status_unavailable:
            if runtime_state.listener_active:
                message = "Web server status is unavailable, but the listener is still responding."
            elif runtime_state.starting_without_status:
                message = "Web server status is unavailable while the helper is starting."
            elif self._paths.stop_path.exists():
                message = "Stop requested. Waiting for the web server to exit."
            else:
                message = "Web server status is unavailable."
        elif status.state == "running" and not runtime_state.running:
            message = "Web server process is no longer running."
        self.status_label.setText(message)
        self._refresh_log_view(status.log_path)

    def _refresh_log_view(self, log_path: str) -> None:
        target = log_path.strip() or self._current_log_path
        if target != self._current_log_path:
            self._current_log_path = target
            self._last_log_size = 0
            self.log_output.clear()
        if not self._current_log_path:
            return
        log_path_obj = Path(self._current_log_path).expanduser()
        if not log_path_obj.exists():
            return
        try:
            size = log_path_obj.stat().st_size
        except OSError:
            return
        if size < self._last_log_size:
            self._last_log_size = 0
            self.log_output.clear()
        if size == self._last_log_size:
            return
        with log_path_obj.open("r", encoding="utf-8") as handle:
            handle.seek(self._last_log_size)
            payload = handle.read()
        self._last_log_size = size
        if payload:
            self.log_output.moveCursor(QTextCursor.End)
            self.log_output.insertPlainText(payload)
            self.log_output.moveCursor(QTextCursor.End)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self._persist_splitter_state()
        if self._launch_requested or self._paths.config_path.exists():
            try:
                request_web_server_stop(self._instance_dir)
            except Exception:
                pass
        else:
            self._remove_instance_dir(self._instance_dir)
        super().closeEvent(event)
