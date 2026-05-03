from __future__ import annotations

import argparse
import os
import sys

from snakesh.bootstrap import ensure_runtime_dependencies
from snakesh.core.tool_registry import TOOL_REGISTRY_BY_KEY
from snakesh.runtime import is_frozen
from snakesh.services.diagnostics_service import DEBUG_LEVEL_NAMES
from snakesh.services.linux_desktop_install_service import (
    LinuxDesktopIntegrationError,
    install_desktop_integration,
    uninstall_desktop_integration,
)
from snakesh.services.main_instance_service import activate_existing_main_instance
from snakesh.services.tool_launcher_service import ToolLauncherError, remove_tool_launchers
from snakesh.services.tool_process_service import (
    activate_existing_tool_instance,
    ping_tool_arguments,
    supported_tool_keys,
)
from snakesh.ui.standalone_tool_host import run_standalone_tool


_HELP_FORMATTER = argparse.RawDescriptionHelpFormatter
_WINDOWS_CONSOLE_READY = False


def _ensure_windows_console() -> bool:
    global _WINDOWS_CONSOLE_READY
    if os.name != "nt":
        return True
    if _WINDOWS_CONSOLE_READY:
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        if kernel32.GetConsoleWindow():
            _WINDOWS_CONSOLE_READY = True
            return True
        attached = bool(kernel32.AttachConsole(-1))
        if not attached:
            return False
        sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1)  # noqa: SIM115
        sys.stderr = open("CONOUT$", "w", encoding="utf-8", buffering=1)  # noqa: SIM115
        _WINDOWS_CONSOLE_READY = True
        return True
    except Exception:
        return False


def _show_windows_cli_dialog(message: str, *, error: bool) -> None:
    try:
        import ctypes

        icon = 0x10 if error else 0x40
        ctypes.windll.user32.MessageBoxW(None, message, "SnakeSh Command Line", icon)
    except Exception:
        return


def _write_cli_message(message: str, *, stderr: bool = False) -> None:
    if not message:
        return
    if os.name == "nt" and not _ensure_windows_console():
        _show_windows_cli_dialog(message, error=stderr)
        return
    stream = sys.stderr if stderr else sys.stdout
    stream.write(message)
    stream.flush()


class _SnakeShArgumentParser(argparse.ArgumentParser):
    def _print_message(self, message, file=None) -> None:  # noqa: ANN001
        _write_cli_message(str(message or ""), stderr=file is sys.stderr)

    def error(self, message: str) -> None:
        usage = self.format_usage()
        _write_cli_message(f"{usage}{self.prog}: error: {message}\n", stderr=True)
        raise SystemExit(2)


def _tool_registry_help_lines() -> str:
    width = max(len(entry.key) for entry in TOOL_REGISTRY_BY_KEY.values())
    return "\n".join(
        f"  {entry.key.ljust(width)}  {entry.label}"
        for entry in TOOL_REGISTRY_BY_KEY.values()
    )


def _build_main_usage() -> str:
    debug_levels = ",".join(DEBUG_LEVEL_NAMES)
    return (
        "%(prog)s [-h] [--install-desktop | --uninstall-desktop |\n"
        "         --remove-tool-launchers |\n"
        "         --web-server-helper INSTANCE_DIR |\n"
        "         --network-inspector-ports-helper SESSION_DIR |\n"
        "         --mtr-helper SESSION_DIR |\n"
        "         --syslog-snmp-monitor-helper PROFILE_ID]\n"
        f"         [--debug-level {{{debug_levels}}}] [--debug-log-file PATH] [import_file]\n"
        "       %(prog)s tool ..."
    )


def _build_main_epilog() -> str:
    return (
        "Examples:\n"
        "  snakesh\n"
        "  snakesh export.ssx\n"
        "  snakesh --debug-level debug\n"
        "  snakesh --debug-level trace --debug-log-file /tmp/snakesh.log\n"
        "  snakesh --install-desktop\n"
        "  snakesh --remove-tool-launchers\n"
        "  snakesh tool list\n"
        "  snakesh tool ping --packet-size 1452 --ipv6\n\n"
        "Standalone tool keys:\n"
        f"{_tool_registry_help_lines()}\n\n"
        "Notes:\n"
        "  securepython is a compatibility alias for snakesh.\n"
        "  Helper flags such as --web-server-helper are internal launch modes used by\n"
        "  SnakeSh-managed child processes."
    )


def _build_tool_usage() -> str:
    debug_levels = ",".join(DEBUG_LEVEL_NAMES)
    return (
        f"%(prog)s [-h] [--debug-level {{{debug_levels}}}] [--debug-log-file PATH] list\n"
        f"       %(prog)s [-h] [--debug-level {{{debug_levels}}}] [--debug-log-file PATH] TOOL_KEY\n"
        f"       %(prog)s [-h] [--debug-level {{{debug_levels}}}] [--debug-log-file PATH] ping "
        "[--packet-size PACKET_SIZE] [--ipv6]"
    )


def _build_tool_epilog() -> str:
    return (
        "Examples:\n"
        "  snakesh tool list\n"
        "  snakesh tool help\n"
        "  snakesh tool resource_monitor\n"
        "  snakesh tool resource_monitor --debug-level debug --debug-log-file /tmp/resource-monitor.log\n"
        "  snakesh tool ping --packet-size 1472\n"
        "  snakesh tool ping --packet-size 1452 --ipv6\n\n"
        "Registered tool keys:\n"
        f"{_tool_registry_help_lines()}\n\n"
        "Tool-specific arguments:\n"
        "  ping: --packet-size PACKET_SIZE, --ipv6\n"
        "  all tools: --help, --debug-level {info,debug,trace}, --debug-log-file PATH\n"
        "  debug flags may be placed before TOOL_KEY or after it; values after the\n"
        "  tool key take precedence when both are present\n"
        "  all other tools: no extra tool-specific arguments\n\n"
        "Note:\n"
        "  snakesh tool help launches the Help tool; use snakesh tool --help for this\n"
        "  command reference."
    )


def _add_debug_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--debug-level",
        choices=DEBUG_LEVEL_NAMES,
        help="Enable per-run diagnostics and freeze logging for this launch.",
    )
    parser.add_argument(
        "--debug-log-file",
        metavar="PATH",
        help="Optional diagnostics log path for this run; requires --debug-level.",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = _SnakeShArgumentParser(
        prog="snakesh",
        usage=_build_main_usage(),
        description=(
            "Launch the SnakeSh GUI, import a SnakeSh export bundle, manage Linux "
            "desktop integration, clean up tool launchers, or run SnakeSh internal helper modes."
        ),
        epilog=_build_main_epilog(),
        formatter_class=_HELP_FORMATTER,
    )
    mode_group = parser.add_argument_group(
        "Launch modes",
        "Pick one alternate mode below, or omit them to start the main GUI.",
    )
    group = mode_group.add_mutually_exclusive_group()
    group.add_argument(
        "--install-desktop",
        action="store_true",
        help="Install/repair Linux AppImage desktop integration and exit.",
    )
    group.add_argument(
        "--uninstall-desktop",
        action="store_true",
        help="Remove Linux AppImage desktop integration and managed tool launchers, then exit.",
    )
    group.add_argument(
        "--remove-tool-launchers",
        action="store_true",
        help="Remove managed standalone tool launcher entries and exit.",
    )
    group.add_argument(
        "--web-server-helper",
        metavar="INSTANCE_DIR",
        help="Internal: launch the Web Server helper for INSTANCE_DIR and exit.",
    )
    group.add_argument(
        "--network-inspector-ports-helper",
        metavar="SESSION_DIR",
        help="Internal: launch the Network Inspector ports helper for SESSION_DIR and exit.",
    )
    group.add_argument(
        "--mtr-helper",
        metavar="SESSION_DIR",
        help="Internal: launch the native traceroute/MTR helper for SESSION_DIR and exit.",
    )
    group.add_argument(
        "--syslog-snmp-monitor-helper",
        metavar="PROFILE_ID",
        help="Internal: launch the Syslog / SNMP Monitor helper for PROFILE_ID and exit.",
    )
    parser.add_argument(
        "import_file",
        nargs="?",
        help="Optional SnakeSh export bundle (.ssx) to import on startup.",
    )
    _add_debug_arguments(parser)
    return parser


def _build_tool_parser() -> argparse.ArgumentParser:
    parser = _SnakeShArgumentParser(
        prog="snakesh tool",
        usage=_build_tool_usage(),
        description="Launch a standalone SnakeSh tool or print the supported tool list.",
        epilog=_build_tool_epilog(),
        formatter_class=_HELP_FORMATTER,
    )
    _add_debug_arguments(parser)
    parser.add_argument(
        "tool_key",
        metavar="TOOL_KEY",
        help="Registered tool key to launch, or 'list' to print supported tool keys.",
    )
    parser.add_argument("tool_args", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
    return parser


def _build_tool_ping_parser() -> argparse.ArgumentParser:
    parser = _SnakeShArgumentParser(
        prog="snakesh tool ping",
        description="Launch the standalone Ping tool with optional prefilled values.",
        epilog=(
            "Examples:\n"
            "  snakesh tool ping\n"
            "  snakesh tool ping --packet-size 1472\n"
            "  snakesh tool ping --packet-size 1452 --ipv6\n\n"
            "These arguments prefill the GUI tool; they do not automatically start a ping."
        ),
        formatter_class=_HELP_FORMATTER,
    )
    _add_debug_arguments(parser)
    parser.add_argument(
        "--packet-size",
        type=int,
        help="Prefill the Ping tool packet size in bytes.",
    )
    parser.add_argument(
        "--ipv6",
        action="store_true",
        help="Prefill the Ping tool to use IPv6 mode.",
    )
    return parser


def _build_tool_debug_parser(tool_key: str) -> argparse.ArgumentParser:
    label = TOOL_REGISTRY_BY_KEY.get(tool_key).label if tool_key in TOOL_REGISTRY_BY_KEY else tool_key
    parser = _SnakeShArgumentParser(
        prog=f"snakesh tool {tool_key}",
        description=f"Launch the standalone {label} tool.",
        formatter_class=_HELP_FORMATTER,
    )
    _add_debug_arguments(parser)
    return parser


def _validate_debug_log_file(
    parser: argparse.ArgumentParser,
    *,
    debug_level: str | None,
    debug_log_file: str | None,
) -> None:
    if debug_log_file and not debug_level:
        parser.error("--debug-log-file requires --debug-level")


def _merge_debug_options(
    primary: argparse.Namespace,
    secondary: argparse.Namespace | None,
) -> tuple[str | None, str | None]:
    debug_level = getattr(secondary, "debug_level", None) or getattr(primary, "debug_level", None)
    debug_log_file = getattr(secondary, "debug_log_file", None) or getattr(primary, "debug_log_file", None)
    return debug_level, debug_log_file


def _run_gui(
    import_file: str | None = None,
    *,
    debug_level: str | None = None,
    debug_log_file: str | None = None,
) -> int:
    from snakesh.app import main

    return main(
        import_file=import_file,
        debug_level=debug_level,
        debug_log_file=debug_log_file,
    )


def _run_install_desktop() -> int:
    try:
        installed_path = install_desktop_integration()
    except LinuxDesktopIntegrationError as exc:
        print(f"SnakeSh desktop install failed: {exc}")
        return 1
    print(f"SnakeSh desktop integration installed: {installed_path}")
    return 0


def _run_uninstall_desktop() -> int:
    try:
        removed = uninstall_desktop_integration()
    except LinuxDesktopIntegrationError as exc:
        print(f"SnakeSh desktop uninstall failed: {exc}")
        return 1
    if removed:
        print("SnakeSh desktop integration and tool launchers removed.")
    else:
        print("SnakeSh desktop integration and tool launchers were already absent.")
    return 0


def _run_remove_tool_launchers() -> int:
    try:
        result = remove_tool_launchers()
    except ToolLauncherError as exc:
        print(f"SnakeSh tool launcher removal failed: {exc}")
        return 1
    if result.removed_keys:
        labels = ", ".join(TOOL_REGISTRY_BY_KEY[key].label for key in result.removed_keys)
        print(f"SnakeSh tool launchers removed: {labels}")
    else:
        print("SnakeSh tool launchers were already absent.")
    return 0


def _run_web_server_helper(instance_dir: str) -> int:
    from snakesh.services.web_server_service import run_web_server_helper

    return run_web_server_helper(instance_dir)


def _run_network_inspector_ports_helper(session_dir: str) -> int:
    from snakesh.services.network_inspector import run_network_inspector_ports_helper

    return run_network_inspector_ports_helper(session_dir)


def _run_mtr_helper(session_dir: str) -> int:
    from snakesh.services.mtr_trace import run_mtr_helper

    return run_mtr_helper(session_dir)


def _run_syslog_snmp_monitor_helper(profile_id: str) -> int:
    from snakesh.services.syslog_snmp_monitor import run_syslog_snmp_monitor_helper

    return run_syslog_snmp_monitor_helper(profile_id)


def _run_tool_list() -> int:
    _write_cli_message("\n".join(supported_tool_keys()) + "\n")
    return 0


def _tool_parser_error(parser: argparse.ArgumentParser, message: str) -> int:
    parser.error(message)
    return 2  # pragma: no cover - parser.error always raises


def _run_tool_command(argv: list[str]) -> int:
    parser = _build_tool_parser()
    args = parser.parse_args(argv)
    tool_key = str(args.tool_key).strip()
    _validate_debug_log_file(parser, debug_level=args.debug_level, debug_log_file=args.debug_log_file)
    if tool_key == "list":
        if args.tool_args:
            return _tool_parser_error(parser, "'list' does not accept extra arguments")
        return _run_tool_list()
    if tool_key not in TOOL_REGISTRY_BY_KEY:
        return _tool_parser_error(parser, f"unknown tool key: {tool_key}")
    if tool_key == "ping":
        ping_parser = _build_tool_ping_parser()
        ping_args = ping_parser.parse_args(args.tool_args)
        debug_level, debug_log_file = _merge_debug_options(args, ping_args)
        _validate_debug_log_file(ping_parser, debug_level=debug_level, debug_log_file=debug_log_file)
        if not is_frozen() and not ensure_runtime_dependencies():
            return 1
        activation_arguments = ping_tool_arguments(packet_size=ping_args.packet_size, ipv6=bool(ping_args.ipv6))
        if activate_existing_tool_instance(tool_key, arguments=activation_arguments):
            return 0
        kwargs: dict[str, object] = {
            "ping_packet_size": ping_args.packet_size,
            "ping_ipv6": bool(ping_args.ipv6),
        }
        if debug_level:
            kwargs["debug_level"] = debug_level
        if debug_log_file:
            kwargs["debug_log_file"] = debug_log_file
        return run_standalone_tool(tool_key, **kwargs)
    tool_debug_parser = _build_tool_debug_parser(tool_key)
    tool_debug_args = tool_debug_parser.parse_args(args.tool_args)
    debug_level, debug_log_file = _merge_debug_options(args, tool_debug_args)
    _validate_debug_log_file(tool_debug_parser, debug_level=debug_level, debug_log_file=debug_log_file)
    if getattr(tool_debug_args, "tool_args", None):
        return _tool_parser_error(parser, f"{tool_key} does not accept extra arguments")
    if not is_frozen() and not ensure_runtime_dependencies():
        return 1
    if activate_existing_tool_instance(tool_key):
        return 0
    kwargs = {}
    if debug_level:
        kwargs["debug_level"] = debug_level
    if debug_log_file:
        kwargs["debug_log_file"] = debug_log_file
    return run_standalone_tool(tool_key, **kwargs)


def cli_main(argv: list[str] | None = None) -> int:
    provided_argv = list(argv) if argv is not None else list(sys.argv[1:])
    if provided_argv and provided_argv[0] == "tool":
        return _run_tool_command(provided_argv[1:])

    parser = _build_parser()
    args = parser.parse_args(provided_argv)
    _validate_debug_log_file(parser, debug_level=args.debug_level, debug_log_file=args.debug_log_file)
    if args.install_desktop:
        return _run_install_desktop()
    if args.uninstall_desktop:
        return _run_uninstall_desktop()
    if args.remove_tool_launchers:
        return _run_remove_tool_launchers()

    helper_mode = bool(
        args.web_server_helper
        or args.network_inspector_ports_helper
        or args.mtr_helper
        or args.syslog_snmp_monitor_helper
    )
    if not helper_mode and activate_existing_main_instance(args.import_file):
        return 0

    if not is_frozen() and not ensure_runtime_dependencies():
        return 1
    if args.web_server_helper:
        return _run_web_server_helper(args.web_server_helper)
    if args.network_inspector_ports_helper:
        return _run_network_inspector_ports_helper(args.network_inspector_ports_helper)
    if args.mtr_helper:
        return _run_mtr_helper(args.mtr_helper)
    if args.syslog_snmp_monitor_helper:
        return _run_syslog_snmp_monitor_helper(args.syslog_snmp_monitor_helper)
    if not args.debug_level and not args.debug_log_file:
        return _run_gui(args.import_file)
    return _run_gui(
        args.import_file,
        debug_level=args.debug_level,
        debug_log_file=args.debug_log_file,
    )


if __name__ == "__main__":
    raise SystemExit(cli_main())
