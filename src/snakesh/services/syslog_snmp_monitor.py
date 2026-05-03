from __future__ import annotations

import asyncio
import csv
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import gzip
import importlib
import ipaddress
import json
import os
from pathlib import Path
import platform
import queue
import re
import shlex
import shutil
import socket
import socketserver
import sqlite3
import ssl
import subprocess
import threading
import time
import traceback
from typing import Any, Callable
from uuid import uuid4

from snakesh import runtime
from snakesh.core.paths import data_dir


SYSLOG_SNMP_MONITOR_HELPER_FLAG = "--syslog-snmp-monitor-helper"
_HELPER_DATA_DIR_ENV = "SNAKESH_DATA_DIR"
_HELPER_PARENT_PID_ENV = "SNAKESH_HELPER_PARENT_PID"
DEFAULT_SYSLOG_UDP_PORT = 1514
DEFAULT_SYSLOG_TCP_PORT = 1514
DEFAULT_SYSLOG_TLS_PORT = 6514
DEFAULT_SNMP_PORT = 1162
DEFAULT_PROFILE_NAME = "Monitor Profile"
DEFAULT_EVENT_COLUMNS = [
    "received_ts",
    "source",
    "listener",
    "protocol",
    "transport",
    "severity_name",
    "facility_name",
    "syslog_hostname",
    "app_name",
    "message_text",
    "snmp_version",
    "snmp_security_name",
    "notification_oid",
    "enterprise_oid",
    "varbind_summary",
]
_STATUS_POLL_INTERVAL_SECONDS = 0.5
_ARCHIVE_MAINTENANCE_INTERVAL_SECONDS = 30.0
_ARCHIVE_BATCH_SIZE = 500
_MAX_QUERY_ROWS = 5000
_SYSLOG_SEVERITY_NAMES = {
    0: "Emergency",
    1: "Alert",
    2: "Critical",
    3: "Error",
    4: "Warning",
    5: "Notice",
    6: "Informational",
    7: "Debug",
}
_SYSLOG_FACILITY_NAMES = {
    0: "kernel",
    1: "user",
    2: "mail",
    3: "daemon",
    4: "auth",
    5: "syslog",
    6: "lpr",
    7: "news",
    8: "uucp",
    9: "clock",
    10: "authpriv",
    11: "ftp",
    12: "ntp",
    13: "audit",
    14: "alert",
    15: "clock2",
    16: "local0",
    17: "local1",
    18: "local2",
    19: "local3",
    20: "local4",
    21: "local5",
    22: "local6",
    23: "local7",
}
_SEVERITY_ORDER = {name.lower(): value for value, name in _SYSLOG_SEVERITY_NAMES.items()}


@dataclass(frozen=True, slots=True)
class MonitorSnmpV3User:
    username: str = ""
    auth_protocol: str = "SHA"
    auth_password: str = ""
    priv_protocol: str = "AES128"
    priv_password: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "username": self.username,
            "auth_protocol": self.auth_protocol,
            "auth_password": self.auth_password,
            "priv_protocol": self.priv_protocol,
            "priv_password": self.priv_password,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "MonitorSnmpV3User":
        return cls(
            username=str(raw.get("username", "")).strip(),
            auth_protocol=str(raw.get("auth_protocol", "SHA")).strip().upper() or "SHA",
            auth_password=str(raw.get("auth_password", "")),
            priv_protocol=str(raw.get("priv_protocol", "AES128")).strip().upper() or "AES128",
            priv_password=str(raw.get("priv_password", "")),
        )


@dataclass(frozen=True, slots=True)
class MonitorAlertRule:
    rule_id: str = ""
    name: str = "Alert Rule"
    enabled: bool = True
    protocol: str = "any"
    severity_at_least: str = ""
    source_contains: str = ""
    app_contains: str = ""
    trap_oid_contains: str = ""
    enterprise_oid_contains: str = ""
    text_contains: str = ""
    use_regex: bool = False
    popup: bool = True
    sound: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.rule_id,
            "name": self.name,
            "enabled": self.enabled,
            "protocol": self.protocol,
            "severity_at_least": self.severity_at_least,
            "source_contains": self.source_contains,
            "app_contains": self.app_contains,
            "trap_oid_contains": self.trap_oid_contains,
            "enterprise_oid_contains": self.enterprise_oid_contains,
            "text_contains": self.text_contains,
            "use_regex": self.use_regex,
            "popup": self.popup,
            "sound": self.sound,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "MonitorAlertRule":
        return cls(
            rule_id=str(raw.get("id", "")).strip() or uuid4().hex,
            name=str(raw.get("name", "")).strip() or "Alert Rule",
            enabled=bool(raw.get("enabled", True)),
            protocol=_normalize_choice(str(raw.get("protocol", "any")), {"any", "syslog", "snmp"}, "any"),
            severity_at_least=_normalize_alert_severity(str(raw.get("severity_at_least", ""))),
            source_contains=str(raw.get("source_contains", "")).strip(),
            app_contains=str(raw.get("app_contains", "")).strip(),
            trap_oid_contains=str(raw.get("trap_oid_contains", "")).strip(),
            enterprise_oid_contains=str(raw.get("enterprise_oid_contains", "")).strip(),
            text_contains=str(raw.get("text_contains", "")).strip(),
            use_regex=bool(raw.get("use_regex", False)),
            popup=bool(raw.get("popup", True)),
            sound=bool(raw.get("sound", False)),
        )


@dataclass(frozen=True, slots=True)
class MonitorRetentionPolicy:
    hot_retention_days: int = 7
    archive_retention_days: int = 90
    max_archive_size_mb: int = 4096
    archive_rotation_mb: int = 64

    def to_dict(self) -> dict[str, object]:
        return {
            "hot_retention_days": self.hot_retention_days,
            "archive_retention_days": self.archive_retention_days,
            "max_archive_size_mb": self.max_archive_size_mb,
            "archive_rotation_mb": self.archive_rotation_mb,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "MonitorRetentionPolicy":
        return cls(
            hot_retention_days=max(1, min(3650, _coerce_int(raw.get("hot_retention_days", 7), 7))),
            archive_retention_days=max(1, min(3650, _coerce_int(raw.get("archive_retention_days", 90), 90))),
            max_archive_size_mb=max(128, min(1024 * 1024, _coerce_int(raw.get("max_archive_size_mb", 4096), 4096))),
            archive_rotation_mb=max(1, min(1024, _coerce_int(raw.get("archive_rotation_mb", 64), 64))),
        )


@dataclass(frozen=True, slots=True)
class SyslogSnmpMonitorConfig:
    bind_host: str = "0.0.0.0"
    syslog_udp_enabled: bool = True
    syslog_udp_port: int = DEFAULT_SYSLOG_UDP_PORT
    syslog_tcp_enabled: bool = True
    syslog_tcp_port: int = DEFAULT_SYSLOG_TCP_PORT
    syslog_tls_enabled: bool = False
    syslog_tls_port: int = DEFAULT_SYSLOG_TLS_PORT
    syslog_tls_cert_file: str = ""
    syslog_tls_key_file: str = ""
    syslog_tls_ca_file: str = ""
    snmp_enabled: bool = True
    snmp_port: int = DEFAULT_SNMP_PORT
    snmp_v1_enabled: bool = True
    snmp_v2c_enabled: bool = True
    snmp_v3_enabled: bool = False
    snmp_communities: str = "public"
    snmp_v3_users: list[dict[str, object]] = field(default_factory=list)
    retention: dict[str, object] = field(default_factory=lambda: MonitorRetentionPolicy().to_dict())
    alert_rules: list[dict[str, object]] = field(default_factory=list)
    filter_state: dict[str, object] = field(default_factory=dict)
    layout_state: dict[str, object] = field(default_factory=dict)
    visible_columns: list[str] = field(default_factory=lambda: list(DEFAULT_EVENT_COLUMNS))

    def to_dict(self) -> dict[str, object]:
        return {
            "bind_host": self.bind_host,
            "syslog_udp_enabled": self.syslog_udp_enabled,
            "syslog_udp_port": self.syslog_udp_port,
            "syslog_tcp_enabled": self.syslog_tcp_enabled,
            "syslog_tcp_port": self.syslog_tcp_port,
            "syslog_tls_enabled": self.syslog_tls_enabled,
            "syslog_tls_port": self.syslog_tls_port,
            "syslog_tls_cert_file": self.syslog_tls_cert_file,
            "syslog_tls_key_file": self.syslog_tls_key_file,
            "syslog_tls_ca_file": self.syslog_tls_ca_file,
            "snmp_enabled": self.snmp_enabled,
            "snmp_port": self.snmp_port,
            "snmp_v1_enabled": self.snmp_v1_enabled,
            "snmp_v2c_enabled": self.snmp_v2c_enabled,
            "snmp_v3_enabled": self.snmp_v3_enabled,
            "snmp_communities": self.snmp_communities,
            "snmp_v3_users": list(self.snmp_v3_users),
            "retention": dict(self.retention),
            "alert_rules": list(self.alert_rules),
            "filter_state": dict(self.filter_state),
            "layout_state": dict(self.layout_state),
            "visible_columns": list(self.visible_columns),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "SyslogSnmpMonitorConfig":
        retention = raw.get("retention")
        alert_rules = raw.get("alert_rules")
        snmp_v3_users = raw.get("snmp_v3_users")
        visible_columns = raw.get("visible_columns")
        filter_state = raw.get("filter_state")
        layout_state = raw.get("layout_state")
        return cls(
            bind_host=str(raw.get("bind_host", "0.0.0.0")).strip() or "0.0.0.0",
            syslog_udp_enabled=bool(raw.get("syslog_udp_enabled", True)),
            syslog_udp_port=_normalize_port(raw.get("syslog_udp_port", DEFAULT_SYSLOG_UDP_PORT), DEFAULT_SYSLOG_UDP_PORT),
            syslog_tcp_enabled=bool(raw.get("syslog_tcp_enabled", True)),
            syslog_tcp_port=_normalize_port(raw.get("syslog_tcp_port", DEFAULT_SYSLOG_TCP_PORT), DEFAULT_SYSLOG_TCP_PORT),
            syslog_tls_enabled=bool(raw.get("syslog_tls_enabled", False)),
            syslog_tls_port=_normalize_port(raw.get("syslog_tls_port", DEFAULT_SYSLOG_TLS_PORT), DEFAULT_SYSLOG_TLS_PORT),
            syslog_tls_cert_file=_expand_path(raw.get("syslog_tls_cert_file")),
            syslog_tls_key_file=_expand_path(raw.get("syslog_tls_key_file")),
            syslog_tls_ca_file=_expand_path(raw.get("syslog_tls_ca_file")),
            snmp_enabled=bool(raw.get("snmp_enabled", True)),
            snmp_port=_normalize_port(raw.get("snmp_port", DEFAULT_SNMP_PORT), DEFAULT_SNMP_PORT),
            snmp_v1_enabled=bool(raw.get("snmp_v1_enabled", True)),
            snmp_v2c_enabled=bool(raw.get("snmp_v2c_enabled", True)),
            snmp_v3_enabled=bool(raw.get("snmp_v3_enabled", False)),
            snmp_communities=str(raw.get("snmp_communities", "public")).strip() or "public",
            snmp_v3_users=[
                MonitorSnmpV3User.from_dict(item).to_dict()
                for item in snmp_v3_users
                if isinstance(item, dict) and MonitorSnmpV3User.from_dict(item).username
            ] if isinstance(snmp_v3_users, list) else [],
            retention=MonitorRetentionPolicy.from_dict(retention if isinstance(retention, dict) else {}).to_dict(),
            alert_rules=[
                MonitorAlertRule.from_dict(item).to_dict()
                for item in alert_rules
                if isinstance(item, dict)
            ] if isinstance(alert_rules, list) else [],
            filter_state=dict(filter_state) if isinstance(filter_state, dict) else {},
            layout_state=dict(layout_state) if isinstance(layout_state, dict) else {},
            visible_columns=_normalize_visible_columns(visible_columns),
        )


@dataclass(frozen=True, slots=True)
class SyslogSnmpMonitorStatus:
    state: str = "stopped"
    pid: int | None = None
    message: str = ""
    bind_host: str = ""
    listeners: list[str] = field(default_factory=list)
    event_count: int = 0
    alert_count: int = 0
    last_event_at: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "state": self.state,
            "pid": self.pid,
            "message": self.message,
            "bind_host": self.bind_host,
            "listeners": list(self.listeners),
            "event_count": self.event_count,
            "alert_count": self.alert_count,
            "last_event_at": self.last_event_at,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "SyslogSnmpMonitorStatus":
        listeners = raw.get("listeners", [])
        return cls(
            state=str(raw.get("state", "stopped")).strip() or "stopped",
            pid=_coerce_optional_int(raw.get("pid")),
            message=str(raw.get("message", "")).strip(),
            bind_host=str(raw.get("bind_host", "")).strip(),
            listeners=[str(item).strip() for item in listeners if str(item).strip()] if isinstance(listeners, list) else [],
            event_count=max(0, _coerce_int(raw.get("event_count", 0), 0)),
            alert_count=max(0, _coerce_int(raw.get("alert_count", 0), 0)),
            last_event_at=str(raw.get("last_event_at", "")).strip(),
            error=str(raw.get("error", "")).strip(),
        )


@dataclass(frozen=True, slots=True)
class SyslogSnmpMonitorPaths:
    root: Path
    config_path: Path
    status_path: Path
    stop_path: Path
    db_path: Path
    archives_root: Path


@dataclass(frozen=True, slots=True)
class SyslogSnmpMonitorStorageStats:
    live_event_count: int
    notification_count: int
    db_size_bytes: int
    archive_file_count: int
    archive_size_bytes: int
    oldest_live_event_at: str
    newest_live_event_at: str


@dataclass(frozen=True, slots=True)
class ClearMonitorDataResult:
    live_event_count: int
    notification_count: int
    archive_file_count: int


@dataclass(frozen=True, slots=True)
class MonitorQueryFilters:
    text: str = ""
    use_regex: bool = False
    case_sensitive: bool = False
    start_ts: str = ""
    end_ts: str = ""
    source_contains: str = ""
    listener: str = ""
    protocol: str = ""
    transport: str = ""
    severity_name: str = ""
    facility_name: str = ""
    syslog_hostname: str = ""
    app_name: str = ""
    procid: str = ""
    msgid: str = ""
    snmp_version: str = ""
    snmp_security_name: str = ""
    notification_oid: str = ""
    enterprise_oid: str = ""
    varbind_text: str = ""
    alerted_only: bool = False
    data_scope: str = "live"

    def to_dict(self) -> dict[str, object]:
        return {
            "text": self.text,
            "use_regex": self.use_regex,
            "case_sensitive": self.case_sensitive,
            "start_ts": self.start_ts,
            "end_ts": self.end_ts,
            "source_contains": self.source_contains,
            "listener": self.listener,
            "protocol": self.protocol,
            "transport": self.transport,
            "severity_name": self.severity_name,
            "facility_name": self.facility_name,
            "syslog_hostname": self.syslog_hostname,
            "app_name": self.app_name,
            "procid": self.procid,
            "msgid": self.msgid,
            "snmp_version": self.snmp_version,
            "snmp_security_name": self.snmp_security_name,
            "notification_oid": self.notification_oid,
            "enterprise_oid": self.enterprise_oid,
            "varbind_text": self.varbind_text,
            "alerted_only": self.alerted_only,
            "data_scope": self.data_scope,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "MonitorQueryFilters":
        return cls(
            text=str(raw.get("text", "")).strip(),
            use_regex=bool(raw.get("use_regex", False)),
            case_sensitive=bool(raw.get("case_sensitive", False)),
            start_ts=str(raw.get("start_ts", "")).strip(),
            end_ts=str(raw.get("end_ts", "")).strip(),
            source_contains=str(raw.get("source_contains", "")).strip(),
            listener=str(raw.get("listener", "")).strip(),
            protocol=str(raw.get("protocol", "")).strip(),
            transport=str(raw.get("transport", "")).strip(),
            severity_name=str(raw.get("severity_name", "")).strip(),
            facility_name=str(raw.get("facility_name", "")).strip(),
            syslog_hostname=str(raw.get("syslog_hostname", "")).strip(),
            app_name=str(raw.get("app_name", "")).strip(),
            procid=str(raw.get("procid", "")).strip(),
            msgid=str(raw.get("msgid", "")).strip(),
            snmp_version=str(raw.get("snmp_version", "")).strip(),
            snmp_security_name=str(raw.get("snmp_security_name", "")).strip(),
            notification_oid=str(raw.get("notification_oid", "")).strip(),
            enterprise_oid=str(raw.get("enterprise_oid", "")).strip(),
            varbind_text=str(raw.get("varbind_text", "")).strip(),
            alerted_only=bool(raw.get("alerted_only", False)),
            data_scope=_normalize_choice(str(raw.get("data_scope", "live")), {"live", "archived", "all"}, "live"),
        )


def syslog_snmp_monitor_root() -> Path:
    root = data_dir() / "syslog-snmp-monitor"
    root.mkdir(parents=True, exist_ok=True)
    return root


def syslog_snmp_monitor_profiles_root() -> Path:
    root = syslog_snmp_monitor_root() / "profiles"
    root.mkdir(parents=True, exist_ok=True)
    return root


def syslog_snmp_monitor_profile_paths(profile_id: str) -> SyslogSnmpMonitorPaths:
    safe_id = _safe_profile_id(profile_id)
    root = syslog_snmp_monitor_profiles_root() / safe_id
    return SyslogSnmpMonitorPaths(
        root=root,
        config_path=root / "config.json",
        status_path=root / "status.json",
        stop_path=root / "stop",
        db_path=root / "events.sqlite",
        archives_root=root / "archives",
    )


def read_syslog_snmp_monitor_config(profile_id: str) -> SyslogSnmpMonitorConfig:
    paths = syslog_snmp_monitor_profile_paths(profile_id)
    if not paths.config_path.exists():
        return SyslogSnmpMonitorConfig()
    try:
        payload = json.loads(paths.config_path.read_text(encoding="utf-8"))
    except Exception:
        return SyslogSnmpMonitorConfig()
    return SyslogSnmpMonitorConfig.from_dict(payload if isinstance(payload, dict) else {})


def write_syslog_snmp_monitor_config(profile_id: str, config: SyslogSnmpMonitorConfig) -> None:
    paths = syslog_snmp_monitor_profile_paths(profile_id)
    validated = validate_syslog_snmp_monitor_config(config)
    paths.root.mkdir(parents=True, exist_ok=True)
    paths.archives_root.mkdir(parents=True, exist_ok=True)
    paths.config_path.write_text(json.dumps(validated.to_dict(), indent=2), encoding="utf-8")


def read_syslog_snmp_monitor_status(profile_id: str) -> SyslogSnmpMonitorStatus:
    paths = syslog_snmp_monitor_profile_paths(profile_id)
    if not paths.status_path.exists():
        return SyslogSnmpMonitorStatus()
    try:
        payload = json.loads(paths.status_path.read_text(encoding="utf-8"))
    except Exception:
        return SyslogSnmpMonitorStatus()
    return SyslogSnmpMonitorStatus.from_dict(payload if isinstance(payload, dict) else {})


def write_syslog_snmp_monitor_status(profile_id: str, status: SyslogSnmpMonitorStatus) -> None:
    paths = syslog_snmp_monitor_profile_paths(profile_id)
    paths.root.mkdir(parents=True, exist_ok=True)
    paths.status_path.write_text(json.dumps(status.to_dict(), indent=2), encoding="utf-8")


def validate_syslog_snmp_monitor_config(config: SyslogSnmpMonitorConfig) -> SyslogSnmpMonitorConfig:
    bind_host = config.bind_host.strip() or "0.0.0.0"
    try:
        ipaddress.ip_address(bind_host)
    except ValueError as exc:
        raise ValueError("Bind address must be a valid IPv4 or IPv6 address.") from exc

    communities = ", ".join(token for token in _split_csv_tokens(config.snmp_communities) if token) or "public"
    snmp_v3_users = [
        user.to_dict()
        for user in (
            MonitorSnmpV3User.from_dict(item)
            for item in config.snmp_v3_users
            if isinstance(item, dict)
        )
        if user.username
    ]
    if config.syslog_tls_enabled:
        if not config.syslog_tls_cert_file or not Path(config.syslog_tls_cert_file).exists():
            raise ValueError("Syslog TLS certificate file must exist when TLS is enabled.")
        if not config.syslog_tls_key_file or not Path(config.syslog_tls_key_file).exists():
            raise ValueError("Syslog TLS key file must exist when TLS is enabled.")
        if config.syslog_tls_ca_file and not Path(config.syslog_tls_ca_file).exists():
            raise ValueError("Syslog TLS CA file must exist when provided.")
    if config.snmp_enabled and not (config.snmp_v1_enabled or config.snmp_v2c_enabled or config.snmp_v3_enabled):
        raise ValueError("Enable at least one SNMP version when SNMP notifications are enabled.")
    if config.snmp_enabled and config.snmp_v3_enabled and not snmp_v3_users:
        raise ValueError("Add at least one SNMPv3 user when SNMPv3 is enabled.")

    alert_rules = [MonitorAlertRule.from_dict(item).to_dict() for item in config.alert_rules if isinstance(item, dict)]
    retention = MonitorRetentionPolicy.from_dict(config.retention).to_dict()
    return SyslogSnmpMonitorConfig(
        bind_host=bind_host,
        syslog_udp_enabled=bool(config.syslog_udp_enabled),
        syslog_udp_port=_normalize_port(config.syslog_udp_port, DEFAULT_SYSLOG_UDP_PORT),
        syslog_tcp_enabled=bool(config.syslog_tcp_enabled),
        syslog_tcp_port=_normalize_port(config.syslog_tcp_port, DEFAULT_SYSLOG_TCP_PORT),
        syslog_tls_enabled=bool(config.syslog_tls_enabled),
        syslog_tls_port=_normalize_port(config.syslog_tls_port, DEFAULT_SYSLOG_TLS_PORT),
        syslog_tls_cert_file=_expand_path(config.syslog_tls_cert_file),
        syslog_tls_key_file=_expand_path(config.syslog_tls_key_file),
        syslog_tls_ca_file=_expand_path(config.syslog_tls_ca_file),
        snmp_enabled=bool(config.snmp_enabled),
        snmp_port=_normalize_port(config.snmp_port, DEFAULT_SNMP_PORT),
        snmp_v1_enabled=bool(config.snmp_v1_enabled),
        snmp_v2c_enabled=bool(config.snmp_v2c_enabled),
        snmp_v3_enabled=bool(config.snmp_v3_enabled),
        snmp_communities=communities,
        snmp_v3_users=snmp_v3_users,
        retention=retention,
        alert_rules=alert_rules,
        filter_state=dict(config.filter_state),
        layout_state=dict(config.layout_state),
        visible_columns=_normalize_visible_columns(config.visible_columns),
    )


def needs_syslog_snmp_monitor_gui_elevation(
    config: SyslogSnmpMonitorConfig,
    *,
    platform_name: str | None = None,
) -> bool:
    normalized = validate_syslog_snmp_monitor_config(config)
    system = _platform_name(platform_name)
    if system not in {"linux", "darwin"}:
        return False
    for enabled, port in (
        (normalized.syslog_udp_enabled, normalized.syslog_udp_port),
        (normalized.syslog_tcp_enabled, normalized.syslog_tcp_port),
        (normalized.syslog_tls_enabled, normalized.syslog_tls_port),
        (normalized.snmp_enabled, normalized.snmp_port),
    ):
        if enabled and port < 1024:
            return True
    return False


def syslog_snmp_monitor_helper_command(profile_id: str) -> list[str]:
    return runtime.self_launch_command([SYSLOG_SNMP_MONITOR_HELPER_FLAG, _safe_profile_id(profile_id)])


def _helper_launch_environment() -> dict[str, str]:
    return {
        _HELPER_DATA_DIR_ENV: str(data_dir()),
        _HELPER_PARENT_PID_ENV: str(os.getpid()),
    }


def _shell_with_helper_environment(command: list[str]) -> str:
    env_prefix = " ".join(
        f"{key}={shlex.quote(value)}"
        for key, value in _helper_launch_environment().items()
        if value.strip()
    )
    if env_prefix:
        env_prefix += " "
    return f"{env_prefix}nohup {shlex.join(command)} >/dev/null 2>&1 &"


def launch_syslog_snmp_monitor_helper(profile_id: str) -> list[str]:
    command = syslog_snmp_monitor_helper_command(profile_id)
    env = os.environ.copy()
    env.update(_helper_launch_environment())
    if _platform_name() == "windows":
        creationflags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen(  # noqa: S603
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=creationflags,
            env=env,
        )
    else:
        subprocess.Popen(  # noqa: S603
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=True,
            env=env,
        )
    return command


def launch_syslog_snmp_monitor_helper_elevated(profile_id: str, *, platform_name: str | None = None) -> list[str]:
    system = _platform_name(platform_name)
    command = syslog_snmp_monitor_helper_command(profile_id)
    shell_command = _shell_with_helper_environment(command)
    if system == "linux":
        if shutil.which("pkexec") is None:
            raise ValueError("pkexec is required to start privileged syslog/SNMP listeners on Linux.")
        result = subprocess.run(  # noqa: S603
            ["pkexec", "/bin/sh", "-c", shell_command],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise ValueError((result.stderr or result.stdout or "Privileged launch failed.").strip())
        return command
    if system == "darwin":
        if shutil.which("osascript") is None:
            raise ValueError("osascript is required to start privileged syslog/SNMP listeners on macOS.")
        script = f'do shell script "{_escape_applescript(shell_command)}" with administrator privileges'
        result = subprocess.run(  # noqa: S603
            ["osascript", "-e", script],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise ValueError((result.stderr or result.stdout or "Privileged launch failed.").strip())
        return command
    raise ValueError("GUI elevation for privileged syslog/SNMP ports is not supported on this platform.")


def is_syslog_snmp_monitor_running(profile_id: str) -> bool:
    status = read_syslog_snmp_monitor_status(profile_id)
    return bool(status.pid and status.pid > 0 and _pid_exists(status.pid))


def request_syslog_snmp_monitor_stop(profile_id: str) -> None:
    paths = syslog_snmp_monitor_profile_paths(profile_id)
    paths.root.mkdir(parents=True, exist_ok=True)
    paths.stop_path.write_text("stop\n", encoding="utf-8")


def ensure_monitor_database(profile_id: str) -> None:
    paths = syslog_snmp_monitor_profile_paths(profile_id)
    paths.root.mkdir(parents=True, exist_ok=True)
    paths.archives_root.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(paths.db_path)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                received_ts TEXT NOT NULL,
                event_ts TEXT,
                source_ip TEXT,
                source_port INTEGER,
                source_host TEXT,
                listener TEXT,
                protocol TEXT,
                transport TEXT,
                facility INTEGER,
                facility_name TEXT,
                severity INTEGER,
                severity_name TEXT,
                syslog_hostname TEXT,
                app_name TEXT,
                procid TEXT,
                msgid TEXT,
                structured_data TEXT,
                message_text TEXT,
                snmp_version TEXT,
                snmp_security_name TEXT,
                snmp_community TEXT,
                snmp_user TEXT,
                snmp_engine_id TEXT,
                snmp_context_name TEXT,
                notification_oid TEXT,
                enterprise_oid TEXT,
                snmp_uptime TEXT,
                varbind_summary TEXT,
                alerted INTEGER NOT NULL DEFAULT 0,
                raw_payload TEXT,
                details_json TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id INTEGER NOT NULL,
                created_ts TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                play_sound INTEGER NOT NULL DEFAULT 0,
                shown INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_events_received_ts ON events(received_ts DESC)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_events_protocol ON events(protocol)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_events_alerted ON events(alerted)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_events_source_ip ON events(source_ip)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_events_severity ON events(severity)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_notifications_shown ON notifications(shown, created_ts DESC)")
        connection.commit()
    finally:
        connection.close()


def insert_monitor_event(profile_id: str, event: dict[str, object], *, connection: sqlite3.Connection | None = None) -> int:
    ensure_monitor_database(profile_id)
    owns_connection = connection is None
    conn = connection or sqlite3.connect(syslog_snmp_monitor_profile_paths(profile_id).db_path)
    try:
        payload = _normalized_event(event)
        cursor = conn.execute(
            """
            INSERT INTO events (
                received_ts, event_ts, source_ip, source_port, source_host, listener, protocol, transport,
                facility, facility_name, severity, severity_name, syslog_hostname, app_name, procid, msgid,
                structured_data, message_text, snmp_version, snmp_security_name, snmp_community, snmp_user,
                snmp_engine_id, snmp_context_name, notification_oid, enterprise_oid, snmp_uptime,
                varbind_summary, alerted, raw_payload, details_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload.get("received_ts", ""),
                payload.get("event_ts", ""),
                payload.get("source_ip", ""),
                _coerce_optional_int(payload.get("source_port")),
                payload.get("source_host", ""),
                payload.get("listener", ""),
                payload.get("protocol", ""),
                payload.get("transport", ""),
                _coerce_optional_int(payload.get("facility")),
                payload.get("facility_name", ""),
                _coerce_optional_int(payload.get("severity")),
                payload.get("severity_name", ""),
                payload.get("syslog_hostname", ""),
                payload.get("app_name", ""),
                payload.get("procid", ""),
                payload.get("msgid", ""),
                payload.get("structured_data", ""),
                payload.get("message_text", ""),
                payload.get("snmp_version", ""),
                payload.get("snmp_security_name", ""),
                payload.get("snmp_community", ""),
                payload.get("snmp_user", ""),
                payload.get("snmp_engine_id", ""),
                payload.get("snmp_context_name", ""),
                payload.get("notification_oid", ""),
                payload.get("enterprise_oid", ""),
                payload.get("snmp_uptime", ""),
                payload.get("varbind_summary", ""),
                1 if payload.get("alerted") else 0,
                payload.get("raw_payload", ""),
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        event_id = int(cursor.lastrowid)
        notifications = payload.get("notifications", [])
        if isinstance(notifications, list):
            for item in notifications:
                if not isinstance(item, dict):
                    continue
                conn.execute(
                    """
                    INSERT INTO notifications (event_id, created_ts, title, body, play_sound, shown)
                    VALUES (?, ?, ?, ?, ?, 0)
                    """,
                    (
                        event_id,
                        payload.get("received_ts", ""),
                        str(item.get("title", "")).strip() or "Syslog / SNMP Alert",
                        str(item.get("body", "")).strip(),
                        1 if bool(item.get("play_sound", False)) else 0,
                    ),
                )
        if owns_connection:
            conn.commit()
        return event_id
    finally:
        if owns_connection:
            conn.close()


def fetch_monitor_events(profile_id: str, filters: MonitorQueryFilters | None = None, *, limit: int = 500) -> list[dict[str, object]]:
    paths = syslog_snmp_monitor_profile_paths(profile_id)
    ensure_monitor_database(profile_id)
    normalized_filters = filters or MonitorQueryFilters()
    live_rows: list[dict[str, object]] = []
    if normalized_filters.data_scope in {"live", "all"} and paths.db_path.exists():
        connection = sqlite3.connect(paths.db_path)
        connection.row_factory = sqlite3.Row
        try:
            query, params = _live_query_for_filters(normalized_filters, limit=max(limit, _MAX_QUERY_ROWS if normalized_filters.use_regex else limit))
            for row in connection.execute(query, params):
                live_rows.append(_decode_event_row(row))
        finally:
            connection.close()

    archived_rows: list[dict[str, object]] = []
    if normalized_filters.data_scope in {"archived", "all"}:
        archived_rows = _read_archived_events(paths.archives_root, normalized_filters, limit=limit)

    combined = [*live_rows, *archived_rows]
    filtered = [row for row in combined if _event_matches_filters(row, normalized_filters)]
    filtered.sort(key=lambda item: str(item.get("received_ts", "")), reverse=True)
    return filtered[: max(1, limit)]


def fetch_monitor_event(profile_id: str, event_id: int) -> dict[str, object] | None:
    paths = syslog_snmp_monitor_profile_paths(profile_id)
    if paths.db_path.exists():
        connection = sqlite3.connect(paths.db_path)
        connection.row_factory = sqlite3.Row
        try:
            row = connection.execute("SELECT * FROM events WHERE id = ?", (int(event_id),)).fetchone()
            if row is not None:
                return _decode_event_row(row)
        finally:
            connection.close()
    for archived in _read_archived_events(paths.archives_root, MonitorQueryFilters(data_scope="archived"), limit=_MAX_QUERY_ROWS):
        if int(archived.get("id", -1) or -1) == int(event_id):
            return archived
    return None


def export_monitor_events_csv(profile_id: str, filters: MonitorQueryFilters | None = None, *, limit: int = 5000) -> str:
    rows = fetch_monitor_events(profile_id, filters, limit=limit)
    if not rows:
        return ""
    headers = [
        "id",
        "received_ts",
        "event_ts",
        "source",
        "listener",
        "protocol",
        "transport",
        "facility_name",
        "severity_name",
        "syslog_hostname",
        "app_name",
        "procid",
        "msgid",
        "message_text",
        "snmp_version",
        "snmp_security_name",
        "notification_oid",
        "enterprise_oid",
        "varbind_summary",
        "alerted",
    ]
    output: list[str] = []
    writer = csv.writer(_ListWriter(output))
    writer.writerow(headers)
    for row in rows:
        writer.writerow(
            [
                row.get("id", ""),
                row.get("received_ts", ""),
                row.get("event_ts", ""),
                _event_source_text(row),
                row.get("listener", ""),
                row.get("protocol", ""),
                row.get("transport", ""),
                row.get("facility_name", ""),
                row.get("severity_name", ""),
                row.get("syslog_hostname", ""),
                row.get("app_name", ""),
                row.get("procid", ""),
                row.get("msgid", ""),
                row.get("message_text", ""),
                row.get("snmp_version", ""),
                row.get("snmp_security_name", ""),
                row.get("notification_oid", ""),
                row.get("enterprise_oid", ""),
                row.get("varbind_summary", ""),
                "yes" if bool(row.get("alerted")) else "no",
            ]
        )
    return "".join(output)


def export_monitor_events_json(profile_id: str, filters: MonitorQueryFilters | None = None, *, limit: int = 5000) -> str:
    rows = fetch_monitor_events(profile_id, filters, limit=limit)
    return json.dumps(rows, indent=2, ensure_ascii=False)


def fetch_unshown_notifications(profile_id: str, *, limit: int = 20) -> list[dict[str, object]]:
    paths = syslog_snmp_monitor_profile_paths(profile_id)
    if not paths.db_path.exists():
        return []
    connection = sqlite3.connect(paths.db_path)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT id, event_id, created_ts, title, body, play_sound, shown
            FROM notifications
            WHERE shown = 0
            ORDER BY created_ts DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "event_id": int(row["event_id"]),
                "created_ts": str(row["created_ts"]),
                "title": str(row["title"]),
                "body": str(row["body"]),
                "play_sound": bool(row["play_sound"]),
            }
            for row in rows
        ]
    finally:
        connection.close()


def mark_notifications_shown(profile_id: str, notification_ids: list[int]) -> None:
    ids = [int(item) for item in notification_ids if int(item) > 0]
    if not ids:
        return
    paths = syslog_snmp_monitor_profile_paths(profile_id)
    if not paths.db_path.exists():
        return
    placeholders = ", ".join("?" for _ in ids)
    connection = sqlite3.connect(paths.db_path)
    try:
        connection.execute(f"UPDATE notifications SET shown = 1 WHERE id IN ({placeholders})", ids)
        connection.commit()
    finally:
        connection.close()


def archive_monitor_events(profile_id: str, *, retention: MonitorRetentionPolicy | None = None) -> int:
    paths = syslog_snmp_monitor_profile_paths(profile_id)
    if not paths.db_path.exists():
        return 0
    keep = retention or MonitorRetentionPolicy.from_dict(read_syslog_snmp_monitor_config(profile_id).retention)
    cutoff = datetime.now(UTC) - timedelta(days=keep.hot_retention_days)
    connection = sqlite3.connect(paths.db_path)
    connection.row_factory = sqlite3.Row
    archived = 0
    try:
        while True:
            rows = connection.execute(
                """
                SELECT * FROM events
                WHERE received_ts < ?
                ORDER BY received_ts ASC, id ASC
                LIMIT ?
                """,
                (_isoformat(cutoff), _ARCHIVE_BATCH_SIZE),
            ).fetchall()
            if not rows:
                break
            archive_path = _next_archive_path(paths.archives_root)
            archive_path.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(archive_path, "wt", encoding="utf-8") as handle:
                for row in rows:
                    event_payload = _decode_event_row(row)
                    event_payload["archived_at"] = _isoformat(datetime.now(UTC))
                    handle.write(json.dumps(event_payload, ensure_ascii=False) + "\n")
                    archived += 1
            ids = [int(row["id"]) for row in rows]
            placeholders = ", ".join("?" for _ in ids)
            connection.execute(f"DELETE FROM events WHERE id IN ({placeholders})", ids)
            connection.commit()
        connection.execute("VACUUM")
    finally:
        connection.close()
    return archived


def purge_monitor_archives(profile_id: str, *, retention: MonitorRetentionPolicy | None = None) -> int:
    paths = syslog_snmp_monitor_profile_paths(profile_id)
    keep = retention or MonitorRetentionPolicy.from_dict(read_syslog_snmp_monitor_config(profile_id).retention)
    if not paths.archives_root.exists():
        return 0
    removed = 0
    cutoff = time.time() - (keep.archive_retention_days * 24 * 60 * 60)
    archive_files = sorted(
        (path for path in paths.archives_root.rglob("*.jsonl.gz") if path.is_file()),
        key=lambda path: path.stat().st_mtime if path.exists() else 0.0,
    )
    for path in archive_files:
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            continue
    archive_files = sorted(
        (path for path in paths.archives_root.rglob("*.jsonl.gz") if path.is_file()),
        key=lambda path: path.stat().st_mtime if path.exists() else 0.0,
    )
    quota_bytes = keep.max_archive_size_mb * 1024 * 1024
    total_bytes = sum(path.stat().st_size for path in archive_files if path.exists())
    for path in archive_files:
        if total_bytes <= quota_bytes:
            break
        try:
            size = path.stat().st_size
            path.unlink()
            removed += 1
            total_bytes = max(0, total_bytes - size)
        except OSError:
            continue
    _remove_empty_directories(paths.archives_root)
    return removed


def clear_monitor_profile_data(profile_id: str) -> ClearMonitorDataResult:
    paths = syslog_snmp_monitor_profile_paths(profile_id)
    ensure_monitor_database(profile_id)

    live_event_count = 0
    notification_count = 0
    connection = sqlite3.connect(paths.db_path)
    try:
        live_event_count = int(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0])
        notification_count = int(connection.execute("SELECT COUNT(*) FROM notifications").fetchone()[0])
        connection.execute("DELETE FROM notifications")
        connection.execute("DELETE FROM events")
        connection.commit()
        connection.execute("VACUUM")
    finally:
        connection.close()

    archive_file_count = 0
    if paths.archives_root.exists():
        archive_files = [path for path in paths.archives_root.rglob("*.jsonl.gz") if path.is_file()]
        archive_file_count = len(archive_files)
        for path in archive_files:
            try:
                path.unlink()
            except OSError:
                continue
        _remove_empty_directories(paths.archives_root)

    return ClearMonitorDataResult(
        live_event_count=live_event_count,
        notification_count=notification_count,
        archive_file_count=archive_file_count,
    )


def monitor_storage_stats(profile_id: str) -> SyslogSnmpMonitorStorageStats:
    paths = syslog_snmp_monitor_profile_paths(profile_id)
    ensure_monitor_database(profile_id)
    live_event_count = 0
    notification_count = 0
    oldest_live = ""
    newest_live = ""
    if paths.db_path.exists():
        connection = sqlite3.connect(paths.db_path)
        try:
            live_event_count = int(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0])
            notification_count = int(connection.execute("SELECT COUNT(*) FROM notifications WHERE shown = 0").fetchone()[0])
            oldest_row = connection.execute("SELECT received_ts FROM events ORDER BY received_ts ASC LIMIT 1").fetchone()
            newest_row = connection.execute("SELECT received_ts FROM events ORDER BY received_ts DESC LIMIT 1").fetchone()
            oldest_live = str(oldest_row[0]) if oldest_row else ""
            newest_live = str(newest_row[0]) if newest_row else ""
        finally:
            connection.close()
    archive_files = [path for path in paths.archives_root.rglob("*.jsonl.gz")] if paths.archives_root.exists() else []
    archive_size = 0
    for path in archive_files:
        try:
            archive_size += path.stat().st_size
        except OSError:
            continue
    db_size = 0
    if paths.db_path.exists():
        try:
            db_size = paths.db_path.stat().st_size
        except OSError:
            db_size = 0
    return SyslogSnmpMonitorStorageStats(
        live_event_count=live_event_count,
        notification_count=notification_count,
        db_size_bytes=db_size,
        archive_file_count=len(archive_files),
        archive_size_bytes=archive_size,
        oldest_live_event_at=oldest_live,
        newest_live_event_at=newest_live,
    )


def run_syslog_snmp_monitor_helper(profile_id: str) -> int:
    paths = syslog_snmp_monitor_profile_paths(profile_id)
    config = validate_syslog_snmp_monitor_config(read_syslog_snmp_monitor_config(profile_id))
    parent_pid = _coerce_int(os.environ.get(_HELPER_PARENT_PID_ENV, "0"), 0)
    ensure_monitor_database(profile_id)
    paths.root.mkdir(parents=True, exist_ok=True)
    paths.archives_root.mkdir(parents=True, exist_ok=True)
    try:
        paths.stop_path.unlink(missing_ok=True)
    except Exception:
        pass

    stop_event = threading.Event()
    writer = _MonitorWriter(profile_id, config)
    listeners: list[_BaseRuntimeListener] = []
    try:
        active_listener_labels: list[str] = []
        if config.syslog_udp_enabled:
            listeners.append(_SyslogUdpListener(config.bind_host, config.syslog_udp_port, "syslog-udp", writer))
            active_listener_labels.append(f"Syslog UDP {config.syslog_udp_port}")
        if config.syslog_tcp_enabled:
            listeners.append(_SyslogTcpListener(config.bind_host, config.syslog_tcp_port, "syslog-tcp", writer))
            active_listener_labels.append(f"Syslog TCP {config.syslog_tcp_port}")
        if config.syslog_tls_enabled:
            listeners.append(
                _SyslogTlsListener(
                    config.bind_host,
                    config.syslog_tls_port,
                    "syslog-tls",
                    writer,
                    cert_file=config.syslog_tls_cert_file,
                    key_file=config.syslog_tls_key_file,
                    ca_file=config.syslog_tls_ca_file,
                )
            )
            active_listener_labels.append(f"Syslog TLS {config.syslog_tls_port}")
        if config.snmp_enabled:
            listeners.append(_SnmpTrapListener(config.bind_host, config.snmp_port, writer, config))
            active_listener_labels.append(f"SNMP Trap {config.snmp_port}")

        write_syslog_snmp_monitor_status(
            profile_id,
            SyslogSnmpMonitorStatus(
                state="starting",
                pid=os.getpid(),
                message="Starting listeners...",
                bind_host=config.bind_host,
                listeners=active_listener_labels,
            ),
        )
        for listener in listeners:
            listener.start()

        last_maintenance = 0.0
        while not stop_event.is_set():
            if paths.stop_path.exists():
                break
            if parent_pid > 0 and not _pid_exists(parent_pid):
                break
            failures = [listener for listener in listeners if listener.error]
            stats = monitor_storage_stats(profile_id)
            if failures:
                message = "; ".join(listener.error for listener in failures if listener.error)
                write_syslog_snmp_monitor_status(
                    profile_id,
                    SyslogSnmpMonitorStatus(
                        state="error",
                        pid=os.getpid(),
                        message=message,
                        bind_host=config.bind_host,
                        listeners=active_listener_labels,
                        event_count=stats.live_event_count,
                        alert_count=stats.notification_count,
                        last_event_at=stats.newest_live_event_at,
                        error=message,
                    ),
                )
                break
            write_syslog_snmp_monitor_status(
                profile_id,
                SyslogSnmpMonitorStatus(
                    state="running",
                    pid=os.getpid(),
                    message="Listening for syslog and SNMP notifications.",
                    bind_host=config.bind_host,
                    listeners=active_listener_labels,
                    event_count=stats.live_event_count,
                    alert_count=stats.notification_count,
                    last_event_at=stats.newest_live_event_at,
                ),
            )
            now = time.monotonic()
            if now - last_maintenance >= _ARCHIVE_MAINTENANCE_INTERVAL_SECONDS:
                archive_monitor_events(profile_id, retention=MonitorRetentionPolicy.from_dict(config.retention))
                purge_monitor_archives(profile_id, retention=MonitorRetentionPolicy.from_dict(config.retention))
                last_maintenance = now
            time.sleep(_STATUS_POLL_INTERVAL_SECONDS)
        return 0
    except Exception as exc:  # noqa: BLE001
        write_syslog_snmp_monitor_status(
            profile_id,
            SyslogSnmpMonitorStatus(
                state="error",
                pid=os.getpid(),
                message=str(exc),
                bind_host=config.bind_host,
                error=traceback.format_exc(),
            ),
        )
        return 1
    finally:
        for listener in listeners:
            listener.stop()
        try:
            writer.close()
        except Exception:
            pass
        try:
            paths.stop_path.unlink(missing_ok=True)
        except Exception:
            pass
        if read_syslog_snmp_monitor_status(profile_id).state != "error":
            stats = monitor_storage_stats(profile_id)
            write_syslog_snmp_monitor_status(
                profile_id,
                SyslogSnmpMonitorStatus(
                    state="stopped",
                    pid=None,
                    message="Collector stopped.",
                    bind_host=config.bind_host,
                    listeners=[],
                    event_count=stats.live_event_count,
                    alert_count=stats.notification_count,
                    last_event_at=stats.newest_live_event_at,
                ),
            )


def parse_syslog_message(payload: bytes | str, *, source_ip: str = "", source_port: int = 0, listener: str = "", transport: str = "") -> dict[str, object]:
    text = payload.decode("utf-8", errors="replace") if isinstance(payload, bytes) else str(payload)
    cleaned = text.strip("\r\n\0")
    now = _isoformat(datetime.now(UTC))
    event: dict[str, object] = {
        "id": 0,
        "received_ts": now,
        "event_ts": "",
        "source_ip": source_ip,
        "source_port": int(source_port) if source_port else 0,
        "source_host": source_ip,
        "listener": listener,
        "protocol": "syslog",
        "transport": transport,
        "facility": None,
        "facility_name": "",
        "severity": None,
        "severity_name": "",
        "syslog_hostname": "",
        "app_name": "",
        "procid": "",
        "msgid": "",
        "structured_data": "",
        "message_text": cleaned,
        "snmp_version": "",
        "snmp_security_name": "",
        "snmp_community": "",
        "snmp_user": "",
        "snmp_engine_id": "",
        "snmp_context_name": "",
        "notification_oid": "",
        "enterprise_oid": "",
        "snmp_uptime": "",
        "varbind_summary": "",
        "alerted": False,
        "raw_payload": cleaned,
    }
    pri, remainder = _extract_syslog_pri(cleaned)
    if pri is not None:
        event["facility"] = pri // 8
        event["facility_name"] = _SYSLOG_FACILITY_NAMES.get(pri // 8, str(pri // 8))
        event["severity"] = pri % 8
        event["severity_name"] = _SYSLOG_SEVERITY_NAMES.get(pri % 8, str(pri % 8))
    if _parse_rfc5424_syslog(remainder, event):
        return event
    if _parse_rfc3164_syslog(remainder, event):
        return event
    event["event_ts"] = now
    return event


def parse_snmp_notification(
    var_binds: list[tuple[object, object]],
    *,
    source_ip: str = "",
    source_port: int = 0,
    security_name: object = "",
    security_model: object = 0,
    context_engine_id: object = "",
    context_name: object = "",
    listener: str = "snmp",
    transport: str = "udp",
) -> dict[str, object]:
    model = _coerce_int(security_model, 0)
    snmp_version = {1: "v1", 2: "v2c", 3: "v3"}.get(model, "unknown")
    security_name_text = _safe_text(security_name)
    community = security_name_text if snmp_version in {"v1", "v2c"} else ""
    user = security_name_text if snmp_version == "v3" else ""
    notification_oid = ""
    enterprise_oid = ""
    uptime = ""
    varbind_parts: list[str] = []
    for oid, value in var_binds:
        oid_text = str(oid)
        value_text = _safe_text(value)
        varbind_parts.append(f"{oid_text}={value_text}")
        if oid_text.endswith("1.3.6.1.6.3.1.1.4.1.0") or oid_text.endswith("snmpTrapOID.0"):
            notification_oid = value_text
        elif oid_text.endswith("1.3.6.1.6.3.1.1.4.3.0") or oid_text.endswith("snmpTrapEnterprise.0"):
            enterprise_oid = value_text
        elif oid_text.endswith("1.3.6.1.2.1.1.3.0") or oid_text.endswith("sysUpTime.0"):
            uptime = value_text
    return {
        "id": 0,
        "received_ts": _isoformat(datetime.now(UTC)),
        "event_ts": "",
        "source_ip": source_ip,
        "source_port": source_port,
        "source_host": source_ip,
        "listener": listener,
        "protocol": "snmp",
        "transport": transport,
        "facility": None,
        "facility_name": "",
        "severity": None,
        "severity_name": "",
        "syslog_hostname": "",
        "app_name": "",
        "procid": "",
        "msgid": "",
        "structured_data": "",
        "message_text": "",
        "snmp_version": snmp_version,
        "snmp_security_name": security_name_text,
        "snmp_community": community,
        "snmp_user": user,
        "snmp_engine_id": _safe_hex(context_engine_id),
        "snmp_context_name": _safe_text(context_name),
        "notification_oid": notification_oid,
        "enterprise_oid": enterprise_oid,
        "snmp_uptime": uptime,
        "varbind_summary": "; ".join(varbind_parts),
        "alerted": False,
        "raw_payload": "; ".join(varbind_parts),
    }


def normalize_monitor_profile_config(raw: dict[str, object]) -> dict[str, object]:
    return validate_syslog_snmp_monitor_config(SyslogSnmpMonitorConfig.from_dict(raw)).to_dict()


class _MonitorWriter:
    def __init__(self, profile_id: str, config: SyslogSnmpMonitorConfig) -> None:
        self._profile_id = profile_id
        self._config = config
        self._connection = sqlite3.connect(syslog_snmp_monitor_profile_paths(profile_id).db_path, check_same_thread=False)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._lock = threading.Lock()
        self._rules = [MonitorAlertRule.from_dict(item) for item in config.alert_rules if isinstance(item, dict)]

    def write(self, event: dict[str, object]) -> int:
        payload = _normalized_event(event)
        notifications = _notifications_for_event(payload, self._rules)
        payload["alerted"] = bool(notifications)
        payload["notifications"] = notifications
        with self._lock:
            event_id = insert_monitor_event(self._profile_id, payload, connection=self._connection)
            self._connection.commit()
        return event_id

    def close(self) -> None:
        with self._lock:
            self._connection.close()


class _BaseRuntimeListener:
    def __init__(self) -> None:
        self.error = ""

    def start(self) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError


class _SyslogUdpHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        data = self.request[0]
        sock = self.request[1]
        del sock
        server = self.server
        writer = getattr(server, "writer", None)
        listener_name = getattr(server, "listener_name", "syslog-udp")
        transport = getattr(server, "transport_name", "udp")
        if writer is None:
            return
        event = parse_syslog_message(
            data,
            source_ip=str(self.client_address[0]),
            source_port=int(self.client_address[1]),
            listener=listener_name,
            transport=transport,
        )
        writer.write(event)


class _ThreadedUdpServer(socketserver.ThreadingMixIn, socketserver.UDPServer):
    allow_reuse_address = True
    daemon_threads = True


class _SyslogUdpListener(_BaseRuntimeListener):
    def __init__(self, bind_host: str, port: int, listener_name: str, writer: _MonitorWriter) -> None:
        super().__init__()
        self._server = _ThreadedUdpServer((bind_host, port), _SyslogUdpHandler)
        self._server.writer = writer
        self._server.listener_name = listener_name
        self._server.transport_name = "udp"
        self._thread = threading.Thread(target=self._run, name=f"snakesh-{listener_name}", daemon=True)

    def _run(self) -> None:
        try:
            self._server.serve_forever(poll_interval=0.5)
        except Exception as exc:  # noqa: BLE001
            self.error = str(exc)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        try:
            self._server.shutdown()
            self._server.server_close()
        except Exception:
            pass
        self._thread.join(timeout=2.0)


class _ThreadedTcpServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


class _ThreadedTlsServer(_ThreadedTcpServer):
    def __init__(self, server_address, RequestHandlerClass, *, context: ssl.SSLContext):
        self._ssl_context = context
        super().__init__(server_address, RequestHandlerClass)

    def get_request(self):  # type: ignore[override]
        socket_obj, client_address = super().get_request()
        wrapped = self._ssl_context.wrap_socket(socket_obj, server_side=True)
        return wrapped, client_address


class _SyslogTcpHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        writer = getattr(self.server, "writer", None)
        listener_name = getattr(self.server, "listener_name", "syslog-tcp")
        transport = getattr(self.server, "transport_name", "tcp")
        if writer is None:
            return
        buffer = b""
        self.request.settimeout(1.0)
        while True:
            try:
                chunk = self.request.recv(4096)
            except socket.timeout:
                continue
            except Exception:
                break
            if not chunk:
                break
            buffer += chunk
            frames, buffer = _extract_syslog_frames(buffer)
            for frame in frames:
                if not frame.strip():
                    continue
                event = parse_syslog_message(
                    frame,
                    source_ip=str(self.client_address[0]),
                    source_port=int(self.client_address[1]),
                    listener=listener_name,
                    transport=transport,
                )
                writer.write(event)
        if buffer.strip():
            event = parse_syslog_message(
                buffer,
                source_ip=str(self.client_address[0]),
                source_port=int(self.client_address[1]),
                listener=listener_name,
                transport=transport,
            )
            writer.write(event)


class _SyslogTcpListener(_BaseRuntimeListener):
    def __init__(self, bind_host: str, port: int, listener_name: str, writer: _MonitorWriter) -> None:
        super().__init__()
        self._server = _ThreadedTcpServer((bind_host, port), _SyslogTcpHandler)
        self._server.writer = writer
        self._server.listener_name = listener_name
        self._server.transport_name = "tcp"
        self._thread = threading.Thread(target=self._run, name=f"snakesh-{listener_name}", daemon=True)

    def _run(self) -> None:
        try:
            self._server.serve_forever(poll_interval=0.5)
        except Exception as exc:  # noqa: BLE001
            self.error = str(exc)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        try:
            self._server.shutdown()
            self._server.server_close()
        except Exception:
            pass
        self._thread.join(timeout=2.0)


class _SyslogTlsListener(_BaseRuntimeListener):
    def __init__(
        self,
        bind_host: str,
        port: int,
        listener_name: str,
        writer: _MonitorWriter,
        *,
        cert_file: str,
        key_file: str,
        ca_file: str,
    ) -> None:
        super().__init__()
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile=cert_file, keyfile=key_file)
        if ca_file:
            context.load_verify_locations(cafile=ca_file)
        self._server = _ThreadedTlsServer((bind_host, port), _SyslogTcpHandler, context=context)
        self._server.writer = writer
        self._server.listener_name = listener_name
        self._server.transport_name = "tls"
        self._thread = threading.Thread(target=self._run, name=f"snakesh-{listener_name}", daemon=True)

    def _run(self) -> None:
        try:
            self._server.serve_forever(poll_interval=0.5)
        except Exception as exc:  # noqa: BLE001
            self.error = str(exc)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        try:
            self._server.shutdown()
            self._server.server_close()
        except Exception:
            pass
        self._thread.join(timeout=2.0)


class _SnmpTrapListener(_BaseRuntimeListener):
    def __init__(self, bind_host: str, port: int, writer: _MonitorWriter, config: SyslogSnmpMonitorConfig) -> None:
        super().__init__()
        self._bind_host = bind_host
        self._port = port
        self._writer = writer
        self._config = config
        self._stop_requested = threading.Event()
        self._thread = threading.Thread(target=self._run, name="snakesh-snmp-trap", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop_requested.set()
        self._thread.join(timeout=2.0)

    def _run(self) -> None:
        try:
            modules = _load_pysnmp_modules()
        except Exception as exc:  # noqa: BLE001
            self.error = str(exc)
            return

        engine = modules["engine"]
        config = modules["config"]
        ntfrcv = modules["ntfrcv"]
        transport = modules["transport"]
        loop: asyncio.AbstractEventLoop | None = None
        try:
            if _snmp_transport_requires_event_loop(transport):
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            snmp_engine = engine.SnmpEngine()
            domain_name = _snmp_transport_domain_name(transport)
            server_transport = _snmp_open_server_transport(
                _snmp_create_udp_transport(transport, loop=loop),
                (self._bind_host, self._port),
            )
            _snmp_callable(config, "add_transport", "addTransport")(snmp_engine, domain_name, server_transport)
            for community in _split_csv_tokens(self._config.snmp_communities):
                if not community:
                    continue
                _snmp_callable(config, "add_v1_system", "addV1System")(snmp_engine, community, community)
                for model in (1, 2):
                    try:
                        _snmp_callable(config, "add_vacm_user", "addVacmUser")(
                            snmp_engine,
                            model,
                            community,
                            "noAuthNoPriv",
                            (1, 3, 6),
                            (1, 3, 6),
                        )
                    except Exception:
                        pass
            if self._config.snmp_v3_enabled:
                for raw in self._config.snmp_v3_users:
                    if not isinstance(raw, dict):
                        continue
                    user = MonitorSnmpV3User.from_dict(raw)
                    if not user.username:
                        continue
                    kwargs = _snmp_v3_user_kwargs(config, user)
                    _snmp_callable(config, "add_v3_user", "addV3User")(snmp_engine, user.username, **kwargs)
                    try:
                        _snmp_callable(config, "add_vacm_user", "addVacmUser")(
                            snmp_engine,
                            3,
                            user.username,
                            "authPriv",
                            (1, 3, 6),
                            (1, 3, 6),
                        )
                    except Exception:
                        pass

            def _callback(snmpEngine, stateReference, contextEngineId, contextName, varBinds, cbCtx):  # noqa: N803
                del cbCtx
                del stateReference
                observer = getattr(snmpEngine, "observer", None)
                execution = {}
                if observer is not None:
                    try:
                        execution = _snmp_observer_execution_context(observer, "rfc3412.receiveMessage:request")
                    except Exception:
                        execution = {}
                transport_address = execution.get("transportAddress") if isinstance(execution, dict) else None
                source_ip = ""
                source_port = 0
                if isinstance(transport_address, tuple) and transport_address:
                    source_ip = str(transport_address[0])
                    if len(transport_address) > 1:
                        try:
                            source_port = int(transport_address[1])
                        except Exception:
                            source_port = 0
                security_name = _safe_text(execution.get("securityName")) if isinstance(execution, dict) else ""
                security_model = _coerce_int(execution.get("securityModel"), 0) if isinstance(execution, dict) else 0
                event = parse_snmp_notification(
                    [(oid, value) for oid, value in varBinds],
                    source_ip=source_ip,
                    source_port=source_port,
                    security_name=security_name,
                    security_model=security_model,
                    context_engine_id=contextEngineId,
                    context_name=contextName,
                )
                self._writer.write(event)

            ntfrcv.NotificationReceiver(snmp_engine, _callback)
            dispatcher = _snmp_dispatcher(snmp_engine)
            _snmp_callable(dispatcher, "job_started", "jobStarted")(1)
            while not self._stop_requested.is_set():
                _snmp_callable(dispatcher, "run_dispatcher", "runDispatcher")(timeout=0.5)
            try:
                _snmp_callable(dispatcher, "close_dispatcher", "closeDispatcher")()
            except Exception:
                pass
        except Exception as exc:  # noqa: BLE001
            self.error = str(exc)
        finally:
            if loop is not None:
                try:
                    asyncio.set_event_loop(None)
                except Exception:
                    pass
                try:
                    if not loop.is_closed():
                        loop.close()
                except Exception:
                    pass


def _snmp_transport_requires_event_loop(transport_module: object) -> bool:
    module_name = str(getattr(transport_module, "__name__", "")).lower()
    return ".asyncio." in module_name


def _snmp_transport_domain_name(transport_module: object) -> object:
    domain_name = getattr(transport_module, "DOMAIN_NAME", None)
    if domain_name is None:
        domain_name = getattr(transport_module, "domainName", None)
    if domain_name is None:
        raise ValueError("SNMP transport module does not expose a transport domain.")
    return domain_name


def _snmp_create_udp_transport(transport_module: object, *, loop: asyncio.AbstractEventLoop | None = None):
    transport_factory = getattr(transport_module, "UdpTransport", None)
    if not callable(transport_factory):
        raise ValueError("SNMP transport module does not expose UdpTransport.")
    if loop is not None:
        try:
            return transport_factory(loop=loop)
        except TypeError:
            pass
    return transport_factory()


def _snmp_open_server_transport(transport_instance: object, bind_address: tuple[str, int]):
    return _snmp_callable(transport_instance, "open_server_mode", "openServerMode")(bind_address)


def _snmp_dispatcher(snmp_engine: object) -> object:
    dispatcher = getattr(snmp_engine, "transport_dispatcher", None)
    if dispatcher is None:
        dispatcher = getattr(snmp_engine, "transportDispatcher", None)
    if dispatcher is None:
        raise ValueError("SNMP engine did not expose a transport dispatcher.")
    return dispatcher


def _snmp_observer_execution_context(observer: object, execution_point: str) -> dict[str, object]:
    getter = _snmp_callable(observer, "get_execution_context", "getExecutionContext")
    context = getter(execution_point)
    return context if isinstance(context, dict) else {}


def _snmp_callable(target: object, *names: str) -> Callable[..., Any]:
    for name in names:
        candidate = getattr(target, name, None)
        if callable(candidate):
            return candidate
    joined = ", ".join(names)
    raise ValueError(f"Expected one of [{joined}] on SNMP object {target!r}")


class _ListWriter:
    def __init__(self, parts: list[str]) -> None:
        self._parts = parts

    def write(self, value: str) -> int:
        self._parts.append(value)
        return len(value)


def _live_query_for_filters(filters: MonitorQueryFilters, *, limit: int) -> tuple[str, list[object]]:
    clauses = ["1 = 1"]
    params: list[object] = []
    if filters.alerted_only:
        clauses.append("alerted = 1")
    if filters.listener:
        clauses.append("listener = ?")
        params.append(filters.listener)
    if filters.protocol:
        clauses.append("protocol = ?")
        params.append(filters.protocol)
    if filters.transport:
        clauses.append("transport = ?")
        params.append(filters.transport)
    if filters.severity_name:
        clauses.append("severity_name = ?")
        params.append(filters.severity_name)
    if filters.facility_name:
        clauses.append("facility_name = ?")
        params.append(filters.facility_name)
    if filters.syslog_hostname:
        clauses.append("LOWER(syslog_hostname) LIKE ?")
        params.append(f"%{filters.syslog_hostname.lower()}%")
    if filters.app_name:
        clauses.append("LOWER(app_name) LIKE ?")
        params.append(f"%{filters.app_name.lower()}%")
    if filters.procid:
        clauses.append("LOWER(procid) LIKE ?")
        params.append(f"%{filters.procid.lower()}%")
    if filters.msgid:
        clauses.append("LOWER(msgid) LIKE ?")
        params.append(f"%{filters.msgid.lower()}%")
    if filters.snmp_version:
        clauses.append("snmp_version = ?")
        params.append(filters.snmp_version)
    if filters.snmp_security_name:
        clauses.append("LOWER(snmp_security_name) LIKE ?")
        params.append(f"%{filters.snmp_security_name.lower()}%")
    if filters.notification_oid:
        clauses.append("LOWER(notification_oid) LIKE ?")
        params.append(f"%{filters.notification_oid.lower()}%")
    if filters.enterprise_oid:
        clauses.append("LOWER(enterprise_oid) LIKE ?")
        params.append(f"%{filters.enterprise_oid.lower()}%")
    if filters.start_ts:
        clauses.append("received_ts >= ?")
        params.append(filters.start_ts)
    if filters.end_ts:
        clauses.append("received_ts <= ?")
        params.append(filters.end_ts)
    if filters.source_contains:
        clauses.append("(LOWER(source_ip) LIKE ? OR LOWER(source_host) LIKE ?)")
        needle = f"%{filters.source_contains.lower()}%"
        params.extend([needle, needle])
    if filters.varbind_text:
        clauses.append("LOWER(varbind_summary) LIKE ?")
        params.append(f"%{filters.varbind_text.lower()}%")
    if filters.text and not filters.use_regex:
        needle = filters.text if filters.case_sensitive else filters.text.lower()
        if filters.case_sensitive:
            clauses.append(
                "("
                "message_text LIKE ? OR raw_payload LIKE ? OR app_name LIKE ? OR notification_oid LIKE ? OR varbind_summary LIKE ?"
                ")"
            )
            params.extend([f"%{needle}%"] * 5)
        else:
            clauses.append(
                "("
                "LOWER(message_text) LIKE ? OR LOWER(raw_payload) LIKE ? OR LOWER(app_name) LIKE ? "
                "OR LOWER(notification_oid) LIKE ? OR LOWER(varbind_summary) LIKE ?"
                ")"
            )
            params.extend([f"%{needle.lower()}%"] * 5)
    query = (
        "SELECT * FROM events "
        f"WHERE {' AND '.join(clauses)} "
        "ORDER BY received_ts DESC, id DESC "
        "LIMIT ?"
    )
    params.append(max(1, int(limit)))
    return query, params


def _read_archived_events(archives_root: Path, filters: MonitorQueryFilters, *, limit: int) -> list[dict[str, object]]:
    if not archives_root.exists():
        return []
    rows: list[dict[str, object]] = []
    archive_files = sorted(
        (path for path in archives_root.rglob("*.jsonl.gz") if path.is_file()),
        key=lambda path: path.stat().st_mtime if path.exists() else 0.0,
        reverse=True,
    )
    for path in archive_files:
        try:
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                for line in handle:
                    payload = json.loads(line)
                    if not isinstance(payload, dict):
                        continue
                    rows.append(payload)
                    if len(rows) >= max(limit * 5, limit):
                        break
        except Exception:
            continue
        if len(rows) >= max(limit * 5, limit):
            break
    return [row for row in rows if _event_matches_filters(row, filters)][: max(1, limit)]


def _event_matches_filters(row: dict[str, object], filters: MonitorQueryFilters) -> bool:
    if filters.alerted_only and not bool(row.get("alerted", False)):
        return False
    if filters.listener and str(row.get("listener", "")).strip() != filters.listener:
        return False
    if filters.protocol and str(row.get("protocol", "")).strip() != filters.protocol:
        return False
    if filters.transport and str(row.get("transport", "")).strip() != filters.transport:
        return False
    if filters.severity_name and str(row.get("severity_name", "")).strip() != filters.severity_name:
        return False
    if filters.facility_name and str(row.get("facility_name", "")).strip() != filters.facility_name:
        return False
    if filters.start_ts and str(row.get("received_ts", "")).strip() < filters.start_ts:
        return False
    if filters.end_ts and str(row.get("received_ts", "")).strip() > filters.end_ts:
        return False
    if filters.source_contains and not _filter_text_contains(
        f"{row.get('source_ip', '')} {row.get('source_host', '')}",
        filters.source_contains,
    ):
        return False
    if filters.syslog_hostname and not _filter_text_contains(str(row.get("syslog_hostname", "")), filters.syslog_hostname):
        return False
    if filters.app_name and not _filter_text_contains(str(row.get("app_name", "")), filters.app_name):
        return False
    if filters.procid and not _filter_text_contains(str(row.get("procid", "")), filters.procid):
        return False
    if filters.msgid and not _filter_text_contains(str(row.get("msgid", "")), filters.msgid):
        return False
    if filters.snmp_version and str(row.get("snmp_version", "")).strip() != filters.snmp_version:
        return False
    if filters.snmp_security_name and not _filter_text_contains(str(row.get("snmp_security_name", "")), filters.snmp_security_name):
        return False
    if filters.notification_oid and not _filter_text_contains(str(row.get("notification_oid", "")), filters.notification_oid):
        return False
    if filters.enterprise_oid and not _filter_text_contains(str(row.get("enterprise_oid", "")), filters.enterprise_oid):
        return False
    if filters.varbind_text and not _filter_text_contains(str(row.get("varbind_summary", "")), filters.varbind_text):
        return False
    if filters.text:
        haystack = " ".join(
            str(row.get(key, ""))
            for key in (
                "message_text",
                "raw_payload",
                "app_name",
                "notification_oid",
                "varbind_summary",
                "syslog_hostname",
                "source_ip",
            )
        )
        if filters.use_regex:
            flags = 0 if filters.case_sensitive else re.IGNORECASE
            try:
                if re.search(filters.text, haystack, flags=flags) is None:
                    return False
            except re.error:
                return False
        else:
            left = haystack if filters.case_sensitive else haystack.lower()
            needle = filters.text if filters.case_sensitive else filters.text.lower()
            if needle not in left:
                return False
    return True


def _filter_text_contains(value: str, needle: str) -> bool:
    return needle.lower() in value.lower()


def _decode_event_row(row: sqlite3.Row) -> dict[str, object]:
    details_json = str(row["details_json"] or "")
    if details_json:
        try:
            payload = json.loads(details_json)
            if isinstance(payload, dict):
                payload["id"] = int(row["id"])
                payload["alerted"] = bool(row["alerted"])
                return payload
        except Exception:
            pass
    return {
        "id": int(row["id"]),
        "received_ts": str(row["received_ts"] or ""),
        "event_ts": str(row["event_ts"] or ""),
        "source_ip": str(row["source_ip"] or ""),
        "source_port": _coerce_optional_int(row["source_port"]) or 0,
        "source_host": str(row["source_host"] or ""),
        "listener": str(row["listener"] or ""),
        "protocol": str(row["protocol"] or ""),
        "transport": str(row["transport"] or ""),
        "facility": _coerce_optional_int(row["facility"]),
        "facility_name": str(row["facility_name"] or ""),
        "severity": _coerce_optional_int(row["severity"]),
        "severity_name": str(row["severity_name"] or ""),
        "syslog_hostname": str(row["syslog_hostname"] or ""),
        "app_name": str(row["app_name"] or ""),
        "procid": str(row["procid"] or ""),
        "msgid": str(row["msgid"] or ""),
        "structured_data": str(row["structured_data"] or ""),
        "message_text": str(row["message_text"] or ""),
        "snmp_version": str(row["snmp_version"] or ""),
        "snmp_security_name": str(row["snmp_security_name"] or ""),
        "snmp_community": str(row["snmp_community"] or ""),
        "snmp_user": str(row["snmp_user"] or ""),
        "snmp_engine_id": str(row["snmp_engine_id"] or ""),
        "snmp_context_name": str(row["snmp_context_name"] or ""),
        "notification_oid": str(row["notification_oid"] or ""),
        "enterprise_oid": str(row["enterprise_oid"] or ""),
        "snmp_uptime": str(row["snmp_uptime"] or ""),
        "varbind_summary": str(row["varbind_summary"] or ""),
        "alerted": bool(row["alerted"]),
        "raw_payload": str(row["raw_payload"] or ""),
    }


def _normalized_event(event: dict[str, object]) -> dict[str, object]:
    payload = dict(event)
    payload["received_ts"] = str(payload.get("received_ts", "")).strip() or _isoformat(datetime.now(UTC))
    payload["event_ts"] = str(payload.get("event_ts", "")).strip()
    payload["source_ip"] = str(payload.get("source_ip", "")).strip()
    payload["source_port"] = max(0, _coerce_int(payload.get("source_port", 0), 0))
    payload["source_host"] = str(payload.get("source_host", "")).strip() or payload["source_ip"]
    payload["listener"] = str(payload.get("listener", "")).strip()
    payload["protocol"] = str(payload.get("protocol", "")).strip().lower()
    payload["transport"] = str(payload.get("transport", "")).strip().lower()
    payload["facility"] = _coerce_optional_int(payload.get("facility"))
    payload["severity"] = _coerce_optional_int(payload.get("severity"))
    payload["facility_name"] = str(payload.get("facility_name", "")).strip()
    payload["severity_name"] = str(payload.get("severity_name", "")).strip()
    payload["syslog_hostname"] = str(payload.get("syslog_hostname", "")).strip()
    payload["app_name"] = str(payload.get("app_name", "")).strip()
    payload["procid"] = str(payload.get("procid", "")).strip()
    payload["msgid"] = str(payload.get("msgid", "")).strip()
    payload["structured_data"] = str(payload.get("structured_data", "")).strip()
    payload["message_text"] = str(payload.get("message_text", "")).strip()
    payload["snmp_version"] = str(payload.get("snmp_version", "")).strip()
    payload["snmp_security_name"] = str(payload.get("snmp_security_name", "")).strip()
    payload["snmp_community"] = str(payload.get("snmp_community", "")).strip()
    payload["snmp_user"] = str(payload.get("snmp_user", "")).strip()
    payload["snmp_engine_id"] = str(payload.get("snmp_engine_id", "")).strip()
    payload["snmp_context_name"] = str(payload.get("snmp_context_name", "")).strip()
    payload["notification_oid"] = str(payload.get("notification_oid", "")).strip()
    payload["enterprise_oid"] = str(payload.get("enterprise_oid", "")).strip()
    payload["snmp_uptime"] = str(payload.get("snmp_uptime", "")).strip()
    payload["varbind_summary"] = str(payload.get("varbind_summary", "")).strip()
    payload["raw_payload"] = str(payload.get("raw_payload", "")).strip()
    payload["alerted"] = bool(payload.get("alerted", False))
    return payload


def _extract_syslog_pri(text: str) -> tuple[int | None, str]:
    if not text.startswith("<"):
        return None, text
    match = re.match(r"^<(\d{1,3})>(.*)$", text, flags=re.DOTALL)
    if not match:
        return None, text
    try:
        pri = int(match.group(1))
    except ValueError:
        return None, text
    if pri < 0 or pri > 191:
        return None, text
    return pri, match.group(2).lstrip()


def _parse_rfc5424_syslog(text: str, event: dict[str, object]) -> bool:
    match = re.match(
        r"^(?P<version>\d+)\s+(?P<timestamp>\S+)\s+(?P<host>\S+)\s+(?P<app>\S+)\s+(?P<procid>\S+)\s+(?P<msgid>\S+)\s+(?P<rest>.*)$",
        text,
        flags=re.DOTALL,
    )
    if not match:
        return False
    rest = match.group("rest")
    structured_data = ""
    message = ""
    if rest.startswith("-"):
        message = rest[1:].lstrip()
    elif rest.startswith("["):
        structured_data, message = _split_rfc5424_structured_data(rest)
    else:
        return False
    event["event_ts"] = _normalize_timestamp(match.group("timestamp"))
    event["syslog_hostname"] = _dash_to_blank(match.group("host"))
    event["app_name"] = _dash_to_blank(match.group("app"))
    event["procid"] = _dash_to_blank(match.group("procid"))
    event["msgid"] = _dash_to_blank(match.group("msgid"))
    event["structured_data"] = structured_data
    event["message_text"] = message
    return True


def _parse_rfc3164_syslog(text: str, event: dict[str, object]) -> bool:
    match = re.match(
        r"^(?P<timestamp>[A-Z][a-z]{2}\s+\d{1,2}\s+\d\d:\d\d:\d\d)\s+(?P<host>\S+)\s+(?P<tag>[^:]+):?\s*(?P<message>.*)$",
        text,
        flags=re.DOTALL,
    )
    if not match:
        return False
    tag = match.group("tag").strip()
    procid = ""
    app_name = tag
    proc_match = re.match(r"^(?P<name>[^\[]+)\[(?P<pid>[^\]]+)\]$", tag)
    if proc_match:
        app_name = proc_match.group("name").strip()
        procid = proc_match.group("pid").strip()
    event["event_ts"] = _normalize_rfc3164_timestamp(match.group("timestamp"))
    event["syslog_hostname"] = match.group("host").strip()
    event["app_name"] = app_name
    event["procid"] = procid
    event["message_text"] = match.group("message").strip()
    return True


def _split_rfc5424_structured_data(rest: str) -> tuple[str, str]:
    depth = 0
    end_index = -1
    escaped = False
    for index, char in enumerate(rest):
        if char == "\\" and depth > 0:
            escaped = not escaped
            continue
        if char == "[" and not escaped:
            depth += 1
        elif char == "]" and not escaped:
            depth = max(0, depth - 1)
            if depth == 0:
                end_index = index
        escaped = False
    if end_index < 0:
        return "", rest
    structured = rest[: end_index + 1].strip()
    message = rest[end_index + 1 :].strip()
    return structured, message


def _extract_syslog_frames(buffer: bytes) -> tuple[list[bytes], bytes]:
    frames: list[bytes] = []
    remaining = buffer
    while remaining:
        octet_match = re.match(rb"^(\d{1,10})\s", remaining)
        if octet_match:
            length = int(octet_match.group(1))
            prefix_len = len(octet_match.group(0))
            if len(remaining) < prefix_len + length:
                break
            frames.append(remaining[prefix_len : prefix_len + length])
            remaining = remaining[prefix_len + length :]
            continue
        newline_index = remaining.find(b"\n")
        if newline_index < 0:
            break
        frames.append(remaining[:newline_index].rstrip(b"\r"))
        remaining = remaining[newline_index + 1 :]
    return frames, remaining


def _notifications_for_event(event: dict[str, object], rules: list[MonitorAlertRule]) -> list[dict[str, object]]:
    notifications: list[dict[str, object]] = []
    for rule in rules:
        if not rule.enabled or not _alert_rule_matches(rule, event):
            continue
        body = str(event.get("message_text", "")).strip() or str(event.get("varbind_summary", "")).strip() or str(event.get("raw_payload", "")).strip()
        notifications.append(
            {
                "title": rule.name,
                "body": body[:500],
                "play_sound": rule.sound,
                "popup": rule.popup,
            }
        )
    return [item for item in notifications if item.get("popup") or item.get("play_sound")]


def _alert_rule_matches(rule: MonitorAlertRule, event: dict[str, object]) -> bool:
    protocol = str(event.get("protocol", "")).strip().lower()
    if rule.protocol != "any" and protocol != rule.protocol:
        return False
    if rule.severity_at_least and protocol == "syslog":
        event_severity = _coerce_optional_int(event.get("severity"))
        threshold = _SEVERITY_ORDER.get(rule.severity_at_least.lower())
        if event_severity is None or threshold is None or event_severity > threshold:
            return False
    if rule.source_contains and not _rule_text_matches(rule, f"{event.get('source_ip', '')} {event.get('source_host', '')}", rule.source_contains):
        return False
    if rule.app_contains and not _rule_text_matches(rule, str(event.get("app_name", "")), rule.app_contains):
        return False
    if rule.trap_oid_contains and not _rule_text_matches(rule, str(event.get("notification_oid", "")), rule.trap_oid_contains):
        return False
    if rule.enterprise_oid_contains and not _rule_text_matches(rule, str(event.get("enterprise_oid", "")), rule.enterprise_oid_contains):
        return False
    if rule.text_contains:
        haystack = " ".join(
            str(event.get(key, ""))
            for key in ("message_text", "raw_payload", "varbind_summary", "structured_data")
        )
        if not _rule_text_matches(rule, haystack, rule.text_contains):
            return False
    return True


def _rule_text_matches(rule: MonitorAlertRule, haystack: str, needle: str) -> bool:
    if rule.use_regex:
        try:
            return re.search(needle, haystack, flags=re.IGNORECASE) is not None
        except re.error:
            return False
    return needle.lower() in haystack.lower()


def _normalize_timestamp(value: str) -> str:
    cleaned = value.strip()
    if not cleaned or cleaned == "-":
        return ""
    try:
        if cleaned.endswith("Z"):
            cleaned = cleaned[:-1] + "+00:00"
        return _isoformat(datetime.fromisoformat(cleaned))
    except Exception:
        return cleaned


def _normalize_rfc3164_timestamp(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", value.strip())
    now = datetime.now(UTC)
    try:
        parsed = datetime.strptime(f"{now.year} {cleaned}", "%Y %b %d %H:%M:%S")
        return _isoformat(parsed.replace(tzinfo=UTC))
    except Exception:
        return _isoformat(now)


def _safe_profile_id(profile_id: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", profile_id.strip())
    return cleaned or "default"


def _expand_path(value: object) -> str:
    cleaned = str(value or "").strip()
    if not cleaned:
        return ""
    return str(Path(cleaned).expanduser())


def _normalize_visible_columns(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return list(DEFAULT_EVENT_COLUMNS)
    seen: set[str] = set()
    result: list[str] = []
    for item in raw:
        name = str(item).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        result.append(name)
    return result or list(DEFAULT_EVENT_COLUMNS)


def _normalize_choice(value: str, choices: set[str], default: str) -> str:
    cleaned = value.strip().lower()
    return cleaned if cleaned in choices else default


def _normalize_alert_severity(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        return ""
    lowered = cleaned.lower()
    return _SYSLOG_SEVERITY_NAMES.get(_SEVERITY_ORDER.get(lowered, -1), "")


def _normalize_port(value: object, default: int) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError):
        port = default
    return max(1, min(65535, port))


def _coerce_int(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_optional_int(value: object) -> int | None:
    try:
        return int(value) if value is not None and str(value).strip() != "" else None
    except (TypeError, ValueError):
        return None


def _split_csv_tokens(text: str) -> list[str]:
    return [token.strip() for token in text.split(",") if token.strip()]


def _dash_to_blank(value: str) -> str:
    cleaned = value.strip()
    return "" if cleaned == "-" else cleaned


def _isoformat(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _next_archive_path(archives_root: Path) -> Path:
    now = datetime.now(UTC)
    directory = archives_root / now.strftime("%Y") / now.strftime("%m")
    filename = f"events-{now.strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:8]}.jsonl.gz"
    return directory / filename


def _remove_empty_directories(root: Path) -> None:
    if not root.exists():
        return
    directories = sorted((path for path in root.rglob("*") if path.is_dir()), key=lambda path: len(path.parts), reverse=True)
    for path in directories:
        try:
            path.rmdir()
        except OSError:
            continue


def _platform_name(platform_name: str | None = None) -> str:
    token = (platform_name or platform.system()).strip().lower()
    if token.startswith("win"):
        return "windows"
    return token


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if _platform_name() == "windows":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            process_query_limited_information = 0x1000
            process_query_information = 0x0400
            still_active = 259
            handle = 0
            for access in (process_query_limited_information, process_query_information):
                handle = kernel32.OpenProcess(access, False, pid)
                if handle:
                    break
                if ctypes.get_last_error() == 5:
                    return True
            if not handle:
                return False
            try:
                exit_code = ctypes.c_ulong()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return True
                return int(exit_code.value) == still_active
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _escape_applescript(command: str) -> str:
    return command.replace("\\", "\\\\").replace('"', '\\"')


def _event_source_text(row: dict[str, object]) -> str:
    source_ip = str(row.get("source_ip", "")).strip()
    source_host = str(row.get("source_host", "")).strip()
    source_port = _coerce_optional_int(row.get("source_port"))
    base = source_host or source_ip
    if source_host and source_ip and source_host != source_ip:
        base = f"{source_host} ({source_ip})"
    if source_port:
        return f"{base}:{source_port}"
    return base


def _load_pysnmp_modules() -> dict[str, object]:
    try:
        engine = importlib.import_module("pysnmp.entity.engine")
        config = importlib.import_module("pysnmp.entity.config")
        ntfrcv = importlib.import_module("pysnmp.entity.rfc3413.ntfrcv")
    except Exception as exc:  # noqa: BLE001
        raise ValueError("SNMP support requires the pysnmp package to be installed.") from exc
    transport_module_names = (
        "pysnmp.carrier.asyncore.dgram.udp",
        "pysnmp.carrier.asyncio.dgram.udp",
    )
    last_error: Exception | None = None
    for module_name in transport_module_names:
        try:
            transport = importlib.import_module(module_name)
            return {
                "engine": engine,
                "config": config,
                "ntfrcv": ntfrcv,
                "transport": transport,
            }
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            continue
    raise ValueError(f"Unable to import a supported pysnmp UDP transport: {last_error}")


def _snmp_v3_user_kwargs(config_module, user: MonitorSnmpV3User) -> dict[str, object]:
    auth_protocols = {
        "MD5": getattr(config_module, "usmHMACMD5AuthProtocol", None),
        "SHA": getattr(config_module, "usmHMACSHAAuthProtocol", None),
        "SHA224": getattr(config_module, "usmHMAC128SHA224AuthProtocol", None),
        "SHA256": getattr(config_module, "usmHMAC192SHA256AuthProtocol", None),
        "SHA384": getattr(config_module, "usmHMAC256SHA384AuthProtocol", None),
        "SHA512": getattr(config_module, "usmHMAC384SHA512AuthProtocol", None),
    }
    priv_protocols = {
        "DES": getattr(config_module, "usmDESPrivProtocol", None),
        "3DES": getattr(config_module, "usm3DESEDEPrivProtocol", None),
        "AES": getattr(config_module, "usmAesCfb128Protocol", None),
        "AES128": getattr(config_module, "usmAesCfb128Protocol", None),
        "AES192": getattr(config_module, "usmAesCfb192Protocol", None),
        "AES256": getattr(config_module, "usmAesCfb256Protocol", None),
    }
    kwargs: dict[str, object] = {}
    auth_protocol = auth_protocols.get(user.auth_protocol.upper())
    priv_protocol = priv_protocols.get(user.priv_protocol.upper())
    if auth_protocol is not None:
        kwargs["authProtocol"] = auth_protocol
        kwargs["authKey"] = user.auth_password
    if priv_protocol is not None and user.priv_password:
        kwargs["privProtocol"] = priv_protocol
        kwargs["privKey"] = user.priv_password
    return kwargs


def _safe_text(value: object) -> str:
    try:
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)
    except Exception:
        return ""


def _safe_hex(value: object) -> str:
    if isinstance(value, bytes):
        return value.hex()
    return _safe_text(value)
