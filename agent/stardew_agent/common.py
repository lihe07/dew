"""Names shared by every skill module: errors, tile helpers, item labels."""

from __future__ import annotations

import difflib
from collections.abc import Iterable

Tile = tuple[int, int]
FREE = "state.free"


class ActionError(Exception):
    """Something couldn't be done, with a reason worth telling the model."""


def unknown(what: str, name: object, known: dict[str, str]) -> str:
    """An error for a name nothing has, with the nearest exact names (known: each name or title to match -> what to show)."""
    keys = {k.lower(): v for k, v in known.items() if k}
    text = str(name).lower()
    close = difflib.get_close_matches(text, list(keys), n=8, cutoff=0.5) + [k for k in keys if text and text in k]
    picks = list(dict.fromkeys(keys[k] for k in close))[:6]
    return f"{what} {name!r}" + (f"; did you mean: {', '.join(picks)}" if picks else "")


class Interrupted(ActionError):
    """The game needs a decision before continuing."""


def quality(item: dict) -> str:
    return {1: " (silver)", 2: " (gold)", 4: " (iridium)"}.get(item.get("quality", 0), "")


def label(item: dict) -> str:
    text = item["name"] + quality(item)
    if item.get("recipe"):
        text += " (recipe)"
    if item.get("stack", 1) > 1:
        text += f" x{item['stack']}"
    if "water" in item:
        text += f" (water {item['water']}/{item['water_max']})"
    if item.get("attachments"):
        text += f" [{', '.join(item['attachments'])}]"
    return text


def beside(a: Iterable[int], b: Iterable[int]) -> bool:
    (ax, ay), (bx, by) = a, b
    return max(abs(ax - bx), abs(ay - by)) == 1


def crowded(monsters: list[dict], keep: list, me: Iterable[int]) -> list[list[int]]:
    """Tiles around groups of monsters (two or more within 2 tiles), to walk around rather than through."""
    spots = [(m["x"], m["y"]) for m in monsters]
    out: set[Tile] = set()
    for x, y in spots:
        if sum(max(abs(x - a), abs(y - b)) <= 2 for a, b in spots) >= 2:
            out.update((x + dx, y + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1))
    out -= {tuple(t) for t in keep} | {tuple(me)}
    return [list(t) for t in sorted(out)]
