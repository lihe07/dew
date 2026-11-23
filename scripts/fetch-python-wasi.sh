#!/usr/bin/env bash
# Fetch CPython for WASI, which the planner's scripts run in, into .tools/python-wasi/x, and compile it for wasmtime once.
# The build is Brett Cannon's cpython-wasi-build; the checksum pins the exact file.
set -euo pipefail
source "$(dirname "$0")/env.sh"
VERSION=3.15.2
ASSET=python-$VERSION-wasi_sdk-33.zip
SHA256=b8a63559c56760bbdc310dedb74fd69d10bcbafae26b705bea0ad353ad256888
URL=https://github.com/brettcannon/cpython-wasi-build/releases/download/v$VERSION/$ASSET
DEST="$ROOT/.tools/python-wasi"
mkdir -p "$DEST"
if [ ! -f "$DEST/x/python.wasm" ]; then
  if ! echo "$SHA256  $DEST/python.zip" | sha256sum -c --status 2>/dev/null; then
    echo "downloading $URL"
    curl -fL --retry 3 -o "$DEST/python.zip.part" "$URL"
    echo "$SHA256  $DEST/python.zip.part" | sha256sum -c --quiet
    mv "$DEST/python.zip.part" "$DEST/python.zip"
  fi
  rm -rf "$DEST/x.part"
  unzip -q "$DEST/python.zip" -d "$DEST/x.part"
  rm -rf "$DEST/x"
  mv "$DEST/x.part" "$DEST/x"
fi
cd "$ROOT/agent" && uv run python -I stardew_agent/wasm_host.py --compile
echo "CPython $VERSION for WASI is ready in $DEST/x"
