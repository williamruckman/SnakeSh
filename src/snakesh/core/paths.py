from __future__ import annotations

import os
from pathlib import Path
import platform
import shutil
import threading


_DATA_DIR_LOCK = threading.Lock()
_DATA_DIR_CACHE_KEY: tuple[str, Path, Path] | None = None
_DATA_DIR_CACHE: Path | None = None


def _paths_for_system() -> tuple[Path, Path]:
    home = Path.home()
    if platform.system().lower() == "windows":
        root = Path(os.getenv("LOCALAPPDATA", home / "AppData" / "Local"))
        return root / "SnakeSh", root / "SecurePython"
    return home / ".local" / "share" / "snakesh", home / ".local" / "share" / "securepython"


def _paths_identical(a: Path, b: Path) -> bool:
    try:
        if a.stat().st_size != b.stat().st_size:
            return False
    except Exception:
        return False
    try:
        with a.open("rb") as left, b.open("rb") as right:
            while True:
                left_chunk = left.read(64 * 1024)
                right_chunk = right.read(64 * 1024)
                if left_chunk != right_chunk:
                    return False
                if not left_chunk:
                    return True
    except Exception:
        return False


def _move_path(source: Path, destination: Path) -> bool:
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        return False
    try:
        source.rename(destination)
        return True
    except Exception:
        try:
            shutil.move(str(source), str(destination))
            return True
        except Exception:
            return False


def _legacy_conflict_target(preferred_target: Path) -> Path:
    candidate = preferred_target.with_name(f"{preferred_target.name}.securepython-legacy")
    suffix = 1
    while candidate.exists():
        candidate = preferred_target.with_name(f"{preferred_target.name}.securepython-legacy-{suffix}")
        suffix += 1
    return candidate


def _merge_legacy_tree(legacy_dir: Path, preferred_dir: Path) -> None:
    try:
        entries = list(legacy_dir.iterdir())
    except Exception:
        return
    for source in entries:
        target = preferred_dir / source.name
        if not target.exists():
            _move_path(source, target)
            continue
        if source.is_dir() and target.is_dir():
            _merge_legacy_tree(source, target)
            continue
        if source.is_file() and target.is_file() and _paths_identical(source, target):
            try:
                source.unlink()
            except Exception:
                pass
            continue
        _move_path(source, _legacy_conflict_target(target))


def _remove_empty_tree(path: Path) -> None:
    if not path.exists() or not path.is_dir():
        return
    try:
        children = list(path.iterdir())
    except Exception:
        return
    for child in children:
        if child.is_dir():
            _remove_empty_tree(child)
    try:
        path.rmdir()
    except Exception:
        pass


def _migrate_legacy_dir(preferred: Path, legacy: Path) -> None:
    if preferred == legacy or not legacy.exists():
        return
    if legacy.is_dir() and not preferred.exists() and _move_path(legacy, preferred):
        return
    try:
        preferred.mkdir(parents=True, exist_ok=True)
    except Exception:
        return
    if legacy.is_dir():
        _merge_legacy_tree(legacy, preferred)
        _remove_empty_tree(legacy)
    elif legacy.is_file():
        _move_path(legacy, _legacy_conflict_target(preferred / legacy.name))


def data_dir() -> Path:
    global _DATA_DIR_CACHE
    global _DATA_DIR_CACHE_KEY

    override = os.environ.get("SNAKESH_DATA_DIR", "").strip()
    preferred, legacy = _paths_for_system()
    cache_key = (override, preferred, legacy)
    if _DATA_DIR_CACHE is not None and _DATA_DIR_CACHE_KEY == cache_key:
        return _DATA_DIR_CACHE

    with _DATA_DIR_LOCK:
        if _DATA_DIR_CACHE is not None and _DATA_DIR_CACHE_KEY == cache_key:
            return _DATA_DIR_CACHE
        if override:
            target = Path(override).expanduser()
            try:
                target = target.resolve()
            except Exception:
                pass
            try:
                target.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass
            _DATA_DIR_CACHE = target
            _DATA_DIR_CACHE_KEY = cache_key
            return target
        _migrate_legacy_dir(preferred, legacy)
        try:
            preferred.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        _DATA_DIR_CACHE = preferred
        _DATA_DIR_CACHE_KEY = cache_key
        return preferred
