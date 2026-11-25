"""Places: any location by name (places), and the one you're in (here)."""

from __future__ import annotations

import re
from typing import Any

from .. import look
from ..common import unknown
from ..game import ActionError
from ..look import render
from ..navigation import HOURS, KEEPERS
from ..trade import counter
from . import base as b
from .base import Api, fact, field
from .people import Person
from .trade import Blacksmith, Chest, Shop
from .where import Thing, tile


def clock_text(clock: int) -> str:
    h, m = divmod(clock, 100)
    return f"{(h - 1) % 12 + 1}:{m:02d} {'am' if h % 24 < 12 else 'pm'}"


class Place(Api):
    """A location (places["SeedShop"], here, Tile.location, Person.home); a target for go."""

    fields = {"name": 'the game\'s name, like "SeedShop"', "title": 'like "Pierre\'s General Store"'}

    async def _name(self, ctx: Any) -> str:
        name = b.text(self.ref.get("name"), "place")
        if not re.fullmatch(r"[\w ]+", name):
            raise ActionError(f"no place called {name!r}")
        return name

    @field
    async def tile(self, ctx: Any) -> Any:
        """where you come in, walking from here (None when you're there)"""
        name = await self._name(ctx)
        at = await ctx.game.arrival(name)
        return at and tile(*at, name)

    @field
    async def minutes(self, ctx: Any) -> int | None:
        """about how many game minutes the walk there takes (None when you're there)"""
        return await ctx.game.minutes(await self._name(ctx))

    @field
    async def hours(self, ctx: Any) -> str | None:
        """opening hours, for the well-known shops (None otherwise)"""
        if (known := HOURS.get(await self._name(ctx))) is None:
            return None
        opens, closes, off = known
        return f"{clock_text(opens)} to {clock_text(closes)}" + (f", closed {' and '.join(off)}" if off else "")

    @field
    async def open(self, ctx: Any) -> bool | None:
        """whether it's open now, by those hours (None when they aren't known)"""
        if (known := HOURS.get(await self._name(ctx))) is None:
            return None
        opens, closes, off = known
        t = await ctx.game.read("time")
        return t["weekday"] not in off and opens <= t["clock"] < closes and not t.get("festival")

    @field
    async def keeper(self, ctx: Any) -> Person | None:
        """who keeps its shop counter, or None"""
        name = KEEPERS.get(await self._name(ctx))
        if not name:
            return None
        row = next((p for p in await ctx.game.read("player.friends") if p["name"] == name), None)
        return Person.of(row) if row else Person({"name": name}, name=name, met=False)

    @field
    async def shop(self, ctx: Any) -> Shop | None:
        """its shop counter, or None"""
        name = await self._name(ctx)
        found = next((a for a in await ctx.game.read(f"locations['{name}'].actions") or [] if counter(a["action"])), None)
        if not found:
            return None
        verb, _, rest = found["action"].partition(" ")
        shop = rest.split(" ")[0] if verb == "OpenShop" and rest else name  # "OpenShop IceCreamStand ..." names its shop
        return (Blacksmith if name == "Blacksmith" else Shop)({"place": name, "name": shop, "x": found["x"], "y": found["y"]},
                                                             name=shop, place=name, tile=tile(found["x"], found["y"], name))

    @field
    async def exits(self, ctx: Any) -> list[Thing]:
        """the ways out, kind "exit", name where they lead"""
        name = await self._name(ctx)
        seen: dict[str, Thing] = {}
        for e in await ctx.game.read(f"locations['{name}'].exits") or []:
            seen.setdefault(e["to"], Thing.at(name, "exit", e["to"], e["x"], e["y"], door=e["door"]))
        return list(seen.values())


class Places(Api):
    """Every location in the game, by its exact name: places["SeedShop"], or iterate them."""

    live = True

    @fact
    async def each(self, ctx: Any) -> list[Place]:
        return await every(ctx)

    @fact
    async def get(self, ctx: Any, name: Any) -> Place:
        return await place(ctx, name)


async def every(ctx: Any) -> list[Place]:
    return [Place({"name": r["NameOrUniqueName"]}, name=r["NameOrUniqueName"], title=r.get("DisplayName"))
            for r in await ctx.game.read({"from": "game.locations", "select": ["NameOrUniqueName", "DisplayName"]})]


async def place(ctx: Any, name: Any) -> Place:
    """The place with exactly this game name; ActionError naming the nearest ones otherwise."""
    known = await every(ctx)
    found = next((p for p in known if p.f["name"] == b.text(name, "place")), None)
    if not found:
        raise ActionError(unknown("no place is called", name, {**{p.f["name"]: str(p) for p in known}, **{p.f["title"]: str(p) for p in known if p.f["title"]}}))
    return found


class Here(Place):
    """The place you're in, read live: a Place, with what's in it."""

    live = True

    async def _name(self, ctx: Any) -> str:
        return await ctx.game.read("location.name")

    @field
    async def name(self, ctx: Any) -> str:
        return await self._name(ctx)

    @field
    async def title(self, ctx: Any) -> str:
        return await ctx.game.read("location.DisplayName")

    @field
    async def people(self, ctx: Any) -> list[Person]:
        """the people here"""
        known = {p["name"]: p for p in await ctx.game.read("player.friends")}
        return [Person.of(known[n["name"]]) if n["name"] in known else Person({"name": n["name"]}, name=n["name"], met=False)
                for n in await ctx.game.read({"from": "location.npcs", "select": ["name"]})]

    @field
    async def chests(self, ctx: Any) -> list[Chest]:
        place = await self._name(ctx)
        rows = await ctx.game.read({"from": "location.object_rows", "where": "kind == 'chest'", "select": ["x", "y", "name", "Items"]})
        return [Chest.of(place, r) for r in rows]

    @fact
    async def find(self, ctx: Any, kind: Any, name: Any = None) -> list[Thing]:
        """Things of a kind here (and with name in their name), nearest first."""
        if kind in ("chests", "people"):
            raise TypeError(f"use here.{kind}: it gives {'Chest' if kind == 'chests' else 'Person'} records, with their methods")
        if kind not in look.FINDABLE:
            raise TypeError(f"find's kind is one of {', '.join(look.FINDABLE)}; not {kind!r}")
        place = await self._name(ctx)
        rows = await look.find_rows(ctx.game, kind, None if name is None else b.text(name, "name"))
        return [Thing.at(place, kind, r["text"], r["x"], r["y"]) for r in rows[:100]]

    @fact
    async def map(self, ctx: Any, radius: Any = 14, center: Any = None) -> str:
        """The text map around you (or center), with a legend and what's notable."""
        at = None if center is None else list(b.xy(center)[1])
        return render(await look.tile_map(ctx.game, b.number(radius, "radius", 3, 45), at))

    @fact
    async def info(self, ctx: Any, where: Any) -> TileInfo:
        """What's on one tile here."""
        place, at = b.xy(where)
        found = await look.tile(ctx.game, list(at))
        names = {"object": "name", "terrain": "kind", "npc": "name", "exit": "to", "action": "action"}
        return TileInfo(x=at[0], y=at[1], tile=tile(*at, await self._name(ctx)), walkable=found["walkable"], water=bool(found["water"]), diggable=bool(found["diggable"]),
                        **{k: found[k][v] if k in found else None for k, v in names.items()})


class TileInfo(Api):
    """What's on a tile (from here.info)."""

    var = "info"
    fields = {"x": "", "y": "", "tile": "", "walkable": "", "water": "", "diggable": "can be hoed", "object": "its name", "terrain": "its kind",
              "npc": "who stands there", "exit": "where it leads", "action": "a tile action, like a counter"}
