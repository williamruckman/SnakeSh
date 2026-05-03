from __future__ import annotations

from collections.abc import Callable
import glob
import platform
from pathlib import Path

from PySide6.QtCore import Qt, QSettings
from PySide6.QtGui import QCloseEvent, QColor, QIcon
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from snakesh.core.models import (
    DEFAULT_SFTP_LOCAL_FOLDER,
    DEFAULT_SFTP_REMOTE_FOLDER,
    Protocol,
    Session,
    SSHAutomationStep,
    SSHDynamicTunnel,
    SSHStaticTunnel,
    normalize_nomachine_quality,
    normalize_nomachine_resize_mode,
    normalize_rdp_audio_mode,
    normalize_remote_launch_mode,
    normalize_serial_data_bits,
    normalize_serial_flow_control,
    normalize_serial_parity,
    normalize_serial_stop_bits,
    normalize_serial_terminal_type,
    parse_resolution,
    is_auto_resolution,
)
from snakesh.services.session_service import SessionService
from snakesh.ui.color_picker import pick_color

_DISPLAY_RESOLUTION_PRESETS: tuple[str, ...] = (
    "Default",
    "Auto",
    "1024x768",
    "1280x720",
    "1280x800",
    "1366x768",
    "1440x900",
    "1600x900",
    "1920x1080",
    "2560x1440",
)

_DISPLAY_COLOR_CHOICES: tuple[tuple[str, int], ...] = (
    ("Default", 0),
    ("8-bit", 8),
    ("16-bit", 16),
    ("24-bit", 24),
    ("32-bit", 32),
)

_RDP_AUDIO_CHOICES: tuple[tuple[str, str], ...] = (
    ("On this computer", "local"),
    ("On remote computer", "remote"),
    ("Do not play", "mute"),
)

_REMOTE_LAUNCH_CHOICES: tuple[tuple[str, str], ...] = (
    ("Open in tab", "tab"),
    ("Open detached window", "detached"),
)

_NOMACHINE_RESIZE_MODE_CHOICES: tuple[tuple[str, str], ...] = (
    ("Scale to fit", "scaled"),
    ("Viewport (scroll)", "viewport"),
)

_NOMACHINE_QUALITY_CHOICES: tuple[tuple[str, int], ...] = (
    ("0 - Lowest (fastest)", 0),
    ("1 - Very low", 1),
    ("2 - Low", 2),
    ("3 - Lower-medium", 3),
    ("4 - Medium-low", 4),
    ("5 - Balanced (default)", 5),
    ("6 - Medium-high", 6),
    ("7 - High", 7),
    ("8 - Very high", 8),
    ("9 - Highest (best image)", 9),
)

_TELNET_TERMINAL_TYPE_CHOICES: tuple[str, ...] = (
    "xterm-256color",
    "xterm",
    "vt100",
    "ansi",
)

_SERIAL_TERMINAL_TYPE_CHOICES: tuple[str, ...] = (
    "auto",
    "vt100",
    "ansi",
    "xterm",
    "xterm-256color",
)

_SERIAL_BAUD_CHOICES: tuple[int, ...] = (
    300,
    1200,
    2400,
    4800,
    9600,
    19200,
    38400,
    57600,
    115200,
    230400,
)

_SERIAL_DATA_BITS_CHOICES: tuple[tuple[str, int], ...] = (
    ("5 bits", 5),
    ("6 bits", 6),
    ("7 bits", 7),
    ("8 bits", 8),
)

_SERIAL_PARITY_CHOICES: tuple[tuple[str, str], ...] = (
    ("None", "none"),
    ("Even", "even"),
    ("Odd", "odd"),
    ("Mark", "mark"),
    ("Space", "space"),
)

_SERIAL_STOP_BITS_CHOICES: tuple[tuple[str, str], ...] = (
    ("1", "1"),
    ("1.5", "1.5"),
    ("2", "2"),
)

_SERIAL_FLOW_CONTROL_CHOICES: tuple[tuple[str, str], ...] = (
    ("None", "none"),
    ("RTS/CTS (Hardware)", "rtscts"),
    ("XON/XOFF (Software)", "xonxoff"),
    ("DSR/DTR", "dsrdtr"),
)


class DynamicTunnelEditorDialog(QDialog):
    def __init__(self, parent=None, tunnel: SSHDynamicTunnel | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Dynamic Tunnel")
        self._tunnel = tunnel

        self.bind_host_input = QLineEdit()
        self.bind_port_input = QSpinBox()
        self.bind_port_input.setRange(1, 65535)
        self.enabled_input = QCheckBox("Enabled")

        form = QFormLayout()
        form.addRow("Bind Host", self.bind_host_input)
        form.addRow("Bind Port", self.bind_port_input)
        form.addRow("", self.enabled_input)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        root = QVBoxLayout(self)
        root.addLayout(form)
        root.addWidget(buttons)

        self._populate()

    def _populate(self) -> None:
        if self._tunnel is None:
            self.bind_host_input.setText("127.0.0.1")
            self.bind_port_input.setValue(1080)
            self.enabled_input.setChecked(True)
            return
        self.bind_host_input.setText(self._tunnel.bind_host)
        self.bind_port_input.setValue(self._tunnel.bind_port)
        self.enabled_input.setChecked(self._tunnel.enabled)

    def build_tunnel(self) -> SSHDynamicTunnel:
        bind_host = self.bind_host_input.text().strip() or "127.0.0.1"
        return SSHDynamicTunnel(
            bind_host=bind_host,
            bind_port=self.bind_port_input.value(),
            enabled=self.enabled_input.isChecked(),
        )


class StaticTunnelEditorDialog(QDialog):
    def __init__(self, parent=None, tunnel: SSHStaticTunnel | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Static Tunnel")
        self._tunnel = tunnel

        self.direction_input = QComboBox()
        self.direction_input.addItem("Local Forward", "local")
        self.direction_input.addItem("Remote Forward", "remote")
        self.bind_host_input = QLineEdit()
        self.bind_port_input = QSpinBox()
        self.bind_port_input.setRange(1, 65535)
        self.target_host_input = QLineEdit()
        self.target_port_input = QSpinBox()
        self.target_port_input.setRange(1, 65535)
        self.enabled_input = QCheckBox("Enabled")

        form = QFormLayout()
        form.addRow("Direction", self.direction_input)
        form.addRow("Bind Host", self.bind_host_input)
        form.addRow("Bind Port", self.bind_port_input)
        form.addRow("Target Host", self.target_host_input)
        form.addRow("Target Port", self.target_port_input)
        form.addRow("", self.enabled_input)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        root = QVBoxLayout(self)
        root.addLayout(form)
        root.addWidget(buttons)

        self._populate()

    def _populate(self) -> None:
        if self._tunnel is None:
            self.direction_input.setCurrentIndex(0)
            self.bind_host_input.setText("127.0.0.1")
            self.bind_port_input.setValue(8080)
            self.target_host_input.setText("127.0.0.1")
            self.target_port_input.setValue(80)
            self.enabled_input.setChecked(True)
            return

        direction_index = self.direction_input.findData(self._tunnel.direction)
        self.direction_input.setCurrentIndex(max(0, direction_index))
        self.bind_host_input.setText(self._tunnel.bind_host)
        self.bind_port_input.setValue(self._tunnel.bind_port if self._tunnel.bind_port > 0 else 8080)
        self.target_host_input.setText(self._tunnel.target_host)
        self.target_port_input.setValue(self._tunnel.target_port if self._tunnel.target_port > 0 else 80)
        self.enabled_input.setChecked(self._tunnel.enabled)

    def build_tunnel(self) -> SSHStaticTunnel:
        bind_host = self.bind_host_input.text().strip() or "127.0.0.1"
        target_host = self.target_host_input.text().strip() or "127.0.0.1"
        direction_data = self.direction_input.currentData()
        direction = str(direction_data) if isinstance(direction_data, str) else "local"
        return SSHStaticTunnel(
            direction=direction,
            bind_host=bind_host,
            bind_port=self.bind_port_input.value(),
            target_host=target_host,
            target_port=self.target_port_input.value(),
            enabled=self.enabled_input.isChecked(),
        )


class SSHTunnelingDialog(QDialog):
    def __init__(
        self,
        parent=None,
        dynamic_tunnels: list[SSHDynamicTunnel] | None = None,
        static_tunnels: list[SSHStaticTunnel] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("SSH Tunneling")
        self.resize(760, 460)

        self._dynamic_tunnels = list(dynamic_tunnels or [])
        self._static_tunnels = list(static_tunnels or [])

        tabs = QTabWidget()
        tabs.addTab(self._build_dynamic_page(), "Dynamic (SOCKS)")
        tabs.addTab(self._build_static_page(), "Static")

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        root = QVBoxLayout(self)
        root.addWidget(tabs)
        root.addWidget(buttons)
        self._refresh_dynamic_list()
        self._refresh_static_list()

    def _build_dynamic_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("Create SOCKS proxy listeners through this SSH session."))
        self.dynamic_list = QListWidget()
        layout.addWidget(self.dynamic_list)
        controls = QHBoxLayout()
        add_btn = QPushButton("Add")
        edit_btn = QPushButton("Edit")
        remove_btn = QPushButton("Remove")
        add_btn.clicked.connect(self._add_dynamic)
        edit_btn.clicked.connect(self._edit_dynamic)
        remove_btn.clicked.connect(self._remove_dynamic)
        controls.addWidget(add_btn)
        controls.addWidget(edit_btn)
        controls.addWidget(remove_btn)
        controls.addStretch(1)
        layout.addLayout(controls)
        return page

    def _build_static_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("Create local or remote TCP port forwards through this SSH session."))
        self.static_list = QListWidget()
        layout.addWidget(self.static_list)
        controls = QHBoxLayout()
        add_btn = QPushButton("Add")
        edit_btn = QPushButton("Edit")
        remove_btn = QPushButton("Remove")
        add_btn.clicked.connect(self._add_static)
        edit_btn.clicked.connect(self._edit_static)
        remove_btn.clicked.connect(self._remove_static)
        controls.addWidget(add_btn)
        controls.addWidget(edit_btn)
        controls.addWidget(remove_btn)
        controls.addStretch(1)
        layout.addLayout(controls)
        return page

    def _refresh_dynamic_list(self) -> None:
        self.dynamic_list.clear()
        for tunnel in self._dynamic_tunnels:
            state = "On" if tunnel.enabled else "Off"
            label = f"[{state}] SOCKS {tunnel.bind_host}:{tunnel.bind_port}"
            self.dynamic_list.addItem(QListWidgetItem(label))

    def _refresh_static_list(self) -> None:
        self.static_list.clear()
        for tunnel in self._static_tunnels:
            state = "On" if tunnel.enabled else "Off"
            direction = "Local" if tunnel.direction == "local" else "Remote"
            label = (
                f"[{state}] {direction} {tunnel.bind_host}:{tunnel.bind_port} "
                f"-> {tunnel.target_host}:{tunnel.target_port}"
            )
            self.static_list.addItem(QListWidgetItem(label))

    def _add_dynamic(self) -> None:
        dialog = DynamicTunnelEditorDialog(self)
        if not dialog.exec():
            return
        self._dynamic_tunnels.append(dialog.build_tunnel())
        self._refresh_dynamic_list()

    def _edit_dynamic(self) -> None:
        index = self.dynamic_list.currentRow()
        if index < 0 or index >= len(self._dynamic_tunnels):
            return
        dialog = DynamicTunnelEditorDialog(self, self._dynamic_tunnels[index])
        if not dialog.exec():
            return
        self._dynamic_tunnels[index] = dialog.build_tunnel()
        self._refresh_dynamic_list()
        self.dynamic_list.setCurrentRow(index)

    def _remove_dynamic(self) -> None:
        index = self.dynamic_list.currentRow()
        if index < 0 or index >= len(self._dynamic_tunnels):
            return
        self._dynamic_tunnels.pop(index)
        self._refresh_dynamic_list()

    def _add_static(self) -> None:
        dialog = StaticTunnelEditorDialog(self)
        if not dialog.exec():
            return
        self._static_tunnels.append(dialog.build_tunnel())
        self._refresh_static_list()

    def _edit_static(self) -> None:
        index = self.static_list.currentRow()
        if index < 0 or index >= len(self._static_tunnels):
            return
        dialog = StaticTunnelEditorDialog(self, self._static_tunnels[index])
        if not dialog.exec():
            return
        self._static_tunnels[index] = dialog.build_tunnel()
        self._refresh_static_list()
        self.static_list.setCurrentRow(index)

    def _remove_static(self) -> None:
        index = self.static_list.currentRow()
        if index < 0 or index >= len(self._static_tunnels):
            return
        self._static_tunnels.pop(index)
        self._refresh_static_list()

    def dynamic_tunnels(self) -> list[SSHDynamicTunnel]:
        return list(self._dynamic_tunnels)

    def static_tunnels(self) -> list[SSHStaticTunnel]:
        return list(self._static_tunnels)


class AutomationStepEditorDialog(QDialog):
    def __init__(self, parent=None, step: SSHAutomationStep | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Automation Step")
        self._step = step

        self.step_type_input = QComboBox()
        self.step_type_input.addItem("Command", "command")
        self.step_type_input.addItem("Sleep", "sleep")
        self.step_type_input.addItem("Expect", "expect")

        self.command_input = QLineEdit()
        self.sleep_input = QDoubleSpinBox()
        self.sleep_input.setRange(0.0, 86400.0)
        self.sleep_input.setDecimals(1)
        self.sleep_input.setSingleStep(0.5)
        self.sleep_input.setValue(1.0)

        self.expect_text_input = QLineEdit()
        self.expect_timeout_input = QDoubleSpinBox()
        self.expect_timeout_input.setRange(0.1, 86400.0)
        self.expect_timeout_input.setDecimals(1)
        self.expect_timeout_input.setSingleStep(0.5)
        self.expect_timeout_input.setValue(15.0)
        self.expect_timeout_action_input = QComboBox()
        self.expect_timeout_action_input.addItem("Terminate Script", "terminate")
        self.expect_timeout_action_input.addItem("Continue Script", "continue")

        form = QFormLayout()
        self._form = form
        form.addRow("Step Type", self.step_type_input)

        self._command_label = QLabel("Command")
        form.addRow(self._command_label, self.command_input)

        self._sleep_label = QLabel("Seconds")
        form.addRow(self._sleep_label, self.sleep_input)

        self._expect_text_label = QLabel("Expect Text")
        form.addRow(self._expect_text_label, self.expect_text_input)

        self._expect_timeout_label = QLabel("Timeout (s)")
        form.addRow(self._expect_timeout_label, self.expect_timeout_input)

        self._expect_action_label = QLabel("On Timeout")
        form.addRow(self._expect_action_label, self.expect_timeout_action_input)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._handle_accept)
        buttons.rejected.connect(self.reject)

        root = QVBoxLayout(self)
        root.addLayout(form)
        root.addWidget(buttons)

        self.step_type_input.currentIndexChanged.connect(self._update_step_type_fields)
        self._populate()
        self._update_step_type_fields()

    def _populate(self) -> None:
        step = self._step or SSHAutomationStep()
        index = self.step_type_input.findData(step.step_type)
        self.step_type_input.setCurrentIndex(max(0, index))
        self.command_input.setText(step.command)
        self.sleep_input.setValue(step.sleep_seconds)
        self.expect_text_input.setText(step.expect_text)
        self.expect_timeout_input.setValue(step.expect_timeout_seconds)
        timeout_index = self.expect_timeout_action_input.findData(step.expect_on_timeout)
        self.expect_timeout_action_input.setCurrentIndex(max(0, timeout_index))

    def _update_step_type_fields(self) -> None:
        current = self.step_type_input.currentData()
        step_type = str(current) if isinstance(current, str) else "command"
        show_command = step_type == "command"
        show_sleep = step_type == "sleep"
        show_expect = step_type == "expect"

        self._command_label.setVisible(show_command)
        self.command_input.setVisible(show_command)

        self._sleep_label.setVisible(show_sleep)
        self.sleep_input.setVisible(show_sleep)

        self._expect_text_label.setVisible(show_expect)
        self.expect_text_input.setVisible(show_expect)
        self._expect_timeout_label.setVisible(show_expect)
        self.expect_timeout_input.setVisible(show_expect)
        self._expect_action_label.setVisible(show_expect)
        self.expect_timeout_action_input.setVisible(show_expect)

    def _handle_accept(self) -> None:
        step = self.build_step()
        if step.step_type == "command" and not step.command.strip():
            QMessageBox.warning(self, "Invalid Step", "Command cannot be empty.")
            return
        if step.step_type == "expect" and not step.expect_text:
            QMessageBox.warning(self, "Invalid Step", "Expect text cannot be empty.")
            return
        self.accept()

    def build_step(self) -> SSHAutomationStep:
        current = self.step_type_input.currentData()
        step_type = str(current) if isinstance(current, str) else "command"
        timeout_action_data = self.expect_timeout_action_input.currentData()
        timeout_action = str(timeout_action_data) if isinstance(timeout_action_data, str) else "terminate"
        return SSHAutomationStep(
            step_type=step_type,
            command=self.command_input.text(),
            sleep_seconds=self.sleep_input.value(),
            expect_text=self.expect_text_input.text(),
            expect_timeout_seconds=self.expect_timeout_input.value(),
            expect_on_timeout=timeout_action,
        )


class AutomatedScriptingDialog(QDialog):
    def __init__(self, parent=None, steps: list[SSHAutomationStep] | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Automated Scripting")
        self.resize(820, 520)
        self._steps = [
            SSHAutomationStep.from_dict(step.to_dict())
            for step in (steps or [])
        ]

        root = QVBoxLayout(self)
        root.addWidget(QLabel("Build the script that runs after shell login."))
        root.addWidget(QLabel("Supported steps: Command, Sleep, and Expect."))

        self.steps_list = QListWidget()
        root.addWidget(self.steps_list, 1)

        controls = QHBoxLayout()
        add_btn = QPushButton("Add")
        edit_btn = QPushButton("Edit")
        remove_btn = QPushButton("Remove")
        up_btn = QPushButton("Move Up")
        down_btn = QPushButton("Move Down")
        add_btn.clicked.connect(self._add_step)
        edit_btn.clicked.connect(self._edit_step)
        remove_btn.clicked.connect(self._remove_step)
        up_btn.clicked.connect(self._move_step_up)
        down_btn.clicked.connect(self._move_step_down)
        controls.addWidget(add_btn)
        controls.addWidget(edit_btn)
        controls.addWidget(remove_btn)
        controls.addWidget(up_btn)
        controls.addWidget(down_btn)
        controls.addStretch(1)
        root.addLayout(controls)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._refresh_steps()

    def _refresh_steps(self) -> None:
        self.steps_list.clear()
        for idx, step in enumerate(self._steps, start=1):
            self.steps_list.addItem(QListWidgetItem(self._format_step_label(idx, step)))

    def _add_step(self) -> None:
        dialog = AutomationStepEditorDialog(self)
        if not dialog.exec():
            return
        self._steps.append(dialog.build_step())
        self._refresh_steps()
        self.steps_list.setCurrentRow(len(self._steps) - 1)

    def _edit_step(self) -> None:
        index = self.steps_list.currentRow()
        if index < 0 or index >= len(self._steps):
            return
        dialog = AutomationStepEditorDialog(self, self._steps[index])
        if not dialog.exec():
            return
        self._steps[index] = dialog.build_step()
        self._refresh_steps()
        self.steps_list.setCurrentRow(index)

    def _remove_step(self) -> None:
        index = self.steps_list.currentRow()
        if index < 0 or index >= len(self._steps):
            return
        self._steps.pop(index)
        self._refresh_steps()
        if self._steps:
            self.steps_list.setCurrentRow(min(index, len(self._steps) - 1))

    def _move_step_up(self) -> None:
        index = self.steps_list.currentRow()
        if index <= 0 or index >= len(self._steps):
            return
        self._steps[index - 1], self._steps[index] = self._steps[index], self._steps[index - 1]
        self._refresh_steps()
        self.steps_list.setCurrentRow(index - 1)

    def _move_step_down(self) -> None:
        index = self.steps_list.currentRow()
        if index < 0 or index >= len(self._steps) - 1:
            return
        self._steps[index + 1], self._steps[index] = self._steps[index], self._steps[index + 1]
        self._refresh_steps()
        self.steps_list.setCurrentRow(index + 1)

    def steps(self) -> list[SSHAutomationStep]:
        return [
            SSHAutomationStep.from_dict(step.to_dict())
            for step in self._steps
        ]

    @staticmethod
    def _format_step_label(index: int, step: SSHAutomationStep) -> str:
        if step.step_type == "sleep":
            return f"{index:02d}. Sleep {step.sleep_seconds:g}s"
        if step.step_type == "expect":
            action = "continue" if step.expect_on_timeout == "continue" else "terminate"
            preview = step.expect_text.strip() or "(empty)"
            if len(preview) > 56:
                preview = f"{preview[:53]}..."
            return f"{index:02d}. Expect '{preview}' within {step.expect_timeout_seconds:g}s ({action} on timeout)"
        preview = step.command.strip() or "(empty command)"
        if len(preview) > 56:
            preview = f"{preview[:53]}..."
        return f"{index:02d}. Command: {preview}"


class SessionEditorDialog(QDialog):
    def __init__(
        self,
        parent=None,
        session: Session | None = None,
        password_loader: Callable[[Session], str | None] | None = None,
        quick_connect: bool = False,
    ) -> None:
        super().__init__(parent)
        self._quick_connect = bool(quick_connect)
        self.setWindowTitle("Quick Connect" if self._quick_connect else "Session Editor")
        self.resize(760, 800)
        self.setMinimumSize(600, 400)
        self._session = session
        self._password_loader = password_loader
        self._saved_password_loaded = False
        self._ssh_dynamic_tunnels: list[SSHDynamicTunnel] = []
        self._ssh_static_tunnels: list[SSHStaticTunnel] = []
        self._ssh_automation_steps: list[SSHAutomationStep] = []

        self.name_input = QLineEdit()
        self.host_input = QLineEdit()
        self.user_input = QLineEdit()
        self.domain_input = QLineEdit()
        self.port_input = QSpinBox()
        self.port_input.setMaximum(65535)

        self.protocol_input = QComboBox()
        self.protocol_input.addItems([p.value.upper() for p in Protocol])

        self.folder_input = QLineEdit()
        self.tags_input = QLineEdit()
        self.notes_input = QTextEdit()
        self.sftp_local_row, self.sftp_local_folder_input = self._build_directory_input(
            "Select Default Local SFTP Folder"
        )
        self.sftp_local_folder_input.setPlaceholderText(DEFAULT_SFTP_LOCAL_FOLDER)
        self.sftp_local_folder_input.setText(DEFAULT_SFTP_LOCAL_FOLDER)
        self.sftp_remote_folder_input = QLineEdit()
        self.sftp_remote_folder_input.setPlaceholderText(DEFAULT_SFTP_REMOTE_FOLDER)
        self.sftp_remote_folder_input.setText(DEFAULT_SFTP_REMOTE_FOLDER)
        self.key_auth_input = QCheckBox("Use key auth")
        self.key_auth_input.setChecked(True)
        self.private_key_row, self.private_key_input = self._build_file_input(
            "Select Private Key",
            "Private Keys (*.pem *.key *.ppk id_*);;All Files (*)",
        )
        self.public_key_row, self.public_key_input = self._build_file_input(
            "Select Public Key",
            "Public Keys (*.pub);;All Files (*)",
        )
        self.save_password_input = QCheckBox("Save password in OS keyring")
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.Password)
        self.password_row = QWidget(self)
        password_row_layout = QHBoxLayout(self.password_row)
        password_row_layout.setContentsMargins(0, 0, 0, 0)
        password_row_layout.setSpacing(6)
        self.password_visibility_btn = QPushButton("Show")
        self.password_visibility_btn.setCheckable(True)
        eye_icon = QIcon.fromTheme("view-password")
        if not eye_icon.isNull():
            self.password_visibility_btn.setIcon(eye_icon)
        self.password_visibility_btn.setToolTip("Show password")
        self.password_visibility_btn.toggled.connect(self._set_password_visibility)
        password_row_layout.addWidget(self.password_input, 1)
        password_row_layout.addWidget(self.password_visibility_btn, 0)
        self.password_input.setPlaceholderText("Optional")
        self.terminal_color_override_input = QCheckBox("Override global terminal colors for this session")
        self.terminal_bg_color_widget, self.terminal_bg_color_input = self._build_color_input("#000000")
        self.terminal_fg_color_widget, self.terminal_fg_color_input = self._build_color_input("#ffffff")
        self.terminal_bg_color_input.setPlaceholderText("#000000")
        self.terminal_fg_color_input.setPlaceholderText("#ffffff")
        self.terminal_color_preview = QTextEdit()
        self.terminal_color_preview.setReadOnly(True)
        self.terminal_color_preview.setLineWrapMode(QTextEdit.NoWrap)
        self.terminal_color_preview.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.terminal_color_preview.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.terminal_color_preview.setFixedHeight(86)
        self.terminal_color_preview.setPlainText("user@host:~$ echo Session Preview\nSession Preview")
        self.shell_banner_message_input = QLineEdit()
        self.shell_banner_message_input.setPlaceholderText("Optional warning or banner message")
        self.shell_banner_color_widget, self.shell_banner_color_input = self._build_color_input("#f59e0b")
        self.shell_banner_color_input.setPlaceholderText("#f59e0b")
        self.shell_banner_blink_input = QCheckBox("Blink shell banner while connected")
        self.x11_input = QCheckBox("Enable X11 forwarding (SSH/SFTP)")
        self.ssh_legacy_compatibility_input = QCheckBox("Use legacy SSH compatibility mode (older endpoints)")
        self.ssh_keepalive_input = QCheckBox("Enable SSH keepalive packets (30s interval)")
        self.ssh_automation_enabled_input = QCheckBox(
            "Enable automated scripting after connection (SSH/Telnet/Serial)"
        )
        self.ssh_automation_button = QPushButton("Automated Scripting...")
        self.ssh_automation_summary = QLabel()
        self.ssh_tunneling_button = QPushButton("SSH Tunneling...")
        self.ssh_tunnel_summary = QLabel()
        self.display_resolution_input = QComboBox()
        self.display_resolution_input.setEditable(True)
        self.display_resolution_input.addItems(_DISPLAY_RESOLUTION_PRESETS)
        self.display_fullscreen_input = QCheckBox("Launch in fullscreen")
        self.vnc_allow_resize_input = QCheckBox(
            "Allow dynamic remote resize (may be unsupported by some VNC servers)"
        )
        self.display_color_input = QComboBox()
        for label, depth in _DISPLAY_COLOR_CHOICES:
            self.display_color_input.addItem(label, depth)
        self.rdp_audio_mode_input = QComboBox()
        for label, mode in _RDP_AUDIO_CHOICES:
            self.rdp_audio_mode_input.addItem(label, mode)
        self.remote_launch_mode_input = QComboBox()
        for label, mode in _REMOTE_LAUNCH_CHOICES:
            self.remote_launch_mode_input.addItem(label, mode)
        self.nomachine_audio_enabled_input = QCheckBox("Enable audio streaming")
        self.nomachine_mute_remote_audio_input = QCheckBox("Mute audio on remote computer while connected")
        self.nomachine_auto_resize_input = QCheckBox("Auto-resize physical desktop")
        self.nomachine_resize_mode_input = QComboBox()
        for label, mode in _NOMACHINE_RESIZE_MODE_CHOICES:
            self.nomachine_resize_mode_input.addItem(label, mode)
        self.nomachine_link_quality_input = QComboBox()
        self.nomachine_video_quality_input = QComboBox()
        for label, quality in _NOMACHINE_QUALITY_CHOICES:
            self.nomachine_link_quality_input.addItem(label, quality)
            self.nomachine_video_quality_input.addItem(label, quality)
        quality_tip = "NoMachine quality scale: 0 = lowest/fastest, 9 = highest image quality."
        self.nomachine_link_quality_input.setToolTip(quality_tip)
        self.nomachine_video_quality_input.setToolTip(quality_tip)

        self.telnet_terminal_type_input = QComboBox()
        self.telnet_terminal_type_input.setEditable(True)
        self.telnet_terminal_type_input.addItems(_TELNET_TERMINAL_TYPE_CHOICES)
        self.telnet_connect_timeout_input = QDoubleSpinBox()
        self.telnet_connect_timeout_input.setRange(1.0, 120.0)
        self.telnet_connect_timeout_input.setDecimals(1)
        self.telnet_connect_timeout_input.setSingleStep(0.5)
        self.telnet_connect_timeout_input.setValue(10.0)
        self.telnet_use_tls_input = QCheckBox("Use TLS (Telnet over SSL)")
        self.telnet_tls_verify_input = QCheckBox("Validate server certificate")
        self.telnet_tls_verify_input.setChecked(True)

        self.serial_port_row, self.serial_port_input = self._build_serial_port_selector()
        self.serial_baud_rate_input = QComboBox()
        self.serial_baud_rate_input.setEditable(True)
        self.serial_baud_rate_input.addItems([str(value) for value in _SERIAL_BAUD_CHOICES])
        self.serial_baud_rate_input.setCurrentText("9600")
        self.serial_data_bits_input = QComboBox()
        for label, value in _SERIAL_DATA_BITS_CHOICES:
            self.serial_data_bits_input.addItem(label, value)
        self.serial_parity_input = QComboBox()
        for label, value in _SERIAL_PARITY_CHOICES:
            self.serial_parity_input.addItem(label, value)
        self.serial_stop_bits_input = QComboBox()
        for label, value in _SERIAL_STOP_BITS_CHOICES:
            self.serial_stop_bits_input.addItem(label, value)
        self.serial_flow_control_input = QComboBox()
        for label, value in _SERIAL_FLOW_CONTROL_CHOICES:
            self.serial_flow_control_input.addItem(label, value)
        self.serial_terminal_type_input = QComboBox()
        self.serial_terminal_type_input.setEditable(True)
        self.serial_terminal_type_input.addItems(_SERIAL_TERMINAL_TYPE_CHOICES)
        self.serial_terminal_type_input.setCurrentText("auto")

        form = QFormLayout()
        self._form = form
        form.addRow("Name", self.name_input)
        form.addRow("Host", self.host_input)
        form.addRow("Username", self.user_input)
        form.addRow("Domain (RDP)", self.domain_input)
        form.addRow("Protocol", self.protocol_input)
        form.addRow("Port", self.port_input)
        form.addRow("Resolution (RDP/VNC)", self.display_resolution_input)
        form.addRow("", self.display_fullscreen_input)
        form.addRow("", self.vnc_allow_resize_input)
        form.addRow("Color Depth (RDP/VNC)", self.display_color_input)
        form.addRow("Audio (RDP)", self.rdp_audio_mode_input)
        form.addRow("Open Mode", self.remote_launch_mode_input)
        form.addRow("", self.nomachine_audio_enabled_input)
        form.addRow("", self.nomachine_mute_remote_audio_input)
        form.addRow("", self.nomachine_auto_resize_input)
        form.addRow("Resize Mode (NoMachine)", self.nomachine_resize_mode_input)
        form.addRow("Link Quality (NoMachine)", self.nomachine_link_quality_input)
        form.addRow("Video Quality (NoMachine)", self.nomachine_video_quality_input)
        form.addRow("Terminal Type (Telnet)", self.telnet_terminal_type_input)
        form.addRow("Connect Timeout (Telnet, s)", self.telnet_connect_timeout_input)
        form.addRow("", self.telnet_use_tls_input)
        form.addRow("", self.telnet_tls_verify_input)
        form.addRow("Serial Port", self.serial_port_row)
        form.addRow("Baud Rate", self.serial_baud_rate_input)
        form.addRow("Data Bits", self.serial_data_bits_input)
        form.addRow("Parity", self.serial_parity_input)
        form.addRow("Stop Bits", self.serial_stop_bits_input)
        form.addRow("Flow Control", self.serial_flow_control_input)
        form.addRow("Terminal Type (Serial)", self.serial_terminal_type_input)
        form.addRow("Default Local Folder (SFTP)", self.sftp_local_row)
        form.addRow("Default Remote Folder (SFTP)", self.sftp_remote_folder_input)
        form.addRow("Folder", self.folder_input)
        form.addRow("Tags (comma-separated)", self.tags_input)
        form.addRow("", self.key_auth_input)
        form.addRow("Private Key Path", self.private_key_row)
        form.addRow("Public Key Path", self.public_key_row)
        form.addRow("", self.save_password_input)
        form.addRow("Password (optional)", self.password_row)
        form.addRow("", self.terminal_color_override_input)
        form.addRow("Terminal Background Override", self.terminal_bg_color_widget)
        form.addRow("Terminal Foreground Override", self.terminal_fg_color_widget)
        form.addRow("Terminal Color Preview", self.terminal_color_preview)
        form.addRow("Shell Banner Message", self.shell_banner_message_input)
        form.addRow("Shell Banner Font Color", self.shell_banner_color_widget)
        form.addRow("", self.shell_banner_blink_input)
        form.addRow("", self.x11_input)
        form.addRow("", self.ssh_legacy_compatibility_input)
        form.addRow("", self.ssh_keepalive_input)
        form.addRow("", self.ssh_automation_enabled_input)
        form.addRow("Automated Scripting", self.ssh_automation_button)
        form.addRow("", self.ssh_automation_summary)
        form.addRow("SSH Tunneling", self.ssh_tunneling_button)
        form.addRow("", self.ssh_tunnel_summary)
        form.addRow("Notes", self.notes_input)

        self._button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self._accept_button = self._button_box.button(QDialogButtonBox.Ok)
        if self._accept_button is not None and self._quick_connect:
            self._accept_button.setText("Connect")
        self._button_box.accepted.connect(self.accept)
        self._button_box.rejected.connect(self.reject)

        form_container = QWidget()
        form_container.setLayout(form)
        form_scroll = QScrollArea(self)
        form_scroll.setWidget(form_container)
        form_scroll.setWidgetResizable(True)
        form_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        root = QVBoxLayout(self)
        root.addWidget(form_scroll, 1)
        root.addWidget(self._button_box)

        _geo = QSettings("SnakeSh", "SnakeSh").value("session_editor/geometry")
        if _geo is not None:
            self.restoreGeometry(_geo)

        self.protocol_input.currentTextChanged.connect(self._on_protocol_changed)
        self.terminal_color_override_input.toggled.connect(self._update_terminal_color_override_controls)
        self.terminal_bg_color_input.textChanged.connect(self._refresh_terminal_color_preview)
        self.terminal_fg_color_input.textChanged.connect(self._refresh_terminal_color_preview)
        self.nomachine_audio_enabled_input.toggled.connect(self._update_nomachine_audio_controls)
        self.telnet_use_tls_input.toggled.connect(self._update_telnet_tls_controls)
        self.ssh_automation_enabled_input.toggled.connect(self._update_automation_summary)
        self.ssh_automation_button.clicked.connect(self._open_automation_dialog)
        self.ssh_tunneling_button.clicked.connect(self._open_ssh_tunneling_dialog)
        self._refresh_serial_ports()
        self._populate()
        self._update_protocol_specific_fields()
        self._apply_quick_connect_mode()
        self._update_terminal_color_override_controls()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        QSettings("SnakeSh", "SnakeSh").setValue("session_editor/geometry", self.saveGeometry())
        super().closeEvent(event)

    def _on_protocol_changed(self) -> None:
        self._set_default_port()
        self._update_protocol_specific_fields()

    def _set_default_port(self) -> None:
        protocol = Protocol(self.protocol_input.currentText().lower())
        self.port_input.setValue(SessionService.default_port_for(protocol))

    def _update_protocol_specific_fields(self) -> None:
        protocol = Protocol(self.protocol_input.currentText().lower())
        is_rdp = protocol == Protocol.RDP
        is_vnc = protocol == Protocol.VNC
        is_nomachine = protocol == Protocol.NOMACHINE
        is_telnet = protocol == Protocol.TELNET
        is_serial = protocol == Protocol.SERIAL
        is_remote_desktop = protocol in (Protocol.RDP, Protocol.VNC)
        supports_remote_launch_mode = protocol == Protocol.RDP and platform.system().lower() == "linux"
        supports_terminal_automation = protocol in (Protocol.SSH, Protocol.TELNET, Protocol.SERIAL)
        supports_shell_banner = protocol in (Protocol.SSH, Protocol.TELNET, Protocol.SERIAL)
        is_ssh_family = protocol in (Protocol.SSH, Protocol.SFTP)
        is_network_host = protocol != Protocol.SERIAL
        supports_password_storage = (
            protocol in (Protocol.SSH, Protocol.SFTP, Protocol.RDP, Protocol.VNC) and not self._quick_connect
        )
        supports_password_entry = protocol in (Protocol.SSH, Protocol.SFTP, Protocol.RDP, Protocol.VNC)

        self.host_input.setVisible(is_network_host)
        self._set_form_label_visible(self.host_input, is_network_host)
        self.user_input.setVisible(is_network_host)
        self._set_form_label_visible(self.user_input, is_network_host)
        self.domain_input.setVisible(is_rdp)
        self._set_form_label_visible(self.domain_input, is_rdp)
        self.port_input.setVisible(is_network_host)
        self._set_form_label_visible(self.port_input, is_network_host)
        self.display_resolution_input.setVisible(is_remote_desktop)
        self._set_form_label_visible(self.display_resolution_input, is_remote_desktop)
        self.display_fullscreen_input.setVisible(is_remote_desktop)
        self.vnc_allow_resize_input.setVisible(is_vnc)
        self.display_color_input.setVisible(is_remote_desktop)
        self._set_form_label_visible(self.display_color_input, is_remote_desktop)
        self.rdp_audio_mode_input.setVisible(is_rdp)
        self._set_form_label_visible(self.rdp_audio_mode_input, is_rdp)
        self.remote_launch_mode_input.setVisible(supports_remote_launch_mode)
        self._set_form_label_visible(self.remote_launch_mode_input, supports_remote_launch_mode)
        self.nomachine_audio_enabled_input.setVisible(is_nomachine)
        self.nomachine_mute_remote_audio_input.setVisible(is_nomachine)
        self.nomachine_auto_resize_input.setVisible(is_nomachine)
        self.nomachine_resize_mode_input.setVisible(is_nomachine)
        self._set_form_label_visible(self.nomachine_resize_mode_input, is_nomachine)
        self.nomachine_link_quality_input.setVisible(is_nomachine)
        self._set_form_label_visible(self.nomachine_link_quality_input, is_nomachine)
        self.nomachine_video_quality_input.setVisible(is_nomachine)
        self._set_form_label_visible(self.nomachine_video_quality_input, is_nomachine)
        self.telnet_terminal_type_input.setVisible(is_telnet)
        self._set_form_label_visible(self.telnet_terminal_type_input, is_telnet)
        self.telnet_connect_timeout_input.setVisible(is_telnet)
        self._set_form_label_visible(self.telnet_connect_timeout_input, is_telnet)
        self.telnet_use_tls_input.setVisible(is_telnet)
        self._set_form_label_visible(self.telnet_use_tls_input, is_telnet)
        self.telnet_tls_verify_input.setVisible(is_telnet)
        self._set_form_label_visible(self.telnet_tls_verify_input, is_telnet)
        self.serial_port_row.setVisible(is_serial)
        self._set_form_label_visible(self.serial_port_row, is_serial)
        self.serial_baud_rate_input.setVisible(is_serial)
        self._set_form_label_visible(self.serial_baud_rate_input, is_serial)
        self.serial_data_bits_input.setVisible(is_serial)
        self._set_form_label_visible(self.serial_data_bits_input, is_serial)
        self.serial_parity_input.setVisible(is_serial)
        self._set_form_label_visible(self.serial_parity_input, is_serial)
        self.serial_stop_bits_input.setVisible(is_serial)
        self._set_form_label_visible(self.serial_stop_bits_input, is_serial)
        self.serial_flow_control_input.setVisible(is_serial)
        self._set_form_label_visible(self.serial_flow_control_input, is_serial)
        self.serial_terminal_type_input.setVisible(is_serial)
        self._set_form_label_visible(self.serial_terminal_type_input, is_serial)
        self.sftp_local_row.setVisible(is_ssh_family)
        self._set_form_label_visible(self.sftp_local_row, is_ssh_family)
        self.sftp_remote_folder_input.setVisible(is_ssh_family)
        self._set_form_label_visible(self.sftp_remote_folder_input, is_ssh_family)
        self.key_auth_input.setVisible(is_ssh_family)
        self.private_key_row.setVisible(is_ssh_family)
        self._set_form_label_visible(self.private_key_row, is_ssh_family)
        self.public_key_row.setVisible(is_ssh_family)
        self._set_form_label_visible(self.public_key_row, is_ssh_family)
        self.x11_input.setVisible(is_ssh_family)
        self.ssh_legacy_compatibility_input.setVisible(is_ssh_family)
        self.ssh_keepalive_input.setVisible(is_ssh_family)
        self.ssh_automation_enabled_input.setVisible(supports_terminal_automation)
        self.ssh_automation_button.setVisible(supports_terminal_automation)
        self.ssh_automation_summary.setVisible(supports_terminal_automation)
        self._set_form_label_visible(self.ssh_automation_button, supports_terminal_automation)
        self.ssh_tunneling_button.setVisible(is_ssh_family)
        self.ssh_tunnel_summary.setVisible(is_ssh_family)
        self._set_form_label_visible(self.ssh_tunneling_button, is_ssh_family)
        self.save_password_input.setVisible(supports_password_storage)
        self.save_password_input.setEnabled(supports_password_storage)
        self.password_row.setVisible(supports_password_entry)
        self._set_form_label_visible(self.password_row, supports_password_entry)
        self.shell_banner_message_input.setVisible(supports_shell_banner)
        self._set_form_label_visible(self.shell_banner_message_input, supports_shell_banner)
        self.shell_banner_color_widget.setVisible(supports_shell_banner)
        self._set_form_label_visible(self.shell_banner_color_widget, supports_shell_banner)
        self.shell_banner_blink_input.setVisible(supports_shell_banner)
        self._set_form_label_visible(self.shell_banner_blink_input, supports_shell_banner)
        if not supports_password_entry:
            self._set_password_visibility(False)
        if not supports_password_storage:
            self.save_password_input.setChecked(False)

        if not is_rdp:
            self.domain_input.clear()
            self._set_rdp_audio_mode("local")
        if not is_remote_desktop:
            self._set_display_resolution("")
            self.display_fullscreen_input.setChecked(False)
            self.vnc_allow_resize_input.setChecked(False)
            self._set_display_color_depth(0)
            self._set_remote_launch_mode("tab")
        elif protocol == Protocol.VNC:
            self._set_remote_launch_mode("detached")
        elif protocol == Protocol.RDP and not supports_remote_launch_mode:
            self._set_remote_launch_mode("detached")
        if is_remote_desktop and self._session is None and self._normalized_resolution_text() == "":
            self._set_display_resolution("auto")
        if not is_vnc:
            self.vnc_allow_resize_input.setChecked(False)
        if not is_nomachine:
            self.nomachine_audio_enabled_input.setChecked(True)
            self.nomachine_mute_remote_audio_input.setChecked(True)
            self.nomachine_auto_resize_input.setChecked(False)
            self._set_nomachine_resize_mode("scaled")
            self._set_nomachine_link_quality(5)
            self._set_nomachine_video_quality(5)
        if not is_telnet:
            self._set_telnet_terminal_type("xterm-256color")
            self.telnet_connect_timeout_input.setValue(10.0)
            self.telnet_use_tls_input.setChecked(False)
            self.telnet_tls_verify_input.setChecked(True)
        if not is_serial:
            self._set_serial_port("")
            self._set_serial_baud_rate(9600)
            self._set_serial_data_bits(8)
            self._set_serial_parity("none")
            self._set_serial_stop_bits("1")
            self._set_serial_flow_control("none")
            self._set_serial_terminal_type("auto")
        else:
            if not self._selected_serial_port() and self.host_input.text().strip():
                self._set_serial_port(self.host_input.text().strip())
            self._refresh_serial_ports()
        if not is_ssh_family:
            self.key_auth_input.setChecked(False)
            self.private_key_input.clear()
            self.public_key_input.clear()
            self.x11_input.setChecked(False)
            self.ssh_legacy_compatibility_input.setChecked(False)
            self.ssh_keepalive_input.setChecked(False)
            self.sftp_local_folder_input.setText(DEFAULT_SFTP_LOCAL_FOLDER)
            self.sftp_remote_folder_input.setText(DEFAULT_SFTP_REMOTE_FOLDER)
            self._ssh_dynamic_tunnels.clear()
            self._ssh_static_tunnels.clear()
        if not supports_terminal_automation:
            self.ssh_automation_enabled_input.setChecked(False)
            self._ssh_automation_steps.clear()
        if not supports_shell_banner:
            self.shell_banner_message_input.clear()
            self.shell_banner_color_input.setText("#f59e0b")
            self.shell_banner_blink_input.setChecked(False)
        if is_serial:
            self.domain_input.clear()
            self.user_input.clear()
            self.save_password_input.setChecked(False)
            self.password_input.clear()
            self._saved_password_loaded = False
        self._update_telnet_tls_controls()
        self._update_nomachine_audio_controls()
        self._update_automation_summary()
        self._update_ssh_tunnel_summary()
        self._apply_quick_connect_mode()

    def _populate(self) -> None:
        self._saved_password_loaded = False
        self._set_password_visibility(False)
        if not self._session:
            self.password_input.setPlaceholderText(
                "Optional for this connection" if self._quick_connect else "Optional"
            )
            self._set_default_port()
            self.sftp_local_folder_input.setText(DEFAULT_SFTP_LOCAL_FOLDER)
            self.sftp_remote_folder_input.setText(DEFAULT_SFTP_REMOTE_FOLDER)
            self._set_telnet_terminal_type("xterm-256color")
            self.telnet_connect_timeout_input.setValue(10.0)
            self.telnet_use_tls_input.setChecked(False)
            self.telnet_tls_verify_input.setChecked(True)
            self._set_serial_port("")
            self._set_serial_baud_rate(9600)
            self._set_serial_data_bits(8)
            self._set_serial_parity("none")
            self._set_serial_stop_bits("1")
            self._set_serial_flow_control("none")
            self._set_serial_terminal_type("auto")
            self.terminal_color_override_input.setChecked(False)
            self.terminal_bg_color_input.clear()
            self.terminal_fg_color_input.clear()
            self.shell_banner_message_input.clear()
            self.shell_banner_color_input.setText("#f59e0b")
            self.shell_banner_blink_input.setChecked(False)
            if self._quick_connect:
                self.save_password_input.setChecked(False)
            return
        self.name_input.setText(self._session.name)
        self.host_input.setText(self._session.host)
        self.user_input.setText(self._session.username)
        self.domain_input.setText(self._session.domain)
        self.sftp_local_folder_input.setText(self._session.sftp_local_folder)
        self.sftp_remote_folder_input.setText(self._session.sftp_remote_folder)
        self.protocol_input.setCurrentText(self._session.protocol.value.upper())
        self.port_input.setValue(self._session.port)
        self.folder_input.setText(self._session.folder)
        self.tags_input.setText(",".join(self._session.tags))
        self.key_auth_input.setChecked(self._session.use_key_auth)
        self.private_key_input.setText(self._session.private_key_path)
        self.public_key_input.setText(self._session.public_key_path)
        self.save_password_input.setChecked(self._session.save_password)
        if self._quick_connect:
            self.password_input.setPlaceholderText("Optional for this connection")
            self.save_password_input.setChecked(False)
        elif self._session.save_password:
            self.password_input.setPlaceholderText("Saved in OS keyring. Click Show to reveal or type to replace.")
        else:
            self.password_input.setPlaceholderText("Optional")
        self.terminal_color_override_input.setChecked(self._session.terminal_color_override_enabled)
        self.terminal_bg_color_input.setText(self._session.terminal_bg_color)
        self.terminal_fg_color_input.setText(self._session.terminal_fg_color)
        self.shell_banner_message_input.setText(self._session.shell_banner_message)
        self.shell_banner_color_input.setText(self._session.shell_banner_color or "#f59e0b")
        self.shell_banner_blink_input.setChecked(self._session.shell_banner_blink)
        self.x11_input.setChecked(self._session.x11_forwarding)
        self.ssh_legacy_compatibility_input.setChecked(self._session.ssh_legacy_compatibility)
        self.ssh_keepalive_input.setChecked(self._session.ssh_keepalive)
        self.ssh_automation_enabled_input.setChecked(self._session.ssh_automation_enabled)
        self._ssh_automation_steps = list(self._session.ssh_automation_steps)
        self._update_automation_summary()
        self._ssh_dynamic_tunnels = list(self._session.ssh_dynamic_tunnels)
        self._ssh_static_tunnels = list(self._session.ssh_static_tunnels)
        self._update_ssh_tunnel_summary()
        self._set_display_resolution(self._session.display_resolution)
        self.display_fullscreen_input.setChecked(self._session.display_fullscreen)
        self.vnc_allow_resize_input.setChecked(self._session.vnc_allow_resize)
        self._set_display_color_depth(self._session.display_color_depth)
        self._set_rdp_audio_mode(self._session.rdp_audio_mode)
        self._set_remote_launch_mode(self._session.remote_launch_mode)
        self.nomachine_audio_enabled_input.setChecked(self._session.nomachine_audio_enabled)
        self.nomachine_mute_remote_audio_input.setChecked(self._session.nomachine_mute_remote_audio)
        self.nomachine_auto_resize_input.setChecked(self._session.nomachine_physical_desktop_auto_resize)
        self._set_nomachine_resize_mode(self._session.nomachine_physical_desktop_resize_mode)
        self._set_nomachine_link_quality(self._session.nomachine_link_quality)
        self._set_nomachine_video_quality(self._session.nomachine_video_quality)
        self._set_telnet_terminal_type(self._session.telnet_terminal_type)
        self.telnet_connect_timeout_input.setValue(self._session.telnet_connect_timeout_seconds)
        self.telnet_use_tls_input.setChecked(self._session.telnet_use_tls)
        self.telnet_tls_verify_input.setChecked(self._session.telnet_tls_verify)
        self._set_serial_port(self._session.host)
        self._set_serial_baud_rate(self._session.serial_baud_rate)
        self._set_serial_data_bits(self._session.serial_data_bits)
        self._set_serial_parity(self._session.serial_parity)
        self._set_serial_stop_bits(self._session.serial_stop_bits)
        self._set_serial_flow_control(self._session.serial_flow_control)
        self._set_serial_terminal_type(self._session.serial_terminal_type)
        self._update_nomachine_audio_controls()
        self._update_terminal_color_override_controls()
        self.notes_input.setText(self._session.notes)

    def build_session(self) -> Session:
        protocol = Protocol(self.protocol_input.currentText().lower())
        tags = [] if self._quick_connect else [t.strip() for t in self.tags_input.text().split(",") if t.strip()]
        serial_host = self._selected_serial_port().strip() or self.host_input.text().strip()
        payload = dict(
            name="" if self._quick_connect else self.name_input.text().strip(),
            host=self.host_input.text().strip(),
            username=self.user_input.text().strip(),
            domain=self.domain_input.text().strip(),
            protocol=protocol,
            port=self.port_input.value(),
            folder="Default" if self._quick_connect else self.folder_input.text().strip() or "Default",
            tags=tags,
            use_key_auth=self.key_auth_input.isChecked(),
            private_key_path=self.private_key_input.text().strip(),
            public_key_path=self.public_key_input.text().strip(),
            save_password=False if self._quick_connect else self.save_password_input.isChecked(),
            terminal_color_override_enabled=self.terminal_color_override_input.isChecked(),
            terminal_bg_color=self.terminal_bg_color_input.text().strip(),
            terminal_fg_color=self.terminal_fg_color_input.text().strip(),
            shell_banner_message=self.shell_banner_message_input.text().strip(),
            shell_banner_color=self.shell_banner_color_input.text().strip(),
            shell_banner_blink=self.shell_banner_blink_input.isChecked(),
            x11_forwarding=self.x11_input.isChecked(),
            ssh_legacy_compatibility=self.ssh_legacy_compatibility_input.isChecked(),
            ssh_keepalive=self.ssh_keepalive_input.isChecked(),
            ssh_automation_enabled=self.ssh_automation_enabled_input.isChecked(),
            ssh_automation_steps=list(self._ssh_automation_steps),
            ssh_dynamic_tunnels=list(self._ssh_dynamic_tunnels),
            ssh_static_tunnels=list(self._ssh_static_tunnels),
            display_resolution=self._normalized_resolution_text(),
            display_fullscreen=self.display_fullscreen_input.isChecked(),
            display_color_depth=self._selected_color_depth(),
            vnc_allow_resize=self.vnc_allow_resize_input.isChecked(),
            rdp_audio_mode=self._selected_rdp_audio_mode(),
            remote_launch_mode=self._selected_remote_launch_mode(),
            nomachine_audio_enabled=self.nomachine_audio_enabled_input.isChecked(),
            nomachine_mute_remote_audio=self.nomachine_mute_remote_audio_input.isChecked(),
            nomachine_physical_desktop_auto_resize=self.nomachine_auto_resize_input.isChecked(),
            nomachine_physical_desktop_resize_mode=self._selected_nomachine_resize_mode(),
            nomachine_link_quality=self._selected_nomachine_link_quality(),
            nomachine_video_quality=self._selected_nomachine_video_quality(),
            telnet_terminal_type=self._selected_telnet_terminal_type(),
            telnet_connect_timeout_seconds=self.telnet_connect_timeout_input.value(),
            telnet_use_tls=self.telnet_use_tls_input.isChecked(),
            telnet_tls_verify=self.telnet_tls_verify_input.isChecked(),
            serial_baud_rate=self._selected_serial_baud_rate(),
            serial_data_bits=self._selected_serial_data_bits(),
            serial_parity=self._selected_serial_parity(),
            serial_stop_bits=self._selected_serial_stop_bits(),
            serial_flow_control=self._selected_serial_flow_control(),
            serial_terminal_type=self._selected_serial_terminal_type(),
            sftp_local_folder=self.sftp_local_folder_input.text().strip() or DEFAULT_SFTP_LOCAL_FOLDER,
            sftp_remote_folder=self.sftp_remote_folder_input.text().strip() or DEFAULT_SFTP_REMOTE_FOLDER,
            notes="" if self._quick_connect else self.notes_input.toPlainText().strip(),
        )
        if protocol == Protocol.RDP:
            payload["use_key_auth"] = False
            payload["private_key_path"] = ""
            payload["public_key_path"] = ""
            payload["sftp_local_folder"] = DEFAULT_SFTP_LOCAL_FOLDER
            payload["sftp_remote_folder"] = DEFAULT_SFTP_REMOTE_FOLDER
            payload["x11_forwarding"] = False
            payload["ssh_legacy_compatibility"] = False
            payload["ssh_keepalive"] = False
            payload["ssh_automation_enabled"] = False
            payload["ssh_automation_steps"] = []
            payload["ssh_dynamic_tunnels"] = []
            payload["ssh_static_tunnels"] = []
            payload["telnet_terminal_type"] = "xterm-256color"
            payload["telnet_connect_timeout_seconds"] = 10.0
            payload["telnet_use_tls"] = False
            payload["telnet_tls_verify"] = True
            payload["serial_baud_rate"] = 9600
            payload["serial_data_bits"] = 8
            payload["serial_parity"] = "none"
            payload["serial_stop_bits"] = "1"
            payload["serial_flow_control"] = "none"
            payload["serial_terminal_type"] = "auto"
            payload["shell_banner_message"] = ""
            payload["shell_banner_color"] = ""
            payload["shell_banner_blink"] = False
            if platform.system().lower() != "linux":
                payload["remote_launch_mode"] = "detached"
        elif protocol == Protocol.VNC:
            payload["domain"] = ""
            payload["use_key_auth"] = False
            payload["private_key_path"] = ""
            payload["public_key_path"] = ""
            payload["sftp_local_folder"] = DEFAULT_SFTP_LOCAL_FOLDER
            payload["sftp_remote_folder"] = DEFAULT_SFTP_REMOTE_FOLDER
            payload["rdp_audio_mode"] = "local"
            payload["remote_launch_mode"] = "detached"
            payload["x11_forwarding"] = False
            payload["ssh_legacy_compatibility"] = False
            payload["ssh_keepalive"] = False
            payload["ssh_automation_enabled"] = False
            payload["ssh_automation_steps"] = []
            payload["ssh_dynamic_tunnels"] = []
            payload["ssh_static_tunnels"] = []
            payload["telnet_terminal_type"] = "xterm-256color"
            payload["telnet_connect_timeout_seconds"] = 10.0
            payload["telnet_use_tls"] = False
            payload["telnet_tls_verify"] = True
            payload["serial_baud_rate"] = 9600
            payload["serial_data_bits"] = 8
            payload["serial_parity"] = "none"
            payload["serial_stop_bits"] = "1"
            payload["serial_flow_control"] = "none"
            payload["serial_terminal_type"] = "auto"
            payload["shell_banner_message"] = ""
            payload["shell_banner_color"] = ""
            payload["shell_banner_blink"] = False
        elif protocol == Protocol.NOMACHINE:
            payload["domain"] = ""
            payload["use_key_auth"] = False
            payload["private_key_path"] = ""
            payload["public_key_path"] = ""
            payload["save_password"] = False
            payload["sftp_local_folder"] = DEFAULT_SFTP_LOCAL_FOLDER
            payload["sftp_remote_folder"] = DEFAULT_SFTP_REMOTE_FOLDER
            payload["display_resolution"] = ""
            payload["display_fullscreen"] = False
            payload["display_color_depth"] = 0
            payload["vnc_allow_resize"] = False
            payload["rdp_audio_mode"] = "local"
            payload["remote_launch_mode"] = "detached"
            payload["x11_forwarding"] = False
            payload["ssh_legacy_compatibility"] = False
            payload["ssh_keepalive"] = False
            payload["ssh_automation_enabled"] = False
            payload["ssh_automation_steps"] = []
            payload["ssh_dynamic_tunnels"] = []
            payload["ssh_static_tunnels"] = []
            payload["telnet_terminal_type"] = "xterm-256color"
            payload["telnet_connect_timeout_seconds"] = 10.0
            payload["telnet_use_tls"] = False
            payload["telnet_tls_verify"] = True
            payload["serial_baud_rate"] = 9600
            payload["serial_data_bits"] = 8
            payload["serial_parity"] = "none"
            payload["serial_stop_bits"] = "1"
            payload["serial_flow_control"] = "none"
            payload["serial_terminal_type"] = "auto"
            payload["shell_banner_message"] = ""
            payload["shell_banner_color"] = ""
            payload["shell_banner_blink"] = False
        elif protocol == Protocol.TELNET:
            payload["domain"] = ""
            payload["use_key_auth"] = False
            payload["private_key_path"] = ""
            payload["public_key_path"] = ""
            payload["save_password"] = False
            payload["sftp_local_folder"] = DEFAULT_SFTP_LOCAL_FOLDER
            payload["sftp_remote_folder"] = DEFAULT_SFTP_REMOTE_FOLDER
            payload["display_resolution"] = ""
            payload["display_fullscreen"] = False
            payload["display_color_depth"] = 0
            payload["vnc_allow_resize"] = False
            payload["rdp_audio_mode"] = "local"
            payload["remote_launch_mode"] = "tab"
            payload["x11_forwarding"] = False
            payload["ssh_legacy_compatibility"] = False
            payload["ssh_keepalive"] = False
            payload["ssh_dynamic_tunnels"] = []
            payload["ssh_static_tunnels"] = []
            payload["serial_baud_rate"] = 9600
            payload["serial_data_bits"] = 8
            payload["serial_parity"] = "none"
            payload["serial_stop_bits"] = "1"
            payload["serial_flow_control"] = "none"
            payload["serial_terminal_type"] = "auto"
        elif protocol == Protocol.SERIAL:
            payload["host"] = serial_host
            payload["username"] = ""
            payload["domain"] = ""
            payload["port"] = 0
            payload["use_key_auth"] = False
            payload["private_key_path"] = ""
            payload["public_key_path"] = ""
            payload["save_password"] = False
            payload["sftp_local_folder"] = DEFAULT_SFTP_LOCAL_FOLDER
            payload["sftp_remote_folder"] = DEFAULT_SFTP_REMOTE_FOLDER
            payload["display_resolution"] = ""
            payload["display_fullscreen"] = False
            payload["display_color_depth"] = 0
            payload["vnc_allow_resize"] = False
            payload["rdp_audio_mode"] = "local"
            payload["remote_launch_mode"] = "tab"
            payload["x11_forwarding"] = False
            payload["ssh_legacy_compatibility"] = False
            payload["ssh_keepalive"] = False
            payload["ssh_dynamic_tunnels"] = []
            payload["ssh_static_tunnels"] = []
            payload["telnet_terminal_type"] = "xterm-256color"
            payload["telnet_connect_timeout_seconds"] = 10.0
            payload["telnet_use_tls"] = False
            payload["telnet_tls_verify"] = True
        else:
            payload["domain"] = ""
            payload["display_resolution"] = ""
            payload["display_fullscreen"] = False
            payload["display_color_depth"] = 0
            payload["vnc_allow_resize"] = False
            payload["rdp_audio_mode"] = "local"
            payload["remote_launch_mode"] = "tab"
            payload["telnet_terminal_type"] = "xterm-256color"
            payload["telnet_connect_timeout_seconds"] = 10.0
            payload["telnet_use_tls"] = False
            payload["telnet_tls_verify"] = True
            payload["serial_baud_rate"] = 9600
            payload["serial_data_bits"] = 8
            payload["serial_parity"] = "none"
            payload["serial_stop_bits"] = "1"
            payload["serial_flow_control"] = "none"
            payload["serial_terminal_type"] = "auto"
            if protocol not in (Protocol.SSH, Protocol.TELNET, Protocol.SERIAL):
                payload["shell_banner_message"] = ""
                payload["shell_banner_color"] = ""
                payload["shell_banner_blink"] = False
                payload["ssh_automation_enabled"] = False
                payload["ssh_automation_steps"] = []
        if protocol != Protocol.NOMACHINE:
            payload["nomachine_audio_enabled"] = True
            payload["nomachine_mute_remote_audio"] = True
            payload["nomachine_physical_desktop_auto_resize"] = False
            payload["nomachine_physical_desktop_resize_mode"] = "scaled"
            payload["nomachine_link_quality"] = 5
            payload["nomachine_video_quality"] = 5
        if protocol == Protocol.RDP:
            payload["vnc_allow_resize"] = False
        if not payload["terminal_color_override_enabled"]:
            payload["terminal_bg_color"] = ""
            payload["terminal_fg_color"] = ""
        if not payload["shell_banner_message"]:
            payload["shell_banner_color"] = ""
            payload["shell_banner_blink"] = False
        if self._session:
            payload["id"] = self._session.id
        return Session(**payload)

    def password_text(self) -> str:
        return self.password_input.text()

    def _apply_quick_connect_mode(self) -> None:
        quick_connect = self._quick_connect
        self.name_input.setVisible(not quick_connect)
        self._set_form_label_visible(self.name_input, not quick_connect)
        self.folder_input.setVisible(not quick_connect)
        self._set_form_label_visible(self.folder_input, not quick_connect)
        self.tags_input.setVisible(not quick_connect)
        self._set_form_label_visible(self.tags_input, not quick_connect)
        self.notes_input.setVisible(not quick_connect)
        self._set_form_label_visible(self.notes_input, not quick_connect)
        if quick_connect:
            self.save_password_input.setChecked(False)
            self.save_password_input.hide()
            self.save_password_input.setEnabled(False)
            self._set_form_label_text(self.password_row, "Password (this connection only)")
            if not self.password_input.text():
                self.password_input.setPlaceholderText("Optional for this connection")
        else:
            self.save_password_input.setEnabled(True)
            self._set_form_label_text(self.password_row, "Password (optional)")

    def _set_password_visibility(self, visible: bool) -> None:
        showing = bool(visible)
        if showing:
            self._load_saved_password_for_display()
        if self.password_visibility_btn.isChecked() != showing:
            self.password_visibility_btn.blockSignals(True)
            self.password_visibility_btn.setChecked(showing)
            self.password_visibility_btn.blockSignals(False)
        self.password_input.setEchoMode(QLineEdit.Normal if showing else QLineEdit.Password)
        self.password_visibility_btn.setText("Hide" if showing else "Show")
        self.password_visibility_btn.setToolTip("Hide password" if showing else "Show password")

    def _load_saved_password_for_display(self) -> None:
        if self.password_input.text():
            return
        if self._saved_password_loaded:
            return
        session = self._session
        if session is None or not session.save_password or not self.save_password_input.isChecked():
            return
        loader = self._password_loader
        if loader is None:
            return
        try:
            saved_password = loader(session)
        except Exception:
            return
        self._saved_password_loaded = True
        if not saved_password:
            self.password_input.setPlaceholderText("No saved password found in OS keyring.")
            return
        self.password_input.setText(saved_password)

    def _update_terminal_color_override_controls(self) -> None:
        enabled = self.terminal_color_override_input.isChecked()
        self.terminal_bg_color_widget.setEnabled(enabled)
        self.terminal_fg_color_widget.setEnabled(enabled)
        self.terminal_bg_color_input.setEnabled(enabled)
        self.terminal_fg_color_input.setEnabled(enabled)
        self._refresh_terminal_color_preview()

    @staticmethod
    def _normalized_preview_color(raw: str, fallback: str) -> str:
        color = QColor(raw.strip())
        if color.isValid():
            return color.name()
        return fallback

    def _refresh_terminal_color_preview(self) -> None:
        override_enabled = self.terminal_color_override_input.isChecked()
        bg_color = self._normalized_preview_color(self.terminal_bg_color_input.text(), "#000000")
        fg_color = self._normalized_preview_color(self.terminal_fg_color_input.text(), "#ffffff")
        border_style = "1px solid #6b7280" if override_enabled else "1px dashed #6b7280"
        tooltip = (
            "Preview of this session's terminal override colors."
            if override_enabled
            else "Enable terminal color override to apply per-session colors."
        )
        self.terminal_color_preview.setToolTip(tooltip)
        self.terminal_color_preview.setStyleSheet(
            f"QTextEdit {{ background-color: {bg_color}; color: {fg_color}; "
            f"border: {border_style}; border-radius: 6px; padding: 6px; }}"
        )

    def _set_form_label_visible(self, field: QWidget, visible: bool) -> None:
        label = self._form.labelForField(field)
        if label:
            label.setVisible(visible)

    def _set_form_label_text(self, field: QWidget, text: str) -> None:
        label = self._form.labelForField(field)
        if label:
            label.setText(text)

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
            current = QColor(field.text().strip())
            if not current.isValid():
                current = QColor(initial)
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

    def _build_file_input(self, title: str, filters: str) -> tuple[QWidget, QLineEdit]:
        container = QWidget(self)
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        field = QLineEdit()
        browse_btn = QPushButton("Browse")
        browse_btn.setFixedWidth(80)

        def choose_file() -> None:
            start = field.text().strip()
            selected, _ = QFileDialog.getOpenFileName(self, title, start or "", filters)
            if selected:
                field.setText(selected)

        browse_btn.clicked.connect(choose_file)
        layout.addWidget(field, 1)
        layout.addWidget(browse_btn, 0)
        return container, field

    def _build_directory_input(self, title: str) -> tuple[QWidget, QLineEdit]:
        container = QWidget(self)
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        field = QLineEdit()
        browse_btn = QPushButton("Browse")
        browse_btn.setFixedWidth(80)

        def choose_directory() -> None:
            start = field.text().strip()
            selected = QFileDialog.getExistingDirectory(self, title, start or "")
            if selected:
                field.setText(selected)

        browse_btn.clicked.connect(choose_directory)
        layout.addWidget(field, 1)
        layout.addWidget(browse_btn, 0)
        return container, field

    def _build_serial_port_selector(self) -> tuple[QWidget, QComboBox]:
        container = QWidget(self)
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        field = QComboBox()
        field.setEditable(True)
        refresh_btn = QPushButton("Refresh")
        refresh_btn.setFixedWidth(80)
        refresh_btn.clicked.connect(self._refresh_serial_ports)
        layout.addWidget(field, 1)
        layout.addWidget(refresh_btn, 0)
        return container, field

    def _refresh_serial_ports(self) -> None:
        selected = self._selected_serial_port()
        discovered = self._discover_serial_ports()
        self.serial_port_input.blockSignals(True)
        self.serial_port_input.clear()
        for port in discovered:
            self.serial_port_input.addItem(port, port)
        if selected and self.serial_port_input.findData(selected) < 0:
            self.serial_port_input.addItem(selected, selected)
        self.serial_port_input.setCurrentText(selected)
        self.serial_port_input.blockSignals(False)

    def _discover_serial_ports(self) -> list[str]:
        found: dict[str, None] = {}
        try:
            from serial.tools import list_ports

            for info in list_ports.comports():
                device = str(getattr(info, "device", "")).strip()
                if device:
                    found[device] = None
        except Exception:
            pass

        system = platform.system().lower()
        if system == "windows":
            for index in range(1, 257):
                candidate = f"COM{index}"
                found.setdefault(candidate, None)
        else:
            patterns = (
                "/dev/ttyS*",
                "/dev/ttyUSB*",
                "/dev/ttyACM*",
                "/dev/ttyAMA*",
                "/dev/tty.*",
                "/dev/cu.*",
                "/dev/rfcomm*",
                "/dev/serial/by-id/*",
            )
            for pattern in patterns:
                for raw_path in glob.glob(pattern):
                    try:
                        candidate = str(Path(raw_path))
                    except Exception:
                        continue
                    if candidate:
                        found.setdefault(candidate, None)
        return sorted(found.keys(), key=lambda value: value.lower())

    def _selected_serial_port(self) -> str:
        return self.serial_port_input.currentText().strip()

    def _set_serial_port(self, value: str) -> None:
        selected = value.strip()
        if selected and self.serial_port_input.findData(selected) < 0:
            self.serial_port_input.addItem(selected, selected)
        self.serial_port_input.setCurrentText(selected)

    def _selected_telnet_terminal_type(self) -> str:
        cleaned = self.telnet_terminal_type_input.currentText().strip()
        if not cleaned:
            return "xterm-256color"
        return cleaned

    def _set_telnet_terminal_type(self, value: str) -> None:
        cleaned = value.strip() or "xterm-256color"
        if self.telnet_terminal_type_input.findText(cleaned) < 0:
            self.telnet_terminal_type_input.addItem(cleaned)
        self.telnet_terminal_type_input.setCurrentText(cleaned)

    def _update_telnet_tls_controls(self) -> None:
        enabled = self.telnet_use_tls_input.isChecked()
        self.telnet_tls_verify_input.setEnabled(enabled)

    def _selected_serial_baud_rate(self) -> int:
        raw = self.serial_baud_rate_input.currentText().strip()
        try:
            parsed = int(raw)
        except (TypeError, ValueError):
            return 9600
        if parsed <= 0 or parsed > 4_000_000:
            return 9600
        return parsed

    def _set_serial_baud_rate(self, value: int) -> None:
        normalized = self._normalize_serial_baud_rate(value)
        text = str(normalized)
        if self.serial_baud_rate_input.findText(text) < 0:
            self.serial_baud_rate_input.addItem(text)
        self.serial_baud_rate_input.setCurrentText(text)

    @staticmethod
    def _normalize_serial_baud_rate(value: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return 9600
        if parsed <= 0 or parsed > 4_000_000:
            return 9600
        return parsed

    def _selected_serial_data_bits(self) -> int:
        return normalize_serial_data_bits(self.serial_data_bits_input.currentData())

    def _set_serial_data_bits(self, value: int) -> None:
        normalized = normalize_serial_data_bits(value)
        index = self.serial_data_bits_input.findData(normalized)
        if index < 0:
            index = self.serial_data_bits_input.findData(8)
        self.serial_data_bits_input.setCurrentIndex(max(0, index))

    def _selected_serial_parity(self) -> str:
        return normalize_serial_parity(self.serial_parity_input.currentData())

    def _set_serial_parity(self, value: str) -> None:
        normalized = normalize_serial_parity(value)
        index = self.serial_parity_input.findData(normalized)
        if index < 0:
            index = self.serial_parity_input.findData("none")
        self.serial_parity_input.setCurrentIndex(max(0, index))

    def _selected_serial_stop_bits(self) -> str:
        return normalize_serial_stop_bits(self.serial_stop_bits_input.currentData())

    def _set_serial_stop_bits(self, value: str) -> None:
        normalized = normalize_serial_stop_bits(value)
        index = self.serial_stop_bits_input.findData(normalized)
        if index < 0:
            index = self.serial_stop_bits_input.findData("1")
        self.serial_stop_bits_input.setCurrentIndex(max(0, index))

    def _selected_serial_flow_control(self) -> str:
        return normalize_serial_flow_control(self.serial_flow_control_input.currentData())

    def _set_serial_flow_control(self, value: str) -> None:
        normalized = normalize_serial_flow_control(value)
        index = self.serial_flow_control_input.findData(normalized)
        if index < 0:
            index = self.serial_flow_control_input.findData("none")
        self.serial_flow_control_input.setCurrentIndex(max(0, index))

    def _selected_serial_terminal_type(self) -> str:
        return normalize_serial_terminal_type(self.serial_terminal_type_input.currentText())

    def _set_serial_terminal_type(self, value: str) -> None:
        cleaned = normalize_serial_terminal_type(value)
        if self.serial_terminal_type_input.findText(cleaned) < 0:
            self.serial_terminal_type_input.addItem(cleaned)
        self.serial_terminal_type_input.setCurrentText(cleaned)

    def _normalized_resolution_text(self) -> str:
        raw = self.display_resolution_input.currentText()
        if is_auto_resolution(raw):
            return "auto"
        parsed = parse_resolution(raw)
        if not parsed:
            return ""
        width, height = parsed
        return f"{width}x{height}"

    def _set_display_resolution(self, value: str) -> None:
        normalized = value.strip() if value else ""
        if not normalized:
            self.display_resolution_input.setCurrentText("Default")
            return
        if is_auto_resolution(normalized):
            self.display_resolution_input.setCurrentText("Auto")
            return
        self.display_resolution_input.setCurrentText(normalized)

    def _selected_color_depth(self) -> int:
        depth = self.display_color_input.currentData()
        if isinstance(depth, int):
            return depth
        return 0

    def _set_display_color_depth(self, depth: int) -> None:
        index = self.display_color_input.findData(depth)
        if index < 0:
            index = self.display_color_input.findData(0)
        self.display_color_input.setCurrentIndex(max(0, index))

    def _selected_rdp_audio_mode(self) -> str:
        mode = self.rdp_audio_mode_input.currentData()
        if isinstance(mode, str):
            return normalize_rdp_audio_mode(mode)
        return "local"

    def _set_rdp_audio_mode(self, mode: str) -> None:
        normalized = normalize_rdp_audio_mode(mode)
        index = self.rdp_audio_mode_input.findData(normalized)
        if index < 0:
            index = self.rdp_audio_mode_input.findData("local")
        self.rdp_audio_mode_input.setCurrentIndex(max(0, index))

    def _selected_remote_launch_mode(self) -> str:
        mode = self.remote_launch_mode_input.currentData()
        if isinstance(mode, str):
            return normalize_remote_launch_mode(mode)
        return "tab"

    def _set_remote_launch_mode(self, mode: str) -> None:
        normalized = normalize_remote_launch_mode(mode)
        index = self.remote_launch_mode_input.findData(normalized)
        if index < 0:
            index = self.remote_launch_mode_input.findData("tab")
        self.remote_launch_mode_input.setCurrentIndex(max(0, index))

    def _selected_nomachine_resize_mode(self) -> str:
        mode = self.nomachine_resize_mode_input.currentData()
        if isinstance(mode, str):
            return normalize_nomachine_resize_mode(mode)
        return "scaled"

    def _set_nomachine_resize_mode(self, mode: str) -> None:
        normalized = normalize_nomachine_resize_mode(mode)
        index = self.nomachine_resize_mode_input.findData(normalized)
        if index < 0:
            index = self.nomachine_resize_mode_input.findData("scaled")
        self.nomachine_resize_mode_input.setCurrentIndex(max(0, index))

    def _selected_nomachine_link_quality(self) -> int:
        value = self.nomachine_link_quality_input.currentData()
        return normalize_nomachine_quality(value, default=5)

    def _selected_nomachine_video_quality(self) -> int:
        value = self.nomachine_video_quality_input.currentData()
        return normalize_nomachine_quality(value, default=5)

    def _set_nomachine_link_quality(self, value: int) -> None:
        normalized = normalize_nomachine_quality(value, default=5)
        index = self.nomachine_link_quality_input.findData(normalized)
        if index < 0:
            index = self.nomachine_link_quality_input.findData(5)
        self.nomachine_link_quality_input.setCurrentIndex(max(0, index))

    def _set_nomachine_video_quality(self, value: int) -> None:
        normalized = normalize_nomachine_quality(value, default=5)
        index = self.nomachine_video_quality_input.findData(normalized)
        if index < 0:
            index = self.nomachine_video_quality_input.findData(5)
        self.nomachine_video_quality_input.setCurrentIndex(max(0, index))

    def _update_nomachine_audio_controls(self) -> None:
        enabled = self.nomachine_audio_enabled_input.isChecked()
        self.nomachine_mute_remote_audio_input.setEnabled(enabled)

    def _open_ssh_tunneling_dialog(self) -> None:
        dialog = SSHTunnelingDialog(
            self,
            dynamic_tunnels=self._ssh_dynamic_tunnels,
            static_tunnels=self._ssh_static_tunnels,
        )
        if not dialog.exec():
            return
        self._ssh_dynamic_tunnels = dialog.dynamic_tunnels()
        self._ssh_static_tunnels = dialog.static_tunnels()
        self._update_ssh_tunnel_summary()

    def _open_automation_dialog(self) -> None:
        dialog = AutomatedScriptingDialog(self, steps=self._ssh_automation_steps)
        if not dialog.exec():
            return
        self._ssh_automation_steps = dialog.steps()
        self._update_automation_summary()

    def _update_automation_summary(self) -> None:
        total = len(self._ssh_automation_steps)
        enabled = self.ssh_automation_enabled_input.isChecked()
        state = "Enabled" if enabled else "Disabled"
        self.ssh_automation_summary.setText(f"{state}, {total} step(s) configured")

    def _update_ssh_tunnel_summary(self) -> None:
        dynamic_enabled = sum(1 for tunnel in self._ssh_dynamic_tunnels if tunnel.enabled)
        static_enabled = sum(1 for tunnel in self._ssh_static_tunnels if tunnel.enabled)
        dynamic_total = len(self._ssh_dynamic_tunnels)
        static_total = len(self._ssh_static_tunnels)
        self.ssh_tunnel_summary.setText(
            f"Dynamic: {dynamic_enabled}/{dynamic_total} enabled, "
            f"Static: {static_enabled}/{static_total} enabled"
        )
