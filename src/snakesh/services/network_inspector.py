from __future__ import annotations

import json
from dataclasses import dataclass, field
import ipaddress
import os
from pathlib import Path
import platform
import shlex
import shutil
import socket
import subprocess
import tempfile
import time
from typing import Any
from uuid import uuid4

from snakesh import runtime
from snakesh.services.oui_service import OUILookupService, bundled_oui_lookup_service


NETWORK_INSPECTOR_PORTS_HELPER_FLAG = "--network-inspector-ports-helper"
_HELPER_START_TIMEOUT_SECONDS = 8.0
_HELPER_REQUEST_TIMEOUT_SECONDS = 8.0
_HELPER_POLL_INTERVAL_SECONDS = 0.1


@dataclass(frozen=True, slots=True)
class InterfaceAddress:
    family: str
    address: str
    netmask: str = ""
    broadcast: str = ""
    peer: str = ""


@dataclass(frozen=True, slots=True)
class InterfaceInfo:
    name: str
    is_up: bool
    mtu: int
    speed_mbps: int
    duplex: str
    mac_address: str
    addresses: list[InterfaceAddress]


@dataclass(frozen=True, slots=True)
class RouteEntry:
    family: str
    destination: str
    gateway: str
    interface: str
    metric: str = ""
    flags: str = ""
    source: str = ""


@dataclass(frozen=True, slots=True)
class ArpEntry:
    ip_address: str
    mac_address: str
    interface: str
    state: str
    vendor: str = ""


@dataclass(frozen=True, slots=True)
class ListeningPortEntry:
    family: str
    protocol: str
    local_address: str
    pid: int | None
    process_name: str


@dataclass(frozen=True, slots=True)
class DNSConfig:
    host_name: str
    fqdn: str
    nameservers: list[str]
    search_domains: list[str]
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class NetworkInspectorSnapshot:
    interfaces: list[InterfaceInfo] = field(default_factory=list)
    routes: list[RouteEntry] = field(default_factory=list)
    arp_entries: list[ArpEntry] = field(default_factory=list)
    listening_ports: list[ListeningPortEntry] = field(default_factory=list)
    dns_config: DNSConfig | None = None
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ListeningPortsResult:
    entries: list[ListeningPortEntry] = field(default_factory=list)
    warning: str = ""


@dataclass(frozen=True, slots=True)
class PrivilegedPortsSessionPaths:
    root: Path
    ready_path: Path
    request_path: Path
    response_path: Path
    stop_path: Path
    metadata_path: Path


class PrivilegedPortsHelperSession:
    def __init__(self, *, session_dir: str | Path | None = None, parent_pid: int | None = None) -> None:
        root = Path(session_dir) if session_dir is not None else Path(tempfile.mkdtemp(prefix="snakesh-netinspector-"))
        self._paths = PrivilegedPortsSessionPaths(
            root=root,
            ready_path=root / "ready",
            request_path=root / "request",
            response_path=root / "response",
            stop_path=root / "stop",
            metadata_path=root / "session.json",
        )
        self._parent_pid = int(parent_pid or os.getpid())
        self._last_start_failed = False
        self._last_warning = ""
        self._closed = False
        self._initialize_session_dir()

    @property
    def session_dir(self) -> Path:
        return self._paths.root

    @property
    def is_ready(self) -> bool:
        return self._paths.ready_path.exists()

    @property
    def last_start_failed(self) -> bool:
        return self._last_start_failed

    @property
    def last_warning(self) -> str:
        return self._last_warning

    def wait_until_ready(self, timeout: float = _HELPER_START_TIMEOUT_SECONDS) -> bool:
        deadline = time.monotonic() + max(0.1, timeout)
        while time.monotonic() < deadline:
            if self.is_ready:
                return True
            time.sleep(_HELPER_POLL_INTERVAL_SECONDS)
        return self.is_ready

    def collect_ports(self, *, allow_start: bool) -> ListeningPortsResult:
        if self._closed:
            raise RuntimeError("Privileged ports helper session is closed.")

        if not self.is_ready:
            if not allow_start:
                return self._fallback_result(
                    "Privileged ports/processes are unavailable for automatic refresh. "
                    "Click Refresh to retry elevation."
                )
            launch_error = self._ensure_helper_started()
            if launch_error:
                return self._fallback_result(launch_error)

        try:
            request_id = uuid4().hex
            self._remove_file(self._paths.response_path)
            _write_json_atomic(
                self._paths.request_path,
                {
                    "id": request_id,
                    "requested_at": time.time(),
                },
            )
            deadline = time.monotonic() + _HELPER_REQUEST_TIMEOUT_SECONDS
            while time.monotonic() < deadline:
                payload = self._read_json_file(self._paths.response_path)
                if payload.get("id") == request_id:
                    entries = [
                        ListeningPortEntry(
                            family=str(item.get("family", "")).strip(),
                            protocol=str(item.get("protocol", "")).strip(),
                            local_address=str(item.get("local_address", "")).strip(),
                            pid=_normalize_pid(item.get("pid")),
                            process_name=str(item.get("process_name", "")).strip(),
                        )
                        for item in payload.get("entries", [])
                        if isinstance(item, dict)
                    ]
                    self._last_start_failed = False
                    self._last_warning = ""
                    return ListeningPortsResult(entries=entries)
                if not self.is_ready:
                    break
                time.sleep(_HELPER_POLL_INTERVAL_SECONDS)
            self._remove_file(self._paths.ready_path)
            return self._fallback_result("Privileged ports helper timed out while collecting data.")
        except Exception as exc:  # noqa: BLE001
            self._remove_file(self._paths.ready_path)
            return self._fallback_result(str(exc))

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._paths.root.mkdir(parents=True, exist_ok=True)
            self._paths.stop_path.write_text("stop\n", encoding="utf-8")
        except Exception:
            pass
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if not self.is_ready:
                break
            time.sleep(_HELPER_POLL_INTERVAL_SECONDS)
        try:
            for path in (
                self._paths.ready_path,
                self._paths.request_path,
                self._paths.response_path,
                self._paths.stop_path,
                self._paths.metadata_path,
            ):
                self._remove_file(path)
            self._paths.root.rmdir()
        except Exception:
            pass

    def _initialize_session_dir(self) -> None:
        self._paths.root.mkdir(parents=True, exist_ok=True)
        for path in (
            self._paths.ready_path,
            self._paths.request_path,
            self._paths.response_path,
            self._paths.stop_path,
        ):
            self._remove_file(path)
        _write_json_atomic(
            self._paths.metadata_path,
            {
                "parent_pid": self._parent_pid,
            },
        )

    def _ensure_helper_started(self) -> str:
        if self.is_ready:
            self._last_start_failed = False
            self._last_warning = ""
            return ""
        try:
            launch_privileged_ports_helper(self._paths.root)
        except Exception as exc:  # noqa: BLE001
            self._last_start_failed = True
            self._last_warning = str(exc)
            return self._last_warning
        if self.wait_until_ready():
            self._last_start_failed = False
            self._last_warning = ""
            return ""
        self._last_start_failed = True
        self._last_warning = "Privileged ports helper did not become ready."
        return self._last_warning

    def _fallback_result(self, reason: str) -> ListeningPortsResult:
        self._last_start_failed = True
        cleaned_reason = reason.strip() or "Privileged ports/processes are unavailable."
        self._last_warning = cleaned_reason
        warning = f"{cleaned_reason} Showing standard visibility instead."
        return ListeningPortsResult(entries=collect_listening_ports(), warning=warning)

    @staticmethod
    def _read_json_file(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        if isinstance(payload, dict):
            return payload
        return {}

    @staticmethod
    def _remove_file(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass


def collect_network_snapshot(
    oui_lookup: OUILookupService | None = None,
    *,
    use_privileged_ports: bool = False,
    privileged_ports_session: PrivilegedPortsHelperSession | None = None,
    allow_privileged_ports_launch: bool = True,
) -> NetworkInspectorSnapshot:
    lookup = oui_lookup or bundled_oui_lookup_service()
    errors: list[str] = []

    try:
        interfaces = collect_interface_info()
    except Exception as exc:  # noqa: BLE001
        interfaces = []
        errors.append(f"IP/Interfaces: {exc}")

    try:
        routes = collect_routes()
    except Exception as exc:  # noqa: BLE001
        routes = []
        errors.append(f"Routing: {exc}")

    try:
        arp_entries = collect_arp_entries(lookup)
    except Exception as exc:  # noqa: BLE001
        arp_entries = []
        errors.append(f"ARP: {exc}")

    try:
        ports_result = collect_listening_ports_result(
            use_privileged_ports=use_privileged_ports,
            privileged_ports_session=privileged_ports_session,
            allow_privileged_ports_launch=allow_privileged_ports_launch,
        )
        listening_ports = ports_result.entries
        if ports_result.warning:
            errors.append(f"Ports: {ports_result.warning}")
    except Exception as exc:  # noqa: BLE001
        listening_ports = []
        errors.append(f"Ports: {exc}")

    try:
        dns_config = collect_dns_config()
    except Exception as exc:  # noqa: BLE001
        dns_config = None
        errors.append(f"DNS: {exc}")

    return NetworkInspectorSnapshot(
        interfaces=interfaces,
        routes=routes,
        arp_entries=arp_entries,
        listening_ports=listening_ports,
        dns_config=dns_config,
        errors=errors,
    )


def collect_interface_info() -> list[InterfaceInfo]:
    psutil = _psutil()
    interface_stats = psutil.net_if_stats()
    interface_addrs = psutil.net_if_addrs()
    duplex_labels = {
        getattr(psutil, "NIC_DUPLEX_FULL", object()): "Full",
        getattr(psutil, "NIC_DUPLEX_HALF", object()): "Half",
        getattr(psutil, "NIC_DUPLEX_UNKNOWN", object()): "Unknown",
    }
    mac_families = {getattr(psutil, "AF_LINK", object()), getattr(socket, "AF_PACKET", object())}

    results: list[InterfaceInfo] = []
    for name in sorted(interface_addrs):
        stats = interface_stats.get(name)
        addresses: list[InterfaceAddress] = []
        mac_address = ""
        for address in interface_addrs.get(name, []):
            family = getattr(address, "family", None)
            if family in mac_families:
                mac_address = str(getattr(address, "address", "") or "").strip()
                continue
            addresses.append(
                InterfaceAddress(
                    family=_address_family_label(family),
                    address=str(getattr(address, "address", "") or "").strip(),
                    netmask=str(getattr(address, "netmask", "") or "").strip(),
                    broadcast=str(getattr(address, "broadcast", "") or "").strip(),
                    peer=str(getattr(address, "ptp", "") or "").strip(),
                )
            )
        results.append(
            InterfaceInfo(
                name=name,
                is_up=bool(getattr(stats, "isup", False)),
                mtu=int(getattr(stats, "mtu", 0) or 0),
                speed_mbps=int(getattr(stats, "speed", 0) or 0),
                duplex=duplex_labels.get(getattr(stats, "duplex", None), "Unknown"),
                mac_address=mac_address,
                addresses=addresses,
            )
        )
    return results


def collect_listening_ports() -> list[ListeningPortEntry]:
    psutil = _psutil()
    process_name_cache: dict[int, str] = {}
    entries: list[ListeningPortEntry] = []
    seen: set[tuple[str, str, str, int | None]] = set()
    listen_token = getattr(psutil, "CONN_LISTEN", "LISTEN")

    for connection in psutil.net_connections(kind="inet"):
        local = getattr(connection, "laddr", None)
        if not local:
            continue

        protocol = "TCP"
        include = getattr(connection, "status", "") == listen_token
        if getattr(connection, "type", None) == socket.SOCK_DGRAM:
            protocol = "UDP"
            include = True
        if not include:
            continue

        ip_address = getattr(local, "ip", "")
        port = getattr(local, "port", 0)
        family = _address_family_label(getattr(connection, "family", None))
        pid = getattr(connection, "pid", None)
        process_name = ""
        if isinstance(pid, int) and pid > 0:
            if pid not in process_name_cache:
                try:
                    process_name_cache[pid] = psutil.Process(pid).name()
                except Exception:
                    process_name_cache[pid] = ""
            process_name = process_name_cache[pid]

        key = (family, protocol, f"{ip_address}:{port}", pid)
        if key in seen:
            continue
        seen.add(key)
        entries.append(
            ListeningPortEntry(
                family=family,
                protocol=protocol,
                local_address=f"{ip_address}:{port}",
                pid=pid if isinstance(pid, int) else None,
                process_name=process_name,
            )
        )
    entries.sort(key=lambda item: (item.protocol, item.local_address))
    return entries


def collect_listening_ports_result(
    *,
    use_privileged_ports: bool,
    privileged_ports_session: PrivilegedPortsHelperSession | None,
    allow_privileged_ports_launch: bool,
) -> ListeningPortsResult:
    if use_privileged_ports and privileged_ports_session is not None:
        return privileged_ports_session.collect_ports(allow_start=allow_privileged_ports_launch)
    return ListeningPortsResult(entries=collect_listening_ports())


def network_inspector_ports_helper_command(session_dir: str | Path) -> list[str]:
    return runtime.self_launch_command([NETWORK_INSPECTOR_PORTS_HELPER_FLAG, str(Path(session_dir).expanduser())])


def launch_privileged_ports_helper(session_dir: str | Path, *, platform_name: str | None = None) -> list[str]:
    if _is_effectively_elevated(platform_name):
        return _launch_ports_helper_plain(session_dir, platform_name=platform_name)
    return launch_network_inspector_ports_helper_elevated(session_dir, platform_name=platform_name)


def launch_network_inspector_ports_helper_elevated(
    session_dir: str | Path,
    *,
    platform_name: str | None = None,
) -> list[str]:
    system = _platform_name(platform_name)
    command = network_inspector_ports_helper_command(session_dir)
    if system == "linux":
        if shutil.which("pkexec") is None:
            raise ValueError("pkexec is required to view privileged ports/processes on Linux.")
        shell_command = f"nohup {shlex.join(command)} >/dev/null 2>&1 &"
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
            raise ValueError("osascript is required to view privileged ports/processes on macOS.")
        shell_command = f"nohup {shlex.join(command)} >/dev/null 2>&1 &"
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
    if system == "windows":
        payload = json.dumps(command)
        script = (
            "$ErrorActionPreference='Stop'\n"
            "$cmd = ConvertFrom-Json -InputObject @'\n"
            f"{payload}\n"
            "'@\n"
            "if ($cmd -isnot [System.Array]) { $cmd = @($cmd) }\n"
            "if ($cmd.Count -lt 1) { exit 1 }\n"
            "$exe = [string]$cmd[0]\n"
            "$args = @()\n"
            "if ($cmd.Count -gt 1) { $args = @($cmd[1..($cmd.Count - 1)]) }\n"
            "try {\n"
            "  Start-Process -FilePath $exe -ArgumentList $args -Verb RunAs -WindowStyle Hidden | Out-Null\n"
            "  exit 0\n"
            "} catch {\n"
            "  $m = ($_.Exception.Message | Out-String)\n"
            "  if ($m -match 'cancel') { exit 1223 }\n"
            "  Write-Error $_\n"
            "  exit 1\n"
            "}\n"
        )
        result = subprocess.run(  # noqa: S603
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 1223:
            raise ValueError("Elevation was cancelled by the user.")
        if result.returncode != 0:
            raise ValueError((result.stderr or result.stdout or "Privileged launch failed.").strip())
        return command
    raise ValueError("Privileged ports/processes are not supported on this platform.")


def run_network_inspector_ports_helper(session_dir: str | Path) -> int:
    paths = _privileged_ports_session_paths(session_dir)
    metadata = _read_json_dict(paths.metadata_path)
    parent_pid = _normalize_pid(metadata.get("parent_pid")) or 0
    last_request_id = ""
    try:
        paths.root.mkdir(parents=True, exist_ok=True)
        _write_json_atomic(
            paths.ready_path,
            {
                "pid": os.getpid(),
                "parent_pid": parent_pid,
            },
        )
        while True:
            if paths.stop_path.exists():
                break
            if parent_pid > 0 and not _pid_exists(parent_pid):
                break
            request = _read_json_dict(paths.request_path)
            request_id = str(request.get("id", "")).strip()
            if request_id and request_id != last_request_id:
                entries = collect_listening_ports()
                _write_json_atomic(
                    paths.response_path,
                    {
                        "id": request_id,
                        "entries": [
                            {
                                "family": entry.family,
                                "protocol": entry.protocol,
                                "local_address": entry.local_address,
                                "pid": entry.pid,
                                "process_name": entry.process_name,
                            }
                            for entry in entries
                        ],
                    },
                )
                last_request_id = request_id
            time.sleep(_HELPER_POLL_INTERVAL_SECONDS)
        return 0
    finally:
        for path in (paths.ready_path, paths.request_path, paths.response_path):
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass


def collect_routes(platform_name: str | None = None) -> list[RouteEntry]:
    system = _platform_name(platform_name)
    if system == "windows":
        return parse_windows_route_print(_run_capture(["route", "print"]))
    if system == "darwin":
        return parse_macos_netstat_routes(_run_capture(["netstat", "-rn"]))

    routes = parse_linux_ip_route(_run_capture(["ip", "route", "show"]), family="IPv4")
    try:
        routes.extend(parse_linux_ip_route(_run_capture(["ip", "-6", "route", "show"]), family="IPv6"))
    except Exception:
        pass
    return routes


def collect_arp_entries(oui_lookup: OUILookupService | None = None, platform_name: str | None = None) -> list[ArpEntry]:
    lookup = oui_lookup or bundled_oui_lookup_service()
    system = _platform_name(platform_name)
    if system == "windows":
        entries = parse_windows_arp_table(_run_capture(["arp", "-a"]))
    elif system == "darwin":
        entries = parse_macos_arp_table(_run_capture(["arp", "-an"]))
    else:
        entries = parse_linux_ip_neigh(_run_capture(["ip", "neigh", "show"]))

    resolved: list[ArpEntry] = []
    for entry in entries:
        vendor = lookup.lookup_vendor(entry.mac_address) or ""
        resolved.append(
            ArpEntry(
                ip_address=entry.ip_address,
                mac_address=entry.mac_address,
                interface=entry.interface,
                state=entry.state,
                vendor=vendor,
            )
        )
    return resolved


def collect_dns_config(platform_name: str | None = None) -> DNSConfig:
    system = _platform_name(platform_name)
    host_name = socket.gethostname()
    fqdn = socket.getfqdn()
    if system == "windows":
        return parse_windows_ipconfig_dns(_run_capture(["ipconfig", "/all"]), host_name=host_name, fqdn=fqdn)
    if system == "darwin":
        try:
            return parse_macos_scutil_dns(_run_capture(["scutil", "--dns"]), host_name=host_name, fqdn=fqdn)
        except Exception:
            return parse_linux_resolv_conf_dns(
                Path("/etc/resolv.conf").read_text(encoding="utf-8"),
                host_name=host_name,
                fqdn=fqdn,
            )
    return parse_linux_resolv_conf_dns(
        Path("/etc/resolv.conf").read_text(encoding="utf-8"),
        host_name=host_name,
        fqdn=fqdn,
    )


def parse_linux_ip_route(raw_text: str, *, family: str) -> list[RouteEntry]:
    entries: list[RouteEntry] = []
    for line in raw_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        tokens = stripped.split()
        destination = tokens[0]
        gateway = ""
        interface = ""
        metric = ""
        flags: list[str] = []
        source = ""
        index = 1
        while index < len(tokens):
            token = tokens[index]
            value = tokens[index + 1] if index + 1 < len(tokens) else ""
            if token == "via":
                gateway = value
                index += 2
                continue
            if token == "dev":
                interface = value
                index += 2
                continue
            if token == "metric":
                metric = value
                index += 2
                continue
            if token == "src":
                source = value
                index += 2
                continue
            flags.append(token)
            index += 1
        entries.append(
            RouteEntry(
                family=family,
                destination=destination,
                gateway=gateway,
                interface=interface,
                metric=metric,
                flags=" ".join(flags).strip(),
                source=source,
            )
        )
    return entries


def parse_windows_route_print(raw_text: str) -> list[RouteEntry]:
    entries: list[RouteEntry] = []
    section = ""
    active = False
    for line in raw_text.splitlines():
        stripped = line.rstrip()
        token = stripped.strip()
        if token == "IPv4 Route Table":
            section = "IPv4"
            active = False
            continue
        if token == "IPv6 Route Table":
            section = "IPv6"
            active = False
            continue
        if token == "Active Routes:":
            active = True
            continue
        if token == "Persistent Routes:":
            active = False
            continue
        if not active or not token or token.startswith("=") or token.startswith("Network Destination") or token.startswith("If "):
            continue

        parts = token.split()
        if section == "IPv4" and len(parts) >= 5:
            destination = parts[0]
            netmask = parts[1]
            gateway = parts[2]
            interface = parts[3]
            metric = parts[4]
            if destination == "0.0.0.0" and netmask == "0.0.0.0":
                destination = "0.0.0.0/0"
            entries.append(
                RouteEntry(
                    family="IPv4",
                    destination=destination,
                    gateway=gateway,
                    interface=interface,
                    metric=metric,
                )
            )
            continue
        if section == "IPv6" and len(parts) >= 4:
            interface = parts[0]
            metric = parts[1]
            destination = parts[2]
            gateway = parts[3]
            entries.append(
                RouteEntry(
                    family="IPv6",
                    destination=destination,
                    gateway=gateway,
                    interface=interface,
                    metric=metric,
                )
            )
    return entries


def parse_macos_netstat_routes(raw_text: str) -> list[RouteEntry]:
    entries: list[RouteEntry] = []
    family = ""
    for line in raw_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped == "Internet:":
            family = "IPv4"
            continue
        if stripped == "Internet6:":
            family = "IPv6"
            continue
        if stripped.startswith("Destination") or family not in {"IPv4", "IPv6"}:
            continue
        parts = stripped.split()
        if len(parts) < 4:
            continue
        entries.append(
            RouteEntry(
                family=family,
                destination=parts[0],
                gateway=parts[1],
                interface=parts[3],
                flags=parts[2],
            )
        )
    return entries


def parse_linux_ip_neigh(raw_text: str) -> list[ArpEntry]:
    entries: list[ArpEntry] = []
    for line in raw_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        tokens = stripped.split()
        ip_address = tokens[0]
        interface = ""
        mac_address = ""
        state = tokens[-1]
        for index, token in enumerate(tokens):
            if token == "dev" and index + 1 < len(tokens):
                interface = tokens[index + 1]
            if token == "lladdr" and index + 1 < len(tokens):
                mac_address = tokens[index + 1]
        if not mac_address:
            continue
        entries.append(ArpEntry(ip_address=ip_address, mac_address=mac_address, interface=interface, state=state))
    return entries


def parse_windows_arp_table(raw_text: str) -> list[ArpEntry]:
    entries: list[ArpEntry] = []
    current_interface = ""
    for line in raw_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("Interface:"):
            current_interface = stripped.split("Interface:", 1)[1].split("---", 1)[0].strip()
            continue
        if stripped.lower().startswith("internet address"):
            continue
        parts = stripped.split()
        if len(parts) < 3:
            continue
        ip_address, mac_address, state = parts[0], parts[1], parts[2]
        if "." not in ip_address and ":" not in ip_address:
            continue
        entries.append(
            ArpEntry(
                ip_address=ip_address,
                mac_address=mac_address.replace("-", ":").lower(),
                interface=current_interface,
                state=state,
            )
        )
    return entries


def parse_macos_arp_table(raw_text: str) -> list[ArpEntry]:
    entries: list[ArpEntry] = []
    for line in raw_text.splitlines():
        stripped = line.strip()
        if not stripped or " at " not in stripped:
            continue
        try:
            left, right = stripped.split(" at ", 1)
            ip_address = left.rsplit("(", 1)[1].rstrip(")")
            mac_address, remainder = right.split(" on ", 1)
            interface = remainder.split()[0]
        except Exception:
            continue
        if mac_address.lower() == "(incomplete)":
            continue
        entries.append(
            ArpEntry(
                ip_address=ip_address,
                mac_address=mac_address.lower(),
                interface=interface,
                state="resolved",
            )
        )
    return entries


def parse_linux_resolv_conf_dns(raw_text: str, *, host_name: str, fqdn: str) -> DNSConfig:
    nameservers: list[str] = []
    search_domains: list[str] = []
    for line in raw_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("nameserver "):
            nameservers.append(stripped.split(None, 1)[1].strip())
            continue
        if stripped.startswith("search "):
            search_domains.extend(part.strip() for part in stripped.split()[1:] if part.strip())
            continue
        if stripped.startswith("domain "):
            value = stripped.split(None, 1)[1].strip()
            if value:
                search_domains.append(value)
    return DNSConfig(
        host_name=host_name,
        fqdn=fqdn,
        nameservers=_dedupe(nameservers),
        search_domains=_dedupe(search_domains),
    )


def parse_windows_ipconfig_dns(raw_text: str, *, host_name: str, fqdn: str) -> DNSConfig:
    nameservers: list[str] = []
    search_domains: list[str] = []
    pending_multiline: str | None = None
    for line in raw_text.splitlines():
        if not line.strip():
            pending_multiline = None
            continue
        if pending_multiline == "dns_servers" and line.startswith(" "):
            value = line.strip()
            if _looks_like_windows_dns_server(value):
                nameservers.append(value)
                continue
            pending_multiline = None
        if pending_multiline == "search_list" and line.startswith(" "):
            value = line.strip()
            if ":" not in value:
                search_domains.extend(part.strip() for part in value.split(",") if part.strip())
                continue
            pending_multiline = None
        pending_multiline = None
        stripped = line.strip()
        if ":" not in stripped:
            continue
        key, value = [part.strip() for part in stripped.split(":", 1)]
        lowered = " ".join(part for part in key.replace(".", " ").split()).lower()
        if lowered == "host name":
            host_name = value or host_name
            continue
        if lowered == "primary dns suffix" and value:
            fqdn = f"{host_name}.{value}" if host_name and "." not in host_name else fqdn
            continue
        if lowered == "dns servers":
            if value:
                nameservers.append(value)
            pending_multiline = "dns_servers"
            continue
        if lowered == "dns suffix search list":
            if value:
                search_domains.extend(part.strip() for part in value.split(",") if part.strip())
            pending_multiline = "search_list"
    return DNSConfig(
        host_name=host_name,
        fqdn=fqdn,
        nameservers=_dedupe(nameservers),
        search_domains=_dedupe(search_domains),
    )


def parse_macos_scutil_dns(raw_text: str, *, host_name: str, fqdn: str) -> DNSConfig:
    nameservers: list[str] = []
    search_domains: list[str] = []
    for line in raw_text.splitlines():
        stripped = line.strip()
        if "nameserver[" in stripped and ":" in stripped:
            nameservers.append(stripped.split(":", 1)[1].strip())
            continue
        if "search domain[" in stripped and ":" in stripped:
            search_domains.append(stripped.split(":", 1)[1].strip())
    return DNSConfig(
        host_name=host_name,
        fqdn=fqdn,
        nameservers=_dedupe(nameservers),
        search_domains=_dedupe(search_domains),
    )


def _address_family_label(family: Any) -> str:
    if family == socket.AF_INET:
        return "IPv4"
    if family == socket.AF_INET6:
        return "IPv6"
    return "Link"


def _platform_name(platform_name: str | None = None) -> str:
    return (platform_name or platform.system()).strip().lower()


def _run_capture(command: list[str]) -> str:
    run_kwargs: dict[str, Any] = {
        "check": False,
        "capture_output": True,
        "text": True,
        "timeout": 6,
    }
    if _platform_name() == "windows":
        creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if creationflags:
            run_kwargs["creationflags"] = creationflags
    completed = subprocess.run(command, **run_kwargs)
    output = (completed.stdout or "").strip()
    stderr = (completed.stderr or "").strip()
    if completed.returncode != 0 and not output:
        raise ValueError(stderr or f"Command failed: {' '.join(command)}")
    return output


def _launch_ports_helper_plain(session_dir: str | Path, *, platform_name: str | None = None) -> list[str]:
    command = network_inspector_ports_helper_command(session_dir)
    system = _platform_name(platform_name)
    if system == "windows":
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


def _privileged_ports_session_paths(session_dir: str | Path) -> PrivilegedPortsSessionPaths:
    root = Path(session_dir).expanduser()
    return PrivilegedPortsSessionPaths(
        root=root,
        ready_path=root / "ready",
        request_path=root / "request",
        response_path=root / "response",
        stop_path=root / "stop",
        metadata_path=root / "session.json",
    )


def _read_json_dict(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if isinstance(payload, dict):
        return payload
    return {}


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temp_path.replace(path)


def _normalize_pid(value: Any) -> int | None:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None
    return normalized if normalized > 0 else None


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _is_effectively_elevated(platform_name: str | None = None) -> bool:
    system = _platform_name(platform_name)
    if system == "windows":
        try:
            import ctypes

            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    try:
        return os.geteuid() == 0
    except Exception:
        return False


def _escape_applescript(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _looks_like_windows_dns_server(value: str) -> bool:
    candidate = value.strip()
    if not candidate:
        return False
    if "%" in candidate and ":" in candidate:
        candidate = candidate.split("%", 1)[0].strip()
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return False
    return True


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = value.strip()
        if not cleaned:
            continue
        lowered = cleaned.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        result.append(cleaned)
    return result


def _psutil():
    try:
        import psutil
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("psutil is required for the Network Inspector tool.") from exc
    return psutil
