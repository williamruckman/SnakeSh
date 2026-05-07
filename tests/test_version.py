from __future__ import annotations

from pathlib import Path
import re
import unittest

import snakesh


class VersionSourceTests(unittest.TestCase):
    def test_public_version_matches_root_version_file(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        version_text = (project_root / "VERSION").read_text(encoding="utf-8").strip()

        self.assertRegex(version_text, re.compile(r"^[0-9]+([.][0-9]+)*$"))
        self.assertEqual(snakesh.__version__, version_text)


if __name__ == "__main__":
    unittest.main()
