"""What the watcher shows, as plain data. Built from events (live status) and the database (content)."""

import json
from dataclasses import dataclass, field
from typing import Any

from aiswarm.board import Entry
from aiswarm.view import Who

FEED_MAX = 200
_SUMMARY = {"propose": "answer_summary", "critique": "points", "adopt": "reason", "refine": "change",
            "call_consensus": "synthesis"}


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
