from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from snakesh.core.models import Protocol, Session
from snakesh.protocols.base import ProtocolError
from snakesh.protocols.rdp import build_rdp_command, build_rdp_stdin_payload, clear_linux_rdp_known_host, launch_rdp


def _build_session() -> Session:
    return Session(
        id="sess-rdp-test",
        name="RDP Test",
        host="rdp.example.com",
        protocol=Protocol.RDP,
        port=3389,
        username="tester",
        domain="EXAMPLE",
    )


class RDPLauncherTests(unittest.TestCase):
    def test_clear_linux_rdp_known_host_removes_matching_host_and_port(self) -> None:
        session = _build_session()
        with tempfile.TemporaryDirectory() as temp_dir:
            known_hosts_path = Path(temp_dir) / "known_hosts2"
            known_hosts_path.write_text(
                "\n".join(
                    [
                        "rdp.example.com 3389 aa:aa subject issuer",
                        "rdp.example.com 3390 bb:bb subject issuer",
                        "other.example.com 3389 cc:cc subject issuer",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            changed = clear_linux_rdp_known_host(session, known_hosts_path=known_hosts_path)

            remaining = known_hosts_path.read_text(encoding="utf-8")
            self.assertTrue(changed)
            self.assertNotIn("rdp.example.com 3389 aa:aa", remaining)
            self.assertIn("rdp.example.com 3390 bb:bb", remaining)
            self.assertIn("other.example.com 3389 cc:cc", remaining)

    def test_clear_linux_rdp_known_host_handles_bracketed_addresses(self) -> None:
        session = _build_session()
        session.host = "[rdp.example.com]"
        with tempfile.TemporaryDirectory() as temp_dir:
            known_hosts_path = Path(temp_dir) / "known_hosts2"
            known_hosts_path.write_text(
                "[rdp.example.com] 3389 aa:aa subject issuer\n",
                encoding="utf-8",
            )

            changed = clear_linux_rdp_known_host(session, known_hosts_path=known_hosts_path)

            self.assertTrue(changed)
            self.assertEqual(known_hosts_path.read_text(encoding="utf-8"), "")

    def test_clear_linux_rdp_known_host_returns_false_when_no_match(self) -> None:
        session = _build_session()
        with tempfile.TemporaryDirectory() as temp_dir:
            known_hosts_path = Path(temp_dir) / "known_hosts2"
            known_hosts_path.write_text(
                "other.example.com 3389 cc:cc subject issuer\n",
                encoding="utf-8",
            )

            changed = clear_linux_rdp_known_host(session, known_hosts_path=known_hosts_path)

            self.assertFalse(changed)

    def test_linux_build_command_supports_embedded_parent_window(self) -> None:
        session = _build_session()
        with patch("snakesh.protocols.rdp.platform.system", return_value="Linux"):
            cmd = build_rdp_command(
                session,
                password="secret",
                linux_trust_certificate=True,
                linux_parent_window_id=98765,
            )

        self.assertIn("/cert:tofu", cmd)
        self.assertIn("/parent-window:98765", cmd)

    def test_linux_launch_adds_tofu_cert_mode_when_trust_is_confirmed(self) -> None:
        session = _build_session()
        mock_process = Mock()
        mock_process.stdin = Mock()
        with (
            patch("snakesh.protocols.rdp.platform.system", return_value="Linux"),
            patch("snakesh.protocols.rdp.subprocess.Popen", return_value=mock_process) as mock_popen,
        ):
            launch_rdp(session, password="secret", linux_trust_certificate=True)

        cmd = mock_popen.call_args.args[0]
        self.assertIn("/cert:tofu", cmd)
        self.assertIn("/d:EXAMPLE", cmd)
        self.assertIn("/u:tester", cmd)
        self.assertIn("/from-stdin:force", cmd)
        self.assertNotIn("/p:secret", cmd)
        self.assertIn("/v:rdp.example.com", cmd)
        self.assertEqual(mock_popen.call_args.kwargs["stdin"], subprocess.PIPE)
        self.assertTrue(mock_popen.call_args.kwargs["text"])
        mock_process.stdin.write.assert_called_once_with("secret\n")

    def test_linux_launch_omits_tofu_cert_mode_without_trust_confirmation(self) -> None:
        session = _build_session()
        mock_process = Mock()
        mock_process.stdin = Mock()
        with (
            patch("snakesh.protocols.rdp.platform.system", return_value="Linux"),
            patch("snakesh.protocols.rdp.subprocess.Popen", return_value=mock_process) as mock_popen,
        ):
            launch_rdp(session, password="secret", linux_trust_certificate=False)

        cmd = mock_popen.call_args.args[0]
        self.assertNotIn("/cert:tofu", cmd)
        self.assertIn("/from-stdin:force", cmd)

    def test_linux_auto_resolution_enables_dynamic_resize(self) -> None:
        session = _build_session()
        session.display_resolution = "auto"
        with patch("snakesh.protocols.rdp.platform.system", return_value="Linux"):
            cmd = build_rdp_command(session, password="secret", linux_trust_certificate=True)

        self.assertIn("/dynamic-resolution", cmd)
        self.assertIn("/sound", cmd)
        self.assertIn("/audio-mode:0", cmd)

    def test_build_rdp_stdin_payload_returns_password_only_for_linux(self) -> None:
        session = _build_session()
        with patch("snakesh.protocols.rdp.platform.system", return_value="Linux"):
            self.assertEqual(build_rdp_stdin_payload(session, password="secret"), "secret\n")
        with patch("snakesh.protocols.rdp.platform.system", return_value="Windows"):
            self.assertIsNone(build_rdp_stdin_payload(session, password="secret"))

    def test_linux_embedded_auto_resolution_ignores_fullscreen_flag(self) -> None:
        session = _build_session()
        session.display_resolution = "auto"
        session.display_fullscreen = True
        with patch("snakesh.protocols.rdp.platform.system", return_value="Linux"):
            cmd = build_rdp_command(
                session,
                password="secret",
                linux_trust_certificate=True,
                linux_parent_window_id=7654,
            )

        self.assertIn("/dynamic-resolution", cmd)
        self.assertNotIn("/f", cmd)

    def test_linux_rdp_audio_mode_remote(self) -> None:
        session = _build_session()
        session.rdp_audio_mode = "remote"
        with patch("snakesh.protocols.rdp.platform.system", return_value="Linux"):
            cmd = build_rdp_command(session)

        self.assertIn("/audio-mode:1", cmd)
        self.assertNotIn("/sound", cmd)

    def test_windows_auto_resolution_and_audio_uses_rdp_file(self) -> None:
        session = _build_session()
        session.display_resolution = "auto"
        session.rdp_audio_mode = "mute"
        expected_file = Path("C:/Temp/snakesh-test.rdp")

        with (
            patch("snakesh.protocols.rdp.platform.system", return_value="Windows"),
            patch("snakesh.protocols.rdp._windows_mstsc_executable", return_value="mstsc"),
            patch("snakesh.protocols.rdp._build_windows_rdp_file", return_value=expected_file) as mock_builder,
            patch("snakesh.protocols.rdp._seed_windows_rdp_credentials"),
        ):
            cmd = build_rdp_command(session, password="secret")

        self.assertEqual(cmd, ["mstsc", str(expected_file)])
        self.assertTrue(mock_builder.called)
        call_kwargs = mock_builder.call_args.kwargs
        self.assertTrue(call_kwargs["auto_resolution"])
        self.assertEqual(call_kwargs["audio_mode"], "mute")

    def test_windows_command_uses_resolved_mstsc_executable(self) -> None:
        session = _build_session()
        with (
            patch("snakesh.protocols.rdp.platform.system", return_value="Windows"),
            patch("snakesh.protocols.rdp._windows_mstsc_executable", return_value=r"C:\Windows\System32\mstsc.exe"),
        ):
            cmd = build_rdp_command(session)
        self.assertEqual(cmd[0], r"C:\Windows\System32\mstsc.exe")
        self.assertTrue(cmd[1].endswith(".rdp"))

    def test_windows_cmdkey_timeout_does_not_abort_launch(self) -> None:
        session = _build_session()
        expected_file = Path("C:/Temp/snakesh-timeout.rdp")
        timeout_error = subprocess.TimeoutExpired(cmd=["cmdkey"], timeout=3)

        with (
            patch("snakesh.protocols.rdp.platform.system", return_value="Windows"),
            patch("snakesh.protocols.rdp._windows_mstsc_executable", return_value="mstsc"),
            patch("snakesh.protocols.rdp._build_windows_rdp_file", return_value=expected_file),
            patch("snakesh.protocols.rdp.subprocess.run", side_effect=timeout_error),
        ):
            cmd = build_rdp_command(session, password="secret")

        self.assertEqual(cmd, ["mstsc", str(expected_file)])

    def test_unsupported_platform_raises_protocol_error(self) -> None:
        session = _build_session()
        with patch("snakesh.protocols.rdp.platform.system", return_value="Darwin"):
            with self.assertRaises(ProtocolError):
                launch_rdp(session)


if __name__ == "__main__":
    unittest.main()
