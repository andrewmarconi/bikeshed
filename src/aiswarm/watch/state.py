"""What the watcher shows, as plain data. Built from events (live status) and the database (content)."""

import json
import sqlite3
from dataclasses import dataclass, field, replace
from typing import Any

from aiswarm.board import Board, Entry
from aiswarm.consensus import call_status
from aiswarm.postrun import STEPS
from aiswarm.profiles import ProfileStore
from aiswarm.reviews import reputation
from aiswarm.view import Who, make_who

FEED_MAX = 2000  # the feed wraps, so only a runaway entry (e.g. raw JSON) is cut
_SUMMARY = {"propose": "answer_summary", "critique": "points", "adopt": "reason", "refine": "change",
            "call_consensus": "synthesis", "think": "text", "private_message": "text", "abstain": "reason"}


@dataclass
class AgentCard:
    agent_id: str
    name: str
    headline: str = ""
    control: bool = False
    reputation: float | None = None
    state: str = "idle"
    last: str = ""


@dataclass(frozen=True)
class FeedLine:
    entry_id: int
    text: str


@dataclass(frozen=True)
class CallLine:
    entry_id: int
    caller: str
    supporters: int
    objectors: int


@dataclass(frozen=True)
class FileLine:
    path: str
    version: int
    author: str
    round: int


@dataclass(frozen=True)
class PostRunRow:
    step: str
    done: int
    failed: int
    failed_agents: list[str]


def feed_text(entry: Entry, who: Who) -> str:
    p = entry.payload
    head = f"#{entry.id} r{entry.round} {who(entry.author)} {entry.type}"
    if p.get("target") is not None:
        head += f" → #{p['target']}"
    if entry.type == "pass":
        body = f"(failed: {p['failure']})" if p.get("failure") else ""
    elif entry.type == "vote":
        body = f"{p.get('stance', '')}: {p.get('reason', '')}"
    elif entry.type == "write_file":
        body = f"{p.get('path', '')} v{p.get('version', '')}: {p.get('note', '')}"
    elif entry.type in _SUMMARY:
        body = str(p.get(_SUMMARY[entry.type], ""))
    else:
        body = json.dumps(p, ensure_ascii=False)
    text = f"{head}: {' '.join(body.split())}" if body.strip() else head
    return text if len(text) <= FEED_MAX else text[: FEED_MAX - 1] + "…"


@dataclass
class RunState:
    run_id: str
    goal: str
    label: str
    category: str
    max_rounds: int
    phase: str = "waiting"
    round: int = 0
    closed_reason: str | None = None
    cards: dict[str, AgentCard] = field(default_factory=dict)
    feed: list[FeedLine] = field(default_factory=list)
    calls: list[CallLine] = field(default_factory=list)
    files: list[FileLine] = field(default_factory=list)
    postrun: list[PostRunRow] = field(default_factory=list)
    last_entry: int = 0
    postrun_seen: bool = False
    current_step: str | None = None
    profiles_stale: bool = True  # names, headlines and reputation need reloading
    bad_lines: int = 0

    @property
    def finished(self) -> bool:
        return self.phase == "done"

    def card(self, agent_id: str) -> AgentCard:
        return self.cards.setdefault(agent_id, AgentCard(agent_id, agent_id))

    def apply(self, event: dict[str, Any]) -> None:
        name = event.get("event")
        if name in ("agent_step_started", "agent_step_finished", "action_posted") and not event.get("agent"):
            return  # malformed; the runner always names the agent
        if name == "run_started":
            self.phase = "rounds"
            for agent_id in event.get("agents", []):
                self.card(agent_id)
        elif name == "round_started":
            self.phase, self.round = "rounds", int(event.get("round", self.round))
        elif name == "agent_step_started":
            self.card(event["agent"]).state = "thinking…"
        elif name == "agent_step_finished":
            failure = event.get("failure")
            self.card(event["agent"]).state = f"failed: {failure}" if failure else "idle"
        elif name == "action_posted":
            self.card(event["agent"]).last = f"{event.get('type')} #{event.get('entry')}"
        elif name == "run_paused":
            self.phase = f"paused — resume with aiswarm run --resume {self.run_id}"
        elif name == "run_closed":
            self.closed_reason = event.get("reason")
            self.phase = f"closed: {self.closed_reason}"
            self.profiles_stale = True
        elif name == "postrun_started":
            self.phase, self.postrun_seen = "post-run", True
        elif name == "postrun_step_finished":
            self.phase, self.postrun_seen = "post-run", True
            self.current_step = event.get("step")
            self.profiles_stale = True
        elif name == "postrun_paused":
            self.phase = f"post-run paused — resume with aiswarm postrun {self.run_id}"
        elif name == "postrun_finished":
            self.phase, self.current_step, self.profiles_stale = "done", None, True

    def refresh(self, conn: sqlite3.Connection, store: ProfileStore, half_life: float) -> None:
        """Pull content from the database. Atomic: on any error nothing in the state has changed."""
        run = conn.execute("SELECT agent_ids FROM runs WHERE id = ?", (self.run_id,)).fetchone()
        agent_ids = json.loads(run["agent_ids"]) if run else list(self.cards)
        entries = Board(conn, self.run_id).entries()
        known = set(store.ids())
        cards = {a: replace(c) for a, c in self.cards.items()}
        for agent_id in agent_ids:
            cards.setdefault(agent_id, AgentCard(agent_id, agent_id))
        if self.profiles_stale:
            for agent_id, card in cards.items():
                profile = store.public(agent_id) if agent_id in known else None
                card.name = profile.name if profile else agent_id
                card.headline = profile.headline if profile else ""
                card.control = store.record(agent_id).control if agent_id in known else False
                card.reputation = reputation(conn, agent_id, half_life).score
        who = make_who({a: store.public(a) for a in cards if a in known})
        new = [FeedLine(e.id, feed_text(e, who)) for e in entries if e.id > self.last_entry]
        calls = []
        for e in reversed(entries):
            if e.type == "call_consensus":
                status = call_status(entries, e.id)
                calls.append(CallLine(e.id, who(e.author), len(status.supporters), len(status.objectors)))
        latest: dict[str, FileLine] = {}
        for e in entries:
            if e.type == "write_file":
                path = str(e.payload.get("path", ""))
                latest[path] = FileLine(path, int(e.payload.get("version", 0)), e.author, e.round)
        rows = conn.execute("SELECT step, agent_id, status FROM postrun_steps WHERE run_id = ?",
                            (self.run_id,)).fetchall()
        postrun = []
        for step in STEPS:
            mine = [r for r in rows if r["step"] == step]
            postrun.append(PostRunRow(step, sum(r["status"] == "done" for r in mine),
                                      sum(r["status"] == "failed" for r in mine),
                                      sorted({r["agent_id"] for r in mine if r["status"] == "failed"})))
        # everything was read: only now change the state
        self.cards = cards
        self.feed = self.feed + new
        self.last_entry = max([self.last_entry, *(line.entry_id for line in new)])
        self.calls = calls
        self.files = sorted(latest.values(), key=lambda f: f.path)
        self.postrun = postrun
        self.postrun_seen = self.postrun_seen or bool(rows)
        self.profiles_stale = False
