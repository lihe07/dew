# Scripts
How a script runs: blocking SDK calls, print and ask, the budgets, the sandbox, replacing a script, and what a wake-up contains.

## How a script runs
A script is ordinary Python, started with the `run(script, goal, finish_current=False)` tool. It runs in the background while you think. Each SDK call blocks until its action is finished in the game and returns its result; actions walk to their target themselves. Only one action runs at a time.

Before each action, and every couple of seconds during it, the farmer's own safety checks run: see `docs("exceptions#Safety")`. Anything unexpected raises an exception; uncaught, it ends the script and wakes you with the exception, its message and the line (see `docs("exceptions")`).

```python
harvest()            # blocks until every ready crop here is picked
r = water()          # returns a dict, e.g. {"watered": 12, "still_dry": 0}
print("watered", r["watered"])
```

## What a script can use
- The SDK functions: actions (moving, farming, fishing, mining, items, people, time, `screen`) and facts (`money()`, `find()`, `state()`, ...). Index: `docs("moving")`, `docs("farming")`, `docs("fishing")`, `docs("mining")`, `docs("items")`, `docs("people")`, `docs("time")`, `docs("facts")`, `docs("screen")`.
- `backpack`: `.items`, `.food`, `.fish`, `.free`, `.full`, read live (`docs("items#backpack")`).
- The judge: `check(situation)` and `pick(description, options)` (`docs("judge")`).
- `print(...)` and `ask(question)`, below.
- The exception classes, by name: `ActionFailed`, `Closed`, `NotEnough`, `Unreachable`, `Danger`, `LowHealth`, `LowEnergy`, `TimeToGoHome`, `Happened`, `Question`, `Cutscene`, `Unsure`, `Surprise`.
- Python's builtins and standard library.

## The sandbox
Scripts run in CPython compiled to WebAssembly, in a process of their own. There are no files, no network, no environment variables and no subprocesses. Imports from the standard library work (`math`, `random`, `itertools`, ...).

A script is refused before it starts (`run rejected, nothing changed: ...`) if it is longer than 20,000 characters or isn't valid Python (a `SyntaxError` with its line).

## Budgets
- **200 SDK calls** per script. Every SDK function counts, and so do `check`, `pick`, and *each read* of a `backpack` attribute (`backpack.items` is a call). `print` and `ask` don't count. Call 201 and later raise `Surprise("budget: the script made over 200 calls")`; a script that keeps calling past 220 is killed.
- **30 minutes of real time** per script. Past that, the running action is stopped and the script ends.
- **200 printed lines** and **40,000 printed characters** per script; one line keeps at most 6,000 characters (enough for a map). Past either, the script is killed.
- Computation and memory (256 MB) are limited: a busy loop gets the script killed ("used too much computation").

When a limit kills a script, you're woken with the reason ("the script was ended: ...").

Keep the budget in mind in loops: read `backpack.items` once into a variable and reuse it.

```python
items = backpack.items        # one call
seeds = [i for i in items if "Seeds" in i.name]
```

## print
`print(*parts, sep=" ")` sends a message to you, and the script carries on. Messages wake you, but batched: the first one waits about a second for more, and messages wake you at most once every 30 seconds; the rest wait in the queue and come with the next wake-up. If the script ends meanwhile, everything comes with that one wake-up. So print what you'd react to (a find, a problem, a milestone), not every step: every action's result is already reported to you.

## ask
`ask(question)` ends the script at once and wakes you with "the script asks: ...". Use it when the script reaches a decision it can't make; nothing after it runs.

```python
if money() < 500:
    ask("only %dg: buy fewer seeds, or sell the gems first?" % money())
```

## Replacing a running script
Calling `run` again replaces the running script at once: its process is killed and its current action stopped, unless you pass `finish_current=True`, which lets the current action finish first. To change course, just run the new plan; there's nothing to clean up. A script stopped on purpose doesn't wake you.

When no script runs, the farmer isn't frozen: it keeps watch, fights monsters which come close (if it has a weapon), eats when badly hurt, flees or leaves the mine when hurt without food, and in the mines breaks rocks right beside it while energy is above 40. Don't rely on it for progress: the game clock is running.

## What wakes you
- the script ends, raises (uncaught), asks, or is killed;
- the script prints (batched, see `docs("scripts#print")`);
- the player writes to you (marked PLAYER), or takes control and gives it back;
- the day ends (sleep or passing out).

## What a wake-up contains
1. Messages: the player's first (PLAYER), then notes.
2. "What happened": every action since the last wake-up, as `#id name args` with its result, what the journal recorded (items gained and lost, dialogue, letters, level-ups) and the status line; also what was printed, the judge's answers, and safety warnings.
3. "Meanwhile": what happened while no action ran.
4. "Woken because": why you were woken.
5. Screenshots the script took with `screenshot()`.
6. "Farmer": the status line (date, time, place and tile, energy, health, money, free slots, what's held, any open menu, and after 6pm the time until you pass out), a text map of about 10 tiles around (x rulers on top, y down the left; the symbols are listed in `docs("facts#look_map")`) with a "Nearby:" line naming exits, people and things with their tiles and a "Backpack:" line, and what's running.

So you don't need a script just to look around. The morning report adds your memory, the diary of the last days, last night's notes, and `state()` with the inventory.

## Planning
Plan one stretch at a time (an errand, a few hours) and run it; plan the next with fresh facts. Don't leave the farmer idle while you think. Good patterns are in `docs("examples")`, and advice on what's worth doing in `docs("strategy")`.
