"""The machinery of the script API: classes whose methods are actions, facts or settings, the records which cross the wire, and the
helpers which read the arguments every method shares (where, item, count, who, until).

An API class is either live (one instance, a script global, whose fields are read from the game each time) or a record (a snapshot of
fields taken when it was returned, plus a reference, `ref`, which its methods act through). Methods are `async def m(self, ctx, ...)`.
A class may define the facts `each` (iterating it, and len) and `get` (indexing it with `[]`).
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable
from typing import Any

from ..game import ActionError

UNLIMITED = 1_000_000  # shops sell some things without limit
REGISTRY: dict[str, type[Api]] = {}


def action(fn: Callable) -> Callable:
    """Changes the world: one at a time, under the runner's safety checks."""
    fn.kind = "action"
    return fn


def fact(fn: Callable) -> Callable:
    """Only reads."""
    fn.kind = "fact"
    return fn


def field(fn: Callable) -> Callable:
    """A live class's field: a fact without arguments, read as an attribute."""
    fn.kind = "field"
    return fn


def setting(fn: Callable) -> Callable:
    """Changes how the runner acts from now on, beside the running action."""
    fn.kind = "setting"
    return fn


def judge(fn: Callable) -> Callable:
    """Answered by the runner's judge; the body isn't used."""
    fn.kind = "judge"
    return fn


class Api:
    """Base of the API classes. The class name is the script-facing one; `var` is what the reference calls an instance (a live
    class's global). A record's `fields` are its snapshot; @field methods are read live, on records too (a person's tile)."""

    live = False  # one instance, a script global
    value = False  # made by scripts too, like Tile(3, 4): the sandbox has its own code for these
    var = ""
    fields: dict[str, str] = {}

    def __init_subclass__(cls) -> None:
        REGISTRY[cls.__name__] = cls
        cls.var = vars(cls).get("var", cls.__name__.lower())

    def __init__(self, ref: dict | None = None, **fields: Any):
        self.ref, self.f = ref or {}, fields

    def wire(self) -> dict:
        return {"$": type(self).__name__, "ref": self.ref, "f": self.f}

    def __str__(self) -> str:
        return short(self.f)

    @classmethod
    def methods(cls) -> dict[str, Callable]:
        """Every action, fact, setting and field, inherited ones too."""
        found: dict[str, Callable] = {}
        for c in reversed(cls.__mro__):
            found.update({name: fn for name, fn in vars(c).items() if getattr(fn, "kind", None)})
        return found

    @classmethod
    def live_fields(cls) -> dict[str, str]:
        return {name: fn.__doc__ or "" for name, fn in cls.methods().items() if fn.kind == "field"}


def short(f: dict) -> str:
    """A record in a few words: its name, count, and tile."""
    if "x1" in f:
        return f"({f['x1']},{f['y1']})-({f['x2']},{f['y2']})" + (f" in {f['place']}" if f.get("place") else "")
    name = next((str(f[k]) for k in ("name", "title", "kind") if f.get(k) is not None), "")
    if f.get("title") and f.get("name") and f["title"] != f["name"]:
        name += f" ({f['title']})"
    count = f" x{f['count']}" if isinstance(f.get("count"), int) and f["count"] < UNLIMITED else ""
    at = f"({f['x']},{f['y']})" if f.get("x") is not None and f.get("y") is not None else ""
    return f"{name}{count}" + (f" at {at}" if name and at else at)


def record(d: dict) -> Any:
    """json object_hook: a record from the script, back as an instance of its class."""
    cls = REGISTRY.get(d.get("$")) if set(d) == {"$", "ref", "f"} else None
    if not (cls and not cls.live and isinstance(d["ref"], dict) and isinstance(d["f"], dict)):
        return d
    made = cls(d["ref"])
    made.f = d["f"]
    return made


def signature(fn: Callable) -> str:
    """As a script sees it: without self and ctx."""
    sig = inspect.signature(fn)
    params = [p.replace(annotation=inspect.Parameter.empty) for p in list(sig.parameters.values())[2:]]
    return str(sig.replace(parameters=params, return_annotation=inspect.Signature.empty))


# --- the argument vocabulary -------------------------------------------------------------------------------


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


WHERE = "a Tile or (x, y), an Area, or a list of those or of things with a .tile"


def xy(value: Any) -> tuple[str | None, tuple[int, int]]:
    """One tile as (place or None, (x, y)), from a Tile, an (x, y) pair, or a record with a snapshot .tile."""
    if isinstance(value, Api) and "tile" in value.f:
        value = value.f["tile"]
    if isinstance(value, REGISTRY["Tile"]):
        return value.f.get("place"), tuple(ints([value.f.get("x"), value.f.get("y")], 2, "a Tile", "Tile(x, y)"))
    return None, tuple(ints(value, 2, "each tile", WHERE))


async def tiles(ctx: Any, where: Any) -> list[tuple[int, int]]:
    """`where` as tiles in the current location, in order, without repeats; resolved once, as the call starts."""
    if isinstance(where, Api) and type(where).__name__ in ("Area", "Person"):
        where = [where]
    if isinstance(where, (list, tuple)) and len(where) == 2 and all(whole(v) for v in where):  # (x, y): a tuple arrives as a list
        where = [where]
    elif isinstance(where, REGISTRY["Tile"]) or isinstance(where, Api) and "tile" in where.f:
        where = [where]
    if not (isinstance(where, (list, set)) and where):
        raise TypeError(f"where must be {WHERE}, not {where!r}")
    here = await ctx.game.read("location.name")
    found: list[tuple[int, int]] = []
    for one in where:
        if isinstance(one, REGISTRY["Area"]):
            x1, y1, x2, y2 = ints([one.f.get(k) for k in ("x1", "y1", "x2", "y2")], 4, "an Area", "Area(x1, y1, x2, y2)")
            x1, x2 = sorted((x1, x2))
            y1, y2 = sorted((y1, y2))
            if (x2 - x1 + 1) * (y2 - y1 + 1) > 2500:
                raise ActionError("that area is too large")
            place, more = one.f.get("place"), [(x, y) for y in range(y1, y2 + 1) for x in range(x1, x2 + 1)]
        elif isinstance(one, REGISTRY["Person"]):
            place, at = xy(await one.tile(ctx))
            more = [at]
        else:
            place, at = xy(one)
            more = [at]
        if place not in (None, here):
            raise ActionError(f"those tiles are in {place}, and you're in {here}: go there first")
        found += more
    return list(dict.fromkeys(found))


def item(value: Any) -> str:
    """An item's name, from text or an Item."""
    if isinstance(value, REGISTRY["Item"]):
        return text(value.ref.get("name"), "item")
    return text(value, "item")


def count(value: Any, default: int | None = None, high: int = 9999) -> int | None:
    """How many; None is the default (all you have, for what you give away)."""
    if value is None:
        return default
    if default is None and not whole(value):
        raise TypeError(f"count must be a whole number, not {value!r}: leave it out for all of them")
    return number(value, "count", 1, high)


def who(value: Any) -> str:
    """A person's name, from text or a Person."""
    if isinstance(value, REGISTRY["Person"]):
        return text(value.ref.get("name"), "who")
    return text(value, "who")


def until(value: Any) -> int:
    """A game clock (1350, 1am is 2500) from text like '9:00 am', '9am', '21:30', or from such a number."""
    if whole(value):
        return number(value, "until", 600, 2600)
    m = re.fullmatch(r"(\d{1,2}):?(\d{2})?\s*([ap]\.?m\.?)?", text(value, "until").strip().lower())
    if not m:
        raise ActionError(f"not a time: {value!r}; use e.g. '9:00 am'")
    h, mins = int(m[1]), int(m[2] or 0)
    if m[3]:
        h = h % 12 + (12 if m[3].startswith("p") else 0)
    if h < 6:
        h += 24
    return number(h * 100 + mins, "until", 600, 2600)
