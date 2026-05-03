from __future__ import annotations

from collections.abc import Callable

from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from snakesh.services.mtu_tools import (
    MTU_OVERHEAD_PRESETS,
    MtuCalculationRequest,
    MtuCalculationResult,
    calculate_mtu,
    format_mtu_summary,
)


class MTUCalculatorDialog(QDialog):
    def __init__(
        self,
        *,
        on_send_to_ping: Callable[[int, bool], None] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._on_send_to_ping = on_send_to_ping
        self._current_result: MtuCalculationResult | None = None

        self.setWindowTitle("MTU / MSS Calculator")
        self.resize(720, 420)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        intro = QLabel(
            "Estimate effective MTU, Ping payload, UDP payload, and TCP MSS after extra overhead."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        form_container = QWidget(self)
        form = QFormLayout(form_container)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(6)

        self.outer_mtu_input = QSpinBox()
        self.outer_mtu_input.setRange(1, 100000)
        self.outer_mtu_input.setValue(1500)
        form.addRow("Outer / Interface MTU", self.outer_mtu_input)

        self.ip_version_input = QComboBox()
        self.ip_version_input.addItem("IPv4", 4)
        self.ip_version_input.addItem("IPv6", 6)
        form.addRow("IP Version", self.ip_version_input)

        self.overhead_preset_input = QComboBox()
        for preset in MTU_OVERHEAD_PRESETS:
            self.overhead_preset_input.addItem(preset.label, preset.preset_id)
        form.addRow("Extra Overhead Preset", self.overhead_preset_input)

        self.custom_overhead_input = QSpinBox()
        self.custom_overhead_input.setRange(0, 100000)
        self.custom_overhead_input.setEnabled(False)
        form.addRow("Custom Extra Bytes", self.custom_overhead_input)
        root.addWidget(form_container)

        note = QLabel(
            "Extra overhead is subtracted before IP/transport calculations. Real path MTU may still be lower."
        )
        note.setWordWrap(True)
        root.addWidget(note)

        result_container = QWidget(self)
        result_form = QFormLayout(result_container)
        result_form.setContentsMargins(0, 0, 0, 0)
        result_form.setSpacing(6)

        self.effective_mtu_output = self._create_output_field()
        self.overhead_output = self._create_output_field()
        self.ping_payload_output = self._create_output_field()
        self.udp_payload_output = self._create_output_field()
        self.tcp_mss_output = self._create_output_field()
        result_form.addRow("Effective MTU", self.effective_mtu_output)
        result_form.addRow("Total Extra Overhead", self.overhead_output)
        result_form.addRow("Max Ping Payload", self.ping_payload_output)
        result_form.addRow("Max UDP Payload", self.udp_payload_output)
        result_form.addRow("TCP MSS (no options)", self.tcp_mss_output)
        root.addWidget(result_container)

        actions = QHBoxLayout()
        self.copy_btn = QPushButton("Copy Summary")
        self.send_to_ping_btn = QPushButton("Send to Ping")
        actions.addWidget(self.copy_btn)
        actions.addWidget(self.send_to_ping_btn)
        actions.addStretch(1)
        root.addLayout(actions)

        self.status_label = QLabel("Ready.")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        close_btn = buttons.button(QDialogButtonBox.Close)
        if close_btn is not None:
            close_btn.clicked.connect(self.accept)
        root.addWidget(buttons)

        self.outer_mtu_input.valueChanged.connect(self._recalculate)
        self.ip_version_input.currentIndexChanged.connect(self._recalculate)
        self.overhead_preset_input.currentIndexChanged.connect(self._on_preset_changed)
        self.custom_overhead_input.valueChanged.connect(self._recalculate)
        self.copy_btn.clicked.connect(self._copy_summary)
        self.send_to_ping_btn.clicked.connect(self._send_to_ping)

        self._recalculate()

    @staticmethod
    def _create_output_field() -> QLineEdit:
        field = QLineEdit()
        field.setReadOnly(True)
        families = QFontDatabase.families()
        preferred = ("Cascadia Mono", "Consolas", "Courier New", "Courier")
        family = next((name for name in preferred if name in families), "")
        if family:
            font = field.font()
            font.setFamily(family)
            field.setFont(font)
        return field

    def _request(self) -> MtuCalculationRequest:
        return MtuCalculationRequest(
            outer_mtu=self.outer_mtu_input.value(),
            ip_version=int(self.ip_version_input.currentData()),
            overhead_preset_id=str(self.overhead_preset_input.currentData() or "none"),
            custom_overhead=self.custom_overhead_input.value(),
        )

    def _on_preset_changed(self) -> None:
        preset_id = str(self.overhead_preset_input.currentData() or "none")
        self.custom_overhead_input.setEnabled(preset_id == "custom")
        self._recalculate()

    def _recalculate(self) -> None:
        try:
            result = calculate_mtu(self._request())
        except ValueError as exc:
            self._current_result = None
            self._clear_outputs()
            self.copy_btn.setEnabled(False)
            self.send_to_ping_btn.setEnabled(False)
            self.status_label.setStyleSheet("color: #991b1b;")
            self.status_label.setText(str(exc))
            return

        self._current_result = result
        self.effective_mtu_output.setText(str(result.effective_mtu))
        self.overhead_output.setText(str(result.extra_overhead_bytes))
        self.ping_payload_output.setText(str(result.max_ping_payload))
        self.udp_payload_output.setText(str(result.max_udp_payload))
        self.tcp_mss_output.setText(str(result.tcp_mss) if result.tcp_mss is not None else "N/A")
        self.copy_btn.setEnabled(True)
        self.send_to_ping_btn.setEnabled(True)

        if result.advisories:
            self.status_label.setStyleSheet("color: #92400e;")
            self.status_label.setText(" ".join(result.advisories))
        else:
            self.status_label.setStyleSheet("color: #166534;")
            self.status_label.setText(f"Calculated IPv{result.ip_version} values for MTU {result.outer_mtu}.")

    def _clear_outputs(self) -> None:
        for field in (
            self.effective_mtu_output,
            self.overhead_output,
            self.ping_payload_output,
            self.udp_payload_output,
            self.tcp_mss_output,
        ):
            field.clear()

    def _copy_summary(self) -> None:
        if self._current_result is None:
            return
        QApplication.clipboard().setText(format_mtu_summary(self._current_result))
        self.status_label.setStyleSheet("color: #166534;")
        self.status_label.setText("Summary copied to clipboard.")

    def _send_to_ping(self) -> None:
        if self._current_result is None or self._on_send_to_ping is None:
            return
        self._on_send_to_ping(self._current_result.max_ping_payload, self._current_result.ip_version == 6)
        self.status_label.setStyleSheet("color: #166534;")
        self.status_label.setText("Ping prefilled with the calculated packet size.")
