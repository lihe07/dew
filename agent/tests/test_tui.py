"""The full-screen view: the planner's side of each day, and getting around many days."""

from rich.console import Console

from stardew_agent.tui import TUI, tab_window

REPORT = "# Long-term goals (the planner's)\nbundles\n\n# Today's plan (from the planner, last night)\n1. Water\n2. Sell\n\n# Memory\nx: y"


def days(count):
    tui = TUI(Console(width=120, height=40))
    for i in range(1, count + 1):
        tui.show("day", f"Mon spring {i}, year 1")
        tui.show("report", REPORT)
        tui.show("notes", f"planner, tomorrow:\nplant day {i + 1}")
        tui.show("notes", f"diary, Mon spring {i}, year 1: day {i} done")
    return tui


def test_each_day_keeps_its_plan_diary_and_tomorrow():
    day = days(2).days[0]
    assert (day.plan, day.diary, day.tomorrow) == ("1. Water\n2. Sell", "day 1 done", "plant day 2")


def test_the_arrows_move_by_a_day_a_week_or_to_the_ends():
    tui = days(30)
    tui._key("\x1b[D")
    assert tui.viewing == 28
    tui._key("\x1b[1;2D")
    assert tui.viewing == 21
    tui._key("\x1b[1;5D")
    assert tui.viewing == 0
    tui._key("\x1b[1;2C")
    assert tui.viewing == 7
    tui._key("\x1b[1;5C")
    assert tui.viewing is None  # following today again


def test_the_tabs_shown_keep_the_selected_day_in_view():
    assert tab_window([10] * 40, 0, 55) == (0, 5)
    assert tab_window([10] * 40, 39, 55) == (35, 40)
    first, last = tab_window([10] * 40, 20, 55)
    assert first <= 20 < last and last - first == 5
    assert tab_window([10] * 3, 1, 100) == (0, 3)
