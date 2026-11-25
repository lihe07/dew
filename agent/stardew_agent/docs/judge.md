# The judge
check and pick ask a fast model about the situation right now: what it sees, what it's good and bad at, and Unsure.

## check
`check(situation)`

Asks whether a described situation is the case now. Returns `True` or `False`; raises `Unsure` when the judge isn't confident (under 0.6). It's one SDK call and takes a moment.

```python
if check("the shopkeeper said the shop is closed today"):
    go("Farm")
```

## pick
`pick(description, options)`

Asks the judge to choose the option that fits the description, and returns that option itself (not its index). Options can be strings, items (`backpack.food`), numbers or dicts. Raises `Unsure` when it's not confident, when no option fits, or when `options` is empty.

```python
if backpack.food:
    pick("the cheapest food", backpack.food).eat()
```

## What the judge sees
Only this, at the moment of the call:
- the time, minutes until 2am, weekday, season and day, the weather, whether it's raining;
- the location, health and energy (also as percentages), money, free slots, whether the backpack is full, what's held;
- the script's goal, the last action and the first 600 characters of its result;
- the last few things heard: dialogue, questions, letters, messages, speech bubbles, cutscene lines, level-ups;
- for `pick`, the options as text (an item as `Salad: x3, 110g, +113 energy`) and the description.

It doesn't see the map, the crops, the backpack (except what you pass as options), your memory, or anything earlier.

## Good at, bad at
Good at:
- reading what was just said or shown: "the shop is closed", "Lewis asked for a favour", "the last action found nothing to do";
- comparing given options: the cheapest, the most filling, the one that sounds like a gift Haley would like;
- coarse facts it sees: "it's raining", "it's evening", "health is low".

Bad at:
- sums and counting: do arithmetic in Python (`farmer.money // price`, `sum(...)`), and compare numbers yourself (`farmer.energy < 50` is surer and cheaper than `check("energy is low")`);
- things it doesn't see (above): "the crops need water" (use `here.find("crops_thirsty")`), "the backpack has seeds" (look at `backpack.items`);
- borderline cases: near a threshold ("health is below half" at 52%) it may raise `Unsure`.

Prefer plain Python whenever a fact is available; use the judge for words.

## Unsure
`Unsure` has `.situation` (what was asked) and `.confidence` (0 to 1). Without a judge (none configured) every `check` and `pick` raises it. Catch it where a default is fine:

```python
try:
    food = pick("the cheapest food", backpack.food)
except Unsure:
    food = backpack.food[0]
food.eat()
```

`answer("a description")` uses the judge too and can raise `Unsure` (`docs("people#answer")`). All the exception classes: `docs("exceptions")`.
