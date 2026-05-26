from __future__ import annotations

import asyncio
from collections.abc import Callable
import errno
import os
from pathlib import Path
import ssl
import sys
import tempfile
import textwrap
import time
from types import SimpleNamespace
import unittest
import warnings
from unittest.mock import AsyncMock, MagicMock, patch

from PySide6.QtCore import QByteArray, QEvent, QMimeData, QPoint, QPointF, QRect, QSize, QTimer, Qt, QUrl
from PySide6.QtGui import QKeyEvent, QPainter, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStyle,
    QTabBar,
    QToolButton,
    QLineEdit,
    QWidget,
)

from snakesh.core.tool_registry import TOOL_REGISTRY
from snakesh.core.models import Protocol, Session, SSHAutomationStep
from snakesh.protocols.sftp_client import OverwriteConflict, SFTPEntry
from snakesh.services.settings_service import AppSettings
from snakesh.ui import main_window
from snakesh.ui.main_window import (
    TREE_FOLDER_PATH_ROLE,
    TREE_KIND_ROLE,
    TREE_SESSION_ID_ROLE,
    LocalShellWorker,
    MainWindow,
    ProfileManagerDialog,
    RemoteViewerTab,
    SFTPSessionTab,
    SSHShellWorker,
    TERMINAL_RENDER_MODE_ENV,
    TelnetShellWorker,
    TerminalTab,
    TerminalView,
    VT100Emulator,
    WindowsLocalShellWorker,
    _TerminalScrollOperation,
)
from snakesh.ui.terminal_scrollback_dialog import TerminalScrollbackDialog
from snakesh.ui.terminal_scrollback_store import (
    ScrollbackMatch,
    ScrollbackSearchResult,
    TerminalScrollbackStore,
)


class _MenuActionProbe:
    def __init__(self, text: str) -> None:
        self._text = text
        self.enabled = True

    def text(self) -> str:
        return self._text

    def setEnabled(self, enabled: bool) -> None:  # noqa: N802
        self.enabled = bool(enabled)


class _MenuProbe:
    instances: list["_MenuProbe"] = []
    next_action_text: str | None = None

    def __init__(self, _parent=None) -> None:
        self._actions: list[_MenuActionProbe] = []
        _MenuProbe.instances.append(self)

    @classmethod
    def reset(cls) -> None:
        cls.instances = []
        cls.next_action_text = None

    @classmethod
    def latest(cls) -> "_MenuProbe":
        return cls.instances[-1]

    def addAction(self, text: str) -> _MenuActionProbe:  # noqa: N802
        action = _MenuActionProbe(text)
        self._actions.append(action)
        return action

    def addSeparator(self) -> None:  # noqa: N802
        self._actions.append(_MenuActionProbe(""))

    def actions(self) -> list[_MenuActionProbe]:
        return list(self._actions)

    def exec(self, _pos=None):
        if self.next_action_text is None:
            return None
        for action in self._actions:
            if action.text() == self.next_action_text:
                return action
        return None

    def deleteLater(self) -> None:
        return None


class _BulkDisconnectTabsDialogProbe:
    instances: list["_BulkDisconnectTabsDialogProbe"] = []
    next_result = 1
    next_selected_tabs: list[TerminalTab] = []
    next_close_after_disconnect = False

    def __init__(self, entries, preselected: set[int], _parent=None) -> None:
        self.entries = list(entries)
        self.preselected = set(preselected)
        _BulkDisconnectTabsDialogProbe.instances.append(self)

    @classmethod
    def reset(cls) -> None:
        cls.instances = []
        cls.next_result = 1
        cls.next_selected_tabs = []
        cls.next_close_after_disconnect = False

    @classmethod
    def latest(cls) -> "_BulkDisconnectTabsDialogProbe":
        return cls.instances[-1]

    def exec(self) -> int:
        return int(self.next_result)

    def selected_tabs(self) -> list[TerminalTab]:
        return list(self.next_selected_tabs)

    def close_after_disconnect(self) -> bool:
        return bool(self.next_close_after_disconnect)

    def deleteLater(self) -> None:
        return None


class _WheelEventProbe:
    def __init__(self, *, angle_y: int = 0, pixel_y: int = 0) -> None:
        self._angle_delta = QPoint(0, angle_y)
        self._pixel_delta = QPoint(0, pixel_y)
        self.accepted = False
        self.ignored = False

    def angleDelta(self) -> QPoint:  # noqa: N802
        return self._angle_delta

    def pixelDelta(self) -> QPoint:  # noqa: N802
        return self._pixel_delta

    def accept(self) -> None:
        self.accepted = True

    def ignore(self) -> None:
        self.ignored = True


class _MouseEventProbe:
    def __init__(self, pos: QPoint, *, button=Qt.LeftButton) -> None:
        self._position = QPointF(pos)
        self._button = button
        self.accepted = False
        self.ignored = False

    def button(self):
        return self._button

    def position(self) -> QPointF:
        return self._position

    def accept(self) -> None:
        self.accepted = True

    def ignore(self) -> None:
        self.ignored = True


class _MimeDropEventProbe:
    def __init__(self, mime_data: QMimeData) -> None:
        self._mime_data = mime_data
        self.accepted = False

    def mimeData(self) -> QMimeData:  # noqa: N802
        return self._mime_data

    def acceptProposedAction(self) -> None:  # noqa: N802
        self.accepted = True


class _FakeScreen:
    def __init__(self, *, name: str, serial: str, available: QRect) -> None:
        self._name = name
        self._serial = serial
        self._available = QRect(available)

    def name(self) -> str:
        return self._name

    def serialNumber(self) -> str:  # noqa: N802
        return self._serial

    def availableGeometry(self) -> QRect:  # noqa: N802
        return QRect(self._available)


class _FakeWindowHandle:
    def __init__(self) -> None:
        self.screen_set: object | None = None

    def setScreen(self, screen: object) -> None:  # noqa: N802
        self.screen_set = screen

    def screen(self) -> object | None:
        return self.screen_set


class _WindowPlacementTargetProbe:
    def __init__(
        self,
        *,
        handle: _FakeWindowHandle | None,
        frame: QRect | None = None,
        maximized: bool = False,
        fullscreen: bool = False,
    ) -> None:
        self._handle = handle
        self._frame = QRect(frame or QRect(0, 0, 640, 480))
        self._maximized = maximized
        self._fullscreen = fullscreen
        self._pending_window_placement = None
        self.restored_geometry: QByteArray | None = None
        self.set_geometry_calls: list[QRect] = []
        self.move_calls: list[tuple[int, int]] = []
        self.resize_calls: list[tuple[int, int]] = []

    def windowHandle(self):  # noqa: N802
        return self._handle

    def restoreGeometry(self, geometry: QByteArray) -> None:  # noqa: N802
        self.restored_geometry = QByteArray(geometry)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        applied = QRect(rect)
        self.set_geometry_calls.append(applied)
        self._frame = applied

    def frameGeometry(self) -> QRect:  # noqa: N802
        return QRect(self._frame)

    def width(self) -> int:
        return self._frame.width()

    def height(self) -> int:
        return self._frame.height()

    def resize(self, width: int, height: int) -> None:  # noqa: N802
        self.resize_calls.append((width, height))
        self._frame.setSize(QRect(0, 0, width, height).size())

    def move(self, x: int, y: int) -> None:  # noqa: N802
        self.move_calls.append((x, y))
        self._frame.moveTo(x, y)

    def isMaximized(self) -> bool:  # noqa: N802
        return self._maximized

    def isFullScreen(self) -> bool:  # noqa: N802
        return self._fullscreen

    def windowState(self):  # noqa: N802
        return Qt.WindowState.WindowNoState


class _StubSessionService:
    def __init__(self) -> None:
        self._sessions: list[Session] = []
        self._folders = ["Default"]

    def all(self) -> list[Session]:
        return list(self._sessions)

    def all_folders(self) -> list[str]:
        return list(self._folders)

    def add_or_update(self, session: Session) -> None:
        for index, existing in enumerate(self._sessions):
            if existing.id == session.id:
                self._sessions[index] = session
                return
        self._sessions.append(session)

    def by_id(self, session_id: str) -> Session | None:
        for session in self._sessions:
            if session.id == session_id:
                return session
        return None

    @staticmethod
    def normalize_folder_path(folder_path: str) -> str:
        cleaned = folder_path.replace("\\", "/").strip("/")
        return cleaned or "Default"


class _StubSettingsService:
    def __init__(self) -> None:
        self._settings = AppSettings.defaults()
        self.save_calls = 0

    def load(self) -> AppSettings:
        return AppSettings.from_dict(self._settings.to_dict())

    def save(self, settings: AppSettings) -> None:
        self.save_calls += 1
        self._settings = AppSettings.from_dict(settings.to_dict())

    def sanitize_imported_settings(
        self,
        settings: AppSettings,
        *,
        source_platform: str | None = None,
    ) -> AppSettings:
        return main_window.SettingsService.sanitize_imported_settings(settings, source_platform=source_platform)


class _StubToolProcess:
    _next_pid = 41000

    def __init__(self, tool_key: str, arguments: list[str] | None = None) -> None:
        self.tool_key = tool_key
        self.arguments = list(arguments or [])
        self.pid = _StubToolProcess._next_pid
        _StubToolProcess._next_pid += 1
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode

    def finish(self, returncode: int = 0) -> None:
        self.returncode = int(returncode)


def _build_session(name: str, session_id: str) -> Session:
    return Session(
        id=session_id,
        name=name,
        host="127.0.0.1",
        protocol=Protocol.SSH,
        port=22,
        username="tester",
    )


class TabWorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        for widget in list(QApplication.topLevelWidgets()):
            widget.close()
            widget.deleteLater()
        QApplication.processEvents()
        self.session_service = _StubSessionService()
        self.settings_service = _StubSettingsService()
        self.tool_launches: list[_StubToolProcess] = []
        self._launch_standalone_tool_patch = patch(
            "snakesh.ui.main_window.launch_standalone_tool",
            side_effect=self._launch_standalone_tool,
        )
        self.mock_launch_standalone_tool = self._launch_standalone_tool_patch.start()
        self._has_active_tool_instance_patch = patch(
            "snakesh.ui.main_window.has_active_tool_instance",
            return_value=False,
        )
        self.mock_has_active_tool_instance = self._has_active_tool_instance_patch.start()
        self.window = MainWindow(self.session_service, self.settings_service)
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()
        handle = self.window.windowHandle()
        if handle is not None:
            handle.requestActivate()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            QApplication.setActiveWindow(self.window)
        QApplication.processEvents()

    def tearDown(self) -> None:
        for widget in list(QApplication.topLevelWidgets()):
            if widget is self.window:
                continue
            widget.close()
            widget.deleteLater()
        self.window.close()
        self.window.deleteLater()
        QApplication.processEvents()
        for widget in list(QApplication.topLevelWidgets()):
            widget.close()
            widget.deleteLater()
        self._launch_standalone_tool_patch.stop()
        self._has_active_tool_instance_patch.stop()

    def test_initial_workspace_tab_style_uses_pane_without_fixed_tab_bar_offset(self) -> None:
        style = self.window.tabs.styleSheet()
        self.assertIn("QTabWidget::pane", style)
        self.assertIn("QTabWidget::tab-bar { left: 0px; }", style)
        self.assertNotIn("left: 12px", style)

    def _launch_standalone_tool(self, tool_key: str, *, arguments=None, cwd=None, env=None):
        _ = cwd, env
        process = _StubToolProcess(tool_key, list(arguments or []))
        self.tool_launches.append(process)
        return SimpleNamespace(process=process, activated_existing=False)

    def _tool_launch_call(self, tool_key: str, *, arguments=None):
        return unittest.mock.call(tool_key, arguments=arguments, env=unittest.mock.ANY)

    def _assert_last_tool_launch_has_placement_env(self) -> None:
        env = self.mock_launch_standalone_tool.call_args.kwargs.get("env")
        self.assertIsInstance(env, dict)
        assert isinstance(env, dict)
        self.assertIn(main_window._TOOL_LAUNCH_PLACEMENT_ENV, env)

    def _show_terminal_tab(self, tab: TerminalTab) -> None:
        tab.resize(900, 500)
        tab.show()
        QApplication.processEvents()

    def _terminal_cell_point(self, view: TerminalView, *, col: int, row: int) -> QPoint:
        content = view._content_rect()
        return QPoint(
            content.left() + (col * view._cell_width_px) + max(1, view._cell_width_px // 2),
            content.top() + (row * view._cell_height_px) + max(1, view._cell_height_px // 2),
        )

    def _terminal_cell_color(self, view: TerminalView, *, col: int, row: int) -> str:
        view.request_full_repaint()
        QApplication.processEvents()
        assert view._framebuffer is not None
        return view._framebuffer.pixelColor(self._terminal_cell_point(view, col=col, row=row)).name()

    def _double_click_terminal_cell(self, view: TerminalView, *, col: int, row: int) -> _MouseEventProbe:
        event = _MouseEventProbe(self._terminal_cell_point(view, col=col, row=row))
        view.mouseDoubleClickEvent(event)
        return event

    def _build_sftp_tab_for_transfers(
        self,
        *,
        sftp,
        should_confirm_overwrite: Callable[[], bool] | None = None,
        status_messages: list[str] | None = None,
    ) -> SFTPSessionTab:
        session = _build_session("SFTP Host", "sess-sftp-transfer")
        session.protocol = Protocol.SFTP
        messages = status_messages if status_messages is not None else []
        sftp.list_directory = AsyncMock(return_value=("/remote", []))
        sftp.scan_directory = AsyncMock(return_value=("/remote", []))
        return SFTPSessionTab(
            session=session,
            sftp=sftp,
            initial_remote_dir="/remote",
            initial_remote_entries=[],
            initial_local_dir="~",
            password=None,
            execute_remote=lambda _session, current_password, operation: (
                operation(current_password, False),
                current_password,
            ),
            status_callback=lambda message, _timeout_ms: messages.append(message),
            should_confirm_delete=lambda: True,
            should_confirm_overwrite=should_confirm_overwrite or (lambda: True),
            parent=self.window,
        )
        QApplication.processEvents()

    def _menu_labels(self) -> list[str]:
        return [action.text() for action in _MenuProbe.latest().actions() if action.text()]

    def _assert_custom_right_close_button(
        self,
        host,
        index: int,
        *,
        expected_button: QToolButton | None = None,
    ) -> QToolButton:
        tab_bar = host.tabBar()
        left_button = tab_bar.tabButton(index, QTabBar.LeftSide)
        right_control = tab_bar.tabButton(index, QTabBar.RightSide)
        self.assertIsNone(left_button)
        self.assertIsNotNone(right_control)
        assert right_control is not None
        right_button = (
            right_control
            if isinstance(right_control, QToolButton)
            else right_control.findChild(QToolButton, "workspaceTabCloseButton")
        )
        self.assertIsNotNone(right_button)
        assert right_button is not None
        self.assertTrue(bool(right_button.property("sp_custom_close")))
        self.assertEqual(right_button.objectName(), "workspaceTabCloseButton")
        self.assertTrue(str(right_button.property("sp_close_visual_signature") or "").strip())
        if expected_button is not None:
            self.assertIs(right_button, expected_button)
        return right_button

    def _show_scrollback_dialog(self, text: str) -> TerminalScrollbackDialog:
        dialog = TerminalScrollbackDialog(text)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        dialog.setFocus(Qt.ActiveWindowFocusReason)
        handle = dialog.windowHandle()
        if handle is not None:
            handle.requestActivate()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            QApplication.setActiveWindow(dialog)
        QApplication.processEvents()
        return dialog

    def _search_scrollback_dialog(
        self,
        dialog: TerminalScrollbackDialog,
        pattern: str,
        *,
        case_sensitive: bool = False,
        use_regex: bool = False,
    ) -> None:
        if dialog._find_bar.isHidden():
            dialog._open_search_btn.click()
            QApplication.processEvents()
        dialog._find_bar._case_cb.setChecked(case_sensitive)
        dialog._find_bar._regex_cb.setChecked(use_regex)
        dialog._find_bar._debounce.stop()
        dialog._find_bar._input.setText(pattern)
        dialog._find_bar._debounce.stop()
        dialog._find_bar._emit_search()
        QApplication.processEvents()

    def _wait_for(self, predicate: Callable[[], bool], *, timeout_ms: int = 1000) -> None:
        deadline = time.monotonic() + (timeout_ms / 1000.0)
        while time.monotonic() < deadline:
            QApplication.processEvents()
            if predicate():
                return
            QTest.qWait(10)
        self.fail("Timed out waiting for condition.")

    def test_grouped_command_dispatch_targets_all_grouped_terminal_tabs(self) -> None:
        s1 = _build_session("Host A", "sess-a")
        s2 = _build_session("Host B", "sess-b")
        t1 = TerminalTab(settings=self.window._settings)
        t2 = TerminalTab(settings=self.window._settings)
        self.window._add_session_tab(s1, t1, "SSH")
        self.window._add_session_tab(s2, t2, "SSH")

        calls: list[tuple[str, str]] = []
        t1.execute_command = lambda command: calls.append(("t1", command)) or True  # type: ignore[method-assign]
        t2.execute_command = lambda command: calls.append(("t2", command)) or True  # type: ignore[method-assign]

        self.window._set_group_name("group-1", "Ops")
        self.window._set_tab_group_id(t1, "group-1")
        self.window._set_tab_group_id(t2, "group-1")
        self.window._refresh_all_tab_titles()

        self.window._dispatch_terminal_command(t1, "uname -a")

        self.assertEqual(sorted(calls), [("t1", "uname -a"), ("t2", "uname -a")])
        loc1 = self.window._find_widget_location(t1)
        loc2 = self.window._find_widget_location(t2)
        self.assertIsNotNone(loc1)
        self.assertIsNotNone(loc2)
        assert loc1 is not None
        assert loc2 is not None
        self.assertIn("*", loc1[0].tabText(loc1[1]))
        self.assertIn("*", loc2[0].tabText(loc2[1]))

    def test_group_name_is_removed_when_group_shrinks_to_one_member(self) -> None:
        s1 = _build_session("Host A", "sess-a")
        s2 = _build_session("Host B", "sess-b")
        w1 = QWidget()
        w2 = QWidget()
        self.window._add_session_tab(s1, w1, "SSH")
        self.window._add_session_tab(s2, w2, "SFTP")
        self.window._set_group_name("group-2", "Primary")
        self.window._set_tab_group_id(w1, "group-2")
        self.window._set_tab_group_id(w2, "group-2")
        self.window._refresh_all_tab_titles()

        loc = self.window._find_widget_location(w2)
        self.assertIsNotNone(loc)
        assert loc is not None
        self.window._close_tab_in_host(loc[0], loc[1])

        self.assertIsNone(self.window._tab_group_id(w1))
        self.assertNotIn("group-2", self.window._tab_group_names)

    def test_split_and_move_tabs_between_hosts(self) -> None:
        s1 = _build_session("Host A", "sess-a")
        s2 = _build_session("Host B", "sess-b")
        self.window._add_session_tab(s1, QWidget(), "SSH")
        self.window._add_session_tab(s2, QWidget(), "SFTP")
        QApplication.processEvents()

        self.window._split_tab(self.window.tabs, 1, Qt.Horizontal)
        QApplication.processEvents()

        self.assertEqual(len(self.window._tab_hosts), 2)
        primary_count = self.window.tabs.count()
        other_host = [host for host in self.window._tab_hosts if host is not self.window.tabs][0]
        self.assertEqual(other_host.count(), 1)

        self.window._move_tab_between_hosts(other_host, 0, self.window.tabs, self.window.tabs.count())
        QApplication.processEvents()

        self.assertEqual(len(self.window._tab_hosts), 1)
        self.assertEqual(self.window.tabs.count(), primary_count + 1)

    def test_active_tab_coloring_tracks_active_split_host(self) -> None:
        s1 = _build_session("Host A", "sess-a")
        s2 = _build_session("Host B", "sess-b")
        self.window._add_session_tab(s1, QWidget(), "SSH")
        self.window._add_session_tab(s2, QWidget(), "SFTP")
        self.window._split_tab(self.window.tabs, 1, Qt.Horizontal)
        QApplication.processEvents()

        other_host = [host for host in self.window._tab_hosts if host is not self.window.tabs][0]
        self.window._set_active_tab_host(self.window.tabs)
        QApplication.processEvents()
        self.assertEqual(self.window.tabs.tabBar().property("workspace_active"), "true")
        self.assertEqual(other_host.tabBar().property("workspace_active"), "false")

        self.window._on_focus_changed(None, other_host.currentWidget())
        QApplication.processEvents()
        self.assertEqual(self.window.tabs.tabBar().property("workspace_active"), "false")
        self.assertEqual(other_host.tabBar().property("workspace_active"), "true")

    def test_set_active_tab_host_same_host_does_not_reapply_styles(self) -> None:
        s1 = _build_session("Host A", "sess-a")
        self.window._add_session_tab(s1, QWidget(), "SSH")
        QApplication.processEvents()

        self.window._active_tab_host = self.window.tabs
        with patch.object(self.window, "_apply_tab_styles") as mock_apply:
            self.window._set_active_tab_host(self.window.tabs)

        mock_apply.assert_not_called()

    def test_apply_tab_styles_ignores_reentrant_focus_changes(self) -> None:
        s1 = _build_session("Host A", "sess-a")
        s2 = _build_session("Host B", "sess-b")
        self.window._add_session_tab(s1, QWidget(), "SSH")
        self.window._add_session_tab(s2, QWidget(), "SFTP")
        self.window._split_tab(self.window.tabs, 1, Qt.Horizontal)
        QApplication.processEvents()

        other_host = [host for host in self.window._tab_hosts if host is not self.window.tabs][0]
        self.window._set_active_tab_host(self.window.tabs)

        original_set_stylesheet = other_host.setStyleSheet
        triggered = False

        def _reentrant_set_stylesheet(style: str) -> None:
            nonlocal triggered
            if not triggered:
                triggered = True
                self.window._on_focus_changed(None, other_host.currentWidget())
            original_set_stylesheet(style)

        with patch.object(other_host, "setStyleSheet", side_effect=_reentrant_set_stylesheet):
            self.window._apply_tab_styles()

        self.assertTrue(triggered)
        self.assertIs(self.window._active_tab_host, self.window.tabs)

    def test_confirm_delete_suspends_focus_tracking_during_dialog(self) -> None:
        s1 = _build_session("Host A", "sess-a")
        s2 = _build_session("Host B", "sess-b")
        self.window._add_session_tab(s1, QWidget(), "SSH")
        self.window._add_session_tab(s2, QWidget(), "SFTP")
        self.window._split_tab(self.window.tabs, 1, Qt.Horizontal)
        QApplication.processEvents()

        other_host = [host for host in self.window._tab_hosts if host is not self.window.tabs][0]
        self.window._set_active_tab_host(self.window.tabs)
        tab = self._build_sftp_tab_for_transfers(sftp=MagicMock())

        try:
            def _question(*_args, **_kwargs) -> int:
                self.window._on_focus_changed(None, other_host.currentWidget())
                return QMessageBox.No

            with (
                patch.object(self.window, "_apply_tab_styles") as mock_apply,
                patch("snakesh.ui.main_window.QMessageBox.question", side_effect=_question),
            ):
                confirmed = tab._confirm_delete(["/remote/file.txt"], "remote")

            self.assertFalse(confirmed)
            mock_apply.assert_not_called()
            self.assertIs(self.window._active_tab_host, self.window.tabs)
        finally:
            tab.deleteLater()
            QApplication.processEvents()

    def test_confirm_overwrite_suspends_focus_tracking_during_dialog(self) -> None:
        s1 = _build_session("Host A", "sess-a")
        s2 = _build_session("Host B", "sess-b")
        self.window._add_session_tab(s1, QWidget(), "SSH")
        self.window._add_session_tab(s2, QWidget(), "SFTP")
        self.window._split_tab(self.window.tabs, 1, Qt.Horizontal)
        QApplication.processEvents()

        other_host = [host for host in self.window._tab_hosts if host is not self.window.tabs][0]
        self.window._set_active_tab_host(self.window.tabs)
        tab = self._build_sftp_tab_for_transfers(sftp=MagicMock())
        try:
            def _exec() -> int:
                self.window._on_focus_changed(None, other_host.currentWidget())
                return QMessageBox.No

            with (
                patch.object(self.window, "_apply_tab_styles") as mock_apply,
                patch("snakesh.ui.main_window.QMessageBox.exec", side_effect=_exec),
            ):
                approved, allow_all = tab._prompt_overwrite_conflict(
                    OverwriteConflict("/tmp/a.txt", "/remote/a.txt"),
                    destination_label="remote",
                    remaining_count=1,
                )

            self.assertFalse(approved)
            self.assertFalse(allow_all)
            mock_apply.assert_not_called()
            self.assertIs(self.window._active_tab_host, self.window.tabs)
        finally:
            tab.deleteLater()
            QApplication.processEvents()

    def test_open_ssh_tab_allows_multiple_tabs_for_same_session(self) -> None:
        session = _build_session("Host A", "sess-a")
        placeholder_tabs: list[TerminalTab] = []

        def _fake_probe(*, session, password, trust_unknown, x11_forwarding, tab):  # noqa: ANN001
            placeholder_tabs.append(tab)

        with patch.object(self.window, "_run_ssh_probe", side_effect=_fake_probe):
            self.window._open_ssh_tab(session)
            self.window._open_ssh_tab(session)

        ssh_tabs = [
            widget
            for _host, _index, widget in self.window._session_tab_locations()
            if widget.property("session_id") == session.id and widget.property("session_kind") == "SSH"
        ]
        self.assertEqual(len(ssh_tabs), 2)
        self.assertEqual(len(placeholder_tabs), 2)
        for tab in placeholder_tabs:
            self.assertIn("Connecting to", tab.scrollback_text())

    def test_add_session_tab_focuses_newest_terminal_tab(self) -> None:
        first = _build_session("Host A", "sess-focus-a")
        second = _build_session("Host B", "sess-focus-b")
        first_tab = TerminalTab(settings=self.window._settings)
        second_tab = TerminalTab(settings=self.window._settings)

        self.window._add_session_tab(first, first_tab, "SSH")
        self.window._add_session_tab(second, second_tab, "SSH")
        QApplication.processEvents()

        self.assertIs(self.window.tabs.currentWidget(), second_tab)
        self.assertIs(QApplication.focusWidget(), second_tab.output)

    def test_session_tabs_show_close_button_on_right(self) -> None:
        session = _build_session("Host A", "sess-close")
        widget = QWidget()
        self.window._add_session_tab(session, widget, "SSH")

        location = self.window._find_widget_location(widget)
        self.assertIsNotNone(location)
        assert location is not None
        host, index = location
        self._assert_custom_right_close_button(host, index)

    def test_session_details_tab_has_no_close_button(self) -> None:
        details_index = self.window.tabs.indexOf(self.window.details)
        self.assertGreaterEqual(details_index, 0)
        tab_bar = self.window.tabs.tabBar()
        self.assertIsNone(tab_bar.tabButton(details_index, QTabBar.LeftSide))
        self.assertIsNone(tab_bar.tabButton(details_index, QTabBar.RightSide))

    def test_global_command_bar_targets_active_group(self) -> None:
        s1 = _build_session("Host A", "sess-a")
        s2 = _build_session("Host B", "sess-b")
        t1 = TerminalTab(settings=self.window._settings)
        t2 = TerminalTab(settings=self.window._settings)
        self.window._add_session_tab(s1, t1, "SSH")
        self.window._add_session_tab(s2, t2, "SSH")
        self.window._set_group_name("group-3", "Ops")
        self.window._set_tab_group_id(t1, "group-3")
        self.window._set_tab_group_id(t2, "group-3")
        self.window._refresh_all_tab_titles()

        calls: list[tuple[str, str]] = []
        t1.execute_command = lambda command: calls.append(("t1", command)) or True  # type: ignore[method-assign]
        t2.execute_command = lambda command: calls.append(("t2", command)) or True  # type: ignore[method-assign]

        self.window.command_bar_input.setText("whoami")
        self.window._run_workspace_command()

        self.assertEqual(sorted(calls), [("t1", "whoami"), ("t2", "whoami")])

    def test_run_fast_command_targets_active_terminal(self) -> None:
        session = _build_session("Host Fast", "sess-fast")
        tab = TerminalTab(settings=self.window._settings)
        self.window._add_session_tab(session, tab, "SSH")

        calls: list[str] = []
        tab.execute_command = lambda command: calls.append(command) or True  # type: ignore[method-assign]

        self.window._run_fast_command("Hostname", "hostname")

        self.assertEqual(calls, ["hostname"])

    def test_run_fast_command_copies_for_active_remote_viewer(self) -> None:
        session = _build_session("Host RDP", "sess-fast-rdp")
        session.protocol = Protocol.RDP
        session.port = 3389

        def detached_builder() -> tuple[list[str], str, dict[str, str] | None]:
            return ["xfreerdp", session.host], "FreeRDP", None

        tab = RemoteViewerTab(
            session=session,
            protocol_name="RDP",
            detached_command_builder=detached_builder,
        )
        self.window._add_session_tab(session, tab, "RDP")

        clipboard = QApplication.clipboard()
        clipboard.setText("")
        self.window._run_fast_command("Hostname", "hostname\n")

        self.assertEqual(clipboard.text(), "hostname")
        self.assertIn("clipboard", self.window.statusBar().currentMessage().lower())
        tab.deleteLater()

    def test_run_fast_command_requires_active_supported_tab(self) -> None:
        self.window._run_fast_command("Hostname", "hostname")
        self.assertIn("Select a terminal or remote viewer tab", self.window.statusBar().currentMessage())

    def test_add_fast_command_persists_in_settings(self) -> None:
        with patch(
            "snakesh.ui.main_window.QInputDialog.getText",
            return_value=("Restart API", True),
        ), patch(
            "snakesh.ui.main_window.QInputDialog.getMultiLineText",
            return_value=("sudo systemctl restart api", True),
        ):
            created = self.window._add_fast_command()

        self.assertTrue(created)
        self.assertEqual(len(self.settings_service._settings.fast_commands), 1)
        self.assertEqual(self.settings_service._settings.fast_commands[0]["name"], "Restart API")

    def test_fast_commands_menu_populates_saved_commands_and_manager_actions(self) -> None:
        self.window._settings.fast_commands = [
            {"id": "cmd-1", "name": "List Home", "command": "ls -la ~"}
        ]

        self.window._refresh_fast_commands_menu()

        labels = [action.text() for action in self.window._fast_commands_menu.actions() if action.text()]
        self.assertIn("List Home", labels)
        self.assertIn("Add Fast Command...", labels)
        self.assertIn("Manage Fast Commands...", labels)

    def test_grouping_candidates_include_local_shell_tabs(self) -> None:
        ssh_session = _build_session("Host A", "sess-ssh")
        local_session = _build_session("Local Shell", "sess-local")
        ssh_tab = TerminalTab(settings=self.window._settings)
        local_tab = TerminalTab(settings=self.window._settings)
        self.window._add_session_tab(ssh_session, ssh_tab, "SSH")
        self.window._add_session_tab(local_session, local_tab, "LOCAL")

        entries = self.window._session_tabs_for_grouping()
        grouped_widgets = {widget for widget, _label in entries}

        self.assertIn(ssh_tab, grouped_widgets)
        self.assertIn(local_tab, grouped_widgets)

    def test_grouping_candidates_include_telnet_and_serial_tabs(self) -> None:
        telnet_session = _build_session("Telnet Host", "sess-telnet")
        telnet_session.protocol = Protocol.TELNET
        telnet_session.port = 23

        serial_session = _build_session("Serial Lab", "sess-serial")
        serial_session.protocol = Protocol.SERIAL
        serial_session.host = "/dev/ttyUSB0"

        with (
            patch.object(TerminalTab, "start_telnet", autospec=True) as mock_start_telnet,
            patch.object(TerminalTab, "start_serial", autospec=True) as mock_start_serial,
        ):
            self.window._open_telnet_tab(telnet_session)
            self.window._open_serial_tab(serial_session)

        entries = self.window._session_tabs_for_grouping()
        grouped_kinds = {widget.property("session_kind") for widget, _label in entries}

        self.assertIn("TELNET", grouped_kinds)
        self.assertIn("SERIAL", grouped_kinds)
        mock_start_telnet.assert_called_once()
        mock_start_serial.assert_called_once()

    def test_same_host_tabs_are_numbered(self) -> None:
        s1 = _build_session("AngolaSSH", "sess-a")
        s1.host = "angola.example.com"
        s2 = _build_session("AngolaSSH", "sess-b")
        s2.host = "angola.example.com"

        w1 = QWidget()
        self.window._add_session_tab(s1, w1, "SSH")
        loc1 = self.window._find_widget_location(w1)
        self.assertIsNotNone(loc1)
        assert loc1 is not None
        self.assertEqual(loc1[0].tabText(loc1[1]), "AngolaSSH")

        w2 = QWidget()
        self.window._add_session_tab(s2, w2, "SSH")
        titles = []
        for host, index, widget in self.window._session_tab_locations():
            if widget in (w1, w2):
                titles.append(host.tabText(index))
        self.assertEqual(sorted(titles), ["AngolaSSH(1)", "AngolaSSH(2)"])

        loc2 = self.window._find_widget_location(w2)
        self.assertIsNotNone(loc2)
        assert loc2 is not None
        self.window._close_tab_in_host(loc2[0], loc2[1])
        loc1_after = self.window._find_widget_location(w1)
        self.assertIsNotNone(loc1_after)
        assert loc1_after is not None
        self.assertEqual(loc1_after[0].tabText(loc1_after[1]), "AngolaSSH")

    def test_terminal_tab_title_marks_closed_sessions(self) -> None:
        session = _build_session("OpenWebRX", "sess-disconnected-title")
        tab = TerminalTab(settings=self.window._settings)
        self.window._add_session_tab(session, tab, "SSH")

        location = self.window._find_widget_location(tab)
        self.assertIsNotNone(location)
        assert location is not None
        host, index = location
        self.assertEqual(host.tabText(index), "OpenWebRX")

        tab._mark_connection_closed(True)
        QApplication.processEvents()
        self.assertEqual(host.tabText(index), "OpenWebRX (closed)")

        tab._mark_connection_closed(False)
        QApplication.processEvents()
        self.assertEqual(host.tabText(index), "OpenWebRX")

    def test_friendly_ssh_error_message_for_windows_timeout(self) -> None:
        session = _build_session("Host A", "sess-a")
        message = self.window._friendly_ssh_connection_error(
            session,
            "[WinError 121] The semaphore timeout period has expired",
        )
        self.assertIn(f"{session.host}:{session.port}", message)
        self.assertIn("timed out", message.lower())
        self.assertIn("vpn", message.lower())

    def test_friendly_ssh_error_message_for_legacy_algorithm_mismatch(self) -> None:
        session = _build_session("Host A", "sess-legacy")
        message = self.window._friendly_ssh_connection_error(
            session,
            "No matching key exchange method found",
        )
        self.assertIn("algorithm", message.lower())
        self.assertIn("legacy", message.lower())

    def test_scrollback_strips_ansi_and_osc_sequences(self) -> None:
        tab = TerminalTab(settings=self.window._settings)
        tab.append("Connected.\n")
        tab.append("\x1b]0;root@AS1Lab:~\x07[root@AS1Lab ~]# \n")

        text = tab.scrollback_text()
        self.assertIn("Connected.", text)
        self.assertIn("[root@AS1Lab ~]#", text)
        self.assertNotIn("\x1b]0;", text)
        self.assertNotIn("\x07", text)

    def test_scrollback_keeps_single_line_input_echo_together(self) -> None:
        tab = TerminalTab(settings=self.window._settings)
        tab.append("[root@AS1Lab ~]# ")
        tab.append("t")
        tab.append("o")
        tab.append("p")

        text = tab.scrollback_text()
        self.assertIn("[root@AS1Lab ~]# top", text)
        self.assertNotIn("\nt\no\np", text)

    def test_scrollback_preserves_crlf_lines(self) -> None:
        tab = TerminalTab(settings=self.window._settings)
        tab.append("line1\r\nline2\r\n")
        self.assertEqual(tab.scrollback_text(), "line1\nline2")

    def test_scrollback_handles_standalone_carriage_return_overwrite(self) -> None:
        tab = TerminalTab(settings=self.window._settings)
        tab.append("abcdef\rxyz\n")
        self.assertEqual(tab.scrollback_text(), "xyzdef")

    def test_scrollback_matches_visible_prompt_after_bash_history_backlog_redraw(self) -> None:
        tab = TerminalTab(settings=self.window._settings)
        tab.append("[user@host ~]$ echo two\b\b\bone\b\b\btwo")

        self.assertEqual(tab.scrollback_text(), "[user@host ~]$ echo two")

    def test_scrollback_store_supports_tail_pages_and_background_search(self) -> None:
        snapshot = ["line 1", "needle first", "line 2", "needle second"]
        store = TerminalScrollbackStore(max_lines=100, line_source=lambda: snapshot)
        try:
            page = store.snapshot_tail(window_lines=2)
            self.assertEqual(page.lines, ["needle first", "line 2", "needle second"][-2:])

            result = store.search("needle", case_sensitive=False, use_regex=False)
            self.assertEqual(len(result.matches), 2)
            self.assertEqual(result.matches[0].line_index, 1)
            self.assertEqual(result.matches[1].line_index, 3)
        finally:
            store.close()

    def test_scrollback_dialog_provider_opens_tail_page_without_full_snapshot(self) -> None:
        snapshot = [f"line {index}" for index in range(5000)]
        store = TerminalScrollbackStore(max_lines=10000, line_source=lambda: snapshot)
        try:
            with patch.object(store, "snapshot_window", side_effect=AssertionError("unexpected full snapshot")):
                dialog = TerminalScrollbackDialog(provider=store)
                try:
                    text = dialog.viewer().toPlainText()
                    self.assertEqual(dialog._current_page.start_line_index, 3000)
                    self.assertIn("line 4999", text)
                    self.assertNotIn("line 0", text)
                finally:
                    dialog.close()
                    dialog.deleteLater()
                    QApplication.processEvents()
        finally:
            store.close()

    def test_scrollback_dialog_provider_paginates_older_and_newer_pages(self) -> None:
        snapshot = [f"line {index}" for index in range(4500)]
        store = TerminalScrollbackStore(max_lines=10000, line_source=lambda: snapshot)
        try:
            dialog = TerminalScrollbackDialog(provider=store)
            try:
                self.assertEqual(dialog._current_page.start_line_index, 2500)

                dialog._load_older_provider_page()
                self.assertEqual(dialog._current_page.start_line_index, 500)
                self.assertIn("line 500", dialog.viewer().toPlainText())
                self.assertNotIn("line 4499", dialog.viewer().toPlainText())

                dialog._load_newer_provider_page()
                self.assertEqual(dialog._current_page.start_line_index, 2500)
                self.assertIn("line 4499", dialog.viewer().toPlainText())
            finally:
                dialog.close()
                dialog.deleteLater()
                QApplication.processEvents()
        finally:
            store.close()

    def test_scrollback_dialog_search_jump_loads_off_page_match(self) -> None:
        snapshot = [f"line {index}" for index in range(5000)]
        snapshot[12] = "needle early"
        store = TerminalScrollbackStore(max_lines=10000, line_source=lambda: snapshot)
        try:
            dialog = TerminalScrollbackDialog(provider=store)
            try:
                self.assertNotIn("needle early", dialog.viewer().toPlainText())
                dialog._provider_search_in_flight = True
                dialog._provider_search_serial = 1
                dialog._provider_search_scroll_to_current[1] = True
                result = ScrollbackSearchResult(
                    pattern="needle",
                    case_sensitive=False,
                    use_regex=False,
                    matches=[ScrollbackMatch(line_index=12, column=0, length=6)],
                )

                dialog._on_provider_search_ready(1, result, None)

                self.assertEqual(dialog._current_page.start_line_index, 0)
                self.assertIn("needle early", dialog.viewer().toPlainText())
            finally:
                dialog.close()
                dialog.deleteLater()
                QApplication.processEvents()
        finally:
            store.close()

    def test_local_terminal_status_uses_crlf(self) -> None:
        tab = TerminalTab(settings=self.window._settings)
        captured: list[str] = []
        original_append = tab.append

        def _capture(text: str) -> None:
            captured.append(text)
            original_append(text)

        tab.append = _capture  # type: ignore[method-assign]
        tab._append_local_status("Connected. Interactive shell ready.")

        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0], "Connected. Interactive shell ready.\r\n")

    def test_terminal_bell_triggers_beep_on_bel_when_enabled(self) -> None:
        settings = AppSettings.defaults()
        settings.terminal_bell_enabled = True
        tab = TerminalTab(settings=settings)
        try:
            with (
                patch.object(tab, "_play_bell_sound_effect", return_value=False),
                patch("snakesh.ui.main_window.QApplication.beep", autospec=True) as mock_beep,
            ):
                tab._queue_terminal_output("notice\x07")
            mock_beep.assert_called_once()
        finally:
            tab.shutdown()
            tab.deleteLater()
            QApplication.processEvents()

    def test_terminal_bell_does_not_beep_when_disabled(self) -> None:
        settings = AppSettings.defaults()
        settings.terminal_bell_enabled = False
        tab = TerminalTab(settings=settings)
        try:
            with patch("snakesh.ui.main_window.QApplication.beep", autospec=True) as mock_beep:
                tab._queue_terminal_output("notice\x07")
            mock_beep.assert_not_called()
        finally:
            tab.shutdown()
            tab.deleteLater()
            QApplication.processEvents()

    def test_terminal_bell_is_debounced_for_bel_bursts(self) -> None:
        settings = AppSettings.defaults()
        settings.terminal_bell_enabled = True
        tab = TerminalTab(settings=settings)
        try:
            with (
                patch.object(tab, "_play_bell_sound_effect", return_value=False),
                patch("snakesh.ui.main_window.QApplication.beep", autospec=True) as mock_beep,
                patch(
                    "snakesh.ui.main_window.time.monotonic",
                    side_effect=[100.0, 100.05, 100.3],
                ),
            ):
                tab._queue_terminal_output("a\x07")
                tab._queue_terminal_output("b\x07")
                tab._queue_terminal_output("c\x07")
            self.assertEqual(mock_beep.call_count, 2)
        finally:
            tab.shutdown()
            tab.deleteLater()
            QApplication.processEvents()

    def test_terminal_bell_uses_sound_effect_when_available(self) -> None:
        settings = AppSettings.defaults()
        settings.terminal_bell_enabled = True
        tab = TerminalTab(settings=settings)
        try:
            effect = MagicMock()
            tab._bell_sound_effect = effect
            tab._bell_sound_effect_initialized = True
            with patch("snakesh.ui.main_window.QApplication.beep", autospec=True) as mock_beep:
                tab._queue_terminal_output("notice\x07")
            effect.stop.assert_called_once()
            effect.play.assert_called_once()
            mock_beep.assert_not_called()
        finally:
            tab.shutdown()
            tab.deleteLater()
            QApplication.processEvents()

    def test_terminal_bell_falls_back_to_app_beep_when_sound_effect_unavailable(self) -> None:
        settings = AppSettings.defaults()
        settings.terminal_bell_enabled = True
        tab = TerminalTab(settings=settings)
        try:
            tab._bell_sound_effect = None
            tab._bell_sound_effect_initialized = True
            with patch("snakesh.ui.main_window.QApplication.beep", autospec=True) as mock_beep:
                tab._queue_terminal_output("notice\x07")
            mock_beep.assert_called_once()
        finally:
            tab.shutdown()
            tab.deleteLater()
            QApplication.processEvents()

    def test_terminal_bell_queues_retry_when_sound_effect_not_loaded(self) -> None:
        settings = AppSettings.defaults()
        settings.terminal_bell_enabled = True
        tab = TerminalTab(settings=settings)
        try:
            effect = MagicMock()
            effect.isLoaded.return_value = False
            tab._bell_sound_effect = effect
            tab._bell_sound_effect_initialized = True

            tab._queue_terminal_output("notice\x07")

            effect.play.assert_not_called()
            self.assertTrue(tab._pending_bell_sound_effect_play)
            self.assertGreater(tab._bell_sound_retry_attempts_remaining, 0)
        finally:
            tab.shutdown()
            tab.deleteLater()
            QApplication.processEvents()

    def test_terminal_bell_retry_plays_when_sound_effect_finishes_loading(self) -> None:
        settings = AppSettings.defaults()
        settings.terminal_bell_enabled = True
        tab = TerminalTab(settings=settings)
        try:
            effect = MagicMock()
            effect.isLoaded.side_effect = [False, True]
            tab._bell_sound_effect = effect
            tab._bell_sound_effect_initialized = True

            tab._queue_terminal_output("notice\x07")
            tab._retry_pending_bell_sound()

            effect.play.assert_called_once()
            self.assertFalse(tab._pending_bell_sound_effect_play)
            self.assertEqual(tab._bell_sound_retry_attempts_remaining, 0)
        finally:
            tab.shutdown()
            tab.deleteLater()
            QApplication.processEvents()

    def test_terminal_bell_retry_falls_back_to_beep_when_exhausted(self) -> None:
        settings = AppSettings.defaults()
        settings.terminal_bell_enabled = True
        tab = TerminalTab(settings=settings)
        try:
            effect = MagicMock()
            effect.isLoaded.return_value = False
            tab._bell_sound_effect = effect
            tab._bell_sound_effect_initialized = True
            tab._pending_bell_sound_effect_play = True
            tab._bell_sound_retry_attempts_remaining = 0

            with patch("snakesh.ui.main_window.QApplication.beep", autospec=True) as mock_beep:
                tab._retry_pending_bell_sound()

            mock_beep.assert_called_once()
            self.assertFalse(tab._pending_bell_sound_effect_play)
            self.assertEqual(tab._bell_sound_retry_attempts_remaining, 0)
        finally:
            tab.shutdown()
            tab.deleteLater()
            QApplication.processEvents()

    def test_terminal_bell_ignores_osc_bel_terminator(self) -> None:
        settings = AppSettings.defaults()
        settings.terminal_bell_enabled = True
        tab = TerminalTab(settings=settings)
        try:
            with (
                patch.object(tab, "_play_bell_sound_effect", return_value=False),
                patch("snakesh.ui.main_window.QApplication.beep", autospec=True) as mock_beep,
            ):
                tab._queue_terminal_output("\x1b]0;Window Title\x07")
            mock_beep.assert_not_called()
        finally:
            tab.shutdown()
            tab.deleteLater()
            QApplication.processEvents()

    def test_terminal_bell_ignores_chunked_osc_bel_terminator(self) -> None:
        settings = AppSettings.defaults()
        settings.terminal_bell_enabled = True
        tab = TerminalTab(settings=settings)
        try:
            with (
                patch.object(tab, "_play_bell_sound_effect", return_value=False),
                patch("snakesh.ui.main_window.QApplication.beep", autospec=True) as mock_beep,
            ):
                tab._queue_terminal_output("\x1b]0;Window Title")
                tab._queue_terminal_output("\x07")
            mock_beep.assert_not_called()
        finally:
            tab.shutdown()
            tab.deleteLater()
            QApplication.processEvents()

    def test_terminal_bell_still_triggers_for_real_bel_after_osc(self) -> None:
        settings = AppSettings.defaults()
        settings.terminal_bell_enabled = True
        tab = TerminalTab(settings=settings)
        try:
            with (
                patch.object(tab, "_play_bell_sound_effect", return_value=False),
                patch("snakesh.ui.main_window.QApplication.beep", autospec=True) as mock_beep,
            ):
                tab._queue_terminal_output("\x1b]0;Window Title\x07")
                tab._queue_terminal_output("prompt\x07")
            mock_beep.assert_called_once()
        finally:
            tab.shutdown()
            tab.deleteLater()
            QApplication.processEvents()

    def test_terminal_visual_bell_flashes_when_enabled(self) -> None:
        settings = AppSettings.defaults()
        settings.terminal_bell_enabled = False
        settings.terminal_visual_bell_enabled = True
        tab = TerminalTab(settings=settings)
        try:
            with patch.object(tab.output, "flash_visual_bell", autospec=True) as visual:
                tab._queue_terminal_output("notice\x07")
            visual.assert_called_once_with()
        finally:
            tab.shutdown()
            tab.deleteLater()
            QApplication.processEvents()

    def test_terminal_visual_bell_does_not_flash_when_disabled(self) -> None:
        settings = AppSettings.defaults()
        settings.terminal_bell_enabled = False
        settings.terminal_visual_bell_enabled = False
        tab = TerminalTab(settings=settings)
        try:
            with patch.object(tab.output, "flash_visual_bell", autospec=True) as visual:
                tab._queue_terminal_output("notice\x07")
            visual.assert_not_called()
        finally:
            tab.shutdown()
            tab.deleteLater()
            QApplication.processEvents()

    def test_terminal_center_message_expires_after_duration(self) -> None:
        view = TerminalView(
            settings=AppSettings.defaults(),
            emulator=VT100Emulator(cols=20, rows=5, history=100),
        )
        try:
            with patch.object(view, "_update_center_message_overlay") as request_repaint:
                view.show_center_message(" Command is being terminated... ", 10)

                self.assertEqual(view._center_message_text, "Command is being terminated...")
                self.assertTrue(view._center_message_timer.isActive())
                request_repaint.assert_called_once_with()

                QTest.qWait(50)
                QApplication.processEvents()

                self.assertEqual(view._center_message_text, "")
                self.assertFalse(view._center_message_timer.isActive())
                self.assertEqual(request_repaint.call_count, 2)
        finally:
            view.deleteLater()
            QApplication.processEvents()

    def test_terminal_cursor_blink_stops_when_session_disconnects(self) -> None:
        settings = AppSettings.defaults()
        settings.terminal_cursor_blink = True
        tab = TerminalTab(settings=settings)
        try:
            self.assertTrue(tab.output._cursor_blink_timer.isActive())

            tab._mark_connection_closed(True)

            self.assertFalse(tab.output._cursor_blink_timer.isActive())
            self.assertTrue(tab.output._cursor_visible)

            tab._mark_connection_closed(False)

            self.assertTrue(tab.output._cursor_blink_timer.isActive())
        finally:
            tab.shutdown()
            tab.deleteLater()
            QApplication.processEvents()

    def test_local_shell_launch_command_uses_override_when_available(self) -> None:
        with patch(
            "snakesh.ui.main_window.shutil.which",
            side_effect=lambda executable: "/opt/custom-shell" if executable == "custom-shell" else None,
        ):
            launch = self.window._local_shell_launch_command(command_override="custom-shell --fast")

        self.assertEqual(launch, ("/opt/custom-shell", ["--fast"]))

    def test_local_shell_launch_command_strips_windows_wrapping_quotes_for_program_files_paths(self) -> None:
        with (
            patch("snakesh.ui.main_window.platform.system", return_value="Windows"),
            patch(
                "snakesh.ui.main_window.shutil.which",
                side_effect=lambda executable: executable
                if executable == r"C:\Program Files\PowerShell\7\pwsh.EXE"
                else None,
            ),
        ):
            launch = self.window._local_shell_launch_command(
                command_override=r'"C:\Program Files\PowerShell\7\pwsh.EXE" -NoLogo -NoProfile'
            )

        self.assertEqual(
            launch,
            (
                r"C:\Program Files\PowerShell\7\pwsh.EXE",
                ["-NoLogo", "-NoProfile"],
            ),
        )

    def test_local_shell_launch_command_ignores_windows_terminal_override(self) -> None:
        with (
            patch("snakesh.ui.main_window.platform.system", return_value="Windows"),
            patch(
                "snakesh.ui.main_window.shutil.which",
                side_effect=lambda executable: {
                    "wt.exe": r"C:\Users\tester\AppData\Local\Microsoft\WindowsApps\wt.exe",
                    "pwsh": r"C:\Program Files\PowerShell\7\pwsh.exe",
                }.get(executable),
            ),
        ):
            launch = self.window._local_shell_launch_command(command_override="wt.exe")

        self.assertEqual(
            launch,
            (
                r"C:\Program Files\PowerShell\7\pwsh.exe",
                ["-NoLogo", "-NoProfile"],
            ),
        )

    def test_local_shell_launch_command_prefers_real_pwsh_over_windowsapps_alias(self) -> None:
        real_pwsh = r"C:\Program Files\PowerShell\7\pwsh.exe"
        with (
            patch("snakesh.ui.main_window.platform.system", return_value="Windows"),
            patch(
                "snakesh.ui.main_window.os.path.exists",
                side_effect=lambda candidate: candidate == real_pwsh,
            ),
            patch(
                "snakesh.ui.main_window.shutil.which",
                side_effect=lambda executable: {
                    "pwsh": r"C:\Users\tester\AppData\Local\Microsoft\WindowsApps\pwsh.exe",
                    "powershell": r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
                }.get(executable),
            ),
        ):
            launch = self.window._local_shell_launch_command(command_override="pwsh")

        self.assertEqual(
            launch,
            (
                real_pwsh,
                [],
            ),
        )

    def test_local_shell_start_directory_mode_cwd_uses_current_directory(self) -> None:
        self.window._settings.local_shell_start_dir_mode = "cwd"
        self.assertEqual(self.window._resolve_local_shell_working_directory(), str(Path.cwd()))

    def test_local_shell_start_directory_mode_custom_uses_existing_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.window._settings.local_shell_start_dir_mode = "custom"
            self.window._settings.local_shell_custom_start_dir = tmp

            resolved = self.window._resolve_local_shell_working_directory()

        self.assertEqual(resolved, tmp)

    def test_local_shell_start_directory_mode_custom_falls_back_to_home_on_invalid_path(self) -> None:
        self.window._settings.local_shell_start_dir_mode = "custom"
        self.window._settings.local_shell_custom_start_dir = __file__

        resolved = self.window._resolve_local_shell_working_directory()

        self.assertEqual(resolved, str(Path.home()))

    def test_start_local_shell_worker_drops_static_columns_and_lines_env(self) -> None:
        tab = TerminalTab(settings=self.window._settings)
        try:
            with patch("snakesh.ui.main_window.QThread.start", autospec=True):
                started = tab.start_local_shell(program="/bin/sh", arguments=["-i"], working_directory=None)
            self.assertTrue(started)
            self.assertIsInstance(tab._worker, LocalShellWorker)
            assert isinstance(tab._worker, LocalShellWorker)

            captured: dict[str, object] = {}

            def _fake_execvpe(program, argv, env):  # noqa: ANN001
                captured["program"] = program
                captured["argv"] = list(argv)
                captured["env"] = dict(env)
                raise RuntimeError("stop")

            mount_root = "/tmp/.mount_SnakeSh12345"
            with (
                patch.dict(
                    "snakesh.ui.main_window.os.environ",
                    {
                        "APPDIR": mount_root,
                        "COLUMNS": "132",
                        "LINES": "44",
                        "LD_LIBRARY_PATH": os.pathsep.join(
                            (
                                f"{mount_root}/usr/lib/snakesh/_internal/PySide6/Qt/lib",
                                "/usr/lib",
                                f"{mount_root}/usr/lib/snakesh/_internal",
                                "/opt/custom/lib",
                            )
                        ),
                        "QT_PLUGIN_PATH": f"{mount_root}/usr/lib/snakesh/_internal/PySide6/Qt/plugins",
                        "QT_QPA_PLATFORM_PLUGIN_PATH": (
                            f"{mount_root}/usr/lib/snakesh/_internal/PySide6/Qt/plugins/platforms"
                        ),
                    },
                    clear=False,
                ),
                patch("snakesh.ui.main_window.os.execvpe", side_effect=_fake_execvpe),
                patch("snakesh.ui.main_window.os.write"),
            ):
                tab._worker._run_child_process()

            self.assertEqual(captured["program"], "/bin/sh")
            self.assertEqual(captured["argv"], ["/bin/sh", "-i"])
            env = captured["env"]
            assert isinstance(env, dict)
            self.assertNotIn("COLUMNS", env)
            self.assertNotIn("LINES", env)
            self.assertEqual(env["LD_LIBRARY_PATH"], os.pathsep.join(("/usr/lib", "/opt/custom/lib")))
            self.assertNotIn("QT_PLUGIN_PATH", env)
            self.assertNotIn("QT_QPA_PLATFORM_PLUGIN_PATH", env)
        finally:
            tab.shutdown()
            tab.deleteLater()
            QApplication.processEvents()

    def test_start_local_shell_uses_windows_conpty_worker_when_supported(self) -> None:
        tab = TerminalTab(settings=self.window._settings)
        try:
            with (
                patch("snakesh.ui.main_window.os.name", "nt"),
                patch("snakesh.ui.main_window.WindowsLocalShellWorker.is_supported", return_value=True),
                patch("snakesh.ui.main_window.QThread.start", autospec=True),
            ):
                started = tab.start_local_shell(
                    program="powershell.exe",
                    arguments=["-NoLogo", "-NoProfile"],
                    working_directory=None,
                )

            self.assertTrue(started)
            self.assertIsInstance(tab._worker, WindowsLocalShellWorker)
            self.assertIsNone(tab._local_process)
        finally:
            tab.shutdown()
            tab.deleteLater()
            QApplication.processEvents()

    def test_start_local_shell_rejects_external_windows_terminal_host(self) -> None:
        tab = TerminalTab(settings=self.window._settings)
        try:
            messages: list[str] = []
            with (
                patch("snakesh.ui.main_window.os.name", "nt"),
                patch.object(tab, "_append_local_status", side_effect=messages.append),
            ):
                started = tab.start_local_shell(
                    program="wt.exe",
                    arguments=[],
                    working_directory="C:\\",
                )

            self.assertFalse(started)
            self.assertIsNone(tab._worker)
            self.assertIsNone(tab._local_process)
            self.assertTrue(any("external windows terminal host" in message.lower() for message in messages))
        finally:
            tab.shutdown()
            tab.deleteLater()
            QApplication.processEvents()

    def test_start_local_shell_rejects_windows_app_execution_alias(self) -> None:
        tab = TerminalTab(settings=self.window._settings)
        try:
            messages: list[str] = []
            with (
                patch("snakesh.ui.main_window.os.name", "nt"),
                patch.object(tab, "_append_local_status", side_effect=messages.append),
            ):
                started = tab.start_local_shell(
                    program=r"C:\Users\tester\AppData\Local\Microsoft\WindowsApps\pwsh.exe",
                    arguments=[],
                    working_directory="C:\\",
                )

            self.assertFalse(started)
            self.assertIsNone(tab._worker)
            self.assertIsNone(tab._local_process)
            self.assertTrue(any("app execution alias" in message.lower() for message in messages))
        finally:
            tab.shutdown()
            tab.deleteLater()
            QApplication.processEvents()

    def test_start_local_shell_uses_hidden_backend_when_windows_conpty_is_unavailable(self) -> None:
        tab = TerminalTab(settings=self.window._settings)
        try:
            with (
                patch("snakesh.ui.main_window.os.name", "nt"),
                patch("snakesh.ui.main_window.WindowsLocalShellWorker.is_supported", return_value=False),
                patch("snakesh.ui.main_window.QThread.start", autospec=True),
                patch.object(tab, "_start_local_shell_process_fallback") as fallback,
            ):
                started = tab.start_local_shell(
                    program="powershell.exe",
                    arguments=["-NoLogo", "-NoProfile"],
                    working_directory="C:\\",
                )

            self.assertTrue(started)
            self.assertIsInstance(tab._worker, WindowsLocalShellWorker)
            assert isinstance(tab._worker, WindowsLocalShellWorker)
            self.assertTrue(tab._worker.uses_basic_process_io())
            self.assertIs(tab._worker._backend_factory, main_window._WindowsHiddenProcessBackend)
            self.assertIsNone(tab._local_process)
            fallback.assert_not_called()
        finally:
            tab.shutdown()
            tab.deleteLater()
            QApplication.processEvents()

    def test_windows_local_shell_startup_failure_retries_same_tab_with_hidden_backend(self) -> None:
        tab = TerminalTab(settings=self.window._settings)
        try:
            with (
                patch("snakesh.ui.main_window.os.name", "nt"),
                patch("snakesh.ui.main_window.WindowsLocalShellWorker.is_supported", return_value=True),
                patch("snakesh.ui.main_window.QThread.start", autospec=True),
            ):
                started = tab.start_local_shell(
                    program="powershell.exe",
                    arguments=["-NoLogo", "-NoProfile"],
                    working_directory="C:\\",
                )

            self.assertTrue(started)
            messages: list[str] = []
            with (
                patch.object(tab, "_append_local_status", side_effect=messages.append),
                patch.object(tab, "set_shell_banner") as mock_banner,
                patch.object(tab, "_start_windows_local_shell_worker", return_value=True) as fallback,
            ):
                tab._on_local_worker_error("Failed to start local shell console: startup overflow")
                tab._on_local_worker_closed()

            fallback.assert_called_once_with(
                program="powershell.exe",
                arguments=["-NoLogo", "-NoProfile"],
                working_directory="C:\\",
                backend_name="hidden-process",
                backend_factory=main_window._WindowsHiddenProcessBackend,
            )
            mock_banner.assert_called_once()
            self.assertTrue(any("embedded compatibility" in message.lower() for message in messages))
            self.assertNotIn("Shell session closed.", messages)
        finally:
            tab.shutdown()
            tab.deleteLater()
            QApplication.processEvents()

    def test_start_telnet_configures_automation_from_session(self) -> None:
        session = _build_session("Host A", "sess-telnet-automation")
        session.protocol = Protocol.TELNET
        session.port = 23
        session.telnet_use_tls = True
        session.telnet_tls_verify = False
        session.ssh_automation_enabled = True
        session.ssh_automation_steps = [SSHAutomationStep(step_type="command", command="show version")]

        tab = TerminalTab(settings=self.window._settings)
        try:
            with patch("snakesh.ui.main_window.QThread.start", autospec=True):
                tab.start_telnet(session)
            self.assertTrue(tab._automation_enabled)
            self.assertEqual(len(tab._automation_steps), 1)
            self.assertEqual(tab._automation_steps[0].command, "show version")
            self.assertIsInstance(tab._worker, TelnetShellWorker)
            assert isinstance(tab._worker, TelnetShellWorker)
            self.assertTrue(tab._worker._use_tls)
            self.assertFalse(tab._worker._tls_verify)
        finally:
            tab.shutdown()
            tab.deleteLater()
            QApplication.processEvents()

    def test_start_serial_configures_automation_from_session(self) -> None:
        session = _build_session("Host A", "sess-serial-automation")
        session.protocol = Protocol.SERIAL
        session.host = "/dev/ttyUSB0"
        session.ssh_automation_enabled = True
        session.ssh_automation_steps = [SSHAutomationStep(step_type="sleep", sleep_seconds=1.5)]

        tab = TerminalTab(settings=self.window._settings)
        try:
            with patch("snakesh.ui.main_window.QThread.start", autospec=True):
                tab.start_serial(session)
            self.assertTrue(tab._automation_enabled)
            self.assertEqual(len(tab._automation_steps), 1)
            self.assertEqual(tab._automation_steps[0].sleep_seconds, 1.5)
        finally:
            tab.shutdown()
            tab.deleteLater()
            QApplication.processEvents()

    def test_telnet_worker_wraps_socket_with_tls_when_enabled(self) -> None:
        worker = TelnetShellWorker(
            host="127.0.0.1",
            port=992,
            terminal_type="xterm-256color",
            connect_timeout_seconds=5.0,
            use_tls=True,
            tls_verify=False,
            cols=80,
            rows=24,
        )
        plain_sock = MagicMock()
        tls_sock = MagicMock()
        context = MagicMock()
        context.wrap_socket.return_value = tls_sock
        worker._run_io_loop = MagicMock()

        with (
            patch("snakesh.ui.main_window.socket.create_connection", return_value=plain_sock) as mock_connect,
            patch("snakesh.ui.main_window.ssl.create_default_context", return_value=context) as mock_tls_context,
        ):
            worker.start()

        mock_connect.assert_called_once_with(("127.0.0.1", 992), timeout=5.0)
        mock_tls_context.assert_called_once_with()
        self.assertFalse(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_NONE)
        context.wrap_socket.assert_called_once_with(plain_sock, server_hostname=None)
        tls_sock.setblocking.assert_called_once_with(False)

    def test_telnet_worker_supports_do_echo_and_locally_echoes_input(self) -> None:
        worker = TelnetShellWorker(
            host="127.0.0.1",
            port=23,
            terminal_type="xterm-256color",
            connect_timeout_seconds=10.0,
            use_tls=False,
            tls_verify=True,
            cols=80,
            rows=24,
        )
        writes: list[bytes] = []
        echoed: list[str] = []
        worker.output.connect(echoed.append)

        def _capture(payload: bytes) -> None:
            writes.append(payload)

        worker._send_bytes = _capture  # type: ignore[method-assign]
        worker._socket = object()  # type: ignore[assignment]

        worker._handle_negotiation(worker.DO, worker.OPT_ECHO)
        self.assertEqual(writes, [bytes([worker.IAC, worker.WILL, worker.OPT_ECHO])])

        worker.send_text("Rogana\r")
        worker._drain_commands()

        self.assertEqual(writes[-1], b"Rogana\r")
        self.assertEqual(echoed, ["Rogana\r\n"])

    def test_telnet_worker_locally_echoes_input_without_echo_negotiation(self) -> None:
        worker = TelnetShellWorker(
            host="127.0.0.1",
            port=23,
            terminal_type="xterm-256color",
            connect_timeout_seconds=10.0,
            use_tls=False,
            tls_verify=True,
            cols=80,
            rows=24,
        )
        writes: list[bytes] = []
        echoed: list[str] = []
        worker.output.connect(echoed.append)

        def _capture(payload: bytes) -> None:
            writes.append(payload)

        worker._send_bytes = _capture  # type: ignore[method-assign]
        worker._socket = object()  # type: ignore[assignment]

        worker.send_text("hello\r")
        worker._drain_commands()

        self.assertEqual(writes, [b"hello\r"])
        self.assertEqual(echoed, ["hello\r\n"])

    def test_telnet_worker_disables_local_echo_while_remote_echo_is_active(self) -> None:
        worker = TelnetShellWorker(
            host="127.0.0.1",
            port=23,
            terminal_type="xterm-256color",
            connect_timeout_seconds=10.0,
            use_tls=False,
            tls_verify=True,
            cols=80,
            rows=24,
        )
        writes: list[bytes] = []
        echoed: list[str] = []
        worker.output.connect(echoed.append)

        def _capture(payload: bytes) -> None:
            writes.append(payload)

        worker._send_bytes = _capture  # type: ignore[method-assign]
        worker._socket = object()  # type: ignore[assignment]

        worker._handle_negotiation(worker.DO, worker.OPT_ECHO)
        worker._handle_negotiation(worker.WILL, worker.OPT_ECHO)
        worker.send_text("look\r")
        worker._drain_commands()
        self.assertEqual(echoed, [])

        worker._handle_negotiation(worker.WONT, worker.OPT_ECHO)
        worker.send_text("look\r")
        worker._drain_commands()
        self.assertEqual(echoed, ["look\r\n"])

    def test_telnet_worker_ssl_want_read_does_not_close_loop(self) -> None:
        worker = TelnetShellWorker(
            host="127.0.0.1",
            port=992,
            terminal_type="xterm-256color",
            connect_timeout_seconds=10.0,
            use_tls=True,
            tls_verify=True,
            cols=80,
            rows=24,
        )
        sock = MagicMock()
        sock.recv.side_effect = [ssl.SSLWantReadError(), OSError(errno.EBADF, "socket closed")]
        worker._socket = sock  # type: ignore[assignment]
        errors: list[str] = []
        worker.error.connect(errors.append)

        with patch(
            "snakesh.ui.main_window.select.select",
            side_effect=[([sock], [], []), ([sock], [], [])],
        ):
            worker._run_io_loop()

        self.assertEqual(sock.recv.call_count, 2)
        self.assertEqual(errors, [])

    def test_terminal_ctrl_c_maps_without_text_payload(self) -> None:
        event = QKeyEvent(QEvent.KeyPress, Qt.Key_C, Qt.ControlModifier, "")
        self.assertEqual(TerminalView._map_key_event(event), "\x03")

    def test_terminal_arrow_key_maps_without_text_payload(self) -> None:
        event = QKeyEvent(QEvent.KeyPress, Qt.Key_Up, Qt.NoModifier, "")
        self.assertEqual(TerminalView._map_key_event(event), "\x1b[A")

    def test_terminal_arrow_key_uses_application_cursor_mode_when_enabled(self) -> None:
        event = QKeyEvent(QEvent.KeyPress, Qt.Key_Up, Qt.NoModifier, "")
        self.assertEqual(
            TerminalView._map_key_event(event, application_cursor_mode=True),
            "\x1bOA",
        )

    def test_terminal_function_keys_map_without_text_payload(self) -> None:
        cases = [
            (Qt.Key_F1, "\x1bOP"),
            (Qt.Key_F2, "\x1bOQ"),
            (Qt.Key_F3, "\x1bOR"),
            (Qt.Key_F4, "\x1bOS"),
            (Qt.Key_F5, "\x1b[15~"),
            (Qt.Key_F6, "\x1b[17~"),
            (Qt.Key_F7, "\x1b[18~"),
            (Qt.Key_F8, "\x1b[19~"),
            (Qt.Key_F9, "\x1b[20~"),
            (Qt.Key_F10, "\x1b[21~"),
            (Qt.Key_F11, "\x1b[23~"),
            (Qt.Key_F12, "\x1b[24~"),
        ]
        for key, expected in cases:
            with self.subTest(key=key):
                event = QKeyEvent(QEvent.KeyPress, key, Qt.NoModifier, "")
                self.assertEqual(TerminalView._map_key_event(event), expected)

    def test_terminal_backtab_key_maps_to_csi_shift_tab(self) -> None:
        event = QKeyEvent(QEvent.KeyPress, Qt.Key_Backtab, Qt.ShiftModifier, "")
        self.assertEqual(TerminalView._map_key_event(event), "\x1b[Z")

    def test_terminal_shift_tab_key_maps_to_csi_shift_tab(self) -> None:
        event = QKeyEvent(QEvent.KeyPress, Qt.Key_Tab, Qt.ShiftModifier, "\t")
        self.assertEqual(TerminalView._map_key_event(event), "\x1b[Z")

    def test_windows_os_type_summary_reports_windows_11_from_build_number(self) -> None:
        with (
            patch.object(main_window.platform, "win32_ver", return_value=("10", "10.0.26200", "SP0", "")),
            patch.object(main_window.platform, "win32_edition", return_value="Professional", create=True),
        ):
            self.assertEqual(main_window._windows_os_type_summary(), "Windows 11 Pro (10.0.26200-SP0)")

    def test_windows_os_type_summary_keeps_windows_10_for_older_builds(self) -> None:
        with (
            patch.object(main_window.platform, "win32_ver", return_value=("10", "10.0.19045", "SP0", "")),
            patch.object(main_window.platform, "win32_edition", return_value="Professional", create=True),
        ):
            self.assertEqual(main_window._windows_os_type_summary(), "Windows 10 Pro (10.0.19045-SP0)")

    def test_vt100_emulator_supports_dec_special_graphics_line_drawing(self) -> None:
        emulator = VT100Emulator(cols=20, rows=5, history=100)
        emulator.feed("\x1b(0\x0elqk\x0f\r\n\x0ex x\x0f\r\n\x0emqj\x0f")

        self.assertEqual(emulator.screen.display[0][:3], "┌─┐")
        self.assertEqual(emulator.screen.display[1][:3], "│ │")
        self.assertEqual(emulator.screen.display[2][:3], "└─┘")

    def test_vt100_emulator_supports_rep_csi_repeat_for_line_drawing(self) -> None:
        emulator = VT100Emulator(cols=20, rows=5, history=100)
        emulator.feed("\x1b(0lq\x1b[5bk\x0f")

        self.assertEqual(emulator.screen.display[0][:8], "┌──────┐")

    def test_vt100_emulator_supports_cursor_backward_tab(self) -> None:
        for enable_fast_parser in (True, False):
            with self.subTest(enable_fast_parser=enable_fast_parser):
                emulator = VT100Emulator(
                    cols=40,
                    rows=5,
                    history=100,
                    enable_fast_parser=enable_fast_parser,
                )
                line = "value=alpha.beta.gamma/xy"
                emulator.feed(line)

                for ch in "\x1b[Z":
                    emulator.feed(ch)
                self.assertEqual(emulator.screen.cursor.x, 24)

                emulator.feed("\b\b\x1b[1P")
                expected = "value=alpha.beta.gammaxy"
                self.assertEqual(emulator.screen.display[0][: len(expected)], expected)
                self.assertEqual(emulator.screen.cursor.x, 22)

                emulator.feed("\x1b[2Z")
                self.assertEqual(emulator.screen.cursor.x, 8)

    def test_vt100_emulator_ignores_private_sgr_sequences_without_crashing(self) -> None:
        emulator = VT100Emulator(cols=20, rows=5, history=100)
        emulator.feed("\x1b[?4mA")

        self.assertEqual(emulator.screen.display[0][:1], "A")

    def test_vt100_emulator_reports_primary_device_attributes(self) -> None:
        replies: list[str] = []
        emulator = VT100Emulator(cols=20, rows=5, history=100, process_input_writer=replies.append)

        emulator.feed("\x1b[c")

        self.assertEqual(replies, ["\x1b[?6c"])
        self.assertEqual(emulator.rendered_scrollback_text(), "(no scrollback data yet)")

    def test_vt100_emulator_reports_overridden_terminal_device_attributes(self) -> None:
        replies: list[str] = []
        emulator = VT100Emulator(
            cols=20,
            rows=5,
            history=100,
            process_input_writer=replies.append,
            terminal_type="xterm",
        )

        emulator.feed("\x1b[c")
        emulator.set_terminal_type("vt100")
        emulator.feed("\x1b[c")

        self.assertEqual(replies, ["\x1b[?62;1;2;6;7;8;9;15;18;21;22c", "\x1b[?1;0c"])
        self.assertEqual(emulator.rendered_scrollback_text(), "(no scrollback data yet)")

    def test_vt100_emulator_reports_device_status(self) -> None:
        replies: list[str] = []
        emulator = VT100Emulator(cols=20, rows=5, history=100, process_input_writer=replies.append)

        emulator.feed("\x1b[5n")

        self.assertEqual(replies, ["\x1b[0n"])
        self.assertEqual(emulator.rendered_scrollback_text(), "(no scrollback data yet)")

    def test_vt100_emulator_reports_cursor_position(self) -> None:
        replies: list[str] = []
        emulator = VT100Emulator(cols=20, rows=5, history=100, process_input_writer=replies.append)

        emulator.feed("abc")
        emulator.feed("\x1b[6n")

        self.assertEqual(replies, ["\x1b[1;4R"])
        self.assertEqual(emulator.screen.display[0][:3], "abc")

    def test_terminal_backspace_mode_prefers_ctrl_h_when_enabled(self) -> None:
        event = QKeyEvent(QEvent.KeyPress, Qt.Key_Backspace, Qt.NoModifier, "")
        self.assertEqual(
            TerminalView._map_key_event(event, backspace_prefers_ctrl_h=True),
            "\x08",
        )

    def test_terminal_backspace_ctrl_modifier_sends_alternate_codepoint(self) -> None:
        event = QKeyEvent(QEvent.KeyPress, Qt.Key_Backspace, Qt.ControlModifier, "")
        self.assertEqual(TerminalView._map_key_event(event), "\x08")
        self.assertEqual(
            TerminalView._map_key_event(event, backspace_prefers_ctrl_h=True),
            "\x7f",
        )

    def test_terminal_backspace_auto_switches_to_ctrl_h_after_caret_question_echo(self) -> None:
        tab = TerminalTab(settings=self.window._settings)
        captured: list[str] = []
        tab.output.data_input.connect(captured.append)

        backspace = QKeyEvent(QEvent.KeyPress, Qt.Key_Backspace, Qt.NoModifier, "")
        tab.output.keyPressEvent(backspace)
        self.assertEqual(captured[-1], "\x7f")

        tab._on_worker_output("^?")
        tab.output.keyPressEvent(backspace)
        self.assertEqual(captured[-1], "\x08")

    def test_terminal_backspace_auto_switches_back_to_del_after_caret_h_echo(self) -> None:
        tab = TerminalTab(settings=self.window._settings)
        captured: list[str] = []
        tab.output.data_input.connect(captured.append)
        backspace = QKeyEvent(QEvent.KeyPress, Qt.Key_Backspace, Qt.NoModifier, "")

        tab.output.keyPressEvent(backspace)
        tab._on_worker_output("^?")
        tab.output.keyPressEvent(backspace)
        self.assertEqual(captured[-1], "\x08")

        tab._on_worker_output("^H")
        tab.output.keyPressEvent(backspace)
        self.assertEqual(captured[-1], "\x7f")

    def test_local_process_input_normalizer_can_translate_del_to_ctrl_h(self) -> None:
        self.assertEqual(
            TerminalTab._normalize_input_for_local_process("ab\x7f\r\n", convert_del_to_bs=True),
            "ab\x08\r\n",
        )

    def test_local_process_output_normalizer_can_apply_destructive_backspace(self) -> None:
        self.assertEqual(
            TerminalTab._normalize_output_for_local_process("abc\b", destructive_backspace=True),
            "abc\x1b[D\x1b[P",
        )

    def test_terminal_tab_forwards_emulator_generated_input_without_using_user_input_path(self) -> None:
        tab = TerminalTab(settings=self.window._settings)
        try:
            worker = MagicMock(spec=SSHShellWorker)
            tab._worker = worker

            with patch.object(tab, "_send_terminal_input") as send_terminal_input:
                tab.append("\x1b[c")

            worker.send_text.assert_called_once_with("\x1b[?6c")
            send_terminal_input.assert_not_called()
            self.assertEqual(tab.scrollback_text(), "(no scrollback data yet)")
        finally:
            tab.shutdown()
            tab.deleteLater()
            QApplication.processEvents()

    def test_worker_output_replies_to_primary_device_attributes_immediately(self) -> None:
        tab = TerminalTab(settings=self.window._settings)
        try:
            worker = MagicMock(spec=SSHShellWorker)
            tab._worker = worker

            tab._on_worker_output("\x1b[c")

            worker.send_text.assert_called_once_with("\x1b[?6c")
            self.assertFalse(tab._pending_output_chunks)
        finally:
            tab.shutdown()
            tab.deleteLater()
            QApplication.processEvents()

    def test_ssh_shell_reports_xterm_device_attributes(self) -> None:
        session = _build_session("SSH Xterm", "ssh-xterm-device-attributes")
        tab = TerminalTab(settings=self.window._settings)
        try:
            with patch("snakesh.ui.main_window.QThread.start", autospec=True):
                tab.start_shell(
                    session=session,
                    password=None,
                    trust_unknown=False,
                    x11_forwarding=False,
                )
            assert tab._worker is not None
            tab._worker.send_text = MagicMock()  # type: ignore[method-assign]

            tab.append("\x1b[c")

            tab._worker.send_text.assert_called_once_with("\x1b[?62;1;2;6;7;8;9;15;18;21;22c")
        finally:
            tab.shutdown()
            tab.deleteLater()
            QApplication.processEvents()

    def test_local_worker_output_replies_to_primary_device_attributes_immediately(self) -> None:
        tab = TerminalTab(settings=self.window._settings)
        try:
            worker = MagicMock(spec=LocalShellWorker)
            tab._worker = worker

            tab._on_local_worker_output("\x1b[c")

            worker.send_terminal_generated_input.assert_called_once_with("\x1b[?6c")
            self.assertFalse(tab._pending_output_chunks)
        finally:
            tab.shutdown()
            tab.deleteLater()
            QApplication.processEvents()

    def test_worker_output_replies_to_split_terminal_queries_immediately(self) -> None:
        tab = TerminalTab(settings=self.window._settings)
        try:
            worker = MagicMock(spec=SSHShellWorker)
            tab._worker = worker

            tab._on_worker_output("\x1b[")
            worker.send_text.assert_not_called()

            tab._on_worker_output("c")
            worker.send_text.assert_called_once_with("\x1b[?6c")

            worker.reset_mock()
            tab._on_worker_output("\x1b[6")
            worker.send_text.assert_not_called()

            tab._on_worker_output("n")
            worker.send_text.assert_called_once_with("\x1b[1;1R")
        finally:
            tab.shutdown()
            tab.deleteLater()
            QApplication.processEvents()

    def test_worker_output_supports_c1_terminal_queries_immediately(self) -> None:
        tab = TerminalTab(settings=self.window._settings)
        try:
            worker = MagicMock(spec=SSHShellWorker)
            tab._worker = worker

            tab._on_worker_output("\x9b5n")

            worker.send_text.assert_called_once_with("\x1b[0n")
            self.assertFalse(tab._pending_output_chunks)
        finally:
            tab.shutdown()
            tab.deleteLater()
            QApplication.processEvents()

    def test_terminal_tab_does_not_reply_to_secondary_device_attributes_query(self) -> None:
        tab = TerminalTab(settings=self.window._settings)
        try:
            worker = MagicMock(spec=SSHShellWorker)
            tab._worker = worker

            tab.append("\x1b[>c")

            worker.send_text.assert_not_called()
            self.assertEqual(tab.scrollback_text(), "(no scrollback data yet)")
        finally:
            tab.shutdown()
            tab.deleteLater()
            QApplication.processEvents()

    def test_worker_output_does_not_reply_to_secondary_device_attributes_query(self) -> None:
        tab = TerminalTab(settings=self.window._settings)
        try:
            worker = MagicMock(spec=SSHShellWorker)
            tab._worker = worker

            tab._on_worker_output("\x1b[>c")
            QApplication.processEvents()
            tab._drain_pending_output()

            worker.send_text.assert_not_called()
            self.assertEqual(tab.scrollback_text(), "(no scrollback data yet)")
        finally:
            tab.shutdown()
            tab.deleteLater()
            QApplication.processEvents()

    def test_worker_output_flushes_queued_backlog_before_cursor_position_reply(self) -> None:
        tab = TerminalTab(settings=self.window._settings)
        try:
            worker = MagicMock(spec=SSHShellWorker)
            tab._worker = worker

            tab._queue_terminal_output("abc")
            self.assertGreater(tab._pending_output_chars, 0)

            tab._on_worker_output("\x1b[6n")

            worker.send_text.assert_called_once_with("\x1b[1;4R")
            self.assertFalse(tab._pending_output_chunks)
            self.assertEqual(tab.scrollback_text(), "abc")
        finally:
            tab.shutdown()
            tab.deleteLater()
            QApplication.processEvents()

    def test_non_query_output_still_queues_without_immediate_append(self) -> None:
        tab = TerminalTab(settings=self.window._settings)
        try:
            with (
                patch.object(tab, "append") as append_output,
                patch.object(tab._output_drain_timer, "start") as start_timer,
            ):
                tab._queue_terminal_output("plain text")

            append_output.assert_not_called()
            start_timer.assert_called_once_with(0)
            self.assertGreater(tab._pending_output_chars, 0)
        finally:
            tab.shutdown()
            tab.deleteLater()
            QApplication.processEvents()

    def test_terminal_output_backlog_throttles_worker_and_resumes_after_drain(self) -> None:
        tab = TerminalTab(settings=self.window._settings)
        try:
            worker = MagicMock()
            tab._worker = worker
            tab._OUTPUT_QUEUE_SLICE_CHARS = 10
            tab._OUTPUT_BACKLOG_THROTTLE_HIGH_CHARS = 64
            tab._OUTPUT_BACKLOG_THROTTLE_LOW_CHARS = 16
            tab._OUTPUT_DRAIN_BURST_THRESHOLD_CHARS = 64
            tab._OUTPUT_DRAIN_BURST_MAX_CHARS = 20
            tab._OUTPUT_DRAIN_BURST_MAX_CHUNKS = 2

            with patch.object(tab._output_drain_timer, "start"):
                tab._queue_terminal_output("x" * 80)

            self.assertTrue(tab._output_throttled)
            worker.pause_output.assert_called_once_with()
            self.assertFalse(tab._output_throttle_banner.isHidden())

            tab._drain_pending_output()
            self.assertTrue(tab._pending_output_chunks)
            self.assertTrue(tab._output_throttled)

            while tab._pending_output_chunks:
                tab._drain_pending_output()

            self.assertFalse(tab._output_throttled)
            worker.resume_output.assert_called_once_with()
            self.assertTrue(tab._output_throttle_banner.isHidden())
        finally:
            tab.shutdown()
            tab.deleteLater()
            QApplication.processEvents()

    def test_terminal_output_drain_leaves_large_backlog_for_next_event_loop_turn(self) -> None:
        tab = TerminalTab(settings=self.window._settings)
        try:
            tab._OUTPUT_QUEUE_SLICE_CHARS = 10
            tab._OUTPUT_BACKLOG_THROTTLE_HIGH_CHARS = 1000
            tab._OUTPUT_DRAIN_BURST_THRESHOLD_CHARS = 64
            tab._OUTPUT_DRAIN_BURST_MAX_CHARS = 20
            tab._OUTPUT_DRAIN_BURST_MAX_CHUNKS = 2

            with patch.object(tab._output_drain_timer, "start"):
                tab._queue_terminal_output("x" * 80)

            tab._drain_pending_output()

            self.assertGreater(tab._pending_output_chars, 0)
            self.assertTrue(tab._pending_output_chunks)
        finally:
            tab.shutdown()
            tab.deleteLater()
            QApplication.processEvents()

    def test_terminal_output_throttle_interrupt_sends_ctrl_c_without_disconnect(self) -> None:
        tab = TerminalTab(settings=self.window._settings)
        try:
            worker = MagicMock()
            tab._worker = worker
            disconnects: list[object] = []
            tab.disconnect_requested.connect(disconnects.append)

            tab._output_throttled = True
            tab._pending_output_chars = 80
            tab._update_output_throttle_banner()
            with patch.object(tab.output, "show_center_message") as show_message:
                tab._send_output_throttle_interrupt()

            worker.send_text.assert_called_once_with("\x03")
            show_message.assert_called_once_with(
                "Command is being terminated...",
                TerminalView.CENTER_MESSAGE_DURATION_MS,
            )
            self.assertEqual(disconnects, [])
        finally:
            tab.shutdown()
            tab.deleteLater()
            QApplication.processEvents()

    def test_local_shell_raw_query_reply_arrives_before_short_timeout(self) -> None:
        tab = TerminalTab(settings=self.window._settings)
        try:
            code = textwrap.dedent(
                """
                import os
                import select
                import sys
                import time
                import tty

                tty.setraw(sys.stdin.fileno())
                sys.stdout.write("\\x1b[c")
                sys.stdout.flush()
                start = time.perf_counter()
                readable, _, _ = select.select([sys.stdin], [], [], 0.02)
                elapsed = time.perf_counter() - start
                if readable:
                    data = os.read(sys.stdin.fileno(), 64)
                    sys.stdout.write(f"\\r\\nREPLY elapsed={elapsed:.6f} data={data!r}\\r\\n")
                else:
                    sys.stdout.write(f"\\r\\nNOREPLY elapsed={elapsed:.6f}\\r\\n")
                sys.stdout.flush()
                time.sleep(0.05)
                """
            )
            started = tab.start_local_shell(
                program=sys.executable,
                arguments=["-c", code],
                working_directory=str(Path.cwd()),
            )
            self.assertTrue(started)

            deadline = time.time() + 1.0
            text = ""
            while time.time() < deadline:
                QApplication.processEvents()
                text = tab.scrollback_text()
                if "REPLY elapsed=" in text or "NOREPLY elapsed=" in text:
                    break
                time.sleep(0.001)

            self.assertIn("REPLY elapsed=", text)
            self.assertIn(r"data=b'\x1b[?6c'", text)
            self.assertNotIn("NOREPLY elapsed=", text)
        finally:
            tab.shutdown()
            tab.deleteLater()
            QApplication.processEvents()

    def test_local_shell_canonical_query_reply_is_not_buffered_as_input(self) -> None:
        tab = TerminalTab(settings=self.window._settings)
        try:
            code = textwrap.dedent(
                """
                import os
                import select
                import sys
                import time
                import tty

                sys.stdout.write("\\x1b[c")
                sys.stdout.flush()
                time.sleep(0.05)
                tty.setraw(sys.stdin.fileno())
                readable, _, _ = select.select([sys.stdin], [], [], 0.05)
                if readable:
                    data = os.read(sys.stdin.fileno(), 64)
                    sys.stdout.write(f"\\r\\nBUFFERED data={data!r}\\r\\n")
                else:
                    sys.stdout.write("\\r\\nNO_BUFFERED_REPLY\\r\\n")
                sys.stdout.flush()
                time.sleep(0.05)
                """
            )
            started = tab.start_local_shell(
                program=sys.executable,
                arguments=["-c", code],
                working_directory=str(Path.cwd()),
            )
            self.assertTrue(started)

            deadline = time.time() + 1.0
            text = ""
            while time.time() < deadline:
                QApplication.processEvents()
                text = tab.scrollback_text()
                if "NO_BUFFERED_REPLY" in text or "BUFFERED data=" in text:
                    break
                time.sleep(0.001)

            self.assertIn("NO_BUFFERED_REPLY", text)
            self.assertNotIn("BUFFERED data=", text)
        finally:
            tab.shutdown()
            tab.deleteLater()
            QApplication.processEvents()

    def test_terminal_ctrl_alt_preserves_text_input(self) -> None:
        event = QKeyEvent(QEvent.KeyPress, Qt.Key_Q, Qt.ControlModifier | Qt.AltModifier, "@")
        self.assertEqual(TerminalView._map_key_event(event), "@")

    def test_terminal_key_at_maps_when_text_payload_is_empty(self) -> None:
        event = QKeyEvent(QEvent.KeyPress, Qt.Key_At, Qt.NoModifier, "")
        self.assertEqual(TerminalView._map_key_event(event), "@")

    def test_terminal_ctrl_alt_at_fallback_maps_when_text_payload_is_empty(self) -> None:
        event = QKeyEvent(QEvent.KeyPress, Qt.Key_Q, Qt.ControlModifier | Qt.AltModifier, "")
        self.assertEqual(TerminalView._map_key_event(event), "@")

    def test_terminal_color_resolver_supports_extended_rgb_tokens(self) -> None:
        fallback = "#ffffff"
        self.assertEqual(TerminalView._resolve_color("red", fallback), "#cd3131")
        self.assertEqual(TerminalView._resolve_color("ff0000", fallback), "#ff0000")
        self.assertEqual(TerminalView._resolve_color("0c2238", fallback), "#0c2238")
        self.assertEqual(TerminalView._resolve_color("#1A2B3C", fallback), "#1a2b3c")
        self.assertEqual(TerminalView._resolve_color("abc", fallback), "#aabbcc")
        self.assertEqual(TerminalView._resolve_color("not-a-color", fallback), fallback)

    def test_terminal_plain_ctrl_c_ignores_copy_shortcut_even_with_selection(self) -> None:
        tab = TerminalTab(settings=self.window._settings)
        tab.append("echo hello")
        tab.output._selection_anchor = (0, 0)
        tab.output._selection_cursor = (3, 0)
        captured: list[str] = []
        tab.output.data_input.connect(captured.append)

        event = QKeyEvent(QEvent.KeyPress, Qt.Key_C, Qt.ControlModifier, "")
        tab.output.keyPressEvent(event)

        self.assertEqual(captured, ["\x03"])
        self.assertFalse(tab.output._has_selection())

    def test_copy_and_paste_uses_selected_text(self) -> None:
        tab = TerminalTab(settings=self.window._settings)
        tab.append("echo hello")
        tab.output._selection_anchor = (0, 0)
        tab.output._selection_cursor = (3, 0)
        captured: list[str] = []
        tab.output.paste_requested.connect(captured.append)

        tab.output._copy_selection_and_paste()

        self.assertEqual(captured, ["echo"])

    def test_terminal_view_drag_enter_accepts_local_file_urls(self) -> None:
        tab = TerminalTab(settings=self.window._settings)
        self._show_terminal_tab(tab)
        mime_data = QMimeData()
        mime_data.setUrls([QUrl.fromLocalFile("/tmp/alpha one.txt")])
        event = _MimeDropEventProbe(mime_data)

        tab.output.viewport().dragEnterEvent(event)

        self.assertTrue(event.accepted)

    def test_terminal_view_drag_enter_rejects_non_local_urls(self) -> None:
        tab = TerminalTab(settings=self.window._settings)
        self._show_terminal_tab(tab)
        mime_data = QMimeData()
        mime_data.setUrls([QUrl("https://example.com/file.txt")])
        event = _MimeDropEventProbe(mime_data)

        tab.output.viewport().dragEnterEvent(event)

        self.assertFalse(event.accepted)

    def test_terminal_view_drop_event_quotes_full_local_paths(self) -> None:
        tab = TerminalTab(settings=self.window._settings)
        self._show_terminal_tab(tab)
        mime_data = QMimeData()
        mime_data.setUrls(
            [
                QUrl.fromLocalFile("/tmp/plain.txt"),
                QUrl.fromLocalFile("/tmp/alpha one.txt"),
                QUrl.fromLocalFile("/tmp/it's here.txt"),
            ]
        )
        event = _MimeDropEventProbe(mime_data)
        captured: list[str] = []
        tab.output.paste_requested.connect(captured.append)

        tab.output.viewport().dropEvent(event)

        self.assertTrue(event.accepted)
        self.assertEqual(captured, ["'/tmp/plain.txt' '/tmp/alpha one.txt' '/tmp/it'\"'\"'s here.txt'"])

    def test_command_bar_drop_event_inserts_quoted_full_local_paths(self) -> None:
        self.window.command_bar_input.setText("scp ")
        self.window.command_bar_input.setCursorPosition(len("scp "))
        mime_data = QMimeData()
        mime_data.setUrls(
            [
                QUrl.fromLocalFile("/tmp/plain.txt"),
                QUrl.fromLocalFile("/tmp/alpha one.txt"),
            ]
        )
        drag_event = _MimeDropEventProbe(mime_data)
        drop_event = _MimeDropEventProbe(mime_data)

        self.window.command_bar_input.dragEnterEvent(drag_event)
        self.window.command_bar_input.dropEvent(drop_event)

        self.assertTrue(drag_event.accepted)
        self.assertTrue(drop_event.accepted)
        self.assertEqual(
            self.window.command_bar_input.text(),
            "scp '/tmp/plain.txt' '/tmp/alpha one.txt'",
        )

    def test_terminal_double_click_selects_clicked_word(self) -> None:
        tab = TerminalTab(settings=self.window._settings)
        self._show_terminal_tab(tab)
        tab.append("hello world")

        event = self._double_click_terminal_cell(tab.output, col=1, row=0)

        self.assertTrue(event.accepted)
        self.assertEqual(tab.output._selected_text(), "hello")

    def test_terminal_double_click_keeps_non_whitespace_tokens_intact(self) -> None:
        tab = TerminalTab(settings=self.window._settings)
        self._show_terminal_tab(tab)
        tab.append("/var/log/messages\r\n192.0.2.1")

        path_event = self._double_click_terminal_cell(tab.output, col=2, row=0)
        self.assertTrue(path_event.accepted)
        self.assertEqual(tab.output._selected_text(), "/var/log/messages")

        ip_event = self._double_click_terminal_cell(tab.output, col=4, row=1)
        self.assertTrue(ip_event.accepted)
        self.assertEqual(tab.output._selected_text(), "192.0.2.1")

    def test_terminal_double_click_on_whitespace_clears_selection(self) -> None:
        tab = TerminalTab(settings=self.window._settings)
        self._show_terminal_tab(tab)
        tab.append("hello world")
        self._double_click_terminal_cell(tab.output, col=1, row=0)

        event = self._double_click_terminal_cell(tab.output, col=5, row=0)

        self.assertTrue(event.accepted)
        self.assertFalse(tab.output._has_selection())

    def test_copy_and_paste_uses_double_clicked_word_selection(self) -> None:
        tab = TerminalTab(settings=self.window._settings)
        self._show_terminal_tab(tab)
        tab.append("echo hello")
        self._double_click_terminal_cell(tab.output, col=6, row=0)
        captured: list[str] = []
        tab.output.paste_requested.connect(captured.append)

        tab.output._copy_selection_and_paste()

        self.assertEqual(captured, ["hello"])

    def test_terminal_plain_ctrl_c_ignores_copy_shortcut_after_double_click_selection(self) -> None:
        tab = TerminalTab(settings=self.window._settings)
        self._show_terminal_tab(tab)
        tab.append("echo hello")
        self._double_click_terminal_cell(tab.output, col=6, row=0)
        captured: list[str] = []
        tab.output.data_input.connect(captured.append)

        event = QKeyEvent(QEvent.KeyPress, Qt.Key_C, Qt.ControlModifier, "")
        tab.output.keyPressEvent(event)

        self.assertEqual(captured, ["\x03"])
        self.assertFalse(tab.output._has_selection())

    def test_terminal_output_drain_timer_flushes_pending_chunks(self) -> None:
        tab = TerminalTab(settings=self.window._settings)
        tab._on_worker_output("line-one\r\n")
        tab._on_worker_output("line-two\r\n")
        tab._drain_pending_output()
        QApplication.processEvents()

        text = tab.scrollback_text()
        self.assertIn("line-one", text)
        self.assertIn("line-two", text)

    def test_terminal_output_queue_schedules_drain_on_next_event_loop_turn(self) -> None:
        tab = TerminalTab(settings=self.window._settings)

        with patch.object(tab._output_drain_timer, "start") as start_timer:
            tab._queue_terminal_output("line-one\r\n")

        start_timer.assert_called_once_with(0)

    def test_terminal_output_tracks_dirty_rows_and_requests_full_repaint_after_render(self) -> None:
        tab = TerminalTab(settings=self.window._settings)
        tab.append("hello")
        tab._render_timer.stop()

        self.assertTrue(tab._pending_dirty_rows)
        self.assertFalse(tab._pending_full_repaint)

        with (
            patch.object(tab.output, "request_repaint_for_rows") as repaint_rows,
            patch.object(tab.output, "request_full_repaint") as full_repaint,
        ):
            tab._render()

        repaint_rows.assert_called_once_with({0})
        full_repaint.assert_not_called()
        self.assertEqual(tab._pending_dirty_rows, set())
        self.assertFalse(tab._pending_full_repaint)

    def test_terminal_scroll_updates_schedule_render_on_next_event_loop_turn(self) -> None:
        tab = TerminalTab(settings=self.window._settings)

        with patch.object(tab._render_timer, "isActive", return_value=False), patch.object(
            tab._render_timer, "start"
        ) as start_timer:
            tab._schedule_render(
                dirty_rows={0},
                scroll_operations=(_TerminalScrollOperation(top=0, bottom=4, delta=1),),
            )

        start_timer.assert_called_once_with(0)

    def test_terminal_scroll_updates_coalesce_when_output_backlog_exists(self) -> None:
        tab = TerminalTab(settings=self.window._settings)
        tab._pending_output_chars = 128

        with patch.object(tab._render_timer, "isActive", return_value=False), patch.object(
            tab._render_timer, "start"
        ) as start_timer:
            tab._schedule_render(
                dirty_rows={0},
                scroll_operations=(_TerminalScrollOperation(top=0, bottom=4, delta=1),),
            )

        start_timer.assert_called_once_with(tab._RENDER_INTERVAL_MS)

    def test_terminal_draw_viewport_frame_blits_framebuffer(self) -> None:
        tab = TerminalTab(settings=self.window._settings)
        tab.resize(800, 400)
        tab.show()
        QApplication.processEvents()

        tab.append("hello world\r\n")
        tab._render_timer.stop()
        tab._render()
        QApplication.processEvents()

        target = QPixmap(tab.output.viewport().size())
        target.fill(Qt.magenta)
        painter = QPainter(target)
        tab.output._ensure_viewport_framebuffer()
        tab.output._draw_viewport_frame(painter, target.rect())
        painter.end()

        image = target.toImage()
        non_magenta_pixels = 0
        for y in range(image.height()):
            for x in range(image.width()):
                if image.pixelColor(x, y).name() != "#ff00ff":
                    non_magenta_pixels += 1
                    break
            if non_magenta_pixels:
                break

        self.assertGreater(non_magenta_pixels, 0)

    def test_terminal_show_event_requests_repaint_after_output_arrives_before_first_show(self) -> None:
        tab = TerminalTab(settings=self.window._settings)
        tab.append("before-show text\r\n")
        QApplication.processEvents()

        with patch.object(tab.output, "request_full_repaint") as repaint:
            tab.resize(800, 400)
            tab.show()
            QApplication.processEvents()

        repaint.assert_called()

    def test_terminal_resize_schedules_full_repaint_from_existing_buffer(self) -> None:
        tab = TerminalTab(settings=self.window._settings)
        tab.append("hello world\r\nsecond line\r\n")
        tab._render_timer.stop()
        tab._render()

        with (
            patch.object(tab.output, "terminal_size", return_value=(120, 40)),
            patch.object(tab.output, "request_full_repaint") as full_repaint,
        ):
            tab._resize_terminal(120, 40)
            tab._render_timer.stop()
            tab._render()

        full_repaint.assert_called_once()
        self.assertIn("hello world", tab.scrollback_text())

    def test_terminal_viewport_paints_when_status_text_is_appended_before_tab_is_added(self) -> None:
        session = _build_session("Local Shell", "sess-prepaint")
        tab = TerminalTab(settings=self.window._settings)
        self.window._append_terminal_status(tab, "Starting local shell: /bin/bash -i")
        self.window._add_session_tab(session, tab, "LOCAL")
        QApplication.processEvents()

        image = tab.output.viewport().grab().toImage()
        sampled_colors: set[str] = set()
        for y in range(min(40, image.height())):
            for x in range(min(200, image.width())):
                sampled_colors.add(image.pixelColor(x, y).name())

        self.assertGreater(len(sampled_colors), 1)

    def test_terminal_append_skips_redundant_history_scrollbar_updates_when_history_state_is_unchanged(self) -> None:
        tab = TerminalTab(settings=self.window._settings)
        tab.output.sync_history_scrollbar()
        sync_results: list[bool] = []
        original_sync = tab.output.sync_history_scrollbar

        def _wrapped_sync() -> bool:
            result = original_sync()
            sync_results.append(result)
            return result

        with patch.object(tab.output, "sync_history_scrollbar", side_effect=_wrapped_sync):
            tab.append("\x1b[H\x1b[2Jpager redraw")

        self.assertEqual(sync_results, [False])

    def test_terminal_wheel_scroll_returns_exactly_to_bottom(self) -> None:
        settings = AppSettings.defaults()
        settings.terminal_scrollback_lines = 5000
        tab = TerminalTab(settings=settings)
        tab.resize(900, 700)
        tab.show()
        QApplication.processEvents()
        prompt = "operator@example-host:~/Documents$"
        lines = [f"line {index}" for index in range(180)] + [prompt, prompt]
        try:
            tab.append("\r\n".join(lines))
            QApplication.processEvents()
            view = tab.output
            bar = view.verticalScrollBar()
            self.assertGreater(bar.maximum(), 0)
            self.assertEqual(bar.value(), bar.maximum())

            for _ in range(6):
                event = _WheelEventProbe(angle_y=120)
                view._handle_wheel(event)
                self.assertTrue(event.accepted)
                self.assertFalse(event.ignored)
            QApplication.processEvents()
            self.assertLess(bar.value(), bar.maximum())

            for _ in range(6):
                event = _WheelEventProbe(angle_y=-120)
                view._handle_wheel(event)
                self.assertTrue(event.accepted)
                self.assertFalse(event.ignored)
            QApplication.processEvents()

            self.assertEqual(bar.value(), bar.maximum())
            visible_lines = [line.rstrip() for line in view._emulator.screen.display if line.rstrip()]
            self.assertGreaterEqual(len(visible_lines), 2)
            self.assertEqual(visible_lines[-2:], [prompt, prompt])
        finally:
            tab.close()
            tab.deleteLater()
            QApplication.processEvents()

    def test_terminal_wheel_angle_delta_accumulates_partial_notches(self) -> None:
        settings = AppSettings.defaults()
        settings.terminal_scrollback_lines = 5000
        tab = TerminalTab(settings=settings)
        tab.resize(900, 700)
        tab.show()
        QApplication.processEvents()
        try:
            tab.append("\r\n".join(f"line {index}" for index in range(180)))
            QApplication.processEvents()
            view = tab.output
            bar = view.verticalScrollBar()
            self.assertGreater(bar.maximum(), 0)
            starting_value = bar.value()
            lines_per_notch = max(1, int(QApplication.wheelScrollLines()))

            for _ in range(2):
                event = _WheelEventProbe(angle_y=40)
                view._handle_wheel(event)
                self.assertTrue(event.accepted)
                self.assertFalse(event.ignored)
                self.assertEqual(bar.value(), starting_value)

            event = _WheelEventProbe(angle_y=40)
            view._handle_wheel(event)
            self.assertTrue(event.accepted)
            self.assertFalse(event.ignored)
            self.assertEqual(bar.value(), starting_value - lines_per_notch)

            for _ in range(3):
                event = _WheelEventProbe(angle_y=-40)
                view._handle_wheel(event)
                self.assertTrue(event.accepted)
                self.assertFalse(event.ignored)

            self.assertEqual(bar.value(), starting_value)
        finally:
            tab.close()
            tab.deleteLater()
            QApplication.processEvents()

    def test_terminal_wheel_pixel_delta_accumulates_partial_lines(self) -> None:
        settings = AppSettings.defaults()
        settings.terminal_scrollback_lines = 5000
        tab = TerminalTab(settings=settings)
        tab.resize(900, 700)
        tab.show()
        QApplication.processEvents()
        try:
            tab.append("\r\n".join(f"line {index}" for index in range(180)))
            QApplication.processEvents()
            view = tab.output
            bar = view.verticalScrollBar()
            self.assertGreater(bar.maximum(), 0)
            starting_value = bar.value()
            line_height = max(2, view.terminal_line_height_px())

            event = _WheelEventProbe(pixel_y=line_height - 1)
            view._handle_wheel(event)
            self.assertTrue(event.accepted)
            self.assertFalse(event.ignored)
            self.assertEqual(bar.value(), starting_value)

            event = _WheelEventProbe(pixel_y=1)
            view._handle_wheel(event)
            self.assertTrue(event.accepted)
            self.assertFalse(event.ignored)
            self.assertEqual(bar.value(), starting_value - 1)

            event = _WheelEventProbe(pixel_y=-(line_height - 1))
            view._handle_wheel(event)
            self.assertTrue(event.accepted)
            self.assertFalse(event.ignored)
            self.assertEqual(bar.value(), starting_value - 1)

            event = _WheelEventProbe(pixel_y=-1)
            view._handle_wheel(event)
            self.assertTrue(event.accepted)
            self.assertFalse(event.ignored)
            self.assertEqual(bar.value(), starting_value)
        finally:
            tab.close()
            tab.deleteLater()
            QApplication.processEvents()

    def test_terminal_render_prefers_full_repaint_when_full_refresh_is_pending(self) -> None:
        tab = TerminalTab(settings=self.window._settings)
        tab._pending_dirty_rows = {0, 1}
        tab._pending_full_repaint = True

        with (
            patch.object(tab.output, "request_repaint_for_rows") as repaint_rows,
            patch.object(tab.output, "request_full_repaint") as full_repaint,
        ):
            tab._render()

        repaint_rows.assert_not_called()
        full_repaint.assert_called_once()
        self.assertEqual(tab._pending_dirty_rows, set())
        self.assertFalse(tab._pending_full_repaint)

    def test_terminal_render_uses_scroll_region_optimization_in_damage_aware_mode(self) -> None:
        tab = TerminalTab(settings=self.window._settings)
        tab._pending_scroll_operations = [
            _TerminalScrollOperation(top=1, bottom=6, delta=1),
        ]
        tab._pending_dirty_rows = {0}

        with (
            patch.object(tab.output, "apply_scroll_operations", return_value={6}) as apply_scroll_operations,
            patch.object(tab.output, "request_repaint_for_rows") as repaint_rows,
            patch.object(tab.output, "request_full_repaint") as full_repaint,
        ):
            tab._render()

        apply_scroll_operations.assert_called_once_with((_TerminalScrollOperation(top=1, bottom=6, delta=1),))
        repaint_rows.assert_called_once_with({0, 6})
        full_repaint.assert_not_called()
        self.assertEqual(tab._pending_scroll_operations, [])

    def test_terminal_destructive_line_edit_schedules_full_repaint(self) -> None:
        tab = TerminalTab(settings=self.window._settings)
        tab.append("GATEWAY5=sample01")
        tab._render_timer.stop()
        tab._render()

        tab.append("\r\x1b[12G\x1b[K")
        tab._render_timer.stop()

        self.assertTrue(tab._pending_full_repaint)

        with (
            patch.object(tab.output, "request_repaint_for_rows") as repaint_rows,
            patch.object(tab.output, "request_full_repaint") as full_repaint,
        ):
            tab._render()

        repaint_rows.assert_not_called()
        full_repaint.assert_called_once()
        self.assertFalse(tab._pending_full_repaint)

    def test_terminal_view_scroll_damage_repaints_entire_scroll_region(self) -> None:
        tab = TerminalTab(settings=self.window._settings)
        view = tab.output
        view._framebuffer_valid = True

        with patch.object(view, "terminal_size", return_value=(20, 8)):
            exposed_rows = view.apply_scroll_operations((_TerminalScrollOperation(top=1, bottom=6, delta=1),))

        self.assertEqual(exposed_rows, {1, 2, 3, 4, 5, 6})

    def test_terminal_view_coalesces_adjacent_scroll_damage_into_one_repaint_region(self) -> None:
        tab = TerminalTab(settings=self.window._settings)
        view = tab.output
        view._framebuffer_valid = True

        with patch.object(view, "terminal_size", return_value=(20, 8)):
            exposed_rows = view.apply_scroll_operations(
                (
                    _TerminalScrollOperation(top=1, bottom=6, delta=1),
                    _TerminalScrollOperation(top=1, bottom=6, delta=1),
                    _TerminalScrollOperation(top=1, bottom=6, delta=1),
                )
            )

        self.assertEqual(exposed_rows, {1, 2, 3, 4, 5, 6})

    def test_terminal_render_mode_env_controls_damage_aware_path(self) -> None:
        for value, expect_full in ((None, False), ("full_frame", True), ("damage_aware", False)):
            with self.subTest(render_mode=value):
                with patch.dict("snakesh.ui.main_window.os.environ", {}, clear=False):
                    if value is None:
                        os.environ.pop(TERMINAL_RENDER_MODE_ENV, None)
                    else:
                        os.environ[TERMINAL_RENDER_MODE_ENV] = value
                    tab = TerminalTab(settings=self.window._settings)
                    tab._pending_dirty_rows = {0}
                    with (
                        patch.object(tab.output, "request_repaint_for_rows") as repaint_rows,
                        patch.object(tab.output, "request_full_repaint") as full_repaint,
                    ):
                        tab._render()

                if expect_full:
                    repaint_rows.assert_not_called()
                    full_repaint.assert_called_once()
                else:
                    repaint_rows.assert_called_once_with({0})
                    full_repaint.assert_not_called()

    def test_terminal_large_output_backlog_drains_in_burst_mode(self) -> None:
        tab = TerminalTab(settings=self.window._settings)
        chunk = "x" * 4096
        total_chars = 0
        while total_chars < (tab._OUTPUT_DRAIN_BURST_THRESHOLD_CHARS + 4096):
            tab._pending_output_chunks.append(chunk)
            tab._pending_output_chars += len(chunk)
            total_chars += len(chunk)

        with patch.object(tab, "append") as append_output:
            tab._drain_pending_output()

        append_output.assert_called_once()
        drained_payload = append_output.call_args.args[0]
        self.assertGreater(len(drained_payload), tab._OUTPUT_DRAIN_MAX_CHARS)
        self.assertLess(tab._pending_output_chars, total_chars)

    def test_vt100_emulator_reports_scroll_damage_for_edge_scroll(self) -> None:
        emulator = VT100Emulator(cols=8, rows=4, history=100)
        emulator.feed("1.......\r\n2.......\r\n3.......\r\n4.......")
        emulator.consume_dirty_rows()
        emulator.consume_render_damage()

        emulator.feed("\x1b[4;1H\n")

        scroll_ops, structural_damage = emulator.consume_render_damage()
        self.assertEqual(scroll_ops, (_TerminalScrollOperation(top=0, bottom=3, delta=1),))
        self.assertFalse(structural_damage)

    def test_vt100_emulator_shortened_nano_line_has_no_orphan_character(self) -> None:
        for edit_sequence in ("\r\x1b[12G\x1b[K", "\r\x1b[12G\x1b[6P"):
            with self.subTest(edit_sequence=repr(edit_sequence)):
                emulator = VT100Emulator(cols=40, rows=5, history=100)
                emulator.feed("GATEWAY5=sample01")
                emulator.consume_dirty_rows()
                emulator.consume_render_damage()

                emulator.feed(edit_sequence)

                self.assertEqual(emulator.screen.display[0][:11], "GATEWAY5=sa")
                self.assertEqual(emulator.screen.display[0][11:17], " " * 6)
                _scroll_ops, structural_damage = emulator.consume_render_damage()
                self.assertTrue(structural_damage)

    def test_vt100_emulator_reports_scroll_damage_for_reverse_index(self) -> None:
        emulator = VT100Emulator(cols=8, rows=6, history=100)
        emulator.feed("\x1b[2;5r\x1b[2;1H\x1bM")

        scroll_ops, structural_damage = emulator.consume_render_damage()

        self.assertEqual(scroll_ops, (_TerminalScrollOperation(top=1, bottom=4, delta=-1),))
        self.assertFalse(structural_damage)

    def test_vt100_emulator_supports_csi_scroll_up_with_margins(self) -> None:
        emulator = VT100Emulator(cols=4, rows=6, history=100)
        for row, ch in enumerate("ABCDEF", start=1):
            emulator.feed(f"\x1b[{row};1H{ch}")
        emulator.feed("\x1b[2;5r")
        emulator.feed("\x1b[6;2H")
        emulator.feed("\x1b[2S")

        display = emulator.screen.display
        self.assertEqual(display[0][:1], "A")
        self.assertEqual(display[1][:1], "D")
        self.assertEqual(display[2][:1], "E")
        self.assertEqual(display[3][:1], " ")
        self.assertEqual(display[4][:1], " ")
        self.assertEqual(display[5][:1], "F")
        self.assertEqual(emulator.screen.cursor.y, 5)
        self.assertEqual(emulator.screen.cursor.x, 1)

    def test_vt100_emulator_supports_csi_scroll_down_with_margins(self) -> None:
        emulator = VT100Emulator(cols=4, rows=6, history=100)
        for row, ch in enumerate("ABCDEF", start=1):
            emulator.feed(f"\x1b[{row};1H{ch}")
        emulator.feed("\x1b[2;5r")
        emulator.feed("\x1b[1;3H")
        emulator.feed("\x1b[2T")

        display = emulator.screen.display
        self.assertEqual(display[0][:1], "A")
        self.assertEqual(display[1][:1], " ")
        self.assertEqual(display[2][:1], " ")
        self.assertEqual(display[3][:1], "B")
        self.assertEqual(display[4][:1], "C")
        self.assertEqual(display[5][:1], "F")
        self.assertEqual(emulator.screen.cursor.y, 0)
        self.assertEqual(emulator.screen.cursor.x, 2)

    def test_vt100_emulator_rendered_scrollback_preserves_history_order_across_scrolls(self) -> None:
        emulator = VT100Emulator(cols=12, rows=3, history=100)
        emulator.feed("line1\r\nline2\r\nline3\r\nline4\r\nline5")

        self.assertEqual(
            emulator.rendered_scrollback_lines(),
            ["line1", "line2", "line3", "line4", "line5"],
        )

    def test_scrollback_text_flushes_pending_output_queue(self) -> None:
        tab = TerminalTab(settings=self.window._settings)
        tab._queue_terminal_output("line-three\r\n")
        tab._queue_terminal_output("line-four\r\n")

        text = tab.scrollback_text()

        self.assertIn("line-three", text)
        self.assertIn("line-four", text)

    def test_show_scrollback_uses_background_store_without_draining_pending_output_queue(self) -> None:
        tab = TerminalTab(settings=self.window._settings)
        try:
            tab.append("line one\r\n")
            tab._queue_terminal_output("queued line\r\n")

            with patch.object(tab, "_drain_pending_output", side_effect=AssertionError("unexpected drain")):
                tab.show_scrollback()
                QApplication.processEvents()

            dialog = tab._scrollback_dialog
            self.assertIsNotNone(dialog)
            assert dialog is not None
            self.assertIn("line one", dialog._viewer.toPlainText())
            self.assertNotIn("queued line", dialog._viewer.toPlainText())
            self.assertGreater(tab._pending_output_chars, 0)
        finally:
            dialog = tab._scrollback_dialog
            if dialog is not None:
                dialog.close()
                dialog.deleteLater()
                tab._scrollback_dialog = None
            tab.shutdown()
            tab.deleteLater()
            QApplication.processEvents()

    def test_show_scrollback_matches_rendered_prompt_after_bash_history_backlog_redraw(self) -> None:
        tab = TerminalTab(settings=self.window._settings)
        try:
            tab.append("[user@host ~]$ echo two\b\b\bone\b\b\btwo")
            tab.show_scrollback()
            QApplication.processEvents()

            dialog = tab._scrollback_dialog
            self.assertIsNotNone(dialog)
            assert dialog is not None
            self.assertEqual(dialog._viewer.toPlainText(), tab.scrollback_text())
            self.assertEqual(dialog._viewer.toPlainText(), "[user@host ~]$ echo two")
        finally:
            dialog = tab._scrollback_dialog
            if dialog is not None:
                dialog.close()
                dialog.deleteLater()
                tab._scrollback_dialog = None
            tab.shutdown()
            tab.deleteLater()
            QApplication.processEvents()

    def test_provider_backed_scrollback_shows_bounded_paged_window(self) -> None:
        settings = AppSettings.defaults()
        settings.terminal_scrollback_lines = 5000
        tab = TerminalTab(settings=settings)
        lines = [f"line {index}" for index in range(2200)]
        lines[25] = "needle older"
        tab.append("\r\n".join(lines) + "\r\n")
        tab.show_scrollback()
        dialog = tab._scrollback_dialog
        self.assertIsNotNone(dialog)
        assert dialog is not None
        try:
            button_labels = {button.text().strip().lower() for button in dialog.findChildren(QPushButton)}
            self.assertIn("older", button_labels)
            self.assertIn("newer", button_labels)
            self.assertNotIn("needle older", dialog._viewer.toPlainText())

            self._search_scrollback_dialog(dialog, "needle older")
            self._wait_for(lambda: dialog._find_bar._match_label.text() == "1/1")

            self.assertIn("needle older", dialog._viewer.toPlainText())
            self.assertLessEqual(len(dialog._viewer.toPlainText().splitlines()), dialog._PROVIDER_PAGE_LINES)
        finally:
            dialog.close()
            dialog.deleteLater()
            tab._scrollback_dialog = None
            QApplication.processEvents()

    def test_provider_backed_scrollback_stabilizes_active_search_without_following_live_output(self) -> None:
        tab = TerminalTab(settings=self.window._settings)
        initial_text = "\r\n".join([f"line {index}" for index in range(120)])
        tab.append(f"{initial_text}\r\nneedle first\r\n")
        tab.show_scrollback()
        dialog = tab._scrollback_dialog
        self.assertIsNotNone(dialog)
        assert dialog is not None
        try:
            self._search_scrollback_dialog(dialog, "needle")
            self._wait_for(lambda: dialog._find_bar._match_label.text() == "1/1")

            dialog._viewer.verticalScrollBar().setValue(20)
            QApplication.processEvents()
            before_scroll = dialog._viewer.verticalScrollBar().value()

            tab.append("line 120\r\nneedle second\r\n")
            QApplication.processEvents()

            self.assertNotIn("needle second", dialog._viewer.toPlainText())
            self.assertEqual(dialog._find_bar._match_label.text(), "1/1")
            self.assertEqual(dialog._viewer.verticalScrollBar().value(), before_scroll)
        finally:
            dialog.close()
            dialog.deleteLater()
            tab._scrollback_dialog = None
            QApplication.processEvents()

    def test_provider_backed_scrollback_status_reflects_history_pause_and_resume(self) -> None:
        settings = AppSettings.defaults()
        settings.terminal_scrollback_lines = 5000
        tab = TerminalTab(settings=settings)
        lines = [f"line {index}" for index in range(2400)]
        tab.append("\r\n".join(lines) + "\r\n")
        tab.show_scrollback()
        dialog = tab._scrollback_dialog
        self.assertIsNotNone(dialog)
        assert dialog is not None
        try:
            bar = dialog._viewer.verticalScrollBar()
            self.assertTrue(dialog._live_status_label.isVisible())
            self.assertTrue(dialog._resume_refresh_btn.isVisible())
            self.assertEqual(dialog._live_status_label.text(), "Running: Live Refresh")
            self.assertFalse(dialog._resume_refresh_btn.isEnabled())
            self.assertGreater(bar.maximum(), 0)

            bar.setValue(0)
            QApplication.processEvents()

            self.assertEqual(dialog._live_status_label.text(), "Paused: Viewing History")
            self.assertTrue(dialog._resume_refresh_btn.isEnabled())

            dialog._resume_refresh_btn.click()
            self._wait_for(
                lambda: dialog._live_status_label.text() == "Running: Live Refresh"
                and bar.value() == bar.maximum()
            )
            self.assertFalse(dialog._resume_refresh_btn.isEnabled())
        finally:
            dialog.close()
            dialog.deleteLater()
            tab._scrollback_dialog = None
            QApplication.processEvents()

    def test_provider_backed_scrollback_status_reflects_active_search_and_continue_refresh(self) -> None:
        tab = TerminalTab(settings=self.window._settings)
        initial_text = "\r\n".join([f"line {index}" for index in range(120)])
        tab.append(f"{initial_text}\r\nneedle first\r\n")
        tab.show_scrollback()
        dialog = tab._scrollback_dialog
        self.assertIsNotNone(dialog)
        assert dialog is not None
        try:
            self._search_scrollback_dialog(dialog, "needle")
            self._wait_for(lambda: dialog._find_bar._match_label.text() == "1/1")

            self.assertEqual(dialog._live_status_label.text(), "Paused: Search Active")
            self.assertTrue(dialog._resume_refresh_btn.isEnabled())

            dialog._resume_refresh_btn.click()
            self._wait_for(lambda: dialog._live_status_label.text() == "Running: Live Refresh")

            self.assertTrue(dialog._find_bar.isHidden())
            self.assertEqual(dialog._find_bar._input.text(), "")
            self.assertFalse(dialog._resume_refresh_btn.isEnabled())
        finally:
            dialog.close()
            dialog.deleteLater()
            tab._scrollback_dialog = None
            QApplication.processEvents()

    def test_scrollback_dialog_search_bar_is_hidden_by_default(self) -> None:
        dialog = self._show_scrollback_dialog("alpha\nbeta\n")
        try:
            self.assertTrue(dialog._find_bar.isHidden())
            self.assertEqual(dialog._find_bar._match_label.text(), "")
            self.assertFalse(dialog.isModal())
            self.assertEqual(dialog.windowModality(), Qt.NonModal)
        finally:
            dialog.close()
            dialog.deleteLater()
            QApplication.processEvents()

    def test_scrollback_dialog_search_button_toggles_search_bar(self) -> None:
        dialog = self._show_scrollback_dialog("alpha\nbeta\n")
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

    def test_scrollback_dialog_ctrl_f_opens_and_focuses_search(self) -> None:
        dialog = self._show_scrollback_dialog("alpha\nbeta\n")
        try:
            self.assertTrue(dialog._find_bar.isHidden())

            QTest.keyClick(dialog, Qt.Key_F, Qt.ControlModifier)
            QApplication.processEvents()

            self.assertFalse(dialog._find_bar.isHidden())
            self.assertTrue(dialog._find_bar._input.hasFocus())
        finally:
            dialog.close()
            dialog.deleteLater()
            QApplication.processEvents()

    def test_scrollback_dialog_escape_hides_search_and_clears_matches(self) -> None:
        dialog = self._show_scrollback_dialog("alpha\nbeta\nbeta\n")
        try:
            self._search_scrollback_dialog(dialog, "beta")

            self.assertEqual(dialog._search_matches, [(6, 4), (11, 4)])
            self.assertEqual(dialog._find_bar._match_label.text(), "1/2")

            QTest.keyClick(dialog._find_bar._input, Qt.Key_Escape)
            QApplication.processEvents()

            self.assertTrue(dialog._find_bar.isHidden())
            self.assertEqual(dialog._search_matches, [])
            self.assertEqual(dialog._find_bar._match_label.text(), "")
        finally:
            dialog.close()
            dialog.deleteLater()
            QApplication.processEvents()

    def test_scrollback_dialog_plain_text_search_navigation_wraps(self) -> None:
        dialog = self._show_scrollback_dialog("alpha\nbeta\nbeta\n")
        try:
            self._search_scrollback_dialog(dialog, "beta")

            self.assertEqual(dialog._search_matches, [(6, 4), (11, 4)])
            self.assertEqual(dialog._find_bar._match_label.text(), "1/2")
            self.assertEqual(dialog._viewer.textCursor().position(), 6)
            colors_by_start = {
                selection.cursor.selectionStart(): selection.format.background().color()
                for selection in dialog._viewer.extraSelections()
            }
            self.assertEqual(set(colors_by_start), {6, 11})
            self.assertGreater(
                colors_by_start[6].green(),
                colors_by_start[6].red(),
            )
            self.assertGreater(
                colors_by_start[11].red(),
                colors_by_start[11].green(),
            )

            dialog._on_navigate(1)
            self.assertEqual(dialog._find_bar._match_label.text(), "2/2")
            self.assertEqual(dialog._viewer.textCursor().position(), 11)
            colors_by_start = {
                selection.cursor.selectionStart(): selection.format.background().color()
                for selection in dialog._viewer.extraSelections()
            }
            self.assertGreater(
                colors_by_start[6].red(),
                colors_by_start[6].green(),
            )
            self.assertGreater(
                colors_by_start[11].green(),
                colors_by_start[11].red(),
            )

            dialog._on_navigate(1)
            self.assertEqual(dialog._find_bar._match_label.text(), "1/2")
            self.assertEqual(dialog._viewer.textCursor().position(), 6)
        finally:
            dialog.close()
            dialog.deleteLater()
            QApplication.processEvents()

    def test_scrollback_dialog_enter_advances_in_last_search_direction_without_closing(self) -> None:
        dialog = self._show_scrollback_dialog("alpha\nbeta\nbeta\nbeta\n")
        try:
            self._search_scrollback_dialog(dialog, "beta")
            self.assertTrue(dialog.isVisible())
            self.assertEqual(dialog._viewer.textCursor().position(), 6)

            QTest.keyClick(dialog._find_bar._input, Qt.Key_Return)
            QApplication.processEvents()
            self.assertTrue(dialog.isVisible())
            self.assertEqual(dialog._viewer.textCursor().position(), 11)

            dialog._find_bar._prev_btn.click()
            QApplication.processEvents()
            self.assertEqual(dialog._viewer.textCursor().position(), 6)

            QTest.keyClick(dialog._find_bar._input, Qt.Key_Return)
            QApplication.processEvents()
            self.assertTrue(dialog.isVisible())
            self.assertEqual(dialog._viewer.textCursor().position(), 16)
        finally:
            dialog.close()
            dialog.deleteLater()
            QApplication.processEvents()

    def test_scrollback_dialog_supports_case_sensitive_and_regex_search(self) -> None:
        dialog = self._show_scrollback_dialog("Alpha\nalpha\nbeta42\nbeta99\n")
        try:
            self._search_scrollback_dialog(dialog, "Alpha", case_sensitive=True)
            self.assertEqual(dialog._search_matches, [(0, 5)])
            self.assertEqual(dialog._find_bar._match_label.text(), "1/1")

            self._search_scrollback_dialog(dialog, r"beta\d\d", use_regex=True)
            self.assertEqual(dialog._search_matches, [(12, 6), (19, 6)])
            self.assertEqual(dialog._find_bar._match_label.text(), "1/2")

            self._search_scrollback_dialog(dialog, "[", use_regex=True)
            self.assertEqual(dialog._search_matches, [])
            self.assertEqual(dialog._find_bar._match_label.text(), "No matches")
        finally:
            dialog.close()
            dialog.deleteLater()
            QApplication.processEvents()

    def test_terminal_tab_live_scrollback_dialog_refreshes_active_search_without_following(self) -> None:
        tab = TerminalTab(settings=self.window._settings)
        initial_text = "\r\n".join([f"line {index}" for index in range(120)])
        tab.append(f"{initial_text}\r\nneedle first\r\n")
        dialog = self._show_scrollback_dialog(tab.scrollback_text())
        try:
            tab._scrollback_dialog = dialog
            self._search_scrollback_dialog(dialog, "needle")

            dialog._viewer.verticalScrollBar().setValue(20)
            QApplication.processEvents()
            before_scroll = dialog._viewer.verticalScrollBar().value()

            tab.append("line 120\r\nneedle second\r\n")
            self._wait_for(lambda: "needle second" in dialog._viewer.toPlainText())

            self.assertIn("needle second", dialog._viewer.toPlainText())
            self.assertEqual(dialog._find_bar._match_label.text(), "1/2")
            self.assertEqual(dialog._viewer.verticalScrollBar().value(), before_scroll)
        finally:
            tab._scrollback_dialog = None
            dialog.close()
            dialog.deleteLater()
            QApplication.processEvents()

    def test_terminal_tab_apply_settings_updates_open_scrollback_dialog_font_live(self) -> None:
        tab = TerminalTab(settings=self.window._settings)
        tab.append("alpha\r\nbeta\r\n")
        tab.show_scrollback()
        QApplication.processEvents()

        dialog = tab._scrollback_dialog
        self.assertIsNotNone(dialog)
        assert dialog is not None
        try:
            before_viewer_font = dialog._viewer.font().pointSize()
            updated = AppSettings.from_dict(self.window._settings.to_dict())
            updated.terminal_font_pt = max(before_viewer_font + 4, 12)

            tab.apply_settings(updated)
            QApplication.processEvents()

            self.assertEqual(dialog._viewer.toPlainText(), tab.scrollback_text())
            self.assertEqual(dialog._viewer.font().pointSize(), updated.terminal_font_pt)
            self.assertGreater(dialog._viewer.font().pointSize(), before_viewer_font)
        finally:
            dialog.close()
            dialog.deleteLater()
            tab._scrollback_dialog = None
            tab.shutdown()
            tab.deleteLater()
            QApplication.processEvents()

    def test_scrollback_dialog_close_button_closes_on_mouse_click(self) -> None:
        tab = TerminalTab(settings=self.window._settings)
        tab.append("hello\r\n")

        tab.show_scrollback()
        QApplication.processEvents()

        dialog = tab._scrollback_dialog
        self.assertIsNotNone(dialog)
        assert dialog is not None
        close_button = next(
            button
            for button in dialog.findChildren(QPushButton)
            if button.text().strip().lower() == "close"
        )
        QTest.mouseClick(close_button, Qt.LeftButton)
        QApplication.processEvents()

        self.assertIs(tab._scrollback_dialog, dialog)
        self.assertFalse(dialog.isVisible())

    def test_scrollback_dialog_reopens_same_window_immediately_after_close(self) -> None:
        tab = TerminalTab(settings=self.window._settings)
        tab.append("hello\r\n")

        tab.show_scrollback()
        QApplication.processEvents()
        first_dialog = tab._scrollback_dialog
        assert first_dialog is not None
        first_dialog.close()
        QApplication.processEvents()

        tab.show_scrollback()
        QApplication.processEvents()

        self.assertIs(tab._scrollback_dialog, first_dialog)
        self.assertTrue(first_dialog.isVisible())

    def test_scrollback_dialog_reopen_is_blocked_briefly_after_close(self) -> None:
        tab = TerminalTab(settings=self.window._settings)
        tab._scrollback_reopen_block_until = time.monotonic() + 1.0

        before = [widget for widget in QApplication.topLevelWidgets() if isinstance(widget, QDialog)]
        tab.show_scrollback()
        QApplication.processEvents()
        after = [widget for widget in QApplication.topLevelWidgets() if isinstance(widget, QDialog)]

        self.assertEqual(len(after), len(before))

    def test_terminal_logging_writes_output_and_stops(self) -> None:
        tab = TerminalTab(settings=self.window._settings)
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "session.log"
            tab.start_logging(str(log_path))
            tab.append("line1\r\nline2\r\n")
            tab.stop_logging()
            self.assertFalse(tab.is_logging_enabled())
            content = log_path.read_text(encoding="utf-8")
            self.assertIn("line1", content)
            self.assertIn("line2", content)

    def test_terminal_logging_stop_flushes_pending_output_queue(self) -> None:
        tab = TerminalTab(settings=self.window._settings)
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "session.log"
            tab.start_logging(str(log_path))
            tab._queue_terminal_output("line-pending\r\n")
            tab.stop_logging()
            content = log_path.read_text(encoding="utf-8")
            self.assertIn("line-pending", content)

    def test_terminal_logging_processes_backspace(self) -> None:
        tab = TerminalTab(settings=self.window._settings)
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "session.log"
            tab.start_logging(str(log_path))
            tab.append("[root@AS1Lab ~]# h")
            tab.append("\b")
            tab.append("top\r\n")
            tab.stop_logging()
            content = log_path.read_text(encoding="utf-8")
            self.assertIn("[root@AS1Lab ~]# top", content)
            self.assertNotIn("h\btop", content)

    def test_terminal_logging_stops_on_shutdown(self) -> None:
        tab = TerminalTab(settings=self.window._settings)
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "session.log"
            tab.start_logging(str(log_path))
            self.assertTrue(tab.is_logging_enabled())
            tab.shutdown()
            self.assertFalse(tab.is_logging_enabled())

    def test_default_terminal_log_path_uses_settings_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.window._settings.terminal_log_dir = tmp
            session = _build_session("Host A", "sess-log-path")
            tab = TerminalTab(settings=self.window._settings)
            self.window._add_session_tab(session, tab, "SSH")
            default_path = self.window._default_terminal_log_path(tab)
            self.assertTrue(str(default_path).startswith(str(Path(tmp))))

    def test_global_terminal_log_path_includes_session_folder_and_required_filename_format(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.window._settings.terminal_log_dir = tmp
            session = _build_session("Host Alpha", "sess-global-log")
            session.folder = "MainSessionFolder/SessionSubFolder"
            path = self.window._global_terminal_log_path_for_session(session)

            self.assertTrue(
                str(path).startswith(str(Path(tmp) / "MainSessionFolder" / "SessionSubFolder"))
            )
            self.assertRegex(path.name, r"^Host_Alpha-\d{8}-\d{4}-\d{2}\.log$")

    def test_session_log_cleanup_removes_files_older_than_retention(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.window._settings.terminal_log_dir = tmp
            self.window._settings.session_log_cleanup_enabled = True
            self.window._settings.session_log_retention_days = 7

            stale = Path(tmp) / "Main" / "stale.log"
            fresh = Path(tmp) / "Main" / "fresh.log"
            stale.parent.mkdir(parents=True, exist_ok=True)
            stale.write_text("old", encoding="utf-8")
            fresh.write_text("new", encoding="utf-8")
            stale_epoch = time.time() - (9 * 24 * 60 * 60)
            os.utime(stale, (stale_epoch, stale_epoch))

            removed = self.window._prune_session_log_files()

            self.assertEqual(removed, 1)
            self.assertFalse(stale.exists())
            self.assertTrue(fresh.exists())

    def test_global_logging_starts_automatically_for_new_terminal_tabs_when_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.window._settings.terminal_log_dir = tmp
            self.window._settings.global_session_logging_enabled = True
            session = _build_session("Host Auto Log", "sess-auto-log")
            session.folder = "Main/Branch"
            tab = TerminalTab(settings=self.window._settings)

            self.window._add_session_tab(session, tab, "SSH")

            self.assertTrue(tab.is_logging_enabled())
            path = tab.logging_path()
            self.assertIsNotNone(path)
            assert path is not None
            self.assertIn(str(Path(tmp) / "Main" / "Branch"), path)

    def test_duplicate_session_creates_numbered_unique_name(self) -> None:
        original = _build_session("EdgeRouter", "sess-dup-1")
        self.session_service._sessions = [original]

        self.window._duplicate_session(original)

        names = sorted(session.name for session in self.session_service._sessions)
        self.assertIn("EdgeRouter", names)
        self.assertIn("EdgeRouter 2", names)
        self.assertEqual(len(self.session_service._sessions), 2)

    def test_session_list_mode_shown_auto_and_float(self) -> None:
        self.window._set_session_list_mode("auto")
        QApplication.processEvents()
        self.assertFalse(self.window._is_session_list_visible())
        self.assertEqual(self.settings_service._settings.session_list_visibility_mode, "auto")

        self.window._set_session_list_mode("float")
        QApplication.processEvents()
        self.assertTrue(self.window._is_session_list_visible())
        self.assertIsNotNone(self.window._session_list_window)
        self.assertEqual(self.settings_service._settings.session_list_visibility_mode, "float")

        self.window._set_session_list_mode("shown")
        QApplication.processEvents()
        self.assertTrue(self.window._is_session_list_visible())
        self.assertIsNone(self.window._session_list_window)
        self.assertEqual(self.settings_service._settings.session_list_visibility_mode, "shown")

    def test_session_list_auto_mode_reveals_on_hover_and_rehides(self) -> None:
        self.window._set_session_list_mode("auto")
        QApplication.processEvents()
        self.assertFalse(self.window._is_session_list_visible())

        with patch.object(self.window, "_is_pointer_over_session_list_handle", return_value=True):
            self.window._maybe_auto_hide_session_list()
            QApplication.processEvents()
            self.assertTrue(self.window._is_session_list_visible())

        with patch.object(self.window, "_widget_contains_global_pos", return_value=False):
            self.window._maybe_auto_hide_session_list()
        QApplication.processEvents()
        self.assertFalse(self.window._is_session_list_visible())

    def test_session_list_mode_button_uses_push_button_menu_with_expected_modes(self) -> None:
        button = self.window._session_list_mode_btn
        self.assertIsInstance(button, QPushButton)
        self.assertEqual(button.text(), "Show")
        self.assertIs(button.menu(), self.window._session_list_mode_menu)
        self.assertEqual(
            [action.text() for action in self.window._session_list_mode_menu.actions()],
            ["Show", "Auto", "Float"],
        )

    def test_session_list_float_mode_moves_panel_into_separate_window(self) -> None:
        self.window._set_session_list_mode("float")
        QApplication.processEvents()

        session_window = self.window._session_list_window
        self.assertIsNotNone(session_window)
        assert session_window is not None
        self.assertIs(self.window._session_list_panel.window(), session_window)
        self.assertIs(self.window.search_input.window(), session_window)
        self.assertIs(self.window.session_tree.window(), session_window)
        self.assertIs(self.window._session_list_mode_btn.window(), session_window)

    def test_session_list_float_window_uses_always_on_top_regular_window_flags(self) -> None:
        self.window._set_session_list_mode("float")
        QApplication.processEvents()

        session_window = self.window._session_list_window
        self.assertIsNotNone(session_window)
        assert session_window is not None
        flags = session_window.windowFlags()
        self.assertEqual(session_window.windowType(), Qt.Window)
        self.assertTrue(bool(flags & Qt.WindowStaysOnTopHint))
        self.assertTrue(bool(flags & Qt.WindowMinimizeButtonHint))
        self.assertTrue(bool(flags & Qt.WindowMaximizeButtonHint))
        self.assertTrue(bool(flags & Qt.WindowCloseButtonHint))

    def test_session_list_float_mode_stops_auto_hide_timer(self) -> None:
        self.window._set_session_list_mode("auto")
        QApplication.processEvents()
        self.assertTrue(self.window._session_list_auto_hide_timer.isActive())

        self.window._set_session_list_mode("float")
        QApplication.processEvents()

        self.assertFalse(self.window._session_list_auto_hide_timer.isActive())

    def test_session_list_selector_can_dock_float_mode_back_to_shown(self) -> None:
        self.window._set_session_list_mode("float")
        QApplication.processEvents()
        action = self.window._session_list_mode_actions["shown"]

        action.trigger()
        QApplication.processEvents()

        self.assertIsNone(self.window._session_list_window)
        self.assertEqual(self.window._session_list_mode, "shown")
        self.assertTrue(self.window._is_session_list_visible())

    def test_session_list_selector_can_dock_float_mode_back_to_auto(self) -> None:
        self.window._set_session_list_mode("float")
        QApplication.processEvents()
        action = self.window._session_list_mode_actions["auto"]

        action.trigger()
        QApplication.processEvents()

        self.assertIsNone(self.window._session_list_window)
        self.assertEqual(self.window._session_list_mode, "auto")
        self.assertFalse(self.window._is_session_list_visible())

    def test_closing_session_list_float_window_returns_to_shown_mode(self) -> None:
        self.window._set_session_list_mode("float")
        QApplication.processEvents()

        session_window = self.window._session_list_window
        self.assertIsNotNone(session_window)
        assert session_window is not None
        session_window.close()
        QApplication.processEvents()

        self.assertIsNone(self.window._session_list_window)
        self.assertEqual(self.window._session_list_mode, "shown")
        self.assertEqual(self.settings_service._settings.session_list_visibility_mode, "shown")
        self.assertTrue(self.window._is_session_list_visible())

    def test_settings_preview_cancel_restores_previous_visual_settings(self) -> None:
        original = AppSettings.from_dict(self.window._settings.to_dict())
        preview = AppSettings.from_dict(original.to_dict())
        preview.accent_color = "#123456"

        preview_state = {"applied": False}
        dialog = MagicMock()
        dialog.exec.return_value = False

        def _dialog_factory(*_args, **kwargs):
            callback = kwargs.get("on_preview_requested")
            self.assertTrue(callable(callback))
            assert callable(callback)
            callback(preview)
            preview_state["applied"] = self.window._settings.accent_color == "#123456"
            return dialog

        with patch("snakesh.ui.main_window.SettingsDialog", side_effect=_dialog_factory):
            self.window._open_settings()

        self.assertTrue(preview_state["applied"])
        self.assertEqual(self.window._settings.accent_color, original.accent_color)
        self.assertEqual(self.settings_service._settings.accent_color, original.accent_color)

    def test_settings_preview_ok_persists_final_settings(self) -> None:
        original = AppSettings.from_dict(self.window._settings.to_dict())
        preview = AppSettings.from_dict(original.to_dict())
        preview.accent_color = "#123456"
        final = AppSettings.from_dict(original.to_dict())
        final.accent_color = "#abcdef"

        dialog = MagicMock()
        dialog.exec.return_value = True
        dialog.build_backend_auth_updates.return_value = {}
        dialog.build_settings.return_value = final

        def _dialog_factory(*_args, **kwargs):
            callback = kwargs.get("on_preview_requested")
            self.assertTrue(callable(callback))
            assert callable(callback)
            callback(preview)
            return dialog

        with patch("snakesh.ui.main_window.SettingsDialog", side_effect=_dialog_factory):
            self.window._open_settings()

        self.assertEqual(self.window._settings.accent_color, "#abcdef")
        self.assertEqual(self.settings_service._settings.accent_color, "#abcdef")

    def test_settings_preview_cancel_broadcasts_preview_then_restore(self) -> None:
        original = AppSettings.from_dict(self.window._settings.to_dict())
        preview = AppSettings.from_dict(original.to_dict())
        preview.accent_color = "#123456"

        dialog = MagicMock()
        dialog.exec.return_value = False

        def _dialog_factory(*_args, **kwargs):
            callback = kwargs.get("on_preview_requested")
            self.assertTrue(callable(callback))
            assert callable(callback)
            callback(preview)
            return dialog

        with (
            patch("snakesh.ui.main_window.SettingsDialog", side_effect=_dialog_factory),
            patch.object(self.window, "_schedule_tool_settings_sync") as mock_broadcast,
        ):
            self.window._open_settings()

        self.assertGreaterEqual(mock_broadcast.call_count, 2)
        self.assertEqual(mock_broadcast.call_args_list[0].args[0].accent_color, "#123456")
        self.assertEqual(mock_broadcast.call_args_list[0].kwargs, {"preview": True})
        self.assertEqual(mock_broadcast.call_args_list[-1].args[0].accent_color, original.accent_color)
        self.assertEqual(mock_broadcast.call_args_list[-1].kwargs, {"preview": True})

    def test_settings_preview_ok_broadcasts_preview_then_committed_settings(self) -> None:
        original = AppSettings.from_dict(self.window._settings.to_dict())
        preview = AppSettings.from_dict(original.to_dict())
        preview.accent_color = "#123456"
        final = AppSettings.from_dict(original.to_dict())
        final.accent_color = "#abcdef"

        dialog = MagicMock()
        dialog.exec.return_value = True
        dialog.build_backend_auth_updates.return_value = {}
        dialog.build_settings.return_value = final

        def _dialog_factory(*_args, **kwargs):
            callback = kwargs.get("on_preview_requested")
            self.assertTrue(callable(callback))
            assert callable(callback)
            callback(preview)
            return dialog

        with (
            patch("snakesh.ui.main_window.SettingsDialog", side_effect=_dialog_factory),
            patch.object(self.window, "_schedule_tool_settings_sync") as mock_broadcast,
        ):
            self.window._open_settings()

        self.assertGreaterEqual(mock_broadcast.call_count, 2)
        self.assertEqual(mock_broadcast.call_args_list[0].args[0].accent_color, "#123456")
        self.assertEqual(mock_broadcast.call_args_list[0].kwargs, {"preview": True})
        self.assertEqual(mock_broadcast.call_args_list[-1].args[0].accent_color, "#abcdef")
        self.assertEqual(mock_broadcast.call_args_list[-1].kwargs, {"preview": False})

    def test_apply_app_settings_updates_fullscreen_shortcut(self) -> None:
        updated = AppSettings.from_dict(self.window._settings.to_dict())
        updated.main_window_fullscreen_shortcut = "Ctrl+Shift+F11"

        self.window._apply_app_settings(
            updated,
            persist=False,
            apply_runtime_side_effects=False,
            show_status=False,
        )

        self.assertIsNotNone(self.window._main_fullscreen_shortcut)
        assert self.window._main_fullscreen_shortcut is not None
        self.assertEqual(
            self.window._main_fullscreen_shortcut.key().toString(),
            "Ctrl+Shift+F11",
        )

    def test_apply_imported_cross_platform_settings_strips_workspace_profile_geometry(self) -> None:
        updated = AppSettings.from_dict(self.window._settings.to_dict())
        updated.workspace_profiles = [
            {
                "id": "profile-1",
                "name": "Imported",
                "snapshot": {
                    "workspace_tree": {"type": "host", "host_key": "host-1"},
                    "window_geometry_b64": "WINDOW",
                    "main_splitter_b64": "SPLITTER",
                    "session_list_mode": "auto",
                    "session_list_visible": False,
                    "session_list_last_width": 540,
                    "session_list_window_geometry_b64": "FLOAT",
                    "session_list_window_screen_name": "Secondary",
                    "session_list_window_screen_serial": "SERIAL-2",
                    "session_list_window_frame_rect": [2200, 180, 420, 700],
                    "detached_windows": [{"tabs": [], "window_geometry_b64": "DETACHED"}],
                },
            }
        ]

        with patch("snakesh.services.settings_service.platform.system", return_value="Linux"):
            self.window._apply_app_settings(
                updated,
                persist=False,
                apply_runtime_side_effects=False,
                show_status=False,
                imported=True,
                source_platform="windows",
            )

        snapshot = self.window._settings.workspace_profiles[0]["snapshot"]
        self.assertNotIn("window_geometry_b64", snapshot)
        self.assertNotIn("main_splitter_b64", snapshot)
        self.assertNotIn("session_list_mode", snapshot)
        self.assertNotIn("session_list_visible", snapshot)
        self.assertNotIn("session_list_last_width", snapshot)
        self.assertNotIn("session_list_window_geometry_b64", snapshot)
        self.assertNotIn("session_list_window_screen_name", snapshot)
        self.assertNotIn("session_list_window_screen_serial", snapshot)
        self.assertNotIn("session_list_window_frame_rect", snapshot)
        detached_window = snapshot["detached_windows"][0]
        self.assertNotIn("window_geometry_b64", detached_window)

    def test_apply_app_settings_preserves_custom_tab_close_button(self) -> None:
        session = _build_session("Host Apply", "sess-close-apply")
        tab = TerminalTab(settings=self.window._settings)
        self.window._add_session_tab(session, tab, "SSH")
        location = self.window._find_widget_location(tab)
        self.assertIsNotNone(location)
        assert location is not None
        host, index = location

        original_button = self._assert_custom_right_close_button(host, index)
        updated = AppSettings.from_dict(self.window._settings.to_dict())
        updated.tab_active_bg = "#0f4c81"
        updated.tab_active_fg = "#f8fafc"
        updated.tab_inactive_bg = "#0f172a"
        updated.tab_inactive_fg = "#cbd5e1"

        self.window._apply_app_settings(
            updated,
            persist=False,
            apply_runtime_side_effects=False,
            show_status=False,
        )
        QApplication.processEvents()

        button = self._assert_custom_right_close_button(host, index, expected_button=original_button)
        self.assertEqual(button.property("sp_widget_id"), id(tab))
        self.assertNotIn("#ff6b6b", button.styleSheet().lower())

    def test_open_settings_cancel_preserves_custom_tab_close_button(self) -> None:
        session = _build_session("Host Settings", "sess-close-settings")
        tab = TerminalTab(settings=self.window._settings)
        self.window._add_session_tab(session, tab, "SSH")
        location = self.window._find_widget_location(tab)
        self.assertIsNotNone(location)
        assert location is not None
        host, index = location

        original_button = self._assert_custom_right_close_button(host, index)
        preview = AppSettings.from_dict(self.window._settings.to_dict())
        preview.tab_active_bg = "#14532d"
        preview.tab_active_fg = "#ecfdf5"
        preview.tab_inactive_bg = "#052e16"
        preview.tab_inactive_fg = "#bbf7d0"
        dialog = MagicMock()
        dialog.exec.return_value = False

        def _dialog_factory(*_args, **kwargs):
            callback = kwargs.get("on_preview_requested")
            self.assertTrue(callable(callback))
            assert callable(callback)
            callback(preview)
            return dialog

        with patch("snakesh.ui.main_window.SettingsDialog", side_effect=_dialog_factory):
            self.window._open_settings()
            QApplication.processEvents()

        button = self._assert_custom_right_close_button(host, index, expected_button=original_button)
        self.assertEqual(button.property("sp_widget_id"), id(tab))

    def test_tab_close_button_restyles_when_active_host_changes(self) -> None:
        updated = AppSettings.from_dict(self.window._settings.to_dict())
        updated.tab_active_bg = "#0f4c81"
        updated.tab_active_fg = "#f8fafc"
        updated.tab_inactive_bg = "#111827"
        updated.tab_inactive_fg = "#94a3b8"
        self.window._apply_app_settings(
            updated,
            persist=False,
            apply_runtime_side_effects=False,
            show_status=False,
        )

        primary_session = _build_session("Primary Host", "sess-close-active")
        secondary_session = _build_session("Secondary Host", "sess-close-inactive")
        primary_tab = TerminalTab(settings=self.window._settings)
        secondary_tab = TerminalTab(settings=self.window._settings)
        self.window._add_session_tab(primary_session, primary_tab, "SSH")
        self.window._add_session_tab(secondary_session, secondary_tab, "SSH")
        self.window._split_tab(self.window.tabs, self.window.tabs.indexOf(secondary_tab), Qt.Horizontal)
        QApplication.processEvents()

        location = self.window._find_widget_location(primary_tab)
        self.assertIsNotNone(location)
        assert location is not None
        host, index = location

        self.window._set_active_tab_host(host)
        QApplication.processEvents()
        button = self._assert_custom_right_close_button(host, index)
        active_signature = str(button.property("sp_close_visual_signature") or "")
        self.assertNotIn("#ff6b6b", button.styleSheet().lower())

        other_host = [entry for entry in self.window._tab_hosts if entry is not host][0]
        self.window._set_active_tab_host(other_host)
        QApplication.processEvents()

        button = self._assert_custom_right_close_button(host, index, expected_button=button)
        inactive_signature = str(button.property("sp_close_visual_signature") or "")
        self.assertNotEqual(active_signature, inactive_signature)

    def test_main_window_fullscreen_toggle_uses_frameless_state(self) -> None:
        original_geometry = self.window.geometry()
        screen = QApplication.primaryScreen()
        screen_geometry = screen.availableGeometry() if screen is not None else QRect(0, 0, 1200, 800)
        target_geometry = QRect(screen_geometry)
        updated = AppSettings.from_dict(self.window._settings.to_dict())
        updated.main_window_hide_controls_in_fullscreen = True
        self.window._apply_app_settings(
            updated,
            persist=False,
            apply_runtime_side_effects=False,
            show_status=False,
        )

        self.assertTrue(self.window._top_controls_widget.isVisible())
        self.assertTrue(self.window._bottom_command_widget.isVisible())

        with patch.object(self.window, "_fullscreen_target_geometry", return_value=target_geometry):
            self.window._enter_main_window_fullscreen()
            QApplication.processEvents()

        self.assertTrue(self.window._frameless_fullscreen_active)
        self.assertTrue(bool(self.window.windowFlags() & Qt.FramelessWindowHint))
        fullscreen_geometry = self.window.geometry()
        self.assertTrue(fullscreen_geometry.isValid())
        self.assertGreater(fullscreen_geometry.width(), 0)
        self.assertGreater(fullscreen_geometry.height(), 0)
        self.assertFalse(self.window._top_controls_widget.isVisible())
        self.assertFalse(self.window._bottom_command_widget.isVisible())

        self.window._exit_main_window_fullscreen()
        QApplication.processEvents()

        self.assertFalse(self.window._frameless_fullscreen_active)
        self.assertFalse(bool(self.window.windowFlags() & Qt.FramelessWindowHint))
        restored_geometry = self.window.geometry()
        self.assertTrue(restored_geometry.isValid())
        self.assertGreater(restored_geometry.width(), 0)
        self.assertGreater(restored_geometry.height(), 0)
        self.assertTrue(self.window._top_controls_widget.isVisible())
        self.assertTrue(self.window._bottom_command_widget.isVisible())

    def test_password_generator_tool_launch_is_detached(self) -> None:
        self.window._open_password_generator()
        QApplication.processEvents()

        self.mock_launch_standalone_tool.assert_called_once_with(
            "password_generator",
            arguments=None,
            env=unittest.mock.ANY,
        )
        self._assert_last_tool_launch_has_placement_env()
        tracked = self.window._tool_processes.get("password_generator")
        self.assertEqual(len(tracked or []), 1)
        self.assertIs(tracked[0], self.tool_launches[0])
        self.assertFalse(hasattr(self.window, "_tool_windows"))

    def test_tool_launch_reuses_existing_instance_without_tracking_new_process(self) -> None:
        self.mock_launch_standalone_tool.side_effect = lambda tool_key, *, arguments=None, cwd=None, env=None: (
            SimpleNamespace(process=None, activated_existing=True)
        )

        process = self.window._open_password_generator()
        QApplication.processEvents()

        self.assertIsNone(process)
        self.mock_launch_standalone_tool.assert_called_once_with(
            "password_generator",
            arguments=None,
            env=unittest.mock.ANY,
        )
        self.assertIsNone(self.window._tool_processes.get("password_generator"))

    def test_scrollback_dialog_uses_regular_window_flags(self) -> None:
        dialog = self._show_scrollback_dialog("alpha\nbeta\n")
        try:
            flags = dialog.windowFlags()
            self.assertEqual(dialog.windowType(), Qt.Window)
            self.assertFalse(bool(flags & Qt.WindowStaysOnTopHint))
            self.assertTrue(bool(flags & Qt.WindowMinimizeButtonHint))
            self.assertTrue(bool(flags & Qt.WindowCloseButtonHint))
        finally:
            dialog.close()
            dialog.deleteLater()
            QApplication.processEvents()

    def test_main_window_close_leaves_detached_tool_process_running(self) -> None:
        self.window._open_diff_tool()
        QApplication.processEvents()

        tracked = self.window._tool_processes.get("diff")
        self.assertEqual(len(tracked or []), 1)
        detached_process = tracked[0]

        closed = self.window.close()
        QApplication.processEvents()

        self.assertTrue(closed)
        self.assertFalse(self.window.isVisible())
        self.assertIsNone(detached_process.poll())

    def test_tools_menu_contains_new_tools_and_help_precedes_about(self) -> None:
        menu = self.window._build_tools_menu()
        try:
            self.assertEqual(menu.style().pixelMetric(QStyle.PixelMetric.PM_SmallIconSize, None, menu), 33)
            labels = [action.text() for action in menu.actions() if action.text()]
            self.assertIn("ASN Lookup", labels)
            self.assertIn("IP Scan", labels)
            self.assertIn("Resource Monitor", labels)
            self.assertEqual(labels[labels.index("About SnakeSh") - 1], "Help")
            self.assertFalse(any(action.isSeparator() for action in menu.actions()))
            about_actions = [action for action in menu.actions() if action.text() == "About SnakeSh"]
            self.assertEqual(len(about_actions), 1)
            self.assertFalse(about_actions[0].icon().isNull())
            about_size = about_actions[0].icon().actualSize(QSize(64, 64))
            self.assertLessEqual(about_size.width(), 33)
            self.assertLessEqual(about_size.height(), 33)
            for entry in TOOL_REGISTRY:
                matching_actions = [action for action in menu.actions() if action.text() == entry.label]
                self.assertEqual(len(matching_actions), 1)
                self.assertFalse(matching_actions[0].icon().isNull())
                actual_size = matching_actions[0].icon().actualSize(QSize(64, 64))
                self.assertLessEqual(actual_size.width(), 33)
                self.assertLessEqual(actual_size.height(), 33)
        finally:
            menu.deleteLater()
            QApplication.processEvents()

    def test_resource_monitor_tool_launch_is_detached(self) -> None:
        self.window._open_resource_monitor_tool()
        QApplication.processEvents()

        self.mock_launch_standalone_tool.assert_called_once_with(
            "resource_monitor",
            arguments=None,
            env=unittest.mock.ANY,
        )
        tracked = self.window._tool_processes.get("resource_monitor")
        self.assertEqual(len(tracked or []), 1)
        self.assertFalse(hasattr(self.window, "_tool_windows"))

    def test_resource_monitor_launches_new_process_each_time(self) -> None:
        self.window._open_resource_monitor_tool()
        self.window._open_resource_monitor_tool()
        QApplication.processEvents()

        tracked = self.window._tool_processes.get("resource_monitor")
        self.assertEqual(self.mock_launch_standalone_tool.call_args_list, [
            self._tool_launch_call("resource_monitor", arguments=None),
            self._tool_launch_call("resource_monitor", arguments=None),
        ])
        self.assertEqual(len(tracked or []), 2)
        self.assertIsNot(tracked[0], tracked[1])

    def test_apply_app_settings_leaves_detached_resource_monitor_processes_tracked(self) -> None:
        self.window._open_resource_monitor_tool()
        QApplication.processEvents()
        tracked_before = list(self.window._tool_processes.get("resource_monitor", []))

        updated = AppSettings.from_dict(self.window._settings.to_dict())
        updated.field_bg = "#f4efe6"
        updated.app_bg_start = "#faf6ee"
        updated.app_bg_end = "#e6ddd0"
        updated.text_color = "#1f2937"
        updated.field_border = "#887a67"

        self.window._apply_app_settings(
            updated,
            persist=False,
            apply_runtime_side_effects=False,
            show_status=False,
        )
        QApplication.processEvents()

        self.assertEqual(self.window._tool_processes.get("resource_monitor"), tracked_before)
        self.assertEqual(self.mock_launch_standalone_tool.call_count, 1)

    def test_prune_tool_processes_removes_exited_resource_monitor_processes(self) -> None:
        self.window._open_resource_monitor_tool()
        self.window._open_resource_monitor_tool()
        QApplication.processEvents()

        tracked = self.window._tool_processes.get("resource_monitor")
        assert tracked is not None
        tracked[0].finish()

        self.window._prune_tool_processes("resource_monitor")

        remaining = self.window._tool_processes.get("resource_monitor")
        self.assertEqual(len(remaining or []), 1)
        self.assertIs(remaining[0], tracked[1])

    def test_asn_and_ip_scan_tools_launch_new_detached_processes(self) -> None:
        asn_first = self.window._open_asn_lookup_tool()
        ip_scan_first = self.window._open_ip_scan_tool()
        asn_second = self.window._open_asn_lookup_tool()
        ip_scan_second = self.window._open_ip_scan_tool()
        QApplication.processEvents()

        self.assertIsNot(asn_first, asn_second)
        self.assertIsNot(ip_scan_first, ip_scan_second)
        self.assertEqual(
            self.mock_launch_standalone_tool.call_args_list[:4],
            [
                self._tool_launch_call("asn_lookup", arguments=None),
                self._tool_launch_call("ip_scan", arguments=None),
                self._tool_launch_call("asn_lookup", arguments=None),
                self._tool_launch_call("ip_scan", arguments=None),
            ],
        )
        self.assertEqual(len(self.window._tool_processes.get("asn_lookup") or []), 2)
        self.assertEqual(len(self.window._tool_processes.get("ip_scan") or []), 2)

    def test_traceroute_tool_launches_new_detached_processes(self) -> None:
        first = self.window._open_traceroute_tool()
        second = self.window._open_traceroute_tool()
        QApplication.processEvents()

        self.assertIsNone(first)
        self.assertIsNone(second)
        self.assertEqual(
            self.mock_launch_standalone_tool.call_args_list[:2],
            [
                self._tool_launch_call("traceroute", arguments=None),
                self._tool_launch_call("traceroute", arguments=None),
            ],
        )
        self.assertEqual(len(self.window._tool_processes.get("traceroute") or []), 2)

    def test_mtu_ping_prefill_launches_ping_detached_with_expected_arguments(self) -> None:
        self.window._prefill_ping_from_mtu(1452, True)
        QApplication.processEvents()

        self.mock_launch_standalone_tool.assert_called_once_with(
            "ping",
            arguments=["--packet-size", "1452", "--ipv6"],
            env=unittest.mock.ANY,
        )
        self.assertEqual(len(self.window._tool_processes.get("ping") or []), 1)

    def test_open_registered_tool_launches_every_registry_tool_detached(self) -> None:
        expected_calls = []
        with patch.object(self.window, "_maybe_prune_web_server_logs") as mock_prune_logs:
            for entry in TOOL_REGISTRY:
                opened = self.window._open_registered_tool(entry.key, activate=False)
                self.assertTrue(opened)
                arguments = [] if entry.key == "ping" else None
                expected_calls.append(self._tool_launch_call(entry.key, arguments=arguments))

        self.assertEqual(self.mock_launch_standalone_tool.call_args_list, expected_calls)
        self.assertEqual(mock_prune_logs.call_count, 1)
        self.assertFalse(hasattr(self.window, "_tool_windows"))

    def test_second_registered_tool_launch_can_activate_existing_instance_without_duplicate_tracking(self) -> None:
        launch_counts: dict[str, int] = {}

        def _launch(tool_key: str, *, arguments=None, cwd=None, env=None):
            _ = cwd, env
            count = launch_counts.get(tool_key, 0)
            launch_counts[tool_key] = count + 1
            if count >= 1:
                return SimpleNamespace(process=None, activated_existing=True)
            process = _StubToolProcess(tool_key, list(arguments or []))
            self.tool_launches.append(process)
            return SimpleNamespace(process=process, activated_existing=False)

        self.mock_launch_standalone_tool.side_effect = _launch

        for entry in TOOL_REGISTRY:
            with self.subTest(tool_key=entry.key):
                self.window._open_registered_tool(entry.key, activate=False)
                self.window._open_registered_tool(entry.key, activate=False)

        self.assertEqual(
            self.mock_launch_standalone_tool.call_args_list,
            [
                self._tool_launch_call(entry.key, arguments=[] if entry.key == "ping" else None)
                for entry in TOOL_REGISTRY
                for _ in range(2)
            ],
        )
        for entry in TOOL_REGISTRY:
            tracked = self.window._tool_processes.get(entry.key)
            self.assertEqual(len(tracked or []), 1)

    def test_prompt_import_startup_file_yes_runs_import(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            import_path = Path(tmp) / "launch-import.ssx"
            import_path.write_text("placeholder", encoding="utf-8")
            with (
                patch("snakesh.ui.main_window.QMessageBox.question", return_value=QMessageBox.Yes),
                patch.object(self.window, "_import_from_settings_menu", return_value=self.window._settings) as mock_import,
            ):
                self.window.prompt_import_startup_file(str(import_path))
            mock_import.assert_called_once_with(import_path=str(import_path))

    def test_prompt_import_startup_file_rejects_unsupported_extension(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            import_path = Path(tmp) / "launch-import.txt"
            import_path.write_text("placeholder", encoding="utf-8")
            with (
                patch("snakesh.ui.main_window.QMessageBox.warning") as mock_warning,
                patch.object(self.window, "_import_from_settings_menu", return_value=self.window._settings) as mock_import,
            ):
                self.window.prompt_import_startup_file(str(import_path))
            mock_warning.assert_called_once()
            mock_import.assert_not_called()

    def test_prompt_import_startup_file_rejects_json_extension(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            import_path = Path(tmp) / "launch-import.json"
            import_path.write_text("{}", encoding="utf-8")
            with (
                patch("snakesh.ui.main_window.QMessageBox.warning") as mock_warning,
                patch.object(self.window, "_import_from_settings_menu", return_value=self.window._settings) as mock_import,
            ):
                self.window.prompt_import_startup_file(str(import_path))
            mock_warning.assert_called_once()
            mock_import.assert_not_called()

    def test_session_list_auto_mode_does_not_hide_while_hovering_splitter_handle(self) -> None:
        self.window._set_session_list_mode("auto")
        QApplication.processEvents()
        self.assertFalse(self.window._is_session_list_visible())

        with patch.object(self.window, "_is_pointer_over_session_list_handle", return_value=True):
            self.window._maybe_auto_hide_session_list()
            QApplication.processEvents()
            self.assertTrue(self.window._is_session_list_visible())

            with patch.object(self.window, "_widget_contains_global_pos", return_value=False):
                self.window._maybe_auto_hide_session_list()
                QApplication.processEvents()
                self.assertTrue(self.window._is_session_list_visible())

    def test_normalize_export_path_forces_ssx_extension(self) -> None:
        no_suffix = MainWindow._normalize_export_path("/tmp/snakesh-export", "SnakeSh Export (*.ssx)")
        json_suffix = MainWindow._normalize_export_path("/tmp/snakesh-export.json", "All files (*)")
        ssx_suffix = MainWindow._normalize_export_path("/tmp/snakesh-export.ssx", "All files (*)")
        self.assertEqual(no_suffix.suffix, ".ssx")
        self.assertEqual(json_suffix.suffix, ".ssx")
        self.assertEqual(ssx_suffix.suffix, ".ssx")

    def test_bulk_edit_selected_sessions_applies_only_changed_fields(self) -> None:
        first = _build_session("Bulk A", "sess-bulk-a")
        second = _build_session("Bulk B", "sess-bulk-b")
        first.notes = "alpha"
        second.notes = "beta"
        self.session_service._sessions = [first, second]

        template = self.window._build_bulk_edit_template([first, second])
        edited = Session.from_dict(template.to_dict())
        edited.x11_forwarding = True
        edited.ssh_keepalive = True

        dialog = MagicMock()
        dialog.exec.return_value = True
        dialog.build_session.return_value = edited
        dialog.password_text.return_value = ""
        dialog.protocol_input = MagicMock()

        with (
            patch("snakesh.ui.main_window.SessionEditorDialog", return_value=dialog),
            patch("snakesh.ui.main_window.QMessageBox.question", return_value=QMessageBox.Yes),
        ):
            self.window._bulk_edit_selected_sessions([first, second])

        updated_first = self.session_service.by_id(first.id)
        updated_second = self.session_service.by_id(second.id)
        self.assertIsNotNone(updated_first)
        self.assertIsNotNone(updated_second)
        assert updated_first is not None
        assert updated_second is not None
        self.assertTrue(updated_first.x11_forwarding)
        self.assertTrue(updated_second.x11_forwarding)
        self.assertTrue(updated_first.ssh_keepalive)
        self.assertTrue(updated_second.ssh_keepalive)
        self.assertEqual(updated_first.notes, "alpha")
        self.assertEqual(updated_second.notes, "beta")

    def test_bulk_edit_template_blanks_mixed_text_fields(self) -> None:
        first = _build_session("Bulk A", "sess-bulk-template-a")
        second = _build_session("Bulk B", "sess-bulk-template-b")
        first.host = "host-a"
        second.host = "host-b"
        first.notes = "alpha"
        second.notes = "beta"
        first.username = "shared-user"
        second.username = "shared-user"

        template = self.window._build_bulk_edit_template([first, second])

        self.assertEqual(template.name, "")
        self.assertEqual(template.host, "")
        self.assertEqual(template.notes, "")
        self.assertEqual(template.username, "shared-user")

    def test_add_current_workspace_profile_generates_unique_name(self) -> None:
        self.window._settings.workspace_profiles = [
            {"id": "profile-existing", "name": "Profile", "snapshot": {"workspace_tree": {"type": "host"}}}
        ]

        with patch("snakesh.ui.main_window.QInputDialog.getText", return_value=("Profile", True)):
            created = self.window._add_current_workspace_profile()

        self.assertTrue(created)
        names = [str(profile.get("name", "")) for profile in self.window._settings.workspace_profiles]
        self.assertIn("Profile", names)
        self.assertIn("Profile 2", names)

    def test_add_current_workspace_profile_captures_open_tools(self) -> None:
        self.window._open_password_generator()
        self.window._open_ping_tool()
        QApplication.processEvents()

        with patch("snakesh.ui.main_window.QInputDialog.getText", return_value=("Tool Profile", True)):
            created = self.window._add_current_workspace_profile()

        self.assertTrue(created)
        profile = self.window._settings.workspace_profiles[-1]
        self.assertEqual(profile["startup_tools"], ["ping", "password_generator"])

    def test_replace_workspace_profile_refreshes_start_tools_on_load(self) -> None:
        self.window._settings.workspace_profiles = [
            {
                "id": "profile-tools",
                "name": "Tools",
                "snapshot": {"workspace_tree": {"type": "host"}},
                "startup_tools": ["help"],
            }
        ]
        self.window._open_ping_tool()
        QApplication.processEvents()

        replaced = self.window._replace_workspace_profile_with_current("profile-tools")

        self.assertTrue(replaced)
        self.assertEqual(self.window._settings.workspace_profiles[0]["startup_tools"], ["ping"])

    def test_capture_open_profile_startup_tools_includes_resource_monitor(self) -> None:
        ping_process = _StubToolProcess("ping")
        resource_monitor_process = _StubToolProcess("resource_monitor")
        self.window._tool_processes["ping"] = [ping_process]
        self.window._tool_processes["resource_monitor"] = [resource_monitor_process]

        captured = self.window._capture_open_profile_startup_tools()

        self.assertEqual(captured, ["resource_monitor", "ping"])

    def test_capture_open_profile_startup_tools_includes_singleton_instances_not_spawned_here(self) -> None:
        with patch("snakesh.ui.main_window.has_active_tool_instance", side_effect=lambda key: key == "help"):
            captured = self.window._capture_open_profile_startup_tools()

        self.assertEqual(captured, ["help"])

    def test_open_profile_startup_tools_includes_resource_monitor(self) -> None:
        with patch.object(self.window, "_open_registered_tool") as mock_open:
            self.window._open_profile_startup_tools(["resource_monitor", "ping", "help"])

        self.assertEqual(
            mock_open.call_args_list,
            [
                unittest.mock.call("resource_monitor", activate=False),
                unittest.mock.call("ping", activate=False),
                unittest.mock.call("help", activate=False),
            ],
        )

    def test_open_profile_startup_tools_uses_registry_order_and_detached_launches(self) -> None:
        requested_keys = list(reversed([entry.key for entry in TOOL_REGISTRY]))

        self.window._open_profile_startup_tools(requested_keys)
        QApplication.processEvents()

        self.assertEqual(
            self.mock_launch_standalone_tool.call_args_list,
            [
                self._tool_launch_call(entry.key, arguments=[] if entry.key == "ping" else None)
                for entry in TOOL_REGISTRY
            ],
        )

    def test_apply_workspace_profile_restores_split_layout_and_custom_tab_title(self) -> None:
        first = _build_session("Session A", "sess-profile-a")
        second = _build_session("Session B", "sess-profile-b")
        self.session_service._sessions = [first, second]

        first_widget = QWidget()
        second_widget = QWidget()
        self.window._add_session_tab(first, first_widget, "SSH")
        self.window._add_session_tab(second, second_widget, "SSH")
        self.window._split_tab(self.window.tabs, 2, Qt.Horizontal)
        QApplication.processEvents()

        second_host = [host for host in self.window._tab_hosts if host is not self.window.tabs][0]
        self.window._set_active_tab_host(second_host)
        second_widget.setProperty("tab_title_custom", "Pinned B")
        self.window._refresh_session_instance_titles()

        snapshot = self.window._capture_workspace_profile_snapshot()
        self.window._settings.workspace_profiles = [
            {"id": "profile-layout", "name": "Layout", "snapshot": snapshot}
        ]

        def _fake_open(kind: str, session: Session) -> None:
            self.window._add_session_tab(session, QWidget(), kind)

        with patch.object(self.window, "_open_session_for_profile_tab", side_effect=_fake_open):
            applied = self.window._apply_workspace_profile("profile-layout")

        self.assertTrue(applied)
        self.assertEqual(len(self.window._tab_hosts), 2)
        root = self.window._workspace_root_widget()
        self.assertIsInstance(root, QSplitter)
        assert isinstance(root, QSplitter)
        self.assertEqual(root.orientation(), Qt.Horizontal)
        self.assertIsNot(self.window._active_tab_host, self.window.tabs)
        self.assertEqual(len(self.window._session_tab_locations()), 2)
        self.assertIn(
            "Pinned B",
            [str(widget.property("tab_title_custom") or "") for _host, _index, widget in self.window._session_tab_locations()],
        )

    def test_apply_workspace_profile_opens_configured_tools_additively(self) -> None:
        snapshot = {"workspace_tree": {"type": "host", "host_key": "host-1", "is_primary": True, "tabs": []}}
        self.window._open_help_tool()
        QApplication.processEvents()
        self.assertEqual(len(self.window._tool_processes.get("help") or []), 1)

        self.window._settings.workspace_profiles = [
            {
                "id": "profile-tools",
                "name": "Tool Autoload",
                "snapshot": snapshot,
                "startup_tools": ["password_generator", "ping"],
            }
        ]

        applied = self.window._apply_workspace_profile("profile-tools")

        self.assertTrue(applied)
        self.assertEqual(len(self.window._tool_processes.get("help") or []), 1)
        self.assertEqual(len(self.window._tool_processes.get("ping") or []), 1)
        self.assertEqual(len(self.window._tool_processes.get("password_generator") or []), 1)
        self.assertEqual(
            self.mock_launch_standalone_tool.call_args_list[-2:],
            [
                self._tool_launch_call("ping", arguments=[]),
                self._tool_launch_call("password_generator", arguments=None),
            ],
        )

    def test_apply_workspace_profile_launches_existing_start_tool_again_without_activation(self) -> None:
        snapshot = {"workspace_tree": {"type": "host", "host_key": "host-1", "is_primary": True, "tabs": []}}
        first_ping_process = self.window._open_ping_tool()
        QApplication.processEvents()

        self.window._settings.workspace_profiles = [
            {
                "id": "profile-ping",
                "name": "Ping",
                "snapshot": snapshot,
                "startup_tools": ["ping"],
            }
        ]

        with patch.object(self.window, "_open_registered_tool", wraps=self.window._open_registered_tool) as mock_open:
            applied = self.window._apply_workspace_profile("profile-ping")

        self.assertTrue(applied)
        tracked = self.window._tool_processes.get("ping")
        self.assertEqual(len(tracked or []), 2)
        self.assertIs(tracked[0], first_ping_process)
        self.assertIsNot(tracked[0], tracked[1])
        mock_open.assert_any_call("ping", activate=False)

    def test_detach_and_reattach_supported_terminal_tab(self) -> None:
        session = _build_session("Detach Me", "sess-detach-ssh")
        self.session_service._sessions = [session]
        widget = QWidget()
        self.window._add_session_tab(session, widget, "SSH")
        location = self.window._find_widget_location(widget)
        self.assertIsNotNone(location)
        assert location is not None

        self.window._detach_session_tab(widget, location[0], location[1])
        QApplication.processEvents()

        detached_location = self.window._find_widget_location(widget)
        self.assertIsNotNone(detached_location)
        assert detached_location is not None
        self.assertTrue(detached_location[0].property("is_detached_host"))
        self.assertEqual(len(self.window._detached_window_by_host), 1)

        self.window._reattach_session_tab(widget, detached_location[0], detached_location[1])
        QApplication.processEvents()

        reattached_location = self.window._find_widget_location(widget)
        self.assertIsNotNone(reattached_location)
        assert reattached_location is not None
        self.assertIs(reattached_location[0], self.window.tabs)
        self.assertEqual(len(self.window._detached_window_by_host), 0)

    def test_detach_and_reattach_local_terminal_tab(self) -> None:
        session = _build_session("Local Shell", "sess-detach-local")
        self.session_service._sessions = [session]
        widget = QWidget()
        self.window._add_session_tab(session, widget, "LOCAL")
        location = self.window._find_widget_location(widget)
        self.assertIsNotNone(location)
        assert location is not None

        self.window._detach_session_tab(widget, location[0], location[1])
        QApplication.processEvents()

        detached_location = self.window._find_widget_location(widget)
        self.assertIsNotNone(detached_location)
        assert detached_location is not None
        self.assertTrue(detached_location[0].property("is_detached_host"))

        self.window._reattach_session_tab(widget, detached_location[0], detached_location[1])
        QApplication.processEvents()

        reattached_location = self.window._find_widget_location(widget)
        self.assertIsNotNone(reattached_location)
        assert reattached_location is not None
        self.assertIs(reattached_location[0], self.window.tabs)

    def test_closing_detached_window_reattaches_tabs(self) -> None:
        session = _build_session("Detach Close", "sess-detach-close")
        self.session_service._sessions = [session]
        widget = QWidget()
        self.window._add_session_tab(session, widget, "SSH")
        location = self.window._find_widget_location(widget)
        self.assertIsNotNone(location)
        assert location is not None

        self.window._detach_session_tab(widget, location[0], location[1])
        QApplication.processEvents()
        self.assertEqual(len(self.window._detached_window_by_host), 1)
        detached_window = next(iter(self.window._detached_window_by_host.values()))

        detached_window.close()
        QApplication.processEvents()

        final_location = self.window._find_widget_location(widget)
        self.assertIsNotNone(final_location)
        assert final_location is not None
        self.assertIs(final_location[0], self.window.tabs)
        self.assertEqual(len(self.window._detached_window_by_host), 0)

    def test_workspace_profile_restores_detached_windows(self) -> None:
        session = _build_session("Detached Profile", "sess-detached-profile")
        self.session_service._sessions = [session]
        widget = QWidget()
        self.window._add_session_tab(session, widget, "SSH")
        location = self.window._find_widget_location(widget)
        self.assertIsNotNone(location)
        assert location is not None

        self.window._detach_session_tab(widget, location[0], location[1])
        QApplication.processEvents()
        detached_window = next(iter(self.window._detached_window_by_host.values()))
        detached_window.setGeometry(220, 180, 640, 420)

        snapshot = self.window._capture_workspace_profile_snapshot()
        self.assertEqual(len(snapshot.get("detached_windows", [])), 1)
        self.window._settings.workspace_profiles = [
            {"id": "profile-detached", "name": "Detached", "snapshot": snapshot}
        ]

        def _fake_open(kind: str, session: Session) -> None:
            self.window._add_session_tab(session, QWidget(), kind)

        with patch.object(self.window, "_open_session_for_profile_tab", side_effect=_fake_open):
            applied = self.window._apply_workspace_profile("profile-detached")

        self.assertTrue(applied)
        self.assertEqual(len(self.window._detached_window_by_host), 1)
        restored_window = next(iter(self.window._detached_window_by_host.values()))
        self.assertGreater(restored_window.width(), 100)
        self.assertGreater(restored_window.height(), 100)

    def test_window_placement_resolves_matching_secondary_screen_by_serial(self) -> None:
        primary = _FakeScreen(name="Primary", serial="SER-A", available=QRect(0, 0, 1920, 1080))
        secondary = _FakeScreen(name="Secondary", serial="SER-B", available=QRect(1920, 0, 1920, 1080))
        placement = main_window._window_placement_from_payload(
            geometry_b64=bytes(QByteArray(b"MAIN").toBase64()).decode("ascii"),
            screen_name="Secondary",
            screen_serial="SER-B",
            frame_rect=[2100, 140, 900, 700],
        )

        assert placement is not None
        with (
            patch("snakesh.ui.main_window.QApplication.screens", return_value=[primary, secondary]),
            patch("snakesh.ui.main_window.QApplication.screenAt", return_value=None),
            patch("snakesh.ui.main_window.QApplication.primaryScreen", return_value=primary),
        ):
            resolved = main_window._resolve_screen_for_window_placement(placement)

        self.assertIs(resolved, secondary)

    def test_window_placement_falls_back_to_primary_screen_when_saved_screen_missing(self) -> None:
        primary = _FakeScreen(name="Primary", serial="SER-A", available=QRect(0, 0, 1920, 1080))
        target = _WindowPlacementTargetProbe(
            handle=_FakeWindowHandle(),
            frame=QRect(2500, 80, 900, 700),
        )
        placement = main_window._window_placement_from_payload(
            geometry_b64=bytes(QByteArray(b"MAIN").toBase64()).decode("ascii"),
            screen_name="Missing Screen",
            screen_serial="SER-Z",
            frame_rect=[2500, 80, 900, 700],
        )

        assert placement is not None
        with (
            patch("snakesh.ui.main_window.QApplication.screens", return_value=[primary]),
            patch("snakesh.ui.main_window.QApplication.screenAt", return_value=None),
            patch("snakesh.ui.main_window.QApplication.primaryScreen", return_value=primary),
        ):
            applied = main_window._apply_window_placement(target, placement)

        self.assertTrue(applied)
        assert target._handle is not None
        self.assertIs(target._handle.screen_set, primary)
        self.assertEqual(bytes(target.restored_geometry or QByteArray()), b"MAIN")

    def test_window_placement_can_be_deferred_until_handle_exists(self) -> None:
        secondary = _FakeScreen(name="Secondary", serial="SER-B", available=QRect(1920, 0, 1920, 1080))
        target = _WindowPlacementTargetProbe(handle=None, frame=QRect(2100, 100, 900, 700))
        placement = main_window._window_placement_from_payload(
            geometry_b64=bytes(QByteArray(b"MAIN").toBase64()).decode("ascii"),
            screen_name="Secondary",
            screen_serial="SER-B",
            frame_rect=[2100, 100, 900, 700],
        )

        assert placement is not None
        self.assertFalse(main_window._restore_or_defer_window_placement(target, placement))
        self.assertIs(target._pending_window_placement, placement)

        target._handle = _FakeWindowHandle()
        with (
            patch("snakesh.ui.main_window.QApplication.screens", return_value=[secondary]),
            patch("snakesh.ui.main_window.QApplication.screenAt", return_value=secondary),
            patch("snakesh.ui.main_window.QApplication.primaryScreen", return_value=secondary),
        ):
            applied = main_window._apply_pending_window_placement(target)

        self.assertTrue(applied)
        self.assertIsNone(target._pending_window_placement)
        assert target._handle is not None
        self.assertIs(target._handle.screen_set, secondary)
        self.assertEqual(bytes(target.restored_geometry or QByteArray()), b"MAIN")

    def test_restore_detached_windows_from_profile_uses_saved_window_placement_metadata(self) -> None:
        session = _build_session("Detached Restore", "sess-detached-restore")
        self.session_service._sessions = [session]
        snapshot = {
            "detached_windows": [
                {
                    "tabs": [{"kind": "SSH", "session_id": session.id}],
                    "current_session_index": 0,
                    "window_geometry_b64": bytes(QByteArray(b"DETACHED").toBase64()).decode("ascii"),
                    "window_screen_name": "Secondary",
                    "window_screen_serial": "SER-B",
                    "window_frame_rect": [2100, 140, 640, 420],
                }
            ]
        }

        def _fake_open(kind: str, restored_session: Session) -> None:
            self.window._add_session_tab(restored_session, QWidget(), kind)

        with (
            patch.object(self.window, "_open_session_for_profile_tab", side_effect=_fake_open),
            patch("snakesh.ui.main_window._restore_or_defer_window_placement") as mock_restore,
        ):
            failures = self.window._restore_detached_windows_from_profile(snapshot)

        self.assertEqual(failures, [])
        self.assertEqual(mock_restore.call_count, 1)
        placement = mock_restore.call_args.args[1]
        self.assertEqual(placement.screen_name, "Secondary")
        self.assertEqual(placement.screen_serial, "SER-B")
        self.assertEqual(main_window._frame_rect_to_list(placement.frame_rect), [2100, 140, 640, 420])
        self.assertEqual(bytes(placement.geometry or QByteArray()), b"DETACHED")

    def test_default_profile_loader_uses_configured_default_profile(self) -> None:
        snapshot = {"workspace_tree": {"type": "host", "host_key": "host-1", "is_primary": True, "tabs": []}}
        self.window._settings.workspace_profiles = [{"id": "profile-default", "name": "Default", "snapshot": snapshot}]
        self.window._settings.default_workspace_profile_id = "profile-default"

        with patch.object(self.window, "_apply_workspace_profile", return_value=True) as mock_apply:
            self.window._maybe_apply_default_workspace_profile_on_startup()

        mock_apply.assert_called_once_with("profile-default", startup=True)

    def test_safe_mode_skips_default_profile_restore_on_startup(self) -> None:
        settings_service = _StubSettingsService()
        settings_service._settings.workspace_profiles = [
            {
                "id": "profile-default",
                "name": "Default",
                "snapshot": {"workspace_tree": {"type": "host"}},
                "startup_tools": ["ping", "help"],
            }
        ]
        settings_service._settings.default_workspace_profile_id = "profile-default"

        safe_window = MainWindow(self.session_service, settings_service, safe_mode=True)
        safe_window.show()
        QApplication.processEvents()
        try:
            with patch.object(safe_window, "_maybe_apply_default_workspace_profile_on_startup") as mock_restore:
                safe_window._handle_workspace_startup_restore()

            mock_restore.assert_not_called()
            self.assertEqual(settings_service._settings.default_workspace_profile_id, "profile-default")
            self.assertIn("Safe mode enabled.", safe_window.statusBar().currentMessage())
            self.assertFalse(hasattr(safe_window, "_tool_windows"))
            self.assertEqual(safe_window._tool_processes, {})
        finally:
            safe_window.close()
            safe_window.deleteLater()
            QApplication.processEvents()

    def test_profile_manager_dialog_updates_start_tools_summary_and_button_state(self) -> None:
        dialog = ProfileManagerDialog(self.window)
        try:
            dialog.set_profiles([], default_profile_id="")
            self.assertFalse(dialog.edit_startup_tools_btn.isEnabled())
            self.assertEqual(dialog.startup_tools_summary.text(), "Start tools on load: None")

            dialog.set_profiles(
                [
                    {
                        "id": "profile-1",
                        "name": "Ops",
                        "snapshot": {"workspace_tree": {"type": "host"}},
                        "startup_tools": ["help", "ping"],
                    }
                ],
                default_profile_id="",
            )

            self.assertTrue(dialog.edit_startup_tools_btn.isEnabled())
            self.assertEqual(dialog.startup_tools_summary.text(), "Start tools on load: Ping, Help")
        finally:
            dialog.close()
            dialog.deleteLater()
            QApplication.processEvents()

    def test_edit_workspace_profile_startup_tools_persists_and_updates_manager_summary(self) -> None:
        self.window._settings.workspace_profiles = [
            {
                "id": "profile-1",
                "name": "Ops",
                "snapshot": {"workspace_tree": {"type": "host"}},
            }
        ]
        tool_dialog = MagicMock()
        tool_dialog.exec.return_value = QDialog.Accepted
        tool_dialog.selected_tool_keys.return_value = ["help", "ping"]

        with patch("snakesh.ui.main_window.ProfileToolSelectionDialog", return_value=tool_dialog):
            updated = self.window._edit_workspace_profile_startup_tools("profile-1")

        self.assertTrue(updated)
        tool_dialog.set_selected_tool_keys.assert_called_once_with([])
        self.assertEqual(self.window._settings.workspace_profiles[0]["startup_tools"], ["ping", "help"])

        dialog = ProfileManagerDialog(self.window)
        try:
            dialog.set_profiles(self.window._workspace_profile_entries(), default_profile_id="")
            self.assertEqual(dialog.startup_tools_summary.text(), "Start tools on load: Ping, Help")
        finally:
            dialog.close()
            dialog.deleteLater()
            QApplication.processEvents()

    def test_password_generator_launches_new_detached_process_on_each_request(self) -> None:
        self.window._open_password_generator()
        self.window._open_password_generator()
        QApplication.processEvents()

        self.assertEqual(
            self.mock_launch_standalone_tool.call_args_list[-2:],
            [
                self._tool_launch_call("password_generator", arguments=None),
                self._tool_launch_call("password_generator", arguments=None),
            ],
        )
        self.assertEqual(len(self.window._tool_processes.get("password_generator") or []), 2)

    def test_workspace_profile_restores_session_list_visibility_mode(self) -> None:
        self.window._set_session_list_mode("auto")
        snapshot = self.window._capture_workspace_profile_snapshot()
        self.window._settings.workspace_profiles = [
            {"id": "profile-session-list", "name": "Session List Auto", "snapshot": snapshot}
        ]

        self.window._set_session_list_mode("shown")
        applied = self.window._apply_workspace_profile("profile-session-list")

        self.assertTrue(applied)
        self.assertEqual(self.window._session_list_mode, "auto")
        self.assertFalse(self.window._is_session_list_visible())

    def test_workspace_profile_restores_floating_session_list_window_and_placement(self) -> None:
        self.window._set_session_list_mode("float")
        QApplication.processEvents()
        session_window = self.window._session_list_window
        self.assertIsNotNone(session_window)
        assert session_window is not None
        session_window.setGeometry(260, 190, 430, 710)

        snapshot = self.window._capture_workspace_profile_snapshot()
        self.window._settings.workspace_profiles = [
            {"id": "profile-session-list-float", "name": "Session List Float", "snapshot": snapshot}
        ]

        self.window._set_session_list_mode("shown")
        QApplication.processEvents()

        restored_calls: list[main_window._WindowPlacement] = []
        original_restore = main_window._restore_or_defer_window_placement

        def _track_restore(target, placement):
            if isinstance(target, main_window.SessionListWindow) and placement is not None:
                restored_calls.append(placement)
            return original_restore(target, placement)

        with patch("snakesh.ui.main_window._restore_or_defer_window_placement", side_effect=_track_restore):
            applied = self.window._apply_workspace_profile("profile-session-list-float")

        self.assertTrue(applied)
        self.assertEqual(self.window._session_list_mode, "float")
        self.assertIsNotNone(self.window._session_list_window)
        self.assertEqual(len(restored_calls), 1)
        self.assertEqual(
            main_window._frame_rect_to_list(restored_calls[0].frame_rect),
            snapshot["session_list_window_frame_rect"],
        )

    def test_startup_restores_floating_session_list_window_and_saved_placement(self) -> None:
        restored_service = _StubSettingsService()
        restored_service._settings.session_list_visibility_mode = "float"
        restored_service._settings.session_list_window_geometry_b64 = bytes(QByteArray(b"SESSION_LIST").toBase64()).decode(
            "ascii"
        )
        restored_service._settings.session_list_window_screen_name = "Secondary"
        restored_service._settings.session_list_window_screen_serial = "SER-B"
        restored_service._settings.session_list_window_frame_rect = [2400, 160, 420, 700]

        restored_calls: list[main_window._WindowPlacement] = []
        original_restore = main_window._restore_or_defer_window_placement

        def _track_restore(target, placement):
            if isinstance(target, main_window.SessionListWindow) and placement is not None:
                restored_calls.append(placement)
            return original_restore(target, placement)

        with patch("snakesh.ui.main_window._restore_or_defer_window_placement", side_effect=_track_restore):
            restored = MainWindow(self.session_service, restored_service)
            restored.show()
            QApplication.processEvents()
        try:
            self.assertEqual(restored._session_list_mode, "float")
            self.assertIsNotNone(restored._session_list_window)
            self.assertEqual(len(restored_calls), 1)
            self.assertEqual(restored_calls[0].screen_name, "Secondary")
            self.assertEqual(restored_calls[0].screen_serial, "SER-B")
            self.assertEqual(main_window._frame_rect_to_list(restored_calls[0].frame_rect), [2400, 160, 420, 700])
            self.assertEqual(bytes(restored_calls[0].geometry or QByteArray()), b"SESSION_LIST")
        finally:
            restored.close()
            restored.deleteLater()
            QApplication.processEvents()

    def test_rename_tab_changes_only_live_tab_title(self) -> None:
        session = _build_session("Original Name", "sess-rename-tab")
        self.session_service._sessions = [session]
        widget = QWidget()
        self.window._add_session_tab(session, widget, "SSH")
        location = self.window._find_widget_location(widget)
        self.assertIsNotNone(location)
        assert location is not None
        host, index = location

        with patch("snakesh.ui.main_window.QInputDialog.getText", return_value=("Temporary Label", True)):
            self.window._rename_tab_for_life_of_session(widget)

        self.assertEqual(host.tabText(index), "Temporary Label")
        self.assertEqual(session.name, "Original Name")

    def test_reconnect_session_tab_reuses_existing_ssh_terminal_tab(self) -> None:
        session = _build_session("Reconnect Host", "sess-reconnect")
        self.session_service._sessions = [session]
        tab = TerminalTab(settings=self.window._settings)
        tab._mark_connection_closed(True)
        self.window._add_session_tab(session, tab, "SSH")
        location = self.window._find_widget_location(tab)
        self.assertIsNotNone(location)
        assert location is not None
        host, index = location

        with (
            patch.object(self.window, "_close_tab_in_host") as mock_close,
            patch.object(self.window, "_open_ssh_tab") as mock_open_ssh,
        ):
            self.window._reconnect_session_tab(tab, host, index)

        mock_close.assert_not_called()
        mock_open_ssh.assert_called_once_with(session, existing_tab=tab)

    def test_reconnect_session_tab_reuses_existing_local_shell_tab(self) -> None:
        session = Session(
            id="sess-local-reconnect",
            name="Local Shell",
            host="localhost",
            protocol=Protocol.SSH,
            port=22,
            username="tester",
        )
        tab = TerminalTab(settings=self.window._settings)
        tab._windows_local_shell_start_program = "/bin/sh"
        tab._windows_local_shell_start_arguments = ["-i"]
        tab._windows_local_shell_start_working_directory = "/tmp"
        tab._mark_connection_closed(True)
        self.window._add_session_tab(session, tab, "LOCAL")
        location = self.window._find_widget_location(tab)
        self.assertIsNotNone(location)
        assert location is not None
        host, index = location

        with patch.object(self.window, "_open_local_shell_tab", return_value=True) as mock_open_local:
            self.window._reconnect_session_tab(tab, host, index)

        mock_open_local.assert_called_once_with(existing_tab=tab)

    def test_reconnect_session_tab_keeps_remote_viewer_reconnect_behavior(self) -> None:
        session = _build_session("Reconnect Viewer", "sess-reconnect-viewer")
        session.protocol = Protocol.RDP
        session.port = 3389
        self.session_service._sessions = [session]

        def detached_builder() -> tuple[list[str], str, dict[str, str] | None]:
            return ["viewer"], "Viewer", None

        tab = RemoteViewerTab(
            session=session,
            protocol_name="RDP",
            detached_command_builder=detached_builder,
        )
        tab.setProperty("remote_viewer_closed", True)
        self.window._add_session_tab(session, tab, "RDP")
        location = self.window._find_widget_location(tab)
        self.assertIsNotNone(location)
        assert location is not None
        host, index = location

        with (
            patch.object(self.window, "_close_tab_in_host") as mock_close,
            patch.object(self.window, "_connect_session") as mock_connect,
        ):
            self.window._reconnect_session_tab(tab, host, index)

        mock_close.assert_called_once_with(host, index)
        mock_connect.assert_called_once_with(session)

    def test_edit_specific_session_passes_password_loader_to_editor(self) -> None:
        session = _build_session("Editable Host", "sess-edit-password-loader")
        self.session_service._sessions = [session]
        dialog = MagicMock()
        dialog.exec.return_value = False
        with patch("snakesh.ui.main_window.SessionEditorDialog", return_value=dialog) as mock_dialog:
            self.window._edit_specific_session(session)

        _args, kwargs = mock_dialog.call_args
        loader = kwargs.get("password_loader")
        self.assertTrue(callable(loader))
        self.assertEqual(getattr(loader, "__self__", None), self.window._credential_service)
        self.assertEqual(getattr(getattr(loader, "__func__", None), "__name__", ""), "load_password")

    def test_session_actions_menu_includes_quick_connect_after_add(self) -> None:
        labels = [action.text() for action in self.window._session_actions_menu.actions() if action.text()]
        self.assertGreaterEqual(len(labels), 5)
        self.assertEqual(labels[0], "Add Session...")
        self.assertEqual(labels[1], "Quick Connect...")

    def test_quick_connect_uses_editor_without_persisting_session(self) -> None:
        quick_session = Session(
            id="quick-connect-ssh",
            name="",
            host="quick.example",
            protocol=Protocol.SSH,
            port=22,
            username="tester",
        )
        dialog = MagicMock()
        dialog.exec.return_value = True
        dialog.build_session.return_value = quick_session
        dialog.password_text.return_value = ""

        with (
            patch("snakesh.ui.main_window.SessionEditorDialog", return_value=dialog) as mock_dialog,
            patch.object(self.window, "_connect_session") as mock_connect,
        ):
            self.window._quick_connect_session()

        _args, kwargs = mock_dialog.call_args
        self.assertTrue(kwargs.get("quick_connect"))
        self.assertEqual(self.session_service.all(), [])
        mock_connect.assert_called_once_with(
            quick_session,
            password_override=None,
            runtime_only=True,
        )

    def test_quick_connect_passes_typed_password_override(self) -> None:
        quick_session = Session(
            id="quick-connect-password",
            name="",
            host="secure.example",
            protocol=Protocol.SSH,
            port=22,
            username="tester",
        )
        dialog = MagicMock()
        dialog.exec.return_value = True
        dialog.build_session.return_value = quick_session
        dialog.password_text.return_value = "temp-secret"

        with patch.object(self.window, "_connect_session") as mock_connect:
            with patch("snakesh.ui.main_window.SessionEditorDialog", return_value=dialog):
                self.window._quick_connect_session()

        mock_connect.assert_called_once_with(
            quick_session,
            password_override="temp-secret",
            runtime_only=True,
        )

    def test_password_prompt_layout_keeps_controls_visible_at_minimum_size(self) -> None:
        session = Session(
            id="quick-connect-password-retry",
            name="",
            host="192.168.225.112",
            protocol=Protocol.SSH,
            port=22,
            username="bwadmin",
            save_password=True,
        )
        dialog, password_input, remember_check = self.window._build_password_prompt_dialog(
            session,
            allow_save=True,
        )
        try:
            dialog.resize(dialog.minimumSizeHint())
            dialog.show()
            QApplication.processEvents()

            self.assertIsInstance(password_input, QLineEdit)
            self.assertIsInstance(remember_check, QCheckBox)
            self.assertTrue(password_input.isVisible())
            self.assertTrue(remember_check.isVisible())
            button_box = dialog.findChild(QDialogButtonBox)
            self.assertIsNotNone(button_box)
            assert button_box is not None

            contents = dialog.contentsRect()
            for widget in (password_input, remember_check, button_box):
                top_left = widget.mapTo(dialog, widget.rect().topLeft())
                bottom_right = widget.mapTo(dialog, widget.rect().bottomRight())
                self.assertGreater(widget.width(), 0)
                self.assertGreater(widget.height(), 0)
                self.assertGreaterEqual(top_left.x(), contents.left())
                self.assertGreaterEqual(top_left.y(), contents.top())
                self.assertLessEqual(bottom_right.x(), contents.right())
                self.assertLessEqual(bottom_right.y(), contents.bottom())
            self.assertGreaterEqual(password_input.width(), 320)
        finally:
            dialog.close()
            dialog.deleteLater()
            QApplication.processEvents()

    def test_close_tab_defers_when_terminal_shutdown_not_complete(self) -> None:
        session = _build_session("Slow Shutdown", "sess-slow-shutdown")
        tab = TerminalTab(settings=self.window._settings)
        self.window._add_session_tab(session, tab, "SSH")
        location = self.window._find_widget_location(tab)
        self.assertIsNotNone(location)
        assert location is not None
        host, index = location

        with patch.object(tab, "shutdown", return_value=False):
            self.window._close_tab_in_host(host, index)

        self.assertGreaterEqual(host.indexOf(tab), 0)

    def test_active_terminal_close_button_prompts_and_cancel_keeps_tab_open(self) -> None:
        session = _build_session("Prompted Terminal", "sess-close-button-no")
        tab = TerminalTab(settings=self.window._settings)
        self.window._add_session_tab(session, tab, "SSH")
        location = self.window._find_widget_location(tab)
        self.assertIsNotNone(location)
        assert location is not None
        host, index = location
        button = self._assert_custom_right_close_button(host, index)

        with (
            patch.object(tab, "has_active_connection", return_value=True),
            patch.object(tab, "shutdown", return_value=True) as mock_shutdown,
            patch("snakesh.ui.main_window.QMessageBox.question", return_value=QMessageBox.No) as mock_question,
        ):
            button.click()
            QApplication.processEvents()

        self.assertGreaterEqual(host.indexOf(tab), 0)
        prompt_text = str(mock_question.call_args.args[2])
        self.assertIn("Prompted Terminal", prompt_text)
        self.assertIn("active session", prompt_text.lower())
        mock_question.assert_called_once()
        mock_shutdown.assert_not_called()

    def test_active_terminal_close_button_prompts_and_accept_closes_tab(self) -> None:
        session = _build_session("Prompted Terminal", "sess-close-button-yes")
        tab = TerminalTab(settings=self.window._settings)
        self.window._add_session_tab(session, tab, "SSH")
        location = self.window._find_widget_location(tab)
        self.assertIsNotNone(location)
        assert location is not None
        host, index = location
        button = self._assert_custom_right_close_button(host, index)

        with (
            patch.object(tab, "has_active_connection", return_value=True),
            patch.object(tab, "shutdown", return_value=True) as mock_shutdown,
            patch("snakesh.ui.main_window.QMessageBox.question", return_value=QMessageBox.Yes) as mock_question,
        ):
            button.click()
            QApplication.processEvents()

        self.assertEqual(host.indexOf(tab), -1)
        mock_question.assert_called_once()
        mock_shutdown.assert_called_once()

    def test_active_local_shell_tab_close_request_prompts_before_closing(self) -> None:
        session = Session(
            name="Local Shell",
            host="localhost",
            protocol=Protocol.SSH,
            port=22,
            username="tester",
        )
        tab = TerminalTab(settings=self.window._settings)
        self.window._add_session_tab(session, tab, "LOCAL")
        location = self.window._find_widget_location(tab)
        self.assertIsNotNone(location)
        assert location is not None
        host, index = location

        with (
            patch.object(tab, "has_active_connection", return_value=True),
            patch.object(tab, "shutdown", return_value=True) as mock_shutdown,
            patch("snakesh.ui.main_window.QMessageBox.question", return_value=QMessageBox.Yes) as mock_question,
        ):
            host.tabCloseRequested.emit(index)
            QApplication.processEvents()

        self.assertEqual(host.indexOf(tab), -1)
        mock_question.assert_called_once()
        mock_shutdown.assert_called_once()

    def test_active_remote_viewer_context_menu_close_prompts_before_closing(self) -> None:
        session = _build_session("Viewer Host", "sess-close-remote-viewer")
        session.protocol = Protocol.RDP
        session.port = 3389

        def detached_builder() -> tuple[list[str], str, dict[str, str] | None]:
            return ["viewer"], "Viewer", None

        tab = RemoteViewerTab(
            session=session,
            protocol_name="RDP",
            detached_command_builder=detached_builder,
        )
        self.window._add_session_tab(session, tab, "RDP")
        location = self.window._find_widget_location(tab)
        self.assertIsNotNone(location)
        assert location is not None
        host, index = location

        with (
            patch.object(tab, "has_active_connection", return_value=True),
            patch.object(tab, "shutdown") as mock_shutdown,
            patch.object(
                self.window,
                "_exec_menu",
                side_effect=lambda menu, _pos: next(
                    action for action in menu.actions() if action.text() == "Close Tab"
                ),
            ),
            patch("snakesh.ui.main_window.QMessageBox.question", return_value=QMessageBox.Yes) as mock_question,
        ):
            self.window._show_tab_context_menu(host, index, None)
            QApplication.processEvents()

        self.assertEqual(host.indexOf(tab), -1)
        mock_question.assert_called_once()
        mock_shutdown.assert_called_once()

    def test_terminal_context_menu_shows_disconnect_only_when_connected(self) -> None:
        session = _build_session("SSH Host", "sess-terminal-disconnect-menu")
        tab = TerminalTab(settings=self.window._settings)
        self.window._add_session_tab(session, tab, "SSH")

        with patch("snakesh.ui.main_window.QMenu", _MenuProbe):
            _MenuProbe.reset()
            tab.output._show_context_menu(None)
            connected_labels = self._menu_labels()

            tab._mark_connection_closed(True)
            _MenuProbe.reset()
            tab.output._show_context_menu(None)
            closed_labels = self._menu_labels()

        self.assertIn("Disconnect", connected_labels)
        self.assertNotIn("Disconnect", closed_labels)

    def test_terminal_context_menu_disconnect_confirms_and_disconnects(self) -> None:
        session = _build_session("SSH Host", "sess-terminal-disconnect-action")
        tab = TerminalTab(settings=self.window._settings)
        self.window._add_session_tab(session, tab, "SSH")

        with (
            patch("snakesh.ui.main_window.QMenu", _MenuProbe),
            patch.object(tab, "disconnect_session", return_value=True) as mock_disconnect,
            patch("snakesh.ui.main_window.QMessageBox.question", return_value=QMessageBox.Yes) as mock_question,
        ):
            _MenuProbe.reset()
            _MenuProbe.next_action_text = "Disconnect"
            tab.output._show_context_menu(None)
            QApplication.processEvents()

        mock_question.assert_called_once()
        mock_disconnect.assert_called_once()

    def test_tab_context_menu_disconnect_cancel_keeps_terminal_connected(self) -> None:
        session = _build_session("SSH Host", "sess-tab-disconnect-cancel")
        tab = TerminalTab(settings=self.window._settings)
        self.window._add_session_tab(session, tab, "SSH")
        location = self.window._find_widget_location(tab)
        self.assertIsNotNone(location)
        assert location is not None
        host, index = location

        with (
            patch("snakesh.ui.main_window.QMenu", _MenuProbe),
            patch.object(tab, "has_active_connection", return_value=True),
            patch.object(tab, "disconnect_session", return_value=True) as mock_disconnect,
            patch("snakesh.ui.main_window.QMessageBox.question", return_value=QMessageBox.No) as mock_question,
        ):
            _MenuProbe.reset()
            _MenuProbe.next_action_text = "Disconnect"
            self.window._show_tab_context_menu(host, index, None)
            labels = self._menu_labels()

        self.assertIn("Disconnect", labels)
        mock_question.assert_called_once()
        mock_disconnect.assert_not_called()

    def test_tab_context_menu_shows_bulk_disconnect_actions_when_active_terminal_tabs_exist(self) -> None:
        session = _build_session("SSH Host", "sess-bulk-disconnect-menu")
        tab = TerminalTab(settings=self.window._settings)
        self.window._add_session_tab(session, tab, "SSH")
        location = self.window._find_widget_location(tab)
        self.assertIsNotNone(location)
        assert location is not None

        with (
            patch("snakesh.ui.main_window.QMenu", _MenuProbe),
            patch.object(tab, "has_active_connection", return_value=True),
        ):
            _MenuProbe.reset()
            self.window._show_tab_context_menu(location[0], location[1], None)
            actions = {action.text(): action for action in _MenuProbe.latest().actions() if action.text()}

        self.assertIn("Disconnect All Tabs", actions)
        self.assertIn("Disconnect Selected Tabs...", actions)
        self.assertTrue(actions["Disconnect All Tabs"].enabled)
        self.assertTrue(actions["Disconnect Selected Tabs..."].enabled)

    def test_tab_context_menu_disables_bulk_disconnect_actions_without_active_terminal_tabs(self) -> None:
        sftp_session = _build_session("SFTP Host", "sess-bulk-disconnect-sftp")
        sftp_session.protocol = Protocol.SFTP
        sftp_tab = QWidget()
        self.window._add_session_tab(sftp_session, sftp_tab, "SFTP")
        location = self.window._find_widget_location(sftp_tab)
        self.assertIsNotNone(location)
        assert location is not None

        with patch("snakesh.ui.main_window.QMenu", _MenuProbe):
            _MenuProbe.reset()
            self.window._show_tab_context_menu(location[0], location[1], None)
            actions = {action.text(): action for action in _MenuProbe.latest().actions() if action.text()}

        self.assertIn("Disconnect All Tabs", actions)
        self.assertIn("Disconnect Selected Tabs...", actions)
        self.assertFalse(actions["Disconnect All Tabs"].enabled)
        self.assertFalse(actions["Disconnect Selected Tabs..."].enabled)

    def test_tab_context_menu_disconnect_all_disconnects_active_terminal_tabs_across_hosts(self) -> None:
        first_session = _build_session("SSH One", "sess-bulk-disconnect-all-one")
        first_tab = TerminalTab(settings=self.window._settings)
        self.window._add_session_tab(first_session, first_tab, "SSH")

        second_session = _build_session("SSH Two", "sess-bulk-disconnect-all-two")
        second_tab = TerminalTab(settings=self.window._settings)
        self.window._add_session_tab(second_session, second_tab, "SSH")
        second_location = self.window._find_widget_location(second_tab)
        self.assertIsNotNone(second_location)
        assert second_location is not None
        self.window._detach_session_tab(second_tab, second_location[0], second_location[1])
        QApplication.processEvents()

        first_location = self.window._find_widget_location(first_tab)
        self.assertIsNotNone(first_location)
        assert first_location is not None

        with (
            patch("snakesh.ui.main_window.QMenu", _MenuProbe),
            patch.object(first_tab, "has_active_connection", return_value=True),
            patch.object(second_tab, "has_active_connection", return_value=True),
            patch.object(first_tab, "disconnect_session", return_value=True) as first_disconnect,
            patch.object(second_tab, "disconnect_session", return_value=True) as second_disconnect,
            patch("snakesh.ui.main_window.QMessageBox.setCheckBox", autospec=True) as mock_set_checkbox,
            patch("snakesh.ui.main_window.QMessageBox.exec", autospec=True, return_value=QMessageBox.Yes) as mock_exec,
        ):
            _MenuProbe.reset()
            _MenuProbe.next_action_text = "Disconnect All Tabs"
            self.window._show_tab_context_menu(first_location[0], first_location[1], None)
            QApplication.processEvents()

        first_disconnect.assert_called_once()
        second_disconnect.assert_called_once()
        mock_set_checkbox.assert_called_once()
        mock_exec.assert_called_once()

    def test_tab_context_menu_disconnect_all_can_close_tabs_after_disconnect(self) -> None:
        def _check_close_tabs(checkbox) -> None:
            checkbox.setChecked(True)

        session = _build_session("SSH Close All", "sess-bulk-disconnect-all-close")
        tab = TerminalTab(settings=self.window._settings)
        self.window._add_session_tab(session, tab, "SSH")
        location = self.window._find_widget_location(tab)
        self.assertIsNotNone(location)
        assert location is not None
        host, index = location

        with (
            patch("snakesh.ui.main_window.QMenu", _MenuProbe),
            patch.object(tab, "has_active_connection", return_value=True),
            patch.object(tab, "disconnect_session", return_value=True) as mock_disconnect,
            patch.object(tab, "shutdown", return_value=True) as mock_shutdown,
            patch("snakesh.ui.main_window.QMessageBox.setCheckBox", autospec=True, side_effect=_check_close_tabs),
            patch("snakesh.ui.main_window.QMessageBox.exec", autospec=True, return_value=QMessageBox.Yes),
        ):
            _MenuProbe.reset()
            _MenuProbe.next_action_text = "Disconnect All Tabs"
            self.window._show_tab_context_menu(host, index, None)
            QApplication.processEvents()

        mock_disconnect.assert_called_once()
        mock_shutdown.assert_called_once()
        self.assertEqual(host.indexOf(tab), -1)

    def test_tab_context_menu_disconnect_selected_uses_dialog_checked_tabs(self) -> None:
        first_session = _build_session("SSH One", "sess-bulk-disconnect-selected-one")
        first_tab = TerminalTab(settings=self.window._settings)
        self.window._add_session_tab(first_session, first_tab, "SSH")

        second_session = _build_session("SSH Two", "sess-bulk-disconnect-selected-two")
        second_tab = TerminalTab(settings=self.window._settings)
        self.window._add_session_tab(second_session, second_tab, "SSH")

        first_location = self.window._find_widget_location(first_tab)
        self.assertIsNotNone(first_location)
        assert first_location is not None

        with (
            patch("snakesh.ui.main_window.QMenu", _MenuProbe),
            patch("snakesh.ui.main_window.BulkDisconnectTabsDialog", _BulkDisconnectTabsDialogProbe),
            patch.object(first_tab, "has_active_connection", return_value=True),
            patch.object(second_tab, "has_active_connection", return_value=True),
            patch.object(first_tab, "disconnect_session", return_value=True) as first_disconnect,
            patch.object(second_tab, "disconnect_session", return_value=True) as second_disconnect,
            patch("snakesh.ui.main_window.QMessageBox.question", return_value=QMessageBox.Yes),
        ):
            _MenuProbe.reset()
            _BulkDisconnectTabsDialogProbe.reset()
            _BulkDisconnectTabsDialogProbe.next_selected_tabs = [second_tab]
            _MenuProbe.next_action_text = "Disconnect Selected Tabs..."
            self.window._show_tab_context_menu(first_location[0], first_location[1], None)
            QApplication.processEvents()
            dialog = _BulkDisconnectTabsDialogProbe.latest()

        self.assertEqual(dialog.preselected, {id(first_tab)})
        first_disconnect.assert_not_called()
        second_disconnect.assert_called_once()

    def test_tab_context_menu_disconnect_selected_can_close_checked_tabs_after_disconnect(self) -> None:
        session = _build_session("SSH Close", "sess-bulk-disconnect-selected-close")
        tab = TerminalTab(settings=self.window._settings)
        self.window._add_session_tab(session, tab, "SSH")
        location = self.window._find_widget_location(tab)
        self.assertIsNotNone(location)
        assert location is not None
        host, index = location

        with (
            patch("snakesh.ui.main_window.QMenu", _MenuProbe),
            patch("snakesh.ui.main_window.BulkDisconnectTabsDialog", _BulkDisconnectTabsDialogProbe),
            patch.object(tab, "has_active_connection", return_value=True),
            patch.object(tab, "disconnect_session", return_value=True) as mock_disconnect,
            patch.object(tab, "shutdown", return_value=True) as mock_shutdown,
            patch("snakesh.ui.main_window.QMessageBox.question", return_value=QMessageBox.Yes),
        ):
            _MenuProbe.reset()
            _BulkDisconnectTabsDialogProbe.reset()
            _BulkDisconnectTabsDialogProbe.next_selected_tabs = [tab]
            _BulkDisconnectTabsDialogProbe.next_close_after_disconnect = True
            _MenuProbe.next_action_text = "Disconnect Selected Tabs..."
            self.window._show_tab_context_menu(host, index, None)
            QApplication.processEvents()

        mock_disconnect.assert_called_once()
        mock_shutdown.assert_called_once()
        self.assertEqual(host.indexOf(tab), -1)

    def test_tab_context_menu_disconnect_selected_cancel_keeps_tabs_connected(self) -> None:
        session = _build_session("SSH Cancel", "sess-bulk-disconnect-selected-cancel")
        tab = TerminalTab(settings=self.window._settings)
        self.window._add_session_tab(session, tab, "SSH")
        location = self.window._find_widget_location(tab)
        self.assertIsNotNone(location)
        assert location is not None

        with (
            patch("snakesh.ui.main_window.QMenu", _MenuProbe),
            patch("snakesh.ui.main_window.BulkDisconnectTabsDialog", _BulkDisconnectTabsDialogProbe),
            patch.object(tab, "has_active_connection", return_value=True),
            patch.object(tab, "disconnect_session", return_value=True) as mock_disconnect,
            patch("snakesh.ui.main_window.QMessageBox.question") as mock_question,
        ):
            _MenuProbe.reset()
            _BulkDisconnectTabsDialogProbe.reset()
            _BulkDisconnectTabsDialogProbe.next_result = 0
            _BulkDisconnectTabsDialogProbe.next_selected_tabs = [tab]
            _MenuProbe.next_action_text = "Disconnect Selected Tabs..."
            self.window._show_tab_context_menu(location[0], location[1], None)
            QApplication.processEvents()

        mock_disconnect.assert_not_called()
        mock_question.assert_not_called()

    def test_tab_context_menu_disconnect_all_confirmation_cancel_keeps_tabs_connected(self) -> None:
        session = _build_session("SSH Confirm Cancel", "sess-bulk-disconnect-confirm-cancel")
        tab = TerminalTab(settings=self.window._settings)
        self.window._add_session_tab(session, tab, "SSH")
        location = self.window._find_widget_location(tab)
        self.assertIsNotNone(location)
        assert location is not None

        with (
            patch("snakesh.ui.main_window.QMenu", _MenuProbe),
            patch.object(tab, "has_active_connection", return_value=True),
            patch.object(tab, "disconnect_session", return_value=True) as mock_disconnect,
            patch("snakesh.ui.main_window.QMessageBox.setCheckBox", autospec=True) as mock_set_checkbox,
            patch("snakesh.ui.main_window.QMessageBox.exec", autospec=True, return_value=QMessageBox.No) as mock_exec,
        ):
            _MenuProbe.reset()
            _MenuProbe.next_action_text = "Disconnect All Tabs"
            self.window._show_tab_context_menu(location[0], location[1], None)
            QApplication.processEvents()

        mock_set_checkbox.assert_called_once()
        mock_exec.assert_called_once()
        mock_disconnect.assert_not_called()

    def test_terminal_context_menu_shows_open_sftp_for_ssh_tabs_only(self) -> None:
        ssh_session = _build_session("SSH Host", "sess-ssh-menu")
        ssh_tab = TerminalTab(settings=self.window._settings)
        self.window._add_session_tab(ssh_session, ssh_tab, "SSH")

        local_session = Session(
            id="sess-local-menu",
            name="Local Shell",
            host="localhost",
            protocol=Protocol.SSH,
            port=22,
            username="tester",
        )
        local_tab = TerminalTab(settings=self.window._settings)
        self.window._add_session_tab(local_session, local_tab, "LOCAL")

        with patch("snakesh.ui.main_window.QMenu", _MenuProbe):
            _MenuProbe.reset()
            ssh_tab.output._show_context_menu(None)
            ssh_labels = self._menu_labels()

            _MenuProbe.reset()
            local_tab.output._show_context_menu(None)
            local_labels = self._menu_labels()

        self.assertIn("Open SFTP Tab", ssh_labels)
        self.assertNotIn("Open SFTP Tab", local_labels)

    def test_tab_context_menu_shows_disconnect_for_active_terminal_tabs_only(self) -> None:
        ssh_session = _build_session("SSH Host", "sess-ssh-disconnect-menu")
        ssh_tab = TerminalTab(settings=self.window._settings)
        self.window._add_session_tab(ssh_session, ssh_tab, "SSH")
        ssh_location = self.window._find_widget_location(ssh_tab)
        self.assertIsNotNone(ssh_location)
        assert ssh_location is not None

        local_session = Session(
            id="sess-local-disconnect-menu",
            name="Local Shell",
            host="localhost",
            protocol=Protocol.SSH,
            port=22,
            username="tester",
        )
        local_tab = TerminalTab(settings=self.window._settings)
        self.window._add_session_tab(local_session, local_tab, "LOCAL")
        local_location = self.window._find_widget_location(local_tab)
        self.assertIsNotNone(local_location)
        assert local_location is not None

        sftp_session = _build_session("SFTP Host", "sess-sftp-disconnect-menu")
        sftp_session.protocol = Protocol.SFTP
        sftp_tab = QWidget()
        self.window._add_session_tab(sftp_session, sftp_tab, "SFTP")
        sftp_location = self.window._find_widget_location(sftp_tab)
        self.assertIsNotNone(sftp_location)
        assert sftp_location is not None

        with (
            patch("snakesh.ui.main_window.QMenu", _MenuProbe),
            patch.object(ssh_tab, "has_active_connection", return_value=True),
            patch.object(local_tab, "has_active_connection", return_value=True),
        ):
            _MenuProbe.reset()
            self.window._show_tab_context_menu(ssh_location[0], ssh_location[1], None)
            ssh_labels = self._menu_labels()

            _MenuProbe.reset()
            self.window._show_tab_context_menu(local_location[0], local_location[1], None)
            local_labels = self._menu_labels()

            _MenuProbe.reset()
            self.window._show_tab_context_menu(sftp_location[0], sftp_location[1], None)
            sftp_labels = self._menu_labels()

        self.assertIn("Disconnect", ssh_labels)
        self.assertIn("Disconnect", local_labels)
        self.assertNotIn("Disconnect", sftp_labels)

    def test_tab_context_menu_opens_sftp_for_saved_ssh_tab(self) -> None:
        session = _build_session("SSH Host", "sess-ssh-tab-menu")
        self.session_service.add_or_update(session)
        tab = TerminalTab(settings=self.window._settings)
        self.window._add_session_tab(session, tab, "SSH")
        tab._mark_connection_closed(True)
        location = self.window._find_widget_location(tab)
        self.assertIsNotNone(location)
        assert location is not None
        host, index = location

        with (
            patch("snakesh.ui.main_window.QMenu", _MenuProbe),
            patch.object(self.window, "_open_sftp_tab") as mock_open_sftp,
        ):
            _MenuProbe.reset()
            _MenuProbe.next_action_text = "Open SFTP Tab"
            self.window._show_tab_context_menu(host, index, None)
            labels = self._menu_labels()

        self.assertIn("Open SFTP Tab", labels)
        mock_open_sftp.assert_called_once_with(session)

    def test_tab_context_menu_hides_open_sftp_for_local_and_sftp_tabs(self) -> None:
        local_session = Session(
            id="sess-local-tab-menu",
            name="Local Shell",
            host="localhost",
            protocol=Protocol.SSH,
            port=22,
            username="tester",
        )
        local_tab = TerminalTab(settings=self.window._settings)
        self.window._add_session_tab(local_session, local_tab, "LOCAL")
        local_location = self.window._find_widget_location(local_tab)
        self.assertIsNotNone(local_location)
        assert local_location is not None

        sftp_session = _build_session("SFTP Host", "sess-sftp-tab-menu")
        sftp_session.protocol = Protocol.SFTP
        self.session_service.add_or_update(sftp_session)
        sftp_tab = QWidget()
        self.window._add_session_tab(sftp_session, sftp_tab, "SFTP")
        sftp_location = self.window._find_widget_location(sftp_tab)
        self.assertIsNotNone(sftp_location)
        assert sftp_location is not None

        with patch("snakesh.ui.main_window.QMenu", _MenuProbe):
            _MenuProbe.reset()
            self.window._show_tab_context_menu(local_location[0], local_location[1], None)
            local_labels = self._menu_labels()

            _MenuProbe.reset()
            self.window._show_tab_context_menu(sftp_location[0], sftp_location[1], None)
            sftp_labels = self._menu_labels()

        self.assertNotIn("Open SFTP Tab", local_labels)
        self.assertNotIn("Open SFTP Tab", sftp_labels)

    def test_tab_context_menu_shows_reconnect_for_closed_local_shell_tab(self) -> None:
        session = Session(
            id="sess-local-closed-reconnect",
            name="Local Shell",
            host="localhost",
            protocol=Protocol.SSH,
            port=22,
            username="tester",
        )
        tab = TerminalTab(settings=self.window._settings)
        tab._windows_local_shell_start_program = "/bin/sh"
        tab._windows_local_shell_start_arguments = ["-i"]
        tab._windows_local_shell_start_working_directory = "/tmp"
        tab._mark_connection_closed(True)
        self.window._add_session_tab(session, tab, "LOCAL")
        location = self.window._find_widget_location(tab)
        self.assertIsNotNone(location)
        assert location is not None

        with patch("snakesh.ui.main_window.QMenu", _MenuProbe):
            _MenuProbe.reset()
            self.window._show_tab_context_menu(location[0], location[1], None)
            labels = self._menu_labels()

        self.assertIn("Reconnect Session", labels)
        self.assertNotIn("Disconnect", labels)

    def test_quick_connect_ssh_tab_uses_host_title_and_hides_saved_session_actions(self) -> None:
        session = Session(
            id="sess-quick-connect-closed",
            name="",
            host="ephemeral-host",
            protocol=Protocol.SSH,
            port=22,
            username="tester",
        )
        tab = TerminalTab(settings=self.window._settings)
        self.window._add_session_tab(session, tab, "SSH", runtime_only=True)
        tab._mark_connection_closed(True)
        location = self.window._find_widget_location(tab)
        self.assertIsNotNone(location)
        assert location is not None
        host, index = location

        self.assertEqual(host.tabText(index), "ephemeral-host (closed)")
        self.assertFalse(tab.can_reconnect_session())

        with patch("snakesh.ui.main_window.QMenu", _MenuProbe):
            _MenuProbe.reset()
            self.window._show_tab_context_menu(host, index, None)
            tab_labels = self._menu_labels()

            _MenuProbe.reset()
            tab.output._show_context_menu(None)
            terminal_labels = self._menu_labels()

        self.assertNotIn("Reconnect Session", tab_labels)
        self.assertNotIn("Open SFTP Tab", tab_labels)
        self.assertNotIn("Open SFTP Tab", terminal_labels)

    def test_quick_connect_tabs_are_excluded_from_workspace_profile_snapshot(self) -> None:
        saved = _build_session("Saved Host", "sess-profile-saved")
        self.session_service._sessions = [saved]
        quick = Session(
            id="sess-profile-quick",
            name="",
            host="runtime-only-host",
            protocol=Protocol.SSH,
            port=22,
            username="tester",
        )
        self.window._add_session_tab(saved, QWidget(), "SSH")
        self.window._add_session_tab(quick, QWidget(), "SSH", runtime_only=True)

        snapshot = self.window._capture_workspace_profile_snapshot()
        tree = snapshot.get("workspace_tree", {})
        self.assertIsInstance(tree, dict)
        assert isinstance(tree, dict)
        tabs = tree.get("tabs", [])
        self.assertIsInstance(tabs, list)
        assert isinstance(tabs, list)
        self.assertEqual(len(tabs), 1)
        self.assertEqual(tabs[0].get("session_id"), saved.id)

    def test_disconnect_session_keeps_scrollback_and_stops_logging(self) -> None:
        class _FakeProcess:
            def __init__(self) -> None:
                self._state = main_window.QProcess.Running
                self.writes: list[bytes] = []
                self.terminated = False
                self.killed = False

            def state(self):
                return self._state

            def write(self, payload: bytes) -> int:
                self.writes.append(bytes(payload))
                return len(payload)

            def terminate(self) -> None:
                self.terminated = True
                self._state = main_window.QProcess.NotRunning

            def waitForFinished(self, _timeout: int) -> bool:  # noqa: N802
                return self._state == main_window.QProcess.NotRunning

            def kill(self) -> None:
                self.killed = True
                self._state = main_window.QProcess.NotRunning

        tab = TerminalTab(settings=self.window._settings)
        tab.append("before disconnect\r\n")
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "session.log"
            tab.start_logging(str(log_path))
            fake_process = _FakeProcess()
            tab._local_process = fake_process
            tab._mark_connection_closed(False)

            disconnected = tab.disconnect_session(wait_seconds=0.25)

        self.assertTrue(disconnected)
        self.assertTrue(tab.is_session_closed())
        self.assertFalse(tab.is_logging_enabled())
        self.assertIn("before disconnect", tab.scrollback_text())
        self.assertEqual(fake_process.writes, [b"exit\r\n"])
        self.assertTrue(fake_process.terminated)

    def test_closed_terminal_tab_closes_without_active_tab_prompt(self) -> None:
        session = _build_session("Closed Session", "sess-close-closed")
        tab = TerminalTab(settings=self.window._settings)
        self.window._add_session_tab(session, tab, "SSH")
        tab._mark_connection_closed(True)
        location = self.window._find_widget_location(tab)
        self.assertIsNotNone(location)
        assert location is not None
        host, index = location

        with (
            patch.object(tab, "shutdown", return_value=True) as mock_shutdown,
            patch("snakesh.ui.main_window.QMessageBox.question") as mock_question,
        ):
            host.tabCloseRequested.emit(index)
            QApplication.processEvents()

        self.assertEqual(host.indexOf(tab), -1)
        mock_question.assert_not_called()
        mock_shutdown.assert_called_once()

    def test_active_tab_close_prompt_setting_disabled_skips_prompt(self) -> None:
        self.window._settings.warn_before_closing_active_tab = False
        session = _build_session("Prompt Disabled", "sess-close-setting-off")
        tab = TerminalTab(settings=self.window._settings)
        self.window._add_session_tab(session, tab, "SSH")
        location = self.window._find_widget_location(tab)
        self.assertIsNotNone(location)
        assert location is not None
        host, index = location

        with (
            patch.object(tab, "has_active_connection", return_value=True),
            patch.object(tab, "shutdown", return_value=True) as mock_shutdown,
            patch("snakesh.ui.main_window.QMessageBox.question") as mock_question,
        ):
            host.tabCloseRequested.emit(index)
            QApplication.processEvents()

        self.assertEqual(host.indexOf(tab), -1)
        mock_question.assert_not_called()
        mock_shutdown.assert_called_once()

    def test_direct_close_tab_in_host_stays_silent_for_active_tab(self) -> None:
        session = _build_session("Direct Close", "sess-close-direct")
        tab = TerminalTab(settings=self.window._settings)
        self.window._add_session_tab(session, tab, "SSH")
        location = self.window._find_widget_location(tab)
        self.assertIsNotNone(location)
        assert location is not None
        host, index = location

        with (
            patch.object(tab, "has_active_connection", return_value=True),
            patch.object(tab, "shutdown", return_value=True) as mock_shutdown,
            patch("snakesh.ui.main_window.QMessageBox.question") as mock_question,
        ):
            self.window._close_tab_in_host(host, index)
            QApplication.processEvents()

        self.assertEqual(host.indexOf(tab), -1)
        mock_question.assert_not_called()
        mock_shutdown.assert_called_once()

    def test_close_window_prompts_when_connected_tabs_are_open_and_cancel_keeps_window_open(self) -> None:
        session = _build_session("Prompted Session", "sess-close-prompt")
        tab = TerminalTab(settings=self.window._settings)
        self.window._add_session_tab(session, tab, "SSH")

        with (
            patch.object(tab, "has_active_connection", return_value=True),
            patch("snakesh.ui.main_window.QMessageBox.question", return_value=QMessageBox.No) as mock_question,
        ):
            self.window.close()
            QApplication.processEvents()

        self.assertTrue(self.window.isVisible())
        mock_question.assert_called_once()

    def test_close_window_prompts_when_connected_tabs_are_open_and_accept_closes_window(self) -> None:
        session = _build_session("Prompted Session", "sess-close-accept")
        tab = TerminalTab(settings=self.window._settings)
        self.window._add_session_tab(session, tab, "SSH")

        with (
            patch.object(tab, "has_active_connection", return_value=True),
            patch.object(tab, "shutdown", return_value=True) as mock_shutdown,
            patch("snakesh.ui.main_window.QMessageBox.question", return_value=QMessageBox.Yes) as mock_question,
        ):
            self.window.close()
            QApplication.processEvents()

        self.assertFalse(self.window.isVisible())
        mock_question.assert_called_once()
        mock_shutdown.assert_called_once()

    def test_terminal_shutdown_caps_wait_budget_at_four_seconds(self) -> None:
        tab = TerminalTab(settings=self.window._settings)
        thread = MagicMock()
        thread.isRunning.return_value = True
        worker = MagicMock()
        tab._thread = thread
        tab._worker = worker

        monotonic_values = iter([100.0, 100.1, 101.1, 102.1, 103.1, 104.1])
        with (
            patch("snakesh.ui.main_window.time.monotonic", side_effect=lambda: next(monotonic_values)),
            patch.object(tab, "_append_local_status") as mock_status,
        ):
            self.assertFalse(tab.shutdown())

        self.assertEqual(thread.wait.call_count, 4)
        worker.stop.assert_called()
        mock_status.assert_called_once_with("Shell is still shutting down; please close this tab again in a moment.")

    def test_ssh_worker_stop_aborts_transport_immediately(self) -> None:
        session = _build_session("Abort SSH", "sess-ssh-abort")
        worker = SSHShellWorker(
            session=session,
            password=None,
            trust_unknown=False,
            x11_forwarding=False,
            cols=80,
            rows=24,
        )
        queue_obj = MagicMock()
        channel = MagicMock()
        proc = MagicMock()
        proc.channel = channel
        conn = MagicMock()
        main_task = MagicMock()
        main_task.done.return_value = False
        loop = MagicMock()
        loop.is_closed.return_value = False
        loop.call_soon_threadsafe.side_effect = lambda fn: fn()
        worker._queue = queue_obj
        worker._proc = proc
        worker._conn = conn
        worker._main_task = main_task
        worker._loop = loop

        worker.stop()

        queue_obj.put_nowait.assert_called_once_with(None)
        proc.close.assert_called()
        channel.abort.assert_called()
        conn.abort.assert_called_once()
        main_task.cancel.assert_called_once()

    def test_ssh_worker_wait_closed_timeout_aborts_resource(self) -> None:
        session = _build_session("Timeout SSH", "sess-ssh-timeout")
        worker = SSHShellWorker(
            session=session,
            password=None,
            trust_unknown=False,
            x11_forwarding=False,
            cols=80,
            rows=24,
        )

        class _SlowResource:
            def __init__(self) -> None:
                self.abort_calls = 0

            async def wait_closed(self) -> None:
                await asyncio.sleep(0.05)

            def abort(self) -> None:
                self.abort_calls += 1

        resource = _SlowResource()
        asyncio.run(
            worker._wait_closed_with_deadline(
                resource,
                deadline=time.monotonic() + 0.01,
                abort_targets=(resource,),
            )
        )

        self.assertGreaterEqual(resource.abort_calls, 1)

    def test_terminal_tab_applies_session_color_override(self) -> None:
        session = _build_session("Colorized", "sess-color")
        session.terminal_color_override_enabled = True
        session.terminal_bg_color = "#112233"
        session.terminal_fg_color = "#f8fafc"
        tab = TerminalTab(settings=self.window._settings)

        self.window._add_session_tab(session, tab, "SSH")

        style = tab.output.styleSheet().lower()
        self.assertIn("#112233", style)
        self.assertIn("#f8fafc", style)

    def test_terminal_view_uses_configured_defaults_when_classic_mode_disabled(self) -> None:
        settings = AppSettings.defaults()
        settings.terminal_bg = "#102030"
        settings.terminal_fg = "#f8fafc"
        tab = TerminalTab(settings=settings)

        self._show_terminal_tab(tab)

        self.assertEqual(tab.output._terminal_bg_name, "#102030")
        self.assertEqual(tab.output._terminal_fg_name, "#f8fafc")

    def test_terminal_view_uses_classic_defaults_when_enabled(self) -> None:
        settings = AppSettings.defaults()
        settings.terminal_bg = "#102030"
        settings.terminal_fg = "#f8fafc"
        settings.terminal_classic_default_colors = True
        tab = TerminalTab(settings=settings)

        self._show_terminal_tab(tab)

        self.assertEqual(tab.output._terminal_bg_name, "#000000")
        self.assertEqual(tab.output._terminal_fg_name, "#e5e5e5")

    def test_terminal_session_color_override_beats_classic_default_color_mode(self) -> None:
        self.window._settings.terminal_classic_default_colors = True
        session = _build_session("Classic Override", "sess-classic-override")
        session.terminal_color_override_enabled = True
        session.terminal_bg_color = "#112233"
        session.terminal_fg_color = ""
        tab = TerminalTab(settings=self.window._settings)

        self.window._add_session_tab(session, tab, "SSH")

        self.assertEqual(tab.output._terminal_bg_name, "#112233")
        self.assertEqual(tab.output._terminal_fg_name, "#e5e5e5")

    def test_terminal_classic_default_colors_make_default_background_match_explicit_black(self) -> None:
        payload = "\x1b[40m  \x1b[0m  "

        settings = AppSettings.defaults()
        settings.terminal_bg = "#102030"
        settings.terminal_fg = "#f8fafc"
        tab = TerminalTab(settings=settings)
        self._show_terminal_tab(tab)
        tab.append(payload)
        tab._render_timer.stop()
        tab._render()

        black_cell = self._terminal_cell_color(tab.output, col=0, row=0)
        default_cell = self._terminal_cell_color(tab.output, col=2, row=0)
        self.assertEqual(black_cell, "#000000")
        self.assertEqual(default_cell, "#102030")
        self.assertNotEqual(black_cell, default_cell)

        classic_settings = AppSettings.from_dict(settings.to_dict())
        classic_settings.terminal_classic_default_colors = True
        classic_tab = TerminalTab(settings=classic_settings)
        self._show_terminal_tab(classic_tab)
        classic_tab.append(payload)
        classic_tab._render_timer.stop()
        classic_tab._render()

        classic_black_cell = self._terminal_cell_color(classic_tab.output, col=0, row=0)
        classic_default_cell = self._terminal_cell_color(classic_tab.output, col=2, row=0)
        self.assertEqual(classic_black_cell, "#000000")
        self.assertEqual(classic_default_cell, "#000000")

    def test_session_tree_folder_expand_state_persists_across_reload(self) -> None:
        session = _build_session("Broadworks - ADP10CommLab", "sess-folders")
        session.folder = "Broadworks/Lab"
        self.session_service._sessions = [session]
        self.session_service._folders = ["Default", "Broadworks", "Broadworks/Lab"]

        self.window._refresh_tree()
        QApplication.processEvents()
        QTest.qWait(200)
        QApplication.processEvents()

        broadworks_item = self._find_folder_item(self.window, "Broadworks")
        self.assertIsNotNone(broadworks_item)
        assert broadworks_item is not None

        broadworks_item.setExpanded(False)
        QApplication.processEvents()
        self.assertEqual(self.settings_service.save_calls, 1)
        self.assertNotIn("Broadworks", self.window._settings.session_tree_expanded_folders)
        QTest.qWait(200)
        QApplication.processEvents()
        self.assertNotIn("Broadworks", self.settings_service._settings.session_tree_expanded_folders)

        reloaded = MainWindow(self.session_service, self.settings_service)
        reloaded.show()
        QApplication.processEvents()
        try:
            reloaded_item = self._find_folder_item(reloaded, "Broadworks")
            self.assertIsNotNone(reloaded_item)
            assert reloaded_item is not None
            self.assertFalse(reloaded_item.isExpanded())
        finally:
            reloaded.close()
            reloaded.deleteLater()
            QApplication.processEvents()

    def test_session_tree_expand_state_saves_are_coalesced(self) -> None:
        session = _build_session("Broadworks - ADP10CommLab", "sess-folders-coalesce")
        session.folder = "Broadworks/Lab"
        self.session_service._sessions = [session]
        self.session_service._folders = ["Default", "Broadworks", "Broadworks/Lab"]

        self.window._refresh_tree()
        QApplication.processEvents()
        QTest.qWait(200)
        QApplication.processEvents()
        baseline_save_calls = self.settings_service.save_calls

        broadworks_item = self._find_folder_item(self.window, "Broadworks")
        self.assertIsNotNone(broadworks_item)
        assert broadworks_item is not None

        broadworks_item.setExpanded(False)
        QApplication.processEvents()
        broadworks_item.setExpanded(True)
        QApplication.processEvents()
        broadworks_item.setExpanded(False)
        QApplication.processEvents()

        self.assertEqual(self.settings_service.save_calls, baseline_save_calls)
        QTest.qWait(200)
        QApplication.processEvents()
        self.assertEqual(self.settings_service.save_calls, baseline_save_calls + 1)
        self.assertNotIn("Broadworks", self.settings_service._settings.session_tree_expanded_folders)

    def test_close_flushes_pending_session_tree_expand_state_save(self) -> None:
        session = _build_session("Broadworks - ADP10CommLab", "sess-folders-close")
        session.folder = "Broadworks/Lab"
        self.session_service._sessions = [session]
        self.session_service._folders = ["Default", "Broadworks", "Broadworks/Lab"]

        self.window._refresh_tree()
        QApplication.processEvents()
        QTest.qWait(200)
        QApplication.processEvents()

        broadworks_item = self._find_folder_item(self.window, "Broadworks")
        self.assertIsNotNone(broadworks_item)
        assert broadworks_item is not None

        broadworks_item.setExpanded(False)
        QApplication.processEvents()
        self.assertEqual(self.settings_service.save_calls, 1)

        self.window.close()
        QApplication.processEvents()

        self.assertNotIn("Broadworks", self.settings_service._settings.session_tree_expanded_folders)

    def test_enable_password_save_for_session_persists_flag(self) -> None:
        session = _build_session("Host A", "sess-save")
        session.save_password = False
        self.session_service._sessions = [session]

        self.window._enable_password_save_for_session(session)

        self.assertTrue(session.save_password)
        self.assertEqual(len(self.session_service._sessions), 1)
        self.assertTrue(self.session_service._sessions[0].save_password)

    def test_connect_current_opens_selected_sessions_after_confirmation(self) -> None:
        first = _build_session("Host A", "sess-connect-a")
        second = _build_session("Host B", "sess-connect-b")
        self.session_service._sessions = [first, second]
        self.window._refresh_tree()
        QApplication.processEvents()

        first_item = self._find_session_item(self.window, first.id)
        second_item = self._find_session_item(self.window, second.id)
        self.assertIsNotNone(first_item)
        self.assertIsNotNone(second_item)
        assert first_item is not None
        assert second_item is not None
        first_item.setSelected(True)
        second_item.setSelected(True)

        with patch("snakesh.ui.main_window.QMessageBox.question", return_value=QMessageBox.Yes) as mock_question:
            with patch.object(self.window, "_connect_session") as mock_connect:
                self.window._connect_current()

        self.assertEqual([call.args[0].id for call in mock_connect.call_args_list], [first.id, second.id])
        mock_question.assert_called_once()

    def test_connect_current_aborts_when_multi_connect_warning_is_declined(self) -> None:
        first = _build_session("Host A", "sess-connect-a")
        second = _build_session("Host B", "sess-connect-b")
        self.session_service._sessions = [first, second]
        self.window._refresh_tree()
        QApplication.processEvents()

        first_item = self._find_session_item(self.window, first.id)
        second_item = self._find_session_item(self.window, second.id)
        self.assertIsNotNone(first_item)
        self.assertIsNotNone(second_item)
        assert first_item is not None
        assert second_item is not None
        first_item.setSelected(True)
        second_item.setSelected(True)

        with patch("snakesh.ui.main_window.QMessageBox.question", return_value=QMessageBox.No) as mock_question:
            with patch.object(self.window, "_connect_session") as mock_connect:
                self.window._connect_current()

        mock_connect.assert_not_called()
        mock_question.assert_called_once()

    def test_session_tree_double_click_connects_only_clicked_session(self) -> None:
        first = _build_session("Host A", "sess-connect-a")
        second = _build_session("Host B", "sess-connect-b")
        self.session_service._sessions = [first, second]
        self.window._refresh_tree()
        QApplication.processEvents()

        first_item = self._find_session_item(self.window, first.id)
        second_item = self._find_session_item(self.window, second.id)
        self.assertIsNotNone(first_item)
        self.assertIsNotNone(second_item)
        assert first_item is not None
        assert second_item is not None
        first_item.setSelected(True)
        second_item.setSelected(True)

        with patch.object(self.window, "_connect_session") as mock_connect:
            self.window._on_session_tree_double_clicked(second_item, 0)

        mock_connect.assert_called_once_with(second)

    def test_main_splitter_state_is_restored_from_settings(self) -> None:
        self.window._main_splitter.setSizes([120, 1200])
        QApplication.processEvents()
        self.window._persist_main_splitter_state()
        encoded = self.settings_service._settings.main_window_splitter_b64
        self.assertTrue(encoded)

        restored = MainWindow(self.session_service, self.settings_service)
        restored.show()
        QApplication.processEvents()
        try:
            sizes = restored._main_splitter.sizes()
            total = sum(sizes)
            self.assertGreater(total, 0)
            ratio = sizes[0] / total
            self.assertLess(ratio, 0.18)
        finally:
            restored.close()
            restored.deleteLater()
            QApplication.processEvents()

    def test_main_splitter_persistence_uses_docked_state_while_session_list_is_floating(self) -> None:
        self.window._main_splitter.setSizes([180, 1100])
        QApplication.processEvents()
        expected = self.window._capture_current_main_splitter_state()

        self.window._set_session_list_mode("float")
        QApplication.processEvents()
        self.window._persist_main_splitter_state()

        self.assertEqual(self.settings_service._settings.main_window_splitter_b64, expected)

    def test_sftp_tab_initial_local_directory_uses_preferred_path(self) -> None:
        session = _build_session("SFTP Host", "sess-sftp-pref")
        session.protocol = Protocol.SFTP
        tab = SFTPSessionTab(
            session=session,
            sftp=self.window._sftp,
            initial_remote_dir="/",
            initial_remote_entries=[],
            initial_local_dir="~",
            password=None,
            execute_remote=lambda _session, current_password, _operation: (None, current_password),
            status_callback=lambda _message, _timeout_ms: None,
            should_confirm_delete=lambda: True,
        )
        try:
            expected = os.path.abspath(os.path.expanduser("~"))
            self.assertEqual(tab.local_path_input.text(), expected)
        finally:
            tab.deleteLater()
            QApplication.processEvents()

    def test_sftp_tab_initial_local_directory_falls_back_when_missing(self) -> None:
        session = _build_session("SFTP Host", "sess-sftp-fallback")
        session.protocol = Protocol.SFTP
        with tempfile.TemporaryDirectory() as tmp:
            missing = os.path.join(tmp, "missing-dir")
            tab = SFTPSessionTab(
                session=session,
                sftp=self.window._sftp,
                initial_remote_dir="/",
                initial_remote_entries=[],
                initial_local_dir=missing,
                password=None,
                execute_remote=lambda _session, current_password, _operation: (None, current_password),
                status_callback=lambda _message, _timeout_ms: None,
                should_confirm_delete=lambda: True,
            )
        try:
            expected = os.path.abspath(os.path.expanduser("~"))
            self.assertEqual(tab.local_path_input.text(), expected)
        finally:
            tab.deleteLater()
            QApplication.processEvents()

    def test_sftp_remote_listing_includes_modified_column_and_sort_metadata(self) -> None:
        session = _build_session("SFTP Host", "sess-sftp-modified")
        session.protocol = Protocol.SFTP
        entries = [
            SFTPEntry(
                name="app.log",
                path="/app.log",
                is_dir=False,
                size=128,
                modified_time=1_700_000_000,
            )
        ]
        tab = SFTPSessionTab(
            session=session,
            sftp=self.window._sftp,
            initial_remote_dir="/",
            initial_remote_entries=entries,
            initial_local_dir="~",
            password=None,
            execute_remote=lambda _session, current_password, _operation: (None, current_password),
            status_callback=lambda _message, _timeout_ms: None,
            should_confirm_delete=lambda: True,
        )
        try:
            headers = [tab.remote_file_tree.headerItem().text(i) for i in range(tab.remote_file_tree.columnCount())]
            self.assertEqual(headers, ["Name", "Type", "Size", "Modified"])
            self.assertEqual(tab.remote_file_tree.topLevelItemCount(), 1)
            item = tab.remote_file_tree.topLevelItem(0)
            self.assertTrue(item.text(3))
            self.assertEqual(item.data(3, tab.REMOTE_SORT_ROLE)[1], 1_700_000_000)
        finally:
            tab.deleteLater()
            QApplication.processEvents()

    def test_sftp_remote_listing_sorts_by_modified_header(self) -> None:
        session = _build_session("SFTP Host", "sess-sftp-sort")
        session.protocol = Protocol.SFTP
        entries = [
            SFTPEntry(
                name="older.txt",
                path="/older.txt",
                is_dir=False,
                size=10,
                modified_time=100,
            ),
            SFTPEntry(
                name="newer.txt",
                path="/newer.txt",
                is_dir=False,
                size=20,
                modified_time=200,
            ),
        ]
        tab = SFTPSessionTab(
            session=session,
            sftp=self.window._sftp,
            initial_remote_dir="/",
            initial_remote_entries=entries,
            initial_local_dir="~",
            password=None,
            execute_remote=lambda _session, current_password, _operation: (None, current_password),
            status_callback=lambda _message, _timeout_ms: None,
            should_confirm_delete=lambda: True,
        )
        try:
            tab.remote_file_tree.sortByColumn(3, Qt.DescendingOrder)
            first = tab.remote_file_tree.topLevelItem(0)
            self.assertEqual(first.text(0), "newer.txt")
        finally:
            tab.deleteLater()
            QApplication.processEvents()

    def test_sftp_remote_and_local_directory_names_include_trailing_slash(self) -> None:
        session = _build_session("SFTP Host", "sess-sftp-dir-slash")
        session.protocol = Protocol.SFTP
        entries = [
            SFTPEntry(
                name="folder",
                path="/folder",
                is_dir=True,
                size=0,
                modified_time=1_700_000_000,
            )
        ]
        tab = SFTPSessionTab(
            session=session,
            sftp=self.window._sftp,
            initial_remote_dir="/",
            initial_remote_entries=entries,
            initial_local_dir="~",
            password=None,
            execute_remote=lambda _session, current_password, _operation: (None, current_password),
            status_callback=lambda _message, _timeout_ms: None,
            should_confirm_delete=lambda: True,
        )
        try:
            remote_item = tab.remote_file_tree.topLevelItem(0)
            self.assertIsNotNone(remote_item)
            assert remote_item is not None
            self.assertEqual(remote_item.text(0), "folder/")

            tab._populate_local_listing(
                [
                    main_window._LocalSFTPEntry(
                        name="folder",
                        path="/tmp/folder",
                        is_dir=True,
                        is_symlink=False,
                        size=0,
                        modified_time=1_700_000_000,
                    )
                ]
            )
            local_item = tab.local_file_tree.topLevelItem(0)
            self.assertIsNotNone(local_item)
            assert local_item is not None
            self.assertEqual(local_item.text(0), "folder/")
        finally:
            tab.deleteLater()
            QApplication.processEvents()

    def test_sftp_local_panel_matches_remote_listing_structure(self) -> None:
        session = _build_session("SFTP Host", "sess-sftp-matching-layout")
        session.protocol = Protocol.SFTP
        tab = SFTPSessionTab(
            session=session,
            sftp=self.window._sftp,
            initial_remote_dir="/",
            initial_remote_entries=[],
            initial_local_dir="~",
            password=None,
            execute_remote=lambda _session, current_password, _operation: (None, current_password),
            status_callback=lambda _message, _timeout_ms: None,
            should_confirm_delete=lambda: True,
        )
        try:
            local_headers = [tab.local_file_tree.headerItem().text(i) for i in range(tab.local_file_tree.columnCount())]
            remote_headers = [tab.remote_file_tree.headerItem().text(i) for i in range(tab.remote_file_tree.columnCount())]
            self.assertEqual(local_headers, remote_headers)
            self.assertTrue(tab.local_nav_tree.isHeaderHidden())
            self.assertTrue(tab.remote_nav_tree.isHeaderHidden())
        finally:
            tab.deleteLater()
            QApplication.processEvents()

    def test_sftp_local_file_double_click_signals_upload_once(self) -> None:
        session = _build_session("SFTP Host", "sess-sftp-local-double-click")
        session.protocol = Protocol.SFTP
        sftp = MagicMock()
        sftp.list_directory = AsyncMock(return_value=("/remote", []))

        with tempfile.TemporaryDirectory() as temp_dir:
            local_file = Path(temp_dir) / "upload.txt"
            local_file.write_text("payload", encoding="utf-8")

            tab = SFTPSessionTab(
                session=session,
                sftp=sftp,
                initial_remote_dir="/remote",
                initial_remote_entries=[],
                initial_local_dir=temp_dir,
                password=None,
                execute_remote=lambda _session, current_password, _operation: (None, current_password),
                status_callback=lambda _message, _timeout_ms: None,
                should_confirm_delete=lambda: True,
                parent=self.window,
            )
            self.window._add_session_tab(session, tab, "SFTP")
            try:
                QApplication.processEvents()
                item = tab.local_file_tree.topLevelItem(0)
                self.assertIsNotNone(item)
                assert item is not None

                with patch.object(tab, "_upload_local_paths") as mock_upload:
                    tab.local_file_tree.itemDoubleClicked.emit(item, 0)
                    tab.local_file_tree.itemActivated.emit(item, 0)

                mock_upload.assert_called_once_with([str(local_file)])
            finally:
                tab.deleteLater()
                QApplication.processEvents()

    def test_sftp_upload_conflict_prompts_once_and_proceeds_on_yes(self) -> None:
        sftp = MagicMock()
        sftp.find_upload_overwrite_conflicts = AsyncMock(
            return_value=[OverwriteConflict("/tmp/local.txt", "/remote/local.txt")]
        )
        sftp.upload_paths = AsyncMock(return_value=1)
        status_messages: list[str] = []
        tab = self._build_sftp_tab_for_transfers(sftp=sftp, status_messages=status_messages)
        try:
            progress_dialog = MagicMock()
            progress_dialog.is_cancel_requested.return_value = False
            with (
                patch("snakesh.ui.main_window.UploadProgressDialog", return_value=progress_dialog),
                patch(
                    "snakesh.ui.main_window.QMessageBox.exec",
                    autospec=True,
                    return_value=QMessageBox.Yes,
                ) as mock_exec,
            ):
                tab._upload_local_paths(["/tmp/local.txt"])
            sftp.find_upload_overwrite_conflicts.assert_awaited_once()
            sftp.upload_paths.assert_awaited_once()
            self.assertEqual(mock_exec.call_count, 1)
            self.assertIn("Uploaded 1 item(s) to /remote", status_messages)
        finally:
            tab.deleteLater()
            QApplication.processEvents()

    def test_sftp_upload_conflict_cancels_on_no(self) -> None:
        sftp = MagicMock()
        sftp.find_upload_overwrite_conflicts = AsyncMock(
            return_value=[OverwriteConflict("/tmp/local.txt", "/remote/local.txt")]
        )
        sftp.upload_paths = AsyncMock(return_value=1)
        status_messages: list[str] = []
        tab = self._build_sftp_tab_for_transfers(sftp=sftp, status_messages=status_messages)
        try:
            with patch(
                "snakesh.ui.main_window.QMessageBox.exec",
                autospec=True,
                return_value=QMessageBox.No,
            ) as mock_exec:
                tab._upload_local_paths(["/tmp/local.txt"])
            sftp.find_upload_overwrite_conflicts.assert_awaited_once()
            sftp.upload_paths.assert_not_called()
            self.assertEqual(mock_exec.call_count, 1)
            self.assertIn("Upload cancelled: overwrite not confirmed.", status_messages)
        finally:
            tab.deleteLater()
            QApplication.processEvents()

    def test_sftp_download_conflict_prompts_once_and_proceeds_on_yes(self) -> None:
        sftp = MagicMock()
        sftp.find_download_overwrite_conflicts = AsyncMock(
            return_value=[OverwriteConflict("/remote/file.txt", "/tmp/file.txt")]
        )
        sftp.download_paths = AsyncMock(return_value=1)
        status_messages: list[str] = []
        tab = self._build_sftp_tab_for_transfers(sftp=sftp, status_messages=status_messages)
        try:
            with patch(
                "snakesh.ui.main_window.QMessageBox.exec",
                autospec=True,
                return_value=QMessageBox.Yes,
            ) as mock_exec:
                tab._download_remote_paths(["/remote/file.txt"])
            sftp.find_download_overwrite_conflicts.assert_awaited_once()
            sftp.download_paths.assert_awaited_once()
            self.assertEqual(mock_exec.call_count, 1)
            self.assertIn("Downloaded 1 item(s) to ", status_messages[0])
        finally:
            tab.deleteLater()
            QApplication.processEvents()

    def test_sftp_download_uses_progress_dialog_and_wires_callbacks(self) -> None:
        sftp = MagicMock()
        sftp.find_download_overwrite_conflicts = AsyncMock(return_value=[])
        sftp.download_paths = AsyncMock(return_value=1)
        status_messages: list[str] = []
        tab = self._build_sftp_tab_for_transfers(sftp=sftp, status_messages=status_messages)
        try:
            progress_dialog = MagicMock()
            progress_dialog.is_cancel_requested.return_value = False
            with (
                patch("snakesh.ui.main_window.UploadProgressDialog", return_value=progress_dialog) as mock_dialog,
                patch("snakesh.ui.main_window.QMessageBox.exec", autospec=True) as mock_exec,
            ):
                tab._download_remote_paths(["/remote/folder"])

            mock_dialog.assert_called_once_with(item_count=1, operation_label="Downloading", parent=tab)
            progress_dialog.show.assert_called_once_with()
            progress_dialog.close.assert_called_once_with()
            sftp.find_download_overwrite_conflicts.assert_awaited_once()
            sftp.download_paths.assert_awaited_once()
            download_kwargs = sftp.download_paths.await_args.kwargs
            self.assertIs(download_kwargs["progress_callback"], progress_dialog.update_progress)
            self.assertIs(download_kwargs["cancel_requested"], progress_dialog.is_cancel_requested)
            mock_exec.assert_not_called()
            self.assertIn("Downloaded 1 item(s) to ", status_messages[0])
        finally:
            tab.deleteLater()
            QApplication.processEvents()

    def test_sftp_upload_skips_prompt_when_overwrite_warnings_disabled(self) -> None:
        sftp = MagicMock()
        sftp.find_upload_overwrite_conflicts = AsyncMock(return_value=[])
        sftp.upload_paths = AsyncMock(return_value=1)
        tab = self._build_sftp_tab_for_transfers(sftp=sftp, should_confirm_overwrite=lambda: False)
        try:
            progress_dialog = MagicMock()
            progress_dialog.is_cancel_requested.return_value = False
            with (
                patch("snakesh.ui.main_window.UploadProgressDialog", return_value=progress_dialog),
                patch("snakesh.ui.main_window.QMessageBox.exec", autospec=True) as mock_exec,
            ):
                tab._upload_local_paths(["/tmp/local.txt"])
            sftp.find_upload_overwrite_conflicts.assert_not_called()
            sftp.upload_paths.assert_awaited_once()
            mock_exec.assert_not_called()
        finally:
            tab.deleteLater()
            QApplication.processEvents()

    def test_sftp_upload_skips_prompt_when_no_conflicts_exist(self) -> None:
        sftp = MagicMock()
        sftp.find_upload_overwrite_conflicts = AsyncMock(return_value=[])
        sftp.upload_paths = AsyncMock(return_value=1)
        tab = self._build_sftp_tab_for_transfers(sftp=sftp)
        try:
            progress_dialog = MagicMock()
            progress_dialog.is_cancel_requested.return_value = False
            with (
                patch("snakesh.ui.main_window.UploadProgressDialog", return_value=progress_dialog),
                patch("snakesh.ui.main_window.QMessageBox.exec", autospec=True) as mock_exec,
            ):
                tab._upload_local_paths(["/tmp/local.txt"])
            sftp.find_upload_overwrite_conflicts.assert_awaited_once()
            sftp.upload_paths.assert_awaited_once()
            mock_exec.assert_not_called()
        finally:
            tab.deleteLater()
            QApplication.processEvents()

    def test_sftp_batch_upload_allow_all_overwrites_suppresses_later_prompts(self) -> None:
        conflicts = [
            OverwriteConflict("/tmp/a.txt", "/remote/a.txt"),
            OverwriteConflict("/tmp/b.txt", "/remote/b.txt"),
            OverwriteConflict("/tmp/c.txt", "/remote/c.txt"),
        ]
        sftp = MagicMock()
        sftp.find_upload_overwrite_conflicts = AsyncMock(return_value=conflicts)
        sftp.upload_paths = AsyncMock(return_value=3)
        tab = self._build_sftp_tab_for_transfers(sftp=sftp)
        try:
            progress_dialog = MagicMock()
            progress_dialog.is_cancel_requested.return_value = False

            with (
                patch("snakesh.ui.main_window.UploadProgressDialog", return_value=progress_dialog),
                patch.object(tab, "_prompt_overwrite_conflict", return_value=(True, True)) as mock_prompt,
            ):
                tab._upload_local_paths(["/tmp/a.txt", "/tmp/b.txt", "/tmp/c.txt"])
            self.assertEqual(mock_prompt.call_count, 1)
            sftp.upload_paths.assert_awaited_once()
        finally:
            tab.deleteLater()
            QApplication.processEvents()

    def test_sftp_batch_upload_declining_later_conflict_cancels_transfer(self) -> None:
        conflicts = [
            OverwriteConflict("/tmp/a.txt", "/remote/a.txt"),
            OverwriteConflict("/tmp/b.txt", "/remote/b.txt"),
        ]
        sftp = MagicMock()
        sftp.find_upload_overwrite_conflicts = AsyncMock(return_value=conflicts)
        sftp.upload_paths = AsyncMock(return_value=2)
        tab = self._build_sftp_tab_for_transfers(sftp=sftp)
        try:
            progress_dialog = MagicMock()
            progress_dialog.is_cancel_requested.return_value = False
            with (
                patch("snakesh.ui.main_window.UploadProgressDialog", return_value=progress_dialog),
                patch(
                    "snakesh.ui.main_window.QMessageBox.exec",
                    autospec=True,
                    side_effect=[QMessageBox.Yes, QMessageBox.No],
                ) as mock_exec,
            ):
                tab._upload_local_paths(["/tmp/a.txt", "/tmp/b.txt"])
            self.assertEqual(mock_exec.call_count, 2)
            sftp.upload_paths.assert_not_called()
        finally:
            tab.deleteLater()
            QApplication.processEvents()

    def test_sftp_batch_allow_all_resets_between_transfers(self) -> None:
        conflicts = [
            OverwriteConflict("/tmp/a.txt", "/remote/a.txt"),
            OverwriteConflict("/tmp/b.txt", "/remote/b.txt"),
        ]
        sftp = MagicMock()
        sftp.find_upload_overwrite_conflicts = AsyncMock(return_value=conflicts)
        sftp.upload_paths = AsyncMock(return_value=2)
        tab = self._build_sftp_tab_for_transfers(sftp=sftp)
        try:
            progress_dialog = MagicMock()
            progress_dialog.is_cancel_requested.return_value = False

            with (
                patch("snakesh.ui.main_window.UploadProgressDialog", return_value=progress_dialog),
                patch.object(tab, "_prompt_overwrite_conflict", return_value=(True, True)) as mock_prompt,
            ):
                tab._upload_local_paths(["/tmp/a.txt", "/tmp/b.txt"])
                tab._upload_local_paths(["/tmp/a.txt", "/tmp/b.txt"])
            self.assertEqual(mock_prompt.call_count, 2)
            self.assertEqual(sftp.upload_paths.await_count, 2)
        finally:
            tab.deleteLater()
            QApplication.processEvents()

    def test_sftp_multi_conflict_prompt_adds_allow_all_checkbox(self) -> None:
        sftp = MagicMock()
        tab = self._build_sftp_tab_for_transfers(sftp=sftp)
        try:
            with (
                patch("snakesh.ui.main_window.QMessageBox.setCheckBox", autospec=True) as mock_set_checkbox,
                patch(
                    "snakesh.ui.main_window.QMessageBox.exec",
                    autospec=True,
                    return_value=QMessageBox.No,
                ),
            ):
                approved, allow_all = tab._prompt_overwrite_conflict(
                    OverwriteConflict("/tmp/a.txt", "/remote/a.txt"),
                    destination_label="remote",
                    remaining_count=1,
                )
            self.assertFalse(approved)
            self.assertFalse(allow_all)
            mock_set_checkbox.assert_called_once()
        finally:
            tab.deleteLater()
            QApplication.processEvents()

    def test_sftp_context_menus_show_rename_for_single_selection_only(self) -> None:
        session = _build_session("SFTP Host", "sess-sftp-rename-menu")
        session.protocol = Protocol.SFTP
        remote_entries = [
            SFTPEntry(name="a.txt", path="/remote/a.txt", is_dir=False, size=10),
            SFTPEntry(name="b.txt", path="/remote/b.txt", is_dir=False, size=20),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            (Path(temp_dir) / "local.txt").write_text("payload", encoding="utf-8")
            (Path(temp_dir) / "other.txt").write_text("payload", encoding="utf-8")
            tab = SFTPSessionTab(
                session=session,
                sftp=MagicMock(scan_directory=AsyncMock(return_value=("/remote", []))),
                initial_remote_dir="/remote",
                initial_remote_entries=remote_entries,
                initial_local_dir=temp_dir,
                password=None,
                execute_remote=lambda _session, current_password, _operation: (None, current_password),
                status_callback=lambda _message, _timeout_ms: None,
                should_confirm_delete=lambda: True,
                parent=self.window,
            )
            try:
                local_item = tab.local_file_tree.topLevelItem(0)
                self.assertIsNotNone(local_item)
                assert local_item is not None
                local_item.setSelected(True)
                tab.local_file_tree.setCurrentItem(local_item)
                QApplication.processEvents()

                with patch("snakesh.ui.main_window.QMenu", _MenuProbe):
                    _MenuProbe.reset()
                    tab._show_local_menu(tab.local_file_tree.rect().center())
                    local_actions = {action.text(): action for action in _MenuProbe.latest().actions()}

                self.assertIn("Rename...", local_actions)
                self.assertTrue(local_actions["Rename..."].enabled)

                first_remote = tab.remote_file_tree.topLevelItem(0)
                second_remote = tab.remote_file_tree.topLevelItem(1)
                self.assertIsNotNone(first_remote)
                self.assertIsNotNone(second_remote)
                assert first_remote is not None
                assert second_remote is not None
                first_remote.setSelected(True)
                second_remote.setSelected(True)
                QApplication.processEvents()

                with patch("snakesh.ui.main_window.QMenu", _MenuProbe):
                    _MenuProbe.reset()
                    tab._show_remote_menu(tab.remote_file_tree.rect().center())
                    remote_actions = {action.text(): action for action in _MenuProbe.latest().actions()}

                self.assertIn("Rename...", remote_actions)
                self.assertFalse(remote_actions["Rename..."].enabled)
            finally:
                tab.deleteLater()
                QApplication.processEvents()

    def test_sftp_context_menus_include_new_folder_without_selection(self) -> None:
        session = _build_session("SFTP Host", "sess-sftp-new-folder-menu")
        session.protocol = Protocol.SFTP
        with tempfile.TemporaryDirectory() as temp_dir:
            tab = SFTPSessionTab(
                session=session,
                sftp=MagicMock(scan_directory=AsyncMock(return_value=("/remote", []))),
                initial_remote_dir="/remote",
                initial_remote_entries=[],
                initial_local_dir=temp_dir,
                password=None,
                execute_remote=lambda _session, current_password, _operation: (None, current_password),
                status_callback=lambda _message, _timeout_ms: None,
                should_confirm_delete=lambda: True,
                parent=self.window,
            )
            try:
                with patch("snakesh.ui.main_window.QMenu", _MenuProbe):
                    _MenuProbe.reset()
                    tab._show_local_menu(tab.local_file_tree.rect().center())
                    local_actions = {action.text(): action for action in _MenuProbe.latest().actions()}

                    _MenuProbe.reset()
                    tab._show_remote_menu(tab.remote_file_tree.rect().center())
                    remote_actions = {action.text(): action for action in _MenuProbe.latest().actions()}

                self.assertIn("New Folder...", local_actions)
                self.assertTrue(local_actions["New Folder..."].enabled)
                self.assertIn("New Folder...", remote_actions)
                self.assertTrue(remote_actions["New Folder..."].enabled)
            finally:
                tab.deleteLater()
                QApplication.processEvents()

    def test_sftp_local_rename_updates_directory_listing(self) -> None:
        session = _build_session("SFTP Host", "sess-sftp-local-rename")
        session.protocol = Protocol.SFTP
        status_messages: list[str] = []
        with tempfile.TemporaryDirectory() as temp_dir:
            original = Path(temp_dir) / "old.txt"
            original.write_text("payload", encoding="utf-8")
            tab = SFTPSessionTab(
                session=session,
                sftp=MagicMock(scan_directory=AsyncMock(return_value=("/remote", []))),
                initial_remote_dir="/remote",
                initial_remote_entries=[],
                initial_local_dir=temp_dir,
                password=None,
                execute_remote=lambda _session, current_password, _operation: (None, current_password),
                status_callback=lambda message, _timeout_ms: status_messages.append(message),
                should_confirm_delete=lambda: True,
                parent=self.window,
            )
            try:
                item = tab.local_file_tree.topLevelItem(0)
                self.assertIsNotNone(item)
                assert item is not None
                item.setSelected(True)
                tab.local_file_tree.setCurrentItem(item)
                QApplication.processEvents()

                with patch(
                    "snakesh.ui.main_window.QInputDialog.getText",
                    return_value=("renamed.txt", True),
                ):
                    tab._rename_selected_local()

                self.assertFalse(original.exists())
                self.assertTrue((Path(temp_dir) / "renamed.txt").exists())
                self.assertIn("Renamed local item to renamed.txt.", status_messages)
            finally:
                tab.deleteLater()
                QApplication.processEvents()

    def test_sftp_local_new_folder_creates_directory_and_refreshes_listing(self) -> None:
        session = _build_session("SFTP Host", "sess-sftp-local-new-folder")
        session.protocol = Protocol.SFTP
        status_messages: list[str] = []
        with tempfile.TemporaryDirectory() as temp_dir:
            tab = SFTPSessionTab(
                session=session,
                sftp=MagicMock(scan_directory=AsyncMock(return_value=("/remote", []))),
                initial_remote_dir="/remote",
                initial_remote_entries=[],
                initial_local_dir=temp_dir,
                password=None,
                execute_remote=lambda _session, current_password, _operation: (None, current_password),
                status_callback=lambda message, _timeout_ms: status_messages.append(message),
                should_confirm_delete=lambda: True,
                parent=self.window,
            )
            try:
                with patch("snakesh.ui.main_window.QInputDialog.getText", return_value=("logs", True)):
                    tab._create_local_directory()

                created_dir = Path(temp_dir) / "logs"
                self.assertTrue(created_dir.is_dir())
                labels = [tab.local_file_tree.topLevelItem(index).text(0) for index in range(tab.local_file_tree.topLevelItemCount())]
                self.assertIn("logs/", labels)
                self.assertIn("Created local folder logs.", status_messages)
            finally:
                tab.deleteLater()
                QApplication.processEvents()

    def test_sftp_remote_rename_refreshes_after_success(self) -> None:
        session = _build_session("SFTP Host", "sess-sftp-remote-rename")
        session.protocol = Protocol.SFTP
        sftp = MagicMock()
        sftp.remote_path_exists = AsyncMock(return_value=False)
        sftp.rename_path = AsyncMock(return_value="/remote/renamed.txt")
        sftp.scan_directory = AsyncMock(return_value=("/remote", []))
        status_messages: list[str] = []
        tab = SFTPSessionTab(
            session=session,
            sftp=sftp,
            initial_remote_dir="/remote",
            initial_remote_entries=[SFTPEntry(name="old.txt", path="/remote/old.txt", is_dir=False, size=5)],
            initial_local_dir="~",
            password=None,
            execute_remote=lambda _session, current_password, operation: (
                operation(current_password, False),
                current_password,
            ),
            status_callback=lambda message, _timeout_ms: status_messages.append(message),
            should_confirm_delete=lambda: True,
            parent=self.window,
        )
        try:
            item = tab.remote_file_tree.topLevelItem(0)
            self.assertIsNotNone(item)
            assert item is not None
            item.setSelected(True)
            tab.remote_file_tree.setCurrentItem(item)
            QApplication.processEvents()

            with (
                patch("snakesh.ui.main_window.QInputDialog.getText", return_value=("renamed.txt", True)),
                patch.object(tab, "_refresh_remote_directory") as mock_refresh,
            ):
                tab._rename_selected_remote()

            sftp.remote_path_exists.assert_awaited_once()
            sftp.rename_path.assert_awaited_once()
            rename_kwargs = sftp.rename_path.await_args.kwargs
            self.assertFalse(rename_kwargs["replace"])
            mock_refresh.assert_called_once_with()
            self.assertIn("Renamed remote item to renamed.txt.", status_messages)
        finally:
            tab.deleteLater()
            QApplication.processEvents()

    def test_sftp_remote_new_folder_calls_client_and_refreshes(self) -> None:
        session = _build_session("SFTP Host", "sess-sftp-remote-new-folder")
        session.protocol = Protocol.SFTP
        sftp = MagicMock()
        sftp.create_directory = AsyncMock(return_value="/remote/logs")
        sftp.scan_directory = AsyncMock(return_value=("/remote", []))
        status_messages: list[str] = []
        tab = SFTPSessionTab(
            session=session,
            sftp=sftp,
            initial_remote_dir="/remote",
            initial_remote_entries=[],
            initial_local_dir="~",
            password=None,
            execute_remote=lambda _session, current_password, operation: (
                operation(current_password, False),
                current_password,
            ),
            status_callback=lambda message, _timeout_ms: status_messages.append(message),
            should_confirm_delete=lambda: True,
            parent=self.window,
        )
        try:
            with (
                patch("snakesh.ui.main_window.QInputDialog.getText", return_value=("logs", True)),
                patch.object(tab, "_refresh_remote_directory") as mock_refresh,
            ):
                tab._create_remote_directory()

            sftp.create_directory.assert_awaited_once()
            args = sftp.create_directory.await_args.args
            kwargs = sftp.create_directory.await_args.kwargs
            self.assertIs(args[0], session)
            self.assertEqual(args[1], "/remote")
            self.assertEqual(args[2], "logs")
            self.assertIsNone(kwargs["password"])
            self.assertFalse(kwargs["trust_unknown"])
            mock_refresh.assert_called_once_with()
            self.assertIn("Created remote folder logs.", status_messages)
        finally:
            tab.deleteLater()
            QApplication.processEvents()

    def test_sftp_tab_f5_refreshes_both_panes_from_child_widgets(self) -> None:
        session = _build_session("SFTP Host", "sess-sftp-f5-refresh")
        session.protocol = Protocol.SFTP
        tab = SFTPSessionTab(
            session=session,
            sftp=MagicMock(scan_directory=AsyncMock(return_value=("/remote", []))),
            initial_remote_dir="/remote",
            initial_remote_entries=[],
            initial_local_dir="~",
            password=None,
            execute_remote=lambda _session, current_password, _operation: (None, current_password),
            status_callback=lambda _message, _timeout_ms: None,
            should_confirm_delete=lambda: True,
            parent=self.window,
        )
        try:
            self.window._add_session_tab(session, tab, "SFTP")
            QApplication.processEvents()

            with (
                patch.object(tab, "_refresh_local_directory") as mock_local_refresh,
                patch.object(tab, "_refresh_remote_directory") as mock_remote_refresh,
            ):
                for widget in (tab.local_path_input, tab.local_file_tree, tab.remote_path_input, tab.remote_file_tree):
                    with self.subTest(widget=type(widget).__name__):
                        widget.setFocus(Qt.OtherFocusReason)
                        QApplication.processEvents()
                        QTest.keyClick(widget, Qt.Key_F5)
                        QApplication.processEvents()

            self.assertEqual(mock_local_refresh.call_count, 4)
            self.assertEqual(mock_remote_refresh.call_count, 4)
        finally:
            tab.deleteLater()
            QApplication.processEvents()

    def test_sftp_remote_rename_prompts_before_replace(self) -> None:
        session = _build_session("SFTP Host", "sess-sftp-remote-replace")
        session.protocol = Protocol.SFTP
        sftp = MagicMock()
        sftp.remote_path_exists = AsyncMock(return_value=True)
        sftp.rename_path = AsyncMock(return_value="/remote/renamed.txt")
        sftp.scan_directory = AsyncMock(return_value=("/remote", []))
        tab = SFTPSessionTab(
            session=session,
            sftp=sftp,
            initial_remote_dir="/remote",
            initial_remote_entries=[SFTPEntry(name="old.txt", path="/remote/old.txt", is_dir=False, size=5)],
            initial_local_dir="~",
            password=None,
            execute_remote=lambda _session, current_password, operation: (
                operation(current_password, False),
                current_password,
            ),
            status_callback=lambda _message, _timeout_ms: None,
            should_confirm_delete=lambda: True,
            parent=self.window,
        )
        try:
            with (
                patch("snakesh.ui.main_window.QInputDialog.getText", return_value=("renamed.txt", True)),
                patch.object(tab, "_confirm_rename_replace", return_value=True) as mock_confirm,
                patch.object(tab, "_refresh_remote_directory"),
            ):
                tab._rename_remote_path("/remote/old.txt")

            mock_confirm.assert_called_once()
            self.assertTrue(sftp.rename_path.await_args.kwargs["replace"])
        finally:
            tab.deleteLater()
            QApplication.processEvents()

    def test_sftp_local_symlink_directory_double_click_navigates_into_link(self) -> None:
        if not hasattr(os, "symlink"):
            self.skipTest("symlinks are not supported on this platform")
        session = _build_session("SFTP Host", "sess-sftp-local-link-nav")
        session.protocol = Protocol.SFTP
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target_dir = root / "target"
            target_dir.mkdir()
            link_dir = root / "linkdir"
            try:
                os.symlink(target_dir, link_dir)
            except OSError as exc:
                self.skipTest(f"unable to create symlink: {exc}")

            tab = SFTPSessionTab(
                session=session,
                sftp=MagicMock(scan_directory=AsyncMock(return_value=("/remote", []))),
                initial_remote_dir="/remote",
                initial_remote_entries=[],
                initial_local_dir=temp_dir,
                password=None,
                execute_remote=lambda _session, current_password, _operation: (None, current_password),
                status_callback=lambda _message, _timeout_ms: None,
                should_confirm_delete=lambda: True,
                parent=self.window,
            )
            try:
                item = next(
                    tab.local_file_tree.topLevelItem(index)
                    for index in range(tab.local_file_tree.topLevelItemCount())
                    if tab.local_file_tree.topLevelItem(index).text(0) == "linkdir/"
                )
                tab.local_file_tree.itemDoubleClicked.emit(item, 0)
                QApplication.processEvents()
                self.assertEqual(tab.local_path_input.text(), str(link_dir))
            finally:
                tab.deleteLater()
                QApplication.processEvents()

    def test_sftp_local_symlink_delete_unlinks_only_the_link(self) -> None:
        if not hasattr(os, "symlink"):
            self.skipTest("symlinks are not supported on this platform")
        session = _build_session("SFTP Host", "sess-sftp-local-link-delete")
        session.protocol = Protocol.SFTP
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target_dir = root / "target"
            target_dir.mkdir()
            (target_dir / "keep.txt").write_text("payload", encoding="utf-8")
            link_dir = root / "linkdir"
            try:
                os.symlink(target_dir, link_dir)
            except OSError as exc:
                self.skipTest(f"unable to create symlink: {exc}")

            tab = SFTPSessionTab(
                session=session,
                sftp=MagicMock(scan_directory=AsyncMock(return_value=("/remote", []))),
                initial_remote_dir="/remote",
                initial_remote_entries=[],
                initial_local_dir=temp_dir,
                password=None,
                execute_remote=lambda _session, current_password, _operation: (None, current_password),
                status_callback=lambda _message, _timeout_ms: None,
                should_confirm_delete=lambda: True,
                parent=self.window,
            )
            try:
                with patch.object(tab, "_confirm_delete", return_value=True):
                    tab._delete_local_paths([str(link_dir)])
                self.assertTrue(target_dir.exists())
                self.assertFalse(link_dir.exists())
                self.assertTrue((target_dir / "keep.txt").exists())
            finally:
                tab.deleteLater()
                QApplication.processEvents()

    def test_sftp_remote_symlink_directory_double_click_navigates(self) -> None:
        session = _build_session("SFTP Host", "sess-sftp-remote-link-nav")
        session.protocol = Protocol.SFTP
        tab = SFTPSessionTab(
            session=session,
            sftp=MagicMock(scan_directory=AsyncMock(return_value=("/remote", []))),
            initial_remote_dir="/remote",
            initial_remote_entries=[
                SFTPEntry(name="linkdir", path="/remote/linkdir", is_dir=True, size=0, is_symlink=True)
            ],
            initial_local_dir="~",
            password=None,
            execute_remote=lambda _session, current_password, _operation: (None, current_password),
            status_callback=lambda _message, _timeout_ms: None,
            should_confirm_delete=lambda: True,
            parent=self.window,
        )
        try:
            item = tab.remote_file_tree.topLevelItem(0)
            self.assertIsNotNone(item)
            assert item is not None
            with patch.object(tab, "_set_remote_directory") as mock_set_remote_directory:
                tab.remote_file_tree.itemDoubleClicked.emit(item, 0)
            mock_set_remote_directory.assert_called_once_with("/remote/linkdir")
        finally:
            tab.deleteLater()
            QApplication.processEvents()

    def test_sftp_async_remote_load_progressively_updates_then_sorts_final_listing(self) -> None:
        session = _build_session("SFTP Host", "sess-sftp-async-load")
        session.protocol = Protocol.SFTP

        class _SlowSFTP:
            async def scan_directory(
                self,
                _session,
                path: str,
                *,
                password=None,
                trust_unknown=False,
                batch_size=250,
                batch_callback=None,
                cancel_requested=None,
            ):
                self.last_args = (path, password, trust_unknown, batch_size)
                await asyncio.sleep(0.02)
                if batch_callback is not None:
                    batch_callback(path, [SFTPEntry(name="z.txt", path=f"{path}/z.txt", is_dir=False, size=2)])
                await asyncio.sleep(0.03)
                if batch_callback is not None:
                    batch_callback(path, [SFTPEntry(name="alpha", path=f"{path}/alpha", is_dir=True, size=0)])
                return path, [
                    SFTPEntry(name="z.txt", path=f"{path}/z.txt", is_dir=False, size=2),
                    SFTPEntry(name="alpha", path=f"{path}/alpha", is_dir=True, size=0),
                ]

        tab = SFTPSessionTab(
            session=session,
            sftp=_SlowSFTP(),
            initial_remote_dir="/remote",
            initial_remote_entries=[],
            initial_local_dir="~",
            password=None,
            execute_remote=lambda _session, current_password, _operation: (None, current_password),
            status_callback=lambda _message, _timeout_ms: None,
            should_confirm_delete=lambda: True,
            parent=self.window,
        )
        try:
            tab.load_remote_directory("/remote")
            self.assertEqual(tab.remote_file_tree.topLevelItem(0).text(0), "Loading /remote...")

            self._wait_for(lambda: tab.remote_file_tree.topLevelItemCount() == 1 and tab.remote_file_tree.topLevelItem(0).text(0) == "z.txt")
            self._wait_for(lambda: tab.remote_file_tree.topLevelItemCount() == 2 and tab.remote_file_tree.topLevelItem(0).text(0) == "alpha/")
            self.assertEqual(tab.remote_nav_tree.topLevelItem(1).text(0), "alpha/")
        finally:
            tab.close()
            tab.deleteLater()
            QApplication.processEvents()

    def test_sftp_async_remote_load_ignores_stale_batches_and_finishes_current_request(self) -> None:
        session = _build_session("SFTP Host", "sess-sftp-stale-load")
        session.protocol = Protocol.SFTP
        tab = SFTPSessionTab(
            session=session,
            sftp=MagicMock(scan_directory=AsyncMock(return_value=("/remote", []))),
            initial_remote_dir="/remote",
            initial_remote_entries=[],
            initial_local_dir="~",
            password=None,
            execute_remote=lambda _session, current_password, _operation: (None, current_password),
            status_callback=lambda _message, _timeout_ms: None,
            should_confirm_delete=lambda: True,
            parent=self.window,
        )
        try:
            tab._remote_load_request_id = 2
            tab._show_remote_loading_state("Loading /new...")
            stale_entry = SFTPEntry(name="stale.txt", path="/old/stale.txt", is_dir=False, size=1)
            current_entry = SFTPEntry(name="current.txt", path="/new/current.txt", is_dir=False, size=1)

            tab._handle_remote_load_batch(1, "/old", [stale_entry])
            self.assertEqual(tab.remote_file_tree.topLevelItem(0).text(0), "Loading /new...")

            tab._handle_remote_load_batch(2, "/new", [current_entry])
            self.assertEqual(tab.remote_file_tree.topLevelItem(0).text(0), "current.txt")

            tab._handle_remote_load_finished(1, True, "/old", "", False, False, False)
            self.assertEqual(tab.remote_path_input.text(), "/new")

            tab._handle_remote_load_finished(2, True, "/new", "", False, False, False)
            self.assertEqual(tab.remote_path_input.text(), "/new")
            self.assertEqual(tab.remote_file_tree.topLevelItem(0).text(0), "current.txt")
        finally:
            tab.deleteLater()
            QApplication.processEvents()

    def test_open_sftp_tab_creates_tab_before_async_directory_load_finishes(self) -> None:
        session = _build_session("SFTP Host", "sess-sftp-open-async")
        session.protocol = Protocol.SFTP
        self.session_service.add_or_update(session)

        class _SlowSFTP:
            async def scan_directory(
                self,
                _session,
                path: str,
                *,
                password=None,
                trust_unknown=False,
                batch_size=250,
                batch_callback=None,
                cancel_requested=None,
            ):
                await asyncio.sleep(0.04)
                if batch_callback is not None:
                    batch_callback(path, [SFTPEntry(name="done.txt", path=f"{path}/done.txt", is_dir=False, size=4)])
                return path, [SFTPEntry(name="done.txt", path=f"{path}/done.txt", is_dir=False, size=4)]

        original_sftp = self.window._sftp
        self.window._sftp = _SlowSFTP()
        try:
            with patch.object(self.window, "_connect_sftp_with_prompts", return_value=("/remote", None)):
                self.window._open_sftp_tab(session)

            widget = self.window.tabs.widget(self.window.tabs.count() - 1)
            self.assertIsInstance(widget, SFTPSessionTab)
            assert isinstance(widget, SFTPSessionTab)
            self.assertEqual(widget.remote_file_tree.topLevelItem(0).text(0), "Loading /remote...")
            self._wait_for(lambda: widget.remote_file_tree.topLevelItem(0).text(0) == "done.txt")
        finally:
            self.window._sftp = original_sftp

    @staticmethod
    def _find_folder_item(window: MainWindow, folder_path: str):
        stack = [window.session_tree.topLevelItem(i) for i in range(window.session_tree.topLevelItemCount())]
        while stack:
            item = stack.pop()
            if item is None:
                continue
            if (
                item.data(0, TREE_KIND_ROLE) == "folder"
                and item.data(0, TREE_FOLDER_PATH_ROLE) == folder_path
            ):
                return item
            for index in range(item.childCount()):
                stack.append(item.child(index))
        return None

    @staticmethod
    def _find_session_item(window: MainWindow, session_id: str):
        stack = [window.session_tree.topLevelItem(i) for i in range(window.session_tree.topLevelItemCount())]
        while stack:
            item = stack.pop()
            if item is None:
                continue
            if (
                item.data(0, TREE_KIND_ROLE) == "session"
                and item.data(0, TREE_SESSION_ID_ROLE) == session_id
            ):
                return item
            for index in range(item.childCount()):
                stack.append(item.child(index))
        return None


if __name__ == "__main__":
    unittest.main()
