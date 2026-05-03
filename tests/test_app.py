from __future__ import annotations

import logging
import io
import os
from pathlib import Path
import signal
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit, QMessageBox

from snakesh import app
from snakesh.services.master_password_service import MasterPasswordService
from snakesh.services.settings_service import AppSettings


class AppUnlockTests(unittest.TestCase):
    def test_unlock_skips_prompt_when_disabled(self) -> None:
        settings = AppSettings.defaults()
        self.assertTrue(app._unlock_with_master_password(settings))

    def test_unlock_prompts_until_correct_password(self) -> None:
        settings = AppSettings.defaults()
        MasterPasswordService.set_master_password(settings, "top-secret")
        settings.master_password_enabled = True

        with patch("snakesh.app._prompt_master_password", side_effect=[("wrong", True), ("top-secret", True)]) as mock_prompt:
            with patch("snakesh.app._show_master_password_warning") as mock_warning:
                unlocked = app._unlock_with_master_password(settings)

        self.assertTrue(unlocked)
        self.assertEqual(mock_prompt.call_count, 2)
        mock_warning.assert_called_once()

    def test_unlock_returns_false_when_user_cancels(self) -> None:
        settings = AppSettings.defaults()
        MasterPasswordService.set_master_password(settings, "top-secret")
        settings.master_password_enabled = True

        with patch("snakesh.app._prompt_master_password", return_value=("", False)) as mock_prompt:
            unlocked = app._unlock_with_master_password(settings)

        self.assertFalse(unlocked)
        mock_prompt.assert_called_once()

    def test_tool_launch_unlock_skips_prompt_by_default_even_with_master_password(self) -> None:
        settings = AppSettings.defaults()
        MasterPasswordService.set_master_password(settings, "top-secret")
        settings.master_password_enabled = True
        settings.master_password_tools_enabled = False

        with patch("snakesh.app._prompt_master_password") as mock_prompt:
            unlocked = app._unlock_with_master_password(settings, tool_launch=True)

        self.assertTrue(unlocked)
        mock_prompt.assert_not_called()

    def test_tool_launch_unlock_uses_separate_tool_prompt_setting(self) -> None:
        settings = AppSettings.defaults()
        MasterPasswordService.set_master_password(settings, "top-secret")
        settings.master_password_enabled = False
        settings.master_password_tools_enabled = True

        with patch("snakesh.app._prompt_master_password", return_value=("top-secret", True)) as mock_prompt:
            unlocked = app._unlock_with_master_password(settings, tool_launch=True)

        self.assertTrue(unlocked)
        mock_prompt.assert_called_once()


class AppUnlockDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls._app = QApplication.instance() or QApplication([])

    def test_prompt_master_password_returns_text_after_accept(self) -> None:
        def accept_unlock_dialog() -> None:
            for widget in QApplication.topLevelWidgets():
                if isinstance(widget, QDialog) and widget.windowTitle() == "SnakeSh Unlock":
                    password_input = widget.findChild(QLineEdit)
                    assert password_input is not None
                    password_input.setText("top-secret")
                    widget.accept()
                    return
            QTimer.singleShot(10, accept_unlock_dialog)

        QTimer.singleShot(0, accept_unlock_dialog)

        password, accepted = app._prompt_master_password()

        self.assertTrue(accepted)
        self.assertEqual(password, "top-secret")


class AppFaultHandlerTests(unittest.TestCase):
    def tearDown(self) -> None:
        app._stop_ui_hang_watchdog()
        app._close_fault_log_handle()
        app._FAULT_LOG_PATH = None

    def test_initialize_fault_handler_creates_log_and_enables_faulthandler(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch("snakesh.app.data_dir", return_value=Path(tmp)),
                patch("snakesh.app.faulthandler.enable") as mock_enable,
                patch("snakesh.app.faulthandler.register") as mock_register,
                patch("snakesh.app.atexit.register") as mock_atexit,
            ):
                app._initialize_fault_handler()
                self.assertIsNotNone(app._FAULT_LOG_HANDLE)
                self.assertIsNotNone(app._FAULT_LOG_PATH)
                assert app._FAULT_LOG_PATH is not None
                self.assertTrue(app._FAULT_LOG_PATH.exists())

                mock_enable.assert_called_once()
                self.assertTrue(mock_enable.call_args.kwargs.get("all_threads"))
                self.assertIs(mock_enable.call_args.kwargs.get("file"), app._FAULT_LOG_HANDLE)
                mock_atexit.assert_called_once_with(app._close_fault_log_handle)

                if getattr(signal, "SIGUSR1", None) is not None:
                    mock_register.assert_called_once()
                else:
                    mock_register.assert_not_called()

    def test_initialize_fault_handler_falls_back_to_stderr_when_log_dir_fails(self) -> None:
        with (
            patch("snakesh.app._fault_log_directory", side_effect=OSError("no dir")),
            patch("snakesh.app.faulthandler.enable") as mock_enable,
        ):
            app._initialize_fault_handler()

        mock_enable.assert_called_once_with(all_threads=True)

    def test_prune_old_fault_logs_keeps_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            total = app._MAX_CRASH_LOG_FILES + 5
            for index in range(total):
                (directory / f"crash-20260101-0000{index:02d}-{index}.log").write_text("x", encoding="utf-8")

            app._prune_old_fault_logs(directory)

            remaining = list(directory.glob("crash-*.log"))
            self.assertLessEqual(len(remaining), app._MAX_CRASH_LOG_FILES)

    def test_prune_old_fault_logs_removes_entries_older_than_retention(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            fresh = directory / "crash-fresh.log"
            stale = directory / "crash-stale.log"
            fresh.write_text("fresh", encoding="utf-8")
            stale.write_text("stale", encoding="utf-8")
            old_epoch = time.time() - ((app._FAULT_LOG_RETENTION_DAYS + 2) * 24 * 60 * 60)
            os.utime(stale, (old_epoch, old_epoch))

            app._prune_old_fault_logs(directory)

            self.assertTrue(fresh.exists())
            self.assertFalse(stale.exists())

    def test_maybe_initialize_fault_handler_disabled_setting_skips_init(self) -> None:
        settings = AppSettings.defaults()
        settings.crash_logging_enabled = False
        with patch("snakesh.app._initialize_fault_handler") as mock_initialize:
            app._maybe_initialize_fault_handler(settings)
        mock_initialize.assert_not_called()

    def test_maybe_initialize_fault_handler_enabled_setting_runs_init(self) -> None:
        settings = AppSettings.defaults()
        settings.crash_logging_enabled = True
        with patch("snakesh.app._initialize_fault_handler") as mock_initialize:
            app._maybe_initialize_fault_handler(settings)
        mock_initialize.assert_called_once()

    def test_maybe_initialize_fault_handler_uses_debug_log_path_even_when_setting_disabled(self) -> None:
        settings = AppSettings.defaults()
        settings.crash_logging_enabled = False
        debug_log_path = Path("/tmp/snakesh-debug.log")
        with patch("snakesh.app._initialize_fault_handler") as mock_initialize:
            app._maybe_initialize_fault_handler(settings, debug_log_path=debug_log_path)
        mock_initialize.assert_called_once_with(log_path=debug_log_path)


class AppDiagnosticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def tearDown(self) -> None:
        app._stop_ui_hang_watchdog()
        app._close_fault_log_handle()
        app._FAULT_LOG_PATH = None

    def test_ui_hang_watchdog_arms_and_rearms_dump_traceback(self) -> None:
        app._FAULT_LOG_HANDLE = io.StringIO()
        with (
            patch("snakesh.app.faulthandler.dump_traceback_later") as mock_dump,
            patch("snakesh.app.faulthandler.cancel_dump_traceback_later") as mock_cancel,
        ):
            timer = app._start_ui_hang_watchdog(self._app)
            self.assertIsNotNone(timer)
            assert timer is not None
            mock_dump.assert_called_once()
            mock_cancel.assert_called_once()

            timer.timeout.emit()

            self.assertEqual(mock_dump.call_count, 2)
            self.assertEqual(mock_cancel.call_count, 2)

    def test_debug_session_shutdown_flushes_session_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "session-debug.log"
            session = app._start_debug_session("debug", str(log_path))
            self.assertIsNotNone(session)
            logging.getLogger("snakesh.tests").info("manual diagnostics line")

            app._stop_debug_session(session)

            contents = log_path.read_text(encoding="utf-8")
            self.assertIn("Diagnostics enabled", contents)
            self.assertIn("manual diagnostics line", contents)
            self.assertIn("Diagnostics shutdown complete", contents)


class AppMainInstanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls._app = QApplication.instance() or QApplication([])

    def test_main_returns_when_existing_instance_was_activated(self) -> None:
        fake_settings = AppSettings.defaults()
        fake_service = MagicMock()
        fake_service.load.return_value = fake_settings
        fake_app = MagicMock()
        import_path = Path("/tmp/launch-import.ssx")

        with (
            patch("snakesh.app.SettingsService", return_value=fake_service),
            patch("snakesh.app._start_debug_session", return_value=None),
            patch("snakesh.app._stop_debug_session") as mock_stop_debug,
            patch("snakesh.app._maybe_initialize_fault_handler") as mock_fault_handler,
            patch("snakesh.app.QApplication", return_value=fake_app),
            patch(
                "snakesh.app.claim_main_instance",
                return_value=MagicMock(activated_existing=True, lease=None),
            ) as mock_claim,
            patch("snakesh.app._unlock_with_master_password") as mock_unlock,
            patch("snakesh.app.MainWindow") as mock_main_window,
        ):
            exit_code = app.main(str(import_path))

        self.assertEqual(exit_code, 0)
        mock_fault_handler.assert_called_once_with(fake_settings, debug_log_path=None)
        mock_claim.assert_called_once()
        self.assertEqual(
            mock_claim.call_args.kwargs["activation_payload"],
            {"import_file": str(import_path.resolve())},
        )
        mock_unlock.assert_not_called()
        mock_main_window.assert_not_called()
        fake_app.exec.assert_not_called()
        mock_stop_debug.assert_called_once_with(None)

    def test_main_releases_claimed_instance_lease_on_exit(self) -> None:
        fake_settings = AppSettings.defaults()
        fake_service = MagicMock()
        fake_service.load.return_value = fake_settings
        fake_app = MagicMock()
        fake_app.exec.return_value = 23
        fake_icon = MagicMock()
        fake_icon.isNull.return_value = True
        fake_window = MagicMock()
        fake_lease = MagicMock()

        with (
            patch("snakesh.app.SettingsService", return_value=fake_service),
            patch("snakesh.app._start_debug_session", return_value=None),
            patch("snakesh.app._stop_debug_session"),
            patch("snakesh.app._maybe_initialize_fault_handler"),
            patch("snakesh.app.QApplication", return_value=fake_app),
            patch(
                "snakesh.app.claim_main_instance",
                return_value=MagicMock(activated_existing=False, lease=fake_lease),
            ),
            patch("snakesh.app._load_app_icon", return_value=fake_icon),
            patch("snakesh.app.apply_theme"),
            patch("snakesh.app._unlock_with_master_password", return_value=True),
            patch("snakesh.app._maybe_prompt_for_safe_mode", return_value=False),
            patch("snakesh.app._record_run_marker"),
            patch("snakesh.app._register_run_marker_cleanup"),
            patch("snakesh.app.known_hosts_path"),
            patch("snakesh.app.SessionService"),
            patch("snakesh.app.MainWindow", return_value=fake_window),
            patch("snakesh.app.QTimer.singleShot"),
        ):
            exit_code = app.main()

        self.assertEqual(exit_code, 23)
        fake_window.show.assert_called_once_with()
        fake_app.exec.assert_called_once_with()
        fake_lease.release.assert_called_once_with()

    def test_main_activation_focuses_modal_and_queues_import_until_window_exists(self) -> None:
        fake_settings = AppSettings.defaults()
        fake_service = MagicMock()
        fake_service.load.return_value = fake_settings
        fake_app = MagicMock()
        fake_app.exec.return_value = 0
        fake_modal = MagicMock()
        fake_app.activeModalWidget.return_value = fake_modal
        fake_app.activeWindow.return_value = None
        fake_app.topLevelWidgets.return_value = []
        fake_icon = MagicMock()
        fake_icon.isNull.return_value = True
        fake_window = MagicMock()
        fake_lease = MagicMock()
        captured_on_activate: dict[str, object] = {}
        queued_import = "/tmp/queued-import.ssx"

        def _claim_main_instance(*_args, **kwargs):
            captured_on_activate["callback"] = kwargs["on_activate"]
            return MagicMock(activated_existing=False, lease=fake_lease)

        def _unlock(_settings: AppSettings) -> bool:
            callback = captured_on_activate["callback"]
            assert callable(callback)
            self.assertTrue(callback({"import_file": queued_import}))
            return True

        def _single_shot(_delay: int, callback) -> None:  # noqa: ANN001
            callback()

        with (
            patch("snakesh.app.SettingsService", return_value=fake_service),
            patch("snakesh.app._start_debug_session", return_value=None),
            patch("snakesh.app._stop_debug_session"),
            patch("snakesh.app._maybe_initialize_fault_handler"),
            patch("snakesh.app.QApplication", return_value=fake_app),
            patch("snakesh.app.claim_main_instance", side_effect=_claim_main_instance),
            patch("snakesh.app._load_app_icon", return_value=fake_icon),
            patch("snakesh.app.apply_theme"),
            patch("snakesh.app._unlock_with_master_password", side_effect=_unlock),
            patch("snakesh.app._maybe_prompt_for_safe_mode", return_value=False),
            patch("snakesh.app._record_run_marker"),
            patch("snakesh.app._register_run_marker_cleanup"),
            patch("snakesh.app.known_hosts_path"),
            patch("snakesh.app.SessionService"),
            patch("snakesh.app.MainWindow", return_value=fake_window),
            patch("snakesh.app._activate_main_window_target") as mock_activate,
            patch("snakesh.app.QTimer.singleShot", side_effect=_single_shot),
        ):
            exit_code = app.main()

        self.assertEqual(exit_code, 0)
        mock_activate.assert_called_once_with(fake_modal)
        fake_window.prompt_import_startup_file.assert_called_once_with(queued_import)
        fake_lease.release.assert_called_once_with()


class AppRunMarkerTests(unittest.TestCase):
    def tearDown(self) -> None:
        app._RUN_MARKER_CLEANUP_REGISTERED = False

    def test_record_and_clear_run_marker_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch("snakesh.app.data_dir", return_value=Path(tmp)):
                app._record_run_marker()
                marker_path = app._run_marker_path()
                self.assertTrue(marker_path.exists())

                app._clear_run_marker()

                self.assertFalse(marker_path.exists())

    def test_consume_stale_run_markers_returns_stopped_runs_and_deletes_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch("snakesh.app.data_dir", return_value=Path(tmp)):
                marker_path = app._run_marker_path(43210)
                marker_path.write_text('{"pid": 43210, "started_at": 1710000000}', encoding="utf-8")

                with patch("snakesh.app._process_is_running", return_value=False):
                    markers = app._consume_stale_run_markers()

                self.assertEqual(len(markers), 1)
                self.assertEqual(markers[0]["pid"], 43210)
                self.assertFalse(marker_path.exists())

    def test_consume_stale_run_markers_keeps_live_process_markers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch("snakesh.app.data_dir", return_value=Path(tmp)):
                marker_path = app._run_marker_path(54321)
                marker_path.write_text('{"pid": 54321, "started_at": 1710000000}', encoding="utf-8")

                with patch("snakesh.app._process_is_running", return_value=True):
                    markers = app._consume_stale_run_markers()

                self.assertEqual(markers, [])
                self.assertTrue(marker_path.exists())

    def test_prompt_for_safe_mode_returns_true_when_user_accepts(self) -> None:
        marker = {"pid": 1234, "started_at": 1710000000}
        with patch("snakesh.app.QMessageBox.question", return_value=QMessageBox.Yes):
            self.assertTrue(app._prompt_for_safe_mode(marker))

    def test_maybe_prompt_for_safe_mode_uses_latest_stale_marker(self) -> None:
        marker = {"pid": 999, "started_at": 1710000000}
        with (
            patch("snakesh.app._consume_stale_run_markers", return_value=[marker]) as mock_consume,
            patch("snakesh.app._prompt_for_safe_mode", return_value=True) as mock_prompt,
        ):
            enabled = app._maybe_prompt_for_safe_mode()

        self.assertTrue(enabled)
        mock_consume.assert_called_once()
        mock_prompt.assert_called_once_with(marker)

    def test_register_run_marker_cleanup_only_registers_once(self) -> None:
        with patch("snakesh.app.atexit.register") as mock_register:
            app._register_run_marker_cleanup()
            app._register_run_marker_cleanup()

        mock_register.assert_called_once_with(app._clear_run_marker)


if __name__ == "__main__":
    unittest.main()
