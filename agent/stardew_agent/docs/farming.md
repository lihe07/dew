# Farming
The farm's work at a glance: tiles and areas, the daily chores, and where each farming action is documented.

## Tiles and areas
- A tile is `Tile(x, y)` or plain `(x, y)` in the current location; y grows downward. Go to the place first (`go("Farm")`), then use its tiles: tiles in another place raise `ActionFailed`.
- An area is `Area(x1, y1, x2, y2)`, both corners included, in any order: `Area(60, 15, 64, 18)` is 5 x 4 = 20 tiles. At most 2,500 tiles per area (`that area is too large`). A raw `[x1, y1, x2, y2]` list is not an area.
- Every farming action takes `where`: a tile, an Area, or a list of those or of things with a `.tile` (`docs("moving#Tiles and areas")`).
- Every farming action works only on the tiles that need it, and skips the rest, so an area can safely include tilled or planted ground.
- A malformed `where` raises `TypeError`.

To find good ground, look at the map in your wake-up or `here.map()`, or ask `here.find("tillable")`, `here.find("tilled_empty")`, `here.find("water")` (`docs("facts#Here.find")`).

## The daily chores
| action | does | details |
|---|---|---|
| `gather("crops")` | picks every ripe crop here | `docs("gather")` |
| `water(where=None)` | waters the thirsty crops, refilling the can | `docs("farming/watering")` |
| `pet()` | pets the animals here | `docs("farming/animals#pet")` |
| `gather("machines")` | collects what machines here have finished | `docs("gather")` |
| `gather("forage")` | picks up the forage here | `docs("gather")` |

Rain waters every crop outdoors, so on rainy days `water()` finds nothing to do (it returns at once, costing no energy).

## Planting
`till(where)`, then `plant(seeds, where)`, then `water(where)`. Seeds must be in season, and crops die when the season changes: check with `catalog["Parsnip Seeds"].crop`. See `docs("farming/crops")`, and for which crops pay, `docs("strategy#Seasons and crops")`.

## Clearing ground
`gather(["weeds", "twigs", "stone"], plot)` clears the small debris, `gather("debris", plot)` everything (trees, stumps, boulders, grass too). See `docs("farming/clearing")`.

## Placing things
`place(item, where)` puts down chests, scarecrows, sprinklers, machines and other placeable items. See `docs("farming/crops#place")`.

## Energy
Each swing of the hoe, watering can, axe or pickaxe costs energy (about 2 at the start, less as the skill grows); the scythe is free. Watch `farmer.energy`: below 15 the safety raises `LowEnergy` (`docs("exceptions#LowEnergy")`).

## Results
Every farming action returns a dict of counts. If some targets were left, it adds `"left": [[x, y], ...]` (at most 20) and `"why_left"`: what blocked the way, or what else stopped it. A job stopped partway (no energy, a question) says what it had done, like `(watered 7)`, at the end of the error.
