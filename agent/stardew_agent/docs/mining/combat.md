# Combat
fight clears the monsters around you; how the reflexes, tactics, food and retreat limits work, in fights and inside mine.

## fight
`fight()`

Takes your weapon and fights every monster within 12 tiles until none is left in range (or about a minute of game ticks passes). It aims where monsters will be, steps back from ones too close, walks around walls to reach them, breaks a rock that blocks a hit, uses the pickaxe on rock crabs still in their shells, and leaves alone a monster it can't hurt (on a wall, behind rocks).

Returns `{"killed": n, "ate": [names]}`.

Raises:
- `NotEnough` without a weapon (`you don't have a weapon`).
- `LowHealth` when health falls under 25% and there's no food: `retreating: health is 30/150 and there's no food (killed 3)`. Leave: `go("Farm")`.
- `LowHealth` from the safety, under 30% (`docs("exceptions#LowHealth")`).

```python
r = fight()
print("killed", r["killed"])
```

## Food
Below 50% health, `fight` and `mine` eat the item that heals most cheaply (sell price per health point), so valuables aren't eaten; they eat mid-fight only when no monster is within about 3 tiles, or when health is under 25%. Only items with `.healing` above 0 count. To eat yourself: `eat(item)` (`docs("items#eat")`). `pick("the cheapest food", backpack.food)` lets the judge choose; for a sure answer compare `.price` and `.healing` in Python.

## Tactics
Inside `mine`, when monsters are close and a ladder down is in sight, a fast model chooses between fighting first (safer at high health, gives combat experience and loot) and taking the ladder now (better at low health or against many monsters). When it's unsure or unavailable, it fights. You don't control it, but you can keep health high and carry food.

## On the way
Walks (`go`, chores) fight a monster that blocks the way if you have a weapon, a few times at most, then go around or past it. See `docs("moving#Walking around people and monsters")`.

## Limits
| limit | what happens |
|---|---|
| health under 50%, with food | eats (`mine`, `fight`) |
| health under 30% | the safety raises `LowHealth` once |
| health under 25%, no food | `mine` and `fight` raise `Question` (`health is ...` / `retreating: ...`) |
| energy under 30 | `mine` raises `Question` (`energy is ...`) |
| energy under 15 | the safety raises `LowEnergy` once |

What to do with each: `docs("exceptions#Catching patterns")`.
