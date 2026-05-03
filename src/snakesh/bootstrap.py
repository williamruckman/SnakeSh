from __future__ import annotations

import importlib
import subprocess
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeRequirement:
    probe_module: str
    package_spec: str
    force_reinstall: bool = False


MIN_PYTHON_VERSION = (3, 11)

REQUIRED_MODULES = (
    RuntimeRequirement("PySide6", "PySide6>=6.7,<7"),
    RuntimeRequirement("asyncssh", "asyncssh>=2.14"),
    RuntimeRequirement("cryptography.fernet", "cryptography>=42"),
    RuntimeRequirement("cffi", "cffi>=1.16", force_reinstall=True),
    RuntimeRequirement("keyring", "keyring>=25"),
    RuntimeRequirement("psutil", "psutil>=5.9"),
    RuntimeRequirement("pyasn1", "pyasn1>=0.6,<1"),
    RuntimeRequirement("pysnmp", "pysnmp>=7,<8"),
    RuntimeRequirement("pyte", "pyte>=0.8.2"),
)


def _missing_requirements() -> list[RuntimeRequirement]:
    missing: list[RuntimeRequirement] = []
    for requirement in REQUIRED_MODULES:
        try:
            importlib.import_module(requirement.probe_module)
        except Exception:
            missing.append(requirement)
    return missing


def _python_version_ok() -> bool:
    return sys.version_info >= MIN_PYTHON_VERSION


def _run_pip_install(requirements: list[RuntimeRequirement]) -> None:
    standard_specs = sorted(
        {
            requirement.package_spec
            for requirement in requirements
            if not requirement.force_reinstall
        }
    )
    force_specs = sorted(
        {
            requirement.package_spec
            for requirement in requirements
            if requirement.force_reinstall
        }
    )

    if standard_specs:
        subprocess.check_call([sys.executable, "-m", "pip", "install", *standard_specs])
    if force_specs:
        subprocess.check_call(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--force-reinstall",
                "--no-cache-dir",
                *force_specs,
            ]
        )


def _print_manual_install_commands(requirements: list[RuntimeRequirement]) -> None:
    standard_specs = sorted(
        {
            requirement.package_spec
            for requirement in requirements
            if not requirement.force_reinstall
        }
    )
    force_specs = sorted(
        {
            requirement.package_spec
            for requirement in requirements
            if requirement.force_reinstall
        }
    )
    if standard_specs:
        print(f"  {sys.executable} -m pip install " + " ".join(standard_specs))
    if force_specs:
        print(
            f"  {sys.executable} -m pip install --force-reinstall --no-cache-dir "
            + " ".join(force_specs)
        )


def ensure_runtime_dependencies() -> bool:
    if not _python_version_ok():
        version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        required = ".".join(str(part) for part in MIN_PYTHON_VERSION)
        print(f"SnakeSh requires Python {required}+ (found {version}).")
        return False

    missing = _missing_requirements()
    if not missing:
        return True

    print("SnakeSh: missing or broken dependencies detected.")
    for requirement in missing:
        print(f" - {requirement.package_spec}")
    print("Attempting automatic installation with pip...")

    try:
        _run_pip_install(missing)
    except subprocess.CalledProcessError:
        print("SnakeSh: automatic dependency installation failed.")
        print("Run this manually:")
        _print_manual_install_commands(missing)
        return False

    remaining = _missing_requirements()
    if remaining:
        print("SnakeSh: some dependencies are still missing after install.")
        for requirement in remaining:
            print(f" - {requirement.package_spec}")
        return False

    print("SnakeSh: dependencies installed successfully.")
    return True
