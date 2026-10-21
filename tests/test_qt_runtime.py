"""Exercise the Qt binding in a child so native aborts have useful diagnostics."""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap


def test_repeated_qt_void_calls_do_not_abort_python() -> None:
    # PySide6 6.12.0 loses references to None here on Python 3.11. The failure
    # aborts the interpreter rather than raising a catchable Python exception.
    script = textwrap.dedent("""\
        from PySide6.QtWidgets import QApplication, QWidget
        app = QApplication([])
        widget = QWidget()
        for _ in range(20_000):
            widget.setWindowTitle("Qt runtime probe")
        app.processEvents()
    """)
    result = subprocess.run(
        [sys.executable, "-X", "faulthandler", "-c", script],
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"Qt runtime probe exited with {result.returncode}.\n"
        f"{result.stdout}\n{result.stderr}"
    )
