from __future__ import annotations

from pathlib import Path
import threading
from typing import Any

from snakesh.core.models import Session
from snakesh.core.paths import data_dir

_KNOWN_HOSTS_LOCK = threading.RLock()
_KNOWN_HOSTS_PATH_CACHE: Path | None = None


def known_hosts_path() -> Path:
    global _KNOWN_HOSTS_PATH_CACHE

    path = data_dir() / "known_hosts"
    if _KNOWN_HOSTS_PATH_CACHE == path and path.exists():
        return path

    with _KNOWN_HOSTS_LOCK:
        path = data_dir() / "known_hosts"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)
        _KNOWN_HOSTS_PATH_CACHE = path
        return path


def _host_pattern(session: Session) -> str:
    if session.port == 22:
        return session.host
    return f"[{session.host}]:{session.port}"


def trust_host_key(session: Session, key: Any) -> None:
    host = _host_pattern(session)
    exported = key.export_public_key(format_name="openssh")
    if isinstance(exported, bytes):
        exported = exported.decode("ascii")
    line = f"{host} {exported.strip()}"

    with _KNOWN_HOSTS_LOCK:
        path = known_hosts_path()
        existing = set(path.read_text(encoding="utf-8").splitlines())
        if line not in existing:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(f"{line}\n")
