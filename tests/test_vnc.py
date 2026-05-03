from __future__ import annotations

import unittest
from unittest.mock import patch

from snakesh.core.models import Protocol, Session
from snakesh.protocols.base import ProtocolError
from snakesh.protocols import vnc


def _build_session(*, username: str = "tester") -> Session:
    return Session(
        id="sess-vnc-test",
        name="VNC Test",
        host="vnc.example.com",
        protocol=Protocol.VNC,
        port=5901,
        username=username,
    )


class VNCLauncherTests(unittest.TestCase):
    def test_build_vnc_command_rejects_linux_embed_for_tigervnc(self) -> None:
        session = _build_session()
        provider = vnc._VNCProvider(
            name="TigerVNC Viewer",
            executable_candidates=("xtigervncviewer",),
            password_mode=vnc._PASSWORD_MODE_TIGERVNC_ENV,
        )
        with (
            patch("snakesh.protocols.vnc.platform.system", return_value="Linux"),
            patch("snakesh.protocols.vnc._resolve_provider", return_value=(provider, "/usr/bin/xtigervncviewer")),
        ):
            with self.assertRaises(ProtocolError):
                vnc.build_vnc_command(session, linux_parent_window_id=4242)

    def test_build_vnc_command_rejects_embed_when_provider_does_not_support_it(self) -> None:
        session = _build_session()
        provider = vnc._VNCProvider(
            name="Remmina",
            executable_candidates=("remmina",),
        )
        with (
            patch("snakesh.protocols.vnc.platform.system", return_value="Linux"),
            patch("snakesh.protocols.vnc._resolve_provider", return_value=(provider, "/usr/bin/remmina")),
        ):
            with self.assertRaises(ProtocolError):
                vnc.build_vnc_command(session, linux_parent_window_id=4242)

    def test_build_vnc_launch_includes_password_env_for_tigervnc(self) -> None:
        session = _build_session(username="alice")
        provider = vnc._VNCProvider(
            name="TigerVNC Viewer",
            executable_candidates=("xtigervncviewer",),
            password_mode=vnc._PASSWORD_MODE_TIGERVNC_ENV,
        )
        with (
            patch("snakesh.protocols.vnc.platform.system", return_value="Linux"),
            patch("snakesh.protocols.vnc._resolve_provider", return_value=(provider, "/usr/bin/xtigervncviewer")),
        ):
            command, provider_name, launch_env = vnc.build_vnc_launch(session, password="secret")

        self.assertEqual(provider_name, "TigerVNC Viewer")
        self.assertEqual(command[0], "/usr/bin/xtigervncviewer")
        self.assertIsNotNone(launch_env)
        self.assertEqual(launch_env["VNC_PASSWORD"], "secret")
        self.assertEqual(launch_env["VNC_USERNAME"], "alice")

    def test_launch_vnc_passes_password_environment_to_subprocess(self) -> None:
        session = _build_session()
        provider = vnc._VNCProvider(
            name="TigerVNC Viewer",
            executable_candidates=("xtigervncviewer",),
            password_mode=vnc._PASSWORD_MODE_TIGERVNC_ENV,
        )
        with (
            patch("snakesh.protocols.vnc.platform.system", return_value="Linux"),
            patch("snakesh.protocols.vnc._resolve_provider", return_value=(provider, "/usr/bin/xtigervncviewer")),
            patch("snakesh.protocols.vnc.subprocess.Popen") as mock_popen,
        ):
            vnc.launch_vnc(session, password="secret")

        _, kwargs = mock_popen.call_args
        self.assertIn("env", kwargs)
        self.assertIsNotNone(kwargs["env"])
        self.assertEqual(kwargs["env"]["VNC_PASSWORD"], "secret")

    def test_build_vnc_command_disables_remote_resize_for_tigervnc_by_default(self) -> None:
        session = _build_session()
        provider = vnc._VNCProvider(
            name="TigerVNC Viewer",
            executable_candidates=("xtigervncviewer",),
            auto_resize_flag=("-RemoteResize=1",),
            color_mode=vnc._COLOR_MODE_TIGERVNC,
        )
        with (
            patch("snakesh.protocols.vnc.platform.system", return_value="Linux"),
            patch("snakesh.protocols.vnc._resolve_provider", return_value=(provider, "/usr/bin/xtigervncviewer")),
        ):
            command, _provider_name = vnc.build_vnc_command(session)

        self.assertIn("-RemoteResize=0", command)
        self.assertNotIn("-RemoteResize=1", command)

    def test_build_vnc_command_enables_remote_resize_when_requested(self) -> None:
        session = _build_session()
        session.vnc_allow_resize = True
        provider = vnc._VNCProvider(
            name="TigerVNC Viewer",
            executable_candidates=("xtigervncviewer",),
            auto_resize_flag=("-RemoteResize=1",),
            color_mode=vnc._COLOR_MODE_TIGERVNC,
        )
        with (
            patch("snakesh.protocols.vnc.platform.system", return_value="Linux"),
            patch("snakesh.protocols.vnc._resolve_provider", return_value=(provider, "/usr/bin/xtigervncviewer")),
        ):
            command, _provider_name = vnc.build_vnc_command(session)

        self.assertIn("-RemoteResize=1", command)
        self.assertNotIn("-RemoteResize=0", command)


if __name__ == "__main__":
    unittest.main()
