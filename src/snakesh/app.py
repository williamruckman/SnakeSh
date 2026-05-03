from __future__ import annotations

import atexit
import faulthandler
import json
import os
import signal
from pathlib import Path
import time
import sys
from typing import Callable, TextIO

from PySide6.QtCore import QObject, QTimer, Signal, Slot
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from snakesh import runtime
from snakesh.core.hostkeys import known_hosts_path
from snakesh.core.paths import data_dir
from snakesh.services._instance_activation import process_is_running as _shared_process_is_running
from snakesh.services.diagnostics_service import DiagnosticsSession, start_diagnostics_session
from snakesh.services.main_instance_service import (
    MainInstanceClaimResult,
    claim_main_instance,
    main_activation_payload,
)
from snakesh.services.master_password_service import MasterPasswordService
from snakesh.services.settings_service import AppSettings, SettingsService
from snakesh.services.session_service import SessionService
from snakesh.ui.main_window import MainWindow
from snakesh.ui.theme import apply_theme


APP_NAME = "SnakeSh"
APP_ORGANIZATION = "SnakeSh"
_FAULT_LOG_RETENTION_DAYS = 7
_MAX_CRASH_LOG_FILES = 20
_FAULT_LOG_HANDLE: TextIO | None = None
_FAULT_LOG_PATH: Path | None = None
_RUN_MARKER_PREFIX = "active-"
_RUN_MARKER_CLEANUP_REGISTERED = False
_UI_HANG_DUMP_TIMEOUT_SECONDS = 15.0
_UI_HANG_HEARTBEAT_INTERVAL_MS = 5000
_UI_HANG_WATCHDOG_TIMER: QTimer | None = None
_TEST_MAIN_READY_FILE_ENV = "SNAKESH_TEST_MAIN_READY_FILE"
_TEST_CLOSE_MAIN_AFTER_MS_ENV = "SNAKESH_TEST_CLOSE_MAIN_AFTER_MS"


class _MainActivationBridge(QObject):
    activation_requested = Signal(object)

    def __init__(self, on_activate: Callable[[dict[str, object] | None], None]) -> None:
        super().__init__()
        self._on_activate = on_activate
        self.activation_requested.connect(self._dispatch_activation)

    @Slot(object)
    def _dispatch_activation(self, payload: object) -> None:
        request_payload = payload if isinstance(payload, dict) else None
        self._on_activate(request_payload)


def _fault_log_directory() -> Path:
    directory = data_dir() / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _run_marker_directory() -> Path:
    directory = data_dir() / "runtime"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _run_marker_path(pid: int | None = None) -> Path:
    return _run_marker_directory() / f"{_RUN_MARKER_PREFIX}{pid or os.getpid()}.json"


def _process_is_running(pid: int) -> bool:
    return _shared_process_is_running(pid)


def _marker_pid(path: Path, payload: dict[str, object]) -> int:
    raw_pid = payload.get("pid")
    try:
        return int(raw_pid)
    except (TypeError, ValueError):
        pass
    stem = path.stem
    if stem.startswith(_RUN_MARKER_PREFIX):
        try:
            return int(stem[len(_RUN_MARKER_PREFIX):])
        except ValueError:
            return 0
    return 0


def _consume_stale_run_markers() -> list[dict[str, object]]:
    try:
        candidates = sorted(
            _run_marker_directory().glob(f"{_RUN_MARKER_PREFIX}*.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    except Exception:
        return []

    stale_markers: list[dict[str, object]] = []
    current_pid = os.getpid()
    for candidate in candidates:
        payload: dict[str, object] = {}
        try:
            raw = json.loads(candidate.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                payload = raw
        except Exception:
            payload = {}

        pid = _marker_pid(candidate, payload)
        if pid == current_pid:
            continue
        if pid > 0 and _process_is_running(pid):
            continue

        payload["pid"] = pid
        stale_markers.append(payload)
        try:
            candidate.unlink()
        except Exception:
            pass
    return stale_markers


def _record_run_marker() -> None:
    payload = {
        "pid": os.getpid(),
        "started_at": time.time(),
    }
    try:
        _run_marker_path().write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception:
        return


def _clear_run_marker() -> None:
    try:
        _run_marker_path().unlink(missing_ok=True)
    except Exception:
        return


def _register_run_marker_cleanup() -> None:
    global _RUN_MARKER_CLEANUP_REGISTERED
    if _RUN_MARKER_CLEANUP_REGISTERED:
        return
    atexit.register(_clear_run_marker)
    _RUN_MARKER_CLEANUP_REGISTERED = True


def _format_run_marker_started_at(marker: dict[str, object]) -> str:
    raw_started_at = marker.get("started_at")
    try:
        started_at = float(raw_started_at)
    except (TypeError, ValueError):
        return "a previous run"
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(started_at))
    except (OverflowError, OSError, ValueError):
        return "a previous run"


def _prompt_for_safe_mode(marker: dict[str, object]) -> bool:
    started_at = _format_run_marker_started_at(marker)
    if started_at == "a previous run":
        intro = "SnakeSh appears to have ended unexpectedly during a previous run."
    else:
        intro = f"SnakeSh appears to have ended unexpectedly during a run started on {started_at}."
    answer = QMessageBox.question(
        None,
        "SnakeSh Safe Mode",
        f"{intro}\n\nLaunch in safe mode? Safe mode skips the default workspace profile for this launch.",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.Yes,
    )
    return answer == QMessageBox.Yes


def _maybe_prompt_for_safe_mode() -> bool:
    stale_markers = _consume_stale_run_markers()
    if not stale_markers:
        return False
    return _prompt_for_safe_mode(stale_markers[0])


def _prune_old_fault_logs(directory: Path) -> None:
    try:
        candidates = sorted(
            directory.glob("crash-*.log"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    except Exception:
        return
    cutoff = time.time() - (_FAULT_LOG_RETENTION_DAYS * 24 * 60 * 60)
    retained: list[Path] = []
    for candidate in candidates:
        try:
            if candidate.stat().st_mtime < cutoff:
                candidate.unlink()
                continue
        except Exception:
            pass
        retained.append(candidate)
    for stale in retained[_MAX_CRASH_LOG_FILES:]:
        try:
            stale.unlink()
        except Exception:
            continue


def _close_fault_log_handle() -> None:
    global _FAULT_LOG_HANDLE
    handle = _FAULT_LOG_HANDLE
    _FAULT_LOG_HANDLE = None
    if handle is None:
        return
    try:
        handle.flush()
    except Exception:
        pass
    try:
        handle.close()
    except Exception:
        pass


def _initialize_fault_handler(*, log_path: Path | None = None) -> None:
    global _FAULT_LOG_HANDLE
    global _FAULT_LOG_PATH

    if _FAULT_LOG_HANDLE is not None:
        return

    directory: Path | None = None
    resolved_log_path = log_path
    if resolved_log_path is None:
        try:
            directory = _fault_log_directory()
        except Exception:
            try:
                faulthandler.enable(all_threads=True)
            except Exception:
                pass
            return
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        resolved_log_path = directory / f"crash-{timestamp}-{os.getpid()}.log"
    else:
        try:
            resolved_log_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            try:
                faulthandler.enable(all_threads=True)
            except Exception:
                pass
            return

    try:
        handle = resolved_log_path.open("a", encoding="utf-8")
    except Exception:
        try:
            faulthandler.enable(all_threads=True)
        except Exception:
            pass
        return

    _FAULT_LOG_HANDLE = handle
    _FAULT_LOG_PATH = resolved_log_path
    try:
        handle.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] SnakeSh crash handler enabled (pid={os.getpid()})\n")
        handle.flush()
    except Exception:
        pass

    try:
        faulthandler.enable(file=handle, all_threads=True)
    except Exception:
        _close_fault_log_handle()
        return

    sigusr1 = getattr(signal, "SIGUSR1", None)
    if sigusr1 is not None:
        try:
            # Manual dump trigger: `kill -USR1 <pid>`
            faulthandler.register(sigusr1, file=handle, all_threads=True)
        except Exception:
            pass

    if directory is not None:
        _prune_old_fault_logs(directory)
    atexit.register(_close_fault_log_handle)


def _load_app_icon() -> QIcon:
    for candidate in ("snakesh-icon.png", "snakesh-icon.svg"):
        icon_path = runtime.asset_path(candidate)
        if not icon_path.exists():
            icon_path = Path(__file__).resolve().parent / "assets" / candidate
        if not icon_path.exists():
            continue
        icon = QIcon(str(icon_path))
        if not icon.isNull():
            return icon
    return QIcon()


def _startup_dialog_parent() -> QWidget | None:
    app_instance = QApplication.instance()
    if app_instance is None:
        return None
    parent = app_instance.activeModalWidget() or app_instance.activeWindow()
    return parent if isinstance(parent, QWidget) else None


def _prompt_master_password(parent: QWidget | None = None) -> tuple[str, bool]:
    dialog = QDialog(parent)
    dialog.setWindowTitle("SnakeSh Unlock")
    dialog.setModal(True)
    dialog.setMinimumWidth(360)

    layout = QVBoxLayout(dialog)
    prompt = QLabel("Enter master password:", dialog)
    prompt.setWordWrap(True)
    layout.addWidget(prompt)

    password_input = QLineEdit(dialog)
    password_input.setEchoMode(QLineEdit.Password)
    password_input.setClearButtonEnabled(True)
    layout.addWidget(password_input)

    buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=dialog)
    unlock_button = buttons.button(QDialogButtonBox.Ok)
    if unlock_button is not None:
        unlock_button.setText("Unlock")
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)

    password_input.returnPressed.connect(dialog.accept)
    QTimer.singleShot(0, password_input.setFocus)

    accepted = dialog.exec() == QDialog.Accepted
    return password_input.text(), accepted


def _show_master_password_warning(parent: QWidget | None = None) -> None:
    message_box = QMessageBox(parent)
    message_box.setWindowTitle("Incorrect Password")
    message_box.setIcon(QMessageBox.Warning)
    message_box.setText("Master password is incorrect.")
    message_box.setStandardButtons(QMessageBox.Ok)
    message_box.exec()


def _unlock_with_master_password(settings: AppSettings, *, tool_launch: bool = False) -> bool:
    prompt_enabled = settings.master_password_tools_enabled if tool_launch else settings.master_password_enabled
    if not prompt_enabled:
        return True
    if not MasterPasswordService.has_master_password(settings):
        return True
    while True:
        password, ok = _prompt_master_password(_startup_dialog_parent())
        if not ok:
            return False
        if MasterPasswordService.verify_master_password(settings, password):
            return True
        _show_master_password_warning(_startup_dialog_parent())


def _maybe_initialize_fault_handler(
    settings: AppSettings,
    *,
    debug_log_path: Path | None = None,
) -> None:
    if debug_log_path is not None:
        _initialize_fault_handler(log_path=debug_log_path)
        return
    if settings.crash_logging_enabled:
        _initialize_fault_handler()


def _start_debug_session(
    debug_level: str | None,
    debug_log_file: str | None = None,
) -> DiagnosticsSession | None:
    return start_diagnostics_session(
        debug_level=debug_level,
        debug_log_file=debug_log_file,
    )


def _stop_debug_session(session: DiagnosticsSession | None) -> None:
    if session is None:
        return
    session.close()


def _rearm_ui_hang_watchdog() -> None:
    handle = _FAULT_LOG_HANDLE
    if handle is None:
        return
    try:
        faulthandler.cancel_dump_traceback_later()
    except Exception:
        pass
    try:
        faulthandler.dump_traceback_later(
            _UI_HANG_DUMP_TIMEOUT_SECONDS,
            repeat=False,
            file=handle,
            exit=False,
        )
    except Exception:
        pass


def _start_ui_hang_watchdog(app_instance: QApplication) -> QTimer | None:
    global _UI_HANG_WATCHDOG_TIMER
    if _FAULT_LOG_HANDLE is None:
        return None
    if _UI_HANG_WATCHDOG_TIMER is not None:
        return _UI_HANG_WATCHDOG_TIMER
    timer = QTimer(app_instance)
    timer.setInterval(_UI_HANG_HEARTBEAT_INTERVAL_MS)
    timer.timeout.connect(_rearm_ui_hang_watchdog)
    timer.start()
    _UI_HANG_WATCHDOG_TIMER = timer
    _rearm_ui_hang_watchdog()
    return timer


def _stop_ui_hang_watchdog() -> None:
    global _UI_HANG_WATCHDOG_TIMER
    timer = _UI_HANG_WATCHDOG_TIMER
    _UI_HANG_WATCHDOG_TIMER = None
    if timer is not None:
        try:
            timer.stop()
        except Exception:
            pass
    try:
        faulthandler.cancel_dump_traceback_later()
    except Exception:
        pass


def _activate_main_window_target(target: QWidget | None) -> bool:
    if target is None:
        return False
    try:
        if target.isMinimized():
            target.showNormal()
        else:
            target.show()
        target.raise_()
        target.activateWindow()
        handle = target.windowHandle()
        if handle is not None:
            handle.requestActivate()
    except RuntimeError:
        return False
    return True


def _current_main_focus_target(app_instance: QApplication, window: MainWindow | None) -> QWidget | None:
    if window is not None:
        return window
    modal = app_instance.activeModalWidget()
    if modal is not None:
        return modal
    active = app_instance.activeWindow()
    if active is not None:
        return active
    top_levels = [widget for widget in app_instance.topLevelWidgets() if widget.isVisible()]
    if top_levels:
        return top_levels[-1]
    return None


def _record_main_window_ready_for_tests() -> None:
    raw_path = os.environ.get(_TEST_MAIN_READY_FILE_ENV, "").strip()
    if not raw_path:
        return
    try:
        path = Path(raw_path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
    except Exception:
        return


def _test_main_window_close_delay_ms() -> int | None:
    raw_value = os.environ.get(_TEST_CLOSE_MAIN_AFTER_MS_ENV, "").strip()
    if not raw_value:
        return None
    try:
        delay_ms = int(raw_value)
    except ValueError:
        return None
    if delay_ms <= 0:
        return None
    return delay_ms


def _schedule_main_window_test_hooks(window: MainWindow) -> None:
    QTimer.singleShot(0, _record_main_window_ready_for_tests)
    close_delay_ms = _test_main_window_close_delay_ms()
    if close_delay_ms is not None:
        QTimer.singleShot(close_delay_ms, window.close)


def main(
    import_file: str | None = None,
    *,
    debug_level: str | None = None,
    debug_log_file: str | None = None,
) -> int:
    settings_service = SettingsService()
    settings = settings_service.load()
    debug_session = _start_debug_session(debug_level, debug_log_file)
    _maybe_initialize_fault_handler(
        settings,
        debug_log_path=debug_session.log_path if debug_session is not None else None,
    )

    app = QApplication(sys.argv)
    main_instance_lease = None
    window: MainWindow | None = None
    pending_import_files: list[str] = []

    def _schedule_or_queue_import(path: str) -> None:
        if window is None:
            pending_import_files.append(path)
            return
        QTimer.singleShot(0, lambda target=window, import_path=path: target.prompt_import_startup_file(import_path))

    def _dispatch_activation(payload: dict[str, object] | None) -> None:
        _activate_main_window_target(_current_main_focus_target(app, window))
        if not isinstance(payload, dict):
            return
        import_path = str(payload.get("import_file", "")).strip()
        if import_path:
            _schedule_or_queue_import(import_path)

    bridge = _MainActivationBridge(_dispatch_activation)

    def _handle_activation(payload: dict[str, object] | None) -> bool:
        bridge.activation_requested.emit(payload)
        return True

    try:
        claim_result: MainInstanceClaimResult = claim_main_instance(
            on_activate=_handle_activation,
            activation_payload=main_activation_payload(import_file),
        )
        if claim_result.activated_existing:
            return 0
        main_instance_lease = claim_result.lease
        if main_instance_lease is None:  # pragma: no cover - claim result is always one path or the other
            return 0

        if debug_session is not None:
            debug_session.install_qt_message_handler()
            _start_ui_hang_watchdog(app)
        app.setApplicationName(APP_NAME)
        app.setOrganizationName(APP_ORGANIZATION)
        app.setApplicationDisplayName(APP_NAME)
        # Helps Linux shells associate running windows with snakesh.desktop.
        if hasattr(app, "setDesktopFileName"):
            app.setDesktopFileName("snakesh")
        icon = _load_app_icon()
        if not icon.isNull():
            app.setWindowIcon(icon)

        apply_theme(app, settings)
        if not _unlock_with_master_password(settings):
            return 0
        safe_mode = _maybe_prompt_for_safe_mode()
        _record_run_marker()
        _register_run_marker_cleanup()
        try:
            # Prime known_hosts from the UI thread to avoid first-touch filesystem work
            # in background SSH workers.
            known_hosts_path()
        except Exception:
            pass
        session_service = SessionService()

        window = MainWindow(
            session_service=session_service,
            settings_service=settings_service,
            safe_mode=safe_mode,
        )
        if not icon.isNull():
            window.setWindowIcon(icon)
        window.show()
        _schedule_main_window_test_hooks(window)
        if import_file:
            QTimer.singleShot(0, lambda path=import_file: window.prompt_import_startup_file(path))
        for pending_path in pending_import_files:
            QTimer.singleShot(0, lambda path=pending_path: window.prompt_import_startup_file(path))
        pending_import_files.clear()
        return app.exec()
    finally:
        if main_instance_lease is not None:
            main_instance_lease.release()
        _stop_ui_hang_watchdog()
        _close_fault_log_handle()
        _stop_debug_session(debug_session)
