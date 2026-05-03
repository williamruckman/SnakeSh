from __future__ import annotations

import base64
import json
import subprocess
import unittest
from unittest.mock import patch

from snakesh.services.secrets_service import SecretsService
from snakesh.services.settings_service import AppSettings


class _StubSettingsService:
    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings

    def load(self) -> AppSettings:
        return self._settings


class SecretsServiceTests(unittest.TestCase):
    @patch("snakesh.services.secrets_service.keyring.delete_password")
    @patch("snakesh.services.secrets_service.keyring.set_password")
    @patch("snakesh.services.secrets_service.keyring.get_password", return_value="stored-secret")
    def test_keyring_backend_reads_writes_and_deletes(
        self,
        mock_get_password,
        mock_set_password,
        mock_delete_password,
    ) -> None:
        settings = AppSettings.defaults()
        settings.secrets_backend = "keyring"
        service = SecretsService(settings_service=_StubSettingsService(settings))

        self.assertEqual(service.get_secret("svc", "acct"), "stored-secret")
        service.set_secret("svc", "acct", "new-secret")
        service.delete_secret("svc", "acct")
        health = service.check_backend()

        mock_get_password.assert_called_with("svc", "acct")
        mock_set_password.assert_called_once_with("svc", "acct", "new-secret")
        mock_delete_password.assert_called_once_with("svc", "acct")
        self.assertTrue(health.ok)

    def test_unsupported_backend_returns_failed_health(self) -> None:
        settings = AppSettings.defaults()
        settings.secrets_backend = "not-real"
        service = SecretsService(settings_service=_StubSettingsService(settings))

        health = service.check_backend()

        self.assertFalse(health.ok)
        self.assertIn("Unsupported secrets backend", health.message)

    def test_onepassword_backend_requires_vault_name(self) -> None:
        settings = AppSettings.defaults()
        settings.secrets_backend = "1password"
        settings.onepassword_vault = ""
        service = SecretsService(settings_service=_StubSettingsService(settings))

        health = service.check_backend()

        self.assertFalse(health.ok)
        self.assertIn("vault", health.message.lower())

    @patch("snakesh.services.secrets_service.shutil.which", return_value="/usr/bin/bw")
    @patch("snakesh.services.secrets_service.subprocess.run")
    def test_bitwarden_backend_reads_writes_deletes_and_checks(
        self,
        mock_run,
        _mock_which,
    ) -> None:
        settings = AppSettings.defaults()
        settings.secrets_backend = "bitwarden"
        settings.bitwarden_cli_path = "bw"
        service = SecretsService(settings_service=_StubSettingsService(settings))
        item_name = "SnakeSh::svc::acct"

        def _result(args: list[str], code: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(args=args, returncode=code, stdout=stdout, stderr=stderr)

        def _fake_run(command, *args, **kwargs):  # noqa: ANN001
            if command == ["bw", "list", "items", "--search", item_name]:
                return _result(
                    command,
                    stdout=json.dumps(
                        [
                            {
                                "id": "item-1",
                                "name": item_name,
                                "notes": "stored-secret",
                            }
                        ]
                    ),
                )
            if command == ["bw", "get", "item", "item-1"]:
                return _result(
                    command,
                    stdout=json.dumps(
                        {
                            "id": "item-1",
                            "name": item_name,
                            "type": 2,
                            "secureNote": {"type": 0},
                            "notes": "stored-secret",
                        }
                    ),
                )
            if command[:4] == ["bw", "edit", "item", "item-1"]:
                return _result(command, stdout='{"object":"item"}')
            if command == ["bw", "delete", "item", "item-1"]:
                return _result(command, stdout='{"object":"item"}')
            if command == ["bw", "--version"]:
                return _result(command, stdout="2024.12.0")
            if command == ["bw", "status"]:
                return _result(command, stdout='{"status":"unlocked"}')
            raise AssertionError(f"Unexpected command: {command}")

        mock_run.side_effect = _fake_run

        self.assertEqual(service.get_secret("svc", "acct"), "stored-secret")
        service.set_secret("svc", "acct", "new-secret")
        service.delete_secret("svc", "acct")
        health = service.check_backend()

        self.assertTrue(health.ok)
        edit_calls = [
            call.args[0]
            for call in mock_run.call_args_list
            if call.args and isinstance(call.args[0], list) and call.args[0][:4] == ["bw", "edit", "item", "item-1"]
        ]
        self.assertEqual(len(edit_calls), 1)
        encoded_payload = edit_calls[0][4]
        decoded_payload = json.loads(base64.b64decode(encoded_payload).decode("utf-8"))
        self.assertEqual(decoded_payload["notes"], "new-secret")

    @patch("snakesh.services.secrets_service.shutil.which", return_value=None)
    def test_bitwarden_backend_reports_missing_cli(self, _mock_which) -> None:
        settings = AppSettings.defaults()
        settings.secrets_backend = "bitwarden"
        settings.bitwarden_cli_path = "not-a-real-bw-cli"
        service = SecretsService(settings_service=_StubSettingsService(settings))

        health = service.check_backend()

        self.assertFalse(health.ok)
        self.assertIn("bitwarden cli executable was not found", health.message.lower())

    @patch("snakesh.services.secrets_service.os.getenv", return_value="master-pass")
    @patch("snakesh.services.secrets_service.shutil.which", return_value="/usr/bin/keeper")
    @patch("snakesh.services.secrets_service.subprocess.run")
    def test_keeper_backend_reads_writes_deletes_checks_and_setups(
        self,
        mock_run,
        _mock_which,
        _mock_getenv,
    ) -> None:
        settings = AppSettings.defaults()
        settings.secrets_backend = "keeper"
        settings.keeper_cli_path = "keeper"
        settings.keeper_user = "user@example.com"
        settings.keeper_folder = "SnakeSh"
        service = SecretsService(settings_service=_StubSettingsService(settings))
        encoded = f"snakesh:v1:{base64.urlsafe_b64encode(b'stored-secret').decode('ascii')}"
        record_path = "SnakeSh/SnakeSh::svc::acct"

        def _result(args: list[str], code: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(args=args, returncode=code, stdout=stdout, stderr=stderr)

        def _fake_run(command, *args, **kwargs):  # noqa: ANN001
            if command == ["keeper", "--version"]:
                return _result(command, stdout="17.1.0")
            if command == ["keeper", "--batch-mode", "-"]:
                script = kwargs.get("input", "")
                self.assertIn('login "user@example.com" --password "master-pass"', script)
                if f'clipboard-copy "{record_path}" --output stdout' in script:
                    return _result(command, stdout=encoded)
                if f'record-update --record "{record_path}" password=snakesh:v1:' in script:
                    return _result(command, code=1, stderr="Record not found")
                if 'mkdir "SnakeSh"' in script and "record-add" not in script:
                    return _result(command, code=1, stderr="Folder already exists")
                if 'record-add --title "SnakeSh::svc::acct"' in script:
                    return _result(command, stdout="Record created")
                if f'rm "{record_path}" -f' in script:
                    return _result(command, stdout="Record removed")
                if "whoami --format json" in script:
                    return _result(command, stdout='{"result":"ok"}')
                if "sync-down" in script:
                    return _result(command, stdout="Sync complete")
            raise AssertionError(f"Unexpected command: {command}")

        mock_run.side_effect = _fake_run

        self.assertEqual(service.get_secret("svc", "acct"), "stored-secret")
        service.set_secret("svc", "acct", "new-secret")
        service.delete_secret("svc", "acct")
        check = service.check_backend()
        setup = service.setup_backend()

        self.assertTrue(check.ok)
        self.assertTrue(setup.ok)

    @patch("snakesh.services.secrets_service.os.getenv", return_value="")
    @patch("snakesh.services.secrets_service.shutil.which", return_value="/usr/bin/keeper")
    @patch("snakesh.services.secrets_service.subprocess.run")
    def test_keeper_setup_requires_login_or_credentials(
        self,
        mock_run,
        _mock_which,
        _mock_getenv,
    ) -> None:
        settings = AppSettings.defaults()
        settings.secrets_backend = "keeper"
        settings.keeper_cli_path = "keeper"
        settings.keeper_user = ""
        service = SecretsService(settings_service=_StubSettingsService(settings))

        def _result(args: list[str], code: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(args=args, returncode=code, stdout=stdout, stderr=stderr)

        def _fake_run(command, *args, **kwargs):  # noqa: ANN001
            if command == ["keeper", "--version"]:
                return _result(command, stdout="17.1.0")
            if command == ["keeper", "--batch-mode", "-"]:
                script = kwargs.get("input", "")
                if "login-status" in script:
                    return _result(command, code=1, stderr="Not logged in")
                if "whoami --format json" in script:
                    return _result(command, code=1, stderr="Not logged in")
            raise AssertionError(f"Unexpected command: {command}")

        mock_run.side_effect = _fake_run

        setup = service.setup_backend()

        self.assertFalse(setup.ok)
        self.assertIn("missing", setup.message.lower())

    @patch("snakesh.services.secrets_service.keyring.delete_password")
    @patch("snakesh.services.secrets_service.keyring.set_password")
    @patch("snakesh.services.secrets_service.keyring.get_password")
    def test_backend_auth_updates_persist_and_report_state(
        self,
        mock_get_password,
        mock_set_password,
        mock_delete_password,
    ) -> None:
        settings = AppSettings.defaults()
        service = SecretsService(settings_service=_StubSettingsService(settings))

        def _fake_get_password(service_name: str, account: str):  # noqa: ANN001
            if service_name != "SnakeSh::BackendAuth":
                return None
            if account == "auth:vault_token":
                return "stored-vault-token"
            return None

        mock_get_password.side_effect = _fake_get_password

        state = service.backend_auth_state()
        self.assertTrue(state["vault_token"])
        self.assertFalse(state["bitwarden_session"])

        service.apply_backend_auth_updates(
            {
                "vault_token": "  new-vault-token  ",
                "keepass_master_password": None,
                "unknown": "ignored",
                "bitwarden_session": "   ",
            }
        )

        mock_set_password.assert_called_once_with(
            "SnakeSh::BackendAuth",
            "auth:vault_token",
            "new-vault-token",
        )
        mock_delete_password.assert_called_once_with(
            "SnakeSh::BackendAuth",
            "auth:keepass_master_password",
        )

    @patch("snakesh.services.secrets_service.os.getenv", return_value="")
    @patch("snakesh.services.secrets_service.urlopen")
    @patch("snakesh.services.secrets_service.shutil.which", return_value="/usr/bin/keepassxc-cli")
    def test_check_backend_uses_auth_overrides(
        self,
        _mock_which,
        mock_urlopen,
        _mock_getenv,
    ) -> None:
        settings = AppSettings.defaults()
        settings.secrets_backend = "vault"
        settings.vault_addr = "https://vault.example.com"
        settings.vault_token_env = "VAULT_TOKEN"
        service = SecretsService(settings_service=_StubSettingsService(settings))

        missing_token_health = service.check_backend(settings=settings)
        self.assertFalse(missing_token_health.ok)
        self.assertIn("token environment variable is empty", missing_token_health.message.lower())

        mock_urlopen.side_effect = RuntimeError("network blocked")
        override_health = service.check_backend(
            settings=settings,
            auth_overrides={"vault_token": "override-token"},
        )
        self.assertFalse(override_health.ok)
        self.assertNotIn("token environment variable is empty", override_health.message.lower())

    @patch("snakesh.services.secrets_service.Path.exists", return_value=True)
    @patch("snakesh.services.secrets_service.os.getenv", return_value="master-pass")
    @patch("snakesh.services.secrets_service.shutil.which", return_value="/usr/bin/keepassxc-cli")
    @patch("snakesh.services.secrets_service.subprocess.run")
    def test_keepass_backend_reads_writes_deletes_and_checks(
        self,
        mock_run,
        _mock_which,
        _mock_getenv,
        _mock_exists,
    ) -> None:
        settings = AppSettings.defaults()
        settings.secrets_backend = "keepass"
        settings.keepass_cli_path = "keepassxc-cli"
        settings.keepass_database_path = "/tmp/snake.kdbx"
        settings.keepass_password_env = "KEEPASSXC_PASSWORD"
        settings.keepass_group = "SnakeSh"
        service = SecretsService(settings_service=_StubSettingsService(settings))

        encoded = f"snakesh:v1:{base64.urlsafe_b64encode(b'stored-secret').decode('ascii')}"
        entry = "SnakeSh/svc::acct"

        def _result(args: list[str], code: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(args=args, returncode=code, stdout=stdout, stderr=stderr)

        def _fake_run(command, *args, **kwargs):  # noqa: ANN001
            if command == ["keepassxc-cli", "--quiet", "show", "/tmp/snake.kdbx", entry]:
                self.assertEqual(kwargs.get("input"), "master-pass\n")
                return _result(command, stdout=encoded)
            if command == ["keepassxc-cli", "--quiet", "edit", "--notes", f"snakesh:v1:{base64.urlsafe_b64encode(b'new-secret').decode('ascii')}", "/tmp/snake.kdbx", entry]:
                return _result(command, code=1, stderr="Could not find entry")
            if command == ["keepassxc-cli", "--quiet", "mkdir", "/tmp/snake.kdbx", "SnakeSh"]:
                return _result(command, code=1, stderr="Group already exists")
            if command == ["keepassxc-cli", "--quiet", "add", "--notes", f"snakesh:v1:{base64.urlsafe_b64encode(b'new-secret').decode('ascii')}", "/tmp/snake.kdbx", entry]:
                return _result(command, stdout="Entry added")
            if command == ["keepassxc-cli", "--quiet", "rm", "/tmp/snake.kdbx", entry]:
                return _result(command, stdout="Entry removed")
            if command == ["keepassxc-cli", "--version"]:
                return _result(command, stdout="2.7.10")
            if command == ["keepassxc-cli", "--quiet", "db-info", "/tmp/snake.kdbx"]:
                return _result(command, stdout="Database settings")
            raise AssertionError(f"Unexpected command: {command}")

        mock_run.side_effect = _fake_run

        self.assertEqual(service.get_secret("svc", "acct"), "stored-secret")
        service.set_secret("svc", "acct", "new-secret")
        service.delete_secret("svc", "acct")
        health = service.check_backend()

        self.assertTrue(health.ok)
        self.assertIn("keepass cli is available", health.message.lower())

    @patch("snakesh.services.secrets_service.shutil.which", return_value="/usr/bin/keepassxc-cli")
    def test_keepass_backend_requires_database_path(self, _mock_which) -> None:
        settings = AppSettings.defaults()
        settings.secrets_backend = "keepass"
        settings.keepass_database_path = ""
        service = SecretsService(settings_service=_StubSettingsService(settings))

        health = service.check_backend()

        self.assertFalse(health.ok)
        self.assertIn("database path is required", health.message.lower())


if __name__ == "__main__":
    unittest.main()
