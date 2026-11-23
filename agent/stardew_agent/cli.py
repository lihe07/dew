"""Command line. `stardew play` lets the agent play; `stardew saves` lists saves."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import threading
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.text import Text

from . import combat, look, vision
from .brain import Brain, Limits
from .decide import Decider
from .director import Director
from .display import Display
from .tui import TUI
from .driver import BridgeError, Disconnected, Driver, find_session
from .game import ActionError, Game
from .look import status
from .script import Context
from .storage import SaveData, data_root

ROOT = Path(__file__).resolve().parents[2]
PROMPT = (Path(__file__).parent / "prompt.md").read_text()

console = Console(highlight=False)
MAP_STYLES = {"@": "bold black on yellow", "~": "blue", "T": "green", "t": "green", '"': "green", "B": "green", "w": "green4", "c": "yellow", "v": "bright_green", "R": "bold bright_green",
              "x": "red", "h": "dark_goldenrod", "M": "bold red", "N": "bold cyan", "A": "cyan", "D": "bold yellow", ">": "bold yellow", "L": "bold yellow", "$": "magenta", "O": "magenta",
              "f": "bright_magenta", "!": "bright_white", "%": "grey50", "#": "grey35", "o": "grey62", "/": "dark_goldenrod", "S": "grey62", "F": "grey50", "*": "bright_white"}


display = Display(console)


def show(kind: str, text: str, data: dict | None = None, ok: bool = True) -> None:
    display.show(kind, text, data, ok)


async def map_text(game: Game, radius: int) -> Text | None:
    """The map the agent would get from look_map(), in colour."""
    if not await game.read("state.world"):
        return None
    result = await look.tile_map(game, radius)
    text = Text()
    for line in result["map"].split("\n")[2:]:  # without the rulers
        text.append(line[:5], "grey50")
        for char in line[5:]:
            text.append(char, MAP_STYLES.get(char, ""))
        text.append("\n")
    text.rstrip()
    return text


async def draw_map(game: Game) -> None:
    if text := await map_text(game, max(5, min((console.width - 8) // 2, 9))):
        console.print(text)


LIVE = {"clock": "time.clock", "text": "time.text", "place": "location.name", "energy": "player.energy", "maxe": "player.max_energy", "hp": "player.health", "maxh": "player.max_health",
        "money": "player.money", "free": "player.free_slots", "held": "player.held", "weekday": "time.weekday", "season": "time.season", "day": "time.day",
        "weather": "time.weather", "luck": "player.DailyLuck"}


async def live_status(game: Game) -> dict:
    """What the status panel shows."""
    s = await game.read(LIVE)
    luck = s.pop("luck") or 0
    held = s.pop("held")
    return {**s, "held": held["name"] if held else None, "date": f"{s['weekday']} {s['season']} {s['day']}",
            "luck": "very good" if luck > 0.07 else "good" if luck > 0.02 else "very bad" if luck < -0.07 else "bad" if luck < -0.02 else "neutral"}


def listen(brain: Brain) -> None:
    """Pass typed lines to the agent. A daemon thread, so a pending read never keeps the program open."""
    loop = asyncio.get_running_loop()

    def read() -> None:
        for line in sys.stdin:
            text = line.strip()
            if text.startswith("/later "):
                loop.call_soon_threadsafe(brain.tell, f"PLAYER: {text[7:].strip()}")
                loop.call_soon_threadsafe(show, "info", "(the agent gets this when it next wakes)")
            elif text:
                loop.call_soon_threadsafe(brain.steer, f"PLAYER: {text}")
                loop.call_soon_threadsafe(show, "info", "(stopping the script and waking the agent with your message)")

    threading.Thread(target=read, daemon=True, name="stdin").start()


async def connect(speed: int = 1) -> Game:
    port, token = find_session()
    driver = Driver(port, token)
    await driver.connect(speed=speed)
    game = Game(driver)
    await game.setup()
    return game


async def leave(game: Game) -> None:
    """Disconnect, leaving the game paused in its menu so time doesn't run on."""
    try:
        if await game.read("state.free and not state.multiplayer") and not game.d.human:
            await game.press(game.keys["menu"], after=5)
    except BridgeError:
        pass
    await game.d.close()


async def play(args: argparse.Namespace) -> None:
    key = os.environ.get("RESPAN_API_KEY")
    if not key:
        sys.exit("RESPAN_API_KEY isn't set; put it in .env")
    game = await connect(args.speed)
    try:
        if not await game.read("state.world"):
            if args.new:
                await game.new_game(args.new, args.new, "tokens")
            elif args.save:
                await game.load_save(args.save)
            else:
                sys.exit("no save is loaded: load one in the game, or pass --save FOLDER or --new NAME")
        folder = f"{await game.read('game.player.farmName')}_{await game.read('game.uniqueIDForThisGame')}"
        ctx = Context(game, while_idle=lambda stop: combat.keep_busy(game, stop), decide=Decider(key))
        brain = Brain(ctx, api_key=key, model=args.model, system=PROMPT, limits=Limits(turns=args.turns), web=not args.no_web)
        brain.summary_model = args.summary_model
        save = SaveData(data_root(args.data), folder)
        said = save.prepare()
        vision.save_to = save.screenshots
        memory_model = None if args.memory_model.lower() == "none" else args.memory_model
        panels = sys.stdout.isatty() and sys.stdin.isatty() and not args.plain
        if panels:
            await play_in_panels(args, game, brain, ctx, save, said, memory_model)
            return
        director = Director(game, brain, ctx, goal=args.goal, save=save, show=show, memory_model=memory_model, verbose=args.verbose)
        display.verbose = args.verbose
        for line in said:
            director.show("info", line)
        if not args.no_map:
            async def arrived() -> None:  # a map of each new place, not after every action
                if display.moved:
                    display.moved = False
                    await draw_map(game)
            director.after_result = arrived
        if sys.stdin.isatty():
            listen(brain)
        show("info", f"{args.model} via Respan · save {folder} · {args.days} day(s) · data in {save.dir}")
        show("info", "type to steer the agent (Enter stops its script; /later TEXT waits until it next wakes) · F8 in the game takes control")
        await director.play(args.days)
        show("info", await status(game))
    finally:
        await leave(game)


async def play_in_panels(args: argparse.Namespace, game: Game, brain: Brain, ctx: Context, save: SaveData, said: list[str], memory_model: str | None) -> None:
    """The full-screen view, with a tab per day; the save's last 7 days are loaded, so they can be looked through."""
    console.print(f"[grey50]{args.model} · {save.folder} · F8 in the game takes control[/]")
    tui = TUI(console, header="F8 takes control")
    tui.on_steer, tui.on_later = brain.steer, brain.tell
    tui.load(save.load_days(7))
    director = Director(game, brain, ctx, goal=args.goal, save=save, show=tui.show, memory_model=memory_model, verbose=args.verbose)
    for line in said:
        director.show("info", line)
    tui.start()
    watch = asyncio.create_task(tui.watch(lambda: live_status(game)))
    try:
        await director.play(args.days)
        await tui.finished("done · look back through the days, then press q to quit")
    finally:
        watch.cancel()
        tui.stop()
    console.print(f"usage: {brain.usage}")


async def saves(args: argparse.Namespace) -> None:
    game = await connect()
    for s in await game.read("saves"):
        console.print(f"[bold]{s['folder']}[/]  {s['name']} of {s['farm']} farm, day {s['day']} of season {s['season']}, year {s['year']}, {s['money']}g")
    await game.d.close()


def main() -> None:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(prog="stardew")
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("play", help="let the agent play")
    p.add_argument("--plain", action="store_true", help="print a line per action instead of the full-screen panels")
    p.add_argument("--verbose", action="store_true", help="show everything: full thoughts, reports, results, and scripts")
    p.add_argument("--goal", default="Build a thriving farm: earn money steadily, make friends, and explore.")
    p.add_argument("--days", type=int, default=1)
    p.add_argument("--save", metavar="FOLDER", help="load this save if none is loaded")
    p.add_argument("--new", metavar="NAME", help="start a new game if none is loaded")
    p.add_argument("--model", default=os.environ.get("STARDEW_MODEL", "deepseek-v4.1-flash"))
    p.add_argument("--summary-model", metavar="MODEL", help="model for the daily diary and compaction (default: --model, whose cache makes it cheapest), e.g. hy4-flash")
    p.add_argument("--memory-model", metavar="MODEL", default="hy4-flash", help="model which consolidates the memory each night (default hy4-flash; none turns it off)")
    p.add_argument("--data", metavar="DIR", help="where the agent keeps its data (default $STARDEW_DATA, else ./stardew-data)")
    p.add_argument("--turns", type=int, default=60, help="most model turns per day (default 60)")
    p.add_argument("--speed", type=int, default=1, help="how fast the world runs, single-player only (default 1; faster makes combat reflexes less reliable)")
    p.add_argument("--no-web", action="store_true", help="no web lookups")
    p.add_argument("--no-map", action="store_true", help="don't print the map after each action")
    commands.add_parser("saves", help="list saves")
    args = parser.parse_args()
    try:
        asyncio.run({"play": play, "saves": saves}[args.command](args))
    except (Disconnected, ActionError) as ex:
        sys.exit(f"stardew: {ex}")
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
