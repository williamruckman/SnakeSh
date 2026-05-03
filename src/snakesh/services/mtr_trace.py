from __future__ import annotations

import csv
from dataclasses import dataclass, replace
from io import StringIO
import ipaddress
import json
import math
import os
from pathlib import Path
import platform
from queue import Empty, Queue
import re
import select
import shlex
import shutil
import signal
import socket
import struct
import subprocess
import threading
import time
from typing import Any
from uuid import uuid4

from snakesh import runtime


MTR_HELPER_FLAG = "--mtr-helper"
MTR_PROTOCOL_AUTO = "AUTO"
MTR_PROTOCOL_ICMP = "ICMP"
MTR_PROTOCOL_UDP = "UDP"
MTR_PROTOCOLS: tuple[str, ...] = (MTR_PROTOCOL_AUTO, MTR_PROTOCOL_ICMP, MTR_PROTOCOL_UDP)
MTR_TABLE_HEADERS: tuple[str, ...] = (
    "Hop",
    "Host",
    "Address",
    "Loss%",
    "Sent",
    "Recv",
    "Last",
    "Avg",
    "Best",
    "Worst",
    "StDev",
)

_HELPER_POLL_INTERVAL_SECONDS = 0.1
_DEFAULT_UDP_BASE_PORT = 33434
_ICMP_ECHO_REQUEST = 8
_ICMP_ECHO_REPLY = 0
_ICMP_TIME_EXCEEDED = 11
_ICMP_DEST_UNREACHABLE = 3
_ICMP_PORT_UNREACHABLE_CODE = 3
_ICMPV6_DEST_UNREACHABLE = 1
_ICMPV6_TIME_EXCEEDED = 3
_ICMPV6_ECHO_REQUEST = 128
_ICMPV6_ECHO_REPLY = 129
_ICMPV6_PORT_UNREACHABLE_CODE = 4
_ICMP_PAYLOAD_PREFIX = b"snakesh-mtr"


@dataclass(frozen=True, slots=True)
class MTRTraceRequest:
    target: str
    max_hops: int = 30
    timeout_ms: int = 3000
    interval_ms: int = 1000
    cycles: int = 0
    protocol: str = MTR_PROTOCOL_AUTO
    resolve_hostnames: bool = True
    ipv6: bool = False
    fast_mode: bool = False

    def normalized(self) -> "MTRTraceRequest":
        target = self.target.strip()
        if not target:
            raise ValueError("Enter a host or IP address.")
        protocol = self.protocol.strip().upper() or MTR_PROTOCOL_AUTO
        if protocol not in MTR_PROTOCOLS:
            raise ValueError(f"Unsupported traceroute protocol: {protocol}")
        max_hops = max(1, min(255, int(self.max_hops)))
        timeout_ms = max(100, int(self.timeout_ms))
        interval_ms = max(100, int(self.interval_ms))
        cycles = max(0, int(self.cycles))
        return MTRTraceRequest(
            target=target,
            max_hops=max_hops,
            timeout_ms=timeout_ms,
            interval_ms=interval_ms,
            cycles=cycles,
            protocol=protocol,
            resolve_hostnames=bool(self.resolve_hostnames),
            ipv6=bool(self.ipv6),
            fast_mode=bool(self.fast_mode),
        )

    def to_dict(self) -> dict[str, object]:
        normalized = self.normalized()
        return {
            "target": normalized.target,
            "max_hops": normalized.max_hops,
            "timeout_ms": normalized.timeout_ms,
            "interval_ms": normalized.interval_ms,
            "cycles": normalized.cycles,
            "protocol": normalized.protocol,
            "resolve_hostnames": normalized.resolve_hostnames,
            "ipv6": normalized.ipv6,
            "fast_mode": normalized.fast_mode,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "MTRTraceRequest":
        return cls(
            target=str(raw.get("target", "")).strip(),
            max_hops=int(raw.get("max_hops", 30) or 30),
            timeout_ms=int(raw.get("timeout_ms", 3000) or 3000),
            interval_ms=int(raw.get("interval_ms", 1000) or 1000),
            cycles=int(raw.get("cycles", 0) or 0),
            protocol=str(raw.get("protocol", MTR_PROTOCOL_AUTO)).strip() or MTR_PROTOCOL_AUTO,
            resolve_hostnames=bool(raw.get("resolve_hostnames", True)),
            ipv6=bool(raw.get("ipv6", False)),
            fast_mode=bool(raw.get("fast_mode", False)),
        ).normalized()


@dataclass(frozen=True, slots=True)
class MTRHopSnapshot:
    hop: int
    host: str
    address: str
    sent: int
    received: int
    loss_percent: float
    last_ms: float | None
    avg_ms: float | None
    best_ms: float | None
    worst_ms: float | None
    stdev_ms: float | None
    reached_destination: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "hop": self.hop,
            "host": self.host,
            "address": self.address,
            "sent": self.sent,
            "received": self.received,
            "loss_percent": self.loss_percent,
            "last_ms": self.last_ms,
            "avg_ms": self.avg_ms,
            "best_ms": self.best_ms,
            "worst_ms": self.worst_ms,
            "stdev_ms": self.stdev_ms,
            "reached_destination": self.reached_destination,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "MTRHopSnapshot":
        return cls(
            hop=max(1, int(raw.get("hop", 1) or 1)),
            host=str(raw.get("host", "")).strip(),
            address=str(raw.get("address", "")).strip(),
            sent=max(0, int(raw.get("sent", 0) or 0)),
            received=max(0, int(raw.get("received", 0) or 0)),
            loss_percent=float(raw.get("loss_percent", 0.0) or 0.0),
            last_ms=_optional_float(raw.get("last_ms")),
            avg_ms=_optional_float(raw.get("avg_ms")),
            best_ms=_optional_float(raw.get("best_ms")),
            worst_ms=_optional_float(raw.get("worst_ms")),
            stdev_ms=_optional_float(raw.get("stdev_ms")),
            reached_destination=bool(raw.get("reached_destination", False)),
        )


@dataclass(frozen=True, slots=True)
class MTRTraceSnapshot:
    state: str
    message: str
    cycle: int
    target: str
    protocol: str
    ipv6: bool
    hops: list[MTRHopSnapshot]

    def to_dict(self) -> dict[str, object]:
        return {
            "state": self.state,
            "message": self.message,
            "cycle": self.cycle,
            "target": self.target,
            "protocol": self.protocol,
            "ipv6": self.ipv6,
            "hops": [hop.to_dict() for hop in self.hops],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "MTRTraceSnapshot":
        hops_raw = raw.get("hops", [])
        hops = [
            MTRHopSnapshot.from_dict(item)
            for item in hops_raw
            if isinstance(item, dict)
        ]
        return cls(
            state=str(raw.get("state", "idle")).strip() or "idle",
            message=str(raw.get("message", "")).strip(),
            cycle=max(0, int(raw.get("cycle", 0) or 0)),
            target=str(raw.get("target", "")).strip(),
            protocol=str(raw.get("protocol", MTR_PROTOCOL_AUTO)).strip() or MTR_PROTOCOL_AUTO,
            ipv6=bool(raw.get("ipv6", False)),
            hops=sorted(hops, key=lambda hop: hop.hop),
        )


@dataclass(frozen=True, slots=True)
class MTRProbeSample:
    sample_index: int
    timestamp_ms: int
    cycle: int
    hop: int
    host: str
    address: str
    success: bool
    timeout: bool
    rtt_ms: float | None
    reached_destination: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "sample_index": self.sample_index,
            "timestamp_ms": self.timestamp_ms,
            "cycle": self.cycle,
            "hop": self.hop,
            "host": self.host,
            "address": self.address,
            "success": self.success,
            "timeout": self.timeout,
            "rtt_ms": self.rtt_ms,
            "reached_destination": self.reached_destination,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "MTRProbeSample":
        return cls(
            sample_index=max(1, int(raw.get("sample_index", 1) or 1)),
            timestamp_ms=max(0, int(raw.get("timestamp_ms", 0) or 0)),
            cycle=max(0, int(raw.get("cycle", 0) or 0)),
            hop=max(1, int(raw.get("hop", 1) or 1)),
            host=str(raw.get("host", "")).strip(),
            address=str(raw.get("address", "")).strip(),
            success=bool(raw.get("success", False)),
            timeout=bool(raw.get("timeout", False)),
            rtt_ms=_optional_float(raw.get("rtt_ms")),
            reached_destination=bool(raw.get("reached_destination", False)),
        )


@dataclass(frozen=True, slots=True)
class MTRHelperSessionPaths:
    root: Path
    config_path: Path
    state_path: Path
    samples_path: Path
    ready_path: Path
    stop_path: Path
    shutdown_path: Path


@dataclass(frozen=True, slots=True)
class _ResolvedTarget:
    family: int
    address: str
    sockaddr: tuple[object, ...]
    source_address: str


@dataclass(frozen=True, slots=True)
class _ProbeToken:
    protocol: str
    identifier: int = 0
    sequence: int = 0
    dest_port: int = 0


@dataclass(frozen=True, slots=True)
class _ProbeReply:
    responder_address: str
    reached_destination: bool
    rtt_ms: float


@dataclass(frozen=True, slots=True)
class _CycleHopMeasurement:
    hop: int
    host: str
    address: str
    sent: int
    samples_ms: list[float]
    probe_results: list["_ProbeResult"]
    reached_destination: bool


@dataclass(frozen=True, slots=True)
class _TraceCycleCommandSpec:
    command: list[str]
    parser_kind: str
    effective_protocol: str
    note: str


@dataclass(frozen=True, slots=True)
class _ParsedICMPReply:
    responder_address: str
    icmp_type: int
    icmp_code: int
    inner_protocol: str
    identifier: int
    sequence: int
    dest_port: int
    reached_destination: bool


@dataclass(frozen=True, slots=True)
class _ProbeResult:
    success: bool
    timeout: bool
    rtt_ms: float | None


@dataclass(frozen=True, slots=True)
class _PendingProbe:
    token: _ProbeToken
    sample_index: int
    cycle: int
    hop: int
    started_at: float
    deadline_at: float


class _HopStats:
    def __init__(self, hop: int) -> None:
        self.hop = hop
        self.host = ""
        self.address = ""
        self.sent = 0
        self.received = 0
        self._last_ms: float | None = None
        self._best_ms: float | None = None
        self._worst_ms: float | None = None
        self._mean_ms = 0.0
        self._m2 = 0.0
        self.reached_destination = False

    def record_timeout(self) -> None:
        self.sent += 1

    def record_reply(self, *, address: str, host: str, rtt_ms: float, reached_destination: bool) -> None:
        self.sent += 1
        self.received += 1
        self.address = address
        self.host = host
        self.reached_destination = self.reached_destination or reached_destination
        self._last_ms = rtt_ms
        if self._best_ms is None or rtt_ms < self._best_ms:
            self._best_ms = rtt_ms
        if self._worst_ms is None or rtt_ms > self._worst_ms:
            self._worst_ms = rtt_ms

        delta = rtt_ms - self._mean_ms
        self._mean_ms += delta / self.received
        delta2 = rtt_ms - self._mean_ms
        self._m2 += delta * delta2

    def snapshot(self) -> MTRHopSnapshot:
        loss_percent = 0.0
        if self.sent > 0:
            loss_percent = ((self.sent - self.received) / self.sent) * 100.0
        stdev_ms = None
        if self.received > 1:
            stdev_ms = math.sqrt(self._m2 / self.received)
        return MTRHopSnapshot(
            hop=self.hop,
            host=self.host,
            address=self.address,
            sent=self.sent,
            received=self.received,
            loss_percent=loss_percent,
            last_ms=self._last_ms,
            avg_ms=self._mean_ms if self.received > 0 else None,
            best_ms=self._best_ms,
            worst_ms=self._worst_ms,
            stdev_ms=stdev_ms,
            reached_destination=self.reached_destination,
        )


def mtr_helper_session_paths(session_dir: str | Path) -> MTRHelperSessionPaths:
    root = Path(session_dir).expanduser()
    return MTRHelperSessionPaths(
        root=root,
        config_path=root / "config.json",
        state_path=root / "state.json",
        samples_path=root / "samples.jsonl",
        ready_path=root / "ready",
        stop_path=root / "stop",
        shutdown_path=root / "shutdown",
    )


def mtr_helper_command(session_dir: str | Path) -> list[str]:
    return runtime.self_launch_command([MTR_HELPER_FLAG, str(Path(session_dir).expanduser())])


def launch_mtr_helper(session_dir: str | Path, *, platform_name: str | None = None) -> list[str]:
    return _launch_mtr_helper_plain(session_dir)


def supports_mtr_fast_mode(*, platform_name: str | None = None) -> bool:
    return _platform_name(platform_name) != "windows"


def needs_mtr_helper_elevation(request: MTRTraceRequest, *, platform_name: str | None = None) -> bool:
    normalized = request.normalized()
    return supports_mtr_fast_mode(platform_name=platform_name) and normalized.fast_mode and not _is_effectively_elevated(platform_name)


def launch_mtr_helper_elevated(session_dir: str | Path, *, platform_name: str | None = None) -> list[str]:
    system = _platform_name(platform_name)
    command = mtr_helper_command(session_dir)
    shell_command = f"nohup {shlex.join(command)} >/dev/null 2>&1 &"
    if system == "linux":
        if shutil.which("pkexec") is None:
            raise ValueError("pkexec is required to run Traceroute with elevated privileges on Linux.")
        result = subprocess.run(  # noqa: S603
            ["pkexec", "/bin/sh", "-c", shell_command],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise ValueError((result.stderr or result.stdout or "Privileged traceroute launch failed.").strip())
        return command
    if system == "darwin":
        if shutil.which("osascript") is None:
            raise ValueError("osascript is required to run Traceroute with elevated privileges on macOS.")
        script = f'do shell script "{_escape_applescript(shell_command)}" with administrator privileges'
        result = subprocess.run(  # noqa: S603
            ["osascript", "-e", script],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise ValueError((result.stderr or result.stdout or "Privileged traceroute launch failed.").strip())
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
                _windows_powershell_executable(platform_name=system),
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
            raise ValueError((result.stderr or result.stdout or "Privileged traceroute launch failed.").strip())
        return command
    raise ValueError("Traceroute elevation is not supported on this platform.")


def write_mtr_config(
    session_dir: str | Path,
    request: MTRTraceRequest,
    *,
    parent_pid: int | None = None,
    persistent: bool = False,
) -> str:
    paths = mtr_helper_session_paths(session_dir)
    paths.root.mkdir(parents=True, exist_ok=True)
    for path in (paths.stop_path, paths.state_path, paths.samples_path, paths.shutdown_path):
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass
    if not persistent:
        try:
            paths.ready_path.unlink(missing_ok=True)
        except Exception:
            pass
    payload = request.to_dict()
    payload["parent_pid"] = int(parent_pid or os.getpid())
    if persistent:
        payload["persistent"] = True
        payload["request_id"] = uuid4().hex
    _write_json_atomic(paths.config_path, payload)
    return str(payload.get("request_id", ""))


def read_mtr_snapshot(session_dir: str | Path) -> MTRTraceSnapshot | None:
    paths = mtr_helper_session_paths(session_dir)
    raw = _read_json_dict(paths.state_path)
    if not raw:
        return None
    return MTRTraceSnapshot.from_dict(raw)


def read_mtr_probe_samples(session_dir: str | Path) -> list[MTRProbeSample]:
    paths = mtr_helper_session_paths(session_dir)
    if not paths.samples_path.exists():
        return []
    samples: list[MTRProbeSample] = []
    try:
        with paths.samples_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                payload = _read_jsonl_line(line)
                if not isinstance(payload, dict):
                    continue
                try:
                    samples.append(MTRProbeSample.from_dict(payload))
                except Exception:
                    continue
    except FileNotFoundError:
        return []
    return sorted(samples, key=lambda sample: sample.sample_index)


def format_mtr_report(snapshot: MTRTraceSnapshot) -> str:
    lines = [
        f"Traceroute report for {snapshot.target}",
        f"State: {snapshot.state}",
        f"Protocol: {snapshot.protocol}",
        f"IP mode: {'IPv6' if snapshot.ipv6 else 'IPv4'}",
        f"Cycles completed: {snapshot.cycle}",
    ]
    if snapshot.message:
        lines.append(f"Message: {snapshot.message}")
    lines.append("")
    lines.append("\t".join(MTR_TABLE_HEADERS))
    for hop in snapshot.hops:
        lines.append(
            "\t".join(
                [
                    str(hop.hop),
                    hop.host,
                    hop.address,
                    _format_ms(hop.loss_percent),
                    str(hop.sent),
                    str(hop.received),
                    _format_optional_ms(hop.last_ms),
                    _format_optional_ms(hop.avg_ms),
                    _format_optional_ms(hop.best_ms),
                    _format_optional_ms(hop.worst_ms),
                    _format_optional_ms(hop.stdev_ms),
                ]
            )
        )
    return "\n".join(lines).strip() + "\n"


def format_mtr_samples_csv(samples: list[MTRProbeSample]) -> str:
    output = StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(
        [
            "sample_index",
            "timestamp_ms",
            "cycle",
            "hop",
            "host",
            "address",
            "success",
            "timeout",
            "rtt_ms",
            "reached_destination",
        ]
    )
    for sample in samples:
        writer.writerow(
            [
                sample.sample_index,
                sample.timestamp_ms,
                sample.cycle,
                sample.hop,
                sample.host,
                sample.address,
                str(sample.success).lower(),
                str(sample.timeout).lower(),
                "" if sample.rtt_ms is None else f"{sample.rtt_ms:.3f}",
                str(sample.reached_destination).lower(),
            ]
        )
    return output.getvalue()


def run_mtr_helper(session_dir: str | Path) -> int:
    paths = mtr_helper_session_paths(session_dir)
    raw_config = _read_json_dict(paths.config_path)
    if bool(raw_config.get("persistent", False)):
        return _run_mtr_helper_session(paths)
    return _run_mtr_helper_once(paths, raw_config)


def _run_mtr_helper_once(paths: MTRHelperSessionPaths, raw_config: dict[str, Any]) -> int:
    parent_pid = _normalize_pid(raw_config.get("parent_pid")) or 0
    try:
        request = MTRTraceRequest.from_dict(raw_config)
    except Exception as exc:  # noqa: BLE001
        _write_json_atomic(
            paths.state_path,
            MTRTraceSnapshot(
                state="error",
                message=str(exc),
                cycle=0,
                target=str(raw_config.get("target", "")).strip(),
                protocol=str(raw_config.get("protocol", MTR_PROTOCOL_AUTO)).strip() or MTR_PROTOCOL_AUTO,
                ipv6=bool(raw_config.get("ipv6", False)),
                hops=[],
            ).to_dict(),
        )
        return 1

    try:
        paths.root.mkdir(parents=True, exist_ok=True)
        _write_mtr_helper_ready(paths, parent_pid)
        final_snapshot = _run_mtr_helper_request(paths, raw_config, parent_pid=parent_pid, request=request)
        return 0 if final_snapshot.state in {"completed", "stopped"} else 1
    except Exception as exc:  # noqa: BLE001
        error_snapshot = MTRTraceSnapshot(
            state="error",
            message=str(exc),
            cycle=0,
            target=request.target,
            protocol=request.protocol,
            ipv6=request.ipv6,
            hops=[],
        )
        _write_json_atomic(paths.state_path, error_snapshot.to_dict())
        return 1
    finally:
        try:
            paths.ready_path.unlink(missing_ok=True)
        except Exception:
            pass


def _run_mtr_helper_session(paths: MTRHelperSessionPaths) -> int:
    parent_pid = 0
    last_request_id = ""
    last_ready_parent_pid: int | None = None
    try:
        paths.root.mkdir(parents=True, exist_ok=True)
        while True:
            raw_config = _read_json_dict(paths.config_path)
            next_parent_pid = _normalize_pid(raw_config.get("parent_pid"))
            if next_parent_pid is not None:
                parent_pid = next_parent_pid
            if not paths.ready_path.exists() or last_ready_parent_pid != parent_pid:
                _write_mtr_helper_ready(paths, parent_pid)
                last_ready_parent_pid = parent_pid
            if paths.shutdown_path.exists():
                return 0
            if parent_pid > 0 and not _pid_exists(parent_pid):
                return 0

            request_id = str(raw_config.get("request_id", "")).strip()
            if request_id and request_id != last_request_id:
                last_request_id = request_id
                _run_mtr_helper_request(paths, raw_config, parent_pid=parent_pid)
            time.sleep(_HELPER_POLL_INTERVAL_SECONDS)
    finally:
        for path in (paths.ready_path,):
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass


def _write_mtr_helper_ready(paths: MTRHelperSessionPaths, parent_pid: int) -> None:
    _write_json_atomic(
        paths.ready_path,
        {
            "pid": os.getpid(),
            "parent_pid": parent_pid,
        },
    )


def _run_mtr_helper_request(
    paths: MTRHelperSessionPaths,
    raw_config: dict[str, Any],
    *,
    parent_pid: int,
    request: MTRTraceRequest | None = None,
) -> MTRTraceSnapshot:
    try:
        normalized_request = request or MTRTraceRequest.from_dict(raw_config)
    except Exception as exc:  # noqa: BLE001
        snapshot = MTRTraceSnapshot(
            state="error",
            message=str(exc),
            cycle=0,
            target=str(raw_config.get("target", "")).strip(),
            protocol=str(raw_config.get("protocol", MTR_PROTOCOL_AUTO)).strip() or MTR_PROTOCOL_AUTO,
            ipv6=bool(raw_config.get("ipv6", False)),
            hops=[],
        )
        _write_json_atomic(paths.state_path, snapshot.to_dict())
        return snapshot

    _write_json_atomic(paths.state_path, _starting_mtr_snapshot(normalized_request).to_dict())
    try:
        final_snapshot = _run_trace_loop(
            normalized_request,
            stop_path=paths.stop_path,
            parent_pid=parent_pid,
            state_callback=lambda snapshot: _write_json_atomic(paths.state_path, snapshot.to_dict()),
            sample_callback=lambda sample: _append_jsonl(paths.samples_path, sample.to_dict()),
        )
    except Exception as exc:  # noqa: BLE001
        final_snapshot = MTRTraceSnapshot(
            state="error",
            message=str(exc),
            cycle=0,
            target=normalized_request.target,
            protocol=normalized_request.protocol,
            ipv6=normalized_request.ipv6,
            hops=[],
        )
    _write_json_atomic(paths.state_path, final_snapshot.to_dict())
    return final_snapshot


def _starting_mtr_snapshot(request: MTRTraceRequest) -> MTRTraceSnapshot:
    native_prefix = "fast native " if _uses_native_trace_loop(request) else ""
    return MTRTraceSnapshot(
        state="starting",
        message=(
            f"Starting {native_prefix}"
            f"{'automatic' if request.protocol == MTR_PROTOCOL_AUTO else request.protocol} "
            f"traceroute to {request.target}..."
        ),
        cycle=0,
        target=request.target,
        protocol=request.protocol,
        ipv6=request.ipv6,
        hops=[],
    )


def _run_trace_loop(
    request: MTRTraceRequest,
    *,
    stop_path: Path,
    parent_pid: int,
    state_callback,
    sample_callback,
) -> MTRTraceSnapshot:
    if _uses_native_trace_loop(request):
        return _run_trace_loop_native(
            request,
            stop_path=stop_path,
            parent_pid=parent_pid,
            state_callback=state_callback,
            sample_callback=sample_callback,
        )
    return _run_trace_loop_command(
        request,
        stop_path=stop_path,
        parent_pid=parent_pid,
        state_callback=state_callback,
        sample_callback=sample_callback,
    )


def _uses_native_trace_loop(request: MTRTraceRequest, *, platform_name: str | None = None) -> bool:
    return request.fast_mode and supports_mtr_fast_mode(platform_name=platform_name)


def _run_trace_loop_command(
    request: MTRTraceRequest,
    *,
    stop_path: Path,
    parent_pid: int,
    state_callback,
    sample_callback,
) -> MTRTraceSnapshot:
    resolved_target = _resolve_target(request)
    hop_stats: dict[int, _HopStats] = {}
    cycle = 0
    protocol_in_use = request.protocol
    next_sample_index = 1

    while True:
        if _should_stop(stop_path, parent_pid):
            return _build_snapshot(
                request,
                hop_stats,
                cycle=cycle,
                state="stopped",
                message="Trace stopped.",
                protocol=protocol_in_use,
            )

        if request.cycles > 0 and cycle >= request.cycles:
            return _build_snapshot(
                request,
                hop_stats,
                cycle=cycle,
                state="completed",
                message=f"Trace complete after {cycle} cycle(s).",
                protocol=protocol_in_use,
            )

        cycle += 1
        stopped, protocol_in_use, note, next_sample_index = _run_trace_cycle(
            request,
            destination_address=resolved_target.address,
            stop_path=stop_path,
            parent_pid=parent_pid,
            hop_stats=hop_stats,
            cycle=cycle,
            state_callback=state_callback,
            sample_callback=sample_callback,
            next_sample_index=next_sample_index,
        )
        if stopped:
            return _build_snapshot(
                request,
                hop_stats,
                cycle=cycle,
                state="stopped",
                message="Trace stopped.",
                protocol=protocol_in_use,
            )

        if request.cycles > 0 and cycle >= request.cycles:
            return _build_snapshot(
                request,
                hop_stats,
                cycle=cycle,
                state="completed",
                message=(note + " " if note else "") + f"Trace complete after {cycle} cycle(s).",
                protocol=protocol_in_use,
            )

        if _wait_for_interval(stop_path, parent_pid, request.interval_ms):
            return _build_snapshot(
                request,
                hop_stats,
                cycle=cycle,
                state="stopped",
                message="Trace stopped.",
                protocol=protocol_in_use,
            )


def _run_trace_loop_native(
    request: MTRTraceRequest,
    *,
    stop_path: Path,
    parent_pid: int,
    state_callback,
    sample_callback,
) -> MTRTraceSnapshot:
    resolved_target = _resolve_target(request)
    hop_stats: dict[int, _HopStats] = {}
    host_cache: dict[str, str] = {}
    protocol_in_use = _native_effective_protocol(request)
    cycle = 0
    next_sample_index = 1
    next_token_value = 1
    next_launch_at = time.perf_counter()
    interval_seconds = request.interval_ms / 1000.0
    timeout_seconds = request.timeout_ms / 1000.0
    destination_hop: int | None = None
    pending_probes: dict[tuple[str, int, int, int], _PendingProbe] = {}

    receive_socket = _open_receive_socket(resolved_target.family)
    send_socket = _open_send_socket(resolved_target.family, protocol_in_use)
    try:
        while True:
            if _should_stop(stop_path, parent_pid):
                return _build_snapshot(
                    request,
                    hop_stats,
                    cycle=cycle,
                    state="stopped",
                    message="Trace stopped.",
                    protocol=protocol_in_use,
                )

            now = time.perf_counter()
            can_launch_more = request.cycles == 0 or cycle < request.cycles
            while can_launch_more and now >= next_launch_at:
                cycle += 1
                cycle_hop_limit = min(request.max_hops, destination_hop or request.max_hops)
                for hop in range(1, cycle_hop_limit + 1):
                    token = _build_probe_token(
                        protocol_in_use,
                        identifier=(next_token_value >> 16) & 0xFFFF,
                        sequence=next_token_value & 0xFFFF,
                    )
                    next_token_value += 1
                    started_at = time.perf_counter()
                    _send_probe(
                        protocol=protocol_in_use,
                        request=request,
                        resolved_target=resolved_target,
                        send_socket=send_socket,
                        hop=hop,
                        token=token,
                    )
                    pending_probes[_probe_token_key(token)] = _PendingProbe(
                        token=token,
                        sample_index=next_sample_index,
                        cycle=cycle,
                        hop=hop,
                        started_at=started_at,
                        deadline_at=started_at + timeout_seconds,
                    )
                    next_sample_index += 1
                state_callback(
                    _build_snapshot(
                        request,
                        hop_stats,
                        cycle=cycle,
                        state="running",
                        message=f"Cycle {cycle}: launched {cycle_hop_limit} native probe(s).",
                        protocol=protocol_in_use,
                    )
                )
                next_launch_at += interval_seconds
                now = time.perf_counter()
                can_launch_more = request.cycles == 0 or cycle < request.cycles

            expired_keys = [
                key
                for key, pending in pending_probes.items()
                if pending.deadline_at <= now
            ]
            if expired_keys:
                for key in sorted(expired_keys, key=lambda item: pending_probes[item].sample_index):
                    pending = pending_probes.pop(key, None)
                    if pending is None:
                        continue
                    if destination_hop is not None and pending.hop > destination_hop:
                        continue
                    _emit_pending_probe_result(
                        request=request,
                        hop_stats=hop_stats,
                        pending=pending,
                        probe_result=_ProbeResult(success=False, timeout=True, rtt_ms=None),
                        host="",
                        address="",
                        reached_destination=False,
                        cycle=cycle,
                        protocol=protocol_in_use,
                        state_callback=state_callback,
                        sample_callback=sample_callback,
                    )
                continue

            if request.cycles > 0 and cycle >= request.cycles and not pending_probes:
                return _build_snapshot(
                    request,
                    hop_stats,
                    cycle=cycle,
                    state="completed",
                    message=f"Trace complete after {cycle} cycle(s).",
                    protocol=protocol_in_use,
                )

            wait_timeout = _HELPER_POLL_INTERVAL_SECONDS
            if can_launch_more:
                wait_timeout = min(wait_timeout, max(0.0, next_launch_at - now))
            next_deadline = _next_pending_probe_timeout(pending_probes)
            if next_deadline is not None:
                wait_timeout = min(wait_timeout, max(0.0, next_deadline - now))

            if not pending_probes:
                time.sleep(max(0.0, wait_timeout))
                continue

            ready, _, _ = select.select([receive_socket], [], [], max(0.0, wait_timeout))
            if not ready:
                continue

            while True:
                try:
                    payload, address = receive_socket.recvfrom(65535)
                except BlockingIOError:
                    break
                except OSError:
                    break
                reply = _parse_probe_reply(payload, address, resolved_target.family)
                if reply is None:
                    continue
                pending = pending_probes.pop(_parsed_reply_key(reply), None)
                if pending is None:
                    continue
                if destination_hop is not None and pending.hop > destination_hop:
                    continue
                responder_address = reply.responder_address
                host = _resolve_host_name(responder_address, host_cache, request.resolve_hostnames)
                _emit_pending_probe_result(
                    request=request,
                    hop_stats=hop_stats,
                    pending=pending,
                    probe_result=_ProbeResult(
                        success=True,
                        timeout=False,
                        rtt_ms=(time.perf_counter() - pending.started_at) * 1000.0,
                    ),
                    host=host,
                    address=responder_address,
                    reached_destination=reply.reached_destination,
                    cycle=cycle,
                    protocol=protocol_in_use,
                    state_callback=state_callback,
                    sample_callback=sample_callback,
                )
                if reply.reached_destination and (destination_hop is None or pending.hop < destination_hop):
                    destination_hop = pending.hop
                    pending_probes = {
                        key: active_pending
                        for key, active_pending in pending_probes.items()
                        if active_pending.hop <= destination_hop
                    }
    finally:
        try:
            receive_socket.close()
        except Exception:
            pass
        try:
            send_socket.close()
        except Exception:
            pass


def _native_effective_protocol(request: MTRTraceRequest, *, platform_name: str | None = None) -> str:
    if request.protocol != MTR_PROTOCOL_AUTO:
        return request.protocol
    if _platform_name(platform_name) == "windows":
        return MTR_PROTOCOL_ICMP
    return MTR_PROTOCOL_UDP


def _probe_token_key(token: _ProbeToken) -> tuple[str, int, int, int]:
    if token.protocol == MTR_PROTOCOL_UDP:
        return (token.protocol, 0, 0, token.dest_port)
    return (token.protocol, token.identifier, token.sequence, 0)


def _parsed_reply_key(reply: _ParsedICMPReply) -> tuple[str, int, int, int]:
    if reply.inner_protocol == MTR_PROTOCOL_UDP:
        return (reply.inner_protocol, 0, 0, reply.dest_port)
    return (reply.inner_protocol, reply.identifier, reply.sequence, 0)


def _next_pending_probe_timeout(pending_probes: dict[tuple[str, int, int, int], _PendingProbe]) -> float | None:
    if not pending_probes:
        return None
    return min(pending.deadline_at for pending in pending_probes.values())


def _emit_pending_probe_result(
    *,
    request: MTRTraceRequest,
    hop_stats: dict[int, _HopStats],
    pending: _PendingProbe,
    probe_result: _ProbeResult,
    host: str,
    address: str,
    reached_destination: bool,
    cycle: int,
    protocol: str,
    state_callback,
    sample_callback,
) -> None:
    host_value = host or address
    address_value = address or host
    sample_callback(
        MTRProbeSample(
            sample_index=pending.sample_index,
            timestamp_ms=time.time_ns() // 1_000_000,
            cycle=pending.cycle,
            hop=pending.hop,
            host=host_value,
            address=address_value,
            success=probe_result.success,
            timeout=probe_result.timeout,
            rtt_ms=probe_result.rtt_ms,
            reached_destination=reached_destination,
        )
    )
    _apply_cycle_measurement(
        hop_stats,
        _CycleHopMeasurement(
            hop=pending.hop,
            host=host_value,
            address=address_value,
            sent=1,
            samples_ms=[] if probe_result.rtt_ms is None else [probe_result.rtt_ms],
            probe_results=[probe_result],
            reached_destination=reached_destination,
        ),
    )
    state_callback(
        _build_snapshot(
            request,
            hop_stats,
            cycle=cycle,
            state="running",
            message=f"Cycle {pending.cycle}: processed hop {pending.hop}.",
            protocol=protocol,
        )
    )


def _probe_hop(
    *,
    request: MTRTraceRequest,
    resolved_target: _ResolvedTarget,
    receive_socket: socket.socket,
    send_socket: socket.socket,
    hop: int,
    token: _ProbeToken,
) -> _ProbeReply | None:
    started = time.perf_counter()
    _send_probe(
        protocol=token.protocol,
        request=request,
        resolved_target=resolved_target,
        send_socket=send_socket,
        hop=hop,
        token=token,
    )

    while True:
        remaining = request.timeout_ms / 1000.0 - (time.perf_counter() - started)
        if remaining <= 0:
            return None
        ready, _, _ = select.select([receive_socket], [], [], remaining)
        if not ready:
            return None
        try:
            payload, address = receive_socket.recvfrom(65535)
        except OSError:
            return None
        reply = _match_probe_reply(
            payload=payload,
            address=address,
            family=resolved_target.family,
            token=token,
        )
        if reply is None:
            continue
        return _ProbeReply(
            responder_address=reply.responder_address,
            reached_destination=reply.reached_destination,
            rtt_ms=(time.perf_counter() - started) * 1000.0,
        )


def _send_probe(
    *,
    protocol: str,
    request: MTRTraceRequest,
    resolved_target: _ResolvedTarget,
    send_socket: socket.socket,
    hop: int,
    token: _ProbeToken,
) -> None:
    _configure_hop_limit(send_socket, resolved_target.family, hop)
    if protocol == MTR_PROTOCOL_ICMP:
        packet = _build_icmp_packet(
            family=resolved_target.family,
            source_address=resolved_target.source_address,
            destination_address=resolved_target.address,
            identifier=token.identifier,
            sequence=token.sequence,
            hop=hop,
        )
        send_socket.sendto(packet, resolved_target.sockaddr)
        return

    packet = _build_udp_payload(token.dest_port, hop)
    udp_sockaddr = _udp_sockaddr(resolved_target.address, token.dest_port, request.ipv6)
    send_socket.sendto(packet, udp_sockaddr)


def _build_probe_token(protocol: str, *, identifier: int, sequence: int) -> _ProbeToken:
    if protocol == MTR_PROTOCOL_ICMP:
        return _ProbeToken(protocol=protocol, identifier=identifier, sequence=sequence)
    return _ProbeToken(
        protocol=protocol,
        identifier=identifier,
        sequence=sequence,
        dest_port=_DEFAULT_UDP_BASE_PORT + (sequence % 30000),
    )


def _build_snapshot(
    request: MTRTraceRequest,
    hop_stats: dict[int, _HopStats],
    *,
    cycle: int,
    state: str,
    message: str,
    protocol: str | None = None,
) -> MTRTraceSnapshot:
    destination_hop = min(
        (hop for hop, stats in hop_stats.items() if stats.reached_destination),
        default=None,
    )
    visible_hops = [
        hop
        for hop in sorted(hop_stats)
        if destination_hop is None or hop <= destination_hop
    ]
    return MTRTraceSnapshot(
        state=state,
        message=message,
        cycle=cycle,
        target=request.target,
        protocol=protocol or request.protocol,
        ipv6=request.ipv6,
        hops=[hop_stats[hop].snapshot() for hop in visible_hops],
    )


def build_trace_cycle_command(
    request: MTRTraceRequest,
    *,
    target_override: str | None = None,
    platform_name: str | None = None,
) -> tuple[list[str], str, str, str]:
    command_spec = build_trace_cycle_commands(request, target_override=target_override, platform_name=platform_name)[0]
    return (
        list(command_spec.command),
        command_spec.parser_kind,
        command_spec.effective_protocol,
        command_spec.note,
    )


def build_trace_cycle_commands(
    request: MTRTraceRequest,
    *,
    target_override: str | None = None,
    platform_name: str | None = None,
) -> list[_TraceCycleCommandSpec]:
    system = _platform_name(platform_name)
    command_target = (target_override or request.target).strip() or request.target
    if system == "windows":
        command = [_windows_tracert_executable(platform_name=system), "-h", str(request.max_hops), "-w", str(request.timeout_ms)]
        if not request.resolve_hostnames:
            command.insert(1, "-d")
        if request.ipv6:
            command.insert(1, "-6")
        command.append(command_target)
        note = ""
        if request.protocol == MTR_PROTOCOL_UDP:
            note = "UDP mode is not available via Windows tracert; using ICMP."
        return [_TraceCycleCommandSpec(command=command, parser_kind="windows", effective_protocol=MTR_PROTOCOL_ICMP, note=note)]

    candidates: list[_TraceCycleCommandSpec] = []
    has_traceroute = shutil.which("traceroute") is not None
    has_tracepath = shutil.which("tracepath") is not None

    if request.protocol == MTR_PROTOCOL_AUTO:
        if has_traceroute:
            candidates.append(
                _TraceCycleCommandSpec(
                    command=_build_traceroute_command(request, target=command_target, use_icmp=False, platform_name=system),
                    parser_kind="traceroute",
                    effective_protocol=MTR_PROTOCOL_UDP,
                    note="",
                )
            )
        elif has_tracepath:
            candidates.append(
                _TraceCycleCommandSpec(
                    command=_build_tracepath_command(request, target=command_target),
                    parser_kind="tracepath",
                    effective_protocol=MTR_PROTOCOL_UDP,
                    note="Using tracepath fallback.",
                )
            )
    elif request.protocol == MTR_PROTOCOL_ICMP:
        if has_traceroute:
            candidates.append(
                _TraceCycleCommandSpec(
                    command=_build_traceroute_command(request, target=command_target, use_icmp=True, platform_name=system),
                    parser_kind="traceroute",
                    effective_protocol=MTR_PROTOCOL_ICMP,
                    note="",
                )
            )
            candidates.append(
                _TraceCycleCommandSpec(
                    command=_build_traceroute_command(request, target=command_target, use_icmp=False, platform_name=system),
                    parser_kind="traceroute",
                    effective_protocol=MTR_PROTOCOL_UDP,
                    note="Native ICMP traceroute failed; using UDP traceroute fallback.",
                )
            )
        if has_tracepath:
            candidates.append(
                _TraceCycleCommandSpec(
                    command=_build_tracepath_command(request, target=command_target),
                    parser_kind="tracepath",
                    effective_protocol=MTR_PROTOCOL_UDP,
                    note=(
                        "Native ICMP traceroute failed; using tracepath fallback."
                        if has_traceroute
                        else "ICMP mode is not available without traceroute; using tracepath fallback."
                    ),
                )
            )
    elif request.protocol == MTR_PROTOCOL_UDP:
        if has_traceroute:
            candidates.append(
                _TraceCycleCommandSpec(
                    command=_build_traceroute_command(request, target=command_target, use_icmp=False, platform_name=system),
                    parser_kind="traceroute",
                    effective_protocol=MTR_PROTOCOL_UDP,
                    note="",
                )
            )
        if has_tracepath:
            candidates.append(
                _TraceCycleCommandSpec(
                    command=_build_tracepath_command(request, target=command_target),
                    parser_kind="tracepath",
                    effective_protocol=MTR_PROTOCOL_UDP,
                    note="Using tracepath fallback." if not has_traceroute else "UDP traceroute failed; using tracepath fallback.",
                )
            )

    deduped: list[_TraceCycleCommandSpec] = []
    seen: set[tuple[str, ...]] = set()
    for candidate in candidates:
        key = tuple(candidate.command)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)

    if deduped:
        return deduped
    raise ValueError("No traceroute tool is available. Install traceroute or tracepath.")


def _build_traceroute_command(
    request: MTRTraceRequest,
    *,
    target: str,
    use_icmp: bool,
    platform_name: str | None = None,
) -> list[str]:
    system = _platform_name(platform_name)
    timeout_seconds = max(1.0, request.timeout_ms / 1000.0)
    command = [
        "traceroute",
        "-m",
        str(request.max_hops),
        "-w",
        f"{timeout_seconds:.1f}",
        "-q",
        "1",
    ]
    if system == "linux":
        command.extend(["-N", "1"])
    if not request.resolve_hostnames:
        command.append("-n")
    if request.ipv6:
        command.append("-6")
    if use_icmp:
        command.append("-I")
    command.append(target)
    return command


def _build_tracepath_command(request: MTRTraceRequest, *, target: str) -> list[str]:
    command = ["tracepath", "-m", str(request.max_hops)]
    if not request.resolve_hostnames:
        command.append("-n")
    if request.ipv6:
        command.append("-6")
    command.append(target)
    return command


def _run_trace_cycle(
    request: MTRTraceRequest,
    *,
    destination_address: str,
    stop_path: Path,
    parent_pid: int,
    hop_stats: dict[int, _HopStats],
    cycle: int,
    state_callback,
    sample_callback,
    next_sample_index: int,
) -> tuple[bool, str, str, int]:
    errors: list[str] = []
    for command_spec in build_trace_cycle_commands(request, target_override=destination_address):
        try:
            stopped, next_sample_index = _run_trace_cycle_attempt(
                request,
                destination_address=destination_address,
                stop_path=stop_path,
                parent_pid=parent_pid,
                hop_stats=hop_stats,
                cycle=cycle,
                state_callback=state_callback,
                sample_callback=sample_callback,
                next_sample_index=next_sample_index,
                command_spec=command_spec,
            )
        except ValueError as exc:
            errors.append(str(exc))
            continue
        return stopped, command_spec.effective_protocol, command_spec.note, next_sample_index

    detail = "; ".join(dict.fromkeys(item for item in errors if item.strip()))
    raise ValueError(detail or "Traceroute did not return any hop data.")


def _run_trace_cycle_attempt(
    request: MTRTraceRequest,
    *,
    destination_address: str,
    stop_path: Path,
    parent_pid: int,
    hop_stats: dict[int, _HopStats],
    cycle: int,
    state_callback,
    sample_callback,
    next_sample_index: int,
    command_spec: _TraceCycleCommandSpec,
) -> tuple[bool, int]:
    command = list(command_spec.command)
    parser_kind = command_spec.parser_kind
    effective_protocol = command_spec.effective_protocol
    note = command_spec.note
    launch_command = _wrap_command_for_live_output(command)
    executable = command[0]
    timeout_seconds = _trace_cycle_timeout_seconds(request, parser_kind=parser_kind)
    output_lines: list[str] = []
    parsed_any = False
    stop_requested = False
    destination_hop: int | None = None
    completed_early = False

    try:
        process = subprocess.Popen(  # noqa: S603
            launch_command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            start_new_session=os.name != "nt",
            creationflags=_windows_process_creationflags(new_process_group=True, no_window=True) if os.name == "nt" else 0,
        )
    except FileNotFoundError as exc:
        raise ValueError(f"{executable} is not available.") from exc

    queue: Queue[str | None] = Queue()
    reader = threading.Thread(target=_read_process_lines, args=(process, queue), daemon=True)
    reader.start()

    deadline = time.monotonic() + timeout_seconds
    try:
        while True:
            if _should_stop(stop_path, parent_pid) and not stop_requested:
                stop_requested = True
                _terminate_process(process)

            try:
                line = queue.get(timeout=0.1)
            except Empty:
                line = None

            if line is not None:
                if line:
                    output_lines.append(line)
                    measurement = _parse_trace_output_line(
                        line,
                        parser_kind=parser_kind,
                        destination_address=destination_address,
                        resolve_hostnames=request.resolve_hostnames,
                    )
                    if measurement is not None:
                        if not measurement.reached_destination and _line_mentions_destination(line, destination_address):
                            measurement = replace(measurement, reached_destination=True)
                        if destination_hop is not None and measurement.hop > destination_hop:
                            continue
                        parsed_any = True
                        next_sample_index = _record_measurement_samples(
                            measurement,
                            cycle=cycle,
                            next_sample_index=next_sample_index,
                            sample_callback=sample_callback,
                        )
                        _apply_cycle_measurement(hop_stats, measurement)
                        state_callback(
                            _build_snapshot(
                                request,
                                hop_stats,
                                cycle=cycle,
                                state="running",
                                message=note or f"Cycle {cycle}: processed hop {measurement.hop}.",
                                protocol=effective_protocol,
                            )
                        )
                        if measurement.reached_destination and destination_hop is None:
                            destination_hop = measurement.hop
                            completed_early = True
                            if process.poll() is None:
                                _terminate_process(process)

            if process.poll() is not None and queue.empty():
                break

            if not stop_requested and time.monotonic() >= deadline:
                stop_requested = True
                _terminate_process(process)

        reader.join(timeout=1.0)
    finally:
        if process.poll() is None:
            _terminate_process(process)

    if stop_requested and not completed_early:
        return True, next_sample_index

    output = "\n".join(output_lines).strip()
    if not parsed_any and process.returncode not in (0, None):
        detail = output or f"{executable} exited with code {process.returncode}."
        raise ValueError(detail)
    if not parsed_any:
        raise ValueError(f"{executable} did not return any hop data.")
    return False, next_sample_index


def _parse_trace_cycle_output(
    output: str,
    *,
    parser_kind: str,
    destination_address: str,
    request: MTRTraceRequest,
) -> list[_CycleHopMeasurement]:
    if parser_kind == "windows":
        return _parse_windows_tracert_output(output, destination_address=destination_address)
    if parser_kind == "tracepath":
        return _parse_tracepath_output(output, destination_address=destination_address, resolve_hostnames=request.resolve_hostnames)
    return _parse_traceroute_output(output, destination_address=destination_address, resolve_hostnames=request.resolve_hostnames)


def _parse_trace_output_line(
    line: str,
    *,
    parser_kind: str,
    destination_address: str,
    resolve_hostnames: bool,
) -> _CycleHopMeasurement | None:
    if parser_kind == "windows":
        return _parse_windows_tracert_line(line, destination_address=destination_address)
    if parser_kind == "tracepath":
        return _parse_tracepath_line(line, destination_address=destination_address, resolve_hostnames=resolve_hostnames)
    return _parse_traceroute_line(line, destination_address=destination_address, resolve_hostnames=resolve_hostnames)


def _parse_windows_tracert_output(output: str, *, destination_address: str) -> list[_CycleHopMeasurement]:
    hops: list[_CycleHopMeasurement] = []
    for line in output.splitlines():
        measurement = _parse_windows_tracert_line(line, destination_address=destination_address)
        if measurement is not None:
            hops.append(measurement)
            if measurement.reached_destination:
                break
    return hops


def _parse_traceroute_output(
    output: str,
    *,
    destination_address: str,
    resolve_hostnames: bool,
) -> list[_CycleHopMeasurement]:
    hops: list[_CycleHopMeasurement] = []
    for line in output.splitlines():
        measurement = _parse_traceroute_line(line, destination_address=destination_address, resolve_hostnames=resolve_hostnames)
        if measurement is not None:
            hops.append(measurement)
            if measurement.reached_destination:
                break
    return hops


def _parse_tracepath_output(
    output: str,
    *,
    destination_address: str,
    resolve_hostnames: bool,
) -> list[_CycleHopMeasurement]:
    hops_by_number: dict[int, _CycleHopMeasurement] = {}
    for line in output.splitlines():
        measurement = _parse_tracepath_line(line, destination_address=destination_address, resolve_hostnames=resolve_hostnames)
        if measurement is not None:
            hops_by_number[measurement.hop] = measurement
            if measurement.reached_destination:
                break
    return [hops_by_number[hop] for hop in sorted(hops_by_number)]


def _read_process_lines(process: subprocess.Popen[str], queue: Queue[str | None]) -> None:
    stream = process.stdout
    if stream is None:
        queue.put(None)
        return
    try:
        for line in iter(stream.readline, ""):
            queue.put(line.rstrip("\r\n"))
    finally:
        queue.put(None)


def _terminate_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name != "nt" and process.pid > 0:
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
        process.wait(timeout=0.3)
        return
    except (ProcessLookupError, PermissionError, subprocess.TimeoutExpired):
        pass
    except Exception:
        try:
            process.terminate()
            process.wait(timeout=0.3)
            return
        except Exception:
            pass

    try:
        if os.name != "nt" and process.pid > 0:
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
        process.wait(timeout=0.3)
    except Exception:
        pass


def _apply_cycle_measurement(hop_stats: dict[int, _HopStats], measurement: _CycleHopMeasurement) -> None:
    stats = hop_stats.setdefault(measurement.hop, _HopStats(measurement.hop))
    host = measurement.host or measurement.address
    address = measurement.address or measurement.host
    probe_results = list(measurement.probe_results)
    if not probe_results:
        probe_results = [
            *[_ProbeResult(success=True, timeout=False, rtt_ms=rtt_ms) for rtt_ms in measurement.samples_ms],
            *[
                _ProbeResult(success=False, timeout=True, rtt_ms=None)
                for _ in range(max(0, measurement.sent - len(measurement.samples_ms)))
            ],
        ]

    for probe_result in probe_results:
        if probe_result.success and probe_result.rtt_ms is not None:
            stats.record_reply(
                address=address,
                host=host,
                rtt_ms=probe_result.rtt_ms,
                reached_destination=measurement.reached_destination,
            )
        else:
            stats.record_timeout()


def _record_measurement_samples(
    measurement: _CycleHopMeasurement,
    *,
    cycle: int,
    next_sample_index: int,
    sample_callback,
) -> int:
    timestamp_ms = time.time_ns() // 1_000_000
    probe_results = list(measurement.probe_results)
    if not probe_results:
        probe_results = [
            *[_ProbeResult(success=True, timeout=False, rtt_ms=rtt_ms) for rtt_ms in measurement.samples_ms],
            *[
                _ProbeResult(success=False, timeout=True, rtt_ms=None)
                for _ in range(max(0, measurement.sent - len(measurement.samples_ms)))
            ],
        ]

    host = measurement.host or measurement.address
    address = measurement.address or measurement.host
    for probe_result in probe_results:
        sample_callback(
            MTRProbeSample(
                sample_index=next_sample_index,
                timestamp_ms=timestamp_ms,
                cycle=cycle,
                hop=measurement.hop,
                host=host,
                address=address,
                success=probe_result.success,
                timeout=probe_result.timeout,
                rtt_ms=probe_result.rtt_ms,
                reached_destination=measurement.reached_destination,
            )
        )
        next_sample_index += 1
    return next_sample_index


def _parse_windows_tracert_line(line: str, *, destination_address: str) -> _CycleHopMeasurement | None:
    match = re.match(
        r"^\s*(\d+)\s+((?:<\d+(?:\.\d+)?|\d+(?:\.\d+)?)\s*ms|\*)\s+"
        r"((?:<\d+(?:\.\d+)?|\d+(?:\.\d+)?)\s*ms|\*)\s+"
        r"((?:<\d+(?:\.\d+)?|\d+(?:\.\d+)?)\s*ms|\*)\s*(.*)$",
        line,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None

    hop = int(match.group(1))
    latency_tokens = (match.group(2), match.group(3), match.group(4))
    descriptor = match.group(5).strip()
    probe_results = [
        _ProbeResult(success=False, timeout=True, rtt_ms=None)
        if token.strip() == "*"
        else _ProbeResult(success=True, timeout=False, rtt_ms=_parse_windows_latency_ms(token))
        for token in latency_tokens
    ]
    samples_ms = [probe_result.rtt_ms for probe_result in probe_results if probe_result.rtt_ms is not None]
    if descriptor.lower().startswith("request timed out"):
        descriptor = ""
    host, address = _parse_host_and_address(descriptor)
    reached_destination = _addresses_match(address or host, destination_address)
    return _CycleHopMeasurement(
        hop=hop,
        host=host,
        address=address,
        sent=len(latency_tokens),
        samples_ms=samples_ms,
        probe_results=probe_results,
        reached_destination=reached_destination,
    )


def _parse_traceroute_line(
    line: str,
    *,
    destination_address: str,
    resolve_hostnames: bool,
) -> _CycleHopMeasurement | None:
    del resolve_hostnames
    match = re.match(r"^\s*(\d+)\s+(.*)$", line)
    if match is None:
        return None

    hop = int(match.group(1))
    remainder = match.group(2).strip()
    if not remainder:
        return None
    token_pattern = re.compile(r"\*|<?\d+(?:\.\d+)?\s*ms\b", flags=re.IGNORECASE)
    token_matches = list(token_pattern.finditer(remainder))
    if not token_matches:
        return None

    descriptor = remainder[: token_matches[0].start()].strip()
    descriptor = descriptor.rstrip("!").strip()
    host, address = _parse_host_and_address(descriptor)
    probe_results = [
        _ProbeResult(success=False, timeout=True, rtt_ms=None)
        if token_match.group(0).strip() == "*"
        else _ProbeResult(success=True, timeout=False, rtt_ms=_parse_windows_latency_ms(token_match.group(0)))
        for token_match in token_matches
    ]
    samples_ms = [probe_result.rtt_ms for probe_result in probe_results if probe_result.rtt_ms is not None]
    reached_destination = _addresses_match(address or host, destination_address)
    return _CycleHopMeasurement(
        hop=hop,
        host=host,
        address=address,
        sent=len(probe_results),
        samples_ms=samples_ms,
        probe_results=probe_results,
        reached_destination=reached_destination,
    )


def _parse_tracepath_line(
    line: str,
    *,
    destination_address: str,
    resolve_hostnames: bool,
) -> _CycleHopMeasurement | None:
    del resolve_hostnames
    match = re.match(r"^\s*(\d+)\??:\s+(.*)$", line)
    if match is None:
        return None

    hop = int(match.group(1))
    remainder = match.group(2).strip()
    if not remainder or remainder.lower().startswith("resume:"):
        return None
    if "pmtu" in remainder.lower() and "ms" not in remainder.lower():
        return None
    if remainder.lower().startswith("no reply"):
        return _CycleHopMeasurement(
            hop=hop,
            host="",
            address="",
            sent=1,
            samples_ms=[],
            probe_results=[_ProbeResult(success=False, timeout=True, rtt_ms=None)],
            reached_destination=False,
        )

    latency_match = re.search(r"(<?\d+(?:\.\d+)?)\s*ms\b", remainder, flags=re.IGNORECASE)
    if latency_match is None:
        return None

    descriptor = remainder[: latency_match.start()].strip()
    descriptor = descriptor.strip("[]").strip()
    sample_ms = _parse_windows_latency_ms(latency_match.group(1))
    host, address = _parse_host_and_address(descriptor)
    reached_destination = "reached" in remainder.lower() or _addresses_match(address or host, destination_address)
    return _CycleHopMeasurement(
        hop=hop,
        host=host,
        address=address,
        sent=1,
        samples_ms=[sample_ms],
        probe_results=[_ProbeResult(success=True, timeout=False, rtt_ms=sample_ms)],
        reached_destination=reached_destination,
    )


def _resolve_target(request: MTRTraceRequest) -> _ResolvedTarget:
    family = socket.AF_INET6 if request.ipv6 else socket.AF_INET
    try:
        info = socket.getaddrinfo(
            request.target,
            0,
            family,
            socket.SOCK_DGRAM,
            0,
            socket.AI_ADDRCONFIG,
        )
    except socket.gaierror as exc:
        raise ValueError(f"Unable to resolve target: {request.target}") from exc
    if not info:
        raise ValueError(f"Unable to resolve target: {request.target}")
    entry = info[0]
    resolved_family = int(entry[0])
    sockaddr = entry[4]
    address = sockaddr[0]
    return _ResolvedTarget(
        family=resolved_family,
        address=address,
        sockaddr=_icmp_sockaddr(address, request.ipv6),
        source_address=_determine_source_address(resolved_family, address),
    )


def _open_receive_socket(family: int) -> socket.socket:
    protocol = socket.IPPROTO_ICMPV6 if family == socket.AF_INET6 else socket.IPPROTO_ICMP
    sock = socket.socket(family, socket.SOCK_RAW, protocol)
    sock.setblocking(False)
    return sock


def _open_send_socket(family: int, protocol: str) -> socket.socket:
    if protocol == MTR_PROTOCOL_UDP:
        return socket.socket(family, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    raw_protocol = socket.IPPROTO_ICMPV6 if family == socket.AF_INET6 else socket.IPPROTO_ICMP
    return socket.socket(family, socket.SOCK_RAW, raw_protocol)


def _configure_hop_limit(sock: socket.socket, family: int, hop: int) -> None:
    if family == socket.AF_INET6:
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_UNICAST_HOPS, hop)
    else:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_TTL, hop)


def _build_icmp_packet(
    *,
    family: int,
    source_address: str,
    destination_address: str,
    identifier: int,
    sequence: int,
    hop: int,
) -> bytes:
    payload = _ICMP_PAYLOAD_PREFIX + struct.pack("!HH", hop, sequence)
    if family == socket.AF_INET6:
        packet = struct.pack("!BBHHH", _ICMPV6_ECHO_REQUEST, 0, 0, identifier, sequence) + payload
        checksum = _icmpv6_checksum(source_address, destination_address, packet)
        return struct.pack("!BBHHH", _ICMPV6_ECHO_REQUEST, 0, checksum, identifier, sequence) + payload
    packet = struct.pack("!BBHHH", _ICMP_ECHO_REQUEST, 0, 0, identifier, sequence) + payload
    checksum = _internet_checksum(packet)
    return struct.pack("!BBHHH", _ICMP_ECHO_REQUEST, 0, checksum, identifier, sequence) + payload


def _build_udp_payload(dest_port: int, hop: int) -> bytes:
    return _ICMP_PAYLOAD_PREFIX + struct.pack("!HH", dest_port & 0xFFFF, hop & 0xFFFF)


def _match_probe_reply(
    *,
    payload: bytes,
    address: tuple[object, ...],
    family: int,
    token: _ProbeToken,
) -> _ParsedICMPReply | None:
    reply = _parse_probe_reply(payload, address, family)
    if reply is None:
        return None
    if token.protocol != reply.inner_protocol:
        return None
    if token.protocol == MTR_PROTOCOL_ICMP:
        if reply.identifier != token.identifier or reply.sequence != token.sequence:
            return None
    else:
        if reply.dest_port != token.dest_port:
            return None
    return reply


def _parse_probe_reply(payload: bytes, address: tuple[object, ...], family: int) -> _ParsedICMPReply | None:
    if family == socket.AF_INET6:
        return _parse_ipv6_icmp_reply(payload, address)
    return _parse_ipv4_icmp_reply(payload, address)


def _parse_ipv4_icmp_reply(payload: bytes, address: tuple[object, ...]) -> _ParsedICMPReply | None:
    if len(payload) < 8:
        return None
    offset = 0
    responder_address = str(address[0])
    if (payload[0] >> 4) == 4 and len(payload) >= 20:
        header_length = (payload[0] & 0x0F) * 4
        if header_length < 20 or len(payload) < header_length + 8:
            return None
        responder_address = socket.inet_ntoa(payload[12:16])
        offset = header_length

    icmp_type = payload[offset]
    icmp_code = payload[offset + 1]
    if icmp_type == _ICMP_ECHO_REPLY:
        if len(payload) < offset + 8:
            return None
        identifier, sequence = struct.unpack("!HH", payload[offset + 4:offset + 8])
        return _ParsedICMPReply(
            responder_address=responder_address,
            icmp_type=icmp_type,
            icmp_code=icmp_code,
            inner_protocol=MTR_PROTOCOL_ICMP,
            identifier=identifier,
            sequence=sequence,
            dest_port=0,
            reached_destination=True,
        )
    if icmp_type not in {_ICMP_TIME_EXCEEDED, _ICMP_DEST_UNREACHABLE}:
        return None
    inner = payload[offset + 8:]
    if len(inner) < 20 or (inner[0] >> 4) != 4:
        return None
    inner_header_length = (inner[0] & 0x0F) * 4
    if inner_header_length < 20 or len(inner) < inner_header_length + 8:
        return None
    protocol_number = inner[9]
    transport = inner[inner_header_length:]
    if protocol_number == socket.IPPROTO_ICMP and len(transport) >= 8:
        identifier, sequence = struct.unpack("!HH", transport[4:8])
        return _ParsedICMPReply(
            responder_address=responder_address,
            icmp_type=icmp_type,
            icmp_code=icmp_code,
            inner_protocol=MTR_PROTOCOL_ICMP,
            identifier=identifier,
            sequence=sequence,
            dest_port=0,
            reached_destination=False,
        )
    if protocol_number == socket.IPPROTO_UDP and len(transport) >= 8:
        _src_port, dest_port = struct.unpack("!HH", transport[:4])
        return _ParsedICMPReply(
            responder_address=responder_address,
            icmp_type=icmp_type,
            icmp_code=icmp_code,
            inner_protocol=MTR_PROTOCOL_UDP,
            identifier=0,
            sequence=0,
            dest_port=dest_port,
            reached_destination=icmp_type == _ICMP_DEST_UNREACHABLE and icmp_code == _ICMP_PORT_UNREACHABLE_CODE,
        )
    return None


def _parse_ipv6_icmp_reply(payload: bytes, address: tuple[object, ...]) -> _ParsedICMPReply | None:
    if len(payload) < 8:
        return None
    offset = 0
    responder_address = str(address[0])
    if (payload[0] >> 4) == 6 and len(payload) >= 48:
        responder_address = socket.inet_ntop(socket.AF_INET6, payload[8:24])
        offset = 40

    if len(payload) < offset + 8:
        return None
    icmp_type = payload[offset]
    icmp_code = payload[offset + 1]
    if icmp_type == _ICMPV6_ECHO_REPLY:
        identifier, sequence = struct.unpack("!HH", payload[offset + 4:offset + 8])
        return _ParsedICMPReply(
            responder_address=responder_address,
            icmp_type=icmp_type,
            icmp_code=icmp_code,
            inner_protocol=MTR_PROTOCOL_ICMP,
            identifier=identifier,
            sequence=sequence,
            dest_port=0,
            reached_destination=True,
        )
    if icmp_type not in {_ICMPV6_TIME_EXCEEDED, _ICMPV6_DEST_UNREACHABLE}:
        return None
    inner = payload[offset + 8:]
    if len(inner) < 40 or (inner[0] >> 4) != 6:
        return None
    next_header = inner[6]
    transport = inner[40:]
    if next_header == socket.IPPROTO_ICMPV6 and len(transport) >= 8:
        identifier, sequence = struct.unpack("!HH", transport[4:8])
        return _ParsedICMPReply(
            responder_address=responder_address,
            icmp_type=icmp_type,
            icmp_code=icmp_code,
            inner_protocol=MTR_PROTOCOL_ICMP,
            identifier=identifier,
            sequence=sequence,
            dest_port=0,
            reached_destination=False,
        )
    if next_header == socket.IPPROTO_UDP and len(transport) >= 8:
        _src_port, dest_port = struct.unpack("!HH", transport[:4])
        return _ParsedICMPReply(
            responder_address=responder_address,
            icmp_type=icmp_type,
            icmp_code=icmp_code,
            inner_protocol=MTR_PROTOCOL_UDP,
            identifier=0,
            sequence=0,
            dest_port=dest_port,
            reached_destination=icmp_type == _ICMPV6_DEST_UNREACHABLE and icmp_code == _ICMPV6_PORT_UNREACHABLE_CODE,
        )
    return None


def _resolve_host_name(address: str, cache: dict[str, str], resolve_hostnames: bool) -> str:
    if not resolve_hostnames:
        return address
    cached = cache.get(address)
    if cached is not None:
        return cached
    try:
        if ":" in address:
            host = socket.getnameinfo((address, 0, 0, 0), 0)[0]
        else:
            host = socket.getnameinfo((address, 0), 0)[0]
    except Exception:
        host = address
    cache[address] = host
    return host


def _wait_for_interval(stop_path: Path, parent_pid: int, interval_ms: int) -> bool:
    deadline = time.monotonic() + (interval_ms / 1000.0)
    while time.monotonic() < deadline:
        if _should_stop(stop_path, parent_pid):
            return True
        time.sleep(_HELPER_POLL_INTERVAL_SECONDS)
    return _should_stop(stop_path, parent_pid)


def _should_stop(stop_path: Path, parent_pid: int) -> bool:
    if stop_path.exists():
        return True
    if parent_pid > 0 and not _pid_exists(parent_pid):
        return True
    return False


def _launch_mtr_helper_plain(session_dir: str | Path) -> list[str]:
    command = mtr_helper_command(session_dir)
    subprocess.Popen(  # noqa: S603
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    return command


def _windows_tracert_executable(*, platform_name: str | None = None) -> str:
    return _windows_system_executable(
        relative_paths=("System32/tracert.exe", "Sysnative/tracert.exe"),
        fallback_names=("tracert.exe", "tracert"),
        platform_name=platform_name,
    )


def _windows_powershell_executable(*, platform_name: str | None = None) -> str:
    return _windows_system_executable(
        relative_paths=(
            "System32/WindowsPowerShell/v1.0/powershell.exe",
            "Sysnative/WindowsPowerShell/v1.0/powershell.exe",
        ),
        fallback_names=("powershell.exe", "powershell"),
        platform_name=platform_name,
    )


def _windows_system_executable(
    *,
    relative_paths: tuple[str, ...],
    fallback_names: tuple[str, ...],
    platform_name: str | None = None,
) -> str:
    if _platform_name(platform_name) != "windows":
        return fallback_names[0]

    system_root_raw = (os.environ.get("SystemRoot") or os.environ.get("WINDIR") or r"C:\Windows").strip()
    system_root = Path(system_root_raw or r"C:\Windows")
    for relative_path in relative_paths:
        candidate = system_root / relative_path
        if candidate.exists():
            return str(candidate)
    for fallback_name in fallback_names:
        resolved = shutil.which(fallback_name)
        if resolved:
            return resolved
    return fallback_names[0]


def _windows_process_creationflags(*, new_process_group: bool = False, no_window: bool = False) -> int:
    flags = 0
    if new_process_group:
        flags |= int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    if no_window:
        flags |= int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return flags


def _wrap_command_for_live_output(command: list[str], *, platform_name: str | None = None) -> list[str]:
    if _platform_name(platform_name) == "linux" and shutil.which("stdbuf") is not None:
        return ["stdbuf", "-oL", "-eL", *command]
    return command


def _platform_name(platform_name: str | None = None) -> str:
    value = platform_name if platform_name is not None else platform.system()
    return str(value).strip().lower()


def _optional_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _format_optional_ms(value: float | None) -> str:
    if value is None:
        return ""
    return _format_ms(value)


def _format_ms(value: float) -> str:
    return f"{value:.1f}"


def _trace_cycle_timeout_seconds(request: MTRTraceRequest, *, parser_kind: str) -> float:
    probes_per_hop = 3 if parser_kind == "windows" else 1
    expected = (request.max_hops * request.timeout_ms * probes_per_hop) / 1000.0
    return max(5.0, expected + 5.0)


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temp_path.replace(path)


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, separators=(",", ":")))
        handle.write("\n")


def _read_json_dict(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def _read_jsonl_line(line: str) -> dict[str, Any] | None:
    candidate = line.strip()
    if not candidate:
        return None
    try:
        raw = json.loads(candidate)
    except Exception:
        return None
    return raw if isinstance(raw, dict) else None


def _normalize_pid(value: Any) -> int | None:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None
    return normalized if normalized > 0 else None


def _pid_exists(pid: int, *, platform_name: str | None = None) -> bool:
    if pid <= 0:
        return False
    if _platform_name(platform_name) == "windows":
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


def _parse_windows_latency_ms(token: str) -> float:
    cleaned = token.strip().lower()
    if cleaned.startswith("<"):
        digits = "".join(ch for ch in cleaned if ch.isdigit())
        return float(digits or "1")
    digits = "".join(ch for ch in cleaned if ch.isdigit() or ch == ".")
    return float(digits or "0")


def _parse_host_and_address(descriptor: str) -> tuple[str, str]:
    value = descriptor.strip()
    if not value:
        return "", ""

    bracket_match = re.search(r"^(.*?)\s*\[([^\]]+)\]\s*$", value)
    if bracket_match is not None:
        host = bracket_match.group(1).strip()
        address = bracket_match.group(2).strip()
        return host or address, address

    paren_match = re.search(r"^(.*?)\s*\(([^)]+)\)\s*$", value)
    if paren_match is not None:
        host = paren_match.group(1).strip()
        address = paren_match.group(2).strip()
        return host or address, address

    token = value.split()[0]
    try:
        socket.inet_pton(socket.AF_INET, token)
        return token, token
    except OSError:
        pass
    try:
        socket.inet_pton(socket.AF_INET6, token)
        return token, token
    except OSError:
        pass
    return value, ""


def _addresses_match(left: str, right: str) -> bool:
    if not left or not right:
        return False
    try:
        return socket.getaddrinfo(left, 0)[0][4][0] == socket.getaddrinfo(right, 0)[0][4][0]
    except Exception:
        return left.strip().lower() == right.strip().lower()


def _line_mentions_destination(line: str, destination_address: str) -> bool:
    candidate_line = line.strip()
    target = destination_address.strip()
    if not candidate_line or not target:
        return False
    try:
        target_ip = ipaddress.ip_address(target)
    except ValueError:
        return target.lower() in candidate_line.lower()

    for raw_token in re.findall(r"[0-9A-Fa-f:.]+", candidate_line):
        token = raw_token.strip("[](),")
        if not token or "." not in token and ":" not in token:
            continue
        try:
            if ipaddress.ip_address(token) == target_ip:
                return True
        except ValueError:
            continue
    return False


def _determine_source_address(family: int, destination_address: str) -> str:
    sock = socket.socket(family, socket.SOCK_DGRAM)
    try:
        if family == socket.AF_INET6:
            sock.connect((destination_address, 1, 0, 0))
        else:
            sock.connect((destination_address, 1))
        return str(sock.getsockname()[0])
    finally:
        sock.close()


def _icmp_sockaddr(address: str, ipv6: bool) -> tuple[object, ...]:
    if ipv6:
        return (address, 0, 0, 0)
    return (address, 0)


def _udp_sockaddr(address: str, port: int, ipv6: bool) -> tuple[object, ...]:
    if ipv6:
        return (address, port, 0, 0)
    return (address, port)


def _internet_checksum(payload: bytes) -> int:
    if len(payload) % 2 == 1:
        payload += b"\x00"
    total = 0
    for index in range(0, len(payload), 2):
        total += (payload[index] << 8) + payload[index + 1]
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return (~total) & 0xFFFF


def _icmpv6_checksum(source_address: str, destination_address: str, payload: bytes) -> int:
    pseudo_header = (
        socket.inet_pton(socket.AF_INET6, source_address)
        + socket.inet_pton(socket.AF_INET6, destination_address)
        + struct.pack("!I3xB", len(payload), socket.IPPROTO_ICMPV6)
    )
    return _internet_checksum(pseudo_header + payload)
