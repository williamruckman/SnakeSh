from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QPixmap

from snakesh.core.tool_icons import app_icon_path, tool_icon_path


TOOL_MENU_ICON_SIZE = 33


def scaled_menu_icon(icon_path: Path) -> QIcon:
    pixmap = QPixmap(str(icon_path))
    if pixmap.isNull():
        return QIcon(str(icon_path))
    return QIcon(
        pixmap.scaled(
            QSize(TOOL_MENU_ICON_SIZE, TOOL_MENU_ICON_SIZE),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    )


def tool_menu_icon(tool_key: str) -> QIcon:
    return scaled_menu_icon(tool_icon_path(tool_key, "png"))


def app_menu_icon() -> QIcon:
    return scaled_menu_icon(app_icon_path("png"))
