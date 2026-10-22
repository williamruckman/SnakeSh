from __future__ import annotations

from unittest.mock import Mock, patch

import pytest
from PySide6.QtWidgets import QApplication

from snakesh.core.models import Protocol, Session
from snakesh.protocols.rdp import build_rdp_command, prepare_rdp_launch_environment
from snakesh.ui.main_window import RemoteViewerTab

_APP = None


@pytest.fixture
def tab():
    global _APP
    _APP = QApplication.instance() or QApplication([])
    session = Session(id="rdp", name="Test", host="example.test", protocol=Protocol.RDP, port=3389)
    widget = RemoteViewerTab(
        session=session, protocol_name="RDP",
        detached_command_builder=lambda: (["xfreerdp"], "FreeRDP", None),
        embedded_command_builder=lambda parent: (["xfreerdp", f"/parent-window:{parent}"], "FreeRDP", None),
    )
    try:
        yield widget
    finally:
        widget._embedded_process = None
        widget.shutdown()
        widget.deleteLater()
        _APP.processEvents()


def test_temporary_missing_rdp_child_does_not_change_running_tab_to_detached(tab):
    process = Mock(pid=12345)
    process.poll.return_value = None
    tab._mode = "embedded"
    tab._embedded_process = process
    tab._update_state_text()
    with (
        patch("snakesh.ui.main_window.platform.system", return_value="Linux"),
        patch.object(tab._linux_rdp_window, "find_child", return_value=None),
        patch.object(tab, "_fallback_linux_embedded_to_detached") as fallback,
    ):
        for _ in range(10):
            tab._watch_process_state()
    assert tab._mode == "embedded"
    assert tab._embedded_process is process
    assert "Embedded mode active" in tab._state_label.text()
    fallback.assert_not_called()


def test_rdp_rejects_non_x11_qt_window_ids(tab):
    with (
        patch("snakesh.ui.main_window.platform.system", return_value="Linux"),
        patch.dict("os.environ", {"DISPLAY": ":99"}),
        patch("snakesh.ui.main_window.QApplication.platformName", return_value="wayland"),
        patch.object(tab, "_start_viewer_process") as launch,
    ):
        success, message = tab.attach_viewer()
    assert not success
    assert "X11 backend" in message
    launch.assert_not_called()


def test_rdp_initial_size_and_resize_use_physical_pixels(tab):
    with patch.object(tab._embed_host, "devicePixelRatioF", return_value=2.0):
        size = tab._linux_rdp_target_size()
    assert size == (tab._embed_host.width() * 2, tab._embed_host.height() * 2)


@pytest.mark.parametrize("resolution", ["", "1920x1080", "auto"])
def test_embedded_rdp_fits_tab_without_conflicting_scaling_options(resolution):
    session = Session(id="rdp", name="Test", host="example.test", protocol=Protocol.RDP, port=3389)
    session.display_resolution = resolution
    with patch("snakesh.protocols.rdp.platform.system", return_value="Linux"):
        command = build_rdp_command(session, linux_parent_window_id=1234)
    assert ("/dynamic-resolution" in command) == (resolution == "auto")
    assert ("/smart-sizing" in command) == (resolution != "auto")


def test_system_freerdp_environment_excludes_appimage_libraries():
    with (
        patch("snakesh.protocols.rdp.platform.system", return_value="Linux"),
        patch.dict("os.environ", {
            "APPDIR": "/tmp/.mount_SnakeSh-test", "DISPLAY": ":1",
            "LD_LIBRARY_PATH": "/tmp/.mount_SnakeSh-test/usr/lib/snakesh/_internal",
            "QT_PLUGIN_PATH": "/tmp/.mount_SnakeSh-test/usr/lib/qt/plugins",
        }, clear=True),
    ):
        environment = prepare_rdp_launch_environment("/usr/bin/xfreerdp")
    assert environment["DISPLAY"] == ":1"
    assert "LD_LIBRARY_PATH" not in environment
    assert "QT_PLUGIN_PATH" not in environment
