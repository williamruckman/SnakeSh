from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QPushButton, QVBoxLayout

from snakesh.services.macos_local_network import request_local_network_access


class LocalNetworkAccessDialog(QDialog):
    """Explain the macOS prompt without claiming to know its permission state."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Local Network Access")
        self.setModal(False)
        self.resize(560, 310)
        layout = QVBoxLayout(self)
        explanation = QLabel(
            "SnakeSh needs Local Network access to connect to computers and devices "
            "on your network using SSH, SFTP, RDP, VNC, and network tools."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        instructions = QLabel(
            "Click Allow if macOS asks for access. macOS remembers your choice.\n\n"
            "If access was already denied, enable SnakeSh in System Settings > "
            "Privacy & Security > Local Network. After allowing access, retry your connection.\n\n"
            "You can return here from Tools > Local Network Access."
        )
        instructions.setWordWrap(True)
        layout.addWidget(instructions)
        self.status_label = QLabel("Request access to show the macOS prompt if needed.")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.request_button = QPushButton("Request Access")
        buttons.addButton(self.request_button, QDialogButtonBox.ButtonRole.ActionRole)
        self.request_button.clicked.connect(self.request_access)
        self.settings_button = QPushButton("Open System Settings")
        buttons.addButton(self.settings_button, QDialogButtonBox.ButtonRole.ActionRole)
        self.settings_button.clicked.connect(self._open_settings)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def request_access(self) -> None:
        attempts = request_local_network_access()
        if attempts:
            self.status_label.setText(
                "Check for a macOS permission prompt and click Allow. "
                "If no prompt appears, access may already be allowed or denied; check "
                "Local Network in System Settings. SnakeSh cannot read your permission choice."
            )
        else:
            self.status_label.setText(
                "The permission prompt could not be requested on the current network. "
                "Connect to Wi-Fi or Ethernet and try again, or check Local Network in "
                "System Settings. A normal connection may also trigger the macOS prompt."
            )

    def _open_settings(self) -> None:
        # Best-effort shortcut; keep manual navigation visible for OS versions
        # that open the general Privacy & Security page instead of this pane.
        opened = QDesktopServices.openUrl(QUrl(
            "x-apple.systempreferences:com.apple.preference.security?Privacy_LocalNetwork"
        ))
        if not opened:
            self.status_label.setText(
                "Open System Settings > Privacy & Security > Local Network, "
                "enable SnakeSh, then retry your connection."
            )
