from __future__ import annotations

import os
from pathlib import Path
import sys
from typing import Mapping, Sequence


_BUNDLE_RUNTIME_PATH_ENV_KEYS = ("LD_LIBRARY_PATH", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH")
_DEFAULT_POSIX_EXEC_PATH = "/usr/local/bin:/usr/bin:/bin:/usr/local/sbin:/usr/sbin:/sbin"
_SELF_LAUNCH_RUNTIME_RESET_ENV_KEYS = (
    "APPDIR",
    "APPIMAGE",
    "_MEIPASS2",
    "_PYI_APPLICATION_HOME_DIR",
    "_PYI_ARCHIVE_FILE",
    "_PYI_PARENT_PROCESS_LEVEL",
    "_PYI_SPLASH_IPC",
    "_PYI_LINUX_PROCESS_NAME",
)


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def executable_path() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve()
    return Path(__file__).resolve()


def package_root() -> Path:
    if is_frozen():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            root = Path(meipass) / "snakesh"
            if root.exists():
                return root
        fallback = Path(sys.executable).resolve().parent / "snakesh"
        if fallback.exists():
            return fallback
    return Path(__file__).resolve().parent


def asset_path(filename: str) -> Path:
    return package_root() / "assets" / filename


def self_launch_command(arguments: Sequence[str] | None = None) -> list[str]:
    extra = [str(argument) for argument in (arguments or [])]
    if is_frozen():
        appimage = appimage_path()
        if appimage is not None:
            return [str(appimage), *extra]
        return [str(executable_path()), *extra]
    return [sys.executable, "-m", "snakesh", *extra]


def appimage_path() -> Path | None:
    raw = os.environ.get("APPIMAGE", "").strip()
    if not raw:
        return None
    candidate = Path(raw).expanduser()
    try:
        return candidate.resolve()
    except Exception:
        return candidate


def is_appimage() -> bool:
    if appimage_path() is not None:
        return True
    if not is_frozen():
        return False
    return executable_path().name.lower().endswith(".appimage")


def _normalize_runtime_path(candidate: str | Path | None) -> Path | None:
    if candidate is None:
        return None
    try:
        text = str(candidate).strip()
    except Exception:
        return None
    if not text:
        return None
    try:
        return Path(text).expanduser().resolve(strict=False)
    except Exception:
        try:
            return Path(text).expanduser()
        except Exception:
            return None


def _bundle_owned_runtime_roots(env: Mapping[str, str]) -> tuple[Path, ...]:
    roots: list[Path] = []
    seen: set[str] = set()

    def _add(candidate: str | Path | None) -> None:
        path = _normalize_runtime_path(candidate)
        if path is None:
            return
        key = str(path)
        if key in seen:
            return
        seen.add(key)
        roots.append(path)

    _add(env.get("APPDIR"))
    _add(getattr(sys, "_MEIPASS", None))
    if is_frozen():
        executable_dir = executable_path().parent
        _add(executable_dir)
        _add(executable_dir / "_internal")

    return tuple(roots)


def _path_is_within_bundle_root(candidate: str, roots: Sequence[Path]) -> bool:
    path = _normalize_runtime_path(candidate)
    if path is None:
        return False
    for root in roots:
        if path == root or path.is_relative_to(root):
            return True
    return False


def _sanitize_path_list(value: str, *, roots: Sequence[Path]) -> str:
    kept_segments: list[str] = []
    for segment in value.split(os.pathsep):
        if not segment:
            continue
        if _path_is_within_bundle_root(segment, roots):
            continue
        kept_segments.append(segment)
    return os.pathsep.join(kept_segments)


def _sanitize_bundle_runtime_path_variables(
    env: dict[str, str],
    *,
    roots: Sequence[Path],
) -> None:
    for key in _BUNDLE_RUNTIME_PATH_ENV_KEYS:
        value = env.get(key)
        if not value:
            continue
        cleaned = _sanitize_path_list(str(value), roots=roots)
        if cleaned:
            env[key] = cleaned
        else:
            env.pop(key, None)

    path_value = env.get("PATH")
    if path_value:
        cleaned = _sanitize_path_list(str(path_value), roots=roots)
        if cleaned:
            env["PATH"] = cleaned
        elif os.name == "posix":
            env["PATH"] = _DEFAULT_POSIX_EXEC_PATH


def sanitized_local_shell_environment(env: Mapping[str, str] | None = None) -> dict[str, str]:
    sanitized = dict(os.environ if env is None else env)
    roots = _bundle_owned_runtime_roots(sanitized)
    if not roots:
        return sanitized

    _sanitize_bundle_runtime_path_variables(sanitized, roots=roots)
    return sanitized


def sanitized_self_launch_environment(env: Mapping[str, str] | None = None) -> dict[str, str]:
    sanitized = dict(os.environ if env is None else env)
    roots = _bundle_owned_runtime_roots(sanitized)
    if roots:
        _sanitize_bundle_runtime_path_variables(sanitized, roots=roots)

    for key in _SELF_LAUNCH_RUNTIME_RESET_ENV_KEYS:
        sanitized.pop(key, None)
    if is_frozen():
        sanitized["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return sanitized
