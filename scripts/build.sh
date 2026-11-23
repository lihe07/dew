#!/usr/bin/env bash
# Build the mod into dist/StardewBridge. With --install, also copy it into the game's Mods folder.
set -euo pipefail
source "$(dirname "$0")/env.sh"
dotnet build "$ROOT/bridge/StardewBridge.csproj" -c Release -v quiet -p:GamePath="$STARDEW_PATH"
mkdir -p "$ROOT/dist/StardewBridge"
cp "$ROOT/bridge/bin/Release/net6.0/StardewBridge.dll" "$ROOT/bridge/manifest.json" "$ROOT/dist/StardewBridge/"
echo "built dist/StardewBridge"
if [ "${1:-}" = "--install" ]; then
  mkdir -p "$STARDEW_PATH/Mods/StardewBridge"
  cp "$ROOT/dist/StardewBridge/"{StardewBridge.dll,manifest.json} "$STARDEW_PATH/Mods/StardewBridge/"
  echo "installed to $STARDEW_PATH/Mods/StardewBridge"
fi
