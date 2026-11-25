# Examples
Complete scripts for common stretches of a day, to copy and adapt: chores and shopping, looking around, the mines, fishing, gifts, foraging, a new plot, the farm chest.

## Morning chores, then seeds
Chores first, then Pierre's when it opens, then plant by the house. Tiles are examples: use your own plot's.
```python
gather("crops")
water()                      # returns at once on rainy days
pet()
gather("machines")
try:
    go("SeedShop")
except Closed:               # before 9am the door is locked
    wait("9:00 am")
    go("SeedShop")
price = 20
for i in here.shop.stock():
    if i.name == "Parsnip Seeds":
        price = i.price
count = min(30, (farmer.money - 100) // price)
if count > 0:
    print(here.shop.buy("Parsnip Seeds", count))
    plot = Area(60, 15, 65, 19)
    go("Farm")
    till(plot)
    r = plant("Parsnip Seeds", plot)
    water(plot)
    print("planted", r["placed"], "seeds")
```

## Find things out before deciding
Facts only: nothing moves, and everything printed comes back when the script ends.
```python
print(day.date, day.time, day.weather, "tomorrow", day.tomorrow, "luck", day.luck)
print("people:", [(p.name, p.hearts, p.place) for p in people if p.met])
print("ripe here:", len(here.find("crops")), "thirsty:", len(here.find("crops_thirsty")))
print("forage here:", [(t.name, t.x, t.y) for t in here.find("forage")[:5]])
print("parsnip:", catalog["Parsnip Seeds"].crop)
print("cauliflower:", catalog["Cauliflower Seeds"].crop)
items = backpack.items
print("food:", [(i.name, i.count, i.energy, i.healing) for i in items if i.energy > 0])
print("can:", [(i.water, i.water_max) for i in items if i.water is not None], "free slots:", backpack.free)
print("quests:", [q.title for q in quests])
```

## A rainy day in the mines, home in time
Rain waters the crops, so the day is free for the mines. The guard fights what comes close and eats when hurt; the loop breaks the nearest rocks until a ladder shows, prefers ore, and leaves in time to be home by midnight.
```python
if "Rain" not in day.weather:
    water()
gather("crops")
guard("near", flee_below=0.35)
level = 0
try:
    mine.descend()                               # the elevator's deepest stop
    while day.minutes_left > 330 and not backpack.full:
        if mine.ladders or mine.shafts:
            level = mine.descend()["level"]
            continue
        rocks = mine.rocks
        if not rocks:
            fight()                              # infested: the ladder appears once they're dead
            if not mine.ladders:
                break
            continue
        ore = [r for r in rocks if r.kind in ("ore", "gems")]
        r = gather(ore[:4] or sorted(rocks, key=lambda r: r.dist)[:4])
        if "left" in r and not r["done"]:
            print("stuck on floor", mine.level, r["why_left"])
            break
except LowHealth:
    print("hurt, no food: going home")
except (LowEnergy, TimeToGoHome):
    pass
print("reached floor", level, "free slots:", backpack.free)
go("Farm")
```

## Fish the evening, ship the cheap ones
Short calls in a loop, so it stops in time; valuable fish are kept to sell at Willy's.
```python
go("Beach")
caught = 0
try:
    while day.time < 2130 and farmer.energy > 30 and not backpack.full:
        caught += fish(4)["caught"]
except (LowEnergy, TimeToGoHome):
    pass
fishes = backpack.fish
good = [f.name for f in fishes if f.price >= 150]
if good:
    print("valuable:", good)
for f in fishes:
    if f.price < 75:
        f.ship()             # walks to the farm's bin
print("caught", caught)
```

## A gift round on a birthday
The likes come from memory (looked up once with `web`). The birthday person first, then the rest who haven't been talked to today.
```python
likes = {"Haley": "Daffodil", "Emily": "Amethyst", "Lewis": "Hot Pepper", "Linus": "Wild Horseradish"}
have = [i.name for i in backpack.items]
order = [n for n in likes if n == day.birthday] + [n for n in likes if n != day.birthday]
for name in order:
    person = people[name]
    if person.talked_today:
        continue
    gift = likes[name] if likes[name] in have else None
    try:
        r = person.talk(gift)
        print(name, "now", r["hearts"], "hearts", "(gift)" if gift else "")
        if gift:
            have.remove(gift)
    except (Closed, Unreachable):
        print(name, "couldn't be reached today")
```

## An evening of foraging and shipping
A forage round through the forest, town and mountain, then everything new is shipped.
```python
before = {}
for i in backpack:
    before[i.name] = before.get(i.name, 0) + i.count
found = 0
for place in ["Forest", "Town", "Mountain"]:
    if day.time >= 2000:
        break
    try:
        go(place)
    except Unreachable:
        print("couldn't get to", place)
        continue
    r = gather(["forage", "dig_spots"])          # dig spots need the hoe
    found += sum(r["got"].values())
    if backpack.full:
        print("backpack full")
        break
go("Farm")
for i in backpack:
    extra = i.count - before.get(i.name, 0)
    if extra > 0 and i.price > 0:
        i.ship(extra)
print("foraged", found)
```

## Clear and plant a new plot
Clears debris, tills, and plants as many seeds as you have, if the crop can finish before the season ends.
```python
plot = Area(58, 20, 67, 24)
days_left = 28 - day.day
crop = catalog["Cauliflower Seeds"].crop or {}
seeds = [i for i in backpack if i.name == "Cauliflower Seeds"]
if seeds and crop.get("days", 99) <= days_left:
    go("Farm")
    print(gather(["weeds", "twigs", "stone", "grass"], plot))
    till(plot)
    r = plant("Cauliflower Seeds", plot)
    print("planted", r["placed"], "of", seeds[0].count)
    water(plot)
else:
    print("no seeds, or not enough days left:", days_left, crop)
```

## Store the haul in the farm chest
Everything but tools goes into the chest by the house; then the seeds come back out, to plant tomorrow.
```python
go("Farm")
chests = here.chests
if chests:
    chest = chests[0]
    print(chest.put("all"))
    try:
        chest.take("Parsnip Seeds")
    except ActionFailed:
        print("no parsnip seeds in the chest")
else:
    print("no chest on the farm")
```
