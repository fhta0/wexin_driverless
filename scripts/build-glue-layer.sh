#!/bin/bash
# Build glue-layer.exe using PyInstaller
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
GLUE_LAYER_DIR="$SCRIPT_DIR/../glue-layer"
OUTPUT_DIR="$SCRIPT_DIR/../sightflow-desktop-agent/resources/glue-layer"

echo "=== Building glue-layer ==="
cd "$GLUE_LAYER_DIR"

pip install pyinstaller cryptography pywin32 2>/dev/null

pyinstaller --onefile \
  --hidden-import=cryptography \
  --hidden-import=cffi \
  --hidden-import=_cffi_backend \
  --hidden-import=win32crypt \
  --hidden-import=win32security \
  --hidden-import=win32gui \
  --hidden-import=win32process \
  --hidden-import=win32api \
  --hidden-import=win32con \
  main.py --name glue-layer

mkdir -p "$OUTPUT_DIR"
cp dist/glue-layer.exe "$OUTPUT_DIR/"
echo "✅ glue-layer.exe → $OUTPUT_DIR/"
