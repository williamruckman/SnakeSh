from __future__ import annotations

from pathlib import Path
import platform
import shutil
import subprocess

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices


def _platform_name(platform_name: str | None = None) -> str:
    return (platform_name or platform.system()).strip().lower()


def open_local_path(target: str | Path, *, platform_name: str | None = None) -> bool:
    path = Path(target).expanduser()
    if _platform_name(platform_name) == "linux":
        for command in _linux_open_commands(path):
            if shutil.which(command[0]) is None:
                continue
            try:
                subprocess.Popen(  # noqa: S603
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    close_fds=True,
                    start_new_session=True,
                )
            except OSError:
                continue
            return True
    return QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


def _linux_open_commands(path: Path) -> tuple[list[str], ...]:
    target = str(path)
    return (
        ["xdg-open", target],
        ["gio", "open", target],
        ["kioclient5", "exec", target],
        ["kioclient", "exec", target],
    )
