"""The diary: one automatic summary per day, the last 7 shown each morning."""

import asyncio
from types import SimpleNamespace as NS

from stardew_agent.director import Director
from stardew_agent.storage import SaveData


def director(tmp_path, answer="Shipped potatoes for 2100g."):
    async def ask(prompt):
        return answer

    d = Director.__new__(Director)
    d.save, d.brain, d.show = SaveData(tmp_path, "Farm_1"), NS(summarize=ask), lambda kind, text: None
    return d


def test_keeps_the_last_seven_days(tmp_path):
    d = director(tmp_path)
    for i in range(9):
        asyncio.run(d.write_diary(f"day {i}"))
    week = d.diary()
    assert [e["date"] for e in week] == [f"day {i}" for i in range(2, 9)]
    assert week[-1]["summary"] == "Shipped potatoes for 2100g."


def test_a_failed_summary_never_stops_the_night(tmp_path):
    d = director(tmp_path)

    async def broken(prompt):
        raise RuntimeError("gateway down")

    d.brain = NS(summarize=broken)
    asyncio.run(d.write_diary("day 1"))
    assert d.diary() == []
