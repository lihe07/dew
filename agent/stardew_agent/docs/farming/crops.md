# Crops
Tilling, planting, harvesting and placing: till, plant, harvest and place, with their results and gotchas.

## till
`till(where)`

Hoes the bare, diggable ground among the tiles of `where` (an Area, a tile, or a list of them: `docs("moving#Tiles and areas")`). Tiles already tilled, planted, covered by an object, or not diggable (paths, floors, grass on some farms) are skipped.

Returns `{"done": n, "got": {...}, "skipped": k}` (`got`: what the hoe dug up; `skipped`: tiles that didn't need or allow tilling), plus `"left"` and `"why_left"` if some couldn't be reached. When there's nothing to till: `{"tilled": 0, "note": "nothing to till there (12 of 20 tiles already tilled; the rest can't be dug or are covered)"}`. Note the two shapes: read the count with `r.get("done", 0)`.

Raises `TypeError` for a malformed `where`, `ActionFailed` for tiles in another place, `NotEnough` without a hoe (`you don't have a hoe`), and `NotEnough` when energy runs out (`out of energy`).

Gotcha: stones, weeds and twigs cover the ground; clear them first (`docs("farming/clearing")`). `here.find("tillable")` lists diggable tiles with nothing on them.

```python
till(Area(60, 15, 64, 18))
```

## plant
`plant(item, where)`

Sows seeds (or spreads fertilizer) on the tilled, empty soil among the tiles of `where`. `item` is an exact name like `"Parsnip Seeds"` or an Item from `backpack`.

Returns `{"placed": n, "couldnt_place_at": [[x, y], ...], "skipped": k}`. When the seeds run out partway: `{"placed": n, "note": "ran out of Parsnip Seeds", "left": k}`.

Raises:
- `NotEnough` when none of the tiles is tilled and empty: `no tilled, empty soil among those 20 tiles: till them first, or pick other tiles`.
- `NotEnough` when you have none of the item: `you don't have 'Parsnip Seeds'`.

Gotchas:
- Out-of-season seeds are refused by the game: they show up in `couldnt_place_at`, not as an error.
- Names match exactly: `"Parsnip"` isn't `"Parsnip Seeds"`. Use the full name.
- Newly planted seeds need water the same day: `water(where)` after planting.

```python
plot = Area(60, 15, 64, 18)
plant("Parsnip Seeds", plot)
water(plot)
```

## harvest
Ripe crops are harvested with `gather("crops")` (`docs("gather")`): every ripe crop here, by hand or with the scythe for wheat and the like. A full backpack stops it, and `why_left` says so: ship or store some things and call it again.

How many crops are ripe here: `len(here.find("crops"))` (`docs("facts#Here.find")`); `here.find("planted")` lists every crop with its days left.

## place
`place(item, where)`

Puts down a placeable item (a chest, a scarecrow, a sprinkler, a machine, a torch, a fence, seeds) on each tile of `where` (a tile, a list of tiles, or an Area: every tile of it), walking beside each.

Returns `{"placed": n, "couldnt_place_at": [[x, y], ...]}`, or `{"placed": n, "note": "ran out of Chest", "left": k}`.

Raises `NotEnough` if you don't have the item.

```python
place("Chest", (64, 14))
place("Scarecrow", [(62, 17), (70, 17)])
```

## Crop facts
`catalog["Cauliflower Seeds"].crop` gives the crop's seasons, days to grow, whether it regrows, what it gives and what that sells for (`docs("facts#catalog")`). A season has 28 days, and a crop still growing when the season changes dies. `28 - day.day` is how many days are left. For choosing crops, see `docs("strategy#Seasons and crops")`.
