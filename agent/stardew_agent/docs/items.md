# Items
The backpack and its item fields, naming items, and an item's eat, use, hold, trash and give, and crafting; shops, shipping and chests have their own pages.

## backpack
`backpack` is read live, each time it's used:
- `for item in backpack`, `len(backpack)`: every item you carry, in slot order.
- `backpack["Parsnip"]`: the stack with that exact name (an Item); `NotEnough` if you have none.
- `backpack.items`: the same items, as a list.
- `backpack.food`: the items with `.energy` above 0.
- `backpack.fish`: the fish.
- `backpack.free`: the number of free slots.
- `backpack.full`: `True` when no slot is free. A full backpack stops `gather`, which says so in `why_left` (`docs("gather")`).

```python
items = backpack.items
print([(i.name, i.count) for i in items], "free:", backpack.free)
```

## Item fields
Items are read-only records, a snapshot from when they were read, with:
| field | what |
|---|---|
| `.name` | the display name, like `"Parsnip"` |
| `.count` | how many in the stack |
| `.price` | what one sells for at a shop (0 if it can't be sold); for items from `shop.stock()`, the shop's price |
| `.quality` | 0 normal, 1 silver, 2 gold, 4 iridium |
| `.energy`, `.healing` | what eating one restores (0 if it isn't food; energy can be negative for things that hurt) |
| `.category` | the game's category, like `"Fish"` (or its number as text, like `"-74"` for seeds) |
| `.water`, `.water_max` | the watering can's water (None for everything else) |
| `.upgrade` | a tool's level: 0 basic, 1 copper, 2 steel, 3 gold, 4 iridium (None for non-tools) |

Quality stacks are separate items, named like `"Parsnip (silver)"`.

## Naming items
Wherever an SDK call takes an `item`, give a name (`"Parsnip Seeds"`) or an Item (from `backpack`, a chest's `.items` or a shop's stock; it stands for its name). Names are matched exactly, without case: `"Seeds"` matches nothing, and `"Parsnip"` isn't `"Parsnip (silver)"`. With no match it raises `NotEnough` (`you don't have 'Melon'`), or `ActionFailed` with a "did you mean" list.

## Item.eat
`item.eat()`, like `backpack["Salad"].eat()`

Eats one of the item, answering the game's "Eat it?" question. If something next to you (a chest, a bin) catches the click, it closes it and steps to open ground to try again.

Returns `{"ate": "Salad"}`. Raises `ActionFailed` when it isn't edible (`Copper Ore isn't edible`) or eating didn't start, and `NotEnough` when you don't have it. Eating is always allowed by the safety, even at low health or energy (`docs("exceptions#Safety")`).

```python
if backpack.food:
    pick("the cheapest food", backpack.food).eat()
```

## Item.use
`item.use()`

Holds the item and uses it on your own tile: totems (they warp you), a staircase in the mines, and other things used on yourself.

Returns `{"used": "Warp Totem: Farm", "now_at": "Farm"}`, or `{"used": ..., "note": "it asks a question; answer it with answer()"}`.

## Item.hold
`item.hold()`

Selects the item in the toolbar, as if pressing its key. Returns `{"holding": "Copper Ore x12"}`. Most actions pick their own tool, so you rarely need this; it's for `screen` work, where what you hold matters (`docs("screen#Screen.interact")`).

## Item.trash
`item.trash()`

Destroys the whole stack, through the inventory menu (or the open menu if it has a trash can). Returns `{"trashed": "Joja Cola x3"}`. Raises `ActionFailed` for things that can't be trashed (tools, quest items). It's permanent: prefer `.ship()` or `shop.sell` for anything worth money.

## Item.ship
`item.ship(count=None)`: into the shipping bin, from anywhere. See `docs("items/shipping#Item.ship")`.

## Item.give
`item.give(who)`

Gives the item to someone, as `talk(who, gift=item)` does: `backpack["Daffodil"].give(people["Haley"])`. Returns what `talk` returns (`docs("people#talk")`).

## Recipe.craft
`recipe.craft(count=1)`, like `recipes["Chest"].craft(2)`

Crafts a known recipe `count` times (1 to 999) through the crafting menu, stopping early if the ingredients run out. `recipes["X"]` raises `ActionFailed` for a recipe you don't know; iterate `recipes` to see what you know and `.can_make` (`docs("facts#recipes")`).

Returns `{"crafted": "Chest x2"}`: check the count, it can be fewer than asked.

Raises `NotEnough` for missing ingredients (`missing ingredients for Chest: needs ...`) or no free slot (`crafting needs a free inventory slot`).

```python
recipes["Chest"].craft()
place("Chest", (64, 14))
```

## Gifts
Gifts are given by talking while holding the item: `talk(people["Haley"], gift="Daffodil")` (`docs("people#talk")`), or `item.give(who)`. Each person takes up to 2 gifts a week and one a day, and a gift on their birthday counts much more. Liked gifts are personal: ask `web("what does Haley like as gifts")` once and remember it. See `docs("people#Friendship")`.

## More
- Buying and selling, shop stock and hours, the bigger backpack: `docs("items/shops")`.
- Shipping for money overnight: `docs("items/shipping")`.
- Chests, `put` and `take`: `docs("items/chests")`.
