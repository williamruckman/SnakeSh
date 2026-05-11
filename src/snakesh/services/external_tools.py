from __future__ import annotations

from collections.abc import Sequence
import os
from pathlib import Path
import platform
import shutil


MACOS_EXECUTABLE_DIRS: tuple[Path, ...] = (
    Path("/opt/homebrew/bin"),
    Path("/opt/homebrew/sbin"),
    Path("/usr/local/bin"),
    Path("/usr/local/sbin"),
)


def resolve_executable(candidates: Sequence[str], *, platform_name: str | None = None) -> str | None:
    system = _platform_name(platform_name)
    for candidate in candidates:
        resolved = _resolve_candidate(candidate, platform_name=system)
        if resolved:
            return resolved
    return None


def _resolve_candidate(candidate: str, *, platform_name: str) -> str | None:
    raw = str(candidate or "").strip()
    if not raw:
        return None

    expanded = Path(os.path.expandvars(os.path.expanduser(raw)))
    if expanded.is_absolute() or _looks_path_like(raw):
        if expanded.exists():
            return str(expanded)
        return None

    resolved = shutil.which(raw)
    if resolved:
        return resolved

    if platform_name in {"darwin", "macos"}:
        for directory in MACOS_EXECUTABLE_DIRS:
            path = directory / raw
            if path.exists():
                return str(path)
    return None


def _looks_path_like(value: str) -> bool:
    return "/" in value or "\\" in value or value.startswith(".")


def _platform_name(platform_name: str | None = None) -> str:
    raw = (platform_name or platform.system()).strip().lower()
    if raw in {"mac", "macos", "osx"}:
        return "darwin"
    return raw
