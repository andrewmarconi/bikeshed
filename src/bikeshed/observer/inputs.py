"""What each judgment reads. All text comes from the community store as of the end of the run."""

import sqlite3
from datetime import datetime, timedelta

from bikeshed.board import Board
from bikeshed.observer.metrics import BenchmarkAnswer, RunInfo
from bikeshed.profiles import ProfileStore
from bikeshed.view import Who, render_entry

NONE = "(none)"


def _after(run: RunInfo) -> datetime:
    return datetime.fromisoformat(run.end) + timedelta(seconds=1)


def _journal(conn: sqlite3.Connection, run_id: str, agent_id: str, section: str | None = None) -> str:
    rows = conn.execute("SELECT section, text FROM journals WHERE run_id = ?1 AND agent_id = ?2 "
                        "AND (?3 IS NULL OR section = ?3) ORDER BY created_at", (run_id, agent_id, section)).fetchall()
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
    # the journal written before the agent read its reviews, so reactions to them don't colour the comparison
    return {"journal": _journal(conn, run.id, agent_id, section="before"), "reviews given": reviews}


def think_vs_act_inputs(conn: sqlite3.Connection, run: RunInfo, agent_id: str, who: Who) -> dict[str, str] | None:
    """Round by round: one agent's think note and what it then did in that round (its public entries and the
    private messages it sent). Failed turns are left out, and a round with nothing else is dropped. Nothing it received."""
    own = [e for e in Board(conn, run.id).entries() if e.author == agent_id]

    def acted(e) -> bool:
        failed = e.type == "pass" and "failure" in e.payload
        return e.type != "think" and not failed and (e.visible_to is None or e.type == "private_message")

    def show(entries) -> str:
        return "\n\n".join(render_entry(e, who) for e in entries)

    out: dict[str, str] = {}
    for rnd in sorted({e.round for e in own if e.type == "think"}):
        done = [e for e in own if e.round == rnd and acted(e)]
        if done:
            out[f"round {rnd} think note"] = show([e for e in own if e.round == rnd and e.type == "think"])
            out[f"round {rnd} actions"] = show(done)
    return out or None


def before_moment(store: ProfileStore, runs: list[RunInfo], run: RunInfo, agent_id: str) -> datetime | None:
    """When an agent's "before" snapshot is read: the later of the end of its latest run that ended before this one,
    and its latest reset to seed before this run ended. Runs are compared by when they ended, not when they began."""
    end = datetime.fromisoformat(run.end)
    ends = [datetime.fromisoformat(r.end) for r in runs
            if r.id != run.id and agent_id in r.agent_ids and datetime.fromisoformat(r.end) < end]
    resets = [c.at for c in store.history(agent_id)
              if c.subject == f"reset control agent {agent_id} to seed" and c.at < end]
    moments = ends + resets
    return max(moments) if moments else None


def self_image_inputs(store: ProfileStore, runs: list[RunInfo], run: RunInfo, agent_id: str) -> dict[str, str]:
    now = _after(run)
    out = {"self.md now": store.file_at(agent_id, "self.md", now) or NONE,
           "public profile now": store.file_at(agent_id, "public.toml", now) or NONE}
    moment = before_moment(store, runs, run, agent_id)
    if moment is not None:
        then = moment + timedelta(seconds=1)
        out["self.md before"] = store.file_at(agent_id, "self.md", then) or NONE
        out["public profile before"] = store.file_at(agent_id, "public.toml", then) or NONE
    else:
        out["self.md before"] = out["public profile before"] = "(no earlier run)"
    return out


def benchmark_inputs(answers: list[BenchmarkAnswer], run_id: str) -> dict[str, str]:
    index = next(i for i, a in enumerate(answers) if a.run_id == run_id)
    earlier = "\n\n".join(f"### {a.run_id}\n{a.text}" for a in answers[:index]) or NONE
    return {"this answer": answers[index].text, "earlier answers": earlier}
