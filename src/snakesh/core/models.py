from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4


class Protocol(str, Enum):
    SSH = "ssh"
    SFTP = "sftp"
    RDP = "rdp"
    VNC = "vnc"
    NOMACHINE = "nomachine"
    TELNET = "telnet"
    SERIAL = "serial"


_AUTO_RESOLUTION_TOKENS = {"auto", "adaptive", "dynamic"}
_VALID_RDP_AUDIO_MODES = {"local", "remote", "mute"}
_VALID_REMOTE_LAUNCH_MODES = {"tab", "detached"}
_VALID_NOMACHINE_RESIZE_MODES = {"scaled", "viewport"}
_VALID_SERIAL_PARITY = {"none", "even", "odd", "mark", "space"}
_VALID_SERIAL_STOP_BITS = {"1", "1.5", "2"}
_VALID_SERIAL_FLOW_CONTROL = {"none", "rtscts", "xonxoff", "dsrdtr"}
_VALID_SERIAL_TERMINAL_TYPE_ALIASES = {
    "auto": "auto",
    "default": "auto",
    "vt-100": "vt100",
    "vt100": "vt100",
    "ansi": "ansi",
    "xterm": "xterm",
    "xterm-256color": "xterm-256color",
}
DEFAULT_SFTP_LOCAL_FOLDER = "~"
DEFAULT_SFTP_REMOTE_FOLDER = "."


@dataclass(slots=True)
class SSHDynamicTunnel:
    bind_host: str = "127.0.0.1"
    bind_port: int = 1080
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "bind_host": self.bind_host,
            "bind_port": self.bind_port,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "SSHDynamicTunnel":
        bind_port = _normalize_port(raw.get("bind_port", 1080), default=1080)
        bind_host = str(raw.get("bind_host", "127.0.0.1")).strip() or "127.0.0.1"
        enabled = bool(raw.get("enabled", True))
        return cls(bind_host=bind_host, bind_port=bind_port, enabled=enabled)


@dataclass(slots=True)
class SSHStaticTunnel:
    direction: str = "local"
    bind_host: str = "127.0.0.1"
    bind_port: int = 0
    target_host: str = "127.0.0.1"
    target_port: int = 0
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "direction": self.direction,
            "bind_host": self.bind_host,
            "bind_port": self.bind_port,
            "target_host": self.target_host,
            "target_port": self.target_port,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "SSHStaticTunnel":
        direction = str(raw.get("direction", "local")).strip().lower()
        if direction not in ("local", "remote"):
            direction = "local"
        bind_host = str(raw.get("bind_host", "127.0.0.1")).strip() or "127.0.0.1"
        target_host = str(raw.get("target_host", "127.0.0.1")).strip() or "127.0.0.1"
        bind_port = _normalize_port(raw.get("bind_port", 0), default=0)
        target_port = _normalize_port(raw.get("target_port", 0), default=0)
        enabled = bool(raw.get("enabled", True))
        return cls(
            direction=direction,
            bind_host=bind_host,
            bind_port=bind_port,
            target_host=target_host,
            target_port=target_port,
            enabled=enabled,
        )


@dataclass(slots=True)
class SSHAutomationStep:
    step_type: str = "command"  # command | sleep | expect
    command: str = ""
    sleep_seconds: float = 1.0
    expect_text: str = ""
    expect_timeout_seconds: float = 15.0
    expect_on_timeout: str = "terminate"  # continue | terminate

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_type": self.step_type,
            "command": self.command,
            "sleep_seconds": self.sleep_seconds,
            "expect_text": self.expect_text,
            "expect_timeout_seconds": self.expect_timeout_seconds,
            "expect_on_timeout": self.expect_on_timeout,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "SSHAutomationStep":
        step_type = str(raw.get("step_type", "command")).strip().lower()
        if step_type not in ("command", "sleep", "expect"):
            step_type = "command"

        expect_on_timeout = str(raw.get("expect_on_timeout", "terminate")).strip().lower()
        if expect_on_timeout not in ("continue", "terminate"):
            expect_on_timeout = "terminate"

        return cls(
            step_type=step_type,
            command=str(raw.get("command", "")),
            sleep_seconds=_normalize_float(raw.get("sleep_seconds", 1.0), default=1.0, min_value=0.0, max_value=86400.0),
            expect_text=str(raw.get("expect_text", "")),
            expect_timeout_seconds=_normalize_float(
                raw.get("expect_timeout_seconds", 15.0),
                default=15.0,
                min_value=0.1,
                max_value=86400.0,
            ),
            expect_on_timeout=expect_on_timeout,
        )


@dataclass(slots=True)
class Session:
    name: str
    host: str
    protocol: Protocol
    port: int
    username: str = ""
    domain: str = ""
    display_resolution: str = ""
    display_fullscreen: bool = False
    display_color_depth: int = 0
    vnc_allow_resize: bool = False
    rdp_audio_mode: str = "local"
    remote_launch_mode: str = "tab"
    nomachine_audio_enabled: bool = True
    nomachine_mute_remote_audio: bool = True
    nomachine_physical_desktop_auto_resize: bool = False
    nomachine_physical_desktop_resize_mode: str = "scaled"
    nomachine_link_quality: int = 5
    nomachine_video_quality: int = 5
    telnet_terminal_type: str = "xterm-256color"
    telnet_connect_timeout_seconds: float = 10.0
    telnet_use_tls: bool = False
    telnet_tls_verify: bool = True
    serial_baud_rate: int = 9600
    serial_data_bits: int = 8
    serial_parity: str = "none"
    serial_stop_bits: str = "1"
    serial_flow_control: str = "none"
    serial_terminal_type: str = "auto"
    notes: str = ""
    sftp_local_folder: str = DEFAULT_SFTP_LOCAL_FOLDER
    sftp_remote_folder: str = DEFAULT_SFTP_REMOTE_FOLDER
    use_key_auth: bool = True
    private_key_path: str = ""
    public_key_path: str = ""
    save_password: bool = False
    terminal_color_override_enabled: bool = False
    terminal_bg_color: str = ""
    terminal_fg_color: str = ""
    shell_banner_message: str = ""
    shell_banner_color: str = ""
    shell_banner_blink: bool = False
    x11_forwarding: bool = False
    ssh_legacy_compatibility: bool = False
    ssh_keepalive: bool = False
    ssh_automation_enabled: bool = False
    ssh_automation_steps: list[SSHAutomationStep] = field(default_factory=list)
    ssh_dynamic_tunnels: list[SSHDynamicTunnel] = field(default_factory=list)
    ssh_static_tunnels: list[SSHStaticTunnel] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    folder: str = "Default"
    id: str = field(default_factory=lambda: str(uuid4()))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "host": self.host,
            "protocol": self.protocol.value,
            "port": self.port,
            "username": self.username,
            "domain": self.domain,
            "display_resolution": self.display_resolution,
            "display_fullscreen": self.display_fullscreen,
            "display_color_depth": self.display_color_depth,
            "vnc_allow_resize": self.vnc_allow_resize,
            "rdp_audio_mode": self.rdp_audio_mode,
            "remote_launch_mode": self.remote_launch_mode,
            "nomachine_audio_enabled": self.nomachine_audio_enabled,
            "nomachine_mute_remote_audio": self.nomachine_mute_remote_audio,
            "nomachine_physical_desktop_auto_resize": self.nomachine_physical_desktop_auto_resize,
            "nomachine_physical_desktop_resize_mode": self.nomachine_physical_desktop_resize_mode,
            "nomachine_link_quality": self.nomachine_link_quality,
            "nomachine_video_quality": self.nomachine_video_quality,
            "telnet_terminal_type": self.telnet_terminal_type,
            "telnet_connect_timeout_seconds": self.telnet_connect_timeout_seconds,
            "telnet_use_tls": self.telnet_use_tls,
            "telnet_tls_verify": self.telnet_tls_verify,
            "serial_baud_rate": self.serial_baud_rate,
            "serial_data_bits": self.serial_data_bits,
            "serial_parity": self.serial_parity,
            "serial_stop_bits": self.serial_stop_bits,
            "serial_flow_control": self.serial_flow_control,
            "serial_terminal_type": self.serial_terminal_type,
            "notes": self.notes,
            "sftp_local_folder": self.sftp_local_folder,
            "sftp_remote_folder": self.sftp_remote_folder,
            "use_key_auth": self.use_key_auth,
            "private_key_path": self.private_key_path,
            "public_key_path": self.public_key_path,
            "save_password": self.save_password,
            "terminal_color_override_enabled": self.terminal_color_override_enabled,
            "terminal_bg_color": self.terminal_bg_color,
            "terminal_fg_color": self.terminal_fg_color,
            "shell_banner_message": self.shell_banner_message,
            "shell_banner_color": self.shell_banner_color,
            "shell_banner_blink": self.shell_banner_blink,
            "x11_forwarding": self.x11_forwarding,
            "ssh_legacy_compatibility": self.ssh_legacy_compatibility,
            "ssh_keepalive": self.ssh_keepalive,
            "ssh_automation_enabled": self.ssh_automation_enabled,
            "ssh_automation_steps": [step.to_dict() for step in self.ssh_automation_steps],
            "ssh_dynamic_tunnels": [tunnel.to_dict() for tunnel in self.ssh_dynamic_tunnels],
            "ssh_static_tunnels": [tunnel.to_dict() for tunnel in self.ssh_static_tunnels],
            "tags": self.tags,
            "folder": self.folder,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Session":
        raw_color_depth = raw.get("display_color_depth", 0)
        try:
            display_color_depth = int(raw_color_depth)
        except (TypeError, ValueError):
            display_color_depth = 0
        if display_color_depth not in (8, 16, 24, 32):
            display_color_depth = 0

        dynamic_tunnels_raw = raw.get("ssh_dynamic_tunnels", [])
        dynamic_tunnels: list[SSHDynamicTunnel] = []
        if isinstance(dynamic_tunnels_raw, list):
            for item in dynamic_tunnels_raw:
                if isinstance(item, dict):
                    dynamic_tunnels.append(SSHDynamicTunnel.from_dict(item))

        static_tunnels_raw = raw.get("ssh_static_tunnels", [])
        static_tunnels: list[SSHStaticTunnel] = []
        if isinstance(static_tunnels_raw, list):
            for item in static_tunnels_raw:
                if isinstance(item, dict):
                    static_tunnels.append(SSHStaticTunnel.from_dict(item))

        automation_steps_raw = raw.get("ssh_automation_steps", [])
        automation_steps: list[SSHAutomationStep] = []
        if isinstance(automation_steps_raw, list):
            for item in automation_steps_raw:
                if isinstance(item, dict):
                    step = SSHAutomationStep.from_dict(item)
                    if step.step_type == "command" and not step.command.strip():
                        continue
                    if step.step_type == "expect" and not step.expect_text:
                        continue
                    automation_steps.append(step)

        protocol = _normalize_protocol(raw.get("protocol", "ssh"))
        raw_host = str(raw.get("host", ""))
        if protocol == Protocol.SERIAL and not raw_host.strip():
            raw_host = str(raw.get("serial_port", ""))

        return cls(
            id=raw.get("id", str(uuid4())),
            name=raw["name"],
            host=raw_host,
            protocol=protocol,
            port=int(raw["port"]),
            username=str(raw.get("username", "")),
            domain=str(raw.get("domain", "")),
            display_resolution=_normalize_resolution(raw.get("display_resolution", "")),
            display_fullscreen=bool(raw.get("display_fullscreen", False)),
            display_color_depth=display_color_depth,
            vnc_allow_resize=bool(raw.get("vnc_allow_resize", False)),
            rdp_audio_mode=normalize_rdp_audio_mode(raw.get("rdp_audio_mode", "local")),
            remote_launch_mode=normalize_remote_launch_mode(raw.get("remote_launch_mode", "tab")),
            nomachine_audio_enabled=bool(raw.get("nomachine_audio_enabled", True)),
            nomachine_mute_remote_audio=bool(raw.get("nomachine_mute_remote_audio", True)),
            nomachine_physical_desktop_auto_resize=bool(raw.get("nomachine_physical_desktop_auto_resize", False)),
            nomachine_physical_desktop_resize_mode=normalize_nomachine_resize_mode(
                raw.get("nomachine_physical_desktop_resize_mode", "scaled")
            ),
            nomachine_link_quality=normalize_nomachine_quality(raw.get("nomachine_link_quality", 5), default=5),
            nomachine_video_quality=normalize_nomachine_quality(raw.get("nomachine_video_quality", 5), default=5),
            telnet_terminal_type=_normalize_telnet_terminal_type(raw.get("telnet_terminal_type", "xterm-256color")),
            telnet_connect_timeout_seconds=_normalize_telnet_connect_timeout(
                raw.get("telnet_connect_timeout_seconds", 10.0)
            ),
            telnet_use_tls=bool(raw.get("telnet_use_tls", False)),
            telnet_tls_verify=bool(raw.get("telnet_tls_verify", True)),
            serial_baud_rate=_normalize_serial_baud_rate(raw.get("serial_baud_rate", 9600)),
            serial_data_bits=normalize_serial_data_bits(raw.get("serial_data_bits", 8)),
            serial_parity=normalize_serial_parity(raw.get("serial_parity", "none")),
            serial_stop_bits=normalize_serial_stop_bits(raw.get("serial_stop_bits", "1")),
            serial_flow_control=normalize_serial_flow_control(raw.get("serial_flow_control", "none")),
            serial_terminal_type=normalize_serial_terminal_type(raw.get("serial_terminal_type", "auto")),
            notes=raw.get("notes", ""),
            sftp_local_folder=_normalize_sftp_local_folder(
                raw.get("sftp_local_folder", DEFAULT_SFTP_LOCAL_FOLDER)
            ),
            sftp_remote_folder=_normalize_sftp_remote_folder(
                raw.get("sftp_remote_folder", DEFAULT_SFTP_REMOTE_FOLDER)
            ),
            use_key_auth=bool(raw.get("use_key_auth", True)),
            private_key_path=raw.get("private_key_path", ""),
            public_key_path=raw.get("public_key_path", ""),
            save_password=bool(raw.get("save_password", False)),
            terminal_color_override_enabled=bool(raw.get("terminal_color_override_enabled", False)),
            terminal_bg_color=str(raw.get("terminal_bg_color", "")),
            terminal_fg_color=str(raw.get("terminal_fg_color", "")),
            shell_banner_message=_normalize_shell_banner_message(raw.get("shell_banner_message", "")),
            shell_banner_color=str(raw.get("shell_banner_color", "")).strip(),
            shell_banner_blink=bool(raw.get("shell_banner_blink", False)),
            x11_forwarding=bool(raw.get("x11_forwarding", False)),
            ssh_legacy_compatibility=bool(raw.get("ssh_legacy_compatibility", False)),
            ssh_keepalive=bool(raw.get("ssh_keepalive", False)),
            ssh_automation_enabled=bool(raw.get("ssh_automation_enabled", False)),
            ssh_automation_steps=automation_steps,
            ssh_dynamic_tunnels=dynamic_tunnels,
            ssh_static_tunnels=static_tunnels,
            tags=list(raw.get("tags", [])),
            folder=raw.get("folder", "Default"),
        )


def parse_resolution(value: str) -> tuple[int, int] | None:
    cleaned = value.strip().lower().replace(" ", "")
    if not cleaned or cleaned in {"default"} or cleaned in _AUTO_RESOLUTION_TOKENS:
        return None
    if "x" not in cleaned:
        return None
    width_raw, height_raw = cleaned.split("x", 1)
    if not (width_raw.isdigit() and height_raw.isdigit()):
        return None
    width = int(width_raw)
    height = int(height_raw)
    if width <= 0 or height <= 0:
        return None
    return width, height


def _normalize_resolution(value: Any) -> str:
    raw = str(value).strip().lower().replace(" ", "")
    if not raw or raw == "default":
        return ""
    if raw in _AUTO_RESOLUTION_TOKENS:
        return "auto"
    parsed = parse_resolution(raw)
    if not parsed:
        return ""
    width, height = parsed
    return f"{width}x{height}"


def is_auto_resolution(value: str) -> bool:
    cleaned = value.strip().lower().replace(" ", "")
    return cleaned in _AUTO_RESOLUTION_TOKENS


def normalize_rdp_audio_mode(value: Any) -> str:
    cleaned = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    if cleaned in ("", "default", "local", "play_local", "on_this_computer", "this_computer"):
        return "local"
    if cleaned in ("remote", "play_remote", "on_remote", "on_remote_computer"):
        return "remote"
    if cleaned in ("mute", "off", "disabled", "disable"):
        return "mute"
    if cleaned in _VALID_RDP_AUDIO_MODES:
        return cleaned
    return "local"


def normalize_remote_launch_mode(value: Any) -> str:
    cleaned = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    if cleaned in ("", "default", "tab", "tabbed", "embed", "embedded", "in_tab", "in-tab"):
        return "tab"
    if cleaned in ("detached", "detach", "external", "window", "detached_window", "separate_window"):
        return "detached"
    if cleaned in _VALID_REMOTE_LAUNCH_MODES:
        return cleaned
    return "tab"


def normalize_nomachine_resize_mode(value: Any) -> str:
    cleaned = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    if cleaned in ("", "default", "scaled", "scale", "fit"):
        return "scaled"
    if cleaned in ("viewport", "scroll", "scrollable", "native"):
        return "viewport"
    if cleaned in _VALID_NOMACHINE_RESIZE_MODES:
        return cleaned
    return "scaled"


def normalize_nomachine_quality(value: Any, *, default: int = 5) -> int:
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


def _normalize_shell_banner_message(value: Any) -> str:
    normalized = str(value).replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(normalized) > 512:
        return normalized[:512].rstrip()
    return normalized


def _normalize_telnet_terminal_type(value: Any) -> str:
    cleaned = str(value).strip()
    if not cleaned:
        return "xterm-256color"
    return cleaned


def _normalize_telnet_connect_timeout(value: Any) -> float:
    return _normalize_float(value, default=10.0, min_value=1.0, max_value=120.0)


def _normalize_serial_baud_rate(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 9600
    if parsed <= 0 or parsed > 4_000_000:
        return 9600
    return parsed


def normalize_serial_data_bits(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 8
    if parsed not in (5, 6, 7, 8):
        return 8
    return parsed


def normalize_serial_parity(value: Any) -> str:
    cleaned = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    if cleaned in ("", "default", "none", "off"):
        return "none"
    if cleaned in ("even",):
        return "even"
    if cleaned in ("odd",):
        return "odd"
    if cleaned in ("mark",):
        return "mark"
    if cleaned in ("space",):
        return "space"
    if cleaned in _VALID_SERIAL_PARITY:
        return cleaned
    return "none"


def normalize_serial_stop_bits(value: Any) -> str:
    cleaned = str(value).strip().lower().replace(" ", "")
    if cleaned in ("", "default", "1", "1.0"):
        return "1"
    if cleaned in ("1.5", "1,5"):
        return "1.5"
    if cleaned in ("2", "2.0"):
        return "2"
    if cleaned in _VALID_SERIAL_STOP_BITS:
        return cleaned
    return "1"


def normalize_serial_flow_control(value: Any) -> str:
    cleaned = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    if cleaned in ("", "default", "none", "off"):
        return "none"
    if cleaned in ("hardware", "rts_cts", "rtscts"):
        return "rtscts"
    if cleaned in ("software", "xon_xoff", "xonxoff"):
        return "xonxoff"
    if cleaned in ("dsr_dtr", "dsrdtr"):
        return "dsrdtr"
    if cleaned in _VALID_SERIAL_FLOW_CONTROL:
        return cleaned
    return "none"


def normalize_serial_terminal_type(value: Any) -> str:
    cleaned = str(value).strip()
    if not cleaned:
        return "auto"
    normalized = cleaned.lower().replace("_", "-")
    return _VALID_SERIAL_TERMINAL_TYPE_ALIASES.get(normalized, cleaned)


def _normalize_sftp_local_folder(value: Any) -> str:
    cleaned = str(value).strip()
    if not cleaned:
        return DEFAULT_SFTP_LOCAL_FOLDER
    return cleaned


def _normalize_sftp_remote_folder(value: Any) -> str:
    cleaned = str(value).strip()
    if not cleaned:
        return DEFAULT_SFTP_REMOTE_FOLDER
    return cleaned


def _normalize_protocol(value: Any) -> Protocol:
    cleaned = str(value).strip().lower()
    if not cleaned:
        return Protocol.SSH
    try:
        return Protocol(cleaned)
    except ValueError:
        # Keep legacy/unknown session entries loadable after protocol removals.
        return Protocol.SSH


def _normalize_port(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if parsed < 0 or parsed > 65535:
        return default
    return parsed


def _normalize_float(value: Any, *, default: float, min_value: float, max_value: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if parsed < min_value or parsed > max_value:
        return default
    return parsed
