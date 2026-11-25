"""The API: its methods call the right skills with the right arguments, its argument vocabulary, and the wire protocol between the
sandbox and the runner (driven with the real sandbox code under plain CPython, so no WebAssembly is needed)."""

import asyncio
import sys
from pathlib import Path

import pytest
from conftest import FakeGame, run_script
from stardew_agent import api, script, work
from stardew_agent.api import base
from stardew_agent.api.items import Item
from stardew_agent.api.people import Person
from stardew_agent.api.trade import Chest
from stardew_agent.api.where import Area, Tile
from stardew_agent.game import ActionError
from stardew_agent.navigation import Arrival
from stardew_agent.script import Context


class Recorder(FakeGame):
    """Any skill it doesn't have is recorded as (name, args), and returns returns.get(name)."""

    def __init__(self, values=None, returns=None):
        super().__init__({"time.minutes": 540, **(values or {})})
        self.did, self.returns = [], {"step": {"fired": "time"}, "go": Arrival((0, 0), person={"name": "Abigail"}), **(returns or {})}

    def __getattr__(self, name):
        async def skill(*args, **kwargs):
            self.did.append((name, *args, *kwargs.values()))
            return self.returns.get(name)
        return skill


@pytest.fixture
def ctx(monkeypatch):
    for name in ("water", "till", "plant", "pet", "gather", "fish", "fight", "descend"):
        async def skill(g, *args, _name=name, **kwargs):
            g.did.append((f"work.{_name}", *args, *kwargs.values()))
        monkeypatch.setattr(work, name, skill)
    places = [{"NameOrUniqueName": "Beach", "DisplayName": "Beach"}, {"NameOrUniqueName": "SeedShop", "DisplayName": "Pierre's General Store"}]
    return Context(Recorder({"game.locations": places}, returns={"hold": {"name": "Hoe"}, "sleep": {"woke_up": "Tue spring 4"}}))


def call(ctx, name, *args, me=None, **kwargs):
    _, cls, fn = api.lookup(name)
    return asyncio.run(fn(me or cls(), ctx, *args, **kwargs))


AREA = Area(x1=0, y1=0, x2=1, y2=0)
CHEST = Chest({"place": "Farm", "x": 64, "y": 15})


@pytest.mark.parametrize("name, args, kwargs, me, did", [
    ("go", ["Beach"], {}, None, ("go", "Beach")),
    ("go", [(3, 4)], {}, None, ("go", (3, 4))),
    ("go", [Tile(x=3, y=4, place="Town")], {}, None, ("go", ("Town", (3, 4)))),  # a tile elsewhere: there first
    ("go", [Person({"name": "Abigail"})], {}, None, ("go", "Abigail", True)),  # a person is followed
    ("gather", ["rocks"], {}, None, ("work.gather", {"stone", "ore", "gems"}, None, None)),  # a group stands for its kinds
    ("gather", [["weeds", "twigs"]], {"where": Area(x1=0, y1=0, x2=4, y2=4)}, None, ("work.gather", {"weeds", "twigs"}, [0, 0, 4, 4], None)),
    ("gather", [[(1, 2), Tile(x=3, y=4)]], {"tool": "pickaxe"}, None, ("work.gather", [(1, 2), (3, 4)], None, "pickaxe")),
    ("water", [], {}, None, ("work.water", None)),
    ("water", [AREA], {}, None, ("work.water", {(0, 0), (1, 0)})),
    ("till", [[(1, 1), (1, 1)]], {}, None, ("work.till", {(1, 1)})),
    ("plant", ["Parsnip Seeds", AREA], {}, None, ("work.plant", "Parsnip Seeds", {(0, 0), (1, 0)})),
    ("fish", [5], {}, None, ("work.fish", 5, None)),
    ("wait", ["9:30 am"], {}, None, ("step", 6000, {"time": "time.minutes >= 570", "menu": "state.menu", "scene": "state.cutscene", "night": "state.night"})),
    ("wait", [], {"minutes": 30}, None, ("step", 6000, {"time": "time.minutes >= 570", "menu": "state.menu", "scene": "state.cutscene", "night": "state.night"})),
    ("soak", ["4:00 pm"], {}, None, ("soak", 1600)),
    ("talk", ["Robin"], {"gift": Item({"name": "Wood"})}, None, ("talk", "Robin", "Wood")),
    ("answer", [1], {}, None, ("dialogue", 1)),
    ("Mine.descend", [], {}, None, ("work.descend", None)),
    ("Quests.collect", [], {}, None, ("collect_rewards",)),
    ("Item.ship", [3], {}, Item({"name": "Carp (silver)"}), ("ship", "Carp (silver)", 3)),
    ("Item.give", ["Robin"], {}, Item({"name": "Wood"}), ("talk", "Robin", "Wood")),
    ("Chest.put", ["Wood", 5], {}, CHEST, ("container", "put", "Wood", 5)),
    ("Chest.take", [], {}, CHEST, ("open_chest", "Farm", (64, 15))),  # that chest, not the nearest
    ("Screen.interact", [[(5, 6)]], {}, None, ("interact", [(5, 6)], None)),
])
def test_each_method_calls_its_skill(ctx, name, args, kwargs, me, did):
    call(ctx, name, *args, me=me, **kwargs)
    assert did in ctx.game.did, ctx.game.did


def test_names_are_exact_and_a_wrong_one_says_the_nearest(ctx):
    with pytest.raises(ActionError, match=r"no place is called \"Pierre's\"; did you mean: SeedShop \(Pierre's General Store\)"):
        call(ctx, "go", "Pierre's")
    assert str(call(ctx, "Places.get", "SeedShop")) == "SeedShop (Pierre's General Store)"


def test_a_shop_works_only_where_it_is(ctx):
    with pytest.raises(ActionError, match="SeedShop is in SeedShop, and you're in Farm"):
        call(ctx, "Shop.buy", "Parsnip Seeds", me=api.trade.Shop({"place": "SeedShop", "name": "SeedShop", "x": 4, "y": 19}))



def test_sleep_and_a_profession_choice_end_the_day(ctx):
    call(ctx, "sleep")
    assert ctx.finished
    ctx.finished = False
    ctx.game.values["menu.type"] = "LevelUpMenu"
    ctx.game.returns["choose_profession"] = {"woke_up": "Tue spring 4", "notes": []}
    call(ctx, "answer", 1)
    assert ctx.finished and ctx.last_night["chose"] == "option 1"


def test_raw_input_is_limited(ctx):
    ran = []

    async def run(*ops):
        ran.append(ops)

    ctx.game.d.run = run
    call(ctx, "Screen.raw", [{"op": "step", "n": 5000}, {"op": "input", "tile": [1, 2]}])
    assert ran[0] == ({"op": "step", "n": 1200}, {"op": "input", "cursor": {"tile": [1, 2]}}) and ran[1][0]["buttons"] == []
    with pytest.raises(ActionError, match="only allows"):
        call(ctx, "Screen.raw", [{"op": "cheat"}])


@pytest.mark.parametrize("name, args, error, match", [
    ("till", [[0, 0, 1]], TypeError, r"each tile must be"),
    ("till", [Tile(x=1, y=1, place="Town")], ActionError, "those tiles are in Town, and you're in Farm"),
    ("water", [Area(x1=0, y1=0, x2=100, y2=100)], ActionError, "too large"),
    ("gather", ["dragons"], TypeError, "gather takes kinds from"),
    ("fish", [50], ActionError, "casts must be from 1 to 20"),
    ("go", [5], TypeError, "where must be"),
    ("wait", [], TypeError, "wait takes until"),
    ("wait", ["noon"], ActionError, "not a time"),
    ("answer", [1.5], TypeError, "answer takes"),
    ("talk", [3], TypeError, "who must be text"),
])
def test_bad_arguments_are_explained(ctx, name, args, error, match):
    with pytest.raises(error, match=match):
        call(ctx, name, *args)
    assert not [d for d in ctx.game.did if d[0] != "ready"]


@pytest.mark.parametrize("text, clock", [("9:00 am", 900), ("9am", 900), ("11pm", 2300), ("21:30", 2130), ("2300", 2300), ("1am", 2500), (1350, 1350)])
def test_until_reads_clock_times(text, clock):
    assert base.until(text) == clock


def test_safety_lets_cures_run():
    assert api.recovers("Item.eat", {"of": "Salad"}) and api.recovers("sleep", {}) and api.recovers("go", {"target": "Farm"})
    assert not api.recovers("go", {"target": "Mine"}) and not api.recovers("go", {"target": [1, 2]}) and not api.recovers("fish", {})


def test_records_come_back_from_the_wire_as_their_class():
    made = base.record({"$": "Chest", "ref": {"place": "Farm", "x": 1, "y": 2}, "f": {"ref": "not a keyword", "x": 1}})
    assert isinstance(made, Chest) and made.ref["place"] == "Farm" and made.f["ref"] == "not a keyword"
    assert base.record({"$": "Farmer", "ref": {}, "f": {}}) == {"$": "Farmer", "ref": {}, "f": {}}  # a live object isn't a record
    assert base.record({"$": "Nope", "ref": {}, "f": {}}) == {"$": "Nope", "ref": {}, "f": {}}


# --- the wire protocol, end to end ---------------------------------------------------------------------------


@pytest.fixture
def cpython(monkeypatch):
    """Scripts run in the real sandbox code under this Python, not WebAssembly."""
    child = Path(script.__file__).parent / "sandbox_child.py"
    monkeypatch.setattr(script, "_COMMAND", [sys.executable, "-I", str(child)])


def test_records_cross_both_ways_and_their_methods_act_on_them(cpython, make_runner, acts):
    values = {"location.object_rows": [{"x": 64, "y": 15, "name": "Chest", "Items": [{"name": "Wood", "stack": 9}, None]}],
              "player.friends": [{"name": "Abigail", "hearts": 2, "location": "Town", "x": 5, "y": 6}]}
    r, _ = make_runner(values=values)
    source = ("c = here.chests[0]\nprint(c, c.items, c.tile, c.tile == Tile(64, 15, 'Farm'))\nc.take(c.items[0], 2)\n"
              "a = people['Abigail']\nprint(a.hearts, a.tile)\ngo(a)\ngo([a.tile, (1, 2)])")
    why = asyncio.run(run_script(r, source, timeout=8))
    assert why == ["the script ended"]
    assert "printed: Chest(Chest at (64,15)) [Item(Wood x9)] Tile(64, 15, 'Farm') True" in r.trace and "printed: 2 Tile(5, 6, 'Town')" in r.trace
    assert acts.log == [("Chest.take", {"item": "Wood: x9", "count": 2}), ("go", {"target": "Abigail"}), ("go", {"target": ["(5,6)", [1, 2]]})]


def test_tiles_and_areas_are_values_in_the_script(cpython, make_runner, acts):
    r, _ = make_runner()
    source = ("a = Area(0, 0, 2, 1, 'Farm')\nprint(len(a), a.center, (1, 1) in a, Tile(3, 0) in a, list(a)[:2])\n"
              "x, y = Tile(3, 4)\nprint(x + y, {Tile(1, 2), Tile(1, 2)}, Tile(1, 2).tile)")
    asyncio.run(run_script(r, source, timeout=8))
    assert "printed: 6 Tile(1, 0, 'Farm') True False [Tile(0, 0, 'Farm'), Tile(1, 0, 'Farm')]" in r.trace and "printed: 7 {Tile(1, 2)} Tile(1, 2)" in r.trace


def test_help_describes_functions_objects_and_methods(cpython, make_runner, acts):
    r, _ = make_runner()
    asyncio.run(run_script(r, "help(go)\nhelp(backpack)\nhelp(screen.click)", timeout=8))
    said = "\n".join(r.trace)
    assert "printed: go(target): Walk to a place" in said and "Backpack: What you carry" in said and "fields: items, food" in said
    assert "screen.click(target, right=False): Click a menu component" in said
