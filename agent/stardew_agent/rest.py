"""Soaking, sleeping, and the end-of-night screens."""

from __future__ import annotations

from .common import ActionError, Interrupted

POOL_WATER = [(15, 15), (10, 14), (18, 16), (12, 13)]  # inside the spa's pool; stepping in starts swimming


class Rest:
    """Mixin for Game: restoring energy and passing the night."""

    async def soak(self, until: int | None = None) -> dict:
        """Get into the spa's pool and stay until energy is full (or the clock reaches until, like 1600)."""
        await self.ready()
        if await self.read("location.name") != "BathHouse_Pool":
            await self.go("BathHouse_Pool")
        if not await self.read("player.swimming"):
            for tile in POOL_WATER:
                try:
                    await self.go(tile)
                except ActionError:
                    continue
                if await self.read("player.swimming"):
                    break
            else:
                raise ActionError("couldn't get into the pool's water")
        before = await self.read({"energy": "player.energy", "clock": "time.clock"})
        stop = {"full": "player.energy >= player.max_energy", "out": "not player.swimming", "menu": "state.menu", "night": "state.night"}
        if until:
            stop["time"] = f"time.clock >= {until}"
        for _ in range(40):
            fired = (await self.step(6000, stop))["fired"]
            if fired in ("full", "time", "night"):
                break
            if fired == "out":
                raise ActionError("left the water before energy was full")
            if fired == "menu":
                await self.ready()
        after = await self.read({"energy": "player.energy", "max": "player.max_energy", "clock": "time.clock"})
        return {"energy": f"{int(before['energy'])} -> {int(after['energy'])}/{after['max']}", "from": before["clock"], "to": after["clock"]}

    async def sleep(self) -> dict:
        """Go to bed. The night's screens are left for the next day (finish_night)."""
        if not await self.read("state.night"):
            await self.ready()
            home, bed = await self.read(["player.home", "player.bed_tile"])
            await self.go(home)
            if not bed:
                raise ActionError("there's no bed at home")
            for _ in range(3):
                if await self.here() == tuple(bed):
                    # the prompt only appears when stepping onto the bed
                    await self.go([(bed[0] - 2, bed[1]), (bed[0] - 2, bed[1] + 1), (bed[0] - 3, bed[1])], what="beside the bed")
                try:
                    await self.go([bed], what="the bed")
                except Interrupted:
                    pass
                await self.step(30, "state.menu")
                if await self.read("menu != null and menu.choices != null"):
                    break
            else:
                raise ActionError("couldn't get the sleep prompt at the bed")
            await self.dialogue(0)
        return {"went_to_bed": await self.read("time.text")}

    async def finish_night(self, day: int | None = None) -> dict:
        """Go through the end-of-night screens until morning, or until something needs a choice. Without day, morning is when the night screens are over."""
        notes: list[str] = []
        calm = 0
        await self.step(60, "state.night or state.menu")
        for _ in range(400):
            menu = await self.read({"type": "menu.type", "choices": "menu.choices", "chooser": "menu.raw.isProfessionChooser", "active": "menu.raw.isActive", "title": "menu.raw.title", "state": "state", "day": "time.total"})
            kind = menu["type"]
            if day is None:
                menu["day"] = None if menu["state"].get("night") else -1  # anything but None counts as a new day
            if kind == "LevelUpMenu" and menu["active"]:
                calm = 0
                await self.step(60)
                menu = await self.read({"chooser": "menu.raw.isProfessionChooser", "title": "menu.raw.title", "type": "menu.type"})
                if menu["type"] != "LevelUpMenu":
                    continue
                if menu["chooser"]:
                    options = await self.read(["menu.raw.leftProfessionDescription", "menu.raw.rightProfessionDescription"])
                    return {"needs_choice": menu["title"], "professions": [f"{i}: {' - '.join(o or [])}" for i, o in enumerate(options)], "how": "answer(0) or answer(1)"}
                notes.append(menu["title"])
                await self.click_component(field="okButton", after=20)
            elif kind == "ShippingMenu":
                calm = 0
                await self.step(120)
                totals = await self.read("menu.raw.categoryTotals")
                if totals and totals[-1]:
                    note = f"shipping earned {totals[-1]}g"
                    if note not in notes:
                        notes.append(note)
                await self.click_component(field="okButton", after=40)
            elif kind == "DialogueBox" and menu["choices"]:
                return {"question": menu["choices"]}
            elif kind == "DialogueBox":
                calm = 0
                await self.dialogue()
            elif kind is not None and not menu["state"].get("night") and menu["day"] != day:
                return {"woke_up": await self.date(), "notes": notes, "menu": kind}
            elif kind is None and menu["state"].get("free") and menu["day"] != day:
                calm += 1
                if calm >= 3:
                    return {"woke_up": await self.date(), "notes": notes}
                await self.step(20)
            elif kind is None and menu["state"].get("cutscene") and menu["day"] != day:
                return {"woke_up": await self.date(), "notes": notes, "note": "a cutscene started"}
            else:
                calm = 0
                changed = f"time.total != {day}" if day is not None else "not state.night"
                await self.step(60, {"menu": f"menu != null and menu.type != '{kind}'", "free": f"state.free and {changed}"})
        raise ActionError("the night didn't finish")

    async def choose_profession(self, choice: int) -> dict:
        if not await self.read("menu.type == 'LevelUpMenu' and menu.raw.isProfessionChooser"):
            raise ActionError("there's no profession to choose")
        box = await self.read(["menu.raw.xPositionOnScreen", "menu.raw.yPositionOnScreen", "menu.raw.width", "menu.raw.height"])
        x = box[0] + box[2] * (0.25 if choice == 0 else 0.75)
        await self.click_ui((x, box[1] + 300), after=30)
        return await self.finish_night()

    async def date(self) -> str:
        t = await self.read("time")
        return f"{t['weekday']} {t['season']} {t['day']}, year {t['year']}"
