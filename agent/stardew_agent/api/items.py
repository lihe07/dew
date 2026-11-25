"""What you carry, what things are, and what you can make."""

from __future__ import annotations

from typing import Any

from .. import look
from ..common import label, quality, unknown
from ..game import ActionError
from . import base as b
from .base import Api, action, fact, field
from .people import talk


class Item(Api):
    """An item: in the backpack, a chest, or a shop's stock. Usable wherever an item goes."""

    fields = {"name": "", "count": "", "price": "what it sells for (a shop's price, in its stock)", "quality": "0 normal, 1 silver, 2 gold, 4 iridium",
              "category": "", "energy": "what eating it gives (0 if inedible)", "healing": "", "water": "a watering can's water", "water_max": "",
              "upgrade": "a tool's level: 0 basic to 4 iridium"}

    @classmethod
    def of(cls, row: dict) -> Item:
        # numbers are 0 when the game gives none, so scripts can always do sums
        return cls({"name": row["name"] + quality(row)}, name=row["name"], count=row.get("stack") or row.get("Stock") or 1, price=row.get("price") or row.get("Price") or 0,
                   quality=row.get("quality") or 0, category=row.get("category_name") or (str(row["category"]) if row.get("category") is not None else None),
                   energy=row.get("energy") or 0, healing=row.get("healing") or 0, water=row.get("water"), water_max=row.get("water_max"), upgrade=row.get("upgrade"))

    def __str__(self) -> str:
        f = self.f
        return (f"{f['name']}" + (f": x{f['count']}" if f["count"] < b.UNLIMITED else "") + (f", water {f['water']}/{f['water_max']}" if f["water"] is not None else "")
                + (f", {f['price']}g" if f["price"] else "") + (f", +{f['energy']} energy" if f["energy"] > 0 else ""))

    @action
    async def eat(self, ctx: Any) -> Any:
        """Eat or drink it."""
        return await ctx.game.eat(b.item(self))

    @action
    async def use(self, ctx: Any) -> Any:
        """Use it on yourself, like a totem or a staircase."""
        return await ctx.game.use(b.item(self))

    @action
    async def hold(self, ctx: Any) -> Any:
        """Hold it, like pressing its toolbar key."""
        return {"holding": label(await ctx.game.hold(b.item(self)))}

    @action
    async def trash(self, ctx: Any) -> Any:
        """Destroy the whole stack."""
        return await ctx.game.trash(b.item(self))

    @action
    async def ship(self, ctx: Any, count: Any = None) -> Any:
        """Put it (or count of it) in the shipping bin, from anywhere: it's sold overnight."""
        await ctx.game.ready()
        return await ctx.game.ship(b.item(self), b.count(count))

    @action
    async def give(self, ctx: Any, who: Any) -> Any:
        """Give it to someone, as talk(who, gift=item) does."""
        return await talk(ctx, who, self)


class Backpack(Api):
    """What you carry, read live: iterate it, len(backpack), or backpack["Parsnip"] (an Item; NotEnough if you have none)."""

    live = True

    async def _items(self, ctx: Any) -> list[Item]:
        return [Item.of(i) for i in await ctx.game.inventory()]

    @fact
    async def each(self, ctx: Any) -> list[Item]:
        return await self._items(ctx)

    @fact
    async def get(self, ctx: Any, name: Any) -> Item:
        return Item.of(await ctx.game.find_item(b.item(name)))

    @field
    async def items(self, ctx: Any) -> list[Item]:
        return await self._items(ctx)

    @field
    async def food(self, ctx: Any) -> list[Item]:
        """the items which give energy"""
        return [i for i in await self._items(ctx) if i.f["energy"] > 0]

    @field
    async def fish(self, ctx: Any) -> list[Item]:
        return [i for i in await self._items(ctx) if i.f["category"] in ("Fish", "-4")]

    @field
    async def free(self, ctx: Any) -> int:
        """empty slots"""
        return await ctx.game.read("player.free_slots")

    @field
    async def full(self, ctx: Any) -> bool:
        return await ctx.game.read("player.free_slots") == 0


class ItemInfo(Api):
    """What an item is, from the game's data (catalog["Parsnip Seeds"])."""

    var = "info"
    fields = {"name": "", "id": "", "description": "", "category": "", "energy": "", "sells_for": "",
              "crop": "for seeds: seasons, days, regrows, gives, sells_for"}


class Catalog(Api):
    """Every item in the game: catalog["Parsnip Seeds"] is an ItemInfo."""

    live = True

    @fact
    async def get(self, ctx: Any, name: Any) -> ItemInfo:
        found = await look.item(ctx.game, b.item(name))
        return ItemInfo(name=found["name"], id=found["id"], description=found["description"], category=found["category_name"], energy=found["energy"],
                        sells_for=found["you_sell_for"], crop=found.get("crop"))


class Recipe(Api):
    """A crafting recipe you know (recipes["Chest"])."""

    fields = {"name": "", "needs": "the ingredients", "can_make": "whether you have them now"}

    @action
    async def craft(self, ctx: Any, count: Any = 1) -> Any:
        """Craft it count times."""
        return await ctx.game.craft(b.text(self.ref.get("name"), "recipe"), b.count(count, 1, 999))


class Recipes(Api):
    """The crafting recipes you know: iterate them, or recipes["Chest"]."""

    live = True

    @fact
    async def each(self, ctx: Any) -> list[Recipe]:
        return [Recipe({"name": r["id"]}, name=r["name"], needs=r["needs"], can_make=r["craftable"]) for r in await ctx.game.read("player.recipes")]

    @fact
    async def get(self, ctx: Any, name: Any) -> Recipe:
        known = await self.each(ctx)
        found = next((r for r in known if b.text(name, "recipe") in (r.ref["name"], r.f["name"])), None)
        if not found:
            raise ActionError(unknown("you don't know a recipe called", name, {r.f["name"]: r.f["name"] for r in known}))
        return found
