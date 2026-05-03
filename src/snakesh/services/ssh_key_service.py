from __future__ import annotations

import os
from pathlib import Path

from snakesh.core.models import Session

_DEFAULT_PUBLIC_KEY_FILES: tuple[str, ...] = (
    "id_ed25519.pub",
    "id_ecdsa.pub",
    "id_rsa.pub",
    "id_dsa.pub",
)
_VALID_KEY_PREFIXES: tuple[str, ...] = ("ssh-", "ecdsa-", "sk-")


def candidate_public_key_paths(session: Session, *, home_dir: Path | None = None) -> list[Path]:
    candidates: list[Path] = []
    seen: set[str] = set()

    def add_candidate(path: Path | None) -> None:
        if path is None:
            return
        expanded = path.expanduser()
        key = str(expanded).lower() if os.name == "nt" else str(expanded)
        if key in seen:
            return
        seen.add(key)
        candidates.append(expanded)

    explicit_public = _expanded_path(session.public_key_path)
    add_candidate(explicit_public)

    private_path = _expanded_path(session.private_key_path)
    if private_path is not None:
        if private_path.suffix == ".pub":
            add_candidate(private_path)
        else:
            add_candidate(Path(f"{private_path}.pub"))

    home_root = (home_dir or Path.home()).expanduser()
    ssh_dir = home_root / ".ssh"
    for file_name in _DEFAULT_PUBLIC_KEY_FILES:
        add_candidate(ssh_dir / file_name)

    if ssh_dir.exists() and ssh_dir.is_dir():
        for path in sorted(ssh_dir.glob("*.pub"), key=lambda item: item.name.lower()):
            if path.is_file():
                add_candidate(path)

    return candidates


def resolve_existing_public_key(session: Session, *, home_dir: Path | None = None) -> Path | None:
    for path in candidate_public_key_paths(session, home_dir=home_dir):
        if path.exists() and path.is_file():
            return path
    return None


def validate_public_key_file(path: Path) -> tuple[bool, str]:
    expanded = path.expanduser()
    if not expanded.exists() or not expanded.is_file():
        return False, f"Public key file not found: {expanded}"
    try:
        key_line = expanded.read_text(encoding="utf-8").strip()
    except Exception as exc:
        return False, f"Unable to read public key file: {exc}"
    if not is_openssh_public_key_line(key_line):
        return False, "Public key must be in OpenSSH public format."
    return True, ""


def is_openssh_public_key_line(key_line: str) -> bool:
    parts = key_line.strip().split()
    if len(parts) < 2:
        return False
    key_type = parts[0].strip()
    if not key_type:
        return False
    return key_type.startswith(_VALID_KEY_PREFIXES)


def _expanded_path(value: str) -> Path | None:
    cleaned = value.strip()
    if not cleaned:
        return None
    return Path(cleaned).expanduser()
