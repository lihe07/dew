"""The guard, which runs while no script does: its safety reflexes, decided without the agent."""

import asyncio
from types import SimpleNamespace as NS

from stardew_agent import combat


class FakeGame:
    def __init__(self, hp, mine, food):
        self.d = NS(human=False)
        self.hp, self.mine, self.food = hp, mine, food
        self.did = []
        self.guard = combat.Guard()

    async def read(self, what):
        if isinstance(what, dict) and "hp" in what:
            return {"free": True, "mine": self.mine, "me": [100, 100], "tile": [1, 1], "energy": 200, "hp": self.hp, "max": 100,
                    "weapon": [{"name": "Rusty Sword"}], "monsters": [{"px": 120, "py": 100}]}
        raise AssertionError(f"unexpected read {what}")

    async def inventory(self):
        return [{"name": "Carp", "healing": 10, "price": 30}] if self.food else []

    async def eat(self, name):
        self.did.append(f"eat {name}")
        self.hp = 60

    async def go(self, target):
        self.did.append(f"go to {target}")
        self.mine = None
        self.hp = 100  # out of danger: nothing close is hurting it now


async def guard(g, seconds=0.3):
    stop = asyncio.Event()
    task = asyncio.create_task(combat.keep_busy(g, stop))
    await asyncio.sleep(seconds)
    stop.set()
    return await asyncio.wait_for(task, 2)


def stub_fighting(monkeypatch):
    async def fight(self, radius=6, max_ticks=3600, targets=None):
        await asyncio.sleep(0.01)
        return {"killed": 0, "ate": []}
    monkeypatch.setattr(combat.Fight, "run", fight)
    monkeypatch.setattr(combat.Fight, "walls", lambda self: asyncio.sleep(0))
    async def look(self, keys, cursor=None, ticks=1):
        return {}
    monkeypatch.setattr(combat.Fight, "look", look)


def test_hurt_with_food_it_eats(monkeypatch):
    stub_fighting(monkeypatch)
    g = FakeGame(hp=20, mine=40, food=True)
    g.hold_tool = lambda name: asyncio.sleep(0)
    asyncio.run(guard(g))
    assert g.did[0] == "eat Carp" and "go to Mine" not in g.did


def test_hurt_without_food_in_the_mines_it_leaves(monkeypatch):
    stub_fighting(monkeypatch)
    g = FakeGame(hp=20, mine=40, food=False)
    g.hold_tool = lambda name: asyncio.sleep(0)
    done = asyncio.run(guard(g))
    assert g.did == ["go to Mine"] and done.get("left the mine") == 1


def test_a_menu_an_action_just_opened_stays_open():
    """interact() advances plain dialogue with auto_close; it must not close the shop or chest it just opened."""
    from stardew_agent.game import Game

    class Menus:
        def __init__(self, kind):
            self.kind, self.closed = kind, False

        async def read(self, what):
            if isinstance(what, dict) and "root" in what:
                return {"type": self.kind, "choices": None, "root": self.kind, "held": None}
            if isinstance(what, dict) and "source" in what:
                return {"source": 1, "bin": False}
            return self.closed

    for kind in ("ShopMenu", "ItemGrabMenu"):
        g = Game.__new__(Game)
        g.d = Menus(kind)
        async def close(discard=False, g=g):
            g.d.closed = True
        g.close_menu = close
        assert asyncio.run(g.auto_close()) is False and not g.d.closed, kind
        assert asyncio.run(g.auto_close(leftovers=True)) is True and g.d.closed, kind
