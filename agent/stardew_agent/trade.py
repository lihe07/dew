"""Shipping, shops, and containers."""

from __future__ import annotations

from collections.abc import Iterable

from . import driver as d
from .common import ActionError, Interrupted, Tile, label, quality


class Trade:
    """Mixin for Game: things that change hands."""

    async def ship(self, query: str, count: int | None = None) -> dict:
        item = await self.find_item(query)
        if not item["shippable"]:
            raise ActionError(f"{item['name']} can't be shipped")
        bins = await self.read({"from": "locations['Farm'].building_rows", "where": "name == 'Shipping Bin' and days_left == 0"})
        if not bins:
            raise ActionError("there's no shipping bin")
        bin = bins[0]
        tiles = [(bin["x"], bin["y"]), (bin["x"] + 1, bin["y"])]
        await self.travel("Farm", [list(t) for t in tiles])
        target = await self.goto(tiles, 8, what="the shipping bin")
        await self.empty_hands()
        await self.act(target)
        await self.step(60, "state.menu")
        if await self.read("menu.type") != "ItemGrabMenu":
            raise ActionError("the shipping bin didn't open")
        count = min(count or item["stack"], item["stack"])
        slots = await self.backpack()
        at = slots[(await self.find_item(item["slot"]))["slot"]]["at"]
        await self._click_stack(at, count, item["stack"])
        await self.close_menu()
        await self._expect_fewer(item, count, "shipped")
        return {"shipped": f"{item['name']}{quality(item)} x{count}", "worth_about": (item.get("price") or 0) * count}

    async def _click_stack(self, at: Iterable[float], count: int, stack: int) -> None:
        """Move a whole stack with one click, or part of it with a right-click each."""
        if count >= stack:
            await self.click_ui(at)
        else:
            for _ in range(count):
                await self.click_ui(at, right=True, after=2)

    async def _expect_fewer(self, item: dict, count: int, verb: str) -> None:
        now = next((i["stack"] for i in await self.inventory() if i["slot"] == item["slot"] and i["id"] == item["id"]), 0)
        if now != item["stack"] - count:
            raise ActionError(f"only {item['stack'] - now} of {count} {item['name']} were {verb}")

    async def shop_stock(self) -> list[dict]:
        goods = await self.read({"from": "menu.raw.itemPriceAndStock", "select": ["Key.DisplayName", "Key.IsRecipe", "Value.Price", "Value.Stock", "Value.TradeItem", "Value.TradeItemCount"]})
        prices = {(g["Key.DisplayName"], bool(g["Key.IsRecipe"])): g for g in goods}
        order = await self.read({"from": "menu.raw.forSale", "select": ["DisplayName", "IsRecipe"]})
        return [{"i": i, "name": o["DisplayName"], "recipe": bool(o["IsRecipe"]), **{k[6:]: v for k, v in prices.get((o["DisplayName"], bool(o["IsRecipe"])), {}).items() if k.startswith("Value.")}} for i, o in enumerate(order)]

    async def open_shop(self) -> None:
        """If we're in a store and no shop is open, use its counter."""
        if await self.read("menu.type") == "ShopMenu":
            return
        verbs = ("Buy", "OpenShop", "Blacksmith", "Carpenter", "AnimalShop", "Saloon", "IceCreamStand", "Theater_BoxOffice", "BuyBackpack")
        me = await self.here()
        counters = sorted((a for a in await self.read("location.actions") if a["action"].split(" ")[0] in verbs and a["action"] != "BuyBackpack"),
                          key=lambda a: abs(a["x"] - me[0]) + abs(a["y"] - me[1]))
        if not counters:
            raise ActionError("no shop is open, and there's no shop counter here")
        await self.ready()
        await self.interact([(counters[0]["x"], counters[0]["y"])])
        choices = await self.read("menu.choices")
        if choices:
            # e.g. the blacksmith asks whether to shop, upgrade, or open geodes
            pick = next((i for i, c in enumerate(choices) if any(k in c.lower() for k in ("shop", "supplies", "buy"))), None)
            if pick is None:
                raise Interrupted(f"the counter asks: {' / '.join(f'{i}: {c}' for i, c in enumerate(choices))}; answer with dialogue")
            await self.dialogue(pick)
            await self.step(30, "state.menu")
        if await self.read("menu.type") != "ShopMenu":
            text = await self.read("menu.text")
            if text:
                await self.auto_close()
            raise ActionError("the shop didn't open" + (f": {text}" if text else "; the shopkeeper may be away (find people shows where they are)"))

    async def buy(self, query: str, count: int = 1) -> dict:
        if "pack" in str(query).lower():
            # a bigger backpack is sold at its own spot on Pierre's counter, not in the shop menu
            spots = await self.read({"from": "location.actions", "where": "action == 'BuyBackpack'"})
            if spots:
                return await self.buy_backpack((spots[0]["x"], spots[0]["y"]))
        await self.open_shop()
        stock = await self.shop_stock()
        text = str(query).lower()
        good = next((g for g in stock if text.isdigit() and g["i"] == int(text)), None) or next((g for g in stock if g["name"].lower() == text), None) or next((g for g in stock if text in g["name"].lower()), None)
        if not good:
            raise ActionError(f"'{query}' isn't sold here")
        money = await self.read("player.money")
        count = min(count, good.get("Stock") or 999)
        if good["Price"] * count > money:
            raise ActionError(f"{count}x {good['name']} costs {good['Price'] * count}g but you have {money}g")
        for _ in range(len(stock)):
            top = await self.read("menu.raw.currentItemIndex")
            if top <= good["i"] < top + 4:
                break
            await self.d.run(d.input(scroll=1 if good["i"] < top else -1), d.step(3))
        top = await self.read("menu.raw.currentItemIndex")
        spots = await self.read({"from": "menu.raw.forSaleButtons", "select": ["bounds.Center"]})
        at = spots[good["i"] - top]["bounds.Center"]
        await self.step(40, "menu.raw.safetyTimer <= 0")
        left = count
        while left > 0:
            size, keys = (25, ["LeftShift", "LeftControl"]) if left >= 25 else (5, ["LeftShift"]) if left >= 5 else (1, [])
            if good["recipe"] or (good.get("TradeItem") is not None):
                size, keys = 1, []
            before = await self.read("player.money")
            await self.click_ui(at, keys=keys)
            if await self.read("player.money") == before and good["Price"]:
                break
            left -= size
        if await self.read("menu.type") == "ShopMenu":
            await self.stow_held()
        return {"bought": f"{good['name']} x{count - left}", "money": await self.read("player.money")}

    async def buy_backpack(self, spot: Tile) -> dict:
        before = await self.read({"size": "player.maxItems", "money": "player.money"})
        await self.interact([spot])
        if await self.read("menu.choices"):
            await self.dialogue(0)
        await self.settle()
        after = await self.read({"size": "player.maxItems", "money": "player.money"})
        if after["size"] <= before["size"]:
            raise ActionError(f"the backpack wasn't bought (backpack {before['size']} slots, {before['money']}g)")
        return {"bought": f"backpack: {before['size']} -> {after['size']} slots", "money": after["money"]}

    async def sell(self, query: str, count: int | None = None) -> dict:
        await self.open_shop()
        item = await self.find_item(query)
        slot = (await self.backpack())[item["slot"]]
        if not slot["usable"]:
            raise ActionError(f"this shop doesn't buy {item['name']}")
        await self.step(40, "menu.raw.safetyTimer <= 0")
        count = min(count or item["stack"], item["stack"])
        await self._click_stack(slot["at"], count, item["stack"])
        await self.step(60, "menu == null or menu.closable")
        await self._expect_fewer(item, count, "sold")
        return {"sold": f"{item['name']} x{count}", "money": await self.read("player.money")}

    async def open_chest(self) -> None:
        """Open the nearest chest here (walking to it), unless a container is already open."""
        if await self.read("menu.type") == "ItemGrabMenu":
            return
        await self.ready()
        chests = await self.read({"from": "location.object_rows", "where": "name == 'Chest' or name == 'Big Chest' or name == 'Stone Chest'", "select": ["x", "y"]})
        if not chests:
            raise ActionError(f"there's no chest in {await self.read('location.name')}; place one, or go to the one on the farm")
        me = await self.here()
        x, y = min(((c["x"], c["y"]) for c in chests), key=lambda c: abs(c[0] - me[0]) + abs(c[1] - me[1]))
        await self.goto([(x, y)], 4, what="the chest")
        await self.empty_hands()
        await self.click_tile((x, y), right=True, after=30)
        await self.step(60, "menu.type == 'ItemGrabMenu'")
        if await self.read("menu.type") != "ItemGrabMenu":
            raise ActionError(f"the chest at {[x, y]} didn't open")

    async def container(self, action: str, query: str = "all") -> dict:
        if await self.read("menu.type") != "ItemGrabMenu":
            await self.open_chest()
        if await self.read("menu.raw.shippingBin"):
            if action != "put" or query == "all":
                raise ActionError("this is the shipping bin: put one named item at a time, or use ship(item)")
            item = await self.find_item(query)
            await self.click_ui((await self.backpack())[item["slot"]]["at"])
            await self._expect_fewer(item, item["stack"], "shipped")
            return {"shipped": label(item)}
        moved: list[str] = []
        for _ in range(72):
            if await self.read("menu.type") != "ItemGrabMenu":
                break
            grids = await self.read("menu.inventories")
            grid = next((g for g in grids if g["player"] == (action == "put")), None)
            if not grid:
                raise ActionError("this menu has no container")
            full = [s for s in grid["slots"] if s.get("item")]
            text = str(query).lower()
            slot = next((s for s in full if text == "all" or text == str(s["slot"]) or text == s["item"]["name"].lower()), None) or next((s for s in full if text in s["item"]["name"].lower()), None)
            if not slot:
                if not moved:
                    where = "your backpack" if action == "put" else "the chest"
                    raise ActionError(f"'{query}' isn't in {where}" if text != "all" else f"{where} is empty")
                break
            if action == "put" and text == "all":
                raise ActionError("name the item to put")
            if action == "take" and not await self.read("player.free_slots"):
                item = slot["item"]
                if not any(i["id"] == item["id"] and i["quality"] == item["quality"] and i["stack"] + item["stack"] <= i["max_stack"] for i in await self.inventory()):
                    raise ActionError("your inventory is full" + (f" (took {', '.join(moved)})" if moved else ""))
            await self.click_ui(slot["at"])
            if await self.read("menu.held"):
                await self.stow_held()
            now = next((g for g in await self.read("menu.inventories") or [] if g["player"] == (action == "put")), None)
            if now and len([s for s in now["slots"] if s.get("item")]) >= len(full):
                raise ActionError("that didn't move; is there room? (use_item action='trash' can make some)")
            moved.append(label(slot["item"]))
            if text != "all":
                break
        return {"moved": moved}
