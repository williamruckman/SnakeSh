from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sys

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QColor, QKeySequence, QShortcut, QTextCharFormat, QTextCursor, QTextDocument
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from snakesh import runtime


_HELP_BROWSER_STYLE = """
QTextBrowser {
    background: #f7f9fc;
    color: #1d2733;
    border: 1px solid #385074;
    border-radius: 10px;
    selection-background-color: #cfe2ff;
    selection-color: #10223a;
}
"""

_HELP_DOCUMENT_STYLE = """
body {
    background: #f7f9fc;
    color: #1d2733;
}
h1, h2, h3 {
    color: #183b63;
}
a {
    color: #0b63b4;
}
code {
    background: #eef2f7;
    color: #1d2733;
}
"""

_HELP_MANIFEST_CACHE: dict[str, tuple[tuple[str, str, Path], ...]] = {}
_HELP_PAGE_TEXT_CACHE: dict[str, str] = {}


@dataclass(frozen=True)
class _HelpSearchMatch:
    page_id: str
    start: int
    end: int


def _load_help_manifest_entries() -> tuple[tuple[str, str, Path], ...]:
    manifest_path = runtime.asset_path("help/manifest.json")
    cache_key = str(manifest_path.resolve(strict=False))
    cached = _HELP_MANIFEST_CACHE.get(cache_key)
    if cached is not None:
        return cached

    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries: list[tuple[str, str, Path]] = []
    for page in payload.get("pages", []):
        if not isinstance(page, dict):
            continue
        page_id = str(page.get("id", "")).strip()
        title = str(page.get("title", "")).strip()
        file_name = str(page.get("file", "")).strip()
        if not page_id or not title or not file_name:
            continue
        entries.append((page_id, title, runtime.asset_path(f"help/{file_name}")))

    cached_entries = tuple(entries)
    _HELP_MANIFEST_CACHE[cache_key] = cached_entries
    return cached_entries


def _help_page_text(page_path: Path) -> str:
    cache_key = str(page_path.resolve(strict=False))
    cached = _HELP_PAGE_TEXT_CACHE.get(cache_key)
    if cached is not None:
        return cached
    try:
        html = page_path.read_text(encoding="utf-8")
    except Exception:
        _HELP_PAGE_TEXT_CACHE[cache_key] = ""
        return ""
    document = QTextDocument()
    document.setHtml(html)
    text = document.toPlainText()
    _HELP_PAGE_TEXT_CACHE[cache_key] = text
    return text


def _help_find_prompt_text() -> str:
    if sys.platform == "darwin":
        return "Press Command-F to search help"
    return "Press CNTL-F to search help"


class _HelpFindLineEdit(QLineEdit):
    next_requested = Signal()
    previous_requested = Signal()
    close_requested = Signal()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        if key in (Qt.Key_Return, Qt.Key_Enter):
            if event.modifiers() & Qt.ShiftModifier:
                self.previous_requested.emit()
            else:
                self.next_requested.emit()
            event.accept()
            return
        if key == Qt.Key_Escape:
            self.close_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class HelpDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("SnakeSh Help")
        self.resize(1080, 760)
        self._manifest_entries: tuple[tuple[str, str, Path], ...] = ()
        self._pages_by_id: dict[str, Path] = {}
        self._current_page_id = ""
        self._search_matches: list[_HelpSearchMatch] = []
        self._current_match_index = -1
        self._suppress_selection_change = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        self._intro_label = QLabel(_help_find_prompt_text())
        self._intro_label.setWordWrap(True)
        layout.addWidget(self._intro_label)

        self._find_bar = QWidget(self)
        find_layout = QHBoxLayout(self._find_bar)
        find_layout.setContentsMargins(0, 0, 0, 0)
        find_layout.setSpacing(6)
        find_layout.addWidget(QLabel("Find"))
        self._find_input = _HelpFindLineEdit(self._find_bar)
        self._find_input.setPlaceholderText("Search all help pages...")
        find_layout.addWidget(self._find_input, 1)
        self._find_previous_btn = QPushButton("Previous", self._find_bar)
        self._find_next_btn = QPushButton("Next", self._find_bar)
        self._find_match_label = QLabel("0/0", self._find_bar)
        self._find_match_label.setMinimumWidth(52)
        self._find_close_btn = QPushButton("Close", self._find_bar)
        find_layout.addWidget(self._find_previous_btn)
        find_layout.addWidget(self._find_next_btn)
        find_layout.addWidget(self._find_match_label)
        find_layout.addWidget(self._find_close_btn)
        self._find_bar.hide()
        layout.addWidget(self._find_bar)

        splitter = QSplitter(Qt.Horizontal, self)
        layout.addWidget(splitter, 1)

        sidebar = QWidget(self)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.setSpacing(6)
        sidebar_layout.addWidget(QLabel("Contents"))
        self.index_list = QListWidget(sidebar)
        sidebar_layout.addWidget(self.index_list, 1)
        splitter.addWidget(sidebar)

        self.browser = QTextBrowser(self)
        self.browser.setOpenExternalLinks(True)
        self.browser.setStyleSheet(_HELP_BROWSER_STYLE)
        self.browser.document().setDefaultStyleSheet(_HELP_DOCUMENT_STYLE)
        splitter.addWidget(self.browser)
        splitter.setSizes([260, 780])

        self.index_list.currentItemChanged.connect(self._on_selection_changed)
        self._find_input.textChanged.connect(self._on_find_text_changed)
        self._find_input.next_requested.connect(self._find_next)
        self._find_input.previous_requested.connect(self._find_previous)
        self._find_input.close_requested.connect(self._close_find_bar)
        self._find_previous_btn.clicked.connect(self._find_previous)
        self._find_next_btn.clicked.connect(self._find_next)
        self._find_close_btn.clicked.connect(self._close_find_bar)
        self._find_shortcut = QShortcut(QKeySequence.Find, self)
        self._find_shortcut.activated.connect(self._show_find_bar)
        self._update_find_controls()
        self._load_manifest()

    def _load_manifest(self) -> None:
        self.index_list.clear()
        self.index_list.setEnabled(True)
        self._pages_by_id.clear()
        self._manifest_entries = ()
        try:
            entries = _load_help_manifest_entries()
        except Exception:
            self.index_list.setEnabled(False)
            self.browser.setHtml("<h2>Help content unavailable</h2><p>The bundled help index could not be loaded.</p>")
            self._clear_find_results()
            return
        self._manifest_entries = entries
        for page_id, title, page_path in entries:
            item = QListWidgetItem(title)
            item.setData(Qt.UserRole, page_id)
            self.index_list.addItem(item)
            self._pages_by_id[page_id] = page_path

        if self.index_list.count() > 0:
            self.index_list.setCurrentRow(0)
        else:
            self.browser.setHtml("<h2>Help content unavailable</h2><p>No help pages were found in the bundled index.</p>")
        self._run_global_find(self._find_input.text())

    def _on_selection_changed(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        if current is None or self._suppress_selection_change:
            return
        page_id = str(current.data(Qt.UserRole) or "").strip()
        self._load_page(page_id, scroll_to_active_match=False)

    def _load_page(self, page_id: str, *, scroll_to_active_match: bool) -> None:
        path = self._pages_by_id.get(page_id)
        if path is None or not path.exists():
            self._current_page_id = page_id
            self.browser.setHtml("<h2>Help page missing</h2><p>The selected help page could not be loaded.</p>")
            self.browser.setExtraSelections([])
            return
        self._current_page_id = page_id
        self._select_index_page(page_id)
        self.browser.setSource(QUrl.fromLocalFile(str(path.resolve())))
        self._refresh_search_highlights(scroll_to_active=scroll_to_active_match)

    def _select_index_page(self, page_id: str) -> None:
        for index in range(self.index_list.count()):
            item = self.index_list.item(index)
            if item is not None and str(item.data(Qt.UserRole) or "").strip() == page_id:
                if self.index_list.currentRow() == index:
                    return
                self._suppress_selection_change = True
                try:
                    self.index_list.setCurrentRow(index)
                finally:
                    self._suppress_selection_change = False
                return

    def _show_find_bar(self) -> None:
        self._find_bar.show()
        self._find_input.setFocus(Qt.ShortcutFocusReason)
        self._find_input.selectAll()

    def _close_find_bar(self) -> None:
        self._find_input.clear()
        self._find_bar.hide()
        self.browser.setFocus(Qt.ShortcutFocusReason)

    def _on_find_text_changed(self, text: str) -> None:
        self._run_global_find(text)

    def _clear_find_results(self) -> None:
        self._search_matches = []
        self._current_match_index = -1
        self.browser.setExtraSelections([])
        self._update_find_controls()

    def _run_global_find(self, raw_pattern: str) -> None:
        pattern = raw_pattern.strip()
        if not pattern:
            self._clear_find_results()
            return
        lowered = pattern.lower()
        matches: list[_HelpSearchMatch] = []
        for page_id, _title, page_path in self._manifest_entries:
            page_text = _help_page_text(page_path)
            search_text = page_text.lower()
            start = 0
            while True:
                index = search_text.find(lowered, start)
                if index < 0:
                    break
                end = index + len(pattern)
                matches.append(_HelpSearchMatch(page_id=page_id, start=index, end=end))
                start = max(index + 1, end)
        self._search_matches = matches
        self._current_match_index = 0 if matches else -1
        self._update_find_controls()
        if matches:
            self._go_to_match(self._current_match_index)
        else:
            self._refresh_search_highlights(scroll_to_active=False)

    def _find_next(self) -> None:
        if not self._find_bar.isVisible():
            self._show_find_bar()
        if not self._search_matches:
            return
        next_index = (self._current_match_index + 1) % len(self._search_matches)
        self._go_to_match(next_index)

    def _find_previous(self) -> None:
        if not self._find_bar.isVisible():
            self._show_find_bar()
        if not self._search_matches:
            return
        previous_index = (self._current_match_index - 1) % len(self._search_matches)
        self._go_to_match(previous_index)

    def _go_to_match(self, match_index: int) -> None:
        if not self._search_matches:
            self._current_match_index = -1
            self._update_find_controls()
            return
        self._current_match_index = max(0, min(match_index, len(self._search_matches) - 1))
        match = self._search_matches[self._current_match_index]
        if match.page_id != self._current_page_id:
            self._load_page(match.page_id, scroll_to_active_match=True)
        else:
            self._select_index_page(match.page_id)
            self._refresh_search_highlights(scroll_to_active=True)
        self._update_find_controls()

    def _update_find_controls(self) -> None:
        total = len(self._search_matches)
        current = self._current_match_index + 1 if total and self._current_match_index >= 0 else 0
        self._find_match_label.setText(f"{current}/{total}")
        enabled = total > 0
        self._find_previous_btn.setEnabled(enabled)
        self._find_next_btn.setEnabled(enabled)

    def _refresh_search_highlights(self, *, scroll_to_active: bool) -> None:
        selections: list[QTextEdit.ExtraSelection] = []
        active_cursor: QTextCursor | None = None
        if not self._current_page_id or not self._search_matches:
            self.browser.setExtraSelections([])
            return
        document = self.browser.document()
        doc_limit = max(0, document.characterCount() - 1)
        for match_index, match in enumerate(self._search_matches):
            if match.page_id != self._current_page_id:
                continue
            start = min(max(0, match.start), doc_limit)
            end = min(max(start, match.end), doc_limit)
            cursor = QTextCursor(document)
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.KeepAnchor)
            selection = QTextEdit.ExtraSelection()
            selection.cursor = cursor
            selection.format = self._search_match_format(active=match_index == self._current_match_index)
            selections.append(selection)
            if match_index == self._current_match_index:
                active_cursor = QTextCursor(cursor)
        self.browser.setExtraSelections(selections)
        if scroll_to_active and active_cursor is not None:
            self.browser.setTextCursor(active_cursor)
            self.browser.ensureCursorVisible()

    @staticmethod
    def _search_match_format(*, active: bool) -> QTextCharFormat:
        char_format = QTextCharFormat()
        if active:
            char_format.setBackground(QColor("#f97316"))
            char_format.setForeground(QColor("#111827"))
        else:
            char_format.setBackground(QColor("#fde68a"))
            char_format.setForeground(QColor("#111827"))
        return char_format
