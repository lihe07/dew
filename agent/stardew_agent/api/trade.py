"""Shops and chests."""

from __future__ import annotations

from typing import Any

from ..game import ActionError
from . import base as b
from .base import Api, action
from .items import Item
from .where import tile


class Shop(Api):
    """A shop counter (here.shop, or a Place's shop); its calls work only in its place, so go there first."""

    fields = {"name": "which shop, like SeedShop or IceCreamStand", "place": "the place it's in", "tile": "its counter"}

    async def _open(self, ctx: Any) -> tuple[int, int]:
        """Its counter's tile, once we're in its place."""
        place, here = self.ref.get("place"), await ctx.game.read("location.name")
        if place != here:
            raise ActionError(f"{self.ref.get('name')} is in {place}, and you're in {here}: go there first")
        return (self.ref["x"], self.ref["y"])

    @action
    async def stock(self, ctx: Any) -> Any:
        """What it sells, as Items with the shop's price, and count its stock."""
        await ctx.game.open_shop(await self._open(ctx))
        return [Item.of(s) for s in await ctx.game.shop_stock()]

    @action
    async def buy(self, ctx: Any, item: Any, count: Any = 1) -> Any:
        """Buy count of something it sells."""
        return await ctx.game.buy(b.item(item), b.count(count, 1), await self._open(ctx))

    @action
    async def sell(self, ctx: Any, item: Any, count: Any = None) -> Any:
        """Sell it the whole stack, or count of it."""
        return await ctx.game.sell(b.item(item), b.count(count), await self._open(ctx))


class Blacksmith(Shop):
    """Clint's counter: a Shop, which also cracks geodes."""

    @action
    async def crack_geodes(self, ctx: Any, item: Any = "all", count: Any = None) -> Any:
        """Open geodes on the anvil, 25g each: every one you carry, or one kind, or count of it."""
        return await ctx.game.crack_geodes(b.item(item), b.count(count), await self._open(ctx))


class Chest(Api):
    """A chest (here.chests); usable wherever a tile goes."""

    fields = {"name": "", "x": "", "y": "", "tile": "", "items": "what was in it, as Items"}

    @classmethod
    def of(cls, place: str, row: dict) -> Chest:
        return cls({"place": place, "x": row["x"], "y": row["y"]}, name=row["name"], x=row["x"], y=row["y"], tile=tile(row["x"], row["y"], place),
                   items=[Item.of(i) for i in row.get("Items") or [] if i])

    async def _open(self, ctx: Any) -> None:
        at = b.ints([self.ref.get("x"), self.ref.get("y")], 2, "a chest's tile", "(x, y)")
        await ctx.game.open_chest(b.text(self.ref.get("place"), "place"), tuple(at))

    @action
    async def put(self, ctx: Any, item: Any, count: Any = None) -> Any:
        """Put a stack (or count of it) in it; "all" puts everything but tools."""
        await self._open(ctx)
        return await ctx.game.container("put", b.item(item), b.count(count))

    @action
    async def take(self, ctx: Any, item: Any = "all", count: Any = None) -> Any:
        """Take a stack (or count of it) out of it, or everything."""
        await self._open(ctx)
        return await ctx.game.container("take", b.item(item), b.count(count))
