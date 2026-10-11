#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -x .venv/bin/python ]]; then
  echo 'Runtime missing. Run: bash scripts/setup.sh' >&2
  exit 1
fi
.venv/bin/python scripts/check_runtime.py
app="$PWD/native/build/VoiceRelay.app"
mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources"
xcrun clang++ -std=c++17 -O2 -Wall -Wextra -Werror -Wno-unused-function -Wno-unused-parameter -fobjc-arc -mmacosx-version-min=14.2 \
  -framework AppKit -framework CoreAudio -framework AudioToolbox -framework UniformTypeIdentifiers \
  native/app/Main.mm -o "$app/Contents/MacOS/VoiceRelay"
.venv/bin/python scripts/package_app.py
codesign --force --sign - "$app"
codesign --verify --strict "$app"
printf '%s\n' "$app"
