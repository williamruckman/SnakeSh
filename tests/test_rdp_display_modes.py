"""Exercise display preferences at the external RDP client boundary."""
from pathlib import Path
from unittest.mock import patch

import pytest

from snakesh.core.models import Protocol, Session
from snakesh.protocols.rdp import build_rdp_command


@pytest.mark.parametrize("system", ["Darwin", "Linux"])
@pytest.mark.parametrize("fullscreen", [False, True])
def test_freerdp_auto_keeps_remote_resize_enabled_when_starting_fullscreen(system, fullscreen):
    session = Session(
        id="display-test", name="RDP", host="example.test", protocol=Protocol.RDP,
        port=3389, display_resolution="auto", display_fullscreen=fullscreen,
    )
    executable = "/opt/homebrew/bin/sdl-freerdp" if system == "Darwin" else "/usr/bin/xfreerdp"
    with (
        patch("snakesh.protocols.rdp.platform.system", return_value=system),
        patch("snakesh.protocols.rdp._freerdp_executable", return_value=executable),
    ):
        command = build_rdp_command(session)

    assert command[0] == executable
    assert ("/f" in command) == fullscreen
    assert "/dynamic-resolution" in command
    assert "/smart-sizing" not in command


@pytest.mark.parametrize("fullscreen", [False, True])
@pytest.mark.parametrize("resolution", ["auto", "1920x1080", ""])
def test_mstsc_profiles_use_supported_scaling_for_auto_in_either_start_mode(tmp_path, fullscreen, resolution):
    session = Session(
        id="display-test", name="RDP", host="example.test", protocol=Protocol.RDP,
        port=3389, username="tester", display_resolution=resolution,
        display_fullscreen=fullscreen,
    )
    with (
        patch("snakesh.protocols.rdp.platform.system", return_value="Windows"),
        patch("snakesh.protocols.rdp._windows_mstsc_executable", return_value="mstsc.exe"),
        patch("snakesh.protocols.rdp.tempfile.gettempdir", return_value=str(tmp_path)),
    ):
        command = build_rdp_command(session)

    assert command[0] == "mstsc.exe"
    profile = Path(command[1]).read_text(encoding="utf-8").splitlines()
    assert f"screen mode id:i:{2 if fullscreen else 1}" in profile
    assert ("smart sizing:i:1" in profile) == (resolution == "auto")
    # mstsc.exe does not implement the newer clients' live resolution option.
    assert not any(line.startswith("dynamic resolution:") for line in profile)
    if resolution == "1920x1080" and not fullscreen:
        assert "desktopwidth:i:1920" in profile
        assert "desktopheight:i:1080" in profile
    else:
        assert not any(line.startswith(("desktopwidth:", "desktopheight:")) for line in profile)
