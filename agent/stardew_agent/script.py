"""Plans as Python scripts: each runs in WebAssembly in its own process (wasm_host.py, sandbox_child.py), and the runner here carries out its calls to the API (api/) in the game."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
import signal
import sys
import time
from collections import Counter, deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import api, combat, wasm_host
from .api.base import Api, record
from .driver import BridgeError, Disconnected, HumanControl
from .game import ActionError, Game, Interrupted
from .memory import Memory
from .display import action_lines
from .look import STATUS, Image, minutes, news, render, status, until_2am
from .sandbox_child import ActionFailed, Closed, Cutscene, LowEnergy, LowHealth, Stuck, NotEnough, Question, Surprise, TimeToGoHome, Unreachable, Unsure

SURE = 0.6  # the judge's least confidence for an answer to count
MAX_MESSAGE = 1 << 20  # bytes in one message from a script
LIMITS = {"fuel": 100_000_000_000, "memory": 256 << 20}  # per script: WebAssembly computation (Python's start takes about 3 billion, a second of work about 10) and memory
MAX_SOURCE = 20_000  # characters
MAX_SECONDS = 30 * 60  # real time per script
MAX_LOGS = 200  # log lines per script
WAKE_EVERY = 30  # seconds: a script's messages wake the agent at most this often; meanwhile they wait in the queue
GATHER = 1.0  # seconds a message waits for more, before it wakes the agent
QUEUE = 3  # script slots: the running one, and two waiting their turn
STUCK_AFTER, STUCK_EVERY, SAMPLE = 10, 5, 2  # seconds: an action is first checked after this, then this often; positions every SAMPLE
STUCK_SURE = 0.75  # how sure the judge must be, twice in a row: a false alarm costs a agent turn
STUCK = ["the farmer is making progress: moving somewhere new, using energy on work, getting or selling items, or doing something that stays in one place by nature (fishing, fighting, sleeping, soaking, waiting, talking)",
         "the farmer is stuck: standing or walking in place without getting anywhere, the same action failing again and again, "
         "or going round in a loop (the same few actions or places over and over without anything changing)"]
MAX_TEXT = 500  # characters kept of anything else the script's process says
MAX_LOG_TEXT = 6000  # characters kept of one log line, enough for a map
MAX_LOG_CHARS = 40_000  # logged characters per script
HEARD = {"bubble", "chat", "dialogue", "event", "letter", "level", "message", "question"}  # journal entries the judge is shown as recent events
LOW_HEALTH = 0.3  # of the maximum
LOW_ENERGY = 15
HOME_SLACK = 15  # game minutes to spare, arriving home by midnight


@dataclass
class Context:
    """What the agent's tools, the runner, and the SDK share."""

    game: Game
    memory: Memory = field(default_factory=Memory)  # the agent's lasting facts (remember, forget)
    before_action: Callable[[], Awaitable[None]] | None = None
    shot: dict = field(default_factory=lambda: {"scale": 1.0, "cropped": False})  # the last screenshot's scale, for clicks at its pixels
    finished: bool = False  # the farmer slept: the day is over
    last_night: dict | None = None  # what the night screens showed
    only_tools: set[str] | None = None  # when set, the agent's other tools are refused
    while_idle: Callable[[asyncio.Event], Awaitable[dict]] | None = None  # work done while no script runs, until the event is set
    decide: Callable[[dict, dict[str, str]], Awaitable[tuple[str | None, float]]] | None = None  # the judge for check and pick


@dataclass
class Step:
    """Something that happened, for display."""

    kind: str  # think, say, call, plan, result, log, judge, safety, busy, info
    text: str
    ok: bool = True
    data: dict = field(default_factory=dict)  # for display: an action's name, arguments, result, journal, and status


@dataclass
class Outcome:
    """What the agent reads of an action or a tool call."""

    text: str
    ok: bool = True
    shown: dict = field(default_factory=dict)  # what the display needs


class Rejected(Exception):
    """A script which won't be run."""


class Stopped(BaseException):
    """The player steered, a new script replaced this one, or the day ended: the script's process is killed."""


class Kill(Exception):
    """The script's process broke a rule, and is ended for this reason."""


def vet(source: str) -> None:
    """Refuse a script which is too long or isn't Python; everything else is up to the sandbox."""
    if len(source) > MAX_SOURCE:
        raise Rejected(f"the script is longer than {MAX_SOURCE} characters")
    try:
        compile(source, "<script>", "exec")
    except SyntaxError as ex:
        raise Rejected(f"line {ex.lineno}: SyntaxError: {ex.msg}") from None
    except ValueError as ex:
        raise Rejected(f"the script can't be compiled: {ex}") from None


# --- errors as the script's exceptions ---------------------------------------------------------------------


FAILURES = [
    (Closed, r"\bclosed\b|\blocked\b|festival|isn't around today|didn't open|no shop is open"),
    (NotEnough, r"inventory is (too )?full|free inventory slot|but you have \d+g|you don't have|missing ingredients|out of energy|isn't in there|isn't in your backpack|isn't in the chest|is empty|only \d+ of|is there room|no tilled, empty soil"),
    (Unreachable, r"couldn't reach|no walking route|couldn't get to|lost sight|couldn't catch up|gave up travelling|blocked|can't reach|can't get beside|no walkable path"),
]


def failure(reason: str) -> ActionFailed:
    """The exception for an action's error."""
    reason = reason.split("\n")[0]
    for cls, pattern in FAILURES:
        if re.search(pattern, reason, re.I):
            return cls(reason)
    return ActionFailed(reason)


def interruption(what: str, choices: list[str] | None) -> Surprise:
    """The exception for something which needs a decision before the action can go on."""
    what = what.split("\n")[0]
    if "cutscene" in what or "minigame" in what:
        return Cutscene(what)
    if not choices and re.search(r"\benergy is\b", what):  # a skill's own stop (mine, fight, walking), not a question
        return LowEnergy(what)
    if not choices and re.search(r"\bhealth is\b|\bretreating\b|\blow health\b", what):
        return LowHealth(what)
    return Question(what, choices or [])


# --- facts and the judge -----------------------------------------------------------------------------------


def stuck_facts(done: list[tuple[str, str, str]], samples: list[dict], now: float) -> dict:
    """What the stuck check sees: recent actions and positions with their times, and counts worked out for it."""
    recent = [x for x in samples if now - x["at"] <= 20]
    before = {(x["place"], x["tile"]) for x in samples if now - x["at"] > 20}
    steps = sum(abs(a["tile"][0] - b["tile"][0]) + abs(a["tile"][1] - b["tile"][1]) for a, b in zip(recent, recent[1:]) if a["place"] == b["place"])
    still = 0.0
    for x in reversed(samples):
        if (x["place"], x["tile"]) != (samples[-1]["place"], samples[-1]["tile"]):
            break
        still = now - x["at"]
    kinds = Counter(action for _, action, _ in done)
    places = Counter((x["place"], x["tile"]) for x in samples)
    return {
        "recent_actions": [f"{t} {action}: {how}" for t, action, how in done],
        "positions": [f"{x['clock']} {x['place']} {x['tile']} energy {x['energy']} money {x['money']} ({x['action']})" for x in samples],
        "tiles_walked_last_20s": steps, "new_tiles_last_20s": len({(x["place"], x["tile"]) for x in recent} - before), "seconds_on_this_tile": round(still),
        "energy_used_last_20s": (recent[0]["energy"] - recent[-1]["energy"]) if recent else 0,
        "money_change_last_20s": (recent[-1]["money"] - recent[0]["money"]) if recent else 0,
        "failures_in_recent_actions": sum(1 for _, _, how in done if how.startswith("failed")),
        "most_repeated_action": f"{kinds.most_common(1)[0][0]} x{kinds.most_common(1)[0][1]}" if kinds else "none",
        "most_revisited_tile": f"{places.most_common(1)[0][0]} x{places.most_common(1)[0][1]}" if places else "none",
    }


def facts(t: dict, s: dict) -> dict:
    """Ready-made, comparable facts for the judge: it compares well but can't do arithmetic."""
    weather = str(t.get("weather") or "")
    return {
        "time": t.get("text"), "minutes_until_2am": until_2am(t["clock"]), "day": f"{t.get('weekday')} {t.get('season')} {t.get('day')}",
        "weather": weather, "raining": "yes" if any(w in weather.lower() for w in ("rain", "storm")) else "no", "location": s["place"],
        "health": f"{s['hp']}/{s['maxh']}", "health_percent": round(100 * s["hp"] / max(1, s["maxh"])),
        "energy": f"{int(s['energy'])}/{s['maxe']}", "energy_percent": round(100 * s["energy"] / max(1, s["maxe"])),
        "money": s["money"], "free_slots": s["free"], "backpack_full": "yes" if s["free"] == 0 else "no", "holding": (s.get("held") or {}).get("name"),
    }


FACTS_READ = {**STATUS, "time": "time", "held": "player.held"}
NONE = "None of these: something else is the case, or it isn't clear."  # the judge's way out


# --- what crosses to and from the script -------------------------------------------------------------------


def wire(value: Any) -> Any:
    return value.wire() if isinstance(value, Api) else str(value)


def described(option: Any) -> str:
    return option if isinstance(option, str) else str(option) if isinstance(option, Api) else json.dumps(option, default=str)


def text_of(value: Any, limit: int = MAX_TEXT) -> str:
    """Text from the script's process, which is untrusted: a string, and short."""
    text = value if isinstance(value, str) else json.dumps(value, default=str)[:limit * 2]
    return text[:limit] + (" ..." if len(text) > limit else "")


def line_of(message: dict, last: int | None) -> int | None:
    line = message.get("line")
    return line if isinstance(line, int) and not isinstance(line, bool) and 0 < line < 100_000 else last


# --- the sandbox's process ---------------------------------------------------------------------------------


_COMMAND: list[str] | None = None  # tests put a fake script process here


CHILD = wasm_host.CHILD.read_text()  # the sandbox's code as this session started: later edits to the file can't reach a running session
API = api.spec()  # what the sandbox builds a script's globals from


def command(limits: dict) -> list[str]:
    """How to start a script's process: CPython for WASI under wasmtime. There's no other way, so without it scripts don't run."""
    if _COMMAND:
        return _COMMAND
    if why := wasm_host.missing():
        raise Rejected(f"scripts can't run on this computer yet: {why}")
    return [sys.executable, "-I", str(Path(wasm_host.__file__)), str(limits["fuel"]), str(limits["memory"]), CHILD]


def death(code: int | None, stderr: bytes) -> str:
    """Why a script's process ended without saying so."""
    if code == wasm_host.FUEL:
        return "the script used too much computation (a busy loop?)"
    if code in (-signal.SIGKILL, 128 + signal.SIGKILL):
        return "it was killed"
    tail = stderr.decode(errors="replace").strip().splitlines()[-1:] or [f"exit code {code}"]
    return f"its process ended: {tail[0][:300]}"


# --- running -----------------------------------------------------------------------------------------------


@dataclass
class Job:
    """One action being run."""

    id: int
    name: str
    args: dict
    started: float = 0

    def __str__(self) -> str:
        return f"#{self.id} {self.name}"

    @property
    def label(self) -> str:
        text = " ".join(f"{k}={v if isinstance(v, str) else json.dumps(v, default=str)}" for k, v in self.args.items() if v is not None)
        return f"#{self.id} {self.name} {text[:120]}".strip()


@dataclass
class Script:
    """One run of a script, in its own process."""

    source: str
    goal: str
    proc: Any = None
    task: asyncio.Task | None = None
    started: float = 0
    logged: int = 0  # lines
    chars: int = 0  # logged characters
    line: int = 0
    stopped: str | None = None
    outcome: tuple = ()  # how it ended: done, error, killed, or stopped
    done: asyncio.Event = field(default_factory=asyncio.Event)
    query: bool = False  # a read-only script beside the running one: its prints are its answer
    output: list[str] = field(default_factory=list)

    def stop(self, why: str) -> Stopped:
        """Mark it stopped on purpose; returns the Stopped to raise."""
        self.stopped, self.outcome = why, ("stopped", why)
        return Stopped(why)


class Runner:
    """Plays scripts in sandboxed processes, one action at a time, keeps the farmer safe and busy, and wakes the agent when a script ends."""

    def __init__(self, ctx: Context, emit: Callable[[Step], None], wake: Callable[[], None], poll: float = 2):
        self.ctx, self.emit, self.on_wake, self.poll = ctx, emit, wake, poll
        self.limits, self.max_seconds = dict(LIMITS), MAX_SECONDS
        self.script: Script | None = None
        self.current: Job | None = None
        self.count = 0
        self.error: BaseException | None = None  # the player took control, or the game went away
        self._lock = asyncio.Lock()  # one action at a time, across scripts
        self._owner: Script | None = None  # whose action is running
        self._trace: list[str] = []  # what the agent hasn't been told yet
        self._images: list[Image] = []
        self._why: list[str] = []
        self._heard: deque[str] = deque(maxlen=8)
        self._last = ("(none yet)", "")
        self._warned: set[str] = set()  # safety warnings given, until they clear
        self._home: dict = {}  # the last estimate of the walk home
        self._changed = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._job: asyncio.Task | None = None
        self._cancel: BaseException | None = None  # what a cancelled action raises
        self._listening = False
        self._woken_for_messages = -WAKE_EVERY  # when messages last woke the agent
        self._done: deque[tuple[str, str, str]] = deque(maxlen=12)  # the last actions, for the stuck check: (game time, action, how it went)
        self._finished: deque[float] = deque(maxlen=4)  # when the last few actions ended, to tell a run of short ones
        self._samples: deque[dict] = deque(maxlen=15)  # every SAMPLE seconds during an action: game time, place, tile, energy, money, action
        self._sampled = self._asked = 0.0  # when the farmer was last sampled, and the stuck check last asked
        self._strikes = 0  # the stuck check's "stuck" verdicts in a row
        self._queued = False  # messages the agent hasn't been woken for
        self.waiting: list[tuple[str, str]] = []  # scripts in slots 2 and 3: (source, goal)
        self.printed: list[tuple[float, str]] = []  # what the script printed (and when), until the agent reads it
        self._message_timer: asyncio.TimerHandle | None = None

    # --- control -------------------------------------------------------------------------------------------

    @property
    def idle(self) -> bool:
        return self.script is None and self.current is None

    def start(self) -> None:
        if not self._listening and self.ctx.game is not None:
            self.ctx.game.d.on_journal.append(self.hear)
            self._listening = True
        if not self._task or self._task.done():
            self.error = None
            self._task = asyncio.create_task(self._main())

    async def run(self, source: str, goal: str, slot: int | str = "next") -> str:
        """Put a script in a slot: 1 is the running one (replaced now), 2 and 3 wait their turn, "next" appends (or starts it, if
        nothing runs). Raises Rejected for a script which won't run, or a slot which can't take it."""
        vet(source)
        if slot in (1, "1", "now"):
            return await self._start(source, goal)
        if slot in (2, 3, "2", "3"):
            i = int(slot) - 2
            if i > len(self.waiting):
                raise Rejected(f"slot {i + 1} is empty, so it can't take slot {i + 2}: use slot {len(self.waiting) + 2} or \"next\"")
            if i < len(self.waiting):
                self.waiting[i] = (source, goal)
                return f"replaced slot {i + 2}"
            slot = "next"
        if slot != "next":
            raise Rejected('slot is 1, 2, 3, or "next"')
        if self.script is None:  # nothing runs: it starts now
            return await self._start(source, goal)
        if len(self.waiting) >= QUEUE - 1:
            raise Rejected(f"the queue is full ({QUEUE} slots: the running script and {QUEUE - 1} waiting): drop one, or replace a slot")
        self.waiting.append((source, goal))
        return f"queued in slot {len(self.waiting) + 1}"

    async def drop(self, slot: int) -> str:
        """Delete a slot: 1 stops the running script (the next one starts), 2 and 3 delete what waits there."""
        if slot == 1:
            if not self.script:
                return "slot 1 is empty: nothing is running"
            await self._stop_script(self.script, "you dropped it")
            return "stopped slot 1; " + (await self._start_next() if self.waiting else "nothing waits")
        if slot not in (2, 3) or slot - 2 >= len(self.waiting):
            return f"slot {slot} is empty"
        _, goal = self.waiting.pop(slot - 2)
        return f"dropped slot {slot} ({goal})"

    async def _start_next(self) -> str:
        source, goal = self.waiting.pop(0)
        started = await self._start(source, goal)
        if not self.waiting:
            self.wake("the last queued script started: queue what comes next")
        return f"slot 1 now: {goal} ({started})"

    async def _start(self, source: str, goal: str) -> str:
        """Start a script in slot 1, replacing the one running."""
        how = command(self.limits)  # refuse before stopping anything when scripts can't run here
        replaced = ""
        if old := self.script:
            replaced = f"stopped the previous script at line {old.line}; "
            await self._stop_script(old, "a new script replaced it")
        if self.ctx.game is not None:  # each script starts with the default guard: near, when armed
            self.ctx.game.guard = combat.Guard(tactics=combat.Tactics(self.ctx.decide) if self.ctx.decide else None)
        self.script = await self._spawn(how, source, goal)
        self.emit(Step("script", "started", True, {"goal": goal, "source": source}))
        self._changed.set()
        return replaced + "started"

    async def _spawn(self, how: list[str], source: str, goal: str, query: bool = False) -> Script:
        proc = await asyncio.create_subprocess_exec(*how, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                                                    env={}, cwd="/", limit=MAX_MESSAGE)
        script = Script(source, goal, proc, started=time.monotonic(), query=query)
        proc.stdin.write(json.dumps({"source": source, "api": API}).encode() + b"\n")
        script.task = asyncio.create_task(self._serve(script))
        return script

    async def query(self, source: str, timeout: float = 90) -> str:
        """Run a read-only script beside the running one, without disturbing it, and return what it printed."""
        vet(source)
        script = await self._spawn(command(self.limits), source, "a query", query=True)
        try:
            await asyncio.wait_for(script.done.wait(), timeout)
        except asyncio.TimeoutError:
            script.task.cancel()
            await asyncio.wait({script.task})
            script.outcome = ("killed", f"it took over {timeout:.0f}s")
        how, *rest = script.outcome or ("killed", "it ended without saying how")
        printed = "\n".join(script.output) or "(it printed nothing)"
        if how == "error":
            e = rest[0]
            return f"{printed}\n{e['error']}: {e.get('message', '')} at line {e.get('line')}: {e.get('source_line', '')}"
        if how == "killed":
            return f"{printed}\nthe query ended: {rest[0]}"
        return printed

    async def halt(self, why: str) -> None:
        """Stop the script and the running action, and the idle work."""
        await self.interrupt(why)
        if self._task:
            self._task.cancel()
            await asyncio.wait({self._task})

    async def interrupt(self, why: str) -> None:
        if self.script:
            await self._stop_script(self.script, why)

    def hear(self, entry: dict) -> None:
        if entry.get("kind") in HEARD and entry.get("text"):
            self._heard.append(f"{entry['kind']}: {entry['text'][:200]}")

    def describe(self) -> str:
        """The script slots: what runs (and its action), and what waits."""
        lines = [f"slot 1: {self.script.goal}, at line {self.script.line}" + (f", running {self.current.label} for {time.monotonic() - self.current.started:.0f}s" if self.current else "")
                 if self.script else "slot 1: empty (the farmer is idle)"]
        lines += [f"slot {i + 2}: {goal}" for i, (_, goal) in enumerate(self.waiting)]
        return "\n".join(lines)

    def take(self) -> tuple[list[str], list[Image], list[str]]:
        """The trace since the last call, its images, and why the agent was woken."""
        taken = self._trace, self._images, list(dict.fromkeys(self._why))
        self._trace, self._images, self._why, self.printed = [], [], [], []
        return taken

    # --- serving a script ----------------------------------------------------------------------------------

    async def _serve(self, script: Script) -> None:
        """Answer the script's process until it ends, is stopped, or breaks a limit; then kill it."""
        proc = script.proc

        def time_left() -> float:
            return max(0.05, script.started + self.max_seconds - time.monotonic())

        try:
            while True:
                try:
                    raw = await asyncio.wait_for(proc.stdout.readline(), time_left())
                except asyncio.TimeoutError:
                    raise Kill(f"it ran over {self.max_seconds / 60:.0f} minutes") from None
                except ValueError:
                    raise Kill(f"it sent a message over {MAX_MESSAGE} bytes") from None
                if not raw:
                    await proc.wait()
                    raise Kill(death(proc.returncode, await proc.stderr.read(MAX_MESSAGE)))
                try:
                    message = json.loads(raw, object_hook=record)
                except (ValueError, RecursionError):
                    message = None
                if not isinstance(message, dict):
                    raise Kill("it sent something which isn't a message")
                script.line = line_of(message, script.line)
                if set(message) == {"line"}:  # the script moved on to another line (pure Python too)
                    continue
                if "log" in message:
                    script.logged += 1
                    if script.logged > MAX_LOGS:
                        raise Kill(f"it logged over {MAX_LOGS} lines")
                    script.chars += len(logged := text_of(message["log"], MAX_LOG_TEXT))
                    if script.chars > MAX_LOG_CHARS:
                        raise Kill(f"it logged over {MAX_LOG_CHARS} characters")
                    if script.query:  # a query's prints are its answer, not messages
                        script.output.append(logged)
                    else:
                        self._note("printed: " + logged, "log")
                        self.printed.append((time.monotonic(), logged))
                        self._queued = True
                        self._wake_for_messages()
                elif "call" in message:
                    try:
                        reply = await self._answer(script, message)
                    except (Stopped, asyncio.CancelledError):
                        raise
                    except Exception as ex:  # a malformed call: end the script, but say why
                        raise Kill(f"it sent a malformed {text_of(message.get('call'), 40)} call ({type(ex).__name__})") from None
                    if script.stopped:
                        return
                    proc.stdin.write(json.dumps(reply, default=wire).encode() + b"\n")
                    try:
                        await asyncio.wait_for(proc.stdin.drain(), time_left())
                    except asyncio.TimeoutError:  # it stopped reading its replies
                        raise Kill("it stopped reading replies") from None
                else:
                    if "error" in message:
                        script.outcome = ("error", {k: text_of(message.get(k)) for k in ("error", "message", "source_line")} | {"line": script.line})
                    else:
                        script.outcome = ("done",)
                    return
        except Kill as ex:
            script.outcome = ("killed", str(ex))
        except (Stopped, asyncio.CancelledError, ConnectionError):
            pass
        finally:
            if proc.returncode is None:
                proc.kill()
            await asyncio.shield(proc.wait())
            self._ended(script)

    async def _answer(self, script: Script, message: dict) -> dict:
        """Carry out one call; a Surprise or TypeError becomes a raise in the script."""
        try:
            return {"value": await self._sdk(script, message)}
        except Surprise as ex:
            return {"raise": type(ex).__name__, "args": list(ex.args)}
        except TypeError as ex:
            return {"raise": "TypeError", "args": [str(ex)]}

    async def _sdk(self, script: Script, message: dict) -> Any:
        name, args, kwargs = message["call"], message.get("args") or [], message.get("kwargs") or {}
        if script.stopped:  # a stale script gets nothing more done
            raise Stopped(script.stopped)
        kind, cls, fn = api.lookup(name)
        if script.query and (kind in ("action", "setting") or name == "Screen.shot"):
            raise TypeError(f"a query only looks: {name} isn't allowed; use run to act" + (", or the screenshot tool" if name == "Screen.shot" else ""))
        if name == "answer" and args and isinstance(args[0], str):
            args = [await self._pick(args[0], await self.ctx.game.read("menu.choices") or []), *args[1:]]
        me = cls() if cls.live else cls(message.get("self"))
        call = inspect.signature(fn).bind(me, self.ctx, *args, **kwargs)  # a TypeError for the script
        if kind == "action":
            return await self._act(script, name, fn, call)
        given = list(call.arguments.values())[2:]
        if kind == "judge" and name == "check":
            return await self._check(*given)
        if kind == "judge":
            description, options = given
            return options[await self._pick(description, options)]
        try:
            value = await fn(*call.args, **call.kwargs)
        except (ActionError, BridgeError) as ex:
            raise failure(str(ex)) from None
        if kind == "setting":
            self._note(value, "safety")
        if isinstance(value, Image):  # for the agent, who sees it when woken
            self._images.append(value)
            self._note("screenshot taken")
            return "(the agent sees the screenshot when next woken)"
        return value

    async def _check(self, situation: Any) -> bool:
        if not isinstance(situation, str):
            raise TypeError(f"check takes a situation as text, not {situation!r}")
        i, sure = await self._which([situation])
        if sure < SURE:
            raise Unsure(situation, sure)
        self._note(f"check {situation!r}: {'yes' if i == 0 else 'no'} ({sure:.2f})", "judge")
        return i == 0

    async def _pick(self, description: Any, options: Any) -> int:
        """The index of the option the judge picks; raises Unsure."""
        if not (isinstance(description, str) and isinstance(options, list)):
            raise TypeError("pick takes a description (text) and a list of options")
        if not options:
            raise Unsure(description, 0.0)
        i, sure = await self._which([described(o) for o in options], {"choosing": description})
        if i is None or sure < SURE:
            raise Unsure(description, sure)
        self._note(f"pick {description!r}: {described(options[i])} ({sure:.2f})", "judge")
        return i

    async def _stop_script(self, script: Script, why: str) -> None:
        """Stop a script: its running action is cancelled, and its process killed."""
        script.stop(why)
        if self.script is script:
            self.script = None
        if self.current and self._owner is script and self._job and not self._job.done():
            self._cancel = Stopped(why)
            self._job.cancel()  # the action winds down, then the process is killed
        elif script.task:
            script.task.cancel()
        if script.task:
            await asyncio.wait({script.task}, timeout=5)

    def _ended(self, script: Script) -> None:
        """A script's process is gone: tell the agent how it ended, unless it was stopped on purpose."""
        script.done.set()
        if script.query:  # its caller reads how it ended
            return
        self.emit(Step("script", "ended"))
        if self.script is script:
            self.script = None
        self._queued = False  # its messages go with this wake-up
        if self._message_timer:
            self._message_timer.cancel()
            self._message_timer = None
        how, *rest = script.outcome or ("killed", "it ended without saying how")
        if how == "done" and self.waiting:  # the next one goes on at once: the agent queued it
            asyncio.get_running_loop().create_task(self._start_next())
            return
        if how in ("error", "killed") and self.waiting:  # what's queued may have depended on this one: you decide again
            self._why.append(f"the queue was cleared: {'; '.join(goal for _, goal in self.waiting)}")
            self.waiting = []
        if how == "done":
            self.wake("the script ended")
        elif how == "error":
            e = rest[0]
            self._note(f"{e['error']}: {e.get('message', '')}\n  at line {e.get('line')}: {e.get('source_line', '')}", ok=False)
            self.wake(f"the script raised {e['error']} at line {e.get('line')}")
        elif how == "killed":
            self._note(f"the script was ended at line {script.line}: {rest[0]}", ok=False)
            self.wake(rest[0] if rest[0].startswith("the script") else f"the script was ended: {rest[0]}")
        else:
            self._note(f"the script stopped at line {script.line}: {rest[0]}")
            if self.error:
                self.on_wake()

    # --- actions -------------------------------------------------------------------------------------------

    async def _which(self, options: list[str], extra: dict | None = None) -> tuple[int | None, float]:
        """The judge: the index of the option which holds (None: none of them), and how sure it is, from the facts and what just happened."""
        if not self.ctx.decide:
            return None, 0.0
        criteria = {f"case{i}": c for i, c in enumerate(options)} | {"none": NONE}
        try:
            now = await self.ctx.game.read(FACTS_READ)
            known = facts(now["time"], now)
        except (BridgeError, KeyError, TypeError) as ex:
            known = {"status": f"unknown ({ex})"}
        context = {"goal": self.script.goal if self.script else "", "last_step": self._last[0], "its_result": self._last[1][:600], "recent_events": list(self._heard)}
        try:
            key, sure = await self.ctx.decide({**context, **known, **(extra or {})}, criteria)
        except Exception:
            return None, 0.0
        return (int(key[4:]) if key and key.startswith("case") else None), sure

    def _note(self, line: str, kind: str = "info", ok: bool = True) -> None:
        self._trace.append(line)
        self.emit(Step(kind, line, ok))

    def _wake_for_messages(self) -> None:
        """A script's messages wake the agent, which reads them all: after a moment (so a burst comes as one, and a script
        which ends meanwhile wakes it just once), and not more often than WAKE_EVERY."""
        if self._queued and not self._message_timer:
            wait = max(GATHER, self._woken_for_messages + WAKE_EVERY - time.monotonic())
            self._message_timer = asyncio.get_running_loop().call_later(wait, self._messages_due)

    def _messages_due(self) -> None:
        self._message_timer = None
        if self.script and self._queued:
            self._woken_for_messages, self._queued = time.monotonic(), False
            self.wake("the script sent messages (it's still running)")

    def wake(self, why: str) -> None:
        self._why.append(why)
        self.emit(Step("wake", why))
        self.on_wake()

    async def _main(self) -> None:
        while True:
            if self.script is None:
                await self._idle()
            else:
                await self.script.done.wait()

    async def _act(self, script: Script, name: str, fn: Callable, call: inspect.BoundArguments) -> Any:
        """Run one action for a script, keeping watch for safety meanwhile. Returns its result; raises a Surprise, TypeError, or Stopped."""
        me = call.arguments["self"]
        given = ({"of": me.ref["name"]} if "name" in me.ref else {}) | dict(list(call.arguments.items())[2:])
        async with self._lock:
            if script.stopped:
                raise Stopped(script.stopped)
            self._overtime(script)
            if hit := await self._danger(name, given):
                raise hit
            self.count += 1
            job = self.current = Job(self.count, name, given, time.monotonic())
            self._owner = script
            self.emit(Step("call", job.label, data={"name": name, "args": given, "line": script.line}))
            self._cancel = None
            self._job = asyncio.create_task(self._perform(name, fn, call, given))
            try:
                while not self._job.done():
                    await asyncio.wait({self._job}, timeout=self.poll)
                    if self._job.done():
                        break
                    if self._overtime(script):
                        self._cancel = Stopped(script.stopped)
                    else:
                        await self._sample(job)
                        if hit := await self._danger(name, given) or await self._stuck(job):
                            self._cancel = hit
                    if self._cancel:
                        self._job.cancel()
                        await asyncio.wait({self._job})
            except BaseException:
                self._job.cancel()  # and let it wind down, so it never runs beside the next action
                await asyncio.wait({self._job}, timeout=5)
                self.current = self._owner = None
                raise
            self.current = self._owner = None
            if self._job.cancelled():
                await self.ctx.game.release()
                raised = self._cancel or Stopped("stopped")
                self._record(job, Outcome(f"stopped partway: {type(raised).__name__}: {raised}", ok=False))
                raise raised
            if isinstance(ex := self._job.exception(), HumanControl):
                self.wake("the player took control")  # the agent pauses until it's given back
                raise script.stop("the player took control")
            if isinstance(ex, Disconnected):
                self.error = ex
                raise script.stop("the game went away")
            if ex:  # the report after the action failed
                outcome, value, raised = Outcome(f"ERROR: {type(ex).__name__}: {ex}", ok=False), None, failure(f"{type(ex).__name__}: {ex}")
            else:
                outcome, value, raised = self._job.result()
            self._record(job, outcome)
            if not outcome.ok and (stuck := await self._stuck(job, failed=True)):
                raised = stuck  # failing again and again: say so, rather than the failure alone
            if self.ctx.finished:
                self.wake("the day ended")
                raise script.stop("the day ended")
            if raised:
                raise raised
            if isinstance(value, dict) and value.get("needs_choice"):
                raise Question(str(value["needs_choice"]), [str(p) for p in value.get("professions", [])])
            return value

    async def _perform(self, name: str, fn: Callable, call: inspect.BoundArguments, given: dict) -> tuple[Outcome, Any, BaseException | None]:
        """Do the action. Returns what the agent reads of it, what the script gets, and what the script should raise instead."""
        g = self.ctx.game
        if self.ctx.before_action:
            await self.ctx.before_action()
        value, raised = None, None
        try:
            if not api.recovers(name, given):
                await g.guard.watch(g, hunt=True)  # with guard("all"), the floor is cleared between actions
            value = await fn(*call.args, **call.kwargs)
            text = render(value) if value not in (None, {}, []) else "done"
        except (HumanControl, Disconnected):
            raise
        except Interrupted as ex:
            text, raised = f"INTERRUPTED: {ex}", interruption(str(ex), await g.read("menu.choices"))
        except (ActionError, BridgeError) as ex:
            text, raised = f"ERROR: {ex}", failure(str(ex))
        except TypeError as ex:  # the call's arguments were the wrong shape
            text, raised = f"ERROR: TypeError: {ex}", TypeError(str(ex))
        except Exception as ex:  # a bug in a skill shouldn't end the day
            logging.exception("action %s failed with %s", name, given)
            reason = f"this action failed unexpectedly ({type(ex).__name__}: {ex}); try another way"
            text, raised = f"ERROR: {reason}", failure(reason)
        parts = [text]
        entries = g.drain_journal()
        if happened := news(entries):
            parts.append(happened)
        parts.append(await status(g))
        shown = {"value": value, "journal": entries, "error": text.removeprefix("ERROR: ") if raised else None, "status": await g.read(STATUS)}
        return Outcome("\n".join(parts), ok=raised is None, shown=shown), value, raised

    def _overtime(self, script: Script) -> bool:
        """Past the script's real-time limit, even in the middle of an action: it's ended, and says so. Raises Stopped before an action."""
        if time.monotonic() < script.started + self.max_seconds:
            return False
        if not script.stopped:
            script.stopped = f"it ran over {self.max_seconds / 60:.0f} minutes"
            script.outcome = ("killed", f"{script.stopped}, so its action was stopped")
        if self.current is None:
            raise Stopped(script.stopped)
        return True

    def _record(self, job: Job, outcome: Outcome) -> None:
        data = {"name": job.name, "args": job.args, **outcome.shown} if outcome.shown else {}
        # the agent reads the same lines the view draws (display.action_lines), as plain text and uncut
        told = "\n".join(line.plain for line in action_lines(data, outcome.ok, full=True)) if data else f"{job.label}: {outcome.text}"
        self._last = (job.label, told)
        self._finished.append(time.monotonic())
        self._done.append((self._samples[-1]["clock"] if self._samples else "", job.label, ("ok" if outcome.ok else "failed: ") + (told.split("\n")[0][len(job.label) + 2:][:150] if not outcome.ok else "")))
        self._trace.append(told)
        self.emit(Step("result", f"{job}: {outcome.text}", outcome.ok, data))

    async def _sample(self, job: Job) -> None:
        """Every SAMPLE seconds while an action runs: where the farmer is, for the stuck check."""
        if time.monotonic() - self._sampled < SAMPLE:
            return
        self._sampled = time.monotonic()
        try:
            at = await self.ctx.game.read({"tile": "player.tile", "place": "location.name", "clock": "time.text", "energy": "player.energy", "money": "player.money",
                                           "busy": "state.cutscene or state.menu"})
        except BridgeError:
            return
        if at.pop("busy"):  # a cutscene or a dialogue: standing still then is no sign of being stuck, so the check waits
            self._asked = self._sampled
            return
        self._samples.append({**at, "tile": tuple(at["tile"]), "energy": int(at["energy"]), "action": job.label, "at": self._sampled})

    async def _stuck(self, job: Job, failed: bool = False) -> Stuck | None:
        """The judge's always-on check, for what the other checks miss: is the farmer stuck, failing again and again, or going round
        in a loop? Asked every STUCK_EVERY seconds while an action runs (once it has run STUCK_AFTER, or a few actions were done), and
        after a failed action once failures repeat (3 of the last 4 actions; one failure is just a failure, with its own reason).
        It sees the recent actions and positions with their times, and ready-made counts (it compares well but can't do arithmetic)."""
        now = time.monotonic()
        busy = now - job.started >= STUCK_AFTER or len(self._finished) == 4 and now - self._finished[0] <= 6 * STUCK_AFTER  # a long action, or a run of short ones (a loop)
        if failed and sum(how.startswith("failed") for _, _, how in list(self._done)[-4:]) < 3:
            return None
        if not failed and (not busy or now - self._asked < STUCK_EVERY):
            return None
        self._asked = now
        seen = {"running": f"{job.label}, for {now - job.started:.0f}s" if not failed else "nothing (the last action failed)",
                **stuck_facts(list(self._done), list(self._samples), now)}
        if not failed and seen["new_tiles_last_20s"] >= 3:  # getting somewhere new is progress; a loop comes back to the same tiles
            self._strikes = 0
            return None
        i, sure = await self._which(STUCK, seen)
        stuck = i == 1 and sure >= STUCK_SURE
        self._strikes = self._strikes + 1 if stuck else 0
        if not stuck or (not failed and self._strikes < 2):
            return None
        self._strikes = 0
        self._trace.append(f"stuck check: stuck ({sure:.2f})")
        self.emit(Step("judge", f"stuck check: stuck ({sure:.2f})", True, {"saw": seen}))  # what it judged by, for the transcript
        if failed:
            _, label, how = self._done[-1]
            return Stuck(f"failing again and again; the last: {label}: {how.removeprefix('failed: ')}")
        return Stuck(f"{job.label} isn't getting anywhere")

    async def _danger(self, name: str, args: dict) -> Surprise | None:
        """Low health, low energy, or the time to head home, each raised once until it clears. Actions which cure it may run."""
        if api.recovers(name, args):
            return None
        try:
            s = await self.ctx.game.read(STATUS)
        except BridgeError:
            return None
        found: dict[str, Surprise] = {}
        if s["hp"] < LOW_HEALTH * s["maxh"]:
            found["health"] = LowHealth(f"health is low ({s['hp']}/{s['maxh']})")
        if s["energy"] < LOW_ENERGY:
            found["energy"] = LowEnergy(f"energy is low ({int(s['energy'])})")
        left = until_2am(s["clock"])
        # aim to be home by midnight: going to bed later costs energy the next morning
        if s["clock"] >= 1800 and (walk := await self._walk_home(s)) is not None and left - 120 <= walk + HOME_SLACK:
            found["home"] = TimeToGoHome(walk, left)
        new = [ex for key, ex in found.items() if key not in self._warned]
        self._warned = set(found)  # a warning which cleared may be given again
        if new:
            self._note(f"SAFETY: {type(new[0]).__name__}: {new[0]}", "safety")
        return new[0] if new else None

    async def _walk_home(self, s: dict) -> int | None:
        """Game minutes to walk home, estimated again when the location changes or every 30 game minutes."""
        estimate = getattr(self.ctx.game, "minutes", None)
        if not estimate:
            return None
        h = self._home
        if h.get("place") != s["place"] or minutes(s["clock"]) - h.get("at", -999) >= 30:
            try:
                self._home = h = {"place": s["place"], "at": minutes(s["clock"]), "walk": await estimate("home")}
            except (BridgeError, ActionError):
                return h.get("walk")
        return h["walk"]

    async def _idle(self) -> None:
        """Keep the farmer busy until a script starts."""
        self._changed.clear()
        if self.script:
            return
        stop = asyncio.Event()
        work = asyncio.ensure_future(self.ctx.while_idle(stop)) if self.ctx.while_idle else None
        try:
            await self._changed.wait()
        finally:
            if work:
                stop.set()
                try:
                    done = await asyncio.shield(work)
                except Exception as ex:  # optional work must never break the runner
                    done = {"error": str(ex)[:120]}
                done = {k: v for k, v in (done or {}).items() if v and k != "ticks"}
                if done:
                    self._note("while idle: " + ", ".join(f"{k} {v}" for k, v in done.items()), "busy")
