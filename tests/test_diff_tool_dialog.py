from __future__ import annotations

import os
import unittest

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QFontDatabase, QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from snakesh.services.settings_service import AppSettings
from snakesh.ui.diff_tool_dialog import DiffToolDialog, _compute_diffs, _scroll_center_ratio


class DiffToolDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls._app = QApplication.instance() or QApplication([])

    def _set_buffers(self, dialog: DiffToolDialog, left: str, right: str) -> None:
        dialog._left_editor.setPlainText(left)
        dialog._right_editor.setPlainText(right)
        dialog._diff_timer.stop()

    def _show_dialog(self, dialog: DiffToolDialog) -> None:
        dialog.resize(1100, 700)
        dialog.show()
        QApplication.processEvents()

    def _make_dialog(
        self,
        left: str = "",
        right: str = "",
        *,
        settings: AppSettings | None = None,
    ) -> DiffToolDialog:
        dialog = DiffToolDialog(settings=settings)
        self._set_buffers(dialog, left, right)
        self._show_dialog(dialog)
        return dialog

    def _prepare_long_diff(self, dialog: DiffToolDialog) -> None:
        left = "\n".join(f"line {i}" for i in range(300))
        right_lines = [f"line {i}" for i in range(300)]
        right_lines[220] = "changed line"
        right = "\n".join(right_lines)
        self._set_buffers(dialog, left, right)
        dialog._on_diff_finished(_compute_diffs(left, right), dialog._diff_generation)
        self._show_dialog(dialog)

    def _search(self, dialog: DiffToolDialog, pattern: str, side: str) -> None:
        if dialog._find_bar.isHidden():
            dialog._open_search_btn.click()
            QApplication.processEvents()
        if side == "right":
            dialog._find_bar._right_btn.click()
        else:
            dialog._find_bar._left_btn.click()
        dialog._find_bar._input.setText(pattern)
        dialog._find_bar._debounce.stop()
        dialog._find_bar._emit_search()

    def _point_for_position(self, editor, row: int, column: int) -> QPoint:
        block = editor.document().findBlockByNumber(row)
        assert block.isValid()
        cursor = QTextCursor(editor.document())
        cursor.setPosition(block.position() + min(len(block.text()), max(0, column)))
        rect = editor.cursorRect(cursor)
        return QPoint(rect.x(), rect.center().y())

    def test_search_bar_is_hidden_by_default(self) -> None:
        dialog = DiffToolDialog()
        try:
            self.assertTrue(dialog._find_bar.isHidden())
            self.assertTrue(dialog._find_bar._left_btn.isChecked())
            self.assertFalse(dialog._find_bar._right_btn.isChecked())
        finally:
            dialog.close()
            dialog.deleteLater()
            QApplication.processEvents()

    def test_search_button_toggles_search_bar(self) -> None:
        dialog = DiffToolDialog()
        try:
            self.assertTrue(dialog._find_bar.isHidden())

            dialog._open_search_btn.click()
            QApplication.processEvents()
            self.assertFalse(dialog._find_bar.isHidden())

            dialog._open_search_btn.click()
            QApplication.processEvents()
            self.assertTrue(dialog._find_bar.isHidden())
        finally:
            dialog.close()
            dialog.deleteLater()
            QApplication.processEvents()

    def test_left_search_only_highlights_left_matches(self) -> None:
        dialog = DiffToolDialog()
        try:
            self._set_buffers(dialog, "alpha\nbeta\nalpha\n", "beta\ngamma\n")

            self._search(dialog, "beta", "left")

            self.assertEqual(dialog._search_matches_left, [(6, 4)])
            self.assertEqual(dialog._search_matches_right, [])
            self.assertEqual(dialog._find_bar._match_label.text(), "1/1")
            self.assertEqual(dialog._left_editor.textCursor().position(), 6)
        finally:
            dialog.close()
            dialog.deleteLater()
            QApplication.processEvents()

    def test_right_search_navigation_stays_on_right_side(self) -> None:
        dialog = DiffToolDialog()
        try:
            self._set_buffers(dialog, "beta\nleft only\n", "beta\nbeta\n")

            self._search(dialog, "beta", "right")
            self.assertEqual(dialog._search_matches_left, [])
            self.assertEqual(dialog._search_matches_right, [(0, 4), (5, 4)])
            self.assertEqual(dialog._right_editor.textCursor().position(), 0)

            dialog._on_navigate(1)

            self.assertEqual(dialog._find_bar._match_label.text(), "2/2")
            self.assertEqual(dialog._right_editor.textCursor().position(), 5)
        finally:
            dialog.close()
            dialog.deleteLater()
            QApplication.processEvents()

    def test_escape_closes_search_bar(self) -> None:
        dialog = self._make_dialog("alpha\nbeta", "")
        try:
            dialog._open_search_btn.click()
            QApplication.processEvents()
            self.assertTrue(dialog._find_bar.isVisible())

            dialog._on_escape()

            self.assertTrue(dialog._find_bar.isHidden())
        finally:
            dialog.close()
            dialog.deleteLater()
            QApplication.processEvents()

    def test_apply_right_to_left_replaces_left_block(self) -> None:
        left = "left only\ncommon"
        right = "right only\ncommon"
        dialog = self._make_dialog(left, right)
        try:
            dialog._on_diff_finished(_compute_diffs(left, right), dialog._diff_generation)
            dialog._apply_right_to_left(0)
            self.assertEqual(dialog._left_editor.toPlainText(), right)
        finally:
            dialog.close()
            dialog.deleteLater()
            QApplication.processEvents()

    def test_ctrl_alt_click_does_not_enable_column_mode(self) -> None:
        dialog = self._make_dialog("alpha\nbeta\ngamma", "")
        try:
            point = self._point_for_position(dialog._left_editor, 1, 2)
            QTest.mouseClick(
                dialog._left_editor.viewport(),
                Qt.LeftButton,
                Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier,
                point,
            )
            QApplication.processEvents()

            self.assertFalse(dialog._left_editor.has_column_selection())
        finally:
            dialog.close()
            dialog.deleteLater()
            QApplication.processEvents()

    def test_diff_dialog_uses_settings_backed_terminal_font(self) -> None:
        fixed_pitch_families = [
            name for name in QFontDatabase.families() if QFontDatabase.isFixedPitch(name) and name.lower() != "fixedsys"
        ]
        if not fixed_pitch_families:
            self.skipTest("No fixed-pitch fonts available in test environment")

        settings = AppSettings.defaults()
        settings.terminal_font_family = fixed_pitch_families[0]
        settings.terminal_font_pt = 13
        dialog = self._make_dialog(settings=settings)
        try:
            self.assertEqual(dialog._left_editor.font().family().lower(), settings.terminal_font_family.lower())
            self.assertEqual(dialog._right_editor.font().family().lower(), settings.terminal_font_family.lower())
            self.assertEqual(dialog._left_editor.font().pointSize(), 13)
            self.assertEqual(dialog._right_editor.font().pointSize(), 13)
        finally:
            dialog.close()
            dialog.deleteLater()
            QApplication.processEvents()

    def test_apply_runtime_settings_updates_editor_fonts_without_disturbing_selection_or_scroll(self) -> None:
        dialog = self._make_dialog()
        try:
            long_line = " ".join(f"segment-{index:02d}" for index in range(32))
            left = "\n".join(f"{row:03d} {long_line}" for row in range(220))
            right_lines = left.splitlines()
            right_lines[140] = f"140 {long_line} changed"
            right = "\n".join(right_lines)
            self._set_buffers(dialog, left, right)
            dialog._on_diff_finished(_compute_diffs(left, right), dialog._diff_generation)
            self._show_dialog(dialog)

            block = dialog._left_editor.document().findBlockByNumber(140)
            cursor = dialog._left_editor.textCursor()
            cursor.setPosition(block.position() + 4)
            cursor.setPosition(block.position() + 24, QTextCursor.KeepAnchor)
            dialog._left_editor.setTextCursor(cursor)

            left_vbar = dialog._left_editor.verticalScrollBar()
            left_hbar = dialog._left_editor.horizontalScrollBar()
            right_vbar = dialog._right_editor.verticalScrollBar()
            right_hbar = dialog._right_editor.horizontalScrollBar()
            left_vbar.setValue(left_vbar.maximum() // 2)
            left_hbar.setValue(left_hbar.maximum() // 2)
            right_vbar.setValue(right_vbar.maximum() // 2)
            right_hbar.setValue(right_hbar.maximum() // 2)
            QApplication.processEvents()

            selection_before = (
                dialog._left_editor.textCursor().selectionStart(),
                dialog._left_editor.textCursor().selectionEnd(),
            )
            scroll_before = (
                left_vbar.value(),
                left_hbar.value(),
                right_vbar.value(),
                right_hbar.value(),
            )
            old_point_size = dialog._left_editor.font().pointSize()

            updated = AppSettings.defaults()
            updated.terminal_font_pt = max(old_point_size + 4, 12)

            dialog.apply_runtime_settings(updated)
            QApplication.processEvents()

            self.assertEqual(dialog._left_editor.toPlainText(), left)
            self.assertEqual(dialog._right_editor.toPlainText(), right)
            self.assertEqual(dialog._left_editor.font().pointSize(), updated.terminal_font_pt)
            self.assertEqual(dialog._right_editor.font().pointSize(), updated.terminal_font_pt)
            self.assertEqual(
                (
                    dialog._left_editor.textCursor().selectionStart(),
                    dialog._left_editor.textCursor().selectionEnd(),
                ),
                selection_before,
            )
            self.assertEqual(
                (
                    left_vbar.value(),
                    left_hbar.value(),
                    right_vbar.value(),
                    right_hbar.value(),
                ),
                scroll_before,
            )
        finally:
            dialog.close()
            dialog.deleteLater()
            QApplication.processEvents()

    def test_wide_files_do_not_force_large_dialog_minimum_width(self) -> None:
        dialog = self._make_dialog()
        try:
            wide_line = "x" * 5000
            self._set_buffers(dialog, f"{wide_line}\nleft", f"{wide_line}\nright")
            QApplication.processEvents()

            self.assertLessEqual(dialog._left_editor.minimumWidth(), 0)
            self.assertLessEqual(dialog._right_editor.minimumWidth(), 0)
            self.assertLess(dialog.minimumSizeHint().width(), 760)
        finally:
            dialog.close()
            dialog.deleteLater()
            QApplication.processEvents()

    def test_scroll_map_click_jumps_to_expected_location_and_keeps_panes_synced(self) -> None:
        dialog = DiffToolDialog()
        try:
            self._prepare_long_diff(dialog)
            block = next(blk for blk in dialog._diff_blocks if blk.tag != "equal")
            target_y = dialog._right_map._line_y_in_map(block.right_start, False, dialog._right_map.height())
            assert target_y is not None

            QTest.mouseClick(
                dialog._right_map,
                Qt.LeftButton,
                Qt.NoModifier,
                QPoint(dialog._right_map.width() // 2, target_y),
            )
            QApplication.processEvents()

            expected_ratio = block.right_start / max(1, dialog._right_editor.document().blockCount())
            right_ratio = _scroll_center_ratio(dialog._right_editor.verticalScrollBar())
            left_ratio = _scroll_center_ratio(dialog._left_editor.verticalScrollBar())
            self.assertAlmostEqual(right_ratio, expected_ratio, delta=0.06)
            self.assertAlmostEqual(left_ratio, right_ratio, delta=0.03)
        finally:
            dialog.close()
            dialog.deleteLater()
            QApplication.processEvents()

    def test_native_diff_scrollbar_groove_click_jumps_absolutely_and_keeps_panes_synced(self) -> None:
        dialog = DiffToolDialog()
        try:
            self._prepare_long_diff(dialog)
            bar = dialog._right_editor.verticalScrollBar()
            bar.setValue(0)
            QApplication.processEvents()

            target_y = round(bar.height() * 0.78)
            QTest.mouseClick(
                bar,
                Qt.LeftButton,
                Qt.NoModifier,
                QPoint(bar.width() // 2, target_y),
            )
            QApplication.processEvents()

            right_ratio = _scroll_center_ratio(bar)
            left_ratio = _scroll_center_ratio(dialog._left_editor.verticalScrollBar())
            self.assertGreater(right_ratio, 0.65)
            self.assertAlmostEqual(left_ratio, right_ratio, delta=0.03)
        finally:
            dialog.close()
            dialog.deleteLater()
            QApplication.processEvents()


if __name__ == "__main__":
    unittest.main()
