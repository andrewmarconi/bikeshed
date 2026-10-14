"""Process 4 for the Observer: the Scorer's call-outs, how private facts travelled, and what was invented.

Read-only like the rest of the Observer. Nothing here reaches an agent.
"""

import json
import sqlite3
from dataclasses import dataclass, field

from bikeshed.board import SYSTEM, Board, Entry
from bikeshed.briefs import load as load_briefs
from bikeshed.briefs import load_conflicts
from bikeshed.observer.metrics import RunInfo
from bikeshed.observer.store import Judgment
from bikeshed.view import Who, render_entry


@dataclass(frozen=True)
class Callout:
    call_id: int
    round: int  # the round the rejection was posted in; members can react from the next one
    constraint_id: str
    constraint: str
    holder: str | None  # None: the shared brief


def callouts(conn: sqlite3.Connection, run_id: str) -> list[Callout]:
    briefs = load_briefs(conn, run_id)
    if briefs.empty:
        return []
    rounds = {json.loads(r["payload"]).get("call_id"): r["round"] for r in conn.execute(
        "SELECT round, payload FROM entries WHERE run_id = ? AND type = 'scorer_rejected'", (run_id,))}
    text = {c.id: c.text for c in briefs.constraints}
    return [Callout(call, rounds.get(call, 0), cid, text.get(cid, cid), briefs.holder(cid))
            for call, cid in load_conflicts(conn, run_id)]


@dataclass(frozen=True)
class Reaction:
    round: int  # the round after the call-out
    named: str
    entries: tuple[str, ...]  # rendered entries from that round that refer to the named member


def _refers(entry: Entry, agent_id: str, name: str, theirs: set[int]) -> bool:
    text = json.dumps(entry.payload, ensure_ascii=False).casefold()
    return bool(set(entry.refs) & theirs) or name.casefold() in text or f"({agent_id})" in text


def reactions(conn: sqlite3.Connection, run: RunInfo, who: Who) -> list[Reaction]:
    entries = Board(conn, run.id).entries()
    out: list[Reaction] = []
    seen: set[tuple[int, str]] = set()
    for c in callouts(conn, run.id):
        if c.holder is None or (c.round, c.holder) in seen:
            continue
        seen.add((c.round, c.holder))
        theirs = {e.id for e in entries if e.author == c.holder}
        name = who(c.holder).rsplit(" (", 1)[0]
        found = [e for e in entries if e.round == c.round + 1 and e.author not in (c.holder, SYSTEM)
                 and _refers(e, c.holder, name, theirs)]
        out.append(Reaction(c.round + 1, c.holder, tuple(render_entry(e, who) for e in found)))
    return out


@dataclass(frozen=True)
class FactStat:
    agent_id: str
    fact: str  # a constraint id, or "text" for the brief's text
    text: str
    disclosed: bool
    round: int | None  # round of the first own entry carrying the quote
    timing: str  # "unprompted", "after call-out", "disclosed (round unknown)", "never"
    relayed: bool


@dataclass
class BriefsSection:
    callouts: list[Callout]
    reactions: list[Reaction]
    facts: list[FactStat] = field(default_factory=list)
    fabrication: Judgment | None = None
    has_shared: bool = False
    closed_by_consensus: bool = False  # whether the run then closed by consensus


def section(conn: sqlite3.Connection, run: RunInfo, who: Who, judgments: list[Judgment]) -> BriefsSection | None:
    briefs = load_briefs(conn, run.id)
    if briefs.empty:
        return None
    return BriefsSection(callouts(conn, run.id), reactions(conn, run, who), has_shared=briefs.shared is not None,
                         closed_by_consensus=run.ended_reason == "consensus")
