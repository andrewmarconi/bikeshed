"""End-of-run report: the answer (or competing positions) and the minority report."""

from pathlib import Path

from aiswarm.board import Entry
from aiswarm.consensus import StopDecision, call_status
from aiswarm.view import Who


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

    (out / "answer.md").write_text("\n".join(answer).rstrip() + "\n")
    (out / "minority.md").write_text("\n".join(minority).rstrip() + "\n")
