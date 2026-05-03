from __future__ import annotations

import ctypes
from dataclasses import dataclass, field, replace
import errno
import ipaddress
import json
import logging
import os
from pathlib import Path
import platform
import re
import shutil
import socket
import subprocess
import time
from ctypes import wintypes
from typing import Any, Callable

from snakesh.services.privilege_service import run_command


_WINDOWS_CPU_TEMPERATURE_CACHE_SECONDS = 10.0
_WINDOWS_CPU_SENSOR_TOKENS = ("cpu", "package", "core", "ccd", "tdie", "tctl")
_GPU_INVENTORY_CACHE_SECONDS = 30.0
_WINDOWS_GPU_COUNTER_CACHE_SECONDS = 5.0
_WINDOWS_GPU_SENSOR_CACHE_SECONDS = 10.0
_WINDOWS_GPU_INVENTORY_TIMEOUT_SECONDS = 1.5
_WINDOWS_GPU_COUNTER_TIMEOUT_SECONDS = 1.5
_WINDOWS_GPU_SENSOR_TIMEOUT_SECONDS = 1.5
_WINDOWS_CPU_TEMPERATURE_TIMEOUT_SECONDS = 1.5
_WINDOWS_DISK_MAPPING_TIMEOUT_SECONDS = 1.5
_WINDOWS_GPU_PROBE_FAILURE_LIMIT = 2
_WINDOWS_GPU_PROBE_BACKOFF_SECONDS = 30.0
_FAST_OVERVIEW_WARNING_SECONDS = 1.0
_SLOW_DETAIL_WARNING_SECONDS = 3.0
_PROCESS_REFRESH_WARNING_SECONDS = 3.0


_LOGGER = logging.getLogger(__name__)


class ResourceMonitorCancelledError(RuntimeError):
    """Raised when a cooperative stop request interrupts resource collection."""


@dataclass(frozen=True, slots=True)
class FilesystemEntry:
    device: str
    mountpoint: str
    filesystem_type: str
    used_bytes: int
    total_bytes: int
    free_bytes: int
    usage_percent: float
    is_home: bool = False
    disk_device_key: str = ""


@dataclass(frozen=True, slots=True)
class DiskDeviceSample:
    key: str
    display_label: str
    read_bytes_per_sec: float
    write_bytes_per_sec: float
    read_bytes_since_open: int
    write_bytes_since_open: int


@dataclass(frozen=True, slots=True)
class InterfaceBandwidthEntry:
    name: str
    ipv4_address: str
    ipv6_address: str
    is_up: bool
    speed_mbps: int
    recv_bytes_per_sec: float
    sent_bytes_per_sec: float
    recv_bytes_total: int
    sent_bytes_total: int


@dataclass(frozen=True, slots=True)
class GpuAdapterSample:
    id: str
    vendor: str
    name: str
    adapter_index: int | None = None
    backend: str = ""
    utilization_percent: float | None = None
    memory_used_bytes: int | None = None
    memory_total_bytes: int | None = None
    temperature_c: float | None = None


@dataclass(frozen=True, slots=True)
class GpuSample:
    available: bool = False
    detected: bool = False
    name: str = ""
    gpu_count: int = 0
    utilization_percent: float | None = None
    memory_used_bytes: int | None = None
    memory_total_bytes: int | None = None
    memory_percent: float | None = None
    temperature_c: float | None = None
    has_utilization: bool = False
    has_memory: bool = False
    has_temperature: bool = False
    adapters: list[GpuAdapterSample] = field(default_factory=list)
    message: str = ""


@dataclass(frozen=True, slots=True)
class ResourceMonitorSample:
    timestamp_monotonic: float
    cpu_percent: float
    logical_cpu_count: int
    memory_used_bytes: int
    memory_total_bytes: int
    memory_percent: float
    swap_used_bytes: int
    swap_total_bytes: int
    swap_percent: float
    disk_mountpoint: str
    disk_used_bytes: int
    disk_total_bytes: int
    disk_free_bytes: int
    disk_percent: float
    disk_read_bytes_per_sec: float
    disk_write_bytes_per_sec: float
    disk_read_bytes_since_open: int
    disk_write_bytes_since_open: int
    network_recv_bytes_per_sec: float
    network_sent_bytes_per_sec: float
    network_recv_bytes_since_open: int
    network_sent_bytes_since_open: int
    process_count: int
    thread_count: int
    cpu_temperature_c: float | None = None
    cpu_per_core_percentages: tuple[float, ...] = ()


@dataclass(frozen=True, slots=True)
class ResourceMonitorSnapshot:
    sample: ResourceMonitorSample
    filesystems: list[FilesystemEntry] = field(default_factory=list)
    disk_devices: list[DiskDeviceSample] = field(default_factory=list)
    interfaces: list[InterfaceBandwidthEntry] = field(default_factory=list)
    gpu: GpuSample = field(default_factory=GpuSample)
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ProcessEntry:
    pid: int
    name: str
    cpu_percent: float
    memory_rss_bytes: int
    threads: int
    user: str
    status: str
    started_at: float | None
    command: str


@dataclass(frozen=True, slots=True)
class ProcessInventorySnapshot:
    entries: list[ProcessEntry] = field(default_factory=list)
    total_threads: int = 0
    collected_at: float = 0.0
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ProcessCountsSnapshot:
    process_count: int = 0
    thread_count: int = 0
    collected_at: float = 0.0
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ProcessActionResult:
    success: bool
    message: str
    pid: int
    action: str
    elevated: bool = False
    cancelled: bool = False
    requires_elevation: bool = False


StopCallback = Callable[[], bool]


@dataclass(frozen=True, slots=True)
class _InterfaceMetadata:
    name: str
    ipv4_address: str
    ipv6_address: str
    is_up: bool
    speed_mbps: int


def _stop_requested(stop_callback: StopCallback | None) -> bool:
    if stop_callback is None:
        return False
    try:
        return bool(stop_callback())
    except Exception:
        return False


def _raise_if_stop_requested(stop_callback: StopCallback | None, message: str) -> None:
    if _stop_requested(stop_callback):
        raise ResourceMonitorCancelledError(message)


class ResourceMonitorOverviewCollector:
    def __init__(self, *, home_path: str | Path | None = None, platform_name: str | None = None) -> None:
        self._home_path = Path(home_path).expanduser() if home_path is not None else Path.home()
        self._platform_name = _platform_name(platform_name)
        self._last_monotonic: float | None = None
        self._last_disk_totals: tuple[int, int] | None = None
        self._last_disk_device_totals: dict[str, tuple[int, int]] = {}
        self._last_network_totals: tuple[int, int] | None = None
        self._initial_disk_totals: tuple[int, int] | None = None
        self._initial_disk_device_totals: dict[str, tuple[int, int]] = {}
        self._initial_network_totals: tuple[int, int] | None = None
        self._last_interface_totals: dict[str, tuple[int, int]] = {}
        self._last_interface_monotonic: float | None = None
        self._cached_interface_metadata: dict[str, _InterfaceMetadata] = {}
        self._last_interface_metadata_refresh_monotonic: float | None = None
        self._windows_volume_to_disk_map: dict[str, str] = {}
        self._gpu_unavailable_message = ""
        self._gpu_disabled = self._platform_name not in {"linux", "windows", "darwin"}
        self._gpu_inventory_cache: list[_GpuAdapterState] | None = None
        self._gpu_inventory_checked_at: float | None = None
        self._cached_filesystems: list[FilesystemEntry] = []
        self._cached_filesystem_mountpoints: tuple[str, ...] = ()
        self._cached_interfaces: list[InterfaceBandwidthEntry] = []
        self._cached_gpu_sample = GpuSample(
            message=(
                "Collecting GPU telemetry..."
                if not self._gpu_disabled
                else "GPU telemetry is unavailable on this system."
            )
        )
        self._slow_detail_errors: list[str] = []
        self._windows_cpu_sampler = _create_windows_cpu_sampler(platform_name=self._platform_name)
        self._windows_cpu_temperature_checked_at: float | None = None
        self._windows_cpu_temperature_value: float | None = None
        self._windows_gpu_counter_sampler = _create_windows_gpu_counter_sampler(platform_name=self._platform_name)
        self._windows_gpu_counter_checked_at: float | None = None
        self._windows_gpu_counter_payload: object | None = None
        self._windows_gpu_counter_failure_count = 0
        self._windows_gpu_counter_backoff_until: float | None = None
        self._windows_gpu_counter_backoff_active = False
        self._windows_gpu_sensor_checked_at: float | None = None
        self._windows_gpu_sensor_payload: object | None = None
        self._windows_gpu_sensor_failure_count = 0
        self._windows_gpu_sensor_backoff_until: float | None = None
        self._windows_gpu_sensor_backoff_active = False
        self._linux_drm_fdinfo_engine_totals: dict[str, tuple[float, int]] = {}

        psutil = _psutil()
        try:
            psutil.cpu_percent(interval=None)
        except Exception:
            pass
        try:
            psutil.cpu_percent(interval=None, percpu=True)
        except Exception:
            pass

    def __del__(self) -> None:
        for attribute_name in ("_windows_cpu_sampler", "_windows_gpu_counter_sampler"):
            sampler = getattr(self, attribute_name, None)
            if sampler is None:
                continue
            try:
                sampler.close()
            except Exception:
                continue

    @property
    def platform_name(self) -> str:
        return self._platform_name

    def collect(self, *, process_count: int = 0, thread_count: int = 0) -> ResourceMonitorSnapshot:
        now = time.monotonic()
        self.refresh_slow_details(now=now)
        return self.collect_fast(process_count=process_count, thread_count=thread_count, now=now)

    def collect_fast(
        self,
        *,
        process_count: int = 0,
        thread_count: int = 0,
        now: float | None = None,
    ) -> ResourceMonitorSnapshot:
        psutil = _psutil()
        now = time.monotonic() if now is None else now
        errors: list[str] = []

        cpu_percentages, cpu_percent = self._collect_cpu_metrics(psutil)
        logical_cpu_count = len(cpu_percentages) or int(os.cpu_count() or 0)
        cpu_temperature_c = (
            self._windows_cpu_temperature_value
            if self._platform_name == "windows"
            else self._collect_psutil_cpu_temperature(psutil)
        )

        try:
            virtual_memory = psutil.virtual_memory()
            memory_used_bytes = int(getattr(virtual_memory, "used", 0) or 0)
            memory_total_bytes = int(getattr(virtual_memory, "total", 0) or 0)
            memory_percent = _safe_float(getattr(virtual_memory, "percent", 0.0))
        except Exception as exc:  # noqa: BLE001
            memory_used_bytes = 0
            memory_total_bytes = 0
            memory_percent = 0.0
            errors.append(f"Memory: {exc}")

        try:
            swap = psutil.swap_memory()
            swap_used_bytes = int(getattr(swap, "used", 0) or 0)
            swap_total_bytes = int(getattr(swap, "total", 0) or 0)
            swap_percent = _safe_float(getattr(swap, "percent", 0.0))
        except Exception as exc:  # noqa: BLE001
            swap_used_bytes = 0
            swap_total_bytes = 0
            swap_percent = 0.0
            errors.append(f"Swap: {exc}")

        filesystems = list(self._cached_filesystems)
        home_filesystem = next((entry for entry in filesystems if entry.is_home), None)
        if home_filesystem is None and filesystems:
            home_filesystem = filesystems[0]

        try:
            disk_totals = _disk_totals()
            disk_read_rate, disk_write_rate, disk_read_since_open, disk_write_since_open = self._rate_and_totals(
                current=disk_totals,
                last=self._last_disk_totals,
                initial=self._initial_disk_totals,
                now=now,
            )
        except Exception as exc:  # noqa: BLE001
            disk_totals = (0, 0)
            disk_read_rate = 0.0
            disk_write_rate = 0.0
            disk_read_since_open = 0
            disk_write_since_open = 0
            errors.append(f"Disk I/O: {exc}")
            disk_devices = []
        else:
            try:
                disk_devices = self._collect_disk_devices(now=now, filesystems=filesystems)
            except Exception as exc:  # noqa: BLE001
                disk_devices = []
                errors.append(f"Disk devices: {exc}")

        try:
            network_totals = _network_totals()
            network_recv_rate, network_send_rate, network_recv_since_open, network_send_since_open = self._rate_and_totals(
                current=network_totals,
                last=self._last_network_totals,
                initial=self._initial_network_totals,
                now=now,
            )
        except Exception as exc:  # noqa: BLE001
            network_totals = (0, 0)
            network_recv_rate = 0.0
            network_send_rate = 0.0
            network_recv_since_open = 0
            network_send_since_open = 0
            errors.append(f"Network: {exc}")

        interfaces = list(self._cached_interfaces)
        try:
            interfaces = self._collect_interface_rates(now=now)
        except Exception:
            interfaces = list(self._cached_interfaces)
        if interfaces:
            self._cached_interfaces = list(interfaces)
            network_recv_rate = sum(entry.recv_bytes_per_sec for entry in interfaces)
            network_send_rate = sum(entry.sent_bytes_per_sec for entry in interfaces)
        gpu = self._cached_gpu_sample
        errors.extend(self._slow_detail_errors)

        sample = ResourceMonitorSample(
            timestamp_monotonic=now,
            cpu_percent=cpu_percent,
            logical_cpu_count=logical_cpu_count,
            memory_used_bytes=memory_used_bytes,
            memory_total_bytes=memory_total_bytes,
            memory_percent=memory_percent,
            swap_used_bytes=swap_used_bytes,
            swap_total_bytes=swap_total_bytes,
            swap_percent=swap_percent,
            disk_mountpoint=home_filesystem.mountpoint if home_filesystem else "",
            disk_used_bytes=home_filesystem.used_bytes if home_filesystem else 0,
            disk_total_bytes=home_filesystem.total_bytes if home_filesystem else 0,
            disk_free_bytes=home_filesystem.free_bytes if home_filesystem else 0,
            disk_percent=home_filesystem.usage_percent if home_filesystem else 0.0,
            disk_read_bytes_per_sec=disk_read_rate,
            disk_write_bytes_per_sec=disk_write_rate,
            disk_read_bytes_since_open=disk_read_since_open,
            disk_write_bytes_since_open=disk_write_since_open,
            network_recv_bytes_per_sec=network_recv_rate,
            network_sent_bytes_per_sec=network_send_rate,
            network_recv_bytes_since_open=network_recv_since_open,
            network_sent_bytes_since_open=network_send_since_open,
            process_count=max(0, int(process_count)),
            thread_count=max(0, int(thread_count)),
            cpu_temperature_c=cpu_temperature_c,
            cpu_per_core_percentages=cpu_percentages,
        )

        self._last_monotonic = now
        self._last_disk_totals = disk_totals
        self._last_network_totals = network_totals
        if self._initial_disk_totals is None:
            self._initial_disk_totals = disk_totals
        if self._initial_network_totals is None:
            self._initial_network_totals = network_totals

        return ResourceMonitorSnapshot(
            sample=sample,
            filesystems=filesystems,
            disk_devices=disk_devices,
            interfaces=interfaces,
            gpu=gpu,
            errors=errors,
        )

    def refresh_slow_details(
        self,
        *,
        now: float | None = None,
        stop_callback: StopCallback | None = None,
    ) -> None:
        now = time.monotonic() if now is None else now
        errors: list[str] = []

        _raise_if_stop_requested(stop_callback, "Resource Monitor slow detail refresh cancelled.")
        try:
            started_at = time.monotonic()
            filesystems = collect_filesystem_entries(self._home_path)
            mountpoints = tuple(entry.mountpoint for entry in filesystems)
            mounts_changed = mountpoints != self._cached_filesystem_mountpoints
            if self._platform_name == "windows" and (mounts_changed or not self._windows_volume_to_disk_map):
                self._windows_volume_to_disk_map = _collect_windows_volume_to_disk_map(
                    platform_name=self._platform_name
                )
            filesystems = self._resolve_filesystem_entries(filesystems)
            self._cached_filesystems = filesystems
            self._cached_filesystem_mountpoints = mountpoints
            _LOGGER.debug(
                "Resource Monitor filesystem collection completed duration=%.3fs rows=%s mounts_changed=%s",
                time.monotonic() - started_at,
                len(filesystems),
                mounts_changed,
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Filesystems: {exc}")

        _raise_if_stop_requested(stop_callback, "Resource Monitor slow detail refresh cancelled.")
        try:
            self._cached_interfaces = self._collect_interfaces(now=now)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Interfaces: {exc}")

        _raise_if_stop_requested(stop_callback, "Resource Monitor slow detail refresh cancelled.")
        if self._platform_name == "windows":
            temperature_warning = self._refresh_windows_cpu_temperature_cache(now=now)
            if temperature_warning:
                errors.append(temperature_warning)
        else:
            try:
                self._windows_cpu_temperature_value = self._collect_psutil_cpu_temperature(_psutil())
            except Exception:
                self._windows_cpu_temperature_value = None

        _raise_if_stop_requested(stop_callback, "Resource Monitor slow detail refresh cancelled.")
        try:
            gpu = self._collect_gpu()
            gpu = self._reuse_previous_linux_intel_gpu_metrics(gpu)
            if self._should_keep_previous_gpu_sample(gpu):
                if self._cached_gpu_sample.detected:
                    errors.append("GPU telemetry timed out; showing last successful sample.")
            else:
                self._cached_gpu_sample = gpu
        except Exception as exc:  # noqa: BLE001
            if self._cached_gpu_sample.detected:
                errors.append("GPU telemetry timed out; showing last successful sample.")
            else:
                self._cached_gpu_sample = GpuSample(message=f"GPU telemetry failed: {exc}")
                errors.append(f"GPU: {exc}")

        _raise_if_stop_requested(stop_callback, "Resource Monitor slow detail refresh cancelled.")
        self._slow_detail_errors = errors

    def _resolve_filesystem_entries(self, entries: list[FilesystemEntry]) -> list[FilesystemEntry]:
        resolved_entries: list[FilesystemEntry] = []
        windows_volume_to_disk_map = self._windows_volume_to_disk_map if self._platform_name == "windows" else None
        for entry in entries:
            disk_device_key = _filesystem_disk_device_key(
                entry,
                platform_name=self._platform_name,
                windows_volume_to_disk_map=windows_volume_to_disk_map,
            )
            if entry.disk_device_key == disk_device_key:
                resolved_entries.append(entry)
                continue
            resolved_entries.append(replace(entry, disk_device_key=disk_device_key))
        return resolved_entries

    def _collect_disk_devices(
        self,
        *,
        now: float,
        filesystems: list[FilesystemEntry],
    ) -> list[DiskDeviceSample]:
        current_disk_totals = _disk_totals_per_device(platform_name=self._platform_name)
        mapped_keys = {entry.disk_device_key for entry in filesystems if entry.disk_device_key}
        if not mapped_keys:
            self._last_disk_device_totals = {}
            self._initial_disk_device_totals = {}
            return []

        last_monotonic = self._last_monotonic
        elapsed = (now - last_monotonic) if last_monotonic is not None else 0.0
        if elapsed <= 0:
            elapsed = 0.0

        disk_devices: list[DiskDeviceSample] = []
        next_last_totals: dict[str, tuple[int, int]] = {}
        home_filesystem = next((entry for entry in filesystems if entry.is_home), None)
        home_device_key = home_filesystem.disk_device_key if home_filesystem is not None else ""
        filesystem_order_by_device_key: dict[str, int] = {}
        for index, entry in enumerate(filesystems):
            if entry.disk_device_key and entry.disk_device_key not in filesystem_order_by_device_key:
                filesystem_order_by_device_key[entry.disk_device_key] = index

        for key, (display_label, totals) in current_disk_totals.items():
            if key not in mapped_keys:
                continue
            last_totals = self._last_disk_device_totals.get(key)
            initial_totals = self._initial_disk_device_totals.get(key)
            if initial_totals is None:
                initial_totals = totals
                self._initial_disk_device_totals[key] = totals
            read_rate, write_rate, read_since_open, write_since_open = self._rate_and_totals(
                current=totals,
                last=last_totals,
                initial=initial_totals,
                now=now,
            )
            next_last_totals[key] = totals
            if last_totals is None and elapsed <= 0:
                read_rate = 0.0
                write_rate = 0.0
            disk_devices.append(
                DiskDeviceSample(
                    key=key,
                    display_label=display_label,
                    read_bytes_per_sec=read_rate,
                    write_bytes_per_sec=write_rate,
                    read_bytes_since_open=read_since_open,
                    write_bytes_since_open=write_since_open,
                )
            )

        self._last_disk_device_totals = next_last_totals
        self._initial_disk_device_totals = {
            key: self._initial_disk_device_totals[key]
            for key in next_last_totals
            if key in self._initial_disk_device_totals
        }
        disk_devices.sort(
            key=lambda sample: (
                0 if sample.key == home_device_key else 1,
                filesystem_order_by_device_key.get(sample.key, 10_000),
                sample.display_label.casefold(),
            )
        )
        return disk_devices

    def _collect_cpu_metrics(self, psutil) -> tuple[tuple[float, ...], float]:  # noqa: ANN001
        if self._platform_name == "windows" and self._windows_cpu_sampler is not None:
            started_at = time.monotonic()
            try:
                overall, per_core = self._windows_cpu_sampler.collect()
                values = tuple(max(0.0, _safe_float(value)) for value in per_core)
                if values:
                    duration = time.monotonic() - started_at
                    _LOGGER.debug(
                        "Windows CPU sampler collected duration=%.3fs overall=%.1f logical_cores=%s",
                        duration,
                        max(0.0, _safe_float(overall)),
                        len(values),
                    )
                    return values, max(0.0, _safe_float(overall))
            except Exception:
                duration = time.monotonic() - started_at
                _LOGGER.warning(
                    "Windows CPU sampler failed after %.3fs; falling back to psutil CPU telemetry.",
                    duration,
                    exc_info=True,
                )

        values = self._collect_psutil_cpu_percentages(psutil)
        overall = (
            sum(values) / len(values)
            if values
            else _safe_float(_call_or_default(lambda: psutil.cpu_percent(interval=None), 0.0))
        )
        return values, overall

    @staticmethod
    def _collect_psutil_cpu_percentages(psutil) -> tuple[float, ...]:  # noqa: ANN001
        try:
            raw_values = psutil.cpu_percent(interval=None, percpu=True)
        except TypeError:
            raw_values = []
        except Exception:
            raw_values = []
        if isinstance(raw_values, list | tuple):
            values = tuple(max(0.0, _safe_float(value)) for value in raw_values)
            if values:
                return values
        overall = _safe_float(_call_or_default(lambda: psutil.cpu_percent(interval=None), 0.0))
        logical_cpu_count = int(os.cpu_count() or 0)
        if logical_cpu_count > 0:
            return tuple(overall for _ in range(logical_cpu_count))
        return ()

    def _collect_cpu_temperature(self, psutil, *, now: float) -> float | None:  # noqa: ANN001
        if self._platform_name == "windows":
            return self._collect_windows_cpu_temperature(now=now)
        return self._collect_psutil_cpu_temperature(psutil)

    def _collect_windows_cpu_temperature(self, *, now: float) -> float | None:
        checked_at = self._windows_cpu_temperature_checked_at
        if checked_at is not None and (now - checked_at) < _WINDOWS_CPU_TEMPERATURE_CACHE_SECONDS:
            return self._windows_cpu_temperature_value

        self._windows_cpu_temperature_checked_at = now
        previous_value = self._windows_cpu_temperature_value
        try:
            value = _probe_windows_cpu_temperature(platform_name=self._platform_name)
        except Exception:
            value = previous_value
        if value is None and previous_value is not None:
            value = previous_value
        self._windows_cpu_temperature_value = value
        return value

    def _refresh_windows_cpu_temperature_cache(self, *, now: float) -> str:
        previous_value = self._windows_cpu_temperature_value
        value = self._collect_windows_cpu_temperature(now=now)
        if value is None and previous_value is not None:
            self._windows_cpu_temperature_value = previous_value
            return "CPU temperature telemetry timed out; showing last successful sample."
        return ""

    def _should_keep_previous_gpu_sample(self, gpu: GpuSample) -> bool:
        if not self._cached_gpu_sample.detected:
            return False
        if gpu.detected and (gpu.has_utilization or gpu.has_memory or gpu.has_temperature):
            return False
        return not gpu.detected or not (gpu.has_utilization or gpu.has_memory or gpu.has_temperature)

    def _reuse_previous_linux_intel_gpu_metrics(self, gpu: GpuSample) -> GpuSample:
        if self._platform_name != "linux" or not self._cached_gpu_sample.detected or not gpu.detected:
            return gpu
        previous_intel_adapters = {
            adapter.id: adapter
            for adapter in self._cached_gpu_sample.adapters
            if adapter.vendor == "Intel"
        }
        if not previous_intel_adapters:
            return gpu

        changed = False
        states: list[_GpuAdapterState] = []
        for adapter in gpu.adapters:
            utilization_percent = adapter.utilization_percent
            memory_used_bytes = adapter.memory_used_bytes
            memory_total_bytes = adapter.memory_total_bytes
            temperature_c = adapter.temperature_c
            backend = adapter.backend
            previous = previous_intel_adapters.get(adapter.id) if adapter.vendor == "Intel" else None
            if previous is not None:
                if utilization_percent is None and previous.utilization_percent is not None:
                    utilization_percent = previous.utilization_percent
                    changed = True
                if memory_used_bytes is None and previous.memory_used_bytes is not None:
                    memory_used_bytes = previous.memory_used_bytes
                    changed = True
                if memory_total_bytes is None and previous.memory_total_bytes is not None:
                    memory_total_bytes = previous.memory_total_bytes
                    changed = True
                if temperature_c is None and previous.temperature_c is not None:
                    temperature_c = previous.temperature_c
                    changed = True
                if not backend and previous.backend:
                    backend = previous.backend
                    changed = True
            states.append(
                _GpuAdapterState(
                    id=adapter.id,
                    vendor=adapter.vendor,
                    name=adapter.name,
                    adapter_index=adapter.adapter_index,
                    backend=backend,
                    utilization_percent=utilization_percent,
                    memory_used_bytes=memory_used_bytes,
                    memory_total_bytes=memory_total_bytes,
                    temperature_c=temperature_c,
                )
            )
        if not changed:
            return gpu
        return _build_gpu_sample(states)

    @staticmethod
    def _collect_psutil_cpu_temperature(psutil) -> float | None:  # noqa: ANN001
        sensors_temperatures = getattr(psutil, "sensors_temperatures", None)
        if not callable(sensors_temperatures):
            return None
        try:
            sensors = sensors_temperatures(fahrenheit=False)
        except TypeError:
            sensors = sensors_temperatures()
        except Exception:
            return None
        if not isinstance(sensors, dict):
            return None

        preferred_values: list[float] = []
        fallback_values: list[float] = []
        for sensor_name, entries in sensors.items():
            sensor_key = str(sensor_name or "").strip().lower()
            sensor_entries = entries if isinstance(entries, list | tuple) else []
            for entry in sensor_entries:
                current = _coerce_optional_float(getattr(entry, "current", None))
                if current is None:
                    continue
                fallback_values.append(current)
                label = str(getattr(entry, "label", "") or "").strip().lower()
                if any(token in f"{sensor_key} {label}" for token in ("cpu", "core", "package", "tdie", "tctl", "k10")):
                    preferred_values.append(current)
        values = preferred_values or fallback_values
        if not values:
            return None
        return max(values)

    def _rate_and_totals(
        self,
        *,
        current: tuple[int, int],
        last: tuple[int, int] | None,
        initial: tuple[int, int] | None,
        now: float,
    ) -> tuple[float, float, int, int]:
        if initial is None:
            initial = current
        if last is None or self._last_monotonic is None:
            return 0.0, 0.0, max(0, current[0] - initial[0]), max(0, current[1] - initial[1])

        elapsed = now - self._last_monotonic
        if elapsed <= 0:
            elapsed = 0.0
        delta_a = max(0, current[0] - last[0])
        delta_b = max(0, current[1] - last[1])
        rate_a = (delta_a / elapsed) if elapsed > 0 else 0.0
        rate_b = (delta_b / elapsed) if elapsed > 0 else 0.0
        total_a = max(0, current[0] - initial[0])
        total_b = max(0, current[1] - initial[1])
        return rate_a, rate_b, total_a, total_b

    def _collect_interfaces(self, *, now: float) -> list[InterfaceBandwidthEntry]:
        started_at = time.monotonic()
        psutil = _psutil()
        metadata = self._refresh_interface_metadata(psutil, now=now, force=True)
        counters = psutil.net_io_counters(pernic=True)
        entries = self._build_interface_entries(
            now=now,
            counters=counters,
            metadata=metadata,
        )
        entries.sort(
            key=lambda entry: (
                -1 if entry.is_up else 0,
                entry.name.lower(),
            )
        )
        if self._platform_name == "windows":
            _LOGGER.debug(
                "Resource Monitor interface collection completed duration=%.3fs interfaces=%s",
                time.monotonic() - started_at,
                len(entries),
            )
        return entries

    def _collect_interface_rates(self, *, now: float) -> list[InterfaceBandwidthEntry]:
        psutil = _psutil()
        counters = psutil.net_io_counters(pernic=True)
        metadata = self._refresh_interface_metadata(psutil, now=now)
        entries = self._build_interface_entries(
            now=now,
            counters=counters,
            metadata=metadata,
        )
        entries.sort(
            key=lambda entry: (
                -1 if entry.is_up else 0,
                entry.name.lower(),
            )
        )
        return entries

    def _refresh_interface_metadata(
        self,
        psutil,  # noqa: ANN001
        *,
        now: float,
        force: bool = False,
    ) -> dict[str, _InterfaceMetadata]:
        last_refresh = self._last_interface_metadata_refresh_monotonic
        should_refresh = force or last_refresh is None or now > last_refresh
        if should_refresh:
            metadata = self._collect_interface_metadata(psutil)
            self._cached_interface_metadata = dict(metadata)
            self._last_interface_metadata_refresh_monotonic = now
        return dict(self._cached_interface_metadata)

    @staticmethod
    def _collect_interface_metadata(psutil) -> dict[str, _InterfaceMetadata]:  # noqa: ANN001
        stats = psutil.net_if_stats()
        addresses = psutil.net_if_addrs()
        metadata: dict[str, _InterfaceMetadata] = {}
        for name in sorted(set(stats) | set(addresses)):
            stat = stats.get(name)
            ipv4_address, ipv6_address = _preferred_interface_addresses(addresses.get(name, []))
            metadata[name] = _InterfaceMetadata(
                name=name,
                ipv4_address=ipv4_address,
                ipv6_address=ipv6_address,
                is_up=bool(getattr(stat, "isup", False)),
                speed_mbps=int(getattr(stat, "speed", 0) or 0),
            )
        return metadata

    def _build_interface_entries(
        self,
        *,
        now: float,
        counters,
        metadata: dict[str, _InterfaceMetadata],
    ) -> list[InterfaceBandwidthEntry]:
        last_monotonic = self._last_interface_monotonic
        elapsed = (now - last_monotonic) if last_monotonic is not None else 0.0
        if elapsed < 0:
            elapsed = 0.0

        entries: list[InterfaceBandwidthEntry] = []
        next_totals: dict[str, tuple[int, int]] = {}
        for name in sorted(set(counters) | set(metadata)):
            counter = counters.get(name)
            recv_total = int(getattr(counter, "bytes_recv", 0) or 0)
            send_total = int(getattr(counter, "bytes_sent", 0) or 0)
            next_totals[name] = (recv_total, send_total)

            previous = self._last_interface_totals.get(name)
            recv_rate = 0.0
            send_rate = 0.0
            if previous is not None and elapsed > 0:
                recv_rate = max(0.0, (recv_total - previous[0]) / elapsed)
                send_rate = max(0.0, (send_total - previous[1]) / elapsed)

            interface_metadata = metadata.get(name)
            is_up = interface_metadata.is_up if interface_metadata is not None else False
            speed_mbps = interface_metadata.speed_mbps if interface_metadata is not None else 0
            ipv4_address = interface_metadata.ipv4_address if interface_metadata is not None else ""
            ipv6_address = interface_metadata.ipv6_address if interface_metadata is not None else ""
            entries.append(
                InterfaceBandwidthEntry(
                    name=name,
                    ipv4_address=ipv4_address,
                    ipv6_address=ipv6_address,
                    is_up=is_up,
                    speed_mbps=speed_mbps,
                    recv_bytes_per_sec=recv_rate,
                    sent_bytes_per_sec=send_rate,
                    recv_bytes_total=recv_total,
                    sent_bytes_total=send_total,
                )
            )

        self._last_interface_totals = next_totals
        self._last_interface_monotonic = now
        return entries

    def _collect_gpu(self) -> GpuSample:
        if self._gpu_disabled:
            return GpuSample(message=self._gpu_unavailable_message or "GPU telemetry is unavailable on this system.")
        adapters = self._gpu_inventory_snapshot()
        if self._platform_name == "linux":
            _apply_nvidia_smi_metrics(adapters, platform_name=self._platform_name)
            _apply_linux_amd_sysfs_metrics(adapters)
            _apply_linux_intel_sysfs_metrics(adapters)
            _apply_amd_smi_metrics(adapters, platform_name=self._platform_name)
            _apply_rocm_smi_metrics(adapters, platform_name=self._platform_name)
            _apply_intel_gpu_top_metrics(adapters, platform_name=self._platform_name)
            _apply_linux_drm_fdinfo_metrics(
                adapters,
                previous_engine_totals=self._linux_drm_fdinfo_engine_totals,
                now=time.monotonic(),
            )
        elif self._platform_name == "windows":
            now = time.monotonic()
            _apply_nvidia_smi_metrics(adapters, platform_name=self._platform_name)
            _apply_amd_smi_metrics(adapters, platform_name=self._platform_name)
            _apply_windows_gpu_counter_metrics(
                adapters,
                platform_name=self._platform_name,
                payload=self._cached_windows_gpu_counter_payload(now=now),
            )
            _apply_windows_hardware_monitor_gpu_metrics(
                adapters,
                platform_name=self._platform_name,
                payload=self._cached_windows_gpu_sensor_payload(now=now),
            )
        elif self._platform_name == "darwin":
            _apply_system_profiler_gpu_metrics(adapters)

        return _build_gpu_sample(adapters)

    def _cached_windows_gpu_counter_payload(self, *, now: float) -> object | None:
        if self._platform_name != "windows":
            return None
        backoff_until = self._windows_gpu_counter_backoff_until
        if backoff_until is not None and now < backoff_until:
            if not self._windows_gpu_counter_backoff_active:
                self._windows_gpu_counter_backoff_active = True
                _LOGGER.warning(
                    "Windows GPU counter sampler entered backoff until %.3f; using cached payload.",
                    backoff_until,
                )
            return self._windows_gpu_counter_payload
        checked_at = self._windows_gpu_counter_checked_at
        if checked_at is not None and (now - checked_at) < _WINDOWS_GPU_COUNTER_CACHE_SECONDS:
            return self._windows_gpu_counter_payload

        previous_payload = self._windows_gpu_counter_payload
        self._windows_gpu_counter_checked_at = now
        probe_failed = False
        source = "sampler" if self._windows_gpu_counter_sampler is not None else "powershell"
        started_at = time.monotonic()
        try:
            sampler = self._windows_gpu_counter_sampler
            if sampler is not None:
                payload = sampler.collect_payload()
            else:
                payload = _probe_windows_gpu_counter_payload(platform_name=self._platform_name)
        except Exception:
            payload = None
            probe_failed = True
            _LOGGER.warning(
                "Windows GPU counter payload collection failed source=%s duration=%.3fs",
                source,
                time.monotonic() - started_at,
                exc_info=True,
            )
        else:
            probe_failed = payload is None
            engine_rows = len(_normalize_json_entries(payload.get("engine") if isinstance(payload, dict) else None))
            memory_rows = len(_normalize_json_entries(payload.get("memory") if isinstance(payload, dict) else None))
            _LOGGER.debug(
                "Windows GPU counter payload collected source=%s duration=%.3fs engine_rows=%s memory_rows=%s",
                source,
                time.monotonic() - started_at,
                engine_rows,
                memory_rows,
            )
        if payload is None and previous_payload is not None:
            payload = previous_payload
        if probe_failed:
            self._windows_gpu_counter_failure_count += 1
            if self._windows_gpu_counter_failure_count >= _WINDOWS_GPU_PROBE_FAILURE_LIMIT:
                self._windows_gpu_counter_backoff_until = now + _WINDOWS_GPU_PROBE_BACKOFF_SECONDS
                if not self._windows_gpu_counter_backoff_active:
                    self._windows_gpu_counter_backoff_active = True
                    _LOGGER.warning(
                        "Windows GPU counter sampler entered backoff after %s consecutive failures.",
                        self._windows_gpu_counter_failure_count,
                    )
        else:
            self._windows_gpu_counter_failure_count = 0
            self._windows_gpu_counter_backoff_until = None
            if self._windows_gpu_counter_backoff_active:
                self._windows_gpu_counter_backoff_active = False
                _LOGGER.info("Windows GPU counter sampler recovered and exited backoff.")
        self._windows_gpu_counter_payload = payload
        return payload

    def _cached_windows_gpu_sensor_payload(self, *, now: float) -> object | None:
        if self._platform_name != "windows":
            return None
        backoff_until = self._windows_gpu_sensor_backoff_until
        if backoff_until is not None and now < backoff_until:
            if not self._windows_gpu_sensor_backoff_active:
                self._windows_gpu_sensor_backoff_active = True
                _LOGGER.warning(
                    "Windows GPU sensor probe entered backoff until %.3f; using cached payload.",
                    backoff_until,
                )
            return self._windows_gpu_sensor_payload
        checked_at = self._windows_gpu_sensor_checked_at
        if checked_at is not None and (now - checked_at) < _WINDOWS_GPU_SENSOR_CACHE_SECONDS:
            return self._windows_gpu_sensor_payload

        previous_payload = self._windows_gpu_sensor_payload
        self._windows_gpu_sensor_checked_at = now
        probe_failed = False
        started_at = time.monotonic()
        try:
            payload = _probe_windows_gpu_sensor_payload(platform_name=self._platform_name)
        except Exception:
            payload = None
            probe_failed = True
            _LOGGER.warning(
                "Windows GPU sensor payload collection failed duration=%.3fs",
                time.monotonic() - started_at,
                exc_info=True,
            )
        else:
            probe_failed = payload is None
            libre_rows = len(_normalize_json_entries(payload.get("libre") if isinstance(payload, dict) else None))
            open_rows = len(_normalize_json_entries(payload.get("open") if isinstance(payload, dict) else None))
            _LOGGER.debug(
                "Windows GPU sensor payload collected duration=%.3fs libre_rows=%s open_rows=%s",
                time.monotonic() - started_at,
                libre_rows,
                open_rows,
            )
        if payload is None and previous_payload is not None:
            payload = previous_payload
        if probe_failed:
            self._windows_gpu_sensor_failure_count += 1
            if self._windows_gpu_sensor_failure_count >= _WINDOWS_GPU_PROBE_FAILURE_LIMIT:
                self._windows_gpu_sensor_backoff_until = now + _WINDOWS_GPU_PROBE_BACKOFF_SECONDS
                if not self._windows_gpu_sensor_backoff_active:
                    self._windows_gpu_sensor_backoff_active = True
                    _LOGGER.warning(
                        "Windows GPU sensor probe entered backoff after %s consecutive failures.",
                        self._windows_gpu_sensor_failure_count,
                    )
        else:
            self._windows_gpu_sensor_failure_count = 0
            self._windows_gpu_sensor_backoff_until = None
            if self._windows_gpu_sensor_backoff_active:
                self._windows_gpu_sensor_backoff_active = False
                _LOGGER.info("Windows GPU sensor probe recovered and exited backoff.")
        self._windows_gpu_sensor_payload = payload
        return payload

    def _gpu_inventory_snapshot(self) -> list[_GpuAdapterState]:
        now = time.monotonic()
        checked_at = self._gpu_inventory_checked_at
        if (
            self._gpu_inventory_cache is None
            or checked_at is None
            or (now - checked_at) >= _GPU_INVENTORY_CACHE_SECONDS
        ):
            collected = _collect_gpu_inventory(platform_name=self._platform_name)
            if collected or self._gpu_inventory_cache is None:
                self._gpu_inventory_cache = collected
            self._gpu_inventory_checked_at = now
        return _copy_gpu_adapter_states(self._gpu_inventory_cache or [])


class ResourceProcessCollector:
    def __init__(self) -> None:
        self._process_cache: dict[int, object] = {}
        self._last_thread_count = 0

    def collect_counts(self, *, stop_callback: StopCallback | None = None) -> ProcessCountsSnapshot:
        _raise_if_stop_requested(stop_callback, "Resource Monitor process count refresh cancelled.")
        psutil = _psutil()
        collected_at = time.monotonic()
        errors: list[str] = []
        try:
            pids = psutil.pids()
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))
            return ProcessCountsSnapshot(collected_at=collected_at, errors=errors)

        current_pid_set = set(pids)
        stale_pids = [pid for pid in self._process_cache if pid not in current_pid_set]
        for pid in stale_pids:
            self._process_cache.pop(pid, None)

        return ProcessCountsSnapshot(
            process_count=len(pids),
            thread_count=max(0, int(self._last_thread_count)),
            collected_at=collected_at,
            errors=errors,
        )

    def collect(self, *, stop_callback: StopCallback | None = None) -> ProcessInventorySnapshot:
        psutil = _psutil()
        collected_at = time.monotonic()
        collection_started_at = time.monotonic()
        entries: list[ProcessEntry] = []
        errors: list[str] = []
        total_threads = 0

        _raise_if_stop_requested(stop_callback, "Resource Monitor process refresh cancelled.")
        pids = sorted(psutil.pids())
        current_pid_set = set(pids)
        stale_pids = [pid for pid in self._process_cache if pid not in current_pid_set]
        for pid in stale_pids:
            self._process_cache.pop(pid, None)

        no_such_process = getattr(psutil, "NoSuchProcess", RuntimeError)
        zombie_process = getattr(psutil, "ZombieProcess", RuntimeError)
        access_denied = getattr(psutil, "AccessDenied", RuntimeError)

        for pid in pids:
            _raise_if_stop_requested(stop_callback, "Resource Monitor process refresh cancelled.")
            try:
                process = self._process_cache.get(pid)
                cpu_percent = 0.0
                if process is None:
                    process = psutil.Process(pid)
                    self._process_cache[pid] = process
                    _call_or_default(lambda: process.cpu_percent(interval=None), 0.0)
                else:
                    cpu_percent = _safe_float(_call_or_default(lambda: process.cpu_percent(interval=None), 0.0))

                with process.oneshot():
                    name = str(_call_or_default(process.name, "") or "").strip() or f"PID {pid}"
                    memory_rss = int(getattr(_call_or_default(process.memory_info, None), "rss", 0) or 0)
                    threads = int(_call_or_default(process.num_threads, 0) or 0)
                    user = str(_call_or_default(process.username, "") or "").strip()
                    status = str(_call_or_default(process.status, "") or "").strip()
                    process_started_at = _coerce_optional_float(_call_or_default(process.create_time, None))
                    command = _command_text(_call_or_default(process.cmdline, []) or [])
                total_threads += max(0, threads)
                entries.append(
                    ProcessEntry(
                        pid=pid,
                        name=name,
                        cpu_percent=cpu_percent,
                        memory_rss_bytes=memory_rss,
                        threads=threads,
                        user=user,
                        status=status,
                        started_at=process_started_at,
                        command=command,
                    )
                )
            except (no_such_process, zombie_process):
                self._process_cache.pop(pid, None)
            except access_denied:
                self._process_cache.pop(pid, None)
                entries.append(
                    ProcessEntry(
                        pid=pid,
                        name=f"PID {pid}",
                        cpu_percent=0.0,
                        memory_rss_bytes=0,
                        threads=0,
                        user="",
                        status="access denied",
                        started_at=None,
                        command="",
                    )
                )
            except Exception as exc:  # noqa: BLE001
                self._process_cache.pop(pid, None)
                errors.append(f"PID {pid}: {exc}")

        entries.sort(key=lambda entry: (-entry.cpu_percent, -entry.memory_rss_bytes, entry.name.lower(), entry.pid))
        self._last_thread_count = total_threads
        duration = time.monotonic() - collection_started_at
        if _platform_name() == "windows":
            _LOGGER.debug(
                "Resource Monitor process inventory collected duration=%.3fs entries=%s total_threads=%s errors=%s",
                duration,
                len(entries),
                total_threads,
                len(errors),
            )
        if duration > _PROCESS_REFRESH_WARNING_SECONDS:
            _LOGGER.warning(
                "Resource Monitor process inventory refresh was slow duration=%.3fs entries=%s",
                duration,
                len(entries),
            )
        return ProcessInventorySnapshot(
            entries=entries,
            total_threads=total_threads,
            collected_at=collected_at,
            errors=errors,
        )


def perform_process_action(
    pid: int,
    *,
    force: bool = False,
    allow_elevation: bool = False,
    current_pid: int | None = None,
    platform_name: str | None = None,
) -> ProcessActionResult:
    action = "kill" if force else "terminate"
    if pid <= 0:
        return ProcessActionResult(success=False, message="Invalid process ID.", pid=pid, action=action)
    if current_pid is not None and pid == int(current_pid):
        return ProcessActionResult(
            success=False,
            message="SnakeSh cannot terminate its own process from the Resource Monitor.",
            pid=pid,
            action=action,
        )

    psutil = _psutil()
    no_such_process = getattr(psutil, "NoSuchProcess", RuntimeError)

    try:
        process = psutil.Process(pid)
    except no_such_process:
        return ProcessActionResult(
            success=True,
            message=f"Process {pid} has already exited.",
            pid=pid,
            action=action,
        )
    except Exception as exc:  # noqa: BLE001
        return ProcessActionResult(success=False, message=str(exc), pid=pid, action=action)

    try:
        if force:
            process.kill()
        else:
            process.terminate()
        process.wait(timeout=5.0)
        return ProcessActionResult(
            success=True,
            message=f"Process {pid} was {'force killed' if force else 'terminated'}.",
            pid=pid,
            action=action,
        )
    except no_such_process:
        return ProcessActionResult(
            success=True,
            message=f"Process {pid} has already exited.",
            pid=pid,
            action=action,
        )
    except Exception as exc:  # noqa: BLE001
        if _is_permission_error(exc):
            if not allow_elevation:
                return ProcessActionResult(
                    success=False,
                    message=f"Administrative privileges are required to {'force kill' if force else 'terminate'} PID {pid}.",
                    pid=pid,
                    action=action,
                    requires_elevation=True,
                )
            command = build_elevated_process_action_command(pid, force=force, platform_name=platform_name)
            result = run_command(command, require_elevation=True, timeout=15.0)
            if result.cancelled:
                return ProcessActionResult(
                    success=False,
                    message="Elevation was cancelled by the user.",
                    pid=pid,
                    action=action,
                    elevated=True,
                    cancelled=True,
                )
            if not result.success:
                return ProcessActionResult(
                    success=False,
                    message=result.message,
                    pid=pid,
                    action=action,
                    elevated=result.elevated,
                )
            if _wait_for_process_exit(pid, timeout=5.0):
                return ProcessActionResult(
                    success=True,
                    message=f"Process {pid} was {'force killed' if force else 'terminated'} with administrator privileges.",
                    pid=pid,
                    action=action,
                    elevated=True,
                )
            return ProcessActionResult(
                success=False,
                message=f"Process {pid} is still running after the elevated {'kill' if force else 'terminate'} request.",
                pid=pid,
                action=action,
                elevated=True,
            )
        return ProcessActionResult(success=False, message=str(exc), pid=pid, action=action)


def build_elevated_process_action_command(
    pid: int,
    *,
    force: bool = False,
    platform_name: str | None = None,
) -> list[str]:
    system = _platform_name(platform_name)
    if system == "windows":
        command = ["taskkill", "/PID", str(pid), "/T"]
        if force:
            command.append("/F")
        return command
    signal_name = "-KILL" if force else "-TERM"
    return ["/bin/kill", signal_name, str(pid)]


def collect_filesystem_entries(home_path: str | Path | None = None) -> list[FilesystemEntry]:
    psutil = _psutil()
    home = Path(home_path).expanduser() if home_path is not None else Path.home()
    partitions = psutil.disk_partitions(all=False)
    seen_mounts: set[str] = set()
    entries: list[FilesystemEntry] = []

    home_mount = determine_home_mountpoint(partitions, home)
    for partition in partitions:
        mountpoint = str(getattr(partition, "mountpoint", "") or "").strip()
        if not mountpoint or mountpoint in seen_mounts:
            continue
        seen_mounts.add(mountpoint)
        try:
            usage = psutil.disk_usage(mountpoint)
        except Exception:
            continue
        entries.append(
            FilesystemEntry(
                device=str(getattr(partition, "device", "") or "").strip(),
                mountpoint=mountpoint,
                filesystem_type=str(getattr(partition, "fstype", "") or "").strip(),
                used_bytes=int(getattr(usage, "used", 0) or 0),
                total_bytes=int(getattr(usage, "total", 0) or 0),
                free_bytes=int(getattr(usage, "free", 0) or 0),
                usage_percent=_safe_float(getattr(usage, "percent", 0.0)),
                is_home=mountpoint == home_mount,
            )
        )
    entries.sort(key=lambda entry: (0 if entry.is_home else 1, entry.mountpoint.lower()))
    return entries


def determine_home_mountpoint(partitions: list[object], home_path: str | Path) -> str:
    home = _normalized_path_for_match(Path(home_path).expanduser())
    best_mount = ""
    best_length = -1
    for partition in partitions:
        mountpoint = str(getattr(partition, "mountpoint", "") or "").strip()
        if not mountpoint:
            continue
        candidate = _normalized_path_for_match(Path(mountpoint))
        if not home.startswith(candidate.rstrip("/") + "/") and home != candidate:
            continue
        if len(candidate) > best_length:
            best_length = len(candidate)
            best_mount = mountpoint
    return best_mount


def _disk_totals() -> tuple[int, int]:
    psutil = _psutil()
    counters = psutil.disk_io_counters()
    return (
        int(getattr(counters, "read_bytes", 0) or 0),
        int(getattr(counters, "write_bytes", 0) or 0),
    )


def _disk_totals_per_device(
    *,
    platform_name: str | None = None,
) -> dict[str, tuple[str, tuple[int, int]]]:
    psutil = _psutil()
    try:
        counters = psutil.disk_io_counters(perdisk=True)
    except Exception:
        return {}
    if not isinstance(counters, dict):
        return {}

    totals_by_key: dict[str, tuple[str, tuple[int, int]]] = {}
    for raw_name, counter in counters.items():
        key = _normalize_disk_counter_key(str(raw_name or ""), platform_name=platform_name)
        if not key:
            continue
        totals = (
            int(getattr(counter, "read_bytes", 0) or 0),
            int(getattr(counter, "write_bytes", 0) or 0),
        )
        display_label = _disk_device_display_label(
            str(raw_name or ""),
            key=key,
            platform_name=platform_name,
        )
        existing = totals_by_key.get(key)
        if existing is None or sum(totals) >= sum(existing[1]):
            totals_by_key[key] = (display_label, totals)
    return totals_by_key


def _filesystem_disk_device_key(
    entry: FilesystemEntry,
    *,
    platform_name: str | None = None,
    windows_volume_to_disk_map: dict[str, str] | None = None,
) -> str:
    system = _platform_name(platform_name)
    if system == "windows":
        for candidate in (entry.device, entry.mountpoint):
            drive_key = _normalize_windows_drive_letter(candidate)
            if drive_key:
                return (windows_volume_to_disk_map or {}).get(drive_key, drive_key)
            disk_key = _normalize_windows_disk_device_name(candidate)
            if disk_key:
                return disk_key
        return ""
    return _normalize_posix_disk_device_name(entry.device)


def _normalize_disk_counter_key(value: str, *, platform_name: str | None = None) -> str:
    system = _platform_name(platform_name)
    if system == "windows":
        return _normalize_windows_disk_device_name(value)
    return _normalize_posix_disk_device_name(value)


def _normalize_posix_disk_device_name(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return Path(text).name.strip().casefold()


def _normalize_windows_drive_letter(value: str) -> str:
    text = str(value or "").strip()
    match = re.match(r"^([a-zA-Z]):(?:[\\/]|$)", text)
    if match is None:
        return ""
    return f"{match.group(1).lower()}:"


def _normalize_windows_disk_device_name(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    drive_key = _normalize_windows_drive_letter(text)
    if drive_key:
        return drive_key

    normalized = text.replace("/", "\\")
    match = re.search(r"physicaldrive\s*(\d+)", normalized, re.IGNORECASE)
    if match is not None:
        return f"physicaldrive{int(match.group(1))}"

    for prefix in ("\\\\.\\", "\\??\\"):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :]
            break
    return normalized.strip("\\").casefold()


def _disk_device_display_label(
    value: str,
    *,
    key: str,
    platform_name: str | None = None,
) -> str:
    system = _platform_name(platform_name)
    if system == "windows":
        match = re.match(r"^physicaldrive(\d+)$", key, re.IGNORECASE)
        if match is not None:
            return f"PhysicalDrive{match.group(1)}"
        drive_key = _normalize_windows_drive_letter(value) or _normalize_windows_drive_letter(key)
        if drive_key:
            return drive_key.upper()
        return str(value or key).strip() or key

    label = Path(str(value or "").strip()).name.strip()
    if label:
        return label
    return key


def _collect_windows_volume_to_disk_map(*, platform_name: str | None = None) -> dict[str, str]:
    output = _run_windows_powershell_output(
        _windows_disk_mapping_script(),
        timeout=_WINDOWS_DISK_MAPPING_TIMEOUT_SECONDS,
        platform_name=platform_name,
    )
    if output is None:
        return {}
    return _parse_windows_volume_to_disk_map(_load_json_payload(output))


def _parse_windows_volume_to_disk_map(payload: object) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for entry in _normalize_json_entries(payload):
        logical = _normalize_windows_drive_letter(_extract_text_value(entry, ("logical", "drive", "deviceid")))
        if not logical or logical in mapping:
            continue
        disk_index = _extract_int_value(entry, ("diskindex", "index"))
        disk_device_id = _extract_text_value(entry, ("diskdeviceid", "deviceid"))
        disk_key = (
            f"physicaldrive{disk_index}"
            if disk_index is not None and disk_index >= 0
            else _normalize_windows_disk_device_name(disk_device_id)
        )
        if disk_key:
            mapping[logical] = disk_key
    return mapping


def _network_totals() -> tuple[int, int]:
    psutil = _psutil()
    counters = psutil.net_io_counters()
    return (
        int(getattr(counters, "bytes_recv", 0) or 0),
        int(getattr(counters, "bytes_sent", 0) or 0),
    )


def _preferred_interface_addresses(addresses: list[object]) -> tuple[str, str]:
    preferred_ipv4 = ""
    preferred_ipv6 = ""
    best_ipv4_rank = 99
    best_ipv6_rank = 99
    for address in addresses:
        family = getattr(address, "family", None)
        value = str(getattr(address, "address", "") or "").strip()
        if not value:
            continue
        if family == socket.AF_INET:
            rank = _interface_address_rank(value)
            if rank < best_ipv4_rank:
                preferred_ipv4 = value
                best_ipv4_rank = rank
        elif family == socket.AF_INET6:
            rank = _interface_address_rank(value)
            if rank < best_ipv6_rank:
                preferred_ipv6 = value
                best_ipv6_rank = rank
    return preferred_ipv4, preferred_ipv6


def _interface_address_rank(value: str) -> int:
    normalized = value.split("%", 1)[0].strip()
    try:
        parsed = ipaddress.ip_address(normalized)
    except ValueError:
        return 98
    if parsed.is_unspecified:
        return 97
    if parsed.is_loopback:
        return 2
    if parsed.is_link_local:
        return 1
    return 0


@dataclass(slots=True)
class _GpuAdapterState:
    id: str
    vendor: str
    name: str
    adapter_index: int | None = None
    windows_phys_index: int | None = None
    backend: str = ""
    utilization_percent: float | None = None
    memory_used_bytes: int | None = None
    memory_total_bytes: int | None = None
    temperature_c: float | None = None
    sysfs_path: Path | None = None


@dataclass(frozen=True, slots=True)
class _LinuxDrmFdinfoSample:
    adapter_id: str
    client_key: str
    engine_time_ns: int = 0
    memory_used_bytes: int | None = None
    memory_total_bytes: int | None = None


_GPU_VENDOR_NAMES = {
    "nvidia": "NVIDIA",
    "amd": "AMD",
    "intel": "Intel",
    "apple": "Apple",
}
_PCI_VENDOR_NAME_MAP = {
    "0x10de": "NVIDIA",
    "0x1002": "AMD",
    "0x1022": "AMD",
    "0x8086": "Intel",
    "0x106b": "Apple",
}
_DRM_CARD_PATTERN = re.compile(r"^card\d+$")
_WINDOWS_GPU_PHYS_INDEX_PATTERN = re.compile(r"phys_(\d+)", re.IGNORECASE)
_UNKNOWN_INTEL_DRM_ADAPTER_ID = "__intel_drm_unknown_adapter__"


def _copy_gpu_adapter_states(adapters: list[_GpuAdapterState]) -> list[_GpuAdapterState]:
    return [
        _GpuAdapterState(
            id=adapter.id,
            vendor=adapter.vendor,
            name=adapter.name,
            adapter_index=adapter.adapter_index,
            windows_phys_index=adapter.windows_phys_index,
            backend=adapter.backend,
            utilization_percent=adapter.utilization_percent,
            memory_used_bytes=adapter.memory_used_bytes,
            memory_total_bytes=adapter.memory_total_bytes,
            temperature_c=adapter.temperature_c,
            sysfs_path=adapter.sysfs_path,
        )
        for adapter in adapters
    ]


def _collect_gpu_inventory(*, platform_name: str | None = None) -> list[_GpuAdapterState]:
    system = _platform_name(platform_name)
    if system == "linux":
        return _collect_linux_gpu_inventory()
    if system == "windows":
        return _collect_windows_gpu_inventory(platform_name=platform_name)
    if system == "darwin":
        return _collect_darwin_gpu_inventory(platform_name=platform_name)
    return []


def _build_gpu_sample(adapters: list[_GpuAdapterState]) -> GpuSample:
    detected = bool(adapters)
    utilization_values = [adapter.utilization_percent for adapter in adapters if adapter.utilization_percent is not None]
    memory_pairs = [
        (adapter.memory_used_bytes, adapter.memory_total_bytes)
        for adapter in adapters
        if adapter.memory_used_bytes is not None and adapter.memory_total_bytes is not None and adapter.memory_total_bytes > 0
    ]
    temperature_values = [adapter.temperature_c for adapter in adapters if adapter.temperature_c is not None]
    has_utilization = bool(utilization_values)
    has_memory = bool(memory_pairs)
    has_temperature = bool(temperature_values)

    utilization_percent = (
        sum(utilization_values) / len(utilization_values)
        if utilization_values
        else None
    )
    memory_used_bytes = (
        sum(int(used or 0) for used, _total in memory_pairs)
        if memory_pairs
        else None
    )
    memory_total_bytes = (
        sum(int(total or 0) for _used, total in memory_pairs)
        if memory_pairs
        else None
    )
    memory_percent: float | None = None
    if memory_used_bytes is not None and memory_total_bytes and memory_total_bytes > 0:
        memory_percent = min(100.0, max(0.0, (memory_used_bytes / memory_total_bytes) * 100.0))

    message = ""
    if not detected:
        message = "GPU telemetry is unavailable on this system."
    elif not (has_utilization or has_memory or has_temperature):
        message = "GPU detected, but live telemetry is unavailable on this system."
    elif not (has_utilization and has_memory and has_temperature):
        message = "Some GPU metrics are unavailable on this system."

    return GpuSample(
        available=detected,
        detected=detected,
        name=_gpu_display_name(adapters),
        gpu_count=len(adapters),
        utilization_percent=utilization_percent,
        memory_used_bytes=memory_used_bytes,
        memory_total_bytes=memory_total_bytes,
        memory_percent=memory_percent,
        temperature_c=max(temperature_values) if temperature_values else None,
        has_utilization=has_utilization,
        has_memory=has_memory,
        has_temperature=has_temperature,
        adapters=[
            GpuAdapterSample(
                id=adapter.id,
                vendor=adapter.vendor,
                name=adapter.name,
                adapter_index=adapter.adapter_index,
                backend=adapter.backend,
                utilization_percent=adapter.utilization_percent,
                memory_used_bytes=adapter.memory_used_bytes,
                memory_total_bytes=adapter.memory_total_bytes,
                temperature_c=adapter.temperature_c,
            )
            for adapter in adapters
        ],
        message=message,
    )


def _gpu_display_name(adapters: list[_GpuAdapterState]) -> str:
    if not adapters:
        return ""
    if len(adapters) == 1:
        return adapters[0].name or adapters[0].vendor or "GPU"
    return f"{len(adapters)} GPUs"


def _collect_linux_gpu_inventory(*, sys_class_drm: str | Path = "/sys/class/drm") -> list[_GpuAdapterState]:
    root = Path(sys_class_drm)
    if not root.exists():
        return []
    adapters: list[_GpuAdapterState] = []
    seen_ids: set[str] = set()
    for child in sorted(root.iterdir(), key=lambda path: path.name):
        if not _DRM_CARD_PATTERN.match(child.name):
            continue
        device_path = child / "device"
        if not device_path.exists():
            continue
        class_code = _read_text_file(device_path / "class")
        if class_code and not str(class_code).strip().lower().startswith("0x03"):
            continue
        resolved_path = _safe_resolve_path(device_path)
        adapter_id = _gpu_adapter_id_from_device_path(resolved_path, fallback=child.name)
        if adapter_id in seen_ids:
            continue
        seen_ids.add(adapter_id)
        vendor = _gpu_vendor_from_pci_vendor(_read_text_file(device_path / "vendor"))
        name = _linux_gpu_name_from_sysfs(device_path, vendor=vendor, fallback=child.name)
        adapter = _GpuAdapterState(
            id=adapter_id,
            vendor=vendor or "Unknown",
            name=name,
            adapter_index=len(adapters),
            sysfs_path=device_path,
        )
        total_vram = _read_int_file(device_path / "mem_info_vram_total")
        if total_vram is not None and total_vram > 0:
            adapter.memory_total_bytes = total_vram
        adapters.append(adapter)
    return adapters


def _collect_windows_gpu_inventory(*, platform_name: str | None = None) -> list[_GpuAdapterState]:
    payload = _probe_windows_gpu_inventory_payload(platform_name=platform_name)
    entries = _normalize_json_entries(payload)
    adapters: list[_GpuAdapterState] = []
    seen_ids: set[str] = set()
    for index, entry in enumerate(entries):
        raw_name = _extract_text_value(entry, ("name", "caption"))
        vendor_text = _extract_text_value(entry, ("adaptercompatibility", "vendor", "videoprocessor"))
        adapter_id = _extract_text_value(entry, ("pnpdeviceid", "deviceid")) or f"windows-gpu-{index}"
        if not _looks_like_physical_gpu(raw_name, vendor_text, adapter_id):
            continue
        normalized_id = adapter_id.strip() or f"windows-gpu-{index}"
        if normalized_id in seen_ids:
            continue
        seen_ids.add(normalized_id)
        vendor = _preferred_gpu_vendor(name=raw_name, compatibility=vendor_text)
        adapter = _GpuAdapterState(
            id=normalized_id,
            vendor=vendor or "Unknown",
            name=raw_name or (vendor or "GPU"),
            adapter_index=len(adapters),
        )
        adapter_ram = _extract_int_value(entry, ("adapterram",))
        if adapter_ram is not None and adapter_ram > 0:
            adapter.memory_total_bytes = adapter_ram
        adapters.append(adapter)
    return adapters


def _collect_darwin_gpu_inventory(*, platform_name: str | None = None) -> list[_GpuAdapterState]:
    _ = platform_name
    executable = shutil.which("system_profiler")
    if not executable:
        return []
    completed = subprocess.run(
        [executable, "SPDisplaysDataType", "-json"],
        check=False,
        capture_output=True,
        text=True,
        timeout=4.0,
    )
    if completed.returncode != 0:
        return []
    try:
        payload = json.loads(completed.stdout or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    entries = _normalize_json_entries(payload.get("SPDisplaysDataType"))
    adapters: list[_GpuAdapterState] = []
    for index, entry in enumerate(entries):
        name = _extract_text_value(entry, ("sppci_model", "chipset_model", "_name")) or f"GPU {index + 1}"
        vendor = _gpu_vendor_from_text(_extract_text_value(entry, ("spdisplays_vendor", "vendor")) or name)
        adapter = _GpuAdapterState(
            id=_extract_text_value(entry, ("spdisplays_device-id", "spdisplays_pci_device")) or f"darwin-gpu-{index}",
            vendor=vendor or "Apple",
            name=name,
            adapter_index=len(adapters),
        )
        adapters.append(adapter)
    return adapters


def _safe_resolve_path(path: Path) -> Path:
    try:
        return path.resolve()
    except Exception:
        return path


def _gpu_adapter_id_from_device_path(path: Path, *, fallback: str) -> str:
    name = path.name.strip()
    if re.match(r"^[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-9a-f]$", name, re.IGNORECASE):
        return name.lower()
    return fallback


def _linux_gpu_name_from_sysfs(device_path: Path, *, vendor: str, fallback: str) -> str:
    for candidate in (
        device_path / "product_name",
        device_path / "product_number",
    ):
        value = _read_text_file(candidate)
        if value:
            return str(value).strip()
    lspci_name = _linux_gpu_name_from_lspci(device_path)
    if lspci_name:
        return lspci_name
    vendor_label = vendor or _gpu_vendor_from_text(_read_text_file(device_path / "uevent") or "") or "GPU"
    return f"{vendor_label} {fallback}"


def _linux_gpu_name_from_lspci(device_path: Path) -> str:
    executable = shutil.which("lspci")
    if not executable:
        return ""
    adapter_id = _gpu_adapter_id_from_device_path(_safe_resolve_path(device_path), fallback="")
    if not adapter_id:
        return ""
    try:
        completed = subprocess.run(
            [executable, "-D", "-s", adapter_id],
            check=False,
            capture_output=True,
            text=True,
            timeout=1.5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if completed.returncode != 0:
        return ""
    output = (completed.stdout or "").strip()
    if not output:
        return ""
    match = re.match(r"^[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-9a-f]\s+(.+)$", output, re.IGNORECASE)
    if match is not None:
        output = match.group(1).strip()
    if ":" in output:
        output = output.split(":", 1)[1].strip()
    return re.sub(r"\s*\(rev\s+[0-9a-f]+\)\s*$", "", output, flags=re.IGNORECASE).strip()


def _gpu_vendor_from_pci_vendor(value: str | None) -> str:
    key = str(value or "").strip().lower()
    return _PCI_VENDOR_NAME_MAP.get(key, "")


def _gpu_vendor_from_text(value: str) -> str:
    lowered = str(value or "").strip().lower()
    if not lowered:
        return ""
    if any(token in lowered for token in ("nvidia", "geforce", "quadro", "tesla", "rtx")):
        return "NVIDIA"
    if any(token in lowered for token in ("advanced micro devices", "amd", "radeon", "ati")):
        return "AMD"
    if any(token in lowered for token in ("intel", "iris", "uhd", "arc")):
        return "Intel"
    if "apple" in lowered:
        return "Apple"
    return ""


def _preferred_gpu_vendor(*, name: str, compatibility: str) -> str:
    name_vendor = _gpu_vendor_from_text(name)
    compatibility_vendor = _gpu_vendor_from_text(compatibility)
    return name_vendor or compatibility_vendor


def _looks_like_physical_gpu(name: str, vendor: str, adapter_id: str) -> bool:
    combined = " ".join(part for part in (name, vendor, adapter_id) if part).lower()
    if not combined:
        return False
    excluded_tokens = (
        "basic render",
        "remote display",
        "remotefx",
        "virtualbox",
        "vmware",
        "hyper-v",
        "parallels",
        "qxl",
    )
    return not any(token in combined for token in excluded_tokens)


def _read_text_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except Exception:
        return ""


def _read_int_file(path: Path) -> int | None:
    raw_value = _read_text_file(path)
    if not raw_value:
        return None
    try:
        return int(raw_value, 10)
    except ValueError:
        try:
            return int(raw_value, 0)
        except ValueError:
            return None


def _normalize_json_entries(payload: object) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        return [payload]
    if isinstance(payload, list | tuple):
        return [entry for entry in payload if isinstance(entry, dict)]
    return []


def _extract_text_value(payload: object, candidate_keys: tuple[str, ...]) -> str:
    for key, value in _walk_json_scalars(payload):
        normalized_key = key.casefold()
        if any(candidate.casefold() in normalized_key for candidate in candidate_keys):
            text = str(value or "").strip()
            if text:
                return text
    return ""


def _extract_float_value(payload: object, candidate_keys: tuple[str, ...]) -> float | None:
    for key, value in _walk_json_scalars(payload):
        normalized_key = key.casefold()
        if not any(candidate.casefold() in normalized_key for candidate in candidate_keys):
            continue
        numeric = _coerce_optional_float(value)
        if numeric is not None:
            return numeric
    return None


def _extract_int_value(payload: object, candidate_keys: tuple[str, ...]) -> int | None:
    value = _extract_float_value(payload, candidate_keys)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _walk_json_scalars(payload: object, *, prefix: str = "") -> list[tuple[str, object]]:
    items: list[tuple[str, object]] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            items.extend(_walk_json_scalars(value, prefix=child_prefix))
        return items
    if isinstance(payload, list | tuple):
        for index, value in enumerate(payload):
            child_prefix = f"{prefix}[{index}]" if prefix else f"[{index}]"
            items.extend(_walk_json_scalars(value, prefix=child_prefix))
        return items
    items.append((prefix, payload))
    return items


def _merge_gpu_metrics(
    adapter: _GpuAdapterState,
    *,
    backend: str,
    name: str = "",
    utilization_percent: float | None = None,
    memory_used_bytes: int | None = None,
    memory_total_bytes: int | None = None,
    temperature_c: float | None = None,
) -> None:
    if name and (not adapter.name or adapter.name.endswith(adapter.id) or adapter.name.endswith(adapter.id.upper())):
        adapter.name = name
    elif name and adapter.name.startswith(adapter.vendor) and adapter.name.endswith(("card0", "card1", "card2", "card3", "card4", "card5")):
        adapter.name = name
    if utilization_percent is not None and adapter.utilization_percent is None:
        adapter.utilization_percent = max(0.0, utilization_percent)
    if memory_used_bytes is not None and adapter.memory_used_bytes is None:
        adapter.memory_used_bytes = max(0, memory_used_bytes)
    if memory_total_bytes is not None and memory_total_bytes > 0 and adapter.memory_total_bytes is None:
        adapter.memory_total_bytes = max(0, memory_total_bytes)
    if temperature_c is not None and adapter.temperature_c is None:
        adapter.temperature_c = temperature_c
    if backend and not adapter.backend:
        adapter.backend = backend


def _match_gpu_adapter(
    adapters: list[_GpuAdapterState],
    *,
    vendor: str = "",
    adapter_id: str = "",
    name: str = "",
    adapter_index: int | None = None,
    windows_phys_index: int | None = None,
) -> _GpuAdapterState | None:
    if windows_phys_index is not None:
        phys_matches = [
            adapter
            for adapter in adapters
            if adapter.windows_phys_index == windows_phys_index and (not vendor or adapter.vendor == vendor)
        ]
        if len(phys_matches) == 1:
            return phys_matches[0]

    if adapter_index is not None:
        index_matches = [
            adapter
            for adapter in adapters
            if adapter.adapter_index == adapter_index and (not vendor or adapter.vendor == vendor)
        ]
        if len(index_matches) == 1:
            return index_matches[0]

    normalized_id = _normalize_gpu_adapter_id(adapter_id)
    if normalized_id:
        exact_matches = [adapter for adapter in adapters if _normalize_gpu_adapter_id(adapter.id) == normalized_id]
        if len(exact_matches) == 1:
            return exact_matches[0]

    candidate_pool = [adapter for adapter in adapters if not vendor or adapter.vendor == vendor]
    normalized_name = _normalize_gpu_name(name)
    if normalized_name:
        name_matches = [adapter for adapter in candidate_pool if _normalize_gpu_name(adapter.name) == normalized_name]
        if len(name_matches) == 1:
            return name_matches[0]
    if len(candidate_pool) == 1:
        return candidate_pool[0]
    if len(adapters) == 1 and (not vendor or adapters[0].vendor == vendor):
        return adapters[0]
    return None


def _normalize_gpu_adapter_id(value: str) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    match = re.search(r"([0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-9a-f])$", text, re.IGNORECASE)
    if match:
        return match.group(1).lower()
    return text


def _normalize_gpu_name(value: str) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _apply_nvidia_smi_metrics(adapters: list[_GpuAdapterState], *, platform_name: str | None = None) -> None:
    if not any(adapter.vendor == "NVIDIA" for adapter in adapters):
        return
    executable = shutil.which("nvidia-smi")
    if not executable:
        return
    completed = subprocess.run(
        [
            executable,
            "--query-gpu=pci.bus_id,name,utilization.gpu,memory.used,memory.total,temperature.gpu",
            "--format=csv,noheader,nounits",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=2.5,
        **_windows_hidden_run_kwargs(platform_name=platform_name),
    )
    if completed.returncode != 0:
        return
    for row in _parse_nvidia_smi_gpu_rows(completed.stdout or ""):
        adapter = _match_gpu_adapter(
            adapters,
            vendor="NVIDIA",
            adapter_id=str(row.get("id", "")),
            name=str(row.get("name", "")),
        )
        if adapter is None:
            continue
        _merge_gpu_metrics(
            adapter,
            backend="nvidia-smi",
            name=str(row.get("name", "")),
            utilization_percent=_coerce_optional_float(row.get("utilization_percent")),
            memory_used_bytes=_coerce_optional_int(row.get("memory_used_bytes")),
            memory_total_bytes=_coerce_optional_int(row.get("memory_total_bytes")),
            temperature_c=_coerce_optional_float(row.get("temperature_c")),
        )


def _parse_nvidia_smi_gpu_rows(raw_text: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for line in (raw_text or "").splitlines():
        if not line.strip():
            continue
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 6:
            continue
        rows.append(
            {
                "id": _normalize_gpu_adapter_id(parts[0]),
                "name": parts[1],
                "utilization_percent": _safe_float(parts[2]),
                "memory_used_bytes": _mebibytes_to_bytes(_safe_float(parts[3])),
                "memory_total_bytes": _mebibytes_to_bytes(_safe_float(parts[4])),
                "temperature_c": _coerce_optional_float(parts[5]),
            }
        )
    return rows


def _apply_linux_amd_sysfs_metrics(adapters: list[_GpuAdapterState]) -> None:
    for adapter in adapters:
        if adapter.vendor != "AMD" or adapter.sysfs_path is None:
            continue
        sysfs_path = adapter.sysfs_path
        _merge_gpu_metrics(
            adapter,
            backend="amdgpu-sysfs",
            name=_linux_gpu_name_from_sysfs(sysfs_path, vendor=adapter.vendor, fallback=adapter.name),
            utilization_percent=_coerce_optional_float(_read_int_file(sysfs_path / "gpu_busy_percent")),
            memory_used_bytes=_read_int_file(sysfs_path / "mem_info_vram_used"),
            memory_total_bytes=_read_int_file(sysfs_path / "mem_info_vram_total"),
            temperature_c=_linux_gpu_temperature_from_sysfs(sysfs_path),
        )


def _apply_linux_intel_sysfs_metrics(adapters: list[_GpuAdapterState]) -> None:
    for adapter in adapters:
        if adapter.vendor != "Intel" or adapter.sysfs_path is None:
            continue
        sysfs_path = adapter.sysfs_path
        temperature_c = _linux_gpu_temperature_from_sysfs(sysfs_path)
        _merge_gpu_metrics(
            adapter,
            backend="i915-sysfs" if temperature_c is not None else "",
            name=_linux_gpu_name_from_sysfs(sysfs_path, vendor=adapter.vendor, fallback=adapter.name),
            temperature_c=temperature_c,
        )


def _linux_gpu_temperature_from_sysfs(device_path: Path) -> float | None:
    hwmon_root = device_path / "hwmon"
    if not hwmon_root.exists():
        return None
    temperatures: list[float] = []
    for hwmon_path in sorted(hwmon_root.glob("hwmon*")):
        for temp_input in sorted(hwmon_path.glob("temp*_input")):
            raw_value = _read_int_file(temp_input)
            if raw_value is None:
                continue
            value_c = raw_value / 1000.0 if raw_value > 1000 else float(raw_value)
            if _is_plausible_temperature_c(value_c):
                temperatures.append(value_c)
    if not temperatures:
        return None
    return max(temperatures)


def _apply_amd_smi_metrics(adapters: list[_GpuAdapterState], *, platform_name: str | None = None) -> None:
    if not any(adapter.vendor == "AMD" for adapter in adapters):
        return
    executable = shutil.which("amd-smi")
    if not executable:
        return
    payload = _run_json_command(
        [executable, "monitor", "--json", "-u", "-m", "-t", "-i", "1"],
        timeout=4.0,
        platform_name=platform_name,
    )
    if payload is None:
        payload = _run_json_command([executable, "monitor", "--json"], timeout=4.0, platform_name=platform_name)
    if payload is None:
        return
    for row in _parse_amd_smi_payload(payload):
        adapter = _match_gpu_adapter(
            adapters,
            vendor="AMD",
            adapter_id=str(row.get("id", "")),
            name=str(row.get("name", "")),
        )
        if adapter is None:
            continue
        _merge_gpu_metrics(
            adapter,
            backend="amd-smi",
            name=str(row.get("name", "")),
            utilization_percent=_coerce_optional_float(row.get("utilization_percent")),
            memory_used_bytes=_coerce_optional_int(row.get("memory_used_bytes")),
            memory_total_bytes=_coerce_optional_int(row.get("memory_total_bytes")),
            temperature_c=_coerce_optional_float(row.get("temperature_c")),
        )


def _apply_rocm_smi_metrics(adapters: list[_GpuAdapterState], *, platform_name: str | None = None) -> None:
    if not any(adapter.vendor == "AMD" for adapter in adapters):
        return
    executable = shutil.which("rocm-smi")
    if not executable:
        return
    payload = _run_json_command(
        [
            executable,
            "--json",
            "--showproductname",
            "--showuse",
            "--showmeminfo",
            "vram",
            "--showtemp",
        ],
        timeout=4.0,
        platform_name=platform_name,
    )
    if payload is None:
        return
    for row in _parse_rocm_smi_payload(payload):
        adapter = _match_gpu_adapter(
            adapters,
            vendor="AMD",
            adapter_id=str(row.get("id", "")),
            name=str(row.get("name", "")),
        )
        if adapter is None:
            continue
        _merge_gpu_metrics(
            adapter,
            backend="rocm-smi",
            name=str(row.get("name", "")),
            utilization_percent=_coerce_optional_float(row.get("utilization_percent")),
            memory_used_bytes=_coerce_optional_int(row.get("memory_used_bytes")),
            memory_total_bytes=_coerce_optional_int(row.get("memory_total_bytes")),
            temperature_c=_coerce_optional_float(row.get("temperature_c")),
        )


def _parse_amd_smi_payload(payload: object) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    seen_keys: set[tuple[str, str, float | None, int | None, int | None, float | None]] = set()
    for entry in _collect_gpu_candidate_entries(payload):
        name = _extract_text_value(entry, ("name", "product", "asic", "model"))
        adapter_id = _extract_text_value(entry, ("bdf", "pci", "bus", "deviceid", "gpu_id"))
        utilization = _extract_float_value(entry, ("gpu_busy_percent", "gpu_usage", "utilization", "gfx_activity", "gpu use"))
        memory_used = _extract_int_value(entry, ("mem_info_vram_used", "vram_used", "used_vram", "memory_used"))
        memory_total = _extract_int_value(entry, ("mem_info_vram_total", "vram_total", "total_vram", "memory_total"))
        temperature = _extract_float_value(entry, ("temperature", "edge_temp", "junction_temp", "temp"))
        if not any(value is not None for value in (utilization, memory_used, memory_total, temperature)) and not name and not adapter_id:
            continue
        row_key = (_normalize_gpu_adapter_id(adapter_id), name, utilization, memory_used, memory_total, temperature)
        if row_key in seen_keys:
            continue
        seen_keys.add(row_key)
        rows.append(
            {
                "id": _normalize_gpu_adapter_id(adapter_id),
                "name": name,
                "utilization_percent": utilization,
                "memory_used_bytes": memory_used,
                "memory_total_bytes": memory_total,
                "temperature_c": temperature,
            }
        )
    return rows


def _parse_rocm_smi_payload(payload: object) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    seen_keys: set[tuple[str, str, float | None, int | None, int | None, float | None]] = set()
    for entry in _collect_gpu_candidate_entries(payload):
        name = _extract_text_value(entry, ("card series", "product_name", "name", "card model"))
        adapter_id = _extract_text_value(entry, ("bdf", "pci", "card bus", "deviceid"))
        utilization = _extract_float_value(entry, ("gpu use", "gpu_busy_percent", "utilization"))
        memory_used = _extract_int_value(entry, ("used_vram", "vram total used memory", "memory used"))
        memory_total = _extract_int_value(entry, ("total_vram", "vram total memory", "memory total"))
        temperature = _extract_float_value(entry, ("temperature", "temp"))
        if not any(value is not None for value in (utilization, memory_used, memory_total, temperature)) and not name and not adapter_id:
            continue
        row_key = (_normalize_gpu_adapter_id(adapter_id), name, utilization, memory_used, memory_total, temperature)
        if row_key in seen_keys:
            continue
        seen_keys.add(row_key)
        rows.append(
            {
                "id": _normalize_gpu_adapter_id(adapter_id),
                "name": name,
                "utilization_percent": utilization,
                "memory_used_bytes": memory_used,
                "memory_total_bytes": memory_total,
                "temperature_c": temperature,
            }
        )
    return rows


def _apply_intel_gpu_top_metrics(adapters: list[_GpuAdapterState], *, platform_name: str | None = None) -> None:
    intel_adapters = [adapter for adapter in adapters if adapter.vendor == "Intel"]
    if not intel_adapters:
        return
    executable = shutil.which("intel_gpu_top")
    if not executable:
        return
    if len(intel_adapters) == 1:
        payload = _run_json_command([executable, "-J", "-o", "-", "-s", "200"], timeout=3.0, platform_name=platform_name)
        if payload is None:
            return
        metrics = _parse_intel_gpu_top_payload(payload)
        if metrics is None:
            return
        _merge_gpu_metrics(
            intel_adapters[0],
            backend="intel_gpu_top",
            utilization_percent=_coerce_optional_float(metrics.get("utilization_percent")),
        )
        return

    for adapter in intel_adapters:
        if adapter.sysfs_path is None:
            continue
        card_name = adapter.sysfs_path.parent.name
        payload = _run_json_command(
            [executable, "-J", "-o", "-", "-s", "200", "-d", f"drm:/dev/dri/{card_name}"],
            timeout=3.0,
            platform_name=platform_name,
        )
        if payload is None:
            continue
        metrics = _parse_intel_gpu_top_payload(payload)
        if metrics is None:
            continue
        _merge_gpu_metrics(
            adapter,
            backend="intel_gpu_top",
            utilization_percent=_coerce_optional_float(metrics.get("utilization_percent")),
        )


def _parse_intel_gpu_top_payload(payload: object) -> dict[str, object] | None:
    busy_values: list[float] = []
    for key, value in _walk_json_scalars(payload):
        if "busy" not in key.casefold():
            continue
        numeric = _coerce_optional_float(value)
        if numeric is None:
            continue
        busy_values.append(max(0.0, min(100.0, numeric)))
    if not busy_values:
        return None
    return {"utilization_percent": max(busy_values)}


def _apply_linux_drm_fdinfo_metrics(
    adapters: list[_GpuAdapterState],
    *,
    previous_engine_totals: dict[str, tuple[float, int]],
    now: float | None = None,
    proc_root: str | Path = "/proc",
) -> None:
    intel_adapters = [adapter for adapter in adapters if adapter.vendor == "Intel"]
    if not intel_adapters:
        return
    current_time = time.monotonic() if now is None else now
    samples = _collect_linux_drm_fdinfo_samples(proc_root=proc_root)
    if not samples:
        return
    for adapter in intel_adapters:
        adapter_id = _normalize_drm_adapter_id(adapter.id)
        sample = samples.get(adapter_id)
        sample_totals_key = adapter_id
        if sample is None and len(intel_adapters) == 1:
            sample = samples.get(_UNKNOWN_INTEL_DRM_ADAPTER_ID)
        if sample is None:
            continue
        utilization_percent: float | None = None
        previous = previous_engine_totals.get(sample_totals_key)
        if previous is not None:
            previous_time, previous_engine_time_ns = previous
            elapsed = current_time - previous_time
            delta_ns = max(0, sample.engine_time_ns - previous_engine_time_ns)
            if elapsed > 0 and delta_ns > 0:
                utilization_percent = min(100.0, (delta_ns / (elapsed * 1_000_000_000.0)) * 100.0)
        previous_engine_totals[sample_totals_key] = (current_time, sample.engine_time_ns)
        if utilization_percent is None and sample.memory_used_bytes is None and sample.memory_total_bytes is None:
            continue
        _merge_gpu_metrics(
            adapter,
            backend="linux-drm-fdinfo",
            utilization_percent=utilization_percent,
            memory_used_bytes=sample.memory_used_bytes,
            memory_total_bytes=sample.memory_total_bytes,
        )


def _collect_linux_drm_fdinfo_samples(*, proc_root: str | Path = "/proc") -> dict[str, _LinuxDrmFdinfoSample]:
    root = Path(proc_root)
    client_samples: dict[tuple[str, str], _LinuxDrmFdinfoSample] = {}
    try:
        pid_dirs = sorted(root.glob("[0-9]*"))
    except Exception:
        return {}
    for pid_dir in pid_dirs:
        fdinfo_dir = pid_dir / "fdinfo"
        if not fdinfo_dir.is_dir():
            continue
        try:
            fdinfo_paths = sorted(fdinfo_dir.iterdir(), key=lambda path: path.name)
        except Exception:
            continue
        for fdinfo_path in fdinfo_paths:
            try:
                payload = fdinfo_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            sample = _parse_linux_drm_fdinfo_payload(payload, fallback_client_key=f"{pid_dir.name}:{fdinfo_path.name}")
            if sample is None:
                continue
            client_samples[(sample.adapter_id, sample.client_key)] = sample

    aggregated: dict[str, _LinuxDrmFdinfoSample] = {}
    for sample in client_samples.values():
        current = aggregated.get(sample.adapter_id)
        if current is None:
            aggregated[sample.adapter_id] = _LinuxDrmFdinfoSample(
                adapter_id=sample.adapter_id,
                client_key="",
                engine_time_ns=sample.engine_time_ns,
                memory_used_bytes=sample.memory_used_bytes,
                memory_total_bytes=sample.memory_total_bytes,
            )
            continue
        aggregated[sample.adapter_id] = _LinuxDrmFdinfoSample(
            adapter_id=sample.adapter_id,
            client_key="",
            engine_time_ns=current.engine_time_ns + sample.engine_time_ns,
            memory_used_bytes=_add_optional_ints(current.memory_used_bytes, sample.memory_used_bytes),
            memory_total_bytes=_add_optional_ints(current.memory_total_bytes, sample.memory_total_bytes),
        )
    return aggregated


def _parse_linux_drm_fdinfo_payload(
    payload: str,
    *,
    fallback_client_key: str = "",
) -> _LinuxDrmFdinfoSample | None:
    adapter_id = ""
    client_id = ""
    driver = ""
    engine_time_ns = 0
    memory_used_bytes: int | None = None
    memory_total_bytes: int | None = None
    for raw_line in payload.splitlines():
        if ":" not in raw_line:
            continue
        raw_key, raw_value = raw_line.split(":", 1)
        key = raw_key.strip().casefold()
        value = raw_value.strip()
        if key == "drm-driver":
            driver = value.casefold()
        elif key == "drm-pdev":
            adapter_id = _normalize_drm_adapter_id(value)
        elif key == "drm-client-id":
            client_id = value
        elif key.startswith("drm-engine-"):
            parsed = _parse_drm_duration_ns(value)
            if parsed is not None:
                engine_time_ns += parsed
        elif key.startswith("drm-resident-") or key.startswith("drm-memory-"):
            memory_used_bytes = _add_optional_ints(memory_used_bytes, _parse_drm_memory_bytes(value))
        elif key.startswith("drm-total-"):
            memory_total_bytes = _add_optional_ints(memory_total_bytes, _parse_drm_memory_bytes(value))
    if driver and driver not in {"i915", "xe"}:
        return None
    if not adapter_id:
        if driver in {"i915", "xe"}:
            adapter_id = _UNKNOWN_INTEL_DRM_ADAPTER_ID
        else:
            return None
    if engine_time_ns <= 0 and memory_used_bytes is None and memory_total_bytes is None:
        return None
    client_key = f"{adapter_id}:{client_id}" if client_id else fallback_client_key
    return _LinuxDrmFdinfoSample(
        adapter_id=adapter_id,
        client_key=client_key,
        engine_time_ns=engine_time_ns,
        memory_used_bytes=memory_used_bytes,
        memory_total_bytes=memory_total_bytes,
    )


def _normalize_drm_adapter_id(value: str) -> str:
    cleaned = str(value or "").strip().lower()
    if cleaned.startswith("pci:"):
        cleaned = cleaned[4:]
    return cleaned


def _parse_drm_duration_ns(value: str) -> int | None:
    match = re.search(r"([0-9]+)", str(value or ""))
    if match is None:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _parse_drm_memory_bytes(value: str) -> int | None:
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*([kmgt]?i?b|bytes?)?", str(value or ""), re.IGNORECASE)
    if match is None:
        return None
    try:
        amount = float(match.group(1))
    except ValueError:
        return None
    unit = (match.group(2) or "bytes").casefold()
    scale = 1
    if unit in {"kb", "kib"}:
        scale = 1024
    elif unit in {"mb", "mib"}:
        scale = 1024**2
    elif unit in {"gb", "gib"}:
        scale = 1024**3
    elif unit in {"tb", "tib"}:
        scale = 1024**4
    return max(0, int(amount * scale))


def _add_optional_ints(left: int | None, right: int | None) -> int | None:
    if left is None:
        return right
    if right is None:
        return left
    return left + right


def _apply_windows_gpu_counter_metrics(
    adapters: list[_GpuAdapterState],
    *,
    platform_name: str | None = None,
    payload: object | None = None,
) -> None:
    if not adapters:
        return
    counter_payload = payload if payload is not None else _probe_windows_gpu_counter_payload(platform_name=platform_name)
    if counter_payload is None:
        return
    rows = _parse_windows_gpu_counter_payload(counter_payload)
    if not rows:
        return
    assigned_adapter_ids: set[str] = set()
    for row in rows:
        phys_index = _coerce_optional_int(row.get("phys_index"))
        adapter = _match_gpu_adapter(
            adapters,
            windows_phys_index=phys_index,
        )
        if adapter is None and phys_index is None and len(adapters) == 1:
            adapter = adapters[0]
        if adapter is None:
            adapter = _select_windows_counter_fallback_adapter(
                adapters,
                assigned_adapter_ids=assigned_adapter_ids,
            )
        if adapter is None:
            continue
        if phys_index is not None and adapter.windows_phys_index is None:
            adapter.windows_phys_index = phys_index
        assigned_adapter_ids.add(adapter.id)
        _merge_gpu_metrics(
            adapter,
            backend="windows-counters",
            utilization_percent=_coerce_optional_float(row.get("utilization_percent")),
            memory_used_bytes=_coerce_optional_int(row.get("memory_used_bytes")),
        )


def _select_windows_counter_fallback_adapter(
    adapters: list[_GpuAdapterState],
    *,
    assigned_adapter_ids: set[str],
) -> _GpuAdapterState | None:
    unassigned = [adapter for adapter in adapters if adapter.id not in assigned_adapter_ids]
    if not unassigned:
        return None

    def fallback_key(adapter: _GpuAdapterState) -> tuple[int, int, int, int]:
        has_vendor_specific_backend = int(adapter.backend in {"nvidia-smi", "amd-smi", "rocm-smi"})
        vendor_priority = {"Intel": 0, "AMD": 1, "NVIDIA": 2}.get(adapter.vendor, 3)
        has_utilization = int(adapter.utilization_percent is not None)
        adapter_index = adapter.adapter_index if adapter.adapter_index is not None else 10_000
        return (has_vendor_specific_backend, vendor_priority, has_utilization, adapter_index)

    return min(unassigned, key=fallback_key)


def _parse_windows_gpu_counter_payload(payload: object) -> list[dict[str, object]]:
    entries_by_phys: dict[int | None, dict[str, object]] = {}
    engine_entries = _normalize_json_entries(payload.get("engine") if isinstance(payload, dict) else None)
    for entry in engine_entries:
        status = _coerce_optional_int(entry.get("status") if isinstance(entry, dict) else None)
        if status not in (None, 0):
            continue
        phys_index = _extract_windows_phys_index(entry)
        utilization = _extract_float_value(entry, ("value", "cookedvalue"))
        if utilization is None:
            continue
        bucket = entries_by_phys.setdefault(phys_index, {"phys_index": phys_index})
        current_value = _coerce_optional_float(bucket.get("utilization_percent"))
        bucket["utilization_percent"] = utilization if current_value is None else max(current_value, utilization)

    memory_entries = _normalize_json_entries(payload.get("memory") if isinstance(payload, dict) else None)
    for entry in memory_entries:
        status = _coerce_optional_int(entry.get("status") if isinstance(entry, dict) else None)
        if status not in (None, 0):
            continue
        phys_index = _extract_windows_phys_index(entry)
        memory_used = _extract_int_value(entry, ("value", "cookedvalue"))
        if memory_used is None:
            continue
        bucket = entries_by_phys.setdefault(phys_index, {"phys_index": phys_index})
        current_value = _coerce_optional_int(bucket.get("memory_used_bytes"))
        bucket["memory_used_bytes"] = memory_used if current_value is None else max(current_value, memory_used)

    return [entries_by_phys[key] for key in sorted(entries_by_phys, key=lambda item: (-1 if item is None else item))]


def _extract_windows_phys_index(payload: object) -> int | None:
    path_text = _extract_text_value(payload, ("instance", "path"))
    match = _WINDOWS_GPU_PHYS_INDEX_PATTERN.search(path_text)
    if match is None:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _apply_windows_hardware_monitor_gpu_metrics(
    adapters: list[_GpuAdapterState],
    *,
    platform_name: str | None = None,
    payload: object | None = None,
) -> None:
    if not adapters:
        return
    sensor_payload = payload if payload is not None else _probe_windows_gpu_sensor_payload(platform_name=platform_name)
    if sensor_payload is None:
        return
    for row in _parse_windows_gpu_sensor_payload(sensor_payload):
        adapter = _match_gpu_adapter(
            adapters,
            vendor=str(row.get("vendor", "")),
            name=str(row.get("name", "")),
        )
        if adapter is None:
            continue
        _merge_gpu_metrics(
            adapter,
            backend="hardware-monitor",
            utilization_percent=_coerce_optional_float(row.get("utilization_percent")),
            temperature_c=_coerce_optional_float(row.get("temperature_c")),
        )


def _parse_windows_gpu_sensor_payload(payload: object) -> list[dict[str, object]]:
    rows_by_vendor: dict[str, dict[str, object]] = {}
    for namespace_key in ("libre", "open"):
        entries = _normalize_json_entries(payload.get(namespace_key) if isinstance(payload, dict) else None)
        for entry in entries:
            sensor_text = " ".join(
                [
                    _extract_text_value(entry, ("name",)),
                    _extract_text_value(entry, ("identifier",)),
                ]
            ).strip()
            vendor = _gpu_vendor_from_text(sensor_text)
            row_key = vendor or "single"
            bucket = rows_by_vendor.setdefault(
                row_key,
                {
                    "vendor": vendor,
                    "name": _extract_text_value(entry, ("name",)),
                    "utilization_percent": None,
                    "temperature_c": None,
                },
            )
            sensor_type = _extract_text_value(entry, ("sensortype",)).casefold()
            value = _coerce_optional_float(entry.get("value") if isinstance(entry, dict) else None)
            if value is None:
                continue
            if sensor_type == "load" and "gpu" in sensor_text.casefold() and bucket.get("utilization_percent") is None:
                bucket["utilization_percent"] = value
            elif sensor_type == "temperature" and "gpu" in sensor_text.casefold():
                current_temp = _coerce_optional_float(bucket.get("temperature_c"))
                bucket["temperature_c"] = value if current_temp is None else max(current_temp, value)
    return list(rows_by_vendor.values())


def _apply_system_profiler_gpu_metrics(adapters: list[_GpuAdapterState]) -> None:
    for adapter in adapters:
        if not adapter.backend:
            adapter.backend = "system_profiler"


def _run_json_command(
    command: list[str],
    *,
    timeout: float,
    platform_name: str | None = None,
) -> object | None:
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            **_windows_hidden_run_kwargs(platform_name=platform_name),
        )
    except OSError:
        return None
    except subprocess.TimeoutExpired as exc:
        return _load_json_payload(_subprocess_output_text(getattr(exc, "stdout", None) or getattr(exc, "output", None)))
    if completed.returncode != 0:
        return None
    return _load_json_payload(completed.stdout or "")


def _subprocess_output_text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value or "")


def _load_json_payload(raw_text: str) -> object | None:
    text = (raw_text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    decoder = json.JSONDecoder()
    last_payload: object | None = None
    index = 0
    while index < len(text):
        while index < len(text) and (text[index].isspace() or text[index] in "[,]"):
            index += 1
        if index >= len(text):
            break
        try:
            payload, consumed = decoder.raw_decode(text, index)
        except ValueError:
            break
        last_payload = payload
        index = consumed
    return last_payload


def _run_windows_powershell_output(
    script: str,
    *,
    timeout: float,
    platform_name: str | None = None,
) -> str | None:
    if _platform_name(platform_name) != "windows":
        return None
    executable = _windows_powershell_executable(platform_name=platform_name)
    try:
        completed = subprocess.run(
            [
                executable,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=timeout,
            **_windows_hidden_run_kwargs(platform_name=platform_name),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout or ""


def _collect_gpu_candidate_entries(payload: object) -> list[dict[str, object]]:
    collected: list[dict[str, object]] = []
    seen: set[int] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            node_id = id(node)
            if node_id not in seen:
                seen.add(node_id)
                key_text = " ".join(str(key).casefold() for key in node)
                if any(token in key_text for token in ("gpu", "vram", "pci", "temp", "memory", "util")):
                    collected.append(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list | tuple):
            for item in node:
                walk(item)

    walk(payload)
    return collected


def _probe_windows_gpu_inventory_payload(*, platform_name: str | None = None) -> object | None:
    output = _run_windows_powershell_output(
        _windows_gpu_inventory_script(),
        timeout=_WINDOWS_GPU_INVENTORY_TIMEOUT_SECONDS,
        platform_name=platform_name,
    )
    if output is None:
        return None
    return _load_json_payload(output)


def _probe_windows_gpu_counter_payload(*, platform_name: str | None = None) -> object | None:
    output = _run_windows_powershell_output(
        _windows_gpu_counter_script(),
        timeout=_WINDOWS_GPU_COUNTER_TIMEOUT_SECONDS,
        platform_name=platform_name,
    )
    if output is None:
        return None
    return _load_json_payload(output)


def _probe_windows_gpu_sensor_payload(*, platform_name: str | None = None) -> object | None:
    output = _run_windows_powershell_output(
        _windows_gpu_sensor_probe_script(),
        timeout=_WINDOWS_GPU_SENSOR_TIMEOUT_SECONDS,
        platform_name=platform_name,
    )
    if output is None:
        return None
    return _load_json_payload(output)


def _windows_gpu_inventory_script() -> str:
    return """
$ErrorActionPreference = 'Stop'
try {
  @(Get-CimInstance -ClassName Win32_VideoController -ErrorAction Stop |
    ForEach-Object {
      [pscustomobject]@{
        name = [string]$_.Name
        caption = [string]$_.Caption
        pnpDeviceId = [string]$_.PNPDeviceID
        adapterCompatibility = [string]$_.AdapterCompatibility
        videoProcessor = [string]$_.VideoProcessor
        adapterRam = $_.AdapterRAM
      }
    }) | ConvertTo-Json -Compress -Depth 6
} catch {
  @() | ConvertTo-Json -Compress -Depth 6
}
""".strip()


def _windows_gpu_counter_script() -> str:
    return """
$ErrorActionPreference = 'Stop'
function Read-CounterSafe([string]$Path) {
  try {
    @((Get-Counter -Counter $Path -ErrorAction Stop).CounterSamples |
      ForEach-Object {
        [pscustomobject]@{
          path = [string]$_.Path
          instance = [string]$_.InstanceName
          value = $_.CookedValue
          status = [int]$_.Status
        }
      })
  } catch {
    @()
  }
}
$result = [pscustomobject]@{
  engine = @(Read-CounterSafe '\\GPU Engine(*)\\Utilization Percentage')
  memory = @(Read-CounterSafe '\\GPU Adapter Memory(*)\\Dedicated Usage')
}
$result | ConvertTo-Json -Compress -Depth 6
""".strip()


def _windows_gpu_sensor_probe_script() -> str:
    return """
$ErrorActionPreference = 'Stop'
function Get-GpuSensors([string]$Namespace) {
  try {
    @(Get-CimInstance -Namespace $Namespace -ClassName Sensor -ErrorAction Stop |
      Where-Object {
        ([string]$_.Identifier -match '/gpu') -or ([string]$_.Name -match 'GPU')
      } |
      ForEach-Object {
        [pscustomobject]@{
          name = [string]$_.Name
          identifier = [string]$_.Identifier
          sensorType = [string]$_.SensorType
          value = $_.Value
        }
      })
  } catch {
    @()
  }
}
$result = [pscustomobject]@{
  libre = @(Get-GpuSensors 'root/LibreHardwareMonitor')
  open = @(Get-GpuSensors 'root/OpenHardwareMonitor')
}
$result | ConvertTo-Json -Compress -Depth 6
""".strip()


def _normalized_path_for_match(path: Path) -> str:
    raw = str(path).replace("\\", "/").rstrip("/") or "/"
    if _platform_name() == "windows":
        return raw.casefold()
    return raw


def _platform_name(platform_name: str | None = None) -> str:
    return (platform_name or platform.system()).strip().lower()


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


def _windows_powershell_executable(*, platform_name: str | None = None) -> str:
    return _windows_system_executable(
        relative_paths=(
            "System32/WindowsPowerShell/v1.0/powershell.exe",
            "Sysnative/WindowsPowerShell/v1.0/powershell.exe",
        ),
        fallback_names=("powershell.exe", "powershell"),
        platform_name=platform_name,
    )


def _create_windows_cpu_sampler(*, platform_name: str | None = None) -> _WindowsCpuUtilitySampler | None:
    if _platform_name(platform_name) != "windows":
        return None
    if getattr(ctypes, "WinDLL", None) is None:
        return None
    try:
        return _WindowsCpuUtilitySampler()
    except Exception:
        return None


def _create_windows_gpu_counter_sampler(*, platform_name: str | None = None) -> _WindowsGpuCounterSampler | None:
    if _platform_name(platform_name) != "windows":
        return None
    if getattr(ctypes, "WinDLL", None) is None:
        return None
    try:
        return _WindowsGpuCounterSampler()
    except Exception:
        return None


def _windows_hidden_run_kwargs(*, platform_name: str | None = None) -> dict[str, object]:
    if _platform_name(platform_name) != "windows":
        return {}

    run_kwargs: dict[str, object] = {}
    creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if creationflags:
        run_kwargs["creationflags"] = creationflags

    startupinfo_cls = getattr(subprocess, "STARTUPINFO", None)
    if startupinfo_cls is None:
        return run_kwargs

    startupinfo = startupinfo_cls()
    startupinfo.dwFlags |= int(getattr(subprocess, "STARTF_USESHOWWINDOW", 0))
    startupinfo.wShowWindow = int(getattr(subprocess, "SW_HIDE", 0))
    run_kwargs["startupinfo"] = startupinfo
    return run_kwargs


def _probe_windows_cpu_temperature(*, platform_name: str | None = None) -> float | None:
    output = _run_windows_powershell_output(
        _windows_temperature_probe_script(),
        timeout=_WINDOWS_CPU_TEMPERATURE_TIMEOUT_SECONDS,
        platform_name=platform_name,
    )
    if output is None:
        return None
    return _parse_windows_temperature_probe_output(output)


def _windows_temperature_probe_script() -> str:
    return """
$ErrorActionPreference = 'Stop'
function Get-TemperatureSensors([string]$Namespace) {
  try {
    @(Get-CimInstance -Namespace $Namespace -ClassName Sensor -ErrorAction Stop |
      Where-Object { [string]$_.SensorType -eq 'Temperature' } |
      ForEach-Object {
        [pscustomobject]@{
          name = [string]$_.Name
          identifier = [string]$_.Identifier
          sensorType = [string]$_.SensorType
          value = $_.Value
        }
      })
  } catch {
    @()
  }
}
function Get-AcpiZones {
  try {
    @(Get-CimInstance -Namespace 'root/wmi' -ClassName 'MSAcpi_ThermalZoneTemperature' -ErrorAction Stop |
      ForEach-Object {
        [pscustomobject]@{
          name = [string]$_.InstanceName
          currentTemperature = $_.CurrentTemperature
        }
      })
  } catch {
    @()
  }
}
$result = [pscustomobject]@{
  libre = @(Get-TemperatureSensors 'root/LibreHardwareMonitor')
  open = @(Get-TemperatureSensors 'root/OpenHardwareMonitor')
  acpi = @(Get-AcpiZones)
}
$result | ConvertTo-Json -Compress -Depth 6
""".strip()


def _windows_disk_mapping_script() -> str:
    return """
$ErrorActionPreference = 'Stop'
$result = @()
try {
  @(Get-CimInstance -ClassName Win32_LogicalDisk -Filter "DriveType=3" -ErrorAction Stop) |
    ForEach-Object {
      $logical = [string]$_.DeviceID
      $partitions = @(Get-CimAssociatedInstance -InputObject $_ -Association Win32_LogicalDiskToPartition -ErrorAction SilentlyContinue)
      foreach ($partition in $partitions) {
        $disks = @(Get-CimAssociatedInstance -InputObject $partition -Association Win32_DiskDriveToDiskPartition -ErrorAction SilentlyContinue)
        foreach ($disk in $disks) {
          $result += [pscustomobject]@{
            logical = $logical
            diskDeviceId = [string]$disk.DeviceID
            diskIndex = $disk.Index
          }
        }
      }
    }
} catch {
  $result = @()
}
$result | ConvertTo-Json -Compress -Depth 6
""".strip()


def _parse_windows_temperature_probe_output(raw_text: str) -> float | None:
    if not raw_text.strip():
        return None
    try:
        payload = json.loads(raw_text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return _extract_windows_temperature_from_payload(payload)


def _extract_windows_temperature_from_payload(payload: object) -> float | None:
    if not isinstance(payload, dict):
        return None

    libre_temperature = _select_windows_hardware_monitor_temperature(payload.get("libre"))
    if libre_temperature is not None:
        return libre_temperature

    open_temperature = _select_windows_hardware_monitor_temperature(payload.get("open"))
    if open_temperature is not None:
        return open_temperature

    return _select_windows_acpi_temperature(payload.get("acpi"))


def _select_windows_hardware_monitor_temperature(entries: object) -> float | None:
    matches: list[float] = []
    for entry in _normalize_temperature_entries(entries):
        sensor_type = str(entry.get("sensorType") or entry.get("SensorType") or "").strip().lower()
        if sensor_type and sensor_type != "temperature":
            continue
        sensor_text = " ".join(
            [
                str(entry.get("name") or entry.get("Name") or ""),
                str(entry.get("identifier") or entry.get("Identifier") or ""),
            ]
        ).strip().lower()
        if not any(token in sensor_text for token in _WINDOWS_CPU_SENSOR_TOKENS):
            continue
        value = _coerce_optional_float(entry.get("value", entry.get("Value")))
        if value is None or not _is_plausible_temperature_c(value):
            continue
        matches.append(value)
    if not matches:
        return None
    return max(matches)


def _select_windows_acpi_temperature(entries: object) -> float | None:
    matches: list[float] = []
    for entry in _normalize_temperature_entries(entries):
        raw_value = _coerce_optional_float(entry.get("currentTemperature", entry.get("CurrentTemperature")))
        if raw_value is None:
            continue
        value = _tenths_kelvin_to_celsius(raw_value)
        if not _is_plausible_temperature_c(value):
            continue
        matches.append(value)
    if not matches:
        return None
    return max(matches)


def _normalize_temperature_entries(entries: object) -> list[dict[str, object]]:
    if isinstance(entries, dict):
        return [entries]
    if isinstance(entries, list | tuple):
        return [entry for entry in entries if isinstance(entry, dict)]
    return []


def _tenths_kelvin_to_celsius(value: float) -> float:
    return (value / 10.0) - 273.15


def _is_plausible_temperature_c(value: float) -> bool:
    return 1.0 <= value <= 130.0


_PDH_FMT_DOUBLE = 0x00000200
_PDH_MORE_DATA = 0x800007D2
_PDH_CSTATUS_VALID_DATA = 0x00000000
_PDH_CSTATUS_NEW_DATA = 0x00000001


class _PdhFmtCounterValueUnion(ctypes.Union):
    _fields_ = [
        ("longValue", wintypes.LONG),
        ("doubleValue", ctypes.c_double),
        ("largeValue", ctypes.c_longlong),
        ("ansiStringValue", ctypes.c_char_p),
        ("wideStringValue", wintypes.LPWSTR),
    ]


class _PdhFmtCounterValue(ctypes.Structure):
    _fields_ = [
        ("CStatus", wintypes.DWORD),
        ("value", _PdhFmtCounterValueUnion),
    ]


class _PdhFmtCounterValueItemW(ctypes.Structure):
    _fields_ = [
        ("szName", wintypes.LPWSTR),
        ("FmtValue", _PdhFmtCounterValue),
    ]


class _PdhCounterInfoW(ctypes.Structure):
    _fields_ = [
        ("dwLength", wintypes.DWORD),
        ("dwType", wintypes.DWORD),
        ("CVersion", wintypes.DWORD),
        ("CStatus", wintypes.DWORD),
        ("lScale", wintypes.LONG),
        ("lDefaultScale", wintypes.LONG),
        ("dwUserData", ctypes.c_size_t),
        ("dwQueryUserData", ctypes.c_size_t),
        ("szFullPath", wintypes.LPWSTR),
        ("szMachineName", wintypes.LPWSTR),
        ("szObjectName", wintypes.LPWSTR),
        ("szInstanceName", wintypes.LPWSTR),
        ("szParentInstance", wintypes.LPWSTR),
        ("dwInstanceIndex", wintypes.DWORD),
        ("szCounterName", wintypes.LPWSTR),
        ("szExplainText", wintypes.LPWSTR),
        ("DataBuffer", ctypes.c_size_t),
    ]


class _WindowsCpuUtilitySampler:
    _TOTAL_COUNTER_PATH = r"\Processor Information(_Total)\% Processor Utility"
    _WILDCARD_COUNTER_PATH = r"\Processor Information(*)\% Processor Utility"

    def __init__(self) -> None:
        win_dll = getattr(ctypes, "WinDLL", None)
        if win_dll is None:
            raise OSError("ctypes.WinDLL is unavailable.")

        self._pdh = win_dll("pdh", use_last_error=True)
        self._configure_pdh_api()
        self._query = wintypes.HANDLE()
        self._total_counter = wintypes.HANDLE()
        self._wildcard_counter = wintypes.HANDLE()
        self._per_core_counters: list[wintypes.HANDLE] = []
        self._open_query()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            return

    def close(self) -> None:
        query = getattr(self, "_query", None)
        if not query:
            return
        if int(query.value or 0) == 0:
            return
        self._pdh.PdhCloseQuery(query)
        self._query = wintypes.HANDLE()

    def collect(self) -> tuple[float, tuple[float, ...]]:
        self._check_status(self._pdh.PdhCollectQueryData(self._query), "PdhCollectQueryData")
        overall = self._read_counter_double(self._total_counter)
        entries = self._read_counter_array(self._wildcard_counter)
        filtered = [
            (name, value)
            for name, value in entries
            if name and name != "_Total"
        ]
        filtered.sort(key=lambda item: _cpu_instance_sort_key(item[0]))
        return overall, tuple(value for _name, value in filtered)

    def _configure_pdh_api(self) -> None:
        self._pdh.PdhOpenQueryW.argtypes = [wintypes.LPCWSTR, ctypes.c_size_t, ctypes.POINTER(wintypes.HANDLE)]
        self._pdh.PdhOpenQueryW.restype = wintypes.LONG
        self._pdh.PdhCloseQuery.argtypes = [wintypes.HANDLE]
        self._pdh.PdhCloseQuery.restype = wintypes.LONG
        self._pdh.PdhCollectQueryData.argtypes = [wintypes.HANDLE]
        self._pdh.PdhCollectQueryData.restype = wintypes.LONG
        self._pdh.PdhRemoveCounter.argtypes = [wintypes.HANDLE]
        self._pdh.PdhRemoveCounter.restype = wintypes.LONG
        self._pdh.PdhAddCounterW.argtypes = [
            wintypes.HANDLE,
            wintypes.LPCWSTR,
            ctypes.c_size_t,
            ctypes.POINTER(wintypes.HANDLE),
        ]
        self._pdh.PdhAddCounterW.restype = wintypes.LONG
        self._pdh.PdhAddEnglishCounterW.argtypes = [
            wintypes.HANDLE,
            wintypes.LPCWSTR,
            ctypes.c_size_t,
            ctypes.POINTER(wintypes.HANDLE),
        ]
        self._pdh.PdhAddEnglishCounterW.restype = wintypes.LONG
        self._pdh.PdhGetFormattedCounterValue.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(_PdhFmtCounterValue),
        ]
        self._pdh.PdhGetFormattedCounterValue.restype = wintypes.LONG
        self._pdh.PdhGetFormattedCounterArrayW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(_PdhFmtCounterValueItemW),
        ]
        self._pdh.PdhGetFormattedCounterArrayW.restype = wintypes.LONG
        self._pdh.PdhGetCounterInfoW.argtypes = [
            wintypes.HANDLE,
            wintypes.BOOL,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(_PdhCounterInfoW),
        ]
        self._pdh.PdhGetCounterInfoW.restype = wintypes.LONG
        self._pdh.PdhExpandWildCardPathW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.DWORD,
        ]
        self._pdh.PdhExpandWildCardPathW.restype = wintypes.LONG

    def _open_query(self) -> None:
        self._check_status(
            self._pdh.PdhOpenQueryW(None, 0, ctypes.byref(self._query)),
            "PdhOpenQueryW",
        )
        self._total_counter = self._add_english_counter(self._TOTAL_COUNTER_PATH)
        english_wildcard = self._add_english_counter(self._WILDCARD_COUNTER_PATH)
        localized_wildcard_path = self._counter_full_path(english_wildcard)
        concrete_paths = self._expand_wildcard_paths(localized_wildcard_path)
        self._wildcard_counter = self._add_counter(localized_wildcard_path)
        self._check_status(self._pdh.PdhRemoveCounter(english_wildcard), "PdhRemoveCounter")
        seen_paths: set[str] = set()
        for path in concrete_paths:
            instance_name = _extract_counter_instance_name(path)
            if not instance_name or instance_name == "_Total" or path in seen_paths:
                continue
            seen_paths.add(path)
            self._per_core_counters.append(self._add_counter(path))
        self._check_status(self._pdh.PdhCollectQueryData(self._query), "PdhCollectQueryData")

    def _add_counter(self, path: str) -> wintypes.HANDLE:
        counter = wintypes.HANDLE()
        self._check_status(
            self._pdh.PdhAddCounterW(self._query, path, 0, ctypes.byref(counter)),
            "PdhAddCounterW",
        )
        return counter

    def _add_english_counter(self, path: str) -> wintypes.HANDLE:
        counter = wintypes.HANDLE()
        self._check_status(
            self._pdh.PdhAddEnglishCounterW(self._query, path, 0, ctypes.byref(counter)),
            "PdhAddEnglishCounterW",
        )
        return counter

    def _counter_full_path(self, counter: wintypes.HANDLE) -> str:
        buffer_size = wintypes.DWORD(0)
        status = self._pdh.PdhGetCounterInfoW(counter, False, ctypes.byref(buffer_size), None)
        if _normalize_windows_status(status) != _PDH_MORE_DATA:
            self._check_status(status, "PdhGetCounterInfoW")
        buffer = ctypes.create_string_buffer(buffer_size.value)
        info = ctypes.cast(buffer, ctypes.POINTER(_PdhCounterInfoW))
        self._check_status(
            self._pdh.PdhGetCounterInfoW(counter, False, ctypes.byref(buffer_size), info),
            "PdhGetCounterInfoW",
        )
        path = str(info.contents.szFullPath or "").strip()
        if not path:
            raise RuntimeError("PDH returned an empty counter path.")
        return path

    def _expand_wildcard_paths(self, path: str) -> list[str]:
        buffer_size = wintypes.DWORD(0)
        status = self._pdh.PdhExpandWildCardPathW(None, path, None, ctypes.byref(buffer_size), 0)
        if _normalize_windows_status(status) != _PDH_MORE_DATA:
            self._check_status(status, "PdhExpandWildCardPathW")
        buffer = ctypes.create_unicode_buffer(buffer_size.value)
        self._check_status(
            self._pdh.PdhExpandWildCardPathW(None, path, buffer, ctypes.byref(buffer_size), 0),
            "PdhExpandWildCardPathW",
        )
        return [entry for entry in buffer[: buffer_size.value].split("\x00") if entry]

    def _read_counter_double(self, counter: wintypes.HANDLE) -> float:
        counter_type = wintypes.DWORD(0)
        value = _PdhFmtCounterValue()
        self._check_status(
            self._pdh.PdhGetFormattedCounterValue(
                counter,
                _PDH_FMT_DOUBLE,
                ctypes.byref(counter_type),
                ctypes.byref(value),
            ),
            "PdhGetFormattedCounterValue",
        )
        status = _normalize_windows_status(value.CStatus)
        if status not in {_PDH_CSTATUS_VALID_DATA, _PDH_CSTATUS_NEW_DATA}:
            raise RuntimeError(f"PDH counter value was not valid: 0x{status:08x}")
        return _safe_float(value.value.doubleValue)

    def _read_counter_array(self, counter: wintypes.HANDLE) -> list[tuple[str, float]]:
        buffer_size = wintypes.DWORD(0)
        item_count = wintypes.DWORD(0)
        status = self._pdh.PdhGetFormattedCounterArrayW(
            counter,
            _PDH_FMT_DOUBLE,
            ctypes.byref(buffer_size),
            ctypes.byref(item_count),
            None,
        )
        normalized_status = _normalize_windows_status(status)
        if normalized_status not in {_PDH_MORE_DATA, _PDH_CSTATUS_VALID_DATA}:
            self._check_status(status, "PdhGetFormattedCounterArrayW")
        if buffer_size.value == 0 or item_count.value == 0:
            return []

        buffer = ctypes.create_string_buffer(buffer_size.value)
        items = ctypes.cast(buffer, ctypes.POINTER(_PdhFmtCounterValueItemW))
        self._check_status(
            self._pdh.PdhGetFormattedCounterArrayW(
                counter,
                _PDH_FMT_DOUBLE,
                ctypes.byref(buffer_size),
                ctypes.byref(item_count),
                items,
            ),
            "PdhGetFormattedCounterArrayW",
        )

        values: list[tuple[str, float]] = []
        for index in range(item_count.value):
            item = items[index]
            value_status = _normalize_windows_status(item.FmtValue.CStatus)
            if value_status not in {_PDH_CSTATUS_VALID_DATA, _PDH_CSTATUS_NEW_DATA}:
                continue
            values.append((str(item.szName or "").strip(), _safe_float(item.FmtValue.value.doubleValue)))
        return values

    @staticmethod
    def _check_status(status: int, func_name: str) -> None:
        normalized = _normalize_windows_status(status)
        if normalized == 0:
            return
        raise RuntimeError(f"{func_name} failed with status 0x{normalized:08x}")


class _WindowsGpuCounterSampler:
    _ENGINE_COUNTER_PATH = r"\GPU Engine(*)\Utilization Percentage"
    _MEMORY_COUNTER_PATH = r"\GPU Adapter Memory(*)\Dedicated Usage"

    def __init__(self) -> None:
        win_dll = getattr(ctypes, "WinDLL", None)
        if win_dll is None:
            raise OSError("ctypes.WinDLL is unavailable.")

        self._pdh = win_dll("pdh", use_last_error=True)
        self._configure_pdh_api()
        self._query = wintypes.HANDLE()
        self._engine_counter = wintypes.HANDLE()
        self._memory_counter = wintypes.HANDLE()
        self._open_query()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            return

    def close(self) -> None:
        query = getattr(self, "_query", None)
        if not query:
            return
        if int(query.value or 0) == 0:
            return
        self._pdh.PdhCloseQuery(query)
        self._query = wintypes.HANDLE()

    def collect_payload(self) -> dict[str, object]:
        self._check_status(self._pdh.PdhCollectQueryData(self._query), "PdhCollectQueryData")
        engine_entries = [
            {
                "instance": name,
                "value": value,
                "status": 0,
            }
            for name, value in self._read_counter_array(self._engine_counter)
        ]
        memory_entries = [
            {
                "instance": name,
                "value": value,
                "status": 0,
            }
            for name, value in self._read_counter_array(self._memory_counter)
        ]
        return {
            "engine": engine_entries,
            "memory": memory_entries,
        }

    def _configure_pdh_api(self) -> None:
        self._pdh.PdhOpenQueryW.argtypes = [wintypes.LPCWSTR, ctypes.c_size_t, ctypes.POINTER(wintypes.HANDLE)]
        self._pdh.PdhOpenQueryW.restype = wintypes.LONG
        self._pdh.PdhCloseQuery.argtypes = [wintypes.HANDLE]
        self._pdh.PdhCloseQuery.restype = wintypes.LONG
        self._pdh.PdhCollectQueryData.argtypes = [wintypes.HANDLE]
        self._pdh.PdhCollectQueryData.restype = wintypes.LONG
        self._pdh.PdhAddEnglishCounterW.argtypes = [
            wintypes.HANDLE,
            wintypes.LPCWSTR,
            ctypes.c_size_t,
            ctypes.POINTER(wintypes.HANDLE),
        ]
        self._pdh.PdhAddEnglishCounterW.restype = wintypes.LONG
        self._pdh.PdhGetFormattedCounterArrayW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(_PdhFmtCounterValueItemW),
        ]
        self._pdh.PdhGetFormattedCounterArrayW.restype = wintypes.LONG

    def _open_query(self) -> None:
        self._check_status(
            self._pdh.PdhOpenQueryW(None, 0, ctypes.byref(self._query)),
            "PdhOpenQueryW",
        )
        self._engine_counter = self._add_english_counter(self._ENGINE_COUNTER_PATH)
        self._memory_counter = self._add_english_counter(self._MEMORY_COUNTER_PATH)
        self._check_status(self._pdh.PdhCollectQueryData(self._query), "PdhCollectQueryData")

    def _add_english_counter(self, path: str) -> wintypes.HANDLE:
        counter = wintypes.HANDLE()
        self._check_status(
            self._pdh.PdhAddEnglishCounterW(self._query, path, 0, ctypes.byref(counter)),
            "PdhAddEnglishCounterW",
        )
        return counter

    def _read_counter_array(self, counter: wintypes.HANDLE) -> list[tuple[str, float]]:
        buffer_size = wintypes.DWORD(0)
        item_count = wintypes.DWORD(0)
        status = self._pdh.PdhGetFormattedCounterArrayW(
            counter,
            _PDH_FMT_DOUBLE,
            ctypes.byref(buffer_size),
            ctypes.byref(item_count),
            None,
        )
        normalized_status = _normalize_windows_status(status)
        if normalized_status not in {_PDH_MORE_DATA, _PDH_CSTATUS_VALID_DATA, _PDH_CSTATUS_NEW_DATA}:
            self._check_status(status, "PdhGetFormattedCounterArrayW")
        if buffer_size.value == 0 or item_count.value == 0:
            return []

        buffer = ctypes.create_string_buffer(buffer_size.value)
        items = ctypes.cast(buffer, ctypes.POINTER(_PdhFmtCounterValueItemW))
        self._check_status(
            self._pdh.PdhGetFormattedCounterArrayW(
                counter,
                _PDH_FMT_DOUBLE,
                ctypes.byref(buffer_size),
                ctypes.byref(item_count),
                items,
            ),
            "PdhGetFormattedCounterArrayW",
        )

        values: list[tuple[str, float]] = []
        for index in range(item_count.value):
            item = items[index]
            value_status = _normalize_windows_status(item.FmtValue.CStatus)
            if value_status not in {_PDH_CSTATUS_VALID_DATA, _PDH_CSTATUS_NEW_DATA}:
                continue
            values.append((str(item.szName or "").strip(), _safe_float(item.FmtValue.value.doubleValue)))
        return values

    @staticmethod
    def _check_status(status: int, func_name: str) -> None:
        normalized = _normalize_windows_status(status)
        if normalized == 0:
            return
        raise RuntimeError(f"{func_name} failed with status 0x{normalized:08x}")


def _normalize_windows_status(status: int) -> int:
    return int(status) & 0xFFFFFFFF


def _extract_counter_instance_name(path: str) -> str:
    start = path.rfind("(")
    end = path.rfind(")\\")
    if start < 0 or end <= start:
        return ""
    return path[start + 1 : end].strip()


def _cpu_instance_sort_key(name: str) -> tuple[int, tuple[int, ...] | str]:
    parts = [part.strip() for part in name.split(",")]
    if parts and all(part.isdigit() for part in parts):
        return 0, tuple(int(part) for part in parts)
    return 1, name.casefold()


def _is_permission_error(exc: BaseException) -> bool:
    psutil = _psutil()
    access_denied = getattr(psutil, "AccessDenied", ())
    if isinstance(exc, access_denied):
        return True
    if isinstance(exc, PermissionError):
        return True
    if isinstance(exc, OSError):
        return exc.errno in {errno.EACCES, errno.EPERM}
    return False


def _wait_for_process_exit(pid: int, *, timeout: float) -> bool:
    psutil = _psutil()
    deadline = time.monotonic() + max(0.1, timeout)
    while time.monotonic() < deadline:
        if not psutil.pid_exists(pid):
            return True
        time.sleep(0.1)
    return not psutil.pid_exists(pid)


def _command_text(cmdline: object) -> str:
    if isinstance(cmdline, str):
        return cmdline.strip()
    if not isinstance(cmdline, list):
        return ""
    return " ".join(str(part) for part in cmdline if str(part)).strip()


def _call_or_default(callback, default):
    try:
        value = callback()
    except Exception:
        return default
    return default if value is None else value


def _coerce_optional_float(value: object) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _coerce_optional_int(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _safe_float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _mebibytes_to_bytes(value: float) -> int:
    return int(max(0.0, value) * 1024 * 1024)


def _psutil():
    try:
        import psutil
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("psutil is required for the Resource Monitor tool.") from exc
    return psutil
