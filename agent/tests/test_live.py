"""Plays real game actions in a running game. Skipped unless STARDEW_SESSION points at a sandbox (see scripts/sandbox.sh)."""

import asyncio
import os

import pytest
from stardew_agent.api.places import Here
from stardew_agent.api.screen import Screen
from stardew_agent.api.where import Area
from stardew_agent.api.world import World
from stardew_agent.driver import Driver, find_session
from stardew_agent.game import Game
from stardew_agent.script import Context

pytestmark = pytest.mark.skipif("STARDEW_SESSION" not in os.environ, reason="needs a sandboxed game")


async def first_morning() -> dict:
    port, token = find_session()
    driver = Driver(port, token)
    await driver.connect(speed=16, steal=True)
    game = Game(driver)
    await game.setup()
    ctx = Context(game)
    try:
        if not await game.read("state.world"):
            await game.new_game("Tester", "Smoke", "tests")
        world, field = World(), Area(x1=61, y1=18, x2=63, y2=19)
        await Screen().interact(ctx, [(3, 7)])  # the gift box with the starting seeds
        await world.go(ctx, "Farm")
        await world.gather(ctx, ["weeds", "stone", "twigs"], Area(x1=60, y1=17, x2=66, y2=21))
        await world.till(ctx, field)
        await world.plant(ctx, "Parsnip Seeds", field)
        await world.water(ctx)
        return await Here().find(ctx, "planted")
    finally:
        await driver.close()


def test_first_morning():
    found = asyncio.run(first_morning())
    assert any("Parsnip" in t.f["name"] for t in found), found
