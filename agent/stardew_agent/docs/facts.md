# Facts
Reading the game from a script: farmer, day, here (find, map, info, exits, chests, people, shop), places, catalog, quests, recipes and people.

## Reading facts
`farmer`, `day`, `here`, `mine`, `backpack`, `people`, `quests`, `recipes`, `places` and `catalog` are objects read live: each field is read from the game when you use it, so read it again after an action to see the change (`backpack.free` after `gather`). Reading never moves the farmer or triggers the safety, and works in queries. Records they give (an Item, a Thing, a Person, a Chest) are a snapshot from when they were read, except a Person's `.tile`, which is read live. Print what you want to see yourself: it comes with your next wake-up.

An unknown name (`people["Hailey"]`, `catalog["Parsnp"]`, `recipes["X"]`, `places["X"]`, an unknown `here.find` kind) raises `ActionFailed` with a "did you mean" list of the nearest exact names; `backpack["X"]` raises `NotEnough` when you have none.

## farmer
| field | what |
|---|---|
| `farmer.energy`, `farmer.max_energy` | energy now and at most (270 at the start; more with stardrops) |
| `farmer.health`, `farmer.max_health` | health now and at most |
| `farmer.money` | your gold, an int |
| `farmer.place` | the current location's game name, like `"Farm"`, `"SeedShop"`, `"UndergroundMine23"` (the same as `here.name`) |
| `farmer.tile` | where you stand, a Tile |
| `farmer.held` | the Item in your hands, or None |
| `farmer.skills` | `{"farming": 1, "mining": 0, "foraging": 2, "fishing": 0, "combat": 0}` |

```python
if farmer.energy < 50 and backpack.food:
    backpack.food[0].eat()
```

## day
The clock, date, weather, luck, festival and birthday: see `docs("time#day")`.

## here
`here` is the Place you're in (`docs("moving#Place fields")`), read live, with what's in it:
- `here.name`, `here.title`: `"SeedShop"`, `"Pierre's General Store"`.
- `here.people`: the Persons here now (people off the friendship list too, with `met` False).
- `here.chests`: the Chests here, with what was in them (`docs("items/chests")`).
- `here.shop`: the shop counter here, or None (`docs("items/shops")`).
- `here.exits`: the ways out, as Things of kind `"exit"` named where they lead: `[e.name for e in here.exits]`.
- `here.keeper`, `here.hours`, `here.open`: the shop's keeper and hours, if it's a well-known shop.

## Here.find
`here.find(kind, name=None)`

The Things of a kind in the current location, nearest first, at most 100; pass them (or some of them) to `gather` or `go` to act on them. Each Thing has `.kind`, `.name` (what it is, as text), `.x`, `.y` and `.tile`. With `name`, only those whose name contains it (no case). Loop over them, slice them, or take `[0]` (check the list isn't empty first). An unknown kind raises `TypeError`.

Every kind `gather` takes finds the same things here, with the same names: `rocks`, `stone`, `ore`, `gems`, `boulders`, `trees`, `saplings`, `stumps`, `logs`, `twigs`, `weeds`, `grass`, `dead_crops`, `debris`, `forage`, `dig_spots`, `crops` (ripe), `items` (on the ground), `machines` (ready); the name is the thing's (for ore and gems, like `copper` or `ruby`). See `docs("gather#Kinds")`. Besides those:

| kind | finds | name |
|---|---|---|
| `planted` | every planted crop | the crop, `(dead)` or `(4 days left)` |
| `crops_thirsty` | crops that need water today | as above |
| `tilled_empty` | tilled soil without a crop | `dirt` |
| `tillable` | diggable ground with nothing on it | `tillable` |
| `water` | tiles to refill the can from | `water` |
| `fruit_trees` | fruit trees | `fruit_tree` |
| `objects` | every object except weeds, twigs and stones (machines too, ready or not) | its name |
| `berry_bushes` | bushes with berries | the berries |
| `monsters`, `animals` | who's here | the name |
| `buildings`, `furniture` | buildings, furniture | the name |
| `exits` | exits and doors | where it leads |
| `interactables` | things with an action (counters, signs, the mailbox) | the action |
| `ladders` | mine ladders and shafts | `up`, `down`, `shaft` |

Chests and people aren't kinds: `here.chests` and `here.people` give them as Chests and Persons, with their methods.

In the mines, `mine` gives the floor's ladders, rocks and monsters (`docs("mining#mine")`).

```python
spots = here.find("forage")
print(len(spots), "forage here; nearest:", [(t.name, t.x, t.y) for t in spots[:3]])
```

## Here.map
`here.map(radius=14, center=None)`

A text map around the farmer (or around `center`, a tile), `radius` tiles each way (3 to 45; at most 28 up and down), with x rulers on top and y numbers down the left, a legend of the symbols used, and a list of notable things with their tiles (buildings, exits, objects and machines with what they're making, people, monsters and animals). The same kind of map comes with every wake-up; this one is bigger. Print it to read it.

Symbols: `@` you, `.` walkable, `#` blocked, `~` water, `T` tree, `t` sapling, `o` stone, `w` weeds, `/` twig, `"` grass, `S` stump/log/boulder, `h` tilled, `c` crop (dry), `v` crop (watered), `R` crop ready, `x` dead crop, `f` forage, `O` object/machine, `$` chest, `N` person, `M` monster, `A` animal, `D` door, `>` exit, `!` interactable, `F` furniture, `L` ladder, `B` bush, `%` building, `*` artifact spot.

```python
print(here.map(20))
```

## Here.info
`here.info(where)`

What's on one tile here, as a TileInfo: `.walkable`, `.water`, `.diggable` (can be hoed), `.object` (its name), `.terrain` (its kind), `.npc` (who stands there), `.exit` (where it leads) and `.action` (a tile action, like a counter); each is None when there's nothing of it. Raises `ActionFailed` for a tile outside the map.

```python
t = here.info((64, 15))
print(t.object, t.walkable)
```

## places
`places["SeedShop"]` is a Place, by its game name (`docs("moving#Place names")`); iterating `places` gives every location. A Place's fields: `docs("moving#Place fields")`.

## catalog
`catalog["Parsnip Seeds"]`

What an item is, by its exact name, whether or not you have it, as an ItemInfo: `.name`, `.id`, `.description`, `.category`, `.energy`, `.sells_for`, and for seeds a `.crop`: `{"seasons": "spring", "days": 4, "regrows": "no, one harvest per seed", "gives": "Parsnip", "sells_for": 35}` (None for other items). An unknown name raises `ActionFailed`. It doesn't say what a shop charges: use `shop.stock()` (`docs("items/shops#Shop.stock")`).

```python
print(catalog["Cauliflower Seeds"].crop)
```

## quests
`quests` is the quest log: iterate it for Quest records, with `.title`, `.description`, `.objectives`, `.complete`, `.reward` (money, or None), `.days_left` (None if untimed) and `.special` (True for special orders).

```python
for q in quests:
    print(q.title, "done" if q.complete else q.objectives)
```

## Quests.collect
`quests.collect()`: collects the money of every finished quest, from the journal. Returns `{"collected": "200g"}`, or `"nothing to collect"`.

## recipes
`recipes` is what you can craft: iterate it for Recipe records with `.name`, `.needs` (the ingredients) and `.can_make` (whether you have them now), or take one by name, `recipes["Chest"]`. Craft with `.craft()` (`docs("items#Recipe.craft")`).

## people
`people` and `here.people`: see `docs("people#people list")`.
