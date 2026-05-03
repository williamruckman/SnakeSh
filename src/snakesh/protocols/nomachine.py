from __future__ import annotations

from html import escape
from pathlib import Path
import os
import platform
import re
import shutil
import subprocess
from typing import Any
import xml.etree.ElementTree as ET

from snakesh.core.models import Session
from snakesh.core.paths import data_dir
from snakesh.protocols.base import ProtocolError

_DEFAULT_NOMACHINE_PORT = 4000

_WINDOWS_EXECUTABLE_CANDIDATES: tuple[str, ...] = (
    "nxplayer.exe",
    r"C:\Program Files\NoMachine\bin\nxplayer.exe",
    r"C:\Program Files (x86)\NoMachine\bin\nxplayer.exe",
)

_LINUX_EXECUTABLE_CANDIDATES: tuple[str, ...] = (
    "nxplayer",
    "/usr/NX/bin/nxplayer",
)

_MACOS_EXECUTABLE_CANDIDATES: tuple[str, ...] = (
    "nxplayer",
    "/Applications/NoMachine.app/Contents/MacOS/nxplayer",
)

_GENERAL_EXCLUSIVE_KEYS: tuple[str, ...] = (
    "Connection service",
    "NoMachine daemon port",
    "Server host",
    "Server port",
)


def build_nomachine_command(session: Session) -> tuple[list[str], str]:
    system = platform.system().lower()
    executable = _resolve_executable(system)
    if not executable:
        raise ProtocolError(_missing_client_message(system))

    session_file = _build_session_file(session)
    return [executable, "--session", str(session_file)], "NoMachine Player"


def build_nomachine_launch(session: Session) -> tuple[list[str], str, dict[str, str] | None]:
    command, viewer_name = build_nomachine_command(session)
    return command, viewer_name, None


def launch_nomachine(session: Session) -> str:
    command, viewer_name = build_nomachine_command(session)
    subprocess.Popen(command)
    return viewer_name


def has_supported_nomachine_client() -> bool:
    return _resolve_executable(platform.system().lower()) is not None


def _resolve_executable(system: str) -> str | None:
    for candidate in _candidates_for_system(system):
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
        if Path(candidate).exists():
            return candidate
    return None


def _candidates_for_system(system: str) -> tuple[str, ...]:
    if system == "windows":
        return _WINDOWS_EXECUTABLE_CANDIDATES
    if system == "darwin":
        return _MACOS_EXECUTABLE_CANDIDATES
    return _LINUX_EXECUTABLE_CANDIDATES


def _build_session_file(session: Session) -> Path:
    host = (session.host or "").strip()
    if not host:
        raise ProtocolError("NoMachine launch failed: session host is empty.")
    username = (session.username or "").strip()
    port = _normalize_port(session.port)

    profile_dir = data_dir() / "nomachine"
    profile_dir.mkdir(parents=True, exist_ok=True)
    _harden_private_path(profile_dir, 0o700)
    token = _safe_session_token(session)
    session_file = profile_dir / f"{token}.nxs"
    preserved_groups = _load_preserved_profile_groups(session_file)
    session_file.write_text(
        _render_nxs(
            host=host,
            port=port,
            username=username,
            session=session,
            preserved_groups=preserved_groups,
        ),
        encoding="utf-8",
    )
    _harden_private_path(session_file, 0o600)
    return session_file


def _render_nxs(
    *,
    host: str,
    port: int,
    username: str,
    session: Session,
    preserved_groups: dict[str, dict[str, str]] | None = None,
) -> str:
    groups: dict[str, dict[str, str]] = {
        group_name: dict(options)
        for group_name, options in (preserved_groups or {}).items()
    }

    # NoMachine expects connection endpoint keys in General.
    # If old/generated profiles left these keys in other groups, nxplayer can
    # treat values as ambiguous and ignore the host.
    for group_name, options in groups.items():
        if group_name == "General":
            continue
        for key in _GENERAL_EXCLUSIVE_KEYS:
            options.pop(key, None)

    general = groups.setdefault("General", {})
    login = groups.setdefault("Login", {})
    images = groups.setdefault("Images", {})
    services = groups.setdefault("Services", {})

    general["Connection service"] = "nx"
    general["NoMachine daemon port"] = str(port)
    general["Remember password"] = _normalize_bool_option(general.get("Remember password"), default="true")
    remember_username = "true" if username else "false"
    general["Remember username"] = remember_username
    general["Server host"] = host
    general["Server port"] = "22"
    # Prevent repeated first-time media prompts unless the user explicitly
    # re-enables them in NoMachine.
    general["Show remote audio alert message"] = _normalize_bool_option(
        general.get("Show remote audio alert message"),
        default="false",
    )
    general["Physical desktop auto-resize"] = _to_bool_option(session.nomachine_physical_desktop_auto_resize)
    general["Physical desktop resize mode"] = (
        "viewport" if str(session.nomachine_physical_desktop_resize_mode).strip().lower() == "viewport" else "scaled"
    )
    general["Link quality"] = str(_normalize_quality_option(session.nomachine_link_quality, default=5))

    login["Server authentication method"] = login.get("Server authentication method", "system") or "system"
    login["Auth"] = login.get("Auth", "")
    login["User"] = username
    login["NX login method"] = "password"
    login["System auth"] = login.get("System auth", "EMPTY_PASSWORD") or "EMPTY_PASSWORD"
    login["System login method"] = login.get("System login method", "password") or "password"
    remember_nomachine_password = _normalize_bool_option(
        login.get("Remember NoMachine password"),
        default="false",
    )
    login["Remember NoMachine password"] = remember_nomachine_password
    remember_two_factor_password = _normalize_bool_option(
        login.get("Remember two-factor authentication password"),
        default="false",
    )
    login["Remember two-factor authentication password"] = remember_two_factor_password

    images["Video encoding quality"] = str(_normalize_quality_option(session.nomachine_video_quality, default=5))

    services["Audio"] = _to_bool_option(session.nomachine_audio_enabled)
    services["Mute audio of the remote physical desktop"] = _to_bool_option(session.nomachine_mute_remote_audio)

    lines = [
        "<!DOCTYPE NXClientSettings>",
        '<NXClientSettings version="2.3" application="nxclient" >',
    ]
    for group_name in _group_output_order(groups):
        options = groups[group_name]
        escaped_group_name = escape(group_name, quote=True)
        lines.append(f'  <group name="{escaped_group_name}" >')
        for key, value in options.items():
            escaped_key = escape(key, quote=True)
            escaped_value = escape(value, quote=True)
            lines.append(f'    <option key="{escaped_key}" value="{escaped_value}" />')
        lines.append("  </group>")
    lines.extend(["</NXClientSettings>", ""])
    return "\n".join(lines)


def _group_output_order(groups: dict[str, dict[str, str]]) -> list[str]:
    ordered: list[str] = []
    for name in ("General", "Login"):
        if name in groups:
            ordered.append(name)
    for name in groups:
        if name not in {"General", "Login"}:
            ordered.append(name)
    return ordered


def _load_preserved_profile_groups(session_file: Path) -> dict[str, dict[str, str]]:
    if not session_file.exists():
        return {}

    try:
        payload = session_file.read_text(encoding="utf-8")
    except OSError:
        return {}

    normalized = re.sub(r"<!DOCTYPE[^>]*>\s*", "", payload, count=1).strip()
    if not normalized:
        return {}

    try:
        root = ET.fromstring(normalized)
    except ET.ParseError:
        return {}

    groups: dict[str, dict[str, str]] = {}
    for group in root.findall("group"):
        group_name = (group.attrib.get("name") or "").strip()
        if not group_name:
            continue
        target = groups.setdefault(group_name, {})
        for option in group.findall("option"):
            key = (option.attrib.get("key") or "").strip()
            if not key:
                continue
            target[key] = option.attrib.get("value", "")
    return groups


def _harden_private_path(path: Path, mode: int) -> None:
    if os.name == "nt":
        return
    try:
        path.chmod(mode)
    except Exception:
        return

def _normalize_bool_option(value: str | None, *, default: str) -> str:
    if not value:
        return default
    lowered = value.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return "true"
    if lowered in {"0", "false", "no", "off"}:
        return "false"
    return default


def _to_bool_option(value: Any) -> str:
    return "true" if bool(value) else "false"


def _normalize_quality_option(value: Any, *, default: int) -> int:
    try:
        fallback = int(default)
    except (TypeError, ValueError):
        fallback = 5
    fallback = max(0, min(9, fallback))
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(0, min(9, parsed))


def _safe_session_token(session: Session) -> str:
    raw = (session.id or session.name or session.host or "nomachine").strip()
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", raw).strip("._-")
    return cleaned or "nomachine"


def _normalize_port(port: int | str | None) -> int:
    try:
        parsed = int(port) if port is not None else _DEFAULT_NOMACHINE_PORT
    except (TypeError, ValueError):
        return _DEFAULT_NOMACHINE_PORT
    if parsed <= 0 or parsed > 65535:
        return _DEFAULT_NOMACHINE_PORT
    return parsed


def _missing_client_message(system: str) -> str:
    if system == "windows":
        return "NoMachine client was not found. Install NoMachine to launch NoMachine sessions."
    if system == "darwin":
        return "NoMachine client was not found. Install NoMachine.app to launch NoMachine sessions."
    if system == "linux":
        return "NoMachine client was not found. Install NoMachine to launch NoMachine sessions."
    return f"NoMachine launcher is not supported on {system}."
