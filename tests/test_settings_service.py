from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from snakesh.core.theme_presets import DEFAULT_THEME_ID
from snakesh.services.settings_service import AppSettings, SettingsService


class SettingsServiceTests(unittest.TestCase):
    def test_defaults_use_default_theme(self) -> None:
        settings = AppSettings.defaults()
        self.assertEqual(settings.theme_name, DEFAULT_THEME_ID)

    def test_roundtrip_preserves_theme_name(self) -> None:
        settings = AppSettings.defaults()
        settings.theme_name = "midnight"

        restored = AppSettings.from_dict(settings.to_dict())

        self.assertEqual(restored.theme_name, "midnight")

    def test_from_dict_infers_custom_theme_for_legacy_manual_colors(self) -> None:
        raw = AppSettings.defaults().to_dict()
        raw.pop("theme_name", None)
        raw["accent_color"] = "#123456"

        restored = AppSettings.from_dict(raw)

        self.assertEqual(restored.theme_name, "custom")

    def test_sanitize_switches_to_custom_when_theme_colors_no_longer_match(self) -> None:
        settings = AppSettings.defaults()
        settings.theme_name = "midnight"
        settings.accent_color = "#abcdef"

        sanitized = SettingsService._sanitize(settings)

        self.assertEqual(sanitized.theme_name, "custom")

    def test_sanitize_normalizes_invalid_theme_name_to_default(self) -> None:
        settings = AppSettings.defaults()
        settings.theme_name = "invalid-theme"

        sanitized = SettingsService._sanitize(settings)

        self.assertEqual(sanitized.theme_name, DEFAULT_THEME_ID)

    def test_defaults_disable_cursor_blink(self) -> None:
        settings = AppSettings.defaults()
        self.assertFalse(settings.terminal_cursor_blink)

    def test_defaults_disable_resource_monitor_offline_adapters_and_tool_password_prompt(self) -> None:
        settings = AppSettings.defaults()
        self.assertFalse(settings.resource_monitor_show_offline_adapters)
        self.assertEqual(settings.resource_monitor_zoom_percent, 100)
        self.assertEqual(settings.resource_monitor_sample_refresh_ms, 1000)
        self.assertEqual(settings.resource_monitor_process_refresh_ms, 4000)
        self.assertEqual(settings.resource_monitor_history_minutes, 10)
        self.assertFalse(settings.master_password_tools_enabled)
        self.assertEqual(settings.standalone_tool_window_placements, {})

    def test_sanitize_clamps_resource_monitor_preferences(self) -> None:
        settings = AppSettings.defaults()
        settings.resource_monitor_zoom_percent = 1000
        settings.resource_monitor_sample_refresh_ms = 1
        settings.resource_monitor_process_refresh_ms = 999999
        settings.resource_monitor_history_minutes = 1

        sanitized = SettingsService._sanitize(settings)

        self.assertEqual(sanitized.resource_monitor_zoom_percent, 150)
        self.assertEqual(sanitized.resource_monitor_sample_refresh_ms, 500)
        self.assertEqual(sanitized.resource_monitor_process_refresh_ms, 30000)
        self.assertEqual(sanitized.resource_monitor_history_minutes, 2)

    def test_roundtrip_preserves_standalone_tool_window_placements(self) -> None:
        settings = AppSettings.defaults()
        settings.standalone_tool_window_placements = {
            "resource_monitor": {
                "geometry_b64": "R0VP",
                "screen_name": "Secondary",
                "screen_serial": "SER-B",
                "frame_rect": [1920, 40, 1200, 800],
            },
            "not-a-tool": {"geometry_b64": "BAD"},
        }

        restored = AppSettings.from_dict(settings.to_dict())

        self.assertEqual(list(restored.standalone_tool_window_placements), ["resource_monitor"])
        self.assertEqual(
            restored.standalone_tool_window_placements["resource_monitor"]["frame_rect"],
            [1920, 40, 1200, 800],
        )

    def test_cross_platform_sanitize_clears_standalone_tool_window_placements(self) -> None:
        settings = AppSettings.defaults()
        settings.standalone_tool_window_placements = {
            "help": {
                "geometry_b64": "R0VP",
                "screen_name": "Windows Screen",
                "screen_serial": "WIN",
                "frame_rect": [100, 100, 700, 500],
            }
        }

        sanitized = SettingsService.sanitize_for_current_platform(settings, source_platform="windows")

        if SettingsService.current_platform_name() != "windows":
            self.assertEqual(sanitized.standalone_tool_window_placements, {})

    def test_defaults_disable_classic_terminal_default_colors(self) -> None:
        settings = AppSettings.defaults()
        self.assertFalse(settings.terminal_classic_default_colors)

    def test_roundtrip_preserves_cursor_blink_preference(self) -> None:
        settings = AppSettings.defaults()
        settings.terminal_cursor_blink = True

        restored = AppSettings.from_dict(settings.to_dict())

        self.assertTrue(restored.terminal_cursor_blink)

    def test_roundtrip_preserves_classic_terminal_default_color_preference(self) -> None:
        settings = AppSettings.defaults()
        settings.terminal_classic_default_colors = True

        restored = AppSettings.from_dict(settings.to_dict())

        self.assertTrue(restored.terminal_classic_default_colors)

    def test_defaults_disable_terminal_bell(self) -> None:
        settings = AppSettings.defaults()
        self.assertFalse(settings.terminal_bell_enabled)

    def test_defaults_enable_active_tab_close_prompt(self) -> None:
        settings = AppSettings.defaults()
        self.assertTrue(settings.warn_before_closing_active_tab)

    def test_defaults_enable_file_overwrite_prompt(self) -> None:
        settings = AppSettings.defaults()
        self.assertTrue(settings.warn_before_file_overwrite)

    def test_defaults_disable_visual_bell(self) -> None:
        settings = AppSettings.defaults()
        self.assertFalse(settings.terminal_visual_bell_enabled)

    def test_defaults_use_home_local_shell_start_dir(self) -> None:
        settings = AppSettings.defaults()
        self.assertEqual(settings.local_shell_start_dir_mode, "home")
        self.assertEqual(settings.local_shell_command_override, "")
        self.assertEqual(settings.local_shell_custom_start_dir, "")

    def test_roundtrip_preserves_local_shell_and_bell_preferences(self) -> None:
        settings = AppSettings.defaults()
        settings.terminal_bell_enabled = True
        settings.terminal_visual_bell_enabled = True
        settings.local_shell_command_override = "bash -i"
        settings.local_shell_start_dir_mode = "custom"
        settings.local_shell_custom_start_dir = "~/lab"

        restored = AppSettings.from_dict(settings.to_dict())

        self.assertTrue(restored.terminal_bell_enabled)
        self.assertTrue(restored.terminal_visual_bell_enabled)
        self.assertEqual(restored.local_shell_command_override, "bash -i")
        self.assertEqual(restored.local_shell_start_dir_mode, "custom")
        self.assertEqual(restored.local_shell_custom_start_dir, "~/lab")

    def test_sanitize_normalizes_invalid_local_shell_start_dir_mode(self) -> None:
        settings = AppSettings.defaults()
        settings.local_shell_start_dir_mode = "invalid-mode"

        sanitized = SettingsService._sanitize(settings)

        self.assertEqual(sanitized.local_shell_start_dir_mode, "home")

    def test_sanitize_falls_back_to_home_when_custom_mode_has_blank_path(self) -> None:
        settings = AppSettings.defaults()
        settings.local_shell_start_dir_mode = "custom"
        settings.local_shell_custom_start_dir = "   "

        sanitized = SettingsService._sanitize(settings)

        self.assertEqual(sanitized.local_shell_start_dir_mode, "home")
        self.assertEqual(sanitized.local_shell_custom_start_dir, "")

    def test_sanitize_expands_custom_local_shell_path_and_trims_override(self) -> None:
        settings = AppSettings.defaults()
        settings.local_shell_start_dir_mode = "custom"
        settings.local_shell_custom_start_dir = "  ~/ShellLab  "
        settings.local_shell_command_override = "   bash -l   "

        sanitized = SettingsService._sanitize(settings)

        self.assertEqual(sanitized.local_shell_start_dir_mode, "custom")
        self.assertNotIn("~", sanitized.local_shell_custom_start_dir)
        self.assertEqual(sanitized.local_shell_command_override, "bash -l")

    def test_sanitize_rejects_windows_terminal_local_shell_override(self) -> None:
        settings = AppSettings.defaults()
        settings.local_shell_command_override = " wt.exe new-tab "

        sanitized = SettingsService._sanitize(settings)

        self.assertEqual(sanitized.local_shell_command_override, "")

    def test_sanitize_rejects_windowsapps_local_shell_override(self) -> None:
        settings = AppSettings.defaults()
        settings.local_shell_command_override = (
            r'  "C:\Users\tester\AppData\Local\Microsoft\WindowsApps\pwsh.exe" -NoLogo  '
        )

        sanitized = SettingsService._sanitize(settings)

        self.assertEqual(sanitized.local_shell_command_override, "")

    def test_defaults_include_terminal_log_dir(self) -> None:
        settings = AppSettings.defaults()
        self.assertTrue(settings.terminal_log_dir.strip())

    def test_defaults_disable_global_session_logging(self) -> None:
        settings = AppSettings.defaults()
        self.assertFalse(settings.global_session_logging_enabled)

    def test_roundtrip_preserves_global_session_logging(self) -> None:
        settings = AppSettings.defaults()
        settings.global_session_logging_enabled = True

        restored = AppSettings.from_dict(settings.to_dict())

        self.assertTrue(restored.global_session_logging_enabled)

    def test_roundtrip_preserves_active_tab_close_prompt_preference(self) -> None:
        settings = AppSettings.defaults()
        settings.warn_before_closing_active_tab = False

        restored = AppSettings.from_dict(settings.to_dict())

        self.assertFalse(restored.warn_before_closing_active_tab)

    def test_roundtrip_preserves_file_overwrite_prompt_preference(self) -> None:
        settings = AppSettings.defaults()
        settings.warn_before_file_overwrite = False

        restored = AppSettings.from_dict(settings.to_dict())

        self.assertFalse(restored.warn_before_file_overwrite)

    def test_from_dict_defaults_active_tab_close_prompt_when_key_missing(self) -> None:
        raw = AppSettings.defaults().to_dict()
        raw.pop("warn_before_closing_active_tab", None)

        restored = AppSettings.from_dict(raw)

        self.assertTrue(restored.warn_before_closing_active_tab)

    def test_from_dict_defaults_file_overwrite_prompt_when_key_missing(self) -> None:
        raw = AppSettings.defaults().to_dict()
        raw.pop("warn_before_file_overwrite", None)

        restored = AppSettings.from_dict(raw)

        self.assertTrue(restored.warn_before_file_overwrite)

    def test_defaults_enable_session_log_cleanup(self) -> None:
        settings = AppSettings.defaults()
        self.assertTrue(settings.session_log_cleanup_enabled)
        self.assertEqual(settings.session_log_retention_days, 7)
        self.assertTrue(settings.web_server_log_cleanup_enabled)
        self.assertEqual(settings.web_server_log_retention_days, 7)
        self.assertFalse(settings.crash_logging_enabled)

    def test_defaults_use_shown_session_list(self) -> None:
        settings = AppSettings.defaults()
        self.assertEqual(settings.session_list_visibility_mode, "shown")

    def test_roundtrip_preserves_session_log_cleanup_preferences(self) -> None:
        settings = AppSettings.defaults()
        settings.session_log_cleanup_enabled = True
        settings.session_log_retention_days = 21
        settings.web_server_log_cleanup_enabled = False
        settings.web_server_log_retention_days = 14

        restored = AppSettings.from_dict(settings.to_dict())

        self.assertTrue(restored.session_log_cleanup_enabled)
        self.assertEqual(restored.session_log_retention_days, 21)
        self.assertFalse(restored.web_server_log_cleanup_enabled)
        self.assertEqual(restored.web_server_log_retention_days, 14)

    def test_roundtrip_preserves_session_list_visibility_mode(self) -> None:
        settings = AppSettings.defaults()
        settings.session_list_visibility_mode = "auto"

        restored = AppSettings.from_dict(settings.to_dict())

        self.assertEqual(restored.session_list_visibility_mode, "auto")

    def test_roundtrip_preserves_float_session_list_visibility_mode(self) -> None:
        settings = AppSettings.defaults()
        settings.session_list_visibility_mode = "float"

        restored = AppSettings.from_dict(settings.to_dict())

        self.assertEqual(restored.session_list_visibility_mode, "float")

    def test_save_skips_rewriting_identical_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch("snakesh.services.settings_service.data_dir", return_value=Path(tmp)):
                service = SettingsService()
                settings = AppSettings.defaults()
                original_write_text = Path.write_text
                write_calls = 0

                def _tracked_write_text(path_self: Path, *args, **kwargs):
                    nonlocal write_calls
                    write_calls += 1
                    return original_write_text(path_self, *args, **kwargs)

                with patch("pathlib.Path.write_text", autospec=True, side_effect=_tracked_write_text):
                    service.save(settings)
                    service.save(settings)

                self.assertEqual(write_calls, 1)

    def test_roundtrip_preserves_crash_logging_toggle(self) -> None:
        settings = AppSettings.defaults()
        settings.crash_logging_enabled = True

        restored = AppSettings.from_dict(settings.to_dict())

        self.assertTrue(restored.crash_logging_enabled)

    def test_roundtrip_preserves_main_window_fullscreen_shortcut(self) -> None:
        settings = AppSettings.defaults()
        settings.main_window_fullscreen_shortcut = "Ctrl+Shift+F11"

        restored = AppSettings.from_dict(settings.to_dict())

        self.assertEqual(restored.main_window_fullscreen_shortcut, "Ctrl+Shift+F11")

    def test_roundtrip_preserves_hide_controls_in_fullscreen(self) -> None:
        settings = AppSettings.defaults()
        settings.main_window_hide_controls_in_fullscreen = True

        restored = AppSettings.from_dict(settings.to_dict())

        self.assertTrue(restored.main_window_hide_controls_in_fullscreen)

    def test_roundtrip_preserves_password_generator_preferences(self) -> None:
        settings = AppSettings.defaults()
        settings.password_generator_length = 28
        settings.password_generator_count = 12
        settings.password_generator_complexity = "Maximum"
        settings.password_generator_include_symbols = False
        settings.password_generator_include_characters = "@_"
        settings.password_generator_exclude_characters = "O0Il"

        restored = AppSettings.from_dict(settings.to_dict())

        self.assertEqual(restored.password_generator_length, 28)
        self.assertEqual(restored.password_generator_count, 12)
        self.assertEqual(restored.password_generator_complexity, "Maximum")
        self.assertFalse(restored.password_generator_include_symbols)
        self.assertEqual(restored.password_generator_include_characters, "@_")
        self.assertEqual(restored.password_generator_exclude_characters, "O0Il")

    def test_sanitize_clamps_password_generator_preferences(self) -> None:
        settings = AppSettings.defaults()
        settings.password_generator_length = 999
        settings.password_generator_count = 0
        settings.password_generator_complexity = "unrecognized"

        sanitized = SettingsService._sanitize(settings)

        self.assertEqual(sanitized.password_generator_length, 256)
        self.assertEqual(sanitized.password_generator_count, 1)
        self.assertEqual(sanitized.password_generator_complexity, "Strong")

    def test_sanitize_restores_default_fullscreen_shortcut_when_blank(self) -> None:
        settings = AppSettings.defaults()
        settings.main_window_fullscreen_shortcut = "   "

        sanitized = SettingsService._sanitize(settings)

        self.assertEqual(
            sanitized.main_window_fullscreen_shortcut,
            AppSettings.defaults().main_window_fullscreen_shortcut,
        )

    def test_sanitize_normalizes_invalid_session_list_visibility_mode(self) -> None:
        settings = AppSettings.defaults()
        settings.session_list_visibility_mode = "floating"

        sanitized = SettingsService._sanitize(settings)

        self.assertEqual(sanitized.session_list_visibility_mode, "shown")

    def test_sanitize_accepts_float_session_list_visibility_mode(self) -> None:
        settings = AppSettings.defaults()
        settings.session_list_visibility_mode = "float"

        sanitized = SettingsService._sanitize(settings)

        self.assertEqual(sanitized.session_list_visibility_mode, "float")

    def test_sanitize_maps_legacy_unhide_to_shown(self) -> None:
        settings = AppSettings.defaults()
        settings.session_list_visibility_mode = "unhide"

        sanitized = SettingsService._sanitize(settings)

        self.assertEqual(sanitized.session_list_visibility_mode, "shown")

    def test_sanitize_maps_legacy_hide_to_auto(self) -> None:
        settings = AppSettings.defaults()
        settings.session_list_visibility_mode = "hide"

        sanitized = SettingsService._sanitize(settings)

        self.assertEqual(sanitized.session_list_visibility_mode, "auto")

    def test_sanitize_clamps_session_log_retention_days(self) -> None:
        settings = AppSettings.defaults()
        settings.session_log_retention_days = 0
        settings.web_server_log_retention_days = 0

        sanitized = SettingsService._sanitize(settings)

        self.assertEqual(sanitized.session_log_retention_days, 1)
        self.assertEqual(sanitized.web_server_log_retention_days, 1)

    def test_sanitize_restores_default_terminal_log_dir_when_blank(self) -> None:
        settings = AppSettings.defaults()
        settings.terminal_log_dir = "   "
        sanitized = SettingsService._sanitize(settings)
        self.assertTrue(sanitized.terminal_log_dir.strip())

    def test_roundtrip_preserves_workspace_profiles_and_default(self) -> None:
        settings = AppSettings.defaults()
        settings.workspace_profiles = [
            {
                "id": "profile-a",
                "name": "Ops Layout",
                "snapshot": {"workspace_tree": {"type": "host", "host_key": "host-1"}},
                "startup_tools": ["ping", "help"],
            }
        ]
        settings.default_workspace_profile_id = "profile-a"

        restored = AppSettings.from_dict(settings.to_dict())

        self.assertEqual(len(restored.workspace_profiles), 1)
        self.assertEqual(restored.workspace_profiles[0]["id"], "profile-a")
        self.assertEqual(restored.workspace_profiles[0]["startup_tools"], ["ping", "help"])
        self.assertEqual(restored.default_workspace_profile_id, "profile-a")

    def test_roundtrip_preserves_fast_commands(self) -> None:
        settings = AppSettings.defaults()
        settings.fast_commands = [
            {"id": "cmd-1", "name": "Restart Service", "command": "sudo systemctl restart api"}
        ]

        restored = AppSettings.from_dict(settings.to_dict())

        self.assertEqual(len(restored.fast_commands), 1)
        self.assertEqual(restored.fast_commands[0]["id"], "cmd-1")
        self.assertEqual(restored.fast_commands[0]["name"], "Restart Service")

    def test_sanitize_drops_invalid_fast_commands(self) -> None:
        settings = AppSettings.defaults()
        settings.fast_commands = [
            {"id": "cmd-1", "name": "Valid", "command": "uptime"},
            {"id": "cmd-2", "name": "Blank Command", "command": "   "},
            {"id": "", "name": "Missing Id", "command": "ls -la"},
            {"id": "cmd-1", "name": "Duplicate", "command": "whoami"},
        ]

        sanitized = SettingsService._sanitize(settings)

        self.assertEqual(len(sanitized.fast_commands), 1)
        self.assertEqual(sanitized.fast_commands[0]["id"], "cmd-1")
        self.assertEqual(sanitized.fast_commands[0]["name"], "Valid")

    def test_sanitize_drops_invalid_workspace_profiles_and_clears_missing_default(self) -> None:
        settings = AppSettings.defaults()
        settings.workspace_profiles = [
            {"id": "good", "name": "Good", "snapshot": {"workspace_tree": {"type": "host"}}},
            {"id": "bad-no-snapshot", "name": "Bad", "snapshot": "invalid"},
            {"id": "", "name": "Bad Empty Id", "snapshot": {}},
        ]
        settings.default_workspace_profile_id = "bad-no-snapshot"

        sanitized = SettingsService._sanitize(settings)

        self.assertEqual(len(sanitized.workspace_profiles), 1)
        self.assertEqual(sanitized.workspace_profiles[0]["id"], "good")
        self.assertEqual(sanitized.default_workspace_profile_id, "")

    def test_sanitize_deduplicates_workspace_profile_ids(self) -> None:
        settings = AppSettings.defaults()
        settings.workspace_profiles = [
            {"id": "dup", "name": "First", "snapshot": {"workspace_tree": {"type": "host"}}},
            {"id": "dup", "name": "Second", "snapshot": {"workspace_tree": {"type": "host"}}},
        ]
        settings.default_workspace_profile_id = "dup"

        sanitized = SettingsService._sanitize(settings)

        self.assertEqual(len(sanitized.workspace_profiles), 1)
        self.assertEqual(sanitized.workspace_profiles[0]["name"], "First")
        self.assertEqual(sanitized.default_workspace_profile_id, "dup")

    def test_sanitize_normalizes_workspace_profile_startup_tools(self) -> None:
        settings = AppSettings.defaults()
        settings.workspace_profiles = [
            {
                "id": "profile-tools",
                "name": "Tool Profile",
                "snapshot": {"workspace_tree": {"type": "host"}},
                "startup_tools": [" help ", "invalid", "ping", "ping", "", "dig"],
            }
        ]

        sanitized = SettingsService._sanitize(settings)

        self.assertEqual(sanitized.workspace_profiles[0]["startup_tools"], ["dig", "ping", "help"])

    def test_sanitize_preserves_resource_monitor_in_workspace_profile_startup_tools(self) -> None:
        settings = AppSettings.defaults()
        settings.workspace_profiles = [
            {
                "id": "profile-tools",
                "name": "Tool Profile",
                "snapshot": {"workspace_tree": {"type": "host"}},
                "startup_tools": ["resource_monitor", "help", "ping"],
            }
        ]

        sanitized = SettingsService._sanitize(settings)

        self.assertEqual(sanitized.workspace_profiles[0]["startup_tools"], ["resource_monitor", "ping", "help"])

    def test_roundtrip_preserves_saved_main_window_geometry(self) -> None:
        settings = AppSettings.defaults()
        settings.main_window_geometry_b64 = "AAABAAEAAAD/////"

        restored = AppSettings.from_dict(settings.to_dict())

        self.assertEqual(restored.main_window_geometry_b64, "AAABAAEAAAD/////")

    def test_sanitize_trims_saved_main_window_geometry(self) -> None:
        settings = AppSettings.defaults()
        settings.main_window_geometry_b64 = "  AAECAw==  "

        sanitized = SettingsService._sanitize(settings)

        self.assertEqual(sanitized.main_window_geometry_b64, "AAECAw==")

    def test_roundtrip_preserves_saved_main_window_placement_metadata(self) -> None:
        settings = AppSettings.defaults()
        settings.main_window_screen_name = "Secondary Monitor"
        settings.main_window_screen_serial = "SERIAL-42"
        settings.main_window_frame_rect = [120, 80, 1440, 900]

        restored = AppSettings.from_dict(settings.to_dict())

        self.assertEqual(restored.main_window_screen_name, "Secondary Monitor")
        self.assertEqual(restored.main_window_screen_serial, "SERIAL-42")
        self.assertEqual(restored.main_window_frame_rect, [120, 80, 1440, 900])

    def test_sanitize_normalizes_saved_main_window_placement_metadata(self) -> None:
        settings = AppSettings.defaults()
        settings.main_window_screen_name = "  Secondary Monitor  "
        settings.main_window_screen_serial = "  SERIAL-42  "
        settings.main_window_frame_rect = ["120", 80, "1440", 900]

        sanitized = SettingsService._sanitize(settings)

        self.assertEqual(sanitized.main_window_screen_name, "Secondary Monitor")
        self.assertEqual(sanitized.main_window_screen_serial, "SERIAL-42")
        self.assertEqual(sanitized.main_window_frame_rect, [120, 80, 1440, 900])

    def test_roundtrip_preserves_session_list_window_placement_metadata(self) -> None:
        settings = AppSettings.defaults()
        settings.session_list_window_geometry_b64 = "FLOAT"
        settings.session_list_window_screen_name = "Secondary"
        settings.session_list_window_screen_serial = "SERIAL-2"
        settings.session_list_window_frame_rect = [2200, 160, 420, 700]

        restored = AppSettings.from_dict(settings.to_dict())

        self.assertEqual(restored.session_list_window_geometry_b64, "FLOAT")
        self.assertEqual(restored.session_list_window_screen_name, "Secondary")
        self.assertEqual(restored.session_list_window_screen_serial, "SERIAL-2")
        self.assertEqual(restored.session_list_window_frame_rect, [2200, 160, 420, 700])

    def test_sanitize_normalizes_session_list_window_placement_metadata(self) -> None:
        settings = AppSettings.defaults()
        settings.session_list_window_geometry_b64 = "  FLOAT  "
        settings.session_list_window_screen_name = "  Secondary  "
        settings.session_list_window_screen_serial = "  SERIAL-2  "
        settings.session_list_window_frame_rect = ["2200", 160, "420", 700]

        sanitized = SettingsService._sanitize(settings)

        self.assertEqual(sanitized.session_list_window_geometry_b64, "FLOAT")
        self.assertEqual(sanitized.session_list_window_screen_name, "Secondary")
        self.assertEqual(sanitized.session_list_window_screen_serial, "SERIAL-2")
        self.assertEqual(sanitized.session_list_window_frame_rect, [2200, 160, 420, 700])

    def test_sanitize_for_current_platform_strips_foreign_windows_settings(self) -> None:
        settings = AppSettings.defaults()
        settings.terminal_log_dir = r"C:\Users\tester\Documents\SnakeSh Logs"
        settings.local_shell_command_override = r"C:\Program Files\PowerShell\7\pwsh.exe -NoLogo"
        settings.local_shell_start_dir_mode = "custom"
        settings.local_shell_custom_start_dir = r"C:\Users\tester\Desktop"
        settings.onepassword_cli_path = r"C:\Program Files\1Password\op.exe"
        settings.keepass_database_path = r"C:\Secrets\vault.kdbx"
        settings.keepass_key_file_path = r"C:\Secrets\vault.key"
        settings.main_window_geometry_b64 = "AAA"
        settings.main_window_screen_name = "Secondary Monitor"
        settings.main_window_screen_serial = "SERIAL-42"
        settings.main_window_frame_rect = [120, 80, 1440, 900]
        settings.main_window_splitter_b64 = "BBB"
        settings.session_list_window_geometry_b64 = "FLOAT"
        settings.session_list_window_screen_name = "Session Secondary"
        settings.session_list_window_screen_serial = "SESSION-2"
        settings.session_list_window_frame_rect = [2200, 180, 420, 700]
        settings.web_server_dialog_splitter_b64 = "CCC"
        settings.syslog_snmp_monitor_dialog_splitter_b64 = "DDD"
        settings.workspace_profiles = [
            {
                "id": "profile-1",
                "name": "Imported",
                "snapshot": {
                    "workspace_tree": {"type": "host", "host_key": "host-1"},
                    "window_geometry_b64": "SNAP",
                    "window_screen_name": "Secondary Monitor",
                    "window_screen_serial": "SERIAL-42",
                    "window_frame_rect": [2100, 140, 900, 700],
                    "main_splitter_b64": "SPLIT",
                    "session_list_mode": "auto",
                    "session_list_visible": False,
                    "session_list_last_width": 480,
                    "session_list_window_geometry_b64": "PROFILE-FLOAT",
                    "session_list_window_screen_name": "Profile Secondary",
                    "session_list_window_screen_serial": "PROFILE-2",
                    "session_list_window_frame_rect": [2300, 190, 430, 710],
                    "detached_windows": [
                        {
                            "tabs": [],
                            "window_geometry_b64": "DETACHED",
                            "window_screen_name": "Detached Secondary",
                            "window_screen_serial": "DETACHED-1",
                            "window_frame_rect": [2200, 160, 640, 420],
                        }
                    ],
                },
                "startup_tools": ["ping", "help"],
            }
        ]

        with patch("snakesh.services.settings_service.platform.system", return_value="Linux"):
            sanitized = SettingsService.sanitize_for_current_platform(settings)

        self.assertEqual(sanitized.terminal_log_dir, AppSettings.defaults().terminal_log_dir)
        self.assertEqual(sanitized.local_shell_command_override, "")
        self.assertEqual(sanitized.local_shell_start_dir_mode, "home")
        self.assertEqual(sanitized.local_shell_custom_start_dir, "")
        self.assertEqual(sanitized.onepassword_cli_path, AppSettings.defaults().onepassword_cli_path)
        self.assertEqual(sanitized.keepass_database_path, "")
        self.assertEqual(sanitized.keepass_key_file_path, "")
        self.assertEqual(sanitized.main_window_geometry_b64, "")
        self.assertEqual(sanitized.main_window_screen_name, "")
        self.assertEqual(sanitized.main_window_screen_serial, "")
        self.assertEqual(sanitized.main_window_frame_rect, [])
        self.assertEqual(sanitized.main_window_splitter_b64, "")
        self.assertEqual(sanitized.session_list_window_geometry_b64, "")
        self.assertEqual(sanitized.session_list_window_screen_name, "")
        self.assertEqual(sanitized.session_list_window_screen_serial, "")
        self.assertEqual(sanitized.session_list_window_frame_rect, [])
        self.assertEqual(sanitized.web_server_dialog_splitter_b64, "")
        self.assertEqual(sanitized.syslog_snmp_monitor_dialog_splitter_b64, "")
        snapshot = sanitized.workspace_profiles[0]["snapshot"]
        self.assertEqual(sanitized.workspace_profiles[0]["startup_tools"], ["ping", "help"])
        self.assertNotIn("window_geometry_b64", snapshot)
        self.assertNotIn("window_screen_name", snapshot)
        self.assertNotIn("window_screen_serial", snapshot)
        self.assertNotIn("window_frame_rect", snapshot)
        self.assertNotIn("main_splitter_b64", snapshot)
        self.assertNotIn("session_list_mode", snapshot)
        self.assertNotIn("session_list_visible", snapshot)
        self.assertNotIn("session_list_last_width", snapshot)
        self.assertNotIn("session_list_window_geometry_b64", snapshot)
        self.assertNotIn("session_list_window_screen_name", snapshot)
        self.assertNotIn("session_list_window_screen_serial", snapshot)
        self.assertNotIn("session_list_window_frame_rect", snapshot)
        detached_window = snapshot["detached_windows"][0]
        self.assertNotIn("window_geometry_b64", detached_window)
        self.assertNotIn("window_screen_name", detached_window)
        self.assertNotIn("window_screen_serial", detached_window)
        self.assertNotIn("window_frame_rect", detached_window)

    def test_sanitize_imported_settings_preserves_same_platform_layout_state(self) -> None:
        settings = AppSettings.defaults()
        settings.terminal_log_dir = "/home/tester/SnakeSh Logs"
        settings.local_shell_command_override = "bash -l"
        settings.local_shell_start_dir_mode = "custom"
        settings.local_shell_custom_start_dir = "/home/tester/lab"
        settings.main_window_geometry_b64 = "AAA"
        settings.main_window_screen_name = "Secondary Monitor"
        settings.main_window_screen_serial = "SERIAL-42"
        settings.main_window_frame_rect = [120, 80, 1440, 900]
        settings.main_window_splitter_b64 = "BBB"
        settings.session_list_window_geometry_b64 = "FLOAT"
        settings.session_list_window_screen_name = "Session Secondary"
        settings.session_list_window_screen_serial = "SESSION-2"
        settings.session_list_window_frame_rect = [2200, 180, 420, 700]
        settings.workspace_profiles = [
            {
                "id": "profile-1",
                "name": "Layout",
                "snapshot": {
                    "workspace_tree": {"type": "host", "host_key": "host-1"},
                    "window_geometry_b64": "SNAP",
                    "window_screen_name": "Secondary Monitor",
                    "window_screen_serial": "SERIAL-42",
                    "window_frame_rect": [2100, 140, 900, 700],
                    "main_splitter_b64": "SPLIT",
                    "session_list_mode": "auto",
                    "session_list_visible": False,
                    "session_list_last_width": 500,
                    "session_list_window_geometry_b64": "PROFILE-FLOAT",
                    "session_list_window_screen_name": "Profile Secondary",
                    "session_list_window_screen_serial": "PROFILE-2",
                    "session_list_window_frame_rect": [2300, 190, 430, 710],
                    "detached_windows": [
                        {
                            "tabs": [],
                            "window_geometry_b64": "DETACHED",
                            "window_screen_name": "Detached Secondary",
                            "window_screen_serial": "DETACHED-1",
                            "window_frame_rect": [2200, 160, 640, 420],
                        }
                    ],
                },
                "startup_tools": ["ping", "help"],
            }
        ]

        with patch("snakesh.services.settings_service.platform.system", return_value="Linux"):
            sanitized = SettingsService.sanitize_imported_settings(settings, source_platform="linux")

        self.assertEqual(sanitized.terminal_log_dir, "/home/tester/SnakeSh Logs")
        self.assertEqual(sanitized.local_shell_command_override, "bash -l")
        self.assertEqual(sanitized.local_shell_start_dir_mode, "custom")
        self.assertEqual(sanitized.local_shell_custom_start_dir, "/home/tester/lab")
        self.assertEqual(sanitized.main_window_geometry_b64, "AAA")
        self.assertEqual(sanitized.main_window_screen_name, "Secondary Monitor")
        self.assertEqual(sanitized.main_window_screen_serial, "SERIAL-42")
        self.assertEqual(sanitized.main_window_frame_rect, [120, 80, 1440, 900])
        self.assertEqual(sanitized.main_window_splitter_b64, "BBB")
        self.assertEqual(sanitized.session_list_window_geometry_b64, "FLOAT")
        self.assertEqual(sanitized.session_list_window_screen_name, "Session Secondary")
        self.assertEqual(sanitized.session_list_window_screen_serial, "SESSION-2")
        self.assertEqual(sanitized.session_list_window_frame_rect, [2200, 180, 420, 700])
        self.assertEqual(sanitized.workspace_profiles[0]["startup_tools"], ["ping", "help"])
        snapshot = sanitized.workspace_profiles[0]["snapshot"]
        self.assertEqual(snapshot["window_geometry_b64"], "SNAP")
        self.assertEqual(snapshot["window_screen_name"], "Secondary Monitor")
        self.assertEqual(snapshot["window_screen_serial"], "SERIAL-42")
        self.assertEqual(snapshot["window_frame_rect"], [2100, 140, 900, 700])
        self.assertEqual(snapshot["main_splitter_b64"], "SPLIT")
        self.assertEqual(snapshot["session_list_mode"], "auto")
        self.assertFalse(snapshot["session_list_visible"])
        self.assertEqual(snapshot["session_list_last_width"], 500)
        self.assertEqual(snapshot["session_list_window_geometry_b64"], "PROFILE-FLOAT")
        self.assertEqual(snapshot["session_list_window_screen_name"], "Profile Secondary")
        self.assertEqual(snapshot["session_list_window_screen_serial"], "PROFILE-2")
        self.assertEqual(snapshot["session_list_window_frame_rect"], [2300, 190, 430, 710])
        self.assertEqual(snapshot["detached_windows"][0]["window_geometry_b64"], "DETACHED")
        self.assertEqual(snapshot["detached_windows"][0]["window_screen_name"], "Detached Secondary")
        self.assertEqual(snapshot["detached_windows"][0]["window_screen_serial"], "DETACHED-1")
        self.assertEqual(snapshot["detached_windows"][0]["window_frame_rect"], [2200, 160, 640, 420])

    def test_load_scrubs_foreign_cross_platform_settings_file(self) -> None:
        raw = AppSettings.defaults().to_dict()
        raw["terminal_log_dir"] = r"C:\Users\tester\Documents\SnakeSh Logs"
        raw["main_window_geometry_b64"] = "AAA"
        raw["main_window_screen_name"] = "Secondary Monitor"
        raw["main_window_screen_serial"] = "SERIAL-42"
        raw["main_window_frame_rect"] = [120, 80, 1440, 900]
        raw["session_list_window_geometry_b64"] = "FLOAT"
        raw["session_list_window_screen_name"] = "Session Secondary"
        raw["session_list_window_screen_serial"] = "SESSION-2"
        raw["session_list_window_frame_rect"] = [2200, 180, 420, 700]
        raw["workspace_profiles"] = [
            {
                "id": "profile-1",
                "name": "Imported",
                "snapshot": {
                    "workspace_tree": {"type": "host", "host_key": "host-1"},
                    "window_geometry_b64": "SNAP",
                    "window_screen_name": "Secondary Monitor",
                    "window_screen_serial": "SERIAL-42",
                    "window_frame_rect": [2100, 140, 900, 700],
                    "session_list_window_geometry_b64": "PROFILE-FLOAT",
                    "session_list_window_screen_name": "Profile Secondary",
                    "session_list_window_screen_serial": "PROFILE-2",
                    "session_list_window_frame_rect": [2300, 190, 430, 710],
                },
            }
        ]

        with tempfile.TemporaryDirectory() as tmp:
            with patch("snakesh.services.settings_service.data_dir", return_value=Path(tmp)), patch(
                "snakesh.services.settings_service.platform.system",
                return_value="Linux",
            ):
                settings_path = Path(tmp) / "settings.json"
                settings_path.write_text(json.dumps(raw), encoding="utf-8")
                loaded = SettingsService().load()

        self.assertEqual(loaded.terminal_log_dir, AppSettings.defaults().terminal_log_dir)
        self.assertEqual(loaded.main_window_geometry_b64, "")
        self.assertEqual(loaded.main_window_screen_name, "")
        self.assertEqual(loaded.main_window_screen_serial, "")
        self.assertEqual(loaded.main_window_frame_rect, [])
        self.assertEqual(loaded.session_list_window_geometry_b64, "")
        self.assertEqual(loaded.session_list_window_screen_name, "")
        self.assertEqual(loaded.session_list_window_screen_serial, "")
        self.assertEqual(loaded.session_list_window_frame_rect, [])
        snapshot = loaded.workspace_profiles[0]["snapshot"]
        self.assertNotIn("window_geometry_b64", snapshot)
        self.assertNotIn("window_screen_name", snapshot)
        self.assertNotIn("window_screen_serial", snapshot)
        self.assertNotIn("window_frame_rect", snapshot)
        self.assertNotIn("session_list_window_geometry_b64", snapshot)
        self.assertNotIn("session_list_window_screen_name", snapshot)
        self.assertNotIn("session_list_window_screen_serial", snapshot)
        self.assertNotIn("session_list_window_frame_rect", snapshot)

    def test_roundtrip_preserves_saved_main_window_splitter(self) -> None:
        settings = AppSettings.defaults()
        settings.main_window_splitter_b64 = "AQABAAIA"

        restored = AppSettings.from_dict(settings.to_dict())

        self.assertEqual(restored.main_window_splitter_b64, "AQABAAIA")

    def test_roundtrip_preserves_linux_desktop_prompt_preference(self) -> None:
        settings = AppSettings.defaults()
        settings.linux_desktop_prompt_dismissed = True

        restored = AppSettings.from_dict(settings.to_dict())

        self.assertTrue(restored.linux_desktop_prompt_dismissed)

    def test_roundtrip_preserves_linux_desktop_update_prompt_version(self) -> None:
        settings = AppSettings.defaults()
        settings.linux_desktop_last_update_prompt_version = "0.7.2"

        restored = AppSettings.from_dict(settings.to_dict())

        self.assertEqual(restored.linux_desktop_last_update_prompt_version, "0.7.2")

    def test_sanitize_trims_linux_desktop_update_prompt_version(self) -> None:
        settings = AppSettings.defaults()
        settings.linux_desktop_last_update_prompt_version = "  1.2.3  "

        sanitized = SettingsService._sanitize(settings)

        self.assertEqual(sanitized.linux_desktop_last_update_prompt_version, "1.2.3")

    def test_sanitize_trims_saved_main_window_splitter(self) -> None:
        settings = AppSettings.defaults()
        settings.main_window_splitter_b64 = "  AQABAAIA  "

        sanitized = SettingsService._sanitize(settings)

        self.assertEqual(sanitized.main_window_splitter_b64, "AQABAAIA")

    def test_roundtrip_preserves_web_server_dialog_splitter(self) -> None:
        settings = AppSettings.defaults()
        settings.web_server_dialog_splitter_b64 = "AQABAAIA"

        restored = AppSettings.from_dict(settings.to_dict())

        self.assertEqual(restored.web_server_dialog_splitter_b64, "AQABAAIA")

    def test_sanitize_trims_web_server_dialog_splitter(self) -> None:
        settings = AppSettings.defaults()
        settings.web_server_dialog_splitter_b64 = "  AQABAAIA  "

        sanitized = SettingsService._sanitize(settings)

        self.assertEqual(sanitized.web_server_dialog_splitter_b64, "AQABAAIA")

    def test_roundtrip_preserves_syslog_snmp_monitor_dialog_splitter(self) -> None:
        settings = AppSettings.defaults()
        settings.syslog_snmp_monitor_dialog_splitter_b64 = "AQABAAIB"

        restored = AppSettings.from_dict(settings.to_dict())

        self.assertEqual(restored.syslog_snmp_monitor_dialog_splitter_b64, "AQABAAIB")

    def test_sanitize_trims_syslog_snmp_monitor_dialog_splitter(self) -> None:
        settings = AppSettings.defaults()
        settings.syslog_snmp_monitor_dialog_splitter_b64 = "  AQABAAIB  "

        sanitized = SettingsService._sanitize(settings)

        self.assertEqual(sanitized.syslog_snmp_monitor_dialog_splitter_b64, "AQABAAIB")

    def test_roundtrip_preserves_rdp_trusted_certificate_hosts(self) -> None:
        settings = AppSettings.defaults()
        settings.rdp_trusted_certificate_hosts = ["server-a|3389", "server-b|3390"]

        restored = AppSettings.from_dict(settings.to_dict())

        self.assertEqual(
            restored.rdp_trusted_certificate_hosts,
            ["server-a|3389", "server-b|3390"],
        )

    def test_sanitize_normalizes_rdp_trusted_certificate_hosts(self) -> None:
        settings = AppSettings.defaults()
        settings.rdp_trusted_certificate_hosts = [" Server-A|3389 ", "server-a|3389", "", "server-b|3390"]

        sanitized = SettingsService._sanitize(settings)

        self.assertEqual(
            sanitized.rdp_trusted_certificate_hosts,
            ["server-a|3389", "server-b|3390"],
        )

    def test_roundtrip_preserves_master_password_fields(self) -> None:
        settings = AppSettings.defaults()
        settings.master_password_enabled = True
        settings.master_password_salt_b64 = "c2FsdA=="
        settings.master_password_hash_b64 = "aGFzaA=="

        restored = AppSettings.from_dict(settings.to_dict())

        self.assertTrue(restored.master_password_enabled)
        self.assertEqual(restored.master_password_salt_b64, "c2FsdA==")
        self.assertEqual(restored.master_password_hash_b64, "aGFzaA==")

    def test_sanitize_disables_master_password_when_not_fully_configured(self) -> None:
        settings = AppSettings.defaults()
        settings.master_password_enabled = True
        settings.master_password_salt_b64 = "   "
        settings.master_password_hash_b64 = "  aGFzaA==  "

        sanitized = SettingsService._sanitize(settings)

        self.assertFalse(sanitized.master_password_enabled)
        self.assertEqual(sanitized.master_password_salt_b64, "")
        self.assertEqual(sanitized.master_password_hash_b64, "")

    def test_sanitize_accepts_bitwarden_backend(self) -> None:
        settings = AppSettings.defaults()
        settings.secrets_backend = "BITWARDEN"

        sanitized = SettingsService._sanitize(settings)

        self.assertEqual(sanitized.secrets_backend, "bitwarden")

    def test_sanitize_restores_default_bitwarden_cli_path_when_blank(self) -> None:
        settings = AppSettings.defaults()
        settings.bitwarden_cli_path = "   "

        sanitized = SettingsService._sanitize(settings)

        self.assertEqual(sanitized.bitwarden_cli_path, "bw")

    def test_sanitize_accepts_keeper_backend(self) -> None:
        settings = AppSettings.defaults()
        settings.secrets_backend = "KEEPER"

        sanitized = SettingsService._sanitize(settings)

        self.assertEqual(sanitized.secrets_backend, "keeper")

    def test_sanitize_restores_keeper_defaults_when_blank(self) -> None:
        settings = AppSettings.defaults()
        settings.keeper_cli_path = "   "
        settings.keeper_folder = "   "
        settings.keeper_server = "   "
        settings.keeper_user = "  user@example.com  "

        sanitized = SettingsService._sanitize(settings)

        self.assertEqual(sanitized.keeper_cli_path, "keeper")
        self.assertEqual(sanitized.keeper_folder, "SnakeSh")
        self.assertEqual(sanitized.keeper_server, "")
        self.assertEqual(sanitized.keeper_user, "user@example.com")

    def test_sanitize_accepts_keepass_backend(self) -> None:
        settings = AppSettings.defaults()
        settings.secrets_backend = "KEEPASS"

        sanitized = SettingsService._sanitize(settings)

        self.assertEqual(sanitized.secrets_backend, "keepass")

    def test_sanitize_restores_keepass_defaults_when_blank(self) -> None:
        settings = AppSettings.defaults()
        settings.keepass_cli_path = "   "
        settings.keepass_password_env = "   "
        settings.keepass_group = "   "
        settings.keepass_database_path = "   "
        settings.keepass_key_file_path = "   "

        sanitized = SettingsService._sanitize(settings)

        self.assertEqual(sanitized.keepass_cli_path, "keepassxc-cli")
        self.assertEqual(sanitized.keepass_password_env, "KEEPASSXC_PASSWORD")
        self.assertEqual(sanitized.keepass_group, "SnakeSh")
        self.assertEqual(sanitized.keepass_database_path, "")
        self.assertEqual(sanitized.keepass_key_file_path, "")


if __name__ == "__main__":
    unittest.main()
