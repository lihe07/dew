# People
Talking and giving gifts with talk, answering questions, cutscenes, finding people, birthdays, and how friendship grows.

## talk
`talk(who, gift=None)`

Finds the person wherever they are today, walks to them (following as they move), and talks to them; with `gift` (a name or an Item from `backpack`), holds that item first so the talk gives it. The dialogue is played through and recorded in the wake-up's journal. `who` is a Person (`people["Haley"]`, one of `here.people`) or their exact name.

Returns `{"person": "Haley", "hearts": 2, "points": "500 -> 520", "note": None}`:
- `hearts` and `points` are after the talk (250 points a heart); `None` for people who aren't on the friendship list (Marlon, Gunther, ...).
- `note` is `"had nothing to say"` when no dialogue opened (already talked today, for instance).
- `menu` is added when the talk left a menu open, like a question: answer it with `answer(...)`.

Several people at once: `talk(["Haley", "Alex", "Emily"])` (a list or a set) visits them all, nearest first. Where everyone is is looked up again after each talk, since people walk about. `gift` is one item for everyone, or `{"Haley": "Daffodil", "Alex": "Salad"}` per person. Returns a list with one result per person. Someone who can't be reached gets `{"person": "Alex", "failed": "..."}` in the list; it doesn't raise, and the others are still visited.

Raises:
- `ActionFailed` for an unknown name, with a "did you mean" list.
- `Closed` when they aren't out today: `Haley isn't around today`.
- `Unreachable` when they couldn't be reached: `couldn't get to Haley: ...`.
- `NotEnough` when you don't have the gift.

```python
r = talk(people["Haley"], gift="Daffodil")
print(r["person"], r["hearts"], r["points"])
```

## Person.talk
`person.talk(gift=None)`: the same as `talk(person, gift)`, like `people["Haley"].talk(gift="Daffodil")`. Giving from the item's side: `backpack["Daffodil"].give(people["Haley"])` (`docs("items#Item.give")`).

## answer
`answer(choice)`

Answers the open question: by number (`answer(0)` is the first choice), or by a description, which the judge matches to one of the choices (`answer("yes")`, `answer("the one about the mines")`). It may raise `Unsure` for a description (`docs("judge")`). If the answer leads into a cutscene, it plays it. After a level-up overnight, `answer(0)` or `answer(1)` chooses the profession offered.

Returns `None`, or `{"menu": {...}}` describing what's open next. Raises `TypeError` for anything but a number or text, and `ActionFailed` for a number out of range (`choice must be 0 to 1`).

```python
r = talk(people["Lewis"])
if r.get("menu"):            # he asked something
    print(r["menu"])
    answer(0)
```
An action that runs into a question it can't settle raises `Question` instead, with `.choices` (`docs("exceptions#Questions and cutscenes")`).

## Cutscenes
Cutscenes start on their own (entering a place, a heart event). Actions handle them: a cutscene that can be skipped and asks no question is skipped, and what it would have shown (who said what, items and recipes given) goes into the journal as `event: ...` lines, so you still learn it; any other cutscene is played through, and a question in it is answered with `answer`.

## people list
`people` is everyone on the friendship list: iterate it, or take one by exact name, `people["Haley"]` (`ActionFailed` with a "did you mean" list for an unknown name). Each is a Person:
| field | what |
|---|---|
| `.name` | `"Haley"` |
| `.hearts` | hearts now, None if not met |
| `.met` | whether you've met |
| `.talked_today` | whether you talked today |
| `.gifts_this_week` | 0 to 2 |
| `.birthday` | like `"spring 14"` |
| `.place` | the location's game name where they were when read (None: away today) |
| `.tile` | where they are now, read live, as a Tile (None when away today) |
| `.home` | their house, a Place: `go(people["Haley"].home)` |

`go(person)` follows them; `go(person.tile)` goes to where they were, without following.

```python
for p in people:
    if p.met and not p.talked_today and p.place == here.name:
        talk(p)
```

## People here
`here.people`: everyone in the current location now, as Persons, people off the friendship list too (with `.met` False): `[p.name for p in here.people]`. Each has a live `.tile`.

## Birthdays
`day.birthday` names whose birthday it is today (None if nobody's). Each Person's `.birthday` is as the game gives it (like `spring 14`): compare without case. A liked gift on a birthday is worth eight times as much as on another day; a disliked one hurts as much.

```python
name = day.birthday
if name:
    p = people[name]
    print(name, "has a birthday; hearts", p.hearts, "gifts this week", p.gifts_this_week)
```

## Friendship
These are the game's rules:
- Talking to someone once a day gives a little friendship; it slowly falls on days you don't talk.
- Gifts: up to 2 a week per person, and one a day. Loved and liked gifts give a lot, neutral little, disliked and hated gifts take away. Tastes are personal: ask `web(...)`, and `remember` what you learn (like `people.haley.likes`).
- Hearts unlock cutscenes (when you enter a place) and recipes in the mail.
- Most cooked dishes and artisan goods are liked by nearly everyone; trash is hated by all.

Item-side details of gifts: `docs("items#Gifts")`. Planning a day around people: `docs("strategy#Friendship")`.
