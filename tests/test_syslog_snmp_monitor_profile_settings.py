from __future__ import annotations

import unittest

from snakesh.services.settings_service import AppSettings, SettingsService


class SyslogSnmpMonitorProfileSettingsTests(unittest.TestCase):
    def test_roundtrip_preserves_monitor_profiles_and_selection(self) -> None:
        settings = AppSettings.defaults()
        settings.syslog_snmp_monitor_profiles = [
            {
                "id": "monitor-a",
                "name": "NOC Monitor",
                "config": {
                    "bind_host": "0.0.0.0",
                    "syslog_udp_enabled": True,
                    "syslog_udp_port": 1514,
                    "syslog_tcp_enabled": True,
                    "syslog_tcp_port": 1514,
                    "syslog_tls_enabled": False,
                    "syslog_tls_port": 6514,
                    "snmp_enabled": True,
                    "snmp_port": 1162,
                    "retention": {
                        "hot_retention_days": 14,
                        "archive_retention_days": 120,
                        "max_archive_size_mb": 2048,
                        "archive_rotation_mb": 128,
                    },
                    "filter_state": {"text": "error", "data_scope": "all"},
                    "layout_state": {"display_timezone": "America/New_York", "tab_id": "search"},
                    "visible_columns": ["received_ts", "source", "message_text"],
                },
            }
        ]
        settings.last_syslog_snmp_monitor_profile_id = "monitor-a"

        restored = AppSettings.from_dict(settings.to_dict())

        self.assertEqual(len(restored.syslog_snmp_monitor_profiles), 1)
        self.assertEqual(restored.syslog_snmp_monitor_profiles[0]["id"], "monitor-a")
        self.assertEqual(restored.syslog_snmp_monitor_profiles[0]["name"], "NOC Monitor")
        self.assertEqual(restored.syslog_snmp_monitor_profiles[0]["config"]["syslog_udp_port"], 1514)
        self.assertEqual(
            restored.syslog_snmp_monitor_profiles[0]["config"]["layout_state"]["display_timezone"],
            "America/New_York",
        )
        self.assertEqual(restored.syslog_snmp_monitor_profiles[0]["config"]["layout_state"]["tab_id"], "search")
        self.assertEqual(restored.last_syslog_snmp_monitor_profile_id, "monitor-a")

    def test_sanitize_normalizes_monitor_profiles_and_clears_invalid_selection(self) -> None:
        settings = AppSettings.defaults()
        settings.syslog_snmp_monitor_profiles = [
            {
                "id": "monitor-a",
                "name": "Saved Monitor",
                "config": {
                    "bind_host": "   ",
                    "syslog_udp_port": "70000",
                    "syslog_tcp_port": "bad",
                    "snmp_port": "0",
                    "retention": {
                        "hot_retention_days": "0",
                        "archive_retention_days": "999999",
                        "max_archive_size_mb": "32",
                        "archive_rotation_mb": "0",
                    },
                    "visible_columns": ["received_ts", "source", "source", ""],
                },
            },
            {
                "id": "monitor-a",
                "name": "Duplicate",
                "config": {"bind_host": "127.0.0.1"},
            },
        ]
        settings.last_syslog_snmp_monitor_profile_id = "missing-monitor"

        sanitized = SettingsService._sanitize(settings)

        self.assertEqual(len(sanitized.syslog_snmp_monitor_profiles), 1)
        profile = sanitized.syslog_snmp_monitor_profiles[0]
        config = profile["config"]
        self.assertEqual(profile["id"], "monitor-a")
        self.assertEqual(config["bind_host"], "0.0.0.0")
        self.assertEqual(config["syslog_udp_port"], 65535)
        self.assertEqual(config["syslog_tcp_port"], 1514)
        self.assertEqual(config["snmp_port"], 1)
        self.assertEqual(config["retention"]["hot_retention_days"], 1)
        self.assertEqual(config["retention"]["archive_retention_days"], 3650)
        self.assertEqual(config["retention"]["max_archive_size_mb"], 128)
        self.assertEqual(config["retention"]["archive_rotation_mb"], 1)
        self.assertEqual(config["visible_columns"], ["received_ts", "source"])
        self.assertEqual(sanitized.last_syslog_snmp_monitor_profile_id, "")


if __name__ == "__main__":
    unittest.main()
