# The screen
screen is the escape hatch for anything the other actions can't do: look at, click, type in and close menus, interact with tiles, send raw input, and take screenshots.

## screen
`screen` has `look()`, `click(target, right=False)`, `type(text)`, `close()`, `interact(where, item=None)`, `raw(ops)` and `shot(radius=None, grid=False)`. Most return a description of the open menu afterwards (see `docs("screen#Screen.look")`), or None when no menu is open.

## Screen.look
`screen.look()` describes the open menu and how to work it from a script:
- a dialogue: `{"type": "DialogueBox", "speaker", "text", "choices": ["0: Yes", "1: No"], "how": "answer(N)"}`;
- a letter: `{"type": "LetterViewerMenu", "text", "how": "screen.close() also takes attached items"}`;
- a shop: its money and every item for sale with its number, price and stock;
- a chest: what's in it, numbered by slot;
- anything else: its `text`, `tabs`, what the cursor holds, and `components`, numbered: `"12: OK"`, `"3: Floor 40"`.

## Screen.click
`screen.click(target)` clicks a component of the open menu, by its number from `look`, or by its name, label or field (no case), or a tab by name. `right=True` right-clicks. Raises `ActionFailed` for `nothing called 'X' here; use screen.look()`.

`screen.click((x, y))` clicks at a pixel of the script's last `screen.shot()`, which must be uncropped (`that needs an uncropped screenshot`).

Returns the menu's description, or `"clicked; the menu closed"`.

```python
screen.click("OK")
```

## Screen.type
`screen.type("Mochi")` types into the menu's text box (replacing what's there), like a name for an animal. Returns the menu's description.

## Screen.close
`screen.close()` closes the open menu: plays dialogue through, pages through letters (taking attached items and accepting quests), stows what the cursor holds. Raises `ActionFailed` when closing would destroy items left in it (take them first), or when the menu can't be closed now.

## Screen.interact
`screen.interact(where, item=None)` walks beside each tile of `where` (a tile, an Area, or a list of tiles or things, `docs("moving#Tiles and areas")`) and presses the action button on it, holding `item` if given (else empty hands): opening things, loading machines, reading signs, shaking bushes, using the mailbox. It stops at the first menu that needs you. Returns `{"interacted": n}`, plus `"menu"` with the open menu's description. Raises `TypeError` without tiles, and an error if a single tile can't be reached.

```python
r = screen.interact((30, 12), item="Copper Ore")   # load a furnace
print(r)
```

## Screen.raw
`screen.raw([...])` sends 1 to 20 low-level instructions, each a dict with `"op"`: `"input"` (keys or buttons held, a cursor position or `"tile"`), `"step"` (`{"op": "step", "n": 30}` advances 30 game ticks, at most 1,200), or `"read"`. Every key is released afterwards. Use it only when nothing else works; anything else is refused (`raw only allows input, step, and read`).

## Screen.shot
`screen.shot(radius=None, grid=False)`

Takes a screenshot that you'll see at your next wake-up (the script gets a note, not the picture); people and things are labelled `name (x,y)`. `radius` (2 to 20) crops it around the farmer; `grid=True` overlays tile coordinates. For clicking at pixels with `screen.click((x, y))`, take it uncropped. You can also take one yourself with the `screenshot` tool without disturbing the script.

## Reading mail
The mailbox is on the Farm, by the house: `here.find("interactables", "mail")` finds it there.

```python
go("Farm")
box = here.find("interactables", "mail")
if box:
    r = screen.interact(box[0])
    print(r.get("menu"))          # the letter's text
    screen.close()                # takes any attached item, accepts a quest
```

Interact again for each further letter.

## Elevators, the bus, and other menus
Menus that `go` can't walk through are worked with `interact` then `click`: the mine elevator (a component per floor, like `"40"`), the bus ticket machine, a minecart. Look first, then click:

```python
lift = here.find("interactables", "MineElevator")     # in the Mine entrance
r = screen.interact(lift[0])
print(r.get("menu"))                                   # then e.g. screen.click("40")
```

Menus left open by an earlier step (a chest, a shop, the pause menu) are closed by the next action automatically; others raise `Question` until handled (`docs("exceptions#Questions and cutscenes")`).
