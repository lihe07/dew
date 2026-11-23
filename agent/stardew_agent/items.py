"""The inventory, and using what is held."""

from __future__ import annotations

from collections.abc import Iterable

from . import driver as d
from .common import ActionError, Interrupted, Tile, quality


TOOL_KINDS = {"hoe": "Hoe", "axe": "Axe", "pickaxe": "Pickaxe", "wateringcan": "WateringCan", "scythe": "Scythe", "weapon": "Weapon", "sword": "Weapon",
              "fishingrod": "FishingRod", "rod": "FishingRod", "pan": "Pan", "milkpail": "MilkPail", "shears": "Shears"}


class Items:
    """Mixin for Game: holding things and using them."""

    async def inventory(self) -> list[dict]:
        return await self.read("player.inventory")

    async def find_item(self, query: str | int) -> dict:
        items = await self.inventory()
        text = str(query).strip().lower()
        if text.isdigit():
            for item in items:
                if item["slot"] == int(text):
                    return item
            raise ActionError(f"inventory slot {text} is empty")
        for match in (
            lambda i: text in (i["id"].lower(), i["name"].lower(), (i["name"] + quality(i)).lower()),
            lambda i: text in i["name"].lower(),
        ):
            for item in items:
                if match(item):
                    return item
        raise ActionError(f"you don't have '{query}'")

    async def hold(self, query: str | int) -> dict:
        """Hold an item, like pressing its toolbar key."""
        item = await self.find_item(query)
        for _ in range(3):
            if item["slot"] < 12:
                break
            await self.press(self.keys["row"])
            item = await self.find_item(item["id"])
        if await self.read("player.slot") != item["slot"] or await self.read("player.held == null"):
            await self.press(self.hotbar[item["slot"]])
        return item

    async def armed(self) -> bool:
        return any(i["kind"] == "Weapon" for i in await self.inventory())

    async def hold_tool(self, name: str) -> dict:
        kind = TOOL_KINDS.get(name.lower().replace(" ", "").replace("_", ""))
        for item in await self.inventory():
            if item["tool"] and (item["kind"] == kind or (kind is None and name.lower() in item["name"].lower())):
                return await self.hold(item["slot"])
        raise ActionError(f"you don't have a {name}")

    async def empty_hands(self) -> None:
        """Hold something which can't be gifted, placed, eaten, or inserted by accident."""
        held = await self.read("player.held")
        if not held or held["tool"]:
            return
        items = {i["slot"]: i for i in await self.inventory()}
        for slot in range(12):
            if slot not in items or items[slot]["tool"]:
                await self.press(self.hotbar[slot])
                return

    async def swing(self, tile: Iterable[int], power: int = 0) -> None:
        """Use the held tool once on a tile beside us."""
        energy, held = await self.d.read(["player.energy", "player.held"])
        if not held or not held["tool"]:
            raise ActionError("not holding a tool")
        if energy <= 4 and held["kind"] not in ("Weapon", "Scythe", "MilkPail", "Shears"):
            raise ActionError("out of energy; eat something or sleep")
        charge = min(power, held.get("upgrade") or 0)
        press = d.step(600, f"player.toolPower >= {charge}") if charge else d.step(2)
        await self.d.run(
            d.input(tile=tuple(tile), buttons=[]), d.step(2), d.input(buttons=["mouse_left"]), press, d.input(buttons=[]),
            d.step(2), d.step(240, {"done": "player.canMove and not player.usingTool", "menu": "state.menu"}),
        )

    async def act(self, tile: Iterable[int]) -> None:
        """Press the action button on a tile beside us."""
        await self.click_tile(tile, right=True)

    async def use_tool(self, name: str, tiles: list[Tile], swings: int = 1, power: int = 0) -> dict:
        await self.ready()
        await self.hold_tool(name)
        todo = {tuple(t) for t in tiles}
        total = len(todo)

        async def act(tile: Tile) -> None:
            for _ in range(swings):
                await self.swing(tile, power)
            todo.discard(tile)

        async def targets() -> list[Tile]:
            return list(todo)

        failed = await self.for_nearest(targets, act)
        await self.step(15)
        return {"done": total - len(todo), "of": total, **self.unreached(failed)}

    async def interact(self, tiles: list[Tile], item: str | None = None) -> dict:
        await self.ready()
        responded = 0
        for tile in [tuple(t) for t in tiles]:
            if await self.here() != tile:
                try:
                    await self.goto_around([tile], 8, what=f"tile {list(tile)}")
                except Interrupted:
                    raise
                except ActionError:
                    if len(tiles) == 1:
                        raise
                    continue
            await (self.hold(item) if item else self.empty_hands())
            await self.act(tile)
            responded += 1
            await self.step(45, {"menu": "state.menu", "moving": "state.fading", "scene": "state.cutscene"})
            await self.settle(200)
            if await self.read("state.menu") and not await self.auto_close():
                break
        return {"interacted": responded}

    async def place(self, item: str, tiles: list[Tile]) -> dict:
        await self.ready()
        todo = {tuple(t) for t in tiles}
        placed, refused = 0, []
        first = await self.find_item(item)
        while todo:
            try:
                await self.ready()
                target = await self.goto(list(todo), 8)
            except Interrupted:
                raise
            except ActionError:
                refused += sorted(todo)
                break
            todo.discard(target)
            try:
                held = await self.hold(first["id"])
            except ActionError:
                return {"placed": placed, "note": f"ran out of {first['name']}", "left": len(todo) + 1}
            before = sum(i["stack"] for i in await self.inventory() if i["id"] == held["id"])
            await self.click_tile(target, after=5)
            after = sum(i["stack"] for i in await self.inventory() if i["id"] == held["id"])
            if after < before:
                placed += 1
            else:
                refused.append(target)
        await self.empty_hands()
        return {"placed": placed, "couldnt_place_at": refused}

    async def eat(self, query: str) -> dict:
        await self.ready()
        item = await self.hold(query)
        if item.get("energy") is None:
            raise ActionError(f"{item['name']} isn't edible")
        for attempt in range(2):
            await self.click_tile(await self.here(), right=True)
            await self.step(60, "state.menu")
            question = await self.read("menu.text")
            if question and item["name"].lower() in question.lower():
                break
            # the click reached something next to us (a bin, a chest...), so close it and move to open ground
            if await self.read("state.menu"):
                await self.close_menu()
            if attempt == 0:
                await self.goto(await self.open_ground(), what="open ground")
                await self.hold(item["id"])
        else:
            raise ActionError(f"couldn't start eating {item['name']}")
        await self.dialogue(0)
        await self.step(600, {"done": "state.free"})
        await self.empty_hands()
        return {"ate": item["name"]}

    async def use(self, query: str) -> dict:
        """Use a held item on yourself, like a totem or a staircase: the action button on your own tile."""
        await self.ready()
        item = await self.hold(query)
        here = await self.read("location.name")
        await self.click_tile(await self.here(), right=True)
        await self.step(240, {"moved": f"location.name != '{here}'", "menu": "state.menu", "fading": "state.fading"})
        await self.settle()
        if await self.read("menu.choices"):
            return {"used": item["name"], "note": "it asks a question; answer with dialogue"}
        return {"used": item["name"], "now_at": await self.read("location.name")}
