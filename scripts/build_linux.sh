#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

if [[ -z "${PYTHON_BIN:-}" ]]; then
    PYTHON_BIN="python3.11"
fi
RUN_TESTS="${RUN_TESTS:-1}"
PYTHON_SHARED_LIB="${PYTHON_SHARED_LIB:-}"
BUILD_SCOPE="${BUILD_SCOPE:-release}"
USE_CONTAINER="${USE_CONTAINER:-auto}"
CONTAINER_ENGINE="${CONTAINER_ENGINE:-}"
RELEASE_BUILDER_IMAGE="${RELEASE_BUILDER_IMAGE:-snakesh-linux-release-builder:py311-jammy}"
RELEASE_BUILDER_DOCKERFILE="${RELEASE_BUILDER_DOCKERFILE:-packaging/linux/release-builder/Dockerfile}"
REBUILD_IMAGE="${REBUILD_IMAGE:-0}"
MIN_PORTABLE_GLIBC_VERSION="${MIN_PORTABLE_GLIBC_VERSION:-2.34}"
MAX_PORTABLE_GLIBC_VERSION="${MAX_PORTABLE_GLIBC_VERSION:-2.35}"
SNAKESH_LINUX_RELEASE_CONTAINER="${SNAKESH_LINUX_RELEASE_CONTAINER:-0}"

version_lt() {
    [[ "$1" != "$2" && "$(printf '%s\n%s\n' "$1" "$2" | sort -V | head -n1)" == "$1" ]]
}

version_gt() {
    [[ "$1" != "$2" && "$(printf '%s\n%s\n' "$1" "$2" | sort -V | tail -n1)" == "$1" ]]
}

relink_duplicate_qt_runtime_libs() {
    local bundle_root="${ROOT_DIR}/dist/SnakeSh/_internal"
    local qt_lib_root="${bundle_root}/PySide6/Qt/lib"
    local qt_lib_name=""
    local top_level_path=""
    local qt_lib_path=""
    local top_level_hash=""
    local qt_lib_hash=""

    if [[ ! -d "${bundle_root}" || ! -d "${qt_lib_root}" ]]; then
        return
    fi

    while IFS= read -r -d '' qt_lib_path; do
        qt_lib_name="$(basename "${qt_lib_path}")"
        top_level_path="${bundle_root}/${qt_lib_name}"
        if [[ ! -e "${top_level_path}" ]]; then
            continue
        fi
        if [[ -L "${top_level_path}" ]]; then
            continue
        fi
        top_level_hash="$(sha256sum "${top_level_path}" | awk '{print $1}')"
        qt_lib_hash="$(sha256sum "${qt_lib_path}" | awk '{print $1}')"
        if [[ "${top_level_hash}" == "${qt_lib_hash}" ]]; then
            rm -f "${top_level_path}"
            ln -s "PySide6/Qt/lib/${qt_lib_name}" "${top_level_path}"
            continue
        fi
        case "${qt_lib_name}" in
            libQt6XcbQpa.so*|libQt6Wayland*.so*)
                rm -f "${top_level_path}"
                ln -s "PySide6/Qt/lib/${qt_lib_name}" "${top_level_path}"
                ;;
        esac
    done < <(find "${qt_lib_root}" -maxdepth 1 -type f -name 'libQt6*.so*' -print0)
}

choose_container_engine() {
    if [[ -n "${CONTAINER_ENGINE}" ]]; then
        if ! command -v "${CONTAINER_ENGINE}" >/dev/null 2>&1; then
            echo "Container engine not found: ${CONTAINER_ENGINE}" >&2
            exit 1
        fi
        return 0
    fi

    if command -v podman >/dev/null 2>&1; then
        CONTAINER_ENGINE="podman"
        return 0
    fi
    if command -v docker >/dev/null 2>&1; then
        CONTAINER_ENGINE="docker"
        return 0
    fi

    return 1
}

validate_build_scope() {
    case "${BUILD_SCOPE}" in
        release|payload)
            ;;
        *)
            echo "Unsupported BUILD_SCOPE=${BUILD_SCOPE}. Use 'release' or 'payload'." >&2
            exit 1
            ;;
    esac
}

validate_use_container_mode() {
    case "${USE_CONTAINER}" in
        auto|0|1)
            ;;
        *)
            echo "Unsupported USE_CONTAINER=${USE_CONTAINER}. Use 'auto', '0', or '1'." >&2
            exit 1
            ;;
    esac
}

should_use_container() {
    if [[ "${BUILD_SCOPE}" != "release" ]]; then
        return 1
    fi
    if [[ "${SNAKESH_LINUX_RELEASE_CONTAINER}" == "1" ]]; then
        return 1
    fi

    case "${USE_CONTAINER}" in
        1)
            choose_container_engine
            return 0
            ;;
        0)
            return 1
            ;;
        auto)
            if choose_container_engine; then
                return 0
            fi
            return 1
            ;;
    esac

    return 1
}

validate_python_runtime() {
    if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
        echo "Python 3.11 is required for SnakeSh Linux release builds. Set PYTHON_BIN to a Python 3.11 interpreter and retry." >&2
        exit 1
    fi

    local python_version
    python_version="$("${PYTHON_BIN}" -c 'import sys; print(sys.version.split()[0])')"
    if ! "${PYTHON_BIN}" - <<'PY'
import sys
raise SystemExit(0 if sys.version_info[:2] == (3, 11) else 1)
PY
    then
        echo "SnakeSh Linux release builds require Python 3.11.x exactly (got ${python_version})." >&2
        exit 1
    fi
}

validate_portable_glibc_baseline() {
    local host_glibc_version=""

    if ! command -v ldd >/dev/null 2>&1; then
        return
    fi

    host_glibc_version="$(ldd --version 2>/dev/null | awk 'NR==1 {print $NF}')"
    if [[ -z "${host_glibc_version}" ]]; then
        return
    fi

    if version_lt "${host_glibc_version}" "${MIN_PORTABLE_GLIBC_VERSION}"; then
        echo "This host provides GLIBC ${host_glibc_version}, but SnakeSh's current Linux dependency stack requires at least GLIBC ${MIN_PORTABLE_GLIBC_VERSION}." >&2
        exit 1
    fi
    if version_gt "${host_glibc_version}" "${MAX_PORTABLE_GLIBC_VERSION}"; then
        echo "Refusing to build a release AppImage on GLIBC ${host_glibc_version}. Build on the pinned portable baseline (GLIBC ${MIN_PORTABLE_GLIBC_VERSION}-${MAX_PORTABLE_GLIBC_VERSION}) so the bundled libpython stays compatible with Linux Mint 21.3 and similar systems." >&2
        exit 1
    fi
}

resolve_python_shared_library() {
    if [[ -n "${PYTHON_SHARED_LIB}" ]]; then
        return
    fi

    PYTHON_SHARED_LIB="$(${PYTHON_BIN} - <<'PY'
from pathlib import Path
import sys
import sysconfig

libname = sysconfig.get_config_var('LDLIBRARY') or ''
candidates = []
for key in ('LIBDIR', 'LIBPL'):
    value = sysconfig.get_config_var(key)
    if value and libname:
        candidates.append(Path(value) / libname)
base_prefix = Path(getattr(sys, 'base_prefix', sys.prefix))
if libname:
    candidates.append(base_prefix / 'lib' / libname)
    candidates.append(Path('/usr/local/lib') / libname)
    candidates.append(Path('/usr/lib') / libname)
for candidate in candidates:
    if candidate.is_file():
        print(candidate.resolve())
        raise SystemExit(0)
print('')
PY
)"
}

validate_python_shared_library() {
    resolve_python_shared_library

    if [[ -z "${PYTHON_SHARED_LIB}" || ! -f "${PYTHON_SHARED_LIB}" ]]; then
        echo "Unable to locate ${PYTHON_BIN}'s shared library. Set PYTHON_SHARED_LIB=/absolute/path/to/libpython3.11.so.1.0 and retry." >&2
        exit 1
    fi
    if [[ "$(basename "${PYTHON_SHARED_LIB}")" != libpython3.11.so* ]]; then
        echo "Expected a Python 3.11 shared library, but got: ${PYTHON_SHARED_LIB}" >&2
        exit 1
    fi
}

prepare_python_environment() {
    "${PYTHON_BIN}" -m pip install --upgrade pip
    "${PYTHON_BIN}" -m pip install -e . pyinstaller pytest

    if [[ "${RUN_TESTS}" == "1" ]]; then
        "${PYTHON_BIN}" -m pytest -q
    fi
}

export_python_shared_library_path() {
    local python_shared_lib_dir
    python_shared_lib_dir="$(dirname "${PYTHON_SHARED_LIB}")"

    if [[ -n "${LD_LIBRARY_PATH:-}" ]]; then
        export LD_LIBRARY_PATH="${python_shared_lib_dir}:${LD_LIBRARY_PATH}"
    else
        export LD_LIBRARY_PATH="${python_shared_lib_dir}"
    fi
}

run_payload_build() {
    validate_python_runtime
    validate_portable_glibc_baseline
    validate_python_shared_library
    prepare_python_environment
    export_python_shared_library_path

    "${PYTHON_BIN}" -m PyInstaller --noconfirm --clean packaging/pyinstaller/snakesh.spec
    relink_duplicate_qt_runtime_libs

    echo "PyInstaller build complete: dist/SnakeSh"
}

run_direct_release_build() {
    local release_stamp="${ROOT_DIR}/.build/linux-release-start.stamp"
    local appimage_path=""

    mkdir -p "${ROOT_DIR}/.build"
    touch "${release_stamp}"

    run_payload_build
    PYTHON_BIN="${PYTHON_BIN}" bash scripts/package_linux_appimage.sh

    appimage_path="$(find "${ROOT_DIR}/dist" -maxdepth 1 -type f -name 'SnakeSh-*-x86_64.AppImage' -newer "${release_stamp}" | head -n 1)"
    if [[ -z "${appimage_path}" ]]; then
        echo "Expected a freshly built AppImage in dist/ after packaging." >&2
        exit 1
    fi

    bash scripts/make_checksums.sh
    echo "Portable Linux release build complete: ${appimage_path#${ROOT_DIR}/}"
}

build_release_builder_image() {
    local needs_build=0

    if [[ "${REBUILD_IMAGE}" == "1" ]]; then
        needs_build=1
    elif ! "${CONTAINER_ENGINE}" image inspect "${RELEASE_BUILDER_IMAGE}" >/dev/null 2>&1; then
        needs_build=1
    fi

    if [[ "${needs_build}" == "0" ]]; then
        return
    fi

    "${CONTAINER_ENGINE}" build \
        --file "${RELEASE_BUILDER_DOCKERFILE}" \
        --tag "${RELEASE_BUILDER_IMAGE}" \
        .
}

run_containerized_release_build() {
    local uid_gid
    local container_args=()
    local build_command

    choose_container_engine
    build_release_builder_image

    uid_gid="$(id -u):$(id -g)"
    container_args=(
        --rm
        --user "${uid_gid}"
        --workdir /workspace
        --volume "${ROOT_DIR}:/workspace"
        --env HOME=/workspace/.build/container-home
        --env RUN_TESTS="${RUN_TESTS}"
        --env QT_QPA_PLATFORM=offscreen
    )

    if [[ "${CONTAINER_ENGINE}" == "podman" ]]; then
        container_args+=(--userns keep-id)
    fi

    build_command=$(
        cat <<'SH'
set -euo pipefail
mkdir -p /workspace/.build/container-home
mkdir -p /workspace/.build
python3.11 -m venv /workspace/.build/linux-release-venv
PYTHON_BIN=/workspace/.build/linux-release-venv/bin/python
export PYTHON_BIN
BUILD_SCOPE=release \
USE_CONTAINER=0 \
SNAKESH_LINUX_RELEASE_CONTAINER=1 \
RUN_TESTS="${RUN_TESTS}" \
LINUXDEPLOY_BIN=/opt/snakesh/tools/linuxdeploy-x86_64.AppImage \
APPIMAGETOOL_BIN=/opt/snakesh/tools/appimagetool-x86_64.AppImage \
bash scripts/build_linux.sh
SH
    )

    echo "Using containerized Linux release builder via ${CONTAINER_ENGINE}."
    "${CONTAINER_ENGINE}" run "${container_args[@]}" "${RELEASE_BUILDER_IMAGE}" /bin/bash -lc "${build_command}"
}

main() {
    validate_build_scope
    validate_use_container_mode

    if should_use_container; then
        run_containerized_release_build
        return
    fi

    if [[ "${BUILD_SCOPE}" == "payload" ]]; then
        run_payload_build
        return
    fi

    run_direct_release_build
}

main "$@"
