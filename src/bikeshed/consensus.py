"""Stop rules for a run. Pure functions over board entries; no model involved."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from bikeshed.board import Entry
from bikeshed.config import RunSettings

CLOSE_RULES = {  # how each rule reads in the agents' system prompt
    "critique": "has been critiqued at least once",
    "stood_round": "has stood on the board through the round after the one it was made in",
}


@dataclass(frozen=True)
class CallStatus:
    call_id: int
    caller: str
    supporters: frozenset[str]
    objectors: frozenset[str]
    critiqued: bool


@dataclass(frozen=True)
class StopDecision:
    reason: Literal["consensus", "stagnation", "budget", "abandoned"]  # abandoned: closed by hand, never by a rule
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


def _is_idle(entry: Entry, entries: Sequence[Entry]) -> bool:
    """A pass or an abstention, or an adopt repeating one the same author already made."""
    if entry.type in ("pass", "abstain"):
        return True
    if entry.type != "adopt":
        return False
    target = entry.payload.get("target")
    return any(
        e.id < entry.id and e.author == entry.author and e.type == "adopt" and e.payload.get("target") == target
        for e in entries
    )


def _idle_round(entries: Sequence[Entry], agent_ids: Sequence[str], round: int) -> bool:
    posted = [e for e in entries if e.round == round and e.author in agent_ids and e.type != "think"
              and not e.payload.get("pre_action")]  # stagnation is about each turn's action, not what came before it
    return bool(posted) and all(_is_idle(e, entries) for e in posted)


def ready_to_close(call: Entry, status: CallStatus, round: int, rule: str) -> bool:
    """Whether a call meets its close rule, apart from the quorum."""
    return call.round < round if rule == "stood_round" else status.critiqued


def spent_calls(entries: Sequence[Entry]) -> set[int]:
    """Calls the Scorer rejected: they can never close the run, and votes on them count for nothing."""
    return {e.payload.get("call_id") for e in entries if e.type == "scorer_rejected"}


def stop_decision(
    entries: Sequence[Entry], agent_ids: Sequence[str], round: int, settings: RunSettings
) -> StopDecision | None:
    members = set(agent_ids)
    needed = max(1, len(members) - settings.quorum_slack)
    spent = spent_calls(entries)
    for entry in entries:
        if entry.type == "call_consensus" and entry.id not in spent:
            status = call_status(entries, entry.id)
            if ready_to_close(entry, status, round, settings.close_rule) and len(status.supporters & members) >= needed:
                return StopDecision("consensus", entry.id)
    window = range(round - settings.stagnation_rounds + 1, round + 1)
    if round >= settings.stagnation_rounds and all(_idle_round(entries, agent_ids, r) for r in window):
        return StopDecision("stagnation")
    if round >= settings.max_rounds:
        return StopDecision("budget")
    return None
