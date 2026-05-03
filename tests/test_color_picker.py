from __future__ import annotations

import unittest
from unittest.mock import patch

from PySide6.QtGui import QColor

from snakesh.ui.color_picker import pick_color


class _FakeSignal:
    def __init__(self) -> None:
        self.connected: list[object] = []

    def connect(self, callback) -> None:
        self.connected.append(callback)


class _FakeColorDialog:
    DontUseNativeDialog = 99
    last_instance: _FakeColorDialog | None = None
    next_exec_result = False
    next_selected_color = QColor()

    def __init__(self, parent=None) -> None:
        self.parent = parent
        self.title = ""
        self.options: list[tuple[int, bool]] = []
        self.current_color = QColor()
        self.currentColorChanged = _FakeSignal()
        _FakeColorDialog.last_instance = self

    def setWindowTitle(self, title: str) -> None:
        self.title = title

    def setOption(self, option: int, enabled: bool = True) -> None:
        self.options.append((option, enabled))

    def setCurrentColor(self, color: QColor) -> None:
        self.current_color = QColor(color)

    def exec(self) -> bool:
        return bool(_FakeColorDialog.next_exec_result)

    def selectedColor(self) -> QColor:
        return QColor(_FakeColorDialog.next_selected_color)


class ColorPickerTests(unittest.TestCase):
    def setUp(self) -> None:
        _FakeColorDialog.last_instance = None
        _FakeColorDialog.next_exec_result = False
        _FakeColorDialog.next_selected_color = QColor()

    def test_pick_color_seeds_dialog_with_initial_color(self) -> None:
        with (
            patch("snakesh.ui.color_picker.QColorDialog", _FakeColorDialog),
            patch("snakesh.ui.color_picker.platform.system", return_value="Linux"),
        ):
            pick_color(None, title="Pick", initial=QColor("#ffffff"))

        dialog = _FakeColorDialog.last_instance
        self.assertIsNotNone(dialog)
        assert dialog is not None
        self.assertEqual(dialog.current_color.name(), "#ffffff")
        self.assertIn((_FakeColorDialog.DontUseNativeDialog, True), dialog.options)

    def test_pick_color_falls_back_to_white_for_invalid_initial(self) -> None:
        with patch("snakesh.ui.color_picker.QColorDialog", _FakeColorDialog):
            pick_color(None, title="Pick", initial=QColor())

        dialog = _FakeColorDialog.last_instance
        self.assertIsNotNone(dialog)
        assert dialog is not None
        self.assertEqual(dialog.current_color.name(), "#ffffff")

    def test_pick_color_returns_selected_color_after_accept(self) -> None:
        _FakeColorDialog.next_exec_result = True
        _FakeColorDialog.next_selected_color = QColor("#123456")

        with patch("snakesh.ui.color_picker.QColorDialog", _FakeColorDialog):
            chosen = pick_color(None, title="Pick", initial=QColor("#ffffff"))

        self.assertTrue(chosen.isValid())
        self.assertEqual(chosen.name(), "#123456")


if __name__ == "__main__":
    unittest.main()
