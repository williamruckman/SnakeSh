from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch


path = Path(__file__).resolve().parents[1] / "packaging/macos/check_cryptography_linkage.py"
spec = importlib.util.spec_from_file_location("check_cryptography_linkage", path)
linkage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(linkage)

STATIC = """/build/_rust.abi3.so:
    /usr/lib/libSystem.B.dylib (compatibility version 1.0.0, current version 1345.120.2)
"""
DYNAMIC = STATIC + """    @rpath/libssl.3.dylib (compatibility version 3.0.0, current version 3.6.0)
    /usr/local/opt/openssl@3/lib/libcrypto.3.dylib (compatibility version 3.0.0, current version 3.6.0)
"""


def test_static_crypto_extension_has_no_external_openssl_dependencies():
    assert linkage.openssl_dylibs(STATIC) == []


def test_detects_both_rpath_and_absolute_openssl_dependencies():
    assert linkage.openssl_dylibs(DYNAMIC) == [
        "@rpath/libssl.3.dylib", "/usr/local/opt/openssl@3/lib/libcrypto.3.dylib",
    ]


def test_build_gate_rejects_dynamic_crypto_extension(capsys):
    with patch.object(linkage.subprocess, "check_output", return_value=DYNAMIC):
        assert linkage.main() == 1
    assert "must link OpenSSL statically" in capsys.readouterr().err


def test_build_gate_accepts_static_linkage_and_checks_real_encryption(capsys):
    with patch.object(linkage.subprocess, "check_output", return_value=STATIC):
        assert linkage.main() == 0
    assert "encryption checks passed" in capsys.readouterr().out
