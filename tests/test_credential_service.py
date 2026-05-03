from __future__ import annotations

import unittest
from unittest.mock import patch

from snakesh.core.models import Protocol, Session
from snakesh.services.credential_service import CredentialService, SERVICE
from snakesh.services.settings_service import AppSettings


class _StubSettingsService:
    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings

    def load(self) -> AppSettings:
        return self._settings


def _build_session() -> Session:
    return Session(
        id="sess-credential-test",
        name="Credential Test",
        host="ssh.example.com",
        protocol=Protocol.SSH,
        port=22,
        username="tester",
    )


class CredentialServiceTests(unittest.TestCase):
    def _build_service(self) -> tuple[CredentialService, AppSettings]:
        settings = AppSettings.defaults()
        service = CredentialService(settings_service=_StubSettingsService(settings))
        return service, settings

    def test_save_password_noops_when_password_is_empty(self) -> None:
        service, _settings = self._build_service()
        session = _build_session()

        with patch.object(service._secrets_service, "set_secret") as mock_set_secret:
            saved, error_message = service.save_password(session, "")

        self.assertTrue(saved)
        self.assertIsNone(error_message)
        mock_set_secret.assert_not_called()

    def test_save_password_returns_success_when_backend_write_succeeds(self) -> None:
        service, settings = self._build_service()
        session = _build_session()

        with patch.object(service._secrets_service, "set_secret") as mock_set_secret:
            saved, error_message = service.save_password(session, "new-secret")

        self.assertTrue(saved)
        self.assertIsNone(error_message)
        mock_set_secret.assert_called_once_with(
            SERVICE,
            "session_password:sess-credential-test",
            "new-secret",
            settings=settings,
        )

    def test_save_password_returns_backend_error_message(self) -> None:
        service, _settings = self._build_service()
        session = _build_session()

        with patch.object(service._secrets_service, "set_secret", side_effect=RuntimeError("backend broke")):
            saved, error_message = service.save_password(session, "new-secret")

        self.assertFalse(saved)
        self.assertEqual(error_message, "backend broke")


if __name__ == "__main__":
    unittest.main()
