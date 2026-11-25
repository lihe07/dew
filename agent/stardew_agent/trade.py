"""Shipping, shops, and containers."""

from __future__ import annotations


from . import driver as d
from .common import ActionError, Interrupted, Tile, label, quality, unknown


COUNTERS = ("Buy", "OpenShop", "Blacksmith", "Carpenter", "AnimalShop", "Saloon", "IceCreamStand", "Theater_BoxOffice")


def counter(action: str) -> bool:
    """Whether a tile action is a shop counter."""
    return action.split(" ")[0] in COUNTERS


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
        await self.go("Farm")
        target = (await self.go((bin["x"], bin["y"]), beside=True, what="the shipping bin")).target
        await self.empty_hands()
        await self.act(target)
        await self.step(60, "state.menu")
        if await self.read("menu.type") != "ItemGrabMenu":
            raise ActionError("the shipping bin didn't open")
        moved = await self.move_stack(True, item["slot"], count)
        await self.close_menu()
        worth = (item.get("price") or 0) * moved
        if moved:
            self.sales.append(f"{item['name']}{quality(item)} x{moved} shipped (~{worth}g)")
        if moved < min(count or item["stack"], item["stack"]):
            raise ActionError(f"only {moved} of {count or item['stack']} {item['name']} were shipped")
        return {"shipped": f"{item['name']}{quality(item)} x{moved}", "worth_about": worth}

    async def grid(self, player: bool) -> list[dict]:
        """The open menu's slots: the backpack's, or the container's."""
        if player:
            return await self.backpack()
        grid = next((g for g in await self.read("menu.inventories") or [] if not g["player"]), None)
        if not grid:
            raise ActionError("this menu has no container")
        return grid["slots"]

    async def move_stack(self, player: bool, slot: int, count: int | None = None) -> int:
        """Click count (default all) of the stack in one slot of the open menu (the backpack's, or the container's) out of it:
        shipping bin, chest, or shop alike. The whole stack is one left-click; part of it is one right-click per item, each
        checked (and retried with a longer pause if the game didn't take it). Returns how many moved."""
        async def stack() -> int:
            found = next((s for s in await self.grid(player) if s["slot"] == slot), None)
            return ((found or {}).get("item") or {}).get("stack", 0)

        start = await stack()
        count = min(count or start, start)
        at = next(s for s in await self.grid(player) if s["slot"] == slot)["at"]
        if count == start:
            await self.click_ui(at)
        else:
            for _ in range(count):
                before = await stack()
                for after in (4, 12, 30):
                    await self.click_ui(at, right=True, after=after)
                    if await stack() < before:
                        break
                else:
                    break
        if await self.read("menu.held"):
            await self.stow_held()
        return start - await stack()

    async def shop_stock(self) -> list[dict]:
        goods = await self.read({"from": "menu.raw.itemPriceAndStock", "select": ["Key.DisplayName", "Key.IsRecipe", "Value.Price", "Value.Stock", "Value.TradeItem", "Value.TradeItemCount"]})
        prices = {(g["Key.DisplayName"], bool(g["Key.IsRecipe"])): g for g in goods}
        order = await self.read({"from": "menu.raw.forSale", "select": ["DisplayName", "IsRecipe"]})
        return [{"i": i, "name": o["DisplayName"], "recipe": bool(o["IsRecipe"]), **{k[6:]: v for k, v in prices.get((o["DisplayName"], bool(o["IsRecipe"])), {}).items() if k.startswith("Value.")}} for i, o in enumerate(order)]

    async def open_shop(self, at: Tile | None = None) -> None:
        """If we're in a store and no shop is open, use its counter."""
        if await self.read("menu.type") == "ShopMenu":
            return
        await self.use_counter(("shop", "supplies", "buy"), at)
        if await self.read("menu.type") != "ShopMenu":
            text = await self.read("menu.text")
            if text:
                await self.auto_close()
            raise ActionError("the shop didn't open" + (f": {text}" if text else "; the shopkeeper may be away (people[name].place says where they are)"))

    async def use_counter(self, wanted: tuple[str, ...], at: Tile | None = None) -> None:
        """Use the shop counter at `at` (default: the nearest here); if it asks what you want (Clint: shop, upgrade, geodes),
        answer with the first choice containing one of the wanted words."""
        me = at or await self.here()
        counters = sorted((a for a in await self.read("location.actions") if counter(a["action"])),
                          key=lambda a: abs(a["x"] - me[0]) + abs(a["y"] - me[1]))
        if not counters:
            raise ActionError("no shop is open, and there's no shop counter here")
        await self.ready()
        await self.interact([(counters[0]["x"], counters[0]["y"])])
        choices = await self.read("menu.choices")
        if choices:
            pick = next((i for i, c in enumerate(choices) if any(k in c.lower() for k in wanted)), None)
            if pick is None:
                raise Interrupted(f"the counter asks: {' / '.join(f'{i}: {c}' for i, c in enumerate(choices))}; answer with answer(n)")
            await self.dialogue(pick)
            await self.step(30, "state.menu")

    async def crack_geodes(self, query: str = "all", count: int | None = None, at: Tile | None = None) -> dict:
        """At Clint's: open geodes (all of them, or one kind, or count of it) on his anvil, 25g each. Returns what came out."""
        if await self.read("location.name") != "Blacksmith":
            await self.go("Blacksmith")
        if await self.read("menu.type") != "GeodeMenu":
            await self.use_counter(("geode",), at)
            await self.step(30, "menu.type == 'GeodeMenu'")
        if await self.read("menu.type") != "GeodeMenu":
            raise ActionError("Clint's geode anvil didn't open; Clint may be away from his counter (people[\"Clint\"].place says where he is)")
        before = {i["name"]: i["stack"] for i in await self.inventory()}
        text, cracked, why = str(query).lower(), 0, None
        anvil = next(c["at"] for c in await self.read("menu.components") if c["field"] == "geodeSpot")
        for slot in [s for s in await self.backpack() if s.get("item") and s.get("usable")]:  # the menu lights up only geodes
            if text not in ("all", slot["item"]["name"].lower()):
                continue
            await self.click_ui(slot["at"])  # onto the cursor
            while (held := await self.read("menu.held")) and (count is None or cracked < count):
                if await self.read("player.money") < 25:
                    why = "out of money (25g a geode)"
                    break
                money = await self.read("player.money")
                await self.click_ui(anvil)
                await self.step(240, "menu.raw.geodeAnimationTimer <= 0")
                if await self.read("player.money") == money:
                    why = "the backpack is too full for what comes out"
                    break
                cracked += 1
            await self.stow_held()
            if why or (count is not None and cracked >= count):
                break
        await self.close_menu()
        after = {i["name"]: i["stack"] for i in await self.inventory()}
        got = {n: c - before.get(n, 0) for n, c in after.items() if c > before.get(n, 0)}
        if not cracked:
            raise ActionError(why or (f"you have no {query}" if text != "all" else "you have no geodes"))
        return {"cracked": cracked, "got": got, **({"stopped": why} if why else {})}

    async def buy(self, query: str, count: int = 1, at: Tile | None = None) -> dict:
        if str(query).lower() == "backpack":
            # a bigger backpack is sold at its own spot on Pierre's counter, not in the shop menu
            spots = await self.read({"from": "location.actions", "where": "action == 'BuyBackpack'"})
            if spots:
                return await self.buy_backpack((spots[0]["x"], spots[0]["y"]))
        await self.open_shop(at)
        stock = await self.shop_stock()
        text = str(query).lower()
        good = next((g for g in stock if g["name"].lower() == text), None)
        if not good:
            raise ActionError(unknown("this shop doesn't sell", query, {g["name"]: g["name"] for g in stock}))
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
        bought = count - max(left, 0)
        if bought < count:
            why = "the backpack is full" if not await self.read("player.free_slots") else "the shop wouldn't sell more"
            raise ActionError(f"bought only {bought} of {count} {good['name']}: {why}")
        return {"bought": f"{good['name']} x{bought}", "money": await self.read("player.money")}

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

    async def sell(self, query: str, count: int | None = None, at: Tile | None = None) -> dict:
        await self.open_shop(at)
        item = await self.find_item(query)
        slot = (await self.backpack())[item["slot"]]
        if not slot["usable"]:
            raise ActionError(f"this shop doesn't buy {item['name']}")
        await self.step(40, "menu.raw.safetyTimer <= 0")
        money = await self.read("player.money")
        moved = await self.move_stack(True, item["slot"], count)
        await self.step(60, "menu == null or menu.closable")
        now = await self.read("player.money")
        if moved:
            self.sales.append(f"{item['name']} x{moved} sold ({now - money}g)")
        if moved < min(count or item["stack"], item["stack"]):
            raise ActionError(f"only {moved} of {count or item['stack']} {item['name']} were sold")
        return {"sold": f"{item['name']} x{moved}", "earned": now - money, "money": now}

    async def open_chest(self, place: str, tile: Tile) -> None:
        """Walk to the chest on this tile of a place, and open it."""
        await self.ready()
        await self.go((place, tile))
        await self.empty_hands()
        await self.click_tile(tile, right=True, after=30)
        await self.step(60, "menu.type == 'ItemGrabMenu'")
        if await self.read("menu.type") != "ItemGrabMenu":
            raise ActionError(f"the chest at {list(tile)} didn't open")

    async def container(self, action: str, query: str = "all", count: int | None = None) -> dict:
        """In the open chest, put or take one named stack (or count of it), or everything ("all"; tools stay in the backpack)."""
        if await self.read("menu.type") != "ItemGrabMenu":
            raise ActionError("no chest is open")
        put, text = action == "put", str(query).lower()
        moved: list[str] = []
        for _ in range(72):
            if await self.read("menu.type") != "ItemGrabMenu":
                break
            full = [s for s in await self.grid(put) if s.get("item") and not (put and text == "all" and s["item"].get("tool"))]
            slot = next((s for s in full if text in ("all", s["item"]["name"].lower(), label(s["item"]).lower())), None)
            if not slot:
                if not moved:
                    where = "your backpack" if put else "the chest"
                    raise ActionError(unknown(f"this isn't in {where}:", query, {label(s["item"]): label(s["item"]) for s in full}) if text != "all" else f"{where} is empty")
                break
            item = slot["item"]
            if not put and not await self.read("player.free_slots"):
                if not any(i["id"] == item["id"] and i["quality"] == item["quality"] and i["stack"] < i["max_stack"] for i in await self.inventory()):
                    raise ActionError("your inventory is full" + (f" (took {', '.join(moved)})" if moved else ""))
            n = await self.move_stack(put, slot["slot"], count)
            if not n:
                raise ActionError("that didn't move; is there room? (item.trash() can make some)")
            moved.append(f"{item['name']}{quality(item)} x{n}")
            if text != "all":
                break
        return {"moved": moved}
