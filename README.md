# Stardew Agent

An AI agent that plays Stardew Valley 1.6 by the real rules: it walks everywhere, tools cost energy, shops keep hours, and it passes out at 2am.

```
stardew play (Python)                                          Stardew Valley + SMAPI
  Director  days, diary, memory, bedtime                        Stardew Bridge mod (C#)
  Brain     planner model: remember, forget, run, wait◀── socket ──▶  attach input step read frame path follow cheat
  Runner    plays the planner's Python scripts                    + a journal of everything shown on screen
  Skills    walk, swing, click, travel, fish...
```

The mod is a small, stable instruction set. Everything game-specific lives in Python, so new abilities rarely need mod changes.

## Setup

1. Put your Respan key in `.env` as `RESPAN_API_KEY=...` (git-ignored; only the agent reads it).
2. `just install` builds the mod and copies it to the game's Mods folder. If the game isn't in Steam's default library, put `STARDEW_PATH="/path/to/Stardew Valley"` in `.env`.
3. `just setup-wasm` fetches the WebAssembly Python that the planner's scripts run in (see The script sandbox).
4. Start the game through SMAPI and load a save, or let the agent load one with `--save FOLDER` (see `just saves`) or start one with `--new NAME`.
5. `just play --days 1`

Commands (`just` lists them). The agent can play in two places:

- **your game**: the Stardew Valley you start yourself, with the mod installed.
- **the sandbox**: a private copy for testing, with its own home folder, mods, and saves, on a hidden display and without Steam. Your game and saves are never touched. It starts by itself when a sandbox command needs it, and refuses to start while your game is running, since both use the same install.

| | Your game | Sandbox |
|---|---|---|
| let the agent play | `just play ARGS` | `just sandbox play ARGS` (add `--show` to watch it in a window, muted) |
| list saves | `just saves` | `just sandbox saves` |
| other | `just install` (put the mod in your game) | `just sandbox stop`, `just sandbox log` |

Tests: `just test` (offline: fakes for the game and the model, no network) and `just test-live` (also the tests that drive a real game, in the sandbox). Setup: `just build` (the mod into `dist/`), `just setup-wasm` (once).

## Flags

`stardew play` (`just play ...`):

| Flag | Meaning |
|---|---|
| `--goal TEXT` | what to aim for (default: build a thriving farm) |
| `--days N` | days to play (default 1) |
| `--save FOLDER` | load this save if none is loaded |
| `--new NAME` | start a new game if none is loaded |
| `--model MODEL` | planner model, any gateway model (default `deepseek-v4.1-flash`, or `$STARDEW_MODEL`) |
| `--summary-model MODEL` | model for the daily diary and context compaction (default: `--model`, whose prompt cache makes it cheapest) |
| `--memory-model MODEL` | model which tidies the memory each night (default `hy4-flash`; `none` turns it off) |
| `--data DIR` | where the agent keeps its data (default `$STARDEW_DATA`, else `./stardew-data`; `just` sets it to `stardew-data/` where you ran it) |
| `--turns N` | most model turns per day (default 60) |
| `--speed N` | how fast the world runs, single-player only (default 1; up to 16, but faster makes combat reflexes less reliable) |
| `--no-web` | no web lookups |
| `--plain` | print a line per action instead of the full-screen view (used anyway when the output isn't a terminal) |
| `--verbose` | with `--plain`: full thoughts, reports, results, and scripts |
| `--no-map` | with `--plain`: don't print the map on arriving somewhere |

`stardew saves` lists saves.

## How it plays

The world never pauses: it runs while the planner thinks, as in multiplayer. In single-player, `--speed` runs it up to 16 times faster; game time, energy, and odds are unchanged. There are three layers.

**1. The planner** (`brain.py`, `script.py`). A model with four tools:

- `run`: start a Python script written against an SDK (`go`, `harvest`, `water`, `plant`, `fish`, `mine`, `buy`, `talk`, ...). It runs in the background while the model thinks and replaces any script already running. `stop_if` lists situations to watch for; `finish_current` lets the running action end first.
- `wait`: end the turn; the model is woken when the script ends, raises, or asks.
- `remember(key, value)`: set or replace a fact in its memory; `forget(key)`: remove one (see Memory).

The planner also looks through scripts: SDK views (`state`, `look_map`, `find`, `tile_info`, `exits`, `people`, `quests`, `recipes`, `item_info`, `screenshot`, `web`) return text or data for the script to print, and the wake-up when the script ends carries its output. A screenshot reaches the planner as an image in that wake-up.

The model is woken when a script ends, raises, or calls `ask(question)`, and when you steer. Old screenshots are dropped and the context is summarized once it passes about 70k tokens.

**2. span-02** (`decide.py`, `combat.py`), a fast decision model on the Respan gateway (`/v1/decisions`). In scripts, `check(situation)` returns True or False and `pick(description, options)` returns one option; both raise `Unsure` below 0.6 confidence. It reads plain facts (time, minutes until 2am, weather, location, health, energy, money, free slots, what is held, the last result, recent dialogue) and compares but can't do arithmetic. The same judge decides `stop_if`. In the mines, `combat.Tactics` asks the same judge to choose between options that are all already safe, such as fighting nearby monsters or taking a visible ladder; below 0.4 confidence it takes the safe default.

**3. Deterministic routines** (no model):

- Walking: paths go around people and animals and keep away from monsters; a walk stops if someone steps into the way, and `travel` retries.
- Combat reflexes (`combat.Fight`): one decision per game tick: approach, swing with the right tool, back off when too close, eat when health is below half, retreat below a quarter.
- The guard while idle (`combat.keep_busy`): when no script runs, the farmer fights nearby monsters, eats or flees when hurt, and in the mines breaks rocks next to it.
- Safety: before each action the runner raises `LowHealth` (under 30%), `LowEnergy` (under 15), and `TimeToGoHome` (after 6pm, when the walk home plus 45 minutes is about all the time left), each once until it clears. Eating, sleeping, and going home are exempt. `TimeToGoHome` aims for home by midnight.
- If the farmer is still out at 2am, it passes out, as in the game; the director notices and gets it through the night. Level-up profession choices go to the model.

## What a script looks like

From `agent/stardew_agent/prompt.md`:

```python
try:
    go("SeedShop")
    buy("Parsnip Seeds", 15)
except Closed:
    go("JojaMart")
    buy("Parsnip Seeds", 15)
go("Farm")
plant("Parsnip Seeds", area=[60, 15, 64, 17])
water()
try:
    go("Mine")
    while minutes_until_2am() > 240:
        mine(2)
        if check("a ladder down was found on a floor with lots of ore"):
            log("good floor")
except (LowHealth, TimeToGoHome):
    go("Farm")
```

Each SDK call blocks until its action ends and returns the result. An uncaught exception ends the script and wakes the planner with its type, the line, the script's logs, and each action's result.

```
Surprise
├── ActionFailed(reason)
│   ├── Unreachable
│   ├── NotEnough
│   └── Closed
├── Unsure
├── Danger
│   ├── LowHealth
│   ├── LowEnergy
│   └── TimeToGoHome
└── Happened
    ├── Question(choices)
    ├── Cutscene
    └── StopIf
```

## What you see

In a terminal, a full-screen view: the day's transcript on the left (the planner's reasoning, each script's goal, one line per action with its result, items, and places walked through, `check`/`pick` decisions, safety stops), and on the right the running script with its current line marked and the farmer's status. Tabs along the top switch between days (←/→); ↑/↓ scroll; a past day shows its diary. The save's last 7 days are loaded from its transcripts at start, so they can be looked through after a restart. Piped or with `--plain`, the same lines are printed one after another.

## Steering from the terminal

Type a line in the input box and press Enter: the script and its action stop, and the planner is woken with `PLAYER: your text`. `/later TEXT` queues the message for the next time it wakes, without interrupting. Input is only read when stdin is a terminal.

In the game, **F8** cancels the current action and gives you control; F8 again hands it back (the agent tells the planner you were there).

## Memory

- **Memory** (`memory.py`): the model's one lasting memory, facts by short dotted key (`shop.pierre.hours`, `plan.tomorrow`, `farm.chest`), set with `remember` and removed with `forget`. Keys are at most 60 characters, values 500, and there are at most 150 keys (more is refused, telling the model to forget stale ones). All of it is shown every morning under `# Memory`, sorted by key. It's saved in `memory.json`, written atomically.
- **Consolidation**: each night, after the diary, `--memory-model` (default `hy4-flash`, thinking disabled) gets the memory and the day's diary and returns the whole new memory as JSON: duplicates merged, stale facts dropped, contradicted values fixed, nothing invented. It's applied only if it's a JSON object of text to text within the limits that keeps at least half the keys; the old memory goes to `memory.json.bak`. Otherwise the memory stays as it was and the reason is shown. The display shows a line like `memory consolidated: 42 → 37 keys (merged 3, dropped 2, changed 1)`, and with `--verbose` each changed key.
- **Diary**: at the end of each day the summary model writes under 120 words, appended to `diary.jsonl`. The last 7 days are shown each morning. `--summary-model` picks which model writes it; the same model also compacts the context mid-day.
- Nothing else carries over between days. At the turn limit the model gets three more turns, with only `remember` and `forget`, and the farmer is put to bed.
- An old `notes.md` (from the `note` tool, now gone) is imported once into the key `notes` and renamed `notes.md.imported`.

## Storage

Each save has a folder, `<data>/<farm name>_<game id>/`, where `<data>` is `--data DIR`, else `$STARDEW_DATA`, else `stardew-data/` in the working directory. The `just` recipes set `STARDEW_DATA` to `stardew-data/` in the directory you ran `just` from (git-ignored). A relative `--data` is taken from `agent/` under `just`, so give it an absolute path there.

```
stardew-data/Farm_123456789/
  memory.json          the facts (memory.json.bak: before the last consolidation)
  diary.jsonl          one summary a day
  days/0001-spring-04.jsonl   every step shown that day: {"kind", "text", "ok", "data"}, images left out
  context.json         the planner's messages for the current game day, saved after every turn (screenshots dropped)
  screenshots/         every screenshot the planner takes, full size
```

On start, if `context.json` is from the current game day (the last run stopped partway through it), the planner gets its messages back and is told it was restarted and should continue the day, with its memory and the current status; otherwise the day starts fresh with the morning report. Data from before (`~/.local/share/stardew-agent/<save>/`) is moved into the new folder once, if that folder doesn't exist yet.

- `sleep` saves the game. Use a save you don't mind it playing. When the agent exits it leaves the game paused in the menu.

## The mod

Instructions over a length-prefixed JSON socket on 127.0.0.1, authenticated with a token in `Mods/StardewBridge/session.json` (readable only by you):

| Instruction | Does |
|---|---|
| `attach` | take control: speed (1 to 16, single-player only), heartbeat timeout |
| `input` | which keys and mouse buttons are down, cursor in tile/UI/screen space, wheel, typed text |
| `step` | run up to n ticks, stopping early on named conditions |
| `read` | read-only expressions and queries, e.g. `{"from": "location.terrain", "where": "thirsty"}` |
| `frame` | the current screen |
| `path` | pathfinding with the game's collision rules; `avoid` (default on) goes around people and animals and keeps away from monsters |
| `follow` | walk a path tick by tick, stopping as "blocked" if a person, animal, or monster steps onto the next tiles (`stop_for`: all, people, none) |
| `cheat` | allowed shortcuts; only `catch_fish` |

The agent plays through virtual input only, like a person: no warping or spawning, and reads can't call methods or write. The one cheat is `catch_fish`, which skips the fishing minigame by winning it through the game's own catch path, so the fish, quality, experience, and treasure are as normal. Requests sent with `"lane": "read"` (`read`, `path`) are answered at once, even while an action runs. A journal records dialogue, speech bubbles, letters, messages, item changes, and events, and every reply carries what's new.

## The script sandbox

The planner reads in-game text and web pages, so its scripts are treated as untrusted. They run as ordinary Python, but inside WebAssembly:

- **Isolation.** Each script gets its own process (`wasm_host.py`), which runs CPython 3.15 built for WASI under wasmtime. The guest sees only a read-only copy of the standard library at `/lib`: no other files, no network, no processes, no `ctypes`, and an environment holding only `PYTHONHOME`. The script's code (`sandbox_child.py`) talks to the agent through stdin and stdout only.
- **Limits.** Fuel caps computation (100 billion units, about 10 s of pure work; waiting for an action uses none), and memory is capped at 256 MB (a script that wants more gets a MemoryError). Each script also gets 30 minutes of real time, enforced during actions too. A script which runs out of fuel wakes the planner with "the script used too much computation (a busy loop?)".
- **The agent checks every message.** Each reply from the script must be one JSON object under 1 MB. The agent allows 200 SDK calls and 200 log lines (40,000 characters, 6,000 a line) per script, cuts other long text, checks line numbers and call arguments, and ends a script that misbehaves, saying why. Steering, a new script, and the end of the day kill the process. Before starting, a script is only checked to be Python and under 20,000 characters.
- **Setup.** `just setup-wasm` downloads `python-3.15.2-wasi_sdk-33.zip` from Brett Cannon's [cpython-wasi-build](https://github.com/brettcannon/cpython-wasi-build) release v3.15.2, checks it against SHA-256 `b8a63559c56760bbdc310dedb74fd69d10bcbafae26b705bea0ad353ad256888`, unpacks it into `.tools/python-wasi/x`, and compiles it once for wasmtime, which takes about 10 s of CPU. Later starts take about 0.3 s. Without it, scripts are refused with a message saying to run the recipe; there's no unsandboxed fallback.

## Layout

```
bridge/               the mod (C#): Server, Machine (instructions), Devices (input), Nav (paths, follow),
                      Reader, Expr, Views (what read can see), Frames, Journal
agent/stardew_agent/
  layer 3, the planner
    brain.py          the planner loop (tools remember, forget, run, wait), context limits, steering
    director.py       days, the morning report, memory and its nightly consolidation, diary, resuming a day, bedtime
    memory.py         the memory: remember, forget, memory.json, consolidation
    storage.py        the save's data folder: paths, day transcripts, the saved context, moving old data
    prompt.md         the planner's system prompt
    look.py           views of the game (map, find, people, ...), the status line, journal news
  scripts
    script.py         the runner: budgets, safety, stop_if, exceptions; checks every message from a script
    sdk.py            each SDK function (actions, facts, views), calling a skill below
    wasm_host.py      runs a script in WebAssembly (CPython for WASI under wasmtime)
    sandbox_child.py  the script's side, inside the guest: SDK proxies and exceptions (stdlib only)
  layer 2
    decide.py         span-02: check(), pick(), combat tactics
  layer 1, the skills
    game.py           the Game core (reading, input, menus left open) and its parts:
      moving.py       walking, travel between places, walking time home
      items.py        backpack, holding, tools, placing, eating
      menus.py        menus, crafting, quest rewards
      trade.py        shipping, shops, chests
      people.py       finding and talking to people
      events.py       dialogue and cutscenes
      rest.py         the spa, sleeping, the night screens
      title.py        loading and starting games
      common.py, places.py  shared types and errors; place names
    work.py           farming, fishing, mining
    combat.py         combat reflexes, the guard while idle
    vision.py         screenshots labelled with tiles
  driver.py           the socket to the mod
  cli.py              commands and terminal output
agent/tests/          pytest
agent/bench/          benchmark results and scripts
scripts/              build.sh, sandbox.sh, env.sh (shared settings)
justfile
```

## Development

```
just sandbox play --save FOLDER   # the agent plays the sandbox (it starts, with the current mod, if it isn't running)
just test                         # offline tests
just test-live                    # plus the live tests, in the sandbox
just sandbox stop
```

A running sandbox keeps the mod it started with, and stays hidden or shown as it started: after changing the mod, or to switch, `just sandbox stop` and play again.

Live tests are skipped unless `STARDEW_SESSION` points at a running game's `session.json`; `just test-live` sets it for the sandbox.
