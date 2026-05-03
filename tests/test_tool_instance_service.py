from __future__ import annotations

import json
import os
from pathlib import Path
import socket
from types import SimpleNamespace
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from snakesh.services import _instance_activation as activation_service
from snakesh.services import tool_instance_service as service


class ToolInstanceServiceTests(unittest.TestCase):
    @staticmethod
    def _wait_for(predicate, *, timeout_seconds: float = 3.0) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.02)
        return bool(predicate())

    def test_claim_tool_instance_creates_state_and_lease(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"SNAKESH_DATA_DIR": tmp}, clear=False):
                result = service.claim_tool_instance("diff", on_activate=lambda _payload: True)
                self.assertFalse(result.activated_existing)
                self.assertIsNotNone(result.lease)
                assert result.lease is not None
                try:
                    state = service.read_tool_instance_state("diff")
                    self.assertIsNotNone(state)
                    assert state is not None
                    self.assertEqual(state.tool_key, "diff")
                    self.assertEqual(state.pid, os.getpid())
                    self.assertGreater(state.port, 0)
                    self.assertTrue(state.token)
                finally:
                    result.lease.release()
                self.assertIsNone(service.read_tool_instance_state("diff"))

    def test_activate_tool_instance_notifies_existing_listener(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            events: list[dict[str, object] | None] = []
            lock = threading.Lock()

            def _on_activate(payload: dict[str, object] | None) -> bool:
                with lock:
                    events.append(payload)
                return True

            with patch.dict(os.environ, {"SNAKESH_DATA_DIR": tmp}, clear=False):
                result = service.claim_tool_instance("help", on_activate=_on_activate)
                assert result.lease is not None
                try:
                    activated = service.activate_tool_instance("help", payload={"source": "main"})
                    self.assertTrue(activated)
                    self.assertTrue(self._wait_for(lambda: len(events) == 1))
                    self.assertEqual(events, [{"source": "main"}])
                finally:
                    result.lease.release()

    def test_unreachable_tool_instance_is_removed_and_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"SNAKESH_DATA_DIR": tmp}, clear=False):
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                    sock.bind(("127.0.0.1", 0))
                    stale_port = int(sock.getsockname()[1])
                stale_state = service.ToolInstanceState(
                    tool_key="password_generator",
                    pid=os.getpid(),
                    port=stale_port,
                    token="stale-token",
                )
                state_path = service.tool_instance_state_path("password_generator")
                state_path.write_text(json.dumps(stale_state.to_dict()), encoding="utf-8")

                self.assertFalse(service.activate_tool_instance("password_generator"))
                self.assertFalse(state_path.exists())

                result = service.claim_tool_instance("password_generator", on_activate=lambda _payload: True)
                self.assertIsNotNone(result.lease)
                assert result.lease is not None
                try:
                    replacement = service.read_tool_instance_state("password_generator")
                    self.assertIsNotNone(replacement)
                    assert replacement is not None
                    self.assertNotEqual(replacement.port, stale_port)
                    self.assertNotEqual(replacement.token, stale_state.token)
                finally:
                    result.lease.release()

    def test_concurrent_claims_yield_one_primary_instance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            results: list[service.ToolInstanceClaimResult] = []
            activations: list[dict[str, object] | None] = []
            results_lock = threading.Lock()
            activation_lock = threading.Lock()
            barrier = threading.Barrier(2)

            def _on_activate(payload: dict[str, object] | None) -> bool:
                with activation_lock:
                    activations.append(payload)
                return True

            def _worker() -> None:
                barrier.wait()
                result = service.claim_tool_instance("resource_monitor", on_activate=_on_activate)
                with results_lock:
                    results.append(result)

            with patch.dict(os.environ, {"SNAKESH_DATA_DIR": tmp}, clear=False):
                first = threading.Thread(target=_worker, daemon=True)
                second = threading.Thread(target=_worker, daemon=True)
                first.start()
                second.start()
                first.join(timeout=5.0)
                second.join(timeout=5.0)

                self.assertEqual(len(results), 2)
                leases = [result.lease for result in results if result.lease is not None]
                activated = [result for result in results if result.activated_existing]
                self.assertEqual(len(leases), 1)
                self.assertEqual(len(activated), 1)
                self.assertTrue(self._wait_for(lambda: len(activations) == 1))
                leases[0].release()

    def test_activate_active_tool_instances_notifies_only_running_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            help_events: list[dict[str, object] | None] = []
            diff_events: list[dict[str, object] | None] = []

            with patch.dict(os.environ, {"SNAKESH_DATA_DIR": tmp}, clear=False):
                help_result = service.claim_tool_instance("help", on_activate=lambda payload: help_events.append(payload) or True)
                diff_result = service.claim_tool_instance("diff", on_activate=lambda payload: diff_events.append(payload) or True)
                assert help_result.lease is not None
                assert diff_result.lease is not None
                try:
                    results = service.activate_active_tool_instances(
                        payload_factory=lambda tool_key: {"kind": "settings_sync", "target": tool_key}
                    )

                    self.assertEqual(set(results), {"help", "diff"})
                    self.assertTrue(self._wait_for(lambda: len(help_events) == 1 and len(diff_events) == 1))
                    self.assertEqual(help_events, [{"kind": "settings_sync", "target": "help"}])
                    self.assertEqual(diff_events, [{"kind": "settings_sync", "target": "diff"}])
                finally:
                    help_result.lease.release()
                    diff_result.lease.release()

    def test_windows_process_is_running_uses_process_handle_without_os_kill(self) -> None:
        class _FakeKernel32:
            def __init__(self) -> None:
                self.closed_handles: list[int] = []

            def OpenProcess(self, _access, _inherit_handle, pid):  # noqa: ANN001
                self.opened_pid = pid
                return 100

            def GetExitCodeProcess(self, _handle, exit_code_ptr):  # noqa: ANN001
                exit_code_ptr._obj.value = 259
                return 1

            def CloseHandle(self, handle):  # noqa: ANN001
                self.closed_handles.append(handle)
                return 1

        fake_kernel32 = _FakeKernel32()
        with (
            patch("snakesh.services._instance_activation.os.name", "nt"),
            patch("ctypes.WinDLL", return_value=fake_kernel32, create=True),
            patch("snakesh.services._instance_activation.os.kill", side_effect=SystemError("invalid handle")) as mock_kill,
        ):
            self.assertTrue(activation_service.process_is_running(12345))

        self.assertEqual(fake_kernel32.opened_pid, 12345)
        self.assertEqual(fake_kernel32.closed_handles, [100])
        mock_kill.assert_not_called()

    def test_windows_process_is_running_returns_false_when_open_process_fails(self) -> None:
        fake_kernel32 = SimpleNamespace(OpenProcess=lambda *_args: 0)
        with (
            patch("snakesh.services._instance_activation.os.name", "nt"),
            patch("ctypes.WinDLL", return_value=fake_kernel32, create=True),
            patch("ctypes.get_last_error", return_value=6, create=True),
        ):
            self.assertFalse(activation_service.process_is_running(12345))


if __name__ == "__main__":
    unittest.main()
