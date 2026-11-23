"""The SDK: what each call in a planner's script does in the game.

Actions (ACTIONS) run one at a time, under the runner's safety checks. Facts (FACTS) only read: the state, and views of the game which
the planner looks through by printing them. Each raises TypeError for a call of the wrong shape, and ActionError (or Interrupted) when it
can't be done; the runner turns those into the script's exceptions.
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from . import combat, look, vision, work
from . import driver as d
from .driver import BridgeError
from .game import ActionError, Game, label
from .look import Image, describe_menu, render, until_2am

if TYPE_CHECKING:
    from .script import Context

HOMES = {"Farm", "FarmHouse"}
CLEARABLE = ("weeds", "twigs", "stones", "trees", "saplings", "grass", "dead_crops", "stumps", "boulders")
SCREEN = ("look", "click", "type", "close", "interact", "raw")
Skill = Callable[..., Awaitable[Any]]


# --- arguments ---------------------------------------------------------------------------------------------


def clock(text: str) -> int:
    """Game clock from text like '9:00 am', '11pm', '23:00' or '2300'; 1am is 2500."""
    m = re.fullmatch(r"(\d{1,2}):?(\d{2})?\s*([ap]\.?m\.?)?", text.strip().lower())
    if not m:
        raise ActionError(f"not a time: {text!r}; use e.g. '9:00 am' or minutes")
    h, mins = int(m[1]), int(m[2] or 0)
    if m[3]:
        h = h % 12 + (12 if m[3].startswith("p") else 0)
    if h < 6:
        h += 24
    return h * 100 + mins


def whole(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def ints(value: Any, n: int, what: str, shape: str) -> list[int]:
    if not (isinstance(value, (list, tuple)) and len(value) == n and all(whole(v) for v in value)):
        raise TypeError(f"{what} must be {shape}, not {value!r}")
    return list(value)


def number(value: Any, what: str, low: int, high: int) -> int:
    if not whole(value):
        raise TypeError(f"{what} must be a whole number, not {value!r}")
    if not low <= value <= high:
        raise ActionError(f"{what} must be from {low} to {high}, not {value}")
    return value


def text(value: Any, what: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{what} must be text, not {value!r}")
    return value


def box(area: Any) -> list[int]:
    return ints(area, 4, "area", "[x1, y1, x2, y2]")


def spots(area: Any = None, tiles: Any = None) -> list[tuple[int, int]]:
    """Tiles from a list of [x, y] and an inclusive area, in order, without repeats."""
    found = [tuple(ints(t, 2, "each tile", "[x, y]")) for t in tiles or []]
    if area:
        x1, y1, x2, y2 = box(area)
        x1, x2 = sorted((x1, x2))
        y1, y2 = sorted((y1, y2))
        if (x2 - x1 + 1) * (y2 - y1 + 1) > 2500:
            raise ActionError("that area is too large")
        found += [(x, y) for y in range(y1, y2 + 1) for x in range(x1, x2 + 1)]
    if not found:
        raise TypeError("give an area as [x1, y1, x2, y2] or tiles as [[x, y], ...]")
    return list(dict.fromkeys(found))


def recovers(name: str, args: dict) -> bool:
    """Actions which cure low health, low energy, or the late hour, so safety lets them run."""
    return name in ("sleep", "eat") or name == "go" and isinstance(args.get("target"), str) and args["target"] in HOMES


def named(item: Any) -> str:
    return text(item, "item")


def amount(count: Any, default: int | None = None, high: int = 9999) -> int | None:
    return default if count is None else number(count, "count", 1, high)


def clearable(kinds: Any) -> list[str]:
    kinds = list(kinds or ["weeds", "twigs", "stones"])
    if unknown := [k for k in kinds if k not in CLEARABLE]:
        raise TypeError(f"kinds can be {', '.join(CLEARABLE)}; not {unknown}")
    return kinds


# --- actions -----------------------------------------------------------------------------------------------


# Actions which only check their arguments and call a skill; each takes the game where an action takes the context.
PLAIN: dict[str, Skill] = {
    "harvest": lambda g: work.harvest(g),
    "water": lambda g, area=None: g.use_tool("watering can", spots(area)) if area else work.water(g),
    "till": lambda g, area=None, tiles=None: g.use_tool("hoe", spots(area, tiles)),
    "plant": lambda g, item, area=None, tiles=None: g.place(named(item), spots(area, tiles)),
    "place": lambda g, item, tiles: g.place(named(item), spots(tiles=tiles)),
    "clear": lambda g, area, kinds=None: work.clear(g, box(area), clearable(kinds)),
    "chop": lambda g, area=None, tiles=None: g.use_tool("axe", spots(area, tiles)),
    "break_rocks": lambda g, area=None, tiles=None: g.use_tool("pickaxe", spots(area, tiles)),
    "forage": lambda g: work.collect(g, "forage"),
    "pet": lambda g: work.pet(g),
    "machines": lambda g: work.collect(g, "machines"),
    "fish": lambda g, casts=3: work.fish(g, number(casts, "casts", 1, 20), None, None),
    "fight": lambda g: work.fight(g, 12, None),
    "rewards": lambda g: g.collect_rewards(),
    "buy": lambda g, item, count=1: g.buy(named(item), amount(count, 1)),
    "sell": lambda g, item, count=None: g.sell(named(item), amount(count)),
    "eat": lambda g, item: g.eat(named(item)),
    "use": lambda g, item: g.use(named(item)),
    "craft": lambda g, recipe, count=1: g.craft(text(recipe, "recipe"), amount(count, 1, 999)),
    "trash": lambda g, item: g.trash(named(item)),
    "put": lambda g, item: g.container("put", named(item)),
    "take": lambda g, item="all": g.container("take", named(item)),
}


def on_game(name: str, skill: Skill) -> Skill:
    """A function of the context, with the skill's signature, which calls the skill with the context's game."""
    async def call(ctx: Context, *args: Any, **kwargs: Any) -> Any:
        return await skill(ctx.game, *args, **kwargs)

    call.__name__, call.__signature__ = name, inspect.signature(skill)
    return call


async def go(ctx: Context, target: Any, beside: bool = False) -> Any:
    g = ctx.game
    if isinstance(target, (list, tuple)):
        return await g.walk_to(ints(target, 2, "target", "a place, a person, or (x, y)"), bool(beside))
    text(target, "target")
    try:
        person = None if "'" in target else await g.find_person(target)
    except BridgeError:
        person = None
    if not (person and person.get("location") and person.get("x") is not None):
        return await g.travel(target)
    internal, name = person.get("internal") or person.get("name"), person.get("name", target)

    async def where() -> list:
        return [(r["x"], r["y"]) for r in await g.read({"from": "location.npcs", "where": f"internal == '{internal}'", "select": ["x", "y"]})]

    for _ in range(4):  # they walk about, and may leave for somewhere else
        person = await g.find_person(internal) or person
        if await g.read("location.name") != person["location"]:
            await g.travel(person["location"], [(person["x"], person["y"])])
            continue
        if await g.goto(await where() or [(person["x"], person["y"])], 8, what=name, track=where) is not None:
            return f"beside {name}"
    raise ActionError(f"couldn't catch up with {name}")


async def mine(ctx: Context, floors: Any = 1) -> Any:
    tactics = combat.Tactics(ctx.decide) if ctx.decide else None
    return await work.mine_down(ctx.game, number(floors, "floors", 1, 10), 30, None, tactics=tactics)


async def ship(ctx: Context, item: Any, count: Any = None) -> Any:
    await ctx.game.ready()
    return await ctx.game.ship(named(item), amount(count))


async def hold(ctx: Context, item: Any) -> Any:
    return {"holding": label(await ctx.game.hold(named(item)))}


async def with_menu(g: Game, result: Any) -> Any:
    """The result, and the menu it left open."""
    if await g.read("state.menu"):
        result = {**(result or {}), "menu": await describe_menu(g)}
    return result


async def talk(ctx: Context, person: Any, gift: Any = None) -> Any:
    return await with_menu(ctx.game, await ctx.game.talk(text(person, "person"), None if gift is None else text(gift, "gift")))


async def answer(ctx: Context, choice: Any) -> Any:
    """Answer the open question, or choose a profession. The runner turns a described choice into its number first."""
    g = ctx.game
    if not whole(choice):
        raise TypeError(f"answer takes the choice's number or a description of it, not {choice!r}")
    if await g.read("menu.type") == "LevelUpMenu":
        result = await g.choose_profession(choice)
        ctx.finished = "woke_up" in result
        ctx.last_night = {**result, "chose": f"option {choice}"}
        return result
    await g.dialogue(choice)
    if await g.read("state.cutscene"):
        await g.play_event()
    return await with_menu(g, None)


async def skip_cutscene(ctx: Context) -> Any:
    return {"skipped_script": await ctx.game.skip_event()}


async def wait_until(ctx: Context, when: Any = None, minutes: Any = None) -> Any:
    """Until a clock time like "9:00 am", or for some minutes (also as a plain number)."""
    g = ctx.game
    if minutes is None and whole(when):
        when, minutes = None, when
    if minutes is None and when is None:
        raise TypeError("wait_until needs a time like '9:00 am' or minutes=30")
    until = None if minutes is not None else number(clock(text(when, "the time")), "the time", 600, 2600)
    if minutes is not None:
        number(minutes, "minutes", 10, 600)
    await g.ready()
    target = await g.read("time.minutes") + minutes if until is None else until // 100 * 60 + until % 100
    for _ in range(40):
        result = await g.step(6000, {"time": f"time.minutes >= {target}", "menu": "state.menu", "scene": "state.cutscene", "night": "state.night"})
        if result["fired"] == "time":
            return None
        if result["fired"]:
            await g.ready()
    return None


async def soak(ctx: Context, until: Any = None) -> Any:
    if until is not None:
        until = number(clock(until) if isinstance(until, str) else until, "until", 600, 2600)
    return await ctx.game.soak(until)


async def sleep(ctx: Context, early: Any = False) -> Any:
    g = ctx.game
    if not early and await g.read("time.minutes") < 18 * 60:
        raise ActionError(f"it's only {await g.read('time.text')}: there's most of a day left. Plan more, or sleep with early=True if nothing useful is left")
    result = await g.sleep()
    ctx.finished = "woke_up" in result
    ctx.last_night = result
    return result


async def screen(ctx: Context, action: Any = "look", target: Any = None, at: Any = None, text: Any = None, tiles: Any = None, item: Any = None, ops: Any = None, right: Any = False) -> Any:
    """The escape hatch: look at, click, type in, or close the open menu; interact with tiles; or send raw input."""
    g = ctx.game
    if action not in SCREEN:
        raise TypeError(f"screen's action is one of {', '.join(SCREEN)}, not {action!r}")
    if action == "interact":
        if not tiles:
            raise TypeError("screen('interact') needs tiles=[[x, y], ...]")
        return await with_menu(g, await g.interact(spots(tiles=tiles), item))
    if action == "raw":
        return await raw(g, ops)
    if action == "close":
        await g.close_menu(False)
        return await with_menu(g, None)
    if action == "type":
        await g.d.run(d.input(text=str(text or ""), clear=True), d.step(5))
    elif action == "click":
        if at is not None:
            if ctx.shot["cropped"]:
                raise ActionError("that needs an uncropped screenshot")
            spot = [v / ctx.shot["scale"] for v in ints(at, 2, "at", "a pixel [x, y]")]
        else:
            parts, tabs = await g.read(["menu.components", "menu.tabs"])
            wanted = str(target or "").lower()
            elevator = await g.read("menu.type") == "MineElevatorMenu"
            found = None if elevator else next((c for c in parts or [] if str(c["i"]) == wanted), None)
            found = found or next((c for c in [*(tabs or []), *(parts or [])] if wanted in (str(c.get("name", "")).lower(), str(c.get("label", "")).lower(), str(c.get("field", "")).lower())), None)
            if not found:
                raise ActionError(f"nothing called '{target}' here; use screen('look')")
            spot = found["at"]
        await g.click_ui(spot, right=bool(right), after=25)
        if not await g.read("state.menu"):
            await g.settle()
            return "clicked; the menu closed"
    return await describe_menu(g)


async def raw(g: Game, ops: Any) -> Any:
    """Low-level input, step, and read instructions, for anything the other actions can't do."""
    if not (isinstance(ops, list) and 1 <= len(ops) <= 20 and all(isinstance(op, dict) for op in ops)):
        raise TypeError("screen('raw') needs ops: 1 to 20 instructions like {'op': 'step', 'n': 30}")
    for op in ops:
        if op.get("op") not in ("input", "step", "read"):
            raise ActionError("raw only allows input, step, and read")
        if op["op"] == "step":
            op["n"] = min(int(op.get("n", 1)), 1200)
        if op["op"] == "input" and "tile" in op:
            op["cursor"] = {"tile": op.pop("tile")}
    try:
        return await g.d.run(*ops)
    finally:
        await g.d.run(d.input(buttons=[]))


ACTIONS = {name: on_game(name, skill) for name, skill in PLAIN.items()} | {f.__name__: f for f in (go, mine, ship, hold, talk, answer, skip_cutscene, wait_until, soak, sleep, screen)}


# --- facts and views ---------------------------------------------------------------------------------------


# Facts which are one read, or one of look's views.
READS: dict[str, Skill] = {
    "money": lambda g: g.read("player.money"),
    "time": lambda g: g.read("time.clock"),
    "health": lambda g: g.read("player.health"),
    "here": lambda g: g.read("location.name"),
    "quests": lambda g: g.read("player.quests"),
    "state": look.state,
    "recipes": look.recipes,
    "exits": look.exits,
}


async def energy(ctx: Context) -> int:
    return int(await ctx.game.read("player.energy"))


async def minutes_until_2am(ctx: Context) -> int:
    return until_2am(await ctx.game.read("time.clock"))


async def people_here(ctx: Context) -> list[str]:
    return [n["name"] for n in await ctx.game.read({"from": "location.npcs", "select": ["name"]})]


async def look_map(ctx: Context, radius: Any = 14, center: Any = None) -> str:
    """The text map around the farmer (or center), with a legend and what's notable."""
    center = None if center is None else ints(center, 2, "center", "(x, y)")
    return render(await look.tile_map(ctx.game, number(radius, "radius", 3, 45), center))


async def find(ctx: Context, kind: Any, name: Any = None) -> list[dict]:
    """A list, nearest first, of {"x", "y", "what"}: loop over it, slice it, or take [0]."""
    if kind not in look.FINDABLE:
        raise TypeError(f"find's kind is one of {', '.join(look.FINDABLE)}; not {kind!r}")
    rows = await look.find_rows(ctx.game, kind, None if name is None else text(name, "name"))
    return [{"x": r["x"], "y": r["y"], "what": r["text"]} for r in rows[:100]]


async def people(ctx: Context, name: Any = None) -> list[str]:
    return await look.people(ctx.game, None if name is None else text(name, "name"))


async def item_info(ctx: Context, name: Any) -> dict:
    return await look.item(ctx.game, text(name, "name"))


async def tile_info(ctx: Context, x: Any, y: Any) -> dict:
    return await look.tile(ctx.game, ints([x, y], 2, "the tile", "whole numbers x, y"))


async def screenshot(ctx: Context, radius: Any = None, grid: Any = False) -> Image:
    """The screen, labelled name (x,y). The runner passes the image on to the planner."""
    radius = None if radius is None else min(number(radius, "radius", 2, 45), 20)
    data, ctx.shot = await vision.capture(ctx.game, size="small", radius=radius, grid=bool(grid))
    return Image(data)


async def web(ctx: Context, question: Any) -> str:
    if not ctx.lookup:
        raise ActionError("web lookups are off")
    return await ctx.lookup(text(question, "question"))


FACTS = {name: on_game(name, read) for name, read in READS.items()} | {
    f.__name__: f for f in (energy, minutes_until_2am, people_here, look_map, find, people, item_info, tile_info, screenshot, web)}
