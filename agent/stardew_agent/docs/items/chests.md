# Chests
put and take move items between the backpack and the nearest chest in the current location.

## Which chest
`put` and `take` use the container whose menu is open, if any; otherwise they walk to the nearest chest in the current location (a Chest, Big Chest or Stone Chest) and open it. There must be one here: `there's no chest in Town; place one, or go to the one on the farm`. A new chest: `craft("Chest")` (50 wood) and `place("Chest", [[x, y]])` (`docs("farming/crops#place")`). Remember where your chests are, like `farm.chest`.

## put
`put(item)`

Moves one stack (the first that matches the name) from the backpack into the chest. `put("all")` isn't allowed: name each item.

Returns `{"moved": ["Copper Ore x40"]}`.

Raises `ActionFailed` for `'X' isn't in your backpack` and `name the item to put`, and `NotEnough` when it didn't move (`that didn't move; is there room?`: the chest is full).

If the open container is the shipping bin, `put(item)` ships that stack instead and returns `{"shipped": ...}`.

```python
go("Farm")
for name in ["Copper Ore", "Stone", "Coal"]:
    try:
        put(name)
    except ActionFailed:
        pass
```

## take
`take(item="all")`

Takes everything from the chest (`"all"`, the default) or one stack by name, into the backpack.

Returns `{"moved": ["Wood x99", "Sap x12"]}`.

Raises `NotEnough` when the backpack is full (`your inventory is full (took Wood x99)`) or the chest is empty (`the chest is empty`), and `ActionFailed` when the named item isn't there (`'Coal' isn't in the chest`).

## Seeing what's inside
`find("chests")` lists chests here. To look inside without taking anything: `screen("interact", tiles=[[x, y]])` opens it, and the result's `menu` lists the contents (`docs("screen#interact")`); close it with `screen("close")`.
