from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import platform
import shutil


@dataclass(slots=True)
class PlatformDependency:
    id: str
    display_name: str
    command: str
    required_for: str
    can_auto_install: bool
    install_command: list[str] | None = None
    is_available: Callable[[], bool] | None = None


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


def _windows_install_command(package_id: str) -> list[str] | None:
    if not shutil.which("winget"):
        return None
    return ["winget", "install", "--id", package_id, "-e", "--source", "winget"]


def required_dependencies() -> list[PlatformDependency]:
    from snakesh.protocols.nomachine import has_supported_nomachine_client
    from snakesh.protocols.vnc import has_supported_vnc_client

    system = platform.system().lower()
    deps: list[PlatformDependency] = []

    # Needed for local SSH keypair generation and key-copy workflows.
    deps.append(
        PlatformDependency(
            id="ssh-keygen",
            display_name="OpenSSH keygen",
            command="ssh-keygen",
            required_for="SSH key-based login setup",
            can_auto_install=False,
        )
    )

    if system == "windows":
        deps.append(
            PlatformDependency(
                id="mstsc",
                display_name="Microsoft Remote Desktop (mstsc)",
                command="mstsc",
                required_for="RDP sessions",
                can_auto_install=False,
            )
        )
        deps.append(
            PlatformDependency(
                id="vncviewer",
                display_name="VNC Viewer (TigerVNC or compatible)",
                command="vncviewer",
                required_for="VNC sessions",
                can_auto_install=False,
                is_available=has_supported_vnc_client,
            )
        )
        deps.append(
            PlatformDependency(
                id="nxplayer",
                display_name="NoMachine Player (nxplayer)",
                command="nxplayer",
                required_for="NoMachine sessions",
                can_auto_install=False,
                is_available=has_supported_nomachine_client,
            )
        )
        windows_vnc_cmd = _windows_install_command("TigerVNC.TigerVNC")
        if windows_vnc_cmd:
            for dep in deps:
                if dep.id == "vncviewer":
                    dep.install_command = windows_vnc_cmd
                    dep.can_auto_install = True
        return deps

    if system == "linux":
        deps.append(
            PlatformDependency(
                id="xfreerdp",
                display_name="FreeRDP client (xfreerdp)",
                command="xfreerdp",
                required_for="RDP sessions",
                can_auto_install=False,
            )
        )
        deps.append(
            PlatformDependency(
                id="vncviewer",
                display_name="VNC Viewer (TigerVNC or compatible)",
                command="vncviewer",
                required_for="VNC sessions",
                can_auto_install=False,
                is_available=has_supported_vnc_client,
            )
        )
        deps.append(
            PlatformDependency(
                id="nxplayer",
                display_name="NoMachine Player (nxplayer)",
                command="nxplayer",
                required_for="NoMachine sessions",
                can_auto_install=False,
                is_available=has_supported_nomachine_client,
            )
        )
        deps.append(
            PlatformDependency(
                id="xauth",
                display_name="xauth",
                command="xauth",
                required_for="X11 forwarding",
                can_auto_install=False,
            )
        )

        xfreerdp_cmd = _linux_install_command("freerdp2-x11") or _linux_install_command("freerdp")
        vnc_cmd = _linux_install_command("tigervnc-viewer") or _linux_install_command("tigervnc")
        xauth_cmd = _linux_install_command("xauth")
        sshkey_cmd = _linux_install_command("openssh-client")

        for dep in deps:
            if dep.id == "xfreerdp" and xfreerdp_cmd:
                dep.install_command = xfreerdp_cmd
                dep.can_auto_install = True
            if dep.id == "vncviewer" and vnc_cmd:
                dep.install_command = vnc_cmd
                dep.can_auto_install = True
            if dep.id == "xauth" and xauth_cmd:
                dep.install_command = xauth_cmd
                dep.can_auto_install = True
            if dep.id == "ssh-keygen" and sshkey_cmd:
                dep.install_command = sshkey_cmd
                dep.can_auto_install = True
        return deps

    # Other platforms: keep checks but no auto-install assumptions.
    deps.append(
        PlatformDependency(
            id="xfreerdp",
            display_name="FreeRDP client (xfreerdp)",
            command="xfreerdp",
            required_for="RDP sessions",
            can_auto_install=False,
        )
    )
    deps.append(
        PlatformDependency(
            id="vncviewer",
            display_name="VNC Viewer (TigerVNC or compatible)",
            command="vncviewer",
            required_for="VNC sessions",
            can_auto_install=False,
            is_available=has_supported_vnc_client,
        )
    )
    deps.append(
        PlatformDependency(
            id="nxplayer",
            display_name="NoMachine Player (nxplayer)",
            command="nxplayer",
            required_for="NoMachine sessions",
            can_auto_install=False,
            is_available=has_supported_nomachine_client,
        )
    )
    return deps


def missing_dependencies() -> list[PlatformDependency]:
    missing: list[PlatformDependency] = []
    for dep in required_dependencies():
        if dep.is_available is not None:
            try:
                available = dep.is_available()
            except Exception:
                available = shutil.which(dep.command) is not None
            if not available:
                missing.append(dep)
            continue
        if shutil.which(dep.command) is None:
            missing.append(dep)
    return missing


def attempt_auto_install(dep: PlatformDependency) -> tuple[bool, str]:
    if not dep.can_auto_install or not dep.install_command:
        return False, "Auto-install is not available on this platform."
    try:
        from snakesh.services.privilege_service import run_command

        result = run_command(dep.install_command, require_elevation=True)
        if not result.success:
            return False, result.message
        if dep.is_available is not None:
            try:
                if dep.is_available():
                    return True, "Installed successfully."
            except Exception:
                pass
        if shutil.which(dep.command):
            return True, "Installed successfully."
        return False, "Install command finished, but dependency is still missing."
    except Exception as exc:
        return False, str(exc)


def suggested_install_command(dep: PlatformDependency) -> list[str] | None:
    if dep.install_command:
        return dep.install_command
    if platform.system().lower() != "windows":
        return None
    if dep.id == "ssh-keygen":
        return _windows_install_command("Microsoft.OpenSSH.Beta")
    if dep.id == "vncviewer":
        return _windows_install_command("TigerVNC.TigerVNC")
    return None


def dependency_help_url(dep: PlatformDependency) -> str | None:
    if dep.id == "nxplayer":
        return "https://www.nomachine.com/"
    return None
