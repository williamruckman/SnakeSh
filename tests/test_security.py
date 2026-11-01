from __future__ import annotations

import unittest
from pathlib import Path
import tempfile
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
    def _select_key(self, keys, payload, *, read_error=None):
        settings = AppSettings.defaults()
        service = _StubSecretsService(keys)
        with (
            patch("snakesh.core.security.SettingsService", return_value=_StubSettingsService(settings)),
            patch("snakesh.core.security.SecretsService", return_value=service),
            patch("snakesh.core.security._session_store_payload", return_value=payload),
        ):
            if read_error is not None:
                with patch.object(service, "get_secret", side_effect=read_error):
                    with self.assertRaises(security.SecurityError):
                        security.get_or_create_key()
            else:
                with self.assertRaises(security.SecurityError):
                    security.get_or_create_key()
        self.assertEqual(service.set_calls, [])

    def test_missing_key_never_generates_replacement_for_existing_sessions(self) -> None:
        payload = Fernet(Fernet.generate_key()).encrypt(b'{"sessions": []}')
        self._select_key({}, payload)

    def test_mismatched_keys_are_not_migrated_over_the_active_key(self) -> None:
        payload = Fernet(Fernet.generate_key()).encrypt(b'{"sessions": []}')
        self._select_key({"SecurePython": Fernet.generate_key().decode("ascii")}, payload)

    def test_empty_existing_store_does_not_trigger_new_key_generation(self) -> None:
        self._select_key({}, b"")

    def test_unavailable_backend_does_not_trigger_key_generation_even_without_a_store(self) -> None:
        self._select_key({}, None, read_error=RuntimeError("Keychain is locked"))

    def test_new_install_can_still_create_a_persisted_key(self) -> None:
        settings = AppSettings.defaults()
        service = _StubSecretsService({})
        with (
            patch("snakesh.core.security.SettingsService", return_value=_StubSettingsService(settings)),
            patch("snakesh.core.security.SecretsService", return_value=service),
            patch("snakesh.core.security._session_store_payload", return_value=None),
        ):
            key = security.get_or_create_key()
        self.assertEqual(len(service.set_calls), 1)
        self.assertEqual(Fernet(key).decrypt(Fernet(key).encrypt(b"new install")), b"new install")

    def test_explicit_store_directory_selects_its_key_instead_of_the_default_store(self) -> None:
        settings = AppSettings.defaults()
        active = Fernet.generate_key()
        legacy = Fernet.generate_key()
        service = _StubSecretsService({"SnakeSh": active.decode(), "SecurePython": legacy.decode()})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sessions.enc"
            payload = Fernet(legacy).encrypt(b'{"sessions": []}')
            path.write_bytes(payload)
            with (
                patch("snakesh.core.security.SettingsService", return_value=_StubSettingsService(settings)),
                patch("snakesh.core.security.SecretsService", return_value=service),
                patch("snakesh.core.security.data_dir", side_effect=AssertionError("Wrong data directory")),
            ):
                self.assertEqual(security.get_or_create_key(store_path=path), legacy)
            self.assertEqual(path.read_bytes(), payload)

    def test_unreadable_store_does_not_allow_key_creation(self) -> None:
        with patch.object(Path, "read_bytes", side_effect=PermissionError("denied")):
            with self.assertRaises(security.SecurityError):
                security.get_or_create_key(store_path=Path("/unused/sessions.enc"))

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
