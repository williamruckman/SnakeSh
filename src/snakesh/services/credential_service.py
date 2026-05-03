from __future__ import annotations

from snakesh.core.models import Session
from snakesh.services.secrets_service import SecretsService
from snakesh.services.settings_service import AppSettings, SettingsService

SERVICE = "SnakeSh"
LEGACY_SERVICE = "snakesh"
PASSWORD_PREFIX = "session_password:"


class CredentialService:
    def __init__(self, settings_service: SettingsService | None = None) -> None:
        self._settings_service = settings_service or SettingsService()
        self._secrets_service = SecretsService(settings_service=self._settings_service)

    @staticmethod
    def _account_for(session: Session) -> str:
        return f"{PASSWORD_PREFIX}{session.id}"

    @staticmethod
    def _legacy_settings() -> AppSettings:
        defaults = AppSettings.defaults()
        defaults.secrets_backend = "keyring"
        return defaults

    def save_password(self, session: Session, password: str) -> tuple[bool, str | None]:
        if not password:
            return True, None
        account = self._account_for(session)
        settings = self._settings_service.load()
        try:
            self._secrets_service.set_secret(SERVICE, account, password, settings=settings)
        except Exception as exc:
            # Avoid breaking interactive auth flows when backend tooling is unavailable.
            return False, str(exc) or "Unable to save password in the configured secrets backend."
        return True, None

    def load_password(self, session: Session) -> str | None:
        account = self._account_for(session)
        settings = self._settings_service.load()
        candidates: list[tuple[str, AppSettings]] = [(SERVICE, settings)]
        if settings.secrets_backend != "keyring":
            candidates.append((SERVICE, self._legacy_settings()))
        candidates.append((LEGACY_SERVICE, settings))
        if settings.secrets_backend != "keyring":
            candidates.append((LEGACY_SERVICE, self._legacy_settings()))

        seen: set[tuple[str, str]] = set()
        for service_name, candidate_settings in candidates:
            marker = (service_name, candidate_settings.secrets_backend)
            if marker in seen:
                continue
            seen.add(marker)
            try:
                password = self._secrets_service.get_secret(
                    service_name,
                    account,
                    settings=candidate_settings,
                )
            except Exception:
                password = None
            if password is None:
                continue
            if service_name != SERVICE or candidate_settings.secrets_backend != settings.secrets_backend:
                try:
                    self._secrets_service.set_secret(SERVICE, account, password, settings=settings)
                except Exception:
                    pass
            return password
        return None

    def clear_password(self, session: Session) -> None:
        account = self._account_for(session)
        settings = self._settings_service.load()
        targets: list[tuple[str, AppSettings]] = [(SERVICE, settings), (LEGACY_SERVICE, settings)]
        if settings.secrets_backend != "keyring":
            legacy_settings = self._legacy_settings()
            targets.extend(
                [
                    (SERVICE, legacy_settings),
                    (LEGACY_SERVICE, legacy_settings),
                ]
            )
        seen: set[tuple[str, str]] = set()
        for service_name, candidate_settings in targets:
            marker = (service_name, candidate_settings.secrets_backend)
            if marker in seen:
                continue
            seen.add(marker)
            try:
                self._secrets_service.delete_secret(service_name, account, settings=candidate_settings)
            except Exception:
                continue
