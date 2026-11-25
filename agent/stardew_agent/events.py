"""Dialogue boxes and cutscenes."""

from __future__ import annotations

from . import driver as d
from .common import ActionError


class Events:
    """Mixin for Game: talking and cutscenes."""

    async def dialogue(self, choice: int | None = None) -> None:
        """Advance dialogue until it closes, or asks a question we weren't given an answer to."""
        idle = 0
        for _ in range(250):
            menu = await self.read({"type": "menu.type", "choices": "menu.choices", "ready": "menu.ready", "scene": "state.cutscene", "ui": "screen.ui"})
            if menu["type"] != "DialogueBox":
                if menu["type"] or idle >= (6 if menu["scene"] else 1):
                    return
                idle += 1
                await self.step(80 if menu["scene"] else 12, {"menu": "state.menu", "free": "state.free and not state.cutscene"})
                continue
            idle = 0
            if not menu["ready"]:
                await self.step(90, "menu == null or menu.ready")
                continue
            width, height = menu["ui"]
            if menu["choices"]:
                if choice is None:
                    return
                if not 0 <= choice < len(menu["choices"]):
                    raise ActionError(f"choice must be 0 to {len(menu['choices']) - 1}")
                # finish the text first; answers only respond once it's fully shown
                spots = await self.read({"from": "menu.raw.responseCC", "select": ["bounds.Center"]})
                if not spots:
                    await self.step(10)
                    continue
                await self.click_ui(spots[choice]["bounds.Center"], after=15)
                if await self.read("menu == null or menu.choices == null or not menu.ready"):
                    choice = None
                continue
            await self.click_ui((width / 2, height - 90), after=3)

    async def play_event(self) -> None:
        """Play through a cutscene until it ends or asks something. The journal records what's said."""
        for _ in range(120):
            state = await self.read("state")
            if not state.get("cutscene"):
                await self.settle()
                return
            if state.get("menu"):
                if await self.read("menu.choices != null or menu.type != 'DialogueBox'"):
                    return
                await self.dialogue()
            else:
                await self.step(240, {"menu": "state.menu", "end": "not state.cutscene"})

    async def pass_event(self) -> None:
        """Skip a cutscene when it can be skipped and asks nothing, telling the journal what it would have shown; else play it through."""
        try:
            lines = await self.skip_event()
        except ActionError:
            return await self.play_event()
        for line in lines:
            for listener in self.d.on_journal:
                listener({"kind": "event", "text": line})

    async def skip_event(self) -> list[str]:
        """Skip a cutscene. Returns the rest of its script, since none of it will be shown."""
        if not await self.read("state.cutscene"):
            raise ActionError("no cutscene is playing")
        await self.step(240, {"ok": "event.skippable", "menu": "state.menu"})
        script = await self.read({"commands": "event.commands", "index": "event.index", "skippable": "event.skippable", "actors": {"from": "event.actor_rows", "select": ["internal", "name"]}})
        if not script["skippable"]:
            raise ActionError("this cutscene can't be skipped")
        remaining = script["commands"][script["index"]:]
        if any(c.lower().startswith(("question", "quickquestion")) for c in remaining):
            raise ActionError("this cutscene asks you a question, so it shouldn't be skipped")
        lines = list(filter(None, (script_line(c, {a["internal"]: a["name"] for a in script["actors"]}) for c in remaining)))
        for _ in range(5):
            if await self.read("state.menu"):
                await self.dialogue()
            view = await self.read("screen")
            _, _, w, h = view["viewport"]
            await self.d.run(d.input(screen=((w - 52) * view["zoom"], (h - 34) * view["zoom"]), buttons=[]), d.step(2), d.input(buttons=["mouse_left"]), d.step(2), d.input(buttons=[]), d.step(20))
            if not await self.read("state.cutscene"):
                break
        await self.step(60)
        await self.settle()
        return lines


def script_line(command: str, names: dict[str, str]) -> str | None:
    """Get the text a cutscene command would have shown."""
    parts = split_command(command)
    if not parts:
        return None
    verb = parts[0].lower()
    if verb in ("speak", "textabovehead", "splitspeak") and len(parts) > 2:
        return f"{names.get(parts[1], parts[1])}: {clean_dialogue(parts[2])}" + (" [possible]" if verb == "splitspeak" else "")
    if verb == "message" and len(parts) > 1:
        return clean_dialogue(parts[1])
    if verb in ("additem", "addcraftingrecipe", "addcookingrecipe", "addquest", "mail", "money", "friendship", "awardfestivalprize"):
        return f"[{verb} {' '.join(parts[1:3])}]"
    return None


def split_command(text: str) -> list[str]:
    parts, current, quoted = [], "", False
    for char in text:
        if char == '"':
            quoted = not quoted
        elif char == " " and not quoted:
            if current:
                parts.append(current)
            current = ""
        else:
            current += char
    return parts + ([current] if current else [])


def clean_dialogue(text: str) -> str:
    """Strip portrait and page codes from raw dialogue."""
    out = []
    for page in text.replace("#$e#", "#$b#").split("#$b#"):
        while len(page) > 1 and page[-2] == "$":
            page = page[:-2]
        out.append(page.replace("@", "you").strip())
    return " ".join(out)
