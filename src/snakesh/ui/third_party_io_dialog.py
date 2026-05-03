from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)


class ThirdPartyImportExportDialog(QDialog):
    def __init__(
        self,
        parent=None,
        on_import_securecrt_requested: Callable[[], None] | None = None,
        on_import_openssh_requested: Callable[[], None] | None = None,
        on_import_putty_requested: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Third Party Import")
        self.resize(640, 300)
        self._on_import_securecrt_requested = on_import_securecrt_requested
        self._on_import_openssh_requested = on_import_openssh_requested
        self._on_import_putty_requested = on_import_putty_requested

        root = QVBoxLayout(self)
        intro = QLabel(
            "Use this dialog to import sessions from external tools.\n"
            "Third-party export is intentionally disabled.\n\n"
            "Supported imports: SecureCRT XML, OpenSSH config, PuTTY registry."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        securecrt_label = QLabel("SecureCRT")
        securecrt_label.setStyleSheet("font-weight: 600; margin-top: 6px;")
        root.addWidget(securecrt_label)

        securecrt_row = QHBoxLayout()
        self.import_securecrt_btn = QPushButton("Import SecureCRT XML")
        self.import_securecrt_btn.clicked.connect(self._handle_import_securecrt)
        self.import_securecrt_btn.setEnabled(self._on_import_securecrt_requested is not None)
        securecrt_row.addWidget(self.import_securecrt_btn)
        securecrt_row.addStretch(1)
        root.addLayout(securecrt_row)

        openssh_label = QLabel("OpenSSH")
        openssh_label.setStyleSheet("font-weight: 600; margin-top: 10px;")
        root.addWidget(openssh_label)

        openssh_row = QHBoxLayout()
        self.import_openssh_btn = QPushButton("Import OpenSSH Config")
        self.import_openssh_btn.clicked.connect(self._handle_import_openssh)
        self.import_openssh_btn.setEnabled(self._on_import_openssh_requested is not None)
        openssh_row.addWidget(self.import_openssh_btn)
        openssh_row.addStretch(1)
        root.addLayout(openssh_row)

        putty_label = QLabel("PuTTY")
        putty_label.setStyleSheet("font-weight: 600; margin-top: 10px;")
        root.addWidget(putty_label)

        putty_row = QHBoxLayout()
        self.import_putty_btn = QPushButton("Import PuTTY Sessions")
        self.import_putty_btn.clicked.connect(self._handle_import_putty)
        self.import_putty_btn.setEnabled(self._on_import_putty_requested is not None)
        putty_row.addWidget(self.import_putty_btn)
        putty_row.addStretch(1)
        root.addLayout(putty_row)

        root.addStretch(1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        close_button = buttons.button(QDialogButtonBox.Close)
        if close_button is not None:
            close_button.clicked.connect(self.accept)
        root.addWidget(buttons)

    def _handle_import_securecrt(self) -> None:
        if self._on_import_securecrt_requested:
            self._on_import_securecrt_requested()

    def _handle_import_openssh(self) -> None:
        if self._on_import_openssh_requested:
            self._on_import_openssh_requested()

    def _handle_import_putty(self) -> None:
        if self._on_import_putty_requested:
            self._on_import_putty_requested()
