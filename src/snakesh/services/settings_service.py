from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import platform
import re
import shlex

from snakesh.core.tool_registry import TOOL_REGISTRY_BY_KEY, normalize_profile_startup_tool_keys
from snakesh.core.theme_presets import (
    CUSTOM_THEME_ID,
    DEFAULT_THEME_ID,
    THEME_COLOR_FIELDS,
    infer_theme_id_from_colors,
    normalize_theme_id,
    theme_colors_for,
    theme_matches_colors,
)
from snakesh.core.paths import data_dir
from snakesh.services.syslog_snmp_monitor import normalize_monitor_profile_config


_WINDOWS_EXTERNAL_TERMINAL_HOSTS = {
    "conhost",
    "conhost.exe",
    "openconsole",
    "openconsole.exe",
    "windowsterminal",
    "windowsterminal.exe",
    "wt",
    "wt.exe",
}

_WINDOWS_DRIVE_PREFIX_RE = re.compile(r"^[a-zA-Z]:[\\/]")
_WINDOWS_UNC_PREFIX_RE = re.compile(r"^\\\\")
_MACOS_PATH_MARKERS = (
    "/Applications/",
    "/Library/",
    "/System/",
    "/Users/",
    "/Volumes/",
    "/opt/homebrew/",
)
_LINUX_PATH_MARKERS = (
    "/bin/",
    "/dev/",
    "/etc/",
    "/home/",
    "/lib/",
    "/media/",
    "/mnt/",
    "/proc/",
    "/run/",
    "/sbin/",
    "/snap/",
    "/srv/",
    "/var/",
)


def _strip_wrapping_quotes(value: str) -> str:
    trimmed = value.strip()
    if len(trimmed) >= 2 and trimmed[0] == trimmed[-1] and trimmed[0] in {'"', "'"}:
        return trimmed[1:-1]
    return trimmed


def _sanitize_local_shell_override(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        return ""

    try:
        parts = shlex.split(cleaned, posix=False)
    except ValueError:
        parts = []
    if not parts:
        return cleaned

    executable = _strip_wrapping_quotes(parts[0])
    normalized = executable.replace("\\", "/").rstrip("/").lower()
    basename = normalized.rsplit("/", 1)[-1].strip()
    if basename in _WINDOWS_EXTERNAL_TERMINAL_HOSTS:
        return ""
    if "/microsoft/windowsapps/" in normalized:
        return ""
    return cleaned


def _first_shell_token(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        return ""
    try:
        parts = shlex.split(cleaned, posix=False)
    except ValueError:
        parts = []
    if parts:
        return _strip_wrapping_quotes(parts[0]).strip()
    return _strip_wrapping_quotes(cleaned.split(maxsplit=1)[0]).strip()


def _sanitize_frame_rect_list(value: object) -> list[int]:
    if not isinstance(value, list) or len(value) != 4:
        return []
    cleaned: list[int] = []
    for raw in value:
        try:
            cleaned.append(int(raw))
        except (TypeError, ValueError):
            return []
    if cleaned[2] <= 0 or cleaned[3] <= 0:
        return []
    return cleaned


def _sanitize_standalone_tool_window_placements(value: object) -> dict[str, dict[str, object]]:
    if not isinstance(value, dict):
        return {}
    cleaned: dict[str, dict[str, object]] = {}
    for raw_key, raw_placement in value.items():
        tool_key = str(raw_key).strip()
        if tool_key not in TOOL_REGISTRY_BY_KEY or not isinstance(raw_placement, dict):
            continue
        geometry_b64 = str(raw_placement.get("geometry_b64", "")).strip()
        screen_name = str(raw_placement.get("screen_name", "")).strip()
        screen_serial = str(raw_placement.get("screen_serial", "")).strip()
        frame_rect = _sanitize_frame_rect_list(raw_placement.get("frame_rect"))
        if not geometry_b64 and not screen_name and not screen_serial and not frame_rect:
            continue
        cleaned[tool_key] = {
            "geometry_b64": geometry_b64,
            "screen_name": screen_name,
            "screen_serial": screen_serial,
            "frame_rect": frame_rect,
        }
    return cleaned


def _looks_like_windows_value(value: str) -> bool:
    for candidate in (value, _first_shell_token(value)):
        cleaned = _strip_wrapping_quotes(candidate.strip())
        if not cleaned:
            continue
        lowered = cleaned.lower()
        if _WINDOWS_DRIVE_PREFIX_RE.match(cleaned) or _WINDOWS_UNC_PREFIX_RE.match(cleaned):
            return True
        if lowered.endswith((".exe", ".bat", ".cmd", ".ps1")):
            return True
        if lowered in _WINDOWS_EXTERNAL_TERMINAL_HOSTS:
            return True
        if lowered in {"cmd", "cmd.exe", "powershell", "powershell.exe", "pwsh", "pwsh.exe"}:
            return True
        if "\\users\\" in lowered or "\\windows\\" in lowered:
            return True
        if "/microsoft/windowsapps/" in lowered:
            return True
    return False


def _looks_like_macos_value(value: str) -> bool:
    for candidate in (value, _first_shell_token(value)):
        cleaned = _strip_wrapping_quotes(candidate.strip())
        if not cleaned:
            continue
        if any(cleaned.startswith(prefix) for prefix in _MACOS_PATH_MARKERS):
            return True
        if ".app/Contents/" in cleaned:
            return True
    return False


def _looks_like_linux_value(value: str) -> bool:
    for candidate in (value, _first_shell_token(value)):
        cleaned = _strip_wrapping_quotes(candidate.strip())
        if not cleaned:
            continue
        if any(cleaned.startswith(prefix) for prefix in _LINUX_PATH_MARKERS):
            return True
    return False


def _looks_like_absolute_or_explicit_command_path(value: str) -> bool:
    token = _first_shell_token(value)
    if not token:
        return False
    cleaned = _strip_wrapping_quotes(token.strip())
    if not cleaned:
        return False
    if _WINDOWS_DRIVE_PREFIX_RE.match(cleaned) or _WINDOWS_UNC_PREFIX_RE.match(cleaned):
        return True
    if cleaned.startswith(("~/", "/", "./", "../")):
        return True
    return "/" in cleaned or "\\" in cleaned


def default_terminal_log_dir() -> str:
    documents = Path.home() / "Documents"
    base = documents if documents.exists() else Path.home()
    return str((base / "SnakeSh Logs").expanduser())


CLASSIC_TERMINAL_BG = "#000000"
CLASSIC_TERMINAL_FG = "#e5e5e5"


def resolve_terminal_default_colors(
    settings: "AppSettings",
    *,
    bg_override: str = "",
    fg_override: str = "",
) -> tuple[str, str]:
    if bool(settings.terminal_classic_default_colors):
        background = CLASSIC_TERMINAL_BG
        foreground = CLASSIC_TERMINAL_FG
    else:
        background = settings.terminal_bg
        foreground = settings.terminal_fg

    normalized_bg = bg_override.strip()
    normalized_fg = fg_override.strip()
    if normalized_bg:
        background = normalized_bg
    if normalized_fg:
        foreground = normalized_fg
    return background, foreground


@dataclass(slots=True)
class AppSettings:
    theme_name: str = DEFAULT_THEME_ID
    app_bg_start: str = "#0e1116"
    app_bg_end: str = "#141a22"
    text_color: str = "#e7edf5"
    field_bg: str = "#1a222d"
    field_border: str = "#3b4d66"
    accent_color: str = "#2d6cdf"
    accent_hover: str = "#3b7be9"
    accent_pressed: str = "#2459b8"
    terminal_bg: str = "#0f151e"
    terminal_fg: str = "#93c5fd"
    terminal_classic_default_colors: bool = False
    terminal_font_family: str = "Courier New"
    terminal_font_pt: int = 10
    terminal_scrollback_lines: int = 5000
    terminal_log_dir: str = field(default_factory=default_terminal_log_dir)
    global_session_logging_enabled: bool = False
    session_log_cleanup_enabled: bool = True
    session_log_retention_days: int = 7
    web_server_log_cleanup_enabled: bool = True
    web_server_log_retention_days: int = 7
    crash_logging_enabled: bool = False
    terminal_cursor_blink: bool = False
    terminal_bell_enabled: bool = False
    terminal_visual_bell_enabled: bool = False
    local_shell_command_override: str = ""
    local_shell_start_dir_mode: str = "home"
    local_shell_custom_start_dir: str = ""
    tab_active_bg: str = "#2d6cdf"
    tab_active_fg: str = "#f8fbff"
    tab_inactive_bg: str = "#1c2632"
    tab_inactive_fg: str = "#a8b3c2"
    secrets_backend: str = "keyring"
    onepassword_vault: str = "SnakeSh"
    onepassword_account: str = ""
    onepassword_cli_path: str = "op"
    bitwarden_cli_path: str = "bw"
    keeper_cli_path: str = "keeper"
    keeper_user: str = ""
    keeper_server: str = ""
    keeper_folder: str = "SnakeSh"
    keepass_cli_path: str = "keepassxc-cli"
    keepass_database_path: str = ""
    keepass_password_env: str = "KEEPASSXC_PASSWORD"
    keepass_key_file_path: str = ""
    keepass_group: str = "SnakeSh"
    vault_addr: str = ""
    vault_mount: str = "secret"
    vault_token_env: str = "VAULT_TOKEN"
    vault_namespace: str = ""
    vault_skip_tls_verify: bool = False
    rdp_trusted_certificate_hosts: list[str] = field(default_factory=list)
    warn_before_file_delete: bool = True
    warn_before_file_overwrite: bool = True
    warn_before_closing_active_tab: bool = True
    master_password_enabled: bool = False
    master_password_tools_enabled: bool = False
    master_password_salt_b64: str = ""
    master_password_hash_b64: str = ""
    linux_desktop_prompt_dismissed: bool = False
    linux_desktop_last_update_prompt_version: str = ""
    workspace_profiles: list[dict[str, object]] = field(default_factory=list)
    fast_commands: list[dict[str, str]] = field(default_factory=list)
    web_server_profiles: list[dict[str, object]] = field(default_factory=list)
    syslog_snmp_monitor_profiles: list[dict[str, object]] = field(default_factory=list)
    default_workspace_profile_id: str = ""
    last_web_server_profile_id: str = ""
    last_syslog_snmp_monitor_profile_id: str = ""
    web_server_dialog_splitter_b64: str = ""
    syslog_snmp_monitor_dialog_splitter_b64: str = ""
    session_list_visibility_mode: str = "shown"
    session_list_window_geometry_b64: str = ""
    session_list_window_screen_name: str = ""
    session_list_window_screen_serial: str = ""
    session_list_window_frame_rect: list[int] = field(default_factory=list)
    main_window_geometry_b64: str = ""
    main_window_screen_name: str = ""
    main_window_screen_serial: str = ""
    main_window_frame_rect: list[int] = field(default_factory=list)
    main_window_splitter_b64: str = ""
    main_window_fullscreen_shortcut: str = "F11"
    main_window_hide_controls_in_fullscreen: bool = False
    standalone_tool_window_placements: dict[str, dict[str, object]] = field(default_factory=dict)
    resource_monitor_show_offline_adapters: bool = False
    resource_monitor_zoom_percent: int = 100
    resource_monitor_sample_refresh_ms: int = 1000
    resource_monitor_process_refresh_ms: int = 4000
    resource_monitor_history_minutes: int = 10
    session_tree_expanded_folders: list[str] = field(default_factory=list)
    password_generator_length: int = 20
    password_generator_count: int = 5
    password_generator_complexity: str = "Strong"
    password_generator_include_lower: bool = True
    password_generator_include_upper: bool = True
    password_generator_include_digits: bool = True
    password_generator_include_symbols: bool = True
    password_generator_include_characters: str = ""
    password_generator_exclude_characters: str = ""

    @classmethod
    def defaults(cls) -> "AppSettings":
        settings = cls()
        preset_colors = theme_colors_for(DEFAULT_THEME_ID) or {}
        settings.theme_name = DEFAULT_THEME_ID
        for key in THEME_COLOR_FIELDS:
            color = preset_colors.get(key)
            if isinstance(color, str) and color.strip():
                setattr(settings, key, color.strip())
        return settings

    def to_dict(self) -> dict[str, object]:
        return {
            "theme_name": self.theme_name,
            "app_bg_start": self.app_bg_start,
            "app_bg_end": self.app_bg_end,
            "text_color": self.text_color,
            "field_bg": self.field_bg,
            "field_border": self.field_border,
            "accent_color": self.accent_color,
            "accent_hover": self.accent_hover,
            "accent_pressed": self.accent_pressed,
            "terminal_bg": self.terminal_bg,
            "terminal_fg": self.terminal_fg,
            "terminal_classic_default_colors": self.terminal_classic_default_colors,
            "terminal_font_family": self.terminal_font_family,
            "terminal_font_pt": self.terminal_font_pt,
            "terminal_scrollback_lines": self.terminal_scrollback_lines,
            "terminal_log_dir": self.terminal_log_dir,
            "global_session_logging_enabled": self.global_session_logging_enabled,
            "session_log_cleanup_enabled": self.session_log_cleanup_enabled,
            "session_log_retention_days": self.session_log_retention_days,
            "web_server_log_cleanup_enabled": self.web_server_log_cleanup_enabled,
            "web_server_log_retention_days": self.web_server_log_retention_days,
            "crash_logging_enabled": self.crash_logging_enabled,
            "terminal_cursor_blink": self.terminal_cursor_blink,
            "terminal_bell_enabled": self.terminal_bell_enabled,
            "terminal_visual_bell_enabled": self.terminal_visual_bell_enabled,
            "local_shell_command_override": self.local_shell_command_override,
            "local_shell_start_dir_mode": self.local_shell_start_dir_mode,
            "local_shell_custom_start_dir": self.local_shell_custom_start_dir,
            "tab_active_bg": self.tab_active_bg,
            "tab_active_fg": self.tab_active_fg,
            "tab_inactive_bg": self.tab_inactive_bg,
            "tab_inactive_fg": self.tab_inactive_fg,
            "secrets_backend": self.secrets_backend,
            "onepassword_vault": self.onepassword_vault,
            "onepassword_account": self.onepassword_account,
            "onepassword_cli_path": self.onepassword_cli_path,
            "bitwarden_cli_path": self.bitwarden_cli_path,
            "keeper_cli_path": self.keeper_cli_path,
            "keeper_user": self.keeper_user,
            "keeper_server": self.keeper_server,
            "keeper_folder": self.keeper_folder,
            "keepass_cli_path": self.keepass_cli_path,
            "keepass_database_path": self.keepass_database_path,
            "keepass_password_env": self.keepass_password_env,
            "keepass_key_file_path": self.keepass_key_file_path,
            "keepass_group": self.keepass_group,
            "vault_addr": self.vault_addr,
            "vault_mount": self.vault_mount,
            "vault_token_env": self.vault_token_env,
            "vault_namespace": self.vault_namespace,
            "vault_skip_tls_verify": self.vault_skip_tls_verify,
            "rdp_trusted_certificate_hosts": list(self.rdp_trusted_certificate_hosts),
            "warn_before_file_delete": self.warn_before_file_delete,
            "warn_before_file_overwrite": self.warn_before_file_overwrite,
            "warn_before_closing_active_tab": self.warn_before_closing_active_tab,
            "master_password_enabled": self.master_password_enabled,
            "master_password_tools_enabled": self.master_password_tools_enabled,
            "master_password_salt_b64": self.master_password_salt_b64,
            "master_password_hash_b64": self.master_password_hash_b64,
            "linux_desktop_prompt_dismissed": self.linux_desktop_prompt_dismissed,
            "linux_desktop_last_update_prompt_version": self.linux_desktop_last_update_prompt_version,
            "workspace_profiles": list(self.workspace_profiles),
            "fast_commands": list(self.fast_commands),
            "web_server_profiles": list(self.web_server_profiles),
            "syslog_snmp_monitor_profiles": list(self.syslog_snmp_monitor_profiles),
            "default_workspace_profile_id": self.default_workspace_profile_id,
            "last_web_server_profile_id": self.last_web_server_profile_id,
            "last_syslog_snmp_monitor_profile_id": self.last_syslog_snmp_monitor_profile_id,
            "web_server_dialog_splitter_b64": self.web_server_dialog_splitter_b64,
            "syslog_snmp_monitor_dialog_splitter_b64": self.syslog_snmp_monitor_dialog_splitter_b64,
            "session_list_visibility_mode": self.session_list_visibility_mode,
            "session_list_window_geometry_b64": self.session_list_window_geometry_b64,
            "session_list_window_screen_name": self.session_list_window_screen_name,
            "session_list_window_screen_serial": self.session_list_window_screen_serial,
            "session_list_window_frame_rect": list(self.session_list_window_frame_rect),
            "main_window_geometry_b64": self.main_window_geometry_b64,
            "main_window_screen_name": self.main_window_screen_name,
            "main_window_screen_serial": self.main_window_screen_serial,
            "main_window_frame_rect": list(self.main_window_frame_rect),
            "main_window_splitter_b64": self.main_window_splitter_b64,
            "main_window_fullscreen_shortcut": self.main_window_fullscreen_shortcut,
            "main_window_hide_controls_in_fullscreen": self.main_window_hide_controls_in_fullscreen,
            "standalone_tool_window_placements": dict(self.standalone_tool_window_placements),
            "resource_monitor_show_offline_adapters": self.resource_monitor_show_offline_adapters,
            "resource_monitor_zoom_percent": self.resource_monitor_zoom_percent,
            "resource_monitor_sample_refresh_ms": self.resource_monitor_sample_refresh_ms,
            "resource_monitor_process_refresh_ms": self.resource_monitor_process_refresh_ms,
            "resource_monitor_history_minutes": self.resource_monitor_history_minutes,
            "session_tree_expanded_folders": list(self.session_tree_expanded_folders),
            "password_generator_length": self.password_generator_length,
            "password_generator_count": self.password_generator_count,
            "password_generator_complexity": self.password_generator_complexity,
            "password_generator_include_lower": self.password_generator_include_lower,
            "password_generator_include_upper": self.password_generator_include_upper,
            "password_generator_include_digits": self.password_generator_include_digits,
            "password_generator_include_symbols": self.password_generator_include_symbols,
            "password_generator_include_characters": self.password_generator_include_characters,
            "password_generator_exclude_characters": self.password_generator_exclude_characters,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, object]) -> "AppSettings":
        defaults = cls.defaults()
        payload = defaults.to_dict()
        payload.update({k: v for k, v in raw.items() if k in payload})
        if "theme_name" in raw:
            theme_name = normalize_theme_id(str(payload["theme_name"]))
        else:
            theme_name = infer_theme_id_from_colors(
                {
                    key: str(payload[key])
                    for key in THEME_COLOR_FIELDS
                }
            )
        return cls(
            theme_name=theme_name,
            app_bg_start=str(payload["app_bg_start"]),
            app_bg_end=str(payload["app_bg_end"]),
            text_color=str(payload["text_color"]),
            field_bg=str(payload["field_bg"]),
            field_border=str(payload["field_border"]),
            accent_color=str(payload["accent_color"]),
            accent_hover=str(payload["accent_hover"]),
            accent_pressed=str(payload["accent_pressed"]),
            terminal_bg=str(payload["terminal_bg"]),
            terminal_fg=str(payload["terminal_fg"]),
            terminal_classic_default_colors=bool(payload["terminal_classic_default_colors"]),
            terminal_font_family=str(payload["terminal_font_family"]),
            terminal_font_pt=int(payload["terminal_font_pt"]),
            terminal_scrollback_lines=int(payload["terminal_scrollback_lines"]),
            terminal_log_dir=str(payload["terminal_log_dir"]),
            global_session_logging_enabled=bool(payload["global_session_logging_enabled"]),
            session_log_cleanup_enabled=bool(payload["session_log_cleanup_enabled"]),
            session_log_retention_days=int(payload["session_log_retention_days"]),
            web_server_log_cleanup_enabled=bool(payload["web_server_log_cleanup_enabled"]),
            web_server_log_retention_days=int(payload["web_server_log_retention_days"]),
            crash_logging_enabled=bool(payload["crash_logging_enabled"]),
            terminal_cursor_blink=bool(payload["terminal_cursor_blink"]),
            terminal_bell_enabled=bool(payload["terminal_bell_enabled"]),
            terminal_visual_bell_enabled=bool(payload["terminal_visual_bell_enabled"]),
            local_shell_command_override=str(payload["local_shell_command_override"]),
            local_shell_start_dir_mode=str(payload["local_shell_start_dir_mode"]),
            local_shell_custom_start_dir=str(payload["local_shell_custom_start_dir"]),
            tab_active_bg=str(payload["tab_active_bg"]),
            tab_active_fg=str(payload["tab_active_fg"]),
            tab_inactive_bg=str(payload["tab_inactive_bg"]),
            tab_inactive_fg=str(payload["tab_inactive_fg"]),
            secrets_backend=str(payload["secrets_backend"]),
            onepassword_vault=str(payload["onepassword_vault"]),
            onepassword_account=str(payload["onepassword_account"]),
            onepassword_cli_path=str(payload["onepassword_cli_path"]),
            bitwarden_cli_path=str(payload["bitwarden_cli_path"]),
            keeper_cli_path=str(payload["keeper_cli_path"]),
            keeper_user=str(payload["keeper_user"]),
            keeper_server=str(payload["keeper_server"]),
            keeper_folder=str(payload["keeper_folder"]),
            keepass_cli_path=str(payload["keepass_cli_path"]),
            keepass_database_path=str(payload["keepass_database_path"]),
            keepass_password_env=str(payload["keepass_password_env"]),
            keepass_key_file_path=str(payload["keepass_key_file_path"]),
            keepass_group=str(payload["keepass_group"]),
            vault_addr=str(payload["vault_addr"]),
            vault_mount=str(payload["vault_mount"]),
            vault_token_env=str(payload["vault_token_env"]),
            vault_namespace=str(payload["vault_namespace"]),
            vault_skip_tls_verify=bool(payload["vault_skip_tls_verify"]),
            rdp_trusted_certificate_hosts=[
                str(item).strip()
                for item in payload.get("rdp_trusted_certificate_hosts", [])
                if isinstance(item, str) and str(item).strip()
            ],
            warn_before_file_delete=bool(payload["warn_before_file_delete"]),
            warn_before_file_overwrite=bool(payload["warn_before_file_overwrite"]),
            warn_before_closing_active_tab=bool(payload["warn_before_closing_active_tab"]),
            master_password_enabled=bool(payload["master_password_enabled"]),
            master_password_tools_enabled=bool(payload["master_password_tools_enabled"]),
            master_password_salt_b64=str(payload["master_password_salt_b64"]),
            master_password_hash_b64=str(payload["master_password_hash_b64"]),
            linux_desktop_prompt_dismissed=bool(payload["linux_desktop_prompt_dismissed"]),
            linux_desktop_last_update_prompt_version=str(payload["linux_desktop_last_update_prompt_version"]),
            workspace_profiles=[
                dict(item)
                for item in payload.get("workspace_profiles", [])
                if isinstance(item, dict)
            ],
            fast_commands=[
                dict(item)
                for item in payload.get("fast_commands", [])
                if isinstance(item, dict)
            ],
            web_server_profiles=[
                dict(item)
                for item in payload.get("web_server_profiles", [])
                if isinstance(item, dict)
            ],
            syslog_snmp_monitor_profiles=[
                dict(item)
                for item in payload.get("syslog_snmp_monitor_profiles", [])
                if isinstance(item, dict)
            ],
            default_workspace_profile_id=str(payload["default_workspace_profile_id"]),
            last_web_server_profile_id=str(payload["last_web_server_profile_id"]),
            last_syslog_snmp_monitor_profile_id=str(payload["last_syslog_snmp_monitor_profile_id"]),
            web_server_dialog_splitter_b64=str(payload["web_server_dialog_splitter_b64"]),
            syslog_snmp_monitor_dialog_splitter_b64=str(payload["syslog_snmp_monitor_dialog_splitter_b64"]),
            session_list_visibility_mode=str(payload["session_list_visibility_mode"]),
            session_list_window_geometry_b64=str(payload["session_list_window_geometry_b64"]),
            session_list_window_screen_name=str(payload["session_list_window_screen_name"]),
            session_list_window_screen_serial=str(payload["session_list_window_screen_serial"]),
            session_list_window_frame_rect=_sanitize_frame_rect_list(payload.get("session_list_window_frame_rect")),
            main_window_geometry_b64=str(payload["main_window_geometry_b64"]),
            main_window_screen_name=str(payload["main_window_screen_name"]),
            main_window_screen_serial=str(payload["main_window_screen_serial"]),
            main_window_frame_rect=_sanitize_frame_rect_list(payload.get("main_window_frame_rect")),
            main_window_splitter_b64=str(payload["main_window_splitter_b64"]),
            main_window_fullscreen_shortcut=str(payload["main_window_fullscreen_shortcut"]),
            main_window_hide_controls_in_fullscreen=bool(payload["main_window_hide_controls_in_fullscreen"]),
            standalone_tool_window_placements=_sanitize_standalone_tool_window_placements(
                payload.get("standalone_tool_window_placements")
            ),
            resource_monitor_show_offline_adapters=bool(payload["resource_monitor_show_offline_adapters"]),
            resource_monitor_zoom_percent=int(payload["resource_monitor_zoom_percent"]),
            resource_monitor_sample_refresh_ms=int(payload["resource_monitor_sample_refresh_ms"]),
            resource_monitor_process_refresh_ms=int(payload["resource_monitor_process_refresh_ms"]),
            resource_monitor_history_minutes=int(payload["resource_monitor_history_minutes"]),
            session_tree_expanded_folders=[
                str(item).strip()
                for item in payload.get("session_tree_expanded_folders", [])
                if isinstance(item, str) and str(item).strip()
            ],
            password_generator_length=int(payload["password_generator_length"]),
            password_generator_count=int(payload["password_generator_count"]),
            password_generator_complexity=str(payload["password_generator_complexity"]),
            password_generator_include_lower=bool(payload["password_generator_include_lower"]),
            password_generator_include_upper=bool(payload["password_generator_include_upper"]),
            password_generator_include_digits=bool(payload["password_generator_include_digits"]),
            password_generator_include_symbols=bool(payload["password_generator_include_symbols"]),
            password_generator_include_characters=str(payload["password_generator_include_characters"]),
            password_generator_exclude_characters=str(payload["password_generator_exclude_characters"]),
        )


class SettingsService:
    def __init__(self) -> None:
        self._path = data_dir() / "settings.json"
        self._last_saved_payload: str | None = None

    def load(self) -> AppSettings:
        if not self._path.exists():
            self._last_saved_payload = None
            return AppSettings.defaults()
        try:
            payload = self._path.read_text(encoding="utf-8")
            raw = json.loads(payload)
            if not isinstance(raw, dict):
                self._last_saved_payload = None
                return AppSettings.defaults()
            self._last_saved_payload = payload
            return self.sanitize_for_current_platform(AppSettings.from_dict(raw))
        except Exception:
            self._last_saved_payload = None
            return AppSettings.defaults()

    def save(self, settings: AppSettings) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        sanitized = self.sanitize_settings(settings)
        payload = json.dumps(sanitized.to_dict(), indent=2)
        if payload == self._last_saved_payload:
            return
        if self._last_saved_payload is None and self._path.exists():
            try:
                existing = self._path.read_text(encoding="utf-8")
            except Exception:
                existing = None
            else:
                self._last_saved_payload = existing
                if payload == existing:
                    return
        self._path.write_text(payload, encoding="utf-8")
        self._last_saved_payload = payload

    def reset(self) -> AppSettings:
        settings = AppSettings.defaults()
        self.save(settings)
        return settings

    @classmethod
    def sanitize_settings(cls, settings: AppSettings) -> AppSettings:
        return cls._sanitize(AppSettings.from_dict(settings.to_dict()))

    @classmethod
    def sanitize_imported_settings(
        cls,
        settings: AppSettings,
        *,
        source_platform: str | None = None,
    ) -> AppSettings:
        return cls.sanitize_for_current_platform(settings, source_platform=source_platform)

    @classmethod
    def sanitize_for_current_platform(
        cls,
        settings: AppSettings,
        *,
        source_platform: str | None = None,
    ) -> AppSettings:
        resolved_source_platform = cls.resolve_source_platform(settings, source_platform=source_platform)
        sanitized = cls.sanitize_settings(settings)
        target_platform = cls.current_platform_name()
        if resolved_source_platform and resolved_source_platform != target_platform:
            cls._sanitize_cross_platform_state(
                sanitized,
                source_platform=resolved_source_platform,
                target_platform=target_platform,
            )
        return sanitized

    @classmethod
    def current_platform_name(cls) -> str:
        return cls.normalize_platform_name(platform.system()) or "linux"

    @classmethod
    def normalize_platform_name(cls, raw: str | None) -> str | None:
        if raw is None:
            return None
        normalized = str(raw).strip().lower()
        if not normalized:
            return None
        aliases = {
            "darwin": "macos",
            "gnu/linux": "linux",
            "linux": "linux",
            "mac": "macos",
            "macos": "macos",
            "osx": "macos",
            "windows": "windows",
            "win32": "windows",
        }
        return aliases.get(normalized)

    @classmethod
    def resolve_source_platform(
        cls,
        settings: AppSettings,
        *,
        source_platform: str | None = None,
    ) -> str | None:
        normalized = cls.normalize_platform_name(source_platform)
        if normalized:
            return normalized
        return cls._infer_platform_from_settings(settings)

    @staticmethod
    def _sanitize(settings: AppSettings) -> AppSettings:
        settings.theme_name = normalize_theme_id(settings.theme_name)
        if settings.theme_name != CUSTOM_THEME_ID:
            if not theme_matches_colors(
                settings.theme_name,
                {
                    key: str(getattr(settings, key))
                    for key in THEME_COLOR_FIELDS
                },
            ):
                settings.theme_name = CUSTOM_THEME_ID
        if settings.terminal_font_family.strip().lower() == "fixedsys":
            settings.terminal_font_family = "Courier New"
            settings.terminal_font_pt = 10
        settings.terminal_font_pt = max(8, min(20, settings.terminal_font_pt))
        settings.terminal_scrollback_lines = max(100, min(50000, settings.terminal_scrollback_lines))
        log_dir = settings.terminal_log_dir.strip()
        settings.terminal_log_dir = str(Path(log_dir).expanduser()) if log_dir else default_terminal_log_dir()
        settings.global_session_logging_enabled = bool(settings.global_session_logging_enabled)
        settings.session_log_cleanup_enabled = bool(settings.session_log_cleanup_enabled)
        settings.session_log_retention_days = max(1, min(3650, int(settings.session_log_retention_days)))
        settings.web_server_log_cleanup_enabled = bool(settings.web_server_log_cleanup_enabled)
        settings.web_server_log_retention_days = max(1, min(3650, int(settings.web_server_log_retention_days)))
        settings.crash_logging_enabled = bool(settings.crash_logging_enabled)
        settings.terminal_cursor_blink = bool(settings.terminal_cursor_blink)
        settings.terminal_bell_enabled = bool(settings.terminal_bell_enabled)
        settings.terminal_visual_bell_enabled = bool(settings.terminal_visual_bell_enabled)
        settings.terminal_classic_default_colors = bool(settings.terminal_classic_default_colors)
        settings.local_shell_command_override = _sanitize_local_shell_override(settings.local_shell_command_override)
        local_shell_mode = settings.local_shell_start_dir_mode.strip().lower()
        if local_shell_mode not in {"home", "cwd", "custom"}:
            local_shell_mode = "home"
        local_shell_custom_dir = settings.local_shell_custom_start_dir.strip()
        settings.local_shell_custom_start_dir = (
            str(Path(local_shell_custom_dir).expanduser()) if local_shell_custom_dir else ""
        )
        if local_shell_mode == "custom" and not settings.local_shell_custom_start_dir:
            local_shell_mode = "home"
        settings.local_shell_start_dir_mode = local_shell_mode
        settings.secrets_backend = settings.secrets_backend.strip().lower() or "keyring"
        if settings.secrets_backend not in {"keyring", "1password", "bitwarden", "keeper", "keepass", "vault"}:
            settings.secrets_backend = "keyring"
        settings.onepassword_vault = settings.onepassword_vault.strip() or "SnakeSh"
        settings.onepassword_cli_path = settings.onepassword_cli_path.strip() or "op"
        settings.bitwarden_cli_path = settings.bitwarden_cli_path.strip() or "bw"
        settings.keeper_cli_path = settings.keeper_cli_path.strip() or "keeper"
        settings.keeper_user = settings.keeper_user.strip()
        settings.keeper_server = settings.keeper_server.strip().upper()
        settings.keeper_folder = settings.keeper_folder.strip().strip("/") or "SnakeSh"
        settings.keepass_cli_path = settings.keepass_cli_path.strip() or "keepassxc-cli"
        keepass_database_path = settings.keepass_database_path.strip()
        settings.keepass_database_path = str(Path(keepass_database_path).expanduser()) if keepass_database_path else ""
        settings.keepass_password_env = settings.keepass_password_env.strip() or "KEEPASSXC_PASSWORD"
        keepass_key_file_path = settings.keepass_key_file_path.strip()
        settings.keepass_key_file_path = str(Path(keepass_key_file_path).expanduser()) if keepass_key_file_path else ""
        settings.keepass_group = settings.keepass_group.strip().strip("/") or "SnakeSh"
        settings.vault_mount = settings.vault_mount.strip().strip("/") or "secret"
        settings.vault_token_env = settings.vault_token_env.strip() or "VAULT_TOKEN"
        settings.linux_desktop_last_update_prompt_version = settings.linux_desktop_last_update_prompt_version.strip()
        settings.warn_before_file_delete = bool(settings.warn_before_file_delete)
        settings.warn_before_file_overwrite = bool(settings.warn_before_file_overwrite)
        settings.warn_before_closing_active_tab = bool(settings.warn_before_closing_active_tab)
        normalized_profiles: list[dict[str, object]] = []
        seen_profile_ids: set[str] = set()
        for raw_profile in settings.workspace_profiles:
            if not isinstance(raw_profile, dict):
                continue
            profile_id = str(raw_profile.get("id", "")).strip()
            name = str(raw_profile.get("name", "")).strip()
            snapshot = raw_profile.get("snapshot")
            if not profile_id or not name or not isinstance(snapshot, dict):
                continue
            if profile_id in seen_profile_ids:
                continue
            seen_profile_ids.add(profile_id)
            normalized_profile: dict[str, object] = {
                "id": profile_id,
                "name": name,
                "snapshot": dict(snapshot),
            }
            startup_tools = normalize_profile_startup_tool_keys(raw_profile.get("startup_tools", []))
            if startup_tools:
                normalized_profile["startup_tools"] = startup_tools
            normalized_profiles.append(normalized_profile)
        settings.workspace_profiles = normalized_profiles
        normalized_fast_commands: list[dict[str, str]] = []
        seen_command_ids: set[str] = set()
        for raw_command in settings.fast_commands:
            if not isinstance(raw_command, dict):
                continue
            command_id = str(raw_command.get("id", "")).strip()
            name = str(raw_command.get("name", "")).strip()
            command = str(raw_command.get("command", ""))
            if not command_id or not name or not command.strip():
                continue
            if command_id in seen_command_ids:
                continue
            seen_command_ids.add(command_id)
            normalized_fast_commands.append(
                {
                    "id": command_id,
                    "name": name,
                    "command": command,
                }
            )
        settings.fast_commands = normalized_fast_commands
        normalized_web_server_profiles: list[dict[str, object]] = []
        seen_web_server_profile_ids: set[str] = set()
        for raw_profile in settings.web_server_profiles:
            if not isinstance(raw_profile, dict):
                continue
            profile_id = str(raw_profile.get("id", "")).strip()
            name = str(raw_profile.get("name", "")).strip()
            config = raw_profile.get("config")
            if not profile_id or not name or not isinstance(config, dict):
                continue
            if profile_id in seen_web_server_profile_ids:
                continue
            seen_web_server_profile_ids.add(profile_id)
            normalized_web_server_profiles.append(
                {
                    "id": profile_id,
                    "name": name,
                    "config": SettingsService._sanitize_web_server_profile_config(config),
                }
            )
        settings.web_server_profiles = normalized_web_server_profiles
        normalized_monitor_profiles: list[dict[str, object]] = []
        seen_monitor_profile_ids: set[str] = set()
        for raw_profile in settings.syslog_snmp_monitor_profiles:
            if not isinstance(raw_profile, dict):
                continue
            profile_id = str(raw_profile.get("id", "")).strip()
            name = str(raw_profile.get("name", "")).strip()
            config = raw_profile.get("config")
            if not profile_id or not name or not isinstance(config, dict):
                continue
            if profile_id in seen_monitor_profile_ids:
                continue
            seen_monitor_profile_ids.add(profile_id)
            normalized_monitor_profiles.append(
                {
                    "id": profile_id,
                    "name": name,
                    "config": SettingsService._sanitize_syslog_snmp_monitor_profile_config(config),
                }
            )
        settings.syslog_snmp_monitor_profiles = normalized_monitor_profiles
        default_profile_id = settings.default_workspace_profile_id.strip()
        if default_profile_id and default_profile_id not in seen_profile_ids:
            default_profile_id = ""
        settings.default_workspace_profile_id = default_profile_id
        last_web_server_profile_id = settings.last_web_server_profile_id.strip()
        if last_web_server_profile_id and last_web_server_profile_id not in seen_web_server_profile_ids:
            last_web_server_profile_id = ""
        settings.last_web_server_profile_id = last_web_server_profile_id
        last_monitor_profile_id = settings.last_syslog_snmp_monitor_profile_id.strip()
        if last_monitor_profile_id and last_monitor_profile_id not in seen_monitor_profile_ids:
            last_monitor_profile_id = ""
        settings.last_syslog_snmp_monitor_profile_id = last_monitor_profile_id
        settings.web_server_dialog_splitter_b64 = settings.web_server_dialog_splitter_b64.strip()
        settings.syslog_snmp_monitor_dialog_splitter_b64 = settings.syslog_snmp_monitor_dialog_splitter_b64.strip()
        visibility_mode = settings.session_list_visibility_mode.strip().lower()
        if visibility_mode == "unhide":
            visibility_mode = "shown"
        elif visibility_mode == "hide":
            visibility_mode = "auto"
        if visibility_mode not in {"shown", "auto", "float"}:
            visibility_mode = "shown"
        settings.session_list_visibility_mode = visibility_mode
        normalized_rdp_hosts = sorted(
            {
                token
                for token in (
                    str(item).strip().lower()
                    for item in settings.rdp_trusted_certificate_hosts
                    if isinstance(item, str)
                )
                if token
            }
        )
        settings.rdp_trusted_certificate_hosts = normalized_rdp_hosts
        settings.master_password_salt_b64 = settings.master_password_salt_b64.strip()
        settings.master_password_hash_b64 = settings.master_password_hash_b64.strip()
        settings.master_password_tools_enabled = bool(settings.master_password_tools_enabled)
        if not settings.master_password_salt_b64 or not settings.master_password_hash_b64:
            settings.master_password_salt_b64 = ""
            settings.master_password_hash_b64 = ""
            settings.master_password_enabled = False
            settings.master_password_tools_enabled = False
        settings.session_list_window_geometry_b64 = settings.session_list_window_geometry_b64.strip()
        settings.session_list_window_screen_name = settings.session_list_window_screen_name.strip()
        settings.session_list_window_screen_serial = settings.session_list_window_screen_serial.strip()
        settings.session_list_window_frame_rect = _sanitize_frame_rect_list(settings.session_list_window_frame_rect)
        settings.main_window_geometry_b64 = settings.main_window_geometry_b64.strip()
        settings.main_window_screen_name = settings.main_window_screen_name.strip()
        settings.main_window_screen_serial = settings.main_window_screen_serial.strip()
        settings.main_window_frame_rect = _sanitize_frame_rect_list(settings.main_window_frame_rect)
        settings.main_window_splitter_b64 = settings.main_window_splitter_b64.strip()
        settings.main_window_fullscreen_shortcut = (
            settings.main_window_fullscreen_shortcut.strip() or AppSettings.defaults().main_window_fullscreen_shortcut
        )
        settings.main_window_hide_controls_in_fullscreen = bool(settings.main_window_hide_controls_in_fullscreen)
        settings.standalone_tool_window_placements = _sanitize_standalone_tool_window_placements(
            settings.standalone_tool_window_placements
        )
        settings.resource_monitor_show_offline_adapters = bool(settings.resource_monitor_show_offline_adapters)
        settings.resource_monitor_zoom_percent = max(75, min(150, int(settings.resource_monitor_zoom_percent)))
        settings.resource_monitor_sample_refresh_ms = max(500, min(10000, int(settings.resource_monitor_sample_refresh_ms)))
        settings.resource_monitor_process_refresh_ms = max(2000, min(30000, int(settings.resource_monitor_process_refresh_ms)))
        settings.resource_monitor_history_minutes = max(2, min(60, int(settings.resource_monitor_history_minutes)))
        normalized_folders: set[str] = set()
        for folder in settings.session_tree_expanded_folders:
            cleaned = folder.replace("\\", "/").strip("/")
            if not cleaned:
                continue
            parts = [part.strip() for part in cleaned.split("/") if part.strip()]
            if not parts:
                continue
            normalized_folders.add("/".join(parts))
        settings.session_tree_expanded_folders = sorted(
            normalized_folders,
            key=lambda item: (item.count("/"), item.lower()),
        )
        settings.password_generator_length = max(8, min(256, int(settings.password_generator_length)))
        settings.password_generator_count = max(1, min(200, int(settings.password_generator_count)))
        complexity = settings.password_generator_complexity.strip().lower()
        allowed_complexities = {
            "balanced": "Balanced",
            "strong": "Strong",
            "very strong": "Very Strong",
            "maximum": "Maximum",
        }
        settings.password_generator_complexity = allowed_complexities.get(complexity, "Strong")
        settings.password_generator_include_lower = bool(settings.password_generator_include_lower)
        settings.password_generator_include_upper = bool(settings.password_generator_include_upper)
        settings.password_generator_include_digits = bool(settings.password_generator_include_digits)
        settings.password_generator_include_symbols = bool(settings.password_generator_include_symbols)
        settings.password_generator_include_characters = str(settings.password_generator_include_characters)
        settings.password_generator_exclude_characters = str(settings.password_generator_exclude_characters)
        return settings

    @classmethod
    def _infer_platform_from_settings(cls, settings: AppSettings) -> str | None:
        scores = {
            "windows": 0,
            "linux": 0,
            "macos": 0,
        }
        candidate_values = (
            settings.terminal_log_dir,
            settings.local_shell_command_override,
            settings.local_shell_custom_start_dir,
            settings.onepassword_cli_path,
            settings.bitwarden_cli_path,
            settings.keeper_cli_path,
            settings.keepass_cli_path,
            settings.keepass_database_path,
            settings.keepass_key_file_path,
        )
        for value in candidate_values:
            if _looks_like_windows_value(value):
                scores["windows"] += 1
            if _looks_like_linux_value(value):
                scores["linux"] += 1
            if _looks_like_macos_value(value):
                scores["macos"] += 1
        winning_platform, winning_score = max(scores.items(), key=lambda item: item[1])
        if winning_score <= 0:
            return None
        winners = [name for name, score in scores.items() if score == winning_score]
        if len(winners) != 1:
            return None
        return winning_platform

    @classmethod
    def _sanitize_cross_platform_state(
        cls,
        settings: AppSettings,
        *,
        source_platform: str,
        target_platform: str,
    ) -> None:
        defaults = AppSettings.defaults()
        settings.main_window_geometry_b64 = ""
        settings.main_window_screen_name = ""
        settings.main_window_screen_serial = ""
        settings.main_window_frame_rect = []
        settings.main_window_splitter_b64 = ""
        settings.session_list_window_geometry_b64 = ""
        settings.session_list_window_screen_name = ""
        settings.session_list_window_screen_serial = ""
        settings.session_list_window_frame_rect = []
        settings.standalone_tool_window_placements = {}
        settings.web_server_dialog_splitter_b64 = ""
        settings.syslog_snmp_monitor_dialog_splitter_b64 = ""
        settings.terminal_log_dir = defaults.terminal_log_dir
        settings.local_shell_command_override = ""
        settings.local_shell_custom_start_dir = ""
        if settings.local_shell_start_dir_mode == "custom":
            settings.local_shell_start_dir_mode = "home"
        settings.keepass_database_path = ""
        settings.keepass_key_file_path = ""
        settings.linux_desktop_prompt_dismissed = False
        settings.linux_desktop_last_update_prompt_version = ""
        cli_defaults = {
            "onepassword_cli_path": defaults.onepassword_cli_path,
            "bitwarden_cli_path": defaults.bitwarden_cli_path,
            "keeper_cli_path": defaults.keeper_cli_path,
            "keepass_cli_path": defaults.keepass_cli_path,
        }
        for field_name, default_value in cli_defaults.items():
            value = str(getattr(settings, field_name))
            if cls._command_path_is_nonportable(
                value,
                source_platform=source_platform,
                target_platform=target_platform,
            ):
                setattr(settings, field_name, default_value)
        settings.workspace_profiles = [
            cls._sanitize_cross_platform_workspace_profile(profile)
            for profile in settings.workspace_profiles
            if isinstance(profile, dict)
        ]

    @classmethod
    def _command_path_is_nonportable(
        cls,
        value: str,
        *,
        source_platform: str,
        target_platform: str,
    ) -> bool:
        cleaned = value.strip()
        if not cleaned:
            return False
        if _looks_like_absolute_or_explicit_command_path(cleaned):
            return True
        token = _first_shell_token(cleaned).lower()
        if not token:
            return False
        if target_platform != "windows" and token.endswith((".exe", ".bat", ".cmd", ".ps1")):
            return True
        if target_platform == "windows":
            return source_platform in {"linux", "macos"} and token.startswith("/")
        if target_platform == "linux":
            return source_platform == "macos" and _looks_like_macos_value(cleaned)
        if target_platform == "macos":
            return source_platform == "linux" and _looks_like_linux_value(cleaned)
        return False

    @classmethod
    def _sanitize_cross_platform_workspace_profile(cls, raw_profile: dict[str, object]) -> dict[str, object]:
        profile = dict(raw_profile)
        snapshot = raw_profile.get("snapshot")
        if isinstance(snapshot, dict):
            profile["snapshot"] = cls._sanitize_cross_platform_workspace_profile_snapshot(snapshot)
        return profile

    @classmethod
    def _sanitize_cross_platform_workspace_profile_snapshot(cls, snapshot: dict[str, object]) -> dict[str, object]:
        cleaned = dict(snapshot)
        cleaned.pop("window_geometry_b64", None)
        cleaned.pop("window_screen_name", None)
        cleaned.pop("window_screen_serial", None)
        cleaned.pop("window_frame_rect", None)
        cleaned.pop("main_splitter_b64", None)
        cleaned.pop("session_list_mode", None)
        cleaned.pop("session_list_visible", None)
        cleaned.pop("session_list_last_width", None)
        cleaned.pop("session_list_window_geometry_b64", None)
        cleaned.pop("session_list_window_screen_name", None)
        cleaned.pop("session_list_window_screen_serial", None)
        cleaned.pop("session_list_window_frame_rect", None)
        detached_windows = snapshot.get("detached_windows")
        if isinstance(detached_windows, list):
            cleaned["detached_windows"] = [
                cls._sanitize_cross_platform_detached_window_snapshot(window)
                for window in detached_windows
                if isinstance(window, dict)
            ]
        return cleaned

    @staticmethod
    def _sanitize_cross_platform_detached_window_snapshot(raw_window: dict[str, object]) -> dict[str, object]:
        cleaned = dict(raw_window)
        cleaned.pop("window_geometry_b64", None)
        cleaned.pop("window_screen_name", None)
        cleaned.pop("window_screen_serial", None)
        cleaned.pop("window_frame_rect", None)
        return cleaned

    @staticmethod
    def _sanitize_web_server_profile_config(raw: dict[str, object]) -> dict[str, object]:
        bind_host = str(raw.get("bind_host", "127.0.0.1")).strip() or "127.0.0.1"
        try:
            port = int(raw.get("port", 8000))
        except (TypeError, ValueError):
            port = 8000
        port = max(1, min(65535, port))
        mode = str(raw.get("mode", "static")).strip().lower() or "static"
        if mode not in {"static", "reverse_proxy"}:
            mode = "static"
        document_root = str(raw.get("document_root", "")).strip()
        if document_root:
            document_root = str(Path(document_root).expanduser())
        upstream_url = str(raw.get("upstream_url", "")).strip()
        proxy_path_prefix = str(raw.get("proxy_path_prefix", "/")).strip() or "/"
        if not proxy_path_prefix.startswith("/"):
            proxy_path_prefix = f"/{proxy_path_prefix}"
        if len(proxy_path_prefix) > 1:
            proxy_path_prefix = proxy_path_prefix.rstrip("/")
        cert_file = str(raw.get("cert_file", "")).strip()
        if cert_file:
            cert_file = str(Path(cert_file).expanduser())
        key_file = str(raw.get("key_file", "")).strip()
        if key_file:
            key_file = str(Path(key_file).expanduser())
        chain_file = str(raw.get("chain_file", "")).strip()
        if chain_file:
            chain_file = str(Path(chain_file).expanduser())
        tls_mode = str(raw.get("tls_mode", "")).strip().lower()
        if tls_mode not in {"none", "manual", "self_signed", "certbot"}:
            if bool(raw.get("generate_self_signed", False)):
                tls_mode = "self_signed"
            else:
                protocol = str(raw.get("protocol", "http")).strip().lower() or "http"
                if protocol == "https":
                    tls_mode = "manual"
                else:
                    tls_mode = "none"
        try:
            proxy_connect_timeout = int(raw.get("proxy_connect_timeout", 30))
        except (TypeError, ValueError):
            proxy_connect_timeout = 30
        try:
            proxy_read_timeout = int(raw.get("proxy_read_timeout", 60))
        except (TypeError, ValueError):
            proxy_read_timeout = 60
        proxy_connect_timeout = max(1, min(3600, proxy_connect_timeout))
        proxy_read_timeout = max(1, min(3600, proxy_read_timeout))
        additional_domains_raw = raw.get("certbot_additional_domains", "")
        if isinstance(additional_domains_raw, list):
            additional_domains = ", ".join(
                token
                for token in (
                    str(item).strip()
                    for item in additional_domains_raw
                )
                if token
            )
        else:
            additional_domains = str(additional_domains_raw).strip()
        try:
            certbot_challenge_port = int(raw.get("certbot_challenge_port", 80))
        except (TypeError, ValueError):
            certbot_challenge_port = 80
        certbot_challenge_port = max(1, min(65535, certbot_challenge_port))
        protocol = "https" if tls_mode != "none" else "http"
        return {
            "bind_host": bind_host,
            "port": port,
            "mode": mode,
            "document_root": document_root,
            "index_page": str(raw.get("index_page", "")).strip(),
            "tls_mode": tls_mode,
            "protocol": protocol,
            "cert_file": cert_file,
            "key_file": key_file,
            "chain_file": chain_file,
            "generate_self_signed": tls_mode == "self_signed",
            "allow_directory_listing": bool(raw.get("allow_directory_listing", False)),
            "upstream_url": upstream_url,
            "proxy_path_prefix": proxy_path_prefix,
            "proxy_strip_prefix": bool(raw.get("proxy_strip_prefix", False)),
            "proxy_preserve_host": bool(raw.get("proxy_preserve_host", True)),
            "proxy_send_x_forwarded": bool(raw.get("proxy_send_x_forwarded", True)),
            "proxy_verify_upstream_tls": bool(raw.get("proxy_verify_upstream_tls", True)),
            "proxy_enable_websocket": bool(raw.get("proxy_enable_websocket", True)),
            "proxy_connect_timeout": proxy_connect_timeout,
            "proxy_read_timeout": proxy_read_timeout,
            "proxy_extra_headers": str(raw.get("proxy_extra_headers", "")).strip(),
            "certbot_executable": str(raw.get("certbot_executable", "")).strip() or "certbot",
            "certbot_primary_domain": str(raw.get("certbot_primary_domain", "")).strip(),
            "certbot_additional_domains": additional_domains,
            "certbot_email": str(raw.get("certbot_email", "")).strip(),
            "certbot_challenge_port": certbot_challenge_port,
            "certbot_staging": bool(raw.get("certbot_staging", False)),
        }

    @staticmethod
    def _sanitize_syslog_snmp_monitor_profile_config(raw: dict[str, object]) -> dict[str, object]:
        return normalize_monitor_profile_config(raw)
