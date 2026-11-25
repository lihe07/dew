"""Screenshots, sized for a model and labelled with tile coordinates."""

from __future__ import annotations

import base64
import io
import time
import zlib
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from . import driver as d
from .game import Game

COLORS = {"npc": (120, 220, 255), "monster": (255, 120, 120), "exit": (255, 230, 90), "object": (170, 255, 150)}
MAX_SIDE = 768  # pixels
save_to: Path | None = None  # where full-size screenshots are kept (the save's screenshots/ folder), if anywhere


kept: list[tuple[str, Image.Image]] = []  # today's full-size frames, written when the day ends


def keep(image: Image.Image) -> None:
    """Keep the full-size frame, for looking at later; it's written at the end of the day."""
    if save_to is not None:
        kept.append((f"{time.strftime('%Y%m%d-%H%M%S')}-{time.monotonic_ns() % 1000:03d}.jpg", image))


def write_kept() -> None:
    try:
        if save_to is not None and kept:
            save_to.mkdir(parents=True, exist_ok=True)
            for name, image in kept:
                image.save(save_to / name, "JPEG", quality=90)
    except OSError:
        pass  # a full disk mustn't stop play
    kept.clear()


async def labels(g: Game) -> list[tuple[int, int, str, str]]:
    rows = await g.read({
        "npc": {"from": "location.npcs", "select": ["x", "y", "name"]}, "monster": {"from": "location.monsters", "select": ["x", "y", "name"]},
        "exit": {"from": "location.exits", "select": ["x", "y", "to"]},
        "object": {"from": "location.object_rows", "where": "kind != 'weeds' and kind != 'twig' and kind != 'stone'", "select": ["x", "y", "name"]},
    })
    found, seen = [], set()
    for kind, entries in rows.items():
        for r in entries:
            text = f"to {r['to']}" if kind == "exit" else r["name"]
            if kind == "exit" and text in seen:
                continue  # wide exits have a warp per tile
            seen.add(text)
            found.append((r["x"], r["y"], text, kind))
    return found


async def capture(g: Game, *, radius: int | None = None, grid: bool = False) -> tuple[str, dict]:
    """Returns a base64 JPEG, and how to convert its pixels back to UI positions."""
    shot: dict[str, Any] = await g.d.one(d.frame())
    if "error" in shot:
        raise RuntimeError(shot["error"])
    image = Image.frombytes("RGB", (shot["width"], shot["height"]), zlib.decompress(base64.b64decode(shot["rgb"])))
    keep(image)
    world = await g.read("state.world")
    view_x, view_y, view_width, _ = shot["viewport"]
    zoom = image.width / view_width
    ox = oy = 0
    if radius and world:
        px, py = await g.here()
        cx, cy, reach = (px * 64 + 32 - view_x) * zoom, (py * 64 + 32 - view_y) * zoom, (radius + 0.5) * 64 * zoom
        box = (max(0, int(cx - reach)), max(0, int(cy - reach)), min(image.width, int(cx + reach)), min(image.height, int(cy + reach)))
        if box[2] - box[0] > 32 and box[3] - box[1] > 32:
            image, ox, oy = image.crop(box), box[0], box[1]
    scale = min(1.0, MAX_SIDE / max(image.size))
    if scale < 1:
        image = image.resize((round(image.width * scale), round(image.height * scale)), Image.Resampling.LANCZOS)

    # overlays go on after scaling, so they stay readable
    draw, font = ImageDraw.Draw(image), ImageFont.load_default(13)
    unit = 64 * zoom * scale

    def pixel(x: float, y: float) -> tuple[float, float]:
        return ((x * 64 - view_x) * zoom - ox) * scale, ((y * 64 - view_y) * zoom - oy) * scale

    def write(x: float, y: float, text: str, color: tuple) -> None:
        draw.text((x, y), text, fill=color, font=font, stroke_width=2, stroke_fill=(0, 0, 0))

    if grid and world:
        x0, y0 = int((ox / zoom + view_x) // 64), int((oy / zoom + view_y) // 64)
        for x in range(x0, x0 + int(image.width / unit) + 2):
            draw.line([(pixel(x, 0)[0], 0), (pixel(x, 0)[0], image.height)], fill=(255, 255, 255))
            write(pixel(x, 0)[0] + 2, 0, str(x), (255, 255, 0))
        for y in range(y0, y0 + int(image.height / unit) + 2):
            draw.line([(0, pixel(0, y)[1]), (image.width, pixel(0, y)[1])], fill=(255, 255, 255))
            write(1, pixel(0, y)[1] + 1, str(y), (255, 255, 0))
    if world and not await g.read("state.menu"):
        for x, y, text, kind in await labels(g):
            px, py = pixel(x, y)
            if 0 <= px < image.width and 0 <= py < image.height:
                draw.rectangle([px, py, px + unit, py + unit], outline=COLORS[kind])
                write(px, py - 15, f"{text[:20]} ({x},{y})", COLORS[kind])
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=82, optimize=True)
    return base64.b64encode(buffer.getvalue()).decode(), {"scale": scale * shot["ui_scale"], "cropped": bool(ox or oy)}
