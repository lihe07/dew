"""Plays real game actions in a running game. Skipped unless STARDEW_SESSION points at a sandbox (see scripts/sandbox.sh)."""

import asyncio
import os

import pytest
from stardew_agent import sdk
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
        await sdk.screen(ctx, "interact", tiles=[[3, 7]])  # the gift box with the starting seeds
        await sdk.go(ctx, "Farm")
        await sdk.clear(ctx, [60, 17, 66, 21])
        await sdk.till(ctx, [61, 18, 63, 19])
        await sdk.plant(ctx, "Parsnip Seeds", [61, 18, 63, 19])
        await sdk.water(ctx)
        return await sdk.find(ctx, "crops")
    finally:
        await driver.close()


def test_first_morning():
    found = asyncio.run(first_morning())
    assert any("Parsnip" in row for row in found["nearest"]), found
