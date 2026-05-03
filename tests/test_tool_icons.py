from __future__ import annotations

import unittest

from snakesh.core.tool_icons import TOOL_ICON_FORMATS, tool_icon_path
from snakesh.core.tool_registry import TOOL_REGISTRY


class ToolIconTests(unittest.TestCase):
    def test_every_registered_tool_has_launcher_icon_assets(self) -> None:
        for entry in TOOL_REGISTRY:
            with self.subTest(tool_key=entry.key):
                for icon_format in TOOL_ICON_FORMATS:
                    icon_path = tool_icon_path(entry.key, icon_format, fallback=False)
                    self.assertTrue(icon_path.exists(), f"Missing {icon_format} icon: {icon_path}")
                    self.assertGreater(icon_path.stat().st_size, 0)

    def test_generated_tool_icon_headers_match_expected_formats(self) -> None:
        for entry in TOOL_REGISTRY:
            with self.subTest(tool_key=entry.key):
                png_header = tool_icon_path(entry.key, "png", fallback=False).read_bytes()[:8]
                ico_header = tool_icon_path(entry.key, "ico", fallback=False).read_bytes()[:4]
                icns_header = tool_icon_path(entry.key, "icns", fallback=False).read_bytes()[:4]
                self.assertEqual(png_header, b"\x89PNG\r\n\x1a\n")
                self.assertEqual(ico_header, b"\x00\x00\x01\x00")
                self.assertEqual(icns_header, b"icns")


if __name__ == "__main__":
    unittest.main()
