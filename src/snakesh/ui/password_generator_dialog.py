from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Callable

from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from snakesh.services.settings_service import AppSettings
from snakesh.ui.theme import apply_terminal_output_font

_LOWER = "abcdefghijklmnopqrstuvwxyz"
_UPPER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_DIGITS = "0123456789"
_SYMBOLS = "!@#$%^&*()-_=+[]{};:,.?/"
_COMPLEXITY_CHOICES = ("Balanced", "Strong", "Very Strong", "Maximum")


@dataclass(frozen=True)
class PasswordOptions:
    length: int
    count: int
    complexity: str
    include_lower: bool
    include_upper: bool
    include_digits: bool
    include_symbols: bool
    include_characters: str = ""
    exclude_characters: str = ""


def generate_passwords(options: PasswordOptions) -> list[str]:
    excluded_characters = set(dict.fromkeys(options.exclude_characters))
    required_chars = list(dict.fromkeys(options.include_characters))
    conflicting_chars = [char for char in required_chars if char in excluded_characters]
    if conflicting_chars:
        preview = "".join(conflicting_chars[:12])
        if len(conflicting_chars) > 12:
            preview += "..."
        raise ValueError(
            "The same character cannot appear in both the include and exclude lists: "
            f"{preview}"
        )

    categories: list[str] = []
    if options.include_lower:
        filtered = "".join(char for char in _LOWER if char not in excluded_characters)
        if filtered:
            categories.append(filtered)
    if options.include_upper:
        filtered = "".join(char for char in _UPPER if char not in excluded_characters)
        if filtered:
            categories.append(filtered)
    if options.include_digits:
        filtered = "".join(char for char in _DIGITS if char not in excluded_characters)
        if filtered:
            categories.append(filtered)
    if options.include_symbols:
        filtered = "".join(char for char in _SYMBOLS if char not in excluded_characters)
        if filtered:
            categories.append(filtered)

    if not categories:
        raise ValueError("Select at least one usable character group after exclusions.")

    complexity_key = options.complexity.strip().lower()
    required_groups = {
        "balanced": 1,
        "strong": 2,
        "very strong": 3,
        "maximum": 4,
    }.get(complexity_key, 1)

    if len(categories) < required_groups:
        raise ValueError(
            "Selected complexity requires more character groups. "
            "Enable more groups or lower the complexity setting."
        )

    minimum_length = {
        "balanced": 8,
        "strong": 10,
        "very strong": 14,
        "maximum": 16,
    }.get(complexity_key, 8)

    if options.length < minimum_length:
        raise ValueError(
            f"{options.complexity} complexity requires length >= {minimum_length}."
        )

    if options.length < len(categories):
        raise ValueError(
            "Password length must be at least the number of selected character groups."
        )

    minimum_required_length = len(categories) + len(required_chars)
    if options.length < minimum_required_length:
        raise ValueError(
            "Password length is too short for the selected character groups plus "
            "required included characters."
        )

    alphabet = "".join(dict.fromkeys("".join(categories) + "".join(required_chars)))
    rng = secrets.SystemRandom()
    generated: list[str] = []

    for _ in range(options.count):
        chars = [secrets.choice(category) for category in categories]
        chars.extend(required_chars)
        while len(chars) < options.length:
            chars.append(secrets.choice(alphabet))
        rng.shuffle(chars)
        generated.append("".join(chars))

    return generated


class PasswordGeneratorDialog(QDialog):
    def __init__(
        self,
        *,
        initial_options: PasswordOptions | None = None,
        on_options_changed: Callable[[PasswordOptions], None] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._on_options_changed = on_options_changed
        self.setWindowTitle("Password Generator")
        self.resize(700, 520)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        intro = QLabel(
            "Generate cryptographically secure passwords using local system entropy "
            "(Python secrets / OS CSPRNG)."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        form = QFormLayout()
        self.length_input = QSpinBox()
        self.length_input.setRange(8, 256)
        self.length_input.setValue(20)

        self.count_input = QSpinBox()
        self.count_input.setRange(1, 200)
        self.count_input.setValue(5)

        self.complexity_input = QComboBox()
        self.complexity_input.addItems(list(_COMPLEXITY_CHOICES))
        self.complexity_input.setCurrentText("Strong")

        form.addRow("Length", self.length_input)
        form.addRow("Count", self.count_input)
        form.addRow("Complexity", self.complexity_input)

        groups_row = QHBoxLayout()
        self.lower_input = QCheckBox("Lowercase")
        self.lower_input.setChecked(True)
        self.upper_input = QCheckBox("Uppercase")
        self.upper_input.setChecked(True)
        self.digits_input = QCheckBox("Digits")
        self.digits_input.setChecked(True)
        self.symbols_input = QCheckBox("Symbols")
        self.symbols_input.setChecked(True)
        groups_row.addWidget(self.lower_input)
        groups_row.addWidget(self.upper_input)
        groups_row.addWidget(self.digits_input)
        groups_row.addWidget(self.symbols_input)
        groups_row.addStretch(1)

        groups_widget = QWidget()
        groups_widget.setLayout(groups_row)
        form.addRow("Character Groups", groups_widget)

        self.include_characters_input = QLineEdit()
        self.include_characters_input.setPlaceholderText("Characters that must appear in every password")
        form.addRow("Include These Characters", self.include_characters_input)

        self.exclude_characters_input = QLineEdit()
        self.exclude_characters_input.setPlaceholderText("Characters that must never appear in any password")
        form.addRow("Exclude These Characters", self.exclude_characters_input)

        root.addLayout(form)

        actions = QHBoxLayout()
        self.generate_btn = QPushButton("Generate")
        self.copy_btn = QPushButton("Copy All")
        self.copy_btn.setEnabled(False)
        actions.addWidget(self.generate_btn)
        actions.addWidget(self.copy_btn)
        actions.addStretch(1)
        root.addLayout(actions)

        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setPlaceholderText("Generated passwords will appear here.")
        self._apply_monospace_font()
        root.addWidget(self.output, 1)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        self.generate_btn.clicked.connect(self._generate)
        self.copy_btn.clicked.connect(self._copy_all)

        if initial_options is not None:
            self._apply_options(initial_options)
        self._connect_settings_change_signals()

    def _apply_options(self, options: PasswordOptions) -> None:
        self.length_input.setValue(max(self.length_input.minimum(), min(self.length_input.maximum(), options.length)))
        self.count_input.setValue(max(self.count_input.minimum(), min(self.count_input.maximum(), options.count)))
        complexity = options.complexity.strip().lower()
        normalized_complexity = next(
            (choice for choice in _COMPLEXITY_CHOICES if choice.lower() == complexity),
            "Strong",
        )
        self.complexity_input.setCurrentText(normalized_complexity)
        self.lower_input.setChecked(bool(options.include_lower))
        self.upper_input.setChecked(bool(options.include_upper))
        self.digits_input.setChecked(bool(options.include_digits))
        self.symbols_input.setChecked(bool(options.include_symbols))
        self.include_characters_input.setText(options.include_characters)
        self.exclude_characters_input.setText(options.exclude_characters)

    def _connect_settings_change_signals(self) -> None:
        self.length_input.valueChanged.connect(self._emit_options_changed)
        self.count_input.valueChanged.connect(self._emit_options_changed)
        self.complexity_input.currentTextChanged.connect(self._emit_options_changed)
        self.lower_input.toggled.connect(self._emit_options_changed)
        self.upper_input.toggled.connect(self._emit_options_changed)
        self.digits_input.toggled.connect(self._emit_options_changed)
        self.symbols_input.toggled.connect(self._emit_options_changed)
        self.include_characters_input.textChanged.connect(self._emit_options_changed)
        self.exclude_characters_input.textChanged.connect(self._emit_options_changed)

    def _emit_options_changed(self, *_args) -> None:
        if self._on_options_changed is None:
            return
        self._on_options_changed(self._options())

    def _apply_monospace_font(self) -> None:
        families = QFontDatabase.families()
        for candidate in ("Cascadia Mono", "Consolas", "Courier New", "Liberation Mono", "DejaVu Sans Mono"):
            if candidate in families:
                font = self.output.font()
                font.setFamily(candidate)
                self.output.setFont(font)
                return

    def apply_runtime_settings(self, settings: AppSettings) -> None:
        apply_terminal_output_font(self.output, settings)

    def _options(self) -> PasswordOptions:
        return PasswordOptions(
            length=self.length_input.value(),
            count=self.count_input.value(),
            complexity=self.complexity_input.currentText(),
            include_lower=self.lower_input.isChecked(),
            include_upper=self.upper_input.isChecked(),
            include_digits=self.digits_input.isChecked(),
            include_symbols=self.symbols_input.isChecked(),
            include_characters=self.include_characters_input.text(),
            exclude_characters=self.exclude_characters_input.text(),
        )

    def _generate(self) -> None:
        try:
            passwords = generate_passwords(self._options())
        except ValueError as exc:
            QMessageBox.warning(self, "Password Generator", str(exc))
            return

        self.output.setPlainText("\n".join(passwords))
        self.copy_btn.setEnabled(bool(passwords))
        self.status.setText(f"Generated {len(passwords)} password(s).")

    def _copy_all(self) -> None:
        text = self.output.toPlainText().strip()
        if not text:
            return
        QApplication.clipboard().setText(text)
        self.status.setText("Copied generated passwords to clipboard.")
