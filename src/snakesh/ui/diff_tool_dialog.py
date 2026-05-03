from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from PySide6.QtCore import (
    QObject,
    QPoint,
    QRect,
    QSettings,
    QSize,
    Qt,
    QThread,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPalette,
    QShortcut,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollBar,
    QSizePolicy,
    QStyle,
    QStyleOptionSlider,
    QVBoxLayout,
    QWidget,
)
from snakesh.services.settings_service import AppSettings, SettingsService
from snakesh.ui.theme import build_terminal_output_font

# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


@dataclass
class _DiffBlock:
    tag: str        # 'equal' | 'replace' | 'delete' | 'insert'
    left_start: int
    left_end: int
    right_start: int
    right_end: int


# Thresholds for file-open warnings
_WARN_SIZE_BYTES = 5 * 1024 * 1024   # 5 MB  — ask before opening
_MAX_SIZE_BYTES  = 100 * 1024 * 1024  # 100 MB — refuse (would freeze the editor)

# Use autojunk heuristic above this line count (faster, trivially less accurate)
_AUTOJUNK_LINE_THRESHOLD = 5_000


def _is_binary(data: bytes) -> bool:
    """Return True if *data* looks like binary content (not diffable text)."""
    # Null bytes are the most reliable binary indicator
    if b"\x00" in data[:8192]:
        return True
    # High ratio of non-printable control characters (excluding tab/LF/CR/FF/VT)
    sample = data[:8192]
    non_text = sum(1 for b in sample if b < 32 and b not in (9, 10, 13, 12, 11))
    return len(sample) > 0 and non_text / len(sample) > 0.15


def _compute_diffs(left_text: str, right_text: str) -> list[_DiffBlock]:
    # Fast path: identical content
    if left_text == right_text:
        n = len(left_text.splitlines())
        return [_DiffBlock("equal", 0, n, 0, n)] if n else []

    left_lines = left_text.splitlines()
    right_lines = right_text.splitlines()

    # Use the autojunk heuristic for large files — O(n log n) vs O(n²) worst-case
    autojunk = (
        len(left_lines) > _AUTOJUNK_LINE_THRESHOLD
        or len(right_lines) > _AUTOJUNK_LINE_THRESHOLD
    )
    matcher = difflib.SequenceMatcher(None, left_lines, right_lines, autojunk=autojunk)
    return [
        _DiffBlock(tag=tag, left_start=i1, left_end=i2, right_start=j1, right_end=j2)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes()
    ]


def _clamp_ratio(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _scroll_total_steps(bar: QScrollBar) -> float:
    return max(1.0, float(bar.maximum() + bar.pageStep()))


def _scroll_center_ratio(bar: QScrollBar) -> float:
    return _clamp_ratio((bar.value() + (bar.pageStep() / 2.0)) / _scroll_total_steps(bar))


def _scroll_edge_ratios(bar: QScrollBar) -> tuple[float, float]:
    total = _scroll_total_steps(bar)
    return (
        _clamp_ratio(bar.value() / total),
        _clamp_ratio((bar.value() + bar.pageStep()) / total),
    )


def _build_terminal_font(settings: AppSettings) -> QFont:
    return build_terminal_output_font(settings)


def _set_scroll_center_ratio(bar: QScrollBar, ratio: float) -> None:
    total = _scroll_total_steps(bar)
    target = _clamp_ratio(ratio) * total
    value = round(target - (bar.pageStep() / 2.0))
    bar.setValue(max(0, min(bar.maximum(), value)))


def _line_ratio(editor: QPlainTextEdit, line: int, at_bottom: bool) -> float:
    block_count = max(1, editor.document().blockCount())
    if line <= 0 and not at_bottom:
        return 0.0
    if line >= block_count:
        return 1.0
    edge = max(0, line) + (1.0 if at_bottom else 0.0)
    return _clamp_ratio(edge / block_count)


class _DiffScrollBar(QScrollBar):
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            option = QStyleOptionSlider()
            self.initStyleOption(option)
            hit = self.style().hitTestComplexControl(
                QStyle.ComplexControl.CC_ScrollBar,
                option,
                event.position().toPoint(),
                self,
            )
            if hit in (
                QStyle.SubControl.SC_ScrollBarAddPage,
                QStyle.SubControl.SC_ScrollBarSubPage,
                QStyle.SubControl.SC_ScrollBarGroove,
            ):
                if self.orientation() == Qt.Orientation.Vertical:
                    ratio = event.position().y() / max(1, self.height() - 1)
                else:
                    ratio = event.position().x() / max(1, self.width() - 1)
                _set_scroll_center_ratio(self, ratio)
                event.accept()
                return
        super().mousePressEvent(event)


class _DiffWorker(QObject):
    """Runs _compute_diffs on a background thread and emits the result."""

    finished = Signal(object, int)  # (list[_DiffBlock], generation) — object avoids Qt metatype issues

    def __init__(self, left_text: str, right_text: str, generation: int) -> None:
        super().__init__()
        self._left = left_text
        self._right = right_text
        self._generation = generation

    @Slot()
    def run(self) -> None:
        result = _compute_diffs(self._left, self._right)
        self.finished.emit(result, self._generation)


# ---------------------------------------------------------------------------
# Line-number gutter
# ---------------------------------------------------------------------------


class _LineNumberArea(QWidget):
    def __init__(self, editor: _DiffEditor) -> None:
        super().__init__(editor)
        self._editor = editor

    def sizeHint(self) -> QSize:
        return QSize(self._editor.line_number_area_width(), 0)

    def paintEvent(self, event) -> None:
        self._editor.paint_line_numbers(QPainter(self))


class _DiffEditor(QPlainTextEdit):
    scroll_changed = Signal()

    def __init__(self, parent: QWidget | None = None, *, editor_font: QFont | None = None) -> None:
        super().__init__(parent)
        self.setVerticalScrollBar(_DiffScrollBar(Qt.Orientation.Vertical, self))
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.setMinimumWidth(0)
        self._gutter = _LineNumberArea(self)
        self._apply_editor_font(editor_font)
        self.blockCountChanged.connect(self._update_gutter_width)
        self.updateRequest.connect(self._update_gutter_on_scroll)
        self._update_gutter_width(0)

    def _apply_editor_font(self, editor_font: QFont | None) -> None:
        if editor_font is not None:
            self.setFont(QFont(editor_font))
            return
        families = QFontDatabase.families()
        for candidate in (
            "Courier New",
            "Consolas",
            "Cascadia Mono",
            "Cascadia Code",
            "Lucida Console",
            "Courier",
            "Liberation Mono",
            "DejaVu Sans Mono",
        ):
            if candidate in families:
                f = self.font()
                f.setFamily(candidate)
                f.setPointSize(10)
                f.setKerning(False)
                self.setFont(f)
                return

    def line_number_area_width(self) -> int:
        digits = max(3, len(str(max(1, self.blockCount()))))
        return 8 + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_gutter_width(self, _count: int = 0) -> None:
        self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)

    def _update_gutter_on_scroll(self, rect: QRect, dy: int) -> None:
        if dy:
            self._gutter.scroll(0, dy)
        else:
            self._gutter.update(0, rect.y(), self._gutter.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_gutter_width()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        cr = self.contentsRect()
        self._gutter.setGeometry(QRect(cr.left(), cr.top(), self.line_number_area_width(), cr.height()))

    def scrollContentsBy(self, dx: int, dy: int) -> None:
        super().scrollContentsBy(dx, dy)
        self.scroll_changed.emit()

    def paint_line_numbers(self, painter: QPainter) -> None:
        palette = self.palette()
        painter.fillRect(painter.device().rect(), palette.color(QPalette.ColorRole.AlternateBase))
        block = self.firstVisibleBlock()
        block_num = block.blockNumber()
        offset = self.contentOffset()
        top = round(self.blockBoundingGeometry(block).translated(offset).top())
        bottom = top + round(self.blockBoundingRect(block).height())
        painter.setPen(palette.color(QPalette.ColorRole.PlaceholderText))
        fh = self.fontMetrics().height()
        width = self.line_number_area_width() - 4
        while block.isValid() and top <= painter.device().rect().bottom():
            if block.isVisible() and bottom >= painter.device().rect().top():
                painter.drawText(0, top, width, fh, Qt.AlignmentFlag.AlignRight, str(block_num + 1))
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            block_num += 1
        painter.end()

    def has_column_selection(self) -> bool:
        return False

    def clear_column_selection(self) -> None:
        return


# ---------------------------------------------------------------------------
# Scrollbar overview map
# ---------------------------------------------------------------------------


class _ScrollMapWidget(QWidget):
    _WIDTH = 12

    def __init__(self, editor: _DiffEditor, side: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._editor = editor
        self._side = side  # 'left' or 'right'
        self._blocks: list[_DiffBlock] = []
        self.setFixedWidth(self._WIDTH)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_diff_blocks(self, blocks: list[_DiffBlock]) -> None:
        self._blocks = blocks
        self.update()

    def _line_y_in_map(self, line: int, at_bottom: bool, map_h: int) -> int | None:
        if map_h <= 0:
            return None
        return max(0, min(map_h - 1, round(_line_ratio(self._editor, line, at_bottom) * (map_h - 1))))

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        palette = self.palette()
        painter.fillRect(self.rect(), palette.color(QPalette.ColorRole.Base))

        h = self.height()
        w = self.width()

        for blk in self._blocks:
            if blk.tag == "equal":
                continue
            if self._side == "left":
                s, e = blk.left_start, blk.left_end
                color = QColor(200, 50, 50, 200)
            else:
                s, e = blk.right_start, blk.right_end
                color = QColor(50, 180, 50, 200)

            if e <= s:
                y = self._line_y_in_map(s, False, h)
                if y is not None:
                    painter.fillRect(0, y, w, 2, color)
            else:
                y_top = self._line_y_in_map(s, False, h)
                y_bot = self._line_y_in_map(max(s, e - 1), True, h)
                if y_top is None and y_bot is None:
                    continue
                y_top = y_top if y_top is not None else y_bot
                y_bot = y_bot if y_bot is not None else y_top
                painter.fillRect(0, y_top, w, max(2, y_bot - y_top), color)

        # Viewport indicator
        bar = self._editor.verticalScrollBar()
        rt, rb = _scroll_edge_ratios(bar)
        vt = int(rt * h)
        vb = max(vt + 3, int(rb * h))
        painter.fillRect(0, vt, w, vb - vt, QColor(128, 128, 128, 80))
        painter.end()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            ratio = event.position().y() / max(1, self.height() - 1)
            _set_scroll_center_ratio(self._editor.verticalScrollBar(), ratio)
            event.accept()
            return
        super().mousePressEvent(event)


# ---------------------------------------------------------------------------
# Center gutter (curves + per-block buttons)
# ---------------------------------------------------------------------------


class _CenterGutter(QWidget):
    apply_left_to_right = Signal(int)
    apply_right_to_left = Signal(int)

    def __init__(self, left: _DiffEditor, right: _DiffEditor, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._left = left
        self._right = right
        self._blocks: list[_DiffBlock] = []
        # list of (ltr_btn, rtl_btn) per block index
        self._btn_pairs: list[tuple[QPushButton, QPushButton]] = []
        self.setFixedWidth(56)

    def set_diff_blocks(self, blocks: list[_DiffBlock]) -> None:
        self._blocks = blocks
        self._rebuild_buttons()
        self.refresh()

    def _rebuild_buttons(self) -> None:
        for ltr, rtl in self._btn_pairs:
            if ltr is not None:
                ltr.deleteLater()
            if rtl is not None:
                rtl.deleteLater()
        self._btn_pairs.clear()

        _btn_style = (
            "QPushButton { font-size: 9px; padding: 0px; }"
        )

        for idx, blk in enumerate(self._blocks):
            if blk.tag == "equal":
                self._btn_pairs.append((None, None))  # type: ignore[arg-type]
                continue

            ltr = QPushButton(">", self)
            ltr.setFixedSize(24, 18)
            ltr.setToolTip("Apply left → right")
            ltr.setStyleSheet(_btn_style)
            rtl = QPushButton("<", self)
            rtl.setFixedSize(24, 18)
            rtl.setToolTip("Apply right ← left")
            rtl.setStyleSheet(_btn_style)

            # capture idx
            def _make_ltr(i: int):
                def _slot() -> None:
                    self.apply_left_to_right.emit(i)
                return _slot

            def _make_rtl(i: int):
                def _slot() -> None:
                    self.apply_right_to_left.emit(i)
                return _slot

            ltr.clicked.connect(_make_ltr(idx))
            rtl.clicked.connect(_make_rtl(idx))
            self._btn_pairs.append((ltr, rtl))

    def refresh(self) -> None:
        self.update()
        self._reposition_buttons()

    def _line_y(self, editor: _DiffEditor, line: int, at_bottom: bool) -> int | None:
        doc = editor.document()
        if line < 0 or line >= doc.blockCount():
            return None
        block = doc.findBlockByNumber(line)
        if not block.isValid():
            return None
        geom = editor.blockBoundingGeometry(block).translated(editor.contentOffset())
        y = geom.bottom() if at_bottom else geom.top()
        # viewport() is not in this widget's parent hierarchy, so use global mapping
        global_pt = editor.viewport().mapToGlobal(QPoint(0, int(y)))
        return self.mapFromGlobal(global_pt).y()

    def _block_y_range(self, blk: _DiffBlock) -> tuple[int, int] | None:
        """Returns (top_y, bottom_y) in gutter coords for a visible block, or None."""
        ys = []
        if blk.tag != "insert":
            lt = self._line_y(self._left, blk.left_start, False)
            lb = self._line_y(self._left, max(blk.left_start, blk.left_end - 1), True)
            if lt is not None: ys.append(lt)
            if lb is not None: ys.append(lb)
        if blk.tag != "delete":
            rt = self._line_y(self._right, blk.right_start, False)
            rb = self._line_y(self._right, max(blk.right_start, blk.right_end - 1), True)
            if rt is not None: ys.append(rt)
            if rb is not None: ys.append(rb)
        if not ys:
            return None
        return min(ys), max(ys)

    def _reposition_buttons(self) -> None:
        w = self.width()
        btn_w = 24
        gap = max(2, (w - 2 * btn_w) // 3)
        rtl_x = gap
        ltr_x = w - gap - btn_w
        for idx, (ltr, rtl) in enumerate(self._btn_pairs):
            if ltr is None:
                continue
            blk = self._blocks[idx]
            yr = self._block_y_range(blk)
            if yr is None:
                ltr.hide()
                rtl.hide()
                continue
            mid_y = (yr[0] + yr[1]) // 2
            cy = mid_y - 9  # center the 18px tall button
            rtl.move(rtl_x, cy)
            ltr.move(ltr_x, cy)
            rtl.show()
            ltr.show()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = float(self.width())

        for blk in self._blocks:
            if blk.tag == "equal":
                continue

            # Compute endpoint y coords in gutter space
            if blk.tag == "insert":
                # No left side; draw a horizontal insert marker on right
                rt = self._line_y(self._right, blk.right_start, False)
                rb = self._line_y(self._right, max(blk.right_start, blk.right_end - 1), True)
                if rt is None and rb is None:
                    continue
                mid_r = (rt or rb or 0 + rb or rt or 0) // 2  # type: ignore[operator]
                # just draw a small arrow on right side
                color = QColor(50, 180, 50, 60)
                path = QPainterPath()
                path.moveTo(w / 2, mid_r - 1 if rt is None else rt)
                path.lineTo(w, rb if rb is not None else mid_r)
                path.lineTo(w, rt if rt is not None else mid_r)
                path.closeSubpath()
                painter.fillPath(path, color)
                continue

            if blk.tag == "delete":
                lt = self._line_y(self._left, blk.left_start, False)
                lb = self._line_y(self._left, max(blk.left_start, blk.left_end - 1), True)
                if lt is None and lb is None:
                    continue
                color = QColor(200, 50, 50, 60)
                path = QPainterPath()
                path.moveTo(0, lt if lt is not None else lb)
                path.lineTo(w / 2, lb if lb is not None else lt)
                path.lineTo(0, lb if lb is not None else lt)
                path.closeSubpath()
                painter.fillPath(path, color)
                continue

            # replace: draw trapezoid connecting left and right
            lt = self._line_y(self._left, blk.left_start, False)
            lb = self._line_y(self._left, max(blk.left_start, blk.left_end - 1), True)
            rt = self._line_y(self._right, blk.right_start, False)
            rb = self._line_y(self._right, max(blk.right_start, blk.right_end - 1), True)

            # Need at least one endpoint from each side to draw
            if (lt is None and lb is None) or (rt is None and rb is None):
                continue

            lt_y = float(lt if lt is not None else lb)  # type: ignore[arg-type]
            lb_y = float(lb if lb is not None else lt)  # type: ignore[arg-type]
            rt_y = float(rt if rt is not None else rb)  # type: ignore[arg-type]
            rb_y = float(rb if rb is not None else rt)  # type: ignore[arg-type]

            if blk.tag == "replace":
                color = QColor(60, 120, 220, 55)
            else:
                color = QColor(60, 180, 60, 55)

            path = QPainterPath()
            path.moveTo(0.0, lt_y)
            path.cubicTo(w * 0.4, lt_y, w * 0.6, rt_y, w, rt_y)
            path.lineTo(w, rb_y)
            path.cubicTo(w * 0.6, rb_y, w * 0.4, lb_y, 0.0, lb_y)
            path.closeSubpath()
            painter.fillPath(path, color)

        painter.end()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._reposition_buttons()


# ---------------------------------------------------------------------------
# Find bar
# ---------------------------------------------------------------------------


class _FindBar(QWidget):
    search_changed = Signal(str, bool, bool, str)  # pattern, case_sensitive, use_regex, side
    navigate = Signal(int)                    # +1 next, -1 prev

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("diffFindBar")
        self.setAutoFillBackground(False)
        self.setStyleSheet(
            "QWidget#diffFindBar { background: transparent; border: none; }"
            "QWidget#diffFindBar QLabel { background: transparent; }"
            "QWidget#diffFindBar QCheckBox { background: transparent; }"
        )
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(150)
        self._debounce.timeout.connect(self._emit_search)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        layout.addWidget(QLabel("Find:"))
        self._input = QLineEdit()
        self._input.setPlaceholderText("Search…")
        self._input.setMinimumWidth(180)
        layout.addWidget(self._input)

        self._left_btn = QPushButton("Left")
        self._left_btn.setCheckable(True)
        self._left_btn.setAutoExclusive(True)
        self._left_btn.setChecked(True)
        self._left_btn.setToolTip("Search the left editor")
        layout.addWidget(self._left_btn)

        self._right_btn = QPushButton("Right")
        self._right_btn.setCheckable(True)
        self._right_btn.setAutoExclusive(True)
        self._right_btn.setToolTip("Search the right editor")
        layout.addWidget(self._right_btn)

        self._prev_btn = QPushButton("▲")
        self._prev_btn.setFixedWidth(32)
        self._prev_btn.setToolTip("Previous match")
        layout.addWidget(self._prev_btn)

        self._next_btn = QPushButton("▼")
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

        close_btn = QPushButton("✕")
        close_btn.setFixedWidth(32)
        close_btn.setToolTip("Close find bar")
        layout.addWidget(close_btn)

        self._input.textChanged.connect(lambda _: self._debounce.start())
        self._case_cb.toggled.connect(lambda _: self._debounce.start())
        self._regex_cb.toggled.connect(lambda _: self._debounce.start())
        self._left_btn.toggled.connect(lambda checked: self._debounce.start() if checked else None)
        self._right_btn.toggled.connect(lambda checked: self._debounce.start() if checked else None)
        self._prev_btn.clicked.connect(lambda: self.navigate.emit(-1))
        self._next_btn.clicked.connect(lambda: self.navigate.emit(1))
        close_btn.clicked.connect(self._close)

        # Enter/Shift+Enter navigate
        self._input.returnPressed.connect(lambda: self.navigate.emit(1))

    def toggle(self) -> None:
        if self.isHidden():
            self.setVisible(True)
        self._input.setFocus()
        self._input.selectAll()

    def search_side(self) -> str:
        return "right" if self._right_btn.isChecked() else "left"

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
            self.search_side(),
        )

    @Slot()
    def _close(self) -> None:
        self.setVisible(False)
        # Clear search by emitting empty pattern
        self.search_changed.emit("", False, False, self.search_side())


# ---------------------------------------------------------------------------
# Main dialog
# ---------------------------------------------------------------------------


class _ElidingLabel(QLabel):
    """QLabel that elides its text on the left when there is not enough space."""

    def paintEvent(self, event) -> None:
        from PySide6.QtGui import QFontMetrics
        painter = QPainter(self)
        fm = QFontMetrics(self.font())
        elided = fm.elidedText(self.text(), Qt.TextElideMode.ElideLeft, self.width())
        painter.drawText(self.rect(), self.alignment(), elided)
        painter.end()


class DiffToolDialog(QDialog):
    def __init__(self, parent: QWidget | None = None, *, settings: AppSettings | None = None) -> None:
        super().__init__(parent)
        loaded_settings = settings if isinstance(settings, AppSettings) else SettingsService().load()
        self._settings = AppSettings.from_dict(loaded_settings.to_dict())
        self._editor_font = _build_terminal_font(self._settings)
        self.setWindowTitle("Diff Tool")
        self.resize(1200, 760)

        # State
        self._diff_blocks: list[_DiffBlock] = []
        self._diff_generation: int = 0  # incremented each time a new diff starts
        self._diff_threads: list = []  # list of (QThread, _DiffWorker) to prevent GC
        self._left_path: str | None = None
        self._right_path: str | None = None
        self._syncing_scroll = False
        self._search_matches_left: list[tuple[int, int]] = []
        self._search_matches_right: list[tuple[int, int]] = []
        self._search_current = 0   # index into combined match list
        self._last_search: tuple[str, bool, bool, str] = ("", False, False, "left")

        # Build UI
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(6)

        # --- Header row ---
        header_row = QHBoxLayout()
        header_row.setSpacing(4)

        left_header = self._build_panel_header("left")
        right_header = self._build_panel_header("right")
        header_row.addWidget(left_header, 1)
        # spacer for center gutter
        gutter_spacer = QWidget()
        gutter_spacer.setFixedWidth(56)
        header_row.addWidget(gutter_spacer)
        header_row.addWidget(right_header, 1)
        root.addLayout(header_row)

        # --- Editor area ---
        editor_row = QHBoxLayout()
        editor_row.setSpacing(0)
        editor_row.setContentsMargins(0, 0, 0, 0)

        left_col, self._left_editor, self._left_map = self._build_editor_column("left")
        right_col, self._right_editor, self._right_map = self._build_editor_column("right")
        self._center_gutter = _CenterGutter(self._left_editor, self._right_editor, self)

        editor_row.addWidget(left_col, 1)
        editor_row.addWidget(self._center_gutter, 0)
        editor_row.addWidget(right_col, 1)
        root.addLayout(editor_row, 1)

        # --- Apply All bar ---
        apply_row = QHBoxLayout()
        apply_row.setSpacing(6)
        self._apply_rtl_btn = QPushButton("← Apply All")
        self._apply_rtl_btn.setToolTip("Replace all right-side diffs with left")
        self._open_search_btn = QPushButton("Search")
        self._open_search_btn.setToolTip("Show or hide search (Ctrl+F opens)")
        self._apply_ltr_btn = QPushButton("Apply All →")
        self._apply_ltr_btn.setToolTip("Replace all left-side diffs with right… wait, copy left to right")
        apply_row.addWidget(self._apply_rtl_btn)
        apply_row.addStretch(1)
        apply_row.addWidget(self._open_search_btn)
        apply_row.addWidget(self._apply_ltr_btn)
        root.addLayout(apply_row)

        # --- Find bar ---
        self._find_bar = _FindBar(self)
        self._find_bar.setVisible(False)
        root.addWidget(self._find_bar)

        # --- Status ---
        self._status_label = QLabel("Open files or paste text to compare.")
        root.addWidget(self._status_label)

        # --- Timers ---
        self._diff_timer = QTimer(self)
        self._diff_timer.setSingleShot(True)
        self._diff_timer.setInterval(300)
        self._diff_timer.timeout.connect(self._run_diff)

        # --- Wiring ---
        self._left_editor.document().contentsChanged.connect(self._schedule_diff)
        self._right_editor.document().contentsChanged.connect(self._schedule_diff)

        self._left_editor.scroll_changed.connect(self._on_left_scroll)
        self._right_editor.scroll_changed.connect(self._on_right_scroll)
        # Also refresh gutter on scrollbar value changes (covers programmatic scrolls)
        self._left_editor.verticalScrollBar().valueChanged.connect(self._on_left_scroll_bar)
        self._right_editor.verticalScrollBar().valueChanged.connect(self._on_right_scroll_bar)

        self._center_gutter.apply_left_to_right.connect(self._apply_left_to_right)
        self._center_gutter.apply_right_to_left.connect(self._apply_right_to_left)

        self._apply_ltr_btn.clicked.connect(self._apply_all_left_to_right)
        self._apply_rtl_btn.clicked.connect(self._apply_all_right_to_left)
        self._open_search_btn.clicked.connect(self._toggle_find_bar)

        self._find_bar.search_changed.connect(self._on_search_changed)
        self._find_bar.navigate.connect(self._on_navigate)

        QShortcut(QKeySequence("Ctrl+F"), self).activated.connect(self._find_bar.toggle)
        QShortcut(QKeySequence("Escape"), self).activated.connect(self._on_escape)

        # Restore saved geometry, clamping to available screen space
        _geo = QSettings("SnakeSh", "SnakeSh").value("diff_tool/geometry")
        if _geo is not None:
            self.restoreGeometry(_geo)
        self._clamp_to_screen()

    def closeEvent(self, event) -> None:
        # Discard any pending result so the finished slot is a no-op if it fires late
        self._diff_generation += 1
        # Stop all in-flight worker threads cleanly
        for thread, _worker in list(self._diff_threads):
            if thread.isRunning():
                thread.quit()
                if not thread.wait(2000):
                    thread.terminate()
                    thread.wait(500)
        QSettings("SnakeSh", "SnakeSh").setValue("diff_tool/geometry", self.saveGeometry())
        super().closeEvent(event)

    def apply_runtime_settings(self, settings: AppSettings) -> None:
        self._settings = AppSettings.from_dict(settings.to_dict())
        self._editor_font = _build_terminal_font(self._settings)
        for editor in (self._left_editor, self._right_editor):
            vertical_value = editor.verticalScrollBar().value()
            horizontal_value = editor.horizontalScrollBar().value()
            editor._apply_editor_font(self._editor_font)
            editor._update_gutter_width()
            editor.verticalScrollBar().setValue(vertical_value)
            editor.horizontalScrollBar().setValue(horizontal_value)
            editor.viewport().update()
        self._center_gutter.update()

    def _clamp_to_screen(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        w = min(self.width(), available.width())
        h = min(self.height(), available.height())
        if w != self.width() or h != self.height():
            self.resize(w, h)
        # Ensure top-left corner is within the available area
        x = max(available.left(), min(self.x(), available.right() - self.width()))
        y = max(available.top(), min(self.y(), available.bottom() - self.height()))
        if x != self.x() or y != self.y():
            self.move(x, y)

    # ------------------------------------------------------------------
    # Layout builders
    # ------------------------------------------------------------------

    def _build_panel_header(self, side: str) -> QWidget:
        widget = QWidget()
        widget.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        widget.setMinimumWidth(0)
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        open_btn = QPushButton("Open…")
        save_btn = QPushButton("Save")

        path_label = _ElidingLabel("(no file)")
        path_label.setStyleSheet("color: gray;")
        path_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        path_label.setMinimumWidth(0)

        if side == "left":
            layout.addWidget(open_btn)
            layout.addWidget(save_btn)
            layout.addWidget(path_label, 1)
            open_btn.clicked.connect(self._open_left)
            save_btn.clicked.connect(self._save_left)
            self._left_path_label = path_label
        else:
            layout.addWidget(path_label, 1)
            layout.addWidget(save_btn)
            layout.addWidget(open_btn)
            open_btn.clicked.connect(self._open_right)
            save_btn.clicked.connect(self._save_right)
            self._right_path_label = path_label

        return widget

    def _build_editor_column(self, side: str) -> tuple[QWidget, _DiffEditor, _ScrollMapWidget]:
        editor = _DiffEditor(editor_font=self._editor_font)
        editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        map_widget = _ScrollMapWidget(editor, side)

        container = QWidget()
        container.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        container.setMinimumWidth(0)
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        if side == "left":
            layout.addWidget(editor, 1)
            layout.addWidget(map_widget)
        else:
            layout.addWidget(map_widget)
            layout.addWidget(editor, 1)

        return container, editor, map_widget

    @Slot()
    def _toggle_find_bar(self) -> None:
        if not self._find_bar.isHidden():
            self._find_bar._close()
            return
        self._find_bar.toggle()

    # ------------------------------------------------------------------
    # File I/O
    # ------------------------------------------------------------------

    @Slot()
    def _open_left(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open Left File")
        if path and self._load_file(path, self._left_editor, self._left_path_label):
            self._left_path = path

    @Slot()
    def _open_right(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open Right File")
        if path and self._load_file(path, self._right_editor, self._right_path_label):
            self._right_path = path

    def _load_file(self, path: str, editor: _DiffEditor, label: QLabel) -> bool:
        """Read *path* into *editor*. Returns True on success, False if aborted."""
        p = Path(path)

        # --- Size check ---
        try:
            size = p.stat().st_size
        except OSError as exc:
            QMessageBox.critical(self, "Error", f"Could not access file:\n{exc}")
            return False

        if size > _MAX_SIZE_BYTES:
            QMessageBox.warning(
                self,
                "File Too Large",
                f"This file is {size / 1_048_576:.1f} MB, which exceeds the "
                f"{_MAX_SIZE_BYTES // 1_048_576} MB limit for the Diff Tool.\n\n"
                "The file was not opened.",
            )
            return False

        if size > _WARN_SIZE_BYTES:
            reply = QMessageBox.question(
                self,
                "Large File",
                f"This file is {size / 1_048_576:.1f} MB. "
                "Comparing large files may be slow.\n\nOpen anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return False

        # --- Read raw bytes (needed for binary detection) ---
        try:
            data = p.read_bytes()
        except OSError as exc:
            QMessageBox.critical(self, "Error", f"Could not read file:\n{exc}")
            return False

        # --- Binary check ---
        if _is_binary(data):
            reply = QMessageBox.question(
                self,
                "Binary File",
                "This file appears to be a binary file.\n"
                "Comparing binary files is usually not meaningful.\n\nOpen anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return False

        # --- Decode and load ---
        text = data.decode("utf-8", errors="replace")
        editor.setPlainText(text)
        label.setText(path)
        label.setStyleSheet("")
        label.setToolTip(path)
        return True

    @Slot()
    def _save_left(self) -> None:
        self._save_side("left")

    @Slot()
    def _save_right(self) -> None:
        self._save_side("right")

    def _save_side(self, side: str) -> None:
        if side == "left":
            path = self._left_path
            editor = self._left_editor
            label = self._left_path_label
        else:
            path = self._right_path
            editor = self._right_editor
            label = self._right_path_label

        if not path:
            path, _ = QFileDialog.getSaveFileName(self, f"Save {side.title()} File")
            if not path:
                return
            if side == "left":
                self._left_path = path
            else:
                self._right_path = path
            label.setText(path)
            label.setStyleSheet("")
            label.setToolTip(path)

        try:
            Path(path).write_text(editor.toPlainText(), encoding="utf-8")
        except OSError as exc:
            QMessageBox.critical(self, "Error", f"Could not save file:\n{exc}")

    # ------------------------------------------------------------------
    # Diff computation
    # ------------------------------------------------------------------

    @Slot()
    def _schedule_diff(self) -> None:
        self._diff_timer.start()

    @Slot()
    def _run_diff(self) -> None:
        self._diff_generation += 1
        generation = self._diff_generation

        left_text = self._left_editor.toPlainText()
        right_text = self._right_editor.toPlainText()

        # For trivially empty content skip the thread overhead
        if not left_text and not right_text:
            self._on_diff_finished([], generation)
            return

        self._status_label.setText("Computing differences…")

        # No parent — avoids "Destroyed while thread is still running" crash on close.
        # Both thread and worker are kept in _diff_threads to prevent Python GC while running.
        thread = QThread()
        worker = _DiffWorker(left_text, right_text, generation)
        entry = (thread, worker)
        self._diff_threads.append(entry)

        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_diff_finished)
        worker.finished.connect(thread.quit)
        # Clean up the entry once the thread stops (keeps the list tidy)
        thread.finished.connect(lambda e=entry: self._diff_threads.remove(e) if e in self._diff_threads else None)
        thread.start()

    def _on_diff_finished(self, blocks: list, generation: int) -> None:
        # Discard stale results from superseded computations
        if generation != self._diff_generation:
            return
        self._diff_blocks = blocks
        self._rebuild_all_extra_selections()
        self._center_gutter.set_diff_blocks(self._diff_blocks)
        self._left_map.set_diff_blocks(self._diff_blocks)
        self._right_map.set_diff_blocks(self._diff_blocks)
        if self._last_search[0]:
            self._run_search(*self._last_search)
        self._update_status()

    def _make_diff_sels(self, editor: _DiffEditor, side: str) -> list:
        from PySide6.QtWidgets import QTextEdit
        sels = []
        doc = editor.document()
        palette = editor.palette()
        is_dark = palette.color(QPalette.ColorRole.Window).lightness() < 128

        for blk in self._diff_blocks:
            if blk.tag == "equal":
                continue
            if side == "left":
                if blk.tag == "insert":
                    continue
                start_line, end_line = blk.left_start, blk.left_end
                if blk.tag == "delete":
                    color = QColor(200, 60, 60, 90) if is_dark else QColor(220, 60, 60, 70)
                else:
                    color = QColor(80, 120, 220, 90) if is_dark else QColor(60, 100, 210, 70)
            else:
                if blk.tag == "delete":
                    continue
                start_line, end_line = blk.right_start, blk.right_end
                if blk.tag == "insert":
                    color = QColor(60, 180, 60, 90) if is_dark else QColor(40, 180, 40, 70)
                else:
                    color = QColor(80, 120, 220, 90) if is_dark else QColor(60, 100, 210, 70)

            for line_idx in range(start_line, end_line):
                text_block = doc.findBlockByNumber(line_idx)
                if not text_block.isValid():
                    break
                sel = QTextEdit.ExtraSelection()
                fmt = QTextCharFormat()
                fmt.setBackground(color)
                fmt.setProperty(QTextCharFormat.Property.FullWidthSelection, True)
                sel.format = fmt
                sel.cursor = QTextCursor(text_block)
                sels.append(sel)
        return sels

    def _make_inline_diff_sels(self, side: str) -> list:
        """Character-level highlights within replace blocks (darker overlay on changed chars)."""
        from PySide6.QtWidgets import QTextEdit
        sels = []
        editor = self._left_editor if side == "left" else self._right_editor
        doc = editor.document()
        palette = editor.palette()
        is_dark = palette.color(QPalette.ColorRole.Window).lightness() < 128

        for blk in self._diff_blocks:
            if blk.tag != "replace":
                continue

            left_lines = self._get_lines(self._left_editor, blk.left_start, blk.left_end)
            right_lines = self._get_lines(self._right_editor, blk.right_start, blk.right_end)
            pairs = min(len(left_lines), len(right_lines))

            for i in range(pairs):
                if side == "left":
                    line_idx = blk.left_start + i
                    color = QColor(200, 70, 70, 170) if is_dark else QColor(220, 80, 80, 140)
                else:
                    line_idx = blk.right_start + i
                    color = QColor(40, 190, 40, 170) if is_dark else QColor(30, 170, 30, 140)

                text_block = doc.findBlockByNumber(line_idx)
                if not text_block.isValid():
                    continue
                block_pos = text_block.position()

                matcher = difflib.SequenceMatcher(
                    None, left_lines[i], right_lines[i], autojunk=False
                )
                for op, i1, i2, j1, j2 in matcher.get_opcodes():
                    if op == "equal":
                        continue
                    if side == "left":
                        if op == "insert":
                            continue
                        start, end = i1, i2
                    else:
                        if op == "delete":
                            continue
                        start, end = j1, j2
                    if start >= end:
                        continue

                    sel = QTextEdit.ExtraSelection()
                    cursor = QTextCursor(doc)
                    cursor.setPosition(block_pos + start)
                    cursor.setPosition(block_pos + end, QTextCursor.MoveMode.KeepAnchor)
                    fmt = QTextCharFormat()
                    fmt.setBackground(color)
                    sel.format = fmt
                    sel.cursor = cursor
                    sels.append(sel)
        return sels

    def _make_search_sels(self, editor: _DiffEditor, matches: list[tuple[int, int]]) -> list:
        from PySide6.QtWidgets import QTextEdit
        sels = []
        doc = editor.document()
        color = QColor(255, 165, 0, 160)
        for pos, length in matches:
            sel = QTextEdit.ExtraSelection()
            cursor = QTextCursor(doc)
            cursor.setPosition(pos)
            cursor.setPosition(pos + length, QTextCursor.MoveMode.KeepAnchor)
            fmt = QTextCharFormat()
            fmt.setBackground(color)
            sel.format = fmt
            sel.cursor = cursor
            sels.append(sel)
        return sels

    def _rebuild_all_extra_selections(self) -> None:
        self._left_editor.setExtraSelections(
            self._make_diff_sels(self._left_editor, "left")
            + self._make_inline_diff_sels("left")
            + self._make_search_sels(self._left_editor, self._search_matches_left)
        )
        self._right_editor.setExtraSelections(
            self._make_diff_sels(self._right_editor, "right")
            + self._make_inline_diff_sels("right")
            + self._make_search_sels(self._right_editor, self._search_matches_right)
        )

    def _update_status(self) -> None:
        changed_lines = 0
        for b in self._diff_blocks:
            if b.tag == "delete":
                changed_lines += b.left_end - b.left_start
            elif b.tag == "insert":
                changed_lines += b.right_end - b.right_start
            elif b.tag == "replace":
                changed_lines += (b.left_end - b.left_start) + (b.right_end - b.right_start)
        if changed_lines == 0:
            self._status_label.setText("Files are identical.")
        elif changed_lines == 1:
            self._status_label.setText("1 line changed.")
        else:
            self._status_label.setText(f"{changed_lines} lines changed.")

    # ------------------------------------------------------------------
    # Synchronized scrolling
    # ------------------------------------------------------------------

    @Slot()
    def _on_left_scroll(self) -> None:
        self._sync_from(self._left_editor, self._right_editor)

    @Slot()
    def _on_right_scroll(self) -> None:
        self._sync_from(self._right_editor, self._left_editor)

    @Slot()
    def _on_left_scroll_bar(self) -> None:
        self._left_map.update()
        self._center_gutter.refresh()

    @Slot()
    def _on_right_scroll_bar(self) -> None:
        self._right_map.update()
        self._center_gutter.refresh()

    def _sync_from(self, leader: _DiffEditor, follower: _DiffEditor) -> None:
        if self._syncing_scroll:
            return
        self._syncing_scroll = True
        try:
            bar_l = leader.verticalScrollBar()
            bar_f = follower.verticalScrollBar()
            _set_scroll_center_ratio(bar_f, _scroll_center_ratio(bar_l))
        finally:
            self._syncing_scroll = False
        self._center_gutter.refresh()
        self._left_map.update()
        self._right_map.update()

    # ------------------------------------------------------------------
    # Block application
    # ------------------------------------------------------------------

    @Slot(int)
    def _apply_left_to_right(self, idx: int) -> None:
        if idx >= len(self._diff_blocks):
            return
        blk = self._diff_blocks[idx]
        lines = self._get_lines(self._left_editor, blk.left_start, blk.left_end)
        self._replace_lines(self._right_editor, blk.right_start, blk.right_end, lines)

    @Slot(int)
    def _apply_right_to_left(self, idx: int) -> None:
        if idx >= len(self._diff_blocks):
            return
        blk = self._diff_blocks[idx]
        lines = self._get_lines(self._right_editor, blk.right_start, blk.right_end)
        self._replace_lines(self._left_editor, blk.left_start, blk.left_end, lines)

    @Slot()
    def _apply_all_left_to_right(self) -> None:
        for i in reversed(range(len(self._diff_blocks))):
            blk = self._diff_blocks[i]
            if blk.tag == "equal":
                continue
            lines = self._get_lines(self._left_editor, blk.left_start, blk.left_end)
            self._replace_lines(self._right_editor, blk.right_start, blk.right_end, lines)

    @Slot()
    def _apply_all_right_to_left(self) -> None:
        for i in reversed(range(len(self._diff_blocks))):
            blk = self._diff_blocks[i]
            if blk.tag == "equal":
                continue
            lines = self._get_lines(self._right_editor, blk.right_start, blk.right_end)
            self._replace_lines(self._left_editor, blk.left_start, blk.left_end, lines)

    def _get_lines(self, editor: _DiffEditor, start: int, end: int) -> list[str]:
        doc = editor.document()
        result = []
        for i in range(start, end):
            block = doc.findBlockByNumber(i)
            if block.isValid():
                result.append(block.text())
        return result

    def _replace_lines(self, editor: _DiffEditor, start: int, end: int, new_lines: list[str]) -> None:
        doc = editor.document()
        block_count = doc.blockCount()

        cursor = QTextCursor(doc)
        cursor.beginEditBlock()
        try:
            if start < block_count:
                first_block = doc.findBlockByNumber(start)
                cursor.setPosition(first_block.position())
            else:
                # Appending past end of document
                last_block = doc.findBlockByNumber(block_count - 1)
                cursor.setPosition(last_block.position() + last_block.length() - 1)
                cursor.insertText("\n" + "\n".join(new_lines))
                return

            if end > 0 and end <= block_count:
                last_block = doc.findBlockByNumber(end - 1)
                cursor.setPosition(last_block.position() + last_block.length() - 1, QTextCursor.MoveMode.KeepAnchor)
            elif end > block_count:
                cursor.movePosition(QTextCursor.MoveOperation.End, QTextCursor.MoveMode.KeepAnchor)
            else:
                cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)

            cursor.insertText("\n".join(new_lines))
        finally:
            cursor.endEditBlock()

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    @Slot(str, bool, bool, str)
    def _on_search_changed(self, pattern: str, case_sensitive: bool, use_regex: bool, side: str) -> None:
        self._last_search = (pattern, case_sensitive, use_regex, side)
        if not pattern:
            self._search_matches_left = []
            self._search_matches_right = []
            self._search_current = 0
            self._find_bar.clear_match_info()
            self._rebuild_all_extra_selections()
            return
        self._run_search(pattern, case_sensitive, use_regex, side)

    def _run_search(self, pattern: str, case_sensitive: bool, use_regex: bool, side: str) -> None:
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            if not use_regex:
                pattern = re.escape(pattern)
            compiled = re.compile(pattern, flags)
        except re.error:
            self._search_matches_left = []
            self._search_matches_right = []
            self._search_current = 0
            self._find_bar.set_match_info(0, 0)
            self._rebuild_all_extra_selections()
            return

        self._search_matches_left = []
        self._search_matches_right = []
        if side == "right":
            self._search_matches_right = [
                (m.start(), m.end() - m.start())
                for m in compiled.finditer(self._right_editor.toPlainText())
            ]
        else:
            self._search_matches_left = [
                (m.start(), m.end() - m.start())
                for m in compiled.finditer(self._left_editor.toPlainText())
            ]

        total = len(self._search_matches_left) + len(self._search_matches_right)
        self._search_current = min(self._search_current, max(0, total - 1))
        self._find_bar.set_match_info(self._search_current + 1 if total else 0, total)
        self._rebuild_all_extra_selections()

        if total > 0:
            self._scroll_to_match(self._search_current)

    @Slot(int)
    def _on_navigate(self, direction: int) -> None:
        total = len(self._search_matches_left) + len(self._search_matches_right)
        if total == 0:
            return
        self._search_current = (self._search_current + direction) % total
        self._find_bar.set_match_info(self._search_current + 1, total)
        self._scroll_to_match(self._search_current)

    def _scroll_to_match(self, index: int) -> None:
        nl = len(self._search_matches_left)
        if index < nl:
            pos, _ = self._search_matches_left[index]
            editor = self._left_editor
        else:
            pos, _ = self._search_matches_right[index - nl]
            editor = self._right_editor

        cursor = QTextCursor(editor.document())
        cursor.setPosition(pos)
        editor.setTextCursor(cursor)
        editor.ensureCursorVisible()

    # ------------------------------------------------------------------
    # Misc
    # ------------------------------------------------------------------

    @Slot()
    def _on_escape(self) -> None:
        if self._find_bar.isVisible():
            self._find_bar._close()
