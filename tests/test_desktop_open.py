from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from snakesh.ui.desktop_open import open_local_path


class DesktopOpenTests(unittest.TestCase):
    def test_linux_prefers_detached_xdg_open(self) -> None:
        target = Path("/tmp/snakesh-profile")
        with (
            patch("snakesh.ui.desktop_open.shutil.which", side_effect=lambda name: "/usr/bin/xdg-open" if name == "xdg-open" else None),
            patch("snakesh.ui.desktop_open.subprocess.Popen") as mock_popen,
            patch("snakesh.ui.desktop_open.QDesktopServices.openUrl", return_value=True) as mock_open_url,
        ):
            result = open_local_path(target, platform_name="linux")
        self.assertTrue(result)
        mock_popen.assert_called_once()
        self.assertEqual(mock_popen.call_args.args[0], ["xdg-open", str(target)])
        mock_open_url.assert_not_called()

    def test_linux_falls_back_to_gio_when_xdg_open_is_missing(self) -> None:
        target = Path("/tmp/snakesh-profile")

        def which_side_effect(name: str) -> str | None:
            if name == "gio":
                return "/usr/bin/gio"
            return None

        with (
            patch("snakesh.ui.desktop_open.shutil.which", side_effect=which_side_effect),
            patch("snakesh.ui.desktop_open.subprocess.Popen") as mock_popen,
        ):
            result = open_local_path(target, platform_name="linux")
        self.assertTrue(result)
        mock_popen.assert_called_once()
        self.assertEqual(mock_popen.call_args.args[0], ["gio", "open", str(target)])

    def test_linux_uses_qt_fallback_when_no_launcher_is_available(self) -> None:
        target = Path("/tmp/snakesh-profile")
        with (
            patch("snakesh.ui.desktop_open.shutil.which", return_value=None),
            patch("snakesh.ui.desktop_open.QDesktopServices.openUrl", return_value=True) as mock_open_url,
        ):
            result = open_local_path(target, platform_name="linux")
        self.assertTrue(result)
        opened_url = mock_open_url.call_args.args[0]
        self.assertEqual(Path(opened_url.toLocalFile()), target)


if __name__ == "__main__":
    unittest.main()
