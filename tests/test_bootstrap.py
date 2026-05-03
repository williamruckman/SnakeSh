from __future__ import annotations

import sys
import unittest
from unittest.mock import call, patch

from snakesh import bootstrap
from snakesh.bootstrap import RuntimeRequirement


class BootstrapTests(unittest.TestCase):
    def test_missing_requirements_marks_broken_cffi_for_force_reinstall(self) -> None:
        def fake_import(module_name: str) -> object:
            if module_name == "cffi":
                raise ImportError("broken cffi backend")
            return object()

        with patch("snakesh.bootstrap.importlib.import_module", side_effect=fake_import):
            missing = bootstrap._missing_requirements()

        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0].package_spec, "cffi>=1.16")
        self.assertTrue(missing[0].force_reinstall)

    def test_run_pip_install_uses_force_reinstall_for_flagged_requirements(self) -> None:
        requirements = [
            RuntimeRequirement("PySide6", "PySide6>=6.7,<7"),
            RuntimeRequirement("cffi", "cffi>=1.16", force_reinstall=True),
        ]

        with patch("snakesh.bootstrap.subprocess.check_call") as mock_check_call:
            bootstrap._run_pip_install(requirements)

        self.assertEqual(mock_check_call.call_count, 2)
        mock_check_call.assert_has_calls(
            [
                call([sys.executable, "-m", "pip", "install", "PySide6>=6.7,<7"]),
                call(
                    [
                        sys.executable,
                        "-m",
                        "pip",
                        "install",
                        "--force-reinstall",
                        "--no-cache-dir",
                        "cffi>=1.16",
                    ]
                ),
            ]
        )

    def test_ensure_runtime_dependencies_stops_on_unsupported_python(self) -> None:
        with patch("snakesh.bootstrap._python_version_ok", return_value=False):
            with patch("snakesh.bootstrap._missing_requirements") as mock_missing:
                ok = bootstrap.ensure_runtime_dependencies()

        self.assertFalse(ok)
        mock_missing.assert_not_called()

    def test_ensure_runtime_dependencies_recovers_after_install(self) -> None:
        missing = [RuntimeRequirement("cffi", "cffi>=1.16", force_reinstall=True)]

        with patch("snakesh.bootstrap._python_version_ok", return_value=True):
            with patch("snakesh.bootstrap._missing_requirements", side_effect=[missing, []]):
                with patch("snakesh.bootstrap._run_pip_install") as mock_install:
                    ok = bootstrap.ensure_runtime_dependencies()

        self.assertTrue(ok)
        mock_install.assert_called_once_with(missing)

    def test_required_modules_include_snmp_runtime(self) -> None:
        package_specs = {requirement.package_spec for requirement in bootstrap.REQUIRED_MODULES}

        self.assertIn("pysnmp>=7,<8", package_specs)
        self.assertIn("pyasn1>=0.6,<1", package_specs)


if __name__ == "__main__":
    unittest.main()
