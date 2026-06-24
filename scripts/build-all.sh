#!/bin/bash
# Build all components for the WeChatAgent installer
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$SCRIPT_DIR/.."

echo "=== Step 1: Build wx-cli ==="
cd "$ROOT_DIR/wx-cli"
cargo build --release --target x86_64-pc-windows-gnu
mkdir -p "$ROOT_DIR/sightflow-desktop-agent/resources/wx-cli"
cp target/x86_64-pc-windows-gnu/release/wx.exe "$ROOT_DIR/sightflow-desktop-agent/resources/wx-cli/"
echo "✅ wx.exe copied"

echo "=== Step 2: Build glue-layer ==="
bash "$SCRIPT_DIR/build-glue-layer.sh"

echo "=== Step 3: Copy WeChat installer ==="
mkdir -p "$ROOT_DIR/sightflow-desktop-agent/resources/wechat"
cp "$ROOT_DIR/WeChatWin_4.1.9.exe" "$ROOT_DIR/sightflow-desktop-agent/resources/wechat/"
echo "✅ WeChat installer copied"

echo "=== Step 4: Build SightFlow ==="
cd "$ROOT_DIR/sightflow-desktop-agent"
npm run build:win

echo "✅ All done! Installer at: sightflow-desktop-agent/dist/"
