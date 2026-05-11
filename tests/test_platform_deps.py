from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from snakesh.services import platform_deps


class PlatformDependencyTests(unittest.TestCase):
    def test_macos_protocol_dependencies_use_homebrew_discovery_without_auto_install(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            homebrew_bin = Path(temp_dir) / "bin"
            homebrew_bin.mkdir()
            for executable_name in ("xfreerdp", "vncviewer", "nxplayer"):
                (homebrew_bin / executable_name).write_text("", encoding="utf-8")

            with (
                patch("snakesh.services.platform_deps.platform.system", return_value="Darwin"),
                patch("snakesh.protocols.rdp.platform.system", return_value="Darwin"),
                patch("snakesh.protocols.vnc.platform.system", return_value="Darwin"),
                patch("snakesh.protocols.nomachine.platform.system", return_value="Darwin"),
                patch("snakesh.services.external_tools.MACOS_EXECUTABLE_DIRS", (homebrew_bin,)),
                patch("snakesh.services.external_tools.shutil.which", return_value=None),
            ):
                deps = {dep.id: dep for dep in platform_deps.required_dependencies()}
                available = {
                    dep_id: bool(dep.is_available and dep.is_available())
                    for dep_id, dep in deps.items()
                    if dep_id in {"xfreerdp", "vncviewer", "nxplayer"}
                }

        self.assertFalse(deps["xfreerdp"].can_auto_install)
        self.assertFalse(deps["vncviewer"].can_auto_install)
        self.assertFalse(deps["nxplayer"].can_auto_install)
        self.assertEqual(available, {"xfreerdp": True, "vncviewer": True, "nxplayer": True})


if __name__ == "__main__":
    unittest.main()
