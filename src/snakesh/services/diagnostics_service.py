from __future__ import annotations

import atexit
from collections import defaultdict
from datetime import datetime
import logging
import os
from pathlib import Path
import sys
import threading
import time
from typing import TextIO

from PySide6.QtCore import QtMsgType, qInstallMessageHandler

from snakesh.core.paths import data_dir


TRACE_LEVEL = 5
DEBUG_LEVEL_NAMES = ("info", "debug", "trace")
_DEBUG_LOG_RETENTION_DAYS = 7
_MAX_DEBUG_LOG_FILES = 20
_LOGGER_NAME = "snakesh"
_WORKER_LOGGER_NAME = "snakesh.workers"


def _install_trace_level() -> None:
    if logging.getLevelName(TRACE_LEVEL) != "TRACE":
        logging.addLevelName(TRACE_LEVEL, "TRACE")
    if not hasattr(logging, "TRACE"):
        logging.TRACE = TRACE_LEVEL  # type: ignore[attr-defined]
    if hasattr(logging.Logger, "trace"):
        return

    def _trace(self: logging.Logger, message: str, *args, **kwargs) -> None:
        if self.isEnabledFor(TRACE_LEVEL):
            self._log(TRACE_LEVEL, message, args, **kwargs)

    logging.Logger.trace = _trace  # type: ignore[attr-defined]


_install_trace_level()


def diagnostics_level_value(level_name: str | None) -> int | None:
    normalized = str(level_name or "").strip().lower()
    if not normalized:
        return None
    if normalized == "info":
        return logging.INFO
    if normalized == "debug":
        return logging.DEBUG
    if normalized == "trace":
        return TRACE_LEVEL
    raise ValueError(f"Unsupported diagnostics level: {level_name}")


def debug_logs_root() -> Path:
    root = data_dir() / "logs" / "debug"
    root.mkdir(parents=True, exist_ok=True)
    return root


def create_debug_log_path() -> Path:
    timestamp = datetime.now()
    directory = debug_logs_root() / timestamp.strftime("%Y") / timestamp.strftime("%m")
    directory.mkdir(parents=True, exist_ok=True)
    filename = f"snakesh-debug-{timestamp.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}.log"
    return directory / filename


def prune_debug_log_files(
    root: Path | None = None,
    *,
    retention_days: int = _DEBUG_LOG_RETENTION_DAYS,
    max_files_per_directory: int = _MAX_DEBUG_LOG_FILES,
) -> int:
    base_dir = root or debug_logs_root()
    if not base_dir.exists():
        return 0

    removed = 0
    cutoff = time.time() - (max(1, int(retention_days)) * 24 * 60 * 60)
    files_by_directory: dict[Path, list[Path]] = defaultdict(list)

    for path in base_dir.rglob("*.log"):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        if stat.st_mtime < cutoff:
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
            continue
        files_by_directory[path.parent].append(path)

    for directory, files in files_by_directory.items():
        sorted_files = sorted(files, key=lambda candidate: candidate.stat().st_mtime, reverse=True)
        for stale in sorted_files[max_files_per_directory:]:
            try:
                stale.unlink()
                removed += 1
            except OSError:
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
            except OSError:
                continue
    except OSError:
        pass

    return removed


class DiagnosticsSession:
    def __init__(self, *, level_name: str, level: int, log_path: Path) -> None:
        self.level_name = level_name
        self.level = level
        self.log_path = log_path
        self._stream = self._open_stream(log_path)
        self._handler = logging.StreamHandler(self._stream)
        self._handler.setLevel(level)
        self._handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s %(levelname)s pid=%(process)d tid=%(thread)d %(name)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        self._logger = logging.getLogger(_LOGGER_NAME)
        self._previous_level = self._logger.level
        self._previous_propagate = self._logger.propagate
        self._previous_sys_excepthook = sys.excepthook
        self._previous_threading_excepthook = getattr(threading, "excepthook", None)
        self._previous_qt_message_handler = None
        self._qt_message_handler_installed = False
        self._closed = False
        self._configure_logger()
        self._install_exception_hooks()
        self._logger.info(
            "Diagnostics enabled level=%s path=%s pid=%s",
            self.level_name,
            self.log_path,
            os.getpid(),
        )
        atexit.register(self.close)

    @staticmethod
    def _open_stream(log_path: Path) -> TextIO:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        return log_path.open("a", encoding="utf-8", buffering=1)

    def _configure_logger(self) -> None:
        self._logger.addHandler(self._handler)
        self._logger.propagate = False
        if self._previous_level in {0, logging.NOTSET}:
            self._logger.setLevel(self.level)
        else:
            self._logger.setLevel(min(self._previous_level, self.level))

    def _install_exception_hooks(self) -> None:
        def _sys_excepthook(exc_type, exc_value, exc_traceback) -> None:  # noqa: ANN001
            if issubclass(exc_type, KeyboardInterrupt):
                self._previous_sys_excepthook(exc_type, exc_value, exc_traceback)
                return
            self._logger.critical(
                "Uncaught exception reached sys.excepthook.",
                exc_info=(exc_type, exc_value, exc_traceback),
            )
            self._previous_sys_excepthook(exc_type, exc_value, exc_traceback)

        def _threading_excepthook(args) -> None:  # noqa: ANN001
            exc_type = getattr(args, "exc_type", None)
            exc_value = getattr(args, "exc_value", None)
            exc_traceback = getattr(args, "exc_traceback", None)
            thread = getattr(args, "thread", None)
            if exc_type is not None and not issubclass(exc_type, KeyboardInterrupt):
                thread_name = getattr(thread, "name", "unknown")
                self._logger.critical(
                    "Uncaught thread exception thread=%s",
                    thread_name,
                    exc_info=(exc_type, exc_value, exc_traceback),
                )
            previous_hook = self._previous_threading_excepthook
            if callable(previous_hook):
                previous_hook(args)

        sys.excepthook = _sys_excepthook
        if callable(self._previous_threading_excepthook):
            threading.excepthook = _threading_excepthook

    def install_qt_message_handler(self) -> None:
        if self._qt_message_handler_installed:
            return
        self._previous_qt_message_handler = qInstallMessageHandler(self._qt_message_handler)
        self._qt_message_handler_installed = True
        self._logger.debug("Qt message handler installed.")

    def _qt_message_handler(self, mode, context, message) -> None:  # noqa: ANN001
        logger = logging.getLogger("snakesh.qt")
        location_parts = []
        file_name = getattr(context, "file", "") or ""
        if file_name:
            location_parts.append(str(file_name))
        line = getattr(context, "line", 0) or 0
        if line:
            location_parts.append(str(line))
        function_name = getattr(context, "function", "") or ""
        if function_name:
            location_parts.append(str(function_name))
        location = " | ".join(location_parts)
        payload = str(message or "")
        if location:
            payload = f"{payload} [{location}]"

        if mode == QtMsgType.QtDebugMsg:
            logger.debug(payload)
        elif mode == QtMsgType.QtInfoMsg:
            logger.info(payload)
        elif mode == QtMsgType.QtWarningMsg:
            logger.warning(payload)
        elif mode == QtMsgType.QtCriticalMsg:
            logger.error(payload)
        else:
            logger.critical(payload)

        previous_handler = self._previous_qt_message_handler
        if callable(previous_handler):
            previous_handler(mode, context, message)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True

        if self._qt_message_handler_installed:
            qInstallMessageHandler(self._previous_qt_message_handler)
            self._qt_message_handler_installed = False
        sys.excepthook = self._previous_sys_excepthook
        if callable(self._previous_threading_excepthook):
            threading.excepthook = self._previous_threading_excepthook

        try:
            self._logger.info("Diagnostics shutdown complete.")
        except Exception:
            pass
        try:
            self._handler.flush()
        except Exception:
            pass

        self._logger.removeHandler(self._handler)
        self._logger.propagate = self._previous_propagate
        self._logger.setLevel(self._previous_level)
        try:
            self._handler.close()
        except Exception:
            pass
        try:
            self._stream.close()
        except Exception:
            pass


def start_diagnostics_session(
    *,
    debug_level: str | None,
    debug_log_file: str | None = None,
) -> DiagnosticsSession | None:
    level = diagnostics_level_value(debug_level)
    if level is None:
        return None

    prune_debug_log_files()

    if debug_log_file:
        log_path = Path(debug_log_file).expanduser()
        try:
            log_path = log_path.resolve()
        except OSError:
            pass
    else:
        log_path = create_debug_log_path()

    return DiagnosticsSession(
        level_name=str(debug_level).strip().lower(),
        level=level,
        log_path=log_path,
    )


def _format_worker_context(context: dict[str, object] | None = None) -> str:
    if not context:
        return ""
    items = [f"{key}={value}" for key, value in sorted(context.items())]
    return " " + " ".join(items)


def log_worker_started(name: str, *, context: dict[str, object] | None = None) -> None:
    logging.getLogger(_WORKER_LOGGER_NAME).debug("Worker %s started.%s", name, _format_worker_context(context))


def log_worker_finished(
    name: str,
    duration_seconds: float,
    *,
    cancelled: bool = False,
    context: dict[str, object] | None = None,
) -> None:
    logger = logging.getLogger(_WORKER_LOGGER_NAME)
    if cancelled:
        logger.info(
            "Worker %s cancelled after %.3fs.%s",
            name,
            duration_seconds,
            _format_worker_context(context),
        )
        return
    logger.debug(
        "Worker %s finished in %.3fs.%s",
        name,
        duration_seconds,
        _format_worker_context(context),
    )


def log_worker_failed(
    name: str,
    duration_seconds: float,
    *,
    context: dict[str, object] | None = None,
) -> None:
    logging.getLogger(_WORKER_LOGGER_NAME).exception(
        "Worker %s failed after %.3fs.%s",
        name,
        duration_seconds,
        _format_worker_context(context),
    )
