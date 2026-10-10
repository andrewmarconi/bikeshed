"""Stop rules for a run. Pure functions over board entries; no model involved."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from aiswarm.board import Entry
from aiswarm.config import RunSettings


@dataclass(frozen=True)
class CallStatus:
    call_id: int
    caller: str
    supporters: frozenset[str]
    objectors: frozenset[str]
    critiqued: bool


@dataclass(frozen=True)
class StopDecision:
    reason: Literal["consensus", "stagnation", "budget"]
    call_id: int | None = None


def call_status(entries: Sequence[Entry], call_id: int) -> CallStatus:
    call = next(entry for entry in entries if entry.id == call_id)
    stances = {call.author: "support"}
    critiqued = False
    for entry in entries:
        if entry.id <= call_id or entry.payload.get("target") != call_id:
            continue
        if entry.type == "vote":
            stances[entry.author] = entry.payload["stance"]
        elif entry.type == "critique" and entry.author != call.author:
            critiqued = True
    return CallStatus(
        call_id=call_id,
        caller=call.author,
        supporters=frozenset(a for a, s in stances.items() if s == "support"),
        objectors=frozenset(a for a, s in stances.items() if s == "object"),
        critiqued=critiqued,
    )


def _all_passed(entries: Sequence[Entry], agent_ids: Sequence[str], round: int) -> bool:
    posted = [e for e in entries if e.round == round and e.author in agent_ids]
    return bool(posted) and all(e.type == "pass" for e in posted)


def stop_decision(
    entries: Sequence[Entry], agent_ids: Sequence[str], round: int, settings: RunSettings
) -> StopDecision | None:
    members = set(agent_ids)
    needed = max(1, len(members) - settings.quorum_slack)
    for entry in entries:
        if entry.type == "call_consensus":
            status = call_status(entries, entry.id)
            if status.critiqued and len(status.supporters & members) >= needed:
                return StopDecision("consensus", entry.id)
    window = range(round - settings.stagnation_rounds + 1, round + 1)
    if round >= settings.stagnation_rounds and all(_all_passed(entries, agent_ids, r) for r in window):
        return StopDecision("stagnation")
    if round >= settings.max_rounds:
        return StopDecision("budget")
    return None
