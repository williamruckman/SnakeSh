#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

checksum_command() {
    if command -v sha256sum >/dev/null 2>&1; then
        printf 'sha256sum\n'
        return 0
    fi
    if command -v shasum >/dev/null 2>&1; then
        printf 'shasum -a 256\n'
        return 0
    fi

    echo "Unable to find a SHA-256 tool. Install sha256sum or shasum." >&2
    exit 1
}

OUTPUT_FILE="${1:-dist/SHA256SUMS.txt}"
shift || true

declare -a FILES=()
if [[ $# -gt 0 ]]; then
    FILES=("$@")
else
    while IFS= read -r path; do
        FILES+=("${path}")
    done < <(find dist -maxdepth 1 -type f \( -name "*.AppImage" -o -name "*-Setup.exe" -o -name "*.zip" -o -name "*.dmg" \) | sort)
fi

if [[ ${#FILES[@]} -eq 0 ]]; then
    echo "No files selected for checksum generation." >&2
    exit 1
fi

mkdir -p "$(dirname "${OUTPUT_FILE}")"
: > "${OUTPUT_FILE}"
CHECKSUM_CMD="$(checksum_command)"
for file in "${FILES[@]}"; do
    if [[ ! -f "${file}" ]]; then
        echo "Missing file: ${file}" >&2
        exit 1
    fi
    ${CHECKSUM_CMD} "${file}" >> "${OUTPUT_FILE}"
done

echo "Checksums written: ${OUTPUT_FILE}"
