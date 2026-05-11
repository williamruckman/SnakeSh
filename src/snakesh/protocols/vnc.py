from __future__ import annotations

from dataclasses import dataclass
import os
import platform
import shutil
import subprocess

from snakesh.core.models import Session, parse_resolution
from snakesh.protocols.base import ProtocolError
from snakesh.services.external_tools import resolve_executable
from snakesh.services.privilege_service import run_command

_COLOR_MODE_NONE = "none"
_COLOR_MODE_TIGERVNC = "tigervnc"
_PASSWORD_MODE_NONE = "none"
_PASSWORD_MODE_TIGERVNC_ENV = "tigervnc_env"


class VNCClientMissingError(ProtocolError):
    def __init__(self, message: str, *, can_auto_install: bool) -> None:
        super().__init__(message)
        self.can_auto_install = can_auto_install


@dataclass(frozen=True, slots=True)
class _VNCProvider:
    name: str
    executable_candidates: tuple[str, ...]
    target_mode: str = "host"
    launch_prefix: tuple[str, ...] = ()
    fullscreen_flag: tuple[str, ...] = ()
    geometry_flag: tuple[str, ...] = ()
    auto_resize_flag: tuple[str, ...] = ()
    color_mode: str = _COLOR_MODE_NONE
    embed_mode: str = ""
    password_mode: str = _PASSWORD_MODE_NONE


_WINDOWS_PROVIDERS: tuple[_VNCProvider, ...] = (
    _VNCProvider(
        name="TigerVNC Viewer",
        executable_candidates=(
            "vncviewer.exe",
            r"C:\Program Files\TigerVNC\vncviewer.exe",
            r"C:\Program Files (x86)\TigerVNC\vncviewer.exe",
        ),
        fullscreen_flag=("-FullScreen",),
        geometry_flag=("-geometry",),
        auto_resize_flag=("-RemoteResize=1",),
        color_mode=_COLOR_MODE_TIGERVNC,
        password_mode=_PASSWORD_MODE_TIGERVNC_ENV,
    ),
    _VNCProvider(
        name="UltraVNC Viewer",
        executable_candidates=(
            r"C:\Program Files\UltraVNC\vncviewer.exe",
            r"C:\Program Files (x86)\UltraVNC\vncviewer.exe",
        ),
    ),
    _VNCProvider(
        name="TightVNC Viewer",
        executable_candidates=(
            "tvnviewer.exe",
            r"C:\Program Files\TightVNC\tvnviewer.exe",
            r"C:\Program Files (x86)\TightVNC\tvnviewer.exe",
        ),
    ),
    _VNCProvider(
        name="RealVNC Viewer",
        executable_candidates=(
            r"C:\Program Files\RealVNC\VNC Viewer\vncviewer.exe",
            r"C:\Program Files (x86)\RealVNC\VNC Viewer\vncviewer.exe",
        ),
    ),
)

_LINUX_PROVIDERS: tuple[_VNCProvider, ...] = (
    _VNCProvider(
        name="TigerVNC Viewer",
        executable_candidates=("xtigervncviewer", "vncviewer"),
        fullscreen_flag=("-FullScreen",),
        geometry_flag=("-geometry",),
        auto_resize_flag=("-RemoteResize=1",),
        color_mode=_COLOR_MODE_TIGERVNC,
        password_mode=_PASSWORD_MODE_TIGERVNC_ENV,
    ),
    _VNCProvider(
        name="GNOME VNC Viewer",
        executable_candidates=("gvncviewer",),
        fullscreen_flag=("--fullscreen",),
    ),
    _VNCProvider(
        name="Remmina",
        executable_candidates=("remmina",),
        target_mode="uri",
        launch_prefix=("-c",),
    ),
)

_MACOS_PROVIDERS: tuple[_VNCProvider, ...] = (
    _VNCProvider(
        name="TigerVNC Viewer",
        executable_candidates=(
            "vncviewer",
            "/Applications/TigerVNC Viewer.app/Contents/MacOS/TigerVNC Viewer",
            "/Applications/TigerVNC Viewer.app/Contents/MacOS/vncviewer",
            "/Applications/TigerVNC.app/Contents/MacOS/TigerVNC",
            "/Applications/TigerVNC.app/Contents/MacOS/vncviewer",
        ),
        fullscreen_flag=("-FullScreen",),
        geometry_flag=("-geometry",),
        auto_resize_flag=("-RemoteResize=1",),
        color_mode=_COLOR_MODE_TIGERVNC,
        password_mode=_PASSWORD_MODE_TIGERVNC_ENV,
    ),
)

_WINDOWS_WINGET_IDS: tuple[str, ...] = ("TigerVNC.TigerVNC",)
_LINUX_PACKAGE_CANDIDATES: tuple[str, ...] = ("tigervnc-viewer", "tigervnc", "gvncviewer", "remmina")


def build_vnc_command(
    session: Session,
    *,
    allow_install: bool = False,
    linux_parent_window_id: int | None = None,
) -> tuple[list[str], str]:
    command, provider_name, _ = build_vnc_launch(
        session,
        password=None,
        allow_install=allow_install,
        linux_parent_window_id=linux_parent_window_id,
    )
    return command, provider_name


def build_vnc_launch(
    session: Session,
    *,
    password: str | None = None,
    allow_install: bool = False,
    linux_parent_window_id: int | None = None,
) -> tuple[list[str], str, dict[str, str] | None]:
    system = platform.system().lower()
    if linux_parent_window_id is not None and system != "linux":
        raise ProtocolError("Embedded VNC tabs are only supported on Linux.")

    resolved = _resolve_provider(system)
    if resolved is None:
        if allow_install:
            installed, message = _install_default_client(system)
            if not installed:
                raise VNCClientMissingError(message, can_auto_install=_can_auto_install(system))
            resolved = _resolve_provider(system)
        if resolved is None:
            raise VNCClientMissingError(
                _missing_client_message(system),
                can_auto_install=_can_auto_install(system),
            )

    provider, executable = resolved
    command = _build_command(
        provider,
        executable,
        session,
        linux_parent_window_id=linux_parent_window_id,
    )
    launch_env = _launch_environment(provider, password=password, username=session.username)
    return command, provider.name, launch_env


def launch_vnc(
    session: Session,
    *,
    password: str | None = None,
    allow_install: bool = False,
    linux_parent_window_id: int | None = None,
) -> str:
    command, provider_name, launch_env = build_vnc_launch(
        session,
        password=password,
        allow_install=allow_install,
        linux_parent_window_id=linux_parent_window_id,
    )
    subprocess.Popen(command, env=launch_env)
    return provider_name


def has_supported_vnc_client() -> bool:
    return _resolve_provider(platform.system().lower()) is not None


def _resolve_provider(system: str) -> tuple[_VNCProvider, str] | None:
    for provider in _providers_for_system(system):
        executable = _find_executable(provider, system=system)
        if executable:
            return provider, executable
    return None


def _providers_for_system(system: str) -> tuple[_VNCProvider, ...]:
    if system == "windows":
        return _WINDOWS_PROVIDERS
    if system == "linux":
        return _LINUX_PROVIDERS
    if system == "darwin":
        return _MACOS_PROVIDERS
    return _LINUX_PROVIDERS


def _build_command(
    provider: _VNCProvider,
    executable: str,
    session: Session,
    *,
    linux_parent_window_id: int | None = None,
) -> list[str]:
    target = _target_for(provider.target_mode, session)
    display_args = _display_args(provider, session)
    embed_args = _embed_args(provider, linux_parent_window_id)
    return [executable, *provider.launch_prefix, *display_args, *embed_args, target]


def _embed_args(provider: _VNCProvider, linux_parent_window_id: int | None) -> list[str]:
    if linux_parent_window_id is None:
        return []
    if provider.embed_mode == "x11_parent":
        return ["-Parent", str(linux_parent_window_id)]
    raise ProtocolError(f"Embedded VNC tabs are not supported by {provider.name}.")


def _launch_environment(
    provider: _VNCProvider,
    *,
    password: str | None,
    username: str,
) -> dict[str, str] | None:
    if not password:
        return None
    if provider.password_mode != _PASSWORD_MODE_TIGERVNC_ENV:
        return None
    env = os.environ.copy()
    env["VNC_PASSWORD"] = password
    normalized_username = username.strip()
    if normalized_username:
        env.setdefault("VNC_USERNAME", normalized_username)
    return env


def _display_args(provider: _VNCProvider, session: Session) -> list[str]:
    args: list[str] = []
    resolution = parse_resolution(session.display_resolution)
    color_depth = _normalize_color_depth(session.display_color_depth)

    if provider.fullscreen_flag and session.display_fullscreen:
        args.extend(provider.fullscreen_flag)
    elif provider.geometry_flag and resolution:
        width, height = resolution
        args.extend(provider.geometry_flag)
        args.append(f"{width}x{height}")
    if provider.color_mode == _COLOR_MODE_TIGERVNC:
        args.extend(_tigervnc_color_args(color_depth))
        args.append("-RemoteResize=1" if session.vnc_allow_resize else "-RemoteResize=0")
    return args


def _normalize_color_depth(value: int) -> int | None:
    return value if value in (8, 16, 24, 32) else None


def _tigervnc_color_args(color_depth: int | None) -> list[str]:
    if color_depth is None:
        return []
    if color_depth >= 24:
        return ["-FullColor=1"]
    if color_depth == 16:
        return ["-FullColor=0", "-LowColorLevel=1"]
    return ["-FullColor=0", "-LowColorLevel=2"]


def _target_for(mode: str, session: Session) -> str:
    host = session.host.strip()
    if mode == "uri":
        return f"vnc://{host}:{session.port}"

    if session.port == 5900:
        return host
    if 5900 <= session.port <= 5999:
        return f"{host}:{session.port - 5900}"
    return f"{host}::{session.port}"


def _missing_client_message(system: str) -> str:
    details = (
        "No supported VNC viewer was found. SnakeSh supports TigerVNC, UltraVNC, TightVNC, "
        "RealVNC, gvncviewer, and Remmina."
    )
    if _can_auto_install(system):
        return details + " Automatic install is available."
    return details + " Install a supported viewer and retry."


def _can_auto_install(system: str) -> bool:
    if system == "windows":
        return shutil.which("winget") is not None
    if system == "linux":
        return _linux_package_manager() is not None
    return False


def _install_default_client(system: str) -> tuple[bool, str]:
    if system == "windows":
        winget = shutil.which("winget")
        if not winget:
            return False, "TigerVNC is missing and winget is not available for automatic installation."
        failures: list[str] = []
        for package_id in _WINDOWS_WINGET_IDS:
            try:
                result = run_command(
                    [winget, "install", "--id", package_id, "-e", "--source", "winget"],
                    require_elevation=True,
                )
                if result.success:
                    return True, f"Installed {package_id}."
                failures.append(f"{package_id}: {result.message}")
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{package_id}: {exc}")
        return False, "Failed to install a VNC viewer via winget.\n" + "\n".join(failures)

    if system == "linux":
        if not _linux_package_manager():
            return False, "No supported package manager was detected for automatic VNC viewer installation."

        failures: list[str] = []
        for package_name in _LINUX_PACKAGE_CANDIDATES:
            install_cmd = _linux_install_command(package_name)
            if not install_cmd:
                continue
            try:
                result = run_command(install_cmd, require_elevation=True)
                if result.success:
                    return True, f"Installed {package_name}."
                failures.append(f"{package_name}: {result.message}")
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{package_name}: {exc}")
        if failures:
            return False, "Failed to install a VNC viewer automatically.\n" + "\n".join(failures)
        return False, "No install command could be generated for this Linux system."

    return False, f"VNC launcher auto-install is not supported on {system}."


def _linux_package_manager() -> tuple[str, str] | None:
    managers: list[tuple[str, str]] = [
        ("apt-get", "apt"),
        ("dnf", "dnf"),
        ("yum", "yum"),
        ("pacman", "pacman"),
        ("zypper", "zypper"),
    ]
    for executable, manager in managers:
        if shutil.which(executable):
            return executable, manager
    return None


def _linux_install_command(package_name: str) -> list[str] | None:
    manager_info = _linux_package_manager()
    if not manager_info:
        return None
    executable, manager = manager_info
    if manager == "apt":
        return [executable, "install", "-y", package_name]
    if manager == "dnf":
        return [executable, "install", "-y", package_name]
    if manager == "yum":
        return [executable, "install", "-y", package_name]
    if manager == "pacman":
        return [executable, "-S", "--noconfirm", package_name]
    if manager == "zypper":
        return [executable, "--non-interactive", "install", package_name]
    return None


def _find_executable(provider: _VNCProvider, *, system: str) -> str | None:
    return resolve_executable(provider.executable_candidates, platform_name=system)
