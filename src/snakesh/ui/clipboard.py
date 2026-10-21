"""Clipboard actions which never wait for a remote Windows owner in the UI."""
from __future__ import annotations

from collections.abc import Callable
import json
import logging
from pathlib import Path
import platform
import tempfile

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer
from PySide6.QtWidgets import QApplication

from snakesh import runtime

_requests: set[QObject] = set()


def _report_error(message: str) -> None:
    logging.getLogger(__name__).warning("Clipboard: %s", message)
    window = QApplication.activeWindow()
    if window is not None and hasattr(window, "statusBar"):
        window.statusBar().showMessage(message, 7000)


class _Request(QObject):
    def __init__(self, text: str | None, callback: Callable[[str], None] | None):
        super().__init__(QApplication.instance())
        self.callback = callback
        self.directory = tempfile.TemporaryDirectory(prefix="snakesh-clipboard-")
        self.root = Path(self.directory.name)
        (self.root / "request.json").write_text(json.dumps({"text": text}), encoding="utf-8")
        self.process = QProcess(self)
        environment = QProcessEnvironment()
        for key, value in runtime.sanitized_self_launch_environment().items():
            environment.insert(key, value)
        self.process.setProcessEnvironment(environment)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._timeout)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._error)
        self.done = False
        _requests.add(self)
        command = runtime.self_launch_command(["--clipboard-helper", str(self.root)])
        self.process.start(command[0], command[1:])
        self.timer.start(3000)

    def _timeout(self):
        _report_error("Clipboard request timed out. Check the RDP clipboard connection and retry.")
        self.process.kill()

    def _error(self, error):
        if error == QProcess.FailedToStart:
            _report_error("Unable to start the clipboard helper.")
            self._cleanup()

    def _finished(self, *_args):
        if self.done:
            return
        try:
            result = json.loads((self.root / "result.json").read_text(encoding="utf-8"))
            if result.get("ok"):
                if self.callback:
                    self.callback(str(result.get("text", "")))
            else:
                _report_error(str(result.get("error", "Clipboard unavailable.")))
        except (OSError, ValueError):
            pass  # A timed-out helper has no result.
        except RuntimeError:
            pass  # The original paste target has closed.
        finally:
            self._cleanup()

    def _cleanup(self):
        if self.done:
            return
        self.done = True
        self.timer.stop()
        self.callback = None
        self.directory.cleanup()
        _requests.discard(self)
        self.deleteLater()


def copy_text(text: str) -> None:
    if platform.system().lower() == "windows":
        _Request(text, None)
    else:
        QApplication.clipboard().setText(text)


def paste_text(callback: Callable[[str], None]) -> None:
    if platform.system().lower() == "windows":
        _Request(None, callback)
    else:
        callback(QApplication.clipboard().text())
