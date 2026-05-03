from __future__ import annotations

import os
from pathlib import Path
import unittest

from PySide6.QtWidgets import QApplication

from snakesh.services.settings_service import AppSettings
from snakesh.ui import theme


class ThemeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls._app = QApplication.instance() or QApplication([])

    def test_arrow_icon_paths_are_generated(self) -> None:
        up_path = theme._arrow_icon_path("up", "#ffffff")
        down_path = theme._arrow_icon_path("down", "#ffffff")

        self.assertTrue(up_path)
        self.assertTrue(down_path)
        self.assertTrue(Path(up_path).exists())
        self.assertTrue(Path(down_path).exists())

    def test_apply_theme_includes_explicit_arrow_icons(self) -> None:
        theme.apply_theme(self._app, AppSettings.defaults())
        stylesheet = self._app.styleSheet()

        self.assertIn("QComboBox::down-arrow", stylesheet)
        self.assertIn("QSpinBox::up-arrow", stylesheet)
        self.assertIn("QSpinBox::down-arrow", stylesheet)
        self.assertIn("arrow-up", stylesheet)
        self.assertIn("arrow-down", stylesheet)


if __name__ == "__main__":
    unittest.main()
