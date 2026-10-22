from __future__ import annotations

import os
import sys

import pytest

from snakesh.services.linux_rdp_window import LinuxRdpWindow


@pytest.fixture
def x11_windows():
    if sys.platform != "linux" or not os.environ.get("DISPLAY"):
        pytest.skip("An X11 server is required (use xvfb-run).")
    from Xlib import X, Xatom
    from Xlib.display import Display

    display = Display()
    root = display.screen().root
    # Keep both windows unmapped so this test never displays a desktop window.
    host = root.create_window(0, 0, 640, 480, 0, X.CopyFromParent)
    child = host.create_window(0, 0, 320, 240, 0, X.CopyFromParent)
    child.set_wm_class("xfreerdp", "xfreerdp")
    child.change_property(display.intern_atom("_NET_WM_PID"), Xatom.CARDINAL, 32, [os.getpid()])
    display.sync()
    helper = LinuxRdpWindow()
    try:
        yield display, host, child, helper
    finally:
        helper.close()
        host.destroy()
        display.sync()
        display.close()


def test_find_and_resize_unmapped_rdp_child_without_external_tools(x11_windows):
    display, host, child, helper = x11_windows
    assert helper.find_child(host.id, {os.getpid()}) == child.id
    for width, height in [(1440, 900), (800, 600), (1920, 1080)]:
        assert helper.resize(child.id, width, height)
        geometry = child.get_geometry()
        assert (geometry.x, geometry.y, geometry.width, geometry.height) == (0, 0, width, height)


def test_destroyed_or_replaced_child_is_recovered_safely(x11_windows):
    from Xlib import X

    display, host, child, helper = x11_windows
    child.destroy()
    display.sync()
    assert helper.find_child(host.id, {os.getpid()}) is None
    assert not helper.resize(child.id, 800, 600)
    replacement = host.create_window(0, 0, 800, 600, 0, X.CopyFromParent)
    replacement.set_wm_class("xfreerdp", "FreeRDP")
    display.sync()
    assert helper.find_child(host.id, {os.getpid()}) == replacement.id
