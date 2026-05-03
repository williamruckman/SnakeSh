from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from snakesh.services.file_hash_service import (
    FileHashResult,
    FileHashVerificationResult,
    SUPPORTED_HASH_ALGORITHMS,
    compute_file_hash,
    verify_file_against_checksum_file,
    verify_file_hash,
)
from snakesh.services.settings_service import AppSettings
from snakesh.ui.theme import apply_terminal_output_font


class _TaskWorker(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, task: Callable[[], object]) -> None:
        super().__init__()
        self._task = task

    @Slot()
    def run(self) -> None:
        try:
            self.succeeded.emit(self._task())
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()


class FileHashDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("File Hash")
        self.resize(760, 520)
        self._thread: QThread | None = None
        self._worker: _TaskWorker | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        form = QFormLayout()
        self.file_widget, self.file_input = self._build_path_input("Select file")
        self.algorithm_input = QComboBox()
        for algorithm in SUPPORTED_HASH_ALGORITHMS:
            self.algorithm_input.addItem(algorithm.upper(), algorithm)
        self.generated_digest = QLineEdit()
        self.generated_digest.setReadOnly(True)
        self.manual_digest = QLineEdit()
        self.manual_digest.setPlaceholderText("Paste a known hash here for manual verification")
        self.checksum_widget, self.checksum_input = self._build_path_input("Select checksum file")
        form.addRow("Target File", self.file_widget)
        form.addRow("Algorithm", self.algorithm_input)
        form.addRow("Generated Digest", self.generated_digest)
        form.addRow("Manual Verify", self.manual_digest)
        form.addRow("Checksum File", self.checksum_widget)
        layout.addLayout(form)

        button_row = QHBoxLayout()
        self.generate_btn = QPushButton("Generate")
        self.verify_btn = QPushButton("Verify")
        self.clear_btn = QPushButton("Clear")
        button_row.addWidget(self.generate_btn)
        button_row.addWidget(self.verify_btn)
        button_row.addWidget(self.clear_btn)
        button_row.addStretch(1)
        layout.addLayout(button_row)

        self.details = QPlainTextEdit(self)
        self.details.setReadOnly(True)
        layout.addWidget(self.details, 1)

        self.status_label = QLabel("Ready.")
        layout.addWidget(self.status_label)

        self.generate_btn.clicked.connect(self._generate_hash)
        self.verify_btn.clicked.connect(self._verify_hash)
        self.clear_btn.clicked.connect(self._clear_form)

    def apply_runtime_settings(self, settings: AppSettings) -> None:
        apply_terminal_output_font(self.details, settings)

    def _build_path_input(self, dialog_title: str) -> tuple[QWidget, QLineEdit]:
        container = QWidget(self)
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        field = QLineEdit()
        browse_btn = QPushButton("Browse...")
        row.addWidget(field, 1)
        row.addWidget(browse_btn, 0)
        browse_btn.clicked.connect(lambda: self._browse_file(field, dialog_title))
        return container, field

    def _browse_file(self, target: QLineEdit, dialog_title: str) -> None:
        path, _ = QFileDialog.getOpenFileName(self, dialog_title)
        if path:
            target.setText(path)

    def _selected_algorithm(self) -> str:
        return str(self.algorithm_input.currentData() or self.algorithm_input.currentText()).strip().lower()

    def _set_running(self, running: bool) -> None:
        self.generate_btn.setEnabled(not running)
        self.verify_btn.setEnabled(not running)
        self.clear_btn.setEnabled(not running)

    def _generate_hash(self) -> None:
        file_path = self.file_input.text().strip()
        if not file_path:
            QMessageBox.warning(self, "File Hash", "Select a file first.")
            return
        self._start_task(lambda: compute_file_hash(file_path, self._selected_algorithm()), self._on_generate_complete)
        self.status_label.setText("Generating hash...")

    def _verify_hash(self) -> None:
        file_path = self.file_input.text().strip()
        if not file_path:
            QMessageBox.warning(self, "File Hash", "Select a file first.")
            return

        manual_digest = self.manual_digest.text().strip()
        checksum_file = self.checksum_input.text().strip()
        if manual_digest:
            task = lambda: verify_file_hash(file_path, self._selected_algorithm(), manual_digest)
        elif checksum_file:
            task = lambda: verify_file_against_checksum_file(file_path, self._selected_algorithm(), checksum_file)
        else:
            QMessageBox.warning(self, "File Hash", "Enter a manual hash or choose a checksum file to verify.")
            return

        self._start_task(task, self._on_verify_complete)
        self.status_label.setText("Verifying hash...")

    def _clear_form(self) -> None:
        self.file_input.clear()
        self.generated_digest.clear()
        self.manual_digest.clear()
        self.checksum_input.clear()
        self.details.clear()
        self.status_label.setText("Ready.")

    def _start_task(self, task: Callable[[], object], on_success: Callable[[object], None]) -> None:
        if self._thread is not None:
            return
        self._set_running(True)
        self._thread = QThread(self)
        self._worker = _TaskWorker(task)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.succeeded.connect(on_success)
        self._worker.failed.connect(self._on_task_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._on_task_finished)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    @Slot(object)
    def _on_generate_complete(self, result: object) -> None:
        if not isinstance(result, FileHashResult):
            self._on_task_failed("Unexpected hash result.")
            return
        self.generated_digest.setText(result.digest)
        self.details.setPlainText(
            "\n".join(
                [
                    f"File: {result.file_path}",
                    f"Algorithm: {result.algorithm.upper()}",
                    f"Digest: {result.digest}",
                    f"Size: {result.size_bytes} bytes",
                    f"Elapsed: {result.elapsed_ms:.1f} ms",
                ]
            )
        )
        self.status_label.setText("Hash generation complete.")

    @Slot(object)
    def _on_verify_complete(self, result: object) -> None:
        if not isinstance(result, FileHashVerificationResult):
            self._on_task_failed("Unexpected verification result.")
            return
        self.generated_digest.setText(result.actual_digest)
        verdict = "MATCH" if result.matched else "MISMATCH"
        lines = [
            f"Result: {verdict}",
            f"Algorithm: {result.algorithm.upper()}",
            f"Expected: {result.expected_digest}",
            f"Actual: {result.actual_digest}",
            f"Source: {result.source}",
        ]
        if result.matched_filename:
            lines.append(f"Checksum Entry: {result.matched_filename}")
        self.details.setPlainText("\n".join(lines))
        self.status_label.setText("Verification complete.")

    @Slot(str)
    def _on_task_failed(self, message: str) -> None:
        self.status_label.setText(message)
        self.details.setPlainText(message)

    @Slot()
    def _on_task_finished(self) -> None:
        self._thread = None
        self._worker = None
        self._set_running(False)
