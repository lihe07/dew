"""The agent's documentation: a tree of Markdown files in docs/, read with the docs tool and searched with search_docs.

The system prompt lists only the root topics; each doc links to deeper ones by their path, like `docs("farming/crops")`.
Each file starts with `# Title` and then a one-line summary; sections are `## Heading`.
"""

from __future__ import annotations

import re
from pathlib import Path

DOCS = Path(__file__).parent / "docs"
MAX_LINES = 40  # matching lines search_docs returns


def topics(folder: Path = DOCS) -> dict[str, Path]:
    """Every doc by its path without .md, like "farming" or "farming/crops"."""
    found = {p.relative_to(folder).with_suffix("").as_posix(): p for p in folder.rglob("*.md")}
    return dict(sorted(found.items()))


def read(topic: str, folder: Path = DOCS) -> str:
    """A topic, or one section of it with topic#heading."""
    name, _, section = topic.strip().strip("/").partition("#")
    name = name.removesuffix(".md").removeprefix("docs/")
    found = topics(folder)
    if name not in found:
        near = [t for t in found if t.split("/")[0] == name.split("/")[0]] or [t for t in found if "/" not in t]
        return f"no topic {name!r}; try: {', '.join(near)}"
    text = found[name].read_text()
    if not section:
        return text
    want = section.strip().lower()
    parts = re.split(r"(?m)^(?=## )", text)
    for part in parts:
        heading = part.splitlines()[0].lstrip("# ").strip().lower() if part.strip() else ""
        if heading == want or (want in heading and part.startswith("## ")):
            return part.strip()
    headings = [p.splitlines()[0].lstrip("# ").strip() for p in parts if p.startswith("## ")]
    return f"no section {section!r} in {name}; its sections are: {', '.join(headings)}"


def grep(pattern: str, folder: Path = DOCS) -> str:
    """Lines matching a pattern (a regular expression, or plain text if it isn't one), with their topic and section."""
    try:
        match = re.compile(pattern, re.I).search
    except re.error:
        needle = pattern.lower()
        match = lambda line: needle in line.lower()  # noqa: E731
    hits: list[str] = []
    for name, path in topics(folder).items():
        section = ""
        for line in path.read_text().splitlines():
            if line.startswith("## "):
                section = line[3:].strip()
            if line.strip() and match(line):
                hits.append(f"{name}#{section}: {line.strip()}" if section else f"{name}: {line.strip()}")
    if not hits:
        return f"nothing matches {pattern!r}"
    more = f"\n… {len(hits) - MAX_LINES} more; narrow the search" if len(hits) > MAX_LINES else ""
    return "\n".join(hits[:MAX_LINES]) + more


def entry(name: str, folder: Path = DOCS) -> str | None:
    """Where an SDK call is documented: topic#section, the section named after it (like `## go` or `## Chest.put`)."""
    for topic, path in topics(folder).items():
        if re.search(rf"(?m)^## {re.escape(name)}$", path.read_text()):
            return f"{topic}#{name}"
    return None
