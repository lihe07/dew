# Watering
water waters the crops that need it, here or in an area, and refills the watering can at water nearby.

## water
`water(where=None)`

Waters every thirsty crop in the current location, or only those in `where` (an Area, a tile, or a list of them: `docs("moving#Tiles and areas")`). A crop is thirsty when it's alive, needs water, and hasn't been watered today; dry tilled soil without a crop isn't watered. It walks to the nearest one each time.

When the can runs dry, it walks to the nearest water it can refill from in this location (a pond, a river, the well), fills up, and comes back.

Returns `{"watered": n, "still_dry": k}`, plus `"left"` and `"why_left"` if some crops couldn't be reached. With nothing to do: `{"watered": 0, "note": "nothing needs water here"}` (or `in that area`), at once and without taking out the can.

Raises:
- `NotEnough` when the can is empty and there's no water in this location: `the watering can is empty and there's no water here`.
- `ActionFailed` if refilling fails three times: `couldn't refill the watering can`.
- `NotEnough` when energy runs out (`out of energy (watered 30)`).
- `TypeError` for a malformed `where`, and `ActionFailed` for tiles in another place.

```python
water()                         # everything thirsty here
water(Area(60, 15, 64, 18))     # just the new plot
```

## Gotchas
- Rain waters every outdoor crop: on rainy mornings `water()` returns `watered: 0` and costs nothing, so you don't need to check the weather first.
- Each pour costs energy (about 2 with the basic can). A big field can cost most of a day's energy: see `docs("exceptions#LowEnergy")`.
- Plant near water: walks to refill add up. `here.find("water")` lists refill tiles here, nearest first.
- The can's level is in the backpack: the watering can item has `.water` and `.water_max` (`docs("items#Item fields")`).
- Watering in the evening still counts for today, but leave time to get home (`docs("time#Getting home")`).
