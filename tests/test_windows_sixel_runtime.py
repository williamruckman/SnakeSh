"""Real Windows encoder -> ConPTY -> SnakeSh renderer integration.

The Windows packaging job installs the opt-in converter into the CI user's
module directories and exercises both available PowerShell editions.
"""
from __future__ import annotations

import base64
import shutil
import sys
import time

import pytest
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from snakesh.services.settings_service import AppSettings
from snakesh.services.sixel_setup import WINDOWS_SIXEL_VERSION, ensure_local_sixel_tools
from snakesh.services.windows_conpty import bundled_conpty_directory
from snakesh.ui.main_window import TerminalTab

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Requires the actual Windows console runtime")
_APP = None


@pytest.fixture(scope="module")
def windows_tools():
    global _APP
    if not (bundled_conpty_directory() / "conpty.dll").is_file():
        pytest.fail("Run packaging/windows/prepare_conpty.py before this integration test")
    success, message = ensure_local_sixel_tools()
    assert success, message
    _APP = QApplication.instance() or QApplication([])
    return _APP


@pytest.mark.parametrize("shell_name", ["pwsh.exe", "powershell.exe"])
def test_real_powershell_sixel_renders_through_windows_local_terminal(windows_tools, tmp_path, shell_name):
    shell = shutil.which(shell_name)
    if shell is None:
        pytest.skip(f"{shell_name} is not installed")
    source = QImage(40, 12, QImage.Format_RGB32)
    source.fill(QColor("red"))
    image_path = tmp_path / "red.png"
    assert source.save(str(image_path))
    quoted_path = str(image_path).replace("'", "''")
    script = f"""
$ErrorActionPreference = 'Stop'
try {{
    Import-Module Sixel -RequiredVersion {WINDOWS_SIXEL_VERSION} -ErrorAction Stop
    $image = ConvertTo-Sixel -Path '{quoted_path}' -Protocol Sixel -Force -ErrorAction Stop
    [Console]::Write([char]27 + '[2J' + [char]27 + '[H')
    [Console]::Write($image.ToString())
    [Console]::WriteLine('SIXEL COMPLETE')
}} catch {{
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}}
"""
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    settings = AppSettings.defaults()
    settings.terminal_sixel_local_enabled = True
    tab = TerminalTab(settings)
    try:
        tab.resize(800, 500)
        tab.show()
        windows_tools.processEvents()
        assert tab.start_local_shell(program=shell, arguments=[
            "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded,
        ])
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            windows_tools.processEvents()
            if tab._emulator.screen.graphics:
                break
            time.sleep(0.01)
        assert tab._emulator.screen.graphics, '\n'.join(tab._emulator.screen.display)
        pixel = tab._emulator.screen.graphics[0].image.pixelColor(2, 2)
        assert pixel.red() > 220 and pixel.green() < 30 and pixel.blue() < 30
        tab._render()
        capture = tab.output.viewport().grab().toImage()
        rect = tab.output._content_rect()
        painted = capture.pixelColor(rect.left() + 2, rect.top() + 2)
        assert painted.red() > 220 and painted.green() < 30 and painted.blue() < 30
    finally:
        tab.shutdown()
        tab.deleteLater()
        windows_tools.processEvents()
