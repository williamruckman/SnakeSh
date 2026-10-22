from __future__ import annotations

from unittest.mock import Mock, patch
import shutil
import subprocess

import pytest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
from PySide6.QtGui import QColor, QImage

from snakesh.services.settings_service import AppSettings
from snakesh.services.sixel_setup import ensure_local_sixel_tools
from snakesh.services.privilege_service import run_command
from snakesh.ui.main_window import TerminalTab, VT100Emulator
from snakesh.ui.settings_dialog import SettingsDialog

_APP = None
IMAGE = '\x1bP0;1q#1;2;100;0;0!40~\x1b\\'


@pytest.fixture
def app():
    global _APP
    _APP = QApplication.instance() or QApplication([])
    return _APP


def test_sixel_flags_default_off_and_roundtrip_independently():
    settings = AppSettings.defaults()
    assert not settings.terminal_sixel_local_enabled
    assert not settings.terminal_sixel_remote_enabled
    settings.terminal_sixel_remote_enabled = True
    restored = AppSettings.from_dict(settings.to_dict())
    assert restored.terminal_sixel_remote_enabled
    assert not restored.terminal_sixel_local_enabled
    legacy = settings.to_dict()
    legacy.pop("terminal_sixel_local_enabled")
    legacy.pop("terminal_sixel_remote_enabled")
    assert not AppSettings.from_dict(legacy).terminal_sixel_remote_enabled


def test_capability_reply_and_decoder_follow_enabled_setting():
    replies = []
    emulator = VT100Emulator(80, 24, process_input_writer=replies.append)
    emulator.feed('\x1b[c' + IMAGE)
    assert replies == ['\x1b[?6c']
    assert not emulator.screen.graphics
    emulator.set_sixel_enabled(True)
    emulator.feed('\x1b[c' + IMAGE)
    assert ';4;' in replies[-1]
    assert len(emulator.screen.graphics) == 1
    emulator.set_sixel_enabled(False)
    assert not emulator.screen.graphics


def test_local_sixel_reaches_terminal_framebuffer_and_remote_switch_is_independent(app):
    settings = AppSettings.defaults()
    settings.terminal_sixel_local_enabled = True
    tab = TerminalTab(settings)
    try:
        tab.resize(800, 500)
        tab.show()
        app.processEvents()
        tab.append(IMAGE)
        tab._render()
        image = tab.output.viewport().grab().toImage()
        rect = tab.output._content_rect()
        assert image.pixelColor(rect.left() + 2, rect.top() + 2).getRgb() == (255, 0, 0, 255)
        tab._apply_sixel_context(remote=True)
        tab.append(IMAGE)
        assert not tab._emulator.screen.graphics
        settings.terminal_sixel_remote_enabled = True
        settings.terminal_sixel_local_enabled = False
        tab.apply_settings(settings)
        tab.append(IMAGE)
        assert len(tab._emulator.screen.graphics) == 1
        tab._apply_sixel_context(remote=False)
        tab.append(IMAGE)
        assert not tab._emulator.screen.graphics
    finally:
        tab.shutdown()
        tab.deleteLater()
        app.processEvents()


@pytest.mark.parametrize("local,remote", [(False, True), (True, False)])
def test_settings_save_installs_local_tools_only_when_local_is_enabled(app, local, remote):
    setup = Mock(return_value=(True, "Ready"))
    dialog = SettingsDialog(AppSettings.defaults(), on_sixel_setup_requested=setup)
    try:
        dialog.sixel_local.setChecked(local)
        dialog.sixel_remote.setChecked(remote)
        dialog._validate_and_accept()
        assert dialog.result() == QDialog.Accepted
        assert setup.call_count == int(local)
        assert dialog.build_settings().terminal_sixel_remote_enabled == remote
        assert dialog.build_settings().terminal_sixel_local_enabled == local
    finally:
        dialog.deleteLater()
        app.processEvents()


def test_failed_local_install_keeps_settings_open(app):
    dialog = SettingsDialog(AppSettings.defaults(), on_sixel_setup_requested=lambda: (False, "Install cancelled"))
    try:
        dialog.sixel_local.setChecked(True)
        with patch("snakesh.ui.settings_dialog.QMessageBox.warning") as warning:
            dialog._validate_and_accept()
        assert dialog.result() != QDialog.Accepted
        assert "Install cancelled" in warning.call_args.args
    finally:
        dialog.deleteLater()
        app.processEvents()


def test_linux_mint_installs_official_sixel_tools_only_if_missing():
    with (
        patch("snakesh.services.sixel_setup.platform.system", return_value="Linux"),
        patch("snakesh.services.sixel_setup.shutil.which", side_effect=lambda name: "/usr/bin/apt-get" if name == "apt-get" else None),
        patch("snakesh.services.sixel_setup.attempt_auto_install", return_value=(True, "Ready")) as install,
    ):
        assert ensure_local_sixel_tools()[0]
        dependency = install.call_args.args[0]
        assert dependency.install_command == ["/usr/bin/apt-get", "install", "-y", "libsixel-bin"]
    with (
        patch("snakesh.services.sixel_setup.shutil.which", return_value="/usr/bin/img2sixel"),
        patch("snakesh.services.sixel_setup.attempt_auto_install") as install,
    ):
        assert ensure_local_sixel_tools()[0]
        install.assert_not_called()


def test_system_img2sixel_output_renders_in_snakesh(app, tmp_path):
    converter = shutil.which("img2sixel")
    if converter is None:
        pytest.skip("img2sixel is required for the real encoder integration check.")
    source = QImage(40, 12, QImage.Format_RGB32)
    source.fill(QColor("red"))
    path = tmp_path / "red.png"
    assert source.save(str(path))
    result = subprocess.run([converter, "-w", "40", str(path)], stdin=subprocess.DEVNULL,
                            capture_output=True, check=True, timeout=10)
    emulator = VT100Emulator(80, 24, sixel_enabled=True)
    output = result.stdout.decode("utf-8")
    for start in range(0, len(output), 7):
        emulator.feed(output[start:start + 7])
    assert len(emulator.screen.graphics) == 1
    pixel = emulator.screen.graphics[0].image.pixelColor(2, 2)
    assert pixel.red() > 220 and pixel.green() < 30 and pixel.blue() < 30


def test_compiled_app_install_runs_host_elevation_without_bundled_libraries():
    with (
        patch("snakesh.services.privilege_service.platform.system", return_value="Linux"),
        patch("snakesh.services.privilege_service.is_elevated", return_value=False),
        patch("snakesh.services.privilege_service.shutil.which", side_effect=lambda name: "/usr/bin/" + name),
        patch.dict("os.environ", {
            "APPDIR": "/tmp/.mount_SnakeSh-install", "DISPLAY": ":0",
            "LD_LIBRARY_PATH": "/tmp/.mount_SnakeSh-install/usr/lib",
        }, clear=True),
        patch("snakesh.services.privilege_service.subprocess.run",
              return_value=subprocess.CompletedProcess([], 0, "installed", "")) as runner,
    ):
        result = run_command(["apt-get", "install", "-y", "libsixel-bin"], require_elevation=True)
    assert result.success
    assert runner.call_args.args[0][:2] == ["pkexec", "/usr/bin/apt-get"]
    assert runner.call_args.kwargs["env"]["DISPLAY"] == ":0"
    assert "LD_LIBRARY_PATH" not in runner.call_args.kwargs["env"]
