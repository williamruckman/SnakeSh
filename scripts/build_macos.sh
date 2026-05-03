#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "SnakeSh macOS builds must run on macOS." >&2
    exit 1
fi

PYTHON_BIN="${PYTHON_BIN:-python3}"
RUN_TESTS="${RUN_TESTS:-1}"
TARGET_ARCH="${TARGET_ARCH:-}"
PACKAGE_ZIP="${PACKAGE_ZIP:-1}"
PACKAGE_DMG="${PACKAGE_DMG:-1}"
PACKAGE_CHECKSUMS="${PACKAGE_CHECKSUMS:-1}"
MACOS_SIGN_IDENTITY="${MACOS_SIGN_IDENTITY:-}"
NOTARIZE_MACOS="${NOTARIZE_MACOS:-auto}"
NOTARYTOOL_PROFILE="${NOTARYTOOL_PROFILE:-}"
APPLE_ID="${APPLE_ID:-}"
TEAM_ID="${TEAM_ID:-}"
APP_SPECIFIC_PASSWORD="${APP_SPECIFIC_PASSWORD:-}"
ARTIFACT_SUFFIX="${ARTIFACT_SUFFIX:-}"
VOLUME_NAME="${VOLUME_NAME:-SnakeSh}"

if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
    echo "Python executable not found: ${PYTHON_BIN}" >&2
    exit 1
fi

PYTHON_VERSION="$(${PYTHON_BIN} -c 'import sys; print(sys.version.split()[0])')"
if ! "${PYTHON_BIN}" - <<'PY'
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY
then
    echo "SnakeSh macOS builds require Python 3.11+ (got ${PYTHON_VERSION})." >&2
    exit 1
fi

HOST_ARCH="$(uname -m)"
case "${HOST_ARCH}" in
    x86_64)
        DEFAULT_TARGET_ARCH="x64"
        ;;
    arm64|aarch64)
        DEFAULT_TARGET_ARCH="arm64"
        ;;
    *)
        echo "Unsupported macOS build architecture: ${HOST_ARCH}" >&2
        exit 1
        ;;
esac

if [[ -z "${TARGET_ARCH}" ]]; then
    TARGET_ARCH="${DEFAULT_TARGET_ARCH}"
fi

if [[ "${TARGET_ARCH}" != "${DEFAULT_TARGET_ARCH}" ]]; then
    echo "Requested TARGET_ARCH=${TARGET_ARCH}, but host architecture maps to ${DEFAULT_TARGET_ARCH}." >&2
    exit 1
fi

has_notary_credentials() {
    if [[ -n "${NOTARYTOOL_PROFILE}" ]]; then
        return 0
    fi

    [[ -n "${APPLE_ID}" && -n "${TEAM_ID}" && -n "${APP_SPECIFIC_PASSWORD}" ]]
}

SIGN_RELEASE="0"
if [[ -n "${MACOS_SIGN_IDENTITY}" ]]; then
    SIGN_RELEASE="1"
fi

case "${NOTARIZE_MACOS}" in
    auto)
        if has_notary_credentials; then
            NOTARIZE_RELEASE="1"
        else
            NOTARIZE_RELEASE="0"
        fi
        ;;
    1)
        NOTARIZE_RELEASE="1"
        ;;
    0)
        NOTARIZE_RELEASE="0"
        ;;
    *)
        echo "Unsupported NOTARIZE_MACOS=${NOTARIZE_MACOS}. Use 'auto', '0', or '1'." >&2
        exit 1
        ;;
esac

if [[ "${NOTARIZE_RELEASE}" == "1" && "${SIGN_RELEASE}" != "1" ]]; then
    echo "macOS notarization requires MACOS_SIGN_IDENTITY so the app bundle can be signed first." >&2
    exit 1
fi

if [[ "${NOTARIZE_RELEASE}" == "1" ]] && ! has_notary_credentials; then
    echo "macOS notarization was requested, but no notarytool profile or Apple ID credentials are configured." >&2
    exit 1
fi

"${PYTHON_BIN}" -m pip install --upgrade pip
"${PYTHON_BIN}" -m pip install -e . pyinstaller pytest

if [[ "${RUN_TESTS}" == "1" ]]; then
    QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}" "${PYTHON_BIN}" -m pytest -q
fi

bash scripts/generate_macos_icon.sh
"${PYTHON_BIN}" -m PyInstaller --noconfirm --clean packaging/pyinstaller/snakesh.spec

if [[ ! -d "dist/SnakeSh.app" ]]; then
    echo "PyInstaller did not produce dist/SnakeSh.app." >&2
    exit 1
fi

APP_PATH="dist/SnakeSh.app"

if [[ "${SIGN_RELEASE}" == "1" ]]; then
    MACOS_SIGN_IDENTITY="${MACOS_SIGN_IDENTITY}" APP_PATH="${APP_PATH}" bash scripts/sign_macos.sh
fi

if [[ "${NOTARIZE_RELEASE}" == "1" ]]; then
    TARGET_PATH="${APP_PATH}" \
    NOTARYTOOL_PROFILE="${NOTARYTOOL_PROFILE}" \
    APPLE_ID="${APPLE_ID}" \
    TEAM_ID="${TEAM_ID}" \
    APP_SPECIFIC_PASSWORD="${APP_SPECIFIC_PASSWORD}" \
    bash scripts/notarize_macos.sh
fi

if [[ -z "${ARTIFACT_SUFFIX}" ]]; then
    if [[ "${SIGN_RELEASE}" == "1" ]]; then
        ARTIFACT_SUFFIX=""
    else
        ARTIFACT_SUFFIX="-unsigned"
    fi
fi

APP_PATH="${APP_PATH}" \
PACKAGE_ZIP="${PACKAGE_ZIP}" \
PACKAGE_DMG="${PACKAGE_DMG}" \
ARTIFACT_SUFFIX="${ARTIFACT_SUFFIX}" \
TARGET_ARCH="${TARGET_ARCH}" \
VOLUME_NAME="${VOLUME_NAME}" \
bash scripts/package_macos.sh

ZIP_OUTPUT="dist/SnakeSh-macos-${TARGET_ARCH}${ARTIFACT_SUFFIX}.zip"
DMG_OUTPUT="dist/SnakeSh-macos-${TARGET_ARCH}${ARTIFACT_SUFFIX}.dmg"
declare -a RELEASE_ARTIFACTS=()

if [[ "${PACKAGE_ZIP}" == "1" ]]; then
    RELEASE_ARTIFACTS+=("${ZIP_OUTPUT}")
fi

if [[ "${PACKAGE_DMG}" == "1" ]]; then
    if [[ "${SIGN_RELEASE}" == "1" ]]; then
        MACOS_SIGN_IDENTITY="${MACOS_SIGN_IDENTITY}" SIGN_TARGET="dmg" TARGET_PATH="${DMG_OUTPUT}" bash scripts/sign_macos.sh
    fi
    if [[ "${NOTARIZE_RELEASE}" == "1" ]]; then
        TARGET_PATH="${DMG_OUTPUT}" \
        NOTARYTOOL_PROFILE="${NOTARYTOOL_PROFILE}" \
        APPLE_ID="${APPLE_ID}" \
        TEAM_ID="${TEAM_ID}" \
        APP_SPECIFIC_PASSWORD="${APP_SPECIFIC_PASSWORD}" \
        bash scripts/notarize_macos.sh
    fi
    RELEASE_ARTIFACTS+=("${DMG_OUTPUT}")
fi

if [[ "${PACKAGE_CHECKSUMS}" == "1" && ${#RELEASE_ARTIFACTS[@]} -gt 0 ]]; then
    bash scripts/make_checksums.sh dist/SHA256SUMS.txt "${RELEASE_ARTIFACTS[@]}"
fi

echo "macOS release build complete."
