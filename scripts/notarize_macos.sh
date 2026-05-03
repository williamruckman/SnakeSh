#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "macOS notarization must run on macOS." >&2
    exit 1
fi

TARGET_PATH="${TARGET_PATH:?TARGET_PATH is required}"
NOTARYTOOL_PROFILE="${NOTARYTOOL_PROFILE:-}"
APPLE_ID="${APPLE_ID:-}"
TEAM_ID="${TEAM_ID:-}"
APP_SPECIFIC_PASSWORD="${APP_SPECIFIC_PASSWORD:-}"

if [[ ! -e "${TARGET_PATH}" ]]; then
    echo "Missing notarization target: ${TARGET_PATH}" >&2
    exit 1
fi

declare -a SUBMIT_ARGS=(xcrun notarytool submit "${TARGET_PATH}" --wait)
if [[ -n "${NOTARYTOOL_PROFILE}" ]]; then
    SUBMIT_ARGS+=(--keychain-profile "${NOTARYTOOL_PROFILE}")
else
    : "${APPLE_ID:?APPLE_ID is required when NOTARYTOOL_PROFILE is unset}"
    : "${TEAM_ID:?TEAM_ID is required when NOTARYTOOL_PROFILE is unset}"
    : "${APP_SPECIFIC_PASSWORD:?APP_SPECIFIC_PASSWORD is required when NOTARYTOOL_PROFILE is unset}"
    SUBMIT_ARGS+=(--apple-id "${APPLE_ID}" --team-id "${TEAM_ID}" --password "${APP_SPECIFIC_PASSWORD}")
fi

"${SUBMIT_ARGS[@]}"
xcrun stapler staple "${TARGET_PATH}"

echo "Notarized and stapled: ${TARGET_PATH}"
