# Shops
buy, sell and shop_stock at the shop you're in, the bigger backpack at Pierre's, and when shops are open.

## How shops work
`buy`, `sell` and `shop_stock` work at the shop in the current location: if no shop menu is open, they use the nearest shop counter here (the shopkeeper must be behind it). So go to the shop first (`go("Pierre's")`). A counter that asks what you want (Clint asks shop / upgrade / geodes) is answered with the shopping option. The shop is left open until the next action, which closes it.

Errors when there's no shop: `Closed` for `no shop is open, and there's no shop counter here` and `the shop didn't open ...` (the keeper may be away: `people("Pierre")` says where he is, `docs("people#people")`).

## buy
`buy(item, count=1)`

Buys `count` of an item the shop sells (matched by exact name, then by part of the name, or by its number in the stock list). Buys in clicks of 25, 5 and 1. `count` is capped at the shop's stock.

Returns `{"bought": "Parsnip Seeds x15", "money": 485}`. If the purchases stopped partway (no room in the backpack), the count says how many were really bought.

Raises:
- `NotEnough` when it costs more than you have: `15x Parsnip Seeds costs 300g but you have 250g`. Do the sum first: `money() // price`.
- `ActionFailed` when it isn't sold here: `'Melon Seeds' isn't sold here`.
- `Closed` as above.

```python
go("Pierre's")
buy("Parsnip Seeds", min(20, money() // 20))
```

## The bigger backpack
At Pierre's, `buy("Backpack")` (any name containing "pack") buys the backpack upgrade at its spot on the counter, not from the shop menu: 12 to 24 slots for 2,000g, then 24 to 36 for 10,000g. Returns `{"bought": "backpack: 12 -> 24 slots", "money": ...}`; raises `ActionFailed` if it wasn't bought (`the backpack wasn't bought (backpack 12 slots, 1500g)`).

## sell
`sell(item, count=None)`

Sells the whole stack (or `count` of it) to the shop. Each shop buys only some kinds of things: Pierre crops, forage and seeds; Willy fish; Clint ores, minerals and gems; Marnie animal products.

Returns `{"sold": "Parsnip x10", "money": 820}`.

Raises `ActionFailed` when this shop won't buy it (`this shop doesn't buy Copper Ore`), `NotEnough` when you don't have it, or when fewer moved than asked (`only 3 of 10 Parsnip were sold`).

Selling pays now; shipping (`docs("items/shipping")`) pays the same price overnight but works from anywhere and doesn't need the shop to be open.

## shop_stock
`shop_stock()`

Opens the shop here (like `buy`) and lists what it sells: items with `.name`, `.price` (the shop's price) and `.count` (the stock; very large for unlimited).

```python
prices = {i.name: i.price for i in shop_stock()}
print("seeds:", [n for n in prices if "Seeds" in n])
```

## Opening hours
Most shops open 9am to 5pm, and lock their doors outside their hours (`go` raises `Closed`). Some well-known exceptions (the game's rules; check with `web` and remember what you learn):
- Pierre's: 9am to 5pm, closed on Wednesdays.
- JojaMart: 9am to 11pm.
- Willy's fish shop: 9am to 5pm, but Willy is often away fishing.
- Clint: 9am to 4pm. Marnie: 9am to 4pm, closed Monday and Tuesday. Robin: 9am to 5pm.
- The Saloon: noon to midnight. The Adventurer's Guild (Marlon): 2pm to 10pm.
- Festival days close the shops.

On the shop's own day off, or when the keeper is out, `buy` raises `Closed`. Plan shopping after the morning chores: `docs("strategy#Shops and hours")`.
