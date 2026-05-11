from __future__ import annotations

import asyncio
import array
import base64
import codecs
from collections.abc import Callable
from collections import OrderedDict, deque
import copy
from contextlib import contextmanager
import csv
from dataclasses import dataclass, field
import errno
import html
import json
import logging
import math
import os
from pathlib import Path, PureWindowsPath
import platform
import posixpath
import queue
import re
import select
import shlex
import signal
import shutil
import socket
import ssl
import struct
import subprocess
import tempfile
import threading
import time
from typing import Protocol as TypingProtocol, TextIO, TypeVar
from uuid import uuid4
import wave

import asyncssh
from PySide6.QtCore import QByteArray, QEvent, QMimeData, QObject, QRect, QSize, Qt, QProcess, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import (
    QCloseEvent,
    QColor,
    QCursor,
    QDesktopServices,
    QDrag,
    QDragEnterEvent,
    QDropEvent,
    QFont,
    QFontDatabase,
    QFontMetrics,
    QIcon,
    QImage,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QShortcut,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressDialog,
    QPushButton,
    QProgressBar,
    QProxyStyle,
    QListWidget,
    QListWidgetItem,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QSplitterHandle,
    QStatusBar,
    QStyle,
    QTabBar,
    QTabWidget,
    QToolButton,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
import pyte
from pyte.screens import Char, HistoryScreen
from wcwidth import wcwidth
try:
    from PySide6.QtMultimedia import QSoundEffect
except Exception:  # pragma: no cover - multimedia is optional at runtime
    QSoundEffect = None  # type: ignore[assignment]

from snakesh import __version__
from snakesh.core.models import (
    DEFAULT_SFTP_LOCAL_FOLDER,
    DEFAULT_SFTP_REMOTE_FOLDER,
    Protocol,
    Session,
    SSHAutomationStep,
    is_auto_resolution,
    normalize_rdp_audio_mode,
    normalize_remote_launch_mode,
    normalize_serial_terminal_type,
    parse_resolution,
)
from snakesh.core.hostkeys import known_hosts_path, trust_host_key
from snakesh.core.tool_registry import (
    TOOL_REGISTRY,
    TOOL_REGISTRY_BY_KEY,
    normalize_profile_startup_tool_keys,
    normalize_tool_keys,
    profile_startup_tool_entries,
)
from snakesh.protocols.nomachine import build_nomachine_launch, launch_nomachine
from snakesh.protocols.rdp import (
    build_rdp_command,
    build_rdp_stdin_payload,
    clear_linux_rdp_known_host,
    launch_rdp,
    prepare_rdp_launch_environment,
)
from snakesh.protocols.sftp_client import (
    OverwriteConflict,
    SFTPClient,
    SFTPEntry,
    TransferCancelledError,
    TransferProgress,
)
from snakesh.protocols.ssh_client import SSHClient
from snakesh.protocols.vnc import build_vnc_launch, launch_vnc
from snakesh.runtime import is_appimage, sanitized_local_shell_environment
from snakesh.services.credential_service import CredentialService
from snakesh.services.linux_desktop_install_service import (
    LinuxDesktopIntegrationError,
    desktop_integration_needs_update,
    install_desktop_integration,
    installed_desktop_integration_version,
    is_desktop_integration_installed,
    uninstall_desktop_integration,
)
from snakesh.services.secrets_service import SecretsService
from snakesh.services.ssh_key_service import resolve_existing_public_key, validate_public_key_file
from snakesh.services.backup_service import (
    BackupError,
    BackupFormatError,
    BackupInvalidPasswordError,
    BackupPasswordRequiredError,
    BackupPayload,
    BackupService,
)
from snakesh.services.platform_deps import (
    PlatformDependency,
    attempt_auto_install,
    dependency_help_url,
    required_dependencies,
    suggested_install_command,
)
from snakesh.services.privilege_service import command_to_display
from snakesh.services.settings_service import AppSettings, SettingsService, resolve_terminal_default_colors
from snakesh.services.securecrt_codec import SecureCRTCodecService
from snakesh.services.session_service import SessionService
from snakesh.services.third_party_import_service import ThirdPartyImportReport, ThirdPartyImportService
from snakesh.services.tool_instance_service import has_active_tool_instance
from snakesh.services.tool_launcher_service import (
    installed_tool_launcher_keys,
    launcher_sync_summary,
    sync_tool_launchers,
)
from snakesh.services.tool_process_service import (
    launch_standalone_tool,
    ping_tool_arguments,
    queue_tool_settings_sync,
)
from snakesh.services.x11_service import X11Service
from snakesh.ui.settings_dialog import SettingsDialog
from snakesh.ui.terminal_scrollback_dialog import TerminalScrollbackDialog
from snakesh.ui.terminal_scrollback_store import TerminalScrollbackStore
from snakesh.ui.third_party_io_dialog import ThirdPartyImportExportDialog
from snakesh.ui.tool_launcher_manager_dialog import ToolLauncherManagerDialog
from snakesh.ui.window_placement import placement_to_payload
from snakesh.ui.session_editor import SessionEditorDialog
from snakesh.ui.theme import apply_theme, blend_colors, close_icon_path, readable_foreground_color
from snakesh.ui.tool_icon_helpers import TOOL_MENU_ICON_SIZE, app_menu_icon, tool_menu_icon

if os.name == "posix":
    import fcntl
    import pty
    import termios


_T = TypeVar("_T")
TREE_KIND_ROLE = Qt.UserRole
TREE_SESSION_ID_ROLE = Qt.UserRole + 1
TREE_FOLDER_PATH_ROLE = Qt.UserRole + 2
TREE_SESSION_FOLDER_ROLE = Qt.UserRole + 3
RemoteLaunchSpec = tuple[list[str], str, dict[str, str] | None] | tuple[list[str], str, dict[str, str] | None, str | None]
RemoteDetachedBuilder = Callable[[], RemoteLaunchSpec]
RemoteEmbeddedBuilder = Callable[[int], RemoteLaunchSpec]
LEGACY_REMOTE_VIEWER_DEBUG_ENV = "snakesh_REMOTE_DEBUG"
REMOTE_VIEWER_DEBUG_ENV = "SNAKESH_REMOTE_DEBUG"
REMOTE_VIEWER_DEBUG_LOG = Path(tempfile.gettempdir()) / "snakesh-remote-viewer.log"
LOCAL_PTY_CAPTURE_DIR_ENV = "SNAKESH_LOCAL_PTY_CAPTURE_DIR"
TERMINAL_DEBUG_UNKNOWN_SEQUENCES_ENV = "SNAKESH_TERMINAL_DEBUG_UNKNOWN_SEQUENCES"
TERMINAL_RENDER_MODE_ENV = "SNAKESH_TERMINAL_RENDER_MODE"
_TERMINAL_BELL_WAV = Path(tempfile.gettempdir()) / "snakesh-terminal-bell.wav"
_SNAKESH_IMPORT_SUFFIXES = {".ssx"}
_TOOL_LAUNCH_PLACEMENT_ENV = "SNAKESH_TOOL_LAUNCH_PLACEMENT"
_UI_SETTINGS_SAVE_COALESCE_MS = 150
_LOGGER = logging.getLogger(__name__)
_WINDOWS_EXTERNAL_TERMINAL_HOSTS = {
    "conhost",
    "conhost.exe",
    "openconsole",
    "openconsole.exe",
    "windowsterminal",
    "windowsterminal.exe",
    "wt",
    "wt.exe",
}
_BULK_DISCONNECT_TERMINAL_KINDS = {"SSH", "TELNET", "SERIAL", "LOCAL"}


class _ToolMenuIconStyle(QProxyStyle):
    def pixelMetric(self, metric, option=None, widget=None) -> int:  # noqa: N802
        if metric == QStyle.PixelMetric.PM_SmallIconSize:
            return TOOL_MENU_ICON_SIZE
        return super().pixelMetric(metric, option, widget)

    def sizeFromContents(self, contents_type, option, contents_size, widget=None) -> QSize:  # noqa: N802
        size = super().sizeFromContents(contents_type, option, contents_size, widget)
        if contents_type == QStyle.ContentsType.CT_MenuItem:
            size.setHeight(max(size.height(), TOOL_MENU_ICON_SIZE + 8))
        return size


def _env_flag_enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _strip_wrapping_quotes(value: str) -> str:
    trimmed = value.strip()
    if len(trimmed) >= 2 and trimmed[0] == trimmed[-1] and trimmed[0] in {'"', "'"}:
        return trimmed[1:-1]
    return trimmed


def _os_type_summary() -> str:
    if platform.system().lower() == "windows":
        return _windows_os_type_summary()
    return platform.platform()


def _windows_os_type_summary() -> str:
    try:
        release, version, csd, _ptype = platform.win32_ver()
    except Exception:
        return platform.platform()
    version_text = str(version or "").strip()
    build = _windows_build_from_version(version_text)
    if build is not None and build >= 22000:
        name = "Windows 11"
    elif str(release or "").strip():
        name = f"Windows {str(release).strip()}"
    elif version_text.startswith("10.0"):
        name = "Windows 10"
    else:
        name = "Windows"

    edition = _windows_edition_label()
    if edition and edition not in name:
        name = f"{name} {edition}"
    details = "-".join(part for part in (version_text, str(csd or "").strip()) if part)
    if details:
        return f"{name} ({details})"
    return name


def _windows_build_from_version(version: str) -> int | None:
    parts = [part for part in str(version or "").strip().split(".") if part]
    if len(parts) < 3:
        return None
    try:
        return int(parts[2])
    except ValueError:
        return None


def _windows_edition_label() -> str:
    getter = getattr(platform, "win32_edition", None)
    if not callable(getter):
        return ""
    try:
        raw_edition = str(getter() or "").strip()
    except Exception:
        return ""
    aliases = {
        "Professional": "Pro",
        "ProfessionalN": "Pro N",
        "Core": "Home",
        "CoreN": "Home N",
    }
    return aliases.get(raw_edition, raw_edition)


def _local_paths_from_mime_data(mime_data: QMimeData | None) -> list[str]:
    if mime_data is None or not mime_data.hasUrls():
        return []
    return [
        path
        for path in (url.toLocalFile() for url in mime_data.urls() if url.isLocalFile())
        if path
    ]


def _quote_posix_shell_path(path: str) -> str:
    return "'" + path.replace("'", "'\"'\"'") + "'"


def _format_dropped_local_paths(paths: list[str]) -> str:
    return " ".join(_quote_posix_shell_path(path) for path in paths if path)


def _is_windows_external_terminal_host(path_or_name: str) -> bool:
    candidate = _strip_wrapping_quotes(path_or_name)
    if not candidate:
        return False
    normalized = candidate.replace("\\", "/").rstrip("/")
    name = normalized.rsplit("/", 1)[-1].strip().lower()
    return name in _WINDOWS_EXTERNAL_TERMINAL_HOSTS


def _is_windows_app_execution_alias(path_or_name: str) -> bool:
    candidate = _strip_wrapping_quotes(path_or_name)
    if not candidate:
        return False
    normalized = candidate.replace("\\", "/").lower()
    return "/microsoft/windowsapps/" in normalized


def _windows_local_shell_known_paths(executable: str) -> list[str]:
    name = executable.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1].strip().lower()
    if not name:
        return []

    if name in {"pwsh", "pwsh.exe"}:
        roots = [
            (os.environ.get("ProgramW6432") or "").strip(),
            (os.environ.get("ProgramFiles") or "").strip(),
            r"C:\Program Files",
        ]
        relatives = (
            "PowerShell/7/pwsh.exe",
            "PowerShell/7-preview/pwsh.exe",
        )
    elif name in {"powershell", "powershell.exe"}:
        roots = [
            (os.environ.get("SystemRoot") or os.environ.get("WINDIR") or r"C:\Windows").strip(),
        ]
        relatives = (
            "System32/WindowsPowerShell/v1.0/powershell.exe",
            "Sysnative/WindowsPowerShell/v1.0/powershell.exe",
        )
    elif name in {"cmd", "cmd.exe"}:
        roots = [
            (os.environ.get("SystemRoot") or os.environ.get("WINDIR") or r"C:\Windows").strip(),
        ]
        relatives = (
            "System32/cmd.exe",
            "Sysnative/cmd.exe",
        )
    else:
        return []

    candidates: list[str] = []
    seen: set[str] = set()
    for root in roots:
        if not root:
            continue
        for relative in relatives:
            candidate = str(PureWindowsPath(root) / PureWindowsPath(relative))
            key = candidate.lower()
            if key in seen:
                continue
            seen.add(key)
            candidates.append(candidate)
    return candidates


def _resolve_windows_local_shell_executable(executable: str) -> str | None:
    stripped = _strip_wrapping_quotes(executable)
    if not stripped:
        return None

    if not _is_windows_external_terminal_host(stripped) and not _is_windows_app_execution_alias(stripped):
        if os.path.exists(stripped):
            return stripped

    for candidate in _windows_local_shell_known_paths(stripped):
        if os.path.exists(candidate):
            return candidate

    resolved = shutil.which(stripped)
    if not resolved:
        return None
    resolved = _strip_wrapping_quotes(resolved)
    if _is_windows_external_terminal_host(resolved) or _is_windows_app_execution_alias(resolved):
        return None
    return resolved


def _ensure_terminal_bell_wav() -> Path | None:
    try:
        if _TERMINAL_BELL_WAV.exists() and _TERMINAL_BELL_WAV.stat().st_size > 44:
            try:
                with wave.open(str(_TERMINAL_BELL_WAV), "rb") as existing:
                    if (
                        existing.getnchannels() == 2
                        and existing.getsampwidth() == 2
                        and existing.getframerate() == 44100
                    ):
                        return _TERMINAL_BELL_WAV
            except Exception:
                pass
        _TERMINAL_BELL_WAV.parent.mkdir(parents=True, exist_ok=True)
        sample_rate = 44100
        duration_seconds = 0.11
        frequency_hz = 1080.0
        total_samples = max(1, int(sample_rate * duration_seconds))
        samples = array.array("h")
        attack = max(1, int(sample_rate * 0.006))
        release = max(1, int(sample_rate * 0.04))
        amplitude = 10000

        for index in range(total_samples):
            envelope = 1.0
            if index < attack:
                envelope = index / attack
            elif index > total_samples - release:
                envelope = max(0.0, (total_samples - index) / release)
            phase = (2.0 * math.pi * frequency_hz * index) / sample_rate
            value = int(math.sin(phase) * amplitude * envelope)
            # Stereo PCM improves compatibility with Qt multimedia backends.
            samples.append(value)
            samples.append(value)

        with wave.open(str(_TERMINAL_BELL_WAV), "wb") as stream:
            stream.setnchannels(2)
            stream.setsampwidth(2)
            stream.setframerate(sample_rate)
            stream.writeframes(samples.tobytes())
        return _TERMINAL_BELL_WAV
    except Exception:
        return None


@dataclass(slots=True)
class _WindowPlacement:
    geometry: QByteArray | None = None
    screen_name: str = ""
    screen_serial: str = ""
    frame_rect: QRect = field(default_factory=QRect)

    def has_data(self) -> bool:
        return bool(
            (self.geometry is not None and not self.geometry.isEmpty())
            or self.screen_name
            or self.screen_serial
            or self.frame_rect.isValid()
        )


def _copy_geometry_bytes(value: QByteArray | None) -> QByteArray | None:
    if value is None:
        return None
    try:
        copied = QByteArray(value)
    except Exception:
        return None
    return copied if not copied.isEmpty() else None


def _geometry_to_b64(value: QByteArray | None) -> str:
    geometry = _copy_geometry_bytes(value)
    if geometry is None:
        return ""
    try:
        return bytes(geometry.toBase64()).decode("ascii")
    except Exception:
        return ""


def _geometry_from_b64(encoded: str) -> QByteArray | None:
    cleaned = str(encoded).strip()
    if not cleaned:
        return None
    try:
        geometry = QByteArray.fromBase64(cleaned.encode("ascii"))
    except Exception:
        return None
    return geometry if not geometry.isEmpty() else None


def _screen_name(screen: object | None) -> str:
    if screen is None:
        return ""
    getter = getattr(screen, "name", None)
    if not callable(getter):
        return ""
    try:
        return str(getter()).strip()
    except Exception:
        return ""


def _screen_serial(screen: object | None) -> str:
    if screen is None:
        return ""
    getter = getattr(screen, "serialNumber", None)
    if not callable(getter):
        return ""
    try:
        return str(getter()).strip()
    except Exception:
        return ""


def _frame_rect_to_list(rect: QRect) -> list[int]:
    if not rect.isValid():
        return []
    return [int(rect.x()), int(rect.y()), int(rect.width()), int(rect.height())]


def _frame_rect_from_list(value: object) -> QRect:
    if not isinstance(value, list) or len(value) != 4:
        return QRect()
    try:
        x, y, width, height = (int(item) for item in value)
    except (TypeError, ValueError):
        return QRect()
    if width <= 0 or height <= 0:
        return QRect()
    return QRect(x, y, width, height)


def _window_screen(window: QWidget) -> object | None:
    handle_getter = getattr(window, "windowHandle", None)
    if callable(handle_getter):
        try:
            handle = handle_getter()
        except Exception:
            handle = None
        if handle is not None:
            handle_screen = getattr(handle, "screen", None)
            if callable(handle_screen):
                try:
                    screen = handle_screen()
                except Exception:
                    screen = None
                if screen is not None:
                    return screen
    screen_getter = getattr(window, "screen", None)
    if callable(screen_getter):
        try:
            screen = screen_getter()
        except Exception:
            screen = None
        if screen is not None:
            return screen
    try:
        frame = window.frameGeometry()
    except Exception:
        frame = QRect()
    if frame.isValid():
        try:
            screen = QApplication.screenAt(frame.center())
        except Exception:
            screen = None
        if screen is not None:
            return screen
    try:
        return QApplication.primaryScreen()
    except Exception:
        return None


def _capture_window_placement(
    window: QWidget,
    *,
    geometry_override: QByteArray | None = None,
    frame_rect_override: QRect | None = None,
    screen_override: object | None = None,
) -> _WindowPlacement:
    geometry = _copy_geometry_bytes(geometry_override)
    if geometry is None:
        save_geometry = getattr(window, "saveGeometry", None)
        if callable(save_geometry):
            try:
                geometry = _copy_geometry_bytes(save_geometry())
            except Exception:
                geometry = None
    frame_rect = QRect()
    if frame_rect_override is not None and frame_rect_override.isValid():
        frame_rect = QRect(frame_rect_override)
    else:
        try:
            current_frame = window.frameGeometry()
        except Exception:
            current_frame = QRect()
        if current_frame.isValid():
            frame_rect = QRect(current_frame)
    screen = screen_override
    if screen is None and frame_rect.isValid():
        try:
            screen = QApplication.screenAt(frame_rect.center())
        except Exception:
            screen = None
    if screen is None:
        screen = _window_screen(window)
    return _WindowPlacement(
        geometry=geometry,
        screen_name=_screen_name(screen),
        screen_serial=_screen_serial(screen),
        frame_rect=frame_rect,
    )


def _window_placement_from_payload(
    *,
    geometry_b64: str = "",
    screen_name: str = "",
    screen_serial: str = "",
    frame_rect: object = None,
) -> _WindowPlacement | None:
    placement = _WindowPlacement(
        geometry=_geometry_from_b64(geometry_b64),
        screen_name=str(screen_name).strip(),
        screen_serial=str(screen_serial).strip(),
        frame_rect=_frame_rect_from_list(frame_rect),
    )
    return placement if placement.has_data() else None


def _resolve_screen_for_window_placement(placement: _WindowPlacement) -> object | None:
    try:
        screens = list(QApplication.screens())
    except Exception:
        screens = []
    if placement.screen_serial:
        for screen in screens:
            if _screen_serial(screen) == placement.screen_serial:
                return screen
    if placement.screen_name:
        for screen in screens:
            if _screen_name(screen) == placement.screen_name:
                return screen
    if placement.frame_rect.isValid():
        try:
            screen = QApplication.screenAt(placement.frame_rect.center())
        except Exception:
            screen = None
        if screen is not None:
            return screen
    try:
        return QApplication.primaryScreen()
    except Exception:
        return None


def _window_is_maximized_or_fullscreen(window: QWidget) -> bool:
    try:
        if window.isMaximized() or window.isFullScreen():
            return True
    except Exception:
        pass
    state_getter = getattr(window, "windowState", None)
    if not callable(state_getter):
        return False
    try:
        state = state_getter()
    except Exception:
        return False
    state_value = getattr(state, "value", state)
    try:
        normalized = int(state_value)
    except (TypeError, ValueError):
        return False
    return bool(
        normalized
        & (
            int(Qt.WindowState.WindowMaximized.value)
            | int(Qt.WindowState.WindowFullScreen.value)
        )
    )


def _clamp_top_level_window_to_screen(window: QWidget, screen: object | None) -> None:
    if screen is None:
        return
    available_getter = getattr(screen, "availableGeometry", None)
    if not callable(available_getter):
        return
    try:
        available = available_getter()
    except Exception:
        return
    if not isinstance(available, QRect) or not available.isValid():
        return
    width_getter = getattr(window, "width", None)
    height_getter = getattr(window, "height", None)
    resize_getter = getattr(window, "resize", None)
    if callable(width_getter) and callable(height_getter) and callable(resize_getter):
        try:
            target_width = max(1, min(int(width_getter()), available.width()))
            target_height = max(1, min(int(height_getter()), available.height()))
            resize_getter(target_width, target_height)
        except Exception:
            pass
    try:
        frame = window.frameGeometry()
    except Exception:
        frame = QRect()
    if not frame.isValid():
        return
    max_x = available.right() - frame.width() + 1
    max_y = available.bottom() - frame.height() + 1
    target_x = min(max(frame.x(), available.x()), max_x)
    target_y = min(max(frame.y(), available.y()), max_y)
    if target_x == frame.x() and target_y == frame.y():
        return
    move_getter = getattr(window, "move", None)
    if callable(move_getter):
        try:
            move_getter(target_x, target_y)
        except Exception:
            pass


def _apply_window_placement(window: QWidget, placement: _WindowPlacement) -> bool:
    if not placement.has_data():
        return False
    handle_getter = getattr(window, "windowHandle", None)
    handle = handle_getter() if callable(handle_getter) else None
    if handle is None:
        return False
    screen = _resolve_screen_for_window_placement(placement)
    set_screen = getattr(handle, "setScreen", None)
    if screen is not None and callable(set_screen):
        try:
            set_screen(screen)
        except Exception:
            pass
    if placement.geometry is not None and not placement.geometry.isEmpty():
        try:
            window.restoreGeometry(placement.geometry)
        except Exception:
            return False
    elif placement.frame_rect.isValid():
        try:
            window.setGeometry(placement.frame_rect)
        except Exception:
            return False
    else:
        return False
    if not _window_is_maximized_or_fullscreen(window):
        _clamp_top_level_window_to_screen(window, screen)
    return True


def _restore_or_defer_window_placement(window: QWidget, placement: _WindowPlacement | None) -> bool:
    if placement is None or not placement.has_data():
        setattr(window, "_pending_window_placement", None)
        return False
    if _apply_window_placement(window, placement):
        setattr(window, "_pending_window_placement", None)
        return True
    setattr(window, "_pending_window_placement", placement)
    return False


def _apply_pending_window_placement(window: QWidget) -> bool:
    placement = getattr(window, "_pending_window_placement", None)
    if not isinstance(placement, _WindowPlacement):
        return False
    if _apply_window_placement(window, placement):
        setattr(window, "_pending_window_placement", None)
        return True
    return False


def _session_endpoint_text(session: Session) -> str:
    if session.protocol == Protocol.SERIAL:
        return session.host.strip() or "(not set)"
    host = session.host.strip() or "(not set)"
    port = session.port
    if session.protocol == Protocol.TELNET and (port <= 0 or port > 65535):
        port = SessionService.default_port_for(Protocol.TELNET)
    return f"{host}:{port}"


def _session_display_name(session: Session, *, fallback: str = "Session") -> str:
    name = session.name.strip()
    if name:
        return name
    host = session.host.strip()
    if host:
        return host
    protocol_name = session.protocol.value.upper()
    return protocol_name or fallback


@dataclass
class SSHProbeContext:
    session: Session
    password: str | None
    trust_unknown: bool
    x11_forwarding: bool
    tab: TerminalTab
    save_password_on_success: bool
    allow_password_save: bool


@dataclass
class StatusProgressOperation:
    key: str
    kind: str
    title: str
    total_steps: int
    completed_steps: int = 0
    cancelable: bool = False
    canceled: bool = False


class StatusProgressWidget(QWidget):
    cancel_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("statusProgressWidget")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.message_label = QLabel("")
        self.message_label.setObjectName("statusProgressMessage")
        self.message_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.message_label.setWordWrap(False)
        layout.addWidget(self.message_label, 1)

        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("statusProgressBar")
        self.progress_bar.setMinimumWidth(165)
        self.progress_bar.setTextVisible(True)
        layout.addWidget(self.progress_bar, 0)

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("statusProgressCancelButton")
        self.cancel_button.clicked.connect(self.cancel_requested)
        layout.addWidget(self.cancel_button, 0)
        self.hide()

    def configure(self, *, message: str, completed_steps: int, total_steps: int, cancelable: bool) -> None:
        total = max(1, int(total_steps))
        self.message_label.setText(message)
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(max(0, min(int(completed_steps), total)))
        self.progress_bar.setFormat("%v / %m")
        self.cancel_button.setVisible(cancelable)
        self.setVisible(True)

    def update_message(self, message: str) -> None:
        self.message_label.setText(message)


class ToolLauncherSyncWorker(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, selected_tool_keys: list[str]) -> None:
        super().__init__()
        self._selected_tool_keys = list(selected_tool_keys)

    @Slot()
    def run(self) -> None:
        try:
            self.succeeded.emit(sync_tool_launchers(self._selected_tool_keys))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()


_IMMEDIATE_TERMINAL_QUERY_SEQUENCES = (
    "\x1b[c",
    "\x1b[0c",
    "\x1b[5n",
    "\x1b[6n",
    "\x9bc",
    "\x9b0c",
    "\x9b5n",
    "\x9b6n",
)
_IMMEDIATE_TERMINAL_QUERY_PREFIXES = frozenset(
    sequence[:index]
    for sequence in _IMMEDIATE_TERMINAL_QUERY_SEQUENCES
    for index in range(1, len(sequence))
)
_IMMEDIATE_TERMINAL_QUERY_PROBE_TAIL_MAX = max(
    len(prefix) for prefix in _IMMEDIATE_TERMINAL_QUERY_PREFIXES
)


def _terminal_device_attributes_reply(terminal_type: str) -> str:
    normalized = normalize_serial_terminal_type(terminal_type)
    if normalized in {"vt100", "ansi"}:
        return "\x1b[?1;0c"
    if normalized in {"xterm", "xterm-256color"}:
        return "\x1b[?62;1;2;6;7;8;9;15;18;21;22c"
    return "\x1b[?6c"


class TerminalTab(QWidget):
    _RENDER_INTERVAL_MS = 8
    _SCROLLBACK_DIALOG_REFRESH_INTERVAL_MS = 150
    open_sftp_requested = Signal(str)
    disconnect_requested = Signal(object)
    start_logging_requested = Signal(object)
    stop_logging_requested = Signal(object)
    logging_error = Signal(object, str)
    _OUTPUT_DRAIN_INTERVAL_MS = 4
    _OUTPUT_DRAIN_BURST_INTERVAL_MS = 6
    _OUTPUT_DRAIN_MAX_CHARS = 16 * 1024
    _OUTPUT_DRAIN_MAX_CHUNKS = 8
    _OUTPUT_DRAIN_BURST_MAX_CHARS = 64 * 1024
    _OUTPUT_DRAIN_BURST_MAX_CHUNKS = 24
    _OUTPUT_DRAIN_BURST_THRESHOLD_CHARS = 64 * 1024
    _OUTPUT_DRAIN_TIME_BUDGET_MS = 2.0
    _OUTPUT_DRAIN_BURST_TIME_BUDGET_MS = 3.0
    _OUTPUT_QUEUE_SLICE_CHARS = 4096
    _OUTPUT_BACKLOG_THROTTLE_HIGH_CHARS = 2 * 1024 * 1024
    _OUTPUT_BACKLOG_THROTTLE_LOW_CHARS = 512 * 1024
    _BELL_DEBOUNCE_SECONDS = 0.12
    _BELL_SOUND_VOLUME = 0.6
    _BELL_SOUND_LOAD_RETRY_INTERVAL_MS = 50
    _BELL_SOUND_LOAD_RETRY_ATTEMPTS = 12
    _BELL_PARSE_NORMAL = 0
    _BELL_PARSE_ESC = 1
    _BELL_PARSE_OSC = 2
    _BELL_PARSE_OSC_ESC = 3
    _SHUTDOWN_WAIT_SECONDS = 4.0
    _SHUTDOWN_WAIT_SLICE_MS = 250
    _OSC_RE = re.compile(r"\x1B\].*?(?:\x07|\x1B\\)", re.DOTALL)
    _CSI_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
    _SINGLE_ESC_RE = re.compile(r"\x1B[@-Z\\-_]")

    def __init__(self, settings: AppSettings, parent=None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._worker: SSHShellWorker | LocalShellWorker | WindowsLocalShellWorker | TelnetShellWorker | SerialShellWorker | None = None
        self._thread: QThread | None = None
        self._local_process: QProcess | None = None
        self._session_id: str | None = None
        self._last_synced_size: tuple[int, int] | None = None
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.timeout.connect(self._render)
        self._pending_dirty_rows: set[int] = set()
        self._pending_full_repaint = True
        self._pending_scroll_operations: list[_TerminalScrollOperation] = []
        self._output_drain_timer = QTimer(self)
        self._output_drain_timer.setSingleShot(True)
        self._output_drain_timer.timeout.connect(self._drain_pending_output)
        self._pending_output_chunks: deque[str] = deque()
        self._pending_output_chars = 0
        self._output_throttled = False
        self._local_process_output_paused = False
        self._last_bell_time = 0.0
        self._bell_parse_state = self._BELL_PARSE_NORMAL
        self._terminal_query_probe_tail = ""
        self._bell_sound_effect = None
        self._bell_sound_effect_initialized = False
        self._pending_bell_sound_effect_play = False
        self._bell_sound_retry_attempts_remaining = 0
        self._bell_sound_retry_timer = QTimer(self)
        self._bell_sound_retry_timer.setSingleShot(True)
        self._bell_sound_retry_timer.timeout.connect(self._retry_pending_bell_sound)
        scrollback_limit = max(100, settings.terminal_scrollback_lines)
        self._emulator = VT100Emulator(
            cols=160,
            rows=48,
            history=scrollback_limit,
            process_input_writer=self._send_emulator_generated_input,
        )
        self._scrollback_store = TerminalScrollbackStore(
            max_lines=scrollback_limit,
            line_source=self._emulator.rendered_scrollback_lines,
        )
        self._log_stream: TextIO | None = None
        self._log_file_path: str | None = None
        self._log_current_line = ""
        self._log_pending_cr = False
        self._automation_enabled = False
        self._automation_steps: list[SSHAutomationStep] = []
        self._automation_running = False
        self._automation_step_index = 0
        self._automation_waiting_expect: SSHAutomationStep | None = None
        self._automation_expect_buffer = ""
        self._automation_step_timer = QTimer(self)
        self._automation_step_timer.setSingleShot(True)
        self._automation_step_timer.timeout.connect(self._run_next_automation_step)
        self._automation_expect_timer = QTimer(self)
        self._automation_expect_timer.setSingleShot(True)
        self._automation_expect_timer.timeout.connect(self._on_automation_expect_timeout)
        self._backspace_prefers_ctrl_h_override: bool | None = None
        self._windows_local_process_backspace_mode = False
        self._windows_local_shell_backend = ""
        self._windows_local_shell_connected = False
        self._windows_local_shell_fallback_attempted = False
        self._windows_local_shell_start_program = ""
        self._windows_local_shell_start_arguments: list[str] = []
        self._windows_local_shell_start_working_directory: str | None = None
        self._windows_local_shell_startup_error = ""
        self._suppress_local_worker_closed_message = False
        self._suppress_next_local_process_error_message = False
        self._backspace_probe_expected = ""
        self._backspace_probe_deadline = 0.0
        self._backspace_probe_buffer = ""
        self._scrollback_dialog: TerminalScrollbackDialog | None = None
        self._scrollback_dialog_refresh_timer = QTimer(self)
        self._scrollback_dialog_refresh_timer.setSingleShot(True)
        self._scrollback_dialog_refresh_timer.setInterval(self._SCROLLBACK_DIALOG_REFRESH_INTERVAL_MS)
        self._scrollback_dialog_refresh_timer.timeout.connect(self._flush_scrollback_dialog_refresh)
        self._scrollback_reopen_block_until = 0.0
        self._shell_banner_text = ""
        self._shell_banner_color = QColor("#f59e0b")
        self._shell_banner_blink_visible = True
        self._shell_banner_blink_timer = QTimer(self)
        self._shell_banner_blink_timer.setInterval(550)
        self._shell_banner_blink_timer.timeout.connect(self._toggle_shell_banner_visibility)
        self.setProperty("terminal_session_closed", False)

        layout = QVBoxLayout(self)
        self._shell_banner_label = QLabel("")
        self._shell_banner_label.setWordWrap(True)
        self._shell_banner_label.setAlignment(Qt.AlignCenter)
        self._shell_banner_label.setTextFormat(Qt.PlainText)
        self._shell_banner_label.hide()
        layout.addWidget(self._shell_banner_label)
        self.output = TerminalView(settings=settings, emulator=self._emulator)
        layout.addWidget(self.output)
        self._output_throttle_banner = QFrame(self)
        self._output_throttle_banner.setObjectName("terminalOutputThrottleBanner")
        self._output_throttle_banner.setAutoFillBackground(False)
        self._output_throttle_banner.setStyleSheet(
            "QFrame#terminalOutputThrottleBanner {"
            "background-color: rgba(245, 158, 11, 34);"
            "border: 1px solid #f59e0b;"
            "border-radius: 6px;"
            "}"
            "QFrame#terminalOutputThrottleBanner QLabel { background: transparent; color: #fef3c7; }"
        )
        throttle_layout = QHBoxLayout(self._output_throttle_banner)
        throttle_layout.setContentsMargins(8, 5, 8, 5)
        throttle_layout.setSpacing(8)
        self._output_throttle_label = QLabel("", self._output_throttle_banner)
        self._output_throttle_label.setTextFormat(Qt.PlainText)
        self._output_throttle_label.setWordWrap(True)
        throttle_layout.addWidget(self._output_throttle_label, 1)
        self._output_throttle_interrupt_btn = QPushButton("Send Ctrl-C", self._output_throttle_banner)
        self._output_throttle_interrupt_btn.setToolTip("Send Ctrl-C to interrupt the foreground command")
        throttle_layout.addWidget(self._output_throttle_interrupt_btn, 0)
        self._output_throttle_interrupt_btn.clicked.connect(self._send_output_throttle_interrupt)
        self._output_throttle_banner.hide()
        layout.addWidget(self._output_throttle_banner)
        self.output.data_input.connect(self._send_terminal_input)
        self.output.paste_requested.connect(self._handle_terminal_paste)
        self.output.open_sftp_requested.connect(self._request_open_sftp)
        self.output.disconnect_requested.connect(self._request_disconnect)
        self.output.start_logging_requested.connect(self._request_start_logging)
        self.output.stop_logging_requested.connect(self._request_stop_logging)
        self.output.terminal_resized.connect(self._resize_terminal)
        self.output.set_logging_active(False)
        self.output.set_backspace_prefers_ctrl_h_override(None)
        self._emulator.consume_dirty_rows()
        self._render()

    def set_open_sftp_supported(self, supported: bool) -> None:
        self.output.set_open_sftp_supported(supported)

    def set_shell_banner(self, *, message: str, color: str, blink: bool) -> None:
        normalized = message.replace("\r\n", "\n").replace("\r", "\n").strip()
        self._shell_banner_text = normalized
        self._shell_banner_blink_timer.stop()
        self._shell_banner_blink_visible = True
        if not normalized:
            self._shell_banner_label.clear()
            self._shell_banner_label.hide()
            return

        banner_color = QColor(color.strip())
        if not banner_color.isValid():
            banner_color = QColor("#f59e0b")
        self._shell_banner_color = banner_color
        self._shell_banner_label.setText(normalized)
        self._shell_banner_label.show()
        self._apply_shell_banner_style(text_visible=True)
        if blink:
            self._shell_banner_blink_timer.start()

    def append(self, text: str) -> None:
        cursor_rows: set[int] = set()
        before_cursor_row = self._emulator.cursor_row()
        if before_cursor_row is not None:
            cursor_rows.add(before_cursor_row)
        normalized_text = self._emulator.feed(text)
        dirty_rows = self._emulator.consume_dirty_rows()
        after_cursor_row = self._emulator.cursor_row()
        if after_cursor_row is not None:
            cursor_rows.add(after_cursor_row)
        dirty_rows.update(cursor_rows)
        scroll_operations, structural_damage = self._emulator.consume_render_damage()
        self._write_log_chunk(normalized_text)
        self._schedule_scrollback_dialog_refresh()
        self.output.sync_history_scrollbar()
        self._schedule_render(
            dirty_rows=dirty_rows,
            full=structural_damage,
            scroll_operations=scroll_operations,
        )

    def _append_local_status(self, text: str) -> None:
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        if not normalized.endswith("\n"):
            normalized += "\n"
        payload = normalized.replace("\n", "\r\n")
        if normalized.lstrip().startswith("[Automation]"):
            payload = f"\x1b[1m{payload}\x1b[0m"
        self.append(payload)

    def _render(self) -> None:
        if not (
            self._pending_full_repaint
            or self._pending_dirty_rows
            or self._pending_scroll_operations
        ):
            return
        if self._pending_full_repaint or self._render_mode() == "full_frame":
            self.output.request_full_repaint()
        else:
            dirty_rows = set(self._pending_dirty_rows)
            if self._pending_scroll_operations:
                dirty_rows.update(self.output.apply_scroll_operations(tuple(self._pending_scroll_operations)))
            if dirty_rows:
                self.output.request_repaint_for_rows(dirty_rows)
        self._pending_dirty_rows.clear()
        self._pending_full_repaint = False
        self._pending_scroll_operations.clear()

    @staticmethod
    def _render_mode() -> str:
        raw_mode = os.getenv(TERMINAL_RENDER_MODE_ENV, "").strip().lower()
        if raw_mode == "full_frame":
            return raw_mode
        return "damage_aware"

    def _schedule_render(
        self,
        *,
        dirty_rows: set[int] | None = None,
        full: bool = False,
        scroll_operations: tuple[_TerminalScrollOperation, ...] = (),
    ) -> None:
        if full:
            self._pending_full_repaint = True
            self._pending_dirty_rows.clear()
            self._pending_scroll_operations.clear()
        elif dirty_rows and not self._pending_full_repaint:
            self._pending_dirty_rows.update(row for row in dirty_rows if row >= 0)
        if scroll_operations and not self._pending_full_repaint:
            self._pending_scroll_operations.extend(scroll_operations)
        if full or dirty_rows or scroll_operations:
            # Scroll-driven updates need to land on the very next event-loop
            # turn or stale content becomes visible. Plain text updates can
            # still coalesce slightly. Under sustained output backlog, favor
            # coalescing over immediate scroll paints to avoid animation
            # workloads spending most of their time in repaint churn.
            if not self._render_timer.isActive():
                immediate_scroll_render = bool(scroll_operations) and not self._has_output_backlog()
                self._render_timer.start(0 if immediate_scroll_render else self._RENDER_INTERVAL_MS)

    def _has_output_backlog(self) -> bool:
        return (
            self._pending_output_chars > 0
            or bool(self._pending_output_chunks)
            or self._output_drain_timer.isActive()
        )

    def _reset_terminal_query_probe_tail(self) -> None:
        self._terminal_query_probe_tail = ""

    def _extract_terminal_query_probe_tail(self, text: str) -> str:
        max_length = min(len(text), _IMMEDIATE_TERMINAL_QUERY_PROBE_TAIL_MAX)
        for length in range(max_length, 0, -1):
            candidate = text[-length:]
            if candidate in _IMMEDIATE_TERMINAL_QUERY_PREFIXES:
                return candidate
        return ""

    def _requires_immediate_terminal_output(self, text: str) -> bool:
        combined = f"{self._terminal_query_probe_tail}{text}" if self._terminal_query_probe_tail else text
        self._terminal_query_probe_tail = self._extract_terminal_query_probe_tail(combined)
        return bool(self._terminal_query_probe_tail) or any(
            sequence in combined for sequence in _IMMEDIATE_TERMINAL_QUERY_SEQUENCES
        )

    def _queue_terminal_output(self, text: str) -> None:
        if not text:
            return
        self._ring_terminal_bell(text)
        if self._requires_immediate_terminal_output(text):
            if self._pending_output_chunks:
                self._drain_pending_output(drain_all=True)
            self.append(text)
            return
        for start in range(0, len(text), self._OUTPUT_QUEUE_SLICE_CHARS):
            chunk = text[start : start + self._OUTPUT_QUEUE_SLICE_CHARS]
            self._pending_output_chunks.append(chunk)
            self._pending_output_chars += len(chunk)
        self._update_output_throttle_state()
        if not self._output_drain_timer.isActive():
            self._output_drain_timer.start(0)

    def _update_output_throttle_state(self) -> None:
        if (
            not self._output_throttled
            and self._pending_output_chars >= self._OUTPUT_BACKLOG_THROTTLE_HIGH_CHARS
        ):
            self._output_throttled = True
            self._set_output_reader_paused(True)
        elif (
            self._output_throttled
            and self._pending_output_chars <= self._OUTPUT_BACKLOG_THROTTLE_LOW_CHARS
        ):
            self._output_throttled = False
            self._set_output_reader_paused(False)
        self._update_output_throttle_banner()

    def _set_output_reader_paused(self, paused: bool) -> None:
        worker = self._worker
        if worker is not None:
            method_name = "pause_output" if paused else "resume_output"
            method = getattr(worker, method_name, None)
            if callable(method):
                try:
                    method()
                except Exception:
                    pass
        process = self._local_process
        if process is not None:
            self._local_process_output_paused = paused
            if not paused and process.state() == QProcess.Running:
                QTimer.singleShot(0, self._on_local_process_output)

    @staticmethod
    def _format_output_backlog_size(char_count: int) -> str:
        size = float(max(0, char_count))
        for unit in ("B", "KiB", "MiB", "GiB"):
            if size < 1024.0 or unit == "GiB":
                if unit == "B":
                    return f"{int(size)} {unit}"
                return f"{size:.1f} {unit}"
            size /= 1024.0
        return f"{size:.1f} GiB"

    def _update_output_throttle_banner(self) -> None:
        if not self._output_throttled:
            self._output_throttle_banner.hide()
            return
        backlog = self._format_output_backlog_size(self._pending_output_chars)
        self._output_throttle_label.setText(
            f"Terminal output is throttled while SnakeSh catches up ({backlog} queued)."
        )
        self._output_throttle_banner.show()

    @Slot()
    def _send_output_throttle_interrupt(self) -> None:
        if self.execute_raw_input("\x03"):
            self.output.show_center_message(
                "Command is being terminated...",
                TerminalView.CENTER_MESSAGE_DURATION_MS,
            )

    def _ring_terminal_bell(self, text: str) -> None:
        if not self._has_actionable_bell(text):
            return
        if not self._settings.terminal_bell_enabled and not self._settings.terminal_visual_bell_enabled:
            return
        now = time.monotonic()
        if (now - self._last_bell_time) < self._BELL_DEBOUNCE_SECONDS:
            return
        self._last_bell_time = now

        if self._settings.terminal_visual_bell_enabled:
            self.output.flash_visual_bell()

        if not self._settings.terminal_bell_enabled:
            return

        if self._play_bell_sound_effect():
            return

        self._fallback_app_beep()

    def _has_actionable_bell(self, text: str) -> bool:
        if not text:
            return False
        state = self._bell_parse_state
        for ch in text:
            if state == self._BELL_PARSE_NORMAL:
                if ch == "\x1b":
                    state = self._BELL_PARSE_ESC
                    continue
                if ch == "\x07":
                    self._bell_parse_state = state
                    return True
                continue

            if state == self._BELL_PARSE_ESC:
                if ch == "]":
                    state = self._BELL_PARSE_OSC
                    continue
                state = self._BELL_PARSE_NORMAL
                if ch == "\x1b":
                    state = self._BELL_PARSE_ESC
                    continue
                if ch == "\x07":
                    self._bell_parse_state = state
                    return True
                continue

            if state == self._BELL_PARSE_OSC:
                if ch == "\x07":
                    state = self._BELL_PARSE_NORMAL
                    continue
                if ch == "\x1b":
                    state = self._BELL_PARSE_OSC_ESC
                    continue
                continue

            if state == self._BELL_PARSE_OSC_ESC:
                if ch == "\\" or ch == "\x07":
                    state = self._BELL_PARSE_NORMAL
                    continue
                if ch != "\x1b":
                    state = self._BELL_PARSE_OSC
                continue

        self._bell_parse_state = state
        return False

    def _play_bell_sound_effect(self) -> bool:
        effect = self._get_or_create_bell_sound_effect()
        if effect is None:
            return False
        if self._try_play_bell_sound_effect(effect):
            return True
        self._queue_bell_sound_retry()
        return True

    def _try_play_bell_sound_effect(self, effect) -> bool:
        try:
            is_loaded = getattr(effect, "isLoaded", None)
            loaded = bool(is_loaded()) if callable(is_loaded) else True
            if not loaded:
                return False
            effect.stop()
            effect.play()
            self._pending_bell_sound_effect_play = False
            self._bell_sound_retry_attempts_remaining = 0
            return True
        except Exception:
            return False

    def _queue_bell_sound_retry(self) -> None:
        self._pending_bell_sound_effect_play = True
        self._bell_sound_retry_attempts_remaining = self._BELL_SOUND_LOAD_RETRY_ATTEMPTS
        if not self._bell_sound_retry_timer.isActive():
            self._bell_sound_retry_timer.start(self._BELL_SOUND_LOAD_RETRY_INTERVAL_MS)

    def _retry_pending_bell_sound(self) -> None:
        if not self._pending_bell_sound_effect_play:
            return
        effect = self._get_or_create_bell_sound_effect()
        if effect is None:
            self._pending_bell_sound_effect_play = False
            self._fallback_app_beep()
            return
        if self._try_play_bell_sound_effect(effect):
            return
        self._bell_sound_retry_attempts_remaining = max(0, self._bell_sound_retry_attempts_remaining - 1)
        if self._bell_sound_retry_attempts_remaining > 0:
            self._bell_sound_retry_timer.start(self._BELL_SOUND_LOAD_RETRY_INTERVAL_MS)
            return
        self._pending_bell_sound_effect_play = False
        self._fallback_app_beep()

    @staticmethod
    def _fallback_app_beep() -> None:
        app = QApplication.instance()
        if app is None:
            return
        try:
            app.beep()
        except Exception:
            return

    @classmethod
    def _create_bell_sound_effect(cls):
        if QSoundEffect is None:
            return None
        app = QApplication.instance()
        if app is None:
            return None
        sound_path = _ensure_terminal_bell_wav()
        if sound_path is None:
            return None
        try:
            effect = QSoundEffect(app)
            effect.setLoopCount(1)
            effect.setVolume(cls._BELL_SOUND_VOLUME)
            effect.setSource(QUrl.fromLocalFile(str(sound_path)))
            return effect
        except Exception:
            return None

    def _get_or_create_bell_sound_effect(self):
        if self._bell_sound_effect_initialized:
            return self._bell_sound_effect
        self._bell_sound_effect_initialized = True
        self._bell_sound_effect = self._create_bell_sound_effect()
        return self._bell_sound_effect

    def _drain_pending_output(self, *, drain_all: bool = False) -> None:
        if not self._pending_output_chunks:
            return
        burst_mode = self._pending_output_chars >= self._OUTPUT_DRAIN_BURST_THRESHOLD_CHARS
        max_chars = self._OUTPUT_DRAIN_BURST_MAX_CHARS if burst_mode else self._OUTPUT_DRAIN_MAX_CHARS
        max_chunks = self._OUTPUT_DRAIN_BURST_MAX_CHUNKS if burst_mode else self._OUTPUT_DRAIN_MAX_CHUNKS
        time_budget_ms = (
            self._OUTPUT_DRAIN_BURST_TIME_BUDGET_MS if burst_mode else self._OUTPUT_DRAIN_TIME_BUDGET_MS
        )
        deadline = None if drain_all else (time.perf_counter() + (time_budget_ms / 1000.0))
        drained_chars = 0
        drained_chunks = 0
        chunks: list[str] = []
        while self._pending_output_chunks:
            if not drain_all and deadline is not None and drained_chunks > 0 and time.perf_counter() >= deadline:
                break
            text = self._pending_output_chunks.popleft()
            text_len = len(text)
            self._pending_output_chars = max(0, self._pending_output_chars - text_len)
            chunks.append(text)
            drained_chars += text_len
            drained_chunks += 1
            if not drain_all and (
                drained_chars >= max_chars
                or drained_chunks >= max_chunks
            ):
                break
        if chunks:
            self.append("".join(chunks))
        self._update_output_throttle_state()
        if self._pending_output_chunks and not drain_all and not self._output_drain_timer.isActive():
            self._output_drain_timer.start(0)

    def start_shell(
        self,
        session: Session,
        password: str | None,
        trust_unknown: bool,
        x11_forwarding: bool | None = None,
    ) -> None:
        if self._thread or self._local_process:
            return
        self._mark_connection_closed(False)
        self._bell_parse_state = self._BELL_PARSE_NORMAL
        self._reset_terminal_query_probe_tail()
        self._reset_backspace_adaptation()
        self._emulator.set_terminal_type("auto")
        self._session_id = session.id
        self._configure_automation(session)
        self._thread = QThread(self)
        cols, rows = self.output.terminal_size()
        self._worker = SSHShellWorker(
            session=session,
            password=password,
            trust_unknown=trust_unknown,
            x11_forwarding=session.x11_forwarding if x11_forwarding is None else x11_forwarding,
            cols=cols,
            rows=rows,
        )
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.start)
        self._worker.output.connect(self._on_worker_output, Qt.QueuedConnection)
        self._worker.error.connect(self._on_worker_error, Qt.QueuedConnection)
        self._worker.connected.connect(self._on_worker_connected, Qt.QueuedConnection)
        self._worker.closed.connect(self._on_worker_closed, Qt.QueuedConnection)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._on_thread_finished, Qt.QueuedConnection)
        self._thread.start()
        # Ensure remote PTY matches final rendered widget size after layout settles.
        QTimer.singleShot(0, self._sync_terminal_size)
        QTimer.singleShot(150, self._sync_terminal_size)

    def start_local_shell(
        self,
        *,
        program: str,
        arguments: list[str] | None = None,
        working_directory: str | None = None,
    ) -> bool:
        if self._thread or self._local_process:
            return False
        if os.name == "nt" and _is_windows_external_terminal_host(program):
            self._append_local_status(
                "ERROR: Local Shell cannot launch an external Windows terminal host. Use an embedded shell such as pwsh.exe, powershell.exe, or cmd.exe."
            )
            self._mark_connection_closed(True)
            return False
        if os.name == "nt" and _is_windows_app_execution_alias(program):
            self._append_local_status(
                "ERROR: Local Shell cannot use a Windows App Execution Alias. Use an actual shell executable such as powershell.exe or cmd.exe."
            )
            self._mark_connection_closed(True)
            return False
        self._mark_connection_closed(False)
        self._bell_parse_state = self._BELL_PARSE_NORMAL
        self._reset_terminal_query_probe_tail()
        self._reset_backspace_adaptation()
        self._emulator.set_terminal_type("auto")
        self._stop_automation()
        self._automation_enabled = False
        self._automation_steps = []
        self._automation_step_index = 0
        self._session_id = None
        self._windows_local_shell_backend = ""
        self._windows_local_shell_connected = False
        self._windows_local_shell_fallback_attempted = False
        self._windows_local_shell_start_program = program
        self._windows_local_shell_start_arguments = list(arguments or [])
        self._windows_local_shell_start_working_directory = working_directory
        self._windows_local_shell_startup_error = ""
        self._suppress_local_worker_closed_message = False
        self._suppress_next_local_process_error_message = False
        self.set_shell_banner(message="", color="", blink=False)
        if os.name == "posix":
            return self._start_local_shell_worker(
                program=program,
                arguments=arguments or [],
                working_directory=working_directory,
            )
        if os.name == "nt":
            if WindowsLocalShellWorker.is_supported():
                return self._start_windows_local_shell_worker(
                    program=program,
                    arguments=arguments or [],
                    working_directory=working_directory,
                )
            # Never fall back to a Windows backend that can attach a visible
            # console host outside the SnakeSh workspace.
            self.set_shell_banner(
                message=(
                    "Windows embedded compatibility mode active.\n"
                    "ConPTY is unavailable, so SnakeSh started this shell with the hidden pipe backend."
                ),
                color="#f59e0b",
                blink=False,
            )
            self._append_local_status(
                "WARNING: Windows ConPTY is unavailable. Starting the local shell with the embedded compatibility backend."
            )
            return self._start_windows_local_shell_worker(
                program=program,
                arguments=arguments or [],
                working_directory=working_directory,
                backend_name="hidden-process",
                backend_factory=_WindowsHiddenProcessBackend,
            )
        return self._start_local_shell_process_fallback(
            program=program,
            arguments=arguments or [],
            working_directory=working_directory,
        )

    def start_telnet(self, session: Session) -> None:
        if self._thread or self._local_process:
            return
        self._mark_connection_closed(False)
        self._bell_parse_state = self._BELL_PARSE_NORMAL
        self._reset_terminal_query_probe_tail()
        self._reset_backspace_adaptation()
        self._emulator.set_terminal_type("auto")
        self._configure_automation(session)
        self._session_id = session.id
        self._thread = QThread(self)
        cols, rows = self.output.terminal_size()
        self._worker = TelnetShellWorker(
            host=session.host,
            port=session.port,
            terminal_type=session.telnet_terminal_type,
            connect_timeout_seconds=session.telnet_connect_timeout_seconds,
            use_tls=session.telnet_use_tls,
            tls_verify=session.telnet_tls_verify,
            cols=cols,
            rows=rows,
        )
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.start)
        self._worker.output.connect(self._on_worker_output, Qt.QueuedConnection)
        self._worker.error.connect(self._on_worker_error, Qt.QueuedConnection)
        self._worker.connected.connect(self._on_worker_connected, Qt.QueuedConnection)
        self._worker.closed.connect(self._on_worker_closed, Qt.QueuedConnection)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._on_thread_finished, Qt.QueuedConnection)
        self._thread.start()
        QTimer.singleShot(0, self._sync_terminal_size)
        QTimer.singleShot(150, self._sync_terminal_size)

    def start_serial(self, session: Session) -> None:
        if self._thread or self._local_process:
            return
        self._mark_connection_closed(False)
        self._bell_parse_state = self._BELL_PARSE_NORMAL
        self._reset_terminal_query_probe_tail()
        self._reset_backspace_adaptation()
        self._emulator.set_terminal_type(session.serial_terminal_type)
        self._configure_automation(session)
        self._session_id = session.id
        self._thread = QThread(self)
        cols, rows = self.output.terminal_size()
        self._worker = SerialShellWorker(
            port=session.host,
            baud_rate=session.serial_baud_rate,
            data_bits=session.serial_data_bits,
            parity=session.serial_parity,
            stop_bits=session.serial_stop_bits,
            flow_control=session.serial_flow_control,
            cols=cols,
            rows=rows,
        )
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.start)
        self._worker.output.connect(self._on_worker_output, Qt.QueuedConnection)
        self._worker.error.connect(self._on_worker_error, Qt.QueuedConnection)
        self._worker.connected.connect(self._on_worker_connected, Qt.QueuedConnection)
        self._worker.closed.connect(self._on_worker_closed, Qt.QueuedConnection)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._on_thread_finished, Qt.QueuedConnection)
        self._thread.start()
        QTimer.singleShot(0, self._sync_terminal_size)
        QTimer.singleShot(150, self._sync_terminal_size)

    def _start_local_shell_worker(
        self,
        *,
        program: str,
        arguments: list[str],
        working_directory: str | None,
    ) -> bool:
        self._windows_local_shell_backend = ""
        self._thread = QThread(self)
        cols, rows = self.output.terminal_size()
        self._worker = LocalShellWorker(
            program=program,
            arguments=arguments,
            working_directory=working_directory,
            cols=cols,
            rows=rows,
        )
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.start)
        self._worker.output.connect(self._on_local_worker_output, Qt.QueuedConnection)
        self._worker.error.connect(self._on_local_worker_error, Qt.QueuedConnection)
        self._worker.connected.connect(self._on_local_worker_connected, Qt.QueuedConnection)
        self._worker.closed.connect(self._on_local_worker_closed, Qt.QueuedConnection)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._on_thread_finished, Qt.QueuedConnection)
        self._thread.start()
        # Keep local PTY dimensions in sync with the fully-laid-out terminal viewport.
        QTimer.singleShot(0, self._sync_terminal_size)
        QTimer.singleShot(150, self._sync_terminal_size)
        return True

    def _start_local_shell_process_fallback(
        self,
        *,
        program: str,
        arguments: list[str],
        working_directory: str | None,
    ) -> bool:
        started, _message = self._start_local_shell_process_fallback_internal(
            program=program,
            arguments=arguments,
            working_directory=working_directory,
            report_failure=True,
        )
        return started

    def _start_local_shell_process_fallback_internal(
        self,
        *,
        program: str,
        arguments: list[str],
        working_directory: str | None,
        report_failure: bool,
    ) -> tuple[bool, str]:
        self._windows_local_shell_backend = "process" if os.name == "nt" else ""
        self._windows_local_process_backspace_mode = platform.system().lower() == "windows"
        process = QProcess(self)
        process.setProcessChannelMode(QProcess.MergedChannels)
        if working_directory:
            process.setWorkingDirectory(working_directory)
        process.readyReadStandardOutput.connect(self._on_local_process_output)
        process.started.connect(self._on_local_process_started)
        process.finished.connect(self._on_local_process_finished)
        process.errorOccurred.connect(self._on_local_process_error)
        self._local_process = process
        process.start(program, arguments)
        if process.state() == QProcess.NotRunning and process.error() == QProcess.FailedToStart:
            self._windows_local_process_backspace_mode = False
            self._local_process = None
            process.deleteLater()
            self._suppress_next_local_process_error_message = not report_failure
            message = f"Local shell failed to start ({program})."
            if report_failure:
                self._append_local_status(f"ERROR: {message}")
            return False, message
        return True, ""

    def _start_windows_local_shell_worker(
        self,
        *,
        program: str,
        arguments: list[str],
        working_directory: str | None,
        backend_name: str = "conpty",
        backend_factory: Callable[..., _WindowsTerminalBackend] | None = None,
    ) -> bool:
        self._windows_local_shell_backend = backend_name
        self._windows_local_shell_connected = False
        self._windows_local_process_backspace_mode = backend_name == "hidden-process"
        self._thread = QThread(self)
        cols, rows = self.output.terminal_size()
        self._worker = WindowsLocalShellWorker(
            program=program,
            arguments=arguments,
            working_directory=working_directory,
            cols=cols,
            rows=rows,
            uses_basic_process_io=backend_name == "hidden-process",
            backend_factory=backend_factory,
        )
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.start)
        self._worker.output.connect(self._on_local_worker_output, Qt.QueuedConnection)
        self._worker.error.connect(self._on_local_worker_error, Qt.QueuedConnection)
        self._worker.connected.connect(self._on_local_worker_connected, Qt.QueuedConnection)
        self._worker.closed.connect(self._on_local_worker_closed, Qt.QueuedConnection)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._on_thread_finished, Qt.QueuedConnection)
        self._thread.start()
        QTimer.singleShot(0, self._sync_terminal_size)
        QTimer.singleShot(150, self._sync_terminal_size)
        return True

    def _attempt_windows_local_shell_embedded_fallback(self, startup_error: str) -> bool:
        if self._windows_local_shell_backend != "conpty" or self._windows_local_shell_connected:
            return False
        if self._windows_local_shell_fallback_attempted:
            return False
        self._windows_local_shell_fallback_attempted = True
        self._windows_local_shell_startup_error = startup_error
        self._append_local_status(
            "WARNING: Advanced Windows console startup failed. Retrying with the embedded compatibility backend..."
        )
        self._append_local_status(f"WARNING: {startup_error}")
        self._suppress_local_worker_closed_message = True
        worker = self._worker
        thread = self._thread
        self._worker = None
        self._thread = None
        if worker is not None:
            try:
                worker.stop()
            except Exception:
                pass
        if thread is not None:
            try:
                thread.quit()
            except RuntimeError:
                pass
            try:
                thread.wait(1500)
            except RuntimeError:
                pass
        started = self._start_windows_local_shell_worker(
            program=self._windows_local_shell_start_program,
            arguments=list(self._windows_local_shell_start_arguments),
            working_directory=self._windows_local_shell_start_working_directory,
            backend_name="hidden-process",
            backend_factory=_WindowsHiddenProcessBackend,
        )
        if started:
            self.set_shell_banner(
                message=(
                    "Windows embedded compatibility mode active.\n"
                    "ConPTY startup failed, so SnakeSh restarted this shell with the hidden pipe backend."
                ),
                color="#f59e0b",
                blink=False,
            )
            self._append_local_status("Embedded compatibility mode enabled for this local shell tab.")
            return True

        self.set_shell_banner(message="", color="", blink=False)
        self._append_local_status("ERROR: Windows local shell could not start in advanced console mode.")
        self._append_local_status(f"ERROR: {startup_error}")
        self._append_local_status("ERROR: Embedded compatibility startup also failed.")
        self._append_local_status("Shell session closed.")
        self._mark_connection_closed(True)
        self._windows_local_shell_backend = ""
        return True

    def execute_command(self, command: str) -> bool:
        if self._worker:
            payload = f"{command}\r"
            if self._worker_uses_basic_process_io():
                payload = self._normalize_input_for_local_process(
                    command + "\n",
                    convert_del_to_bs=self._windows_local_process_backspace_mode,
                )
            self._worker.send_text(payload)
            return True
        if self._local_process and self._local_process.state() == QProcess.Running:
            self._local_process.write((command + "\r\n").encode("utf-8", errors="replace"))
            return True
        self._append_local_status("ERROR: Shell is not started.")
        return False

    def execute_raw_input(self, text: str) -> bool:
        if self._worker:
            payload = text
            if self._worker_uses_basic_process_io():
                payload = self._normalize_input_for_local_process(
                    text,
                    convert_del_to_bs=self._windows_local_process_backspace_mode,
                )
            self._worker.send_text(payload)
            return True
        if self._local_process and self._local_process.state() == QProcess.Running:
            payload = self._normalize_input_for_local_process(
                text,
                convert_del_to_bs=self._windows_local_process_backspace_mode,
            )
            self._local_process.write(payload.encode("utf-8", errors="replace"))
            return True
        self._append_local_status("ERROR: Shell is not started.")
        return False

    def _send_emulator_generated_input(self, text: str) -> None:
        if not text:
            return
        if self._worker:
            generated_input_writer = getattr(self._worker, "send_terminal_generated_input", None)
            if callable(generated_input_writer):
                generated_input_writer(text)
            else:
                self._worker.send_text(text)
            return
        if self._local_process and self._local_process.state() == QProcess.Running:
            self._local_process.write(text.encode("utf-8", errors="replace"))

    def _send_terminal_input(self, text: str) -> None:
        self._track_backspace_probe_input(text)
        if self._worker:
            payload = text
            if self._worker_uses_basic_process_io():
                payload = self._normalize_input_for_local_process(
                    text,
                    convert_del_to_bs=self._windows_local_process_backspace_mode,
                )
            self._worker.send_text(payload)
            return
        if self._local_process and self._local_process.state() == QProcess.Running:
            payload = self._normalize_input_for_local_process(
                text,
                convert_del_to_bs=self._windows_local_process_backspace_mode,
            )
            self._local_process.write(payload.encode("utf-8", errors="replace"))

    def _reset_backspace_adaptation(self) -> None:
        self._backspace_prefers_ctrl_h_override = None
        self._windows_local_process_backspace_mode = False
        self._backspace_probe_expected = ""
        self._backspace_probe_deadline = 0.0
        self._backspace_probe_buffer = ""
        self.output.set_backspace_prefers_ctrl_h_override(None)

    def _set_backspace_preference_override(self, preference: bool | None) -> None:
        if self._backspace_prefers_ctrl_h_override == preference:
            return
        self._backspace_prefers_ctrl_h_override = preference
        self.output.set_backspace_prefers_ctrl_h_override(preference)

    def _track_backspace_probe_input(self, text: str) -> None:
        if text == "\x7f":
            self._backspace_probe_expected = "^?"
            self._backspace_probe_deadline = time.monotonic() + 1.2
            self._backspace_probe_buffer = ""
            return
        if text == "\x08":
            self._backspace_probe_expected = "^H"
            self._backspace_probe_deadline = time.monotonic() + 1.2
            self._backspace_probe_buffer = ""
            return
        if text:
            self._backspace_probe_expected = ""
            self._backspace_probe_deadline = 0.0
            self._backspace_probe_buffer = ""

    def _observe_backspace_probe_output(self, text: str) -> None:
        if not text:
            return
        expected = self._backspace_probe_expected
        if not expected:
            return
        if time.monotonic() > self._backspace_probe_deadline:
            self._backspace_probe_expected = ""
            self._backspace_probe_deadline = 0.0
            self._backspace_probe_buffer = ""
            return

        self._backspace_probe_buffer = (self._backspace_probe_buffer + text)[-8:]
        if expected in self._backspace_probe_buffer:
            self._set_backspace_preference_override(expected == "^?")
            self._backspace_probe_expected = ""
            self._backspace_probe_deadline = 0.0
            self._backspace_probe_buffer = ""
            return

        # If the immediate echo does not include the expected caret notation,
        # discard this probe and avoid stale matches from later output.
        if len(self._backspace_probe_buffer) >= 4 or "\n" in self._backspace_probe_buffer or "\r" in self._backspace_probe_buffer:
            self._backspace_probe_expected = ""
            self._backspace_probe_deadline = 0.0
            self._backspace_probe_buffer = ""

    def is_logging_enabled(self) -> bool:
        return self._log_stream is not None

    def logging_path(self) -> str | None:
        return self._log_file_path

    def start_logging(self, file_path: str) -> None:
        target = Path(file_path).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        target_str = str(target)
        if self._log_stream and self._log_file_path == target_str:
            return
        self.stop_logging()
        stream = target.open("a", encoding="utf-8", newline="\n")
        self._log_stream = stream
        self._log_file_path = target_str
        self._log_current_line = ""
        self._log_pending_cr = False
        self.output.set_logging_active(True)

    def stop_logging(self) -> None:
        if self._pending_output_chunks:
            self._drain_pending_output(drain_all=True)
        stream = self._log_stream
        self._log_stream = None
        self._log_file_path = None
        self.output.set_logging_active(False)
        if stream is None:
            return
        try:
            if self._log_current_line:
                stream.write(self._log_current_line)
            stream.flush()
        except Exception:
            pass
        try:
            stream.close()
        except Exception:
            pass
        self._log_current_line = ""
        self._log_pending_cr = False

    def _request_open_sftp(self) -> None:
        if self._session_id:
            self.open_sftp_requested.emit(self._session_id)

    def _request_disconnect(self) -> None:
        self.disconnect_requested.emit(self)

    def _request_start_logging(self) -> None:
        self.start_logging_requested.emit(self)

    def _request_stop_logging(self) -> None:
        self.stop_logging_requested.emit(self)

    def can_reconnect_session(self) -> bool:
        kind = str(self.property("session_kind") or "").strip().upper()
        if kind == "LOCAL":
            return bool(self._windows_local_shell_start_program.strip())
        session_id = self.property("session_id")
        return (
            isinstance(session_id, str)
            and bool(session_id.strip())
            and not bool(self.property("session_runtime_only"))
        )

    def local_shell_reconnect_command(self) -> tuple[str, list[str], str | None] | None:
        program = self._windows_local_shell_start_program.strip()
        if not program:
            return None
        return (
            program,
            list(self._windows_local_shell_start_arguments),
            self._windows_local_shell_start_working_directory,
        )

    def _handle_terminal_paste(self, raw_text: str) -> None:
        has_remote_worker = self._worker is not None
        has_local_process = self._local_process is not None and self._local_process.state() == QProcess.Running
        if not has_remote_worker and not has_local_process:
            return
        if not raw_text:
            return

        normalized = raw_text.replace("\r\n", "\n").replace("\r", "\n")
        if "\n" not in normalized:
            if has_remote_worker:
                assert self._worker is not None
                payload = raw_text
                if self._worker_uses_basic_process_io():
                    payload = self._normalize_input_for_local_process(
                        raw_text,
                        convert_del_to_bs=self._windows_local_process_backspace_mode,
                    )
                self._worker.send_text(payload)
            else:
                assert self._local_process is not None
                payload = self._normalize_input_for_local_process(
                    raw_text,
                    convert_del_to_bs=self._windows_local_process_backspace_mode,
                )
                self._local_process.write(payload.encode("utf-8", errors="replace"))
            return

        edited = self._edit_multiline_paste(normalized)
        if edited is None:
            return
        if has_remote_worker:
            assert self._worker is not None
            payload = self._normalize_paste_for_pty(edited)
            if self._worker_uses_basic_process_io():
                payload = self._normalize_input_for_local_process(
                    edited,
                    convert_del_to_bs=self._windows_local_process_backspace_mode,
                )
            self._worker.send_text(payload)
        else:
            assert self._local_process is not None
            payload = self._normalize_input_for_local_process(
                edited,
                convert_del_to_bs=self._windows_local_process_backspace_mode,
            )
            self._local_process.write(payload.encode("utf-8", errors="replace"))

    @staticmethod
    def _normalize_paste_for_pty(text: str) -> str:
        # Shell input expects carriage return for Enter semantics.
        return text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r")

    @staticmethod
    def _normalize_input_for_local_process(text: str, *, convert_del_to_bs: bool = False) -> str:
        normalized = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")
        if convert_del_to_bs and "\x7f" in normalized:
            normalized = normalized.replace("\x7f", "\x08")
        return normalized

    @staticmethod
    def _normalize_output_for_local_process(text: str, *, destructive_backspace: bool = False) -> str:
        if not destructive_backspace or "\b" not in text:
            return text
        # Windows console-hosted shells can emit bare backspace and rely on the
        # host terminal to perform destructive erase.
        return text.replace("\b", "\x1b[D\x1b[P")

    def _worker_uses_basic_process_io(self) -> bool:
        worker = self._worker
        return isinstance(worker, WindowsLocalShellWorker) and worker.uses_basic_process_io()

    def _edit_multiline_paste(self, text: str) -> str | None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Review Multi-line Paste")
        dialog.resize(860, 520)

        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Review and edit the text before sending it to the terminal:"))

        editor = QPlainTextEdit()
        editor.setPlainText(text)
        layout.addWidget(editor)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        send_btn = buttons.button(QDialogButtonBox.Ok)
        if send_btn is not None:
            send_btn.setText("Send")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec():
            return editor.toPlainText()
        return None

    def _resize_terminal(self, cols: int, rows: int) -> None:
        self._emulator.resize(cols=cols, rows=rows)
        self._emulator.consume_dirty_rows()
        self._sync_terminal_size()
        self._emulator.consume_dirty_rows()
        self._schedule_render(full=True)

    def _sync_terminal_size(self) -> None:
        cols, rows = self.output.terminal_size()
        if self._last_synced_size == (cols, rows):
            return
        self._last_synced_size = (cols, rows)
        self._emulator.resize(cols=cols, rows=rows)
        if self._worker:
            self._worker.resize_terminal(cols, rows)

    def apply_settings(self, settings: AppSettings) -> None:
        self._settings = settings
        scrollback_limit = max(100, settings.terminal_scrollback_lines)
        self._scrollback_store.set_max_lines(scrollback_limit)
        self._emulator.set_history_limit(scrollback_limit)
        if not settings.terminal_bell_enabled:
            self._bell_sound_retry_timer.stop()
            self._pending_bell_sound_effect_play = False
            self._bell_sound_retry_attempts_remaining = 0
        self.output.apply_settings(settings)
        dialog = self._scrollback_dialog
        if dialog is not None:
            apply_runtime_settings = getattr(dialog, "apply_runtime_settings", None)
            if callable(apply_runtime_settings):
                apply_runtime_settings(settings)
        self._sync_terminal_size()
        self._render()

    def scrollback_text(self) -> str:
        if self._pending_output_chunks:
            self._drain_pending_output(drain_all=True)
        return self._compose_scrollback_text()

    def _compose_scrollback_text(self) -> str:
        return self._emulator.rendered_scrollback_text()

    def _schedule_scrollback_dialog_refresh(self) -> None:
        dialog = self._scrollback_dialog
        if dialog is None or not dialog.isVisible():
            return
        if not self._scrollback_dialog_refresh_timer.isActive():
            self._scrollback_dialog_refresh_timer.start()

    def _flush_scrollback_dialog_refresh(self) -> None:
        dialog = self._scrollback_dialog
        if dialog is None or not dialog.isVisible():
            return
        if getattr(dialog, "_provider", None) is not None:
            dialog.refresh_from_provider()
            return
        dialog.set_scrollback_text(self._compose_scrollback_text())

    def _write_log_chunk(self, text: str) -> None:
        stream = self._log_stream
        if stream is None:
            return
        payload = self._sanitize_log_text(text)
        if not payload:
            return
        try:
            self._append_log_chunk(stream, payload)
            stream.flush()
        except Exception as exc:
            self.stop_logging()
            self.logging_error.emit(self, str(exc))

    def _append_log_chunk(self, stream: TextIO, text: str) -> None:
        for ch in text:
            if self._log_pending_cr:
                if ch == "\n":
                    stream.write(f"{self._log_current_line}\n")
                    self._log_current_line = ""
                    self._log_pending_cr = False
                    continue
                self._log_current_line = ""
                self._log_pending_cr = False

            if ch == "\n":
                stream.write(f"{self._log_current_line}\n")
                self._log_current_line = ""
                continue
            if ch == "\r":
                self._log_pending_cr = True
                continue
            if ch == "\b":
                if self._log_current_line:
                    self._log_current_line = self._log_current_line[:-1]
                continue
            self._log_current_line += ch

    @classmethod
    def _sanitize_scrollback_text(cls, text: str) -> str:
        if not text:
            return ""
        cleaned = cls._OSC_RE.sub("", text)
        cleaned = cls._CSI_RE.sub("", cleaned)
        cleaned = cls._SINGLE_ESC_RE.sub("", cleaned)
        # Remove C0 controls except tab/newline/carriage-return/backspace to keep scrollback readable.
        return "".join(ch for ch in cleaned if ch in ("\n", "\r", "\t", "\b") or ord(ch) >= 0x20)

    @classmethod
    def _sanitize_log_text(cls, text: str) -> str:
        return cls._sanitize_scrollback_text(text)

    def _configure_automation(self, session: Session) -> None:
        self._stop_automation()
        self._automation_enabled = bool(session.ssh_automation_enabled)
        self._automation_steps = [
            SSHAutomationStep.from_dict(step.to_dict())
            for step in session.ssh_automation_steps
        ]
        self._automation_step_index = 0

    def _start_automation(self) -> None:
        if not self._automation_enabled or not self._automation_steps:
            return
        self._automation_running = True
        self._automation_step_index = 0
        self._append_local_status(
            f"[Automation] Starting scripted login flow ({len(self._automation_steps)} step(s))."
        )
        self._schedule_automation_step(delay_ms=0)

    def _schedule_automation_step(self, *, delay_ms: int) -> None:
        if not self._automation_running:
            return
        self._automation_step_timer.stop()
        self._automation_step_timer.start(max(0, delay_ms))

    def _run_next_automation_step(self) -> None:
        if not self._automation_running:
            return
        if self._automation_waiting_expect is not None:
            return
        if self._automation_step_index >= len(self._automation_steps):
            self._append_local_status("[Automation] Script completed.")
            self._stop_automation()
            return

        step = self._automation_steps[self._automation_step_index]
        if step.step_type == "sleep":
            delay_ms = max(0, round(step.sleep_seconds * 1000))
            self._append_local_status(f"[Automation] Sleep {step.sleep_seconds:g}s")
            self._automation_step_index += 1
            self._schedule_automation_step(delay_ms=delay_ms)
            return

        if step.step_type == "expect":
            expected = step.expect_text
            if not expected:
                self._append_local_status("[Automation] Skipping empty expect step.")
                self._automation_step_index += 1
                self._schedule_automation_step(delay_ms=0)
                return
            self._automation_expect_buffer = ""
            self._automation_waiting_expect = step
            timeout_ms = max(100, round(step.expect_timeout_seconds * 1000))
            self._automation_expect_timer.stop()
            self._automation_expect_timer.start(timeout_ms)
            self._append_local_status(
                f"[Automation] Expect '{expected}' within {step.expect_timeout_seconds:g}s."
            )
            return

        command = step.command.strip()
        if not command:
            self._append_local_status("[Automation] Skipping empty command step.")
            self._automation_step_index += 1
            self._schedule_automation_step(delay_ms=0)
            return
        self._append_local_status(f"[Automation] Command: {command}")
        if not self.execute_command(command):
            self._append_local_status("[Automation] Stopped: shell is not ready.")
            self._stop_automation()
            return
        self._automation_step_index += 1
        self._schedule_automation_step(delay_ms=0)

    def _consume_automation_output(self, text: str) -> None:
        step = self._automation_waiting_expect
        if step is None:
            return
        cleaned = self._sanitize_scrollback_text(text)
        if not cleaned:
            return
        self._automation_expect_buffer += cleaned
        if len(self._automation_expect_buffer) > 8192:
            self._automation_expect_buffer = self._automation_expect_buffer[-8192:]
        if step.expect_text not in self._automation_expect_buffer:
            return
        self._automation_expect_timer.stop()
        self._automation_waiting_expect = None
        self._automation_expect_buffer = ""
        self._append_local_status(f"[Automation] Expect matched: {step.expect_text}")
        self._automation_step_index += 1
        self._schedule_automation_step(delay_ms=0)

    def _on_automation_expect_timeout(self) -> None:
        if not self._automation_running:
            return
        step = self._automation_waiting_expect
        if step is None:
            return
        self._automation_waiting_expect = None
        self._automation_expect_buffer = ""
        if step.expect_on_timeout == "continue":
            self._append_local_status("[Automation] Expect timed out. Continuing script.")
            self._automation_step_index += 1
            self._schedule_automation_step(delay_ms=0)
            return
        self._append_local_status("[Automation] Expect timed out. Script terminated.")
        self._stop_automation()

    def _stop_automation(self) -> None:
        self._automation_step_timer.stop()
        self._automation_expect_timer.stop()
        self._automation_running = False
        self._automation_waiting_expect = None
        self._automation_expect_buffer = ""

    def resolved_font_family(self) -> str:
        return self.output.resolved_font_family()

    def has_active_connection(self) -> bool:
        if self._worker is not None:
            return True
        process = self._local_process
        if process is None:
            return False
        return process.state() == QProcess.Running

    def is_session_closed(self) -> bool:
        return bool(self.property("terminal_session_closed"))

    def _mark_connection_closed(self, closed: bool) -> None:
        is_closed = bool(closed)
        self.setProperty("terminal_session_closed", is_closed)
        self.output.set_session_connected(not is_closed)
        self._notify_title_refresh()

    def _notify_title_refresh(self) -> None:
        owner = self.window()
        refresh = getattr(owner, "_refresh_tab_title", None)
        if callable(refresh):
            try:
                refresh(self)
            except Exception:
                pass

    def show_scrollback(self) -> None:
        self._show_scrollback()

    def _show_scrollback(self) -> None:
        now = time.monotonic()
        existing_dialog = self._scrollback_dialog
        if existing_dialog is not None:
            if getattr(existing_dialog, "_provider", None) is not None:
                existing_dialog.refresh_from_provider(force=True)
            else:
                existing_dialog.set_scrollback_text(self.scrollback_text())
            existing_dialog.showNormal()
            existing_dialog.raise_()
            existing_dialog.activateWindow()
            return
        if now < self._scrollback_reopen_block_until:
            return
        dialog = TerminalScrollbackDialog(provider=self._scrollback_store, settings=self._settings)
        self._scrollback_dialog = dialog
        self.destroyed.connect(dialog.deleteLater)

        def _mark_closed(_result: int) -> None:
            self._scrollback_dialog_refresh_timer.stop()

        dialog.finished.connect(_mark_closed)
        dialog.destroyed.connect(lambda *_args: setattr(self, "_scrollback_dialog", None))
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _toggle_shell_banner_visibility(self) -> None:
        if not self._shell_banner_text:
            self._shell_banner_blink_timer.stop()
            self._shell_banner_label.hide()
            return
        self._shell_banner_blink_visible = not self._shell_banner_blink_visible
        self._apply_shell_banner_style(text_visible=self._shell_banner_blink_visible)

    def _apply_shell_banner_style(self, *, text_visible: bool) -> None:
        color = self._shell_banner_color
        text_color = color.name() if text_visible else f"rgba({color.red()}, {color.green()}, {color.blue()}, 0)"
        self._shell_banner_label.setStyleSheet(
            "QLabel {"
            f"color: {text_color};"
            f"background-color: rgba({color.red()}, {color.green()}, {color.blue()}, 36);"
            f"border: 1px solid {color.name()};"
            "border-radius: 6px;"
            "padding: 6px 10px;"
            "font-weight: 700;"
            "}"
        )

    def disconnect_session(self, *, wait_seconds: float | None = None) -> bool:
        self._stop_automation()
        self._reset_terminal_query_probe_tail()
        if self._pending_output_chunks:
            self._drain_pending_output(drain_all=True)
        self.stop_logging()

        wait_budget = self._SHUTDOWN_WAIT_SECONDS if wait_seconds is None else max(0.0, float(wait_seconds))

        process = self._local_process
        if process is not None and process.state() == QProcess.Running:
            try:
                process.write(b"exit\r\n")
            except Exception:
                pass
            process.terminate()
            timeout_ms = max(0, int(wait_budget * 1000))
            if timeout_ms <= 0:
                return False
            first_wait_ms = min(1500, timeout_ms)
            if not process.waitForFinished(first_wait_ms):
                process.kill()
                remaining_ms = max(1, timeout_ms - first_wait_ms)
                if not process.waitForFinished(remaining_ms):
                    return False

        worker = self._worker
        if worker is not None:
            try:
                worker.stop()
            except Exception:
                pass

        thread = self._thread
        if thread is not None:
            try:
                running = thread.isRunning()
            except RuntimeError:
                running = False
            if running:
                deadline = time.monotonic() + wait_budget
                try:
                    thread.quit()
                except RuntimeError:
                    running = False
                now = time.monotonic()
                while running and now < deadline:
                    remaining_ms = max(
                        1,
                        min(
                            self._SHUTDOWN_WAIT_SLICE_MS,
                            int(max(0.0, deadline - now) * 1000),
                        ),
                    )
                    thread.wait(remaining_ms)
                    QApplication.processEvents()
                    try:
                        running = thread.isRunning()
                    except RuntimeError:
                        running = False
                    if not running:
                        break
                    if worker is not None:
                        try:
                            worker.stop()
                        except Exception:
                            pass
                    try:
                        thread.quit()
                    except RuntimeError:
                        running = False
                    now = time.monotonic()
                if running:
                    return False

        QApplication.processEvents()
        if self.has_active_connection():
            return False
        self._mark_connection_closed(True)
        return True

    def shutdown(self, *, wait_seconds: float | None = None) -> bool:
        self._stop_automation()
        dialog = self._scrollback_dialog
        self._scrollback_dialog = None
        if dialog is not None:
            dialog.close()
            dialog.deleteLater()
        if self._pending_output_chunks:
            self._drain_pending_output(drain_all=True)
        self.stop_logging()
        self._scrollback_store.close()
        self._output_drain_timer.stop()
        self._shell_banner_blink_timer.stop()
        self._bell_sound_retry_timer.stop()
        self._bell_parse_state = self._BELL_PARSE_NORMAL
        self._reset_terminal_query_probe_tail()
        self._pending_bell_sound_effect_play = False
        self._bell_sound_retry_attempts_remaining = 0
        self._pending_output_chunks.clear()
        self._pending_output_chars = 0
        self._windows_local_shell_backend = ""
        self._windows_local_shell_connected = False
        self._windows_local_shell_fallback_attempted = False
        self._suppress_local_worker_closed_message = False
        self._suppress_next_local_process_error_message = False
        process = self._local_process
        self._local_process = None
        if process is not None:
            if process.state() == QProcess.Running:
                try:
                    process.write(b"exit\r\n")
                except Exception:
                    pass
                process.terminate()
                if not process.waitForFinished(1500):
                    process.kill()
                    process.waitForFinished(1000)
            process.deleteLater()
        worker = self._worker
        if worker:
            worker.stop()
        if self._thread:
            try:
                running = self._thread.isRunning()
            except RuntimeError:
                # Underlying C++ QThread object already deleted.
                self._thread = None
                self._worker = None
                return True
            if running:
                # Avoid force-terminating Python worker threads; request cooperative
                # stop and wait for the worker loop to exit cleanly.
                wait_budget = self._SHUTDOWN_WAIT_SECONDS if wait_seconds is None else max(0.0, float(wait_seconds))
                deadline = time.monotonic() + wait_budget
                try:
                    self._thread.quit()
                except RuntimeError:
                    running = False
                now = time.monotonic()
                while running and now < deadline:
                    remaining_ms = max(
                        1,
                        min(
                            self._SHUTDOWN_WAIT_SLICE_MS,
                            int(max(0.0, deadline - now) * 1000),
                        ),
                    )
                    self._thread.wait(remaining_ms)
                    try:
                        running = self._thread.isRunning()
                    except RuntimeError:
                        running = False
                    if not running:
                        break
                    if worker is not None:
                        try:
                            worker.stop()
                        except Exception:
                            pass
                    try:
                        self._thread.quit()
                    except RuntimeError:
                        running = False
                    now = time.monotonic()
                if running:
                    if wait_budget > 0:
                        self._append_local_status("Shell is still shutting down; please close this tab again in a moment.")
                    return False
            self._thread = None
            self._worker = None
        return True

    @Slot(str)
    def _on_worker_output(self, text: str) -> None:
        self._observe_backspace_probe_output(text)
        self._consume_automation_output(text)
        self._queue_terminal_output(text)

    @Slot(str)
    def _on_worker_error(self, message: str) -> None:
        self._append_local_status(f"ERROR: {message}")

    @Slot()
    def _on_worker_connected(self) -> None:
        self._mark_connection_closed(False)
        self._append_local_status("Connected. Interactive shell ready.")
        self._start_automation()

    @Slot()
    def _on_worker_closed(self) -> None:
        self._stop_automation()
        self._append_local_status("Shell session closed.")
        self._mark_connection_closed(True)
        self.stop_logging()
        if self._thread:
            try:
                self._thread.quit()
            except RuntimeError:
                self._thread = None
                self._worker = None

    @Slot()
    def _on_thread_finished(self) -> None:
        # Clear references so later shutdown calls don't touch deleted Qt objects.
        self._stop_automation()
        self.stop_logging()
        self._thread = None
        self._worker = None

    @Slot(str)
    def _on_local_worker_output(self, text: str) -> None:
        self._observe_backspace_probe_output(text)
        terminal_output = text
        if self._worker_uses_basic_process_io():
            terminal_output = self._normalize_output_for_local_process(
                text,
                destructive_backspace=self._windows_local_process_backspace_mode,
            )
        self._queue_terminal_output(terminal_output)

    @Slot(str)
    def _on_local_worker_error(self, message: str) -> None:
        if self._attempt_windows_local_shell_embedded_fallback(message):
            return
        self._append_local_status(f"ERROR: {message}")

    @Slot()
    def _on_local_worker_connected(self) -> None:
        self._windows_local_shell_connected = True
        self._mark_connection_closed(False)
        self._append_local_status("Connected. Local shell ready.")

    @Slot()
    def _on_local_worker_closed(self) -> None:
        if self._suppress_local_worker_closed_message:
            self._suppress_local_worker_closed_message = False
            return
        self._stop_automation()
        self._append_local_status("Shell session closed.")
        self._mark_connection_closed(True)
        self.stop_logging()
        self._windows_local_shell_backend = ""
        self._windows_local_shell_connected = False
        if self._thread:
            try:
                self._thread.quit()
            except RuntimeError:
                self._thread = None
                self._worker = None

    @Slot()
    def _on_local_process_output(self) -> None:
        if self._local_process_output_paused:
            return
        process = self._local_process
        if process is None:
            return
        payload = bytes(process.readAllStandardOutput())
        if not payload:
            return
        raw_output = payload.decode("utf-8", errors="replace")
        self._observe_backspace_probe_output(raw_output)
        terminal_output = self._normalize_output_for_local_process(
            raw_output,
            destructive_backspace=self._windows_local_process_backspace_mode,
        )
        self._queue_terminal_output(terminal_output)

    @Slot()
    def _on_local_process_started(self) -> None:
        process = self._local_process
        if process is None:
            return
        self._windows_local_shell_connected = True
        self._mark_connection_closed(False)
        program = process.program().strip() or "shell"
        self._append_local_status(f"Connected. Local shell ready ({program}).")

    @Slot(int, QProcess.ExitStatus)
    def _on_local_process_finished(self, _exit_code: int, _status: QProcess.ExitStatus) -> None:
        self._stop_automation()
        self._append_local_status("Shell session closed.")
        self._mark_connection_closed(True)
        self.stop_logging()
        self._windows_local_process_backspace_mode = False
        self._windows_local_shell_backend = ""
        self._windows_local_shell_connected = False
        process = self._local_process
        self._local_process = None
        if process is not None:
            process.deleteLater()

    @Slot(QProcess.ProcessError)
    def _on_local_process_error(self, error: QProcess.ProcessError) -> None:
        if self._suppress_next_local_process_error_message:
            self._suppress_next_local_process_error_message = False
            if error == QProcess.FailedToStart:
                self._windows_local_process_backspace_mode = False
                self._windows_local_shell_backend = ""
                self._windows_local_shell_connected = False
                process = self._local_process
                self._local_process = None
                if process is not None:
                    process.deleteLater()
            return
        labels = {
            QProcess.FailedToStart: "Failed to start",
            QProcess.Crashed: "Process crashed",
            QProcess.Timedout: "Operation timed out",
            QProcess.WriteError: "Write error",
            QProcess.ReadError: "Read error",
            QProcess.UnknownError: "Unknown error",
        }
        label = labels.get(error, "Unknown error")
        self._append_local_status(f"ERROR: Local shell process error ({label}).")
        if error == QProcess.FailedToStart:
            self._windows_local_process_backspace_mode = False
            self._windows_local_shell_backend = ""
            self._windows_local_shell_connected = False
            process = self._local_process
            self._local_process = None
            if process is not None:
                process.deleteLater()


class _LocalPTYCaptureRecorder:
    _SCHEMA_VERSION = 1

    def __init__(self, path: Path, stream: TextIO) -> None:
        self._path = path
        self._stream = stream
        self._closed = False

    @classmethod
    def create(
        cls,
        *,
        program: str,
        arguments: list[str],
        working_directory: str | None,
        term: str,
        cols: int,
        rows: int,
        child_pid: int | None,
    ) -> "_LocalPTYCaptureRecorder | None":
        directory_raw = os.getenv(LOCAL_PTY_CAPTURE_DIR_ENV, "").strip()
        if not directory_raw:
            return None
        try:
            directory = Path(directory_raw).expanduser()
            directory.mkdir(parents=True, exist_ok=True)
            timestamp = time.strftime("%Y%m%d-%H%M%S")
            pid_label = child_pid if child_pid is not None else os.getpid()
            path = directory / f"local-pty-{timestamp}-{pid_label}.jsonl"
            stream = path.open("a", encoding="utf-8", newline="\n")
        except Exception as exc:
            _LOGGER.warning("Failed to start local PTY capture in %s: %s", directory_raw, exc)
            return None

        recorder = cls(path=path, stream=stream)
        recorder._write_record(
            {
                "type": "meta",
                "schema_version": cls._SCHEMA_VERSION,
                "program": program,
                "argv": [program, *arguments],
                "cwd": working_directory or "",
                "term": term,
                "cols": cols,
                "rows": rows,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "child_pid": child_pid,
            }
        )
        return recorder

    @property
    def path(self) -> Path:
        return self._path

    def record_output(self, payload: bytes) -> None:
        if payload:
            self._record_bytes("output", payload)

    def record_input(self, payload: bytes) -> None:
        if payload:
            self._record_bytes("input", payload)

    def record_resize(self, *, cols: int, rows: int) -> None:
        self._write_record({"type": "resize", "cols": cols, "rows": rows})

    def close(self) -> None:
        if self._closed:
            return
        self._write_record({"type": "close"})
        self._closed = True
        try:
            self._stream.close()
        except Exception:
            pass

    def _record_bytes(self, record_type: str, payload: bytes) -> None:
        self._write_record(
            {
                "type": record_type,
                "data_b64": base64.b64encode(payload).decode("ascii"),
            }
        )

    def _write_record(self, payload: dict[str, object]) -> None:
        if self._closed:
            return
        try:
            self._stream.write(json.dumps(payload, ensure_ascii=True, separators=(",", ":")))
            self._stream.write("\n")
            self._stream.flush()
        except Exception as exc:
            _LOGGER.warning("Failed to append local PTY capture %s: %s", self._path, exc)
            self._closed = True
            try:
                self._stream.close()
            except Exception:
                pass


class LocalShellWorker(QObject):
    output = Signal(str)
    error = Signal(str)
    connected = Signal()
    closed = Signal()

    _READ_SIZE = 16384
    _SELECT_TIMEOUT_SECONDS = 0.05

    def __init__(
        self,
        *,
        program: str,
        arguments: list[str],
        working_directory: str | None,
        cols: int,
        rows: int,
    ) -> None:
        super().__init__()
        self._program = program
        self._arguments = list(arguments)
        self._working_directory = working_directory
        self._cols = max(10, cols)
        self._rows = max(5, rows)
        self._master_fd: int | None = None
        self._child_pid: int | None = None
        self._stop_requested = False
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._decoder_flushed = False
        self._capture_recorder: _LocalPTYCaptureRecorder | None = None
        self._command_wakeup_read_fd: int | None = None
        self._command_wakeup_write_fd: int | None = None
        self._output_paused = False
        self._command_queue: queue.Queue[
            tuple[str, str] | tuple[str, int, int] | None
        ] = queue.Queue()

    @Slot()
    def start(self) -> None:
        if os.name != "posix":
            self.error.emit("Local PTY mode is only supported on POSIX systems.")
            self.closed.emit()
            return
        try:
            child_pid, master_fd = pty.fork()
        except Exception as exc:
            self.error.emit(f"Failed to start local shell PTY: {exc}")
            self.closed.emit()
            return

        if child_pid == 0:
            self._run_child_process()
            os._exit(127)

        self._child_pid = child_pid
        self._master_fd = master_fd
        try:
            os.set_blocking(master_fd, False)
        except Exception:
            pass
        self._initialize_command_wakeup_pipe()
        self._capture_recorder = _LocalPTYCaptureRecorder.create(
            program=self._program,
            arguments=self._arguments,
            working_directory=self._working_directory,
            term=(os.environ.get("TERM") or "").strip() or "xterm-256color",
            cols=self._cols,
            rows=self._rows,
            child_pid=child_pid,
        )
        self._apply_terminal_size(self._cols, self._rows)
        self.connected.emit()

        try:
            self._run_io_loop()
        except Exception as exc:
            self.error.emit(f"Local shell loop failed: {exc}")
        finally:
            self._flush_decoder()
            self._close_capture_recorder()
            self._close_command_wakeup_pipe()
            self._cleanup_child()
            self.closed.emit()

    def _run_child_process(self) -> None:
        env = sanitized_local_shell_environment()
        env.setdefault("TERM", "xterm-256color")
        # Let interactive programs read the live PTY size instead of inheriting
        # fixed startup dimensions from the environment.
        env.pop("COLUMNS", None)
        env.pop("LINES", None)
        if self._working_directory:
            try:
                os.chdir(self._working_directory)
            except Exception:
                pass
        argv = [self._program, *self._arguments]
        try:
            os.execvpe(self._program, argv, env)
        except FileNotFoundError:
            try:
                os.write(2, f"Shell executable not found: {self._program}\n".encode("utf-8", errors="replace"))
            except Exception:
                pass
        except Exception as exc:
            try:
                os.write(2, f"Failed to launch shell: {exc}\n".encode("utf-8", errors="replace"))
            except Exception:
                pass

    def _run_io_loop(self) -> None:
        while not self._stop_requested:
            self._drain_commands()
            if self._stop_requested:
                break

            master_fd = self._master_fd
            if master_fd is None:
                break
            wake_fd = self._command_wakeup_read_fd
            wait_fds = [] if self._output_paused else [master_fd]
            if wake_fd is not None:
                wait_fds.append(wake_fd)
            if not wait_fds:
                time.sleep(self._SELECT_TIMEOUT_SECONDS)
                if self._child_exited():
                    break
                continue

            try:
                readable, _writable, _errors = select.select(wait_fds, [], [], self._SELECT_TIMEOUT_SECONDS)
            except (OSError, ValueError):
                break

            if wake_fd is not None and wake_fd in readable:
                self._drain_command_wakeup_pipe()
                self._drain_commands()
                if self._stop_requested:
                    break

            if master_fd in readable:
                try:
                    payload = os.read(master_fd, self._READ_SIZE)
                except BlockingIOError:
                    payload = b""
                except OSError as exc:
                    if exc.errno in {errno.EIO, errno.EBADF}:
                        break
                    self.error.emit(f"Local shell read failed: {exc}")
                    break
                if not payload:
                    break
                recorder = self._capture_recorder
                if recorder is not None:
                    recorder.record_output(payload)
                self._emit_decoded_output(payload)

            if self._child_exited():
                break

    def _initialize_command_wakeup_pipe(self) -> None:
        if self._command_wakeup_read_fd is not None or self._command_wakeup_write_fd is not None:
            return
        try:
            read_fd, write_fd = os.pipe()
            os.set_blocking(read_fd, False)
            os.set_blocking(write_fd, False)
            self._command_wakeup_read_fd = read_fd
            self._command_wakeup_write_fd = write_fd
        except Exception:
            self._command_wakeup_read_fd = None
            self._command_wakeup_write_fd = None

    def _wake_command_waiter(self) -> None:
        wake_fd = self._command_wakeup_write_fd
        if wake_fd is None:
            return
        try:
            os.write(wake_fd, b"\x00")
        except BlockingIOError:
            return
        except Exception:
            return

    def _drain_command_wakeup_pipe(self) -> None:
        wake_fd = self._command_wakeup_read_fd
        if wake_fd is None:
            return
        while True:
            try:
                payload = os.read(wake_fd, 1024)
            except BlockingIOError:
                return
            except Exception:
                return
            if not payload:
                return

    def _close_command_wakeup_pipe(self) -> None:
        read_fd = self._command_wakeup_read_fd
        self._command_wakeup_read_fd = None
        if read_fd is not None:
            try:
                os.close(read_fd)
            except Exception:
                pass
        write_fd = self._command_wakeup_write_fd
        self._command_wakeup_write_fd = None
        if write_fd is not None:
            try:
                os.close(write_fd)
            except Exception:
                pass

    def _drain_commands(self) -> None:
        while True:
            try:
                command = self._command_queue.get_nowait()
            except queue.Empty:
                return

            if command is None:
                self._stop_requested = True
                return

            action = command[0]
            if action == "input":
                self._write_to_pty(command[1])
            elif action == "resize":
                self._apply_terminal_size(command[1], command[2])

    def _write_to_pty(self, text: str) -> None:
        master_fd = self._master_fd
        if master_fd is None or not text:
            return
        payload = text.encode("utf-8", errors="replace")
        while payload:
            try:
                written = os.write(master_fd, payload)
                if written <= 0:
                    break
                recorder = self._capture_recorder
                if recorder is not None:
                    recorder.record_input(payload[:written])
                payload = payload[written:]
            except BlockingIOError:
                time.sleep(0.005)
            except OSError as exc:
                if exc.errno in {errno.EIO, errno.EBADF}:
                    self._stop_requested = True
                    return
                self.error.emit(f"Local shell write failed: {exc}")
                self._stop_requested = True
                return

    def _apply_terminal_size(self, cols: int, rows: int) -> None:
        self._cols = max(10, cols)
        self._rows = max(5, rows)
        master_fd = self._master_fd
        if master_fd is None:
            return
        try:
            packed = struct.pack("HHHH", self._rows, self._cols, 0, 0)
            fcntl.ioctl(master_fd, termios.TIOCSWINSZ, packed)
        except Exception:
            return
        recorder = self._capture_recorder
        if recorder is not None:
            recorder.record_resize(cols=self._cols, rows=self._rows)
        self._notify_resize_signal()

    def _emit_decoded_output(self, payload: bytes) -> None:
        text = self._decoder.decode(payload, final=False)
        if text:
            self.output.emit(text)

    def _flush_decoder(self) -> None:
        if self._decoder_flushed:
            return
        self._decoder_flushed = True
        text = self._decoder.decode(b"", final=True)
        if text:
            self.output.emit(text)

    def _close_capture_recorder(self) -> None:
        recorder = self._capture_recorder
        self._capture_recorder = None
        if recorder is None:
            return
        recorder.close()

    def _notify_resize_signal(self) -> None:
        master_fd = self._master_fd
        if master_fd is not None:
            try:
                fg_pgid = os.tcgetpgrp(master_fd)
            except Exception:
                fg_pgid = None
            if fg_pgid is not None and fg_pgid > 0:
                try:
                    os.killpg(fg_pgid, signal.SIGWINCH)
                    return
                except Exception:
                    pass

        child_pid = self._child_pid
        if child_pid is None:
            return
        try:
            pgid = os.getpgid(child_pid)
        except Exception:
            pgid = None
        if pgid is not None:
            try:
                os.killpg(pgid, signal.SIGWINCH)
                return
            except Exception:
                pass
        try:
            os.kill(child_pid, signal.SIGWINCH)
        except Exception:
            return

    def _child_exited(self) -> bool:
        child_pid = self._child_pid
        if child_pid is None:
            return True
        try:
            exited_pid, _status = os.waitpid(child_pid, os.WNOHANG)
        except ChildProcessError:
            self._child_pid = None
            return True
        except Exception:
            return False
        if exited_pid == 0:
            return False
        self._child_pid = None
        return True

    def _cleanup_child(self) -> None:
        master_fd = self._master_fd
        self._master_fd = None
        if master_fd is not None:
            try:
                os.close(master_fd)
            except Exception:
                pass

        child_pid = self._child_pid
        if child_pid is None:
            return

        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline:
            try:
                exited_pid, _status = os.waitpid(child_pid, os.WNOHANG)
            except ChildProcessError:
                self._child_pid = None
                return
            except Exception:
                break
            if exited_pid == child_pid:
                self._child_pid = None
                return
            time.sleep(0.05)

        try:
            os.kill(child_pid, signal.SIGTERM)
        except ProcessLookupError:
            self._child_pid = None
            return
        except Exception:
            self._child_pid = None
            return

        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            try:
                exited_pid, _status = os.waitpid(child_pid, os.WNOHANG)
            except ChildProcessError:
                self._child_pid = None
                return
            except Exception:
                break
            if exited_pid == child_pid:
                self._child_pid = None
                return
            time.sleep(0.05)

        try:
            os.kill(child_pid, signal.SIGKILL)
        except Exception:
            self._child_pid = None
            return
        try:
            os.waitpid(child_pid, 0)
        except Exception:
            pass
        self._child_pid = None

    @Slot(str)
    def send_text(self, text: str) -> None:
        if not text:
            return
        if self._master_fd is None and self._child_pid is None:
            self.error.emit("Shell is not ready.")
            return
        self._command_queue.put(("input", text))
        self._wake_command_waiter()

    def send_terminal_generated_input(self, text: str) -> None:
        if not text:
            return
        if self._terminal_is_canonical():
            return
        self.send_text(text)

    def _terminal_is_canonical(self) -> bool:
        master_fd = self._master_fd
        if master_fd is None:
            return False
        try:
            attrs = termios.tcgetattr(master_fd)
            lflag = int(attrs[3])
        except Exception:
            return False
        return bool(lflag & termios.ICANON)

    @Slot(int, int)
    def resize_terminal(self, cols: int, rows: int) -> None:
        self._cols = max(10, cols)
        self._rows = max(5, rows)
        if self._master_fd is None:
            return
        self._command_queue.put(("resize", self._cols, self._rows))
        self._wake_command_waiter()

    def pause_output(self) -> None:
        self._output_paused = True
        self._wake_command_waiter()

    def resume_output(self) -> None:
        self._output_paused = False
        self._wake_command_waiter()

    def stop(self) -> None:
        self._stop_requested = True
        try:
            self._command_queue.put_nowait(None)
        except Exception:
            pass
        self._wake_command_waiter()


class _WindowsTerminalBackend(TypingProtocol):
    def start(self) -> None: ...

    def bytes_available(self) -> int: ...

    def read(self, max_bytes: int) -> bytes: ...

    def write(self, payload: bytes) -> None: ...

    def resize(self, cols: int, rows: int) -> None: ...

    def has_exited(self) -> bool: ...

    def close(self) -> None: ...


class _WindowsHiddenProcessBackend:
    _ERROR_BROKEN_PIPE = 109
    _ERROR_NO_DATA = 232
    _ERROR_PIPE_NOT_CONNECTED = 233

    def __init__(
        self,
        *,
        program: str,
        arguments: list[str],
        working_directory: str | None,
        cols: int,
        rows: int,
    ) -> None:
        self._program = program
        self._arguments = list(arguments)
        self._working_directory = working_directory
        self._cols = max(10, cols)
        self._rows = max(5, rows)
        self._process: subprocess.Popen[bytes] | None = None
        self._stdout_handle: int | None = None

    def start(self) -> None:
        if os.name != "nt":
            raise OSError("Hidden Windows local-shell mode is only supported on Windows.")

        create_no_window = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if create_no_window == 0:
            raise OSError(
                "CREATE_NO_WINDOW is unavailable, so SnakeSh cannot guarantee an embedded Windows shell."
            )

        creationflags = create_no_window | int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        startupinfo = None
        startupinfo_cls = getattr(subprocess, "STARTUPINFO", None)
        if startupinfo_cls is not None:
            startupinfo = startupinfo_cls()
            startupinfo.dwFlags |= int(getattr(subprocess, "STARTF_USESHOWWINDOW", 0))
            startupinfo.wShowWindow = int(getattr(subprocess, "SW_HIDE", 0))

        env = os.environ.copy()
        env.pop("COLUMNS", None)
        env.pop("LINES", None)

        process = subprocess.Popen(  # noqa: S603
            [self._program, *self._arguments],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=self._working_directory or None,
            env=env,
            creationflags=creationflags,
            startupinfo=startupinfo,
            bufsize=0,
        )
        if process.stdin is None or process.stdout is None:
            process.kill()
            raise OSError("Hidden Windows shell failed to create stdio pipes.")

        import msvcrt

        self._process = process
        self._stdout_handle = int(msvcrt.get_osfhandle(process.stdout.fileno()))

    def bytes_available(self) -> int:
        handle = self._stdout_handle
        if handle is None:
            return 0

        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.PeekNamedPipe.argtypes = [
            wintypes.HANDLE,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.LPVOID,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPVOID,
        ]
        kernel32.PeekNamedPipe.restype = wintypes.BOOL
        available = wintypes.DWORD()
        ok = kernel32.PeekNamedPipe(
            wintypes.HANDLE(handle),
            None,
            0,
            None,
            ctypes.byref(available),
            None,
        )
        if ok:
            return int(available.value)
        error_code = ctypes.get_last_error()
        if error_code in {self._ERROR_BROKEN_PIPE, self._ERROR_NO_DATA, self._ERROR_PIPE_NOT_CONNECTED}:
            return 0
        raise OSError(error_code, f"Hidden Windows shell output poll failed (Windows error {error_code})")

    def read(self, max_bytes: int) -> bytes:
        process = self._process
        if process is None or process.stdout is None:
            return b""
        try:
            return process.stdout.read(max(1, int(max_bytes))) or b""
        except (BrokenPipeError, OSError):
            return b""

    def write(self, payload: bytes) -> None:
        process = self._process
        if process is None or process.stdin is None or not payload:
            return
        try:
            process.stdin.write(payload)
            process.stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            return

    def resize(self, cols: int, rows: int) -> None:
        self._cols = max(10, cols)
        self._rows = max(5, rows)

    def has_exited(self) -> bool:
        process = self._process
        if process is None:
            return True
        return process.poll() is not None

    def close(self) -> None:
        process = self._process
        self._process = None
        self._stdout_handle = None
        if process is None:
            return

        if process.stdin is not None:
            try:
                process.stdin.close()
            except Exception:
                pass
        try:
            process.wait(timeout=0.2)
        except subprocess.TimeoutExpired:
            try:
                process.terminate()
            except Exception:
                pass
            try:
                process.wait(timeout=0.8)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                except Exception:
                    pass
                try:
                    process.wait(timeout=0.5)
                except Exception:
                    pass
        except Exception:
            pass

        if process.stdout is not None:
            try:
                process.stdout.close()
            except Exception:
                pass


class _WindowsConPTYBackend:
    _WAIT_OBJECT_0 = 0
    _WAIT_TIMEOUT = 258
    _ERROR_BROKEN_PIPE = 109
    _ERROR_NO_DATA = 232
    _ERROR_PIPE_NOT_CONNECTED = 233
    _HANDLE_FLAG_INHERIT = 0x00000001
    _EXTENDED_STARTUPINFO_PRESENT = 0x00080000
    _PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE = 0x00020016

    def __init__(
        self,
        *,
        program: str,
        arguments: list[str],
        working_directory: str | None,
        cols: int,
        rows: int,
    ) -> None:
        self._program = program
        self._arguments = list(arguments)
        self._working_directory = working_directory
        self._cols = max(10, cols)
        self._rows = max(5, rows)
        self._pseudo_console = None
        self._input_write_handle = None
        self._output_read_handle = None
        self._process_handle = None
        self._process_id: int | None = None

    @classmethod
    def is_supported(cls) -> bool:
        if os.name != "nt":
            return False
        try:
            import ctypes
        except Exception:
            return False
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        except Exception:
            return False
        for symbol in ("CreatePseudoConsole", "ResizePseudoConsole", "ClosePseudoConsole"):
            try:
                getattr(kernel32, symbol)
            except AttributeError:
                return False
        return True

    def start(self) -> None:
        if not self.is_supported():
            raise OSError("Windows ConPTY is unavailable on this system.")

        import ctypes
        from ctypes import wintypes

        class COORD(ctypes.Structure):
            _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]

        class SECURITY_ATTRIBUTES(ctypes.Structure):
            _fields_ = [
                ("nLength", wintypes.DWORD),
                ("lpSecurityDescriptor", wintypes.LPVOID),
                ("bInheritHandle", wintypes.BOOL),
            ]

        class STARTUPINFOW(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("lpReserved", wintypes.LPWSTR),
                ("lpDesktop", wintypes.LPWSTR),
                ("lpTitle", wintypes.LPWSTR),
                ("dwX", wintypes.DWORD),
                ("dwY", wintypes.DWORD),
                ("dwXSize", wintypes.DWORD),
                ("dwYSize", wintypes.DWORD),
                ("dwXCountChars", wintypes.DWORD),
                ("dwYCountChars", wintypes.DWORD),
                ("dwFillAttribute", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD),
                ("wShowWindow", wintypes.WORD),
                ("cbReserved2", wintypes.WORD),
                ("lpReserved2", ctypes.POINTER(ctypes.c_ubyte)),
                ("hStdInput", wintypes.HANDLE),
                ("hStdOutput", wintypes.HANDLE),
                ("hStdError", wintypes.HANDLE),
            ]

        class STARTUPINFOEXW(ctypes.Structure):
            _fields_ = [
                ("StartupInfo", STARTUPINFOW),
                ("lpAttributeList", wintypes.LPVOID),
            ]

        class PROCESS_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("hProcess", wintypes.HANDLE),
                ("hThread", wintypes.HANDLE),
                ("dwProcessId", wintypes.DWORD),
                ("dwThreadId", wintypes.DWORD),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreatePipe.argtypes = [
            ctypes.POINTER(wintypes.HANDLE),
            ctypes.POINTER(wintypes.HANDLE),
            ctypes.POINTER(SECURITY_ATTRIBUTES),
            wintypes.DWORD,
        ]
        kernel32.CreatePipe.restype = wintypes.BOOL
        kernel32.SetHandleInformation.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD]
        kernel32.SetHandleInformation.restype = wintypes.BOOL
        kernel32.CreatePseudoConsole.argtypes = [
            COORD,
            wintypes.HANDLE,
            wintypes.HANDLE,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        kernel32.CreatePseudoConsole.restype = ctypes.c_long
        kernel32.InitializeProcThreadAttributeList.argtypes = [
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        kernel32.InitializeProcThreadAttributeList.restype = wintypes.BOOL
        kernel32.UpdateProcThreadAttribute.argtypes = [
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.c_size_t,
            wintypes.LPVOID,
            ctypes.c_size_t,
            wintypes.LPVOID,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        kernel32.UpdateProcThreadAttribute.restype = wintypes.BOOL
        kernel32.DeleteProcThreadAttributeList.argtypes = [wintypes.LPVOID]
        kernel32.DeleteProcThreadAttributeList.restype = None
        kernel32.CreateProcessW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPWSTR,
            wintypes.LPVOID,
            wintypes.LPVOID,
            wintypes.BOOL,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.LPCWSTR,
            ctypes.POINTER(STARTUPINFOEXW),
            ctypes.POINTER(PROCESS_INFORMATION),
        ]
        kernel32.CreateProcessW.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.ClosePseudoConsole.argtypes = [ctypes.c_void_p]
        kernel32.ClosePseudoConsole.restype = None

        def _raise_last_error(action: str) -> None:
            error_code = ctypes.get_last_error() or 1
            raise OSError(error_code, f"{action} (Windows error {error_code})")

        input_read = wintypes.HANDLE()
        input_write = wintypes.HANDLE()
        output_read = wintypes.HANDLE()
        output_write = wintypes.HANDLE()
        pseudo_console = ctypes.c_void_p()
        attribute_buffer: ctypes.Array[ctypes.c_char] | None = None
        attribute_list_initialized = False
        startup_info = STARTUPINFOEXW()
        process_info = PROCESS_INFORMATION()
        started = False

        def _handle_value(handle: object) -> int | None:
            raw_handle = getattr(handle, "value", handle)
            if raw_handle in (None, 0):
                return None
            return int(raw_handle)

        try:
            security_attributes = SECURITY_ATTRIBUTES()
            security_attributes.nLength = ctypes.sizeof(SECURITY_ATTRIBUTES)
            security_attributes.bInheritHandle = True
            security_attributes.lpSecurityDescriptor = None

            if not kernel32.CreatePipe(
                ctypes.byref(input_read),
                ctypes.byref(input_write),
                ctypes.byref(security_attributes),
                0,
            ):
                _raise_last_error("Failed to create ConPTY input pipe")
            if not kernel32.CreatePipe(
                ctypes.byref(output_read),
                ctypes.byref(output_write),
                ctypes.byref(security_attributes),
                0,
            ):
                _raise_last_error("Failed to create ConPTY output pipe")
            if not kernel32.SetHandleInformation(input_write, self._HANDLE_FLAG_INHERIT, 0):
                _raise_last_error("Failed to mark ConPTY input pipe non-inheritable")
            if not kernel32.SetHandleInformation(output_read, self._HANDLE_FLAG_INHERIT, 0):
                _raise_last_error("Failed to mark ConPTY output pipe non-inheritable")

            size = COORD(self._cols, self._rows)
            result = kernel32.CreatePseudoConsole(size, input_read, output_write, 0, ctypes.byref(pseudo_console))
            if result != 0:
                raise OSError(int(result), f"Failed to create ConPTY session (HRESULT {int(result)})")

            attribute_list_size = ctypes.c_size_t(0)
            kernel32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(attribute_list_size))
            if attribute_list_size.value <= 0:
                _raise_last_error("Failed to size ConPTY attribute list")
            attribute_buffer = ctypes.create_string_buffer(attribute_list_size.value)
            startup_info.lpAttributeList = ctypes.cast(attribute_buffer, wintypes.LPVOID)
            if not kernel32.InitializeProcThreadAttributeList(
                startup_info.lpAttributeList,
                1,
                0,
                ctypes.byref(attribute_list_size),
            ):
                _raise_last_error("Failed to initialize ConPTY attribute list")
            attribute_list_initialized = True
            if not kernel32.UpdateProcThreadAttribute(
                startup_info.lpAttributeList,
                0,
                self._PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE,
                pseudo_console,
                ctypes.sizeof(pseudo_console),
                None,
                None,
            ):
                _raise_last_error("Failed to attach ConPTY to child process")

            startup_info.StartupInfo.cb = ctypes.sizeof(STARTUPINFOEXW)
            command_line = subprocess.list2cmdline([self._program, *self._arguments])
            command_line_buffer = ctypes.create_unicode_buffer(command_line)
            working_directory = self._working_directory or None
            if not kernel32.CreateProcessW(
                self._program,
                command_line_buffer,
                None,
                None,
                False,
                self._EXTENDED_STARTUPINFO_PRESENT,
                None,
                working_directory,
                ctypes.byref(startup_info),
                ctypes.byref(process_info),
            ):
                _raise_last_error("Failed to launch local shell inside ConPTY")
            started = True

            pseudo_console_value = _handle_value(pseudo_console)
            input_write_value = _handle_value(input_write)
            output_read_value = _handle_value(output_read)
            process_handle_value = _handle_value(process_info.hProcess)
            if (
                pseudo_console_value is None
                or input_write_value is None
                or output_read_value is None
                or process_handle_value is None
            ):
                raise OSError("ConPTY startup returned an invalid handle.")

            self._pseudo_console = ctypes.c_void_p(pseudo_console_value)
            self._input_write_handle = wintypes.HANDLE(input_write_value)
            self._output_read_handle = wintypes.HANDLE(output_read_value)
            self._process_handle = wintypes.HANDLE(process_handle_value)
            self._process_id = int(process_info.dwProcessId)
        finally:
            if attribute_list_initialized and startup_info.lpAttributeList:
                try:
                    kernel32.DeleteProcThreadAttributeList(startup_info.lpAttributeList)
                except Exception:
                    pass
            if process_info.hThread:
                self._close_handle(process_info.hThread)
            self._close_handle(input_read)
            self._close_handle(output_write)
            if not started:
                self._close_handle(input_write)
                self._close_handle(output_read)
                if pseudo_console.value:
                    self._close_pseudo_console(pseudo_console)

    def bytes_available(self) -> int:
        handle = self._output_read_handle
        if handle is None:
            return 0
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.PeekNamedPipe.argtypes = [
            wintypes.HANDLE,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.LPVOID,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPVOID,
        ]
        kernel32.PeekNamedPipe.restype = wintypes.BOOL
        available = wintypes.DWORD()
        ok = kernel32.PeekNamedPipe(
            handle,
            None,
            0,
            None,
            ctypes.byref(available),
            None,
        )
        if ok:
            return int(available.value)
        error_code = ctypes.get_last_error()
        if error_code in {self._ERROR_BROKEN_PIPE, self._ERROR_NO_DATA, self._ERROR_PIPE_NOT_CONNECTED}:
            return 0
        raise OSError(error_code, f"ConPTY output poll failed (Windows error {error_code})")

    def read(self, max_bytes: int) -> bytes:
        handle = self._output_read_handle
        if handle is None:
            return b""
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.ReadFile.argtypes = [
            wintypes.HANDLE,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPVOID,
        ]
        kernel32.ReadFile.restype = wintypes.BOOL
        size = max(1, int(max_bytes))
        buffer = ctypes.create_string_buffer(size)
        read_size = wintypes.DWORD()
        ok = kernel32.ReadFile(
            handle,
            buffer,
            size,
            ctypes.byref(read_size),
            None,
        )
        if ok:
            return buffer.raw[: read_size.value]
        error_code = ctypes.get_last_error()
        if error_code in {self._ERROR_BROKEN_PIPE, self._ERROR_NO_DATA, self._ERROR_PIPE_NOT_CONNECTED}:
            return b""
        raise OSError(error_code, f"ConPTY output read failed (Windows error {error_code})")

    def write(self, payload: bytes) -> None:
        handle = self._input_write_handle
        if handle is None or not payload:
            return
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.WriteFile.argtypes = [
            wintypes.HANDLE,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPVOID,
        ]
        kernel32.WriteFile.restype = wintypes.BOOL
        view = memoryview(payload)
        while len(view):
            chunk = bytes(view)
            chunk_buffer = ctypes.create_string_buffer(chunk)
            written = wintypes.DWORD()
            ok = kernel32.WriteFile(
                handle,
                chunk_buffer,
                len(chunk),
                ctypes.byref(written),
                None,
            )
            if not ok:
                error_code = ctypes.get_last_error()
                if error_code in {self._ERROR_BROKEN_PIPE, self._ERROR_NO_DATA, self._ERROR_PIPE_NOT_CONNECTED}:
                    return
                raise OSError(error_code, f"ConPTY input write failed (Windows error {error_code})")
            if written.value <= 0:
                return
            view = view[written.value :]

    def resize(self, cols: int, rows: int) -> None:
        pseudo_console = self._pseudo_console
        if pseudo_console is None:
            return
        self._cols = max(10, cols)
        self._rows = max(5, rows)
        import ctypes

        class COORD(ctypes.Structure):
            _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.ResizePseudoConsole.argtypes = [ctypes.c_void_p, COORD]
        kernel32.ResizePseudoConsole.restype = ctypes.c_long
        result = kernel32.ResizePseudoConsole(pseudo_console, COORD(self._cols, self._rows))
        if result != 0:
            raise OSError(int(result), f"ConPTY resize failed (HRESULT {int(result)})")

    def has_exited(self) -> bool:
        handle = self._process_handle
        if handle is None:
            return True
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        result = kernel32.WaitForSingleObject(handle, 0)
        if result == self._WAIT_OBJECT_0:
            return True
        if result == self._WAIT_TIMEOUT:
            return False
        error_code = ctypes.get_last_error() or int(result)
        raise OSError(error_code, f"ConPTY process wait failed (Windows error {error_code})")

    def close(self) -> None:
        process_handle = self._process_handle
        self._process_handle = None
        if process_handle is not None:
            try:
                import ctypes
                from ctypes import wintypes

                kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
                kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
                kernel32.WaitForSingleObject.restype = wintypes.DWORD
                kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
                kernel32.TerminateProcess.restype = wintypes.BOOL
                if kernel32.WaitForSingleObject(process_handle, 200) == self._WAIT_TIMEOUT:
                    input_handle = self._input_write_handle
                    self._input_write_handle = None
                    self._close_handle(input_handle)
                    if kernel32.WaitForSingleObject(process_handle, 800) == self._WAIT_TIMEOUT:
                        kernel32.TerminateProcess(process_handle, 1)
                kernel32.WaitForSingleObject(process_handle, 200)
            except Exception:
                pass
            self._close_handle(process_handle)

        if self._input_write_handle is not None:
            self._close_handle(self._input_write_handle)
            self._input_write_handle = None
        if self._output_read_handle is not None:
            self._close_handle(self._output_read_handle)
            self._output_read_handle = None
        if self._pseudo_console is not None:
            self._close_pseudo_console(self._pseudo_console)
            self._pseudo_console = None
        self._process_id = None

    @staticmethod
    def _close_handle(handle) -> None:
        raw_handle = getattr(handle, "value", handle)
        if raw_handle in (None, 0):
            return
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            kernel32.CloseHandle(handle)
        except Exception:
            pass

    @staticmethod
    def _close_pseudo_console(handle) -> None:
        raw_handle = getattr(handle, "value", handle)
        if raw_handle in (None, 0):
            return
        try:
            import ctypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.ClosePseudoConsole.argtypes = [ctypes.c_void_p]
            kernel32.ClosePseudoConsole.restype = None
            kernel32.ClosePseudoConsole(handle)
        except Exception:
            pass


class WindowsLocalShellWorker(QObject):
    output = Signal(str)
    error = Signal(str)
    connected = Signal()
    closed = Signal()

    _READ_SIZE = 16384
    _POLL_INTERVAL_SECONDS = 0.05

    def __init__(
        self,
        *,
        program: str,
        arguments: list[str],
        working_directory: str | None,
        cols: int,
        rows: int,
        uses_basic_process_io: bool = False,
        backend_factory: Callable[..., _WindowsTerminalBackend] | None = None,
    ) -> None:
        super().__init__()
        self._program = program
        self._arguments = list(arguments)
        self._working_directory = working_directory
        self._cols = max(10, cols)
        self._rows = max(5, rows)
        self._uses_basic_process_io = uses_basic_process_io
        self._backend_factory = backend_factory or _WindowsConPTYBackend
        self._backend: _WindowsTerminalBackend | None = None
        self._stop_requested = False
        self._output_paused = False
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._decoder_flushed = False
        self._command_queue: queue.Queue[
            tuple[str, str] | tuple[str, int, int] | None
        ] = queue.Queue()

    @classmethod
    def is_supported(cls) -> bool:
        return _WindowsConPTYBackend.is_supported()

    def uses_basic_process_io(self) -> bool:
        return self._uses_basic_process_io

    @Slot()
    def start(self) -> None:
        if os.name != "nt":
            self.error.emit("Windows local-shell mode is only supported on Windows.")
            self.closed.emit()
            return
        try:
            backend = self._backend_factory(
                program=self._program,
                arguments=self._arguments,
                working_directory=self._working_directory,
                cols=self._cols,
                rows=self._rows,
            )
            backend.start()
            self._backend = backend
        except Exception as exc:
            self.error.emit(f"Failed to start local shell console: {exc}")
            self.closed.emit()
            return

        self.connected.emit()
        try:
            self._run_io_loop()
        except Exception as exc:
            self.error.emit(f"Local shell loop failed: {exc}")
        finally:
            self._flush_decoder()
            backend = self._backend
            self._backend = None
            if backend is not None:
                backend.close()
            self.closed.emit()

    def _run_io_loop(self) -> None:
        while not self._stop_requested:
            self._drain_commands()
            if self._stop_requested:
                break

            backend = self._backend
            if backend is None:
                break
            if self._output_paused:
                if backend.has_exited():
                    break
                time.sleep(self._POLL_INTERVAL_SECONDS)
                continue

            available = backend.bytes_available()
            if available > 0:
                payload = backend.read(min(self._READ_SIZE, available))
                if payload:
                    self._emit_decoded_output(payload)
                    continue

            if backend.has_exited():
                while True:
                    available = backend.bytes_available()
                    if available <= 0:
                        break
                    payload = backend.read(min(self._READ_SIZE, available))
                    if not payload:
                        break
                    self._emit_decoded_output(payload)
                break

            time.sleep(self._POLL_INTERVAL_SECONDS)

    def _drain_commands(self) -> None:
        while True:
            try:
                command = self._command_queue.get_nowait()
            except queue.Empty:
                return

            if command is None:
                self._stop_requested = True
                return

            action = command[0]
            if action == "input":
                self._write_to_console(command[1])
            elif action == "resize":
                self._apply_terminal_size(command[1], command[2])

    def _write_to_console(self, text: str) -> None:
        backend = self._backend
        if backend is None or not text:
            return
        backend.write(text.encode("utf-8", errors="replace"))

    def _apply_terminal_size(self, cols: int, rows: int) -> None:
        self._cols = max(10, cols)
        self._rows = max(5, rows)
        backend = self._backend
        if backend is None:
            return
        backend.resize(self._cols, self._rows)

    def _emit_decoded_output(self, payload: bytes) -> None:
        text = self._decoder.decode(payload, final=False)
        if text:
            self.output.emit(text)

    def _flush_decoder(self) -> None:
        if self._decoder_flushed:
            return
        self._decoder_flushed = True
        text = self._decoder.decode(b"", final=True)
        if text:
            self.output.emit(text)

    @Slot(str)
    def send_text(self, text: str) -> None:
        if not text:
            return
        if self._backend is None:
            self.error.emit("Shell is not ready.")
            return
        self._command_queue.put(("input", text))

    @Slot(int, int)
    def resize_terminal(self, cols: int, rows: int) -> None:
        self._cols = max(10, cols)
        self._rows = max(5, rows)
        if self._backend is None:
            return
        self._command_queue.put(("resize", self._cols, self._rows))

    def pause_output(self) -> None:
        self._output_paused = True

    def resume_output(self) -> None:
        self._output_paused = False

    def stop(self) -> None:
        self._stop_requested = True
        try:
            self._command_queue.put_nowait(None)
        except Exception:
            pass


class SSHShellWorker(QObject):
    output = Signal(str)
    error = Signal(str)
    connected = Signal()
    closed = Signal()
    _CLEANUP_DEADLINE_SECONDS = 2.5

    def __init__(
        self,
        session: Session,
        password: str | None,
        trust_unknown: bool,
        x11_forwarding: bool,
        cols: int,
        rows: int,
    ) -> None:
        super().__init__()
        self._session = session
        self._password = password
        self._trust_unknown = trust_unknown
        self._x11_forwarding = x11_forwarding
        self._cols = cols
        self._rows = rows
        self._loop: asyncio.AbstractEventLoop | None = None
        self._conn: asyncssh.SSHClientConnection | None = None
        self._proc: asyncssh.SSHClientProcess | None = None
        self._queue: asyncio.Queue[bytes | None] | None = None
        self._tunnel_listeners: list[object] = []
        self._main_task: asyncio.Task[None] | None = None
        self._stop_requested = False
        self._output_paused = False
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._decoder_flushed = False

    @Slot()
    def start(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._main_task = self._loop.create_task(self._run())
            self._loop.run_until_complete(self._main_task)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self.error.emit(str(exc))
        finally:
            try:
                self._loop.run_until_complete(self._cleanup_remote_session())
            except Exception:
                pass
            self._flush_decoder()
            self._main_task = None
            self._queue = None
            if self._loop.is_running():
                self._loop.stop()
            self._loop.close()
            self.closed.emit()

    async def _run(self) -> None:
        connect_kwargs = SSHClient._connect_kwargs(self._session, password=self._password)
        connect_kwargs["x11_forwarding"] = self._x11_forwarding
        connect_kwargs["known_hosts"] = None if self._trust_unknown else str(known_hosts_path())

        if self._session.ssh_legacy_compatibility:
            self.output.emit("SSH compatibility mode enabled for this session.\r\n")
            self._conn = await asyncssh.connect(**SSHClient.apply_legacy_algorithm_overrides(connect_kwargs))
        else:
            try:
                self._conn = await asyncssh.connect(**connect_kwargs)
            except Exception as exc:
                if not SSHClient.is_legacy_negotiation_error(exc):
                    raise
                self.output.emit("SSH compatibility mode enabled: retrying with legacy algorithms...\r\n")
                self._conn = await asyncssh.connect(**SSHClient.apply_legacy_algorithm_overrides(connect_kwargs))
        if self._trust_unknown:
            trust_host_key(self._session, self._conn.get_server_host_key())

        await self._start_configured_tunnels()
        self._proc = await self._conn.create_process(
            term_type="xterm-256color",
            term_size=(self._cols, self._rows),
            encoding=None,
        )
        try:
            self._proc.channel.change_terminal_size(self._cols, self._rows)
        except Exception as exc:
            self.error.emit(f"Initial terminal resize failed: {exc}")
        self._queue = asyncio.Queue()
        self.connected.emit()

        read_task = asyncio.create_task(self._read_stream())
        write_task = asyncio.create_task(self._write_stream())
        done: set[asyncio.Task[None]] = set()
        try:
            done, _pending = await asyncio.wait(
                [read_task, write_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in done:
                try:
                    err = task.exception()
                except asyncio.CancelledError:
                    continue
                if err:
                    self.error.emit(str(err))
        finally:
            for task in (read_task, write_task):
                if task not in done and not task.done():
                    task.cancel()
            for task in (read_task, write_task):
                if task in done:
                    continue
                try:
                    await task
                except asyncio.CancelledError:
                    continue
                except Exception as exc:
                    self.error.emit(str(exc))

    async def _read_stream(self) -> None:
        assert self._proc is not None
        while True:
            while self._output_paused and not self._stop_requested:
                await asyncio.sleep(0.05)
            if self._stop_requested:
                break
            chunk = await self._proc.stdout.read(16384)
            if not chunk:
                break
            self._emit_decoded_output(chunk)

    async def _write_stream(self) -> None:
        assert self._queue is not None
        assert self._proc is not None
        while True:
            payload = await self._queue.get()
            if payload is None:
                break
            self._proc.stdin.write(payload)

    def _emit_decoded_output(self, payload: bytes) -> None:
        text = self._decoder.decode(payload, final=False)
        if text:
            self.output.emit(text)

    def _flush_decoder(self) -> None:
        if self._decoder_flushed:
            return
        self._decoder_flushed = True
        text = self._decoder.decode(b"", final=True)
        if text:
            self.output.emit(text)

    @Slot(str)
    def send_text(self, text: str) -> None:
        if not self._loop or not self._queue or self._loop.is_closed():
            self.error.emit("Shell is not ready.")
            return
        try:
            payload = text.encode("utf-8", errors="replace")
            asyncio.run_coroutine_threadsafe(self._queue.put(payload), self._loop)
        except RuntimeError:
            self.error.emit("Shell loop is closed.")

    @Slot(int, int)
    def resize_terminal(self, cols: int, rows: int) -> None:
        self._cols = cols
        self._rows = rows
        if not self._loop or self._loop.is_closed():
            return
        try:
            asyncio.run_coroutine_threadsafe(self._resize_terminal(), self._loop)
        except RuntimeError:
            return

    def pause_output(self) -> None:
        self._output_paused = True

    def resume_output(self) -> None:
        self._output_paused = False

    async def _resize_terminal(self) -> None:
        if not self._proc:
            return
        try:
            # Resize remote PTY through the underlying SSH channel.
            self._proc.channel.change_terminal_size(self._cols, self._rows)
        except Exception as exc:
            self.error.emit(f"Terminal resize failed: {exc}")

    def stop(self) -> None:
        self._stop_requested = True
        if not self._loop or self._loop.is_closed():
            return
        try:
            self._loop.call_soon_threadsafe(self._request_stop_in_loop)
        except RuntimeError:
            return

    def _request_stop_in_loop(self) -> None:
        queue = self._queue
        if queue is not None:
            try:
                queue.put_nowait(None)
            except Exception:
                pass
        proc = self._proc
        if proc is not None:
            self._close_ssh_process(proc, abrupt=True)
        conn = self._conn
        if conn is not None:
            self._abort_asyncssh_resource(conn)
        main_task = self._main_task
        if main_task is not None and not main_task.done():
            main_task.cancel()

    async def _start_configured_tunnels(self) -> None:
        conn = self._conn
        if conn is None:
            return

        for tunnel in self._session.ssh_dynamic_tunnels:
            if not tunnel.enabled:
                continue
            try:
                socks_forwarder = await conn.forward_socks(tunnel.bind_host, tunnel.bind_port)
                self._tunnel_listeners.append(socks_forwarder)
                self.output.emit(f"Dynamic tunnel active: SOCKS {tunnel.bind_host}:{tunnel.bind_port}\r\n")
            except AttributeError:
                self.error.emit("Dynamic SOCKS forwarding is not supported by this AsyncSSH build.")
                break
            except Exception as exc:
                self.error.emit(
                    f"Dynamic tunnel failed ({tunnel.bind_host}:{tunnel.bind_port}): {exc}"
                )

        for tunnel in self._session.ssh_static_tunnels:
            if not tunnel.enabled:
                continue
            try:
                if tunnel.direction == "remote":
                    forwarder = await conn.forward_remote_port(
                        tunnel.bind_host,
                        tunnel.bind_port,
                        tunnel.target_host,
                        tunnel.target_port,
                    )
                    direction_name = "Remote"
                else:
                    forwarder = await conn.forward_local_port(
                        tunnel.bind_host,
                        tunnel.bind_port,
                        tunnel.target_host,
                        tunnel.target_port,
                    )
                    direction_name = "Local"
                self._tunnel_listeners.append(forwarder)
                self.output.emit(
                    f"{direction_name} tunnel active: {tunnel.bind_host}:{tunnel.bind_port} "
                    f"-> {tunnel.target_host}:{tunnel.target_port}\r\n"
                )
            except Exception as exc:
                self.error.emit(
                    f"Static tunnel failed ({tunnel.bind_host}:{tunnel.bind_port} "
                    f"-> {tunnel.target_host}:{tunnel.target_port}): {exc}"
                )

    async def _close_tunnels(self, *, deadline: float | None = None) -> None:
        if not self._tunnel_listeners:
            return
        listeners = list(self._tunnel_listeners)
        self._tunnel_listeners.clear()
        for listener in listeners:
            close = getattr(listener, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    continue
        for listener in listeners:
            wait_closed = getattr(listener, "wait_closed", None)
            if callable(wait_closed):
                try:
                    if deadline is None:
                        await wait_closed()
                        continue
                    timeout = max(0.0, deadline - time.monotonic())
                    if timeout <= 0:
                        return
                    await asyncio.wait_for(wait_closed(), timeout=timeout)
                except Exception:
                    continue

    async def _cleanup_remote_session(self) -> None:
        deadline = time.monotonic() + self._CLEANUP_DEADLINE_SECONDS
        proc = self._proc
        self._proc = None
        if proc is not None:
            self._close_ssh_process(proc, abrupt=self._stop_requested)
            await self._wait_closed_with_deadline(proc, deadline=deadline, abort_targets=(getattr(proc, "channel", None), proc))
        await self._close_tunnels(deadline=deadline)
        conn = self._conn
        self._conn = None
        if conn is not None:
            if self._stop_requested:
                self._abort_asyncssh_resource(conn)
            else:
                self._close_asyncssh_resource(conn)
            await self._wait_closed_with_deadline(conn, deadline=deadline, abort_targets=(conn,))

    async def _wait_closed_with_deadline(
        self,
        target: object,
        *,
        deadline: float,
        abort_targets: tuple[object | None, ...],
    ) -> None:
        wait_closed = getattr(target, "wait_closed", None)
        if not callable(wait_closed):
            return
        timeout = max(0.0, deadline - time.monotonic())
        if timeout <= 0:
            for abort_target in abort_targets:
                self._abort_asyncssh_resource(abort_target)
            return
        try:
            await asyncio.wait_for(wait_closed(), timeout=timeout)
            return
        except asyncio.TimeoutError:
            for abort_target in abort_targets:
                self._abort_asyncssh_resource(abort_target)
        except Exception:
            return
        timeout = max(0.0, deadline - time.monotonic())
        if timeout <= 0:
            return
        try:
            await asyncio.wait_for(wait_closed(), timeout=timeout)
        except Exception:
            return

    @staticmethod
    def _close_asyncssh_resource(target: object | None) -> None:
        if target is None:
            return
        close = getattr(target, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                return

    @classmethod
    def _abort_asyncssh_resource(cls, target: object | None) -> None:
        if target is None:
            return
        abort = getattr(target, "abort", None)
        if callable(abort):
            try:
                abort()
                return
            except Exception:
                pass
        cls._close_asyncssh_resource(target)

    @classmethod
    def _close_ssh_process(cls, proc: object, *, abrupt: bool) -> None:
        stdin = getattr(proc, "stdin", None)
        if stdin is not None:
            write_eof = getattr(stdin, "write_eof", None)
            if callable(write_eof):
                try:
                    write_eof()
                except Exception:
                    pass
        cls._close_asyncssh_resource(proc)
        channel = getattr(proc, "channel", None)
        if abrupt:
            cls._abort_asyncssh_resource(channel)
            cls._abort_asyncssh_resource(proc)
        else:
            cls._close_asyncssh_resource(channel)


class TelnetShellWorker(QObject):
    output = Signal(str)
    error = Signal(str)
    connected = Signal()
    closed = Signal()

    IAC = 255
    DONT = 254
    DO = 253
    WONT = 252
    WILL = 251
    SB = 250
    SE = 240

    OPT_BINARY = 0
    OPT_ECHO = 1
    OPT_SUPPRESS_GO_AHEAD = 3
    OPT_TERMINAL_TYPE = 24
    OPT_NAWS = 31

    TTYPE_IS = 0
    TTYPE_SEND = 1

    _READ_SIZE = 16384
    _SELECT_TIMEOUT_SECONDS = 0.05

    def __init__(
        self,
        *,
        host: str,
        port: int,
        terminal_type: str,
        connect_timeout_seconds: float,
        use_tls: bool,
        tls_verify: bool,
        cols: int,
        rows: int,
    ) -> None:
        super().__init__()
        self._host = host.strip()
        self._port = max(1, min(65535, int(port or 23)))
        self._terminal_type = terminal_type.strip() or "xterm-256color"
        self._connect_timeout_seconds = max(1.0, min(120.0, float(connect_timeout_seconds)))
        self._use_tls = bool(use_tls)
        self._tls_verify = bool(tls_verify)
        self._cols = max(10, cols)
        self._rows = max(5, rows)
        self._socket: socket.socket | None = None
        self._stop_requested = False
        self._output_paused = False
        self._command_queue: queue.Queue[tuple[str, object, object] | None] = queue.Queue()

        self._iac_pending = False
        self._iac_command: int | None = None
        self._subnegotiation_active = False
        self._subnegotiation_option: int | None = None
        self._subnegotiation_buffer = bytearray()
        self._subnegotiation_iac_pending = False
        self._naws_enabled = False
        # Legacy MUD/BBS services often rely on client-side local echo unless
        # they explicitly negotiate remote echo.
        self._echo_local_requested = True
        self._echo_remote_enabled = False
        self._local_echo_enabled = False
        self._refresh_local_echo_state()

    @Slot()
    def start(self) -> None:
        if not self._host:
            self.error.emit("Telnet host is not configured.")
            self.closed.emit()
            return
        try:
            sock = socket.create_connection((self._host, self._port), timeout=self._connect_timeout_seconds)
            if self._use_tls:
                context = ssl.create_default_context()
                if not self._tls_verify:
                    context.check_hostname = False
                    context.verify_mode = ssl.CERT_NONE
                server_hostname = self._host if self._tls_verify else None
                sock = context.wrap_socket(sock, server_hostname=server_hostname)
            sock.setblocking(False)
        except ssl.SSLError as exc:
            self.error.emit(f"Telnet TLS handshake failed: {exc}")
            self.closed.emit()
            return
        except Exception as exc:
            self.error.emit(f"Telnet connect failed: {exc}")
            self.closed.emit()
            return
        self._socket = sock
        self.connected.emit()
        try:
            self._run_io_loop()
        except Exception as exc:
            self.error.emit(f"Telnet loop failed: {exc}")
        finally:
            self._cleanup_socket()
            self.closed.emit()

    def _run_io_loop(self) -> None:
        while not self._stop_requested:
            self._drain_commands()
            if self._stop_requested:
                break
            sock = self._socket
            if sock is None:
                break
            if self._output_paused:
                time.sleep(self._SELECT_TIMEOUT_SECONDS)
                continue
            try:
                readable, _writable, _errors = select.select([sock], [], [], self._SELECT_TIMEOUT_SECONDS)
            except (OSError, ValueError):
                break
            if not readable:
                continue
            try:
                payload = sock.recv(self._READ_SIZE)
            except ssl.SSLWantReadError:
                continue
            except ssl.SSLWantWriteError:
                time.sleep(0.005)
                continue
            except BlockingIOError:
                continue
            except OSError as exc:
                if exc.errno in {errno.EBADF, errno.ENOTCONN, errno.ECONNRESET, errno.ECONNABORTED, errno.EPIPE}:
                    break
                self.error.emit(f"Telnet read failed: {exc}")
                break
            if not payload:
                break
            parsed = self._consume_telnet_bytes(payload)
            if parsed:
                self.output.emit(parsed.decode("utf-8", errors="replace"))

    def _drain_commands(self) -> None:
        while True:
            try:
                command = self._command_queue.get_nowait()
            except queue.Empty:
                return
            if command is None:
                self._stop_requested = True
                return
            action = command[0]
            if action == "input":
                value = command[1]
                if isinstance(value, str):
                    self._send_bytes(value.encode("utf-8", errors="replace"))
                    if self._local_echo_enabled and not self._stop_requested:
                        echoed = self._format_local_echo(value)
                        if echoed:
                            self.output.emit(echoed)
            elif action == "resize":
                cols = command[1]
                rows = command[2]
                if isinstance(cols, int) and isinstance(rows, int):
                    self._apply_terminal_size(cols, rows)

    def _consume_telnet_bytes(self, payload: bytes) -> bytes:
        output = bytearray()
        for byte in payload:
            if self._subnegotiation_active:
                if self._subnegotiation_option is None:
                    self._subnegotiation_option = byte
                    continue
                if self._subnegotiation_iac_pending:
                    if byte == self.IAC:
                        self._subnegotiation_buffer.append(self.IAC)
                        self._subnegotiation_iac_pending = False
                        continue
                    if byte == self.SE:
                        self._handle_subnegotiation(self._subnegotiation_option, bytes(self._subnegotiation_buffer))
                        self._subnegotiation_active = False
                        self._subnegotiation_option = None
                        self._subnegotiation_buffer.clear()
                        self._subnegotiation_iac_pending = False
                        continue
                    self._subnegotiation_iac_pending = False
                    continue
                if byte == self.IAC:
                    self._subnegotiation_iac_pending = True
                else:
                    self._subnegotiation_buffer.append(byte)
                continue

            if self._iac_command is not None:
                self._handle_negotiation(self._iac_command, byte)
                self._iac_command = None
                continue

            if self._iac_pending:
                self._iac_pending = False
                if byte == self.IAC:
                    output.append(self.IAC)
                elif byte in (self.DO, self.DONT, self.WILL, self.WONT):
                    self._iac_command = byte
                elif byte == self.SB:
                    self._subnegotiation_active = True
                    self._subnegotiation_option = None
                    self._subnegotiation_buffer.clear()
                    self._subnegotiation_iac_pending = False
                continue

            if byte == self.IAC:
                self._iac_pending = True
                continue
            output.append(byte)
        return bytes(output)

    def _handle_negotiation(self, command: int, option: int) -> None:
        if command == self.DO:
            allow = option in {
                self.OPT_BINARY,
                self.OPT_ECHO,
                self.OPT_SUPPRESS_GO_AHEAD,
                self.OPT_TERMINAL_TYPE,
                self.OPT_NAWS,
            }
            self._send_iac(self.WILL if allow else self.WONT, option)
            if option == self.OPT_ECHO:
                self._echo_local_requested = allow
                self._refresh_local_echo_state()
            if option == self.OPT_NAWS:
                self._naws_enabled = allow
                if allow:
                    self._send_naws()
            return
        if command == self.DONT:
            self._send_iac(self.WONT, option)
            if option == self.OPT_ECHO:
                self._echo_local_requested = False
                self._refresh_local_echo_state()
            if option == self.OPT_NAWS:
                self._naws_enabled = False
            return
        if command == self.WILL:
            allow = option in {self.OPT_ECHO, self.OPT_SUPPRESS_GO_AHEAD, self.OPT_BINARY}
            self._send_iac(self.DO if allow else self.DONT, option)
            if option == self.OPT_ECHO:
                self._echo_remote_enabled = allow
                self._refresh_local_echo_state()
            return
        if command == self.WONT:
            self._send_iac(self.DONT, option)
            if option == self.OPT_ECHO:
                self._echo_remote_enabled = False
                self._refresh_local_echo_state()

    def _handle_subnegotiation(self, option: int | None, payload: bytes) -> None:
        if option != self.OPT_TERMINAL_TYPE:
            return
        if not payload or payload[0] != self.TTYPE_SEND:
            return
        terminal = self._terminal_type.encode("ascii", errors="replace")
        response = bytes([self.IAC, self.SB, self.OPT_TERMINAL_TYPE, self.TTYPE_IS]) + terminal + bytes(
            [self.IAC, self.SE]
        )
        self._send_bytes(response)

    def _send_iac(self, command: int, option: int) -> None:
        self._send_bytes(bytes([self.IAC, command, option]))

    def _refresh_local_echo_state(self) -> None:
        # Local echo is only safe when the peer explicitly requests it and
        # isn't already echoing our input back.
        self._local_echo_enabled = self._echo_local_requested and not self._echo_remote_enabled

    @staticmethod
    def _format_local_echo(text: str) -> str:
        if not text:
            return ""
        rendered: list[str] = []
        for ch in text:
            if ch == "\r" or ch == "\n":
                rendered.append("\r\n")
                continue
            if ch in ("\b", "\x7f"):
                rendered.append("\b \b")
                continue
            if ch == "\t":
                rendered.append("\t")
                continue
            if ord(ch) >= 0x20:
                rendered.append(ch)
        return "".join(rendered)

    def _send_naws(self) -> None:
        if not self._naws_enabled:
            return
        cols = max(1, min(65535, self._cols))
        rows = max(1, min(65535, self._rows))
        payload = bytes(
            [
                self.IAC,
                self.SB,
                self.OPT_NAWS,
                (cols >> 8) & 0xFF,
                cols & 0xFF,
                (rows >> 8) & 0xFF,
                rows & 0xFF,
                self.IAC,
                self.SE,
            ]
        )
        self._send_bytes(payload)

    def _send_bytes(self, payload: bytes) -> None:
        sock = self._socket
        if sock is None or not payload:
            return
        view = memoryview(payload)
        while len(view) and not self._stop_requested:
            try:
                sent = sock.send(view)
            except ssl.SSLWantReadError:
                time.sleep(0.005)
                continue
            except ssl.SSLWantWriteError:
                time.sleep(0.005)
                continue
            except BlockingIOError:
                time.sleep(0.005)
                continue
            except OSError as exc:
                if exc.errno in {errno.EBADF, errno.ENOTCONN, errno.ECONNRESET, errno.ECONNABORTED, errno.EPIPE}:
                    self._stop_requested = True
                    return
                self.error.emit(f"Telnet write failed: {exc}")
                self._stop_requested = True
                return
            if sent <= 0:
                self._stop_requested = True
                return
            view = view[sent:]

    def _apply_terminal_size(self, cols: int, rows: int) -> None:
        self._cols = max(10, cols)
        self._rows = max(5, rows)
        self._send_naws()

    def _cleanup_socket(self) -> None:
        sock = self._socket
        self._socket = None
        if sock is None:
            return
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        try:
            sock.close()
        except Exception:
            pass

    @Slot(str)
    def send_text(self, text: str) -> None:
        if not text:
            return
        if self._socket is None:
            self.error.emit("Telnet session is not ready.")
            return
        self._command_queue.put(("input", text, None))

    @Slot(int, int)
    def resize_terminal(self, cols: int, rows: int) -> None:
        self._cols = max(10, cols)
        self._rows = max(5, rows)
        if self._socket is None:
            return
        self._command_queue.put(("resize", self._cols, self._rows))

    def pause_output(self) -> None:
        self._output_paused = True

    def resume_output(self) -> None:
        self._output_paused = False

    def stop(self) -> None:
        self._stop_requested = True
        try:
            self._command_queue.put_nowait(None)
        except Exception:
            pass


class SerialShellWorker(QObject):
    output = Signal(str)
    error = Signal(str)
    connected = Signal()
    closed = Signal()

    _READ_SIZE = 16384
    _SELECT_TIMEOUT_SECONDS = 0.05

    def __init__(
        self,
        *,
        port: str,
        baud_rate: int,
        data_bits: int,
        parity: str,
        stop_bits: str,
        flow_control: str,
        cols: int,
        rows: int,
    ) -> None:
        super().__init__()
        self._port = port.strip()
        self._baud_rate = max(1, int(baud_rate or 9600))
        self._data_bits = data_bits
        self._parity = parity.strip().lower()
        self._stop_bits = stop_bits.strip()
        self._flow_control = flow_control.strip().lower()
        self._cols = max(10, cols)
        self._rows = max(5, rows)
        self._backend = ""
        self._serial = None
        self._fd: int | None = None
        self._stop_requested = False
        self._output_paused = False
        self._command_queue: queue.Queue[tuple[str, object, object] | None] = queue.Queue()

    @Slot()
    def start(self) -> None:
        if not self._port:
            self.error.emit("Serial port is not configured.")
            self.closed.emit()
            return
        opened = False
        if os.name == "posix":
            # Prefer native POSIX handling so Linux/macOS work without PySerial.
            opened = self._open_posix_serial()
            if not opened:
                serial_module, _serial_import_error = self._import_pyserial()
                if serial_module is not None:
                    opened = self._open_pyserial(serial_module)
            if not opened:
                self.closed.emit()
                return
        else:
            serial_module, serial_import_error = self._import_pyserial()
            if serial_module is None:
                if serial_import_error is None:
                    self.error.emit("PySerial is required for serial sessions on this platform.")
                else:
                    self.error.emit(f"PySerial is unavailable: {serial_import_error}")
                self.closed.emit()
                return
            opened = self._open_pyserial(serial_module)
            if not opened:
                self.closed.emit()
                return

        self.connected.emit()
        try:
            self._run_io_loop()
        except Exception as exc:
            self.error.emit(f"Serial loop failed: {exc}")
        finally:
            self._cleanup_serial()
            self.closed.emit()

    def _run_io_loop(self) -> None:
        if self._backend == "pyserial":
            self._run_pyserial_io_loop()
            return
        self._run_posix_io_loop()

    def _run_pyserial_io_loop(self) -> None:
        while not self._stop_requested:
            self._drain_commands()
            if self._stop_requested:
                break
            conn = self._serial
            if conn is None:
                break
            if self._output_paused:
                time.sleep(self._SELECT_TIMEOUT_SECONDS)
                continue
            try:
                waiting = int(getattr(conn, "in_waiting", 0))
            except Exception:
                waiting = 0
            read_size = min(self._READ_SIZE, waiting if waiting > 0 else 1)
            try:
                payload = conn.read(read_size)
            except Exception as exc:
                self.error.emit(f"Serial read failed: {exc}")
                break
            if payload:
                self.output.emit(payload.decode("utf-8", errors="replace"))

    def _run_posix_io_loop(self) -> None:
        while not self._stop_requested:
            self._drain_commands()
            if self._stop_requested:
                break
            fd = self._fd
            if fd is None:
                break
            if self._output_paused:
                time.sleep(self._SELECT_TIMEOUT_SECONDS)
                continue
            try:
                readable, _writable, _errors = select.select([fd], [], [], self._SELECT_TIMEOUT_SECONDS)
            except (OSError, ValueError):
                break
            if not readable:
                continue
            try:
                payload = os.read(fd, self._READ_SIZE)
            except BlockingIOError:
                payload = b""
            except OSError as exc:
                if exc.errno in {errno.EIO, errno.EBADF, errno.ENODEV}:
                    break
                self.error.emit(f"Serial read failed: {exc}")
                break
            if payload:
                self.output.emit(payload.decode("utf-8", errors="replace"))

    def _drain_commands(self) -> None:
        while True:
            try:
                command = self._command_queue.get_nowait()
            except queue.Empty:
                return
            if command is None:
                self._stop_requested = True
                return
            action = command[0]
            if action == "input":
                value = command[1]
                if isinstance(value, str):
                    self._write_serial(value)
            elif action == "resize":
                cols = command[1]
                rows = command[2]
                if isinstance(cols, int) and isinstance(rows, int):
                    self._cols = max(10, cols)
                    self._rows = max(5, rows)

    def _write_serial(self, text: str) -> None:
        if not text:
            return
        payload = text.encode("utf-8", errors="replace")
        conn = self._serial
        if conn is not None:
            try:
                conn.write(payload)
                conn.flush()
                return
            except Exception as exc:
                self.error.emit(f"Serial write failed: {exc}")
                self._stop_requested = True
                return
        fd = self._fd
        if fd is None:
            return
        view = payload
        while view and not self._stop_requested:
            try:
                written = os.write(fd, view)
            except BlockingIOError:
                time.sleep(0.005)
                continue
            except OSError as exc:
                if exc.errno in {errno.EIO, errno.EBADF, errno.ENODEV}:
                    self._stop_requested = True
                    return
                self.error.emit(f"Serial write failed: {exc}")
                self._stop_requested = True
                return
            if written <= 0:
                self._stop_requested = True
                return
            view = view[written:]

    @staticmethod
    def _import_pyserial():
        try:
            import serial as serial_module  # type: ignore[import-not-found]
        except Exception as exc:
            return None, exc
        return serial_module, None

    def _open_pyserial(self, serial_module) -> bool:
        bytesize = {
            5: serial_module.FIVEBITS,
            6: serial_module.SIXBITS,
            7: serial_module.SEVENBITS,
            8: serial_module.EIGHTBITS,
        }.get(self._data_bits, serial_module.EIGHTBITS)
        parity = {
            "none": serial_module.PARITY_NONE,
            "even": serial_module.PARITY_EVEN,
            "odd": serial_module.PARITY_ODD,
            "mark": serial_module.PARITY_MARK,
            "space": serial_module.PARITY_SPACE,
        }.get(self._parity, serial_module.PARITY_NONE)
        stopbits = {
            "1": serial_module.STOPBITS_ONE,
            "1.5": serial_module.STOPBITS_ONE_POINT_FIVE,
            "2": serial_module.STOPBITS_TWO,
        }.get(self._stop_bits, serial_module.STOPBITS_ONE)
        xonxoff = self._flow_control == "xonxoff"
        rtscts = self._flow_control == "rtscts"
        dsrdtr = self._flow_control == "dsrdtr"
        conn = self._serial
        try:
            self._serial = serial_module.Serial(
                port=self._port,
                baudrate=self._baud_rate,
                bytesize=bytesize,
                parity=parity,
                stopbits=stopbits,
                timeout=0.05,
                write_timeout=0.5,
                xonxoff=xonxoff,
                rtscts=rtscts,
                dsrdtr=dsrdtr,
            )
        except Exception as exc:
            self.error.emit(f"Serial open failed: {exc}")
            return False
        self._backend = "pyserial"
        return True

    def _open_posix_serial(self) -> bool:
        if os.name != "posix":
            self.error.emit("PySerial is required for serial sessions on this platform.")
            return False
        try:
            fd = os.open(self._port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        except Exception as exc:
            self.error.emit(f"Serial open failed: {exc}")
            return False
        try:
            self._configure_posix_serial(fd)
        except Exception as exc:
            try:
                os.close(fd)
            except Exception:
                pass
            self.error.emit(f"Serial configure failed: {exc}")
            return False
        self._fd = fd
        self._backend = "posix"
        return True

    def _configure_posix_serial(self, fd: int) -> None:
        if os.name != "posix":
            return
        attrs = termios.tcgetattr(fd)
        iflag, oflag, cflag, lflag, ispeed, ospeed, cc = attrs

        def _flag(name: str) -> int:
            return int(getattr(termios, name, 0))

        iflag &= ~(
            _flag("IGNBRK")
            | _flag("BRKINT")
            | _flag("PARMRK")
            | _flag("ISTRIP")
            | _flag("INLCR")
            | _flag("IGNCR")
            | _flag("ICRNL")
            | _flag("IXON")
            | _flag("IXOFF")
            | _flag("IXANY")
        )
        oflag &= ~_flag("OPOST")
        lflag &= ~(_flag("ECHO") | _flag("ECHONL") | _flag("ICANON") | _flag("ISIG") | _flag("IEXTEN"))
        cflag &= ~(
            _flag("CSIZE")
            | _flag("PARENB")
            | _flag("PARODD")
            | _flag("CSTOPB")
            | _flag("CRTSCTS")
            | _flag("CNEW_RTSCTS")
            | _flag("CMSPAR")
        )
        cflag |= _flag("CLOCAL") | _flag("CREAD")

        data_bits_flag = {
            5: _flag("CS5"),
            6: _flag("CS6"),
            7: _flag("CS7"),
            8: _flag("CS8"),
        }.get(self._data_bits, _flag("CS8"))
        cflag |= data_bits_flag

        cmspar = _flag("CMSPAR")
        parity = self._parity
        if parity == "even":
            cflag |= _flag("PARENB")
        elif parity == "odd":
            cflag |= _flag("PARENB") | _flag("PARODD")
        elif parity in {"mark", "space"} and cmspar:
            cflag |= _flag("PARENB") | cmspar
            if parity == "mark":
                cflag |= _flag("PARODD")
            else:
                cflag &= ~_flag("PARODD")

        if self._stop_bits == "2":
            cflag |= _flag("CSTOPB")

        if self._flow_control == "rtscts":
            hw_flag = _flag("CRTSCTS") or _flag("CNEW_RTSCTS")
            cflag |= hw_flag
        elif self._flow_control == "xonxoff":
            iflag |= _flag("IXON") | _flag("IXOFF") | _flag("IXANY")

        vmin = getattr(termios, "VMIN", 6)
        vtime = getattr(termios, "VTIME", 5)
        cc[vmin] = 0
        cc[vtime] = 1

        baud_const = self._posix_baud_constant(self._baud_rate)
        if baud_const is None:
            fallback = self._posix_baud_constant(9600)
            if fallback is not None:
                baud_const = fallback
                self.error.emit(f"Unsupported serial baud {self._baud_rate}; falling back to 9600.")
            else:
                raise OSError("No supported baud rate constant for this platform.")

        ispeed = baud_const
        ospeed = baud_const
        updated_attrs = [iflag, oflag, cflag, lflag, ispeed, ospeed, cc]
        termios.tcsetattr(fd, termios.TCSANOW, updated_attrs)
        self._set_posix_modem_lines(fd)
        try:
            os.set_blocking(fd, False)
        except Exception:
            pass

    def _set_posix_modem_lines(self, fd: int) -> None:
        if os.name != "posix":
            return
        tiocmget = getattr(termios, "TIOCMGET", None)
        tiocmset = getattr(termios, "TIOCMSET", None)
        if tiocmget is None or tiocmset is None:
            return
        dtr_flag = int(getattr(termios, "TIOCM_DTR", 0))
        rts_flag = int(getattr(termios, "TIOCM_RTS", 0))
        if dtr_flag == 0 and rts_flag == 0:
            return
        modem_bits = array.array("i", [0])
        try:
            fcntl.ioctl(fd, tiocmget, modem_bits, True)
        except Exception:
            return
        # Keep lines asserted by default to match common terminal behavior and
        # support USB CDC ACM devices that only transmit while DTR is high.
        if dtr_flag:
            modem_bits[0] |= dtr_flag
        if rts_flag:
            modem_bits[0] |= rts_flag
        try:
            fcntl.ioctl(fd, tiocmset, modem_bits)
        except Exception:
            return

    @staticmethod
    def _posix_baud_constant(baud_rate: int) -> int | None:
        if os.name != "posix":
            return None
        return getattr(termios, f"B{baud_rate}", None)

    def _cleanup_serial(self) -> None:
        conn = self._serial
        self._serial = None
        if conn is None:
            fd = self._fd
            self._fd = None
            if fd is not None:
                try:
                    os.close(fd)
                except Exception:
                    pass
            return
        try:
            conn.close()
        except Exception:
            pass
        fd = self._fd
        self._fd = None
        if fd is not None:
            try:
                os.close(fd)
            except Exception:
                pass

    @Slot(str)
    def send_text(self, text: str) -> None:
        if not text:
            return
        if self._serial is None and self._fd is None:
            self.error.emit("Serial session is not ready.")
            return
        self._command_queue.put(("input", text, None))

    @Slot(int, int)
    def resize_terminal(self, cols: int, rows: int) -> None:
        self._cols = max(10, cols)
        self._rows = max(5, rows)
        if self._serial is None and self._fd is None:
            return
        self._command_queue.put(("resize", self._cols, self._rows))

    def pause_output(self) -> None:
        self._output_paused = True

    def resume_output(self) -> None:
        self._output_paused = False

    def stop(self) -> None:
        self._stop_requested = True
        try:
            self._command_queue.put_nowait(None)
        except Exception:
            pass


class SSHProbeWorker(QObject):
    finished = Signal(int, bool, str, str, bool, bool)
    PROBE_TIMEOUT_SECONDS = 20
    CLEANUP_TIMEOUT_SECONDS = 1.5

    def __init__(
        self,
        session: Session,
        password: str | None,
        trust_unknown: bool,
        probe_id: int,
    ) -> None:
        super().__init__()
        self._session = session
        self._password = password
        self._trust_unknown = trust_unknown
        self._probe_id = probe_id
        self._client = SSHClient()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task[str] | None = None

    @Slot()
    def run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            if self._trust_unknown:
                probe_coro = self._client.trust_and_verify(self._session, password=self._password)
            else:
                probe_coro = self._client.verify_connectivity(self._session, password=self._password)
            self._task = loop.create_task(probe_coro)
            status = loop.run_until_complete(
                asyncio.wait_for(self._task, timeout=self.PROBE_TIMEOUT_SECONDS)
            )
            self.finished.emit(self._probe_id, True, status, "", False, False)
        except asyncio.TimeoutError:
            self.finished.emit(
                self._probe_id,
                False,
                "",
                "Connection attempt timed out during SSH negotiation.",
                False,
                False,
            )
        except asyncio.CancelledError:
            self.finished.emit(
                self._probe_id,
                False,
                "",
                "Connection attempt canceled.",
                False,
                False,
            )
        except Exception as exc:  # noqa: BLE001
            message = str(exc) or repr(exc)
            lowered = message.lower()
            is_host_key_error = "host key" in lowered and ("not trusted" in lowered or "not verifiable" in lowered)
            is_auth_error = isinstance(exc, asyncssh.PermissionDenied) or (
                "permission denied" in lowered or "authentication failed" in lowered
            )
            self.finished.emit(self._probe_id, False, "", message, is_host_key_error, is_auth_error)
        finally:
            self._task = None
            try:
                pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
                for task in pending:
                    task.cancel()
                if pending:
                    # Keep cleanup bounded so probe threads can't stall app shutdown.
                    loop.run_until_complete(
                        asyncio.wait_for(
                            asyncio.gather(*pending, return_exceptions=True),
                            timeout=self.CLEANUP_TIMEOUT_SECONDS,
                        )
                    )
            except Exception:
                pass
            try:
                loop.close()
            except Exception:
                pass
            self._loop = None

    @Slot(int, bool, str, str, bool, bool)
    def _schedule_delete(
        self,
        _probe_id: int,
        _ok: bool,
        _status: str,
        _error_message: str,
        _host_key_error: bool,
        _auth_error: bool,
    ) -> None:
        self.deleteLater()

    @Slot()
    def cancel(self) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return

        def _cancel_pending() -> None:
            task = self._task
            if task is not None and not task.done():
                task.cancel()
            for pending in asyncio.all_tasks(loop):
                if not pending.done():
                    pending.cancel()

        try:
            loop.call_soon_threadsafe(_cancel_pending)
        except RuntimeError:
            return


@dataclass(frozen=True)
class _TerminalScrollOperation:
    top: int
    bottom: int
    delta: int


class _TerminalViewport(QWidget):
    def __init__(self, owner: "TerminalView") -> None:
        super().__init__(owner)
        self._owner = owner
        self.setFocusPolicy(Qt.NoFocus)
        self.setAcceptDrops(True)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if self._owner._accept_terminal_drop_event(event):
            return
        try:
            super().dragEnterEvent(event)
        except TypeError:
            pass

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if self._owner._accept_terminal_drop_event(event):
            return
        try:
            super().dragMoveEvent(event)
        except TypeError:
            pass

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        if self._owner._handle_terminal_drop_event(event):
            return
        try:
            super().dropEvent(event)
        except TypeError:
            pass


class TerminalView(QAbstractScrollArea):
    data_input = Signal(str)
    paste_requested = Signal(str)
    open_sftp_requested = Signal()
    disconnect_requested = Signal()
    start_logging_requested = Signal()
    stop_logging_requested = Signal()
    terminal_resized = Signal(int, int)

    COLOR_MAP = {
        "default": "",
        "black": "#000000",
        "red": "#cd3131",
        "green": "#0dbc79",
        "brown": "#949800",
        "blue": "#2472c8",
        "magenta": "#bc3fbc",
        "cyan": "#11a8cd",
        "white": "#e5e5e5",
        "brightblack": "#666666",
        "brightred": "#f14c4c",
        "brightgreen": "#23d18b",
        "brightyellow": "#f5f543",
        "brightblue": "#3b8eea",
        "brightmagenta": "#d670d6",
        "brightcyan": "#29b8db",
        "brightwhite": "#ffffff",
    }
    CURSOR_BLINK_INTERVAL_MS = 550
    VISUAL_BELL_DURATION_MS = 130
    CENTER_MESSAGE_DURATION_MS = 5000
    CONTENT_PADDING_PX = 2
    GLYPH_CACHE_MAX_ENTRIES = 4096

    def __init__(self, settings: AppSettings, emulator: "VT100Emulator", parent=None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._emulator = emulator
        self._draw_font = self._build_terminal_font(settings)
        self._metrics = QFontMetrics(self._draw_font)
        self._cell_width_px = 1
        self._cell_height_px = 1
        self._cell_ascent_px = 1
        self._cell_descent_px = 2
        self._selection_anchor: tuple[int, int] | None = None
        self._selection_cursor: tuple[int, int] | None = None
        self._selecting = False
        self._logging_active = False
        self._backspace_prefers_ctrl_h_override: bool | None = None
        self._cursor_blink_enabled = False
        self._cursor_visible = True
        self._session_connected = True
        self._visual_bell_active = False
        self._visual_bell_timer = QTimer(self)
        self._visual_bell_timer.setSingleShot(True)
        self._visual_bell_timer.timeout.connect(self._clear_visual_bell)
        self._center_message_text = ""
        self._center_message_timer = QTimer(self)
        self._center_message_timer.setSingleShot(True)
        self._center_message_timer.timeout.connect(self._clear_center_message)
        self._cursor_blink_timer = QTimer(self)
        self._cursor_blink_timer.setInterval(self.CURSOR_BLINK_INTERVAL_MS)
        self._cursor_blink_timer.timeout.connect(self._toggle_cursor_visibility)
        self._syncing_history_scrollbar = False
        self._last_history_scrollbar_state: tuple[int, int, int] | None = None
        self._pending_history_scroll_value: int | None = None
        self._wheel_angle_remainder_y = 0
        self._wheel_pixel_remainder_y = 0
        self._history_scroll_timer = QTimer(self)
        self._history_scroll_timer.setSingleShot(True)
        self._history_scroll_timer.setInterval(12)
        self._history_scroll_timer.timeout.connect(self._apply_pending_history_scroll)
        self._framebuffer: QImage | None = None
        self._framebuffer_valid = False
        self._glyph_cache: OrderedDict[tuple[str, str, bool], QImage] = OrderedDict()
        self._terminal_bg = QColor("#000000")
        self._terminal_fg = QColor("#ffffff")
        self._terminal_bg_name = self._terminal_bg.name()
        self._terminal_fg_name = self._terminal_fg.name()
        self._selection_bg = QColor("#1d4ed8")
        self._selection_fg = QColor("#ffffff")
        self._selection_bg_name = self._selection_bg.name()
        self._selection_fg_name = self._selection_fg.name()
        self._open_sftp_supported = False
        self.setObjectName("terminalView")
        self.setViewport(_TerminalViewport(self))
        self.viewport().setObjectName("terminalViewport")
        self.setFrameShape(QFrame.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setFont(self._draw_font)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.verticalScrollBar().valueChanged.connect(self._on_history_scrollbar_changed)
        self._refresh_font_metrics()
        self.apply_settings(settings)

    def _content_rect(self) -> QRect:
        viewport = self.viewport().rect()
        if viewport.isEmpty():
            return QRect()
        margin = self.CONTENT_PADDING_PX
        return QRect(
            margin,
            margin,
            max(0, viewport.width() - (margin * 2)),
            max(0, viewport.height() - (margin * 2)),
        )

    def _refresh_font_metrics(self) -> None:
        self._cell_width_px = max(1, self._metrics.horizontalAdvance("0"))
        self._cell_height_px = max(1, self._metrics.height())
        self._cell_ascent_px = self._metrics.ascent()
        self._cell_descent_px = max(2, self._metrics.descent())

    def terminal_size(self) -> tuple[int, int]:
        content = self._content_rect()
        width = max(1, content.width())
        height = max(1, content.height())
        cols = max(10, math.floor(width / self._cell_width_px))
        rows = max(5, math.floor(height / self._cell_height_px))
        return cols, rows

    def terminal_line_height_px(self) -> int:
        return self._cell_height_px

    def apply_settings(self, settings: AppSettings) -> None:
        self._settings = settings
        self._draw_font = self._build_terminal_font(settings)
        self._metrics = QFontMetrics(self._draw_font)
        self._refresh_font_metrics()
        self._framebuffer = None
        self._framebuffer_valid = False
        self._glyph_cache.clear()
        self.setFont(self._draw_font)
        default_bg, default_fg = resolve_terminal_default_colors(settings)
        bg = self._safe_color(default_bg, "#000000")
        fg = self._safe_color(default_fg, "#ffffff")
        self._terminal_bg = QColor(bg)
        self._terminal_fg = QColor(fg)
        self._terminal_bg_name = self._terminal_bg.name()
        self._terminal_fg_name = self._terminal_fg.name()
        self.setStyleSheet(
            f"QAbstractScrollArea#terminalView {{ background-color: {bg}; color: {fg}; "
            "border: 1px solid #334155; border-radius: 8px; }"
            f"QWidget#terminalViewport {{ background-color: {bg}; border: none; }}"
        )
        self._set_cursor_blink_enabled(settings.terminal_cursor_blink)
        if not settings.terminal_visual_bell_enabled:
            self._visual_bell_timer.stop()
            self._visual_bell_active = False
        self._ensure_cursor_visible()
        self.sync_history_scrollbar()

    def set_logging_active(self, active: bool) -> None:
        self._logging_active = active

    def set_open_sftp_supported(self, supported: bool) -> None:
        self._open_sftp_supported = bool(supported)

    def set_backspace_prefers_ctrl_h_override(self, preference: bool | None) -> None:
        self._backspace_prefers_ctrl_h_override = preference

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._framebuffer_valid = False
        cols, rows = self.terminal_size()
        self.terminal_resized.emit(cols, rows)
        self.sync_history_scrollbar()
        self.request_full_repaint()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._framebuffer_valid = False
        QTimer.singleShot(0, self.request_full_repaint)

    def viewportEvent(self, event) -> bool:  # noqa: N802
        if event.type() == QEvent.Paint:
            self._paint_viewport(self.viewport(), event)
            return True
        return super().viewportEvent(event)

    def _history_scrollbar_state(self) -> tuple[int, int, int]:
        history = self._emulator.screen.history
        total_history_lines = len(history.top) + len(history.bottom)
        current_value = len(history.top)
        page_step = max(1, self._emulator.screen.lines)
        return (total_history_lines, current_value, page_step)

    def sync_history_scrollbar(self) -> bool:
        total_history_lines, current_value, page_step = self._history_scrollbar_state()
        state = (total_history_lines, current_value, page_step)
        if state == self._last_history_scrollbar_state:
            return False
        bar = self.verticalScrollBar()
        self._syncing_history_scrollbar = True
        try:
            bar.setSingleStep(1)
            bar.setPageStep(page_step)
            bar.setRange(0, total_history_lines)
            bar.setValue(max(0, min(current_value, total_history_lines)))
        finally:
            self._syncing_history_scrollbar = False
        self._last_history_scrollbar_state = state
        return True

    def request_full_repaint(self) -> None:
        viewport_rect = self.viewport().rect()
        if viewport_rect.isEmpty():
            return
        self._render_to_framebuffer(viewport_rect)
        self._framebuffer_valid = True
        self.viewport().update(viewport_rect)

    def request_repaint_for_rows(self, rows: set[int]) -> None:
        if not rows:
            return
        _, visible_rows = self.terminal_size()
        normalized = sorted({row for row in rows if 0 <= row < visible_rows})
        if not normalized:
            return
        start = normalized[0]
        end = normalized[0]
        for row in normalized[1:]:
            if row == end + 1:
                end = row
                continue
            self._render_and_update_row_band(start, end)
            start = row
            end = row
        self._render_and_update_row_band(start, end)

    def apply_scroll_operations(self, operations: tuple[_TerminalScrollOperation, ...]) -> set[int]:
        if not operations:
            return set()
        _, visible_rows = self.terminal_size()
        if visible_rows <= 0 or not self._framebuffer_valid:
            return set(range(visible_rows))
        operations = self._coalesce_scroll_operations(operations)
        if not operations:
            return set()
        dirty_rows: set[int] = set()
        for operation in operations:
            top = max(0, min(visible_rows - 1, operation.top))
            bottom = max(top, min(visible_rows - 1, operation.bottom))
            dirty_rows.update(range(top, bottom + 1))
        return dirty_rows

    @staticmethod
    def _coalesce_scroll_operations(
        operations: tuple[_TerminalScrollOperation, ...],
    ) -> tuple[_TerminalScrollOperation, ...]:
        coalesced: list[_TerminalScrollOperation] = []
        for operation in operations:
            if operation.delta == 0:
                continue
            if coalesced and coalesced[-1].top == operation.top and coalesced[-1].bottom == operation.bottom:
                merged_delta = coalesced[-1].delta + operation.delta
                if merged_delta == 0:
                    coalesced.pop()
                else:
                    coalesced[-1] = _TerminalScrollOperation(
                        top=operation.top,
                        bottom=operation.bottom,
                        delta=merged_delta,
                    )
                continue
            coalesced.append(operation)
        return tuple(coalesced)

    def _row_band_rect(self, start_row: int, end_row: int) -> QRect:
        viewport = self.viewport().rect()
        content = self._content_rect()
        y = max(content.top(), content.top() + (start_row * self._cell_height_px))
        bottom = min(content.bottom() + 1, content.top() + ((end_row + 1) * self._cell_height_px))
        if bottom <= y:
            return QRect()
        return QRect(0, y, viewport.width(), bottom - y)

    def _render_and_update_row_band(self, start_row: int, end_row: int) -> None:
        rect = self._row_band_rect(start_row, end_row)
        if rect.isEmpty():
            return
        self._render_to_framebuffer(rect)
        self.viewport().update(rect)

    def _on_history_scrollbar_changed(self, value: int) -> None:
        if self._syncing_history_scrollbar:
            return
        self._pending_history_scroll_value = int(value)
        if not self._history_scroll_timer.isActive():
            self._history_scroll_timer.start()

    def _apply_pending_history_scroll(self) -> None:
        value = self._pending_history_scroll_value
        self._pending_history_scroll_value = None
        if value is None:
            return
        self._apply_history_scroll_value(value)

    def _apply_history_scroll_value(self, value: int) -> None:
        if self._history_scroll_timer.isActive():
            self._history_scroll_timer.stop()
        self._pending_history_scroll_value = None
        bar = self.verticalScrollBar()
        target_value = max(bar.minimum(), min(int(value), bar.maximum()))
        history = self._emulator.screen.history
        current_value = len(history.top)
        if target_value < current_value:
            self._emulator.scroll_up(current_value - target_value)
        elif target_value > current_value:
            self._emulator.scroll_down(target_value - current_value)
        self.sync_history_scrollbar()
        self.request_full_repaint()

    def _consume_angle_wheel_steps(self, delta_y: int) -> int:
        if delta_y == 0:
            return 0
        self._wheel_angle_remainder_y += int(delta_y)
        notches = math.trunc(self._wheel_angle_remainder_y / 120)
        if not notches:
            return 0
        self._wheel_angle_remainder_y -= notches * 120
        return notches * max(1, int(QApplication.wheelScrollLines()))

    def _consume_pixel_wheel_steps(self, delta_y: int) -> int:
        if delta_y == 0:
            return 0
        self._wheel_pixel_remainder_y += int(delta_y)
        line_height = max(1, self.terminal_line_height_px())
        steps = math.trunc(self._wheel_pixel_remainder_y / line_height)
        if not steps:
            return 0
        self._wheel_pixel_remainder_y -= steps * line_height
        return steps

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.matches(QKeySequence.Copy):
            modifiers = self._enum_int(event.modifiers())
            ctrl = bool(modifiers & self._enum_int(Qt.ControlModifier))
            shift = bool(modifiers & self._enum_int(Qt.ShiftModifier))
            alt = bool(modifiers & self._enum_int(Qt.AltModifier))
            meta = bool(modifiers & self._enum_int(Qt.MetaModifier))
            is_plain_ctrl_c = (
                ctrl
                and not shift
                and not alt
                and not meta
                and self._enum_int(event.key()) == self._enum_int(Qt.Key_C)
            )
            if self._has_selection() and not is_plain_ctrl_c:
                QApplication.clipboard().setText(self._selected_text())
                return
        if event.matches(QKeySequence.Paste):
            self.paste_requested.emit(QApplication.clipboard().text())
            return
        backspace_prefers_ctrl_h = self._emulator.backspace_sends_ctrl_h()
        if not backspace_prefers_ctrl_h and self._backspace_prefers_ctrl_h_override is not None:
            backspace_prefers_ctrl_h = self._backspace_prefers_ctrl_h_override
        text = self._map_key_event(
            event,
            application_cursor_mode=self._emulator.application_cursor_mode_enabled(),
            backspace_prefers_ctrl_h=backspace_prefers_ctrl_h,
        )
        if text is not None:
            if self._has_selection():
                self._clear_selection()
            self.data_input.emit(text)
            self._ensure_cursor_visible()
            event.accept()
            return
        event.ignore()

    def focusNextPrevChild(self, next_child: bool) -> bool:  # noqa: ARG002, N802
        # Keep Tab/Shift+Tab in the terminal stream instead of moving focus.
        return False

    def _accept_terminal_drop_event(self, event) -> bool:
        if _local_paths_from_mime_data(event.mimeData()):
            event.acceptProposedAction()
            return True
        return False

    def _handle_terminal_drop_event(self, event: QDropEvent) -> bool:
        local_paths = _local_paths_from_mime_data(event.mimeData())
        if not local_paths:
            return False
        payload = _format_dropped_local_paths(local_paths)
        if not payload:
            return False
        self.setFocus(Qt.MouseFocusReason)
        self.paste_requested.emit(payload)
        event.acceptProposedAction()
        return True

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if self._accept_terminal_drop_event(event):
            return
        try:
            super().dragEnterEvent(event)
        except TypeError:
            pass

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if self._accept_terminal_drop_event(event):
            return
        try:
            super().dragMoveEvent(event)
        except TypeError:
            pass

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        if self._handle_terminal_drop_event(event):
            return
        try:
            super().dropEvent(event)
        except TypeError:
            pass

    @staticmethod
    def _map_key_event(
        event: QKeyEvent,
        *,
        application_cursor_mode: bool = False,
        backspace_prefers_ctrl_h: bool = False,
    ) -> str | None:
        key = TerminalView._enum_int(event.key())
        modifiers = TerminalView._enum_int(event.modifiers())
        ctrl = bool(modifiers & TerminalView._enum_int(Qt.ControlModifier))
        alt = bool(modifiers & TerminalView._enum_int(Qt.AltModifier))
        shift = bool(modifiers & TerminalView._enum_int(Qt.ShiftModifier))
        meta = bool(modifiers & TerminalView._enum_int(Qt.MetaModifier))

        if key == TerminalView._enum_int(Qt.Key_Backspace):
            if ctrl and not alt and not shift and not meta:
                return "\x7f" if backspace_prefers_ctrl_h else "\x08"
            return "\x08" if backspace_prefers_ctrl_h else "\x7f"

        if key == TerminalView._enum_int(Qt.Key_Backtab):
            return "\x1b[Z"
        if key == TerminalView._enum_int(Qt.Key_Tab) and shift and not ctrl and not alt and not meta:
            return "\x1b[Z"

        special: dict[int, str] = {
            TerminalView._enum_int(Qt.Key_Return): "\r",
            TerminalView._enum_int(Qt.Key_Enter): "\r",
            TerminalView._enum_int(Qt.Key_Tab): "\t",
            TerminalView._enum_int(Qt.Key_Escape): "\x1b",
            TerminalView._enum_int(Qt.Key_Delete): "\x1b[3~",
            TerminalView._enum_int(Qt.Key_PageUp): "\x1b[5~",
            TerminalView._enum_int(Qt.Key_PageDown): "\x1b[6~",
        }
        if key in special:
            return special[key]

        if not alt and not meta:
            function_keys: dict[int, str] = {
                TerminalView._enum_int(Qt.Key_F1): "\x1bOP",
                TerminalView._enum_int(Qt.Key_F2): "\x1bOQ",
                TerminalView._enum_int(Qt.Key_F3): "\x1bOR",
                TerminalView._enum_int(Qt.Key_F4): "\x1bOS",
                TerminalView._enum_int(Qt.Key_F5): "\x1b[15~",
                TerminalView._enum_int(Qt.Key_F6): "\x1b[17~",
                TerminalView._enum_int(Qt.Key_F7): "\x1b[18~",
                TerminalView._enum_int(Qt.Key_F8): "\x1b[19~",
                TerminalView._enum_int(Qt.Key_F9): "\x1b[20~",
                TerminalView._enum_int(Qt.Key_F10): "\x1b[21~",
                TerminalView._enum_int(Qt.Key_F11): "\x1b[23~",
                TerminalView._enum_int(Qt.Key_F12): "\x1b[24~",
            }
            if key in function_keys:
                return function_keys[key]

        normal_cursor: dict[int, str] = {
            TerminalView._enum_int(Qt.Key_Up): "\x1b[A",
            TerminalView._enum_int(Qt.Key_Down): "\x1b[B",
            TerminalView._enum_int(Qt.Key_Right): "\x1b[C",
            TerminalView._enum_int(Qt.Key_Left): "\x1b[D",
            TerminalView._enum_int(Qt.Key_Home): "\x1b[H",
            TerminalView._enum_int(Qt.Key_End): "\x1b[F",
        }
        if key in normal_cursor:
            if application_cursor_mode:
                application_cursor: dict[int, str] = {
                    TerminalView._enum_int(Qt.Key_Up): "\x1bOA",
                    TerminalView._enum_int(Qt.Key_Down): "\x1bOB",
                    TerminalView._enum_int(Qt.Key_Right): "\x1bOC",
                    TerminalView._enum_int(Qt.Key_Left): "\x1bOD",
                    TerminalView._enum_int(Qt.Key_Home): "\x1bOH",
                    TerminalView._enum_int(Qt.Key_End): "\x1bOF",
                }
                return application_cursor.get(key, normal_cursor[key])
            return normal_cursor[key]

        key_a = TerminalView._enum_int(Qt.Key_A)
        key_z = TerminalView._enum_int(Qt.Key_Z)
        if ctrl and not alt and not meta:
            if key_a <= key <= key_z:
                return chr((key - key_a) + 1)
            if key == TerminalView._enum_int(Qt.Key_BracketLeft):
                return "\x1b"
            if key == TerminalView._enum_int(Qt.Key_Backslash):
                return "\x1c"
            if key == TerminalView._enum_int(Qt.Key_BracketRight):
                return "\x1d"
            if key in (
                TerminalView._enum_int(Qt.Key_6),
                TerminalView._enum_int(Qt.Key_AsciiCircum),
            ):
                return "\x1e"
            if key in (
                TerminalView._enum_int(Qt.Key_Minus),
                TerminalView._enum_int(Qt.Key_Underscore),
            ):
                return "\x1f"

        text = event.text()
        if not text:
            if key == TerminalView._enum_int(Qt.Key_At):
                return "@"
            # AltGr is often reported as Ctrl+Alt on Linux; preserve common @
            # combos even when Qt does not provide an event.text() payload.
            if ctrl and alt and not meta and key in (
                TerminalView._enum_int(Qt.Key_Q),
                TerminalView._enum_int(Qt.Key_2),
            ):
                return "@"
            return None

        if ctrl and not alt and not meta and len(text) == 1:
            ch = text.upper()
            if "A" <= ch <= "Z":
                return chr(ord(ch) - 64)
        return text

    @staticmethod
    def _enum_int(value: object) -> int:
        if isinstance(value, int):
            return value
        enum_value = getattr(value, "value", None)
        if isinstance(enum_value, int):
            return enum_value
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        self._show_context_menu(event.globalPos())

    def _show_context_menu(self, global_pos) -> None:
        menu = QMenu(self)
        copy_and_paste_action = menu.addAction("Copy and Paste")
        copy_and_paste_action.setEnabled(self._has_selection())
        copy_action = menu.addAction("Copy")
        copy_action.setEnabled(self._has_selection())
        menu.addSeparator()
        paste_action = menu.addAction("Paste")
        paste_action.setEnabled(bool(QApplication.clipboard().text()))
        open_sftp_action = None
        disconnect_action = None
        if self._open_sftp_supported:
            menu.addSeparator()
            open_sftp_action = menu.addAction("Open SFTP Tab")
        if self._session_connected:
            menu.addSeparator()
            disconnect_action = menu.addAction("Disconnect")
        menu.addSeparator()
        start_logging_action = None
        stop_logging_action = None
        if self._logging_active:
            stop_logging_action = menu.addAction("Stop Logging")
        else:
            start_logging_action = menu.addAction("Log Session to File...")
        action = menu.exec(global_pos or QCursor.pos())
        if action == copy_and_paste_action:
            self._copy_selection_and_paste()
        elif action == copy_action:
            QApplication.clipboard().setText(self._selected_text())
        elif action == paste_action:
            self.paste_requested.emit(QApplication.clipboard().text())
        elif open_sftp_action is not None and action == open_sftp_action:
            self.open_sftp_requested.emit()
        elif disconnect_action is not None and action == disconnect_action:
            self.disconnect_requested.emit()
        elif action == start_logging_action:
            self.start_logging_requested.emit()
        elif action == stop_logging_action:
            self.stop_logging_requested.emit()
        menu.deleteLater()

    def _copy_selection_and_paste(self) -> None:
        selected = self._selected_text()
        if not selected:
            return
        QApplication.clipboard().setText(selected)
        self.paste_requested.emit(selected)

    def _handle_mouse_press(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self.setFocus(Qt.MouseFocusReason)
            cell = self._cell_from_pos(event.position().toPoint().x(), event.position().toPoint().y())
            self._selection_anchor = cell
            self._selection_cursor = cell
            self._selecting = True
            self._ensure_cursor_visible()
            event.accept()
            return
        event.ignore()

    def _handle_mouse_move(self, event: QMouseEvent) -> None:
        if self._selecting and self._selection_anchor is not None:
            cell = self._cell_from_pos(event.position().toPoint().x(), event.position().toPoint().y())
            self._selection_cursor = cell
            self.request_full_repaint()
            event.accept()
            return
        event.ignore()

    def _handle_mouse_release(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton and self._selecting:
            self._selecting = False
            cell = self._cell_from_pos(event.position().toPoint().x(), event.position().toPoint().y())
            self._selection_cursor = cell
            self.request_full_repaint()
            event.accept()
            return
        event.ignore()

    def _handle_mouse_double_click(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self.setFocus(Qt.MouseFocusReason)
            cell = self._cell_from_pos(event.position().toPoint().x(), event.position().toPoint().y())
            self._select_word_at_cell(cell)
            self._ensure_cursor_visible()
            event.accept()
            return
        event.ignore()

    def _handle_wheel(self, event: QWheelEvent) -> None:
        pixel_delta = event.pixelDelta().y()
        angle_delta = event.angleDelta().y()
        if pixel_delta == 0 and angle_delta == 0:
            event.ignore()
            return
        if pixel_delta != 0:
            steps = self._consume_pixel_wheel_steps(pixel_delta)
        else:
            steps = self._consume_angle_wheel_steps(angle_delta)
        if steps == 0:
            event.accept()
            return
        bar = self.verticalScrollBar()
        self._apply_history_scroll_value(bar.value() - steps)
        event.accept()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._handle_mouse_press(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._handle_mouse_move(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._handle_mouse_release(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._handle_mouse_double_click(event)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        self._handle_wheel(event)

    def _paint_viewport(self, target_widget: QWidget, event) -> None:
        rect = event.rect().intersected(target_widget.rect())
        if rect.isEmpty():
            return
        self._ensure_viewport_framebuffer()
        painter = QPainter(target_widget)
        self._draw_viewport_frame(painter, rect)
        painter.end()

    def _ensure_framebuffer(self) -> QImage | None:
        viewport = self.viewport().rect()
        size = viewport.size()
        if size.isEmpty():
            self._framebuffer = None
            self._framebuffer_valid = False
            return None
        if self._framebuffer is None or self._framebuffer.size() != size:
            self._framebuffer = QImage(size, QImage.Format_ARGB32_Premultiplied)
            self._framebuffer.fill(self._terminal_bg.rgba())
            self._framebuffer_valid = False
        return self._framebuffer

    def _ensure_viewport_framebuffer(self) -> None:
        if (
            not self._framebuffer_valid
            or self._framebuffer is None
            or self._framebuffer.size() != self.viewport().rect().size()
        ):
            self._render_to_framebuffer(self.viewport().rect())
            self._framebuffer_valid = True

    def _draw_viewport_frame(self, painter: QPainter, rect: QRect) -> None:
        painter.setClipRect(rect)
        if self._framebuffer is not None:
            painter.drawImage(rect, self._framebuffer, rect)
        if self._center_message_text:
            self._draw_center_message(painter, self._center_message_text)

    def _draw_center_message(self, painter: QPainter, text: str) -> None:
        viewport = self.viewport().rect()
        if viewport.isEmpty():
            return

        font = QFont(self._draw_font)
        font.setBold(True)
        metrics = QFontMetrics(font)
        margin = max(4, min(24, viewport.width() // 12, viewport.height() // 8))
        available_width = max(1, viewport.width() - (margin * 2))
        available_height = max(1, viewport.height() - (margin * 2))
        padding_x = 18
        padding_y = 12
        max_text_width = max(1, available_width - (padding_x * 2))
        text_bounds = metrics.boundingRect(
            QRect(0, 0, max_text_width, 1000),
            Qt.AlignCenter | Qt.TextWordWrap,
            text,
        )
        box_width = min(available_width, max(1, text_bounds.width() + (padding_x * 2)))
        box_height = min(available_height, max(1, text_bounds.height() + (padding_y * 2)))
        box = QRect(
            viewport.left() + ((viewport.width() - box_width) // 2),
            viewport.top() + ((viewport.height() - box_height) // 2),
            box_width,
            box_height,
        )
        text_rect = box.adjusted(padding_x, padding_y, -padding_x, -padding_y)

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setFont(font)
        painter.setPen(QColor("#f59e0b"))
        painter.setBrush(QColor(15, 23, 42, 230))
        painter.drawRoundedRect(box, 8, 8)
        painter.setPen(QColor("#f8fafc"))
        painter.drawText(text_rect, Qt.AlignCenter | Qt.TextWordWrap, text)
        painter.restore()

    def _render_to_framebuffer(self, rect: QRect) -> None:
        target = rect.intersected(self.viewport().rect())
        if target.isEmpty():
            return
        framebuffer = self._ensure_framebuffer()
        if framebuffer is None:
            return
        painter = QPainter(framebuffer)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        painter.setClipRect(target)
        bg = self._terminal_bg
        fg_default = self._terminal_fg
        selection_bg = self._selection_bg
        selection_fg = self._selection_fg
        painter.fillRect(target, bg)
        painter.setFont(self._draw_font)

        content = self._content_rect()
        cell_w = self._cell_width_px
        cell_h = self._cell_height_px
        ascent = self._cell_ascent_px
        descent = self._cell_descent_px

        cols, rows = self.terminal_size()
        screen = self._emulator.screen
        default_char = screen.default_char
        bg_name = self._terminal_bg_name
        fg_name = self._terminal_fg_name
        row_start = max(0, target.top() // cell_h)
        row_end = min(rows - 1, target.bottom() // cell_h)
        color_cache: dict[str, QColor] = {
            bg_name: bg,
            fg_name: fg_default,
            self._selection_bg_name: selection_bg,
            self._selection_fg_name: selection_fg,
        }
        style_cache: dict[tuple[str, str, bool], tuple[str, str]] = {}
        has_selection = self._has_selection()
        selection_start, selection_end = self._selection_bounds(cols) if has_selection else (0, -1)

        content_target = target.intersected(content)
        if not content_target.isEmpty():
            row_start = max(0, (content_target.top() - content.top()) // cell_h)
            row_end = min(rows - 1, (content_target.bottom() - content.top()) // cell_h)
            col_start = max(0, (content_target.left() - content.left()) // cell_w)
            col_end = min(cols - 1, (content_target.right() - content.left()) // cell_w)

            for row in range(row_start, row_end + 1):
                y = content.top() + (row * cell_h)
                line = screen.buffer.get(row, {})
                if has_selection:
                    for col in range(col_start, col_end + 1):
                        x = content.left() + (col * cell_w)
                        char = line.get(col, default_char)
                        char_fg, char_bg = self._resolved_cell_colors(
                            char,
                            fg_default=fg_name,
                            bg_default=bg_name,
                            cache=style_cache,
                        )
                        is_bold = bool(getattr(char, "bold", False))
                        glyph = getattr(char, "data", " ") or " "
                        if selection_start <= ((row * cols) + col) <= selection_end:
                            char_bg = self._selection_bg_name
                            char_fg = self._selection_fg_name
                        if char_bg != bg_name:
                            painter.fillRect(x, y, cell_w, cell_h, self._cached_qcolor(color_cache, char_bg))
                        if glyph != " ":
                            self._draw_terminal_glyph(
                                painter,
                                x=x,
                                y=y,
                                glyph=glyph,
                                color_name=char_fg,
                                is_bold=is_bold,
                                ascent=ascent,
                                color_cache=color_cache,
                            )
                    continue

                for col, char in line.items():
                    if not col_start <= col <= col_end:
                        continue
                    x = content.left() + (col * cell_w)
                    char_fg, char_bg = self._resolved_cell_colors(
                        char,
                        fg_default=fg_name,
                        bg_default=bg_name,
                        cache=style_cache,
                    )
                    is_bold = bool(getattr(char, "bold", False))
                    glyph = getattr(char, "data", " ") or " "
                    if char_bg != bg_name:
                        painter.fillRect(x, y, cell_w, cell_h, self._cached_qcolor(color_cache, char_bg))
                    if glyph != " ":
                        self._draw_terminal_glyph(
                            painter,
                            x=x,
                            y=y,
                            glyph=glyph,
                            color_name=char_fg,
                            is_bold=is_bold,
                            ascent=ascent,
                            color_cache=color_cache,
                        )

        cursor = screen.cursor
        show_cursor = (not self._cursor_blink_enabled) or (not self._session_connected) or self._cursor_visible
        if show_cursor and cursor is not None and 0 <= cursor.x < cols and 0 <= cursor.y < rows:
            cx = content.left() + (cursor.x * cell_w)
            cy = content.top() + (cursor.y * cell_h)
            cursor_rect = QRect(cx, cy + cell_h - descent, cell_w, descent)
            if cursor_rect.intersects(target):
                painter.fillRect(cursor_rect, fg_default)

        if self._visual_bell_active:
            painter.fillRect(target, QColor(255, 255, 255, 56))

        painter.end()

    @staticmethod
    def _cached_qcolor(color_cache: dict[str, QColor], color_name: str) -> QColor:
        cached = color_cache.get(color_name)
        if cached is not None:
            return cached
        resolved = QColor(color_name)
        color_cache[color_name] = resolved
        return resolved

    def _resolved_cell_colors(
        self,
        char,
        *,
        fg_default: str,
        bg_default: str,
        cache: dict[tuple[str, str, bool], tuple[str, str]],
    ) -> tuple[str, str]:
        raw_fg = getattr(char, "fg", "default")
        raw_bg = getattr(char, "bg", "default")
        reverse = bool(getattr(char, "reverse", False))
        cache_key = (raw_fg, raw_bg, reverse)
        cached = cache.get(cache_key)
        if cached is not None:
            return cached
        char_bg = self._resolve_color(raw_bg, bg_default)
        char_fg = self._resolve_color(raw_fg, fg_default)
        if reverse:
            char_fg, char_bg = char_bg, char_fg
        cache[cache_key] = (char_fg, char_bg)
        return (char_fg, char_bg)

    def _draw_terminal_glyph(
        self,
        painter: QPainter,
        *,
        x: int,
        y: int,
        glyph: str,
        color_name: str,
        is_bold: bool,
        ascent: int,
        color_cache: dict[str, QColor],
    ) -> None:
        glyph_image = self._cached_glyph_image(glyph=glyph, color_name=color_name, is_bold=is_bold)
        if glyph_image is not None:
            painter.drawImage(x, y, glyph_image)
            return
        painter.setPen(self._cached_qcolor(color_cache, color_name))
        painter.drawText(x, y + ascent, glyph)
        if is_bold:
            painter.drawText(x + 1, y + ascent, glyph)

    def _cached_glyph_image(self, *, glyph: str, color_name: str, is_bold: bool) -> QImage | None:
        if not self._should_cache_glyph(glyph):
            return None
        cache_key = (glyph, color_name, is_bold)
        cached = self._glyph_cache.get(cache_key)
        if cached is not None:
            self._glyph_cache.move_to_end(cache_key)
            return cached

        image_width = self._cell_width_px + (1 if is_bold else 0)
        image = QImage(max(1, image_width), self._cell_height_px, QImage.Format_ARGB32_Premultiplied)
        image.fill(Qt.transparent)
        glyph_painter = QPainter(image)
        glyph_painter.setRenderHint(QPainter.TextAntialiasing, True)
        glyph_painter.setFont(self._draw_font)
        glyph_painter.setPen(QColor(color_name))
        glyph_painter.drawText(0, self._cell_ascent_px, glyph)
        if is_bold:
            glyph_painter.drawText(1, self._cell_ascent_px, glyph)
        glyph_painter.end()

        self._glyph_cache[cache_key] = image
        if len(self._glyph_cache) > self.GLYPH_CACHE_MAX_ENTRIES:
            self._glyph_cache.popitem(last=False)
        return image

    def _should_cache_glyph(self, glyph: str) -> bool:
        if not glyph or glyph == " ":
            return False
        if any(ord(ch) < 0x20 for ch in glyph):
            return False
        return self._metrics.horizontalAdvance(glyph) <= (self._cell_width_px + 1)

    @classmethod
    def _resolve_color(cls, name: str, default_hex: str) -> str:
        if not name:
            return default_hex
        normalized = name.strip().lower()
        if not normalized or normalized == "default":
            return default_hex
        mapped = cls.COLOR_MAP.get(normalized)
        if mapped is not None:
            return mapped
        if normalized.startswith("#"):
            normalized = normalized[1:]
        if len(normalized) in (3, 6) and all(ch in "0123456789abcdef" for ch in normalized):
            if len(normalized) == 3:
                normalized = "".join(ch * 2 for ch in normalized)
            return f"#{normalized}"
        return default_hex

    @staticmethod
    def _safe_color(value: str, fallback: str) -> str:
        color = QColor(value)
        if color.isValid():
            return color.name()
        return fallback

    def _set_cursor_blink_enabled(self, enabled: bool) -> None:
        self._cursor_blink_enabled = bool(enabled)
        self._cursor_visible = True
        if self._cursor_blink_enabled and self._session_connected:
            self._cursor_blink_timer.start()
            return
        self._cursor_blink_timer.stop()

    def set_session_connected(self, connected: bool) -> None:
        self._session_connected = bool(connected)
        self._cursor_visible = True
        if self._cursor_blink_enabled and self._session_connected:
            self._cursor_blink_timer.start()
        else:
            self._cursor_blink_timer.stop()
        self.request_full_repaint()

    def _toggle_cursor_visibility(self) -> None:
        self._cursor_visible = not self._cursor_visible
        self.request_full_repaint()

    def flash_visual_bell(self) -> None:
        if not self._settings.terminal_visual_bell_enabled:
            return
        self._visual_bell_active = True
        self._visual_bell_timer.start(self.VISUAL_BELL_DURATION_MS)
        self.request_full_repaint()

    def _clear_visual_bell(self) -> None:
        if not self._visual_bell_active:
            return
        self._visual_bell_active = False
        self.request_full_repaint()

    def show_center_message(self, text: str, duration_ms: int) -> None:
        message = text.strip()
        if not message:
            self._clear_center_message()
            return
        duration = max(0, int(duration_ms))
        if duration <= 0:
            self._clear_center_message()
            return
        self._center_message_text = message
        self._center_message_timer.start(duration)
        self._update_center_message_overlay()

    def _clear_center_message(self) -> None:
        if not self._center_message_text:
            return
        self._center_message_text = ""
        self._update_center_message_overlay()

    def _update_center_message_overlay(self) -> None:
        self.viewport().update()

    def _ensure_cursor_visible(self) -> None:
        self._cursor_visible = True
        if self._cursor_blink_enabled and self._session_connected:
            self._cursor_blink_timer.start()
        self.request_full_repaint()

    def _clear_selection(self) -> None:
        self._selection_anchor = None
        self._selection_cursor = None
        self._selecting = False
        self.request_full_repaint()

    def _has_selection(self) -> bool:
        return self._selection_anchor is not None and self._selection_cursor is not None

    def _cell_from_pos(self, x: int, y: int) -> tuple[int, int]:
        cols, rows = self.terminal_size()
        content = self._content_rect()
        col = max(0, min(cols - 1, max(0, x - content.left()) // self._cell_width_px))
        row = max(0, min(rows - 1, max(0, y - content.top()) // self._cell_height_px))
        return (col, row)

    def _cell_glyph(self, col: int, row: int) -> str:
        cols, rows = self.terminal_size()
        if not (0 <= col < cols and 0 <= row < rows):
            return " "
        screen = self._emulator.screen
        default_char = screen.default_char
        return getattr(screen.buffer.get(row, {}).get(col, default_char), "data", " ") or " "

    def _word_bounds_at_cell(self, cell: tuple[int, int]) -> tuple[int, int] | None:
        col, row = cell
        glyph = self._cell_glyph(col, row)
        if not glyph or glyph.isspace():
            return None
        cols, _ = self.terminal_size()
        start = col
        while start > 0:
            prev_glyph = self._cell_glyph(start - 1, row)
            if not prev_glyph or prev_glyph.isspace():
                break
            start -= 1
        end = col
        while end + 1 < cols:
            next_glyph = self._cell_glyph(end + 1, row)
            if not next_glyph or next_glyph.isspace():
                break
            end += 1
        return (start, end)

    def _select_word_at_cell(self, cell: tuple[int, int]) -> bool:
        bounds = self._word_bounds_at_cell(cell)
        self._selecting = False
        if bounds is None:
            self._selection_anchor = None
            self._selection_cursor = None
            return False
        start, end = bounds
        row = cell[1]
        self._selection_anchor = (start, row)
        self._selection_cursor = (end, row)
        return True

    def _selection_bounds(self, cols: int) -> tuple[int, int]:
        if not self._has_selection():
            return (0, -1)
        assert self._selection_anchor is not None
        assert self._selection_cursor is not None
        a_col, a_row = self._selection_anchor
        b_col, b_row = self._selection_cursor
        start = a_row * cols + a_col
        end = b_row * cols + b_col
        if start <= end:
            return (start, end)
        return (end, start)

    def _is_selected_cell(self, *, col: int, row: int, cols: int) -> bool:
        if not self._has_selection():
            return False
        start, end = self._selection_bounds(cols)
        idx = row * cols + col
        return start <= idx <= end

    def _selected_text(self) -> str:
        if not self._has_selection():
            return ""
        cols, rows = self.terminal_size()
        start, end = self._selection_bounds(cols)
        screen = self._emulator.screen
        default_char = screen.default_char
        chunks: list[str] = []
        line_chars: list[str] = []
        current_row = -1

        for idx in range(start, end + 1):
            row = idx // cols
            col = idx % cols
            if row >= rows:
                break
            if current_row == -1:
                current_row = row
            if row != current_row:
                chunks.append("".join(line_chars).rstrip())
                line_chars = []
                current_row = row
            glyph = getattr(screen.buffer.get(row, {}).get(col, default_char), "data", " ") or " "
            line_chars.append(glyph)

        chunks.append("".join(line_chars).rstrip())
        return "\n".join(chunks)

    def resolved_font_family(self) -> str:
        return self._draw_font.family()

    @staticmethod
    def _build_terminal_font(settings: AppSettings) -> QFont:
        desired = (settings.terminal_font_family or "").strip()
        families = [
            name for name in QFontDatabase.families() if QFontDatabase.isFixedPitch(name) and name.lower() != "fixedsys"
        ]

        preferred = ["Courier New", "Consolas", "Cascadia Mono", "Cascadia Code", "Lucida Console", "Courier"]
        ordered: list[str] = []
        for name in preferred:
            if name in families and name not in ordered:
                ordered.append(name)
        for name in families:
            if name not in ordered:
                ordered.append(name)

        family = ""
        if desired:
            for name in ordered:
                if name.lower() == desired.lower():
                    family = name
                    break
        if not family and ordered:
            family = ordered[0]
        if not family:
            family = "Courier New"

        font = QFont()
        font.setFamily(family)
        font.setPointSize(max(8, settings.terminal_font_pt))
        font.setKerning(False)
        return font


class _SnakeShHistoryScreen(HistoryScreen):
    __getattribute__ = object.__getattribute__
    _DRAW_CHAR_CACHE_MAX_ENTRIES = 4096
    _SGR_TRANSITION_CACHE_MAX_ENTRIES = 512

    def __init__(
        self,
        columns: int,
        lines: int,
        history: int = 100,
        ratio: float = 0.5,
        *,
        process_input_writer: Callable[[str], None] | None = None,
        terminal_type: str = "auto",
    ) -> None:
        self._pending_scroll_operations: list[_TerminalScrollOperation] = []
        self._draw_char_cache: OrderedDict[tuple[Char, str], Char] = OrderedDict()
        self._sgr_transition_cache: OrderedDict[tuple[Char, tuple[int, ...]], Char] = OrderedDict()
        self._last_graphic_char: str | None = None
        self._full_repaint_damage_pending = False
        self._process_input_writer = process_input_writer
        self._terminal_type = normalize_serial_terminal_type(terminal_type)
        super().__init__(columns, lines, history=history, ratio=ratio)

    def reset(self) -> None:
        super().reset()
        self._draw_char_cache.clear()
        self._sgr_transition_cache.clear()
        self._last_graphic_char = None
        self._full_repaint_damage_pending = False

    def _queue_scroll_operation(self, *, top: int, bottom: int, delta: int) -> None:
        if delta == 0:
            return
        normalized_top = max(0, min(self.lines - 1, top))
        normalized_bottom = max(normalized_top, min(self.lines - 1, bottom))
        self._pending_scroll_operations.append(
            _TerminalScrollOperation(
                top=normalized_top,
                bottom=normalized_bottom,
                delta=delta,
            )
        )

    def _raw_index(self) -> None:
        top, bottom = self.margins or (0, self.lines - 1)
        if self.cursor.y == bottom:
            self.history.top.append(self.buffer[top])
            self._queue_scroll_operation(top=top, bottom=bottom, delta=1)
            self.dirty.add(bottom)
            for y in range(top, bottom):
                self.buffer[y] = self.buffer[y + 1]
            self.buffer.pop(bottom, None)
            return
        self.cursor_down()

    def _raw_reverse_index(self) -> None:
        top, bottom = self.margins or (0, self.lines - 1)
        if self.cursor.y == top:
            self.history.bottom.append(self.buffer[bottom])
            self._queue_scroll_operation(top=top, bottom=bottom, delta=-1)
            self.dirty.add(top)
            for y in range(bottom, top, -1):
                self.buffer[y] = self.buffer[y - 1]
            self.buffer.pop(top, None)
            return
        self.cursor_up()

    def _raw_insert_lines(self, count: Optional[int] = None) -> None:
        count = count or 1
        top, bottom = self.margins or (0, self.lines - 1)
        if not top <= self.cursor.y <= bottom:
            return
        line_count = min(max(1, count), bottom - self.cursor.y + 1)
        self._queue_scroll_operation(top=self.cursor.y, bottom=bottom, delta=-line_count)
        self.dirty.update(range(self.cursor.y, self.cursor.y + line_count))
        for y in range(bottom, self.cursor.y - 1, -1):
            if y - line_count >= self.cursor.y and (y - line_count) in self.buffer:
                self.buffer[y] = self.buffer[y - line_count]
            else:
                self.buffer.pop(y, None)
        self.carriage_return()

    def _raw_delete_lines(self, count: Optional[int] = None) -> None:
        count = count or 1
        top, bottom = self.margins or (0, self.lines - 1)
        if not top <= self.cursor.y <= bottom:
            return
        line_count = min(max(1, count), bottom - self.cursor.y + 1)
        self._queue_scroll_operation(top=self.cursor.y, bottom=bottom, delta=line_count)
        self.dirty.update(range(max(self.cursor.y, bottom - line_count + 1), bottom + 1))
        for y in range(self.cursor.y, bottom + 1):
            if y + line_count <= bottom and (y + line_count) in self.buffer:
                self.buffer[y] = self.buffer.pop(y + line_count)
            else:
                self.buffer.pop(y, None)
        self.carriage_return()

    def _raw_scroll_up_lines(self, count: int = 1) -> None:
        count = max(1, count)
        cursor_x = self.cursor.x
        cursor_y = self.cursor.y
        top, bottom = self.margins or (0, self.lines - 1)
        region_height = max(1, bottom - top + 1)
        self.cursor.y = top
        self._raw_delete_lines(min(count, region_height))
        self.cursor.x = cursor_x
        self.cursor.y = cursor_y

    def _raw_scroll_down_lines(self, count: int = 1) -> None:
        count = max(1, count)
        cursor_x = self.cursor.x
        cursor_y = self.cursor.y
        top, bottom = self.margins or (0, self.lines - 1)
        region_height = max(1, bottom - top + 1)
        self.cursor.y = top
        self._raw_insert_lines(min(count, region_height))
        self.cursor.x = cursor_x
        self.cursor.y = cursor_y

    index = _raw_index
    reverse_index = _raw_reverse_index
    insert_lines = _raw_insert_lines
    delete_lines = _raw_delete_lines
    scroll_up_lines = _raw_scroll_up_lines
    scroll_down_lines = _raw_scroll_down_lines

    def _mark_full_repaint_damage(self) -> None:
        self._full_repaint_damage_pending = True

    def insert_characters(self, count: Optional[int] = None) -> None:
        self._mark_full_repaint_damage()
        super().insert_characters(count)

    def delete_characters(self, count: Optional[int] = None) -> None:
        self._mark_full_repaint_damage()
        super().delete_characters(count)

    def erase_characters(self, count: Optional[int] = None) -> None:
        self._mark_full_repaint_damage()
        super().erase_characters(count)

    def erase_in_line(self, how: int = 0, private: bool = False) -> None:
        self._mark_full_repaint_damage()
        super().erase_in_line(how, private=private)

    def erase_in_display(self, how: int = 0, *args, **kwargs) -> None:
        self._mark_full_repaint_damage()
        super().erase_in_display(how, *args, **kwargs)

    def draw(self, data: str) -> None:
        if (
            data
            and data.isascii()
            and pyte.modes.IRM not in self.mode
            and self._active_charset_map() == pyte.charsets.LAT1_MAP
        ):
            self._draw_ascii_text(data)
            return
        super().draw(data)
        last_graphic = self._last_graphic_in_text(data)
        if last_graphic is not None:
            self._last_graphic_char = last_graphic

    def select_graphic_rendition(self, *attrs: int) -> None:
        if not attrs or attrs == (0,):
            self.cursor.attrs = self.default_char
            return
        cache_key = (self.cursor.attrs, attrs)
        cached = self._sgr_transition_cache.get(cache_key)
        if cached is not None:
            self._sgr_transition_cache.move_to_end(cache_key)
            self.cursor.attrs = cached
            return
        super().select_graphic_rendition(*attrs)
        self._sgr_transition_cache[cache_key] = self.cursor.attrs
        if len(self._sgr_transition_cache) > self._SGR_TRANSITION_CACHE_MAX_ENTRIES:
            self._sgr_transition_cache.popitem(last=False)

    def repeat_last_character(self, count: Optional[int] = None) -> None:
        last_graphic = self._last_graphic_char
        if not last_graphic:
            return
        repeat_count = max(1, count or 1)
        self.draw(last_graphic * min(repeat_count, 65535))

    def _draw_ascii_text(self, data: str) -> None:
        cursor = self.cursor
        columns = self.columns
        dirty = self.dirty
        auto_wrap = pyte.modes.DECAWM in self.mode
        draw_cache = self._draw_char_cache
        attrs = cursor.attrs
        last_graphic = self._last_graphic_char
        index = 0
        length = len(data)

        while index < length:
            if cursor.x == columns:
                if auto_wrap:
                    dirty.add(cursor.y)
                    self.carriage_return()
                    self.linefeed()
                else:
                    cursor.x = max(0, columns - 1)

            line = self.buffer[cursor.y]
            remaining_columns = max(0, columns - cursor.x)
            if remaining_columns == 0:
                index += 1
                continue
            segment = data[index : index + remaining_columns]
            x = cursor.x
            for offset, glyph in enumerate(segment):
                cache_key = (attrs, glyph)
                rendered = draw_cache.get(cache_key)
                if rendered is None:
                    rendered = attrs._replace(data=glyph)
                    draw_cache[cache_key] = rendered
                    if len(draw_cache) > self._DRAW_CHAR_CACHE_MAX_ENTRIES:
                        draw_cache.popitem(last=False)
                else:
                    draw_cache.move_to_end(cache_key)
                line[x + offset] = rendered
            cursor.x = min(x + len(segment), columns)
            last_graphic = segment[-1]
            index += len(segment)

        self._last_graphic_char = last_graphic
        dirty.add(cursor.y)

    def _active_charset_map(self) -> str:
        return self.g1_charset if self.charset else self.g0_charset

    @staticmethod
    def _last_graphic_in_text(data: str) -> str | None:
        for glyph in reversed(data):
            if ord(glyph) >= 0x20 and glyph != "\x7f":
                return glyph
        return None

    def render_scrollback_lines(self) -> list[str]:
        top_lines = [self._render_line_text(line).rstrip() for line in self.history.top]
        screen_lines = self._render_visible_screen_lines()
        bottom_lines = [self._render_line_text(line).rstrip() for line in self.history.bottom]
        return top_lines + screen_lines + bottom_lines

    def _render_visible_screen_lines(self) -> list[str]:
        rendered = [self._render_line_text(self.buffer[y]).rstrip() for y in range(self.lines)]
        if self.history.bottom or self.history.position != self.history.size:
            return rendered
        cursor_y = max(0, min(self.lines - 1, int(self.cursor.y)))
        visible = rendered[: cursor_y + 1]
        if visible and visible[-1] == "":
            visible.pop()
        return visible

    def _render_line_text(self, line) -> str:
        rendered: list[str] = []
        skip_wide_stub = False
        for x in range(self.columns):
            if skip_wide_stub:
                skip_wide_stub = False
                continue
            char = line[x].data
            if char:
                skip_wide_stub = wcwidth(char[0]) == 2
            rendered.append(char)
        return "".join(rendered)

    def prepare_for_stream_output(self) -> None:
        history = self.history
        if history.position < history.size:
            self.scroll_history_down(history.size - history.position)

    def finish_stream_output(self) -> None:
        self.cursor.hidden = not (
            self.history.position == self.history.size
            and pyte.modes.DECTCEM in self.mode
        )

    def consume_render_damage(self) -> tuple[tuple[_TerminalScrollOperation, ...], bool]:
        operations = tuple(self._pending_scroll_operations)
        self._pending_scroll_operations.clear()
        structural_damage = self._full_repaint_damage_pending
        self._full_repaint_damage_pending = False
        return (operations, structural_damage)

    def write_process_input(self, data: str) -> None:
        writer = self._process_input_writer
        if writer is None or not data:
            return
        writer(data)

    def set_terminal_type(self, terminal_type: str) -> None:
        self._terminal_type = normalize_serial_terminal_type(terminal_type)

    def report_device_attributes(self, mode: int = 0, **kwargs: bool) -> None:
        if mode == 0 and not kwargs.get("private"):
            self.write_process_input(_terminal_device_attributes_reply(self._terminal_type))

    def scroll_history_up(self, count: int) -> None:
        remaining = max(0, int(count))
        while remaining > 0 and self.history.position > self.lines and self.history.top:
            mid = min(remaining, len(self.history.top), self.lines)
            self.history.bottom.extendleft(
                self.buffer[y]
                for y in range(self.lines - 1, self.lines - mid - 1, -1)
            )
            self.history = self.history._replace(position=self.history.position - mid)
            for y in range(self.lines - 1, mid - 1, -1):
                self.buffer[y] = self.buffer[y - mid]
            for y in range(mid - 1, -1, -1):
                self.buffer[y] = self.history.top.pop()
            remaining -= mid
        if count > 0:
            self.dirty = set(range(self.lines))

    def scroll_history_down(self, count: int) -> None:
        remaining = max(0, int(count))
        while remaining > 0 and self.history.position < self.history.size and self.history.bottom:
            mid = min(remaining, len(self.history.bottom), self.lines)
            self.history.top.extend(self.buffer[y] for y in range(mid))
            self.history = self.history._replace(position=self.history.position + mid)
            for y in range(self.lines - mid):
                self.buffer[y] = self.buffer[y + mid]
            for y in range(self.lines - mid, self.lines):
                self.buffer[y] = self.history.bottom.popleft()
            remaining -= mid
        if count > 0:
            self.dirty = set(range(self.lines))


class _SnakeShStream(pyte.Stream):
    csi = dict(pyte.Stream.csi)
    csi["S"] = "scroll_up_lines"
    csi["T"] = "scroll_down_lines"
    csi["b"] = "repeat_last_character"
    _ESC = pyte.control.ESC
    _CSI_C1 = pyte.control.CSI_C1
    _OSC_C1 = pyte.control.OSC_C1
    _NUL_OR_DEL = frozenset((pyte.control.NUL, pyte.control.DEL))
    _ALLOWED_IN_CSI = frozenset(
        (
            pyte.control.BEL,
            pyte.control.BS,
            pyte.control.HT,
            pyte.control.LF,
            pyte.control.VT,
            pyte.control.FF,
            pyte.control.CR,
        )
    )
    _SP_OR_GT = frozenset((pyte.control.SP, ">"))
    _CAN_OR_SUB = frozenset((pyte.control.CAN, pyte.control.SUB))
    _PRIVATE_KWARG_CSI_FINALS = frozenset(("J", "K", "c", "h", "l"))

    def __init__(self, screen: HistoryScreen, strict: bool = True, *, enable_fast_parser: bool = True) -> None:
        super().__init__(screen, strict=strict)
        self._enable_fast_parser = enable_fast_parser
        self._unknown_sequence_counts: dict[tuple[str, str, str], int] = {}
        self._debug_unknown_sequences_enabled = _env_flag_enabled(TERMINAL_DEBUG_UNKNOWN_SEQUENCES_ENV)
        self._scan_tail = ""
        self._fast_tail = ""
        self._basic_dispatch: dict[str, Callable[..., None]] = {}
        self._escape_dispatch: dict[str, Callable[..., None]] = {}
        self._sharp_dispatch: dict[str, Callable[..., None]] = {}
        self._csi_dispatch: dict[str, Callable[..., None]] = {}
        self._refresh_dispatch_tables()

    def feed(self, data: str) -> None:
        if self._debug_unknown_sequences_enabled and data:
            self._scan_for_unknown_sequences(data)
        listener = self.listener
        if isinstance(listener, _SnakeShHistoryScreen):
            listener.prepare_for_stream_output()
            try:
                if self._enable_fast_parser:
                    self._feed_fast(data, listener)
                    return
                super().feed(data)
            finally:
                listener.finish_stream_output()
            return
        super().feed(data)

    def unknown_sequence_counts(self) -> dict[tuple[str, str, str], int]:
        return dict(self._unknown_sequence_counts)

    def _refresh_dispatch_tables(self) -> None:
        listener = self.listener
        if listener is None:
            self._basic_dispatch = {}
            self._escape_dispatch = {}
            self._sharp_dispatch = {}
            self._csi_dispatch = {}
            return
        self._basic_dispatch = {
            event: getattr(listener, attr)
            for event, attr in self.basic.items()
        }
        self._escape_dispatch = {
            event: getattr(listener, attr)
            for event, attr in self.escape.items()
        }
        self._sharp_dispatch = {
            event: getattr(listener, attr)
            for event, attr in self.sharp.items()
        }
        self._csi_dispatch = {
            event: getattr(listener, attr)
            for event, attr in self.csi.items()
        }

    def _feed_fast(self, data: str, listener: _SnakeShHistoryScreen) -> None:
        if not data and not self._fast_tail:
            return
        text = self._fast_tail + data
        self._fast_tail = ""
        match_text = self._text_pattern.match
        index = 0
        length = len(text)

        while index < length:
            match = match_text(text, index)
            if match is not None:
                start, end = match.span()
                if end > start:
                    listener.draw(text[start:end])
                    index = end
                    continue

            ch = text[index]
            if ch in self._basic_dispatch:
                self._basic_dispatch[ch]()
                index += 1
                continue
            if ch in self._NUL_OR_DEL:
                index += 1
                continue
            if ch == self._ESC:
                result = self._consume_fast_escape(text, index)
                if result is None:
                    self._fast_tail = text[index:]
                    return
                index = result
                continue
            if ch == self._CSI_C1:
                result = self._consume_fast_csi(text, index, prefix_length=1)
                if result is None:
                    self._fast_tail = text[index:]
                    return
                index = result
                continue
            if ch == self._OSC_C1:
                result = self._consume_fast_osc(text, index, prefix_length=1)
                if result is None:
                    self._fast_tail = text[index:]
                    return
                index = result
                continue
            listener.draw(ch)
            index += 1

    def _consume_fast_escape(self, text: str, start: int) -> int | None:
        length = len(text)
        if start + 1 >= length:
            return None

        esc_kind = text[start + 1]
        if esc_kind == "[":
            return self._consume_fast_csi(text, start, prefix_length=2)
        if esc_kind == "]":
            return self._consume_fast_osc(text, start, prefix_length=2)
        if esc_kind == "#":
            if start + 2 >= length:
                return None
            action = self._sharp_dispatch.get(text[start + 2])
            end = start + 3
            if action is None:
                self._fallback_feed_slice(text[start:end])
            else:
                action()
            return end
        if esc_kind in ("(", ")"):
            if start + 2 >= length:
                return None
            if not self.use_utf8 and self.listener is not None:
                self.listener.define_charset(text[start + 2], mode=esc_kind)
            return start + 3
        if esc_kind == "%":
            if start + 2 >= length:
                return None
            self.select_other_charset(text[start + 2])
            return start + 3

        action = self._escape_dispatch.get(esc_kind)
        end = start + 2
        if action is None:
            self._fallback_feed_slice(text[start:end])
        else:
            action()
        return end

    def _consume_fast_csi(self, text: str, start: int, *, prefix_length: int) -> int | None:
        length = len(text)
        cursor = start + prefix_length
        params: list[int] = []
        current = 0
        has_current = False
        private = False
        secondary = False

        while cursor < length:
            ch = text[cursor]
            if ch == "?":
                private = True
                cursor += 1
                continue
            if ch == ">":
                secondary = True
                cursor += 1
                continue
            if ch in self._ALLOWED_IN_CSI:
                action = self._basic_dispatch.get(ch)
                if action is not None:
                    action()
                cursor += 1
                continue
            if ch in self._SP_OR_GT:
                cursor += 1
                continue
            if ch in self._CAN_OR_SUB:
                if self.listener is not None:
                    self.listener.draw(ch)
                return cursor + 1
            if "0" <= ch <= "9":
                current = min(9999, (current * 10) + (ord(ch) - ord("0")))
                has_current = True
                cursor += 1
                continue
            if ch == ";":
                params.append(current if has_current else 0)
                current = 0
                has_current = False
                cursor += 1
                continue
            if ch == "$":
                if cursor + 1 >= length:
                    return None
                end = cursor + 2
                self._fallback_feed_slice(text[start:end])
                return end
            if ord(ch) < 0x40:
                end = self._find_csi_sequence_end(text, cursor)
                if end is None:
                    return None
                self._fallback_feed_slice(text[start:end])
                return end

            params.append(current if has_current else 0)
            action = self._csi_dispatch.get(ch)
            end = cursor + 1
            if secondary:
                return end
            if private and ch == "m":
                return end
            if action is None:
                self._fallback_feed_slice(text[start:end])
                return end
            if private and ch not in self._PRIVATE_KWARG_CSI_FINALS:
                self._fallback_feed_slice(text[start:end])
                return end
            if private:
                action(*params, private=True)
            else:
                action(*params)
            return end

        return None

    def _consume_fast_osc(self, text: str, start: int, *, prefix_length: int) -> int | None:
        end = self._find_osc_sequence_end(text, start + prefix_length)
        if end is None:
            return None
        self._fallback_feed_slice(text[start:end])
        return end

    def _fallback_feed_slice(self, text: str) -> None:
        if text:
            super().feed(text)

    def _scan_for_unknown_sequences(self, data: str) -> None:
        text = self._scan_tail + data
        index = 0
        length = len(text)
        self._scan_tail = ""

        while index < length:
            if text[index] != "\x1b":
                index += 1
                continue
            if index + 1 >= length:
                self._scan_tail = text[index:]
                break

            esc_kind = text[index + 1]

            if esc_kind == "[":
                result = self._consume_csi_sequence(text, index)
                if result is None:
                    self._scan_tail = text[index:]
                    break
                final, private_prefix, sample, end = result
                if final not in self.csi:
                    self._record_unknown_sequence("CSI", private_prefix, final, sample)
                index = end
                continue

            if esc_kind == "]":
                osc_end = self._consume_osc_sequence(text, index)
                if osc_end is None:
                    self._scan_tail = text[index:]
                    break
                index = osc_end
                continue

            if esc_kind == "#":
                if index + 2 >= length:
                    self._scan_tail = text[index:]
                    break
                final = text[index + 2]
                if final not in self.sharp:
                    self._record_unknown_sequence("SHARP", "", final, text[index : index + 3])
                index += 3
                continue

            if esc_kind in ("(", ")", "%"):
                if index + 2 >= length:
                    self._scan_tail = text[index:]
                    break
                index += 3
                continue

            if esc_kind not in self.escape:
                self._record_unknown_sequence("ESC", "", esc_kind, text[index : index + 2])
            index += 2

        if len(self._scan_tail) > 64:
            self._scan_tail = self._scan_tail[-64:]

    def _consume_csi_sequence(self, text: str, start: int) -> tuple[str, str, str, int] | None:
        length = len(text)
        cursor = start + 2
        private_prefix = ""
        if cursor < length and text[cursor] in "<=>?":
            private_prefix = text[cursor]
            cursor += 1
        while cursor < length:
            codepoint = ord(text[cursor])
            if 0x40 <= codepoint <= 0x7E:
                end = cursor + 1
                return text[cursor], private_prefix, text[start:end], end
            cursor += 1
        return None

    def _consume_osc_sequence(self, text: str, start: int) -> int | None:
        return self._find_osc_sequence_end(text, start + 2)

    @classmethod
    def _find_csi_sequence_end(cls, text: str, start: int) -> int | None:
        cursor = start
        length = len(text)
        while cursor < length:
            codepoint = ord(text[cursor])
            if 0x40 <= codepoint <= 0x7E:
                return cursor + 1
            cursor += 1
        return None

    @classmethod
    def _find_osc_sequence_end(cls, text: str, start: int) -> int | None:
        cursor = start
        length = len(text)
        while cursor < length:
            ch = text[cursor]
            if ch == "\x07":
                return cursor + 1
            if ch == cls._ESC:
                if cursor + 1 >= length:
                    return None
                if text[cursor + 1] == "\\":
                    return cursor + 2
            cursor += 1
        return None

    def _record_unknown_sequence(self, kind: str, private_prefix: str, final: str, sample: str) -> None:
        key = (kind, private_prefix, final)
        count = self._unknown_sequence_counts.get(key, 0) + 1
        self._unknown_sequence_counts[key] = count
        if count != 1:
            return
        sample_text = sample.encode("unicode_escape").decode("ascii")
        _LOGGER.warning(
            "Unsupported terminal %s sequence detected: private=%r final=%r sample=%s",
            kind,
            private_prefix,
            final,
            sample_text,
        )


class _TerminalFormattingCompactor:
    _SGR_CATEGORY_ORDER = (
        "intensity",
        "italic",
        "underline",
        "blink",
        "reverse",
        "conceal",
        "strike",
        "fg",
        "bg",
    )

    def __init__(self) -> None:
        self._tail = ""
        self._pending_reset = False
        self._pending_sgr_tokens: dict[str, tuple[int, ...]] = {}
        self._pending_designations: dict[str, str] = {}
        self._current_designations = {"(": "B", ")": "B"}

    def feed(self, data: str) -> str:
        if not data and not self._tail:
            return ""
        text = self._tail + data
        self._tail = ""
        out: list[str] = []
        index = 0
        length = len(text)

        while index < length:
            ch = text[index]
            if ch != "\x1b":
                if self._has_pending_formatting():
                    out.append(self._flush_pending_formatting())
                out.append(ch)
                index += 1
                continue

            if index + 1 >= length:
                self._tail = text[index:]
                break

            esc_kind = text[index + 1]

            if esc_kind == "[":
                csi = self._consume_csi_sequence(text, index)
                if csi is None:
                    self._tail = text[index:]
                    break
                sample, end = csi
                if self._accumulate_sgr_sequence(sample):
                    index = end
                    continue
                if self._has_pending_formatting():
                    out.append(self._flush_pending_formatting())
                out.append(sample)
                index = end
                continue

            if esc_kind in {"(", ")"}:
                if index + 2 >= length:
                    self._tail = text[index:]
                    break
                self._pending_designations[esc_kind] = text[index + 2]
                index += 3
                continue

            if esc_kind == "]":
                osc_end = self._consume_osc_sequence(text, index)
                if osc_end is None:
                    self._tail = text[index:]
                    break
                if self._has_pending_formatting():
                    out.append(self._flush_pending_formatting())
                out.append(text[index:osc_end])
                index = osc_end
                continue

            if self._has_pending_formatting():
                out.append(self._flush_pending_formatting())

            end = index + 2
            if esc_kind in ("(", ")", "#", "%") and end < length:
                end += 1
            out.append(text[index:end])
            index = end

        return "".join(out)

    def has_pending_state(self) -> bool:
        return bool(self._tail) or self._has_pending_formatting()

    def _has_pending_formatting(self) -> bool:
        return self._pending_reset or bool(self._pending_sgr_tokens) or bool(self._pending_designations)

    def _flush_pending_formatting(self) -> str:
        out: list[str] = []
        for slot in ("(", ")"):
            charset = self._pending_designations.pop(slot, None)
            if charset is None:
                continue
            if self._current_designations.get(slot) == charset:
                continue
            self._current_designations[slot] = charset
            out.append(f"\x1b{slot}{charset}")

        if self._pending_reset or self._pending_sgr_tokens:
            params: list[str] = []
            if self._pending_reset:
                if not self._pending_sgr_tokens:
                    out.append("\x1b[m")
                else:
                    params.append("0")
            if self._pending_sgr_tokens:
                for category in self._SGR_CATEGORY_ORDER:
                    token = self._pending_sgr_tokens.get(category)
                    if token is None:
                        continue
                    params.extend(str(value) for value in token)
                out.append(f"\x1b[{';'.join(params)}m")
            self._pending_reset = False
            self._pending_sgr_tokens.clear()

        return "".join(out)

    def _accumulate_sgr_sequence(self, sample: str) -> bool:
        if not sample.endswith("m"):
            return False
        params_text = sample[2:-1]
        if not params_text:
            params = [0]
        else:
            if any(ch not in "0123456789;" for ch in params_text):
                return False
            try:
                params = [int(part) if part else 0 for part in params_text.split(";")]
            except ValueError:
                return False

        pending_reset = self._pending_reset
        pending_tokens = dict(self._pending_sgr_tokens)
        index = 0
        while index < len(params):
            code = params[index]
            if code == 0:
                pending_reset = True
                pending_tokens.clear()
                index += 1
                continue
            if code in {1, 2, 22}:
                pending_tokens["intensity"] = (code,)
                index += 1
                continue
            if code in {3, 23}:
                pending_tokens["italic"] = (code,)
                index += 1
                continue
            if code in {4, 24}:
                pending_tokens["underline"] = (code,)
                index += 1
                continue
            if code in {5, 6, 25}:
                pending_tokens["blink"] = (code,)
                index += 1
                continue
            if code in {7, 27}:
                pending_tokens["reverse"] = (code,)
                index += 1
                continue
            if code in {8, 28}:
                pending_tokens["conceal"] = (code,)
                index += 1
                continue
            if code in {9, 29}:
                pending_tokens["strike"] = (code,)
                index += 1
                continue
            if code == 39 or 30 <= code <= 37 or 90 <= code <= 97:
                pending_tokens["fg"] = (code,)
                index += 1
                continue
            if code == 49 or 40 <= code <= 47 or 100 <= code <= 107:
                pending_tokens["bg"] = (code,)
                index += 1
                continue
            if code in {38, 48}:
                token, next_index = self._consume_extended_color(params, index)
                if token is None:
                    return False
                pending_tokens["fg" if code == 38 else "bg"] = token
                index = next_index
                continue
            return False

        self._pending_reset = pending_reset
        self._pending_sgr_tokens = pending_tokens
        return True

    @staticmethod
    def _consume_extended_color(params: list[int], index: int) -> tuple[tuple[int, ...] | None, int]:
        if index + 1 >= len(params):
            return None, index
        mode = params[index + 1]
        if mode == 5:
            if index + 2 >= len(params):
                return None, index
            return ((params[index], 5, params[index + 2]), index + 3)
        if mode == 2:
            if index + 4 >= len(params):
                return None, index
            return ((params[index], 2, params[index + 2], params[index + 3], params[index + 4]), index + 5)
        return None, index

    @staticmethod
    def _consume_csi_sequence(text: str, start: int) -> tuple[str, int] | None:
        length = len(text)
        cursor = start + 2
        while cursor < length:
            codepoint = ord(text[cursor])
            if 0x40 <= codepoint <= 0x7E:
                end = cursor + 1
                return (text[start:end], end)
            cursor += 1
        return None

    @staticmethod
    def _consume_osc_sequence(text: str, start: int) -> int | None:
        cursor = start + 2
        length = len(text)
        while cursor < length:
            ch = text[cursor]
            if ch == "\x07":
                return cursor + 1
            if ch == "\x1b":
                if cursor + 1 >= length:
                    return None
                if text[cursor + 1] == "\\":
                    return cursor + 2
            cursor += 1
        return None


class VT100Emulator:
    _APPLICATION_CURSOR_KEYS_MODE = 1 << 5
    _BACKSPACE_CTRL_H_MODE = 67 << 5

    def __init__(
        self,
        cols: int,
        rows: int,
        history: int = 5000,
        *,
        enable_fast_parser: bool = True,
        process_input_writer: Callable[[str], None] | None = None,
        terminal_type: str = "auto",
    ) -> None:
        self._history_limit = max(100, history)
        self._enable_fast_parser = bool(enable_fast_parser)
        self._process_input_writer = process_input_writer
        self._terminal_type = normalize_serial_terminal_type(terminal_type)
        self._screen = _SnakeShHistoryScreen(
            cols,
            rows,
            history=history,
            ratio=0.2,
            process_input_writer=process_input_writer,
            terminal_type=self._terminal_type,
        )
        self._stream = _SnakeShStream(self._screen, enable_fast_parser=enable_fast_parser)
        # pyte only handles SI/SO and ESC ( / ESC ) DEC charset switching when
        # UTF-8 mode is disabled. This is needed for ncurses line-drawing.
        self._stream.use_utf8 = False
        self._cols = cols
        self._rows = rows
        self._lock = threading.RLock()

    def feed(self, data: str) -> str:
        with self._lock:
            self._stream.feed(data)
        return data

    def consume_dirty_rows(self) -> set[int]:
        with self._lock:
            dirty = set(self._screen.dirty)
            self._screen.dirty.clear()
        return dirty

    def consume_render_damage(self) -> tuple[tuple[_TerminalScrollOperation, ...], bool]:
        with self._lock:
            return self._screen.consume_render_damage()

    def unknown_sequence_counts(self) -> dict[tuple[str, str, str], int]:
        with self._lock:
            return self._stream.unknown_sequence_counts()

    def cursor_row(self) -> int | None:
        with self._lock:
            cursor = getattr(self._screen, "cursor", None)
            if cursor is None:
                return None
            return int(cursor.y)

    def application_cursor_mode_enabled(self) -> bool:
        with self._lock:
            return self._APPLICATION_CURSOR_KEYS_MODE in self._screen.mode

    def backspace_sends_ctrl_h(self) -> bool:
        with self._lock:
            return self._BACKSPACE_CTRL_H_MODE in self._screen.mode

    def set_terminal_type(self, terminal_type: str) -> None:
        with self._lock:
            self._terminal_type = normalize_serial_terminal_type(terminal_type)
            self._screen.set_terminal_type(self._terminal_type)

    def resize(self, cols: int, rows: int) -> None:
        cols = max(10, cols)
        rows = max(5, rows)
        if cols == self._cols and rows == self._rows:
            return
        with self._lock:
            rendered_before = self._screen.render_scrollback_lines()
            had_visible_text = any(line.strip() for line in rendered_before)
            self._cols = cols
            self._rows = rows
            self._screen.resize(lines=rows, columns=cols)
            if had_visible_text:
                rendered_after = self._screen.render_scrollback_lines()
                if not any(line.strip() for line in rendered_after):
                    self._restore_text_snapshot(rendered_before, cols=cols, rows=rows)

    def set_history_limit(self, limit: int) -> None:
        limit = max(100, limit)
        with self._lock:
            self._history_limit = limit
            hist = self._screen.history
            old_size = hist.size
            at_bottom = hist.position >= old_size
            top = deque(hist.top, maxlen=limit)
            bottom = deque(hist.bottom, maxlen=limit)
            new_pos = limit if at_bottom else min(hist.position, limit)
            self._screen.history = hist._replace(top=top, bottom=bottom, size=limit, position=new_pos)

    def scroll_up(self, lines: int) -> None:
        if lines <= 0:
            return
        with self._lock:
            self._screen.scroll_history_up(lines)

    def scroll_down(self, lines: int) -> None:
        if lines <= 0:
            return
        with self._lock:
            self._screen.scroll_history_down(lines)

    def rendered_scrollback_lines(self) -> list[str]:
        with self._lock:
            return self._screen.render_scrollback_lines()

    def rendered_scrollback_text(self) -> str:
        lines = self.rendered_scrollback_lines()
        if not lines:
            return "(no scrollback data yet)"
        return "\n".join(lines)

    @property
    def screen(self) -> HistoryScreen:
        return self._screen

    def _restore_text_snapshot(self, lines: list[str], *, cols: int, rows: int) -> None:
        restored_screen = _SnakeShHistoryScreen(
            cols,
            rows,
            history=self._history_limit,
            ratio=0.2,
            process_input_writer=self._process_input_writer,
            terminal_type=self._terminal_type,
        )
        restored_stream = _SnakeShStream(restored_screen, enable_fast_parser=self._enable_fast_parser)
        restored_stream.use_utf8 = False
        snapshot_text = "\r\n".join(lines).strip("\r\n")
        if snapshot_text:
            restored_stream.feed(snapshot_text)
        self._screen = restored_screen
        self._stream = restored_stream

class SessionDetailsTab(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self._view = QTextEdit(self)
        self._view.setReadOnly(True)
        self._view.setAcceptRichText(False)
        self._view.setPlaceholderText("Select a session to see details.")
        layout.addWidget(self._view)

    def clear(self) -> None:
        self._view.clear()

    def setPlainText(self, text: str) -> None:  # noqa: N802
        self._view.setPlainText(text)

    def setPlaceholderText(self, text: str) -> None:  # noqa: N802
        self._view.setPlaceholderText(text)

    def setFont(self, font: QFont) -> None:  # noqa: N802
        self._view.setFont(font)

    def setStyleSheet(self, style: str) -> None:  # noqa: N802
        self._view.setStyleSheet(style)


class MainWorkspaceSplitterHandle(QSplitterHandle):
    def __init__(self, orientation: Qt.Orientation, parent: QSplitter) -> None:
        super().__init__(orientation, parent)
        if orientation == Qt.Horizontal:
            self.setCursor(Qt.SplitHCursor)
        else:
            self.setCursor(Qt.SplitVCursor)

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        grip_color = self.palette().color(self.foregroundRole())
        if not grip_color.isValid():
            grip_color = QColor("#94a3b8")
        grip_color.setAlpha(215)
        painter.setPen(Qt.NoPen)
        painter.setBrush(grip_color)

        rect = self.rect()
        if self.orientation() == Qt.Horizontal:
            dash_width = max(8, min(rect.width() - 4, 12))
            dash_height = 2
            dash_gap = 3
            dash_count = 5
            total_height = (dash_count * dash_height) + ((dash_count - 1) * dash_gap)
            start_y = rect.center().y() - (total_height // 2)
            x = rect.center().x() - (dash_width // 2)
            for index in range(dash_count):
                y = start_y + (index * (dash_height + dash_gap))
                painter.drawRoundedRect(x, y, dash_width, dash_height, 1, 1)
            return

        dash_width = 2
        dash_height = max(8, min(rect.height() - 4, 12))
        dash_gap = 3
        dash_count = 5
        total_width = (dash_count * dash_width) + ((dash_count - 1) * dash_gap)
        start_x = rect.center().x() - (total_width // 2)
        y = rect.center().y() - (dash_height // 2)
        for index in range(dash_count):
            x = start_x + (index * (dash_width + dash_gap))
            painter.drawRoundedRect(x, y, dash_width, dash_height, 1, 1)


class MainWorkspaceSplitter(QSplitter):
    def createHandle(self) -> QSplitterHandle:  # noqa: N802
        return MainWorkspaceSplitterHandle(self.orientation(), self)


class CommandInputLineEdit(QLineEdit):
    paste_requested = Signal(str)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.matches(QKeySequence.Paste):
            self.paste_requested.emit(QApplication.clipboard().text())
            return
        super().keyPressEvent(event)

    def _accept_local_path_drop_event(self, event) -> bool:
        if _local_paths_from_mime_data(event.mimeData()):
            event.acceptProposedAction()
            return True
        return False

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if self._accept_local_path_drop_event(event):
            return
        try:
            super().dragEnterEvent(event)
        except TypeError:
            pass

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if self._accept_local_path_drop_event(event):
            return
        try:
            super().dragMoveEvent(event)
        except TypeError:
            pass

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        local_paths = _local_paths_from_mime_data(event.mimeData())
        if local_paths:
            payload = _format_dropped_local_paths(local_paths)
            if payload:
                self.setFocus(Qt.MouseFocusReason)
                self.insert(payload)
                event.acceptProposedAction()
                return
        try:
            super().dropEvent(event)
        except TypeError:
            pass

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        menu = QMenu(self)
        undo_action = menu.addAction("Undo")
        redo_action = menu.addAction("Redo")
        menu.addSeparator()
        cut_action = menu.addAction("Cut")
        copy_action = menu.addAction("Copy")
        paste_action = menu.addAction("Paste")
        delete_action = menu.addAction("Delete")
        menu.addSeparator()
        select_all_action = menu.addAction("Select All")

        undo_action.setEnabled(self.isUndoAvailable())
        redo_action.setEnabled(self.isRedoAvailable())
        cut_action.setEnabled(self.hasSelectedText())
        copy_action.setEnabled(self.hasSelectedText())
        paste_action.setEnabled(bool(QApplication.clipboard().text()))
        delete_action.setEnabled(self.hasSelectedText())
        select_all_action.setEnabled(bool(self.text()))

        chosen = menu.exec(event.globalPos())
        if chosen == undo_action:
            self.undo()
        elif chosen == redo_action:
            self.redo()
        elif chosen == cut_action:
            self.cut()
        elif chosen == copy_action:
            self.copy()
        elif chosen == paste_action:
            self.paste_requested.emit(QApplication.clipboard().text())
        elif chosen == delete_action:
            self.del_()
        elif chosen == select_all_action:
            self.selectAll()
        menu.deleteLater()


class UploadProgressDialog(QDialog):
    def __init__(self, item_count: int, *, operation_label: str = "Uploading", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(operation_label)
        self.setWindowModality(Qt.ApplicationModal)
        self.setMinimumWidth(520)
        self._item_count = max(1, item_count)
        self._cancel_requested = False
        self._started_at = time.perf_counter()
        self._last_paint = 0.0

        layout = QVBoxLayout(self)
        self.summary_label = QLabel(f"Preparing {operation_label.lower()}...")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.percent_label = QLabel("0%")
        self.speed_label = QLabel("Speed: 0 B/s")
        self.time_label = QLabel("Time: 0s elapsed")
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self._cancel)

        layout.addWidget(self.summary_label)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.percent_label)
        layout.addWidget(self.speed_label)
        layout.addWidget(self.time_label)
        layout.addWidget(self.cancel_btn)

    def _cancel(self) -> None:
        self._cancel_requested = True
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.setText("Cancelling...")

    def is_cancel_requested(self) -> bool:
        return self._cancel_requested

    def update_progress(self, progress: TransferProgress) -> None:
        now = time.perf_counter()
        is_done = progress.overall_bytes_total > 0 and progress.overall_bytes_transferred >= progress.overall_bytes_total
        if now - self._last_paint < 0.05 and not is_done:
            return
        self._last_paint = now

        overall_total = max(1, progress.overall_bytes_total)
        overall_done = min(progress.overall_bytes_transferred, overall_total)
        percent = int((overall_done / overall_total) * 100)

        elapsed = max(0.001, now - self._started_at)
        speed = overall_done / elapsed
        remaining = max(0, overall_total - overall_done)
        eta = (remaining / speed) if speed > 0 else 0.0

        self.summary_label.setText(
            f"Item {progress.item_index}/{self._item_count}: {Path(progress.source_path).name} -> {Path(progress.destination_path).name}"
        )
        self.progress_bar.setValue(percent)
        self.percent_label.setText(
            f"{percent}% ({self._format_bytes(overall_done)} / {self._format_bytes(overall_total)})"
        )
        self.speed_label.setText(f"Speed: {self._format_bytes(speed)}/s")
        self.time_label.setText(f"Time: {self._format_duration(elapsed)} elapsed, ETA {self._format_duration(eta)}")
        QApplication.processEvents()

    @staticmethod
    def _format_bytes(value: float) -> str:
        units = ["B", "KB", "MB", "GB", "TB"]
        size = float(max(0.0, value))
        unit_idx = 0
        while size >= 1024 and unit_idx < len(units) - 1:
            size /= 1024
            unit_idx += 1
        if unit_idx == 0:
            return f"{int(size)} {units[unit_idx]}"
        return f"{size:.1f} {units[unit_idx]}"

    @staticmethod
    def _format_duration(seconds: float) -> str:
        total_seconds = int(max(0, round(seconds)))
        minutes, secs = divmod(total_seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours}h {minutes}m {secs}s"
        if minutes:
            return f"{minutes}m {secs}s"
        return f"{secs}s"


class SessionExportPickerDialog(QDialog):
    SESSION_ID_ROLE = Qt.UserRole

    def __init__(self, sessions: list[Session], preselected: set[str] | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Select Sessions to Export")
        self.resize(720, 560)
        self._sessions = sorted(sessions, key=lambda session: (session.folder.lower(), session.name.lower()))
        self._preselected = preselected if preselected is not None else {session.id for session in sessions}

        layout = QVBoxLayout(self)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Session"])
        self.tree.setAlternatingRowColors(True)
        layout.addWidget(self.tree, 1)

        buttons_row = QHBoxLayout()
        select_all_btn = QPushButton("Select All")
        clear_btn = QPushButton("Clear")
        buttons_row.addWidget(select_all_btn)
        buttons_row.addWidget(clear_btn)
        buttons_row.addStretch(1)
        layout.addLayout(buttons_row)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        select_all_btn.clicked.connect(lambda: self._set_all_session_checks(Qt.Checked))
        clear_btn.clicked.connect(lambda: self._set_all_session_checks(Qt.Unchecked))
        self._populate_tree()

    def _populate_tree(self) -> None:
        self.tree.clear()
        folder_items: dict[str, QTreeWidgetItem] = {}

        for session in self._sessions:
            folder = session.folder or "Default"
            parent = self._ensure_folder_item(folder_items, folder)
            endpoint = _session_endpoint_text(session)
            item = QTreeWidgetItem([f"{session.name} [{session.protocol.value.upper()}] - {endpoint}"])
            item.setData(0, self.SESSION_ID_ROLE, session.id)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(0, Qt.Checked if session.id in self._preselected else Qt.Unchecked)
            parent.addChild(item)

        self.tree.expandAll()

    def _ensure_folder_item(self, folder_items: dict[str, QTreeWidgetItem], folder_path: str) -> QTreeWidgetItem:
        normalized = folder_path.strip().replace("\\", "/").strip("/") or "Default"
        existing = folder_items.get(normalized)
        if existing is not None:
            return existing

        parent_item: QTreeWidgetItem | None = None
        running_parts: list[str] = []
        for segment in normalized.split("/"):
            running_parts.append(segment)
            current_path = "/".join(running_parts)
            current_item = folder_items.get(current_path)
            if current_item is None:
                current_item = QTreeWidgetItem([segment])
                current_item.setFlags(Qt.ItemIsEnabled)
                if parent_item is None:
                    self.tree.addTopLevelItem(current_item)
                else:
                    parent_item.addChild(current_item)
                folder_items[current_path] = current_item
            parent_item = current_item
        return folder_items[normalized]

    def _set_all_session_checks(self, state: Qt.CheckState) -> None:
        for top_index in range(self.tree.topLevelItemCount()):
            self._set_item_checks_recursive(self.tree.topLevelItem(top_index), state)

    def _set_item_checks_recursive(self, item: QTreeWidgetItem, state: Qt.CheckState) -> None:
        if item.data(0, self.SESSION_ID_ROLE):
            item.setCheckState(0, state)
        for index in range(item.childCount()):
            self._set_item_checks_recursive(item.child(index), state)

    def _validate_and_accept(self) -> None:
        if not self.selected_session_ids():
            QMessageBox.warning(self, "Nothing Selected", "Select at least one session to export.")
            return
        self.accept()

    def selected_session_ids(self) -> list[str]:
        selected: list[str] = []
        for top_index in range(self.tree.topLevelItemCount()):
            self._collect_selected_recursive(self.tree.topLevelItem(top_index), selected)
        return selected

    def _collect_selected_recursive(self, item: QTreeWidgetItem, selected: list[str]) -> None:
        session_id = item.data(0, self.SESSION_ID_ROLE)
        if isinstance(session_id, str) and session_id and item.checkState(0) == Qt.Checked:
            selected.append(session_id)
        for index in range(item.childCount()):
            self._collect_selected_recursive(item.child(index), selected)


class ExportSelectionDialog(QDialog):
    def __init__(self, sessions: list[Session], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Export Configuration")
        self.setMinimumWidth(460)
        self._sessions = sessions
        self._selected_session_ids: list[str] | None = None

        layout = QVBoxLayout(self)
        self.include_settings = QCheckBox("Include application settings (appearance, profiles, fast commands, etc.)")
        self.include_sessions = QCheckBox("Include saved sessions/folders")
        self.include_settings.setChecked(True)
        self.include_sessions.setChecked(True)
        self.selective_sessions = QCheckBox("Select specific sessions")
        self.selective_sessions.setChecked(False)
        self.selective_sessions.toggled.connect(self._update_selective_controls)
        self.include_sessions.toggled.connect(self._update_selective_controls)
        self.select_sessions_btn = QPushButton("Choose Sessions...")
        self.select_sessions_btn.clicked.connect(self._pick_sessions)
        self.session_summary = QLabel("All sessions will be exported.")

        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.Password)
        self.password_input.setPlaceholderText("Optional password to encrypt export")

        self.password_confirm = QLineEdit()
        self.password_confirm.setEchoMode(QLineEdit.Password)
        self.password_confirm.setPlaceholderText("Confirm password (if set)")

        layout.addWidget(self.include_settings)
        layout.addWidget(self.include_sessions)
        layout.addWidget(self.selective_sessions)
        layout.addWidget(self.select_sessions_btn)
        layout.addWidget(self.session_summary)
        layout.addWidget(QLabel("Password-Protect Export (optional):"))
        layout.addWidget(self.password_input)
        layout.addWidget(self.password_confirm)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._update_selective_controls()

    def _validate_and_accept(self) -> None:
        if not self.include_settings.isChecked() and not self.include_sessions.isChecked():
            QMessageBox.warning(self, "Nothing Selected", "Select settings and/or sessions to export.")
            return
        if self.include_sessions.isChecked() and self.selective_sessions.isChecked():
            if self._selected_session_ids is None:
                QMessageBox.warning(
                    self,
                    "Select Sessions",
                    "Choose which sessions to export, or disable selective export.",
                )
                return
            if not self._selected_session_ids:
                QMessageBox.warning(self, "Nothing Selected", "Select at least one session to export.")
                return
        password = self.password_input.text()
        if password or self.password_confirm.text():
            if password != self.password_confirm.text():
                QMessageBox.warning(self, "Password Mismatch", "Password and confirmation do not match.")
                return
        self.accept()

    def _pick_sessions(self) -> None:
        preselected = set(self._selected_session_ids) if self._selected_session_ids is not None else None
        picker = SessionExportPickerDialog(self._sessions, preselected=preselected, parent=self)
        if not picker.exec():
            return
        self._selected_session_ids = picker.selected_session_ids()
        count = len(self._selected_session_ids)
        self.session_summary.setText(f"{count} session(s) selected.")

    def _update_selective_controls(self) -> None:
        enabled = self.include_sessions.isChecked()
        self.selective_sessions.setEnabled(enabled)
        selective = enabled and self.selective_sessions.isChecked()
        self.select_sessions_btn.setEnabled(selective)
        self.session_summary.setVisible(selective)
        if not enabled:
            self.session_summary.setText("All sessions will be exported.")
        elif not selective:
            self.session_summary.setText("All sessions will be exported.")
        elif self._selected_session_ids is None:
            self.session_summary.setText("No sessions selected yet.")
        else:
            self.session_summary.setText(f"{len(self._selected_session_ids)} session(s) selected.")

    def export_options(self) -> tuple[bool, bool, str, list[str] | None]:
        selected_ids: list[str] | None = None
        if self.include_sessions.isChecked() and self.selective_sessions.isChecked():
            selected_ids = list(self._selected_session_ids or [])
        return (
            self.include_settings.isChecked(),
            self.include_sessions.isChecked(),
            self.password_input.text(),
            selected_ids,
        )


class _RemoteFileTreeItem(QTreeWidgetItem):
    SORT_ROLE = Qt.UserRole + 2

    def __lt__(self, other) -> bool:
        tree = self.treeWidget()
        if tree is None:
            return super().__lt__(other)
        column = tree.sortColumn()
        left = self.data(column, self.SORT_ROLE)
        right = other.data(column, self.SORT_ROLE)
        if left is None or right is None:
            return super().__lt__(other)
        try:
            return left < right
        except Exception:
            return super().__lt__(other)


@dataclass(slots=True)
class _LocalSFTPEntry:
    name: str
    path: str
    is_dir: bool
    is_symlink: bool
    size: int
    modified_time: int | None = None


class SFTPDirectoryLoadWorker(QObject):
    batch_loaded = Signal(int, str, object)
    finished = Signal(int, bool, str, str, bool, bool, bool)
    BATCH_SIZE = 250
    CLEANUP_TIMEOUT_SECONDS = 1.5

    def __init__(
        self,
        *,
        request_id: int,
        session: Session,
        sftp: SFTPClient,
        path: str,
        password: str | None,
        trust_unknown: bool,
    ) -> None:
        super().__init__()
        self._request_id = request_id
        self._session = session
        self._sftp = sftp
        self._path = path
        self._password = password
        self._trust_unknown = trust_unknown
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task[tuple[str, list[SFTPEntry]]] | None = None
        self._cancel_requested = False

    @Slot()
    def run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            self._task = loop.create_task(
                self._sftp.scan_directory(
                    self._session,
                    self._path,
                    password=self._password,
                    trust_unknown=self._trust_unknown,
                    batch_size=self.BATCH_SIZE,
                    batch_callback=self._emit_batch,
                    cancel_requested=self._is_cancel_requested,
                )
            )
            resolved_path, _entries = loop.run_until_complete(self._task)
            self.finished.emit(self._request_id, True, resolved_path, "", False, False, False)
        except TransferCancelledError:
            self.finished.emit(self._request_id, False, self._path, "", False, False, True)
        except asyncio.CancelledError:
            self.finished.emit(self._request_id, False, self._path, "", False, False, True)
        except Exception as exc:  # noqa: BLE001
            message = str(exc) or repr(exc)
            lowered = message.lower()
            is_host_key_error = "host key" in lowered and ("not trusted" in lowered or "not verifiable" in lowered)
            is_auth_error = isinstance(exc, asyncssh.PermissionDenied) or (
                "permission denied" in lowered or "authentication failed" in lowered
            )
            self.finished.emit(
                self._request_id,
                False,
                self._path,
                message,
                is_host_key_error,
                is_auth_error,
                False,
            )
        finally:
            self._task = None
            try:
                pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(
                        asyncio.wait_for(
                            asyncio.gather(*pending, return_exceptions=True),
                            timeout=self.CLEANUP_TIMEOUT_SECONDS,
                        )
                    )
            except Exception:
                pass
            try:
                loop.close()
            except Exception:
                pass
            self._loop = None

    def _emit_batch(self, resolved_path: str, batch: list[SFTPEntry]) -> None:
        if self._cancel_requested or not batch:
            return
        self.batch_loaded.emit(self._request_id, resolved_path, list(batch))

    def _is_cancel_requested(self) -> bool:
        return self._cancel_requested

    @Slot()
    def cancel(self) -> None:
        self._cancel_requested = True
        loop = self._loop
        if loop is None or loop.is_closed():
            return

        def _cancel_pending() -> None:
            task = self._task
            if task is not None and not task.done():
                task.cancel()
            for pending in asyncio.all_tasks(loop):
                if not pending.done():
                    pending.cancel()

        try:
            loop.call_soon_threadsafe(_cancel_pending)
        except RuntimeError:
            return

    @Slot(int, bool, str, str, bool, bool, bool)
    def _schedule_delete(
        self,
        _request_id: int,
        _ok: bool,
        _resolved_path: str,
        _error_message: str,
        _host_key_error: bool,
        _auth_error: bool,
        _cancelled: bool,
    ) -> None:
        self.deleteLater()


class SFTPSessionTab(QWidget):
    LOCAL_PATH_ROLE = Qt.UserRole + 10
    LOCAL_IS_DIR_ROLE = Qt.UserRole + 11
    LOCAL_ENTRY_ROLE = Qt.UserRole + 12
    LOCAL_IS_SYMLINK_ROLE = Qt.UserRole + 13
    LOCAL_SORT_ROLE = _RemoteFileTreeItem.SORT_ROLE
    REMOTE_PATH_ROLE = Qt.UserRole
    REMOTE_IS_DIR_ROLE = Qt.UserRole + 1
    REMOTE_ENTRY_ROLE = Qt.UserRole + 3
    REMOTE_IS_SYMLINK_ROLE = Qt.UserRole + 4
    REMOTE_SORT_ROLE = _RemoteFileTreeItem.SORT_ROLE

    def __init__(
        self,
        session: Session,
        sftp: SFTPClient,
        initial_remote_dir: str,
        initial_remote_entries: list[SFTPEntry] | None,
        initial_local_dir: str,
        password: str | None,
        execute_remote: Callable[[Session, str | None, Callable[[str | None, bool], _T]], tuple[_T | None, str | None]],
        status_callback: Callable[[str, int], None],
        should_confirm_delete: Callable[[], bool],
        should_confirm_overwrite: Callable[[], bool] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._session = session
        self._sftp = sftp
        self._password = password
        self._execute_remote = execute_remote
        self._status_callback = status_callback
        self._should_confirm_delete = should_confirm_delete
        self._should_confirm_overwrite = should_confirm_overwrite or (lambda: True)
        self._local_dir = self._resolve_initial_local_dir(initial_local_dir)
        self._remote_dir = initial_remote_dir
        self._remote_entries: list[SFTPEntry] = []
        self._remote_load_request_id = 0
        self._remote_load_thread: QThread | None = None
        self._remote_load_worker: SFTPDirectoryLoadWorker | None = None
        self._remote_load_path = initial_remote_dir
        self._remote_load_trust_unknown = False
        self._remote_load_save_password_on_success = False
        self._remote_placeholder_active = False
        self._remote_load_closing = False
        self.setAcceptDrops(True)

        root = QVBoxLayout(self)
        split = QSplitter(Qt.Horizontal)
        root.addWidget(split, 1)

        split.addWidget(self._build_local_panel())
        split.addWidget(self._build_remote_panel())
        split.setSizes([620, 620])
        self._refresh_shortcut = QShortcut(QKeySequence("F5"), self)
        self._refresh_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self._refresh_shortcut.activated.connect(self._refresh_directories)

        self._set_local_directory(self._local_dir, sync_tree=True, show_errors=False)
        self.remote_path_input.setText(initial_remote_dir)
        if initial_remote_entries is None:
            self._show_remote_loading_state(f"Loading {initial_remote_dir}...")
        else:
            self._apply_remote_directory_entries(initial_remote_dir, initial_remote_entries, loading=False)

    @staticmethod
    def _resolve_initial_local_dir(path: str) -> str:
        start_path = path.strip() or DEFAULT_SFTP_LOCAL_FOLDER
        expanded = os.path.abspath(os.path.expanduser(start_path))
        if os.path.isdir(expanded):
            return expanded
        fallback = os.path.abspath(os.path.expanduser(DEFAULT_SFTP_LOCAL_FOLDER))
        if os.path.isdir(fallback):
            return fallback
        return str(Path.home())

    def _build_local_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(QLabel("Local"))

        split = QSplitter(Qt.Vertical)
        layout.addWidget(split, 1)

        upper = QWidget()
        upper_layout = QVBoxLayout(upper)
        upper_layout.setContentsMargins(0, 0, 0, 0)
        upper_layout.setSpacing(6)

        local_nav = QHBoxLayout()
        self.local_path_input = QLineEdit()
        self.local_path_input.returnPressed.connect(self._on_local_path_entered)
        self.local_up_btn = QPushButton("Up")
        self.local_up_btn.clicked.connect(self._navigate_local_up)
        local_nav.addWidget(self.local_path_input, 1)
        local_nav.addWidget(self.local_up_btn)
        upper_layout.addLayout(local_nav)

        self.local_nav_tree = QTreeWidget()
        self.local_nav_tree.setHeaderHidden(True)
        self.local_nav_tree.setAlternatingRowColors(True)
        self.local_nav_tree.itemDoubleClicked.connect(self._on_local_nav_activated)
        self.local_nav_tree.itemActivated.connect(self._on_local_nav_activated)

        upper_layout.addWidget(self.local_nav_tree, 1)
        split.addWidget(upper)

        lower = QWidget()
        lower_layout = QVBoxLayout(lower)
        lower_layout.setContentsMargins(0, 0, 0, 0)
        lower_layout.setSpacing(6)
        lower_layout.addWidget(QLabel("Selected Directory Contents"))

        self.local_file_tree = QTreeWidget()
        self.local_file_tree.setColumnCount(4)
        self.local_file_tree.setHeaderLabels(["Name", "Type", "Size", "Modified"])
        self.local_file_tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.local_file_tree.setAlternatingRowColors(True)
        self.local_file_tree.setSortingEnabled(True)
        self.local_file_tree.sortByColumn(0, Qt.AscendingOrder)
        self.local_file_tree.itemDoubleClicked.connect(self._on_local_file_activated)
        self.local_file_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.local_file_tree.customContextMenuRequested.connect(self._show_local_menu)
        lower_layout.addWidget(self.local_file_tree, 1)
        split.addWidget(lower)
        split.setSizes([260, 360])
        return panel

    def _build_remote_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(QLabel("Remote (SFTP)"))

        split = QSplitter(Qt.Vertical)
        layout.addWidget(split, 1)

        upper = QWidget()
        upper_layout = QVBoxLayout(upper)
        upper_layout.setContentsMargins(0, 0, 0, 0)
        upper_layout.setSpacing(6)

        remote_nav = QHBoxLayout()
        self.remote_path_input = QLineEdit()
        self.remote_path_input.returnPressed.connect(self._on_remote_path_entered)
        self.remote_up_btn = QPushButton("Up")
        self.remote_up_btn.clicked.connect(self._navigate_remote_up)
        remote_nav.addWidget(self.remote_path_input, 1)
        remote_nav.addWidget(self.remote_up_btn)
        upper_layout.addLayout(remote_nav)

        self.remote_nav_tree = QTreeWidget()
        self.remote_nav_tree.setHeaderHidden(True)
        self.remote_nav_tree.setAlternatingRowColors(True)
        self.remote_nav_tree.itemDoubleClicked.connect(self._on_remote_nav_activated)
        upper_layout.addWidget(self.remote_nav_tree, 1)
        split.addWidget(upper)

        lower = QWidget()
        lower_layout = QVBoxLayout(lower)
        lower_layout.setContentsMargins(0, 0, 0, 0)
        lower_layout.setSpacing(6)
        lower_layout.addWidget(QLabel("Selected Directory Contents"))

        self.remote_file_tree = QTreeWidget()
        self.remote_file_tree.setColumnCount(4)
        self.remote_file_tree.setHeaderLabels(["Name", "Type", "Size", "Modified"])
        self.remote_file_tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.remote_file_tree.setAlternatingRowColors(True)
        self.remote_file_tree.setSortingEnabled(True)
        self.remote_file_tree.sortByColumn(0, Qt.AscendingOrder)
        self.remote_file_tree.itemDoubleClicked.connect(self._on_remote_file_activated)
        self.remote_file_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.remote_file_tree.customContextMenuRequested.connect(self._show_remote_menu)
        lower_layout.addWidget(self.remote_file_tree, 1)
        split.addWidget(lower)
        split.setSizes([260, 360])
        return panel

    def _run_remote(self, operation: Callable[[str | None, bool], _T]) -> _T | None:
        result, updated_password = self._execute_remote(self._session, self._password, operation)
        self._password = updated_password
        return result

    @staticmethod
    def _local_parent(path: str) -> str:
        resolved = os.path.abspath(os.path.expanduser(path))
        parent = os.path.abspath(os.path.join(resolved, os.pardir))
        if os.path.normcase(parent) == os.path.normcase(resolved):
            return resolved
        return parent

    def _local_entries(self, path: str, *, show_errors: bool) -> list[_LocalSFTPEntry]:
        entries: list[_LocalSFTPEntry] = []
        try:
            with os.scandir(path) as iterator:
                for entry in iterator:
                    name = entry.name
                    full_path = entry.path
                    try:
                        is_symlink = entry.is_symlink()
                    except OSError:
                        is_symlink = False
                    try:
                        is_dir = entry.is_dir(follow_symlinks=True)
                    except OSError:
                        continue
                    size = 0
                    modified_time: int | None = None
                    try:
                        stat_info = entry.stat(follow_symlinks=False)
                        modified_time = int(stat_info.st_mtime)
                        if not is_dir:
                            size = int(stat_info.st_size)
                    except (OSError, OverflowError, ValueError):
                        modified_time = None
                    entries.append(
                        _LocalSFTPEntry(
                            name=name,
                            path=full_path,
                            is_dir=is_dir,
                            is_symlink=is_symlink,
                            size=size,
                            modified_time=modified_time,
                        )
                    )
        except OSError as exc:
            if show_errors:
                QMessageBox.warning(self, "Directory Not Found", f"Unable to read local directory:\n{path}\n\n{exc}")
            return []
        entries.sort(key=lambda value: (not value.is_dir, value.name.lower()))
        return entries

    def _populate_local_navigation(self, entries: list[_LocalSFTPEntry]) -> None:
        self.local_nav_tree.clear()
        parent = self._local_parent(self._local_dir)
        if os.path.normcase(parent) != os.path.normcase(self._local_dir):
            parent_item = QTreeWidgetItem([".."])
            parent_item.setData(0, self.LOCAL_PATH_ROLE, parent)
            self.local_nav_tree.addTopLevelItem(parent_item)

        directory_count = 0
        for entry in entries:
            if not entry.is_dir:
                continue
            directory_count += 1
            item = QTreeWidgetItem([self._entry_label(entry.name, is_dir=True)])
            item.setData(0, self.LOCAL_PATH_ROLE, entry.path)
            self.local_nav_tree.addTopLevelItem(item)

        if directory_count == 0 and os.path.normcase(parent) == os.path.normcase(self._local_dir):
            placeholder = QTreeWidgetItem(["(no subdirectories)"])
            placeholder.setFlags(Qt.NoItemFlags)
            self.local_nav_tree.addTopLevelItem(placeholder)

    def _populate_local_listing(self, entries: list[_LocalSFTPEntry]) -> None:
        self.local_file_tree.clear()
        if not entries:
            placeholder = QTreeWidgetItem(["(empty directory)", "", "", ""])
            placeholder.setFlags(Qt.NoItemFlags)
            self.local_file_tree.addTopLevelItem(placeholder)
            return

        for entry in entries:
            modified_label, modified_sort = self._format_modified_time(entry.modified_time)
            display_name = self._entry_label(entry.name, is_dir=entry.is_dir)
            item = _RemoteFileTreeItem(
                [
                    display_name,
                    "Directory" if entry.is_dir else "File",
                    "" if entry.is_dir else str(entry.size),
                    modified_label,
                ]
            )
            item.setData(0, self.LOCAL_PATH_ROLE, entry.path)
            item.setData(0, self.LOCAL_IS_DIR_ROLE, entry.is_dir)
            item.setData(0, self.LOCAL_IS_SYMLINK_ROLE, entry.is_symlink)
            item.setData(0, self.LOCAL_ENTRY_ROLE, entry)
            type_rank = 0 if entry.is_dir else 1
            normalized_name = entry.name.lower()
            item.setData(0, self.LOCAL_SORT_ROLE, (type_rank, normalized_name))
            item.setData(1, self.LOCAL_SORT_ROLE, (type_rank, "directory" if entry.is_dir else "file", normalized_name))
            item.setData(2, self.LOCAL_SORT_ROLE, (type_rank, -1 if entry.is_dir else entry.size, normalized_name))
            item.setData(3, self.LOCAL_SORT_ROLE, (type_rank, modified_sort, normalized_name))
            self.local_file_tree.addTopLevelItem(item)

        self.local_file_tree.resizeColumnToContents(0)
        self.local_file_tree.resizeColumnToContents(1)
        self.local_file_tree.resizeColumnToContents(3)

    def _set_local_directory(self, path: str, *, sync_tree: bool, show_errors: bool = True) -> None:
        _ = sync_tree
        resolved = os.path.abspath(os.path.expanduser(path))
        if not os.path.isdir(resolved):
            if show_errors:
                QMessageBox.warning(self, "Directory Not Found", f"Local directory does not exist:\n{resolved}")
            return

        self._local_dir = resolved
        self.local_path_input.setText(resolved)
        entries = self._local_entries(resolved, show_errors=show_errors)
        self._populate_local_navigation(entries)
        self._populate_local_listing(entries)

    def _on_local_path_entered(self) -> None:
        entered = self.local_path_input.text().strip()
        if entered:
            self._set_local_directory(entered, sync_tree=True)

    def _navigate_local_up(self) -> None:
        parent = self._local_parent(self._local_dir)
        if os.path.normcase(parent) == os.path.normcase(self._local_dir):
            return
        self._set_local_directory(parent, sync_tree=True)

    def _on_local_nav_activated(self, item: QTreeWidgetItem, _column: int) -> None:
        path = item.data(0, self.LOCAL_PATH_ROLE)
        if isinstance(path, str) and path:
            self._set_local_directory(path, sync_tree=True)

    def _on_local_file_activated(self, item: QTreeWidgetItem, _column: int) -> None:
        path = item.data(0, self.LOCAL_PATH_ROLE)
        if not isinstance(path, str) or not path:
            return
        if bool(item.data(0, self.LOCAL_IS_DIR_ROLE)):
            self._set_local_directory(path, sync_tree=True)
            return
        if path and os.path.isfile(path):
            self._upload_local_paths([path])

    def _selected_local_entries(self) -> list[_LocalSFTPEntry]:
        unique: list[_LocalSFTPEntry] = []
        seen: set[str] = set()
        for item in self.local_file_tree.selectedItems():
            entry = item.data(0, self.LOCAL_ENTRY_ROLE)
            if not isinstance(entry, _LocalSFTPEntry) or not entry.path or entry.path in seen:
                continue
            seen.add(entry.path)
            unique.append(entry)
        return unique

    def _selected_local_paths(self) -> list[str]:
        return [entry.path for entry in self._selected_local_entries()]

    def _show_local_menu(self, position) -> None:
        menu = QMenu(self)
        upload_action = menu.addAction("Upload Selected")
        new_folder_action = menu.addAction("New Folder...")
        rename_action = menu.addAction("Rename...")
        delete_action = menu.addAction("Delete Selected")
        refresh_action = menu.addAction("Refresh")
        selected_entries = self._selected_local_entries()
        has_selection = bool(selected_entries)
        upload_action.setEnabled(has_selection)
        rename_action.setEnabled(len(selected_entries) == 1)
        delete_action.setEnabled(has_selection)
        chosen = menu.exec(self.local_file_tree.viewport().mapToGlobal(position))
        if chosen == upload_action:
            self._upload_selected_local()
        elif chosen == new_folder_action:
            self._create_local_directory()
        elif chosen == rename_action:
            self._rename_selected_local()
        elif chosen == delete_action:
            self._delete_selected_local()
        elif chosen == refresh_action:
            self._refresh_local_directory()
        menu.deleteLater()

    def _upload_selected_local(self) -> None:
        paths = self._selected_local_paths()
        if not paths:
            QMessageBox.information(self, "Nothing Selected", "Select local files/directories to upload.")
            return
        self._upload_local_paths(paths)

    def _upload_local_paths(self, local_paths: list[str]) -> None:
        if not local_paths:
            return
        if self._should_confirm_overwrite():
            conflicts = self._run_remote(
                lambda password, trust_unknown: asyncio.run(
                    self._sftp.find_upload_overwrite_conflicts(
                        self._session,
                        local_paths,
                        self._remote_dir,
                        password=password,
                        trust_unknown=trust_unknown,
                    )
                )
            )
            if conflicts is None:
                return
            if not self._confirm_overwrite_conflicts(conflicts, destination_label="remote"):
                self._status_callback("Upload cancelled: overwrite not confirmed.", 5000)
                return
        progress_dialog = UploadProgressDialog(item_count=len(local_paths), parent=self)
        progress_dialog.show()
        QApplication.processEvents()
        try:
            transferred = self._run_remote(
                lambda password, trust_unknown: asyncio.run(
                    self._sftp.upload_paths(
                        self._session,
                        local_paths,
                        self._remote_dir,
                        password=password,
                        trust_unknown=trust_unknown,
                        progress_callback=progress_dialog.update_progress,
                        cancel_requested=progress_dialog.is_cancel_requested,
                    )
                )
            )
        finally:
            progress_dialog.close()
        if transferred is None:
            return
        self._status_callback(f"Uploaded {transferred} item(s) to {self._remote_dir}", 6000)
        self._refresh_remote_directory()

    def _delete_selected_local(self) -> None:
        paths = self._selected_local_paths()
        if not paths:
            QMessageBox.information(self, "Nothing Selected", "Select local files/directories to delete.")
            return
        self._delete_local_paths(paths)

    def _delete_local_paths(self, local_paths: list[str]) -> None:
        if not local_paths:
            return
        if not self._confirm_delete(local_paths, "local"):
            return
        deleted = 0
        for path in local_paths:
            try:
                if os.path.islink(path):
                    os.remove(path)
                elif os.path.isdir(path):
                    shutil.rmtree(path)
                else:
                    os.remove(path)
                deleted += 1
            except Exception as exc:
                QMessageBox.critical(self, "Delete Failed", f"Failed to delete local path:\n{path}\n\n{exc}")
                break
        if deleted:
            self._status_callback(f"Deleted {deleted} local item(s).", 6000)
        self._set_local_directory(self._local_dir, sync_tree=False, show_errors=False)

    def _rename_selected_local(self) -> None:
        entries = self._selected_local_entries()
        if len(entries) != 1:
            QMessageBox.information(self, "Rename Local Item", "Select exactly one local item to rename.")
            return
        self._rename_local_path(entries[0].path)

    def _create_local_directory(self) -> None:
        name = self._prompt_directory_name("New Local Folder")
        if not name:
            return
        target_path = os.path.join(self._local_dir, name)
        try:
            os.mkdir(target_path)
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Create Folder Failed",
                f"Failed to create local directory:\n{target_path}\n\n{exc}",
            )
            return
        self._status_callback(f"Created local folder {name}.", 6000)
        self._refresh_local_directory()

    def _rename_local_path(self, path: str) -> None:
        current_name = Path(path).name
        new_name = self._prompt_item_rename("Rename Local Item", current_name)
        if not new_name or new_name == current_name:
            return
        target_path = os.path.join(os.path.dirname(path), new_name)
        if os.path.normcase(os.path.abspath(target_path)) == os.path.normcase(os.path.abspath(path)):
            return

        should_replace = False
        if os.path.lexists(target_path):
            should_replace = self._confirm_rename_replace(path, target_path, side="local")
            if not should_replace:
                return
            if os.path.isdir(target_path) and not os.path.islink(target_path):
                try:
                    with os.scandir(target_path) as iterator:
                        if any(True for _ in iterator):
                            QMessageBox.warning(
                                self,
                                "Rename Local Item",
                                f"Cannot replace non-empty local directory:\n{target_path}",
                            )
                            return
                except OSError as exc:
                    QMessageBox.critical(
                        self,
                        "Rename Failed",
                        f"Unable to inspect local target:\n{target_path}\n\n{exc}",
                    )
                    return

        try:
            if should_replace:
                os.replace(path, target_path)
            else:
                os.rename(path, target_path)
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Rename Failed",
                f"Failed to rename local path:\n{path}\n\nto:\n{target_path}\n\n{exc}",
            )
            return

        self._status_callback(f"Renamed local item to {new_name}.", 6000)
        self._set_local_directory(self._local_dir, sync_tree=False, show_errors=False)

    def load_remote_directory(self, path: str) -> None:
        self._set_remote_directory(path)

    def _set_remote_directory(self, path: str, *, preloaded: list[SFTPEntry] | None = None) -> None:
        if preloaded is not None:
            self._cancel_remote_directory_load(wait_ms=0)
            self._apply_remote_directory_entries(path, preloaded, loading=False)
            return
        self._begin_remote_directory_load(path)

    def _refresh_local_directory(self) -> None:
        self._set_local_directory(self._local_dir, sync_tree=True, show_errors=False)

    def _refresh_remote_directory(self) -> None:
        self._set_remote_directory(self._remote_dir)

    def _refresh_directories(self) -> None:
        self._refresh_local_directory()
        self._refresh_remote_directory()

    def _begin_remote_directory_load(
        self,
        path: str,
        *,
        password: str | None = None,
        trust_unknown: bool = False,
        save_password_on_success: bool = False,
    ) -> None:
        self._cancel_remote_directory_load(wait_ms=0)
        self._remote_entries = []
        self._remote_load_request_id += 1
        self._remote_load_path = path
        self._remote_load_trust_unknown = trust_unknown
        self._remote_load_save_password_on_success = save_password_on_success
        if password is not None:
            self._password = password
        self._remote_dir = path
        self.remote_path_input.setText(path)
        self._show_remote_loading_state(f"Loading {path}...")
        self._status_callback(f"Loading remote directory {path}...", 0)

        thread = QThread(self)
        worker = SFTPDirectoryLoadWorker(
            request_id=self._remote_load_request_id,
            session=self._session,
            sftp=self._sftp,
            path=path,
            password=self._password,
            trust_unknown=trust_unknown,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.batch_loaded.connect(self._handle_remote_load_batch, Qt.QueuedConnection)
        worker.finished.connect(self._handle_remote_load_finished, Qt.QueuedConnection)
        worker.finished.connect(worker._schedule_delete, Qt.DirectConnection)
        worker.finished.connect(thread.quit, Qt.DirectConnection)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_remote_load_thread_finished, Qt.QueuedConnection)
        self._remote_load_thread = thread
        self._remote_load_worker = worker
        thread.start()

    def _cancel_remote_directory_load(self, *, wait_ms: int) -> None:
        worker = self._remote_load_worker
        thread = self._remote_load_thread
        self._remote_load_worker = None
        self._remote_load_thread = None
        if worker is not None:
            try:
                worker.cancel()
            except Exception:
                pass
        if thread is not None:
            try:
                thread.quit()
                if wait_ms > 0:
                    thread.wait(wait_ms)
            except RuntimeError:
                pass

    @Slot(int, str, object)
    def _handle_remote_load_batch(self, request_id: int, resolved_path: str, batch: object) -> None:
        if self._remote_load_closing or request_id != self._remote_load_request_id or not isinstance(batch, list):
            return
        entries = [entry for entry in batch if isinstance(entry, SFTPEntry)]
        if not entries:
            return
        self._remote_dir = resolved_path
        self.remote_path_input.setText(resolved_path)
        self._remote_entries.extend(entries)
        self._append_remote_listing_batch(entries)
        self._status_callback(f"Loading {resolved_path}... {len(self._remote_entries)} item(s)", 0)

    @Slot(int, bool, str, str, bool, bool, bool)
    def _handle_remote_load_finished(
        self,
        request_id: int,
        ok: bool,
        resolved_path: str,
        error_message: str,
        host_key_error: bool,
        auth_error: bool,
        cancelled: bool,
    ) -> None:
        if self._remote_load_closing:
            return
        if request_id != self._remote_load_request_id:
            return
        if cancelled:
            return
        if ok:
            self._remote_dir = resolved_path
            self.remote_path_input.setText(resolved_path)
            sorted_entries = sorted(self._remote_entries, key=lambda entry: (not entry.is_dir, entry.name.lower()))
            self._apply_remote_directory_entries(resolved_path, sorted_entries, loading=False)
            if self._remote_load_save_password_on_success:
                self._enable_password_save_for_session()
            if (self._session.save_password or self._remote_load_save_password_on_success) and self._password:
                self._persist_session_password(self._password)
            self._status_callback(f"Loaded {len(sorted_entries)} item(s) from {resolved_path}", 6000)
            return

        if host_key_error and not self._remote_load_trust_unknown:
            if self._prompt_trust_host_key():
                self._begin_remote_directory_load(
                    self._remote_load_path,
                    trust_unknown=True,
                    save_password_on_success=self._remote_load_save_password_on_success,
                )
            else:
                self._show_remote_loading_state("Directory load canceled.")
                self._status_callback("Remote directory load cancelled: host key was not trusted.", 5000)
            return

        if auth_error:
            password, remember_password = self._prompt_password()
            if password:
                self._begin_remote_directory_load(
                    self._remote_load_path,
                    password=password,
                    trust_unknown=self._remote_load_trust_unknown,
                    save_password_on_success=self._remote_load_save_password_on_success or remember_password,
                )
            else:
                self._show_remote_loading_state("Directory load canceled.")
                self._status_callback("Remote directory load cancelled: credentials were not provided.", 5000)
            return

        self._show_remote_loading_state("Directory load failed.")
        QMessageBox.critical(self, "SFTP Error", error_message or "Unable to load remote directory.")
        self._status_callback(f"Remote directory load failed: {self._remote_load_path}", 7000)

    @Slot()
    def _on_remote_load_thread_finished(self) -> None:
        thread = self.sender()
        if thread is self._remote_load_thread:
            self._remote_load_thread = None
            self._remote_load_worker = None

    def _show_remote_loading_state(self, message: str) -> None:
        self._remote_placeholder_active = True
        self.remote_nav_tree.clear()
        nav_placeholder = QTreeWidgetItem([message])
        nav_placeholder.setFlags(Qt.NoItemFlags)
        self.remote_nav_tree.addTopLevelItem(nav_placeholder)
        self.remote_file_tree.setSortingEnabled(False)
        self.remote_file_tree.clear()
        placeholder = QTreeWidgetItem([message, "", "", ""])
        placeholder.setFlags(Qt.NoItemFlags)
        self.remote_file_tree.addTopLevelItem(placeholder)

    def _apply_remote_directory_entries(self, resolved: str, entries: list[SFTPEntry], *, loading: bool) -> None:
        self._remote_dir = resolved
        self.remote_path_input.setText(resolved)
        self._remote_entries = list(entries)
        self._populate_remote_navigation(entries)
        self._populate_remote_listing(entries)
        self._remote_placeholder_active = loading

    def _append_remote_listing_batch(self, entries: list[SFTPEntry]) -> None:
        if self._remote_placeholder_active:
            self.remote_file_tree.clear()
            self._remote_placeholder_active = False
        for entry in entries:
            self.remote_file_tree.addTopLevelItem(self._build_remote_listing_item(entry))

    def _populate_remote_navigation(self, entries: list[SFTPEntry]) -> None:
        self.remote_nav_tree.clear()
        parent = self._remote_parent(self._remote_dir)
        if parent != self._remote_dir:
            parent_item = QTreeWidgetItem([".."])
            parent_item.setData(0, self.REMOTE_PATH_ROLE, parent)
            self.remote_nav_tree.addTopLevelItem(parent_item)

        dir_count = 0
        for entry in entries:
            if not entry.is_dir:
                continue
            dir_count += 1
            item = QTreeWidgetItem([self._entry_label(entry.name, is_dir=True)])
            item.setData(0, self.REMOTE_PATH_ROLE, entry.path)
            self.remote_nav_tree.addTopLevelItem(item)

        if dir_count == 0 and parent == self._remote_dir:
            placeholder = QTreeWidgetItem(["(no subdirectories)"])
            placeholder.setFlags(Qt.NoItemFlags)
            self.remote_nav_tree.addTopLevelItem(placeholder)

    def _populate_remote_listing(self, entries: list[SFTPEntry]) -> None:
        self.remote_file_tree.setSortingEnabled(False)
        self.remote_file_tree.clear()
        if not entries:
            placeholder = QTreeWidgetItem(["(empty directory)", "", "", ""])
            placeholder.setFlags(Qt.NoItemFlags)
            self.remote_file_tree.addTopLevelItem(placeholder)
            self.remote_file_tree.setSortingEnabled(True)
            return

        for entry in entries:
            self.remote_file_tree.addTopLevelItem(self._build_remote_listing_item(entry))

        self.remote_file_tree.setSortingEnabled(True)
        self.remote_file_tree.sortByColumn(self.remote_file_tree.sortColumn(), self.remote_file_tree.header().sortIndicatorOrder())
        self.remote_file_tree.resizeColumnToContents(0)
        self.remote_file_tree.resizeColumnToContents(1)
        self.remote_file_tree.resizeColumnToContents(3)

    def _build_remote_listing_item(self, entry: SFTPEntry) -> _RemoteFileTreeItem:
        modified_label, modified_sort = self._format_modified_time(entry.modified_time)
        display_name = self._entry_label(entry.name, is_dir=entry.is_dir)
        item = _RemoteFileTreeItem(
            [
                display_name,
                "Directory" if entry.is_dir else "File",
                "" if entry.is_dir else str(entry.size),
                modified_label,
            ]
        )
        item.setData(0, self.REMOTE_PATH_ROLE, entry.path)
        item.setData(0, self.REMOTE_IS_DIR_ROLE, entry.is_dir)
        item.setData(0, self.REMOTE_IS_SYMLINK_ROLE, entry.is_symlink)
        item.setData(0, self.REMOTE_ENTRY_ROLE, entry)
        type_rank = 0 if entry.is_dir else 1
        normalized_name = entry.name.lower()
        item.setData(0, self.REMOTE_SORT_ROLE, (type_rank, normalized_name))
        item.setData(1, self.REMOTE_SORT_ROLE, (type_rank, "directory" if entry.is_dir else "file", normalized_name))
        item.setData(2, self.REMOTE_SORT_ROLE, (type_rank, -1 if entry.is_dir else entry.size, normalized_name))
        item.setData(3, self.REMOTE_SORT_ROLE, (type_rank, modified_sort, normalized_name))
        return item

    @staticmethod
    def _entry_label(name: str, *, is_dir: bool) -> str:
        if is_dir and name not in {".", ".."} and not name.endswith("/"):
            return f"{name}/"
        return name

    @staticmethod
    def _format_modified_time(value: int | None) -> tuple[str, int]:
        if value is None:
            return "", -1
        try:
            ts = int(value)
        except (TypeError, ValueError, OverflowError):
            return "", -1
        if ts <= 0:
            return "", -1
        try:
            return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)), ts
        except (OverflowError, OSError, ValueError):
            return "", -1

    def _navigate_remote_up(self) -> None:
        parent = self._remote_parent(self._remote_dir)
        if parent == self._remote_dir:
            return
        self._set_remote_directory(parent)

    def _on_remote_path_entered(self) -> None:
        entered = self.remote_path_input.text().strip()
        if entered:
            self._set_remote_directory(entered)

    def _on_remote_nav_activated(self, item: QTreeWidgetItem, _column: int) -> None:
        path = item.data(0, self.REMOTE_PATH_ROLE)
        if isinstance(path, str) and path:
            self._set_remote_directory(path)

    def _on_remote_file_activated(self, item: QTreeWidgetItem, _column: int) -> None:
        path = item.data(0, self.REMOTE_PATH_ROLE)
        if not isinstance(path, str) or not path:
            return
        if bool(item.data(0, self.REMOTE_IS_DIR_ROLE)):
            self._set_remote_directory(path)
            return
        self._download_remote_paths([path])

    def _selected_remote_paths(self) -> list[str]:
        unique: list[str] = []
        seen: set[str] = set()
        for item in self.remote_file_tree.selectedItems():
            path = item.data(0, self.REMOTE_PATH_ROLE)
            if not isinstance(path, str) or not path or path in seen:
                continue
            seen.add(path)
            unique.append(path)
        return unique

    def _selected_remote_entries(self) -> list[SFTPEntry]:
        unique: list[SFTPEntry] = []
        seen: set[str] = set()
        for item in self.remote_file_tree.selectedItems():
            entry = item.data(0, self.REMOTE_ENTRY_ROLE)
            if not isinstance(entry, SFTPEntry) or not entry.path or entry.path in seen:
                continue
            seen.add(entry.path)
            unique.append(entry)
        return unique

    def _download_selected_remote(self) -> None:
        paths = self._selected_remote_paths()
        if not paths:
            QMessageBox.information(self, "Nothing Selected", "Select remote files/directories to download.")
            return
        self._download_remote_paths(paths)

    def _download_remote_paths(self, remote_paths: list[str]) -> None:
        if not remote_paths:
            return
        if self._should_confirm_overwrite():
            conflicts = self._run_remote(
                lambda password, trust_unknown: asyncio.run(
                    self._sftp.find_download_overwrite_conflicts(
                        self._session,
                        remote_paths,
                        self._local_dir,
                        password=password,
                        trust_unknown=trust_unknown,
                    )
                )
            )
            if conflicts is None:
                return
            if not self._confirm_overwrite_conflicts(conflicts, destination_label="local"):
                self._status_callback("Download cancelled: overwrite not confirmed.", 5000)
                return
        progress_dialog = UploadProgressDialog(
            item_count=len(remote_paths),
            operation_label="Downloading",
            parent=self,
        )
        progress_dialog.show()
        QApplication.processEvents()
        try:
            transferred = self._run_remote(
                lambda password, trust_unknown: asyncio.run(
                    self._sftp.download_paths(
                        self._session,
                        remote_paths,
                        self._local_dir,
                        password=password,
                        trust_unknown=trust_unknown,
                        progress_callback=progress_dialog.update_progress,
                        cancel_requested=progress_dialog.is_cancel_requested,
                    )
                )
            )
        finally:
            progress_dialog.close()
        if transferred is None:
            return
        self._status_callback(f"Downloaded {transferred} item(s) to {self._local_dir}", 6000)
        self._set_local_directory(self._local_dir, sync_tree=False, show_errors=False)

    def _delete_selected_remote(self) -> None:
        paths = self._selected_remote_paths()
        if not paths:
            QMessageBox.information(self, "Nothing Selected", "Select remote files/directories to delete.")
            return
        self._delete_remote_paths(paths)

    def _delete_remote_paths(self, remote_paths: list[str]) -> None:
        if not remote_paths:
            return
        if not self._confirm_delete(remote_paths, "remote"):
            return
        deleted = self._run_remote(
            lambda password, trust_unknown: asyncio.run(
                self._sftp.delete_paths(
                    self._session,
                    remote_paths,
                    password=password,
                    trust_unknown=trust_unknown,
                )
            )
        )
        if deleted is None:
            return
        self._status_callback(f"Deleted {deleted} remote item(s).", 6000)
        self._refresh_remote_directory()

    def _show_remote_menu(self, position) -> None:
        menu = QMenu(self)
        download_action = menu.addAction("Download Selected")
        upload_local_action = menu.addAction("Upload Selected Local")
        new_folder_action = menu.addAction("New Folder...")
        rename_action = menu.addAction("Rename...")
        delete_action = menu.addAction("Delete Selected")
        refresh_action = menu.addAction("Refresh")
        selected_remote_entries = self._selected_remote_entries()
        has_remote_selection = bool(selected_remote_entries)
        has_local_selection = bool(self._selected_local_paths())
        download_action.setEnabled(has_remote_selection)
        upload_local_action.setEnabled(has_local_selection)
        rename_action.setEnabled(len(selected_remote_entries) == 1)
        delete_action.setEnabled(has_remote_selection)
        chosen = menu.exec(self.remote_file_tree.viewport().mapToGlobal(position))
        if chosen == download_action:
            self._download_selected_remote()
        elif chosen == upload_local_action:
            self._upload_selected_local()
        elif chosen == new_folder_action:
            self._create_remote_directory()
        elif chosen == rename_action:
            self._rename_selected_remote()
        elif chosen == delete_action:
            self._delete_selected_remote()
        elif chosen == refresh_action:
            self._refresh_remote_directory()
        menu.deleteLater()

    def _rename_selected_remote(self) -> None:
        entries = self._selected_remote_entries()
        if len(entries) != 1:
            QMessageBox.information(self, "Rename Remote Item", "Select exactly one remote item to rename.")
            return
        self._rename_remote_path(entries[0].path)

    def _create_remote_directory(self) -> None:
        name = self._prompt_directory_name("New Remote Folder")
        if not name:
            return
        created_path = self._run_remote(
            lambda password, trust_unknown: asyncio.run(
                self._sftp.create_directory(
                    self._session,
                    self._remote_dir,
                    name,
                    password=password,
                    trust_unknown=trust_unknown,
                )
            )
        )
        if created_path is None:
            return
        self._status_callback(f"Created remote folder {name}.", 6000)
        self._refresh_remote_directory()

    def _rename_remote_path(self, remote_path: str) -> None:
        current_name = posixpath.basename(remote_path.rstrip("/")) or remote_path
        new_name = self._prompt_item_rename("Rename Remote Item", current_name)
        if not new_name or new_name == current_name:
            return
        parent = posixpath.dirname(remote_path.rstrip("/")) or "/"
        target_path = posixpath.join(parent, new_name)
        if target_path == remote_path:
            return

        target_exists = self._run_remote(
            lambda password, trust_unknown: asyncio.run(
                self._sftp.remote_path_exists(
                    self._session,
                    target_path,
                    password=password,
                    trust_unknown=trust_unknown,
                )
            )
        )
        if target_exists is None:
            return

        replace = False
        if target_exists:
            replace = self._confirm_rename_replace(remote_path, target_path, side="remote")
            if not replace:
                return

        renamed = self._run_remote(
            lambda password, trust_unknown: asyncio.run(
                self._sftp.rename_path(
                    self._session,
                    remote_path,
                    target_path,
                    replace=replace,
                    password=password,
                    trust_unknown=trust_unknown,
                )
            )
        )
        if renamed is None:
            return

        self._status_callback(f"Renamed remote item to {new_name}.", 6000)
        self._refresh_remote_directory()

    def _prompt_single_item_name(self, title: str, prompt: str, default_value: str) -> str | None:
        entered, ok = QInputDialog.getText(self, title, prompt, QLineEdit.Normal, default_value)
        if not ok:
            return None
        trimmed = entered.strip()
        if not trimmed:
            QMessageBox.warning(self, title, "Enter a name.")
            return None
        if trimmed in {".", ".."} or "/" in trimmed or "\\" in trimmed:
            QMessageBox.warning(self, title, "Enter a single file or directory name, not a path.")
            return None
        return trimmed

    def _prompt_item_rename(self, title: str, current_name: str) -> str | None:
        return self._prompt_single_item_name(title, "New name:", current_name)

    def _prompt_directory_name(self, title: str) -> str | None:
        return self._prompt_single_item_name(title, "Folder name:", "New Folder")

    def _confirm_rename_replace(self, source_path: str, target_path: str, *, side: str) -> bool:
        label = "remote" if side == "remote" else "local"
        with self._suspend_focus_tracking():
            answer = QMessageBox.question(
                self,
                "Replace Existing Item",
                (
                    f"A {label} item already exists at:\n{target_path}\n\n"
                    f"Replace it with:\n{source_path}\n\n"
                    "This may fail if the existing target cannot be safely replaced."
                ),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
        return answer == QMessageBox.Yes

    def _prompt_password(self) -> tuple[str | None, bool]:
        owner = self.window()
        prompt_password = getattr(owner, "_prompt_password", None)
        if callable(prompt_password):
            return prompt_password(self._session)
        return None, False

    def _prompt_trust_host_key(self) -> bool:
        owner = self.window()
        prompt_trust_host_key = getattr(owner, "_prompt_trust_host_key", None)
        if callable(prompt_trust_host_key):
            return bool(prompt_trust_host_key(self._session))
        return False

    def _persist_session_password(self, password: str) -> None:
        owner = self.window()
        persist_password = getattr(owner, "_persist_session_password", None)
        if callable(persist_password):
            persist_password(self._session, password, title="Password Save Failed", show_dialog=True)

    def _enable_password_save_for_session(self) -> None:
        owner = self.window()
        enable_password_save = getattr(owner, "_enable_password_save_for_session", None)
        if callable(enable_password_save):
            enable_password_save(self._session)

    def _confirm_delete(self, paths: list[str], side: str) -> bool:
        if not paths:
            return False
        if not self._should_confirm_delete():
            return True
        label = "remote" if side == "remote" else "local"
        plural = "item" if len(paths) == 1 else "items"
        preview = paths[0]
        if len(paths) > 1:
            preview = f"{preview}\n(and {len(paths) - 1} more)"
        with self._suspend_focus_tracking():
            answer = QMessageBox.question(
                self,
                "Confirm Delete",
                f"Delete {len(paths)} {label} {plural}?\n\n{preview}\n\nThis action cannot be undone.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
        return answer == QMessageBox.Yes

    @contextmanager
    def _suspend_focus_tracking(self):
        owner = self.window()
        suspend_focus_tracking = getattr(owner, "_suspend_focus_tracking", None)
        if callable(suspend_focus_tracking):
            with suspend_focus_tracking():
                yield
            return
        yield

    def _confirm_overwrite_conflicts(
        self,
        conflicts: list[OverwriteConflict],
        *,
        destination_label: str,
    ) -> bool:
        if not conflicts:
            return True

        total_conflicts = len(conflicts)
        allow_all_remaining = False
        for index, conflict in enumerate(conflicts):
            if allow_all_remaining:
                break
            remaining_count = total_conflicts - index - 1
            approved, allow_all_remaining = self._prompt_overwrite_conflict(
                conflict,
                destination_label=destination_label,
                remaining_count=remaining_count,
            )
            if not approved:
                return False
        return True

    def _prompt_overwrite_conflict(
        self,
        conflict: OverwriteConflict,
        *,
        destination_label: str,
        remaining_count: int,
    ) -> tuple[bool, bool]:
        plural = "files" if remaining_count else "file"
        preview_lines = [
            f"Source: {conflict.source_path}",
            f"Destination: {conflict.destination_path}",
        ]
        if remaining_count:
            preview_lines.append(f"(and {remaining_count} more)")

        box = QMessageBox(self)
        box.setWindowTitle("Confirm Overwrite")
        box.setIcon(QMessageBox.Warning)
        box.setText(f"Overwrite existing {destination_label} {plural}?")
        box.setInformativeText(
            "\n".join(preview_lines) + "\n\nThis will replace the existing file."
        )
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.No)

        allow_all_checkbox: QCheckBox | None = None
        if remaining_count > 0:
            allow_all_checkbox = QCheckBox("Allow all overwrites for this transfer", box)
            box.setCheckBox(allow_all_checkbox)

        with self._suspend_focus_tracking():
            answer = box.exec()
        allow_all = bool(allow_all_checkbox and allow_all_checkbox.isChecked())
        return answer == QMessageBox.Yes, allow_all

    @staticmethod
    def _remote_parent(path: str) -> str:
        if not path or path == "/":
            return "/"
        cleaned = path.rstrip("/")
        if not cleaned:
            return "/"
        parent = posixpath.dirname(cleaned)
        return parent or "/"

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            local_urls = [url for url in event.mimeData().urls() if url.isLocalFile()]
            if local_urls:
                event.acceptProposedAction()
                return
        super().dragEnterEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        if not event.mimeData().hasUrls():
            super().dropEvent(event)
            return
        local_paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        local_paths = [path for path in local_paths if path]
        if not local_paths:
            super().dropEvent(event)
            return
        self._upload_local_paths(local_paths)
        event.acceptProposedAction()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self._remote_load_closing = True
        self._cancel_remote_directory_load(wait_ms=250)
        super().closeEvent(event)


class RemoteViewerTab(QWidget):
    def __init__(
        self,
        *,
        session: Session,
        protocol_name: str,
        detached_command_builder: RemoteDetachedBuilder,
        embedded_command_builder: RemoteEmbeddedBuilder | None = None,
        windows_reparent_embed: bool = False,
        linux_x11_reparent_embed: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._session = session
        self._protocol_name = protocol_name
        self._detached_command_builder = detached_command_builder
        self._embedded_command_builder = embedded_command_builder
        self._windows_reparent_embed = (
            windows_reparent_embed and platform.system().lower() == "windows"
        )
        self._linux_x11_reparent_embed = (
            linux_x11_reparent_embed and platform.system().lower() == "linux"
        )
        self._embedded_process: subprocess.Popen | None = None
        self._detached_process: subprocess.Popen | None = None
        self._embedded_window_handle: int | None = None
        self._last_exit_code: int | None = None
        self._last_exit_mode: str = ""
        self._mode = "idle"
        self._viewer_name = ""
        self._is_windows_rdp_reparent = (
            self._windows_reparent_embed and self._protocol_name.strip().upper() == "RDP"
        )
        self._auto_display_resolution = is_auto_resolution(session.display_resolution)
        parsed_resolution = parse_resolution(session.display_resolution)
        self._fixed_display_resolution: tuple[int, int] | None = None
        if parsed_resolution and not self._auto_display_resolution:
            self._fixed_display_resolution = parsed_resolution
        self._pending_windows_auto_attach = False
        self._windows_auto_attach_deadline = 0.0
        self._detached_external_handoff = False
        self._linux_embed_missing_window_count = 0
        self._linux_resize_failure_count = 0
        self._debug_enabled = (
            os.getenv(REMOTE_VIEWER_DEBUG_ENV, "").strip().lower() in {"1", "true", "yes", "on"}
            or os.getenv(LEGACY_REMOTE_VIEWER_DEBUG_ENV, "").strip().lower() in {"1", "true", "yes", "on"}
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        session_name = (session.name or "").strip()
        host = session.host.strip()
        if session_name and host:
            title_text = f"{protocol_name} session: {session_name} ({host})"
        else:
            title_text = f"{protocol_name} session: {_session_display_name(session, fallback=protocol_name)}"
        self._title_label = QLabel(title_text)
        self._title_label.setWordWrap(True)
        layout.addWidget(self._title_label)

        self._state_label = QLabel("")
        self._state_label.setWordWrap(True)
        layout.addWidget(self._state_label)

        self._embed_scroll = QScrollArea(self)
        self._embed_scroll.setFrameShape(QFrame.NoFrame)
        self._embed_scroll.setWidgetResizable(True)
        self._embed_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._embed_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._embed_scroll.setMinimumSize(520, 320)

        self._embed_host = QFrame()
        self._embed_host.setFrameShape(QFrame.StyledPanel)
        self._embed_host.setAttribute(Qt.WA_NativeWindow, True)
        self._embed_host.installEventFilter(self)
        self._embed_scroll.setWidget(self._embed_host)
        self._embed_scroll.viewport().installEventFilter(self)
        layout.addWidget(self._embed_scroll, 1)

        self._configure_embed_host_layout()

        self._hint_label = QLabel("Use tab context menu to detach or attach this viewer.")
        self._hint_label.setWordWrap(True)
        layout.addWidget(self._hint_label)

        self._process_watch_timer = QTimer(self)
        self._process_watch_timer.setInterval(1000)
        self._process_watch_timer.timeout.connect(self._watch_process_state)
        self._process_watch_timer.start()
        self._debug(
            f"Initialized remote tab protocol={self._protocol_name} host={self._session.host} "
            f"windows_reparent={self._windows_reparent_embed} "
            f"linux_x11_reparent={self._linux_x11_reparent_embed}"
        )
        self._update_state_text()

    def supports_embedded_mode(self) -> bool:
        if self._embedded_command_builder is not None or self._windows_reparent_embed:
            return True
        if not self._linux_x11_reparent_embed:
            return False
        available, _ = self._linux_x11_reparent_support_status()
        return available

    def _is_linux_parent_window_rdp_embed(self) -> bool:
        return (
            platform.system().lower() == "linux"
            and self._protocol_name.strip().upper() == "RDP"
            and self._embedded_command_builder is not None
            and not self._windows_reparent_embed
            and not self._linux_x11_reparent_embed
        )

    def is_detached(self) -> bool:
        return self._mode == "detached"

    def is_vnc_viewer(self) -> bool:
        return self._protocol_name.strip().upper() == "VNC"

    def is_linux_rdp_viewer(self) -> bool:
        return (
            self._protocol_name.strip().upper() == "RDP"
            and platform.system().lower() == "linux"
        )

    def has_active_connection(self) -> bool:
        return self._is_process_running(self._embedded_process) or self._is_process_running(self._detached_process)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if (
            watched is self._embed_host
            or (self._embed_scroll is not None and watched is self._embed_scroll.viewport())
        ) and event.type() == QEvent.Resize:
            self._resize_embedded_window()
        return super().eventFilter(watched, event)

    def _configure_embed_host_layout(self) -> None:
        if not self._is_windows_rdp_reparent:
            self._embed_scroll.setWidgetResizable(True)
            self._embed_host.setMinimumSize(520, 320)
            return
        if self._fixed_display_resolution:
            width, height = self._fixed_display_resolution
            self._embed_scroll.setWidgetResizable(False)
            self._embed_host.setFixedSize(width, height)
            return
        self._embed_scroll.setWidgetResizable(True)
        self._embed_host.setMinimumSize(1, 1)
        self._embed_host.setMaximumSize(16777215, 16777215)

    def _embed_target_size(self) -> tuple[int, int]:
        if self._is_windows_rdp_reparent and self._fixed_display_resolution:
            width, height = self._fixed_display_resolution
            return max(1, width), max(1, height)
        viewport = self._embed_scroll.viewport().rect()
        width = max(1, viewport.width())
        height = max(1, viewport.height())
        return width, height

    @staticmethod
    def _normalize_launch_spec(
        spec: RemoteLaunchSpec,
    ) -> tuple[list[str], str, dict[str, str] | None, str | None]:
        if len(spec) == 4:
            cmd, viewer_name, launch_env, stdin_payload = spec
            return cmd, viewer_name, launch_env, stdin_payload
        cmd, viewer_name, launch_env = spec
        return cmd, viewer_name, launch_env, None

    @staticmethod
    def _write_process_stdin(process: subprocess.Popen, stdin_payload: str | None) -> None:
        if stdin_payload is None:
            return
        stdin = process.stdin
        if stdin is None:
            try:
                process.kill()
            except Exception:
                pass
            raise RuntimeError("Failed to open stdin for remote viewer credential handoff.")
        try:
            stdin.write(stdin_payload)
            stdin.flush()
        except Exception as exc:
            try:
                process.kill()
            except Exception:
                pass
            raise RuntimeError("Failed to supply credentials to the remote viewer.") from exc
        finally:
            try:
                stdin.close()
            except Exception:
                pass

    def _start_viewer_process(
        self,
        cmd: list[str],
        *,
        launch_env: dict[str, str] | None,
        stdin_payload: str | None = None,
        detached: bool = False,
    ) -> subprocess.Popen:
        popen_kwargs: dict[str, object] = {}
        if detached:
            popen_kwargs.update(self._detached_popen_kwargs(launch_env))
        elif launch_env is not None:
            popen_kwargs["env"] = launch_env
        if stdin_payload is not None:
            popen_kwargs["stdin"] = subprocess.PIPE
            popen_kwargs["text"] = True
        process = subprocess.Popen(cmd, **popen_kwargs)
        self._write_process_stdin(process, stdin_payload)
        return process

    def start(self) -> tuple[bool, str]:
        if not self.supports_embedded_mode():
            return self.detach_viewer()
        embedded_ok, embedded_message = self.attach_viewer()
        if embedded_ok:
            return True, embedded_message
        detached_ok, detached_message = self.detach_viewer()
        if detached_ok:
            return True, f"{embedded_message} Switched to detached mode."
        return False, f"{embedded_message}\n{detached_message}"

    def attach_viewer(self) -> tuple[bool, str]:
        self._detached_external_handoff = False
        if self._windows_reparent_embed:
            return self._attach_windows_reparent()
        if self._linux_x11_reparent_embed:
            available, reason = self._linux_x11_reparent_support_status()
            if not available:
                return False, reason
            return self._attach_linux_x11_reparent()

        if self._embedded_command_builder is None:
            return False, "This viewer does not support embedding in a tab."

        self._stop_detached_process()
        self._stop_embedded_process()
        parent_window_id = int(self._embed_host.winId())

        try:
            cmd, viewer_name, launch_env, stdin_payload = self._normalize_launch_spec(
                self._embedded_command_builder(parent_window_id)
            )
        except Exception as exc:  # noqa: BLE001
            message = str(exc)
            if "not supported" in message.lower():
                self._embedded_command_builder = None
                self._update_state_text()
            return False, f"Failed to prepare embedded launch command: {exc}"

        try:
            self._embedded_process = self._start_viewer_process(
                cmd,
                launch_env=launch_env,
                stdin_payload=stdin_payload,
            )
        except Exception as exc:  # noqa: BLE001
            self._embedded_process = None
            return False, f"Failed to start embedded viewer: {exc}"

        self._viewer_name = viewer_name
        self._mode = "embedded"
        self._clear_exit_state()
        self._update_state_text()
        if self._is_linux_parent_window_rdp_embed() and self._embedded_process is not None:
            self._embedded_window_handle = self._wait_for_linux_main_window(
                self._embedded_process.pid,
                timeout_seconds=1.0,
            )
            self._resize_linux_parent_rdp_embedded_window()
        return True, f"{self._protocol_name} viewer attached in tab ({viewer_name})."

    def detach_viewer(self) -> tuple[bool, str]:
        self._pending_windows_auto_attach = False
        self._windows_auto_attach_deadline = 0.0
        self._detached_external_handoff = False
        if self._windows_reparent_embed:
            detached_ok, detached_message = self._detach_windows_reparent()
            if detached_ok:
                return detached_ok, detached_message
        if self._linux_x11_reparent_embed:
            detached_ok, detached_message = self._detach_linux_x11_reparent()
            if detached_ok:
                return detached_ok, detached_message

        self._stop_embedded_process()
        self._stop_detached_process()

        try:
            cmd, viewer_name, launch_env, stdin_payload = self._normalize_launch_spec(
                self._detached_command_builder()
            )
        except Exception as exc:  # noqa: BLE001
            return False, f"Failed to prepare detached launch command: {exc}"

        try:
            self._detached_process = self._start_viewer_process(
                cmd,
                launch_env=launch_env,
                stdin_payload=stdin_payload,
                detached=True,
            )
        except Exception as exc:  # noqa: BLE001
            self._detached_process = None
            return False, f"Failed to start detached viewer: {exc}"

        self._viewer_name = viewer_name
        self._mode = "detached"
        self._clear_exit_state()
        self._update_state_text()
        return True, f"{self._protocol_name} viewer detached ({viewer_name})."

    def shutdown(self) -> None:
        self._process_watch_timer.stop()
        self._stop_embedded_process()
        self._stop_detached_process()
        self._mode = "idle"
        self._update_state_text()

    def _attach_windows_reparent(self) -> tuple[bool, str]:
        self._debug("Attempting Windows embed via SetParent reparenting.")
        if self._mode == "detached" and self._is_process_running(self._detached_process):
            detached = self._detached_process
            if detached is not None:
                self._debug(f"Re-attaching existing detached process pid={detached.pid}.")
                window_handle = self._embedded_window_handle or self._wait_for_windows_main_window(detached.pid)
                if window_handle is not None and self._set_windows_window_embedded(window_handle):
                    self._embedded_process = detached
                    self._detached_process = None
                    self._embedded_window_handle = window_handle
                    self._mode = "embedded"
                    self._detached_external_handoff = False
                    self._update_state_text()
                    self._debug(f"Attached existing detached process window handle=0x{window_handle:X}.")
                    return True, f"{self._protocol_name} viewer attached in tab ({self._viewer_name or 'viewer'})."
            self._debug("Failed to re-attach detached process window.")
            return False, "Unable to attach the detached viewer window back into this tab."

        self._stop_embedded_process()
        self._stop_detached_process()

        try:
            cmd, viewer_name, launch_env, stdin_payload = self._normalize_launch_spec(
                self._detached_command_builder()
            )
        except Exception as exc:  # noqa: BLE001
            return False, f"Failed to prepare embedded launch command: {exc}"

        try:
            process = self._start_viewer_process(
                cmd,
                launch_env=launch_env,
                stdin_payload=stdin_payload,
            )
        except Exception as exc:  # noqa: BLE001
            return False, f"Failed to start embedded viewer: {exc}"
        self._debug(f"Started viewer process for embed pid={process.pid}.")

        candidates = self._wait_for_windows_window_candidates(process.pid, timeout_seconds=12.0)
        if not candidates:
            self._debug("No top-level window candidates found for embedding; falling back to detached.")
            return self._fallback_windows_process_to_detached(
                process,
                viewer_name,
                "Embedding is unavailable for this session on Windows.",
                enable_auto_attach=True,
            )

        window_handle: int | None = None
        for candidate in candidates:
            if not self._is_windows_candidate_embeddable(candidate):
                continue
            self._debug(f"Trying to embed candidate handle=0x{candidate:X}.")
            if not self._set_windows_window_embedded(candidate):
                continue
            window_handle = candidate
            break
        if window_handle is None:
            self._debug("All candidate windows failed embedding; falling back to detached.")
            return self._fallback_windows_process_to_detached(
                process,
                viewer_name,
                "Viewer started, but Windows prevented in-tab embedding.",
                enable_auto_attach=True,
            )

        self._embedded_process = process
        self._embedded_window_handle = window_handle
        self._viewer_name = viewer_name
        self._mode = "embedded"
        self._clear_exit_state()
        self._update_state_text()
        self._debug(f"Embedded process pid={process.pid} handle=0x{window_handle:X}.")
        return True, f"{self._protocol_name} viewer attached in tab ({viewer_name})."

    def _detach_windows_reparent(self) -> tuple[bool, str]:
        if self._mode == "detached" and self._is_process_running(self._detached_process):
            return True, f"{self._protocol_name} viewer is already detached ({self._viewer_name or 'viewer'})."

        if self._mode != "embedded":
            return False, "No embedded viewer is active."
        if not self._is_process_running(self._embedded_process):
            self._embedded_process = None
            self._embedded_window_handle = None
            self._mode = "idle"
            self._update_state_text()
            return False, "Embedded viewer process is no longer running."

        handle = self._embedded_window_handle
        if handle is None and self._embedded_process is not None:
            handle = self._wait_for_windows_main_window(self._embedded_process.pid, timeout_seconds=1.0)
        if handle is None:
            return False, "Unable to locate embedded viewer window."
        if not self._set_windows_window_detached(handle):
            return False, "Failed to detach embedded viewer window."

        self._detached_process = self._embedded_process
        self._embedded_process = None
        self._embedded_window_handle = handle
        self._mode = "detached"
        self._pending_windows_auto_attach = False
        self._windows_auto_attach_deadline = 0.0
        self._detached_external_handoff = False
        self._clear_exit_state()
        self._update_state_text()
        return True, f"{self._protocol_name} viewer detached ({self._viewer_name or 'viewer'})."

    def _attach_linux_x11_reparent(self) -> tuple[bool, str]:
        self._debug("Attempting Linux embed via X11 window reparenting.")
        if self._mode == "detached" and self._is_process_running(self._detached_process):
            detached = self._detached_process
            if detached is not None:
                self._debug(f"Re-attaching existing detached Linux process pid={detached.pid}.")
                candidate_handles: list[int] = []
                if self._embedded_window_handle is not None:
                    candidate_handles.append(self._embedded_window_handle)
                refreshed = self._wait_for_linux_main_window(detached.pid, timeout_seconds=1.0)
                if refreshed is not None and refreshed not in candidate_handles:
                    candidate_handles.append(refreshed)
                for window_handle in candidate_handles:
                    if not self._set_linux_window_embedded(window_handle):
                        continue
                    self._embedded_process = detached
                    self._detached_process = None
                    self._embedded_window_handle = window_handle
                    self._mode = "embedded"
                    self._detached_external_handoff = False
                    self._linux_embed_missing_window_count = 0
                    self._linux_resize_failure_count = 0
                    self._clear_exit_state()
                    self._update_state_text()
                    self._debug(f"Attached existing detached Linux window handle=0x{window_handle:X}.")
                    return True, f"{self._protocol_name} viewer attached in tab ({self._viewer_name or 'viewer'})."
            self._debug("Failed to re-attach detached Linux process window.")
            return False, "Unable to attach the detached viewer window back into this tab."

        self._stop_embedded_process()
        self._stop_detached_process()

        try:
            cmd, viewer_name, launch_env, stdin_payload = self._normalize_launch_spec(
                self._detached_command_builder()
            )
        except Exception as exc:  # noqa: BLE001
            return False, f"Failed to prepare embedded launch command: {exc}"
        # VNC needs geometry/resize flags for stable tab embedding.
        if self.is_vnc_viewer():
            cmd = self._prepare_linux_embedded_command(cmd)

        try:
            process = self._start_viewer_process(
                cmd,
                launch_env=launch_env,
                stdin_payload=stdin_payload,
            )
        except Exception as exc:  # noqa: BLE001
            return False, f"Failed to start embedded viewer: {exc}"
        self._debug(f"Started viewer process for Linux embed pid={process.pid}.")

        candidates = self._wait_for_linux_window_candidates(process.pid, timeout_seconds=12.0)
        if not candidates:
            self._debug("No X11 window candidates found for embedding; falling back to detached.")
            return self._fallback_linux_process_to_detached(
                process,
                viewer_name,
                "Embedding is unavailable for this session on Linux.",
            )

        window_handle: int | None = None
        for candidate in candidates:
            self._debug(f"Trying to embed Linux candidate handle=0x{candidate:X}.")
            if not self._set_linux_window_embedded(candidate):
                continue
            window_handle = candidate
            break
        if window_handle is None:
            self._debug("All Linux candidate windows failed embedding; falling back to detached.")
            return self._fallback_linux_process_to_detached(
                process,
                viewer_name,
                "Viewer started, but Linux prevented in-tab embedding.",
            )

        self._embedded_process = process
        self._embedded_window_handle = window_handle
        self._viewer_name = viewer_name
        self._mode = "embedded"
        self._linux_embed_missing_window_count = 0
        self._linux_resize_failure_count = 0
        self._clear_exit_state()
        self._update_state_text()
        self._debug(f"Embedded Linux process pid={process.pid} handle=0x{window_handle:X}.")
        if not self._verify_linux_embedded_window_health(window_handle, timeout_seconds=2.0):
            self._debug("Linux embedded window failed post-attach health check; falling back to detached.")
            return self._fallback_linux_process_to_detached(
                process,
                viewer_name,
                "Embedded mode is unstable for this viewer/session; switched to detached mode.",
            )
        return True, f"{self._protocol_name} viewer attached in tab ({viewer_name})."

    def _detach_linux_x11_reparent(self) -> tuple[bool, str]:
        if self._mode == "detached" and self._is_process_running(self._detached_process):
            return True, f"{self._protocol_name} viewer is already detached ({self._viewer_name or 'viewer'})."

        if self._mode != "embedded":
            return False, "No embedded viewer is active."
        if not self._is_process_running(self._embedded_process):
            self._embedded_process = None
            self._embedded_window_handle = None
            self._mode = "idle"
            self._update_state_text()
            return False, "Embedded viewer process is no longer running."

        handle = self._embedded_window_handle
        if handle is None and self._embedded_process is not None:
            handle = self._wait_for_linux_main_window(self._embedded_process.pid, timeout_seconds=1.0)
        if handle is None:
            return False, "Unable to locate embedded viewer window."
        if not self._set_linux_window_detached(handle):
            return False, "Failed to detach embedded viewer window."

        self._detached_process = self._embedded_process
        self._embedded_process = None
        self._embedded_window_handle = handle
        self._mode = "detached"
        self._detached_external_handoff = False
        self._linux_embed_missing_window_count = 0
        self._linux_resize_failure_count = 0
        self._clear_exit_state()
        self._update_state_text()
        return True, f"{self._protocol_name} viewer detached ({self._viewer_name or 'viewer'})."

    def _fallback_linux_process_to_detached(
        self,
        process: subprocess.Popen,
        viewer_name: str,
        reason: str,
    ) -> tuple[bool, str]:
        self._debug(f"Falling back to detached mode on Linux. reason={reason} pid={process.pid}")
        self._embedded_process = None
        self._detached_process = process
        self._embedded_window_handle = self._wait_for_linux_main_window(process.pid, timeout_seconds=1.0)
        self._viewer_name = viewer_name
        self._mode = "detached"
        self._detached_external_handoff = False
        self._linux_embed_missing_window_count = 0
        self._linux_resize_failure_count = 0
        self._clear_exit_state()
        self._update_state_text()
        return True, f"{reason} Running detached via {viewer_name}."

    def _fallback_windows_process_to_detached(
        self,
        process: subprocess.Popen,
        viewer_name: str,
        reason: str,
        *,
        enable_auto_attach: bool = False,
    ) -> tuple[bool, str]:
        self._debug(f"Falling back to detached mode. reason={reason} pid={process.pid}")
        self._embedded_process = None
        self._detached_process = process
        self._embedded_window_handle = self._wait_for_windows_main_window(process.pid, timeout_seconds=1.0)
        self._viewer_name = viewer_name
        self._mode = "detached"
        self._pending_windows_auto_attach = enable_auto_attach
        self._windows_auto_attach_deadline = time.time() + 120.0 if enable_auto_attach else 0.0
        self._detached_external_handoff = False
        self._clear_exit_state()
        self._update_state_text()
        return True, f"{reason} Running detached via {viewer_name}."

    def _watch_process_state(self) -> None:
        if self._mode == "embedded":
            process = self._embedded_process
            if process is None:
                return
            self._sync_embedded_window(process)
            exit_code = self._poll_exit_code(process)
            if exit_code is None:
                return
            self._embedded_process = None
            self._embedded_window_handle = None
            self._debug(f"Embedded process exited with code={exit_code}.")
            self._mark_process_exited(exit_code, mode="embedded")
            return

        if self._mode == "detached":
            process = self._detached_process
            if process is None:
                return
            if self._pending_windows_auto_attach and self._try_auto_attach_windows_detached(process):
                return
            exit_code = self._poll_exit_code(process)
            if exit_code is None:
                return
            if self._should_treat_detached_exit_as_external_handoff(exit_code):
                self._detached_process = None
                self._embedded_window_handle = None
                self._pending_windows_auto_attach = False
                self._windows_auto_attach_deadline = 0.0
                self._detached_external_handoff = True
                self._mode = "detached"
                self._clear_exit_state()
                self._update_state_text()
                self._debug(
                    "Detached process exited after external client handoff; "
                    "keeping detached state active."
                )
                return
            self._detached_process = None
            self._embedded_window_handle = None
            self._pending_windows_auto_attach = False
            self._windows_auto_attach_deadline = 0.0
            self._detached_external_handoff = False
            self._debug(f"Detached process exited with code={exit_code}.")
            self._mark_process_exited(exit_code, mode="detached")

    def _sync_embedded_window(self, process: subprocess.Popen) -> None:
        if self._windows_reparent_embed:
            self._sync_windows_embedded_window(process)
            return
        if self._linux_x11_reparent_embed:
            self._sync_linux_x11_embedded_window(process)
            return
        if self._is_linux_parent_window_rdp_embed():
            self._sync_linux_parent_rdp_embedded_window(process)

    def _sync_windows_embedded_window(self, process: subprocess.Popen) -> None:
        if not self._windows_reparent_embed:
            return
        if platform.system().lower() != "windows":
            return
        if process.poll() is not None:
            return

        current = self._embedded_window_handle
        if current is not None and not self._is_windows_window(current):
            self._debug(f"Embedded handle became invalid handle=0x{current:X}.")
            self._embedded_window_handle = None

        escaped_candidates = self._find_windows_candidates(process.pid)
        if not escaped_candidates:
            return

        self._debug(
            "Found detached top-level windows while mode=embedded: "
            + ", ".join(f"0x{handle:X}" for handle in escaped_candidates)
        )
        for candidate in escaped_candidates:
            if self._set_windows_window_embedded(candidate):
                self._embedded_window_handle = candidate
                self._debug(f"Re-embedded escaped window handle=0x{candidate:X}.")
                return

        self._debug("Re-embed failed for all escaped windows; switching to detached mode state.")
        self._detached_process = process
        self._embedded_process = None
        self._mode = "detached"
        self._pending_windows_auto_attach = False
        self._windows_auto_attach_deadline = 0.0
        self._detached_external_handoff = False
        self._clear_exit_state()
        self._update_state_text()

    def _sync_linux_x11_embedded_window(self, process: subprocess.Popen) -> None:
        if not self._linux_x11_reparent_embed:
            return
        if platform.system().lower() != "linux":
            return
        if process.poll() is not None:
            return

        current = self._embedded_window_handle
        if current is not None and not self._is_linux_window(current):
            self._debug(f"Embedded Linux handle became invalid handle=0x{current:X}.")
            self._embedded_window_handle = None
            current = None

        if current is None:
            recovered = self._wait_for_linux_main_window(process.pid, timeout_seconds=0.2)
            if recovered is None:
                self._linux_embed_missing_window_count += 1
                if self._linux_embed_missing_window_count >= 5:
                    self._fallback_linux_embedded_to_detached(
                        "Embedded window became unavailable; switching to detached mode."
                    )
                return
            self._linux_embed_missing_window_count = 0
            self._embedded_window_handle = recovered
            current = recovered
            self._debug(f"Recovered Linux embedded window handle=0x{current:X}.")

        parent_window = int(self._embed_host.winId())
        if parent_window > 0:
            if not self._linux_window_is_descendant_of(current, parent_window):
                actual_parent = self._linux_window_parent(current)
                self._debug(
                    f"Linux embedded window escaped host parent. "
                    f"handle=0x{current:X} expected_parent=0x{parent_window:X} "
                    f"actual_parent=0x{actual_parent:X}" if actual_parent is not None
                    else (
                        f"Linux embedded window escaped host parent. "
                        f"handle=0x{current:X} expected_parent=0x{parent_window:X} "
                        "actual_parent=<root>; attempting re-embed."
                    )
                )
                if not self._set_linux_window_embedded(current):
                    self._fallback_linux_embedded_to_detached(
                        "Embedded viewer escaped host window; switched to detached mode."
                    )
                    return

        self._resize_linux_x11_embedded_window(periodic=True)

    def _sync_linux_parent_rdp_embedded_window(self, process: subprocess.Popen) -> None:
        if not self._is_linux_parent_window_rdp_embed():
            return
        if process.poll() is not None:
            return

        current = self._embedded_window_handle
        if current is not None and not self._is_linux_window(current):
            self._debug(f"Embedded Linux RDP handle became invalid handle=0x{current:X}.")
            self._embedded_window_handle = None

        if self._embedded_window_handle is None:
            recovered = self._wait_for_linux_main_window(process.pid, timeout_seconds=0.2)
            if recovered is None:
                self._linux_embed_missing_window_count += 1
                if self._linux_embed_missing_window_count >= 5:
                    self._fallback_linux_embedded_to_detached(
                        "Embedded RDP window became unavailable; switched to detached mode."
                    )
                return
            self._linux_embed_missing_window_count = 0
            self._embedded_window_handle = recovered
            self._debug(f"Recovered Linux RDP embedded window handle=0x{recovered:X}.")

        self._resize_linux_parent_rdp_embedded_window()

    def _try_auto_attach_windows_detached(self, process: subprocess.Popen) -> bool:
        if not self._windows_reparent_embed:
            return False
        if platform.system().lower() != "windows":
            return False
        if self._protocol_name.strip().upper() != "RDP":
            return False
        if not self._pending_windows_auto_attach:
            return False
        if not self._is_process_running(process):
            return False
        if self._windows_auto_attach_deadline and time.time() > self._windows_auto_attach_deadline:
            self._pending_windows_auto_attach = False
            self._windows_auto_attach_deadline = 0.0
            self._debug("Auto-attach timed out while waiting for an embeddable RDP window.")
            return False

        candidates = self._find_windows_candidates(process.pid)
        embeddable = [handle for handle in candidates if self._is_windows_candidate_embeddable(handle)]
        if not embeddable:
            return False

        for handle in embeddable:
            if not self._set_windows_window_embedded(handle):
                continue
            self._embedded_process = process
            self._detached_process = None
            self._embedded_window_handle = handle
            self._mode = "embedded"
            self._pending_windows_auto_attach = False
            self._windows_auto_attach_deadline = 0.0
            self._detached_external_handoff = False
            self._clear_exit_state()
            self._update_state_text()
            self._debug(f"Auto-attached delayed RDP window handle=0x{handle:X}.")
            return True
        return False

    @staticmethod
    def _is_windows_window(window_handle: int) -> bool:
        if platform.system().lower() != "windows" or window_handle <= 0:
            return False
        try:
            import ctypes
        except Exception:
            return False
        user32 = ctypes.windll.user32
        try:
            return bool(user32.IsWindow(window_handle))
        except Exception:
            return False

    @staticmethod
    def _poll_exit_code(process: subprocess.Popen | None) -> int | None:
        if process is None:
            return None
        try:
            return process.poll()
        except Exception:
            return None

    def _mark_process_exited(self, exit_code: int, *, mode: str) -> None:
        self._mode = "idle"
        self._last_exit_code = exit_code
        self._last_exit_mode = mode
        self._detached_external_handoff = False
        self._update_state_text()

    def _clear_exit_state(self) -> None:
        self._last_exit_code = None
        self._last_exit_mode = ""

    def _embedded_mode_unavailable_reason(self) -> str:
        if self._protocol_name.strip().upper() == "VNC":
            return "VNC sessions run in detached mode only."
        if self._linux_x11_reparent_embed:
            available, reason = self._linux_x11_reparent_support_status()
            if not available:
                return reason
        return "This viewer does not support in-tab embedding on the current platform."

    def _linux_x11_reparent_support_status(self) -> tuple[bool, str]:
        if not self._linux_x11_reparent_embed:
            return False, "This viewer does not support in-tab embedding on the current platform."
        if platform.system().lower() != "linux":
            return False, "Attach mode for this viewer is only available on Linux X11."

        session_type = os.getenv("XDG_SESSION_TYPE", "").strip().lower()
        if session_type == "wayland":
            return False, "Attach mode requires an X11 session; Wayland is currently active."

        if not os.getenv("DISPLAY", "").strip():
            return False, "Attach mode requires an X11 display (DISPLAY is not set)."

        qt_platform = (QApplication.platformName() or "").strip().lower()
        if qt_platform and "xcb" not in qt_platform:
            return False, f"Attach mode requires Qt X11 backend (current backend: {qt_platform})."

        if shutil.which("xdotool") is None:
            return False, "Install xdotool to enable attach mode on Linux."
        if shutil.which("xwininfo") is None:
            return False, "Install x11-utils (xwininfo) to enable attach mode on Linux."
        return True, ""

    def _update_state_text(self) -> None:
        if self._mode == "embedded":
            viewer = self._viewer_name or "viewer"
            if self._windows_reparent_embed and platform.system().lower() == "windows":
                self._state_label.setText(
                    f"Embedded mode active via {viewer} (window reparenting)."
                )
            elif self._linux_x11_reparent_embed and platform.system().lower() == "linux":
                self._state_label.setText(
                    f"Embedded mode active via {viewer} (X11 window reparenting)."
                )
            else:
                self._state_label.setText(f"Embedded mode active via {viewer}.")
            self._hint_label.setText("Use tab context menu to detach this viewer.")
            self.setProperty("remote_viewer_closed", False)
            self._notify_title_refresh()
            return
        if self._mode == "detached":
            viewer = self._viewer_name or "viewer"
            if self._detached_external_handoff:
                self._state_label.setText(f"Detached mode active via {viewer}.")
                self._hint_label.setText(
                    "Viewer is managed by an external client process. Reconnect from the session list if needed."
                )
            elif self.is_vnc_viewer():
                self._state_label.setText(f"Detached mode active via {viewer}. VNC supports detached mode only.")
                self._hint_label.setText("VNC sessions run in detached mode only.")
            elif self.supports_embedded_mode():
                self._state_label.setText(f"Detached mode active via {viewer}.")
                self._hint_label.setText("Use tab context menu to attach this viewer back into the tab.")
            else:
                self._state_label.setText(f"Detached mode active via {viewer}. Attach mode is unavailable.")
                self._hint_label.setText(self._embedded_mode_unavailable_reason())
            self.setProperty("remote_viewer_closed", False)
            self._notify_title_refresh()
            return
        if self._last_exit_code is not None:
            descriptor = f"{self._last_exit_mode} " if self._last_exit_mode else ""
            self._state_label.setText(
                f"Viewer closed ({descriptor}exit code {self._last_exit_code})."
            )
            self._hint_label.setText("Reconnect this session from the session list to launch the viewer again.")
            self.setProperty("remote_viewer_closed", True)
            self._notify_title_refresh()
            return
        self._state_label.setText("Viewer is not running.")
        self._hint_label.setText("Reconnect this session from the session list to launch the viewer.")
        self.setProperty("remote_viewer_closed", False)
        self._notify_title_refresh()

    def _notify_title_refresh(self) -> None:
        owner = self.window()
        refresh = getattr(owner, "_refresh_tab_title", None)
        if callable(refresh):
            try:
                refresh(self)
            except Exception:
                pass

    def _stop_embedded_process(self) -> None:
        process = self._embedded_process
        self._embedded_process = None
        self._embedded_window_handle = None
        self._pending_windows_auto_attach = False
        self._windows_auto_attach_deadline = 0.0
        self._detached_external_handoff = False
        self._linux_embed_missing_window_count = 0
        self._linux_resize_failure_count = 0
        self._stop_process(process)

    def _stop_detached_process(self) -> None:
        process = self._detached_process
        self._detached_process = None
        self._embedded_window_handle = None
        self._pending_windows_auto_attach = False
        self._windows_auto_attach_deadline = 0.0
        self._detached_external_handoff = False
        self._linux_embed_missing_window_count = 0
        self._linux_resize_failure_count = 0
        self._stop_process(process)

    @staticmethod
    def _is_process_running(process: subprocess.Popen | None) -> bool:
        if process is None:
            return False
        try:
            return process.poll() is None
        except Exception:
            return False

    @staticmethod
    def _is_windows_process_name_running(image_name: str) -> bool:
        if platform.system().lower() != "windows":
            return False
        normalized_name = image_name.strip().lower()
        if not normalized_name:
            return False
        try:
            result = subprocess.run(
                [
                    "tasklist",
                    "/FI",
                    f"IMAGENAME eq {normalized_name}",
                    "/FO",
                    "CSV",
                    "/NH",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=1.5,
            )
        except Exception:
            return False
        if result.returncode != 0:
            return False
        payload = (result.stdout or "").strip()
        if not payload:
            return False
        try:
            rows = list(csv.reader(payload.splitlines()))
        except Exception:
            return False
        for row in rows:
            if not row:
                continue
            candidate = (row[0] or "").strip().lower()
            if candidate == normalized_name:
                return True
        return False

    def _should_treat_detached_exit_as_external_handoff(self, exit_code: int) -> bool:
        if exit_code != 0:
            return False
        system = platform.system().lower()
        protocol = self._protocol_name.strip().upper()
        if protocol == "NOMACHINE":
            if system == "windows":
                return self._is_windows_process_name_running("nxplayer.exe")
            if system == "darwin":
                return True
        return False

    @staticmethod
    def _windows_detached_creationflags() -> int:
        if platform.system().lower() != "windows":
            return 0
        return int(getattr(subprocess, "DETACHED_PROCESS", 0)) | int(
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )

    def _detached_popen_kwargs(self, launch_env: dict[str, str] | None) -> dict[str, object]:
        kwargs: dict[str, object] = {}
        if launch_env is not None:
            kwargs["env"] = launch_env
        creationflags = self._windows_detached_creationflags()
        if creationflags:
            kwargs["creationflags"] = creationflags
        return kwargs

    def _wait_for_windows_main_window(self, pid: int, timeout_seconds: float = 5.0) -> int | None:
        candidates = self._wait_for_windows_window_candidates(pid, timeout_seconds=timeout_seconds)
        if not candidates:
            return None
        return candidates[0]

    def _wait_for_windows_window_candidates(
        self,
        pid: int,
        *,
        timeout_seconds: float = 5.0,
    ) -> list[int]:
        deadline = time.time() + max(0.2, timeout_seconds)
        last_candidates: list[int] = []
        while time.time() < deadline:
            candidates = self._find_windows_candidates(pid)
            if candidates:
                last_candidates = candidates
                embeddable_candidates = [h for h in candidates if self._is_windows_candidate_embeddable(h)]
                if embeddable_candidates:
                    self._debug(
                        f"Found {len(embeddable_candidates)} embeddable candidate window(s) for pid={pid}: "
                        + ", ".join(f"0x{handle:X}" for handle in embeddable_candidates)
                    )
                    return embeddable_candidates
                self._debug(
                    f"Found only non-embeddable candidate window(s) for pid={pid}; waiting for main window."
                )
            QApplication.processEvents()
            time.sleep(0.05)
        if last_candidates:
            self._debug(
                f"Timed out waiting for embeddable candidates for pid={pid}; returning "
                f"{len(last_candidates)} non-embeddable candidate(s)."
            )
            return last_candidates
        self._debug(f"No candidate windows found for pid={pid} after {timeout_seconds:.1f}s.")
        return []

    def _wait_for_linux_main_window(self, pid: int, timeout_seconds: float = 5.0) -> int | None:
        candidates = self._wait_for_linux_window_candidates(pid, timeout_seconds=timeout_seconds)
        if not candidates:
            return None
        return candidates[0]

    def _wait_for_linux_window_candidates(
        self,
        pid: int,
        *,
        timeout_seconds: float = 5.0,
    ) -> list[int]:
        deadline = time.time() + max(0.2, timeout_seconds)
        while time.time() < deadline:
            candidates = self._find_linux_candidates(pid)
            if candidates:
                self._debug(
                    f"Found {len(candidates)} Linux candidate window(s) for pid={pid}: "
                    + ", ".join(f"0x{handle:X}" for handle in candidates[:5])
                )
                return candidates
            # Avoid nested Qt event processing while Linux viewers are attaching.
            time.sleep(0.05)
        self._debug(f"No Linux candidate windows found for pid={pid} after {timeout_seconds:.1f}s.")
        return []

    def _find_linux_candidates(self, pid: int) -> list[int]:
        if platform.system().lower() != "linux":
            return []

        outputs: list[str] = []
        target_pids = sorted(self._linux_process_tree_pids(pid))
        for target_pid in target_pids:
            output = self._run_xdotool_capture("search", "--onlyvisible", "--pid", str(target_pid))
            if output:
                outputs.append(output)

        if not outputs:
            return []

        seen: set[int] = set()
        found: list[tuple[int, str, str, int]] = []
        for output in outputs:
            for raw in output.splitlines():
                token = raw.strip()
                if not token:
                    continue
                try:
                    window_id = int(token, 0)
                except ValueError:
                    continue
                if window_id <= 0 or window_id in seen:
                    continue
                seen.add(window_id)
                class_name, title, area = self._linux_window_metadata(window_id)
                found.append((window_id, title, class_name, max(1, area)))
        if not found:
            return []

        candidate_ids = {item[0] for item in found}
        top_level_found = [
            item
            for item in found
            if (parent := self._linux_window_parent(item[0])) is None or parent not in candidate_ids
        ]
        if top_level_found:
            found = top_level_found

        found.sort(
            key=lambda item: self._window_candidate_score(item[1], item[2], item[3]),
            reverse=True,
        )
        self._debug(
            "Linux candidates ranked: "
            + "; ".join(
                f"0x{handle:X} class={class_name or '<none>'} title={title or '<none>'}"
                for handle, title, class_name, _area in found[:5]
            )
        )
        return [item[0] for item in found]

    def _linux_process_tree_pids(self, root_pid: int) -> set[int]:
        if platform.system().lower() != "linux" or root_pid <= 0:
            return {root_pid} if root_pid > 0 else set()

        discovered: set[int] = {root_pid}
        queue: deque[int] = deque([root_pid])
        while queue:
            current = queue.popleft()
            children_path = Path(f"/proc/{current}/task/{current}/children")
            try:
                raw_children = children_path.read_text(encoding="utf-8")
            except OSError:
                continue
            for token in raw_children.split():
                try:
                    child_pid = int(token)
                except ValueError:
                    continue
                if child_pid <= 0 or child_pid in discovered:
                    continue
                discovered.add(child_pid)
                queue.append(child_pid)
        return discovered

    def _linux_window_metadata(self, window_handle: int) -> tuple[str, str, int]:
        if window_handle <= 0:
            return "", "", 0
        class_name = self._run_xdotool_capture("getwindowclass", str(window_handle))
        if not class_name:
            class_name = self._run_xdotool_capture("getwindowclassname", str(window_handle))
        title = self._run_xdotool_capture("getwindowname", str(window_handle))
        width, height = self._linux_window_size(window_handle)
        return class_name, title, max(0, width) * max(0, height)

    def _linux_window_size(self, window_handle: int) -> tuple[int, int]:
        if window_handle <= 0:
            return 0, 0
        geometry = self._run_xdotool_capture("getwindowgeometry", "--shell", str(window_handle))
        width = 0
        height = 0
        for line in geometry.splitlines():
            if line.startswith("WIDTH="):
                try:
                    width = int(line.removeprefix("WIDTH=").strip())
                except ValueError:
                    width = 0
            elif line.startswith("HEIGHT="):
                try:
                    height = int(line.removeprefix("HEIGHT=").strip())
                except ValueError:
                    height = 0
        return width, height

    def _is_linux_window(self, window_handle: int) -> bool:
        if platform.system().lower() != "linux" or window_handle <= 0:
            return False
        output = self._run_xdotool_capture("getwindowpid", str(window_handle))
        return bool(output.strip())

    @staticmethod
    def _xdotool_executable() -> str | None:
        return shutil.which("xdotool")

    def _run_xdotool_capture(self, *args: str) -> str:
        executable = self._xdotool_executable()
        if not executable:
            return ""
        try:
            result = subprocess.run(
                [executable, *args],
                check=False,
                capture_output=True,
                text=True,
            )
        except Exception:
            return ""
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            if stderr:
                self._debug(f"xdotool {' '.join(args)} failed: {stderr}")
            return ""
        return (result.stdout or "").strip()

    def _run_xdotool(self, *args: str) -> bool:
        executable = self._xdotool_executable()
        if not executable:
            return False
        try:
            result = subprocess.run(
                [executable, *args],
                check=False,
                capture_output=True,
                text=True,
            )
        except Exception:
            return False
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            if stderr:
                self._debug(f"xdotool {' '.join(args)} failed: {stderr}")
            return False
        return True

    def _prepare_linux_embedded_command(self, command: list[str]) -> list[str]:
        # Force embedded viewer behavior: no fullscreen and no server-side remote desktop resize requests.
        filtered: list[str] = []
        skip_next = False
        for index, arg in enumerate(command):
            if skip_next:
                skip_next = False
                continue
            lowered = arg.lower()
            if "remoteresize" in lowered:
                continue
            if lowered in ("-fullscreen", "--fullscreen"):
                continue
            if lowered == "-geometry" and index + 1 < len(command):
                skip_next = True
                continue
            filtered.append(arg)
        width, height = self._embed_target_size()
        if len(filtered) >= 2:
            target = filtered[-1]
            options = filtered[1:-1]
            return [
                filtered[0],
                *options,
                "-RemoteResize=0",
                "-geometry",
                f"{width}x{height}",
                target,
            ]
        filtered.extend(["-RemoteResize=0", "-geometry", f"{width}x{height}"])
        return filtered

    def _linux_window_parent(self, window_handle: int) -> int | None:
        if platform.system().lower() != "linux" or window_handle <= 0:
            return None
        executable = shutil.which("xwininfo")
        if not executable:
            return None
        try:
            result = subprocess.run(
                [executable, "-id", str(window_handle)],
                check=False,
                capture_output=True,
                text=True,
            )
        except Exception:
            return None
        if result.returncode != 0:
            return None
        for line in (result.stdout or "").splitlines():
            cleaned = line.strip().lower()
            if not cleaned.startswith("parent window id:"):
                continue
            if "root window" in cleaned:
                return None
            parts = cleaned.split()
            if len(parts) < 4:
                return None
            token = parts[3]
            try:
                return int(token, 0)
            except ValueError:
                return None
        return None

    def _linux_window_is_descendant_of(self, window_handle: int, ancestor_handle: int, *, max_depth: int = 8) -> bool:
        if window_handle <= 0 or ancestor_handle <= 0:
            return False
        current = window_handle
        for _ in range(max(1, max_depth)):
            parent = self._linux_window_parent(current)
            if parent is None:
                return False
            if parent == ancestor_handle:
                return True
            if parent == current:
                return False
            current = parent
        return False

    def _window_candidate_score(self, title: str, class_name: str, area: int) -> int:
        normalized_title = title.lower()
        normalized_class = class_name.lower()
        score = area
        host = (self._session.host or "").strip().lower()
        if host and host in normalized_title:
            score += 4_000_000_000

        protocol = self._protocol_name.strip().upper()
        if protocol == "RDP":
            if "tscshellcontainerclass" in normalized_class:
                score += 3_000_000_000
            if "remote desktop" in normalized_title:
                score += 2_000_000_000
        elif protocol == "VNC":
            if "vnc" in normalized_class or "vnc" in normalized_title:
                score += 2_500_000_000
        return score

    def _find_windows_candidates(self, pid: int) -> list[int]:
        if platform.system().lower() != "windows":
            return []
        try:
            import ctypes
            from ctypes import wintypes
        except Exception:
            return []

        user32 = ctypes.windll.user32
        found: list[tuple[int, str, str, int]] = []
        enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        gw_owner = 4

        def _enum(hwnd, _lparam):  # noqa: ANN001
            process_id = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
            if process_id.value != pid:
                return True
            if user32.GetWindow(hwnd, gw_owner):
                return True
            if not user32.IsWindowVisible(hwnd):
                return True
            title_buffer = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(hwnd, title_buffer, 512)
            title = title_buffer.value or ""
            class_buffer = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, class_buffer, 256)
            class_name = class_buffer.value or ""
            rect = wintypes.RECT()
            if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return True
            width = max(0, rect.right - rect.left)
            height = max(0, rect.bottom - rect.top)
            area = width * height
            if area <= 0:
                return True
            found.append((int(hwnd), title, class_name, area))
            return True

        user32.EnumWindows(enum_proc(_enum), 0)
        if not found:
            return []
        found.sort(
            key=lambda item: self._window_candidate_score(item[1], item[2], item[3]),
            reverse=True,
        )
        self._debug(
            "Windows candidates ranked: "
            + "; ".join(
                f"0x{handle:X} class={class_name or '<none>'} title={title or '<none>'}"
                for handle, title, class_name, _area in found[:5]
            )
        )
        return [item[0] for item in found]

    def _is_windows_candidate_embeddable(self, window_handle: int) -> bool:
        if platform.system().lower() != "windows":
            return True
        class_name, title = self._windows_window_metadata(window_handle)
        normalized_class = class_name.lower()
        normalized_title = title.lower()
        protocol = self._protocol_name.strip().upper()

        if protocol == "RDP":
            # Skip pre-connect/security dialogs. Wait for the real RDP container window.
            if normalized_class == "#32770":
                self._debug(
                    f"Skipping non-embeddable dialog window handle=0x{window_handle:X} "
                    f"class={class_name or '<none>'} title={title or '<none>'}."
                )
                return False
            if "remote desktop connection" in normalized_title and "tscshellcontainerclass" not in normalized_class:
                self._debug(
                    f"Skipping pre-connect RDP window handle=0x{window_handle:X} "
                    f"class={class_name or '<none>'} title={title or '<none>'}."
                )
                return False
        return True

    @staticmethod
    def _windows_window_metadata(window_handle: int) -> tuple[str, str]:
        if platform.system().lower() != "windows" or window_handle <= 0:
            return "", ""
        try:
            import ctypes
        except Exception:
            return "", ""
        user32 = ctypes.windll.user32
        title_buffer = ctypes.create_unicode_buffer(512)
        class_buffer = ctypes.create_unicode_buffer(256)
        try:
            user32.GetWindowTextW(window_handle, title_buffer, 512)
            user32.GetClassNameW(window_handle, class_buffer, 256)
        except Exception:
            return "", ""
        return class_buffer.value or "", title_buffer.value or ""

    def _set_windows_window_embedded(self, window_handle: int) -> bool:
        if platform.system().lower() != "windows":
            return False
        try:
            import ctypes
        except Exception:
            return False

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        parent_window = int(self._embed_host.winId())
        if parent_window <= 0:
            self._debug("Embed host native window id is invalid; cannot embed.")
            return False

        gwl_style = -16
        ws_child = 0x40000000
        ws_overlapped_window = 0x00CF0000
        swp_nozorder = 0x0004
        swp_noactivate = 0x0010
        swp_framechanged = 0x0020
        swp_showwindow = 0x0040
        sw_show = 5

        self._configure_embed_host_layout()
        width, height = self._embed_target_size()

        attempts = 4
        for attempt in range(1, attempts + 1):
            style = user32.GetWindowLongW(window_handle, gwl_style)
            style = (style & ~ws_overlapped_window) | ws_child
            user32.SetWindowLongW(window_handle, gwl_style, style)
            kernel32.SetLastError(0)
            user32.SetParent(window_handle, parent_window)
            last_error = kernel32.GetLastError()
            actual_parent = user32.GetParent(window_handle)
            if int(actual_parent) != parent_window:
                self._debug(
                    f"SetParent failed attempt={attempt}/{attempts} handle=0x{window_handle:X} "
                    f"parent=0x{parent_window:X} actual_parent=0x{int(actual_parent):X} "
                    f"last_error={last_error}"
                )
                time.sleep(0.08)
                continue
            positioned = user32.SetWindowPos(
                window_handle,
                0,
                0,
                0,
                width,
                height,
                swp_nozorder | swp_noactivate | swp_framechanged | swp_showwindow,
            )
            if not positioned:
                self._debug(
                    f"SetWindowPos failed attempt={attempt}/{attempts} after embedding "
                    f"handle=0x{window_handle:X}."
                )
                time.sleep(0.08)
                continue
            user32.ShowWindow(window_handle, sw_show)
            self._debug(
                f"Embedded window handle=0x{window_handle:X} into parent=0x{parent_window:X} "
                f"size={width}x{height} on attempt={attempt}/{attempts}."
            )
            return True
        return False

    def _set_windows_window_detached(self, window_handle: int) -> bool:
        if platform.system().lower() != "windows":
            return False
        try:
            import ctypes
        except Exception:
            return False

        user32 = ctypes.windll.user32
        gwl_style = -16
        ws_child = 0x40000000
        ws_overlapped_window = 0x00CF0000
        swp_nozorder = 0x0004
        swp_noactivate = 0x0010
        swp_framechanged = 0x0020
        sw_show = 5

        style = user32.GetWindowLongW(window_handle, gwl_style)
        style = (style & ~ws_child) | ws_overlapped_window
        user32.SetWindowLongW(window_handle, gwl_style, style)
        user32.SetParent(window_handle, 0)
        actual_parent = user32.GetParent(window_handle)
        if int(actual_parent) != 0:
            self._debug(
                f"Failed to detach window handle=0x{window_handle:X}; actual_parent=0x{int(actual_parent):X}."
            )
            return False
        positioned = user32.SetWindowPos(
            window_handle,
            0,
            80,
            80,
            1200,
            800,
            swp_nozorder | swp_noactivate | swp_framechanged,
        )
        if not positioned:
            self._debug(f"SetWindowPos failed after detach handle=0x{window_handle:X}.")
            return False
        user32.ShowWindow(window_handle, sw_show)
        self._debug(f"Detached window handle=0x{window_handle:X}.")
        return True

    def _set_linux_window_embedded(self, window_handle: int) -> bool:
        if platform.system().lower() != "linux":
            return False

        parent_window = int(self._embed_host.winId())
        if parent_window <= 0:
            self._debug("Embed host native window id is invalid; cannot embed on Linux.")
            return False

        self._configure_embed_host_layout()
        width, height = self._embed_target_size()
        attempts = 4
        for attempt in range(1, attempts + 1):
            if not self._run_xdotool("windowreparent", str(window_handle), str(parent_window)):
                self._debug(
                    f"windowreparent failed attempt={attempt}/{attempts} "
                    f"handle=0x{window_handle:X} parent=0x{parent_window:X}."
                )
                time.sleep(0.08)
                continue
            actual_parent = self._linux_window_parent(window_handle)
            if actual_parent is not None and actual_parent != parent_window:
                self._debug(
                    f"windowreparent verification failed attempt={attempt}/{attempts} "
                    f"handle=0x{window_handle:X} expected_parent=0x{parent_window:X} "
                    f"actual_parent=0x{actual_parent:X}."
                )
                time.sleep(0.08)
                continue
            moved = self._run_xdotool("windowmove", str(window_handle), "0", "0")
            sized = self._run_xdotool("windowsize", str(window_handle), str(width), str(height))
            mapped = self._run_xdotool("windowmap", str(window_handle))
            if not (moved and sized and mapped):
                self._debug(
                    f"Embedded Linux command sequence failed attempt={attempt}/{attempts} "
                    f"move={moved} size={sized} map={mapped}."
                )
                time.sleep(0.08)
                continue
            self._debug(
                f"Embedded Linux window handle=0x{window_handle:X} into parent=0x{parent_window:X} "
                f"size={width}x{height} on attempt={attempt}/{attempts}."
            )
            return True
        return False

    def _set_linux_window_detached(self, window_handle: int) -> bool:
        if platform.system().lower() != "linux":
            return False
        if not self._run_xdotool("windowreparent", str(window_handle), "root"):
            self._debug(f"windowreparent to root failed handle=0x{window_handle:X}.")
            return False
        self._run_xdotool("windowmove", str(window_handle), "80", "80")
        self._run_xdotool("windowsize", str(window_handle), "1200", "800")
        self._run_xdotool("windowmap", str(window_handle))
        self._debug(f"Detached Linux window handle=0x{window_handle:X}.")
        return True

    def _fallback_linux_embedded_to_detached(self, reason: str) -> None:
        process = self._embedded_process
        if process is None:
            return
        self._debug(f"Linux embedded fallback to detached. reason={reason} pid={process.pid}")
        self._detached_process = process
        self._embedded_process = None
        self._embedded_window_handle = self._wait_for_linux_main_window(process.pid, timeout_seconds=1.0)
        self._mode = "detached"
        self._detached_external_handoff = False
        self._linux_embed_missing_window_count = 0
        self._linux_resize_failure_count = 0
        self._clear_exit_state()
        self._update_state_text()

    def _verify_linux_embedded_window_health(self, window_handle: int, *, timeout_seconds: float) -> bool:
        if window_handle <= 0:
            return False
        deadline = time.time() + max(0.3, timeout_seconds)
        while time.time() < deadline:
            if not self._is_linux_window(window_handle):
                return False
            parent_window = int(self._embed_host.winId())
            if parent_window > 0:
                if not self._linux_window_is_descendant_of(window_handle, parent_window):
                    time.sleep(0.08)
                    continue
            if not self._resize_linux_x11_embedded_window():
                time.sleep(0.08)
                continue
            return True
        return False

    def _resize_embedded_window(self) -> None:
        if self._windows_reparent_embed:
            self._resize_windows_embedded_window()
            return
        if self._linux_x11_reparent_embed:
            self._resize_linux_x11_embedded_window()
            return
        if self._is_linux_parent_window_rdp_embed():
            self._resize_linux_parent_rdp_embedded_window()

    def _resize_windows_embedded_window(self) -> None:
        if not self._windows_reparent_embed or self._mode != "embedded":
            return
        window_handle = self._embedded_window_handle
        if window_handle is None:
            return
        if platform.system().lower() != "windows":
            return
        try:
            import ctypes
        except Exception:
            return

        user32 = ctypes.windll.user32
        self._configure_embed_host_layout()
        width, height = self._embed_target_size()
        try:
            user32.MoveWindow(window_handle, 0, 0, width, height, True)
        except Exception:
            return

    def _resize_linux_x11_embedded_window(self, *, periodic: bool = False) -> bool:
        if not self._linux_x11_reparent_embed or self._mode != "embedded":
            return False
        window_handle = self._embedded_window_handle
        if window_handle is None:
            return False
        if platform.system().lower() != "linux":
            return False
        self._configure_embed_host_layout()
        width, height = self._embed_target_size()
        moved = self._run_xdotool("windowmove", str(window_handle), "0", "0")
        sized = self._run_xdotool("windowsize", str(window_handle), str(width), str(height))
        actual_width, actual_height = self._linux_window_size(window_handle)
        size_ok = (
            actual_width >= max(120, int(width * 0.65))
            and actual_height >= max(90, int(height * 0.65))
        )
        if moved and sized and size_ok:
            self._linux_resize_failure_count = 0
            return True
        self._linux_resize_failure_count += 1
        self._debug(
            f"Linux embedded resize failed move={moved} size={sized} "
            f"actual={actual_width}x{actual_height} target={width}x{height} "
            f"failure_count={self._linux_resize_failure_count}."
        )
        if self._linux_resize_failure_count >= 3:
            self._fallback_linux_embedded_to_detached(
                "Embedded viewer could not be moved/resized reliably; switched to detached mode."
            )
        return False

    def _resize_linux_parent_rdp_embedded_window(self) -> bool:
        if not self._is_linux_parent_window_rdp_embed() or self._mode != "embedded":
            return False
        if platform.system().lower() != "linux":
            return False

        window_handle = self._embedded_window_handle
        process = self._embedded_process
        if window_handle is None:
            if process is None or process.poll() is not None:
                return False
            recovered = self._wait_for_linux_main_window(process.pid, timeout_seconds=0.2)
            if recovered is None:
                return False
            window_handle = recovered
            self._embedded_window_handle = recovered
            self._debug(f"Resolved Linux RDP window for resize handle=0x{recovered:X}.")
        elif not self._is_linux_window(window_handle):
            self._embedded_window_handle = None
            self._debug(f"Linux RDP resize skipped: invalid handle=0x{window_handle:X}.")
            return False

        self._configure_embed_host_layout()
        width, height = self._embed_target_size()
        actual_width, actual_height = self._linux_window_size(window_handle)
        if actual_width == width and actual_height == height:
            self._linux_resize_failure_count = 0
            return True

        # Avoid frequent map/move operations in parent-window mode; they can destabilize FreeRDP on resize.
        sized = self._run_xdotool("windowsize", str(window_handle), str(width), str(height))
        post_width, post_height = self._linux_window_size(window_handle)
        size_ok = (
            post_width >= max(120, int(width * 0.65))
            and post_height >= max(90, int(height * 0.65))
        )
        if sized and size_ok:
            self._linux_resize_failure_count = 0
            return True

        self._linux_resize_failure_count += 1
        self._debug(
            f"Linux RDP embedded resize failed handle=0x{window_handle:X} "
            f"size={sized} actual={post_width}x{post_height} "
            f"target={width}x{height} failure_count={self._linux_resize_failure_count}."
        )
        if self._linux_resize_failure_count >= 3:
            self._fallback_linux_embedded_to_detached(
                "Embedded RDP viewer could not be resized reliably; switched to detached mode."
            )
        return False

    def _debug(self, message: str) -> None:
        if not self._debug_enabled:
            return
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        line = (
            f"{timestamp} [{self._protocol_name}] host={self._session.host} "
            f"mode={self._mode} :: {message}\n"
        )
        try:
            REMOTE_VIEWER_DEBUG_LOG.parent.mkdir(parents=True, exist_ok=True)
            with REMOTE_VIEWER_DEBUG_LOG.open("a", encoding="utf-8") as handle:
                handle.write(line)
        except Exception:
            pass

    @staticmethod
    def _stop_process(process: subprocess.Popen | None) -> None:
        if process is None:
            return
        try:
            if process.poll() is not None:
                return
        except Exception:
            return
        try:
            process.terminate()
        except Exception:
            return
        try:
            process.wait(timeout=2)
            return
        except Exception:
            pass
        try:
            process.kill()
        except Exception:
            return
        try:
            process.wait(timeout=1)
        except Exception:
            pass


class SessionTreeWidget(QTreeWidget):
    session_drop_requested = Signal(list, str)

    MIME_TYPE = "application/x-snakesh-session-ids"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setIndentation(18)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setDefaultDropAction(Qt.MoveAction)

    def startDrag(self, supported_actions) -> None:  # noqa: N802
        selected_ids = self._selected_session_ids()
        if not selected_ids:
            return
        payload = ",".join(selected_ids).encode("utf-8")
        mime = QMimeData()
        mime.setData(self.MIME_TYPE, payload)
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.MoveAction)

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if self._extract_session_ids(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if not self._extract_session_ids(event.mimeData()):
            super().dragMoveEvent(event)
            return
        target_folder = self._target_folder_for_position(event.position().toPoint().x(), event.position().toPoint().y())
        if target_folder:
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        session_ids = self._extract_session_ids(event.mimeData())
        if not session_ids:
            super().dropEvent(event)
            return
        target_folder = self._target_folder_for_position(event.position().toPoint().x(), event.position().toPoint().y())
        if not target_folder:
            super().dropEvent(event)
            return
        self.session_drop_requested.emit(session_ids, target_folder)
        event.acceptProposedAction()

    def _extract_session_ids(self, mime_data: QMimeData) -> list[str]:
        if not mime_data.hasFormat(self.MIME_TYPE):
            return []
        data = bytes(mime_data.data(self.MIME_TYPE)).decode("utf-8")
        return [item for item in data.split(",") if item]

    def _selected_session_ids(self) -> list[str]:
        seen: set[str] = set()
        output: list[str] = []
        for item in self.selectedItems():
            if item.data(0, TREE_KIND_ROLE) != "session":
                continue
            session_id = item.data(0, TREE_SESSION_ID_ROLE)
            if not isinstance(session_id, str) or not session_id or session_id in seen:
                continue
            seen.add(session_id)
            output.append(session_id)
        return output

    def _target_folder_for_position(self, x: int, y: int) -> str | None:
        item = self.itemAt(x, y)
        if not item:
            return "Default"
        kind = item.data(0, TREE_KIND_ROLE)
        if kind == "folder":
            folder = item.data(0, TREE_FOLDER_PATH_ROLE)
            return folder if isinstance(folder, str) and folder else "Default"
        if kind == "session":
            folder = item.data(0, TREE_SESSION_FOLDER_ROLE)
            return folder if isinstance(folder, str) and folder else "Default"
        return "Default"


@dataclass(frozen=True)
class _ActiveTerminalTabEntry:
    host: "WorkspaceTabWidget"
    index: int
    tab: TerminalTab
    label: str


class TabGroupingDialog(QDialog):
    def __init__(
        self,
        entries: list[tuple[QWidget, str]],
        preselected: set[int],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Group Tabs")
        self.resize(520, 380)
        self._entry_by_row: dict[int, QWidget] = {}

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Select terminal tabs to include in this group:"))

        self.tab_list = QListWidget()
        for row, (widget, label) in enumerate(entries):
            item = QListWidgetItem(label)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            item.setCheckState(Qt.Checked if id(widget) in preselected else Qt.Unchecked)
            self.tab_list.addItem(item)
            self._entry_by_row[row] = widget
        layout.addWidget(self.tab_list, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_widgets(self) -> list[QWidget]:
        selected: list[QWidget] = []
        for row in range(self.tab_list.count()):
            item = self.tab_list.item(row)
            if not item or item.checkState() != Qt.Checked:
                continue
            widget = self._entry_by_row.get(row)
            if widget is not None:
                selected.append(widget)
        return selected


class BulkDisconnectTabsDialog(QDialog):
    def __init__(
        self,
        entries: list[_ActiveTerminalTabEntry],
        preselected: set[int],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Disconnect Selected Tabs")
        self.resize(560, 420)
        self._entry_by_row: dict[int, _ActiveTerminalTabEntry] = {}

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Select terminal tabs to disconnect:"))

        self.tab_list = QListWidget()
        for row, entry in enumerate(entries):
            item = QListWidgetItem(entry.label)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            item.setCheckState(Qt.Checked if id(entry.tab) in preselected else Qt.Unchecked)
            self.tab_list.addItem(item)
            self._entry_by_row[row] = entry
        layout.addWidget(self.tab_list, 1)

        self.close_after_disconnect_checkbox = QCheckBox("Close selected tabs after disconnect")
        layout.addWidget(self.close_after_disconnect_checkbox)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_tabs(self) -> list[TerminalTab]:
        selected: list[TerminalTab] = []
        for row in range(self.tab_list.count()):
            item = self.tab_list.item(row)
            if not item or item.checkState() != Qt.Checked:
                continue
            entry = self._entry_by_row.get(row)
            if entry is not None:
                selected.append(entry.tab)
        return selected

    def close_after_disconnect(self) -> bool:
        return self.close_after_disconnect_checkbox.isChecked()


class GroupManagerDialog(QDialog):
    def __init__(
        self,
        groups: list[tuple[str, str, list[str]]],
        preselect_group_id: str | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Group Manager")
        self.resize(760, 430)
        self._group_rows: dict[str, int] = {}
        self._renamed: dict[str, str] = {}
        self._ungrouped: set[str] = set()

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Manage terminal tab groups:"))

        self.group_list = QTreeWidget()
        self.group_list.setColumnCount(3)
        self.group_list.setHeaderLabels(["Group", "Tabs", "Members"])
        self.group_list.setAlternatingRowColors(True)
        self.group_list.setRootIsDecorated(False)
        self.group_list.setSelectionMode(QAbstractItemView.SingleSelection)
        layout.addWidget(self.group_list, 1)

        for group_id, group_name, member_titles in groups:
            item = QTreeWidgetItem(
                [
                    group_name,
                    str(len(member_titles)),
                    ", ".join(member_titles),
                ]
            )
            item.setData(0, Qt.UserRole, group_id)
            self.group_list.addTopLevelItem(item)
            self._group_rows[group_id] = self.group_list.topLevelItemCount() - 1
            if preselect_group_id and group_id == preselect_group_id:
                self.group_list.setCurrentItem(item)

        if self.group_list.topLevelItemCount() and self.group_list.currentItem() is None:
            self.group_list.setCurrentItem(self.group_list.topLevelItem(0))
        for column in range(3):
            self.group_list.resizeColumnToContents(column)

        button_row = QHBoxLayout()
        self.rename_btn = QPushButton("Rename Group")
        self.ungroup_btn = QPushButton("Ungroup Selected")
        close_btn = QPushButton("Close")
        button_row.addWidget(self.rename_btn)
        button_row.addWidget(self.ungroup_btn)
        button_row.addStretch(1)
        button_row.addWidget(close_btn)
        layout.addLayout(button_row)

        self.rename_btn.clicked.connect(self._rename_selected)
        self.ungroup_btn.clicked.connect(self._ungroup_selected)
        close_btn.clicked.connect(self.accept)
        self.group_list.itemSelectionChanged.connect(self._update_buttons)
        self._update_buttons()

    def renamed_groups(self) -> dict[str, str]:
        return dict(self._renamed)

    def ungrouped_groups(self) -> set[str]:
        return set(self._ungrouped)

    def _selected_group_item(self) -> QTreeWidgetItem | None:
        current = self.group_list.currentItem()
        if current is None:
            return None
        group_id = current.data(0, Qt.UserRole)
        if not isinstance(group_id, str) or not group_id:
            return None
        return current

    def _selected_group_id(self) -> str | None:
        item = self._selected_group_item()
        if item is None:
            return None
        value = item.data(0, Qt.UserRole)
        if isinstance(value, str) and value:
            return value
        return None

    def _rename_selected(self) -> None:
        item = self._selected_group_item()
        if item is None:
            return
        group_id = self._selected_group_id()
        if not group_id:
            return
        current_name = item.text(0).strip()
        entered, ok = QInputDialog.getText(self, "Rename Group", "Group name:", text=current_name)
        if not ok:
            return
        name = entered.strip()
        if not name:
            return
        item.setText(0, name)
        self._renamed[group_id] = name
        self.group_list.resizeColumnToContents(0)

    def _ungroup_selected(self) -> None:
        item = self._selected_group_item()
        if item is None:
            return
        group_id = self._selected_group_id()
        if not group_id:
            return
        row = self.group_list.indexOfTopLevelItem(item)
        if row >= 0:
            self.group_list.takeTopLevelItem(row)
        self._ungrouped.add(group_id)
        self._renamed.pop(group_id, None)
        self._group_rows.pop(group_id, None)
        if self.group_list.topLevelItemCount():
            self.group_list.setCurrentItem(self.group_list.topLevelItem(0))
        self._update_buttons()

    def _update_buttons(self) -> None:
        has_selection = self._selected_group_item() is not None
        self.rename_btn.setEnabled(has_selection)
        self.ungroup_btn.setEnabled(has_selection)


class ProfileToolSelectionDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Start Tools on Load")
        self.resize(360, 480)
        self._checkboxes: dict[str, QCheckBox] = {}

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Open these tools whenever this profile is loaded:"))

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        container = QWidget(scroll)
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(10, 10, 10, 10)
        container_layout.setSpacing(8)

        current_group = None
        for entry in profile_startup_tool_entries():
            if current_group is not None and entry.menu_group != current_group:
                separator = QFrame(container)
                separator.setFrameShape(QFrame.Shape.HLine)
                separator.setFrameShadow(QFrame.Shadow.Sunken)
                container_layout.addWidget(separator)
            checkbox = QCheckBox(entry.label, container)
            self._checkboxes[entry.key] = checkbox
            container_layout.addWidget(checkbox)
            current_group = entry.menu_group
        container_layout.addStretch(1)

        scroll.setWidget(container)
        layout.addWidget(scroll, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def set_selected_tool_keys(self, tool_keys: list[str]) -> None:
        selected = set(normalize_profile_startup_tool_keys(tool_keys))
        for key, checkbox in self._checkboxes.items():
            checkbox.setChecked(key in selected)

    def selected_tool_keys(self) -> list[str]:
        return [
            entry.key
            for entry in profile_startup_tool_entries()
            if self._checkboxes.get(entry.key) is not None and self._checkboxes[entry.key].isChecked()
        ]


class ProfileManagerDialog(QDialog):
    open_profile_requested = Signal(str)
    add_profile_requested = Signal()
    replace_profile_requested = Signal(str)
    delete_profile_requested = Signal(str)
    set_default_profile_requested = Signal(str)
    clear_default_profile_requested = Signal()
    edit_startup_tools_requested = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Profile Manager")
        self.resize(620, 420)
        self._profiles_by_id: dict[str, dict[str, object]] = {}
        self._default_profile_id = ""

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Saved workspace profiles:"))

        self.profile_list = QListWidget()
        self.profile_list.setSelectionMode(QAbstractItemView.SingleSelection)
        layout.addWidget(self.profile_list, 1)

        self.startup_tools_summary = QLabel("Start tools on load: None")
        self.startup_tools_summary.setWordWrap(True)
        layout.addWidget(self.startup_tools_summary)

        controls = QHBoxLayout()
        self.open_btn = QPushButton("Open Profile")
        self.add_btn = QPushButton("Add Current")
        self.replace_btn = QPushButton("Replace With Current")
        self.delete_btn = QPushButton("Delete")
        self.edit_startup_tools_btn = QPushButton("Start Tools on Load...")
        self.set_default_btn = QPushButton("Set Default")
        self.clear_default_btn = QPushButton("Clear Default")
        controls.addWidget(self.open_btn)
        controls.addWidget(self.add_btn)
        controls.addWidget(self.replace_btn)
        controls.addWidget(self.delete_btn)
        controls.addWidget(self.edit_startup_tools_btn)
        controls.addWidget(self.set_default_btn)
        controls.addWidget(self.clear_default_btn)
        controls.addStretch(1)
        layout.addLayout(controls)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        close_btn = buttons.button(QDialogButtonBox.Close)
        if close_btn is not None:
            close_btn.clicked.connect(self.accept)
        layout.addWidget(buttons)

        self.profile_list.itemSelectionChanged.connect(self._update_buttons)
        self.profile_list.itemDoubleClicked.connect(lambda _item: self._emit_open_selected())
        self.open_btn.clicked.connect(self._emit_open_selected)
        self.add_btn.clicked.connect(self.add_profile_requested.emit)
        self.replace_btn.clicked.connect(self._emit_replace_selected)
        self.delete_btn.clicked.connect(self._emit_delete_selected)
        self.edit_startup_tools_btn.clicked.connect(self._emit_edit_startup_tools_selected)
        self.set_default_btn.clicked.connect(self._emit_set_default_selected)
        self.clear_default_btn.clicked.connect(self.clear_default_profile_requested.emit)
        self._update_buttons()

    def set_profiles(self, profiles: list[dict[str, object]], *, default_profile_id: str) -> None:
        selected_profile_id = self._selected_profile_id()
        self._profiles_by_id.clear()
        self._default_profile_id = default_profile_id.strip()
        self.profile_list.clear()
        for profile in profiles:
            profile_id = str(profile.get("id", "")).strip()
            name = str(profile.get("name", "")).strip()
            if not profile_id or not name:
                continue
            label = f"{name} (default)" if profile_id == self._default_profile_id else name
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, profile_id)
            self.profile_list.addItem(item)
            self._profiles_by_id[profile_id] = dict(profile)
        if selected_profile_id:
            for row in range(self.profile_list.count()):
                item = self.profile_list.item(row)
                if item is None:
                    continue
                value = item.data(Qt.UserRole)
                if isinstance(value, str) and value.strip() == selected_profile_id:
                    self.profile_list.setCurrentRow(row)
                    break
        if self.profile_list.count() > 0 and self.profile_list.currentRow() < 0:
            self.profile_list.setCurrentRow(0)
        self._update_buttons()
        self._update_startup_tools_summary()

    def _selected_profile_id(self) -> str | None:
        item = self.profile_list.currentItem()
        if item is None:
            return None
        value = item.data(Qt.UserRole)
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    def _emit_open_selected(self) -> None:
        profile_id = self._selected_profile_id()
        if not profile_id:
            return
        self.open_profile_requested.emit(profile_id)

    def _emit_replace_selected(self) -> None:
        profile_id = self._selected_profile_id()
        if not profile_id:
            return
        self.replace_profile_requested.emit(profile_id)

    def _emit_delete_selected(self) -> None:
        profile_id = self._selected_profile_id()
        if not profile_id:
            return
        self.delete_profile_requested.emit(profile_id)

    def _emit_edit_startup_tools_selected(self) -> None:
        profile_id = self._selected_profile_id()
        if not profile_id:
            return
        self.edit_startup_tools_requested.emit(profile_id)

    def _emit_set_default_selected(self) -> None:
        profile_id = self._selected_profile_id()
        if not profile_id:
            return
        self.set_default_profile_requested.emit(profile_id)

    def _update_buttons(self) -> None:
        profile_id = self._selected_profile_id()
        has_selection = profile_id is not None
        has_profiles = self.profile_list.count() > 0
        self.open_btn.setEnabled(has_selection)
        self.replace_btn.setEnabled(has_selection)
        self.delete_btn.setEnabled(has_selection)
        self.edit_startup_tools_btn.setEnabled(has_selection)
        self.set_default_btn.setEnabled(has_selection and profile_id != self._default_profile_id)
        self.clear_default_btn.setEnabled(bool(self._default_profile_id) and has_profiles)
        self._update_startup_tools_summary()

    def _update_startup_tools_summary(self) -> None:
        profile_id = self._selected_profile_id()
        if not profile_id:
            self.startup_tools_summary.setText("Start tools on load: None")
            return
        profile = self._profiles_by_id.get(profile_id, {})
        tool_keys = normalize_profile_startup_tool_keys(profile.get("startup_tools", []))
        labels = [TOOL_REGISTRY_BY_KEY[key].label for key in tool_keys if key in TOOL_REGISTRY_BY_KEY]
        summary = ", ".join(labels) if labels else "None"
        self.startup_tools_summary.setText(f"Start tools on load: {summary}")


class FastCommandManagerDialog(QDialog):
    add_command_requested = Signal()
    edit_command_requested = Signal(str)
    rename_command_requested = Signal(str)
    delete_command_requested = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Fast Commands Manager")
        self.resize(660, 420)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Saved fast commands:"))

        self.command_list = QListWidget()
        self.command_list.setSelectionMode(QAbstractItemView.SingleSelection)
        layout.addWidget(self.command_list, 1)

        controls = QHBoxLayout()
        self.add_btn = QPushButton("Add")
        self.edit_btn = QPushButton("Edit Command")
        self.rename_btn = QPushButton("Rename")
        self.delete_btn = QPushButton("Delete")
        controls.addWidget(self.add_btn)
        controls.addWidget(self.edit_btn)
        controls.addWidget(self.rename_btn)
        controls.addWidget(self.delete_btn)
        controls.addStretch(1)
        layout.addLayout(controls)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        close_btn = buttons.button(QDialogButtonBox.Close)
        if close_btn is not None:
            close_btn.clicked.connect(self.accept)
        layout.addWidget(buttons)

        self.command_list.itemSelectionChanged.connect(self._update_buttons)
        self.command_list.itemDoubleClicked.connect(lambda _item: self._emit_edit_selected())
        self.add_btn.clicked.connect(self.add_command_requested.emit)
        self.edit_btn.clicked.connect(self._emit_edit_selected)
        self.rename_btn.clicked.connect(self._emit_rename_selected)
        self.delete_btn.clicked.connect(self._emit_delete_selected)
        self._update_buttons()

    def set_commands(self, commands: list[tuple[str, str, str]]) -> None:
        self.command_list.clear()
        for command_id, name, command in commands:
            first_line = command.strip().splitlines()[0] if command.strip() else ""
            if len(first_line) > 56:
                first_line = f"{first_line[:53]}..."
            label = f"{name} - {first_line}" if first_line else name
            item = QListWidgetItem(label)
            item.setToolTip(command)
            item.setData(Qt.UserRole, command_id)
            self.command_list.addItem(item)
        if self.command_list.count() > 0 and self.command_list.currentRow() < 0:
            self.command_list.setCurrentRow(0)
        self._update_buttons()

    def _selected_command_id(self) -> str | None:
        item = self.command_list.currentItem()
        if item is None:
            return None
        value = item.data(Qt.UserRole)
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    def _emit_edit_selected(self) -> None:
        command_id = self._selected_command_id()
        if command_id:
            self.edit_command_requested.emit(command_id)

    def _emit_rename_selected(self) -> None:
        command_id = self._selected_command_id()
        if command_id:
            self.rename_command_requested.emit(command_id)

    def _emit_delete_selected(self) -> None:
        command_id = self._selected_command_id()
        if command_id:
            self.delete_command_requested.emit(command_id)

    def _update_buttons(self) -> None:
        has_selection = self._selected_command_id() is not None
        self.edit_btn.setEnabled(has_selection)
        self.rename_btn.setEnabled(has_selection)
        self.delete_btn.setEnabled(has_selection)


class DetachedTabWindow(QMainWindow):
    def __init__(self, owner: "MainWindow", parent=None) -> None:
        super().__init__(parent)
        self._owner = owner
        self._allow_owner_close = False
        self._pending_window_placement: _WindowPlacement | None = None
        self.setWindowTitle("Detached Session")
        self.resize(980, 680)
        self.setWindowFlag(Qt.WindowType.Tool, False)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, False)
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.setWindowModality(Qt.NonModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

    def prepare_for_owner_close(self) -> None:
        self._allow_owner_close = True

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        _apply_pending_window_placement(self)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self._allow_owner_close:
            super().closeEvent(event)
            return
        if not self._owner._on_detached_tab_window_close_requested(self):
            event.ignore()
            return
        self._allow_owner_close = True
        super().closeEvent(event)


class SessionListWindow(QMainWindow):
    def __init__(self, owner: "MainWindow", parent=None) -> None:
        super().__init__(parent)
        self._owner = owner
        self._allow_owner_close = False
        self._pending_window_placement: _WindowPlacement | None = None
        self.setWindowTitle("Session List - SnakeSh")
        self.resize(420, 720)
        self.setWindowFlag(Qt.WindowType.Tool, False)
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setWindowFlag(Qt.WindowType.WindowMinimizeButtonHint, True)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, True)
        self.setWindowModality(Qt.NonModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

    def prepare_for_owner_close(self) -> None:
        self._allow_owner_close = True

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        _apply_pending_window_placement(self)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self._allow_owner_close:
            super().closeEvent(event)
            return
        if not self._owner._on_session_list_window_close_requested(self):
            event.ignore()
            return
        self._allow_owner_close = True
        super().closeEvent(event)


_TAB_DRAG_MIME = "application/x-snakesh-tab"


class WorkspaceTabBar(QTabBar):
    def __init__(self, owner: "MainWindow", host: "WorkspaceTabWidget", parent=None) -> None:
        super().__init__(parent)
        self._owner = owner
        self._host = host
        self._press_pos = None
        self.setAcceptDrops(True)
        self.setMovable(False)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

    def _show_context_menu(self, position) -> None:
        index = self.tabAt(position)
        if index < 0:
            return
        self._owner._set_active_tab_host(self._host)
        self._owner._show_tab_context_menu(self._host, index, self.mapToGlobal(position))

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._press_pos = event.position().toPoint()
            self._owner._set_active_tab_host(self._host)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if (
            self._press_pos is not None
            and event.buttons() & Qt.LeftButton
            and (event.position().toPoint() - self._press_pos).manhattanLength() >= QApplication.startDragDistance()
        ):
            index = self.tabAt(self._press_pos)
            self._press_pos = None
            if index < 0:
                return
            if not self._owner._begin_tab_drag(self._host, index):
                return
            drag = QDrag(self)
            mime = QMimeData()
            mime.setData(_TAB_DRAG_MIME, b"1")
            drag.setMimeData(mime)
            try:
                drag.exec(Qt.MoveAction)
            finally:
                self._owner._end_tab_drag()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._press_pos = None
        super().mouseReleaseEvent(event)

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasFormat(_TAB_DRAG_MIME) and self._owner._has_tab_drag():
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasFormat(_TAB_DRAG_MIME) and self._owner._has_tab_drag():
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        if not (event.mimeData().hasFormat(_TAB_DRAG_MIME) and self._owner._has_tab_drag()):
            super().dropEvent(event)
            return
        target_index = self.tabAt(event.position().toPoint())
        if target_index < 0:
            target_index = self.count()
        self._owner._drop_dragged_tab(self._host, target_index)
        event.acceptProposedAction()


class WorkspaceTabWidget(QTabWidget):
    def __init__(self, owner: "MainWindow", parent=None) -> None:
        super().__init__(parent)
        self._owner = owner
        self.setAcceptDrops(True)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._owner._set_active_tab_host(self)
        super().mousePressEvent(event)

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasFormat(_TAB_DRAG_MIME) and self._owner._has_tab_drag():
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasFormat(_TAB_DRAG_MIME) and self._owner._has_tab_drag():
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        if not (event.mimeData().hasFormat(_TAB_DRAG_MIME) and self._owner._has_tab_drag()):
            super().dropEvent(event)
            return
        tab_bar = self.tabBar()
        local_on_bar = tab_bar.mapFrom(self, event.position().toPoint())
        target_index = tab_bar.tabAt(local_on_bar)
        if target_index < 0:
            target_index = self.count()
        self._owner._drop_dragged_tab(self, target_index)
        event.acceptProposedAction()


class MainWindow(QMainWindow):
    def __init__(
        self,
        session_service: SessionService,
        settings_service: SettingsService,
        *,
        safe_mode: bool = False,
    ) -> None:
        super().__init__()
        self.setWindowTitle("SnakeSh")
        self.resize(1320, 820)

        self._session_service = session_service
        self._settings_service = settings_service
        self._settings = settings_service.load()
        self._secrets_service = SecretsService(settings_service=self._settings_service)
        self._credential_service = CredentialService(settings_service=self._settings_service)
        self._backup_service = BackupService()
        self._securecrt_codec = SecureCRTCodecService()
        self._third_party_import = ThirdPartyImportService()
        self._x11_service = X11Service()
        self._ssh = SSHClient()
        self._sftp = SFTPClient()
        self._ssh_probe_threads: set[QThread] = set()
        self._ssh_probe_threads_by_id: dict[int, QThread] = {}
        self._ssh_probe_workers_by_id: dict[int, SSHProbeWorker] = {}
        self._ssh_probe_contexts: dict[int, SSHProbeContext] = {}
        self._ssh_probe_watchdogs_by_id: dict[int, QTimer] = {}
        self._legacy_compat_prompted_session_ids: set[str] = set()
        self._next_ssh_probe_id = 1
        self._is_shutting_down = False
        self._safe_mode = safe_mode
        self._main_fullscreen_shortcut: QShortcut | None = None
        self._pending_window_placement: _WindowPlacement | None = None
        self._frameless_fullscreen_active = False
        self._fullscreen_restore_geometry: QByteArray | None = None
        self._fullscreen_restore_placement: _WindowPlacement | None = None
        self._fullscreen_restore_was_maximized = False
        self._tab_hosts: list[WorkspaceTabWidget] = []
        self._active_tab_host: WorkspaceTabWidget | None = None
        self._dragged_tab_widget: QWidget | None = None
        self._dragged_tab_source: WorkspaceTabWidget | None = None
        self._dragged_tab_title: str = ""
        self._next_tab_group_id = 1
        self._next_tab_group_name_id = 1
        self._tab_group_names: dict[str, str] = {}
        self._tool_processes: dict[str, list[subprocess.Popen[bytes]]] = {}
        self._detached_window_by_host: dict[WorkspaceTabWidget, DetachedTabWindow] = {}
        self._detached_host_by_window: dict[DetachedTabWindow, WorkspaceTabWidget] = {}
        self._profile_restore_in_progress = False
        self._profile_restore_pending_remote_starts: list[tuple[RemoteViewerTab, bool]] = []
        self._remote_viewer_start_queue: deque[tuple[RemoteViewerTab, bool]] = deque()
        self._remote_viewer_start_scheduled = False
        self._remote_viewer_start_in_progress = False
        self._last_session_log_cleanup_monotonic = 0.0
        self._last_web_server_log_cleanup_monotonic = 0.0
        self._session_list_mode = self._normalized_session_list_mode(self._settings.session_list_visibility_mode)
        self._session_list_last_width = 370
        self._session_list_docked_splitter_b64 = self._settings.main_window_splitter_b64.strip()
        self._session_list_window: SessionListWindow | None = None
        self._next_status_progress_id = 1
        self._active_status_progress: StatusProgressOperation | None = None
        self._connection_load_progress: StatusProgressOperation | None = None
        self._connection_load_completion_message = ""
        self._applying_tab_styles = False
        self._suspend_focus_tracking_count = 0
        self._session_list_auto_hide_timer = QTimer(self)
        self._session_list_auto_hide_timer.setInterval(200)
        self._session_list_auto_hide_timer.timeout.connect(self._maybe_auto_hide_session_list)
        self._ui_settings_flush_timer = QTimer(self)
        self._ui_settings_flush_timer.setSingleShot(True)
        self._ui_settings_flush_timer.setInterval(_UI_SETTINGS_SAVE_COALESCE_MS)
        self._ui_settings_flush_timer.timeout.connect(self._flush_ui_settings_save)
        self._ui_settings_save_pending = False
        self._tool_settings_sync_timer = QTimer(self)
        self._tool_settings_sync_timer.setSingleShot(True)
        self._tool_settings_sync_timer.timeout.connect(self._flush_tool_settings_sync)
        self._pending_tool_settings_sync: tuple[AppSettings, bool] | None = None
        self._session_tree_search_cache: dict[str, tuple[tuple[object, ...], str]] = {}

        root = QWidget(self)
        root.setObjectName("mainWindowRoot")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(4)

        self._top_controls_widget = QWidget()
        actions = QHBoxLayout(self._top_controls_widget)
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(6)
        self.local_shell_btn = QPushButton("Local Shell")
        self.session_actions_btn = QPushButton("Session")
        self._session_actions_menu = self._build_session_actions_menu()
        self.session_actions_btn.setMenu(self._session_actions_menu)
        self.connect_btn = QPushButton("Connect")
        self.settings_btn = QPushButton("Settings")
        self.profiles_btn = QPushButton("Profiles")
        self._profiles_menu = self._build_profiles_menu()
        self.profiles_btn.setMenu(self._profiles_menu)
        self.fast_commands_btn = QPushButton("Fast Commands")
        self._fast_commands_menu = self._build_fast_commands_menu()
        self.fast_commands_btn.setMenu(self._fast_commands_menu)
        self.tools_btn = QPushButton("Tools")
        self._tools_menu = self._build_tools_menu()
        self.tools_btn.setMenu(self._tools_menu)
        actions.addWidget(self.local_shell_btn)
        actions.addWidget(self.session_actions_btn)
        actions.addWidget(self.connect_btn)
        actions.addWidget(self.settings_btn)
        actions.addWidget(self.profiles_btn)
        actions.addWidget(self.fast_commands_btn)
        actions.addWidget(self.tools_btn)
        actions.addStretch(1)
        outer.addWidget(self._top_controls_widget)

        self._workspace_row = QHBoxLayout()
        self._workspace_row.setContentsMargins(0, 0, 0, 0)
        self._workspace_row.setSpacing(0)
        self._main_splitter = MainWorkspaceSplitter(Qt.Horizontal)
        self._main_splitter.setChildrenCollapsible(False)
        self._main_splitter.setHandleWidth(14)
        self._main_splitter.splitterMoved.connect(self._on_main_splitter_moved)
        self._workspace_row.addWidget(self._main_splitter, 1)
        outer.addLayout(self._workspace_row, 1)
        self._session_list_placeholder = QWidget()
        self._session_list_placeholder.setMinimumWidth(0)
        self._session_list_placeholder.setMaximumWidth(0)
        self._session_list_placeholder.hide()

        left = QWidget()
        left.setMinimumWidth(0)
        self._session_list_panel = left
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)

        search_row = QHBoxLayout()
        search_row.setContentsMargins(0, 0, 0, 0)
        search_row.setSpacing(6)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search sessions...")
        search_row.addWidget(self.search_input, 1)
        self._session_list_mode_btn = QPushButton("Show")
        self._session_list_mode_btn.setCursor(Qt.PointingHandCursor)
        self._session_list_mode_btn.setFixedWidth(88)
        self._session_list_mode_btn.setFixedHeight(max(30, self.search_input.sizeHint().height()))
        self._session_list_mode_menu = self._build_session_list_mode_menu()
        self._session_list_mode_btn.setMenu(self._session_list_mode_menu)
        search_row.addWidget(self._session_list_mode_btn, 0)
        left_layout.addLayout(search_row)

        self.session_tree = SessionTreeWidget()
        self.session_tree.itemSelectionChanged.connect(self._update_details)
        self.session_tree.itemDoubleClicked.connect(self._on_session_tree_double_clicked)
        self._syncing_tree_expansion_state = False
        self.session_tree.itemExpanded.connect(self._on_session_tree_item_expanded)
        self.session_tree.itemCollapsed.connect(self._on_session_tree_item_collapsed)
        self.session_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.session_tree.customContextMenuRequested.connect(self._show_session_tree_menu)
        self.session_tree.session_drop_requested.connect(self._move_sessions_to_folder)
        left_layout.addWidget(self.session_tree, 1)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)

        self._tab_workspace = QWidget()
        self._tab_workspace_layout = QVBoxLayout(self._tab_workspace)
        self._tab_workspace_layout.setContentsMargins(0, 0, 0, 0)
        self._tab_workspace_layout.setSpacing(0)
        self.tabs = self._create_tab_host(primary=True)
        self._tab_workspace_layout.addWidget(self.tabs, 1)
        right_layout.addWidget(self._tab_workspace, 1)

        self.details = SessionDetailsTab()
        self._apply_session_details_style()
        self.tabs.addTab(self.details, "Session Details")
        self._sync_host_tab_close_buttons(self.tabs)
        self._apply_tab_styles()

        self._main_splitter.addWidget(left)
        self._main_splitter.addWidget(right)
        self._main_splitter.setSizes([370, 950])
        self._update_session_list_splitter_handle_tooltip()

        self._bottom_command_widget = QWidget()
        self._bottom_command_widget.setObjectName("footerCommandBar")
        command_row = QHBoxLayout(self._bottom_command_widget)
        command_row.setContentsMargins(0, 0, 0, 0)
        command_row.setSpacing(4)
        self.command_bar_input = CommandInputLineEdit()
        self.command_bar_input.setPlaceholderText("Type command and press Enter...")
        self.command_bar_run_btn = QPushButton("Run")
        self.command_bar_scrollback_btn = QPushButton("Scrollback")
        command_row.addWidget(self.command_bar_input, 1)
        command_row.addWidget(self.command_bar_run_btn)
        command_row.addWidget(self.command_bar_scrollback_btn)
        outer.addWidget(self._bottom_command_widget)

        status = QStatusBar()
        status.setObjectName("mainStatusBar")
        self.setStatusBar(status)
        self._status_progress_widget = StatusProgressWidget(self)
        self._status_progress_widget.cancel_requested.connect(self._cancel_active_status_progress)
        self.statusBar().addPermanentWidget(self._status_progress_widget, 1)
        self.statusBar().showMessage("Ready")

        self.local_shell_btn.clicked.connect(self._open_local_shell_tab)
        self.connect_btn.clicked.connect(self._connect_current)
        self.settings_btn.clicked.connect(self._open_settings)
        self.search_input.textChanged.connect(self._refresh_tree)
        self.command_bar_input.returnPressed.connect(self._run_workspace_command)
        self.command_bar_input.paste_requested.connect(self._handle_workspace_command_paste)
        self.command_bar_run_btn.clicked.connect(self._run_workspace_command)
        self.command_bar_scrollback_btn.clicked.connect(self._show_active_terminal_scrollback)

        app = QApplication.instance()
        if app is not None:
            app.focusChanged.connect(self._on_focus_changed)

        self._update_main_fullscreen_shortcut()
        self._restore_saved_window_geometry()
        self._restore_saved_main_splitter_state()
        self._remember_session_list_width_from_splitter()
        self._sync_session_list_mode_actions()
        self._apply_session_list_mode(self._session_list_mode, persist=False)
        self._sync_main_fullscreen_control_visibility()
        self._refresh_tree()
        QTimer.singleShot(0, self._handle_workspace_startup_restore)
        QTimer.singleShot(0, self._maybe_prune_session_logs)
        QTimer.singleShot(0, self._maybe_prune_web_server_logs)
        QTimer.singleShot(0, self._offer_linux_desktop_integration_on_startup)

    def _create_tab_host(self, *, primary: bool = False, parent: QWidget | None = None) -> WorkspaceTabWidget:
        if parent is None:
            parent = self._tab_workspace
        host = WorkspaceTabWidget(owner=self, parent=parent)
        host.setProperty("is_primary_host", primary)
        host.setTabBar(WorkspaceTabBar(owner=self, host=host, parent=host))
        host.setTabsClosable(True)
        host.tabCloseRequested.connect(lambda index, h=host: self._request_close_tab_in_host(h, index))
        host.currentChanged.connect(lambda _index, h=host: self._on_host_current_changed(h))
        self._tab_hosts.append(host)
        self._sync_host_tab_close_buttons(host)
        if self._active_tab_host is None:
            self._set_active_tab_host(host)
        else:
            self._apply_tab_styles()
        return host

    def _schedule_ui_settings_save(self) -> None:
        self._ui_settings_save_pending = True
        self._ui_settings_flush_timer.start()

    def _flush_ui_settings_save(self) -> None:
        if self._ui_settings_flush_timer.isActive():
            self._ui_settings_flush_timer.stop()
        if not self._ui_settings_save_pending:
            return
        self._ui_settings_save_pending = False
        self._settings_service.save(self._settings)

    def _on_host_current_changed(self, host: WorkspaceTabWidget) -> None:
        if host.currentIndex() >= 0:
            self._set_active_tab_host(host)

    @contextmanager
    def _suspend_focus_tracking(self):
        self._suspend_focus_tracking_count += 1
        try:
            yield
        finally:
            self._suspend_focus_tracking_count = max(0, self._suspend_focus_tracking_count - 1)

    def _set_active_tab_host(self, host: WorkspaceTabWidget) -> None:
        if host not in self._tab_hosts:
            return
        if self._applying_tab_styles:
            return
        if self._active_tab_host == host:
            return
        self._active_tab_host = host
        self._apply_tab_styles()

    def _on_focus_changed(self, _old, now) -> None:
        if self._applying_tab_styles:
            return
        if self._suspend_focus_tracking_count > 0:
            return
        if not isinstance(now, QWidget):
            return
        host = self._host_for_widget(now)
        if host is None:
            return
        self._set_active_tab_host(host)

    @staticmethod
    def _normalize_main_window_shortcut_text(raw: str) -> str:
        text = raw.strip()
        if not text:
            return ""
        sequence = QKeySequence.fromString(text, QKeySequence.PortableText)
        if sequence.isEmpty():
            sequence = QKeySequence.fromString(text, QKeySequence.NativeText)
        if sequence.isEmpty():
            return ""
        try:
            first = sequence[0]
        except Exception:
            return sequence.toString(QKeySequence.PortableText)
        return QKeySequence(first).toString(QKeySequence.PortableText)

    def _update_main_fullscreen_shortcut(self) -> None:
        existing = self._main_fullscreen_shortcut
        if existing is not None:
            try:
                existing.activated.disconnect(self._toggle_main_window_fullscreen)
            except Exception:
                pass
            existing.setEnabled(False)
            existing.deleteLater()
            self._main_fullscreen_shortcut = None

        normalized = self._normalize_main_window_shortcut_text(self._settings.main_window_fullscreen_shortcut)
        if not normalized:
            normalized = self._normalize_main_window_shortcut_text(AppSettings.defaults().main_window_fullscreen_shortcut)
        self._settings.main_window_fullscreen_shortcut = normalized
        if not normalized:
            return
        shortcut = QShortcut(QKeySequence.fromString(normalized, QKeySequence.PortableText), self)
        shortcut.setAutoRepeat(False)
        shortcut.activated.connect(self._toggle_main_window_fullscreen)
        self._main_fullscreen_shortcut = shortcut

    def _toggle_main_window_fullscreen(self) -> None:
        if self._frameless_fullscreen_active:
            self._exit_main_window_fullscreen()
            return
        self._enter_main_window_fullscreen()

    def _target_screen_for_main_fullscreen(self):
        handle = self.windowHandle()
        if handle is not None:
            screen = handle.screen()
            if screen is not None:
                return screen
        screen = self.screen()
        if screen is not None:
            return screen
        try:
            frame = self.frameGeometry()
        except RuntimeError:
            frame = QRect()
        if not frame.isNull():
            screen = QApplication.screenAt(frame.center())
            if screen is not None:
                return screen
        return QApplication.primaryScreen()

    def _fullscreen_target_geometry(self) -> QRect:
        screen = self._target_screen_for_main_fullscreen()
        if screen is None:
            return self.geometry()
        geometry = screen.geometry()
        if geometry.isValid():
            return geometry
        return self.geometry()

    def _apply_main_window_fullscreen_geometry(self, target_geometry: QRect) -> None:
        if not self._frameless_fullscreen_active or not target_geometry.isValid():
            return
        self.setGeometry(target_geometry)
        self.move(target_geometry.topLeft())
        self.resize(target_geometry.size())

    def _sync_main_fullscreen_control_visibility(self) -> None:
        hide_controls = self._frameless_fullscreen_active and self._settings.main_window_hide_controls_in_fullscreen
        self._top_controls_widget.setVisible(not hide_controls)
        self._bottom_command_widget.setVisible(not hide_controls)

    def _enter_main_window_fullscreen(self) -> None:
        if self._frameless_fullscreen_active:
            return
        self._fullscreen_restore_placement = _capture_window_placement(self)
        self._fullscreen_restore_geometry = self.saveGeometry()
        self._fullscreen_restore_was_maximized = self.isMaximized()
        self._frameless_fullscreen_active = True
        target_screen = self._target_screen_for_main_fullscreen()
        target_geometry = self._fullscreen_target_geometry()
        self.hide()
        self.setWindowState(Qt.WindowState.WindowNoState)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.show()
        if target_screen is not None:
            handle = self.windowHandle()
            if handle is not None:
                handle.setScreen(target_screen)
        self._sync_main_fullscreen_control_visibility()
        self._apply_main_window_fullscreen_geometry(target_geometry)
        QTimer.singleShot(
            0,
            lambda geometry=QRect(target_geometry): self._apply_main_window_fullscreen_geometry(geometry),
        )
        self.raise_()
        self.activateWindow()

    def _exit_main_window_fullscreen(self) -> None:
        if not self._frameless_fullscreen_active:
            return
        restore_geometry = self._fullscreen_restore_geometry
        was_maximized = self._fullscreen_restore_was_maximized
        self._frameless_fullscreen_active = False
        self.hide()
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, False)
        if was_maximized:
            self.showMaximized()
        else:
            self.showNormal()
            if restore_geometry is not None and not restore_geometry.isEmpty():
                self.restoreGeometry(restore_geometry)
        self._fullscreen_restore_placement = None
        self._sync_main_fullscreen_control_visibility()
        self.raise_()
        self.activateWindow()

    def _host_for_widget(self, widget: QWidget) -> WorkspaceTabWidget | None:
        current: QWidget | None = widget
        while current is not None:
            if isinstance(current, WorkspaceTabWidget):
                return current
            current = current.parentWidget()
        return None

    def _session_tab_locations(self) -> list[tuple[WorkspaceTabWidget, int, QWidget]]:
        locations: list[tuple[WorkspaceTabWidget, int, QWidget]] = []
        for host in self._tab_hosts:
            for index in range(host.count()):
                widget = host.widget(index)
                if widget is None:
                    continue
                if widget.property("session_id"):
                    locations.append((host, index, widget))
        return locations

    def _session_tabs_for_grouping(self) -> list[tuple[QWidget, str]]:
        entries: list[tuple[QWidget, str]] = []
        for host, index, widget in self._session_tab_locations():
            if not isinstance(widget, TerminalTab):
                continue
            entries.append((widget, host.tabText(index)))
        return entries

    def _find_widget_location(self, target: QWidget) -> tuple[WorkspaceTabWidget, int] | None:
        for host in self._tab_hosts:
            index = host.indexOf(target)
            if index >= 0:
                return host, index
        return None

    def _base_tab_title(self, widget: QWidget) -> str:
        base = widget.property("base_tab_title")
        if isinstance(base, str) and base.strip():
            return base
        location = self._find_widget_location(widget)
        if location is None:
            return ""
        host, index = location
        return host.tabText(index).replace("*", "").strip()

    def _set_base_tab_title(self, widget: QWidget, title: str) -> None:
        widget.setProperty("base_tab_title", title)

    def _tab_title_root(self, widget: QWidget) -> str:
        custom = widget.property("tab_title_custom")
        if isinstance(custom, str) and custom.strip():
            return custom.strip()
        root = widget.property("tab_title_root")
        if isinstance(root, str) and root.strip():
            return root.strip()
        return self._base_tab_title(widget)

    def _session_instance_group_key(self, widget: QWidget) -> tuple[str, str]:
        host_key = widget.property("session_host_key")
        if not isinstance(host_key, str):
            host_key = ""
        kind = widget.property("session_kind")
        if not isinstance(kind, str):
            kind = ""
        return host_key.strip().lower(), kind.strip().upper()

    def _refresh_session_instance_titles(self) -> None:
        groups: dict[tuple[str, str], list[QWidget]] = {}
        for _host, _index, widget in self._session_tab_locations():
            groups.setdefault(self._session_instance_group_key(widget), []).append(widget)

        for widgets in groups.values():
            total = len(widgets)
            for ordinal, widget in enumerate(widgets, start=1):
                root = self._tab_title_root(widget)
                numbered = f"{root}({ordinal})" if total > 1 else root
                self._set_base_tab_title(widget, numbered)

        self._refresh_all_tab_titles()
        for host in list(self._detached_window_by_host.keys()):
            self._refresh_detached_window_title(host)

    def _tab_group_id(self, widget: QWidget) -> str | None:
        value = widget.property("tab_group_id")
        if isinstance(value, str) and value.strip():
            return value
        return None

    def _set_tab_group_id(self, widget: QWidget, group_id: str | None) -> None:
        widget.setProperty("tab_group_id", group_id or "")

    def _group_members(self, group_id: str) -> list[QWidget]:
        members: list[QWidget] = []
        for _host, _index, widget in self._session_tab_locations():
            if self._tab_group_id(widget) == group_id:
                members.append(widget)
        return members

    def _group_name(self, group_id: str) -> str:
        current = self._tab_group_names.get(group_id, "").strip()
        if current:
            return current
        fallback = f"Group {self._next_tab_group_name_id}"
        self._next_tab_group_name_id += 1
        self._tab_group_names[group_id] = fallback
        return fallback

    def _set_group_name(self, group_id: str, name: str) -> None:
        cleaned = name.strip()
        if not cleaned:
            return
        self._tab_group_names[group_id] = cleaned

    def _normalize_tab_groups(self) -> None:
        grouped: dict[str, list[QWidget]] = {}
        for _host, _index, widget in self._session_tab_locations():
            group_id = self._tab_group_id(widget)
            if not group_id:
                continue
            grouped.setdefault(group_id, []).append(widget)
        for group_id, members in grouped.items():
            if len(members) >= 2:
                continue
            for member in members:
                self._set_tab_group_id(member, None)
        valid_groups = {group_id for group_id, members in grouped.items() if len(members) >= 2}
        self._tab_group_names = {group_id: name for group_id, name in self._tab_group_names.items() if group_id in valid_groups}

    def _group_manager_entries(self) -> list[tuple[str, str, list[str]]]:
        grouped: dict[str, list[str]] = {}
        for host, index, widget in self._session_tab_locations():
            group_id = self._tab_group_id(widget)
            if not group_id:
                continue
            grouped.setdefault(group_id, []).append(host.tabText(index).replace("*", "").strip())

        entries: list[tuple[str, str, list[str]]] = []
        for group_id, members in grouped.items():
            if len(members) < 2:
                continue
            entries.append((group_id, self._group_name(group_id), sorted(members)))
        entries.sort(key=lambda item: item[1].lower())
        return entries

    def _display_title_for_widget(self, widget: QWidget) -> str:
        base = self._base_tab_title(widget)
        if isinstance(widget, TerminalTab) and widget.is_session_closed():
            base = f"{base} (closed)"
        if isinstance(widget, RemoteViewerTab) and bool(widget.property("remote_viewer_closed")):
            base = f"{base} (closed)"
        if self._tab_group_id(widget):
            return f"{base} *"
        return base

    def _refresh_tab_title(self, widget: QWidget) -> None:
        location = self._find_widget_location(widget)
        if location is None:
            return
        host, index = location
        host.setTabText(index, self._display_title_for_widget(widget))
        if host in self._detached_window_by_host:
            self._refresh_detached_window_title(host)

    def _refresh_all_tab_titles(self) -> None:
        for _host, _index, widget in self._session_tab_locations():
            self._refresh_tab_title(widget)

    def _show_tab_context_menu(self, host: WorkspaceTabWidget, index: int, global_pos) -> None:
        if index < 0:
            return
        widget = host.widget(index)
        if widget is None or not widget.property("session_id"):
            return
        session_for_tab = self._session_for_tab(widget)
        kind = str(widget.property("session_kind") or "").strip().upper()
        active_terminal_entries = self._active_terminal_tab_entries()

        menu = QMenu(self)
        group_action = menu.addAction("Group Tabs...")
        manage_group_action = menu.addAction("Manage Groups...")
        ungroup_action = menu.addAction("Ungroup Tabs")
        current_group_id = self._tab_group_id(widget)
        manage_group_action.setEnabled(bool(self._group_manager_entries()))
        ungroup_action.setEnabled(current_group_id is not None)
        menu.addSeparator()
        split_vertical_action = menu.addAction("Split Vertical (Side by Side)")
        split_horizontal_action = menu.addAction("Split Horizontal (Top/Bottom)")
        menu.addSeparator()
        rename_tab_action = menu.addAction("Rename Tab...")
        detach_tab_action = None
        reattach_tab_action = None
        if self._is_detachable_session_widget(widget):
            if bool(host.property("is_detached_host")):
                reattach_tab_action = menu.addAction("Reattach Tab")
            else:
                detach_tab_action = menu.addAction("Detach Tab")
        disconnect_action = None
        close_action = menu.addAction("Close Tab")
        disconnect_all_tabs_action = menu.addAction("Disconnect All Tabs")
        disconnect_selected_tabs_action = menu.addAction("Disconnect Selected Tabs...")
        has_active_terminal_tabs = bool(active_terminal_entries)
        disconnect_all_tabs_action.setEnabled(has_active_terminal_tabs)
        disconnect_selected_tabs_action.setEnabled(has_active_terminal_tabs)
        reconnect_action = None
        open_sftp_action = None
        start_logging_action = None
        stop_logging_action = None
        detach_remote_action = None
        attach_remote_action = None
        if isinstance(widget, TerminalTab):
            if self._widget_has_active_session(widget):
                disconnect_action = menu.addAction("Disconnect")
            if widget.is_session_closed() and widget.can_reconnect_session():
                reconnect_action = menu.addAction("Reconnect Session")
            if session_for_tab is not None and kind == "SSH":
                open_sftp_action = menu.addAction("Open SFTP Tab")
            menu.addSeparator()
            if widget.is_logging_enabled():
                stop_logging_action = menu.addAction("Stop Logging")
            else:
                start_logging_action = menu.addAction("Log Session to File...")
        elif isinstance(widget, RemoteViewerTab):
            if session_for_tab is not None and bool(widget.property("remote_viewer_closed")):
                reconnect_action = menu.addAction("Reconnect Session")
            menu.addSeparator()
            if widget.is_linux_rdp_viewer():
                if widget.is_detached():
                    attach_remote_action = menu.addAction("Attach Viewer to Tab")
                    attach_remote_action.setEnabled(widget.supports_embedded_mode())
                else:
                    detach_remote_action = menu.addAction("Detach Viewer")

        chosen = self._exec_menu(menu, global_pos)
        if chosen is group_action:
            self._show_group_tabs_dialog(widget)
        elif chosen is manage_group_action:
            self._open_group_manager(current_group_id)
        elif chosen is ungroup_action:
            self._ungroup_tab(widget)
        elif chosen is split_vertical_action:
            self._split_tab(host, index, Qt.Horizontal)
        elif chosen is split_horizontal_action:
            self._split_tab(host, index, Qt.Vertical)
        elif chosen is rename_tab_action:
            self._rename_tab_for_life_of_session(widget)
        elif detach_tab_action is not None and chosen is detach_tab_action:
            self._detach_session_tab(widget, host, index)
        elif reattach_tab_action is not None and chosen is reattach_tab_action:
            self._reattach_session_tab(widget, host, index)
        elif disconnect_action is not None and chosen is disconnect_action and isinstance(widget, TerminalTab):
            self._disconnect_terminal_tab(widget)
        elif chosen is close_action:
            self._request_close_tab_in_host(host, index)
        elif chosen is disconnect_all_tabs_action and active_terminal_entries:
            self._disconnect_terminal_tab_entries(
                active_terminal_entries,
                close_after_disconnect=False,
                allow_close_after_disconnect_choice=True,
            )
        elif chosen is disconnect_selected_tabs_action and active_terminal_entries:
            self._show_bulk_disconnect_tabs_dialog(active_terminal_entries, widget)
        elif reconnect_action is not None and chosen is reconnect_action:
            self._reconnect_session_tab(widget, host, index)
        elif open_sftp_action is not None and chosen is open_sftp_action and session_for_tab is not None:
            self._open_sftp_tab(session_for_tab)
        elif chosen is not None and chosen is start_logging_action and isinstance(widget, TerminalTab):
            self._start_terminal_logging_for_tab(widget)
        elif chosen is not None and chosen is stop_logging_action and isinstance(widget, TerminalTab):
            self._stop_terminal_logging_for_tab(widget)
        elif chosen is not None and chosen is detach_remote_action and isinstance(widget, RemoteViewerTab):
            ok, message = widget.detach_viewer()
            if ok:
                self.statusBar().showMessage(message, 7000)
            else:
                QMessageBox.critical(self, "Connection Error", message)
        elif chosen is not None and chosen is attach_remote_action and isinstance(widget, RemoteViewerTab):
            ok, message = widget.attach_viewer()
            if ok:
                self.statusBar().showMessage(message, 7000)
            else:
                QMessageBox.critical(self, "Connection Error", message)
        menu.deleteLater()

    def _active_terminal_tab_entries(self) -> list[_ActiveTerminalTabEntry]:
        entries: list[_ActiveTerminalTabEntry] = []
        for host, index, widget in self._session_tab_locations():
            if not isinstance(widget, TerminalTab):
                continue
            kind = str(widget.property("session_kind") or "").strip().upper()
            if kind not in _BULK_DISCONNECT_TERMINAL_KINDS:
                continue
            if not self._widget_has_active_session(widget):
                continue
            label = self._display_title_for_widget(widget).strip() or "Session"
            entries.append(_ActiveTerminalTabEntry(host=host, index=index, tab=widget, label=label))
        return entries

    def _show_bulk_disconnect_tabs_dialog(
        self,
        entries: list[_ActiveTerminalTabEntry],
        anchor_widget: QWidget,
    ) -> None:
        preselected = {id(anchor_widget)} if any(entry.tab is anchor_widget for entry in entries) else set()
        dialog = BulkDisconnectTabsDialog(entries, preselected, self)
        try:
            if not dialog.exec():
                return
            selected_tabs = dialog.selected_tabs()
            if not selected_tabs:
                self.statusBar().showMessage("No tabs selected to disconnect.", 5000)
                return
            selected_ids = {id(tab) for tab in selected_tabs}
            selected_entries = [entry for entry in entries if id(entry.tab) in selected_ids]
            self._disconnect_terminal_tab_entries(
                selected_entries,
                close_after_disconnect=dialog.close_after_disconnect(),
            )
        finally:
            dialog.deleteLater()

    def _confirm_bulk_terminal_disconnect(
        self,
        entries: list[_ActiveTerminalTabEntry],
        *,
        close_after_disconnect: bool,
        allow_close_after_disconnect_choice: bool = False,
    ) -> tuple[bool, bool]:
        if not entries:
            return False, close_after_disconnect
        preview = "\n".join(entry.label for entry in entries[:6])
        if len(entries) > 6:
            preview += f"\n... and {len(entries) - 6} more."
        noun = "tab" if len(entries) == 1 else "tabs"
        result_text = (
            "The visible terminal output and scrollback will remain available by default. "
            "Select the checkbox below to close tabs that disconnect successfully."
            if allow_close_after_disconnect_choice
            else (
                "Tabs that disconnect successfully will be closed."
                if close_after_disconnect
                else "The visible terminal output and scrollback will remain available."
            )
        )
        if allow_close_after_disconnect_choice:
            box = QMessageBox(self)
            box.setWindowTitle("Disconnect Tabs")
            box.setIcon(QMessageBox.Question)
            box.setText(f"Disconnect {len(entries)} terminal {noun}?")
            box.setInformativeText(f"{preview}\n\n{result_text}")
            box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
            box.setDefaultButton(QMessageBox.No)
            close_checkbox = QCheckBox("Close tabs after disconnect", box)
            close_checkbox.setChecked(close_after_disconnect)
            box.setCheckBox(close_checkbox)
            answer = box.exec()
            return answer == QMessageBox.Yes, close_checkbox.isChecked()

        answer = QMessageBox.question(
            self,
            "Disconnect Tabs",
            f"Disconnect {len(entries)} terminal {noun}?\n\n{preview}\n\n{result_text}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return answer == QMessageBox.Yes, close_after_disconnect

    def _disconnect_terminal_tab_entries(
        self,
        entries: list[_ActiveTerminalTabEntry],
        *,
        close_after_disconnect: bool,
        allow_close_after_disconnect_choice: bool = False,
    ) -> None:
        if not entries:
            return
        confirmed, close_after_disconnect = self._confirm_bulk_terminal_disconnect(
            entries,
            close_after_disconnect=close_after_disconnect,
            allow_close_after_disconnect_choice=allow_close_after_disconnect_choice,
        )
        if not confirmed:
            return

        disconnected: list[TerminalTab] = []
        failed_count = 0
        for entry in entries:
            tab = entry.tab
            if not self._widget_has_active_session(tab):
                continue
            if tab.disconnect_session():
                disconnected.append(tab)
            else:
                failed_count += 1

        closed_count = 0
        if close_after_disconnect:
            for tab in disconnected:
                location = self._find_widget_location(tab)
                if location is None:
                    continue
                self._close_tab_in_host(location[0], location[1])
                if self._find_widget_location(tab) is None:
                    closed_count += 1

        if close_after_disconnect:
            message = f"Disconnected {len(disconnected)} tab(s); closed {closed_count}."
        else:
            message = f"Disconnected {len(disconnected)} tab(s). Tabs kept open."
        if failed_count:
            message += f" {failed_count} still disconnecting."
        self.statusBar().showMessage(message, 7000)

    def _session_for_tab(self, widget: QWidget) -> Session | None:
        session_id = widget.property("session_id")
        if not isinstance(session_id, str) or not session_id:
            return None
        lookup = getattr(self._session_service, "by_id", None)
        if callable(lookup):
            return lookup(session_id)
        return None

    @staticmethod
    def _widget_has_active_session(widget: QWidget) -> bool:
        if isinstance(widget, TerminalTab):
            return not widget.is_session_closed() and widget.has_active_connection()
        if isinstance(widget, RemoteViewerTab):
            return widget.has_active_connection()
        return False

    def _confirm_terminal_disconnect(self, tab: TerminalTab) -> bool:
        title = self._display_title_for_widget(tab).strip()
        if title.endswith("*"):
            title = title[:-1].strip()
        title = title or "Session"
        answer = QMessageBox.question(
            self,
            "Disconnect Session",
            f"Disconnect {title} and keep this tab open?\n\n"
            "The visible terminal output and scrollback will remain available, and you can reconnect later.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return answer == QMessageBox.Yes

    def _disconnect_terminal_tab(self, tab: TerminalTab) -> None:
        if not tab.has_active_connection() and tab.is_session_closed():
            return
        if not self._confirm_terminal_disconnect(tab):
            return
        title = self._display_title_for_widget(tab).replace("*", "").strip() or "Session"
        disconnected = tab.disconnect_session()
        if disconnected:
            self.statusBar().showMessage(f"Disconnected {title}. Tab kept open.", 7000)
            return
        self.statusBar().showMessage(f"Disconnecting {title}...", 5000)

    def _confirm_active_tab_close(self, widget: QWidget) -> bool:
        if not self._settings.warn_before_closing_active_tab or not self._widget_has_active_session(widget):
            return True
        title = self._display_title_for_widget(widget).strip()
        if title.endswith("*"):
            title = title[:-1].strip()
        title = title or "Session"
        answer = QMessageBox.question(
            self,
            "Close Active Tab",
            f"{title} still has an active session.\n\n"
            "Closing this tab will close the active session.\n\n"
            "Continue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return answer == QMessageBox.Yes

    def _request_close_tab_in_host(self, host: WorkspaceTabWidget, index: int) -> None:
        widget = host.widget(index)
        if widget is None:
            return
        if not self._confirm_active_tab_close(widget):
            return
        self._close_tab_in_host(host, index)

    def _connected_session_tab_labels(self) -> list[str]:
        labels: list[str] = []
        for _host, _index, widget in self._session_tab_locations():
            if not self._widget_has_active_session(widget):
                continue
            label = self._display_title_for_widget(widget).strip()
            if not label:
                session = self._session_for_tab(widget)
                if session is not None:
                    label = (session.name or "").strip() or session.host.strip()
            labels.append(label or "Session")
        return labels

    def _confirm_application_close_with_connected_tabs(self) -> bool:
        labels = self._connected_session_tab_labels()
        if not labels:
            return True
        preview = "\n".join(labels[:6])
        if len(labels) > 6:
            preview += f"\n... and {len(labels) - 6} more."
        noun = "tab is" if len(labels) == 1 else "tabs are"
        answer = QMessageBox.question(
            self,
            "Close SnakeSh",
            f"{len(labels)} connected {noun} still open:\n\n{preview}\n\n"
            "Close SnakeSh and disconnect these sessions?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return answer == QMessageBox.Yes

    def _rename_tab_for_life_of_session(self, widget: QWidget) -> None:
        current_name = self._tab_title_root(widget)
        entered, ok = QInputDialog.getText(
            self,
            "Rename Tab",
            "Tab name:",
            QLineEdit.Normal,
            current_name,
        )
        if not ok:
            return
        trimmed = entered.strip()
        widget.setProperty("tab_title_custom", trimmed)
        self._refresh_session_instance_titles()
        if trimmed:
            self.statusBar().showMessage(f"Tab renamed to {trimmed}.", 5000)
        else:
            self.statusBar().showMessage("Tab name reset to session default.", 5000)

    def _reconnect_session_tab(self, widget: QWidget, host: WorkspaceTabWidget, index: int) -> None:
        if isinstance(widget, TerminalTab):
            self._reconnect_terminal_tab_in_place(widget)
            return
        session = self._session_for_tab(widget)
        if session is None:
            QMessageBox.warning(self, "Session Missing", "Unable to find this saved session to reconnect.")
            return
        self._close_tab_in_host(host, index)
        self._connect_session(session)

    def _reconnect_terminal_tab_in_place(self, tab: TerminalTab) -> None:
        kind = str(tab.property("session_kind") or "").strip().upper()
        if kind == "LOCAL":
            if not tab.can_reconnect_session():
                QMessageBox.warning(self, "Session Missing", "Unable to reconnect this local shell tab.")
                return
            self._open_local_shell_tab(existing_tab=tab)
            return

        session = self._session_for_tab(tab)
        if session is None:
            QMessageBox.warning(self, "Session Missing", "Unable to find this saved session to reconnect.")
            return

        if session.protocol == Protocol.SSH:
            self._open_ssh_tab(session, existing_tab=tab)
            return
        if session.protocol == Protocol.TELNET:
            self._open_telnet_tab(session, existing_tab=tab)
            return
        if session.protocol == Protocol.SERIAL:
            self._open_serial_tab(session, existing_tab=tab)
            return

        QMessageBox.warning(
            self,
            "Reconnect Unsupported",
            "This tab can only reconnect SSH, Telnet, Serial, or Local Shell sessions in place.",
        )

    @staticmethod
    def _is_detachable_session_widget(widget: QWidget) -> bool:
        kind = str(widget.property("session_kind") or "").strip().upper()
        return kind in {"SSH", "SFTP", "TELNET", "LOCAL"}

    def _create_detached_tab_window(self) -> tuple[DetachedTabWindow, WorkspaceTabWidget]:
        window = DetachedTabWindow(owner=self, parent=self)
        container = QWidget(window)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        host = self._create_tab_host(primary=False, parent=container)
        host.setProperty("is_detached_host", True)
        layout.addWidget(host, 1)
        window.setCentralWidget(container)
        self._detached_window_by_host[host] = window
        self._detached_host_by_window[window] = host
        host.currentChanged.connect(lambda _index, h=host: self._refresh_detached_window_title(h))
        return window, host

    def _remove_detached_host(self, host: WorkspaceTabWidget, *, close_window: bool) -> None:
        window = self._detached_window_by_host.pop(host, None)
        if window is not None:
            self._detached_host_by_window.pop(window, None)

        if host in self._tab_hosts:
            self._tab_hosts.remove(host)
        if self._active_tab_host == host:
            self._set_active_tab_host(self.tabs)

        try:
            host.setParent(None)
            host.deleteLater()
        except RuntimeError:
            pass

        if close_window and window is not None:
            window.prepare_for_owner_close()
            try:
                window.close()
            except RuntimeError:
                pass
            window.deleteLater()

    def _refresh_detached_window_title(self, host: WorkspaceTabWidget) -> None:
        window = self._detached_window_by_host.get(host)
        if window is None:
            return
        if host.count() <= 0:
            window.setWindowTitle("Detached Session")
            return
        current_index = host.currentIndex()
        if current_index < 0:
            current_index = 0
        title = host.tabText(current_index).replace("*", "").strip()
        if not title:
            title = "Detached Session"
        window.setWindowTitle(f"{title} - SnakeSh")

    def _detach_session_tab(self, widget: QWidget, host: WorkspaceTabWidget, index: int) -> None:
        if not self._is_detachable_session_widget(widget):
            return
        window, detached_host = self._create_detached_tab_window()
        self._move_tab_between_hosts(host, index, detached_host, detached_host.count())
        window.show()
        window.raise_()
        window.activateWindow()
        self._refresh_detached_window_title(detached_host)
        self._set_active_tab_host(detached_host)
        self.statusBar().showMessage("Tab detached to a separate window.", 5000)

    def _reattach_session_tab(self, widget: QWidget, host: WorkspaceTabWidget, index: int) -> None:
        if not self._is_detachable_session_widget(widget):
            return
        self._move_tab_between_hosts(host, index, self.tabs, self.tabs.count())
        self._set_active_tab_host(self.tabs)
        self.statusBar().showMessage("Detached tab reattached to main workspace.", 5000)

    def _reattach_all_tabs_from_host(self, host: WorkspaceTabWidget) -> bool:
        while host.count() > 0:
            widget = host.widget(0)
            if widget is None:
                break
            self._move_tab_between_hosts(host, 0, self.tabs, self.tabs.count(), cleanup_source=False)
        self._sync_host_tab_close_buttons(host)
        return host.count() == 0

    def _on_detached_tab_window_close_requested(self, window: DetachedTabWindow) -> bool:
        host = self._detached_host_by_window.get(window)
        if host is None:
            return True
        if self._is_shutting_down:
            self._remove_detached_host(host, close_window=False)
            return True
        if not self._reattach_all_tabs_from_host(host):
            return False
        self._remove_detached_host(host, close_window=False)
        self._set_active_tab_host(self.tabs)
        self.statusBar().showMessage("Detached window closed and tabs reattached.", 5000)
        return True

    def _show_group_tabs_dialog(self, anchor_widget: QWidget) -> None:
        entries = self._session_tabs_for_grouping()
        if not entries:
            QMessageBox.information(self, "No Open Session Tabs", "Open terminal tabs first.")
            return

        anchor_group = self._tab_group_id(anchor_widget)
        if anchor_group:
            preselected = {id(widget) for widget in self._group_members(anchor_group)}
        else:
            preselected = {id(anchor_widget)}

        dialog = TabGroupingDialog(entries, preselected, self)
        if not dialog.exec():
            return
        selected_widgets = dialog.selected_widgets()

        if len(selected_widgets) < 2:
            if anchor_group:
                for member in self._group_members(anchor_group):
                    self._set_tab_group_id(member, None)
            self._normalize_tab_groups()
            self._refresh_all_tab_titles()
            return

        selected_set = {id(widget) for widget in selected_widgets}
        for _host, _index, widget in self._session_tab_locations():
            if id(widget) in selected_set:
                self._set_tab_group_id(widget, None)

        if anchor_group:
            group_id = anchor_group
            self._group_name(group_id)
        else:
            group_id = f"group-{self._next_tab_group_id}"
            self._next_tab_group_id += 1
            self._group_name(group_id)
        for widget in selected_widgets:
            self._set_tab_group_id(widget, group_id)

        self._normalize_tab_groups()
        self._refresh_all_tab_titles()

    def _open_group_manager(self, preselect_group_id: str | None = None) -> None:
        entries = self._group_manager_entries()
        if not entries:
            QMessageBox.information(self, "No Groups", "There are no active tab groups.")
            return

        dialog = GroupManagerDialog(groups=entries, preselect_group_id=preselect_group_id, parent=self)
        if not dialog.exec():
            return

        for group_id in dialog.ungrouped_groups():
            for member in self._group_members(group_id):
                self._set_tab_group_id(member, None)
            self._tab_group_names.pop(group_id, None)

        for group_id, name in dialog.renamed_groups().items():
            if self._group_members(group_id):
                self._set_group_name(group_id, name)

        self._normalize_tab_groups()
        self._refresh_all_tab_titles()

    def _ungroup_tab(self, widget: QWidget) -> None:
        group_id = self._tab_group_id(widget)
        if not group_id:
            return
        for member in self._group_members(group_id):
            self._set_tab_group_id(member, None)
        self._tab_group_names.pop(group_id, None)
        self._refresh_all_tab_titles()

    @Slot(object)
    def _on_terminal_tab_start_logging_requested(self, tab_obj: object) -> None:
        if not isinstance(tab_obj, TerminalTab):
            return
        self._start_terminal_logging_for_tab(tab_obj)

    @Slot(object)
    def _on_terminal_tab_stop_logging_requested(self, tab_obj: object) -> None:
        if not isinstance(tab_obj, TerminalTab):
            return
        self._stop_terminal_logging_for_tab(tab_obj)

    @Slot(object)
    def _on_terminal_tab_disconnect_requested(self, tab_obj: object) -> None:
        if not isinstance(tab_obj, TerminalTab):
            return
        self._disconnect_terminal_tab(tab_obj)

    @Slot(object, str)
    def _on_terminal_tab_logging_error(self, tab_obj: object, message: str) -> None:
        if not isinstance(tab_obj, TerminalTab):
            return
        tab = tab_obj
        tab.setProperty("global_logging_auto_started", False)
        location = self._find_widget_location(tab)
        title = "Terminal"
        if location is not None:
            title = location[0].tabText(location[1]).replace("*", "").strip() or title
        self.statusBar().showMessage(f"Logging stopped for {title}: {message}", 7000)

    def _start_terminal_logging_for_tab(self, tab: TerminalTab) -> None:
        self._maybe_prune_session_logs()
        default_path = self._default_terminal_log_path(tab)
        selected_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Terminal Session Log",
            str(default_path),
            "Log Files (*.log *.txt);;All Files (*)",
        )
        if not selected_path:
            return

        target = Path(selected_path).expanduser()
        if not target.suffix:
            target = target.with_suffix(".log")

        try:
            tab.start_logging(str(target))
        except Exception as exc:
            QMessageBox.critical(self, "Logging Error", f"Failed to start logging:\n{exc}")
            return
        tab.setProperty("global_logging_auto_started", False)

        self.statusBar().showMessage(f"Logging started: {target}", 7000)

    def _stop_terminal_logging_for_tab(self, tab: TerminalTab) -> None:
        if not tab.is_logging_enabled():
            return
        path = tab.logging_path()
        tab.stop_logging()
        tab.setProperty("global_logging_auto_started", False)
        if path:
            self.statusBar().showMessage(f"Logging stopped: {path}", 7000)
        else:
            self.statusBar().showMessage("Logging stopped.", 5000)

    def _terminal_log_base_dir(self) -> Path:
        configured_dir = (self._settings.terminal_log_dir or "").strip()
        if configured_dir:
            directory = Path(configured_dir).expanduser()
        else:
            directory = Path(AppSettings.defaults().terminal_log_dir).expanduser()
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        return directory

    def _prune_session_log_files(self) -> int:
        base_dir = self._terminal_log_base_dir()
        retention_days = max(1, int(self._settings.session_log_retention_days))
        cutoff = time.time() - (retention_days * 24 * 60 * 60)
        removed = 0

        for path in base_dir.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() not in {".log", ".txt"}:
                continue
            try:
                if path.stat().st_mtime >= cutoff:
                    continue
            except Exception:
                continue
            try:
                path.unlink()
                removed += 1
            except Exception:
                continue

        try:
            directories = sorted(
                (entry for entry in base_dir.rglob("*") if entry.is_dir()),
                key=lambda entry: len(entry.parts),
                reverse=True,
            )
            for directory in directories:
                try:
                    directory.rmdir()
                except Exception:
                    continue
        except Exception:
            pass

        return removed

    def _maybe_prune_session_logs(self, *, force: bool = False) -> None:
        if not self._settings.session_log_cleanup_enabled:
            return
        now = time.monotonic()
        if not force and (now - self._last_session_log_cleanup_monotonic) < 300.0:
            return
        self._last_session_log_cleanup_monotonic = now
        try:
            removed = self._prune_session_log_files()
        except Exception:
            return
        if removed > 0:
            self.statusBar().showMessage(f"Session log cleanup removed {removed} old file(s).", 5000)

    def _prune_web_server_log_files(self) -> int:
        from snakesh.services.web_server_service import prune_web_server_log_files

        retention_days = max(1, int(self._settings.web_server_log_retention_days))
        return prune_web_server_log_files(retention_days)

    def _maybe_prune_web_server_logs(self, *, force: bool = False) -> None:
        if not self._settings.web_server_log_cleanup_enabled:
            return
        now = time.monotonic()
        if not force and (now - self._last_web_server_log_cleanup_monotonic) < 300.0:
            return
        self._last_web_server_log_cleanup_monotonic = now
        try:
            removed = self._prune_web_server_log_files()
        except Exception:
            return
        if removed > 0:
            self.statusBar().showMessage(f"Web server log cleanup removed {removed} old file(s).", 5000)

    def _default_terminal_log_path(self, tab: TerminalTab) -> Path:
        directory = self._terminal_log_base_dir()
        root_title = self._tab_title_root(tab)
        safe_root = re.sub(r"[^A-Za-z0-9._-]+", "_", root_title).strip("._")
        if not safe_root:
            safe_root = "session"
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        return directory / f"{safe_root}-{timestamp}.log"

    def _global_terminal_log_path_for_session(self, session: Session) -> Path:
        directory = self._terminal_log_base_dir()
        normalized_folder = self._session_service.normalize_folder_path(session.folder)
        if normalized_folder:
            for segment in normalized_folder.split("/"):
                safe_segment = re.sub(r"[^A-Za-z0-9._-]+", "_", segment).strip("._")
                if not safe_segment:
                    safe_segment = "folder"
                directory = directory / safe_segment
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        base_name = (session.name or "").strip() or session.host.strip() or "session"
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", base_name).strip("._")
        if not safe_name:
            safe_name = "session"

        now = time.localtime()
        date_part = time.strftime("%Y%m%d", now)
        time_part = time.strftime("%H%M", now)
        second_part = time.strftime("%S", now)
        target = directory / f"{safe_name}-{date_part}-{time_part}-{second_part}.log"
        if not target.exists():
            return target
        counter = 2
        while True:
            candidate = directory / f"{safe_name}-{date_part}-{time_part}-{second_part}-{counter}.log"
            if not candidate.exists():
                return candidate
            counter += 1

    def _maybe_start_global_logging_for_tab(self, tab: TerminalTab, session: Session) -> None:
        if not self._settings.global_session_logging_enabled:
            return
        if tab.is_logging_enabled():
            return
        self._maybe_prune_session_logs()
        try:
            target = self._global_terminal_log_path_for_session(session)
            tab.start_logging(str(target))
            tab.setProperty("global_logging_auto_started", True)
        except Exception as exc:
            self.statusBar().showMessage(
                f"Global logging failed for {_session_display_name(session)}: {exc}",
                7000,
            )

    def _active_terminal_tab_for_command(self) -> TerminalTab | None:
        focused = QApplication.focusWidget()
        while isinstance(focused, QWidget):
            if isinstance(focused, TerminalTab):
                return focused
            focused = focused.parentWidget()

        if self._active_tab_host is not None:
            current = self._active_tab_host.currentWidget()
            if isinstance(current, TerminalTab):
                return current

        terminal_tabs = [
            host.currentWidget()
            for host in self._tab_hosts
            if isinstance(host.currentWidget(), TerminalTab)
        ]
        if len(terminal_tabs) == 1 and isinstance(terminal_tabs[0], TerminalTab):
            return terminal_tabs[0]
        return None

    def _active_fast_command_target(self) -> TerminalTab | RemoteViewerTab | None:
        focused = QApplication.focusWidget()
        while isinstance(focused, QWidget):
            if isinstance(focused, (TerminalTab, RemoteViewerTab)):
                return focused
            focused = focused.parentWidget()

        if self._active_tab_host is not None:
            current = self._active_tab_host.currentWidget()
            if isinstance(current, (TerminalTab, RemoteViewerTab)):
                return current

        candidates: list[TerminalTab | RemoteViewerTab] = []
        for host in self._tab_hosts:
            current = host.currentWidget()
            if isinstance(current, (TerminalTab, RemoteViewerTab)):
                candidates.append(current)
        if len(candidates) == 1:
            return candidates[0]
        return None

    def _run_workspace_command(self) -> None:
        command = self.command_bar_input.text()
        self.command_bar_input.clear()
        target = self._active_terminal_tab_for_command()
        if target is None:
            self.statusBar().showMessage("Select a terminal tab to run commands.", 5000)
            return
        self._dispatch_terminal_command(target, command)

    def _handle_workspace_command_paste(self, raw_text: str) -> None:
        if not raw_text:
            return

        normalized = raw_text.replace("\r\n", "\n").replace("\r", "\n")
        if "\n" not in normalized:
            self.command_bar_input.insert(raw_text)
            return

        target = self._active_terminal_tab_for_command()
        if target is None:
            self.statusBar().showMessage("Select a terminal tab to run commands.", 5000)
            return

        edited = self._edit_workspace_multiline_paste(normalized)
        if edited is None:
            return
        self._dispatch_terminal_batch(target, TerminalTab._normalize_paste_for_pty(edited))

    def _edit_workspace_multiline_paste(self, text: str) -> str | None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Review Multi-line Paste")
        dialog.resize(860, 520)

        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Review and edit the text before sending it to the terminal:"))

        editor = QPlainTextEdit()
        editor.setPlainText(text)
        layout.addWidget(editor)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        send_btn = buttons.button(QDialogButtonBox.Ok)
        if send_btn is not None:
            send_btn.setText("Send")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec():
            return editor.toPlainText()
        return None

    def _show_active_terminal_scrollback(self) -> None:
        target = self._active_terminal_tab_for_command()
        if target is None:
            self.statusBar().showMessage("Select a terminal tab to view scrollback.", 5000)
            return
        target.show_scrollback()

    def _dispatch_terminal_command(self, source_tab: TerminalTab, command: str) -> None:
        group_id = self._tab_group_id(source_tab)
        targets: list[TerminalTab] = []
        if group_id:
            targets = [
                widget
                for widget in self._group_members(group_id)
                if isinstance(widget, TerminalTab)
            ]
        if not targets:
            targets = [source_tab]

        sent = 0
        for target in targets:
            if target.execute_command(command):
                sent += 1

        if sent > 1:
            self.statusBar().showMessage(f"Command sent to {sent} grouped tabs.", 4000)

    def _dispatch_terminal_batch(self, source_tab: TerminalTab, payload: str) -> None:
        group_id = self._tab_group_id(source_tab)
        targets: list[TerminalTab] = []
        if group_id:
            targets = [
                widget
                for widget in self._group_members(group_id)
                if isinstance(widget, TerminalTab)
            ]
        if not targets:
            targets = [source_tab]

        sent = 0
        for target in targets:
            if target.execute_raw_input(payload):
                sent += 1

        if sent > 1:
            self.statusBar().showMessage(f"Batch command sent to {sent} grouped tabs.", 4000)

    def _split_tab(self, source_host: WorkspaceTabWidget, tab_index: int, orientation: Qt.Orientation) -> None:
        widget = source_host.widget(tab_index)
        if widget is None or widget == self.details:
            return

        new_host = self._create_tab_host(primary=False)
        self._insert_host_split(source_host, new_host, orientation)
        self._move_tab_between_hosts(source_host, tab_index, new_host, 0)
        self._set_active_tab_host(new_host)

    def _insert_host_split(
        self,
        source_host: WorkspaceTabWidget,
        new_host: WorkspaceTabWidget,
        orientation: Qt.Orientation,
    ) -> None:
        parent = source_host.parentWidget()
        if isinstance(parent, QSplitter) and parent.orientation() == orientation:
            source_index = parent.indexOf(source_host)
            parent.insertWidget(source_index + 1, new_host)
            parent.setSizes([1] * max(1, parent.count()))
            return

        new_splitter = QSplitter(orientation)
        if isinstance(parent, QSplitter):
            parent_index = parent.indexOf(source_host)
            source_host.setParent(None)
            parent.insertWidget(parent_index, new_splitter)
        else:
            self._tab_workspace_layout.replaceWidget(source_host, new_splitter)
            source_host.setParent(None)
        new_splitter.addWidget(source_host)
        new_splitter.addWidget(new_host)
        new_splitter.setSizes([1, 1])

    def _begin_tab_drag(self, source_host: WorkspaceTabWidget, source_index: int) -> bool:
        widget = source_host.widget(source_index)
        if widget is None or widget == self.details:
            return False
        self._dragged_tab_source = source_host
        self._dragged_tab_widget = widget
        self._dragged_tab_title = source_host.tabText(source_index)
        return True

    def _end_tab_drag(self) -> None:
        self._dragged_tab_source = None
        self._dragged_tab_widget = None
        self._dragged_tab_title = ""

    def _has_tab_drag(self) -> bool:
        return self._dragged_tab_source is not None and self._dragged_tab_widget is not None

    def _drop_dragged_tab(self, target_host: WorkspaceTabWidget, target_index: int) -> None:
        if not self._has_tab_drag():
            return
        source_host = self._dragged_tab_source
        widget = self._dragged_tab_widget
        if source_host is None or widget is None:
            return
        source_index = source_host.indexOf(widget)
        if source_index < 0:
            return
        self._move_tab_between_hosts(source_host, source_index, target_host, target_index)
        self._set_active_tab_host(target_host)

    def _move_tab_between_hosts(
        self,
        source_host: WorkspaceTabWidget,
        source_index: int,
        target_host: WorkspaceTabWidget,
        target_index: int,
        *,
        cleanup_source: bool = True,
    ) -> None:
        widget = source_host.widget(source_index)
        if widget is None:
            return
        title = source_host.tabText(source_index)
        source_host.removeTab(source_index)

        if source_host == target_host and target_index > source_index:
            target_index -= 1
        target_index = max(0, min(target_index, target_host.count()))
        target_host.insertTab(target_index, widget, title)
        target_host.setCurrentIndex(target_index)
        self._sync_host_tab_close_buttons(source_host)
        self._sync_host_tab_close_buttons(target_host)
        self._apply_tab_styles()
        self._refresh_detached_window_title(source_host)
        self._refresh_detached_window_title(target_host)

        if cleanup_source:
            self._remove_host_if_empty(source_host)
        self._normalize_tab_groups()
        self._refresh_session_instance_titles()

    def _remove_host_if_empty(self, host: WorkspaceTabWidget) -> None:
        if host.property("is_primary_host"):
            return
        if host.count() > 0:
            return
        if bool(host.property("is_detached_host")):
            self._remove_detached_host(host, close_window=True)
            return
        if host in self._tab_hosts:
            self._tab_hosts.remove(host)
        if self._active_tab_host == host:
            self._set_active_tab_host(self.tabs)

        parent = host.parentWidget()
        host.setParent(None)
        host.deleteLater()
        if isinstance(parent, QSplitter):
            self._collapse_splitter(parent)

    def _collapse_splitter(self, splitter: QSplitter) -> None:
        current = splitter
        while current.count() <= 1:
            if current.count() == 0:
                break
            remaining = current.widget(0)
            if remaining is None:
                break
            parent = current.parentWidget()
            remaining.setParent(None)
            if isinstance(parent, QSplitter):
                parent_index = parent.indexOf(current)
                current.setParent(None)
                current.deleteLater()
                parent.insertWidget(parent_index, remaining)
                current = parent
                continue
            self._tab_workspace_layout.replaceWidget(current, remaining)
            current.setParent(None)
            current.deleteLater()
            break

    def _apply_tab_styles(self) -> None:
        if self._applying_tab_styles:
            return
        self._applying_tab_styles = True
        try:
            s = self._settings
            host_style = (
                "QTabWidget::pane {"
                f"border: 1px solid {s.field_border};"
                f"background-color: {s.field_bg};"
                "border-radius: 6px;"
                "top: -1px;"
                "}"
                "QTabWidget::tab-bar { left: 0px; }"
            )
            style = (
                "QTabBar::tab {"
                f"background-color: {s.tab_inactive_bg};"
                f"color: {s.tab_inactive_fg};"
                f"border: 1px solid {s.field_border};"
                "border-bottom: none;"
                "padding: 6px 16px 6px 12px;"
                "min-height: 24px;"
                "margin-right: 2px;"
                "margin-bottom: -1px;"
                "border-top-left-radius: 4px;"
                "border-top-right-radius: 4px;"
                "}"
                "QTabBar::tab:selected {"
                f"background-color: {s.tab_inactive_bg};"
                f"color: {s.tab_inactive_fg};"
                "font-weight: 500;"
                "}"
                "QTabBar[workspace_active=\"true\"]::tab:selected {"
                f"background-color: {s.tab_active_bg};"
                f"color: {s.tab_active_fg};"
                "font-weight: 600;"
                "}"
            )
            for host in self._tab_hosts:
                tab_bar = host.tabBar()
                tab_bar.setProperty("workspace_active", "true" if host == self._active_tab_host else "false")
                host.setStyleSheet(host_style)
                tab_bar.setContentsMargins(0, 0, 0, 0)
                tab_bar.setStyleSheet(style)
                self._sync_host_tab_close_buttons(host)
        finally:
            self._applying_tab_styles = False

    @staticmethod
    def _safe_theme_color(value: str, fallback: str) -> str:
        color = QColor(str(value).strip())
        if color.isValid():
            return color.name()
        return fallback

    def _apply_session_details_style(self) -> None:
        bg = self._safe_theme_color(self._settings.terminal_bg, "#000000")
        fg = self._safe_theme_color(self._settings.terminal_fg, "#ffffff")
        details_font = QFont(self._settings.terminal_font_family or "Courier New", self._settings.terminal_font_pt)
        self.details.setFont(details_font)
        self.details.setStyleSheet(
            f"QTextEdit {{ background-color: {bg}; color: {fg}; "
            "border: 1px solid #334155; border-radius: 8px; }"
        )

    def _tab_close_button_palette(self, host: WorkspaceTabWidget, index: int) -> dict[str, str]:
        is_active_tab = host == self._active_tab_host and index == host.currentIndex()
        if is_active_tab:
            tab_bg = self._safe_theme_color(self._settings.tab_active_bg, "#2563eb")
            tab_fg = self._safe_theme_color(self._settings.tab_active_fg, "#ffffff")
        else:
            tab_bg = self._safe_theme_color(self._settings.tab_inactive_bg, "#1f2937")
            tab_fg = self._safe_theme_color(self._settings.tab_inactive_fg, "#cbd5e1")

        icon_color = readable_foreground_color(tab_fg, tab_bg, minimum_ratio=3.6)
        hover_bg = blend_colors(tab_bg, icon_color, 0.16)
        pressed_bg = blend_colors(tab_bg, icon_color, 0.24)
        return {
            "tab_bg": tab_bg,
            "tab_fg": tab_fg,
            "icon": icon_color,
            "hover": hover_bg,
            "pressed": pressed_bg,
        }

    def _style_tab_close_button(
        self,
        button: QToolButton,
        *,
        host: WorkspaceTabWidget,
        index: int,
        widget: QWidget,
    ) -> None:
        palette = self._tab_close_button_palette(host, index)
        icon_path = close_icon_path(palette["icon"])
        button.setProperty("sp_custom_close", True)
        button.setProperty("sp_widget_id", id(widget))
        button.setProperty("sp_close_icon_color", palette["icon"])
        button.setProperty("sp_close_hover_bg", palette["hover"])
        button.setProperty("sp_close_pressed_bg", palette["pressed"])
        button.setProperty(
            "sp_close_visual_signature",
            "|".join((palette["icon"], palette["hover"], palette["pressed"])),
        )
        button.setProperty("sp_close_icon_path", icon_path)
        button.setIcon(QIcon(icon_path) if icon_path else QIcon())
        button.setIconSize(QSize(10, 10))
        button.setToolTip("Close Tab")
        button.setStyleSheet(
            "QToolButton {"
            "background: transparent;"
            "border: none;"
            "border-radius: 6px;"
            "padding: 0px;"
            "margin: 0px;"
            "}"
            "QToolButton:hover {"
            f"background-color: {palette['hover']};"
            "}"
            "QToolButton:pressed {"
            f"background-color: {palette['pressed']};"
            "}"
        )

    def _tab_close_button_from_control(self, control: QWidget | None) -> QToolButton | None:
        if isinstance(control, QToolButton) and bool(control.property("sp_custom_close")):
            return control
        if control is None:
            return None
        button = control.findChild(QToolButton, "workspaceTabCloseButton")
        if button is not None and bool(button.property("sp_custom_close")):
            return button
        return None

    def _create_tab_close_button(self, widget: QWidget) -> QWidget:
        container = QWidget(self)
        container.setObjectName("workspaceTabCloseButtonContainer")
        container.setProperty("sp_custom_close_container", True)
        container.setProperty("sp_widget_id", id(widget))
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 4, 0)
        layout.setSpacing(0)
        button = QToolButton(self)
        button.setObjectName("workspaceTabCloseButton")
        button.setAutoRaise(True)
        button.setFocusPolicy(Qt.NoFocus)
        button.setCursor(Qt.CursorShape.ArrowCursor)
        button.setFixedSize(18, 18)
        button.clicked.connect(self._on_workspace_tab_close_button_clicked)
        button.setProperty("sp_custom_close", True)
        button.setProperty("sp_widget_id", id(widget))
        layout.addWidget(button)
        return container

    def _on_workspace_tab_close_button_clicked(self) -> None:
        button = self.sender()
        if not isinstance(button, QToolButton):
            return
        widget_id = button.property("sp_widget_id")
        if not isinstance(widget_id, int):
            return
        for host in self._tab_hosts:
            for index in range(host.count()):
                widget = host.widget(index)
                if widget is None or id(widget) != widget_id:
                    continue
                self._request_close_tab_in_host(host, index)
                return

    def _sync_host_tab_close_buttons(self, host: WorkspaceTabWidget) -> None:
        details_widget = getattr(self, "details", None)
        tab_bar = host.tabBar()
        for index in range(host.count()):
            widget = host.widget(index)
            left_button = tab_bar.tabButton(index, QTabBar.LeftSide)
            right_button = tab_bar.tabButton(index, QTabBar.RightSide)

            if widget == details_widget:
                if left_button is not None:
                    tab_bar.setTabButton(index, QTabBar.LeftSide, None)
                    left_button.deleteLater()
                if right_button is not None:
                    tab_bar.setTabButton(index, QTabBar.RightSide, None)
                    right_button.deleteLater()
                continue

            widget_id = id(widget)
            chosen_control: QWidget | None = None
            chosen_button: QToolButton | None = None
            for candidate in (right_button, left_button):
                candidate_button = self._tab_close_button_from_control(candidate)
                if candidate_button is not None and candidate_button.property("sp_widget_id") == widget_id:
                    chosen_control = candidate
                    chosen_button = candidate_button
                    break

            if left_button is not None and left_button is not chosen_control:
                tab_bar.setTabButton(index, QTabBar.LeftSide, None)
                left_button.deleteLater()
            if right_button is not None and right_button is not chosen_control:
                tab_bar.setTabButton(index, QTabBar.RightSide, None)
                right_button.deleteLater()

            if chosen_control is None or chosen_button is None:
                chosen_control = self._create_tab_close_button(widget)
                chosen_button = self._tab_close_button_from_control(chosen_control)
                if chosen_button is None:
                    continue

            if tab_bar.tabButton(index, QTabBar.LeftSide) is chosen_control:
                tab_bar.setTabButton(index, QTabBar.LeftSide, None)
            elif tab_bar.tabButton(index, QTabBar.LeftSide) is not None:
                stale_left_button = tab_bar.tabButton(index, QTabBar.LeftSide)
                tab_bar.setTabButton(index, QTabBar.LeftSide, None)
                if stale_left_button is not None:
                    stale_left_button.deleteLater()

            if tab_bar.tabButton(index, QTabBar.RightSide) is not chosen_control:
                tab_bar.setTabButton(index, QTabBar.RightSide, chosen_control)

            self._style_tab_close_button(chosen_button, host=host, index=index, widget=widget)

    @staticmethod
    def _session_tree_search_fields(session: Session) -> tuple[object, ...]:
        return (
            session.name,
            session.host,
            session.username,
            session.domain,
            session.folder,
            session.telnet_terminal_type,
            session.serial_baud_rate,
            session.serial_data_bits,
            session.serial_parity,
            session.serial_stop_bits,
            session.serial_flow_control,
            session.serial_terminal_type,
            tuple(session.tags),
        )

    def _session_tree_search_text(self, session: Session) -> str:
        cache_key = self._session_tree_search_fields(session)
        cached = self._session_tree_search_cache.get(session.id)
        if cached is not None and cached[0] == cache_key:
            return cached[1]
        haystack = " ".join(
            [
                session.name,
                session.host,
                session.username,
                session.domain,
                session.folder,
                session.telnet_terminal_type,
                str(session.serial_baud_rate),
                str(session.serial_data_bits),
                session.serial_parity,
                session.serial_stop_bits,
                session.serial_flow_control,
                session.serial_terminal_type,
                " ".join(session.tags),
            ]
        ).lower()
        self._session_tree_search_cache[session.id] = (cache_key, haystack)
        return haystack

    def _refresh_tree(self) -> None:
        filter_text = self.search_input.text().strip().lower()
        sessions = sorted(self._session_service.all(), key=lambda s: (s.folder.lower(), s.name.lower()))
        folders = sorted(set(self._session_service.all_folders()), key=lambda value: (value.count("/"), value.lower()))
        active_session_ids = {session.id for session in sessions}
        stale_ids = [
            session_id
            for session_id in self._session_tree_search_cache
            if session_id not in active_session_ids
        ]
        for session_id in stale_ids:
            self._session_tree_search_cache.pop(session_id, None)

        matched_sessions: list[Session] = []
        visible_folders: set[str] = set()
        for session in sessions:
            haystack = self._session_tree_search_text(session)
            if filter_text and filter_text not in haystack:
                continue
            matched_sessions.append(session)
            folder_path = self._session_service.normalize_folder_path(session.folder)
            visible_folders.add(folder_path)
            while "/" in folder_path:
                folder_path = folder_path.rsplit("/", 1)[0]
                visible_folders.add(folder_path)

        if filter_text:
            for folder in folders:
                if filter_text in folder.lower():
                    visible_folders.add(folder)
                    parent = folder
                    while "/" in parent:
                        parent = parent.rsplit("/", 1)[0]
                        visible_folders.add(parent)
        else:
            visible_folders.update(folders)

        folder_items: dict[str, QTreeWidgetItem] = {}
        self.session_tree.setUpdatesEnabled(False)
        try:
            self.session_tree.clear()
            for folder_path in folders:
                if filter_text and folder_path not in visible_folders:
                    continue
                item = self._ensure_folder_item(folder_items, folder_path)
                item.setData(0, TREE_KIND_ROLE, "folder")
                item.setData(0, TREE_FOLDER_PATH_ROLE, folder_path)

            for session in matched_sessions:
                folder_path = self._session_service.normalize_folder_path(session.folder)
                folder_item = self._ensure_folder_item(folder_items, folder_path)
                item = QTreeWidgetItem([f"{session.name} [{session.protocol.value.upper()}]"])
                item.setData(0, TREE_KIND_ROLE, "session")
                item.setData(0, TREE_SESSION_ID_ROLE, session.id)
                item.setData(0, TREE_SESSION_FOLDER_ROLE, folder_path)
                item.setFlags(
                    Qt.ItemIsEnabled
                    | Qt.ItemIsSelectable
                    | Qt.ItemIsDragEnabled
                )
                folder_item.addChild(item)

            self._restore_session_tree_expansion(folder_items, bool(filter_text))
        finally:
            self.session_tree.setUpdatesEnabled(True)
            self.session_tree.viewport().update()
        self._update_details()

    def _restore_session_tree_expansion(
        self,
        folder_items: dict[str, QTreeWidgetItem],
        filter_active: bool,
    ) -> None:
        self._syncing_tree_expansion_state = True
        try:
            if filter_active:
                self.session_tree.expandAll()
                return

            existing_paths = {
                self._session_service.normalize_folder_path(path)
                for path in folder_items
            }
            saved_paths = {
                self._session_service.normalize_folder_path(path)
                for path in self._settings.session_tree_expanded_folders
                if path
            }
            saved_paths &= existing_paths

            # Keep settings clean when folders are removed/renamed.
            if saved_paths != set(self._settings.session_tree_expanded_folders):
                self._persist_session_tree_expanded_folders(saved_paths)

            if not saved_paths:
                # First run or no previous preference: default to expanded.
                self.session_tree.expandAll()
                self._persist_session_tree_expanded_folders(self._expanded_session_tree_folders())
                return

            expanded_required: set[str] = set(saved_paths)
            for folder_path in list(saved_paths):
                parent = folder_path
                while "/" in parent:
                    parent = parent.rsplit("/", 1)[0]
                    if parent in existing_paths:
                        expanded_required.add(parent)

            for folder_path, item in folder_items.items():
                normalized = self._session_service.normalize_folder_path(folder_path)
                item.setExpanded(normalized in expanded_required)
        finally:
            self._syncing_tree_expansion_state = False

    def _expanded_session_tree_folders(self) -> set[str]:
        expanded: set[str] = set()
        for index in range(self.session_tree.topLevelItemCount()):
            self._collect_expanded_folder_paths(self.session_tree.topLevelItem(index), expanded)
        return expanded

    def _collect_expanded_folder_paths(self, item: QTreeWidgetItem, output: set[str]) -> None:
        if item.data(0, TREE_KIND_ROLE) == "folder":
            folder = item.data(0, TREE_FOLDER_PATH_ROLE)
            if isinstance(folder, str) and folder.strip() and item.isExpanded():
                output.add(self._session_service.normalize_folder_path(folder))
        for index in range(item.childCount()):
            self._collect_expanded_folder_paths(item.child(index), output)

    def _persist_session_tree_expanded_folders(self, folders: set[str]) -> None:
        normalized = {
            self._session_service.normalize_folder_path(path)
            for path in folders
            if isinstance(path, str) and path.strip()
        }
        ordered = sorted(normalized, key=lambda value: (value.count("/"), value.lower()))
        if ordered == self._settings.session_tree_expanded_folders:
            return
        self._settings.session_tree_expanded_folders = ordered
        self._schedule_ui_settings_save()

    def _on_session_tree_item_expanded(self, item: QTreeWidgetItem) -> None:
        if self._syncing_tree_expansion_state:
            return
        if item.data(0, TREE_KIND_ROLE) != "folder":
            return
        folder = item.data(0, TREE_FOLDER_PATH_ROLE)
        if not isinstance(folder, str) or not folder.strip():
            return
        current = set(self._settings.session_tree_expanded_folders)
        current.add(self._session_service.normalize_folder_path(folder))
        self._persist_session_tree_expanded_folders(current)

    def _on_session_tree_item_collapsed(self, item: QTreeWidgetItem) -> None:
        if self._syncing_tree_expansion_state:
            return
        if item.data(0, TREE_KIND_ROLE) != "folder":
            return
        folder = item.data(0, TREE_FOLDER_PATH_ROLE)
        if not isinstance(folder, str) or not folder.strip():
            return
        normalized = self._session_service.normalize_folder_path(folder)
        current = {
            path
            for path in self._settings.session_tree_expanded_folders
            if path != normalized and not path.startswith(f"{normalized}/")
        }
        self._persist_session_tree_expanded_folders(current)

    def _ensure_folder_item(self, folder_items: dict[str, QTreeWidgetItem], folder_path: str) -> QTreeWidgetItem:
        normalized = self._session_service.normalize_folder_path(folder_path)
        existing = folder_items.get(normalized)
        if existing is not None:
            return existing

        parent_item: QTreeWidgetItem | None = None
        running_parts: list[str] = []
        for segment in normalized.split("/"):
            running_parts.append(segment)
            current_path = "/".join(running_parts)
            current_item = folder_items.get(current_path)
            if current_item is None:
                current_item = QTreeWidgetItem([segment])
                current_item.setData(0, TREE_KIND_ROLE, "folder")
                current_item.setData(0, TREE_FOLDER_PATH_ROLE, current_path)
                current_item.setFlags(
                    Qt.ItemIsEnabled
                    | Qt.ItemIsSelectable
                    | Qt.ItemIsDropEnabled
                )
                if parent_item is None:
                    self.session_tree.addTopLevelItem(current_item)
                else:
                    parent_item.addChild(current_item)
                folder_items[current_path] = current_item
            parent_item = current_item
        return folder_items[normalized]

    def _current_session(self) -> Session | None:
        item = self.session_tree.currentItem()
        if not item:
            return None
        if item.data(0, TREE_KIND_ROLE) != "session":
            return None
        session_id = item.data(0, TREE_SESSION_ID_ROLE)
        if not session_id:
            return None
        return self._session_service.by_id(session_id)

    def _selected_session_ids(self) -> list[str]:
        selected: list[str] = []
        seen: set[str] = set()
        for item in self.session_tree.selectedItems():
            if item.data(0, TREE_KIND_ROLE) != "session":
                continue
            session_id = item.data(0, TREE_SESSION_ID_ROLE)
            if not isinstance(session_id, str) or not session_id or session_id in seen:
                continue
            seen.add(session_id)
            selected.append(session_id)
        return selected

    def _selected_folder_paths(self) -> list[str]:
        selected: list[str] = []
        seen: set[str] = set()
        for item in self.session_tree.selectedItems():
            if item.data(0, TREE_KIND_ROLE) != "folder":
                continue
            folder = item.data(0, TREE_FOLDER_PATH_ROLE)
            if not isinstance(folder, str) or not folder or folder in seen:
                continue
            seen.add(folder)
            selected.append(folder)
        return selected

    def _selected_sessions(self) -> list[Session]:
        selected: list[Session] = []
        for session_id in self._selected_session_ids():
            session = self._session_service.by_id(session_id)
            if session is not None:
                selected.append(session)
        return selected

    def _exec_menu(self, menu: QMenu, global_pos) -> object | None:
        return menu.exec(global_pos)

    def _show_session_tree_menu(self, position) -> None:
        item = self.session_tree.itemAt(position)
        if item is None:
            self.session_tree.clearSelection()
        elif item not in self.session_tree.selectedItems():
            self.session_tree.clearSelection()
            item.setSelected(True)
            self.session_tree.setCurrentItem(item)

        selected_session_ids = self._selected_session_ids()
        selected_folder_paths = self._selected_folder_paths()
        selected_sessions = [self._session_service.by_id(session_id) for session_id in selected_session_ids]
        selected_sessions = [session for session in selected_sessions if session is not None]
        selected_session = selected_sessions[0] if len(selected_sessions) == 1 else None
        current_folder_path: str | None = None
        if item and item.data(0, TREE_KIND_ROLE) == "folder":
            folder = item.data(0, TREE_FOLDER_PATH_ROLE)
            if isinstance(folder, str) and folder:
                current_folder_path = folder
        elif item and item.data(0, TREE_KIND_ROLE) == "session":
            folder = item.data(0, TREE_SESSION_FOLDER_ROLE)
            if isinstance(folder, str) and folder:
                current_folder_path = folder

        menu = QMenu(self)
        add_session_folder = self._suggest_new_session_folder(
            current_folder_path=current_folder_path,
            selected_session=selected_session,
            selected_folder_paths=selected_folder_paths,
        )
        add_session_action = menu.addAction("Add Session...")
        edit_session_action = menu.addAction("Edit Session...")
        edit_session_action.setEnabled(selected_session is not None and not selected_folder_paths)
        menu.addSeparator()
        add_folder_action = menu.addAction("New Folder")
        add_subfolder_action = None
        if current_folder_path:
            add_subfolder_action = menu.addAction("New Subfolder")

        connect_action = None
        open_sftp_action = None
        install_key_action = None
        bulk_edit_action = None
        duplicate_action = None
        rename_action = None
        rename_folder_path: str | None = None
        rename_session: Session | None = None
        if len(selected_folder_paths) == 1 and not selected_sessions:
            rename_folder_path = selected_folder_paths[0]
            menu.addSeparator()
            rename_action = menu.addAction("Rename Folder...")
        if selected_sessions and not selected_folder_paths:
            menu.addSeparator()
            if len(selected_sessions) == 1:
                rename_session = selected_sessions[0]
                rename_action = menu.addAction("Rename Session...")
                duplicate_action = menu.addAction("Duplicate Session")
            if len(selected_sessions) >= 2:
                protocols = {session.protocol for session in selected_sessions}
                bulk_edit_action = menu.addAction("Bulk Edit Selected Sessions...")
                bulk_edit_action.setEnabled(len(protocols) == 1)
            connect_action = menu.addAction("Connect" if len(selected_sessions) == 1 else "Connect Selected Sessions")
            if len(selected_sessions) == 1 and selected_sessions[0].protocol in (Protocol.SSH, Protocol.SFTP):
                open_sftp_action = menu.addAction("Open SFTP Tab")
                install_key_action = menu.addAction("Install Public Key")

        delete_action = None
        if selected_session_ids or selected_folder_paths:
            menu.addSeparator()
            delete_action = menu.addAction("Delete")

        chosen = self._exec_menu(menu, self.session_tree.viewport().mapToGlobal(position))
        if chosen == add_session_action:
            self._new_session(default_folder=add_session_folder)
        elif chosen == edit_session_action and selected_session is not None and not selected_folder_paths:
            self._edit_specific_session(selected_session)
        elif chosen == add_folder_action:
            self._create_folder(None)
        elif add_subfolder_action is not None and chosen == add_subfolder_action:
            self._create_folder(current_folder_path)
        elif rename_action is not None and chosen == rename_action:
            if rename_session is not None:
                self._rename_session(rename_session)
            elif rename_folder_path is not None:
                self._rename_folder(rename_folder_path)
        elif duplicate_action is not None and chosen == duplicate_action and rename_session is not None:
            self._duplicate_session(rename_session)
        elif bulk_edit_action is not None and chosen == bulk_edit_action and selected_sessions:
            self._bulk_edit_selected_sessions(selected_sessions)
        elif connect_action is not None and chosen == connect_action and selected_sessions:
            self._connect_selected_sessions(selected_sessions)
        elif open_sftp_action is not None and chosen == open_sftp_action and selected_sessions:
            self._open_sftp_tab(selected_sessions[0])
        elif install_key_action is not None and chosen == install_key_action and selected_sessions:
            self._install_public_key_for_session(selected_sessions[0])
        elif delete_action is not None and chosen == delete_action:
            self._delete_selected_tree_items(selected_session_ids, selected_folder_paths)
        menu.deleteLater()

    def _suggest_new_session_folder(
        self,
        *,
        current_folder_path: str | None,
        selected_session: Session | None,
        selected_folder_paths: list[str],
    ) -> str:
        if current_folder_path and current_folder_path.strip():
            return self._session_service.normalize_folder_path(current_folder_path)
        if len(selected_folder_paths) == 1 and selected_folder_paths[0].strip():
            return self._session_service.normalize_folder_path(selected_folder_paths[0])
        if selected_session and selected_session.folder.strip():
            return self._session_service.normalize_folder_path(selected_session.folder)
        return "Default"

    def _rename_session(self, session: Session) -> None:
        new_name, ok = QInputDialog.getText(
            self,
            "Rename Session",
            "Session name:",
            QLineEdit.Normal,
            session.name,
        )
        if not ok:
            return
        trimmed = new_name.strip()
        if not trimmed or trimmed == session.name:
            return
        try:
            changed = self._session_service.rename_session(session.id, trimmed)
        except ValueError as exc:
            QMessageBox.warning(self, "Rename Failed", str(exc))
            return
        if not changed:
            return
        self._rename_open_session_tabs(session.id, trimmed)
        self._refresh_tree()
        self.statusBar().showMessage(f"Renamed session to {trimmed}.", 5000)

    def _duplicate_session(self, session: Session) -> None:
        cloned = Session.from_dict(session.to_dict())
        cloned.id = str(uuid4())
        cloned.name = self._next_duplicate_session_name(session.name)
        self._session_service.add_or_update(cloned)
        if cloned.save_password:
            try:
                source_password = self._credential_service.load_password(session)
                if source_password:
                    self._persist_session_password(cloned, source_password)
            except Exception:
                pass
        self._refresh_tree()
        self._select_session_item_by_id(cloned.id)
        self.statusBar().showMessage(f"Duplicated session as {cloned.name}.", 5000)

    @staticmethod
    def _changed_session_fields(before: Session, after: Session) -> list[str]:
        changed: list[str] = []
        for field_name in Session.__dataclass_fields__.keys():
            if field_name == "id":
                continue
            if getattr(before, field_name) != getattr(after, field_name):
                changed.append(field_name)
        return changed

    @staticmethod
    def _build_bulk_edit_template(sessions: list[Session]) -> Session:
        template = Session.from_dict(sessions[0].to_dict())
        if len(sessions) < 2:
            return template

        defaults = Session(
            name="",
            host="",
            protocol=template.protocol,
            port=SessionService.default_port_for(template.protocol),
        )
        for field_name in Session.__dataclass_fields__.keys():
            if field_name in {"id", "protocol"}:
                continue
            first_value = getattr(sessions[0], field_name)
            if all(getattr(session, field_name) == first_value for session in sessions[1:]):
                continue
            fallback = copy.deepcopy(getattr(defaults, field_name))
            if isinstance(fallback, str):
                fallback = ""
            setattr(template, field_name, fallback)
        return template

    def _bulk_edit_selected_sessions(self, sessions: list[Session]) -> None:
        if len(sessions) < 2:
            QMessageBox.information(self, "Bulk Edit", "Select at least two sessions to use bulk edit.")
            return
        protocols = {session.protocol for session in sessions}
        if len(protocols) != 1:
            QMessageBox.warning(self, "Bulk Edit", "Select sessions with the same protocol for bulk edit.")
            return

        template = self._build_bulk_edit_template(sessions)
        dialog = SessionEditorDialog(
            parent=self,
            session=template,
            password_loader=self._credential_service.load_password,
        )
        protocol_name = template.protocol.value.upper()
        dialog.setWindowTitle(f"Bulk Edit {len(sessions)} {protocol_name} Sessions")
        dialog.protocol_input.setEnabled(False)
        if not dialog.exec():
            return

        edited = dialog.build_session()
        changed_fields = self._changed_session_fields(template, edited)
        entered_password = dialog.password_text().strip()
        if not changed_fields and not entered_password:
            self.statusBar().showMessage("No bulk changes were applied.", 5000)
            return

        preview = ", ".join(changed_fields[:8]) if changed_fields else "password only"
        if len(changed_fields) > 8:
            preview += f", +{len(changed_fields) - 8} more"
        answer = QMessageBox.question(
            self,
            "Apply Bulk Changes",
            (
                f"Apply these updates to {len(sessions)} session(s)?\n\n"
                f"Changed fields: {preview}"
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        changed_field_set = set(changed_fields)
        for source_session in sessions:
            updated = Session.from_dict(source_session.to_dict())
            for field_name in changed_fields:
                setattr(updated, field_name, copy.deepcopy(getattr(edited, field_name)))
            updated.id = source_session.id
            self._session_service.add_or_update(updated)
            self._apply_bulk_session_updates_to_open_tabs(updated, changed_field_set)
            if "save_password" in changed_field_set and not updated.save_password:
                self._credential_service.clear_password(updated)
            if entered_password and updated.save_password:
                self._persist_session_password(updated, entered_password)

        self._refresh_tree()
        self._update_details()
        self.statusBar().showMessage(f"Applied bulk changes to {len(sessions)} session(s).", 6000)

    def _apply_bulk_session_updates_to_open_tabs(self, session: Session, changed_fields: set[str]) -> None:
        if "name" in changed_fields:
            self._rename_open_session_tabs(session.id, session.name)
        color_fields = {"terminal_color_override_enabled", "terminal_bg_color", "terminal_fg_color"}
        if not (changed_fields & color_fields):
            return
        for _host, _index, widget in self._session_tab_locations():
            if widget.property("session_id") != session.id:
                continue
            if not isinstance(widget, TerminalTab):
                continue
            self._apply_terminal_color_override_from_session(widget, session)
            widget.apply_settings(self._terminal_settings_for_tab(widget))

    def _next_duplicate_session_name(self, original_name: str) -> str:
        base_name = original_name.strip() or "Session"
        used = {
            (session.name or "").strip().lower()
            for session in self._session_service.all()
            if (session.name or "").strip()
        }
        suffix = 2
        while True:
            candidate = f"{base_name} {suffix}"
            if candidate.lower() not in used:
                return candidate
            suffix += 1

    def _select_session_item_by_id(self, session_id: str) -> None:
        if not session_id:
            return

        def _scan(item: QTreeWidgetItem) -> QTreeWidgetItem | None:
            if item.data(0, TREE_KIND_ROLE) == "session" and item.data(0, TREE_SESSION_ID_ROLE) == session_id:
                return item
            for idx in range(item.childCount()):
                found = _scan(item.child(idx))
                if found is not None:
                    return found
            return None

        match: QTreeWidgetItem | None = None
        for top_index in range(self.session_tree.topLevelItemCount()):
            item = self.session_tree.topLevelItem(top_index)
            if item is None:
                continue
            match = _scan(item)
            if match is not None:
                break
        if match is None:
            return
        self.session_tree.setCurrentItem(match)
        self.session_tree.scrollToItem(match)
        self.session_tree.clearSelection()
        match.setSelected(True)

    def _rename_open_session_tabs(self, session_id: str, new_name: str) -> None:
        for _host, _index, widget in self._session_tab_locations():
            current_id = widget.property("session_id")
            if current_id != session_id:
                continue
            widget.setProperty("tab_title_root", new_name)
            self._set_base_tab_title(widget, new_name)
        self._refresh_session_instance_titles()

    def _rename_folder(self, folder_path: str) -> None:
        normalized = self._session_service.normalize_folder_path(folder_path)
        if normalized == "Default":
            QMessageBox.information(self, "Rename Folder", "The Default folder cannot be renamed.")
            return

        parent = ""
        current_name = normalized
        if "/" in normalized:
            parent, current_name = normalized.rsplit("/", 1)

        new_name, ok = QInputDialog.getText(
            self,
            "Rename Folder",
            "Folder name:",
            QLineEdit.Normal,
            current_name,
        )
        if not ok:
            return
        trimmed = new_name.strip()
        if not trimmed:
            return
        target_path = f"{parent}/{trimmed}" if parent else trimmed
        target_normalized = self._session_service.normalize_folder_path(target_path)
        if target_normalized == normalized:
            return

        try:
            renamed_to = self._session_service.rename_folder(normalized, target_normalized)
        except ValueError as exc:
            QMessageBox.warning(self, "Rename Failed", str(exc))
            return

        self._remap_expanded_folder_paths_after_rename(normalized, renamed_to)
        self._refresh_tree()
        self.statusBar().showMessage(f"Renamed folder to {renamed_to}.", 5000)

    def _remap_expanded_folder_paths_after_rename(self, old_path: str, new_path: str) -> None:
        old_normalized = self._session_service.normalize_folder_path(old_path)
        new_normalized = self._session_service.normalize_folder_path(new_path)
        mapped: set[str] = set()
        for path in self._settings.session_tree_expanded_folders:
            normalized = self._session_service.normalize_folder_path(path)
            if normalized == old_normalized:
                mapped.add(new_normalized)
                continue
            if normalized.startswith(f"{old_normalized}/"):
                suffix = normalized[len(old_normalized) :]
                mapped.add(f"{new_normalized}{suffix}")
                continue
            mapped.add(normalized)
        self._persist_session_tree_expanded_folders(mapped)

    def _on_session_tree_double_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        if item.data(0, TREE_KIND_ROLE) != "session":
            return
        session_id = item.data(0, TREE_SESSION_ID_ROLE)
        if not isinstance(session_id, str) or not session_id:
            return
        session = self._session_service.by_id(session_id)
        if session is None:
            return
        self._connect_session(session)

    def _create_folder(self, parent_folder: str | None) -> None:
        title = "New Subfolder" if parent_folder else "New Folder"
        prompt = "Subfolder name:" if parent_folder else "Folder name:"
        name, ok = QInputDialog.getText(self, title, prompt)
        if not ok:
            return
        trimmed = name.strip()
        if not trimmed:
            return
        folder_path = trimmed if not parent_folder else f"{parent_folder}/{trimmed}"
        self._session_service.create_folder(folder_path)
        self._refresh_tree()

    def _move_sessions_to_folder(self, session_ids: list[str], target_folder: str) -> None:
        moved = self._session_service.move_sessions(session_ids, target_folder)
        if moved <= 0:
            return
        self._refresh_tree()
        self.statusBar().showMessage(f"Moved {moved} session(s) to {target_folder}.", 5000)

    def _required_dependency_id_for_protocol(self, protocol: Protocol) -> str | None:
        if protocol == Protocol.VNC:
            return "vncviewer"
        if protocol == Protocol.NOMACHINE:
            return "nxplayer"
        if protocol != Protocol.RDP:
            return None
        if platform.system().lower() == "windows":
            return "mstsc"
        return "xfreerdp"

    def _dependency_by_id(self, dep_id: str) -> PlatformDependency | None:
        for dep in required_dependencies():
            if dep.id == dep_id:
                return dep
        return None

    @staticmethod
    def _is_dependency_missing(dep: PlatformDependency) -> bool:
        if dep.is_available is not None:
            try:
                return not dep.is_available()
            except Exception:
                pass
        return shutil.which(dep.command) is None

    def _show_dependency_warning(self, title: str, message: str, help_url: str | None = None) -> None:
        if not help_url:
            QMessageBox.warning(self, title, message)
            return

        safe_message = html.escape(message).replace("\n", "<br>")
        safe_url = html.escape(help_url, quote=True)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(title)
        box.setText(safe_message)
        box.setInformativeText(f'Download: <a href="{safe_url}">{safe_url}</a>')
        box.setStandardButtons(QMessageBox.Ok)
        box.setTextFormat(Qt.RichText)
        box.setTextInteractionFlags(Qt.TextBrowserInteraction)
        for label in box.findChildren(QLabel):
            label.setOpenExternalLinks(True)
            label.setTextInteractionFlags(Qt.TextBrowserInteraction)
        box.exec()

    def _ensure_dependency(self, dep_id: str) -> bool:
        dep = self._dependency_by_id(dep_id)
        if dep is None:
            return True
        if not self._is_dependency_missing(dep):
            return True

        if dep.can_auto_install and dep.install_command:
            cmd = command_to_display(dep.install_command)
            answer = QMessageBox.question(
                self,
                "Missing Dependency",
                (
                    f"Missing dependency: {dep.display_name}\n"
                    f"Required for: {dep.required_for}\n\n"
                    "SnakeSh will request administrator/root privileges if needed.\n\n"
                    f"Install automatically now?\n\nCommand:\n{cmd}"
                ),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if answer != QMessageBox.Yes:
                self.statusBar().showMessage("Launch cancelled: required dependency is missing.", 5000)
                return False
            success, message = attempt_auto_install(dep)
            if success:
                QMessageBox.information(self, "Dependency Installed", f"{dep.display_name}: {message}")
                return True
            hint = suggested_install_command(dep)
            hint_text = f"\n\nSuggested command:\n{command_to_display(hint)}" if hint else ""
            help_url = dependency_help_url(dep)
            self._show_dependency_warning(
                "Install Failed",
                (
                    f"Failed to install {dep.display_name} automatically.\n"
                    f"Reason: {message}\n\nPlease install it manually.{hint_text}"
                ),
                help_url=help_url,
            )
            return False

        hint = suggested_install_command(dep)
        hint_text = f"\n\nSuggested command:\n{command_to_display(hint)}" if hint else ""
        help_url = dependency_help_url(dep)
        self._show_dependency_warning(
            "Missing Dependency",
            (
                f"Missing dependency: {dep.display_name}\n"
                f"Required for: {dep.required_for}\n\n"
                "Automatic install is not available for this dependency on this platform."
                f"{hint_text}"
            ),
            help_url=help_url,
        )
        return False

    def _ensure_protocol_dependency(self, protocol: Protocol) -> bool:
        dep_id = self._required_dependency_id_for_protocol(protocol)
        if not dep_id:
            return True
        return self._ensure_dependency(dep_id)

    @staticmethod
    def _linux_rdp_certificate_trust_key(session: Session) -> str:
        host = (session.host or "").strip().lower()
        if not host:
            return ""
        try:
            port = int(session.port)
        except (TypeError, ValueError):
            port = 3389
        if port <= 0 or port > 65535:
            port = 3389
        return f"{host}|{port}"

    def _is_linux_rdp_certificate_trusted(self, session: Session) -> bool:
        key = self._linux_rdp_certificate_trust_key(session)
        if not key:
            return False
        return key in self._settings.rdp_trusted_certificate_hosts

    def _remember_linux_rdp_certificate_trust(self, session: Session) -> None:
        key = self._linux_rdp_certificate_trust_key(session)
        if not key:
            return
        current = set(self._settings.rdp_trusted_certificate_hosts)
        if key in current:
            return
        current.add(key)
        self._settings.rdp_trusted_certificate_hosts = sorted(current)
        self._settings_service.save(self._settings)

    def _confirm_linux_rdp_certificate_trust(self, session: Session) -> bool:
        if self._is_linux_rdp_certificate_trusted(session):
            return True
        answer = QMessageBox.question(
            self,
            "RDP Certificate Trust",
            (
                f"Accept and remember the TLS certificate trust for RDP host {session.host}:{session.port}?\n\n"
                "SnakeSh will launch FreeRDP with trust-on-first-use (/cert:tofu) "
                "to avoid hidden terminal prompts."
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return False
        self._remember_linux_rdp_certificate_trust(session)
        return True

    def _open_remote_viewer_tab(
        self,
        session: Session,
        *,
        protocol_name: str,
        detached_command_builder: RemoteDetachedBuilder,
        embedded_command_builder: RemoteEmbeddedBuilder | None = None,
        windows_reparent_embed: bool = False,
        linux_x11_reparent_embed: bool = False,
        start_detached: bool = False,
        runtime_only: bool = False,
    ) -> None:
        tab = RemoteViewerTab(
            session=session,
            protocol_name=protocol_name,
            detached_command_builder=detached_command_builder,
            embedded_command_builder=embedded_command_builder,
            windows_reparent_embed=windows_reparent_embed,
            linux_x11_reparent_embed=linux_x11_reparent_embed,
            parent=self,
        )
        self._add_session_tab(session, tab, protocol_name.upper(), runtime_only=runtime_only)
        self._schedule_remote_viewer_tab_start(tab, start_detached=start_detached)

    def _schedule_remote_viewer_tab_start(self, tab: RemoteViewerTab, *, start_detached: bool) -> None:
        if self._profile_restore_in_progress:
            self._profile_restore_pending_remote_starts.append((tab, start_detached))
            return
        self._remote_viewer_start_queue.append((tab, start_detached))
        self._schedule_next_remote_viewer_tab_start()

    def _flush_profile_restore_remote_starts(self) -> None:
        if not self._profile_restore_pending_remote_starts:
            return
        pending = list(self._profile_restore_pending_remote_starts)
        self._profile_restore_pending_remote_starts.clear()
        self._remote_viewer_start_queue.extend(pending)
        self._schedule_next_remote_viewer_tab_start()

    def _schedule_next_remote_viewer_tab_start(self) -> None:
        if self._profile_restore_in_progress:
            return
        if self._remote_viewer_start_in_progress or self._remote_viewer_start_scheduled:
            return
        if not self._remote_viewer_start_queue:
            return
        self._remote_viewer_start_scheduled = True
        QTimer.singleShot(0, self._start_next_queued_remote_viewer_tab)

    def _start_next_queued_remote_viewer_tab(self) -> None:
        self._remote_viewer_start_scheduled = False
        if self._profile_restore_in_progress or self._remote_viewer_start_in_progress:
            self._schedule_next_remote_viewer_tab_start()
            return
        if self._connection_load_canceled():
            self._cancel_pending_remote_viewer_starts()
            operation = self._connection_load_progress
            cancel_message = (
                f"{operation.title} canceled." if operation is not None else "Connection loading canceled."
            )
            self._finish_connection_load_progress(
                final_message=cancel_message,
                timeout_ms=7000,
                mark_complete=False,
            )
            return

        while self._remote_viewer_start_queue:
            tab, start_detached = self._remote_viewer_start_queue.popleft()
            try:
                location = self._find_widget_location(tab)
            except RuntimeError:
                location = None
            if location is None:
                self._advance_connection_load_progress(
                    f"Skipped {tab._session.name or tab._session.host or tab._protocol_name} ({tab._protocol_name})."
                )
                continue
            label = f"{tab._session.name or tab._session.host or tab._protocol_name} ({tab._protocol_name})"
            self._refresh_connection_load_message(f"Starting {label}...")
            self._remote_viewer_start_in_progress = True
            try:
                self._start_remote_viewer_tab(tab, start_detached)
            finally:
                self._remote_viewer_start_in_progress = False
            self._advance_connection_load_progress(f"Started {label}.")
            break

        self._schedule_next_remote_viewer_tab_start()
        self._maybe_finish_connection_load_progress(
            self._connection_load_completion_message or "Connection loading complete."
        )

    def _start_remote_viewer_tab(self, tab: RemoteViewerTab, start_detached: bool = False) -> None:
        if start_detached:
            ok, message = tab.detach_viewer()
        else:
            ok, message = tab.start()
        if ok:
            self.statusBar().showMessage(message, 7000)
            return
        QMessageBox.critical(self, "Connection Error", message)
        location = self._find_widget_location(tab)
        if location is not None:
            host, index = location
            self._close_tab_in_host(host, index)

    @staticmethod
    def _start_detached_for_remote_session(session: Session) -> bool:
        if session.protocol in (Protocol.VNC, Protocol.NOMACHINE):
            return True
        if session.protocol != Protocol.RDP:
            return False
        if platform.system().lower() != "linux":
            return True
        return normalize_remote_launch_mode(session.remote_launch_mode) == "detached"

    def _open_rdp_tab(
        self,
        session: Session,
        *,
        password: str | None,
        linux_trust_certificate: bool,
        runtime_only: bool = False,
    ) -> None:
        system = platform.system().lower()
        if system != "linux":
            def detached_builder() -> RemoteLaunchSpec:
                cmd = build_rdp_command(
                    session,
                    password=password,
                    linux_trust_certificate=linux_trust_certificate,
                )
                stdin_payload = build_rdp_stdin_payload(session, password=password)
                return cmd, "RDP Client", prepare_rdp_launch_environment(), stdin_payload

            self._open_remote_viewer_tab(
                session,
                protocol_name="RDP",
                detached_command_builder=detached_builder,
                start_detached=True,
                runtime_only=runtime_only,
            )
            return

        def detached_builder() -> RemoteLaunchSpec:
            cmd = build_rdp_command(
                session,
                password=password,
                linux_trust_certificate=linux_trust_certificate,
            )
            stdin_payload = build_rdp_stdin_payload(session, password=password)
            return cmd, "FreeRDP", None, stdin_payload

        def embedded_builder(parent_window_id: int) -> RemoteLaunchSpec:
            cmd = build_rdp_command(
                session,
                password=password,
                linux_trust_certificate=linux_trust_certificate,
                linux_parent_window_id=parent_window_id,
            )
            stdin_payload = build_rdp_stdin_payload(session, password=password)
            return cmd, "FreeRDP", None, stdin_payload

        self._open_remote_viewer_tab(
            session,
            protocol_name="RDP",
            detached_command_builder=detached_builder,
            embedded_command_builder=embedded_builder,
            start_detached=self._start_detached_for_remote_session(session),
            runtime_only=runtime_only,
        )

    def _open_vnc_tab(
        self,
        session: Session,
        *,
        password: str | None,
        runtime_only: bool = False,
    ) -> None:
        system = platform.system().lower()

        if system == "windows":
            def detached_builder() -> RemoteLaunchSpec:
                return build_vnc_launch(
                    session,
                    password=password,
                    allow_install=False,
                )

            self._open_remote_viewer_tab(
                session,
                protocol_name="VNC",
                detached_command_builder=detached_builder,
                start_detached=self._start_detached_for_remote_session(session),
                runtime_only=runtime_only,
            )
            return

        def detached_builder() -> RemoteLaunchSpec:
            return build_vnc_launch(
                session,
                password=password,
                allow_install=False,
            )

        self._open_remote_viewer_tab(
            session,
            protocol_name="VNC",
            detached_command_builder=detached_builder,
            start_detached=self._start_detached_for_remote_session(session),
            runtime_only=runtime_only,
        )

    def _open_nomachine_tab(self, session: Session, *, runtime_only: bool = False) -> None:
        def detached_builder() -> RemoteLaunchSpec:
            return build_nomachine_launch(session)

        self._open_remote_viewer_tab(
            session,
            protocol_name="NoMachine",
            detached_command_builder=detached_builder,
            start_detached=True,
            runtime_only=runtime_only,
        )

    def _connect_session(
        self,
        session: Session,
        *,
        password_override: str | None = None,
        runtime_only: bool = False,
    ) -> None:
        session_label = _session_display_name(session)
        if session.protocol == Protocol.RDP:
            if not self._ensure_protocol_dependency(session.protocol):
                return
            try:
                password = (
                    password_override
                    if password_override is not None
                    else (self._credential_service.load_password(session) if session.save_password else None)
                )
                system = platform.system().lower()
                if system in {"linux", "darwin"}:
                    if not self._confirm_linux_rdp_certificate_trust(session):
                        self.statusBar().showMessage(
                            "RDP launch cancelled: certificate was not accepted.",
                            5000,
                        )
                        return
                    password, password_ok = self._resolve_linux_rdp_password(
                        session,
                        password,
                        allow_save=not runtime_only,
                    )
                    if not password_ok:
                        self.statusBar().showMessage(
                            "RDP launch cancelled: password was not provided.",
                            5000,
                        )
                        return
                    try:
                        clear_linux_rdp_known_host(session)
                    except OSError:
                        # Best-effort cleanup: stale FreeRDP host keys can force hidden terminal prompts.
                        pass
                    if runtime_only:
                        self._open_rdp_tab(
                            session,
                            password=password,
                            linux_trust_certificate=True,
                            runtime_only=True,
                        )
                    else:
                        self._open_rdp_tab(
                            session,
                            password=password,
                            linux_trust_certificate=True,
                        )
                    return
                if system == "windows":
                    if runtime_only:
                        self._open_rdp_tab(
                            session,
                            password=password,
                            linux_trust_certificate=False,
                            runtime_only=True,
                        )
                    else:
                        self._open_rdp_tab(
                            session,
                            password=password,
                            linux_trust_certificate=False,
                        )
                    return
                launch_rdp(session, password=password)
                self.statusBar().showMessage(f"RDP launched for {session_label}", 5000)
            except Exception as exc:
                QMessageBox.critical(self, "Connection Error", str(exc))
            return

        if session.protocol == Protocol.VNC:
            if not self._ensure_protocol_dependency(session.protocol):
                return
            try:
                password = (
                    password_override
                    if password_override is not None
                    else (self._credential_service.load_password(session) if session.save_password else None)
                )
                if platform.system().lower() in {"linux", "windows"}:
                    if runtime_only:
                        self._open_vnc_tab(session, password=password, runtime_only=True)
                    else:
                        self._open_vnc_tab(session, password=password)
                    return
                self._launch_vnc_session(session, password=password)
            except Exception as exc:
                QMessageBox.critical(self, "Connection Error", str(exc))
            return

        if session.protocol == Protocol.NOMACHINE:
            if not self._ensure_protocol_dependency(session.protocol):
                return
            try:
                if platform.system().lower() in {"linux", "windows", "darwin"}:
                    if runtime_only:
                        self._open_nomachine_tab(session, runtime_only=True)
                    else:
                        self._open_nomachine_tab(session)
                    return
                viewer = launch_nomachine(session)
                self.statusBar().showMessage(f"NoMachine launched for {session_label} ({viewer})", 5000)
            except Exception as exc:
                QMessageBox.critical(self, "Connection Error", str(exc))
            return

        if session.protocol == Protocol.SFTP:
            if runtime_only:
                self._open_sftp_tab(
                    session,
                    password_override=password_override,
                    runtime_only=True,
                )
            else:
                self._open_sftp_tab(
                    session,
                    password_override=password_override,
                )
            return

        if session.protocol == Protocol.SSH:
            if runtime_only:
                self._open_ssh_tab(
                    session,
                    password_override=password_override,
                    runtime_only=True,
                )
            else:
                self._open_ssh_tab(
                    session,
                    password_override=password_override,
                )
            return

        if session.protocol == Protocol.TELNET:
            if runtime_only:
                self._open_telnet_tab(session, runtime_only=True)
            else:
                self._open_telnet_tab(session)
            return

        if session.protocol == Protocol.SERIAL:
            if runtime_only:
                self._open_serial_tab(session, runtime_only=True)
            else:
                self._open_serial_tab(session)
            return

        QMessageBox.warning(self, "Unsupported", f"Unsupported protocol: {session.protocol.value.upper()}.")

    def _launch_vnc_session(self, session: Session, *, password: str | None) -> None:
        try:
            viewer = launch_vnc(session, password=password, allow_install=False)
            self.statusBar().showMessage(f"VNC launched for {session.host} ({viewer})", 5000)
        except Exception as exc:
            QMessageBox.critical(self, "Connection Error", str(exc))

    @staticmethod
    def _default_private_key_path(session: Session) -> str:
        configured = session.private_key_path.strip()
        if configured:
            return str(Path(configured).expanduser())
        return str((Path.home() / ".ssh" / "id_ed25519").expanduser())

    @staticmethod
    def _default_ssh_key_comment(session: Session) -> str:
        user = session.username.strip() or os.environ.get("USER", "").strip() or os.environ.get("USERNAME", "").strip()
        host = session.host.strip() or platform.node().strip() or "host"
        return f"{user or 'snakesh'}@{host}"

    def _persist_session_key_paths(self, session: Session, *, private_key_path: str, public_key_path: str) -> None:
        session.private_key_path = str(Path(private_key_path).expanduser())
        session.public_key_path = str(Path(public_key_path).expanduser())
        session.use_key_auth = True
        self._session_service.add_or_update(session)
        self._update_details()

    def _generate_ssh_key_pair_for_session(self, session: Session) -> str | None:
        if not self._ensure_dependency("ssh-keygen"):
            return None
        private_key_path, ok = QInputDialog.getText(
            self,
            "Generate SSH Key Pair",
            "Private key path:",
            QLineEdit.Normal,
            self._default_private_key_path(session),
        )
        if not ok or not private_key_path.strip():
            return None

        private_path = Path(private_key_path.strip()).expanduser()
        if private_path.exists():
            QMessageBox.warning(
                self,
                "Key Exists",
                (
                    f"The private key path already exists:\n{private_path}\n\n"
                    "Choose a different path or use Import Key Pair."
                ),
            )
            return None

        try:
            private_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            QMessageBox.critical(self, "Generate Key Failed", f"Unable to create key folder: {exc}")
            return None

        command = [
            "ssh-keygen",
            "-t",
            "ed25519",
            "-f",
            str(private_path),
            "-N",
            "",
            "-C",
            self._default_ssh_key_comment(session),
        ]
        try:
            result = subprocess.run(command, capture_output=True, text=True, check=False)
        except Exception as exc:
            QMessageBox.critical(self, "Generate Key Failed", str(exc))
            return None

        if result.returncode != 0:
            details = result.stderr.strip() or result.stdout.strip() or "ssh-keygen failed."
            QMessageBox.critical(self, "Generate Key Failed", details)
            return None

        public_path = Path(f"{private_path}.pub")
        valid, message = validate_public_key_file(public_path)
        if not valid:
            QMessageBox.critical(self, "Generate Key Failed", message)
            return None

        self._persist_session_key_paths(
            session,
            private_key_path=str(private_path),
            public_key_path=str(public_path),
        )
        QMessageBox.information(
            self,
            "Key Pair Generated",
            f"Generated key pair:\nPrivate: {private_path}\nPublic: {public_path}",
        )
        return str(public_path)

    def _import_ssh_key_pair_for_session(self, session: Session) -> str | None:
        private_start = session.private_key_path.strip() or str((Path.home() / ".ssh").expanduser())
        private_path_raw, _ = QFileDialog.getOpenFileName(
            self,
            "Select Private Key",
            private_start,
            "Private Keys (*.pem *.key *.ppk id_*);;All Files (*)",
        )
        if not private_path_raw:
            return None

        private_path = Path(private_path_raw).expanduser()
        if not private_path.exists() or not private_path.is_file():
            QMessageBox.warning(self, "Import Failed", f"Private key file not found: {private_path}")
            return None

        suggested_public = Path(f"{private_path}.pub")
        public_start = session.public_key_path.strip()
        if not public_start:
            public_start = str(suggested_public if suggested_public.exists() else private_path.parent)
        public_path_raw, _ = QFileDialog.getOpenFileName(
            self,
            "Select Public Key",
            public_start,
            "Public Keys (*.pub);;All Files (*)",
        )
        if not public_path_raw:
            return None

        public_path = Path(public_path_raw).expanduser()
        valid, message = validate_public_key_file(public_path)
        if not valid:
            QMessageBox.warning(self, "Import Failed", message)
            return None

        self._persist_session_key_paths(
            session,
            private_key_path=str(private_path),
            public_key_path=str(public_path),
        )
        QMessageBox.information(
            self,
            "Key Pair Imported",
            f"Imported key pair:\nPrivate: {private_path}\nPublic: {public_path}",
        )
        return str(public_path)

    def _resolve_or_prepare_public_key(self, session: Session) -> str | None:
        existing = resolve_existing_public_key(session)
        if existing is not None:
            valid, message = validate_public_key_file(existing)
            if valid:
                return str(existing)
            QMessageBox.warning(self, "Invalid Public Key", message)

        chooser = QMessageBox(self)
        chooser.setWindowTitle("No SSH Public Key Found")
        chooser.setIcon(QMessageBox.Warning)
        chooser.setText("No SSH public key was found for this session.")
        chooser.setInformativeText(
            "Generate a new key pair now, or import an existing private/public key pair."
        )
        generate_button = chooser.addButton("Generate Key Pair", QMessageBox.AcceptRole)
        import_button = chooser.addButton("Import Key Pair", QMessageBox.ActionRole)
        chooser.addButton(QMessageBox.Cancel)
        chooser.setDefaultButton(generate_button)
        chooser.exec()

        clicked = chooser.clickedButton()
        if clicked is generate_button:
            return self._generate_ssh_key_pair_for_session(session)
        if clicked is import_button:
            return self._import_ssh_key_pair_for_session(session)
        return None

    def _install_public_key_for_session(self, session: Session) -> None:
        if session.protocol not in (Protocol.SSH, Protocol.SFTP):
            QMessageBox.warning(self, "Unsupported", "Public key install is available for SSH/SFTP sessions.")
            return

        public_key_path = self._resolve_or_prepare_public_key(session)
        if not public_key_path:
            return

        password = self._credential_service.load_password(session) if session.save_password else None
        trust_unknown = False
        save_password_on_success = False
        while True:
            try:
                if not password:
                    password, remember_password = self._prompt_password(session)
                    if not password:
                        return
                    save_password_on_success = save_password_on_success or remember_password
                asyncio.run(
                    self._ssh.install_public_key(
                        session,
                        public_key_path=public_key_path,
                        password=password,
                        trust_unknown=trust_unknown,
                    )
                )
                if save_password_on_success:
                    self._enable_password_save_for_session(session)
                if (session.save_password or save_password_on_success) and password:
                    self._persist_session_password(session, password)
                QMessageBox.information(self, "Success", "Public key installed to remote authorized_keys.")
                return
            except Exception as exc:
                if self._is_host_key_error(exc) and not trust_unknown:
                    if self._prompt_trust_host_key(session):
                        trust_unknown = True
                        continue
                    return
                if self._is_auth_error(exc):
                    password, remember_password = self._prompt_password(session)
                    if not password:
                        return
                    save_password_on_success = save_password_on_success or remember_password
                    continue
                QMessageBox.critical(self, "Install Key Failed", str(exc))
                return

    def _delete_selected_tree_items(self, session_ids: list[str], folder_paths: list[str]) -> None:
        session_snapshot = {session.id: session for session in self._session_service.all()}
        session_ids_set = set(session_ids)
        folder_paths_sorted = sorted(
            {self._session_service.normalize_folder_path(folder) for folder in folder_paths},
            key=lambda folder: (folder.count("/"), folder.lower()),
        )
        # Remove nested folders when a parent is already selected.
        normalized_folders: list[str] = []
        for folder in folder_paths_sorted:
            if any(folder == parent or folder.startswith(f"{parent}/") for parent in normalized_folders):
                continue
            normalized_folders.append(folder)

        folder_session_ids: set[str] = set()
        for folder in normalized_folders:
            for session in self._session_service.sessions_in_folder(folder, recursive=True):
                folder_session_ids.add(session.id)

        direct_session_ids = [session_id for session_id in session_ids_set if session_id not in folder_session_ids]
        total_sessions_to_delete = len(folder_session_ids) + len(direct_session_ids)
        total_folders_to_delete = len(normalized_folders)
        if total_sessions_to_delete == 0 and total_folders_to_delete == 0:
            return

        lines: list[str] = []
        if total_folders_to_delete:
            lines.append(f"You are deleting {total_folders_to_delete} folder(s).")
            if total_sessions_to_delete:
                lines.append(
                    f"All saved sessions in those folders and subfolders will also be deleted ({total_sessions_to_delete} session(s))."
                )
        else:
            lines.append(f"You are deleting {total_sessions_to_delete} session(s).")
        lines.append("")
        lines.append("This action cannot be undone.")
        answer = QMessageBox.question(
            self,
            "Confirm Delete",
            "\n".join(lines),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        deleted_session_ids: set[str] = set()
        for folder in sorted(normalized_folders, key=lambda value: (value.count("/"), value.lower()), reverse=True):
            ids = self._session_service.delete_folder(folder)
            deleted_session_ids.update(ids)

        if direct_session_ids:
            deleted = self._session_service.delete_many(direct_session_ids)
            deleted_session_ids.update(deleted)

        for session_id in deleted_session_ids:
            session = session_snapshot.get(session_id)
            if session:
                self._credential_service.clear_password(session)
            self._close_session_tab(session_id)

        self._refresh_tree()
        self.details.clear()
        if total_folders_to_delete:
            message = f"Deleted {total_folders_to_delete} folder(s) and {len(deleted_session_ids)} session(s)."
        else:
            message = f"Deleted {len(deleted_session_ids)} session(s)."
        self.statusBar().showMessage(message, 7000)

    def _update_details(self) -> None:
        session = self._current_session()
        if not session:
            self.details.clear()
            return
        tags = ", ".join(session.tags) if session.tags else "(none)"
        display_resolution = "Auto" if is_auto_resolution(session.display_resolution) else (session.display_resolution or "Default")
        display_color = f"{session.display_color_depth}-bit" if session.display_color_depth else "Default"
        display_fullscreen = "Yes" if session.display_fullscreen else "No"
        launch_mode = normalize_remote_launch_mode(session.remote_launch_mode)
        launch_mode_label = "Detached window" if launch_mode == "detached" else "In tab"
        rdp_audio_mode = normalize_rdp_audio_mode(session.rdp_audio_mode)
        rdp_audio_label = {
            "local": "On this computer",
            "remote": "On remote computer",
            "mute": "Do not play",
        }.get(rdp_audio_mode, "On this computer")
        dynamic_tunnel_enabled = sum(1 for tunnel in session.ssh_dynamic_tunnels if tunnel.enabled)
        static_tunnel_enabled = sum(1 for tunnel in session.ssh_static_tunnels if tunnel.enabled)
        endpoint_label = "Device" if session.protocol == Protocol.SERIAL else "Host"
        username_label = "(not used)" if session.protocol == Protocol.SERIAL else (session.username or "(not set)")
        supports_automation = session.protocol in (Protocol.SSH, Protocol.TELNET, Protocol.SERIAL)
        lines = [
            f"Name: {session.name}",
            f"Folder: {session.folder}",
            f"Protocol: {session.protocol.value.upper()}",
            f"{endpoint_label}: {_session_endpoint_text(session)}",
            f"Username: {username_label}",
        ]
        if session.protocol in (Protocol.SSH, Protocol.SFTP):
            lines.extend(
                [
                    f"Auth Mode: {'Key' if session.use_key_auth else 'Password'}",
                    f"Password Saved: {'Yes' if session.save_password else 'No'}",
                    f"Private Key Path: {session.private_key_path or '(not set)'}",
                    f"Public Key Path: {session.public_key_path or '(auto-discover)'}",
                    f"Default SFTP Local Folder: {session.sftp_local_folder}",
                    f"Default SFTP Remote Folder: {session.sftp_remote_folder}",
                    f"X11 Forwarding: {'Enabled' if session.x11_forwarding else 'Disabled'}",
                    f"SSH Keepalive: {'Enabled (30s interval)' if session.ssh_keepalive else 'Disabled'}",
                    f"Legacy SSH Compatibility: {'Enabled' if session.ssh_legacy_compatibility else 'Auto'}",
                    (
                        "SSH Tunnels: "
                        f"Dynamic {dynamic_tunnel_enabled}/{len(session.ssh_dynamic_tunnels)}, "
                        f"Static {static_tunnel_enabled}/{len(session.ssh_static_tunnels)}"
                    ),
                ]
            )
        elif session.protocol == Protocol.RDP:
            if platform.system().lower() == "linux":
                open_mode_line = f"Open Mode: {launch_mode_label}"
            else:
                open_mode_line = "Open Mode: Detached window only"
            lines.extend(
                [
                    f"Domain: {session.domain or '(none)'}",
                    f"Password Saved: {'Yes' if session.save_password else 'No'}",
                    f"Resolution: {display_resolution}",
                    f"Fullscreen: {display_fullscreen}",
                    f"Color Depth: {display_color}",
                    open_mode_line,
                    f"Audio: {rdp_audio_label}",
                ]
            )
        elif session.protocol == Protocol.VNC:
            lines.extend(
                [
                    "Auth: Managed by the selected VNC viewer.",
                    f"Password Saved: {'Yes' if session.save_password else 'No'}",
                    f"Resolution: {display_resolution}",
                    f"Fullscreen: {display_fullscreen}",
                    f"Color Depth: {display_color}",
                    f"Dynamic Resize: {'Enabled' if session.vnc_allow_resize else 'Disabled'}",
                    "Open Mode: Detached window only",
                ]
            )
        elif session.protocol == Protocol.NOMACHINE:
            resize_mode_label = (
                "Viewport (scroll)"
                if session.nomachine_physical_desktop_resize_mode == "viewport"
                else "Scale to fit"
            )
            quality_labels = {
                0: "Lowest (fastest)",
                1: "Very low",
                2: "Low",
                3: "Lower-medium",
                4: "Medium-low",
                5: "Balanced",
                6: "Medium-high",
                7: "High",
                8: "Very high",
                9: "Highest (best image)",
            }
            link_quality_label = quality_labels.get(session.nomachine_link_quality, "Custom")
            video_quality_label = quality_labels.get(session.nomachine_video_quality, "Custom")
            lines.extend(
                [
                    "Auth: Managed by the NoMachine client.",
                    "Open Mode: Detached window only",
                    f"Audio Streaming: {'Enabled' if session.nomachine_audio_enabled else 'Disabled'}",
                    (
                        "Mute Audio On Remote: Yes"
                        if session.nomachine_audio_enabled and session.nomachine_mute_remote_audio
                        else "Mute Audio On Remote: No"
                    ),
                    f"Auto Resize Physical Desktop: {'Enabled' if session.nomachine_physical_desktop_auto_resize else 'Disabled'}",
                    f"Resize Mode: {resize_mode_label}",
                    f"Link Quality: {session.nomachine_link_quality} ({link_quality_label})",
                    f"Video Quality: {session.nomachine_video_quality} ({video_quality_label})",
                ]
            )
        elif session.protocol == Protocol.TELNET:
            transport_label = "TLS" if session.telnet_use_tls else "Plaintext"
            lines.extend(
                [
                    "Auth: Interactive login handled by the remote Telnet endpoint.",
                    f"Transport: {transport_label}",
                    (
                        f"TLS Certificate Validation: {'Enabled' if session.telnet_tls_verify else 'Disabled'}"
                        if session.telnet_use_tls
                        else "TLS Certificate Validation: (not applicable)"
                    ),
                    f"Terminal Type: {session.telnet_terminal_type}",
                    f"Connect Timeout: {session.telnet_connect_timeout_seconds:g}s",
                ]
            )
        elif session.protocol == Protocol.SERIAL:
            parity_label = {
                "none": "None",
                "even": "Even",
                "odd": "Odd",
                "mark": "Mark",
                "space": "Space",
            }.get(session.serial_parity, session.serial_parity)
            flow_label = {
                "none": "None",
                "rtscts": "RTS/CTS (Hardware)",
                "xonxoff": "XON/XOFF (Software)",
                "dsrdtr": "DSR/DTR",
            }.get(session.serial_flow_control, session.serial_flow_control)
            lines.extend(
                [
                    f"Baud Rate: {session.serial_baud_rate}",
                    f"Data Bits: {session.serial_data_bits}",
                    f"Parity: {parity_label}",
                    f"Stop Bits: {session.serial_stop_bits}",
                    f"Flow Control: {flow_label}",
                    f"Terminal Type: {session.serial_terminal_type}",
                ]
            )

        if supports_automation:
            lines.append(
                "Automated Scripting: "
                f"{'Enabled' if session.ssh_automation_enabled else 'Disabled'} "
                f"({len(session.ssh_automation_steps)} step(s))"
            )
        if session.terminal_color_override_enabled:
            lines.append(
                "Terminal Color Override: "
                f"Background {session.terminal_bg_color or '(default)'} / "
                f"Foreground {session.terminal_fg_color or '(default)'}"
            )
        if session.protocol in (Protocol.SSH, Protocol.TELNET, Protocol.SERIAL) and session.shell_banner_message:
            lines.extend(
                [
                    f"Shell Banner: {session.shell_banner_message}",
                    f"Shell Banner Color: {session.shell_banner_color or '#f59e0b'}",
                    f"Shell Banner Blink: {'Yes' if session.shell_banner_blink else 'No'}",
                ]
            )

        lines.extend(
            [
                f"Tags: {tags}",
                "",
                f"Notes:\n{session.notes or '(none)'}",
            ]
        )
        self.details.setPlainText("\n".join(lines))

    def _new_session(self, *, default_folder: str | None = None) -> None:
        dialog = SessionEditorDialog(parent=self)
        if default_folder and default_folder.strip():
            normalized = self._session_service.normalize_folder_path(default_folder)
            dialog.folder_input.setText(normalized)
        if dialog.exec():
            session = dialog.build_session()
            self._session_service.add_or_update(session)
            self._save_password_preference(session, dialog.password_text())
            self._refresh_tree()

    def _edit_session(self) -> None:
        session = self._current_session()
        if not session:
            QMessageBox.warning(self, "No Selection", "Select a session first.")
            return
        self._edit_specific_session(session)

    def _edit_specific_session(self, session: Session) -> None:
        dialog = SessionEditorDialog(
            parent=self,
            session=session,
            password_loader=self._credential_service.load_password,
        )
        if dialog.exec():
            updated = dialog.build_session()
            self._session_service.add_or_update(updated)
            self._save_password_preference(updated, dialog.password_text())
            self._refresh_tree()

    def _quick_connect_session(self) -> None:
        dialog = SessionEditorDialog(parent=self, quick_connect=True)
        if not dialog.exec():
            return
        session = dialog.build_session()
        password_override = dialog.password_text()
        self._connect_session(
            session,
            password_override=password_override if password_override else None,
            runtime_only=True,
        )

    def _notify_password_save_failure(
        self,
        error_message: str | None,
        *,
        title: str = "Password Save Failed",
        show_dialog: bool = False,
    ) -> None:
        summary = "Password was not saved in the configured secrets backend."
        details = (error_message or "Unknown secrets backend error.").strip() or "Unknown secrets backend error."
        self.statusBar().showMessage(summary, 8000)
        if show_dialog:
            QMessageBox.warning(self, title, f"{summary}\n\nDetails:\n{details}")

    def _persist_session_password(
        self,
        session: Session,
        password: str,
        *,
        title: str = "Password Save Failed",
        show_dialog: bool = False,
    ) -> bool:
        saved, error_message = self._credential_service.save_password(session, password)
        if saved:
            return True
        self._notify_password_save_failure(error_message, title=title, show_dialog=show_dialog)
        return False

    def _save_password_preference(self, session: Session, raw_password: str) -> None:
        if session.save_password and raw_password:
            self._persist_session_password(
                session,
                raw_password,
                title="Password Save Failed",
                show_dialog=True,
            )
        if not session.save_password:
            self._credential_service.clear_password(session)

    def _is_saved_session(self, session: Session | None) -> bool:
        if session is None:
            return False
        session_id = session.id.strip()
        if not session_id:
            return False
        lookup = getattr(self._session_service, "by_id", None)
        if not callable(lookup):
            return False
        return lookup(session_id) is not None

    def _delete_session(self) -> None:
        selected_session_ids = self._selected_session_ids()
        selected_folder_paths = self._selected_folder_paths()
        if not selected_session_ids and not selected_folder_paths:
            session = self._current_session()
            if session:
                selected_session_ids = [session.id]
        if not selected_session_ids and not selected_folder_paths:
            QMessageBox.warning(self, "No Selection", "Select sessions or folders first.")
            return
        self._delete_selected_tree_items(selected_session_ids, selected_folder_paths)

    def _connect_current(self) -> None:
        selected_sessions = self._selected_sessions()
        if not selected_sessions:
            current = self._current_session()
            if current is not None:
                selected_sessions = [current]
        if not selected_sessions:
            QMessageBox.warning(self, "No Selection", "Select a session first.")
            return
        self._connect_selected_sessions(selected_sessions)

    def _connect_selected_sessions(self, sessions: list[Session]) -> None:
        if not sessions:
            return
        if len(sessions) > 1:
            answer = QMessageBox.question(
                self,
                "Connect Multiple Sessions",
                (
                    f"You are about to open {len(sessions)} sessions at once.\n\n"
                    "Continue?"
                ),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
        remote_entries = sum(1 for session in sessions if self._session_uses_remote_viewer_queue(session))
        self._begin_connection_load_progress(
            title="Opening Sessions",
            total_entries=len(sessions),
            remote_entries=remote_entries,
        )
        opened = 0
        for session in sessions:
            label = f"{session.name or session.host or session.id} ({session.protocol.value.upper()})"
            if self._connection_load_canceled():
                break
            self._refresh_connection_load_message(f"Opening {label}...")
            self._pump_ui()
            if self._connection_load_canceled():
                break
            self._connect_session(session)
            opened += 1
            self._advance_connection_load_progress(f"Opened {label}.")
            self._pump_ui()
        if self._connection_load_canceled():
            self._cancel_pending_remote_viewer_starts()
            self._finish_connection_load_progress(
                final_message=f"Stopped after opening {opened} of {len(sessions)} session(s).",
                timeout_ms=7000,
                mark_complete=False,
            )
            return
        self._maybe_finish_connection_load_progress(f"Opened {opened} session(s).")

    def _open_local_shell_tab(self, *, existing_tab: TerminalTab | None = None) -> bool:
        launch = self._local_shell_launch_command(command_override=self._settings.local_shell_command_override)
        if launch is None:
            QMessageBox.critical(
                self,
                "Local Shell Error",
                "No supported local shell executable was found on this system.",
            )
            return False
        program, arguments = launch
        tab = existing_tab if existing_tab is not None else TerminalTab(settings=self._settings)
        command_preview = " ".join([program, *arguments]).strip()
        local_host = platform.node().strip() or "localhost"
        local_user = os.environ.get("USERNAME", "") or os.environ.get("USER", "")
        pseudo_session = Session(
            name="Local Shell",
            host=local_host,
            protocol=Protocol.SSH,
            port=22,
            username=local_user,
        )
        if existing_tab is None:
            self._add_session_tab(pseudo_session, tab, "LOCAL")
        else:
            self._prepare_existing_terminal_tab_for_session(tab, pseudo_session, "LOCAL")
            self._activate_session_tab(tab)
        self._append_terminal_status(tab, f"Starting local shell: {command_preview}")
        started = tab.start_local_shell(
            program=program,
            arguments=arguments,
            working_directory=self._resolve_local_shell_working_directory(),
        )
        if started:
            message = "Local shell reconnected." if existing_tab is not None else "Local shell tab opened."
            self.statusBar().showMessage(message, 5000)
            return True
        QMessageBox.critical(
            self,
            "Local Shell Error",
            f"Failed to start local shell using: {command_preview}",
        )
        if existing_tab is None:
            location = self._find_widget_location(tab)
            if location is not None:
                host, index = location
                self._close_tab_in_host(host, index)
        return False

    def _local_shell_launch_command(self, command_override: str = "") -> tuple[str, list[str]] | None:
        override = command_override.strip()
        if override:
            try:
                parts = shlex.split(override, posix=platform.system().lower() != "windows")
            except ValueError:
                parts = []
            if parts:
                executable = _strip_wrapping_quotes(parts[0]) if platform.system().lower() == "windows" else parts[0]
                args = parts[1:]
                resolved = (
                    _resolve_windows_local_shell_executable(executable)
                    if platform.system().lower() == "windows"
                    else shutil.which(executable)
                )
                if not resolved and Path(executable).exists():
                    resolved = executable
                if resolved and not (
                    platform.system().lower() == "windows"
                    and (
                        _is_windows_external_terminal_host(resolved)
                        or _is_windows_app_execution_alias(resolved)
                    )
                ):
                    return resolved, args

        system = platform.system().lower()
        if system == "windows":
            candidates: list[tuple[str, list[str]]] = [
                ("pwsh", ["-NoLogo", "-NoProfile"]),
                ("powershell", ["-NoLogo", "-NoProfile"]),
            ]
            comspec = os.environ.get("COMSPEC", "").strip()
            if comspec:
                candidates.append((comspec, []))
            candidates.append(("cmd.exe", []))
            for executable, args in candidates:
                resolved = _resolve_windows_local_shell_executable(executable)
                if not resolved and os.path.exists(executable):
                    resolved = executable
                if not resolved:
                    continue
                return resolved, args
            return None

        shell_candidates: list[str] = []
        shell_env = os.environ.get("SHELL", "").strip()
        if shell_env and Path(shell_env).exists():
            shell_candidates.append(shell_env)
        for executable in ("bash", "zsh", "sh"):
            resolved = shutil.which(executable)
            if resolved and resolved not in shell_candidates:
                shell_candidates.append(resolved)
        if not shell_candidates:
            return None

        shell_path = shell_candidates[0]
        shell_name = Path(shell_path).name.lower()
        interactive_shells = {"bash", "zsh", "sh", "dash", "ksh", "mksh", "ash"}
        interactive_args = ["-i"] if shell_name in interactive_shells else []
        return shell_path, interactive_args

    def _resolve_local_shell_working_directory(self) -> str:
        mode = self._settings.local_shell_start_dir_mode.strip().lower()
        home = Path.home()
        if mode == "cwd":
            try:
                return str(Path.cwd())
            except OSError:
                return str(home)
        if mode == "custom":
            custom = self._settings.local_shell_custom_start_dir.strip()
            if custom:
                candidate = Path(custom).expanduser()
                if candidate.is_dir():
                    return str(candidate)
            return str(home)
        return str(home)

    def _apply_session_shell_banner(self, tab: TerminalTab, session: Session) -> None:
        if session.protocol not in (Protocol.SSH, Protocol.TELNET, Protocol.SERIAL):
            tab.set_shell_banner(message="", color="", blink=False)
            return
        tab.set_shell_banner(
            message=session.shell_banner_message,
            color=session.shell_banner_color,
            blink=session.shell_banner_blink,
        )

    def _open_ssh_tab(
        self,
        session: Session,
        *,
        existing_tab: TerminalTab | None = None,
        password_override: str | None = None,
        runtime_only: bool = False,
    ) -> None:
        password = (
            password_override
            if password_override is not None
            else (self._credential_service.load_password(session) if session.save_password else None)
        )
        x11_forwarding = session.x11_forwarding
        if session.x11_forwarding:
            x11_prepared = self._prepare_local_x11_for_session(session)
            if x11_prepared is None:
                return
            x11_forwarding = x11_prepared

        tab = existing_tab if existing_tab is not None else TerminalTab(settings=self._settings)
        self._apply_session_shell_banner(tab, session)
        user_label = session.username or "(default user)"
        if existing_tab is None:
            self._add_session_tab(session, tab, "SSH", runtime_only=runtime_only)
            if not runtime_only:
                tab.open_sftp_requested.connect(self._open_sftp_for_session_id)
        else:
            self._prepare_existing_terminal_tab_for_session(tab, session, "SSH", runtime_only=runtime_only)
            self._activate_session_tab(tab)
        self._append_terminal_status(tab, f"Connecting to {session.host}:{session.port} as {user_label}...")
        self.statusBar().showMessage(f"Connecting SSH session: {_session_display_name(session)}", 5000)
        if runtime_only:
            self._run_ssh_probe(
                session=session,
                password=password,
                trust_unknown=False,
                x11_forwarding=x11_forwarding,
                tab=tab,
                allow_password_save=False,
            )
        else:
            self._run_ssh_probe(
                session=session,
                password=password,
                trust_unknown=False,
                x11_forwarding=x11_forwarding,
                tab=tab,
            )

    def _open_telnet_tab(
        self,
        session: Session,
        *,
        existing_tab: TerminalTab | None = None,
        runtime_only: bool = False,
    ) -> None:
        host = session.host.strip()
        if not host:
            QMessageBox.warning(self, "Invalid Session", "Telnet sessions require a host.")
            return
        port = session.port if 0 < session.port <= 65535 else SessionService.default_port_for(Protocol.TELNET)
        tab = existing_tab if existing_tab is not None else TerminalTab(settings=self._settings)
        self._apply_session_shell_banner(tab, session)
        transport = "Telnet/TLS" if session.telnet_use_tls else "Telnet"
        tls_suffix = ""
        if session.telnet_use_tls:
            tls_suffix = ", cert verify on" if session.telnet_tls_verify else ", cert verify off"
        if existing_tab is None:
            self._add_session_tab(session, tab, "TELNET", runtime_only=runtime_only)
        else:
            self._prepare_existing_terminal_tab_for_session(tab, session, "TELNET", runtime_only=runtime_only)
            self._activate_session_tab(tab)
        self._append_terminal_status(
            tab,
            (
                f"Connecting to {host}:{port} via {transport} "
                f"(terminal type: {session.telnet_terminal_type}{tls_suffix})..."
            ),
        )
        self.statusBar().showMessage(f"Connecting Telnet session: {_session_display_name(session)}", 5000)
        tab.start_telnet(session)

    def _open_serial_tab(
        self,
        session: Session,
        *,
        existing_tab: TerminalTab | None = None,
        runtime_only: bool = False,
    ) -> None:
        endpoint = session.host.strip()
        if not endpoint:
            QMessageBox.warning(self, "Invalid Session", "Serial sessions require a serial port/device path.")
            return
        tab = existing_tab if existing_tab is not None else TerminalTab(settings=self._settings)
        self._apply_session_shell_banner(tab, session)
        if existing_tab is None:
            self._add_session_tab(session, tab, "SERIAL", runtime_only=runtime_only)
        else:
            self._prepare_existing_terminal_tab_for_session(tab, session, "SERIAL", runtime_only=runtime_only)
            self._activate_session_tab(tab)
        self._append_terminal_status(
            tab,
            (
                f"Opening serial device {endpoint} at {session.serial_baud_rate} baud "
                f"({session.serial_data_bits}{session.serial_parity[0].upper() if session.serial_parity else 'N'}"
                f"{session.serial_stop_bits}, terminal type: {session.serial_terminal_type})..."
            ),
        )
        self.statusBar().showMessage(f"Opening serial session: {_session_display_name(session)}", 5000)
        tab.start_serial(session)

    def _run_ssh_probe(
        self,
        *,
        session: Session,
        password: str | None,
        trust_unknown: bool,
        x11_forwarding: bool,
        tab: TerminalTab,
        save_password_on_success: bool = False,
        allow_password_save: bool = True,
    ) -> None:
        connect_session = Session.from_dict(session.to_dict())
        connect_session.x11_forwarding = x11_forwarding

        probe_id = self._next_ssh_probe_id
        self._next_ssh_probe_id += 1
        thread = QThread(self)
        worker = SSHProbeWorker(
            connect_session,
            password=password,
            trust_unknown=trust_unknown,
            probe_id=probe_id,
        )
        self._ssh_probe_contexts[probe_id] = SSHProbeContext(
            session=session,
            password=password,
            trust_unknown=trust_unknown,
            x11_forwarding=x11_forwarding,
            tab=tab,
            save_password_on_success=save_password_on_success,
            allow_password_save=allow_password_save,
        )
        self._ssh_probe_threads_by_id[probe_id] = thread
        self._ssh_probe_workers_by_id[probe_id] = worker
        watchdog = QTimer(self)
        watchdog.setSingleShot(True)
        watchdog.timeout.connect(lambda pid=probe_id: self._on_ssh_probe_watchdog_timeout(pid))
        self._ssh_probe_watchdogs_by_id[probe_id] = watchdog
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._handle_ssh_probe_finished, Qt.QueuedConnection)
        worker.finished.connect(worker._schedule_delete, Qt.DirectConnection)
        worker.finished.connect(thread.quit, Qt.DirectConnection)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_ssh_probe_thread_finished, Qt.QueuedConnection)
        self._ssh_probe_threads.add(thread)
        thread.start()
        watchdog.start((SSHProbeWorker.PROBE_TIMEOUT_SECONDS + 5) * 1000)

    def _on_ssh_probe_watchdog_timeout(self, probe_id: int) -> None:
        if probe_id not in self._ssh_probe_contexts:
            return
        worker = self._ssh_probe_workers_by_id.get(probe_id)
        if worker is not None:
            try:
                worker.cancel()
            except Exception:
                pass
        thread = self._ssh_probe_threads_by_id.get(probe_id)
        if thread is not None:
            try:
                if thread.isRunning():
                    thread.quit()
                    thread.wait(500)
            except RuntimeError:
                pass
        self._handle_ssh_probe_finished(
            probe_id,
            False,
            "",
            "Connection attempt timed out during SSH negotiation.",
            False,
            False,
        )

    @Slot(int, bool, str, str, bool, bool)
    def _handle_ssh_probe_finished(
        self,
        probe_id: int,
        ok: bool,
        status: str,
        error_message: str,
        host_key_error: bool,
        auth_error: bool,
    ) -> None:
        watchdog = self._ssh_probe_watchdogs_by_id.pop(probe_id, None)
        if watchdog is not None:
            watchdog.stop()
            watchdog.deleteLater()
        self._ssh_probe_workers_by_id.pop(probe_id, None)
        thread = self._ssh_probe_threads_by_id.pop(probe_id, None)
        if thread is not None:
            try:
                if thread.isRunning():
                    thread.quit()
            except RuntimeError:
                pass

        context = self._ssh_probe_contexts.pop(probe_id, None)
        if context is None:
            return

        session = context.session
        password = context.password
        trust_unknown = context.trust_unknown
        x11_forwarding = context.x11_forwarding
        tab = context.tab
        save_password_on_success = context.save_password_on_success
        allow_password_save = context.allow_password_save

        try:
            location = self._find_widget_location(tab)
        except RuntimeError:
            return
        if location is None:
            return
        if self._is_shutting_down:
            return

        if ok:
            legacy_mode_used = self._status_uses_legacy_compatibility(status)
            if legacy_mode_used:
                self._prompt_to_persist_legacy_compatibility(session)
            if allow_password_save and save_password_on_success:
                self._enable_password_save_for_session(session)
            if allow_password_save and (session.save_password or save_password_on_success) and password:
                self._persist_session_password(session, password)
            self._append_terminal_status(tab, f"Connectivity verified ({status}). Starting interactive shell...")
            tab.start_shell(
                session=session,
                password=password,
                trust_unknown=trust_unknown,
                x11_forwarding=x11_forwarding,
            )
            self.statusBar().showMessage(f"SSH connected: {_session_display_name(session)}", 5000)
            return

        if host_key_error and not trust_unknown:
            self._append_terminal_status(tab, "Host key is not trusted. Confirmation is required before retrying.")
            if self._prompt_trust_host_key(session):
                tab._mark_connection_closed(False)
                self._append_terminal_status(tab, "Host key trusted. Retrying connection...")
                self._run_ssh_probe(
                    session=session,
                    password=password,
                    trust_unknown=True,
                    x11_forwarding=x11_forwarding,
                    tab=tab,
                    save_password_on_success=save_password_on_success,
                    **({"allow_password_save": False} if not allow_password_save else {}),
                )
            else:
                tab._mark_connection_closed(True)
                self._append_terminal_status(tab, "Connection canceled: host key was not trusted.")
            return

        if auth_error:
            self._append_terminal_status(tab, "Authentication failed. Enter credentials to retry.")
            prompted_password, remember_password = self._prompt_password(
                session,
                allow_save=allow_password_save,
            )
            if prompted_password:
                tab._mark_connection_closed(False)
                self._append_terminal_status(tab, "Retrying with updated credentials...")
                self._run_ssh_probe(
                    session=session,
                    password=prompted_password,
                    trust_unknown=trust_unknown,
                    x11_forwarding=x11_forwarding,
                    tab=tab,
                    save_password_on_success=save_password_on_success or remember_password,
                    **({"allow_password_save": False} if not allow_password_save else {}),
                )
            else:
                tab._mark_connection_closed(True)
                self._append_terminal_status(tab, "Connection canceled: credentials were not provided.")
            return

        friendly = self._friendly_ssh_connection_error(session, error_message)
        tab._mark_connection_closed(True)
        self._append_terminal_status(tab, f"Connection failed.\n{friendly}")
        QMessageBox.critical(self, "Connection Error", friendly)
        self.statusBar().showMessage(f"SSH connection failed: {_session_display_name(session)}", 7000)

    def _append_terminal_status(self, tab: TerminalTab, text: str) -> None:
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        if not normalized.endswith("\n"):
            normalized += "\n"
        tab.append(normalized.replace("\n", "\r\n"))

    @Slot()
    def _on_ssh_probe_thread_finished(self) -> None:
        thread = self.sender()
        if not isinstance(thread, QThread):
            return
        self._ssh_probe_threads.discard(thread)
        stale_ids = [probe_id for probe_id, probe_thread in self._ssh_probe_threads_by_id.items() if probe_thread is thread]
        for probe_id in stale_ids:
            self._ssh_probe_threads_by_id.pop(probe_id, None)
            self._ssh_probe_workers_by_id.pop(probe_id, None)
            self._ssh_probe_contexts.pop(probe_id, None)
            watchdog = self._ssh_probe_watchdogs_by_id.pop(probe_id, None)
            if watchdog is not None:
                watchdog.stop()
                watchdog.deleteLater()

    @staticmethod
    def _friendly_ssh_connection_error(session: Session, raw_message: str) -> str:
        details = raw_message.strip() or "Unknown SSH connection error."
        lowered = details.lower()
        endpoint = f"{session.host}:{session.port}"

        if "winerror 121" in lowered or "semaphore timeout period has expired" in lowered:
            reason = (
                "Connection timed out while waiting for a network response.\n"
                "Check VPN/firewall access and that the host/port are reachable."
            )
        elif "timed out" in lowered:
            reason = (
                "Connection timed out before SSH handshake completed.\n"
                "Check network reachability and firewall rules."
            )
        elif (
            "no matching key exchange" in lowered
            or "no matching host key" in lowered
            or "no matching cipher" in lowered
            or "no matching mac" in lowered
            or "key exchange failed" in lowered
        ):
            reason = (
                "SSH algorithm negotiation failed.\n"
                "The server may only support legacy SSH ciphers, host keys, or key-exchange algorithms."
            )
        elif "connection refused" in lowered:
            reason = "The remote host refused the connection. SSH service may be down or listening on a different port."
        elif "name or service not known" in lowered or "nodename nor servname provided" in lowered:
            reason = "The host name could not be resolved. Verify DNS/host spelling."
        elif "no route to host" in lowered or "network is unreachable" in lowered:
            reason = "No route to host. Check network path/VPN and gateway configuration."
        else:
            reason = "Unable to establish an SSH connection."

        return f"Could not connect to {endpoint}.\n\n{reason}\n\nDetails:\n{details}"

    @staticmethod
    def _status_uses_legacy_compatibility(status: str) -> bool:
        return "legacy algorithm compatibility mode" in status.lower()

    def _prompt_to_persist_legacy_compatibility(self, session: Session) -> None:
        if session.protocol not in (Protocol.SSH, Protocol.SFTP):
            return
        if not self._is_saved_session(session):
            return
        if session.ssh_legacy_compatibility:
            return
        if session.id in self._legacy_compat_prompted_session_ids:
            return
        self._legacy_compat_prompted_session_ids.add(session.id)
        answer = QMessageBox.question(
            self,
            "Legacy SSH Compatibility Detected",
            (
                f"{_session_display_name(session)} connected using legacy SSH algorithms.\n\n"
                "Enable legacy compatibility mode for this session to use that profile by default?"
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if answer != QMessageBox.Yes:
            return
        session.ssh_legacy_compatibility = True
        self._session_service.add_or_update(session)
        self.statusBar().showMessage(
            f"Enabled legacy SSH compatibility for {_session_display_name(session)}.",
            7000,
        )
        self._update_details()

    def _open_sftp_for_session_id(self, session_id: str) -> None:
        session = self._session_service.by_id(session_id)
        if not session:
            QMessageBox.warning(self, "Session Missing", "Unable to find the session for this terminal tab.")
            return
        if session.protocol not in (Protocol.SSH, Protocol.SFTP):
            QMessageBox.warning(self, "Unsupported", "SFTP can only be opened for SSH/SFTP sessions.")
            return
        self._open_sftp_tab(session)

    def _open_sftp_tab(
        self,
        session: Session,
        *,
        password_override: str | None = None,
        runtime_only: bool = False,
    ) -> None:
        connect_result = self._connect_sftp_with_prompts(
            session,
            password_override=password_override,
            runtime_only=runtime_only,
        )
        if not connect_result:
            return
        remote_dir, password = connect_result

        tab = SFTPSessionTab(
            session=session,
            sftp=self._sftp,
            initial_remote_dir=remote_dir,
            initial_remote_entries=None,
            initial_local_dir=session.sftp_local_folder,
            password=password,
            execute_remote=self._execute_sftp_operation,
            status_callback=self.statusBar().showMessage,
            should_confirm_delete=lambda: self._settings.warn_before_file_delete,
            should_confirm_overwrite=lambda: self._settings.warn_before_file_overwrite,
        )
        self._add_session_tab(session, tab, "SFTP", runtime_only=runtime_only)
        tab.load_remote_directory(remote_dir)
        self.statusBar().showMessage(f"SFTP connected: {_session_display_name(session)}", 5000)

    def _prepare_local_x11_for_session(self, session: Session) -> bool | None:
        system = platform.system().lower()
        if system == "linux":
            if self._ensure_dependency("xauth"):
                return True
            continue_answer = QMessageBox.question(
                self,
                "Continue Without X11",
                (
                    "xauth is required for X11 forwarding on Linux.\n\n"
                    "Continue this SSH connection with X11 forwarding disabled?"
                ),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if continue_answer == QMessageBox.Yes:
                return False
            return None
        if system != "windows":
            return True

        ready, message = self._x11_service.ensure_windows_x_server(allow_install=False)
        if ready:
            self.statusBar().showMessage(message, 4000)
            return True

        install_answer = QMessageBox.question(
            self,
            "X11 Server Required",
            (
                "X11 forwarding is enabled for this SSH session, but no local X server is available.\n\n"
                "SnakeSh can install and launch VcXsrv now.\n\n"
                "Install and launch VcXsrv?"
            ),
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
            QMessageBox.Yes,
        )
        if install_answer == QMessageBox.Cancel:
            return None
        if install_answer == QMessageBox.Yes:
            ready, message = self._x11_service.ensure_windows_x_server(allow_install=True)
            if ready:
                self.statusBar().showMessage(message, 5000)
                return True
            QMessageBox.warning(self, "X11 Setup Failed", message)

        continue_answer = QMessageBox.question(
            self,
            "Continue Without X11",
            (
                "Local X server is unavailable.\n\n"
                "Continue this SSH connection with X11 forwarding disabled?"
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if continue_answer == QMessageBox.Yes:
            return False
        return None

    def _connect_sftp_with_prompts(
        self,
        session: Session,
        *,
        password_override: str | None = None,
        runtime_only: bool = False,
    ) -> tuple[str, str | None] | None:
        password = (
            password_override
            if password_override is not None
            else (self._credential_service.load_password(session) if session.save_password else None)
        )
        # Start at the user-defined remote path (defaulting to server home ".").
        start_path = session.sftp_remote_folder.strip() or DEFAULT_SFTP_REMOTE_FOLDER
        result, password = self._execute_sftp_operation(
            session,
            password,
            lambda current_password, trust_unknown: asyncio.run(
                self._sftp.resolve_directory(
                    session,
                    start_path,
                    password=current_password,
                    trust_unknown=trust_unknown,
                )
            ),
            allow_password_save=not runtime_only,
        )
        if result is None:
            return None
        remote_dir = result
        return remote_dir, password

    def _execute_sftp_operation(
        self,
        session: Session,
        password: str | None,
        operation: Callable[[str | None, bool], _T],
        *,
        save_password_on_success: bool = False,
        allow_password_save: bool = True,
    ) -> tuple[_T | None, str | None]:
        current_password = password
        trust_unknown = False

        while True:
            try:
                result = operation(current_password, trust_unknown)
                if allow_password_save and save_password_on_success:
                    self._enable_password_save_for_session(session)
                if allow_password_save and (session.save_password or save_password_on_success) and current_password:
                    self._persist_session_password(session, current_password)
                return result, current_password
            except TransferCancelledError:
                self.statusBar().showMessage("Transfer cancelled.", 5000)
                return None, current_password
            except Exception as exc:
                if self._is_host_key_error(exc) and not trust_unknown:
                    if self._prompt_trust_host_key(session):
                        trust_unknown = True
                        continue
                    return None, current_password
                if self._is_auth_error(exc):
                    current_password, remember_password = self._prompt_password(
                        session,
                        allow_save=allow_password_save,
                    )
                    save_password_on_success = save_password_on_success or remember_password
                    if not current_password:
                        return None, current_password
                    continue
                QMessageBox.critical(self, "SFTP Error", str(exc))
                return None, current_password

    @staticmethod
    def _is_host_key_error(exc: Exception) -> bool:
        message = str(exc).lower()
        return "host key" in message and ("not trusted" in message or "not verifiable" in message)

    @staticmethod
    def _is_auth_error(exc: Exception) -> bool:
        if isinstance(exc, asyncssh.PermissionDenied):
            return True
        message = str(exc).lower()
        return "permission denied" in message or "authentication failed" in message

    def _prompt_password(self, session: Session, *, allow_save: bool = True) -> tuple[str | None, bool]:
        dialog = QDialog(self)
        dialog.setWindowTitle("Password Required")
        dialog.setModal(True)
        dialog.resize(420, 160)

        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(f"Enter password for {session.username or 'user'}@{session.host}:"))

        password_input = QLineEdit(dialog)
        password_input.setEchoMode(QLineEdit.Password)
        password_input.setPlaceholderText("Password")
        layout.addWidget(password_input)

        remember_check = QCheckBox("Save password for this session", dialog)
        remember_check.setChecked(session.save_password and allow_save)
        if allow_save:
            layout.addWidget(remember_check)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=dialog)
        ok_button = buttons.button(QDialogButtonBox.Ok)
        if ok_button is not None:
            ok_button.setText("Connect")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        password_input.returnPressed.connect(dialog.accept)
        password_input.setFocus(Qt.ActiveWindowFocusReason)

        if dialog.exec() != QDialog.Accepted:
            return None, False

        password = password_input.text()
        if not password:
            return None, False
        return password, allow_save and remember_check.isChecked()

    def _resolve_linux_rdp_password(
        self,
        session: Session,
        password: str | None,
        *,
        allow_save: bool = True,
    ) -> tuple[str | None, bool]:
        if password:
            return password, True

        prompted_password, remember_password = self._prompt_password(session, allow_save=allow_save)
        if not prompted_password:
            return None, False

        if allow_save and remember_password:
            self._enable_password_save_for_session(session)
        if allow_save and (session.save_password or remember_password) and prompted_password:
            self._persist_session_password(
                session,
                prompted_password,
                title="Password Save Failed",
                show_dialog=remember_password,
            )
        return prompted_password, True

    def _enable_password_save_for_session(self, session: Session) -> None:
        if not self._is_saved_session(session):
            return
        if session.save_password:
            return
        session.save_password = True
        self._session_service.add_or_update(session)
        current = self._current_session()
        if current and current.id == session.id:
            self._update_details()

    def _prompt_trust_host_key(self, session: Session) -> bool:
        answer = QMessageBox.question(
            self,
            "Untrusted Host Key",
            (
                f"The host key for {session.host}:{session.port} is not trusted yet.\n\n"
                "Only continue if you verified the fingerprint out-of-band."
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return answer == QMessageBox.Yes

    def _install_public_key_current(self) -> None:
        session = self._current_session()
        if not session:
            QMessageBox.warning(self, "No Selection", "Select a session first.")
            return
        self._install_public_key_for_session(session)

    def _build_session_actions_menu(self) -> QMenu:
        menu = QMenu(self)
        self._session_action_add = menu.addAction("Add Session...")
        self._session_action_add.triggered.connect(self._new_session_from_actions_menu)
        self._session_action_quick_connect = menu.addAction("Quick Connect...")
        self._session_action_quick_connect.triggered.connect(self._quick_connect_session)
        self._session_action_edit = menu.addAction("Edit Selected Session")
        self._session_action_edit.triggered.connect(self._edit_session)
        self._session_action_delete = menu.addAction("Delete Selected")
        self._session_action_delete.triggered.connect(self._delete_session)
        menu.addSeparator()
        self._session_action_install_key = menu.addAction("Install Public Key")
        self._session_action_install_key.triggered.connect(self._install_public_key_current)
        menu.aboutToShow.connect(self._refresh_session_actions_menu)
        return menu

    def _new_session_from_actions_menu(self) -> None:
        current_item = self.session_tree.currentItem()
        current_folder_path: str | None = None
        if current_item and current_item.data(0, TREE_KIND_ROLE) == "folder":
            folder = current_item.data(0, TREE_FOLDER_PATH_ROLE)
            if isinstance(folder, str) and folder.strip():
                current_folder_path = folder
        elif current_item and current_item.data(0, TREE_KIND_ROLE) == "session":
            folder = current_item.data(0, TREE_SESSION_FOLDER_ROLE)
            if isinstance(folder, str) and folder.strip():
                current_folder_path = folder

        selected_sessions = self._selected_sessions()
        selected_session = selected_sessions[0] if len(selected_sessions) == 1 else None
        default_folder = self._suggest_new_session_folder(
            current_folder_path=current_folder_path,
            selected_session=selected_session,
            selected_folder_paths=self._selected_folder_paths(),
        )
        self._new_session(default_folder=default_folder)

    def _refresh_session_actions_menu(self) -> None:
        selected_sessions = self._selected_sessions()
        selected_folder_paths = self._selected_folder_paths()
        if not selected_sessions and not selected_folder_paths:
            current = self._current_session()
            if current is not None:
                selected_sessions = [current]

        single_session = selected_sessions[0] if len(selected_sessions) == 1 and not selected_folder_paths else None
        self._session_action_edit.setEnabled(single_session is not None)
        self._session_action_delete.setEnabled(bool(selected_sessions or selected_folder_paths))
        self._session_action_install_key.setEnabled(
            single_session is not None and single_session.protocol in (Protocol.SSH, Protocol.SFTP)
        )

    def _cancel_active_status_progress(self) -> None:
        operation = self._active_status_progress
        if operation is None or not operation.cancelable or operation.canceled:
            return
        operation.canceled = True
        self._status_progress_widget.update_message(f"{operation.title}: canceling after the current step...")

    def _begin_status_progress_operation(
        self,
        *,
        kind: str,
        title: str,
        total_steps: int,
        cancelable: bool,
    ) -> StatusProgressOperation:
        operation = StatusProgressOperation(
            key=f"{kind}-{self._next_status_progress_id}",
            kind=kind,
            title=title,
            total_steps=max(1, int(total_steps)),
            cancelable=cancelable,
        )
        self._next_status_progress_id += 1
        self._active_status_progress = operation
        self._status_progress_widget.configure(
            message=title,
            completed_steps=operation.completed_steps,
            total_steps=operation.total_steps,
            cancelable=cancelable,
        )
        return operation

    def _refresh_status_progress(self, operation: StatusProgressOperation, *, message: str) -> None:
        if self._active_status_progress is not operation:
            return
        self._status_progress_widget.configure(
            message=f"{operation.title}: {message}",
            completed_steps=operation.completed_steps,
            total_steps=operation.total_steps,
            cancelable=operation.cancelable,
        )

    def _advance_status_progress(self, operation: StatusProgressOperation, *, message: str) -> None:
        operation.completed_steps = min(operation.total_steps, operation.completed_steps + 1)
        self._refresh_status_progress(operation, message=message)

    def _finish_status_progress(
        self,
        operation: StatusProgressOperation | None,
        *,
        final_message: str | None = None,
        timeout_ms: int = 5000,
        mark_complete: bool = False,
    ) -> None:
        if operation is None:
            return
        if self._active_status_progress is operation:
            if mark_complete:
                operation.completed_steps = operation.total_steps
                self._refresh_status_progress(operation, message="Complete.")
            self._status_progress_widget.hide()
            self._active_status_progress = None
        if self._connection_load_progress is operation:
            self._connection_load_progress = None
        if final_message:
            self.statusBar().showMessage(final_message, timeout_ms)

    @staticmethod
    def _pump_ui() -> None:
        QApplication.processEvents()

    def _begin_connection_load_progress(
        self,
        *,
        title: str,
        total_entries: int,
        remote_entries: int,
        close_entries: int = 0,
    ) -> None:
        self._connection_load_progress = None
        self._connection_load_completion_message = ""
        total_steps = max(0, int(close_entries)) + max(0, int(total_entries)) + max(0, int(remote_entries))
        if total_steps <= 1:
            return
        self._connection_load_progress = self._begin_status_progress_operation(
            kind="connection-load",
            title=title,
            total_steps=total_steps,
            cancelable=True,
        )

    def _connection_load_canceled(self) -> bool:
        operation = self._connection_load_progress
        return bool(operation is not None and operation.canceled)

    def _refresh_connection_load_message(self, message: str) -> None:
        operation = self._connection_load_progress
        if operation is None:
            return
        self._refresh_status_progress(operation, message=message)

    def _advance_connection_load_progress(self, message: str) -> None:
        operation = self._connection_load_progress
        if operation is None:
            return
        self._advance_status_progress(operation, message=message)

    def _finish_connection_load_progress(
        self,
        *,
        final_message: str | None = None,
        timeout_ms: int = 7000,
        mark_complete: bool = False,
    ) -> None:
        if final_message:
            self._connection_load_completion_message = final_message
        self._finish_status_progress(
            self._connection_load_progress,
            final_message=final_message,
            timeout_ms=timeout_ms,
            mark_complete=mark_complete,
        )
        if self._connection_load_progress is None:
            self._connection_load_completion_message = ""

    def _has_pending_remote_viewer_starts(self) -> bool:
        return bool(
            self._profile_restore_pending_remote_starts
            or self._remote_viewer_start_queue
            or self._remote_viewer_start_in_progress
            or self._remote_viewer_start_scheduled
        )

    def _cancel_pending_remote_viewer_starts(self) -> None:
        pending_tabs: list[RemoteViewerTab] = []
        for tab, _start_detached in self._profile_restore_pending_remote_starts:
            pending_tabs.append(tab)
        self._profile_restore_pending_remote_starts.clear()
        while self._remote_viewer_start_queue:
            tab, _start_detached = self._remote_viewer_start_queue.popleft()
            pending_tabs.append(tab)
        self._remote_viewer_start_scheduled = False
        for tab in pending_tabs:
            location = self._find_widget_location(tab)
            if location is None:
                continue
            host, index = location
            self._close_tab_in_host(host, index, show_progress=False)

    def _maybe_finish_connection_load_progress(self, final_message: str) -> None:
        operation = self._connection_load_progress
        if operation is None:
            return
        if final_message:
            self._connection_load_completion_message = final_message
        if operation.canceled:
            self._cancel_pending_remote_viewer_starts()
            cancel_message = f"{operation.title} canceled."
            self._finish_connection_load_progress(
                final_message=cancel_message,
                timeout_ms=7000,
                mark_complete=False,
            )
            return
        if self._has_pending_remote_viewer_starts():
            return
        self._finish_connection_load_progress(
            final_message=self._connection_load_completion_message or final_message,
            timeout_ms=7000,
            mark_complete=True,
        )

    def _session_uses_remote_viewer_queue(self, session: Session) -> bool:
        system = platform.system().lower()
        if session.protocol == Protocol.RDP:
            return system in {"linux", "windows"}
        if session.protocol == Protocol.VNC:
            return system in {"linux", "windows"}
        if session.protocol == Protocol.NOMACHINE:
            return system in {"linux", "windows", "darwin"}
        return False

    def _profile_tab_payloads(self, snapshot: dict[str, object]) -> list[dict[str, object]]:
        payloads: list[dict[str, object]] = []
        tree = snapshot.get("workspace_tree")
        for host_node in self._profile_host_nodes(tree):
            raw_tabs = host_node.get("tabs")
            if not isinstance(raw_tabs, list):
                continue
            for raw_tab in raw_tabs:
                if isinstance(raw_tab, dict):
                    payloads.append(raw_tab)
        raw_windows = snapshot.get("detached_windows")
        if isinstance(raw_windows, list):
            for raw_window in raw_windows:
                if not isinstance(raw_window, dict):
                    continue
                raw_tabs = raw_window.get("tabs")
                if not isinstance(raw_tabs, list):
                    continue
                for raw_tab in raw_tabs:
                    if isinstance(raw_tab, dict):
                        payloads.append(raw_tab)
        return payloads

    def _profile_tab_uses_remote_viewer_queue(self, tab_payload: dict[str, object]) -> bool:
        kind = str(tab_payload.get("kind", "")).strip().upper()
        if kind in {"RDP", "VNC", "NOMACHINE"}:
            session_id = str(tab_payload.get("session_id", "")).strip()
            session = self._session_service.by_id(session_id) if session_id else None
            if session is not None:
                return self._session_uses_remote_viewer_queue(session)
            protocol_map = {
                "RDP": Protocol.RDP,
                "VNC": Protocol.VNC,
                "NOMACHINE": Protocol.NOMACHINE,
            }
            return self._session_uses_remote_viewer_queue(
                Session(name=kind, host="", protocol=protocol_map[kind], port=0)
            )
        return False

    def _profile_tab_display_label(self, tab_payload: dict[str, object]) -> str:
        kind = str(tab_payload.get("kind", "")).strip().upper()
        if kind == "LOCAL":
            return "Local Shell"
        session_id = str(tab_payload.get("session_id", "")).strip()
        if session_id:
            session = self._session_service.by_id(session_id)
            if session is not None:
                name = session.name.strip() or session.host.strip() or session_id
                return f"{name} ({session.protocol.value.upper()})"
        if kind:
            return kind
        return "Session"

    @staticmethod
    def _normalized_session_list_mode(raw_mode: str) -> str:
        cleaned = str(raw_mode).strip().lower()
        if cleaned == "unhide":
            cleaned = "shown"
        elif cleaned == "hide":
            cleaned = "auto"
        if cleaned in {"auto", "float"}:
            return cleaned
        return "shown"

    @staticmethod
    def _session_list_mode_tooltip(mode: str) -> str:
        normalized = MainWindow._normalized_session_list_mode(mode)
        if normalized == "auto":
            return "Session list mode: Auto (hover the splitter divider to reveal)"
        if normalized == "float":
            return "Session list mode: Float (always-on-top separate window)"
        return "Session list mode: Show"

    def _build_session_list_mode_menu(self) -> QMenu:
        menu = QMenu(self)
        self._session_list_mode_actions: dict[str, object] = {}
        for label, mode in (("Show", "shown"), ("Auto", "auto"), ("Float", "float")):
            action = menu.addAction(label)
            action.setCheckable(True)
            action.triggered.connect(lambda _checked=False, chosen_mode=mode: self._set_session_list_mode(chosen_mode))
            self._session_list_mode_actions[mode] = action
        return menu

    def _sync_session_list_mode_actions(self) -> None:
        mode = self._session_list_mode
        if not hasattr(self, "_session_list_mode_btn"):
            return
        labels = {"shown": "Show", "auto": "Auto", "float": "Float"}
        self._session_list_mode_btn.setText(labels.get(mode, "Show"))
        self._session_list_mode_btn.setToolTip(self._session_list_mode_tooltip(mode))
        for action_mode, action in getattr(self, "_session_list_mode_actions", {}).items():
            try:
                action.setChecked(action_mode == mode)
            except Exception:
                continue
        self._update_session_list_splitter_handle_tooltip()

    def _set_session_list_mode(
        self,
        mode: str,
        *,
        persist: bool = True,
        float_placement: _WindowPlacement | None = None,
    ) -> None:
        normalized = self._normalized_session_list_mode(mode)
        self._session_list_mode = normalized
        self._apply_session_list_mode(normalized, persist=persist, float_placement=float_placement)
        self._sync_session_list_mode_actions()

    def _is_session_list_visible(self) -> bool:
        if self._is_session_list_floating():
            return True
        sizes = self._main_splitter.sizes()
        if not sizes:
            return True
        return sizes[0] > 12

    def _is_session_list_floating(self) -> bool:
        return self._session_list_window is not None

    def _remember_session_list_width_from_splitter(self) -> None:
        if self._is_session_list_floating():
            return
        sizes = self._main_splitter.sizes()
        if len(sizes) < 2:
            return
        if sizes[0] > 24:
            self._session_list_last_width = sizes[0]

    def _capture_current_main_splitter_state(self) -> str:
        try:
            encoded = bytes(self._main_splitter.saveState().toBase64()).decode("ascii")
        except Exception:
            return ""
        return encoded.strip()

    def _remember_docked_main_splitter_state(self) -> str:
        if self._is_session_list_floating():
            return self._session_list_docked_splitter_b64.strip()
        encoded = self._capture_current_main_splitter_state()
        if encoded:
            self._session_list_docked_splitter_b64 = encoded
        return encoded

    def _restore_docked_main_splitter_state(self) -> bool:
        encoded = self._session_list_docked_splitter_b64.strip()
        if not encoded:
            return False
        try:
            state = QByteArray.fromBase64(encoded.encode("ascii"))
        except Exception:
            return False
        if state.isEmpty():
            return False
        try:
            return bool(self._main_splitter.restoreState(state))
        except Exception:
            return False

    def _session_list_window_placement_from_settings(self) -> _WindowPlacement | None:
        return _window_placement_from_payload(
            geometry_b64=self._settings.session_list_window_geometry_b64,
            screen_name=self._settings.session_list_window_screen_name,
            screen_serial=self._settings.session_list_window_screen_serial,
            frame_rect=self._settings.session_list_window_frame_rect,
        )

    def _capture_session_list_window_placement(self, window: SessionListWindow | None = None) -> None:
        target = window or self._session_list_window
        if target is None:
            return
        placement = _capture_window_placement(target)
        self._settings.session_list_window_geometry_b64 = _geometry_to_b64(placement.geometry)
        self._settings.session_list_window_screen_name = placement.screen_name
        self._settings.session_list_window_screen_serial = placement.screen_serial
        self._settings.session_list_window_frame_rect = _frame_rect_to_list(placement.frame_rect)

    def _attach_session_list_panel_to_window(self, window: SessionListWindow) -> None:
        total = sum(self._main_splitter.sizes()) or max(1, self.width())
        self._session_list_panel.setMaximumWidth(16777215)
        self._session_list_panel.setVisible(True)
        self._main_splitter.replaceWidget(0, self._session_list_placeholder)
        window.setCentralWidget(self._session_list_panel)
        self._main_splitter.setSizes([0, max(1, total)])

    def _take_session_list_panel_from_window(self, window: SessionListWindow) -> QWidget:
        panel = window.takeCentralWidget()
        if isinstance(panel, QWidget):
            return panel
        return self._session_list_panel

    def _restore_session_list_panel_to_splitter(self, panel: QWidget) -> None:
        self._main_splitter.replaceWidget(0, panel)
        self._session_list_panel = panel
        self._session_list_panel.setMaximumWidth(16777215)
        self._session_list_panel.setVisible(True)

    def _enter_session_list_float_mode(self, *, placement: _WindowPlacement | None = None) -> None:
        self._session_list_auto_hide_timer.stop()
        if self._session_list_window is not None:
            if placement is not None:
                _restore_or_defer_window_placement(self._session_list_window, placement)
            self._session_list_window.show()
            self._session_list_window.raise_()
            self._session_list_window.activateWindow()
            return

        self._remember_session_list_width_from_splitter()
        self._remember_docked_main_splitter_state()
        window = SessionListWindow(owner=self, parent=self)
        self._attach_session_list_panel_to_window(window)
        _restore_or_defer_window_placement(window, placement or self._session_list_window_placement_from_settings())
        self._session_list_window = window
        window.show()
        window.raise_()
        window.activateWindow()

    def _apply_docked_session_list_mode(self, mode: str) -> None:
        normalized = self._normalized_session_list_mode(mode)
        if normalized == "shown":
            if not self._is_session_list_visible():
                self._set_session_list_visible(True)
            self._session_list_auto_hide_timer.stop()
            return
        if self._is_session_list_visible():
            self._set_session_list_visible(False)
        self._session_list_auto_hide_timer.start()

    def _dock_session_list_panel(
        self,
        target_mode: str,
        *,
        persist: bool,
        close_window: bool,
        window: SessionListWindow | None = None,
    ) -> None:
        normalized = self._normalized_session_list_mode(target_mode)
        if normalized == "float":
            normalized = "shown"
        active_window = window or self._session_list_window
        if active_window is not None:
            self._capture_session_list_window_placement(active_window)
            panel = self._take_session_list_panel_from_window(active_window)
            self._restore_session_list_panel_to_splitter(panel)
            if self._session_list_window is active_window:
                self._session_list_window = None
            if close_window:
                active_window.prepare_for_owner_close()
                try:
                    active_window.close()
                except RuntimeError:
                    pass
                active_window.deleteLater()
        self._restore_docked_main_splitter_state()
        self._session_list_mode = normalized
        self._apply_docked_session_list_mode(normalized)
        if persist:
            self._settings.session_list_visibility_mode = normalized
            self._settings_service.save(self._settings)

    def _set_session_list_visible(self, visible: bool) -> None:
        if self._is_session_list_floating():
            return
        sizes = self._main_splitter.sizes()
        total = sum(sizes) if sizes else max(1, self.width())
        if total <= 0:
            total = 1320
        if visible:
            self._session_list_panel.setMaximumWidth(16777215)
            self._session_list_panel.setVisible(True)
            left_width = max(220, min(self._session_list_last_width, max(220, total - 280)))
            self._main_splitter.setSizes([left_width, max(1, total - left_width)])
        else:
            self._remember_session_list_width_from_splitter()
            self._session_list_panel.setVisible(True)
            self._session_list_panel.setMaximumWidth(0)
            self._main_splitter.setSizes([0, max(1, total)])

    def _apply_session_list_mode(
        self,
        mode: str,
        *,
        persist: bool,
        float_placement: _WindowPlacement | None = None,
    ) -> None:
        normalized = self._normalized_session_list_mode(mode)
        if normalized == "float":
            self._enter_session_list_float_mode(placement=float_placement)
        else:
            if self._is_session_list_floating():
                self._dock_session_list_panel(normalized, persist=False, close_window=True)
            else:
                self._apply_docked_session_list_mode(normalized)
        if persist:
            self._settings.session_list_visibility_mode = normalized
            self._settings_service.save(self._settings)

    def _on_main_splitter_moved(self, _pos: int, _index: int) -> None:
        if self._is_session_list_floating():
            return
        self._remember_session_list_width_from_splitter()

    def _session_list_splitter_handle(self) -> QWidget | None:
        try:
            handle = self._main_splitter.handle(1)
        except Exception:
            return None
        if not isinstance(handle, QWidget):
            return None
        return handle

    def _update_session_list_splitter_handle_tooltip(self) -> None:
        handle = self._session_list_splitter_handle()
        if handle is None:
            return
        if self._session_list_mode == "float":
            handle.setToolTip("Session list is floating in its own always-on-top window.")
            return
        if self._session_list_mode == "auto":
            handle.setToolTip("Hover to reveal the session list. Drag to resize while shown.")
            return
        handle.setToolTip("Drag to resize the session list.")

    def _is_pointer_over_session_list_handle(self, global_pos) -> bool:
        handle = self._session_list_splitter_handle()
        if handle is None:
            return False
        return self._widget_contains_global_pos(handle, global_pos)

    @staticmethod
    def _widget_contains_global_pos(widget: QWidget, global_pos) -> bool:
        if not widget.isVisible():
            return False
        local = widget.mapFromGlobal(global_pos)
        return widget.rect().contains(local)

    def _maybe_auto_hide_session_list(self) -> None:
        if self._session_list_mode != "auto" or self._is_session_list_floating():
            self._session_list_auto_hide_timer.stop()
            return
        global_pos = QCursor.pos()
        if not self._is_session_list_visible():
            if self._is_pointer_over_session_list_handle(global_pos):
                self._set_session_list_visible(True)
            return
        if QApplication.mouseButtons() != Qt.NoButton:
            return
        if self._is_pointer_over_session_list_handle(global_pos):
            return
        if self._widget_contains_global_pos(self._session_list_panel, global_pos):
            return
        self._set_session_list_visible(False)

    def _on_session_list_window_close_requested(self, window: SessionListWindow) -> bool:
        if self._is_shutting_down:
            return True
        if window is not self._session_list_window:
            return True
        self._dock_session_list_panel("shown", persist=True, close_window=False, window=window)
        self._sync_session_list_mode_actions()
        self.statusBar().showMessage("Session list window closed and reattached to the main window.", 5000)
        return True

    def _build_profiles_menu(self) -> QMenu:
        menu = QMenu(self)
        menu.aboutToShow.connect(self._refresh_profiles_menu)
        return menu

    def _build_fast_commands_menu(self) -> QMenu:
        menu = QMenu(self)
        menu.aboutToShow.connect(self._refresh_fast_commands_menu)
        return menu

    def _refresh_fast_commands_menu(self) -> None:
        menu = self._fast_commands_menu
        menu.clear()
        commands = self._fast_command_entries()

        if not commands:
            no_commands_action = menu.addAction("No Fast Commands Saved")
            no_commands_action.setEnabled(False)
        else:
            for command in commands:
                command_id = str(command.get("id", "")).strip()
                name = str(command.get("name", "")).strip() or "Fast Command"
                payload = str(command.get("command", ""))
                action = menu.addAction(name)
                action.setToolTip(payload)
                action.triggered.connect(
                    lambda _checked=False, cid=command_id: self._run_fast_command_by_id(cid)
                )

        menu.addSeparator()
        add_action = menu.addAction("Add Fast Command...")
        add_action.triggered.connect(self._add_fast_command)
        manager_action = menu.addAction("Manage Fast Commands...")
        manager_action.triggered.connect(self._open_fast_command_manager)

    def _fast_command_entries(self) -> list[dict[str, str]]:
        entries: list[dict[str, str]] = []
        seen_ids: set[str] = set()
        for raw in self._settings.fast_commands:
            if not isinstance(raw, dict):
                continue
            command_id = str(raw.get("id", "")).strip()
            name = str(raw.get("name", "")).strip()
            command = str(raw.get("command", ""))
            if not command_id or not name or not command.strip():
                continue
            if command_id in seen_ids:
                continue
            seen_ids.add(command_id)
            entries.append(
                {
                    "id": command_id,
                    "name": name,
                    "command": command,
                }
            )
        return entries

    def _fast_command_by_id(self, command_id: str) -> dict[str, str] | None:
        target = command_id.strip()
        if not target:
            return None
        for command in self._fast_command_entries():
            if str(command.get("id", "")).strip() == target:
                return command
        return None

    def _fast_command_display_rows(self) -> list[tuple[str, str, str]]:
        rows: list[tuple[str, str, str]] = []
        for command in self._fast_command_entries():
            command_id = str(command.get("id", "")).strip()
            name = str(command.get("name", "")).strip()
            payload = str(command.get("command", ""))
            if command_id and name and payload.strip():
                rows.append((command_id, name, payload))
        return rows

    def _save_fast_commands(self, commands: list[dict[str, str]]) -> None:
        cleaned: list[dict[str, str]] = []
        seen_ids: set[str] = set()
        for command in commands:
            if not isinstance(command, dict):
                continue
            command_id = str(command.get("id", "")).strip()
            name = str(command.get("name", "")).strip()
            payload = str(command.get("command", ""))
            if not command_id or not name or not payload.strip():
                continue
            if command_id in seen_ids:
                continue
            seen_ids.add(command_id)
            cleaned.append(
                {
                    "id": command_id,
                    "name": name,
                    "command": payload,
                }
            )
        self._settings.fast_commands = cleaned
        self._settings_service.save(self._settings)

    def _unique_fast_command_name(self, base_name: str, *, exclude_command_id: str = "") -> str:
        base = base_name.strip() or "Fast Command"
        taken = {
            str(command.get("name", "")).strip().lower()
            for command in self._fast_command_entries()
            if str(command.get("id", "")).strip() != exclude_command_id.strip()
        }
        if base.lower() not in taken:
            return base
        suffix = 2
        while True:
            candidate = f"{base} {suffix}"
            if candidate.lower() not in taken:
                return candidate
            suffix += 1

    def _next_fast_command_name(self) -> str:
        return self._unique_fast_command_name("Fast Command")

    def _prompt_fast_command_name(
        self,
        *,
        title: str,
        prompt: str,
        default_name: str,
        exclude_command_id: str = "",
    ) -> str | None:
        entered, ok = QInputDialog.getText(self, title, prompt, QLineEdit.Normal, default_name)
        if not ok:
            return None
        trimmed = entered.strip()
        if not trimmed:
            return None
        return self._unique_fast_command_name(trimmed, exclude_command_id=exclude_command_id)

    def _prompt_fast_command_payload(
        self,
        *,
        title: str,
        prompt: str,
        default_command: str,
    ) -> str | None:
        entered, ok = QInputDialog.getMultiLineText(self, title, prompt, default_command)
        if not ok:
            return None
        if not entered.strip():
            return None
        return entered

    def _add_fast_command(self) -> bool:
        name = self._prompt_fast_command_name(
            title="Add Fast Command",
            prompt="Command name:",
            default_name=self._next_fast_command_name(),
        )
        if not name:
            return False
        command = self._prompt_fast_command_payload(
            title="Add Fast Command",
            prompt="Command to send:",
            default_command=self.command_bar_input.text().strip(),
        )
        if command is None:
            return False
        commands = self._fast_command_entries()
        commands.append(
            {
                "id": str(uuid4()),
                "name": name,
                "command": command,
            }
        )
        self._save_fast_commands(commands)
        self.statusBar().showMessage(f"Saved fast command {name}.", 5000)
        return True

    def _edit_fast_command(self, command_id: str) -> bool:
        command = self._fast_command_by_id(command_id)
        if command is None:
            QMessageBox.warning(self, "Fast Command Missing", "Selected fast command no longer exists.")
            return False
        name = str(command.get("name", "")).strip() or "Fast Command"
        updated_payload = self._prompt_fast_command_payload(
            title="Edit Fast Command",
            prompt=f"Command for {name}:",
            default_command=str(command.get("command", "")),
        )
        if updated_payload is None:
            return False
        commands = self._fast_command_entries()
        for item in commands:
            if str(item.get("id", "")).strip() != command_id.strip():
                continue
            item["command"] = updated_payload
            break
        self._save_fast_commands(commands)
        self.statusBar().showMessage(f"Updated fast command {name}.", 5000)
        return True

    def _rename_fast_command(self, command_id: str) -> bool:
        command = self._fast_command_by_id(command_id)
        if command is None:
            QMessageBox.warning(self, "Fast Command Missing", "Selected fast command no longer exists.")
            return False
        current_name = str(command.get("name", "")).strip() or "Fast Command"
        renamed = self._prompt_fast_command_name(
            title="Rename Fast Command",
            prompt="Command name:",
            default_name=current_name,
            exclude_command_id=command_id,
        )
        if not renamed:
            return False
        commands = self._fast_command_entries()
        for item in commands:
            if str(item.get("id", "")).strip() != command_id.strip():
                continue
            item["name"] = renamed
            break
        self._save_fast_commands(commands)
        self.statusBar().showMessage(f"Renamed fast command to {renamed}.", 5000)
        return True

    def _delete_fast_command(self, command_id: str) -> bool:
        command = self._fast_command_by_id(command_id)
        if command is None:
            return False
        name = str(command.get("name", "")).strip() or "Fast Command"
        answer = QMessageBox.question(
            self,
            "Delete Fast Command",
            f"Delete fast command {name}?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return False
        remaining = [
            entry
            for entry in self._fast_command_entries()
            if str(entry.get("id", "")).strip() != command_id.strip()
        ]
        self._save_fast_commands(remaining)
        self.statusBar().showMessage(f"Deleted fast command {name}.", 5000)
        return True

    def _open_fast_command_manager(self) -> None:
        dialog = FastCommandManagerDialog(self)

        def _reload_dialog_rows() -> None:
            dialog.set_commands(self._fast_command_display_rows())

        dialog.add_command_requested.connect(lambda: (self._add_fast_command(), _reload_dialog_rows()))
        dialog.edit_command_requested.connect(
            lambda command_id: (self._edit_fast_command(command_id), _reload_dialog_rows())
        )
        dialog.rename_command_requested.connect(
            lambda command_id: (self._rename_fast_command(command_id), _reload_dialog_rows())
        )
        dialog.delete_command_requested.connect(
            lambda command_id: (self._delete_fast_command(command_id), _reload_dialog_rows())
        )
        _reload_dialog_rows()
        dialog.exec()

    def _run_fast_command_by_id(self, command_id: str) -> None:
        command = self._fast_command_by_id(command_id)
        if command is None:
            QMessageBox.warning(self, "Fast Command Missing", "Selected fast command no longer exists.")
            return
        name = str(command.get("name", "")).strip() or "Fast Command"
        payload = str(command.get("command", ""))
        self._run_fast_command(name, payload)

    def _run_fast_command(self, name: str, payload: str) -> None:
        target = self._active_fast_command_target()
        if target is None:
            self.statusBar().showMessage("Select a terminal or remote viewer tab to run fast commands.", 5000)
            return
        if not payload.strip():
            self.statusBar().showMessage(f"Fast command {name} is empty.", 5000)
            return
        if isinstance(target, TerminalTab):
            self._dispatch_terminal_command(target, payload)
            self.statusBar().showMessage(f"Ran fast command {name}.", 4000)
            return

        clipboard = QApplication.clipboard()
        clipboard.setText(payload.rstrip("\r\n"))
        self.statusBar().showMessage(
            f"Copied fast command {name} to the clipboard for {target._protocol_name} paste.",
            6000,
        )

    def _refresh_profiles_menu(self) -> None:
        menu = self._profiles_menu
        menu.clear()
        profiles = self._workspace_profile_entries()
        default_profile_id = self._settings.default_workspace_profile_id.strip()

        if not profiles:
            no_profiles_action = menu.addAction("No Profiles Saved")
            no_profiles_action.setEnabled(False)
        else:
            for profile in profiles:
                profile_id = str(profile.get("id", "")).strip()
                name = str(profile.get("name", "")).strip() or "Profile"
                label = f"{name} (default)" if profile_id == default_profile_id else name
                action = menu.addAction(label)
                action.triggered.connect(
                    lambda _checked=False, pid=profile_id: self._apply_workspace_profile(pid)
                )

        menu.addSeparator()
        add_action = menu.addAction("Add Current Profile...")
        add_action.triggered.connect(self._add_current_workspace_profile)
        manager_action = menu.addAction("Profile Manager...")
        manager_action.triggered.connect(self._open_profile_manager)

    def _workspace_profile_entries(self) -> list[dict[str, object]]:
        entries: list[dict[str, object]] = []
        for raw in self._settings.workspace_profiles:
            if not isinstance(raw, dict):
                continue
            profile_id = str(raw.get("id", "")).strip()
            name = str(raw.get("name", "")).strip()
            snapshot = raw.get("snapshot")
            if not profile_id or not name or not isinstance(snapshot, dict):
                continue
            entries.append(
                {
                    "id": profile_id,
                    "name": name,
                    "snapshot": dict(snapshot),
                    "startup_tools": normalize_profile_startup_tool_keys(raw.get("startup_tools", [])),
                }
            )
        return entries

    def _workspace_profile_by_id(self, profile_id: str) -> dict[str, object] | None:
        target = profile_id.strip()
        if not target:
            return None
        for profile in self._workspace_profile_entries():
            if str(profile.get("id", "")).strip() == target:
                return profile
        return None

    def _workspace_profile_display_rows(self) -> list[tuple[str, str]]:
        rows: list[tuple[str, str]] = []
        for profile in self._workspace_profile_entries():
            profile_id = str(profile.get("id", "")).strip()
            name = str(profile.get("name", "")).strip()
            if not profile_id or not name:
                continue
            rows.append((profile_id, name))
        return rows

    def _save_workspace_profiles(self, profiles: list[dict[str, object]]) -> None:
        cleaned: list[dict[str, object]] = []
        seen_ids: set[str] = set()
        for profile in profiles:
            if not isinstance(profile, dict):
                continue
            profile_id = str(profile.get("id", "")).strip()
            name = str(profile.get("name", "")).strip()
            snapshot = profile.get("snapshot")
            if not profile_id or not name or not isinstance(snapshot, dict):
                continue
            if profile_id in seen_ids:
                continue
            seen_ids.add(profile_id)
            cleaned_profile: dict[str, object] = {
                "id": profile_id,
                "name": name,
                "snapshot": dict(snapshot),
            }
            startup_tools = normalize_profile_startup_tool_keys(profile.get("startup_tools", []))
            if startup_tools:
                cleaned_profile["startup_tools"] = startup_tools
            cleaned.append(cleaned_profile)

        default_profile_id = self._settings.default_workspace_profile_id.strip()
        if default_profile_id and default_profile_id not in seen_ids:
            default_profile_id = ""

        self._settings.workspace_profiles = cleaned
        self._settings.default_workspace_profile_id = default_profile_id
        self._settings_service.save(self._settings)

    def _open_profile_manager(self) -> None:
        dialog = ProfileManagerDialog(self)

        def _reload_dialog_rows() -> None:
            dialog.set_profiles(
                self._workspace_profile_entries(),
                default_profile_id=self._settings.default_workspace_profile_id,
            )

        dialog.open_profile_requested.connect(lambda profile_id: self._apply_workspace_profile(profile_id))
        dialog.add_profile_requested.connect(lambda: (self._add_current_workspace_profile(), _reload_dialog_rows()))
        dialog.replace_profile_requested.connect(
            lambda profile_id: (self._replace_workspace_profile_with_current(profile_id), _reload_dialog_rows())
        )
        dialog.delete_profile_requested.connect(
            lambda profile_id: (self._delete_workspace_profile(profile_id), _reload_dialog_rows())
        )
        dialog.edit_startup_tools_requested.connect(
            lambda profile_id: (self._edit_workspace_profile_startup_tools(profile_id), _reload_dialog_rows())
        )
        dialog.set_default_profile_requested.connect(
            lambda profile_id: (self._set_default_workspace_profile(profile_id), _reload_dialog_rows())
        )
        dialog.clear_default_profile_requested.connect(lambda: (self._clear_default_workspace_profile(), _reload_dialog_rows()))
        _reload_dialog_rows()
        dialog.exec()

    def _unique_workspace_profile_name(self, base_name: str, *, exclude_profile_id: str = "") -> str:
        base = base_name.strip() or "Profile"
        taken = {
            str(profile.get("name", "")).strip().lower()
            for profile in self._workspace_profile_entries()
            if str(profile.get("id", "")).strip() != exclude_profile_id.strip()
        }
        if base.lower() not in taken:
            return base
        suffix = 2
        while True:
            candidate = f"{base} {suffix}"
            if candidate.lower() not in taken:
                return candidate
            suffix += 1

    def _next_workspace_profile_name(self) -> str:
        return self._unique_workspace_profile_name("Profile")

    def _prompt_workspace_profile_name(
        self,
        *,
        title: str,
        prompt: str,
        default_name: str,
        exclude_profile_id: str = "",
    ) -> str | None:
        entered, ok = QInputDialog.getText(self, title, prompt, QLineEdit.Normal, default_name)
        if not ok:
            return None
        trimmed = entered.strip()
        if not trimmed:
            return None
        return self._unique_workspace_profile_name(trimmed, exclude_profile_id=exclude_profile_id)

    def _add_current_workspace_profile(self) -> bool:
        suggested = self._next_workspace_profile_name()
        profile_name = self._prompt_workspace_profile_name(
            title="Add Profile",
            prompt="Profile name:",
            default_name=suggested,
        )
        if not profile_name:
            return False

        profile = {
            "id": str(uuid4()),
            "name": profile_name,
            "snapshot": self._capture_workspace_profile_snapshot(),
            "startup_tools": self._capture_open_profile_startup_tools(),
        }
        profiles = self._workspace_profile_entries()
        profiles.append(profile)
        self._save_workspace_profiles(profiles)
        self.statusBar().showMessage(f"Saved profile {profile_name}.", 5000)
        return True

    def _replace_workspace_profile_with_current(self, profile_id: str) -> bool:
        target = profile_id.strip()
        if not target:
            return False
        profiles = self._workspace_profile_entries()
        replaced_name = ""
        replaced = False
        for profile in profiles:
            current_id = str(profile.get("id", "")).strip()
            if current_id != target:
                continue
            profile["snapshot"] = self._capture_workspace_profile_snapshot()
            profile["startup_tools"] = self._capture_open_profile_startup_tools()
            replaced_name = str(profile.get("name", "")).strip() or "Profile"
            replaced = True
            break
        if not replaced:
            QMessageBox.warning(self, "Profile Missing", "Selected profile no longer exists.")
            return False
        self._save_workspace_profiles(profiles)
        self.statusBar().showMessage(f"Updated profile {replaced_name}.", 5000)
        return True

    def _edit_workspace_profile_startup_tools(self, profile_id: str) -> bool:
        profile = self._workspace_profile_by_id(profile_id)
        if profile is None:
            QMessageBox.warning(self, "Profile Missing", "Selected profile no longer exists.")
            return False

        dialog = ProfileToolSelectionDialog(self)
        dialog.set_selected_tool_keys(normalize_profile_startup_tool_keys(profile.get("startup_tools", [])))
        if dialog.exec() != QDialog.Accepted:
            return False

        updated_tools = dialog.selected_tool_keys()
        profiles = self._workspace_profile_entries()
        updated_name = "Profile"
        updated = False
        for current in profiles:
            current_id = str(current.get("id", "")).strip()
            if current_id != profile_id.strip():
                continue
            current["startup_tools"] = updated_tools
            updated_name = str(current.get("name", "")).strip() or "Profile"
            updated = True
            break
        if not updated:
            QMessageBox.warning(self, "Profile Missing", "Selected profile no longer exists.")
            return False

        self._save_workspace_profiles(profiles)
        self.statusBar().showMessage(f"Updated start tools on load for {updated_name}.", 5000)
        return True

    def _delete_workspace_profile(self, profile_id: str) -> bool:
        profile = self._workspace_profile_by_id(profile_id)
        if profile is None:
            return False
        name = str(profile.get("name", "")).strip() or "Profile"
        answer = QMessageBox.question(
            self,
            "Delete Profile",
            f"Delete profile {name}?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return False
        target = str(profile.get("id", "")).strip()
        remaining = [entry for entry in self._workspace_profile_entries() if str(entry.get("id", "")).strip() != target]
        self._save_workspace_profiles(remaining)
        self.statusBar().showMessage(f"Deleted profile {name}.", 5000)
        return True

    def _set_default_workspace_profile(self, profile_id: str) -> bool:
        profile = self._workspace_profile_by_id(profile_id)
        if profile is None:
            return False
        self._settings.default_workspace_profile_id = str(profile.get("id", "")).strip()
        self._settings_service.save(self._settings)
        name = str(profile.get("name", "")).strip() or "Profile"
        self.statusBar().showMessage(f"Default profile set to {name}.", 5000)
        return True

    def _clear_default_workspace_profile(self) -> bool:
        if not self._settings.default_workspace_profile_id.strip():
            return False
        self._settings.default_workspace_profile_id = ""
        self._settings_service.save(self._settings)
        self.statusBar().showMessage("Default profile cleared.", 5000)
        return True

    def _handle_workspace_startup_restore(self) -> None:
        if self._safe_mode:
            self.statusBar().showMessage(
                "Safe mode enabled. Default workspace profile was skipped for this launch.",
                10000,
            )
            return
        self._maybe_apply_default_workspace_profile_on_startup()

    def _maybe_apply_default_workspace_profile_on_startup(self) -> None:
        default_profile_id = self._settings.default_workspace_profile_id.strip()
        if not default_profile_id:
            return
        if self._workspace_profile_by_id(default_profile_id) is None:
            self._settings.default_workspace_profile_id = ""
            self._settings_service.save(self._settings)
            return
        self._apply_workspace_profile(default_profile_id, startup=True)

    def _capture_workspace_profile_snapshot(self) -> dict[str, object]:
        host_keys: dict[WorkspaceTabWidget, str] = {}
        root = self._workspace_root_widget()
        tree = self._capture_workspace_profile_tree_node(root, host_keys)
        if tree is None:
            tree = self._capture_workspace_profile_host_node(self.tabs, host_keys)

        active_key = ""
        if self._active_tab_host in host_keys:
            active_key = host_keys[self._active_tab_host]

        group_names = {
            str(group_id): str(name).strip()
            for group_id, name in self._tab_group_names.items()
            if str(group_id).strip() and str(name).strip()
        }

        placement = self._current_main_window_placement()
        geometry_b64 = _geometry_to_b64(placement.geometry)
        splitter_b64 = self._remember_docked_main_splitter_state()

        snapshot = {
            "version": 1,
            "workspace_tree": tree,
            "detached_windows": self._capture_detached_windows_profile_snapshot(),
            "active_host_key": active_key,
            "session_list_mode": self._session_list_mode,
            "session_list_visible": self._is_session_list_visible(),
            "session_list_last_width": int(max(220, self._session_list_last_width)),
            "tab_group_names": group_names,
            "next_tab_group_id": int(max(1, self._next_tab_group_id)),
            "next_tab_group_name_id": int(max(1, self._next_tab_group_name_id)),
            "window_geometry_b64": geometry_b64,
            "window_screen_name": placement.screen_name,
            "window_screen_serial": placement.screen_serial,
            "window_frame_rect": _frame_rect_to_list(placement.frame_rect),
            "main_splitter_b64": splitter_b64,
        }
        if self._session_list_mode == "float":
            if self._session_list_window is not None:
                session_list_placement = _capture_window_placement(self._session_list_window)
                snapshot["session_list_window_geometry_b64"] = _geometry_to_b64(session_list_placement.geometry)
                snapshot["session_list_window_screen_name"] = session_list_placement.screen_name
                snapshot["session_list_window_screen_serial"] = session_list_placement.screen_serial
                snapshot["session_list_window_frame_rect"] = _frame_rect_to_list(session_list_placement.frame_rect)
            else:
                snapshot["session_list_window_geometry_b64"] = self._settings.session_list_window_geometry_b64
                snapshot["session_list_window_screen_name"] = self._settings.session_list_window_screen_name
                snapshot["session_list_window_screen_serial"] = self._settings.session_list_window_screen_serial
                snapshot["session_list_window_frame_rect"] = list(self._settings.session_list_window_frame_rect)
        return snapshot

    def _workspace_root_widget(self) -> QWidget:
        item = self._tab_workspace_layout.itemAt(0)
        if item is not None:
            root = item.widget()
            if isinstance(root, QWidget):
                return root
        return self.tabs

    def _capture_workspace_profile_tree_node(
        self,
        widget: QWidget,
        host_keys: dict[WorkspaceTabWidget, str],
    ) -> dict[str, object] | None:
        if isinstance(widget, WorkspaceTabWidget):
            return self._capture_workspace_profile_host_node(widget, host_keys)
        if isinstance(widget, QSplitter):
            children: list[dict[str, object]] = []
            for index in range(widget.count()):
                child = widget.widget(index)
                if not isinstance(child, QWidget):
                    continue
                child_node = self._capture_workspace_profile_tree_node(child, host_keys)
                if child_node is None:
                    continue
                children.append(child_node)
            if not children:
                return None
            return {
                "type": "splitter",
                "orientation": "vertical" if widget.orientation() == Qt.Vertical else "horizontal",
                "sizes": [int(value) for value in widget.sizes()],
                "children": children,
            }
        return None

    def _capture_workspace_profile_host_node(
        self,
        host: WorkspaceTabWidget,
        host_keys: dict[WorkspaceTabWidget, str],
    ) -> dict[str, object]:
        host_key = host_keys.get(host, f"host-{len(host_keys) + 1}")
        host_keys[host] = host_key
        tab_entries: list[dict[str, object]] = []
        current_session_index = -1
        session_counter = -1

        for index in range(host.count()):
            widget = host.widget(index)
            if widget is None or not widget.property("session_id"):
                continue
            tab_entry = self._capture_workspace_profile_tab_entry(widget)
            if tab_entry is None:
                continue
            session_counter += 1
            tab_entries.append(tab_entry)
            if host.currentWidget() is widget:
                current_session_index = session_counter

        return {
            "type": "host",
            "host_key": host_key,
            "is_primary": bool(host.property("is_primary_host")),
            "tabs": tab_entries,
            "current_session_index": int(current_session_index),
        }

    def _capture_detached_windows_profile_snapshot(self) -> list[dict[str, object]]:
        snapshots: list[dict[str, object]] = []
        for host, window in list(self._detached_window_by_host.items()):
            tab_entries: list[dict[str, object]] = []
            current_session_index = -1
            session_counter = -1
            for index in range(host.count()):
                widget = host.widget(index)
                if widget is None or not widget.property("session_id"):
                    continue
                tab_entry = self._capture_workspace_profile_tab_entry(widget)
                if tab_entry is None:
                    continue
                session_counter += 1
                tab_entries.append(tab_entry)
                if host.currentWidget() is widget:
                    current_session_index = session_counter

            placement = _capture_window_placement(window)

            snapshots.append(
                {
                    "tabs": tab_entries,
                    "current_session_index": int(current_session_index),
                    "window_geometry_b64": _geometry_to_b64(placement.geometry),
                    "window_screen_name": placement.screen_name,
                    "window_screen_serial": placement.screen_serial,
                    "window_frame_rect": _frame_rect_to_list(placement.frame_rect),
                }
            )
        return snapshots

    def _capture_workspace_profile_tab_entry(self, widget: QWidget) -> dict[str, object] | None:
        kind = str(widget.property("session_kind") or "").strip().upper()
        if not kind:
            return None
        if bool(widget.property("session_runtime_only")):
            return None
        session_id = widget.property("session_id")
        if kind != "LOCAL":
            if not isinstance(session_id, str) or not session_id.strip():
                return None

        payload: dict[str, object] = {"kind": kind}
        if kind != "LOCAL" and isinstance(session_id, str):
            payload["session_id"] = session_id.strip()
        custom_title = widget.property("tab_title_custom")
        if isinstance(custom_title, str) and custom_title.strip():
            payload["custom_title"] = custom_title.strip()
        group_id = self._tab_group_id(widget)
        if group_id:
            payload["tab_group_id"] = group_id
        return payload

    def _capture_open_profile_startup_tools(self) -> list[str]:
        self._prune_tool_processes()
        captured: list[str] = []
        for entry in profile_startup_tool_entries():
            processes = self._tool_processes.get(entry.key)
            if not processes and not has_active_tool_instance(entry.key):
                continue
            captured.append(entry.key)
        return captured

    @staticmethod
    def _workspace_profile_startup_tools(profile: dict[str, object]) -> list[str]:
        return normalize_profile_startup_tool_keys(profile.get("startup_tools", []))

    def _apply_workspace_profile(self, profile_id: str, *, startup: bool = False) -> bool:
        profile = self._workspace_profile_by_id(profile_id)
        if profile is None:
            if not startup:
                QMessageBox.warning(self, "Profile Missing", "Selected profile no longer exists.")
            return False

        snapshot = profile.get("snapshot")
        if not isinstance(snapshot, dict):
            if not startup:
                QMessageBox.warning(self, "Invalid Profile", "This profile does not contain usable workspace data.")
            return False

        profile_name = str(profile.get("name", "")).strip() or "Profile"
        existing_open_tabs = len(self._session_tab_locations())
        profile_tabs = self._profile_tab_payloads(snapshot)
        profile_remote_tabs = sum(1 for tab_payload in profile_tabs if self._profile_tab_uses_remote_viewer_queue(tab_payload))
        self._begin_connection_load_progress(
            title=f"Loading Profile {profile_name}",
            total_entries=len(profile_tabs),
            remote_entries=profile_remote_tabs,
            close_entries=existing_open_tabs,
        )

        if not self._prepare_workspace_for_profile_apply():
            self._finish_connection_load_progress()
            if not startup:
                QMessageBox.warning(
                    self,
                    "Profile Apply Failed",
                    "Could not close one or more existing tabs. Close busy sessions and try again.",
                )
            return False

        self._profile_restore_in_progress = True
        self._profile_restore_pending_remote_starts.clear()
        profile_restore_succeeded = False
        profile_restore_canceled = False
        failures: list[str] = []
        try:
            host_map = self._restore_workspace_layout_from_profile(snapshot)
            failures = self._restore_workspace_tabs_from_profile(snapshot, host_map)
            profile_restore_canceled = self._connection_load_canceled()
            if not profile_restore_canceled:
                failures.extend(self._restore_detached_windows_from_profile(snapshot))
            profile_restore_canceled = profile_restore_canceled or self._connection_load_canceled()
            self._restore_workspace_group_metadata_from_profile(snapshot)
            self._restore_workspace_selection_from_profile(snapshot, host_map)
            self._restore_workspace_window_state_from_profile(snapshot)
            self._refresh_session_instance_titles()
            profile_restore_succeeded = not profile_restore_canceled
        finally:
            self._profile_restore_in_progress = False
            if profile_restore_succeeded:
                self._flush_profile_restore_remote_starts()
            else:
                self._profile_restore_pending_remote_starts.clear()

        if profile_restore_canceled:
            self._cancel_pending_remote_viewer_starts()
            self._finish_connection_load_progress(
                final_message=f"Stopped loading profile {profile_name}.",
                timeout_ms=7000,
                mark_complete=False,
            )
            return True

        self._open_profile_startup_tools(self._workspace_profile_startup_tools(profile))
        if self._connection_load_progress is not None:
            self._maybe_finish_connection_load_progress(f"Loaded profile {profile_name}.")
        else:
            self.statusBar().showMessage(f"Loaded profile {profile_name}.", 7000)
        if failures and not startup:
            preview = "\n".join(failures[:8])
            if len(failures) > 8:
                preview += f"\n... and {len(failures) - 8} more."
            QMessageBox.warning(
                self,
                "Profile Loaded with Warnings",
                f"Some tabs could not be restored:\n\n{preview}",
            )
        return True

    def _prepare_workspace_for_profile_apply(self) -> bool:
        self._remote_viewer_start_queue.clear()
        self._remote_viewer_start_scheduled = False
        if not self._close_open_tabs_for_profile_restore():
            return False
        self._clear_detached_window_hosts()
        self._reset_workspace_to_primary_host()
        self._tab_group_names = {}
        self._next_tab_group_id = 1
        self._next_tab_group_name_id = 1
        return True

    def _close_open_tabs_for_profile_restore(self) -> bool:
        for _attempt in range(3):
            locations = list(self._session_tab_locations())
            if not locations:
                return True
            progress = False
            for _host, _index, widget in locations:
                location = self._find_widget_location(widget)
                if location is None:
                    continue
                title = self._display_title_for_widget(widget).strip() or "Session"
                if self._connection_load_progress is not None:
                    self._refresh_connection_load_message(f"Closing {title}...")
                    self._pump_ui()
                before = len(self._session_tab_locations())
                self._close_tab_in_host(location[0], location[1], show_progress=False)
                QApplication.processEvents()
                after = len(self._session_tab_locations())
                if after < before:
                    progress = True
                    if self._connection_load_progress is not None:
                        self._advance_connection_load_progress(f"Closed {title}.")
                        self._pump_ui()
                elif self._connection_load_progress is not None:
                    self._refresh_connection_load_message(f"Waiting for {title} to stop...")
                    self._pump_ui()
            if not progress:
                break
        return len(self._session_tab_locations()) == 0

    def _reset_workspace_to_primary_host(self) -> None:
        for host in list(self._tab_hosts):
            if host is self.tabs:
                continue
            if host in self._tab_hosts:
                self._tab_hosts.remove(host)
            try:
                host.setParent(None)
                host.deleteLater()
            except RuntimeError:
                continue

        current_root = self._workspace_root_widget()
        if current_root is not self.tabs:
            try:
                self.tabs.setParent(None)
                self._tab_workspace_layout.replaceWidget(current_root, self.tabs)
            except Exception:
                pass
            try:
                current_root.setParent(None)
                current_root.deleteLater()
            except RuntimeError:
                pass

        if self.tabs not in self._tab_hosts:
            self._tab_hosts.insert(0, self.tabs)
        self.tabs.setProperty("is_primary_host", True)
        self._set_active_tab_host(self.tabs)
        self._sync_host_tab_close_buttons(self.tabs)

    def _restore_workspace_layout_from_profile(self, snapshot: dict[str, object]) -> dict[str, WorkspaceTabWidget]:
        tree = snapshot.get("workspace_tree")
        host_map: dict[str, WorkspaceTabWidget] = {}
        primary_key = self._profile_primary_host_key(tree)
        if not primary_key:
            primary_key = "host-1"
        self._tab_workspace_layout.removeWidget(self.tabs)

        primary_consumed = False

        def _allocate_host(raw_key: str) -> tuple[str, WorkspaceTabWidget]:
            nonlocal primary_consumed
            key = raw_key.strip() or f"host-{len(host_map) + 1}"
            if key in host_map:
                suffix = 2
                while f"{key}-{suffix}" in host_map:
                    suffix += 1
                key = f"{key}-{suffix}"
            use_primary = not primary_consumed and (key == primary_key or len(host_map) == 0)
            if use_primary:
                host = self.tabs
                primary_consumed = True
            else:
                host = self._create_tab_host(primary=False)
            host_map[key] = host
            return key, host

        def _build_node(node: object) -> QWidget | None:
            if not isinstance(node, dict):
                return None
            node_type = str(node.get("type", "")).strip().lower()
            if node_type == "host":
                raw_key = str(node.get("host_key", "")).strip()
                _key, host = _allocate_host(raw_key)
                return host
            if node_type != "splitter":
                return None
            raw_children = node.get("children")
            if not isinstance(raw_children, list):
                return None
            child_widgets: list[QWidget] = []
            for child in raw_children:
                child_widget = _build_node(child)
                if isinstance(child_widget, QWidget):
                    child_widgets.append(child_widget)
            if not child_widgets:
                return None
            if len(child_widgets) == 1:
                return child_widgets[0]
            orientation = str(node.get("orientation", "horizontal")).strip().lower()
            splitter = QSplitter(Qt.Vertical if orientation == "vertical" else Qt.Horizontal, self._tab_workspace)
            for child_widget in child_widgets:
                splitter.addWidget(child_widget)
            raw_sizes = node.get("sizes")
            sizes: list[int] = []
            if isinstance(raw_sizes, list):
                for raw in raw_sizes[: len(child_widgets)]:
                    try:
                        size = max(1, int(raw))
                    except (TypeError, ValueError):
                        size = 1
                    sizes.append(size)
            if len(sizes) < len(child_widgets):
                sizes.extend([1] * (len(child_widgets) - len(sizes)))
            splitter.setSizes(sizes)
            return splitter

        root_widget = _build_node(tree)
        if not isinstance(root_widget, QWidget):
            if self.tabs not in self._tab_hosts:
                self._tab_hosts.insert(0, self.tabs)
            host_map.setdefault(primary_key, self.tabs)
            root_widget = self.tabs
        self._tab_workspace_layout.addWidget(root_widget, 1)
        if self.tabs not in self._tab_hosts:
            self._tab_hosts.insert(0, self.tabs)
        self.tabs.setProperty("is_primary_host", True)
        self._set_active_tab_host(self.tabs)
        return host_map

    def _profile_primary_host_key(self, tree: object) -> str:
        if not isinstance(tree, dict):
            return ""
        node_type = str(tree.get("type", "")).strip().lower()
        if node_type == "host":
            if bool(tree.get("is_primary")):
                return str(tree.get("host_key", "")).strip()
            return ""
        if node_type != "splitter":
            return ""
        children = tree.get("children")
        if not isinstance(children, list):
            return ""
        for child in children:
            key = self._profile_primary_host_key(child)
            if key:
                return key
        return ""

    def _profile_host_nodes(self, tree: object) -> list[dict[str, object]]:
        host_nodes: list[dict[str, object]] = []

        def _walk(node: object) -> None:
            if not isinstance(node, dict):
                return
            node_type = str(node.get("type", "")).strip().lower()
            if node_type == "host":
                host_nodes.append(node)
                return
            if node_type != "splitter":
                return
            children = node.get("children")
            if not isinstance(children, list):
                return
            for child in children:
                _walk(child)

        _walk(tree)
        return host_nodes

    def _restore_workspace_tabs_from_profile(
        self,
        snapshot: dict[str, object],
        host_map: dict[str, WorkspaceTabWidget],
    ) -> list[str]:
        failures: list[str] = []
        tree = snapshot.get("workspace_tree")
        host_nodes = self._profile_host_nodes(tree)
        opened_tabs_by_host: dict[str, list[QWidget]] = {}

        for host_node in host_nodes:
            host_key = str(host_node.get("host_key", "")).strip()
            target_host = host_map.get(host_key, self.tabs)
            raw_tabs = host_node.get("tabs")
            if not isinstance(raw_tabs, list):
                continue
            for raw_tab in raw_tabs:
                if not isinstance(raw_tab, dict):
                    continue
                label = self._profile_tab_display_label(raw_tab)
                if self._connection_load_canceled():
                    break
                self._refresh_connection_load_message(f"Opening {label}...")
                self._pump_ui()
                if self._connection_load_canceled():
                    break
                restored_widget, failure = self._restore_workspace_tab_entry(raw_tab, target_host)
                if restored_widget is not None:
                    opened_tabs_by_host.setdefault(host_key, []).append(restored_widget)
                    self._advance_connection_load_progress(f"Opened {label}.")
                elif failure:
                    failures.append(failure)
                    self._advance_connection_load_progress(f"Skipped {label}.")
                else:
                    self._advance_connection_load_progress(f"Processed {label}.")
                self._pump_ui()
            if self._connection_load_canceled():
                break

        for host_node in host_nodes:
            host_key = str(host_node.get("host_key", "")).strip()
            target_host = host_map.get(host_key)
            if target_host is None:
                continue
            try:
                desired_index = int(host_node.get("current_session_index", -1))
            except (TypeError, ValueError):
                desired_index = -1
            host_tabs = opened_tabs_by_host.get(host_key, [])
            if 0 <= desired_index < len(host_tabs):
                location = self._find_widget_location(host_tabs[desired_index])
                if location is not None and location[0] is target_host:
                    target_host.setCurrentIndex(location[1])
            elif target_host is self.tabs and self.tabs.indexOf(self.details) >= 0:
                self.tabs.setCurrentWidget(self.details)

        return failures

    def _restore_detached_windows_from_profile(self, snapshot: dict[str, object]) -> list[str]:
        failures: list[str] = []
        raw_windows = snapshot.get("detached_windows")
        if not isinstance(raw_windows, list):
            return failures

        for raw_window in raw_windows:
            if not isinstance(raw_window, dict):
                continue
            window, host = self._create_detached_tab_window()
            restored_tabs: list[QWidget] = []

            raw_tabs = raw_window.get("tabs")
            if isinstance(raw_tabs, list):
                for raw_tab in raw_tabs:
                    if not isinstance(raw_tab, dict):
                        continue
                    label = self._profile_tab_display_label(raw_tab)
                    if self._connection_load_canceled():
                        break
                    self._refresh_connection_load_message(f"Opening {label}...")
                    self._pump_ui()
                    if self._connection_load_canceled():
                        break
                    restored_widget, failure = self._restore_workspace_tab_entry(raw_tab, host)
                    if restored_widget is not None:
                        restored_tabs.append(restored_widget)
                        self._advance_connection_load_progress(f"Opened {label}.")
                    elif failure:
                        failures.append(failure)
                        self._advance_connection_load_progress(f"Skipped {label}.")
                    else:
                        self._advance_connection_load_progress(f"Processed {label}.")
                    self._pump_ui()

            if self._connection_load_canceled():
                self._remove_detached_host(host, close_window=True)
                break

            if restored_tabs:
                try:
                    desired_index = int(raw_window.get("current_session_index", -1))
                except (TypeError, ValueError):
                    desired_index = -1
                if 0 <= desired_index < len(restored_tabs):
                    location = self._find_widget_location(restored_tabs[desired_index])
                    if location is not None and location[0] is host:
                        host.setCurrentIndex(location[1])

                placement = _window_placement_from_payload(
                    geometry_b64=str(raw_window.get("window_geometry_b64", "")).strip(),
                    screen_name=str(raw_window.get("window_screen_name", "")).strip(),
                    screen_serial=str(raw_window.get("window_screen_serial", "")).strip(),
                    frame_rect=raw_window.get("window_frame_rect"),
                )
                _restore_or_defer_window_placement(window, placement)
                window.show()
                self._refresh_detached_window_title(host)
                continue

            self._remove_detached_host(host, close_window=True)

        return failures

    def _clear_detached_window_hosts(self) -> None:
        for host in list(self._detached_window_by_host.keys()):
            self._remove_detached_host(host, close_window=True)

    def _restore_workspace_tab_entry(
        self,
        tab_payload: dict[str, object],
        target_host: WorkspaceTabWidget,
    ) -> tuple[QWidget | None, str | None]:
        kind = str(tab_payload.get("kind", "")).strip().upper()
        if not kind:
            return None, "Profile entry missing tab kind."

        before_ids = {id(widget) for _host, _index, widget in self._session_tab_locations()}
        self._set_active_tab_host(target_host)

        session_label = "Local Shell"
        if kind == "LOCAL":
            self._open_local_shell_tab()
        else:
            session_id = str(tab_payload.get("session_id", "")).strip()
            if not session_id:
                return None, f"{kind}: missing session id."
            session = self._session_service.by_id(session_id)
            if session is None:
                return None, f"{kind}: session {session_id} was not found."
            session_label = (session.name or "").strip() or session.host.strip() or session_id
            self._open_session_for_profile_tab(kind, session)

        QApplication.processEvents()
        restored_widget = self._first_new_session_tab(before_ids, target_host)
        if restored_widget is None:
            return None, f"{kind}: failed to open {session_label}."

        custom_title = tab_payload.get("custom_title")
        if isinstance(custom_title, str):
            restored_widget.setProperty("tab_title_custom", custom_title.strip())
        group_id = str(tab_payload.get("tab_group_id", "")).strip()
        self._set_tab_group_id(restored_widget, group_id or None)
        return restored_widget, None

    def _first_new_session_tab(self, before_ids: set[int], preferred_host: WorkspaceTabWidget) -> QWidget | None:
        for host, _index, widget in self._session_tab_locations():
            if id(widget) in before_ids:
                continue
            if host is preferred_host:
                return widget
        for _host, _index, widget in self._session_tab_locations():
            if id(widget) in before_ids:
                continue
            return widget
        return None

    def _open_session_for_profile_tab(self, kind: str, session: Session) -> None:
        if kind == "SFTP":
            self._open_sftp_tab(session)
            return
        if kind == "SSH" and session.protocol == Protocol.SSH:
            self._open_ssh_tab(session)
            return
        if kind == "TELNET" and session.protocol == Protocol.TELNET:
            self._open_telnet_tab(session)
            return
        if kind == "SERIAL" and session.protocol == Protocol.SERIAL:
            self._open_serial_tab(session)
            return
        self._connect_session(session)

    def _restore_workspace_group_metadata_from_profile(self, snapshot: dict[str, object]) -> None:
        raw_group_names = snapshot.get("tab_group_names")
        cleaned_group_names: dict[str, str] = {}
        if isinstance(raw_group_names, dict):
            for raw_group_id, raw_name in raw_group_names.items():
                group_id = str(raw_group_id).strip()
                name = str(raw_name).strip()
                if group_id and name:
                    cleaned_group_names[group_id] = name
        self._tab_group_names = cleaned_group_names
        try:
            self._next_tab_group_id = max(1, int(snapshot.get("next_tab_group_id", 1)))
        except (TypeError, ValueError):
            self._next_tab_group_id = 1
        try:
            self._next_tab_group_name_id = max(1, int(snapshot.get("next_tab_group_name_id", 1)))
        except (TypeError, ValueError):
            self._next_tab_group_name_id = 1
        self._normalize_tab_groups()

    def _restore_workspace_selection_from_profile(
        self,
        snapshot: dict[str, object],
        host_map: dict[str, WorkspaceTabWidget],
    ) -> None:
        active_key = str(snapshot.get("active_host_key", "")).strip()
        active_host = host_map.get(active_key)
        if active_host is None:
            active_host = self.tabs
        self._set_active_tab_host(active_host)

    def _restore_workspace_window_state_from_profile(self, snapshot: dict[str, object]) -> None:
        try:
            self._session_list_last_width = max(220, int(snapshot.get("session_list_last_width", self._session_list_last_width)))
        except (TypeError, ValueError):
            self._session_list_last_width = max(220, self._session_list_last_width)

        if self._is_session_list_floating():
            self._dock_session_list_panel("shown", persist=False, close_window=True)

        placement = _window_placement_from_payload(
            geometry_b64=str(snapshot.get("window_geometry_b64", "")).strip(),
            screen_name=str(snapshot.get("window_screen_name", "")).strip(),
            screen_serial=str(snapshot.get("window_screen_serial", "")).strip(),
            frame_rect=snapshot.get("window_frame_rect"),
        )
        _restore_or_defer_window_placement(self, placement)

        splitter_b64 = str(snapshot.get("main_splitter_b64", "")).strip()
        if splitter_b64:
            self._session_list_docked_splitter_b64 = splitter_b64
            try:
                splitter = QByteArray.fromBase64(splitter_b64.encode("ascii"))
            except Exception:
                splitter = QByteArray()
            if not splitter.isEmpty():
                self._main_splitter.restoreState(splitter)

        profile_mode = self._normalized_session_list_mode(str(snapshot.get("session_list_mode", self._session_list_mode)))
        float_placement = _window_placement_from_payload(
            geometry_b64=str(snapshot.get("session_list_window_geometry_b64", "")).strip(),
            screen_name=str(snapshot.get("session_list_window_screen_name", "")).strip(),
            screen_serial=str(snapshot.get("session_list_window_screen_serial", "")).strip(),
            frame_rect=snapshot.get("session_list_window_frame_rect"),
        )
        self._set_session_list_mode(profile_mode, persist=False, float_placement=float_placement)
        if profile_mode == "float":
            return
        profile_visible = bool(snapshot.get("session_list_visible", profile_mode == "shown"))
        if profile_mode == "auto":
            self._set_session_list_visible(profile_visible)
            if profile_visible:
                self._session_list_auto_hide_timer.start()
        else:
            self._set_session_list_visible(True)

    def _open_registered_tool(self, tool_key: str, *, activate: bool = True) -> bool:
        entry = TOOL_REGISTRY_BY_KEY.get(tool_key)
        if entry is None:
            return False
        opener = getattr(self, entry.opener_name, None)
        if not callable(opener):
            return False
        opener(activate=activate)
        return True

    def _open_profile_startup_tools(self, tool_keys: list[str]) -> None:
        for tool_key in normalize_profile_startup_tool_keys(tool_keys):
            self._open_registered_tool(tool_key, activate=False)

    def _build_tools_menu(self) -> QMenu:
        menu = QMenu(self)
        style = _ToolMenuIconStyle()
        style.setParent(menu)
        menu.setStyle(style)
        menu._snakesh_tool_menu_icon_style = style  # type: ignore[attr-defined]
        for entry in TOOL_REGISTRY:
            action = menu.addAction(tool_menu_icon(entry.key), entry.label)
            action.triggered.connect(lambda _checked=False, key=entry.key: self._open_registered_tool(key))
        about_action = menu.addAction(app_menu_icon(), "About SnakeSh")
        about_action.triggered.connect(self._open_about_dialog)
        return menu

    def _open_about_dialog(self) -> None:
        author_website = "https://ruckman.net/support.html"
        app = QApplication.instance()
        app_name = "SnakeSh"
        if app is not None:
            display_name = app.applicationDisplayName().strip()
            basic_name = app.applicationName().strip()
            app_name = display_name or basic_name or app_name
        os_type = _os_type_summary()
        cpu = self._cpu_summary()
        ram = self._format_storage_bytes(self._total_ram_bytes())
        disk_space = self._disk_space_summary()

        dialog = QDialog(self)
        dialog.setWindowTitle(f"About {app_name}")
        dialog.setModal(True)
        dialog.setMinimumWidth(560)

        layout = QVBoxLayout(dialog)

        header_layout = QHBoxLayout()
        icon_label = QLabel()
        icon_label.setFixedSize(72, 72)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon = self.windowIcon()
        if icon.isNull() and app is not None:
            icon = app.windowIcon()
        if not icon.isNull():
            icon_label.setPixmap(icon.pixmap(64, 64))
        else:
            icon_label.setText("N/A")
        header_layout.addWidget(icon_label, 0, Qt.AlignmentFlag.AlignTop)

        title_label = QLabel(f"{app_name}\nVersion {__version__}")
        title_label.setStyleSheet("font-weight: 600; font-size: 14px;")
        header_layout.addWidget(title_label, 1, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(header_layout)

        details_lines = [
            f"App Name: {app_name}",
            f"App Version: {__version__}",
            f"OS Type: {os_type}",
            f"CPU: {cpu}",
            f"RAM: {ram}",
            f"Disk Space: {disk_space}",
            "Author: William Ruckman",
        ]
        details_label = QLabel("\n".join(details_lines))
        details_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        details_label.setWordWrap(True)
        layout.addWidget(details_label)

        website_label = QLabel(f'Author Website: <a href="{author_website}">{author_website}</a>')
        website_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        website_label.setOpenExternalLinks(False)
        website_label.linkActivated.connect(self._open_external_link)
        layout.addWidget(website_label)

        buttons = QDialogButtonBox(QDialogButtonBox.Close, parent=dialog)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        dialog.exec()

    @Slot(str)
    def _open_external_link(self, url: str) -> None:
        if QDesktopServices.openUrl(QUrl(url)):
            return
        QMessageBox.warning(self, "Open Link Failed", f"Unable to open this URL:\n{url}")

    @staticmethod
    def _cpu_summary() -> str:
        cpu = platform.processor().strip()
        if not cpu and platform.system().lower() == "linux":
            try:
                with open("/proc/cpuinfo", encoding="utf-8", errors="ignore") as handle:
                    for line in handle:
                        if line.lower().startswith("model name"):
                            cpu = line.split(":", 1)[1].strip()
                            break
            except OSError:
                pass
        if not cpu:
            cpu = platform.machine().strip() or "Unknown"
        cores = os.cpu_count()
        if cores and cores > 0:
            return f"{cpu} ({cores} cores)"
        return cpu

    @staticmethod
    def _total_ram_bytes() -> int | None:
        if platform.system().lower() == "windows":
            try:
                import ctypes

                class MEMORYSTATUSEX(ctypes.Structure):
                    _fields_ = [
                        ("dwLength", ctypes.c_ulong),
                        ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                    ]

                state = MEMORYSTATUSEX()
                state.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
                if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(state)):
                    return int(state.ullTotalPhys)
            except Exception:
                return None
            return None
        page_size_keys = ("SC_PAGE_SIZE", "SC_PAGESIZE")
        for page_size_key in page_size_keys:
            try:
                page_size = int(os.sysconf(page_size_key))
                pages = int(os.sysconf("SC_PHYS_PAGES"))
            except (AttributeError, OSError, ValueError):
                continue
            if page_size > 0 and pages > 0:
                return page_size * pages
        return None

    @staticmethod
    def _disk_space_summary() -> str:
        try:
            target = Path.home()
            usage = shutil.disk_usage(target)
        except OSError:
            return "Unknown"
        total = MainWindow._format_storage_bytes(usage.total)
        free = MainWindow._format_storage_bytes(usage.free)
        return f"{total} total ({free} free)"

    @staticmethod
    def _format_storage_bytes(value: int | None) -> str:
        if value is None or value < 0:
            return "Unknown"
        size = float(value)
        units = ["B", "KB", "MB", "GB", "TB", "PB"]
        unit_idx = 0
        while size >= 1024 and unit_idx < len(units) - 1:
            size /= 1024.0
            unit_idx += 1
        if unit_idx == 0:
            return f"{int(size)} {units[unit_idx]}"
        return f"{size:.1f} {units[unit_idx]}"

    def _prune_tool_processes(self, tool_key: str | None = None) -> None:
        keys = [tool_key] if tool_key else list(self._tool_processes)
        for key in keys:
            processes = self._tool_processes.get(key, [])
            running = [process for process in processes if process.poll() is None]
            if running:
                self._tool_processes[key] = running
            else:
                self._tool_processes.pop(key, None)

    def _launch_tool_process(
        self,
        tool_key: str,
        *,
        arguments: list[str] | None = None,
    ) -> subprocess.Popen[bytes] | None:
        self._prune_tool_processes(tool_key)
        env = dict(os.environ)
        placement = _capture_window_placement(self)
        if placement.has_data():
            env[_TOOL_LAUNCH_PLACEMENT_ENV] = json.dumps(placement_to_payload(placement))
        result = launch_standalone_tool(tool_key, arguments=arguments, env=env)
        if result.process is None:
            return None
        self._tool_processes.setdefault(tool_key, []).append(result.process)
        return result.process

    def _open_whois_tool(self, _checked: bool = False, *, activate: bool = True) -> None:
        _ = activate
        self._launch_tool_process("whois")

    def _open_asn_lookup_tool(
        self,
        _checked: bool = False,
        *,
        activate: bool = True,
    ) -> subprocess.Popen[bytes] | None:
        _ = activate
        return self._launch_tool_process("asn_lookup")

    def _open_dig_tool(self, _checked: bool = False, *, activate: bool = True) -> None:
        _ = activate
        self._launch_tool_process("dig")

    def _open_traceroute_tool(self, _checked: bool = False, *, activate: bool = True) -> None:
        _ = activate
        self._launch_tool_process("traceroute")

    def _open_ping_tool(
        self,
        _checked: bool = False,
        *,
        packet_size: int | None = None,
        ipv6: bool | None = None,
        activate: bool = True,
    ) -> subprocess.Popen[bytes] | None:
        _ = activate
        return self._launch_tool_process(
            "ping",
            arguments=ping_tool_arguments(packet_size=packet_size, ipv6=ipv6),
        )

    def _open_ip_scan_tool(
        self,
        _checked: bool = False,
        *,
        activate: bool = True,
    ) -> subprocess.Popen[bytes] | None:
        _ = activate
        return self._launch_tool_process("ip_scan")

    def _open_mtu_calculator(self, _checked: bool = False, *, activate: bool = True) -> None:
        _ = activate
        self._launch_tool_process("mtu_calculator")

    def _prefill_ping_from_mtu(self, packet_size: int, ipv6: bool) -> None:
        self._open_ping_tool(packet_size=packet_size, ipv6=ipv6)

    def _open_resource_monitor_tool(self, _checked: bool = False, *, activate: bool = True) -> None:
        _ = activate
        self._launch_tool_process("resource_monitor")

    def _open_network_inspector_tool(self, _checked: bool = False, *, activate: bool = True) -> None:
        _ = activate
        self._launch_tool_process("network_inspector")

    def _open_file_hash_tool(self, _checked: bool = False, *, activate: bool = True) -> None:
        _ = activate
        self._launch_tool_process("file_hash")

    def _open_oui_lookup_tool(self, _checked: bool = False, *, activate: bool = True) -> None:
        _ = activate
        self._launch_tool_process("oui_lookup")

    def _open_web_server_tool(self, _checked: bool = False, *, activate: bool = True) -> None:
        _ = activate
        self._maybe_prune_web_server_logs(force=True)
        self._launch_tool_process("web_server")

    def _open_syslog_snmp_monitor_tool(self, _checked: bool = False, *, activate: bool = True) -> None:
        _ = activate
        self._launch_tool_process("syslog_snmp_monitor")

    def _open_help_tool(self, _checked: bool = False, *, activate: bool = True) -> None:
        _ = activate
        self._launch_tool_process("help")

    def _open_subnet_calculator(self, _checked: bool = False, *, activate: bool = True) -> None:
        _ = activate
        self._launch_tool_process("subnet_calculator")

    def _open_password_generator(self, _checked: bool = False, *, activate: bool = True) -> None:
        _ = activate
        self._launch_tool_process("password_generator")

    def _open_diff_tool(self, _checked: bool = False, *, activate: bool = True) -> None:
        _ = activate
        self._launch_tool_process("diff")

    def _open_settings(self) -> None:
        linux_desktop_actions = platform.system().lower() == "linux"
        launcher_management_available = platform.system().lower() in {"linux", "windows", "darwin"}
        settings_before_dialog = AppSettings.from_dict(self._settings.to_dict())
        dialog = SettingsDialog(
            settings=settings_before_dialog,
            parent=self,
            on_export_requested=self._export_from_settings_menu,
            on_import_requested=self._import_from_settings_menu,
            on_third_party_io_requested=self._open_third_party_io,
            on_test_secrets_requested=self._test_secrets_backend_from_settings,
            on_setup_secrets_requested=self._setup_secrets_backend_from_settings,
            on_desktop_install_requested=(
                self._install_or_repair_linux_desktop_integration if linux_desktop_actions else None
            ),
            on_desktop_uninstall_requested=(
                self._remove_linux_desktop_integration if linux_desktop_actions else None
            ),
            on_manage_tool_launchers_requested=(
                self._manage_tool_launchers if launcher_management_available else None
            ),
            backend_auth_state=self._secrets_service.backend_auth_state(),
            on_preview_requested=self._preview_settings_from_dialog,
        )
        if not dialog.exec():
            self._apply_app_settings(
                settings_before_dialog,
                persist=False,
                apply_runtime_side_effects=False,
                show_status=False,
            )
            return
        auth_updates = dialog.build_backend_auth_updates()
        if auth_updates:
            try:
                self._secrets_service.apply_backend_auth_updates(auth_updates)
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(
                    self,
                    "Backend Auth Storage",
                    f"Unable to save one or more backend auth values:\n{exc}",
                )
        self._apply_app_settings(dialog.build_settings())

    def _preview_settings_from_dialog(self, settings: AppSettings) -> None:
        self._apply_app_settings(
            settings,
            persist=False,
            apply_runtime_side_effects=False,
            show_status=False,
        )

    def _offer_linux_desktop_integration_on_startup(self) -> None:
        if platform.system().lower() != "linux":
            return
        if not is_appimage():
            return
        try:
            integration_installed = is_desktop_integration_installed()
        except Exception:
            return
        if integration_installed:
            self._offer_linux_desktop_update_prompt_on_startup()
            return
        if self._settings.linux_desktop_prompt_dismissed:
            return

        prompt = QMessageBox(self)
        prompt.setIcon(QMessageBox.Information)
        prompt.setWindowTitle("Install to App Menu")
        prompt.setText("Install SnakeSh to your app menu for one-click launch?")
        prompt.setInformativeText(
            "This copies the AppImage to ~/.local/lib/SnakeSh and creates desktop/app-menu entries."
        )
        install_btn = prompt.addButton("Install to App Menu", QMessageBox.AcceptRole)
        run_once_btn = prompt.addButton("Run Once", QMessageBox.RejectRole)
        dont_ask_btn = prompt.addButton("Don't Ask Again", QMessageBox.DestructiveRole)
        prompt.setDefaultButton(install_btn)
        prompt.exec()

        clicked = prompt.clickedButton()
        if clicked is install_btn:
            ok, message = self._install_or_repair_linux_desktop_integration()
            if ok:
                QMessageBox.information(self, "Desktop Integration", message)
            else:
                QMessageBox.warning(self, "Desktop Integration", message)
            return
        if clicked is dont_ask_btn:
            self._settings.linux_desktop_prompt_dismissed = True
            self._settings_service.save(self._settings)
            self.statusBar().showMessage("Startup desktop-install prompt disabled.", 5000)
            return
        if clicked is run_once_btn:
            self.statusBar().showMessage("Running without desktop integration.", 5000)

    def _offer_linux_desktop_update_prompt_on_startup(self) -> None:
        current_version = __version__.strip()
        if not current_version:
            return
        if self._settings.linux_desktop_last_update_prompt_version.strip() == current_version:
            return
        try:
            if not desktop_integration_needs_update(current_version=current_version):
                return
            installed_version = installed_desktop_integration_version() or "unknown"
        except Exception:
            return

        prompt = QMessageBox(self)
        prompt.setIcon(QMessageBox.Information)
        prompt.setWindowTitle("Desktop Integration Update")
        prompt.setText("SnakeSh desktop integration can be updated.")
        prompt.setInformativeText(
            f"Installed integration version: {installed_version}\n"
            f"Running SnakeSh version: {current_version}\n\n"
            "Update desktop integration now?"
        )
        update_btn = prompt.addButton("Update Integration", QMessageBox.AcceptRole)
        later_btn = prompt.addButton("Later (this version)", QMessageBox.RejectRole)
        prompt.setDefaultButton(update_btn)
        prompt.exec()

        clicked = prompt.clickedButton()
        if clicked is update_btn:
            ok, message = self._install_or_repair_linux_desktop_integration()
            if ok:
                QMessageBox.information(self, "Desktop Integration", message)
            else:
                QMessageBox.warning(self, "Desktop Integration", message)
            return
        if clicked is later_btn or clicked is None:
            self._settings.linux_desktop_last_update_prompt_version = current_version
            self._settings_service.save(self._settings)
            self.statusBar().showMessage("Desktop integration update deferred for this version.", 5000)

    def _install_or_repair_linux_desktop_integration(self) -> tuple[bool, str]:
        try:
            installed_path = install_desktop_integration()
        except LinuxDesktopIntegrationError as exc:
            return False, str(exc)
        self._settings.linux_desktop_prompt_dismissed = False
        self._settings.linux_desktop_last_update_prompt_version = __version__
        self._settings_service.save(self._settings)
        return True, f"Desktop integration installed at:\n{installed_path}"

    def _remove_linux_desktop_integration(self) -> tuple[bool, str]:
        try:
            removed = uninstall_desktop_integration()
        except LinuxDesktopIntegrationError as exc:
            return False, str(exc)
        if removed:
            return True, "Desktop integration and SnakeSh tool launchers removed. Your SnakeSh data remains untouched."
        return True, "Desktop integration and SnakeSh tool launchers are already absent."

    def _manage_tool_launchers(self) -> None:
        dialog = ToolLauncherManagerDialog(self)
        dialog.set_selected_tool_keys(installed_tool_launcher_keys())
        if not dialog.exec():
            return
        selected_tool_keys = dialog.selected_tool_keys()
        progress = QProgressDialog("Updating tool launchers...", "", 0, 0, self)
        progress.setWindowTitle("Tool Launchers")
        progress.setWindowModality(Qt.ApplicationModal)
        progress.setCancelButton(None)
        progress.setMinimumDuration(0)
        result_holder: dict[str, object] = {}
        thread = QThread(self)
        worker = ToolLauncherSyncWorker(selected_tool_keys)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.succeeded.connect(lambda result: result_holder.update(result=result))
        worker.failed.connect(lambda message: result_holder.update(error=message))
        worker.finished.connect(progress.accept)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.start()
        progress.exec()
        thread.wait(5000)
        error = result_holder.get("error")
        if error:
            QMessageBox.warning(self, "Tool Launchers", str(error))
            return
        result = result_holder.get("result")
        if result is None:
            QMessageBox.warning(self, "Tool Launchers", "Tool launcher update did not complete.")
            return
        QMessageBox.information(self, "Tool Launchers", launcher_sync_summary(result))

    def _open_third_party_io(self) -> None:
        dialog = ThirdPartyImportExportDialog(
            parent=self,
            on_import_securecrt_requested=self._import_securecrt_from_settings_menu,
            on_import_openssh_requested=self._import_openssh_from_settings_menu,
            on_import_putty_requested=self._import_putty_from_settings_menu,
        )
        dialog.exec()

    def _apply_app_settings(
        self,
        settings: AppSettings,
        *,
        persist: bool = True,
        apply_runtime_side_effects: bool = True,
        show_status: bool = True,
        imported: bool = False,
        source_platform: str | None = None,
    ) -> None:
        if imported:
            self._settings = self._settings_service.sanitize_imported_settings(
                settings,
                source_platform=source_platform,
            )
        else:
            self._settings = AppSettings.from_dict(settings.to_dict())
        normalized_shortcut = self._normalize_main_window_shortcut_text(self._settings.main_window_fullscreen_shortcut)
        if not normalized_shortcut:
            normalized_shortcut = self._normalize_main_window_shortcut_text(
                AppSettings.defaults().main_window_fullscreen_shortcut
            )
        self._settings.main_window_fullscreen_shortcut = normalized_shortcut
        if persist:
            self._settings_service.save(self._settings)
        self._update_main_fullscreen_shortcut()
        self._sync_main_fullscreen_control_visibility()
        self._session_list_mode = self._normalized_session_list_mode(self._settings.session_list_visibility_mode)
        self._sync_session_list_mode_actions()
        self._apply_session_list_mode(self._session_list_mode, persist=False)
        if apply_runtime_side_effects:
            self._maybe_prune_session_logs(force=True)
            self._maybe_prune_web_server_logs(force=True)
        app = QApplication.instance()
        if app:
            apply_theme(app, self._settings)
        self._apply_session_details_style()
        resolved_fonts: list[str] = []
        for host in self._tab_hosts:
            for index in range(host.count()):
                widget = host.widget(index)
                if isinstance(widget, TerminalTab):
                    widget.apply_settings(self._terminal_settings_for_tab(widget))
                    session_id = widget.property("session_id")
                    session = self._session_service.by_id(session_id) if isinstance(session_id, str) else None
                    auto_started = bool(widget.property("global_logging_auto_started"))
                    kind = str(widget.property("session_kind") or "").strip().upper()
                    if apply_runtime_side_effects:
                        if self._settings.global_session_logging_enabled:
                            if session is not None and kind != "LOCAL" and not widget.is_logging_enabled():
                                self._maybe_start_global_logging_for_tab(widget, session)
                        elif auto_started and widget.is_logging_enabled():
                            widget.stop_logging()
                            widget.setProperty("global_logging_auto_started", False)
                    resolved_fonts.append(widget.resolved_font_family())
        self._apply_tab_styles()
        self._schedule_tool_settings_sync(self._settings, preview=not persist)
        if show_status and resolved_fonts:
            current = ", ".join(sorted(set(resolved_fonts)))
            self.statusBar().showMessage(f"Applied terminal font setting: {self._settings.terminal_font_family} (resolved: {current})", 7000)

    def _schedule_tool_settings_sync(self, settings: AppSettings, *, preview: bool) -> None:
        self._pending_tool_settings_sync = (AppSettings.from_dict(settings.to_dict()), bool(preview))
        if not self._tool_settings_sync_timer.isActive():
            self._tool_settings_sync_timer.start(0)

    def _flush_tool_settings_sync(self) -> None:
        pending = self._pending_tool_settings_sync
        self._pending_tool_settings_sync = None
        if pending is None:
            return
        settings, preview = pending
        queue_tool_settings_sync(settings, preview=preview)

    def _apply_terminal_color_override_from_session(self, tab: TerminalTab, session: Session) -> None:
        tab.setProperty("terminal_color_override_enabled", bool(session.terminal_color_override_enabled))
        tab.setProperty("terminal_bg_override", str(session.terminal_bg_color or ""))
        tab.setProperty("terminal_fg_override", str(session.terminal_fg_color or ""))

    def _terminal_settings_for_tab(self, tab: TerminalTab) -> AppSettings:
        merged = AppSettings.from_dict(self._settings.to_dict())
        if not bool(tab.property("terminal_color_override_enabled")):
            return merged
        bg_override = tab.property("terminal_bg_override")
        fg_override = tab.property("terminal_fg_override")
        resolved_bg, resolved_fg = resolve_terminal_default_colors(
            merged,
            bg_override=bg_override if isinstance(bg_override, str) else "",
            fg_override=fg_override if isinstance(fg_override, str) else "",
        )
        merged.terminal_bg = resolved_bg
        merged.terminal_fg = resolved_fg
        merged.terminal_classic_default_colors = False
        return merged

    def _export_from_settings_menu(self) -> None:
        all_sessions = self._session_service.all()
        dialog = ExportSelectionDialog(all_sessions, self)
        if not dialog.exec():
            return
        include_settings, include_sessions, password, selected_ids = dialog.export_options()
        export_path, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Export SnakeSh Data",
            "",
            "SnakeSh Export (*.ssx);;All files (*)",
        )
        if not export_path:
            return
        target_path = self._normalize_export_path(export_path, selected_filter)
        sessions_to_export: list[Session] | None = None
        folders_to_export: list[str] | None = None
        if include_sessions:
            if selected_ids is None:
                sessions_to_export = list(all_sessions)
            else:
                selected_set = set(selected_ids)
                sessions_to_export = [session for session in all_sessions if session.id in selected_set]
            folders_to_export = self._folders_for_exported_sessions(sessions_to_export)
        try:
            self._backup_service.export_bundle(
                target_path,
                settings=self._settings if include_settings else None,
                sessions=sessions_to_export,
                folders=folders_to_export,
                password=password.strip() or None,
            )
            QMessageBox.information(self, "Export Complete", f"Export saved to:\n{target_path}")
        except BackupError as exc:
            QMessageBox.critical(self, "Export Failed", str(exc))

    def _folders_for_exported_sessions(self, sessions: list[Session]) -> list[str]:
        folders: set[str] = set()
        for session in sessions:
            path = self._session_service.normalize_folder_path(session.folder)
            folders.add(path)
            parent = path
            while "/" in parent:
                parent = parent.rsplit("/", 1)[0]
                folders.add(parent)
        if not folders and sessions:
            folders.add("Default")
        return sorted(folders, key=lambda value: (value.count("/"), value.lower()))

    def _import_from_settings_menu(self, import_path: str | None = None) -> AppSettings | None:
        selected_path = import_path
        if selected_path is None:
            chosen_path, _ = QFileDialog.getOpenFileName(
                self,
                "Import SnakeSh Data",
                "",
                "SnakeSh Export (*.ssx);;All files (*)",
            )
            if not chosen_path:
                return None
            selected_path = chosen_path

        path = Path(selected_path).expanduser()
        if not path.exists() or not path.is_file():
            QMessageBox.warning(self, "Import Failed", f"Import file does not exist:\n{path}")
            return None
        if path.suffix.lower() not in _SNAKESH_IMPORT_SUFFIXES:
            QMessageBox.warning(self, "Import Failed", f"Unsupported import file extension:\n{path}")
            return None

        payload: BackupPayload | None = None
        password: str | None = None
        while True:
            try:
                payload = self._backup_service.import_bundle(path, password=password)
                break
            except BackupPasswordRequiredError:
                entered, ok = QInputDialog.getText(
                    self,
                    "Backup Password Required",
                    "Enter backup password:",
                    QLineEdit.Password,
                )
                if not ok:
                    return None
                password = entered
                continue
            except BackupInvalidPasswordError:
                QMessageBox.warning(self, "Invalid Password", "Incorrect password for this backup file.")
                password = None
                continue
            except (BackupFormatError, BackupError) as exc:
                QMessageBox.critical(self, "Import Failed", str(exc))
                return None

        if payload is None:
            return None

        has_settings = payload.has_settings
        has_sessions = payload.has_sessions
        if not has_settings and not has_sessions:
            QMessageBox.warning(self, "Import Failed", "Backup does not contain settings or sessions.")
            return None

        apply_settings = False
        if has_settings:
            settings_answer = QMessageBox.question(
                self,
                "Import Settings",
                (
                    "Imported settings will overwrite your current application settings, including "
                    "appearance, fast commands, workspace profiles, and startup/security options.\n\nContinue?"
                ),
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
                QMessageBox.Yes,
            )
            if settings_answer == QMessageBox.Cancel:
                return None
            apply_settings = settings_answer == QMessageBox.Yes

        session_mode = "skip"
        if has_sessions:
            session_box = QMessageBox(self)
            session_box.setIcon(QMessageBox.Warning)
            session_box.setWindowTitle("Import Sessions")
            session_box.setText("Choose how to apply imported sessions.")
            session_box.setInformativeText(
                "Overwrite replaces your current saved sessions/folders. "
                "Merge keeps current sessions and adds imported sessions."
            )
            overwrite_button = session_box.addButton("Overwrite", QMessageBox.AcceptRole)
            merge_button = session_box.addButton("Merge", QMessageBox.ActionRole)
            skip_button = session_box.addButton("Skip", QMessageBox.RejectRole)
            cancel_button = session_box.addButton(QMessageBox.Cancel)
            session_box.setDefaultButton(merge_button)
            session_box.exec()
            clicked = session_box.clickedButton()
            if clicked == cancel_button:
                return None
            if clicked == overwrite_button:
                session_mode = "overwrite"
            elif clicked == merge_button:
                session_mode = "merge"
            else:
                session_mode = "skip"

        if not apply_settings and session_mode == "skip":
            return self._settings

        if apply_settings and payload.settings:
            self._apply_app_settings(
                payload.settings,
                imported=True,
                source_platform=payload.source_platform,
            )

        if session_mode in ("overwrite", "merge"):
            existing_sessions = self._session_service.all()
            existing_snapshot = {session.id: session for session in existing_sessions}
            if session_mode == "overwrite":
                self._session_service.replace_all(payload.sessions, payload.folders)
                new_ids = {session.id for session in self._session_service.all()}
                removed_ids = [session_id for session_id in existing_snapshot if session_id not in new_ids]
                for session_id in removed_ids:
                    session = existing_snapshot.get(session_id)
                    if session:
                        self._credential_service.clear_password(session)
                    self._close_session_tab(session_id)
                self.statusBar().showMessage(
                    f"Imported {len(payload.sessions)} session(s) with overwrite.", 7000
                )
            else:
                added = self._session_service.merge_sessions_no_overwrite(payload.sessions, payload.folders)
                self.statusBar().showMessage(f"Merged {added} imported session(s).", 7000)
            self._refresh_tree()

        return self._settings

    def prompt_import_startup_file(self, file_path: str) -> None:
        path = Path(file_path).expanduser()
        try:
            resolved = path.resolve()
        except Exception:
            resolved = path
        if not resolved.exists() or not resolved.is_file():
            QMessageBox.warning(
                self,
                "Import File Not Found",
                f"SnakeSh was launched with an import file that does not exist:\n{resolved}",
            )
            return
        if resolved.suffix.lower() not in _SNAKESH_IMPORT_SUFFIXES:
            QMessageBox.warning(
                self,
                "Unsupported Import File",
                (
                    "SnakeSh can import .ssx export files.\n\n"
                    f"Received:\n{resolved}"
                ),
            )
            return
        answer = QMessageBox.question(
            self,
            "Import SnakeSh Data",
            f"SnakeSh was opened with this import file:\n{resolved}\n\nImport it now?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if answer != QMessageBox.Yes:
            return
        self._import_from_settings_menu(import_path=str(resolved))

    @staticmethod
    def _normalize_export_path(raw_path: str, _selected_filter: str) -> Path:
        target = Path(raw_path).expanduser()
        if target.suffix.lower() == ".ssx":
            return target
        return target.with_suffix(".ssx")

    def _test_secrets_backend_from_settings(
        self,
        settings: AppSettings,
        auth_updates: dict[str, str | None] | None = None,
    ) -> tuple[bool, str]:
        health = self._secrets_service.check_backend(settings=settings, auth_overrides=auth_updates)
        return health.ok, health.message

    def _setup_secrets_backend_from_settings(
        self,
        settings: AppSettings,
        auth_updates: dict[str, str | None] | None = None,
    ) -> tuple[bool, str]:
        health = self._secrets_service.setup_backend(settings=settings, auth_overrides=auth_updates)
        return health.ok, health.message

    def _import_securecrt_from_settings_menu(self) -> None:
        source_path = self._choose_securecrt_import_path()
        if not source_path:
            return
        if Path(source_path).suffix.lower() != ".xml":
            QMessageBox.warning(self, "Unsupported Format", "SecureCRT import currently accepts XML files only.")
            return

        try:
            report = self._securecrt_codec.import_from_path(Path(source_path))
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "SecureCRT Import Failed", str(exc))
            return

        if not report.imported_sessions:
            message = "No importable SecureCRT sessions were found in the selected path."
            if report.warnings:
                warning_preview = "\n".join(report.warnings[:6])
                if len(report.warnings) > 6:
                    warning_preview += f"\n... and {len(report.warnings) - 6} more warning(s)."
                message += f"\n\nDetails:\n{warning_preview}"
            QMessageBox.warning(self, "SecureCRT Import", message)
            return

        mode_box = QMessageBox(self)
        mode_box.setIcon(QMessageBox.Warning)
        mode_box.setWindowTitle("Import SecureCRT Sessions")
        mode_box.setText(
            f"Found {report.imported_count} session(s) in {report.scanned_files} source item(s).\n"
            f"Detected folder structure entries: {len(report.folders)}.\n\n"
            "Choose how to apply imported sessions."
        )
        overwrite_button = mode_box.addButton("Overwrite Existing", QMessageBox.AcceptRole)
        merge_button = mode_box.addButton("Merge", QMessageBox.ActionRole)
        cancel_button = mode_box.addButton(QMessageBox.Cancel)
        mode_box.setDefaultButton(merge_button)
        mode_box.exec()

        clicked = mode_box.clickedButton()
        if clicked == cancel_button:
            return

        existing_sessions = self._session_service.all()
        existing_snapshot = {session.id: session for session in existing_sessions}

        if clicked == overwrite_button:
            self._session_service.replace_all(report.imported_sessions, report.folders)
            new_ids = {session.id for session in self._session_service.all()}
            removed_ids = [session_id for session_id in existing_snapshot if session_id not in new_ids]
            for session_id in removed_ids:
                session = existing_snapshot.get(session_id)
                if session:
                    self._credential_service.clear_password(session)
                self._close_session_tab(session_id)
            status = f"Imported {report.imported_count} SecureCRT session(s) with overwrite."
        else:
            added = self._session_service.merge_sessions_no_overwrite(report.imported_sessions, report.folders)
            status = f"Merged {added} SecureCRT session(s)."

        self._refresh_tree()
        self.statusBar().showMessage(status, 7000)

        summary = [status]
        if report.folders:
            summary.append(f"Imported folder structure entries: {len(report.folders)}")
        if report.skipped_files:
            summary.append(f"Skipped files: {len(report.skipped_files)}")
        if report.warnings:
            warning_preview = "\n".join(report.warnings[:5])
            if len(report.warnings) > 5:
                warning_preview += f"\n... and {len(report.warnings) - 5} more warning(s)."
            summary.append(f"Warnings:\n{warning_preview}")
        QMessageBox.information(self, "SecureCRT Import Complete", "\n\n".join(summary))

    def _import_openssh_from_settings_menu(self) -> None:
        suggested = str((Path.home() / ".ssh" / "config").expanduser())
        source_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select OpenSSH Config",
            suggested,
            "OpenSSH Config (config *.conf);;All Files (*)",
        )
        if not source_path:
            return

        try:
            report = self._third_party_import.import_openssh_config(Path(source_path))
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "OpenSSH Import Failed", str(exc))
            return
        self._apply_third_party_import_report(report)

    def _import_putty_from_settings_menu(self) -> None:
        try:
            report = self._third_party_import.import_putty_registry()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "PuTTY Import Failed", str(exc))
            return
        self._apply_third_party_import_report(report)

    def _apply_third_party_import_report(self, report: ThirdPartyImportReport) -> None:
        if not report.imported_sessions:
            message = f"No importable {report.source_name} sessions were found."
            if report.warnings:
                warning_preview = "\n".join(report.warnings[:6])
                if len(report.warnings) > 6:
                    warning_preview += f"\n... and {len(report.warnings) - 6} more warning(s)."
                message += f"\n\nDetails:\n{warning_preview}"
            QMessageBox.warning(self, f"{report.source_name} Import", message)
            return

        mode_box = QMessageBox(self)
        mode_box.setIcon(QMessageBox.Warning)
        mode_box.setWindowTitle(f"Import {report.source_name} Sessions")
        mode_box.setText(
            f"Found {report.imported_count} session(s) from {report.source_name}.\n"
            f"Scanned entries: {report.scanned_entries}.\n"
            f"Detected folder structure entries: {len(report.folders)}.\n\n"
            "Choose how to apply imported sessions."
        )
        overwrite_button = mode_box.addButton("Overwrite Existing", QMessageBox.AcceptRole)
        merge_button = mode_box.addButton("Merge", QMessageBox.ActionRole)
        cancel_button = mode_box.addButton(QMessageBox.Cancel)
        mode_box.setDefaultButton(merge_button)
        mode_box.exec()

        clicked = mode_box.clickedButton()
        if clicked == cancel_button:
            return

        existing_sessions = self._session_service.all()
        existing_snapshot = {session.id: session for session in existing_sessions}

        if clicked == overwrite_button:
            self._session_service.replace_all(report.imported_sessions, report.folders)
            new_ids = {session.id for session in self._session_service.all()}
            removed_ids = [session_id for session_id in existing_snapshot if session_id not in new_ids]
            for session_id in removed_ids:
                session = existing_snapshot.get(session_id)
                if session:
                    self._credential_service.clear_password(session)
                self._close_session_tab(session_id)
            status = f"Imported {report.imported_count} {report.source_name} session(s) with overwrite."
        else:
            added = self._session_service.merge_sessions_no_overwrite(report.imported_sessions, report.folders)
            status = f"Merged {added} {report.source_name} session(s)."

        self._refresh_tree()
        self.statusBar().showMessage(status, 7000)

        summary = [status]
        if report.folders:
            summary.append(f"Imported folder structure entries: {len(report.folders)}")
        if report.warnings:
            warning_preview = "\n".join(report.warnings[:5])
            if len(report.warnings) > 5:
                warning_preview += f"\n... and {len(report.warnings) - 5} more warning(s)."
            summary.append(f"Warnings:\n{warning_preview}")
        QMessageBox.information(self, f"{report.source_name} Import Complete", "\n\n".join(summary))

    def _choose_securecrt_import_path(self) -> str | None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select SecureCRT XML Export",
            "",
            "XML Files (*.xml)",
        )
        return file_path or None

    def _add_session_tab(self, session: Session, widget: QWidget, kind: str, *, runtime_only: bool = False) -> None:
        widget.setProperty("session_id", session.id)
        widget.setProperty("session_kind", kind)
        widget.setProperty("session_runtime_only", bool(runtime_only))
        widget.setProperty("tab_title_custom", "")
        normalized_kind = kind.strip().upper()
        root_title = _session_display_name(session)
        widget.setProperty("tab_title_root", root_title)
        widget.setProperty("session_host_key", session.host.strip().lower())
        self._set_base_tab_title(widget, root_title)
        if isinstance(widget, TerminalTab):
            widget.set_open_sftp_supported(normalized_kind == "SSH" and not runtime_only)
            self._apply_terminal_color_override_from_session(widget, session)
            widget.apply_settings(self._terminal_settings_for_tab(widget))
            widget.setProperty("global_logging_auto_started", False)
            widget.start_logging_requested.connect(self._on_terminal_tab_start_logging_requested)
            widget.stop_logging_requested.connect(self._on_terminal_tab_stop_logging_requested)
            widget.disconnect_requested.connect(self._on_terminal_tab_disconnect_requested)
            widget.logging_error.connect(self._on_terminal_tab_logging_error)
            if normalized_kind != "LOCAL":
                self._maybe_start_global_logging_for_tab(widget, session)

        host = self._active_tab_host if self._active_tab_host in self._tab_hosts else self.tabs
        index = host.addTab(widget, self._display_title_for_widget(widget))
        self._sync_host_tab_close_buttons(host)
        host.setCurrentIndex(index)
        self._set_active_tab_host(host)
        self._apply_tab_styles()
        self._refresh_session_instance_titles()
        self._focus_new_session_tab(widget)

    def _prepare_existing_terminal_tab_for_session(
        self,
        tab: TerminalTab,
        session: Session,
        kind: str,
        *,
        runtime_only: bool = False,
    ) -> None:
        normalized_kind = kind.strip().upper()
        root_title = _session_display_name(session)
        tab.setProperty("session_id", session.id)
        tab.setProperty("session_kind", normalized_kind)
        tab.setProperty("session_runtime_only", bool(runtime_only))
        tab.setProperty("session_host_key", session.host.strip().lower())
        self._set_base_tab_title(tab, root_title)
        tab.set_open_sftp_supported(normalized_kind == "SSH" and not runtime_only)
        self._apply_terminal_color_override_from_session(tab, session)
        tab.apply_settings(self._terminal_settings_for_tab(tab))
        if normalized_kind != "LOCAL":
            self._maybe_start_global_logging_for_tab(tab, session)
        self._refresh_session_instance_titles()

    def _activate_session_tab(self, widget: QWidget) -> None:
        location = self._find_widget_location(widget)
        if location is None:
            return
        host, index = location
        host.setCurrentIndex(index)
        self._set_active_tab_host(host)
        self._focus_new_session_tab(widget)

    def _focus_new_session_tab(self, widget: QWidget) -> None:
        target: QWidget = widget.output if isinstance(widget, TerminalTab) else widget

        def _apply_focus() -> None:
            try:
                location = self._find_widget_location(widget)
            except RuntimeError:
                return
            if location is None:
                return
            host, index = location
            if host.currentIndex() != index:
                return
            try:
                if target.focusPolicy() == Qt.NoFocus:
                    return
                target.setFocus(Qt.ActiveWindowFocusReason)
            except RuntimeError:
                return

        # Defer focus until after Qt applies the new current tab selection.
        QTimer.singleShot(0, _apply_focus)

    def _close_session_tab(self, session_id: str) -> None:
        for host in list(self._tab_hosts):
            for index in range(host.count() - 1, -1, -1):
                widget = host.widget(index)
                if widget and widget.property("session_id") == session_id:
                    self._close_tab_in_host(host, index)

    def _close_tab_in_host(self, host: WorkspaceTabWidget, index: int, *, show_progress: bool = True) -> None:
        widget = host.widget(index)
        if widget is None:
            return
        if widget == self.details:
            return
        title = self._display_title_for_widget(widget).strip() or "Session"
        close_progress = None
        if show_progress and self._active_status_progress is None and isinstance(widget, (TerminalTab, RemoteViewerTab)):
            close_progress = self._begin_status_progress_operation(
                kind="close-tab",
                title=f"Closing {title}",
                total_steps=1,
                cancelable=False,
            )
            self._refresh_status_progress(close_progress, message=f"Closing {title}...")
            self._pump_ui()
        if isinstance(widget, TerminalTab):
            if not widget.shutdown():
                self._finish_status_progress(close_progress)
                self.statusBar().showMessage("Session is still shutting down. Try closing the tab again shortly.", 5000)
                return
        elif isinstance(widget, RemoteViewerTab):
            widget.shutdown()
        host.removeTab(index)
        widget.setParent(None)
        widget.deleteLater()
        self._normalize_tab_groups()
        self._refresh_session_instance_titles()
        self._remove_host_if_empty(host)
        if close_progress is not None:
            self._advance_status_progress(close_progress, message=f"Closed {title}.")
            self._pump_ui()
            self._finish_status_progress(
                close_progress,
                final_message=f"Closed {title}.",
                timeout_ms=4000,
                mark_complete=True,
            )

    def _restore_saved_window_geometry(self) -> None:
        placement = _window_placement_from_payload(
            geometry_b64=self._settings.main_window_geometry_b64,
            screen_name=self._settings.main_window_screen_name,
            screen_serial=self._settings.main_window_screen_serial,
            frame_rect=self._settings.main_window_frame_rect,
        )
        _restore_or_defer_window_placement(self, placement)

    def _current_main_window_placement(self) -> _WindowPlacement:
        if (
            self._frameless_fullscreen_active
            and self._fullscreen_restore_placement is not None
            and self._fullscreen_restore_placement.has_data()
        ):
            return self._fullscreen_restore_placement
        geometry = self._fullscreen_restore_geometry if self._frameless_fullscreen_active else None
        return _capture_window_placement(self, geometry_override=geometry)

    def _persist_window_geometry(self) -> None:
        placement = self._current_main_window_placement()
        if not placement.has_data():
            return
        encoded = _geometry_to_b64(placement.geometry)
        frame_rect = _frame_rect_to_list(placement.frame_rect)
        if (
            encoded == self._settings.main_window_geometry_b64
            and placement.screen_name == self._settings.main_window_screen_name
            and placement.screen_serial == self._settings.main_window_screen_serial
            and frame_rect == self._settings.main_window_frame_rect
        ):
            return
        self._settings.main_window_geometry_b64 = encoded
        self._settings.main_window_screen_name = placement.screen_name
        self._settings.main_window_screen_serial = placement.screen_serial
        self._settings.main_window_frame_rect = frame_rect
        self._settings_service.save(self._settings)

    def _persist_session_list_window_placement(self) -> None:
        if self._session_list_window is None:
            return
        self._capture_session_list_window_placement(self._session_list_window)
        self._settings_service.save(self._settings)

    def _restore_saved_main_splitter_state(self) -> None:
        encoded = self._settings.main_window_splitter_b64.strip()
        if not encoded:
            return
        self._session_list_docked_splitter_b64 = encoded
        try:
            state = QByteArray.fromBase64(encoded.encode("ascii"))
        except Exception:
            return
        if state.isEmpty():
            return
        self._main_splitter.restoreState(state)

    def _persist_main_splitter_state(self) -> None:
        encoded = self._remember_docked_main_splitter_state()
        if not encoded or encoded == self._settings.main_window_splitter_b64:
            return
        self._settings.main_window_splitter_b64 = encoded
        self._settings_service.save(self._settings)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        _apply_pending_window_placement(self)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if not self._confirm_application_close_with_connected_tabs():
            event.ignore()
            return
        self._is_shutting_down = True
        if self._connection_load_progress is not None:
            self._finish_connection_load_progress()
        self._flush_ui_settings_save()
        self._persist_window_geometry()
        self._persist_session_list_window_placement()
        self._persist_main_splitter_state()
        closable_widgets: list[QWidget] = []
        for host in self._tab_hosts:
            for index in range(host.count()):
                widget = host.widget(index)
                if isinstance(widget, (TerminalTab, RemoteViewerTab)):
                    closable_widgets.append(widget)
        shutdown_progress = None
        if len(closable_widgets) > 1:
            shutdown_progress = self._begin_status_progress_operation(
                kind="shutdown",
                title="Closing Sessions",
                total_steps=len(closable_widgets),
                cancelable=False,
            )
        terminal_tabs: list[TerminalTab] = []
        # Ensure all SSH worker threads are stopped before Qt destroys widgets.
        for host in self._tab_hosts:
            for index in range(host.count()):
                widget = host.widget(index)
                title = "Session"
                if widget is not None:
                    title = self._display_title_for_widget(widget).strip() or "Session"
                if isinstance(widget, TerminalTab):
                    if shutdown_progress is not None:
                        self._refresh_status_progress(shutdown_progress, message=f"Closing {title}...")
                        self._pump_ui()
                    terminal_closed = widget.shutdown(wait_seconds=0.0)
                    if not terminal_closed:
                        terminal_tabs.append(widget)
                    if shutdown_progress is not None:
                        status_text = f"Closed {title}." if terminal_closed else f"Stopping {title}..."
                        self._advance_status_progress(shutdown_progress, message=status_text)
                        self._pump_ui()
                elif isinstance(widget, RemoteViewerTab):
                    if shutdown_progress is not None:
                        self._refresh_status_progress(shutdown_progress, message=f"Closing {title}...")
                        self._pump_ui()
                    widget.shutdown()
                    if shutdown_progress is not None:
                        self._advance_status_progress(shutdown_progress, message=f"Closed {title}.")
                        self._pump_ui()
        tabs_shutdown_cleanly = True
        if terminal_tabs:
            deadline = time.monotonic() + TerminalTab._SHUTDOWN_WAIT_SECONDS
            pending = list(terminal_tabs)
            while pending and time.monotonic() < deadline:
                if shutdown_progress is not None:
                    waiting_label = self._base_tab_title(pending[0]).strip() or "session"
                    remaining_count = max(0, len(pending) - 1)
                    trailing = f" and {remaining_count} more session(s)" if remaining_count else ""
                    self._refresh_status_progress(
                        shutdown_progress,
                        message=f"Waiting for {waiting_label}{trailing} to stop...",
                    )
                    self._pump_ui()
                next_pending: list[TerminalTab] = []
                for widget in pending:
                    remaining = max(0.0, deadline - time.monotonic())
                    if remaining <= 0 or not widget.shutdown(wait_seconds=min(0.25, remaining)):
                        next_pending.append(widget)
                pending = next_pending
            tabs_shutdown_cleanly = not pending
        if not tabs_shutdown_cleanly:
            self._is_shutting_down = False
            event.ignore()
            self._finish_status_progress(shutdown_progress)
            self.statusBar().showMessage("Waiting for one or more sessions to stop. Try closing again in a moment.", 7000)
            return
        for worker in list(self._ssh_probe_workers_by_id.values()):
            try:
                worker.cancel()
            except Exception:
                continue
        probe_threads_stopped = True
        for thread in list(self._ssh_probe_threads):
            try:
                running = thread.isRunning()
            except RuntimeError:
                running = False
            if running:
                # Avoid QThread.terminate() here to prevent unsafe interpreter teardown.
                thread.quit()
                thread.wait(3000)
                try:
                    still_running = thread.isRunning()
                except RuntimeError:
                    still_running = False
                if still_running:
                    probe_threads_stopped = False
                    continue
            self._ssh_probe_threads.discard(thread)
        if not probe_threads_stopped:
            self._is_shutting_down = False
            event.ignore()
            self._finish_status_progress(shutdown_progress)
            self.statusBar().showMessage("Waiting for SSH background checks to stop. Please close again shortly.", 7000)
            return
        self._ssh_probe_threads_by_id.clear()
        self._ssh_probe_workers_by_id.clear()
        self._ssh_probe_contexts.clear()
        for timer in list(self._ssh_probe_watchdogs_by_id.values()):
            try:
                timer.stop()
                timer.deleteLater()
            except Exception:
                continue
        self._ssh_probe_watchdogs_by_id.clear()
        for window in list(self._detached_host_by_window.keys()):
            try:
                window.prepare_for_owner_close()
                window.close()
            except RuntimeError:
                continue
        if self._session_list_window is not None:
            try:
                self._session_list_window.prepare_for_owner_close()
                self._session_list_window.close()
            except RuntimeError:
                pass
            self._session_list_window = None
        self._detached_window_by_host.clear()
        self._detached_host_by_window.clear()
        self._finish_status_progress(shutdown_progress)
        super().closeEvent(event)
