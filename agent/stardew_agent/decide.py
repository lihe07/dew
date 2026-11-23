"""A fast decision model (span-02) which says which of several described situations is the case."""

from __future__ import annotations

import httpx

GATEWAY = "https://gateway.respan.ai"


class Decider:
    """Call it with a small state and {key: description}; it returns the chosen key (None on failure) and the confidence."""

    def __init__(self, api_key: str, model: str = "span-02", timeout: float = 5):
        self.key, self.model = api_key, model
        self.client = httpx.AsyncClient(base_url=GATEWAY, timeout=timeout)

    async def __call__(self, state: dict, options: dict[str, str]) -> tuple[str | None, float]:
        try:
            r = await self.client.post("/v1/decisions", headers={"Authorization": f"Bearer {self.key}"},
                                       json={"model": self.model, "state": state, "questions": {"case": {"type": "choice", "criteria": options}}})
            r.raise_for_status()
            answer = r.json()["answers"]["case"]
            choice, confidence = answer.get("choice"), float(answer.get("confidence") or 0)
        except (httpx.HTTPError, KeyError, ValueError, TypeError, AttributeError):
            return None, 0.0
        return (choice if choice in options else None), confidence
