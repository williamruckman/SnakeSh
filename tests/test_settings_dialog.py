from __future__ import annotations

import os
from pathlib import Path
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton

from snakesh.core.theme_presets import theme_colors_for
from snakesh.services.settings_service import AppSettings
from snakesh.ui.settings_dialog import SettingsDialog


class SettingsDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls._app = QApplication.instance() or QApplication([])

    @staticmethod
    def _set_combo_by_data(dialog: SettingsDialog, theme_id: str) -> None:
        for index in range(dialog.theme_name.count()):
            if dialog.theme_name.itemData(index) == theme_id:
                dialog.theme_name.setCurrentIndex(index)
                return
        raise AssertionError(f"Theme not found in combo box: {theme_id}")

    def test_theme_picker_defaults_to_onyx(self) -> None:
        dialog = SettingsDialog(settings=AppSettings.defaults())
        try:
            self.assertEqual(dialog.theme_name.currentData(), "onyx")
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_active_tab_close_prompt_defaults_checked(self) -> None:
        dialog = SettingsDialog(settings=AppSettings.defaults())
        try:
            self.assertTrue(dialog.warn_before_closing_active_tab.isChecked())
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_sftp_overwrite_prompt_defaults_checked(self) -> None:
        dialog = SettingsDialog(settings=AppSettings.defaults())
        try:
            self.assertTrue(dialog.warn_before_file_overwrite.isChecked())
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_selecting_theme_applies_preset_colors(self) -> None:
        dialog = SettingsDialog(settings=AppSettings.defaults())
        try:
            self._set_combo_by_data(dialog, "midnight")
            expected = theme_colors_for("midnight")
            assert expected is not None
            self.assertEqual(dialog.accent_color.text(), expected["accent_color"])
            self.assertEqual(dialog.field_bg.text(), expected["field_bg"])
            self.assertEqual(dialog.terminal_fg.text(), expected["terminal_fg"])
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_manual_color_change_switches_theme_to_custom(self) -> None:
        dialog = SettingsDialog(settings=AppSettings.defaults())
        try:
            self._set_combo_by_data(dialog, "midnight")
            dialog.accent_color.setText("#123456")
            self.assertEqual(dialog.theme_name.currentData(), "custom")
            saved = dialog.build_settings()
            self.assertEqual(saved.theme_name, "custom")
            self.assertEqual(saved.accent_color, "#123456")
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_restore_defaults_cancel_keeps_current_values(self) -> None:
        settings = AppSettings.defaults()
        settings.accent_color = "#123456"
        dialog = SettingsDialog(settings=settings)
        try:
            with patch("snakesh.ui.settings_dialog.QMessageBox.warning", return_value=QMessageBox.No) as mock_warning:
                dialog._restore_defaults()
            self.assertEqual(dialog.accent_color.text(), "#123456")
            self.assertTrue(mock_warning.called)
            warning_text = str(mock_warning.call_args.args[2])
            self.assertIn("Workspace Profiles", warning_text)
            self.assertIn("Fast Commands", warning_text)
            self.assertIn("Web Server Profiles", warning_text)
            self.assertIn("Syslog / SNMP Monitor Profiles", warning_text)
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_restore_defaults_yes_clears_profiles_and_fast_commands(self) -> None:
        settings = AppSettings.defaults()
        settings.accent_color = "#123456"
        settings.fast_commands = [{"id": "cmd-1", "name": "Test", "command": "echo hi"}]
        settings.workspace_profiles = [
            {"id": "profile-1", "name": "Lab", "snapshot": {"workspace_tree": {"type": "host"}}}
        ]
        settings.web_server_profiles = [
            {
                "id": "web-1",
                "name": "Docs Server",
                "config": {"bind_host": "127.0.0.1", "port": 8000, "document_root": "/tmp"},
            }
        ]
        settings.default_workspace_profile_id = "profile-1"
        settings.last_web_server_profile_id = "web-1"
        settings.syslog_snmp_monitor_profiles = [
            {
                "id": "monitor-1",
                "name": "SOC Monitor",
                "config": {"bind_host": "0.0.0.0", "syslog_udp_port": 1514},
            }
        ]
        settings.last_syslog_snmp_monitor_profile_id = "monitor-1"

        dialog = SettingsDialog(settings=settings)
        try:
            with patch("snakesh.ui.settings_dialog.QMessageBox.warning", return_value=QMessageBox.Yes):
                dialog._restore_defaults()
            restored = dialog.build_settings()
            defaults = AppSettings.defaults()
            self.assertEqual(dialog.accent_color.text(), defaults.accent_color)
            self.assertEqual(dialog.theme_name.currentData(), "onyx")
            self.assertEqual(restored.theme_name, "onyx")
            self.assertEqual(restored.fast_commands, [])
            self.assertEqual(restored.workspace_profiles, [])
            self.assertEqual(restored.web_server_profiles, [])
            self.assertEqual(restored.syslog_snmp_monitor_profiles, [])
            self.assertEqual(restored.default_workspace_profile_id, "")
            self.assertEqual(restored.last_web_server_profile_id, "")
            self.assertEqual(restored.last_syslog_snmp_monitor_profile_id, "")
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_manage_tool_launchers_button_invokes_callback_when_available(self) -> None:
        callback = unittest.mock.Mock()
        dialog = SettingsDialog(
            settings=AppSettings.defaults(),
            on_manage_tool_launchers_requested=callback,
        )
        try:
            manage_button = next(
                widget
                for widget in dialog.findChildren(QPushButton)
                if widget.text() == "Manage Tool Launchers..."
            )
            manage_button.click()
            callback.assert_called_once_with()
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_build_settings_preserves_fullscreen_shortcut(self) -> None:
        settings = AppSettings.defaults()
        dialog = SettingsDialog(settings=settings)
        try:
            dialog.main_window_fullscreen_shortcut.set_shortcut_text("Ctrl+Shift+F11")
            saved = dialog.build_settings()
            self.assertEqual(saved.main_window_fullscreen_shortcut, "Ctrl+Shift+F11")
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_build_settings_preserves_hide_controls_in_fullscreen(self) -> None:
        settings = AppSettings.defaults()
        dialog = SettingsDialog(settings=settings)
        try:
            dialog.main_window_hide_controls_in_fullscreen.setChecked(True)
            saved = dialog.build_settings()
            self.assertTrue(saved.main_window_hide_controls_in_fullscreen)
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_build_settings_preserves_tool_master_password_prompt_preference(self) -> None:
        settings = AppSettings.defaults()
        settings.master_password_salt_b64 = "salt"
        settings.master_password_hash_b64 = "hash"
        dialog = SettingsDialog(settings=settings)
        try:
            dialog.master_password_tools_enabled.setChecked(True)
            saved = dialog.build_settings()
            self.assertTrue(saved.master_password_tools_enabled)
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_build_settings_preserves_classic_terminal_default_color_preference(self) -> None:
        settings = AppSettings.defaults()
        dialog = SettingsDialog(settings=settings)
        try:
            dialog.terminal_classic_default_colors.setChecked(True)
            saved = dialog.build_settings()
            self.assertTrue(saved.terminal_classic_default_colors)
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_build_settings_preserves_web_server_log_cleanup_preferences(self) -> None:
        settings = AppSettings.defaults()
        dialog = SettingsDialog(settings=settings)
        try:
            dialog.web_server_log_cleanup_enabled.setChecked(False)
            dialog.web_server_log_retention_days.setValue(14)
            saved = dialog.build_settings()
            self.assertFalse(saved.web_server_log_cleanup_enabled)
            self.assertEqual(saved.web_server_log_retention_days, 14)
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_build_settings_preserves_active_tab_close_prompt_preference(self) -> None:
        settings = AppSettings.defaults()
        dialog = SettingsDialog(settings=settings)
        try:
            dialog.warn_before_closing_active_tab.setChecked(False)
            saved = dialog.build_settings()
            self.assertFalse(saved.warn_before_closing_active_tab)
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_build_settings_preserves_sftp_overwrite_prompt_preference(self) -> None:
        settings = AppSettings.defaults()
        dialog = SettingsDialog(settings=settings)
        try:
            dialog.warn_before_file_overwrite.setChecked(False)
            saved = dialog.build_settings()
            self.assertFalse(saved.warn_before_file_overwrite)
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_apply_settings_reloads_active_tab_close_prompt_preference(self) -> None:
        settings = AppSettings.defaults()
        dialog = SettingsDialog(settings=settings)
        try:
            updated = AppSettings.defaults()
            updated.warn_before_closing_active_tab = False
            dialog.apply_settings(updated)
            self.assertFalse(dialog.warn_before_closing_active_tab.isChecked())
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_apply_settings_reloads_sftp_overwrite_prompt_preference(self) -> None:
        settings = AppSettings.defaults()
        dialog = SettingsDialog(settings=settings)
        try:
            updated = AppSettings.defaults()
            updated.warn_before_file_overwrite = False
            dialog.apply_settings(updated)
            self.assertFalse(dialog.warn_before_file_overwrite.isChecked())
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_apply_settings_reloads_classic_terminal_default_color_preference(self) -> None:
        settings = AppSettings.defaults()
        dialog = SettingsDialog(settings=settings)
        try:
            updated = AppSettings.defaults()
            updated.terminal_classic_default_colors = True
            dialog.apply_settings(updated)
            self.assertTrue(dialog.terminal_classic_default_colors.isChecked())
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_scrollback_ram_estimate_describes_current_setting(self) -> None:
        settings = AppSettings.defaults()
        dialog = SettingsDialog(settings=settings)
        try:
            estimate_text = dialog.scrollback_ram_estimate.text()
            self.assertIn("Rough upper bound", estimate_text)
            self.assertIn(f"{settings.terminal_scrollback_lines:,} lines", estimate_text)
            self.assertIn("per terminal", estimate_text.lower())
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_scrollback_ram_estimate_updates_when_line_count_changes(self) -> None:
        dialog = SettingsDialog(settings=AppSettings.defaults())
        try:
            before = dialog.scrollback_ram_estimate.text()
            dialog.scrollback.setValue(10000)
            QApplication.processEvents()
            after = dialog.scrollback_ram_estimate.text()
            self.assertNotEqual(before, after)
            self.assertIn("10,000 lines", after)
            self.assertIn("per terminal", after.lower())
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_open_session_log_folder_opens_selected_directory(self) -> None:
        settings = AppSettings.defaults()
        dialog = SettingsDialog(settings=settings)
        try:
            dialog.log_dir.setText("/tmp/snakesh-test-logs")
            with patch("snakesh.ui.settings_dialog.open_local_path", return_value=True) as mock_open:
                dialog._open_folder_path(dialog.log_dir.text(), label="Session Log Folder")
            self.assertEqual(mock_open.call_args.args[0], Path("/tmp/snakesh-test-logs"))
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_open_crash_log_folder_uses_logs_directory(self) -> None:
        settings = AppSettings.defaults()
        dialog = SettingsDialog(settings=settings)
        try:
            with (
                patch("snakesh.ui.settings_dialog.data_dir", return_value=Path("/tmp/snakesh-data")),
                patch("snakesh.ui.settings_dialog.open_local_path", return_value=True) as mock_open,
            ):
                dialog._open_crash_log_folder()
            self.assertEqual(mock_open.call_args.args[0], Path("/tmp/snakesh-data/logs"))
        finally:
            dialog.deleteLater()
            QApplication.processEvents()


if __name__ == "__main__":
    unittest.main()
