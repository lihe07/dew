# Combat
The guard (the combat reflex that runs during every action), fight for clearing monsters now, food, tactics, and the health limits.

## guard
`guard(fight="near", flee_below=0.3, avoid=())`

A setting, not an action: it returns at once and applies to every action after it in this script (each new script starts with the default). While any action runs (walking, `gather`, `wait`, ...), the guard watches the monsters every few game ticks:
- `fight="near"` (the default): when a monster comes within about 4 tiles and you carry a weapon, the action pauses, the farmer fights it, then the action goes on.
- `fight="all"`: as near, and between steps (before each action, between `gather` targets) it hunts every monster on the floor.
- `fight="off"`: never fights; walks keep away from monsters, and it still eats or retreats when hurt.
- `flee_below`: a fraction of max health. Below it, with monsters close, it eats the food that heals most cheaply; with no food it retreats (up the ladder out of the mines, else a step away) and raises `LowHealth` (`retreating: health is 30/150 and there's no food`) once, until health recovers.
- `avoid`: monster names never to fight, like `["Serpent"]` (or one name).

Without a weapon the guard doesn't fight, only eats or retreats. Returns a line like `guard: near, flee below 30% health`.

```python
guard("all")                            # clear every floor while descending
guard("near", flee_below=0.4, avoid=["Mummy"])
```

## fight
`fight()`

Takes your weapon and fights every monster within 12 tiles until none is left in range (or about a minute of game ticks passes), then picks up their loot. It keeps its distance and dodges:
- **Spacing.** It holds just inside the swing's reach, so a monster walks into the blade and not into the farmer. It swings when the monster will be in reach as the hit lands. After a hit, while the monster can't be hurt for a moment, it backs off to about two tiles.
- **Dodging.** Every few ticks it judges the eight steps and standing still, about 8 ticks ahead: where each monster is heading, projectiles in flight (skeletons' bones, shooters' shots), a spiker's row and column, and walls and corners. It takes the safest step that keeps the spacing. It won't start a swing that another monster or a projectile would interrupt.
- **Monsters a weapon can't hurt:** spikers, armored bugs (without the Bug Killer enchantment), and mummies while crumbled. It never targets them, only dodges them. When nothing hurtable is left in range, it keeps out of their way until none is close.
- It walks around walls to reach a far monster, breaks a rock that blocks a hit, uses the pickaxe on rock crabs still in their shells, and leaves alone a monster it can't reach (on a wall, behind rocks).

Returns `{"killed": n, "ate": [names], "got": {item: count}}`.

Raises:
- `NotEnough` without a weapon (`you don't have a weapon`).
- `LowHealth` under `flee_below` with no food: `retreating: health is 30/150 and there's no food (killed 3)`. Leave: `go("Farm")`.

```python
r = fight()
print("killed", r["killed"], "loot", r["got"])
```

## Food
Mid-fight, below 50% health, the reflex eats the item that heals most cheaply (sell price per health point), so valuables aren't eaten; it eats only when no monster is within about 3 tiles, or when health is under `flee_below`. Only items with `.healing` above 0 count. To eat yourself: `item.eat()` (`docs("items#Item.eat")`). `pick("the cheapest food", backpack.food).eat()` lets the judge choose; for a sure answer compare `.price` and `.healing` in Python.

## Tactics
In `mine.descend()`, when monsters are close and a ladder down is in sight, a fast model chooses between fighting first (safer at high health, gives combat experience and loot) and taking the ladder now (better at low health or against many monsters). When it's unsure or unavailable, it fights. You don't control it, but you can keep health high and carry food.

## On the way
A monster that blocks a walk is fought if you have a weapon (a few times at most, and not with `guard("off")`), then walked around or past. See `docs("moving#Walking around people and monsters")`.

## Limits
| limit | what happens |
|---|---|
| health under 50%, with food, mid-fight | the reflex eats |
| health under `flee_below` (30%), monsters close | the guard eats, or retreats and raises `LowHealth` once |
| health under 30% | the safety raises `LowHealth` once (`docs("exceptions#LowHealth")`) |
| energy under 15 | the safety raises `LowEnergy` once |

What to do with each: `docs("exceptions#Catching patterns")`.
