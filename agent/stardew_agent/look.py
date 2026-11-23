"""What the planner sees of the game: the status line, the journal's news, menus, and the views the SDK's facts read (map, find, people, ...)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from . import work
from .game import ActionError, Game, label


@dataclass
class Image:
    """A screenshot for the planner, as base64 JPEG."""

    data: str


def minutes(clock: int) -> int:
    return clock // 100 * 60 + clock % 100


def until_2am(clock: int) -> int:
    return 26 * 60 - minutes(clock)


def render(data: Any, indent: int = 0) -> str:
    """Compact text for results: empty fields are dropped, flat dicts and number lists stay on one line."""
    pad = "  " * indent

    def scalar(v: Any) -> str:
        return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)

    if isinstance(data, dict):
        lines = []
        for k, v in data.items():
            if v is None or v == [] or v == {}:
                continue
            if isinstance(v, str) and "\n" in v:
                lines.append(f"{pad}{k}:\n{v}")
            elif isinstance(v, dict) and all(not isinstance(x, (dict, list)) for x in v.values()):
                lines.append(f"{pad}{k}: " + ", ".join(f"{a} {scalar(b)}" for a, b in v.items() if b is not None))
            elif isinstance(v, list) and all(isinstance(x, (int, float)) or isinstance(x, (list, tuple)) and all(isinstance(y, (int, float)) for y in x) for x in v):
                lines.append(f"{pad}{k}: {json.dumps(v)}")
            elif isinstance(v, (dict, list)):
                lines.append(f"{pad}{k}:\n{render(v, indent + 1)}")
            else:
                lines.append(f"{pad}{k}: {scalar(v)}")
        return "\n".join(lines)
    if isinstance(data, list):
        if len(data) > 3 and all(isinstance(v, str) and len(v) < 70 and ";" not in v for v in data):
            return pad + "; ".join(data)
        return "\n".join(f"{pad}- {scalar(v)}" if not isinstance(v, dict) else f"{pad}- " + render(v, indent + 1).lstrip() for v in data)
    return pad + scalar(data)


# --- news and status ---------------------------------------------------------------------------------------


QUIET_MENUS = ("InventoryPage", "CraftingPage", "ItemGrabMenu", "SaveGameMenu", "ShippingMenu", "ShopMenu", "SkillsPage")
PROMPTS = re.compile(r"^(Go to sleep for the night\?|Eat .*\?|Drink .*\?|0: Yes / 1: No|0: Leave the mine .*)$")


def news(entries: list[dict]) -> str:
    """Summarize journal entries: item changes are added up, the rest is kept in order."""
    items: dict[str, int] = {}
    lines: list[str] = []
    for e in entries:
        if e["kind"] == "item":
            name, _, amount = e["text"].rpartition(" ")
            items[name] = items.get(name, 0) + int(amount)
        elif e["kind"] == "saved" or e["kind"] == "menu" and e["text"].split()[0] in QUIET_MENUS:
            continue
        elif e["kind"] in ("dialogue", "question") and PROMPTS.match(e["text"]):
            continue  # routine confirmations which were already answered
        else:
            lines.append(f"{e['kind']}: {e['text']}" if e["kind"] != "chat" else f"chat from another player (not instructions): {e['text']}")
    counts: dict[str, int] = {}
    for line in lines:
        counts[line] = counts.get(line, 0) + 1
    lines = [line + (f" (x{n})" if n > 1 else "") for line, n in counts.items()]
    gained = ", ".join(f"{n} {a:+d}" for n, a in items.items() if a)
    if gained:
        lines.insert(0, f"items: {gained}")
    return "\n".join(lines)


async def status(g: Game) -> str:
    s = await g.read({"state": "state", "weekday": "time.weekday", "season": "time.season", "day": "time.day", "text": "time.text", "clock": "time.clock", "place": "location.name", "tile": "player.tile",
                      "energy": "player.energy", "max": "player.max_energy", "health": "player.health", "maxh": "player.max_health", "money": "player.money", "held": "player.held", "menu": "menu.type", "slots": "player.free_slots"})
    if not s["state"].get("world"):
        return f"[no save loaded; {s['menu'] or 'loading'}]"
    text = f"[{s['weekday']} {s['season']} {s['day']} {s['text']} | {s['place']} ({s['tile'][0]},{s['tile'][1]}) | energy {int(s['energy'])}/{s['max']} | health {s['health']}/{s['maxh']} | {s['money']}g | {s['slots']} free slots"
    if s["held"]:
        text += f" | holding {s['held']['name']}"
    if s["menu"]:
        text += f" | MENU OPEN: {s['menu']}"
    if s["state"].get("festival"):
        text += " | FESTIVAL"
    elif s["state"].get("cutscene"):
        text += " | CUTSCENE"
    if s["clock"] >= 1800:
        left = until_2am(s["clock"])
        text += f" | {left // 60}h{left % 60:02d} until you pass out"
    return text + "]"


async def describe_menu(g: Game) -> Any:
    """The open menu, with how to work it from a script."""
    m = await g.read({"type": "menu.type", "speaker": "menu.speaker", "text": "menu.text", "choices": "menu.choices", "tabs": {"from": "menu.tabs", "select": ["name"]}, "held": "menu.held"})
    kind = m["type"]
    if not kind:
        return "no menu is open"
    out: dict[str, Any] = {"type": kind}
    if kind == "DialogueBox":
        return {**out, "speaker": m["speaker"], "text": m["text"], "choices": [f"{i}: {c}" for i, c in enumerate(m["choices"] or [])], "how": "answer(N)" if m["choices"] else "screen('close')"}
    if kind == "LetterViewerMenu":
        return {**out, "text": m["text"], "how": "screen('close') also takes attached items"}
    if kind == "ShopMenu":
        rows = []
        for s in await g.shop_stock():
            text = f"{s['i']}: {s['name']}{' (recipe)' if s['recipe'] else ''} {s.get('Price')}g"
            if s.get("TradeItem"):
                text += f" + {s.get('TradeItemCount') or 5}x {s['TradeItem']}"
            if (s.get("Stock") or 0) < 2**31 - 1:
                text += f" (stock {s['Stock']})"
            rows.append(text)
        return {**out, "money": await g.read("player.money"), "for_sale": rows, "how": "buy(item, count) or sell(item, count), then screen('close')"}
    if kind == "ItemGrabMenu":
        grids = await g.read("menu.inventories")
        return {**out, "container": [f"{s['slot']}: {label(s['item'])}" for grid in grids if not grid["player"] for s in grid["slots"] if s.get("item")], "how": "take(item) or put(item), then screen('close')"}
    if kind == "LevelUpMenu":
        return {**out, "how": "answer(0 or 1) if it offers professions"}
    rows = []
    for c in await g.read("menu.components"):
        text = " | ".join(dict.fromkeys(t for t in (c.get("name"), c.get("label"), c.get("hover"), c.get("item")) if t and not (t.isdigit() and kind != "MineElevatorMenu")))
        if not text and not c["field"].endswith(("CC", "Slots", "inventory")):
            text = c["field"]  # unlabelled buttons are still named in code, e.g. doneNamingButton
        if text:
            rows.append(f"{c['i']}: {text}")
    return {**out, "text": m["text"], "tabs": [t["name"] for t in m["tabs"]], "cursor_holds": label(m["held"]) if m["held"] else None, "components": rows[:90],
            "how": "screen('click', target=<number or name>)"}


# --- the views ---------------------------------------------------------------------------------------------


async def state(g: Game) -> dict:
    """Date, weather, stats, farm to-dos, mail, and any open menu or event."""
    s = await g.read({"time": "time", "state": "state", "luck": "player.DailyLuck", "skills": "player.skills", "slots": "player.free_slots", "buffs": "player.buff_names", "mail": "game.mailbox.count",
                      "mail_at": "player.mailbox_tile", "quests": {"from": "player.quests", "select": ["title", "complete"]}, "level": "location.mine_level",
                      "farm": {"crops": {"from": "locations['Farm'].terrain", "where": "crop != null", "select": ["thirsty", "ready", "dead"]}}, "festival": "event.festival_name"})
    t, luck = s["time"], s["luck"] or 0
    crops = s["farm"]["crops"]
    return {
        "date": f"{t['weekday']} {t['season']} {t['day']}, year {t['year']} ({28 - t['day']} days left in the season)", "time": t["text"], "weather": f"{t.get('weather')}, tomorrow {t.get('tomorrow')}",
        "luck": "very good" if luck > 0.07 else "good" if luck > 0.02 else "very bad" if luck < -0.07 else "bad" if luck < -0.02 else "neutral",
        "skills": s["skills"], "free_inventory_slots": s["slots"], "buffs": s["buffs"],
        "farm_crops": f"{len(crops)} planted, {sum(1 for c in crops if c['thirsty'])} thirsty, {sum(1 for c in crops if c['ready'])} ready, {sum(1 for c in crops if c['dead'])} dead",
        "mail": f"{s['mail']} letter(s) in the mailbox at {s['mail_at']} on the Farm" if s["mail"] else None,
        "festival_today": True if t.get("festival") else None, "at_festival": s["festival"], "birthday_today": t.get("birthday"), "mine_level": s["level"],
        "quests": [q["title"] + (" (complete: collect the reward in the journal)" if q["complete"] else "") for q in s["quests"]],
        "menu": await describe_menu(g) if s["state"].get("menu") else None,
    }


async def items(g: Game) -> dict:
    rows = []
    for i in await g.inventory():
        text = f"{i['slot']}: {label(i)}"
        if i.get("price"):
            text += f" | {i['price']}g each"
        if (i.get("energy") or 0) > 0:
            text += f" | +{i['energy']} energy"
        rows.append(text)
    return {"capacity": await g.read("player.maxItems"), "items": rows}


async def people(g: Game, name: str | None = None) -> list[str]:
    rows = []
    for p in await g.read("player.friends"):
        if name and name.lower() not in p["name"].lower():
            continue
        text = p["name"] + (f": {p['hearts']} hearts, {'talked' if p['talked'] else 'not talked'} today, {p['gifts']}/2 gifts this week" if p.get("hearts") is not None else ": not met")
        rows.append(text + (f" | at {p['location']} ({p['x']},{p['y']})" if p.get("location") else "") + (f" | birthday {p['birthday']}" if p.get("birthday") else ""))
    return rows


async def recipes(g: Game) -> list[str]:
    return [f"{r['name']}: {r['needs']}" + (" [CAN CRAFT NOW]" if r["craftable"] else " [missing ingredients]") for r in await g.read("player.recipes")]


async def exits(g: Game) -> list[str]:
    seen: dict[str, str] = {}
    for e in await g.read("location.exits"):
        seen.setdefault(e["to"], f"{e['to']} via {'door' if e['door'] else 'path'} ({e['x']},{e['y']})")
    return list(seen.values())


async def item(g: Game, name: str) -> dict:
    found = await g.read({"$item": name})
    if not found:
        raise ActionError(f"no item called '{name}'")
    crop = found.pop("crop", None)
    out = {k: found.get(k) for k in ("name", "id", "description", "category_name", "energy")}
    out["you_sell_for"] = found.get("price")
    if crop:
        produce = await g.read({"$item": f"(O){crop['HarvestItemId']}"}) or {}
        out["crop"] = {"seasons": " ".join(crop["Seasons"]), "days": sum(crop["DaysInPhase"]), "regrows": f"every {crop['RegrowDays']} days" if crop["RegrowDays"] > 0 else "no, one harvest per seed",
                       "gives": produce.get("name"), "sells_for": produce.get("price")}
    return out


async def tile(g: Game, at: list[int]) -> dict:
    x, y = at
    where = f"x == {x} and y == {y}"
    rows = await g.read({"object": {"from": "location.object_rows", "where": where}, "terrain": {"from": "location.terrain", "where": where}, "npc": {"from": "location.npcs", "where": where, "select": ["name"]},
                         "exit": {"from": "location.exits", "where": where, "select": ["to"]}, "action": {"from": "location.actions", "where": where, "select": ["action"]},
                         "grid": {"$grid": {"rect": [x, y, 1, 1], "layers": ["pass", "water", "dig"]}}})
    grid = rows.pop("grid")
    if not grid["pass"]:
        raise ActionError("that's outside the map")
    return {**{k: v[0] for k, v in rows.items() if v}, "walkable": grid["pass"][0] != "#", "water": grid["water"][0] == "1" or None, "diggable": grid["dig"][0] == "1" or None}


SYMBOLS = {"@": "you", ".": "walkable", "#": "blocked", "~": "water", "T": "tree", "t": "sapling", "o": "stone", "w": "weeds", "/": "twig", '"': "grass", "S": "stump/log/boulder", "h": "tilled", "c": "crop, dry",
           "v": "crop, watered", "R": "crop ready", "x": "dead crop", "f": "forage", "O": "object/machine", "$": "chest", "N": "person", "M": "monster", "A": "animal", "D": "door", ">": "exit", "!": "interactable",
           "F": "furniture", "L": "ladder", "B": "bush", "%": "building", "*": "artifact spot"}


async def tile_map(g: Game, radius: int = 14, center: list[int] | None = None) -> dict:
    """Tile map of the surroundings as text, with rulers, a legend, and notable things."""
    me, size = await g.read(["player.tile", "location.size"])
    cx, cy = center or me
    x0, y0 = max(0, cx - radius), max(0, cy - min(radius, 28))
    rect = [x0, y0, min(size[0], cx + radius + 1) - x0, min(size[1], cy + min(radius, 28) + 1) - y0]
    near = f"x >= {rect[0]} and x < {rect[0] + rect[2]} and y >= {rect[1]} and y < {rect[1] + rect[3]}"
    r = await g.read({
        "grid": {"$grid": {"rect": rect, "layers": ["pass", "water"]}}, "place": "location.name",
        "terrain": {"from": "location.terrain", "where": near, "select": ["x", "y", "kind", "crop", "dead", "ready", "watered"]},
        "objects": {"from": "location.object_rows", "where": near, "select": ["x", "y", "kind", "name", "ready", "holds", "minutes"]},
        "clumps": "location.clumps", "bushes": {"from": "location.bushes", "where": near, "select": ["x", "y"]}, "buildings": "location.building_rows", "furniture": "location.furniture_rows",
        "exits": {"from": "location.exits", "where": f"x >= {rect[0] - 1} and x <= {rect[0] + rect[2]} and y >= {rect[1] - 1} and y <= {rect[1] + rect[3]}"}, "actions": {"from": "location.actions", "where": near},
        "ladders": "location.ladders", "npcs": {"from": "location.npcs", "where": near, "select": ["x", "y", "name"]}, "monsters": {"from": "location.monsters", "where": near, "select": ["x", "y", "name", "hp"]},
        "animals": {"from": "location.animal_rows", "where": near, "select": ["x", "y", "name", "kind", "petted"]},
    })
    cells = [["~" if w == "1" and p == "#" else "D" if p == "d" else p for p, w in zip(a, b)] for a, b in zip(r["grid"]["pass"], r["grid"]["water"])]
    notable: list[str] = []

    def put(x: int, y: int, symbol: str, text: str | None = None) -> None:
        if 0 <= x - rect[0] < rect[2] and 0 <= y - rect[1] < rect[3]:
            cells[y - rect[1]][x - rect[0]] = symbol
        if text and text not in notable:
            notable.append(text)

    for b in r["buildings"]:
        for x in range(b["x"], b["x"] + b["w"]):
            for y in range(b["y"], b["y"] + b["h"]):
                if 0 <= x - rect[0] < rect[2] and 0 <= y - rect[1] < rect[3] and cells[y - rect[1]][x - rect[0]] == "#":
                    put(x, y, "%", f"{b['name']} ({b['x']},{b['y']})-({b['x'] + b['w'] - 1},{b['y'] + b['h'] - 1})")
    for f in r["furniture"]:
        if not f["rug"]:
            for x in range(f["x"], f["x"] + f["w"]):
                for y in range(f["y"], f["y"] + f["h"]):
                    put(x, y, "F")
    for t in r["terrain"]:
        symbol = {"tree": "T", "fruit_tree": "T", "sapling": "t", "stump": "S", "grass": '"'}.get(t["kind"])
        if t["kind"] == "dirt":
            symbol = "h" if not t["crop"] else "x" if t["dead"] else "R" if t["ready"] else "v" if t["watered"] else "c"
        if symbol:
            put(t["x"], t["y"], symbol)
    for c in r["clumps"]:
        for x in range(c["x"], c["x"] + c["w"]):
            for y in range(c["y"], c["y"] + c["h"]):
                put(x, y, "S")
    for b in r["bushes"]:
        put(b["x"], b["y"], "B")
    for a in r["actions"]:
        put(a["x"], a["y"], "!", f"\"{a['action']}\" ({a['x']},{a['y']})")
    for o in r["objects"]:
        symbol = {"weeds": "w", "twig": "/", "stone": "o", "artifact_spot": "*", "chest": "$", "gift": "$", "forage": "f"}.get(o["kind"], "O")
        extra = f" READY: {o['holds']}" if o["ready"] else f" making {o['holds']}" if o["holds"] else ""
        put(o["x"], o["y"], symbol, None if symbol in "w/o*" else f"{o['name']}{extra} ({o['x']},{o['y']})")
    for e in r["exits"]:
        put(e["x"], e["y"], "D" if e["door"] else ">", f"to {e['to']} ({e['x']},{e['y']})" if not any(n.startswith(f"to {e['to']} ") for n in notable) else None)
    for ladder in r["ladders"]:
        put(ladder["x"], ladder["y"], "L", f"{ladder['kind']} ({ladder['x']},{ladder['y']})")
    for key, symbol in (("animals", "A"), ("npcs", "N"), ("monsters", "M")):
        for n in r[key]:
            put(n["x"], n["y"], symbol, f"{n['name']} ({n['x']},{n['y']})" + (f" hp {n['hp']}" if "hp" in n else ""))
    put(me[0], me[1], "@")
    xs = range(rect[0], rect[0] + rect[2])
    ruler = "     " + "".join(str(x // 10 % 10) if x % 10 == 0 else " " for x in xs) + "\n     " + "".join(str(x % 10) for x in xs)
    body = "\n".join(f"{rect[1] + i:4} " + "".join(row) for i, row in enumerate(cells))
    used = {c for row in cells for c in row}
    return {"location": r["place"], "map": ruler + "\n" + body, "legend": ", ".join(f"{k} {v}" for k, v in SYMBOLS.items() if k in used), "notable": notable[:45]}


FIND = {
    "crops": ("terrain", "crop != null", "crop"), "crops_ready": ("terrain", "ready and crop != null", "crop"), "crops_thirsty": ("terrain", "thirsty", "crop"), "tilled_empty": ("terrain", "kind == 'dirt' and crop == null", "kind"),
    "trees": ("terrain", "kind == 'tree' or kind == 'fruit_tree'", "kind"), "forage": ("object_rows", "kind == 'forage'", "name"), "artifact_spots": ("object_rows", "kind == 'artifact_spot'", "name"),
    "stones": ("object_rows", "kind == 'stone'", "name"), "weeds": ("object_rows", "kind == 'weeds'", "kind"), "twigs": ("object_rows", "kind == 'twig'", "kind"), "chests": ("object_rows", "kind == 'chest' or kind == 'gift'", "name"),
    "machines": ("object_rows", "kind == 'machine'", "name"), "objects": ("object_rows", "kind != 'weeds' and kind != 'twig' and kind != 'stone'", "name"), "big_debris": ("clumps", "true", "kind"),
    "berry_bushes": ("bushes", "berries", "berries"), "people": ("npcs", "true", "name"), "monsters": ("monsters", "true", "name"), "animals": ("animal_rows", "true", "name"), "buildings": ("building_rows", "true", "name"),
    "furniture": ("furniture_rows", "true", "name"), "exits": ("exits", "true", "to"), "interactables": ("actions", "true", "action"), "ladders": ("ladders", "true", "kind"),
}
FINDABLE = [*FIND, "tillable", "water"]


async def find(g: Game, kind: str, name: str | None = None, limit: int = 25) -> dict:
    """Things in the current location, nearest first, summed up as text."""
    rows = await find_rows(g, kind, name)
    groups: dict[str, list[str]] = {}
    for r in rows[:limit]:
        groups.setdefault(r["text"], []).append(f"({r['x']},{r['y']})")
    return {"total": len(rows), "nearest": [f"{k}: {' '.join(v)}" for k, v in groups.items()]}


async def find_rows(g: Game, kind: str, name: str | None = None) -> list[dict]:
    """Things in the current location, nearest first: x, y, and what each is as text."""
    me = await g.here()
    if kind in ("water", "tillable"):
        spots = await work.layer(g, "refill" if kind == "water" else "dig")
        if kind == "tillable":
            busy = set(work.xy(await g.read({"from": "location.terrain", "select": ["x", "y"], "limit": 9000}))) | set(work.xy(await g.read({"from": "location.object_rows", "select": ["x", "y"], "limit": 9000})))
            spots = [s for s in spots if s not in busy]
        rows = [{"x": x, "y": y, "text": kind} for x, y in spots]
        rows.sort(key=lambda r: abs(r["x"] - me[0]) + abs(r["y"] - me[1]))
    else:
        table, where, text = FIND[kind]
        rows = await g.read({"from": f"location.{table}", "where": where, "near": list(me), "limit": 9000})
        for r in rows:
            r["text"] = str(r.get(text, kind)) + (" (dead)" if r.get("dead") else " (ready)" if r.get("ready") else f" ({r['days_left']} days left)" if r.get("days_left") and kind.startswith("crops") else "")
    if name:
        rows = [r for r in rows if name.lower() in r["text"].lower()]
    return rows
