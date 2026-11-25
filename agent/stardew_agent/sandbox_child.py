"""The process a agent's script runs in, inside WebAssembly (see wasm_host.py). It uses only the standard library and knows nothing of the agent.

It reads {"source", "api"} as one JSON line on stdin (api: what to build the script's globals from), runs the script as ordinary Python, and talks to its parent in JSON lines:
  child -> parent  {"call": name, "self": ref, "args": [...], "kwargs": {...}, "line": n}, {"log": text}, {"done": true},
                   {"error": type, "message": text, "line": n, "source_line": text}
  parent -> child  {"value": ...} or {"raise": type, "args": [...]}
Records go both ways as {"$": class, "ref": {...}, "f": {fields}}. The parent trusts none of it: isolation comes from WebAssembly, and the parent checks every message.
"""

from __future__ import annotations

import json
import sys
import time
import traceback
from types import CodeType
from typing import Any

# --- exceptions --------------------------------------------------------------------------------------------


class Surprise(Exception):
    """Something unexpected; uncaught, it ends the script and wakes the agent."""

    fields: tuple[str, ...] = ()

    def __init__(self, *args: Any):
        super().__init__(*args)
        for name, value in zip(self.fields, args):
            setattr(self, name, value)


class ActionFailed(Surprise):
    fields = ("reason",)


class Unreachable(ActionFailed):
    pass


class NotEnough(ActionFailed):
    @property
    def what(self) -> str:
        return self.reason


class Closed(ActionFailed):
    pass


class Stuck(Surprise):
    pass


class Unsure(Surprise):
    fields = ("situation", "confidence")

    def __str__(self) -> str:
        return f"{self.situation!r} (confidence {self.confidence:.2f})"


class Danger(Surprise):
    pass


class LowHealth(Danger):
    pass


class LowEnergy(Danger):
    pass


class TimeToGoHome(Danger):
    fields = ("minutes_home", "minutes_left")

    def __str__(self) -> str:
        return f"about {self.minutes_home} minutes' walk home, {self.minutes_left} minutes until 2am"


class Happened(Surprise):
    fields = ("text",)


class Question(Happened):
    fields = ("text", "choices")

    def __str__(self) -> str:
        return self.text


class Cutscene(Happened):
    pass


EXCEPTIONS: dict[str, type[Exception]] = {c.__name__: c for c in (Surprise, ActionFailed, Unreachable, NotEnough, Closed, Unsure, Danger, LowHealth, LowEnergy, Stuck, TimeToGoHome,
                                                                   Happened, Question, Cutscene)}
REPLY_ERRORS = {**EXCEPTIONS, "TypeError": TypeError, "ValueError": ValueError}


# --- talking to the parent ---------------------------------------------------------------------------------


UNLIMITED = 1_000_000  # shops sell some things without limit
LINE = {"now": 0, "sent": 0, "at": 0.0}  # the script's line running now (from follow_lines), and the last one sent


def short(f: dict) -> str:
    """A record in a few words: its name, count, and tile."""
    name = next((str(f[k]) for k in ("name", "title", "kind") if f.get(k) is not None), "")
    if f.get("title") and f.get("name") and f["title"] != f["name"]:
        name += f" ({f['title']})"
    count = f" x{f['count']}" if isinstance(f.get("count"), int) and f["count"] < UNLIMITED else ""
    at = f"({f['x']},{f['y']})" if f.get("x") is not None and f.get("y") is not None else ""
    return f"{name}{count}" + (f" at {at}" if name and at else at)


class Record:
    """A snapshot of something in the game, read-only; its methods act on it through its reference."""

    __slots__ = ("_ref", "_f")
    _spec: dict = {}

    def __init__(self, ref: dict, f: dict):
        object.__setattr__(self, "_ref", ref)
        object.__setattr__(self, "_f", f)

    def __getattr__(self, key: str) -> Any:
        f = object.__getattribute__(self, "_f")
        if key in f:
            return f[key]
        raise AttributeError(f"{type(self).__name__} has no {key!r}")

    def __setattr__(self, key: str, value: Any) -> None:
        raise AttributeError(f"{type(self).__name__} is read-only")

    def __repr__(self) -> str:
        return f"{type(self).__name__}({short(self._f)})"

    def _key(self) -> str:
        return json.dumps(self._ref or self._f, sort_keys=True, default=str)

    def __eq__(self, other: Any) -> bool:
        return type(other) is type(self) and other._key() == self._key()

    def __hash__(self) -> int:
        return hash((type(self).__name__, self._key()))

    def wire(self) -> dict:
        return {"$": type(self).__name__, "ref": self._ref, "f": self._f}


class Live:
    """One of the script's globals, read from the game each time."""

    __slots__ = ()
    _spec: dict = {}
    _var = ""

    def __repr__(self) -> str:
        return f"<{self._var}>"


def encode(o: Any) -> Any:
    if isinstance(o, Record):
        return o.wire()
    return sorted(o, key=str) if isinstance(o, (set, frozenset)) else str(o)


class Channel:
    def __init__(self) -> None:
        self.classes: dict[str, type] = {}

    def send(self, message: dict) -> None:
        sys.stdout.write(json.dumps(message, default=encode) + "\n")
        sys.stdout.flush()

    def decode(self, d: dict) -> Any:
        cls = self.classes.get(d.get("$")) if set(d) == {"$", "ref", "f"} else None
        if cls is None or not issubclass(cls, Record):
            return d
        made = cls.__new__(cls)
        Record.__init__(made, d["ref"], d["f"])
        return made

    def call(self, name: str, me: Any, args: tuple, kwargs: dict) -> Any:
        self.send({"call": name, "self": me._ref if isinstance(me, Record) else None, "args": list(args), "kwargs": kwargs, "line": LINE["now"]})
        reply = json.loads(sys.stdin.readline() or '{"raise": "Surprise", "args": ["the parent went away"]}', object_hook=self.decode)
        if "raise" in reply:
            raise REPLY_ERRORS.get(reply["raise"], Surprise)(*reply.get("args", []))
        return reply.get("value")


def namespace(channel: Channel, api: dict) -> dict[str, Any]:
    """The script's globals, built from the parent's description of the API: functions and live objects whose calls the parent
    carries out, and the classes of the records it sends."""

    def function(wire: str, sig: str, doc: str, owner: str | None = None) -> Any:
        if owner is None:
            def call(*args: Any, **kwargs: Any) -> Any:
                return channel.call(wire, None, args, kwargs)
        else:
            def call(self: Any, *args: Any, **kwargs: Any) -> Any:
                return channel.call(wire, self, args, kwargs)
        call.__name__ = wire.rpartition(".")[2]
        call.__qualname__ = f"{owner.lower()}.{call.__name__}" if owner else wire
        call.__doc__, call._sig = doc, sig
        return call

    class Tile(Record):
        __slots__ = ()

        def __init__(self, x: int, y: int, place: str | None = None):
            Record.__init__(self, {}, {"x": x, "y": y, "place": place})

        tile = property(lambda self: self)
        location = property(lambda self: channel.call("Places.get", None, (self.place,), {}) if self.place else globals_["here"])

        def __iter__(self) -> Any:
            return iter((self.x, self.y))

        def __repr__(self) -> str:
            return f"Tile({self.x}, {self.y}" + (f", {self.place!r})" if self.place else ")")

    class Area(Record):
        __slots__ = ()

        def __init__(self, x1: int, y1: int, x2: int, y2: int, place: str | None = None):
            Record.__init__(self, {}, {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "place": place})

        @property
        def tiles(self) -> list:
            return [Tile(x, y, self.place) for y in range(min(self.y1, self.y2), max(self.y1, self.y2) + 1) for x in range(min(self.x1, self.x2), max(self.x1, self.x2) + 1)]

        @property
        def center(self) -> Tile:
            return Tile((self.x1 + self.x2) // 2, (self.y1 + self.y2) // 2, self.place)

        tile = center

        def __contains__(self, tile: Any) -> bool:
            x, y = tile
            return min(self.x1, self.x2) <= x <= max(self.x1, self.x2) and min(self.y1, self.y2) <= y <= max(self.y1, self.y2)

        def __iter__(self) -> Any:
            return iter(self.tiles)

        def __len__(self) -> int:
            return (abs(self.x2 - self.x1) + 1) * (abs(self.y2 - self.y1) + 1)

        def __repr__(self) -> str:
            return f"Area({self.x1}, {self.y1}, {self.x2}, {self.y2}" + (f", {self.place!r})" if self.place else ")")

    made: dict[str, type] = {"Tile": Tile, "Area": Area}
    for name, c in api["classes"].items():
        if name in made:
            made[name]._spec = c
            continue
        ns: dict[str, Any] = {"__slots__": (), "__doc__": c["doc"], "_spec": c}
        for m, (sig, doc) in c["methods"].items():
            ns[m] = function(f"{name}.{m}", sig, doc, name)
        for f, doc in c["live_fields"].items():
            ns[f] = property(lambda self, w=f"{name}.{f}": channel.call(w, self, (), {}), doc=doc)
        if c["each"]:
            ns["__iter__"] = lambda self, w=f"{name}.each": iter(channel.call(w, self, (), {}))
            ns["__len__"] = lambda self, w=f"{name}.each": len(channel.call(w, self, (), {}))
        if c["get"]:
            ns["__getitem__"] = lambda self, key, w=f"{name}.get": channel.call(w, self, (key,), {})
        made[name] = type(name, (Live if c["single"] else Record,), ns)
    channel.classes = made

    def print_(*parts: Any, sep: str = " ", **_: Any) -> None:
        """print: a message to the agent, which wakes it."""
        channel.send({"log": str(sep).join(str(p) for p in parts)})

    def help_(thing: Any = None) -> None:
        """help(go), help(chest), help(chest.put): what it is, as a message (the real help() would write over the parent's channel)."""
        if hasattr(thing, "_sig"):
            return print_(f"{thing.__qualname__}{thing._sig}: {thing.__doc__}")
        c = getattr(thing, "_spec", None)
        if not c:
            return print_("help(x) describes a function or object of the SDK; the SDK is listed in your system prompt")
        fields = {**c["fields"], **c["live_fields"]}
        lines = [f"{type(thing).__name__ if not isinstance(thing, type) else thing.__name__}: {c['doc']}"]
        lines += ["fields: " + ", ".join(f"{k} ({v})" if v else k for k, v in fields.items())] if fields else []
        lines += [f"  .{m}{sig}: {doc}" for m, (sig, doc) in c["methods"].items()]
        print_("\n".join(lines))

    globals_: dict[str, Any] = {name: function(name, sig, doc) for name, (sig, doc) in api["functions"].items()}
    for var, name in api["globals"].items():
        instance = made[name]()
        type(instance)._var = var
        globals_[var] = instance
    globals_.update({"Tile": Tile, "Area": Area, "print": print_, "help": help_, **EXCEPTIONS})
    return globals_


# --- running ----------------------------------------------------------------------------------------------


def main() -> None:
    start = json.loads(sys.stdin.readline())
    source = start["source"]
    channel = Channel()
    try:
        code = compile(source, "<script>", "exec")
    except SyntaxError as ex:
        channel.send({"error": "SyntaxError", "message": str(ex.msg), "line": ex.lineno or 0, "source_line": (ex.text or "").strip()})
        return
    globals_ = {"__name__": "__script__", **namespace(channel, start["api"])}
    follow_lines(code, channel)
    try:
        exec(code, globals_)
    except BaseException as ex:  # everything goes to the agent
        where = line_of(ex)
        rows = source.splitlines()
        channel.send({"error": type(ex).__name__, "message": str(ex)[:500], "line": where, "source_line": rows[where - 1].strip() if 0 < where <= len(rows) else ""})
    else:
        channel.send({"done": True})


LINE_EVERY = 0.2  # seconds: at most this often, the parent hears which line runs


def follow_lines(code: CodeType, channel: Channel) -> None:
    """Follow which line of the script runs, pure Python too: sys.monitoring's LINE events on the script's own code (its
    functions and lambdas too; not the SDK or the standard library). The parent hears of a change at most every LINE_EVERY,
    and with every SDK call."""
    mon = sys.monitoring
    tool = mon.OPTIMIZER_ID

    def on_line(_code: CodeType, line: int) -> None:
        LINE["now"] = line
        if line != LINE["sent"] and (now := time.monotonic()) - LINE["at"] >= LINE_EVERY:
            LINE.update(sent=line, at=now)
            channel.send({"line": line})

    mon.use_tool_id(tool, "lines")
    mon.register_callback(tool, mon.events.LINE, on_line)
    todo = [code]
    while todo:
        c = todo.pop()
        mon.set_local_events(tool, c, mon.events.LINE)
        todo += [k for k in c.co_consts if isinstance(k, CodeType)]


def line_of(ex: BaseException) -> int:
    return next((f.lineno for f in reversed(traceback.extract_tb(ex.__traceback__)) if f.filename == "<script>"), 0) or 0


if __name__ == "__main__":
    main()
