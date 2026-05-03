#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "macOS icon generation requires Darwin." >&2
    exit 1
fi

SOURCE_ICON="${SOURCE_ICON:-src/snakesh/assets/snakesh-icon.png}"
ICONSET_DIR="${ICONSET_DIR:-build/macos/SnakeSh.iconset}"
OUTPUT_ICNS="${OUTPUT_ICNS:-build/macos/SnakeSh.icns}"

if [[ ! -f "${SOURCE_ICON}" ]]; then
    echo "Missing source icon: ${SOURCE_ICON}" >&2
    exit 1
fi

mkdir -p "$(dirname "${ICONSET_DIR}")"
rm -rf "${ICONSET_DIR}"
mkdir -p "${ICONSET_DIR}"

generate_icon() {
    local size="$1"
    local output_name="$2"
    sips -z "${size}" "${size}" "${SOURCE_ICON}" --out "${ICONSET_DIR}/${output_name}" >/dev/null
}

generate_icon 16 icon_16x16.png
generate_icon 32 icon_16x16@2x.png
generate_icon 32 icon_32x32.png
generate_icon 64 icon_32x32@2x.png
generate_icon 128 icon_128x128.png
generate_icon 256 icon_128x128@2x.png
generate_icon 256 icon_256x256.png
generate_icon 512 icon_256x256@2x.png
generate_icon 512 icon_512x512.png
generate_icon 1024 icon_512x512@2x.png

mkdir -p "$(dirname "${OUTPUT_ICNS}")"
rm -f "${OUTPUT_ICNS}"
iconutil -c icns "${ICONSET_DIR}" -o "${OUTPUT_ICNS}"

echo "Generated macOS icon: ${OUTPUT_ICNS}"
