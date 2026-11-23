# Facts
Reading the game from a script: money, health, energy, here, state, find and its kinds, item_info, tile_info, exits, look_map, quests, recipes, shop_stock and screenshot.

## Reading facts
Facts only read; they never move the farmer or trigger the safety, but each is one SDK call (`docs("scripts#Budgets")`). Print them to see them yourself: the result comes with your next wake-up.

## money
`money()`: your gold, an int.

## health
`health()`: current health, an int. The maximum is in the status line (`health 120/150`).

## energy
`energy()`: current energy, an int (270 at the start; more with stardrops).

## here
`here()`: the current location's game name, like `"Farm"`, `"SeedShop"`, `"Beach"`, `"UndergroundMine23"`.

## time
`time()` and `minutes_until_2am()`: see `docs("time#time")`.

## state
`state()`: a dict of the day's facts:
| key | example |
|---|---|
| `date` | `"Mon Spring 1, year 1 (27 days left in the season)"` |
| `time` | `"6:10 am"` |
| `weather` | `"Sun, tomorrow Rain"` |
| `luck` | `"very good"`, `"good"`, `"neutral"`, `"bad"`, `"very bad"` |
| `skills` | `{"farming": 1, "mining": 0, "foraging": 2, "fishing": 0, "combat": 0}` |
| `free_inventory_slots` | `5` |
| `buffs` | `["Speed"]` |
| `farm_crops` | `"40 planted, 12 thirsty, 3 ready, 0 dead"` (the farm, from anywhere) |
| `mail` | `"2 letter(s) in the mailbox at ... on the Farm"` with the mailbox's tile, or None |
| `festival_today`, `at_festival` | True / the festival's name, or None |
| `birthday_today` | `"Haley"`, or None |
| `mine_level` | the mine floor you're on, or None |
| `quests` | `["Introductions", "How To Win Friends (complete: collect the reward in the journal)"]` |
| `menu` | the open menu, described as by `screen("look")`, or None |

```python
s = state()
if "Rain" in s["weather"].split(",")[0]:
    print("rain today: no watering")
```

## find
`find(kind, name=None)`

A list of `{"x": x, "y": y, "what": text}` in the current location, nearest first, at most 100. With `name`, only those whose `what` contains it (no case). Loop over it, slice it, or take `[0]` (check it isn't empty first). Raises `TypeError` for an unknown kind.

| kind | finds | `what` |
|---|---|---|
| `crops` | planted crops | the crop, `(ready)`, `(dead)` or `(4 days left)` |
| `crops_ready` | crops ready to harvest | as above |
| `crops_thirsty` | crops that need water today | as above |
| `tilled_empty` | tilled soil without a crop | `dirt` |
| `tillable` | diggable ground with nothing on it | `tillable` |
| `water` | tiles to refill the can from | `water` |
| `trees` | trees and fruit trees | `tree`, `fruit_tree` |
| `forage` | forage to pick up | its name |
| `artifact_spots` | artifact spots (dig with the hoe) | its name |
| `stones`, `weeds`, `twigs` | small debris | name / kind |
| `big_debris` | large stumps, logs, boulders | the kind, like `large stump` |
| `chests` | chests (and gifts) | its name |
| `machines` | machines | its name |
| `objects` | every object except weeds, twigs and stones | its name |
| `berry_bushes` | bushes with berries | the berries |
| `people`, `monsters`, `animals` | who's here | the name |
| `buildings`, `furniture` | buildings, furniture | the name |
| `exits` | exits and doors | where it leads |
| `interactables` | things with an action (counters, signs, the mailbox) | the action |
| `ladders` | mine ladders and shafts | `up`, `down`, `shaft` |

```python
spots = find("forage")
print(len(spots), "forage here; nearest:", spots[:3])
```

## item_info
`item_info(name)`

What an item is, by name, whether or not you have it: `{"name", "id", "description", "category_name", "energy", "you_sell_for"}`, and for seeds a `"crop"`: `{"seasons": "spring", "days": 4, "regrows": "no, one harvest per seed", "gives": "Parsnip", "sells_for": 35}`. Raises `ActionFailed` for an unknown name (`no item called 'X'`). It doesn't say what a shop charges: use `shop_stock()`.

```python
print(item_info("Cauliflower Seeds")["crop"])
```

## tile_info
`tile_info(x, y)`

What's on one tile here: any of `"object"`, `"terrain"`, `"npc"`, `"exit"`, `"action"` (each the row the game reports), plus `"walkable"` (bool), `"water"` and `"diggable"` (True or None). Raises `ActionFailed` for a tile outside the map.

## exits
`exits()`: where the current location leads, one string each: `["Town via path (0,54)", "FarmHouse via door (64,15)"]`.

## look_map
`look_map(radius=14, center=None)`

A text map around the farmer (or around `center`, a tile), `radius` tiles each way (3 to 45; at most 28 up and down), with x rulers on top and y numbers down the left, a legend of the symbols used, and a list of notable things with their tiles (buildings, exits, objects and machines with what they're making, people, monsters and animals). The same kind of map comes with every wake-up; this one is bigger. Print it to read it.

Symbols: `@` you, `.` walkable, `#` blocked, `~` water, `T` tree, `t` sapling, `o` stone, `w` weeds, `/` twig, `"` grass, `S` stump/log/boulder, `h` tilled, `c` crop (dry), `v` crop (watered), `R` crop ready, `x` dead crop, `f` forage, `O` object/machine, `$` chest, `N` person, `M` monster, `A` animal, `D` door, `>` exit, `!` interactable, `F` furniture, `L` ladder, `B` bush, `%` building, `*` artifact spot.

```python
print(look_map(20))
```

## quests
`quests()`: the quest log, a list of dicts with `"title"`, `"description"`, `"objectives"`, `"complete"`, `"reward"` (money, or None) and `"days_left"` (None if untimed); special orders have `"special": True`. Collect finished quests' money with `rewards()`, which returns `{"collected": "200g"}` (or `"nothing to collect"`).

## recipes
`recipes()`: what you can craft, one string each: `"Chest: <ingredients> [CAN CRAFT NOW]"` or `"... [missing ingredients]"`. Craft with `craft` (`docs("items#craft")`).

## shop_stock
`shop_stock()`: what the shop here sells; see `docs("items/shops#shop_stock")`.

## people
`people()`, `people_here()`: see `docs("people#people")`.

## screenshot
`screenshot(radius=None, grid=False)`

Takes a screenshot that you'll see at your next wake-up (the call returns a note, not the picture); people and things are labelled `name (x,y)`. `radius` (2 to 20) crops it around the farmer; `grid=True` overlays tile coordinates. For clicking at pixels with `screen("click", at=...)`, take it uncropped (`docs("screen#click")`). You can also take one yourself with the `screenshot` tool without disturbing the script.
