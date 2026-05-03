from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from snakesh.core.models import Protocol, Session
from snakesh.services.ssh_key_service import (
    candidate_public_key_paths,
    is_openssh_public_key_line,
    resolve_existing_public_key,
    validate_public_key_file,
)


def _build_session(**kwargs: object) -> Session:
    payload = {
        "id": "sess-ssh-key-service",
        "name": "SSH Key Service Test",
        "host": "192.0.2.60",
        "protocol": Protocol.SSH,
        "port": 22,
    }
    payload.update(kwargs)
    return Session(**payload)


class SSHKeyServiceTests(unittest.TestCase):
    def test_candidate_public_key_paths_prioritizes_session_paths(self) -> None:
        session = _build_session(
            private_key_path="/tmp/session_key",
            public_key_path="/tmp/session_key.pub",
        )

        candidates = candidate_public_key_paths(session, home_dir=Path("/tmp/home-placeholder"))

        self.assertGreaterEqual(len(candidates), 1)
        self.assertEqual(str(candidates[0]), "/tmp/session_key.pub")

    def test_resolve_existing_public_key_uses_common_default_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            ssh_dir = home / ".ssh"
            ssh_dir.mkdir(parents=True, exist_ok=True)
            key_path = ssh_dir / "id_ed25519.pub"
            key_path.write_text("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA test@example\n", encoding="utf-8")

            session = _build_session()
            resolved = resolve_existing_public_key(session, home_dir=home)
            self.assertEqual(resolved, key_path)

    def test_validate_public_key_file_rejects_invalid_format(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "bad.pub"
            path.write_text("not-a-valid-key", encoding="utf-8")

            ok, message = validate_public_key_file(path)
            self.assertFalse(ok)
            self.assertIn("OpenSSH", message)

    def test_validate_public_key_file_accepts_openssh_format(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "ok.pub"
            path.write_text("ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQC test@example\n", encoding="utf-8")

            ok, message = validate_public_key_file(path)
            self.assertTrue(ok)
            self.assertEqual(message, "")

    def test_is_openssh_public_key_line_accepts_ecdsa_variant(self) -> None:
        self.assertTrue(
            is_openssh_public_key_line(
                "ecdsa-sha2-nistp256 AAAAE2VjZHNhLXNoYTItbmlzdHAyNTY= test@example"
            )
        )
        self.assertFalse(is_openssh_public_key_line("not-a-key"))


if __name__ == "__main__":
    unittest.main()
