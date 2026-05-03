from __future__ import annotations

import json
from pathlib import Path

from snakesh.core.paths import data_dir
from snakesh.core.models import Session
from snakesh.core.security import build_cipher


class EncryptedSessionStore:
    def __init__(self, store_dir: Path | None = None) -> None:
        self._data_dir = store_dir or data_dir()
        self._path = self._data_dir / "sessions.enc"
        self._cipher = build_cipher()
        self._last_saved_payload: str | None = None

    def load(self) -> list[Session]:
        sessions, _folders = self.load_payload()
        return sessions

    def load_payload(self) -> tuple[list[Session], list[str]]:
        if not self._path.exists():
            self._last_saved_payload = None
            return [], []
        encrypted = self._path.read_bytes()
        decrypted = self._cipher.decrypt(encrypted).decode("utf-8")
        self._last_saved_payload = decrypted
        raw = json.loads(decrypted)
        if isinstance(raw, list):
            sessions = [Session.from_dict(item) for item in raw]
            folders = sorted({s.folder for s in sessions if s.folder})
            return sessions, folders
        if isinstance(raw, dict):
            raw_sessions = raw.get("sessions", [])
            raw_folders = raw.get("folders", [])
            sessions = [Session.from_dict(item) for item in raw_sessions if isinstance(item, dict)]
            folders = [str(folder) for folder in raw_folders if isinstance(folder, str) and folder.strip()]
            if not folders:
                folders = sorted({s.folder for s in sessions if s.folder})
            return sessions, sorted(set(folders))
        return [], []

    def save(self, sessions: list[Session]) -> None:
        self.save_payload(sessions, sorted({s.folder for s in sessions if s.folder}))

    def save_payload(self, sessions: list[Session], folders: list[str]) -> None:
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
        self._path.write_bytes(encrypted)
        self._last_saved_payload = payload
