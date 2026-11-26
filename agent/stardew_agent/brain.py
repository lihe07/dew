"""The agent: a model loop which writes scripts while a runner plays them, and keeps the context small."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import anthropic

from .decide import GATEWAY
from .web import Research
from .driver import BridgeError
from . import docs, look
from .api.screen import Screen
from .look import news, status
from .memory import MAX_KEY, MAX_VALUE, Memory
from .script import Context, Outcome, Rejected, Runner, Step

REMEMBER = {"name": "remember", "description": "Keep a fact for future days, by a short dotted key (like shop.pierre.hours, plan.tomorrow, farm.chest); this sets or replaces it. Your memory is all you keep besides the planner's diary and plan; it's shown every morning.",
            "input_schema": {"type": "object", "additionalProperties": False, "required": ["key", "value"], "properties": {
                "key": {"type": "string", "maxLength": MAX_KEY, "description": "A short dotted name"},
                "value": {"type": "string", "maxLength": MAX_VALUE, "description": "The fact, as short text"}}}}
FORGET = {"name": "forget", "description": "Remove a fact from your memory, once it's stale or wrong.",
          "input_schema": {"type": "object", "additionalProperties": False, "required": ["key"], "properties": {"key": {"type": "string", "maxLength": MAX_KEY}}}}
RUN = {"name": "run", "description": "Put a script (Python with the SDK) in one of 3 slots. Slot 1 runs now, in the background while you think; slots 2 and 3 wait their turn and start one after another, so queue the next plan before this one ends. slot \"next\" (the default) appends, or starts at once if nothing runs; 1 replaces the running script now (its action stops); 2 or 3 sets that slot.",
       "input_schema": {"type": "object", "required": ["script", "goal"], "properties": {
           "script": {"type": "string", "description": "Python using the SDK"}, "goal": {"type": "string", "maxLength": 300},
           "slot": {"anyOf": [{"type": "integer", "enum": [1, 2, 3]}, {"type": "string", "enum": ["next"]}]}}}}
DROP = {"name": "drop", "description": "Delete a script slot: 1 stops the running script (the next one starts), 2 or 3 deletes what waits there.",
        "input_schema": {"type": "object", "additionalProperties": False, "required": ["slot"], "properties": {"slot": {"type": "integer", "enum": [1, 2, 3]}}}}
QUERY = {"name": "query", "description": "Find something out without disturbing the running script: a short read-only script (farmer, day, here.find, people, check...) whose printed output comes back as the answer. It can't act; use run for that.",
         "input_schema": {"type": "object", "additionalProperties": False, "required": ["script"], "properties": {"script": {"type": "string", "description": "Python using the SDK's facts; print what you want to know"}}}}
SCREENSHOT = {"name": "screenshot", "description": "See the game screen now, with people, exits, and objects labelled name (x,y). It doesn't disturb the running script.",
              "input_schema": {"type": "object", "additionalProperties": False, "properties": {
                  "radius": {"type": "integer", "minimum": 2, "maximum": 20, "description": "Crop to this many tiles around the farmer"},
                  "grid": {"type": "boolean", "description": "Overlay tile coordinates"}}}}
WEB = {"name": "web", "description": "Ask a research helper a question about the game; it searches the web and reads the wiki, then answers in a few sentences. It takes a few seconds and costs a little, so remember what you learn.",
       "input_schema": {"type": "object", "additionalProperties": False, "required": ["question"], "properties": {"question": {"type": "string", "maxLength": 400}}}}
DOCS = {"name": "docs", "description": "Read a documentation topic by its path (like farming, or farming/crops from a link in another doc), or one section of it as path#section.",
        "input_schema": {"type": "object", "additionalProperties": False, "required": ["topic"], "properties": {"topic": {"type": "string", "maxLength": 100}}}}
SEARCH_DOCS = {"name": "search_docs", "description": "Search all the documentation for a word or regular expression; returns matching lines as topic#section: line.",
               "input_schema": {"type": "object", "additionalProperties": False, "required": ["pattern"], "properties": {"pattern": {"type": "string", "maxLength": 200}}}}
WAIT = {"name": "wait", "description": "Let the script run and end your turn. You're woken when it ends or needs you.", "input_schema": {"type": "object", "properties": {}}}
TOOLS = [REMEMBER, FORGET, RUN, DROP, QUERY, SCREENSHOT, WEB, DOCS, SEARCH_DOCS, WAIT]
MAP_RADIUS = 10  # tiles each way in the map at every wake-up


class OutOfBudget(Exception):
    """A limit was reached."""



@dataclass
class Usage:
    turns: int = 0
    input: int = 0
    cached: int = 0
    output: int = 0
    web: int = 0
    seconds: float = 0

    def __str__(self) -> str:
        hit = self.cached / max(1, self.input + self.cached)
        return f"{self.turns} turns, {self.input + self.cached:,} tokens in ({hit:.0%} cached), {self.output:,} out, {self.web} web lookups, {self.seconds:.0f}s thinking"


TRIM_AT = 20_000  # characters of stale context (about 5k tokens) worth one cache miss to cut
DOCS_KEPT = 4  # turns a doc result stays whole


class Brain:
    def __init__(self, ctx: Context, *, api_key: str, model: str, system: str):
        self.ctx, self.model, self.system = ctx, model, system
        self.max_turns: int | None = None  # no cap, except briefly at night (Director.think)
        self.usage = Usage()
        self.messages: list[dict] = []
        self.inbox: list[str] = []
        self._sent: list[tuple[float, str]] = []  # the player's messages not yet read, and when they came
        self.stop_reason: str | None = None
        self.effort = "low"  # how hard the agent reasons: low, medium, high, or off (the model's own default is high)
        self.client = anthropic.AsyncAnthropic(api_key=api_key, base_url=GATEWAY, max_retries=4, timeout=180)
        self.is_claude = "claude" in model
        self._steps: asyncio.Queue[Step] = asyncio.Queue()
        self._wake = asyncio.Event()  # the runner needs the agent
        self._poke = asyncio.Event()  # the player steered, or the director ended the turn
        self.runner = Runner(ctx, self._steps.put_nowait, self._wake.set)
        self.research: Research | None = None  # set by whoever has the TinyFish key
        self.custom_id: str | None = None  # Respan's custom ID for the agent's requests: <save folder>-<day>, set each morning

    def reset(self) -> None:
        self.messages.clear()
        self.usage = Usage()
        self.runner.count = 0

    def tell(self, text: str) -> None:
        """Queue a message for the agent's next wake-up, without waking it (the director's notes)."""
        self.inbox.append(text)

    def steer(self, text: str) -> None:
        """The player's message: it joins the queue and wakes the agent, or is read right after the agent's current thought.
        The script keeps running; the agent decides what to do about it."""
        self.inbox.append(text)
        self._sent.append((time.monotonic(), text.removeprefix("PLAYER: ")))
        self.runner.wake("the player sent a message")

    def queued(self) -> list[tuple[float, str, str]]:
        """What waits for the agent to read, in the order it arrived: (when, "you" or "printed", text)."""
        return sorted([(t, "you", text) for t, text in self._sent] + [(t, "printed", text) for t, text in self.runner.printed])

    def interrupt(self, reason: str) -> None:
        """End the turn now, stopping the script."""
        self.stop_reason = reason
        self._poke.set()

    async def think(self, prompt: str) -> AsyncIterator[Step]:
        """Plan while the runner plays the script, until the day ends, the director stops it, or the model rests with nothing left to run."""
        self.stop_reason = None
        self.runner.start()
        play = self._play(prompt)
        try:
            async for s in play:
                yield s
        finally:
            await play.aclose()
            await self.runner.halt(self.stop_reason or "the turn ended")
        while not self._steps.empty():
            yield self._steps.get_nowait()

    async def _play(self, prompt: str) -> AsyncIterator[Step]:
        r = self.runner
        self._wake.clear()
        self._append("user", [{"type": "text", "text": prompt}, *self._news(full=False)])
        while True:
            if self.ctx.game is not None and getattr(self.ctx.game.d, "human", False):
                yield Step("agent", "paused")
                await self._wait_for_player(r)
                yield Step("agent", "resumed")
                continue
            self._check_limits()
            if self._poke.is_set():
                if not await self._poked():
                    return
                yield Step("info", "stopped the script for your message")
                self._append("user", await self._observe())
            yield Step("agent", "thinking")
            request = asyncio.ensure_future(self._send())
            async for s in self._during(request):
                yield s
            if not request.done():
                request.cancel()
                await asyncio.wait({request})
                if not await self._poked():
                    return
                yield Step("info", "restarting the agent's thinking with your message")
                self._append("user", await self._observe())
                continue
            response = request.result()
            yield Step("agent", "done")
            calls = [b for b in response.content if b.type == "tool_use"]
            for block in response.content:
                if block.type == "thinking" and block.thinking.strip():
                    yield Step("think", block.thinking.strip())
                elif block.type == "text" and block.text.strip():
                    yield Step("say", block.text.strip())
            self._append("assistant", self._keep(response))
            if response.stop_reason == "refusal":
                yield Step("say", "(the model declined to continue)")
                return
            if not calls and response.stop_reason == "max_tokens":
                self._append("user", [{"type": "text", "text": "You ran out of room. Think less, and act with one tool call."}])
                continue

            results: list[dict] = []
            acted = False
            for call in calls:
                args = call.input if isinstance(call.input, dict) else {}
                if self.ctx.finished:
                    outcome = Outcome("not run: the day is over", ok=False)
                elif self.ctx.only_tools is not None and call.name not in self.ctx.only_tools:
                    outcome = Outcome(f"only {', '.join(sorted(self.ctx.only_tools))} can be used now", ok=False)
                elif call.name in ("remember", "forget"):  # memory changes at once, even while a script runs
                    outcome = self._memory(call.name, args)
                    text, data = Memory.note(call.name, args, outcome.ok, outcome.text)
                    yield Step("notes", text, outcome.ok, data)
                elif call.name == "query":
                    source = args.get("script")
                    if not isinstance(source, str) or not source.strip():
                        outcome = Outcome("query takes a script", ok=False)
                    else:
                        try:
                            outcome = Outcome(await r.query(source))
                        except Rejected as ex:
                            outcome = Outcome(f"query rejected: {ex}", ok=False)
                    yield Step("query", outcome.text, outcome.ok, {"script": source})
                elif call.name in ("docs", "search_docs"):  # answered at once, from files
                    value = args.get("topic" if call.name == "docs" else "pattern")
                    outcome = Outcome(docs.read(value) if call.name == "docs" else docs.grep(value)) if isinstance(value, str) and value.strip() else Outcome(f"{call.name} takes {'a topic' if call.name == 'docs' else 'a pattern'}", ok=False)
                    yield Step("docs", f"{call.name} {value}", outcome.ok)
                elif call.name == "web":
                    yield Step("agent", "researching", data={"question": str(args.get("question", ""))})
                    outcome = await self._web(args)
                    yield Step("agent", "done")
                    yield Step("web", outcome.text, outcome.ok, {"question": str(args.get("question", ""))})
                elif call.name == "screenshot":
                    content = await self._screenshot(args)
                    yield Step("info", "the agent took a screenshot" if content[0]["type"] == "image" else content[0]["text"])
                    results.append({"type": "tool_result", "tool_use_id": call.id, "content": content})
                    continue
                elif call.name in ("run", "drop", "wait"):
                    outcome = await self._control(call.name, args)
                    acted |= call.name == "wait" and outcome.ok  # only wait ends the turn: run and drop return at once, and thinking goes on
                    starts_now = call.name == "run" and outcome.ok and outcome.text.startswith(("started", "stopped the previous", "slot 1 now"))
                    yield Step("plan", outcome.text, outcome.ok, {"script": args.get("script"), "goal": args.get("goal"), "queued": not starts_now} if call.name == "run" else {})
                else:
                    outcome = Outcome(f"{call.name} isn't a tool: act and look by running a script which calls the SDK", ok=False)
                results.append({"type": "tool_result", "tool_use_id": call.id, "content": [{"type": "text", "text": outcome.text}], "is_error": not outcome.ok})
            if results:
                self._append("user", results)
            if self.ctx.finished or r.error:
                break
            if not calls:  # only wait ends a turn
                self._append("user", [{"type": "text", "text": "Your turn goes on until you call wait(): act with a tool, or wait() once a script is running."}])
                self._append("user", await self._observe())
                continue
            if acted:
                yield Step("agent", "waiting")
                wake = asyncio.ensure_future(self._wake.wait())
                async for s in self._during(wake):
                    yield s
                wake.cancel()
                if self._poke.is_set():
                    continue
                if self.ctx.finished or r.error:
                    break
            self._append("user", await self._observe())
            self._shrink()
        if r.error:
            raise r.error

    async def _during(self, work: asyncio.Future) -> AsyncIterator[Step]:
        """Pass on the runner's steps until work is done or the turn is poked."""
        poke = asyncio.ensure_future(self._poke.wait())
        try:
            while not work.done() and not self._poke.is_set():
                step = asyncio.ensure_future(self._steps.get())
                await asyncio.wait({work, poke, step}, return_when=asyncio.FIRST_COMPLETED)
                if step.done():
                    yield step.result()
                else:
                    step.cancel()
        finally:
            poke.cancel()
        while not self._steps.empty():
            yield self._steps.get_nowait()

    async def _wait_for_player(self, r: Runner) -> None:
        """The player took control (F8): stop the script, make no model calls, and wait until it's given back."""
        await r.interrupt("the player took control")
        g = self.ctx.game
        while g.d.human:
            await asyncio.sleep(0.5)
            try:
                await g.read("state")  # every reply says who has control
            except BridgeError:
                pass
        self._wake.clear()
        self._append("user", [{"type": "text", "text": "PLAYER: I took control for a while and gave it back. Check what changed (time, place, energy, items) before going on."}])

    async def _poked(self) -> bool:
        """Handle a poke. Returns whether to go on: a player's message stops the script and goes on, a stop ends the turn."""
        self._poke.clear()
        if self.stop_reason:
            return False
        await self.runner.interrupt("the PLAYER interrupted")
        return True

    async def _control(self, name: str, args: dict) -> Outcome:
        """Handle run, drop, and wait."""
        if name == "wait":
            if self.runner.idle:  # nothing would wake you
                return Outcome("nothing is running, so nothing would wake you: run a script first", ok=False)
            return Outcome("waiting")
        if name == "drop":
            slot = args.get("slot")
            return Outcome(await self.runner.drop(slot)) if slot in (1, 2, 3) else Outcome("drop takes a slot: 1, 2, or 3", ok=False)
        source, goal = args.get("script"), args.get("goal")
        if not isinstance(source, str) or not isinstance(goal, str):
            return Outcome("run takes script (text), goal (text), and slot (1, 2, 3, or next)", ok=False)
        try:
            done = await self.runner.run(source, goal, args.get("slot", "next"))
        except Rejected as ex:
            return Outcome(f"run rejected, nothing changed: {ex}", ok=False)
        return Outcome(f"{done}\n{self.runner.describe()}")

    def _memory(self, name: str, args: dict) -> Outcome:
        ok, text = self.ctx.memory.use(name, args)
        return Outcome(text, ok)

    def _news(self, full: bool = True) -> list[dict]:
        """Messages (the player's first), what the script did and logged, and why the agent was woken."""
        trace, images, why = self.runner.take()
        parts = []
        if self.inbox:
            self.inbox.sort(key=lambda text: not text.startswith("PLAYER"))
            parts.append("\n".join(self.inbox))
            self.inbox, self._sent = [], []
        if trace:
            parts.append("What happened:\n" + "\n".join(trace))
        if full and self.runner.current is None and (happened := news(self.ctx.game.drain_journal())):
            parts.append(f"Meanwhile:\n{happened}")
        if why:
            parts.append("Woken because: " + "; ".join(why))
        return ([{"type": "text", "text": "\n\n".join(parts)}] if parts else []) + [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": i.data}} for i in images]

    async def _observe(self) -> list[dict]:
        """A wake-up: the news, the farmer and a map of what's around, and what is running."""
        self._wake.clear()
        news = self._news()
        return [*news, {"type": "text", "text": f"Farmer: {await status(self.ctx.game)}\n{await self._backpack()}{await self._map()}{self.runner.describe()}"}]

    async def _backpack(self) -> str:
        try:
            items = await self.ctx.game.inventory()
            free = await self.ctx.game.read("player.free_slots")
        except Exception:
            return ""
        def one(i: dict) -> str:
            extra = f" (water {i['water']}/{i['water_max']})" if i.get("water") is not None else f" x{i['stack']}" if (i.get("stack") or 1) > 1 else ""
            return f"{i['name']}{extra}"
        return f"Backpack ({free} free): {', '.join(one(i) for i in items)}\n"

    async def _map(self) -> str:
        """A text map of the farmer's surroundings, and the named things on it, for every wake-up (the agent wakes rarely, so it's worth the tokens)."""
        try:
            m = await look.tile_map(self.ctx.game, MAP_RADIUS)
        except Exception:  # between locations, or in a menu: the status says enough
            return ""
        nearby = "; ".join(m.get("notable", [])[:15])
        return f"Map of {m['location']} around you:\n{m['map']}\n" + (f"Nearby: {nearby}\n" if nearby else "")

    async def _screenshot(self, args: dict) -> list[dict]:
        radius, grid = args.get("radius"), bool(args.get("grid"))
        try:
            image = await Screen().shot(self.ctx, radius, grid)
        except Exception as ex:
            return [{"type": "text", "text": f"no screenshot: {ex}"}]
        return [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image.data}}, {"type": "text", "text": "the screen now"}]

    async def _web(self, args: dict) -> Outcome:
        """The web tool: a small research subagent answers, so searched pages never enter this context."""
        question = args.get("question")
        if not isinstance(question, str) or not question.strip():
            return Outcome("web takes a question", ok=False)
        if not self.research:
            return Outcome("web lookups are off (no TINYFISH_API_KEY, or --no-web)", ok=False)
        self.usage.web += 1
        try:
            answer = await self.research.answer(question)
        except anthropic.APIError as ex:
            return Outcome(f"the lookup failed: {ex.message[:200]}", ok=False)
        return Outcome(answer)

    # --- internals -----------------------------------------------------------------------------------------

    def _append(self, role: str, content: list) -> None:
        """Add a message, joined to the last if it's from the same side, since the sides must alternate."""
        if self.messages and self.messages[-1]["role"] == role:
            self.messages[-1] = {"role": role, "content": [*self.messages[-1]["content"], *content]}
        else:
            self.messages.append({"role": role, "content": content})

    def _keep(self, response: Any) -> list[dict]:
        """Choose which blocks to send back on later turns."""
        if self.is_claude:
            return [b.model_dump(exclude_none=True) for b in response.content]
        kept: list[dict] = []
        for b in response.content:
            if b.type == "text" and b.text.strip():
                kept.append({"type": "text", "text": b.text})
            elif b.type == "tool_use":
                kept.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input if isinstance(b.input, dict) else {}})
        return kept or [{"type": "text", "text": "(thinking)"}]

    def _check_limits(self) -> None:
        if self.max_turns is not None and self.usage.turns >= self.max_turns:
            raise OutOfBudget(f"{self.usage.turns} turns")

    def _count(self, response: Any, seconds: float) -> None:
        self.usage.turns += 1
        self.usage.input += response.usage.input_tokens or 0
        self.usage.cached += getattr(response.usage, "cache_read_input_tokens", 0) or 0
        self.usage.output += response.usage.output_tokens or 0
        self.usage.seconds += seconds

    async def _send(self) -> Any:
        started = time.monotonic()
        for attempt in range(4):
            try:
                response = await self.client.messages.create(
                    model=self.model, max_tokens=6000, tools=TOOLS, messages=self.messages,
                    system=[{"type": "text", "text": self.system, "cache_control": {"type": "ephemeral"}}],
                    **({"metadata": {"respan_params": {"custom_identifier": self.custom_id}}} if self.custom_id else {}),
                    **({"thinking": {"type": "disabled"}} if self.effort == "off" else {"extra_body": {"output_config": {"effort": self.effort}}}),
                )
            except anthropic.RateLimitError:
                await asyncio.sleep(5 * 2**attempt)
                continue
            except anthropic.BadRequestError as ex:
                raise OutOfBudget(f"the gateway rejected the request: {ex.message[:300]}") from None
            self._count(response, time.monotonic() - started)
            if response.content:
                return response
        raise OutOfBudget("the model kept returning nothing")

    def _trim(self) -> None:
        """Cut what has gone stale, all at once when there's enough of it: every observation but the latest down to its status
        line, and doc results from more than DOCS_KEPT turns ago down to a note. Any edit to an old block makes the rest of the
        context miss the cache for one request, so it waits until the stale part is big enough (TRIM_AT) to pay for that."""
        names = {b["id"]: (b["name"], b.get("input") or {}) for m in self.messages if m["role"] == "assistant" for b in m["content"] if b.get("type") == "tool_use"}
        users = [m for m in self.messages if m["role"] == "user"]
        cuts: list[tuple[dict, str]] = []
        for age, m in enumerate(reversed(users)):
            for b in m["content"]:
                if age and b.get("type") == "text" and b["text"].startswith("Farmer:") and "\n" in b["text"]:
                    cuts.append((b, b["text"].split("\n", 1)[0]))
                elif age > DOCS_KEPT and b.get("type") == "tool_result" and names.get(b["tool_use_id"], ("",))[0] in ("docs", "search_docs"):
                    name, args = names[b["tool_use_id"]]
                    text = "".join(c.get("text", "") for c in b["content"]) if isinstance(b["content"], list) else str(b["content"])
                    if not text.startswith("(read"):
                        what = f'docs("{args.get("topic", "")}")' if name == "docs" else f'search_docs("{args.get("pattern", "")}")'
                        cuts.append((b, f"(read {what} here, {len(text)} characters; read it again if you need it)"))
        stale = sum(len(json.dumps(b)) - len(short) for b, short in cuts)
        if stale < TRIM_AT:
            return
        for b, short in cuts:
            if b.get("type") == "tool_result":
                b["content"] = [{"type": "text", "text": short}]
            else:
                b["text"] = short

    def _shrink(self) -> None:
        """Keep the context small: old screenshots and stale observations go. Done rarely, since it invalidates the prompt cache."""
        blocks = [c for m in self.messages if m["role"] == "user" for b in m["content"] for c in ([b] if b.get("type") != "tool_result" else b["content"] if isinstance(b["content"], list) else [])]
        images = [b for b in blocks if b.get("type") == "image"]
        for block in images[:-2] if len(images) > 6 else []:
            block.clear()
            block.update(type="text", text="(old screenshot removed)")
        self._trim()
