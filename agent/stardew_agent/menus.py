"""Clicking through menus: the backpack, crafting, letters, quests."""

from __future__ import annotations

from .common import ActionError, label


class Menus:
    """Mixin for Game: menu pages."""

    async def component(self, *, name: str | None = None, field: str | None = None) -> dict | None:
        for c in await self.read("menu.components") or []:
            if (name is not None and c.get("name") == name) or (field is not None and c["field"].lower() == field.lower()):
                return c
        return None

    async def click_component(self, *, name: str | None = None, field: str | None = None, after: int = 10) -> bool:
        c = await self.component(name=name, field=field)
        if c:
            await self.click_ui(c["at"], after=after)
        return c is not None

    async def backpack(self) -> list[dict]:
        grids = [g for g in await self.read("menu.inventories") or [] if g["player"]]
        if not grids:
            raise ActionError("this menu doesn't show your inventory")
        return min(grids, key=lambda g: g["field"] != "inventory")["slots"]  # some menus show the backpack twice; only this one takes clicks

    async def stow_held(self) -> None:
        """Put the item on the cursor into the backpack."""
        for _ in range(4):
            held = await self.read("menu.held")
            if not held:
                return
            slots = await self.backpack()
            spot = next((s for s in slots if s.get("item") and s["item"]["id"] == held["id"] and s["item"]["quality"] == held["quality"] and s["item"]["stack"] < s["item"]["max_stack"]), None)
            capacity = await self.read("player.maxItems")
            spot = spot or next((s for s in slots[:capacity] if not s.get("item")), None)
            if not spot:
                # drop it beside us, like clicking outside the menu
                width, height = await self.read("screen.ui")
                await self.click_ui((4, height / 2))
                raise ActionError(f"your inventory is full, so {label(held)} was dropped at your feet")
            await self.click_ui(spot["at"])

    async def close_menu(self) -> None:
        menu = await self.read({"type": "menu.type", "root": "menu.root", "source": "menu.raw.source", "choices": "menu.choices"})
        kind = menu["type"]
        if kind is None:
            return  # already closed: what was asked for is true
        if menu["root"] == "TitleMenu":
            raise ActionError("you're at the title screen")
        if kind == "DialogueBox":
            await self.dialogue()
            return
        if kind == "LetterViewerMenu":
            await self._close_letter()
            return
        if kind == "ItemGrabMenu" and menu["source"] == 4:
            left = [s for grid in await self.read("menu.inventories") if not grid["player"] for s in grid["slots"] if s.get("item")]
            if left:
                raise ActionError("closing this would destroy the items in it; take them first (item.trash() can make room)")
        if await self.read("menu.held") and kind != "InventoryPage":
            await self.stow_held()
        await self.step(180, "menu == null or menu.closable")
        for _ in range(3):
            await self.press("Escape", after=12)
            if not await self.read("menu != null and menu.type == '%s'" % kind):
                break
        else:
            raise ActionError(f"{kind} can't be closed right now; look at its components")
        await self.settle(120)

    async def _close_letter(self) -> None:
        for _ in range(15):
            letter = await self.read({"type": "menu.type", "page": "menu.raw.page", "pages": "menu.raw.mailMessage.count",
                                      "items": {"from": "menu.raw.itemsToGrab", "where": "item != null", "select": ["bounds.Center"]}, "quest": "menu.raw.questID", "parts": "menu.components"})
            if letter["type"] != "LetterViewerMenu":
                break
            if letter["page"] < letter["pages"] - 1:
                forward = next(c for c in letter["parts"] if c["field"] == "forwardButton")
                await self.click_ui(forward["at"], after=15)
                continue
            if letter["items"]:
                before = len(letter["items"])
                await self.click_ui(letter["items"][0]["bounds.Center"], after=15)
                if await self.read("menu.type == 'LetterViewerMenu'") and len(await self.read({"from": "menu.raw.itemsToGrab", "where": "item != null", "select": ["name"]})) >= before:
                    raise ActionError("your inventory is too full for the attached item")
                continue
            accept = next((c for c in letter["parts"] if c["field"] == "acceptQuestButton"), None)
            if accept and letter["quest"]:
                await self.click_ui(accept["at"], after=15)
                if await self.read("menu.raw.questID") == letter["quest"]:
                    await self.press("Escape", after=15)
                continue
            await self.press("Escape", after=15)
        await self.settle(120)

    async def open_tab(self, tab: str) -> None:
        await self.ready()
        await self.press(self.keys["menu"], after=12)
        tabs = await self.read("menu.tabs") or []
        spot = next((t for t in tabs if t["name"] == tab), None)
        if not spot:
            raise ActionError(f"couldn't open the {tab} tab")
        await self.click_ui(spot["at"], after=8)

    async def trash(self, query: str) -> dict:
        """Destroy a stack. Works in any menu with a backpack and trash can; opens the inventory otherwise."""
        item = await self.find_item(query)
        if not item["trashable"]:
            raise ActionError(f"{item['name']} can't be trashed")
        opened = False
        if await self.read("menu == null"):
            await self.open_tab("inventory")
            opened = True
        slots = await self.backpack()
        await self.click_ui(slots[item["slot"]]["at"])
        can = await self.component(field="trashCan")
        if not can:
            await self.stow_held()
            raise ActionError("there's no trash can in this menu")
        await self.click_ui(can["at"])
        if opened:
            await self.close_menu()
        return {"trashed": label(item)}

    async def craft(self, recipe: str, times: int = 1) -> dict:
        known = await self.read("player.recipes")
        match = next((r for r in known if recipe.lower() in (r["id"].lower(), r["name"].lower())), None)
        if not match:
            raise ActionError(f"you don't know a recipe called '{recipe}'")
        if not match["craftable"]:
            raise ActionError(f"missing ingredients for {match['name']}: needs {match['needs']}")
        if not await self.read("player.free_slots"):
            raise ActionError("crafting needs a free inventory slot")
        await self.open_tab("crafting")
        made = 0
        try:
            for page in range(await self.read("menu.raw.pagesOfCraftingRecipes.count")):
                entries = await self.read({"from": f"menu.raw.pagesOfCraftingRecipes[{page}]", "select": ["Value.name", "Key.bounds.Center"]})
                spot = next((e for e in entries if e["Value.name"] == match["id"]), None)
                if not spot:
                    if not await self.click_component(field="downButton"):
                        break
                    continue
                for _ in range(times):
                    before = await self.read({"from": "player.recipes", "where": f"id == '{match['id'].replace(chr(39), '')}'", "select": ["craftable"]})
                    if before and not before[0]["craftable"]:
                        break
                    await self.click_ui(spot["Key.bounds.Center"])
                    if not await self.read("menu.held"):
                        break
                    made += 1
                    await self.stow_held()
                break
        finally:
            if await self.read("menu != null"):
                await self.close_menu()
        return {"crafted": f"{match['name']} x{made}"}

    async def collect_rewards(self) -> dict:
        """Take money rewards from completed quests, through the journal."""
        done = [q for q in await self.read("player.quests") if q.get("complete")]
        if not done:
            return {"collected": "nothing to collect"}
        await self.ready()
        before = await self.read("player.money")
        await self.press(self.keys["journal"], after=15)
        if await self.read("menu.type") != "QuestLog":
            raise ActionError("the journal didn't open")
        for quest in done:
            spot = None
            for page in range(await self.read("menu.raw.pages.count")):
                names = await self.read({"from": f"menu.raw.pages[{page}]", "select": ["questTitle"]})
                spot = next(((page, n) for n, q in enumerate(names) if q["questTitle"] == quest["title"]), None)
                if spot:
                    break
            if not spot:
                continue
            for _ in range(6):
                current = await self.read("menu.raw.currentPage")
                if current == spot[0]:
                    break
                await self.click_component(field="forwardButton" if current < spot[0] else "backButton", after=8)
            buttons = await self.read({"from": "menu.raw.questLogButtons", "select": ["bounds.Center"]})
            await self.click_ui(buttons[spot[1]]["bounds.Center"], after=15)
            if await self.read("menu.raw.questPage") == -1:
                continue
            box = await self.component(field="rewardBox")
            if box:
                await self.click_ui(box["at"], after=30)
            if await self.read("menu.type == 'QuestLog' and menu.raw.questPage != -1"):
                await self.click_component(field="backButton", after=15)
        for _ in range(2):
            if await self.read("menu != null"):
                await self.press("Escape", after=15)
        return {"collected": f"{await self.read('player.money') - before}g"}
