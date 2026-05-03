from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from snakesh.services import linux_desktop_install_service as service
from snakesh.services import tool_launcher_service


class LinuxDesktopInstallServiceTests(unittest.TestCase):
    def test_install_and_uninstall_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir) / "home"
            home.mkdir(parents=True)
            source = Path(tmpdir) / "SnakeSh-x86_64.AppImage"
            source.write_bytes(b"appimage-payload")
            source.chmod(0o755)
            paths = service.integration_paths(home=home)

            with patch("snakesh.services.linux_desktop_install_service.platform.system", return_value="Linux"):
                installed = service.install_desktop_integration(appimage_path=source, home=home)
                self.assertTrue(installed.exists())
                self.assertTrue(os.access(installed, os.X_OK))
                self.assertTrue(service.is_desktop_integration_installed(home=home))
                self.assertTrue(paths.launcher_icon_file.exists())
                self.assertTrue(paths.icon_file.exists())
                self.assertTrue(paths.mime_definition_file.exists())
                desktop_payload = paths.desktop_file.read_text(encoding="utf-8")
                self.assertIn(f"Icon={paths.launcher_icon_file}", desktop_payload)
                self.assertIn(f"MimeType={service.EXPORT_MIME_TYPE};", desktop_payload)
                self.assertIn("StartupWMClass=SnakeSh", desktop_payload)
                self.assertIn(f"{service.DESKTOP_VERSION_KEY}=", desktop_payload)
                mime_payload = paths.mime_definition_file.read_text(encoding="utf-8")
                self.assertIn('glob pattern="*.ssx"', mime_payload)
                self.assertEqual(service.installed_desktop_integration_version(home=home), service.__version__)

                removed = service.uninstall_desktop_integration(home=home)
                self.assertTrue(removed)
                self.assertFalse(installed.exists())
                self.assertFalse(paths.launcher_icon_file.exists())
                self.assertFalse(paths.mime_definition_file.exists())
                self.assertFalse(service.is_desktop_integration_installed(home=home))

    def test_uninstall_removes_tool_launcher_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir) / "home"
            home.mkdir(parents=True)
            source = Path(tmpdir) / "SnakeSh-x86_64.AppImage"
            source.write_bytes(b"appimage-payload")
            source.chmod(0o755)

            with (
                patch("snakesh.services.linux_desktop_install_service.platform.system", return_value="Linux"),
                patch("snakesh.services.tool_launcher_service._refresh_linux_desktop_index"),
            ):
                service.install_desktop_integration(appimage_path=source, home=home)
                tool_launcher_service.sync_tool_launchers(["ping", "help"], platform_name="linux", home=home)

                ping_desktop = tool_launcher_service._linux_tool_desktop_path("ping", home=home)
                help_desktop = tool_launcher_service._linux_tool_desktop_path("help", home=home)
                ping_icon = tool_launcher_service._linux_tool_icon_path("ping", home=home)
                help_icon = tool_launcher_service._linux_tool_icon_path("help", home=home)
                self.assertTrue(ping_desktop.exists())
                self.assertTrue(help_desktop.exists())
                self.assertTrue(ping_icon.exists())
                self.assertTrue(help_icon.exists())

                removed = service.uninstall_desktop_integration(home=home)

                self.assertTrue(removed)
                self.assertFalse(ping_desktop.exists())
                self.assertFalse(help_desktop.exists())
                self.assertFalse(ping_icon.exists())
                self.assertFalse(help_icon.exists())
                self.assertEqual(
                    tool_launcher_service.installed_tool_launcher_keys(platform_name="linux", home=home),
                    [],
                )

    def test_reinstall_replaces_corrupt_copy(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir) / "home"
            home.mkdir(parents=True)
            source = Path(tmpdir) / "SnakeSh-x86_64.AppImage"
            source.write_bytes(b"initial")
            source.chmod(0o755)

            with patch("snakesh.services.linux_desktop_install_service.platform.system", return_value="Linux"):
                installed = service.install_desktop_integration(appimage_path=source, home=home)
                installed.write_bytes(b"corrupt")
                source.write_bytes(b"fresh")
                service.install_desktop_integration(appimage_path=source, home=home)
                self.assertEqual(installed.read_bytes(), b"fresh")

    def test_install_requires_appimage_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir) / "home"
            home.mkdir(parents=True)

            with (
                patch("snakesh.services.linux_desktop_install_service.platform.system", return_value="Linux"),
                patch("snakesh.services.linux_desktop_install_service.runtime.appimage_path", return_value=None),
            ):
                with self.assertRaises(service.LinuxDesktopIntegrationError):
                    service.install_desktop_integration(home=home)

    def test_desktop_integration_needs_update_detects_missing_or_older_versions(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir) / "home"
            home.mkdir(parents=True)
            source = Path(tmpdir) / "SnakeSh-x86_64.AppImage"
            source.write_bytes(b"appimage-payload")
            source.chmod(0o755)
            paths = service.integration_paths(home=home)

            with patch("snakesh.services.linux_desktop_install_service.platform.system", return_value="Linux"):
                service.install_desktop_integration(appimage_path=source, home=home)

                self.assertFalse(
                    service.desktop_integration_needs_update(current_version=service.__version__, home=home)
                )
                self.assertTrue(
                    service.desktop_integration_needs_update(current_version="999.0.0", home=home)
                )

                payload = paths.desktop_file.read_text(encoding="utf-8")
                payload = "\n".join(
                    line for line in payload.splitlines() if not line.startswith(f"{service.DESKTOP_VERSION_KEY}=")
                )
                paths.desktop_file.write_text(payload + "\n", encoding="utf-8")

                self.assertIsNone(service.installed_desktop_integration_version(home=home))
                self.assertTrue(
                    service.desktop_integration_needs_update(current_version=service.__version__, home=home)
                )

    def test_legacy_desktop_integration_without_mime_is_treated_as_installed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir) / "home"
            home.mkdir(parents=True)
            paths = service.integration_paths(home=home)
            paths.install_dir.mkdir(parents=True, exist_ok=True)
            paths.desktop_file.parent.mkdir(parents=True, exist_ok=True)

            paths.installed_appimage.write_bytes(b"legacy")
            paths.installed_appimage.chmod(0o755)
            paths.launcher_icon_file.write_bytes(b"png")
            paths.desktop_file.write_text(
                "\n".join(
                    (
                        "[Desktop Entry]",
                        "Type=Application",
                        "Name=SnakeSh",
                        f'Exec="{paths.installed_appimage}" %U',
                        f"Icon={paths.launcher_icon_file}",
                        "Terminal=false",
                        f"{service.DESKTOP_VERSION_KEY}=0.8.1",
                        "",
                    )
                ),
                encoding="utf-8",
            )

            self.assertTrue(service.is_desktop_integration_installed(home=home))
            self.assertEqual(service.installed_desktop_integration_version(home=home), "0.8.1")
            self.assertTrue(service.desktop_integration_needs_update(current_version="0.8.5", home=home))


if __name__ == "__main__":
    unittest.main()
