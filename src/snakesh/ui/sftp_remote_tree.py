from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QMimeData, Qt
from PySide6.QtGui import QDrag, QPainter
from PySide6.QtWidgets import QAbstractItemView, QTreeWidget

if TYPE_CHECKING:
    from snakesh.ui.main_window import SFTPSessionTab


class SFTPRemoteTree(QTreeWidget):
    """Move a snapshot of this tab's remote selection onto a directory row."""

    MOVE_MIME = "application/x-snakesh-sftp-remote-move"

    def __init__(self, owner: SFTPSessionTab, *, navigation: bool = False) -> None:
        super().__init__(owner)
        self._owner = owner
        self._drag_paths: list[str] = []
        self._drop_rect = None
        self.setDragEnabled(not navigation)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DropOnly if navigation else QAbstractItemView.DragDrop)
        self.setDefaultDropAction(Qt.MoveAction)

    def startDrag(self, _supported_actions) -> None:  # noqa: N802
        if not self._owner._remote_move_available():
            return
        paths = self._owner._selected_remote_paths()
        if not paths:
            return
        mime = QMimeData()
        mime.setData(self.MOVE_MIME, b"1")
        drag = QDrag(self)
        drag.setMimeData(mime)
        self._drag_paths = paths
        try:
            # We perform the server operation ourselves. Qt must not remove
            # model rows merely because a MoveAction was accepted.
            drag.exec(Qt.MoveAction)
        finally:
            self._drag_paths = []

    def _move_target(self, event) -> tuple[list[str], str] | None:
        source = event.source()
        if source is not self._owner.remote_file_tree or not self._owner._remote_move_available():
            return None
        paths = source._drag_paths
        item = self.itemAt(event.position().toPoint())
        if not paths or item is None or not item.data(0, self._owner.REMOTE_IS_DIR_ROLE):
            return None
        directory = item.data(0, self._owner.REMOTE_PATH_ROLE)
        if not isinstance(directory, str) or not directory:
            return None
        if any(directory == path or directory.startswith(path.rstrip("/") + "/") for path in paths):
            return None
        self._drop_rect = self.visualItemRect(item)
        return list(paths), directory

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasFormat(self.MOVE_MIME):
            if event.source() is self._owner.remote_file_tree and self._owner._remote_move_available():
                event.setDropAction(Qt.MoveAction)
                event.accept()
            else:
                event.ignore()
        else:
            self._owner.dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        self._drop_rect = None
        if event.mimeData().hasFormat(self.MOVE_MIME):
            if self._move_target(event) is not None:
                event.setDropAction(Qt.MoveAction)
                event.accept()
            else:
                event.ignore()
        else:
            self._owner.dragEnterEvent(event)
        self.viewport().update()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self._drop_rect = None
        self.viewport().update()
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasFormat(self.MOVE_MIME):
            target = self._move_target(event)
            self._drop_rect = None
            self.viewport().update()
            if target is not None and self._owner._move_remote_paths(*target):
                event.setDropAction(Qt.MoveAction)
                event.accept()
            else:
                event.ignore()
            return
        self._owner.dropEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        if self._drop_rect is not None:
            painter = QPainter(self.viewport())
            color = self.palette().highlight().color()
            painter.setPen(color)
            color.setAlpha(45)
            painter.setBrush(color)
            painter.drawRect(self._drop_rect.adjusted(0, 0, -1, -1))
            painter.end()
