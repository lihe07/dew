"""The inventory, and using what is held."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from . import driver as d
from .common import ActionError, Interrupted, Tile, label, quality, unknown


TOOL_KINDS = {"hoe": "Hoe", "axe": "Axe", "pickaxe": "Pickaxe", "wateringcan": "WateringCan", "scythe": "Scythe", "weapon": "Weapon", "sword": "Weapon",
              "fishingrod": "FishingRod", "rod": "FishingRod", "pan": "Pan", "milkpail": "MilkPail", "shears": "Shears"}


@dataclass
class Visit:
    """How a visit went: how many targets were done, and each one skipped with why."""

    done: int = 0
    skipped: dict = field(default_factory=dict)

    def report(self, limit: int = 20) -> dict:
        """For a result: the skipped targets (at most limit) and why, if any."""
        if not self.skipped:
            return {}
        return {"left": [list(t) if isinstance(t, tuple) else t for t in list(self.skipped)[:limit]], "why_left": "; ".join(dict.fromkeys(self.skipped.values()))[:300]}


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
        for item in items:
            if text in (i.lower() for i in (item["id"], item["name"], item["name"] + quality(item))):
                return item
        raise ActionError(unknown("you don't have", query, {label(i): label(i) for i in items}))

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

    async def swing(self, tile: Iterable[int]) -> None:
        """Use the held tool once on a tile beside us."""
        energy, held = await self.d.read(["player.energy", "player.held"])
        if not held or not held["tool"]:
            raise ActionError("not holding a tool")
        if energy <= 4 and held["kind"] not in ("Weapon", "Scythe", "MilkPail", "Shears"):
            raise ActionError("out of energy; eat something or sleep")
        await self.d.run(
            d.input(tile=tuple(tile), buttons=[]), d.step(2), d.input(buttons=["mouse_left"]), d.step(2), d.input(buttons=[]),
            d.step(2), d.step(240, {"done": "player.canMove and not player.usingTool", "menu": "state.menu"}),
        )

    async def act(self, tile: Iterable[int]) -> None:
        """Press the action button on a tile beside us."""
        await self.click_tile(tile, right=True)

    async def visit(self, targets: Callable[[], Awaitable[list]], act: Callable[[Any], Awaitable[str | None]], limit: int = 2000) -> Visit:
        """The one way to work through many targets: go beside the nearest (by walking), act on it, and look again, until none are
        left. targets() is asked again after every act, since things move and clearing one opens the way to others. Targets are
        tiles (the nearest by path), or names of people (the nearest by walking minutes). act returns None, or why to skip that
        target; a target which act left unchanged is skipped too. What can't be reached is skipped and reported, never raised;
        Interrupted and act's own errors propagate. Between targets the guard may hunt (guard("all"))."""
        seen = Visit()
        for _ in range(limit):
            remaining = [t for t in await targets() if t not in seen.skipped]
            if not remaining:
                break
            await self.ready()
            await self.guard.watch(self, hunt=True)
            if isinstance(remaining[0], str):  # people: each can be reached, or not, on its own
                costs = []
                for name in remaining:
                    try:
                        costs.append((await self.minutes(name) or 0, name))
                    except ActionError:
                        costs.append((10_000, name))
                target = min(costs)[1]
                try:
                    await self.go(target, beside=True)
                except Interrupted:
                    raise
                except ActionError as ex:
                    seen.skipped[target] = str(ex)
                    continue
            else:
                try:
                    target = (await self.go(remaining, beside=True)).target
                except Interrupted:
                    raise
                except ActionError as ex:  # the nearest by path failed, so none of them can be reached
                    seen.skipped.update(dict.fromkeys(remaining, str(ex)))
                    break
                if target is None:
                    break
            why = await act(target)
            after = [t for t in await targets() if t not in seen.skipped]
            if why or (target in after and len(after) >= len(remaining)):
                seen.skipped[target] = why or "it was still there afterwards"
            else:
                seen.done += 1
        return seen

    async def interact(self, tiles: list[Tile], item: str | None = None) -> dict:
        await self.ready()
        responded = 0
        for tile in [tuple(t) for t in tiles]:
            if await self.here() != tile:
                try:
                    await self.go(tile, beside=True)
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
                target = (await self.go(list(todo), beside=True)).target
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
                await self.go(await self._open_ground(), what="open ground")
                await self.hold(item["id"])
        else:
            raise ActionError(f"couldn't start eating {item['name']}")
        await self.dialogue(0)
        await self.step(600, {"done": "state.free"})
        await self.empty_hands()
        return {"ate": item["name"]}

    async def _open_ground(self) -> list:
        """Walkable tiles nearby with nothing around them, where clicking yourself does nothing else."""
        x, y = await self.here()
        rect = [x - 5, y - 5, 11, 11]
        grid = await self.read({"$grid": {"rect": rect, "layers": ["pass"]}})
        rows = grid["pass"]
        spots = []
        for j in range(1, len(rows) - 1):
            for i in range(1, len(rows[j]) - 1):
                if all(rows[j + dy][i + dx] == "." for dx in (-1, 0, 1) for dy in (-1, 0, 1)):
                    spots.append((grid["x"] + i, grid["y"] + j))
        if not spots:
            raise ActionError("there's no open ground nearby to stop and eat")
        return spots

    async def use(self, query: str) -> dict:
        """Use a held item on yourself, like a totem or a staircase: the action button on your own tile."""
        await self.ready()
        item = await self.hold(query)
        here = await self.read("location.name")
        await self.click_tile(await self.here(), right=True)
        await self.step(240, {"moved": f"location.name != '{here}'", "menu": "state.menu", "fading": "state.fading"})
        await self.settle()
        if await self.read("menu.choices"):
            return {"used": item["name"], "note": "it asks a question; answer it with answer()"}
        return {"used": item["name"], "now_at": await self.read("location.name")}
