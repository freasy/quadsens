#!/bin/sh
# Build a single-file QuadSens binary into dist/quadsens.
# Needs Python 3.10+ with the venv module; everything else is fetched from PyPI
# into ./.venv (no root, nothing installed system-wide).
set -eu
cd "$(dirname "$0")"
PYTHON="${PYTHON:-python3}"
[ -x .venv/bin/python ] || "$PYTHON" -m venv .venv
.venv/bin/pip install --quiet --disable-pip-version-check "PySide6-Essentials>=6.5" pyinstaller
.venv/bin/pyinstaller --noconfirm --clean --onefile --windowed --name quadsens \
    --paths . --exclude-module PyQt6 --exclude-module tkinter \
    packaging/entry.py
echo "Built: $(pwd)/dist/quadsens"
