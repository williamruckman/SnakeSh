from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import patch

# Allow tests to run in lightweight environments where asyncssh isn't installed.
if "asyncssh" not in sys.modules:
    try:
        __import__("asyncssh")
    except ModuleNotFoundError:
        asyncssh_stub = types.ModuleType("asyncssh")
        asyncssh_stub.PermissionDenied = type("PermissionDenied", (Exception,), {})
        asyncssh_stub.connect = None  # Patched in tests.
        sys.modules["asyncssh"] = asyncssh_stub

from snakesh.core.models import Protocol, Session
from snakesh.protocols.ssh_client import SSHClient


class _DummyAsyncSSHConnection:
    def __init__(self) -> None:
        self.run_called = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN001
        return False

    async def run(self, command: str, check: bool = True):  # noqa: ARG002
        self.run_called = True
        return type("Result", (), {"stdout": "connected", "stderr": ""})()

    def get_server_host_key(self):  # noqa: ANN201
        return "dummy-host-key"


def _build_session() -> Session:
    return Session(
        id="sess-ssh-client",
        name="SSH Client Test",
        host="192.0.2.10",
        protocol=Protocol.SSH,
        port=22,
        username="tester",
    )


class SSHClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_verify_connectivity_does_not_run_remote_command(self) -> None:
        client = SSHClient()
        session = _build_session()
        conn = _DummyAsyncSSHConnection()

        with patch("snakesh.protocols.ssh_client.asyncssh.connect", return_value=conn) as mock_connect:
            result = await client.verify_connectivity(session, password="secret")

        self.assertEqual(result, "connected")
        self.assertFalse(conn.run_called)
        kwargs = mock_connect.call_args.kwargs
        self.assertEqual(kwargs.get("connect_timeout"), 12)
        self.assertEqual(kwargs.get("login_timeout"), 12)
        self.assertNotIn("keepalive_interval", kwargs)
        self.assertNotIn("keepalive_count_max", kwargs)

    async def test_verify_connectivity_enables_keepalive_when_requested(self) -> None:
        client = SSHClient()
        session = _build_session()
        session.ssh_keepalive = True
        conn = _DummyAsyncSSHConnection()

        with patch("snakesh.protocols.ssh_client.asyncssh.connect", return_value=conn) as mock_connect:
            result = await client.verify_connectivity(session, password="secret")

        self.assertEqual(result, "connected")
        kwargs = mock_connect.call_args.kwargs
        self.assertEqual(kwargs.get("keepalive_interval"), SSHClient.KEEPALIVE_INTERVAL_SECONDS)
        self.assertEqual(kwargs.get("keepalive_count_max"), SSHClient.KEEPALIVE_COUNT_MAX)

    async def test_trust_and_verify_trusts_host_key_without_exec(self) -> None:
        client = SSHClient()
        session = _build_session()
        conn = _DummyAsyncSSHConnection()

        with (
            patch("snakesh.protocols.ssh_client.asyncssh.connect", return_value=conn) as mock_connect,
            patch("snakesh.protocols.ssh_client.trust_host_key") as mock_trust_host_key,
        ):
            result = await client.trust_and_verify(session, password="secret")

        self.assertEqual(result, "connected")
        self.assertFalse(conn.run_called)
        mock_trust_host_key.assert_called_once_with(session, "dummy-host-key")
        kwargs = mock_connect.call_args.kwargs
        self.assertIsNone(kwargs.get("known_hosts"))
        self.assertEqual(kwargs.get("login_timeout"), 12)

    async def test_verify_connectivity_retries_with_legacy_algorithms(self) -> None:
        client = SSHClient()
        session = _build_session()
        conn = _DummyAsyncSSHConnection()

        with patch(
            "snakesh.protocols.ssh_client.asyncssh.connect",
            side_effect=[RuntimeError("No matching key exchange"), conn],
        ) as mock_connect:
            result = await client.verify_connectivity(session, password="secret")

        self.assertEqual(result, "connected (legacy algorithm compatibility mode)")
        self.assertEqual(mock_connect.call_count, 2)
        retry_kwargs = mock_connect.call_args_list[-1].kwargs
        self.assertIn("kex_algs", retry_kwargs)
        self.assertIn("server_host_key_algs", retry_kwargs)

    async def test_verify_connectivity_prefers_legacy_when_session_requests_it(self) -> None:
        client = SSHClient()
        session = _build_session()
        session.ssh_legacy_compatibility = True
        conn = _DummyAsyncSSHConnection()

        with patch("snakesh.protocols.ssh_client.asyncssh.connect", return_value=conn) as mock_connect:
            result = await client.verify_connectivity(session, password="secret")

        self.assertEqual(result, "connected (legacy algorithm compatibility mode)")
        self.assertEqual(mock_connect.call_count, 1)
        kwargs = mock_connect.call_args.kwargs
        self.assertIn("kex_algs", kwargs)
        self.assertIn("server_host_key_algs", kwargs)

    def test_legacy_negotiation_detection(self) -> None:
        self.assertTrue(SSHClient.is_legacy_negotiation_error("No matching host key"))
        self.assertFalse(SSHClient.is_legacy_negotiation_error("Connection refused"))


if __name__ == "__main__":
    unittest.main()
