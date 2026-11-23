# Time
The clock and the day's shape: time, wait_until, soak, sleep, minutes_until_2am, getting home by midnight, and passing out.

## The day's shape
- The day starts at 6:00am. The clock runs in 10-minute steps, about 7 real seconds each, and never pauses: your thinking costs game time.
- Most shops open around 9am and close around 5pm (`docs("items/shops#Opening hours")`).
- From 6pm the status line shows the time until you pass out, and the safety starts watching the walk home (`docs("exceptions#TimeToGoHome")`).
- Going to bed after midnight costs energy the next morning. At 2am you pass out wherever you are.
- A season has 28 days; there are four seasons a year.

## time
`time()`

The clock as a number, `hhmm` in 24 hours: `630` is 6:30am, `1430` is 2:30pm. After midnight it keeps counting: `2400` is midnight, `2530` is 1:30am. So `time() < 2200` means "before 10pm" all day.

## minutes_until_2am
`minutes_until_2am()`

Game minutes left until you pass out: 1,200 at 6am, 120 at midnight. Handy for loops: `while minutes_until_2am() > 300:` stops at 9pm.

## wait_until
`wait_until("9:00 am")`, `wait_until(minutes=30)`, or `wait_until(30)`

Lets time pass where you are, until a clock time (text like `"9:00 am"`, `"9am"`, `"21:30"`, `"2130"`; times before 6am mean after midnight, so `"1am"` is 2500) or for some minutes (10 to 600). It plays through dialogue that needs no decision on the way.

Returns `None`. Raises `TypeError` without a time or minutes, and `ActionFailed` for a time it can't read (`not a time: 'noon'`) or out of range. Waiting until a time already past returns at once.

```python
try:
    go("Pierre's")
except Closed:
    wait_until("9:00 am")
    go("Pierre's")
```

Waiting is idle time: prefer work nearby (forage, clear, fish) that ends around when you need it.

## soak
`soak(until=None)`

Walks to the spa's pool (the bathhouse by the mountain, reachable from Summer 3 when the railroad opens), gets into the water, and stays until energy is full, or until the clock reaches `until` (`"4:00 pm"` or `1600`). It's free, but takes game time: roughly an hour or more from low energy.

Returns `{"energy": "20 -> 270/270", "from": 1410, "to": 1600}`.

Raises `ActionFailed` if it couldn't get into the water or got out early, and an `Unreachable` or `Closed` when the spa can't be reached yet. `soak` is let through by the safety even when energy is low, since it cures it (`docs("exceptions#Safety")`).

## sleep
`sleep(early=False)`

Goes home to bed and sleeps, ending the day. The night's screens (shipping totals, level-ups) are played through; the script stops there, and your next turn is the morning report.

Before 6pm it refuses unless `early=True`: `it's only 2:30 pm: there's most of a day left. Plan more, or sleep with early=True if nothing useful is left` (ActionFailed). Sleeping early is right when energy is gone and nothing free is left to do: an early night isn't penalized.

Returns `{"woke_up": "Tue Spring 2, year 1", "notes": [...]}`, though nothing after it in the script runs. If a level-up offers a profession, it raises `Question` with the two professions as choices: answer with `answer(0)` or `answer(1)`.

Raises `ActionFailed` if the bed couldn't be reached. `sleep` is always allowed by the safety.

## Getting home
- From 6pm, the safety estimates the walk home and raises `TimeToGoHome` once when that walk plus 15 minutes no longer fits before midnight. It has `minutes_home` and `minutes_left`. Then `go("Farm")` (always allowed) and `sleep()`.
- Rough walks: Beach or Mountain to the farmhouse about 50 game minutes, the mines about 70 to 80 (`docs("moving#How far things are")`).
- Plan evening trips with the walk in mind: fish at the beach until about 10:30pm at the latest, leave the mines by about 10:30pm.

```python
try:
    while time() < 2300:
        fish(3)
except TimeToGoHome:
    pass
go("Farm")
sleep()
```

## Passing out
At 2am the farmer passes out wherever it is, and the day ends. The game then takes a fee (some of your money, up to 1,000g) unless you were at home, and you wake with less energy. Passing out from exhaustion (energy below 0) or from 0 health in the mines has its own costs: lost items in the mines. Avoid all of it: be home by midnight.
