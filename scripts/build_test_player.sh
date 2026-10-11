#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p native/build
xcrun clang++ -std=c++17 -O2 -Wall -Wextra -Werror -fobjc-arc -mmacosx-version-min=14.2 \
  -framework Foundation -framework AVFoundation -framework CoreAudio -framework AudioToolbox \
  tests/player.mm -o native/build/VoiceRelayTestPlayer
codesign --force --sign - native/build/VoiceRelayTestPlayer
