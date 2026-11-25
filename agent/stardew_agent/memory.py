"""The agent's lasting memory: short facts by dotted key, kept in memory.json and consolidated each night."""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, field
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

    def render(self) -> str:
        return "\n".join(f"{k}: {' / '.join(l.strip() for l in v.splitlines() if l.strip())}" for k, v in sorted(self.facts.items()))

    def section(self) -> str:
        return "# Memory\n" + (self.render() or "(empty: keep facts with the remember tool)")

    def import_notes(self, notes: Path) -> str | None:
        """Once: move the old free-form notes.md into the key "notes" (longer than a value may be; the nightly consolidation may split it)."""
        if not notes.exists():
            return None
        text = notes.read_text().strip()
        if text:
            self.facts["notes"] = text
            self.save()
        notes.rename(notes.with_name(notes.name + ".imported"))
        return f"imported {notes} into the memory key \"notes\" (the file is now {notes.name}.imported)"


# --- consolidation ---------------------------------------------------------------------------------------

CONSOLIDATE = (Path(__file__).parent / "prompts" / "memory.md").read_text()


class Rejected(ValueError):
    """A consolidation which isn't applied."""


@dataclass
class Change:
    before: int
    after: int
    merged: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        extra = f", added {len(self.added)}" if self.added else ""
        return f"memory consolidated: {self.before} → {self.after} keys (merged {len(self.merged)}, dropped {len(self.dropped)}, changed {len(self.changed)}{extra})"

    def details(self, old: dict[str, str], new: dict[str, str]) -> list[str]:
        return ([f"merged {k}" for k in self.merged] + [f"dropped {k}: {old[k]}" for k in self.dropped]
                + [f"changed {k}: {new[k]}" for k in self.changed] + [f"added {k}: {new[k]}" for k in self.added])


def words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def parse(reply: str, old: dict[str, str]) -> dict[str, str]:
    """The model's new memory, if it keeps to the rules."""
    text = reply.strip()
    if fence := re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.S):
        text = fence.group(1)
    try:
        new = json.loads(text)
    except json.JSONDecodeError as ex:
        raise Rejected(f"the reply isn't JSON ({ex.msg})") from None
    if not isinstance(new, dict):
        raise Rejected("the reply isn't a JSON object")
    for k, v in new.items():
        if not isinstance(v, str):
            raise Rejected(f"the value of {k!r} isn't text")
        if error := key_error(k):
            raise Rejected(f"bad key {k!r}: {error}")
        if len(v) > MAX_VALUE and v != old.get(k):
            raise Rejected(f"the value of {k!r} is over {MAX_VALUE} characters")
    if len(new) > MAX_KEYS:
        raise Rejected(f"{len(new)} keys is over the limit of {MAX_KEYS}")
    # judge by what's kept, not by key count: splitting one long fact into several is fine
    gone = [k for k in old if k not in new]
    if len(old) >= 4 and len(gone) > len(old) / 2:
        raise Rejected(f"it would drop {len(gone)} of {len(old)} keys")
    kept, had = sum(map(len, new.values())), sum(map(len, old.values()))
    if had and kept < had / 4:
        raise Rejected(f"it would keep only {kept} of {had} characters")
    return new


def compare(old: dict[str, str], new: dict[str, str]) -> Change:
    change = Change(len(old), len(new))
    change.added = sorted(k for k in new if k not in old)
    change.changed = sorted(k for k in new if k in old and new[k] != old[k])
    rewritten = [words(new[k]) for k in change.added + change.changed]
    for k in sorted(k for k in old if k not in new):
        said = words(old[k])
        kept = any(said and len(said & w) >= len(said) / 2 for w in rewritten)
        (change.merged if kept else change.dropped).append(k)
    return change


async def consolidate(memory: Memory, date: str, diary: str, client: Any, model: str) -> Change:
    """Ask the model for the whole new memory; apply it (keeping a backup) only if it passes the checks. Raises Rejected otherwise."""
    old = dict(memory.facts)
    prompt = CONSOLIDATE.format(date=date, max_value=MAX_VALUE, max_keys=MAX_KEYS, memory=dump(old), diary=diary)
    response = await client.messages.create(model=model, max_tokens=16_000, thinking={"type": "disabled"}, messages=[{"role": "user", "content": prompt}])
    reply = "".join(b.text for b in response.content if getattr(b, "type", "") == "text")
    if not reply.strip():
        raise Rejected("the model returned no text")
    new = parse(reply, old)
    change = compare(old, new)
    if memory.path:
        write_atomic(memory.path.with_name(memory.path.name + ".bak"), dump(old) + "\n")
    memory.facts = new
    memory.save()
    return change
