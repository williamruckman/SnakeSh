from __future__ import annotations

import unittest
from unittest.mock import patch

from snakesh.ui.main_window import SerialShellWorker


def _build_worker() -> SerialShellWorker:
    return SerialShellWorker(
        port="/dev/ttyACM0",
        baud_rate=115200,
        data_bits=8,
        parity="none",
        stop_bits="1",
        flow_control="none",
        cols=80,
        rows=24,
    )


class SerialWorkerTests(unittest.TestCase):
    def test_start_prefers_posix_backend_when_available_without_pyserial(self) -> None:
        worker = _build_worker()
        errors: list[str] = []
        connected: list[bool] = []
        closed: list[bool] = []
        worker.error.connect(errors.append)
        worker.connected.connect(lambda: connected.append(True))
        worker.closed.connect(lambda: closed.append(True))

        with (
            patch("snakesh.ui.main_window.os.name", "posix"),
            patch.object(worker, "_open_posix_serial", return_value=True) as mock_open_posix,
            patch.object(
                worker,
                "_import_pyserial",
                return_value=(None, ModuleNotFoundError("No module named 'serial'")),
            ) as mock_import_pyserial,
            patch.object(worker, "_open_pyserial", return_value=True) as mock_open_pyserial,
            patch.object(worker, "_run_io_loop"),
            patch.object(worker, "_cleanup_serial"),
        ):
            worker.start()

        self.assertEqual(errors, [])
        self.assertEqual(connected, [True])
        self.assertEqual(closed, [True])
        mock_open_posix.assert_called_once_with()
        mock_import_pyserial.assert_not_called()
        mock_open_pyserial.assert_not_called()

    def test_start_uses_pyserial_fallback_on_posix_when_native_open_fails(self) -> None:
        worker = _build_worker()
        connected: list[bool] = []
        closed: list[bool] = []
        worker.connected.connect(lambda: connected.append(True))
        worker.closed.connect(lambda: closed.append(True))

        fake_serial_module = object()
        with (
            patch("snakesh.ui.main_window.os.name", "posix"),
            patch.object(worker, "_open_posix_serial", return_value=False) as mock_open_posix,
            patch.object(worker, "_import_pyserial", return_value=(fake_serial_module, None)) as mock_import_pyserial,
            patch.object(worker, "_open_pyserial", return_value=True) as mock_open_pyserial,
            patch.object(worker, "_run_io_loop"),
            patch.object(worker, "_cleanup_serial"),
        ):
            worker.start()

        self.assertEqual(connected, [True])
        self.assertEqual(closed, [True])
        mock_open_posix.assert_called_once_with()
        mock_import_pyserial.assert_called_once_with()
        mock_open_pyserial.assert_called_once_with(fake_serial_module)

    def test_start_requires_pyserial_on_non_posix_when_missing(self) -> None:
        worker = _build_worker()
        errors: list[str] = []
        connected: list[bool] = []
        closed: list[bool] = []
        worker.error.connect(errors.append)
        worker.connected.connect(lambda: connected.append(True))
        worker.closed.connect(lambda: closed.append(True))

        with (
            patch("snakesh.ui.main_window.os.name", "nt"),
            patch.object(
                worker,
                "_import_pyserial",
                return_value=(None, ModuleNotFoundError("No module named 'serial'")),
            ),
            patch.object(worker, "_open_posix_serial", return_value=False) as mock_open_posix,
            patch.object(worker, "_open_pyserial", return_value=False) as mock_open_pyserial,
        ):
            worker.start()

        self.assertEqual(connected, [])
        self.assertEqual(closed, [True])
        self.assertEqual(errors, ["PySerial is unavailable: No module named 'serial'"])
        mock_open_posix.assert_not_called()
        mock_open_pyserial.assert_not_called()

    def test_set_posix_modem_lines_asserts_dtr_and_rts(self) -> None:
        worker = _build_worker()
        ioctl_calls: list[tuple[int, int]] = []
        captured_bits: list[int] = []

        def _fake_ioctl(fd, request, arg, mutate=False):  # noqa: ANN001
            ioctl_calls.append((fd, request))
            if request == 100:
                arg[0] = 0
                return 0
            if request == 101:
                captured_bits.append(int(arg[0]))
                return 0
            raise AssertionError(f"Unexpected ioctl request: {request}")

        with (
            patch("snakesh.ui.main_window.os.name", "posix"),
            patch("snakesh.ui.main_window.termios.TIOCMGET", 100, create=True),
            patch("snakesh.ui.main_window.termios.TIOCMSET", 101, create=True),
            patch("snakesh.ui.main_window.termios.TIOCM_DTR", 0x002, create=True),
            patch("snakesh.ui.main_window.termios.TIOCM_RTS", 0x004, create=True),
            patch("snakesh.ui.main_window.fcntl.ioctl", side_effect=_fake_ioctl),
        ):
            worker._set_posix_modem_lines(42)

        self.assertEqual(ioctl_calls, [(42, 100), (42, 101)])
        self.assertEqual(captured_bits, [0x002 | 0x004])
