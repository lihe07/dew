# Chests
A chest's put and take move items between the backpack and that chest.

## Which chest
`here.chests` lists the chests in the current location (a Chest, Big Chest or Stone Chest), as Chest records with `.name`, `.x`, `.y`, `.tile` and `.items` (what was in it when read). `chest.put` and `chest.take` walk to *that* chest and open it, so pick the one you mean: `here.chests[0]`, or by tile (`[c for c in here.chests if (c.x, c.y) == (64, 14)]`). A chest in another place raises `ActionFailed`: go there first. A new chest: `recipes["Chest"].craft()` (50 wood) and `place("Chest", (x, y))` (`docs("farming/crops#place")`). Remember where your chests are, like `farm.chest`.

## Chest.put
`chest.put(item, count=None)`

Moves one stack (the one with that exact name), or `count` of it, from the backpack into the chest. `chest.put("all")` puts everything but tools.

Returns `{"moved": ["Copper Ore x40"]}`.

Raises `ActionFailed` for `'X' isn't in your backpack` (or `your backpack is empty`), and `NotEnough` when it didn't move (`that didn't move; is there room?`: the chest is full).

```python
go("Farm")
chest = here.chests[0]
for name in ["Copper Ore", "Stone", "Coal"]:
    try:
        chest.put(name)
    except ActionFailed:
        pass
```

## Chest.take
`chest.take(item="all", count=None)`

Takes everything from the chest (`"all"`, the default), or one stack by name (or `count` of it), into the backpack.

Returns `{"moved": ["Wood x99", "Sap x12"]}`.

Raises `NotEnough` when the backpack is full (`your inventory is full (took Wood x99)`) or the chest is empty (`the chest is empty`), and `ActionFailed` when the named item isn't there (`'Coal' isn't in the chest`).

## Seeing what's inside
A Chest's `.items` is what was in it when `here.chests` was read: `print([(i.name, i.count) for i in here.chests[0].items])`. Read `here.chests` again after putting or taking.
