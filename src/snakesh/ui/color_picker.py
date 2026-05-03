from __future__ import annotations

from collections.abc import Callable
import platform

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QColorDialog, QWidget


def pick_color(
    parent: QWidget | None,
    *,
    title: str,
    initial: QColor,
    on_preview: Callable[[QColor], None] | None = None,
) -> QColor:
    dialog = QColorDialog(parent)
    dialog.setWindowTitle(title)
    if platform.system().lower() == "linux":
        dialog.setOption(QColorDialog.DontUseNativeDialog, True)
    seed_color = QColor(initial) if initial.isValid() else QColor("#ffffff")
    dialog.setCurrentColor(seed_color)
    if on_preview is not None:
        dialog.currentColorChanged.connect(on_preview)
    if dialog.exec():
        chosen = dialog.selectedColor()
        if chosen.isValid():
            return chosen
    if on_preview is not None and initial.isValid():
        on_preview(initial)
    return QColor()
