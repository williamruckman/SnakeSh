from __future__ import annotations

import unittest

from snakesh.core.models import Protocol, SSHAutomationStep, SSHDynamicTunnel, SSHStaticTunnel, Session


class SessionModelTests(unittest.TestCase):
    def test_session_roundtrip_includes_legacy_compatibility(self) -> None:
        session = Session(
            id="sess-model-legacy",
            name="Legacy Test",
            host="192.0.2.25",
            protocol=Protocol.SSH,
            port=22,
            username="tester",
            ssh_legacy_compatibility=True,
            ssh_keepalive=True,
            private_key_path="~/.ssh/id_ed25519",
            public_key_path="~/.ssh/id_ed25519.pub",
            ssh_automation_enabled=True,
            ssh_automation_steps=[
                SSHAutomationStep(step_type="command", command="terminal length 0"),
                SSHAutomationStep(step_type="sleep", sleep_seconds=2.5),
                SSHAutomationStep(
                    step_type="expect",
                    expect_text="ready",
                    expect_timeout_seconds=8.0,
                    expect_on_timeout="continue",
                ),
            ],
        )

        payload = session.to_dict()
        self.assertTrue(payload["ssh_legacy_compatibility"])
        self.assertTrue(payload["ssh_keepalive"])
        self.assertEqual(payload["public_key_path"], "~/.ssh/id_ed25519.pub")
        self.assertTrue(payload["ssh_automation_enabled"])
        self.assertEqual(len(payload["ssh_automation_steps"]), 3)

        restored = Session.from_dict(payload)
        self.assertTrue(restored.ssh_legacy_compatibility)
        self.assertTrue(restored.ssh_keepalive)
        self.assertEqual(restored.public_key_path, "~/.ssh/id_ed25519.pub")
        self.assertTrue(restored.ssh_automation_enabled)
        self.assertEqual(restored.ssh_automation_steps[0].step_type, "command")
        self.assertEqual(restored.ssh_automation_steps[1].step_type, "sleep")
        self.assertEqual(restored.ssh_automation_steps[2].step_type, "expect")
        self.assertEqual(restored.ssh_automation_steps[2].expect_on_timeout, "continue")

    def test_session_defaults_legacy_compatibility_disabled(self) -> None:
        session = Session(
            id="sess-model-default",
            name="Default Test",
            host="192.0.2.26",
            protocol=Protocol.SSH,
            port=22,
        )
        self.assertFalse(session.ssh_legacy_compatibility)
        self.assertFalse(session.ssh_keepalive)
        self.assertFalse(session.ssh_automation_enabled)
        self.assertEqual(session.ssh_automation_steps, [])

    def test_session_roundtrip_includes_ssh_tunnel_rules(self) -> None:
        session = Session(
            id="sess-model-tunnels",
            name="Tunnel Test",
            host="192.0.2.27",
            protocol=Protocol.SSH,
            port=22,
            ssh_dynamic_tunnels=[
                SSHDynamicTunnel(bind_host="127.0.0.1", bind_port=1080, enabled=True),
            ],
            ssh_static_tunnels=[
                SSHStaticTunnel(
                    direction="local",
                    bind_host="127.0.0.1",
                    bind_port=8443,
                    target_host="198.51.100.10",
                    target_port=443,
                    enabled=True,
                ),
                SSHStaticTunnel(
                    direction="remote",
                    bind_host="0.0.0.0",
                    bind_port=2222,
                    target_host="127.0.0.1",
                    target_port=22,
                    enabled=False,
                ),
            ],
        )

        payload = session.to_dict()
        restored = Session.from_dict(payload)
        self.assertEqual(len(restored.ssh_dynamic_tunnels), 1)
        self.assertEqual(restored.ssh_dynamic_tunnels[0].bind_port, 1080)
        self.assertEqual(len(restored.ssh_static_tunnels), 2)
        self.assertEqual(restored.ssh_static_tunnels[0].direction, "local")
        self.assertEqual(restored.ssh_static_tunnels[1].direction, "remote")
        self.assertFalse(restored.ssh_static_tunnels[1].enabled)

    def test_session_from_dict_drops_empty_command_or_expect_automation_steps(self) -> None:
        payload = {
            "id": "sess-model-automation-filter",
            "name": "Automation Filter Test",
            "host": "192.0.2.29",
            "protocol": "ssh",
            "port": 22,
            "ssh_automation_enabled": True,
            "ssh_automation_steps": [
                {"step_type": "command", "command": "   "},
                {"step_type": "expect", "expect_text": ""},
                {"step_type": "sleep", "sleep_seconds": 1.0},
            ],
        }

        restored = Session.from_dict(payload)

        self.assertTrue(restored.ssh_automation_enabled)
        self.assertEqual(len(restored.ssh_automation_steps), 1)
        self.assertEqual(restored.ssh_automation_steps[0].step_type, "sleep")

    def test_session_from_dict_normalizes_invalid_automation_step_values(self) -> None:
        payload = {
            "id": "sess-model-automation-normalize",
            "name": "Automation Normalize Test",
            "host": "192.0.2.31",
            "protocol": "ssh",
            "port": 22,
            "ssh_automation_enabled": True,
            "ssh_automation_steps": [
                {
                    "step_type": "expect",
                    "expect_text": "Prompt>",
                    "expect_timeout_seconds": -10,
                    "expect_on_timeout": "invalid-mode",
                }
            ],
        }

        restored = Session.from_dict(payload)

        self.assertEqual(len(restored.ssh_automation_steps), 1)
        step = restored.ssh_automation_steps[0]
        self.assertEqual(step.step_type, "expect")
        self.assertEqual(step.expect_timeout_seconds, 15.0)
        self.assertEqual(step.expect_on_timeout, "terminate")

    def test_session_from_dict_sanitizes_invalid_tunnel_ports(self) -> None:
        payload = {
            "id": "sess-model-invalid-tunnels",
            "name": "Invalid Tunnel Test",
            "host": "192.0.2.28",
            "protocol": "ssh",
            "port": 22,
            "ssh_dynamic_tunnels": [
                {"bind_host": "127.0.0.1", "bind_port": "bad", "enabled": True},
            ],
            "ssh_static_tunnels": [
                {
                    "direction": "invalid-direction",
                    "bind_host": "127.0.0.1",
                    "bind_port": 70000,
                    "target_host": "example.internal",
                    "target_port": -50,
                    "enabled": True,
                }
            ],
        }
        restored = Session.from_dict(payload)
        self.assertEqual(restored.ssh_dynamic_tunnels[0].bind_port, 1080)
        self.assertEqual(restored.ssh_static_tunnels[0].direction, "local")
        self.assertEqual(restored.ssh_static_tunnels[0].bind_port, 0)
        self.assertEqual(restored.ssh_static_tunnels[0].target_port, 0)

    def test_session_from_dict_preserves_auto_resolution(self) -> None:
        payload = {
            "id": "sess-model-auto-resolution",
            "name": "Auto Resolution Test",
            "host": "192.0.2.40",
            "protocol": "rdp",
            "port": 3389,
            "display_resolution": "AUTO",
        }

        restored = Session.from_dict(payload)

        self.assertEqual(restored.display_resolution, "auto")

    def test_session_from_dict_normalizes_rdp_audio_mode(self) -> None:
        payload = {
            "id": "sess-model-rdp-audio",
            "name": "Audio Mode Test",
            "host": "192.0.2.41",
            "protocol": "rdp",
            "port": 3389,
            "rdp_audio_mode": "on remote computer",
        }

        restored = Session.from_dict(payload)
        self.assertEqual(restored.rdp_audio_mode, "remote")

        serialized = restored.to_dict()
        self.assertEqual(serialized["rdp_audio_mode"], "remote")

    def test_session_remote_launch_mode_defaults_to_tab(self) -> None:
        session = Session(
            id="sess-model-launch-default",
            name="Launch Mode Default",
            host="192.0.2.42",
            protocol=Protocol.RDP,
            port=3389,
        )
        self.assertEqual(session.remote_launch_mode, "tab")
        self.assertEqual(session.to_dict()["remote_launch_mode"], "tab")

    def test_session_from_dict_normalizes_remote_launch_mode(self) -> None:
        payload = {
            "id": "sess-model-launch-mode",
            "name": "Launch Mode Normalize",
            "host": "192.0.2.43",
            "protocol": "vnc",
            "port": 5901,
            "remote_launch_mode": "Detached Window",
        }

        restored = Session.from_dict(payload)
        self.assertEqual(restored.remote_launch_mode, "detached")

        serialized = restored.to_dict()
        self.assertEqual(serialized["remote_launch_mode"], "detached")

    def test_session_vnc_resize_defaults_disabled(self) -> None:
        session = Session(
            id="sess-model-vnc-resize-default",
            name="VNC Resize Default",
            host="192.0.2.44",
            protocol=Protocol.VNC,
            port=5901,
        )
        self.assertFalse(session.vnc_allow_resize)
        self.assertFalse(session.to_dict()["vnc_allow_resize"])

    def test_session_from_dict_preserves_vnc_resize_setting(self) -> None:
        payload = {
            "id": "sess-model-vnc-resize",
            "name": "VNC Resize",
            "host": "192.0.2.45",
            "protocol": "vnc",
            "port": 5901,
            "vnc_allow_resize": True,
        }

        restored = Session.from_dict(payload)
        self.assertTrue(restored.vnc_allow_resize)
        self.assertTrue(restored.to_dict()["vnc_allow_resize"])

    def test_session_terminal_color_override_roundtrip(self) -> None:
        session = Session(
            id="sess-model-color-override",
            name="Color Override",
            host="192.0.2.51",
            protocol=Protocol.SSH,
            port=22,
            terminal_color_override_enabled=True,
            terminal_bg_color="#102030",
            terminal_fg_color="#f5f7fa",
        )

        restored = Session.from_dict(session.to_dict())

        self.assertTrue(restored.terminal_color_override_enabled)
        self.assertEqual(restored.terminal_bg_color, "#102030")
        self.assertEqual(restored.terminal_fg_color, "#f5f7fa")

    def test_session_nomachine_tuning_defaults(self) -> None:
        session = Session(
            id="sess-model-nomachine-defaults",
            name="NoMachine Defaults",
            host="192.0.2.48",
            protocol=Protocol.NOMACHINE,
            port=4000,
        )
        serialized = session.to_dict()
        self.assertTrue(session.nomachine_audio_enabled)
        self.assertTrue(session.nomachine_mute_remote_audio)
        self.assertFalse(session.nomachine_physical_desktop_auto_resize)
        self.assertEqual(session.nomachine_physical_desktop_resize_mode, "scaled")
        self.assertEqual(session.nomachine_link_quality, 5)
        self.assertEqual(session.nomachine_video_quality, 5)
        self.assertEqual(serialized["nomachine_physical_desktop_resize_mode"], "scaled")
        self.assertEqual(serialized["nomachine_link_quality"], 5)
        self.assertEqual(serialized["nomachine_video_quality"], 5)

    def test_session_from_dict_normalizes_nomachine_tuning(self) -> None:
        payload = {
            "id": "sess-model-nomachine-normalize",
            "name": "NoMachine Normalize",
            "host": "192.0.2.49",
            "protocol": "nomachine",
            "port": 4000,
            "nomachine_audio_enabled": False,
            "nomachine_mute_remote_audio": False,
            "nomachine_physical_desktop_auto_resize": True,
            "nomachine_physical_desktop_resize_mode": "invalid-mode",
            "nomachine_link_quality": 99,
            "nomachine_video_quality": -5,
        }
        restored = Session.from_dict(payload)
        self.assertFalse(restored.nomachine_audio_enabled)
        self.assertFalse(restored.nomachine_mute_remote_audio)
        self.assertTrue(restored.nomachine_physical_desktop_auto_resize)
        self.assertEqual(restored.nomachine_physical_desktop_resize_mode, "scaled")
        self.assertEqual(restored.nomachine_link_quality, 9)
        self.assertEqual(restored.nomachine_video_quality, 0)
        serialized = restored.to_dict()
        self.assertEqual(serialized["nomachine_link_quality"], 9)
        self.assertEqual(serialized["nomachine_video_quality"], 0)

    def test_session_sftp_folder_defaults(self) -> None:
        session = Session(
            id="sess-model-sftp-defaults",
            name="SFTP Defaults",
            host="192.0.2.46",
            protocol=Protocol.SFTP,
            port=22,
        )
        self.assertEqual(session.sftp_local_folder, "~")
        self.assertEqual(session.sftp_remote_folder, ".")
        serialized = session.to_dict()
        self.assertEqual(serialized["sftp_local_folder"], "~")
        self.assertEqual(serialized["sftp_remote_folder"], ".")

    def test_session_from_dict_normalizes_blank_sftp_folders(self) -> None:
        payload = {
            "id": "sess-model-sftp-normalize",
            "name": "SFTP Normalize",
            "host": "192.0.2.47",
            "protocol": "ssh",
            "port": 22,
            "sftp_local_folder": "  ",
            "sftp_remote_folder": "",
        }
        restored = Session.from_dict(payload)
        self.assertEqual(restored.sftp_local_folder, "~")
        self.assertEqual(restored.sftp_remote_folder, ".")

    def test_session_telnet_defaults_and_roundtrip(self) -> None:
        session = Session(
            id="sess-model-telnet-defaults",
            name="Telnet Defaults",
            host="192.0.2.60",
            protocol=Protocol.TELNET,
            port=23,
        )
        self.assertEqual(session.telnet_terminal_type, "xterm-256color")
        self.assertEqual(session.telnet_connect_timeout_seconds, 10.0)
        self.assertFalse(session.telnet_use_tls)
        self.assertTrue(session.telnet_tls_verify)

        restored = Session.from_dict(session.to_dict())
        self.assertEqual(restored.protocol, Protocol.TELNET)
        self.assertEqual(restored.telnet_terminal_type, "xterm-256color")
        self.assertEqual(restored.telnet_connect_timeout_seconds, 10.0)
        self.assertFalse(restored.telnet_use_tls)
        self.assertTrue(restored.telnet_tls_verify)

    def test_session_from_dict_normalizes_telnet_timeout(self) -> None:
        payload = {
            "id": "sess-model-telnet-timeout",
            "name": "Telnet Timeout",
            "host": "192.0.2.61",
            "protocol": "telnet",
            "port": 23,
            "telnet_connect_timeout_seconds": -20,
            "telnet_terminal_type": "  ",
            "telnet_use_tls": True,
            "telnet_tls_verify": False,
        }
        restored = Session.from_dict(payload)
        self.assertEqual(restored.telnet_connect_timeout_seconds, 10.0)
        self.assertEqual(restored.telnet_terminal_type, "xterm-256color")
        self.assertTrue(restored.telnet_use_tls)
        self.assertFalse(restored.telnet_tls_verify)

    def test_session_from_dict_normalizes_serial_settings(self) -> None:
        payload = {
            "id": "sess-model-serial-normalize",
            "name": "Serial Normalize",
            "host": "COM3",
            "protocol": "serial",
            "port": 0,
            "serial_baud_rate": "abc",
            "serial_data_bits": 9,
            "serial_parity": "invalid",
            "serial_stop_bits": "9",
            "serial_flow_control": "invalid",
            "serial_terminal_type": "  ",
        }
        restored = Session.from_dict(payload)
        self.assertEqual(restored.protocol, Protocol.SERIAL)
        self.assertEqual(restored.serial_baud_rate, 9600)
        self.assertEqual(restored.serial_data_bits, 8)
        self.assertEqual(restored.serial_parity, "none")
        self.assertEqual(restored.serial_stop_bits, "1")
        self.assertEqual(restored.serial_flow_control, "none")
        self.assertEqual(restored.serial_terminal_type, "auto")

    def test_session_serial_terminal_type_defaults_and_roundtrip(self) -> None:
        session = Session(
            id="sess-model-serial-terminal",
            name="Serial Terminal",
            host="COM3",
            protocol=Protocol.SERIAL,
            port=0,
            serial_terminal_type="xterm",
        )

        restored = Session.from_dict(session.to_dict())

        self.assertEqual(restored.serial_terminal_type, "xterm")

    def test_session_from_dict_maps_unknown_protocol_to_ssh(self) -> None:
        payload = {
            "id": "sess-model-unknown-protocol",
            "name": "Unknown Protocol",
            "host": "192.0.2.50",
            "protocol": "webapp",
            "port": 443,
        }
        restored = Session.from_dict(payload)
        self.assertEqual(restored.protocol, Protocol.SSH)


if __name__ == "__main__":
    unittest.main()
