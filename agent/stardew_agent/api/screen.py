"""The escape hatch: the open menu, raw input, and the screen itself."""

from __future__ import annotations

from typing import Any

from .. import driver as d
from .. import vision
from ..game import ActionError, Game
from ..look import Image, describe_menu
from . import base
from .base import Api, action, fact, ints, number, tiles


async def with_menu(g: Game, result: Any) -> Any:
    """The result, and the menu it left open."""
    if await g.read("state.menu"):
        result = {**(result or {}), "menu": await describe_menu(g)}
    return result


class Screen(Api):
    """The open menu and the screen, for what the other calls can't do."""

    live, var = True, "screen"

    @fact
    async def look(self, ctx: Any) -> Any:
        """The open menu: its text, choices, and components."""
        return await describe_menu(ctx.game)

    @action
    async def click(self, ctx: Any, target: Any, right: Any = False) -> Any:
        """Click a menu component (its number, name or label), or a pixel (x, y) of an uncropped screen.shot()."""
        g = ctx.game
        if isinstance(target, (list, tuple)):
            if ctx.shot["cropped"]:
                raise ActionError("that needs an uncropped screenshot")
            spot = [v / ctx.shot["scale"] for v in ints(target, 2, "target", "a pixel (x, y)")]
        else:
            parts, tabs = await g.read(["menu.components", "menu.tabs"])
            wanted = str(target).lower()
            elevator = await g.read("menu.type") == "MineElevatorMenu"
            found = None if elevator else next((c for c in parts or [] if str(c["i"]) == wanted), None)
            found = found or next((c for c in [*(tabs or []), *(parts or [])] if wanted in (str(c.get("name", "")).lower(), str(c.get("label", "")).lower(), str(c.get("field", "")).lower())), None)
            if not found:
                raise ActionError(f"nothing called '{target}' here; use screen.look()")
            spot = found["at"]
        await g.click_ui(spot, right=bool(right), after=25)
        if not await g.read("state.menu"):
            await g.settle()
            return "clicked; the menu closed"
        return await describe_menu(g)

    @action
    async def type(self, ctx: Any, text: Any) -> Any:
        """Type text into the open menu's text box."""
        await ctx.game.d.run(d.input(text=base.text(text, "text"), clear=True), d.step(5))
        return await describe_menu(ctx.game)

    @action
    async def close(self, ctx: Any) -> Any:
        """Close the open menu."""
        await ctx.game.close_menu()
        return await with_menu(ctx.game, None)

    @action
    async def interact(self, ctx: Any, where: Any, item: Any = None) -> Any:
        """The action button on the first of these tiles that responds, holding item if given."""
        return await with_menu(ctx.game, await ctx.game.interact(await tiles(ctx, where), None if item is None else base.item(item)))

    @action
    async def raw(self, ctx: Any, ops: Any) -> Any:
        """Low-level input, step and read instructions, for anything else."""
        if not (isinstance(ops, list) and 1 <= len(ops) <= 20 and all(isinstance(op, dict) for op in ops)):
            raise TypeError("screen.raw needs ops: 1 to 20 instructions like {'op': 'step', 'n': 30}")
        for op in ops:
            if op.get("op") not in ("input", "step", "read"):
                raise ActionError("raw only allows input, step, and read")
            if op["op"] == "step":
                op["n"] = min(int(op.get("n", 1)), 1200)
            if op["op"] == "input" and "tile" in op:
                op["cursor"] = {"tile": op.pop("tile")}
        g = ctx.game
        try:
            return await g.d.run(*ops)
        finally:
            await g.d.run(d.input(buttons=[]))

    @fact
    async def shot(self, ctx: Any, radius: Any = None, grid: Any = False) -> Image:
        """A screenshot, labelled name (x,y), which you see when next woken."""
        radius = None if radius is None else min(number(radius, "radius", 2, 45), 20)
        data, ctx.shot = await vision.capture(ctx.game, radius=radius, grid=bool(grid))
        return Image(data)
