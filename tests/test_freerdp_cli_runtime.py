"""Use FreeRDP's real shared parser; never start a connection or a desktop."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
from unittest.mock import patch

import pytest

from snakesh.core.models import Protocol, Session
from snakesh.protocols.rdp import build_rdp_command
from snakesh.services.rdp_resize import smart_sizing_command


@pytest.fixture(scope="module")
def probe(tmp_path_factory):
    compiler = shutil.which("cc")
    pkg_config = shutil.which("pkg-config")
    packages = ["freerdp-client3", "freerdp3", "winpr3"]
    available = compiler and pkg_config and subprocess.run(
        [pkg_config, "--exists", *packages], check=False,
    ).returncode == 0
    if not available:
        if os.environ.get("SNAKESH_REQUIRE_FREERDP_PARSER_TEST") == "1":
            pytest.fail("FreeRDP 3 SDK and a C compiler are required by this release gate.")
        pytest.skip("FreeRDP 3 SDK/compiler unavailable; enforced by the FreeRDP CLI release gate.")
    flags = subprocess.check_output([pkg_config, "--cflags", "--libs", *packages], text=True)
    executable = tmp_path_factory.mktemp("freerdp-parser") / "probe"
    source = Path(__file__).parent / "native" / "freerdp_cli_probe.c"
    subprocess.run([compiler, str(source), "-o", str(executable), *shlex.split(flags)], check=True)
    return executable


@pytest.mark.parametrize("resolution", ["auto", "1920x1080", ""])
@pytest.mark.parametrize("fullscreen", [False, True])
def test_generated_mac_launch_parses_and_maps_command_to_the_correct_control_keys(probe, resolution, fullscreen):
    session = Session(id="parse", name="Parse only", host="example.test", protocol=Protocol.RDP,
                      port=3389, username="tester", display_resolution=resolution, display_fullscreen=fullscreen)
    with (
        patch("snakesh.protocols.rdp.platform.system", return_value="Darwin"),
        patch("snakesh.protocols.rdp._freerdp_executable", return_value="sdl-freerdp"),
    ):
        command = build_rdp_command(session)
    commands = [command]
    if resolution == "auto":
        commands.append(smart_sizing_command(command))
    for arguments in commands:
        result = subprocess.run([str(probe), *arguments[1:]], capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stdout + result.stderr
        parsed = json.loads(result.stdout)
        assert parsed["valid"] is True
        assert parsed["left_command"] == 0x1D
        assert parsed["right_command"] == 0x11D
