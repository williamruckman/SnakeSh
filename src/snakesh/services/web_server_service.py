from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import posixpath
import re
import shlex
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
import traceback
from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit
from uuid import uuid4

from aiohttp import ClientSession, ClientTimeout, ClientWSTimeout, TCPConnector, WSMsgType, web
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from snakesh import runtime
from snakesh.core.paths import data_dir


WEB_SERVER_HELPER_FLAG = "--web-server-helper"
_DEFAULT_PROXY_CONNECT_TIMEOUT = 30
_DEFAULT_PROXY_READ_TIMEOUT = 60
_CERTBOT_RENEWAL_WINDOW_DAYS = 7
_HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}
_CONDITIONAL_REQUEST_HEADERS = {
    "if-match",
    "if-none-match",
    "if-modified-since",
    "if-unmodified-since",
    "if-range",
    "range",
}
_MALFORMED_CLOSE_RESPONSE_MARKER = "Data after `Connection: close`"


@dataclass(frozen=True, slots=True)
class WebServerConfig:
    bind_host: str = "127.0.0.1"
    port: int = 8000
    mode: str = "static"
    document_root: str = ""
    index_page: str = ""
    tls_mode: str = "none"
    cert_file: str = ""
    key_file: str = ""
    chain_file: str = ""
    allow_directory_listing: bool = False
    upstream_url: str = ""
    proxy_path_prefix: str = "/"
    proxy_strip_prefix: bool = False
    proxy_preserve_host: bool = True
    proxy_send_x_forwarded: bool = True
    proxy_verify_upstream_tls: bool = True
    proxy_enable_websocket: bool = True
    proxy_connect_timeout: int = _DEFAULT_PROXY_CONNECT_TIMEOUT
    proxy_read_timeout: int = _DEFAULT_PROXY_READ_TIMEOUT
    proxy_extra_headers: str = ""
    certbot_executable: str = "certbot"
    certbot_primary_domain: str = ""
    certbot_additional_domains: str = ""
    certbot_email: str = ""
    certbot_challenge_port: int = 80
    certbot_staging: bool = False

    @property
    def protocol(self) -> str:
        return "https" if self.tls_mode != "none" else "http"

    @property
    def uses_tls(self) -> bool:
        return self.tls_mode != "none"

    def to_dict(self) -> dict[str, object]:
        return {
            "bind_host": self.bind_host,
            "port": self.port,
            "mode": self.mode,
            "document_root": self.document_root,
            "index_page": self.index_page,
            "tls_mode": self.tls_mode,
            "protocol": self.protocol,
            "cert_file": self.cert_file,
            "key_file": self.key_file,
            "chain_file": self.chain_file,
            "generate_self_signed": self.tls_mode == "self_signed",
            "allow_directory_listing": self.allow_directory_listing,
            "upstream_url": self.upstream_url,
            "proxy_path_prefix": self.proxy_path_prefix,
            "proxy_strip_prefix": self.proxy_strip_prefix,
            "proxy_preserve_host": self.proxy_preserve_host,
            "proxy_send_x_forwarded": self.proxy_send_x_forwarded,
            "proxy_verify_upstream_tls": self.proxy_verify_upstream_tls,
            "proxy_enable_websocket": self.proxy_enable_websocket,
            "proxy_connect_timeout": self.proxy_connect_timeout,
            "proxy_read_timeout": self.proxy_read_timeout,
            "proxy_extra_headers": self.proxy_extra_headers,
            "certbot_executable": self.certbot_executable,
            "certbot_primary_domain": self.certbot_primary_domain,
            "certbot_additional_domains": self.certbot_additional_domains,
            "certbot_email": self.certbot_email,
            "certbot_challenge_port": self.certbot_challenge_port,
            "certbot_staging": self.certbot_staging,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "WebServerConfig":
        try:
            port = int(raw.get("port", 8000))
        except (TypeError, ValueError):
            port = 8000
        try:
            proxy_connect_timeout = int(raw.get("proxy_connect_timeout", _DEFAULT_PROXY_CONNECT_TIMEOUT))
        except (TypeError, ValueError):
            proxy_connect_timeout = _DEFAULT_PROXY_CONNECT_TIMEOUT
        try:
            proxy_read_timeout = int(raw.get("proxy_read_timeout", _DEFAULT_PROXY_READ_TIMEOUT))
        except (TypeError, ValueError):
            proxy_read_timeout = _DEFAULT_PROXY_READ_TIMEOUT
        try:
            certbot_challenge_port = int(raw.get("certbot_challenge_port", 80))
        except (TypeError, ValueError):
            certbot_challenge_port = 80

        tls_mode = str(raw.get("tls_mode", "")).strip().lower()
        if tls_mode not in {"none", "manual", "self_signed", "certbot"}:
            protocol = str(raw.get("protocol", "http")).strip().lower() or "http"
            if protocol == "https":
                if bool(raw.get("generate_self_signed", False)):
                    tls_mode = "self_signed"
                else:
                    tls_mode = "manual"
            else:
                tls_mode = "none"

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

        mode = str(raw.get("mode", "static")).strip().lower() or "static"
        if mode not in {"static", "reverse_proxy"}:
            mode = "static"

        proxy_path_prefix = str(raw.get("proxy_path_prefix", "/")).strip() or "/"
        if not proxy_path_prefix.startswith("/"):
            proxy_path_prefix = f"/{proxy_path_prefix}"
        if len(proxy_path_prefix) > 1:
            proxy_path_prefix = proxy_path_prefix.rstrip("/")

        return cls(
            bind_host=str(raw.get("bind_host", "127.0.0.1")).strip() or "127.0.0.1",
            port=port,
            mode=mode,
            document_root=str(raw.get("document_root", "")).strip(),
            index_page=str(raw.get("index_page", "")).strip(),
            tls_mode=tls_mode,
            cert_file=str(raw.get("cert_file", "")).strip(),
            key_file=str(raw.get("key_file", "")).strip(),
            chain_file=str(raw.get("chain_file", "")).strip(),
            allow_directory_listing=bool(raw.get("allow_directory_listing", False)),
            upstream_url=str(raw.get("upstream_url", "")).strip(),
            proxy_path_prefix=proxy_path_prefix,
            proxy_strip_prefix=bool(raw.get("proxy_strip_prefix", False)),
            proxy_preserve_host=bool(raw.get("proxy_preserve_host", True)),
            proxy_send_x_forwarded=bool(raw.get("proxy_send_x_forwarded", True)),
            proxy_verify_upstream_tls=bool(raw.get("proxy_verify_upstream_tls", True)),
            proxy_enable_websocket=bool(raw.get("proxy_enable_websocket", True)),
            proxy_connect_timeout=proxy_connect_timeout,
            proxy_read_timeout=proxy_read_timeout,
            proxy_extra_headers=str(raw.get("proxy_extra_headers", "")).strip(),
            certbot_executable=str(raw.get("certbot_executable", "")).strip() or "certbot",
            certbot_primary_domain=str(raw.get("certbot_primary_domain", "")).strip(),
            certbot_additional_domains=additional_domains,
            certbot_email=str(raw.get("certbot_email", "")).strip(),
            certbot_challenge_port=certbot_challenge_port,
            certbot_staging=bool(raw.get("certbot_staging", False)),
        )


@dataclass(frozen=True, slots=True)
class WebServerStatus:
    state: str = "stopped"
    pid: int | None = None
    url: str = ""
    message: str = ""
    bind_host: str = ""
    port: int = 0
    protocol: str = "http"
    document_root: str = ""
    started_at: str = ""
    log_path: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "state": self.state,
            "pid": self.pid,
            "url": self.url,
            "message": self.message,
            "bind_host": self.bind_host,
            "port": self.port,
            "protocol": self.protocol,
            "document_root": self.document_root,
            "started_at": self.started_at,
            "log_path": self.log_path,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "WebServerStatus":
        pid = raw.get("pid")
        try:
            normalized_pid = int(pid) if pid is not None else None
        except (TypeError, ValueError):
            normalized_pid = None
        return cls(
            state=str(raw.get("state", "stopped")).strip() or "stopped",
            pid=normalized_pid,
            url=str(raw.get("url", "")).strip(),
            message=str(raw.get("message", "")).strip(),
            bind_host=str(raw.get("bind_host", "")).strip(),
            port=int(raw.get("port", 0) or 0),
            protocol=str(raw.get("protocol", "http")).strip() or "http",
            document_root=str(raw.get("document_root", "")).strip(),
            started_at=str(raw.get("started_at", "")).strip(),
            log_path=str(raw.get("log_path", "")).strip(),
        )


@dataclass(frozen=True, slots=True)
class WebServerInstancePaths:
    root: Path
    config_path: Path
    status_path: Path
    log_path: Path
    stop_path: Path
    generated_cert_path: Path
    generated_key_path: Path
    prepared_chain_cert_path: Path


def web_server_instances_root() -> Path:
    root = data_dir() / "web-servers"
    root.mkdir(parents=True, exist_ok=True)
    return root


def web_server_logs_root() -> Path:
    root = data_dir() / "logs" / "web-server"
    root.mkdir(parents=True, exist_ok=True)
    return root


def web_server_certbot_root() -> Path:
    root = data_dir() / "web-server-certbot"
    root.mkdir(parents=True, exist_ok=True)
    return root


def create_web_server_archived_log_path(label: str = "web-server") -> Path:
    timestamp = datetime.now(UTC)
    directory = web_server_logs_root() / timestamp.strftime("%Y") / timestamp.strftime("%m")
    directory.mkdir(parents=True, exist_ok=True)
    safe_label = _safe_log_label(label)
    filename = f"{safe_label}-{timestamp.strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:8]}.log"
    return directory / filename


def prune_web_server_log_files(retention_days: int) -> int:
    base_dir = web_server_logs_root()
    cutoff = time.time() - (max(1, int(retention_days)) * 24 * 60 * 60)
    removed = 0

    for path in base_dir.rglob("*.log"):
        if not path.is_file():
            continue
        try:
            if path.stat().st_mtime >= cutoff:
                continue
        except OSError:
            continue
        try:
            path.unlink()
            removed += 1
        except OSError:
            continue

    try:
        directories = sorted(
            (entry for entry in base_dir.rglob("*") if entry.is_dir()),
            key=lambda entry: len(entry.parts),
            reverse=True,
        )
        for directory in directories:
            try:
                directory.rmdir()
            except OSError:
                continue
    except OSError:
        pass

    return removed


def create_web_server_instance_dir() -> Path:
    instance_dir = web_server_instances_root() / f"{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:10]}"
    instance_dir.mkdir(parents=True, exist_ok=True)
    return instance_dir


def web_server_instance_paths(instance_dir: str | Path) -> WebServerInstancePaths:
    root = Path(instance_dir).expanduser()
    return WebServerInstancePaths(
        root=root,
        config_path=root / "config.json",
        status_path=root / "status.json",
        log_path=root / "server.log",
        stop_path=root / "stop",
        generated_cert_path=root / "generated-cert.pem",
        generated_key_path=root / "generated-key.pem",
        prepared_chain_cert_path=root / "prepared-chain-cert.pem",
    )


def validate_web_server_config(config: WebServerConfig) -> WebServerConfig:
    bind_host = config.bind_host.strip() or "127.0.0.1"
    try:
        ipaddress.ip_address(bind_host)
    except ValueError as exc:
        raise ValueError("Bind address must be a valid IPv4 or IPv6 address.") from exc

    try:
        port = int(config.port)
    except (TypeError, ValueError) as exc:
        raise ValueError("Port must be a number.") from exc
    if port <= 0 or port > 65535:
        raise ValueError("Port must be between 1 and 65535.")

    mode = config.mode.strip().lower() or "static"
    if mode not in {"static", "reverse_proxy"}:
        raise ValueError("Mode must be Static Files or Reverse Proxy.")

    tls_mode = config.tls_mode.strip().lower() or "none"
    if tls_mode not in {"none", "manual", "self_signed", "certbot"}:
        raise ValueError("TLS mode is invalid.")

    proxy_path_prefix = config.proxy_path_prefix.strip() or "/"
    if not proxy_path_prefix.startswith("/"):
        proxy_path_prefix = f"/{proxy_path_prefix}"
    if len(proxy_path_prefix) > 1:
        proxy_path_prefix = proxy_path_prefix.rstrip("/")

    try:
        proxy_connect_timeout = int(config.proxy_connect_timeout)
    except (TypeError, ValueError) as exc:
        raise ValueError("Proxy connect timeout must be a number.") from exc
    try:
        proxy_read_timeout = int(config.proxy_read_timeout)
    except (TypeError, ValueError) as exc:
        raise ValueError("Proxy read timeout must be a number.") from exc
    if proxy_connect_timeout <= 0 or proxy_connect_timeout > 3600:
        raise ValueError("Proxy connect timeout must be between 1 and 3600 seconds.")
    if proxy_read_timeout <= 0 or proxy_read_timeout > 3600:
        raise ValueError("Proxy read timeout must be between 1 and 3600 seconds.")

    try:
        certbot_challenge_port = int(config.certbot_challenge_port)
    except (TypeError, ValueError) as exc:
        raise ValueError("Certbot challenge port must be a number.") from exc
    if certbot_challenge_port <= 0 or certbot_challenge_port > 65535:
        raise ValueError("Certbot challenge port must be between 1 and 65535.")

    document_root = str(Path(config.document_root).expanduser()) if config.document_root.strip() else ""
    cert_file = str(Path(config.cert_file).expanduser()) if config.cert_file.strip() else ""
    key_file = str(Path(config.key_file).expanduser()) if config.key_file.strip() else ""
    chain_file = str(Path(config.chain_file).expanduser()) if config.chain_file.strip() else ""
    upstream_url = config.upstream_url.strip()
    certbot_executable = config.certbot_executable.strip() or "certbot"

    normalized = WebServerConfig(
        bind_host=bind_host,
        port=port,
        mode=mode,
        document_root=document_root,
        index_page=config.index_page.strip(),
        tls_mode=tls_mode,
        cert_file=cert_file,
        key_file=key_file,
        chain_file=chain_file,
        allow_directory_listing=bool(config.allow_directory_listing),
        upstream_url=upstream_url,
        proxy_path_prefix=proxy_path_prefix,
        proxy_strip_prefix=bool(config.proxy_strip_prefix),
        proxy_preserve_host=bool(config.proxy_preserve_host),
        proxy_send_x_forwarded=bool(config.proxy_send_x_forwarded),
        proxy_verify_upstream_tls=bool(config.proxy_verify_upstream_tls),
        proxy_enable_websocket=bool(config.proxy_enable_websocket),
        proxy_connect_timeout=proxy_connect_timeout,
        proxy_read_timeout=proxy_read_timeout,
        proxy_extra_headers=config.proxy_extra_headers.strip(),
        certbot_executable=certbot_executable,
        certbot_primary_domain=config.certbot_primary_domain.strip(),
        certbot_additional_domains=config.certbot_additional_domains.strip(),
        certbot_email=config.certbot_email.strip(),
        certbot_challenge_port=certbot_challenge_port,
        certbot_staging=bool(config.certbot_staging),
    )

    if normalized.mode == "static":
        document_root_path = Path(normalized.document_root).expanduser()
        if not document_root_path.exists() or not document_root_path.is_dir():
            raise ValueError("Document root must point to an existing folder.")
        resolved_index = resolve_index_page(normalized)
        if resolved_index is not None:
            if not resolved_index.exists() or not resolved_index.is_file():
                raise ValueError("Index page must resolve to an existing file.")
            try:
                resolved_index.resolve().relative_to(document_root_path.resolve())
            except ValueError as exc:
                raise ValueError("Index page must live under the selected document root.") from exc

    if normalized.mode == "reverse_proxy":
        if not normalized.upstream_url:
            raise ValueError("Upstream URL is required for reverse proxy mode.")
        parsed_upstream = urlsplit(normalized.upstream_url)
        if parsed_upstream.scheme not in {"http", "https"} or not parsed_upstream.netloc:
            raise ValueError("Upstream URL must be a valid HTTP or HTTPS URL.")

    if normalized.tls_mode == "manual":
        cert_path = Path(normalized.cert_file)
        key_path = Path(normalized.key_file)
        if not cert_path.exists() or not cert_path.is_file():
            raise ValueError("Certificate file must exist for manual TLS.")
        if not key_path.exists() or not key_path.is_file():
            raise ValueError("Key file must exist for manual TLS.")
        if normalized.chain_file:
            chain_path = Path(normalized.chain_file)
            if not chain_path.exists() or not chain_path.is_file():
                raise ValueError("Intermediate / chain certificate file must exist when provided.")
    elif normalized.tls_mode == "certbot":
        if not _resolve_executable_path(normalized.certbot_executable):
            raise ValueError("Certbot executable could not be found.")
        domains = _certbot_domains(normalized)
        if not domains:
            raise ValueError("Certbot requires at least one domain.")
        if not normalized.certbot_email:
            raise ValueError("Certbot email is required.")

    _parse_proxy_extra_headers(normalized.proxy_extra_headers)
    return normalized


def needs_gui_elevation(config: WebServerConfig, *, platform_name: str | None = None) -> bool:
    normalized = validate_web_server_config(config)
    system = _platform_name(platform_name)
    if system not in {"linux", "darwin"}:
        return False
    if normalized.port < 1024:
        return True
    return normalized.tls_mode == "certbot" and normalized.certbot_challenge_port < 1024


def helper_launch_command(instance_dir: str | Path) -> list[str]:
    return runtime.self_launch_command([WEB_SERVER_HELPER_FLAG, str(Path(instance_dir).expanduser())])


def launch_web_server_helper(instance_dir: str | Path) -> list[str]:
    command = helper_launch_command(instance_dir)
    if _platform_name() == "windows":
        creationflags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen(  # noqa: S603
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=creationflags,
        )
    else:
        subprocess.Popen(  # noqa: S603
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=True,
        )
    return command


def launch_web_server_helper_elevated(instance_dir: str | Path, *, platform_name: str | None = None) -> list[str]:
    system = _platform_name(platform_name)
    command = helper_launch_command(instance_dir)
    shell_command = f"nohup {shlex.join(command)} >/dev/null 2>&1 &"
    if system == "linux":
        if shutil.which("pkexec") is None:
            raise ValueError("pkexec is required to start a privileged web server on Linux.")
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
            raise ValueError("osascript is required to start a privileged web server on macOS.")
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
    raise ValueError("GUI elevation for privileged web-server ports is not supported on this platform.")


def write_web_server_config(
    instance_dir: str | Path,
    config: WebServerConfig,
    *,
    log_path: str | Path = "",
    log_label: str = "",
) -> None:
    validated = validate_web_server_config(config)
    paths = web_server_instance_paths(instance_dir)
    paths.root.mkdir(parents=True, exist_ok=True)
    payload = validated.to_dict()
    if log_path:
        payload["log_path"] = str(Path(log_path).expanduser())
    if log_label.strip():
        payload["log_label"] = log_label.strip()
    _write_json(paths.config_path, payload)


def read_web_server_config(instance_dir: str | Path) -> WebServerConfig:
    payload = _read_web_server_config_payload(instance_dir)
    return WebServerConfig.from_dict(payload)


def write_web_server_status(instance_dir: str | Path, status: WebServerStatus) -> None:
    paths = web_server_instance_paths(instance_dir)
    paths.root.mkdir(parents=True, exist_ok=True)
    _write_json(paths.status_path, status.to_dict(), file_mode=0o644)


def read_web_server_status(instance_dir: str | Path) -> WebServerStatus:
    paths = web_server_instance_paths(instance_dir)
    if not paths.status_path.exists():
        return WebServerStatus()
    try:
        return WebServerStatus.from_dict(json.loads(paths.status_path.read_text(encoding="utf-8")))
    except Exception:
        return WebServerStatus()


def append_web_server_log(instance_dir: str | Path, message: str, *, log_path: str | Path | None = None) -> None:
    paths = web_server_instance_paths(instance_dir)
    target = Path(log_path).expanduser() if log_path else _configured_log_path(paths.root)
    if target is None:
        target = paths.log_path
    target.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    with target.open("a", encoding="utf-8") as handle:
        handle.write(f"[{timestamp}] {message.rstrip()}\n")


def is_web_server_running(instance_dir: str | Path) -> bool:
    status = read_web_server_status(instance_dir)
    if status.pid is None or status.pid <= 0:
        return False
    return _pid_exists(status.pid)


def request_web_server_stop(instance_dir: str | Path) -> None:
    paths = web_server_instance_paths(instance_dir)
    paths.root.mkdir(parents=True, exist_ok=True)
    paths.stop_path.write_text("stop\n", encoding="utf-8")


def resolve_index_page(config: WebServerConfig) -> Path | None:
    value = config.index_page.strip()
    if not value:
        return None
    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        return candidate
    return Path(config.document_root).expanduser() / value


def build_server_url(bind_host: str, port: int, protocol: str) -> str:
    host = bind_host.strip() or "127.0.0.1"
    if host == "0.0.0.0":
        host = "127.0.0.1"
    elif host == "::":
        host = "::1"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"{protocol}://{host}:{port}/"


def run_web_server_helper(instance_dir: str | Path) -> int:
    paths = web_server_instance_paths(instance_dir)
    log_path = ""
    try:
        raw_payload = _read_web_server_config_payload(paths.root)
        config = validate_web_server_config(WebServerConfig.from_dict(raw_payload))
        log_path = _ensure_run_log_path(paths, raw_payload, config)
        if paths.stop_path.exists():
            try:
                paths.stop_path.unlink()
            except OSError:
                pass

        append_web_server_log(paths.root, f"Starting {config.mode} listener on {config.bind_host}:{config.port}", log_path=log_path)
        write_web_server_status(
            paths.root,
            WebServerStatus(
                state="starting",
                pid=None,
                message="Starting web server...",
                bind_host=config.bind_host,
                port=config.port,
                protocol=config.protocol,
                document_root=config.document_root,
                log_path=log_path,
            ),
        )
        if config.mode == "reverse_proxy":
            return asyncio.run(_run_reverse_proxy_helper(paths, config, log_path))
        return _run_static_web_server(paths, config, log_path)
    except Exception as exc:  # noqa: BLE001
        append_web_server_log(paths.root, f"ERROR: {exc}", log_path=log_path or None)
        append_web_server_log(paths.root, traceback.format_exc(), log_path=log_path or None)
        write_web_server_status(
            paths.root,
            WebServerStatus(
                state="error",
                pid=os.getpid(),
                message=str(exc),
                log_path=log_path,
            ),
        )
        return 1
    finally:
        if paths.stop_path.exists():
            try:
                paths.stop_path.unlink()
            except OSError:
                pass


def ensure_self_signed_certificate(paths: WebServerInstancePaths, *, bind_host: str) -> tuple[str, str]:
    if paths.generated_cert_path.exists() and paths.generated_key_path.exists():
        return str(paths.generated_cert_path), str(paths.generated_key_path)

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    common_name = "localhost"
    san_entries: list[x509.GeneralName] = [x509.DNSName("localhost")]
    try:
        san_entries.append(x509.IPAddress(ipaddress.ip_address(bind_host)))
        common_name = bind_host
    except ValueError:
        if bind_host and bind_host not in {"0.0.0.0", "::"}:
            san_entries.append(x509.DNSName(bind_host))
            common_name = bind_host
    san_entries.append(x509.IPAddress(ipaddress.ip_address("127.0.0.1")))
    san_entries.append(x509.IPAddress(ipaddress.ip_address("::1")))

    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(minutes=5))
        .not_valid_after(datetime.now(UTC) + timedelta(days=30))
        .add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
        .sign(private_key, hashes.SHA256())
    )

    paths.generated_cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    paths.generated_key_path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return str(paths.generated_cert_path), str(paths.generated_key_path)


class _ManagedStaticFileHandler(SimpleHTTPRequestHandler):
    def __init__(
        self,
        *args,
        config: WebServerConfig,
        instance_dir: str | Path,
        log_path: str,
        **kwargs,
    ) -> None:
        self._config = config
        self._instance_dir = Path(instance_dir)
        self._log_path = log_path
        super().__init__(*args, directory=str(Path(config.document_root).expanduser()), **kwargs)

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        append_web_server_log(self._instance_dir, format % args, log_path=self._log_path)

    def list_directory(self, path: str):  # type: ignore[override]
        if not self._config.allow_directory_listing:
            self.send_error(403, "Directory listing is disabled.")
            return None
        return super().list_directory(path)

    def do_GET(self) -> None:  # noqa: N802
        self._rewrite_root_request()
        super().do_GET()

    def do_HEAD(self) -> None:  # noqa: N802
        self._rewrite_root_request()
        super().do_HEAD()

    def _rewrite_root_request(self) -> None:
        if self.path not in {"", "/"}:
            return
        index_page = self._config.index_page.strip()
        if not index_page:
            return
        resolved = resolve_index_page(self._config)
        if resolved is None or not resolved.exists():
            return
        document_root = Path(self._config.document_root).expanduser().resolve()
        target = resolved.resolve()
        try:
            relative = target.relative_to(document_root)
        except ValueError:
            return
        relative_path = "/".join(relative.parts)
        self.path = "/" + relative_path

    def translate_path(self, path: str) -> str:
        translated = super().translate_path(path)
        parsed_path = unquote(urlsplit(path).path)
        if self._config.index_page.strip() and parsed_path in {"", "/"}:
            resolved = resolve_index_page(self._config)
            if resolved is not None:
                return str(resolved)
        return translated


class _ReverseProxyHandler:
    def __init__(
        self,
        *,
        config: WebServerConfig,
        instance_dir: str | Path,
        log_path: str,
        client: ClientSession,
    ) -> None:
        self._config = config
        self._instance_dir = Path(instance_dir)
        self._log_path = log_path
        self._client = client
        self._upstream = urlsplit(config.upstream_url)
        self._extra_headers = _parse_proxy_extra_headers(config.proxy_extra_headers)

    async def handle(self, request: web.Request) -> web.StreamResponse:
        started = time.monotonic()
        try:
            if not self._path_matches(request.path):
                append_web_server_log(
                    self._instance_dir,
                    f'HTTP {request.method} {request.rel_url.path_qs} -> 404 (path prefix "{self._config.proxy_path_prefix}" not matched)',
                    log_path=self._log_path,
                )
                raise web.HTTPNotFound(text="Path is not handled by this reverse proxy.")

            if self._is_websocket_request(request):
                response = await self._handle_websocket(request)
                elapsed_ms = int((time.monotonic() - started) * 1000)
                append_web_server_log(
                    self._instance_dir,
                    f"WS {request.rel_url.path_qs} -> 101 proxied in {elapsed_ms}ms",
                    log_path=self._log_path,
                )
                return response

            response = await self._handle_http(request)
            elapsed_ms = int((time.monotonic() - started) * 1000)
            append_web_server_log(
                self._instance_dir,
                f"HTTP {request.method} {request.rel_url.path_qs} -> {response.status} in {elapsed_ms}ms",
                log_path=self._log_path,
            )
            return response
        except web.HTTPException:
            raise
        except asyncio.TimeoutError as exc:
            append_web_server_log(
                self._instance_dir,
                f"ERROR: proxy timeout for {request.method} {request.rel_url.path_qs}",
                log_path=self._log_path,
            )
            raise web.HTTPGatewayTimeout(text="Upstream timed out.") from exc
        except Exception as exc:  # noqa: BLE001
            append_web_server_log(
                self._instance_dir,
                f"ERROR: proxy failure for {request.method} {request.rel_url.path_qs}: {exc}",
                log_path=self._log_path,
            )
            raise web.HTTPBadGateway(text="Proxy request failed.") from exc

    def _path_matches(self, request_path: str) -> bool:
        prefix = self._config.proxy_path_prefix
        if prefix == "/":
            return True
        return request_path == prefix or request_path.startswith(prefix + "/")

    def _is_websocket_request(self, request: web.Request) -> bool:
        if not self._config.proxy_enable_websocket:
            return False
        return request.headers.get("Upgrade", "").strip().lower() == "websocket"

    async def _handle_http(self, request: web.Request) -> web.StreamResponse:
        upstream_url = self._build_upstream_url(request, websocket=False)
        headers = self._build_request_headers(request)
        request_body = await request.read()
        ssl_option: bool | ssl.SSLContext | None = None
        if self._upstream.scheme == "https" and not self._config.proxy_verify_upstream_tls:
            ssl_option = False

        request_kwargs = {
            "headers": headers,
            "data": request_body if request_body else None,
            "allow_redirects": False,
            "ssl": ssl_option,
        }
        try:
            async with self._client.request(
                request.method,
                upstream_url,
                **request_kwargs,
            ) as upstream_response:
                return await self._proxy_http_response(request, upstream_response)
        except Exception as exc:
            if not self._should_retry_after_malformed_close_response(request, exc):
                raise

            retry_headers = self._build_retry_request_headers(headers)
            append_web_server_log(
                self._instance_dir,
                (
                    f"Retrying {request.method} {request.rel_url.path_qs} without cache validators "
                    "after malformed upstream close response."
                ),
                log_path=self._log_path,
            )
            async with self._client.request(
                request.method,
                upstream_url,
                headers=retry_headers,
                data=request_body if request_body else None,
                allow_redirects=False,
                ssl=ssl_option,
            ) as upstream_response:
                return await self._proxy_http_response(request, upstream_response)

    async def _proxy_http_response(
        self,
        request: web.Request,
        upstream_response,
    ) -> web.StreamResponse:
        downstream_headers = {
            key: value
            for key, value in upstream_response.headers.items()
            if key.lower() not in _HOP_BY_HOP_HEADERS
        }
        downstream = web.StreamResponse(status=upstream_response.status, reason=upstream_response.reason)
        for key, value in downstream_headers.items():
            downstream.headers[key] = value
        await downstream.prepare(request)
        async for chunk in upstream_response.content.iter_chunked(65536):
            await downstream.write(chunk)
        await downstream.write_eof()
        return downstream

    async def _handle_websocket(self, request: web.Request) -> web.WebSocketResponse:
        protocols = [
            token.strip()
            for token in request.headers.get("Sec-WebSocket-Protocol", "").split(",")
            if token.strip()
        ]
        upstream_url = self._build_upstream_url(request, websocket=True)
        headers = self._build_request_headers(request, websocket=True)
        ssl_option: bool | ssl.SSLContext = True
        if self._upstream.scheme == "https" and not self._config.proxy_verify_upstream_tls:
            ssl_option = False

        async with self._client.ws_connect(
            upstream_url,
            headers=headers,
            protocols=protocols,
            ssl=ssl_option,
            timeout=ClientWSTimeout(ws_close=self._config.proxy_read_timeout),
            autoclose=False,
            autoping=True,
        ) as upstream_ws:
            downstream_ws = web.WebSocketResponse(protocols=protocols, autoclose=False, autoping=True)
            await downstream_ws.prepare(request)

            async def _client_to_upstream() -> None:
                async for message in downstream_ws:
                    if message.type == WSMsgType.TEXT:
                        await upstream_ws.send_str(message.data)
                    elif message.type == WSMsgType.BINARY:
                        await upstream_ws.send_bytes(message.data)
                    elif message.type == WSMsgType.PING:
                        await upstream_ws.ping()
                    elif message.type == WSMsgType.PONG:
                        await upstream_ws.pong()
                    elif message.type in {WSMsgType.CLOSE, WSMsgType.CLOSING, WSMsgType.CLOSED}:
                        break
                    elif message.type == WSMsgType.ERROR:
                        raise RuntimeError("Downstream websocket error.")

            async def _upstream_to_client() -> None:
                async for message in upstream_ws:
                    if message.type == WSMsgType.TEXT:
                        await downstream_ws.send_str(message.data)
                    elif message.type == WSMsgType.BINARY:
                        await downstream_ws.send_bytes(message.data)
                    elif message.type == WSMsgType.PING:
                        await downstream_ws.ping()
                    elif message.type == WSMsgType.PONG:
                        await downstream_ws.pong()
                    elif message.type in {WSMsgType.CLOSE, WSMsgType.CLOSING, WSMsgType.CLOSED}:
                        break
                    elif message.type == WSMsgType.ERROR:
                        raise RuntimeError("Upstream websocket error.")

            tasks = [
                asyncio.create_task(_client_to_upstream()),
                asyncio.create_task(_upstream_to_client()),
            ]
            try:
                done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
            finally:
                if not downstream_ws.closed:
                    await downstream_ws.close()
                if not upstream_ws.closed:
                    await upstream_ws.close()

            return downstream_ws

    def _build_request_headers(self, request: web.Request, *, websocket: bool = False) -> dict[str, str]:
        headers = {
            key: value
            for key, value in request.headers.items()
            if key.lower() not in _HOP_BY_HOP_HEADERS
        }
        if not self._config.proxy_preserve_host:
            headers.pop("Host", None)
        if self._config.proxy_send_x_forwarded:
            host = request.headers.get("Host", "")
            forwarded_proto = "https" if request.secure else "http"
            if request.remote:
                existing = headers.get("X-Forwarded-For", "")
                headers["X-Forwarded-For"] = f"{existing}, {request.remote}".strip(", ")
            if host:
                headers["X-Forwarded-Host"] = host
            headers["X-Forwarded-Proto"] = forwarded_proto
        for key, value in self._extra_headers.items():
            headers[key] = value
        if websocket:
            headers.pop("Content-Length", None)
        return headers

    def _should_retry_after_malformed_close_response(self, request: web.Request, exc: Exception) -> bool:
        if request.method.upper() not in {"GET", "HEAD"}:
            return False
        return _MALFORMED_CLOSE_RESPONSE_MARKER in str(exc)

    def _build_retry_request_headers(self, headers: dict[str, str]) -> dict[str, str]:
        retry_headers = {
            key: value
            for key, value in headers.items()
            if key.lower() not in _CONDITIONAL_REQUEST_HEADERS
        }
        retry_headers["Accept-Encoding"] = "identity"
        retry_headers["Connection"] = "close"
        return retry_headers

    def _build_upstream_url(self, request: web.Request, *, websocket: bool) -> str:
        forwarded_path = request.rel_url.path
        prefix = self._config.proxy_path_prefix
        if self._config.proxy_strip_prefix and prefix != "/":
            if forwarded_path == prefix:
                forwarded_path = "/"
            else:
                forwarded_path = forwarded_path[len(prefix):] or "/"
        combined_path = _join_url_paths(self._upstream.path or "/", forwarded_path)
        scheme = self._upstream.scheme
        if websocket:
            scheme = "wss" if scheme == "https" else "ws"
        return urlunsplit(
            (
                scheme,
                self._upstream.netloc,
                combined_path,
                request.rel_url.query_string,
                "",
            )
        )


def _run_static_web_server(paths: WebServerInstancePaths, config: WebServerConfig, log_path: str) -> int:
    server: ThreadingHTTPServer | None = None
    try:
        handler = partial(_ManagedStaticFileHandler, config=config, instance_dir=paths.root, log_path=log_path)
        server = ThreadingHTTPServer((config.bind_host, config.port), handler)
        server.timeout = 0.5

        if config.uses_tls:
            context = _build_server_ssl_context(paths, config, log_path)
            server.socket = context.wrap_socket(server.socket, server_side=True)

        actual_port = int(server.server_address[1])
        url = build_server_url(config.bind_host, actual_port, config.protocol)
        write_web_server_status(
            paths.root,
            WebServerStatus(
                state="running",
                pid=os.getpid(),
                url=url,
                message="Web server is running.",
                bind_host=config.bind_host,
                port=actual_port,
                protocol=config.protocol,
                document_root=config.document_root,
                started_at=datetime.now(UTC).isoformat(),
                log_path=log_path,
            ),
        )
        append_web_server_log(paths.root, f"Serving static files from {config.document_root} at {url}", log_path=log_path)

        while not paths.stop_path.exists():
            server.handle_request()

        append_web_server_log(paths.root, "Stop requested.", log_path=log_path)
        write_web_server_status(
            paths.root,
            WebServerStatus(
                state="stopped",
                pid=os.getpid(),
                url=url,
                message="Web server stopped.",
                bind_host=config.bind_host,
                port=actual_port,
                protocol=config.protocol,
                document_root=config.document_root,
                log_path=log_path,
            ),
        )
        return 0
    finally:
        if server is not None:
            try:
                server.server_close()
            except Exception:
                pass


async def _run_reverse_proxy_helper(paths: WebServerInstancePaths, config: WebServerConfig, log_path: str) -> int:
    timeout = ClientTimeout(sock_connect=config.proxy_connect_timeout, sock_read=config.proxy_read_timeout)
    connector = TCPConnector(ssl=config.proxy_verify_upstream_tls)
    session = ClientSession(timeout=timeout, connector=connector, auto_decompress=False)
    runner: web.AppRunner | None = None
    site: web.BaseSite | None = None
    try:
        handler = _ReverseProxyHandler(config=config, instance_dir=paths.root, log_path=log_path, client=session)
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", handler.handle)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        ssl_context = _build_server_ssl_context(paths, config, log_path) if config.uses_tls else None
        site = web.TCPSite(runner, host=config.bind_host, port=config.port, ssl_context=ssl_context)
        await site.start()

        actual_port = config.port
        if getattr(site, "_server", None) is not None:
            sockets = getattr(site._server, "sockets", None)
            if sockets:
                actual_port = int(sockets[0].getsockname()[1])

        url = build_server_url(config.bind_host, actual_port, config.protocol)
        write_web_server_status(
            paths.root,
            WebServerStatus(
                state="running",
                pid=os.getpid(),
                url=url,
                message="Reverse proxy is running.",
                bind_host=config.bind_host,
                port=actual_port,
                protocol=config.protocol,
                document_root=config.upstream_url,
                started_at=datetime.now(UTC).isoformat(),
                log_path=log_path,
            ),
        )
        append_web_server_log(paths.root, f"Proxying {config.upstream_url} at {url}", log_path=log_path)

        while not paths.stop_path.exists():
            await asyncio.sleep(0.25)

        append_web_server_log(paths.root, "Stop requested.", log_path=log_path)
        write_web_server_status(
            paths.root,
            WebServerStatus(
                state="stopped",
                pid=os.getpid(),
                url=url,
                message="Reverse proxy stopped.",
                bind_host=config.bind_host,
                port=actual_port,
                protocol=config.protocol,
                document_root=config.upstream_url,
                log_path=log_path,
            ),
        )
        return 0
    finally:
        if site is not None:
            try:
                await site.stop()
            except Exception:
                pass
        if runner is not None:
            try:
                await runner.cleanup()
            except Exception:
                pass
        await session.close()


def _build_server_ssl_context(paths: WebServerInstancePaths, config: WebServerConfig, log_path: str) -> ssl.SSLContext:
    cert_path, key_path = _resolve_tls_material(paths, config, log_path)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=cert_path, keyfile=key_path)
    return context


def _resolve_tls_material(paths: WebServerInstancePaths, config: WebServerConfig, log_path: str) -> tuple[str, str]:
    if config.tls_mode == "self_signed":
        append_web_server_log(paths.root, "Preparing self-signed certificate.", log_path=log_path)
        return ensure_self_signed_certificate(paths, bind_host=config.bind_host)
    if config.tls_mode == "manual":
        cert_path = str(Path(config.cert_file).expanduser())
        key_path = str(Path(config.key_file).expanduser())
        if config.chain_file.strip():
            bundle_path = _prepare_manual_certificate_chain(paths, cert_path, config.chain_file)
            append_web_server_log(paths.root, "Prepared manual certificate with intermediate chain.", log_path=log_path)
            return bundle_path, key_path
        return cert_path, key_path
    if config.tls_mode == "certbot":
        return _ensure_certbot_certificate(paths, config, log_path)
    raise ValueError("TLS material requested for an HTTP-only profile.")


def _prepare_manual_certificate_chain(paths: WebServerInstancePaths, cert_path: str, chain_path: str) -> str:
    certificate = Path(cert_path).expanduser().read_bytes()
    chain = Path(chain_path).expanduser().read_bytes()
    payload = certificate.rstrip() + b"\n" + chain.lstrip()
    paths.prepared_chain_cert_path.write_bytes(payload)
    return str(paths.prepared_chain_cert_path)


def _ensure_certbot_certificate(paths: WebServerInstancePaths, config: WebServerConfig, log_path: str) -> tuple[str, str]:
    cert_path = _certbot_live_dir(config) / "fullchain.pem"
    key_path = _certbot_live_dir(config) / "privkey.pem"
    if _certificate_is_usable(cert_path, renewal_window_days=_CERTBOT_RENEWAL_WINDOW_DAYS) and key_path.exists():
        append_web_server_log(paths.root, f"Using existing certbot certificate for {_certbot_domains(config)[0]}.", log_path=log_path)
        return str(cert_path), str(key_path)

    append_web_server_log(paths.root, "Checking certificate with certbot.", log_path=log_path)
    command = _build_certbot_command(config)
    append_web_server_log(paths.root, f"Running certbot: {shlex.join(command)}", log_path=log_path)
    result = subprocess.run(  # noqa: S603
        command,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.stdout.strip():
        append_web_server_log(paths.root, result.stdout.strip(), log_path=log_path)
    if result.returncode != 0:
        if result.stderr.strip():
            append_web_server_log(paths.root, result.stderr.strip(), log_path=log_path)
        raise ValueError((result.stderr or result.stdout or "Certbot failed.").strip())
    if not cert_path.exists() or not key_path.exists():
        raise ValueError("Certbot did not produce certificate files.")
    append_web_server_log(paths.root, f"Certbot certificate ready for {_certbot_domains(config)[0]}.", log_path=log_path)
    return str(cert_path), str(key_path)


def _build_certbot_command(config: WebServerConfig) -> list[str]:
    certbot_root = web_server_certbot_root()
    config_dir = certbot_root / "config"
    work_dir = certbot_root / "work"
    logs_dir = certbot_root / "logs"
    config_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    command = [
        config.certbot_executable,
        "certonly",
        "--standalone",
        "--non-interactive",
        "--agree-tos",
        "--keep-until-expiring",
        "--preferred-challenges",
        "http",
        "--config-dir",
        str(config_dir),
        "--work-dir",
        str(work_dir),
        "--logs-dir",
        str(logs_dir),
        "--email",
        config.certbot_email,
        "--http-01-port",
        str(config.certbot_challenge_port),
    ]
    if config.certbot_staging:
        command.append("--staging")
    for domain in _certbot_domains(config):
        command.extend(["-d", domain])
    return command


def _certbot_live_dir(config: WebServerConfig) -> Path:
    primary_domain = _certbot_domains(config)[0]
    return web_server_certbot_root() / "config" / "live" / primary_domain


def _certbot_domains(config: WebServerConfig) -> list[str]:
    tokens = [config.certbot_primary_domain, *_split_delimited_tokens(config.certbot_additional_domains)]
    normalized: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        value = token.strip()
        if not value:
            continue
        lowered = value.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        normalized.append(value)
    return normalized


def _certificate_is_usable(cert_path: Path, *, renewal_window_days: int) -> bool:
    if not cert_path.exists() or not cert_path.is_file():
        return False
    try:
        certificate = x509.load_pem_x509_certificate(cert_path.read_bytes())
    except Exception:
        return False
    not_valid_after = getattr(certificate, "not_valid_after_utc", None)
    if not_valid_after is None:
        not_valid_after = certificate.not_valid_after.replace(tzinfo=UTC)
    return not_valid_after > (datetime.now(UTC) + timedelta(days=max(1, renewal_window_days)))


def _configured_log_path(instance_dir: str | Path) -> Path | None:
    paths = web_server_instance_paths(instance_dir)
    try:
        payload = _read_web_server_config_payload(paths.root)
    except ValueError:
        payload = {}
    raw = str(payload.get("log_path", "")).strip()
    if raw:
        return Path(raw).expanduser()
    status = read_web_server_status(paths.root)
    if status.log_path:
        return Path(status.log_path).expanduser()
    return None


def _ensure_run_log_path(
    paths: WebServerInstancePaths,
    raw_payload: dict[str, object],
    config: WebServerConfig,
) -> str:
    raw_log_path = str(raw_payload.get("log_path", "")).strip()
    if raw_log_path:
        return str(Path(raw_log_path).expanduser())
    label = str(raw_payload.get("log_label", "")).strip() or _default_log_label(config)
    log_path = str(create_web_server_archived_log_path(label))
    updated_payload = dict(raw_payload)
    updated_payload["log_path"] = log_path
    if label:
        updated_payload["log_label"] = label
    _write_json(paths.config_path, updated_payload)
    return log_path


def _default_log_label(config: WebServerConfig) -> str:
    if config.mode == "reverse_proxy":
        return "reverse-proxy"
    return "static-server"


def _parse_proxy_extra_headers(raw: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    for line in raw.splitlines():
        entry = line.strip()
        if not entry:
            continue
        if ":" not in entry:
            raise ValueError("Extra proxy headers must use the format Header: Value.")
        key, value = entry.split(":", 1)
        header_name = key.strip()
        header_value = value.strip()
        if not header_name:
            raise ValueError("Extra proxy headers must include a header name.")
        headers[header_name] = header_value
    return headers


def _join_url_paths(base_path: str, forwarded_path: str) -> str:
    base = base_path or "/"
    forward = forwarded_path or "/"
    if forward == "/":
        combined = base
    else:
        combined = posixpath.join(base.rstrip("/"), forward.lstrip("/"))
    if not combined.startswith("/"):
        combined = f"/{combined}"
    return combined


def _safe_log_label(label: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", label.strip()).strip("._")
    return cleaned or "web-server"


def _split_delimited_tokens(raw: str) -> list[str]:
    return [token for token in re.split(r"[\s,]+", raw.strip()) if token]


def _resolve_executable_path(executable: str) -> str | None:
    candidate = executable.strip()
    if not candidate:
        return None
    path_candidate = Path(candidate).expanduser()
    if path_candidate.is_absolute() or any(sep in candidate for sep in ("/", "\\")):
        return str(path_candidate) if path_candidate.exists() else None
    return shutil.which(candidate)


def _read_web_server_config_payload(instance_dir: str | Path) -> dict[str, object]:
    paths = web_server_instance_paths(instance_dir)
    if not paths.config_path.exists():
        raise ValueError(f"Web server config not found: {paths.config_path}")
    raw = json.loads(paths.config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Web server config is invalid.")
    return dict(raw)


def _write_json(path: Path, payload: dict[str, object], *, file_mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=path.parent) as handle:
        json.dump(payload, handle, ensure_ascii=True, indent=2)
        temp_path = Path(handle.name)
    if file_mode is not None:
        try:
            temp_path.chmod(file_mode)
        except OSError:
            pass
    temp_path.replace(path)
    if file_mode is not None:
        try:
            path.chmod(file_mode)
        except OSError:
            pass


def _escape_applescript(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _platform_name(platform_name: str | None = None) -> str:
    token = (platform_name or sys.platform).strip().lower()
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
    except PermissionError:
        return True
    except OSError:
        return False
    return True
