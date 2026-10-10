"""An agent's timeline: one chronological, user-only markdown view of what it did, wrote, and received."""

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime

from aiswarm.profiles import ProfileStore
from aiswarm.reviews import ratings_for, render_review, responses_for, reviews_by, reviews_of
from aiswarm.view import Who, make_who

EXCERPT = 300


@dataclass(frozen=True)
class Item:
    at: datetime
    run_id: str | None  # None: placed by time
    title: str
    body: str = ""


def _at(text: str) -> datetime:
    return datetime.fromisoformat(text)


def _excerpt(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= EXCERPT else text[:EXCERPT] + "…"


def resolve_agent(store: ProfileStore, key: str) -> str | None:
    """An agent id from its id, current name, or a former name (case-insensitive)."""
    if key in store.ids():
        return key
    folded = key.casefold()
    profiles = {i: store.public(i) for i in store.ids()}
    for agent_id, profile in profiles.items():
        if profile and profile.name.casefold() == folded:
            return agent_id
    for agent_id, profile in profiles.items():
        if profile and any(name.casefold() == folded for name in profile.former_names):
            return agent_id
    return None


def _rename(patch: str) -> tuple[str, str] | None:
    old = re.search(r'^-name = "(.*)"$', patch, re.M)
    new = re.search(r'^\+name = "(.*)"$', patch, re.M)
    return (old.group(1), new.group(1)) if old and new else None


def _git_items(store: ProfileStore, agent_id: str) -> list[Item]:
    items = []
    for commit in store.history(agent_id):
        title = f"{commit.subject} (by {commit.author})"
        if renamed := _rename(commit.patch):
            title += f" — renamed {renamed[0]} → {renamed[1]}"
        items.append(Item(commit.at, None, title, f"```diff\n{commit.patch}\n```" if commit.patch else ""))
    return items


def _review_items(conn: sqlite3.Connection, agent_id: str, who: Who) -> list[Item]:
    received = reviews_of(conn, agent_id, include_self=True)
    given = [r for r in reviews_by(conn, agent_id) if not r.is_self]
    reviews = received + given
    ids = [r.id for r in reviews]
    responses, ratings = responses_for(conn, ids), ratings_for(conn, ids)
    created = {row["id"]: row["created_at"] for row in conn.execute(
        "SELECT id, created_at FROM reviews WHERE reviewer = ? OR reviewee = ?", (agent_id, agent_id))}
    items = []
    for r in reviews:
        if r.is_self:
            title = "self-review"
        elif r.reviewee == agent_id:
            title = f"review received from {who(r.reviewer)}"
        else:
            title = f"review given to {who(r.reviewee)}"
        body = render_review(r, who, private=True, response=responses.get(r.id), ratings=ratings[r.id])
        items.append(Item(_at(created[r.id]), r.run_id, title, body))
    return items


def _credit_items(conn: sqlite3.Connection, agent_id: str, who: Who) -> list[Item]:
    items = []
    for closed in conn.execute("SELECT run_id, payload, created_at FROM entries WHERE type = 'run_closed'"):
        call_id = json.loads(closed["payload"]).get("call_id")
        if not call_id:
            continue
        call = conn.execute("SELECT author, payload FROM entries WHERE id = ?", (call_id,)).fetchone()
        credits = json.loads(call["payload"]).get("credits", [])
        at, run_id = _at(closed["created_at"]), closed["run_id"]
        if call["author"] == agent_id:
            body = "\n".join(f"- {who(c['agent_id'])}: {c['contribution']}" for c in credits) or "(credited no one)"
            items.append(Item(at, run_id, "credits given in the closing call", body))
        for credit in credits:
            if credit.get("agent_id") == agent_id:
                items.append(Item(at, run_id, f"credited by {who(call['author'])}", credit.get("contribution", "")))
    return items


def _db_items(conn: sqlite3.Connection, agent_id: str, who: Who) -> list[Item]:
    items = [Item(_at(r["created_at"]), r["run_id"], f"journal ({r['section']} reviews)", r["text"].strip())
             for r in conn.execute("SELECT * FROM journals WHERE agent_id = ?", (agent_id,))]
    items += _review_items(conn, agent_id, who) + _credit_items(conn, agent_id, who)
    for r in conn.execute("SELECT * FROM norm_proposals WHERE author = ?", (agent_id,)):
        what = r["text"] or f"{r['name']}: {r['question']}"
        items.append(Item(_at(r["created_at"]), r["run_id"], f"proposed a {r['kind']} (#{r['id']}, {r['status']})", what))
    for r in conn.execute("SELECT c.points, c.created_at, p.id, p.run_id FROM norm_critiques c "
                          "JOIN norm_proposals p ON p.id = c.proposal_id WHERE c.author = ?", (agent_id,)):
        items.append(Item(_at(r["created_at"]), r["run_id"], f"critiqued proposal #{r['id']}", r["points"]))
    for r in conn.execute("SELECT v.stance, v.reason, v.created_at, p.id, p.run_id FROM norm_votes v "
                          "JOIN norm_proposals p ON p.id = v.proposal_id WHERE v.author = ?", (agent_id,)):
        items.append(Item(_at(r["created_at"]), r["run_id"], f"voted {r['stance']} on proposal #{r['id']}", r["reason"]))
    for r in conn.execute("SELECT t.reasoning, t.run_id, e.id, e.type, e.created_at FROM traces t "
                          "JOIN entries e ON e.id = t.entry_id WHERE t.agent_id = ?", (agent_id,)):
        items.append(Item(_at(r["created_at"]), r["run_id"], f"reasoning behind #{r['id']} {r['type']}",
                          _excerpt(r["reasoning"])))
    for r in conn.execute("SELECT run_id, step, subject, reasoning, updated_at FROM postrun_steps "
                          "WHERE agent_id = ? AND reasoning IS NOT NULL", (agent_id,)):
        subject = f" ({r['subject']})" if r["subject"] else ""
        items.append(Item(_at(r["updated_at"]), r["run_id"], f"reasoning in post-run {r['step']}{subject}",
                          _excerpt(r["reasoning"])))
    return items


def timeline(conn: sqlite3.Connection, store: ProfileStore, agent_id: str, since: str | None = None) -> str:
    who = make_who({i: store.public(i) for i in store.ids()})
    runs = [(r["id"], r["goal"], _at(r["created_at"]))
            for r in conn.execute("SELECT id, goal, created_at FROM runs ORDER BY rowid")]
    order = {run_id: n for n, (run_id, _, _) in enumerate(runs)}
    if since is not None and since not in order:
        raise KeyError(since)

    def run_of(item: Item) -> str | None:
        if item.run_id is not None:
            return item.run_id
        latest = None
        for run_id, _, started in runs:
            if started <= item.at:
                latest = run_id
        return latest

    groups: dict[str | None, list[Item]] = {}
    for item in _git_items(store, agent_id) + _db_items(conn, agent_id, who):
        groups.setdefault(run_of(item), []).append(item)
    keys: list[str | None] = ([None] if None in groups else []) + [r for r, _, _ in runs if r in groups]
    if since is not None:
        keys = [k for k in keys if k is not None and order[k] >= order[since]]

    profile = store.public(agent_id)
    out = [f"# Timeline: {who(agent_id)}"]
    if profile and profile.former_names:
        out.append(f"formerly: {', '.join(profile.former_names)}")
    goals = {run_id: goal for run_id, goal, _ in runs}
    for key in keys:
        out += ["", "## Before any run" if key is None else f"## {key} — {goals[key]}"]
        for item in sorted(groups[key], key=lambda i: i.at):
            out += ["", f"- **{item.at.isoformat(timespec='seconds')}** {item.title}"]
            if item.body:
                out += ["", item.body]
    if not keys:
        out += ["", "_Nothing recorded yet._"]
    return "\n".join(out) + "\n"
