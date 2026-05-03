from __future__ import annotations

import plistlib
import re
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from snakesh.services import tool_launcher_service as service


class ToolLauncherServiceTests(unittest.TestCase):
    def test_linux_sync_installs_and_removes_tool_desktop_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir(parents=True)
            paths = service.integration_paths(home=home)

            def _fake_linux_install(*, home: Path | None = None) -> Path:
                install_paths = service.integration_paths(home=home)
                install_paths.install_dir.mkdir(parents=True, exist_ok=True)
                install_paths.desktop_file.parent.mkdir(parents=True, exist_ok=True)
                install_paths.installed_appimage.write_bytes(b"appimage")
                install_paths.installed_appimage.chmod(0o755)
                install_paths.launcher_icon_file.write_bytes(b"png")
                return install_paths.installed_appimage

            def _integration_installed(*, home: Path | None = None) -> bool:
                install_paths = service.integration_paths(home=home)
                return install_paths.installed_appimage.exists() and install_paths.launcher_icon_file.exists()

            with (
                patch(
                    "snakesh.services.tool_launcher_service.install_desktop_integration",
                    side_effect=_fake_linux_install,
                ),
                patch(
                    "snakesh.services.tool_launcher_service.is_desktop_integration_installed",
                    side_effect=_integration_installed,
                ),
                patch("snakesh.services.tool_launcher_service._refresh_linux_desktop_index"),
            ):
                result = service.sync_tool_launchers(["ping", "help"], platform_name="linux", home=home)

                self.assertEqual(result.installed_keys, ("ping", "help"))
                self.assertEqual(service.installed_tool_launcher_keys(platform_name="linux", home=home), ["ping", "help"])

                ping_desktop = service._linux_tool_desktop_path("ping", home=home)
                help_desktop = service._linux_tool_desktop_path("help", home=home)
                self.assertTrue(ping_desktop.exists())
                self.assertTrue(help_desktop.exists())
                ping_payload = ping_desktop.read_text(encoding="utf-8")
                ping_icon_path = service._linux_tool_icon_path("ping", home=home)
                help_icon_path = service._linux_tool_icon_path("help", home=home)
                self.assertTrue(ping_icon_path.exists())
                self.assertTrue(help_icon_path.exists())
                self.assertIn("tool ping", ping_payload)
                self.assertIn(str(paths.installed_appimage), ping_payload)
                self.assertIn(str(ping_icon_path), ping_payload)
                self.assertNotIn(str(paths.launcher_icon_file), ping_payload)
                self.assertIn("StartupWMClass=snakesh-tool-ping", ping_payload)
                self.assertIn(f"{service._TOOL_KEY_KEY}=ping", ping_payload)

                remove_result = service.sync_tool_launchers(["help"], platform_name="linux", home=home)

                self.assertEqual(remove_result.removed_keys, ("ping",))
                self.assertEqual(service.installed_tool_launcher_keys(platform_name="linux", home=home), ["help"])
                self.assertFalse(ping_desktop.exists())
                self.assertFalse(ping_icon_path.exists())
                self.assertTrue(help_desktop.exists())
                self.assertTrue(help_icon_path.exists())

    def test_windows_sync_creates_and_removes_start_menu_shortcuts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            appdata = Path(tmp) / "AppData" / "Roaming"
            created_shortcuts: list[tuple[Path, str, str, str, str]] = []

            def _fake_shortcut(
                path: Path,
                *,
                target: str,
                arguments: str,
                description: str,
                icon_location: str,
                app_user_model_id: str = "",
            ) -> None:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    "\n".join((target, arguments, description, icon_location, app_user_model_id)),
                    encoding="utf-8",
                )
                created_shortcuts.append((path, target, arguments, description, icon_location))

            with (
                patch(
                    "snakesh.services.tool_launcher_service.runtime.self_launch_command",
                    side_effect=lambda args=None: ["C:/Program Files/SnakeSh/SnakeSh.exe", *(args or [])],
                ),
                patch(
                    "snakesh.services.tool_launcher_service._create_windows_shortcut",
                    side_effect=_fake_shortcut,
                ),
            ):
                result = service.sync_tool_launchers(["ping"], platform_name="windows", appdata=appdata)

                self.assertEqual(result.installed_keys, ("ping",))
                self.assertEqual(service.installed_tool_launcher_keys(platform_name="windows", appdata=appdata), ["ping"])
                shortcut_path = service._windows_shortcut_path("ping", appdata=appdata)
                self.assertTrue(shortcut_path.exists())
                payload = shortcut_path.read_text(encoding="utf-8")
                self.assertIn("SnakeSh.exe", payload)
                self.assertIn("tool ping", payload)
                self.assertEqual(created_shortcuts[0][3], "Open the Ping tool")
                self.assertTrue(created_shortcuts[0][4].endswith("ping.ico"))
                self.assertNotIn("snakesh-icon.ico", created_shortcuts[0][4])
                self.assertIn("com.snakesh.tool.ping", payload)

                remove_result = service.sync_tool_launchers([], platform_name="windows", appdata=appdata)

                self.assertEqual(remove_result.removed_keys, ("ping",))
                self.assertEqual(service.installed_tool_launcher_keys(platform_name="windows", appdata=appdata), [])
                self.assertFalse(shortcut_path.exists())

    def test_windows_sync_repairs_matching_pinned_taskbar_shortcuts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            appdata = Path(tmp) / "AppData" / "Roaming"
            pinned_shortcut = service._windows_pinned_shortcut_path("ping", appdata=appdata)
            pinned_shortcut.parent.mkdir(parents=True)
            pinned_shortcut.write_text("pinned", encoding="utf-8")
            repaired: list[tuple[Path, str]] = []

            def _fake_shortcut(
                path: Path,
                *,
                target: str,
                arguments: str,
                description: str,
                icon_location: str,
                app_user_model_id: str = "",
            ) -> None:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(app_user_model_id, encoding="utf-8")

            with (
                patch(
                    "snakesh.services.tool_launcher_service.runtime.self_launch_command",
                    side_effect=lambda args=None: ["C:/Program Files/SnakeSh/SnakeSh.exe", *(args or [])],
                ),
                patch(
                    "snakesh.services.tool_launcher_service._create_windows_shortcut",
                    side_effect=_fake_shortcut,
                ),
                patch(
                    "snakesh.services.tool_launcher_service._set_windows_shortcut_app_user_model_id",
                    side_effect=lambda path, app_id: repaired.append((path, app_id)),
                ),
            ):
                service.sync_tool_launchers(["ping"], platform_name="windows", appdata=appdata)

            self.assertEqual(repaired, [(pinned_shortcut, "com.snakesh.tool.ping")])

    def test_remove_tool_launchers_removes_linux_entries_without_installing_desktop_integration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir(parents=True)
            applications_dir = service.integration_paths(home=home).desktop_file.parent
            applications_dir.mkdir(parents=True)
            ping_desktop = service._linux_tool_desktop_path("ping", home=home)
            help_desktop = service._linux_tool_desktop_path("help", home=home)
            ping_icon = service._linux_tool_icon_path("ping", home=home)
            help_icon = service._linux_tool_icon_path("help", home=home)
            ping_icon.parent.mkdir(parents=True, exist_ok=True)
            ping_desktop.write_text("[Desktop Entry]\nX-SnakeSh-Tool-Key=ping\n", encoding="utf-8")
            help_desktop.write_text("[Desktop Entry]\nX-SnakeSh-Tool-Key=help\n", encoding="utf-8")
            ping_icon.write_bytes(b"png")
            help_icon.write_bytes(b"png")

            with (
                patch(
                    "snakesh.services.tool_launcher_service.install_desktop_integration",
                    side_effect=AssertionError("remove-only cleanup must not install desktop integration"),
                ),
                patch("snakesh.services.tool_launcher_service._refresh_linux_desktop_index"),
            ):
                result = service.remove_tool_launchers(platform_name="linux", home=home)

            self.assertEqual(result.removed_keys, ("ping", "help"))
            self.assertFalse(ping_desktop.exists())
            self.assertFalse(help_desktop.exists())
            self.assertFalse(ping_icon.exists())
            self.assertFalse(help_icon.exists())

    def test_remove_tool_launchers_removes_windows_start_menu_shortcuts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            appdata = Path(tmp) / "AppData" / "Roaming"
            ping_shortcut = service._windows_shortcut_path("ping", appdata=appdata)
            help_shortcut = service._windows_shortcut_path("help", appdata=appdata)
            ping_shortcut.parent.mkdir(parents=True)
            ping_shortcut.write_text("ping", encoding="utf-8")
            help_shortcut.write_text("help", encoding="utf-8")

            result = service.remove_tool_launchers(platform_name="windows", appdata=appdata)

            self.assertEqual(result.removed_keys, ("ping", "help"))
            self.assertFalse(ping_shortcut.exists())
            self.assertFalse(help_shortcut.exists())
            self.assertFalse(ping_shortcut.parent.exists())

    def test_run_powershell_hides_windows_console_window(self) -> None:
        class _FakeStartupInfo:
            def __init__(self) -> None:
                self.dwFlags = 0
                self.wShowWindow = 1

        completed = unittest.mock.Mock(returncode=0, stdout="", stderr="")
        with (
            patch.object(service.os, "name", "nt"),
            patch.object(service.subprocess, "run", return_value=completed) as mock_run,
            patch.object(service.subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True),
            patch.object(service.subprocess, "STARTUPINFO", _FakeStartupInfo, create=True),
            patch.object(service.subprocess, "STARTF_USESHOWWINDOW", 1, create=True),
            patch.object(service.subprocess, "SW_HIDE", 0, create=True),
        ):
            service._run_powershell("Write-Output 'ok'")

        kwargs = mock_run.call_args.kwargs
        self.assertEqual(kwargs["creationflags"], 0x08000000)
        self.assertEqual(getattr(kwargs["startupinfo"], "dwFlags", 0), 1)
        self.assertEqual(getattr(kwargs["startupinfo"], "wShowWindow", 1), 0)

    def test_windows_shortcut_app_user_model_id_script_uses_valid_guids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            shortcut = Path(tmp) / "SnakeSh - Ping.lnk"
            shortcut.write_text("shortcut", encoding="utf-8")
            scripts: list[str] = []

            with patch("snakesh.services.tool_launcher_service._run_powershell", side_effect=scripts.append):
                service._set_windows_shortcut_app_user_model_id(shortcut, "com.snakesh.tool.ping")

            self.assertEqual(len(scripts), 1)
            guid_values = re.findall(r'Guid\("([^"]+)"\)', scripts[0])
            self.assertIn(service._WINDOWS_PROPERTY_STORE_IID, guid_values)
            self.assertIn(service._WINDOWS_APP_USER_MODEL_ID_FMTID, guid_values)
            for value in guid_values:
                uuid.UUID(value)

    def test_macos_sync_creates_and_removes_app_bundle_launchers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir(parents=True)

            with patch(
                "snakesh.services.tool_launcher_service.runtime.self_launch_command",
                side_effect=lambda args=None: ["/Applications/SnakeSh.app/Contents/MacOS/SnakeSh", *(args or [])],
            ):
                result = service.sync_tool_launchers(["ping"], platform_name="macos", home=home)

                self.assertEqual(result.installed_keys, ("ping",))
                self.assertEqual(service.installed_tool_launcher_keys(platform_name="macos", home=home), ["ping"])

                bundle_path = service._macos_bundle_path("ping", home=home)
                info_path = bundle_path / "Contents" / "Info.plist"
                executable_dir = bundle_path / "Contents" / "MacOS"
                executable_name = service._sanitize_filename(service._tool_launcher_display_name("ping"))
                launcher_path = executable_dir / executable_name
                icon_path = bundle_path / "Contents" / "Resources" / "ping.icns"

                self.assertTrue(info_path.exists())
                self.assertTrue(launcher_path.exists())
                self.assertTrue(icon_path.exists())
                info_payload = plistlib.loads(info_path.read_bytes())
                self.assertEqual(info_payload["SnakeShToolKey"], "ping")
                self.assertEqual(info_payload["CFBundleIconFile"], "ping.icns")
                self.assertIn("tool ping", launcher_path.read_text(encoding="utf-8"))

                remove_result = service.sync_tool_launchers([], platform_name="macos", home=home)

                self.assertEqual(remove_result.removed_keys, ("ping",))
                self.assertEqual(service.installed_tool_launcher_keys(platform_name="macos", home=home), [])
                self.assertFalse(bundle_path.exists())

    def test_remove_tool_launchers_removes_macos_app_bundle_launchers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir(parents=True)

            with patch(
                "snakesh.services.tool_launcher_service.runtime.self_launch_command",
                side_effect=lambda args=None: ["/Applications/SnakeSh.app/Contents/MacOS/SnakeSh", *(args or [])],
            ):
                service.sync_tool_launchers(["ping", "help"], platform_name="macos", home=home)

            ping_bundle = service._macos_bundle_path("ping", home=home)
            help_bundle = service._macos_bundle_path("help", home=home)

            result = service.remove_tool_launchers(platform_name="macos", home=home)

            self.assertEqual(result.removed_keys, ("ping", "help"))
            self.assertFalse(ping_bundle.exists())
            self.assertFalse(help_bundle.exists())
            self.assertFalse(service._macos_tool_applications_dir(home=home).exists())

    def test_launcher_sync_summary_reports_installed_and_removed_labels(self) -> None:
        result = service.ToolLauncherSyncResult(
            selected_keys=("ping", "help"),
            installed_keys=("ping",),
            removed_keys=("help",),
        )

        summary = service.launcher_sync_summary(result)

        self.assertIn("Installed: Ping", summary)
        self.assertIn("Removed: Help", summary)


if __name__ == "__main__":
    unittest.main()
