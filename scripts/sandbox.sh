#!/usr/bin/env bash
# Run the game in an isolated sandbox: separate home (so saves, settings, and Steam are never touched),
# separate Mods folder, virtual display. Usage: sandbox.sh start|stop|log|ready
#   ready: start it unless it's running, then wait until the mod is listening
set -euo pipefail
source "$(dirname "$0")/env.sh"
BOX="${SANDBOX:-$ROOT/.tools/sandbox}"
export STARDEW_BRIDGE_PORT="${STARDEW_BRIDGE_PORT:-8738}"
case "${1:-start}" in
  start)
    # never run beside a real game: both share the install
    if pgrep -x StardewModdingA >/dev/null; then echo "a game is already running" >&2; exit 1; fi
    mkdir -p "$BOX/home/.config" "$BOX/Mods"
    rm -rf "$BOX/Mods/StardewMcp" "$BOX/Mods/StardewBridge"; cp -r "$ROOT/dist/StardewBridge" "$BOX/Mods/"
    cd "$STARDEW_PATH"
    env -u WAYLAND_DISPLAY -u DISPLAY HOME="$BOX/home" XDG_CONFIG_HOME="$BOX/home/.config" XDG_DATA_HOME="$BOX/home/.local/share" \
      SMAPI_MODS_PATH="$BOX/Mods" SMAPI_NO_TERMINAL=true SDL_VIDEODRIVER=x11 SDL_AUDIODRIVER=dummy ALSOFT_DRIVERS=null LIBGL_ALWAYS_SOFTWARE=1 \
      setsid xvfb-run -a -s "-screen 0 1280x720x24" ./StardewModdingAPI --no-terminal > "$BOX/out.log" 2>&1 &
    echo $! > "$BOX/pid"
    ;;
  stop)
    # only ever signals our own process group, by its recorded ID
    group="$(cat "$BOX/pid")"
    kill -- -"$group" 2>/dev/null || true
    for _ in 1 2 3 4 5 6; do kill -0 -- -"$group" 2>/dev/null || exit 0; sleep 1; done
    kill -9 -- -"$group" 2>/dev/null || true
    ;;
  log) tail -n "${2:-40}" "$BOX/out.log" ;;
  ready)
    session="$BOX/Mods/StardewBridge/session.json"
    if ! { [ -f "$BOX/pid" ] && kill -0 -- -"$(cat "$BOX/pid")" 2>/dev/null; }; then
      rm -f "$session"
      "$(dirname "$0")/build.sh" >/dev/null  # so it starts with the current mod
      "$0" start
    fi
    for _ in $(seq 90); do
      [ -f "$session" ] && exit 0
      sleep 1
    done
    echo "the sandbox didn't come up; see: just sandbox log" >&2; exit 1
    ;;
  *) echo "usage: sandbox.sh start|stop|log|ready" >&2; exit 2 ;;
esac

# connect with: STARDEW_SESSION=.tools/sandbox/Mods/StardewBridge/session.json
