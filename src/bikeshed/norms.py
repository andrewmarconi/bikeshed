"""Community norms and rating dimensions: agents propose, critique, and vote; code applies the consensus rule."""

import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace

from bikeshed.db import now_iso, transaction

NORM_MAX = 500
DIMENSION_NAME_MAX = 40
Who = Callable[[str], str]


@dataclass(frozen=True)
class Proposal:
    id: int
    run_id: str
    author: str
    kind: str
    text: str
    target: int | None
    name: str
    question: str
    status: str


@dataclass(frozen=True)
class Dimension:
    id: int
    name: str
    question: str
    adopted_run: str


def _proposal(row: sqlite3.Row) -> Proposal:
    return Proposal(row["id"], row["run_id"], row["author"], row["kind"], row["text"], row["target"],
                    row["name"], row["question"], row["status"])


def add_proposal(
    conn: sqlite3.Connection, *, run_id: str, author: str, kind: str, text: str = "", target: int | None = None,
    name: str = "", question: str = "",
) -> None:
    conn.execute(
        "INSERT INTO norm_proposals (run_id, author, kind, text, target, name, question, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (run_id, author) DO UPDATE SET kind = excluded.kind, "
        "text = excluded.text, target = excluded.target, name = excluded.name, question = excluded.question, "
        "created_at = excluded.created_at WHERE norm_proposals.status = 'open'",
        (run_id, author, kind, text, target, name, question, now_iso()),
    )


def proposals(conn: sqlite3.Connection, run_id: str, status: str | None = None) -> list[Proposal]:
    sql, params = "SELECT * FROM norm_proposals WHERE run_id = ?", [run_id]
    if status:
        sql += " AND status = ?"
        params.append(status)
    return [_proposal(row) for row in conn.execute(sql + " ORDER BY id", params)]


def add_norm_critique(conn: sqlite3.Connection, proposal_id: int, author: str, points: str) -> None:
    conn.execute(
        "INSERT INTO norm_critiques (proposal_id, author, points, created_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT (proposal_id, author) DO UPDATE SET points = excluded.points, created_at = excluded.created_at",
        (proposal_id, author, points, now_iso()),
    )


def add_norm_vote(conn: sqlite3.Connection, proposal_id: int, author: str, stance: str, reason: str) -> None:
    conn.execute(
        "INSERT INTO norm_votes (proposal_id, author, stance, reason, created_at) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT (proposal_id, author) DO UPDATE SET stance = excluded.stance, reason = excluded.reason, "
        "created_at = excluded.created_at",
        (proposal_id, author, stance, reason, now_iso()),
    )


def critiques_of(conn: sqlite3.Connection, proposal_id: int) -> list[tuple[str, str]]:
    rows = conn.execute("SELECT author, points FROM norm_critiques WHERE proposal_id = ? ORDER BY rowid", (proposal_id,))
    return [(row["author"], row["points"]) for row in rows]


def current_norms(conn: sqlite3.Connection) -> list[tuple[int, str]]:
    return [(row["number"], row["text"]) for row in conn.execute("SELECT number, text FROM norms ORDER BY number")]


def dimensions(
    conn: sqlite3.Connection, exclude_run: str | None = None, before_run: str | None = None,
) -> list[Dimension]:
    """Adopted dimensions; `before_run` keeps only those adopted after runs created before that run."""
    sql, params = "SELECT * FROM dimensions WHERE adopted_run IS NOT ?", [exclude_run]
    if before_run is not None:
        sql += (" AND adopted_run IN (SELECT a.id FROM runs a, runs r WHERE r.id = ? "
                "AND (a.created_at, a.id) < (r.created_at, r.id))")
        params.append(before_run)
    rows = conn.execute(sql + " ORDER BY id", params)
    return [Dimension(row["id"], row["name"], row["question"], row["adopted_run"]) for row in rows]


def _apply(conn: sqlite3.Connection, p: Proposal) -> bool:
    if p.kind == "norm":
        number = conn.execute("SELECT COALESCE(MAX(number), 0) + 1 FROM norms").fetchone()[0]
        conn.execute("INSERT INTO norms (number, text, proposal_id, updated_at) VALUES (?, ?, ?, ?)",
                     (number, p.text, p.id, now_iso()))
        return True
    if p.kind == "amendment":
        updated = conn.execute("UPDATE norms SET text = ?, proposal_id = ?, updated_at = ? WHERE number = ?",
                               (p.text, p.id, now_iso(), p.target))
        return updated.rowcount == 1
    try:
        conn.execute("INSERT INTO dimensions (name, question, proposal_id, adopted_run) VALUES (?, ?, ?, ?)",
                     (p.name, p.question, p.id, p.run_id))
    except sqlite3.IntegrityError:
        return False
    return True


def decide(conn: sqlite3.Connection, run_id: str, agent_ids: Sequence[str], quorum_slack: int) -> list[Proposal]:
    """Close every open proposal of the run with the run consensus rule; apply the adopted ones."""
    members = set(agent_ids)
    needed = max(1, len(members) - quorum_slack)
    decided = []
    with transaction(conn):
        for p in proposals(conn, run_id, "open"):
            stances = {p.author: "support"}
            for row in conn.execute("SELECT author, stance FROM norm_votes WHERE proposal_id = ?", (p.id,)):
                stances[row["author"]] = row["stance"]
            supporters = {a for a, s in stances.items() if s == "support" and a in members}
            critiqued = conn.execute(
                "SELECT 1 FROM norm_critiques WHERE proposal_id = ? AND author != ?", (p.id, p.author)
            ).fetchone() is not None
            status = "adopted" if critiqued and len(supporters) >= needed and _apply(conn, p) else "rejected"
            conn.execute("UPDATE norm_proposals SET status = ? WHERE id = ?", (status, p.id))
            decided.append(replace(p, status=status))
    return decided


def render_norms(conn: sqlite3.Connection) -> str:
    norms, dims = current_norms(conn), dimensions(conn)
    if not norms and not dims:
        return ""
    lines = [f"{number}. {text}" for number, text in norms] or ["(no norms adopted yet)"]
    if dims:
        lines += ["", "## Rating dimensions", ""] + [f'- "{d.name}": {d.question}' for d in dims]
    return "\n".join(lines) + "\n"


def render_proposal(p: Proposal, who: Who, norms: Mapping[int, str]) -> str:
    if p.kind == "norm":
        what = f'a new norm: "{p.text}"'
    elif p.kind == "amendment":
        what = f'amend norm {p.target} (now: "{norms.get(p.target, "?")}") to: "{p.text}"'
    else:
        what = f'a new rating dimension "{p.name}": {p.question}'
    return f"Proposal {p.id} by {who(p.author)}: {what}"
