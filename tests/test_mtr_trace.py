from __future__ import annotations

import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch

from snakesh.services import mtr_trace
from snakesh.services.mtr_trace import (
    MTR_PROTOCOL_AUTO,
    MTR_PROTOCOL_ICMP,
    MTR_PROTOCOL_UDP,
    MTRHopSnapshot,
    MTRProbeSample,
    MTRTraceRequest,
    MTRTraceSnapshot,
    build_trace_cycle_command,
    build_trace_cycle_commands,
    format_mtr_samples_csv,
    format_mtr_report,
    launch_mtr_helper,
    launch_mtr_helper_elevated,
    mtr_helper_session_paths,
    needs_mtr_helper_elevation,
    read_mtr_probe_samples,
    read_mtr_snapshot,
    run_mtr_helper,
    write_mtr_config,
)


def _ipv4_header(src: str, dst: str, protocol: int, payload_length: int) -> bytes:
    return struct.pack(
        "!BBHHHBBH4s4s",
        0x45,
        0,
        20 + payload_length,
        0,
        0,
        64,
        protocol,
        0,
        mtr_trace.socket.inet_aton(src),
        mtr_trace.socket.inet_aton(dst),
    )


def _ipv6_header(src: str, dst: str, next_header: int, payload_length: int) -> bytes:
    return struct.pack(
        "!IHBB16s16s",
        0x60000000,
        payload_length,
        next_header,
        64,
        mtr_trace.socket.inet_pton(mtr_trace.socket.AF_INET6, src),
        mtr_trace.socket.inet_pton(mtr_trace.socket.AF_INET6, dst),
    )


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def perf_counter(self) -> float:
        return self.now

    def time_ns(self) -> int:
        return int(self.now * 1_000_000_000)

    def advance(self, seconds: float) -> None:
        self.now += max(0.0, seconds)


class _FakeReceiveSocket:
    def __init__(self, clock: _FakeClock, events: list[tuple[float, bytes, tuple[object, ...]]]) -> None:
        self._clock = clock
        self._events = list(events)
        self.closed = False

    def has_ready(self) -> bool:
        return any(event[0] <= self._clock.now for event in self._events)

    def advance(self, timeout: float) -> None:
        if not self._events:
            self._clock.advance(timeout)
            return
        next_ready = min(event[0] for event in self._events)
        if next_ready <= self._clock.now:
            return
        self._clock.advance(min(timeout, next_ready - self._clock.now))

    def recvfrom(self, _size: int) -> tuple[bytes, tuple[object, ...]]:
        for index, event in enumerate(self._events):
            ready_at, payload, address = event
            if ready_at <= self._clock.now:
                self._events.pop(index)
                return payload, address
        raise BlockingIOError

    def close(self) -> None:
        self.closed = True


class _FakeSendSocket:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class MTRTraceTests(unittest.TestCase):
    def test_request_normalized_bounds_and_protocol(self) -> None:
        request = MTRTraceRequest(
            target="  example.com  ",
            max_hops=500,
            timeout_ms=20,
            interval_ms=25,
            cycles=-10,
            protocol="udp",
            resolve_hostnames=False,
            ipv6=True,
            fast_mode=True,
        ).normalized()
        self.assertEqual(request.target, "example.com")
        self.assertEqual(request.max_hops, 255)
        self.assertEqual(request.timeout_ms, 100)
        self.assertEqual(request.interval_ms, 100)
        self.assertEqual(request.cycles, 0)
        self.assertEqual(request.protocol, MTR_PROTOCOL_UDP)
        self.assertFalse(request.resolve_hostnames)
        self.assertTrue(request.ipv6)
        self.assertTrue(request.fast_mode)

    def test_request_defaults_to_auto_protocol(self) -> None:
        request = MTRTraceRequest(target="example.com").normalized()
        self.assertEqual(request.protocol, MTR_PROTOCOL_AUTO)

    def test_hop_stats_aggregates_latency_metrics(self) -> None:
        stats = mtr_trace._HopStats(3)
        stats.record_timeout()
        stats.record_reply(address="198.51.100.3", host="router-3", rtt_ms=10.0, reached_destination=False)
        stats.record_reply(address="198.51.100.3", host="router-3", rtt_ms=20.0, reached_destination=True)

        snapshot = stats.snapshot()
        self.assertEqual(snapshot.hop, 3)
        self.assertEqual(snapshot.sent, 3)
        self.assertEqual(snapshot.received, 2)
        self.assertAlmostEqual(snapshot.loss_percent, 33.3333333333, places=3)
        self.assertEqual(snapshot.last_ms, 20.0)
        self.assertEqual(snapshot.best_ms, 10.0)
        self.assertEqual(snapshot.worst_ms, 20.0)
        self.assertEqual(snapshot.avg_ms, 15.0)
        self.assertAlmostEqual(snapshot.stdev_ms or 0.0, 5.0, places=3)
        self.assertTrue(snapshot.reached_destination)

    def test_match_probe_reply_parses_ipv4_echo_reply(self) -> None:
        token = mtr_trace._ProbeToken(protocol=MTR_PROTOCOL_ICMP, identifier=111, sequence=222)
        icmp = struct.pack("!BBHHH", 0, 0, 0, token.identifier, token.sequence)
        payload = _ipv4_header("8.8.8.8", "192.0.2.10", mtr_trace.socket.IPPROTO_ICMP, len(icmp)) + icmp

        reply = mtr_trace._match_probe_reply(
            payload=payload,
            address=("8.8.8.8", 0),
            family=mtr_trace.socket.AF_INET,
            token=token,
        )

        self.assertIsNotNone(reply)
        assert reply is not None
        self.assertEqual(reply.responder_address, "8.8.8.8")
        self.assertTrue(reply.reached_destination)

    def test_match_probe_reply_parses_ipv4_time_exceeded_for_icmp(self) -> None:
        token = mtr_trace._ProbeToken(protocol=MTR_PROTOCOL_ICMP, identifier=321, sequence=654)
        inner_icmp = struct.pack("!BBHHH", 8, 0, 0, token.identifier, token.sequence)
        inner_ip = _ipv4_header("192.0.2.10", "8.8.8.8", mtr_trace.socket.IPPROTO_ICMP, len(inner_icmp))
        outer_icmp = struct.pack("!BBHI", 11, 0, 0, 0) + inner_ip + inner_icmp
        payload = _ipv4_header("203.0.113.1", "192.0.2.10", mtr_trace.socket.IPPROTO_ICMP, len(outer_icmp)) + outer_icmp

        reply = mtr_trace._match_probe_reply(
            payload=payload,
            address=("203.0.113.1", 0),
            family=mtr_trace.socket.AF_INET,
            token=token,
        )

        self.assertIsNotNone(reply)
        assert reply is not None
        self.assertEqual(reply.responder_address, "203.0.113.1")
        self.assertFalse(reply.reached_destination)

    def test_match_probe_reply_parses_ipv4_port_unreachable_for_udp(self) -> None:
        token = mtr_trace._ProbeToken(protocol=MTR_PROTOCOL_UDP, dest_port=33455)
        inner_udp = struct.pack("!HHHH", 40000, token.dest_port, 8, 0)
        inner_ip = _ipv4_header("192.0.2.10", "8.8.8.8", mtr_trace.socket.IPPROTO_UDP, len(inner_udp))
        outer_icmp = struct.pack("!BBHI", 3, 3, 0, 0) + inner_ip + inner_udp
        payload = _ipv4_header("8.8.8.8", "192.0.2.10", mtr_trace.socket.IPPROTO_ICMP, len(outer_icmp)) + outer_icmp

        reply = mtr_trace._match_probe_reply(
            payload=payload,
            address=("8.8.8.8", 0),
            family=mtr_trace.socket.AF_INET,
            token=token,
        )

        self.assertIsNotNone(reply)
        assert reply is not None
        self.assertTrue(reply.reached_destination)

    def test_match_probe_reply_parses_ipv6_echo_reply(self) -> None:
        token = mtr_trace._ProbeToken(protocol=MTR_PROTOCOL_ICMP, identifier=500, sequence=12)
        icmp = struct.pack("!BBHHH", 129, 0, 0, token.identifier, token.sequence)
        payload = _ipv6_header("2001:db8::8", "2001:db8::10", mtr_trace.socket.IPPROTO_ICMPV6, len(icmp)) + icmp

        reply = mtr_trace._match_probe_reply(
            payload=payload,
            address=("2001:db8::8", 0, 0, 0),
            family=mtr_trace.socket.AF_INET6,
            token=token,
        )

        self.assertIsNotNone(reply)
        assert reply is not None
        self.assertEqual(reply.responder_address, "2001:db8::8")
        self.assertTrue(reply.reached_destination)

    def test_match_probe_reply_parses_ipv6_time_exceeded_for_icmp(self) -> None:
        token = mtr_trace._ProbeToken(protocol=MTR_PROTOCOL_ICMP, identifier=77, sequence=88)
        inner_icmp = struct.pack("!BBHHH", 128, 0, 0, token.identifier, token.sequence)
        inner_ip = _ipv6_header("2001:db8::10", "2001:db8::8", mtr_trace.socket.IPPROTO_ICMPV6, len(inner_icmp))
        outer_icmp = struct.pack("!BBHI", 3, 0, 0, 0) + inner_ip + inner_icmp
        payload = _ipv6_header("2001:db8::1", "2001:db8::10", mtr_trace.socket.IPPROTO_ICMPV6, len(outer_icmp)) + outer_icmp

        reply = mtr_trace._match_probe_reply(
            payload=payload,
            address=("2001:db8::1", 0, 0, 0),
            family=mtr_trace.socket.AF_INET6,
            token=token,
        )

        self.assertIsNotNone(reply)
        assert reply is not None
        self.assertEqual(reply.responder_address, "2001:db8::1")
        self.assertFalse(reply.reached_destination)

    def test_match_probe_reply_parses_ipv6_port_unreachable_for_udp(self) -> None:
        token = mtr_trace._ProbeToken(protocol=MTR_PROTOCOL_UDP, dest_port=33460)
        inner_udp = struct.pack("!HHHH", 40000, token.dest_port, 8, 0)
        inner_ip = _ipv6_header("2001:db8::10", "2001:db8::8", mtr_trace.socket.IPPROTO_UDP, len(inner_udp))
        outer_icmp = struct.pack("!BBHI", 1, 4, 0, 0) + inner_ip + inner_udp
        payload = _ipv6_header("2001:db8::8", "2001:db8::10", mtr_trace.socket.IPPROTO_ICMPV6, len(outer_icmp)) + outer_icmp

        reply = mtr_trace._match_probe_reply(
            payload=payload,
            address=("2001:db8::8", 0, 0, 0),
            family=mtr_trace.socket.AF_INET6,
            token=token,
        )

        self.assertIsNotNone(reply)
        assert reply is not None
        self.assertTrue(reply.reached_destination)

    def test_format_mtr_report_contains_summary_and_rows(self) -> None:
        snapshot = MTRTraceSnapshot(
            state="completed",
            message="Trace complete after 1 cycle.",
            cycle=1,
            target="8.8.8.8",
            protocol=MTR_PROTOCOL_ICMP,
            ipv6=False,
            hops=[
                MTRHopSnapshot(
                    hop=1,
                    host="router",
                    address="192.0.2.1",
                    sent=2,
                    received=2,
                    loss_percent=0.0,
                    last_ms=1.0,
                    avg_ms=1.5,
                    best_ms=1.0,
                    worst_ms=2.0,
                    stdev_ms=0.5,
                    reached_destination=False,
                )
            ],
        )
        report = format_mtr_report(snapshot)
        self.assertIn("Traceroute report for 8.8.8.8", report)
        self.assertIn("Hop\tHost\tAddress", report)
        self.assertIn("router", report)

    def test_launch_mtr_helper_uses_plain_launch_without_elevation(self) -> None:
        with (
            patch("snakesh.services.mtr_trace._launch_mtr_helper_plain", return_value=["plain"]) as mock_plain,
            patch("snakesh.services.mtr_trace.launch_mtr_helper_elevated") as mock_elevated,
        ):
            command = launch_mtr_helper("/tmp/snakesh-mtr")
        self.assertEqual(command, ["plain"])
        mock_plain.assert_called_once_with("/tmp/snakesh-mtr")
        mock_elevated.assert_not_called()

    def test_launch_mtr_helper_elevated_linux_uses_pkexec(self) -> None:
        with (
            patch("snakesh.services.mtr_trace.shutil.which", return_value="/usr/bin/pkexec"),
            patch(
                "snakesh.services.mtr_trace.subprocess.run",
                return_value=mtr_trace.subprocess.CompletedProcess(args=["pkexec"], returncode=0, stdout="", stderr=""),
            ) as mock_run,
        ):
            command = launch_mtr_helper_elevated("/tmp/snakesh-mtr", platform_name="linux")
        self.assertTrue(command)
        self.assertEqual(mock_run.call_args.args[0][:2], ["pkexec", "/bin/sh"])

    def test_launch_mtr_helper_elevated_windows_uses_explicit_powershell_path(self) -> None:
        with (
            patch(
                "snakesh.services.mtr_trace.runtime.self_launch_command",
                return_value=[r"C:\Program Files\SnakeSh\SnakeSh.exe", "--mtr-helper", r"C:\Temp\snakesh-mtr"],
            ),
            patch(
                "snakesh.services.mtr_trace._windows_powershell_executable",
                return_value=r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
            ),
            patch(
                "snakesh.services.mtr_trace.subprocess.run",
                return_value=mtr_trace.subprocess.CompletedProcess(
                    args=[r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"],
                    returncode=0,
                    stdout="",
                    stderr="",
                ),
            ) as mock_run,
        ):
            command = launch_mtr_helper_elevated(r"C:\Temp\snakesh-mtr", platform_name="windows")
        self.assertEqual(command, [r"C:\Program Files\SnakeSh\SnakeSh.exe", "--mtr-helper", r"C:\Temp\snakesh-mtr"])
        self.assertEqual(
            mock_run.call_args.args[0][0],
            r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
        )

    def test_needs_mtr_helper_elevation_only_for_fast_mode_when_not_already_elevated(self) -> None:
        with patch("snakesh.services.mtr_trace._is_effectively_elevated", return_value=False):
            self.assertFalse(needs_mtr_helper_elevation(MTRTraceRequest(target="8.8.8.8", fast_mode=False)))
            self.assertTrue(needs_mtr_helper_elevation(MTRTraceRequest(target="8.8.8.8", fast_mode=True)))
        with patch("snakesh.services.mtr_trace._is_effectively_elevated", return_value=True):
            self.assertFalse(needs_mtr_helper_elevation(MTRTraceRequest(target="8.8.8.8", fast_mode=True)))

    def test_needs_mtr_helper_elevation_is_disabled_on_windows(self) -> None:
        with patch("snakesh.services.mtr_trace._is_effectively_elevated", return_value=False):
            self.assertFalse(
                needs_mtr_helper_elevation(MTRTraceRequest(target="8.8.8.8", fast_mode=True), platform_name="windows")
            )

    def test_starting_snapshot_omits_native_prefix_for_windows_frozen_fast_mode(self) -> None:
        request = MTRTraceRequest(target="8.8.8.8", fast_mode=True).normalized()
        with (
            patch("snakesh.services.mtr_trace.runtime.is_frozen", return_value=True),
            patch("snakesh.services.mtr_trace._platform_name", return_value="windows"),
        ):
            snapshot = mtr_trace._starting_mtr_snapshot(request)
        self.assertNotIn("fast native", snapshot.message.lower())
        self.assertIn("starting automatic traceroute", snapshot.message.lower())

    def test_build_trace_cycle_command_windows_normalizes_udp_to_icmp(self) -> None:
        request = MTRTraceRequest(target="8.8.8.8", protocol=MTR_PROTOCOL_UDP).normalized()
        with patch(
            "snakesh.services.mtr_trace._windows_tracert_executable",
            return_value=r"C:\Windows\System32\tracert.exe",
        ):
            command, parser_kind, effective_protocol, note = build_trace_cycle_command(request, platform_name="windows")
        self.assertEqual(command[0], r"C:\Windows\System32\tracert.exe")
        self.assertEqual(parser_kind, "windows")
        self.assertEqual(effective_protocol, MTR_PROTOCOL_ICMP)
        self.assertIn("UDP mode", note)

    def test_windows_system_executable_prefers_system_root_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            candidate = Path(tmp) / "System32" / "tracert.exe"
            candidate.parent.mkdir(parents=True, exist_ok=True)
            candidate.write_text("", encoding="utf-8")
            with (
                patch.dict("os.environ", {"SystemRoot": tmp}, clear=True),
                patch("snakesh.services.mtr_trace.shutil.which", return_value=None),
            ):
                executable = mtr_trace._windows_system_executable(
                    relative_paths=("System32/tracert.exe", "Sysnative/tracert.exe"),
                    fallback_names=("tracert.exe", "tracert"),
                    platform_name="windows",
                )
        self.assertTrue(executable.endswith("System32/tracert.exe"))

    def test_pid_exists_windows_uses_process_handle_probe(self) -> None:
        import ctypes as real_ctypes

        class _FakeKernel32:
            def OpenProcess(self, access, _inherit_handle, pid):
                self.access = access
                self.pid = pid
                return 123

            def GetExitCodeProcess(self, handle, exit_code_ptr):
                self.handle = handle
                exit_code_ptr._obj.value = 259
                return 1

            def CloseHandle(self, handle):
                self.closed_handle = handle
                return 1

        fake_kernel32 = _FakeKernel32()
        fake_ctypes = types.SimpleNamespace(
            windll=types.SimpleNamespace(kernel32=fake_kernel32),
            c_ulong=real_ctypes.c_ulong,
            byref=real_ctypes.byref,
            get_last_error=lambda: 0,
        )

        with patch.dict(sys.modules, {"ctypes": fake_ctypes}):
            self.assertTrue(mtr_trace._pid_exists(4321, platform_name="windows"))

        self.assertEqual(fake_kernel32.pid, 4321)
        self.assertEqual(fake_kernel32.handle, 123)
        self.assertEqual(fake_kernel32.closed_handle, 123)

    def test_build_trace_cycle_command_uses_target_override_for_hostname_requests(self) -> None:
        request = MTRTraceRequest(target="voidfall.czargamingco.com", protocol=MTR_PROTOCOL_AUTO).normalized()
        with patch(
            "snakesh.services.mtr_trace.shutil.which",
            side_effect=lambda name: f"/usr/bin/{name}" if name in {"tracepath", "traceroute"} else None,
        ):
            command, parser_kind, effective_protocol, note = build_trace_cycle_command(
                request,
                target_override="24.53.168.145",
                platform_name="linux",
            )
        self.assertEqual(command[-1], "24.53.168.145")
        self.assertEqual(parser_kind, "traceroute")
        self.assertEqual(effective_protocol, MTR_PROTOCOL_UDP)
        self.assertEqual(note, "")

    def test_build_trace_cycle_command_linux_auto_prefers_udp_traceroute(self) -> None:
        request = MTRTraceRequest(target="8.8.8.8", protocol=MTR_PROTOCOL_AUTO).normalized()
        with (
            patch("snakesh.services.mtr_trace.shutil.which", side_effect=lambda name: f"/usr/bin/{name}" if name in {"tracepath", "traceroute"} else None),
        ):
            command, parser_kind, effective_protocol, note = build_trace_cycle_command(request, platform_name="linux")
        self.assertEqual(command[0], "traceroute")
        self.assertIn("-N", command)
        self.assertEqual(command[command.index("-N") + 1], "1")
        self.assertNotIn("-I", command)
        self.assertEqual(parser_kind, "traceroute")
        self.assertEqual(effective_protocol, MTR_PROTOCOL_UDP)
        self.assertEqual(note, "")

    def test_build_trace_cycle_command_darwin_uses_integer_traceroute_wait(self) -> None:
        request = MTRTraceRequest(target="8.8.8.8", timeout_ms=3000, protocol=MTR_PROTOCOL_AUTO).normalized()
        with patch(
            "snakesh.services.mtr_trace.shutil.which",
            side_effect=lambda name: f"/usr/bin/{name}" if name in {"tracepath", "traceroute"} else None,
        ):
            command, parser_kind, _effective_protocol, _note = build_trace_cycle_command(
                request,
                platform_name="darwin",
            )

        self.assertEqual(command[0], "traceroute")
        self.assertEqual(parser_kind, "traceroute")
        self.assertEqual(command[command.index("-w") + 1], "3")
        self.assertNotIn("3.0", command)

    def test_build_trace_cycle_commands_linux_icmp_tries_native_then_udp_then_tracepath(self) -> None:
        request = MTRTraceRequest(target="8.8.8.8", protocol=MTR_PROTOCOL_ICMP).normalized()
        with patch(
            "snakesh.services.mtr_trace.shutil.which",
            side_effect=lambda name: f"/usr/bin/{name}" if name in {"tracepath", "traceroute"} else None,
        ):
            commands = build_trace_cycle_commands(request, platform_name="linux")
        self.assertEqual(len(commands), 3)
        self.assertEqual(commands[0].command[0], "traceroute")
        self.assertIn("-I", commands[0].command)
        self.assertEqual(commands[0].effective_protocol, MTR_PROTOCOL_ICMP)
        self.assertEqual(commands[1].command[0], "traceroute")
        self.assertNotIn("-I", commands[1].command)
        self.assertEqual(commands[1].effective_protocol, MTR_PROTOCOL_UDP)
        self.assertIn("udp traceroute fallback", commands[1].note.lower())
        self.assertEqual(commands[2].command[0], "tracepath")
        self.assertEqual(commands[2].effective_protocol, MTR_PROTOCOL_UDP)
        self.assertIn("tracepath fallback", commands[2].note.lower())

    def test_run_trace_cycle_uses_fallback_candidate_after_primary_failure(self) -> None:
        request = MTRTraceRequest(target="8.8.8.8", protocol=MTR_PROTOCOL_ICMP).normalized()
        primary = mtr_trace._TraceCycleCommandSpec(
            command=["traceroute", "-I", "8.8.8.8"],
            parser_kind="traceroute",
            effective_protocol=MTR_PROTOCOL_ICMP,
            note="",
        )
        fallback = mtr_trace._TraceCycleCommandSpec(
            command=["traceroute", "8.8.8.8"],
            parser_kind="traceroute",
            effective_protocol=MTR_PROTOCOL_UDP,
            note="Native ICMP traceroute failed; using UDP traceroute fallback.",
        )
        attempts: list[tuple[str, ...]] = []

        def _fake_attempt(*_args, command_spec, next_sample_index: int, **_kwargs):
            attempts.append(tuple(command_spec.command))
            if command_spec.command == primary.command:
                raise ValueError("icmp failed")
            return False, next_sample_index + 1

        with (
            patch("snakesh.services.mtr_trace.build_trace_cycle_commands", return_value=[primary, fallback]),
            patch("snakesh.services.mtr_trace._run_trace_cycle_attempt", side_effect=_fake_attempt),
        ):
            stopped, effective_protocol, note, next_sample_index = mtr_trace._run_trace_cycle(
                request,
                destination_address="8.8.8.8",
                stop_path=Path("/tmp/nonexistent-stop"),
                parent_pid=os.getpid(),
                hop_stats={},
                cycle=1,
                state_callback=lambda _snapshot: None,
                sample_callback=lambda _sample: None,
                next_sample_index=1,
            )

        self.assertFalse(stopped)
        self.assertEqual(effective_protocol, MTR_PROTOCOL_UDP)
        self.assertIn("udp traceroute fallback", note.lower())
        self.assertEqual(next_sample_index, 2)
        self.assertEqual(attempts, [tuple(primary.command), tuple(fallback.command)])

    def test_run_trace_cycle_attempt_stops_collecting_after_destination_hop(self) -> None:
        request = MTRTraceRequest(target="8.8.8.8", protocol=MTR_PROTOCOL_AUTO).normalized()
        command_spec = mtr_trace._TraceCycleCommandSpec(
            command=["traceroute", "-N", "1", "8.8.8.8"],
            parser_kind="traceroute",
            effective_protocol=MTR_PROTOCOL_UDP,
            note="",
        )
        lines = ["hop1", "hop2 8.8.8.8", "hop3"]
        parsed_map = {
            "hop1": mtr_trace._CycleHopMeasurement(
                hop=1,
                host="router-1",
                address="192.0.2.1",
                sent=1,
                samples_ms=[1.0],
                probe_results=[mtr_trace._ProbeResult(success=True, timeout=False, rtt_ms=1.0)],
                reached_destination=False,
            ),
            "hop2 8.8.8.8": mtr_trace._CycleHopMeasurement(
                hop=2,
                host="dest",
                address="8.8.8.8",
                sent=1,
                samples_ms=[5.0],
                probe_results=[mtr_trace._ProbeResult(success=True, timeout=False, rtt_ms=5.0)],
                reached_destination=False,
            ),
            "hop3": mtr_trace._CycleHopMeasurement(
                hop=3,
                host="",
                address="",
                sent=1,
                samples_ms=[],
                probe_results=[mtr_trace._ProbeResult(success=False, timeout=True, rtt_ms=None)],
                reached_destination=False,
            ),
        }

        class _FakeProcess:
            def __init__(self) -> None:
                self.pid = 12345
                self.stdout = object()
                self.returncode = None

            def poll(self):
                return self.returncode

            def terminate(self):
                self.returncode = 0

            def kill(self):
                self.returncode = 0

            def wait(self, timeout=None):
                self.returncode = 0
                return 0

        def _fake_reader(process, queue) -> None:
            for line in lines:
                queue.put(line)
            process.returncode = 0
            queue.put(None)

        state_updates: list[MTRTraceSnapshot] = []
        samples: list[MTRProbeSample] = []
        hop_stats: dict[int, mtr_trace._HopStats] = {}

        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch("snakesh.services.mtr_trace.subprocess.Popen", return_value=_FakeProcess()),
                patch("snakesh.services.mtr_trace._read_process_lines", side_effect=_fake_reader),
                patch("snakesh.services.mtr_trace._parse_trace_output_line", side_effect=lambda line, **_: parsed_map.get(line)),
            ):
                stopped, next_sample_index = mtr_trace._run_trace_cycle_attempt(
                    request,
                    destination_address="8.8.8.8",
                    stop_path=Path(tmp) / "stop",
                    parent_pid=os.getpid(),
                    hop_stats=hop_stats,
                    cycle=1,
                    state_callback=state_updates.append,
                    sample_callback=samples.append,
                    next_sample_index=1,
                    command_spec=command_spec,
                )

        self.assertFalse(stopped)
        self.assertEqual(next_sample_index, 3)
        self.assertEqual(sorted(hop_stats), [1, 2])
        self.assertEqual([sample.hop for sample in samples], [1, 2])
        self.assertTrue(state_updates[-1].hops[-1].reached_destination)

    def test_run_trace_cycle_attempt_hides_windows_console_window(self) -> None:
        request = MTRTraceRequest(target="8.8.8.8").normalized()
        command_spec = mtr_trace._TraceCycleCommandSpec(
            command=[r"C:\Windows\System32\tracert.exe", "8.8.8.8"],
            parser_kind="windows",
            effective_protocol=MTR_PROTOCOL_ICMP,
            note="",
        )
        measurement = mtr_trace._CycleHopMeasurement(
            hop=1,
            host="router-1",
            address="192.0.2.1",
            sent=3,
            samples_ms=[1.0, 2.0, 3.0],
            probe_results=[
                mtr_trace._ProbeResult(success=True, timeout=False, rtt_ms=1.0),
                mtr_trace._ProbeResult(success=True, timeout=False, rtt_ms=2.0),
                mtr_trace._ProbeResult(success=True, timeout=False, rtt_ms=3.0),
            ],
            reached_destination=False,
        )

        class _FakeProcess:
            def __init__(self) -> None:
                self.pid = 321
                self.stdout = object()
                self.returncode = None

            def poll(self):
                return self.returncode

            def terminate(self):
                self.returncode = 0

            def kill(self):
                self.returncode = 0

            def wait(self, timeout=None):
                self.returncode = 0
                return 0

        def _fake_reader(process, queue) -> None:
            queue.put("hop1")
            process.returncode = 0
            queue.put(None)

        with tempfile.TemporaryDirectory() as tmp:
            stop_path = Path(tmp) / "stop"
            with (
                patch("snakesh.services.mtr_trace.os.name", "nt"),
                patch.object(mtr_trace.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200, create=True),
                patch.object(mtr_trace.subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True),
                patch("snakesh.services.mtr_trace.subprocess.Popen", return_value=_FakeProcess()) as mock_popen,
                patch("snakesh.services.mtr_trace._read_process_lines", side_effect=_fake_reader),
                patch("snakesh.services.mtr_trace._parse_trace_output_line", return_value=measurement),
            ):
                stopped, next_sample_index = mtr_trace._run_trace_cycle_attempt(
                    request,
                    destination_address="8.8.8.8",
                    stop_path=stop_path,
                    parent_pid=os.getpid(),
                    hop_stats={},
                    cycle=1,
                    state_callback=lambda _snapshot: None,
                    sample_callback=lambda _sample: None,
                    next_sample_index=1,
                    command_spec=command_spec,
                )

        self.assertFalse(stopped)
        self.assertEqual(next_sample_index, 4)
        self.assertEqual(
            mock_popen.call_args.kwargs["creationflags"],
            0x00000200 | 0x08000000,
        )

    def test_wrap_command_for_live_output_uses_stdbuf_on_linux(self) -> None:
        with patch("snakesh.services.mtr_trace.shutil.which", return_value="/usr/bin/stdbuf"):
            command = mtr_trace._wrap_command_for_live_output(["tracepath", "8.8.8.8"], platform_name="linux")
        self.assertEqual(command[:3], ["stdbuf", "-oL", "-eL"])
        self.assertEqual(command[3:], ["tracepath", "8.8.8.8"])

    def test_parse_windows_tracert_output(self) -> None:
        output = """
Tracing route to dns.google [8.8.8.8]

  1     1 ms     2 ms     *     192.0.2.1
  2     *        *        *     Request timed out.
 11     8 ms     9 ms     8 ms  dns.google [8.8.8.8]
Trace complete.
"""
        hops = mtr_trace._parse_windows_tracert_output(output, destination_address="8.8.8.8")
        self.assertEqual(len(hops), 3)
        self.assertEqual(hops[0].sent, 3)
        self.assertEqual(hops[0].samples_ms, [1.0, 2.0])
        self.assertEqual(hops[1].samples_ms, [])
        self.assertTrue(hops[2].reached_destination)

    def test_parse_traceroute_output(self) -> None:
        output = """
traceroute to dns.google (8.8.8.8), 30 hops max
 1  example-router (192.0.2.1)  0.412 ms
 2  *
11  dns.google (8.8.8.8)  9.125 ms
12  *
"""
        hops = mtr_trace._parse_traceroute_output(output, destination_address="8.8.8.8", resolve_hostnames=True)
        self.assertEqual(len(hops), 3)
        self.assertEqual(hops[0].host, "example-router")
        self.assertEqual(hops[1].samples_ms, [])
        self.assertTrue(hops[2].reached_destination)

    def test_parse_tracepath_output(self) -> None:
        output = """
 1?: [LOCALHOST]                      pmtu 1500
 1:  192.0.2.1                      0.312ms
 2:  no reply
11:  dns.google                       9.843ms reached
     Resume: pmtu 1500 hops 11 back 11
"""
        hops = mtr_trace._parse_tracepath_output(output, destination_address="8.8.8.8", resolve_hostnames=True)
        self.assertEqual(len(hops), 3)
        self.assertEqual(hops[0].address, "192.0.2.1")
        self.assertEqual(hops[1].samples_ms, [])
        self.assertTrue(hops[2].reached_destination)

    def test_record_measurement_samples_assigns_incrementing_indexes(self) -> None:
        measurement = mtr_trace._CycleHopMeasurement(
            hop=4,
            host="router-4",
            address="192.0.2.4",
            sent=3,
            samples_ms=[8.5, 9.0],
            probe_results=[
                mtr_trace._ProbeResult(success=True, timeout=False, rtt_ms=8.5),
                mtr_trace._ProbeResult(success=False, timeout=True, rtt_ms=None),
                mtr_trace._ProbeResult(success=True, timeout=False, rtt_ms=9.0),
            ],
            reached_destination=False,
        )
        recorded: list[MTRProbeSample] = []

        next_index = mtr_trace._record_measurement_samples(
            measurement,
            cycle=2,
            next_sample_index=7,
            sample_callback=recorded.append,
        )

        self.assertEqual(next_index, 10)
        self.assertEqual([sample.sample_index for sample in recorded], [7, 8, 9])
        self.assertEqual([sample.timeout for sample in recorded], [False, True, False])
        self.assertEqual(recorded[0].host, "router-4")

    def test_run_trace_loop_threads_sample_index_across_cycles(self) -> None:
        call_indexes: list[int] = []

        def _fake_run_trace_cycle(*_args, next_sample_index: int, **_kwargs):
            call_indexes.append(next_sample_index)
            return False, MTR_PROTOCOL_ICMP, "", next_sample_index + 3

        request = MTRTraceRequest(target="8.8.8.8", cycles=2)
        resolved_target = mtr_trace._ResolvedTarget(
            family=mtr_trace.socket.AF_INET,
            address="8.8.8.8",
            sockaddr=("8.8.8.8", 0),
            source_address="192.0.2.10",
        )
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch("snakesh.services.mtr_trace._resolve_target", return_value=resolved_target),
                patch("snakesh.services.mtr_trace._run_trace_cycle", side_effect=_fake_run_trace_cycle),
            ):
                snapshot = mtr_trace._run_trace_loop(
                    request,
                    stop_path=Path(tmp) / "stop",
                    parent_pid=os.getpid(),
                    state_callback=lambda _snapshot: None,
                    sample_callback=lambda _sample: None,
                )

        self.assertEqual(call_indexes, [1, 4])
        self.assertEqual(snapshot.state, "completed")
        self.assertEqual(snapshot.cycle, 2)

    def test_run_trace_loop_windows_frozen_fast_mode_falls_back_to_command_loop(self) -> None:
        request = MTRTraceRequest(target="8.8.8.8", fast_mode=True).normalized()
        expected = MTRTraceSnapshot(
            state="completed",
            message="Trace complete after 1 cycle.",
            cycle=1,
            target="8.8.8.8",
            protocol=MTR_PROTOCOL_AUTO,
            ipv6=False,
            hops=[],
        )

        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch("snakesh.services.mtr_trace.runtime.is_frozen", return_value=True),
                patch("snakesh.services.mtr_trace._platform_name", return_value="windows"),
                patch("snakesh.services.mtr_trace._run_trace_loop_command", return_value=expected) as mock_command,
                patch("snakesh.services.mtr_trace._run_trace_loop_native") as mock_native,
            ):
                snapshot = mtr_trace._run_trace_loop(
                    request,
                    stop_path=Path(tmp) / "stop",
                    parent_pid=os.getpid(),
                    state_callback=lambda _snapshot: None,
                    sample_callback=lambda _sample: None,
                )

        self.assertIs(snapshot, expected)
        mock_command.assert_called_once()
        mock_native.assert_not_called()

    def test_run_trace_loop_native_launches_new_cycles_before_prior_timeouts_and_waits_for_final_timeouts(self) -> None:
        clock = _FakeClock()
        receive_socket = _FakeReceiveSocket(clock, [])
        send_log: list[tuple[float, int]] = []
        request = MTRTraceRequest(
            target="8.8.8.8",
            protocol=MTR_PROTOCOL_ICMP,
            fast_mode=True,
            cycles=2,
            max_hops=1,
            timeout_ms=500,
            interval_ms=100,
        ).normalized()
        resolved_target = mtr_trace._ResolvedTarget(
            family=mtr_trace.socket.AF_INET,
            address="8.8.8.8",
            sockaddr=("8.8.8.8", 0),
            source_address="192.0.2.10",
        )
        samples: list[MTRProbeSample] = []

        def _fake_send_probe(*, hop: int, **_kwargs) -> None:
            send_log.append((clock.now, hop))

        def _fake_select(readers, _writers, _errors, timeout):
            if receive_socket.has_ready():
                return readers, [], []
            receive_socket.advance(float(timeout))
            if receive_socket.has_ready():
                return readers, [], []
            return [], [], []

        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch("snakesh.services.mtr_trace._resolve_target", return_value=resolved_target),
                patch("snakesh.services.mtr_trace._open_receive_socket", return_value=receive_socket),
                patch("snakesh.services.mtr_trace._open_send_socket", return_value=_FakeSendSocket()),
                patch("snakesh.services.mtr_trace._send_probe", side_effect=_fake_send_probe),
                patch("snakesh.services.mtr_trace.select.select", side_effect=_fake_select),
                patch("snakesh.services.mtr_trace.time.perf_counter", side_effect=clock.perf_counter),
                patch("snakesh.services.mtr_trace.time.time_ns", side_effect=clock.time_ns),
            ):
                snapshot = mtr_trace._run_trace_loop(
                    request,
                    stop_path=Path(tmp) / "stop",
                    parent_pid=os.getpid(),
                    state_callback=lambda _snapshot: None,
                    sample_callback=samples.append,
                )

        self.assertEqual(send_log, [(0.0, 1), (0.1, 1)])
        self.assertEqual([sample.cycle for sample in samples], [1, 2])
        self.assertTrue(all(sample.timeout for sample in samples))
        self.assertGreaterEqual(clock.now, 0.6)
        self.assertEqual(snapshot.state, "completed")
        self.assertEqual(snapshot.cycle, 2)

    def test_run_trace_loop_native_preserves_dispatch_order_sample_indexes_when_replies_arrive_out_of_order(self) -> None:
        clock = _FakeClock()
        receive_socket = _FakeReceiveSocket(
            clock,
            [
                (0.0, b"hop2", ("8.8.8.8", 0)),
                (0.05, b"hop1", ("192.0.2.1", 0)),
            ],
        )
        request = MTRTraceRequest(
            target="8.8.8.8",
            protocol=MTR_PROTOCOL_ICMP,
            fast_mode=True,
            cycles=1,
            max_hops=2,
        ).normalized()
        resolved_target = mtr_trace._ResolvedTarget(
            family=mtr_trace.socket.AF_INET,
            address="8.8.8.8",
            sockaddr=("8.8.8.8", 0),
            source_address="192.0.2.10",
        )
        samples: list[MTRProbeSample] = []

        def _fake_select(readers, _writers, _errors, timeout):
            if receive_socket.has_ready():
                return readers, [], []
            receive_socket.advance(float(timeout))
            if receive_socket.has_ready():
                return readers, [], []
            return [], [], []

        def _fake_parse(payload: bytes, _address, _family):
            if payload == b"hop2":
                return mtr_trace._ParsedICMPReply(
                    responder_address="8.8.8.8",
                    icmp_type=0,
                    icmp_code=0,
                    inner_protocol=MTR_PROTOCOL_ICMP,
                    identifier=0,
                    sequence=2,
                    dest_port=0,
                    reached_destination=True,
                )
            if payload == b"hop1":
                return mtr_trace._ParsedICMPReply(
                    responder_address="192.0.2.1",
                    icmp_type=11,
                    icmp_code=0,
                    inner_protocol=MTR_PROTOCOL_ICMP,
                    identifier=0,
                    sequence=1,
                    dest_port=0,
                    reached_destination=False,
                )
            return None

        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch("snakesh.services.mtr_trace._resolve_target", return_value=resolved_target),
                patch("snakesh.services.mtr_trace._open_receive_socket", return_value=receive_socket),
                patch("snakesh.services.mtr_trace._open_send_socket", return_value=_FakeSendSocket()),
                patch("snakesh.services.mtr_trace._send_probe"),
                patch("snakesh.services.mtr_trace._parse_probe_reply", side_effect=_fake_parse),
                patch("snakesh.services.mtr_trace.select.select", side_effect=_fake_select),
                patch("snakesh.services.mtr_trace.time.perf_counter", side_effect=clock.perf_counter),
                patch("snakesh.services.mtr_trace.time.time_ns", side_effect=clock.time_ns),
            ):
                snapshot = mtr_trace._run_trace_loop(
                    request,
                    stop_path=Path(tmp) / "stop",
                    parent_pid=os.getpid(),
                    state_callback=lambda _snapshot: None,
                    sample_callback=samples.append,
                )

        self.assertEqual([(sample.hop, sample.sample_index) for sample in samples], [(2, 2), (1, 1)])
        self.assertEqual(snapshot.state, "completed")
        self.assertEqual(snapshot.cycle, 1)

    def test_run_trace_loop_native_matches_udp_replies_by_destination_port(self) -> None:
        clock = _FakeClock()
        receive_socket = _FakeReceiveSocket(
            clock,
            [
                (0.0, b"udp-hop", ("203.0.113.1", 0)),
            ],
        )
        request = MTRTraceRequest(
            target="8.8.8.8",
            protocol=MTR_PROTOCOL_UDP,
            fast_mode=True,
            cycles=1,
            max_hops=1,
            timeout_ms=500,
        ).normalized()
        resolved_target = mtr_trace._ResolvedTarget(
            family=mtr_trace.socket.AF_INET,
            address="8.8.8.8",
            sockaddr=("8.8.8.8", 0),
            source_address="192.0.2.10",
        )
        samples: list[MTRProbeSample] = []

        def _fake_select(readers, _writers, _errors, timeout):
            if receive_socket.has_ready():
                return readers, [], []
            receive_socket.advance(float(timeout))
            if receive_socket.has_ready():
                return readers, [], []
            return [], [], []

        def _fake_parse(payload: bytes, _address, _family):
            if payload != b"udp-hop":
                return None
            return mtr_trace._ParsedICMPReply(
                responder_address="203.0.113.1",
                icmp_type=11,
                icmp_code=0,
                inner_protocol=MTR_PROTOCOL_UDP,
                identifier=0,
                sequence=0,
                dest_port=mtr_trace._DEFAULT_UDP_BASE_PORT + 1,
                reached_destination=False,
            )

        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch("snakesh.services.mtr_trace._resolve_target", return_value=resolved_target),
                patch("snakesh.services.mtr_trace._open_receive_socket", return_value=receive_socket),
                patch("snakesh.services.mtr_trace._open_send_socket", return_value=_FakeSendSocket()),
                patch("snakesh.services.mtr_trace._send_probe"),
                patch("snakesh.services.mtr_trace._parse_probe_reply", side_effect=_fake_parse),
                patch("snakesh.services.mtr_trace.select.select", side_effect=_fake_select),
                patch("snakesh.services.mtr_trace.time.perf_counter", side_effect=clock.perf_counter),
                patch("snakesh.services.mtr_trace.time.time_ns", side_effect=clock.time_ns),
            ):
                snapshot = mtr_trace._run_trace_loop(
                    request,
                    stop_path=Path(tmp) / "stop",
                    parent_pid=os.getpid(),
                    state_callback=lambda _snapshot: None,
                    sample_callback=samples.append,
                )

        self.assertEqual(len(samples), 1)
        self.assertTrue(samples[0].success)
        self.assertFalse(samples[0].timeout)
        self.assertEqual(samples[0].address, "203.0.113.1")
        self.assertEqual(snapshot.state, "completed")
        self.assertEqual(snapshot.hops[0].received, 1)

    def test_run_trace_loop_native_discovers_destination_and_does_not_record_false_loss_beyond_it(self) -> None:
        clock = _FakeClock()
        receive_socket = _FakeReceiveSocket(
            clock,
            [
                (0.0, b"dest", ("8.8.8.8", 0)),
            ],
        )
        request = MTRTraceRequest(
            target="8.8.8.8",
            protocol=MTR_PROTOCOL_ICMP,
            fast_mode=True,
            cycles=2,
            max_hops=3,
            timeout_ms=500,
            interval_ms=100,
        ).normalized()
        resolved_target = mtr_trace._ResolvedTarget(
            family=mtr_trace.socket.AF_INET,
            address="8.8.8.8",
            sockaddr=("8.8.8.8", 0),
            source_address="192.0.2.10",
        )
        send_hops: list[int] = []
        samples: list[MTRProbeSample] = []

        def _fake_send_probe(*, hop: int, **_kwargs) -> None:
            send_hops.append(hop)

        def _fake_select(readers, _writers, _errors, timeout):
            if receive_socket.has_ready():
                return readers, [], []
            receive_socket.advance(float(timeout))
            if receive_socket.has_ready():
                return readers, [], []
            return [], [], []

        def _fake_parse(payload: bytes, _address, _family):
            if payload == b"dest":
                return mtr_trace._ParsedICMPReply(
                    responder_address="8.8.8.8",
                    icmp_type=0,
                    icmp_code=0,
                    inner_protocol=MTR_PROTOCOL_ICMP,
                    identifier=0,
                    sequence=2,
                    dest_port=0,
                    reached_destination=True,
                )
            return None

        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch("snakesh.services.mtr_trace._resolve_target", return_value=resolved_target),
                patch("snakesh.services.mtr_trace._open_receive_socket", return_value=receive_socket),
                patch("snakesh.services.mtr_trace._open_send_socket", return_value=_FakeSendSocket()),
                patch("snakesh.services.mtr_trace._send_probe", side_effect=_fake_send_probe),
                patch("snakesh.services.mtr_trace._parse_probe_reply", side_effect=_fake_parse),
                patch("snakesh.services.mtr_trace.select.select", side_effect=_fake_select),
                patch("snakesh.services.mtr_trace.time.perf_counter", side_effect=clock.perf_counter),
                patch("snakesh.services.mtr_trace.time.time_ns", side_effect=clock.time_ns),
            ):
                snapshot = mtr_trace._run_trace_loop(
                    request,
                    stop_path=Path(tmp) / "stop",
                    parent_pid=os.getpid(),
                    state_callback=lambda _snapshot: None,
                    sample_callback=samples.append,
                )

        self.assertEqual(send_hops, [1, 2, 3, 1, 2])
        self.assertNotIn(3, [sample.hop for sample in samples])
        self.assertNotIn(3, [hop.hop for hop in snapshot.hops])
        self.assertEqual(snapshot.state, "completed")

    def test_build_snapshot_keeps_only_earliest_destination_hop(self) -> None:
        hop_stats = {
            1: mtr_trace._HopStats(1),
            2: mtr_trace._HopStats(2),
            3: mtr_trace._HopStats(3),
        }
        hop_stats[1].record_reply(
            address="192.0.2.1",
            host="hop-1",
            rtt_ms=1.0,
            reached_destination=False,
        )
        hop_stats[2].record_reply(
            address="8.8.8.8",
            host="dest-2",
            rtt_ms=2.0,
            reached_destination=True,
        )
        hop_stats[3].record_reply(
            address="8.8.8.8",
            host="dest-3",
            rtt_ms=3.0,
            reached_destination=True,
        )

        snapshot = mtr_trace._build_snapshot(
            MTRTraceRequest(target="8.8.8.8"),
            hop_stats,
            cycle=1,
            state="completed",
            message="Trace complete.",
        )

        self.assertEqual([hop.hop for hop in snapshot.hops], [1, 2])

    def test_read_mtr_probe_samples_ignores_partial_jsonl_lines(self) -> None:
        sample = MTRProbeSample(
            sample_index=1,
            timestamp_ms=123,
            cycle=1,
            hop=1,
            host="router-1",
            address="192.0.2.1",
            success=True,
            timeout=False,
            rtt_ms=1.25,
            reached_destination=False,
        )
        with tempfile.TemporaryDirectory() as tmp:
            paths = mtr_helper_session_paths(tmp)
            paths.root.mkdir(parents=True, exist_ok=True)
            paths.samples_path.write_text(
                json.dumps(sample.to_dict()) + "\n" + '{"sample_index":2,"timestamp_ms":124',
                encoding="utf-8",
            )

            samples = read_mtr_probe_samples(tmp)

        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0].sample_index, 1)
        self.assertEqual(samples[0].host, "router-1")

    def test_format_mtr_samples_csv_includes_timeout_rows(self) -> None:
        csv_text = format_mtr_samples_csv(
            [
                MTRProbeSample(
                    sample_index=1,
                    timestamp_ms=123,
                    cycle=1,
                    hop=1,
                    host="router-1",
                    address="192.0.2.1",
                    success=True,
                    timeout=False,
                    rtt_ms=1.25,
                    reached_destination=False,
                ),
                MTRProbeSample(
                    sample_index=2,
                    timestamp_ms=124,
                    cycle=1,
                    hop=2,
                    host="",
                    address="",
                    success=False,
                    timeout=True,
                    rtt_ms=None,
                    reached_destination=False,
                ),
            ]
        )

        self.assertIn("sample_index,timestamp_ms,cycle,hop,host,address,success,timeout,rtt_ms,reached_destination", csv_text)
        self.assertIn("1,123,1,1,router-1,192.0.2.1,true,false,1.250,false", csv_text)
        self.assertIn("2,124,1,2,,,false,true,,false", csv_text)

    def test_wait_for_interval_stops_when_stop_file_appears(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stop_path = Path(tmp) / "stop"

            def _touch_stop() -> None:
                time.sleep(0.05)
                stop_path.write_text("stop\n", encoding="utf-8")

            worker = threading.Thread(target=_touch_stop)
            worker.start()
            try:
                self.assertTrue(mtr_trace._wait_for_interval(stop_path, os.getpid(), 500))
            finally:
                worker.join()

    def test_run_mtr_helper_writes_final_state_and_cleans_ready_file(self) -> None:
        request = MTRTraceRequest(target="8.8.8.8")
        snapshot = MTRTraceSnapshot(
            state="completed",
            message="Trace complete after 1 cycle(s).",
            cycle=1,
            target=request.target,
            protocol=request.protocol,
            ipv6=request.ipv6,
            hops=[],
        )
        with tempfile.TemporaryDirectory() as tmp:
            write_mtr_config(tmp, request, parent_pid=os.getpid())
            with patch("snakesh.services.mtr_trace._run_trace_loop", return_value=snapshot):
                exit_code = run_mtr_helper(tmp)

            paths = mtr_helper_session_paths(tmp)
            self.assertEqual(exit_code, 0)
            self.assertFalse(paths.ready_path.exists())
            self.assertTrue(paths.state_path.exists())
            final_snapshot = read_mtr_snapshot(tmp)
            self.assertIsNotNone(final_snapshot)
            assert final_snapshot is not None
            self.assertEqual(final_snapshot.state, "completed")

    def test_run_mtr_helper_persistent_session_processes_multiple_requests_until_shutdown(self) -> None:
        first_request = MTRTraceRequest(target="8.8.8.8", fast_mode=True)
        second_request = MTRTraceRequest(target="1.1.1.1", fast_mode=True)
        first_snapshot = MTRTraceSnapshot(
            state="completed",
            message="Trace complete after 1 cycle(s).",
            cycle=1,
            target=first_request.target,
            protocol=first_request.protocol,
            ipv6=first_request.ipv6,
            hops=[],
        )
        second_snapshot = MTRTraceSnapshot(
            state="completed",
            message="Trace complete after 1 cycle(s).",
            cycle=1,
            target=second_request.target,
            protocol=second_request.protocol,
            ipv6=second_request.ipv6,
            hops=[],
        )

        with tempfile.TemporaryDirectory() as tmp:
            paths = mtr_helper_session_paths(tmp)
            write_mtr_config(tmp, first_request, parent_pid=os.getpid(), persistent=True)
            with (
                patch("snakesh.services.mtr_trace._HELPER_POLL_INTERVAL_SECONDS", 0.01),
                patch(
                    "snakesh.services.mtr_trace._run_trace_loop",
                    side_effect=[first_snapshot, second_snapshot],
                ) as mock_run_trace_loop,
            ):
                worker = threading.Thread(target=run_mtr_helper, args=(tmp,))
                worker.start()
                try:
                    deadline = time.monotonic() + 2.0
                    while time.monotonic() < deadline and not paths.ready_path.exists():
                        time.sleep(0.01)
                    self.assertTrue(paths.ready_path.exists())

                    deadline = time.monotonic() + 2.0
                    while time.monotonic() < deadline:
                        snapshot = read_mtr_snapshot(tmp)
                        if snapshot is not None and snapshot.target == first_request.target and snapshot.state == "completed":
                            break
                        time.sleep(0.01)
                    snapshot = read_mtr_snapshot(tmp)
                    self.assertIsNotNone(snapshot)
                    assert snapshot is not None
                    self.assertEqual(snapshot.target, first_request.target)
                    self.assertTrue(paths.ready_path.exists())

                    write_mtr_config(tmp, second_request, parent_pid=os.getpid(), persistent=True)
                    deadline = time.monotonic() + 2.0
                    while time.monotonic() < deadline:
                        snapshot = read_mtr_snapshot(tmp)
                        if snapshot is not None and snapshot.target == second_request.target and snapshot.state == "completed":
                            break
                        time.sleep(0.01)
                    snapshot = read_mtr_snapshot(tmp)
                    self.assertIsNotNone(snapshot)
                    assert snapshot is not None
                    self.assertEqual(snapshot.target, second_request.target)
                    self.assertEqual(mock_run_trace_loop.call_count, 2)

                    paths.shutdown_path.write_text("shutdown\n", encoding="utf-8")
                    worker.join(timeout=2.0)
                    self.assertFalse(worker.is_alive())
                    self.assertFalse(paths.ready_path.exists())
                finally:
                    if worker.is_alive():
                        paths.shutdown_path.write_text("shutdown\n", encoding="utf-8")
                        worker.join(timeout=2.0)


if __name__ == "__main__":
    unittest.main()
