from __future__ import annotations

import asyncio
import gzip
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
import socket
import ssl
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from aiohttp import ClientSession, WSMsgType, web

from snakesh.services.web_server_service import (
    WEB_SERVER_HELPER_FLAG,
    WebServerConfig,
    WebServerStatus,
    _pid_exists,
    _build_certbot_command,
    build_server_url,
    ensure_self_signed_certificate,
    helper_launch_command,
    launch_web_server_helper_elevated,
    needs_gui_elevation,
    prune_web_server_log_files,
    read_web_server_status,
    request_web_server_stop,
    run_web_server_helper,
    web_server_instance_paths,
    web_server_logs_root,
    write_web_server_config,
    write_web_server_status,
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _EchoHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        body = f"upstream:{self.path}".encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return


def _start_http_upstream(port: int) -> tuple[ThreadingHTTPServer, threading.Thread]:
    server = ThreadingHTTPServer(("127.0.0.1", port), _EchoHTTPRequestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _start_websocket_upstream(port: int) -> tuple[dict[str, object], threading.Thread]:
    state: dict[str, object] = {}
    ready = threading.Event()

    async def websocket_handler(request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        async for message in ws:
            if message.type == WSMsgType.TEXT:
                await ws.send_str(f"echo:{message.data}")
        return ws

    def runner() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        state["loop"] = loop
        app = web.Application()
        app.router.add_get("/{tail:.*}", websocket_handler)
        runner_obj = web.AppRunner(app)
        loop.run_until_complete(runner_obj.setup())
        site = web.TCPSite(runner_obj, "127.0.0.1", port)
        loop.run_until_complete(site.start())
        state["runner"] = runner_obj
        ready.set()
        try:
            loop.run_forever()
        finally:
            loop.run_until_complete(runner_obj.cleanup())
            loop.close()

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    ready.wait(timeout=5)
    return state, thread


def _stop_websocket_upstream(state: dict[str, object], thread: threading.Thread) -> None:
    loop = state.get("loop")
    if isinstance(loop, asyncio.AbstractEventLoop):
        loop.call_soon_threadsafe(loop.stop)
    thread.join(timeout=5)


def _start_malformed_cache_upstream(port: int) -> tuple[socket.socket, threading.Thread]:
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind(("127.0.0.1", port))
    server_sock.listen()

    def runner() -> None:
        while True:
            try:
                conn, _ = server_sock.accept()
            except OSError:
                break
            with conn:
                request = b""
                while b"\r\n\r\n" not in request:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    request += chunk
                if b"If-None-Match" in request:
                    malformed_body = gzip.compress(b"unexpected")
                    response = (
                        b"HTTP/1.1 304 Not Modified\r\n"
                        b"Connection: close\r\n"
                        b"Content-Encoding: gzip\r\n"
                        b"\r\n"
                        + malformed_body
                    )
                else:
                    payload = b"asset ok"
                    response = (
                        b"HTTP/1.1 200 OK\r\n"
                        b"Connection: close\r\n"
                        b"Content-Type: text/plain; charset=utf-8\r\n"
                        b"Content-Length: 8\r\n"
                        b"\r\n"
                        + payload
                    )
                conn.sendall(response)

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    return server_sock, thread


class WebServerServiceTests(unittest.TestCase):
    def _free_port_or_skip(self) -> int:
        try:
            return _free_port()
        except PermissionError as exc:
            self.skipTest(f"Socket creation is blocked in this environment: {exc}")

    def test_needs_gui_elevation_for_privileged_listener_or_certbot_port(self) -> None:
        listener_config = WebServerConfig(bind_host="127.0.0.1", port=443, mode="static", document_root=".")
        certbot_config = WebServerConfig(
            bind_host="127.0.0.1",
            port=8443,
            mode="static",
            document_root=".",
            tls_mode="certbot",
            certbot_executable="/usr/bin/certbot",
            certbot_primary_domain="example.com",
            certbot_email="admin@example.com",
            certbot_challenge_port=80,
        )
        with patch("snakesh.services.web_server_service._resolve_executable_path", return_value="/usr/bin/certbot"):
            self.assertTrue(needs_gui_elevation(listener_config, platform_name="linux"))
            self.assertTrue(needs_gui_elevation(certbot_config, platform_name="linux"))
        self.assertFalse(needs_gui_elevation(listener_config, platform_name="windows"))

    def test_build_server_url_normalizes_any_address(self) -> None:
        self.assertEqual(build_server_url("0.0.0.0", 8080, "http"), "http://127.0.0.1:8080/")

    def test_helper_launch_command_uses_internal_flag(self) -> None:
        command = helper_launch_command("/tmp/example")
        self.assertIn(WEB_SERVER_HELPER_FLAG, command)
        self.assertIn("/tmp/example", command)

    def test_launch_web_server_helper_elevated_linux_uses_pkexec(self) -> None:
        with (
            patch("snakesh.services.web_server_service.shutil.which", return_value="/usr/bin/pkexec"),
            patch("snakesh.services.web_server_service.subprocess.run") as mock_run,
            patch("snakesh.services.web_server_service.runtime.self_launch_command", return_value=["snakesh", "--web-server-helper", "/tmp/x"]),
        ):
            mock_run.return_value.returncode = 0
            launch_web_server_helper_elevated("/tmp/x", platform_name="linux")

        self.assertEqual(mock_run.call_args.args[0][:2], ["pkexec", "/bin/sh"])

    def test_pid_exists_uses_windows_process_query_api(self) -> None:
        class FakeKernel32:
            def OpenProcess(self, access, inherit_handle, pid):  # noqa: ANN001
                self.access = access
                self.inherit_handle = inherit_handle
                self.pid = pid
                return 41

            def GetExitCodeProcess(self, handle, exit_code_ptr):  # noqa: ANN001
                self.handle = handle
                exit_code_ptr._obj.value = 259
                return True

            def CloseHandle(self, handle):  # noqa: ANN001
                self.closed_handle = handle
                return True

        fake_kernel32 = FakeKernel32()
        with (
            patch("snakesh.services.web_server_service._platform_name", return_value="windows"),
            patch("ctypes.windll", new=SimpleNamespace(kernel32=fake_kernel32), create=True),
            patch("ctypes.get_last_error", return_value=0, create=True),
            patch("snakesh.services.web_server_service.os.kill", side_effect=AssertionError("os.kill should not be used")),
        ):
            self.assertTrue(_pid_exists(1234))

        self.assertEqual(fake_kernel32.pid, 1234)
        self.assertEqual(fake_kernel32.handle, 41)
        self.assertEqual(fake_kernel32.closed_handle, 41)

    def test_static_http_helper_serves_index_and_directory_listing_toggle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            root.mkdir()
            (root / "index.html").write_text("hello from index", encoding="utf-8")
            (root / "subdir").mkdir()
            (root / "subdir" / "child.txt").write_text("child", encoding="utf-8")
            instance = Path(tmp) / "instance"
            instance.mkdir()
            config = WebServerConfig(
                bind_host="127.0.0.1",
                port=self._free_port_or_skip(),
                mode="static",
                document_root=str(root),
                index_page="index.html",
                allow_directory_listing=False,
            )
            write_web_server_config(instance, config)
            result: dict[str, int] = {}
            thread = threading.Thread(target=lambda: result.setdefault("exit_code", run_web_server_helper(instance)), daemon=True)
            thread.start()
            status = self._wait_for_status(instance, "running")

            with urllib.request.urlopen(status.url, timeout=5) as response:
                body = response.read().decode("utf-8")
            self.assertIn("hello from index", body)
            self.assertTrue(status.log_path)
            self.assertTrue(Path(status.log_path).exists())

            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(status.url + "subdir/", timeout=5)

            request_web_server_stop(instance)
            thread.join(timeout=5)
            self.assertEqual(result.get("exit_code"), 0)

    def test_manual_tls_with_chain_file_serves_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            root.mkdir()
            (root / "index.html").write_text("secure index", encoding="utf-8")
            instance = Path(tmp) / "instance"
            instance.mkdir()
            cert_paths = web_server_instance_paths(instance)
            cert_path, key_path = ensure_self_signed_certificate(cert_paths, bind_host="127.0.0.1")
            config = WebServerConfig(
                bind_host="127.0.0.1",
                port=self._free_port_or_skip(),
                mode="static",
                document_root=str(root),
                index_page="index.html",
                tls_mode="manual",
                cert_file=cert_path,
                key_file=key_path,
                chain_file=cert_path,
            )
            write_web_server_config(instance, config)
            result: dict[str, int] = {}
            thread = threading.Thread(target=lambda: result.setdefault("exit_code", run_web_server_helper(instance)), daemon=True)
            thread.start()
            status = self._wait_for_status(instance, "running")

            context = ssl._create_unverified_context()
            with urllib.request.urlopen(status.url, context=context, timeout=5) as response:
                body = response.read().decode("utf-8")
            self.assertIn("secure index", body)

            request_web_server_stop(instance)
            thread.join(timeout=5)
            self.assertEqual(result.get("exit_code"), 0)

    def test_reverse_proxy_forwards_http_requests(self) -> None:
        upstream_port = self._free_port_or_skip()
        proxy_port = self._free_port_or_skip()
        upstream, upstream_thread = _start_http_upstream(upstream_port)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                instance = Path(tmp) / "instance"
                instance.mkdir()
                config = WebServerConfig(
                    bind_host="127.0.0.1",
                    port=proxy_port,
                    mode="reverse_proxy",
                    upstream_url=f"http://127.0.0.1:{upstream_port}",
                    proxy_path_prefix="/api",
                    proxy_strip_prefix=True,
                )
                write_web_server_config(instance, config)
                result: dict[str, int] = {}
                thread = threading.Thread(target=lambda: result.setdefault("exit_code", run_web_server_helper(instance)), daemon=True)
                thread.start()
                status = self._wait_for_status(instance, "running")

                with urllib.request.urlopen(status.url + "api/hello?x=1", timeout=5) as response:
                    body = response.read().decode("utf-8")
                self.assertIn("upstream:/hello?x=1", body)

                request_web_server_stop(instance)
                thread.join(timeout=5)
                self.assertEqual(result.get("exit_code"), 0)
        finally:
            upstream.shutdown()
            upstream.server_close()
            upstream_thread.join(timeout=5)

    def test_reverse_proxy_forwards_websocket_requests(self) -> None:
        upstream_port = self._free_port_or_skip()
        proxy_port = self._free_port_or_skip()
        upstream_state, upstream_thread = _start_websocket_upstream(upstream_port)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                instance = Path(tmp) / "instance"
                instance.mkdir()
                config = WebServerConfig(
                    bind_host="127.0.0.1",
                    port=proxy_port,
                    mode="reverse_proxy",
                    upstream_url=f"http://127.0.0.1:{upstream_port}",
                    proxy_enable_websocket=True,
                )
                write_web_server_config(instance, config)
                result: dict[str, int] = {}
                thread = threading.Thread(target=lambda: result.setdefault("exit_code", run_web_server_helper(instance)), daemon=True)
                thread.start()
                status = self._wait_for_status(instance, "running")

                async def websocket_roundtrip() -> str:
                    async with ClientSession() as session:
                        async with session.ws_connect(status.url + "ws") as ws:
                            await ws.send_str("ping")
                            message = await ws.receive(timeout=5)
                            return str(message.data)

                self.assertEqual(asyncio.run(websocket_roundtrip()), "echo:ping")

                request_web_server_stop(instance)
                thread.join(timeout=5)
                self.assertEqual(result.get("exit_code"), 0)
        finally:
            _stop_websocket_upstream(upstream_state, upstream_thread)

    def test_reverse_proxy_retries_malformed_conditional_cache_response(self) -> None:
        upstream_port = self._free_port_or_skip()
        proxy_port = self._free_port_or_skip()
        upstream_sock, upstream_thread = _start_malformed_cache_upstream(upstream_port)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                instance = Path(tmp) / "instance"
                instance.mkdir()
                config = WebServerConfig(
                    bind_host="127.0.0.1",
                    port=proxy_port,
                    mode="reverse_proxy",
                    upstream_url=f"http://127.0.0.1:{upstream_port}",
                )
                write_web_server_config(instance, config)
                result: dict[str, int] = {}
                thread = threading.Thread(target=lambda: result.setdefault("exit_code", run_web_server_helper(instance)), daemon=True)
                thread.start()
                status = self._wait_for_status(instance, "running")

                request = urllib.request.Request(status.url + "compiled/receiver.js", headers={"If-None-Match": '"abc"'})
                with urllib.request.urlopen(request, timeout=5) as response:
                    body = response.read().decode("utf-8")
                self.assertEqual(body, "asset ok")

                request_web_server_stop(instance)
                thread.join(timeout=5)
                self.assertEqual(result.get("exit_code"), 0)
        finally:
            upstream_sock.close()
            upstream_thread.join(timeout=5)

    def test_certbot_command_uses_app_managed_dirs_and_domains(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = WebServerConfig(
                mode="static",
                document_root=".",
                tls_mode="certbot",
                certbot_executable="certbot",
                certbot_primary_domain="example.com",
                certbot_additional_domains="www.example.com",
                certbot_email="admin@example.com",
                certbot_challenge_port=80,
            )
            with patch("snakesh.services.web_server_service.data_dir", return_value=Path(tmp)):
                command = _build_certbot_command(config)

            self.assertIn("--config-dir", command)
            self.assertIn(str(Path(tmp) / "web-server-certbot" / "config"), command)
            self.assertIn("--http-01-port", command)
            self.assertIn("80", command)
            self.assertIn("example.com", command)
            self.assertIn("www.example.com", command)

    def test_prune_web_server_log_files_removes_stale_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch("snakesh.services.web_server_service.data_dir", return_value=Path(tmp)):
                logs_root = web_server_logs_root()
                stale = logs_root / "2025" / "01" / "stale.log"
                fresh = logs_root / "2025" / "01" / "fresh.log"
                stale.parent.mkdir(parents=True, exist_ok=True)
                stale.write_text("old", encoding="utf-8")
                fresh.write_text("new", encoding="utf-8")
                stale_epoch = time.time() - (9 * 24 * 60 * 60)
                os.utime(stale, (stale_epoch, stale_epoch))

                removed = prune_web_server_log_files(7)

            self.assertEqual(removed, 1)
            self.assertFalse(stale.exists())
            self.assertTrue(fresh.exists())

    def test_write_web_server_status_sets_readable_file_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            instance = Path(tmp) / "instance"
            instance.mkdir()

            write_web_server_status(
                instance,
                WebServerStatus(
                    state="running",
                    pid=1234,
                    url="https://127.0.0.1:443/",
                    message="Running.",
                    log_path=str(instance / "server.log"),
                ),
            )

            mode = (web_server_instance_paths(instance).status_path.stat().st_mode & 0o777)
            self.assertEqual(mode, 0o644)

    def _wait_for_status(self, instance: Path, state: str) -> object:
        deadline = time.time() + 5
        while time.time() < deadline:
            status = read_web_server_status(instance)
            if status.state == state:
                return status
            time.sleep(0.1)
        raise AssertionError(f"Timed out waiting for status {state}")


if __name__ == "__main__":
    unittest.main()
