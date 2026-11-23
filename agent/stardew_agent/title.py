"""The title screen: loading a save or starting a new game."""

from __future__ import annotations

from . import driver as d
from .common import ActionError


class Title:
    """Mixin for Game: before a save is loaded."""

    async def _title_button(self, name: str, opens: str) -> None:
        for _ in range(40):
            if await self.read("menu.type") == opens:
                return
            if await self.read("menu.type") == "TitleMenu":
                spot = await self.component(name=name)
                if spot and await self.read("menu.raw.titleInPosition"):
                    await self.click_ui(spot["at"], after=40)
                    continue
                await self.click_ui((200, 200), after=30)  # skips the logos
            else:
                await self.step(30)
        raise ActionError(f"couldn't open '{name}' from the title screen")

    async def load_save(self, name: str) -> None:
        if await self.read("state.world"):
            raise ActionError("a save is already loaded")
        saves = [s["folder"] for s in await self.read("saves")]
        folder = next((f for f in saves if f.lower() == name.lower()), None)
        if not folder:
            raise ActionError(f"no save called '{name}'; there's {', '.join(saves) or 'none'}")
        await self._title_button("Load", "LoadGameMenu")
        for _ in range(60):
            slots = await self.read({"from": "menu.raw.MenuSlots", "select": ["Farmer.slotName"]})
            if slots:
                break
            await self.step(20)
        index = next((i for i, s in enumerate(slots) if s["Farmer.slotName"] == folder), None)
        if index is None:
            raise ActionError(f"the game doesn't list {folder}")
        for _ in range(len(slots)):
            top = await self.read("menu.raw.currentItemIndex")
            if top <= index < top + 4:
                break
            await self.d.run(d.input(scroll=-1 if index >= top + 4 else 1), d.step(3))
        spots = await self.read({"from": "menu.raw.slotButtons", "select": ["bounds.Center"]})
        await self.click_ui(spots[index - await self.read("menu.raw.currentItemIndex")]["bounds.Center"])
        await self._wait_for_world()

    async def new_game(self, name: str, farm: str, favorite: str, farm_type: str = "Standard") -> None:
        if await self.read("state.world"):
            raise ActionError("a save is already loaded")
        await self._title_button("New", "CharacterCustomization")
        await self.step(30)
        for box, text in (("nameBoxCC", name), ("farmnameBoxCC", farm), ("favThingBoxCC", favorite)):
            spot = await self.component(field=box)
            assert spot
            await self.click_ui(spot["at"])
            await self.d.run(d.input(text=text, clear=True), d.step(5))
        if not await self.read("menu.raw.skipIntro"):
            await self.click_component(field="skipIntroButton")
        if farm_type != "Standard":
            await self.click_component(name=farm_type)
        await self.click_component(field="okButton")
        await self._wait_for_world()

    async def _wait_for_world(self) -> None:
        result = await self.step(7200, {"free": "state.free", "scene": "state.world and state.cutscene"})
        if not result["fired"]:
            raise ActionError("the save didn't finish loading")
        await self.step(60)
