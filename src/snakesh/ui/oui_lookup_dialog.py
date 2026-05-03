from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from snakesh.services.oui_service import (
    bundled_oui_lookup_service,
    bundled_oui_snapshot_available,
    normalize_oui_query,
)


class OUILookupDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("OUI Lookup")
        self.resize(520, 220)
        self._snapshot_available = bundled_oui_snapshot_available()
        self._lookup_service = bundled_oui_lookup_service()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        prompt = QLabel("Enter a MAC address or OUI prefix (for example `00:11:22:33:44:55` or `70-B3-D5-F2-F/36`).")
        prompt.setWordWrap(True)
        layout.addWidget(prompt)

        row = QHBoxLayout()
        self.query_input = QLineEdit()
        self.query_input.setPlaceholderText("MAC address or OUI prefix")
        self.lookup_btn = QPushButton("Lookup")
        row.addWidget(self.query_input, 1)
        row.addWidget(self.lookup_btn, 0)
        layout.addLayout(row)

        form = QFormLayout()
        self.vendor_label = QLabel("(not looked up yet)")
        self.vendor_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.prefix_label = QLabel("")
        self.prefix_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.normalized_label = QLabel("")
        self.normalized_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        form.addRow("Vendor", self.vendor_label)
        form.addRow("Matched Prefix", self.prefix_label)
        form.addRow("Normalized Input", self.normalized_label)
        layout.addLayout(form)

        self.status_label = QLabel(
            "Ready."
            if self._snapshot_available
            else "No bundled OUI snapshot is available. Vendor lookup is unavailable."
        )
        layout.addWidget(self.status_label)

        self.lookup_btn.clicked.connect(self._lookup)
        self.query_input.returnPressed.connect(self._lookup)

    def _lookup(self) -> None:
        raw_query = self.query_input.text()
        try:
            match = self._lookup_service.lookup(raw_query)
        except ValueError as exc:
            self.vendor_label.setText("(invalid input)")
            self.prefix_label.setText("")
            self.normalized_label.setText("")
            self.status_label.setText(str(exc))
            return

        if match is None:
            normalized, _ = normalize_oui_query(raw_query)
            self.normalized_label.setText(normalized)
            if not self._snapshot_available:
                self.vendor_label.setText("Unavailable")
                self.prefix_label.setText("")
                self.status_label.setText(
                    "Vendor lookup is unavailable because no bundled OUI snapshot is available."
                )
                return
            self.vendor_label.setText("Unknown")
            self.prefix_label.setText("")
            self.status_label.setText("No vendor match found in the bundled snapshot.")
            return

        self.vendor_label.setText(match.vendor)
        self.prefix_label.setText(f"{match.prefix} / {match.bits} bits")
        self.normalized_label.setText(match.normalized)
        self.status_label.setText("Lookup complete.")
