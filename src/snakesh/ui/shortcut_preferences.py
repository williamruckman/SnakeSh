from __future__ import annotations

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


def normalize_shortcut_text(raw: str) -> str:
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


def display_shortcut_text(raw: str) -> str:
    normalized = normalize_shortcut_text(raw)
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
        allow_disabled: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.resize(460, 200)

        self._default_shortcut = normalize_shortcut_text(default_shortcut)
        self._allow_disabled = bool(allow_disabled)

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
        normalized_initial = normalize_shortcut_text(initial_shortcut)
        if normalized_initial:
            self.sequence_edit.setKeySequence(QKeySequence.fromString(normalized_initial, QKeySequence.PortableText))
        root.addWidget(self.sequence_edit)

        self.preview = QLabel("")
        self.preview.setWordWrap(True)
        root.addWidget(self.preview)

        actions = QHBoxLayout()
        default_btn = QPushButton("Default")
        actions.addWidget(default_btn)
        if self._allow_disabled:
            disable_btn = QPushButton("Disable")
            actions.addWidget(disable_btn)
            disable_btn.clicked.connect(self.sequence_edit.clear)
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
        rendered = (
            display_shortcut_text(QKeySequence(first).toString(QKeySequence.PortableText))
            if first
            else "Disabled"
        )
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
        allow_disabled: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._default_shortcut = normalize_shortcut_text(default_shortcut)
        self._dialog_title = dialog_title
        self._allow_disabled = bool(allow_disabled)
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
        if self._allow_disabled:
            self.disable_btn = QPushButton("Disable")
            self.disable_btn.clicked.connect(lambda: self.set_shortcut_text(""))
            layout.addWidget(self.disable_btn, 0)
        else:
            self.disable_btn = None

        initial = initial_shortcut if (initial_shortcut or self._allow_disabled) else self._default_shortcut
        self.set_shortcut_text(initial)

    def _open_capture_dialog(self) -> None:
        dialog = ShortcutCaptureDialog(
            title=self._dialog_title,
            initial_shortcut=self._shortcut_text,
            default_shortcut=self._default_shortcut,
            allow_disabled=self._allow_disabled,
            parent=self,
        )
        if dialog.exec():
            self.set_shortcut_text(dialog.shortcut_text())

    def shortcut_text(self) -> str:
        return self._shortcut_text

    def set_shortcut_text(self, value: str) -> None:
        self._shortcut_text = normalize_shortcut_text(value)
        self.display.setText(display_shortcut_text(self._shortcut_text))
