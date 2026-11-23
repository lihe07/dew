# Two places the agent can play:
#   your game  - the Stardew Valley you start yourself (with the mod installed)
#   sandbox    - a private copy of the game for testing: its own home folder, mods,
#                and saves, on a hidden display; your game and saves are never touched

sandbox_session := justfile_directory() / ".tools/sandbox/Mods/StardewBridge/session.json"
# the agent's memory, diary, and transcripts land where you ran `just`, not in agent/
data := invocation_directory() / "stardew-data"

# List the commands
default:
    @just --list --unsorted

# --- your game ---------------------------------------------------------------

# Let the agent play your running game, e.g. `just play --days 3`
play *args:
    cd agent && STARDEW_DATA="{{data}}" uv run stardew play {{args}}

# List the saves in your running game
saves:
    cd agent && uv run stardew saves

# Put the mod into your game's Mods folder (then restart the game)
install:
    scripts/build.sh --install

# --- sandbox -----------------------------------------------------------------

# Sandbox: `just sandbox play [--save FOLDER ...]`, `just sandbox stop`, `just sandbox log`, `just sandbox saves`
sandbox command="play" *args:
    #!/usr/bin/env bash
    set -euo pipefail
    case "{{command}}" in
      play)  scripts/sandbox.sh ready; cd agent && STARDEW_SESSION={{sandbox_session}} STARDEW_DATA="{{data}}" uv run stardew play {{args}} ;;
      saves) scripts/sandbox.sh ready; cd agent && STARDEW_SESSION={{sandbox_session}} uv run stardew saves ;;
      start) scripts/sandbox.sh ready ;;
      stop)  scripts/sandbox.sh stop ;;
      log)   scripts/sandbox.sh log {{args}} ;;
      *)     echo "just sandbox play|saves|start|stop|log" >&2; exit 2 ;;
    esac

# --- tests -------------------------------------------------------------------

# Offline tests: no game needed
test:
    cd agent && uv run --group dev pytest -q -rs

# Offline tests plus the ones that drive a real game, in the sandbox (started if needed)
test-live:
    scripts/sandbox.sh ready
    cd agent && STARDEW_SESSION={{sandbox_session}} uv run --group dev pytest -q -rs

# --- setup -------------------------------------------------------------------

# Build the mod into dist/
build:
    scripts/build.sh

# Fetch CPython for WebAssembly, which the agent's scripts run in (once; checked by its SHA-256)
setup-wasm:
    scripts/fetch-python-wasi.sh
