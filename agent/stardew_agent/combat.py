"""Fighting in two fast layers: reflexes run every tick in code, and tactics are decided by a fast model on events.

Neither waits for the agent. The Guard holds the agent's settings (guard()) and is the one reflex that watches for monsters:
Game.step, every walk segment and the visitor call Guard.watch, so it reacts while any action runs; keep_busy runs it while none does.
"""

from __future__ import annotations

import asyncio
import math
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from . import driver as d
from .common import ActionError, Interrupted

if TYPE_CHECKING:
    from .game import Game

REACH = 88  # pixels between centers at which a swing lands
TOO_CLOSE = 56
CONTACT = 60  # pixels between centers at which a monster touches you and hurts
HOLD = REACH - 12  # the distance to keep from the target with a swing ready: it walks into the blade, not into you
SAFE = 120  # the distance to keep while the target can't be hurt for a moment (just hit), so its knockback isn't wasted
SPEED = 5  # pixels the farmer walks a tick
AHEAD = 8  # ticks the movement looks ahead
LANE = 2 * 64  # pixels either side of a spiker's row and column that it darts along
WINDUP = 5  # ticks from pressing the button to the hit
NEAR = 4 * 64  # pixels: a monster this close pauses the action
TRACE = bool(os.environ.get("STARDEW_COMBAT_TRACE"))
SURE = 0.4  # the tactics model's least confidence for its choice to count
MODES = ("near", "all", "off")

TICK = {
    "me": "player.StandingPixel", "tile": "player.tile", "hp": "player.health", "max": "player.max_health", "busy": "player.usingTool", "menu": "state.menu", "eating": "player.isEating",
    "shield": "player.temporarilyInvincible",
    "monsters": {"from": "location.monsters", "where": "not hidden", "select": ["name", "x", "y", "px", "py", "hp", "unhurt", "raw.shellGone", "raw.invincibleCountdown"]},
    "shots": {"from": "location.projectiles", "select": ["px", "py", "vx", "vy"]},
}
MONSTERS = {"from": "location.monsters", "where": "not hidden", "select": ["name", "x", "y", "px", "py"]}
SEEN = {"free": "state.free", "mine": "location.mine_level", "place": "location.name", "me": "player.StandingPixel", "tile": "player.tile", "hp": "player.health",
        "max": "player.max_health", "weapon": {"from": "player.inventory", "where": "kind == 'Weapon'", "select": ["name"]}}


class Tactics:
    """Asks the fast decision model (decide.Decider) to choose between options which are all already safe."""

    def __init__(self, decide: Callable[[dict, dict[str, str]], Awaitable[tuple[str | None, float]]]):
        self.decide = decide

    async def choose(self, state: dict, options: dict[str, str], default: str) -> str:
        """Pick one option, or the default if the model is unsure, slow, or unavailable."""
        choice, confidence = await self.decide(state, options)
        return default if choice is None or confidence < SURE else choice


@dataclass
class Guard:
    """The combat reflex around every action, and its settings (the SDK's guard()). fight: "near" fights a monster which comes
    within NEAR (with a weapon), "all" also hunts every monster on the floor between steps, "off" never fights. Below flee_below of
    max health with monsters close, it eats the cheapest healing food, else retreats (out of the mines, or a step away) and raises
    Interrupted("retreating: ...") once, until health recovers. avoid names monsters never to fight."""

    fight: str = "near"
    flee_below: float = 0.3
    avoid: frozenset[str] = frozenset()
    eat_below: float = 0.5  # mid-fight, with food
    tactics: Tactics | None = None
    busy: bool = False  # reacting now: the steps and walks it makes aren't watched again
    warned: bool = False  # it raised for low health, which hasn't recovered since
    ignored: set = field(default_factory=set)  # monsters a hunt couldn't reach, on this floor
    place: str | None = None

    async def watch(self, g: Game, hunt: bool = False) -> bool:
        """React to monsters close by now (with hunt and fight="all", to any on the floor). Returns whether there are monsters here
        at all, so a long wait knows to keep watching. Raises Interrupted to retreat."""
        if self.busy:
            return False
        monsters = await g.read(MONSTERS)
        if not monsters or g.d.human:
            return bool(monsters)
        s = await g.read(SEEN)
        if s["place"] != self.place:
            self.place, self.ignored = s["place"], set()
        close = [m for m in monsters if math.dist(s["me"], (m["px"], m["py"])) < NEAR]
        if s["free"] and (close or hunt and self.fight == "all"):
            await self.respond(g, s, close, hunt=hunt)
        return True

    async def respond(self, g: Game, s: dict, close: list[dict], *, hunt: bool = False, idle: bool = False) -> str | None:
        """What the reflex does about these monsters: eat, retreat, or fight. Returns what it did."""
        hurt = s["hp"] < self.flee_below * s["max"]
        self.warned = self.warned and hurt
        was, self.busy = self.busy, True
        try:
            if close and hurt:
                meal = await Fight(g, self).food()
                if meal:
                    await g.eat(meal["name"])
                    return "ate"
                did = await self.retreat(g, s, close)
                if not idle and not self.warned:
                    self.warned = True
                    raise Interrupted(f"retreating: health is {s['hp']}/{s['max']} and there's no food")
                return did
            if self.fight == "off" or not s["weapon"] or hurt and self.warned:
                return None
            if hunt and self.fight == "all":
                radius, ticks = 80, 6000  # an infested floor's ladder appears once they're all dead
            elif any(m.get("name") not in self.avoid for m in close):
                radius, ticks = (4, 60) if idle else (5, 900)  # idle: short, so stopping is quick
            else:
                return None
            try:
                r = await self.fight_off(g, radius, ticks, restore=not idle, hunt=hunt)
            except Interrupted:
                if idle or self.warned:
                    return None  # too hurt: the next round eats or flees
                self.warned = True
                raise
            return f"killed {r['killed']}" if r["killed"] else "fought"
        finally:
            self.busy = was

    async def fight_off(self, g: Game, radius: float, ticks: int, *, restore: bool = True, hunt: bool = False) -> dict:
        """Fight the monsters within radius tiles, then (with restore) hold what was held before."""
        was, self.busy = self.busy, True
        slot = await g.read("player.slot") if restore else None
        await g.hold_tool("weapon")
        try:
            return await (Fight(g, self, ignored=self.ignored) if hunt else Fight(g, self)).run(radius, ticks)
        finally:
            self.busy = was
            if restore and slot < len(g.hotbar) and await g.read("player.slot") != slot:
                await g.press(g.hotbar[slot])

    async def retreat(self, g: Game, s: dict, close: list[dict]) -> str:
        if s["mine"] is not None:
            # hurt, no food, and monsters close: get out rather than wait for orders
            try:
                await g.go("Mine" if s["mine"] <= 120 else "SkullCave")  # up the ladder, out of the mines
                return "left the mine"
            except ActionError:
                pass
        fight = Fight(g, self)
        await fight.walls()
        await fight.look(fight.escape(s, close), ticks=8)
        await fight.look([])
        return "fled"


@dataclass
class Fight:
    """The reflex layer: one decision per game tick."""

    g: Game
    guard: Guard = field(default_factory=Guard)
    killed: int = 0
    ate: list[str] = field(default_factory=list)
    _grid: list[str] | None = None
    _origin: tuple[int, int] = (0, 0)
    _route: list = field(default_factory=list)
    count: int = 0
    _last: list = field(default_factory=list)  # the monsters last seen, with their velocities
    _dt: int = 1  # ticks since then
    _stuck: dict = field(default_factory=dict)  # ticks each monster has gone unhurt in reach
    ignored: set = field(default_factory=set)

    async def food(self) -> dict | None:
        """The cheapest thing that heals, so valuables aren't eaten."""
        items = [i for i in await self.g.inventory() if (i.get("healing") or 0) > 0]
        return min(items, key=lambda i: (i.get("price") or 0) / max(1, i["healing"]), default=None)

    async def look(self, keys: list[str], cursor: tuple | None = None, ticks: int = 1) -> dict:
        ops = [d.input(buttons=keys, tile=cursor) if cursor else d.input(buttons=keys), d.step(ticks), d.read(TICK)]
        self._dt = ticks
        return (await self.g.d.run(*ops))[-1]

    def track(self, s: dict) -> None:
        """Each monster's velocity (pixels a tick), from where the nearest one of its kind was last time, smoothed."""
        for m in s["monsters"]:
            prev = min((p for p in self._last if p["name"] == m["name"]), key=lambda p: math.dist((p["px"], p["py"]), (m["px"], m["py"])), default=None)
            if prev and math.dist((prev["px"], prev["py"]), (m["px"], m["py"])) < 24 * self._dt:
                vx, vy = (m["px"] - prev["px"]) / self._dt, (m["py"] - prev["py"]) / self._dt
                m["vx"], m["vy"] = (prev["vx"] + vx) / 2, (prev["vy"] + vy) / 2
            else:
                m["vx"], m["vy"] = 0.0, 0.0
        self._last = s["monsters"]

    def danger(self, s: dict) -> bool:
        """Whether anything is about to touch the farmer: a monster close or closing, or a projectile coming."""
        return self.threat(s["me"], s, 0) > 0 or self.threat(s["me"], s, AHEAD) > 0

    def threat(self, at: tuple, s: dict, t: int) -> float:
        """How much standing at `at` in t ticks would hurt: monsters (where each will be) and projectiles closer than touching,
        weighted by how much closer; a spiker's lanes (its row and column) count too, since it darts along them."""
        hurt = 0.0
        for m in s["monsters"]:
            mx, my = m["px"] + m.get("vx", 0) * t, m["py"] + m.get("vy", 0) * t
            hurt += max(0.0, CONTACT + 16 - math.dist(at, (mx, my)))
            if "Spiker" in m["name"] and math.dist(at, (m["px"], m["py"])) < 8 * 64:
                # it darts when you're within a tile of its row or column: the farther from the lane's middle, the better
                hurt += max(0.0, LANE - min(abs(at[0] - m["px"]), abs(at[1] - m["py"])))
        for p in s.get("shots") or []:
            gap = min(math.dist(at, (p["px"] + p["vx"] * k, p["py"] + p["vy"] * k)) for k in range(0, 41, 2))  # its path over the next 40 ticks
            hurt += 2 * max(0.0, 56 - gap)
        return hurt

    def dodge(self, s: dict, target: dict | None, hold: float = HOLD) -> list[str]:
        """The keys for the best of the nine moves (eight steps, or staying), judged AHEAD ticks on: away from every threat
        (monsters a weapon can't hurt count double), at `hold` from the target, and out of corners."""
        me, (tx, ty) = s["me"], s["tile"]
        armour = 0.3 if s.get("shield") else 1.0  # just hit: a moment of invincibility, so press on
        best, score = (0, 0), -math.inf
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                n = math.hypot(dx, dy) or 1
                at = (me[0] + dx / n * SPEED * AHEAD, me[1] + dy / n * SPEED * AHEAD)
                tile = (int(at[0] // 64), int(at[1] // 64))
                if (dx or dy) and not (self.open(*tile) and self.open(tx + dx, ty) and self.open(tx, ty + dy)):
                    continue
                value = -armour * 6 * self.threat(at, s, AHEAD)
                value -= 6 * armour * sum(max(0.0, CONTACT + 16 - math.dist(at, (m["px"] + m.get("vx", 0) * AHEAD, m["py"] + m.get("vy", 0) * AHEAD))) for m in s["monsters"] if m.get("unhurt"))
                if target:
                    value -= abs(math.dist(at, (target["px"] + target["vx"] * AHEAD, target["py"] + target["vy"] * AHEAD)) - hold)
                value -= 4 * sum(not self.open(tile[0] + i, tile[1] + j) for i in (-1, 0, 1) for j in (-1, 0, 1))
                value -= 2 if (dx or dy) else 0  # stay put when it's all the same
                if value > score:
                    best, score = (dx, dy), value
        return self.keys_toward(best[0] * 64, best[1] * 64)

    def hit_during_swing(self, s: dict, target: dict) -> bool:
        """Whether something other than the target (or a projectile) would touch the farmer while a swing roots them."""
        if s.get("shield"):
            return False
        others = {**s, "monsters": [m for m in s["monsters"] if m is not target]}
        return self.threat(s["me"], others, 3 * WINDUP) > 30

    def clear(self, start: tuple, goal: tuple) -> bool:
        """A straight line of open tiles between two tiles."""
        steps = max(abs(goal[0] - start[0]), abs(goal[1] - start[1]))
        return steps <= 1 or all(self.open(round(start[0] + (goal[0] - start[0]) * k / steps), round(start[1] + (goal[1] - start[1]) * k / steps)) for k in range(1, steps))

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

    async def run(self, radius: float = 6, max_ticks: int = 3600) -> dict:
        """Fight until no monster is within radius tiles. Raises Interrupted at the retreat limit."""
        g = self.g
        await self.walls()
        s = await self.look([])
        ticks, futile, total, dodged = 0, 0, None, 0
        while ticks < max_ticks:
            if s["menu"]:
                await g.ready()
                s = await self.look([])
                continue
            max_hp = s["max"]
            if s["hp"] < max_hp * self.guard.eat_below and not s["eating"]:
                meal = await self.food()
                if meal and (s["hp"] < max_hp * self.guard.flee_below or not any(math.dist(s["me"], (m["px"], m["py"])) < 2 * REACH for m in s["monsters"])):
                    await g.eat(meal["name"])
                    self.ate.append(meal["name"])
                    s = await self.look([])
                    continue
                if s["hp"] < max_hp * self.guard.flee_below:
                    raise Interrupted(f"retreating: health is {s['hp']}/{max_hp} and there's no food (killed {self.killed})")
            self.track(s)
            fair = [m for m in s["monsters"] if not m.get("unhurt") and (m["name"], m["x"] // 4, m["y"] // 4) not in self.ignored and m["name"] not in self.guard.avoid]
            near = sorted(((math.dist(s["me"], (m["px"], m["py"])), i, m) for i, m in enumerate(fair)), key=lambda t: t[:2])
            near = [(dist, m) for dist, _, m in near if dist <= radius * 64]
            if not near:
                # only monsters a weapon can't hurt (or none): step out of their way until none is close, then stop
                if self.danger(s) and dodged < 240:
                    s, dodged = await self.look(self.dodge(s, None)), dodged + 1
                    ticks += 1
                    continue
                break
            hp = sum(m["hp"] for m in fair)
            futile = 0 if total is None or hp < total else futile + 1
            if total is not None and len(fair) < self.count:
                self.killed += self.count - len(fair)
            self.count, total = len(fair), hp
            distance, target = near[0]
            if futile > 360:
                # out of reach (on a wall, behind rocks): leave it be
                self.ignored.add((target["name"], target["x"] // 4, target["y"] // 4))
                futile, total = 0, None
                continue
            if TRACE:
                print(f"t{ticks} hp{s['hp']} me{s['me']} tile{s['tile']} busy{s['busy']} -> {target['name']} hp{target['hp']} d{distance:.0f} inv{target.get('raw.invincibleCountdown')} at{target['px'],target['py']} futile{futile}", flush=True)
            ahead = math.dist(s["me"], (target["px"] + target["vx"] * WINDUP, target["py"] + target["vy"] * WINDUP))
            stunned = (target.get("raw.invincibleCountdown") or 0) > 120
            if s["busy"]:
                s = await self.look([])
            elif min(distance, ahead) <= REACH and not stunned and not self.hit_during_swing(s, target):
                s = await self.strike(s, target, futile)
            elif distance > 4 * 64 and not self.clear(s["tile"], (target["x"], target["y"])):
                s = await self.approach(s, target)
            else:
                s = await self.look(self.dodge(s, target, SAFE if stunned else HOLD), ticks=3)
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
        if self.clear((tx, ty), goal):
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


async def keep_busy(g: Game, stop: asyncio.Event) -> dict:
    """Work while the agent thinks, until stop is set: the world keeps running, so the guard keeps watch, and in the mines breaks nearby rocks."""
    done = {"killed": 0, "rocks": 0, "ticks": 0}
    while not stop.is_set():
        if g.d.human:
            await asyncio.sleep(0.2)  # the player is playing; only watch
            continue
        s = await g.read({**SEEN, "energy": "player.energy", "monsters": MONSTERS})
        if not s["free"]:
            await g.step(10)
            continue
        close = [m for m in s["monsters"] if math.dist(s["me"], (m["px"], m["py"])) < NEAR]
        if close and (did := await g.guard.respond(g, s, close, idle=True)):
            key = {"ate": "ate", "fled": "fled", "left the mine": "left the mine"}.get(did, "killed")
            done[key] = done.get(key, 0) + (int(did.split()[-1]) if did.startswith("killed ") else 0 if key == "killed" else 1)
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
