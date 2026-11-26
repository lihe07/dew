"""The diary: the planner's summary of each day, the last 7 shown each morning."""

from stardew_agent.director import Director
from stardew_agent.storage import SaveData


def director(tmp_path):
    d = Director.__new__(Director)
    d.save, d.show = SaveData(tmp_path, "Farm_1"), lambda kind, text: None
    return d


def test_keeps_the_last_seven_days(tmp_path):
    d = director(tmp_path)
    for i in range(9):
        d.write_diary(f"day {i}", "Shipped potatoes for 2100g.", "money 0g -> 2100g (+2100g)")
    week = d.diary()
    assert [e["date"] for e in week] == [f"day {i}" for i in range(2, 9)]
    assert week[-1]["summary"] == "Shipped potatoes for 2100g.\nEnd of day: money 0g -> 2100g (+2100g)"


def test_a_night_without_a_diary_writes_nothing(tmp_path):
    d = director(tmp_path)
    d.write_diary("day 1", "", "money 0g -> 0g (+0g)")
    assert d.diary() == []
