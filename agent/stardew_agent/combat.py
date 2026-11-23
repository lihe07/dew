"""Fighting in two fast layers: reflexes run every tick in code, and tactics are decided by a fast model on events.

Neither waits for the planner. Hard limits (when to eat, when to retreat) are set by the caller and always win.
"""

from __future__ import annotations

import asyncio
import math
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from . import driver as d
from .game import ActionError, Game, Interrupted

REACH = 88  # pixels between centers at which a swing lands
TOO_CLOSE = 56
WINDUP = 5  # ticks from pressing the button to the hit
TRACE = bool(os.environ.get("STARDEW_COMBAT_TRACE"))

TICK = {
    "me": "player.StandingPixel", "tile": "player.tile", "hp": "player.health", "max": "player.max_health", "busy": "player.usingTool", "menu": "state.menu", "eating": "player.isEating",
    "monsters": {"from": "location.monsters", "where": "not hidden", "select": ["name", "x", "y", "px", "py", "hp", "raw.shellGone", "raw.invincibleCountdown"]},
}


@dataclass
class Orders:
    """Limits set by the planner; reflexes and tactics must respect them."""

    eat_below: float = 0.5  # fraction of max health
    retreat_below: float = 0.25


class Tactics:
    """Asks the fast decision model (decide.Decider) to choose between options which are all already safe."""

    def __init__(self, decide: Callable[[dict, dict[str, str]], Awaitable[tuple[str | None, float]]] | None, min_confidence: float = 0.4):
        self.decide, self.min_confidence = decide, min_confidence
        self.log: list[str] = []

    async def choose(self, state: dict, options: dict[str, str], default: str) -> str:
        """Pick one option, or the default if the model is unsure, slow, or unavailable."""
        if len(options) == 1 or not self.decide:
            return default
        choice, confidence = await self.decide(state, options)
        if choice is None or confidence < self.min_confidence:
            self.log.append(f"tactics unsure ({choice} at {confidence:.2f}); chose {default}")
            return default
        self.log.append(f"tactics chose {choice} ({confidence:.2f})")
        return choice


@dataclass
class Fight:
    """The reflex layer: one decision per game tick."""

    g: Game
    orders: Orders = field(default_factory=Orders)
    killed: int = 0
    ate: list[str] = field(default_factory=list)
    _grid: list[str] | None = None
    _origin: tuple[int, int] = (0, 0)
    _route: list = field(default_factory=list)
    count: int = 0
    _last: dict = field(default_factory=dict)
    _stuck: dict = field(default_factory=dict)  # ticks each monster has gone unhurt in reach
    ignored: set = field(default_factory=set)

    async def food(self) -> dict | None:
        """The cheapest thing that heals, so valuables aren't eaten."""
        items = [i for i in await self.g.inventory() if (i.get("healing") or 0) > 0]
        return min(items, key=lambda i: (i.get("price") or 0) / max(1, i["healing"]), default=None)

    async def look(self, keys: list[str], cursor: tuple | None = None, ticks: int = 1) -> dict:
        ops = [d.input(buttons=keys, tile=cursor) if cursor else d.input(buttons=keys), d.step(ticks), d.read(TICK)]
        return (await self.g.d.run(*ops))[-1]

    async def walls(self) -> None:
        grid = await self.g.read({"$grid": {"layers": ["pass"]}})
        self._grid, self._origin = grid["pass"], (grid["x"], grid["y"])

    def open(self, x: int, y: int) -> bool:
        gy, gx = y - self._origin[1], x - self._origin[0]
        return bool(self._grid) and 0 <= gy < len(self._grid) and 0 <= gx < len(self._grid[gy]) and self._grid[gy][gx] == "."

    def keys_toward(self, dx: float, dy: float) -> list[str]:
        keys = self.g.keys
        out = []
        if abs(dx) > 6:
            out.append(keys["right"] if dx > 0 else keys["left"])
        if abs(dy) > 6:
            out.append(keys["down"] if dy > 0 else keys["up"])
        return out

    def escape(self, s: dict, threats: list[dict]) -> list[str]:
        """Step to the open neighbouring tile farthest from the monsters."""
        tx, ty = s["tile"]
        best, score = None, -1.0
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if (dx or dy) and self.open(tx + dx, ty + dy) and (not (dx and dy) or (self.open(tx + dx, ty) and self.open(tx, ty + dy))):
                    gap = min(math.dist(((tx + dx) * 64 + 32, (ty + dy) * 64 + 32), (m["px"], m["py"])) for m in threats)
                    if gap > score:
                        best, score = (dx, dy), gap
        return self.keys_toward(best[0] * 64, best[1] * 64) if best else []

    async def run(self, radius: float = 6, max_ticks: int = 3600, targets: set[str] | None = None) -> dict:
        """Fight until no monster is within radius tiles. Raises Interrupted at the retreat limit."""
        g = self.g
        await self.walls()
        s = await self.look([])
        ticks, futile, total = 0, 0, None
        while ticks < max_ticks:
            if s["menu"]:
                await g.ready()
                s = await self.look([])
                continue
            max_hp = s["max"]
            if s["hp"] < max_hp * self.orders.eat_below and not s["eating"]:
                meal = await self.food()
                if meal and (s["hp"] < max_hp * self.orders.retreat_below or not any(math.dist(s["me"], (m["px"], m["py"])) < 2 * REACH for m in s["monsters"])):
                    await g.eat(meal["name"])
                    self.ate.append(meal["name"])
                    s = await self.look([])
                    continue
                if s["hp"] < max_hp * self.orders.retreat_below:
                    raise Interrupted(f"retreating: health is {s['hp']}/{max_hp} and there's no food (killed {self.killed})")
            # where each monster will be when a swing started now would land
            for m in s["monsters"]:
                prev = self._last.get(m["name"] + str(len(s["monsters"])))
                m["vx"], m["vy"] = (m["px"] - prev[0], m["py"] - prev[1]) if prev else (0, 0)
            self._last = {m["name"] + str(len(s["monsters"])): (m["px"], m["py"]) for m in s["monsters"]}
            near = sorted((math.dist(s["me"], (m["px"], m["py"])), i, m) for i, m in enumerate(s["monsters"]) if (not targets or m["name"] in targets) and (m["name"], m["x"] // 4, m["y"] // 4) not in self.ignored)
            near = [(dist, m) for dist, _, m in near if dist <= radius * 64]
            if not near:
                break
            hp = sum(m["hp"] for m in s["monsters"])
            futile = 0 if total is None or hp < total else futile + 1
            if total is not None and len(s["monsters"]) < self.count:
                self.killed += self.count - len(s["monsters"])
            self.count, total = len(s["monsters"]), hp
            distance, target = near[0]
            if futile > 360:
                # out of reach (on a wall, behind rocks): leave it be
                self.ignored.add((target["name"], target["x"] // 4, target["y"] // 4))
                futile, total = 0, None
                continue
            if TRACE:
                print(f"t{ticks} hp{s['hp']} me{s['me']} tile{s['tile']} busy{s['busy']} -> {target['name']} hp{target['hp']} d{distance:.0f} inv{target.get('raw.invincibleCountdown')} at{target['px'],target['py']} futile{futile}", flush=True)
            ahead = math.dist(s["me"], (target["px"] + target["vx"] * WINDUP, target["py"] + target["vy"] * WINDUP))
            closing = ahead < distance - 4
            if s["busy"]:
                s = await self.look([])
            elif min(distance, ahead) <= REACH and not (target.get("raw.invincibleCountdown") or 0) > 120:
                s = await self.strike(s, target, futile)
            elif distance < TOO_CLOSE:
                s = await self.look(self.escape(s, [m for _, m in near]), ticks=4)
            elif closing and distance < 4 * 64:
                s = await self.look([])  # let it come into the swing
            else:
                s = await self.approach(s, target)
            ticks += 1
        return {"killed": self.killed, "ate": self.ate}

    async def strike(self, s: dict, target: dict, futile: int) -> dict:
        g = self.g
        tx, ty = s["tile"]
        gap = (target["x"] - tx, target["y"] - ty)
        sx, sy = (gap[0] > 0) - (gap[0] < 0), (gap[1] > 0) - (gap[1] < 0)
        if futile > 60 and sx and sy:
            # a rock between us blocks the hit: break it
            for rock in ((tx + sx, ty), (tx, ty + sy)):
                if await g.read({"from": "location.object_rows", "where": f"x == {rock[0]} and y == {rock[1]} and kind == 'stone'", "select": ["x"]}):
                    await g.hold_tool("pickaxe")
                    await g.swing(rock)
                    await self.walls()
                    return await self.look([])
        # rock crabs hide in a shell which only a pickaxe breaks
        shelled = "Crab" in target["name"] and not target.get("raw.shellGone")
        await g.hold_tool("pickaxe" if shelled else "weapon")
        aim = (tx + sx, ty + sy) if (sx or sy) else (tx, ty + (1 if target["py"] > s["me"][1] else -1))
        await g.d.run(d.input(tile=aim, buttons=[]), d.step(1), d.input(buttons=["mouse_left"]), d.step(2), d.input(buttons=[]))
        return await self.look([])

    async def approach(self, s: dict, target: dict) -> dict:
        tx, ty = s["tile"]
        goal = (target["x"], target["y"])
        # straight at it when the way is clear, else along a path
        steps = max(abs(goal[0] - tx), abs(goal[1] - ty))
        clear = all(self.open(round(tx + (goal[0] - tx) * k / steps), round(ty + (goal[1] - ty) * k / steps)) for k in range(1, steps)) if steps > 1 else True
        if clear:
            return await self.look(self.keys_toward(target["px"] - s["me"][0], target["py"] - s["me"][1]), ticks=3)
        if not self._route or self._route[-1] != list(goal) or tuple(self._route[0]) != tuple(s["tile"]):
            found = await self.g.d.one(d.path([goal], 8, avoid=False))
            self._route = found["path"] if found else []
        if len(self._route) < 2:
            return await self.look([], ticks=10)  # can't reach it, so let it come
        nx, ny = self._route[1]
        if (nx, ny) == tuple(s["tile"]):
            self._route.pop(0)
        return await self.look(self.keys_toward(nx * 64 + 32 - s["me"][0], ny * 64 + 32 - s["me"][1]), ticks=3)


def situation(s: dict, extra: dict) -> dict:
    """What the tactics model sees: small and concrete."""
    me = s["me"]
    return {
        "health_percent": round(100 * s["hp"] / s["max"]),
        "monsters_nearby": [{"name": m["name"], "health": m["hp"], "distance_tiles": round(math.dist(me, (m["px"], m["py"])) / 64, 1)} for m in sorted(s["monsters"], key=lambda m: math.dist(me, (m["px"], m["py"])))[:6]],
        **extra,
    }


async def keep_busy(g: Game, stop: asyncio.Event, orders: Orders | None = None) -> dict:
    """Work while the planner thinks, until stop is set: the world keeps running, so guard against monsters, and in the mines break nearby rocks."""
    orders = orders or Orders()
    done = {"killed": 0, "rocks": 0, "ticks": 0}
    while not stop.is_set():
        if g.d.human:
            await asyncio.sleep(0.2)  # the player is playing; only watch
            continue
        s = await g.read({"free": "state.free", "mine": "location.mine_level", "me": "player.StandingPixel", "tile": "player.tile", "energy": "player.energy",
                          "hp": "player.health", "max": "player.max_health", "weapon": {"from": "player.inventory", "where": "kind == 'Weapon'", "select": ["name"]},
                          "monsters": {"from": "location.monsters", "where": "not hidden", "select": ["px", "py"]}})
        if not s["free"]:
            await g.step(10)
            continue
        close = [m for m in s["monsters"] if math.dist(s["me"], (m["px"], m["py"])) < 4 * 64]
        if close and s["hp"] <= orders.retreat_below * s["max"]:
            # too hurt to fight: eat if we can, else keep away from them
            fight = Fight(g, orders)
            meal = await fight.food()
            if meal:
                await g.eat(meal["name"])
                done["ate"] = done.get("ate", 0) + 1
                continue
            if s["mine"] is not None:
                # no food, hurt, and monsters close: get out rather than wait for orders
                try:
                    await g.leave_mine()
                    done["left the mine"] = 1
                except ActionError:
                    pass
                else:
                    continue
            await fight.walls()
            await fight.look(fight.escape(s, close), ticks=8)
            await fight.look([])
            done["fled"] = done.get("fled", 0) + 1
            continue
        if close and s["weapon"]:
            await g.hold_tool("weapon")
            fight = Fight(g, orders)
            try:
                await fight.run(radius=4, max_ticks=60)  # short, so stopping is quick
            except Interrupted:
                continue  # too hurt: the next round eats or flees
            finally:
                done["killed"] += fight.killed
            continue
        if s["mine"] is not None and s["energy"] > 40:
            tx, ty = s["tile"]
            rocks = await g.read({"from": "location.object_rows", "where": f"kind == 'stone' and x >= {tx - 1} and x <= {tx + 1} and y >= {ty - 1} and y <= {ty + 1}", "select": ["x", "y"]})
            if rocks:
                await g.hold_tool("pickaxe")
                await g.swing((rocks[0]["x"], rocks[0]["y"]))
                done["rocks"] += 1
                continue
        await g.step(10)
        done["ticks"] += 10
    return {k: v for k, v in done.items() if v}
