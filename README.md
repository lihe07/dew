# Stardew Agent

An AI agent that plays Stardew Valley 1.6 by the real rules: it walks everywhere, tools cost energy, shops keep hours, and it passes out at 2am.

```
stardew play (Python)                                          Stardew Valley + SMAPI
  Director  days, the night, bedtime                            Stardew Bridge mod (C#)
  Brain     agent model: run, wait, screenshot, web, remember, forget◀── socket ──▶  attach input step read frame path follow cheat
  Runner    plays the agent's Python scripts                    + a journal of everything shown on screen
  Skills    walk, swing, click, travel, fish...
```

The mod is a small, stable instruction set. Everything game-specific lives in Python, so new abilities rarely need mod changes.

## Setup

1. Put your keys in `.env` (git-ignored; only the agent reads it): `RESPAN_API_KEY=...` for the models, and `TINYFISH_API_KEY=...` for web lookups (optional; without it the `web` tool says lookups are off).
2. `just install` builds the mod and copies it to the game's Mods folder. If the game isn't in Steam's default library, put `STARDEW_PATH="/path/to/Stardew Valley"` in `.env`.
3. `just setup-wasm` fetches the WebAssembly Python that the agent's scripts run in (see The script sandbox).
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

`stardew play` (`just play ...`). Their defaults live in `stardew.toml` at the repo root (keys are the flags without the dashes, like `speed = 1`); a flag given on the command line wins. API keys stay in `.env`.

| Flag | Meaning |
|---|---|
| `--goal TEXT` | what to aim for (default: build a thriving farm) |
| `--days N` | stop after N days (default: keep playing until you quit) |
| `--save FOLDER` | load this save if none is loaded |
| `--new NAME` | start a new game if none is loaded |
| `--model MODEL` | agent model, any gateway model (default `deepseek-v4.1-flash`, or `$STARDEW_MODEL`) |
| `--data DIR` | where the agent keeps its data (default `$STARDEW_DATA`, else `./stardew-data`; `just` sets it to `stardew-data/` where you ran it) |
| `--effort LEVEL` | how hard the agent reasons: `low` (default), `medium`, `high`, `off` |
| `--speed N` | how fast the world runs, single-player only (default 1; up to 16, but faster makes combat reflexes less reliable) |
| `--web-model MODEL` | the research subagent behind the `web` tool (default hy4-flash) |
| `--no-web` | no web lookups |
| `--plain` | print a line per action instead of the full-screen view (used anyway when the output isn't a terminal) |
| `--verbose` | with `--plain`: full thoughts, reports, results, and scripts |
| `--no-map` | with `--plain`: don't print the map on arriving somewhere |

`stardew saves` lists saves.

## How it plays

The world never pauses: it runs while the agent thinks, as in multiplayer. In single-player, `--speed` runs it up to 16 times faster; game time, energy, and odds are unchanged. There are three layers.

**1. The agent** (`brain.py`, `script.py`). A model with six tools:

- `run`: start a Python script written against an SDK (functions like `go`, `gather`, `water`, `plant`, `fish`, `talk`, and objects like `farmer`, `here`, `backpack`, `mine`, whose records carry their own methods: `here.shop.buy(...)`, `chest.put(...)`, `item.ship()`). It runs in the background while the model thinks and replaces any script already running. `finish_current` lets the running action end first.
- `screenshot(radius, grid)`: see the screen now, without disturbing the script.
- `web(question)`: a small research subagent (`web.py`, `--web-model`, default hy4-flash) searches and reads pages through TinyFish's search and fetch APIs, then answers in a few sentences, so the agent's context never fills with web pages.
- `wait`: end the turn; the model is woken when the script ends, raises, asks, or prints.
- `remember(key, value)`: set or replace a fact in its memory; `forget(key)`: remove one (see Memory).

Every wake-up carries the farmer's status and a text map around it. For more, a script reads the live objects (`farmer`, `day`, `here.find`, `here.map`, `mine`, `people`, `places`, `catalog`, `quests`, `recipes`) and prints what the agent needs.

The model is woken when a script ends, raises an exception it doesn't catch, or calls `ask(question)` (which ends it), when it prints (`print` queues a message and wakes the agent while the script keeps running; messages are batched, at most one wake-up every 30 seconds), and when you steer or the day ends. Old screenshots and stale observations are dropped; the context is never summarized, so a day's whole conversation stays.

**2. span-02** (`decide.py`, `combat.py`), a fast decision model on the Respan gateway (`/v1/decisions`). In scripts, `check(situation)` returns True or False and `pick(description, options)` returns one option; both raise `Unsure` below 0.6 confidence. It reads plain facts (time, minutes until 2am, weather, location, health, energy, money, free slots, what is held, the last result, recent dialogue) and compares but can't do arithmetic. In `descend`, `combat.Tactics` asks the same judge to choose between options that are all already safe, such as fighting nearby monsters or taking a visible ladder; below 0.4 confidence it takes the safe default.

**3. Deterministic routines** (no model):

- Walking: paths go around people and animals and keep away from monsters; a walk stops if someone steps into the way, and `travel` retries.
- One visitor (`Game.visit`) for every job with many targets (`gather`, `water`, talking to several people): the nearest by walking first, looked up again after each, unreachable ones skipped and reported. `gather` takes one table of kinds, the same names `find` uses, picks each target's tool, and picks up what they drop.
- Combat reflexes (`combat.Fight`): one decision per game tick: approach, swing with the right tool, back off when too close, eat when health is below half, retreat below the guard's limit.
- The guard (`combat.Guard`, set by the script's `guard()`): while any action runs, `Game.step` (in slices of 10 ticks when monsters are about), each walk segment, and the visitor ask it to look; a monster within 4 tiles pauses the action for a fight, then the action goes on. Hurt with monsters close, it eats, or retreats and raises `LowHealth` once. While no script runs, `combat.keep_busy` runs the same guard, and in the mines breaks rocks next to the farmer.
- Safety: before each action the runner raises `LowHealth` (under 30%), `LowEnergy` (under 15), and `TimeToGoHome` (after 6pm, when the walk home plus 45 minutes is about all the time left), each once until it clears. Eating, sleeping, and going home are exempt. `TimeToGoHome` aims for home by midnight.
- If the farmer is still out at 2am, it passes out, as in the game; the director notices and gets it through the night. Level-up profession choices go to the model.

## What a script looks like

From `agent/stardew_agent/prompt.md`:

```python
try:
    go("SeedShop")
    here.shop.buy("Parsnip Seeds", 15)
except Closed:
    go("JojaMart")
    here.shop.buy("Parsnip Seeds", 15)
go("Farm")
plant("Parsnip Seeds", Area(60, 15, 64, 17))
water()
try:
    mine.descend()
    while day.minutes_left > 240:
        if mine.ladders or mine.shafts:
            mine.descend()
        else:
            gather(mine.rocks[:3])
except (LowHealth, TimeToGoHome):
    go("Farm")
```

Each SDK call blocks until its action ends and returns the result. An uncaught exception ends the script and wakes the agent with its type, the line, the script's logs, and each action's result.

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
    └── Cutscene
```

## What you see

In a terminal, a full-screen view: the day's transcript on the left (the agent's reasoning, each script's goal, one line per action with its result, items, and places walked through, `check`/`pick` decisions, safety stops), and on the right the running script with its current line marked, the Day (the planner's plan for it, its diary, and on the latest day the plan for tomorrow once the night has written it), and the farmer's status. Tabs along the top switch between days: ←/→ a day, Shift+←/→ a week, Ctrl+←/→ the first day and today; the tabs scroll to keep the day shown in view, with counts of those left out at either end. ↑/↓ scroll. The save's last 7 days are loaded from its transcripts at start, so they can be looked through after a restart. Piped or with `--plain`, the same lines are printed one after another.

## Steering from the terminal

Type a line in the input box and press Enter: it joins the agent's queue, next to what the script printed, and is shown above the input until the agent reads it. A waiting agent is woken at once; a thinking one reads it right after its current thought. The script keeps running; the agent decides what to do. Input is only read when stdin is a terminal.

In the game, **F8** gives you control: the script stops, the guard and the agent pause (no model calls), and the view says so. F8 again hands it back, and the agent is told to check what changed.

## Memory

- **Memory** (`memory.py`): the model's one lasting memory, facts by short dotted key (`shop.pierre.hours`, `plan.tomorrow`, `farm.chest`), set with `remember` and removed with `forget`. Keys are at most 60 characters, values 500, and there are at most 150 keys (more is refused, telling the model to forget stale ones). All of it is shown every morning under `# Memory`, sorted by key. It's saved in `memory.json`, written atomically.
- **The night** (`planner.py`): once the farmer is in bed, the planner (`--planner-model`, default the agent's model, at `--planner-effort`) gets the record of the day as the agent played it (its thoughts and scripts, results cut short), the day's numbers, the memory and the farmer's state. It tidies the memory with `remember` and `forget` (duplicates merged, stale facts dropped, contradicted values fixed, nothing invented), may look things up with `web`, and ends with `plan`: the diary of the day, the long-term goals, and tomorrow's plan. Its conversation is kept for 14 nights in `planner.json`, each past night's record replaced by the diary it wrote. A night that fails keeps last night's goals and plan, and writes no diary.
- **Diary**: the planner's under-120-word summary of each day, ended by the day's numbers, appended to `diary.jsonl`. The last 7 days are shown each morning.
- Nothing else carries over between days. If the day ends without the agent going to bed (it stops answering, or the gateway refuses a request), the model gets three more turns, with only `remember` and `forget`, and the farmer is put to bed.
- An old `notes.md` (from the `note` tool, now gone) is imported once into the key `notes` and renamed `notes.md.imported`.

## Storage

Each save has a folder, `<data>/<farm name>_<game id>/`, where `<data>` is `--data DIR`, else `$STARDEW_DATA`, else `stardew-data/` in the working directory. The `just` recipes set `STARDEW_DATA` to `stardew-data/` in the directory you ran `just` from (git-ignored). A relative `--data` is taken from `agent/` under `just`, so give it an absolute path there.

```
stardew-data/Farm_123456789/
  memory.json          the facts
  diary.jsonl          one summary a day
  planner.json         the planner's goals, tomorrow's plan, and its last 14 nights of conversation
  days/0001-spring-04.jsonl   every step shown that day: {"kind", "text", "ok", "data"}, images left out
  context.json         the agent's messages for the last finished day (screenshots dropped), a record
  screenshots/         every screenshot the agent takes, full size
```

The memory and the context reach disk only when a day ends (after the planner's night), as the game itself saves only when you sleep. A run stopped partway through a day starts that day again from its morning report, just as the game starts it again from its morning. Data from before (`~/.local/share/stardew-agent/<save>/`) is moved into the new folder once, if that folder doesn't exist yet.

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

The agent reads in-game text and web pages, so its scripts are treated as untrusted. They run as ordinary Python, but inside WebAssembly:

- **Isolation.** Each script gets its own process (`wasm_host.py`), which runs CPython 3.15 built for WASI under wasmtime. The guest sees only a read-only copy of the standard library at `/lib`: no other files, no network, no processes, no `ctypes`, and an environment holding only `PYTHONHOME`. The script's code (`sandbox_child.py`) talks to the agent through stdin and stdout only.
- **Limits.** Fuel caps computation (100 billion units, about 10 s of pure work; waiting for an action uses none), and memory is capped at 256 MB (a script that wants more gets a MemoryError). Each script also gets 30 minutes of real time, enforced during actions too. A script which runs out of fuel wakes the agent with "the script used too much computation (a busy loop?)".
- **The agent checks every message.** Each reply from the script must be one JSON object under 1 MB. The agent allows 200 SDK calls and 200 log lines (40,000 characters, 6,000 a line) per script, cuts other long text, checks line numbers and call arguments, and ends a script that misbehaves, saying why. Steering, a new script, and the end of the day kill the process. Before starting, a script is only checked to be Python and under 20,000 characters.
- **Setup.** `just setup-wasm` downloads `python-3.15.2-wasi_sdk-33.zip` from Brett Cannon's [cpython-wasi-build](https://github.com/brettcannon/cpython-wasi-build) release v3.15.2, checks it against SHA-256 `b8a63559c56760bbdc310dedb74fd69d10bcbafae26b705bea0ad353ad256888`, unpacks it into `.tools/python-wasi/x`, and compiles it once for wasmtime, which takes about 10 s of CPU. Later starts take about 0.3 s. Without it, scripts are refused with a message saying to run the recipe; there's no unsandboxed fallback.

## Layout

```
bridge/               the mod (C#): Server, Machine (instructions), Devices (input), Nav (paths, follow),
                      Reader, Expr, Views (what read can see), Frames, Journal
agent/stardew_agent/
  layer 3, the agent
    brain.py          the agent loop (tools run, wait, screenshot, web, remember, forget), context limits, steering
    director.py       days, the morning report, the night, the diary, saving at the day's end, bedtime
    planner.py        the night: the day's record, memory edits, the diary, the goals and tomorrow's plan
    memory.py         the memory: remember, forget, memory.json
    storage.py        the save's data folder: paths, day transcripts, the saved context, moving old data
    prompt.md         the agent's system prompt
    look.py           views of the game (map, find, people, ...), the status line, journal news
  scripts
    script.py         the runner: budgets, safety, exceptions; checks every message from a script
    api/              the script API: one declaration of its functions, live objects and records (base.py: the
                      machinery and the argument vocabulary), from which the sandbox, the runner and the prompt are built
    wasm_host.py      runs a script in WebAssembly (CPython for WASI under wasmtime)
    sandbox_child.py  the script's side, inside the guest: the API's classes built from its spec, exceptions (stdlib only)
  layer 2
    decide.py         span-02: check(), pick(), combat tactics
    web.py            the web tool's research subagent (TinyFish search and fetch)
  layer 1, the skills
    game.py           the Game core (reading, input, menus left open) and its parts:
      moving.py       walking, travel between places, walking time home
      items.py        backpack, holding, tools, placing, eating, the visitor
      menus.py        menus, crafting, quest rewards
      trade.py        shipping, shops, chests
      people.py       finding and talking to people
      events.py       dialogue and cutscenes
      rest.py         the spa, sleeping, the night screens
      title.py        loading and starting games
      common.py, places.py  shared types and errors; place names
    work.py           farming, gather (and the kinds table find shares), fishing, the mines
    combat.py         combat reflexes, the guard (during actions and while idle)
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
