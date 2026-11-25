"""Walking around people, animals, and monsters, against a scripted fake game."""

import asyncio

import pytest
from stardew_agent.combat import Guard
from stardew_agent.common import crowded
from stardew_agent.game import ActionError, Game
from stardew_agent.navigation import SEGMENT


class FakeDriver:
    """Answers path with `paths`, follow with the scripted `follows`, and reads from `state`."""

    def __init__(self, follows, paths=None, state=None):
        self.on_journal = []
        self.human = False
        self.follows = list(follows)
        self.arrived = False
        self.paths = paths or (lambda op: {"path": [[2, 0]] if self.arrived else [[0, 0], [1, 0], [2, 0]], "target": op["goals"][0], "cost": 2})
        self.state = {"player.health": 100, "player.slot": 0, "player.inventory": [], "me": [0, 0], "monsters": []}
        self.state.update(state or {})
        self.sent = []

    async def read(self, what):
        if isinstance(what, dict) and "$grid" in what:  # open ground everywhere
            x, y, w, h = what["$grid"]["rect"]
            return {"x": x, "y": y, "pass": ["." * w] * h}
        if isinstance(what, dict) and "from" in what:  # a query: the guard's look for monsters finds none
            return self.state.get(what["from"], [])
        if isinstance(what, dict):
            return {k: self.state.get(k) for k in what}
        return self.state.get(what)

    async def look(self, *ops):
        self.sent.extend(ops)
        return self.paths(ops[-1])

    async def one(self, op):
        self.sent.append(op)
        if op["op"] == "follow":
            result = {"at": [1, 0], "ticks": 1, "fired": None, **self.follows.pop(0)}
            self.arrived = result["status"] == "arrived"
            return result
        if op["op"] == "path":
            return self.paths(op)
        return {"ticks": op.get("n", 1), "fired": None}

    async def run(self, *ops):
        return [await self.one(op) for op in ops]

    def ops(self, name):
        return [op for op in self.sent if op["op"] == name]


def walk(driver, **kw):
    return asyncio.run(Game(driver).go([(2, 0)], **kw)).target


def test_waits_for_a_person_then_goes_on():
    driver = FakeDriver([{"status": "blocked", "by": "Robin", "monster": False}, {"status": "arrived"}])
    assert walk(driver) == (2, 0)
    assert len(driver.ops("follow")) == 2
    assert any(op["op"] == "step" and op["n"] == 30 for op in driver.sent)


def test_someone_always_in_the_way_is_reported():
    driver = FakeDriver([{"status": "blocked", "by": "Robin", "monster": False}] * 9)
    with pytest.raises(ActionError, match="Robin kept standing in the way"):
        walk(driver)


def test_only_way_through_is_occupied():
    driver = FakeDriver([], paths=lambda op: None if op.get("avoid", True) else {"path": [[0, 0], [1, 0]], "target": [1, 0]})
    with pytest.raises(ActionError, match="people or animals are standing in the way"):
        walk(driver)
    assert sum(op["op"] == "step" and op["n"] == 60 for op in driver.sent) == 6


def test_armed_fights_a_monster_in_the_way(monkeypatch):
    driver = FakeDriver([{"status": "blocked", "by": "Green Slime", "monster": True}, {"status": "arrived"}],
                        state={"player.inventory": [{"kind": "Weapon"}], "monsters": [{"x": 1, "y": 0}]})
    fought = []

    async def fight_off(self, g, radius, ticks):
        fought.append(ticks)

    monkeypatch.setattr(Guard, "fight_off", fight_off)
    assert walk(driver) == (2, 0)
    assert fought == [240]
    assert all(op["n"] == SEGMENT for op in driver.ops("follow"))  # monsters about: plan again often


def test_unarmed_pushes_past_a_monster_it_cant_shake():
    driver = FakeDriver([{"status": "blocked", "by": "Bat", "monster": True}] * 3 + [{"status": "arrived"}], state={"monsters": [{"x": 1, "y": 0}]})
    assert walk(driver) == (2, 0)
    assert [op["stop_for"] for op in driver.ops("follow")] == ["all", "all", "all", "people"]


def test_follows_a_moving_target():
    spots = [[[5, 0]], [[6, 0]], [[7, 0]]]
    driver = FakeDriver([{"status": "timeout"}, {"status": "arrived"}],
                        paths=lambda op: {"path": [[0, 0]] if op["goals"] == [[7, 0]] else [[0, 0], [1, 0]], "target": op["goals"][0]})

    async def track():
        return spots.pop(0)

    assert asyncio.run(Game(driver).go(track, beside=True)).target == (7, 0)
    assert all(op["n"] == SEGMENT for op in driver.ops("follow"))


def test_stuck_tiles_are_avoided_then_forgotten():
    driver = FakeDriver([{"status": "stuck", "at": [1, 0]}, {"status": "arrived"}])
    walk(driver)
    assert driver.ops("path")[-1]["blocked"] == [[1, 0]]


def test_groups_of_monsters_are_walked_around():
    assert crowded([{"x": 5, "y": 5}], [], (0, 0)) == []
    tiles = crowded([{"x": 5, "y": 5}, {"x": 6, "y": 6}], [[6, 7]], (4, 4))
    assert [5, 5] in tiles and [7, 7] in tiles and [6, 7] not in tiles and [4, 4] not in tiles
