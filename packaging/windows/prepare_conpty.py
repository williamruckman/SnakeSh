"""Fetch the pinned official Microsoft console runtime for Windows packaging."""
from __future__ import annotations

import hashlib
import io
from pathlib import Path
import shutil
import time
import urllib.request
import zipfile

VERSION = "1.25.260930003"
SHA256 = "02b07b349af66d801159bdf9e440d4a1ce78bb951f37fc8609731665afdae7ee"
URL = ("https://api.nuget.org/v3-flatcontainer/microsoft.windows.console.conpty/"
       f"{VERSION}/microsoft.windows.console.conpty.{VERSION}.nupkg")
FILES = {
    "runtimes/win-x64/native/conpty.dll": "conpty.dll",
    "build/native/runtimes/x64/OpenConsole.exe": "OpenConsole.exe",
}


def prepare(destination: Path, package: bytes) -> None:
    if hashlib.sha256(package).hexdigest() != SHA256:
        raise ValueError("Microsoft ConPTY package checksum mismatch")
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        # Read every required member before modifying the build output.
        payloads = {name: archive.read(member) for member, name in FILES.items()}
    destination.mkdir(parents=True, exist_ok=True)
    for name, data in payloads.items():
        (destination / name).write_bytes(data)
    shutil.copy2(Path(__file__).with_name("conpty-LICENSE.txt"), destination / "LICENSE.txt")


def main() -> None:
    for attempt in range(4):
        try:
            with urllib.request.urlopen(URL, timeout=60) as response:
                package = response.read()
            break
        except OSError:
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)
    destination = Path(__file__).resolve().parents[2] / "build" / "windows" / "conpty"
    prepare(destination, package)
    print(f"Verified Microsoft ConPTY {VERSION}: {destination}")


if __name__ == "__main__":
    main()
