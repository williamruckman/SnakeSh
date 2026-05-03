from __future__ import annotations

import os
import unittest

from PySide6.QtWidgets import QApplication

from snakesh.services.settings_service import AppSettings
from snakesh.ui.file_hash_dialog import FileHashDialog


class FileHashDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls._app = QApplication.instance() or QApplication([])

    def test_apply_runtime_settings_updates_details_font_only(self) -> None:
        dialog = FileHashDialog()
        try:
            dialog.details.setPlainText("hash details")
            before_file_font = dialog.file_input.font().pointSize()
            before_details_font = dialog.details.font().pointSize()

            settings = AppSettings.defaults()
            settings.terminal_font_pt = max(before_details_font + 4, 12)

            dialog.apply_runtime_settings(settings)
            QApplication.processEvents()

            self.assertEqual(dialog.details.toPlainText(), "hash details")
            self.assertEqual(dialog.details.font().pointSize(), settings.terminal_font_pt)
            self.assertEqual(dialog.file_input.font().pointSize(), before_file_font)
            self.assertGreater(dialog.details.font().pointSize(), before_details_font)
        finally:
            dialog.close()
            dialog.deleteLater()
            QApplication.processEvents()


if __name__ == "__main__":
    unittest.main()
