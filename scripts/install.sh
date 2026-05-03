#!/usr/bin/env bash
set -euo pipefail

MIN_PYTHON_MAJOR=3
MIN_PYTHON_MINOR=11

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${PROJECT_ROOT}"

is_supported_python() {
    local candidate="$1"
    "${candidate}" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY
}

find_python() {
    local candidates=(
        python3.11
        python3.12
        python3.13
        python3
        python
    )

    local candidate
    for candidate in "${candidates[@]}"; do
        if command -v "${candidate}" >/dev/null 2>&1 && is_supported_python "${candidate}"; then
            printf '%s\n' "${candidate}"
            return 0
        fi
    done

    return 1
}

python_version_string() {
    local candidate="$1"
    "${candidate}" - <<'PY'
import sys
print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
PY
}

VENV_PYTHON=""
RECREATE_BROKEN_VENV=0
BOOTSTRAP_PYTHONPATH=""
PYTHON_BIN=""

discover_bootstrap_pythonpath() {
    local candidate="$1"

    BOOTSTRAP_PYTHONPATH="$("${candidate}" - <<'PY'
from pathlib import Path
import sys

candidates = []

for path in sys.path:
    entry = Path(path)
    if entry.exists() and (entry / "pip").is_dir():
        candidates.append(str(entry))

wheel_dir = Path("/usr/share/python-wheels")
if wheel_dir.is_dir():
    for wheel in sorted(wheel_dir.glob("pip-*.whl")):
        candidates.append(str(wheel))

print(":".join(candidates))
PY
)"

    if [[ -z "${BOOTSTRAP_PYTHONPATH}" ]]; then
        echo "Unable to locate a pip bootstrap source."
        echo "Install python3-venv or python3-pip and rerun this script."
        exit 1
    fi
}

create_venv() {
    local candidate="$1"
    shift

    if "${candidate}" -m venv "$@" .venv; then
        return 0
    fi

    echo "Standard venv creation failed; retrying without ensurepip bootstrap..."
    "${candidate}" -m venv --without-pip "$@" .venv
    discover_bootstrap_pythonpath "${candidate}"
}

ensure_activation_scripts() {
    if [[ -f ".venv/bin/activate" ]]; then
        return 0
    fi

    echo "Restoring missing virtual environment activation scripts..."
    "${PYTHON_BIN}" - <<'PY'
import venv

builder = venv.EnvBuilder(with_pip=False)
context = builder.ensure_directories(".venv")
builder.setup_scripts(context)
PY
}

venv_pip() {
    if "${VENV_PYTHON}" -m pip --version >/dev/null 2>&1; then
        "${VENV_PYTHON}" -m pip "$@"
    else
        if [[ -z "${BOOTSTRAP_PYTHONPATH}" ]]; then
            if [[ -z "${PYTHON_BIN}" ]]; then
                if ! PYTHON_BIN="$(find_python)"; then
                    echo "Unable to locate a Python interpreter to bootstrap pip."
                    exit 1
                fi
            fi
            discover_bootstrap_pythonpath "${PYTHON_BIN}"
        fi

        PYTHONPATH="${BOOTSTRAP_PYTHONPATH}" "${VENV_PYTHON}" -m pip "$@"
    fi
}

if [[ -x ".venv/bin/python" ]]; then
    if is_supported_python ".venv/bin/python"; then
        VENV_PYTHON=".venv/bin/python"
        echo "Using existing virtual environment (.venv)."
    else
        current_version="$(python_version_string ".venv/bin/python")"
        echo "Existing .venv uses Python ${current_version}, but ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR}+ is required."
        echo "Remove it and rerun: rm -rf .venv"
        exit 1
    fi
elif [[ -d ".venv" ]]; then
    echo "Existing .venv is incomplete or points at a missing interpreter."
    RECREATE_BROKEN_VENV=1
fi

if [[ -z "${VENV_PYTHON}" ]]; then
    if ! PYTHON_BIN="$(find_python)"; then
        echo "No Python ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR}+ interpreter found on PATH."
        echo "Install Python ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR}+ and rerun this script."
        echo "Debian/Ubuntu example:"
        echo "  sudo apt update && sudo apt install -y python3.11 python3.11-venv"
        exit 1
    fi

    selected_version="$(python_version_string "${PYTHON_BIN}")"
    if [[ "${RECREATE_BROKEN_VENV}" -eq 1 ]]; then
        echo "Recreating .venv with ${PYTHON_BIN} (${selected_version})..."
        create_venv "${PYTHON_BIN}" --clear
    else
        echo "Creating .venv with ${PYTHON_BIN} (${selected_version})..."
        create_venv "${PYTHON_BIN}"
    fi
    VENV_PYTHON=".venv/bin/python"
fi

if [[ -z "${PYTHON_BIN}" ]]; then
    if ! PYTHON_BIN="$(find_python)"; then
        echo "Unable to locate a Python interpreter for .venv maintenance."
        exit 1
    fi
fi

ensure_activation_scripts

echo "Upgrading packaging tooling..."
venv_pip install --upgrade pip setuptools wheel

echo "Installing SnakeSh in editable mode..."
venv_pip install -e .

if ! "${VENV_PYTHON}" -c "import cffi; import _cffi_backend" >/dev/null 2>&1; then
    echo "Detected broken cffi backend, forcing reinstall..."
    venv_pip install --force-reinstall --no-cache-dir "cffi>=1.16"
fi

if ! "${VENV_PYTHON}" -c "import cffi; import _cffi_backend; import cryptography.fernet" >/dev/null 2>&1; then
    echo "Dependency validation failed."
    echo "Try these commands manually inside the virtual environment:"
    echo "  python -m pip install -e ."
    echo "  python -m pip install --force-reinstall --no-cache-dir \"cffi>=1.16\""
    exit 1
fi

echo
echo "Install complete."
echo "Activate the virtual environment with:"
echo "  source .venv/bin/activate"
