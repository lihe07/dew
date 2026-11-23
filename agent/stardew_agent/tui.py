"""The full-screen view: the day's Timeline (the planner's reasoning, its scripts, and each action's result, in order), the Plan
(the running script, its current line marked), the Farmer (time, place, energy, health, gold), what the agent is doing now, and
an input box to steer it. One tab per day.

Keys: Tab focuses the Timeline or the Plan; ↑/↓, PgUp/PgDn, Home/End or the mouse wheel scroll it (End follows the newest again);
←/→ switch days; type and Enter to queue a message for the planner; q quits when the agent is done.
"""

from __future__ import annotations

import asyncio
import os
import re
import select
import sys
import termios
import time
import threading
import tty
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from rich.console import Console, Group
from rich import box
from rich.layout import Layout
from rich.progress_bar import ProgressBar
from rich.table import Table
from rich.live import Live
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

from .display import action_lines, clock, event_text, notes_text

KEEP = 2000  # lines of actions kept per day


@dataclass
class Day:
    """What one game day showed."""

    title: str
    lines: list[Text] = field(default_factory=list)  # the transcript
    scripts: list[dict] = field(default_factory=list)  # goal, source, the line running
    status: dict = field(default_factory=dict)
    report: str = ""
    running: str | None = None  # the action under way
    diary: str = ""


class TUI:
    def __init__(self, console: Console, header: str = ""):
        self.console, self.header = console, header
        self.days: list[Day] = [Day("starting")]
        self.viewing: int | None = None  # None follows the live day
        self.scroll = 0  # Timeline rows scrolled back from the newest; 0 follows it
        self.plan_top: int | None = None  # the Plan's first line shown; None follows the running line
        self.focus = "timeline"
        self.typed = ""
        self.notice = ""
        self.on_steer: Callable[[str], None] = lambda text: None
        self.queued: Callable[[], list[tuple[str, str]]] = lambda: []  # messages waiting for the planner
        self._live: Live | None = None
        self._keys: threading.Thread | None = None
        self._saved: Any = None
        self._stop = threading.Event()
        self._quit: asyncio.Event | None = None
        self.thinking_since: float | None = None  # the planner is waiting on the model
        self.script_live = False  # a script is running
        self.action_since = 0.0
        self.paused = False  # the player has control (F8)
        self._woke: list[str] = []  # why the planner was woken, for its next turn's heading

    # --- the same interface as Display -----------------------------------------------------------------------

    def show(self, kind: str, text: str, data: dict | None = None, ok: bool = True) -> None:
        data, day = data or {}, self.days[-1]
        if kind == "day":
            if day.title in ("starting", text):  # the same day again: a restart resumed it
                day.title = text
            else:
                self.days.append(Day(text))
            self.scroll = 0
        elif kind == "report":
            day.report = text
        elif kind == "planner":
            if text == "thinking":  # a planner turn starts: one heading, saying what woke it
                woke = " · ".join(dict.fromkeys(self._woke)) or "the day began"
                at = clock(day.status.get("clock")).strip()
                day.lines.append(Text("◆ Planner", "bold magenta") + Text(f" · woke: {woke}", "grey62") + Text(f" · {at}" if at else "", "grey42"))
                self._woke = []
            self.thinking_since = time.monotonic() if text == "thinking" else None
            self.paused = text == "paused"
            if text in ("paused", "resumed"):
                day.lines.append(Text("⏸ you took control; the agent is paused" if text == "paused" else "▶ control given back; the agent goes on", "bold cyan"))
        elif kind == "script":  # the runner says when a script's process starts and ends
            self.script_live = text == "started"
            if text == "ended":
                day.running = None
        elif kind == "wake":
            self._woke.append(text.replace(" (it's still running)", "").replace("the script sent messages", "the script printed"))
        elif kind == "call":
            day.running = text.split(" ", 1)[-1]
            self.action_since = time.monotonic()
            if day.scripts and data.get("line"):
                day.scripts[-1]["line"] = data["line"]
        elif kind == "result":
            day.running = None
            day.lines += [nest(l) for l in action_lines(data, ok)] if data else [nest(Text(text[:300], "default" if ok else "red"))]
            if data.get("status"):
                day.status.update(data["status"])
        elif kind == "plan" and not data.get("script") and ok and text in ("waiting", "nothing is running; the farmer keeps busy nearby"):
            pass  # waiting is what the activity line shows
        elif kind == "plan" and data.get("script"):
            day.scripts.append({"goal": data.get("goal") or "a script", "source": data["script"], "line": None})
            day.lines.append(Text(f"⏺ Script {len(day.scripts)}", "bold yellow") + Text(f" · {data.get('goal') or ''}", "yellow"))
            if not ok:
                day.lines.append(nest(Text(text[:300], "red")))
        elif kind == "log":
            printed = text.removeprefix("printed: ").replace("\n", " ")
            day.lines.append(nest(Text("“" + (printed[:140] + "…" if len(printed) > 140 else printed) + "”", "bright_cyan")))
        elif kind in ("judge", "safety", "busy"):
            day.lines.append(nest(event_text(kind, text)) if kind != "busy" else event_text(kind, text))
        elif kind == "think":
            day.lines.append(Text("✻ ", "grey50") + Text(shorten(text, 2), "grey58 italic"))
        elif kind == "say":
            day.lines.append(Text("● ", "bright_white") + Text(shorten(text, 3), "bright_white"))
        elif kind == "notes":
            if text.startswith("diary"):
                day.diary = text.split(":", 1)[-1].strip()
            else:
                day.lines.append(Text("✎ " + notes_text(text, data)[:300], "magenta" if ok else "red"))
        else:
            style = "red" if not ok or kind == "error" else "green"
            day.lines.append(Text(("✗ " if style == "red" else "") + text[:300], style))
        day.lines = day.lines[-KEEP:]
        self.refresh()

    def load(self, days: list[list[dict]]) -> None:
        """Replay saved days (from days/*.jsonl), so past days can be looked through after a restart."""
        for steps in days:
            for s in steps:
                if not isinstance(s, dict) or not isinstance(s.get("kind"), str):
                    continue
                try:
                    self.show(s["kind"], str(s.get("text", "")), s.get("data") if isinstance(s.get("data"), dict) else {}, bool(s.get("ok", True)))
                except (KeyError, TypeError, ValueError, AttributeError):
                    continue  # a step this version can't draw
        for day in self.days:
            day.running = None
        self.thinking_since, self.script_live = None, False

    # --- running ---------------------------------------------------------------------------------------------

    def start(self) -> None:
        self._live = Live(console=self.console, screen=True, auto_refresh=True, refresh_per_second=4, transient=False, get_renderable=self.render)
        self._live.start()
        if sys.stdin.isatty():
            fd = sys.stdin.fileno()
            self._saved = termios.tcgetattr(fd)
            tty.setcbreak(fd)
            sys.stdout.write(MOUSE_ON)
            sys.stdout.flush()
            self._keys = threading.Thread(target=self._read_keys, args=(asyncio.get_running_loop(),), daemon=True, name="keys")
            self._keys.start()

    async def finished(self, notice: str) -> None:
        """The agent is done: keep the view up until q is pressed."""
        self.notice, self._quit = notice, asyncio.Event()
        self.refresh()
        if self._keys:
            await self._quit.wait()

    def stop(self) -> None:
        self._stop.set()
        if self._saved is not None:
            sys.stdout.write(MOUSE_OFF)
            sys.stdout.flush()
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self._saved)
            self._saved = None
        if self._live:
            self._live.stop()
            self._live = None

    async def watch(self, read: Callable[[], Awaitable[dict]]) -> None:
        """Keep the status current while the agent plays."""
        while not self._stop.is_set():
            try:
                self.days[-1].status.update(await read())
            except Exception:  # the game may be between states; try again shortly
                pass
            self.refresh()
            await asyncio.sleep(1)

    def refresh(self) -> None:
        if self._live:
            self._live.refresh()

    # --- drawing ---------------------------------------------------------------------------------------------

    def render(self) -> Layout:
        index = len(self.days) - 1 if self.viewing is None else self.viewing
        day, live = self.days[index], index == len(self.days) - 1
        root = Layout()
        queue = self._queue() if live else []
        root.split_column(Layout(self._header(index), size=1), Layout(name="body"), Layout(Group(*queue), size=max(1, len(queue)), visible=bool(queue)),
                          Layout(self._activity(day, live), size=1), Layout(self._input(live), size=3), Layout(self._hint(), size=1))
        root["body"].split_row(Layout(self._timeline(day), name="timeline", ratio=3), Layout(name="side", ratio=2, minimum_size=40))
        root["side"].split_column(Layout(self._plan(day), name="plan"), Layout(self._farmer(day, live), size=9))
        return root

    def _panel(self, body: Any, title: str, name: str | None = None, subtitle: str | None = None) -> Panel:
        focused = name is not None and self.focus == name
        return Panel(body, title=f"[bold]{title}[/]" if focused else title, title_align="left", subtitle=subtitle, subtitle_align="right",
                     box=box.ROUNDED, border_style="bright_blue" if focused else "grey35", padding=(0, 1))

    def _header(self, index: int) -> Table:
        tabs = Text()
        for i, d in enumerate(self.days):
            today = i == len(self.days) - 1
            tabs.append(f" {d.title.split(',')[0]}{' ●' if today else ''} ", "bold black on bright_blue" if i == index else "grey62")
            tabs.append(" ")
        grid = Table.grid(expand=True)
        grid.add_column(no_wrap=True)
        grid.add_column(justify="right", no_wrap=True)
        grid.add_row(Text(" dew ", "bold bright_white") + tabs, Text("←/→ days ", "grey42"))
        return grid

    def _timeline(self, day: Day) -> Panel:
        view = Rows(day.lines, self.scroll, lambda top: setattr(self, "_timeline_max", top))
        return self._panel(view, "Timeline", "timeline", "scrolled back · End follows" if self.scroll else None)

    def _plan(self, day: Day) -> Panel:
        if not day.scripts:
            return self._panel(Text("no script yet", "grey50"), "Plan", "plan")
        script = day.scripts[-1]
        at = script.get("line")
        where = f"script {len(day.scripts)} · line {at} of {len(script['source'].strip(chr(10)).split(chr(10)))}" if at else f"script {len(day.scripts)}"
        body = Code(script["goal"], script["source"], at, self.plan_top, self._plan_seen)
        return self._panel(body, "Plan", "plan", where)

    def _plan_seen(self, first: int, last_top: int) -> None:
        self._plan_shown, self._plan_max = first, last_top

    def _farmer(self, day: Day, live: bool) -> Panel:
        s = day.status
        if not live:
            return self._panel(Text(day.diary or "no diary for this day", "grey70"), "Diary")
        if not s:
            return self._panel(Text("waiting for the game", "grey50"), "Farmer")
        grid = Table.grid(padding=(0, 1), expand=True)
        grid.add_column(style="grey50", no_wrap=True)
        grid.add_column(ratio=1)
        grid.add_column(justify="right", no_wrap=True)
        when = " · ".join(str(v) for v in (s.get("date"), s.get("text") or clock(s.get("clock")), s.get("weather")) if v)
        grid.add_row("when", Text(when), "")
        grid.add_row("where", Text(str(s.get("place", "")), "bold"), "")
        for label, value, top, style in (("energy", s.get("energy"), s.get("maxe"), "yellow"), ("health", s.get("hp"), s.get("maxh"), "red")):
            value, top = int(value or 0), int(top or 1)
            low = value < 0.25 * top
            grid.add_row(label, ProgressBar(total=top, completed=value, complete_style="bright_red" if low else style, finished_style=style, style="grey23"),
                         Text(f"{value}/{top}", "bright_red" if low else "default"))
        grid.add_row("gold", Text(f"{s.get('money', '?')}g"), "")
        grid.add_row("pack", Text(f"{s.get('free', '?')} free" + (f" · {s['held']}" if s.get("held") else "")), "")
        if s.get("luck"):
            grid.add_row("luck", Text(str(s["luck"])), "")
        return self._panel(grid, "Farmer")

    def _queue(self) -> list[Text]:
        """Messages waiting for the planner, above the input as Claude Code shows queued messages."""
        items = self.queued()
        rows = [Text(" ⧗ ", "grey50") + Text(f"{who}: ", "bold cyan" if who == "you" else "bright_cyan") + Text(text[:200], "grey70") for who, text in items[-4:]]
        if len(items) > 4:
            rows.insert(0, Text(f" ⧗ … {len(items) - 4} more waiting", "grey50"))
        return rows

    def _activity(self, day: Day, live: bool) -> Text:
        """What the agent is doing this moment."""
        if not live:
            return Text(" a past day · → comes back to today", "grey50")
        spin = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"[int(time.monotonic() * 8) % 10]
        line = Text(" ")
        if self.paused:
            return line.append("⏸ Paused", "bold cyan").append(" · you have control in the game · F8 gives it back", "grey50")
        if self.thinking_since is not None:
            line.append(f"{spin} Planner thinking", "bold magenta").append(f" · {elapsed(self.thinking_since)}", "grey50")
            if self.script_live:
                line.append(f" · meanwhile {self._where(day)}", "grey50")
        elif day.running:
            line.append(f"{spin} ", "yellow").append(day.running, "bold yellow").append(f" · {elapsed(self.action_since)} · {self._where(day)}", "grey50")
        elif self.script_live:
            line.append(f"{spin} Script running", "yellow").append(f" · {self._where(day)}", "grey50")
        else:
            line.append("⟳ Idle", "blue").append(" · guarding the farmer until the planner sends a script", "grey50")
        return line

    def _where(self, day: Day) -> str:
        if not day.scripts:
            return ""
        script = day.scripts[-1]
        return f"script {len(day.scripts)}, line {script.get('line') or 1} of {len(script['source'].strip(chr(10)).split(chr(10)))}"

    def _input(self, live: bool) -> Panel:
        prompt = Text("› ", "bold bright_blue") + Text(self.typed, "bright_white") + Text("▏", "grey62")
        if not self.typed:
            prompt.append("steer the agent" if live else "a past day: → comes back to today", "grey50")
        return Panel(prompt, box=box.ROUNDED, border_style="grey50")

    def _hint(self) -> Text:
        if self.notice:
            return Text("  " + self.notice, "green")
        return Text("  Enter queues a message for the planner · Tab switches pane · ↑↓ PgUp PgDn wheel scroll · End follows"
                    + (f" · {self.header}" if self.header else ""), "grey42")

    # --- keys ------------------------------------------------------------------------------------------------

    def _read_keys(self, loop: asyncio.AbstractEventLoop) -> None:
        fd = sys.stdin.fileno()
        while not self._stop.is_set():
            if not select.select([fd], [], [], 0.2)[0]:
                continue
            chunk = os.read(fd, 256).decode(errors="ignore")
            for key in KEY.findall(chunk):
                loop.call_soon_threadsafe(self._key, key)

    def _key(self, key: str) -> None:
        last = len(self.days) - 1
        index = last if self.viewing is None else self.viewing
        if wheel := MOUSE.fullmatch(key):
            button, x = int(wheel[1]), int(wheel[2])
            if button in (64, 65):
                self.focus = "timeline" if x < self.console.size.width * 0.6 else "plan"
                self._scroll(3 if button == 64 else -3)
        elif key in ("\x1b[D",):  # left
            self.viewing, self.scroll, self.plan_top = max(0, index - 1), 0, None
        elif key in ("\x1b[C",):  # right
            self.viewing = None if index + 1 >= last else index + 1
            self.scroll, self.plan_top = 0, None
        elif key == "\x1b[A":
            self._scroll(1)
        elif key == "\x1b[B":
            self._scroll(-1)
        elif key == "\x1b[5~":
            self._scroll(10)
        elif key == "\x1b[6~":
            self._scroll(-10)
        elif key in ("\x1b[H", "\x1b[1~"):  # Home: the oldest
            self._scroll(10**6)
        elif key in ("\x1b[F", "\x1b[4~"):  # End: follow the newest again
            self._scroll(-10**6)
        elif key == "\t":
            self.focus = "plan" if self.focus == "timeline" else "timeline"
        elif key.startswith("\x1b"):
            pass
        elif self._quit is not None and key in ("q", "\r", "\n") and not self.typed:
            self._quit.set()
        elif key in ("\r", "\n"):
            text, self.typed = self.typed.strip(), ""
            if text:
                self.on_steer(f"PLAYER: {text}")
                self.notice = "queued for the planner; it reads it now, or right after its current thought"
        elif key in ("\x7f", "\b"):
            self.typed = self.typed[:-1]
        elif key.isprintable():
            self.typed += key
            self.notice = ""
        self.refresh()

    def _scroll(self, rows: int) -> None:
        """Positive scrolls back (up), negative forward."""
        if self.focus == "timeline":
            self.scroll = max(0, min(self.scroll + rows, getattr(self, "_timeline_max", 0)))
        else:
            top = self.plan_top if self.plan_top is not None else getattr(self, "_plan_shown", 1)
            self.plan_top = None if rows <= -10**6 else max(1, min(top - rows, max(1, getattr(self, "_plan_max", 1))))
            if rows >= 10**6:
                self.plan_top = 1


KEY = re.compile(r"\x1b\[<\d+;\d+;\d+[mM]|\x1b\[\d*~|\x1b\[[A-Za-z]|\x1bO[A-Za-z]|\x1b|[\s\S]")
MOUSE = re.compile(r"\x1b\[<(\d+);(\d+);(\d+)[mM]")
MOUSE_ON, MOUSE_OFF = "\x1b[?1000h\x1b[?1006h", "\x1b[?1000l\x1b[?1006l"  # wheel events, in the SGR encoding


class Rows:
    """Lines wrapped to the pane's width and cut to its height, so scrolling moves by screen rows."""

    def __init__(self, lines: list[Text], back: int, report: Callable[[int], None]):
        self.lines, self.back, self.report = lines, back, report

    def __rich_console__(self, console: Console, options: Any):
        width, height = options.max_width, options.height or 20
        rows: list[Text] = []
        for line in self.lines[-600:]:
            rows.extend(hang(console, line, width))
        self.report(max(0, len(rows) - height))
        end = len(rows) - min(self.back, max(0, len(rows) - height))
        shown = rows[max(0, end - height):end]
        yield from (Text("\n").join(shown) if shown else Text("nothing yet", "grey50"),)


class Code:
    """The running script, scrolled to its current line unless the reader scrolled it."""

    def __init__(self, goal: str, source: str, line: int | None, top: int | None, report: Callable[[int, int], None]):
        self.goal, self.source, self.line, self.top, self.report = goal, source.strip("\n"), line, top, report

    def __rich_console__(self, console: Console, options: Any):
        # drawn here rather than in a Group, which wouldn't pass the pane's height on
        goal = Text(self.goal, "yellow").wrap(console, options.max_width)
        yield from goal
        lines = self.source.split("\n")
        room = max(3, (options.height or 12) - len(goal))
        first = self.top or max(1, min((self.line or 1) - room // 3, len(lines) - room + 1))
        self.report(first, max(1, len(lines) - room + 1))
        yield Syntax(self.source, "python", line_numbers=True, theme="ansi_dark", background_color="default", word_wrap=False,
                     line_range=(first, first + room - 1), highlight_lines={self.line} if self.line else set())


def hang(console: Console, line: Text, width: int) -> list[Text]:
    """Wrap a line so its continuation rows start under its text: past its indent, and past a leading marker like ✻."""
    plain = line.plain
    indent = len(plain) - len(plain.lstrip(" "))
    if plain[indent:indent + 2] in ("✻ ", "● ", "⏺ ", "◆ ", "✎ ", "✗ ", "✓ "):
        indent += 2
    rows = list(line.wrap(console, max(10, width - indent), overflow="fold"))
    return rows[:1] + [Text(" " * indent) + r for r in rows[1:]]


def nest(line: Text) -> Text:
    """A line under the script above it: flush left, like everything else."""
    indent = len(line.plain) - len(line.plain.lstrip())
    return line.divide([indent])[-1] if indent else line


def elapsed(since: float) -> str:
    s = int(time.monotonic() - since)
    return f"{s // 60}:{s % 60:02d}" if s >= 60 else f"{s}s"


def shorten(text: str, lines: int) -> str:
    kept = [l.strip() for l in text.split("\n") if l.strip()]
    short = " ".join(kept[:lines])
    return short[:400] + (" …" if len(kept) > lines or len(short) > 400 else "")
