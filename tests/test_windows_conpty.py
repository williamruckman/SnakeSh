from __future__ import annotations

import hashlib
import io
from pathlib import Path
import runpy
from types import SimpleNamespace
from unittest.mock import Mock, patch
import zipfile

import pytest

from snakesh.services.windows_conpty import load_bundled_conpty


def test_loader_uses_absolute_bundled_dll_and_matching_lifecycle_exports(tmp_path):
    for name in ("conpty.dll", "OpenConsole.exe"):
        (tmp_path / name).touch()
    dll = SimpleNamespace(ConptyCreatePseudoConsole=Mock(), ConptyResizePseudoConsole=Mock(),
                          ConptyClosePseudoConsole=Mock())
    with (
        patch("snakesh.services.windows_conpty.bundled_conpty_directory", return_value=tmp_path),
        patch("ctypes.WinDLL", return_value=dll, create=True) as loader,
    ):
        api = load_bundled_conpty()
    assert api.library is dll
    assert api.CreatePseudoConsole is dll.ConptyCreatePseudoConsole
    assert api.ResizePseudoConsole is dll.ConptyResizePseudoConsole
    assert api.ClosePseudoConsole is dll.ConptyClosePseudoConsole
    loader.assert_called_once_with(str(tmp_path / "conpty.dll"), use_last_error=True, winmode=0x00001100)


def test_missing_console_host_is_an_error_instead_of_silent_old_runtime_fallback(tmp_path):
    (tmp_path / "conpty.dll").touch()
    with patch("snakesh.services.windows_conpty.bundled_conpty_directory", return_value=tmp_path):
        with pytest.raises(OSError, match="runtime is missing"):
            load_bundled_conpty()


def test_packaging_rejects_tampered_download_before_writing_files(tmp_path):
    script = Path(__file__).resolve().parents[1] / "packaging/windows/prepare_conpty.py"
    prepare = runpy.run_path(str(script))["prepare"]
    destination = tmp_path / "conpty"
    with pytest.raises(ValueError, match="checksum mismatch"):
        prepare(destination, b"tampered package")
    assert not destination.exists()


def test_packaging_keeps_native_files_and_microsoft_license_intact(tmp_path):
    script = Path(__file__).resolve().parents[1] / "packaging/windows/prepare_conpty.py"
    module = runpy.run_path(str(script))
    archive = io.BytesIO()
    payloads = {member: ("native file " + name).encode() for member, name in module["FILES"].items()}
    with zipfile.ZipFile(archive, "w") as stream:
        for member, data in payloads.items():
            stream.writestr(member, data)
    data = archive.getvalue()
    prepare = module["prepare"]
    prepare.__globals__["SHA256"] = hashlib.sha256(data).hexdigest()
    prepare(tmp_path, data)
    for member, name in module["FILES"].items():
        assert (tmp_path / name).read_bytes() == payloads[member]
    assert (tmp_path / "LICENSE.txt").read_bytes() == script.with_name("conpty-LICENSE.txt").read_bytes()
