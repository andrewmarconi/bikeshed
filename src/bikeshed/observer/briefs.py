"""Goal briefs for the Observer: the Scorer's call-outs, how private facts travelled, and what was invented.

Read-only like the rest of the Observer. Nothing here reaches an agent.
"""

import json
import re
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, field

from bikeshed.board import SYSTEM, Board, Entry
from bikeshed.briefs import RunBriefs, brief_body
from bikeshed.briefs import load as load_briefs
from bikeshed.briefs import load_conflicts
from bikeshed.db import has_table
from bikeshed.observer.judge import quoted_in
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
    disclosed: bool | None = None  # whether the named member disclosed a fact in `round`; None: not judged
    ended: bool = False  # the board has no entry at all in `round`: the run ended first


def _strings(value: object):
    """Every string value in a payload, nested lists and dicts included; keys are not content."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _strings(v)


def _refers(entry: Entry, agent_id: str, name: str, theirs: set[int]) -> bool:
    if set(entry.refs) & theirs:
        return True
    terms = [f"({agent_id})"]
    if name and name.casefold() != agent_id.casefold():  # a bare id would match nearly any text
        terms.append(name)
    pattern = re.compile("|".join(rf"(?<!\w){re.escape(t)}(?!\w)" for t in terms), re.IGNORECASE)
    return any(pattern.search(text) for text in _strings(entry.payload))


def reactions(conn: sqlite3.Connection, run: RunInfo, who: Who, facts: Sequence["FactStat"] = ()) -> list[Reaction]:
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
        theirs_judged = [f for f in facts if f.agent_id == c.holder]
        out.append(Reaction(c.round + 1, c.holder, tuple(render_entry(e, who) for e in found),
                            any(f.round == c.round + 1 for f in theirs_judged) if theirs_judged else None,
                            not any(e.round == c.round + 1 for e in entries)))
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


TEXT_FACT = "text"  # a private brief's text, judged as one fact beside its constraints


def _facts(briefs: RunBriefs, agent_id: str) -> list[tuple[str, str]]:
    mine = briefs.for_agent(agent_id)
    if mine is None:
        return []
    text = " ".join(mine.text.split())
    return ([(TEXT_FACT, text)] if text else []) + [(c.id, c.text) for c in mine.constraints]


def _said(conn: sqlite3.Connection, run_id: str) -> list[Entry]:
    """What members said or wrote to each other; think notes are private and never count as disclosure."""
    return [e for e in Board(conn, run_id).entries() if e.author != SYSTEM and e.type != "think"]


def disclosure_inputs(conn: sqlite3.Connection, run: RunInfo, agent_id: str, who: Who) -> dict[str, str] | None:
    facts = _facts(load_briefs(conn, run.id), agent_id)
    if not facts:
        return None
    entries = _said(conn, run.id)
    return {"private facts": "\n".join(f"{fid}: {text}" for fid, text in facts),
            "this member's entries": "\n".join(render_entry(e, who) for e in entries if e.author == agent_id),
            "other members' entries": "\n".join(render_entry(e, who) for e in entries if e.author != agent_id)}


def fabrication_inputs(conn: sqlite3.Connection, run: RunInfo, who: Who) -> dict[str, str] | None:
    briefs = load_briefs(conn, run.id)
    if briefs.shared is None:
        return None
    entries = Board(conn, run.id).entries()
    by_id = {e.id: e for e in entries}
    closed = next((e for e in reversed(entries) if e.type == "run_closed"), None)
    call = by_id.get(closed.payload.get("call_id")) if closed else None
    return {"shared brief": brief_body(briefs.shared),
            "private briefs": "\n\n".join(f"{who(b.agent_id)}:\n{brief_body(b)}" for b in briefs.private),
            "opening proposals": "\n".join(render_entry(e, who) for e in entries
                                           if e.type == "propose" and e.round == 1),
            "final synthesis": str(call.payload.get("synthesis", "")) if call else ""}


def fact_stats(conn: sqlite3.Connection, run: RunInfo, who: Who, judgments: list[Judgment]) -> list[FactStat]:
    briefs = load_briefs(conn, run.id)
    entries = _said(conn, run.id)
    first: dict[str, int] = {}
    for c in callouts(conn, run.id):
        if c.holder is not None:
            first[c.holder] = min(first.get(c.holder, c.round), c.round)
    out = []
    for j in judgments:
        if j.analysis != "disclosure" or j.status != "ok" or j.agent_id is None:
            continue
        texts = dict(_facts(briefs, j.agent_id))
        own = [e for e in entries if e.author == j.agent_id]
        for f in j.result["facts"]:
            first_entry = next((e for e in own if quoted_in(f["quote"], render_entry(e, who))), None) \
                if f["disclosed"] else None
            rnd = first_entry.round if first_entry else None
            seen = first.get(j.agent_id)
            if not f["disclosed"]:
                timing = "never"
            elif rnd is None:
                timing = "disclosed (round unknown)"
            elif seen is not None and rnd > seen:
                timing = "after call-out"
            else:
                timing = "unprompted"
            relay = f.get("relay_quote") or ""
            # relayed means said by someone else after the holder's disclosing entry (any time if that is unknown)
            relayed = bool(relay) and any(
                e.author != j.agent_id and (first_entry is None or e.id > first_entry.id)
                and quoted_in(relay, render_entry(e, who)) for e in entries)
            out.append(FactStat(j.agent_id, f["fact"], texts.get(f["fact"], ""), f["disclosed"], rnd, timing,
                                relayed))
    return out


def section(conn: sqlite3.Connection, run: RunInfo, who: Who, judgments: list[Judgment]) -> BriefsSection | None:
    briefs = load_briefs(conn, run.id)
    if briefs.empty:
        return None
    facts = fact_stats(conn, run, who, judgments)
    return BriefsSection(
        callouts=callouts(conn, run.id), reactions=reactions(conn, run, who, facts), facts=facts,
        fabrication=next((j for j in judgments if j.analysis == "fabrication"), None),
        has_shared=briefs.shared is not None, closed_by_consensus=run.ended_reason == "consensus")


@dataclass(frozen=True)
class RunStat:
    tag: str
    category: str
    callouts: int  # distinct calls the Scorer rejected over a constraint
    facts: int
    disclosed: int
    unprompted: int
    after_callout: int
    fabricated: int | None  # None: no shared brief, or not judged
    rounds: tuple[int, ...] = ()  # round of first disclosure, for each disclosed fact whose round is known
    process: int = 1

    @property
    def mean_round(self) -> float | None:
        return sum(self.rounds) / len(self.rounds) if self.rounds else None


def run_stat(conn: sqlite3.Connection, run: RunInfo, who: Who, judgments: list[Judgment]) -> RunStat | None:
    s = section(conn, run, who, judgments)
    if s is None:
        return None
    fab = s.fabrication
    return RunStat(run.tag, run.category, len({c.call_id for c in s.callouts}), len(s.facts),
                   sum(f.disclosed for f in s.facts), sum(f.timing == "unprompted" for f in s.facts),
                   sum(f.timing == "after call-out" for f in s.facts),
                   len(fab.result["items"]) if fab is not None and fab.status == "ok" else None,
                   tuple(f.round for f in s.facts if f.round is not None), run.process)


def slot_label(conn: sqlite3.Connection, run_id: str, agent_id: str) -> str:
    """Which private brief an agent held in a run: "slot k", "shared only", or "–" for an unbriefed run."""
    if not has_table(conn, "run_briefs"):
        return "–"
    rows = conn.execute("SELECT slot, agent_id FROM run_briefs WHERE run_id = ?", (run_id,)).fetchall()
    if not rows:
        return "–"
    held = [r["slot"] for r in rows if r["slot"] >= 1 and r["agent_id"] == agent_id]
    return f"slot {held[0]}" if held else "shared only"
