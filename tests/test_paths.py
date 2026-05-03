from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from snakesh.core.paths import data_dir


class DataDirMigrationTests(unittest.TestCase):
    def test_linux_legacy_directory_is_renamed_to_snakesh(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir) / "home"
            legacy = home / ".local" / "share" / "securepython"
            legacy.mkdir(parents=True)
            (legacy / "settings.json").write_text('{"theme":"legacy"}', encoding="utf-8")
            (legacy / "sessions.enc").write_bytes(b"encrypted")

            with (
                patch("snakesh.core.paths.Path.home", return_value=home),
                patch("snakesh.core.paths.platform.system", return_value="Linux"),
            ):
                resolved = data_dir()

            preferred = home / ".local" / "share" / "snakesh"
            self.assertEqual(resolved, preferred)
            self.assertTrue((preferred / "settings.json").exists())
            self.assertTrue((preferred / "sessions.enc").exists())
            self.assertFalse(legacy.exists())

    def test_linux_conflicts_keep_preferred_and_preserve_legacy_copy(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir) / "home"
            preferred = home / ".local" / "share" / "snakesh"
            legacy = home / ".local" / "share" / "securepython"
            preferred.mkdir(parents=True)
            legacy.mkdir(parents=True)
            (preferred / "settings.json").write_text('{"source":"snakesh"}', encoding="utf-8")
            (legacy / "settings.json").write_text('{"source":"securepython"}', encoding="utf-8")
            (legacy / "sessions.enc").write_bytes(b"legacy-session")

            with (
                patch("snakesh.core.paths.Path.home", return_value=home),
                patch("snakesh.core.paths.platform.system", return_value="Linux"),
            ):
                resolved = data_dir()

            self.assertEqual(resolved, preferred)
            self.assertEqual((preferred / "settings.json").read_text(encoding="utf-8"), '{"source":"snakesh"}')
            self.assertEqual((preferred / "sessions.enc").read_bytes(), b"legacy-session")
            conflict_copy = preferred / "settings.json.securepython-legacy"
            self.assertTrue(conflict_copy.exists())
            self.assertEqual(conflict_copy.read_text(encoding="utf-8"), '{"source":"securepython"}')
            self.assertFalse(legacy.exists())

    def test_windows_legacy_directory_is_renamed_to_snakesh(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            localapp = Path(tmpdir) / "localappdata"
            legacy = localapp / "SecurePython"
            legacy.mkdir(parents=True)
            (legacy / "known_hosts").write_text("legacy-host", encoding="utf-8")

            with (
                patch.dict("snakesh.core.paths.os.environ", {"LOCALAPPDATA": str(localapp)}, clear=False),
                patch("snakesh.core.paths.Path.home", return_value=Path(tmpdir) / "home"),
                patch("snakesh.core.paths.platform.system", return_value="Windows"),
            ):
                resolved = data_dir()

            preferred = localapp / "SnakeSh"
            self.assertEqual(resolved, preferred)
            self.assertEqual((preferred / "known_hosts").read_text(encoding="utf-8"), "legacy-host")
            self.assertFalse(legacy.exists())

    def test_concurrent_calls_return_consistent_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir) / "home"
            preferred = home / ".local" / "share" / "snakesh"
            with (
                patch("snakesh.core.paths.Path.home", return_value=home),
                patch("snakesh.core.paths.platform.system", return_value="Linux"),
            ):
                with ThreadPoolExecutor(max_workers=8) as executor:
                    results = list(executor.map(lambda _i: data_dir(), range(32)))

            self.assertTrue(all(path == preferred for path in results))
            self.assertTrue(preferred.exists())

    def test_data_dir_honors_environment_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            override = Path(tmpdir) / "custom-data"
            with patch.dict("snakesh.core.paths.os.environ", {"SNAKESH_DATA_DIR": str(override)}, clear=False):
                resolved = data_dir()

            self.assertEqual(resolved, override.resolve())
            self.assertTrue(resolved.exists())


if __name__ == "__main__":
    unittest.main()
