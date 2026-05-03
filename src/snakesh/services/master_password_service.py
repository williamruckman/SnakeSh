from __future__ import annotations

import base64
import hashlib
import hmac
import os

from snakesh.services.settings_service import AppSettings

_PBKDF2_ITERATIONS = 260_000
_SALT_BYTES = 16
_DKLEN_BYTES = 32


class MasterPasswordService:
    @staticmethod
    def has_master_password(settings: AppSettings) -> bool:
        return bool(
            settings.master_password_salt_b64.strip()
            and settings.master_password_hash_b64.strip()
        )

    @classmethod
    def clear_master_password(cls, settings: AppSettings) -> None:
        settings.master_password_salt_b64 = ""
        settings.master_password_hash_b64 = ""
        settings.master_password_enabled = False

    @classmethod
    def set_master_password(cls, settings: AppSettings, password: str) -> None:
        raw_password = password.strip()
        if not raw_password:
            raise ValueError("Master password cannot be empty.")
        salt = os.urandom(_SALT_BYTES)
        digest = cls._derive_digest(raw_password, salt)
        settings.master_password_salt_b64 = base64.b64encode(salt).decode("ascii")
        settings.master_password_hash_b64 = base64.b64encode(digest).decode("ascii")

    @classmethod
    def verify_master_password(cls, settings: AppSettings, password: str) -> bool:
        if not cls.has_master_password(settings):
            return False
        try:
            salt = base64.b64decode(settings.master_password_salt_b64, validate=True)
            expected = base64.b64decode(settings.master_password_hash_b64, validate=True)
        except Exception:
            return False
        candidate = cls._derive_digest(password, salt)
        return hmac.compare_digest(candidate, expected)

    @staticmethod
    def _derive_digest(password: str, salt: bytes) -> bytes:
        return hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            _PBKDF2_ITERATIONS,
            dklen=_DKLEN_BYTES,
        )
