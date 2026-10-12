"""Process 2 metrics: the private-message network, poll participation and speaking order. Deterministic; no writes."""

import json
import sqlite3
from collections import Counter
from dataclasses import dataclass, fields

from aiswarm.observer.metrics import RunInfo


def _entries(conn: sqlite3.Connection, run: RunInfo) -> list[sqlite3.Row]:
    rows = conn.execute("SELECT * FROM entries WHERE run_id = ? ORDER BY id", (run.id,)).fetchall()
    return [r for r in rows if r["author"] in run.agent_ids]


def _turns(rows: list[sqlite3.Row]) -> dict[tuple[int, str], int]:
    """(round, member) -> entry id of that member's react turn: its first entry in the round that is not a think note."""
    turns: dict[tuple[int, str], int] = {}
    for r in rows:
        if r["type"] != "think":
            turns.setdefault((r["round"], r["author"]), r["id"])
    return turns


def pm_network(conn: sqlite3.Connection, run: RunInfo) -> Counter:
    counts: Counter = Counter()
    for r in _entries(conn, run):
        if r["type"] == "private_message":
            to = json.loads(r["payload"]).get("to")
            if to in run.agent_ids:
                counts[(r["author"], to)] += 1
    return counts


@dataclass(frozen=True)
class RecurringPair:
    sender: str
    recipient: str
    runs: int
    messages: int


def recurring_pairs(conn: sqlite3.Connection, runs: list[RunInfo]) -> list[RecurringPair]:
    seen: Counter = Counter()
    sent: Counter = Counter()
    for run in runs:
        network = pm_network(conn, run)
        seen.update(network.keys())
        sent.update(network)
    pairs = [RecurringPair(a, b, n, sent[(a, b)]) for (a, b), n in seen.items() if n >= 2]
    return sorted(pairs, key=lambda p: (-p.runs, -p.messages, p.sender, p.recipient))


@dataclass(frozen=True)
class Participation:
    supported: int = 0
    objected: int = 0
    abstained: int = 0
    failed: int = 0
    unprompted: int = 0  # votes cast in the agent's own react turn, without being polled

    @property
    def polled(self) -> int:
        return self.supported + self.objected + self.abstained + self.failed


def poll_participation(conn: sqlite3.Connection, run: RunInfo) -> dict[str, Participation]:
    counts: dict[str, Counter] = {agent: Counter() for agent in run.agent_ids}
    rows = _entries(conn, run)
    turns = _turns(rows)
    for r in rows:
        turn = turns.get((r["round"], r["author"]))
        if r["type"] == "vote" and r["id"] == turn:
            counts[r["author"]]["unprompted"] += 1
        elif r["type"] == "vote" and turn is not None and r["id"] > turn:
            stance = json.loads(r["payload"]).get("stance")
            counts[r["author"]]["supported" if stance == "support" else "objected"] += 1
        elif r["type"] == "abstain":
            counts[r["author"]]["abstained"] += 1
    for f in conn.execute("SELECT agent_id FROM failures WHERE run_id = ? AND kind LIKE 'poll:%'", (run.id,)):
        if f["agent_id"] in counts:
            counts[f["agent_id"]]["failed"] += 1
    return {agent: Participation(**c) for agent, c in counts.items()}


@dataclass(frozen=True)
class OrderStat:
    position: int  # 1 = spoke first in the round
    turns: int  # turns that did not fail
    taken_up: int  # turns adopted, refined or cited in a call by another member


def speaking_order(conn: sqlite3.Connection, run: RunInfo) -> list[OrderStat]:
    """Round 1 is blind (everyone acts at once), so only rounds from 2 on have a speaking order."""
    rows = _entries(conn, run)
    author_of = {r["id"]: r["author"] for r in rows}
    used: set[int] = set()
    failed: set[int] = set()  # a failed turn keeps its place in the order but could not be taken up
    for r in rows:
        payload = json.loads(r["payload"])
        if r["type"] == "pass" and "failure" in payload:
            failed.add(r["id"])
        targets = [payload.get("target")] if r["type"] in ("adopt", "refine") else []
        if r["type"] == "call_consensus":
            targets = json.loads(r["refs"])
        used |= {t for t in targets if t in author_of and author_of[t] != r["author"]}
    by_round: dict[int, list[int]] = {}
    for (rnd, _), entry_id in _turns(rows).items():
        if rnd >= 2:
            by_round.setdefault(rnd, []).append(entry_id)
    turns: Counter = Counter()
    taken: Counter = Counter()
    for ids in by_round.values():
        for position, entry_id in enumerate(sorted(ids), start=1):
            if entry_id in failed:
                continue
            turns[position] += 1
            taken[position] += entry_id in used
    return [OrderStat(p, turns[p], taken[p]) for p in sorted(turns)]


def combine_participation(parts: list[dict[str, Participation]]) -> dict[str, Participation]:
    totals: dict[str, Counter] = {}
    for part in parts:
        for agent, p in part.items():
            totals.setdefault(agent, Counter()).update({f.name: getattr(p, f.name) for f in fields(p)})
    return {agent: Participation(**c) for agent, c in sorted(totals.items())}


def combine_order(stats: list[list[OrderStat]]) -> list[OrderStat]:
    turns: Counter = Counter()
    taken: Counter = Counter()
    for run in stats:
        for s in run:
            turns[s.position] += s.turns
            taken[s.position] += s.taken_up
    return [OrderStat(p, turns[p], taken[p]) for p in sorted(turns)]
