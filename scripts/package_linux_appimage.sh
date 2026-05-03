#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

PYTHON_BIN="${PYTHON_BIN:-python3.11}"
LINUXDEPLOY_BIN="${LINUXDEPLOY_BIN:-$(command -v linuxdeploy || true)}"
APPIMAGETOOL_BIN="${APPIMAGETOOL_BIN:-$(command -v appimagetool || true)}"
QT_LIB_DIR="${QT_LIB_DIR:-}"
SAFE_BUNDLE_LIBS=(
    "libxcb.so.1"
    "libxcb-cursor.so.0"
    "libxcb-icccm.so.4"
    "libxcb-image.so.0"
    "libxcb-keysyms.so.1"
    "libxcb-render-util.so.0"
    "libxcb-util.so.1"
    "libxkbcommon-x11.so.0"
    "libwayland-client.so.0"
    "libwayland-cursor.so.0"
    "libwayland-egl.so.1"
)
HOST_RUNTIME_ALLOWLIST=(
    "ld-linux-x86-64.so.2"
    "libEGL.so.1"
    "libGL.so.1"
    "libGLX.so.0"
    "libGLdispatch.so.0"
    "libc.so.6"
    "libdl.so.2"
    "libdrm.so.2"
    "libm.so.6"
    "libpthread.so.0"
    "libresolv.so.2"
)
KEEP_QT_PLATFORM_PLUGINS=(
    "libqoffscreen.so"
    "libqwayland.so"
    "libqxcb.so"
)

ensure_qt_platform_plugins() {
    local plugin_dir="${APPDIR}/usr/lib/snakesh/_internal/PySide6/Qt/plugins/platforms"
    local source_plugin_dir=""
    local plugin_name=""

    if [[ -n "${QT_LIB_DIR}" && -d "${QT_LIB_DIR}" ]]; then
        source_plugin_dir="$(cd "${QT_LIB_DIR}/../plugins/platforms" 2>/dev/null && pwd || true)"
    fi

    mkdir -p "${plugin_dir}"

    for plugin_name in "${KEEP_QT_PLATFORM_PLUGINS[@]}"; do
        if [[ -f "${plugin_dir}/${plugin_name}" ]]; then
            continue
        fi
        if [[ -n "${source_plugin_dir}" && -f "${source_plugin_dir}/${plugin_name}" ]]; then
            cp -L "${source_plugin_dir}/${plugin_name}" "${plugin_dir}/${plugin_name}"
        fi
    done

    if [[ ! -f "${plugin_dir}/libqxcb.so" ]]; then
        echo "Missing Qt platform plugin libqxcb.so. Rebuild with a PySide6 environment that provides Qt platform plugins." >&2
        exit 1
    fi
}

copy_missing_qt_plugin_runtime_libs() {
    local plugin_dir="${APPDIR}/usr/lib/snakesh/_internal/PySide6/Qt/plugins"
    local qt_runtime_lib_dir="${APPDIR}/usr/lib/snakesh/_internal/PySide6/Qt/lib"
    local source_qt_lib_dir="${QT_LIB_DIR}"
    local candidate=""
    local needed=""
    local copied_any=1

    if [[ -z "${source_qt_lib_dir}" || ! -d "${source_qt_lib_dir}" ]]; then
        return
    fi

    mkdir -p "${qt_runtime_lib_dir}"

    while [[ "${copied_any}" -eq 1 ]]; do
        copied_any=0
        while IFS= read -r -d '' candidate; do
            while IFS= read -r needed; do
                if [[ "${needed}" != libQt*.so* ]]; then
                    continue
                fi
                if [[ -f "${qt_runtime_lib_dir}/${needed}" ]]; then
                    continue
                fi
                if [[ -f "${source_qt_lib_dir}/${needed}" ]]; then
                    cp -L "${source_qt_lib_dir}/${needed}" "${qt_runtime_lib_dir}/${needed}"
                    copied_any=1
                fi
            done < <(objdump -p "${candidate}" 2>/dev/null | awk '$1 == "NEEDED" {print $2}')
        done < <(find "${plugin_dir}" "${qt_runtime_lib_dir}" -type f \( -name '*.so' -o -name '*.so.*' \) -print0)
    done
}

copy_bundled_xcb_runtime_libs_into_qt_lib() {
    local runtime_root="${APPDIR}/usr/lib/snakesh"
    local plugin_dir="${runtime_root}/_internal/PySide6/Qt/plugins"
    local qt_runtime_lib_dir="${runtime_root}/_internal/PySide6/Qt/lib"
    local bundled_internal_dir="${runtime_root}/_internal"
    local candidate=""
    local needed=""
    local source_path=""

    if [[ ! -d "${plugin_dir}" || ! -d "${bundled_internal_dir}" ]]; then
        return
    fi

    mkdir -p "${qt_runtime_lib_dir}"

    while IFS= read -r -d '' candidate; do
        while IFS= read -r needed; do
            if [[ "${needed}" != libxcb* && "${needed}" != libxkbcommon* && "${needed}" != libX11* ]]; then
                continue
            fi
            if [[ -f "${qt_runtime_lib_dir}/${needed}" ]]; then
                continue
            fi
            source_path="${bundled_internal_dir}/${needed}"
            if [[ -f "${source_path}" ]]; then
                cp -L "${source_path}" "${qt_runtime_lib_dir}/${needed}"
            fi
        done < <(objdump -p "${candidate}" 2>/dev/null | awk '$1 == "NEEDED" {print $2}')
    done < <(find "${plugin_dir}" -type f \( -name '*.so' -o -name '*.so.*' \) -print0)
}

relink_duplicate_qt_runtime_libs_in_appdir() {
    local runtime_root="${APPDIR}/usr/lib/snakesh/_internal"
    local qt_lib_root="${runtime_root}/PySide6/Qt/lib"
    local qt_lib_path=""
    local qt_lib_name=""
    local top_level_path=""

    if [[ ! -d "${runtime_root}" || ! -d "${qt_lib_root}" ]]; then
        return
    fi

    while IFS= read -r -d '' qt_lib_path; do
        qt_lib_name="$(basename "${qt_lib_path}")"
        top_level_path="${runtime_root}/${qt_lib_name}"
        if [[ ! -e "${top_level_path}" || -L "${top_level_path}" ]]; then
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

resolve_host_shared_lib() {
    local soname="$1"
    local resolved=""
    local search_root

    if command -v ldconfig >/dev/null 2>&1; then
        resolved="$(ldconfig -p 2>/dev/null | awk -v name="$soname" '$1 == name {print $NF; exit}')"
    fi

    if [[ -z "${resolved}" ]]; then
        for search_root in \
            /lib64 \
            /usr/lib64 \
            /lib/x86_64-linux-gnu \
            /usr/lib/x86_64-linux-gnu \
            /lib \
            /usr/lib \
            /usr/local/lib
        do
            if [[ -f "${search_root}/${soname}" ]]; then
                resolved="${search_root}/${soname}"
                break
            fi
        done
    fi

    if [[ -z "${resolved}" || ! -f "${resolved}" ]]; then
        return 1
    fi

    printf '%s\n' "${resolved}"
}

prune_qt_platform_plugins() {
    local plugin_dir="${APPDIR}/usr/lib/snakesh/_internal/PySide6/Qt/plugins/platforms"
    local plugin_path=""
    local plugin_name=""
    declare -A keep_map=()

    if [[ ! -d "${plugin_dir}" ]]; then
        return
    fi

    for plugin_name in "${KEEP_QT_PLATFORM_PLUGINS[@]}"; do
        keep_map["${plugin_name}"]=1
    done

    while IFS= read -r -d '' plugin_path; do
        plugin_name="$(basename "${plugin_path}")"
        if [[ -z "${keep_map[${plugin_name}]:-}" ]]; then
            rm -f "${plugin_path}"
        fi
    done < <(find "${plugin_dir}" -maxdepth 1 -type f -name 'libq*.so' -print0)
}

audit_and_bundle_safe_shared_libs() {
    local runtime_root="${APPDIR}/usr/lib/snakesh"
    local qt_runtime_lib_dir="${runtime_root}/_internal/PySide6/Qt/lib"
    local candidate=""
    local bundled_path=""
    local needed=""
    local soname=""
    local host_path=""
    declare -A bundled_libs=()
    declare -A referenced_libs=()

    if ! command -v objdump >/dev/null 2>&1; then
        echo "objdump is required to audit AppImage runtime dependencies." >&2
        exit 1
    fi

    mkdir -p "${qt_runtime_lib_dir}"

    while IFS= read -r -d '' bundled_path; do
        bundled_libs["$(basename "${bundled_path}")"]=1
    done < <(find "${APPDIR}" -type f \( -name '*.so' -o -name '*.so.*' \) -print0)

    while IFS= read -r -d '' candidate; do
        while IFS= read -r needed; do
            if [[ -n "${needed}" ]]; then
                referenced_libs["${needed}"]=1
            fi
        done < <(objdump -p "${candidate}" 2>/dev/null | awk '$1 == "NEEDED" {print $2}')
    done < <(find "${runtime_root}" -type f \( -perm -u+x -o -name '*.so' -o -name '*.so.*' \) -print0)

    for soname in "${SAFE_BUNDLE_LIBS[@]}"; do
        if [[ -z "${referenced_libs[${soname}]:-}" ]]; then
            continue
        fi
        if [[ -n "${bundled_libs[${soname}]:-}" ]]; then
            continue
        fi
        if ! host_path="$(resolve_host_shared_lib "${soname}")"; then
            echo "Missing required runtime library for AppImage packaging: ${soname}" >&2
            exit 1
        fi
        cp -L "${host_path}" "${qt_runtime_lib_dir}/${soname}"
        bundled_libs["${soname}"]=1
        echo "Bundled runtime library: ${soname}"
    done

    for soname in "${HOST_RUNTIME_ALLOWLIST[@]}"; do
        if [[ -n "${referenced_libs[${soname}]:-}" ]]; then
            echo "Retaining host runtime library: ${soname}"
        fi
    done
}

if [[ ! -d "dist/SnakeSh" ]]; then
    echo "Missing dist/SnakeSh. Run scripts/build_linux.sh first." >&2
    exit 1
fi
if [[ -z "${LINUXDEPLOY_BIN}" ]]; then
    echo "linuxdeploy is required (set LINUXDEPLOY_BIN or add to PATH)." >&2
    exit 1
fi
if [[ -z "${APPIMAGETOOL_BIN}" ]]; then
    echo "appimagetool is required (set APPIMAGETOOL_BIN or add to PATH)." >&2
    exit 1
fi

if [[ -z "${QT_LIB_DIR}" ]]; then
    QT_LIB_DIR="$(${PYTHON_BIN} - <<'PY'
from pathlib import Path

try:
    import PySide6
except Exception:
    print("")
    raise SystemExit(0)

qt_lib_dir = Path(PySide6.__file__).resolve().parent / "Qt" / "lib"
print(qt_lib_dir if qt_lib_dir.is_dir() else "")
PY
)"
fi

VERSION="$(${PYTHON_BIN} -c "import tomllib, pathlib; print(tomllib.loads(pathlib.Path('pyproject.toml').read_text(encoding='utf-8'))['project']['version'])")"
APPDIR="build/AppDir"
OUTPUT_APPIMAGE="dist/SnakeSh-${VERSION}-x86_64.AppImage"
OUTPUT_APPIMAGE_STEM="$(basename "${OUTPUT_APPIMAGE%.AppImage}")"

rm -rf "${APPDIR}"
mkdir -p "${APPDIR}/usr/lib/snakesh"
mkdir -p "${APPDIR}/usr/share/applications"
mkdir -p "${APPDIR}/usr/share/icons/hicolor/256x256/apps"
mkdir -p "${APPDIR}/usr/share/mime/packages"

cp -a dist/SnakeSh/. "${APPDIR}/usr/lib/snakesh/"
cp packaging/linux/AppDir/AppRun "${APPDIR}/AppRun"
cp packaging/linux/AppDir/snakesh.desktop "${APPDIR}/snakesh.desktop"
cp packaging/linux/AppDir/snakesh.desktop "${APPDIR}/usr/share/applications/snakesh.desktop"
cp packaging/linux/AppDir/snakesh.xml "${APPDIR}/usr/share/mime/packages/snakesh.xml"
cp packaging/linux/AppDir/snakesh.png "${APPDIR}/snakesh.png"
cp packaging/linux/AppDir/snakesh.png "${APPDIR}/usr/share/icons/hicolor/256x256/apps/snakesh.png"
chmod +x "${APPDIR}/AppRun"

# SnakeSh does not rely on TIFF assets, and the Qt TIFF plugin introduces a
# host-specific libtiff.so.5 dependency that breaks AppImage packaging on newer
# distros where only libtiff.so.6 is installed.
rm -f "${APPDIR}/usr/lib/snakesh/_internal/PySide6/Qt/plugins/imageformats/libqtiff.so"
# SnakeSh does not require Qt desktop theme bridge plugins such as qgtk3. Keep
# the runtime focused on the explicitly supported platform backends instead of
# dragging GTK into the AppImage dependency closure.
rm -rf "${APPDIR}/usr/lib/snakesh/_internal/PySide6/Qt/plugins/platformthemes"
ensure_qt_platform_plugins
copy_missing_qt_plugin_runtime_libs
copy_bundled_xcb_runtime_libs_into_qt_lib
relink_duplicate_qt_runtime_libs_in_appdir
prune_qt_platform_plugins
audit_and_bundle_safe_shared_libs

declare -a LD_LIBRARY_PATH_SEGMENTS=()
if [[ -n "${QT_LIB_DIR}" && -d "${QT_LIB_DIR}" ]]; then
    LD_LIBRARY_PATH_SEGMENTS+=("${QT_LIB_DIR}")
fi
if [[ -d "${ROOT_DIR}/dist/SnakeSh/_internal" ]]; then
    LD_LIBRARY_PATH_SEGMENTS+=("${ROOT_DIR}/dist/SnakeSh/_internal")
fi
if [[ -n "${LD_LIBRARY_PATH:-}" ]]; then
    LD_LIBRARY_PATH_SEGMENTS+=("${LD_LIBRARY_PATH}")
fi
if [[ ${#LD_LIBRARY_PATH_SEGMENTS[@]} -gt 0 ]]; then
    LD_LIBRARY_PATH="$(IFS=:; printf '%s' "${LD_LIBRARY_PATH_SEGMENTS[*]}")"
    export LD_LIBRARY_PATH
fi

APPIMAGE_EXTRACT_AND_RUN=1 "${LINUXDEPLOY_BIN}" --appdir "${APPDIR}" -d "${APPDIR}/snakesh.desktop" -i "${APPDIR}/snakesh.png"
APPIMAGE_EXTRACT_AND_RUN=1 ARCH=x86_64 "${APPIMAGETOOL_BIN}" "${APPDIR}" "${OUTPUT_APPIMAGE}"

APPIMAGE_SHA256="$(sha256sum "${OUTPUT_APPIMAGE}" | awk '{print $1}')"
printf '%s  %s\n' "${APPIMAGE_SHA256}" "${OUTPUT_APPIMAGE}" > "dist/${OUTPUT_APPIMAGE_STEM}.sha256"
printf '%s  %s\n' "${APPIMAGE_SHA256}" "$(basename "${OUTPUT_APPIMAGE}")" > "dist/${OUTPUT_APPIMAGE_STEM}.Appimage.sha256"

echo "AppImage created: ${OUTPUT_APPIMAGE}"
