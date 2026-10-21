"""Reject Intel crypto extensions that can collide with bundled OpenSSL dylibs."""
from __future__ import annotations

from pathlib import Path, PurePosixPath
import subprocess
import sys


def openssl_dylibs(otool_output: str) -> list[str]:
    dependencies = []
    for line in otool_output.splitlines()[1:]:
        fields = line.split()
        if not fields:
            continue
        path = fields[0]
        name = PurePosixPath(path).name
        if name.startswith(("libssl.", "libcrypto.")) and name.endswith(".dylib"):
            dependencies.append(path)
    return dependencies


def main() -> int:
    import ssl  # Load Python's OpenSSL first, as happens during app startup.
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.bindings import _rust
    from cryptography.fernet import Fernet

    extension = Path(_rust.__file__)
    output = subprocess.check_output(["otool", "-L", str(extension)], text=True)
    dynamic = openssl_dylibs(output)
    if dynamic:
        print("cryptography must link OpenSSL statically before freezing: " + ", ".join(dynamic), file=sys.stderr)
        return 1
    cipher = Fernet(Fernet.generate_key())
    if cipher.decrypt(cipher.encrypt(b"SnakeSh packaging probe")) != b"SnakeSh packaging probe":
        raise RuntimeError("cryptography encryption round trip failed")
    print("cryptography static-link and encryption checks passed "
          f"(crypto: {default_backend().openssl_version_text()}; Python: {ssl.OPENSSL_VERSION}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
