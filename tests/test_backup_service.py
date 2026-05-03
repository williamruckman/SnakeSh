from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from snakesh.services.backup_service import BackupService
from snakesh.services.settings_service import AppSettings, SettingsService


class BackupServiceTests(unittest.TestCase):
    def test_export_settings_include_fast_commands_and_workspace_profiles(self) -> None:
        service = BackupService()
        settings = AppSettings.defaults()
        settings.fast_commands = [
            {"id": "cmd-1", "name": "Restart API", "command": "sudo systemctl restart api"}
        ]
        settings.workspace_profiles = [
            {
                "id": "profile-1",
                "name": "Dual Pane",
                "snapshot": {"workspace_tree": {"type": "split", "orientation": "horizontal"}},
                "startup_tools": ["ping", "help"],
            }
        ]
        settings.default_workspace_profile_id = "profile-1"
        settings.syslog_snmp_monitor_profiles = [
            {
                "id": "monitor-1",
                "name": "NOC",
                "config": {"bind_host": "0.0.0.0", "syslog_udp_port": 1514},
            }
        ]
        settings.last_syslog_snmp_monitor_profile_id = "monitor-1"

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "export.ssx"
            service.export_bundle(path, settings=settings, sessions=None, folders=None, password=None)
            raw = json.loads(path.read_text(encoding="utf-8"))

        self.assertFalse(raw["encrypted"])
        payload = raw["payload"]
        self.assertEqual(payload["source_platform"], SettingsService.current_platform_name())
        exported_settings = payload["settings"]
        self.assertEqual(exported_settings["fast_commands"], settings.fast_commands)
        self.assertEqual(exported_settings["workspace_profiles"], settings.workspace_profiles)
        self.assertEqual(exported_settings["default_workspace_profile_id"], "profile-1")
        self.assertEqual(exported_settings["syslog_snmp_monitor_profiles"], settings.syslog_snmp_monitor_profiles)
        self.assertEqual(exported_settings["last_syslog_snmp_monitor_profile_id"], "monitor-1")

    def test_import_settings_roundtrip_preserves_fast_commands_and_workspace_profiles(self) -> None:
        service = BackupService()
        settings = AppSettings.defaults()
        settings.fast_commands = [
            {"id": "cmd-1", "name": "Restart API", "command": "sudo systemctl restart api"}
        ]
        settings.workspace_profiles = [
            {
                "id": "profile-1",
                "name": "Dual Pane",
                "snapshot": {"workspace_tree": {"type": "split", "orientation": "horizontal"}},
                "startup_tools": ["ping", "help"],
            }
        ]
        settings.default_workspace_profile_id = "profile-1"
        settings.syslog_snmp_monitor_profiles = [
            {
                "id": "monitor-1",
                "name": "NOC",
                "config": {"bind_host": "0.0.0.0", "syslog_udp_port": 1514},
            }
        ]
        settings.last_syslog_snmp_monitor_profile_id = "monitor-1"

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "export.ssx"
            service.export_bundle(path, settings=settings, sessions=None, folders=None, password=None)
            imported = service.import_bundle(path)

        self.assertTrue(imported.has_settings)
        self.assertIsNotNone(imported.settings)
        assert imported.settings is not None
        self.assertEqual(imported.source_platform, SettingsService.current_platform_name())
        self.assertEqual(imported.settings.fast_commands, settings.fast_commands)
        self.assertEqual(imported.settings.workspace_profiles, settings.workspace_profiles)
        self.assertEqual(imported.settings.default_workspace_profile_id, "profile-1")
        self.assertEqual(imported.settings.syslog_snmp_monitor_profiles, settings.syslog_snmp_monitor_profiles)
        self.assertEqual(imported.settings.last_syslog_snmp_monitor_profile_id, "monitor-1")

    def test_import_legacy_backup_without_source_platform_keeps_metadata_optional(self) -> None:
        service = BackupService()
        settings = AppSettings.defaults()

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "legacy.ssx"
            path.write_text(
                json.dumps(
                    {
                        "format": service.FORMAT,
                        "version": service.VERSION,
                        "encrypted": False,
                        "payload": {
                            "exported_at": "2026-01-01T00:00:00+00:00",
                            "settings": settings.to_dict(),
                        },
                    }
                ),
                encoding="utf-8",
            )
            imported = service.import_bundle(path)

        self.assertIsNone(imported.source_platform)
        self.assertTrue(imported.has_settings)


if __name__ == "__main__":
    unittest.main()
