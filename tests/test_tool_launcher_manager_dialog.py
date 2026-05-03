from __future__ import annotations

import os
import unittest

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QApplication

from snakesh.core.tool_registry import TOOL_REGISTRY
from snakesh.ui.tool_icon_helpers import TOOL_MENU_ICON_SIZE
from snakesh.ui.tool_launcher_manager_dialog import ToolLauncherManagerDialog


class ToolLauncherManagerDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls._app = QApplication.instance() or QApplication([])

    def test_tool_list_uses_same_sized_icons_as_tools_menu(self) -> None:
        dialog = ToolLauncherManagerDialog()
        try:
            self.assertEqual(dialog.tool_list.iconSize(), QSize(TOOL_MENU_ICON_SIZE, TOOL_MENU_ICON_SIZE))
            self.assertEqual(dialog.tool_list.count(), len(TOOL_REGISTRY))
            for index, entry in enumerate(TOOL_REGISTRY):
                item = dialog.tool_list.item(index)
                self.assertIsNotNone(item)
                assert item is not None
                self.assertEqual(item.text(), entry.label)
                self.assertFalse(item.icon().isNull())
                self.assertGreaterEqual(item.sizeHint().height(), TOOL_MENU_ICON_SIZE)
                actual_size = item.icon().actualSize(QSize(64, 64))
                self.assertLessEqual(actual_size.width(), TOOL_MENU_ICON_SIZE)
                self.assertLessEqual(actual_size.height(), TOOL_MENU_ICON_SIZE)
        finally:
            dialog.deleteLater()
            QApplication.processEvents()


if __name__ == "__main__":
    unittest.main()
