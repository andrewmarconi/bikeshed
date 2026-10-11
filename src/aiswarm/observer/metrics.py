"""Deterministic metrics over the community store. No LLM calls, no writes."""

import json
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from aiswarm.profiles import SEED_DIR, ProfileStore
from aiswarm.provenance import provenance_changes
from aiswarm.timeline import _rename
from aiswarm.view import Who

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


def peer_scores(conn: sqlite3.Connection, run_id: str) -> dict[str, float]:
    rows = conn.execute("SELECT reviewee, AVG(overall) AS score FROM reviews WHERE run_id = ? AND is_self = 0 "
                        "GROUP BY reviewee", (run_id,))
    return {r["reviewee"]: float(r["score"]) for r in rows}


def self_scores(conn: sqlite3.Connection, run_id: str) -> dict[str, int]:
    rows = conn.execute("SELECT reviewee, overall FROM reviews WHERE run_id = ? AND is_self = 1", (run_id,))
    return {r["reviewee"]: r["overall"] for r in rows}


def self_peer_gap(conn: sqlite3.Connection, run_id: str) -> dict[str, float]:
    """Self-rating minus mean peer rating; positive means the agent rates itself higher than peers do."""
    peers, own = peer_scores(conn, run_id), self_scores(conn, run_id)
    return {agent: own[agent] - peers[agent] for agent in own if agent in peers}


def dimension_scores(conn: sqlite3.Connection, run_id: str) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    rows = conn.execute(
        "SELECT v.reviewee, d.name, AVG(r.rating) AS score FROM review_ratings r JOIN reviews v ON v.id = r.review_id "
        "JOIN dimensions d ON d.id = r.dimension_id WHERE v.run_id = ? AND v.is_self = 0 GROUP BY v.reviewee, d.name",
        (run_id,))
    for r in rows:
        out.setdefault(r["reviewee"], {})[r["name"]] = float(r["score"])
    return out


@dataclass(frozen=True)
class IdentityEvent:
    at: datetime
    agent_id: str
    kind: str  # rename | public profile | temperament.md | self.md | control reset
    detail: str
    prior_peer: float | None  # mean peer score in the latest run started at or before the event


_IDENTITY_FILES = ("temperament.md", "self.md")


def _kind(agent_id: str, subject: str, patch: str) -> tuple[str, str] | None:
    if subject.startswith("reset control agent"):
        return "control reset", ""
    if renamed := _rename(patch):
        return "rename", f"{renamed[0]} → {renamed[1]}"
    if subject == f"{agent_id} updated public profile":
        return "public profile", ""
    for filename in _IDENTITY_FILES:
        if subject == f"{agent_id} updated {filename}":
            return filename, ""
    return None


def identity_events(conn: sqlite3.Connection, store: ProfileStore, runs: list[RunInfo]) -> list[IdentityEvent]:
    starts = [(datetime.fromisoformat(r.created_at), r.id) for r in runs]
    events = []
    for agent_id in store.ids():
        for commit in store.history(agent_id):
            kind = _kind(agent_id, commit.subject, commit.patch)
            if kind is None:
                continue
            prior = [run_id for started, run_id in starts if started <= commit.at]
            score = peer_scores(conn, prior[-1]).get(agent_id) if prior else None
            events.append(IdentityEvent(commit.at, agent_id, kind[0], kind[1], score))
    return sorted(events, key=lambda e: e.at)


def _norm(text: str | None) -> str:
    return " ".join((text or "").split())


def twins(store: ProfileStore) -> dict[str, list[str]]:
    """Each control agent's twins: non-control agents whose first temperament equals the control's seed."""
    out: dict[str, list[str]] = {}
    for agent_id in store.ids():
        if not store.record(agent_id).control:
            continue
        seed = store.root / "agents" / agent_id / SEED_DIR / "temperament.md"
        target = _norm(seed.read_text()) if seed.exists() else None
        out[agent_id] = sorted(
            other for other in store.ids()
            if other != agent_id and not store.record(other).control
            and target and _norm(store.first_version(other, "temperament.md")) == target
        )
    return out


@dataclass
class CategoryStats:
    runs: int = 0
    endings: Counter = field(default_factory=Counter)
    actions: Counter = field(default_factory=Counter)


def by_category(conn: sqlite3.Connection, runs: list[RunInfo]) -> dict[str, CategoryStats]:
    stats: dict[str, CategoryStats] = {}
    for run in runs:
        s = stats.setdefault(run.category, CategoryStats())
        s.runs += 1
        s.endings[run.ended_reason or "unknown"] += 1
        for counts in action_mix(conn, run).values():
            s.actions.update({k: v for k, v in counts.items() if v})
    return stats


@dataclass(frozen=True)
class BenchmarkAnswer:
    run_id: str
    consensus: bool
    text: str


def benchmark_answers(conn: sqlite3.Connection, runs: list[RunInfo], who: Who) -> list[BenchmarkAnswer]:
    out = []
    for run in runs:
        if not run.is_benchmark:
            continue
        closed = conn.execute("SELECT payload FROM entries WHERE run_id = ? AND type = 'run_closed'",
                              (run.id,)).fetchone()
        call_id = json.loads(closed["payload"]).get("call_id") if closed else None
        if call_id:
            call = conn.execute("SELECT payload FROM entries WHERE id = ?", (call_id,)).fetchone()
            out.append(BenchmarkAnswer(run.id, True, json.loads(call["payload"])["synthesis"]))
            continue
        latest: dict[str, str] = {}
        for row in _entries(conn, run.id):
            if row["type"] == "propose":
                latest[row["author"]] = json.loads(row["payload"])["answer_summary"]
        text = "\n".join(f"- {who(a)}: {t}" for a, t in latest.items()) or "(no proposals)"
        out.append(BenchmarkAnswer(run.id, False, text))
    return out


@dataclass(frozen=True)
class NormEvent:
    run_id: str
    author: str
    kind: str
    text: str
    status: str


def norm_events(conn: sqlite3.Connection) -> list[NormEvent]:
    rows = conn.execute("SELECT * FROM norm_proposals ORDER BY id")
    return [NormEvent(r["run_id"], r["author"], r["kind"], r["text"] or f"{r['name']}: {r['question']}", r["status"])
            for r in rows]


def harness_changes(conn: sqlite3.Connection, runs: list[RunInfo]) -> list[int]:
    """Indices of runs whose provenance shows a harness change (since the previous run, or within the run)."""
    return [i for i, run in enumerate(runs) if provenance_changes(conn, run.id)]
