"""Runs the days: the morning report, the night (the planner writes the diary, tidies the memory and plans), resuming a day after a restart, noticing the day end, and bedtime."""

from __future__ import annotations

import asyncio
import dataclasses
import json
from collections.abc import Awaitable, Callable

from . import memory as kv

from . import look
from .brain import Brain, OutOfBudget
from .driver import BridgeError, HumanControl
from .game import ActionError, Game
from .look import STATUS, news, render, status
from .planner import Planner, record
from .script import Context
from .storage import SaveData, day_key


class Director:
    def __init__(self, game: Game, brain: Brain, ctx: Context, *, save: SaveData, planner: Planner | None = None, show: Callable[..., None] = lambda kind, text, data=None, ok=True: None):
        self.game, self.brain, self.ctx, self.planner = game, brain, ctx, planner
        if planner:
            planner.show = self.show
        self.save = save
        self._show = show
        self.after_result: Callable[[], Awaitable[None]] | None = None  # e.g. to draw the map
        self.ended = False  # the day ended without sleep
        self.date: str | None = None
        self._context_failed = False
        ctx.memory = kv.Memory(save.memory_file)
        ctx.before_action = self.wait_for_control  # the player may have taken control

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

    def diary(self, days: int = 7) -> list[dict]:
        """The last few days' summaries, oldest first."""
        file = self.save.diary_file
        return [json.loads(l) for l in file.read_text().splitlines() if l.strip()][-days:] if file.exists() else []

    def write_diary(self, date: str, text: str, summary: str = "") -> None:
        """Keep the planner's diary of the day, ended by the day's numbers as they are, so the coming days remember it."""
        text = f"{text}\nEnd of day: {summary}" if text and summary else text
        if not text:
            return
        self.save.diary_file.parent.mkdir(parents=True, exist_ok=True)
        with self.save.diary_file.open("a") as f:
            f.write(json.dumps({"date": date, "summary": text}) + "\n")
        self.show("notes", f"diary, {date}: {text}")

    def save_context(self, messages: list[dict]) -> None:
        if not self.date:
            return
        try:
            self.save.save_context(self.date, messages, self.brain.usage.turns)
        except (OSError, TypeError, ValueError) as ex:
            if not self._context_failed:
                self._context_failed = True
                self.show("error", f"couldn't save the agent's context: {ex}")

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

    async def play(self, days: int = 0) -> None:
        """Play this many days, or (0) until the program is stopped."""
        played = 0
        while not days or played < days:
            await self.day()
            played += 1

    async def day(self) -> None:
        g = self.game
        await self.wait_for_control()
        self.brain.reset()
        self.ctx.finished = False
        self.ctx.only_tools = None
        await self.night()
        await self._settle_morning()
        date = self.date = await g.date()
        self.brain.custom_id = f"{self.save.folder}-{day_key(date)}"
        self.show("day", date)
        self.show("status", "", {"status": await g.read(STATUS)})  # the view's clock and farmer, before any action
        start = await g.read({"money": "player.money", "skills": "player.skills"})
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
                self.show("wake", "no script is running")
                prompt = f"{await status(g)}\nThe day isn't over. Keep playing, or update your memory and sleep."
        except OutOfBudget as ex:
            self.show("info", f"out of budget for today: {ex}")
        except HumanControl:
            await self.wait_for_control()
            self.show("info", "continuing the day")
            return await self.day()
        finally:
            watch.cancel()
        if self.ended:
            self.show("info", "the farmer passed out")
        elif not self.ctx.finished:
            await self.last_words()
            await self.force_sleep()
        # the day is over once the farmer is in bed: its diary and saves don't wait for the night, which opens the next day
        summary = await self.day_summary(start)
        self.write_diary(date, await self.plan_tomorrow(date, summary), summary)
        self.ctx.memory.save()  # everything reaches disk only now that the day is over, as the game's own save does
        self.save_context(self.brain.messages)
        if self.planner:
            self.planner.save()
        self.show("info", f"usage: {self.brain.usage}", {"usage": dataclasses.asdict(self.brain.usage)})
        self.save.end_day()

    async def plan_tomorrow(self, date: str, summary: str) -> str:
        """The planner's night: it reads the day (what the agent did, the day's numbers, the memory, the farmer's state and
        friendships), edits the memory, and keeps the goals and tomorrow's plan, which the next morning's report carries.
        Returns its diary of the day."""
        if not self.planner:
            return ""
        parts = [f"# The day, as the agent played it\n{record(self.brain.messages) or '(nothing recorded)'}",
                 f"# The day's numbers\n{summary or '(not read)'}"]
        try:
            g = self.game
            stats = await g.read({"money": "player.money", "earned": "player.totalMoneyEarned", "deepest": "player.deepestMineLevel"})
            parts.append(f"# The farmer\n{render(await look.state(g))}\nmoney {stats['money']}g, {stats['earned']}g earned in all, the mines' deepest floor {stats['deepest']}"
                         f"\ninventory:\n{render(await look.items(g), 1)}\nfriendships:\n{render(await look.people(g), 1)}")
        except BridgeError as ex:
            parts.append(f"(the farmer's state couldn't be read: {ex})")
        self.show("info", "the planner is planning tomorrow")
        diary = await self.planner.night(date, "\n\n".join(parts), self.memory)
        self.show("info", f"the planner used {self.planner.tokens:,} tokens")
        return diary

    async def day_summary(self, start: dict) -> str:
        """The day in numbers: money, what was shipped and sold for how much, and level-ups. (The night's shipping total is in the next morning's report.)"""
        try:
            now = await self.game.read({"money": "player.money", "skills": "player.skills"})
        except BridgeError:
            return ""
        parts = [f"money {start['money']}g -> {now['money']}g ({now['money'] - start['money']:+}g)"]
        if self.game.sales:
            parts.append("sales: " + ", ".join(self.game.sales))
            self.game.sales = []
        parts += [f"{skill} level {start['skills'].get(skill, 0)} -> {level}" for skill, level in (now.get("skills") or {}).items()
                  if isinstance(level, int) and level > (start.get("skills") or {}).get(skill, 0)]
        return "; ".join(parts)

    async def night(self) -> None:
        """The night before this day, if it's still on: its screens, and a profession for the agent to choose (in this day's
        fresh context: what it chooses is in this morning's report and so in today's diary)."""
        g = self.game
        try:
            if not await g.read("state.night or menu.type == 'LevelUpMenu' or menu.type == 'ShippingMenu'"):
                return
        except BridgeError:
            return
        await self.settle_choices(await g.finish_night())

    async def opening(self, date: str) -> str:
        """The day's first prompt: the morning report. (A run stopped partway through a day starts it again, as the game does.)"""
        report = await self.report()
        self.show("report", report)
        return f"{report}\n\nStart the farmer on a short first script now (the chores you know); read and plan the rest while it runs."

    async def think(self, prompt: str, turns: int | None = None) -> None:
        """Let the model play, showing what it does; turns caps how many more model turns it gets."""
        if turns:
            self.brain.max_turns = self.brain.usage.turns + turns
        try:
            async for s in self.brain.think(prompt):
                self.show(s.kind, s.text, s.data, s.ok)
                if s.kind == "result" and self.after_result:
                    await self.after_result()
        finally:
            self.brain.max_turns = None

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
            self.show("wake", f"a profession to choose ({result['needs_choice']})")
            options = "\n".join(result["professions"])
            known = "\n\n".join(p for p in (self.planner.morning() if self.planner else "", self.memory.section()) if p)
            await self.think(f"{known}\n\nThe night before the new day: {result['needs_choice']}! Choose a profession, "
                             f"which is permanent:\n{options}\nAnswer by running the script answer(0) or answer(1). The morning report follows.", turns=3)
            if self.ctx.finished:
                return
            if not await self.game.read("menu.type == 'LevelUpMenu' and menu.raw.isProfessionChooser"):
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

    async def report(self) -> str:
        """What the model wakes up to."""
        g = self.game
        overnight = news(g.drain_journal())
        state = await look.state(g)
        parts = [p for p in (self.planner.morning() if self.planner else "", self.memory.section()) if p]
        if week := self.diary():
            parts.append("# The last days (the planner's diary)\n" + "\n".join(f"- {d['date']}: {d['summary']}" for d in week))
        if self.ctx.last_night:
            night = {k: v for k, v in self.ctx.last_night.items() if k in ("notes", "chose")}
            if night.get("notes") or night.get("chose"):
                parts.append("# Last night\n" + render(night))
            self.ctx.last_night = None
        if overnight:
            parts.append("# Since last time\n" + overnight)
        parts.append("# Now\n" + render(state) + "\ninventory:\n" + render(await look.items(g), 1) + f"\n{await status(g)}")
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
            await g.sleep()
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
