"""Walking within a location, and travelling between locations."""

from __future__ import annotations

import difflib
import heapq
import re
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from . import driver as d
from .common import FREE, ActionError, Interrupted, Tile, crowded
from .places import MINUTES_PER_DOOR, PLACES, TILES_PER_MINUTE


SEGMENT = 45  # ticks walked before planning again, while things around us move


class Moving:
    """Mixin for Game: paths, doors, and routes."""

    _health_floor = 9999  # health when the current walk began; only a drop below it triggers the low-health guard

    def guards(self) -> dict[str, str]:
        return {"low health": f"player.health < player.max_health * {self.min_health} and player.health < {self._health_floor}"}

    async def goto(self, goals: Iterable[Iterable[int]], near: int = 0, *, what: str = "the target", force: Iterable = (),
                   track: Callable[[], Awaitable[list]] | None = None, avoid: bool = True) -> Tile | None:
        """Walk to the nearest goal (near=0), or beside it (near=4 or 8). Returns the goal reached, or None if we left the location.

        People and animals in the way are waited for, then walked around; monsters are fought if armed, else avoided.
        With track, the goals are read again as we go, to catch up with something that moves.
        """
        goals = [list(g) for g in goals]
        force = [list(f) for f in force]
        stuck: list = []  # where walking failed or a door stayed shut
        trouble = waits = fights = monsters = segments = 0
        stop_for, armed = "all", None
        self._health_floor = await self.read("player.health")
        while True:
            if track:
                goals = [list(g) for g in await track()]
                if not goals:
                    raise ActionError(f"lost sight of {what}")
            view = await self.read({"me": "player.tile", "monsters": {"from": "location.monsters", "where": "not hidden", "select": ["x", "y"]}})
            crowd = crowded(view["monsters"], goals, view["me"])
            found = await self.d.look(d.path(goals, near, force=force, blocked=stuck + crowd, avoid=avoid))
            if found is None and crowd:
                found = await self.d.look(d.path(goals, near, force=force, blocked=stuck, avoid=avoid))
            if found is None:
                if stuck:
                    stuck = []
                    await self.step(45)
                    continue
                if waits < 6 and await self.d.look(d.path(goals, near, force=force, avoid=False)):
                    waits += 1  # someone stands in the only way through
                    await self.step(60)
                    continue
                raise ActionError(await self._unreachable(goals, near, what))
            if len(found["path"]) <= 1:
                return tuple(found["target"])
            short = track is not None or bool(view["monsters"])
            result = await self.d.one({**d.follow(found["path"], self.guards(), SEGMENT if short else 3600), "stop_for": stop_for if avoid else "none"})
            status = result["status"]
            if status == "warped":
                await self.settle()
                return None
            if status == "until":
                raise Interrupted(result["fired"])
            if status in ("timeout", "arrived") and short:
                segments += 1
                if segments > (80 if track else 400):
                    raise ActionError(f"couldn't catch up with {what}" if track else f"couldn't reach {what}: it took too long")
                continue
            if status == "arrived":
                await self.step(4, FREE)
                return tuple(found["target"])
            if status == "blocked":
                if not result["monster"]:
                    waits += 1
                    if waits > 8:
                        raise ActionError(f"couldn't reach {what}: {result['by']} kept standing in the way near {result['at']}")
                    await self.step(30)  # they usually walk on; if not, the next path goes around them
                    continue
                if armed is None:
                    armed = await self.armed()
                if armed and fights < 4:
                    fights += 1
                    await self.fight_off()
                    continue
                monsters += 1
                if monsters >= 3:
                    stop_for = "people"  # can't shake it: keep going past
                continue
            trouble += 1
            if trouble > 9:
                raise ActionError(f"couldn't reach {what}: the way kept being blocked")
            if status == "interrupted":
                await self.ready()
            elif status == "door":
                await self.click_tile(result["at"], right=True, after=30)
                if await self.read("state.menu"):
                    await self.auto_close()
                    stuck.append(result["at"])
            elif result["at"]:
                stuck.append(result["at"])

    async def fight_off(self, ticks: int = 240) -> dict:
        """Fight the monsters close by for a moment, then hold what we held before."""
        from . import combat

        slot = await self.read("player.slot")
        await self.hold_tool("weapon")
        try:
            return await combat.Fight(self, self.orders or combat.Orders()).run(radius=3, max_ticks=ticks)
        finally:
            if slot < len(self.hotbar) and await self.read("player.slot") != slot:
                await self.press(self.hotbar[slot])

    async def goto_around(self, goals: list, near: int, what: str) -> Tile | None:
        """Like goto, but if this part of the location is cut off, leave and come back another way."""
        try:
            return await self.goto(goals, near, what=what)
        except Interrupted:
            raise
        except ActionError as ex:
            here = await self.read("location.name")
            if not await self._route(here, set(), [list(g) for g in goals]):
                raise ex
            await self.travel(here, [list(g) for g in goals])
            return await self.goto(goals, near, what=what)

    async def _unreachable(self, goals: list, near: int, what: str) -> str:
        here = await self.read("player.tile")
        if await self.d.look(d.path(goals, near, avoid=False)):
            return f"can't reach {what} from {here}: people or animals are standing in the way"
        if len(goals) == 1 and not await self.standable(goals[0]):
            kind = await self.describe_tile(goals[0])
            if near == 0:
                return f"can't stand on {goals[0]}: it's {kind}; walk beside it instead"
            return f"can't get beside {goals[0]} ({kind}) from {here}: no walkable path"
        found = await self.d.look(d.path(goals, near, debris=True, avoid=False))
        if found and found["debris"]:
            return f"can't reach {what} from {here}: debris is in the way, clear {found['debris'][:15]} first (include trees and saplings if needed)"
        return f"can't reach {what} from {here}: no walkable path"

    async def walk_to(self, tile: Iterable[int], adjacent: bool = False) -> str | None:
        await self.ready()
        tile = list(tile)
        if not adjacent and not await self.standable(tile):
            # walking "to" a chest or counter means walking up to it
            await self.goto_around([tile], 8, what=f"beside {tile}")
            return f"{tile} is {await self.describe_tile(tile)}, so you stopped beside it"
        await self.goto_around([tile], 4 if adjacent else 0, what=f"tile {tile}")
        return None

    async def open_ground(self) -> list:
        """Walkable tiles nearby with nothing around them, where clicking yourself does nothing else."""
        x, y = await self.here()
        rect = [x - 5, y - 5, 11, 11]
        grid = await self.read({"$grid": {"rect": rect, "layers": ["pass"]}})
        rows = grid["pass"]
        spots = []
        for j in range(1, len(rows) - 1):
            for i in range(1, len(rows[j]) - 1):
                if all(rows[j + dy][i + dx] == "." for dx in (-1, 0, 1) for dy in (-1, 0, 1)):
                    spots.append([grid["x"] + i, grid["y"] + j])
        if not spots:
            raise ActionError("there's no open ground nearby to stop and eat")
        return spots

    async def standable(self, tile: list) -> bool:
        grid = await self.read({"$grid": {"rect": [tile[0], tile[1], 1, 1], "layers": ["pass"]}})
        return bool(grid["pass"]) and grid["pass"][0] != "#"

    async def describe_tile(self, tile: list) -> str:
        """Say what blocks a tile, for error messages."""
        where = f"x == {tile[0]} and y == {tile[1]}"
        found = await self.read({"o": {"from": "location.object_rows", "where": where, "select": ["name"]}, "t": {"from": "location.terrain", "where": where, "select": ["kind"]},
                                 "g": {"$grid": {"rect": [tile[0], tile[1], 1, 1], "layers": ["water"]}}, "size": "location.size"})
        if not (0 <= tile[0] < found["size"][0] and 0 <= tile[1] < found["size"][1]):
            here = await self.read("location.name")
            return f"outside {here}, which is {found['size'][0]}x{found['size'][1]} (tiles are in the current location; go to the place first)"
        if found["o"]:
            return f"a {found['o'][0]['name']}"
        if found["t"]:
            return f"a {found['t'][0]['kind']}"
        if found["g"]["water"] and found["g"]["water"][0] == "1":
            return "water"
        return "blocked (a wall, building, or furniture)"

    async def for_nearest(self, targets: Callable[[], Awaitable[list[Tile]]], act: Callable[[Tile], Awaitable[None]], near: int = 8, limit: int = 2000) -> list[Tile]:
        """Repeatedly go to the nearest target and act, until there are none. Returns targets we couldn't reach."""
        failed: set[Tile] = set()
        self.why = None
        for _ in range(limit):
            remaining = [t for t in await targets() if t not in failed]
            if not remaining:
                break
            await self.ready()
            try:
                target = await self.goto(remaining, near)
            except Interrupted:
                raise
            except ActionError as ex:
                self.why = str(ex)
                failed.update(remaining)
                break
            if target is None:
                break
            before = len(remaining)
            await act(target)
            after = [t for t in await targets() if t not in failed]
            if target in after and len(after) >= before:
                failed.add(target)
        return sorted(failed)

    async def _route(self, target: str, banned: set, goals: list | None = None, *, with_cost: bool = False, start: str | None = None) -> Any:
        """Find which exits to take, by shortest walking distance. With goals, the route must end where one can be reached.
        With with_cost, return (tiles walked, locations crossed) instead. With start, plan from that location's main entrance."""
        if start:
            entry = await self.read(f"locations['{start}'].exits") or []
            begin = (start, (entry[0]["x"], entry[0]["y"]) if entry else (0, 0))
        else:
            here = await self.read({"name": "location.name", "tile": "player.tile"})
            begin = (here["name"], tuple(here["tile"]))
        queue: list = [(0.0, 0, begin[0], begin[1], [])]
        seen, count = set(), 0
        while queue:
            cost, _, name, tile, route = heapq.heappop(queue)
            if name.lower() == target.lower():
                if not goals or await self.d.look(d.path(goals, 8, location=name, start=list(tile), avoid=False)):
                    return (cost, len(route)) if with_cost else route
            if (name, tile) in seen or len(seen) > 120:
                continue
            seen.add((name, tile))
            exits = await self.read(f"locations['{name}'].exits")
            exits = [e for e in exits or [] if (name, e["x"], e["y"]) not in banned]
            paths = [e for e in exits if not e["door"]]
            doors = [e for e in exits if e["door"]]
            options = {"location": name, "start": list(tile), "costs": True, "avoid": False}  # people elsewhere will have moved
            walk, knock = await self.d.run(
                d.path([[e["x"], e["y"]] for e in paths], 0, force=[[e["x"], e["y"]] for e in paths], **options),
                d.path([[e["x"], e["y"]] for e in doors], 4, force=[[e["x"], e["y"]] for e in paths], **options),
            )
            costs = {(x, y): c for x, y, c in walk}
            costs.update({(x, y): c + 3 for x, y, c in knock if c is not None})
            for e in exits:
                c = costs.get((e["x"], e["y"]))
                if c is not None:
                    count += 1
                    heapq.heappush(queue, (cost + c + 5, count, e["to"], (e["ax"], e["ay"]), [*route, e]))
        return None

    async def location_name(self, name: str) -> str:
        """The game's name for a location, from a loose spelling like 'AdventurerGuild' or 'pierre's'."""
        rows = await self.read({"from": "game.locations", "select": ["NameOrUniqueName", "DisplayName"]})
        key = lambda text: re.sub(r"[^a-z0-9]", "", re.sub(r"^the |'s\b", "", str(text).lower()))
        known = {key(r[f]): r["NameOrUniqueName"] for r in rows for f in ("DisplayName", "NameOrUniqueName") if r.get(f)}
        if key(name) in PLACES:  # first: some places share a display name, like the Wizard's tower and its basement
            return PLACES[key(name)]
        if key(name) in known:
            return known[key(name)]
        close = difflib.get_close_matches(key(name), list(known), n=1, cutoff=0.75)
        return known[close[0]] if close else name

    async def travel(self, target: str, goals: list | None = None) -> None:
        """Walk to another location, and with goals, to a part of it from which one can be reached."""
        target = await self.location_name(target)
        if target == "Farm" and not goals:
            # the farm is often split by debris, so come in on the side with the house
            doors = await self.read({"from": "locations['Farm'].exits", "where": "door and to == 'FarmHouse'"})
            if doors and await self.read("location.name") != "Farm":
                goals = [[doors[0]["x"], doors[0]["y"]]]
        banned: set = set()
        for _ in range(40):
            await self.ready()
            here = await self.read("location.name")
            if here.lower() == target.lower() and not goals:
                return
            if await self.read("location.mine_level") is not None:
                await self.leave_mine()
                continue
            route = await self._route(target, banned, goals)
            if route == []:
                return
            if route is None and goals:
                goals = None  # it can't be reached from anywhere, so just get there
                continue
            if not route:
                raise ActionError(f"no walking route from {here} to '{target}'" + (" (doors on the way are locked or closed now)" if banned else "; check the name, or whether it needs a menu like the mine elevator or bus"))
            exit = route[0]
            at = (exit["x"], exit["y"])
            closed = None
            if exit["door"]:
                await self.goto([at], 4, what=f"the door to {exit['to']}")
                await self.empty_hands()
                mark = len(self.journal)
                await self.click_tile(at, right=True, after=1)
                await self.step(60, {"moving": "state.fading", "menu": "state.menu"})
                if await self.read("menu != null and menu.type == 'DialogueBox' and menu.choices == null"):
                    speaker = await self.read("menu.speaker")
                    await self.dialogue()
                    if speaker:
                        # someone was standing in the doorway, and we talked to them instead
                        await self.step(120)
                        continue
                    closed = " ".join(e["text"] for e in self.journal[mark:] if e["kind"] == "dialogue")
            else:
                await self.goto([at], 0, what=f"the exit to {exit['to']}", force=[at])
            await self.settle()
            if await self.read("location.name") == here and await self.read("state.free"):
                banned.add((here, *at))
                if closed:
                    raise ActionError(f"the way to {exit['to']} is closed: {closed}")
        raise ActionError(f"gave up travelling to {target}")

    async def leave_mine(self) -> None:
        ladders = await self.read("location.ladders")
        up = [(l["x"], l["y"]) for l in ladders if l["kind"] == "up"]
        if not up:
            raise ActionError("this floor has no ladder up")
        target = await self.goto(up, 8, what="the ladder up")
        await self.empty_hands()
        await self.click_tile(target or up[0], right=True)
        await self.step(60, "state.menu")
        await self.dialogue(0)
        await self.settle()

    async def minutes_home(self) -> int | None:
        """About how many game minutes the walk home takes from here; None when already home."""
        home = await self.read("player.home")
        if await self.read("location.name") == home:
            return None
        if await self.read("location.mine_level") is not None:
            return 20 + (await self.minutes_from("Mine", home) or 60)
        return await self.minutes_from(None, home)

    async def minutes_from(self, start: str | None, target: str) -> int | None:
        """Walking minutes from here (or from a location's entrance) to the target location."""
        found = await self._route(target, set(), with_cost=True, start=start)
        if found is None:
            return None
        tiles, hops = found
        return round(tiles / TILES_PER_MINUTE + MINUTES_PER_DOOR * hops)
