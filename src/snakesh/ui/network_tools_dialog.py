from __future__ import annotations

from collections.abc import Callable
import ipaddress
from pathlib import Path
import shlex
import shutil
import tempfile
import threading
import time

from PySide6.QtCore import QObject, QProcess, QSettings, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QAction, QCloseEvent, QFontDatabase, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from snakesh.services.mtr_trace import (
    MTR_PROTOCOL_AUTO,
    MTR_PROTOCOL_ICMP,
    MTR_PROTOCOL_UDP,
    MTRProbeSample,
    MTR_TABLE_HEADERS,
    MTRTraceRequest,
    MTRTraceSnapshot,
    format_mtr_report,
    launch_mtr_helper,
    launch_mtr_helper_elevated,
    mtr_helper_session_paths,
    needs_mtr_helper_elevation,
    read_mtr_probe_samples,
    read_mtr_snapshot,
    supports_mtr_fast_mode,
    write_mtr_config,
)
from snakesh.services.settings_service import AppSettings
from snakesh.services.network_tools import (
    ASNLookupRequest,
    ASNLookupResult,
    DNSLookupRequest,
    DNS_RECORD_TYPES,
    IPScanHostResult,
    IPScanPortResult,
    IPScanProgress,
    IPScanRequest,
    IPScanResult,
    IP_SCAN_DEFAULT_TIMEOUT_MS,
    IP_SCAN_PRESET_COMMON_20,
    IP_SCAN_PRESET_COMMON_100,
    IP_SCAN_PRESET_CUSTOM,
    PingRequest,
    WhoisLookupRequest,
    build_ping_command,
    format_asn_result,
    format_dns_result,
    format_whois_result,
    perform_asn_lookup,
    perform_dns_lookup,
    perform_ip_scan,
    perform_whois_lookup,
)
from snakesh.ui.theme import apply_terminal_output_font
from snakesh.ui.traceroute_graph_dialog import TracerouteGraphDialog


_RUNTIME_BADGE_STYLES = {
    "running": ("Running", "#166534"),
    "starting": ("Starting", "#92400e"),
    "stopping": ("Stopping", "#92400e"),
    "stopped": ("Stopped", "#b91c1c"),
}


class _SortableTreeWidgetItem(QTreeWidgetItem):
    """QTreeWidgetItem that sorts IP addresses and numeric values correctly."""

    def __lt__(self, other: QTreeWidgetItem) -> bool:
        tree = self.treeWidget()
        col = tree.sortColumn() if tree else 0
        a = self.text(col)
        b = other.text(col)
        try:
            return int(ipaddress.ip_address(a)) < int(ipaddress.ip_address(b))
        except ValueError:
            pass
        try:
            return float(a) < float(b)
        except ValueError:
            return a < b


class _TaskWorker(QObject):
    succeeded = Signal(str)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, task: Callable[[], str]) -> None:
        super().__init__()
        self._task = task

    @Slot()
    def run(self) -> None:
        try:
            payload = self._task()
            self.succeeded.emit(payload)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()


def _apply_monospace_font(widget: QPlainTextEdit, settings: AppSettings | None = None) -> None:
    if isinstance(settings, AppSettings):
        apply_terminal_output_font(widget, settings)
        return
    families = QFontDatabase.families()
    preferred = ("Cascadia Mono", "Consolas", "Courier New", "DejaVu Sans Mono", "Courier")
    family = next((name for name in preferred if name in families), "")
    if not family:
        return
    font = widget.font()
    font.setFamily(family)
    widget.setFont(font)


class _ProcessToolDialogBase(QDialog):
    def __init__(self, title: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(980, 680)

        self._process = QProcess(self)
        self._process.readyReadStandardOutput.connect(self._read_stdout)
        self._process.readyReadStandardError.connect(self._read_stderr)
        self._process.errorOccurred.connect(self._on_process_error)
        self._process.finished.connect(self._on_process_finished)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        self._options_container = QWidget(self)
        self.options_form = QFormLayout(self._options_container)
        self.options_form.setContentsMargins(0, 0, 0, 0)
        self.options_form.setSpacing(6)
        root.addWidget(self._options_container)

        button_row = QHBoxLayout()
        self.run_btn = QPushButton("Run")
        self.stop_btn = QPushButton("Stop")
        self.clear_btn = QPushButton("Clear")
        self.stop_btn.setEnabled(False)
        button_row.addWidget(self.run_btn)
        button_row.addWidget(self.stop_btn)
        button_row.addWidget(self.clear_btn)
        button_row.addStretch(1)
        root.addLayout(button_row)

        self.command_label = QLabel("Command: (not started)")
        self.command_label.setWordWrap(True)
        root.addWidget(self.command_label)

        self.output = QPlainTextEdit(self)
        self.output.setReadOnly(True)
        _apply_monospace_font(self.output)
        root.addWidget(self.output, 1)

        self.status_label = QLabel("Ready.")
        root.addWidget(self.status_label)

        self.stop_btn.clicked.connect(self._stop_process)
        self.clear_btn.clicked.connect(self.output.clear)

    def apply_runtime_settings(self, settings: AppSettings) -> None:
        _apply_monospace_font(self.output, settings)

    def _set_running(self, running: bool) -> None:
        self.run_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)

    def _append_output(self, text: str) -> None:
        cleaned = text.replace("\r\n", "\n").replace("\r", "\n")
        if not cleaned:
            return
        self.output.moveCursor(QTextCursor.End)
        self.output.insertPlainText(cleaned)
        self.output.moveCursor(QTextCursor.End)

    def _start_process(self, command: list[str]) -> None:
        if not command:
            self.status_label.setText("No command to run.")
            return
        if self._process.state() != QProcess.NotRunning:
            return

        executable = command[0]
        if shutil.which(executable) is None:
            self.status_label.setText(f"{executable} is not available.")
            self._append_output(f"[Error] Required command not found: {executable}\n")
            return

        quoted = " ".join(shlex.quote(part) for part in command)
        self.command_label.setText(f"Command: {quoted}")
        self._append_output(f"$ {quoted}\n")

        self._process.setProgram(executable)
        self._process.setArguments(command[1:])
        self._process.start()
        if not self._process.waitForStarted(2000):
            self.status_label.setText("Unable to start process.")
            self._append_output("[Error] Failed to start process.\n")
            return

        self._set_running(True)
        self.status_label.setText("Running...")

    def _stop_process(self) -> None:
        if self._process.state() == QProcess.NotRunning:
            return
        self.status_label.setText("Stopping...")
        self._append_output("[Info] Stopping process...\n")
        self._process.terminate()
        QTimer.singleShot(1500, self._kill_process_if_running)

    def _kill_process_if_running(self) -> None:
        if self._process.state() == QProcess.NotRunning:
            return
        self._process.kill()

    def _read_stdout(self) -> None:
        data = bytes(self._process.readAllStandardOutput())
        if data:
            self._append_output(data.decode("utf-8", errors="replace"))

    def _read_stderr(self) -> None:
        data = bytes(self._process.readAllStandardError())
        if data:
            self._append_output(data.decode("utf-8", errors="replace"))

    def _on_process_error(self, _error: QProcess.ProcessError) -> None:
        self._set_running(False)
        self.status_label.setText("Process failed.")

    def _on_process_finished(self, exit_code: int, _status: QProcess.ExitStatus) -> None:
        self._set_running(False)
        self.status_label.setText(f"Finished (exit code {exit_code}).")
        self._append_output(f"\n[Process exited with code {exit_code}]\n")

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self._process.state() != QProcess.NotRunning:
            self._process.kill()
            self._process.waitForFinished(1000)
        super().closeEvent(event)


class PingToolDialog(_ProcessToolDialogBase):
    def __init__(self, parent=None) -> None:
        super().__init__("Ping", parent=parent)

        self.target_input = QLineEdit("8.8.8.8")
        self.target_input.setPlaceholderText("Host or IP address")
        self.count_input = QSpinBox()
        self.count_input.setRange(1, 1000)
        self.count_input.setValue(4)
        self.timeout_input = QSpinBox()
        self.timeout_input.setRange(100, 30000)
        self.timeout_input.setSingleStep(100)
        self.timeout_input.setSuffix(" ms")
        self.timeout_input.setValue(1000)
        self.size_input = QSpinBox()
        self.size_input.setRange(1, 65000)
        self.size_input.setValue(56)
        self.ipv6_check = QCheckBox("Use IPv6")

        self.options_form.addRow(QLabel("Target"), self.target_input)
        self.options_form.addRow(QLabel("Count"), self.count_input)
        self.options_form.addRow(QLabel("Timeout per probe"), self.timeout_input)
        self.options_form.addRow(QLabel("Packet size"), self.size_input)
        self.options_form.addRow(QLabel("IP mode"), self.ipv6_check)

        self.run_btn.clicked.connect(self._run_ping)
        self.target_input.returnPressed.connect(self._run_ping)

    def apply_prefill(self, packet_size: int | None = None, ipv6: bool | None = None) -> None:
        if packet_size is not None:
            bounded = max(self.size_input.minimum(), min(self.size_input.maximum(), int(packet_size)))
            self.size_input.setValue(bounded)
        if ipv6 is not None:
            self.ipv6_check.setChecked(bool(ipv6))

    def _run_ping(self) -> None:
        request = PingRequest(
            target=self.target_input.text(),
            count=self.count_input.value(),
            timeout_ms=self.timeout_input.value(),
            packet_size=self.size_input.value(),
            ipv6=self.ipv6_check.isChecked(),
        )
        try:
            command = build_ping_command(request)
        except ValueError as exc:
            self.status_label.setText(str(exc))
            self._append_output(f"[Error] {exc}\n")
            return
        self._start_process(command)


class TracerouteToolDialog(QDialog):
    def __init__(self, parent=None) -> None:
        QDialog.__init__(self, parent)
        self.setWindowTitle("Traceroute")
        self.resize(1040, 760)
        self.setMinimumSize(760, 520)

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(250)
        self._poll_timer.timeout.connect(self._poll_helper_state)
        self._session_dir: Path | None = None
        self._last_snapshot: MTRTraceSnapshot | None = None
        self._active_request: MTRTraceRequest | None = None
        self._probe_samples: list[MTRProbeSample] = []
        self._graph_dialog: TracerouteGraphDialog | None = None
        self._running = False
        self._pending_reset = False
        self._helper_launch_started_at = 0.0
        self._trace_column_widths_initialized = False
        self._helper_is_elevated = False
        self._fast_mode_supported = supports_mtr_fast_mode()

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        form = QFormLayout()
        self.target_input = QLineEdit("8.8.8.8")
        self.target_input.setPlaceholderText("Host or IP address")
        self.max_hops_input = QSpinBox()
        self.max_hops_input.setRange(1, 255)
        self.max_hops_input.setValue(30)
        self.timeout_input = QSpinBox()
        self.timeout_input.setRange(100, 30000)
        self.timeout_input.setSingleStep(100)
        self.timeout_input.setValue(3000)
        self.timeout_input.setSuffix(" ms")
        self.interval_input = QSpinBox()
        self.interval_input.setRange(100, 60000)
        self.interval_input.setSingleStep(100)
        self.interval_input.setValue(1000)
        self.interval_input.setSuffix(" ms")
        self.cycles_choice_input = QComboBox()
        self.cycles_choice_input.addItem("Run until stopped", 0)
        for cycles in range(1, 11):
            self.cycles_choice_input.addItem(str(cycles), cycles)
        self.cycles_choice_input.addItem("Custom", "custom")
        self.cycles_custom_input = QSpinBox()
        self.cycles_custom_input.setRange(1, 1000000)
        self.cycles_custom_input.setValue(11)
        self.cycles_custom_input.setMinimumWidth(110)
        self.cycles_custom_input.hide()
        cycles_widget = QWidget(self)
        cycles_layout = QHBoxLayout(cycles_widget)
        cycles_layout.setContentsMargins(0, 0, 0, 0)
        cycles_layout.setSpacing(8)
        cycles_layout.addWidget(self.cycles_choice_input, 1)
        cycles_layout.addWidget(self.cycles_custom_input)
        self.protocol_input = QComboBox()
        self.protocol_input.addItem("Auto", MTR_PROTOCOL_AUTO)
        self.protocol_input.addItem(MTR_PROTOCOL_ICMP, MTR_PROTOCOL_ICMP)
        self.protocol_input.addItem(MTR_PROTOCOL_UDP, MTR_PROTOCOL_UDP)
        self.fast_mode_check = QCheckBox("Fast native probing (requires escalation)")
        if not self._fast_mode_supported:
            self.fast_mode_check.setText("Fast native probing (not available on Windows)")
            self.fast_mode_check.setToolTip("Disabled on Windows builds.")
            self.fast_mode_check.setChecked(False)
            self.fast_mode_check.setEnabled(False)
        self.resolve_names_check = QCheckBox("Resolve hostnames")
        self.resolve_names_check.setChecked(True)
        self.ipv6_check = QCheckBox("Use IPv6")

        form.addRow(QLabel("Target"), self.target_input)
        form.addRow(QLabel("Max hops"), self.max_hops_input)
        form.addRow(QLabel("Timeout per probe"), self.timeout_input)
        form.addRow(QLabel("Interval"), self.interval_input)
        form.addRow(QLabel("Cycles"), cycles_widget)
        form.addRow(QLabel("Protocol"), self.protocol_input)
        form.addRow(QLabel("Engine"), self.fast_mode_check)
        form.addRow(QLabel("Name resolution"), self.resolve_names_check)
        form.addRow(QLabel("IP mode"), self.ipv6_check)
        root.addLayout(form)

        button_row = QHBoxLayout()
        self.start_btn = QPushButton("Start")
        self.stop_btn = QPushButton("Stop")
        self.reset_btn = QPushButton("Reset Stats")
        self.graph_btn = QPushButton("Graph")
        self.copy_report_btn = QPushButton("Copy Report")
        button_row.addWidget(self.start_btn)
        button_row.addWidget(self.stop_btn)
        button_row.addWidget(self.reset_btn)
        button_row.addStretch(1)
        button_row.addWidget(self.graph_btn)
        button_row.addWidget(self.copy_report_btn)
        root.addLayout(button_row)

        self.trace_tree = QTreeWidget(self)
        self.trace_tree.setColumnCount(len(MTR_TABLE_HEADERS))
        self.trace_tree.setHeaderLabels(list(MTR_TABLE_HEADERS))
        self.trace_tree.setAlternatingRowColors(True)
        self.trace_tree.setRootIsDecorated(False)
        self.trace_tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.trace_tree.setSortingEnabled(True)
        self.trace_tree.setTextElideMode(Qt.ElideMiddle)
        self.trace_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.trace_tree.header().setSortIndicatorShown(True)
        self.trace_tree.header().setSectionsClickable(True)
        self.trace_tree.header().setContextMenuPolicy(Qt.CustomContextMenu)
        self.trace_tree.header().setSortIndicator(0, Qt.AscendingOrder)
        self.trace_tree.sortItems(0, Qt.AscendingOrder)
        root.addWidget(self.trace_tree, 1)

        self.status_label = QLabel("Ready.")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        self.start_btn.clicked.connect(self._start_trace)
        self.stop_btn.clicked.connect(self._stop_trace)
        self.reset_btn.clicked.connect(self._reset_trace)
        self.graph_btn.clicked.connect(self._show_graph_dialog)
        self.copy_report_btn.clicked.connect(self._copy_report)
        self.target_input.returnPressed.connect(self._start_trace)
        self.cycles_choice_input.currentIndexChanged.connect(self._on_cycles_choice_changed)
        self.trace_tree.customContextMenuRequested.connect(self._show_trace_tree_context_menu)
        self.trace_tree.header().customContextMenuRequested.connect(self._show_trace_header_context_menu)

        self._on_cycles_choice_changed(self.cycles_choice_input.currentIndex())
        self._initialize_trace_columns()
        self._set_running(False)

    def _set_running(self, running: bool) -> None:
        self._running = running
        self.start_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)
        for widget in (
            self.target_input,
            self.max_hops_input,
            self.timeout_input,
            self.interval_input,
            self.cycles_choice_input,
            self.protocol_input,
            self.resolve_names_check,
            self.ipv6_check,
        ):
            widget.setEnabled(not running)
        self.fast_mode_check.setEnabled(not running and self._fast_mode_supported)
        self.cycles_custom_input.setEnabled(not running and self._cycles_choice_data() == "custom")
        self.graph_btn.setEnabled(bool(self._probe_samples))
        self.copy_report_btn.setEnabled(self._last_snapshot is not None and bool(self._last_snapshot.hops))

    def _build_request(self) -> MTRTraceRequest:
        return MTRTraceRequest(
            target=self.target_input.text(),
            max_hops=self.max_hops_input.value(),
            timeout_ms=self.timeout_input.value(),
            interval_ms=self.interval_input.value(),
            cycles=self._selected_cycles_value(),
            protocol=str(self.protocol_input.currentData() or MTR_PROTOCOL_AUTO),
            resolve_hostnames=self.resolve_names_check.isChecked(),
            ipv6=self.ipv6_check.isChecked(),
            fast_mode=self._fast_mode_supported and self.fast_mode_check.isChecked(),
        ).normalized()

    def _cycles_choice_data(self) -> int | str:
        return self.cycles_choice_input.currentData()

    def _selected_cycles_value(self) -> int:
        selected = self._cycles_choice_data()
        if selected == "custom":
            return self.cycles_custom_input.value()
        return int(selected or 0)

    def _on_cycles_choice_changed(self, index: int) -> None:
        selected = self.cycles_choice_input.itemData(index)
        is_custom = selected == "custom"
        self.cycles_custom_input.setVisible(is_custom)
        self.cycles_custom_input.setEnabled(is_custom and not self._running)

    def _ensure_trace_session_dir(self) -> Path:
        if self._session_dir is None:
            self._session_dir = Path(tempfile.mkdtemp(prefix="snakesh-mtr-"))
        return self._session_dir

    def _trace_helper_ready(self) -> bool:
        return self._session_dir is not None and mtr_helper_session_paths(self._session_dir).ready_path.exists()

    def _launch_trace_helper(self, request: MTRTraceRequest) -> None:
        if self._session_dir is None:
            raise RuntimeError("Traceroute helper session is not initialized.")
        if needs_mtr_helper_elevation(request):
            launch_mtr_helper_elevated(self._session_dir)
            self._helper_is_elevated = True
            return
        launch_mtr_helper(self._session_dir)
        self._helper_is_elevated = False

    def _shutdown_trace_helper_session(self, *, cleanup_session: bool) -> None:
        session_dir = self._session_dir
        if session_dir is None:
            return
        paths = mtr_helper_session_paths(session_dir)
        try:
            paths.root.mkdir(parents=True, exist_ok=True)
            paths.shutdown_path.write_text("shutdown\n", encoding="utf-8")
        except Exception:
            pass
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if not paths.ready_path.exists():
                break
            QApplication.processEvents()
            time.sleep(0.05)
        if cleanup_session:
            shutil.rmtree(session_dir, ignore_errors=True)
            self._session_dir = None
            self._helper_is_elevated = False

    def _prepare_trace_helper_session(self, request: MTRTraceRequest) -> Path:
        requires_elevation = needs_mtr_helper_elevation(request)
        if self._session_dir is None:
            return self._ensure_trace_session_dir()
        if requires_elevation and not self._helper_is_elevated:
            self._shutdown_trace_helper_session(cleanup_session=True)
            return self._ensure_trace_session_dir()
        return self._session_dir

    def _start_trace(self) -> None:
        if self._running:
            return
        try:
            request = self._build_request()
        except ValueError as exc:
            self.status_label.setText(str(exc))
            return

        self._pending_reset = False
        self._clear_trace_data(cleanup_session=False)
        try:
            session_dir = self._prepare_trace_helper_session(request)
            write_mtr_config(session_dir, request, persistent=True)
            if not self._trace_helper_ready():
                self._launch_trace_helper(request)
        except Exception as exc:  # noqa: BLE001
            self.status_label.setText(str(exc))
            if not self._trace_helper_ready():
                self._shutdown_trace_helper_session(cleanup_session=True)
            return

        self._session_dir = session_dir
        self._active_request = request
        self._helper_launch_started_at = time.monotonic()
        self.status_label.setText("Starting traceroute...")
        self._set_running(True)
        self._poll_timer.start()
        self._poll_helper_state()

    def _stop_trace(self) -> None:
        if self._session_dir is None:
            return
        paths = mtr_helper_session_paths(self._session_dir)
        try:
            paths.stop_path.write_text("stop\n", encoding="utf-8")
        except Exception:
            self.status_label.setText("Unable to signal the traceroute helper to stop.")
            return
        self.stop_btn.setEnabled(False)
        self.status_label.setText("Stopping traceroute...")

    def _reset_trace(self) -> None:
        if self._running:
            self._pending_reset = True
            self._stop_trace()
            return
        self._clear_trace_data(cleanup_session=False)
        self.status_label.setText("Ready.")

    def _clear_trace_data(self, *, cleanup_session: bool = True) -> None:
        self._poll_timer.stop()
        self._set_running(False)
        self.trace_tree.clear()
        self._last_snapshot = None
        self._active_request = None
        self._probe_samples = []
        self._helper_launch_started_at = 0.0
        if cleanup_session:
            self._shutdown_trace_helper_session(cleanup_session=True)
        if self._graph_dialog is not None:
            self._graph_dialog.clear_trace_data()
        self.graph_btn.setEnabled(False)
        self.copy_report_btn.setEnabled(False)

    @staticmethod
    def _filter_probe_samples_for_snapshot(
        snapshot: MTRTraceSnapshot | None,
        samples: list[MTRProbeSample],
    ) -> list[MTRProbeSample]:
        if snapshot is None:
            return list(samples)
        destination_hop = min((hop.hop for hop in snapshot.hops if hop.reached_destination), default=None)
        if destination_hop is None:
            return list(samples)
        return [sample for sample in samples if sample.hop <= destination_hop]

    def _poll_helper_state(self) -> None:
        if self._session_dir is None:
            self._poll_timer.stop()
            self._set_running(False)
            return

        snapshot = read_mtr_snapshot(self._session_dir)
        probe_samples = read_mtr_probe_samples(self._session_dir)
        ready_exists = mtr_helper_session_paths(self._session_dir).ready_path.exists()

        if snapshot is not None:
            self._last_snapshot = snapshot
            self._populate_snapshot(snapshot)
            self.status_label.setText(snapshot.message or self._status_text_for_state(snapshot.state))
            self.copy_report_btn.setEnabled(bool(snapshot.hops))
        self._probe_samples = self._filter_probe_samples_for_snapshot(snapshot, probe_samples)
        self.graph_btn.setEnabled(bool(self._probe_samples))
        self._sync_graph_dialog()

        if self._running and snapshot is not None and snapshot.state in {"completed", "stopped", "error"}:
            self._poll_timer.stop()
            self._set_running(False)
            if self._pending_reset:
                self._pending_reset = False
                self._clear_trace_data(cleanup_session=False)
                self.status_label.setText("Ready.")
            return

        if not ready_exists and self._running:
            if snapshot is None and self._last_snapshot is None and (time.monotonic() - self._helper_launch_started_at) < 5.0:
                return
            self._poll_timer.stop()
            self._set_running(False)
            if snapshot is None and self._last_snapshot is None:
                self.status_label.setText("Traceroute helper exited without reporting state.")
            elif snapshot is None and self._last_snapshot is not None:
                self.status_label.setText(
                    self._last_snapshot.message or self._status_text_for_state(self._last_snapshot.state)
                )
            if self._pending_reset:
                self._pending_reset = False
                self._clear_trace_data()
                self.status_label.setText("Ready.")

    def _populate_snapshot(self, snapshot: MTRTraceSnapshot) -> None:
        self.trace_tree.setSortingEnabled(False)
        self.trace_tree.clear()
        for hop in snapshot.hops:
            item = _SortableTreeWidgetItem(
                [
                    str(hop.hop),
                    hop.host,
                    hop.address,
                    self._format_metric(hop.loss_percent),
                    str(hop.sent),
                    str(hop.received),
                    self._format_optional_metric(hop.last_ms),
                    self._format_optional_metric(hop.avg_ms),
                    self._format_optional_metric(hop.best_ms),
                    self._format_optional_metric(hop.worst_ms),
                    self._format_optional_metric(hop.stdev_ms),
                ]
            )
            item.setToolTip(1, hop.host)
            item.setToolTip(2, hop.address)
            self.trace_tree.addTopLevelItem(item)
        self.trace_tree.setSortingEnabled(True)
        header = self.trace_tree.header()
        self.trace_tree.sortItems(header.sortIndicatorSection(), header.sortIndicatorOrder())

    def _initialize_trace_columns(self) -> None:
        header = self.trace_tree.header()
        header.setStretchLastSection(False)
        for index in range(self.trace_tree.columnCount()):
            header.setSectionResizeMode(index, QHeaderView.Interactive)
        if self._trace_column_widths_initialized:
            return
        default_widths = {
            0: 70,
            1: 200,
            2: 200,
            3: 70,
            4: 70,
            5: 70,
            6: 80,
            7: 80,
            8: 80,
            9: 80,
            10: 80,
        }
        for index, width in default_widths.items():
            self.trace_tree.setColumnWidth(index, width)
        self._trace_column_widths_initialized = True

    def _show_graph_dialog(self) -> None:
        if not self._probe_samples:
            self.status_label.setText("No traceroute probe history is available to graph.")
            return
        if self._graph_dialog is None:
            self._graph_dialog = TracerouteGraphDialog(self)
        self._sync_graph_dialog()
        self._graph_dialog.show()
        self._graph_dialog.raise_()
        self._graph_dialog.activateWindow()

    def _sync_graph_dialog(self) -> None:
        if self._graph_dialog is None:
            return
        self._graph_dialog.set_trace_data(self._last_snapshot, self._probe_samples)

    @staticmethod
    def _format_metric(value: float) -> str:
        return f"{value:.1f}"

    @classmethod
    def _format_optional_metric(cls, value: float | None) -> str:
        if value is None:
            return ""
        return cls._format_metric(value)

    @staticmethod
    def _status_text_for_state(state: str) -> str:
        mapping = {
            "completed": "Trace complete.",
            "stopped": "Trace stopped.",
            "error": "Trace failed.",
            "starting": "Starting traceroute...",
            "running": "Traceroute running...",
        }
        return mapping.get(state, "Traceroute ready.")

    def _copy_report(self) -> None:
        if self._last_snapshot is None or not self._last_snapshot.hops:
            self.status_label.setText("No traceroute data to copy.")
            return
        if not self._set_clipboard_text(
            format_mtr_report(self._last_snapshot),
            "Traceroute report copied to the clipboard.",
        ):
            self.status_label.setText("Clipboard is unavailable.")

    def _set_clipboard_text(self, payload: str, success_message: str) -> bool:
        clipboard = QApplication.clipboard()
        if clipboard is None:
            return False
        clipboard.setText(payload)
        self.status_label.setText(success_message)
        return True

    def _trace_headers(self) -> list[str]:
        return [self.trace_tree.headerItem().text(index) for index in range(self.trace_tree.columnCount())]

    def _trace_item_values(self, item: QTreeWidgetItem) -> list[str]:
        return [item.text(index) for index in range(self.trace_tree.columnCount())]

    def _visible_trace_items(self) -> list[QTreeWidgetItem]:
        items: list[QTreeWidgetItem] = []
        for index in range(self.trace_tree.topLevelItemCount()):
            item = self.trace_tree.topLevelItem(index)
            if not item.isHidden():
                items.append(item)
        return items

    def _selected_trace_items(self) -> list[QTreeWidgetItem]:
        selected = set(self.trace_tree.selectedItems())
        return [item for item in self._visible_trace_items() if item in selected]

    def _copy_trace_cell(self, item: QTreeWidgetItem | None, column: int) -> None:
        if item is None or column < 0:
            self.status_label.setText("No traceroute cell selected to copy.")
            return
        if not self._set_clipboard_text(item.text(column), "Traceroute cell copied to the clipboard."):
            self.status_label.setText("Clipboard is unavailable.")

    def _copy_trace_row(self, item: QTreeWidgetItem | None) -> None:
        if item is None:
            self.status_label.setText("No traceroute row selected to copy.")
            return
        payload = "\n".join(
            "\t".join(row)
            for row in [self._trace_headers(), self._trace_item_values(item)]
        )
        if not self._set_clipboard_text(payload, "Traceroute row copied to the clipboard."):
            self.status_label.setText("Clipboard is unavailable.")

    def _copy_trace_selected_rows(self) -> None:
        selected_rows = self._selected_trace_items()
        if not selected_rows:
            self.status_label.setText("Select one or more traceroute rows to copy.")
            return
        payload = "\n".join(
            "\t".join(row)
            for row in [self._trace_headers(), *[self._trace_item_values(item) for item in selected_rows]]
        )
        noun = "row" if len(selected_rows) == 1 else "rows"
        if not self._set_clipboard_text(payload, f"Copied {len(selected_rows)} traceroute {noun} to the clipboard."):
            self.status_label.setText("Clipboard is unavailable.")

    def _copy_trace_column(self, column: int) -> None:
        if column < 0 or column >= self.trace_tree.columnCount():
            self.status_label.setText("No traceroute column selected to copy.")
            return
        rows = [item.text(column) for item in self._visible_trace_items()]
        payload = "\n".join([self.trace_tree.headerItem().text(column), *rows])
        if not self._set_clipboard_text(
            payload,
            f"Traceroute column '{self.trace_tree.headerItem().text(column)}' copied.",
        ):
            self.status_label.setText("Clipboard is unavailable.")

    @Slot(object)
    def _show_trace_tree_context_menu(self, position) -> None:
        item = self.trace_tree.itemAt(position)
        column = self.trace_tree.columnAt(position.x())
        if item is None or column < 0:
            return
        menu, actions = self._build_trace_tree_context_menu(item, column)
        chosen = menu.exec(self.trace_tree.viewport().mapToGlobal(position))
        if chosen == actions["copy_cell"]:
            self._copy_trace_cell(item, column)
        elif chosen == actions["copy_row"]:
            self._copy_trace_row(item)
        elif chosen == actions["copy_selected_rows"]:
            self._copy_trace_selected_rows()
        elif chosen == actions["copy_column"]:
            self._copy_trace_column(column)

    @Slot(object)
    def _show_trace_header_context_menu(self, position) -> None:
        column = self.trace_tree.header().logicalIndexAt(position)
        if column < 0:
            return
        menu, actions = self._build_trace_header_context_menu(column)
        chosen = menu.exec(self.trace_tree.header().viewport().mapToGlobal(position))
        if chosen == actions["copy_column"]:
            self._copy_trace_column(column)

    def _build_trace_tree_context_menu(
        self,
        item: QTreeWidgetItem | None,
        column: int,
    ) -> tuple[QMenu, dict[str, QAction]]:
        menu = QMenu(self)
        actions = {
            "copy_cell": menu.addAction("Copy Cell"),
            "copy_row": menu.addAction("Copy Row"),
            "copy_selected_rows": menu.addAction("Copy Selected Rows"),
            "copy_column": menu.addAction("Copy Column"),
        }
        actions["copy_cell"].setEnabled(item is not None and column >= 0)
        actions["copy_row"].setEnabled(item is not None)
        actions["copy_selected_rows"].setEnabled(bool(self.trace_tree.selectedItems()))
        actions["copy_column"].setEnabled(item is not None and column >= 0 and bool(self._visible_trace_items()))
        return menu, actions

    def _build_trace_header_context_menu(self, column: int) -> tuple[QMenu, dict[str, QAction]]:
        menu = QMenu(self)
        actions = {
            "copy_column": menu.addAction("Copy Column"),
        }
        actions["copy_column"].setEnabled(column >= 0 and bool(self._visible_trace_items()))
        return menu, actions

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self._running:
            self._stop_trace()
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                self._poll_helper_state()
                if not self._running:
                    break
                QApplication.processEvents()
                time.sleep(0.05)
        if self._graph_dialog is not None:
            self._graph_dialog.close()
        self._clear_trace_data(cleanup_session=True)
        super().closeEvent(event)


class _LookupToolDialogBase(QDialog):
    def __init__(self, title: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(980, 680)

        self._task_thread: QThread | None = None
        self._task_worker: _TaskWorker | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        self._options_container = QWidget(self)
        self.options_form = QFormLayout(self._options_container)
        self.options_form.setContentsMargins(0, 0, 0, 0)
        self.options_form.setSpacing(6)
        root.addWidget(self._options_container)

        button_row = QHBoxLayout()
        self.run_btn = QPushButton("Run")
        self.clear_btn = QPushButton("Clear")
        button_row.addWidget(self.run_btn)
        button_row.addWidget(self.clear_btn)
        button_row.addStretch(1)
        root.addLayout(button_row)

        self.output = QPlainTextEdit(self)
        self.output.setReadOnly(True)
        _apply_monospace_font(self.output)
        root.addWidget(self.output, 1)

        self.status_label = QLabel("Ready.")
        root.addWidget(self.status_label)

        self.clear_btn.clicked.connect(self.output.clear)

    def apply_runtime_settings(self, settings: AppSettings) -> None:
        _apply_monospace_font(self.output, settings)

    def _append_output(self, text: str) -> None:
        cleaned = text.replace("\r\n", "\n").replace("\r", "\n")
        if not cleaned:
            return
        if not cleaned.endswith("\n"):
            cleaned += "\n"
        self.output.moveCursor(QTextCursor.End)
        self.output.insertPlainText(cleaned)
        self.output.moveCursor(QTextCursor.End)

    def _set_running(self, running: bool) -> None:
        self.run_btn.setEnabled(not running)

    def _start_task(self, task: Callable[[], str]) -> None:
        if self._task_thread is not None:
            return

        worker = _TaskWorker(task)
        thread = QThread(self)
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.succeeded.connect(self._on_task_success)
        worker.failed.connect(self._on_task_failure)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_task_thread_finished)

        self._task_thread = thread
        self._task_worker = worker
        self._set_running(True)
        self.status_label.setText("Running...")
        thread.start()

    @Slot(str)
    def _on_task_success(self, payload: str) -> None:
        self._append_output(payload)
        self.status_label.setText("Complete.")

    @Slot(str)
    def _on_task_failure(self, message: str) -> None:
        self._append_output(f"[Error] {message}")
        self.status_label.setText("Failed.")

    def _on_task_thread_finished(self) -> None:
        self._task_thread = None
        self._task_worker = None
        self._set_running(False)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self._task_thread is not None:
            QMessageBox.information(self, "Lookup Running", "Wait for the current lookup to finish.")
            event.ignore()
            return
        super().closeEvent(event)


class DigToolDialog(_LookupToolDialogBase):
    def __init__(self, parent=None) -> None:
        super().__init__("Dig", parent=parent)

        self.query_input = QLineEdit("example.com")
        self.query_input.setPlaceholderText("Hostname or domain")
        self.record_type_input = QComboBox()
        self.record_type_input.addItems(list(DNS_RECORD_TYPES))
        self.nameserver_input = QLineEdit("")
        self.nameserver_input.setPlaceholderText("Optional nameserver IP, example: 8.8.8.8")
        self.timeout_input = QSpinBox()
        self.timeout_input.setRange(100, 30000)
        self.timeout_input.setSingleStep(100)
        self.timeout_input.setSuffix(" ms")
        self.timeout_input.setValue(5000)
        self.use_tcp_check = QCheckBox("Use TCP")

        self.options_form.addRow(QLabel("Query"), self.query_input)
        self.options_form.addRow(QLabel("Record type"), self.record_type_input)
        self.options_form.addRow(QLabel("Nameserver"), self.nameserver_input)
        self.options_form.addRow(QLabel("Timeout"), self.timeout_input)
        self.options_form.addRow(QLabel("Transport"), self.use_tcp_check)

        self.run_btn.setText("Lookup")
        self.run_btn.clicked.connect(self._run_lookup)
        self.query_input.returnPressed.connect(self._run_lookup)

    def _run_lookup(self) -> None:
        request = DNSLookupRequest(
            query=self.query_input.text(),
            record_type=self.record_type_input.currentText(),
            nameserver=self.nameserver_input.text(),
            timeout_ms=self.timeout_input.value(),
            use_tcp=self.use_tcp_check.isChecked(),
        )
        self._append_output(
            f";; Lookup {request.record_type} {request.query.strip() or '(empty)'} "
            f"using {request.nameserver.strip() or 'system resolver'}"
        )
        self._start_task(lambda: format_dns_result(perform_dns_lookup(request)))


class WhoisToolDialog(_LookupToolDialogBase):
    def __init__(self, parent=None) -> None:
        super().__init__("Whois", parent=parent)

        self.query_input = QLineEdit("example.com")
        self.query_input.setPlaceholderText("Domain, IP address, or ASN")
        self.server_input = QLineEdit("")
        self.server_input.setPlaceholderText("Optional WHOIS server (example: whois.verisign-grs.com)")
        self.timeout_input = QSpinBox()
        self.timeout_input.setRange(500, 30000)
        self.timeout_input.setSingleStep(100)
        self.timeout_input.setSuffix(" ms")
        self.timeout_input.setValue(8000)
        self.follow_referral_check = QCheckBox("Follow referrals")
        self.follow_referral_check.setChecked(True)

        self.options_form.addRow(QLabel("Query"), self.query_input)
        self.options_form.addRow(QLabel("Server"), self.server_input)
        self.options_form.addRow(QLabel("Timeout"), self.timeout_input)
        self.options_form.addRow(QLabel("Referral handling"), self.follow_referral_check)

        self.run_btn.setText("Lookup")
        self.run_btn.clicked.connect(self._run_lookup)
        self.query_input.returnPressed.connect(self._run_lookup)

    def _run_lookup(self) -> None:
        request = WhoisLookupRequest(
            query=self.query_input.text(),
            server=self.server_input.text(),
            timeout_ms=self.timeout_input.value(),
            follow_referral=self.follow_referral_check.isChecked(),
        )
        self._append_output(
            f"# WHOIS {request.query.strip() or '(empty)'} via {request.server.strip() or 'whois.iana.org'}"
        )
        self._start_task(lambda: format_whois_result(perform_whois_lookup(request)))


class _ASNLookupWorker(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, request: ASNLookupRequest) -> None:
        super().__init__()
        self._request = request

    @Slot()
    def run(self) -> None:
        try:
            self.succeeded.emit(perform_asn_lookup(self._request))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()


class _IPScanWorker(QObject):
    progress = Signal(object)
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, request: IPScanRequest) -> None:
        super().__init__()
        self._request = request
        self._cancel_event = threading.Event()

    @Slot()
    def run(self) -> None:
        try:
            result = perform_ip_scan(
                self._request,
                progress_callback=self.progress.emit,
                cancel_event=self._cancel_event,
            )
            self.succeeded.emit(result)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()

    @Slot()
    def cancel(self) -> None:
        self._cancel_event.set()


class ASNLookupDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("ASN Lookup")
        self.resize(980, 720)

        self._thread: QThread | None = None
        self._worker: _ASNLookupWorker | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        form = QFormLayout()
        self.query_input = QLineEdit("AS15169")
        self.query_input.setPlaceholderText("ASN or AS number, for example AS15169 or 15169")
        self.server_input = QLineEdit("")
        self.server_input.setPlaceholderText("Optional WHOIS server (example: whois.ripe.net)")
        self.timeout_input = QSpinBox()
        self.timeout_input.setRange(500, 30000)
        self.timeout_input.setSingleStep(100)
        self.timeout_input.setSuffix(" ms")
        self.timeout_input.setValue(8000)
        self.follow_referral_check = QCheckBox("Follow referrals")
        self.follow_referral_check.setChecked(True)
        form.addRow(QLabel("ASN"), self.query_input)
        form.addRow(QLabel("Server"), self.server_input)
        form.addRow(QLabel("Timeout"), self.timeout_input)
        form.addRow(QLabel("Referral handling"), self.follow_referral_check)
        root.addLayout(form)

        button_row = QHBoxLayout()
        self.lookup_btn = QPushButton("Lookup")
        self.clear_btn = QPushButton("Clear")
        button_row.addWidget(self.lookup_btn)
        button_row.addWidget(self.clear_btn)
        button_row.addStretch(1)
        root.addLayout(button_row)

        summary_form = QFormLayout()
        self.asn_label = QLabel("(not looked up yet)")
        self.as_name_label = QLabel("")
        self.organization_label = QLabel("")
        self.description_label = QLabel("")
        self.country_label = QLabel("")
        self.registry_server_label = QLabel("")
        self.remarks_label = QLabel("")
        for label in (
            self.asn_label,
            self.as_name_label,
            self.organization_label,
            self.description_label,
            self.country_label,
            self.registry_server_label,
            self.remarks_label,
        ):
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        summary_form.addRow("ASN", self.asn_label)
        summary_form.addRow("AS Name", self.as_name_label)
        summary_form.addRow("Organization", self.organization_label)
        summary_form.addRow("Description", self.description_label)
        summary_form.addRow("Country", self.country_label)
        summary_form.addRow("Registry Server", self.registry_server_label)
        summary_form.addRow("Remarks", self.remarks_label)
        root.addLayout(summary_form)

        self.output = QPlainTextEdit(self)
        self.output.setReadOnly(True)
        _apply_monospace_font(self.output)
        root.addWidget(self.output, 1)

        self.status_label = QLabel("Ready.")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        self.lookup_btn.clicked.connect(self._run_lookup)
        self.clear_btn.clicked.connect(self._clear_results)
        self.query_input.returnPressed.connect(self._run_lookup)

    def apply_runtime_settings(self, settings: AppSettings) -> None:
        _apply_monospace_font(self.output, settings)

    def _set_running(self, running: bool) -> None:
        self.lookup_btn.setEnabled(not running)
        self.clear_btn.setEnabled(not running)
        self.query_input.setEnabled(not running)
        self.server_input.setEnabled(not running)
        self.timeout_input.setEnabled(not running)
        self.follow_referral_check.setEnabled(not running)

    def _clear_results(self) -> None:
        if self._thread is not None:
            return
        self.asn_label.setText("(not looked up yet)")
        self.as_name_label.clear()
        self.organization_label.clear()
        self.description_label.clear()
        self.country_label.clear()
        self.registry_server_label.clear()
        self.remarks_label.clear()
        self.output.clear()
        self.status_label.setText("Ready.")

    def _run_lookup(self) -> None:
        if self._thread is not None:
            return
        request = ASNLookupRequest(
            query=self.query_input.text(),
            server=self.server_input.text(),
            timeout_ms=self.timeout_input.value(),
            follow_referral=self.follow_referral_check.isChecked(),
        )
        self._clear_results()
        self._worker = _ASNLookupWorker(request)
        self._thread = QThread(self)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.succeeded.connect(self._on_lookup_success)
        self._worker.failed.connect(self._on_lookup_failure)
        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._on_lookup_finished)
        self._thread.finished.connect(self._thread.deleteLater)
        self._set_running(True)
        self.status_label.setText("Looking up ASN...")
        self._thread.start()

    @Slot(object)
    def _on_lookup_success(self, payload: object) -> None:
        if not isinstance(payload, ASNLookupResult):
            self._on_lookup_failure("Unexpected ASN lookup payload.")
            return
        self.asn_label.setText(payload.normalized_asn)
        self.as_name_label.setText(payload.as_name)
        self.organization_label.setText(payload.organization)
        self.description_label.setText(payload.description)
        self.country_label.setText(payload.country)
        self.registry_server_label.setText(payload.registry_server)
        self.remarks_label.setText("\n".join(payload.remarks))
        self.output.setPlainText(format_asn_result(payload))
        self.status_label.setText("Lookup complete.")

    @Slot(str)
    def _on_lookup_failure(self, message: str) -> None:
        self.status_label.setText(message)

    @Slot()
    def _on_lookup_finished(self) -> None:
        self._thread = None
        self._worker = None
        self._set_running(False)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self._thread is not None:
            QMessageBox.information(self, "ASN Lookup Running", "Wait for the current ASN lookup to finish.")
            event.ignore()
            return
        super().closeEvent(event)


class IPScanDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("IP Scan")
        self.resize(1080, 760)
        self.setMinimumSize(760, 500)

        self._thread: QThread | None = None
        self._worker: _IPScanWorker | None = None
        self._tree_pages: dict[QTreeWidget, QWidget] = {}
        self._page_trees: dict[QWidget, QTreeWidget] = {}
        self._open_ports_host_filter = ""
        self._scan_stop_requested = False

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        form = QFormLayout()
        self.target_input = QLineEdit("192.0.2.0/24")
        self.target_input.setPlaceholderText("Hostname, IP address, or CIDR")
        self.port_preset_input = QComboBox()
        self.port_preset_input.addItem("Common TCP 20", IP_SCAN_PRESET_COMMON_20)
        self.port_preset_input.addItem("Common TCP 100", IP_SCAN_PRESET_COMMON_100)
        self.port_preset_input.addItem("Custom Ports", IP_SCAN_PRESET_CUSTOM)
        self.custom_ports_input = QLineEdit("")
        self.custom_ports_input.setPlaceholderText("Example: 22,80,443,8000-8100")
        self.timeout_input = QSpinBox()
        self.timeout_input.setRange(50, 10000)
        self.timeout_input.setSingleStep(50)
        self.timeout_input.setSuffix(" ms")
        self.timeout_input.setValue(IP_SCAN_DEFAULT_TIMEOUT_MS)
        self.reverse_dns_check = QCheckBox("Resolve names when available")
        self.reverse_dns_check.setChecked(True)
        form.addRow(QLabel("Target"), self.target_input)
        form.addRow(QLabel("Port Set"), self.port_preset_input)
        form.addRow(QLabel("Custom Ports"), self.custom_ports_input)
        form.addRow(QLabel("Timeout per port"), self.timeout_input)
        form.addRow(QLabel("Name Enrichment"), self.reverse_dns_check)
        root.addLayout(form)

        button_row = QHBoxLayout()
        self.scan_btn = QPushButton("Scan")
        self.stop_btn = QPushButton("Stop")
        self.clear_btn = QPushButton("Clear")
        self.copy_selected_btn = QPushButton("Copy Selected")
        self.copy_all_btn = QPushButton("Copy All")
        self.stop_btn.setEnabled(False)
        button_row.addWidget(self.scan_btn)
        button_row.addWidget(self.stop_btn)
        button_row.addWidget(self.clear_btn)
        button_row.addStretch(1)
        button_row.addWidget(self.copy_selected_btn)
        button_row.addWidget(self.copy_all_btn)
        self.runtime_badge = QLabel(self)
        self.runtime_badge.setAlignment(Qt.AlignCenter)
        self.runtime_badge.setMinimumWidth(104)
        button_row.addWidget(self.runtime_badge)
        root.addLayout(button_row)
        self._set_runtime_badge("stopped")

        self.progress_bar = QProgressBar(self)
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        root.addWidget(self.progress_bar)

        self.tabs = QTabWidget(self)
        (
            self.hosts_page,
            self.hosts_filter_input,
            self.hosts_tree,
        ) = self._create_filterable_tab(
            headers=["Host", "Name", "Status", "Open Ports", "Elapsed ms"],
            filter_placeholder="Search hosts by address, name, status, or port count",
        )
        (
            self.open_ports_page,
            self.open_ports_filter_input,
            self.open_ports_tree,
        ) = self._create_filterable_tab(
            headers=["Host", "Name", "Port", "Service"],
            filter_placeholder="Search open ports by host, name, port, or service",
        )
        self._open_ports_host_filter_label = QLabel("")
        self._open_ports_host_filter_label.hide()
        self.clear_open_ports_host_filter_btn = QPushButton("Clear Host Focus")
        self.clear_open_ports_host_filter_btn.hide()
        self.clear_open_ports_host_filter_btn.clicked.connect(self._clear_open_ports_host_filter)
        open_ports_filter_layout = self.open_ports_page.layout()
        assert isinstance(open_ports_filter_layout, QVBoxLayout)
        host_focus_row = QHBoxLayout()
        host_focus_row.addWidget(self._open_ports_host_filter_label)
        host_focus_row.addWidget(self.clear_open_ports_host_filter_btn)
        host_focus_row.addStretch(1)
        open_ports_filter_layout.insertLayout(1, host_focus_row)
        self.tabs.addTab(self.hosts_page, "Hosts")
        self.tabs.addTab(self.open_ports_page, "Open Ports")
        root.addWidget(self.tabs, 1)

        self.status_label = QLabel("Ready.")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        self.port_preset_input.currentIndexChanged.connect(self._update_custom_ports_input)
        self.scan_btn.clicked.connect(self._start_scan)
        self.stop_btn.clicked.connect(self._stop_scan)
        self.clear_btn.clicked.connect(self._clear_results)
        self.copy_selected_btn.clicked.connect(self._copy_selected_rows)
        self.copy_all_btn.clicked.connect(self._copy_all_rows)
        self.tabs.currentChanged.connect(self._update_copy_actions)
        self.target_input.returnPressed.connect(self._start_scan)
        self.hosts_filter_input.textChanged.connect(self._apply_hosts_filter)
        self.open_ports_filter_input.textChanged.connect(self._apply_open_ports_filter)
        self.hosts_tree.itemClicked.connect(self._on_hosts_item_clicked)

        self._update_custom_ports_input()
        self._update_copy_actions()

        _geo = QSettings("SnakeSh", "SnakeSh").value("ip_scan_dialog/geometry")
        if _geo is not None:
            self.restoreGeometry(_geo)

    def _create_filterable_tab(
        self,
        *,
        headers: list[str],
        filter_placeholder: str,
    ) -> tuple[QWidget, QLineEdit, QTreeWidget]:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        filter_row = QHBoxLayout()
        filter_row.setContentsMargins(0, 0, 0, 0)
        filter_row.setSpacing(6)
        filter_row.addWidget(QLabel("Filter"))
        filter_input = QLineEdit("")
        filter_input.setClearButtonEnabled(True)
        filter_input.setPlaceholderText(filter_placeholder)
        filter_row.addWidget(filter_input, 1)
        layout.addLayout(filter_row)

        tree = self._create_tree(headers)
        layout.addWidget(tree, 1)
        self._tree_pages[tree] = page
        self._page_trees[page] = tree
        return page, filter_input, tree

    def _create_tree(self, headers: list[str]) -> QTreeWidget:
        tree = QTreeWidget(self)
        tree.setColumnCount(len(headers))
        tree.setHeaderLabels(headers)
        tree.setAlternatingRowColors(True)
        tree.setRootIsDecorated(False)
        tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        tree.setSortingEnabled(True)
        tree.header().setSortIndicatorShown(True)
        tree.header().setSectionsClickable(True)
        tree.setContextMenuPolicy(Qt.CustomContextMenu)
        tree.customContextMenuRequested.connect(self._show_tree_context_menu)
        tree.itemSelectionChanged.connect(self._update_copy_actions)
        return tree

    def _set_running(self, running: bool) -> None:
        self.scan_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)
        self.clear_btn.setEnabled(not running)
        self.target_input.setEnabled(not running)
        self.port_preset_input.setEnabled(not running)
        self.custom_ports_input.setEnabled(not running and self._current_port_preset() == IP_SCAN_PRESET_CUSTOM)
        self.timeout_input.setEnabled(not running)
        self.reverse_dns_check.setEnabled(not running)
        self._update_copy_actions()

    def _current_port_preset(self) -> str:
        value = self.port_preset_input.currentData()
        return str(value or IP_SCAN_PRESET_COMMON_20)

    def _update_custom_ports_input(self) -> None:
        is_custom = self._current_port_preset() == IP_SCAN_PRESET_CUSTOM
        self.custom_ports_input.setEnabled(self._thread is None and is_custom)
        self.custom_ports_input.setPlaceholderText(
            "Example: 22,80,443,8000-8100" if is_custom else "Select Custom Ports to edit this field"
        )
        self._update_copy_actions()

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

    def _clear_results(self) -> None:
        if self._thread is not None:
            return
        self.hosts_tree.clear()
        self.open_ports_tree.clear()
        self.hosts_filter_input.clear()
        self.open_ports_filter_input.clear()
        self._clear_open_ports_host_filter(reset_status=False)
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self._scan_stop_requested = False
        self._set_runtime_badge("stopped")
        self.status_label.setText("Ready.")
        self._update_copy_actions()

    def _start_scan(self) -> None:
        if self._thread is not None:
            return
        request = IPScanRequest(
            target=self.target_input.text(),
            port_preset=self._current_port_preset(),
            custom_ports=self.custom_ports_input.text(),
            timeout_ms=self.timeout_input.value(),
            resolve_names=self.reverse_dns_check.isChecked(),
        )
        self._clear_results()
        self._worker = _IPScanWorker(request)
        self._thread = QThread(self)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_scan_progress)
        self._worker.succeeded.connect(self._on_scan_success)
        self._worker.failed.connect(self._on_scan_failure)
        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._on_scan_finished)
        self._thread.finished.connect(self._thread.deleteLater)
        self._set_running(True)
        self._scan_stop_requested = False
        self._set_runtime_badge("starting")
        self.status_label.setText("Starting IP scan...")
        self._thread.start()

    def _stop_scan(self) -> None:
        if self._worker is None:
            return
        self.stop_btn.setEnabled(False)
        self._scan_stop_requested = True
        self._set_runtime_badge("stopping")
        self.status_label.setText("Stopping IP scan...")
        self._worker.cancel()

    @Slot(object)
    def _on_scan_progress(self, payload: object) -> None:
        if not isinstance(payload, IPScanProgress):
            return
        self.progress_bar.setRange(0, max(1, payload.total_probes))
        self.progress_bar.setValue(min(payload.completed_probes, payload.total_probes))
        if not self._scan_stop_requested and (
            payload.current_host or payload.completed_probes > 0 or payload.completed_hosts > 0
        ):
            self._set_runtime_badge("running")
        if payload.current_host:
            self.status_label.setText(
                f"Scanning {payload.current_host}:{payload.current_port} "
                f"({payload.completed_hosts}/{payload.total_hosts} hosts, "
                f"{payload.completed_probes}/{payload.total_probes} probes, "
                f"{payload.open_ports_found} open ports)"
            )

    @Slot(object)
    def _on_scan_success(self, payload: object) -> None:
        if not isinstance(payload, IPScanResult):
            self._on_scan_failure("Unexpected IP scan payload.")
            return
        self.status_label.setText("Populating results...")
        self.progress_bar.setRange(0, 0)
        QApplication.processEvents()
        self._populate_hosts(payload.hosts)
        self._populate_open_ports(payload.open_ports)
        self.progress_bar.setRange(0, max(1, payload.total_probes))
        self.progress_bar.setValue(min(payload.scanned_probes, payload.total_probes))
        if payload.canceled:
            self.status_label.setText(
                f"Scan canceled after {payload.scanned_hosts}/{payload.total_hosts} hosts "
                f"and {payload.scanned_probes}/{payload.total_probes} probes. "
                f"Found {len(payload.open_ports)} open TCP port(s)."
            )
        else:
            self.status_label.setText(
                f"Scan complete. Scanned {payload.scanned_hosts} host(s), found {len(payload.open_ports)} "
                f"open TCP port(s), and finished in {payload.elapsed_ms:.1f} ms."
            )

    def _populate_hosts(self, hosts: list[IPScanHostResult]) -> None:
        self.hosts_tree.setSortingEnabled(False)
        self.hosts_tree.setUpdatesEnabled(False)
        self.hosts_tree.clear()
        for entry in hosts:
            item = _SortableTreeWidgetItem(
                [
                    entry.host,
                    entry.resolved_name,
                    entry.status,
                    str(entry.open_port_count),
                    f"{entry.elapsed_ms:.1f}",
                ]
            )
            if entry.open_port_count > 0:
                item.setToolTip(0, f"Click to show open ports for {entry.host}.")
                item.setToolTip(3, f"Click to show open ports for {entry.host}.")
            self.hosts_tree.addTopLevelItem(item)
        self.hosts_tree.setUpdatesEnabled(True)
        self.hosts_tree.setSortingEnabled(True)
        self.hosts_tree.sortItems(0, Qt.AscendingOrder)
        self._apply_hosts_filter()
        self._resize_tree(self.hosts_tree)

    def _populate_open_ports(self, open_ports: list[IPScanPortResult]) -> None:
        self.open_ports_tree.setSortingEnabled(False)
        self.open_ports_tree.setUpdatesEnabled(False)
        self.open_ports_tree.clear()
        for entry in open_ports:
            self.open_ports_tree.addTopLevelItem(
                _SortableTreeWidgetItem(
                    [
                        entry.host,
                        entry.resolved_name,
                        str(entry.port),
                        entry.service_name,
                    ]
                )
            )
        self.open_ports_tree.setUpdatesEnabled(True)
        self.open_ports_tree.setSortingEnabled(True)
        self.open_ports_tree.sortItems(0, Qt.AscendingOrder)
        self._apply_open_ports_filter()
        self._resize_tree(self.open_ports_tree)

    @Slot(str)
    def _on_scan_failure(self, message: str) -> None:
        self.status_label.setText(message)

    @Slot()
    def _on_scan_finished(self) -> None:
        self._thread = None
        self._worker = None
        self._scan_stop_requested = False
        self._set_runtime_badge("stopped")
        self._set_running(False)
        self._update_copy_actions()

    def _resize_tree(self, tree: QTreeWidget) -> None:
        for index in range(tree.columnCount()):
            tree.resizeColumnToContents(index)
        self._update_copy_actions()

    def _current_tree(self) -> QTreeWidget | None:
        current = self.tabs.currentWidget()
        if current is None:
            return None
        return self._page_trees.get(current)

    def _update_copy_actions(self, *_args: object) -> None:
        tree = self._current_tree()
        has_rows = tree is not None and self._visible_top_level_item_count(tree) > 0
        has_selection = tree is not None and bool(tree.selectedItems())
        self.copy_selected_btn.setEnabled(has_selection)
        self.copy_all_btn.setEnabled(has_rows)

    @Slot()
    def _copy_selected_rows(self) -> None:
        tree = self._current_tree()
        if tree is None:
            return
        rows = [self._tree_item_values(tree, item) for item in tree.selectedItems()]
        self._copy_rows(tree, rows, empty_message="Select one or more rows to copy.")

    @Slot()
    def _copy_all_rows(self) -> None:
        tree = self._current_tree()
        if tree is None:
            return
        rows = [
            self._tree_item_values(tree, tree.topLevelItem(index))
            for index in range(tree.topLevelItemCount())
            if not tree.topLevelItem(index).isHidden()
        ]
        self._copy_rows(tree, rows, empty_message="There is no data to copy on this tab.")

    def _copy_rows(self, tree: QTreeWidget, rows: list[list[str]], *, empty_message: str) -> None:
        if not rows:
            self.status_label.setText(empty_message)
            self._update_copy_actions()
            return
        headers = [tree.headerItem().text(index) for index in range(tree.columnCount())]
        payload = "\n".join("\t".join(row) for row in [headers, *rows])
        QApplication.clipboard().setText(payload)
        tab_name = self.tabs.tabText(self.tabs.currentIndex())
        noun = "row" if len(rows) == 1 else "rows"
        self.status_label.setText(f"Copied {len(rows)} {noun} from {tab_name}.")
        self._update_copy_actions()

    def _tree_item_values(self, tree: QTreeWidget, item: QTreeWidgetItem) -> list[str]:
        return [item.text(index) for index in range(tree.columnCount())]

    @Slot(object)
    def _show_tree_context_menu(self, position) -> None:
        tree = self.sender()
        if not isinstance(tree, QTreeWidget):
            return
        page = self._tree_pages.get(tree)
        if page is not None:
            self.tabs.setCurrentWidget(page)
        menu = QMenu(self)
        copy_selected_action = menu.addAction("Copy Selected")
        copy_selected_action.setEnabled(bool(tree.selectedItems()))
        copy_all_action = menu.addAction("Copy All")
        copy_all_action.setEnabled(self._visible_top_level_item_count(tree) > 0)
        chosen = menu.exec(tree.viewport().mapToGlobal(position))
        if chosen == copy_selected_action:
            self._copy_selected_rows()
        elif chosen == copy_all_action:
            self._copy_all_rows()

    def _apply_hosts_filter(self, *_args: object) -> None:
        self._apply_tree_text_filter(self.hosts_tree, self.hosts_filter_input.text())

    def _apply_open_ports_filter(self, *_args: object) -> None:
        needle = self.open_ports_filter_input.text().strip().lower()
        for index in range(self.open_ports_tree.topLevelItemCount()):
            item = self.open_ports_tree.topLevelItem(index)
            matches_host = not self._open_ports_host_filter or item.text(0) == self._open_ports_host_filter
            haystack = " ".join(item.text(column) for column in range(self.open_ports_tree.columnCount())).lower()
            matches_text = not needle or needle in haystack
            item.setHidden(not (matches_host and matches_text))
        self._update_copy_actions()

    def _apply_tree_text_filter(self, tree: QTreeWidget, text: str) -> None:
        needle = text.strip().lower()
        for index in range(tree.topLevelItemCount()):
            item = tree.topLevelItem(index)
            haystack = " ".join(item.text(column) for column in range(tree.columnCount())).lower()
            item.setHidden(bool(needle) and needle not in haystack)
        self._update_copy_actions()

    def _visible_top_level_item_count(self, tree: QTreeWidget) -> int:
        count = 0
        for index in range(tree.topLevelItemCount()):
            if not tree.topLevelItem(index).isHidden():
                count += 1
        return count

    def _on_hosts_item_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        if column not in {0, 3}:
            return
        try:
            open_port_count = int(item.text(3))
        except ValueError:
            return
        if open_port_count <= 0:
            return
        self._focus_open_ports_for_host(item.text(0))

    def _focus_open_ports_for_host(self, host: str) -> None:
        self._open_ports_host_filter = host
        self._open_ports_host_filter_label.setText(f"Host Focus: {host}")
        self._open_ports_host_filter_label.show()
        self.clear_open_ports_host_filter_btn.show()
        self.tabs.setCurrentWidget(self.open_ports_page)
        self._apply_open_ports_filter()
        for index in range(self.open_ports_tree.topLevelItemCount()):
            item = self.open_ports_tree.topLevelItem(index)
            if item.isHidden():
                continue
            self.open_ports_tree.setCurrentItem(item)
            self.open_ports_tree.scrollToItem(item)
            break
        self.status_label.setText(f"Showing open ports for {host}.")

    def _clear_open_ports_host_filter(self, *, reset_status: bool = True) -> None:
        self._open_ports_host_filter = ""
        self._open_ports_host_filter_label.clear()
        self._open_ports_host_filter_label.hide()
        self.clear_open_ports_host_filter_btn.hide()
        self._apply_open_ports_filter()
        if reset_status and self._thread is None:
            self.status_label.setText("Host focus cleared.")

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self._thread is not None:
            QMessageBox.information(self, "IP Scan Running", "Stop the current IP scan before closing this window.")
            event.ignore()
            return
        QSettings("SnakeSh", "SnakeSh").setValue("ip_scan_dialog/geometry", self.saveGeometry())
        super().closeEvent(event)
