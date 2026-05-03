#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "macOS certificate import must run on macOS." >&2
    exit 1
fi

PYTHON_BIN="${PYTHON_BIN:-python3}"
MACOS_CERT_BASE64="${MACOS_CERT_BASE64:?MACOS_CERT_BASE64 is required}"
MACOS_CERT_PASSWORD="${MACOS_CERT_PASSWORD:?MACOS_CERT_PASSWORD is required}"
MACOS_KEYCHAIN_PASSWORD="${MACOS_KEYCHAIN_PASSWORD:-snakesh-temporary-keychain-password}"
MACOS_KEYCHAIN_NAME="${MACOS_KEYCHAIN_NAME:-snakesh-signing.keychain-db}"

CERT_PATH="${RUNNER_TEMP:-${ROOT_DIR}/build}/snakesh-signing-cert.p12"
KEYCHAIN_PATH="${HOME}/Library/Keychains/${MACOS_KEYCHAIN_NAME}"

mkdir -p "$(dirname "${CERT_PATH}")"
MACOS_CERT_PATH="${CERT_PATH}" "${PYTHON_BIN}" - <<'PY'
import base64
import os
from pathlib import Path

Path(os.environ["MACOS_CERT_PATH"]).write_bytes(base64.b64decode(os.environ["MACOS_CERT_BASE64"]))
PY

security create-keychain -p "${MACOS_KEYCHAIN_PASSWORD}" "${KEYCHAIN_PATH}" 2>/dev/null || true
security set-keychain-settings -lut 21600 "${KEYCHAIN_PATH}"
security unlock-keychain -p "${MACOS_KEYCHAIN_PASSWORD}" "${KEYCHAIN_PATH}"
security import "${CERT_PATH}" -k "${KEYCHAIN_PATH}" -P "${MACOS_CERT_PASSWORD}" -T /usr/bin/codesign -T /usr/bin/security -T /usr/bin/xcrun
security list-keychains -d user -s "${KEYCHAIN_PATH}" $(security list-keychains -d user | tr -d '"')
security default-keychain -d user -s "${KEYCHAIN_PATH}"
security set-key-partition-list -S apple-tool:,apple: -s -k "${MACOS_KEYCHAIN_PASSWORD}" "${KEYCHAIN_PATH}"
security find-identity -v "${KEYCHAIN_PATH}"

if [[ -n "${GITHUB_ENV:-}" ]]; then
    {
        echo "MACOS_KEYCHAIN_PATH=${KEYCHAIN_PATH}"
        echo "MACOS_CERT_PATH=${CERT_PATH}"
    } >> "${GITHUB_ENV}"
fi

echo "Imported macOS signing certificate into ${KEYCHAIN_PATH}"
