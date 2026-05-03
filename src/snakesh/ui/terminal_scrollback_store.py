from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import re
import threading
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ScrollbackMatch:
    line_index: int
    column: int
    length: int


@dataclass(frozen=True, slots=True)
class ScrollbackPage:
    start_line_index: int
    total_line_count: int
    lines: list[str]

    @property
    def end_line_index(self) -> int:
        return self.start_line_index + len(self.lines)


@dataclass(frozen=True, slots=True)
class ScrollbackSearchResult:
    pattern: str
    case_sensitive: bool
    use_regex: bool
    matches: list[ScrollbackMatch]


class ScrollbackProvider(Protocol):
    def max_lines(self) -> int: ...

    def set_max_lines(self, max_lines: int) -> None: ...

    def snapshot_tail(self, *, window_lines: int = 1000) -> ScrollbackPage: ...

    def snapshot_window(self) -> ScrollbackPage: ...

    def page_ending_at(self, end_line_index: int, *, window_lines: int) -> ScrollbackPage: ...

    def page_for_line(self, line_index: int, *, window_lines: int) -> ScrollbackPage: ...

    def read_page(self, start_line_index: int, end_line_index: int) -> ScrollbackPage: ...

    def search(
        self,
        pattern: str,
        *,
        case_sensitive: bool,
        use_regex: bool,
        max_matches: int | None = None,
    ) -> ScrollbackSearchResult: ...


class TerminalScrollbackStore:
    def __init__(
        self,
        *,
        max_lines: int,
        line_source: Callable[[], Sequence[str]] | None = None,
    ) -> None:
        self._max_lines = max(100, max_lines)
        self._line_source = line_source
        self._closed = False
        self._lock = threading.Lock()

    def max_lines(self) -> int:
        with self._lock:
            return self._max_lines

    def set_max_lines(self, max_lines: int) -> None:
        with self._lock:
            self._max_lines = max(100, max_lines)

    def snapshot_tail(self, *, window_lines: int = 1000) -> ScrollbackPage:
        total = self.total_line_count()
        return self.page_ending_at(total, window_lines=window_lines)

    def snapshot_window(self) -> ScrollbackPage:
        lines = self._snapshot_lines()
        return ScrollbackPage(0, len(lines), lines)

    def total_line_count(self) -> int:
        return len(self._snapshot_lines())

    def page_ending_at(self, end_line_index: int, *, window_lines: int) -> ScrollbackPage:
        total = self.total_line_count()
        end = max(0, min(total, end_line_index))
        start = max(0, end - max(1, window_lines))
        return self.read_page(start, end)

    def page_for_line(self, line_index: int, *, window_lines: int) -> ScrollbackPage:
        total = self.total_line_count()
        if total <= 0:
            return ScrollbackPage(0, 0, [])
        normalized = max(0, min(total - 1, line_index))
        size = max(1, window_lines)
        start = max(0, normalized - (size // 2))
        end = min(total, start + size)
        if end - start < size:
            start = max(0, end - size)
        return self.read_page(start, end)

    def read_page(self, start_line_index: int, end_line_index: int) -> ScrollbackPage:
        lines = self._snapshot_lines()
        total = len(lines)
        if total <= 0:
            return ScrollbackPage(0, 0, [])
        start = max(0, min(total, start_line_index))
        end = max(start, min(total, end_line_index))
        return ScrollbackPage(start, total, lines[start:end])

    def search(
        self,
        pattern: str,
        *,
        case_sensitive: bool,
        use_regex: bool,
        max_matches: int | None = None,
    ) -> ScrollbackSearchResult:
        if not pattern:
            return ScrollbackSearchResult(pattern, case_sensitive, use_regex, [])
        flags = 0 if case_sensitive else re.IGNORECASE
        search_pattern = pattern if use_regex else re.escape(pattern)
        compiled = re.compile(search_pattern, flags)

        matches: list[ScrollbackMatch] = []
        for line_index, line_text in enumerate(self._snapshot_lines()):
            for match in compiled.finditer(line_text):
                matches.append(
                    ScrollbackMatch(
                        line_index=line_index,
                        column=match.start(),
                        length=max(1, match.end() - match.start()),
                    )
                )
                if max_matches is not None and len(matches) >= max_matches:
                    return ScrollbackSearchResult(pattern, case_sensitive, use_regex, matches)
        return ScrollbackSearchResult(pattern, case_sensitive, use_regex, matches)

    def close(self) -> None:
        with self._lock:
            self._closed = True

    def _snapshot_lines(self) -> list[str]:
        with self._lock:
            if self._closed or self._line_source is None:
                return []
            max_lines = self._max_lines
            line_source = self._line_source
        lines = [str(line) for line in line_source()]
        if max_lines > 0 and len(lines) > max_lines:
            return lines[-max_lines:]
        return lines
