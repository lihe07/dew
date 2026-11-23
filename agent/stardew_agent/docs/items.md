# Items
The backpack and its item fields, naming items, and eat, use, craft, trash and hold; shops, shipping and chests have their own pages.

## backpack
`backpack` is read live; each attribute read is one SDK call (`docs("scripts#Budgets")`):
- `backpack.items`: every item you carry, in slot order (a list of items).
- `backpack.food`: the items with `.energy` above 0.
- `backpack.fish`: the fish.
- `backpack.free`: the number of free slots.
- `backpack.full`: `True` when no slot is free.

```python
items = backpack.items
print([(i.name, i.count) for i in items], "free:", backpack.free)
```

## Item fields
Items are read-only, with:
| field | what |
|---|---|
| `.name` | the display name, like `"Parsnip"` |
| `.count` | how many in the stack |
| `.price` | what one sells for at a shop (0 if it can't be sold); for items from `shop_stock()`, the shop's price |
| `.energy`, `.healing` | what eating one restores (0 if it isn't food; energy can be negative for things that hurt) |
| `.category` | the game's category number as text, like `"-4"` for fish, `"-74"` for seeds, `"-75"` for vegetables |
| `.water`, `.water_max` | the watering can's water (None for everything else) |
| `.upgrade` | a tool's level: 0 basic, 1 copper, 2 steel, 3 gold, 4 iridium (None for non-tools) |

Quality (silver, gold, iridium) isn't a field; quality stacks are separate items with the same name.

## Naming items
Wherever an SDK call takes an `item`, give a name (`"Parsnip Seeds"`) or an item from `backpack` (it stands for its name). Names are matched without case: first an exact name, then the first item whose name *contains* the text, so `"Seeds"` means whichever seed stack comes first. A backpack slot number as text (`"3"`) also works. With no match it raises `NotEnough`: `you don't have 'Melon'`.

## eat
`eat(item)`

Eats one of the item, answering the game's "Eat it?" question. If something next to you (a chest, a bin) catches the click, it closes it and steps to open ground to try again.

Returns `{"ate": "Salad"}`. Raises `ActionFailed` when it isn't edible (`Copper Ore isn't edible`) or eating didn't start, and `NotEnough` when you don't have it. Eating is always allowed by the safety, even at low health or energy (`docs("exceptions#Safety")`).

```python
if backpack.food:
    eat(pick("the cheapest food", backpack.food))
```

## use
`use(item)`

Holds the item and uses it on your own tile: totems (they warp you), a staircase in the mines, and other things used on yourself.

Returns `{"used": "Warp Totem: Farm", "now_at": "Farm"}`, or `{"used": ..., "note": "it asks a question; answer it with answer()"}`.

## craft
`craft(recipe, count=1)`

Crafts a known recipe `count` times (1 to 999) through the crafting menu, stopping early if the ingredients run out.

Returns `{"crafted": "Chest x2"}`: check the count, it can be fewer than asked.

Raises: `ActionFailed` for a recipe you don't know (`you don't know a recipe called 'X'`), `NotEnough` for missing ingredients (`missing ingredients for Chest: needs ...`) or no free slot (`crafting needs a free inventory slot`). `recipes()` lists what you know and what you can make now (`docs("facts#recipes")`).

```python
craft("Chest")
place("Chest", [[64, 14]])
```

## trash
`trash(item)`

Destroys the whole stack, through the inventory menu (or the open menu if it has a trash can). Returns `{"trashed": "Joja Cola x3"}`. Raises `ActionFailed` for things that can't be trashed (tools, quest items). It's permanent: prefer `ship` or `sell` for anything worth money.

## hold
`hold(item)`

Selects the item in the toolbar, as if pressing its key. Returns `{"holding": "Copper Ore x12"}`. Most actions pick their own tool, so you rarely need this; it's for `screen` work, where what you hold matters (`docs("screen#interact")`).

## Gifts
Gifts are given by talking while holding the item: `talk("Haley", gift="Daffodil")` (`docs("people#talk")`). Each person takes up to 2 gifts a week and one a day, and a gift on their birthday counts much more. Liked gifts are personal: ask `web("what does Haley like as gifts")` once and remember it. See `docs("people#Friendship")`.

## More
- Buying and selling, shop stock and hours, the bigger backpack: `docs("items/shops")`.
- Shipping for money overnight: `docs("items/shipping")`.
- Chests, `put` and `take`: `docs("items/chests")`.
