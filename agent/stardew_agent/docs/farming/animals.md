# Animals, machines and forage
The other chores: pet, and collecting from machines and forage, which work on everything in the current location.

## pet
`pet()`

Pets every animal in the current location that hasn't been petted today, following them as they wander (up to 4 tries each). Animals are wherever they are: outdoors on the farm on fine days, or inside their barn or coop, so go into the building first when they're inside.

Returns `{"petted": ["Daisy", "Clover"]}`.

```python
r = pet()
print("petted", len(r["petted"]))
```

## Machines and forage
Both are `gather` (`docs("gather")`): `gather("machines")` collects the finished output of every machine here (kegs, furnaces, preserves jars, bee houses, ...), and `gather("forage")` picks up the forage. Neither loads machines again: for that, hold the input with `screen.interact((x, y), item="...")` (`docs("screen#Screen.interact")`). `here.find("machines")` lists the ready ones here; `here.map()` marks them `READY: ...` in its notable list.

Forage appears fresh each day in the Forest, the Mountain, the Beach, the Bus Stop, the Backwoods and town: a round on the way somewhere is worth it.

```python
for place in ["Forest", "Town", "Mountain"]:
    go(place)
    gather("forage")
```

Selling or shipping forage: `docs("items/shops#Shop.sell")` and `docs("items/shipping")`.
