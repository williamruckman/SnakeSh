from __future__ import annotations

import json
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from snakesh.services import main_instance_service as service


class MainInstanceServiceTests(unittest.TestCase):
    @staticmethod
    def _wait_for(predicate, *, timeout_seconds: float = 3.0) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.02)
        return bool(predicate())

    def test_claim_main_instance_creates_state_and_lease(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"SNAKESH_DATA_DIR": tmp}, clear=False):
                result = service.claim_main_instance(on_activate=lambda _payload: True)
                self.assertFalse(result.activated_existing)
                self.assertIsNotNone(result.lease)
                assert result.lease is not None
                try:
                    state = service.read_main_instance_state()
                    self.assertIsNotNone(state)
                    assert state is not None
                    self.assertEqual(state.pid, os.getpid())
                    self.assertGreater(state.port, 0)
                    self.assertTrue(state.token)
                finally:
                    result.lease.release()
                self.assertIsNone(service.read_main_instance_state())

    def test_activate_existing_main_instance_notifies_listener_with_import_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            events: list[dict[str, object] | None] = []
            lock = threading.Lock()
            import_path = Path(tmp) / "launch.ssx"

            def _on_activate(payload: dict[str, object] | None) -> bool:
                with lock:
                    events.append(payload)
                return True

            with patch.dict(os.environ, {"SNAKESH_DATA_DIR": tmp}, clear=False):
                result = service.claim_main_instance(on_activate=_on_activate)
                assert result.lease is not None
                try:
                    activated = service.activate_existing_main_instance(str(import_path))
                    self.assertTrue(activated)
                    self.assertTrue(self._wait_for(lambda: len(events) == 1))
                    self.assertEqual(events, [{"import_file": str(import_path.resolve())}])
                finally:
                    result.lease.release()

    def test_unreachable_main_instance_is_removed_and_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"SNAKESH_DATA_DIR": tmp}, clear=False):
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                    sock.bind(("127.0.0.1", 0))
                    stale_port = int(sock.getsockname()[1])
                state_path = service.main_instance_state_path()
                state_path.write_text(
                    json.dumps(
                        {
                            "app_key": "main",
                            "pid": os.getpid(),
                            "port": stale_port,
                            "token": "stale-token",
                        }
                    ),
                    encoding="utf-8",
                )

                self.assertFalse(service.activate_existing_main_instance())
                self.assertFalse(state_path.exists())

                result = service.claim_main_instance(on_activate=lambda _payload: True)
                self.assertIsNotNone(result.lease)
                assert result.lease is not None
                try:
                    replacement = service.read_main_instance_state()
                    self.assertIsNotNone(replacement)
                    assert replacement is not None
                    self.assertNotEqual(replacement.port, stale_port)
                    self.assertNotEqual(replacement.token, "stale-token")
                finally:
                    result.lease.release()

    def test_concurrent_claims_yield_one_primary_instance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            results: list[service.MainInstanceClaimResult] = []
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
                result = service.claim_main_instance(on_activate=_on_activate)
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


if __name__ == "__main__":
    unittest.main()
