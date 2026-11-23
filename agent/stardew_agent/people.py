"""Finding and talking to villagers."""

from __future__ import annotations

from .common import ActionError, Interrupted, Tile, beside
from .places import WORKPLACES


class People:
    """Mixin for Game: villagers."""

    async def find_person(self, name: str) -> dict | None:
        """Where someone is: villagers from the friendship list, others (shopkeepers like Marlon) from where they work."""
        rows = await self.read({"from": "player.friends", "where": f"name == '{name}' or internal == '{name}'"})
        if rows:
            return rows[0]
        here = await self.read("location.name")
        for place in dict.fromkeys([here, WORKPLACES.get(name.title())]):
            if place:
                found = await self.read({"from": f"locations['{place}'].npcs", "where": f"name == '{name}' or internal == '{name}' or name == '{name.title()}'"})
                if found:
                    return {**found[0], "location": place}
        return None

    async def talk(self, name: str, gift: str | None = None) -> dict:
        person = await self.find_person(name)
        if not person:
            raise ActionError(f"no one called '{name}' was found (people() lists the villagers)")
        if not person.get("location"):
            raise ActionError(f"{person['name']} isn't around today")
        item = await self.find_item(gift) if gift else None
        internal = person["internal"]

        async def where() -> list[Tile]:
            return [(r["x"], r["y"]) for r in await self.read({"from": "location.npcs", "where": f"internal == '{internal}'", "select": ["x", "y"]})]

        why = "they may be behind a counter or on the move"
        for _ in range(8):
            await self.ready()
            person = await self.find_person(internal) or person
            if await self.read("location.name") != person["location"]:
                await self.travel(person["location"])
                continue
            try:
                # they walk about, so keep heading for where they are now
                if await self.goto(await where() or [(person["x"], person["y"])], 8, what=person["name"], track=where) is None:
                    continue
            except Interrupted:
                raise
            except ActionError as ex:
                why = str(ex)
                await self.step(30)
                continue
            await (self.hold(item["id"]) if item else self.empty_hands())
            now = await self.read({"npc": {"from": "location.npcs", "where": f"internal == '{internal}'", "select": ["x", "y"]}, "me": "player.tile"})
            if not now["npc"]:
                continue
            at = (now["npc"][0]["x"], now["npc"][0]["y"])
            if not beside(at, now["me"]):
                continue
            await self.act(at)
            await self.step(45, {"menu": "state.menu", "scene": "state.cutscene"})
            opened = await self.read("state.menu")
            if opened:
                await self.dialogue()
            await self.empty_hands()
            after = await self.find_person(internal) or {}
            friend = "points" in person
            return {"person": person["name"], "hearts": after.get("hearts") if friend else None, "points": f"{person.get('points') or 0} -> {after.get('points') or 0}" if friend else None,
                    "note": None if opened else "had nothing to say"}
        raise ActionError(f"couldn't get to {person['name']}: {why}")
