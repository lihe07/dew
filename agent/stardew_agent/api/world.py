"""The farmer acting in the world (a script's plain functions), the farmer's state, the day, tiles and areas, and the mines."""

from __future__ import annotations

from typing import Any

from .. import combat, look, work
from ..game import ActionError
from ..look import luck_text, until_2am
from ..navigation import TILES_PER_MINUTE
from . import base as b
from .base import Api, action, field, judge, setting
from .items import Item
from .people import Person, talk
from .places import Place, place
from .screen import with_menu
from .where import Area, Thing, Tile, tile


class World(Api):
    """What the farmer does in the world: a script calls these as plain functions."""

    live, var = True, ""

    @action
    async def go(self, ctx: Any, target: Any) -> Any:
        """Walk to a place (its exact game name, like "SeedShop", or a Place), a Person (following them), a tile or area, or the
        nearest of a list of those; it stops beside a tile it can't stand on."""
        g = ctx.game
        if isinstance(target, list) and any(isinstance(t, (str, Place, Person)) for t in target):
            costs = [(await minutes(ctx, t), i) for i, t in enumerate(target)]
            target = target[min(costs)[1]]
        if isinstance(target, str):
            target = await place(ctx, target)
        if isinstance(target, Place):
            return (await g.go(await target._name(ctx))).note
        if isinstance(target, Person):
            return f"beside {(await g.go(b.who(target), beside=True)).person['name']}"
        if isinstance(target, Tile) and target.f.get("place") not in (None, await g.read("location.name")):
            return (await g.go((b.text(target.f["place"], "place"), b.xy(target)[1]))).note
        found = await b.tiles(ctx, target)
        return (await g.go(found[0] if len(found) == 1 else found)).note

    @action
    async def wait(self, ctx: Any, until: Any = None, minutes: Any = None) -> Any:
        """Wait until a clock time like "9:00 am", or for some game minutes."""
        g = ctx.game
        if (until is None) == (minutes is None):
            raise TypeError("wait takes until (a time like '9:00 am') or minutes=30")
        if minutes is not None:
            target = await g.read("time.minutes") + b.number(minutes, "minutes", 10, 600)
        else:
            clock = b.until(until)
            target = clock // 100 * 60 + clock % 100
        await g.ready()
        for _ in range(40):
            result = await g.step(6000, {"time": f"time.minutes >= {target}", "menu": "state.menu", "scene": "state.cutscene", "night": "state.night"})
            if result["fired"] == "time":
                return None
            if result["fired"]:
                await g.ready()
        return None

    @action
    async def sleep(self, ctx: Any) -> Any:
        """Go home to bed: the day ends."""
        result = await ctx.game.sleep()
        ctx.finished = True
        return result

    @action
    async def soak(self, ctx: Any, until: Any = None) -> Any:
        """Soak in the spa's pool until energy is full, or until a clock time."""
        return await ctx.game.soak(None if until is None else b.until(until))

    @action
    async def gather(self, ctx: Any, what: Any, where: Any = None, tool: Any = None) -> Any:
        """Harvest, forage, chop, break, cut or dig every target: a kind ("rocks"), a list of kinds, or tiles; only within the area
        where spans, if given."""
        names = [what] if isinstance(what, str) else what
        if isinstance(names, (list, tuple)) and names and all(isinstance(n, str) for n in names):
            if unknown := [n for n in names if n not in work.KINDS]:
                raise TypeError(f"gather takes kinds from {', '.join(work.KINDS)}; not {', '.join(map(repr, unknown))}")
            targets: Any = work.kinds(names)
        else:
            targets = await b.tiles(ctx, what)
        box = None
        if where is not None:
            spots = await b.tiles(ctx, where)
            box = [min(x for x, _ in spots), min(y for _, y in spots), max(x for x, _ in spots), max(y for _, y in spots)]
        return await work.gather(ctx.game, targets, box, None if tool is None else b.text(tool, "tool"))

    @action
    async def water(self, ctx: Any, where: Any = None) -> Any:
        """Water the thirsty crops here, or only those in where; refills the can on the way."""
        return await work.water(ctx.game, None if where is None else set(await b.tiles(ctx, where)))

    @action
    async def till(self, ctx: Any, where: Any) -> Any:
        """Hoe the bare ground in where."""
        return await work.till(ctx.game, set(await b.tiles(ctx, where)))

    @action
    async def plant(self, ctx: Any, item: Any, where: Any) -> Any:
        """Plant seeds (or spread fertilizer) on the tilled, empty soil in where."""
        return await work.plant(ctx.game, b.item(item), set(await b.tiles(ctx, where)))

    @action
    async def place(self, ctx: Any, item: Any, where: Any) -> Any:
        """Put down a placeable item (a chest, a machine, a path...) on each of these tiles."""
        return await ctx.game.place(b.item(item), await b.tiles(ctx, where))

    @action
    async def fish(self, ctx: Any, casts: Any = 3, where: Any = None) -> Any:
        """Cast this many times from the nearest good spot, or toward where (a water tile)."""
        at = None
        if where is not None:
            spots = await b.tiles(ctx, where)
            if len(spots) != 1:
                raise TypeError("fish's where is one water tile")
            at = spots[0]
        return await work.fish(ctx.game, b.number(casts, "casts", 1, 20), at)

    @action
    async def fight(self, ctx: Any) -> Any:
        """Clear the monsters nearby, then pick up their loot."""
        return await work.fight(ctx.game)

    @action
    async def pet(self, ctx: Any) -> Any:
        """Pet every animal here that hasn't been petted today."""
        return await work.pet(ctx.game)

    @action
    async def talk(self, ctx: Any, who: Any, gift: Any = None) -> Any:
        """Talk to someone (following them), or to each of a list or set of people, nearest first; gift is an item for all, or {who: item}."""
        return await talk(ctx, who, gift)

    @action
    async def answer(self, ctx: Any, choice: Any) -> Any:
        """Answer the open question (or choose a profession) by number, or by a description of the choice."""
        g = ctx.game
        if not b.whole(choice):
            raise TypeError(f"answer takes the choice's number or a description of it, not {choice!r}")
        if await g.read("menu.type") == "LevelUpMenu":
            result = await g.choose_profession(choice)
            ctx.finished = "woke_up" in result
            ctx.last_night = {**result, "chose": f"option {choice}"}
            return result
        await g.dialogue(choice)
        if await g.read("state.cutscene"):
            await g.pass_event()
        return await with_menu(g, None)

    @setting
    async def guard(self, ctx: Any, fight: Any = "near", flee_below: Any = 0.3, avoid: Any = ()) -> Any:
        """How the combat reflex treats monsters during every action from now on in this script."""
        if fight not in combat.MODES:
            raise TypeError(f"guard's fight is one of {', '.join(combat.MODES)}, not {fight!r}")
        if not (isinstance(flee_below, (int, float)) and not isinstance(flee_below, bool) and 0 <= flee_below <= 0.9):
            raise TypeError(f"flee_below is a fraction of max health from 0 to 0.9, not {flee_below!r}")
        names = [avoid] if isinstance(avoid, str) else list(avoid)
        old = ctx.game.guard
        ctx.game.guard = combat.Guard(fight, float(flee_below), frozenset(b.text(n, "avoid") for n in names), tactics=old.tactics)
        return f"guard: {fight}, flee below {round(100 * flee_below)}% health" + (f", never fighting {', '.join(names)}" if names else "")

    @judge
    async def check(self, ctx: Any, situation: Any) -> Any:
        """Whether a situation holds now, as the judge sees it; raises Unsure."""

    @judge
    async def pick(self, ctx: Any, description: Any, options: Any) -> Any:
        """The option which best fits a description, as the judge sees it; raises Unsure."""


async def minutes(ctx: Any, target: Any) -> float:
    """About how far a target of go is, in game minutes, for choosing the nearest."""
    g = ctx.game
    if isinstance(target, (str, Place)):
        name = await (await place(ctx, target) if isinstance(target, str) else target)._name(ctx)
        if name == await g.read("location.name"):
            return 0
        found = await g.minutes(name)
        return float("inf") if found is None else found
    if isinstance(target, Person):
        there, at = b.xy(await target.tile(ctx))
    elif isinstance(target, Area):
        f = target.f
        there, at = f.get("place"), ((f["x1"] + f["x2"]) // 2, (f["y1"] + f["y2"]) // 2)
    else:
        there, at = b.xy(target)
    here, me = await g.read(["location.name", "player.tile"])
    if there in (None, here):
        return (abs(at[0] - me[0]) + abs(at[1] - me[1])) / TILES_PER_MINUTE
    found = await g.minutes(there)
    return float("inf") if found is None else found


class Farmer(Api):
    """You, read live."""

    live = True

    @field
    async def energy(self, ctx: Any) -> int:
        return int(await ctx.game.read("player.energy"))

    @field
    async def max_energy(self, ctx: Any) -> int:
        return await ctx.game.read("player.max_energy")

    @field
    async def health(self, ctx: Any) -> int:
        return await ctx.game.read("player.health")

    @field
    async def max_health(self, ctx: Any) -> int:
        return await ctx.game.read("player.max_health")

    @field
    async def money(self, ctx: Any) -> int:
        return await ctx.game.read("player.money")

    @field
    async def place(self, ctx: Any) -> str:
        """the location's name"""
        return await ctx.game.read("location.name")

    @field
    async def tile(self, ctx: Any) -> Tile:
        """a Tile"""
        at, place = await ctx.game.read(["player.tile", "location.name"])
        return tile(*at, place)

    @field
    async def held(self, ctx: Any) -> Item | None:
        """an Item, or None"""
        row = await ctx.game.read("player.held")
        return Item.of(row) if row else None

    @field
    async def skills(self, ctx: Any) -> dict:
        """levels by skill"""
        return await ctx.game.read("player.skills")


class Day(Api):
    """Today, read live."""

    live = True

    @field
    async def time(self, ctx: Any) -> int:
        """the clock, like 1350 (1am is 2500)"""
        return await ctx.game.read("time.clock")

    @field
    async def minutes_left(self, ctx: Any) -> int:
        """game minutes until 2am"""
        return until_2am(await ctx.game.read("time.clock"))

    @field
    async def date(self, ctx: Any) -> str:
        t = await ctx.game.read("time")
        return f"{t['weekday']} {t['season']} {t['day']}, year {t['year']}"

    @field
    async def weekday(self, ctx: Any) -> str:
        """like "Mon\""""
        return await ctx.game.read("time.weekday")

    @field
    async def season(self, ctx: Any) -> str:
        return await ctx.game.read("time.season")

    @field
    async def day(self, ctx: Any) -> int:
        """of the season, 1 to 28"""
        return await ctx.game.read("time.day")

    @field
    async def year(self, ctx: Any) -> int:
        return await ctx.game.read("time.year")

    @field
    async def weather(self, ctx: Any) -> str:
        return await ctx.game.read("time.weather")

    @field
    async def tomorrow(self, ctx: Any) -> str:
        """tomorrow's weather"""
        return await ctx.game.read("time.tomorrow")

    @field
    async def luck(self, ctx: Any) -> str:
        """"very bad" to "very good\""""
        return luck_text(await ctx.game.read("player.DailyLuck"))

    @field
    async def festival(self, ctx: Any) -> Any:
        """whether there's a festival today"""
        return bool(await ctx.game.read("time.festival"))

    @field
    async def birthday(self, ctx: Any) -> str | None:
        """whose birthday it is, or None"""
        return await ctx.game.read("time.birthday")


SKULL = 120  # Skull Cavern floors are mine levels past this


class Mine(Api):
    """The Mines and the Skull Cavern, read live; the lists are what's on this floor, nearest first, as Things."""

    live = True

    async def _floor(self, ctx: Any, key: str) -> Any:
        found, place = await look.floor(ctx.game), await ctx.game.read("location.name")
        kinds = {"ladders": "ladder", "shafts": "shaft", "monsters": "monster", "dig_spots": "dig_spot", "chests": "chest", "items": "item"}
        if key == "exit":
            return found["exit"] and Thing.at(place, "exit", "ladder up", found["exit"]["x"], found["exit"]["y"])
        return [Thing.at(place, r.pop("kind", kinds.get(key)), r.pop("what", None) or r.pop("name", None), r.pop("x"), r.pop("y"), **r) for r in found[key]]

    @field
    async def cave(self, ctx: Any) -> str | None:
        """"mines", "skull", or None outside them"""
        level = await ctx.game.read("location.mine_level")
        return None if level is None else "skull" if level > SKULL else "mines"

    @field
    async def level(self, ctx: Any) -> int | None:
        """the floor here (Skull Cavern floors count from 1 too), or None"""
        level = await ctx.game.read("location.mine_level")
        return None if level is None else level - SKULL if level > SKULL else level

    @field
    async def deepest(self, ctx: Any) -> int:
        """the deepest floor of the Mines reached"""
        return min(SKULL, await ctx.game.read("player.deepestMineLevel") or 0)

    @field
    async def deepest_skull(self, ctx: Any) -> int:
        """the deepest Skull Cavern floor reached"""
        return max(0, (await ctx.game.read("player.deepestMineLevel") or 0) - SKULL)

    @field
    async def ladders(self, ctx: Any) -> list[Thing]:
        return await self._floor(ctx, "ladders")

    @field
    async def shafts(self, ctx: Any) -> list[Thing]:
        """holes down several floors"""
        return await self._floor(ctx, "shafts")

    @field
    async def rocks(self, ctx: Any) -> list[Thing]:
        """kind stone, ore or gems"""
        return await self._floor(ctx, "rocks")

    @field
    async def monsters(self, ctx: Any) -> list[Thing]:
        return await self._floor(ctx, "monsters")

    @field
    async def dig_spots(self, ctx: Any) -> list[Thing]:
        return await self._floor(ctx, "dig_spots")

    @field
    async def chests(self, ctx: Any) -> list[Thing]:
        return await self._floor(ctx, "chests")

    @field
    async def items(self, ctx: Any) -> list[Thing]:
        """dropped on the ground"""
        return await self._floor(ctx, "items")

    @field
    async def exit(self, ctx: Any) -> Thing | None:
        """the ladder up"""
        return await self._floor(ctx, "exit")

    @action
    async def descend(self, ctx: Any, to: Any = None) -> Any:
        """Down a floor by the nearest ladder (or a shaft); from outside, into the Mines by the elevator, to its deepest stop or to (a multiple of 5)."""
        if to is not None and (await ctx.game.read("location.mine_level") or 0) > SKULL:
            raise ActionError("the elevator is only in the Mines")
        return await work.descend(ctx.game, None if to is None else b.number(to, "to", 5, SKULL))

