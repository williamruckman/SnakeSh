from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from snakesh.core.hostkeys import known_hosts_path, trust_host_key
from snakesh.core.models import Protocol, Session


class _FakeHostKey:
    def export_public_key(self, format_name: str = "openssh") -> str:
        _ = format_name
        return "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeHostKeyForTests"


class HostKeysTests(unittest.TestCase):
    def _session(self, *, port: int = 22) -> Session:
        return Session(
            name="Test Session",
            host="example.com",
            protocol=Protocol.SSH,
            port=port,
        )

    def test_known_hosts_path_is_safe_under_concurrent_calls(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "snakesh"
            expected = root / "known_hosts"
            with patch("snakesh.core.hostkeys.data_dir", return_value=root):
                with ThreadPoolExecutor(max_workers=8) as executor:
                    results = list(executor.map(lambda _i: known_hosts_path(), range(32)))

            self.assertTrue(all(path == expected for path in results))
            self.assertTrue(expected.exists())

    def test_trust_host_key_does_not_append_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "snakesh"
            known_hosts = root / "known_hosts"
            session = self._session()
            key = _FakeHostKey()
            with patch("snakesh.core.hostkeys.data_dir", return_value=root):
                trust_host_key(session, key)
                trust_host_key(session, key)

            self.assertEqual(
                known_hosts.read_text(encoding="utf-8").splitlines(),
                ["example.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeHostKeyForTests"],
            )

    def test_trust_host_key_uses_host_port_pattern_for_non_default_port(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "snakesh"
            known_hosts = root / "known_hosts"
            session = self._session(port=2222)
            key = _FakeHostKey()
            with patch("snakesh.core.hostkeys.data_dir", return_value=root):
                trust_host_key(session, key)

            self.assertEqual(
                known_hosts.read_text(encoding="utf-8").strip(),
                "[example.com]:2222 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeHostKeyForTests",
            )


if __name__ == "__main__":
    unittest.main()
