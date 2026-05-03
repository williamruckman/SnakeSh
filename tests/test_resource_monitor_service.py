from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import snakesh.services.resource_monitor as resource_monitor
from snakesh.services.privilege_service import CommandResult
from snakesh.services.resource_monitor import (
    FilesystemEntry,
    GpuSample,
    ProcessCountsSnapshot,
    ResourceMonitorCancelledError,
    ResourceMonitorOverviewCollector,
    ResourceProcessCollector,
    build_elevated_process_action_command,
    collect_filesystem_entries,
    perform_process_action,
)


class _FakeAccessDenied(Exception):
    pass


class _FakeNoSuchProcess(Exception):
    pass


class _FakeZombieProcess(Exception):
    pass


class _FakeProcess:
    def __init__(
        self,
        pid: int,
        *,
        name: str,
        cpu_sequence: list[float],
        rss: int,
        threads: int,
        user: str,
        status: str,
        started_at: float,
        command: list[str],
        terminate_exception: Exception | None = None,
    ) -> None:
        self.pid = pid
        self._name = name
        self._cpu_sequence = list(cpu_sequence)
        self._rss = rss
        self._threads = threads
        self._user = user
        self._status = status
        self._started_at = started_at
        self._command = list(command)
        self._terminate_exception = terminate_exception

    def cpu_percent(self, interval=None):  # noqa: ANN001
        _ = interval
        if self._cpu_sequence:
            return self._cpu_sequence.pop(0)
        return 0.0

    def oneshot(self):
        return nullcontext()

    def name(self) -> str:
        return self._name

    def memory_info(self):
        return SimpleNamespace(rss=self._rss)

    def num_threads(self) -> int:
        return self._threads

    def username(self) -> str:
        return self._user

    def status(self) -> str:
        return self._status

    def create_time(self) -> float:
        return self._started_at

    def cmdline(self) -> list[str]:
        return list(self._command)

    def terminate(self) -> None:
        if self._terminate_exception is not None:
            raise self._terminate_exception

    def kill(self) -> None:
        if self._terminate_exception is not None:
            raise self._terminate_exception

    def wait(self, timeout=None):  # noqa: ANN001
        _ = timeout
        return 0


class ResourceMonitorServiceTests(unittest.TestCase):
    def test_preferred_interface_addresses_keeps_loopback_and_ipv6(self) -> None:
        addresses = [
            SimpleNamespace(family=resource_monitor.socket.AF_INET, address="127.0.0.1"),
            SimpleNamespace(family=resource_monitor.socket.AF_INET6, address="::1"),
        ]

        ipv4_address, ipv6_address = resource_monitor._preferred_interface_addresses(addresses)

        self.assertEqual(ipv4_address, "127.0.0.1")
        self.assertEqual(ipv6_address, "::1")

    def test_preferred_interface_addresses_prioritize_non_loopback_over_link_local(self) -> None:
        addresses = [
            SimpleNamespace(family=resource_monitor.socket.AF_INET, address="169.254.10.20"),
            SimpleNamespace(family=resource_monitor.socket.AF_INET, address="192.0.2.20"),
            SimpleNamespace(family=resource_monitor.socket.AF_INET6, address="fe80::1234%eth0"),
            SimpleNamespace(family=resource_monitor.socket.AF_INET6, address="2001:db8::20"),
        ]

        ipv4_address, ipv6_address = resource_monitor._preferred_interface_addresses(addresses)

        self.assertEqual(ipv4_address, "192.0.2.20")
        self.assertEqual(ipv6_address, "2001:db8::20")

    def test_build_gpu_sample_aggregates_multiple_mixed_vendor_adapters(self) -> None:
        adapters = [
            resource_monitor._GpuAdapterState(  # noqa: SLF001
                id="0000:01:00.0",
                vendor="NVIDIA",
                name="RTX 4090",
                backend="nvidia-smi",
                utilization_percent=70.0,
                memory_used_bytes=2_000,
                memory_total_bytes=8_000,
                temperature_c=61.0,
            ),
            resource_monitor._GpuAdapterState(  # noqa: SLF001
                id="0000:02:00.0",
                vendor="AMD",
                name="RX 7800 XT",
                backend="amdgpu-sysfs",
                utilization_percent=30.0,
                memory_used_bytes=1_000,
                memory_total_bytes=4_000,
                temperature_c=55.0,
            ),
            resource_monitor._GpuAdapterState(  # noqa: SLF001
                id="0000:00:02.0",
                vendor="Intel",
                name="Arc Test",
            ),
        ]

        sample = resource_monitor._build_gpu_sample(adapters)  # noqa: SLF001

        self.assertTrue(sample.available)
        self.assertTrue(sample.detected)
        self.assertEqual(sample.gpu_count, 3)
        self.assertEqual(sample.name, "3 GPUs")
        self.assertTrue(sample.has_utilization)
        self.assertTrue(sample.has_memory)
        self.assertTrue(sample.has_temperature)
        self.assertEqual(sample.utilization_percent, 50.0)
        self.assertEqual(sample.memory_used_bytes, 3_000)
        self.assertEqual(sample.memory_total_bytes, 12_000)
        self.assertAlmostEqual(sample.memory_percent or 0.0, 25.0)
        self.assertEqual(sample.temperature_c, 61.0)
        self.assertEqual(len(sample.adapters), 3)

    def test_parse_nvidia_smi_rows_returns_adapter_metrics(self) -> None:
        rows = resource_monitor._parse_nvidia_smi_gpu_rows(  # noqa: SLF001
            "00000000:01:00.0, NVIDIA RTX 4090, 72, 2048, 8192, 65\n"
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], "0000:01:00.0")
        self.assertEqual(rows[0]["name"], "NVIDIA RTX 4090")
        self.assertEqual(rows[0]["utilization_percent"], 72.0)
        self.assertEqual(rows[0]["memory_used_bytes"], 2_147_483_648)
        self.assertEqual(rows[0]["memory_total_bytes"], 8_589_934_592)
        self.assertEqual(rows[0]["temperature_c"], 65.0)

    def test_parse_amd_smi_payload_returns_metrics(self) -> None:
        payload = {
            "gpu_metrics": [
                {
                    "name": "AMD Radeon Test",
                    "bdf": "0000:03:00.0",
                    "gpu_busy_percent": 46,
                    "mem_info_vram_used": 1024,
                    "mem_info_vram_total": 4096,
                    "temperature": 58.0,
                }
            ]
        }

        rows = resource_monitor._parse_amd_smi_payload(payload)  # noqa: SLF001

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], "0000:03:00.0")
        self.assertEqual(rows[0]["name"], "AMD Radeon Test")
        self.assertEqual(rows[0]["utilization_percent"], 46.0)
        self.assertEqual(rows[0]["memory_used_bytes"], 1024)
        self.assertEqual(rows[0]["memory_total_bytes"], 4096)
        self.assertEqual(rows[0]["temperature_c"], 58.0)

    def test_parse_intel_gpu_top_payload_returns_busy_percent(self) -> None:
        payload = {
            "engines": {
                "Render/3D/0": {"busy": 37.5},
                "Copy/0": {"busy": 9.0},
            }
        }

        metrics = resource_monitor._parse_intel_gpu_top_payload(payload)  # noqa: SLF001

        self.assertEqual(metrics, {"utilization_percent": 37.5})

    def test_load_json_payload_returns_latest_complete_streamed_object(self) -> None:
        payload = (
            '[\n{"engines": {"Render/3D/0": {"busy": 12.5}}},\n'
            '{"engines": {"Render/3D/0": {"busy": 28.0}}},\n'
        )

        latest = resource_monitor._load_json_payload(payload)  # noqa: SLF001
        metrics = resource_monitor._parse_intel_gpu_top_payload(latest)  # noqa: SLF001

        self.assertEqual(metrics, {"utilization_percent": 28.0})

    def test_parse_linux_drm_fdinfo_payload_returns_intel_usage_stats(self) -> None:
        payload = "\n".join(
            [
                "drm-driver:\ti915",
                "drm-pdev:\t0000:00:02.0",
                "drm-client-id:\t7",
                "drm-engine-render:\t1000000000 ns",
                "drm-engine-copy:\t250000000 ns",
                "drm-resident-memory:\t4096 KiB",
                "drm-total-memory:\t8192 KiB",
            ]
        )

        sample = resource_monitor._parse_linux_drm_fdinfo_payload(payload, fallback_client_key="123:4")  # noqa: SLF001

        self.assertIsNotNone(sample)
        assert sample is not None
        self.assertEqual(sample.adapter_id, "0000:00:02.0")
        self.assertEqual(sample.engine_time_ns, 1_250_000_000)
        self.assertEqual(sample.memory_used_bytes, 4_194_304)
        self.assertEqual(sample.memory_total_bytes, 8_388_608)

    def test_apply_linux_drm_fdinfo_metrics_maps_missing_pdev_to_single_intel_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proc_root = Path(tmp)
            fdinfo_path = proc_root / "123" / "fdinfo" / "4"
            fdinfo_path.parent.mkdir(parents=True)
            fdinfo_path.write_text(
                "\n".join(
                    [
                        "drm-driver:\ti915",
                        "drm-client-id:\t7",
                        "drm-engine-render:\t1000000000 ns",
                        "drm-resident-memory:\t1024 KiB",
                        "drm-total-memory:\t4096 KiB",
                    ]
                ),
                encoding="utf-8",
            )
            adapters = [
                resource_monitor._GpuAdapterState(  # noqa: SLF001
                    id="0000:00:02.0",
                    vendor="Intel",
                    name="Intel UHD Graphics",
                )
            ]
            previous: dict[str, tuple[float, int]] = {}

            resource_monitor._apply_linux_drm_fdinfo_metrics(  # noqa: SLF001
                adapters,
                previous_engine_totals=previous,
                now=10.0,
                proc_root=proc_root,
            )
            fdinfo_path.write_text(
                "\n".join(
                    [
                        "drm-driver:\ti915",
                        "drm-client-id:\t7",
                        "drm-engine-render:\t1500000000 ns",
                        "drm-resident-memory:\t2048 KiB",
                        "drm-total-memory:\t4096 KiB",
                    ]
                ),
                encoding="utf-8",
            )
            adapters = [
                resource_monitor._GpuAdapterState(  # noqa: SLF001
                    id="0000:00:02.0",
                    vendor="Intel",
                    name="Intel UHD Graphics",
                )
            ]
            resource_monitor._apply_linux_drm_fdinfo_metrics(  # noqa: SLF001
                adapters,
                previous_engine_totals=previous,
                now=11.0,
                proc_root=proc_root,
            )

        self.assertEqual(adapters[0].backend, "linux-drm-fdinfo")
        self.assertEqual(adapters[0].utilization_percent, 50.0)
        self.assertEqual(adapters[0].memory_used_bytes, 2_097_152)
        self.assertEqual(adapters[0].memory_total_bytes, 4_194_304)

    def test_apply_linux_drm_fdinfo_metrics_calculates_rate_across_samples(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proc_root = Path(tmp)
            fdinfo_path = proc_root / "123" / "fdinfo" / "4"
            fdinfo_path.parent.mkdir(parents=True)
            fdinfo_path.write_text(
                "\n".join(
                    [
                        "drm-driver:\ti915",
                        "drm-pdev:\t0000:00:02.0",
                        "drm-client-id:\t7",
                        "drm-engine-render:\t1000000000 ns",
                        "drm-resident-memory:\t1024 KiB",
                        "drm-total-memory:\t4096 KiB",
                    ]
                ),
                encoding="utf-8",
            )
            adapters = [
                resource_monitor._GpuAdapterState(  # noqa: SLF001
                    id="0000:00:02.0",
                    vendor="Intel",
                    name="Intel UHD Graphics",
                )
            ]
            previous: dict[str, tuple[float, int]] = {}

            resource_monitor._apply_linux_drm_fdinfo_metrics(  # noqa: SLF001
                adapters,
                previous_engine_totals=previous,
                now=10.0,
                proc_root=proc_root,
            )
            fdinfo_path.write_text(
                "\n".join(
                    [
                        "drm-driver:\ti915",
                        "drm-pdev:\t0000:00:02.0",
                        "drm-client-id:\t7",
                        "drm-engine-render:\t1500000000 ns",
                        "drm-resident-memory:\t2048 KiB",
                        "drm-total-memory:\t4096 KiB",
                    ]
                ),
                encoding="utf-8",
            )
            adapters = [
                resource_monitor._GpuAdapterState(  # noqa: SLF001
                    id="0000:00:02.0",
                    vendor="Intel",
                    name="Intel UHD Graphics",
                )
            ]
            resource_monitor._apply_linux_drm_fdinfo_metrics(  # noqa: SLF001
                adapters,
                previous_engine_totals=previous,
                now=11.0,
                proc_root=proc_root,
            )

        self.assertEqual(adapters[0].backend, "linux-drm-fdinfo")
        self.assertEqual(adapters[0].memory_used_bytes, 2_097_152)
        self.assertEqual(adapters[0].memory_total_bytes, 4_194_304)
        self.assertEqual(adapters[0].utilization_percent, 50.0)

    def test_linux_fdinfo_fills_intel_memory_without_replacing_intel_gpu_top_usage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proc_root = Path(tmp)
            fdinfo_path = proc_root / "123" / "fdinfo" / "4"
            fdinfo_path.parent.mkdir(parents=True)
            fdinfo_path.write_text(
                "\n".join(
                    [
                        "drm-driver:\ti915",
                        "drm-pdev:\t0000:00:02.0",
                        "drm-client-id:\t7",
                        "drm-engine-render:\t1000000000 ns",
                        "drm-resident-memory:\t1024 KiB",
                        "drm-total-memory:\t4096 KiB",
                    ]
                ),
                encoding="utf-8",
            )
            adapters = [
                resource_monitor._GpuAdapterState(  # noqa: SLF001
                    id="0000:00:02.0",
                    vendor="Intel",
                    name="Intel UHD Graphics",
                )
            ]

            with (
                patch("snakesh.services.resource_monitor.shutil.which", return_value="intel_gpu_top"),
                patch(
                    "snakesh.services.resource_monitor._run_json_command",
                    return_value={"engines": {"Render/3D/0": {"busy": 37.5}}},
                ),
            ):
                resource_monitor._apply_intel_gpu_top_metrics(adapters, platform_name="linux")  # noqa: SLF001

            resource_monitor._apply_linux_drm_fdinfo_metrics(  # noqa: SLF001
                adapters,
                previous_engine_totals={},
                now=10.0,
                proc_root=proc_root,
            )

        self.assertEqual(adapters[0].backend, "intel_gpu_top")
        self.assertEqual(adapters[0].utilization_percent, 37.5)
        self.assertEqual(adapters[0].memory_used_bytes, 1_048_576)
        self.assertEqual(adapters[0].memory_total_bytes, 4_194_304)

    def test_linux_intel_previous_usage_survives_partial_fdinfo_fallback_with_nvidia_present(self) -> None:
        collector = ResourceMonitorOverviewCollector(platform_name="linux")
        collector._cached_gpu_sample = GpuSample(  # noqa: SLF001
            available=True,
            detected=True,
            name="2 GPUs",
            gpu_count=2,
            utilization_percent=34.0,
            has_utilization=True,
            adapters=[
                resource_monitor.GpuAdapterSample(
                    id="0000:00:02.0",
                    vendor="Intel",
                    name="Intel UHD Graphics",
                    adapter_index=0,
                    backend="intel_gpu_top",
                    utilization_percent=22.0,
                ),
                resource_monitor.GpuAdapterSample(
                    id="0000:01:00.0",
                    vendor="NVIDIA",
                    name="NVIDIA GeForce RTX 3050 Ti",
                    adapter_index=1,
                    backend="nvidia-smi",
                    utilization_percent=46.0,
                ),
            ],
        )
        current_gpu = GpuSample(
            available=True,
            detected=True,
            name="2 GPUs",
            gpu_count=2,
            utilization_percent=61.0,
            memory_used_bytes=2_097_152,
            memory_total_bytes=4_194_304,
            memory_percent=50.0,
            has_utilization=True,
            has_memory=True,
            adapters=[
                resource_monitor.GpuAdapterSample(
                    id="0000:00:02.0",
                    vendor="Intel",
                    name="Intel UHD Graphics",
                    adapter_index=0,
                    backend="linux-drm-fdinfo",
                    memory_used_bytes=2_097_152,
                    memory_total_bytes=4_194_304,
                ),
                resource_monitor.GpuAdapterSample(
                    id="0000:01:00.0",
                    vendor="NVIDIA",
                    name="NVIDIA GeForce RTX 3050 Ti",
                    adapter_index=1,
                    backend="nvidia-smi",
                    utilization_percent=61.0,
                ),
            ],
        )

        merged_gpu = collector._reuse_previous_linux_intel_gpu_metrics(current_gpu)  # noqa: SLF001

        self.assertEqual(merged_gpu.adapters[0].backend, "linux-drm-fdinfo")
        self.assertEqual(merged_gpu.adapters[0].utilization_percent, 22.0)
        self.assertEqual(merged_gpu.adapters[0].memory_used_bytes, 2_097_152)
        self.assertEqual(merged_gpu.adapters[1].utilization_percent, 61.0)

    def test_parse_windows_gpu_counter_payload_groups_by_adapter_index(self) -> None:
        payload = {
            "engine": [
                {"instance": "luid_0x0000_0x0000_phys_0_eng_0_engtype_3D", "value": 43.0},
                {"instance": "luid_0x0000_0x0000_phys_0_eng_1_engtype_Copy", "value": 11.0},
            ],
            "memory": [
                {"instance": "luid_0x0000_0x0000_phys_0", "value": 4096},
            ],
        }

        rows = resource_monitor._parse_windows_gpu_counter_payload(payload)  # noqa: SLF001

        self.assertEqual(rows, [{"phys_index": 0, "utilization_percent": 43.0, "memory_used_bytes": 4096}])

    def test_parse_windows_gpu_counter_payload_ignores_non_success_status_rows(self) -> None:
        payload = {
            "engine": [
                {"instance": "luid_0x0000_0x0000_phys_0_eng_0_engtype_3D", "value": 99.0, "status": 1},
                {"instance": "luid_0x0000_0x0000_phys_0_eng_1_engtype_Copy", "value": 43.0, "status": 0},
            ],
            "memory": [
                {"instance": "luid_0x0000_0x0000_phys_0", "value": 4096, "status": 1},
                {"instance": "luid_0x0000_0x0000_phys_0", "value": 2048, "status": 0},
            ],
        }

        rows = resource_monitor._parse_windows_gpu_counter_payload(payload)  # noqa: SLF001

        self.assertEqual(rows, [{"phys_index": 0, "utilization_percent": 43.0, "memory_used_bytes": 2048}])

    def test_windows_gpu_counter_sampler_is_preferred_over_powershell_probe(self) -> None:
        def cpu_percent(interval=None, percpu=False):  # noqa: ANN001, ARG001
            return [0.0, 0.0] if percpu else 0.0

        class _FakeGpuSampler:
            def __init__(self) -> None:
                self.calls = 0

            def collect_payload(self) -> dict[str, object]:
                self.calls += 1
                return {"engine": [{"instance": "luid_0x0000_0x0000_phys_0_eng_0_engtype_3D", "value": 14.0}]}

            def close(self) -> None:
                return None

        fake_psutil = SimpleNamespace(cpu_percent=cpu_percent)
        fake_sampler = _FakeGpuSampler()

        with (
            patch("snakesh.services.resource_monitor._psutil", return_value=fake_psutil),
            patch("snakesh.services.resource_monitor._create_windows_cpu_sampler", return_value=None),
            patch("snakesh.services.resource_monitor._create_windows_gpu_counter_sampler", return_value=fake_sampler),
            patch("snakesh.services.resource_monitor._probe_windows_gpu_counter_payload") as mock_probe,
        ):
            collector = ResourceMonitorOverviewCollector(platform_name="windows")
            first_counter = collector._cached_windows_gpu_counter_payload(now=10.0)  # noqa: SLF001
            second_counter = collector._cached_windows_gpu_counter_payload(now=11.0)  # noqa: SLF001
            third_counter = collector._cached_windows_gpu_counter_payload(now=15.5)  # noqa: SLF001

        self.assertEqual(first_counter, second_counter)
        self.assertEqual(first_counter, {"engine": [{"instance": "luid_0x0000_0x0000_phys_0_eng_0_engtype_3D", "value": 14.0}]})
        self.assertEqual(third_counter, first_counter)
        self.assertEqual(fake_sampler.calls, 2)
        mock_probe.assert_not_called()

    def test_collect_windows_gpu_inventory_prefers_adapter_name_vendor_over_conflicting_compatibility(self) -> None:
        payload = [
            {
                "name": "Intel(R) UHD Graphics",
                "adapterCompatibility": "Advanced Micro Devices, Inc.",
                "pnpDeviceId": "PCI\\VEN_8086&DEV_9A49",
            },
            {
                "name": "NVIDIA GeForce RTX 3050 Ti Laptop GPU",
                "adapterCompatibility": "NVIDIA",
                "pnpDeviceId": "PCI\\VEN_10DE&DEV_25A0",
            },
        ]

        with patch("snakesh.services.resource_monitor._probe_windows_gpu_inventory_payload", return_value=payload):
            adapters = resource_monitor._collect_windows_gpu_inventory(platform_name="windows")  # noqa: SLF001

        self.assertEqual([adapter.vendor for adapter in adapters], ["Intel", "NVIDIA"])
        self.assertEqual([adapter.adapter_index for adapter in adapters], [0, 1])
        self.assertEqual(adapters[0].name, "Intel(R) UHD Graphics")

    def test_windows_intel_nvidia_inventory_does_not_probe_amd_smi(self) -> None:
        def cpu_percent(interval=None, percpu=False):  # noqa: ANN001, ARG001
            return [0.0, 0.0] if percpu else 0.0

        fake_psutil = SimpleNamespace(cpu_percent=cpu_percent)
        inventory_payload = [
            {
                "name": "Intel(R) UHD Graphics",
                "adapterCompatibility": "Advanced Micro Devices, Inc.",
                "pnpDeviceId": "PCI\\VEN_8086&DEV_9A49",
            },
            {
                "name": "NVIDIA GeForce RTX 3050 Ti Laptop GPU",
                "adapterCompatibility": "NVIDIA",
                "pnpDeviceId": "PCI\\VEN_10DE&DEV_25A0",
            },
        ]
        completed = SimpleNamespace(
            returncode=0,
            stdout="00000000:01:00.0, NVIDIA GeForce RTX 3050 Ti Laptop GPU, 44, 1024, 4096, 67\n",
            stderr="",
        )
        which_calls: list[str] = []

        def which(name: str) -> str | None:
            which_calls.append(name)
            if name == "nvidia-smi":
                return "nvidia-smi"
            if name == "amd-smi":
                return "amd-smi"
            return None

        with (
            patch("snakesh.services.resource_monitor._psutil", return_value=fake_psutil),
            patch("snakesh.services.resource_monitor._create_windows_cpu_sampler", return_value=None),
            patch("snakesh.services.resource_monitor._create_windows_gpu_counter_sampler", return_value=None),
            patch("snakesh.services.resource_monitor._probe_windows_gpu_inventory_payload", return_value=inventory_payload),
            patch("snakesh.services.resource_monitor._probe_windows_gpu_counter_payload", return_value=None),
            patch("snakesh.services.resource_monitor._probe_windows_gpu_sensor_payload", return_value=None),
            patch("snakesh.services.resource_monitor.shutil.which", side_effect=which),
            patch("snakesh.services.resource_monitor.subprocess.run", return_value=completed),
        ):
            collector = ResourceMonitorOverviewCollector(platform_name="windows")
            gpu = collector._collect_gpu()

        self.assertEqual([adapter.vendor for adapter in gpu.adapters], ["Intel", "NVIDIA"])
        self.assertIn("nvidia-smi", which_calls)
        self.assertNotIn("amd-smi", which_calls)

    def test_apply_windows_gpu_counter_metrics_maps_rows_to_matching_adapter_index(self) -> None:
        adapters = [
            resource_monitor._GpuAdapterState(  # noqa: SLF001
                id="intel-0",
                vendor="Intel",
                name="Intel(R) UHD Graphics",
                adapter_index=0,
            ),
            resource_monitor._GpuAdapterState(  # noqa: SLF001
                id="nvidia-1",
                vendor="NVIDIA",
                name="NVIDIA GeForce RTX 3050 Ti Laptop GPU",
                adapter_index=1,
            ),
        ]
        payload = {
            "engine": [
                {"instance": "luid_0x0000_0x0000_phys_0_eng_0_engtype_3D", "value": 13.0},
                {"instance": "luid_0x0000_0x0000_phys_1_eng_0_engtype_3D", "value": 71.0},
            ],
            "memory": [
                {"instance": "luid_0x0000_0x0000_phys_0", "value": 1024},
                {"instance": "luid_0x0000_0x0000_phys_1", "value": 4096},
            ],
        }

        resource_monitor._apply_windows_gpu_counter_metrics(adapters, payload=payload)  # noqa: SLF001

        self.assertEqual(adapters[0].utilization_percent, 13.0)
        self.assertEqual(adapters[0].memory_used_bytes, 1024)
        self.assertEqual(adapters[0].backend, "windows-counters")
        self.assertEqual(adapters[1].utilization_percent, 71.0)
        self.assertEqual(adapters[1].memory_used_bytes, 4096)
        self.assertEqual(adapters[1].backend, "windows-counters")

    def test_apply_windows_gpu_counter_metrics_uses_persisted_phys_index_when_inventory_order_differs(self) -> None:
        adapters = [
            resource_monitor._GpuAdapterState(  # noqa: SLF001
                id="nvidia-1",
                vendor="NVIDIA",
                name="NVIDIA GeForce RTX 3050 Ti Laptop GPU",
                adapter_index=0,
                windows_phys_index=1,
            ),
            resource_monitor._GpuAdapterState(  # noqa: SLF001
                id="intel-0",
                vendor="Intel",
                name="Intel(R) UHD Graphics",
                adapter_index=1,
                windows_phys_index=0,
            ),
        ]
        payload = {
            "engine": [
                {"instance": "luid_0x0000_0x0000_phys_0_eng_0_engtype_3D", "value": 13.0},
                {"instance": "luid_0x0000_0x0000_phys_1_eng_0_engtype_3D", "value": 71.0},
            ],
        }

        resource_monitor._apply_windows_gpu_counter_metrics(adapters, payload=payload)  # noqa: SLF001

        self.assertEqual(adapters[0].utilization_percent, 71.0)
        self.assertEqual(adapters[1].utilization_percent, 13.0)

    def test_apply_windows_gpu_counter_metrics_prefers_vendor_aware_fallback_over_inventory_order(self) -> None:
        adapters = [
            resource_monitor._GpuAdapterState(  # noqa: SLF001
                id="nvidia-1",
                vendor="NVIDIA",
                name="NVIDIA GeForce RTX 3050 Ti Laptop GPU",
                adapter_index=0,
                backend="nvidia-smi",
                utilization_percent=0.0,
                memory_used_bytes=8_388_608,
                memory_total_bytes=4_294_967_296,
                temperature_c=60.0,
            ),
            resource_monitor._GpuAdapterState(  # noqa: SLF001
                id="intel-0",
                vendor="Intel",
                name="Intel(R) UHD Graphics",
                adapter_index=1,
            ),
        ]
        payload = {
            "engine": [
                {"instance": "luid_0x0000_0x0000_phys_0_eng_0_engtype_3D", "value": 8.0},
                {"instance": "luid_0x0000_0x0000_phys_1_eng_0_engtype_3D", "value": 0.0},
            ],
        }

        resource_monitor._apply_windows_gpu_counter_metrics(adapters, payload=payload)  # noqa: SLF001

        self.assertEqual(adapters[0].utilization_percent, 0.0)
        self.assertEqual(adapters[0].windows_phys_index, 1)
        self.assertEqual(adapters[1].utilization_percent, 8.0)
        self.assertEqual(adapters[1].windows_phys_index, 0)
        self.assertEqual(adapters[1].backend, "windows-counters")

    def test_collect_fast_uses_cached_slow_details_and_reports_stale_gpu_warning(self) -> None:
        fake_psutil = SimpleNamespace(
            cpu_percent=lambda interval=None, percpu=False: [10.0, 20.0] if percpu else 15.0,  # noqa: ARG005
            virtual_memory=lambda: SimpleNamespace(used=8_000, total=32_000, percent=25.0),
            swap_memory=lambda: SimpleNamespace(used=1_000, total=8_000, percent=12.5),
            sensors_temperatures=lambda fahrenheit=False: {},  # noqa: ARG005
        )
        cached_gpu = GpuSample(
            available=True,
            detected=True,
            name="Intel(R) UHD Graphics",
            gpu_count=1,
            utilization_percent=12.0,
            has_utilization=True,
            adapters=[
                resource_monitor.GpuAdapterSample(
                    id="gpu-intel-0",
                    vendor="Intel",
                    name="Intel(R) UHD Graphics",
                    adapter_index=0,
                    backend="windows-counters",
                    utilization_percent=12.0,
                )
            ],
        )
        with (
            patch("snakesh.services.resource_monitor._psutil", return_value=fake_psutil),
            patch("snakesh.services.resource_monitor._create_windows_cpu_sampler", return_value=None),
            patch(
                "snakesh.services.resource_monitor.collect_filesystem_entries",
                return_value=[
                    FilesystemEntry(
                        device="C:",
                        mountpoint="C:\\",
                        filesystem_type="ntfs",
                        used_bytes=400_000,
                        total_bytes=1_000_000,
                        free_bytes=600_000,
                        usage_percent=40.0,
                        is_home=True,
                    )
                ],
            ),
            patch("snakesh.services.resource_monitor._disk_totals", return_value=(1_000, 2_000)),
            patch("snakesh.services.resource_monitor._network_totals", return_value=(3_000, 5_000)),
            patch.object(ResourceMonitorOverviewCollector, "_collect_interfaces", return_value=[]),
            patch.object(ResourceMonitorOverviewCollector, "_collect_gpu", side_effect=[cached_gpu, GpuSample(message="GPU unavailable")]),
            patch(
                "snakesh.services.resource_monitor.time.monotonic",
                side_effect=[10.0, 10.1, 10.2, 12.0, 12.1, 12.2, 13.0],
            ),
        ):
            collector = ResourceMonitorOverviewCollector(platform_name="windows")
            collector.refresh_slow_details()
            collector.refresh_slow_details()
            snapshot = collector.collect_fast(process_count=5, thread_count=20)

        self.assertTrue(snapshot.gpu.detected)
        self.assertEqual(snapshot.gpu.utilization_percent, 12.0)
        self.assertIn("showing last successful sample", " | ".join(snapshot.errors).lower())

    def test_refresh_slow_details_honors_stop_callback_between_sections(self) -> None:
        fake_psutil = SimpleNamespace(
            cpu_percent=lambda interval=None, percpu=False: [0.0, 0.0] if percpu else 0.0,  # noqa: ARG005
        )

        stop_calls = {"count": 0}

        def stop_callback() -> bool:
            stop_calls["count"] += 1
            return stop_calls["count"] >= 2

        with (
            patch("snakesh.services.resource_monitor._psutil", return_value=fake_psutil),
            patch("snakesh.services.resource_monitor._create_windows_cpu_sampler", return_value=None),
            patch("snakesh.services.resource_monitor._create_windows_gpu_counter_sampler", return_value=None),
            patch(
                "snakesh.services.resource_monitor.collect_filesystem_entries",
                return_value=[
                    FilesystemEntry(
                        device="C:",
                        mountpoint="C:\\",
                        filesystem_type="ntfs",
                        used_bytes=1,
                        total_bytes=2,
                        free_bytes=1,
                        usage_percent=50.0,
                        is_home=True,
                    )
                ],
            ),
            patch.object(ResourceMonitorOverviewCollector, "_collect_interfaces", return_value=[]),
            patch.object(ResourceMonitorOverviewCollector, "_collect_gpu", return_value=GpuSample(message="GPU unavailable")),
        ):
            collector = ResourceMonitorOverviewCollector(platform_name="windows")
            with self.assertRaises(ResourceMonitorCancelledError):
                collector.refresh_slow_details(now=10.0, stop_callback=stop_callback)

    def test_refresh_slow_details_logs_filesystem_rows_and_mount_changes(self) -> None:
        fake_psutil = SimpleNamespace(
            cpu_percent=lambda interval=None, percpu=False: [0.0, 0.0] if percpu else 0.0,  # noqa: ARG005
        )
        filesystem_snapshots = [
            [
                FilesystemEntry(
                    device="C:",
                    mountpoint="C:\\",
                    filesystem_type="ntfs",
                    used_bytes=1,
                    total_bytes=2,
                    free_bytes=1,
                    usage_percent=50.0,
                    is_home=True,
                )
            ],
            [
                FilesystemEntry(
                    device="C:",
                    mountpoint="C:\\",
                    filesystem_type="ntfs",
                    used_bytes=1,
                    total_bytes=2,
                    free_bytes=1,
                    usage_percent=50.0,
                    is_home=True,
                ),
                FilesystemEntry(
                    device="D:",
                    mountpoint="D:\\",
                    filesystem_type="ntfs",
                    used_bytes=3,
                    total_bytes=6,
                    free_bytes=3,
                    usage_percent=50.0,
                ),
            ],
        ]

        with (
            patch("snakesh.services.resource_monitor._psutil", return_value=fake_psutil),
            patch("snakesh.services.resource_monitor._create_windows_cpu_sampler", return_value=None),
            patch("snakesh.services.resource_monitor._create_windows_gpu_counter_sampler", return_value=None),
            patch("snakesh.services.resource_monitor.collect_filesystem_entries", side_effect=filesystem_snapshots),
            patch.object(ResourceMonitorOverviewCollector, "_collect_interfaces", return_value=[]),
            patch.object(ResourceMonitorOverviewCollector, "_collect_gpu", return_value=GpuSample(message="GPU unavailable")),
            patch.object(resource_monitor._LOGGER, "debug") as mock_debug,
        ):
            collector = ResourceMonitorOverviewCollector(platform_name="windows")
            collector.refresh_slow_details(now=10.0)
            collector.refresh_slow_details(now=20.0)

        debug_messages = [str(call.args[0]) for call in mock_debug.call_args_list]
        self.assertTrue(any("filesystem collection completed" in message for message in debug_messages))
        self.assertTrue(any(call.args[-1] is True for call in mock_debug.call_args_list if call.args))

    def test_process_collector_honors_stop_callback(self) -> None:
        processes = {
            100: _FakeProcess(
                100,
                name="python",
                cpu_sequence=[0.0, 14.5],
                rss=400_000,
                threads=7,
                user="alice",
                status="running",
                started_at=1000.0,
                command=["python", "app.py"],
            ),
            200: _FakeProcess(
                200,
                name="sshd",
                cpu_sequence=[0.0, 3.5],
                rss=150_000,
                threads=3,
                user="root",
                status="sleeping",
                started_at=900.0,
                command=["sshd", "-D"],
            ),
        }
        fake_psutil = SimpleNamespace(
            pids=lambda: [100, 200],
            Process=lambda pid: processes[pid],
            NoSuchProcess=_FakeNoSuchProcess,
            ZombieProcess=_FakeZombieProcess,
            AccessDenied=_FakeAccessDenied,
        )
        stop_calls = {"count": 0}

        def stop_callback() -> bool:
            stop_calls["count"] += 1
            return stop_calls["count"] >= 2

        with patch("snakesh.services.resource_monitor._psutil", return_value=fake_psutil):
            collector = ResourceProcessCollector()
            with self.assertRaises(ResourceMonitorCancelledError):
                collector.collect(stop_callback=stop_callback)

    def test_process_collector_logs_positive_duration_and_slow_warning(self) -> None:
        processes = {
            100: _FakeProcess(
                100,
                name="python",
                cpu_sequence=[0.0, 14.5],
                rss=400_000,
                threads=7,
                user="alice",
                status="running",
                started_at=1_700_000_000.0,
                command=["python", "app.py"],
            ),
            200: _FakeProcess(
                200,
                name="sshd",
                cpu_sequence=[0.0, 3.5],
                rss=150_000,
                threads=3,
                user="root",
                status="sleeping",
                started_at=1_699_000_000.0,
                command=["sshd", "-D"],
            ),
        }
        fake_psutil = SimpleNamespace(
            pids=lambda: [100, 200],
            Process=lambda pid: processes[pid],
            NoSuchProcess=_FakeNoSuchProcess,
            ZombieProcess=_FakeZombieProcess,
            AccessDenied=_FakeAccessDenied,
        )

        with (
            patch("snakesh.services.resource_monitor._psutil", return_value=fake_psutil),
            patch("snakesh.services.resource_monitor._platform_name", return_value="windows"),
            patch("snakesh.services.resource_monitor.time.monotonic", side_effect=[10.0, 10.1, 14.6]),
            patch.object(resource_monitor._LOGGER, "debug") as mock_debug,
            patch.object(resource_monitor._LOGGER, "warning") as mock_warning,
        ):
            collector = ResourceProcessCollector()
            collector.collect()

        debug_call = next(
            call for call in mock_debug.call_args_list if "process inventory collected duration" in str(call.args[0])
        )
        self.assertGreater(debug_call.args[1], 0.0)
        self.assertGreater(mock_warning.call_count, 0)

    def test_windows_gpu_counter_backoff_transition_is_logged(self) -> None:
        def cpu_percent(interval=None, percpu=False):  # noqa: ANN001, ARG001
            return [0.0, 0.0] if percpu else 0.0

        fake_psutil = SimpleNamespace(cpu_percent=cpu_percent)

        with (
            patch("snakesh.services.resource_monitor._psutil", return_value=fake_psutil),
            patch("snakesh.services.resource_monitor._create_windows_cpu_sampler", return_value=None),
            patch("snakesh.services.resource_monitor._create_windows_gpu_counter_sampler", return_value=None),
            patch("snakesh.services.resource_monitor._probe_windows_gpu_counter_payload", return_value=None),
            patch.object(resource_monitor._LOGGER, "warning") as mock_warning,
        ):
            collector = ResourceMonitorOverviewCollector(platform_name="windows")
            collector._cached_windows_gpu_counter_payload(now=10.0)  # noqa: SLF001
            collector._cached_windows_gpu_counter_payload(now=16.0)  # noqa: SLF001

        warning_messages = [str(call.args[0]) for call in mock_warning.call_args_list]
        self.assertTrue(any("entered backoff" in message for message in warning_messages))

    def test_process_collector_can_return_lightweight_counts_without_full_inventory(self) -> None:
        process_calls: list[int] = []

        fake_psutil = SimpleNamespace(
            pids=lambda: [100, 200],
            Process=lambda pid: process_calls.append(pid) or _FakeProcess(  # noqa: ARG005
                pid,
                name=f"PID {pid}",
                cpu_sequence=[0.0],
                rss=10_000,
                threads=3 if pid == 100 else 5,
                user="tester",
                status="running",
                started_at=1_700_000_000.0,
                command=["python"],
            ),
        )

        with patch("snakesh.services.resource_monitor._psutil", return_value=fake_psutil):
            collector = ResourceProcessCollector()
            counts = collector.collect_counts()

        self.assertIsInstance(counts, ProcessCountsSnapshot)
        self.assertEqual(counts.process_count, 2)
        self.assertEqual(counts.thread_count, 0)
        self.assertEqual(process_calls, [])

    def test_process_collector_counts_reuse_last_full_thread_total(self) -> None:
        process_calls: list[int] = []

        fake_psutil = SimpleNamespace(
            pids=lambda: [100, 200],
            Process=lambda pid: process_calls.append(pid) or _FakeProcess(  # noqa: ARG005
                pid,
                name=f"PID {pid}",
                cpu_sequence=[0.0],
                rss=10_000,
                threads=3 if pid == 100 else 5,
                user="tester",
                status="running",
                started_at=1_700_000_000.0,
                command=["python"],
            ),
        )

        with patch("snakesh.services.resource_monitor._psutil", return_value=fake_psutil):
            collector = ResourceProcessCollector()
            snapshot = collector.collect()
            process_calls.clear()
            counts = collector.collect_counts()

        self.assertEqual(snapshot.total_threads, 8)
        self.assertEqual(counts.process_count, 2)
        self.assertEqual(counts.thread_count, 8)
        self.assertEqual(process_calls, [])

    def test_collect_filesystem_entries_marks_home_mount(self) -> None:
        fake_psutil = SimpleNamespace(
            disk_partitions=lambda all=False: [  # noqa: ARG005
                SimpleNamespace(device="/dev/root", mountpoint="/", fstype="ext4"),
                SimpleNamespace(device="/dev/home", mountpoint="/home", fstype="ext4"),
            ],
            disk_usage=lambda mount: SimpleNamespace(
                used=300 if mount == "/home" else 500,
                total=1000,
                free=700 if mount == "/home" else 500,
                percent=30 if mount == "/home" else 50,
            ),
        )

        with patch("snakesh.services.resource_monitor._psutil", return_value=fake_psutil):
            entries = collect_filesystem_entries("/home/tester/projects")

        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].mountpoint, "/home")
        self.assertTrue(entries[0].is_home)
        self.assertFalse(entries[1].is_home)

    def test_overview_collector_tracks_rates_and_since_open_totals(self) -> None:
        fake_psutil = SimpleNamespace(
            cpu_percent=lambda interval=None: 12.5 if interval is None else 12.5,  # noqa: ARG005
            virtual_memory=lambda: SimpleNamespace(used=8_000, total=32_000, percent=25.0),
            swap_memory=lambda: SimpleNamespace(used=1_000, total=8_000, percent=12.5),
        )

        with (
            patch("snakesh.services.resource_monitor._psutil", return_value=fake_psutil),
            patch(
                "snakesh.services.resource_monitor.collect_filesystem_entries",
                return_value=[
                    FilesystemEntry(
                        device="/dev/home",
                        mountpoint="/home",
                        filesystem_type="ext4",
                        used_bytes=400_000,
                        total_bytes=1_000_000,
                        free_bytes=600_000,
                        usage_percent=40.0,
                        is_home=True,
                    )
                ],
            ),
            patch("snakesh.services.resource_monitor._disk_totals", side_effect=[(1_000, 2_000), (1_600, 2_900)]),
            patch("snakesh.services.resource_monitor._network_totals", side_effect=[(3_000, 5_000), (3_800, 5_600)]),
            patch.object(ResourceMonitorOverviewCollector, "_collect_interfaces", return_value=[]),
            patch.object(ResourceMonitorOverviewCollector, "_collect_gpu", return_value=GpuSample(message="GPU unavailable")),
            patch(
                "snakesh.services.resource_monitor.time.monotonic",
                side_effect=[10.0, 10.1, 10.2, 12.0, 12.1, 12.2],
            ),
        ):
            collector = ResourceMonitorOverviewCollector()
            first = collector.collect(process_count=5, thread_count=20)
            second = collector.collect(process_count=6, thread_count=21)

        self.assertEqual(first.sample.disk_read_bytes_per_sec, 0.0)
        self.assertEqual(second.sample.disk_read_bytes_per_sec, 300.0)
        self.assertEqual(second.sample.disk_write_bytes_per_sec, 450.0)
        self.assertEqual(second.sample.network_recv_bytes_per_sec, 400.0)
        self.assertEqual(second.sample.network_sent_bytes_per_sec, 300.0)
        self.assertEqual(second.sample.disk_write_bytes_since_open, 900)
        self.assertEqual(second.sample.network_recv_bytes_since_open, 800)
        self.assertEqual(second.sample.process_count, 6)
        self.assertEqual(second.sample.thread_count, 21)

    def test_collect_fast_aggregates_network_rates_from_cached_interfaces(self) -> None:
        fake_psutil = SimpleNamespace(
            cpu_percent=lambda interval=None: 12.5 if interval is None else 12.5,  # noqa: ARG005
            virtual_memory=lambda: SimpleNamespace(used=8_000, total=32_000, percent=25.0),
            swap_memory=lambda: SimpleNamespace(used=1_000, total=8_000, percent=12.5),
        )

        with (
            patch("snakesh.services.resource_monitor._psutil", return_value=fake_psutil),
            patch("snakesh.services.resource_monitor._disk_totals", return_value=(1_000, 2_000)),
            patch("snakesh.services.resource_monitor._network_totals", return_value=(30_000, 50_000)),
        ):
            collector = ResourceMonitorOverviewCollector()
            collector._cached_filesystems = [  # noqa: SLF001
                FilesystemEntry(
                    device="/dev/home",
                    mountpoint="/home",
                    filesystem_type="ext4",
                    used_bytes=400_000,
                    total_bytes=1_000_000,
                    free_bytes=600_000,
                    usage_percent=40.0,
                    is_home=True,
                )
            ]
            collector._cached_interfaces = [  # noqa: SLF001
                resource_monitor.InterfaceBandwidthEntry(
                    name="eth0",
                    ipv4_address="192.0.2.20",
                    ipv6_address="2001:db8::20",
                    is_up=True,
                    speed_mbps=1000,
                    recv_bytes_per_sec=160_000.0,
                    sent_bytes_per_sec=40_000.0,
                    recv_bytes_total=2_000_000,
                    sent_bytes_total=500_000,
                ),
                resource_monitor.InterfaceBandwidthEntry(
                    name="wlan0",
                    ipv4_address="192.0.2.30",
                    ipv6_address="2001:db8::30",
                    is_up=True,
                    speed_mbps=866,
                    recv_bytes_per_sec=32_000.0,
                    sent_bytes_per_sec=8_000.0,
                    recv_bytes_total=600_000,
                    sent_bytes_total=200_000,
                ),
            ]

            snapshot = collector.collect_fast(process_count=5, thread_count=20, now=10.0)

        self.assertEqual(snapshot.sample.network_recv_bytes_per_sec, 192_000.0)
        self.assertEqual(snapshot.sample.network_sent_bytes_per_sec, 48_000.0)

    def test_collect_fast_refreshes_interface_rates_on_fast_cadence(self) -> None:
        counters_sequence = [
            {
                "eth0": SimpleNamespace(bytes_recv=1_000, bytes_sent=2_000),
            },
            {
                "eth0": SimpleNamespace(bytes_recv=3_000, bytes_sent=4_000),
            },
        ]
        stats_calls = {"count": 0}
        addrs_calls = {"count": 0}

        def cpu_percent(interval=None, percpu=False):  # noqa: ANN001, ARG001
            return [0.0, 0.0] if percpu else 0.0

        def net_io_counters(*, pernic=False):
            if pernic:
                return counters_sequence.pop(0)
            return SimpleNamespace(bytes_recv=10_000, bytes_sent=20_000)

        def net_if_stats():
            stats_calls["count"] += 1
            return {"eth0": SimpleNamespace(isup=True, speed=1000)}

        def net_if_addrs():
            addrs_calls["count"] += 1
            return {"eth0": [SimpleNamespace(family=resource_monitor.socket.AF_INET, address="192.0.2.20")]}

        fake_psutil = SimpleNamespace(
            cpu_percent=cpu_percent,
            virtual_memory=lambda: SimpleNamespace(used=8_000, total=32_000, percent=25.0),
            swap_memory=lambda: SimpleNamespace(used=1_000, total=8_000, percent=12.5),
            net_io_counters=net_io_counters,
            net_if_stats=net_if_stats,
            net_if_addrs=net_if_addrs,
        )

        with (
            patch("snakesh.services.resource_monitor._psutil", return_value=fake_psutil),
            patch("snakesh.services.resource_monitor._disk_totals", side_effect=[(1_000, 2_000), (1_400, 2_400)]),
            patch("snakesh.services.resource_monitor._network_totals", side_effect=[(10_000, 20_000), (12_000, 22_000)]),
        ):
            collector = ResourceMonitorOverviewCollector()
            collector._cached_filesystems = [  # noqa: SLF001
                FilesystemEntry(
                    device="/dev/home",
                    mountpoint="/home",
                    filesystem_type="ext4",
                    used_bytes=400_000,
                    total_bytes=1_000_000,
                    free_bytes=600_000,
                    usage_percent=40.0,
                    is_home=True,
                )
            ]
            first = collector.collect_fast(now=10.0)
            second = collector.collect_fast(now=11.0)

        self.assertEqual(len(first.interfaces), 1)
        self.assertEqual(first.interfaces[0].recv_bytes_per_sec, 0.0)
        self.assertEqual(second.interfaces[0].recv_bytes_per_sec, 2_000.0)
        self.assertEqual(second.interfaces[0].sent_bytes_per_sec, 2_000.0)
        self.assertEqual(second.sample.network_recv_bytes_per_sec, 2_000.0)
        self.assertEqual(second.sample.network_sent_bytes_per_sec, 2_000.0)
        self.assertEqual(stats_calls["count"], 2)
        self.assertEqual(addrs_calls["count"], 2)

    def test_collect_fast_includes_disconnected_adapters_with_zero_traffic(self) -> None:
        def cpu_percent(interval=None, percpu=False):  # noqa: ANN001, ARG001
            return [0.0, 0.0] if percpu else 0.0

        counters = {
            "eth0": SimpleNamespace(bytes_recv=1_000, bytes_sent=2_000),
            "wlan0": SimpleNamespace(bytes_recv=0, bytes_sent=0),
        }
        fake_psutil = SimpleNamespace(
            cpu_percent=cpu_percent,
            virtual_memory=lambda: SimpleNamespace(used=8_000, total=32_000, percent=25.0),
            swap_memory=lambda: SimpleNamespace(used=1_000, total=8_000, percent=12.5),
            net_io_counters=lambda pernic=False: counters if pernic else SimpleNamespace(bytes_recv=10_000, bytes_sent=20_000),  # noqa: ARG005
            net_if_stats=lambda: {
                "eth0": SimpleNamespace(isup=True, speed=1000),
                "wlan0": SimpleNamespace(isup=False, speed=866),
            },
            net_if_addrs=lambda: {
                "eth0": [SimpleNamespace(family=resource_monitor.socket.AF_INET, address="192.0.2.20")],
                "wlan0": [],
            },
        )

        with (
            patch("snakesh.services.resource_monitor._psutil", return_value=fake_psutil),
            patch("snakesh.services.resource_monitor._disk_totals", return_value=(1_000, 2_000)),
            patch("snakesh.services.resource_monitor._network_totals", return_value=(10_000, 20_000)),
        ):
            collector = ResourceMonitorOverviewCollector()
            collector._cached_filesystems = [  # noqa: SLF001
                FilesystemEntry(
                    device="/dev/home",
                    mountpoint="/home",
                    filesystem_type="ext4",
                    used_bytes=400_000,
                    total_bytes=1_000_000,
                    free_bytes=600_000,
                    usage_percent=40.0,
                    is_home=True,
                )
            ]
            snapshot = collector.collect_fast(now=10.0)

        self.assertEqual([entry.name for entry in snapshot.interfaces], ["eth0", "wlan0"])
        self.assertTrue(snapshot.interfaces[0].is_up)
        self.assertFalse(snapshot.interfaces[1].is_up)
        self.assertEqual(snapshot.interfaces[1].recv_bytes_per_sec, 0.0)
        self.assertEqual(snapshot.interfaces[1].sent_bytes_per_sec, 0.0)
        self.assertEqual(snapshot.interfaces[1].recv_bytes_total, 0)
        self.assertEqual(snapshot.interfaces[1].sent_bytes_total, 0)

    def test_collect_interfaces_emits_metadata_only_adapter_without_counters(self) -> None:
        def cpu_percent(interval=None, percpu=False):  # noqa: ANN001, ARG001
            return [0.0, 0.0] if percpu else 0.0

        fake_psutil = SimpleNamespace(
            cpu_percent=cpu_percent,
            net_io_counters=lambda pernic=False: {} if pernic else SimpleNamespace(),  # noqa: ARG005
            net_if_stats=lambda: {
                "lan1": SimpleNamespace(isup=False, speed=1000),
            },
            net_if_addrs=lambda: {
                "lan1": [SimpleNamespace(family=resource_monitor.socket.AF_INET, address="198.51.100.10")],
            },
        )

        with patch("snakesh.services.resource_monitor._psutil", return_value=fake_psutil):
            collector = ResourceMonitorOverviewCollector()
            entries = collector._collect_interfaces(now=10.0)  # noqa: SLF001

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].name, "lan1")
        self.assertFalse(entries[0].is_up)
        self.assertEqual(entries[0].ipv4_address, "198.51.100.10")
        self.assertEqual(entries[0].recv_bytes_total, 0)
        self.assertEqual(entries[0].sent_bytes_total, 0)

    def test_collect_fast_discovers_new_offline_adapter_on_next_fast_refresh(self) -> None:
        counters_sequence = [
            {
                "eth0": SimpleNamespace(bytes_recv=1_000, bytes_sent=2_000),
            },
            {
                "eth0": SimpleNamespace(bytes_recv=3_000, bytes_sent=4_000),
            },
        ]
        stats_sequence = [
            {"eth0": SimpleNamespace(isup=True, speed=1000)},
            {
                "eth0": SimpleNamespace(isup=True, speed=1000),
                "wlan0": SimpleNamespace(isup=False, speed=866),
            },
        ]
        addrs_sequence = [
            {"eth0": [SimpleNamespace(family=resource_monitor.socket.AF_INET, address="192.0.2.20")]},
            {
                "eth0": [SimpleNamespace(family=resource_monitor.socket.AF_INET, address="192.0.2.20")],
                "wlan0": [],
            },
        ]

        def cpu_percent(interval=None, percpu=False):  # noqa: ANN001, ARG001
            return [0.0, 0.0] if percpu else 0.0

        fake_psutil = SimpleNamespace(
            cpu_percent=cpu_percent,
            virtual_memory=lambda: SimpleNamespace(used=8_000, total=32_000, percent=25.0),
            swap_memory=lambda: SimpleNamespace(used=1_000, total=8_000, percent=12.5),
            net_io_counters=lambda pernic=False: counters_sequence.pop(0) if pernic else SimpleNamespace(bytes_recv=10_000, bytes_sent=20_000),  # noqa: ARG005
            net_if_stats=lambda: stats_sequence.pop(0),
            net_if_addrs=lambda: addrs_sequence.pop(0),
        )

        with (
            patch("snakesh.services.resource_monitor._psutil", return_value=fake_psutil),
            patch("snakesh.services.resource_monitor._disk_totals", side_effect=[(1_000, 2_000), (1_400, 2_400)]),
            patch("snakesh.services.resource_monitor._network_totals", side_effect=[(10_000, 20_000), (12_000, 22_000)]),
        ):
            collector = ResourceMonitorOverviewCollector()
            collector._cached_filesystems = [  # noqa: SLF001
                FilesystemEntry(
                    device="/dev/home",
                    mountpoint="/home",
                    filesystem_type="ext4",
                    used_bytes=400_000,
                    total_bytes=1_000_000,
                    free_bytes=600_000,
                    usage_percent=40.0,
                    is_home=True,
                )
            ]
            first = collector.collect_fast(now=10.0)
            second = collector.collect_fast(now=11.0)

        self.assertEqual([entry.name for entry in first.interfaces], ["eth0"])
        self.assertEqual([entry.name for entry in second.interfaces], ["eth0", "wlan0"])
        self.assertFalse(second.interfaces[1].is_up)
        self.assertEqual(second.interfaces[1].recv_bytes_per_sec, 0.0)
        self.assertEqual(second.interfaces[1].sent_bytes_per_sec, 0.0)

    def test_collect_fast_includes_offline_wireless_adapter_from_launch(self) -> None:
        def cpu_percent(interval=None, percpu=False):  # noqa: ANN001, ARG001
            return [0.0, 0.0] if percpu else 0.0

        fake_psutil = SimpleNamespace(
            cpu_percent=cpu_percent,
            virtual_memory=lambda: SimpleNamespace(used=8_000, total=32_000, percent=25.0),
            swap_memory=lambda: SimpleNamespace(used=1_000, total=8_000, percent=12.5),
            net_io_counters=lambda pernic=False: {} if pernic else SimpleNamespace(bytes_recv=10_000, bytes_sent=20_000),  # noqa: ARG005
            net_if_stats=lambda: {
                "wlan0": SimpleNamespace(isup=False, speed=866),
            },
            net_if_addrs=lambda: {
                "wlan0": [SimpleNamespace(family=resource_monitor.socket.AF_INET, address="192.0.2.55")],
            },
        )

        with (
            patch("snakesh.services.resource_monitor._psutil", return_value=fake_psutil),
            patch("snakesh.services.resource_monitor._disk_totals", return_value=(1_000, 2_000)),
            patch("snakesh.services.resource_monitor._network_totals", return_value=(10_000, 20_000)),
        ):
            collector = ResourceMonitorOverviewCollector()
            collector._cached_filesystems = [  # noqa: SLF001
                FilesystemEntry(
                    device="/dev/home",
                    mountpoint="/home",
                    filesystem_type="ext4",
                    used_bytes=400_000,
                    total_bytes=1_000_000,
                    free_bytes=600_000,
                    usage_percent=40.0,
                    is_home=True,
                )
            ]
            snapshot = collector.collect_fast(now=10.0)

        self.assertEqual(len(snapshot.interfaces), 1)
        self.assertEqual(snapshot.interfaces[0].name, "wlan0")
        self.assertFalse(snapshot.interfaces[0].is_up)
        self.assertEqual(snapshot.interfaces[0].ipv4_address, "192.0.2.55")
        self.assertEqual(snapshot.interfaces[0].recv_bytes_per_sec, 0.0)
        self.assertEqual(snapshot.interfaces[0].sent_bytes_per_sec, 0.0)

    def test_collect_interfaces_uses_interface_refresh_elapsed_instead_of_last_fast_sample(self) -> None:
        def cpu_percent(interval=None, percpu=False):  # noqa: ANN001, ARG001
            return [0.0, 0.0] if percpu else 0.0

        counters_sequence = [
            {
                "eth0": SimpleNamespace(bytes_recv=1_000, bytes_sent=2_000),
            },
            {
                "eth0": SimpleNamespace(bytes_recv=9_000, bytes_sent=7_000),
            },
        ]
        stats = {"eth0": SimpleNamespace(isup=True, speed=1000)}
        addresses = {"eth0": [SimpleNamespace(family=resource_monitor.socket.AF_INET, address="192.0.2.20")]}
        fake_psutil = SimpleNamespace(
            cpu_percent=cpu_percent,
            net_io_counters=lambda pernic=False: counters_sequence.pop(0) if pernic else SimpleNamespace(),  # noqa: ARG005
            net_if_stats=lambda: stats,
            net_if_addrs=lambda: addresses,
        )

        with patch("snakesh.services.resource_monitor._psutil", return_value=fake_psutil):
            collector = ResourceMonitorOverviewCollector()
            first = collector._collect_interfaces(now=10.0)  # noqa: SLF001
            collector._last_monotonic = 14.0  # noqa: SLF001
            second = collector._collect_interfaces(now=15.0)  # noqa: SLF001

        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 1)
        self.assertEqual(second[0].recv_bytes_per_sec, 1_600.0)
        self.assertEqual(second[0].sent_bytes_per_sec, 1_000.0)

    def test_overview_collector_tracks_per_device_disk_rates_and_reuses_duplicate_mount_devices(self) -> None:
        fake_psutil = SimpleNamespace(
            cpu_percent=lambda interval=None: 12.5 if interval is None else 12.5,  # noqa: ARG005
            virtual_memory=lambda: SimpleNamespace(used=8_000, total=32_000, percent=25.0),
            swap_memory=lambda: SimpleNamespace(used=1_000, total=8_000, percent=12.5),
        )

        filesystems = [
            FilesystemEntry(
                device="/dev/nvme0n1p5",
                mountpoint="/home",
                filesystem_type="ext4",
                used_bytes=400_000,
                total_bytes=1_000_000,
                free_bytes=600_000,
                usage_percent=40.0,
                is_home=True,
            ),
            FilesystemEntry(
                device="/dev/nvme0n1p5",
                mountpoint="/tmp",
                filesystem_type="ext4",
                used_bytes=400_000,
                total_bytes=1_000_000,
                free_bytes=600_000,
                usage_percent=40.0,
            ),
            FilesystemEntry(
                device="/dev/sdb1",
                mountpoint="/data",
                filesystem_type="ext4",
                used_bytes=500_000,
                total_bytes=2_000_000,
                free_bytes=1_500_000,
                usage_percent=25.0,
            ),
        ]

        with (
            patch("snakesh.services.resource_monitor._psutil", return_value=fake_psutil),
            patch("snakesh.services.resource_monitor.collect_filesystem_entries", return_value=filesystems),
            patch("snakesh.services.resource_monitor._disk_totals", side_effect=[(3_000, 6_000), (4_200, 8_100)]),
            patch(
                "snakesh.services.resource_monitor._disk_totals_per_device",
                side_effect=[
                    {
                        "nvme0n1p5": ("nvme0n1p5", (1_000, 2_000)),
                        "sdb1": ("sdb1", (2_000, 4_000)),
                        "loop0": ("loop0", (9_999, 9_999)),
                    },
                    {
                        "nvme0n1p5": ("nvme0n1p5", (1_600, 2_900)),
                        "sdb1": ("sdb1", (2_600, 5_200)),
                    },
                ],
            ),
            patch("snakesh.services.resource_monitor._network_totals", side_effect=[(3_000, 5_000), (3_800, 5_600)]),
            patch.object(ResourceMonitorOverviewCollector, "_collect_interfaces", return_value=[]),
            patch.object(ResourceMonitorOverviewCollector, "_collect_gpu", return_value=GpuSample(message="GPU unavailable")),
            patch(
                "snakesh.services.resource_monitor.time.monotonic",
                side_effect=[10.0, 10.1, 10.2, 12.0, 12.1, 12.2],
            ),
        ):
            collector = ResourceMonitorOverviewCollector()
            first = collector.collect(process_count=5, thread_count=20)
            second = collector.collect(process_count=6, thread_count=21)

        self.assertEqual(first.filesystems[0].disk_device_key, "nvme0n1p5")
        self.assertEqual(first.filesystems[1].disk_device_key, "nvme0n1p5")
        self.assertEqual(first.filesystems[2].disk_device_key, "sdb1")
        self.assertEqual([sample.key for sample in second.disk_devices], ["nvme0n1p5", "sdb1"])
        self.assertEqual(second.disk_devices[0].read_bytes_per_sec, 300.0)
        self.assertEqual(second.disk_devices[0].write_bytes_per_sec, 450.0)
        self.assertEqual(second.disk_devices[0].read_bytes_since_open, 600)
        self.assertEqual(second.disk_devices[0].write_bytes_since_open, 900)
        self.assertEqual(second.disk_devices[1].read_bytes_per_sec, 300.0)
        self.assertEqual(second.disk_devices[1].write_bytes_per_sec, 600.0)
        self.assertEqual(second.disk_devices[1].read_bytes_since_open, 600)
        self.assertEqual(second.disk_devices[1].write_bytes_since_open, 1_200)

    def test_overview_collector_leaves_unresolved_filesystems_visible_without_disk_device_samples(self) -> None:
        fake_psutil = SimpleNamespace(
            cpu_percent=lambda interval=None: 12.5 if interval is None else 12.5,  # noqa: ARG005
            virtual_memory=lambda: SimpleNamespace(used=8_000, total=32_000, percent=25.0),
            swap_memory=lambda: SimpleNamespace(used=1_000, total=8_000, percent=12.5),
        )

        with (
            patch("snakesh.services.resource_monitor._psutil", return_value=fake_psutil),
            patch(
                "snakesh.services.resource_monitor.collect_filesystem_entries",
                return_value=[
                    FilesystemEntry(
                        device="/dev/mapper/vg-home",
                        mountpoint="/home",
                        filesystem_type="ext4",
                        used_bytes=400_000,
                        total_bytes=1_000_000,
                        free_bytes=600_000,
                        usage_percent=40.0,
                        is_home=True,
                    )
                ],
            ),
            patch("snakesh.services.resource_monitor._disk_totals", return_value=(1_000, 2_000)),
            patch(
                "snakesh.services.resource_monitor._disk_totals_per_device",
                return_value={"sda1": ("sda1", (1_000, 2_000))},
            ),
            patch("snakesh.services.resource_monitor._network_totals", return_value=(3_000, 5_000)),
            patch.object(ResourceMonitorOverviewCollector, "_collect_interfaces", return_value=[]),
            patch.object(ResourceMonitorOverviewCollector, "_collect_gpu", return_value=GpuSample(message="GPU unavailable")),
            patch("snakesh.services.resource_monitor.time.monotonic", side_effect=[10.0, 10.1, 10.2]),
        ):
            collector = ResourceMonitorOverviewCollector()
            snapshot = collector.collect(process_count=5, thread_count=20)

        self.assertEqual(len(snapshot.filesystems), 1)
        self.assertEqual(snapshot.filesystems[0].disk_device_key, "vg-home")
        self.assertEqual(snapshot.disk_devices, [])

    def test_overview_collector_captures_per_core_cpu_and_temperature(self) -> None:
        def cpu_percent(interval=None, percpu=False):  # noqa: ANN001, ARG001
            if percpu:
                return [10.0, 20.0, 40.0, 50.0]
            return 30.0

        fake_psutil = SimpleNamespace(
            cpu_percent=cpu_percent,
            virtual_memory=lambda: SimpleNamespace(used=8_000, total=32_000, percent=25.0),
            swap_memory=lambda: SimpleNamespace(used=1_000, total=8_000, percent=12.5),
            sensors_temperatures=lambda fahrenheit=False: {  # noqa: ARG005
                "coretemp": [
                    SimpleNamespace(label="Package id 0", current=64.5),
                    SimpleNamespace(label="Core 0", current=59.0),
                ]
            },
        )

        with (
            patch("snakesh.services.resource_monitor._psutil", return_value=fake_psutil),
            patch(
                "snakesh.services.resource_monitor.collect_filesystem_entries",
                return_value=[
                    FilesystemEntry(
                        device="/dev/home",
                        mountpoint="/home",
                        filesystem_type="ext4",
                        used_bytes=400_000,
                        total_bytes=1_000_000,
                        free_bytes=600_000,
                        usage_percent=40.0,
                        is_home=True,
                    )
                ],
            ),
            patch("snakesh.services.resource_monitor._disk_totals", return_value=(1_000, 2_000)),
            patch("snakesh.services.resource_monitor._network_totals", return_value=(3_000, 5_000)),
            patch.object(ResourceMonitorOverviewCollector, "_collect_interfaces", return_value=[]),
            patch.object(ResourceMonitorOverviewCollector, "_collect_gpu", return_value=GpuSample(message="GPU unavailable")),
            patch("snakesh.services.resource_monitor.time.monotonic", return_value=10.0),
        ):
            collector = ResourceMonitorOverviewCollector()
            snapshot = collector.collect(process_count=5, thread_count=20)

        self.assertEqual(snapshot.sample.cpu_per_core_percentages, (10.0, 20.0, 40.0, 50.0))
        self.assertEqual(snapshot.sample.cpu_percent, 30.0)
        self.assertEqual(snapshot.sample.cpu_temperature_c, 64.5)

    def test_overview_collector_reports_gpu_unavailable_without_error(self) -> None:
        fake_psutil = SimpleNamespace(cpu_percent=lambda interval=None: 0.0)  # noqa: ARG005
        with (
            patch("snakesh.services.resource_monitor._psutil", return_value=fake_psutil),
            patch("snakesh.services.resource_monitor._collect_gpu_inventory", return_value=[]),
        ):
            collector = ResourceMonitorOverviewCollector(platform_name="linux")
            gpu = collector._collect_gpu()

        self.assertFalse(gpu.available)
        self.assertIn("GPU telemetry", gpu.message)

    def test_overview_collector_hides_windows_gpu_probe_console(self) -> None:
        class _FakeStartupInfo:
            def __init__(self) -> None:
                self.dwFlags = 0
                self.wShowWindow = 1

        fake_psutil = SimpleNamespace(cpu_percent=lambda interval=None: 0.0)  # noqa: ARG005
        completed = SimpleNamespace(
            returncode=0,
            stdout="00000000:01:00.0, NVIDIA RTX 4090, 72, 2048, 8192, 65\n",
            stderr="",
        )
        with (
            patch("snakesh.services.resource_monitor._psutil", return_value=fake_psutil),
            patch(
                "snakesh.services.resource_monitor._collect_gpu_inventory",
                return_value=[
                    resource_monitor._GpuAdapterState(  # noqa: SLF001
                        id="0000:01:00.0",
                        vendor="NVIDIA",
                        name="NVIDIA card0",
                    )
                ],
            ),
            patch("snakesh.services.resource_monitor.shutil.which", return_value="nvidia-smi"),
            patch.object(resource_monitor.subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True),
            patch.object(resource_monitor.subprocess, "STARTUPINFO", _FakeStartupInfo, create=True),
            patch.object(resource_monitor.subprocess, "STARTF_USESHOWWINDOW", 1, create=True),
            patch.object(resource_monitor.subprocess, "SW_HIDE", 0, create=True),
            patch("snakesh.services.resource_monitor.subprocess.run", return_value=completed) as mock_run,
        ):
            collector = ResourceMonitorOverviewCollector(platform_name="windows")
            gpu = collector._collect_gpu()

        self.assertTrue(gpu.available)
        self.assertEqual(gpu.name, "NVIDIA RTX 4090")
        self.assertEqual(gpu.gpu_count, 1)
        self.assertTrue(gpu.has_utilization)
        self.assertEqual(gpu.adapters[0].backend, "nvidia-smi")
        kwargs = mock_run.call_args.kwargs
        self.assertEqual(kwargs["creationflags"], 0x08000000)
        self.assertEqual(getattr(kwargs["startupinfo"], "dwFlags", 0), 1)
        self.assertEqual(getattr(kwargs["startupinfo"], "wShowWindow", 1), 0)

    def test_windows_overview_collector_uses_utility_sampler_when_available(self) -> None:
        class _FakeSampler:
            def collect(self) -> tuple[float, tuple[float, ...]]:
                return 132.0, (145.0, 118.5)

            def close(self) -> None:
                return None

        cpu_calls: list[tuple[object, bool]] = []

        def cpu_percent(interval=None, percpu=False):  # noqa: ANN001
            cpu_calls.append((interval, percpu))
            return [5.0, 6.0] if percpu else 4.0

        fake_psutil = SimpleNamespace(
            cpu_percent=cpu_percent,
            virtual_memory=lambda: SimpleNamespace(used=8_000, total=32_000, percent=25.0),
            swap_memory=lambda: SimpleNamespace(used=1_000, total=8_000, percent=12.5),
        )

        with (
            patch("snakesh.services.resource_monitor._psutil", return_value=fake_psutil),
            patch("snakesh.services.resource_monitor._create_windows_cpu_sampler", return_value=_FakeSampler()),
            patch("snakesh.services.resource_monitor._probe_windows_cpu_temperature", return_value=None),
            patch(
                "snakesh.services.resource_monitor.collect_filesystem_entries",
                return_value=[
                    FilesystemEntry(
                        device="C:",
                        mountpoint="C:\\",
                        filesystem_type="ntfs",
                        used_bytes=400_000,
                        total_bytes=1_000_000,
                        free_bytes=600_000,
                        usage_percent=40.0,
                        is_home=True,
                    )
                ],
            ),
            patch("snakesh.services.resource_monitor._disk_totals", return_value=(1_000, 2_000)),
            patch("snakesh.services.resource_monitor._network_totals", return_value=(3_000, 5_000)),
            patch.object(ResourceMonitorOverviewCollector, "_collect_interfaces", return_value=[]),
            patch.object(ResourceMonitorOverviewCollector, "_collect_gpu", return_value=GpuSample(message="GPU unavailable")),
            patch("snakesh.services.resource_monitor.time.monotonic", return_value=10.0),
        ):
            collector = ResourceMonitorOverviewCollector(platform_name="windows")
            snapshot = collector.collect(process_count=5, thread_count=20)

        self.assertEqual(snapshot.sample.cpu_percent, 132.0)
        self.assertEqual(snapshot.sample.cpu_per_core_percentages, (145.0, 118.5))
        self.assertEqual(snapshot.sample.logical_cpu_count, 2)
        self.assertEqual(cpu_calls, [(None, False), (None, True)])

    def test_windows_filesystem_to_disk_mapping_is_cached_outside_fast_collect(self) -> None:
        def cpu_percent(interval=None, percpu=False):  # noqa: ANN001, ARG001
            return [0.0, 0.0] if percpu else 0.0

        fake_psutil = SimpleNamespace(
            cpu_percent=cpu_percent,
            virtual_memory=lambda: SimpleNamespace(used=8_000, total=32_000, percent=25.0),
            swap_memory=lambda: SimpleNamespace(used=1_000, total=8_000, percent=12.5),
        )

        with (
            patch("snakesh.services.resource_monitor._psutil", return_value=fake_psutil),
            patch("snakesh.services.resource_monitor._create_windows_cpu_sampler", return_value=None),
            patch("snakesh.services.resource_monitor._probe_windows_cpu_temperature", return_value=None),
            patch(
                "snakesh.services.resource_monitor.collect_filesystem_entries",
                return_value=[
                    FilesystemEntry(
                        device="C:\\",
                        mountpoint="C:\\",
                        filesystem_type="ntfs",
                        used_bytes=400_000,
                        total_bytes=1_000_000,
                        free_bytes=600_000,
                        usage_percent=40.0,
                        is_home=True,
                    )
                ],
            ),
            patch(
                "snakesh.services.resource_monitor._collect_windows_volume_to_disk_map",
                return_value={"c:": "physicaldrive0"},
            ) as mock_mapping,
            patch("snakesh.services.resource_monitor._disk_totals", return_value=(1_000, 2_000)),
            patch(
                "snakesh.services.resource_monitor._disk_totals_per_device",
                return_value={"physicaldrive0": ("PhysicalDrive0", (1_000, 2_000))},
            ),
            patch("snakesh.services.resource_monitor._network_totals", return_value=(3_000, 5_000)),
            patch.object(ResourceMonitorOverviewCollector, "_collect_interfaces", return_value=[]),
            patch.object(ResourceMonitorOverviewCollector, "_collect_gpu", return_value=GpuSample(message="GPU unavailable")),
        ):
            collector = ResourceMonitorOverviewCollector(platform_name="windows")
            collector.refresh_slow_details(now=10.0)
            snapshot = collector.collect_fast(now=11.0)

        self.assertEqual(mock_mapping.call_count, 1)
        self.assertEqual(snapshot.filesystems[0].disk_device_key, "physicaldrive0")
        self.assertEqual(snapshot.disk_devices[0].key, "physicaldrive0")

    def test_windows_overview_collector_falls_back_to_psutil_cpu_when_sampler_fails(self) -> None:
        class _BrokenSampler:
            def collect(self) -> tuple[float, tuple[float, ...]]:
                raise RuntimeError("PDH failure")

            def close(self) -> None:
                return None

        def cpu_percent(interval=None, percpu=False):  # noqa: ANN001
            if percpu:
                return [10.0, 20.0]
            return 15.0

        fake_psutil = SimpleNamespace(
            cpu_percent=cpu_percent,
            virtual_memory=lambda: SimpleNamespace(used=8_000, total=32_000, percent=25.0),
            swap_memory=lambda: SimpleNamespace(used=1_000, total=8_000, percent=12.5),
        )

        with (
            patch("snakesh.services.resource_monitor._psutil", return_value=fake_psutil),
            patch("snakesh.services.resource_monitor._create_windows_cpu_sampler", return_value=_BrokenSampler()),
            patch("snakesh.services.resource_monitor._probe_windows_cpu_temperature", return_value=None),
            patch(
                "snakesh.services.resource_monitor.collect_filesystem_entries",
                return_value=[
                    FilesystemEntry(
                        device="C:",
                        mountpoint="C:\\",
                        filesystem_type="ntfs",
                        used_bytes=400_000,
                        total_bytes=1_000_000,
                        free_bytes=600_000,
                        usage_percent=40.0,
                        is_home=True,
                    )
                ],
            ),
            patch("snakesh.services.resource_monitor._disk_totals", return_value=(1_000, 2_000)),
            patch("snakesh.services.resource_monitor._network_totals", return_value=(3_000, 5_000)),
            patch.object(ResourceMonitorOverviewCollector, "_collect_interfaces", return_value=[]),
            patch.object(ResourceMonitorOverviewCollector, "_collect_gpu", return_value=GpuSample(message="GPU unavailable")),
            patch("snakesh.services.resource_monitor.time.monotonic", return_value=10.0),
        ):
            collector = ResourceMonitorOverviewCollector(platform_name="windows")
            snapshot = collector.collect(process_count=5, thread_count=20)

        self.assertEqual(snapshot.sample.cpu_percent, 15.0)
        self.assertEqual(snapshot.sample.cpu_per_core_percentages, (10.0, 20.0))

    def test_windows_cpu_temperature_probe_uses_cache(self) -> None:
        fake_psutil = SimpleNamespace(cpu_percent=lambda interval=None: 0.0)  # noqa: ARG005
        with (
            patch("snakesh.services.resource_monitor._psutil", return_value=fake_psutil),
            patch("snakesh.services.resource_monitor._create_windows_cpu_sampler", return_value=None),
            patch("snakesh.services.resource_monitor._probe_windows_cpu_temperature", return_value=61.5) as mock_probe,
        ):
            collector = ResourceMonitorOverviewCollector(platform_name="windows")
            first = collector._collect_cpu_temperature(fake_psutil, now=10.0)
            second = collector._collect_cpu_temperature(fake_psutil, now=12.0)
            third = collector._collect_cpu_temperature(fake_psutil, now=21.0)

        self.assertEqual(first, 61.5)
        self.assertEqual(second, 61.5)
        self.assertEqual(third, 61.5)
        self.assertEqual(mock_probe.call_count, 2)

    def test_windows_gpu_counter_and_sensor_probe_use_short_cache(self) -> None:
        def cpu_percent(interval=None, percpu=False):  # noqa: ANN001, ARG001
            return [0.0, 0.0] if percpu else 0.0

        fake_psutil = SimpleNamespace(cpu_percent=cpu_percent)
        counter_payload_a = {"engine": [{"instance": "luid_0x0000_0x0000_phys_0_eng_0_engtype_3D", "value": 14.0}]}
        counter_payload_b = {"engine": [{"instance": "luid_0x0000_0x0000_phys_0_eng_0_engtype_3D", "value": 37.0}]}
        sensor_payload_a = {"libre": [{"name": "GPU Core", "identifier": "/gpu-nvidia/0/temperature/0", "sensorType": "Temperature", "value": 61.0}]}
        sensor_payload_b = {"libre": [{"name": "GPU Core", "identifier": "/gpu-nvidia/0/temperature/0", "sensorType": "Temperature", "value": 66.0}]}

        with (
            patch("snakesh.services.resource_monitor._psutil", return_value=fake_psutil),
            patch("snakesh.services.resource_monitor._create_windows_cpu_sampler", return_value=None),
            patch("snakesh.services.resource_monitor._create_windows_gpu_counter_sampler", return_value=None),
            patch("snakesh.services.resource_monitor._probe_windows_gpu_counter_payload", side_effect=[counter_payload_a, counter_payload_b]) as mock_counter,
            patch("snakesh.services.resource_monitor._probe_windows_gpu_sensor_payload", side_effect=[sensor_payload_a, sensor_payload_b]) as mock_sensor,
        ):
            collector = ResourceMonitorOverviewCollector(platform_name="windows")
            first_counter = collector._cached_windows_gpu_counter_payload(now=10.0)  # noqa: SLF001
            second_counter = collector._cached_windows_gpu_counter_payload(now=11.0)  # noqa: SLF001
            third_counter = collector._cached_windows_gpu_counter_payload(now=15.5)  # noqa: SLF001

            first_sensor = collector._cached_windows_gpu_sensor_payload(now=20.0)  # noqa: SLF001
            second_sensor = collector._cached_windows_gpu_sensor_payload(now=21.0)  # noqa: SLF001
            third_sensor = collector._cached_windows_gpu_sensor_payload(now=31.0)  # noqa: SLF001

        self.assertEqual(first_counter, second_counter)
        self.assertEqual(first_counter, counter_payload_a)
        self.assertEqual(third_counter, counter_payload_b)
        self.assertEqual(mock_counter.call_count, 2)

        self.assertEqual(first_sensor, second_sensor)
        self.assertEqual(first_sensor, sensor_payload_a)
        self.assertEqual(third_sensor, sensor_payload_b)
        self.assertEqual(mock_sensor.call_count, 2)

    def test_windows_gpu_counter_probe_backs_off_after_repeated_failures(self) -> None:
        def cpu_percent(interval=None, percpu=False):  # noqa: ANN001, ARG001
            return [0.0, 0.0] if percpu else 0.0

        fake_psutil = SimpleNamespace(cpu_percent=cpu_percent)
        counter_payload = {"engine": [{"instance": "luid_0x0000_0x0000_phys_0_eng_0_engtype_3D", "value": 37.0}]}

        with (
            patch("snakesh.services.resource_monitor._psutil", return_value=fake_psutil),
            patch("snakesh.services.resource_monitor._create_windows_cpu_sampler", return_value=None),
            patch("snakesh.services.resource_monitor._create_windows_gpu_counter_sampler", return_value=None),
            patch("snakesh.services.resource_monitor._probe_windows_gpu_counter_payload", side_effect=[None, None, counter_payload]) as mock_counter,
        ):
            collector = ResourceMonitorOverviewCollector(platform_name="windows")
            first_counter = collector._cached_windows_gpu_counter_payload(now=10.0)  # noqa: SLF001
            second_counter = collector._cached_windows_gpu_counter_payload(now=16.0)  # noqa: SLF001
            third_counter = collector._cached_windows_gpu_counter_payload(now=20.0)  # noqa: SLF001
            fourth_counter = collector._cached_windows_gpu_counter_payload(now=48.0)  # noqa: SLF001

        self.assertIsNone(first_counter)
        self.assertIsNone(second_counter)
        self.assertIsNone(third_counter)
        self.assertEqual(fourth_counter, counter_payload)
        self.assertEqual(mock_counter.call_count, 3)

    def test_collect_fast_reports_gpu_collection_in_progress_before_first_slow_refresh(self) -> None:
        def cpu_percent(interval=None, percpu=False):  # noqa: ANN001, ARG001
            return [0.0, 0.0] if percpu else 0.0

        fake_psutil = SimpleNamespace(
            cpu_percent=cpu_percent,
            virtual_memory=lambda: SimpleNamespace(used=10_000, total=100_000, percent=10.0),
            swap_memory=lambda: SimpleNamespace(used=0, total=0, percent=0.0),
        )

        with (
            patch("snakesh.services.resource_monitor._psutil", return_value=fake_psutil),
            patch("snakesh.services.resource_monitor._create_windows_cpu_sampler", return_value=None),
            patch("snakesh.services.resource_monitor._disk_totals", return_value=(0, 0)),
            patch("snakesh.services.resource_monitor._network_totals", return_value=(0, 0)),
        ):
            collector = ResourceMonitorOverviewCollector(platform_name="windows")
            snapshot = collector.collect_fast()

        self.assertFalse(snapshot.gpu.detected)
        self.assertEqual(snapshot.gpu.message, "Collecting GPU telemetry...")

    def test_extract_windows_temperature_from_libre_hardware_monitor_payload(self) -> None:
        payload = {
            "libre": [
                {"name": "CPU Package", "identifier": "/intelcpu/0/temperature/0", "sensorType": "Temperature", "value": 72.5},
                {"name": "GPU Hot Spot", "identifier": "/gpu/0/temperature/0", "sensorType": "Temperature", "value": 81.0},
            ],
            "open": [],
            "acpi": [],
        }

        self.assertEqual(resource_monitor._extract_windows_temperature_from_payload(payload), 72.5)

    def test_extract_windows_temperature_from_open_hardware_monitor_payload(self) -> None:
        payload = {
            "libre": [],
            "open": [
                {"name": "CPU Core #1", "identifier": "/amdcpu/0/temperature/3", "sensorType": "Temperature", "value": 67.25},
                {"name": "GPU Core", "identifier": "/gpu-nvidia/0/temperature/0", "sensorType": "Temperature", "value": 54.0},
            ],
            "acpi": [],
        }

        self.assertEqual(resource_monitor._extract_windows_temperature_from_payload(payload), 67.25)

    def test_extract_windows_temperature_from_acpi_payload_discards_implausible_values(self) -> None:
        payload = {
            "libre": [],
            "open": [],
            "acpi": [
                {"name": "TZ00", "currentTemperature": 3002},
                {"name": "TZ01", "currentTemperature": 5000},
                {"name": "TZ02", "currentTemperature": 1000},
            ],
        }

        self.assertAlmostEqual(resource_monitor._extract_windows_temperature_from_payload(payload), 27.05, places=2)

    def test_probe_windows_cpu_temperature_uses_hidden_powershell_and_explicit_executable(self) -> None:
        class _FakeStartupInfo:
            def __init__(self) -> None:
                self.dwFlags = 0
                self.wShowWindow = 1

        completed = SimpleNamespace(
            returncode=0,
            stdout='{"libre":[{"name":"CPU Package","identifier":"/cpu/0","sensorType":"Temperature","value":63.0}],"open":[],"acpi":[]}',
            stderr="",
        )
        with (
            patch(
                "snakesh.services.resource_monitor._windows_powershell_executable",
                return_value=r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
            ),
            patch.object(resource_monitor.subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True),
            patch.object(resource_monitor.subprocess, "STARTUPINFO", _FakeStartupInfo, create=True),
            patch.object(resource_monitor.subprocess, "STARTF_USESHOWWINDOW", 1, create=True),
            patch.object(resource_monitor.subprocess, "SW_HIDE", 0, create=True),
            patch("snakesh.services.resource_monitor.subprocess.run", return_value=completed) as mock_run,
        ):
            value = resource_monitor._probe_windows_cpu_temperature(platform_name="windows")

        self.assertEqual(value, 63.0)
        command = mock_run.call_args.args[0]
        self.assertEqual(command[0], r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
        self.assertIn("-NoProfile", command)
        self.assertIn("-Command", command)
        kwargs = mock_run.call_args.kwargs
        self.assertEqual(kwargs["creationflags"], 0x08000000)
        self.assertEqual(getattr(kwargs["startupinfo"], "dwFlags", 0), 1)
        self.assertEqual(getattr(kwargs["startupinfo"], "wShowWindow", 1), 0)

    def test_process_collector_returns_sorted_entries_and_thread_totals(self) -> None:
        processes = {
            100: _FakeProcess(
                100,
                name="python",
                cpu_sequence=[0.0, 14.5],
                rss=400_000,
                threads=7,
                user="alice",
                status="running",
                started_at=1000.0,
                command=["python", "app.py"],
            ),
            200: _FakeProcess(
                200,
                name="sshd",
                cpu_sequence=[0.0, 3.5],
                rss=150_000,
                threads=3,
                user="root",
                status="sleeping",
                started_at=900.0,
                command=["sshd", "-D"],
            ),
        }
        fake_psutil = SimpleNamespace(
            pids=lambda: [200, 100],
            Process=lambda pid: processes[pid],
            NoSuchProcess=_FakeNoSuchProcess,
            ZombieProcess=_FakeZombieProcess,
            AccessDenied=_FakeAccessDenied,
        )

        with patch("snakesh.services.resource_monitor._psutil", return_value=fake_psutil):
            collector = ResourceProcessCollector()
            first = collector.collect()
            second = collector.collect()

        self.assertEqual(len(first.entries), 2)
        self.assertEqual(second.entries[0].pid, 100)
        self.assertEqual(second.entries[0].cpu_percent, 14.5)
        self.assertEqual(second.total_threads, 10)

    def test_perform_process_action_requires_elevation_on_access_denied(self) -> None:
        fake_process = _FakeProcess(
            4321,
            name="systemd",
            cpu_sequence=[0.0],
            rss=10,
            threads=1,
            user="root",
            status="sleeping",
            started_at=10.0,
            command=["systemd"],
            terminate_exception=_FakeAccessDenied("denied"),
        )
        fake_psutil = SimpleNamespace(
            Process=lambda pid: fake_process,
            NoSuchProcess=_FakeNoSuchProcess,
            AccessDenied=_FakeAccessDenied,
        )

        with patch("snakesh.services.resource_monitor._psutil", return_value=fake_psutil):
            result = perform_process_action(4321, allow_elevation=False)

        self.assertFalse(result.success)
        self.assertTrue(result.requires_elevation)
        self.assertIn("Administrative privileges", result.message)

    def test_perform_process_action_uses_elevated_fallback_when_allowed(self) -> None:
        fake_process = _FakeProcess(
            4321,
            name="systemd",
            cpu_sequence=[0.0],
            rss=10,
            threads=1,
            user="root",
            status="sleeping",
            started_at=10.0,
            command=["systemd"],
            terminate_exception=_FakeAccessDenied("denied"),
        )
        fake_psutil = SimpleNamespace(
            Process=lambda pid: fake_process,
            NoSuchProcess=_FakeNoSuchProcess,
            AccessDenied=_FakeAccessDenied,
            pid_exists=lambda pid: False,
        )

        with (
            patch("snakesh.services.resource_monitor._psutil", return_value=fake_psutil),
            patch(
                "snakesh.services.resource_monitor.run_command",
                return_value=CommandResult(success=True, message="ok", elevated=True),
            ) as mock_run,
            patch("snakesh.services.resource_monitor._wait_for_process_exit", return_value=True),
        ):
            result = perform_process_action(4321, allow_elevation=True, platform_name="linux")

        self.assertTrue(result.success)
        self.assertTrue(result.elevated)
        self.assertEqual(mock_run.call_args.args[0], ["/bin/kill", "-TERM", "4321"])
        self.assertTrue(mock_run.call_args.kwargs["require_elevation"])

    def test_build_elevated_process_action_command_uses_platform_specific_tools(self) -> None:
        self.assertEqual(
            build_elevated_process_action_command(321, force=False, platform_name="windows"),
            ["taskkill", "/PID", "321", "/T"],
        )
        self.assertEqual(
            build_elevated_process_action_command(321, force=True, platform_name="windows"),
            ["taskkill", "/PID", "321", "/T", "/F"],
        )
        self.assertEqual(
            build_elevated_process_action_command(321, force=False, platform_name="linux"),
            ["/bin/kill", "-TERM", "321"],
        )
        self.assertEqual(
            build_elevated_process_action_command(321, force=True, platform_name="darwin"),
            ["/bin/kill", "-KILL", "321"],
        )


if __name__ == "__main__":
    unittest.main()
