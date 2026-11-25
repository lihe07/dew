# Time
The clock and the day's shape: day, wait, soak, sleep, getting home by midnight, and passing out.

## day
`day` is read live, each time a field is read:
| field | what |
|---|---|
| `day.time` | the clock as a number, `hhmm` in 24 hours: `630` is 6:30am, `1430` is 2:30pm. After midnight it keeps counting: `2400` is midnight, `2530` is 1:30am. So `day.time < 2200` means "before 10pm" all day. |
| `day.minutes_left` | game minutes until you pass out at 2am: 1,200 at 6am, 120 at midnight. Handy for loops: `while day.minutes_left > 300:` stops at 9pm. |
| `day.date` | like `"Mon Spring 1, year 1"` |
| `day.weekday` | like `"Mon"` |
| `day.season`, `day.day`, `day.year` | the season; the day of the season, 1 to 28; the year. Days left in the season: `28 - day.day`. |
| `day.weather`, `day.tomorrow` | today's and tomorrow's weather, like `"Rain"` |
| `day.luck` | `"very good"`, `"good"`, `"neutral"`, `"bad"`, `"very bad"` |
| `day.festival` | whether there's a festival today (the shops close) |
| `day.birthday` | whose birthday it is, like `"Haley"`, or None |

```python
if "Rain" in day.weather:
    print("rain today: no watering")
```

## The shape of a day
- The day starts at 6:00am. The clock runs in 10-minute steps, about 7 real seconds each, and never pauses: your thinking costs game time.
- Most shops open around 9am and close around 5pm (`docs("items/shops#Opening hours")`).
- From 6pm the status line shows the time until you pass out, and the safety starts watching the walk home (`docs("exceptions#TimeToGoHome")`).
- Going to bed after midnight costs energy the next morning. At 2am you pass out wherever you are.
- A season has 28 days; there are four seasons a year.

## wait
`wait("9:00 am")` or `wait(minutes=30)`

Lets time pass where you are, until a clock time (text like `"9:00 am"`, `"9am"`, `"21:30"`, `"2130"`, or a number like `900`; times before 6am mean after midnight, so `"1am"` is 2500) or for some minutes (10 to 600; `minutes` must be given by keyword). It plays through dialogue that needs no decision on the way.

Returns `None`. Raises `TypeError` without a time or minutes (or with both), and `ActionFailed` for a time it can't read (`not a time: 'noon'`) or out of range. Waiting until a time already past returns at once.

```python
try:
    go("SeedShop")
except Closed:
    wait("9:00 am")
    go("SeedShop")
```

Waiting is idle time: prefer work nearby (forage, clear, fish) that ends around when you need it.

## soak
`soak(until=None)`

Walks to the spa's pool (the bathhouse by the mountain, reachable from Summer 3 when the railroad opens), gets into the water, and stays until energy is full, or until the clock reaches `until` (`"4:00 pm"` or `1600`). It's free, but takes game time: roughly an hour or more from low energy.

Returns `{"energy": "20 -> 270/270", "from": 1410, "to": 1600}`.

Raises `ActionFailed` if it couldn't get into the water or got out early, and an `Unreachable` or `Closed` when the spa can't be reached yet. `soak` is let through by the safety even when energy is low, since it cures it (`docs("exceptions#Safety")`).

## sleep
`sleep()`

Goes home to bed: the day ends there, at any hour (an early night isn't penalized; the game has no naps, so to pass an hour use `wait`). Nothing after it in the script runs. The night's screens (shipping totals, level-ups) open the next day: if a level-up offers a profession, the next day starts by asking you to choose (`answer(0)` or `answer(1)`, permanent), and its morning report says what the night brought.

Raises `ActionFailed` if the bed couldn't be reached. `sleep` is always allowed by the safety.

## Getting home
- From 6pm, the safety estimates the walk home and raises `TimeToGoHome` once when that walk plus 15 minutes no longer fits before midnight. It has `minutes_home` and `minutes_left`. Then `go("Farm")` (always allowed) and `sleep()`.
- Rough walks: Beach or Mountain to the farmhouse about 50 game minutes, the mines about 70 to 80 (`docs("moving#How far things are")`). `places["FarmHouse"].minutes` is the estimate from where you are.
- Plan evening trips with the walk in mind: fish at the beach until about 10:30pm at the latest, leave the mines by about 10:30pm.

```python
try:
    while day.time < 2300:
        fish(3)
except TimeToGoHome:
    pass
go("Farm")
sleep()
```

## Passing out
At 2am the farmer passes out wherever it is, and the day ends. The game then takes a fee (some of your money, up to 1,000g) unless you were at home, and you wake with less energy. Passing out from exhaustion (energy below 0) or from 0 health in the mines has its own costs: lost items in the mines. Avoid all of it: be home by midnight.
