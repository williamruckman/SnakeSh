from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox

from snakesh.core.models import Protocol, Session
from snakesh.protocols.sftp_client import SFTPEntry
from snakesh.ui.main_window import SFTPSessionTab
from snakesh.ui.sftp_remote_tree import SFTPRemoteTree


@pytest.fixture(scope="module")
def app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


@pytest.fixture
def tab(app, tmp_path):
    window = QMainWindow()
    sftp = SimpleNamespace(move_paths=AsyncMock(return_value=2))
    session = Session(id="remote-move", name="Remote Move", host="example.test", protocol=Protocol.SFTP, port=22)
    widget = SFTPSessionTab(
        session=session, sftp=sftp, initial_remote_dir="/remote",
        initial_remote_entries=[SFTPEntry("a.txt", "/remote/a.txt", False, 10),
                                SFTPEntry("b.txt", "/remote/b.txt", False, 20),
                                SFTPEntry("folder", "/remote/folder", True, 0)],
        initial_local_dir=str(tmp_path), password=None,
        execute_remote=lambda _session, password, operation: (operation(password, False), password),
        status_callback=Mock(), should_confirm_delete=lambda: False,
        parent=window,
    )
    window.setCentralWidget(widget)
    window.resize(1100, 750)
    window.show()
    app.processEvents()
    yield widget
    widget.shutdown()
    window.close()
    window.deleteLater()
    app.processEvents()


def drop_event(tab, tree=None, target="/remote/folder", source=None, mime=None):
    tree = tree or tab.remote_file_tree
    row = next(i for i in range(tree.topLevelItemCount())
               if tree.topLevelItem(i).data(0, tab.REMOTE_PATH_ROLE) == target)
    point = tree.visualItemRect(tree.topLevelItem(row)).center()
    if mime is None:
        mime = QMimeData()
        mime.setData(SFTPRemoteTree.MOVE_MIME, b"1")
    return SimpleNamespace(
        source=lambda: source or tab.remote_file_tree,
        position=lambda: QPointF(point), mimeData=lambda: mime,
        setDropAction=Mock(), accept=Mock(), ignore=Mock(), acceptProposedAction=Mock(),
    )


@pytest.mark.parametrize("navigation", [False, True])
def test_confirmed_drop_moves_snapshot_and_refreshes_without_qt_reparenting(tab, navigation):
    tab.remote_file_tree._drag_paths = ["/remote/a.txt", "/remote/b.txt"]
    tree = tab.remote_nav_tree if navigation else tab.remote_file_tree
    event = drop_event(tab, tree)
    with (
        patch("snakesh.ui.main_window.QMessageBox.question", return_value=QMessageBox.Yes) as confirm,
        patch.object(tab, "_refresh_remote_directory") as refresh,
    ):
        tree.dropEvent(event)
    confirm.assert_called_once()
    assert confirm.call_args.args[-1] == QMessageBox.No
    tab._sftp.move_paths.assert_awaited_once()
    assert tab._sftp.move_paths.await_args.args[1:] == (["/remote/a.txt", "/remote/b.txt"], "/remote/folder")
    refresh.assert_called_once()
    event.accept.assert_called_once()
    event.setDropAction.assert_called_with(Qt.MoveAction)
    assert tab.remote_file_tree.topLevelItemCount() == 3
    assert not tab._file_task_active
    assert tab.remote_file_tree.isEnabled()


def test_declining_move_keeps_items_and_makes_no_server_request(tab):
    tab.remote_file_tree._drag_paths = ["/remote/a.txt"]
    event = drop_event(tab)
    with (
        patch("snakesh.ui.main_window.QMessageBox.question", return_value=QMessageBox.No),
        patch.object(tab, "_refresh_remote_directory") as refresh,
    ):
        tab.remote_file_tree.dropEvent(event)
    tab._sftp.move_paths.assert_not_called()
    refresh.assert_not_called()
    event.ignore.assert_called_once()


@pytest.mark.parametrize("invalid", ["file", "self", "busy", "loading", "other-server"])
def test_invalid_targets_and_foreign_drags_do_not_prompt_or_move(tab, invalid):
    tab.remote_file_tree._drag_paths = ["/remote/a.txt"]
    event = drop_event(tab, target="/remote/a.txt" if invalid == "file" else "/remote/folder")
    if invalid == "self":
        tab.remote_file_tree._drag_paths = ["/remote/folder"]
    if invalid == "busy":
        tab._file_task_active = True
    if invalid == "loading":
        tab._remote_load_worker = Mock()
    if invalid == "other-server":
        event.source = lambda: object()
    try:
        with patch("snakesh.ui.main_window.QMessageBox.question") as question:
            tab.remote_file_tree.dropEvent(event)
        question.assert_not_called()
        tab._sftp.move_paths.assert_not_called()
        event.ignore.assert_called_once()
    finally:
        tab._file_task_active = False
        tab._remote_load_worker = None


def test_error_refreshes_view_and_restores_controls(tab):
    tab._sftp.move_paths.side_effect = FileExistsError("destination exists")
    tab.remote_file_tree._drag_paths = ["/remote/a.txt"]
    event = drop_event(tab)
    with (
        patch("snakesh.ui.main_window.QMessageBox.question", return_value=QMessageBox.Yes),
        patch("snakesh.ui.main_window.QMessageBox.warning") as warning,
        patch.object(tab, "_refresh_remote_directory") as refresh,
    ):
        tab.remote_file_tree.dropEvent(event)
    refresh.assert_called_once()
    warning.assert_called_once()
    assert "destination exists" in warning.call_args.args[2]
    assert not tab._file_task_active and tab.remote_file_tree.isEnabled()


def test_external_file_drop_retains_upload_behavior(tab, tmp_path):
    path = tmp_path / "upload.txt"
    path.write_text("payload")
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path))])
    event = drop_event(tab, mime=mime)
    with patch.object(tab, "_upload_local_paths") as upload:
        tab.remote_file_tree.dropEvent(event)
    upload.assert_called_once_with([str(path)])
    tab._sftp.move_paths.assert_not_called()


def test_start_drag_uses_selection_snapshot_and_only_move_action(tab):
    tree = tab.remote_file_tree
    item = next(tree.topLevelItem(i) for i in range(tree.topLevelItemCount())
                if tree.topLevelItem(i).data(0, tab.REMOTE_PATH_ROLE) == "/remote/a.txt")
    item.setSelected(True)
    def run_drag(action):
        assert action == Qt.MoveAction
        assert tree._drag_paths == ["/remote/a.txt"]
    drag = Mock()
    drag.exec.side_effect = run_drag
    with patch("snakesh.ui.sftp_remote_tree.QDrag", return_value=drag):
        tree.startDrag(Qt.CopyAction | Qt.MoveAction)
    assert tree._drag_paths == []
    assert drag.setMimeData.call_args.args[0].hasFormat(SFTPRemoteTree.MOVE_MIME)
