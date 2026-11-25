# Playing well
Strategy for a good farm: seasons and crop timing, money, energy, shops and hours, friendship, backpack room, fishing, mining, and getting home.

## The shape of a good day
1. Morning on the farm: `gather("crops")`, `water()`, `pet()`, `gather("machines")`, and ship what's ready. Check `day` for birthdays and the weather, and the mailbox.
2. Late morning to afternoon: errands in town while the shops are open (seeds, upgrades, gifts), forage on the way.
3. Afternoon and evening: money or skill work: plant and clear, fish, or the mines.
4. Home by midnight; sleep. Spend the day's energy: energy left at bedtime is wasted.

Plan one stretch at a time and keep the farmer busy while you think: `docs("scripts#Planning")`.

## Seasons and crops
- A season has 28 days, and crops still growing when it changes die. A seed planted today with `days` to grow is ready on day `today + days`, so only plant it if `days` is at most the days left (`28 - day.day`). `catalog["Melon Seeds"].crop` gives the seasons, days, whether it regrows, and the price of the produce.
- Crops that regrow (green beans, strawberries, blueberries, hot peppers, cranberries) keep paying after the first harvest; plant them early in their season.
- Good early choices (the game's numbers; check with `catalog`):
  - Spring: parsnips (20g seed, 4 days, sell 35g) to start, potatoes, cauliflower (80g, 12 days, 175g), green beans; strawberries from the Egg Festival.
  - Summer: blueberries (80g, 13 days, then every 4), melons (80g, 12 days, 250g), hot peppers, tomatoes.
  - Fall: cranberries (240g, 7 days, then every 5), pumpkins (100g, 13 days, 320g), corn carries over from summer.
  - Winter: nothing grows outdoors; forage, fish, mine and make friends.
- Plant compact blocks near water, so watering walks are short (`docs("farming/watering")`). A scarecrow keeps crows off a block (`place("Scarecrow", tile)`).
- On the last days of a season, harvest everything and don't plant what won't finish.

## Money
- Money compounds early: put most of it into the best seeds you can plant, water and harvest before the season ends. Keep a little for food.
- Do the sums in Python: `count = min(30, (farmer.money - 100) // price)`.
- Ship crops and forage overnight (`docs("items/shipping")`), or sell them at a shop for money now. Willy pays for fish, Clint for minerals.
- Quests pay: check `quests` and collect with `quests.collect()`.
- Worth buying early: the bigger backpack at Pierre's (2,000g), a copper watering can and hoe at Clint's later.

## Energy
- Tool swings and watering cost about 2 energy each with basic tools, fishing about 8 a cast. The scythe, walking, talking, shopping, foraging and petting are free.
- Under 15 the safety raises `LowEnergy` (`docs("exceptions#LowEnergy")`). Eat cheap food (`docs("items#Item.eat")`), or soak at the spa from Summer 3 (`docs("time#soak")`), to keep going.
- Energy left at bedtime is wasted, but going to bed after midnight costs energy tomorrow. A full night restores it all.

## Shops and hours
- Shops mostly open 9am to 5pm; Pierre's is closed on Wednesdays. Do farm work first, then shop. Hours and exceptions: `docs("items/shops#Opening hours")`.
- On a locked door, `go` raises `Closed`: catch it and `wait("9:00 am")`, or do something else first.
- Remember opening hours you learn (`shop.pierre.hours`).

## Friendship
- Talk to people daily when you pass them; it's free. `people` shows who you haven't talked to today (`.talked_today`) and where they are (`.place`).
- Liked gifts (up to two a week each) build friendship fast, and birthday gifts count eight times. Look up likes with `web` once, remember them, and keep a few in the backpack. See `docs("people#Friendship")`.
- `day.birthday` tells you whose birthday it is.

## Backpack room
- Keep a few free slots: harvests, forage, fish and ore each need room, and a full backpack stops `gather` and `fish`.
- Ship or sell low-value things, store keepers in a chest (`docs("items/chests")`), trash junk (`docs("items#Item.trash")`).
- Pierre's bigger backpack (2,000g, 24 slots) pays for itself.

## Fishing
- Fishing costs no money and levels a skill, and fish sell well; a good use of rainy days and evenings. The Beach, the river and the mountain lake have different fish. See `docs("fishing")`.
- Rain brings better fish to rivers and lakes.

## Mining
- The mines give ore for tool upgrades and crafting, gems to sell, and combat experience. Rainy days and days without farm work suit them. Bring food that heals. See `docs("mining")`.
- Every 5 floors unlocks an elevator stop, so progress is kept.

## Going home
- Be home by midnight: walking home from the mines or the beach takes an hour or more of game time. The safety raises `TimeToGoHome` when it's time (`docs("time#Getting home")`).
- End evening trips at the farm: ship, then `sleep()`.

## Memory
Remember what you'll need again: tiles of your plots and chests, prices, hours, people's likes and birthdays, deadlines, and the plan for tomorrow. Forget what's done.
