# Gather
gather takes, breaks, cuts, digs or collects many things at once, nearest first: its kinds (the same names as `here.find`), targets as tiles, tools, what it returns, and choosing targets in Python.

## gather
`gather(what, where=None, tool=None)`

Works through every target in the current location: walks beside the nearest (by walking distance), deals with it, then looks again (things move, and clearing one often opens the way to others), until none are left. Each target gets the tool what's on it needs: pickaxe for rocks and boulders, axe for trees, stumps, logs and twigs, scythe for weeds, grass and dead crops (your weapon if you have no scythe), hoe for artifact spots, your hands for forage, ripe crops (the scythe for wheat and the like) and machines. Afterwards it walks over what the targets dropped (stone, ore, wood, sap, fiber, seeds, ...) so it lands in the backpack.

- `what`: a kind (`"rocks"`), a list of kinds (`["weeds", "twigs", "stone"]`), or tiles: a Tile or `(x, y)`, an Area, or a list of tiles or of the Things `here.find(...)` and `mine` give (`docs("moving#Tiles and areas")`).
- `where`: only targets inside the box these tiles span (usually an Area, corners included).
- `tool`: use this tool on every target instead (`"pickaxe"`, `"axe"`, `"hoe"`, `"scythe"`, `"weapon"`). On listed tiles with nothing known on them, it swings the tool once each.

Returns `{"done": n, "got": {"Stone": 12, "Copper Ore": 3}}`: how many targets were dealt with, and what the backpack gained. When something is still there it adds `"left": [[x, y], ...]` (at most 20) and `"why_left"`: out of reach (and why), big debris your tools can't break yet, or `the backpack is full`. A full backpack stops it; it doesn't raise.

Raises `NotEnough` when a tool is missing (`you don't have a pickaxe`) or energy runs out (`out of energy; eat something or sleep (done 12)`), `TypeError` for an unknown kind. While it runs, the guard fights monsters that come close (`docs("mining/combat#guard")`).

```python
gather("crops")                         # harvest everything ripe here
gather("forage")
gather("debris", Area(55, 12, 70, 22))
r = gather("rocks")
print(r["got"], r.get("why_left"))
```

## Kinds
`here.find(kind)` takes the same names and finds the same things (`docs("facts#Here.find")`).

| kind | what | tool |
|---|---|---|
| `"rocks"` | every breakable rock: `"stone"`, `"ore"` and `"gems"` | pickaxe |
| `"stone"` | plain stones | pickaxe |
| `"ore"` | copper, iron, gold, iridium and radioactive nodes | pickaxe |
| `"gems"` | gem nodes (diamond, ruby, jade, amethyst, topaz, emerald, aquamarine) and mystic stones | pickaxe |
| `"boulders"` | boulders (steel pickaxe or better), meteorites (gold or better), mine boulders | pickaxe |
| `"trees"` | grown wild trees, and the stumps they leave (not fruit trees) | axe |
| `"saplings"` | young trees | axe |
| `"stumps"` | tree stumps, and large stumps (copper axe or better) | axe |
| `"logs"` | large logs (steel axe or better) | axe |
| `"twigs"` | twigs | axe |
| `"weeds"` | weeds | scythe |
| `"grass"` | long grass | scythe |
| `"dead_crops"` | dead crops | scythe |
| `"debris"` | everything above: all of it is clearable | each its own |
| `"forage"` | forage lying about (leeks, daffodils, shells, mushrooms, ...) | hands |
| `"dig_spots"` | artifact spots (the wiggling worms) | hoe |
| `"crops"` | ripe crops | hands, or scythe |
| `"items"` | items lying on the ground | walking over them |
| `"machines"` | machines with finished output (it doesn't load them again) | hands |

Big debris your tools can't break yet is left out and counted in `why_left`; check your tools' `.upgrade` (`docs("items#Item fields")`).

## Choosing targets
Pick targets in Python from `here.find(...)` or `mine`'s lists, then pass the list. Each is a Thing with `.kind`, `.name`, `.x`, `.y` and `.tile`, and from `mine` also `.dist` (`docs("mining#mine")`).

```python
nearest = sorted(mine.rocks, key=lambda r: r.dist)[:5]
gather(nearest)

ore = [r for r in mine.rocks if r.kind in ("ore", "gems")]
gather(ore or "rocks")                  # the valuable ones first, else anything

leeks = here.find("forage", "Leek")
gather(leeks)

near_house = Area(54, 5, 74, 25)
gather([t for t in here.find("weeds") if t.tile in near_house])
```

## Good practice
- Trees and boulders take many swings and a lot of energy: `"debris"` includes trees and grass, so for just small debris name the kinds: `gather(["weeds", "twigs", "stone"], plot)`.
- Keep a free slot or two: new kinds of drops each take a slot, and a full backpack stops `gather` (`docs("items/shipping")`, `docs("items/chests")`).
- Forage appears fresh each day in the Forest, the Mountain, the Beach, the Bus Stop, the Backwoods and town: free money, no energy. Berry bushes (`here.find("berry_bushes")`) aren't forage: shake one with `screen.interact(bush)`, then `gather("items")`.
- When a walk fails because debris is in the way, the error names the tiles: `debris is in the way, clear [[30, 40], [31, 40]] first`; `gather([(30, 40), (31, 40)])` clears them. `gather` itself cuts through what walls it in.
