"""Facts about a finished run, assembled by code: who interacted, and what each agent is told about its part."""

from collections.abc import Sequence

from bikeshed.board import SYSTEM, Entry
from bikeshed.view import Who, render_entry


def interactions(
    entries: Sequence[Entry], file_authors: Sequence[tuple[str, str]], agent_ids: Sequence[str]
) -> dict[str, set[str]]:
    """For each member, the other members it interacted with: a ref either way, or a shared workspace file."""
    members = set(agent_ids)
    pairs: dict[str, set[str]] = {agent_id: set() for agent_id in agent_ids}

    def link(x: str, y: str) -> None:
        if x != y and x in members and y in members:
            pairs[x].add(y)
            pairs[y].add(x)

    authors = {entry.id: entry.author for entry in entries}
    for entry in entries:
        for ref in entry.refs:
            if ref in authors:
                link(entry.author, authors[ref])
    writers: dict[str, set[str]] = {}
    for path, author in file_authors:
        writers.setdefault(path, set()).add(author)
    for group in writers.values():
        for x in group:
            for y in group:
                link(x, y)
    return pairs


def feedback_packet(
    agent_id: str, *, run_id: str, goal: str, entries: Sequence[Entry], who: Who, answer: str,
    failures: Sequence[tuple[int, str, str]], briefs: str = "",
) -> str:
    by_id = {entry.id: entry for entry in entries}
    closed = next((entry for entry in reversed(entries) if entry.type == "run_closed"), None)
    reason = closed.payload.get("reason", "unknown") if closed else "unknown"
    winning = by_id.get(closed.payload.get("call_id")) if closed else None

    lines = [f"Run {run_id}", f"Goal: {goal}", f"The run ended by {reason}.", "", "## What you posted"]
    mine = [entry for entry in entries if entry.author == agent_id]
    if not mine:
        lines.append("(nothing)")
    for entry in mine:
        lines.append(render_entry(entry, who))
        for other in entries:
            if entry.id in other.refs and other.author not in (agent_id, SYSTEM):
                lines.append(f"  ← #{other.id} {who(other.author)} {other.type}")

    lines += ["", "## Credits you received"]
    received = [
        (call, credit)
        for call in entries if call.type == "call_consensus"
        for credit in call.payload.get("credits", []) if credit.get("agent_id") == agent_id
    ]
    for call, credit in received:
        mark = " (the winning call)" if winning and call.id == winning.id else ""
        lines.append(f"- from {who(call.author)} in call #{call.id}{mark}: {credit.get('contribution', '')}")
    if not received:
        lines.append("(none)")

    lines += ["", "## The winning call"]
    if winning:
        cited = [ref for ref in winning.refs if ref in by_id and by_id[ref].author == agent_id]
        listed = ", ".join(f"#{ref}" for ref in cited) or "none"
        lines.append(f"Call #{winning.id} by {who(winning.author)} cited your entries: {listed}")
    else:
        lines.append("The run ended without consensus.")

    lines += ["", "## The outcome", answer.strip() or "(no report)", "", "## Your failed steps"]
    lines += [f"- round {rnd}: {kind}: {detail}" for rnd, kind, detail in failures] or ["(none)"]
    if briefs:  # Process 4: after the run every brief is revealed
        lines += ["", briefs]
    return "\n".join(lines)


def transcript(entries: Sequence[Entry], who: Who, authors: set[str], max_chars: int) -> str:
    items = [render_entry(entry, who) for entry in entries if entry.author in authors]
    kept: list[str] = []
    used = 0
    for item in reversed(items):
        if used + len(item) + 1 > max_chars:
            break
        kept.append(item)
        used += len(item) + 1
    text = "\n".join(reversed(kept))
    if len(kept) < len(items):
        text = "[earlier entries omitted]\n" + text
    return text or "(no entries)"
