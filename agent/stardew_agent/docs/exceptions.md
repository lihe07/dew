# Exceptions
The exception classes a script can catch, their fields, which errors become which class, what the farmer's own safety raises, and patterns for catching.

## The tree
```
Surprise                         anything unexpected
├── ActionFailed(reason)         an action couldn't be done
│   ├── Closed                   hours, locked doors, away, festivals
│   ├── NotEnough                money, items, room, ingredients, energy, soil
│   └── Unreachable              no way there, someone or something in the way
├── Danger                       raised by the farmer's own safety
│   ├── LowHealth
│   ├── LowEnergy
│   └── TimeToGoHome(minutes_home, minutes_left)
├── Happened(text)               something needs a decision first
│   ├── Question(text, choices)
│   └── Cutscene(text)
└── Unsure(situation, confidence)   the judge wasn't sure
```
All of them are available in scripts by name. Besides these, a call of the wrong shape (a bad argument type, an unknown `here.find` kind, a raw list where an Area goes) raises Python's `TypeError`.

## Fields
- `ActionFailed.reason`: the game's or the skill's explanation, one line. `str(ex)` is the same. `NotEnough.what` is the same text.
- `TimeToGoHome.minutes_home` (about how many game minutes the walk home takes) and `.minutes_left` (minutes until 2am).
- `Happened.text`, `Question.text` and `Question.choices` (a list of strings, often empty: see `docs("exceptions#Questions and cutscenes")`).
- `Unsure.situation` and `Unsure.confidence` (0 to 1).
- `LowHealth` and `LowEnergy` have only their message, like `health is low (40/150)`.

```python
try:
    here.shop.buy("Cauliflower Seeds", 10)
except NotEnough as ex:
    print("couldn't buy:", ex.reason)
```

## Which errors become which class
An action's error message is matched, first match wins:
- **Closed**: "closed", "locked", "festival", "isn't around today", "didn't open", "no shop is open". For example a locked shop door (`the way to SeedShop is closed: ...`), a shop whose keeper is away (`the shop didn't open ...`), `talk` to someone who isn't out today.
- **NotEnough**: "inventory is full", "free inventory slot", "but you have Ng", "you don't have", "missing ingredients", "out of energy", "isn't in there", "is empty", "only N of", "is there room", "no tilled, empty soil". For example `buy` without the money, any item you don't have, `plant` with no tilled soil, an empty watering can with no water here, a tool you don't carry.
- **Unreachable**: "couldn't reach", "no walking route", "couldn't get to", "lost sight", "couldn't catch up", "gave up travelling", "blocked". For example an unknown place name, someone who kept standing in the way, a person who kept walking off.
- Anything else is a plain **ActionFailed**: `'X' isn't sold here`, `this shop doesn't buy X`, `X isn't edible`, `the cast didn't land in water`, an unknown name with its "did you mean" list, tiles in another place (`those tiles are in Town, ...: go there first`), a shop's call outside its place (`that shop is in SeedShop, ...`).

Walking to a tile with no path raises `Unreachable` (`can't reach ... no walkable path`); `debris is in the way, clear [...] first` is a plain `ActionFailed`. `'X' isn't in the chest` (from `chest.take`) and `the chest is empty` are `NotEnough`. Catch `ActionFailed` when you want every failure.

Facts (`here.find`, `catalog[...]`, `here.info`, `people[...]`, ...) raise the same classes for their errors, e.g. an unknown item name in `catalog` is a plain `ActionFailed`.

## Safety
Before every action, and about every 2 seconds while one runs, the farmer's safety checks the state. When a condition starts, it raises the matching `Danger` once: before the action (which then doesn't run), or in the middle (the action is stopped partway, and the result says so). It isn't raised again until the condition has cleared, so after catching it, the next action runs normally. Facts never trigger it.

These actions are let through even while a condition holds, because they cure it: `sleep()`, `item.eat()`, `soak(...)`, and `go` home (`"Farm"`, `"FarmHouse"`). Other actions, `wait()` among them, raise the warning first; since a warning is given only once, the next action after catching it runs anyway.

## LowHealth
Raised when health drops below 30% of the maximum, and by the guard when it retreats from monsters without food (`retreating: health is ...`, `docs("mining/combat#guard")`). In the mines this usually means: eat, or leave. The guard eats on its own when you carry food that heals (`docs("mining/combat#Food")`).

## LowEnergy
Raised when energy drops below 15. Eat something (`docs("items#Item.eat")`), soak (`docs("time#soak")`), or do work that costs no energy (talking, shopping, foraging, petting). At about 4 energy or less, tool swings refuse: `out of energy; eat something or sleep` (NotEnough).

## TimeToGoHome
Raised from 6pm on, when the walk home (an estimate, recomputed when the location changes or every 30 game minutes) plus 15 minutes no longer fits before midnight. It carries `minutes_home` and `minutes_left` (until 2am). Going to bed after midnight costs energy the next morning, and at 2am you pass out wherever you are: see `docs("time#Getting home")`.

## Questions and cutscenes
When an action can't go on without a decision, it raises a `Happened`:
- `Cutscene(text)` when the message mentions a cutscene or a minigame (`a minigame is running (...)`).
- `Question(text, choices)` for everything else: a dialogue question (`a menu needs your attention (DialogueBox)`), a counter that asks what you want (`the counter asks: 0: ... / 1: ...`), a profession to choose after a level-up (choices like `0: Rancher - ...`).

Answer a question with `answer(n)` or `answer("a description")` (`docs("people#answer")`), or look at it with `screen.look()` (`docs("screen")`). A cutscene that can be skipped and asks nothing is skipped on its own, its lines going to the journal (`docs("people#Cutscenes")`).

The skills' own stopping points arrive as `Danger`: the guard or `fight()` retreating without food (`retreating: health is ...`) and a walk interrupted by low health raise `LowHealth`.

## Unsure
`check` and `pick` raise `Unsure` when the judge's confidence is under 0.6, when `pick` gets no options, or when the judge is unavailable. `answer("description")` can raise it too. See `docs("judge")`.

## Other errors
- `TypeError` for a call of the wrong shape, with what was expected: `where must be a Tile or (x, y), an Area, or a list of those or of things with a .tile, not ...`.
- Your own Python errors (`KeyError`, `IndexError`, ...) end the script like any other uncaught exception. `here.find(...)[0]` on an empty list is a common one.

## What ends the script
Any uncaught exception ends the script and wakes you with its class, message (up to 500 characters) and line. The day ending (sleep, passing out) stops the script without an exception.

## Catching patterns
Catch what you have a plan for, and let the rest wake you.

```python
# retry after opening hours
try:
    go("SeedShop")
except Closed:
    wait("9:00 am")
    go("SeedShop")
```

```python
# a danger, then carry on
try:
    gather("rocks")
    mine.descend()
except LowHealth:
    if backpack.food:
        pick("the cheapest food", backpack.food).eat()
except (LowEnergy, TimeToGoHome):
    go("Farm")
```

```python
# keep going through a list, noting what failed
for name in ["Haley", "Alex", "Emily"]:
    try:
        talk(people[name])
    except ActionFailed as ex:   # Closed, Unreachable, ... are all ActionFailed
        print(name, "skipped:", ex.reason)
```

Don't wrap everything in `except Surprise: pass`: you'll miss what you needed to know, and the script will carry on in the wrong place.
