"""The planner: a model loop which writes scripts while a runner plays them, and keeps the context small."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

import anthropic

from .decide import GATEWAY
from .game import ActionError
from .look import news, status
from .memory import MAX_KEY, MAX_VALUE
from .script import Context, Outcome, Rejected, Runner, Step

REMEMBER = {"name": "remember", "description": "Keep a fact for future days, by a short dotted key (like shop.pierre.hours, plan.tomorrow, farm.chest); this sets or replaces it. Your memory is all you keep besides an automatic diary; it's shown every morning.",
            "input_schema": {"type": "object", "additionalProperties": False, "required": ["key", "value"], "properties": {
                "key": {"type": "string", "maxLength": MAX_KEY, "description": "A short dotted name"},
                "value": {"type": "string", "maxLength": MAX_VALUE, "description": "The fact, as short text"}}}}
FORGET = {"name": "forget", "description": "Remove a fact from your memory, once it's stale or wrong.",
          "input_schema": {"type": "object", "additionalProperties": False, "required": ["key"], "properties": {"key": {"type": "string", "maxLength": MAX_KEY}}}}
RUN = {"name": "run", "description": "Run a script (Python with the SDK) in the background while you think. It replaces the running script; the running action is stopped unless finish_current is true. You're woken when the script ends, raises, or asks.",
       "input_schema": {"type": "object", "required": ["script", "goal"], "properties": {
           "script": {"type": "string", "description": "Python using the SDK"}, "goal": {"type": "string", "maxLength": 300},
           "stop_if": {"type": "array", "items": {"type": "string"}, "maxItems": 6, "description": "Situations judged when something new is said or shown; a match raises StopIf in the script"},
           "finish_current": {"type": "boolean", "description": "Let the running action finish first"}}}}
WAIT = {"name": "wait", "description": "Let the script run and end your turn. You're woken when it ends or needs you.", "input_schema": {"type": "object", "properties": {}}}
TOOLS = [REMEMBER, FORGET, RUN, WAIT]
SERVER_TOOLS = [
    {"type": "web_search_20260209", "name": "web_search", "max_uses": 3},
    {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 3},
]


class OutOfBudget(Exception):
    """A limit was reached."""


@dataclass
class Limits:
    """Hard caps for one day."""

    turns: int = 60
    output_tokens: int = 60_000
    input_tokens: int = 3_000_000
    lookups: int = 8


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


class Brain:
    def __init__(self, ctx: Context, *, api_key: str, model: str, system: str, limits: Limits | None = None, thinking: bool = True, web: bool = True, max_context: int = 70_000, base_url: str = GATEWAY):
        self.ctx, self.model, self.system = ctx, model, system
        self.limits, self.thinking, self.web, self.max_context = limits or Limits(), thinking, web, max_context
        self.usage = Usage()
        self.messages: list[dict] = []
        self.summary_model: str | None = None
        self.inbox: list[str] = []
        self.stop_reason: str | None = None
        self.on_context: Callable[[list[dict]], None] | None = None  # called with the messages whenever the planner's turn is complete
        self.client = anthropic.AsyncAnthropic(api_key=api_key, base_url=base_url, max_retries=4, timeout=180)
        self.is_claude = "claude" in model
        self.lookups = 0
        self._steps: asyncio.Queue[Step] = asyncio.Queue()
        self._wake = asyncio.Event()  # the runner needs the planner
        self._poke = asyncio.Event()  # the player steered, or the director ended the turn
        self.runner = Runner(ctx, self._steps.put_nowait, self._wake.set)
        if web:
            ctx.lookup = self.lookup

    def reset(self) -> None:
        self.messages.clear()
        self.usage = Usage()
        self.lookups = 0
        self.runner.count = 0

    def tell(self, text: str) -> None:
        """Queue a message for the planner's next wake-up, without interrupting anything."""
        self.inbox.append(text)

    def steer(self, text: str) -> None:
        """Deliver a message now: stop the script and its action, and wake the planner with it."""
        self.inbox.append(text)
        self._poke.set()

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
            self._check_limits()
            if self._poke.is_set():
                if not await self._poked():
                    return
                yield Step("info", "stopped the script for your message")
                self._append("user", await self._observe())
            yield Step("planner", "thinking")
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
            yield Step("planner", "done")
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
                    yield Step("notes", outcome.text if outcome.ok else f"{call.name} refused: {outcome.text}", outcome.ok, {"key": args.get("key"), "value": args.get("value")})
                elif call.name in ("run", "wait"):
                    outcome = await self._control(call.name, args)
                    acted |= outcome.ok
                    yield Step("plan", outcome.text, outcome.ok, {"script": args.get("script"), "goal": args.get("goal")} if call.name == "run" else {})
                else:
                    outcome = Outcome(f"{call.name} isn't a tool: act and look by running a script which calls the SDK", ok=False)
                results.append({"type": "tool_result", "tool_use_id": call.id, "content": [{"type": "text", "text": outcome.text}], "is_error": not outcome.ok})
            if results:
                self._append("user", results)
            if self.ctx.finished or r.error:
                break
            if acted or not calls:
                if not calls and r.idle and not self._wake.is_set() and not self.inbox:
                    return
                yield Step("planner", "waiting")
                wake = asyncio.ensure_future(self._wake.wait())
                async for s in self._during(wake):
                    yield s
                wake.cancel()
                if self._poke.is_set():
                    continue
                if self.ctx.finished or r.error:
                    break
            self._append("user", await self._observe())
            await self._shrink(response)
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

    async def _poked(self) -> bool:
        """Handle a poke. Returns whether to go on: a player's message stops the script and goes on, a stop ends the turn."""
        self._poke.clear()
        if self.stop_reason:
            return False
        await self.runner.interrupt("the PLAYER interrupted")
        return True

    async def _control(self, name: str, args: dict) -> Outcome:
        """Handle run and wait."""
        if name == "wait":
            return Outcome("waiting" if not self.runner.idle else "nothing is running; the farmer keeps busy nearby")
        source, goal, stop_if = args.get("script"), args.get("goal"), args.get("stop_if") or []
        if not isinstance(source, str) or not isinstance(goal, str) or not isinstance(stop_if, list) or not all(isinstance(s, str) for s in stop_if):
            return Outcome("run takes script (text), goal (text), stop_if (a list of situations), and finish_current", ok=False)
        try:
            started = await self.runner.run(source, goal, stop_if, bool(args.get("finish_current")))
        except Rejected as ex:
            return Outcome(f"run rejected, nothing changed: {ex}", ok=False)
        return Outcome(started)

    def _memory(self, name: str, args: dict) -> Outcome:
        """Handle remember and forget."""
        memory = self.ctx.memory
        if name == "remember":
            if set(args) != {"key", "value"}:
                return Outcome("remember takes key (a short dotted name) and value (short text)", ok=False)
            ok, text = memory.remember(args["key"], args["value"])
        else:
            if set(args) != {"key"}:
                return Outcome("forget takes key", ok=False)
            ok, text = memory.forget(args["key"])
        return Outcome(text, ok)

    def _news(self, full: bool = True) -> list[dict]:
        """Messages (the player's first), what the script did and logged, and why the planner was woken."""
        trace, images, why = self.runner.take()
        parts = []
        if self.inbox:
            self.inbox.sort(key=lambda text: not text.startswith("PLAYER"))
            parts.append("\n".join(self.inbox))
            self.inbox = []
        if trace:
            parts.append("What happened:\n" + "\n".join(trace))
        if full and self.runner.current is None and (happened := news(self.ctx.game.drain_journal())):
            parts.append(f"Meanwhile:\n{happened}")
        if why:
            parts.append("Woken because: " + "; ".join(why))
        return ([{"type": "text", "text": "\n\n".join(parts)}] if parts else []) + [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": i.data}} for i in images]

    async def _observe(self) -> list[dict]:
        """A wake-up: the news, the status, and what is running."""
        self._wake.clear()
        news = self._news()
        return [*news, {"type": "text", "text": f"{await status(self.ctx.game)}\n{self.runner.describe()}"}]

    async def summarize(self, prompt: str, max_tokens: int = 1500) -> str:
        """Summarize the conversation so far, with the summary model (the main model by default, whose cache makes it cheapest)."""
        return await self.ask(prompt, max_tokens, model=self.summary_model)

    async def ask(self, prompt: str, max_tokens: int = 1500, model: str | None = None) -> str:
        """Get a plain answer without tools."""
        model = model or self.model
        messages = self._join(self.messages, "user", [{"type": "text", "text": prompt}])
        response = await self.client.messages.create(model=model, max_tokens=max_tokens, system=self.system, messages=messages, **({} if "claude" in model else {"thinking": {"type": "disabled"}}))
        self._count(response, 0)
        return "\n".join(b.text for b in response.content if b.type == "text").strip()

    async def lookup(self, question: str) -> str:
        """Answer a question from the web. This is a separate conversation, since searched pages would flood this one."""
        if self.lookups >= self.limits.lookups:
            raise ActionError(f"no lookups left today (limit {self.limits.lookups})")
        self.lookups += 1
        self.usage.web += 1
        prompt = f"Answer this question about the game Stardew Valley (version 1.6): {question}\n\nSearch, preferring stardewvalleywiki.com, and fetch the page if the search result isn't enough. Reply with only the facts which answer it, under 120 words. If you can't find it, say so."
        try:
            response = await self.client.messages.create(model=self.model, max_tokens=3000, tools=SERVER_TOOLS, messages=[{"role": "user", "content": prompt}])
        except anthropic.APIError as ex:
            raise ActionError(f"the lookup failed: {ex.message[:200]}") from None
        self._count(response, 0)
        self.usage.turns -= 1
        return "\n".join(b.text for b in response.content if b.type == "text").strip() or "nothing found"

    # --- internals -----------------------------------------------------------------------------------------

    def _append(self, role: str, content: list) -> None:
        self.messages = self._join(self.messages, role, content)
        if role == "user" and self.on_context:  # every tool use has its result: a context which can be resumed
            self.on_context(self.messages)

    @staticmethod
    def _join(messages: list[dict], role: str, content: list) -> list[dict]:
        """The messages and one more, joined to the last if it's from the same side, since the sides must alternate."""
        if messages and messages[-1]["role"] == role:
            return [*messages[:-1], {"role": role, "content": [*messages[-1]["content"], *content]}]
        return [*messages, {"role": role, "content": content}]

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
        u, l = self.usage, self.limits
        if u.turns >= l.turns:
            raise OutOfBudget(f"{u.turns} turns today")
        if u.output >= l.output_tokens:
            raise OutOfBudget(f"{u.output:,} output tokens today")
        if u.input + u.cached >= l.input_tokens:
            raise OutOfBudget(f"{u.input + u.cached:,} input tokens today")

    def _count(self, response: Any, seconds: float) -> None:
        self.usage.turns += 1
        self.usage.input += response.usage.input_tokens or 0
        self.usage.cached += getattr(response.usage, "cache_read_input_tokens", 0) or 0
        self.usage.output += response.usage.output_tokens or 0
        self.usage.seconds += seconds

    async def _send(self) -> Any:
        started = time.monotonic()
        options: dict[str, Any] = {}
        if not self.thinking and not self.is_claude:
            options["thinking"] = {"type": "disabled"}
        for attempt in range(4):
            try:
                response = await self.client.messages.create(
                    model=self.model, max_tokens=6000, tools=TOOLS, messages=self.messages,
                    system=[{"type": "text", "text": self.system, "cache_control": {"type": "ephemeral"}}], **options,
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

    async def _shrink(self, response: Any) -> None:
        """Keep the context small. Done rarely, since it invalidates the prompt cache."""
        blocks = [c for m in self.messages if m["role"] == "user" for b in m["content"] for c in ([b] if b.get("type") != "tool_result" else b["content"] if isinstance(b["content"], list) else [])]
        images = [b for b in blocks if b.get("type") == "image"]
        for block in images[:-2] if len(images) > 6 else []:
            block.clear()
            block.update(type="text", text="(old screenshot removed)")
        used = (response.usage.input_tokens or 0) + (getattr(response.usage, "cache_read_input_tokens", 0) or 0)
        if used > self.max_context:
            summary = await self.summarize("Summarize this day so far for yourself: what you did, what you learned, positions that matter, and your plan for the rest of the day. Under 300 words.")
            opening, latest = self.messages[0]["content"][0], [b for b in self.messages[-1]["content"] if b.get("type") == "text"]
            self.messages = [{"role": "user", "content": [opening, {"type": "text", "text": f"(Earlier turns were summarized.)\n{summary}\n\nContinue the day. The latest:"}, *latest]}]
