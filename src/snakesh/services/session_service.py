from __future__ import annotations

from collections.abc import Iterable
from uuid import uuid4

from snakesh.core.models import Protocol, Session
from snakesh.core.session_store import EncryptedSessionStore


class SessionService:
    def __init__(self, store: EncryptedSessionStore | None = None) -> None:
        self._store = store or EncryptedSessionStore()
        self._sessions, self._folders = self._store.load_payload()
        if not self._folders:
            self._folders = ["Default"]
        self._folders = self._normalize_folder_set(self._folders)
        self._ensure_session_folders_present()

    def all(self) -> list[Session]:
        return list(self._sessions)

    def all_folders(self) -> list[str]:
        return list(self._folders)

    def add_or_update(self, session: Session) -> None:
        session.folder = self.normalize_folder_path(session.folder)
        for idx, existing in enumerate(self._sessions):
            if existing.id == session.id:
                if existing.to_dict() == session.to_dict():
                    if existing is session:
                        self._ensure_folder_path(session.folder)
                        self._save()
                    return
                self._sessions[idx] = session
                self._ensure_folder_path(session.folder)
                self._save()
                return
        self._sessions.append(session)
        self._ensure_folder_path(session.folder)
        self._save()

    def delete(self, session_id: str) -> None:
        remaining = [s for s in self._sessions if s.id != session_id]
        if len(remaining) == len(self._sessions):
            return
        self._sessions = remaining
        self._save()

    def delete_many(self, session_ids: list[str]) -> list[str]:
        id_set = set(session_ids)
        if not id_set:
            return []
        before_ids = {s.id for s in self._sessions}
        self._sessions = [s for s in self._sessions if s.id not in id_set]
        deleted = sorted(before_ids - {s.id for s in self._sessions})
        if not deleted:
            return []
        self._save()
        return deleted

    def by_id(self, session_id: str) -> Session | None:
        for session in self._sessions:
            if session.id == session_id:
                return session
        return None

    def create_folder(self, folder_path: str) -> str:
        normalized = self.normalize_folder_path(folder_path)
        if normalized in self._folders:
            return normalized
        self._ensure_folder_path(normalized)
        self._save()
        return normalized

    def delete_folder(self, folder_path: str) -> list[str]:
        normalized = self.normalize_folder_path(folder_path)
        prefix = f"{normalized}/"
        deleted_ids = [s.id for s in self._sessions if s.folder == normalized or s.folder.startswith(prefix)]
        if deleted_ids:
            id_set = set(deleted_ids)
            self._sessions = [s for s in self._sessions if s.id not in id_set]

        self._folders = [
            folder
            for folder in self._folders
            if not (folder == normalized or folder.startswith(prefix))
        ]
        if not self._folders:
            self._folders = ["Default"]
        self._save()
        return deleted_ids

    def move_sessions(self, session_ids: list[str], target_folder: str) -> int:
        id_set = set(session_ids)
        if not id_set:
            return 0
        normalized_target = self.normalize_folder_path(target_folder)
        moved = 0
        for session in self._sessions:
            if session.id in id_set and session.folder != normalized_target:
                session.folder = normalized_target
                moved += 1
        if moved:
            self._ensure_folder_path(normalized_target)
            self._save()
        return moved

    def rename_session(self, session_id: str, new_name: str) -> bool:
        normalized_name = new_name.strip()
        if not normalized_name:
            raise ValueError("Session name cannot be empty.")
        for session in self._sessions:
            if session.id != session_id:
                continue
            if session.name == normalized_name:
                return False
            session.name = normalized_name
            self._save()
            return True
        return False

    def rename_folder(self, old_folder_path: str, new_folder_path: str) -> str:
        old_normalized = self.normalize_folder_path(old_folder_path)
        new_normalized = self.normalize_folder_path(new_folder_path)
        if old_normalized == "Default":
            raise ValueError("The Default folder cannot be renamed.")
        if old_normalized == new_normalized:
            return old_normalized
        if new_normalized.startswith(f"{old_normalized}/"):
            raise ValueError("A folder cannot be moved into its own subfolder.")
        if any(folder == new_normalized for folder in self._folders if folder != old_normalized):
            raise ValueError("A folder with this name already exists.")

        prefix = f"{old_normalized}/"
        moved_any = False
        for session in self._sessions:
            current = self.normalize_folder_path(session.folder)
            if current == old_normalized:
                session.folder = new_normalized
                moved_any = True
                continue
            if current.startswith(prefix):
                suffix = current[len(old_normalized) :]
                session.folder = f"{new_normalized}{suffix}"
                moved_any = True

        updated_folders: list[str] = []
        for folder in self._folders:
            current = self.normalize_folder_path(folder)
            if current == old_normalized:
                updated_folders.append(new_normalized)
                moved_any = True
                continue
            if current.startswith(prefix):
                suffix = current[len(old_normalized) :]
                updated_folders.append(f"{new_normalized}{suffix}")
                moved_any = True
                continue
            updated_folders.append(current)

        self._folders = self._normalize_folder_set(updated_folders)
        self._ensure_session_folders_present()
        if moved_any:
            self._save()
        return new_normalized

    def sessions_in_folder(self, folder_path: str, recursive: bool = True) -> list[Session]:
        normalized = self.normalize_folder_path(folder_path)
        if not recursive:
            return [s for s in self._sessions if s.folder == normalized]
        prefix = f"{normalized}/"
        return [s for s in self._sessions if s.folder == normalized or s.folder.startswith(prefix)]

    def replace_all(self, sessions: list[Session], folders: list[str] | None = None) -> None:
        cloned_sessions: list[Session] = []
        for session in sessions:
            cloned = Session.from_dict(session.to_dict())
            cloned.folder = self.normalize_folder_path(cloned.folder)
            cloned_sessions.append(cloned)
        self._sessions = cloned_sessions
        base_folders = folders if folders is not None else [session.folder for session in cloned_sessions]
        self._folders = self._normalize_folder_set(base_folders)
        self._ensure_session_folders_present()
        self._save()

    def merge_sessions_no_overwrite(self, sessions: list[Session], folders: list[str] | None = None) -> int:
        existing_ids = {session.id for session in self._sessions}
        added = 0
        for session in sessions:
            cloned = Session.from_dict(session.to_dict())
            cloned.folder = self.normalize_folder_path(cloned.folder)
            while cloned.id in existing_ids:
                cloned.id = str(uuid4())
            existing_ids.add(cloned.id)
            self._sessions.append(cloned)
            self._ensure_folder_path(cloned.folder)
            added += 1
        if folders:
            for folder in folders:
                self._ensure_folder_path(folder)
        if added:
            self._save()
        return added

    @staticmethod
    def default_port_for(protocol: Protocol) -> int:
        if protocol in (Protocol.SSH, Protocol.SFTP):
            return 22
        if protocol == Protocol.TELNET:
            return 23
        if protocol == Protocol.SERIAL:
            return 0
        if protocol == Protocol.VNC:
            return 5900
        if protocol == Protocol.NOMACHINE:
            return 4000
        return 3389

    @staticmethod
    def folder_names(sessions: Iterable[Session]) -> list[str]:
        return sorted({s.folder for s in sessions if s.folder})

    @staticmethod
    def normalize_folder_path(folder_path: str) -> str:
        cleaned = folder_path.replace("\\", "/").strip("/")
        parts = [part.strip() for part in cleaned.split("/") if part.strip()]
        if not parts:
            return "Default"
        return "/".join(parts)

    def _save(self) -> None:
        self._store.save_payload(self._sessions, self._folders)

    def _ensure_folder_path(self, folder_path: str) -> None:
        normalized = self.normalize_folder_path(folder_path)
        parts = normalized.split("/")
        current: list[str] = []
        for part in parts:
            current.append(part)
            joined = "/".join(current)
            if joined not in self._folders:
                self._folders.append(joined)
        self._folders = self._normalize_folder_set(self._folders)

    def _ensure_session_folders_present(self) -> None:
        for session in self._sessions:
            session.folder = self.normalize_folder_path(session.folder)
            self._ensure_folder_path(session.folder)
        self._folders = self._normalize_folder_set(self._folders)

    def _normalize_folder_set(self, folders: list[str]) -> list[str]:
        normalized = {self.normalize_folder_path(folder) for folder in folders if folder.strip()}
        normalized.add("Default")
        return sorted(normalized, key=lambda item: (item.count("/"), item.lower()))
