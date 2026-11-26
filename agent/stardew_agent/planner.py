"""The planner: once a day, at night, it reads the record of the day the agent played, writes the day's diary, tidies the
agent's memory, keeps the long-term goals (the Community Center, money, friendships, the mines, achievements...), and writes
the plan for tomorrow, which the agent wakes up to. Its conversation is kept from night to night (each past night's record cut
down to the diary it wrote), so it sees the whole game while the agent sees only today."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .brain import FORGET, REMEMBER, WEB
from .memory import Memory, write_atomic
from .storage import clean
from .web import Research

PROMPT = (Path(__file__).parent / "prompts" / "planner.md").read_text()
PLAN = {"name": "plan", "description": "Finish the night: the diary of the day, the long-term goals as they stand now (a short document you rewrite each night: what the farm works toward, the progress on each, what's next), and the plan for tomorrow, which the agent reads when it wakes.",
        "input_schema": {"type": "object", "additionalProperties": False, "required": ["diary", "goals", "tomorrow"], "properties": {
            "diary": {"type": "string", "maxLength": 1500}, "goals": {"type": "string", "maxLength": 4000}, "tomorrow": {"type": "string", "maxLength": 3000}}}}
TOOLS = [WEB, REMEMBER, FORGET, PLAN]
NIGHTS = 14  # nights of conversation kept; the goals and the diary carry what's older
NIGHT_OF = "Night of "
TODAY = "# Today's plan (from the planner, last night)"  # its heading in the morning report
RECORD = 120_000  # characters of the day's record the planner reads at most; the end of the day is kept
SAID, RESULT = 1500, 600  # characters kept of each thought or script, and of each thing that came back


def record(messages: list[dict]) -> str:
    """The agent's day as plain text, for the planner: its thoughts, scripts and tool calls in full or nearly, what came back cut short."""
    lines: list[str] = []
    for m in messages:
        for b in m["content"] if isinstance(m["content"], list) else [{"type": "text", "text": str(m["content"])}]:
            kind = b.get("type")
            if kind == "text" and b["text"].strip():
                said = b["text"].strip()
                lines.append(f"AGENT: {said[:SAID]}" if m["role"] == "assistant" else f"[{said[:RESULT]}]")
            elif kind == "tool_use":
                args = b.get("input") or {}
                if b["name"] == "run":
                    lines.append(f"RUN ({args.get('goal', '')}):\n{str(args.get('script', ''))[:SAID]}")
                elif b["name"] != "wait":
                    lines.append(f"{b['name'].upper()} {json.dumps(args, ensure_ascii=False)[:SAID]}")
            elif kind == "tool_result":
                parts = b["content"] if isinstance(b["content"], list) else [{"type": "text", "text": str(b["content"])}]
                said = " ".join(p.get("text", "(screenshot)") for p in parts).strip()
                if said:
                    lines.append(f"-> {said[:RESULT]}")
    out = "\n".join(lines)
    return out if len(out) <= RECORD else f"(the start of the day is left out)\n…{out[-RECORD:]}"


class Planner:
    def __init__(self, client: Any, model: str, file: Path, *, research: Research | None = None, effort: str = "high",
                 show: Callable[..., None] = lambda kind, text, data=None, ok=True: None):
        self.client, self.model, self.file, self.research, self.effort, self.show = client, model, file, research, effort, show
        saved = json.loads(file.read_text()) if file.exists() else {}
        self.messages: list[dict] = saved.get("messages", [])
        self.goals: str = saved.get("goals", "")
        self.tomorrow: str = saved.get("tomorrow", "")
        self.diary = ""  # tonight's, once the night is planned
        self.tokens = 0  # input tokens used tonight

    async def night(self, date: str, day: str, memory: Memory) -> str:
        """Think about the day that ended (its record, numbers and the farmer's state, in `day`) and the memory; edits the memory and
        leaves the goals and tomorrow's plan in self.goals and self.tomorrow. Returns the day's diary, or "" if the night failed,
        which keeps last night's goals and plan (and whatever memory edits it made)."""
        self.tokens, self.diary = 0, ""
        self._forget_old_nights()
        if self.messages and self.messages[-1]["role"] == "user":  # last night broke off partway
            self.messages.append({"role": "assistant", "content": [{"type": "text", "text": "(the night broke off)"}]})
        before = len(self.messages)
        # the day's record is big and every turn of the night resends it, so it's cached
        self.messages.append({"role": "user", "content": [{"type": "text", "text": f"{NIGHT_OF}{date}.\n\n{day}\n\n{memory.section()}", "cache_control": {"type": "ephemeral"}}]})
        try:
            for _ in range(30):  # a night ends when it calls plan; this only stops a model that never does
                response = await self.client.messages.create(
                    model=self.model, max_tokens=8000, tools=TOOLS, system=PROMPT, messages=self.messages,
                    **({"thinking": {"type": "disabled"}} if self.effort == "off" else {"extra_body": {"output_config": {"effort": self.effort}}}))
                self.tokens += (response.usage.input_tokens or 0) + (getattr(response.usage, "cache_read_input_tokens", 0) or 0)
                kept = [b.model_dump(exclude_none=True) for b in response.content if b.type in ("text", "tool_use")]
                self.messages.append({"role": "assistant", "content": kept or [{"type": "text", "text": "(thinking)"}]})
                calls = [b for b in response.content if b.type == "tool_use"]
                if not calls:
                    self.messages.append({"role": "user", "content": [{"type": "text", "text": "Finish the night by calling plan."}]})
                    continue
                results, done = [], False
                for call in calls:
                    text = await self._tool(call.name, call.input if isinstance(call.input, dict) else {}, memory)
                    done |= call.name == "plan" and text == "planned"
                    results.append({"type": "tool_result", "tool_use_id": call.id, "content": [{"type": "text", "text": text}]})
                self.messages.append({"role": "user", "content": results})
                if done:
                    self.messages.append({"role": "assistant", "content": [{"type": "text", "text": "(the night is planned)"}]})  # so tomorrow night opens with its own message
                    self.messages[before]["content"] = [{"type": "text", "text": f"{NIGHT_OF}{date}.\n\n(The day's record is left out now; your diary of it:)\n{self.diary}"}]
                    return self.diary
            self.show("info", "the planner didn't finish its plan; the agent gets last night's")
        except Exception as ex:  # the gateway may be down; the morning goes on with the old plan
            self.show("error", f"the planner failed: {str(ex)[:200]}")
        del self.messages[before:]  # an unfinished night is left out, so the conversation stays well-formed and small
        return ""

    async def _tool(self, name: str, args: dict, memory: Memory) -> str:
        if name == "plan":
            diary, goals, tomorrow = (str(args.get(k, "")).strip() for k in ("diary", "goals", "tomorrow"))
            if not diary or not goals or not tomorrow:
                return "plan needs the diary, the goals, and tomorrow's plan"
            self.diary, self.goals, self.tomorrow = diary, goals, tomorrow
            self.show("notes", f"planner, goals:\n{goals}")
            self.show("notes", f"planner, tomorrow:\n{tomorrow}")
            return "planned"
        if name in ("remember", "forget"):
            ok, text = memory.use(name, args)
            self.show("notes", *Memory.note(name, args, ok, text), ok)
            return text
        if name == "web":
            if not self.research:
                return "web lookups are off"
            self.show("agent", "researching", {"question": str(args.get("question", ""))})
            answer = await self.research.answer(str(args.get("question", "")))
            self.show("web", answer, {"question": args.get("question")})
            return answer
        return f"no tool {name}"

    def _forget_old_nights(self) -> None:
        """Keep the last NIGHTS nights of conversation. (The cache is cold by night anyway, so cutting the front costs nothing.)"""
        starts = [i for i, m in enumerate(self.messages) if m["role"] == "user" and isinstance(m["content"], list)
                  and m["content"] and m["content"][0].get("type") == "text" and m["content"][0]["text"].startswith(NIGHT_OF)]
        if len(starts) >= NIGHTS:
            self.messages = self.messages[starts[len(starts) - NIGHTS + 1]:]

    def morning(self) -> str:
        """What the agent reads of the planner in its morning report."""
        parts = []
        if self.goals:
            parts.append("# Long-term goals (the planner's)\n" + self.goals)
        if self.tomorrow:
            parts.append(f"{TODAY}\n{self.tomorrow}")
        return "\n\n".join(parts)

    def save(self) -> None:
        write_atomic(self.file, json.dumps({"goals": self.goals, "tomorrow": self.tomorrow, "messages": clean(self.messages)}, default=str, ensure_ascii=False))
