from __future__ import annotations

import unittest

from snakesh.core.models import Protocol, Session
from snakesh.services.session_service import SessionService


class _StubStore:
    def __init__(self) -> None:
        self.sessions: list[Session] = []
        self.folders: list[str] = ["Default"]
        self.save_calls = 0

    def load_payload(self) -> tuple[list[Session], list[str]]:
        cloned_sessions = [Session.from_dict(session.to_dict()) for session in self.sessions]
        return cloned_sessions, list(self.folders)

    def save_payload(self, sessions: list[Session], folders: list[str]) -> None:
        self.save_calls += 1
        self.sessions = [Session.from_dict(session.to_dict()) for session in sessions]
        self.folders = list(folders)


def _session(session_id: str, name: str, folder: str) -> Session:
    return Session(
        id=session_id,
        name=name,
        host=f"{session_id}.example.com",
        protocol=Protocol.SSH,
        port=22,
        username="tester",
        folder=folder,
    )


class SessionServiceRenameTests(unittest.TestCase):
    def test_rename_session_updates_name(self) -> None:
        store = _StubStore()
        store.sessions = [_session("sess-1", "Old Name", "Default")]
        service = SessionService(store=store)

        changed = service.rename_session("sess-1", "New Name")

        self.assertTrue(changed)
        renamed = service.by_id("sess-1")
        self.assertIsNotNone(renamed)
        assert renamed is not None
        self.assertEqual(renamed.name, "New Name")

    def test_rename_folder_updates_nested_folders_and_sessions(self) -> None:
        store = _StubStore()
        store.sessions = [
            _session("sess-a", "A", "Broadworks/Lab"),
            _session("sess-b", "B", "Broadworks/Lab/North"),
            _session("sess-c", "C", "Default"),
        ]
        store.folders = [
            "Default",
            "Broadworks",
            "Broadworks/Lab",
            "Broadworks/Lab/North",
            "Broadworks/Other",
        ]
        service = SessionService(store=store)

        renamed_to = service.rename_folder("Broadworks/Lab", "Broadworks/Production")

        self.assertEqual(renamed_to, "Broadworks/Production")
        folders = set(service.all_folders())
        self.assertIn("Broadworks/Production", folders)
        self.assertIn("Broadworks/Production/North", folders)
        self.assertNotIn("Broadworks/Lab", folders)
        self.assertNotIn("Broadworks/Lab/North", folders)

        by_id = {session.id: session for session in service.all()}
        self.assertEqual(by_id["sess-a"].folder, "Broadworks/Production")
        self.assertEqual(by_id["sess-b"].folder, "Broadworks/Production/North")
        self.assertEqual(by_id["sess-c"].folder, "Default")

    def test_rename_default_folder_is_rejected(self) -> None:
        service = SessionService(store=_StubStore())
        with self.assertRaises(ValueError):
            service.rename_folder("Default", "Anything")

    def test_add_or_update_skips_save_when_session_is_unchanged(self) -> None:
        store = _StubStore()
        store.sessions = [_session("sess-1", "Host A", "Default")]
        service = SessionService(store=store)

        service.add_or_update(_session("sess-1", "Host A", "Default"))

        self.assertEqual(store.save_calls, 0)

    def test_add_or_update_persists_same_object_mutation(self) -> None:
        store = _StubStore()
        store.sessions = [_session("sess-1", "Host A", "Default")]
        service = SessionService(store=store)

        session = service.by_id("sess-1")
        self.assertIsNotNone(session)
        assert session is not None
        session.save_password = True

        service.add_or_update(session)

        self.assertEqual(store.save_calls, 1)
        reloaded = SessionService(store=store).by_id("sess-1")
        self.assertIsNotNone(reloaded)
        assert reloaded is not None
        self.assertTrue(reloaded.save_password)

    def test_delete_skips_save_when_session_is_missing(self) -> None:
        store = _StubStore()
        store.sessions = [_session("sess-1", "Host A", "Default")]
        service = SessionService(store=store)

        service.delete("missing-session")

        self.assertEqual(store.save_calls, 0)


class SessionServicePortDefaultsTests(unittest.TestCase):
    def test_default_port_for_nomachine(self) -> None:
        self.assertEqual(SessionService.default_port_for(Protocol.NOMACHINE), 4000)

    def test_default_port_for_telnet(self) -> None:
        self.assertEqual(SessionService.default_port_for(Protocol.TELNET), 23)

    def test_default_port_for_serial(self) -> None:
        self.assertEqual(SessionService.default_port_for(Protocol.SERIAL), 0)


if __name__ == "__main__":
    unittest.main()
