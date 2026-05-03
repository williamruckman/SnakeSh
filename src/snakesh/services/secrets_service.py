from __future__ import annotations

import base64
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import ssl
import subprocess
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import keyring

from snakesh.services.settings_service import AppSettings, SettingsService


class SecretsError(RuntimeError):
    pass


class SecretsBackendUnavailableError(SecretsError):
    pass


class SecretsConfigError(SecretsError):
    pass


@dataclass(slots=True)
class SecretsHealth:
    ok: bool
    message: str


BACKEND_AUTH_KEYS = (
    "onepassword_service_token",
    "bitwarden_session",
    "keeper_master_password",
    "keepass_master_password",
    "vault_token",
)


class BackendAuthStore:
    _SERVICE = "SnakeSh::BackendAuth"

    def __init__(self, overrides: dict[str, str | None] | None = None) -> None:
        cleaned: dict[str, str | None] = {}
        for key, raw_value in (overrides or {}).items():
            if key not in BACKEND_AUTH_KEYS:
                continue
            if raw_value is None:
                cleaned[key] = None
                continue
            value = str(raw_value).strip()
            cleaned[key] = value or None
        self._overrides = cleaned

    @classmethod
    def keys(cls) -> tuple[str, ...]:
        return BACKEND_AUTH_KEYS

    @classmethod
    def _account_for(cls, key: str) -> str:
        if key not in BACKEND_AUTH_KEYS:
            raise SecretsConfigError(f"Unsupported backend auth key: {key}")
        return f"auth:{key}"

    def get(self, key: str) -> str | None:
        if key in self._overrides:
            return self._overrides.get(key)
        account = self._account_for(key)
        try:
            value = keyring.get_password(self._SERVICE, account)
        except Exception:
            return None
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def has(self, key: str) -> bool:
        return self.get(key) is not None

    def set(self, key: str, value: str) -> None:
        account = self._account_for(key)
        try:
            keyring.set_password(self._SERVICE, account, value)
        except Exception as exc:
            raise SecretsError(f"Unable to persist backend auth value: {exc}") from exc

    def delete(self, key: str) -> None:
        account = self._account_for(key)
        try:
            keyring.delete_password(self._SERVICE, account)
        except Exception:
            return


class _Backend(Protocol):
    def get(self, namespace: str, key: str) -> str | None:
        ...

    def set(self, namespace: str, key: str, value: str) -> None:
        ...

    def delete(self, namespace: str, key: str) -> None:
        ...

    def check(self) -> SecretsHealth:
        ...

    def setup(self) -> SecretsHealth:
        ...


class _KeyringBackend:
    def get(self, namespace: str, key: str) -> str | None:
        return keyring.get_password(namespace, key)

    def set(self, namespace: str, key: str, value: str) -> None:
        keyring.set_password(namespace, key, value)

    def delete(self, namespace: str, key: str) -> None:
        try:
            keyring.delete_password(namespace, key)
        except Exception:
            return

    def check(self) -> SecretsHealth:
        return SecretsHealth(ok=True, message="Using OS keyring backend.")

    def setup(self) -> SecretsHealth:
        return SecretsHealth(ok=True, message="OS keyring backend is ready. No setup required.")


class _OnePasswordBackend:
    def __init__(self, settings: AppSettings, auth_store: BackendAuthStore | None = None) -> None:
        self._cli = settings.onepassword_cli_path.strip() or "op"
        self._account = settings.onepassword_account.strip()
        self._vault = settings.onepassword_vault.strip()
        self._service_token = ""
        if auth_store is not None:
            self._service_token = auth_store.get("onepassword_service_token") or ""
        if not self._service_token:
            self._service_token = os.getenv("OP_SERVICE_ACCOUNT_TOKEN", "").strip()
        if not self._vault:
            raise SecretsConfigError("1Password vault name is required.")
        if shutil.which(self._cli) is None and not Path(self._cli).exists():
            raise SecretsBackendUnavailableError(
                f"1Password CLI executable was not found: {self._cli}"
            )

    def get(self, namespace: str, key: str) -> str | None:
        item = self._item_name(namespace, key)
        result = self._run("read", self._item_ref(item))
        if result.returncode == 0:
            return result.stdout.strip() or None
        if self._is_not_found(result.stderr):
            return None
        raise SecretsError(self._stderr_or_default(result, "1Password read failed."))

    def set(self, namespace: str, key: str, value: str) -> None:
        item = self._item_name(namespace, key)
        edit = self._run(
            "item",
            "edit",
            item,
            "--vault",
            self._vault,
            f"password={value}",
            "--format",
            "json",
        )
        if edit.returncode == 0:
            return
        if not self._is_not_found(edit.stderr):
            raise SecretsError(self._stderr_or_default(edit, "1Password update failed."))

        create = self._run(
            "item",
            "create",
            "--category",
            "Secure Note",
            "--title",
            item,
            "--vault",
            self._vault,
            f"password={value}",
            "--format",
            "json",
        )
        if create.returncode != 0:
            raise SecretsError(self._stderr_or_default(create, "1Password create failed."))

    def delete(self, namespace: str, key: str) -> None:
        item = self._item_name(namespace, key)
        result = self._run("item", "delete", item, "--vault", self._vault, "--archive")
        if result.returncode == 0:
            return
        if self._is_not_found(result.stderr):
            return
        raise SecretsError(self._stderr_or_default(result, "1Password delete failed."))

    def check(self) -> SecretsHealth:
        result = self._run("--version")
        if result.returncode != 0:
            return SecretsHealth(ok=False, message=self._stderr_or_default(result, "Unable to run 1Password CLI."))
        return SecretsHealth(ok=True, message=f"1Password CLI is available: {result.stdout.strip()}")

    def setup(self) -> SecretsHealth:
        version = self._run("--version")
        if version.returncode != 0:
            return SecretsHealth(ok=False, message=self._stderr_or_default(version, "Unable to run 1Password CLI."))
        whoami = self._run("whoami")
        if whoami.returncode == 0:
            return SecretsHealth(ok=True, message="1Password CLI is authenticated.")
        if self._service_token:
            return SecretsHealth(
                ok=False,
                message=self._stderr_or_default(
                    whoami,
                    "Configured 1Password service token appears invalid. Re-enter token in Settings.",
                ),
            )
        return SecretsHealth(
            ok=False,
            message=(
                "1Password is not authenticated. Run `op signin` once or set a Service Token in Settings."
            ),
        )

    def _item_name(self, namespace: str, key: str) -> str:
        return f"{namespace}::{key}"

    def _item_ref(self, item_name: str) -> str:
        return f"op://{self._vault}/{item_name}/password"

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        command = [self._cli]
        if self._account:
            command.extend(["--account", self._account])
        command.extend(args)
        env = None
        if self._service_token:
            env = os.environ.copy()
            env["OP_SERVICE_ACCOUNT_TOKEN"] = self._service_token
        return subprocess.run(command, capture_output=True, text=True, check=False, env=env)

    @staticmethod
    def _is_not_found(stderr: str) -> bool:
        text = (stderr or "").lower()
        return "not found" in text or "doesn't exist" in text

    @staticmethod
    def _stderr_or_default(result: subprocess.CompletedProcess[str], fallback: str) -> str:
        return (result.stderr or result.stdout or "").strip() or fallback


class _BitwardenBackend:
    _ITEM_PREFIX = "SnakeSh"

    def __init__(self, settings: AppSettings, auth_store: BackendAuthStore | None = None) -> None:
        self._cli = settings.bitwarden_cli_path.strip() or "bw"
        self._session = ""
        if auth_store is not None:
            self._session = auth_store.get("bitwarden_session") or ""
        if not self._session:
            self._session = os.getenv("BW_SESSION", "").strip()
        if shutil.which(self._cli) is None and not Path(self._cli).exists():
            raise SecretsBackendUnavailableError(
                f"Bitwarden CLI executable was not found: {self._cli}"
            )

    def get(self, namespace: str, key: str) -> str | None:
        item = self._find_item(namespace, key)
        if item is None:
            return None
        value = item.get("notes")
        if value is None:
            return None
        return str(value)

    def set(self, namespace: str, key: str, value: str) -> None:
        item_name = self._item_name(namespace, key)
        existing = self._find_item(namespace, key)
        if existing is None:
            payload = self._item_template()
            payload["name"] = item_name
            payload["type"] = 2
            payload["secureNote"] = {"type": 0}
            payload["notes"] = value
            create = self._run("create", "item", self._encode_payload(payload))
            if create.returncode != 0:
                raise SecretsError(self._stderr_or_default(create, "Bitwarden create failed."))
            return

        item_id = self._item_id(existing)
        payload = self._get_item_payload(item_id)
        payload["notes"] = value
        edit = self._run("edit", "item", item_id, self._encode_payload(payload))
        if edit.returncode != 0:
            raise SecretsError(self._stderr_or_default(edit, "Bitwarden update failed."))

    def delete(self, namespace: str, key: str) -> None:
        existing = self._find_item(namespace, key)
        if existing is None:
            return
        item_id = self._item_id(existing)
        result = self._run("delete", "item", item_id)
        if result.returncode == 0:
            return
        if self._is_not_found(result.stderr or result.stdout):
            return
        raise SecretsError(self._stderr_or_default(result, "Bitwarden delete failed."))

    def check(self) -> SecretsHealth:
        version = self._run("--version")
        if version.returncode != 0:
            return SecretsHealth(ok=False, message=self._stderr_or_default(version, "Unable to run Bitwarden CLI."))

        status_result = self._run("status")
        if status_result.returncode != 0:
            return SecretsHealth(
                ok=False,
                message=self._stderr_or_default(status_result, "Unable to check Bitwarden CLI status."),
            )

        status_payload = self._parse_json_object(status_result, "Bitwarden status response was invalid JSON.")
        status = str(status_payload.get("status", "")).strip().lower()
        if status == "unlocked":
            return SecretsHealth(ok=True, message=f"Bitwarden CLI is available: {version.stdout.strip()}")
        if status == "locked":
            return SecretsHealth(ok=False, message="Bitwarden vault is locked. Run `bw unlock` first.")
        if status == "unauthenticated":
            return SecretsHealth(ok=False, message="Bitwarden CLI is not authenticated. Run `bw login` first.")
        return SecretsHealth(ok=False, message=f"Bitwarden CLI reported unexpected status: {status or 'unknown'}")

    def setup(self) -> SecretsHealth:
        version = self._run("--version")
        if version.returncode != 0:
            return SecretsHealth(ok=False, message=self._stderr_or_default(version, "Unable to run Bitwarden CLI."))
        status_result = self._run("status")
        if status_result.returncode != 0:
            return SecretsHealth(
                ok=False,
                message=self._stderr_or_default(status_result, "Unable to check Bitwarden CLI status."),
            )
        status_payload = self._parse_json_object(status_result, "Bitwarden status response was invalid JSON.")
        status = str(status_payload.get("status", "")).strip().lower()
        if status == "unlocked":
            sync_result = self._run("sync")
            if sync_result.returncode == 0:
                return SecretsHealth(ok=True, message="Bitwarden is unlocked and synced.")
            return SecretsHealth(
                ok=False,
                message=self._stderr_or_default(sync_result, "Bitwarden is unlocked but sync failed."),
            )
        if status == "locked":
            return SecretsHealth(
                ok=False,
                message="Bitwarden is locked. Run `bw unlock` and optionally store BW_SESSION in Settings.",
            )
        if status == "unauthenticated":
            return SecretsHealth(
                ok=False,
                message="Bitwarden is not authenticated. Run `bw login`, then store BW_SESSION in Settings.",
            )
        return SecretsHealth(ok=False, message=f"Bitwarden CLI reported unexpected status: {status or 'unknown'}")

    def _item_name(self, namespace: str, key: str) -> str:
        return f"{self._ITEM_PREFIX}::{namespace}::{key}"

    def _find_item(self, namespace: str, key: str) -> dict[str, object] | None:
        item_name = self._item_name(namespace, key)
        result = self._run("list", "items", "--search", item_name)
        if result.returncode != 0:
            raise SecretsError(self._stderr_or_default(result, "Bitwarden item search failed."))
        items = self._parse_json_list(result, "Bitwarden item search response was invalid JSON.")
        exact_matches = [
            item
            for item in items
            if isinstance(item, dict) and str(item.get("name", "")) == item_name
        ]
        if not exact_matches:
            return None
        exact_matches.sort(key=lambda item: str(item.get("id", "")))
        return exact_matches[0]

    def _item_id(self, item: dict[str, object]) -> str:
        item_id = str(item.get("id", "")).strip()
        if not item_id:
            raise SecretsError("Bitwarden item search returned an entry without an id.")
        return item_id

    def _item_template(self) -> dict[str, object]:
        result = self._run("get", "template", "item")
        if result.returncode != 0:
            raise SecretsError(self._stderr_or_default(result, "Bitwarden item template request failed."))
        payload = self._parse_json_object(result, "Bitwarden item template response was invalid JSON.")
        if not isinstance(payload.get("secureNote"), dict):
            payload["secureNote"] = {"type": 0}
        return payload

    def _get_item_payload(self, item_id: str) -> dict[str, object]:
        result = self._run("get", "item", item_id)
        if result.returncode != 0:
            raise SecretsError(self._stderr_or_default(result, "Bitwarden item fetch failed."))
        return self._parse_json_object(result, "Bitwarden item fetch response was invalid JSON.")

    @staticmethod
    def _encode_payload(payload: dict[str, object]) -> str:
        raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        return base64.b64encode(raw).decode("ascii")

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = None
        if self._session:
            env = os.environ.copy()
            env["BW_SESSION"] = self._session
        return subprocess.run([self._cli, *args], capture_output=True, text=True, check=False, env=env)

    @staticmethod
    def _is_not_found(text: str) -> bool:
        normalized = (text or "").strip().lower()
        return "not found" in normalized or "object not found" in normalized

    @staticmethod
    def _parse_json_object(result: subprocess.CompletedProcess[str], message: str) -> dict[str, object]:
        try:
            payload = json.loads(result.stdout or "{}")
        except Exception as exc:
            raise SecretsError(message) from exc
        if not isinstance(payload, dict):
            raise SecretsError(message)
        return payload

    @staticmethod
    def _parse_json_list(result: subprocess.CompletedProcess[str], message: str) -> list[object]:
        try:
            payload = json.loads(result.stdout or "[]")
        except Exception as exc:
            raise SecretsError(message) from exc
        if not isinstance(payload, list):
            raise SecretsError(message)
        return payload

    @staticmethod
    def _stderr_or_default(result: subprocess.CompletedProcess[str], fallback: str) -> str:
        return (result.stderr or result.stdout or "").strip() or fallback


class _KeeperBackend:
    _TITLE_PREFIX = "SnakeSh"
    _VALUE_PREFIX = "snakesh:v1:"

    def __init__(self, settings: AppSettings, auth_store: BackendAuthStore | None = None) -> None:
        self._cli = settings.keeper_cli_path.strip() or "keeper"
        self._user = settings.keeper_user.strip()
        self._server = settings.keeper_server.strip()
        self._folder = settings.keeper_folder.strip().strip("/") or "SnakeSh"
        self._master_password = ""
        if auth_store is not None:
            self._master_password = auth_store.get("keeper_master_password") or ""
        if not self._master_password:
            self._master_password = os.getenv("KEEPER_PASSWORD", "").strip()
        if shutil.which(self._cli) is None and not Path(self._cli).exists():
            raise SecretsBackendUnavailableError(
                f"Keeper CLI executable was not found: {self._cli}"
            )

    def get(self, namespace: str, key: str) -> str | None:
        record_path = self._record_path(namespace, key)
        result = self._run_batch(f'clipboard-copy "{self._escape(record_path)}" --output stdout')
        if result.returncode != 0:
            if self._is_not_found(result.stderr or result.stdout):
                return None
            raise SecretsError(self._stderr_or_default(result, "Keeper read failed."))
        value = self._extract_value(result.stdout)
        if not value:
            return None
        return self._decode_value(value)

    def set(self, namespace: str, key: str, value: str) -> None:
        encoded = self._encode_value(value)
        record_path = self._record_path(namespace, key)
        update = self._run_batch(
            f'record-update --record "{self._escape(record_path)}" password={encoded}'
        )
        if update.returncode == 0:
            return
        if not self._is_not_found(update.stderr or update.stdout):
            raise SecretsError(self._stderr_or_default(update, "Keeper update failed."))

        self._ensure_folder()
        add = self._run_batch(
            (
                f'record-add --title "{self._escape(self._title(namespace, key))}" '
                f'--record-type login --folder "{self._escape(self._folder)}" '
                f"login=snakesh password={encoded}"
            )
        )
        if add.returncode != 0:
            raise SecretsError(self._stderr_or_default(add, "Keeper create failed."))

    def delete(self, namespace: str, key: str) -> None:
        record_path = self._record_path(namespace, key)
        result = self._run_batch(f'rm "{self._escape(record_path)}" -f')
        if result.returncode == 0:
            return
        if self._is_not_found(result.stderr or result.stdout):
            return
        raise SecretsError(self._stderr_or_default(result, "Keeper delete failed."))

    def check(self) -> SecretsHealth:
        version = self._run_plain("--version")
        if version.returncode != 0:
            return SecretsHealth(ok=False, message=self._stderr_or_default(version, "Unable to run Keeper CLI."))
        whoami = self._run_batch("whoami --format json")
        if whoami.returncode == 0:
            return SecretsHealth(ok=True, message=f"Keeper CLI is available: {version.stdout.strip()}")
        return SecretsHealth(
            ok=False,
            message=self._stderr_or_default(
                whoami,
                "Keeper is not authenticated. Configure Keeper user/password in Settings or login externally.",
            ),
        )

    def setup(self) -> SecretsHealth:
        version = self._run_plain("--version")
        if version.returncode != 0:
            return SecretsHealth(ok=False, message=self._stderr_or_default(version, "Unable to run Keeper CLI."))

        if not self._user or not self._master_password:
            status = self._run_batch("login-status")
            if status.returncode == 0:
                return SecretsHealth(ok=True, message="Keeper CLI is already authenticated.")
            return SecretsHealth(
                ok=False,
                message=(
                    "Keeper login details are missing. Enter Keeper user and master password in Settings "
                    "or login in Keeper CLI externally."
                ),
            )

        sync = self._run_batch("sync-down")
        if sync.returncode != 0:
            return SecretsHealth(
                ok=False,
                message=self._stderr_or_default(sync, "Keeper setup failed. Verify user/password and MFA workflow."),
            )
        return SecretsHealth(ok=True, message="Keeper setup completed.")

    def _title(self, namespace: str, key: str) -> str:
        return f"{self._TITLE_PREFIX}::{namespace}::{key}"

    def _record_path(self, namespace: str, key: str) -> str:
        return f"{self._folder}/{self._title(namespace, key)}"

    def _ensure_folder(self) -> None:
        result = self._run_batch(f'mkdir "{self._escape(self._folder)}"')
        if result.returncode == 0:
            return
        if self._is_exists(result.stderr or result.stdout):
            return
        raise SecretsError(self._stderr_or_default(result, "Unable to create Keeper folder."))

    def _run_plain(self, *args: str) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run([self._cli, *args], capture_output=True, text=True, check=False, timeout=15)
        except subprocess.TimeoutExpired as exc:
            raise SecretsError("Keeper CLI command timed out.") from exc

    def _run_batch(self, *commands: str) -> subprocess.CompletedProcess[str]:
        script_lines: list[str] = []
        if self._server:
            script_lines.append(f"server {self._server}")
        if self._user and self._master_password:
            script_lines.append(
                f'login "{self._escape(self._user)}" --password "{self._escape(self._master_password)}"'
            )
        script_lines.extend(commands)
        script = "\n".join(line for line in script_lines if line.strip()) + "\n"
        try:
            return subprocess.run(
                [self._cli, "--batch-mode", "-"],
                input=script,
                capture_output=True,
                text=True,
                check=False,
                timeout=25,
            )
        except subprocess.TimeoutExpired as exc:
            raise SecretsError("Keeper CLI batch command timed out.") from exc

    @staticmethod
    def _escape(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')

    @classmethod
    def _encode_value(cls, value: str) -> str:
        encoded = base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii")
        return f"{cls._VALUE_PREFIX}{encoded}"

    @classmethod
    def _decode_value(cls, value: str) -> str:
        if not value.startswith(cls._VALUE_PREFIX):
            return value
        raw = value[len(cls._VALUE_PREFIX) :]
        padding = "=" * (-len(raw) % 4)
        try:
            return base64.urlsafe_b64decode(f"{raw}{padding}".encode("ascii")).decode("utf-8")
        except Exception:
            return value

    @staticmethod
    def _extract_value(stdout: str) -> str | None:
        lines = [line.strip() for line in (stdout or "").splitlines() if line.strip()]
        if not lines:
            return None
        for line in reversed(lines):
            lower = line.lower()
            if lower.startswith("warning:") or lower.startswith("error:"):
                continue
            return line
        return None

    @staticmethod
    def _is_not_found(text: str) -> bool:
        normalized = (text or "").lower()
        return (
            "not found" in normalized
            or "cannot resolve" in normalized
            or "could not find" in normalized
            or "no record" in normalized
        )

    @staticmethod
    def _is_exists(text: str) -> bool:
        normalized = (text or "").lower()
        return "already exists" in normalized or "exist" in normalized

    @staticmethod
    def _stderr_or_default(result: subprocess.CompletedProcess[str], fallback: str) -> str:
        return (result.stderr or result.stdout or "").strip() or fallback


class _KeePassBackend:
    _NOTES_PREFIX = "snakesh:v1:"

    def __init__(self, settings: AppSettings, auth_store: BackendAuthStore | None = None) -> None:
        self._cli = settings.keepass_cli_path.strip() or "keepassxc-cli"
        if shutil.which(self._cli) is None and not Path(self._cli).exists():
            raise SecretsBackendUnavailableError(
                f"KeePass CLI executable was not found: {self._cli}"
            )

        database_path = settings.keepass_database_path.strip()
        if not database_path:
            raise SecretsConfigError("KeePass database path is required.")
        self._database = str(Path(database_path).expanduser())
        if not Path(self._database).exists():
            raise SecretsConfigError(f"KeePass database file was not found: {self._database}")

        self._password_env = settings.keepass_password_env.strip() or "KEEPASSXC_PASSWORD"
        self._master_password = ""
        if auth_store is not None:
            self._master_password = auth_store.get("keepass_master_password") or ""
        if not self._master_password:
            self._master_password = os.getenv(self._password_env, "").strip()
        if not self._master_password:
            raise SecretsConfigError(
                f"KeePass password environment variable is empty: {self._password_env}"
            )

        self._key_file = settings.keepass_key_file_path.strip()
        if self._key_file:
            self._key_file = str(Path(self._key_file).expanduser())
            if not Path(self._key_file).exists():
                raise SecretsConfigError(f"KeePass key file was not found: {self._key_file}")

        self._group = settings.keepass_group.strip().strip("/") or "SnakeSh"

    def get(self, namespace: str, key: str) -> str | None:
        result = self._run("show", self._database, self._entry_path(namespace, key))
        if result.returncode != 0:
            if self._is_not_found(result.stderr or result.stdout):
                return None
            raise SecretsError(self._stderr_or_default(result, "KeePass read failed."))
        notes = self._extract_notes(result.stdout)
        if not notes:
            return None
        return self._decode_secret(notes)

    def set(self, namespace: str, key: str, value: str) -> None:
        encoded = self._encode_secret(value)
        entry_path = self._entry_path(namespace, key)
        edit = self._run("edit", "--notes", encoded, self._database, entry_path)
        if edit.returncode == 0:
            return
        if not self._is_not_found(edit.stderr or edit.stdout):
            raise SecretsError(self._stderr_or_default(edit, "KeePass update failed."))

        self._ensure_group()
        add = self._run("add", "--notes", encoded, self._database, entry_path)
        if add.returncode == 0:
            return
        if self._is_exists(add.stderr or add.stdout):
            retry = self._run("edit", "--notes", encoded, self._database, entry_path)
            if retry.returncode == 0:
                return
            raise SecretsError(self._stderr_or_default(retry, "KeePass update failed."))
        raise SecretsError(self._stderr_or_default(add, "KeePass create failed."))

    def delete(self, namespace: str, key: str) -> None:
        result = self._run("rm", self._database, self._entry_path(namespace, key))
        if result.returncode == 0:
            return
        if self._is_not_found(result.stderr or result.stdout):
            return
        raise SecretsError(self._stderr_or_default(result, "KeePass delete failed."))

    def check(self) -> SecretsHealth:
        version = self._run_plain("--version")
        if version.returncode != 0:
            return SecretsHealth(ok=False, message=self._stderr_or_default(version, "Unable to run KeePass CLI."))
        db_info = self._run("db-info", self._database)
        if db_info.returncode != 0:
            return SecretsHealth(
                ok=False,
                message=self._stderr_or_default(
                    db_info,
                    "Unable to access KeePass database. Check database path/password/key file.",
                ),
            )
        return SecretsHealth(ok=True, message=f"KeePass CLI is available: {version.stdout.strip()}")

    def setup(self) -> SecretsHealth:
        return self.check()

    def _entry_path(self, namespace: str, key: str) -> str:
        return f"{self._group}/{namespace}::{key}"

    def _ensure_group(self) -> None:
        result = self._run("mkdir", self._database, self._group)
        if result.returncode == 0:
            return
        if self._is_exists(result.stderr or result.stdout):
            return
        raise SecretsError(self._stderr_or_default(result, "Unable to create KeePass group."))

    def _run_plain(self, *args: str) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run([self._cli, *args], capture_output=True, text=True, check=False, timeout=12)
        except subprocess.TimeoutExpired as exc:
            raise SecretsError("KeePass CLI command timed out.") from exc

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        command = [self._cli]
        if self._key_file:
            command.extend(["--key-file", self._key_file])
        command.extend(["--quiet"])
        command.extend(args)
        try:
            return subprocess.run(
                command,
                input=f"{self._master_password}\n",
                capture_output=True,
                text=True,
                check=False,
                timeout=12,
            )
        except subprocess.TimeoutExpired as exc:
            raise SecretsError(
                "KeePass CLI command timed out. Check KeePass database password and key-file settings."
            ) from exc

    @classmethod
    def _encode_secret(cls, value: str) -> str:
        encoded = base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii")
        return f"{cls._NOTES_PREFIX}{encoded}"

    @classmethod
    def _decode_secret(cls, value: str) -> str:
        if not value.startswith(cls._NOTES_PREFIX):
            return value
        raw = value[len(cls._NOTES_PREFIX) :]
        padding = "=" * (-len(raw) % 4)
        try:
            return base64.urlsafe_b64decode(f"{raw}{padding}".encode("ascii")).decode("utf-8")
        except Exception:
            return value

    @staticmethod
    def _extract_notes(stdout: str) -> str | None:
        text = (stdout or "").strip()
        if not text:
            return None
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        for line in lines:
            if line.lower().startswith("notes:"):
                return line.split(":", 1)[1].strip() or None
        if len(lines) == 1:
            return lines[0]
        return None

    @staticmethod
    def _is_not_found(text: str) -> bool:
        normalized = (text or "").lower()
        return (
            "not found" in normalized
            or "could not find" in normalized
            or "does not exist" in normalized
            or "no entry found" in normalized
        )

    @staticmethod
    def _is_exists(text: str) -> bool:
        normalized = (text or "").lower()
        return "already exists" in normalized or "exists already" in normalized

    @staticmethod
    def _stderr_or_default(result: subprocess.CompletedProcess[str], fallback: str) -> str:
        return (result.stderr or result.stdout or "").strip() or fallback


class _VaultBackend:
    def __init__(self, settings: AppSettings, auth_store: BackendAuthStore | None = None) -> None:
        self._addr = settings.vault_addr.strip().rstrip("/")
        self._mount = settings.vault_mount.strip().strip("/") or "secret"
        self._token_env = settings.vault_token_env.strip() or "VAULT_TOKEN"
        self._namespace = settings.vault_namespace.strip()
        self._skip_tls_verify = bool(settings.vault_skip_tls_verify)
        if not self._addr:
            raise SecretsConfigError("Vault address is required (for example: https://vault.example.com).")
        token = ""
        if auth_store is not None:
            token = auth_store.get("vault_token") or ""
        if not token:
            token = os.getenv(self._token_env, "").strip()
        if not token:
            raise SecretsConfigError(f"Vault token environment variable is empty: {self._token_env}")
        self._token = token
        self._ssl_ctx = ssl.create_default_context()
        if self._skip_tls_verify:
            self._ssl_ctx.check_hostname = False
            self._ssl_ctx.verify_mode = ssl.CERT_NONE

    def get(self, namespace: str, key: str) -> str | None:
        path = self._kv_path(namespace, key)
        response = self._request("GET", f"/v1/{self._mount}/data/{path}")
        if response.status == 404:
            return None
        if response.status != 200:
            raise SecretsError(f"Vault read failed ({response.status}): {response.message}")
        payload = response.json_data or {}
        outer = payload.get("data")
        inner = outer.get("data") if isinstance(outer, dict) else None
        if not isinstance(inner, dict):
            return None
        value = inner.get("value")
        if value is None:
            return None
        return str(value)

    def set(self, namespace: str, key: str, value: str) -> None:
        path = self._kv_path(namespace, key)
        response = self._request(
            "POST",
            f"/v1/{self._mount}/data/{path}",
            payload={"data": {"value": value}},
        )
        if response.status not in (200, 204):
            raise SecretsError(f"Vault write failed ({response.status}): {response.message}")

    def delete(self, namespace: str, key: str) -> None:
        path = self._kv_path(namespace, key)
        response = self._request("DELETE", f"/v1/{self._mount}/metadata/{path}")
        if response.status in (200, 204, 404):
            return
        raise SecretsError(f"Vault delete failed ({response.status}): {response.message}")

    def check(self) -> SecretsHealth:
        response = self._request("GET", "/v1/sys/health")
        if response.status in (200, 429, 472, 473, 501, 503):
            return SecretsHealth(ok=True, message=f"Vault endpoint reachable at {self._addr}.")
        return SecretsHealth(ok=False, message=f"Vault health check failed ({response.status}): {response.message}")

    def setup(self) -> SecretsHealth:
        return self.check()

    def _kv_path(self, namespace: str, key: str) -> str:
        return f"{quote(namespace, safe='')}/{quote(key, safe='')}"

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
    ) -> "_HTTPResult":
        url = f"{self._addr}{path}"
        data = None
        headers = {
            "X-Vault-Token": self._token,
            "Content-Type": "application/json",
        }
        if self._namespace:
            headers["X-Vault-Namespace"] = self._namespace
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
        request = Request(url=url, data=data, headers=headers, method=method)
        try:
            with urlopen(request, context=self._ssl_ctx, timeout=8) as response:
                body = response.read().decode("utf-8")
                json_data = json.loads(body) if body.strip() else {}
                return _HTTPResult(status=response.status, message="", json_data=json_data)
        except HTTPError as exc:
            body = exc.read().decode("utf-8") if exc.fp is not None else ""
            message = body.strip() or str(exc)
            try:
                json_data = json.loads(body) if body.strip() else {}
            except Exception:
                json_data = {}
            return _HTTPResult(status=exc.code, message=message, json_data=json_data)
        except URLError as exc:
            raise SecretsError(f"Vault request failed: {exc}") from exc


@dataclass(slots=True)
class _HTTPResult:
    status: int
    message: str
    json_data: dict[str, object]


class SecretsService:
    def __init__(self, settings_service: SettingsService | None = None) -> None:
        self._settings_service = settings_service or SettingsService()
        self._auth_store = BackendAuthStore()

    def get_secret(
        self,
        namespace: str,
        key: str,
        *,
        settings: AppSettings | None = None,
    ) -> str | None:
        backend = self._backend_for(settings=settings)
        return backend.get(namespace, key)

    def set_secret(
        self,
        namespace: str,
        key: str,
        value: str,
        *,
        settings: AppSettings | None = None,
    ) -> None:
        backend = self._backend_for(settings=settings)
        backend.set(namespace, key, value)

    def delete_secret(
        self,
        namespace: str,
        key: str,
        *,
        settings: AppSettings | None = None,
    ) -> None:
        backend = self._backend_for(settings=settings)
        backend.delete(namespace, key)

    def check_backend(
        self,
        settings: AppSettings | None = None,
        auth_overrides: dict[str, str | None] | None = None,
    ) -> SecretsHealth:
        try:
            backend = self._backend_for(settings=settings, auth_overrides=auth_overrides)
            return backend.check()
        except SecretsError as exc:
            return SecretsHealth(ok=False, message=str(exc))
        except Exception as exc:  # noqa: BLE001
            return SecretsHealth(ok=False, message=f"Unexpected secrets backend error: {exc}")

    def setup_backend(
        self,
        settings: AppSettings | None = None,
        auth_overrides: dict[str, str | None] | None = None,
    ) -> SecretsHealth:
        try:
            backend = self._backend_for(settings=settings, auth_overrides=auth_overrides)
            return backend.setup()
        except SecretsError as exc:
            return SecretsHealth(ok=False, message=str(exc))
        except Exception as exc:  # noqa: BLE001
            return SecretsHealth(ok=False, message=f"Unexpected secrets backend setup error: {exc}")

    def backend_auth_state(self) -> dict[str, bool]:
        return {key: self._auth_store.has(key) for key in BACKEND_AUTH_KEYS}

    def apply_backend_auth_updates(self, updates: dict[str, str | None]) -> None:
        for key, raw_value in updates.items():
            if key not in BACKEND_AUTH_KEYS:
                continue
            if raw_value is None:
                self._auth_store.delete(key)
                continue
            value = str(raw_value).strip()
            if not value:
                continue
            self._auth_store.set(key, value)

    def _backend_for(
        self,
        settings: AppSettings | None = None,
        auth_overrides: dict[str, str | None] | None = None,
    ) -> _Backend:
        s = settings or self._settings_service.load()
        auth_store = self._auth_store if auth_overrides is None else BackendAuthStore(auth_overrides)
        backend = (s.secrets_backend or "keyring").strip().lower()
        if backend == "keyring":
            return _KeyringBackend()
        if backend == "1password":
            return _OnePasswordBackend(s, auth_store=auth_store)
        if backend == "bitwarden":
            return _BitwardenBackend(s, auth_store=auth_store)
        if backend == "keeper":
            return _KeeperBackend(s, auth_store=auth_store)
        if backend == "keepass":
            return _KeePassBackend(s, auth_store=auth_store)
        if backend == "vault":
            return _VaultBackend(s, auth_store=auth_store)
        raise SecretsConfigError(f"Unsupported secrets backend: {backend}")
