"""The full-screen view, kept plain: a transcript of the day (reasoning, scripts, actions, in order), a sidebar with the running plan
and the farmer's status, and an input line to steer the agent. One tab per day.

Keys: ←/→ days, ↑/↓ scroll, type and Enter to steer (/later TEXT waits until the agent next wakes), q to quit when done.
"""

from __future__ import annotations

import asyncio
import os
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
from rich.padding import Padding
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
        self.scroll = 0  # actions scrolled back from the newest
        self.typed = ""
        self.notice = ""
        self.on_steer: Callable[[str], None] = lambda text: None
        self.on_later: Callable[[str], None] = lambda text: None
        self._live: Live | None = None
        self._keys: threading.Thread | None = None
        self._saved: Any = None
        self._stop = threading.Event()
        self._quit: asyncio.Event | None = None
        self.thinking_since: float | None = None  # the planner is waiting on the model
        self.script_live = False  # a script is running
        self.action_since = 0.0

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
            self.thinking_since = time.monotonic() if text == "thinking" else None
        elif kind == "wake":
            if any(w in text for w in ("script ended", "script raised", "script was ended", "script asks", "the day ended")):
                self.script_live = False
            day.lines.append(Text("⏸ woken: " + text, "grey50"))
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
        elif kind == "plan" and data.get("script"):
            day.scripts.append({"goal": data.get("goal") or "a script", "source": data["script"], "line": None})
            self.script_live = ok
            day.lines.append(Text(f"⏺ Script {len(day.scripts)}", "bold yellow") + Text(f" · {data.get('goal') or ''}", "yellow"))
            if not ok:
                day.lines.append(nest(Text(text[:300], "red")))
        elif kind in ("log", "judge", "safety", "busy"):
            day.lines.append(nest(event_text(kind, text)) if kind != "busy" else event_text(kind, text))
        elif kind == "think":
            day.lines.append(Text("✻ ", "grey50") + Text(shorten(text, 3), "grey62 italic"))
        elif kind == "say":
            day.lines.append(Text("● ", "bright_white") + Text(shorten(text, 4), "bright_white"))
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
        height = self.console.size.height
        root = Layout()
        root.split_column(Layout(self._tabs(index), size=1), Layout(name="body"), Layout(self._activity(day, live), size=1), Layout(self._input(live), size=3), Layout(self._hint(live), size=1))
        root["body"].split_row(Layout(self._transcript(day, height - 7), ratio=3), Layout(self._sidebar(day, live), ratio=2, minimum_size=34))
        return root

    def _tabs(self, index: int) -> Text:
        tabs = Text()
        for i, d in enumerate(self.days):
            name = d.title.split(",")[0]
            tabs.append(f" {name}{' ●' if i == len(self.days) - 1 else ''} ", "bold" if i == index else "grey50")
            tabs.append("  ")
        return tabs

    def _transcript(self, day: Day, rows: int) -> Text | Group:
        end = len(day.lines) - self.scroll
        shown = day.lines[max(0, end - rows):end]
        return Padding(Group(*shown), (0, 1)) if shown else Text("")

    def _activity(self, day: Day, live: bool) -> Text:
        """What the agent is doing this moment."""
        if not live:
            return Text("")
        spin = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"[int(time.monotonic() * 8) % 10]
        line = Text(" ")
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
        total = len(script["source"].strip("\n").split("\n"))
        return f"script {len(day.scripts)}, line {script.get('line') or 1} of {total}"

    def _sidebar(self, day: Day, live: bool) -> Padding:
        parts: list = []
        if day.scripts:
            script = day.scripts[-1]
            lines = script["source"].strip("\n").split("\n")
            at, room = script.get("line") or 1, 10
            first = max(1, min(at - room // 2, len(lines) - room + 1))
            total = len(lines)
            where = f"script {len(day.scripts)} · line {at} of {total}" if script.get("line") else f"script {len(day.scripts)} · starting"
            parts += [Text("PLAN  ", "grey50") + Text(where, "grey62"), Text(script["goal"], "yellow"),
                      Syntax("\n".join(lines), "python", line_numbers=True, theme="ansi_dark", background_color="default",
                             line_range=(first, first + room - 1), highlight_lines={at} if script.get("line") else set()), Text("")]
        s = day.status
        if s and live:
            parts += [Text(f"{s.get('date', day.title)} · {s.get('text') or clock(s.get('clock'))}" + (f" · {s['weather']}" if s.get("weather") else ""), "grey62"),
                      Text(str(s.get("place", "")), "bold"),
                      bar("⚡", s.get("energy"), s.get("maxe"), "yellow"), bar("♥", s.get("hp"), s.get("maxh"), "red"),
                      Text(f"{s.get('money', '?')}g · {s.get('free', '?')} free" + (f" · {s['held']}" if s.get("held") else ""), "grey62")]
        elif day.diary:
            parts += [Text("DIARY", "grey50"), Text(day.diary, "grey70")]
        return Padding(Group(*parts), (0, 1, 0, 2), style="")

    def _input(self, live: bool) -> Panel:
        if not live:
            return Panel(Text("a past day · → to come back", "grey50"), box=box.ROUNDED, border_style="grey35")
        prompt = Text("> ", "bold") + Text(self.typed, "bright_white") + Text("▏", "grey62")
        if not self.typed:
            prompt.append("steer the agent", "grey50")
        return Panel(prompt, box=box.ROUNDED, border_style="grey50")

    def _hint(self, live: bool) -> Text:
        hint = self.notice or "Enter stops the script · /later TEXT waits · ←/→ days · ↑/↓ scroll"
        return Text("  " + hint + ("" if not self.header else f" · {self.header}"), "green" if self.notice else "grey42")

    # --- keys ------------------------------------------------------------------------------------------------

    def _read_keys(self, loop: asyncio.AbstractEventLoop) -> None:
        fd = sys.stdin.fileno()
        while not self._stop.is_set():
            if not select.select([fd], [], [], 0.2)[0]:
                continue
            key = os.read(fd, 8).decode(errors="ignore")
            loop.call_soon_threadsafe(self._key, key)

    def _key(self, key: str) -> None:
        last = len(self.days) - 1
        index = last if self.viewing is None else self.viewing
        if key == "\x1b[D":  # left
            self.viewing, self.scroll = max(0, index - 1), 0
        elif key == "\x1b[C":  # right
            self.viewing = None if index + 1 >= last else index + 1
            self.scroll = 0
        elif key == "\x1b[A":  # up
            self.scroll = min(self.scroll + 3, max(0, len(self.days[index].lines) - 3))
        elif key == "\x1b[B":  # down
            self.scroll = max(0, self.scroll - 3)
        elif key.startswith("\x1b"):
            pass
        elif self._quit is not None and key in ("q", "\r", "\n") and not self.typed:
            self._quit.set()
        elif key in ("\r", "\n"):
            text, self.typed = self.typed.strip(), ""
            if text.startswith("/later "):
                self.on_later(f"PLAYER: {text[7:].strip()}")
                self.notice = "sent; the agent gets it when it next wakes"
            elif text:
                self.on_steer(f"PLAYER: {text}")
                self.notice = "sent; stopping the script and waking the agent"
        elif key in ("\x7f", "\b"):
            self.typed = self.typed[:-1]
        elif key.isprintable():
            self.typed += key
            self.notice = ""
        self.refresh()


def nest(line: Text) -> Text:
    """Under the script above it, as Claude Code shows a tool's output."""
    indent = len(line.plain) - len(line.plain.lstrip())
    return Text("  ⎿ ", "grey42") + (line.divide([indent])[-1] if indent else line)


def elapsed(since: float) -> str:
    s = int(time.monotonic() - since)
    return f"{s // 60}:{s % 60:02d}" if s >= 60 else f"{s}s"


def shorten(text: str, lines: int) -> str:
    kept = [l.strip() for l in text.split("\n") if l.strip()]
    short = " ".join(kept[:lines])
    return short[:400] + (" …" if len(kept) > lines or len(short) > 400 else "")


def bar(mark: str, value: Any, top: Any, style: str, width: int = 16) -> Text:
    value, top = int(value or 0), int(top or 1)
    filled = round(width * max(0, min(value, top)) / top)
    low = value < 0.25 * top
    return Text(f"{mark} ") + Text("█" * filled, "bold " + ("bright_red" if low else style)) + Text("░" * (width - filled), "grey35") + Text(f" {value}/{top}", "grey70")
