from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess

from snakesh import __version__
from snakesh import runtime


DESKTOP_FILE_NAME = "snakesh.desktop"
INSTALLED_APPIMAGE_NAME = "SnakeSh.AppImage"
ICON_FILE_NAME = "snakesh.png"
DESKTOP_VERSION_KEY = "X-SnakeSh-Version"
EXPORT_MIME_TYPE = "application/x-snakesh-export"
MIME_DEFINITION_FILE_NAME = "snakesh.xml"
TOOL_DESKTOP_FILE_GLOB = "snakesh-tool-*.desktop"
TOOL_ICON_FILE_GLOB = "snakesh-tool-*.png"
_VERSION_TOKEN_RE = re.compile(r"[0-9]+|[A-Za-z]+")


class LinuxDesktopIntegrationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class LinuxDesktopPaths:
    install_dir: Path
    installed_appimage: Path
    desktop_file: Path
    launcher_icon_file: Path
    icon_file: Path
    mime_packages_dir: Path
    mime_definition_file: Path


def integration_paths(home: Path | None = None) -> LinuxDesktopPaths:
    root = (home or Path.home()).expanduser()
    install_dir = root / ".local" / "lib" / "SnakeSh"
    desktop_file = root / ".local" / "share" / "applications" / DESKTOP_FILE_NAME
    launcher_icon_file = install_dir / ICON_FILE_NAME
    icon_file = root / ".local" / "share" / "icons" / "hicolor" / "256x256" / "apps" / ICON_FILE_NAME
    mime_packages_dir = root / ".local" / "share" / "mime" / "packages"
    return LinuxDesktopPaths(
        install_dir=install_dir,
        installed_appimage=install_dir / INSTALLED_APPIMAGE_NAME,
        desktop_file=desktop_file,
        launcher_icon_file=launcher_icon_file,
        icon_file=icon_file,
        mime_packages_dir=mime_packages_dir,
        mime_definition_file=mime_packages_dir / MIME_DEFINITION_FILE_NAME,
    )


def is_desktop_integration_installed(home: Path | None = None) -> bool:
    paths = integration_paths(home=home)
    if (
        not paths.installed_appimage.exists()
        or not paths.desktop_file.exists()
        or not paths.launcher_icon_file.exists()
    ):
        return False
    if not os.access(paths.installed_appimage, os.X_OK):
        return False
    try:
        payload = paths.desktop_file.read_text(encoding="utf-8")
    except OSError:
        return False
    core_tokens = (
        str(paths.installed_appimage),
        str(paths.launcher_icon_file),
    )
    if not all(token in payload for token in core_tokens):
        return False

    # Preserve compatibility with integrations installed before MIME support was added.
    has_mime_registration = (
        f"MimeType={EXPORT_MIME_TYPE};" in payload and paths.mime_definition_file.exists()
    )
    return has_mime_registration or installed_desktop_integration_version(home=home) is not None


def installed_desktop_integration_version(home: Path | None = None) -> str | None:
    paths = integration_paths(home=home)
    try:
        payload = paths.desktop_file.read_text(encoding="utf-8")
    except OSError:
        return None
    prefix = f"{DESKTOP_VERSION_KEY}="
    for line in payload.splitlines():
        if line.startswith(prefix):
            value = line[len(prefix) :].strip()
            if value:
                return value
    return None


def desktop_integration_needs_update(*, current_version: str, home: Path | None = None) -> bool:
    if not is_desktop_integration_installed(home=home):
        return False
    installed_version = installed_desktop_integration_version(home=home)
    if not installed_version:
        return True
    return _version_key(current_version) > _version_key(installed_version)


def install_desktop_integration(
    *,
    appimage_path: Path | None = None,
    home: Path | None = None,
) -> Path:
    _require_linux()
    source = appimage_path or runtime.appimage_path()
    if source is None:
        raise LinuxDesktopIntegrationError(
            "Desktop integration requires launching from an AppImage (APPIMAGE is not set)."
        )

    source_path = source.expanduser()
    if not source_path.exists() or not source_path.is_file():
        raise LinuxDesktopIntegrationError(f"AppImage not found: {source_path}")

    paths = integration_paths(home=home)
    paths.install_dir.mkdir(parents=True, exist_ok=True)

    temp_target = paths.install_dir / f".{INSTALLED_APPIMAGE_NAME}.tmp-{os.getpid()}"
    shutil.copy2(source_path, temp_target)
    current_mode = temp_target.stat().st_mode
    temp_target.chmod(current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    temp_target.replace(paths.installed_appimage)

    paths.desktop_file.parent.mkdir(parents=True, exist_ok=True)

    icon_source = _icon_source_path()
    if not icon_source.exists():
        raise LinuxDesktopIntegrationError(f"SnakeSh icon asset is missing: {icon_source}")
    paths.launcher_icon_file.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(icon_source, paths.launcher_icon_file)
    paths.icon_file.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(icon_source, paths.icon_file)

    paths.desktop_file.write_text(
        _desktop_file_payload(paths.installed_appimage, paths.launcher_icon_file),
        encoding="utf-8",
    )
    _install_mime_definition(paths)

    _refresh_desktop_index(paths)
    _set_default_file_handlers()
    return paths.installed_appimage


def uninstall_desktop_integration(*, home: Path | None = None) -> bool:
    _require_linux()
    paths = integration_paths(home=home)
    removed_any = False
    removed_any = _remove_installed_tool_launchers(paths) or removed_any

    for target in (
        paths.desktop_file,
        paths.icon_file,
        paths.launcher_icon_file,
        paths.installed_appimage,
        paths.mime_definition_file,
    ):
        try:
            target.unlink()
            removed_any = True
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise LinuxDesktopIntegrationError(f"Failed to remove {target}: {exc}") from exc

    _remove_dir_if_empty(paths.install_dir)
    _remove_dir_if_empty(paths.mime_definition_file.parent)
    _remove_dir_if_empty(paths.mime_definition_file.parent.parent)
    _remove_dir_if_empty(paths.icon_file.parent)
    _remove_dir_if_empty(paths.icon_file.parent.parent)
    _remove_dir_if_empty(paths.icon_file.parent.parent.parent)

    _refresh_desktop_index(paths)
    return removed_any


def _remove_installed_tool_launchers(paths: LinuxDesktopPaths) -> bool:
    removed_any = False
    for directory, pattern in (
        (paths.desktop_file.parent, TOOL_DESKTOP_FILE_GLOB),
        (paths.install_dir, TOOL_ICON_FILE_GLOB),
    ):
        for target in directory.glob(pattern):
            if not target.is_file() and not target.is_symlink():
                continue
            try:
                target.unlink()
                removed_any = True
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise LinuxDesktopIntegrationError(f"Failed to remove {target}: {exc}") from exc
    return removed_any


def _desktop_file_payload(installed_appimage: Path, icon_path: Path) -> str:
    escaped_exec = _desktop_exec_escape(str(installed_appimage))
    escaped_icon = str(icon_path)
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        "Name=SnakeSh",
        "Comment=Remote terminal client",
        f'Exec="{escaped_exec}" %U',
        f"Icon={escaped_icon}",
        f"MimeType={EXPORT_MIME_TYPE};",
        "Terminal=false",
        "Categories=Network;Utility;",
        "StartupNotify=true",
        "StartupWMClass=SnakeSh",
        f"{DESKTOP_VERSION_KEY}={__version__}",
        "",
    ]
    return "\n".join(lines)


def _desktop_exec_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _icon_source_path() -> Path:
    png = runtime.asset_path("snakesh-icon.png")
    if png.exists():
        return png
    raise LinuxDesktopIntegrationError(f"SnakeSh PNG icon asset is missing: {png}")


def _install_mime_definition(paths: LinuxDesktopPaths) -> None:
    paths.mime_definition_file.parent.mkdir(parents=True, exist_ok=True)
    paths.mime_definition_file.write_text(_mime_definition_payload(), encoding="utf-8")


def _mime_definition_payload() -> str:
    return "\n".join(
        (
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?>",
            "<mime-info xmlns=\"http://www.freedesktop.org/standards/shared-mime-info\">",
            f"  <mime-type type=\"{EXPORT_MIME_TYPE}\">",
            "    <comment>SnakeSh export bundle</comment>",
            "    <glob pattern=\"*.ssx\"/>",
            "  </mime-type>",
            "</mime-info>",
            "",
        )
    )


def _refresh_desktop_index(paths: LinuxDesktopPaths) -> None:
    desktop_dir = paths.desktop_file.parent
    icon_root = paths.icon_file.parent.parent.parent
    mime_root = paths.mime_definition_file.parent.parent
    _run_optional(["update-mime-database", str(mime_root)])
    _run_optional(["update-desktop-database", str(desktop_dir)])
    _run_optional(["gtk-update-icon-cache", "-f", str(icon_root)])


def _set_default_file_handlers() -> None:
    _run_optional(["xdg-mime", "default", DESKTOP_FILE_NAME, EXPORT_MIME_TYPE])


def _run_optional(command: list[str]) -> None:
    if not command:
        return
    executable = shutil.which(command[0])
    if executable is None:
        return
    subprocess.run([executable, *command[1:]], check=False, capture_output=True, text=True)


def _remove_dir_if_empty(path: Path) -> None:
    try:
        path.rmdir()
    except Exception:
        return


def _require_linux() -> None:
    if platform.system().lower() != "linux":
        raise LinuxDesktopIntegrationError("Desktop integration is only available on Linux.")


def _version_key(value: str) -> tuple[tuple[int, int | str], ...]:
    tokens = _VERSION_TOKEN_RE.findall(value.strip())
    key: list[tuple[int, int | str]] = []
    for token in tokens:
        if token.isdigit():
            key.append((1, int(token)))
        else:
            key.append((0, token.lower()))
    return tuple(key)
