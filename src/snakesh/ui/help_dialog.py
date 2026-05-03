from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSplitter,
    QTextBrowser,
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


class HelpDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("SnakeSh Help")
        self.resize(1080, 760)
        self._pages_by_id: dict[str, Path] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        intro = QLabel("Browse the in-depth SnakeSh guide while keeping the main app open.")
        intro.setWordWrap(True)
        layout.addWidget(intro)

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
        self._load_manifest()

    def _load_manifest(self) -> None:
        self.index_list.clear()
        self.index_list.setEnabled(True)
        self._pages_by_id.clear()
        try:
            entries = _load_help_manifest_entries()
        except Exception:
            self.index_list.setEnabled(False)
            self.browser.setHtml("<h2>Help content unavailable</h2><p>The bundled help index could not be loaded.</p>")
            return
        for page_id, title, page_path in entries:
            item = QListWidgetItem(title)
            item.setData(Qt.UserRole, page_id)
            self.index_list.addItem(item)
            self._pages_by_id[page_id] = page_path

        if self.index_list.count() > 0:
            self.index_list.setCurrentRow(0)
        else:
            self.browser.setHtml("<h2>Help content unavailable</h2><p>No help pages were found in the bundled index.</p>")

    def _on_selection_changed(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        if current is None:
            return
        page_id = str(current.data(Qt.UserRole) or "").strip()
        path = self._pages_by_id.get(page_id)
        if path is None or not path.exists():
            self.browser.setHtml("<h2>Help page missing</h2><p>The selected help page could not be loaded.</p>")
            return
        self.browser.setSource(QUrl.fromLocalFile(str(path.resolve())))
