"""Goal briefs (Process 4): facts a goal gives the community, shared by all or private to one member.

Plain code. The Runner stores each run's briefs when it starts; the View, the Scorer, the post-run cycle and the
Observer read them back from `run_briefs`. A brief states facts about the situation; it never tells anyone what to do.
"""

import json
import random
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass

from bikeshed.db import has_table


@dataclass(frozen=True)
class BriefSpec:
    """A goal's briefs as written in goals.toml, before they are assigned to a run's members."""
    shared: str | None = None
    shared_constraints: tuple[str, ...] = ()
    private: tuple[tuple[str, tuple[str, ...]], ...] = ()  # (text, constraints) per brief, in file order


@dataclass(frozen=True)
class Constraint:
    id: str  # s.c1, p2.c1: shown to the Scorer, never to agents
    text: str


@dataclass(frozen=True)
class Brief:
    slot: int  # 0: shared; 1..N: private, in file order
    agent_id: str | None  # None for the shared brief
    text: str
    constraints: tuple[Constraint, ...] = ()


@dataclass(frozen=True)
class RunBriefs:
    shared: Brief | None = None
    private: tuple[Brief, ...] = ()

    @property
    def empty(self) -> bool:
        return self.shared is None and not self.private

    @property
    def constraints(self) -> tuple[Constraint, ...]:
        briefs = ([self.shared] if self.shared else []) + list(self.private)
        return tuple(c for b in briefs for c in b.constraints)

    def for_agent(self, agent_id: str) -> Brief | None:
        return next((b for b in self.private if b.agent_id == agent_id), None)

    def holder(self, constraint_id: str) -> str | None:
        """The member whose private brief holds the constraint; None for a shared constraint."""
        return next((b.agent_id for b in self.private for c in b.constraints if c.id == constraint_id), None)


EMPTY = RunBriefs()


def too_many(briefs: int, agents: int) -> str:
    return (f"the goal has {briefs} private brief{'s' if briefs != 1 else ''} "
            f"but the run has {agents} agent{'s' if agents != 1 else ''}")


def assign(spec: BriefSpec, agent_ids: Sequence[str], seed: int) -> RunBriefs:
    """Number the constraints and hand private brief k to the k-th member of a seeded shuffle."""
    if len(spec.private) > len(agent_ids):
        raise ValueError(too_many(len(spec.private), len(agent_ids)))
    shared = None if spec.shared is None else Brief(
        0, None, spec.shared, tuple(Constraint(f"s.c{i}", t) for i, t in enumerate(spec.shared_constraints, 1)))
    order = list(agent_ids)
    random.Random(f"{seed}-briefs").shuffle(order)
    private = tuple(
        Brief(k, order[k - 1], text, tuple(Constraint(f"p{k}.c{i}", t) for i, t in enumerate(constraints, 1)))
        for k, (text, constraints) in enumerate(spec.private, 1))
    return RunBriefs(shared, private)


def save(conn: sqlite3.Connection, run_id: str, briefs: RunBriefs) -> None:
    """The run's snapshot of its briefs. The caller holds the transaction that creates the run."""
    rows = ([briefs.shared] if briefs.shared else []) + list(briefs.private)
    conn.executemany(
        "INSERT INTO run_briefs (run_id, slot, agent_id, text, constraints) VALUES (?, ?, ?, ?, ?)",
        [(run_id, b.slot, b.agent_id, b.text,
          json.dumps([{"id": c.id, "text": c.text} for c in b.constraints], ensure_ascii=False)) for b in rows])


def load(conn: sqlite3.Connection, run_id: str) -> RunBriefs:
    if not has_table(conn, "run_briefs"):  # read-only readers of an older store
        return EMPTY
    rows = conn.execute("SELECT slot, agent_id, text, constraints FROM run_briefs WHERE run_id = ? ORDER BY slot",
                        (run_id,)).fetchall()
    briefs = [Brief(r["slot"], r["agent_id"], r["text"],
                    tuple(Constraint(c["id"], c["text"]) for c in json.loads(r["constraints"]))) for r in rows]
    return RunBriefs(next((b for b in briefs if b.slot == 0), None), tuple(b for b in briefs if b.slot > 0))


def load_conflicts(conn: sqlite3.Connection, run_id: str) -> list[tuple[int, str]]:
    """(call id, constraint id) for every constraint a Scorer rejection named, in the order recorded."""
    if not has_table(conn, "scorer_conflicts"):
        return []
    return [(r["call_id"], r["constraint_id"]) for r in conn.execute(
        "SELECT call_id, constraint_id FROM scorer_conflicts WHERE run_id = ? ORDER BY id", (run_id,))]
