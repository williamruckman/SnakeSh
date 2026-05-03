from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import platform
import plistlib
import re
import shlex
import shutil
import subprocess

from snakesh import runtime
from snakesh import __version__
from snakesh.core.tool_icons import tool_icon_path
from snakesh.core.tool_registry import TOOL_REGISTRY, TOOL_REGISTRY_BY_KEY, normalize_tool_keys
from snakesh.services.linux_desktop_install_service import (
    LinuxDesktopIntegrationError,
    _desktop_exec_escape,
    install_desktop_integration,
    integration_paths,
    is_desktop_integration_installed,
)


_WINDOWS_ILLEGAL_FILENAME = re.compile(r"[<>:\"/\\\\|?*]+")
_TOOL_DESKTOP_PREFIX = "snakesh-tool-"
_TOOL_DESKTOP_VERSION_KEY = "X-SnakeSh-Tool-Version"
_TOOL_KEY_KEY = "X-SnakeSh-Tool-Key"
_WINDOWS_SHORTCUTS_DIR = Path("Microsoft") / "Windows" / "Start Menu" / "Programs" / "SnakeSh Tools"
_WINDOWS_PINNED_TASKBAR_DIR = Path("Microsoft") / "Internet Explorer" / "Quick Launch" / "User Pinned" / "TaskBar"
_WINDOWS_PROPERTY_STORE_IID = "886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"
_WINDOWS_APP_USER_MODEL_ID_FMTID = "9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"
_MACOS_APPLICATIONS_DIR = Path("Applications") / "SnakeSh Tools"
_MACOS_INFO_TOOL_KEY = "SnakeShToolKey"


class ToolLauncherError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ToolLauncherSyncResult:
    selected_keys: tuple[str, ...]
    installed_keys: tuple[str, ...]
    removed_keys: tuple[str, ...]


def supported_launcher_platform(platform_name: str | None = None) -> bool:
    return _platform_name(platform_name) in {"linux", "windows", "macos"}


def installed_tool_launcher_keys(
    *,
    platform_name: str | None = None,
    home: Path | None = None,
    appdata: Path | None = None,
) -> list[str]:
    system = _platform_name(platform_name)
    if system == "linux":
        applications_dir = integration_paths(home=home).desktop_file.parent
        installed: list[str] = []
        for candidate in sorted(applications_dir.glob(f"{_TOOL_DESKTOP_PREFIX}*.desktop")):
            tool_key = _linux_desktop_tool_key(candidate)
            if tool_key:
                installed.append(tool_key)
        return normalize_tool_keys(installed)
    if system == "windows":
        shortcuts_dir = _windows_shortcuts_dir(appdata=appdata, home=home)
        installed = []
        for entry in TOOL_REGISTRY:
            if _windows_shortcut_path(entry.key, appdata=appdata, home=home).exists():
                installed.append(entry.key)
        if not shortcuts_dir.exists():
            return []
        return normalize_tool_keys(installed)
    if system == "macos":
        applications_dir = _macos_tool_applications_dir(home=home)
        installed: list[str] = []
        if not applications_dir.exists():
            return []
        for candidate in sorted(applications_dir.glob("*.app")):
            tool_key = _macos_bundle_tool_key(candidate)
            if tool_key:
                installed.append(tool_key)
        return normalize_tool_keys(installed)
    return []


def sync_tool_launchers(
    selected_tool_keys: list[str],
    *,
    platform_name: str | None = None,
    home: Path | None = None,
    appdata: Path | None = None,
) -> ToolLauncherSyncResult:
    system = _platform_name(platform_name)
    if not supported_launcher_platform(system):
        raise ToolLauncherError("Tool launchers are not supported on this platform.")

    selected = tuple(normalize_tool_keys(selected_tool_keys))
    installed_before = set(installed_tool_launcher_keys(platform_name=system, home=home, appdata=appdata))
    requested = set(selected)

    if system == "linux":
        _sync_linux_tool_launchers(requested, home=home)
    elif system == "windows":
        _sync_windows_tool_launchers(requested, home=home, appdata=appdata)
    else:
        _sync_macos_tool_launchers(requested, home=home)

    installed_after = set(installed_tool_launcher_keys(platform_name=system, home=home, appdata=appdata))
    installed_keys = tuple(key for key in selected if key in installed_after and key not in installed_before)
    removed_keys = tuple(
        entry.key for entry in TOOL_REGISTRY if entry.key in installed_before and entry.key not in installed_after
    )
    return ToolLauncherSyncResult(
        selected_keys=selected,
        installed_keys=installed_keys,
        removed_keys=removed_keys,
    )


def remove_tool_launchers(
    *,
    platform_name: str | None = None,
    home: Path | None = None,
    appdata: Path | None = None,
) -> ToolLauncherSyncResult:
    system = _platform_name(platform_name)
    if not supported_launcher_platform(system):
        raise ToolLauncherError("Tool launchers are not supported on this platform.")

    installed_before = set(installed_tool_launcher_keys(platform_name=system, home=home, appdata=appdata))
    if system == "linux":
        _remove_linux_tool_launchers(home=home)
    elif system == "windows":
        _remove_windows_tool_launchers(home=home, appdata=appdata)
    else:
        _remove_macos_tool_launchers(home=home)

    installed_after = set(installed_tool_launcher_keys(platform_name=system, home=home, appdata=appdata))
    removed_keys = tuple(
        entry.key for entry in TOOL_REGISTRY if entry.key in installed_before and entry.key not in installed_after
    )
    return ToolLauncherSyncResult(
        selected_keys=(),
        installed_keys=(),
        removed_keys=removed_keys,
    )


def launcher_sync_summary(result: ToolLauncherSyncResult) -> str:
    if not result.installed_keys and not result.removed_keys:
        if result.selected_keys:
            return "Tool launcher entries are already up to date."
        return "No tool launcher entries are installed."
    segments: list[str] = []
    if result.installed_keys:
        labels = ", ".join(TOOL_REGISTRY_BY_KEY[key].label for key in result.installed_keys)
        segments.append(f"Installed: {labels}")
    if result.removed_keys:
        labels = ", ".join(TOOL_REGISTRY_BY_KEY[key].label for key in result.removed_keys)
        segments.append(f"Removed: {labels}")
    return "\n".join(segments)


def _platform_name(platform_name: str | None = None) -> str:
    raw = (
        platform_name
        or os.environ.get("SNAKESH_TOOL_LAUNCHER_PLATFORM")
        or platform.system()
    ).strip().lower()
    if raw in {"darwin", "mac", "macos", "osx"}:
        return "macos"
    if raw in {"nt", "windows", "win32"}:
        return "windows"
    if raw == "posix":
        return platform_name.lower().strip() if platform_name else "linux"  # pragma: no cover - defensive
    return raw or "linux"


def _sanitize_filename(value: str) -> str:
    cleaned = _WINDOWS_ILLEGAL_FILENAME.sub(" - ", value.strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned or "SnakeSh Tool"


def _tool_launcher_display_name(tool_key: str) -> str:
    entry = TOOL_REGISTRY_BY_KEY[tool_key]
    return f"SnakeSh - {entry.label}"


def _linux_desktop_tool_key(path: Path) -> str | None:
    try:
        payload = path.read_text(encoding="utf-8")
    except OSError:
        return None
    prefix = f"{_TOOL_KEY_KEY}="
    for line in payload.splitlines():
        if line.startswith(prefix):
            key = line[len(prefix):].strip()
            if key in TOOL_REGISTRY_BY_KEY:
                return key
    return None


def _linux_tool_desktop_path(tool_key: str, *, home: Path | None = None) -> Path:
    return integration_paths(home=home).desktop_file.parent / f"{_TOOL_DESKTOP_PREFIX}{tool_key}.desktop"


def _linux_launcher_target(home: Path | None = None) -> Path:
    paths = integration_paths(home=home)
    if is_desktop_integration_installed(home=home):
        return paths.installed_appimage
    try:
        install_desktop_integration(home=home)
    except LinuxDesktopIntegrationError as exc:
        raise ToolLauncherError(str(exc)) from exc
    return paths.installed_appimage


def _tool_icon_asset_path(tool_key: str, icon_format: str) -> Path:
    icon_path = tool_icon_path(tool_key, icon_format)
    if not icon_path.exists():
        raise ToolLauncherError(f"Missing {icon_format.upper()} launcher icon asset: {icon_path}")
    return icon_path


def _copy_tool_icon_asset(tool_key: str, icon_format: str, destination: Path) -> Path:
    source = _tool_icon_asset_path(tool_key, icon_format)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return destination


def _linux_tool_icon_path(tool_key: str, *, home: Path | None = None) -> Path:
    return integration_paths(home=home).install_dir / f"{_TOOL_DESKTOP_PREFIX}{tool_key}.png"


def _linux_tool_desktop_payload(tool_key: str, *, executable: Path, icon_path: Path) -> str:
    display_name = _tool_launcher_display_name(tool_key)
    escaped_exec = _desktop_exec_escape(str(executable))
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        f"Name={display_name}",
        f"Comment=Open the {TOOL_REGISTRY_BY_KEY[tool_key].label} tool",
        f'Exec="{escaped_exec}" tool {tool_key}',
        f"Icon={icon_path}",
        "Terminal=false",
        "Categories=Network;Utility;",
        "StartupNotify=true",
        f"StartupWMClass={_TOOL_DESKTOP_PREFIX}{tool_key}",
        f"{_TOOL_KEY_KEY}={tool_key}",
        f"{_TOOL_DESKTOP_VERSION_KEY}={__version__}",
        "",
    ]
    return "\n".join(lines)


def _refresh_linux_desktop_index(home: Path | None = None) -> None:
    applications_dir = integration_paths(home=home).desktop_file.parent
    _run_optional(["update-desktop-database", str(applications_dir)])


def _sync_linux_tool_launchers(selected: set[str], *, home: Path | None = None) -> None:
    executable = _linux_launcher_target(home=home)
    applications_dir = integration_paths(home=home).desktop_file.parent
    applications_dir.mkdir(parents=True, exist_ok=True)

    for entry in TOOL_REGISTRY:
        desktop_path = _linux_tool_desktop_path(entry.key, home=home)
        if entry.key in selected:
            icon_path = _copy_tool_icon_asset(
                entry.key,
                "png",
                _linux_tool_icon_path(entry.key, home=home),
            )
            desktop_path.write_text(
                _linux_tool_desktop_payload(entry.key, executable=executable, icon_path=icon_path),
                encoding="utf-8",
            )
        else:
            desktop_path.unlink(missing_ok=True)
            _linux_tool_icon_path(entry.key, home=home).unlink(missing_ok=True)
    _refresh_linux_desktop_index(home=home)


def _remove_linux_tool_launchers(*, home: Path | None = None) -> None:
    for entry in TOOL_REGISTRY:
        _linux_tool_desktop_path(entry.key, home=home).unlink(missing_ok=True)
        _linux_tool_icon_path(entry.key, home=home).unlink(missing_ok=True)
    _refresh_linux_desktop_index(home=home)


def _windows_shortcuts_dir(*, appdata: Path | None = None, home: Path | None = None) -> Path:
    if appdata is not None:
        root = appdata.expanduser()
    else:
        default_home = (home or Path.home()).expanduser()
        root = Path(os.getenv("APPDATA", default_home / "AppData" / "Roaming")).expanduser()
    return root / _WINDOWS_SHORTCUTS_DIR


def _windows_shortcut_path(tool_key: str, *, appdata: Path | None = None, home: Path | None = None) -> Path:
    display_name = _sanitize_filename(_tool_launcher_display_name(tool_key))
    return _windows_shortcuts_dir(appdata=appdata, home=home) / f"{display_name}.lnk"


def _windows_pinned_taskbar_dir(*, appdata: Path | None = None, home: Path | None = None) -> Path:
    if appdata is not None:
        root = appdata.expanduser()
    else:
        default_home = (home or Path.home()).expanduser()
        root = Path(os.getenv("APPDATA", default_home / "AppData" / "Roaming")).expanduser()
    return root / _WINDOWS_PINNED_TASKBAR_DIR


def _windows_pinned_shortcut_path(tool_key: str, *, appdata: Path | None = None, home: Path | None = None) -> Path:
    display_name = _sanitize_filename(_tool_launcher_display_name(tool_key))
    return _windows_pinned_taskbar_dir(appdata=appdata, home=home) / f"{display_name}.lnk"


def _windows_tool_app_user_model_id(tool_key: str) -> str:
    return f"com.snakesh.tool.{tool_key}"


def _windows_command(tool_key: str) -> tuple[str, str]:
    command = runtime.self_launch_command(["tool", tool_key])
    target = str(command[0])
    arguments = subprocess.list2cmdline([str(part) for part in command[1:]]) if len(command) > 1 else ""
    return target, arguments


def _run_powershell(script: str) -> None:
    command = [
        "powershell",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        script,
    ]
    run_kwargs: dict[str, object] = {
        "check": False,
        "capture_output": True,
        "text": True,
    }
    if os.name == "nt":
        creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if creationflags:
            run_kwargs["creationflags"] = creationflags
        startupinfo_cls = getattr(subprocess, "STARTUPINFO", None)
        if startupinfo_cls is not None:
            startupinfo = startupinfo_cls()
            startupinfo.dwFlags |= int(getattr(subprocess, "STARTF_USESHOWWINDOW", 0))
            startupinfo.wShowWindow = int(getattr(subprocess, "SW_HIDE", 0))
            run_kwargs["startupinfo"] = startupinfo
    result = subprocess.run(command, **run_kwargs)  # noqa: S603
    if result.returncode != 0:
        raise ToolLauncherError((result.stderr or result.stdout or "Failed to create Windows shortcut.").strip())


def _powershell_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _windows_icon_location(tool_key: str, target: str) -> str:
    icon_path = tool_icon_path(tool_key, "ico")
    if icon_path.exists():
        return str(icon_path)
    return target


def _create_windows_shortcut(
    shortcut_path: Path,
    *,
    target: str,
    arguments: str,
    description: str,
    icon_location: str,
    app_user_model_id: str = "",
) -> None:
    shortcut_path.parent.mkdir(parents=True, exist_ok=True)
    working_directory = str(Path(target).expanduser().parent)
    script = "\n".join(
        (
            "$Shell = New-Object -ComObject WScript.Shell",
            f"$Shortcut = $Shell.CreateShortcut({_powershell_quote(str(shortcut_path))})",
            f"$Shortcut.TargetPath = {_powershell_quote(target)}",
            f"$Shortcut.Arguments = {_powershell_quote(arguments)}",
            f"$Shortcut.WorkingDirectory = {_powershell_quote(working_directory)}",
            f"$Shortcut.Description = {_powershell_quote(description)}",
            f"$Shortcut.IconLocation = {_powershell_quote(icon_location)}",
            "$Shortcut.Save()",
        )
    )
    _run_powershell(script)
    if app_user_model_id:
        _set_windows_shortcut_app_user_model_id(shortcut_path, app_user_model_id)


def _set_windows_shortcut_app_user_model_id(shortcut_path: Path, app_user_model_id: str) -> None:
    if not shortcut_path.exists() or not app_user_model_id:
        return
    script = "\n".join(
        (
            "$Definition = @'",
            "using System;",
            "using System.Runtime.InteropServices;",
            "public static class SnakeShShortcutAppId {",
            "  [StructLayout(LayoutKind.Sequential, Pack=4)]",
            "  public struct PROPERTYKEY {",
            "    public Guid fmtid;",
            "    public uint pid;",
            "    public PROPERTYKEY(Guid fmtid, uint pid) { this.fmtid = fmtid; this.pid = pid; }",
            "  }",
            "  [StructLayout(LayoutKind.Sequential)]",
            "  public struct PROPVARIANT {",
            "    public ushort vt;",
            "    public ushort wReserved1;",
            "    public ushort wReserved2;",
            "    public ushort wReserved3;",
            "    public IntPtr p;",
            "    public int p2;",
            "    public static PROPVARIANT FromString(string value) {",
            "      PROPVARIANT pv = new PROPVARIANT();",
            "      pv.vt = 31;",
            "      pv.p = Marshal.StringToCoTaskMemUni(value);",
            "      return pv;",
            "    }",
            "  }",
            f"  [ComImport, Guid(\"{_WINDOWS_PROPERTY_STORE_IID}\"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]",
            "  interface IPropertyStore {",
            "    void GetCount(out uint cProps);",
            "    void GetAt(uint iProp, out PROPERTYKEY pkey);",
            "    void GetValue(ref PROPERTYKEY key, out PROPVARIANT pv);",
            "    void SetValue(ref PROPERTYKEY key, ref PROPVARIANT pv);",
            "    void Commit();",
            "  }",
            "  [DllImport(\"shell32.dll\", CharSet=CharSet.Unicode, SetLastError=true)]",
            "  static extern int SHGetPropertyStoreFromParsingName(string pszPath, IntPtr pbc, uint flags, ref Guid riid, out IPropertyStore ppv);",
            "  [DllImport(\"Ole32.dll\")]",
            "  static extern int PropVariantClear(ref PROPVARIANT pvar);",
            "  public static void SetAppUserModelId(string path, string appId) {",
            f"    Guid iid = new Guid(\"{_WINDOWS_PROPERTY_STORE_IID}\");",
            "    IPropertyStore store;",
            "    int hr = SHGetPropertyStoreFromParsingName(path, IntPtr.Zero, 2, ref iid, out store);",
            "    Marshal.ThrowExceptionForHR(hr);",
            f"    PROPERTYKEY key = new PROPERTYKEY(new Guid(\"{_WINDOWS_APP_USER_MODEL_ID_FMTID}\"), 5);",
            "    PROPVARIANT pv = PROPVARIANT.FromString(appId);",
            "    try { store.SetValue(ref key, ref pv); store.Commit(); } finally { PropVariantClear(ref pv); }",
            "  }",
            "}",
            "'@",
            "Add-Type -TypeDefinition $Definition",
            (
                "[SnakeShShortcutAppId]::SetAppUserModelId("
                f"{_powershell_quote(str(shortcut_path))}, {_powershell_quote(app_user_model_id)})"
            ),
        )
    )
    _run_powershell(script)


def _repair_windows_pinned_tool_shortcut(
    tool_key: str,
    *,
    home: Path | None = None,
    appdata: Path | None = None,
) -> None:
    pinned_shortcut = _windows_pinned_shortcut_path(tool_key, appdata=appdata, home=home)
    if pinned_shortcut.exists():
        _set_windows_shortcut_app_user_model_id(pinned_shortcut, _windows_tool_app_user_model_id(tool_key))


def _sync_windows_tool_launchers(
    selected: set[str],
    *,
    home: Path | None = None,
    appdata: Path | None = None,
) -> None:
    shortcuts_dir = _windows_shortcuts_dir(appdata=appdata, home=home)
    shortcuts_dir.mkdir(parents=True, exist_ok=True)
    for entry in TOOL_REGISTRY:
        shortcut_path = _windows_shortcut_path(entry.key, appdata=appdata, home=home)
        if entry.key in selected:
            target, arguments = _windows_command(entry.key)
            _create_windows_shortcut(
                shortcut_path,
                target=target,
                arguments=arguments,
                description=f"Open the {entry.label} tool",
                icon_location=_windows_icon_location(entry.key, target),
                app_user_model_id=_windows_tool_app_user_model_id(entry.key),
            )
            _repair_windows_pinned_tool_shortcut(entry.key, home=home, appdata=appdata)
        else:
            shortcut_path.unlink(missing_ok=True)
    _remove_empty_tree(shortcuts_dir)


def _remove_windows_tool_launchers(
    *,
    home: Path | None = None,
    appdata: Path | None = None,
) -> None:
    shortcuts_dir = _windows_shortcuts_dir(appdata=appdata, home=home)
    for entry in TOOL_REGISTRY:
        _windows_shortcut_path(entry.key, appdata=appdata, home=home).unlink(missing_ok=True)
    _remove_empty_tree(shortcuts_dir)


def _macos_tool_applications_dir(*, home: Path | None = None) -> Path:
    return (home or Path.home()).expanduser() / _MACOS_APPLICATIONS_DIR


def _macos_bundle_path(tool_key: str, *, home: Path | None = None) -> Path:
    display_name = _sanitize_filename(_tool_launcher_display_name(tool_key))
    return _macos_tool_applications_dir(home=home) / f"{display_name}.app"


def _macos_bundle_tool_key(bundle_path: Path) -> str | None:
    info_path = bundle_path / "Contents" / "Info.plist"
    try:
        with info_path.open("rb") as handle:
            payload = plistlib.load(handle)
    except Exception:
        return None
    tool_key = str(payload.get(_MACOS_INFO_TOOL_KEY, "")).strip()
    if tool_key in TOOL_REGISTRY_BY_KEY:
        return tool_key
    return None


def _macos_icon_asset_path(tool_key: str) -> Path:
    return _tool_icon_asset_path(tool_key, "icns")


def _create_macos_bundle(bundle_path: Path, *, tool_key: str) -> None:
    command = runtime.self_launch_command(["tool", tool_key])
    contents_dir = bundle_path / "Contents"
    macos_dir = contents_dir / "MacOS"
    resources_dir = contents_dir / "Resources"
    shutil.rmtree(bundle_path, ignore_errors=True)
    macos_dir.mkdir(parents=True, exist_ok=True)
    resources_dir.mkdir(parents=True, exist_ok=True)

    executable_name = _sanitize_filename(_tool_launcher_display_name(tool_key))
    launcher_script_path = macos_dir / executable_name
    launcher_script_path.write_text(
        "\n".join(
            (
                "#!/bin/sh",
                "set -eu",
                f"exec {shlex_join(command)} \"$@\"",
                "",
            )
        ),
        encoding="utf-8",
    )
    launcher_script_path.chmod(0o755)

    icon_asset_path = _macos_icon_asset_path(tool_key)
    icon_name = icon_asset_path.name
    shutil.copy2(icon_asset_path, resources_dir / icon_name)
    info_payload = {
        "CFBundleName": _tool_launcher_display_name(tool_key),
        "CFBundleDisplayName": _tool_launcher_display_name(tool_key),
        "CFBundleExecutable": executable_name,
        "CFBundleIdentifier": f"com.snakesh.tool.{tool_key}.launcher",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": __version__,
        "CFBundleVersion": __version__,
        "CFBundleIconFile": icon_name,
        _MACOS_INFO_TOOL_KEY: tool_key,
    }
    with (contents_dir / "Info.plist").open("wb") as handle:
        plistlib.dump(info_payload, handle)


def _sync_macos_tool_launchers(selected: set[str], *, home: Path | None = None) -> None:
    applications_dir = _macos_tool_applications_dir(home=home)
    applications_dir.mkdir(parents=True, exist_ok=True)
    for entry in TOOL_REGISTRY:
        bundle_path = _macos_bundle_path(entry.key, home=home)
        if entry.key in selected:
            _create_macos_bundle(bundle_path, tool_key=entry.key)
        else:
            shutil.rmtree(bundle_path, ignore_errors=True)
    _remove_empty_tree(applications_dir)


def _remove_macos_tool_launchers(*, home: Path | None = None) -> None:
    applications_dir = _macos_tool_applications_dir(home=home)
    for entry in TOOL_REGISTRY:
        shutil.rmtree(_macos_bundle_path(entry.key, home=home), ignore_errors=True)
    _remove_empty_tree(applications_dir)


def _run_optional(command: list[str]) -> None:
    if not command:
        return
    executable = shutil.which(command[0])
    if executable is None:
        return
    subprocess.run([executable, *command[1:]], check=False, capture_output=True, text=True)  # noqa: S603


def _remove_empty_tree(path: Path) -> None:
    if not path.exists() or not path.is_dir():
        return
    try:
        children = list(path.iterdir())
    except OSError:
        return
    for child in children:
        if child.is_dir():
            _remove_empty_tree(child)
    try:
        path.rmdir()
    except OSError:
        return


def shlex_join(command: list[str]) -> str:
    return shlex.join(command)
