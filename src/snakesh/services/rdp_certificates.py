"""Explicit replacement of one reviewed FreeRDP certificate pin."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import tempfile

from snakesh.core.models import Session

_FINGERPRINT = r"(?:[0-9a-fA-F]{2}:){31}[0-9a-fA-F]{2}"


@dataclass(frozen=True)
class RdpCertificateChange:
    previous: str
    current: str


def certificate_change_from_output(output: str) -> RdpCertificateChange | None:
    sections = re.search(r"New Certificate details:(.*?)Old Certificate details:(.*)", output, re.S)
    if sections is None:
        return None
    current = re.search(r"Thumbprint:\s*(" + _FINGERPRINT + r")", sections[1])
    previous = re.search(r"Thumbprint:\s*(" + _FINGERPRINT + r")", sections[2])
    if current is None or previous is None or current[1].lower() == previous[1].lower():
        return None
    return RdpCertificateChange(previous[1].lower(), current[1].lower())


def replace_reviewed_certificate(
    session: Session, change: RdpCertificateChange, *, known_hosts_path: Path | None = None
) -> None:
    if not all(re.fullmatch(_FINGERPRINT, value) for value in (change.previous, change.current)):
        raise ValueError("Invalid certificate fingerprint.")
    config = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    path = (known_hosts_path or config / "freerdp" / "known_hosts2").resolve()
    original = path.read_text(encoding="utf-8")
    lines = original.splitlines(keepends=True)
    updated = False
    for index, line in enumerate(lines):
        fields = line.split()
        if len(fields) < 3 or fields[0].strip("[]").lower() != session.host.strip("[]").lower():
            continue
        if fields[1] != str(session.port):
            continue
        if fields[2].lower() != change.previous:
            raise ValueError("The saved certificate changed while the approval dialog was open. Reconnect to review it again.")
        lines[index] = line.replace(fields[2], change.current, 1)
        updated = True
    if not updated:
        raise ValueError("The previous certificate was not found in FreeRDP's saved hosts. No trust records were changed.")
    if path.read_text(encoding="utf-8") != original:
        raise ValueError("FreeRDP's saved hosts changed during review. Reconnect to review the certificate again.")
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".snakesh-rdp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write("".join(lines))
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
