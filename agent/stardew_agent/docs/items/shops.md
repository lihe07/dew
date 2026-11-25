# Shops
A shop's buy, sell and stock, Clint's geodes, the bigger backpack at Pierre's, and when shops are open.

## How shops work
A shop counter is a Shop: `here.shop` (None if there's no counter here), or a Place's `.shop`, like `places["SeedShop"].shop`. Its calls work only in its own place, so go there first: `go("SeedShop")`, then `here.shop.buy(...)`; elsewhere they raise `ActionFailed` (`that shop is in SeedShop, and you're in Farm: go there first`). They open the shop at the counter (the shopkeeper must be behind it). A counter that asks what you want (Clint asks shop / upgrade / geodes) is answered with the shopping option. The shop is left open until the next action, which closes it.

Errors when the shop won't open: `Closed` for `no shop is open, and there's no shop counter here` and `the shop didn't open ...` (the keeper may be away: `here.keeper.place` says where they were, `docs("people#people list")`).

## Shop.buy
`shop.buy(item, count=1)`

Buys `count` of an item the shop sells (matched by exact name, or by its number in the stock list). Buys in clicks of 25, 5 and 1. `count` is capped at the shop's stock.

Returns `{"bought": "Parsnip Seeds x15", "money": 485}`. If the purchases stopped partway (no room in the backpack), the count says how many were really bought.

Raises:
- `NotEnough` when it costs more than you have: `15x Parsnip Seeds costs 300g but you have 250g`. Do the sum first: `farmer.money // price`.
- `ActionFailed` when it isn't sold here: `this shop doesn't sell 'Melon Seeds'; did you mean: ...`.
- `Closed` as above.

```python
go("SeedShop")
here.shop.buy("Parsnip Seeds", min(20, farmer.money // 20))
```

## The bigger backpack
At Pierre's, `here.shop.buy("Backpack")` (that exact name) buys the backpack upgrade at its spot on the counter, not from the shop menu: 12 to 24 slots for 2,000g, then 24 to 36 for 10,000g. Returns `{"bought": "backpack: 12 -> 24 slots", "money": ...}`; raises `ActionFailed` if it wasn't bought (`the backpack wasn't bought (backpack 12 slots, 1500g)`).

## Shop.sell
`shop.sell(item, count=None)`

Sells the whole stack (or `count` of it) to the shop. Each shop buys only some kinds of things: Pierre crops, forage and seeds; Willy fish; Clint ores, minerals and gems; Marnie animal products.

Returns `{"sold": "Parsnip x10", "earned": 350, "money": 820}`.

Raises `ActionFailed` when this shop won't buy it (`this shop doesn't buy Copper Ore`), `NotEnough` when you don't have it, or when fewer moved than asked (`only 3 of 10 Parsnip were sold`).

Selling pays now; shipping (`docs("items/shipping")`) pays the same price overnight but works from anywhere and doesn't need the shop to be open.

## Shop.stock
`shop.stock()`

Opens the shop (like `buy`) and lists what it sells: Items with `.name`, `.price` (the shop's price) and `.count` (the stock; very large for unlimited).

```python
prices = {i.name: i.price for i in here.shop.stock()}
print("seeds:", [n for n in prices if "Seeds" in n])
```

## Opening hours
Most shops open 9am to 5pm, and lock their doors outside their hours (`go` raises `Closed`). A well-known shop's Place has `.hours` and `.open` (`places["SeedShop"].open`). Some well-known exceptions (the game's rules; check with `web` and remember what you learn):
- Pierre's (`SeedShop`): 9am to 5pm, closed on Wednesdays.
- `JojaMart`: 9am to 11pm.
- Willy's (`FishShop`): 9am to 5pm, but Willy is often away fishing.
- Clint (`Blacksmith`): 9am to 4pm. Marnie (`AnimalShop`): 9am to 4pm, closed Monday and Tuesday. Robin (`ScienceHouse`): 9am to 5pm.
- The `Saloon`: noon to midnight. The `AdventureGuild` (Marlon): 2pm to 10pm.
- Festival days close the shops (`day.festival`).

On the shop's own day off, or when the keeper is out, `buy` raises `Closed`. Plan shopping after the morning chores: `docs("strategy#Shops and hours")`.

## Blacksmith.crack_geodes
`here.shop.crack_geodes(item="all", count=None)`, at Clint's

Clint's counter (`places["Blacksmith"].shop`) is a Blacksmith: a Shop which also opens geodes on the anvil, 25g each: every geode you carry (geodes, frozen, magma and omni geodes, artifact troves...), or only one kind (`item`, by exact name), or `count` of them. Like the other shop calls, it works only in the Blacksmith: go there first. It picks "process geodes" at his counter.

Returns `{"cracked": 5, "got": {"Copper Ore": 6, "Quartz": 1, ...}}`, plus `"stopped"` with the reason when it stopped early: out of money, or the backpack too full for what comes out (keep two slots free).

Raises `ActionFailed` when you have no geodes (`you have no geodes`) or the anvil didn't open (Clint away from his counter).

```python
go("Blacksmith")
r = here.shop.crack_geodes()
print(r["got"])  # minerals and artifacts: donate new ones to the museum
```
