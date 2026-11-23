# Moving
Getting around with go: places by loose names, people (followed as they walk), tiles, doors and locked buildings, and how long walks take.

## go
`go(where, beside=False)`

Walks the farmer somewhere, through doors and exits, and returns when it's there. `where` is one of:
- **a place**, by any loose name: `"Pierre's"`, `"the beach"`, `"Mine"`, `"Farm"`, `"home"`. See `docs("moving#Place names")`.
- **a person**, by name: `"Haley"`, `"Lewis"`. They're found wherever they are today and followed as they walk, until the farmer is beside them.
- **a tile** `(x, y)` or `[x, y]` in the *current* location. With `beside=True`, it stops next to the tile instead of on it.

Returns:
- for a place: `None`;
- for a person: `"beside Haley"`;
- for a tile: `None`, or, if the tile can't be stood on (a chest, a counter, a bin), a note like `[64, 15] is a Chest, so you stopped beside it`.

Raises:
- `Closed` when a door on the way is locked (`the way to SeedShop is closed: ...`, with what the game said), or the only routes go through doors locked now.
- `Unreachable` for a name that isn't a place or a person (`no walking route from Farm to 'Pierrs'; check the name, or whether it needs a menu like the mine elevator or bus`), for someone who keeps walking away (`couldn't catch up with Haley`), or someone who kept standing in the way.
- `ActionFailed` for a tile with no path to it: `can't reach ... no walkable path`, or `debris is in the way, clear [[x, y], ...] first`. (Clear it with `clear`: `docs("farming/clearing")`.)
- `TypeError` for a tile that isn't two whole numbers.

```python
go("Pierre's")          # the shop
go("Haley")             # the person, wherever she is
go((64, 15), beside=True)
```

## Place names
Names are matched loosely: case, spaces and punctuation don't matter, and a leading "the" and a trailing "'s" are dropped. The game's own location names and display names both work (`"Beach"`, `"Forest"`, `"Town"`, `"Mountain"`, `"BusStop"`, `"Saloon"`, `"Railroad"`, `"Desert"` once it's reachable), and close misspellings are corrected.

Shortcuts the agent knows:
| say | goes to |
|---|---|
| pierre, pierre's, general store | SeedShop (Pierre's) |
| joja | JojaMart |
| blacksmith, clint | Blacksmith |
| carpenter, carpenter shop, robin | ScienceHouse (Robin's) |
| marnie, ranch | AnimalShop (Marnie's ranch) |
| willy, fish shop | FishShop |
| museum, library | ArchaeologyHouse |
| clinic, harvey | Hospital |
| guild, adventurer's guild | AdventureGuild |
| wizard, wizard tower, tower | WizardHouse |
| community center, cc | CommunityCenter |
| mines | Mine (the entrance) |
| home, house, farmhouse | FarmHouse (inside) |

`go("Farm")` arrives by the farmhouse door, on the side of the farm the house is on (farms are often split by debris).

## People or places
A name is first tried as a person, unless it has an apostrophe:
- `go("Pierre")` follows Pierre the man, wherever he is (he may be out in town).
- `go("Pierre's")` goes to his shop.
- Person names are matched exactly as the game spells them (`"Pierre"`, not `"pierre"`); a lowercase name usually falls through to the place shortcuts above.

People who aren't on the friendship list (Marlon, Gil, Gunther, Morris, Krobus, the Dwarf, Sandy, ...) are found only where they work or in the current location.

## Walking around people and monsters
- People and animals in the way are waited for (they usually walk on), then walked around. Someone who keeps standing in the only way through ends the walk with `Unreachable` (`couldn't reach ...: Lewis kept standing in the way near [...]`).
- Groups of monsters are walked around. A monster that blocks the way is fought if you carry a weapon (a few times at most), else avoided; if it can't be shaken, the farmer walks on past it.
- A walk is interrupted when health falls below 30% (see `docs("exceptions#Questions and cutscenes")`).

## Doors and locked buildings
Doors are opened on the way. A locked door (a shop before opening time, someone's house, a room you haven't unlocked) makes `go` look for another route; if there's none, it raises `Closed` with what the game said. Most shops open at 9am: see `docs("items/shops#Opening hours")`.

```python
try:
    go("Pierre's")
except Closed:
    wait_until("9:00 am")
    go("Pierre's")
```

Some places need a menu, not a walk: the mine's elevator (`mine()` uses it, `docs("mining")`), the bus to the desert, a minecart. Use `screen` for those (`docs("screen")`).

## From the mines
Inside the mines, `go` to anywhere first climbs back out (the ladder up leads to the mine entrance), then walks. `go("Farm")` from the mines is the usual way home.

## How far things are
There's no distance function, but walks have a rough cost: about 4 tiles per game minute, plus about 2 minutes per door. From the Mountain or the Beach to the farmhouse takes about 50 game minutes; from the mines, add 20 minutes to climb out. Plan errands so they chain (farm → town → beach → farm), and leave time to get home: see `docs("time#Getting home")`.

`exits()` lists where the current location leads (`docs("facts#exits")`), and `look_map()` shows what's around (`docs("facts#look_map")`).

## Tiles
A tile is `(x, y)` in the current location, with x growing right and y growing down. Coordinates change meaning when you change location: go to the place first, then use its tiles. The map in each wake-up and `look_map()` show coordinates; `tile_info(x, y)` says what's on one (`docs("facts#tile_info")`).
