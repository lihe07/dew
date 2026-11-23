# Examples
Complete scripts for common stretches of a day, to copy and adapt: chores and shopping, looking around, the mines, fishing, gifts, foraging, a new plot.

## Morning chores, then seeds
Chores first, then Pierre's when it opens, then plant by the house. Tiles are examples: use your own plot's.
```python
harvest()
water()                      # returns at once on rainy days
pet()
machines()
try:
    go("Pierre's")
except Closed:               # before 9am the door is locked
    wait_until("9:00 am")
    go("Pierre's")
price = 20
for i in shop_stock():
    if i.name == "Parsnip Seeds":
        price = i.price
count = min(30, (money() - 100) // price)
if count > 0:
    print(buy("Parsnip Seeds", count))
    plot = [60, 15, 65, 19]
    go("Farm")
    till(plot)
    r = plant("Parsnip Seeds", plot)
    water(plot)
    print("planted", r["placed"], "seeds")
```

## Find things out before deciding
Facts only: nothing moves, and everything printed comes back when the script ends.
```python
print(state())
print("people:", people())
print("ready here:", len(find("crops_ready")), "thirsty:", len(find("crops_thirsty")))
print("forage here:", find("forage")[:5])
print("parsnip:", item_info("Parsnip Seeds").get("crop"))
print("cauliflower:", item_info("Cauliflower Seeds").get("crop"))
items = backpack.items
print("food:", [(i.name, i.count, i.energy, i.healing) for i in items if i.energy > 0])
print("can:", [(i.water, i.water_max) for i in items if i.water is not None], "free slots:", backpack.free)
```

## A rainy day in the mines, home in time
Rain waters the crops, so the day is free for the mines. Eats when hurt, stops at its limits, and leaves in time to be home by midnight.
```python
if "Rain" not in state()["weather"].split(",")[0]:
    water()
harvest()
go("Mine")
level = 0
while minutes_until_2am() > 330 and not backpack.full:
    try:
        r = mine(3)
        level = r["level"]
        if "note" in r:          # stepped on a shaft: jump down
            answer(0)
    except LowHealth:
        food = backpack.food
        if not food:
            break
        best = food[0]
        for f in food:
            if f.healing > 0 and (best.healing <= 0 or f.price * best.healing < best.price * f.healing):
                best = f
        eat(best)
    except (LowEnergy, TimeToGoHome, Question):
        break                    # Question: mine's own energy or health floor
print("reached floor", level, "free slots:", backpack.free)
go("Farm")
```

## Fish the evening, ship the cheap ones
Short calls in a loop, so it stops in time; valuable fish are kept to sell at Willy's.
```python
go("the beach")
caught = 0
try:
    while time() < 2130 and energy() > 30 and not backpack.full:
        caught += fish(4)["caught"]
except (LowEnergy, TimeToGoHome):
    pass
fishes = backpack.fish
good = [f.name for f in fishes if f.price >= 150]
if good:
    print("valuable:", good)
for f in fishes:
    if f.price < 75:
        ship(f)              # walks to the farm's bin
print("caught", caught)
```

## A gift round on a birthday
The likes come from memory (looked up once with `web`). The birthday person first, then the rest who haven't been talked to today.
```python
likes = {"Haley": "Daffodil", "Emily": "Amethyst", "Lewis": "Hot Pepper", "Linus": "Wild Horseradish"}
have = [i.name for i in backpack.items]
who = state()["birthday_today"]
order = [n for n in likes if n == who] + [n for n in likes if n != who]
for name in order:
    row = people(name)
    if row and "not talked" not in row[0]:
        continue
    gift = likes[name] if likes[name] in have else None
    try:
        r = talk(name, gift)
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
for i in backpack.items:
    before[i.name] = before.get(i.name, 0) + i.count
found = 0
for place in ["Forest", "Town", "Mountain"]:
    if time() >= 2000:
        break
    try:
        go(place)
        found += forage()["collected"]
    except NotEnough:
        print("backpack full")
        break
    except Unreachable:
        print("couldn't get to", place)
go("Farm")
for i in backpack.items:
    extra = i.count - before.get(i.name, 0)
    if extra > 0 and i.price > 0:
        ship(i, extra)
print("foraged", found)
```

## Clear and plant a new plot
Clears debris, tills, and plants as many seeds as you have, if the crop can finish before the season ends.
```python
plot = [58, 20, 67, 24]
days_left = 28 - int(state()["date"].split(",")[0].split()[-1])
crop = item_info("Cauliflower Seeds").get("crop") or {}
seeds = [i for i in backpack.items if i.name == "Cauliflower Seeds"]
if seeds and crop.get("days", 99) <= days_left:
    go("Farm")
    print(clear(plot, ["weeds", "twigs", "stones", "grass"]))
    till(plot)
    r = plant("Cauliflower Seeds", plot)
    print("planted", r["placed"], "of", seeds[0].count)
    water(plot)
else:
    print("no seeds, or not enough days left:", days_left, crop)
```
