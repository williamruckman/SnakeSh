"""Continuously drain a viewer's output while retaining only a bounded tail."""
from __future__ import annotations

from collections import deque
from collections.abc import Callable
import re
import threading


class ViewerOutput:
    MAX_CHARS = 16 * 1024

    def __init__(self, stream, *, on_output: Callable[[str], None] | None = None) -> None:
        self._chunks: deque[str] = deque()
        self._size = 0
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._read, args=(stream, on_output), daemon=True)
        self._thread.start()

    def _read(self, stream, on_output: Callable[[str], None] | None) -> None:
        try:
            while chunk := stream.read(2048):
                if isinstance(chunk, bytes):
                    chunk = chunk.decode("utf-8", errors="replace")
                if on_output is not None:
                    on_output(chunk)
                with self._lock:
                    self._chunks.append(chunk)
                    self._size += len(chunk)
                    while self._size > self.MAX_CHARS:
                        self._size -= len(self._chunks.popleft())
        except (OSError, ValueError):
            pass
        finally:
            stream.close()

    def tail(self) -> str:
        self._thread.join(timeout=0.05)
        with self._lock:
            text = "".join(self._chunks)
        text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
        return text.strip()

    @property
    def finished(self) -> bool:
        return not self._thread.is_alive()
