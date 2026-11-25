"""Talking to villagers."""

from __future__ import annotations

from .common import ActionError, Interrupted, beside


class People:
    """Mixin for Game: villagers."""

    async def talk_all(self, names: list[str], gifts: dict[str, str]) -> list[dict]:
        """Talk to several people, nearest first (visit looks again after each talk, since people walk about: a fresh greedy tour
        beats a planned one that's stale by the second stop). Who can't be reached is noted, not raised."""
        left, done = list(dict.fromkeys(names)), []

        async def targets() -> list[str]:
            return list(left)

        async def act(name: str) -> None:
            left.remove(name)
            try:
                done.append(await self.talk(name, gifts.get(name)))
            except Interrupted:
                raise
            except ActionError as ex:
                done.append({"person": name, "failed": str(ex)})

        visit = await self.visit(targets, act)
        return done + [{"person": name, "failed": why} for name, why in visit.skipped.items()]

    async def talk(self, name: str, gift: str | None = None) -> dict:
        item = await self.find_item(gift) if gift else None
        for _ in range(3):  # they may step away between arriving and speaking
            person = (await self.go(name, beside=True)).person
            internal = person.get("internal") or person["name"]
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
            after = (await self.read({"from": "player.friends", "where": f"name == '{internal}' or internal == '{internal}'"}) or [{}])[0]
            friend = "points" in person
            return {"person": person["name"], "hearts": after.get("hearts") if friend else None, "points": f"{person.get('points') or 0} -> {after.get('points') or 0}" if friend else None,
                    "note": None if opened else "had nothing to say"}
        raise ActionError(f"couldn't get to {name}: they kept moving away")
