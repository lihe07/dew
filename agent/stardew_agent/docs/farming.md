# Farming
The farm's work at a glance: tiles and areas, the daily chores, and where each farming action is documented.

## Tiles and areas
- A tile is `(x, y)` in the current location; y grows downward. Go to the place first (`go("Farm")`), then use its tiles.
- An area is `[x1, y1, x2, y2]`, both corners included, in any order: `[60, 15, 64, 18]` is 5 x 4 = 20 tiles. At most 2,500 tiles per area (`that area is too large`).
- Actions that take `tiles` want a list of `[x, y]`: `[[60, 15], [61, 15]]`.
- Every farming action works only on the tiles that need it, and skips the rest, so an area can safely include tilled or planted ground.
- A malformed area raises `TypeError` (`area must be [x1, y1, x2, y2], not ...`).

To find good ground, look at the map in your wake-up or `look_map()`, or ask `find("tillable")`, `find("tilled_empty")`, `find("water")` (`docs("facts#find")`).

## The daily chores
| action | does | details |
|---|---|---|
| `harvest()` | picks every ready crop here | `docs("farming/crops#harvest")` |
| `water(area=None)` | waters the thirsty crops, refilling the can | `docs("farming/watering")` |
| `pet()` | pets the animals here | `docs("farming/animals#pet")` |
| `machines()` | collects what machines here have finished | `docs("farming/animals#machines")` |
| `forage()` | picks up the forage here | `docs("farming/animals#forage")` |

Rain waters every crop outdoors, so on rainy days `water()` finds nothing to do (it returns at once, costing no energy).

## Planting
`till(area)`, then `plant(seeds, area)`, then `water(area)`. Seeds must be in season, and crops die when the season changes: check with `item_info("Parsnip Seeds")`. See `docs("farming/crops")`, and for which crops pay, `docs("strategy#Seasons and crops")`.

## Clearing ground
`clear(area, kinds)` removes weeds, twigs, stones, and with the right kinds and tools, trees, stumps, boulders, grass, saplings and dead crops. `chop` and `break_rocks` swing once per tile. See `docs("farming/clearing")`.

## Placing things
`place(item, tiles)` puts down chests, scarecrows, sprinklers, machines and other placeable items. See `docs("farming/crops#place")`.

## Energy
Each swing of the hoe, watering can, axe or pickaxe costs energy (about 2 at the start, less as the skill grows); the scythe is free. Watch `energy()`: below 15 the safety raises `LowEnergy` (`docs("exceptions#LowEnergy")`).

## Results
Every farming action returns a dict of counts. If some targets couldn't be reached, it adds `"unreachable": [[x, y], ...]` (at most 20) and `"why"`: what blocked the way. A job stopped partway (a full backpack, a question) says what it had done, like `(harvested 7)`, at the end of the error.
