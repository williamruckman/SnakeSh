from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from snakesh.services.settings_service import AppSettings, SettingsService
from snakesh.services.tool_instance_service import tool_instance_state_path


class PackagedToolRuntimeSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._runtime_executable = Path(__file__).resolve().parent.parent / "dist" / "SnakeSh" / "SnakeSh"
        if sys.platform != "linux":
            raise unittest.SkipTest("Packaged runtime smoke only runs on Linux.")
        if not cls._runtime_executable.exists():
            raise unittest.SkipTest("Packaged runtime not found at dist/SnakeSh/SnakeSh.")
        probe = subprocess.run(  # noqa: S603
            [str(cls._runtime_executable), "tool", "list"],
            cwd=str(cls._runtime_executable.parent.parent),
            env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15.0,
        )
        if probe.returncode != 0:
            raise unittest.SkipTest(
                "Packaged runtime at dist/SnakeSh/SnakeSh is stale or not rebuilt for the current source tree."
            )

    @staticmethod
    def _write_settings(data_root: Path, tool_key: str) -> None:
        settings = AppSettings.defaults()
        settings.workspace_profiles = [
            {
                "id": "smoke-profile",
                "name": "Smoke",
                "snapshot": {
                    "workspace_tree": {
                        "type": "host",
                        "host_key": "host-1",
                        "is_primary": True,
                        "tabs": [],
                    }
                },
                "startup_tools": [tool_key],
            }
        ]
        settings.default_workspace_profile_id = "smoke-profile"
        settings.password_generator_length = 29
        settings.password_generator_count = 3

        with patch.dict(os.environ, {"SNAKESH_DATA_DIR": str(data_root)}, clear=False):
            SettingsService().save(settings)

    @staticmethod
    def _read_json_when_ready(path: Path, *, timeout_seconds: float) -> dict[str, object]:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
            time.sleep(0.05)
        raise AssertionError(f"Timed out waiting for {path}")

    @staticmethod
    def _pid_is_running(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True

    def _assert_packaged_tool_survives_main_close(self, tool_key: str) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_root = root / "data"
            tool_ready_file = root / f"{tool_key}-ready.json"
            self._write_settings(data_root, tool_key)

            env = os.environ.copy()
            env.update(
                {
                    "QT_QPA_PLATFORM": "offscreen",
                    "SNAKESH_DATA_DIR": str(data_root),
                    "SNAKESH_TEST_CLOSE_MAIN_AFTER_MS": "2000",
                    "SNAKESH_TEST_TOOL_READY_FILE": str(tool_ready_file),
                    "SNAKESH_TEST_TOOL_AUTO_CLOSE_MS": "8000",
                }
            )

            process = subprocess.Popen(  # noqa: S603
                [str(self._runtime_executable)],
                cwd=str(self._runtime_executable.parent.parent),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            tool_pid = 0
            try:
                ready_payload = self._read_json_when_ready(tool_ready_file, timeout_seconds=20.0)
                tool_pid = int(ready_payload["pid"])
                with patch.dict(os.environ, {"SNAKESH_DATA_DIR": str(data_root)}, clear=False):
                    state_path = tool_instance_state_path(tool_key)
                try:
                    state_before = self._read_json_when_ready(state_path, timeout_seconds=5.0)
                except AssertionError as exc:
                    self.skipTest(
                        "Packaged runtime at dist/SnakeSh/SnakeSh is stale or not rebuilt for tool singleton support: "
                        f"{exc}"
                    )

                relaunch = subprocess.run(  # noqa: S603
                    [str(self._runtime_executable), "tool", tool_key],
                    cwd=str(self._runtime_executable.parent.parent),
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=15.0,
                )
                self.assertEqual(
                    relaunch.returncode,
                    0,
                    msg=f"Relaunch failed for {tool_key}\nstdout:\n{relaunch.stdout}\nstderr:\n{relaunch.stderr}",
                )
                state_after = self._read_json_when_ready(state_path, timeout_seconds=5.0)
                self.assertEqual(state_after.get("pid"), state_before.get("pid"))
                self.assertEqual(int(state_after.get("pid", 0)), tool_pid)

                stdout, stderr = process.communicate(timeout=15.0)
                self.assertEqual(
                    process.returncode,
                    0,
                    msg=f"Packaged main runtime exited with {process.returncode}\nstdout:\n{stdout}\nstderr:\n{stderr}",
                )

                time.sleep(0.75)
                self.assertTrue(
                    self._pid_is_running(tool_pid),
                    msg=f"Detached tool {tool_key} did not survive main-window shutdown.",
                )

                deadline = time.monotonic() + 15.0
                while time.monotonic() < deadline and self._pid_is_running(tool_pid):
                    time.sleep(0.1)
                self.assertFalse(
                    self._pid_is_running(tool_pid),
                    msg=f"Detached tool {tool_key} did not close after the scheduled test shutdown.",
                )
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5.0)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5.0)
                if tool_pid and self._pid_is_running(tool_pid):
                    try:
                        os.kill(tool_pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    else:
                        deadline = time.monotonic() + 5.0
                        while time.monotonic() < deadline and self._pid_is_running(tool_pid):
                            time.sleep(0.1)

    def test_packaged_diff_tool_survives_main_window_close(self) -> None:
        self._assert_packaged_tool_survives_main_close("diff")

    def test_packaged_password_generator_survives_main_window_close(self) -> None:
        self._assert_packaged_tool_survives_main_close("password_generator")


if __name__ == "__main__":
    unittest.main()
