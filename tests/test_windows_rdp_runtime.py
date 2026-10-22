"""Check the RDP console helper from a real Windows GUI parent process."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Requires Windows process creation")


def test_rdp_credential_helper_has_no_console_from_gui_process(tmp_path):
    python = Path(sys.executable)
    pythonw = python.with_name("pythonw.exe")
    assert pythonw.is_file(), "This check requires Python's GUI executable"
    result_path = tmp_path / "console.json"
    driver_path = tmp_path / "gui_driver.py"
    # Replace cmdkey with a console probe so this check never writes credentials.
    # pythonw reproduces the installed GUI app's lack of an inherited console.
    probe = textwrap.dedent("""
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetConsoleWindow.restype = wintypes.HWND
        print(int(kernel.GetConsoleWindow() or 0))
    """)
    driver_path.write_text(textwrap.dedent(f"""
        import json
        from pathlib import Path
        from types import SimpleNamespace
        from snakesh.protocols import rdp
        run = rdp.subprocess.run
        observations = []
        def run_probe(command, **kwargs):
            assert command[0] == 'cmdkey'
            result = run([{str(python)!r}, '-c', {probe!r}], **kwargs)
            observations.append(int(result.stdout.strip()))
            return result
        rdp.subprocess.run = run_probe
        session = SimpleNamespace(host='rdp.example.test', port=3389)
        rdp._seed_windows_rdp_credentials(session, 'test-user', 'test-password')
        Path({str(result_path)!r}).write_text(json.dumps(observations), encoding='utf-8')
    """), encoding="utf-8")
    result = subprocess.run(
        [str(pythonw), str(driver_path)], capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert result_path.is_file(), "GUI driver did not finish the console check"
    assert json.loads(result_path.read_text(encoding="utf-8")) == [0], "RDP helper acquired a console window"
