from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
import sqlite3
import subprocess
import threading
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from snakesh.services.syslog_snmp_monitor import (
    DEFAULT_SYSLOG_UDP_PORT,
    SYSLOG_SNMP_MONITOR_HELPER_FLAG,
    MonitorQueryFilters,
    MonitorRetentionPolicy,
    _SnmpTrapListener,
    _pid_exists,
    clear_monitor_profile_data,
    monitor_storage_stats,
    SyslogSnmpMonitorConfig,
    archive_monitor_events,
    fetch_monitor_events,
    insert_monitor_event,
    launch_syslog_snmp_monitor_helper,
    launch_syslog_snmp_monitor_helper_elevated,
    needs_syslog_snmp_monitor_gui_elevation,
    parse_snmp_notification,
    parse_syslog_message,
    syslog_snmp_monitor_profile_paths,
    syslog_snmp_monitor_helper_command,
    write_syslog_snmp_monitor_config,
)


class SyslogSnmpMonitorServiceTests(unittest.TestCase):
    def test_parse_rfc5424_syslog_message_extracts_common_fields(self) -> None:
        event = parse_syslog_message(
            '<34>1 2026-03-31T14:22:19Z edge01 appx 412 ID47 [example@32473 test="1"] link down',
            source_ip="192.0.2.10",
            source_port=514,
            listener="syslog-tcp",
            transport="tcp",
        )

        self.assertEqual(event["protocol"], "syslog")
        self.assertEqual(event["transport"], "tcp")
        self.assertEqual(event["facility_name"], "auth")
        self.assertEqual(event["severity_name"], "Critical")
        self.assertEqual(event["syslog_hostname"], "edge01")
        self.assertEqual(event["app_name"], "appx")
        self.assertEqual(event["procid"], "412")
        self.assertEqual(event["msgid"], "ID47")
        self.assertEqual(event["structured_data"], '[example@32473 test="1"]')
        self.assertEqual(event["message_text"], "link down")

    def test_parse_rfc3164_syslog_message_extracts_tag_and_pid(self) -> None:
        event = parse_syslog_message(
            "<13>Mar 31 14:22:19 router01 sshd[9911]: Login failed",
            source_ip="198.51.100.20",
            source_port=514,
            listener="syslog-udp",
            transport="udp",
        )

        self.assertEqual(event["facility_name"], "user")
        self.assertEqual(event["severity_name"], "Notice")
        self.assertEqual(event["syslog_hostname"], "router01")
        self.assertEqual(event["app_name"], "sshd")
        self.assertEqual(event["procid"], "9911")
        self.assertEqual(event["message_text"], "Login failed")

    def test_parse_snmp_notification_extracts_common_fields(self) -> None:
        event = parse_snmp_notification(
            [
                ("1.3.6.1.2.1.1.3.0", "12345"),
                ("1.3.6.1.6.3.1.1.4.1.0", "1.3.6.1.4.1.9.9.41.2.0.1"),
                ("1.3.6.1.6.3.1.1.4.3.0", "1.3.6.1.4.1.9"),
                ("1.3.6.1.2.1.2.2.1.8.2", "down"),
            ],
            source_ip="203.0.113.30",
            source_port=162,
            security_name="trapuser",
            security_model=3,
            context_engine_id=b"\x80\x00\x00\x09",
            context_name="ops",
        )

        self.assertEqual(event["protocol"], "snmp")
        self.assertEqual(event["snmp_version"], "v3")
        self.assertEqual(event["snmp_security_name"], "trapuser")
        self.assertEqual(event["snmp_user"], "trapuser")
        self.assertEqual(event["snmp_engine_id"], "80000009")
        self.assertEqual(event["snmp_context_name"], "ops")
        self.assertEqual(event["notification_oid"], "1.3.6.1.4.1.9.9.41.2.0.1")
        self.assertEqual(event["enterprise_oid"], "1.3.6.1.4.1.9")
        self.assertEqual(event["snmp_uptime"], "12345")
        self.assertIn("1.3.6.1.2.1.2.2.1.8.2=down", event["varbind_summary"])

    def test_needs_gui_elevation_only_for_privileged_ports_on_unix(self) -> None:
        default_config = SyslogSnmpMonitorConfig()
        privileged_config = SyslogSnmpMonitorConfig(syslog_udp_port=514)

        self.assertFalse(needs_syslog_snmp_monitor_gui_elevation(default_config, platform_name="linux"))
        self.assertTrue(needs_syslog_snmp_monitor_gui_elevation(privileged_config, platform_name="linux"))
        self.assertFalse(needs_syslog_snmp_monitor_gui_elevation(privileged_config, platform_name="windows"))

    def test_helper_launch_command_uses_hidden_flag(self) -> None:
        command = syslog_snmp_monitor_helper_command("profile-a")

        self.assertIn(SYSLOG_SNMP_MONITOR_HELPER_FLAG, command)
        self.assertIn("profile-a", command)

    def test_launch_helper_elevated_linux_uses_pkexec(self) -> None:
        with (
            patch("snakesh.services.syslog_snmp_monitor.shutil.which", return_value="/usr/bin/pkexec"),
            patch("snakesh.services.syslog_snmp_monitor.subprocess.run") as mock_run,
            patch("snakesh.services.syslog_snmp_monitor.data_dir", return_value=Path("/tmp/snakesh-data")),
            patch(
                "snakesh.services.syslog_snmp_monitor.runtime.self_launch_command",
                return_value=["snakesh", SYSLOG_SNMP_MONITOR_HELPER_FLAG, "profile-a"],
            ),
        ):
            mock_run.return_value.returncode = 0
            launch_syslog_snmp_monitor_helper_elevated("profile-a", platform_name="linux")

        self.assertEqual(mock_run.call_args.args[0][:2], ["pkexec", "/bin/sh"])
        shell_command = mock_run.call_args.args[0][3]
        self.assertIn("SNAKESH_DATA_DIR=/tmp/snakesh-data", shell_command)
        self.assertIn("SNAKESH_HELPER_PARENT_PID=", shell_command)

    def test_launch_helper_sets_monitor_runtime_environment(self) -> None:
        with (
            patch("snakesh.services.syslog_snmp_monitor.subprocess.Popen") as mock_popen,
            patch("snakesh.services.syslog_snmp_monitor.data_dir", return_value=Path("/tmp/snakesh-data")),
            patch(
                "snakesh.services.syslog_snmp_monitor.runtime.self_launch_command",
                return_value=["snakesh", SYSLOG_SNMP_MONITOR_HELPER_FLAG, "profile-a"],
            ),
        ):
            launch_syslog_snmp_monitor_helper("profile-a")

        env = mock_popen.call_args.kwargs["env"]
        self.assertEqual(env["SNAKESH_DATA_DIR"], "/tmp/snakesh-data")
        self.assertTrue(env["SNAKESH_HELPER_PARENT_PID"].isdigit())

    def test_launch_helper_uses_windows_detached_process_flags(self) -> None:
        with (
            patch("snakesh.services.syslog_snmp_monitor.subprocess.Popen") as mock_popen,
            patch("snakesh.services.syslog_snmp_monitor.platform.system", return_value="Windows"),
            patch("snakesh.services.syslog_snmp_monitor.data_dir", return_value=Path("/tmp/snakesh-data")),
            patch(
                "snakesh.services.syslog_snmp_monitor.runtime.self_launch_command",
                return_value=["snakesh", SYSLOG_SNMP_MONITOR_HELPER_FLAG, "profile-a"],
            ),
        ):
            launch_syslog_snmp_monitor_helper("profile-a")

        self.assertEqual(
            mock_popen.call_args.kwargs["creationflags"],
            getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )

    def test_pid_exists_uses_windows_process_query_api(self) -> None:
        class FakeKernel32:
            def OpenProcess(self, access, inherit_handle, pid):  # noqa: ANN001
                self.access = access
                self.pid = pid
                return 41

            def GetExitCodeProcess(self, handle, exit_code_ptr):  # noqa: ANN001
                exit_code_ptr._obj.value = 259
                return True

            def CloseHandle(self, handle):  # noqa: ANN001
                self.closed_handle = handle
                return True

        fake_kernel32 = FakeKernel32()
        with (
            patch("snakesh.services.syslog_snmp_monitor.platform.system", return_value="Windows"),
            patch("ctypes.windll", new=SimpleNamespace(kernel32=fake_kernel32), create=True),
            patch("ctypes.get_last_error", return_value=0, create=True),
        ):
            self.assertTrue(_pid_exists(1234))

        self.assertEqual(fake_kernel32.pid, 1234)

    def test_snmp_listener_creates_thread_event_loop_for_asyncio_transport(self) -> None:
        transport_state: dict[str, object] = {}
        run_started = threading.Event()

        class FakeDispatcher:
            def __init__(self) -> None:
                self.closed = False

            def job_started(self, job_id, count: int = 1) -> None:
                transport_state["job"] = (job_id, count)

            def run_dispatcher(self, timeout: float = 0.0) -> None:
                transport_state["timeout"] = timeout
                run_started.set()
                time.sleep(0.01)

            def close_dispatcher(self) -> None:
                self.closed = True

        dispatcher = FakeDispatcher()

        class FakeObserver:
            def get_execution_context(self, execution_point: str) -> dict[str, object]:
                transport_state["execution_point"] = execution_point
                return {}

        class FakeSnmpEngine:
            def __init__(self) -> None:
                self.transport_dispatcher = dispatcher
                self.observer = FakeObserver()

        class FakeUdpTransport:
            def __init__(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
                if loop is None:
                    loop = asyncio.get_event_loop()
                transport_state["loop"] = loop

            def open_server_mode(self, iface: tuple[str, int]):
                transport_state["iface"] = iface
                return self

        fake_modules = {
            "engine": SimpleNamespace(SnmpEngine=FakeSnmpEngine),
            "config": SimpleNamespace(
                add_transport=lambda *_args: None,
                add_v1_system=lambda *_args: None,
                add_vacm_user=lambda *_args: None,
                add_v3_user=lambda *_args, **_kwargs: None,
            ),
            "ntfrcv": SimpleNamespace(NotificationReceiver=lambda *_args, **_kwargs: None),
            "transport": SimpleNamespace(
                __name__="pysnmp.carrier.asyncio.dgram.udp",
                DOMAIN_NAME=(1, 3, 6, 1, 6, 1, 1),
                UdpTransport=FakeUdpTransport,
            ),
        }
        listener = _SnmpTrapListener(
            "127.0.0.1",
            1162,
            SimpleNamespace(write=lambda *_args, **_kwargs: None),
            SyslogSnmpMonitorConfig(),
        )

        with patch("snakesh.services.syslog_snmp_monitor._load_pysnmp_modules", return_value=fake_modules):
            listener.start()
            self.assertTrue(run_started.wait(timeout=1.0))
            listener.stop()

        self.assertEqual(listener.error, "")
        self.assertEqual(transport_state["iface"], ("127.0.0.1", 1162))
        self.assertEqual(transport_state["job"], (1, 1))
        self.assertEqual(transport_state["timeout"], 0.5)
        self.assertTrue(dispatcher.closed)
        loop = transport_state.get("loop")
        self.assertIsNotNone(loop)
        assert isinstance(loop, asyncio.AbstractEventLoop)
        self.assertTrue(loop.is_closed())

    def test_archive_preserves_old_events_and_archived_filters_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch("snakesh.services.syslog_snmp_monitor.data_dir", return_value=Path(tmp)):
                profile_id = "profile-archive"
                retention = MonitorRetentionPolicy(hot_retention_days=1, archive_retention_days=90, max_archive_size_mb=4096, archive_rotation_mb=64)
                write_syslog_snmp_monitor_config(
                    profile_id,
                    SyslogSnmpMonitorConfig(retention=retention.to_dict()),
                )
                old_time = (datetime.now(UTC) - timedelta(days=5)).isoformat()
                new_time = datetime.now(UTC).isoformat()
                insert_monitor_event(
                    profile_id,
                    {
                        "received_ts": old_time,
                        "source_ip": "192.0.2.99",
                        "listener": "syslog-udp",
                        "protocol": "syslog",
                        "transport": "udp",
                        "severity": 2,
                        "severity_name": "Critical",
                        "facility": 4,
                        "facility_name": "auth",
                        "syslog_hostname": "old-router",
                        "app_name": "sshd",
                        "message_text": "old event",
                        "raw_payload": "old payload",
                    },
                )
                insert_monitor_event(
                    profile_id,
                    {
                        "received_ts": new_time,
                        "source_ip": "192.0.2.100",
                        "listener": "syslog-udp",
                        "protocol": "syslog",
                        "transport": "udp",
                        "severity": 6,
                        "severity_name": "Informational",
                        "facility": 1,
                        "facility_name": "user",
                        "syslog_hostname": "new-router",
                        "app_name": "cron",
                        "message_text": "new event",
                        "raw_payload": "new payload",
                    },
                )

                archived = archive_monitor_events(profile_id, retention=retention)
                self.assertEqual(archived, 1)

                live_rows = fetch_monitor_events(profile_id, MonitorQueryFilters(data_scope="live"), limit=20)
                self.assertEqual(len(live_rows), 1)
                self.assertEqual(live_rows[0]["message_text"], "new event")

                archived_rows = fetch_monitor_events(
                    profile_id,
                    MonitorQueryFilters(
                        data_scope="archived",
                        severity_name="Critical",
                        source_contains="192.0.2.99",
                    ),
                    limit=20,
                )
                self.assertEqual(len(archived_rows), 1)
                self.assertEqual(archived_rows[0]["message_text"], "old event")

    def test_clear_monitor_profile_data_removes_live_events_notifications_and_archives(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch("snakesh.services.syslog_snmp_monitor.data_dir", return_value=Path(tmp)):
                profile_id = "profile-clear"
                retention = MonitorRetentionPolicy(hot_retention_days=1, archive_retention_days=90, max_archive_size_mb=4096, archive_rotation_mb=64)
                write_syslog_snmp_monitor_config(
                    profile_id,
                    SyslogSnmpMonitorConfig(retention=retention.to_dict()),
                )
                insert_monitor_event(
                    profile_id,
                    {
                        "received_ts": (datetime.now(UTC) - timedelta(days=5)).isoformat(),
                        "source_ip": "192.0.2.50",
                        "listener": "syslog-udp",
                        "protocol": "syslog",
                        "transport": "udp",
                        "message_text": "archived event",
                    },
                )
                live_event_id = insert_monitor_event(
                    profile_id,
                    {
                        "received_ts": datetime.now(UTC).isoformat(),
                        "source_ip": "192.0.2.51",
                        "listener": "syslog-udp",
                        "protocol": "syslog",
                        "transport": "udp",
                        "message_text": "live event",
                    },
                )
                archive_monitor_events(profile_id, retention=retention)
                paths = syslog_snmp_monitor_profile_paths(profile_id)
                connection = sqlite3.connect(paths.db_path)
                try:
                    connection.execute(
                        """
                        INSERT INTO notifications (event_id, created_ts, title, body, play_sound, shown)
                        VALUES (?, ?, ?, ?, ?, 0)
                        """,
                        (
                            live_event_id,
                            datetime.now(UTC).isoformat(),
                            "Alert",
                            "Body",
                            0,
                        ),
                    )
                    connection.commit()
                finally:
                    connection.close()

                self.assertEqual(len(list(paths.archives_root.rglob("*.jsonl.gz"))), 1)

                result = clear_monitor_profile_data(profile_id)

                self.assertEqual(result.live_event_count, 1)
                self.assertEqual(result.notification_count, 1)
                self.assertEqual(result.archive_file_count, 1)
                self.assertEqual(fetch_monitor_events(profile_id, MonitorQueryFilters(data_scope="all"), limit=20), [])
                stats = monitor_storage_stats(profile_id)
                self.assertEqual(stats.live_event_count, 0)
                self.assertEqual(stats.notification_count, 0)
                self.assertEqual(stats.archive_file_count, 0)


if __name__ == "__main__":
    unittest.main()
