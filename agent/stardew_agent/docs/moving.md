# Moving
Getting around with go: places by their game names, people (followed as they walk), tiles and areas, doors and locked buildings, and how long walks take.

## go
`go(target)`

Walks the farmer somewhere, through doors and exits, and returns when it's there. `target` is one of:
- **a place**: its game location name as text (`"SeedShop"`, `"Beach"`, `"Mine"`, `"Farm"`, `"FarmHouse"`), or a Place (`places["SeedShop"]`, `docs("moving#Place names")`). Text is always a place, never a person.
- **a person**: a Person record, like `people["Haley"]` (`docs("people#people list")`). They're found wherever they are today and followed as they walk, until the farmer is beside them. `go(people["Haley"].tile)` goes to where she was when read, without following.
- **a tile**: `Tile(x, y)` or plain `(x, y)` in the current location; `Tile(x, y, "Town")` in another place: it goes there, coming in on the side from which the tile can be reached.
- **an area or several tiles**: `Area(x1, y1, x2, y2)` (both corners included), or a list of tiles or of things with a `.tile` (from `here.find`, `here.chests`, `mine.rocks`, ...): the nearest one it can reach.
- **a list mixing these**: the nearest of them, by about how long the walk takes: `go(["SeedShop", "JojaMart"])`.

A goal tile that can't be stood on (a chest, a counter, a rock, a person) is stopped next to. All of them use the same pathfinding: doors, people and monsters in the way, and the reasons below are the same.

Returns:
- for a place: `None`;
- for a person: `"beside Haley"`;
- for a tile: `None`, or, if the tile can't be stood on, a note like `[64, 15] is a Chest, so you stopped beside it`.

Raises:
- `Closed` when a door on the way is locked (`the way to SeedShop is closed: ...`, with what the game said), or the only routes go through doors locked now.
- `ActionFailed` for a name that isn't a place, with a "did you mean" list of the nearest game names.
- `Unreachable` for a place with no walking route (`no walking route from Farm to Desert; ... whether it needs a menu like the mine elevator or bus`), for someone who keeps walking away (`couldn't catch up with Haley`), or someone who kept standing in the way.
- `ActionFailed` for a tile with no path to it: `can't reach ... no walkable path`, or `debris is in the way, clear [[x, y], ...] first`. (Clear it with `gather`: `docs("farming/clearing")`.)
- `ActionFailed` for tiles in another place than the one you're in, given as an Area or a list: `those tiles are in Town, and you're in Farm: go there first`.
- `TypeError` for a tile that isn't two whole numbers.

```python
go("SeedShop")                   # Pierre's shop
go(people["Haley"])              # the person, wherever she is
go(here.chests[0])               # stops beside the chest
go(Tile(45, 70, "Town"))         # a tile in another place
go(Area(60, 15, 64, 18))         # the nearest tile of an area
```

## Tiles and areas
Every location has its own grid of tiles. x grows to the right, y grows *downward*, and `(0, 0)` is the top-left corner of the location's map. One tile is one step.

- `Tile(x, y, place=None)`: one tile. `place` None means the current location; wherever a Tile goes, a plain `(x, y)` does too. A tile is only meaningful in its location: `(45, 70)` in Town is not `(45, 70)` on the Farm, so give the place, `Tile(45, 70, "Town")`, when you aren't there.
- `Area(x1, y1, x2, y2, place=None)`: every tile between two corners, both included, in any order: `Area(60, 15, 64, 18)` is 5 x 4 = 20 tiles. `tile in area` tests a tile; iterating it, or `.tiles`, gives its Tiles; `.center` is its middle. At most 2,500 tiles (`that area is too large`).
- `where` (the tiles an action works on, as in `water(where)`, `till(where)`, `screen.interact(where)`): a Tile or `(x, y)`, an Area, or a list of those or of things with a `.tile` (a Thing, a Chest, a Person). A raw `[x1, y1, x2, y2]` list is not an area: use `Area(...)`.

Tiles in another place than here raise `ActionFailed` (`those tiles are in X, and you're in Y: go there first`), except as a `go` target. Your own tile is `farmer.tile` (and in the status line, `Farm (64,15)`); the map in each wake-up is drawn around you (`@`) with its coordinates; `here.info(where)` says what's on a tile (`docs("facts#Here.info")`).

## Place names
Places are named only by their exact game location name (case doesn't matter): there's no guessing. An unknown name raises `ActionFailed` with a "did you mean" list of the nearest names. `places` lists every location (`for p in places: print(p.name, p.title)`), and `here.exits` the ways out of this one, each named where it leads.

Common game names:
| name | what |
|---|---|
| `Farm`, `FarmHouse` | your farm, your house (inside) |
| `Town`, `Mountain`, `Forest`, `Beach`, `BusStop`, `Backwoods`, `Railroad` | outdoors |
| `SeedShop` | Pierre's General Store |
| `JojaMart` | JojaMart |
| `Blacksmith` | Clint's |
| `AnimalShop` | Marnie's ranch |
| `ScienceHouse` | Robin's (the carpenter) |
| `FishShop` | Willy's |
| `Saloon` | the Stardrop Saloon |
| `ArchaeologyHouse` | the museum and library |
| `WizardHouse` | the wizard's tower |
| `AdventureGuild` | the Adventurer's Guild |
| `Hospital` | the clinic |
| `ManorHouse` | Lewis's house |
| `Mine` | the mine entrance |
| `BathHouse_Pool` | the spa's pool |

Someone's house: their Person's `.home` (a Place), like `go(people["Haley"].home)`. A shop's keeper: its Place's `.keeper` (a Person, or None).

`go("Farm")` arrives by the farmhouse door, on the side of the farm the house is on (farms are often split by debris).

## Place fields
`places["SeedShop"]` (or `here`, or a Tile's `.location`) is a Place: `.name` (the game name), `.title` (like `"Pierre's General Store"`), `.tile` (where you come in, walking from here), `.minutes` (about how long the walk there takes), `.hours` and `.open` (for the well-known shops), `.keeper`, `.shop` (its counter, `docs("items/shops")`) and `.exits`. The fields that depend on where you are are None when you're there.

```python
pierre = places["SeedShop"]
if pierre.open and pierre.minutes < 60:
    go(pierre)
```

## People or places
Text is always a place: `go("SeedShop")` goes to Pierre's shop, and `go("Pierre")` raises `ActionFailed` (no place called that). To go to the man himself, wherever he is, `go(people["Pierre"])`.

People who aren't on the friendship list (Marlon, Gil, Gunther, Morris, Krobus, the Dwarf, Sandy, ...) are found only in the current location: `here.people`.

## Walking around people and monsters
- People and animals in the way are waited for (they usually walk on), then walked around. Someone who keeps standing in the only way through ends the walk with `Unreachable` (`couldn't reach ...: Lewis kept standing in the way near [...]`).
- Groups of monsters are walked around. A monster that blocks the way is fought if you carry a weapon (a few times at most), else avoided; if it can't be shaken, the farmer walks on past it.
- A walk is interrupted when health falls below 30% (see `docs("exceptions#Questions and cutscenes")`).

## Doors and locked buildings
Doors are opened on the way. A locked door (a shop before opening time, someone's house, a room you haven't unlocked) makes `go` look for another route; if there's none, it raises `Closed` with what the game said. Most shops open at 9am: see `docs("items/shops#Opening hours")`, or a Place's `.open`.

```python
try:
    go("SeedShop")
except Closed:
    wait("9:00 am")
    go("SeedShop")
```

Some places need a menu, not a walk: the mine's elevator (`mine.descend(to=...)` uses it, `docs("mining#Mine.descend")`), the bus to the desert, a minecart. Use `screen` for those (`docs("screen")`).

## From the mines
Inside the mines, `go` to anywhere first climbs back out (the ladder up leads to the mine entrance), then walks. `go("Farm")` from the mines is the usual way home.

## How far things are
A Place's `.minutes` is about how many game minutes the walk there takes: about 4 tiles per game minute, plus about 2 minutes per door. From the Mountain or the Beach to the farmhouse takes about 50 game minutes; from the mines, add 20 minutes to climb out. Plan errands so they chain (farm → town → beach → farm), and leave time to get home: see `docs("time#Getting home")`.

`here.exits` lists where the current location leads, and `here.map()` shows what's around (`docs("facts#Here.map")`).
