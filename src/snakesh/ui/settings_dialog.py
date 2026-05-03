from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor, QFontDatabase, QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from snakesh.core.theme_presets import (
    CUSTOM_THEME_ID,
    THEME_COLOR_FIELDS,
    THEME_PRESETS,
    infer_theme_id_from_colors,
    normalize_theme_id,
    theme_colors_for,
    theme_matches_colors,
)
from snakesh.core.paths import data_dir
from snakesh.services.master_password_service import MasterPasswordService
from snakesh.services.settings_service import AppSettings
from snakesh.ui.color_picker import pick_color
from snakesh.ui.desktop_open import open_local_path

_SCROLLBACK_ESTIMATED_FIXED_BYTES = 2 * 1024 * 1024
_SCROLLBACK_ESTIMATED_BYTES_PER_LINE = 5 * 1024


def _normalize_shortcut_text(raw: str) -> str:
    text = raw.strip()
    if not text:
        return ""
    sequence = QKeySequence.fromString(text, QKeySequence.PortableText)
    if sequence.isEmpty():
        sequence = QKeySequence.fromString(text, QKeySequence.NativeText)
    if sequence.isEmpty():
        return ""
    try:
        first = sequence[0]
    except Exception:
        return sequence.toString(QKeySequence.PortableText)
    return QKeySequence(first).toString(QKeySequence.PortableText)


def _display_shortcut_text(raw: str) -> str:
    normalized = _normalize_shortcut_text(raw)
    if not normalized:
        return "Disabled"
    sequence = QKeySequence.fromString(normalized, QKeySequence.PortableText)
    display = sequence.toString(QKeySequence.NativeText) or normalized
    return display.replace("Return", "Enter")


class ShortcutCaptureDialog(QDialog):
    def __init__(
        self,
        *,
        title: str,
        initial_shortcut: str,
        default_shortcut: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.resize(460, 200)

        self._default_shortcut = _normalize_shortcut_text(default_shortcut)

        root = QVBoxLayout(self)
        instructions = QLabel("Press the shortcut you want to use. The capture field records the next key combination.")
        instructions.setWordWrap(True)
        root.addWidget(instructions)

        self.sequence_edit = QKeySequenceEdit(self)
        if hasattr(self.sequence_edit, "setMaximumSequenceLength"):
            try:
                self.sequence_edit.setMaximumSequenceLength(1)
            except Exception:
                pass
        normalized_initial = _normalize_shortcut_text(initial_shortcut)
        if normalized_initial:
            self.sequence_edit.setKeySequence(QKeySequence.fromString(normalized_initial, QKeySequence.PortableText))
        root.addWidget(self.sequence_edit)

        self.preview = QLabel("")
        self.preview.setWordWrap(True)
        root.addWidget(self.preview)

        actions = QHBoxLayout()
        default_btn = QPushButton("Default")
        actions.addWidget(default_btn)
        actions.addStretch(1)
        root.addLayout(actions)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        default_btn.clicked.connect(self._restore_default_shortcut)
        self.sequence_edit.keySequenceChanged.connect(self._update_preview)
        self._update_preview(self.sequence_edit.keySequence())
        QTimer.singleShot(0, self.sequence_edit.setFocus)

    def _restore_default_shortcut(self) -> None:
        if not self._default_shortcut:
            self.sequence_edit.clear()
            return
        self.sequence_edit.setKeySequence(QKeySequence.fromString(self._default_shortcut, QKeySequence.PortableText))

    def _update_preview(self, sequence: QKeySequence) -> None:
        try:
            first = sequence[0]
        except Exception:
            first = None
        rendered = _display_shortcut_text(QKeySequence(first).toString(QKeySequence.PortableText)) if first else "Disabled"
        self.preview.setText(f"Current selection: {rendered}")

    def shortcut_text(self) -> str:
        sequence = self.sequence_edit.keySequence()
        try:
            first = sequence[0]
        except Exception:
            first = None
        if not first:
            return ""
        return QKeySequence(first).toString(QKeySequence.PortableText)


class ShortcutPreferenceEditor(QWidget):
    def __init__(
        self,
        *,
        initial_shortcut: str,
        default_shortcut: str,
        dialog_title: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._default_shortcut = _normalize_shortcut_text(default_shortcut)
        self._dialog_title = dialog_title
        self._shortcut_text = ""

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.display = QLineEdit(self)
        self.display.setReadOnly(True)
        self.display.setPlaceholderText("Disabled")
        self.display.setFocusPolicy(Qt.NoFocus)

        self.set_btn = QPushButton("Set Shortcut...")
        self.default_btn = QPushButton("Default")

        self.set_btn.clicked.connect(self._open_capture_dialog)
        self.default_btn.clicked.connect(lambda: self.set_shortcut_text(self._default_shortcut))

        layout.addWidget(self.display, 1)
        layout.addWidget(self.set_btn, 0)
        layout.addWidget(self.default_btn, 0)

        self.set_shortcut_text(initial_shortcut or self._default_shortcut)

    def _open_capture_dialog(self) -> None:
        dialog = ShortcutCaptureDialog(
            title=self._dialog_title,
            initial_shortcut=self._shortcut_text,
            default_shortcut=self._default_shortcut,
            parent=self,
        )
        if dialog.exec():
            self.set_shortcut_text(dialog.shortcut_text())

    def shortcut_text(self) -> str:
        return self._shortcut_text

    def set_shortcut_text(self, value: str) -> None:
        self._shortcut_text = _normalize_shortcut_text(value)
        self.display.setText(_display_shortcut_text(self._shortcut_text))


class SettingsDialog(QDialog):
    def __init__(
        self,
        settings: AppSettings,
        parent=None,
        on_export_requested: Callable[[], None] | None = None,
        on_import_requested: Callable[[], AppSettings | None] | None = None,
        on_third_party_io_requested: Callable[[], None] | None = None,
        on_test_secrets_requested: Callable[[AppSettings, dict[str, str | None]], tuple[bool, str]] | None = None,
        on_setup_secrets_requested: Callable[[AppSettings, dict[str, str | None]], tuple[bool, str]] | None = None,
        on_desktop_install_requested: Callable[[], tuple[bool, str]] | None = None,
        on_desktop_uninstall_requested: Callable[[], tuple[bool, str]] | None = None,
        on_manage_tool_launchers_requested: Callable[[], None] | None = None,
        backend_auth_state: dict[str, bool] | None = None,
        on_preview_requested: Callable[[AppSettings], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(760, 860)
        self.setMinimumSize(620, 540)
        self._defaults = AppSettings.defaults()
        self._base_settings = AppSettings.from_dict(settings.to_dict())
        self._font_families = self._terminal_font_families()
        self._on_export_requested = on_export_requested
        self._on_import_requested = on_import_requested
        self._on_third_party_io_requested = on_third_party_io_requested
        self._on_test_secrets_requested = on_test_secrets_requested
        self._on_setup_secrets_requested = on_setup_secrets_requested
        self._on_desktop_install_requested = on_desktop_install_requested
        self._on_desktop_uninstall_requested = on_desktop_uninstall_requested
        self._on_manage_tool_launchers_requested = on_manage_tool_launchers_requested
        self._on_preview_requested = on_preview_requested
        self._backend_auth_state = dict(backend_auth_state or {})
        self._suppress_theme_selection = False
        self._suppress_custom_theme_auto_switch = False

        self.app_bg_start_widget, self.app_bg_start = self._build_color_input(settings.app_bg_start)
        self.app_bg_end_widget, self.app_bg_end = self._build_color_input(settings.app_bg_end)
        self.text_color_widget, self.text_color = self._build_color_input(settings.text_color)
        self.field_bg_widget, self.field_bg = self._build_color_input(settings.field_bg)
        self.field_border_widget, self.field_border = self._build_color_input(settings.field_border)
        self.accent_color_widget, self.accent_color = self._build_color_input(settings.accent_color)
        self.accent_hover_widget, self.accent_hover = self._build_color_input(settings.accent_hover)
        self.accent_pressed_widget, self.accent_pressed = self._build_color_input(settings.accent_pressed)
        self.terminal_bg_widget, self.terminal_bg = self._build_color_input(settings.terminal_bg)
        self.terminal_fg_widget, self.terminal_fg = self._build_color_input(settings.terminal_fg)
        self.terminal_classic_default_colors = QCheckBox(
            "Use classic black/white terminal default colors for TUI apps"
        )
        self.terminal_classic_default_colors.setChecked(settings.terminal_classic_default_colors)
        self.terminal_classic_default_colors_help = QLabel(
            "Some ncurses apps mix explicit ANSI black with the terminal's default colors. "
            "Enable this to map the terminal defaults to classic black/white without changing the rest of the app theme."
        )
        self.terminal_classic_default_colors_help.setWordWrap(True)
        self.tab_active_bg_widget, self.tab_active_bg = self._build_color_input(settings.tab_active_bg)
        self.tab_active_fg_widget, self.tab_active_fg = self._build_color_input(settings.tab_active_fg)
        self.tab_inactive_bg_widget, self.tab_inactive_bg = self._build_color_input(settings.tab_inactive_bg)
        self.tab_inactive_fg_widget, self.tab_inactive_fg = self._build_color_input(settings.tab_inactive_fg)
        self.theme_name = QComboBox()
        for preset in THEME_PRESETS:
            self.theme_name.addItem(preset.label, preset.theme_id)
        self.theme_name.addItem("Custom", CUSTOM_THEME_ID)
        self._set_theme_selection(self._theme_name_for_settings(settings))

        self.font_family = QComboBox()
        self.font_family.addItems(self._font_families)
        self._set_font_selection(settings.terminal_font_family)

        self.font_pt = QSpinBox()
        self.font_pt.setRange(8, 20)
        self.font_pt.setValue(settings.terminal_font_pt)

        self.main_window_fullscreen_shortcut = ShortcutPreferenceEditor(
            initial_shortcut=settings.main_window_fullscreen_shortcut,
            default_shortcut=self._defaults.main_window_fullscreen_shortcut,
            dialog_title="Set Fullscreen Shortcut",
            parent=self,
        )
        self.main_window_hide_controls_in_fullscreen = QCheckBox(
            "Hide the top action bar and bottom command bar during fullscreen"
        )
        self.main_window_hide_controls_in_fullscreen.setChecked(settings.main_window_hide_controls_in_fullscreen)

        self.scrollback = QSpinBox()
        self.scrollback.setRange(100, 50000)
        self.scrollback.setSingleStep(100)
        self.scrollback.setValue(settings.terminal_scrollback_lines)
        self.scrollback_ram_estimate = QLabel("")
        self.scrollback_ram_estimate.setWordWrap(True)
        self.scrollback_ram_estimate.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.log_dir_widget, self.log_dir = self._build_directory_input(
            settings.terminal_log_dir,
            dialog_title="Select Session Log Folder",
            on_open_requested=lambda value: self._open_folder_path(
                value or self._defaults.terminal_log_dir,
                label="Session Log Folder",
            ),
        )
        self.global_session_logging_enabled = QCheckBox("Automatically log terminal sessions on connect")
        self.global_session_logging_enabled.setChecked(settings.global_session_logging_enabled)
        self.session_log_cleanup_enabled = QCheckBox("Automatically clean old session logs")
        self.session_log_cleanup_enabled.setChecked(settings.session_log_cleanup_enabled)
        self.session_log_retention_days = QSpinBox()
        self.session_log_retention_days.setRange(1, 3650)
        self.session_log_retention_days.setValue(settings.session_log_retention_days)
        self.web_server_log_cleanup_enabled = QCheckBox("Automatically clean old web server logs")
        self.web_server_log_cleanup_enabled.setChecked(settings.web_server_log_cleanup_enabled)
        self.web_server_log_retention_days = QSpinBox()
        self.web_server_log_retention_days.setRange(1, 3650)
        self.web_server_log_retention_days.setValue(settings.web_server_log_retention_days)
        self.crash_logging_enabled = QCheckBox("Enable crash logging for fatal errors only")
        self.crash_logging_enabled.setChecked(settings.crash_logging_enabled)
        self.crash_logging_enabled.setToolTip(
            "Captures fatal crashes and native faults. For live hangs or freezes, launch SnakeSh with "
            "--debug-level debug."
        )
        self.crash_logging_widget = QWidget(self)
        crash_logging_layout = QHBoxLayout(self.crash_logging_widget)
        crash_logging_layout.setContentsMargins(0, 0, 0, 0)
        crash_logging_layout.setSpacing(6)
        self.open_crash_log_folder_btn = QPushButton("Open Folder")
        self.open_crash_log_folder_btn.setToolTip(
            "Open the fatal crash log folder. Freeze investigation logs created with --debug-level use "
            "the debug log folder under SnakeSh data."
        )
        self.open_crash_log_folder_btn.clicked.connect(self._open_crash_log_folder)
        crash_logging_layout.addWidget(self.crash_logging_enabled, 1)
        crash_logging_layout.addWidget(self.open_crash_log_folder_btn, 0)
        self.cursor_blink = QCheckBox("Blink cursor in SSH sessions")
        self.cursor_blink.setChecked(settings.terminal_cursor_blink)
        self.terminal_bell = QCheckBox("Enable terminal bell on BEL")
        self.terminal_bell.setChecked(settings.terminal_bell_enabled)
        self.terminal_visual_bell = QCheckBox("Enable visual bell flash on BEL")
        self.terminal_visual_bell.setChecked(settings.terminal_visual_bell_enabled)
        self.local_shell_command_override = QLineEdit(settings.local_shell_command_override)
        self.local_shell_start_dir_mode = QComboBox()
        self.local_shell_start_dir_mode.addItem("Home directory", "home")
        self.local_shell_start_dir_mode.addItem("App current directory", "cwd")
        self.local_shell_start_dir_mode.addItem("Custom path", "custom")
        self.local_shell_custom_start_dir_widget, self.local_shell_custom_start_dir = self._build_directory_input(
            settings.local_shell_custom_start_dir,
            dialog_title="Select Local Shell Startup Directory",
        )

        self.warn_before_file_delete = QCheckBox("Warn before deleting files in SFTP")
        self.warn_before_file_delete.setChecked(settings.warn_before_file_delete)
        self.warn_before_file_overwrite = QCheckBox("Warn before overwriting files in SFTP")
        self.warn_before_file_overwrite.setChecked(settings.warn_before_file_overwrite)
        self.warn_before_closing_active_tab = QCheckBox("Warn before closing active tabs")
        self.warn_before_closing_active_tab.setChecked(settings.warn_before_closing_active_tab)
        self._has_existing_master_password = MasterPasswordService.has_master_password(settings)
        self.master_password_enabled = QCheckBox("Require a master password before opening SnakeSh")
        self.master_password_enabled.setChecked(
            settings.master_password_enabled and self._has_existing_master_password
        )
        self.master_password_tools_enabled = QCheckBox("Require a master password before launching standalone tools")
        self.master_password_tools_enabled.setChecked(
            settings.master_password_tools_enabled and self._has_existing_master_password
        )
        self.master_password = QLineEdit("")
        self.master_password.setEchoMode(QLineEdit.Password)
        self.master_password_confirm = QLineEdit("")
        self.master_password_confirm.setEchoMode(QLineEdit.Password)
        self.master_password_clear = QCheckBox("Clear stored master password")
        self.master_password_clear.setEnabled(self._has_existing_master_password)
        self.master_password_hint = QLabel("")
        self.master_password_hint.setWordWrap(True)

        self.secrets_backend = QComboBox()
        self.secrets_backend.addItem("OS Keyring (default)", "keyring")
        self.secrets_backend.addItem("1Password CLI", "1password")
        self.secrets_backend.addItem("Bitwarden CLI", "bitwarden")
        self.secrets_backend.addItem("Keeper Commander CLI", "keeper")
        self.secrets_backend.addItem("KeePass 2.x (KeePassXC CLI)", "keepass")
        self.secrets_backend.addItem("HashiCorp Vault (KV v2)", "vault")

        self.onepassword_vault = QLineEdit(settings.onepassword_vault)
        self.onepassword_account = QLineEdit(settings.onepassword_account)
        self.onepassword_cli_path = QLineEdit(settings.onepassword_cli_path)
        self.bitwarden_cli_path = QLineEdit(settings.bitwarden_cli_path)
        self.keeper_cli_path = QLineEdit(settings.keeper_cli_path)
        self.keeper_user = QLineEdit(settings.keeper_user)
        self.keeper_server = QLineEdit(settings.keeper_server)
        self.keeper_folder = QLineEdit(settings.keeper_folder)
        self.keepass_cli_path = QLineEdit(settings.keepass_cli_path)
        self.keepass_database_path = QLineEdit(settings.keepass_database_path)
        self.keepass_password_env = QLineEdit(settings.keepass_password_env)
        self.keepass_key_file_path = QLineEdit(settings.keepass_key_file_path)
        self.keepass_group = QLineEdit(settings.keepass_group)
        self.vault_addr = QLineEdit(settings.vault_addr)
        self.vault_mount = QLineEdit(settings.vault_mount)
        self.vault_token_env = QLineEdit(settings.vault_token_env)
        self.vault_namespace = QLineEdit(settings.vault_namespace)
        self.vault_skip_tls_verify = QCheckBox("Skip TLS certificate verification")
        self.vault_skip_tls_verify.setChecked(settings.vault_skip_tls_verify)
        (
            self.onepassword_service_token_widget,
            self.onepassword_service_token,
            self.onepassword_service_token_clear,
        ) = self._build_secret_input(
            has_existing=self._backend_auth_state.get("onepassword_service_token", False)
        )
        (
            self.bitwarden_session_widget,
            self.bitwarden_session,
            self.bitwarden_session_clear,
        ) = self._build_secret_input(
            has_existing=self._backend_auth_state.get("bitwarden_session", False)
        )
        (
            self.keepass_master_password_widget,
            self.keepass_master_password,
            self.keepass_master_password_clear,
        ) = self._build_secret_input(
            has_existing=self._backend_auth_state.get("keepass_master_password", False)
        )
        (
            self.keeper_master_password_widget,
            self.keeper_master_password,
            self.keeper_master_password_clear,
        ) = self._build_secret_input(
            has_existing=self._backend_auth_state.get("keeper_master_password", False)
        )
        (
            self.vault_token_widget,
            self.vault_token,
            self.vault_token_clear,
        ) = self._build_secret_input(
            has_existing=self._backend_auth_state.get("vault_token", False)
        )

        self.test_secrets_btn = QPushButton("Test Backend")
        self.test_secrets_btn.clicked.connect(self._handle_test_secrets_backend)
        self.test_secrets_btn.setEnabled(self._on_test_secrets_requested is not None)
        self.setup_secrets_btn = QPushButton("Setup Backend")
        self.setup_secrets_btn.clicked.connect(self._handle_setup_secrets_backend)
        self.setup_secrets_btn.setEnabled(self._on_setup_secrets_requested is not None)
        self.secrets_health = QLabel("")
        self.secrets_health.setWordWrap(False)
        self.secrets_test_hint = QLabel("Checks backend connectivity/configuration only. No secrets are changed.")
        self.secrets_test_hint.setWordWrap(True)
        self.secrets_auth_hint = QLabel(
            "Sensitive auth values are stored in the OS keyring. Leave blank to keep current values."
        )
        self.secrets_auth_hint.setWordWrap(True)
        self.secrets_provider_help = QLabel("")
        self.secrets_provider_help.setWordWrap(True)

        self._form_container = QWidget(self)
        self._form = QFormLayout(self._form_container)
        form = self._form
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        form.addRow(self._section_label("Appearance"))
        form.addRow(
            self._hint_label(
                "Theme",
                "Choose a curated palette. Manual color edits automatically switch this to Custom.",
            ),
            self.theme_name,
        )
        form.addRow(
            self._hint_label("App Background Start", "Main app/window gradient start color."),
            self.app_bg_start_widget,
        )
        form.addRow(
            self._hint_label("App Background End", "Main app/window gradient end color."),
            self.app_bg_end_widget,
        )
        form.addRow(
            self._hint_label("Text Color", "Primary text color used across the app."),
            self.text_color_widget,
        )
        form.addRow(
            self._hint_label("Field Background", "Input/list/table background color."),
            self.field_bg_widget,
        )
        form.addRow(
            self._hint_label("Field Border", "Input/list/table border color."),
            self.field_border_widget,
        )
        form.addRow(
            self._hint_label("Accent Color", "Button and action base color."),
            self.accent_color_widget,
        )
        form.addRow(
            self._hint_label("Accent Hover", "Button hover color."),
            self.accent_hover_widget,
        )
        form.addRow(
            self._hint_label("Accent Pressed", "Button pressed color."),
            self.accent_pressed_widget,
        )

        form.addRow(self._section_label("Tab Colors"))
        form.addRow(
            self._hint_label("Tab Active Background", "Background color for the selected tab."),
            self.tab_active_bg_widget,
        )
        form.addRow(
            self._hint_label("Tab Active Foreground", "Text color for the selected tab."),
            self.tab_active_fg_widget,
        )
        form.addRow(
            self._hint_label("Tab Inactive Background", "Background color for unselected tabs."),
            self.tab_inactive_bg_widget,
        )
        form.addRow(
            self._hint_label("Tab Inactive Foreground", "Text color for unselected tabs."),
            self.tab_inactive_fg_widget,
        )

        form.addRow(self._section_label("Window"))
        form.addRow(
            self._hint_label("Fullscreen Shortcut", "Toggles borderless fullscreen for the main SnakeSh window."),
            self.main_window_fullscreen_shortcut,
        )
        form.addRow(
            self._hint_label(
                "Hide Controls in Fullscreen",
                "When enabled, fullscreen only shows the session list and workspace tabs.",
            ),
            self.main_window_hide_controls_in_fullscreen,
        )

        form.addRow(self._section_label("Terminal"))
        form.addRow(
            self._hint_label(
                "Terminal Background",
                "Default terminal canvas color used when classic default-color compatibility is disabled.",
            ),
            self.terminal_bg_widget,
        )
        form.addRow(
            self._hint_label(
                "Terminal Foreground",
                "Default terminal text color used when classic default-color compatibility is disabled.",
            ),
            self.terminal_fg_widget,
        )
        form.addRow(
            self._hint_label(
                "Classic Default Colors",
                "Use classic black/white terminal defaults for apps that rely on the terminal's default colors instead of explicit ANSI black.",
            ),
            self.terminal_classic_default_colors,
        )
        form.addRow(
            self._hint_label(
                "Default Color Help",
                "Explains the difference between explicit ANSI colors and the terminal's default colors.",
            ),
            self.terminal_classic_default_colors_help,
        )
        form.addRow(QLabel("Terminal Font Family"), self.font_family)
        form.addRow(QLabel("Terminal Font (pt)"), self.font_pt)
        form.addRow(QLabel("Terminal Scrollback Lines"), self.scrollback)
        form.addRow(
            self._hint_label(
                "Per-Terminal RAM Estimate",
                "Rough upper bound for in-memory terminal state retained for each terminal's searchable scrollback.",
            ),
            self.scrollback_ram_estimate,
        )
        form.addRow(QLabel("Session Log Folder"), self.log_dir_widget)
        form.addRow(QLabel("Global Session Logging"), self.global_session_logging_enabled)
        form.addRow(QLabel("Session Log Cleanup"), self.session_log_cleanup_enabled)
        form.addRow(QLabel("Session Log Retention (days)"), self.session_log_retention_days)
        form.addRow(QLabel("Web Server Log Cleanup"), self.web_server_log_cleanup_enabled)
        form.addRow(QLabel("Web Server Log Retention (days)"), self.web_server_log_retention_days)
        form.addRow(QLabel("Crash Logging"), self.crash_logging_widget)
        form.addRow(QLabel("Cursor Blink"), self.cursor_blink)
        form.addRow(QLabel("Terminal Bell"), self.terminal_bell)
        form.addRow(QLabel("Visual Bell"), self.terminal_visual_bell)
        form.addRow(QLabel("SFTP Delete Prompt"), self.warn_before_file_delete)
        form.addRow(QLabel("SFTP Overwrite Prompt"), self.warn_before_file_overwrite)
        form.addRow(QLabel("Active Tab Close Prompt"), self.warn_before_closing_active_tab)
        form.addRow(self._section_label("Local Shell"))
        form.addRow(QLabel("Default Shell Command"), self.local_shell_command_override)
        form.addRow(QLabel("Startup Directory"), self.local_shell_start_dir_mode)
        form.addRow(QLabel("Custom Startup Directory"), self.local_shell_custom_start_dir_widget)

        form.addRow(self._section_label("Startup Security"))
        form.addRow(QLabel("Master Password"), self.master_password_enabled)
        form.addRow(QLabel("Tool Launch Password"), self.master_password_tools_enabled)
        form.addRow(QLabel("New Password"), self.master_password)
        form.addRow(QLabel("Confirm Password"), self.master_password_confirm)
        form.addRow(QLabel("Clear Saved Password"), self.master_password_clear)
        form.addRow(QLabel("Master Password Help"), self.master_password_hint)

        form.addRow(self._section_label("Secrets Backend"))
        form.addRow(QLabel("Backend Provider"), self.secrets_backend)
        form.addRow(QLabel("Backend Auth"), self.secrets_auth_hint)
        form.addRow(QLabel("Backend Guide"), self.secrets_provider_help)

        self._onepassword_rows = [
            (QLabel("1Password Vault"), self.onepassword_vault),
            (QLabel("1Password Account"), self.onepassword_account),
            (QLabel("1Password CLI Path"), self.onepassword_cli_path),
            (QLabel("1Password Service Token"), self.onepassword_service_token_widget),
        ]
        for label, widget in self._onepassword_rows:
            form.addRow(label, widget)

        self._bitwarden_rows = [
            (QLabel("Bitwarden CLI Path"), self.bitwarden_cli_path),
            (QLabel("Bitwarden Session"), self.bitwarden_session_widget),
        ]
        for label, widget in self._bitwarden_rows:
            form.addRow(label, widget)

        self._keeper_rows = [
            (QLabel("Keeper CLI Path"), self.keeper_cli_path),
            (QLabel("Keeper User"), self.keeper_user),
            (QLabel("Keeper Master Password"), self.keeper_master_password_widget),
            (QLabel("Keeper Server (optional)"), self.keeper_server),
            (QLabel("Keeper Folder"), self.keeper_folder),
        ]
        for label, widget in self._keeper_rows:
            form.addRow(label, widget)

        self._keepass_rows = [
            (QLabel("KeePass CLI Path"), self.keepass_cli_path),
            (QLabel("KeePass Database"), self.keepass_database_path),
            (QLabel("KeePass Master Password"), self.keepass_master_password_widget),
            (QLabel("KeePass Password Env Var"), self.keepass_password_env),
            (QLabel("KeePass Key File (optional)"), self.keepass_key_file_path),
            (QLabel("KeePass Group"), self.keepass_group),
        ]
        for label, widget in self._keepass_rows:
            form.addRow(label, widget)

        self._vault_rows = [
            (QLabel("Vault Address"), self.vault_addr),
            (QLabel("Vault Mount"), self.vault_mount),
            (QLabel("Vault Token"), self.vault_token_widget),
            (QLabel("Vault Token Env Var"), self.vault_token_env),
            (QLabel("Vault Namespace"), self.vault_namespace),
            (QLabel("Vault TLS"), self.vault_skip_tls_verify),
        ]
        for label, widget in self._vault_rows:
            form.addRow(label, widget)

        secrets_health_row = QWidget(self)
        secrets_health_layout = QHBoxLayout(secrets_health_row)
        secrets_health_layout.setContentsMargins(0, 0, 0, 0)
        secrets_health_layout.setSpacing(6)
        secrets_health_layout.addWidget(self.setup_secrets_btn, 0)
        secrets_health_layout.addWidget(self.test_secrets_btn, 0)
        secrets_health_layout.addWidget(self.secrets_health, 1)
        form.addRow(QLabel("Backend Status"), secrets_health_row)
        form.addRow(QLabel("Backend Test"), self.secrets_test_hint)

        export_btn = QPushButton("Export SnakeSh")
        import_btn = QPushButton("Import SnakeSh")
        third_party_btn = QPushButton("Third Party Import")
        desktop_install_btn = QPushButton("Install/Repair Desktop Integration")
        desktop_remove_btn = QPushButton("Remove Desktop Integration")
        manage_tool_launchers_btn = QPushButton("Manage Tool Launchers...")
        export_btn.clicked.connect(self._handle_export)
        import_btn.clicked.connect(self._handle_import)
        third_party_btn.clicked.connect(self._handle_third_party_io)
        desktop_install_btn.clicked.connect(self._handle_desktop_install)
        desktop_remove_btn.clicked.connect(self._handle_desktop_uninstall)
        manage_tool_launchers_btn.clicked.connect(self._handle_manage_tool_launchers)
        export_btn.setEnabled(self._on_export_requested is not None)
        import_btn.setEnabled(self._on_import_requested is not None)
        third_party_btn.setEnabled(self._on_third_party_io_requested is not None)
        desktop_install_btn.setVisible(self._on_desktop_install_requested is not None)
        desktop_remove_btn.setVisible(self._on_desktop_uninstall_requested is not None)
        manage_tool_launchers_btn.setVisible(self._on_manage_tool_launchers_requested is not None)
        self._lock_button_widths(
            export_btn,
            import_btn,
            third_party_btn,
            desktop_install_btn,
            desktop_remove_btn,
            manage_tool_launchers_btn,
        )

        reset_btn = QPushButton("Restore Defaults")
        reset_btn.clicked.connect(self._restore_defaults)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)

        io_actions = QVBoxLayout()
        io_row_primary = QHBoxLayout()
        io_row_primary.addWidget(export_btn)
        io_row_primary.addWidget(import_btn)
        io_row_primary.addWidget(third_party_btn)
        io_row_primary.addStretch(1)
        io_actions.addLayout(io_row_primary)

        io_row_desktop = QHBoxLayout()
        io_row_desktop.addWidget(desktop_install_btn)
        io_row_desktop.addWidget(desktop_remove_btn)
        io_row_desktop.addWidget(manage_tool_launchers_btn)
        io_row_desktop.addStretch(1)
        io_actions.addLayout(io_row_desktop)

        bottom_actions = QHBoxLayout()
        bottom_actions.addWidget(reset_btn)
        bottom_actions.addStretch(1)
        bottom_actions.addWidget(buttons)

        root = QVBoxLayout(self)
        self._form_scroll = QScrollArea(self)
        self._form_scroll.setWidgetResizable(True)
        self._form_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._form_scroll.setWidget(self._form_container)
        root.addWidget(self._form_scroll, 1)
        root.addLayout(io_actions)
        root.addLayout(bottom_actions)

        self._set_backend_selection(settings.secrets_backend)
        self.secrets_backend.currentIndexChanged.connect(self._update_secrets_rows)
        self._set_local_shell_start_dir_mode(settings.local_shell_start_dir_mode)
        self.local_shell_start_dir_mode.currentIndexChanged.connect(self._update_local_shell_controls)
        self.session_log_cleanup_enabled.toggled.connect(self._update_session_log_cleanup_controls)
        self.web_server_log_cleanup_enabled.toggled.connect(self._update_web_server_log_cleanup_controls)
        self.master_password_enabled.toggled.connect(self._update_master_password_controls)
        self.master_password_tools_enabled.toggled.connect(self._update_master_password_controls)
        self.master_password_clear.toggled.connect(self._update_master_password_controls)
        self.master_password.textChanged.connect(self._update_master_password_controls)
        self.scrollback.valueChanged.connect(self._update_scrollback_estimate)
        self._update_master_password_controls()
        self._update_local_shell_controls()
        self._update_scrollback_estimate()
        self._update_session_log_cleanup_controls()
        self._update_web_server_log_cleanup_controls()
        self._update_secrets_rows()
        self.theme_name.currentIndexChanged.connect(self._on_theme_selection_changed)
        self._bind_live_preview_controls()
        QTimer.singleShot(0, self._sync_wrapped_hint_heights)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._sync_wrapped_hint_heights()

    def _restore_defaults(self) -> None:
        answer = QMessageBox.warning(
            self,
            "Restore Defaults",
            (
                "Restore all settings to default values?\n\n"
                "This also clears saved Workspace Profiles, Fast Commands, Web Server Profiles, and Syslog / SNMP Monitor Profiles "
                "when you save settings."
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self.apply_settings(self._defaults)

    def _handle_export(self) -> None:
        if self._on_export_requested:
            self._on_export_requested()

    def _handle_import(self) -> None:
        if not self._on_import_requested:
            return
        imported = self._on_import_requested()
        if imported:
            self.apply_settings(imported)

    def _handle_third_party_io(self) -> None:
        if self._on_third_party_io_requested:
            self._on_third_party_io_requested()

    def _handle_desktop_install(self) -> None:
        if not self._on_desktop_install_requested:
            return
        ok, message = self._on_desktop_install_requested()
        if ok:
            QMessageBox.information(self, "Desktop Integration", message)
            return
        QMessageBox.warning(self, "Desktop Integration", message)

    def _handle_desktop_uninstall(self) -> None:
        if not self._on_desktop_uninstall_requested:
            return
        ok, message = self._on_desktop_uninstall_requested()
        if ok:
            QMessageBox.information(self, "Desktop Integration", message)
            return
        QMessageBox.warning(self, "Desktop Integration", message)

    def _handle_manage_tool_launchers(self) -> None:
        if self._on_manage_tool_launchers_requested is not None:
            self._on_manage_tool_launchers_requested()

    def _handle_test_secrets_backend(self) -> None:
        if not self._on_test_secrets_requested:
            return
        ok, message = self._on_test_secrets_requested(
            self.build_settings(),
            self.build_backend_auth_updates(),
        )
        self.secrets_health.setStyleSheet(f"color: {'#166534' if ok else '#b91c1c'};")
        self.secrets_health.setText(message)

    def _handle_setup_secrets_backend(self) -> None:
        if not self._on_setup_secrets_requested:
            return
        ok, message = self._on_setup_secrets_requested(
            self.build_settings(),
            self.build_backend_auth_updates(),
        )
        self.secrets_health.setStyleSheet(f"color: {'#166534' if ok else '#b91c1c'};")
        self.secrets_health.setText(message)

    @staticmethod
    def _section_label(title: str) -> QLabel:
        label = QLabel(title)
        label.setStyleSheet("font-weight: 600; margin-top: 8px;")
        return label

    @staticmethod
    def _hint_label(title: str, tip: str) -> QLabel:
        label = QLabel(title)
        label.setToolTip(tip)
        return label

    @staticmethod
    def _lock_button_widths(*buttons: QPushButton) -> None:
        for button in buttons:
            button.setMinimumWidth(button.sizeHint().width())

    def _open_folder_path(self, raw_path: str, *, label: str) -> None:
        path_text = raw_path.strip()
        target = Path(path_text).expanduser() if path_text else Path.home()
        if target.exists() and not target.is_dir():
            QMessageBox.warning(self, label, f"The selected path is not a folder:\n{target}")
            return
        try:
            target.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            QMessageBox.warning(self, label, f"Unable to create or open the folder:\n{target}\n\n{exc}")
            return
        if open_local_path(target):
            return
        QMessageBox.warning(self, label, f"Unable to open the folder:\n{target}")

    def _open_crash_log_folder(self) -> None:
        self._open_folder_path(str(data_dir() / "logs"), label="Crash Logging Folder")

    def _theme_color_fields(self) -> dict[str, QLineEdit]:
        return {
            "app_bg_start": self.app_bg_start,
            "app_bg_end": self.app_bg_end,
            "text_color": self.text_color,
            "field_bg": self.field_bg,
            "field_border": self.field_border,
            "accent_color": self.accent_color,
            "accent_hover": self.accent_hover,
            "accent_pressed": self.accent_pressed,
            "terminal_bg": self.terminal_bg,
            "terminal_fg": self.terminal_fg,
            "tab_active_bg": self.tab_active_bg,
            "tab_active_fg": self.tab_active_fg,
            "tab_inactive_bg": self.tab_inactive_bg,
            "tab_inactive_fg": self.tab_inactive_fg,
        }

    @staticmethod
    def _theme_color_values_from_settings(settings: AppSettings) -> dict[str, str]:
        return {
            key: str(getattr(settings, key))
            for key in THEME_COLOR_FIELDS
        }

    def _theme_name_for_settings(self, settings: AppSettings) -> str:
        theme_name = normalize_theme_id(settings.theme_name)
        color_values = self._theme_color_values_from_settings(settings)
        if theme_name != CUSTOM_THEME_ID and not theme_matches_colors(theme_name, color_values):
            return infer_theme_id_from_colors(color_values)
        return theme_name

    def _selected_theme_name(self) -> str:
        current = self.theme_name.currentData()
        if isinstance(current, str) and current:
            return normalize_theme_id(current)
        return CUSTOM_THEME_ID

    def _set_theme_selection(self, theme_name: str) -> None:
        target = normalize_theme_id(theme_name)
        self._suppress_theme_selection = True
        try:
            for index in range(self.theme_name.count()):
                data = self.theme_name.itemData(index)
                if isinstance(data, str) and normalize_theme_id(data) == target:
                    self.theme_name.setCurrentIndex(index)
                    return
            for index in range(self.theme_name.count()):
                data = self.theme_name.itemData(index)
                if isinstance(data, str) and data == CUSTOM_THEME_ID:
                    self.theme_name.setCurrentIndex(index)
                    return
            self.theme_name.setCurrentIndex(0)
        finally:
            self._suppress_theme_selection = False

    def _on_theme_selection_changed(self) -> None:
        if self._suppress_theme_selection:
            return
        theme_name = self._selected_theme_name()
        if theme_name == CUSTOM_THEME_ID:
            return
        self._apply_theme_preset(theme_name)

    def _apply_theme_preset(self, theme_name: str) -> None:
        preset_colors = theme_colors_for(theme_name)
        if preset_colors is None:
            return
        self._suppress_custom_theme_auto_switch = True
        try:
            for key, field in self._theme_color_fields().items():
                field.setText(preset_colors[key])
        finally:
            self._suppress_custom_theme_auto_switch = False

    def _auto_switch_theme_to_custom(self) -> None:
        if self._suppress_custom_theme_auto_switch:
            return
        if self._selected_theme_name() == CUSTOM_THEME_ID:
            return
        self._set_theme_selection(CUSTOM_THEME_ID)

    def _selected_backend(self) -> str:
        current = self.secrets_backend.currentData()
        if isinstance(current, str) and current:
            return current
        return "keyring"

    def _selected_local_shell_start_dir_mode(self) -> str:
        current = self.local_shell_start_dir_mode.currentData()
        if isinstance(current, str) and current:
            return current
        return "home"

    def _set_local_shell_start_dir_mode(self, mode: str) -> None:
        target = (mode or "home").strip().lower()
        for index in range(self.local_shell_start_dir_mode.count()):
            data = self.local_shell_start_dir_mode.itemData(index)
            if isinstance(data, str) and data.lower() == target:
                self.local_shell_start_dir_mode.setCurrentIndex(index)
                return
        self.local_shell_start_dir_mode.setCurrentIndex(0)

    def _update_local_shell_controls(self) -> None:
        custom_selected = self._selected_local_shell_start_dir_mode() == "custom"
        self.local_shell_custom_start_dir_widget.setEnabled(custom_selected)

    @staticmethod
    def _format_memory_size(byte_count: int) -> str:
        size = float(max(0, byte_count))
        for unit in ("B", "KiB", "MiB", "GiB"):
            if size < 1024.0 or unit == "GiB":
                if unit == "B":
                    return f"{int(size)} {unit}"
                return f"{size:.1f} {unit}"
            size /= 1024.0
        return f"{size:.1f} GiB"

    @staticmethod
    def _estimate_terminal_scrollback_ram_bytes(lines: int) -> int:
        normalized = max(100, int(lines))
        return _SCROLLBACK_ESTIMATED_FIXED_BYTES + (normalized * _SCROLLBACK_ESTIMATED_BYTES_PER_LINE)

    def _update_scrollback_estimate(self) -> None:
        lines = self.scrollback.value()
        estimate = self._estimate_terminal_scrollback_ram_bytes(lines)
        self.scrollback_ram_estimate.setText(
            (
                f"Rough upper bound: about {self._format_memory_size(estimate)} RAM per terminal "
                f"at {lines:,} lines. Actual usage depends on terminal width and content."
            )
        )
        QTimer.singleShot(0, self._sync_wrapped_hint_heights)

    def _update_session_log_cleanup_controls(self) -> None:
        self.session_log_retention_days.setEnabled(self.session_log_cleanup_enabled.isChecked())

    def _update_web_server_log_cleanup_controls(self) -> None:
        self.web_server_log_retention_days.setEnabled(self.web_server_log_cleanup_enabled.isChecked())

    def _set_backend_selection(self, backend: str) -> None:
        target = (backend or "keyring").strip().lower()
        for index in range(self.secrets_backend.count()):
            data = self.secrets_backend.itemData(index)
            if isinstance(data, str) and data.lower() == target:
                self.secrets_backend.setCurrentIndex(index)
                return
        self.secrets_backend.setCurrentIndex(0)

    def _update_secrets_rows(self) -> None:
        backend = self._selected_backend()
        show_onepassword = backend == "1password"
        show_bitwarden = backend == "bitwarden"
        show_keeper = backend == "keeper"
        show_keepass = backend == "keepass"
        show_vault = backend == "vault"
        if hasattr(self._form, "setRowVisible"):
            for label, _widget in self._onepassword_rows:
                self._form.setRowVisible(label, show_onepassword)
            for label, _widget in self._bitwarden_rows:
                self._form.setRowVisible(label, show_bitwarden)
            for label, _widget in self._keeper_rows:
                self._form.setRowVisible(label, show_keeper)
            for label, _widget in self._keepass_rows:
                self._form.setRowVisible(label, show_keepass)
            for label, _widget in self._vault_rows:
                self._form.setRowVisible(label, show_vault)
        else:
            for label, widget in self._onepassword_rows:
                label.setVisible(show_onepassword)
                widget.setVisible(show_onepassword)
            for label, widget in self._bitwarden_rows:
                label.setVisible(show_bitwarden)
                widget.setVisible(show_bitwarden)
            for label, widget in self._keeper_rows:
                label.setVisible(show_keeper)
                widget.setVisible(show_keeper)
            for label, widget in self._keepass_rows:
                label.setVisible(show_keepass)
                widget.setVisible(show_keepass)
            for label, widget in self._vault_rows:
                label.setVisible(show_vault)
                widget.setVisible(show_vault)
        self.secrets_provider_help.setText(self._backend_setup_help_text(backend))
        self.secrets_health.clear()
        QTimer.singleShot(0, self._sync_wrapped_hint_heights)

    def _sync_wrapped_hint_heights(self) -> None:
        hint_rows = (
            (self.terminal_classic_default_colors_help, 2),
            (self.scrollback_ram_estimate, 3),
            (self.master_password_hint, 2),
            (self.secrets_auth_hint, 2),
            (self.secrets_provider_help, 2),
            (self.secrets_test_hint, 2),
        )
        for label, min_lines in hint_rows:
            width = label.width()
            if width <= 1:
                continue
            metrics = label.fontMetrics()
            text = label.text().strip()
            wrapped_height = metrics.boundingRect(0, 0, width, 2000, Qt.TextWordWrap, text).height() if text else 0
            min_height = metrics.lineSpacing() * min_lines
            label.setMinimumHeight(max(min_height, wrapped_height))

    @staticmethod
    def _backend_setup_help_text(backend: str) -> str:
        if backend == "1password":
            return (
                "Install `op`, set vault/account in SnakeSh, then either sign in once with `op signin` "
                "or save a Service Token here. Use Setup Backend, then Test Backend."
            )
        if backend == "bitwarden":
            return (
                "Install `bw`, run `bw login` and `bw unlock`, then optionally save BW_SESSION here. "
                "Use Setup Backend to verify unlock/sync, then Test Backend."
            )
        if backend == "keeper":
            return (
                "Install Keeper Commander (`keeper`), set user/folder and optionally server. "
                "Save master password here or use KEEPER_PASSWORD. Setup Backend validates login/sync."
            )
        if backend == "keepass":
            return (
                "Install `keepassxc-cli`, set a KeePass 2.x .kdbx path, and provide master password here "
                "or via the configured env var. Optional key file is supported. Use Test Backend."
            )
        if backend == "vault":
            return (
                "Set Vault address/mount/namespace and provide token here or via the configured env var. "
                "Setup/Test verifies KV v2 connectivity."
            )
        return "Uses OS keyring directly. No extra setup is required."

    def build_settings(self) -> AppSettings:
        payload = self._base_settings.to_dict()
        payload.update(
            {
                "theme_name": self._selected_theme_name(),
                "app_bg_start": self.app_bg_start.text().strip() or self._defaults.app_bg_start,
                "app_bg_end": self.app_bg_end.text().strip() or self._defaults.app_bg_end,
                "text_color": self.text_color.text().strip() or self._defaults.text_color,
                "field_bg": self.field_bg.text().strip() or self._defaults.field_bg,
                "field_border": self.field_border.text().strip() or self._defaults.field_border,
                "accent_color": self.accent_color.text().strip() or self._defaults.accent_color,
                "accent_hover": self.accent_hover.text().strip() or self._defaults.accent_hover,
                "accent_pressed": self.accent_pressed.text().strip() or self._defaults.accent_pressed,
                "terminal_bg": self.terminal_bg.text().strip() or self._defaults.terminal_bg,
                "terminal_fg": self.terminal_fg.text().strip() or self._defaults.terminal_fg,
                "terminal_classic_default_colors": self.terminal_classic_default_colors.isChecked(),
                "tab_active_bg": self.tab_active_bg.text().strip() or self._defaults.tab_active_bg,
                "tab_active_fg": self.tab_active_fg.text().strip() or self._defaults.tab_active_fg,
                "tab_inactive_bg": self.tab_inactive_bg.text().strip() or self._defaults.tab_inactive_bg,
                "tab_inactive_fg": self.tab_inactive_fg.text().strip() or self._defaults.tab_inactive_fg,
                "terminal_font_family": self.font_family.currentText().strip() or "Courier New",
                "terminal_font_pt": self.font_pt.value(),
                "main_window_fullscreen_shortcut": (
                    self.main_window_fullscreen_shortcut.shortcut_text()
                    or self._defaults.main_window_fullscreen_shortcut
                ),
                "main_window_hide_controls_in_fullscreen": self.main_window_hide_controls_in_fullscreen.isChecked(),
                "terminal_scrollback_lines": self.scrollback.value(),
                "terminal_log_dir": self.log_dir.text().strip() or self._defaults.terminal_log_dir,
                "global_session_logging_enabled": self.global_session_logging_enabled.isChecked(),
                "session_log_cleanup_enabled": self.session_log_cleanup_enabled.isChecked(),
                "session_log_retention_days": self.session_log_retention_days.value(),
                "web_server_log_cleanup_enabled": self.web_server_log_cleanup_enabled.isChecked(),
                "web_server_log_retention_days": self.web_server_log_retention_days.value(),
                "crash_logging_enabled": self.crash_logging_enabled.isChecked(),
                "terminal_cursor_blink": self.cursor_blink.isChecked(),
                "terminal_bell_enabled": self.terminal_bell.isChecked(),
                "terminal_visual_bell_enabled": self.terminal_visual_bell.isChecked(),
                "local_shell_command_override": self.local_shell_command_override.text().strip(),
                "local_shell_start_dir_mode": self._selected_local_shell_start_dir_mode(),
                "local_shell_custom_start_dir": self.local_shell_custom_start_dir.text().strip(),
                "secrets_backend": self._selected_backend(),
                "onepassword_vault": self.onepassword_vault.text().strip() or self._defaults.onepassword_vault,
                "onepassword_account": self.onepassword_account.text().strip(),
                "onepassword_cli_path": self.onepassword_cli_path.text().strip() or self._defaults.onepassword_cli_path,
                "bitwarden_cli_path": self.bitwarden_cli_path.text().strip() or self._defaults.bitwarden_cli_path,
                "keeper_cli_path": self.keeper_cli_path.text().strip() or self._defaults.keeper_cli_path,
                "keeper_user": self.keeper_user.text().strip(),
                "keeper_server": self.keeper_server.text().strip(),
                "keeper_folder": self.keeper_folder.text().strip() or self._defaults.keeper_folder,
                "keepass_cli_path": self.keepass_cli_path.text().strip() or self._defaults.keepass_cli_path,
                "keepass_database_path": self.keepass_database_path.text().strip(),
                "keepass_password_env": self.keepass_password_env.text().strip() or self._defaults.keepass_password_env,
                "keepass_key_file_path": self.keepass_key_file_path.text().strip(),
                "keepass_group": self.keepass_group.text().strip() or self._defaults.keepass_group,
                "vault_addr": self.vault_addr.text().strip(),
                "vault_mount": self.vault_mount.text().strip() or self._defaults.vault_mount,
                "vault_token_env": self.vault_token_env.text().strip() or self._defaults.vault_token_env,
                "vault_namespace": self.vault_namespace.text().strip(),
                "vault_skip_tls_verify": self.vault_skip_tls_verify.isChecked(),
                "warn_before_file_delete": self.warn_before_file_delete.isChecked(),
                "warn_before_file_overwrite": self.warn_before_file_overwrite.isChecked(),
                "warn_before_closing_active_tab": self.warn_before_closing_active_tab.isChecked(),
                "master_password_enabled": self.master_password_enabled.isChecked(),
                "master_password_tools_enabled": self.master_password_tools_enabled.isChecked(),
            }
        )
        settings = AppSettings.from_dict(payload)
        if self.master_password_clear.isChecked():
            settings.master_password_salt_b64 = ""
            settings.master_password_hash_b64 = ""
        new_password = self.master_password.text().strip()
        if new_password:
            MasterPasswordService.set_master_password(settings, new_password)
        settings.master_password_enabled = self.master_password_enabled.isChecked()
        if not MasterPasswordService.has_master_password(settings):
            settings.master_password_enabled = False
            settings.master_password_tools_enabled = False
        else:
            settings.master_password_tools_enabled = self.master_password_tools_enabled.isChecked()
        return settings

    def build_backend_auth_updates(self) -> dict[str, str | None]:
        updates: dict[str, str | None] = {}
        self._collect_secret_update(
            updates,
            "onepassword_service_token",
            self.onepassword_service_token,
            self.onepassword_service_token_clear,
        )
        self._collect_secret_update(
            updates,
            "bitwarden_session",
            self.bitwarden_session,
            self.bitwarden_session_clear,
        )
        self._collect_secret_update(
            updates,
            "keeper_master_password",
            self.keeper_master_password,
            self.keeper_master_password_clear,
        )
        self._collect_secret_update(
            updates,
            "keepass_master_password",
            self.keepass_master_password,
            self.keepass_master_password_clear,
        )
        self._collect_secret_update(
            updates,
            "vault_token",
            self.vault_token,
            self.vault_token_clear,
        )
        return updates

    @staticmethod
    def _collect_secret_update(
        updates: dict[str, str | None],
        key: str,
        field: QLineEdit,
        clear_checkbox: QCheckBox,
    ) -> None:
        if clear_checkbox.isChecked():
            updates[key] = None
            return
        value = field.text().strip()
        if value:
            updates[key] = value

    def _build_secret_input(self, *, has_existing: bool) -> tuple[QWidget, QLineEdit, QCheckBox]:
        container = QWidget(self)
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        field = QLineEdit("")
        field.setEchoMode(QLineEdit.Password)
        if has_existing:
            field.setPlaceholderText("Stored value exists; enter to replace.")
        else:
            field.setPlaceholderText("Optional")

        clear_checkbox = QCheckBox("Clear")
        clear_checkbox.setToolTip("Remove stored value from OS keyring.")
        clear_checkbox.setEnabled(has_existing)

        def _toggle_clear(checked: bool) -> None:
            field.setEnabled(not checked)
            if checked:
                field.clear()

        clear_checkbox.toggled.connect(_toggle_clear)
        layout.addWidget(field, 1)
        layout.addWidget(clear_checkbox, 0)
        return container, field, clear_checkbox

    def _build_color_input(self, initial: str) -> tuple[QWidget, QLineEdit]:
        container = QWidget(self)
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        field = QLineEdit(initial)
        swatch = QLabel("")
        swatch.setFixedSize(32, 20)
        swatch.setAlignment(Qt.AlignCenter)
        pick_btn = QPushButton("Pick")
        pick_btn.setFixedWidth(64)

        def refresh_swatch(value: str) -> None:
            color = QColor(value.strip())
            if color.isValid():
                swatch.setText("")
                swatch.setToolTip(color.name())
                swatch.setStyleSheet(
                    f"background-color: {color.name()}; border: 1px solid #6b7280; border-radius: 4px;"
                )
                return
            swatch.setText("?")
            swatch.setToolTip("Invalid color value")
            swatch.setStyleSheet(
                "background-color: transparent; border: 1px dashed #6b7280; border-radius: 4px; color: #9ca3af;"
            )

        def choose_color() -> None:
            original = field.text()
            current = QColor(original.strip())
            if not current.isValid():
                current = QColor("#ffffff")

            def preview_color(changed: QColor) -> None:
                if changed.isValid():
                    field.setText(changed.name())

            chosen = pick_color(
                self,
                title="Select Color",
                initial=current,
                on_preview=preview_color,
            )
            if chosen.isValid():
                field.setText(chosen.name())
            else:
                field.setText(original)

        field.textChanged.connect(refresh_swatch)
        pick_btn.clicked.connect(choose_color)
        refresh_swatch(initial)
        layout.addWidget(field, 1)
        layout.addWidget(swatch, 0)
        layout.addWidget(pick_btn, 0)
        return container, field

    def _bind_live_preview_controls(self) -> None:
        for field in self._theme_color_fields().values():
            field.textChanged.connect(self._auto_switch_theme_to_custom)
            field.textChanged.connect(self._emit_preview_settings)
        self.font_family.currentTextChanged.connect(self._emit_preview_settings)
        self.font_pt.valueChanged.connect(self._emit_preview_settings)
        self.terminal_classic_default_colors.toggled.connect(self._emit_preview_settings)

    def _emit_preview_settings(self, *_args) -> None:
        if self._on_preview_requested is None:
            return
        self._on_preview_requested(self._build_preview_settings())

    @staticmethod
    def _preview_color_value(raw: str, fallback: str) -> str:
        color = QColor(raw.strip())
        if color.isValid():
            return color.name()
        return fallback

    def _build_preview_settings(self) -> AppSettings:
        payload = self._base_settings.to_dict()
        payload.update(
            {
                "theme_name": self._selected_theme_name(),
                "app_bg_start": self._preview_color_value(
                    self.app_bg_start.text(),
                    str(payload["app_bg_start"]),
                ),
                "app_bg_end": self._preview_color_value(
                    self.app_bg_end.text(),
                    str(payload["app_bg_end"]),
                ),
                "text_color": self._preview_color_value(
                    self.text_color.text(),
                    str(payload["text_color"]),
                ),
                "field_bg": self._preview_color_value(
                    self.field_bg.text(),
                    str(payload["field_bg"]),
                ),
                "field_border": self._preview_color_value(
                    self.field_border.text(),
                    str(payload["field_border"]),
                ),
                "accent_color": self._preview_color_value(
                    self.accent_color.text(),
                    str(payload["accent_color"]),
                ),
                "accent_hover": self._preview_color_value(
                    self.accent_hover.text(),
                    str(payload["accent_hover"]),
                ),
                "accent_pressed": self._preview_color_value(
                    self.accent_pressed.text(),
                    str(payload["accent_pressed"]),
                ),
                "terminal_bg": self._preview_color_value(
                    self.terminal_bg.text(),
                    str(payload["terminal_bg"]),
                ),
                "terminal_fg": self._preview_color_value(
                    self.terminal_fg.text(),
                    str(payload["terminal_fg"]),
                ),
                "terminal_classic_default_colors": self.terminal_classic_default_colors.isChecked(),
                "tab_active_bg": self._preview_color_value(
                    self.tab_active_bg.text(),
                    str(payload["tab_active_bg"]),
                ),
                "tab_active_fg": self._preview_color_value(
                    self.tab_active_fg.text(),
                    str(payload["tab_active_fg"]),
                ),
                "tab_inactive_bg": self._preview_color_value(
                    self.tab_inactive_bg.text(),
                    str(payload["tab_inactive_bg"]),
                ),
                "tab_inactive_fg": self._preview_color_value(
                    self.tab_inactive_fg.text(),
                    str(payload["tab_inactive_fg"]),
                ),
                "terminal_font_family": self.font_family.currentText().strip() or str(payload["terminal_font_family"]),
                "terminal_font_pt": self.font_pt.value(),
            }
        )
        return AppSettings.from_dict(payload)

    def _build_directory_input(
        self,
        initial: str,
        *,
        dialog_title: str,
        on_open_requested: Callable[[str], None] | None = None,
    ) -> tuple[QWidget, QLineEdit]:
        container = QWidget(self)
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        field = QLineEdit(initial)
        browse_btn = QPushButton("Browse")
        browse_btn.setFixedWidth(80)
        open_btn: QPushButton | None = None
        if on_open_requested is not None:
            open_btn = QPushButton("Open Folder")

        def choose_directory() -> None:
            start = field.text().strip()
            selected = QFileDialog.getExistingDirectory(self, dialog_title, start or "")
            if selected:
                field.setText(selected)

        browse_btn.clicked.connect(choose_directory)
        if open_btn is not None:
            open_btn.clicked.connect(lambda: on_open_requested(field.text()))
        layout.addWidget(field, 1)
        layout.addWidget(browse_btn, 0)
        if open_btn is not None:
            layout.addWidget(open_btn, 0)
        return container, field

    @staticmethod
    def _terminal_font_families() -> list[str]:
        families = [
            name for name in QFontDatabase.families() if QFontDatabase.isFixedPitch(name) and name.lower() != "fixedsys"
        ]
        preferred = ["Courier New", "Consolas", "Cascadia Mono", "Cascadia Code", "Lucida Console", "Courier"]
        ordered: list[str] = []
        for name in preferred:
            if name in families and name not in ordered:
                ordered.append(name)
        for name in families:
            if name not in ordered:
                ordered.append(name)
        return ordered or ["Courier New"]

    def _set_font_selection(self, requested_family: str) -> None:
        target = requested_family.strip().lower()
        if target:
            for idx, family in enumerate(self._font_families):
                if family.lower() == target:
                    self.font_family.setCurrentIndex(idx)
                    return
        # Default to Courier New when available.
        for idx, family in enumerate(self._font_families):
            if family.lower() == "courier new":
                self.font_family.setCurrentIndex(idx)
                return
        self.font_family.setCurrentIndex(0)

    def apply_settings(self, settings: AppSettings) -> None:
        self._base_settings = AppSettings.from_dict(settings.to_dict())
        self._suppress_custom_theme_auto_switch = True
        try:
            self.app_bg_start.setText(settings.app_bg_start)
            self.app_bg_end.setText(settings.app_bg_end)
            self.text_color.setText(settings.text_color)
            self.field_bg.setText(settings.field_bg)
            self.field_border.setText(settings.field_border)
            self.accent_color.setText(settings.accent_color)
            self.accent_hover.setText(settings.accent_hover)
            self.accent_pressed.setText(settings.accent_pressed)
            self.terminal_bg.setText(settings.terminal_bg)
            self.terminal_fg.setText(settings.terminal_fg)
            self.terminal_classic_default_colors.setChecked(settings.terminal_classic_default_colors)
            self.tab_active_bg.setText(settings.tab_active_bg)
            self.tab_active_fg.setText(settings.tab_active_fg)
            self.tab_inactive_bg.setText(settings.tab_inactive_bg)
            self.tab_inactive_fg.setText(settings.tab_inactive_fg)
        finally:
            self._suppress_custom_theme_auto_switch = False
        self._set_theme_selection(self._theme_name_for_settings(settings))
        self._set_font_selection(settings.terminal_font_family)
        self.font_pt.setValue(settings.terminal_font_pt)
        self.main_window_fullscreen_shortcut.set_shortcut_text(
            settings.main_window_fullscreen_shortcut or self._defaults.main_window_fullscreen_shortcut
        )
        self.main_window_hide_controls_in_fullscreen.setChecked(settings.main_window_hide_controls_in_fullscreen)
        self.scrollback.setValue(settings.terminal_scrollback_lines)
        self._update_scrollback_estimate()
        self.log_dir.setText(settings.terminal_log_dir)
        self.global_session_logging_enabled.setChecked(settings.global_session_logging_enabled)
        self.session_log_cleanup_enabled.setChecked(settings.session_log_cleanup_enabled)
        self.session_log_retention_days.setValue(settings.session_log_retention_days)
        self.web_server_log_cleanup_enabled.setChecked(settings.web_server_log_cleanup_enabled)
        self.web_server_log_retention_days.setValue(settings.web_server_log_retention_days)
        self.crash_logging_enabled.setChecked(settings.crash_logging_enabled)
        self._update_session_log_cleanup_controls()
        self._update_web_server_log_cleanup_controls()
        self.cursor_blink.setChecked(settings.terminal_cursor_blink)
        self.terminal_bell.setChecked(settings.terminal_bell_enabled)
        self.terminal_visual_bell.setChecked(settings.terminal_visual_bell_enabled)
        self.local_shell_command_override.setText(settings.local_shell_command_override)
        self._set_local_shell_start_dir_mode(settings.local_shell_start_dir_mode)
        self.local_shell_custom_start_dir.setText(settings.local_shell_custom_start_dir)
        self.warn_before_file_delete.setChecked(settings.warn_before_file_delete)
        self.warn_before_file_overwrite.setChecked(settings.warn_before_file_overwrite)
        self.warn_before_closing_active_tab.setChecked(settings.warn_before_closing_active_tab)
        self._has_existing_master_password = MasterPasswordService.has_master_password(settings)
        self.master_password_enabled.setChecked(
            settings.master_password_enabled and self._has_existing_master_password
        )
        self.master_password_tools_enabled.setChecked(
            settings.master_password_tools_enabled and self._has_existing_master_password
        )
        self.master_password.clear()
        self.master_password_confirm.clear()
        self.master_password_clear.setChecked(False)
        self.master_password_clear.setEnabled(self._has_existing_master_password)

        self._set_backend_selection(settings.secrets_backend)
        self.onepassword_vault.setText(settings.onepassword_vault)
        self.onepassword_account.setText(settings.onepassword_account)
        self.onepassword_cli_path.setText(settings.onepassword_cli_path)
        self.bitwarden_cli_path.setText(settings.bitwarden_cli_path)
        self.keeper_cli_path.setText(settings.keeper_cli_path)
        self.keeper_user.setText(settings.keeper_user)
        self.keeper_server.setText(settings.keeper_server)
        self.keeper_folder.setText(settings.keeper_folder)
        self.keepass_cli_path.setText(settings.keepass_cli_path)
        self.keepass_database_path.setText(settings.keepass_database_path)
        self.keepass_password_env.setText(settings.keepass_password_env)
        self.keepass_key_file_path.setText(settings.keepass_key_file_path)
        self.keepass_group.setText(settings.keepass_group)
        self.vault_addr.setText(settings.vault_addr)
        self.vault_mount.setText(settings.vault_mount)
        self.vault_token_env.setText(settings.vault_token_env)
        self.vault_namespace.setText(settings.vault_namespace)
        self.vault_skip_tls_verify.setChecked(settings.vault_skip_tls_verify)
        self.onepassword_service_token.clear()
        self.onepassword_service_token_clear.setChecked(False)
        self.bitwarden_session.clear()
        self.bitwarden_session_clear.setChecked(False)
        self.keeper_master_password.clear()
        self.keeper_master_password_clear.setChecked(False)
        self.keepass_master_password.clear()
        self.keepass_master_password_clear.setChecked(False)
        self.vault_token.clear()
        self.vault_token_clear.setChecked(False)
        self._update_master_password_controls()
        self._update_local_shell_controls()
        self._update_secrets_rows()

    def _validate_and_accept(self) -> None:
        new_password = self.master_password.text().strip()
        confirm_password = self.master_password_confirm.text().strip()
        if new_password or confirm_password:
            if not new_password:
                QMessageBox.warning(self, "Master Password", "Enter a new master password.")
                return
            if new_password != confirm_password:
                QMessageBox.warning(self, "Master Password", "Master password and confirmation do not match.")
                return
        has_existing_after_save = self._has_existing_master_password and not self.master_password_clear.isChecked()
        if (
            (self.master_password_enabled.isChecked() or self.master_password_tools_enabled.isChecked())
            and not (has_existing_after_save or new_password)
        ):
            QMessageBox.warning(
                self,
                "Master Password",
                "Master password protection is enabled, but no password is set.\nSet a password or disable the toggle.",
            )
            return
        self.accept()

    def _update_master_password_controls(self) -> None:
        clearing = self.master_password_clear.isChecked()
        if clearing and self.master_password_enabled.isChecked():
            self.master_password_enabled.blockSignals(True)
            self.master_password_enabled.setChecked(False)
            self.master_password_enabled.blockSignals(False)
        if clearing and self.master_password_tools_enabled.isChecked():
            self.master_password_tools_enabled.blockSignals(True)
            self.master_password_tools_enabled.setChecked(False)
            self.master_password_tools_enabled.blockSignals(False)
        self.master_password_enabled.setEnabled(not clearing)
        new_password_pending = bool(self.master_password.text().strip())
        self.master_password_tools_enabled.setEnabled(
            not clearing and (self._has_existing_master_password or new_password_pending)
        )
        enabled = self.master_password_enabled.isChecked()
        tools_enabled = self.master_password_tools_enabled.isChecked()
        has_existing = self._has_existing_master_password and not clearing
        self.master_password.setEnabled(not clearing)
        self.master_password_confirm.setEnabled(not clearing)
        if has_existing:
            self.master_password.setPlaceholderText("Leave blank to keep current password.")
            self.master_password_confirm.setPlaceholderText("Repeat new password to change it.")
        elif clearing:
            self.master_password.setPlaceholderText("Password will be cleared when saved.")
            self.master_password_confirm.setPlaceholderText("Password will be cleared when saved.")
        else:
            self.master_password.setPlaceholderText("Set a master password.")
            self.master_password_confirm.setPlaceholderText("Type the same password again.")
        if clearing:
            self.master_password_hint.setText(
                "Stored master password will be removed when you save settings."
            )
        elif (enabled or tools_enabled) and has_existing:
            self.master_password_hint.setText(
                "SnakeSh will ask for this password before opening the selected protected surfaces."
            )
        elif enabled:
            self.master_password_hint.setText(
                "Set a new password and confirm it to enable startup protection."
            )
        else:
            self.master_password_hint.setText(
                "Startup protection is disabled."
            )
        QTimer.singleShot(0, self._sync_wrapped_hint_heights)
