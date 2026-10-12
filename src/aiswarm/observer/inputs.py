"""What each judgment reads. All text comes from the community store as of the end of the run."""

import sqlite3
from datetime import datetime, timedelta

from aiswarm.board import Board, public
from aiswarm.observer.metrics import BenchmarkAnswer, RunInfo
from aiswarm.profiles import ProfileStore
from aiswarm.view import Who, render_entry

NONE = "(none)"


def _after(run: RunInfo) -> datetime:
    return datetime.fromisoformat(run.end) + timedelta(seconds=1)


def _journal(conn: sqlite3.Connection, run_id: str, agent_id: str) -> str:
    rows = conn.execute("SELECT section, text FROM journals WHERE run_id = ? AND agent_id = ? ORDER BY created_at",
                        (run_id, agent_id)).fetchall()
    return "\n\n".join(f"### {r['section']} reviews\n{r['text'].strip()}" for r in rows) or NONE


def _reviews_by(conn: sqlite3.Connection, run_id: str, agent_id: str, who: Who | None, *, include_self: bool) -> str:
    rows = conn.execute("SELECT * FROM reviews WHERE run_id = ? AND reviewer = ? ORDER BY id",
                        (run_id, agent_id)).fetchall()
    parts = []
    for r in rows:
        if r["is_self"] and not include_self:
            continue
        heading = "self-review" if r["is_self"] else f"about {who(r['reviewee']) if who else r['reviewee']}"
        parts.append(f"### {heading}\nmore of: {r['more_of']}\nless of: {r['less_of']}\n"
                     f"private note: {r['private_note']}\noverall: {r['overall']} — {r['overall_reason']}")
    return "\n\n".join(parts) or NONE


def themes_inputs(conn: sqlite3.Connection, store: ProfileStore, run: RunInfo, agent_id: str) -> dict[str, str]:
    at = _after(run)
    files = {name: store.file_at(agent_id, name, at) or NONE for name in ("self.md", "lessons.md", "temperament.md")}
    return {"journal": _journal(conn, run.id, agent_id), "reviews written": _reviews_by(conn, run.id, agent_id, None, include_self=True),
            **files}


def private_public_inputs(conn: sqlite3.Connection, run: RunInfo, agent_id: str, who: Who) -> dict[str, str] | None:
    reviews = _reviews_by(conn, run.id, agent_id, who, include_self=False)  # colleagues only
    if reviews == NONE:
        return None
    return {"journal": _journal(conn, run.id, agent_id), "reviews given": reviews}


def think_vs_act_inputs(conn: sqlite3.Connection, run: RunInfo, agent_id: str, who: Who) -> dict[str, str] | None:
    """One agent's own think notes, its own public entries, and the private messages it sent. Nothing it received."""
    own = [e for e in Board(conn, run.id).entries() if e.author == agent_id]
    notes = [e for e in own if e.type == "think"]
    if not notes:
        return None

    def show(entries) -> str:
        return "\n\n".join(render_entry(e, who) for e in entries) or NONE

    return {"think notes": show(notes), "public actions": show(public(own)),
            "private messages sent": show([e for e in own if e.type == "private_message"])}


def self_image_inputs(store: ProfileStore, runs: list[RunInfo], run: RunInfo, agent_id: str) -> dict[str, str]:
    index = next(i for i, r in enumerate(runs) if r.id == run.id)
    earlier = [r for r in runs[:index] if agent_id in r.agent_ids]
    now = _after(run)
    out = {"self.md now": store.file_at(agent_id, "self.md", now) or NONE,
           "public profile now": store.file_at(agent_id, "public.toml", now) or NONE}
    if earlier:
        then = _after(earlier[-1])
        out["self.md before"] = store.file_at(agent_id, "self.md", then) or NONE
        out["public profile before"] = store.file_at(agent_id, "public.toml", then) or NONE
    else:
        out["self.md before"] = out["public profile before"] = "(no earlier run)"
    return out


def benchmark_inputs(answers: list[BenchmarkAnswer], run_id: str) -> dict[str, str]:
    index = next(i for i, a in enumerate(answers) if a.run_id == run_id)
    earlier = "\n\n".join(f"### {a.run_id}\n{a.text}" for a in answers[:index]) or NONE
    return {"this answer": answers[index].text, "earlier answers": earlier}
