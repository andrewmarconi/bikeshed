"""Scorer metrics: its rulings, the points it awarded, and how often callers credit themselves. Deterministic; no writes.

The Observer only reports these records; it never reads to or from the Scorer.
"""

import json
import sqlite3
from collections import Counter
from dataclasses import dataclass, fields

from aiswarm.db import has_table
from aiswarm.observer.metrics import RunInfo


@dataclass(frozen=True)
class RulingRow:
    call_id: int
    caller: str
    accepted: bool
    feedback: str  # empty on acceptance


@dataclass(frozen=True)
class Award:
    agent_id: str
    call_id: int
    contribution: str  # what the caller credited the agent for


@dataclass(frozen=True)
class SelfCredit:
    calls: int = 0  # calls for consensus the agent made
    self_credited: int = 0  # of those, calls whose credits name the caller


def rulings(conn: sqlite3.Connection, run_id: str) -> list[RulingRow]:
    rows = conn.execute("SELECT id, author, type, payload FROM entries WHERE run_id = ? AND type IN "
                        "('call_consensus', 'scorer_accepted', 'scorer_rejected') ORDER BY id", (run_id,)).fetchall()
    callers = {r["id"]: r["author"] for r in rows if r["type"] == "call_consensus"}
    out = []
    for r in rows:
        if r["type"] == "call_consensus":
            continue
        payload = json.loads(r["payload"])
        call = payload.get("call_id")
        out.append(RulingRow(call, callers.get(call, "?"), r["type"] == "scorer_accepted",
                             str(payload.get("feedback", ""))))
    return out


def awards(conn: sqlite3.Connection, run_id: str) -> list[Award]:
    if not has_table(conn, "scorer_points"):  # a store from before the Scorer; never migrated
        return []
    rows = conn.execute("SELECT agent_id, call_id, contribution FROM scorer_points WHERE run_id = ? ORDER BY id",
                        (run_id,))
    return [Award(r["agent_id"], r["call_id"], r["contribution"]) for r in rows]


def self_credit(conn: sqlite3.Connection, run: RunInfo) -> dict[str, SelfCredit]:
    """Every call for consensus in the run, spent or not: did its caller credit itself?"""
    counts: dict[str, Counter] = {agent: Counter() for agent in run.agent_ids}
    for r in conn.execute("SELECT author, payload FROM entries WHERE run_id = ? AND type = 'call_consensus'",
                          (run.id,)):
        if r["author"] not in counts:
            continue
        credited = {c.get("agent_id") for c in json.loads(r["payload"]).get("credits", []) if isinstance(c, dict)}
        counts[r["author"]]["calls"] += 1
        counts[r["author"]]["self_credited"] += r["author"] in credited
    return {agent: SelfCredit(**c) for agent, c in counts.items()}


def combine_self_credit(parts: list[dict[str, SelfCredit]]) -> dict[str, SelfCredit]:
    totals: dict[str, Counter] = {}
    for part in parts:
        for agent, s in part.items():
            totals.setdefault(agent, Counter()).update({f.name: getattr(s, f.name) for f in fields(s)})
    return {agent: SelfCredit(**c) for agent, c in sorted(totals.items())}
