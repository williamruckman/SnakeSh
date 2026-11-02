from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import time
from unittest.mock import Mock, patch

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import QTimer

from snakesh.core.models import Protocol, Session
from snakesh.services.rdp_resize import RdpResizeMonitor, smart_sizing_command
from snakesh.services.viewer_output import ViewerOutput
from snakesh.ui.main_window import RemoteViewerTab


PREFIX = "[DEBUG][com.freerdp.core.rdp] - "
ACTIVE = PREFIX + "[rdp_client_transition_to_state][0x1]: CONNECTION_STATE_FINALIZATION_CLIENT_FONT_MAP --> CONNECTION_STATE_ACTIVE\n"
REACTIVATE = PREFIX + "[rdp_client_transition_to_state][0x1]: CONNECTION_STATE_ACTIVE --> CONNECTION_STATE_CAPABILITIES_EXCHANGE_DEMAND_ACTIVE\n"
ABORT = "[WARN][com.freerdp.core.rdp] - [update_recv][0x1]: State CONNECTION_STATE_ACTIVE (or higher) requested, but CONNECTION_STATE_FINALIZATION_CLIENT_SYNC found. Aborting.\n"
FAILED = "[ERROR][com.freerdp.core.rdp] - [rdp_recv_callback_int][0x1]: CONNECTION_STATE_FINALIZATION_CLIENT_SYNC status STATE_RUN_FAILED [-1]\n"
CANCELLED = "[ERROR][com.freerdp.core] - [freerdp_abort_connect_context]: ERRCONNECT_CONNECT_CANCELLED [0x0002000B]\n"
RESIZE_FAILURE = ACTIVE + REACTIVATE + ABORT + FAILED
_APP = None


@pytest.mark.parametrize("chunk_size", [1, 17, 2048])
def test_recognizes_only_failed_reactivation_after_an_established_session(chunk_size):
    monitor = RdpResizeMonitor()
    for offset in range(0, len(RESIZE_FAILURE), chunk_size):
        monitor.feed(RESIZE_FAILURE[offset:offset + chunk_size])
    assert monitor.should_recover(1)
    assert not monitor.should_recover(0)
    assert not monitor.should_recover(-11)


@pytest.mark.parametrize("output", [
    ABORT + FAILED,
    REACTIVATE + ABORT + FAILED,
    ACTIVE + ABORT + FAILED,
    ACTIVE + REACTIVATE + FAILED,
    ACTIVE + REACTIVATE + ABORT,
    ACTIVE + "ERRCONNECT_LOGON_FAILURE\n",
    ACTIVE + "Network disconnect!\n",
    RESIZE_FAILURE + CANCELLED,
    RESIZE_FAILURE + ACTIVE + "Network disconnect!\n",
    RESIZE_FAILURE + PREFIX + "[rdp_client_transition_to_state][0x1]: CONNECTION_STATE_INITIAL --> CONNECTION_STATE_NEGO\nERRCONNECT_LOGON_FAILURE\n",
])
def test_login_failures_cancellations_and_unrelated_exits_are_not_resize_recovery(output):
    monitor = RdpResizeMonitor()
    monitor.feed(output)
    assert not monitor.should_recover(1)


def test_stream_monitor_remembers_activation_after_it_leaves_the_bounded_output_tail():
    monitor = RdpResizeMonitor()
    payload = ACTIVE + "x\n" * 100000 + REACTIVATE + ABORT + FAILED
    script = "import sys; sys.stdout.write(" + repr(payload) + "); sys.exit(1)"
    # A stdin-fed script avoids the OS argument-length limit for this fixture.
    process = subprocess.Popen([sys.executable, "-"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    output = ViewerOutput(process.stdout, on_output=monitor.feed)
    process.stdin.write(script.encode())
    process.stdin.close()
    assert process.wait(timeout=10) == 1
    tail = output.tail()
    assert len(tail) <= ViewerOutput.MAX_CHARS
    assert ACTIVE.strip() not in tail
    assert monitor.should_recover(1)


@pytest.mark.parametrize("extra", [[], ["/size:1280x720", "/parent-window:1234"], ["/f"]])
def test_smart_sizing_recovery_preserves_security_and_target_without_mutating_command(extra):
    command = ["sdl-freerdp", "/v:example.test:3390", "/u:tester", "/cert:tofu", "/sec:nla",
               "/from-stdin:force", "/dynamic-resolution", "/bpp:32", "/audio-mode:2",
               "/kbd:remap:347=29,remap:348=285", *extra]
    original = list(command)
    result = smart_sizing_command(command)
    assert command == original
    assert "/dynamic-resolution" not in result
    assert result.count("/smart-sizing") == 1
    assert all(arg in result for arg in original if arg != "/dynamic-resolution")
    assert ("/size:1920x1080" in result) == (not extra)
    assert smart_sizing_command(result) == result


@pytest.fixture
def tab():
    global _APP
    _APP = QApplication.instance() or QApplication([])
    session = Session(id="resize", name="RDP", host="example.test", protocol=Protocol.RDP,
                      port=3389, display_resolution="auto")
    command = ["sdl-freerdp", "/v:example.test", "/u:tester", "/cert:tofu",
               "/dynamic-resolution", "/from-stdin:force", "/audio-mode:0"]
    with patch("snakesh.ui.main_window.platform.system", return_value="Darwin"):
        widget = RemoteViewerTab(
            session=session, protocol_name="RDP",
            detached_command_builder=lambda: (list(command), "FreeRDP", {"PATH": "/usr/bin"}, "secret\n"),
        )
        try:
            yield widget
        finally:
            widget.shutdown()
            widget.deleteLater()
            _APP.processEvents()


def process(output="", code=None):
    child = Mock(pid=12345, stdout=io.StringIO(output))
    child.poll.return_value = code
    return child


def test_mac_failure_reconnects_once_and_shows_one_explanation(tab):
    original = tab._session.to_dict()
    first, second = process(RESIZE_FAILURE, 1), process()
    with patch("snakesh.ui.main_window.subprocess.Popen", side_effect=[first, second]) as launch:
        assert tab.detach_viewer()[0]
        tab._watch_process_state()
        QApplication.processEvents()
        assert launch.call_count == 2
        initial, recovered = launch.call_args_list
        assert "/log-filters:com.freerdp.core.rdp:DEBUG" in initial.args[0]
        assert "/dynamic-resolution" not in recovered.args[0]
        assert "/smart-sizing" in recovered.args[0]
        assert "/cert:tofu" in recovered.args[0]
        assert initial.kwargs["env"] == recovered.kwargs["env"]
        assert recovered.kwargs["stdin"] == subprocess.PIPE
        assert "secret" not in " ".join(recovered.args[0])
        first.stdin.write.assert_called_once_with("secret\n")
        second.stdin.write.assert_called_once_with("secret\n")
        assert tab._mode == "detached"
        assert not tab.property("remote_viewer_closed")
        assert "Smart sizing fallback" in tab._hint_label.text()
        notice = tab._rdp_resize_notice
        assert notice.isVisible()
        assert "restarted" in notice.text()
        assert "fixed remote resolution" in notice.informativeText()
        assert "distribution repository update" in notice.informativeText()
        assert tab._session.to_dict() == original
        # A failed recovery client never causes another automatic launch.
        second.poll.return_value = 1
        tab._watch_process_state()
        QApplication.processEvents()
        assert launch.call_count == 2
        assert tab._rdp_resize_notice is notice
        assert len(tab.findChildren(QMessageBox)) == 1


@pytest.mark.parametrize("output,code", [(ABORT + FAILED, 1), (RESIZE_FAILURE + CANCELLED, 1),
                                        (RESIZE_FAILURE, 0), (RESIZE_FAILURE, -11)])
def test_no_reconnect_for_initial_errors_normal_exit_or_user_cancellation(tab, output, code):
    with patch("snakesh.ui.main_window.subprocess.Popen", return_value=process(output, code)) as launch:
        assert tab.detach_viewer()[0]
        tab._watch_process_state()
        QApplication.processEvents()
        assert launch.call_count == 1
        assert not tab._rdp_smart_sizing_fallback
        assert tab._rdp_resize_notice is None


def test_closing_the_tab_cancels_a_queued_recovery(tab):
    with patch("snakesh.ui.main_window.subprocess.Popen", return_value=process(RESIZE_FAILURE, 1)) as launch:
        assert tab.detach_viewer()[0]
        tab._watch_process_state()
        assert tab._rdp_resize_retry_timer.isActive()
        tab.shutdown()
        QApplication.processEvents()
        assert launch.call_count == 1
        assert tab._rdp_resize_notice is None


def test_manual_launch_supersedes_a_queued_recovery(tab):
    with patch("snakesh.ui.main_window.subprocess.Popen", side_effect=[process(RESIZE_FAILURE, 1), process()]) as launch:
        assert tab.detach_viewer()[0]
        tab._watch_process_state()
        assert tab.detach_viewer()[0]
        QApplication.processEvents()
        assert launch.call_count == 2
        assert "/dynamic-resolution" in launch.call_args.args[0]
        assert tab._rdp_resize_notice is None


def test_failed_recovery_launch_is_reported_without_a_retry_loop(tab):
    with patch("snakesh.ui.main_window.subprocess.Popen", side_effect=[process(RESIZE_FAILURE, 1), OSError("launch failed")]) as launch:
        assert tab.detach_viewer()[0]
        tab._watch_process_state()
        QApplication.processEvents()
        assert launch.call_count == 2
        assert "also failed" in tab._rdp_resize_notice.text()
        assert "launch failed" in tab._rdp_resize_notice.text()
        tab._watch_process_state()
        QApplication.processEvents()
        assert launch.call_count == 2


def test_closing_during_recovery_command_preparation_does_not_launch_or_show_a_popup(tab):
    builder = tab._detached_command_builder
    with patch("snakesh.ui.main_window.subprocess.Popen", return_value=process(RESIZE_FAILURE, 1)) as launch:
        assert tab.detach_viewer()[0]
        tab._watch_process_state()
        def closing_builder():
            tab.shutdown()
            return builder()
        tab._detached_command_builder = closing_builder
        QApplication.processEvents()
        assert launch.call_count == 1
        assert tab._rdp_resize_notice is None


@pytest.mark.parametrize("changed_option", ["/v:different.test", "/cert:ignore", "/u:other-user", "/sec:rdp"])
def test_recovery_never_sends_credentials_to_a_changed_destination_or_security_context(tab, changed_option):
    builder = tab._detached_command_builder
    with patch("snakesh.ui.main_window.subprocess.Popen", return_value=process(RESIZE_FAILURE, 1)) as launch:
        assert tab.detach_viewer()[0]
        tab._watch_process_state()
        def changed_builder():
            command, viewer, env, payload = builder()
            prefix = changed_option.split(":", 1)[0] + ":"
            command = [arg for arg in command if not arg.startswith(prefix)]
            return [*command, changed_option], viewer, env, payload
        tab._detached_command_builder = changed_builder
        QApplication.processEvents()
        assert launch.call_count == 1
        assert "connection settings changed" in tab._rdp_resize_notice.text()


@pytest.mark.parametrize("finish_before_deadline", [True, False])
def test_incomplete_output_never_triggers_recovery_before_late_cancellation(tab, finish_before_deadline):
    child = process(RESIZE_FAILURE, 1)
    with patch("snakesh.ui.main_window.subprocess.Popen", return_value=child) as launch:
        assert tab.detach_viewer()[0]
        # Model a reader still draining output after the client exits.
        tab._viewer_output.tail()
        draining = Mock(finished=False)
        draining.tail.return_value = RESIZE_FAILURE
        tab._viewer_output = draining
        with patch("snakesh.ui.main_window.time.monotonic", side_effect=[1.0, 5.0]):
            tab._watch_process_state()
            assert not tab._rdp_resize_retry_timer.isActive()
            if finish_before_deadline:
                tab._rdp_resize_monitor.feed(CANCELLED)
                draining.finished = True
            tab._watch_process_state()
        QApplication.processEvents()
        assert launch.call_count == 1
        assert tab._rdp_resize_notice is None
        assert tab._mode == "idle"


def test_linux_embedded_recovery_preserves_the_parent_and_size(tab):
    tab._embedded_command_builder = lambda parent: (
        ["xfreerdp3", "/v:example.test", "/dynamic-resolution", f"/parent-window:{parent}", "/size:1280x720"],
        "FreeRDP", None,
    )
    with (
        patch("snakesh.ui.main_window.platform.system", return_value="Linux"),
        patch.object(tab, "_linux_parent_rdp_support_status", return_value=(True, "")),
        patch.object(tab, "_sync_linux_parent_rdp_embedded_window"),
        patch("snakesh.ui.main_window.subprocess.Popen", side_effect=[process(RESIZE_FAILURE, 1), process()]) as launch,
    ):
        assert tab.attach_viewer()[0]
        tab._watch_process_state()
        QApplication.processEvents()
        assert launch.call_count == 2
        command = launch.call_args.args[0]
        assert "/smart-sizing" in command and "/dynamic-resolution" not in command
        assert "/size:1280x720" in command
        assert any(arg.startswith("/parent-window:") for arg in command)
        assert tab._mode == "embedded"


def test_real_child_process_recovers_while_the_qt_event_loop_remains_responsive(tab, tmp_path):
    executable = tmp_path / "sdl-freerdp"
    records = tmp_path / "launches.jsonl"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys, time\n"
        "credential_ok = sys.stdin.read() == 'secret\\n'\n"
        "with open(os.environ['RESIZE_TEST_RECORDS'], 'a') as record:\n"
        "    record.write(json.dumps({'args': sys.argv[1:], 'credential_ok': credential_ok}) + '\\n')\n"
        "if '/dynamic-resolution' in sys.argv:\n"
        f"    sys.stderr.write({RESIZE_FAILURE!r})\n"
        "    sys.exit(1)\n"
        "time.sleep(10)\n",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    tab._detached_command_builder = lambda: (
        [str(executable), "/v:example.test", "/dynamic-resolution", "/from-stdin:force", "/cert:tofu"],
        "FreeRDP replay fixture", dict(os.environ, RESIZE_TEST_RECORDS=str(records)), "secret\n",
    )
    ticks = []
    heartbeat = QTimer()
    heartbeat.setInterval(5)
    heartbeat.timeout.connect(lambda: ticks.append(True))
    heartbeat.start()
    try:
        assert tab.detach_viewer()[0]
        deadline = time.monotonic() + 5
        launches = []
        while time.monotonic() < deadline:
            QApplication.processEvents()
            tab._watch_process_state()
            if records.exists():
                launches = [json.loads(line) for line in records.read_text().splitlines()]
            if len(launches) == 2 and tab._rdp_resize_notice is not None:
                break
            time.sleep(0.01)
        assert len(launches) == 2
        assert ticks
        assert all(launch['credential_ok'] for launch in launches)
        assert "/dynamic-resolution" in launches[0]['args']
        assert "/smart-sizing" in launches[1]['args']
        assert "/dynamic-resolution" not in launches[1]['args']
        assert tab._detached_process.poll() is None
        assert tab._rdp_resize_notice.isVisible()
    finally:
        heartbeat.stop()
