# Crops
Tilling, planting, harvesting and placing: till, plant, harvest and place, with their results and gotchas.

## till
`till(area=None, tiles=None)`

Hoes the bare, diggable ground among the tiles (give an area, tiles, or both). Tiles already tilled, planted, covered by an object, or not diggable (paths, floors, grass on some farms) are skipped.

Returns `{"done": n, "of": m, "skipped": k}` (`skipped`: tiles that didn't need or allow tilling), plus `"unreachable"` and `"why"` if some couldn't be reached. When there's nothing to till: `{"tilled": 0, "note": "nothing to till there (12 of 20 tiles already tilled; the rest can't be dug or are covered)"}`. Note the two shapes: read the count with `r.get("done", 0)`.

Raises `TypeError` without an area or tiles, `NotEnough` without a hoe (`you don't have a hoe`), and `NotEnough` when energy runs out (`out of energy`).

Gotcha: stones, weeds and twigs cover the ground; clear them first (`docs("farming/clearing#clear")`). `find("tillable")` lists diggable tiles with nothing on them.

```python
till([60, 15, 64, 18])
```

## plant
`plant(item, area=None, tiles=None)`

Sows seeds (or spreads fertilizer) on the tilled, empty soil among the tiles. `item` is a name like `"Parsnip Seeds"` or an item from `backpack`.

Returns `{"placed": n, "couldnt_place_at": [[x, y], ...], "skipped": k}`. When the seeds run out partway: `{"placed": n, "note": "ran out of Parsnip Seeds", "left": k}`.

Raises:
- `NotEnough` when none of the tiles is tilled and empty: `no tilled, empty soil among those 20 tiles: till them first, or pick other tiles`.
- `NotEnough` when you have none of the item: `you don't have 'Parsnip Seeds'`.

Gotchas:
- Out-of-season seeds are refused by the game: they show up in `couldnt_place_at`, not as an error.
- Names match loosely: `"Parsnip"` would match "Parsnip Seeds" only if no item is called exactly "Parsnip". Use the full name.
- Newly planted seeds need water the same day: `water(area)` after planting.

```python
plant("Parsnip Seeds", [60, 15, 64, 18])
water([60, 15, 64, 18])
```

## harvest
`harvest()`

Picks every ready crop in the current location, walking to each (crops which need the scythe, it uses the scythe for). There's no area: it takes everything ready here.

Returns `{"harvested": n}`, plus `"unreachable"` and `"why"`.

Raises `NotEnough` (`your inventory is full (harvested 7)`) when the backpack fills up: ship or store some things and call it again.

```python
r = harvest()
print("harvested", r["harvested"])
```

How many crops are ready, here or on the farm: `len(find("crops_ready"))`, or `state()["farm_crops"]` from anywhere (`docs("facts#state")`).

## place
`place(item, tiles)`

Puts down a placeable item (a chest, a scarecrow, a sprinkler, a machine, a torch, a fence, seeds) on each of the tiles, walking beside each. Only `tiles` (a list of `[x, y]`) is taken, not an area.

Returns `{"placed": n, "couldnt_place_at": [[x, y], ...]}`, or `{"placed": n, "note": "ran out of Chest", "left": k}`.

Raises `NotEnough` if you don't have the item.

```python
place("Chest", [[64, 14]])
place("Scarecrow", [[62, 17]])
```

## Crop facts
`item_info("Cauliflower Seeds")` gives the crop's seasons, days to grow, whether it regrows, what it gives and what that sells for (`docs("facts#item_info")`). A season has 28 days, and a crop still growing when the season changes dies. `state()["date"]` says how many days are left. For choosing crops, see `docs("strategy#Seasons and crops")`.
