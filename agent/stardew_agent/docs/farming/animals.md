# Animals, machines and forage
The other chores: pet, machines and forage, which work on everything in the current location.

## pet
`pet()`

Pets every animal in the current location that hasn't been petted today, following them as they wander (up to 4 tries each). Animals are wherever they are: outdoors on the farm on fine days, or inside their barn or coop, so go into the building first when they're inside.

Returns `{"petted": ["Daisy", "Clover"]}`.

```python
r = pet()
print("petted", len(r["petted"]))
```

## machines
`machines()`

Collects the finished output from every machine in the current location (kegs, furnaces, preserves jars, bee houses, ...), walking to each. It doesn't load them again: for that, hold the input with `screen("interact", tiles=[[x, y]], item="...")` (`docs("screen#interact")`).

Returns `{"collected": n}`, plus `"unreachable"` and `"why"`. Raises `NotEnough` when the backpack is full.

`find("machines")` lists machines here; `look_map()` marks the ready ones `READY: ...` in its notable list.

## forage
`forage()`

Picks up every forage item in the current location (spring onions, leeks, daffodils, shells, mushrooms, ...), walking to each.

Returns `{"collected": n}`, plus `"unreachable"` and `"why"`. Raises `NotEnough` (`your inventory is full (collected 4)`) when the backpack is full.

Forage appears fresh each day in the Forest, the Mountain, the Beach, the Bus Stop, the Backwoods and town. It's free money and costs no energy: a round on the way somewhere is worth it. `find("forage")` lists what's here, nearest first. Berry bushes with berries (`find("berry_bushes")`) aren't forage: shake one with `screen("interact", tiles=[[x, y]])`, and the berries drop beside it to be walked over.

```python
for place in ["Forest", "Town", "Mountain"]:
    go(place)
    forage()
```

Selling or shipping forage: `docs("items/shops#sell")` and `docs("items/shipping")`.
