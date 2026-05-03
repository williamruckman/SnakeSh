#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

RUN_TESTS="${RUN_TESTS:-0}"
export RUN_TESTS
export BUILD_SCOPE="release"
export USE_CONTAINER="1"

bash scripts/build_linux.sh "$@"
