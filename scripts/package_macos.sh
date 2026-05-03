#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "SnakeSh macOS packaging must run on macOS." >&2
    exit 1
fi

APP_PATH="${APP_PATH:-dist/SnakeSh.app}"
PACKAGE_ZIP="${PACKAGE_ZIP:-1}"
PACKAGE_DMG="${PACKAGE_DMG:-1}"
ARTIFACT_SUFFIX="${ARTIFACT_SUFFIX:--unsigned}"
VOLUME_NAME="${VOLUME_NAME:-SnakeSh}"
TARGET_ARCH="${TARGET_ARCH:-}"

if [[ ! -d "${APP_PATH}" ]]; then
    echo "Missing app bundle: ${APP_PATH}" >&2
    exit 1
fi

if [[ -z "${TARGET_ARCH}" ]]; then
    case "$(uname -m)" in
        x86_64)
            TARGET_ARCH="x64"
            ;;
        arm64|aarch64)
            TARGET_ARCH="arm64"
            ;;
        *)
            echo "Unsupported macOS packaging architecture." >&2
            exit 1
            ;;
    esac
fi

ZIP_OUTPUT="dist/SnakeSh-macos-${TARGET_ARCH}${ARTIFACT_SUFFIX}.zip"
DMG_OUTPUT="dist/SnakeSh-macos-${TARGET_ARCH}${ARTIFACT_SUFFIX}.dmg"
UNINSTALL_SCRIPT_NAME="Uninstall SnakeSh.command"
UNINSTALL_SCRIPT_SOURCE="packaging/macos/${UNINSTALL_SCRIPT_NAME}"
ZIP_STAGING_DIR=""
DMG_STAGING_DIR=""

if [[ ! -f "${UNINSTALL_SCRIPT_SOURCE}" ]]; then
    echo "Missing macOS uninstall helper: ${UNINSTALL_SCRIPT_SOURCE}" >&2
    exit 1
fi

cleanup_staging_dirs() {
    if [[ -n "${ZIP_STAGING_DIR}" ]]; then
        rm -rf "${ZIP_STAGING_DIR}"
    fi
    if [[ -n "${DMG_STAGING_DIR}" ]]; then
        rm -rf "${DMG_STAGING_DIR}"
    fi
}

stage_payload() {
    local staging_dir="$1"
    ditto "${APP_PATH}" "${staging_dir}/SnakeSh.app"
    cp "${UNINSTALL_SCRIPT_SOURCE}" "${staging_dir}/${UNINSTALL_SCRIPT_NAME}"
    chmod 755 "${staging_dir}/${UNINSTALL_SCRIPT_NAME}"
}

trap cleanup_staging_dirs EXIT

if [[ "${PACKAGE_ZIP}" == "1" ]]; then
    ZIP_STAGING_DIR="$(mktemp -d "${ROOT_DIR}/build/macos-zip.${TARGET_ARCH}.XXXXXX")"
    stage_payload "${ZIP_STAGING_DIR}"
    rm -f "${ZIP_OUTPUT}"
    (cd "${ZIP_STAGING_DIR}" && ditto -c -k --sequesterRsrc . "${ROOT_DIR}/${ZIP_OUTPUT}")
    echo "macOS zip created: ${ZIP_OUTPUT}"
fi

if [[ "${PACKAGE_DMG}" == "1" ]]; then
    DMG_STAGING_DIR="$(mktemp -d "${ROOT_DIR}/build/macos-dmg.${TARGET_ARCH}.XXXXXX")"
    stage_payload "${DMG_STAGING_DIR}"
    ln -s /Applications "${DMG_STAGING_DIR}/Applications"
    rm -f "${DMG_OUTPUT}"
    hdiutil create -volname "${VOLUME_NAME}" -srcfolder "${DMG_STAGING_DIR}" -ov -format UDZO "${DMG_OUTPUT}"
    echo "macOS dmg created: ${DMG_OUTPUT}"
fi
