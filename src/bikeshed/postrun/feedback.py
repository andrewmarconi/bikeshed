"""Facts about a finished run, assembled by code: who interacted, and what each agent is told about its part."""

from collections.abc import Sequence

from bikeshed.board import SYSTEM, Entry
from bikeshed.view import Who, render_entry


def participants(
    entries: Sequence[Entry], file_authors: Sequence[tuple[str, str]], agent_ids: Sequence[str]
) -> list[str]:
    """The members who took part in a run: a public entry other than a pass, or a workspace file written."""
    took_part = {entry.author for entry in entries if entry.visible_to is None and entry.type != "pass"}
    took_part |= {author for _, author in file_authors}
    return [agent_id for agent_id in agent_ids if agent_id in took_part]


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
    if briefs:  # briefed goals: after the run every brief is revealed
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
