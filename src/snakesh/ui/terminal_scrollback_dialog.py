from __future__ import annotations

import re
import threading

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QKeySequence, QTextCharFormat, QTextCursor, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from snakesh.ui.terminal_scrollback_store import (
    ScrollbackMatch,
    ScrollbackPage,
    ScrollbackProvider,
    ScrollbackSearchResult,
)
from snakesh.services.settings_service import AppSettings
from snakesh.ui.theme import apply_terminal_output_font


class _ScrollbackFindBar(QWidget):
    search_changed = Signal(str, bool, bool)  # pattern, case_sensitive, use_regex
    navigate = Signal(int)  # +1 next, -1 prev

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._last_direction = 1
        self.setObjectName("scrollbackFindBar")
        self.setAutoFillBackground(False)
        self.setStyleSheet(
            "QWidget#scrollbackFindBar { background: transparent; border: none; }"
            "QWidget#scrollbackFindBar QLabel { background: transparent; }"
            "QWidget#scrollbackFindBar QCheckBox { background: transparent; }"
        )

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(300)
        self._debounce.timeout.connect(self._emit_search)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        layout.addWidget(QLabel("Find:"))
        self._input = QLineEdit()
        self._input.setPlaceholderText("Search...")
        self._input.setMinimumWidth(180)
        self._input.installEventFilter(self)
        layout.addWidget(self._input)

        self._prev_btn = QPushButton("^")
        self._prev_btn.setFixedWidth(32)
        self._prev_btn.setToolTip("Previous match")
        layout.addWidget(self._prev_btn)

        self._next_btn = QPushButton("v")
        self._next_btn.setFixedWidth(32)
        self._next_btn.setToolTip("Next match")
        layout.addWidget(self._next_btn)

        self._case_cb = QCheckBox("Case")
        layout.addWidget(self._case_cb)

        self._regex_cb = QCheckBox("Regex")
        layout.addWidget(self._regex_cb)

        self._match_label = QLabel("")
        layout.addWidget(self._match_label)

        layout.addStretch(1)

        close_btn = QPushButton("X")
        close_btn.setFixedWidth(32)
        close_btn.setToolTip("Close find bar")
        layout.addWidget(close_btn)

        self._input.textChanged.connect(lambda _: self._debounce.start())
        self._case_cb.toggled.connect(lambda _: self._debounce.start())
        self._regex_cb.toggled.connect(lambda _: self._debounce.start())
        self._prev_btn.clicked.connect(lambda: self._emit_navigate(-1))
        self._next_btn.clicked.connect(lambda: self._emit_navigate(1))
        close_btn.clicked.connect(self._close)

    def toggle(self) -> None:
        if self.isHidden():
            self.setVisible(True)
        self._input.setFocus()
        self._input.selectAll()

    def set_match_info(self, current: int, total: int) -> None:
        if total == 0:
            self._match_label.setText("No matches")
        else:
            self._match_label.setText(f"{current}/{total}")

    def clear_match_info(self) -> None:
        self._match_label.setText("")

    @Slot()
    def _emit_search(self) -> None:
        self.search_changed.emit(
            self._input.text(),
            self._case_cb.isChecked(),
            self._regex_cb.isChecked(),
        )

    @Slot()
    def _close(self) -> None:
        self.setVisible(False)
        self.search_changed.emit("", False, False)

    def _emit_navigate(self, direction: int) -> None:
        self._last_direction = direction
        self.navigate.emit(direction)

    def eventFilter(self, watched, event) -> bool:
        if watched is self._input and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key_Escape:
                self._close()
                return True
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                direction = -1 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else self._last_direction
                self.navigate.emit(direction)
                return True
        return super().eventFilter(watched, event)


class _SearchResultEmitter(QObject):
    result_ready = Signal(int, object, object)


class TerminalScrollbackDialog(QDialog):
    _PROVIDER_SEARCH_MAX_MATCHES = 2000
    _PROVIDER_PAGE_LINES = 2000

    def __init__(
        self,
        scrollback_text: str = "",
        parent: QWidget | None = None,
        *,
        provider: ScrollbackProvider | None = None,
        settings: AppSettings | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Terminal Scrollback")
        self.resize(980, 640)
        self.setModal(False)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setWindowFlag(Qt.WindowType.Tool, False)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, False)
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.setWindowFlag(Qt.WindowType.WindowSystemMenuHint, True)
        self.setWindowFlag(Qt.WindowType.WindowMinimizeButtonHint, True)
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, True)

        self._provider = provider
        self._global_search_matches: list[ScrollbackMatch] = []
        self._visible_search_matches: list[tuple[int, int]] = []
        self._search_matches: list[tuple[int, int]] = []
        self._search_current = 0
        self._last_search: tuple[str, bool, bool] = ("", False, False)
        self._current_text = ""
        self._current_page = ScrollbackPage(0, 0, [])
        self._provider_search_in_flight = False
        self._provider_search_pending_request: tuple[str, bool, bool, bool] | None = None
        self._provider_search_scroll_to_current: dict[int, bool] = {}
        self._provider_search_serial = 0
        self._search_result_emitter = _SearchResultEmitter(self)
        self._search_result_emitter.result_ready.connect(self._on_provider_search_ready, Qt.QueuedConnection)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(6)

        self._viewer = QPlainTextEdit(self)
        self._viewer.setReadOnly(True)
        if self._provider is not None:
            self._viewer.document().setMaximumBlockCount(self._provider.max_lines())
            self._viewer.verticalScrollBar().valueChanged.connect(self._update_provider_status)
        root.addWidget(self._viewer, 1)
        if isinstance(settings, AppSettings):
            self.apply_runtime_settings(settings)

        self._find_bar = _ScrollbackFindBar(self)
        self._find_bar.setVisible(False)
        root.addWidget(self._find_bar)

        button_row = QHBoxLayout()
        self._older_page_btn = QPushButton("Older")
        self._older_page_btn.setToolTip("Load older scrollback")
        self._older_page_btn.setVisible(self._provider is not None)
        button_row.addWidget(self._older_page_btn, 0)
        self._newer_page_btn = QPushButton("Newer")
        self._newer_page_btn.setToolTip("Load newer scrollback")
        self._newer_page_btn.setVisible(self._provider is not None)
        button_row.addWidget(self._newer_page_btn, 0)
        self._live_status_label = QLabel("")
        self._live_status_label.setVisible(False)
        button_row.addWidget(self._live_status_label, 0)
        self._resume_refresh_btn = QPushButton("Continue Refresh")
        self._resume_refresh_btn.setToolTip("Return to the live tail and resume automatic refresh")
        self._resume_refresh_btn.setVisible(False)
        button_row.addWidget(self._resume_refresh_btn, 0)
        button_row.addStretch(1)
        self._open_search_btn = QPushButton("Search")
        self._open_search_btn.setToolTip("Show or hide search (Ctrl+F opens)")
        button_row.addWidget(self._open_search_btn)
        self._close_btn = QPushButton("Close")
        button_row.addWidget(self._close_btn)
        root.addLayout(button_row)

        self._open_search_btn.clicked.connect(self._toggle_find_bar)
        self._find_bar.search_changed.connect(self._on_search_changed)
        self._find_bar.navigate.connect(self._on_navigate)
        self._older_page_btn.clicked.connect(self._load_older_provider_page)
        self._newer_page_btn.clicked.connect(self._load_newer_provider_page)
        self._resume_refresh_btn.clicked.connect(self._resume_live_refresh)
        self._close_btn.pressed.connect(self.accept)
        self._close_btn.clicked.connect(self.accept)

        QShortcut(QKeySequence("Ctrl+F"), self).activated.connect(self._find_bar.toggle)
        QShortcut(QKeySequence("Escape"), self).activated.connect(self._on_escape)

        if self._provider is None:
            self.set_scrollback_text(scrollback_text)
        else:
            self._load_provider_page(
                self._provider.snapshot_tail(window_lines=self._PROVIDER_PAGE_LINES),
                preserve_view=False,
                follow_bottom=True,
            )
            self._update_provider_status()

    def apply_runtime_settings(self, settings: AppSettings) -> None:
        apply_terminal_output_font(self._viewer, settings)

    def viewer(self) -> QPlainTextEdit:
        return self._viewer

    def keyPressEvent(self, event) -> None:  # noqa: N802, ANN001
        if event.key() == Qt.Key_F and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self._find_bar.toggle()
            event.accept()
            return
        if event.key() == Qt.Key_Escape:
            self._on_escape()
            event.accept()
            return
        super().keyPressEvent(event)

    def set_scrollback_text(self, text: str) -> None:
        if self._provider is not None:
            return
        if text == self._current_text:
            return
        if self._current_text and text.startswith(self._current_text):
            self._append_scrollback_text(text[len(self._current_text):], preserve_view=True)
            self._current_text = text
            return
        self._set_scrollback_text(text, preserve_view=True)
        self._current_text = text

    def refresh_from_provider(self, *, force: bool = False) -> None:
        if self._provider is None:
            return
        if not force and (
            self._last_search[0]
            or self._provider_search_in_flight
            or not self._is_current_provider_page_live_tail()
            or not self._is_view_near_bottom()
        ):
            self._update_provider_status()
            return
        follow_bottom = force or self._is_view_near_bottom()
        page = self._provider.snapshot_tail(window_lines=self._PROVIDER_PAGE_LINES)
        self._load_provider_page(
            page,
            preserve_view=not follow_bottom,
            follow_bottom=follow_bottom,
        )
        if self._last_search[0]:
            self._start_provider_search(*self._last_search, scroll_to_current=False)
        else:
            self._rebuild_extra_selections()
        self._update_provider_status()

    def _set_scrollback_text(self, text: str, *, preserve_view: bool) -> None:
        cursor = self._viewer.textCursor()
        anchor = cursor.anchor()
        position = cursor.position()
        vbar = self._viewer.verticalScrollBar()
        hbar = self._viewer.horizontalScrollBar()
        vvalue = vbar.value()
        hvalue = hbar.value()

        self._viewer.setPlainText(text)

        doc = self._viewer.document()
        max_pos = max(0, doc.characterCount() - 1)
        restored_cursor = QTextCursor(doc)
        restored_anchor = max(0, min(anchor, max_pos))
        restored_position = max(0, min(position, max_pos))
        restored_cursor.setPosition(restored_anchor)
        restored_cursor.setPosition(restored_position, QTextCursor.MoveMode.KeepAnchor)
        self._viewer.setTextCursor(restored_cursor)

        if preserve_view:
            vbar.setValue(min(vvalue, vbar.maximum()))
            hbar.setValue(min(hvalue, hbar.maximum()))

        if self._provider is None and self._last_search[0]:
            self._run_search(*self._last_search, scroll_to_current=False)
        else:
            self._rebuild_extra_selections()

    def _append_scrollback_text(self, text: str, *, preserve_view: bool) -> None:
        if not text:
            return
        cursor = self._viewer.textCursor()
        anchor = cursor.anchor()
        position = cursor.position()
        vbar = self._viewer.verticalScrollBar()
        hbar = self._viewer.horizontalScrollBar()
        vvalue = vbar.value()
        hvalue = hbar.value()

        append_cursor = QTextCursor(self._viewer.document())
        append_cursor.movePosition(QTextCursor.MoveOperation.End)
        append_cursor.insertText(text)

        doc = self._viewer.document()
        max_pos = max(0, doc.characterCount() - 1)
        restored_cursor = QTextCursor(doc)
        restored_anchor = max(0, min(anchor, max_pos))
        restored_position = max(0, min(position, max_pos))
        restored_cursor.setPosition(restored_anchor)
        restored_cursor.setPosition(restored_position, QTextCursor.MoveMode.KeepAnchor)
        self._viewer.setTextCursor(restored_cursor)

        if preserve_view:
            vbar.setValue(min(vvalue, vbar.maximum()))
            hbar.setValue(min(hvalue, hbar.maximum()))

        if self._provider is None and self._last_search[0]:
            self._run_search(*self._last_search, scroll_to_current=False)
        else:
            self._rebuild_extra_selections()

    def _make_search_sels(self) -> list[QTextEdit.ExtraSelection]:
        selections: list[QTextEdit.ExtraSelection] = []
        doc = self._viewer.document()
        max_pos = max(0, doc.characterCount() - 1)
        match_color = QColor(255, 165, 0, 160)
        current_color = QColor(34, 197, 94, 170)
        for index, (pos, length) in enumerate(self._visible_search_matches):
            start = max(0, min(pos, max_pos))
            end = max(start, min(pos + max(1, length), max_pos))
            selection = QTextEdit.ExtraSelection()
            cursor = QTextCursor(doc)
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            fmt = QTextCharFormat()
            fmt.setBackground(current_color if index == self._current_visible_match_index() else match_color)
            selection.format = fmt
            selection.cursor = cursor
            selections.append(selection)
        return selections

    def _rebuild_extra_selections(self) -> None:
        self._viewer.setExtraSelections(self._make_search_sels())

    @Slot()
    def _toggle_find_bar(self) -> None:
        if not self._find_bar.isHidden():
            self._find_bar._close()
            self._update_provider_status()
            return
        self._find_bar.toggle()
        self._update_provider_status()

    @Slot()
    def _on_escape(self) -> None:
        if self._find_bar.isVisible():
            self._find_bar._close()
            return
        self.reject()

    @Slot(str, bool, bool)
    def _on_search_changed(self, pattern: str, case_sensitive: bool, use_regex: bool) -> None:
        self._last_search = (pattern, case_sensitive, use_regex)
        if not pattern:
            self._global_search_matches = []
            self._visible_search_matches = []
            self._search_matches = []
            self._search_current = 0
            self._provider_search_pending_request = None
            self._find_bar.clear_match_info()
            self._rebuild_extra_selections()
            if self._provider is not None and self._is_view_near_bottom():
                self.refresh_from_provider(force=True)
            else:
                self._update_provider_status()
            return
        self._run_search(pattern, case_sensitive, use_regex, scroll_to_current=True)
        self._update_provider_status()

    def _run_search(
        self,
        pattern: str,
        case_sensitive: bool,
        use_regex: bool,
        *,
        scroll_to_current: bool,
    ) -> None:
        if self._provider is None:
            flags = 0 if case_sensitive else re.IGNORECASE
            try:
                if not use_regex:
                    pattern = re.escape(pattern)
                compiled = re.compile(pattern, flags)
            except re.error:
                self._global_search_matches = []
                self._visible_search_matches = []
                self._search_matches = []
                self._search_current = 0
                self._find_bar.set_match_info(0, 0)
                self._rebuild_extra_selections()
                return

            lines = self._viewer.toPlainText().split("\n")
            matches: list[ScrollbackMatch] = []
            for line_index, line in enumerate(lines):
                for match in compiled.finditer(line):
                    matches.append(
                        ScrollbackMatch(
                            line_index=line_index,
                            column=match.start(),
                            length=max(1, match.end() - match.start()),
                        )
                    )
            self._global_search_matches = matches
            total = len(matches)
            self._search_current = min(self._search_current, max(0, total - 1))
            self._find_bar.set_match_info(self._search_current + 1 if total else 0, total)
            self._rebuild_visible_matches()
            if total > 0 and scroll_to_current:
                self._scroll_to_current_match()
            return

        self._start_provider_search(pattern, case_sensitive, use_regex, scroll_to_current=scroll_to_current)

    def _start_provider_search(
        self,
        pattern: str,
        case_sensitive: bool,
        use_regex: bool,
        *,
        scroll_to_current: bool,
    ) -> None:
        if self._provider is None:
            return
        if self._provider_search_in_flight:
            self._provider_search_pending_request = (pattern, case_sensitive, use_regex, scroll_to_current)
            return
        self._provider_search_in_flight = True
        self._provider_search_serial += 1
        serial = self._provider_search_serial
        self._provider_search_scroll_to_current[serial] = scroll_to_current
        self._find_bar._match_label.setText("Searching...")
        self._update_provider_status()

        def _worker() -> None:
            result: ScrollbackSearchResult | None = None
            error_text: str | None = None
            try:
                result = self._provider.search(
                    pattern,
                    case_sensitive=case_sensitive,
                    use_regex=use_regex,
                    max_matches=self._PROVIDER_SEARCH_MAX_MATCHES,
                )
            except re.error:
                error_text = "regex"
            except Exception:
                error_text = "error"
            self._search_result_emitter.result_ready.emit(serial, result, error_text)

        threading.Thread(target=_worker, name="snakesh-scrollback-search", daemon=True).start()

    @Slot(int, object, object)
    def _on_provider_search_ready(self, serial: int, result: object, error_text: object) -> None:
        self._provider_search_in_flight = False
        scroll_to_current = self._provider_search_scroll_to_current.pop(serial, False)
        if serial != self._provider_search_serial:
            if self._provider_search_pending_request is not None:
                pending = self._provider_search_pending_request
                self._provider_search_pending_request = None
                self._start_provider_search(*pending[:3], scroll_to_current=pending[3])
            return

        if error_text:
            self._global_search_matches = []
            self._visible_search_matches = []
            self._search_matches = []
            self._search_current = 0
            self._find_bar.set_match_info(0, 0)
            self._rebuild_extra_selections()
        else:
            assert isinstance(result, ScrollbackSearchResult)
            self._global_search_matches = result.matches
            total = len(self._global_search_matches)
            self._search_current = min(self._search_current, max(0, total - 1))
            self._find_bar.set_match_info(self._search_current + 1 if total else 0, total)
            self._rebuild_visible_matches()
            if total > 0 and scroll_to_current:
                self._scroll_to_current_match()

        if self._provider_search_pending_request is not None:
            pending = self._provider_search_pending_request
            self._provider_search_pending_request = None
            self._start_provider_search(*pending[:3], scroll_to_current=pending[3])
            return
        self._update_provider_status()

    @Slot(int)
    def _on_navigate(self, direction: int) -> None:
        total = len(self._global_search_matches)
        if total == 0:
            return
        self._search_current = (self._search_current + direction) % total
        self._find_bar.set_match_info(self._search_current + 1, total)
        self._rebuild_visible_matches()
        self._scroll_to_current_match()

    def _load_provider_page(self, page: ScrollbackPage, *, preserve_view: bool, follow_bottom: bool = False) -> None:
        self._current_page = page
        text = "(no scrollback data yet)" if not page.lines else "\n".join(page.lines)
        self._current_text = text
        self._set_scrollback_text(text, preserve_view=preserve_view)
        if follow_bottom:
            self._scroll_to_bottom()
        self._update_provider_status()

    @Slot()
    def _load_older_provider_page(self) -> None:
        if self._provider is None or self._current_page.start_line_index <= 0:
            return
        page = self._provider.page_ending_at(
            self._current_page.start_line_index,
            window_lines=self._PROVIDER_PAGE_LINES,
        )
        self._load_provider_page(page, preserve_view=False, follow_bottom=True)
        if self._last_search[0]:
            self._rebuild_visible_matches()

    @Slot()
    def _load_newer_provider_page(self) -> None:
        if self._provider is None:
            return
        if self._current_page.end_line_index >= self._current_page.total_line_count:
            self.refresh_from_provider(force=True)
            return
        start = self._current_page.end_line_index
        page = self._provider.read_page(start, start + self._PROVIDER_PAGE_LINES)
        self._load_provider_page(page, preserve_view=False, follow_bottom=False)
        if self._last_search[0]:
            self._rebuild_visible_matches()

    def _rebuild_visible_matches(self) -> None:
        if self._provider is None:
            self._visible_search_matches = [
                (match.column + self._line_position_offset(match.line_index), match.length)
                for match in self._global_search_matches
            ]
            self._search_matches = list(self._visible_search_matches)
            self._rebuild_extra_selections()
            return

        current = self._current_match()
        self._visible_search_matches = []
        if current is not None and self._is_match_visible(current):
            line_offset = current.line_index - self._current_page.start_line_index
            self._visible_search_matches.append(
                (current.column + self._line_position_offset(line_offset), current.length)
            )
        self._search_matches = list(self._visible_search_matches)
        self._rebuild_extra_selections()

    def _line_position_offset(self, line_index: int) -> int:
        lines = self._viewer.toPlainText().split("\n")
        return sum(len(line) + 1 for line in lines[:line_index])

    def _current_visible_match_index(self) -> int:
        if not self._visible_search_matches:
            return -1
        if self._provider is None:
            return self._search_current
        return 0

    def _current_match(self) -> ScrollbackMatch | None:
        if 0 <= self._search_current < len(self._global_search_matches):
            return self._global_search_matches[self._search_current]
        return None

    def _is_match_visible(self, match: ScrollbackMatch) -> bool:
        return self._current_page.start_line_index <= match.line_index < self._current_page.end_line_index

    def _scroll_to_current_match(self) -> None:
        current = self._current_match()
        if current is None:
            return
        if self._provider is not None and not self._is_match_visible(current):
            page = self._provider.page_for_line(current.line_index, window_lines=self._PROVIDER_PAGE_LINES)
            self._load_provider_page(page, preserve_view=False, follow_bottom=False)
            self._rebuild_visible_matches()
        current_visible_index = self._current_visible_match_index()
        if current_visible_index < 0 or current_visible_index >= len(self._visible_search_matches):
            return
        pos, _ = self._visible_search_matches[current_visible_index]
        cursor = QTextCursor(self._viewer.document())
        max_pos = max(0, self._viewer.document().characterCount() - 1)
        cursor.setPosition(max(0, min(pos, max_pos)))
        self._viewer.setTextCursor(cursor)
        self._viewer.ensureCursorVisible()

    def _is_view_near_bottom(self) -> bool:
        bar = self._viewer.verticalScrollBar()
        return bar.value() >= max(0, bar.maximum() - 2)

    def _scroll_to_bottom(self) -> None:
        bar = self._viewer.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _is_current_provider_page_live_tail(self) -> bool:
        if self._provider is None:
            return False
        return self._current_page.end_line_index >= self._current_page.total_line_count

    def _clear_provider_search_state(self) -> None:
        self._provider_search_serial += 1
        self._provider_search_in_flight = False
        self._provider_search_pending_request = None
        self._provider_search_scroll_to_current.clear()
        self._global_search_matches = []
        self._visible_search_matches = []
        self._search_matches = []
        self._search_current = 0
        self._last_search = ("", False, False)
        self._find_bar.clear_match_info()
        self._rebuild_extra_selections()

    @Slot()
    def _resume_live_refresh(self) -> None:
        if self._provider is None:
            return
        if self._last_search[0]:
            self._find_bar._debounce.stop()
            self._find_bar._input.blockSignals(True)
            self._find_bar._input.clear()
            self._find_bar._input.blockSignals(False)
            self._find_bar.setVisible(False)
        self._clear_provider_search_state()
        self.refresh_from_provider(force=True)
        self._scroll_to_bottom()
        self._update_provider_status()

    def _provider_status_state(self) -> tuple[str, str, str, bool]:
        if self._provider is None:
            return ("", "", "", False)
        if self._provider_search_in_flight:
            return (
                "Paused: Searching",
                "#f59e0b",
                "Live refresh is paused while search results are being collected.",
                True,
            )
        if self._last_search[0]:
            return (
                "Paused: Search Active",
                "#f59e0b",
                "Live refresh is paused while search is active. Continue Refresh returns to the live tail.",
                True,
            )
        if not self._is_current_provider_page_live_tail() or not self._is_view_near_bottom():
            return (
                "Paused: Viewing History",
                "#f59e0b",
                "Live refresh is paused while you are scrolled away from the bottom.",
                True,
            )
        return (
            "Running: Live Refresh",
            "#16a34a",
            "Scrollback is following the live tail.",
            False,
        )

    @Slot()
    def _update_provider_status(self) -> None:
        if self._provider is None:
            self._live_status_label.setVisible(False)
            self._resume_refresh_btn.setVisible(False)
            self._older_page_btn.setVisible(False)
            self._newer_page_btn.setVisible(False)
            return
        text, color, tooltip, paused = self._provider_status_state()
        red = QColor(color).red()
        green = QColor(color).green()
        blue = QColor(color).blue()
        self._live_status_label.setVisible(True)
        self._resume_refresh_btn.setVisible(True)
        self._older_page_btn.setVisible(True)
        self._newer_page_btn.setVisible(True)
        self._live_status_label.setText(text)
        self._live_status_label.setToolTip(tooltip)
        self._resume_refresh_btn.setEnabled(paused)
        self._resume_refresh_btn.setToolTip(
            "Return to the live tail and resume automatic refresh"
            if paused
            else "Scrollback is already following the live tail"
        )
        self._live_status_label.setStyleSheet(
            "QLabel {"
            f"color: {color};"
            f"background-color: rgba({red}, {green}, {blue}, 32);"
            f"border: 1px solid {color};"
            "border-radius: 10px;"
            "padding: 3px 10px;"
            "font-weight: 700;"
            "}"
        )
        self._older_page_btn.setEnabled(self._current_page.start_line_index > 0)
        self._newer_page_btn.setEnabled(
            self._current_page.end_line_index < self._current_page.total_line_count
        )
