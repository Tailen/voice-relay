#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ "$(uname -s)" != Darwin || "$(uname -m)" != arm64 ]]; then
  echo 'VoiceRelay requires macOS on Apple Silicon.' >&2
  exit 1
fi
command -v uv >/dev/null || { echo 'Install uv: brew install uv' >&2; exit 1; }
xcrun --find clang++ >/dev/null || { echo 'Install Apple Command Line Tools: xcode-select --install' >&2; exit 1; }
uv sync --frozen
.venv/bin/python scripts/setup_runtime.py "$@"
