from __future__ import annotations

import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet

from snakesh.core import security
from snakesh.services.settings_service import AppSettings


class _StubSettingsService:
    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings

    def load(self) -> AppSettings:
        return self._settings


class _StubSecretsService:
    def __init__(self, keys_by_service: dict[str, str]) -> None:
        self._keys_by_service = keys_by_service
        self.set_calls: list[tuple[str, str, str, AppSettings | None]] = []

    def get_secret(
        self,
        namespace: str,
        key: str,
        *,
        settings: AppSettings | None = None,
    ) -> str | None:
        return self._keys_by_service.get(namespace)

    def set_secret(
        self,
        namespace: str,
        key: str,
        value: str,
        *,
        settings: AppSettings | None = None,
    ) -> None:
        self.set_calls.append((namespace, key, value, settings))


class SecurityKeySelectionTests(unittest.TestCase):
    def test_get_or_create_key_prefers_key_that_decrypts_session_store(self) -> None:
        settings = AppSettings.defaults()
        settings.secrets_backend = "keyring"
        settings_service = _StubSettingsService(settings)

        active_key = Fernet.generate_key().decode("ascii")
        legacy_key = Fernet.generate_key().decode("ascii")
        encrypted_payload = Fernet(legacy_key.encode("ascii")).encrypt(b'{"sessions": [], "folders": []}')
        secrets_service = _StubSecretsService(
            {
                "SnakeSh": active_key,
                "SecurePython": legacy_key,
            }
        )

        with (
            patch("snakesh.core.security.SettingsService", return_value=settings_service),
            patch("snakesh.core.security.SecretsService", return_value=secrets_service),
            patch("snakesh.core.security._session_store_payload", return_value=encrypted_payload),
        ):
            selected_key = security.get_or_create_key()

        self.assertEqual(selected_key, legacy_key.encode("ascii"))
        self.assertEqual(len(secrets_service.set_calls), 1)
        namespace, account, value, call_settings = secrets_service.set_calls[0]
        self.assertEqual(namespace, "SnakeSh")
        self.assertEqual(account, "session_store_master_key")
        self.assertEqual(value, legacy_key)
        self.assertIsNotNone(call_settings)


if __name__ == "__main__":
    unittest.main()
