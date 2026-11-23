"""The process a planner's script runs in, inside WebAssembly (see wasm_host.py). It uses only the standard library and knows nothing of the agent.

It reads {"source"} as one JSON line on stdin, runs the script as ordinary Python, and talks to its parent in JSON lines:
  child -> parent  {"call": name, "args": [...], "kwargs": {...}, "line": n}, {"log": text}, {"done": true}, {"ask": question, "line": n},
                   {"error": type, "message": text, "line": n, "source_line": text}
  parent -> child  {"value": ...} or {"raise": type, "args": [...]}
The parent trusts none of it: isolation comes from WebAssembly, and the parent checks every message.
"""

from __future__ import annotations

import json
import sys
import traceback
from typing import Any

# --- exceptions --------------------------------------------------------------------------------------------


class Surprise(Exception):
    """Something unexpected; uncaught, it ends the script and wakes the planner."""

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


class StopIf(Happened):
    fields = ("situation",)


class Ask(BaseException):
    """The script asked the planner something; it ends here."""


EXCEPTIONS: dict[str, type[Exception]] = {c.__name__: c for c in (Surprise, ActionFailed, Unreachable, NotEnough, Closed, Unsure, Danger, LowHealth, LowEnergy, TimeToGoHome,
                                                                   Happened, Question, Cutscene, StopIf)}
REPLY_ERRORS = {**EXCEPTIONS, "TypeError": TypeError, "ValueError": ValueError}


# --- talking to the parent ---------------------------------------------------------------------------------


class Item:
    """An item as scripts see it; read-only."""

    __slots__ = ("name", "count", "price", "energy", "healing", "category")

    def __init__(self, fields: dict):
        for key in self.__slots__:
            object.__setattr__(self, key, fields.get(key))

    def __setattr__(self, key: str, value: Any) -> None:
        raise AttributeError("items are read-only")

    def __repr__(self) -> str:
        return f"Item({self.name} x{self.count})"

    def wire(self) -> dict:
        return {"item": {k: getattr(self, k) for k in self.__slots__}}


def line() -> int:
    """The script's line being run."""
    frame = sys._getframe(1)
    while frame and frame.f_code.co_filename != "<script>":
        frame = frame.f_back
    return frame.f_lineno if frame else 0


class Channel:
    def send(self, message: dict) -> None:
        sys.stdout.write(json.dumps(message, default=lambda o: o.wire() if isinstance(o, Item) else str(o)) + "\n")
        sys.stdout.flush()

    def call(self, name: str, args: tuple, kwargs: dict) -> Any:
        self.send({"call": name, "args": list(args), "kwargs": kwargs, "line": line()})
        reply = json.loads(sys.stdin.readline() or '{"raise": "Surprise", "args": ["the parent went away"]}', object_hook=lambda d: Item(d["item"]) if set(d) == {"item"} else d)
        if "raise" in reply:
            raise REPLY_ERRORS.get(reply["raise"], Surprise)(*reply.get("args", []))
        return reply.get("value")


SDK = ["go", "harvest", "water", "till", "plant", "place", "clear", "chop", "break_rocks", "forage", "pet", "machines", "fish", "mine", "fight", "rewards",
       "buy", "sell", "eat", "ship", "use", "craft", "trash", "hold", "put", "take", "talk", "answer", "skip_cutscene", "wait_until", "soak", "sleep", "screen",
       "money", "time", "minutes_until_2am", "health", "energy", "here", "people_here", "shop_stock", "check",
       "state", "look_map", "find", "people", "quests", "recipes", "item_info", "exits", "tile_info", "screenshot", "web"]


def namespace(channel: Channel) -> dict[str, Any]:
    """The script's globals: proxies for the SDK, which the parent carries out."""
    def proxy(name: str) -> Any:
        def call(*args: Any, **kwargs: Any) -> Any:
            return channel.call(name, args, kwargs)
        call.__name__ = name
        return call

    Backpack = type("Backpack", (), {"__doc__": "Your backpack, read live: .items, .food, .fish, .free, .full.",
                                     **{part: property(lambda self, part=part: channel.call("backpack", (part,), {})) for part in ("items", "food", "fish", "free", "full")}})

    def pick(description: str, options: Any) -> Any:
        options = list(options)
        return options[channel.call("pick", (description, options), {})]

    def log(*parts: Any, **_: Any) -> None:
        channel.send({"log": " ".join(str(p) for p in parts)})

    def ask(question: str) -> None:
        raise Ask(str(question))

    return {**{name: proxy(name) for name in SDK}, "backpack": Backpack(), "pick": pick, "log": log, "ask": ask, **EXCEPTIONS}


# --- running ----------------------------------------------------------------------------------------------


def main() -> None:
    source = json.loads(sys.stdin.readline())["source"]
    channel = Channel()
    try:
        code = compile(source, "<script>", "exec")
    except SyntaxError as ex:
        channel.send({"error": "SyntaxError", "message": str(ex.msg), "line": ex.lineno or 0, "source_line": (ex.text or "").strip()})
        return
    sdk = namespace(channel)
    globals_ = {"__name__": "__script__", **sdk, "print": sdk["log"]}
    try:
        exec(code, globals_)
    except Ask as ex:
        channel.send({"ask": str(ex), "line": line_of(ex)})
    except BaseException as ex:  # everything goes to the planner
        where = line_of(ex)
        rows = source.splitlines()
        channel.send({"error": type(ex).__name__, "message": str(ex)[:500], "line": where, "source_line": rows[where - 1].strip() if 0 < where <= len(rows) else ""})
    else:
        channel.send({"done": True})


def line_of(ex: BaseException) -> int:
    return next((f.lineno for f in reversed(traceback.extract_tb(ex.__traceback__)) if f.filename == "<script>"), 0) or 0


if __name__ == "__main__":
    main()
