"""Where a save's data lives: <data>/<save folder>/ with memory.json, diary.jsonl, days/, context.json, screenshots/."""

from __future__ import annotations

import json
import os
import re
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .memory import Memory, write_atomic

SEASONS = ["spring", "summer", "fall", "winter"]
OLD_DATA = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "stardew-agent"  # before data lived beside the user
MAX_STRING = 20_000  # longer strings in a day's transcript (images) are left out


def data_root(flag: str | os.PathLike | None = None, env: Mapping[str, str] = os.environ, cwd: Path | None = None) -> Path:
    """--data, else $STARDEW_DATA, else ./stardew-data."""
    if flag:
        return Path(flag).expanduser().absolute()
    if env.get("STARDEW_DATA"):
        return Path(env["STARDEW_DATA"]).expanduser().absolute()
    return (cwd or Path.cwd()) / "stardew-data"


def day_key(date: str) -> str:
    """'Tue spring 4, year 1' -> '0001-spring-04', the name of that day's transcript."""
    if m := re.search(r"(spring|summer|fall|winter)\s+(\d+),\s*year\s+(\d+)", date, re.I):
        return f"{int(m[3]):04d}-{m[1].lower()}-{int(m[2]):02d}"
    return re.sub(r"[^A-Za-z0-9-]+", "-", date).strip("-") or "unknown"


def day_order(key: str) -> tuple:
    if m := re.fullmatch(r"(\d+)-(spring|summer|fall|winter)-(\d+)", key):
        return (int(m[1]), SEASONS.index(m[2]), int(m[3]))
    return (-1, 0, 0)


def clean(value: Any) -> Any:
    """Data fit for a JSON line: images and other huge strings left out."""
    if type(value).__name__ == "Image":
        return "(screenshot)"
    if isinstance(value, dict):
        if value.get("type") == "image":
            return {"type": "text", "text": "(screenshot not kept)"}
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, str) and len(value) > MAX_STRING:
        return value[:2000] + f" …({len(value) - 2000} characters left out)"
    return value


class SaveData:
    def __init__(self, root: Path, folder: str):
        self.root, self.folder = root, folder
        self.dir = root / folder
        self.today: str | None = None  # the day steps are recorded to
        self._pending: list[str] = []  # steps shown before the day was known

    memory_file = property(lambda self: self.dir / "memory.json")
    diary_file = property(lambda self: self.dir / "diary.jsonl")
    days_dir = property(lambda self: self.dir / "days")
    context_file = property(lambda self: self.dir / "context.json")
    screenshots = property(lambda self: self.dir / "screenshots")

    def prepare(self, old_root: Path = OLD_DATA) -> list[str]:
        """Move data over from the old place, import notes.md, and make the folders. Returns what to tell the user."""
        said = []
        old = old_root / self.folder
        if old.is_dir() and not self.dir.exists():
            self.root.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old), str(self.dir))
            said.append(f"moved this save's agent data from {old} to {self.dir}")
        self.dir.mkdir(parents=True, exist_ok=True)
        self.days_dir.mkdir(exist_ok=True)
        self.screenshots.mkdir(exist_ok=True)
        memory = Memory(self.memory_file)
        if note := memory.import_notes(self.dir / "notes.md"):
            said.append(note)
        return said

    # --- the days' transcripts ------------------------------------------------------------------------------

    def record(self, kind: str, text: str, data: dict | None = None, ok: bool = True) -> None:
        """Append a step shown to the day's transcript."""
        line = json.dumps({"kind": kind, "text": text, "ok": ok, "data": clean(data or {})}, default=str, ensure_ascii=False)
        if kind == "day":
            self.today = day_key(text)
        if self.today is None:
            self._pending.append(line)
            return
        self.days_dir.mkdir(parents=True, exist_ok=True)
        with (self.days_dir / f"{self.today}.jsonl").open("a") as f:
            f.write("".join(l + "\n" for l in [*self._pending, line]))
        self._pending = []

    def load_days(self, count: int = 7) -> list[list[dict]]:
        """The last few days' steps, oldest day first."""
        if not self.days_dir.is_dir():
            return []
        files = sorted(self.days_dir.glob("*.jsonl"), key=lambda p: day_order(p.stem))[-count:]
        days = []
        for path in files:
            steps = []
            for l in path.read_text().splitlines():
                try:
                    steps.append(json.loads(l))
                except json.JSONDecodeError:
                    continue  # a line cut off by a crash
            days.append(steps)
        return days

    # --- the planner's context ------------------------------------------------------------------------------

    def save_context(self, date: str, messages: list[dict], turns: int = 0) -> None:
        write_atomic(self.context_file, json.dumps({"date": date, "turns": turns, "messages": clean(messages)}, default=str, ensure_ascii=False))

    def load_context(self, date: str) -> dict | None:
        """The planner's saved context, if it's from this game day."""
        try:
            saved = json.loads(self.context_file.read_text())
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(saved, dict) or saved.get("date") != date or not isinstance(saved.get("messages"), list) or not saved["messages"]:
            return None
        return saved
