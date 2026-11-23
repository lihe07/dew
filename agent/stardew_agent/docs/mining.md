# Mining
mine goes down through the mines: the elevator, rocks and ladders, fighting on the way, what it returns, and how to stay safe.

## mine
`mine(floors=1)`

Goes down `floors` floors (1 to 10). If you aren't in the mines yet, it travels to the Mine entrance and goes in: by the elevator to the deepest floor it reaches (a multiple of 5) once you've been to floor 5 or deeper, otherwise down the ladder to floor 1. Then, on each floor:
1. if health is under 50% and you carry food that heals, it eats the cheapest (`docs("mining/combat#Food")`);
2. if monsters are close and you have a weapon, it fights them, unless a ladder down is in sight and the tactics model decides to go for the ladder instead (`docs("mining/combat#Tactics")`);
3. if a ladder or shaft down is in sight, it takes it;
4. otherwise it breaks rocks with the pickaxe until a ladder appears, cutting through weeds or rocks that wall it in. On a floor with no rocks but monsters, it fights them all, since the ladder appears when they're dead.

Returns `{"level": n, "descended": k, "rocks": r, "killed": m}`, plus `"ate": [names]` if it ate, and `"note": "a shaft: answer(0) jumps in"` when it stepped on a shaft: the game is asking whether to jump (it drops you several floors and costs some health). `answer(0)` jumps, `answer(1)` declines.

Raises:
- `LowEnergy` at its own energy limit (`energy is 25; {...}`, under 30) and `LowHealth` at its health limit (`health is 30; {...}`, under 25% with no food). The text includes the result so far. See `docs("exceptions#Safety")`.
- `LowHealth`, `LowEnergy`, `TimeToGoHome` from the safety (`docs("exceptions#Safety")`). LowEnergy (under 15) rarely comes first here, since `mine` stops itself at 30.
- `ActionFailed`: `no rocks or ladder on this floor; {...}`, `the elevator doesn't offer floor 40`, `can't find the way into the mines`.
- `NotEnough` without a pickaxe.

```python
go("Mine")
r = mine(3)
print("now on floor", r["level"], "rocks", r["rocks"], "killed", r["killed"])
```

## Floors and the elevator
- The elevator stops every 5 floors you've reached; `mine` uses it only when entering from outside, and takes you to the deepest stop. `state()["mine_level"]` is the floor you're on (None outside the mines).
- Deeper floors have richer ore and tougher monsters: floors 1 to 39 give copper, 40 to 79 (icy) iron, 80 to 119 (lava) gold. A floor's ladder down appears from a broken rock, or when every monster on an infested floor is dead.
- A staircase item (`use("Staircase")`) takes you straight down a floor (`docs("items#use")`).

## Getting out
`go("Farm")` (or anywhere) from inside the mines climbs the ladder up, which leads to the entrance, then walks. It takes about 20 game minutes plus the walk home: from the mines to the farmhouse is about 70 to 80 game minutes in all, so leave by about 10:30pm. The safety raises `TimeToGoHome` when it's time (`docs("exceptions#TimeToGoHome")`).

## Safety in the mines
- Bring food that heals: `mine` and `fight` eat it on their own below 50% health. Without food, leave when `LowHealth` comes (under 30%).
- Watch energy: each rock costs energy (about 2 swings' worth with a basic pickaxe). `mine` stops itself under 30.
- Keep backpack room: ore, stones and gems fill slots fast. `backpack.full` ends a trip's usefulness; `docs("items#trash")` frees slots.
- When no script runs, the farmer fights close monsters, eats when badly hurt, and leaves the mine if hurt without food.
- Passing out in the mines (at 2am, or at 0 health) costs items and money. See `docs("time#Passing out")`.

## Food
What heals and how much is in each item's `.healing` (and `.energy`): `backpack.food` lists everything edible you carry. Good mine food: salads, fried eggs, cheese, salmonberries, cave carrots. See `docs("items#eat")` and `docs("mining/combat#Food")`.

## A trip
```python
try:
    go("Mine")
    while minutes_until_2am() > 330 and not backpack.full:
        r = mine(3)
        if "note" in r:
            answer(0)
except LowHealth:
    print("hurt; going home")
except (LowEnergy, TimeToGoHome):
    print("stopping")
go("Farm")
```
More complete trips are in `docs("examples")`. Fighting outside `mine`: `docs("mining/combat")`.
