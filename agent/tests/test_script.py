"""Scripts: the SDK, the judge, exceptions, WebAssembly isolation, and the runner, with fake actions and a fake judge."""

import asyncio
import json
import re
from pathlib import Path

import pytest
from conftest import judge, last_text, names, needs_wasm, play, reply, run_call, run_script, until
from stardew_agent import look, script, vision
from stardew_agent.script import Rejected, failure, interruption, vet

pytestmark = needs_wasm


def text(r):
    return "\n".join(r.trace)


@pytest.mark.parametrize("source, call, args", [
    ("go('Beach')", "go", {"target": "Beach"}),
    ("go((3, 4))", "go", {"target": [3, 4]}),
    ("water(Area(0, 0, 2, 2))", "water", {"where": "(0,0)-(2,2)"}),
    ("plant('Parsnip Seeds', [Tile(1, 1), (2, 1)])", "plant", {"item": "Parsnip Seeds", "where": ["(1,1)", [2, 1]]}),
    ("fish(5)", "fish", {"casts": 5}),
    ("wait(minutes=30)", "wait", {"minutes": 30}),
    ("talk('Robin', gift='Wood')", "talk", {"who": "Robin", "gift": "Wood"}),
    ("answer(1)", "answer", {"choice": 1}),
    ("screen.raw([{'op': 'step', 'n': 5}])", "Screen.raw", {"ops": [{"op": "step", "n": 5}]}),
    ("here.chests[0].put('Wood', 5)", "Chest.put", {"item": "Wood", "count": 5}),  # a method of a record, acting through its reference
    ("backpack['Carp'].ship()", "Item.ship", {"of": "Carp"}),
])
def test_calls_reach_their_actions(make_runner, acts, source, call, args):
    values = {"location.object_rows": [{"x": 64, "y": 15, "name": "Chest", "Items": [{"name": "Wood", "stack": 9}]}], "inventory": [{"name": "Carp", "stack": 3}]}
    r, _ = make_runner(values=values)
    why = asyncio.run(run_script(r, source))
    assert acts.log == [(call, args)] and why == ["the script ended"]


def test_a_call_of_the_wrong_shape_raises_type_error(make_runner, acts):
    r, _ = make_runner()
    why = asyncio.run(run_script(r, "try:\n    fish(3, 4, 5)\nexcept TypeError as e:\n    print('wrong:', e)\nwait(1, 2, 3)"))
    assert not acts.log and why == ["the script raised TypeError at line 5"] and any(t.startswith("printed: wrong:") for t in r.trace)


def test_actions_return_their_results_and_fields_read_live(make_runner, acts):
    acts.data["fish"] = {"caught": 2, "fish": ["Carp", "Sunfish"]}
    values = {"player.money": 1840, "time.clock": 1430, "location.name": "Beach", "location.npcs": [{"name": "Willy"}], "player.free_slots": 0,
              "inventory": [{"name": "Carp", "stack": 3, "price": 30, "energy": 13, "category": -4}, {"name": "Wood", "stack": 50, "price": 2}]}
    r, _ = make_runner(values=values)
    asyncio.run(run_script(r, "got = fish()\nprint(got['caught'], farmer.money, day.time, day.minutes_left, here.name, here.people)\n"
                              "print([f.name for f in backpack.fish], [f.name for f in backpack.food], backpack.free, backpack.full, backpack.items[1].count, len(backpack))"))
    assert "printed: 2 1840 1430 690 Beach [Person(Willy)]" in r.trace and "printed: ['Carp'] ['Carp'] 0 True 50 2" in r.trace


def test_check_is_true_false_or_unsure(make_runner, acts):
    r, ctx = make_runner(decide=judge(("case0", 0.9), ("none", 0.8), ("case0", 0.4)))
    why = asyncio.run(run_script(r, "if check('it is raining'):\n    fish()\nif check('the soil is dry'):\n    water()\ncheck('a festival is on')\nforage()"))
    assert names(acts) == ["fish"] and why == ["the script raised Unsure at line 5"]
    assert "Unsure: 'a festival is on' (confidence 0.40)\n  at line 5: check('a festival is on')" in text(r)
    assert ctx.decide.asked[0] == {"case0": "it is raining", "none": script.NONE}


def test_pick_chooses_among_options(make_runner, acts):
    values = {"inventory": [{"name": "Carp", "stack": 3, "price": 30, "energy": 13}, {"name": "Salad", "stack": 1, "price": 110, "energy": 113}],
              "menu.choices": ["Sure, I'll help", "Not now"]}
    r, ctx = make_runner(values=values, decide=judge(("case0", 0.9), ("case1", 0.8), ("case0", 0.2)))
    why = asyncio.run(run_script(r, "pick('the cheapest food', backpack.food).eat()\nanswer('agree to help')\npick('anyone', ['Robin', 'Pam'])"))
    assert acts.log[0] == ("Item.eat", {"of": "Carp"})
    assert acts.log[1] == ("answer", {"choice": 1})
    assert ctx.decide.asked[0] == {"case0": "Carp: x3, 30g, +13 energy", "case1": "Salad: x1, 110g, +113 energy", "none": script.NONE}
    assert ctx.decide.states[0]["choosing"] == "the cheapest food"
    assert why == ["the script raised Unsure at line 3"]


@pytest.mark.parametrize("reason, kind", [
    ("the way to Mountain is closed: the bridge is out", script.Closed),
    ("Robin isn't around today", script.Closed),
    ("40x Parsnip Seeds costs 800g but you have 500g", script.NotEnough),
    ("your inventory is full", script.NotEnough),
    ("you don't have 'Salad'", script.NotEnough),
    ("couldn't reach the shop counter: the way kept being blocked", script.Unreachable),
    ("no walking route from Farm to 'Moon'", script.Unreachable),
    ("Carp can't be shipped", script.ActionFailed),
])
def test_errors_become_exceptions(reason, kind):
    assert type(failure(reason)) is kind and failure(reason).reason == reason


def test_interruptions_become_questions_or_cutscenes():
    asked = interruption("a menu needs your attention (DialogueBox)", ["Yes", "No"])
    assert type(asked) is script.Question and asked.choices == ["Yes", "No"]
    assert type(interruption("a minigame is running (FishingGame)", None)) is script.Cutscene


def test_caught_and_uncaught_exceptions(make_runner, acts):
    acts.errors["talk"] = "Robin isn't around today"
    r, _ = make_runner()
    why = asyncio.run(run_script(r, "try:\n    talk('Robin')\nexcept Closed as e:\n    print('closed:', e.reason)\n    go('Town')\n    talk('Robin')\nfish()"))
    assert names(acts) == ["talk", "go", "talk"] and why == ["the script raised Closed at line 6"]
    assert "printed: closed: Robin isn't around today" in r.trace
    assert any(" talk " in t and " — " in t for t in r.trace)  # a failed action: the reason after a dash
    assert "Closed: Robin isn't around today\n  at line 6: talk('Robin')" in text(r)


def test_danger_is_raised_between_calls_and_mid_action(make_runner, acts):
    r, _ = make_runner(values={"player.health": 25, "inventory": [{"name": "Salad", "stack": 1, "energy": 113}]})
    why = asyncio.run(run_script(r, "try:\n    mine.descend()\nexcept LowHealth:\n    backpack['Salad'].eat()\n    go('Farm')"))
    assert names(acts) == ["Item.eat", "go"] and why == ["the script ended"]
    acts.log.clear()
    acts.seconds["fish"] = 3
    acts.checks.append(lambda name: acts.log and acts.seconds.update(fish=0.01))  # the second fish is quick
    r, ctx = make_runner()

    async def tire():
        await until(lambda: r.current is not None)
        ctx.game.values["player.energy"] = 10

    why = asyncio.run(run_script(r, "try:\n    fish(20)\nexcept LowEnergy as e:\n    print('tired', e)\nfish(20)", timeout=4, during=tire))
    assert "printed: tired energy is low (10)" in r.trace and ctx.game.released
    assert names(acts) == ["fish", "fish"] and why == ["the script ended"]  # raised once, then the script went on


def test_time_to_go_home_is_computed_once(make_runner, acts):
    r, ctx = make_runner(values={"time.clock": 2345})

    async def walk(target):
        return 90 if target == "home" else None

    ctx.game.minutes = walk
    why = asyncio.run(run_script(r, "try:\n    fish()\nexcept TimeToGoHome as e:\n    print(e.minutes_home, e.minutes_left)\n    go('Farm')\n    sleep()"))
    assert "printed: 90 135" in r.trace and names(acts) == ["go", "sleep"] and why == ["the script ended"]


def test_stopped_cant_be_caught_and_a_new_run_replaces_the_old(make_runner, acts):
    acts.seconds["Mine.descend"] = 0.5
    r, ctx = make_runner()
    said = []

    async def replace():
        await until(lambda: r.current is not None)
        old = r.script
        said.append(await r.run("fish()", "new", 1))
        await until(lambda: r.script is None and r.current is None and old.done.is_set())

    why = asyncio.run(run_script(r, "try:\n    mine.descend()\n    mine.descend()\nexcept Exception:\n    print('caught it')\nfight()", during=replace))
    assert said[0].startswith("stopped the previous script")
    assert names(acts) == ["Mine.descend", "fish"] and "printed: caught it" not in r.trace
    assert any("the script stopped at line" in t for t in r.trace) and why == ["the script ended"]


def test_busy_loops_are_killed(make_runner, acts, monkeypatch):
    r, _ = make_runner()
    r.limits["fuel"] = 5_000_000_000
    why = asyncio.run(run_script(r, "n = 0\nwhile True:\n    try:\n        n += 1\n    except BaseException:\n        pass", timeout=10))
    assert why == ["the script used too much computation (a busy loop?)"]


@pytest.mark.parametrize("source, reason", [("x = [", "line 1: SyntaxError"), ("if True:\nprint(1)", "line 2: SyntaxError"), ("x = 1\n" * 5000, "longer than 20000")])
def test_scripts_which_arent_python_are_rejected(source, reason):
    with pytest.raises(Rejected, match=re.escape(reason)):
        vet(source)


def test_scripts_are_ordinary_python(make_runner, acts):
    source = ("import json, math, itertools\nclass Plan:\n    steps = 2\nmatch Plan.steps:\n    case 2:\n        print(json.dumps({'k': math.floor(2.5)}))\n"
              "print(sum(x for x in range(4)), list(itertools.islice(range(9), 2)))\nwith open('/lib/python3.15/os.py') as f:\n    print(f.readline()[:3])")
    r, _ = make_runner()
    why = asyncio.run(run_script(r, source, timeout=8))
    assert why == ["the script ended"] and 'printed: {"k": 2}' in r.trace and "printed: 6 [0, 1]" in r.trace


def test_idle_work_runs_while_no_script_does(make_runner, acts):
    events, stops = [], []

    async def idle(stop):
        events.append("idle")
        stops.append(stop)
        await stop.wait()
        events.append("stopped")
        return {"killed": 1, "ticks": 40}

    acts.checks.append(lambda name: events.append(name) or (stops and stops[-1].is_set()) or events.append("NOT STOPPED"))
    r, _ = make_runner(idle=idle)

    async def go():
        r.start()
        await until(lambda: events == ["idle"])
        await r.run("fish()", "test")
        await until(lambda: events.count("idle") == 2)
        trace = r.take()[0]
        await r.halt("test over")
        return trace

    trace = asyncio.run(go())
    assert events[:3] == ["idle", "stopped", "fish"] and "NOT STOPPED" not in events
    assert "while idle: killed 1" in trace


def test_the_agent_gets_rejections_and_wake_ups(make_brain, acts):
    acts.seconds["Mine.descend"] = 0.3
    b, ctx, sent = make_brain([reply(run_call("x = [")), reply(run_call("mine.descend()\nprint('done')", id="c2"))])
    asyncio.run(play(b))
    assert "run rejected, nothing changed: line 1: SyntaxError" in json.dumps(sent[1].messages[-1]["content"])
    woken = last_text(sent[2])
    assert "09:00 mine.descend\nprinted: done" in woken and "Woken because: the script ended" in woken


def test_a_look_script_answers_in_the_wake_up(make_brain, acts, monkeypatch):
    async def tile_map(g, radius, center):
        return {"location": "Farm", "map": f"radius {radius}: @..~"}

    async def capture(g, radius, grid):
        return "IMG", {"scale": 0.5, "cropped": False}

    monkeypatch.setattr(look, "tile_map", tile_map)
    monkeypatch.setattr(vision, "capture", capture)
    source = "print(here.map(8))\nscreen.shot(radius=6)\ntry:\n    here.find('dragons')\nexcept TypeError as e:\n    print('no:', e)"
    b, ctx, sent = make_brain([reply(run_call(source))])
    asyncio.run(play(b))
    woken = sent[1].messages[-1]["content"]
    said = last_text(sent[1])
    assert "printed: location: Farm\nmap: radius 8: @..~" in said and "printed: no: find's kind is one of" in said
    assert "Woken because: the script ended" in said and not acts.log
    assert [c["source"]["data"] for c in woken if c["type"] == "image"] == ["IMG"] and ctx.shot["scale"] == 0.5


def test_the_prompt_examples_are_python_using_only_the_sdk():
    import ast
    import builtins
    from stardew_agent import api, sandbox_child
    here = Path(script.__file__).parent
    prompt = (here / "prompts" / "agent.md").read_text()
    examples = re.findall(r"Example:\n```\n(.*?)```", prompt, re.S)
    if (here / "docs" / "examples.md").exists():
        examples += re.findall(r"```python\n(.*?)```", (here / "docs" / "examples.md").read_text(), re.S)
    known = set(sandbox_child.namespace(sandbox_child.Channel(), api.spec())) | set(dir(builtins))
    assert len(examples) >= 1
    for source in examples:
        vet(source)
        tree = ast.parse(source)
        assigned = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
        used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
        assert used <= known | assigned, used - known - assigned


# --- WebAssembly isolation -----------------------------------------------------------------------------------


ESCAPES = {
    "a host file": "open('/home')",
    "a file by its real path": "open(__import__('os').path.expanduser('~/.bashrc'))",
    "the project": "open('/../../agent/pyproject.toml')",
    "up from the stdlib": "open('/lib/../../../etc/passwd')",
    "writing the stdlib": "open('/lib/python3.15/new.py', 'w').write('x')",
    "the environment": "__import__('os').environ['HOME']",
    "a socket": "__import__('socket').create_connection(('1.1.1.1', 80), timeout=2)",
    "a process": "__import__('subprocess').run(['ls'])",
    "forking": "__import__('os').fork()",
    "ctypes": "__import__('ctypes').CDLL(None)",
}


def test_the_guest_cant_reach_the_host(make_runner, acts):
    """Nothing in Python stops these; WebAssembly does."""
    probe = "results = {}\n" + "".join(
        f"try:\n    {code}\n    results[{name!r}] = 'ALLOWED'\nexcept BaseException as ex:\n    results[{name!r}] = type(ex).__name__\n" for name, code in ESCAPES.items()
    ) + "print(results)"
    r, _ = make_runner()
    why = asyncio.run(run_script(r, probe, timeout=10))
    assert why == ["the script ended"]
    results = text(r)
    assert "ALLOWED" not in results, results
    assert all(name in results for name in ESCAPES)


def test_a_memory_blowup_stays_in_the_guest(make_runner, acts):
    r, _ = make_runner()
    why = asyncio.run(run_script(r, "x = 'x' * 10**9"))
    assert why == ["the script raised MemoryError at line 1"]


@pytest.mark.parametrize("source", ["x = 10**10**8", "x = sum(range(10**12))"])
def test_a_huge_computation_runs_out_of_fuel(make_runner, acts, source):
    r, _ = make_runner()
    r.limits["fuel"] = 5_000_000_000
    why = asyncio.run(run_script(r, source, timeout=10))
    assert why == ["the script used too much computation (a busy loop?)"]


def test_waiting_for_an_action_costs_no_fuel(make_runner, acts):
    acts.seconds["fish"] = 1
    r, _ = make_runner()
    r.limits["fuel"] = 5_000_000_000
    assert asyncio.run(run_script(r, "fish()\nfish()", timeout=8)) == ["the script ended"]


def test_startup_with_the_compiled_cache_is_quick(make_runner, acts):
    import time
    r, _ = make_runner()
    asyncio.run(run_script(r, "print(1)", timeout=10))  # makes sure the cache exists
    started = time.monotonic()
    r, _ = make_runner()
    asyncio.run(run_script(r, "print(1)", timeout=10))
    assert time.monotonic() - started < 1.5


def test_without_webassembly_scripts_are_refused(make_runner, acts, monkeypatch):
    monkeypatch.setattr(script.wasm_host, "WASM", script.wasm_host.ROOT / "missing.wasm")
    r, _ = make_runner()
    with pytest.raises(Rejected, match="just setup-wasm"):
        asyncio.run(r.run("print(1)", "test"))


def test_a_stopped_scripts_process_is_gone(make_runner, acts):
    acts.seconds["Mine.descend"] = 5
    r, _ = make_runner()

    async def go():
        r.start()
        await r.run("mine.descend()", "test")
        proc = r.script.proc
        await until(lambda: r.current is not None, 5)
        await r.interrupt("the PLAYER interrupted")
        await r.halt("test over")
        return proc

    assert asyncio.run(go()).returncode is not None


def test_the_wall_clock_ends_a_hung_action(make_runner, acts):
    acts.seconds["fish"] = 30
    r, ctx = make_runner()
    r.max_seconds = 1.5
    why = asyncio.run(run_script(r, "fish()", timeout=8))
    assert why == ["the script was ended: it ran over 0 minutes, so its action was stopped"] and ctx.game.released
    assert any("stopped partway" in t for t in r.trace)


def test_a_stopped_script_gets_nothing_more_done(make_runner, acts):
    r, ctx = make_runner()
    stale = script.Script("here.shop.stock()", "old", [], stopped="a new script replaced it")

    async def go():
        for message in ({"call": "Shop.stock", "self": {"place": "Farm"}}, {"call": "Farmer.money"}, {"call": "go", "args": ["Town"]}, {"call": "check", "args": ["x"]}):
            with pytest.raises(script.Stopped):
                await r._sdk(stale, message)

    asyncio.run(go())
    assert not acts.log


def test_a_log_wakes_the_agent_while_the_script_runs(make_runner, acts, monkeypatch):
    monkeypatch.setattr(script, "WAKE_EVERY", 0.3)
    monkeypatch.setattr(script, "GATHER", 0.05)
    acts.seconds["fish"] = 0.8
    r, _ = make_runner()

    async def go():
        r.start()
        await r.run("print('a treasure chest!')\nprint('and a second one')\nfish(3)\nprint('later')\nfish(3)", "test")
        await until(lambda: r._why)  # woken a moment after the two messages, as one
        trace, _, why = r.take()
        assert why == ["the script sent messages (it's still running)"] and r.script is not None
        assert "printed: a treasure chest!" in trace
        await until(lambda: "printed: later" in r._trace, 3)
        await until(lambda: r._why, 3)  # once the gap since the last wake-up has passed
        _, _, again = r.take()
        await until(lambda: r.script is None, 3)
        await r.halt("test over")
        return again

    assert asyncio.run(go()) == ["the script sent messages (it's still running)"]



def test_scripts_queue_in_three_slots_and_run_one_after_another(make_runner, acts):
    acts.seconds["Mine.descend"] = 0.3
    r, ctx = make_runner()

    async def go():
        assert await r.run("mine.descend()", "first") == "started"
        assert await r.run("fish()", "second") == "queued in slot 2"
        assert await r.run("fish()", "third") == "queued in slot 3"
        with pytest.raises(Rejected, match="full"):
            await r.run("fish()", "fourth")
        assert (await r.drop(3)).startswith("dropped slot 3")
        await until(lambda: "fish" in names(acts) and r.script is None)

    asyncio.run(go())
    assert names(acts) == ["Mine.descend", "fish"]  # the second started on its own when the first ended


def test_the_line_is_followed_in_pure_python_too(make_runner, acts):
    r, _ = make_runner()
    seen = set()

    async def watch():
        while r.script is not None:
            seen.add(r.script.line)
            await asyncio.sleep(0.02)

    asyncio.run(run_script(r, "n = 0\nwhile n < 1_000_000:\n    n += 1\nfarmer.money", timeout=20, during=watch))
    assert seen & {2, 3}  # inside the loop, before any SDK call


def test_talk_takes_a_set_of_people(make_runner, acts):
    r, _ = make_runner()
    asyncio.run(run_script(r, "talk({'Haley', 'Alex'}, gift='Daffodil')"))
    assert acts.log == [("talk", {"who": ["Alex", "Haley"], "gift": "Daffodil"})]
