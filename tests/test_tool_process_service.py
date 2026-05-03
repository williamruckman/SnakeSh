from __future__ import annotations

import unittest
from unittest.mock import patch

from snakesh.core.tool_registry import TOOL_REGISTRY
from snakesh.services import tool_process_service as service
from snakesh.services.settings_service import AppSettings


class ToolProcessServiceTests(unittest.TestCase):
    def test_supported_tool_keys_matches_registry_order(self) -> None:
        self.assertEqual(
            service.supported_tool_keys(),
            [entry.key for entry in TOOL_REGISTRY],
        )

    def test_launch_standalone_tool_sanitizes_environment_before_detach(self) -> None:
        sanitized_env = {"PATH": "/usr/bin", "PYINSTALLER_RESET_ENVIRONMENT": "1"}
        raw_env = {"APPDIR": "/tmp/.mount_SnakeSh12345", "PATH": "/usr/bin"}

        with (
            patch("snakesh.services.tool_process_service.activate_existing_tool_instance", return_value=False),
            patch(
                "snakesh.services.tool_process_service.runtime.sanitized_self_launch_environment",
                return_value=sanitized_env,
            ) as mock_sanitize,
            patch("snakesh.services.tool_process_service.detached_popen", return_value=object()) as mock_popen,
        ):
            result = service.launch_standalone_tool("ping", arguments=["--ipv6"], env=raw_env)

        self.assertIs(result.process, mock_popen.return_value)
        self.assertFalse(result.activated_existing)
        self.assertTrue(result.spawned_new)
        mock_sanitize.assert_called_once_with(raw_env)
        mock_popen.assert_called_once_with(
            service.standalone_tool_command("ping", arguments=["--ipv6"]),
            cwd=None,
            env=sanitized_env,
        )

    def test_launch_standalone_tool_activates_existing_instance_without_spawning(self) -> None:
        with (
            patch("snakesh.services.tool_process_service.activate_existing_tool_instance", return_value=True),
            patch("snakesh.services.tool_process_service.detached_popen") as mock_popen,
        ):
            result = service.launch_standalone_tool("help")

        self.assertTrue(result.activated_existing)
        self.assertFalse(result.spawned_new)
        self.assertIsNone(result.process)
        mock_popen.assert_not_called()

    def test_activate_existing_tool_instance_passes_activation_payload(self) -> None:
        with patch("snakesh.services.tool_process_service.activate_tool_instance", return_value=True) as mock_activate:
            activated = service.activate_existing_tool_instance("ping", arguments=["--packet-size", "1452", "--ipv6"])

        self.assertTrue(activated)
        mock_activate.assert_called_once_with(
            "ping",
            payload={"arguments": ["--packet-size", "1452", "--ipv6"]},
        )

    def test_broadcast_tool_settings_sync_wraps_preview_payload(self) -> None:
        settings = AppSettings.defaults()
        settings.accent_color = "#123456"

        with patch("snakesh.services.tool_process_service.activate_active_tool_instances", return_value={"help": True}) as mock_activate:
            result = service.broadcast_tool_settings_sync(settings, preview=True)

        self.assertEqual(result, {"help": True})
        payload_factory = mock_activate.call_args.kwargs["payload_factory"]
        payload = payload_factory("help")
        self.assertEqual(
            payload,
            {
                "kind": "settings_sync",
                "preview": True,
                "settings": settings.to_dict(),
            },
        )

    def test_queue_tool_settings_sync_delegates_to_async_dispatcher(self) -> None:
        settings = AppSettings.defaults()
        settings.accent_color = "#123456"

        with patch.object(service._TOOL_SETTINGS_SYNC_DISPATCHER, "queue") as mock_queue:
            service.queue_tool_settings_sync(settings, preview=True)

        mock_queue.assert_called_once_with(settings, preview=True)


if __name__ == "__main__":
    unittest.main()
