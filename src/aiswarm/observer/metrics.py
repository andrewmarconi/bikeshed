"""Deterministic metrics over the community store. No LLM calls, no writes."""

import json
import sqlite3
from collections import Counter
from dataclasses import dataclass, field

ACTIONS = ("propose", "critique", "adopt", "refine", "vote", "call_consensus", "write_file", "pass", "failed")


@dataclass(frozen=True)
class RunInfo:
    id: str
    goal: str
    goal_id: str | None
    goal_category: str | None
    is_benchmark: bool
    ended_reason: str | None
    agent_ids: tuple[str, ...]
    created_at: str
    end: str

    @property
    def label(self) -> str:
        return self.goal_id or self.id

    @property
    def category(self) -> str:
        return self.goal_category or "ad hoc"


def run_end(conn: sqlite3.Connection, run_id: str) -> str:
    """The last recorded moment of a run, including its post-run cycle."""
    return conn.execute(
        "SELECT MAX(t) FROM (SELECT updated_at AS t FROM postrun_steps WHERE run_id = ?1 "
        "UNION ALL SELECT created_at FROM entries WHERE run_id = ?1 "
        "UNION ALL SELECT created_at FROM runs WHERE id = ?1)", (run_id,),
    ).fetchone()[0]


def closed_runs(conn: sqlite3.Connection) -> list[RunInfo]:
    rows = conn.execute("SELECT * FROM runs WHERE status = 'closed' ORDER BY rowid").fetchall()
    return [
        RunInfo(r["id"], r["goal"], r["goal_id"], r["goal_category"], bool(r["is_benchmark"]), r["ended_reason"],
                tuple(json.loads(r["agent_ids"])), r["created_at"], run_end(conn, r["id"]))
        for r in rows
    ]


def open_runs(conn: sqlite3.Connection) -> list[str]:
    return [r["id"] for r in conn.execute("SELECT id FROM runs WHERE status != 'closed' ORDER BY rowid")]


def _entries(conn: sqlite3.Connection, run_id: str) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM entries WHERE run_id = ? ORDER BY id", (run_id,)).fetchall()


def action_mix(conn: sqlite3.Connection, run: RunInfo) -> dict[str, Counter]:
    mix: dict[str, Counter] = {agent: Counter({a: 0 for a in ACTIONS}) for agent in run.agent_ids}
    for row in _entries(conn, run.id):
        if row["author"] not in mix:
            continue
        kind = row["type"]
        if kind == "pass" and "failure" in json.loads(row["payload"]):
            kind = "failed"
        if kind in ACTIONS:
            mix[row["author"]][kind] += 1
    return mix


@dataclass(frozen=True)
class Influence:
    adopted: int = 0    # others adopted your entries
    refined: int = 0    # others refined your entries
    critiqued: int = 0  # others critiqued your entries
    cited: int = 0      # your entries cited in others' calls for consensus
    files: int = 0      # workspace file versions you wrote

    @property
    def score(self) -> int:
        return self.adopted + self.refined + self.cited + self.files


def influence(conn: sqlite3.Connection, run: RunInfo) -> dict[str, Influence]:
    rows = _entries(conn, run.id)
    author_of = {r["id"]: r["author"] for r in rows}
    counts: dict[str, Counter] = {agent: Counter() for agent in run.agent_ids}
    field_for = {"adopt": "adopted", "refine": "refined", "critique": "critiqued"}
    for row in rows:
        if row["type"] in field_for:
            owner = author_of.get(json.loads(row["payload"]).get("target"))
            if owner in counts and owner != row["author"]:
                counts[owner][field_for[row["type"]]] += 1
        elif row["type"] == "call_consensus":
            for ref in json.loads(row["refs"]):
                owner = author_of.get(ref)
                if owner in counts and owner != row["author"]:
                    counts[owner]["cited"] += 1
    for row in conn.execute("SELECT author, COUNT(*) AS n FROM files WHERE run_id = ? GROUP BY author", (run.id,)):
        if row["author"] in counts:
            counts[row["author"]]["files"] += row["n"]
    return {agent: Influence(**c) for agent, c in counts.items()}


@dataclass(frozen=True)
class Credits:
    caller: str
    credited: dict[str, str] = field(default_factory=dict)  # agent id -> contribution


def credits(conn: sqlite3.Connection, run_id: str) -> Credits | None:
    """Credits from the call that closed the run; None when the run did not close on a call."""
    row = conn.execute("SELECT payload FROM entries WHERE run_id = ? AND type = 'run_closed'", (run_id,)).fetchone()
    call_id = json.loads(row["payload"]).get("call_id") if row else None
    if not call_id:
        return None
    call = conn.execute("SELECT author, payload FROM entries WHERE id = ?", (call_id,)).fetchone()
    given = json.loads(call["payload"]).get("credits", [])
    return Credits(call["author"], {c["agent_id"]: c.get("contribution", "") for c in given if "agent_id" in c})


def credit_pairs(conn: sqlite3.Connection, runs: list[RunInfo]) -> list[tuple[str, str, str]]:
    """(run id, giver, receiver) for every credit in every run that closed on a call, in run order."""
    pairs = []
    for run in runs:
        got = credits(conn, run.id)
        if got:
            pairs += [(run.id, got.caller, receiver) for receiver in sorted(got.credited)]
    return pairs
