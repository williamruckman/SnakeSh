from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import secrets
import socket
import socketserver
import threading
from typing import Any

if os.name == "nt":  # pragma: no cover - exercised on Windows
    import msvcrt
else:  # pragma: no cover - exercised on POSIX
    import fcntl


_ACTIVATION_HOST = "127.0.0.1"
_ACTIVATION_CONNECT_TIMEOUT_SECONDS = 0.75
_ACTIVATION_RESPONSE_TIMEOUT_SECONDS = 0.75
_LOCK_REGION_BYTES = 1


@dataclass(frozen=True)
class InstanceState:
    instance_key: str
    pid: int
    port: int
    token: str

    @classmethod
    def from_dict(
        cls,
        raw: Mapping[str, object],
        *,
        key_field: str,
        expected_key: str,
    ) -> "InstanceState" | None:
        instance_key = str(raw.get(key_field, "")).strip()
        token = str(raw.get("token", "")).strip()
        try:
            pid = int(raw.get("pid", 0))
            port = int(raw.get("port", 0))
        except (TypeError, ValueError):
            return None
        if instance_key != expected_key:
            return None
        if pid <= 0 or port <= 0 or not token:
            return None
        return cls(instance_key=instance_key, pid=pid, port=port, token=token)

    def to_dict(self, *, key_field: str) -> dict[str, object]:
        return {
            key_field: self.instance_key,
            "pid": self.pid,
            "port": self.port,
            "token": self.token,
        }


@dataclass(frozen=True)
class InstanceClaimResult:
    lease: "InstanceLease" | None = None
    activated_existing: bool = False


class _ActivationTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        *,
        instance_key: str,
        key_field: str,
        token: str,
        on_activate: Callable[[dict[str, object] | None], bool],
    ) -> None:
        self.instance_key = instance_key
        self.key_field = key_field
        self.token = token
        self.on_activate = on_activate
        super().__init__((_ACTIVATION_HOST, 0), _ActivationRequestHandler)


class _ActivationRequestHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        self.request.settimeout(_ACTIVATION_RESPONSE_TIMEOUT_SECONDS)
        ok = False
        try:
            payload = _receive_json_message(self.request)
            if (
                isinstance(payload, dict)
                and str(payload.get(self.server.key_field, "")).strip() == self.server.instance_key
                and str(payload.get("token", "")).strip() == self.server.token
            ):
                raw_request_payload = payload.get("payload")
                request_payload = raw_request_payload if isinstance(raw_request_payload, dict) else None
                ok = bool(self.server.on_activate(request_payload))
        except Exception:
            ok = False
        try:
            _send_json_message(self.request, {"ok": ok})
        except Exception:
            return


class InstanceLease:
    def __init__(
        self,
        *,
        instance_key: str,
        key_field: str,
        state_path: Path,
        lock_path: Path,
        state: InstanceState,
        server: _ActivationTCPServer,
        thread: threading.Thread,
    ) -> None:
        self.instance_key = instance_key
        self.key_field = key_field
        self.state_path = state_path
        self.lock_path = lock_path
        self.state = state
        self._server = server
        self._thread = thread
        self._released = False
        self._release_lock = threading.Lock()

    def release(self) -> None:
        with self._release_lock:
            if self._released:
                return
            self._released = True
            try:
                self._server.shutdown()
            except Exception:
                pass
            try:
                self._server.server_close()
            except Exception:
                pass
            if self._thread is not threading.current_thread():
                self._thread.join(timeout=2.0)
            with instance_file_lock(self.lock_path):
                remove_instance_state_unlocked(
                    self.state_path,
                    instance_key=self.instance_key,
                    key_field=self.key_field,
                    expected=self.state,
                )


def read_instance_state(
    *,
    state_path: Path,
    lock_path: Path,
    instance_key: str,
    key_field: str,
) -> InstanceState | None:
    with instance_file_lock(lock_path):
        return read_instance_state_unlocked(
            state_path,
            instance_key=instance_key,
            key_field=key_field,
        )


def has_active_instance(
    *,
    state_path: Path,
    lock_path: Path,
    instance_key: str,
    key_field: str,
) -> bool:
    with instance_file_lock(lock_path):
        state = read_instance_state_unlocked(
            state_path,
            instance_key=instance_key,
            key_field=key_field,
        )
        if state is None:
            return False
        if not process_is_running(state.pid):
            remove_instance_state_unlocked(
                state_path,
                instance_key=instance_key,
                key_field=key_field,
                expected=state,
            )
            return False
        return True


def activate_instance(
    *,
    state_path: Path,
    lock_path: Path,
    instance_key: str,
    key_field: str,
    payload: Mapping[str, object] | None = None,
) -> bool:
    with instance_file_lock(lock_path):
        state = read_instance_state_unlocked(
            state_path,
            instance_key=instance_key,
            key_field=key_field,
        )
        if state is None:
            return False
        if not process_is_running(state.pid):
            remove_instance_state_unlocked(
                state_path,
                instance_key=instance_key,
                key_field=key_field,
                expected=state,
            )
            return False
        if send_activation_request(state, key_field=key_field, payload=payload):
            return True
        remove_instance_state_unlocked(
            state_path,
            instance_key=instance_key,
            key_field=key_field,
            expected=state,
        )
        return False


def claim_instance(
    *,
    state_path: Path,
    lock_path: Path,
    instance_key: str,
    key_field: str,
    on_activate: Callable[[dict[str, object] | None], bool],
    activation_payload: Mapping[str, object] | None = None,
) -> InstanceClaimResult:
    token = secrets.token_urlsafe(24)
    server: _ActivationTCPServer | None = None
    state: InstanceState | None = None
    try:
        with instance_file_lock(lock_path):
            existing = read_instance_state_unlocked(
                state_path,
                instance_key=instance_key,
                key_field=key_field,
            )
            if existing is not None:
                if process_is_running(existing.pid) and send_activation_request(
                    existing,
                    key_field=key_field,
                    payload=activation_payload,
                ):
                    return InstanceClaimResult(lease=None, activated_existing=True)
                remove_instance_state_unlocked(
                    state_path,
                    instance_key=instance_key,
                    key_field=key_field,
                    expected=existing,
                )

            server = _ActivationTCPServer(
                instance_key=instance_key,
                key_field=key_field,
                token=token,
                on_activate=on_activate,
            )
            port = int(server.server_address[1])
            state = InstanceState(instance_key=instance_key, pid=os.getpid(), port=port, token=token)
            write_instance_state_unlocked(state_path, state, key_field=key_field)

        thread = threading.Thread(
            target=server.serve_forever,
            name=f"snakesh-instance-{instance_key}",
            daemon=True,
        )
        thread.start()
        return InstanceClaimResult(
            lease=InstanceLease(
                instance_key=instance_key,
                key_field=key_field,
                state_path=state_path,
                lock_path=lock_path,
                state=state,
                server=server,
                thread=thread,
            ),
            activated_existing=False,
        )
    except Exception:
        if server is not None:
            try:
                server.server_close()
            except Exception:
                pass
        if state is not None:
            with instance_file_lock(lock_path):
                remove_instance_state_unlocked(
                    state_path,
                    instance_key=instance_key,
                    key_field=key_field,
                    expected=state,
                )
        raise


def process_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        return _windows_process_is_running(pid)
    return _posix_process_is_running(pid)


def _windows_process_is_running(pid: int) -> bool:
    try:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        process_query_limited_information = 0x1000
        process_query_information = 0x0400
        error_access_denied = 5
        still_active = 259

        handle = 0
        for access in (process_query_limited_information, process_query_information):
            handle = kernel32.OpenProcess(access, False, int(pid))
            if handle:
                break
            if ctypes.get_last_error() == error_access_denied:
                return True
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return True
            return int(exit_code.value) == still_active
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return False


def _posix_process_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, SystemError):
        return False
    return True


def send_activation_request(
    state: InstanceState,
    *,
    key_field: str,
    payload: Mapping[str, object] | None,
) -> bool:
    request_payload = dict(payload) if payload is not None else None
    try:
        with socket.create_connection(
            (_ACTIVATION_HOST, state.port),
            timeout=_ACTIVATION_CONNECT_TIMEOUT_SECONDS,
        ) as sock:
            sock.settimeout(_ACTIVATION_RESPONSE_TIMEOUT_SECONDS)
            _send_json_message(
                sock,
                {
                    key_field: state.instance_key,
                    "token": state.token,
                    "payload": request_payload,
                },
            )
            response = _receive_json_message(sock)
    except Exception:
        return False
    return isinstance(response, dict) and bool(response.get("ok"))


def _send_json_message(sock: socket.socket, payload: Mapping[str, Any]) -> None:
    message = json.dumps(dict(payload), separators=(",", ":")).encode("utf-8") + b"\n"
    sock.sendall(message)


def _receive_json_message(sock: socket.socket) -> Any:
    chunks = bytearray()
    while len(chunks) < 64 * 1024:
        chunk = sock.recv(4096)
        if not chunk:
            break
        chunks.extend(chunk)
        if b"\n" in chunk:
            break
    if not chunks:
        raise ValueError("missing activation payload")
    line = bytes(chunks).split(b"\n", 1)[0]
    return json.loads(line.decode("utf-8"))


@contextmanager
def instance_file_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        acquire_file_lock(handle)
        try:
            yield
        finally:
            release_file_lock(handle)


def acquire_file_lock(handle) -> None:  # noqa: ANN001
    handle.seek(0, os.SEEK_END)
    if handle.tell() < _LOCK_REGION_BYTES:
        handle.write(b"0" * _LOCK_REGION_BYTES)
        handle.flush()
    handle.seek(0)
    if os.name == "nt":  # pragma: no cover - exercised on Windows
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, _LOCK_REGION_BYTES)
        return
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


def release_file_lock(handle) -> None:  # noqa: ANN001
    handle.seek(0)
    if os.name == "nt":  # pragma: no cover - exercised on Windows
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, _LOCK_REGION_BYTES)
        return
    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def read_instance_state_unlocked(
    state_path: Path,
    *,
    instance_key: str,
    key_field: str,
) -> InstanceState | None:
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    return InstanceState.from_dict(payload, key_field=key_field, expected_key=instance_key)


def write_instance_state_unlocked(
    state_path: Path,
    state: InstanceState,
    *,
    key_field: str,
) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state.to_dict(key_field=key_field), indent=2), encoding="utf-8")


def remove_instance_state_unlocked(
    state_path: Path,
    *,
    instance_key: str,
    key_field: str,
    expected: InstanceState | None = None,
) -> None:
    if expected is not None:
        current = read_instance_state_unlocked(
            state_path,
            instance_key=instance_key,
            key_field=key_field,
        )
        if current != expected:
            return
    try:
        state_path.unlink()
    except FileNotFoundError:
        return
    except Exception:
        return
