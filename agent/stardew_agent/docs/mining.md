# Mining
The mines from a few pieces: mine.descend (ladders, shafts, the elevator), mine (what's on this floor), gather for rocks, and the guard that fights while you work; a whole trip as a script, and staying safe.

## mine
`mine` is the floor you're on, read live (it doesn't move the farmer, and works in queries):
| field | what |
|---|---|
| `mine.cave` | `"mines"`, `"skull"`, or None outside them |
| `mine.level` | the floor here (Skull Cavern floors count from 1 too), or None |
| `mine.deepest`, `mine.deepest_skull` | the deepest floor reached in the Mines, and in the Skull Cavern |
| `mine.ladders`, `mine.shafts` | ladders down, and shafts (holes down several floors) |
| `mine.rocks` | breakable rocks: `.kind` is `"stone"`, `"ore"` or `"gems"`, `.name` like `"copper"` |
| `mine.monsters` | monsters: `.name` like `"Green Slime"`, `.hp` |
| `mine.dig_spots`, `mine.chests`, `mine.items` | artifact spots, chests, items dropped on the ground |
| `mine.exit` | the ladder up (a Thing), or None |

The lists are Things, nearest first, with `.kind`, `.name`, `.x`, `.y`, `.tile`, and `.dist`: how many tiles away (steps across and down, not the walk). Pass them to `gather` or `go`. Outside the mines the lists are empty. Each field is read from the game when used, so keep a list in a variable when you use it twice.

```python
rocks = mine.rocks
print(mine.level, len(rocks), "rocks", [(m.name, m.hp) for m in mine.monsters])
```

## Mine.descend
`mine.descend(to=None)`

Goes down. From outside the mines it walks to the Mine entrance and goes in: by the elevator to the deepest floor it reaches (a multiple of 5) once you've been to floor 5 or deeper, otherwise down the ladder to floor 1. With `to` (5, 10, ... up to the deepest you've reached) it takes the elevator to that floor, from anywhere but the Skull Cavern. Inside the mines it walks to the nearest ladder down and takes it; with no ladder in sight but a shaft, it jumps down the shaft (several floors at once, and it costs some health).

The mines open on Spring 5 of the first year: before that a landslide blocks the path from the Mountain, and `mine.descend()` or `go("Mine")` raise `Closed`.

Returns `{"level": n}`, the floor you're on now.

Raises:
- `ActionFailed`: `no ladder or shaft in sight; break rocks to find one` (gather rocks first, below), `the elevator stops every 5 floors you've reached (...)`, `can't find the way into the mines`, `the elevator is only in the Mines` (`to` in the Skull Cavern).
- `Unreachable` when the ladder can't be reached.

When monsters are close and a ladder is in sight, a fast model decides whether to fight them first or go straight for the ladder (`docs("mining/combat#Tactics")`).

## Rocks
Rocks are broken with `gather` (`docs("gather")`): `gather("rocks")` breaks every rock here, nearest first, and picks up the stone, ore and gems they drop; `gather("ore")` only the ore. Choose rocks in Python from `mine.rocks`:

```python
rocks = mine.rocks
gather(rocks[:5])                                 # the five nearest
gather([r for r in rocks if r.kind != "stone"])   # only ore and gems
```

A floor's ladder down appears from a broken rock, or when every monster on an infested floor (monsters but no rocks) is dead: `fight()` or `guard("all")` (`docs("mining/combat")`).

## A trip
The pieces make a trip. Ten floors, breaking rocks until a ladder shows, home in time:

```python
guard("near", flee_below=0.3)           # the default; guard("all") clears each floor too
try:
    mine.descend()                      # into the mines
    for _ in range(10):
        if backpack.full or day.minutes_left < 330:
            break
        for _ in range(15):             # bounded: rocks out of reach never go away
            if mine.ladders or mine.shafts:
                break
            rocks = mine.rocks
            if rocks:
                gather(rocks[:3])       # the nearest few, then look again
            else:
                fight()                 # an infested floor: the ladder appears once they're dead
        print("floor", mine.descend()["level"])   # ActionFailed if still no ladder: you decide
except LowHealth:
    print("hurt; going home")
except (LowEnergy, TimeToGoHome):
    print("stopping")
go("Farm")
```
More trips are in `docs("examples")`.

## Floors and the elevator
- The elevator stops every 5 floors you've reached: `mine.descend(to=40)`. `mine.level` is the floor you're on, `mine.deepest` the deepest reached.
- Deeper floors have richer ore and tougher monsters: floors 1 to 39 give copper, 40 to 79 (icy) iron, 80 to 119 (lava) gold.
- A staircase item (`backpack["Staircase"].use()`) takes you straight down a floor (`docs("items#Item.use")`).

## Getting out
`go("Farm")` (or anywhere) from inside the mines climbs the ladder up, which leads to the entrance, then walks. It takes about 20 game minutes plus the walk home: from the mines to the farmhouse is about 70 to 80 game minutes in all, so leave by about 10:30pm. The safety raises `TimeToGoHome` when it's time (`docs("exceptions#TimeToGoHome")`).

## Safety in the mines
- The guard fights monsters that come close during every action, eats when hurt, and retreats when hurt without food (`docs("mining/combat#guard")`).
- Bring food that heals: `backpack.food` lists what you carry, with `.healing`. Good mine food: salads, fried eggs, cheese, salmonberries, cave carrots (`docs("mining/combat#Food")`).
- Watch energy: each rock costs energy (about 2 swings' worth with a basic pickaxe). Below 15 the safety raises `LowEnergy` (`docs("exceptions#LowEnergy")`).
- Keep backpack room: ore, stones and gems fill slots fast, and a full backpack stops `gather` (its `why_left` says so). `item.trash()` frees slots (`docs("items#Item.trash")`).
- When no script runs, the farmer fights close monsters, eats when badly hurt, leaves the mine if hurt without food, and breaks rocks beside it.
- Passing out in the mines (at 2am, or at 0 health) costs items and money. See `docs("time#Passing out")`.
