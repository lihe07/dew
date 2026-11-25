"""Getting anywhere: to a place, a person, or tiles, across locations. The rest of the agent only uses go and minutes.

Everything else here is how: routes between locations, path segments planned again as things move, doors (closed, locked, kept hours),
people and animals in the way, monsters on the way (fought if armed), locked bedrooms, following someone who walks, leaving the mines.
"""

from __future__ import annotations

import difflib
import heapq
import re
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any

from . import driver as d
from .common import FREE, ActionError, Interrupted, Tile, beside as next_to, crowded


SEGMENT = 45  # ticks walked before planning again, while things around us move
TILES_PER_MINUTE, MINUTES_PER_DOOR = 4.0, 2  # fitted to real walks: Mountain and Beach to the farmhouse both take 50 minutes

# what people call places, by the game's names
PLACES = {"pierre": "SeedShop", "pierres": "SeedShop", "generalstore": "SeedShop", "wizardtower": "WizardHouse", "wizard": "WizardHouse", "tower": "WizardHouse",
          "blacksmith": "Blacksmith", "clint": "Blacksmith", "guild": "AdventureGuild", "adventurersguild": "AdventureGuild", "joja": "JojaMart",
          "museum": "ArchaeologyHouse", "library": "ArchaeologyHouse", "carpenter": "ScienceHouse", "robin": "ScienceHouse", "carpentershop": "ScienceHouse",
          "marnie": "AnimalShop", "ranch": "AnimalShop", "willy": "FishShop", "fishshop": "FishShop", "clinic": "Hospital", "harvey": "Hospital",
          "communitycenter": "CommunityCenter", "cc": "CommunityCenter", "mines": "Mine", "home": "FarmHouse", "house": "FarmHouse", "farmhouse": "FarmHouse"}

# where people live, for "Haley's house": the game's location of each home
HOMES = {"HaleyHouse": "Haley Emily", "SamHouse": "Sam Jodi Kent Vincent", "JoshHouse": "Alex George Evelyn", "Trailer": "Pam Penny",
         "ManorHouse": "Lewis", "HarveyRoom": "Harvey", "ElliottHouse": "Elliott", "LeahHouse": "Leah", "Saloon": "Gus",
         "ScienceHouse": "Robin Demetrius Maru Sebastian", "AnimalShop": "Marnie Shane Jas", "Blacksmith": "Clint", "WizardHouse": "Wizard",
         "SeedShop": "Pierre Caroline Abigail", "FishShop": "Willy", "Tent": "Linus", "Sewer": "Krobus"}
HOMES = {name.lower(): home for home, names in HOMES.items() for name in names.split()}

# opening hours, as (opens, closes, weekdays closed): the game's rules for the well-known shops
HOURS = {"SeedShop": (900, 1700, ("Wed",)), "JojaMart": (900, 2300, ()), "FishShop": (900, 1700, ()), "Blacksmith": (900, 1600, ()),
         "AnimalShop": (900, 1600, ("Mon", "Tue")), "ScienceHouse": (900, 1700, ()), "Saloon": (1200, 2400, ()), "AdventureGuild": (1400, 2200, ())}

# who keeps each shop's counter
KEEPERS = {"SeedShop": "Pierre", "JojaMart": "Morris", "FishShop": "Willy", "Blacksmith": "Clint", "AnimalShop": "Marnie", "ScienceHouse": "Robin",
           "Saloon": "Gus", "AdventureGuild": "Marlon", "Hospital": "Harvey"}

# people who aren't on the friendship list, and where they're usually found
WORKPLACES = {"Marlon": "AdventureGuild", "Gil": "AdventureGuild", "Gunther": "ArchaeologyHouse", "Morris": "JojaMart", "Krobus": "Sewer", "Dwarf": "Mine",
              "Sandy": "SandyHouse", "Bouncer": "Club", "Mister Qi": "QiNutRoom", "Henchman": "WitchSwamp"}

Moving = Callable[[], Awaitable[list[Tile]]]  # where something that walks about is now


@dataclass
class Arrival:
    target: Tile | None  # the tile walked to (of a list, the one reached); None when the walk led into another location
    person: dict | None = None  # who we're beside, when going to a person
    note: str | None = None  # worth telling the agent, like having stopped beside a tile it can't stand on


class Navigation:
    """Mixin for Game: go and minutes. Everything else is private to this module."""

    _health_floor = 9999  # health when the current walk began; only a drop below it triggers the low-health guard

    async def go(self, target: str | Tile | list[Tile] | tuple[str, Tile] | Moving, *, beside: bool | int = False, what: str | None = None) -> Arrival:
        """Walk to a target, from anywhere:

        - a name: someone who's around today (found, and followed as they move), else a place, by any loose name ("Haley's house" too).
        - (place, tile): a tile in another location, coming into it on the side from which the tile can be reached.
          With beside=True it must be a person: "no one called", "isn't around today", and locked bedrooms are reasons.
        - a tile (x, y) here: onto it, or beside it if it can't be stood on (the note says so). If this part of the location is cut off, leaves and comes back another way.
        - a list of tiles here: the nearest one that can be reached.
        - an async function giving the tiles of something that moves: followed until reached.
        beside=True stops on one of the 8 tiles around the target, beside=4 on one of its 4 sides. what names the target in errors.
        Raises ActionError (Interrupted when the game needs a decision) saying why it can't be reached.
        """
        near = 8 if beside is True else int(beside)
        if isinstance(target, tuple) and len(target) == 2 and isinstance(target[0], str):  # a tile in another place
            place, tile = target
            await self._travel(place, [list(tile)])
            target = tuple(tile)
        if isinstance(target, str):
            if not beside:
                await self._travel(target)
                return Arrival(None)
            person = await self._reach(target)
            return Arrival((person["x"], person["y"]), person=person)
        if callable(target):
            return Arrival(await self._walk([], near, what=what or "the target", track=target))
        if isinstance(target, tuple):
            await self.ready()
            tile = list(target)
            if not near and not await self._standable(tile):
                # walking "to" a chest or counter means walking up to it
                reached = await self._walk_around([tile], 8, what=f"beside {tile}")
                return Arrival(reached, note=f"{tile} is {await self._describe_tile(tile)}, so you stopped beside it")
            return Arrival(await self._walk_around([tile], near, what=what or f"tile {tile}"))
        goals = [tuple(t) for t in target]
        if not near:
            goals = await self._standing(goals)
        try:
            return Arrival(await self._walk(goals, near, what=what or "the target"))
        except Interrupted:
            raise
        except ActionError as ex:
            if near or len(goals) != 1 or "in the way" not in str(ex) or not await self._is_bed(goals[0]):
                raise
            # pets like to lie on the bed, and the farmer can walk past them there
            return Arrival(await self._walk(goals, 0, what=what or "the target", avoid=False))

    async def minutes(self, target: str) -> int | None:
        """About how many game minutes the walk to a place or a person takes from here ("home" is the player's home); None when
        already there. A person here is estimated by tiles; one elsewhere by the walk to where they are."""
        person = None if "'" in target or target == "home" else await self._find_person(target)
        if person and person.get("location"):
            if await self.read("location.name") == person["location"] and person.get("x") is not None:
                x, y = await self.read("player.tile")
                return round((abs(person["x"] - x) + abs(person["y"] - y)) / TILES_PER_MINUTE)
            target = person["location"]
        target = await self.read("player.home") if target == "home" else await self.place_name(target)
        if await self.read("location.name") == target:
            return None
        if await self.read("location.mine_level") is not None:
            return 20 + (await self._minutes_from("Mine", target) or 60)
        return await self._minutes_from(None, target)

    # --- walking within a location ---------------------------------------------------------------------------

    def _guards(self) -> dict[str, str]:
        return {"low health": f"player.health < player.max_health * {self.min_health} and player.health < {self._health_floor}"}

    async def _walk(self, goals: Iterable[Iterable[int]], near: int = 0, *, what: str = "the target", force: Iterable = (),
                    track: Moving | None = None, avoid: bool = True, blocked: Iterable = ()) -> Tile | None:
        """Walk to the nearest goal (near=0), or beside it (near=4 or 8). Returns the goal reached, or None if we left the location.

        People and animals in the way are waited for, then walked around; monsters are fought if armed, else avoided.
        With track, the goals are read again as we go, to catch up with something that moves.
        """
        goals = [list(g) for g in goals]
        force = [list(f) for f in force]
        stuck: list = [list(t) for t in blocked]  # where walking failed or a door stayed shut (and doors known to be locked)
        trouble = waits = fights = monsters = segments = 0
        stop_for, armed = "all", None
        self._health_floor = await self.read("player.health")
        while True:
            await self.guard.watch(self)  # a monster close by pauses the walk for a fight
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
            result = await self.d.one({**d.follow(found["path"], self._guards(), SEGMENT if short else 3600), "stop_for": stop_for if avoid else "none"})
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
                if armed and fights < 4 and self.guard.fight != "off":
                    fights += 1
                    await self.guard.fight_off(self, 3, 240)
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

    async def _walk_around(self, goals: list, near: int, what: str) -> Tile | None:
        """Like _walk, but if this part of the location is cut off, leave and come back another way."""
        try:
            return await self._walk(goals, near, what=what)
        except Interrupted:
            raise
        except ActionError as ex:
            here = await self.read("location.name")
            if not await self._route(here, set(), [list(g) for g in goals]):
                raise ex
            await self._travel(here, [list(g) for g in goals])
            return await self._walk(goals, near, what=what)

    async def _unreachable(self, goals: list, near: int, what: str) -> str:
        here = await self.read("player.tile")
        if await self.d.look(d.path(goals, near, avoid=False)):
            return f"can't reach {what} from {here}: people or animals are standing in the way"
        if len(goals) == 1 and not await self._standable(goals[0]):
            kind = await self._describe_tile(goals[0])
            if near == 0:
                return f"can't stand on {goals[0]}: it's {kind}; walk beside it instead"
            return f"can't get beside {goals[0]} ({kind}) from {here}: no walkable path"
        found = await self.d.look(d.path(goals, near, debris=True, avoid=False))
        if found and found["debris"]:
            return f"can't reach {what} from {here}: debris is in the way, clear {found['debris'][:15]} first (include trees and saplings if needed)"
        return f"can't reach {what} from {here}: no walkable path"

    async def _standing(self, goals: list[Tile]) -> list[Tile]:
        """The goals that can be stood on, and for each that can't (a chest, a rock), the tiles around it that can."""
        xs, ys = [x for x, _ in goals], [y for _, y in goals]
        x0, y0 = min(xs) - 1, min(ys) - 1
        grid = await self.read({"$grid": {"rect": [x0, y0, max(xs) - x0 + 2, max(ys) - y0 + 2], "layers": ["pass"]}})
        rows, x0, y0 = grid["pass"], grid["x"], grid["y"]

        def free(x: int, y: int) -> bool:
            return 0 <= y - y0 < len(rows) and 0 <= x - x0 < len(rows[y - y0]) and rows[y - y0][x - x0] != "#"

        out = [(x + dx, y + dy) for x, y in goals for dx, dy in ([(0, 0)] if free(x, y) else [(a, b) for a in (-1, 0, 1) for b in (-1, 0, 1) if a or b]) if free(x + dx, y + dy)]
        return list(dict.fromkeys(out)) or goals

    async def arrival(self, target: str) -> Tile | None:
        """The tile you come in on, walking from here to a place; None when already there or there's no way."""
        route = await self._route(await self.place_name(target), set())
        return (route[-1]["ax"], route[-1]["ay"]) if route else None

    async def _standable(self, tile: list) -> bool:
        grid = await self.read({"$grid": {"rect": [tile[0], tile[1], 1, 1], "layers": ["pass"]}})
        return bool(grid["pass"]) and grid["pass"][0] != "#"

    async def _is_bed(self, tile: Tile) -> bool:
        at = await self.read({"home": "player.home", "bed": "player.bed_tile", "here": "location.name"})
        return bool(at["bed"]) and at["here"] == at["home"] and tuple(at["bed"]) == tuple(tile)

    async def _describe_tile(self, tile: list) -> str:
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

    # --- between locations -----------------------------------------------------------------------------------

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
            for e, c in await self._hops(name, tile):
                if (name, e["x"], e["y"]) not in banned:
                    count += 1
                    heapq.heappush(queue, (cost + c + 5, count, e["to"], (e["ax"], e["ay"]), [*route, e]))
        return None

    async def _hops(self, name: str, tile: Tile) -> list[tuple[dict, int]]:
        """The exits of a location reachable from a tile in it, with the walk to each. Kept for the day: asking the mod means
        pathfinding over the whole map on the game's thread, which stalls the game if a route search does it dozens of times."""
        day = await self.read("time.total")
        if getattr(self, "_hops_day", None) != day:
            self._hops_day, self._hops_known = day, {}
        if (name, tile) not in self._hops_known:
            exits = await self.read(f"locations['{name}'].exits") or []
            paths = [e for e in exits if not e["door"]]
            doors = [e for e in exits if e["door"]]
            options = {"location": name, "start": list(tile), "costs": True, "avoid": False}  # people elsewhere will have moved
            walk, knock = await self.d.run(
                d.path([[e["x"], e["y"]] for e in paths], 0, force=[[e["x"], e["y"]] for e in paths], **options),
                d.path([[e["x"], e["y"]] for e in doors], 4, force=[[e["x"], e["y"]] for e in paths], **options),
            )
            costs = {(x, y): c for x, y, c in walk}
            costs.update({(x, y): c + 3 for x, y, c in knock if c is not None})
            self._hops_known[(name, tile)] = [(e, costs[(e["x"], e["y"])]) for e in exits if costs.get((e["x"], e["y"])) is not None]
        return self._hops_known[(name, tile)]

    async def place_name(self, name: str) -> str:
        """The game's name for a location, from a loose spelling like 'AdventurerGuild' or 'pierre's'."""
        rows = await self.read({"from": "game.locations", "select": ["NameOrUniqueName", "DisplayName"]})
        key = lambda text: re.sub(r"[^a-z0-9]", "", re.sub(r"^the |'s\b", "", str(text).lower()))
        known = {key(r[f]): r["NameOrUniqueName"] for r in rows for f in ("DisplayName", "NameOrUniqueName") if r.get(f)}
        if key(name) in PLACES:  # first: some places share a display name, like the Wizard's tower and its basement
            return PLACES[key(name)]
        if home := re.match(r"(?:the )?(\w+)'s (?:house|home|room|place)$", name.strip().lower()):
            return HOMES.get(home[1], name)
        if key(name) in known:
            return known[key(name)]
        close = difflib.get_close_matches(key(name), list(known), n=1, cutoff=0.75)
        return known[close[0]] if close else name

    async def _travel(self, target: str, goals: list | None = None) -> None:
        """Walk to another location, and with goals, to a part of it from which one can be reached."""
        target = await self.place_name(target)
        if target == "Mine" and await self.read("location.name") != "Mine" and await self.read("time.total") < 4:
            raise ActionError("the mines are closed: a landslide blocks the path to them until Robin clears it on Spring 5")
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
                await self._leave_mine()
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
                await self._walk([at], 4, what=f"the door to {exit['to']}")
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
                await self._walk([at], 0, what=f"the exit to {exit['to']}", force=[at])
            await self.settle()
            if await self.read("location.name") == here and await self.read("state.free"):
                banned.add((here, *at))
                if closed:
                    raise ActionError(f"the way to {exit['to']} is closed: {closed}")
        raise ActionError(f"gave up travelling to {target}")

    async def _leave_mine(self) -> None:
        ladders = await self.read("location.ladders")
        up = [(l["x"], l["y"]) for l in ladders if l["kind"] == "up"]
        if not up:
            raise ActionError("this floor has no ladder up")
        target = await self._walk(up, 8, what="the ladder up")
        await self.empty_hands()
        await self.click_tile(target or up[0], right=True)
        await self.step(60, "state.menu")
        await self.dialogue(0)
        await self.settle()

    async def _minutes_from(self, start: str | None, target: str) -> int | None:
        """Walking minutes from here (or from a location's entrance) to the target location."""
        found = await self._route(target, set(), with_cost=True, start=start)
        if found is None:
            return None
        tiles, hops = found
        return round(tiles / TILES_PER_MINUTE + MINUTES_PER_DOOR * hops)

    # --- people ----------------------------------------------------------------------------------------------

    async def _find_person(self, name: str) -> dict | None:
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

    async def _locked_doors(self) -> list[tuple[Tile, list[str], int]]:
        """Bedroom doors here which won't open yet (each opens at 2 hearts with one of the people it names): tile, names, best hearts."""
        rows = await self.read({"from": "location.actions", "select": ["action", "x", "y"]})
        locked = []
        for r in rows:
            parts = (r.get("action") or "").split()
            if parts[:1] != ["Door"] or len(parts) < 2:
                continue
            hearts = max([((await self._find_person(n)) or {}).get("hearts") or 0 for n in parts[1:]])
            if hearts < 2:
                locked.append(((r["x"], r["y"]), parts[1:], hearts))
        return locked

    async def _reach(self, name: str) -> dict:
        """Walk beside someone, following them as they move and across locations."""
        person = await self._find_person(name)
        if not person:
            raise ActionError(f"no one called '{name}' was found (people lists the villagers)")
        if not person.get("location"):
            raise ActionError(f"{person['name']} isn't around today")
        internal = person.get("internal") or person["name"]

        async def where() -> list[Tile]:
            return [(r["x"], r["y"]) for r in await self.read({"from": "location.npcs", "where": f"internal == '{internal}'", "select": ["x", "y"]})]

        why = "they may be behind a counter or on the move"
        for _ in range(8):
            await self.ready()
            person = await self._find_person(internal) or person
            if await self.read("location.name") != person["location"]:
                await self._travel(person["location"])
                continue
            locked = await self._locked_doors()
            try:
                # they walk about, so keep heading for where they are now; a locked bedroom door is never tried
                reached = await self._walk([], 8, what=person["name"], track=where, blocked=[t for t, _, _ in locked])
            except Interrupted:
                raise
            except ActionError as ex:
                if locked and ("reach" in str(ex) or "path" in str(ex)):
                    names = sorted({n for _, owners, _ in locked for n in owners})
                    best = max(h for _, _, h in locked)
                    raise ActionError(f"{person['name']} is in a room that's locked to you: it opens at 2 hearts with {' or '.join(names)} (you have {best}); try another time or place") from None
                why = str(ex)
                await self.step(30)
                continue
            if reached is None:
                continue
            now = await self.read({"npc": {"from": "location.npcs", "where": f"internal == '{internal}'", "select": ["x", "y"]}, "me": "player.tile"})
            if now["npc"] and next_to((now["npc"][0]["x"], now["npc"][0]["y"]), now["me"]):
                return {**person, "x": now["npc"][0]["x"], "y": now["npc"][0]["y"]}
        raise ActionError(f"couldn't get to {person['name']}: {why}")
