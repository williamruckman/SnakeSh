from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from uuid import uuid4

from cryptography.fernet import InvalidToken

from snakesh.core.paths import data_dir
from snakesh.core.models import Session
from snakesh.core.security import SecurityError, build_cipher


class SessionStoreError(RuntimeError):
    def __init__(self, path: Path, reason: str) -> None:
        self.path = path
        super().__init__(reason)


def _write_private_copy(path: Path, payload: bytes) -> None:
    # Exclusive creation prevents recovery from overwriting another file.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def archive_unreadable_store(path: Path) -> Path:
    """Preserve encrypted bytes before an explicitly requested fresh workspace."""
    backup = path.with_name(f"{path.name}.unreadable-{uuid4().hex}")
    try:
        payload = path.read_bytes()
        _write_private_copy(backup, payload)
        if path.read_bytes() != payload:
            raise SessionStoreError(path, "Saved sessions changed during backup. Retry loading them.")
        path.unlink()
    except OSError as exc:
        raise SessionStoreError(path, "Unable to preserve the saved session file. It was not reset.") from exc
    return backup


def restore_archived_store(backup: Path, path: Path) -> None:
    # Keep the backup even after restoration. Never overwrite concurrent data.
    _write_private_copy(path, backup.read_bytes())


class EncryptedSessionStore:
    def __init__(self, store_dir: Path | None = None) -> None:
        self._data_dir = store_dir or data_dir()
        self._path = self._data_dir / "sessions.enc"
        try:
            self._cipher = build_cipher(store_path=self._path)
        except (SecurityError, ValueError) as exc:
            raise SessionStoreError(
                self._path,
                "The saved sessions could not be unlocked. The encryption key may be "
                "unavailable or the file may be damaged. Unlock your secrets backend "
                "or restore its configuration, then retry.",
            ) from exc
        self._last_saved_payload: str | None = None
        self._load_failed = False

    def load(self) -> list[Session]:
        sessions, _folders = self.load_payload()
        return sessions

    def load_payload(self) -> tuple[list[Session], list[str]]:
        self._load_failed = True
        try:
            encrypted = self._path.read_bytes()
        except FileNotFoundError:
            self._last_saved_payload = None
            self._load_failed = False
            return [], []
        except OSError as exc:
            raise SessionStoreError(self._path, "The saved session file could not be read.") from exc
        try:
            decrypted = self._cipher.decrypt(encrypted).decode("utf-8")
            raw = json.loads(decrypted)
            sessions, folders = self._parse_payload(raw)
        except InvalidToken as exc:
            raise SessionStoreError(
                self._path, "The saved session file cannot be decrypted with the available key."
            ) from exc
        except (UnicodeError, ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
            raise SessionStoreError(self._path, "The saved session file contains invalid session data.") from exc
        self._last_saved_payload = decrypted
        self._load_failed = False
        return sessions, folders

    @staticmethod
    def _parse_payload(raw: object) -> tuple[list[Session], list[str]]:
        if isinstance(raw, list):
            if not all(isinstance(item, dict) for item in raw):
                raise ValueError("Invalid legacy session list")
            sessions = [Session.from_dict(item) for item in raw]
            folders = sorted({s.folder for s in sessions if s.folder})
            return sessions, folders
        if isinstance(raw, dict):
            raw_sessions = raw.get("sessions", [])
            raw_folders = raw.get("folders", [])
            if not isinstance(raw_sessions, list) or not isinstance(raw_folders, list):
                raise ValueError("Invalid session or folder list")
            if not all(isinstance(item, dict) for item in raw_sessions):
                raise ValueError("Invalid session record")
            sessions = [Session.from_dict(item) for item in raw_sessions]
            folders = [str(folder) for folder in raw_folders if isinstance(folder, str) and folder.strip()]
            if not folders:
                folders = sorted({s.folder for s in sessions if s.folder})
            return sessions, sorted(set(folders))
        raise ValueError("Invalid session payload")

    def save(self, sessions: list[Session]) -> None:
        self.save_payload(sessions, sorted({s.folder for s in sessions if s.folder}))

    def save_payload(self, sessions: list[Session], folders: list[str]) -> None:
        if self._load_failed:
            raise SessionStoreError(self._path, "Unreadable saved sessions cannot be overwritten.")
        self._data_dir.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {
                "sessions": [s.to_dict() for s in sessions],
                "folders": sorted(set(folder for folder in folders if folder.strip())),
            },
            indent=2,
        )
        if payload == self._last_saved_payload:
            return
        encrypted = self._cipher.encrypt(payload.encode("utf-8"))
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=self._data_dir, prefix=".sessions-", delete=False) as output:
                temporary_path = Path(output.name)
                output.write(encrypted)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary_path, self._path)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
        self._last_saved_payload = payload
