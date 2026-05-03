from __future__ import annotations

import os
import unittest

from PySide6.QtWidgets import QApplication, QDialogButtonBox, QLineEdit

from snakesh.core.models import Protocol, Session, SSHAutomationStep
from snakesh.ui.session_editor import SessionEditorDialog


class SessionEditorDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls._app = QApplication.instance() or QApplication([])

    def test_automation_controls_visible_for_telnet_and_serial(self) -> None:
        dialog = SessionEditorDialog()
        try:
            dialog.protocol_input.setCurrentText("TELNET")
            dialog._update_protocol_specific_fields()
            self.assertFalse(dialog.ssh_automation_enabled_input.isHidden())
            self.assertFalse(dialog.ssh_automation_button.isHidden())
            self.assertFalse(dialog.ssh_automation_summary.isHidden())
            self.assertFalse(dialog.telnet_use_tls_input.isHidden())
            self.assertFalse(dialog.telnet_tls_verify_input.isHidden())

            dialog.protocol_input.setCurrentText("SERIAL")
            dialog._update_protocol_specific_fields()
            self.assertFalse(dialog.ssh_automation_enabled_input.isHidden())
            self.assertFalse(dialog.ssh_automation_button.isHidden())
            self.assertFalse(dialog.ssh_automation_summary.isHidden())
            self.assertTrue(dialog.telnet_use_tls_input.isHidden())
            self.assertTrue(dialog.telnet_tls_verify_input.isHidden())

            dialog.protocol_input.setCurrentText("RDP")
            dialog._update_protocol_specific_fields()
            self.assertTrue(dialog.ssh_automation_enabled_input.isHidden())
            self.assertTrue(dialog.ssh_automation_button.isHidden())
            self.assertTrue(dialog.ssh_automation_summary.isHidden())
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_build_session_retains_telnet_automation_settings(self) -> None:
        dialog = SessionEditorDialog()
        try:
            dialog.protocol_input.setCurrentText("TELNET")
            dialog._update_protocol_specific_fields()
            dialog.name_input.setText("Telnet Host")
            dialog.host_input.setText("127.0.0.1")
            dialog.user_input.setText("tester")
            dialog.port_input.setValue(23)
            dialog.telnet_use_tls_input.setChecked(True)
            dialog.telnet_tls_verify_input.setChecked(False)
            dialog.ssh_automation_enabled_input.setChecked(True)
            dialog._ssh_automation_steps = [
                SSHAutomationStep(step_type="command", command="show version"),
                SSHAutomationStep(step_type="expect", expect_text="Password:"),
            ]

            session = dialog.build_session()

            self.assertEqual(session.protocol, Protocol.TELNET)
            self.assertTrue(session.ssh_automation_enabled)
            self.assertEqual(len(session.ssh_automation_steps), 2)
            self.assertEqual(session.ssh_automation_steps[0].step_type, "command")
            self.assertEqual(session.ssh_automation_steps[1].step_type, "expect")
            self.assertTrue(session.telnet_use_tls)
            self.assertFalse(session.telnet_tls_verify)
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_build_session_retains_serial_automation_settings(self) -> None:
        dialog = SessionEditorDialog()
        try:
            dialog.protocol_input.setCurrentText("SERIAL")
            dialog._update_protocol_specific_fields()
            dialog.name_input.setText("Serial Lab")
            dialog._set_serial_port("/dev/ttyUSB0")
            dialog._set_serial_terminal_type("vt100")
            dialog.ssh_automation_enabled_input.setChecked(True)
            dialog._ssh_automation_steps = [SSHAutomationStep(step_type="sleep", sleep_seconds=2.0)]

            session = dialog.build_session()

            self.assertEqual(session.protocol, Protocol.SERIAL)
            self.assertEqual(session.host, "/dev/ttyUSB0")
            self.assertEqual(session.serial_terminal_type, "vt100")
            self.assertTrue(session.ssh_automation_enabled)
            self.assertEqual(len(session.ssh_automation_steps), 1)
            self.assertEqual(session.ssh_automation_steps[0].step_type, "sleep")
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_build_session_includes_terminal_color_override(self) -> None:
        dialog = SessionEditorDialog()
        try:
            dialog.name_input.setText("Color Session")
            dialog.host_input.setText("127.0.0.1")
            dialog.protocol_input.setCurrentText("SSH")
            dialog._update_protocol_specific_fields()
            dialog.terminal_color_override_input.setChecked(True)
            dialog.terminal_bg_color_input.setText("#112233")
            dialog.terminal_fg_color_input.setText("#f8fafc")

            session = dialog.build_session()

            self.assertTrue(session.terminal_color_override_enabled)
            self.assertEqual(session.terminal_bg_color, "#112233")
            self.assertEqual(session.terminal_fg_color, "#f8fafc")
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_terminal_color_preview_updates_with_override_values(self) -> None:
        dialog = SessionEditorDialog()
        try:
            dialog.terminal_color_override_input.setChecked(True)
            dialog.terminal_bg_color_input.setText("#102030")
            dialog.terminal_fg_color_input.setText("#f1f5f9")

            style = dialog.terminal_color_preview.styleSheet().lower()
            self.assertIn("#102030", style)
            self.assertIn("#f1f5f9", style)
            self.assertIn("border: 1px solid", style)
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_password_visibility_toggle_switches_echo_mode(self) -> None:
        dialog = SessionEditorDialog()
        try:
            self.assertEqual(dialog.password_input.echoMode(), QLineEdit.Password)
            self.assertEqual(dialog.password_visibility_btn.text(), "Show")
            dialog._set_password_visibility(True)
            self.assertEqual(dialog.password_input.echoMode(), QLineEdit.Normal)
            self.assertEqual(dialog.password_visibility_btn.text(), "Hide")
            dialog._set_password_visibility(False)
            self.assertEqual(dialog.password_input.echoMode(), QLineEdit.Password)
            self.assertEqual(dialog.password_visibility_btn.text(), "Show")
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_password_visibility_loads_saved_password_from_loader(self) -> None:
        session = Session(name="Saved", host="127.0.0.1", protocol=Protocol.SSH, port=22, save_password=True)
        calls: list[str] = []

        def _loader(target: Session) -> str | None:
            calls.append(target.id)
            return "from-keyring"

        dialog = SessionEditorDialog(session=session, password_loader=_loader)
        try:
            self.assertEqual(dialog.password_input.text(), "")
            self.assertIn("Saved in OS keyring", dialog.password_input.placeholderText())
            dialog._set_password_visibility(True)
            self.assertEqual(dialog.password_input.text(), "from-keyring")
            self.assertEqual(dialog.password_input.echoMode(), QLineEdit.Normal)
            self.assertEqual(len(calls), 1)
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_quick_connect_mode_updates_title_buttons_and_hides_library_fields(self) -> None:
        dialog = SessionEditorDialog(quick_connect=True)
        try:
            self.assertEqual(dialog.windowTitle(), "Quick Connect")
            ok_button = dialog._button_box.button(QDialogButtonBox.Ok)
            self.assertIsNotNone(ok_button)
            assert ok_button is not None
            self.assertEqual(ok_button.text(), "Connect")
            self.assertTrue(dialog.name_input.isHidden())
            self.assertTrue(dialog.folder_input.isHidden())
            self.assertTrue(dialog.tags_input.isHidden())
            self.assertTrue(dialog.notes_input.isHidden())
            self.assertTrue(dialog.save_password_input.isHidden())
            self.assertFalse(dialog.save_password_input.isEnabled())
            self.assertEqual(dialog.password_input.placeholderText(), "Optional for this connection")
            password_label = dialog._form.labelForField(dialog.password_row)
            self.assertIsNotNone(password_label)
            assert password_label is not None
            self.assertEqual(password_label.text(), "Password (this connection only)")
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_quick_connect_mode_keeps_protocol_specific_fields(self) -> None:
        dialog = SessionEditorDialog(quick_connect=True)
        try:
            dialog.protocol_input.setCurrentText("SFTP")
            dialog._update_protocol_specific_fields()
            self.assertFalse(dialog.host_input.isHidden())
            self.assertFalse(dialog.sftp_local_row.isHidden())
            self.assertFalse(dialog.sftp_remote_folder_input.isHidden())
            self.assertFalse(dialog.password_row.isHidden())
            self.assertTrue(dialog.save_password_input.isHidden())
            dialog.protocol_input.setCurrentText("SERIAL")
            dialog._update_protocol_specific_fields()
            self.assertFalse(dialog.serial_port_row.isHidden())
            self.assertFalse(dialog.serial_terminal_type_input.isHidden())
            self.assertTrue(dialog.password_row.isHidden())
        finally:
            dialog.deleteLater()
            QApplication.processEvents()


if __name__ == "__main__":
    unittest.main()
