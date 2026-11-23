# People
Talking and giving gifts with talk, answering questions, skipping cutscenes, finding people, birthdays, and how friendship grows.

## talk
`talk(person, gift=None)`

Finds the person wherever they are today, walks to them (following as they move), and talks to them; with `gift` (a name or an item from `backpack`), holds that item first so the talk gives it. The dialogue is played through and recorded in the wake-up's journal.

Returns `{"person": "Haley", "hearts": 2, "points": "500 -> 520", "note": None}`:
- `hearts` and `points` are after the talk (250 points a heart); `None` for people who aren't on the friendship list (Marlon, Gunther, ...).
- `note` is `"had nothing to say"` when no dialogue opened (already talked today, for instance).
- `menu` is added when the talk left a menu open, like a question: answer it with `answer(...)`.

Raises:
- `ActionFailed` for an unknown name: `no one called 'Hailey' was found (people() lists the villagers)`.
- `Closed` when they aren't out today: `Haley isn't around today`.
- `Unreachable` when they couldn't be reached: `couldn't get to Haley: ...`.
- `NotEnough` when you don't have the gift.

```python
r = talk("Haley", gift="Daffodil")
print(r["person"], r["hearts"], r["points"])
```

## answer
`answer(choice)`

Answers the open question: by number (`answer(0)` is the first choice), or by a description, which the judge matches to one of the choices (`answer("yes")`, `answer("the one about the mines")`). It may raise `Unsure` for a description (`docs("judge")`). If the answer leads into a cutscene, it plays it. After a level-up overnight, `answer(0)` or `answer(1)` chooses the profession offered.

Returns `None`, or `{"menu": {...}}` describing what's open next. Raises `TypeError` for anything but a number or text, and `ActionFailed` for a number out of range (`choice must be 0 to 1`).

```python
r = talk("Lewis")
if r.get("menu"):            # he asked something
    print(r["menu"])
    answer(0)
```
An action that runs into a question it can't settle raises `Question` instead, with `.choices` (`docs("exceptions#Questions and cutscenes")`).

## skip_cutscene
`skip_cutscene()`

Skips the playing cutscene. Returns `{"skipped_script": [lines]}`: what it would have shown (who said what, items and recipes given), since none of it was seen.

Raises `ActionFailed` when no cutscene is playing, when it can't be skipped, or when it asks you a question later on (`this cutscene asks you a question, so it shouldn't be skipped`): then just let actions play it, answering with `answer`.

## people
`people(name=None)`

A list of strings, one per villager on the friendship list (or those whose name contains `name`):
`"Haley: 2 hearts, not talked today, 0/2 gifts this week | at Town (20,88) | birthday spring 14"`, or `"Shane: not met | at JojaMart (12,5) | birthday spring 20"`.

The location is left out for people who aren't out right now.

## people_here
`people_here()`

The names of everyone in the current location: `["Pierre", "Abigail"]`. `find("people")` gives their tiles too (`docs("facts#find")`).

## Birthdays
`state()["birthday_today"]` names whose birthday it is today (None if nobody's). `people()` lists every birthday, as the game gives it (like `spring 14`): compare without case. A liked gift on a birthday is worth eight times as much as on another day; a disliked one hurts as much.

```python
who = state()["birthday_today"]
if who:
    print(who, "has a birthday:", people(who))
```

## Friendship
These are the game's rules:
- Talking to someone once a day gives a little friendship; it slowly falls on days you don't talk.
- Gifts: up to 2 a week per person, and one a day. Loved and liked gifts give a lot, neutral little, disliked and hated gifts take away. Tastes are personal: ask `web(...)`, and `remember` what you learn (like `people.haley.likes`).
- Hearts unlock cutscenes (when you enter a place) and recipes in the mail.
- Most cooked dishes and artisan goods are liked by nearly everyone; trash is hated by all.

Item-side details of gifts: `docs("items#Gifts")`. Planning a day around people: `docs("strategy#Friendship")`.
