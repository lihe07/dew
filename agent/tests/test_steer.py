"""Your messages join the planner's queue: they wake it if it's waiting, or are read right after its current thought; the script keeps running."""

import asyncio

from conftest import last_text, names, needs_wasm, play, reply, run_call, text


@needs_wasm
def test_a_message_wakes_the_planner_and_the_script_goes_on(make_brain, acts):
    acts.seconds["mine"] = 0.5
    b, ctx, sent = make_brain([reply(run_call("mine()\ngo('Farm')"))])
    acts.checks.append(lambda name: name == "mine" and asyncio.get_running_loop().call_later(0.1, b.steer, "PLAYER: go fishing instead"))

    async def go():
        result = asyncio.ensure_future(play(b, timeout=10))
        await asyncio.sleep(0.3)
        queued = b.queued()  # shown in the view until the planner reads it
        return await result, queued

    _, queued = asyncio.run(go())
    assert ("you", "go fishing instead") in queued or not queued  # it may already have been read
    assert names(acts) == ["mine", "go"]  # not stopped: the planner decides
    assert not ctx.game.released
    woken = last_text(sent[1])
    assert woken.startswith("PLAYER: go fishing instead") and "the player sent a message" in woken
    assert b.queued() == []  # read


def test_a_message_while_thinking_is_read_right_after(make_brain, acts):
    b, ctx, sent = make_brain([reply(text(), stop="end_turn")] * 2)
    steps = asyncio.run(play(b, steer=(0.01, "PLAYER: sleep now")))
    assert not any("restarting" in s.text for s in steps)  # the thought isn't thrown away
    assert "PLAYER: sleep now" in last_text(sent[-1]) and len(sent) == 2


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
