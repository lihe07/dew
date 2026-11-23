"""The planner's web tool: a small research subagent (hy4-flash by default) which searches and reads pages through TinyFish,
then answers in a few sentences, so the planner's own context never fills with web pages."""

from __future__ import annotations

import json
from typing import Any

import httpx

SEARCH, FETCH = "https://api.search.tinyfish.ai", "https://api.fetch.tinyfish.ai"
MAX_STEPS = 6  # model calls per question
PAGE = 8000  # characters of a fetched page the subagent sees

TOOLS = [
    {"name": "search", "description": "Search the web. Results are titles, snippets, and urls.",
     "input_schema": {"type": "object", "required": ["query"], "properties": {
         "query": {"type": "string"},
         "site": {"type": "string", "description": "Only this domain, e.g. stardewvalleywiki.com"}}}},
    {"name": "fetch", "description": f"Read a page as markdown (the first {PAGE} characters, or the parts around `find`).",
     "input_schema": {"type": "object", "required": ["url"], "properties": {
         "url": {"type": "string"},
         "find": {"type": "string", "description": "Only the parts of the page around this text, e.g. 'Loved Gifts'"}}}},
]

PROMPT = """Answer this question about the game Stardew Valley (version 1.6), for a player in the middle of a game:

{question}

Search (prefer stardewvalleywiki.com), read the page if the snippets aren't enough, then reply with only the facts that answer it, in under 120 words. If you can't find it, say so."""


class Research:
    """Ask a question; a small model searches and reads, then answers."""

    def __init__(self, client: Any, model: str, tinyfish_key: str, timeout: float = 150):
        self.client, self.model = client, model
        self.http = httpx.AsyncClient(headers={"X-API-Key": tinyfish_key}, timeout=timeout)

    async def answer(self, question: str) -> tuple[str, list[str]]:
        """The answer, and what the subagent did (for display)."""
        messages: list[dict] = [{"role": "user", "content": PROMPT.format(question=question)}]
        did: list[str] = []
        for _ in range(MAX_STEPS):
            last = len(did) >= MAX_STEPS - 1
            response = await self.client.messages.create(model=self.model, max_tokens=1200, messages=messages, thinking={"type": "disabled"},
                                                          **({} if last else {"tools": TOOLS}))
            calls = [b for b in response.content if b.type == "tool_use"]
            if not calls:
                return "\n".join(b.text for b in response.content if b.type == "text").strip() or "nothing found", did
            messages.append({"role": "assistant", "content": [b.model_dump() for b in response.content if b.type in ("text", "tool_use")]})
            results = []
            for call in calls:
                did.append(f"{call.name} {json.dumps(call.input, ensure_ascii=False)[:120]}")
                results.append({"type": "tool_result", "tool_use_id": call.id, "content": await self._tool(call.name, call.input)})
            messages.append({"role": "user", "content": results})
        return "no answer in time", did

    async def _tool(self, name: str, args: dict) -> str:
        try:
            if name == "search":
                return await self.search(str(args.get("query", "")), args.get("site"))
            if name == "fetch":
                return await self.fetch(str(args.get("url", "")), args.get("find"))
        except (httpx.HTTPError, ValueError, KeyError) as ex:
            return f"failed: {type(ex).__name__}: {str(ex)[:200]}"
        return f"no tool {name}"

    async def search(self, query: str, site: str | None = None) -> str:
        params = {"query": query[:2000], **({"include_domains": site} if site else {})}
        r = await self.http.get(SEARCH, params=params)
        r.raise_for_status()
        found = r.json().get("results") or []
        return "\n".join(f"{x.get('title')} — {x.get('url')}\n  {(x.get('snippet') or '')[:300]}" for x in found[:6]) or "no results"

    async def fetch(self, url: str, find: str | None = None) -> str:
        r = await self.http.post(FETCH, json={"urls": [url], "format": "markdown"})
        r.raise_for_status()
        data = r.json()
        if not data.get("results"):
            errors = data.get("errors") or [{}]
            return f"couldn't read it: {errors[0].get('error', 'unknown error')}"
        text = data["results"][0].get("text") or ""
        if find:
            text = around(text, find) or text
        return text[:PAGE] + ("\n…" if len(text) > PAGE else "")


def around(text: str, find: str, width: int = 1500) -> str:
    """The parts of a page around each mention of find."""
    low, needle = text.lower(), find.lower()
    parts, at = [], low.find(needle)
    while at >= 0 and len(parts) < 4:
        parts.append(text[max(0, at - 200):at + width])
        at = low.find(needle, at + width)
    return "\n…\n".join(parts)
