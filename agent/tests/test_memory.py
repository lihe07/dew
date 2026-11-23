"""Lasting memory (remember, forget, memory.json, the nightly consolidation) and the save's storage (paths, day transcripts, context, migration)."""

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from conftest import FakeGame
from rich.console import Console
from stardew_agent import director as director_module
from stardew_agent import look
from stardew_agent import memory as kv
from stardew_agent.brain import Brain
from stardew_agent.director import Director
from stardew_agent.look import Image
from stardew_agent.memory import Memory
from stardew_agent.script import Context
from stardew_agent.storage import SaveData, data_root, day_key
from stardew_agent.tui import TUI

DATE = "Tue spring 4, year 1"


def brain_with(memory):
    return Brain(Context(None, memory=memory), api_key="x", model="m", system="s", web=False)


def make_director(tmp_path, monkeypatch=None, memory_model="hy4-flash", client=None, verbose=False):
    shown = []
    save = SaveData(tmp_path / "data", "Farm_1")
    save.prepare(old_root=tmp_path / "old")
    brain = NS(messages=[], usage=NS(turns=0), client=client, on_context=None)
    d = Director(FakeGame(), brain, Context(FakeGame()), goal="farm", save=save, show=lambda kind, text, data=None, ok=True: shown.append((kind, text, data, ok)),
                 memory_model=memory_model, verbose=verbose)
    d.shown = shown
    if monkeypatch:
        async def state(g):
            return {"date": DATE}

        async def items(g):
            return {"items": [{"name": "Parsnip"}]}

        async def status(g):
            return "status: 9:00 am on the Farm"

        monkeypatch.setattr(look, "state", state)
        monkeypatch.setattr(look, "items", items)
        monkeypatch.setattr(director_module, "status", status)
    return d


# --- the tools -------------------------------------------------------------------------------------------


def test_remember_sets_and_replaces_and_forget_removes(tmp_path):
    m = Memory(tmp_path / "memory.json")
    b = brain_with(m)
    out = b._memory("remember", {"key": "shop.pierre.hours", "value": "9am-5pm, closed Wednesday"})
    assert out.ok and out.text == "remembered shop.pierre.hours (1 keys)"
    out = b._memory("remember", {"key": "shop.pierre.hours", "value": "9am-5pm"})
    assert out.ok and out.text.startswith("replaced shop.pierre.hours")
    assert m.facts == {"shop.pierre.hours": "9am-5pm"}
    assert not (tmp_path / "memory.json").exists()  # written only when the day ends, as the game saves
    m.save()
    assert json.loads((tmp_path / "memory.json").read_text()) == {"shop.pierre.hours": "9am-5pm"}
    assert b._memory("forget", {"key": "shop.pierre.hours"}).text == "forgot shop.pierre.hours (0 keys)"
    m.save()
    assert json.loads((tmp_path / "memory.json").read_text()) == {}
    assert not b._memory("forget", {"key": "nothing.here"}).ok


def test_memory_limits(tmp_path):
    b = brain_with(Memory(tmp_path / "memory.json"))
    assert not b._memory("remember", {"key": "k" * 61, "value": "v"}).ok
    assert b._memory("remember", {"key": "k" * 60, "value": "v"}).ok
    assert not b._memory("remember", {"key": "has spaces", "value": "v"}).ok
    assert not b._memory("remember", {"key": "a.b", "value": "x" * 501}).ok
    assert not b._memory("remember", {"key": "a.b", "value": ""}).ok
    assert not b._memory("remember", {"key": "a.b"}).ok and not b._memory("forget", {}).ok
    for i in range(149):
        assert b._memory("remember", {"key": f"fact.{i}", "value": "v"}).ok
    full = b._memory("remember", {"key": "one.more", "value": "v"})
    assert not full.ok and "forget stale" in full.text
    assert b._memory("remember", {"key": "fact.3", "value": "replacing is fine"}).ok  # a full memory can still change a fact


def test_the_planner_tools_change_memory_while_it_plays(make_brain):
    from conftest import call, play, reply

    b, ctx, sent = make_brain([reply(call("remember", {"key": "farm.chest", "value": "by the house (64,15)"}), call("forget", {"key": "x"}, id="c2"))])
    steps = asyncio.run(play(b))
    assert ctx.memory.facts == {"farm.chest": "by the house (64,15)"}
    notes = [s for s in steps if s.kind == "notes"]
    assert notes[0].ok and notes[0].text.startswith("remembered farm.chest") and not notes[1].ok


def test_memory_is_written_atomically(tmp_path, monkeypatch):
    path = tmp_path / "memory.json"
    m = Memory(path)
    m.remember("a", "1")
    m.save()

    def crash(src, dst):
        raise OSError("disk died")

    monkeypatch.setattr(os, "replace", crash)
    m.remember("b", "2")
    with pytest.raises(OSError):
        m.save()
    assert json.loads(path.read_text()) == {"a": "1"}  # the old file is whole
    assert [p.name for p in tmp_path.iterdir()] == ["memory.json"]  # and no temporary file is left


def test_the_morning_report_shows_all_memory_sorted(tmp_path, monkeypatch):
    d = make_director(tmp_path, monkeypatch)
    d.memory.remember("shop.pierre.hours", "9am-5pm")
    d.memory.remember("farm.chest", "by the house")
    report = asyncio.run(d.report())
    assert "# Memory\nfarm.chest: by the house\nshop.pierre.hours: 9am-5pm\n" in report
    assert "# Memory\n(empty" in asyncio.run(make_director(tmp_path / "2", monkeypatch).report())


def test_notes_md_is_imported_once(tmp_path):
    folder = tmp_path / "data" / "Farm_1"
    folder.mkdir(parents=True)
    (folder / "notes.md").write_text("Plant parsnips.\nChest by the house.\n")
    save = SaveData(tmp_path / "data", "Farm_1")
    said = save.prepare(old_root=tmp_path / "old")
    assert any("notes" in s for s in said)
    assert Memory(save.memory_file).facts == {"notes": "Plant parsnips.\nChest by the house."}
    assert not (folder / "notes.md").exists() and (folder / "notes.md.imported").exists()
    assert save.prepare(old_root=tmp_path / "old") == []  # once
    assert Memory(save.memory_file).render() == "notes: Plant parsnips. / Chest by the house."


# --- storage ---------------------------------------------------------------------------------------------


def test_data_lives_under_the_working_directory_unless_told(tmp_path):
    assert data_root(None, env={}, cwd=tmp_path) == tmp_path / "stardew-data"
    assert data_root(None, env={"STARDEW_DATA": str(tmp_path / "env")}, cwd=tmp_path) == tmp_path / "env"
    assert data_root(str(tmp_path / "flag"), env={"STARDEW_DATA": str(tmp_path / "env")}, cwd=tmp_path) == tmp_path / "flag"
    save = SaveData(data_root(None, env={}, cwd=tmp_path), "Farm_1")
    save.prepare(old_root=tmp_path / "old")
    assert save.memory_file == tmp_path / "stardew-data/Farm_1/memory.json"
    assert (tmp_path / "stardew-data/Farm_1/days").is_dir() and (tmp_path / "stardew-data/Farm_1/screenshots").is_dir()


def test_the_cli_takes_a_data_flag(monkeypatch):
    from stardew_agent import cli

    seen = {}
    monkeypatch.setattr("sys.argv", ["stardew", "play", "--data", "/somewhere", "--memory-model", "none"])
    monkeypatch.setattr(cli, "load_dotenv", lambda path: None)
    monkeypatch.setattr(cli.asyncio, "run", lambda coro: (seen.update(coro.cr_frame.f_locals), coro.close()))
    cli.main()
    args = seen["args"]
    assert args.data == "/somewhere" and args.memory_model == "none"


def test_old_data_is_moved_over_once(tmp_path):
    old = tmp_path / "old" / "Farm_1"
    old.mkdir(parents=True)
    (old / "diary.jsonl").write_text('{"date": "x", "summary": "y"}\n')
    (old / "notes.md").write_text("old notes")
    save = SaveData(tmp_path / "data", "Farm_1")
    said = save.prepare(old_root=tmp_path / "old")
    assert said[0].startswith("moved") and not old.exists()
    assert (save.dir / "diary.jsonl").exists() and Memory(save.memory_file).facts == {"notes": "old notes"}
    assert save.prepare(old_root=tmp_path / "old") == []


def test_old_data_is_left_when_the_new_folder_exists(tmp_path):
    old = tmp_path / "old" / "Farm_1"
    old.mkdir(parents=True)
    (old / "diary.jsonl").write_text("")
    (tmp_path / "data" / "Farm_1").mkdir(parents=True)
    assert SaveData(tmp_path / "data", "Farm_1").prepare(old_root=tmp_path / "old") == []
    assert old.exists()


def test_each_day_is_written_to_its_own_transcript_and_loaded_back(tmp_path):
    save = SaveData(tmp_path, "Farm_1")
    save.record("info", "before the day")  # kept until the day is known
    for date in ["Mon spring 3, year 1", "Tue spring 4, year 1"]:
        save.record("day", date)
        save.record("result", "done", {"name": "harvest", "value": Image("A" * 50_000), "status": {"clock": 900}, "big": "x" * 30_000})
        save.record("notes", "remembered farm.chest (1 keys)", {"key": "farm.chest", "value": "here"})
    assert sorted(p.name for p in save.days_dir.iterdir()) == ["0001-spring-03.jsonl", "0001-spring-04.jsonl"]
    days = save.load_days()
    assert [d[0]["text"] for d in days] == ["before the day", "Tue spring 4, year 1"]
    step = days[1][1]
    assert step["kind"] == "result" and step["ok"] and step["data"]["value"] == "(screenshot)" and len(step["data"]["big"]) < 3000
    assert len((save.days_dir / "0001-spring-04.jsonl").read_text()) < 10_000


def test_days_load_in_calendar_order_and_only_the_last_seven(tmp_path):
    save = SaveData(tmp_path, "Farm_1")
    dates = [f"Mon {season} {day}, year {year}" for year in (1, 2) for season in ("spring", "summer", "fall", "winter") for day in (1, 28)]
    for date in dates:
        save.record("day", date)
    assert [d[0]["text"] for d in save.load_days(7)] == dates[-7:]
    assert day_key("Sun winter 28, year 12") == "0012-winter-28"


def test_the_full_screen_view_loads_past_days(tmp_path):
    save = SaveData(tmp_path, "Farm_1")
    for date in ["Mon spring 3, year 1", "Tue spring 4, year 1"]:
        save.record("day", date)
        save.record("plan", "started", {"script": "harvest()", "goal": "chores"})
        save.record("result", "done", {"name": "harvest", "args": {}, "value": None, "status": {"clock": 900}})
        save.record("notes", f"diary, {date}: shipped parsnips")
    tui = TUI(Console(file=open(os.devnull, "w")))
    tui.load(save.load_days())
    assert [d.title for d in tui.days] == ["Mon spring 3, year 1", "Tue spring 4, year 1"]
    assert tui.days[0].diary == "shipped parsnips" and len(tui.days[0].scripts) == 1
    tui.show("day", "Tue spring 4, year 1")  # a restart continues the same day in its tab
    tui.show("day", "Wed spring 5, year 1")
    assert [d.title for d in tui.days][-2:] == ["Tue spring 4, year 1", "Wed spring 5, year 1"]


def test_the_director_records_what_it_shows(tmp_path):
    d = make_director(tmp_path)
    d.show("day", DATE)
    d.show("info", "hello")
    lines = [json.loads(l) for l in (d.save.days_dir / "0001-spring-04.jsonl").read_text().splitlines()]
    assert [(l["kind"], l["text"]) for l in lines] == [("day", DATE), ("info", "hello")]
    assert d.shown[-1][:2] == ("info", "hello")


# --- the context ---------------------------------------------------------------------------------------------


def saved_messages():
    return [{"role": "user", "content": [{"type": "text", "text": "morning report"}]},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "c1", "name": "run", "input": {"script": "harvest()"}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "c1", "content": [{"type": "text", "text": "started"}]},
                                         {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "A" * 100_000}}]}]


def test_the_context_is_saved_after_each_planner_turn(tmp_path):
    d = make_director(tmp_path)
    d.date = DATE
    d.brain.usage.turns = 5
    d.save_context(saved_messages())
    saved = json.loads(d.save.context_file.read_text())
    assert saved["date"] == DATE and saved["turns"] == 5
    assert saved["messages"][2]["content"][1] == {"type": "text", "text": "(screenshot not kept)"}
    assert saved["messages"][1] == saved_messages()[1]


def test_a_restart_starts_the_day_from_its_morning(tmp_path, monkeypatch):
    """The game saves only at night, so a run stopped partway through a day begins it again, fresh: no saved context is resumed."""
    d = make_director(tmp_path, monkeypatch)
    d.save.save_context(DATE, saved_messages(), turns=7)
    prompt = asyncio.run(d.opening(DATE))
    assert d.brain.messages == [] and prompt.startswith("GOAL: farm") and "an errand or a few hours" in prompt


# --- consolidation -------------------------------------------------------------------------------------------


class FakeClient:
    def __init__(self, reply):
        self.calls, self.reply = [], reply
        self.messages = self

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.reply, Exception):
            raise self.reply
        return NS(content=[NS(type="text", text=self.reply)])


OLD = {"shop.pierre.hours": "9am-5pm", "shop.pierre.closed": "Wednesday", "quest.robin.deadline": "spring 3", "farm.chest": "by the house",
       "person.haley.likes": "sunflowers"}


def consolidating(tmp_path, reply, verbose=False):
    client = FakeClient(reply)
    d = make_director(tmp_path, client=client, verbose=verbose)
    d.memory.facts = dict(OLD)
    d.memory.save()
    return d, client


def test_a_good_consolidation_is_applied_with_a_backup(tmp_path):
    new = {"shop.pierre.hours": "9am-5pm, closed Wednesday", "farm.chest": "north of the house", "person.haley.likes": "sunflowers"}
    d, client = consolidating(tmp_path, "```json\n" + json.dumps(new) + "\n```", verbose=True)
    asyncio.run(d.consolidate(DATE, "Moved the chest north of the house. Robin's quest is done."))
    assert d.memory.facts == new and json.loads(d.save.memory_file.read_text()) == new
    assert json.loads(d.save.memory_file.with_name("memory.json.bak").read_text()) == OLD
    kind, text, data, ok = d.shown[-1]
    assert kind == "notes" and text == "memory consolidated: 5 → 3 keys (merged 1, dropped 1, changed 2)"
    assert "dropped quest.robin.deadline: spring 3" in data["changes"] and "merged shop.pierre.closed" in data["changes"]
    sent = client.calls[0]
    assert sent["model"] == "hy4-flash" and sent["thinking"] == {"type": "disabled"}
    prompt = sent["messages"][0]["content"]
    assert DATE in prompt and "Robin's quest is done" in prompt and '"farm.chest": "by the house"' in prompt and "JSON" in prompt


@pytest.mark.parametrize("reply, why", [
    ("Here is the memory: {", "isn't JSON"),
    ('["a", "b"]', "isn't a JSON object"),
    ('{"farm.chest": 3, "shop.pierre.hours": "9", "person.haley.likes": "x"}', "isn't text"),
    ('{"farm.chest": "x"}', "drop 4 of 5"),
    (json.dumps({**OLD, **{f"fact.{i}": "v" for i in range(150)}}), "over the limit"),
    (json.dumps({**OLD, "farm.chest": "x" * 501}), "over 500"),
    (json.dumps({**OLD, "bad key": "x"}), "bad key"),
    ("", "no text"),
    (RuntimeError("gateway down"), "failed"),
])
def test_a_bad_consolidation_keeps_the_memory(tmp_path, reply, why):
    d, client = consolidating(tmp_path, reply)
    asyncio.run(d.consolidate(DATE, "a day"))
    assert d.memory.facts == OLD and json.loads(d.save.memory_file.read_text()) == OLD
    assert not d.save.memory_file.with_name("memory.json.bak").exists()
    kind, text, data, ok = d.shown[-1]
    assert text.startswith("memory kept as it was") and why in text


def test_consolidation_can_be_turned_off(tmp_path):
    client = FakeClient("{}")
    d = make_director(tmp_path, memory_model=None, client=client)
    asyncio.run(d.consolidate(DATE, "a day"))
    assert client.calls == []


def test_the_imported_notes_survive_consolidation_unchanged(tmp_path):
    d, client = consolidating(tmp_path, "")
    d.memory.facts["notes"] = "n" * 3000
    client.reply = json.dumps(d.memory.facts)
    asyncio.run(d.consolidate(DATE, "a day"))
    assert d.shown[-1][1].startswith("memory consolidated")


def test_the_change_counts():
    old = {"a": "parsnips sell for 35g", "b": "x", "c": "y"}
    new = {"a": "parsnips sell for 35g at Pierre's", "d": "z"}
    change = kv.compare(old, new)
    assert (change.merged, change.dropped, change.changed, change.added) == ([], ["b", "c"], ["a"], ["d"])
    assert str(change) == "memory consolidated: 3 → 2 keys (merged 0, dropped 2, changed 1, added 1)"


def test_one_long_fact_may_be_split_into_several(tmp_path):
    notes = "Pierre's shop opens 9am-5pm and is closed on Wednesdays. The chest is north of the house. Haley likes sunflowers."
    split = {"shop.pierre.hours": "9am-5pm, closed Wednesdays", "farm.chest": "north of the house", "person.haley.likes": "sunflowers"}
    client = FakeClient(json.dumps(split))
    d = make_director(tmp_path, client=client)
    d.memory.facts = {"notes": notes}
    d.memory.save()
    asyncio.run(d.consolidate(DATE, "a day"))
    assert d.memory.facts == split  # one key dropped, but its facts are kept


def test_losing_most_of_the_text_is_refused(tmp_path):
    client = FakeClient(json.dumps({"notes": "short"}))
    d = make_director(tmp_path, client=client)
    d.memory.facts = {"notes": "x" * 400}
    d.memory.save()
    asyncio.run(d.consolidate(DATE, "a day"))
    assert d.memory.facts == {"notes": "x" * 400} and "characters" in d.shown[-1][1]
