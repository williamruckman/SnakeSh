from __future__ import annotations

from base64 import urlsafe_b64encode
from hashlib import sha256
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from snakesh.core.paths import data_dir
from snakesh.services.secrets_service import SecretsService
from snakesh.services.settings_service import AppSettings, SettingsService

KEYRING_SERVICE = "SnakeSh"
LEGACY_KEYRING_SERVICES = ("SecurePython", "securepython", "snakesh")
KEYRING_ACCOUNT = "session_store_master_key"


class SecurityError(RuntimeError):
    pass


def _generate_fernet_key() -> str:
    # Fernet keys are URL-safe base64-encoded 32-byte values.
    return Fernet.generate_key().decode("ascii")


def _normalize_key(candidate: str) -> bytes:
    if len(candidate) == 44:
        return candidate.encode("ascii")
    digest = sha256(candidate.encode("utf-8")).digest()
    return urlsafe_b64encode(digest)


def _legacy_settings() -> AppSettings:
    defaults = AppSettings.defaults()
    defaults.secrets_backend = "keyring"
    return defaults


def _candidate_settings(settings: AppSettings) -> list[AppSettings]:
    candidates = [settings]
    if settings.secrets_backend != "keyring":
        candidates.append(_legacy_settings())
    return candidates


def _candidate_sources(settings: AppSettings) -> list[tuple[str, AppSettings]]:
    sources: list[tuple[str, AppSettings]] = []
    settings_candidates = _candidate_settings(settings)
    for candidate_settings in settings_candidates:
        sources.append((KEYRING_SERVICE, candidate_settings))
    for service_name in LEGACY_KEYRING_SERVICES:
        for candidate_settings in settings_candidates:
            sources.append((service_name, candidate_settings))
    return sources


def _session_store_payload(store_path: Path | None = None) -> bytes | None:
    path = store_path if store_path is not None else data_dir() / "sessions.enc"
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise SecurityError(f"Unable to read saved sessions at {path}.") from exc


def get_or_create_key(store_path: Path | None = None) -> bytes:
    payload = _session_store_payload(store_path)
    settings_service = SettingsService()
    secrets_service = SecretsService(settings_service=settings_service)
    settings = settings_service.load()
    backend_errors: list[str] = []
    found_keys: list[tuple[str, AppSettings, str, bytes]] = []
    seen: set[tuple[str, str]] = set()
    seen_keys: set[bytes] = set()
    for service_name, candidate_settings in _candidate_sources(settings):
        marker = (service_name, candidate_settings.secrets_backend)
        if marker in seen:
            continue
        seen.add(marker)
        try:
            raw_key = secrets_service.get_secret(
                service_name,
                KEYRING_ACCOUNT,
                settings=candidate_settings,
            )
        except Exception as exc:  # pragma: no cover
            backend_errors.append(str(exc))
            continue
        if not raw_key:
            continue
        try:
            normalized = _normalize_key(raw_key)
            Fernet(normalized)
        except Exception as exc:  # pragma: no cover
            backend_errors.append(str(exc))
            continue
        if normalized in seen_keys:
            continue
        seen_keys.add(normalized)
        found_keys.append((service_name, candidate_settings, raw_key, normalized))

    if found_keys:
        selected = found_keys[0]
        if payload is not None:
            selected = None
            for candidate in found_keys:
                _, _, _, normalized = candidate
                try:
                    Fernet(normalized).decrypt(payload)
                except InvalidToken:
                    continue
                except Exception:  # pragma: no cover
                    continue
                selected = candidate
                break
            if selected is None:
                raise SecurityError(
                    "The available encryption keys cannot unlock the saved sessions. "
                    "Restore access to the original secrets backend or encryption key."
                )

        selected_service_name, selected_settings, selected_raw_key, selected_normalized_key = selected
        if (
            selected_service_name != KEYRING_SERVICE
            or selected_settings.secrets_backend != settings.secrets_backend
        ):
            try:
                # Best-effort migration from legacy service/backend to active backend.
                secrets_service.set_secret(
                    KEYRING_SERVICE,
                    KEYRING_ACCOUNT,
                    selected_raw_key,
                    settings=settings,
                )
            except Exception:
                pass
        return selected_normalized_key

    if payload is not None:
        raise SecurityError(
            "The saved session encryption key is unavailable. "
            "No replacement key was created. Unlock the secrets backend and retry."
        )
    if backend_errors:
        raise SecurityError("Unable to read the secrets backend. No new encryption key was created.")

    key = _generate_fernet_key()
    persisted = False
    try:
        secrets_service.set_secret(KEYRING_SERVICE, KEYRING_ACCOUNT, key, settings=settings)
        persisted = True
    except Exception as exc:  # pragma: no cover
        backend_errors.append(str(exc))
    if not persisted and settings.secrets_backend != "keyring":
        try:
            secrets_service.set_secret(
                KEYRING_SERVICE,
                KEYRING_ACCOUNT,
                key,
                settings=_legacy_settings(),
            )
            persisted = True
        except Exception as exc:  # pragma: no cover
            backend_errors.append(str(exc))
    if not persisted:  # pragma: no cover
        message = "; ".join(entry for entry in backend_errors if entry) or "unknown backend error"
        raise SecurityError(f"Unable to persist encryption key in any secrets backend: {message}")

    try:
        return _normalize_key(key)
    except Exception as exc:  # pragma: no cover
        raise SecurityError("Unable to normalize encryption key from configured secrets backend.") from exc


def build_cipher(store_path: Path | None = None) -> Fernet:
    return Fernet(get_or_create_key(store_path))
