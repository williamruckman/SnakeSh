from __future__ import annotations

import asyncio
import json
from pathlib import Path
import signal
import struct
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from snakesh.ui import main_window
from snakesh.core.models import Protocol, Session
from snakesh.ui.main_window import LocalShellWorker, SSHShellWorker, WindowsLocalShellWorker, _WindowsConPTYBackend


def _build_worker() -> LocalShellWorker:
    return LocalShellWorker(
        program="/bin/sh",
        arguments=["-i"],
        working_directory=None,
        cols=80,
        rows=24,
    )


def _build_ssh_worker() -> SSHShellWorker:
    session = Session(
        id="ssh-worker-test",
        name="SSH Worker Test",
        host="127.0.0.1",
        protocol=Protocol.SSH,
        port=22,
        username="tester",
    )
    return SSHShellWorker(
        session=session,
        password=None,
        trust_unknown=False,
        x11_forwarding=False,
        cols=80,
        rows=24,
    )


class _FakeWindowsConPTYBackend:
    instances: list["_FakeWindowsConPTYBackend"] = []

    def __init__(
        self,
        *,
        program: str,
        arguments: list[str],
        working_directory: str | None,
        cols: int,
        rows: int,
    ) -> None:
        self.program = program
        self.arguments = list(arguments)
        self.working_directory = working_directory
        self.cols = cols
        self.rows = rows
        self.payloads = [b"PS C:\\> "]
        self.writes: list[bytes] = []
        self.resizes: list[tuple[int, int]] = []
        self.started = False
        self.closed = False
        self.__class__.instances.append(self)

    def start(self) -> None:
        self.started = True

    def bytes_available(self) -> int:
        if not self.payloads:
            return 0
        return len(self.payloads[0])

    def read(self, max_bytes: int) -> bytes:
        if not self.payloads:
            return b""
        payload = self.payloads.pop(0)
        return payload[:max_bytes]

    def write(self, payload: bytes) -> None:
        self.writes.append(bytes(payload))

    def resize(self, cols: int, rows: int) -> None:
        self.resizes.append((cols, rows))

    def has_exited(self) -> bool:
        return not self.payloads

    def close(self) -> None:
        self.closed = True


class LocalShellWorkerTests(unittest.TestCase):
    def test_windows_hidden_process_backend_uses_create_no_window(self) -> None:
        class _FakeStartupInfo:
            def __init__(self) -> None:
                self.dwFlags = 0
                self.wShowWindow = 1

        class _FakeStdout:
            def __init__(self) -> None:
                self.closed = False

            def fileno(self) -> int:
                return 51

            def read(self, _size: int) -> bytes:
                return b""

            def close(self) -> None:
                self.closed = True

        class _FakeStdin:
            def __init__(self) -> None:
                self.closed = False

            def write(self, _payload: bytes) -> int:
                return 0

            def flush(self) -> None:
                return None

            def close(self) -> None:
                self.closed = True

        class _FakeProcess:
            def __init__(self) -> None:
                self.stdin = _FakeStdin()
                self.stdout = _FakeStdout()
                self.returncode = 0
                self.killed = False

            def poll(self) -> int | None:
                return self.returncode

            def wait(self, timeout: float | None = None) -> int:
                return self.returncode

            def terminate(self) -> None:
                self.returncode = 1

            def kill(self) -> None:
                self.killed = True
                self.returncode = 1

        fake_process = _FakeProcess()
        backend = main_window._WindowsHiddenProcessBackend(
            program="powershell.exe",
            arguments=["-NoLogo", "-NoProfile"],
            working_directory="C:\\",
            cols=80,
            rows=24,
        )

        with (
            patch("snakesh.ui.main_window.os.name", "nt"),
            patch.dict("snakesh.ui.main_window.os.environ", {"COLUMNS": "132", "LINES": "44"}, clear=False),
            patch.object(main_window.subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True),
            patch.object(main_window.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200, create=True),
            patch.object(main_window.subprocess, "STARTUPINFO", _FakeStartupInfo, create=True),
            patch.object(main_window.subprocess, "STARTF_USESHOWWINDOW", 1, create=True),
            patch.object(main_window.subprocess, "SW_HIDE", 0, create=True),
            patch.object(main_window.subprocess, "Popen", return_value=fake_process) as mock_popen,
            patch.dict(sys.modules, {"msvcrt": SimpleNamespace(get_osfhandle=lambda _fd: 4321)}),
        ):
            backend.start()

        kwargs = mock_popen.call_args.kwargs
        self.assertEqual(
            kwargs["creationflags"],
            0x08000000 | 0x00000200,
        )
        self.assertEqual(kwargs["stdin"], subprocess.PIPE)
        self.assertEqual(kwargs["stdout"], subprocess.PIPE)
        self.assertEqual(kwargs["stderr"], subprocess.STDOUT)
        self.assertNotIn("COLUMNS", kwargs["env"])
        self.assertNotIn("LINES", kwargs["env"])
        self.assertEqual(getattr(kwargs["startupinfo"], "dwFlags", 0), 1)
        self.assertEqual(getattr(kwargs["startupinfo"], "wShowWindow", 1), 0)
        self.assertEqual(backend._stdout_handle, 4321)

    def test_windows_conpty_backend_keeps_pointer_sized_handles_in_ctypes_wrappers(self) -> None:
        class _FakeWinFunc:
            def __init__(self, callback) -> None:
                self._callback = callback
                self.argtypes = None
                self.restype = None

            def __call__(self, *args, **kwargs):
                return self._callback(*args, **kwargs)

        handles = iter([101, 102, 103, 104])
        pseudo_console_value = (2**63) + 99
        recorded_attr_value: object | None = None

        def _create_pipe(read_ptr, write_ptr, _security, _size):
            read_ptr._obj.value = next(handles)
            write_ptr._obj.value = next(handles)
            return 1

        def _initialize_attr_list(attr_list, _count, _flags, size_ptr):
            if not attr_list:
                size_ptr._obj.value = 64
                return 0
            return 1

        def _create_pseudo_console(_size, _input_read, _output_write, _flags, pseudo_console_ptr):
            pseudo_console_ptr._obj.value = pseudo_console_value
            return 0

        def _create_process(_app, _cmdline, _proc_attrs, _thread_attrs, _inherit, _flags, _env, _cwd, _startup, process_info_ptr):
            process_info_ptr._obj.hProcess = 201
            process_info_ptr._obj.hThread = 202
            process_info_ptr._obj.dwProcessId = 203
            process_info_ptr._obj.dwThreadId = 204
            return 1

        def _update_proc_thread_attribute(_attr_list, _flags, _attribute, value, _size, _previous, _returned):
            nonlocal recorded_attr_value
            recorded_attr_value = value
            return 1

        fake_kernel32 = SimpleNamespace(
            CreatePipe=_FakeWinFunc(_create_pipe),
            SetHandleInformation=_FakeWinFunc(lambda *_args: 1),
            CreatePseudoConsole=_FakeWinFunc(_create_pseudo_console),
            InitializeProcThreadAttributeList=_FakeWinFunc(_initialize_attr_list),
            UpdateProcThreadAttribute=_FakeWinFunc(_update_proc_thread_attribute),
            DeleteProcThreadAttributeList=_FakeWinFunc(lambda *_args: None),
            CreateProcessW=_FakeWinFunc(_create_process),
            CloseHandle=_FakeWinFunc(lambda *_args: 1),
            ClosePseudoConsole=_FakeWinFunc(lambda *_args: None),
        )

        backend = _WindowsConPTYBackend(
            program="powershell.exe",
            arguments=["-NoLogo", "-NoProfile"],
            working_directory=None,
            cols=80,
            rows=24,
        )

        with (
            patch.object(_WindowsConPTYBackend, "is_supported", return_value=True),
            patch("ctypes.WinDLL", return_value=fake_kernel32, create=True),
        ):
            backend.start()

        self.assertEqual(getattr(backend._pseudo_console, "value", None), pseudo_console_value)
        self.assertEqual(getattr(backend._input_write_handle, "value", None), 102)
        self.assertEqual(getattr(backend._output_read_handle, "value", None), 103)
        self.assertEqual(getattr(backend._process_handle, "value", None), 201)
        self.assertEqual(backend._process_id, 203)
        self.assertEqual(getattr(recorded_attr_value, "value", None), pseudo_console_value)
        self.assertIsNotNone(fake_kernel32.CreatePseudoConsole.argtypes)
        self.assertIsNotNone(fake_kernel32.CreateProcessW.argtypes)

    def test_run_child_process_drops_static_columns_and_lines_from_environment(self) -> None:
        worker = _build_worker()
        captured: dict[str, object] = {}

        def _fake_execvpe(program, argv, env):  # noqa: ANN001
            captured["program"] = program
            captured["argv"] = list(argv)
            captured["env"] = dict(env)
            raise RuntimeError("stop")

        with (
            patch.dict("snakesh.ui.main_window.os.environ", {"COLUMNS": "132", "LINES": "44"}, clear=False),
            patch("snakesh.ui.main_window.os.execvpe", side_effect=_fake_execvpe),
            patch("snakesh.ui.main_window.os.write"),
        ):
            worker._run_child_process()

        self.assertEqual(captured["program"], "/bin/sh")
        self.assertEqual(captured["argv"], ["/bin/sh", "-i"])
        env = captured["env"]
        assert isinstance(env, dict)
        self.assertNotIn("COLUMNS", env)
        self.assertNotIn("LINES", env)
        self.assertTrue((env.get("TERM") or "").strip())

    def test_apply_terminal_size_updates_winsize_and_signals_foreground_process_group(self) -> None:
        worker = _build_worker()
        worker._master_fd = 42
        worker._child_pid = 111

        with (
            patch("snakesh.ui.main_window.fcntl.ioctl") as mock_ioctl,
            patch("snakesh.ui.main_window.os.tcgetpgrp", return_value=333) as mock_tcgetpgrp,
            patch("snakesh.ui.main_window.os.getpgid") as mock_getpgid,
            patch("snakesh.ui.main_window.os.killpg") as mock_killpg,
            patch("snakesh.ui.main_window.os.kill") as mock_kill,
        ):
            worker._apply_terminal_size(120, 40)

        mock_ioctl.assert_called_once()
        fd, request, packed = mock_ioctl.call_args.args
        self.assertEqual(fd, 42)
        self.assertEqual(request, main_window.termios.TIOCSWINSZ)
        self.assertEqual(struct.unpack("HHHH", packed), (40, 120, 0, 0))
        mock_tcgetpgrp.assert_called_once_with(42)
        mock_getpgid.assert_not_called()
        mock_killpg.assert_called_once_with(333, signal.SIGWINCH)
        mock_kill.assert_not_called()

    def test_apply_terminal_size_falls_back_to_child_group_when_foreground_lookup_fails(self) -> None:
        worker = _build_worker()
        worker._master_fd = 42
        worker._child_pid = 111

        with (
            patch("snakesh.ui.main_window.fcntl.ioctl"),
            patch("snakesh.ui.main_window.os.tcgetpgrp", side_effect=OSError("no tty")),
            patch("snakesh.ui.main_window.os.getpgid", return_value=222),
            patch("snakesh.ui.main_window.os.killpg") as mock_killpg,
            patch("snakesh.ui.main_window.os.kill") as mock_kill,
        ):
            worker._apply_terminal_size(120, 40)

        mock_killpg.assert_called_once_with(222, signal.SIGWINCH)
        mock_kill.assert_not_called()

    def test_apply_terminal_size_falls_back_to_child_pid_when_group_signal_fails(self) -> None:
        worker = _build_worker()
        worker._master_fd = 42
        worker._child_pid = 111

        with (
            patch("snakesh.ui.main_window.fcntl.ioctl"),
            patch("snakesh.ui.main_window.os.tcgetpgrp", return_value=333),
            patch("snakesh.ui.main_window.os.killpg", side_effect=ProcessLookupError),
            patch("snakesh.ui.main_window.os.getpgid", return_value=222),
            patch("snakesh.ui.main_window.os.kill") as mock_kill,
        ):
            worker._apply_terminal_size(120, 40)

        mock_kill.assert_called_once_with(111, signal.SIGWINCH)

    def test_apply_terminal_size_skips_signaling_when_ioctl_fails(self) -> None:
        worker = _build_worker()
        worker._master_fd = 42
        worker._child_pid = 111

        with (
            patch("snakesh.ui.main_window.fcntl.ioctl", side_effect=OSError("boom")),
            patch("snakesh.ui.main_window.os.tcgetpgrp") as mock_tcgetpgrp,
            patch("snakesh.ui.main_window.os.getpgid") as mock_getpgid,
            patch("snakesh.ui.main_window.os.killpg") as mock_killpg,
            patch("snakesh.ui.main_window.os.kill") as mock_kill,
        ):
            worker._apply_terminal_size(120, 40)

        mock_tcgetpgrp.assert_not_called()
        mock_getpgid.assert_not_called()
        mock_killpg.assert_not_called()
        mock_kill.assert_not_called()

    def test_incremental_decoder_reassembles_split_multibyte_utf8(self) -> None:
        worker = _build_worker()
        emitted: list[str] = []
        worker.output.connect(emitted.append)
        payload = "€".encode("utf-8")

        worker._emit_decoded_output(payload[:1])
        worker._emit_decoded_output(payload[1:])
        worker._flush_decoder()

        self.assertEqual(emitted, ["€"])

    def test_decoder_flush_emits_replacement_for_truncated_utf8(self) -> None:
        worker = _build_worker()
        emitted: list[str] = []
        worker.output.connect(emitted.append)
        payload = "€".encode("utf-8")

        worker._emit_decoded_output(payload[:2])
        worker._flush_decoder()

        self.assertEqual(emitted, ["�"])

    def test_terminal_generated_input_is_skipped_while_local_pty_is_canonical(self) -> None:
        worker = _build_worker()
        worker._master_fd = 42
        worker._child_pid = 111

        attrs = [0, 0, 0, main_window.termios.ICANON, 0, 0]
        with (
            patch("snakesh.ui.main_window.termios.tcgetattr", return_value=attrs),
            patch.object(worker, "_wake_command_waiter") as wake_command_waiter,
        ):
            worker.send_terminal_generated_input("\x1b[?6c")

        self.assertTrue(worker._command_queue.empty())
        wake_command_waiter.assert_not_called()

    def test_terminal_generated_input_is_sent_while_local_pty_is_raw(self) -> None:
        worker = _build_worker()
        worker._master_fd = 42
        worker._child_pid = 111

        attrs = [0, 0, 0, 0, 0, 0]
        with (
            patch("snakesh.ui.main_window.termios.tcgetattr", return_value=attrs),
            patch.object(worker, "_wake_command_waiter") as wake_command_waiter,
        ):
            worker.send_terminal_generated_input("\x1b[?6c")

        self.assertEqual(worker._command_queue.get_nowait(), ("input", "\x1b[?6c"))
        wake_command_waiter.assert_called_once_with()

    def test_invalid_utf8_does_not_poison_later_valid_output(self) -> None:
        worker = _build_worker()
        emitted: list[str] = []
        worker.output.connect(emitted.append)

        worker._emit_decoded_output(b"\xff")
        worker._emit_decoded_output("ok".encode("utf-8"))
        worker._flush_decoder()

        self.assertEqual("".join(emitted), "�ok")

    def test_ssh_worker_incremental_decoder_reassembles_split_multibyte_utf8(self) -> None:
        worker = _build_ssh_worker()
        emitted: list[str] = []
        worker.output.connect(emitted.append)
        payload = "€".encode("utf-8")

        worker._emit_decoded_output(payload[:1])
        worker._emit_decoded_output(payload[1:])
        worker._flush_decoder()

        self.assertEqual(emitted, ["€"])

    def test_ssh_worker_write_stream_writes_raw_bytes(self) -> None:
        worker = _build_ssh_worker()
        stdin = SimpleNamespace(writes=[])
        stdin.write = lambda payload: stdin.writes.append(payload)
        worker._proc = SimpleNamespace(stdin=stdin)

        async def _drive() -> None:
            worker._queue = asyncio.Queue()
            await worker._queue.put("hé".encode("utf-8"))
            await worker._queue.put(None)
            await worker._write_stream()

        asyncio.run(_drive())

        self.assertEqual(stdin.writes, ["hé".encode("utf-8")])

    def test_ssh_worker_requests_raw_asyncssh_process_streams(self) -> None:
        worker = _build_ssh_worker()
        create_process_kwargs: dict[str, object] = {}

        class _FakeStdout:
            async def read(self, _size: int) -> bytes:
                return b""

        class _FakeStdin:
            def write(self, _payload: bytes) -> None:
                return None

            def write_eof(self) -> None:
                return None

        class _FakeChannel:
            def change_terminal_size(self, _cols: int, _rows: int) -> None:
                return None

        class _FakeProc:
            def __init__(self) -> None:
                self.stdout = _FakeStdout()
                self.stdin = _FakeStdin()
                self.channel = _FakeChannel()

        class _FakeConn:
            async def create_process(self, **kwargs):
                create_process_kwargs.update(kwargs)
                return _FakeProc()

        async def _fake_connect(**_kwargs):
            return _FakeConn()

        with (
            patch("snakesh.ui.main_window.SSHClient._connect_kwargs", return_value={"host": "127.0.0.1"}),
            patch("snakesh.ui.main_window.asyncssh.connect", side_effect=_fake_connect),
            patch.object(worker, "_start_configured_tunnels", AsyncMock()),
        ):
            asyncio.run(worker._run())

        self.assertIn("encoding", create_process_kwargs)
        self.assertIsNone(create_process_kwargs["encoding"])
        self.assertEqual(
            create_process_kwargs.get("term_modes"),
            {main_window.asyncssh.PTY_VERASE: 0x7F},
        )

    def test_ssh_capture_mode_records_generic_output_without_session_details(self) -> None:
        worker = _build_ssh_worker()

        class _FakeStdout:
            def __init__(self) -> None:
                self._payloads = [b"OK", b""]

            async def read(self, _size: int) -> bytes:
                return self._payloads.pop(0)

        class _FakeStdin:
            def write(self, _payload: bytes) -> None:
                return None

        class _FakeChannel:
            def change_terminal_size(self, _cols: int, _rows: int) -> None:
                return None

        class _FakeProc:
            def __init__(self) -> None:
                self.stdout = _FakeStdout()
                self.stdin = _FakeStdin()
                self.channel = _FakeChannel()

        class _FakeConn:
            async def create_process(self, **_kwargs):
                return _FakeProc()

        async def _fake_connect(**_kwargs):
            return _FakeConn()

        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch.dict(
                    "snakesh.ui.main_window.os.environ",
                    {main_window.TERMINAL_CAPTURE_DIR_ENV: tmp},
                    clear=False,
                ),
                patch("snakesh.ui.main_window.SSHClient._connect_kwargs", return_value={"host": "unused"}),
                patch("snakesh.ui.main_window.asyncssh.connect", side_effect=_fake_connect),
                patch.object(worker, "_start_configured_tunnels", AsyncMock()),
            ):
                asyncio.run(worker._run())

            paths = list(Path(tmp).glob("ssh-terminal-*.jsonl"))
            self.assertEqual(len(paths), 1)
            rows = [
                json.loads(line)
                for line in paths[0].read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

        self.assertEqual(rows[0]["type"], "meta")
        self.assertEqual(rows[0]["program"], "ssh")
        self.assertEqual(rows[0]["argv"], ["ssh"])
        self.assertEqual(rows[0]["protocol"], "ssh")
        self.assertNotIn("host", rows[0])
        self.assertEqual(rows[1], {"type": "output", "data_b64": "T0s="})
        self.assertEqual(rows[-1], {"type": "close"})

    def test_capture_mode_records_output_input_and_resize_without_changing_emitted_text(self) -> None:
        worker = _build_worker()
        worker._master_fd = 42
        worker._child_pid = 111
        emitted: list[str] = []
        worker.output.connect(emitted.append)

        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict("snakesh.ui.main_window.os.environ", {main_window.LOCAL_PTY_CAPTURE_DIR_ENV: tmp}, clear=False):
                worker._capture_recorder = main_window._LocalPTYCaptureRecorder.create(
                    program="/bin/sh",
                    arguments=["-i"],
                    working_directory="/tmp",
                    term="xterm-256color",
                    cols=80,
                    rows=24,
                    child_pid=111,
                )
                assert worker._capture_recorder is not None

                with patch("snakesh.ui.main_window.os.write", return_value=2):
                    worker._write_to_pty("hi")

                worker._emit_decoded_output("hé".encode("utf-8"))

                with (
                    patch("snakesh.ui.main_window.fcntl.ioctl"),
                    patch("snakesh.ui.main_window.os.tcgetpgrp", return_value=333),
                    patch("snakesh.ui.main_window.os.killpg"),
                    patch("snakesh.ui.main_window.os.kill"),
                ):
                    worker._apply_terminal_size(120, 40)

                worker._capture_recorder.record_output("hé".encode("utf-8"))
                recorder_path = worker._capture_recorder.path
                worker._close_capture_recorder()

            rows = [
                json.loads(line)
                for line in recorder_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

        self.assertEqual(emitted, ["hé"])
        self.assertEqual(rows[0]["type"], "meta")
        self.assertEqual(rows[1], {"type": "input", "data_b64": "aGk="})
        self.assertEqual(rows[2], {"type": "resize", "cols": 120, "rows": 40})
        self.assertEqual(rows[3]["type"], "output")
        self.assertEqual(rows[4], {"type": "close"})

    def test_windows_local_shell_worker_routes_input_resize_and_output_through_backend(self) -> None:
        _FakeWindowsConPTYBackend.instances.clear()
        worker = WindowsLocalShellWorker(
            program="powershell.exe",
            arguments=["-NoLogo", "-NoProfile"],
            working_directory=None,
            cols=80,
            rows=24,
            backend_factory=_FakeWindowsConPTYBackend,
        )
        emitted: list[str] = []
        errors: list[str] = []
        worker.output.connect(emitted.append)
        worker.error.connect(errors.append)

        def _queue_commands() -> None:
            worker.send_text("\x1b[A")
            worker.resize_terminal(120, 40)

        worker.connected.connect(_queue_commands)
        with patch("snakesh.ui.main_window.os.name", "nt"):
            worker.start()

        backend = _FakeWindowsConPTYBackend.instances[-1]
        self.assertTrue(backend.started)
        self.assertEqual(emitted, ["PS C:\\> "])
        self.assertEqual(errors, [])
        self.assertEqual(backend.writes, [b"\x1b[A"])
        self.assertEqual(backend.resizes, [(120, 40)])
        self.assertTrue(backend.closed)
