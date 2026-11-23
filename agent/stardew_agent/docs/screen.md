# The screen
screen is the escape hatch for anything the other actions can't do: look at, click, type in and close menus, interact with tiles, and send raw input.

## screen
`screen(action="look", target=None, at=None, text=None, tiles=None, item=None, ops=None, right=False)`

`action` is one of `"look"`, `"click"`, `"type"`, `"close"`, `"interact"`, `"raw"` (anything else is a `TypeError`). Most return a description of the open menu afterwards (see `docs("screen#look")`), or `"no menu is open"`.

## look
`screen("look")` describes the open menu and how to work it from a script:
- a dialogue: `{"type": "DialogueBox", "speaker", "text", "choices": ["0: Yes", "1: No"], "how": "answer(N)"}`;
- a letter: `{"type": "LetterViewerMenu", "text", "how": "screen('close') also takes attached items"}`;
- a shop: its money and every item for sale with its number, price and stock;
- a chest: what's in it, numbered by slot;
- anything else: its `text`, `tabs`, what the cursor holds, and `components`, numbered: `"12: OK"`, `"3: Floor 40"`.

## click
`screen("click", target=...)` clicks a component of the open menu, by its number from `look`, or by its name, label or field (no case), or a tab by name. `right=True` right-clicks. Raises `ActionFailed` for `nothing called 'X' here; use screen('look')`.

`screen("click", at=[x, y])` clicks at a pixel of your last screenshot, which must be uncropped (`that needs an uncropped screenshot`).

Returns the menu's description, or `"clicked; the menu closed"`.

```python
screen("click", target="OK")
```

## type
`screen("type", text="Mochi")` types into the menu's text box (replacing what's there), like a name for an animal. Returns the menu's description.

## close
`screen("close")` closes the open menu: plays dialogue through, pages through letters (taking attached items and accepting quests), stows what the cursor holds. Raises `ActionFailed` when closing would destroy items left in it (take them first), or when the menu can't be closed now.

## interact
`screen("interact", tiles=[[x, y], ...], item=None)` walks beside each tile and presses the action button on it, holding `item` if given (else empty hands): opening things, loading machines, reading signs, shaking bushes, using the mailbox. It stops at the first menu that needs you. Returns `{"interacted": n}`, plus `"menu"` with the open menu's description. Raises `TypeError` without tiles, and an error if a single tile can't be reached.

```python
r = screen("interact", tiles=[[30, 12]], item="Copper Ore")   # load a furnace
print(r)
```

## raw
`screen("raw", ops=[...])` sends 1 to 20 low-level instructions, each a dict with `"op"`: `"input"` (keys or buttons held, a cursor position or `"tile"`), `"step"` (`{"op": "step", "n": 30}` advances 30 game ticks, at most 1,200), or `"read"`. Every key is released afterwards. Use it only when nothing else works; anything else is refused (`raw only allows input, step, and read`).

## Reading mail
`state()["mail"]` says how many letters are waiting and the mailbox's tile on the Farm.

```python
go("Farm")
box = find("interactables", "mail")
if box:
    r = screen("interact", tiles=[[box[0]["x"], box[0]["y"]]])
    print(r.get("menu"))          # the letter's text
    screen("close")               # takes any attached item, accepts a quest
```

Interact again for each further letter.

## Elevators, the bus, and other menus
Menus that `go` can't walk through are worked with `interact` then `click`: the mine elevator (a component per floor, like `"40"`), the bus ticket machine, a minecart. Look first, then click:

```python
lift = find("interactables", "MineElevator")     # in the Mine entrance
r = screen("interact", tiles=[[lift[0]["x"], lift[0]["y"]]])
print(r.get("menu"))                              # then e.g. screen("click", target="40")
```

Menus left open by an earlier step (a chest, a shop, the pause menu) are closed by the next action automatically; others raise `Question` until handled (`docs("exceptions#Questions and cutscenes")`).
