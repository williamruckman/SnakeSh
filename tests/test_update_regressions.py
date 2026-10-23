from __future__ import annotations

import asyncio
import os
from pathlib import Path
import tempfile
import threading
import time
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QTabWidget, QWidget, QSystemTrayIcon
from PySide6.QtTest import QTest

from snakesh.core.models import Protocol, Session
from snakesh.protocols.sftp_client import _ResponsiveSFTP, TransferCancelledError, SFTPEntry, TransferProgress
from snakesh.protocols.rdp import build_rdp_command, prepare_rdp_launch_environment
from snakesh.services.settings_service import AppSettings
from snakesh.services.platform_deps import required_dependencies
from snakesh.ui.background import run_background_call
from snakesh.ui.main_window import MainWindow, SFTPSessionTab, UploadProgressDialog, TerminalTab
from snakesh.ui.session_editor import SessionEditorDialog
from snakesh.ui.tray import TrayController


@pytest.fixture(scope="module")
def app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    application = QApplication.instance() or QApplication([])
    yield application
    application.processEvents()


def test_background_call_keeps_gui_timers_alive_and_propagates_errors(app):
    gui_thread = threading.get_ident()
    ticks = []
    timer = QTimer()
    timer.setInterval(5)
    timer.timeout.connect(lambda: ticks.append(threading.get_ident()))
    timer.start()
    try:
        def work():
            time.sleep(0.08)
            return threading.get_ident()
        assert run_background_call(work) != gui_thread
        assert ticks and all(thread == gui_thread for thread in ticks)
        def fail():
            raise ValueError("worker failure")
        with pytest.raises(ValueError, match="worker failure"):
            run_background_call(fail)
    finally:
        timer.stop()


def test_sftp_preparation_progress_is_modeless_and_other_tab_remains_usable(app):
    with tempfile.TemporaryDirectory() as directory:
        session = Session(name="SFTP", host="example.invalid", protocol=Protocol.SFTP, port=22)
        owner = SimpleNamespace()
        owner._is_host_key_error = lambda error: False
        owner._is_auth_error = lambda error: False
        async def prepare(*args, **kwargs):
            await asyncio.sleep(0.12)
            return []
        client = SimpleNamespace(find_upload_overwrite_conflicts=prepare,
                                 upload_paths=AsyncMock(return_value=1))
        tabs = QTabWidget()
        tab = SFTPSessionTab(session=session, sftp=client, initial_remote_dir="/remote",
            initial_remote_entries=[], initial_local_dir=directory, password=None,
            execute_remote=lambda session, password, operation:
                MainWindow._execute_sftp_operation(owner, session, password, operation),
            status_callback=lambda *_: None, should_confirm_delete=lambda: True)
        tabs.addTab(tab, "SFTP")
        other = QWidget()
        tabs.addTab(other, "Terminal")
        observations = []
        def switch():
            dialog = tab.findChild(UploadProgressDialog)
            observations.append((dialog.isVisible(), dialog.windowModality(), tab._file_task_active,
                                 tab.local_upload_btn.isEnabled(), tab.remote_download_btn.isEnabled()))
            tabs.setCurrentWidget(other)
        tabs.show()
        QTimer.singleShot(20, switch)
        try:
            with patch.object(tab, '_refresh_remote_directory'):
                tab._upload_local_paths([str(Path(directory) / 'file')])
            assert observations == [(True, Qt.NonModal, True, False, False)]
            assert tabs.currentWidget() is other
            assert not tab._file_task_active
            assert tab.local_upload_btn.isEnabled()
            assert tab.remote_download_btn.isEnabled()
        finally:
            tabs.close()
            tabs.deleteLater()


@pytest.mark.parametrize("operation", ["Uploading", "Downloading"])
def test_sftp_transfer_keeps_item_summary_when_activity_updates_arrive(app, operation):
    dialog = UploadProgressDialog(item_count=2, operation_label=operation)
    try:
        dialog.update_status('/scan/first.txt')
        assert dialog.summary_label.text() == 'Processing: /scan/first.txt'
        with patch('snakesh.ui.main_window.time.perf_counter', side_effect=[10, 11]):
            dialog.update_progress(TransferProgress('/source/first.txt', '/target/first.txt', 1, 2, 10, 50, 10, 100))
            assert dialog.summary_label.text() == 'Item 1/2: first.txt -> first.txt'
            for status in ('/source/first.txt', '', '/scan/second.txt'):
                dialog.update_status(status)
                assert dialog.summary_label.text() == 'Item 1/2: first.txt -> first.txt'
            dialog.update_progress(TransferProgress('/source/second.txt', '/target/second.txt', 2, 2, 10, 50, 60, 100))
            dialog.update_status('/source/second.txt')
            assert dialog.summary_label.text() == 'Item 2/2: second.txt -> second.txt'
            assert dialog.progress_bar.value() == 60
    finally:
        dialog.deleteLater()


def test_sftp_delete_progress_continues_showing_activity(app):
    dialog = UploadProgressDialog(item_count=2, operation_label='Deleting remote files')
    try:
        for path in ('/remote/first.txt', '/remote/second.txt'):
            dialog.update_status(path)
            assert dialog.summary_label.text() == f'Processing: {path}'
        dialog.update_status('')
        assert dialog.summary_label.text() == 'Waiting for the server...'
    finally:
        dialog.deleteLater()


def test_sftp_stalled_request_times_out_and_cancels_awaitable():
    cancelled = []
    async def stalled():
        try:
            await asyncio.sleep(60)
        finally:
            cancelled.append(True)
    async def check():
        client = _ResponsiveSFTP(SimpleNamespace(stat=stalled))
        client.IDLE_TIMEOUT = 0.01
        with pytest.raises(TimeoutError):
            await client.stat()
    asyncio.run(check())
    assert cancelled == [True]


def test_sftp_progress_extends_idle_deadline_and_cancel_interrupts_wait():
    async def put(path, *, progress_handler):
        for index in range(35):
            progress_handler(path, 'target', index, 35)
            await asyncio.sleep(0.01)
    async def stalled():
        await asyncio.sleep(60)
    async def check():
        client = _ResponsiveSFTP(SimpleNamespace(put=put))
        client.IDLE_TIMEOUT = 0.05
        await client.put('source')
        client = _ResponsiveSFTP(SimpleNamespace(stat=stalled), cancel_requested=lambda: True)
        with pytest.raises(TransferCancelledError):
            await client.stat()
    asyncio.run(check())


def test_tray_setting_roundtrips_and_disabled_default_keeps_window_visible(app):
    assert AppSettings.defaults().minimize_to_tray is False
    settings = AppSettings.defaults()
    settings.minimize_to_tray = True
    assert AppSettings.from_dict(settings.to_dict()).minimize_to_tray is True
    window = QWidget()
    with patch.object(QSystemTrayIcon, 'isSystemTrayAvailable', return_value=False):
        controller = TrayController(window, enabled=True)
        window.showMinimized()
        app.processEvents()
        assert window.isVisible()
    window.close()
    window.deleteLater()


def test_retired_nomachine_records_roundtrip_but_cannot_be_created(app):
    session = Session(name="Old record", host="example.invalid", protocol=Protocol.NOMACHINE, port=4000)
    assert Session.from_dict(session.to_dict()).protocol == Protocol.NOMACHINE
    editor = SessionEditorDialog()
    assert editor.protocol_input.findText('NOMACHINE') == -1
    editor.deleteLater()
    with pytest.raises(ValueError, match="NoMachine integration has been removed"):
        SessionEditorDialog(session=session)
    assert 'nxplayer' not in {dependency.id for dependency in required_dependencies()}


def test_macos_native_rdp_avoids_xquartz_and_preserves_stdin_credentials():
    session = Session(name="RDP", host="example.invalid", protocol=Protocol.RDP, port=3389)
    with (patch('snakesh.protocols.rdp.platform.system', return_value='Darwin'),
          patch('snakesh.protocols.rdp.resolve_executable', return_value='/opt/homebrew/bin/sdl-freerdp'),
          patch('snakesh.protocols.rdp._launch_macos_xquartz') as launch):
        command = build_rdp_command(session, password='never-on-command-line', linux_trust_certificate=True)
        environment = prepare_rdp_launch_environment(command[0])
    assert command[0].endswith('sdl-freerdp')
    assert '/from-stdin:force' in command
    assert '/cert:tofu' in command
    assert all('never-on-command-line' not in argument for argument in command)
    assert environment is not None
    launch.assert_not_called()


def test_macos_missing_xquartz_display_fails_visibly():
    with (patch('snakesh.protocols.rdp.platform.system', return_value='Darwin'),
          patch.dict(os.environ, {'DISPLAY': ''}),
          patch('snakesh.protocols.rdp._macos_launchctl_getenv', return_value=''),
          patch('snakesh.protocols.rdp._wait_for_macos_x11_display', return_value=''),
          patch('snakesh.protocols.rdp._launch_macos_xquartz')):
        with pytest.raises(Exception, match='XQuartz did not provide a display'):
            prepare_rdp_launch_environment('/opt/homebrew/bin/xfreerdp')


def test_windows_clipboard_timeout_leaves_gui_responsive(app):
    from PySide6.QtCore import QEventLoop
    from snakesh.ui import clipboard
    errors = []
    loop = QEventLoop()
    ticks = []
    timer = QTimer()
    timer.setInterval(5)
    timer.timeout.connect(lambda: ticks.append(True))
    with (patch.object(clipboard.runtime, 'self_launch_command',
                       return_value=[sys.executable, '-c', 'import time; time.sleep(60)']),
          patch.object(clipboard, '_report_error', side_effect=errors.append)):
        request = clipboard._Request(None, lambda text: pytest.fail('Timed-out paste must not deliver text'))
        directory = request.root
        request.timer.start(30)
        timer.start()
        QTimer.singleShot(200, loop.quit)
        loop.exec()
        timer.stop()
    assert ticks
    assert any('timed out' in error for error in errors)
    assert request not in clipboard._requests
    assert not directory.exists()


def test_linux_folder_launcher_drops_bundled_runtime_paths():
    from snakesh.ui.desktop_open import open_local_path
    with (patch('snakesh.runtime.is_frozen', return_value=True),
          patch('snakesh.runtime.executable_path', return_value=Path('/bundle/SnakeSh')),
          patch.dict(os.environ, {'LD_LIBRARY_PATH': '/bundle/lib:/usr/lib', 'APPDIR': '/bundle'}),
          patch('snakesh.ui.desktop_open.shutil.which', return_value='/usr/bin/xdg-open'),
          patch('snakesh.ui.desktop_open.subprocess.Popen') as launch):
        assert open_local_path('/tmp/a folder', platform_name='linux')
    assert launch.call_args.kwargs['env']['LD_LIBRARY_PATH'] == '/usr/lib'
    assert launch.call_args.args[0] == ['xdg-open', '/tmp/a folder']


def test_native_windows_folder_open_uses_shell():
    from snakesh.ui.desktop_open import open_local_path
    with patch('snakesh.ui.desktop_open.os.startfile', create=True) as start:
        assert open_local_path('/tmp/a folder', platform_name='windows')
    start.assert_called_once_with('/tmp/a folder')


def test_tray_minimize_and_restore_keep_window_reachable(app):
    window = QWidget()
    with patch.object(QSystemTrayIcon, 'isSystemTrayAvailable', return_value=True):
        controller = TrayController(window, enabled=True)
        window.showMinimized()
        app.processEvents()
        app.processEvents()
        assert not window.isVisible()
        controller.restore()
        assert window.isVisible()
        window.showMinimized()
        app.processEvents()
        controller.set_enabled(False)
        assert window.isVisible()
        assert not controller.icon.isVisible()
    window.close()
    window.deleteLater()


def test_real_loopback_sftp_transfer_progress_and_recursive_deletion():
    import asyncssh
    from snakesh.protocols.sftp_client import SFTPClient
    class Server(asyncssh.SSHServer):
        def begin_auth(self, username):
            return False
    async def check():
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            server_root = root / 'server'
            server_root.mkdir()
            source = root / 'source'
            source.mkdir()
            (source / 'a').write_bytes(b'a' * 100)
            (source / 'b').write_bytes(b'b' * 50)
            (source / 'empty').touch()
            listener = await asyncssh.listen('127.0.0.1', 0, server_factory=Server,
                server_host_keys=[asyncssh.generate_private_key('ssh-ed25519')],
                sftp_factory=lambda channel: asyncssh.SFTPServer(channel, chroot=str(server_root)))
            session = Session(name='Local test', host='127.0.0.1', port=listener.get_port(), protocol=Protocol.SFTP)
            progress = []
            try:
                with patch.object(SFTPClient, '_connect_kwargs', return_value={
                        'host': session.host, 'port': session.port, 'username': 'test',
                        'known_hosts': None, 'connect_timeout': 3}):
                    client = SFTPClient()
                    assert await client.upload_paths(session, [str(source)], '/', progress_callback=progress.append) == 1
                    assert (server_root / 'source' / 'a').read_bytes() == b'a' * 100
                    assert progress[-1].overall_bytes_transferred == 150
                    assert progress[-1].overall_bytes_total == 150
                    values = [event.overall_bytes_transferred for event in progress]
                    assert values == sorted(values)
                    destination = root / 'downloads'
                    progress.clear()
                    assert await client.download_paths(session, ['/source'], str(destination), progress_callback=progress.append) == 1
                    assert (destination / 'source' / 'b').read_bytes() == b'b' * 50
                    assert progress[-1].overall_bytes_transferred == 150
                    assert await client.delete_paths(session, ['/source']) == 1
                    assert not (server_root / 'source').exists()
            finally:
                listener.close()
                await listener.wait_closed()
    asyncio.run(check())


def test_terminal_context_menu_does_not_contact_a_stalled_clipboard(app):
    tab = TerminalTab(settings=AppSettings.defaults())
    menu = MagicMock()
    menu.exec.return_value = None
    try:
        with (patch('snakesh.ui.main_window.QMenu', return_value=menu),
              patch.object(QApplication, 'clipboard', side_effect=AssertionError('RDP clipboard blocked'))):
            tab.output._show_context_menu(None)
        menu.exec.assert_called_once()
    finally:
        tab.shutdown()
        tab.deleteLater()


def test_sixel_image_reaches_the_terminal_framebuffer(app):
    settings = AppSettings.defaults()
    settings.terminal_sixel_local_enabled = True
    tab = TerminalTab(settings=settings)
    try:
        tab.resize(640, 400)
        tab.show()
        app.processEvents()
        tab.append('\x1bPq#1;2;100;0;0!10~\x1b\\')
        tab.output._render_to_framebuffer(tab.output.viewport().rect())
        framebuffer = tab.output._ensure_framebuffer()
        content = tab.output._content_rect()
        assert framebuffer.pixelColor(content.left() + 2, content.top() + 2).getRgb() == (255, 0, 0, 255)
    finally:
        tab.shutdown()
        tab.close()
        tab.deleteLater()


def test_sftp_delete_button_and_key_use_the_highlighted_pane(app):
    with tempfile.TemporaryDirectory() as directory:
        local = Path(directory) / 'local.txt'
        local.write_text('local')
        session = Session(name='SFTP', host='example.invalid', port=22, protocol=Protocol.SFTP)
        client = SimpleNamespace(delete_paths=AsyncMock(return_value=1))
        tab = SFTPSessionTab(session=session, sftp=client, initial_remote_dir='/remote',
            initial_remote_entries=[SFTPEntry('remote.txt', '/remote/remote.txt', False, 4)],
            initial_local_dir=directory, password=None,
            execute_remote=lambda session, password, operation: (run_background_call(lambda: operation(password, False)), password),
            status_callback=lambda *_: None, should_confirm_delete=lambda: False)
        try:
            tab.show()
            app.processEvents()
            item = next(tab.local_file_tree.topLevelItem(i) for i in range(tab.local_file_tree.topLevelItemCount())
                        if tab.local_file_tree.topLevelItem(i).text(0) == 'local.txt')
            item.setSelected(True)
            tab.local_delete_btn.click()
            assert not local.exists()
            tab.remote_file_tree.topLevelItem(0).setSelected(True)
            tab.activateWindow()
            tab.remote_file_tree.setFocus()
            app.processEvents()
            with patch.object(tab, '_refresh_remote_directory'):
                QTest.keyClick(tab.remote_file_tree, Qt.Key_Delete)
            client.delete_paths.assert_awaited_once()
            assert client.delete_paths.await_args.args[1] == ['/remote/remote.txt']
        finally:
            tab.shutdown()
            tab.close()
            tab.deleteLater()
