"""Longer jobs built from skills: farming chores, gathering (one table of what things are, for gather and find), fishing, the mines."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from contextlib import contextmanager
from typing import Any

from . import combat
from . import driver as d
from .driver import BridgeError
from .game import FREE, ActionError, Game, Interrupted, Tile, beside


@contextmanager
def progress(describe: Callable[[], str]):
    """Include what was finished if a job stops early."""
    try:
        yield
    except Interrupted as ex:
        raise Interrupted(f"{ex} ({describe()})") from None
    except ActionError as ex:
        raise ActionError(f"{ex} ({describe()})") from None


def xy(rows: list[dict]) -> list[Tile]:
    return [(r["x"], r["y"]) for r in rows]


async def tiles(g: Game, table: str, where: str) -> list[Tile]:
    return xy(await g.read({"from": f"location.{table}", "where": where, "select": ["x", "y"], "limit": 4000}))


async def layer(g: Game, name: str) -> list[Tile]:
    grid = await g.read({"$grid": {"layers": [name]}})
    return [(x, y) for y, row in enumerate(grid[name]) for x, c in enumerate(row) if c == "1"]


# --- farming -----------------------------------------------------------------------------------------------


async def water(g: Game, within: set[Tile] | None = None) -> dict:
    """Water the crops which need it (only those within the given tiles, if any), refilling the can at water nearby."""
    await g.ready()
    watered = 0

    async def dry() -> list[Tile]:
        found = await tiles(g, "terrain", "thirsty")
        return [t for t in found if within is None or t in within]

    if not await dry():
        return {"watered": 0, "note": "nothing needs water " + ("in that area" if within else "here")}
    await g.hold_tool("watering can")

    async def act(tile: Tile) -> None:
        nonlocal watered
        if (await g.read("player.held"))["water"] <= 0:
            await refill(g)
            await g.go([tile], beside=True)
        await g.swing(tile)
        watered += 1

    with progress(lambda: f"watered {watered}"):
        visit = await g.visit(dry, act)
    return {"watered": watered, "still_dry": len(await dry()), **visit.report()}


async def till(g: Game, within: set[Tile]) -> dict:
    """Hoe the bare ground among these tiles; tiles already tilled, planted, or covered are left alone."""
    await g.ready()
    diggable = set(await layer(g, "dig"))
    covered = set(await tiles(g, "terrain", "x >= 0")) | set(await tiles(g, "object_rows", "x >= 0"))
    todo = sorted(t for t in within if t in diggable and t not in covered)
    if not todo:
        tilled = len([t for t in within if t in set(await tiles(g, "terrain", "kind == 'dirt'"))])
        return {"tilled": 0, "note": f"nothing to till there ({tilled} of {len(within)} tiles already tilled; the rest can't be dug or are covered)"}
    return {**await gather(g, todo, tool="hoe"), "skipped": len(within) - len(todo)}


async def plant(g: Game, item: str, within: set[Tile]) -> dict:
    """Plant seeds (or place fertilizer) on the tilled, empty soil among these tiles."""
    await g.ready()
    empty = set(await tiles(g, "terrain", "kind == 'dirt' and crop == null")) - set(await tiles(g, "object_rows", "x >= 0"))
    todo = sorted(t for t in within if t in empty)
    if not todo:
        raise ActionError(f"no tilled, empty soil among those {len(within)} tiles: till them first, or pick other tiles")
    return {**await g.place(item, todo), "skipped": len(within) - len(todo)}


async def refill(g: Game) -> None:
    sources = await layer(g, "refill")
    if not sources:
        raise ActionError("the watering can is empty and there's no water here")
    for _ in range(3):
        source = (await g.go(sources, beside=True, what="water")).target
        await g.swing(source)
        if (await g.read("player.held"))["water"] > 0:
            return
        sources.remove(source)
    raise ActionError("couldn't refill the watering can")


async def pet(g: Game) -> dict:
    await g.ready()
    petted, tries = [], {}

    async def unpetted() -> list[dict]:
        return [a for a in await g.read("location.animal_rows") if not a["petted"] and tries.get(a["name"], 0) < 4]

    async def spots() -> list[Tile]:
        return xy(await unpetted())

    for _ in range(150):
        animals = await unpetted()
        if not animals:
            break
        # they wander, so follow them as we go and pet whichever is beside us on arrival
        if (await g.go(spots, beside=True, what="an animal")).target is None:
            break
        me = await g.here()
        animal = next((a for a in await unpetted() if beside((a["x"], a["y"]), me)), None)
        if not animal:
            continue
        await g.empty_hands()
        await g.act((animal["x"], animal["y"]))
        await g.step(20)
        await g.settle(60)
        if any(a["name"] == animal["name"] and a["petted"] for a in await g.read("location.animal_rows")):
            petted.append(animal["name"])
        else:
            tries[animal["name"]] = tries.get(animal["name"], 0) + 1  # it probably wandered off
    return {"petted": petted}


# --- gathering ---------------------------------------------------------------------------------------------

# Every kind of thing gather takes and find finds, and the tool each takes (None: by hand, or for items, by walking over them).
TOOLS = {"stone": "pickaxe", "ore": "pickaxe", "gems": "pickaxe", "boulders": "pickaxe", "trees": "axe", "saplings": "axe", "stumps": "axe", "logs": "axe",
         "twigs": "axe", "weeds": "scythe", "grass": "scythe", "dead_crops": "scythe", "dig_spots": "hoe", "forage": None, "crops": None, "machines": None, "items": None}
GROUPS = {"rocks": ("stone", "ore", "gems"),
          "debris": ("weeds", "twigs", "stone", "ore", "gems", "grass", "saplings", "trees", "stumps", "logs", "boulders", "dead_crops")}
KINDS = [*TOOLS, *GROUPS]
ORE = {"(O)751": "copper", "(O)849": "copper", "(O)290": "iron", "(O)850": "iron", "(O)764": "gold", "(O)765": "iridium", "(O)95": "radioactive"}
GEMS = {"(O)2": "diamond", "(O)4": "ruby", "(O)6": "jade", "(O)8": "amethyst", "(O)10": "topaz", "(O)12": "emerald", "(O)14": "aquamarine", "(O)44": "gem", "(O)46": "mystic"}
CLUMPS = {"large stump": ("stumps", 1), "large log": ("logs", 2), "boulder": ("boulders", 2), "meteorite": ("boulders", 3), "mine boulder": ("boulders", 0)}  # and the tool level each needs
OBJECTS = {"weeds": "weeds", "twig": "twigs", "forage": "forage", "artifact_spot": "dig_spots"}
TERRAIN = {"tree": "trees", "sapling": "saplings", "stump": "stumps", "grass": "grass"}
DROPS = {"items", "forage", "crops", "machines"}  # what's taken by hand: a full backpack stops it


def kinds(names: list[str]) -> set[str]:
    """The kinds a list of names stands for, groups opened up."""
    return {k for n in names for k in GROUPS.get(n, (n,))}


async def scan(g: Game, wanted: set[str], area: list[int] | None = None) -> dict[Tile, dict]:
    """What of the wanted kinds is where, here (or inside an area): tile -> {"kind", "what"}, plus "level" (the tool level big
    debris needs) and "scythe" (a crop cut with it). A big stump or boulder is on each of its tiles."""
    where = "true" if area is None else f"x >= {min(area[0], area[2])} and x <= {max(area[0], area[2])} and y >= {min(area[1], area[3])} and y <= {max(area[1], area[3])}"
    asks = {}
    if wanted & {"weeds", "twigs", "stone", "ore", "gems", "forage", "dig_spots", "machines"}:
        asks["objects"] = {"from": "location.object_rows", "select": ["x", "y", "kind", "id", "name"], "limit": 4000,
                           "where": f"({where}) and (kind == 'weeds' or kind == 'twig' or kind == 'stone' or kind == 'forage' or kind == 'artifact_spot' or (kind == 'machine' and ready))"}
    if wanted & {"trees", "saplings", "stumps", "grass", "dead_crops", "crops"}:
        asks["terrain"] = {"from": "location.terrain", "where": where, "select": ["x", "y", "kind", "dead", "ready", "crop", "falling", "scythe"], "limit": 4000}
    if wanted & {"stumps", "logs", "boulders"}:
        asks["clumps"] = {"from": "location.clumps", "where": where, "select": ["x", "y", "w", "h", "kind"]}
    if "items" in wanted:
        asks["items"] = {"from": "location.debris", "where": "itemId != null and itemId != ''", "select": ["itemId", "Chunks[0].position"]}
    found: dict[Tile, dict] = {}
    rows = await g.read(asks) if asks else {}
    for r in rows.get("items", []):
        if p := r["Chunks[0].position"]:
            t = (int(p[0] // 64), int(p[1] // 64))
            if area is None or min(area[0], area[2]) <= t[0] <= max(area[0], area[2]) and min(area[1], area[3]) <= t[1] <= max(area[1], area[3]):
                found[t] = {"kind": "items", "what": r["itemId"]}
    for r in rows.get("clumps", []):
        kind, level = CLUMPS.get(r["kind"], (None, 0))
        found.update({(r["x"] + dx, r["y"] + dy): {"kind": kind, "what": r["kind"], "level": level} for dx in range(r["w"]) for dy in range(r["h"])} if kind else {})
    for r in rows.get("terrain", []):
        if r.get("falling"):
            continue
        kind = TERRAIN.get(r["kind"])
        if r["kind"] == "dirt" and r.get("crop"):
            kind = "dead_crops" if r.get("dead") else "crops" if r.get("ready") else None
        if kind:
            found[(r["x"], r["y"])] = {"kind": kind, "what": r.get("crop") or r["kind"], "scythe": bool(r.get("scythe"))}
    for r in rows.get("objects", []):
        kind = "machines" if r["kind"] == "machine" else OBJECTS.get(r["kind"])
        what = r["name"]
        if r["kind"] == "stone":
            kind, what = ("ore", ORE[r["id"]]) if r["id"] in ORE else ("gems", GEMS[r["id"]]) if r["id"] in GEMS else ("stone", "stone")
        found[(r["x"], r["y"])] = {"kind": kind, "what": what}
    return {t: v for t, v in found.items() if v["kind"] in wanted}


def _stacks(items: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for i in items:
        out[i["name"]] = out.get(i["name"], 0) + (i.get("stack") or 1)
    return out


class Full(ActionError):
    """The backpack is full, so what's here can't be taken."""


async def take_tool(g: Game, name: str) -> None:
    """Hold a tool; weeds and grass are cut with a weapon when there's no scythe."""
    try:
        await g.hold_tool(name)
    except ActionError:
        if name != "scythe":
            raise
        await g.hold_tool("weapon")


async def gather(g: Game, targets: set[str] | list[Tile], area: list[int] | None = None, tool: str | None = None) -> dict:
    """Take, break, cut, dig or collect every target: kinds (from KINDS, groups opened) here or inside an area, or these tiles.
    Nearest first through the visitor; each gets the tool what's on it needs (tool overrides; a tile with nothing known on it gets
    one swing of the tool). Then what the targets dropped is picked up. Returns {"done", "got": {item: count}}, plus "left" and
    "why_left" for what's still there: out of reach, too big for your tools, or no room in the backpack."""
    await g.ready()
    before = _stacks(await g.inventory())
    listed = None if isinstance(targets, set) else list(dict.fromkeys(tuple(t) for t in targets))
    wanted = set(TOOLS) if listed is not None else targets
    if listed is not None and area is None:
        xs, ys = [t[0] for t in listed], [t[1] for t in listed]
        area = [min(xs), min(ys), max(xs), max(ys)]
    levels = {i["kind"].lower(): i["upgrade"] for i in await g.inventory() if i["kind"] in ("Axe", "Pickaxe")}
    swung: set[Tile] = set()
    touched: list[Tile] = []
    weak: set[Tile] = set()  # big debris the tools can't break yet
    full, done = False, 0

    async def todo() -> list[Tile]:
        found = await scan(g, wanted, area)
        out = []
        for t, thing in found.items():
            if listed is not None and t not in listed:
                continue
            if "level" in thing and levels.get(TOOLS[thing["kind"]], -1) < thing["level"]:
                weak.add(t)
                continue
            out.append(t)
        if listed is not None and tool:
            out += [t for t in listed if t not in found and t not in swung]
        return out

    async def act(tile: Tile) -> str | None:
        nonlocal full, done
        near = [tile[0] - 2, tile[1] - 2, tile[0], tile[1]]  # a big stump's corner is up to 2 tiles up and left of it
        thing = (await scan(g, wanted, near)).get(tile)
        touched.append(tile)
        if thing is None:
            if tool and listed is not None and tile not in swung:
                swung.add(tile)
                await g.hold_tool(tool)
                await g.swing(tile)
                done += 1
            return None
        kind = thing["kind"]
        if TOOLS[kind] or thing.get("scythe"):
            await take_tool(g, tool or TOOLS[kind] or "scythe")
            idle = 0  # swings in a row which cost no energy: they didn't land (the axe, pickaxe and hoe always cost some)
            for _ in range(45):
                energy = await g.read("player.energy")
                await g.swing(tile)
                idle = idle + 1 if await g.read("player.energy") >= energy and (tool or TOOLS[kind]) in ("axe", "pickaxe", "hoe") else 0
                if idle >= 3:
                    return "the swings didn't land on it"
                now = (await scan(g, wanted, near)).get(tile)
                if not now or now["kind"] != kind:
                    if kind in ("trees", "saplings"):
                        await g.step(150)  # let it fall
                        if kind == "trees" and ((await scan(g, wanted, near)).get(tile) or {}).get("kind") == "stumps":
                            kind = "stumps"
                            continue
                    done += 1
                    return None
                await g.guard.watch(g)
            return "still there after 45 swings"
        if kind == "items":
            await g.step(25)  # beside it, the game pulls it in
        else:
            await g.empty_hands()
            await g.act(tile)
            await g.settle(120)
        if not (await scan(g, wanted, near)).get(tile):
            done += 1
        elif await g.read("player.free_slots") == 0:
            full = True
            raise Full("the backpack is full")
        return None

    visit = None
    with progress(lambda: f"done {done}"):
        for _ in range(5):
            try:
                visit = await g.visit(todo, act)
            except Full:
                break
            if "debris is in the way" not in " ".join(visit.skipped.values()) or not await cut_through(g, [list(t) for t in visit.skipped]):
                break
    await g.step(20)
    if touched and not full:
        xs, ys = [t[0] for t in touched], [t[1] for t in touched]
        reach = 8 if wanted & {"trees", "stumps", "logs"} else 3  # trees scatter wood where they fall
        full = await pickup(g, [min(xs) - reach, min(ys) - reach, max(xs) + reach, max(ys) + reach])
    after = _stacks(await g.inventory())
    result: dict[str, Any] = {"done": done, "got": {k: n - before.get(k, 0) for k, n in after.items() if n > before.get(k, 0)}}
    left = sorted({*await todo(), *weak} - swung)
    if left:
        why = [w for w in dict.fromkeys((visit.skipped if visit else {}).values())]
        if full:
            why.insert(0, "the backpack is full")
        if weak:
            why.append(f"{len(weak)} tiles of big debris need a better tool")
        result |= {"left": [list(t) for t in left[:20]], "why_left": "; ".join(why)[:300] or "still there"}
    elif full:
        result["why_left"] = "the backpack is full: some drops are still on the ground"
    return result


async def cut_through(g: Game, goals: list) -> bool:
    """Weeds or rocks wall us in, so break the first one on the way to the goals. Returns whether it opened."""
    route = await g.d.one(d.path(goals, 8, debris=True))
    if not (route and route["debris"]):
        return False
    spot = tuple(route["debris"][0])
    thing = (await scan(g, set(TOOLS), [spot[0] - 2, spot[1] - 2, spot[0], spot[1]])).get(spot)
    await g.go([spot], beside=True, what="debris")
    await take_tool(g, (TOOLS.get(thing["kind"]) or "pickaxe") if thing else "pickaxe")
    for _ in range(8):
        await g.swing(spot)
        if (await g.read({"$grid": {"rect": [*spot, 1, 1], "layers": ["pass"]}}))["pass"][0] == ".":
            return True
    return False


async def pickup(g: Game, area: list[int]) -> bool:
    """Walk over the items lying inside an area, so they're picked up. Returns whether the backpack filled first."""
    skip: set[Tile] = set()
    for _ in range(40):
        spots = set(await scan(g, {"items"}, area)) - skip
        if not spots:
            return False
        if await g.read("player.free_slots == 0 and player.inventory.count >= player.maxItems"):
            return True
        try:
            target = (await g.go(list(spots), beside=True, what="dropped items")).target
        except Interrupted:
            raise
        except ActionError:
            return False
        await g.step(25)
        if target:
            skip.add(target)
    return False


# --- fishing -----------------------------------------------------------------------------------------------

ROD = "player.CurrentTool"


async def fishing_spot(g: Game, reach: int, at: Tile | None = None) -> tuple[list[Tile], Any]:
    """Where to stand, and how to cast from a tile: into the deepest water in reach, or, with at, into that water tile."""
    grid, me = await g.read([{"$grid": {"layers": ["fish", "pass"]}}, "player.tile"])
    fish, walk, w, h = grid["fish"], grid["pass"], grid["w"], grid["h"]
    if at and not (0 <= at[0] < w and 0 <= at[1] < h and fish[at[1]][at[0]] != "0"):
        raise ActionError(f"{list(at)} isn't water you can fish in")
    depth = [[0 if fish[y][x] == "0" else -1 for x in range(w)] for y in range(h)]
    queue = deque((x, y) for y in range(h) for x in range(w) if depth[y][x] == 0)
    while queue:
        x, y = queue.popleft()
        for dx, dy in ((0, 1), (1, 0), (0, -1), (-1, 0)):
            if 0 <= x + dx < w and 0 <= y + dy < h and depth[y + dy][x + dx] < 0:
                depth[y + dy][x + dx] = depth[y][x] + 1
                queue.append((x + dx, y + dy))

    def cast(x: int, y: int) -> tuple[int, int, int, int] | None:
        best = None
        for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0)):
            for n in range(2, reach + (4 if dx else 3) + 1):
                tx, ty = x + dx * n, y + dy * n
                if at and (tx, ty) != tuple(at):
                    continue
                if 0 <= tx < w and 0 <= ty < h and depth[ty][tx] != 0 and (best is None or depth[ty][tx] > best[0]):
                    best = (depth[ty][tx], dx, dy, n)
        return best

    options = []
    for y in range(h):
        for x in range(w):
            if walk[y][x] == "." and fish[y][x] == "0" and (best := cast(x, y)):
                options.append((abs(x - me[0]) + abs(y - me[1]) - best[0] * 8, (x, y)))
    nearby = [t for _, t in sorted(options)[:1500]]
    reachable = [(c - cast(x, y)[0] * 8, (x, y)) for x, y, c in (await g.d.one(d.path(nearby, costs=True)) if nearby else []) if c is not None]
    if not reachable:
        raise ActionError(f"there's nowhere reachable to cast to {list(at)} from" if at else "there's nowhere reachable to fish from here")
    return [min(reachable)[1]], cast


async def fish(g: Game, casts: int, at: Tile | None = None) -> dict:
    await g.ready()
    await g.hold_tool("fishing rod")
    level = (await g.read("player.skills"))["fishing"]
    reach = 4 if level >= 15 else 3 if level >= 8 else 2 if level >= 4 else 1 if level >= 1 else 0
    spots, cast = await fishing_spot(g, reach, at)
    await g.go(spots, what="a fishing spot")
    me = await g.here()
    best = cast(*me)
    if not best:
        raise ActionError(f"can't fish from {list(me)}")
    _, dx, dy, distance = best
    power = min(0.99, (distance + 0.15) / (reach + (4 if dx else 3)))  # as far as the deepest water in reach: deeper water has better fish
    result: dict[str, Any] = {"spot": list(me), "casts": 0, "caught": 0, "got_away": 0}

    for _ in range(casts):
        await g.ready()
        energy, slots = await g.read(["player.energy", "player.free_slots"])
        if energy < 10:
            result["note"] = "stopped: low energy"
            break
        if slots == 0:
            result["note"] = "stopped: inventory full"
            break
        result["casts"] += 1
        await g.d.run(
            d.input(tile=(me[0] + dx, me[1] + dy), buttons=[]), d.step(2), d.input(buttons=["mouse_left"]),
            d.step(40, f"{ROD}.isTimingCast"), d.step(300, f"{ROD}.castingPower >= {power}"), d.input(buttons=[]),
            d.step(400, {"water": f"{ROD}.isFishing", "free": FREE}),
        )
        if not await g.read(f"{ROD}.isFishing"):
            raise ActionError("the cast didn't land in water; try another spot")
        bite = await g.step(4000, {"bite": f"{ROD}.isNibbling", "menu": "state.menu", "free": FREE})
        if bite["fired"] != "bite":
            continue
        await g.d.run(d.step(6), d.input(buttons=["mouse_left"]), d.step(2), d.input(buttons=[]))
        caught = played = False
        for _ in range(60):
            now = await g.step(300, {"bar": "menu != null and menu.type == 'BobberBar'", "caught": f"{ROD}.fishCaught", "free": FREE, "menu": "menu != null and menu.type != 'BobberBar'"})
            if now["fired"] == "bar":
                played = True
                try:
                    await g.d.one({"op": "cheat", "what": "catch_fish"})  # the player allowed skipping the minigame
                except BridgeError:
                    pass  # the world runs on, so it may have closed meanwhile
            elif now["fired"] == "caught":
                caught = True
                await g.d.run(d.step(40), d.input(buttons=["mouse_left"]), d.step(2), d.input(buttons=[]), d.step(10))
            elif now["fired"] == "menu":
                if await g.read("menu.type") != "ItemGrabMenu":
                    break
                await g.step(30)
                await g.container("take")
                await g.close_menu()
                result["treasure"] = True
            elif now["fired"] == "free":
                break
        result["caught" if caught else "got_away"] += 1 if caught or played else 0
    return result


# --- combat and the mines ----------------------------------------------------------------------------------


async def fight(g: Game, radius: int = 12, max_ticks: int = 3600) -> dict:
    """Clear the monsters within radius tiles now, then pick up their loot."""
    await g.ready()
    before = _stacks(await g.inventory())
    result = await g.guard.fight_off(g, radius, max_ticks, restore=False)
    x, y = await g.here()
    await pickup(g, [x - radius, y - radius, x + radius, y + radius])
    after = _stacks(await g.inventory())
    return {**result, "got": {k: n - before.get(k, 0) for k, n in after.items() if n > before.get(k, 0)}}


async def enter_mine(g: Game, to: int | None) -> None:
    """From anywhere, into the mines: by the elevator to floor to (by default the deepest stop reached), else down the ladder."""
    if await g.read("location.name") != "Mine":
        await g.go("Mine")
    deepest = await g.read("player.deepestMineLevel") or 0
    if to is not None and (to % 5 or to > deepest):
        raise ActionError(f"the elevator stops every 5 floors you've reached (the deepest is {deepest // 5 * 5}), not at {to}")
    actions = await g.read({"from": "location.actions", "where": "action == 'MineElevator' or action == 'Mine'"})
    elevator = next((a for a in actions if a["action"] == "MineElevator"), None)
    ladder = next((a for a in actions if a["action"] == "Mine"), None)
    target = elevator if (to or deepest) >= 5 and elevator else ladder
    if not target:
        raise ActionError("can't find the way into the mines")
    await g.interact([(target["x"], target["y"])])
    if await g.read("menu.type") == "MineElevatorMenu":
        floor = str(min(120, to or deepest // 5 * 5))
        if not await g.click_component(name=floor, after=30):
            await g.close_menu()
            raise ActionError(f"the elevator doesn't offer floor {floor}")
    await g.settle()
    if await g.read("location.mine_level") is None:
        raise ActionError("couldn't get into the mines")


async def descend(g: Game, to: int | None = None) -> dict:
    """Down a floor: into the mines from outside (the elevator's deepest stop, or floor to), else the nearest ladder down, or a
    shaft (jumped into) when there's no ladder. With monsters close and a way down in sight, the tactics model may choose to go
    for the ladder rather than fight first."""
    await g.ready()
    if to is not None or await g.read("location.mine_level") is None:
        if await g.read("location.mine_level") is not None:
            await g.go("Mine")  # up and out, to the elevator
        await enter_mine(g, to)
        return {"level": await g.read("location.mine_level")}
    state = await g.read({"ladders": "location.ladders", "name": "location.name", "health": "player.health", "max": "player.max_health", "me": "player.StandingPixel",
                          "monsters": {"from": "location.monsters", "where": "not hidden"}})
    ladders = [l for l in state["ladders"] if l["kind"] == "down"] or [l for l in state["ladders"] if l["kind"] == "shaft"]
    if not ladders:
        raise ActionError("no ladder or shaft in sight; break rocks to find one")
    close = [m for m in state["monsters"] if abs(m["px"] - state["me"][0]) + abs(m["py"] - state["me"][1]) < 300]
    guard, mode = g.guard, g.guard.fight
    if close and guard.tactics and await g.armed():
        # with a way down in sight, fighting is optional
        s = {"hp": state["health"], "max": state["max"], "me": state["me"], "monsters": close}
        choice = await guard.tactics.choose(combat.situation(s, {"ladder_down_visible": True}),
                                            {"fight": "kill the nearby monsters first; safer at high health, gives combat experience and loot",
                                             "descend": "ignore them and take the ladder down now; better at low health or against many monsters"}, "fight")
        if choice == "fight":
            await guard.watch(g)
        else:
            guard.fight = "off"  # past them to the ladder; the guard still eats or flees when hurt
    try:
        target = (await g.go(xy(ladders), beside=True, what="the ladder")).target
    finally:
        guard.fight = mode
    await g.empty_hands()
    await g.act(target)
    await g.step(60, {"moving": "state.fading", "menu": "state.menu"})
    await g.settle()
    if await g.read("menu.choices"):
        await g.dialogue(0)  # a shaft: jump in
        await g.settle()
    if await g.read("location.name") == state["name"]:
        raise ActionError("took the ladder, but stayed on this floor")
    return {"level": await g.read("location.mine_level")}
