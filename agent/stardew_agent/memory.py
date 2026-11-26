"""The agent's lasting memory: short facts by dotted key, kept in memory.json, edited by the agent by day and by the planner at night."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

MAX_KEY = 60
MAX_VALUE = 500
MAX_KEYS = 150
KEY = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-]*$")


def write_atomic(path: Path, text: str) -> None:
    """Write a temporary file beside path, then replace path with it, so a crash leaves the old or the new file whole."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


def dump(facts: dict[str, str]) -> str:
    return json.dumps(dict(sorted(facts.items())), indent=1, ensure_ascii=False)


def key_error(key: Any) -> str | None:
    if not isinstance(key, str) or not key.strip():
        return "a key is a short dotted name, like shop.pierre.hours"
    if len(key) > MAX_KEY:
        return f"keys are at most {MAX_KEY} characters"
    if not KEY.match(key):
        return "keys use letters, digits, _, - and dots, like shop.pierre.hours"
    return None


class Memory:
    """Facts by key. Without a path it lives only in memory (for tests and tools)."""

    def __init__(self, path: Path | None = None):
        self.path = path
        self.facts: dict[str, str] = {}
        if path and path.exists():
            data = json.loads(path.read_text() or "{}")
            self.facts = {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}

    def save(self) -> None:
        if self.path:
            write_atomic(self.path, dump(self.facts) + "\n")

    def remember(self, key: Any, value: Any) -> tuple[bool, str]:
        if error := key_error(key):
            return False, error
        if not isinstance(value, str) or not value.strip():
            return False, "value is short text; to remove a fact use forget"
        if len(value) > MAX_VALUE:
            return False, f"values are at most {MAX_VALUE} characters; split it into several keys"
        key = key.strip()
        if key not in self.facts and len(self.facts) >= MAX_KEYS:
            return False, f"memory is full ({MAX_KEYS} keys): forget stale facts first, or replace an existing key"
        old = self.facts.get(key)
        self.facts[key] = value.strip()  # written to disk when the day ends, as the game saves only then
        return True, f"{'replaced' if old is not None else 'remembered'} {key} ({len(self.facts)} keys)"

    def forget(self, key: Any) -> tuple[bool, str]:
        if not isinstance(key, str) or key not in self.facts:
            return False, f"there is no key {key!r} to forget"
        del self.facts[key]
        return True, f"forgot {key} ({len(self.facts)} keys)"

    def use(self, tool: str, args: dict) -> tuple[bool, str]:
        """The remember and forget tools, for the agent and the planner."""
        if tool == "remember":
            if set(args) != {"key", "value"}:
                return False, "remember takes key (a short dotted name) and value (short text)"
            return self.remember(args["key"], args["value"])
        if set(args) != {"key"}:
            return False, "forget takes key"
        return self.forget(args["key"])

    @staticmethod
    def note(tool: str, args: dict, ok: bool, text: str) -> tuple[str, dict]:
        """How a memory edit is shown: its text and data, for a "notes" step."""
        return text if ok else f"{tool} refused: {text}", {"key": args.get("key"), "value": args.get("value")}

    def render(self) -> str:
        return "\n".join(f"{k}: {' / '.join(l.strip() for l in v.splitlines() if l.strip())}" for k, v in sorted(self.facts.items()))

    def section(self) -> str:
        return "# Memory\n" + (self.render() or "(empty: keep facts with the remember tool)")

    def import_notes(self, notes: Path) -> str | None:
        """Once: move the old free-form notes.md into the key "notes" (longer than a value may be; the planner may split it)."""
        if not notes.exists():
            return None
        text = notes.read_text().strip()
        if text:
            self.facts["notes"] = text
            self.save()
        notes.rename(notes.with_name(notes.name + ".imported"))
        return f"imported {notes} into the memory key \"notes\" (the file is now {notes.name}.imported)"

