from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import base64

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from snakesh.core.models import Session
from snakesh.services.settings_service import AppSettings, SettingsService


class BackupError(Exception):
    pass


class BackupFormatError(BackupError):
    pass


class BackupPasswordRequiredError(BackupError):
    pass


class BackupInvalidPasswordError(BackupError):
    pass


@dataclass(slots=True)
class BackupPayload:
    settings: AppSettings | None
    sessions: list[Session]
    folders: list[str]
    encrypted: bool
    has_settings: bool
    has_sessions: bool
    source_platform: str | None


class BackupService:
    FORMAT = "snakesh-export"
    LEGACY_FORMATS = {"securepython-export"}
    VERSION = 1
    PBKDF2_ITERATIONS = 390000

    def export_bundle(
        self,
        path: Path,
        *,
        settings: AppSettings | None,
        sessions: list[Session] | None,
        folders: list[str] | None,
        password: str | None,
    ) -> None:
        payload: dict[str, object] = {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "source_platform": SettingsService.current_platform_name(),
        }
        if settings is not None:
            payload["settings"] = settings.to_dict()
        if sessions is not None:
            payload["sessions"] = [session.to_dict() for session in sessions]
            payload["folders"] = sorted(set((folders or [])))

        if not any(key in payload for key in ("settings", "sessions")):
            raise BackupError("Nothing selected to export.")

        if password:
            salt = os.urandom(16)
            key = self._derive_key(password=password, salt=salt, iterations=self.PBKDF2_ITERATIONS)
            token = Fernet(key).encrypt(json.dumps(payload, indent=2).encode("utf-8")).decode("utf-8")
            wrapper = {
                "format": self.FORMAT,
                "version": self.VERSION,
                "encrypted": True,
                "kdf": {
                    "name": "pbkdf2-hmac-sha256",
                    "iterations": self.PBKDF2_ITERATIONS,
                    "salt": base64.b64encode(salt).decode("ascii"),
                },
                "payload": token,
            }
        else:
            wrapper = {
                "format": self.FORMAT,
                "version": self.VERSION,
                "encrypted": False,
                "payload": payload,
            }

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(wrapper, indent=2), encoding="utf-8")

    def import_bundle(self, path: Path, password: str | None = None) -> BackupPayload:
        if not path.exists():
            raise BackupError(f"Backup file not found: {path}")

        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise BackupFormatError(f"Invalid backup file: {exc}") from exc

        if not isinstance(raw, dict):
            raise BackupFormatError("Backup root must be an object.")
        raw_format = str(raw.get("format", "")).strip()
        if raw_format not in {self.FORMAT, *self.LEGACY_FORMATS}:
            raise BackupFormatError("Unsupported backup format.")
        if int(raw.get("version", 0)) != self.VERSION:
            raise BackupFormatError("Unsupported backup version.")

        encrypted = bool(raw.get("encrypted", False))
        if encrypted:
            payload = self._decrypt_payload(raw, password=password)
        else:
            payload = raw.get("payload")
            if not isinstance(payload, dict):
                raise BackupFormatError("Unencrypted payload is invalid.")

        has_settings = "settings" in payload
        has_sessions = "sessions" in payload

        settings: AppSettings | None = None
        raw_settings = payload.get("settings")
        if has_settings and not isinstance(raw_settings, dict):
            raise BackupFormatError("Settings section is invalid.")
        if isinstance(raw_settings, dict):
            settings = AppSettings.from_dict(raw_settings)

        sessions: list[Session] = []
        raw_sessions = payload.get("sessions", [])
        if has_sessions and not isinstance(raw_sessions, list):
            raise BackupFormatError("Sessions section is invalid.")
        if isinstance(raw_sessions, list):
            for item in raw_sessions:
                if isinstance(item, dict):
                    sessions.append(Session.from_dict(item))

        folders: list[str] = []
        raw_folders = payload.get("folders", [])
        if isinstance(raw_folders, list):
            folders = [str(folder) for folder in raw_folders if isinstance(folder, str) and folder.strip()]
        source_platform = SettingsService.normalize_platform_name(payload.get("source_platform"))

        if not folders:
            folders = sorted({session.folder for session in sessions if session.folder})

        return BackupPayload(
            settings=settings,
            sessions=sessions,
            folders=sorted(set(folders)),
            encrypted=encrypted,
            has_settings=has_settings,
            has_sessions=has_sessions,
            source_platform=source_platform,
        )

    def _decrypt_payload(self, raw: dict[str, object], password: str | None) -> dict[str, object]:
        if not password:
            raise BackupPasswordRequiredError("Password required to decrypt this backup.")

        kdf = raw.get("kdf")
        if not isinstance(kdf, dict):
            raise BackupFormatError("Missing KDF parameters.")
        salt_b64 = kdf.get("salt")
        iterations = int(kdf.get("iterations", self.PBKDF2_ITERATIONS))
        if not isinstance(salt_b64, str) or not salt_b64:
            raise BackupFormatError("Invalid KDF salt.")

        token = raw.get("payload")
        if not isinstance(token, str) or not token:
            raise BackupFormatError("Missing encrypted payload.")

        try:
            salt = base64.b64decode(salt_b64.encode("ascii"))
            key = self._derive_key(password=password, salt=salt, iterations=iterations)
            decrypted = Fernet(key).decrypt(token.encode("utf-8")).decode("utf-8")
            payload = json.loads(decrypted)
            if not isinstance(payload, dict):
                raise BackupFormatError("Encrypted payload is invalid.")
            return payload
        except InvalidToken as exc:
            raise BackupInvalidPasswordError("Invalid password or corrupted backup.") from exc
        except BackupFormatError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise BackupFormatError(f"Failed to decrypt backup: {exc}") from exc

    @staticmethod
    def _derive_key(*, password: str, salt: bytes, iterations: int) -> bytes:
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=max(100_000, iterations),
        )
        return base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8")))
