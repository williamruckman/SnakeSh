"""Exercise macOS's Qt modifier representation at the terminal/UI boundary."""
from __future__ import annotations

from unittest.mock import Mock, patch

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from snakesh.services.settings_service import AppSettings
from snakesh.ui.main_window import CommandInputLineEdit, TerminalTab, TerminalView


_APP = None


@pytest.fixture
def terminal():
    global _APP
    _APP = QApplication.instance() or QApplication([])
    tab = TerminalTab(settings=AppSettings.defaults())
    tab.append("hello world")
    with patch("snakesh.ui.main_window.platform.system", return_value="Darwin"):
        try:
            yield tab
        finally:
            tab._worker = None
            tab.shutdown()
            tab.deleteLater()
            _APP.processEvents()


@pytest.mark.parametrize("selected", [True, False])
@pytest.mark.parametrize("text", ["", "c", "\x03"])
def test_command_c_copies_selection_without_interrupting_the_terminal(terminal, selected, text):
    if selected:
        terminal.output._selection_anchor = (0, 0)
        terminal.output._selection_cursor = (4, 0)
    sent = []
    terminal.output.data_input.connect(sent.append)
    # Qt maps physical Command to ControlModifier on macOS.
    event = QKeyEvent(QEvent.KeyPress, Qt.Key_C, Qt.ControlModifier, text)
    with patch("snakesh.ui.main_window.copy_text") as copy:
        terminal.output.keyPressEvent(event)
    assert not sent
    assert copy.call_count == int(selected)
    if selected:
        copy.assert_called_once_with("hello")
        assert terminal.output._has_selection()
    assert event.isAccepted()


@pytest.mark.parametrize("key,expected", [(Qt.Key_C, "\x03"), (Qt.Key_A, "\x01"),
                                         (Qt.Key_D, "\x04"), (Qt.Key_V, "\x16"), (Qt.Key_Z, "\x1a")])
@pytest.mark.parametrize("system,modifier", [("Darwin", Qt.MetaModifier), ("Linux", Qt.ControlModifier),
                                          ("Windows", Qt.ControlModifier)])
def test_physical_control_keys_keep_terminal_control_semantics(system, modifier, key, expected):
    event = QKeyEvent(QEvent.KeyPress, key, modifier, "")
    with patch("snakesh.ui.main_window.platform.system", return_value=system):
        assert TerminalView._map_key_event(event) == expected


@pytest.mark.parametrize("selected", [True, False])
def test_physical_control_c_interrupts_even_with_a_selection(terminal, selected):
    if selected:
        terminal.output._selection_anchor = (0, 0)
        terminal.output._selection_cursor = (4, 0)
    sent = []
    terminal.output.data_input.connect(sent.append)
    with patch("snakesh.ui.main_window.copy_text") as copy:
        terminal.output.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_C, Qt.MetaModifier, ""))
    assert sent == ["\x03"]
    copy.assert_not_called()
    assert not terminal.output._has_selection()


@pytest.mark.parametrize("key", [Qt.Key_C, Qt.Key_V, Qt.Key_A, Qt.Key_D, Qt.Key_Backspace, Qt.Key_Left])
def test_other_command_combinations_are_not_sent_as_terminal_control_keys(key):
    with patch("snakesh.ui.main_window.platform.system", return_value="Darwin"):
        assert TerminalView._map_key_event(QKeyEvent(QEvent.KeyPress, key, Qt.ControlModifier, "x")) is None


@pytest.mark.parametrize("approve", [True, False])
def test_command_v_keeps_multiline_paste_review_before_sending_to_remote(terminal, approve):
    worker = Mock()
    terminal._worker = worker
    with (
        patch("snakesh.ui.main_window.paste_text", side_effect=lambda callback: callback("echo one\necho two")),
        patch.object(terminal, "_edit_multiline_paste", return_value="echo reviewed\n" if approve else None) as review,
    ):
        terminal.output.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_V, Qt.ControlModifier, "v"))
    review.assert_called_once_with("echo one\necho two")
    if approve:
        worker.send_text.assert_called_once_with("echo reviewed\r")
    else:
        worker.send_text.assert_not_called()


def test_command_bar_uses_native_copy_cut_and_paste_shortcuts(terminal):
    field = CommandInputLineEdit()
    field.setText("hello world")
    field.setSelection(0, 5)
    pasted = []
    field.paste_requested.connect(pasted.append)
    try:
        with patch("snakesh.ui.main_window.copy_text") as copy:
            field.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_C, Qt.ControlModifier, "c"))
            copy.assert_called_once_with("hello")
            assert field.text() == "hello world"
        with patch("snakesh.ui.main_window.copy_text") as copy:
            field.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_X, Qt.ControlModifier, "x"))
            copy.assert_called_once_with("hello")
            assert field.text() == " world"
        with patch("snakesh.ui.main_window.paste_text", side_effect=lambda callback: callback("héllo\nworld")):
            field.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_V, Qt.ControlModifier, "v"))
        assert pasted == ["héllo\nworld"]
    finally:
        field.deleteLater()
