"""A compromised script process: whatever it sends, the agent's side stays safe. These use a fake process, so they need no WebAssembly."""

import asyncio

import pytest
from conftest import run_script
from stardew_agent import script


def hostile(monkeypatch, tmp_path, body: str):
    """Run this Python as the 'script process' instead of the sandbox child."""
    import sys
    child = tmp_path / "hostile.py"
    child.write_text("import json, sys, time\nsys.stdin.readline()\n" + body)
    monkeypatch.setattr(script, "_COMMAND", [sys.executable, "-I", str(child)])


def test_a_process_that_stops_reading_is_ended(make_runner, acts, monkeypatch, tmp_path):
    hostile(monkeypatch, tmp_path, "for _ in range(20000):\n    print(json.dumps({'call': 'money', 'args': []}), flush=True)\ntime.sleep(30)\n")
    r, _ = make_runner()
    r.max_seconds = 2
    why = asyncio.run(run_script(r, "money()", timeout=10))
    assert why and why[0].startswith("the script was ended")


@pytest.mark.parametrize("size, limit", [(10, f"{script.MAX_LOGS} lines"), (9000, f"{script.MAX_LOG_CHARS} characters")])
def test_a_log_flood_is_cut_off(make_runner, acts, monkeypatch, tmp_path, size, limit):
    hostile(monkeypatch, tmp_path, f"for _ in range(100000):\n    print(json.dumps({{'log': 'x' * {size}}}), flush=True)\n")
    r, _ = make_runner()
    why = asyncio.run(run_script(r, "money()", timeout=10))
    assert why == [f"the script was ended: it logged over {limit}"]
    assert len(r.trace) <= script.MAX_LOGS + 2 and all(len(t) < script.MAX_LOG_TEXT + 50 for t in r.trace)


@pytest.mark.parametrize("message", ["[1]", "5", "null", "[" * 100000 + "]" * 100000, '{"call": "backpack", "args": ["bogus"]}', '{"call": "go", "args": [{"item": {}}]}'])
def test_malformed_messages_end_the_script_with_a_reason(make_runner, acts, monkeypatch, tmp_path, message):
    hostile(monkeypatch, tmp_path, f"print({message!r}, flush=True)\ntime.sleep(5)\n")
    r, _ = make_runner()
    why = asyncio.run(run_script(r, "money()", timeout=8))
    assert why and why[0].startswith("the script was ended") and "without saying how" not in why[0]


def test_long_text_from_the_process_is_cut(make_runner, acts, monkeypatch, tmp_path):
    hostile(monkeypatch, tmp_path, "print(json.dumps({'ask': 'A' * 900000, 'line': 'x'}), flush=True)\n")
    r, _ = make_runner()
    why = asyncio.run(run_script(r, "money()", timeout=8))
    assert len(why[0]) < script.MAX_TEXT + 60
