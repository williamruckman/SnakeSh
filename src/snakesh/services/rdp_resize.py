"""Recognize FreeRDP's resize reactivation failure without retrying other exits."""
from __future__ import annotations

from pathlib import Path
import re
import threading


_CLIENTS = {"sdl-freerdp", "sdl-freerdp3", "xfreerdp", "xfreerdp3"}
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_TRANSITION = re.compile(r"\b(CONNECTION_STATE_\w+) --> (CONNECTION_STATE_\w+)\b")


def is_freerdp_command(command: list[str]) -> bool:
    return bool(command) and Path(command[0]).name in _CLIENTS


def same_rdp_connection(before: list[str], after: list[str]) -> bool:
    def connection_options(command: list[str]) -> list[str]:
        return [
            arg for arg in command
            if arg not in {"/dynamic-resolution", "/smart-sizing", "/f"}
            and not arg.startswith(("/size:", "/parent-window:"))
        ]
    return connection_options(before) == connection_options(after)


def smart_sizing_command(command: list[str]) -> list[str]:
    """Keep the endpoint, authentication and transport options on recovery."""
    result = [arg for arg in command if arg != "/dynamic-resolution"]
    if "/smart-sizing" not in result:
        result.append("/smart-sizing")
    if "/f" not in result and not any(arg.startswith("/size:") for arg in result):
        result.append("/size:1920x1080")
    return result


class RdpResizeMonitor:
    """Process state messages as they arrive, independently of the output tail."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending = ""
        self._established = False
        self._reactivating = False
        self._update_aborted = False
        self._failed = False
        self._cancelled = False

    def feed(self, text: str) -> None:
        with self._lock:
            lines = (self._pending + text).split("\n")
            self._pending = lines.pop()[-4096:]
            for line in lines:
                self._consume(_ANSI.sub("", line))

    def _consume(self, line: str) -> None:
        if "[com.freerdp.core." not in line and "[com.freerdp.core]" not in line:
            return
        if "ERRCONNECT_CONNECT_CANCELLED" in line:
            self._cancelled = True
        if "[rdp_client_transition_to_state]" in line:
            match = _TRANSITION.search(line)
            if match:
                previous, current = match.groups()
                if current == "CONNECTION_STATE_NEGO":
                    self._established = self._reactivating = self._update_aborted = self._failed = False
                elif current == "CONNECTION_STATE_ACTIVE":
                    self._established = True
                    self._reactivating = self._update_aborted = self._failed = False
                elif previous == "CONNECTION_STATE_ACTIVE" and current == "CONNECTION_STATE_CAPABILITIES_EXCHANGE_DEMAND_ACTIVE":
                    self._reactivating = self._established
        if self._reactivating:
            if (
                "[update_recv]" in line
                and "State CONNECTION_STATE_ACTIVE (or higher) requested, but CONNECTION_STATE_FINALIZATION_" in line
                and "found. Aborting." in line
            ):
                self._update_aborted = True
            if self._update_aborted and "[rdp_recv_callback_int]" in line and "STATE_RUN_FAILED" in line:
                self._failed = True

    def should_recover(self, exit_code: int) -> bool:
        with self._lock:
            return exit_code > 0 and self._failed and not self._cancelled
