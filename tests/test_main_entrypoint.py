from __future__ import annotations

import unittest
from unittest.mock import patch

from snakesh import __main__ as main_entry
from snakesh.core.tool_registry import TOOL_REGISTRY


class MainEntrypointTests(unittest.TestCase):
    def test_non_frozen_runtime_checks_dependencies(self) -> None:
        with (
            patch("snakesh.__main__.activate_existing_main_instance", return_value=False) as mock_activate_main,
            patch("snakesh.__main__.is_frozen", return_value=False),
            patch("snakesh.__main__.ensure_runtime_dependencies", return_value=False) as mock_bootstrap,
            patch("snakesh.__main__._run_gui") as mock_run_gui,
        ):
            exit_code = main_entry.cli_main([])

        self.assertEqual(exit_code, 1)
        mock_activate_main.assert_called_once_with(None)
        mock_bootstrap.assert_called_once()
        mock_run_gui.assert_not_called()

    def test_frozen_runtime_skips_dependency_bootstrap(self) -> None:
        with (
            patch("snakesh.__main__.activate_existing_main_instance", return_value=False) as mock_activate_main,
            patch("snakesh.__main__.is_frozen", return_value=True),
            patch("snakesh.__main__.ensure_runtime_dependencies") as mock_bootstrap,
            patch("snakesh.__main__._run_gui", return_value=7) as mock_run_gui,
        ):
            exit_code = main_entry.cli_main([])

        self.assertEqual(exit_code, 7)
        mock_activate_main.assert_called_once_with(None)
        mock_bootstrap.assert_not_called()
        mock_run_gui.assert_called_once_with(None)

    def test_cli_passes_import_file_to_gui(self) -> None:
        with (
            patch("snakesh.__main__.activate_existing_main_instance", return_value=False) as mock_activate_main,
            patch("snakesh.__main__.is_frozen", return_value=True),
            patch("snakesh.__main__.ensure_runtime_dependencies") as mock_bootstrap,
            patch("snakesh.__main__._run_gui", return_value=0) as mock_run_gui,
        ):
            exit_code = main_entry.cli_main(["/tmp/test-export.ssx"])

        self.assertEqual(exit_code, 0)
        mock_activate_main.assert_called_once_with("/tmp/test-export.ssx")
        mock_bootstrap.assert_not_called()
        mock_run_gui.assert_called_once_with("/tmp/test-export.ssx")

    def test_cli_activates_existing_main_instance_without_running_gui(self) -> None:
        with (
            patch("snakesh.__main__.activate_existing_main_instance", return_value=True) as mock_activate_main,
            patch("snakesh.__main__.is_frozen") as mock_is_frozen,
            patch("snakesh.__main__.ensure_runtime_dependencies") as mock_bootstrap,
            patch("snakesh.__main__._run_gui") as mock_run_gui,
        ):
            exit_code = main_entry.cli_main([])

        self.assertEqual(exit_code, 0)
        mock_activate_main.assert_called_once_with(None)
        mock_is_frozen.assert_not_called()
        mock_bootstrap.assert_not_called()
        mock_run_gui.assert_not_called()

    def test_cli_forwards_import_file_when_activating_existing_main_instance(self) -> None:
        with (
            patch("snakesh.__main__.activate_existing_main_instance", return_value=True) as mock_activate_main,
            patch("snakesh.__main__.ensure_runtime_dependencies") as mock_bootstrap,
            patch("snakesh.__main__._run_gui") as mock_run_gui,
        ):
            exit_code = main_entry.cli_main(["/tmp/test-export.ssx"])

        self.assertEqual(exit_code, 0)
        mock_activate_main.assert_called_once_with("/tmp/test-export.ssx")
        mock_bootstrap.assert_not_called()
        mock_run_gui.assert_not_called()

    def test_cli_debug_flags_still_activate_existing_main_instance_first(self) -> None:
        with (
            patch("snakesh.__main__.activate_existing_main_instance", return_value=True) as mock_activate_main,
            patch("snakesh.__main__.is_frozen") as mock_is_frozen,
            patch("snakesh.__main__.ensure_runtime_dependencies") as mock_bootstrap,
            patch("snakesh.__main__._run_gui") as mock_run_gui,
        ):
            exit_code = main_entry.cli_main(["--debug-level", "debug"])

        self.assertEqual(exit_code, 0)
        mock_activate_main.assert_called_once_with(None)
        mock_is_frozen.assert_not_called()
        mock_bootstrap.assert_not_called()
        mock_run_gui.assert_not_called()

    def test_cli_passes_debug_flags_to_gui(self) -> None:
        with (
            patch("snakesh.__main__.activate_existing_main_instance", return_value=False) as mock_activate_main,
            patch("snakesh.__main__.is_frozen", return_value=True),
            patch("snakesh.__main__.ensure_runtime_dependencies") as mock_bootstrap,
            patch("snakesh.__main__._run_gui", return_value=0) as mock_run_gui,
        ):
            exit_code = main_entry.cli_main(
                [
                    "--debug-level",
                    "debug",
                    "--debug-log-file",
                    "/tmp/snakesh-debug.log",
                    "/tmp/test-export.ssx",
                ]
            )

        self.assertEqual(exit_code, 0)
        mock_activate_main.assert_called_once_with("/tmp/test-export.ssx")
        mock_bootstrap.assert_not_called()
        mock_run_gui.assert_called_once_with(
            "/tmp/test-export.ssx",
            debug_level="debug",
            debug_log_file="/tmp/snakesh-debug.log",
        )

    def test_debug_log_file_requires_debug_level(self) -> None:
        with self.assertRaises(SystemExit):
            main_entry.cli_main(["--debug-log-file", "/tmp/snakesh-debug.log"])

    def test_install_desktop_flag_routes_to_cli_handler(self) -> None:
        with patch("snakesh.__main__._run_install_desktop", return_value=0) as mock_handler:
            exit_code = main_entry.cli_main(["--install-desktop"])

        self.assertEqual(exit_code, 0)
        mock_handler.assert_called_once()

    def test_uninstall_desktop_flag_routes_to_cli_handler(self) -> None:
        with patch("snakesh.__main__._run_uninstall_desktop", return_value=0) as mock_handler:
            exit_code = main_entry.cli_main(["--uninstall-desktop"])

        self.assertEqual(exit_code, 0)
        mock_handler.assert_called_once()

    def test_remove_tool_launchers_flag_routes_to_cli_handler(self) -> None:
        with patch("snakesh.__main__._run_remove_tool_launchers", return_value=0) as mock_handler:
            exit_code = main_entry.cli_main(["--remove-tool-launchers"])

        self.assertEqual(exit_code, 0)
        mock_handler.assert_called_once()

    def test_web_server_helper_flag_routes_to_helper_handler(self) -> None:
        with (
            patch("snakesh.__main__.is_frozen", return_value=False),
            patch("snakesh.__main__.ensure_runtime_dependencies", return_value=True) as mock_bootstrap,
            patch("snakesh.__main__._run_web_server_helper", return_value=0) as mock_handler,
        ):
            exit_code = main_entry.cli_main(["--web-server-helper", "/tmp/web-instance"])

        self.assertEqual(exit_code, 0)
        mock_bootstrap.assert_called_once()
        mock_handler.assert_called_once_with("/tmp/web-instance")

    def test_network_inspector_ports_helper_flag_routes_to_helper_handler(self) -> None:
        with (
            patch("snakesh.__main__.is_frozen", return_value=False),
            patch("snakesh.__main__.ensure_runtime_dependencies", return_value=True) as mock_bootstrap,
            patch("snakesh.__main__._run_network_inspector_ports_helper", return_value=0) as mock_handler,
        ):
            exit_code = main_entry.cli_main(["--network-inspector-ports-helper", "/tmp/network-session"])

        self.assertEqual(exit_code, 0)
        mock_bootstrap.assert_called_once()
        mock_handler.assert_called_once_with("/tmp/network-session")

    def test_mtr_helper_flag_routes_to_helper_handler(self) -> None:
        with (
            patch("snakesh.__main__.is_frozen", return_value=False),
            patch("snakesh.__main__.ensure_runtime_dependencies", return_value=True) as mock_bootstrap,
            patch("snakesh.__main__._run_mtr_helper", return_value=0) as mock_handler,
        ):
            exit_code = main_entry.cli_main(["--mtr-helper", "/tmp/mtr-session"])

        self.assertEqual(exit_code, 0)
        mock_bootstrap.assert_called_once()
        mock_handler.assert_called_once_with("/tmp/mtr-session")

    def test_syslog_snmp_monitor_helper_flag_routes_to_helper_handler(self) -> None:
        with (
            patch("snakesh.__main__.is_frozen", return_value=False),
            patch("snakesh.__main__.ensure_runtime_dependencies", return_value=True) as mock_bootstrap,
            patch("snakesh.__main__._run_syslog_snmp_monitor_helper", return_value=0) as mock_handler,
        ):
            exit_code = main_entry.cli_main(["--syslog-snmp-monitor-helper", "profile-a"])

        self.assertEqual(exit_code, 0)
        mock_bootstrap.assert_called_once()
        mock_handler.assert_called_once_with("profile-a")

    def test_tool_list_prints_supported_keys_in_registry_order(self) -> None:
        with (
            patch("snakesh.__main__.supported_tool_keys", return_value=["resource_monitor", "ping"]),
            patch("snakesh.__main__._write_cli_message") as mock_write,
        ):
            exit_code = main_entry.cli_main(["tool", "list"])

        self.assertEqual(exit_code, 0)
        mock_write.assert_called_once_with("resource_monitor\nping\n")

    def test_windows_gui_build_main_help_routes_to_dialog_when_no_console(self) -> None:
        main_entry._WINDOWS_CONSOLE_READY = False
        with (
            patch.object(main_entry.os, "name", "nt"),
            patch("snakesh.__main__._ensure_windows_console", return_value=False),
            patch("snakesh.__main__._show_windows_cli_dialog") as mock_dialog,
        ):
            with self.assertRaises(SystemExit) as raised:
                main_entry.cli_main(["--help"])

        self.assertEqual(raised.exception.code, 0)
        message = mock_dialog.call_args.args[0]
        self.assertIn("Launch the SnakeSh GUI", message)
        self.assertFalse(mock_dialog.call_args.kwargs["error"])

    def test_windows_gui_build_tool_list_routes_to_dialog_when_no_console(self) -> None:
        main_entry._WINDOWS_CONSOLE_READY = False
        with (
            patch.object(main_entry.os, "name", "nt"),
            patch("snakesh.__main__._ensure_windows_console", return_value=False),
            patch("snakesh.__main__.supported_tool_keys", return_value=["resource_monitor", "ping"]),
            patch("snakesh.__main__._show_windows_cli_dialog") as mock_dialog,
        ):
            exit_code = main_entry.cli_main(["tool", "list"])

        self.assertEqual(exit_code, 0)
        mock_dialog.assert_called_once_with("resource_monitor\nping\n", error=False)

    def test_windows_gui_build_parser_errors_route_to_dialog_when_no_console(self) -> None:
        main_entry._WINDOWS_CONSOLE_READY = False
        with (
            patch.object(main_entry.os, "name", "nt"),
            patch("snakesh.__main__._ensure_windows_console", return_value=False),
            patch("snakesh.__main__._show_windows_cli_dialog") as mock_dialog,
        ):
            with self.assertRaises(SystemExit) as raised:
                main_entry.cli_main(["tool", "not-a-tool"])

        self.assertEqual(raised.exception.code, 2)
        message = mock_dialog.call_args.args[0]
        self.assertIn("unknown tool key: not-a-tool", message)
        self.assertTrue(mock_dialog.call_args.kwargs["error"])

    def test_main_help_text_lists_all_top_level_options_and_tool_keys(self) -> None:
        help_text = main_entry._build_parser().format_help()

        self.assertIn("--install-desktop", help_text)
        self.assertIn("--uninstall-desktop", help_text)
        self.assertIn("--remove-tool-launchers", help_text)
        self.assertIn("--web-server-helper INSTANCE_DIR", help_text)
        self.assertIn("--network-inspector-ports-helper SESSION_DIR", help_text)
        self.assertIn("--mtr-helper SESSION_DIR", help_text)
        self.assertIn("--syslog-snmp-monitor-helper PROFILE_ID", help_text)
        self.assertIn("--debug-level {info,debug,trace}", help_text)
        self.assertIn("--debug-log-file PATH", help_text)
        self.assertIn("%(prog)s tool", main_entry._build_main_usage())
        self.assertIn("resource_monitor", help_text)
        self.assertIn("syslog_snmp_monitor", help_text)
        self.assertIn("securepython is a compatibility alias", help_text)

    def test_tool_help_text_lists_registered_tool_keys_and_ping_options(self) -> None:
        help_text = main_entry._build_tool_parser().format_help()

        self.assertIn("Registered tool keys:", help_text)
        self.assertIn("resource_monitor", help_text)
        self.assertIn("syslog_snmp_monitor", help_text)
        self.assertIn("ping: --packet-size PACKET_SIZE, --ipv6", help_text)
        self.assertIn("--debug-level {info,debug,trace}", help_text)
        self.assertIn("--debug-log-file PATH", help_text)
        self.assertIn("snakesh tool help launches the Help tool", help_text)

    def test_tool_ping_help_text_lists_prefill_options(self) -> None:
        help_text = main_entry._build_tool_ping_parser().format_help()

        self.assertIn("--packet-size PACKET_SIZE", help_text)
        self.assertIn("Prefill the Ping tool packet size in bytes.", help_text)
        self.assertIn("--ipv6", help_text)
        self.assertIn("These arguments prefill the GUI tool", help_text)

    def test_tool_launch_routes_valid_tool_key_to_standalone_host(self) -> None:
        with (
            patch("snakesh.__main__.is_frozen", return_value=False),
            patch("snakesh.__main__.ensure_runtime_dependencies", return_value=True) as mock_bootstrap,
            patch("snakesh.__main__.activate_existing_tool_instance", return_value=False) as mock_activate_existing,
            patch("snakesh.__main__.run_standalone_tool", return_value=11) as mock_run_tool,
        ):
            exit_code = main_entry.cli_main(["tool", "help"])

        self.assertEqual(exit_code, 11)
        mock_bootstrap.assert_called_once()
        mock_activate_existing.assert_called_once_with("help")
        mock_run_tool.assert_called_once_with("help")

    def test_tool_launch_activates_existing_instance_without_running_host(self) -> None:
        with (
            patch("snakesh.__main__.is_frozen", return_value=False),
            patch("snakesh.__main__.ensure_runtime_dependencies", return_value=True) as mock_bootstrap,
            patch("snakesh.__main__.activate_existing_tool_instance", return_value=True) as mock_activate_existing,
            patch("snakesh.__main__.run_standalone_tool") as mock_run_tool,
        ):
            exit_code = main_entry.cli_main(["tool", "help"])

        self.assertEqual(exit_code, 0)
        mock_bootstrap.assert_called_once()
        mock_activate_existing.assert_called_once_with("help")
        mock_run_tool.assert_not_called()

    def test_tool_command_accepts_every_registered_tool_key(self) -> None:
        for entry in TOOL_REGISTRY:
            with self.subTest(tool_key=entry.key):
                with (
                    patch("snakesh.__main__.is_frozen", return_value=False),
                    patch("snakesh.__main__.ensure_runtime_dependencies", return_value=True) as mock_bootstrap,
                    patch("snakesh.__main__.activate_existing_tool_instance", return_value=False) as mock_activate_existing,
                    patch("snakesh.__main__.run_standalone_tool", return_value=0) as mock_run_tool,
                ):
                    exit_code = main_entry.cli_main(["tool", entry.key])

                self.assertEqual(exit_code, 0)
                mock_bootstrap.assert_called_once()
                if entry.key == "ping":
                    mock_activate_existing.assert_called_once_with("ping", arguments=[])
                else:
                    mock_activate_existing.assert_called_once_with(entry.key)
                if entry.key == "ping":
                    mock_run_tool.assert_called_once_with("ping", ping_packet_size=None, ping_ipv6=False)
                else:
                    mock_run_tool.assert_called_once_with(entry.key)

    def test_tool_ping_routes_prefill_args_to_standalone_host(self) -> None:
        with (
            patch("snakesh.__main__.is_frozen", return_value=False),
            patch("snakesh.__main__.ensure_runtime_dependencies", return_value=True) as mock_bootstrap,
            patch("snakesh.__main__.activate_existing_tool_instance", return_value=False) as mock_activate_existing,
            patch("snakesh.__main__.run_standalone_tool", return_value=0) as mock_run_tool,
        ):
            exit_code = main_entry.cli_main(["tool", "ping", "--packet-size", "1472", "--ipv6"])

        self.assertEqual(exit_code, 0)
        mock_bootstrap.assert_called_once()
        mock_activate_existing.assert_called_once_with(
            "ping",
            arguments=["--packet-size", "1472", "--ipv6"],
        )
        mock_run_tool.assert_called_once_with("ping", ping_packet_size=1472, ping_ipv6=True)

    def test_tool_launch_passes_debug_options_to_standalone_host(self) -> None:
        with (
            patch("snakesh.__main__.is_frozen", return_value=False),
            patch("snakesh.__main__.ensure_runtime_dependencies", return_value=True) as mock_bootstrap,
            patch("snakesh.__main__.activate_existing_tool_instance", return_value=False) as mock_activate_existing,
            patch("snakesh.__main__.run_standalone_tool", return_value=0) as mock_run_tool,
        ):
            exit_code = main_entry.cli_main(
                [
                    "tool",
                    "resource_monitor",
                    "--debug-level",
                    "debug",
                    "--debug-log-file",
                    "/tmp/resource-monitor.log",
                ]
            )

        self.assertEqual(exit_code, 0)
        mock_bootstrap.assert_called_once()
        mock_activate_existing.assert_called_once_with("resource_monitor")
        mock_run_tool.assert_called_once_with(
            "resource_monitor",
            debug_level="debug",
            debug_log_file="/tmp/resource-monitor.log",
        )

    def test_tool_ping_debug_options_do_not_break_prefill_activation(self) -> None:
        with (
            patch("snakesh.__main__.is_frozen", return_value=False),
            patch("snakesh.__main__.ensure_runtime_dependencies", return_value=True),
            patch("snakesh.__main__.activate_existing_tool_instance", return_value=False) as mock_activate_existing,
            patch("snakesh.__main__.run_standalone_tool", return_value=0) as mock_run_tool,
        ):
            exit_code = main_entry.cli_main(
                [
                    "tool",
                    "ping",
                    "--packet-size",
                    "1472",
                    "--ipv6",
                    "--debug-level",
                    "trace",
                ]
            )

        self.assertEqual(exit_code, 0)
        mock_activate_existing.assert_called_once_with(
            "ping",
            arguments=["--packet-size", "1472", "--ipv6"],
        )
        mock_run_tool.assert_called_once_with(
            "ping",
            ping_packet_size=1472,
            ping_ipv6=True,
            debug_level="trace",
        )

    def test_tool_command_rejects_invalid_tool_key(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            main_entry.cli_main(["tool", "not-a-tool"])

        self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
