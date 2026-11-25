"""The script API: the plain functions a script calls (World's methods), the live globals (farmer, day, backpack, here, ...) and
the records they return, each with the methods which act on it. One declaration serves the sandbox (spec), the runner (lookup),
and the system prompt (reference)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from . import items, people, places, screen, trade, where, world  # noqa: F401  (each registers its classes)
from .base import REGISTRY, Api, signature
from .world import World

HOME = {"farm", "farmhouse", "home", "house", "bed"}


def wire_names() -> dict[str, tuple[type[Api], Callable]]:
    """Every call a script can make, by the name it goes by on the wire: "go", "Chest.put", "Farmer.energy"."""
    out = {}
    for name, cls in REGISTRY.items():
        for m, fn in cls.methods().items():
            out[m if cls is World else f"{name}.{m}"] = (cls, fn)
    return out


CALLS = wire_names()


def lookup(name: Any) -> tuple[str, type[Api], Callable]:
    """A call's kind (action, fact, field, setting, judge), its class, and its implementation; TypeError if there's none."""
    if not isinstance(name, str) or name not in CALLS:
        raise TypeError(f"there's no {name!r} in the SDK")
    cls, fn = CALLS[name]
    return fn.kind, cls, fn


def doc(thing: Any) -> str:
    return " ".join((thing.__doc__ or "").split())


def spec() -> dict:
    """What the sandbox builds the script's globals from."""
    def methods(cls: type[Api]) -> dict[str, list[str]]:
        return {m: [signature(fn), doc(fn)] for m, fn in cls.methods().items() if fn.kind != "field" and m not in ("each", "get")}

    classes = {name: {"single": cls.live, "value": cls.value, "doc": doc(cls), "fields": {} if cls.live else dict(cls.fields), "live_fields": cls.live_fields(),
                      "methods": methods(cls), "each": "each" in cls.methods(), "get": "get" in cls.methods()}
               for name, cls in REGISTRY.items() if cls is not World}
    return {"functions": methods(World), "classes": classes, "globals": {cls.var: name for name, cls in REGISTRY.items() if cls.live and cls is not World}}


def reference() -> str:
    """The SDK at a glance, for the system prompt: the functions, then the live globals and the records, each with its fields and
    methods (a subclass, like Here of Place, only with what it adds) and where their docs are."""
    from .. import docs

    def line(call: str, wire: str, fn: Callable) -> str:
        found = docs.entry(wire)
        return f"- `{call}{signature(fn)}`: {doc(fn)}" + (f" (`{found}`)" if found else "")

    out = ["Functions:"] + [line(m, m, fn) for m, fn in World.methods().items()]
    for name, cls in sorted(REGISTRY.items(), key=lambda kv: not kv[1].live):
        if cls is World:
            continue
        sub = cls.__bases__[0] is not Api
        own = {m: fn for m, fn in cls.methods().items() if not sub or m in vars(cls)}
        shown = {**({} if cls.live or sub else cls.fields), **{m: doc(fn) for m, fn in own.items() if fn.kind == "field"}}
        out += ["", (f"`{cls.var}` ({name})" if cls.live else f"`{name}`") + f": {doc(cls)}"]
        if shown:
            out.append("- fields: " + ", ".join(f"{k} ({v})" if v else k for k, v in shown.items()))
        out += [line(f"{cls.var}.{m}", f"{name}.{m}", fn) for m, fn in own.items() if fn.kind != "field" and m not in ("each", "get")]
    return "\n".join(out)


def recovers(name: str, args: dict) -> bool:
    """Actions which cure low health, low energy, or the late hour, so safety lets them run."""
    target = args.get("target")
    return name in ("sleep", "soak", "Item.eat") or name == "go" and isinstance(target, str) and target.strip().lower().replace(" ", "") in HOME
