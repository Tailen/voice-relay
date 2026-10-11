#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
.venv/bin/ruff check voicerelay scripts tests native/app/worker.py
.venv/bin/ruff format --check voicerelay scripts tests native/app/worker.py
.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
mkdir -p native/build
for test in ring timing; do
  xcrun clang++ -std=c++17 -O2 -Wall -Wextra -Werror "native/app/tests/${test}_test.cpp" -o "native/build/${test}_test"
  "native/build/${test}_test"
done
