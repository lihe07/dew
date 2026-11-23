"""Longer jobs built from skills: farming chores, fishing, fighting, mining."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from contextlib import contextmanager
from typing import Any

from . import combat
from . import driver as d
from .driver import BridgeError
from .game import FREE, ActionError, Game, Interrupted, Tile, beside

DEBRIS_TOOLS = {"weeds": "scythe", "twig": "axe", "stone": "pickaxe", "tree": "axe", "stump": "axe", "sapling": "axe", "grass": "scythe", "dead": "scythe"}
CLUMP_TOOLS = {"large stump": ("axe", 1), "large log": ("axe", 2), "boulder": ("pickaxe", 2), "meteorite": ("pickaxe", 3), "mine boulder": ("pickaxe", 0)}


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


async def water(g: Game) -> dict:
    await g.ready()
    watered = 0

    async def dry() -> list[Tile]:
        return await tiles(g, "terrain", "thirsty")

    if not await dry():
        return {"watered": 0, "note": "nothing needs water here"}
    await g.hold_tool("watering can")

    async def act(tile: Tile) -> None:
        nonlocal watered
        if (await g.read("player.held"))["water"] <= 0:
            await refill(g)
            await g.goto([tile], 8)
        await g.swing(tile)
        watered += 1

    with progress(lambda: f"watered {watered}"):
        failed = await g.for_nearest(dry, act)
    return {"watered": watered, "still_dry": len(await dry()), **g.unreached(failed)}


async def refill(g: Game) -> None:
    sources = await layer(g, "refill")
    if not sources:
        raise ActionError("the watering can is empty and there's no water here")
    for _ in range(3):
        source = await g.goto(sources, 8, what="water")
        await g.swing(source)
        if (await g.read("player.held"))["water"] > 0:
            return
        sources.remove(source)
    raise ActionError("couldn't refill the watering can")


async def harvest(g: Game) -> dict:
    await g.ready()
    count = 0

    async def ready() -> list[Tile]:
        return await tiles(g, "terrain", "ready and crop != null")

    async def act(tile: Tile) -> None:
        nonlocal count
        scythe = await g.read({"from": "location.terrain", "where": f"x == {tile[0]} and y == {tile[1]}", "select": ["scythe"]})
        if scythe and scythe[0]["scythe"]:
            await g.hold_tool("scythe")
            await g.swing(tile)
        else:
            await g.empty_hands()
            await g.act(tile)
            await g.settle(120)
        if tile not in await ready():
            count += 1
        elif await g.read("player.free_slots") == 0:
            raise ActionError("your inventory is full")

    with progress(lambda: f"harvested {count}"):
        failed = await g.for_nearest(ready, act)
    await g.step(20)
    return {"harvested": count, **g.unreached(failed)}


async def collect(g: Game, kind: str) -> dict:
    await g.ready()
    where = "kind == 'forage'" if kind == "forage" else "kind == 'machine' and ready"
    count = 0

    async def spots() -> list[Tile]:
        return await tiles(g, "object_rows", where)

    async def act(tile: Tile) -> None:
        nonlocal count
        await g.empty_hands()
        await g.act(tile)
        await g.settle(120)
        if tile not in await spots():
            count += 1
        elif await g.read("player.free_slots") == 0:
            raise ActionError("your inventory is full")

    with progress(lambda: f"collected {count}"):
        failed = await g.for_nearest(spots, act)
    await g.step(15)
    return {"collected": count, **g.unreached(failed)}


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
        if await g.goto(xy(animals), 8, what="an animal", track=spots) is None:
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


async def clear(g: Game, area: list[int], kinds: list[str]) -> dict:
    await g.ready()
    x1, x2 = sorted((area[0], area[2]))
    y1, y2 = sorted((area[1], area[3]))
    inside = f"x >= {x1} and x <= {x2} and y >= {y1} and y <= {y2}"
    names = {"weeds": "weeds", "twigs": "twig", "stones": "stone", "trees": "tree", "saplings": "sapling", "grass": "grass"}
    wanted = {names[k] for k in kinds if k in names} | ({"stump"} if "trees" in kinds else set())
    cleared = 0
    levels = {i["kind"].lower(): i["upgrade"] for i in await g.inventory() if i["kind"] in ("Axe", "Pickaxe")}

    async def targets() -> dict[Tile, str]:
        objects, terrain, clumps = await g.read([
            {"from": "location.object_rows", "where": inside + " and (kind == 'weeds' or kind == 'twig' or kind == 'stone')", "select": ["x", "y", "kind"]},
            {"from": "location.terrain", "where": inside, "select": ["x", "y", "kind", "dead", "falling"]},
            {"from": "location.clumps", "where": inside, "select": ["x", "y", "w", "h", "kind"]},
        ])
        found = {(r["x"], r["y"]): r["kind"] for r in objects + terrain if r["kind"] in wanted and not r.get("falling")}
        if "dead_crops" in kinds:
            found.update({(r["x"], r["y"]): "dead" for r in terrain if r.get("dead")})
        for r in clumps:
            tool, level = CLUMP_TOOLS.get(r["kind"], (None, 99))
            group = "boulders" if tool == "pickaxe" else "stumps"
            if tool and group in kinds and levels.get(tool, -1) >= level:
                found.update({(r["x"] + dx, r["y"] + dy): r["kind"] for dx in range(r["w"]) for dy in range(r["h"])})
        return found

    async def listing() -> list[Tile]:
        return list(await targets())

    async def act(tile: Tile) -> None:
        nonlocal cleared
        kind = (await targets()).get(tile)
        if not kind:
            return
        await g.hold_tool(DEBRIS_TOOLS.get(kind) or CLUMP_TOOLS[kind][0])
        for _ in range(45):
            await g.swing(tile)
            now = (await targets()).get(tile)
            if now != kind:
                if kind in ("tree", "sapling"):
                    await g.step(150)  # let it fall
                    if kind == "tree" and (await targets()).get(tile) == "stump":
                        kind = "stump"
                        continue
                cleared += 1
                return

    with progress(lambda: f"cleared {cleared}"):
        failed = await g.for_nearest(listing, act)
    await g.step(20)
    if "trees" in kinds or "stumps" in kinds:
        await gather(g, [x1 - 8, y1 - 8, x2 + 8, y2 + 8])  # trees scatter wood where they fall
    return {"cleared": cleared, "left": len(await targets()), **g.unreached(failed, 10)}


async def gather(g: Game, area: list[int]) -> None:
    """Walk over dropped items."""
    skip: set[Tile] = set()
    for _ in range(40):
        rows = await g.read({"from": "location.debris", "where": "itemId != null and itemId != ''", "select": ["Chunks[0].position"]})
        spots = {(int(p[0] // 64), int(p[1] // 64)) for r in rows if (p := r["Chunks[0].position"])}
        spots = {t for t in spots if area[0] <= t[0] <= area[2] and area[1] <= t[1] <= area[3]} - skip
        if not spots or await g.read("player.free_slots == 0 and player.inventory.count >= player.maxItems"):
            return
        try:
            target = await g.goto(list(spots), 8, what="dropped items")
        except Interrupted:
            raise
        except ActionError:
            return
        await g.step(25)
        if target:
            skip.add(target)


# --- fishing -----------------------------------------------------------------------------------------------

ROD = "player.CurrentTool"


async def fishing_spot(g: Game, reach: int, stand: Tile | None) -> tuple[list[Tile], Any]:
    grid, me = await g.read([{"$grid": {"layers": ["fish", "pass"]}}, "player.tile"])
    fish, walk, w, h = grid["fish"], grid["pass"], grid["w"], grid["h"]
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
                if 0 <= tx < w and 0 <= ty < h and depth[ty][tx] != 0 and (best is None or depth[ty][tx] > best[0]):
                    best = (depth[ty][tx], dx, dy, n)
        return best

    if stand:
        return [stand], cast
    options = []
    for y in range(h):
        for x in range(w):
            if walk[y][x] == "." and fish[y][x] == "0" and (best := cast(x, y)):
                options.append((abs(x - me[0]) + abs(y - me[1]) - best[0] * 8, (x, y)))
    nearby = [t for _, t in sorted(options)[:1500]]
    reachable = [(c - cast(x, y)[0] * 8, (x, y)) for x, y, c in (await g.d.one(d.path(nearby, costs=True)) if nearby else []) if c is not None]
    if not reachable:
        raise ActionError("there's nowhere reachable to fish from here")
    return [min(reachable)[1]], cast


async def fish(g: Game, casts: int, stand: Tile | None = None, power: int | None = None) -> dict:
    await g.ready()
    await g.hold_tool("fishing rod")
    level = (await g.read("player.skills"))["fishing"]
    reach = 4 if level >= 15 else 3 if level >= 8 else 2 if level >= 4 else 1 if level >= 1 else 0
    spots, cast = await fishing_spot(g, reach, stand)
    await g.goto(spots, what="a fishing spot")
    me = await g.here()
    best = cast(*me)
    if not best:
        raise ActionError(f"can't fish from {list(me)}")
    _, dx, dy, distance = best
    # by default, cast as far as the deepest water in reach; deeper water has better fish
    power = min(0.99, (distance + 0.15) / (reach + (4 if dx else 3))) if power is None else max(0.01, min(1.0, power / 100))
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


# --- combat and mining -------------------------------------------------------------------------------------


async def fight(g: Game, radius: int = 12, min_health: int | None = None, max_ticks: int = 3600, orders: "combat.Orders | None" = None) -> dict:
    await g.ready()
    await g.hold_tool("weapon")
    orders = orders or combat.Orders()
    if min_health is not None:
        orders.retreat_below = min_health / await g.read("player.max_health")
    return await combat.Fight(g, orders).run(radius, max_ticks)


async def enter_mine(g: Game) -> None:
    """From anywhere, get to the deepest floor the elevator reaches."""
    if await g.read("location.name") != "Mine":
        await g.travel("Mine")
    deepest = await g.read("player.deepestMineLevel") or 0
    actions = await g.read({"from": "location.actions", "where": "action == 'MineElevator' or action == 'Mine'"})
    elevator = next((a for a in actions if a["action"] == "MineElevator"), None)
    ladder = next((a for a in actions if a["action"] == "Mine"), None)
    target = elevator if deepest >= 5 and elevator else ladder
    if not target:
        raise ActionError("can't find the way into the mines")
    await g.interact([(target["x"], target["y"])])
    if await g.read("menu.type") == "MineElevatorMenu":
        floor = str(min(120, deepest // 5 * 5))
        if not await g.click_component(name=floor, after=30):
            await g.close_menu()
            raise ActionError(f"the elevator doesn't offer floor {floor}")
    await g.settle()
    if await g.read("location.mine_level") is None:
        raise ActionError("couldn't get into the mines")


async def mine_down(g: Game, floors: int = 1, min_energy: int = 30, min_health: int | None = None, tactics: "combat.Tactics | None" = None) -> dict:
    await g.ready()
    if await g.read("location.mine_level") is None:
        await enter_mine(g)
    max_health = await g.read("player.max_health")
    orders = combat.Orders(retreat_below=(min_health / max_health) if min_health is not None else 0.25)
    floor = orders.retreat_below * max_health
    armed = await g.armed()
    result = {"level": 0, "descended": 0, "rocks": 0, "killed": 0}
    while result["descended"] < floors:
        await g.ready()
        state = await g.read({"level": "location.mine_level", "name": "location.name", "ladders": "location.ladders", "health": "player.health", "energy": "player.energy", "me": "player.StandingPixel",
                              "monsters": {"from": "location.monsters", "where": "not hidden"}, "stones": {"from": "location.object_rows", "where": "kind == 'stone'", "select": ["x", "y"]}})
        if state["level"] is None:
            break
        result["level"] = state["level"]
        if state["health"] < orders.eat_below * max_health:
            meal = await combat.Fight(g, orders).food()
            if meal:
                await g.eat(meal["name"])
                result.setdefault("ate", []).append(meal["name"])
                continue
        if state["health"] < floor:
            raise Interrupted(f"health is {state['health']}; {result}")
        close = [m for m in state["monsters"] if abs(m["px"] - state["me"][0]) + abs(m["py"] - state["me"][1]) < 300]
        down = [l for l in state["ladders"] if l["kind"] in ("down", "shaft")]
        if armed and close:
            # tactics: with a way down in sight, fighting is optional
            choice = "fight"
            if down and tactics:
                s = {"hp": state["health"], "max": max_health, "me": state["me"], "monsters": close}
                choice = await tactics.choose(combat.situation(s, {"ladder_down_visible": True, "floors_left": floors - result["descended"]}),
                                              {"fight": "kill the nearby monsters first; safer at high health, gives combat experience and loot",
                                               "descend": "ignore them and take the ladder down now; better at low health or against many monsters"}, "fight")
            if choice == "fight":
                r = await combat.Fight(g, orders).run(5, 900)
                result["killed"] += r["killed"]
                if r["ate"]:
                    result.setdefault("ate", []).extend(r["ate"])
                continue
        if down:
            try:
                target = await g.goto(xy(down), 8, what="the ladder")
            except Interrupted:
                raise
            except ActionError:
                target = None
            if target:
                await g.empty_hands()
                await g.act(target)
                await g.step(60, {"moving": "state.fading", "menu": "state.menu"})
                await g.settle()
                if await g.read("menu.choices"):
                    result["note"] = "a shaft: answer(0) jumps in"
                    return result
                if await g.read("location.name") != state["name"]:
                    result["descended"] += 1
                    result["level"] = await g.read("location.mine_level")
                continue
        if state["energy"] < min_energy:
            raise Interrupted(f"energy is {int(state['energy'])}; {result}")
        if not state["stones"] and state["monsters"] and armed:
            # an infested floor: the way down appears once they're all dead
            result["killed"] += (await fight(g, 80, floor, 6000))["killed"]
            continue
        if not state["stones"]:
            raise ActionError(f"no rocks or ladder on this floor; {result}")
        stones = [[x, y] for x, y in xy(state["stones"])]
        route = await g.d.one(d.path(stones, 8, debris=True))
        if route and route["debris"]:
            # weeds or rocks wall us in, so cut through
            spot = tuple(route["debris"][0])
            kind = await g.read({"from": "location.object_rows", "where": f"x == {spot[0]} and y == {spot[1]}", "select": ["kind"]})
            await g.goto([spot], 8, what="debris")
            await g.hold_tool(DEBRIS_TOOLS.get(kind[0]["kind"] if kind else "stone", "pickaxe"))
            for _ in range(8):
                await g.swing(spot)
                if (await g.read({"$grid": {"rect": [*spot, 1, 1], "layers": ["pass"]}}))["pass"][0] == ".":
                    break
            continue
        await g.hold_tool("pickaxe")
        target = await g.goto(stones, 8, what="a rock")
        for _ in range(20):
            now = await g.read({"rock": {"from": "location.object_rows", "where": f"x == {target[0]} and y == {target[1]} and kind == 'stone'", "select": ["x"]}, "energy": "player.energy", "health": "player.health",
                                "me": "player.StandingPixel", "monsters": {"from": "location.monsters", "where": "not hidden", "select": ["px", "py"]}})
            if not now["rock"]:
                result["rocks"] += 1
                break
            if now["energy"] < 4 or now["health"] < floor or armed and any(abs(m["px"] - now["me"][0]) + abs(m["py"] - now["me"][1]) < 220 for m in now["monsters"]):
                break
            await g.swing(target)
    await g.step(15)
    return result
