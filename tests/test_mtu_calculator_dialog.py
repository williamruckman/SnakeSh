from __future__ import annotations

import os
import unittest

from PySide6.QtWidgets import QApplication

from snakesh.ui.mtu_calculator_dialog import MTUCalculatorDialog


class MtuCalculatorDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls._app = QApplication.instance() or QApplication([])

    def test_dialog_shows_default_ipv4_results(self) -> None:
        dialog = MTUCalculatorDialog()
        try:
            self.assertEqual(dialog.effective_mtu_output.text(), "1500")
            self.assertEqual(dialog.ping_payload_output.text(), "1472")
            self.assertEqual(dialog.udp_payload_output.text(), "1472")
            self.assertEqual(dialog.tcp_mss_output.text(), "1460")
            self.assertTrue(dialog.copy_btn.isEnabled())
            self.assertTrue(dialog.send_to_ping_btn.isEnabled())
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_custom_overhead_input_enables_and_recalculates(self) -> None:
        dialog = MTUCalculatorDialog()
        try:
            dialog.overhead_preset_input.setCurrentText("Custom")
            dialog.custom_overhead_input.setValue(40)
            QApplication.processEvents()

            self.assertTrue(dialog.custom_overhead_input.isEnabled())
            self.assertEqual(dialog.effective_mtu_output.text(), "1460")
            self.assertEqual(dialog.ping_payload_output.text(), "1432")
            self.assertEqual(dialog.tcp_mss_output.text(), "1420")
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_copy_summary_writes_clipboard(self) -> None:
        dialog = MTUCalculatorDialog()
        try:
            dialog._copy_summary()

            clipboard_text = QApplication.clipboard().text()
            self.assertIn("SnakeSh MTU / MSS Calculator", clipboard_text)
            self.assertIn("Max Ping Payload: 1472", clipboard_text)
            self.assertEqual(dialog.status_label.text(), "Summary copied to clipboard.")
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_invalid_values_disable_actions(self) -> None:
        dialog = MTUCalculatorDialog()
        try:
            dialog.outer_mtu_input.setValue(1)
            QApplication.processEvents()

            self.assertFalse(dialog.copy_btn.isEnabled())
            self.assertFalse(dialog.send_to_ping_btn.isEnabled())
            self.assertEqual(dialog.effective_mtu_output.text(), "")
            self.assertIn("Effective MTU must be greater than", dialog.status_label.text())
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_send_to_ping_callback_receives_payload_and_ip_version(self) -> None:
        calls: list[tuple[int, bool]] = []
        dialog = MTUCalculatorDialog(on_send_to_ping=lambda packet_size, ipv6: calls.append((packet_size, ipv6)))
        try:
            dialog.ip_version_input.setCurrentIndex(1)
            QApplication.processEvents()

            dialog._send_to_ping()

            self.assertEqual(calls, [(1452, True)])
            self.assertEqual(dialog.status_label.text(), "Ping prefilled with the calculated packet size.")
        finally:
            dialog.deleteLater()
            QApplication.processEvents()
