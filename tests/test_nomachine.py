from __future__ import annotations

from pathlib import Path
import os
import stat
import tempfile
import unittest
from unittest.mock import patch

from snakesh.core.models import Protocol, Session
from snakesh.protocols.base import ProtocolError
from snakesh.protocols.nomachine import build_nomachine_command, launch_nomachine


def _build_session(*, host: str = "nx.example.com", port: int = 4000, username: str = "alice") -> Session:
    return Session(
        id="sess-nx-test",
        name="NoMachine Test",
        host=host,
        protocol=Protocol.NOMACHINE,
        port=port,
        username=username,
    )


class NoMachineLauncherTests(unittest.TestCase):
    def test_build_nomachine_command_uses_session_file_target(self) -> None:
        session = _build_session(port=4011, username="lab")
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch("snakesh.protocols.nomachine.platform.system", return_value="Linux"),
                patch("snakesh.protocols.nomachine._resolve_executable", return_value="/usr/NX/bin/nxplayer"),
                patch("snakesh.protocols.nomachine.data_dir", return_value=Path(temp_dir)),
            ):
                command, viewer = build_nomachine_command(session)

            self.assertEqual(command[0], "/usr/NX/bin/nxplayer")
            self.assertEqual(command[1], "--session")
            session_file = Path(command[2])
            self.assertEqual(session_file.suffix, ".nxs")
            self.assertTrue(session_file.exists())
            self.assertEqual(session_file.parent, Path(temp_dir) / "nomachine")
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(session_file.parent.stat().st_mode), 0o700)
                self.assertEqual(stat.S_IMODE(session_file.stat().st_mode), 0o600)
            payload = session_file.read_text(encoding="utf-8")
            self.assertIn('<option key="Server host" value="nx.example.com" />', payload)
            self.assertIn('<option key="NoMachine daemon port" value="4011" />', payload)
            self.assertIn('<option key="User" value="lab" />', payload)
            self.assertIn('<option key="Remember password" value="true" />', payload)
            self.assertIn('<option key="Show remote audio alert message" value="false" />', payload)
            self.assertIn('<option key="Physical desktop auto-resize" value="false" />', payload)
            self.assertIn('<option key="Physical desktop resize mode" value="scaled" />', payload)
            self.assertIn('<option key="Link quality" value="5" />', payload)
            self.assertIn('<option key="Video encoding quality" value="5" />', payload)
            self.assertIn('<option key="Audio" value="true" />', payload)
            self.assertIn('<option key="Mute audio of the remote physical desktop" value="true" />', payload)
            self.assertEqual(payload.count('key="Server host"'), 1)
            self.assertEqual(payload.count('key="Connection service"'), 1)
            self.assertEqual(payload.count('key="NoMachine daemon port"'), 1)
            self.assertEqual(viewer, "NoMachine Player")

    def test_build_nomachine_command_normalizes_default_port_and_escapes_values(self) -> None:
        session = _build_session(host="2001:db8::15", port=0, username="")
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch("snakesh.protocols.nomachine.platform.system", return_value="Linux"),
                patch("snakesh.protocols.nomachine._resolve_executable", return_value="/usr/NX/bin/nxplayer"),
                patch("snakesh.protocols.nomachine.data_dir", return_value=Path(temp_dir)),
            ):
                command, _viewer = build_nomachine_command(session)

            self.assertEqual(command[0], "/usr/NX/bin/nxplayer")
            self.assertEqual(command[1], "--session")
            payload = Path(command[2]).read_text(encoding="utf-8")
            self.assertIn('<option key="Server host" value="2001:db8::15" />', payload)
            self.assertIn('<option key="NoMachine daemon port" value="4000" />', payload)
            self.assertIn('<option key="Remember username" value="false" />', payload)

    def test_build_nomachine_command_preserves_existing_auth_tokens(self) -> None:
        session = _build_session(host="192.0.2.168", username="operator")
        with tempfile.TemporaryDirectory() as temp_dir:
            session_file = Path(temp_dir) / "nomachine" / "sess-nx-test.nxs"
            session_file.parent.mkdir(parents=True, exist_ok=True)
            session_file.write_text(
                "\n".join(
                    [
                        "<!DOCTYPE NXClientSettings>",
                        '<NXClientSettings version="2.3" application="nxclient" >',
                        '  <group name="General" >',
                        '    <option key="Remember password" value="true" />',
                        "  </group>",
                        '  <group name="Login" >',
                        '    <option key="Auth" value="persisted-token" />',
                        '    <option key="System auth" value="persisted-system-token" />',
                        "  </group>",
                        "</NXClientSettings>",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            with (
                patch("snakesh.protocols.nomachine.platform.system", return_value="Linux"),
                patch("snakesh.protocols.nomachine._resolve_executable", return_value="/usr/NX/bin/nxplayer"),
                patch("snakesh.protocols.nomachine.data_dir", return_value=Path(temp_dir)),
            ):
                command, _viewer = build_nomachine_command(session)

            payload = Path(command[2]).read_text(encoding="utf-8")
            self.assertIn('<option key="Auth" value="persisted-token" />', payload)
            self.assertIn('<option key="System auth" value="persisted-system-token" />', payload)
            self.assertIn('<option key="Remember password" value="true" />', payload)

    def test_build_nomachine_command_preserves_other_groups_and_removes_ambiguous_keys(self) -> None:
        session = _build_session(host="192.0.2.168", username="operator")
        with tempfile.TemporaryDirectory() as temp_dir:
            session_file = Path(temp_dir) / "nomachine" / "sess-nx-test.nxs"
            session_file.parent.mkdir(parents=True, exist_ok=True)
            session_file.write_text(
                "\n".join(
                    [
                        "<!DOCTYPE NXClientSettings>",
                        '<NXClientSettings version="2.3" application="nxclient" >',
                        '  <group name="Advanced" >',
                        '    <option key="Connection service" value="wrong" />',
                        '    <option key="Server host" value="wrong-host" />',
                        "  </group>",
                        '  <group name="Services" >',
                        '    <option key="Audio" value="true" />',
                        '    <option key="Mute audio of the remote physical desktop" value="true" />',
                        "  </group>",
                        "</NXClientSettings>",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            with (
                patch("snakesh.protocols.nomachine.platform.system", return_value="Linux"),
                patch("snakesh.protocols.nomachine._resolve_executable", return_value="/usr/NX/bin/nxplayer"),
                patch("snakesh.protocols.nomachine.data_dir", return_value=Path(temp_dir)),
            ):
                command, _viewer = build_nomachine_command(session)

            payload = Path(command[2]).read_text(encoding="utf-8")
            self.assertIn('<group name="Services" >', payload)
            self.assertIn('<option key="Audio" value="true" />', payload)
            self.assertIn('<option key="Mute audio of the remote physical desktop" value="true" />', payload)
            self.assertNotIn('<option key="Connection service" value="wrong" />', payload)
            self.assertNotIn('<option key="Server host" value="wrong-host" />', payload)

    def test_build_nomachine_command_applies_nomachine_session_tuning(self) -> None:
        session = _build_session(host="192.0.2.168", username="operator")
        session.nomachine_audio_enabled = False
        session.nomachine_mute_remote_audio = False
        session.nomachine_physical_desktop_auto_resize = True
        session.nomachine_physical_desktop_resize_mode = "viewport"
        session.nomachine_link_quality = 9
        session.nomachine_video_quality = 2

        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch("snakesh.protocols.nomachine.platform.system", return_value="Linux"),
                patch("snakesh.protocols.nomachine._resolve_executable", return_value="/usr/NX/bin/nxplayer"),
                patch("snakesh.protocols.nomachine.data_dir", return_value=Path(temp_dir)),
            ):
                command, _viewer = build_nomachine_command(session)

            payload = Path(command[2]).read_text(encoding="utf-8")
            self.assertIn('<option key="Physical desktop auto-resize" value="true" />', payload)
            self.assertIn('<option key="Physical desktop resize mode" value="viewport" />', payload)
            self.assertIn('<option key="Link quality" value="9" />', payload)
            self.assertIn('<option key="Video encoding quality" value="2" />', payload)
            self.assertIn('<option key="Audio" value="false" />', payload)
            self.assertIn('<option key="Mute audio of the remote physical desktop" value="false" />', payload)

    def test_build_nomachine_command_raises_when_client_missing(self) -> None:
        session = _build_session()
        with (
            patch("snakesh.protocols.nomachine.platform.system", return_value="Linux"),
            patch("snakesh.protocols.nomachine._resolve_executable", return_value=None),
        ):
            with self.assertRaises(ProtocolError):
                build_nomachine_command(session)

    def test_launch_nomachine_starts_subprocess(self) -> None:
        session = _build_session()
        with patch("snakesh.protocols.nomachine.subprocess.Popen") as mock_popen:
            with patch(
                "snakesh.protocols.nomachine.build_nomachine_command",
                return_value=(["/usr/NX/bin/nxplayer", "--session", "/tmp/snakesh/nomachine/session.nxs"], "NoMachine Player"),
            ):
                viewer = launch_nomachine(session)

        self.assertEqual(viewer, "NoMachine Player")
        mock_popen.assert_called_once_with(["/usr/NX/bin/nxplayer", "--session", "/tmp/snakesh/nomachine/session.nxs"])


if __name__ == "__main__":
    unittest.main()
