# Shipping
item.ship() puts items into the farm's shipping bin, from anywhere, to be paid overnight.

## Item.ship
`item.ship(count=None)`, like `backpack["Parsnip"].ship(10)`

Walks to the farm's shipping bin (from wherever you are: it travels to the Farm first), opens it, puts in the whole stack or `count` of it, and closes it. The money comes overnight, at the item's sell price (`.price`).

Returns `{"shipped": "Parsnip x10", "worth_about": 350}` (`worth_about` is `.price` times the count).

Raises:
- `ActionFailed` for things that can't be shipped: `Hoe can't be shipped`.
- `NotEnough` when you don't have it, or fewer went in than asked (`only 3 of 10 Parsnip were shipped`).
- `ActionFailed` if there's no shipping bin or it didn't open.

```python
for f in backpack.fish:
    if f.price < 75:
        f.ship()
```

## Gotchas
- Each `.ship()` walks to the bin, so from town or the beach the first one costs a long walk; ship everything in one go when you're home.
- Shipping is the only way to sell after the shops close, and its prices are the same as selling.
- What was earned shows in the night's notes (`shipping earned 820g`) and in the morning report.
