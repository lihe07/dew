"""Command line. `stardew play` lets the agent play; `stardew saves` lists saves."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tomllib
import threading
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.text import Text

from . import api, combat, look, vision
from .brain import Brain
from .decide import Decider
from .director import Director
from .display import Display
from .planner import Planner
from .tui import TUI
from .web import Research
from .driver import BridgeError, Disconnected, Driver, find_session
from .game import ActionError, Game
from .look import status
from .script import Context
from .storage import SaveData, data_root

ROOT = Path(__file__).resolve().parents[2]
PROMPT = (Path(__file__).parent / "prompts" / "agent.md").read_text().replace("{sdk}", api.reference())

console = Console(highlight=False)
MAP_STYLES = {"@": "bold black on yellow", "~": "blue", "T": "green", "t": "green", '"': "green", "B": "green", "w": "green4", "c": "yellow", "v": "bright_green", "R": "bold bright_green",
              "x": "red", "h": "dark_goldenrod", "M": "bold red", "N": "bold cyan", "A": "cyan", "D": "bold yellow", ">": "bold yellow", "L": "bold yellow", "$": "magenta", "O": "magenta",
              "f": "bright_magenta", "!": "bright_white", "%": "grey50", "#": "grey35", "o": "grey62", "/": "dark_goldenrod", "S": "grey62", "F": "grey50", "*": "bright_white"}


display = Display(console)
show = display.show


async def draw_map(game: Game) -> None:
    """The map the agent would get from here.map(), in colour."""
    if not await game.read("state.world"):
        return
    result = await look.tile_map(game, max(5, min((console.width - 8) // 2, 9)))
    text = Text()
    for line in result["map"].split("\n")[2:]:  # without the rulers
        text.append(line[:5], "grey50")
        for char in line[5:]:
            text.append(char, MAP_STYLES.get(char, ""))
        text.append("\n")
    text.rstrip()
    console.print(text)


LIVE = {**look.STATUS, "text": "time.text", "weekday": "time.weekday", "season": "time.season", "day": "time.day", "weather": "time.weather", "luck": "player.DailyLuck"}


async def live_status(game: Game) -> dict:
    """What the status panel shows."""
    s = await game.read(LIVE)
    return {**s, "date": f"{s['weekday']} {s['season']} {s['day']}", "luck": look.luck_text(s["luck"])}


def listen(brain: Brain) -> None:
    """Pass typed lines to the agent's queue. A daemon thread, so a pending read never keeps the program open."""
    loop = asyncio.get_running_loop()

    def read() -> None:
        for line in sys.stdin:
            if text := line.strip():
                loop.call_soon_threadsafe(brain.steer, f"PLAYER: {text}")
                loop.call_soon_threadsafe(show, "info", "(queued for the agent)")

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
        brain = Brain(ctx, api_key=key, model=args.model, system=PROMPT)
        brain.effort = args.effort
        if (tinyfish := os.environ.get("TINYFISH_API_KEY")) and not args.no_web:
            brain.research = Research(brain.client, args.web_model, tinyfish)
        save = SaveData(data_root(args.data), folder)
        said = save.prepare()
        vision.save_to = save.screenshots
        panels = sys.stdout.isatty() and sys.stdin.isatty() and not args.plain
        if panels:
            await play_in_panels(args, game, brain, ctx, save, said)
            return
        director = Director(game, brain, ctx, save=save, planner=planner(args, brain, save), show=show)
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
        show("info", f"{args.model} via Respan · save {folder} · {f"{args.days} day(s)" if args.days else "until you quit"} · data in {save.dir}")
        show("info", "type a message and Enter to queue it for the agent · F8 in the game takes control")
        await director.play(args.days)
        show("info", await status(game))
    finally:
        await leave(game)


def planner(args: argparse.Namespace, brain: Brain, save: SaveData) -> Planner:
    """The planner that comes in each night; its show is the director's, set once the director is made."""
    return Planner(brain.client, args.planner_model or args.model, save.planner_file, research=brain.research, effort=args.planner_effort)


async def play_in_panels(args: argparse.Namespace, game: Game, brain: Brain, ctx: Context, save: SaveData, said: list[str]) -> None:
    """The full-screen view, with a tab per day; the save's last 7 days are loaded, so they can be looked through."""
    console.print(f"[grey50]{args.model} · {save.folder} · F8 in the game takes control[/]")
    tui = TUI(console, header="F8 takes control")
    tui.on_steer, tui.queued, tui.usage, tui.model = brain.steer, brain.queued, lambda: brain.usage, args.model
    tui.waiting = lambda: [goal for _, goal in brain.runner.waiting]
    tui.line = lambda: brain.runner.script.line if brain.runner.script else None
    tui.load(save.load_days(7))
    director = Director(game, brain, ctx, save=save, planner=planner(args, brain, save), show=tui.show)
    for line in said:
        director.show("info", line)
    tui.start()
    watch = asyncio.create_task(tui.watch(lambda: live_status(game)))
    try:
        await director.play(args.days)
        played = len([d for d in tui.days if d.title != "starting"])
        await tui.finished(f"Done: played {played} day{'s' if played != 1 else ''}. Look back with ←/→, then press q to quit.")
    finally:
        watch.cancel()
        tui.stop()
    console.print(f"usage: {brain.usage}")


async def saves(args: argparse.Namespace) -> None:
    game = await connect()
    for s in await game.read("saves"):
        console.print(f"[bold]{s['folder']}[/]  {s['name']} of {s['farm']} farm, day {s['day']} of season {s['season']}, year {s['year']}, {s['money']}g")
    await game.d.close()


def config(p: argparse.ArgumentParser, path: Path) -> dict:
    """play's defaults from stardew.toml (keys named like the flags, without the dashes); a flag given still wins."""
    if not path.exists():
        return {}
    try:
        values = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as ex:
        sys.exit(f"stardew: {path.name}: {ex}")
    known = {a.dest for a in p._actions}
    if wrong := [k for k in values if k.replace("-", "_") not in known]:
        sys.exit(f"stardew: {path.name}: unknown setting {', '.join(wrong)}; the settings are: {', '.join(sorted(k.replace('_', '-') for k in known - {'help'}))}")
    return {k.replace("-", "_"): v for k, v in values.items()}


def main() -> None:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(prog="stardew")
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("play", help="let the agent play")
    p.add_argument("--plain", action="store_true", help="print a line per action instead of the full-screen panels")
    p.add_argument("--verbose", action="store_true", help="show everything: full thoughts, reports, results, and scripts")
    p.add_argument("--days", type=int, default=0, help="stop after this many days (default: keep playing until you quit)")
    p.add_argument("--save", metavar="FOLDER", help="load this save if none is loaded")
    p.add_argument("--new", metavar="NAME", help="start a new game if none is loaded")
    p.add_argument("--model", default=os.environ.get("STARDEW_MODEL", "deepseek-v4.1-flash"))
    p.add_argument("--data", metavar="DIR", help="where the agent keeps its data (default $STARDEW_DATA, else ./stardew-data)")
    p.add_argument("--effort", default="low", choices=["low", "medium", "high", "off"], help="how hard the agent reasons (default low; the model's own default is high, and slow)")
    p.add_argument("--speed", type=int, default=1, help="how fast the world runs, single-player only (default 1; faster makes combat reflexes less reliable)")
    p.add_argument("--no-web", action="store_true", help="no web lookups")
    p.add_argument("--planner-model", metavar="MODEL", help="the model of the planner, which each night writes the diary, tidies the memory and plans tomorrow (default: the agent's model)")
    p.add_argument("--planner-effort", default="high", choices=["low", "medium", "high", "off"], help="how hard the planner reasons (default high: it runs once a day)")
    p.add_argument("--web-model", default="hy4-flash", metavar="MODEL", help="the model of the research helper behind the web tool (default hy4-flash)")
    p.add_argument("--no-map", action="store_true", help="don't print the map after each action")
    commands.add_parser("saves", help="list saves")
    p.set_defaults(**config(p, ROOT / "stardew.toml"))
    args = parser.parse_args()
    try:
        asyncio.run({"play": play, "saves": saves}[args.command](args))
    except (Disconnected, ActionError) as ex:
        sys.exit(f"stardew: {ex}")
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
