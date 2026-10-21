"""Run blocking calls without starving Qt's GUI event loop.

Callers keep their sequential authentication/confirmation flow on the GUI
thread. Only the supplied callable runs on the worker; it must not touch widgets.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from PySide6.QtCore import QEventLoop, QThread

_T = TypeVar("_T")
_active_calls: set[QThread] = set()


class _Call(QThread):
    def __init__(self, operation: Callable[[], _T]) -> None:
        super().__init__()
        self.operation = operation
        self.result: _T | None = None
        self.error: BaseException | None = None

    def run(self) -> None:
        try:
            self.result = self.operation()
        except BaseException as exc:
            self.error = exc


def background_calls_active() -> bool:
    return bool(_active_calls)


def run_background_call(operation: Callable[[], _T]) -> _T:
    call = _Call(operation)
    loop = QEventLoop()
    call.finished.connect(loop.quit)
    _active_calls.add(call)
    try:
        call.start()
        loop.exec()
        call.wait()
        if call.error is not None:
            raise call.error
        return call.result
    finally:
        _active_calls.discard(call)
        call.deleteLater()
