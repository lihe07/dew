"""Your messages join the agent's queue: they wake it if it's waiting, or are read right after its current thought; the script keeps running."""

import asyncio

from conftest import last_text, names, needs_wasm, play, reply, run_call, text


@needs_wasm
def test_a_message_wakes_the_agent_and_the_script_goes_on(make_brain, acts):
    acts.seconds["Mine.descend"] = 0.5
    b, ctx, sent = make_brain([reply(run_call("mine.descend()\ngo('Farm')"))])
    acts.checks.append(lambda name: name == "Mine.descend" and asyncio.get_running_loop().call_later(0.1, b.steer, "PLAYER: go fishing instead"))

    async def go():
        result = asyncio.ensure_future(play(b, timeout=10))
        await asyncio.sleep(0.3)
        queued = b.queued()  # shown in the view until the agent reads it
        return await result, queued

    _, queued = asyncio.run(go())
    assert [(who, text) for _, who, text in queued] in ([("you", "go fishing instead")], [])  # shown until read (it may already have been)
    assert names(acts) == ["Mine.descend", "go"]  # not stopped: the agent decides
    assert not ctx.game.released
    woken = last_text(sent[1])
    assert woken.startswith("PLAYER: go fishing instead") and "the player sent a message" in woken
    assert b.queued() == []  # read


def test_a_message_while_thinking_is_read_right_after(make_brain, acts):
    b, ctx, sent = make_brain([reply(text(), stop="end_turn")])
    steps = asyncio.run(play(b, steer=(0.01, "PLAYER: sleep now")))
    assert not any("restarting" in s.text for s in steps)  # the thought isn't thrown away
    assert "PLAYER: sleep now" in last_text(sent[1])


@needs_wasm
def test_later_waits_for_the_next_wake_up(make_brain, acts):
    acts.seconds["Mine.descend"] = 0.3
    b, ctx, sent = make_brain([reply(run_call("mine.descend()"))])

    async def go():
        asyncio.get_running_loop().call_later(0.1, b.tell, "PLAYER: buy seeds later")
        return await play(b)

    asyncio.run(go())
    assert names(acts) == ["Mine.descend"]  # not stopped
    assert not ctx.game.released
    assert "PLAYER: buy seeds later" in last_text(sent[1]) and "09:00 mine.descend" in last_text(sent[1])
