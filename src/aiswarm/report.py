"""End-of-run report: the answer (or competing positions) and the minority report."""

from pathlib import Path
from typing import Any

from aiswarm.board import Entry
from aiswarm.consensus import StopDecision, call_status
from aiswarm.view import Who


def _quote(text: Any, limit: int = 300) -> str:
    flat = " ".join(str(text).split())
    return f'"{flat[:limit]}…"' if len(flat) > limit else f'"{flat}"'


def account(goal: str, entries: list[Entry], decision: StopDecision, who: Who) -> str:
    """The run's key moments, built from the refs graph. Quotes only agents' own words."""
    by_id = {e.id: e for e in entries}
    rounds = max((e.round for e in entries), default=0)
    ended = decision.reason + (f" (call #{decision.call_id})" if decision.call_id else "")
    out = ["# Account", "", f"**Goal:** {goal}", "",
           f"**Ended:** {ended} after {rounds} round{'' if rounds == 1 else 's'}", ""]

    def owner(entry_id: Any) -> str:
        target = by_id.get(entry_id)
        return f"#{entry_id} ({who(target.author)})" if target else f"#{entry_id}"

    out += ["## Opening proposals", ""]
    opening = [
        f"- #{e.id} {who(e.author)}: {_quote(e.payload.get('answer_summary', ''))} — method: {_quote(e.payload.get('method', ''))}"
        for e in entries if e.type == "propose" and e.round == 1
    ]
    out += opening or ["_None._"]

    out += ["", "## Critiques that changed something", ""]
    turns = []
    for critique in entries:
        if critique.type != "critique":
            continue
        target = critique.payload.get("target")
        follow = next((e for e in entries if e.id > critique.id and e.type in ("refine", "adopt")
                       and e.payload.get("target") == target), None)
        if follow:
            verb = "refined" if follow.type == "refine" else "adopted"
            said = _quote(follow.payload.get("change") or follow.payload.get("reason", ""))
            turns.append(f"- #{critique.id} {who(critique.author)} critiqued {owner(target)}: "
                         f"{_quote(critique.payload.get('points', ''))} → #{follow.id} {who(follow.author)} {verb} it: {said}")
    out += turns or ["_None._"]

    out += ["", "## Adoptions", ""]
    seen: set[tuple[str, Any]] = set()
    adoptions = []
    for e in entries:
        key = (e.author, e.payload.get("target"))
        if e.type == "adopt" and key not in seen:
            seen.add(key)
            adoptions.append(f"- r{e.round}: {who(e.author)} adopted {owner(key[1])}: {_quote(e.payload.get('reason', ''))}")
    out += adoptions or ["_None._"]

    out += ["", "## Calls for consensus", ""]
    calls = [e for e in entries if e.type == "call_consensus"]
    for call in calls:
        status = call_status(entries, call.id)
        supporters = ", ".join(sorted(who(a) for a in status.supporters)) or "none"
        objectors = ", ".join(sorted(who(a) for a in status.objectors)) or "none"
        out.append(f"- #{call.id} by {who(call.author)} in r{call.round}: {_quote(call.payload.get('synthesis', ''))} — "
                   f"supporters: {supporters}; objectors: {objectors}; critiqued: {'yes' if status.critiqued else 'no'}")
        out += [f"  - r{v.round} {who(v.author)} {v.payload.get('stance')}: {_quote(v.payload.get('reason', ''))}"
                for v in entries if v.type == "vote" and v.payload.get("target") == call.id]
    if not calls:
        out.append("_None._")
    return "\n".join(out) + "\n"


def write_report(run_dir: Path, goal: str, entries: list[Entry], decision: StopDecision, who: Who) -> None:
    out = run_dir / "report"
    out.mkdir(parents=True, exist_ok=True)
    ended = decision.reason + (f" (call #{decision.call_id})" if decision.call_id else "")
    answer = ["# Answer", "", f"**Goal:** {goal}", "", f"**Ended:** {ended}", ""]
    minority = ["# Minority report", ""]

    if decision.reason == "consensus" and decision.call_id is not None:
        call = next(e for e in entries if e.id == decision.call_id)
        answer += ["## Synthesis", "", call.payload["synthesis"], "", "## Credits", ""]
        credits = call.payload.get("credits", [])
        answer += [f"- {who(c['agent_id'])}: {c['contribution']}" for c in credits] or ["_The caller credited no one._"]
        objectors = call_status(entries, call.id).objectors
        reasons = {
            e.author: e.payload["reason"]
            for e in entries
            if e.type == "vote" and e.payload.get("target") == call.id and e.author in objectors
        }
        minority += [f"- {who(author)}: {reason}" for author, reason in sorted(reasons.items())] or ["_No objections._"]
    else:
        answer += ["## Competing positions", ""]
        latest: dict[str, Entry] = {}
        for entry in entries:
            if entry.type == "propose":
                latest[entry.author] = entry
        for author, entry in latest.items():
            answer += [f"### {who(author)} (#{entry.id})", "", entry.payload["answer_summary"], "",
                       f"*Method:* {entry.payload['method']}", ""]
        if not latest:
            answer.append("_No proposals were made._")
        minority.append("_No consensus was reached._")

    (out / "account.md").write_text(account(goal, entries, decision, who))
    (out / "answer.md").write_text("\n".join(answer).rstrip() + "\n")
    (out / "minority.md").write_text("\n".join(minority).rstrip() + "\n")
