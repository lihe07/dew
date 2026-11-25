"""Connection to the Stardew Bridge mod, and its seven instructions."""

from __future__ import annotations

import asyncio
import json
import os
import struct
from collections.abc import Callable
from pathlib import Path
from typing import Any

DEFAULT_GAME = Path.home() / "Disk1/SteamLibrary/steamapps/common/Stardew Valley"


class BridgeError(Exception):
    """The mod rejected or couldn't finish an instruction."""


class Disconnected(BridgeError):
    """The game closed, or isn't running."""


class HumanControl(BridgeError):
    """The player took control."""


Op = dict[str, Any]


def input(*, buttons: list[str] | None = None, tile: tuple[float, float] | None = None, ui: tuple[float, float] | None = None,
          screen: tuple[float, float] | None = None, scroll: int | None = None, text: str | None = None, clear: bool = False) -> Op:
    """Set which buttons are down, where the cursor is, the wheel, or typed text. Omitted fields keep their state."""
    op: Op = {"op": "input"}
    if buttons is not None:
        op["buttons"] = buttons
    for key, at in (("tile", tile), ("ui", ui), ("screen", screen)):
        if at is not None:
            op["cursor"] = {key: list(at)}
    if scroll is not None:
        op["scroll"] = scroll
    if text is not None:
        op.update(text=text, clear=clear)
    return op


def step(n: int = 1, until: dict[str, str] | str | None = None) -> Op:
    """Run up to n ticks, stopping early if a condition becomes true."""
    return {"op": "step", "n": n, "until": until}


def read(what: Any) -> Op:
    """Read expressions, queries, grids, or game data."""
    return {"op": "read", "what": what}


def frame(shrink: int = 1) -> Op:
    return {"op": "frame", "shrink": shrink}


def path(goals: list, near: int = 0, **options: Any) -> Op:
    return {"op": "path", "goals": [list(g) for g in goals], "near": near, **options}


def follow(route: list, until: dict[str, str] | None = None, n: int = 3600) -> Op:
    return {"op": "follow", "path": route, "until": until, "n": n}


def find_session() -> tuple[int, str]:
    """Find the running mod's port and token."""
    override = os.environ.get("STARDEW_SESSION")
    game = Path(os.environ.get("STARDEW_PATH", DEFAULT_GAME))
    file = Path(override) if override else game / "Mods/StardewBridge/session.json"
    try:
        data = json.loads(file.read_text())
    except FileNotFoundError:
        raise Disconnected(f"no session file at {file}; is the Stardew Bridge mod installed and the game running?") from None
    return data["port"], data["token"]


class Driver:
    """Sends instruction batches to the mod. Each batch runs in order and stops at the first failure."""

    def __init__(self, port: int, token: str):
        self.port, self.token = port, token
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self.on_journal: list[Callable[[dict], None]] = []
        self.human = False
        self._next = 0
        self._waiting: dict[int, asyncio.Future] = {}
        self._tasks: list[asyncio.Task] = []
        self._lock = asyncio.Lock()

    async def connect(self, *, speed: int = 4, steal: bool = False) -> dict:
        try:
            self.reader, self.writer = await asyncio.open_connection("127.0.0.1", self.port, limit=1 << 26)
        except OSError:
            raise Disconnected(f"the game isn't listening on port {self.port}") from None
        self._tasks.append(asyncio.create_task(self._receive()))
        await self._request({"token": self.token, "steal": steal})
        self._tasks.append(asyncio.create_task(self._heartbeat()))
        return (await self.run({"op": "attach", "speed": speed, "ttl_ms": 15000}))[0]

    async def close(self) -> None:
        for task in self._tasks:
            task.cancel()
        if self.writer:
            self.writer.close()

    async def run(self, *ops: Op) -> list[Any]:
        """Run instructions, and return their results."""
        async with self._lock:
            message = {"ops": list(ops)}
            try:
                reply = await self._request(message)
            except asyncio.CancelledError:
                # stop the farmer now: this request, whether it runs already or still waits its turn in the game
                if "id" in message:
                    asyncio.ensure_future(self._quietly({"cancel": message["id"]}))
                raise
        return reply["results"]

    async def one(self, op: Op) -> Any:
        return (await self.run(op))[-1]

    async def read(self, what: Any) -> Any:
        """Read at once, even while an action runs (the game answers reads on a separate lane)."""
        return await self.look(read(what))

    async def look(self, *ops: Op) -> Any:
        """Run read and path instructions beside any running action. Returns the last result."""
        reply = await self._request({"ops": list(ops), "lane": "read"})
        return reply["results"][-1]

    async def _quietly(self, message: dict) -> None:
        try:
            await self._request(message)
        except BridgeError:
            pass

    async def _request(self, message: dict) -> dict:
        if not self.writer or self.writer.is_closing():
            raise Disconnected("not connected to the game")
        self._next += 1
        message["id"] = self._next
        future = asyncio.get_running_loop().create_future()
        self._waiting[self._next] = future
        body = json.dumps(message).encode()
        self.writer.write(struct.pack(">I", len(body)) + body)
        try:
            await self.writer.drain()
            reply = await future
        except ConnectionError:
            raise Disconnected("lost the connection to the game") from None
        finally:
            self._waiting.pop(message["id"], None)
        if message.get("lane") != "read" or "human" in reply:  # every reply says who has control (reads too, so it's noticed within a second)
            self.human = bool(reply.get("human"))
        for entry in reply.get("journal", []):
            for listener in self.on_journal:
                listener(entry)
        if error := reply.get("error"):
            if "player" in error and "control" in error:
                raise HumanControl(error)
            raise BridgeError(error)
        if "results" not in reply:
            raise BridgeError(f"malformed reply: {str(reply)[:200]}")
        return reply

    async def _receive(self) -> None:
        assert self.reader
        try:
            while True:
                length = struct.unpack(">I", await self.reader.readexactly(4))[0]
                reply = json.loads(await self.reader.readexactly(length))
                future = self._waiting.get(reply.get("id"))
                if future and not future.done():
                    future.set_result(reply)
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            for future in self._waiting.values():
                if not future.done():
                    future.set_exception(Disconnected("the game closed the connection"))
            if self.writer:
                self.writer.close()

    async def _heartbeat(self) -> None:
        while True:
            await asyncio.sleep(3)
            # never wait forever on one lost reply: the next beat matters more
            try:
                await asyncio.wait_for(self._quietly({"ping": True}), 5)
            except asyncio.TimeoutError:
                continue
