"""Fakes for testing the agent and the plan runner without the game or a model."""

import asyncio
import copy
import inspect
from types import SimpleNamespace as NS

import pytest
from stardew_agent import api, wasm_host
from stardew_agent.api.base import Api
from stardew_agent.brain import Brain
from stardew_agent.driver import BridgeError
from stardew_agent.game import ActionError
from stardew_agent.script import Context, Runner


needs_wasm = pytest.mark.skipif(bool(wasm_host.missing()), reason=f"scripts run in WebAssembly: {wasm_host.missing()}")


class FakeGame:
    def __init__(self, values=None):
        self.values = {"player.health": 100, "player.max_health": 100, "player.energy": 200, "player.max_energy": 270, "time.clock": 900, "location.name": "Farm",
                       "player.money": 500, "player.free_slots": 12, "player.held": None, "player.friends": [],
                       "state": {"world": True}, "time.weekday": "Mon", "time.season": "spring", "time.day": 3, "time.text": "9:00 am", "player.tile": [1, 2], **(values or {})}
        self.values.setdefault("time", {"clock": self.values["time.clock"], "text": "9:00 am", "weather": "Sun", "weekday": "Mon", "season": "spring", "day": 3})
        self.d = NS(on_journal=[])
        self.released = False

    async def read(self, what):
        if isinstance(what, dict) and "from" in what:
            return self.values.get(what["from"], [])
        if isinstance(what, list):
            return [self.values.get(w) for w in what]
        if isinstance(what, dict):
            return {k: self.values.get(v) for k, v in what.items()}
        if what.startswith("bad"):
            raise BridgeError(f"can't read {what}")
        return self.values.get(what)

    async def inventory(self):
        return self.values.get("inventory", [])

    async def find_item(self, query):
        found = next((i for i in await self.inventory() if i["name"].lower() == query.lower()), None)
        if not found:
            raise ActionError(f"you don't have '{query}'")
        return found

    async def open_shop(self):
        self.values["menu.type"] = "ShopMenu"

    async def shop_stock(self):
        return self.values.get("stock", [])

    async def release(self):
        self.released = True

    def drain_journal(self):
        return []

    def say(self, kind, text):
        for listener in self.d.on_journal:
            listener({"kind": kind, "text": text})


@pytest.fixture
def acts(monkeypatch):
    """Fake actions with the real signatures: each logs (wire name, its arguments: records as their short text, and "of" what a
    method acts on), takes acts.seconds[name] (default 10ms), returns acts.data[name], and raises ActionError(acts.errors[name]) if set."""
    acts = NS(log=[], seconds={}, errors={}, checks=[], data={})

    def shown(v):
        return str(v) if isinstance(v, Api) else [shown(x) for x in v] if isinstance(v, list) else v

    def fake(name, real):
        async def act(me, ctx, *args, **kwargs):
            bound = inspect.signature(real).bind(me, ctx, *args, **kwargs)
            for check in acts.checks:
                check(name)
            acts.log.append((name, ({"of": me.ref["name"]} if "name" in me.ref else {}) | {k: shown(v) for k, v in list(bound.arguments.items())[2:]}))
            await asyncio.sleep(acts.seconds.get(name, 0.01))
            if name in acts.errors:
                raise ActionError(acts.errors[name])
            return acts.data.get(name)

        act.__signature__, act.kind = inspect.signature(real), "action"
        return act

    for name, (cls, real) in list(api.CALLS.items()):
        if real.kind == "action":
            monkeypatch.setitem(api.CALLS, name, (cls, fake(name, real)))
    return acts


def judge(*answers):
    """A fake decide which gives these (choice, confidence) answers in turn, then 'other'."""
    asked, states = [], []

    async def decide(state, options):
        asked.append(options)
        states.append(state)
        return answers[len(asked) - 1] if len(asked) <= len(answers) else ("other", 0.9)

    decide.asked, decide.states = asked, states
    return decide


@pytest.fixture
def make_runner(acts):
    def make(values=None, decide=None, idle=None):
        ctx = Context(FakeGame(values), decide=decide, while_idle=idle)
        return Runner(ctx, emit=lambda s: None, wake=lambda: None, poll=0.05), ctx

    return make


def reply(*blocks, stop="tool_use"):
    """A model reply. One which runs a script also waits, as a agent does when it has nothing else to do meanwhile."""
    blocks = list(blocks)
    if any(getattr(b, "name", "") == "run" for b in blocks) and not any(getattr(b, "name", "") == "wait" for b in blocks):
        blocks.append(call("wait", id="wait"))
    return NS(content=blocks, stop_reason=stop, usage=None)


def text(t="ok"):
    return NS(type="text", text=t)


def call(name, input=None, id="c1"):
    return NS(type="tool_use", name=name, input=input or {}, id=id)


def run_call(source, id="c1", **extra):
    return call("run", {"goal": "test", "script": source, **extra}, id)

@pytest.fixture
def make_brain(monkeypatch, acts):
    """A brain whose model gives these replies in turn, then rests. sent records what each request saw."""

    def make(responses, decide=None, values=None, idle=None):
        ctx = Context(FakeGame(values), decide=decide, while_idle=idle)
        b = Brain(ctx, api_key="x", model="m", system="s")
        b.runner.poll = 0.05
        sent = []

        async def send():
            sent.append(NS(messages=copy.deepcopy(b.messages), current=b.runner.current and b.runner.current.name, log=list(acts.log)))
            await asyncio.sleep(0.05)
            if not responses:  # out of replies: wait while a script runs, then the test's agent is done
                if not b.runner.idle:
                    return reply(call("wait", id="rest"))
                ctx.finished = True
                return reply(text(), stop="end_turn")
            return responses.pop(0)

        monkeypatch.setattr(b, "_send", send)
        monkeypatch.setattr(b, "_shrink", lambda r: asyncio.sleep(0))
        monkeypatch.setattr(b, "_keep", lambda r: [{"type": "text", "text": "ok"}])
        monkeypatch.setattr(b, "_check_limits", lambda: None)
        return b, ctx, sent

    return make


def last_text(request):
    """The text of the latest user message a request saw."""
    return "\n".join(c.get("text", "") for c in request.messages[-1]["content"] if c["type"] == "text")


async def play(b, prompt="play", steer=None, timeout=3):
    if steer:
        asyncio.get_running_loop().call_later(steer[0], b.steer, steer[1])
    return await asyncio.wait_for(collect(b.think(prompt)), timeout)


async def collect(steps):
    return [s async for s in steps]


def names(acts):
    return [name for name, _ in acts.log]


async def run_script(r, source, timeout=3, during=None, **options):
    """Run a script to its end, doing during() meanwhile; returns why the agent was woken, and keeps the trace and images on r."""
    r.start()
    await r.run(source, "test", **options)
    if during:
        await during()
    await until(lambda: r.script is None, timeout)
    r.trace, r.images, why = r.take()
    await r.halt("test over")
    return why


async def until(condition, timeout=2):
    for _ in range(int(timeout / 0.01)):
        if condition():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("timed out")
