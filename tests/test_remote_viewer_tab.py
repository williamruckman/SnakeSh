from __future__ import annotations

import os
import subprocess
import unittest
from unittest.mock import patch

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication

from snakesh.core.models import Protocol, Session
from snakesh.services.settings_service import AppSettings
from snakesh.ui.main_window import MainWindow, RemoteViewerTab


class _StubSessionService:
    def all(self) -> list[Session]:
        return []

    def all_folders(self) -> list[str]:
        return ["Default"]

    def add_or_update(self, session: Session) -> None:
        _ = session

    @staticmethod
    def normalize_folder_path(folder_path: str) -> str:
        cleaned = folder_path.replace("\\", "/").strip("/")
        return cleaned or "Default"


class _StubSettingsService:
    def __init__(self) -> None:
        self._settings = AppSettings.defaults()

    def load(self) -> AppSettings:
        return self._settings

    def save(self, settings: AppSettings) -> None:
        self._settings = settings


def _build_vnc_session() -> Session:
    return Session(
        id="sess-vnc-remote",
        name="VNC Remote",
        host="192.0.2.50",
        protocol=Protocol.VNC,
        port=5900,
        username="tester",
    )


def _build_rdp_session() -> Session:
    return Session(
        id="sess-rdp-remote",
        name="RDP Remote",
        host="192.0.2.75",
        protocol=Protocol.RDP,
        port=3389,
        username="tester",
    )


def _build_nomachine_session() -> Session:
    return Session(
        id="sess-nx-remote",
        name="NoMachine Remote",
        host="192.0.2.90",
        protocol=Protocol.NOMACHINE,
        port=4000,
        username="tester",
    )


class RemoteViewerTabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls._app = QApplication.instance() or QApplication([])

    def _build_tab(self) -> RemoteViewerTab:
        session = _build_vnc_session()

        def detached_builder() -> tuple[list[str], str, dict[str, str] | None]:
            return ["vncviewer", session.host], "TigerVNC Viewer", None

        with patch("snakesh.ui.main_window.platform.system", return_value="Linux"):
            tab = RemoteViewerTab(
                session=session,
                protocol_name="VNC",
                detached_command_builder=detached_builder,
                linux_x11_reparent_embed=True,
            )
        return tab

    def test_attach_dispatches_to_linux_reparent_handler(self) -> None:
        tab = self._build_tab()
        with (
            patch("snakesh.ui.main_window.platform.system", return_value="Linux"),
            patch.object(tab, "_linux_x11_reparent_support_status", return_value=(True, "")),
            patch.object(tab, "_attach_linux_x11_reparent", return_value=(True, "ok")) as mock_attach,
        ):
            ok, message = tab.attach_viewer()

        self.assertTrue(ok)
        self.assertEqual(message, "ok")
        mock_attach.assert_called_once_with()
        tab.deleteLater()

    def test_detach_viewer_writes_optional_stdin_payload(self) -> None:
        session = _build_rdp_session()

        def detached_builder() -> tuple[list[str], str, dict[str, str] | None, str | None]:
            return ["xfreerdp", "/v:192.0.2.75", "/from-stdin:force"], "FreeRDP", None, "secret\n"

        with patch("snakesh.ui.main_window.platform.system", return_value="Linux"):
            tab = RemoteViewerTab(
                session=session,
                protocol_name="RDP",
                detached_command_builder=detached_builder,
            )

        fake_process = unittest.mock.Mock()
        fake_process.stdin = unittest.mock.Mock()
        with patch("snakesh.ui.main_window.subprocess.Popen", return_value=fake_process) as mock_popen:
            ok, _message = tab.detach_viewer()

        self.assertTrue(ok)
        self.assertEqual(mock_popen.call_args.kwargs["stdin"], subprocess.PIPE)
        self.assertTrue(mock_popen.call_args.kwargs["text"])
        fake_process.stdin.write.assert_called_once_with("secret\n")
        tab.deleteLater()

    def test_detached_state_reports_vnc_detached_only_reason(self) -> None:
        tab = self._build_tab()
        tab._mode = "detached"
        tab._viewer_name = "TigerVNC Viewer"

        with (
            patch("snakesh.ui.main_window.platform.system", return_value="Linux"),
            patch.object(
                tab,
                "_linux_x11_reparent_support_status",
                return_value=(False, "Attach mode requires an X11 session; Wayland is currently active."),
            ),
        ):
            tab._update_state_text()

        self.assertIn("VNC supports detached mode only", tab._state_label.text())
        self.assertIn("VNC sessions run in detached mode only", tab._hint_label.text())
        tab.deleteLater()

    def test_closed_state_hint_reconnects_from_session_list(self) -> None:
        tab = self._build_tab()
        tab._mode = "idle"
        tab._last_exit_mode = "detached"
        tab._last_exit_code = 0
        tab._update_state_text()

        self.assertIn("session list", tab._hint_label.text().lower())
        self.assertNotIn("context menu", tab._hint_label.text().lower())
        tab.deleteLater()

    def test_windows_nomachine_detached_handoff_does_not_mark_closed(self) -> None:
        session = _build_nomachine_session()

        def detached_builder() -> tuple[list[str], str, dict[str, str] | None]:
            return ["nxplayer.exe", "--session", "dummy.nxs"], "NoMachine Player", None

        with patch("snakesh.ui.main_window.platform.system", return_value="Windows"):
            tab = RemoteViewerTab(
                session=session,
                protocol_name="NoMachine",
                detached_command_builder=detached_builder,
            )

        fake_process = unittest.mock.Mock()
        fake_process.poll.return_value = 0
        tab._mode = "detached"
        tab._viewer_name = "NoMachine Player"
        tab._detached_process = fake_process

        with (
            patch("snakesh.ui.main_window.platform.system", return_value="Windows"),
            patch.object(tab, "_is_windows_process_name_running", return_value=True),
        ):
            tab._watch_process_state()

        self.assertEqual(tab._mode, "detached")
        self.assertIsNone(tab._detached_process)
        self.assertFalse(bool(tab.property("remote_viewer_closed")))
        self.assertIn("external client process", tab._hint_label.text().lower())
        tab.deleteLater()

    def test_macos_nomachine_detached_handoff_does_not_mark_closed(self) -> None:
        session = _build_nomachine_session()

        def detached_builder() -> tuple[list[str], str, dict[str, str] | None]:
            return ["/Applications/NoMachine.app/Contents/MacOS/nxplayer", "--session", "dummy.nxs"], "NoMachine Player", None

        with patch("snakesh.ui.main_window.platform.system", return_value="Darwin"):
            tab = RemoteViewerTab(
                session=session,
                protocol_name="NoMachine",
                detached_command_builder=detached_builder,
            )

        fake_process = unittest.mock.Mock()
        fake_process.poll.return_value = 0
        tab._mode = "detached"
        tab._viewer_name = "NoMachine Player"
        tab._detached_process = fake_process

        with patch("snakesh.ui.main_window.platform.system", return_value="Darwin"):
            tab._watch_process_state()

        self.assertEqual(tab._mode, "detached")
        self.assertIsNone(tab._detached_process)
        self.assertFalse(bool(tab.property("remote_viewer_closed")))
        self.assertIn("external client process", tab._hint_label.text().lower())
        tab.deleteLater()

    def test_linux_support_status_mentions_xdotool_when_missing(self) -> None:
        tab = self._build_tab()
        with (
            patch("snakesh.ui.main_window.platform.system", return_value="Linux"),
            patch("snakesh.ui.main_window.QApplication.platformName", return_value="xcb"),
            patch("snakesh.ui.main_window.shutil.which", return_value=None),
            patch.dict(os.environ, {"DISPLAY": ":0", "XDG_SESSION_TYPE": "x11"}, clear=False),
        ):
            supported, reason = tab._linux_x11_reparent_support_status()

        self.assertFalse(supported)
        self.assertIn("xdotool", reason)
        tab.deleteLater()

    def test_find_linux_candidates_scans_process_tree_pids(self) -> None:
        tab = self._build_tab()

        def fake_capture(*args: str) -> str:
            if args == ("search", "--onlyvisible", "--pid", "4200"):
                return ""
            if args == ("search", "--onlyvisible", "--pid", "4201"):
                return "101\n"
            return ""

        with (
            patch("snakesh.ui.main_window.platform.system", return_value="Linux"),
            patch.object(tab, "_linux_process_tree_pids", return_value={4200, 4201}),
            patch.object(tab, "_run_xdotool_capture", side_effect=fake_capture),
            patch.object(tab, "_linux_window_metadata", return_value=("Vncviewer", "VNC", 640000)),
        ):
            candidates = tab._find_linux_candidates(4200)

        self.assertEqual(candidates, [101])
        tab.deleteLater()

    def test_find_linux_candidates_prefers_top_level_windows(self) -> None:
        tab = self._build_tab()

        def fake_capture(*args: str) -> str:
            if args == ("search", "--onlyvisible", "--pid", "7000"):
                return "301\n302\n"
            return ""

        def fake_metadata(window_id: int) -> tuple[str, str, int]:
            if window_id == 301:
                return "Vncviewer", "Main", 600000
            return "Vncviewer", "Child", 800000

        def fake_parent(window_id: int) -> int | None:
            if window_id == 301:
                return 999
            if window_id == 302:
                return 301
            return None

        with (
            patch("snakesh.ui.main_window.platform.system", return_value="Linux"),
            patch.object(tab, "_linux_process_tree_pids", return_value={7000}),
            patch.object(tab, "_run_xdotool_capture", side_effect=fake_capture),
            patch.object(tab, "_linux_window_metadata", side_effect=fake_metadata),
            patch.object(tab, "_linux_window_parent", side_effect=fake_parent),
        ):
            candidates = tab._find_linux_candidates(7000)

        self.assertEqual(candidates, [301])
        tab.deleteLater()

    def test_wait_for_linux_window_candidates_does_not_pump_qt_events(self) -> None:
        tab = self._build_tab()
        with (
            patch("snakesh.ui.main_window.platform.system", return_value="Linux"),
            patch("snakesh.ui.main_window.time.time", side_effect=[0.0, 0.0, 0.1]),
            patch("snakesh.ui.main_window.time.sleep"),
            patch.object(tab, "_find_linux_candidates", side_effect=[[], [101]]),
            patch("snakesh.ui.main_window.QApplication.processEvents") as mock_process_events,
        ):
            candidates = tab._wait_for_linux_window_candidates(7000, timeout_seconds=1.0)

        self.assertEqual(candidates, [101])
        mock_process_events.assert_not_called()
        tab.deleteLater()

    def test_linux_window_metadata_uses_getwindowclass_first(self) -> None:
        tab = self._build_tab()

        def fake_capture(*args: str) -> str:
            if args == ("getwindowclass", "4242"):
                return "Vncviewer"
            if args == ("getwindowname", "4242"):
                return "VNC Session"
            if args == ("getwindowgeometry", "--shell", "4242"):
                return "WIDTH=100\nHEIGHT=200"
            return ""

        with patch.object(tab, "_run_xdotool_capture", side_effect=fake_capture):
            class_name, title, area = tab._linux_window_metadata(4242)

        self.assertEqual(class_name, "Vncviewer")
        self.assertEqual(title, "VNC Session")
        self.assertEqual(area, 20000)
        tab.deleteLater()

    def test_prepare_linux_embedded_command_forces_remote_resize_off(self) -> None:
        tab = self._build_tab()
        with patch.object(tab, "_embed_target_size", return_value=(1280, 720)):
            cleaned = tab._prepare_linux_embedded_command(
                ["vncviewer", "-RemoteResize=1", "-FullScreen", "-geometry", "1024x768", "host:1"]
            )
        self.assertIn("-RemoteResize=0", cleaned)
        self.assertIn("-geometry", cleaned)
        self.assertIn("1280x720", cleaned)
        self.assertNotIn("-RemoteResize=1", cleaned)
        self.assertNotIn("-FullScreen", cleaned)
        tab.deleteLater()

    def test_linux_resize_failures_trigger_fallback(self) -> None:
        tab = self._build_tab()
        tab._mode = "embedded"
        tab._embedded_window_handle = 123
        with (
            patch("snakesh.ui.main_window.platform.system", return_value="Linux"),
            patch.object(tab, "_run_xdotool", return_value=False),
            patch.object(tab, "_fallback_linux_embedded_to_detached") as mock_fallback,
        ):
            tab._resize_linux_x11_embedded_window()
            tab._resize_linux_x11_embedded_window()
            tab._resize_linux_x11_embedded_window()

        mock_fallback.assert_called_once()
        tab.deleteLater()

    def test_attach_falls_back_when_post_attach_health_check_fails(self) -> None:
        tab = self._build_tab()
        fake_process = unittest.mock.Mock()
        fake_process.pid = 4242

        with (
            patch("snakesh.ui.main_window.platform.system", return_value="Linux"),
            patch.object(tab, "_linux_x11_reparent_support_status", return_value=(True, "")),
            patch.object(tab, "_prepare_linux_embedded_command", return_value=["vncviewer", "host:1"]),
            patch("snakesh.ui.main_window.subprocess.Popen", return_value=fake_process),
            patch.object(tab, "_wait_for_linux_window_candidates", return_value=[111]),
            patch.object(tab, "_set_linux_window_embedded", return_value=True),
            patch.object(tab, "_verify_linux_embedded_window_health", return_value=False),
            patch.object(
                tab,
                "_fallback_linux_process_to_detached",
                return_value=(True, "Embedded mode is unstable for this viewer/session; switched to detached mode."),
            ) as mock_fallback,
        ):
            ok, _message = tab.attach_viewer()

        self.assertTrue(ok)
        mock_fallback.assert_called_once()
        tab.deleteLater()

    def test_sync_dispatches_to_linux_parent_rdp_handler(self) -> None:
        session = _build_rdp_session()

        def detached_builder() -> tuple[list[str], str, dict[str, str] | None]:
            return ["xfreerdp", "/v:192.0.2.75"], "FreeRDP", None

        def embedded_builder(_parent_window_id: int) -> tuple[list[str], str, dict[str, str] | None]:
            return ["xfreerdp", "/v:192.0.2.75"], "FreeRDP", None

        with patch("snakesh.ui.main_window.platform.system", return_value="Linux"):
            tab = RemoteViewerTab(
                session=session,
                protocol_name="RDP",
                detached_command_builder=detached_builder,
                embedded_command_builder=embedded_builder,
            )

        process = unittest.mock.Mock()
        process.poll.return_value = None
        with (
            patch("snakesh.ui.main_window.platform.system", return_value="Linux"),
            patch.object(tab, "_sync_linux_parent_rdp_embedded_window") as mock_sync,
        ):
            tab._sync_embedded_window(process)

        mock_sync.assert_called_once_with(process)
        tab.deleteLater()

    def test_resize_dispatches_to_linux_parent_rdp_handler(self) -> None:
        session = _build_rdp_session()

        def detached_builder() -> tuple[list[str], str, dict[str, str] | None]:
            return ["xfreerdp", "/v:192.0.2.75"], "FreeRDP", None

        def embedded_builder(_parent_window_id: int) -> tuple[list[str], str, dict[str, str] | None]:
            return ["xfreerdp", "/v:192.0.2.75"], "FreeRDP", None

        with patch("snakesh.ui.main_window.platform.system", return_value="Linux"):
            tab = RemoteViewerTab(
                session=session,
                protocol_name="RDP",
                detached_command_builder=detached_builder,
                embedded_command_builder=embedded_builder,
            )

        with (
            patch("snakesh.ui.main_window.platform.system", return_value="Linux"),
            patch.object(tab, "_resize_linux_parent_rdp_embedded_window") as mock_resize,
        ):
            tab._resize_embedded_window()

        mock_resize.assert_called_once_with()
        tab.deleteLater()

    def test_linux_parent_rdp_resize_uses_xdotool(self) -> None:
        session = _build_rdp_session()

        def detached_builder() -> tuple[list[str], str, dict[str, str] | None]:
            return ["xfreerdp", "/v:192.0.2.75"], "FreeRDP", None

        def embedded_builder(_parent_window_id: int) -> tuple[list[str], str, dict[str, str] | None]:
            return ["xfreerdp", "/v:192.0.2.75"], "FreeRDP", None

        with patch("snakesh.ui.main_window.platform.system", return_value="Linux"):
            tab = RemoteViewerTab(
                session=session,
                protocol_name="RDP",
                detached_command_builder=detached_builder,
                embedded_command_builder=embedded_builder,
            )

        tab._mode = "embedded"
        tab._embedded_window_handle = 1234
        with (
            patch("snakesh.ui.main_window.platform.system", return_value="Linux"),
            patch.object(tab, "_is_linux_window", return_value=True),
            patch.object(tab, "_embed_target_size", return_value=(1440, 900)),
            patch.object(tab, "_linux_window_size", side_effect=[(1200, 700), (1440, 900)]),
            patch.object(tab, "_run_xdotool", return_value=True) as mock_xdotool,
        ):
            ok = tab._resize_linux_parent_rdp_embedded_window()

        self.assertTrue(ok)
        mock_xdotool.assert_any_call("windowsize", "1234", "1440", "900")
        tab.deleteLater()

    def test_linux_parent_rdp_resize_failures_trigger_fallback(self) -> None:
        session = _build_rdp_session()

        def detached_builder() -> tuple[list[str], str, dict[str, str] | None]:
            return ["xfreerdp", "/v:192.0.2.75"], "FreeRDP", None

        def embedded_builder(_parent_window_id: int) -> tuple[list[str], str, dict[str, str] | None]:
            return ["xfreerdp", "/v:192.0.2.75"], "FreeRDP", None

        with patch("snakesh.ui.main_window.platform.system", return_value="Linux"):
            tab = RemoteViewerTab(
                session=session,
                protocol_name="RDP",
                detached_command_builder=detached_builder,
                embedded_command_builder=embedded_builder,
            )

        tab._mode = "embedded"
        tab._embedded_window_handle = 1234
        with (
            patch("snakesh.ui.main_window.platform.system", return_value="Linux"),
            patch.object(tab, "_is_linux_window", return_value=True),
            patch.object(tab, "_embed_target_size", return_value=(1440, 900)),
            patch.object(tab, "_linux_window_size", return_value=(0, 0)),
            patch.object(tab, "_run_xdotool", return_value=False),
            patch.object(tab, "_fallback_linux_embedded_to_detached") as mock_fallback,
        ):
            tab._resize_linux_parent_rdp_embedded_window()
            tab._resize_linux_parent_rdp_embedded_window()
            tab._resize_linux_parent_rdp_embedded_window()

        mock_fallback.assert_called_once()
        tab.deleteLater()


class VNCOpenTabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.window = MainWindow(_StubSessionService(), _StubSettingsService())
        self.window.show()
        QApplication.processEvents()

    def tearDown(self) -> None:
        self.window.close()
        self.window.deleteLater()
        QApplication.processEvents()

    def test_open_vnc_tab_on_linux_uses_detached_only(self) -> None:
        session = _build_vnc_session()
        with (
            patch("snakesh.ui.main_window.platform.system", return_value="Linux"),
            patch.object(self.window, "_open_remote_viewer_tab") as mock_open_remote_viewer_tab,
        ):
            self.window._open_vnc_tab(session, password=None)

        mock_open_remote_viewer_tab.assert_called_once()
        kwargs = mock_open_remote_viewer_tab.call_args.kwargs
        self.assertFalse(kwargs.get("linux_x11_reparent_embed", False))
        self.assertFalse(kwargs.get("windows_reparent_embed", False))
        self.assertTrue(kwargs.get("start_detached", False))

    def test_open_vnc_tab_on_windows_uses_detached_only(self) -> None:
        session = _build_vnc_session()
        with (
            patch("snakesh.ui.main_window.platform.system", return_value="Windows"),
            patch.object(self.window, "_open_remote_viewer_tab") as mock_open_remote_viewer_tab,
        ):
            self.window._open_vnc_tab(session, password=None)

        mock_open_remote_viewer_tab.assert_called_once()
        kwargs = mock_open_remote_viewer_tab.call_args.kwargs
        self.assertFalse(kwargs.get("linux_x11_reparent_embed", False))
        self.assertFalse(kwargs.get("windows_reparent_embed", False))
        self.assertTrue(kwargs.get("start_detached", False))

    def test_nomachine_sessions_start_detached_by_default(self) -> None:
        session = _build_nomachine_session()
        self.assertTrue(self.window._start_detached_for_remote_session(session))

    def test_rdp_sessions_start_detached_on_non_linux(self) -> None:
        session = _build_rdp_session()
        session.remote_launch_mode = "tab"
        with patch("snakesh.ui.main_window.platform.system", return_value="Windows"):
            self.assertTrue(self.window._start_detached_for_remote_session(session))

    def test_rdp_sessions_respect_open_mode_on_linux(self) -> None:
        session = _build_rdp_session()
        session.remote_launch_mode = "tab"
        with patch("snakesh.ui.main_window.platform.system", return_value="Linux"):
            self.assertFalse(self.window._start_detached_for_remote_session(session))
        session.remote_launch_mode = "detached"
        with patch("snakesh.ui.main_window.platform.system", return_value="Linux"):
            self.assertTrue(self.window._start_detached_for_remote_session(session))

    def test_open_nomachine_tab_on_linux_uses_detached_only(self) -> None:
        session = _build_nomachine_session()
        with (
            patch("snakesh.ui.main_window.platform.system", return_value="Linux"),
            patch.object(self.window, "_open_remote_viewer_tab") as mock_open_remote_viewer_tab,
        ):
            self.window._open_nomachine_tab(session)

        mock_open_remote_viewer_tab.assert_called_once()
        kwargs = mock_open_remote_viewer_tab.call_args.kwargs
        self.assertFalse(kwargs.get("linux_x11_reparent_embed", False))
        self.assertFalse(kwargs.get("windows_reparent_embed", False))
        self.assertTrue(kwargs.get("start_detached", False))

    def test_open_nomachine_tab_on_windows_uses_detached_only(self) -> None:
        session = _build_nomachine_session()
        with (
            patch("snakesh.ui.main_window.platform.system", return_value="Windows"),
            patch.object(self.window, "_open_remote_viewer_tab") as mock_open_remote_viewer_tab,
        ):
            self.window._open_nomachine_tab(session)

        mock_open_remote_viewer_tab.assert_called_once()
        kwargs = mock_open_remote_viewer_tab.call_args.kwargs
        self.assertFalse(kwargs.get("linux_x11_reparent_embed", False))
        self.assertFalse(kwargs.get("windows_reparent_embed", False))
        self.assertTrue(kwargs.get("start_detached", False))

    def test_nomachine_protocol_dependency_maps_to_nxplayer(self) -> None:
        self.assertEqual(
            self.window._required_dependency_id_for_protocol(Protocol.NOMACHINE),
            "nxplayer",
        )

    def test_connect_session_linux_rdp_clears_known_host_before_launch(self) -> None:
        session = _build_rdp_session()
        with (
            patch("snakesh.ui.main_window.platform.system", return_value="Linux"),
            patch.object(self.window, "_ensure_protocol_dependency", return_value=True),
            patch.object(self.window, "_confirm_linux_rdp_certificate_trust", return_value=True),
            patch.object(self.window, "_resolve_linux_rdp_password", return_value=("secret", True)),
            patch("snakesh.ui.main_window.clear_linux_rdp_known_host") as mock_clear,
            patch.object(self.window, "_open_rdp_tab") as mock_open,
        ):
            self.window._connect_session(session)

        mock_clear.assert_called_once_with(session)
        mock_open.assert_called_once_with(
            session,
            password="secret",
            linux_trust_certificate=True,
        )

    def test_connect_session_macos_rdp_uses_freerdp_tab_launch(self) -> None:
        session = _build_rdp_session()
        with (
            patch("snakesh.ui.main_window.platform.system", return_value="Darwin"),
            patch.object(self.window, "_ensure_protocol_dependency", return_value=True),
            patch.object(self.window, "_confirm_linux_rdp_certificate_trust", return_value=True),
            patch.object(self.window, "_resolve_linux_rdp_password", return_value=("secret", True)),
            patch("snakesh.ui.main_window.clear_linux_rdp_known_host") as mock_clear,
            patch.object(self.window, "_open_rdp_tab") as mock_open,
            patch("snakesh.ui.main_window.launch_rdp") as mock_launch,
        ):
            self.window._connect_session(session)

        mock_clear.assert_called_once_with(session)
        mock_launch.assert_not_called()
        mock_open.assert_called_once_with(
            session,
            password="secret",
            linux_trust_certificate=True,
        )

    def test_open_rdp_tab_on_windows_uses_detached_only(self) -> None:
        session = _build_rdp_session()

        with (
            patch("snakesh.ui.main_window.platform.system", return_value="Windows"),
            patch.object(self.window, "_open_remote_viewer_tab") as mock_open_remote_viewer_tab,
        ):
            self.window._open_rdp_tab(
                session,
                password="secret",
                linux_trust_certificate=False,
            )

        mock_open_remote_viewer_tab.assert_called_once()
        kwargs = mock_open_remote_viewer_tab.call_args.kwargs
        self.assertTrue(kwargs.get("start_detached", False))
        self.assertFalse(kwargs.get("windows_reparent_embed", False))
        self.assertFalse(kwargs.get("linux_x11_reparent_embed", False))
        self.assertIsNone(kwargs.get("embedded_command_builder"))

    def test_open_rdp_tab_on_macos_supplies_xquartz_launch_environment(self) -> None:
        session = _build_rdp_session()
        launch_env = {"DISPLAY": ":0"}

        with (
            patch("snakesh.ui.main_window.platform.system", return_value="Darwin"),
            patch("snakesh.ui.main_window.prepare_rdp_launch_environment", return_value=launch_env),
            patch("snakesh.ui.main_window.build_rdp_command", return_value=["xfreerdp", "/v:192.0.2.75"]),
            patch("snakesh.ui.main_window.build_rdp_stdin_payload", return_value="secret\n"),
            patch.object(self.window, "_open_remote_viewer_tab") as mock_open_remote_viewer_tab,
        ):
            self.window._open_rdp_tab(
                session,
                password="secret",
                linux_trust_certificate=True,
            )
            kwargs = mock_open_remote_viewer_tab.call_args.kwargs
            self.assertTrue(kwargs.get("start_detached", False))
            detached_builder = kwargs["detached_command_builder"]
            command, viewer_name, env, stdin_payload = detached_builder()

        self.assertEqual(command, ["xfreerdp", "/v:192.0.2.75"])
        self.assertEqual(viewer_name, "RDP Client")
        self.assertEqual(env, launch_env)
        self.assertEqual(stdin_payload, "secret\n")

    def test_open_rdp_tab_on_linux_allows_in_tab_mode_when_selected(self) -> None:
        session = _build_rdp_session()
        session.remote_launch_mode = "tab"

        with (
            patch("snakesh.ui.main_window.platform.system", return_value="Linux"),
            patch.object(self.window, "_open_remote_viewer_tab") as mock_open_remote_viewer_tab,
        ):
            self.window._open_rdp_tab(
                session,
                password="secret",
                linux_trust_certificate=True,
            )

        mock_open_remote_viewer_tab.assert_called_once()
        kwargs = mock_open_remote_viewer_tab.call_args.kwargs
        self.assertFalse(kwargs.get("start_detached", True))
        self.assertIsNotNone(kwargs.get("embedded_command_builder"))

    def test_open_rdp_tab_on_linux_respects_detached_mode(self) -> None:
        session = _build_rdp_session()
        session.remote_launch_mode = "detached"

        with (
            patch("snakesh.ui.main_window.platform.system", return_value="Linux"),
            patch.object(self.window, "_open_remote_viewer_tab") as mock_open_remote_viewer_tab,
        ):
            self.window._open_rdp_tab(
                session,
                password="secret",
                linux_trust_certificate=True,
            )

        mock_open_remote_viewer_tab.assert_called_once()
        kwargs = mock_open_remote_viewer_tab.call_args.kwargs
        self.assertTrue(kwargs.get("start_detached", False))
        self.assertIsNotNone(kwargs.get("embedded_command_builder"))

    def test_open_remote_viewer_tab_starts_immediately_when_not_restoring_profile(self) -> None:
        session = _build_rdp_session()

        def detached_builder() -> tuple[list[str], str, dict[str, str] | None]:
            return ["xfreerdp", "/v:192.0.2.75"], "FreeRDP", None

        with patch.object(self.window, "_start_remote_viewer_tab") as mock_start:
            self.window._open_remote_viewer_tab(
                session,
                protocol_name="RDP",
                detached_command_builder=detached_builder,
                start_detached=True,
            )
            QApplication.processEvents()

        mock_start.assert_called_once()

    def test_remote_viewer_start_queue_avoids_nested_reentry(self) -> None:
        session = _build_rdp_session()

        def detached_builder() -> tuple[list[str], str, dict[str, str] | None]:
            return ["xfreerdp", "/v:192.0.2.75"], "FreeRDP", None

        first_tab = RemoteViewerTab(
            session=session,
            protocol_name="RDP",
            detached_command_builder=detached_builder,
            parent=self.window,
        )
        second_tab = RemoteViewerTab(
            session=session,
            protocol_name="RDP",
            detached_command_builder=detached_builder,
            parent=self.window,
        )
        self.window._add_session_tab(session, first_tab, "RDP")
        self.window._add_session_tab(session, second_tab, "RDP")
        self.window._remote_viewer_start_queue.extend([(first_tab, True), (second_tab, True)])

        started: list[RemoteViewerTab] = []
        nested_counts: list[int] = []

        def fake_start(tab: RemoteViewerTab, start_detached: bool = False) -> None:
            _ = start_detached
            started.append(tab)
            if len(started) == 1:
                QApplication.processEvents()
                nested_counts.append(len(started))

        with patch.object(self.window, "_start_remote_viewer_tab", side_effect=fake_start):
            self.window._start_next_queued_remote_viewer_tab()
            self.assertEqual(started, [first_tab])
            self.assertEqual(nested_counts, [1])
            QApplication.processEvents()

        self.assertEqual(started, [first_tab, second_tab])

    def test_open_remote_viewer_tab_defers_start_while_restoring_profile(self) -> None:
        session = _build_rdp_session()

        def detached_builder() -> tuple[list[str], str, dict[str, str] | None]:
            return ["xfreerdp", "/v:192.0.2.75"], "FreeRDP", None

        self.window._profile_restore_in_progress = True
        with patch.object(self.window, "_start_remote_viewer_tab") as mock_start:
            self.window._open_remote_viewer_tab(
                session,
                protocol_name="RDP",
                detached_command_builder=detached_builder,
                start_detached=True,
            )
            QApplication.processEvents()
            self.assertFalse(mock_start.called)
            self.assertEqual(len(self.window._profile_restore_pending_remote_starts), 1)

            self.window._profile_restore_in_progress = False
            self.window._flush_profile_restore_remote_starts()
            QApplication.processEvents()

        mock_start.assert_called_once()

    def test_tab_context_menu_dismiss_does_not_trigger_remote_attach_or_detach(self) -> None:
        session = _build_rdp_session()

        def detached_builder() -> tuple[list[str], str, dict[str, str] | None]:
            return ["xfreerdp", "/v:192.0.2.75"], "FreeRDP", None

        def embedded_builder(_parent_window_id: int) -> tuple[list[str], str, dict[str, str] | None]:
            return ["xfreerdp", "/v:192.0.2.75"], "FreeRDP", None

        with patch("snakesh.ui.main_window.platform.system", return_value="Linux"):
            tab = RemoteViewerTab(
                session=session,
                protocol_name="RDP",
                detached_command_builder=detached_builder,
                embedded_command_builder=embedded_builder,
                parent=self.window,
            )
        tab._mode = "embedded"
        tab._viewer_name = "FreeRDP"
        tab._update_state_text()
        self.window._add_session_tab(session, tab, "RDP")
        QApplication.processEvents()

        location = self.window._find_widget_location(tab)
        self.assertIsNotNone(location)
        host, index = location

        with (
            patch.object(self.window, "_exec_menu", return_value=None),
            patch.object(tab, "attach_viewer") as mock_attach,
            patch.object(tab, "detach_viewer") as mock_detach,
        ):
            self.window._show_tab_context_menu(
                host,
                index,
                self.window.mapToGlobal(QPoint(40, 40)),
            )

        mock_attach.assert_not_called()
        mock_detach.assert_not_called()

    def test_tab_context_menu_hides_remote_attach_detach_for_nomachine(self) -> None:
        session = _build_nomachine_session()

        def detached_builder() -> tuple[list[str], str, dict[str, str] | None]:
            return ["/usr/NX/bin/nxplayer", "--session", "dummy.nxs"], "NoMachine Player", None

        with patch("snakesh.ui.main_window.platform.system", return_value="Linux"):
            tab = RemoteViewerTab(
                session=session,
                protocol_name="NoMachine",
                detached_command_builder=detached_builder,
                parent=self.window,
            )
        tab._mode = "detached"
        tab._viewer_name = "NoMachine Player"
        tab._update_state_text()
        self.window._add_session_tab(session, tab, "NOMACHINE")
        QApplication.processEvents()

        location = self.window._find_widget_location(tab)
        self.assertIsNotNone(location)
        host, index = location

        action_labels: list[str] = []

        def _capture_menu_actions(menu, _global_pos):  # noqa: ANN001
            action_labels.extend(action.text() for action in menu.actions() if action.text())
            return None

        with patch.object(self.window, "_exec_menu", side_effect=_capture_menu_actions):
            self.window._show_tab_context_menu(
                host,
                index,
                self.window.mapToGlobal(QPoint(40, 40)),
            )

        self.assertNotIn("Detach Viewer", action_labels)
        self.assertNotIn("Attach Viewer to Tab", action_labels)

    def test_tab_context_menu_keeps_remote_attach_for_linux_rdp(self) -> None:
        session = _build_rdp_session()

        def detached_builder() -> tuple[list[str], str, dict[str, str] | None]:
            return ["xfreerdp", "/v:192.0.2.75"], "FreeRDP", None

        def embedded_builder(_parent_window_id: int) -> tuple[list[str], str, dict[str, str] | None]:
            return ["xfreerdp", "/v:192.0.2.75"], "FreeRDP", None

        with patch("snakesh.ui.main_window.platform.system", return_value="Linux"):
            tab = RemoteViewerTab(
                session=session,
                protocol_name="RDP",
                detached_command_builder=detached_builder,
                embedded_command_builder=embedded_builder,
                parent=self.window,
            )
        tab._mode = "detached"
        tab._viewer_name = "FreeRDP"
        tab._update_state_text()
        self.window._add_session_tab(session, tab, "RDP")
        QApplication.processEvents()

        location = self.window._find_widget_location(tab)
        self.assertIsNotNone(location)
        host, index = location

        action_labels: list[str] = []

        def _capture_menu_actions(menu, _global_pos):  # noqa: ANN001
            action_labels.extend(action.text() for action in menu.actions() if action.text())
            return None

        with patch.object(self.window, "_exec_menu", side_effect=_capture_menu_actions):
            self.window._show_tab_context_menu(
                host,
                index,
                self.window.mapToGlobal(QPoint(40, 40)),
            )

        self.assertIn("Attach Viewer to Tab", action_labels)
