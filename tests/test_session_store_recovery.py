import json
import os
from pathlib import Path
from unittest.mock import Mock, patch
from contextlib import ExitStack

import pytest
from cryptography.fernet import Fernet
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from snakesh import app as app_module
from snakesh.core.models import Protocol, Session
from snakesh.core.security import SecurityError
from snakesh.core.session_store import (
    EncryptedSessionStore, SessionStoreError, archive_unreadable_store, restore_archived_store,
)
from snakesh.services.session_service import SessionService
from snakesh.services.settings_service import AppSettings


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def store_at(root, cipher):
    with patch("snakesh.core.session_store.build_cipher", return_value=cipher):
        return EncryptedSessionStore(root)


def test_wrong_key_produces_controlled_error_and_blocks_overwriting(tmp_path):
    path = tmp_path / "sessions.enc"
    original = Fernet(Fernet.generate_key()).encrypt(b'{"sessions": [], "folders": []}')
    path.write_bytes(original)
    store = store_at(tmp_path, Fernet(Fernet.generate_key()))
    with pytest.raises(SessionStoreError) as raised:
        store.load_payload()
    assert raised.value.path == path
    with pytest.raises(SessionStoreError, match="cannot be overwritten"):
        store.save_payload([], ["Default"])
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("decrypted", [b"not json", b"\xff", b"null", b'{"sessions": "bad"}', b"[null]", b'{"sessions": [false]}'])
def test_authenticated_but_invalid_payload_is_not_silently_reset(tmp_path, decrypted):
    cipher = Fernet(Fernet.generate_key())
    path = tmp_path / "sessions.enc"
    original = cipher.encrypt(decrypted)
    path.write_bytes(original)
    store = store_at(tmp_path, cipher)
    with pytest.raises(SessionStoreError, match="invalid session data"):
        store.load_payload()
    assert path.read_bytes() == original


def test_key_backend_failure_is_wrapped_for_startup_recovery(tmp_path):
    with patch("snakesh.core.session_store.build_cipher", side_effect=SecurityError("backend unavailable")):
        with pytest.raises(SessionStoreError) as raised:
            EncryptedSessionStore(tmp_path)
    assert raised.value.path == tmp_path / "sessions.enc"


def test_save_is_atomic_and_can_reload_current_and_legacy_payloads(tmp_path):
    cipher = Fernet(Fernet.generate_key())
    store = store_at(tmp_path, cipher)
    assert store.load_payload() == ([], [])
    session = Session(name="Saved server", host="example.test", protocol=Protocol.SSH, port=22)
    store.save_payload([session], ["Default", "Lab"])
    sessions, folders = store_at(tmp_path, cipher).load_payload()
    assert sessions[0].to_dict() == session.to_dict()
    assert folders == ["Default", "Lab"]
    path = tmp_path / "sessions.enc"
    path.write_bytes(cipher.encrypt(json.dumps([session.to_dict()]).encode()))
    assert store_at(tmp_path, cipher).load_payload()[0][0].id == session.id
    assert not list(tmp_path.glob(".sessions-*"))


def test_interrupted_save_preserves_the_previous_encrypted_file(tmp_path):
    cipher = Fernet(Fernet.generate_key())
    store = store_at(tmp_path, cipher)
    store.save_payload([], ["Default"])
    path = tmp_path / "sessions.enc"
    original = path.read_bytes()
    with patch("snakesh.core.session_store.os.replace", side_effect=OSError("disk failure")):
        with pytest.raises(OSError, match="disk failure"):
            store.save_payload([], ["Default", "New"])
    assert path.read_bytes() == original
    assert not list(tmp_path.glob(".sessions-*"))


def test_archive_preserves_exact_bytes_and_restore_never_overwrites(tmp_path):
    path = tmp_path / "sessions.enc"
    path.write_bytes(b"unreadable encrypted data")
    backup = archive_unreadable_store(path)
    assert not path.exists()
    assert backup.read_bytes() == b"unreadable encrypted data"
    if os.name != "nt":
        assert backup.stat().st_mode & 0o777 == 0o600
    restore_archived_store(backup, path)
    assert path.read_bytes() == backup.read_bytes()
    with pytest.raises(FileExistsError):
        restore_archived_store(backup, path)
    assert backup.exists()


def test_failed_backup_never_removes_original_sessions(tmp_path):
    path = tmp_path / "sessions.enc"
    path.write_bytes(b"original ciphertext")
    with patch("snakesh.core.session_store.os.fsync", side_effect=OSError("disk full")):
        with pytest.raises(SessionStoreError, match="not reset"):
            archive_unreadable_store(path)
    assert path.read_bytes() == b"original ciphertext"
    assert list(tmp_path.iterdir()) == [path]


def test_recovery_close_does_not_modify_any_files(tmp_path):
    path = tmp_path / "sessions.enc"
    path.write_bytes(b"original ciphertext")
    with (
        patch.object(app_module, "SessionService", side_effect=SessionStoreError(path, "unreadable")),
        patch.object(app_module, "_prompt_session_store_recovery", return_value="close"),
    ):
        assert app_module._load_session_service() is None
    assert path.read_bytes() == b"original ciphertext"
    assert list(tmp_path.iterdir()) == [path]


def test_retry_can_restore_key_access_without_archiving_or_resetting(tmp_path):
    path = tmp_path / "sessions.enc"
    path.write_bytes(b"original ciphertext")
    recovered = Mock()
    with (
        patch.object(app_module, "SessionService", side_effect=[SessionStoreError(path, "locked keychain"), recovered]),
        patch.object(app_module, "_prompt_session_store_recovery", return_value="retry"),
    ):
        assert app_module._load_session_service() is recovered
    assert list(tmp_path.iterdir()) == [path]
    assert path.read_bytes() == b"original ciphertext"


def test_explicit_fresh_workspace_backs_up_original_before_saving_new_sessions(tmp_path):
    path = tmp_path / "sessions.enc"
    original = Fernet(Fernet.generate_key()).encrypt(b'{"sessions": []}')
    path.write_bytes(original)
    cipher = Fernet(Fernet.generate_key())
    def create_service():
        return SessionService(store_at(tmp_path, cipher))
    with (
        patch.object(app_module, "SessionService", side_effect=create_service),
        patch.object(app_module, "_prompt_session_store_recovery", return_value="fresh"),
        patch.object(QMessageBox, "information") as notice,
    ):
        service = app_module._load_session_service()
    assert service.all() == []
    backup, = tmp_path.glob("sessions.enc.unreadable-*")
    assert backup.read_bytes() == original
    assert not path.exists()
    service.add_or_update(Session(name="New server", host="new.example.test", protocol=Protocol.SSH, port=22))
    assert len(store_at(tmp_path, cipher).load()) == 1
    assert backup.read_bytes() == original
    assert str(backup) in notice.call_args.args[2]


def test_failed_fresh_workspace_restores_original_and_retains_backup(tmp_path):
    path = tmp_path / "sessions.enc"
    path.write_bytes(b"original ciphertext")
    with (
        patch.object(app_module, "SessionService", side_effect=SessionStoreError(path, "keychain unavailable")),
        patch.object(app_module, "_prompt_session_store_recovery", side_effect=["fresh", "close"]),
        patch.object(QMessageBox, "warning"),
    ):
        assert app_module._load_session_service() is None
    backup, = tmp_path.glob("sessions.enc.unreadable-*")
    assert path.read_bytes() == backup.read_bytes() == b"original ciphertext"


def test_failed_fresh_workspace_preserves_a_concurrently_created_file(tmp_path):
    path = tmp_path / "sessions.enc"
    path.write_bytes(b"original ciphertext")
    calls = []
    def fail_service():
        if calls:
            path.write_bytes(b"concurrent ciphertext")
        calls.append(True)
        raise SessionStoreError(path, "backend unavailable")
    with (
        patch.object(app_module, "SessionService", side_effect=fail_service),
        patch.object(app_module, "_prompt_session_store_recovery", return_value="fresh"),
        patch.object(QMessageBox, "critical") as notice,
    ):
        assert app_module._load_session_service() is None
    backup, = tmp_path.glob("sessions.enc.unreadable-*")
    assert path.read_bytes() == b"concurrent ciphertext"
    assert backup.read_bytes() == b"original ciphertext"
    assert str(backup) in notice.call_args.args[2]


def test_changed_session_file_is_not_removed_during_backup(tmp_path):
    path = tmp_path / "sessions.enc"
    path.write_bytes(b"original ciphertext")
    def copy_and_change(destination, payload):
        destination.write_bytes(payload)
        path.write_bytes(b"updated ciphertext")
    with patch("snakesh.core.session_store._write_private_copy", side_effect=copy_and_change):
        with pytest.raises(SessionStoreError, match="changed during backup"):
            archive_unreadable_store(path)
    assert path.read_bytes() == b"updated ciphertext"
    backup, = tmp_path.glob("sessions.enc.unreadable-*")
    assert backup.read_bytes() == b"original ciphertext"


def test_startup_recovery_close_exits_normally_and_releases_the_instance_lease(qapp, tmp_path):
    fake_app = Mock()
    lease = Mock()
    icon = Mock()
    icon.isNull.return_value = True
    settings = Mock()
    settings.load.return_value = AppSettings.defaults()
    with ExitStack() as stack:
        replacements = {
            "SettingsService": Mock(return_value=settings),
            "QApplication": Mock(return_value=fake_app),
            "claim_main_instance": Mock(return_value=Mock(activated_existing=False, lease=lease)),
            "_load_app_icon": Mock(return_value=icon),
            "_unlock_with_master_password": Mock(return_value=True),
            "_maybe_prompt_for_safe_mode": Mock(return_value=False),
            "SessionService": Mock(side_effect=SessionStoreError(tmp_path / "sessions.enc", "wrong key")),
            "_prompt_session_store_recovery": Mock(return_value="close"),
            "MainWindow": Mock(),
        }
        for name in ("_start_debug_session", "_stop_debug_session", "_maybe_initialize_fault_handler",
                     "apply_theme", "_record_run_marker", "_register_run_marker_cleanup", "known_hosts_path"):
            replacements[name] = Mock(return_value=None)
        for name, replacement in replacements.items():
            stack.enter_context(patch.object(app_module, name, replacement))
        assert app_module.main() == 0
    replacements["MainWindow"].assert_not_called()
    fake_app.exec.assert_not_called()
    lease.release.assert_called_once()


@pytest.mark.parametrize("inspect_error", [None, PermissionError("Session directory unavailable")])
def test_recovery_dialog_defaults_to_close_and_escape(qapp, tmp_path, inspect_error):
    observed = []
    def close_dialog():
        for widget in QApplication.topLevelWidgets():
            if isinstance(widget, QMessageBox) and widget.windowTitle() == "Saved Sessions Unavailable":
                observed.append((widget.defaultButton().text(), widget.escapeButton().text(), widget.informativeText()))
                widget.reject()
                return
        QTimer.singleShot(5, close_dialog)
    QTimer.singleShot(0, close_dialog)
    with patch.object(Path, "is_file", side_effect=inspect_error, return_value=False):
        assert app_module._prompt_session_store_recovery(SessionStoreError(tmp_path / "sessions.enc", "key unavailable")) == "close"
    assert observed[0][:2] == ("Close", "Close")
    assert str(tmp_path / "sessions.enc") in observed[0][2]
