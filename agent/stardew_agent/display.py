"""What the terminal shows: one line per action, status only when it changes, the planner's scripts and thoughts kept short."""

from __future__ import annotations

import re
from typing import Any

from rich.console import Console
from rich.rule import Rule
from rich.syntax import Syntax
from rich.text import Text

QUIET_KEYS = {"spot", "of", "worth_about", "from", "to", "note"}  # result fields the line can do without
REPORT_KEYS = {"date": "", "time": "", "weather": "", "luck": "luck ", "farm_crops": "crops ", "quests": "quests: "}


class Display:
    def __init__(self, console: Console, verbose: bool = False):
        self.console, self.verbose = console, verbose
        self.last: dict = {}  # the status last shown
        self.moved = False  # arrived somewhere since the map was last drawn
        self.spinner = None

    def show(self, kind: str, text: str, data: dict | None = None, ok: bool = True) -> None:
        if kind == "call":
            self._spin(text)
            return
        self._unspin()
        getattr(self, f"_{kind}", self._plain)(kind, text, data or {}, ok)

    # --- kinds ---------------------------------------------------------------------------------------------

    def _result(self, kind, text, data, ok):
        if not data:
            return self._plain(kind, text, data, ok)
        for line in action_lines(data, ok):
            self.console.print(line)
        self._status(data.get("status") or {})

    def _plan(self, kind, text, data, ok):
        if not data.get("script"):
            return self._plain(kind, text, data, ok)
        self.console.print(Text("▶ ", "bold yellow") + Text(data.get("goal") or "a script", "bold yellow"))
        lines = data["script"].strip("\n").split("\n")
        shown = lines if self.verbose else lines[:14]
        self.console.print(Syntax("\n".join(shown), "python", line_numbers=True, theme="ansi_dark", background_color="default", word_wrap=True))
        if len(shown) < len(lines):
            self.console.print(Text(f"    … {len(lines) - len(shown)} more lines", "grey50"))
        if not ok:
            self.console.print(Text("  " + text, "red"))

    def _planner(self, kind, text, data, ok):
        pass  # the full-screen view shows this as its activity line

    def _wake(self, kind, text, data, ok):
        self.console.print(Text("⏸ woken: " + text, "grey62"))

    def _think(self, kind, text, data, ok):
        lines = [l for l in text.split("\n") if l.strip()]
        short = " ".join(lines if self.verbose else lines[:2])
        if not self.verbose and len(short) > 220:
            short = short[:220] + " …"
        self.console.print(Text("· " + short, "dim italic"))

    def _say(self, kind, text, data, ok):
        self.console.print(Text("» " + (text if self.verbose else text[:400]), "cyan"))

    def _log(self, kind, text, data, ok):
        self.console.print(event_text(kind, text))

    def _judge(self, kind, text, data, ok):
        self.console.print(event_text(kind, text))

    def _safety(self, kind, text, data, ok):
        self.console.print(event_text(kind, text))

    def _busy(self, kind, text, data, ok):
        self.console.print(event_text(kind, text))

    def _day(self, kind, text, data, ok):
        self.last = {}
        self.console.print()
        self.console.print(Rule(Text(text, "bold green"), style="green"))

    def _report(self, kind, text, data, ok):
        if self.verbose:
            return self.console.print(Text(text, "grey62"))
        facts = {}
        for line in text.split("\n"):
            key, _, value = line.partition(":")
            if key in REPORT_KEYS and value.strip():
                facts[key] = REPORT_KEYS[key] + value.strip()
        self.console.print(Text("  " + " · ".join(facts.values()), "grey62"))

    def _notes(self, kind, text, data, ok):
        self.console.print(Text("✎ " + notes_text(text, data, self.verbose), "magenta" if ok else "red"))
        if self.verbose:
            for line in data.get("changes") or []:
                self.console.print(Text("    " + line, "grey62"))

    def _plain(self, kind, text, data, ok):
        style = "red" if not ok or kind == "error" else {"info": "green"}.get(kind, "")
        self.console.print(Text(("✗ " if not ok or kind == "error" else "") + (text if self.verbose else text[:600]), style))

    # --- helpers -------------------------------------------------------------------------------------------

    def _status(self, s: dict) -> None:
        """A status line when something worth seeing changed."""
        if not s:
            return
        last, show = self.last, False
        if s.get("place") != last.get("place"):
            show = self.moved = True
        for key in ("money", "hp"):
            show |= s.get(key) != last.get(key)
        show |= s.get("free") == 0 and last.get("free") != 0  # the backpack just filled
        show |= abs((s.get("energy") or 0) - (last.get("energy") or 0)) >= 20
        self.last = s
        if not show:
            return
        energy = s.get("energy") or 0
        low = energy < 0.25 * (s.get("maxe") or 270)
        line = Text("      ", "grey50")
        for part, style in ((s.get("place"), "bold grey70"), (f"⚡{int(energy)}/{s.get('maxe')}", "yellow" if low else "grey62"), (f"♥{s.get('hp')}/{s.get('maxh')}", "grey62"),
                            (f"{s.get('money')}g", "grey62"), (f"{s.get('free')} free", "grey62")):
            line.append(str(part), style).append("  ")
        self.console.print(line)

    def _spin(self, text: str) -> None:
        self._unspin()
        label = re.sub(r"^#\d+ ", "", text)
        self.spinner = self.console.status(Text(label, "grey62"), spinner="dots")
        self.spinner.start()

    def _unspin(self) -> None:
        if self.spinner:
            self.spinner.stop()
            self.spinner = None


def notes_text(text: str, data: dict, verbose: bool = False) -> str:
    """A memory change, the diary, or a consolidation, on one line."""
    if data.get("value") and not text.startswith(("forgot", "remember refused", "forget refused")):
        text = f"{text}: {data['value']}"
    return text if verbose else text[:400]


def action_lines(data: dict, ok: bool) -> list[Text]:
    """A finished action: one line, plus anything said or shown meanwhile."""
    s, value, entries = data.get("status") or {}, data.get("value"), data.get("journal") or []
    line = Text(f"{clock(s.get('clock'))} ", "grey50")
    line.append("✓ " if ok else "✗ ", "green" if ok else "bold red")
    line.append(call_text(data["name"], data.get("args") or {}), "bold bright_white" if ok else "bold red")
    if ok and (summary := summarize(value)):
        line.append(f"  {summary}", "default")
    if not ok:
        line.append(f"  {(data.get('error') or '')[:160]}", "red")
    places = [e["text"].removeprefix("entered ") for e in entries if e["kind"] == "place"]
    if len(places) > 1:
        line.append("  via " + " → ".join(places[:-1]), "grey50")
    for name, amount in items(entries).items():
        line.append(f"  {amount:+d} {name}", "green" if amount > 0 else "red")
    lines = [line]
    for e in entries:
        if e["kind"] in ("dialogue", "letter", "question", "level", "message", "quest", "chat", "event"):
            lines.append(Text(f"      {e['kind']}: {e['text'][:200]}", "cyan" if e["kind"] in ("dialogue", "chat") else "grey62"))
    return lines


EVENTS = {"log": ("│ ", "bright_cyan", "log: "), "judge": ("? ", "magenta", ""), "safety": ("⚠ ", "bold yellow", "SAFETY: "), "busy": ("⟳ ", "blue", "")}


def event_text(kind: str, text: str) -> Text:
    """Something a script or the runner reported: a log line, a judgement, a safety stop, idle work."""
    mark, style, prefix = EVENTS[kind]
    return Text("  " + mark + text.removeprefix(prefix)[:300], style)


def clock(value: Any) -> str:
    if not isinstance(value, int):
        return "     "
    h, m = divmod(value, 100)
    return f"{h % 24:02d}:{m:02d}"


MAIN_ARGS = {"item", "target", "place", "person", "recipe", "question", "when"}  # shown without their name


def call_text(name: str, args: dict) -> str:
    shown = []
    for key, value in args.items():
        if value in (None, False, [], {}):
            continue
        if key in ("area", "tiles") and isinstance(value, (list, tuple)):
            value = area_text(value)
        shown.append(str(value) if key in MAIN_ARGS else f"{key}={value}")
    return f"{name} {' '.join(shown)}"[:80].strip()


def area_text(value: list) -> str:
    if len(value) == 4 and all(isinstance(v, int) for v in value):
        return f"({value[0]},{value[1]})-({value[2]},{value[3]})"
    return f"{len(value)} tiles"


def summarize(value: Any) -> str:
    if isinstance(value, dict):
        parts = [f"{k} {v}" for k, v in value.items() if k not in QUIET_KEYS and k != "items" and v not in (None, [], {}, "")]
        return ", ".join(parts)[:120]
    if isinstance(value, list):
        return f"{len(value)} found" if value else "none"
    return "" if value in (None, "done", True) else str(value)[:120]


def items(entries: list[dict]) -> dict[str, int]:
    """Item changes from the journal, added up."""
    out: dict[str, int] = {}
    for e in entries:
        if e["kind"] == "item":
            name, _, amount = e["text"].rpartition(" ")
            try:
                out[name] = out.get(name, 0) + int(amount)
            except ValueError:
                continue
    return {k: v for k, v in out.items() if v}
