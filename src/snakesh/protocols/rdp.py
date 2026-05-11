from __future__ import annotations

import os
import platform
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from snakesh.core.models import Session, is_auto_resolution, normalize_rdp_audio_mode, parse_resolution
from snakesh.protocols.base import ProtocolError
from snakesh.services.external_tools import resolve_executable


_FREERDP_EXECUTABLE_CANDIDATES: tuple[str, ...] = ("xfreerdp",)
_MACOS_XQUARTZ_DISPLAY_FALLBACK = ":0"


def clear_linux_rdp_known_host(session: Session, *, known_hosts_path: Path | None = None) -> bool:
    path = known_hosts_path or (Path.home() / ".config" / "freerdp" / "known_hosts2")
    if not path.exists():
        return False

    host = _normalize_known_host_token(session.host)
    if not host:
        return False
    port = str(_normalize_rdp_port(session.port))

    lines = path.read_text(encoding="utf-8").splitlines()
    kept: list[str] = []
    removed = False
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            kept.append(line)
            continue
        parts = stripped.split()
        if len(parts) < 2:
            kept.append(line)
            continue

        line_host = _normalize_known_host_token(parts[0])
        line_port = parts[1].strip()
        if line_host == host and line_port == port:
            removed = True
            continue
        kept.append(line)

    if not removed:
        return False

    payload = "\n".join(kept)
    if kept:
        payload += "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")
    return True


def build_rdp_command(
    session: Session,
    *,
    password: str | None = None,
    linux_trust_certificate: bool = False,
    linux_parent_window_id: int | None = None,
) -> list[str]:
    system = platform.system().lower()
    target = session.host if session.port == 3389 else f"{session.host}:{session.port}"
    auto_resolution = is_auto_resolution(session.display_resolution)
    resolution = parse_resolution(session.display_resolution)
    color_depth = _normalize_color_depth(session.display_color_depth)
    audio_mode = normalize_rdp_audio_mode(session.rdp_audio_mode)

    if system == "windows":
        mstsc_executable = _windows_mstsc_executable()
        rdp_username = _rdp_username(session)
        if rdp_username and password:
            _seed_windows_rdp_credentials(session, rdp_username, password)
        if rdp_username or session.display_fullscreen or auto_resolution or resolution or color_depth or audio_mode != "local":
            rdp_file = _build_windows_rdp_file(
                session,
                target=target,
                username=rdp_username,
                has_password=bool(password),
                auto_resolution=auto_resolution,
                resolution=resolution,
                fullscreen=session.display_fullscreen,
                color_depth=color_depth,
                audio_mode=audio_mode,
            )
            return [mstsc_executable, str(rdp_file)]
        return [mstsc_executable, f"/v:{target}"]

    if system in {"linux", "darwin"}:
        freerdp_executable = _freerdp_executable(system)
        if freerdp_executable is None:
            raise ProtocolError("FreeRDP client was not found. Install FreeRDP to launch RDP sessions.")
        user = session.username or os.getenv("USER", "")
        embedded_parent = linux_parent_window_id is not None
        if embedded_parent and system != "linux":
            raise ProtocolError("Embedded RDP tabs are only supported on Linux.")
        cmd = [freerdp_executable, f"/v:{target}"]
        if linux_trust_certificate:
            # Avoids interactive certificate prompts by using trust-on-first-use.
            cmd.append("/cert:tofu")
        if linux_parent_window_id is not None:
            cmd.append(f"/parent-window:{linux_parent_window_id}")
        if session.domain:
            cmd.append(f"/d:{session.domain}")
        if user:
            cmd.append(f"/u:{user}")
        if password:
            cmd.append("/from-stdin:force")
        if session.display_fullscreen and not embedded_parent:
            cmd.append("/f")
        elif auto_resolution:
            cmd.append("/dynamic-resolution")
        elif resolution:
            width, height = resolution
            cmd.append(f"/size:{width}x{height}")
        if color_depth:
            cmd.append(f"/bpp:{color_depth}")
        if audio_mode == "local":
            cmd.extend(["/sound", "/audio-mode:0"])
        elif audio_mode == "remote":
            cmd.append("/audio-mode:1")
        else:
            cmd.append("/audio-mode:2")
        return cmd

    raise ProtocolError(f"RDP launcher is not supported on {system}.")


def launch_rdp(
    session: Session,
    *,
    password: str | None = None,
    linux_trust_certificate: bool = False,
) -> None:
    cmd = build_rdp_command(
        session,
        password=password,
        linux_trust_certificate=linux_trust_certificate,
    )
    stdin_payload = build_rdp_stdin_payload(
        session,
        password=password,
    )
    popen_kwargs: dict[str, object] = {}
    launch_env = prepare_rdp_launch_environment()
    if launch_env is not None:
        popen_kwargs["env"] = launch_env
    if platform.system().lower() == "windows":
        creationflags = int(getattr(subprocess, "DETACHED_PROCESS", 0)) | int(
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
        if creationflags:
            popen_kwargs["creationflags"] = creationflags
    _launch_rdp_process(cmd, stdin_payload=stdin_payload, **popen_kwargs)


def prepare_rdp_launch_environment() -> dict[str, str] | None:
    if platform.system().lower() != "darwin":
        return None

    env = os.environ.copy()
    display = (env.get("DISPLAY") or "").strip() or _macos_launchctl_getenv("DISPLAY")
    if not display:
        _launch_macos_xquartz()
        display = _wait_for_macos_x11_display()
    env["DISPLAY"] = display or _MACOS_XQUARTZ_DISPLAY_FALLBACK
    return env


def build_rdp_stdin_payload(
    session: Session,
    *,
    password: str | None = None,
) -> str | None:
    if platform.system().lower() not in {"linux", "darwin"}:
        return None
    if not password:
        return None
    return f"{password}\n"


def has_supported_rdp_client() -> bool:
    system = platform.system().lower()
    if system == "windows":
        return (
            _windows_mstsc_executable() != "mstsc"
            or shutil.which("mstsc.exe") is not None
            or shutil.which("mstsc") is not None
        )
    if system in {"linux", "darwin"}:
        return resolve_executable(_FREERDP_EXECUTABLE_CANDIDATES, platform_name=system) is not None
    return False


def _freerdp_executable(system: str | None = None) -> str | None:
    platform_name = (system or platform.system()).strip().lower()
    resolved = resolve_executable(_FREERDP_EXECUTABLE_CANDIDATES, platform_name=platform_name)
    if resolved:
        return resolved
    if platform_name == "linux":
        return "xfreerdp"
    return None


def _launch_macos_xquartz() -> None:
    try:
        subprocess.run(
            ["open", "-gja", "XQuartz"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
    except Exception:
        return


def _wait_for_macos_x11_display(timeout_seconds: float = 5.0) -> str:
    deadline = time.monotonic() + max(0.1, timeout_seconds)
    while time.monotonic() < deadline:
        display = _macos_launchctl_getenv("DISPLAY")
        if display:
            return display
        if _macos_x11_socket_ready():
            return _MACOS_XQUARTZ_DISPLAY_FALLBACK
        time.sleep(0.1)
    if _macos_x11_socket_ready():
        return _MACOS_XQUARTZ_DISPLAY_FALLBACK
    return ""


def _macos_launchctl_getenv(name: str) -> str:
    try:
        result = subprocess.run(
            ["launchctl", "getenv", name],
            check=False,
            capture_output=True,
            text=True,
            timeout=1,
        )
    except Exception:
        return ""
    if result.returncode != 0:
        return ""
    return (result.stdout or "").strip()


def _macos_x11_socket_ready() -> bool:
    return Path("/tmp/.X11-unix/X0").exists() or Path("/private/tmp/.X11-unix/X0").exists()


def _launch_rdp_process(
    command: list[str],
    *,
    stdin_payload: str | None = None,
    **popen_kwargs: object,
) -> subprocess.Popen[bytes] | subprocess.Popen[str]:
    launch_kwargs: dict[str, object] = dict(popen_kwargs)
    if stdin_payload is not None:
        launch_kwargs["stdin"] = subprocess.PIPE
        launch_kwargs["text"] = True

    process = subprocess.Popen(command, **launch_kwargs)
    if stdin_payload is None:
        return process

    stdin = process.stdin
    if stdin is None:
        try:
            process.kill()
        except Exception:
            pass
        raise ProtocolError("Failed to open stdin for FreeRDP credential handoff.")

    try:
        stdin.write(stdin_payload)
        stdin.flush()
    except Exception as exc:
        try:
            process.kill()
        except Exception:
            pass
        raise ProtocolError("Failed to supply RDP credentials to FreeRDP via stdin.") from exc
    finally:
        try:
            stdin.close()
        except Exception:
            pass
    return process


def _seed_windows_rdp_credentials(session: Session, username: str, password: str) -> None:
    # Preload credentials into Windows Credential Manager so mstsc can use them.
    targets = [f"TERMSRV/{session.host}"]
    if session.port and session.port != 3389:
        targets.append(f"TERMSRV/{session.host}:{session.port}")

    for credential_target in targets:
        try:
            subprocess.run(
                [
                    "cmdkey",
                    f"/generic:{credential_target}",
                    f"/user:{username}",
                    f"/pass:{password}",
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=3,
            )
        except subprocess.TimeoutExpired:
            # Allow launch to continue; mstsc can still prompt for credentials.
            continue
        except subprocess.CalledProcessError as exc:
            output = (exc.stderr or exc.stdout or "").strip()
            if output:
                raise ProtocolError(f"Failed to preload RDP credentials: {output}") from exc
            raise ProtocolError("Failed to preload RDP credentials with cmdkey.") from exc


def _windows_mstsc_executable() -> str:
    system_root = (os.environ.get("SystemRoot") or os.environ.get("WINDIR") or r"C:\Windows").strip()
    candidates: list[Path] = [
        Path(system_root) / "System32" / "mstsc.exe",
        Path(system_root) / "Sysnative" / "mstsc.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    resolved = shutil.which("mstsc.exe") or shutil.which("mstsc")
    return resolved or "mstsc"


def _rdp_username(session: Session) -> str:
    user = (session.username or "").strip()
    domain = (session.domain or "").strip()
    if not user:
        return ""
    if "\\" in user or "@" in user:
        return user
    if domain:
        return f"{domain}\\{user}"
    return user


def _build_windows_rdp_file(
    session: Session,
    *,
    target: str,
    username: str,
    has_password: bool,
    auto_resolution: bool,
    resolution: tuple[int, int] | None,
    fullscreen: bool,
    color_depth: int | None,
    audio_mode: str,
) -> Path:
    temp_dir = Path(tempfile.gettempdir()) / "snakesh"
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_path = temp_dir / f"rdp-{session.id}.rdp"
    lines = [
        f"full address:s:{target}",
        f"screen mode id:i:{2 if fullscreen else 1}",
        "administrative session:i:0",
        "enablecredsspsupport:i:1",
        "authentication level:i:2",
    ]
    if username:
        lines.extend(
            [
                f"username:s:{username}",
                f"prompt for credentials:i:{0 if has_password else 1}",
                f"promptcredentialonce:i:{0 if has_password else 1}",
            ]
        )
    if resolution and not fullscreen:
        width, height = resolution
        lines.extend(
            [
                f"desktopwidth:i:{width}",
                f"desktopheight:i:{height}",
            ]
        )
    if auto_resolution and not fullscreen:
        lines.extend(
            [
                "dynamic resolution:i:1",
                "smart sizing:i:1",
            ]
        )
    if color_depth:
        lines.append(f"session bpp:i:{color_depth}")
    lines.append(f"audiomode:i:{_rdp_audio_mode_windows_value(audio_mode)}")
    temp_path.write_text("\n".join(lines), encoding="utf-8")
    return temp_path


def _normalize_color_depth(value: int) -> int | None:
    return value if value in (8, 16, 24, 32) else None


def _normalize_rdp_port(port: int | str | None) -> int:
    try:
        parsed = int(port) if port is not None else 3389
    except (TypeError, ValueError):
        return 3389
    if parsed <= 0 or parsed > 65535:
        return 3389
    return parsed


def _normalize_known_host_token(value: str | None) -> str:
    token = (value or "").strip().lower()
    if token.startswith("[") and token.endswith("]"):
        token = token[1:-1].strip()
    return token


def _rdp_audio_mode_windows_value(mode: str) -> int:
    normalized = normalize_rdp_audio_mode(mode)
    if normalized == "remote":
        return 1
    if normalized == "mute":
        return 2
    return 0
