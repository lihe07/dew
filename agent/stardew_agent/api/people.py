"""Villagers and quests."""

from __future__ import annotations

from typing import Any

from ..common import unknown
from ..game import ActionError
from ..navigation import HOMES
from .base import Api, action, fact, field, item, who
from .screen import with_menu
from .where import Tile, tile


class Person(Api):
    """Someone on the friendship list (people, here.people). Usable as `who`."""

    fields = {"name": "", "hearts": "None if not met", "talked_today": "", "gifts_this_week": "of 2", "birthday": "like 'spring 4'",
              "met": ""}

    @classmethod
    def of(cls, row: dict) -> Person:
        return cls({"name": row["name"]}, name=row["name"], hearts=row.get("hearts"), talked_today=row.get("talked"), gifts_this_week=row.get("gifts"),
                   birthday=row.get("birthday"), met=row.get("hearts") is not None)

    @field
    async def home(self, ctx: Any) -> Any:
        """the Place they live in, or None"""
        from .places import place
        home = HOMES.get(str(self.ref.get("name")).lower())
        return home and await place(ctx, home)

    @field
    async def tile(self, ctx: Any) -> Tile | None:
        """where they are now, as a Tile (None when away today)"""
        found = next((p for p in await ctx.game.read("player.friends") if p["name"] == self.ref.get("name")), None)
        if found and found.get("location"):
            return tile(found["x"], found["y"], found["location"])
        here = next((n for n in await ctx.game.read({"from": "location.npcs", "select": ["name", "x", "y"]}) if n["name"] == self.ref.get("name")), None)
        return here and tile(here["x"], here["y"], await ctx.game.read("location.name"))

    @field
    async def x(self, ctx: Any) -> int | None:
        """now, like tile.x"""
        return (t := await self.tile(ctx)) and t.f["x"]

    @field
    async def y(self, ctx: Any) -> int | None:
        """now, like tile.y"""
        return (t := await self.tile(ctx)) and t.f["y"]

    @field
    async def place(self, ctx: Any) -> str | None:
        """the name of the place they're in now (None when away today)"""
        return (t := await self.tile(ctx)) and t.f["place"]

    @action
    async def talk(self, ctx: Any, gift: Any = None) -> Any:
        """Walk to them and talk, giving gift (an item) if given."""
        return await talk(ctx, self, gift)


async def talk(ctx: Any, people: Any, gift: Any = None) -> Any:
    """One person, or several (a list or set: visited nearest first, each found again after every talk). gift is one item for
    everyone, or {who: item}."""
    if not isinstance(people, (list, tuple, set)):
        return await with_menu(ctx.game, await ctx.game.talk(who(people), None if gift is None else item(gift)))
    names = [who(p) for p in people]
    if not names:
        raise TypeError("talk needs someone to talk to")
    if isinstance(gift, dict):
        gifts = {who(k): item(v) for k, v in gift.items()}
    else:
        gifts = {} if gift is None else dict.fromkeys(names, item(gift))
    return await ctx.game.talk_all(names, gifts)


async def friends(ctx: Any) -> list[Person]:
    return [Person.of(p) for p in await ctx.game.read("player.friends")]


class People(Api):
    """Everyone on the friendship list: iterate it, or `people["Abigail"]`."""

    live, var = True, "people"

    @fact
    async def each(self, ctx: Any) -> list[Person]:
        return await friends(ctx)

    @fact
    async def get(self, ctx: Any, name: Any) -> Person:
        everyone = await friends(ctx)
        found = next((p for p in everyone if p.f["name"] == who(name)), None)
        if not found:
            raise ActionError(unknown("no one on the friendship list is called", name, {p.f["name"]: p.f["name"] for p in everyone}))
        return found


class Quest(Api):
    """A quest in the journal (from quests)."""

    fields = {"title": "", "description": "", "objectives": "", "complete": "", "reward": "money, if any", "days_left": "if timed", "special": "a special order"}


class Quests(Api):
    """The journal's quests and special orders: iterate it."""

    live, var = True, "quests"

    @fact
    async def each(self, ctx: Any) -> list[Quest]:
        return [Quest(**{k: q.get(k) for k in Quest.fields}) for q in await ctx.game.read("player.quests")]

    @action
    async def collect(self, ctx: Any) -> Any:
        """Take the money rewards of completed quests, through the journal."""
        return await ctx.game.collect_rewards()

