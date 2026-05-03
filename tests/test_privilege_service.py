from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from snakesh.services import privilege_service
from snakesh.services.privilege_service import CommandResult


class PrivilegeServiceTests(unittest.TestCase):
    def test_run_command_routes_windows_elevation(self) -> None:
        expected = CommandResult(success=True, message="ok")
        with (
            patch("snakesh.services.privilege_service.platform.system", return_value="Windows"),
            patch("snakesh.services.privilege_service._run_windows_elevated", return_value=expected) as mock_runner,
        ):
            result = privilege_service.run_command(["taskkill", "/PID", "123"], require_elevation=True)

        self.assertIs(result, expected)
        mock_runner.assert_called_once_with(["taskkill", "/PID", "123"])

    def test_run_command_routes_linux_elevation(self) -> None:
        expected = CommandResult(success=True, message="ok")
        with (
            patch("snakesh.services.privilege_service.platform.system", return_value="Linux"),
            patch("snakesh.services.privilege_service._run_linux_elevated", return_value=expected) as mock_runner,
        ):
            result = privilege_service.run_command(["/bin/kill", "-TERM", "123"], require_elevation=True, timeout=5.0)

        self.assertIs(result, expected)
        mock_runner.assert_called_once_with(["/bin/kill", "-TERM", "123"], timeout=5.0)

    def test_run_command_routes_macos_elevation(self) -> None:
        expected = CommandResult(success=True, message="ok")
        with (
            patch("snakesh.services.privilege_service.platform.system", return_value="Darwin"),
            patch("snakesh.services.privilege_service._run_macos_elevated", return_value=expected) as mock_runner,
        ):
            result = privilege_service.run_command(["/bin/kill", "-TERM", "123"], require_elevation=True, timeout=7.0)

        self.assertIs(result, expected)
        mock_runner.assert_called_once_with(["/bin/kill", "-TERM", "123"], timeout=7.0)

    def test_is_elevated_uses_geteuid_on_macos(self) -> None:
        with (
            patch("snakesh.services.privilege_service.platform.system", return_value="Darwin"),
            patch("snakesh.services.privilege_service.os.geteuid", return_value=0),
        ):
            self.assertTrue(privilege_service.is_elevated())

    def test_run_macos_elevated_uses_osascript_and_marks_cancelled(self) -> None:
        with (
            patch("snakesh.services.privilege_service.platform.system", return_value="Darwin"),
            patch("snakesh.services.privilege_service.is_elevated", return_value=False),
            patch("snakesh.services.privilege_service.shutil.which", return_value="/usr/bin/osascript"),
            patch(
                "snakesh.services.privilege_service.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    args=["osascript"],
                    returncode=1,
                    stdout="",
                    stderr="User canceled.",
                ),
            ) as mock_run,
        ):
            result = privilege_service.run_command(["/bin/kill", "-TERM", "123"], require_elevation=True)

        self.assertFalse(result.success)
        self.assertTrue(result.elevated)
        self.assertTrue(result.cancelled)
        self.assertEqual(result.message, "Elevation was cancelled by the user.")
        self.assertEqual(mock_run.call_args.args[0][0], "osascript")
        self.assertIn("with administrator privileges", mock_run.call_args.args[0][2])


if __name__ == "__main__":
    unittest.main()
