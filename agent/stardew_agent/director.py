"""Runs the days: the morning report, memory and the diary, resuming a day after a restart, noticing the day end, and bedtime."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path

from . import memory as kv

from . import look
from .brain import Brain, OutOfBudget
from .driver import BridgeError, HumanControl
from .game import ActionError, Game
from .look import news, render, status
from .script import Context
from .storage import SaveData

DIARY = ("The day is over. In under 120 words, summarize it for your future self: what you did and earned, what you learned "
         "(prices, places, people, what worked or failed), and what's unfinished. Plain text, no headings.")


class Director:
    def __init__(self, game: Game, brain: Brain, ctx: Context, *, goal: str, save: SaveData, show: Callable[..., None] = lambda kind, text, data=None, ok=True: None,
                 memory_model: str | None = "hy4-flash", verbose: bool = False):
        self.game, self.brain, self.ctx, self.goal = game, brain, ctx, goal
        self.save, self.memory_model, self.verbose = save, memory_model, verbose
        self._show = show
        self.after_result: Callable[[], Awaitable[None]] | None = None  # e.g. to draw the map
        self.ended = False  # the day ended without sleep
        self.date: str | None = None
        self.resumable = True  # the first day played may continue a day the last run didn't finish
        self._context_failed = False
        ctx.memory = kv.Memory(save.memory_file)
        ctx.before_action = self.wait_for_control  # the player may have taken control
        brain.on_context = self.save_context

    def show(self, kind: str, text: str, data: dict | None = None, ok: bool = True) -> None:
        """Show a step and add it to the day's transcript."""
        try:
            self.save.record(kind, text, data, ok)
        except OSError:
            pass  # a full disk mustn't stop play
        self._show(kind, text, data, ok)

    # --- memory, the diary, the context ------------------------------------------------------------------

    @property
    def memory(self) -> kv.Memory:
        return self.ctx.memory

    @property
    def diary_file(self) -> Path:
        return self.save.diary_file

    def diary(self, days: int = 7) -> list[dict]:
        """The last few days' summaries, oldest first."""
        if not self.diary_file.exists():
            return []
        lines = [json.loads(l) for l in self.diary_file.read_text().splitlines() if l.strip()]
        return lines[-days:]

    async def write_diary(self, date: str) -> str:
        """Summarize the day while its context is still here, so the coming days remember it."""
        try:
            text = await self.brain.summarize(DIARY)
        except Exception as ex:  # a missing summary must never stop the night
            self.show("error", f"couldn't write the diary: {ex}")
            return ""
        if text:
            self.diary_file.parent.mkdir(parents=True, exist_ok=True)
            with self.diary_file.open("a") as f:
                f.write(json.dumps({"date": date, "summary": text}) + "\n")
            self.show("notes", f"diary, {date}: {text}")
        return text

    async def consolidate(self, date: str, diary: str) -> None:
        """Tidy the memory overnight with a small model; anything doubtful keeps the memory as it was."""
        if not self.memory_model:
            return
        if not diary:
            self.show("info", "memory not consolidated: no diary today")
            return
        old = dict(self.memory.facts)
        try:
            change = await kv.consolidate(self.memory, date, diary, self.brain.client, self.memory_model)
        except kv.Rejected as ex:
            self.show("info", f"memory kept as it was: {ex}")
            return
        except Exception as ex:  # the gateway may be down; the night goes on
            self.show("info", f"memory kept as it was: the consolidation failed ({str(ex)[:200]})")
            return
        self.show("notes", str(change), {"changes": change.details(old, self.memory.facts)} if self.verbose else {})

    def save_context(self, messages: list[dict]) -> None:
        if not self.date:
            return
        try:
            self.save.save_context(self.date, messages, self.brain.usage.turns)
        except (OSError, TypeError, ValueError) as ex:
            if not self._context_failed:
                self._context_failed = True
                self.show("error", f"couldn't save the planner's context: {ex}")

    # --- the player ----------------------------------------------------------------------------------------

    async def wait_for_control(self) -> None:
        if not await self.game.read("state") or not self.game.d.human:
            return
        self.show("info", "you have control; press F8 in the game to give it back")
        while self.game.d.human:
            await asyncio.sleep(1)
            await self.game.read("state")
        self.brain.tell("PLAYER: I took control for a while and gave it back. Check what changed.")

    # --- days ----------------------------------------------------------------------------------------------

    async def play(self, days: int) -> None:
        for i in range(days):
            await self.day()
            if i + 1 < days:
                self.show("info", "")

    async def day(self) -> None:
        g = self.game
        await self.wait_for_control()
        self.brain.reset()
        self.ctx.finished = False
        self.ctx.only_tools = None
        await self._settle_morning()
        date = self.date = await g.date()
        self.show("day", date)
        prompt = await self.opening(date)
        today = await g.read("time.total")
        self.ended = False
        nudges = 0
        watch = asyncio.create_task(self.watch(today))
        try:
            while not self.ctx.finished and not self.ended:
                await self.think(prompt)
                if self.ctx.finished or self.ended:
                    break
                nudges += 1
                if nudges > 3:
                    break
                prompt = f"{await status(g)}\nThe day isn't over. Keep playing, or update your memory and sleep."
        except OutOfBudget as ex:
            self.show("info", f"out of budget for today: {ex}")
        except HumanControl:
            await self.wait_for_control()
            self.show("info", "continuing the day")
            return await self.day()
        finally:
            watch.cancel()
        if not self.ended and not self.ctx.finished:
            await self.last_words()
        await self.consolidate(date, await self.write_diary(date))
        if self.ended:
            self.show("info", "the farmer passed out")
            await self.settle_choices(await self.game.finish_night(today))
        elif not self.ctx.finished:
            await self.force_sleep()
        self.show("info", f"usage: {self.brain.usage}")

    async def opening(self, date: str) -> str:
        """The day's first prompt: the morning report, or, when the last run stopped partway through this same day, its saved context and a note to continue."""
        saved = self.save.load_context(date) if self.resumable else None
        self.resumable = False
        if saved:
            self.brain.messages = saved["messages"]
            self.brain.usage.turns = int(saved.get("turns") or 0)
            self.show("info", f"resuming today from the saved context ({len(saved['messages'])} messages)")
            report = await self.report(resumed=True)
            self.show("report", report)
            return report
        report = await self.report()
        self.show("report", report)
        return f"{report}\n\nPlan the day, then play it."

    async def think(self, prompt: str, turns: int | None = None) -> None:
        """Let the model play, showing what it does; turns caps how many more model turns it gets."""
        if turns:
            self.brain.limits.turns = self.brain.usage.turns + turns
        async for s in self.brain.think(prompt):
            self.show(s.kind, s.text, s.data, s.ok)
            if s.kind == "result" and self.after_result:
                await self.after_result()

    async def watch(self, today: int) -> None:
        """While the day is played, notice it ending without sleep (passing out)."""
        while not self.ctx.finished:
            await asyncio.sleep(2)
            job = self.brain.runner.current
            if job and job.name == "sleep":
                continue  # going to bed ends the day on purpose
            try:
                t = await self.game.read({"total": "time.total", "night": "state.night", "out": "state.passed_out"})
            except BridgeError:
                continue
            if not self.ctx.finished and (t["total"] != today or t["night"] or t["out"]):
                self.ended = True
                self.brain.interrupt("the day ended")
                return

    async def last_words(self) -> None:
        """Before the director ends a day, let the model update its memory."""
        self.ctx.only_tools = {"remember", "forget"}
        try:
            await self.think("You're out of turns for today, so the farmer will be put to bed now. Before that, update your memory: "
                             "remember what you learned today and your plan for tomorrow, and forget what's stale. Only remember and forget work now.", turns=3)
        except OutOfBudget:
            pass
        finally:
            self.ctx.only_tools = None

    async def settle_choices(self, result: dict) -> None:
        """Ask the model about anything the night screens need decided."""
        self.ctx.last_night = result
        for _ in range(3):
            if not result.get("needs_choice"):
                return
            self.ctx.finished = False
            options = "\n".join(result["professions"])
            await self.think(f"{result['needs_choice']}! Choose a profession, which is permanent:\n{options}\nAnswer by running the script answer(0) or answer(1).", turns=3)
            if self.ctx.finished:
                return
            if await self.game.read("menu.type == 'LevelUpMenu' and menu.raw.isProfessionChooser"):
                continue
            return
        self.show("info", "the model didn't choose a profession; leaving the choice open in the game")

    async def _settle_morning(self) -> None:
        for _ in range(5):
            try:
                await self.game.ready()
                return
            except ActionError:
                # the game may be paused in its own menu, where we left it
                if await self.game.read("menu.type == 'LetterViewerMenu' or menu.root == 'GameMenu'"):
                    await self.game.close_menu()
                else:
                    return  # the model will see it in the report

    async def report(self, resumed: bool = False) -> str:
        """What the model wakes up to; resumed: after the program restarted partway through the day."""
        g = self.game
        overnight = news(g.drain_journal())
        state = await look.state(g)
        items = await look.items(g)
        if resumed:
            return "\n\n".join([
                "The agent program was restarted partway through today. Your turns so far today are above; any script that was running "
                "was stopped. Check where you are and continue the day.", self.memory.section(),
                *(["# Since then\n" + overnight] if overnight else []),
                "# Now\n" + render(state) + "\ninventory:\n" + render(items["items"], 1) + f"\n{await status(g)}"])
        parts = [f"GOAL: {self.goal}", self.memory.section()]
        if week := self.diary():
            parts.append("# The last days (automatic summaries)\n" + "\n".join(f"- {d['date']}: {d['summary']}" for d in week))
        if self.ctx.last_night:
            night = {k: v for k, v in self.ctx.last_night.items() if k in ("notes", "chose")}
            if night.get("notes") or night.get("chose"):
                parts.append("# Last night\n" + render(night))
            self.ctx.last_night = None
        if overnight:
            parts.append("# Since last time\n" + overnight)
        parts.append("# Now\n" + render(state) + "\ninventory:\n" + render(items["items"], 1) + f"\n{await status(g)}")
        return "\n\n".join(parts)

    async def force_sleep(self) -> None:
        self.show("info", "putting the farmer to bed")
        g = self.game
        try:
            if await g.read("state.menu"):
                try:
                    await g.ready()
                except ActionError:
                    if await g.read("menu.type") not in ("LevelUpMenu", "ShippingMenu", "DialogueBox"):
                        await g.close_menu()
            result = await g.sleep()
            await self.settle_choices(result)
        except ActionError as ex:
            self.show("info", f"couldn't get to bed ({ex}); waiting for the day to end")
            day = await g.read("time.total")
            for _ in range(60):
                await g.step(6000, {"next": f"time.total != {day} and state.free", "menu": "state.menu", "night": "state.night"})
                if await g.read("state.night") or await g.read("menu.type") in ("LevelUpMenu", "ShippingMenu"):
                    await g.finish_night(day)
                if await g.read("time.total") != day:
                    break
                if await g.read("state.menu"):
                    await g.auto_close() or await g.press("Escape", after=20)
        self.ctx.finished = True
