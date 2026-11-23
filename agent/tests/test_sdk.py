"""The SDK's actions call the right skills with the right arguments, against a game which records what it's asked to do."""

import asyncio

import pytest
from conftest import FakeGame
from stardew_agent import look, sdk, work
from stardew_agent.game import ActionError
from stardew_agent.script import Context


class Recorder(FakeGame):
    """Any skill it doesn't have is recorded as (name, args), and returns returns.get(name)."""

    def __init__(self, values=None, returns=None):
        super().__init__({"time.minutes": 540, "time.text": "9:00 am", **(values or {})})
        self.did, self.returns = [], {"step": {"fired": "time"}, **(returns or {})}

    def __getattr__(self, name):
        async def skill(*args, **kwargs):
            self.did.append((name, *args, *kwargs.values()))
            return self.returns.get(name)
        return skill


@pytest.fixture
def ctx(monkeypatch):
    for name in ("harvest", "water", "collect", "pet", "clear", "fish", "fight", "mine_down"):
        async def skill(g, *args, _name=name, **kwargs):
            g.did.append((f"work.{_name}", *args, *kwargs.values()))
        monkeypatch.setattr(work, name, skill)
    return Context(Recorder(returns={"hold": {"name": "Hoe"}, "sleep": {"woke_up": "Tue spring 4"}}))


AREA = [(0, 0), (1, 0)]


@pytest.mark.parametrize("name, args, kwargs, did", [
    ("go", ["Beach"], {}, ("travel", "Beach")),
    ("go", [(3, 4)], {"beside": True}, ("walk_to", [3, 4], True)),
    ("harvest", [], {}, ("work.harvest",)),
    ("water", [], {}, ("work.water",)),
    ("water", [], {"area": [0, 0, 1, 0]}, ("use_tool", "watering can", AREA)),
    ("till", [[0, 0, 1, 0]], {}, ("use_tool", "hoe", AREA)),
    ("till", [], {"tiles": [[1, 1], [1, 1]]}, ("use_tool", "hoe", [(1, 1)])),
    ("plant", ["Parsnip Seeds", [0, 0, 1, 0]], {}, ("place", "Parsnip Seeds", AREA)),
    ("place", ["Chest", [[5, 5]]], {}, ("place", "Chest", [(5, 5)])),
    ("clear", [[0, 0, 4, 4]], {}, ("work.clear", [0, 0, 4, 4], ["weeds", "twigs", "stones"])),
    ("chop", [[0, 0, 1, 0]], {}, ("use_tool", "axe", AREA)),
    ("break_rocks", [[0, 0, 1, 0]], {}, ("use_tool", "pickaxe", AREA)),
    ("forage", [], {}, ("work.collect", "forage")),
    ("pet", [], {}, ("work.pet",)),
    ("machines", [], {}, ("work.collect", "machines")),
    ("fish", [5], {}, ("work.fish", 5, None, None)),
    ("mine", [2], {}, ("work.mine_down", 2, 30, None, None)),
    ("fight", [], {}, ("work.fight", 12, None)),
    ("rewards", [], {}, ("collect_rewards",)),
    ("buy", ["Parsnip Seeds", 15], {}, ("buy", "Parsnip Seeds", 15)),
    ("sell", ["Carp"], {}, ("sell", "Carp", None)),
    ("eat", ["Salad"], {}, ("eat", "Salad")),
    ("ship", ["Carp", 3], {}, ("ship", "Carp", 3)),
    ("use", ["Warp Totem: Farm"], {}, ("use", "Warp Totem: Farm")),
    ("craft", ["Chest", 2], {}, ("craft", "Chest", 2)),
    ("trash", ["Joja Cola"], {}, ("trash", "Joja Cola")),
    ("hold", ["Hoe"], {}, ("hold", "Hoe")),
    ("put", ["Wood"], {}, ("container", "put", "Wood")),
    ("take", [], {}, ("container", "take", "all")),
    ("talk", ["Robin"], {"gift": "Wood"}, ("talk", "Robin", "Wood")),
    ("answer", [1], {}, ("dialogue", 1)),
    ("skip_cutscene", [], {}, ("skip_event",)),
    ("wait_until", ["9:30 am"], {}, ("step", 6000, {"time": "time.minutes >= 570", "menu": "state.menu", "scene": "state.cutscene", "night": "state.night"})),
    ("wait_until", [30], {}, ("step", 6000, {"time": "time.minutes >= 570", "menu": "state.menu", "scene": "state.cutscene", "night": "state.night"})),
    ("soak", ["4:00 pm"], {}, ("soak", 1600)),
    ("sleep", [], {"early": True}, ("sleep",)),
    ("screen", ["interact"], {"tiles": [[5, 6]]}, ("interact", [(5, 6)], None)),
    ("screen", ["close"], {}, ("close_menu", False)),
])
def test_each_action_calls_its_skill(ctx, name, args, kwargs, did):
    asyncio.run(sdk.ACTIONS[name](ctx, *args, **kwargs))
    assert did in ctx.game.did, ctx.game.did


def test_results_are_what_the_script_gets(ctx):
    assert asyncio.run(sdk.hold(ctx, "Hoe")) == {"holding": "Hoe"}
    assert asyncio.run(sdk.skip_cutscene(ctx)) == {"skipped_script": None}


def test_sleep_ends_the_day_and_keeps_the_night(ctx):
    assert not ctx.finished
    asyncio.run(sdk.sleep(ctx, early=True))
    assert ctx.finished and ctx.last_night == {"woke_up": "Tue spring 4"}


def test_sleep_refuses_before_6pm_unless_early(ctx):
    with pytest.raises(ActionError, match="there's most of a day left"):
        asyncio.run(sdk.sleep(ctx))
    assert not ctx.finished


def test_a_profession_choice_is_kept_for_the_morning(ctx):
    ctx.game.values["menu.type"] = "LevelUpMenu"
    ctx.game.returns["choose_profession"] = {"woke_up": "Tue spring 4", "notes": []}
    asyncio.run(sdk.answer(ctx, 1))
    assert ("choose_profession", 1) in ctx.game.did and ctx.finished and ctx.last_night["chose"] == "option 1"


def test_raw_input_is_limited(ctx):
    ran = []

    async def run(*ops):
        ran.append(ops)

    ctx.game.d.run = run
    asyncio.run(sdk.screen(ctx, "raw", ops=[{"op": "step", "n": 5000}, {"op": "input", "tile": [1, 2]}]))
    assert ran[0] == ({"op": "step", "n": 1200}, {"op": "input", "cursor": {"tile": [1, 2]}}) and ran[1][0]["buttons"] == []
    with pytest.raises(ActionError, match="only allows"):
        asyncio.run(sdk.screen(ctx, "raw", ops=[{"op": "cheat"}]))


@pytest.mark.parametrize("name, args, kwargs, error, match", [
    ("till", [[0, 0]], {}, TypeError, r"area must be \[x1, y1, x2, y2\]"),
    ("till", [], {}, TypeError, "give an area"),
    ("plant", ["Seeds"], {"tiles": [[1]]}, TypeError, r"each tile must be \[x, y\]"),
    ("water", [], {"area": [0, 0, 100, 100]}, ActionError, "too large"),
    ("clear", [[0, 0, 1, 1], ["rocks"]], {}, TypeError, "kinds can be"),
    ("fish", [50], {}, ActionError, "casts must be from 1 to 20"),
    ("fish", ["many"], {}, TypeError, "casts must be a whole number"),
    ("eat", [3], {}, TypeError, "item must be text"),
    ("go", [5], {}, TypeError, "target must be text"),
    ("wait_until", [], {}, TypeError, "wait_until needs a time"),
    ("wait_until", ["noon"], {}, ActionError, "not a time"),
    ("wait_until", [], {"minutes": 5}, ActionError, "minutes must be from 10 to 600"),
    ("answer", [1.5], {}, TypeError, "answer takes"),
    ("screen", ["dance"], {}, TypeError, "screen's action is one of"),
    ("screen", ["interact"], {}, TypeError, "needs tiles"),
])
def test_bad_arguments_are_explained(ctx, name, args, kwargs, error, match):
    with pytest.raises(error, match=match):
        asyncio.run(sdk.ACTIONS[name](ctx, *args, **kwargs))
    assert not [d for d in ctx.game.did if d[0] != "ready"]


@pytest.mark.parametrize("text, clock", [("9:00 am", 900), ("11pm", 2300), ("23:00", 2300), ("2300", 2300), ("1am", 2500), ("12:30 pm", 1230)])
def test_clock_times(text, clock):
    assert sdk.clock(text) == clock


def test_safety_lets_cures_run():
    assert sdk.recovers("eat", {"item": "Salad"}) and sdk.recovers("sleep", {}) and sdk.recovers("go", {"target": "Farm"})
    assert not sdk.recovers("go", {"target": "Mine"}) and not sdk.recovers("go", {"target": [1, 2]}) and not sdk.recovers("fish", {})


def test_facts_read_the_game():
    ctx = Context(FakeGame({"time.clock": 2330, "player.energy": 12.7, "location.npcs": [{"name": "Gus"}], "player.money": 75}))
    assert [asyncio.run(sdk.FACTS[name](ctx)) for name in ("minutes_until_2am", "energy", "people_here", "money")] == [150, 12, ["Gus"], 75]


def test_views_call_looks_views(ctx, monkeypatch):
    seen = []
    for name in ("tile_map", "find_rows", "people", "item", "tile"):
        async def view(g, *args, _name=name):
            seen.append((_name, *args))
            return {"map": "@"} if _name == "tile_map" else [{"x": 5, "y": 6, "text": "Leek"}] if _name == "find_rows" else "ok"
        monkeypatch.setattr(look, name, view)

    async def go():
        assert await sdk.look_map(ctx, 8, (3, 4)) == "map: @"
        assert await sdk.FACTS["find"](ctx, "forage", "Leek") == [{"x": 5, "y": 6, "what": "Leek"}]  # a list a script can slice and loop over
        for name, args in (("people", ["Rob"]), ("item_info", ["Parsnip"]), ("tile_info", [3, 4])):
            assert await sdk.FACTS[name](ctx, *args) == "ok"

    asyncio.run(go())
    assert seen == [("tile_map", 8, [3, 4]), ("find_rows", "forage", "Leek"), ("people", "Rob"), ("item", "Parsnip"), ("tile", [3, 4])]


@pytest.mark.parametrize("name, args, error, match", [
    ("look_map", [99], ActionError, "radius must be from 3 to 45"),
    ("look_map", [8, 3], TypeError, "center must be"),
    ("find", ["dragons"], TypeError, "find's kind is one of"),
    ("tile_info", [1, "2"], TypeError, "the tile must be"),
    ("item_info", [None], TypeError, "name must be text"),
    ("web", ["What does Robin love?"], ActionError, "web lookups are off"),
    ("screenshot", [1], ActionError, "radius must be from 2"),
])
def test_views_explain_bad_arguments(ctx, name, args, error, match):
    with pytest.raises(error, match=match):
        asyncio.run(sdk.FACTS[name](ctx, *args))
