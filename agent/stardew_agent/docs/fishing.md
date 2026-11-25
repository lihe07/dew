# Fishing
fish finds a spot, casts, and lands what bites (the minigame is skipped); what it returns, what it costs, and how to fish well.

## fish
`fish(casts=3, where=None)`

Takes the fishing rod, picks a spot in the current location, walks there, and casts `casts` times (1 to 20). By itself it casts into the deepest water within reach of the nearest good spot; with `where`, one water tile (`(x, y)` or a Tile) (say, when a river and a pond are both nearby), it stands on land in line with that tile, within casting range, and casts at it. More than one tile raises `TypeError`; a tile that isn't water raises `ActionFailed` (`[x, y] isn't water you can fish in`), and so does one with no reachable land in range. Each cast waits for a bite, hooks it, and lands the fish: the reeling minigame is skipped, so a hooked fish is caught. Treasure chests that come up are emptied into the backpack.

Returns `{"spot": [x, y], "casts": n, "caught": c, "got_away": g}`, plus:
- `"note": "stopped: low energy"` when energy fell under 10 before a cast,
- `"note": "stopped: inventory full"` when there was no free slot,
- `"treasure": True` when a treasure chest came up.

A cast where nothing bit counts in `casts` but not in `caught` or `got_away`, so `caught` can be less than `casts` without anything getting away.

Raises:
- `ActionFailed` when there's no water to fish from here: `there's nowhere reachable to fish from here`.
- `ActionFailed` when a cast lands on land: `the cast didn't land in water; try another spot` (call it again, or `go` somewhere else first).
- `NotEnough` without a rod: `you don't have a fishing rod`.
- `ActionFailed` for casts outside 1 to 20 (`casts must be from 1 to 20, not 30`); `TypeError` if it isn't a whole number.

```python
go("Beach")
r = fish(5)
print("caught", r["caught"], "of", r["casts"])
```

## How the spot is chosen
It looks for a tile you can stand on beside water from which a cast reaches the deepest water (deeper water has better fish), weighing depth against how far the walk is, and casts as far as that deepest water. Higher fishing skill casts farther. Calling `fish` again from the same place reuses about the same spot. To fish somewhere else, `go` there first: the Beach, the river in town or the Forest, the mountain lake.

## Energy
Each cast costs energy (about 8 at fishing level 0, less as the skill grows). The safety raises `LowEnergy` below 15, before `fish` would stop on its own at 10, so catch it or check `farmer.energy` between calls (`docs("exceptions#LowEnergy")`).

## Good practice
- Fish in short calls (3 to 5 casts) in a loop, checking the clock, energy and room in between, so the script can react.
- Keep a free slot or two: new kinds of fish each take a slot.
- Fish sell well at Willy's shop (`docs("items/shops#Shop.sell")`) or by shipping (`docs("items/shipping")`); `backpack.fish` lists what you carry, with `.price` each.
- Which fish bite depends on the place, season, weather and time of day; ask `web(...)` for the fish you want, and remember the answer. More in `docs("strategy#Fishing")`.
- Mind the walk home after an evening at the beach (`docs("time#Getting home")`).

```python
go("Beach")
while day.time < 2100 and farmer.energy > 30 and not backpack.full:
    fish(4)
```
