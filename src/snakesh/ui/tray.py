from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QTimer, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from snakesh import runtime


class TrayController(QObject):
    def __init__(self, window, enabled: bool = False):
        super().__init__(window)
        self.window = window
        self.enabled = False
        self.icon = QSystemTrayIcon(QIcon(str(runtime.asset_path("snakesh-icon.png"))), self)
        self.icon.setToolTip("SnakeSh")
        self.menu = QMenu(window)
        self.menu.addAction("Show SnakeSh", self.restore)
        self.menu.addSeparator()
        self.menu.addAction("Quit SnakeSh", window.close)
        self.icon.setContextMenu(self.menu)
        self.icon.activated.connect(self._activated)
        window.installEventFilter(self)
        self.set_enabled(enabled)

    def set_enabled(self, enabled: bool):
        self.enabled = bool(enabled)
        if self.enabled and QSystemTrayIcon.isSystemTrayAvailable():
            self.icon.show()
        else:
            if not self.window.isVisible() and self.icon.isVisible():
                self.restore()
            self.icon.hide()

    def eventFilter(self, watched, event):
        if watched is self.window and event.type() == QEvent.WindowStateChange:
            if self.enabled and self.window.isMinimized() and QSystemTrayIcon.isSystemTrayAvailable():
                QTimer.singleShot(0, self._hide_minimized)
        return False

    def _hide_minimized(self):
        if self.enabled and self.window.isMinimized() and self.icon.isVisible():
            self.window.hide()

    def restore(self):
        self.window.setWindowState(self.window.windowState() & ~Qt.WindowMinimized)
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def _activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.restore()

    def shutdown(self):
        self.icon.hide()
