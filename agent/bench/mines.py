"""Benchmark mine combat: from a fixed save, take the elevator down and mine N floors. No language model is involved.

    python bench/mines.py SAVE_FOLDER --trials 3 --floors 5 --label baseline
Needs a sandboxed game (STARDEW_SESSION); each trial restarts it from the save.
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time

from dotenv import load_dotenv
from pathlib import Path

from stardew_agent import combat, work
from stardew_agent.decide import Decider
from stardew_agent.driver import Disconnected, Driver, find_session
from stardew_agent.game import ActionError, Game

ROOT = Path(__file__).resolve().parents[2]


async def connect(realtime: bool = False) -> Game:
    for _ in range(90):
        try:
            port, token = find_session()
            driver = Driver(port, token)
            await driver.connect(speed=1 if realtime else 16, steal=True)
            game = Game(driver)
            await game.setup()
            return game
        except (Disconnected, OSError):
            await asyncio.sleep(1)
    raise SystemExit("the sandbox didn't come up")


async def trial(save: str, floors: int, tactics, realtime: bool = False) -> dict:
    subprocess.run([ROOT / "scripts/sandbox.sh", "stop"], capture_output=True)
    while subprocess.run(["pgrep", "-x", "StardewModdingA"], capture_output=True).returncode == 0:
        await asyncio.sleep(1)
    subprocess.run([ROOT / "scripts/sandbox.sh", "start"], check=True)
    await asyncio.sleep(5)
    g = await connect(realtime)
    await g.load_save(save)
    # the same preparation each time: room in the backpack, and a little food
    for item in await g.inventory():
        if item["name"] in ("Trash", "Soggy Newspaper", "Broken CD", "Broken Glasses", "Driftwood", "Joja Cola", "Stone", "Fiber", "Wood"):
            await g.trash(item["name"])
    await g.travel("Farm")
    await work.fish(g, 3)
    await g.travel("Mine")
    before = await g.read({"health": "player.health", "max": "player.max_health", "energy": "player.energy", "minutes": "time.minutes"})
    started = time.monotonic()
    outcome, result = "done", {}
    try:
        result = await work.mine_down(g, floors, tactics=tactics)
    except ActionError as ex:
        outcome = str(ex)
    after = await g.read({"health": "player.health", "energy": "player.energy", "minutes": "time.minutes", "level": "location.mine_level", "out": "state.passed_out",
                          "deepest": "player.deepestMineLevel", "food": {"from": "player.inventory", "where": "energy > 0", "select": ["name", "stack"]}})
    await g.d.close()
    return {
        "outcome": outcome[:160], "result": result, "health_lost": before["health"] - after["health"], "max_health": before["max"],
        "energy_used": round(before["energy"] - after["energy"]), "game_minutes": after["minutes"] - before["minutes"], "real_seconds": round(time.monotonic() - started, 1),
        "ended_on": after["level"], "passed_out": after["out"], "food_left": after["food"],
    }


async def main() -> None:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser()
    parser.add_argument("save")
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--floors", type=int, default=5)
    parser.add_argument("--label", default="run")
    parser.add_argument("--realtime", action="store_true", help="run at normal speed instead of 16x")
    parser.add_argument("--tactics", action="store_true", help="ask span-02 for tactical choices")
    args = parser.parse_args()
    results = []
    for i in range(args.trials):
        tactics = combat.Tactics(Decider(os.environ['RESPAN_API_KEY'])) if args.tactics else None
        r = await trial(args.save, args.floors, tactics, args.realtime)
        if tactics:
            r["tactics"] = tactics.log
        print(json.dumps(r), flush=True)
        results.append(r)
    out = Path(__file__).parent / f"results-{args.label}.json"
    out.write_text(json.dumps(results, indent=1))
    subprocess.run([ROOT / "scripts/sandbox.sh", "stop"], capture_output=True)


if __name__ == "__main__":
    asyncio.run(main())
