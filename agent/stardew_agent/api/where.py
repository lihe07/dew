"""Where things are: tiles, areas, and things found, which go wherever a tile goes."""

from __future__ import annotations

from typing import Any

from .base import Api


class Tile(Api):
    """A tile: Tile(x, y, place=None), where None is the current location. Wherever a Tile goes, (x, y) does too."""

    value = True
    fields = {"x": "", "y": "", "place": "a place's name, or None for here", "tile": "itself", "location": "the Place (here when place is None)"}


class Area(Api):
    """Area(x1, y1, x2, y2, place=None): every tile between two corners, both included. `tile in area`, and iterating gives its Tiles."""

    value = True
    fields = {"x1": "", "y1": "", "x2": "", "y2": "", "place": "", "tiles": "a list of Tiles", "center": "a Tile", "tile": "its center"}


def tile(x: int, y: int, place: str | None) -> Tile:
    return Tile(x=x, y=y, place=place)


class Thing(Api):
    """Something found (here.find, here.exits, mine's fields): its kind, name, and where it is; usable wherever a tile goes."""

    fields = {"kind": "", "name": "", "x": "", "y": "", "tile": "a Tile", "...": "more, by kind (hp for monsters, dist for mine finds)"}

    @classmethod
    def at(cls, place: str, kind: str, name: Any, x: int, y: int, **more: Any) -> Thing:
        return cls(kind=kind, name=name, x=x, y=y, tile=tile(x, y, place), **more)
