"""Plans as Python scripts: each runs in WebAssembly in its own process (wasm_host.py, sandbox_child.py), and the runner here carries out its SDK calls (sdk.py) in the game."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
import signal
import sys
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from . import wasm_host
from .driver import BridgeError, Disconnected, HumanControl
from .game import ActionError, Game, Interrupted
from .memory import Memory
from .look import Image, minutes, news, render, status, until_2am
from .sandbox_child import ActionFailed, Closed, Cutscene, LowEnergy, LowHealth, NotEnough, Question, Surprise, TimeToGoHome, Unreachable, Unsure
from .sdk import ACTIONS, FACTS, recovers

SURE = 0.6  # the judge's least confidence for an answer to count
MAX_CALLS = 200  # SDK calls per script
MAX_MESSAGE = 1 << 20  # bytes in one message from a script
LIMITS = {"fuel": 100_000_000_000, "memory": 256 << 20}  # per script: WebAssembly computation (Python's start takes about 3 billion, a second of work about 10) and memory
MAX_SOURCE = 20_000  # characters
MAX_SECONDS = 30 * 60  # real time per script
MAX_LOGS = 200  # log lines per script
WAKE_EVERY = 30  # seconds: a script's messages wake the planner at most this often; meanwhile they wait in the queue
GATHER = 1.0  # seconds a message waits for more, before it wakes the planner
MAX_TEXT = 500  # characters kept of anything else the script's process says
MAX_LOG_TEXT = 6000  # characters kept of one log line, enough for a map
MAX_LOG_CHARS = 40_000  # logged characters per script
HEARD = {"bubble", "chat", "dialogue", "event", "letter", "level", "message", "question"}  # journal entries the judge is shown as recent events
LOW_HEALTH = 0.3  # of the maximum
LOW_ENERGY = 15
HOME_SLACK = 15  # game minutes to spare, arriving home by midnight


@dataclass
class Context:
    """What the planner's tools, the runner, and the SDK share."""

    game: Game
    memory: Memory = field(default_factory=Memory)  # the planner's lasting facts (remember, forget)
    before_action: Callable[[], Awaitable[None]] | None = None
    shot: dict = field(default_factory=lambda: {"scale": 1.0, "cropped": False})  # the last screenshot's scale, for clicks at its pixels
    finished: bool = False  # the farmer slept: the day is over
    last_night: dict | None = None  # what the night screens showed
    only_tools: set[str] | None = None  # when set, the planner's other tools are refused
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
    """What the planner reads of an action or a tool call."""

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
    (NotEnough, r"inventory is (too )?full|free inventory slot|but you have \d+g|you don't have|missing ingredients|out of energy|isn't in there|is empty|only \d+ of|is there room|no tilled, empty soil"),
    (Unreachable, r"couldn't reach|no walking route|couldn't get to|lost sight|couldn't catch up|gave up travelling|blocked"),
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
    return Cutscene(what) if "cutscene" in what or "minigame" in what else Question(what, choices or [])


# --- facts and the judge -----------------------------------------------------------------------------------


def facts(t: dict, s: dict) -> dict:
    """Ready-made, comparable facts for the judge: it compares well but can't do arithmetic."""
    weather = str(t.get("weather") or "")
    return {
        "time": t.get("text"), "minutes_until_2am": until_2am(t["clock"]), "day": f"{t.get('weekday')} {t.get('season')} {t.get('day')}",
        "weather": weather, "raining": "yes" if any(w in weather.lower() for w in ("rain", "storm")) else "no", "location": s["place"],
        "health": f"{s['health']}/{s['maxh']}", "health_percent": round(100 * s["health"] / max(1, s["maxh"])),
        "energy": f"{int(s['energy'])}/{s['maxe']}", "energy_percent": round(100 * s["energy"] / max(1, s["maxe"])),
        "money": s["money"], "free_slots": s["slots"], "backpack_full": "yes" if s["slots"] == 0 else "no", "holding": (s.get("held") or {}).get("name"),
    }


FACTS_READ = {"time": "time", "place": "location.name", "health": "player.health", "maxh": "player.max_health", "energy": "player.energy", "maxe": "player.max_energy",
              "money": "player.money", "slots": "player.free_slots", "held": "player.held"}


class Judge:
    """Says which of some options is the case, from the facts and what just happened."""

    NONE = "None of these: something else is the case, or it isn't clear."

    def __init__(self, ctx: Context, context: Callable[[], dict]):
        self.ctx, self.context = ctx, context

    async def which(self, options: list[str], extra: dict | None = None) -> tuple[int | None, float]:
        """The index of the option which holds (None: none of them), and how sure the judge is."""
        if not self.ctx.decide:
            return None, 0.0
        criteria = {f"case{i}": c for i, c in enumerate(options)} | {"none": self.NONE}
        try:
            now = await self.ctx.game.read(FACTS_READ)
            known = facts(now["time"], now)
        except (BridgeError, KeyError, TypeError) as ex:
            known = {"status": f"unknown ({ex})"}
        try:
            key, sure = await self.ctx.decide({**self.context(), **known, **(extra or {})}, criteria)
        except Exception:
            return None, 0.0
        return (int(key[4:]) if key and key.startswith("case") else None), sure


# --- what crosses to and from the script -------------------------------------------------------------------


@dataclass(frozen=True)
class Thing:
    """An item as scripts see it."""

    name: str
    count: int = 1
    price: int = 0  # numbers are 0 when the game gives none, so scripts can always do sums
    energy: int = 0
    healing: int = 0
    category: str | None = None
    water: int | None = None  # a watering can's water, and how much it holds
    water_max: int | None = None
    upgrade: int | None = None  # a tool's level: 0 basic, 1 copper, 2 steel, 3 gold, 4 iridium

    def __str__(self) -> str:
        return (f"{self.name}" + (f": x{self.count}" if self.count < 1_000_000 else "") + (f", water {self.water}/{self.water_max}" if self.water is not None else "")
                + (f", {self.price}g" if self.price else "") + (f", +{self.energy} energy" if self.energy > 0 else ""))


def thing(row: dict) -> Thing:
    return Thing(row["name"], row.get("stack") or row.get("Stock") or 1, row.get("price") or row.get("Price") or 0, row.get("energy") or 0, row.get("healing") or 0,
                 row.get("category_name") or (str(row["category"]) if row.get("category") is not None else None),
                 row.get("water"), row.get("water_max"), row.get("upgrade"))


def plain(value: Any) -> Any:
    """An argument from the script: items stand for their names."""
    if isinstance(value, dict):
        return value["item"]["name"] if set(value) == {"item"} else {k: plain(v) for k, v in value.items()}
    return [plain(v) for v in value] if isinstance(value, list) else value


def wire(value: Any) -> Any:
    return {"item": asdict(value)} if isinstance(value, Thing) else str(value)


def described(option: Any) -> str:
    if isinstance(option, dict) and set(option) == {"item"}:
        return str(Thing(**option["item"]))
    return option if isinstance(option, str) else json.dumps(option, default=str)


def text_of(value: Any, limit: int = MAX_TEXT) -> str:
    """Text from the script's process, which is untrusted: a string, and short."""
    text = value if isinstance(value, str) else json.dumps(value, default=str)[:limit * 2]
    return text[:limit] + (" ..." if len(text) > limit else "")


def line_of(message: dict, last: int | None) -> int | None:
    line = message.get("line")
    return line if isinstance(line, int) and not isinstance(line, bool) and 0 < line < 100_000 else last


# --- the sandbox's process ---------------------------------------------------------------------------------


_COMMAND: list[str] | None = None  # tests put a fake script process here


def command(limits: dict) -> list[str]:
    """How to start a script's process: CPython for WASI under wasmtime. There's no other way, so without it scripts don't run."""
    if _COMMAND:
        return _COMMAND
    if why := wasm_host.missing():
        raise Rejected(f"scripts can't run on this computer yet: {why}")
    return [sys.executable, "-I", str(Path(wasm_host.__file__)), str(limits["fuel"]), str(limits["memory"])]


def death(code: int | None, stderr: bytes) -> str:
    """Why a script's process ended without saying so."""
    if code == wasm_host.FUEL:
        return "the script used too much computation (a busy loop?)"
    if code in (-signal.SIGKILL, 128 + signal.SIGKILL):
        return "it was killed"
    tail = stderr.decode(errors="replace").strip().splitlines()[-1:] or [f"exit code {code}"]
    return f"its process ended: {tail[0][:300]}"


# --- running -----------------------------------------------------------------------------------------------


STATUS = {"clock": "time.clock", "place": "location.name", "energy": "player.energy", "maxe": "player.max_energy", "hp": "player.health", "maxh": "player.max_health",
          "money": "player.money", "free": "player.free_slots"}  # what the display shows after an action


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
    calls: int = 0
    logged: int = 0  # lines
    chars: int = 0  # logged characters
    line: int = 0
    stopped: str | None = None
    outcome: tuple = ()  # how it ended: done, ask, error, killed, or stopped
    done: asyncio.Event = field(default_factory=asyncio.Event)

    def stop(self, why: str) -> Stopped:
        """Mark it stopped on purpose; returns the Stopped to raise."""
        self.stopped, self.outcome = why, ("stopped", why)
        return Stopped(why)


class Runner:
    """Plays scripts in sandboxed processes, one action at a time, keeps the farmer safe and busy, and wakes the planner when a script ends."""

    def __init__(self, ctx: Context, emit: Callable[[Step], None], wake: Callable[[], None], poll: float = 2):
        self.ctx, self.emit, self.on_wake, self.poll = ctx, emit, wake, poll
        self.limits, self.max_seconds = dict(LIMITS), MAX_SECONDS
        self.script: Script | None = None
        self.current: Job | None = None
        self.count = 0
        self.error: BaseException | None = None  # the player took control, or the game went away
        self.judge = Judge(ctx, self._context)
        self._lock = asyncio.Lock()  # one action at a time, across scripts
        self._owner: Script | None = None  # whose action is running
        self._trace: list[str] = []  # what the planner hasn't been told yet
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
        self._woken_for_messages = -WAKE_EVERY  # when messages last woke the planner
        self._queued = False  # messages the planner hasn't been woken for
        self.printed: list[str] = []  # what the script printed, until the planner reads it
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

    async def run(self, source: str, goal: str, finish_current: bool = False) -> str:
        """Start a script, replacing the one running. Raises Rejected for a script which won't run."""
        vet(source)
        how = command(self.limits)  # refuse before stopping anything when scripts can't run here
        replaced = ""
        if old := self.script:
            replaced = f"stopped the previous script at line {old.line}; "
            await self._stop_script(old, "a new script replaced it", cancel_action=not finish_current, wait=not finish_current)
        proc = await asyncio.create_subprocess_exec(*how, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                                                    env={}, cwd="/", limit=MAX_MESSAGE)
        script = self.script = Script(source, goal, proc, started=time.monotonic())
        self.emit(Step("script", "started"))
        proc.stdin.write(json.dumps({"source": source}).encode() + b"\n")
        script.task = asyncio.create_task(self._serve(script))
        self._changed.set()
        return replaced + "started"

    async def halt(self, why: str) -> None:
        """Stop the script and the running action, and the idle work."""
        await self.interrupt(why)
        if self._task:
            self._task.cancel()
            await asyncio.wait({self._task})

    async def interrupt(self, why: str) -> None:
        if self.script:
            await self._stop_script(self.script, why, cancel_action=True, wait=True)

    def hear(self, entry: dict) -> None:
        if entry.get("kind") in HEARD and entry.get("text"):
            self._heard.append(f"{entry['kind']}: {entry['text'][:200]}")

    def describe(self) -> str:
        lines = []
        if self.script:
            lines.append(f"script running ({self.script.goal}), at line {self.script.line}")
        if self.current:
            lines.append(f"running {self.current.label} (started {time.monotonic() - self.current.started:.0f}s ago)")
        return "\n".join(lines) or "no script: the farmer is idle"

    def take(self) -> tuple[list[str], list[Image], list[str]]:
        """The trace since the last call, its images, and why the planner was woken."""
        taken = self._trace, self._images, list(dict.fromkeys(self._why))
        self._trace, self._images, self._why, self.printed = [], [], [], []
        return taken

    def wake(self, why: str) -> None:
        """Wake the planner for something outside the script (the player's message)."""
        self._wake(why)

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
                    message = json.loads(raw)
                except (ValueError, RecursionError):
                    message = None
                if not isinstance(message, dict):
                    raise Kill("it sent something which isn't a message")
                script.line = line_of(message, script.line)
                if "log" in message:
                    script.logged += 1
                    if script.logged > MAX_LOGS:
                        raise Kill(f"it logged over {MAX_LOGS} lines")
                    script.chars += len(logged := text_of(message["log"], MAX_LOG_TEXT))
                    if script.chars > MAX_LOG_CHARS:
                        raise Kill(f"it logged over {MAX_LOG_CHARS} characters")
                    self._note("printed: " + logged, "log")
                    self.printed.append(logged)
                    self._queued = True
                    self._wake_for_messages()
                elif "call" in message:
                    if script.calls > MAX_CALLS + 20:  # it keeps calling past its budget
                        raise Kill(f"it went on calling after {MAX_CALLS} calls")
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
                    if "ask" in message:
                        script.outcome = ("ask", text_of(message["ask"]))
                    elif "error" in message:
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
        """Carry out one SDK call; a Surprise or TypeError becomes a raise in the script."""
        name, args, kwargs = message["call"], message.get("args") or [], message.get("kwargs") or {}
        script.calls += 1
        try:
            if script.calls > MAX_CALLS:
                raise Surprise(f"budget: the script made over {MAX_CALLS} calls")
            return {"value": await self._sdk(script, name, args, kwargs)}
        except Surprise as ex:
            return {"raise": type(ex).__name__, "args": list(ex.args)}
        except TypeError as ex:
            return {"raise": "TypeError", "args": [str(ex)]}

    async def _sdk(self, script: Script, name: str, args: list, kwargs: dict) -> Any:
        g = self.ctx.game
        if script.stopped:  # a stale script gets nothing more done
            raise Stopped(script.stopped)
        if name == "answer" and args and isinstance(args[0], str):
            args = [await self._pick(args[0], await g.read("menu.choices") or []), *args[1:]]
        if name in ACTIONS:
            return await self._act(script, name, plain(args), plain(kwargs))
        if name in FACTS:
            call = inspect.signature(FACTS[name]).bind(self.ctx, *plain(args), **plain(kwargs))  # a TypeError for the script
            try:
                value = await FACTS[name](*call.args, **call.kwargs)
            except (ActionError, BridgeError) as ex:
                raise failure(str(ex)) from None
            if isinstance(value, Image):  # for the planner, who sees it when woken
                self._images.append(value)
                self._note("screenshot taken")
                return "(the planner sees the screenshot when next woken)"
            return value
        match name, args, kwargs:
            case "shop_stock", [], {}:
                return [thing(s) for s in await self._stock(script)]
            case "backpack", [part], {}:
                items = [thing(i) for i in await g.inventory()] if part in ("items", "food", "fish") else []
                free = await g.read("player.free_slots") if part in ("free", "full") else 0
                return {"items": items, "food": [i for i in items if i.energy > 0], "fish": [i for i in items if i.category in ("Fish", "-4")],
                        "free": free, "full": free == 0}[part]
            case "check", [str(situation)], {}:
                i, sure = await self.judge.which([situation])
                if sure < SURE:
                    raise Unsure(situation, sure)
                self._note(f"check {situation!r}: {'yes' if i == 0 else 'no'} ({sure:.2f})", "judge")
                return i == 0
            case "pick", [str(description), list(options)], {}:
                return await self._pick(description, options)
        raise TypeError(f"{name}() was called the wrong way: {args} {kwargs}")

    async def _pick(self, description: str, options: list) -> int:
        """The index of the option the judge picks; raises Unsure."""
        if not options:
            raise Unsure(description, 0.0)
        i, sure = await self.judge.which([described(o) for o in options], {"choosing": description})
        if i is None or sure < SURE:
            raise Unsure(description, sure)
        self._note(f"pick {description!r}: {described(options[i])} ({sure:.2f})", "judge")
        return i

    async def _stop_script(self, script: Script, why: str, cancel_action: bool, wait: bool) -> None:
        """Stop a script: its process is killed, and its running action cancelled unless it should finish."""
        script.stop(why)
        if self.script is script:
            self.script = None
        running = self.current and self._owner is script and self._job and not self._job.done()
        if running and cancel_action:
            self._cancel = Stopped(why)
            self._job.cancel()  # the action winds down, then the process is killed
        elif not running and script.task:
            script.task.cancel()
        if wait and script.task:
            await asyncio.wait({script.task}, timeout=5)

    def _ended(self, script: Script) -> None:
        """A script's process is gone: tell the planner how it ended, unless it was stopped on purpose."""
        script.done.set()
        self.emit(Step("script", "ended"))
        if self.script is script:
            self.script = None
        self._queued = False  # its messages go with this wake-up
        if self._message_timer:
            self._message_timer.cancel()
            self._message_timer = None
        how, *rest = script.outcome or ("killed", "it ended without saying how")
        if how == "done":
            self._wake("the script ended")
        elif how == "ask":
            self._note(f"the script asks: {rest[0]}")
            self._wake(f"the script asks: {rest[0]}")
        elif how == "error":
            e = rest[0]
            self._note(f"{e['error']}: {e.get('message', '')}\n  at line {e.get('line')}: {e.get('source_line', '')}", ok=False)
            self._wake(f"the script raised {e['error']} at line {e.get('line')}")
        elif how == "killed":
            self._note(f"the script was ended at line {script.line}: {rest[0]}", ok=False)
            self._wake(rest[0] if rest[0].startswith("the script") else f"the script was ended: {rest[0]}")
        else:
            self._note(f"the script stopped at line {script.line}: {rest[0]}")
            if self.error:
                self.on_wake()

    # --- actions -------------------------------------------------------------------------------------------

    def _context(self) -> dict:
        return {"goal": self.script.goal if self.script else "", "last_step": self._last[0], "its_result": self._last[1][:600], "recent_events": list(self._heard)}

    def _note(self, line: str, kind: str = "info", ok: bool = True) -> None:
        self._trace.append(line)
        self.emit(Step(kind, line, ok))

    def _wake_for_messages(self) -> None:
        """A script's messages wake the planner, which reads them all: after a moment (so a burst comes as one, and a script
        which ends meanwhile wakes it just once), and not more often than WAKE_EVERY."""
        if self._queued and not self._message_timer:
            wait = max(GATHER, self._woken_for_messages + WAKE_EVERY - time.monotonic())
            self._message_timer = asyncio.get_running_loop().call_later(wait, self._messages_due)

    def _messages_due(self) -> None:
        self._message_timer = None
        if self.script and self._queued:
            self._woken_for_messages, self._queued = time.monotonic(), False
            self._wake("the script sent messages (it's still running)")

    def _wake(self, why: str) -> None:
        self._why.append(why)
        self.emit(Step("wake", why))
        self.on_wake()

    async def _main(self) -> None:
        while True:
            if self.script is None:
                await self._idle()
            else:
                await self.script.done.wait()

    async def _act(self, script: Script, name: str, args: list, kwargs: dict) -> Any:
        """Run one action for a script, keeping watch for safety meanwhile. Returns its result; raises a Surprise, TypeError, or Stopped."""
        call = inspect.signature(ACTIONS[name]).bind(self.ctx, *args, **kwargs)  # a TypeError for the script
        given = dict(list(call.arguments.items())[1:])
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
            self._job = asyncio.create_task(self._perform(name, call))
            try:
                while not self._job.done():
                    await asyncio.wait({self._job}, timeout=self.poll)
                    if self._job.done():
                        break
                    if self._overtime(script):
                        self._cancel = Stopped(script.stopped)
                    elif hit := await self._danger(name, given):
                        self._cancel = hit
                    if self._cancel:
                        self._job.cancel()
                        await asyncio.wait({self._job})
            except BaseException:
                self._job.cancel()
                self.current = self._owner = None
                raise
            self.current = self._owner = None
            if self._job.cancelled():
                await self.ctx.game.release()
                raised = self._cancel or Stopped("stopped")
                self._record(job, Outcome(f"stopped partway: {type(raised).__name__}: {raised}", ok=False))
                raise raised
            if isinstance(ex := self._job.exception(), HumanControl):
                self._wake("the player took control")  # the planner pauses until it's given back
                raise script.stop("the player took control")
            if isinstance(ex, Disconnected):
                self.error = ex
                raise script.stop("the game went away")
            if ex:  # the report after the action failed
                outcome, value, raised = Outcome(f"ERROR: {type(ex).__name__}: {ex}", ok=False), None, failure(f"{type(ex).__name__}: {ex}")
            else:
                outcome, value, raised = self._job.result()
            self._record(job, outcome)
            if self.ctx.finished:
                self._wake("the day ended")
                raise script.stop("the day ended")
            if raised:
                raise raised
            if isinstance(value, dict) and value.get("needs_choice"):
                raise Question(str(value["needs_choice"]), [str(p) for p in value.get("professions", [])])
            return value

    async def _perform(self, name: str, call: inspect.BoundArguments) -> tuple[Outcome, Any, BaseException | None]:
        """Do the action. Returns what the planner reads of it, what the script gets, and what the script should raise instead."""
        g = self.ctx.game
        if self.ctx.before_action:
            await self.ctx.before_action()
        value, raised = None, None
        try:
            value = await ACTIONS[name](*call.args, **call.kwargs)
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
            logging.exception("action %s failed with %s", name, call.arguments)
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
        self._last = (job.label, outcome.text)
        self._trace.append(f"{job.label}{'' if outcome.ok else ' FAILED'}:\n{outcome.text}")
        self.emit(Step("result", f"{job}: {outcome.text}", outcome.ok, {"name": job.name, "args": job.args, **outcome.shown} if outcome.shown else {}))

    async def _stock(self, script: Script) -> list[dict]:
        async with self._lock:
            if script.stopped:  # it may have been stopped while it waited for the lock
                raise Stopped(script.stopped)
            try:
                await self.ctx.game.open_shop()
            except ActionError as ex:
                raise failure(str(ex)) from None
            return await self.ctx.game.shop_stock()

    async def _danger(self, name: str, args: dict) -> Surprise | None:
        """Low health, low energy, or the time to head home, each raised once until it clears. Actions which cure it may run."""
        if recovers(name, args):
            return None
        try:
            s = await self.ctx.game.read({"health": "player.health", "maxh": "player.max_health", "energy": "player.energy", "clock": "time.clock", "place": "location.name"})
        except BridgeError:
            return None
        found: dict[str, Surprise] = {}
        if s["health"] < LOW_HEALTH * s["maxh"]:
            found["health"] = LowHealth(f"health is low ({s['health']}/{s['maxh']})")
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
        estimate = getattr(self.ctx.game, "minutes_home", None)
        if not estimate:
            return None
        h = self._home
        if h.get("place") != s["place"] or minutes(s["clock"]) - h.get("at", -999) >= 30:
            try:
                self._home = h = {"place": s["place"], "at": minutes(s["clock"]), "walk": await estimate()}
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
