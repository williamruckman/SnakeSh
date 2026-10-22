"""Side-by-side Microsoft ConPTY for local Windows Sixel passthrough."""
from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace


def bundled_conpty_directory() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "windows-conpty"
    return Path(__file__).resolve().parents[3] / "build" / "windows" / "conpty"


def load_bundled_conpty():
    import ctypes

    directory = bundled_conpty_directory()
    library = directory / "conpty.dll"
    if not library.is_file() or not (directory / "OpenConsole.exe").is_file():
        raise OSError("The Windows Sixel console runtime is missing. Reinstall the current SnakeSh package.")
    # An absolute path avoids loading a DLL from the current working directory.
    dll = ctypes.WinDLL(str(library), use_last_error=True, winmode=0x00001100)
    return SimpleNamespace(
        library=dll,
        CreatePseudoConsole=dll.ConptyCreatePseudoConsole,
        ResizePseudoConsole=dll.ConptyResizePseudoConsole,
        ReleasePseudoConsole=dll.ConptyReleasePseudoConsole,
        ClosePseudoConsole=dll.ConptyClosePseudoConsole,
    )
