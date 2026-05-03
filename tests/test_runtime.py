from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

from snakesh import runtime


class RuntimeHelperTests(unittest.TestCase):
    def test_self_launch_command_uses_module_mode_when_not_frozen(self) -> None:
        with (
            patch("snakesh.runtime.is_frozen", return_value=False),
            patch("snakesh.runtime.appimage_path", return_value=None),
        ):
            command = runtime.self_launch_command(["--web-server-helper", "/tmp/instance"])

        self.assertEqual(command[:2], [sys.executable, "-m"])
        self.assertEqual(command[2:], ["snakesh", "--web-server-helper", "/tmp/instance"])

    def test_self_launch_command_uses_executable_when_frozen(self) -> None:
        with (
            patch("snakesh.runtime.is_frozen", return_value=True),
            patch("snakesh.runtime.appimage_path", return_value=None),
            patch("snakesh.runtime.executable_path", return_value=runtime.Path("/opt/SnakeSh/SnakeSh")),
        ):
            command = runtime.self_launch_command(["--web-server-helper", "/tmp/instance"])

        self.assertEqual(command, ["/opt/SnakeSh/SnakeSh", "--web-server-helper", "/tmp/instance"])

    def test_self_launch_command_uses_appimage_path_when_available(self) -> None:
        with (
            patch("snakesh.runtime.is_frozen", return_value=True),
            patch(
                "snakesh.runtime.appimage_path",
                return_value=runtime.Path("/home/user/.local/lib/SnakeSh/SnakeSh.AppImage"),
            ),
            patch("snakesh.runtime.executable_path", return_value=runtime.Path("/tmp/.mount_SnakeSh/usr/lib/snakesh/SnakeSh")),
        ):
            command = runtime.self_launch_command(["--mtr-helper", "/tmp/session"])

        self.assertEqual(
            command,
            ["/home/user/.local/lib/SnakeSh/SnakeSh.AppImage", "--mtr-helper", "/tmp/session"],
        )

    def test_sanitized_local_shell_environment_removes_bundle_owned_library_and_qt_paths(self) -> None:
        mount_root = "/tmp/.mount_SnakeSh12345"
        env = {
            "APPDIR": mount_root,
            "LD_LIBRARY_PATH": os.pathsep.join(
                (
                    f"{mount_root}/usr/lib/snakesh/_internal/PySide6/Qt/lib",
                    "/usr/lib",
                    f"{mount_root}/usr/lib/snakesh/_internal",
                    "/opt/custom/lib",
                )
            ),
            "QT_PLUGIN_PATH": os.pathsep.join(
                (
                    f"{mount_root}/usr/lib/snakesh/_internal/PySide6/Qt/plugins",
                    f"{mount_root}/usr/lib/snakesh/_internal/PySide6/Qt/plugins/imageformats",
                )
            ),
            "QT_QPA_PLATFORM_PLUGIN_PATH": f"{mount_root}/usr/lib/snakesh/_internal/PySide6/Qt/plugins/platforms",
            "PATH": os.pathsep.join((f"{mount_root}/usr/bin", "/usr/bin", "/bin")),
        }

        sanitized = runtime.sanitized_local_shell_environment(env)

        self.assertEqual(sanitized["LD_LIBRARY_PATH"], os.pathsep.join(("/usr/lib", "/opt/custom/lib")))
        self.assertNotIn("QT_PLUGIN_PATH", sanitized)
        self.assertNotIn("QT_QPA_PLATFORM_PLUGIN_PATH", sanitized)
        self.assertEqual(sanitized["PATH"], os.pathsep.join(("/usr/bin", "/bin")))
        self.assertEqual(sanitized["APPDIR"], mount_root)

    def test_sanitized_local_shell_environment_replaces_bundle_only_path_with_host_default(self) -> None:
        if os.name != "posix":
            self.skipTest("bundle-only PATH fallback is POSIX-specific")
        mount_root = "/tmp/.mount_SnakeSh12345"
        env = {
            "APPDIR": mount_root,
            "PATH": os.pathsep.join((f"{mount_root}/usr/bin", f"{mount_root}/usr/sbin")),
        }

        sanitized = runtime.sanitized_local_shell_environment(env)

        self.assertEqual(sanitized["PATH"], runtime._DEFAULT_POSIX_EXEC_PATH)

    def test_sanitized_local_shell_environment_preserves_unrelated_host_paths(self) -> None:
        env = {
            "LD_LIBRARY_PATH": os.pathsep.join(("/usr/lib", "/opt/custom/lib")),
            "QT_PLUGIN_PATH": "/opt/custom/qt/plugins",
            "QT_QPA_PLATFORM_PLUGIN_PATH": "/opt/custom/qt/plugins/platforms",
        }

        sanitized = runtime.sanitized_local_shell_environment(env)

        self.assertEqual(sanitized["LD_LIBRARY_PATH"], env["LD_LIBRARY_PATH"])
        self.assertEqual(sanitized["QT_PLUGIN_PATH"], env["QT_PLUGIN_PATH"])
        self.assertEqual(sanitized["QT_QPA_PLATFORM_PLUGIN_PATH"], env["QT_QPA_PLATFORM_PLUGIN_PATH"])

    def test_sanitized_self_launch_environment_removes_bundle_paths_and_parent_runtime_markers(self) -> None:
        mount_root = "/tmp/.mount_SnakeSh12345"
        env = {
            "APPDIR": mount_root,
            "APPIMAGE": f"{mount_root}/SnakeSh.AppImage",
            "LD_LIBRARY_PATH": os.pathsep.join(
                (
                    f"{mount_root}/usr/lib/snakesh/_internal/PySide6/Qt/lib",
                    "/usr/lib",
                    f"{mount_root}/usr/lib/snakesh/_internal",
                    "/opt/custom/lib",
                )
            ),
            "QT_PLUGIN_PATH": f"{mount_root}/usr/lib/snakesh/_internal/PySide6/Qt/plugins",
            "QT_QPA_PLATFORM_PLUGIN_PATH": f"{mount_root}/usr/lib/snakesh/_internal/PySide6/Qt/plugins/platforms",
            "_MEIPASS2": f"{mount_root}/usr/lib/snakesh/_internal",
            "_PYI_APPLICATION_HOME_DIR": f"{mount_root}/usr/lib/snakesh",
            "_PYI_ARCHIVE_FILE": f"{mount_root}/usr/lib/snakesh/SnakeSh",
            "_PYI_PARENT_PROCESS_LEVEL": "1",
            "_PYI_SPLASH_IPC": "socket-name",
            "_PYI_LINUX_PROCESS_NAME": "snakesh-parent",
        }

        with patch("snakesh.runtime.is_frozen", return_value=True):
            sanitized = runtime.sanitized_self_launch_environment(env)

        self.assertEqual(sanitized["LD_LIBRARY_PATH"], os.pathsep.join(("/usr/lib", "/opt/custom/lib")))
        self.assertNotIn("APPDIR", sanitized)
        self.assertNotIn("APPIMAGE", sanitized)
        self.assertNotIn("QT_PLUGIN_PATH", sanitized)
        self.assertNotIn("QT_QPA_PLATFORM_PLUGIN_PATH", sanitized)
        self.assertNotIn("_MEIPASS2", sanitized)
        self.assertNotIn("_PYI_APPLICATION_HOME_DIR", sanitized)
        self.assertNotIn("_PYI_ARCHIVE_FILE", sanitized)
        self.assertNotIn("_PYI_PARENT_PROCESS_LEVEL", sanitized)
        self.assertNotIn("_PYI_SPLASH_IPC", sanitized)
        self.assertNotIn("_PYI_LINUX_PROCESS_NAME", sanitized)
        self.assertEqual(sanitized["PYINSTALLER_RESET_ENVIRONMENT"], "1")

    def test_sanitized_self_launch_environment_preserves_unrelated_host_paths(self) -> None:
        env = {
            "LD_LIBRARY_PATH": os.pathsep.join(("/usr/lib", "/opt/custom/lib")),
            "QT_PLUGIN_PATH": "/opt/custom/qt/plugins",
            "QT_QPA_PLATFORM_PLUGIN_PATH": "/opt/custom/qt/plugins/platforms",
            "PATH": "/usr/bin",
        }

        with patch("snakesh.runtime.is_frozen", return_value=False):
            sanitized = runtime.sanitized_self_launch_environment(env)

        self.assertEqual(sanitized["LD_LIBRARY_PATH"], env["LD_LIBRARY_PATH"])
        self.assertEqual(sanitized["QT_PLUGIN_PATH"], env["QT_PLUGIN_PATH"])
        self.assertEqual(sanitized["QT_QPA_PLATFORM_PLUGIN_PATH"], env["QT_QPA_PLATFORM_PLUGIN_PATH"])
        self.assertEqual(sanitized["PATH"], env["PATH"])
        self.assertNotIn("PYINSTALLER_RESET_ENVIRONMENT", sanitized)


if __name__ == "__main__":
    unittest.main()
