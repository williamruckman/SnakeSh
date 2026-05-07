from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path
import sys


def _candidate_version_paths() -> tuple[Path, ...]:
    paths: list[Path] = []
    seen: set[Path] = set()

    def add(path: Path) -> None:
        candidate = path.resolve(strict=False)
        if candidate in seen:
            return
        seen.add(candidate)
        paths.append(candidate)

    module_path = Path(__file__).resolve()
    add(module_path.parents[2] / "VERSION")

    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        add(Path(frozen_root) / "VERSION")
        add(Path(frozen_root) / "_internal" / "VERSION")

    executable_dir = Path(sys.executable).resolve().parent
    add(executable_dir / "VERSION")
    add(executable_dir / "_internal" / "VERSION")

    return tuple(paths)


def read_version() -> str:
    for path in _candidate_version_paths():
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if value:
            return value

    try:
        return package_version("snakesh")
    except PackageNotFoundError:
        return "0.0.0"


__version__ = read_version()
