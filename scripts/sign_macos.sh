#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "macOS signing must run on macOS." >&2
    exit 1
fi

MACOS_SIGN_IDENTITY="${MACOS_SIGN_IDENTITY:?MACOS_SIGN_IDENTITY is required}"
SIGN_TARGET="${SIGN_TARGET:-app}"
APP_PATH="${APP_PATH:-dist/SnakeSh.app}"
TARGET_PATH="${TARGET_PATH:-}"
ENTITLEMENTS_PATH="${ENTITLEMENTS_PATH:-packaging/macos/entitlements.plist}"

sign_nested_code() {
    local app_path="$1"
    while IFS= read -r component; do
        [[ -z "${component}" ]] && continue
        [[ "${component}" == "${app_path}" ]] && continue
        codesign --force --sign "${MACOS_SIGN_IDENTITY}" --timestamp "${component}"
    done < <(
        find "${app_path}/Contents" \
            \( -type f \( -perm -111 -o -name "*.dylib" -o -name "*.so" \) -o -type d \( -name "*.framework" -o -name "*.app" -o -name "*.bundle" \) \) \
            -print | awk '{ print length, $0 }' | sort -rn | cut -d" " -f2-
    )
}

if [[ "${SIGN_TARGET}" == "app" ]]; then
    if [[ ! -d "${APP_PATH}" ]]; then
        echo "Missing app bundle: ${APP_PATH}" >&2
        exit 1
    fi
    sign_nested_code "${APP_PATH}"
    declare -a APP_SIGN_ARGS=(codesign --force --sign "${MACOS_SIGN_IDENTITY}" --timestamp --options runtime)
    if [[ -f "${ENTITLEMENTS_PATH}" ]]; then
        APP_SIGN_ARGS+=(--entitlements "${ENTITLEMENTS_PATH}")
    fi
    APP_SIGN_ARGS+=("${APP_PATH}")
    "${APP_SIGN_ARGS[@]}"
    codesign --verify --deep --strict --verbose=2 "${APP_PATH}"
    echo "Signed macOS app bundle: ${APP_PATH}"
    exit 0
fi

if [[ "${SIGN_TARGET}" == "dmg" ]]; then
    if [[ -z "${TARGET_PATH}" || ! -f "${TARGET_PATH}" ]]; then
        echo "Missing dmg target: ${TARGET_PATH}" >&2
        exit 1
    fi
    codesign --force --sign "${MACOS_SIGN_IDENTITY}" --timestamp "${TARGET_PATH}"
    codesign --verify --verbose=2 "${TARGET_PATH}"
    echo "Signed macOS dmg: ${TARGET_PATH}"
    exit 0
fi

echo "Unsupported SIGN_TARGET=${SIGN_TARGET}. Use 'app' or 'dmg'." >&2
exit 1
