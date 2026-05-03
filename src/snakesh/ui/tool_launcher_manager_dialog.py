from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from snakesh.core.tool_registry import TOOL_REGISTRY, normalize_tool_keys
from snakesh.ui.tool_icon_helpers import TOOL_MENU_ICON_SIZE, tool_menu_icon


class ToolLauncherManagerDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Manage Tool Launchers")
        self.resize(520, 560)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        intro = QLabel(
            "Choose which SnakeSh tools should have launcher entries in your desktop environment."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        actions = QHBoxLayout()
        self.select_all_btn = QPushButton("Select All")
        self.clear_all_btn = QPushButton("Clear All")
        actions.addWidget(self.select_all_btn)
        actions.addWidget(self.clear_all_btn)
        actions.addStretch(1)
        root.addLayout(actions)

        self.tool_list = QListWidget(self)
        self.tool_list.setIconSize(QSize(TOOL_MENU_ICON_SIZE, TOOL_MENU_ICON_SIZE))
        for entry in TOOL_REGISTRY:
            item = QListWidgetItem(tool_menu_icon(entry.key), entry.label, self.tool_list)
            item.setData(Qt.UserRole, entry.key)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable | Qt.ItemIsEnabled)
            item.setSizeHint(QSize(0, TOOL_MENU_ICON_SIZE + 8))
            item.setCheckState(Qt.Unchecked)
        root.addWidget(self.tool_list, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        apply_btn = buttons.button(QDialogButtonBox.Ok)
        if apply_btn is not None:
            apply_btn.setText("Apply")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self.select_all_btn.clicked.connect(self._select_all)
        self.clear_all_btn.clicked.connect(self._clear_all)

    def _set_all_items(self, checked: bool) -> None:
        state = Qt.Checked if checked else Qt.Unchecked
        for index in range(self.tool_list.count()):
            item = self.tool_list.item(index)
            if item is not None:
                item.setCheckState(state)

    def _select_all(self) -> None:
        self._set_all_items(True)

    def _clear_all(self) -> None:
        self._set_all_items(False)

    def set_selected_tool_keys(self, tool_keys: list[str]) -> None:
        selected = set(normalize_tool_keys(tool_keys))
        for index in range(self.tool_list.count()):
            item = self.tool_list.item(index)
            if item is None:
                continue
            tool_key = str(item.data(Qt.UserRole) or "").strip()
            item.setCheckState(Qt.Checked if tool_key in selected else Qt.Unchecked)

    def selected_tool_keys(self) -> list[str]:
        selected: list[str] = []
        for index in range(self.tool_list.count()):
            item = self.tool_list.item(index)
            if item is None or item.checkState() != Qt.Checked:
                continue
            tool_key = str(item.data(Qt.UserRole) or "").strip()
            if tool_key:
                selected.append(tool_key)
        return normalize_tool_keys(selected)
