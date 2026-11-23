"""Steering: a typed message stops the script and its action, and wakes the planner; /later waits."""

import asyncio

from conftest import last_text, names, needs_wasm, play, reply, run_call, text


@needs_wasm
def test_message_stops_a_running_action(make_brain, acts):
    acts.seconds["mine"] = 5
    b, ctx, sent = make_brain([reply(run_call("mine()\ngo('Farm')"))])
    acts.checks.append(lambda name: asyncio.get_running_loop().call_later(0.2, b.steer, "PLAYER: go fishing instead"))  # once the action runs
    asyncio.run(play(b, timeout=10))
    assert ctx.game.released
    assert names(acts) == ["mine"]  # the script was stopped, so it never went to the farm
    woken = last_text(sent[1])
    assert woken.startswith("PLAYER: go fishing instead")
    assert "stopped partway: Stopped: the PLAYER interrupted" in woken and "the script stopped at line 1" in woken


def test_message_restarts_thinking(make_brain, acts):
    b, ctx, sent = make_brain([reply(text(), stop="end_turn")] * 2)
    steps = asyncio.run(play(b, steer=(0.01, "PLAYER: sleep now")))
    assert any("restarting" in s.text for s in steps)
    assert "PLAYER: sleep now" in last_text(sent[-1])


@needs_wasm
def test_later_waits_for_the_next_wake_up(make_brain, acts):
    acts.seconds["mine"] = 0.3
    b, ctx, sent = make_brain([reply(run_call("mine()"))])

    async def go():
        asyncio.get_running_loop().call_later(0.1, b.tell, "PLAYER: buy seeds later")
        return await play(b)

    asyncio.run(go())
    assert names(acts) == ["mine"]  # not stopped
    assert not ctx.game.released
    assert "PLAYER: buy seeds later" in last_text(sent[1]) and "#1 mine:\ndone" in last_text(sent[1])
