"""Skills: reusable moves built from the low-level instructions. Nothing here talks to a language model.

Game is assembled from mixins, one per area; this module holds the core (reading, waiting, input) and re-exports the public names.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from . import driver as d
from .common import (
    FREE,
    ActionError,
    Interrupted,
    Tile,
    beside,
    crowded,
    label,
    quality,
)
from .driver import BridgeError, Driver
from .events import Events, clean_dialogue, script_line, split_command
from .items import Items
from .menus import Menus
from .moving import SEGMENT, Moving
from .people import People
from .places import PLACES, WORKPLACES
from .rest import Rest
from .title import Title
from .trade import Trade

if TYPE_CHECKING:
    from . import combat

__all__ = ["FREE", "PLACES", "SEGMENT", "WORKPLACES", "ActionError", "Game", "Interrupted", "Tile", "beside", "clean_dialogue", "crowded", "label", "quality", "script_line", "split_command"]


class Game(Moving, Items, Menus, Trade, Events, People, Rest, Title):
    def __init__(self, driver: Driver):
        self.d = driver
        self.journal: list[dict] = []
        self.min_health = 0.3
        self.why: str | None = None  # why targets were last unreachable
        self.keys: dict[str, str] = {}
        self.hotbar: list[str] = []
        self.orders: combat.Orders | None = None  # limits for fights on the way
        driver.on_journal.append(self.journal.append)

    async def setup(self) -> None:
        """Learn the player's key bindings."""
        names = {"menu": "menuButton", "row": "toolbarSwap", "journal": "journalButton", "up": "moveUpButton", "down": "moveDownButton", "left": "moveLeftButton", "right": "moveRightButton"}
        values = await self.d.read([f"options.{v}[0].key" for v in names.values()] + [f"options.inventorySlot{i}[0].key" for i in range(1, 13)])
        self.keys = dict(zip(names, values[: len(names)]))
        self.hotbar = values[len(names):]

    def drain_journal(self) -> list[dict]:
        entries, self.journal[:] = list(self.journal), []
        return entries

    async def read(self, what: Any) -> Any:
        return await self.d.read(what)

    async def here(self) -> Tile:
        return tuple(await self.read("player.tile"))

    async def release(self) -> None:
        """Let go of every key and button, e.g. after an action was cancelled."""
        try:
            await self.d.one(d.input(buttons=[]))
        except Exception:
            pass

    async def step(self, n: int, until: dict[str, str] | str | None = None) -> dict:
        return await self.d.one(d.step(n, until))

    async def settle(self, n: int = 400) -> None:
        """Wait until the player can act, or something needs a decision."""
        await self.step(n, {"free": FREE, "menu": "state.menu and not state.fading", "scene": "state.cutscene", "minigame": "state.minigame"})

    async def ready(self) -> None:
        """Get to a state where the farmer can act, playing through anything which needs no decision."""
        for _ in range(12):
            state = await self.read("state")
            if state.get("free"):
                return
            if not state.get("world"):
                raise ActionError("no save is loaded")
            if state.get("passed_out") and not state.get("menu"):
                await self.step(300, {"menu": "state.menu", "free": FREE})
                continue
            if state.get("menu"):
                if not await self.auto_close(leftovers=True):
                    menu = await self.read("menu.type")
                    raise Interrupted(f"a menu needs your attention ({menu})")
            elif state.get("cutscene"):
                await self.play_event()
            elif state.get("minigame"):
                raise Interrupted(f"a minigame is running ({await self.read('minigame.type')})")
            elif not await self.reel_in():
                await self.settle()
        raise Interrupted("the farmer can't act right now")

    async def reel_in(self) -> bool:
        """If a cast was left in the water (say, a fishing step was stopped), bring the line in. Returns whether there was one."""
        rod = "player.CurrentTool"
        found = False
        for _ in range(12):
            r = await self.read({"rod": "player.held != null and player.held.kind == 'FishingRod'", "bar": "menu != null and menu.type == 'BobberBar'"})
            if r["rod"] and not r["bar"]:
                r.update(await self.read({"line": f"{rod}.isFishing or {rod}.isNibbling or {rod}.isCasting or {rod}.isTimingCast", "caught": f"{rod}.fishCaught"}))
            if r["bar"]:
                try:
                    await self.d.one({"op": "cheat", "what": "catch_fish"})
                except BridgeError:
                    pass  # it closed meanwhile
            elif r["rod"] and (r.get("caught") or r.get("line")):
                await self.d.run(d.input(buttons=["mouse_left"]), d.step(2), d.input(buttons=[]))
            else:
                return found
            found = True
            await self.step(40, {"free": FREE, "bar": "menu != null and menu.type == 'BobberBar'"})
        return found

    async def auto_close(self, leftovers: bool = False) -> bool:
        """Advance plain dialogue. Returns false if the open menu needs a decision.

        With leftovers (before a new action), also close what an earlier action left open: a chest, a shop, a fishing minigame.
        Not otherwise, since an action may have just opened one on purpose."""
        menu = await self.read({"type": "menu.type", "choices": "menu.choices", "root": "menu.root", "held": "menu.held"})
        if menu["root"] == "GameMenu" and not menu["held"]:  # the pause menu; nothing is lost by closing it
            await self.press("Escape", after=15)
            return await self.read("menu == null")
        if leftovers and menu["type"] == "ItemGrabMenu" and not menu["held"]:
            grab = await self.read({"source": "menu.raw.source", "bin": "menu.raw.shippingBin"})
            if grab["source"] == 3:  # a fishing treasure: keep what's in it
                await self.container("take")
            if grab["bin"] or grab["source"] in (1, 3):  # a chest or the bin: closing loses nothing
                await self.close_menu()
                return await self.read("menu == null")
        if leftovers and menu["type"] == "BobberBar":  # a fishing minigame left over from a stopped step: land the fish
            await self.reel_in()
            return await self.read("menu == null or menu.type != 'BobberBar'")
        if leftovers and menu["type"] == "ShopMenu" and not menu["held"]:  # a shop left open; leaving it loses nothing
            await self.close_menu()
            return await self.read("menu == null")
        if menu["type"] == "ItemListMenu":  # what was lost after passing out
            await self.click_component(field="okButton", after=30)
            return await self.read("menu == null or menu.type != 'ItemListMenu'")
        if menu["type"] == "DialogueBox" and not menu["choices"]:
            await self.dialogue()
            return await self.read("menu == null or menu.type == 'DialogueBox' and menu.choices == null")
        return False

    async def press(self, key: str, hold: int = 2, after: int = 3) -> None:
        await self.d.run(d.input(buttons=[key]), d.step(hold), d.input(buttons=[]), d.step(after))

    async def click_ui(self, at: Iterable[float], *, right: bool = False, keys: Iterable[str] = (), after: int = 4) -> None:
        button = "mouse_right" if right else "mouse_left"
        keys = list(keys)
        await self.d.run(
            d.input(ui=tuple(at), buttons=keys), d.step(2), d.input(buttons=[*keys, button]), d.step(2), d.input(buttons=keys), d.step(1),
            d.input(buttons=[]), d.step(after),
        )

    async def click_tile(self, tile: Iterable[int], *, right: bool = False, after: int = 3) -> None:
        button = "mouse_right" if right else "mouse_left"
        await self.d.run(d.input(tile=tuple(tile), buttons=[]), d.step(2), d.input(buttons=[button]), d.step(2), d.input(buttons=[]), d.step(after))

    def unreached(self, failed: list[Tile], limit: int = 20) -> dict:
        """What to add to a result when for_nearest couldn't get to some targets."""
        return {"unreachable": failed[:limit], "why": self.why} if failed else {}
