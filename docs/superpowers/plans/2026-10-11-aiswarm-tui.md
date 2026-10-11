# AISwarm TUI Implementation Plan (Plan 3c)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `aiswarm watch [RUN_ID]` opens a live, read-only Textual view of a run — board feed, agent cards, calls for consensus, workspace, and the post-run grid — and can open finished runs.

**Architecture:** `aiswarm.watch` package: `source.py` (event tail, read-only community access, `Watcher` that polls) → `state.py` (pure `RunState`: `apply(event)` + `refresh(conn, store)`) → `render.py` (pure text for each panel) → `app.py` (thin Textual app, 1 s tick). CLI command in `cli.py`.

**Tech Stack:** Python 3.14, uv, Textual 8.2.8 (`Log`, `Static`, `Horizontal`/`Vertical`, `App.run_test()` + `Pilot`), sqlite3, pytest + pytest-asyncio (auto mode).

**Spec:** `docs/superpowers/specs/2026-10-11-aiswarm-tui-design.md`

## Global Constraints

- The watcher writes nothing: `connect_readonly`, `ProfileStore(create=False)`, no files created anywhere.
- No module on the agent path imports `aiswarm.watch`.
- All `Static` widgets use `markup=False` (agent text and `[control]` must render literally).
- Feed lines are at most 200 characters.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Before any commit on `develop`'s checkout a lab run may be active — implement in a sibling worktree and merge only when the lab is idle.
- Test command for the whole suite: `uv run pytest -q`.

## Plan-level decisions (deviations from the spec, argued here)

- **Workspace panel source:** the `files` table has no round column; the panel is built from the run's `write_file` entries (path, version, author, round), which exist only for successful writes.
- **Post-run grid "pending":** nothing records how many calls a step expects, so pending counts cannot be computed. The grid shows done and failed counts, the agents whose step failed, and marks the step most recently reported as running (`← running`).
- **Polling stops** when the phase reaches `done`. A finished run that never had a post-run keeps polling (cheap, harmless); its grid says "no post-run".

## Review Focus

1. **A half-written last line in `events.jsonl`** (the runner is mid-write) — must wait for the next tick, never be counted as broken (Task 1 test).
2. **A refresh that fails mid-way** (database locked) — the previous state must stay intact; nothing half-updated (Task 3 test).
3. **Agent text containing `[`…`]` or very long text** — renders literally and is truncated to 200 characters (Task 2 and Task 5 tests).
4. **Opening a run whose events file does not exist yet** — phase `waiting`, no crash, picks up events when the file appears (Task 3 test).
5. **Watching never changes the community** — DB bytes and git HEAD unchanged (Task 6 test).

---

### Task 1: Textual dependency and the event tail

**Files:**
- Modify: `pyproject.toml`
- Create: `src/aiswarm/watch/__init__.py`, `src/aiswarm/watch/source.py`
- Test: `tests/test_watch_source.py`

**Interfaces:**
- Produces: `EventTail(path: Path)` with `read() -> list[dict]` and attribute `bad: int`.

- [ ] **Step 1: Add Textual**

Run: `uv add "textual>=8.2.8"`
Expected: `uv run python -c "import textual; print(textual.__version__)"` prints `8.2.8` or later.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_watch_source.py`:

```python
from aiswarm.watch.source import EventTail


def test_reads_only_new_complete_lines(tmp_path):
    path = tmp_path / "events.jsonl"
    tail = EventTail(path)
    assert tail.read() == []  # no file yet
    path.write_text('{"event": "a"}\n{"event": "b"}\n{"event": "c"')
    assert [e["event"] for e in tail.read()] == ["a", "b"]
    assert tail.read() == []
    with path.open("a") as f:
        f.write(', "x": 1}\n{"event": "d"}\n')
    assert [e["event"] for e in tail.read()] == ["c", "d"]
    assert tail.bad == 0


def test_broken_lines_are_skipped_and_counted(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text('{"event": "a"}\nnot json\n[1, 2]\n\n{"event": "b"}\n')
    tail = EventTail(path)
    assert [e["event"] for e in tail.read()] == ["a", "b"]
    assert tail.bad == 2
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_watch_source.py`
Expected: FAIL — `No module named 'aiswarm.watch'`.

- [ ] **Step 4: Implement**

Create `src/aiswarm/watch/__init__.py`:

```python
"""`aiswarm watch`: a live, read-only terminal view of a run, for the user only."""
```

Create `src/aiswarm/watch/source.py`:

```python
"""Read-only sources for the watcher: the run's events file and the community store."""

import json
from pathlib import Path
from typing import Any


class EventTail:
    """Reads new complete lines of an events.jsonl file; a partial last line waits for the next read."""

    def __init__(self, path: Path):
        self.path = path
        self.offset = 0
        self.bad = 0

    def read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with self.path.open("rb") as handle:
            handle.seek(self.offset)
            data = handle.read()
        end = data.rfind(b"\n")
        if end < 0:
            return []
        chunk = data[: end + 1]
        self.offset += len(chunk)
        events = []
        for line in chunk.splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                self.bad += 1
                continue
            if isinstance(event, dict):
                events.append(event)
            else:
                self.bad += 1
        return events
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest -q tests/test_watch_source.py`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock src/aiswarm/watch tests/test_watch_source.py
git commit -m "feat: textual dependency and an events tail that waits for complete lines

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: RunState — events and feed lines

**Files:**
- Create: `src/aiswarm/watch/state.py`
- Test: `tests/test_watch_state.py`

**Interfaces:**
- Consumes: `Entry` (existing `aiswarm.board`), `Who` (existing `aiswarm.view`).
- Produces:
  - dataclasses `AgentCard(agent_id, name, headline="", control=False, reputation: float | None = None, state="idle", last="")`, `FeedLine(entry_id, text)`, `CallLine(entry_id, caller, supporters, objectors)`, `FileLine(path, version, author, round)`, `PostRunRow(step, done, failed, failed_agents: list[str])`
  - `RunState(run_id, goal, label, category, max_rounds)` with fields `phase="waiting"`, `round=0`, `closed_reason=None`, `cards: dict[str, AgentCard]`, `feed`, `calls`, `files`, `postrun`, `last_entry=0`, `postrun_seen=False`, `current_step=None`, `profiles_stale=True`, `bad_lines=0`; methods `card(agent_id)`, `apply(event)`; property `finished`
  - `feed_text(entry: Entry, who: Who) -> str`; `FEED_MAX = 200`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_watch_state.py`:

```python
from aiswarm.board import Entry
from aiswarm.watch.state import FEED_MAX, RunState, feed_text


def state():
    return RunState("r1", "Pick a fruit.", "best-fruit", "contested", 12)


def who(agent_id):
    return {"a": "Ash (a)", "b": "Bea (b)"}.get(agent_id, f"{agent_id} ({agent_id})")


def entry(id_, type_, payload, author="a", round_=2):
    return Entry(id_, "r1", round_, author, type_, payload, [], "all", "t")


def test_events_drive_round_phase_and_cards():
    s = state()
    s.apply({"event": "run_started", "agents": ["a", "b"]})
    assert s.phase == "rounds" and set(s.cards) == {"a", "b"}
    s.apply({"event": "round_started", "round": 3})
    s.apply({"event": "agent_step_started", "round": 3, "agent": "a"})
    assert s.round == 3 and s.cards["a"].state == "thinking…"
    s.apply({"event": "agent_step_finished", "agent": "a", "action": "pass", "failure": "invalid_action"})
    s.apply({"event": "action_posted", "agent": "a", "entry": 7, "type": "pass", "failure": "invalid_action"})
    assert s.cards["a"].state == "failed: invalid_action" and s.cards["a"].last == "pass #7"
    s.apply({"event": "agent_step_finished", "agent": "b", "action": "propose", "failure": None})
    assert s.cards["b"].state == "idle"


def test_phases_through_pause_close_and_post_run():
    s = state()
    s.apply({"event": "run_paused", "round": 2})
    assert s.phase == "paused — resume with aiswarm run --resume r1"
    s.apply({"event": "run_closed", "reason": "budget", "call_id": None})
    assert s.phase == "closed: budget" and s.closed_reason == "budget" and not s.finished
    s.apply({"event": "postrun_started", "run_id": "r1"})
    s.apply({"event": "postrun_step_finished", "step": "review", "agent": "a", "status": "done"})
    assert s.phase == "post-run" and s.current_step == "review" and s.postrun_seen
    s.apply({"event": "postrun_paused", "step": "review"})
    assert s.phase == "post-run paused — resume with aiswarm postrun r1"
    s.apply({"event": "postrun_finished", "failed": 0})
    assert s.phase == "done" and s.finished and s.current_step is None


def test_unknown_events_are_ignored():
    s = state()
    s.apply({"event": "library_retrieved"})
    s.apply({"no": "event"})
    assert s.phase == "waiting"


def test_feed_text_per_type():
    assert feed_text(entry(5, "propose", {"answer_summary": "Mango", "method": "m"}), who) == \
        "#5 r2 Ash (a) propose: Mango"
    assert feed_text(entry(6, "critique", {"target": 5, "about": "answer", "points": "Too sweet."}, "b"), who) == \
        "#6 r2 Bea (b) critique → #5: Too sweet."
    assert feed_text(entry(7, "vote", {"target": 9, "stance": "support", "reason": "ok"}), who) == \
        "#7 r2 Ash (a) vote → #9: support: ok"
    assert feed_text(entry(8, "pass", {"failure": "invalid_action"}), who) == \
        "#8 r2 Ash (a) pass: (failed: invalid_action)"
    assert feed_text(entry(9, "pass", {}), who) == "#9 r2 Ash (a) pass"
    assert feed_text(entry(10, "write_file", {"path": "rules.md", "version": 3, "note": "tidy"}), who) == \
        "#10 r2 Ash (a) write_file: rules.md v3: tidy"


def test_feed_text_is_one_line_and_truncated():
    text = feed_text(entry(5, "propose", {"answer_summary": "line one\nline [two] " + "x" * 300, "method": "m"}), who)
    assert "\n" not in text and len(text) == FEED_MAX and text.endswith("…")
    assert "[two]" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_watch_state.py`
Expected: FAIL — `No module named 'aiswarm.watch.state'`.

- [ ] **Step 3: Implement**

Create `src/aiswarm/watch/state.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q tests/test_watch_state.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aiswarm/watch/state.py tests/test_watch_state.py
git commit -m "feat: watcher run state driven by run events, and one-line feed text per entry

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Refresh from the database, and the Watcher

**Files:**
- Modify: `src/aiswarm/watch/state.py`, `src/aiswarm/watch/source.py`
- Test: `tests/test_watch_state.py`, `tests/test_watch_source.py`

**Interfaces:**
- Consumes: `Board`, `call_status`, `reputation`, `STEPS`, `make_who`, `ProfileStore(create=False)`, `connect_readonly` (existing); `RunState`, `EventTail` (Tasks 1–2).
- Produces:
  - `RunState.refresh(conn, store, half_life: float) -> None` (atomic: assigns only after everything was read)
  - `Watcher(state, tail, conn, store, half_life)` with `poll() -> None`
  - `open_watcher(cfg: Config, run_id: str | None) -> Watcher` (raises `ValueError` for no runs, unknown run, or no community)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_watch_state.py`:

```python
from observer_helpers import add_entry, add_review, add_run  # noqa: E402


def refreshed(community, **kw):
    store, conn = community
    s = RunState("r1", "g", "r1", "ad hoc", 12)
    s.refresh(conn, store, 5.0)
    return s


def test_refresh_builds_feed_calls_files_and_cards(community):
    store, conn = community
    add_run(conn, "r1", agents=("a", "b"))
    p = add_entry(conn, "r1", "a", "propose", {"answer_summary": "Mango", "method": "m"})
    call = add_entry(conn, "r1", "b", "call_consensus", {"synthesis": "Mango it is", "credits": []}, refs=[p])
    add_entry(conn, "r1", "a", "vote", {"target": call, "stance": "support", "reason": "yes"})
    add_entry(conn, "r1", "a", "write_file", {"path": "rules.md", "version": 1, "note": "n"}, round=2)
    add_entry(conn, "r1", "b", "write_file", {"path": "rules.md", "version": 2, "note": "n"}, round=3)
    s = refreshed(community)
    assert [f.entry_id for f in s.feed] == [p, call, call + 1, call + 2, call + 3]
    assert s.feed[0].text == f"#{p} r1 Alpha (a) propose: Mango"
    assert s.calls == [type(s.calls[0])(call, "Bea (b)", 2, 0)]
    assert [(f.path, f.version, f.author, f.round) for f in s.files] == [("rules.md", 2, "b", 3)]
    assert s.cards["a"].name == "Alpha" and s.cards["a"].headline == "Alpha headline"
    assert s.cards["a"].reputation is None and not s.profiles_stale


def test_refresh_adds_only_new_entries(community):
    store, conn = community
    add_run(conn, "r1", agents=("a",))
    add_entry(conn, "r1", "a", "pass", {})
    s = refreshed(community)
    add_entry(conn, "r1", "a", "pass", {})
    s.refresh(conn, store, 5.0)
    assert len(s.feed) == 2


def test_reputation_and_names_reload_only_when_stale(community):
    store, conn = community
    add_run(conn, "r0", agents=("a", "b"))
    add_review(conn, "r0", "b", "a", 4)
    add_run(conn, "r1", agents=("a", "b"))
    s = refreshed(community)
    assert s.cards["a"].reputation == 4.0
    store.set_public("a", "Ashley", "h", "i")
    s.refresh(conn, store, 5.0)
    assert s.cards["a"].name == "Alpha"
    s.apply({"event": "postrun_step_finished", "step": "update", "agent": "a", "status": "done"})
    s.refresh(conn, store, 5.0)
    assert s.cards["a"].name == "Ashley"


def test_post_run_grid_counts_done_and_failed(community):
    store, conn = community
    add_run(conn, "r1", agents=("a", "b"))
    for agent, status in (("a", "done"), ("b", "failed")):
        conn.execute("INSERT INTO postrun_steps (run_id, step, agent_id, subject, status, detail, updated_at) "
                     "VALUES ('r1', 'journal', ?, '', ?, '', 't')", (agent, status))
    s = refreshed(community)
    journal = next(r for r in s.postrun if r.step == "journal")
    assert (journal.done, journal.failed, journal.failed_agents) == (1, 1, ["b"])
    assert [r.step for r in s.postrun][0] == "journal" and len(s.postrun) == 8


def test_a_failed_refresh_keeps_the_previous_state(community):
    import sqlite3

    store, conn = community
    add_run(conn, "r1", agents=("a",))
    add_entry(conn, "r1", "a", "pass", {})
    s = refreshed(community)
    before = (list(s.feed), list(s.calls), dict(s.cards))

    class Broken:
        def execute(self, *a, **k):
            raise sqlite3.OperationalError("database is locked")

    try:
        s.refresh(Broken(), store, 5.0)
    except sqlite3.OperationalError:
        pass
    assert (s.feed, s.calls, s.cards) == before
```

Append to `tests/test_watch_source.py`:

```python
import json  # noqa: E402

import pytest  # noqa: E402
from observer_helpers import add_entry, add_run  # noqa: E402

from aiswarm.config import Config  # noqa: E402
from aiswarm.watch.source import open_watcher  # noqa: E402


def cfg_for(tmp_path):
    cfg = Config.model_validate({"models": {"local": {"kind": "openai", "model": "m"}}})
    cfg.community_dir = tmp_path / "community"
    cfg.runs_dir = tmp_path / "runs"
    return cfg


def test_open_watcher_errors(tmp_path, community):
    cfg = cfg_for(tmp_path)
    with pytest.raises(ValueError, match="no runs yet"):
        open_watcher(cfg, None)
    store, conn = community
    add_run(conn, "r1")
    with pytest.raises(ValueError, match="no run 'nope'"):
        open_watcher(cfg, "nope")
    cfg.community_dir = tmp_path / "elsewhere"
    with pytest.raises(ValueError):
        open_watcher(cfg, None)


def test_watcher_waits_then_follows_events_and_entries(tmp_path, community):
    cfg = cfg_for(tmp_path)
    store, conn = community
    add_run(conn, "r1", status="running", goal_id="best-fruit", category="contested")
    add_run(conn, "r2", status="running")
    watcher = open_watcher(cfg, "r1")
    watcher.poll()
    assert watcher.state.phase == "waiting" and watcher.state.label == "best-fruit"
    events = cfg.runs_dir / "r1" / "events.jsonl"
    events.parent.mkdir(parents=True)
    events.write_text(json.dumps({"event": "run_started", "agents": ["a", "b"]}) + "\n"
                      + json.dumps({"event": "round_started", "round": 1}) + "\n")
    add_entry(conn, "r1", "a", "propose", {"answer_summary": "Mango", "method": "m"})
    watcher.poll()
    assert watcher.state.phase == "rounds" and watcher.state.round == 1
    assert len(watcher.state.feed) == 1
    assert open_watcher(cfg, None).state.run_id == "r2"  # latest by default


def test_max_rounds_comes_from_the_runs_start_provenance(tmp_path, community):
    cfg = cfg_for(tmp_path)
    store, conn = community
    add_run(conn, "r1")
    conn.execute("INSERT INTO run_provenance (run_id, phase, config, prompts, models, servers, fingerprint, "
                 "captured_at) VALUES ('r1', 'start', ?, '{}', '{}', '{}', 'f', 't')",
                 (json.dumps({"run": {"max_rounds": 6}}),))
    assert open_watcher(cfg, "r1").state.max_rounds == 6
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_watch_state.py tests/test_watch_source.py`
Expected: FAIL — `RunState` has no `refresh`; `cannot import name 'open_watcher'`.

- [ ] **Step 3: Implement refresh**

Add to `src/aiswarm/watch/state.py` (imports at top: `import sqlite3`, `from aiswarm.board import Board`, `from aiswarm.consensus import call_status`, `from aiswarm.postrun import STEPS`, `from aiswarm.profiles import ProfileStore`, `from aiswarm.reviews import reputation`, `from aiswarm.view import make_who`):

```python
    def refresh(self, conn: sqlite3.Connection, store: ProfileStore, half_life: float) -> None:
        """Pull content from the database. Atomic: on any error nothing in the state has changed."""
        run = conn.execute("SELECT agent_ids FROM runs WHERE id = ?", (self.run_id,)).fetchone()
        agent_ids = json.loads(run["agent_ids"]) if run else list(self.cards)
        entries = Board(conn, self.run_id).entries()
        cards = {a: AgentCard(**vars(c)) for a, c in self.cards.items()}
        for agent_id in agent_ids:
            cards.setdefault(agent_id, AgentCard(agent_id, agent_id))
        if self.profiles_stale:
            for agent_id, card in cards.items():
                profile = store.public(agent_id) if agent_id in store.ids() else None
                card.name = profile.name if profile else agent_id
                card.headline = profile.headline if profile else ""
                card.control = store.record(agent_id).control if agent_id in store.ids() else False
                card.reputation = reputation(conn, agent_id, half_life).score
        who = make_who({a: store.public(a) for a in cards if a in store.ids()})
        new = [FeedLine(e.id, feed_text(e, who)) for e in entries if e.id > self.last_entry]
        calls = []
        for e in reversed(entries):
            if e.type == "call_consensus":
                status = call_status(entries, e.id)
                calls.append(CallLine(e.id, who(e.author), len(status.supporters), len(status.objectors)))
        latest: dict[str, FileLine] = {}
        for e in entries:
            if e.type == "write_file":
                latest[e.payload.get("path", "")] = FileLine(e.payload.get("path", ""),
                                                             int(e.payload.get("version", 0)), e.author, e.round)
        rows = conn.execute("SELECT step, agent_id, status FROM postrun_steps WHERE run_id = ?",
                            (self.run_id,)).fetchall()
        postrun = []
        for step in STEPS:
            mine = [r for r in rows if r["step"] == step]
            postrun.append(PostRunRow(step, sum(r["status"] == "done" for r in mine),
                                      sum(r["status"] == "failed" for r in mine),
                                      sorted({r["agent_id"] for r in mine if r["status"] == "failed"})))
        # everything read: commit to the new state
        self.cards = cards
        self.feed = self.feed + new
        self.last_entry = max([self.last_entry, *(line.entry_id for line in new)])
        self.calls = calls
        self.files = sorted(latest.values(), key=lambda f: f.path)
        self.postrun = postrun
        self.postrun_seen = self.postrun_seen or bool(rows)
        self.profiles_stale = False
```

- [ ] **Step 4: Implement the Watcher**

Append to `src/aiswarm/watch/source.py` (imports at top: `import sqlite3`, `from dataclasses import dataclass`, `from aiswarm.config import Config`, `from aiswarm.db import connect_readonly`, `from aiswarm.profiles import ProfileError, ProfileStore`, `from aiswarm.watch.state import RunState`):

```python
@dataclass
class Watcher:
    state: RunState
    tail: EventTail
    conn: sqlite3.Connection
    store: ProfileStore
    half_life: float

    def poll(self) -> None:
        for event in self.tail.read():
            self.state.apply(event)
        self.state.bad_lines = self.tail.bad
        try:
            self.state.refresh(self.conn, self.store, self.half_life)
        except sqlite3.Error:
            pass  # e.g. the writer holds a lock; keep what we have and try again next tick


def _max_rounds(conn: sqlite3.Connection, run_id: str, default: int) -> int:
    row = conn.execute("SELECT config FROM run_provenance WHERE run_id = ? AND phase = 'start' ORDER BY id LIMIT 1",
                       (run_id,)).fetchone()
    try:
        return int(json.loads(row["config"])["run"]["max_rounds"]) if row else default
    except (KeyError, TypeError, ValueError):
        return default


def open_watcher(cfg: Config, run_id: str | None) -> Watcher:
    try:
        store = ProfileStore(cfg.community_dir, create=False)
    except ProfileError as exc:
        raise ValueError(str(exc)) from exc
    conn = connect_readonly(cfg.db_path)
    if run_id is None:
        row = conn.execute("SELECT * FROM runs ORDER BY rowid DESC LIMIT 1").fetchone()
        if row is None:
            raise ValueError("no runs yet; start one with `aiswarm run`")
    else:
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise ValueError(f"no run {run_id!r}")
    state = RunState(row["id"], row["goal"], row["goal_id"] or "ad hoc", row["goal_category"] or "ad hoc",
                     _max_rounds(conn, row["id"], cfg.run.max_rounds))
    return Watcher(state, EventTail(cfg.runs_dir / row["id"] / "events.jsonl"), conn, store,
                   cfg.run.reputation_half_life_runs)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest -q tests/test_watch_state.py tests/test_watch_source.py`
Expected: PASS. (If `RunState(...)` for `label` should show the goal id and the test expects `"best-fruit"`, the code above passes `goal_id` as `label` — consistent.)

- [ ] **Step 6: Commit**

```bash
git add src/aiswarm/watch tests/test_watch_state.py tests/test_watch_source.py
git commit -m "feat: watcher refreshes run content from the database atomically and follows the events file

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Panel text

**Files:**
- Create: `src/aiswarm/watch/render.py`
- Test: `tests/test_watch_render.py`

**Interfaces:**
- Consumes: `RunState` and its dataclasses (Tasks 2–3).
- Produces: `header(state) -> str`, `cards(state) -> str`, `calls(state) -> str`, `files(state) -> str`, `postrun(state) -> str`, `footer(state, following: bool) -> str`, `show_postrun(state) -> bool`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_watch_render.py`:

```python
from aiswarm.watch import render
from aiswarm.watch.state import AgentCard, CallLine, FileLine, PostRunRow, RunState


def state(**kw):
    s = RunState("r1", "Pick a fruit.", "best-fruit", "contested", 12)
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def test_header():
    assert render.header(state(round=4, phase="rounds")) == \
        "best-fruit (contested) · run r1 · round 4/12 · rounds — Pick a fruit."


def test_cards_show_control_reputation_state_and_last_action():
    s = state(cards={"a": AgentCard("a", "Ash", control=True, reputation=4.25, state="thinking…", last="critique #301"),
                     "b": AgentCard("b", "Bea")})
    text = render.cards(s)
    assert "Ash (a) [control] ★4.2  thinking…" in text and "  last: critique #301" in text
    assert "Bea (b) ★–  idle" in text and "  last: –" in text


def test_calls_and_files():
    s = state(calls=[CallLine(9, "Bea (b)", 3, 1)], files=[FileLine("rules.md", 3, "c", 4)])
    assert render.calls(s) == "Calls for consensus\n#9 by Bea (b): 3 for, 1 against"
    assert render.files(s) == "Workspace\nrules.md v3 (c, r4)"
    assert render.calls(state()) == "Calls for consensus\n(none yet)"
    assert render.files(state()) == "Workspace\n(no files yet)"


def test_postrun_grid():
    rows = [PostRunRow("journal", 5, 0, []), PostRunRow("review", 3, 2, ["b", "d"])]
    text = render.postrun(state(postrun=rows, postrun_seen=True, current_step="review"))
    assert "journal" in text and "done 5" in text
    assert "review" in text and "failed 2 (b, d)" in text and "← running" in text
    assert render.postrun(state(closed_reason="budget")) == "Post-run\nno post-run"


def test_show_postrun_only_after_the_rounds():
    assert not render.show_postrun(state(phase="rounds"))
    assert render.show_postrun(state(closed_reason="budget"))


def test_footer():
    assert render.footer(state(), True) == "q quit · f follow: on"
    assert render.footer(state(bad_lines=2), False) == "q quit · f follow: off · 2 unreadable event lines"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_watch_render.py`
Expected: FAIL — `cannot import name 'render'`.

- [ ] **Step 3: Implement**

Create `src/aiswarm/watch/render.py`:

```python
"""Plain text for each watcher panel. Pure functions of RunState, so they test without a terminal."""

from aiswarm.watch.state import RunState


def header(s: RunState) -> str:
    return f"{s.label} ({s.category}) · run {s.run_id} · round {s.round}/{s.max_rounds} · {s.phase} — {s.goal}"


def cards(s: RunState) -> str:
    lines = []
    for card in s.cards.values():
        rep = "–" if card.reputation is None else f"{card.reputation:.1f}"
        control = " [control]" if card.control else ""
        lines += [f"{card.name} ({card.agent_id}){control} ★{rep}  {card.state}", f"  last: {card.last or '–'}"]
    return "\n".join(lines) or "(no agents yet)"


def calls(s: RunState) -> str:
    lines = [f"#{c.entry_id} by {c.caller}: {c.supporters} for, {c.objectors} against" for c in s.calls]
    return "\n".join(["Calls for consensus", *(lines or ["(none yet)"])])


def files(s: RunState) -> str:
    lines = [f"{f.path} v{f.version} ({f.author}, r{f.round})" for f in s.files]
    return "\n".join(["Workspace", *(lines or ["(no files yet)"])])


def show_postrun(s: RunState) -> bool:
    return s.closed_reason is not None or s.postrun_seen


def postrun(s: RunState) -> str:
    if not s.postrun_seen:
        return "Post-run\nno post-run"
    lines = ["Post-run"]
    for row in s.postrun:
        failed = f"failed {row.failed}" + (f" ({', '.join(row.failed_agents)})" if row.failed_agents else "")
        running = "  ← running" if row.step == s.current_step else ""
        lines.append(f"{row.step:<15} done {row.done:<3} {failed}{running}")
    return "\n".join(lines)


def footer(s: RunState, following: bool) -> str:
    text = f"q quit · f follow: {'on' if following else 'off'}"
    if s.bad_lines:
        text += f" · {s.bad_lines} unreadable event lines"
    return text
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q tests/test_watch_render.py`
Expected: PASS. (`f"{4.25:.1f}"` is `4.2` under round-half-even; the test expects `★4.2`.)

- [ ] **Step 5: Commit**

```bash
git add src/aiswarm/watch/render.py tests/test_watch_render.py
git commit -m "feat: plain-text panels for the watcher

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: The Textual app

**Files:**
- Create: `src/aiswarm/watch/app.py`
- Test: `tests/test_watch_app.py`

**Interfaces:**
- Consumes: `Watcher`, `open_watcher` (Task 3), `render` (Task 4).
- Produces: `WatchApp(watcher: Watcher, interval: float = 1.0)` with `tick()`, `action_toggle_follow()`; widget ids `header`, `feed`, `cards`, `calls`, `files`, `postrun`, `footer`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_watch_app.py`:

```python
import json

from observer_helpers import add_entry, add_run
from textual.widgets import Log, Static

from aiswarm.config import Config
from aiswarm.watch.app import WatchApp
from aiswarm.watch.source import open_watcher


def setup(tmp_path, community):
    store, conn = community
    add_run(conn, "r1", agents=("a", "b"), status="running", goal_id="best-fruit", category="contested")
    add_entry(conn, "r1", "a", "propose", {"answer_summary": "Mango [ripe]", "method": "m"})
    cfg = Config.model_validate({"models": {"local": {"kind": "openai", "model": "m"}}})
    cfg.community_dir, cfg.runs_dir = tmp_path / "community", tmp_path / "runs"
    events = cfg.runs_dir / "r1" / "events.jsonl"
    events.parent.mkdir(parents=True)
    events.write_text(json.dumps({"event": "run_started", "agents": ["a", "b"]}) + "\n"
                      + json.dumps({"event": "round_started", "round": 1}) + "\n")
    return conn, events, open_watcher(cfg, "r1")


def text(app, widget_id):
    return str(app.query_one(f"#{widget_id}", Static).content)


async def test_renders_a_run_and_follows_new_entries(tmp_path, community):
    conn, events, watcher = setup(tmp_path, community)
    app = WatchApp(watcher, interval=3600)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        assert "best-fruit (contested) · run r1 · round 1/12 · rounds" in text(app, "header")
        assert "Alpha (a)" in text(app, "cards")
        log = app.query_one("#feed", Log)
        assert any("Mango [ripe]" in line for line in log.lines)
        add_entry(conn, "r1", "b", "critique", {"target": 1, "about": "answer", "points": "Too sweet."})
        app.tick()
        await pilot.pause()
        assert any("Too sweet." in line for line in log.lines)
        assert app.query_one("#calls", Static).display and not app.query_one("#postrun", Static).display


async def test_follow_toggle_and_quit(tmp_path, community):
    conn, events, watcher = setup(tmp_path, community)
    app = WatchApp(watcher, interval=3600)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("f")
        assert "follow: off" in text(app, "footer")
        await pilot.press("f")
        assert "follow: on" in text(app, "footer")
        await pilot.press("q")
    assert app.return_code == 0 or app.return_code is None


async def test_post_run_grid_replaces_calls_after_the_rounds(tmp_path, community):
    conn, events, watcher = setup(tmp_path, community)
    app = WatchApp(watcher, interval=3600)
    async with app.run_test(size=(140, 40)) as pilot:
        with events.open("a") as f:
            f.write(json.dumps({"event": "run_closed", "reason": "budget", "call_id": None}) + "\n")
            f.write(json.dumps({"event": "postrun_started", "run_id": "r1"}) + "\n")
        app.tick()
        await pilot.pause()
        assert app.query_one("#postrun", Static).display
        assert not app.query_one("#calls", Static).display and not app.query_one("#files", Static).display
        assert "post-run" in text(app, "header")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_watch_app.py`
Expected: FAIL — `No module named 'aiswarm.watch.app'`.

- [ ] **Step 3: Implement**

Create `src/aiswarm/watch/app.py`:

```python
"""The Textual view. Thin: every tick it polls the Watcher and renders RunState through render.py."""

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Log, Static

from aiswarm.watch import render
from aiswarm.watch.source import Watcher


class WatchApp(App):
    CSS = """
    #header { height: auto; padding: 0 1; background: $boost; }
    #body { height: 1fr; }
    #feed { width: 2fr; border: round $primary; }
    #side { width: 1fr; }
    #side Static { border: round $primary; padding: 0 1; height: auto; }
    #footer { height: 1; padding: 0 1; color: $text-muted; }
    """
    BINDINGS = [("q", "quit", "Quit"), ("f", "toggle_follow", "Follow")]

    def __init__(self, watcher: Watcher, interval: float = 1.0):
        super().__init__()
        self.watcher = watcher
        self.interval = interval
        self.following = True
        self.written = 0
        self.timer = None

    def compose(self) -> ComposeResult:
        yield Static(id="header", markup=False)
        with Horizontal(id="body"):
            yield Log(id="feed", auto_scroll=True)
            with Vertical(id="side"):
                yield Static(id="cards", markup=False)
                yield Static(id="calls", markup=False)
                yield Static(id="files", markup=False)
                yield Static(id="postrun", markup=False)
        yield Static(id="footer", markup=False)

    def on_mount(self) -> None:
        self.tick()
        self.timer = self.set_interval(self.interval, self.tick)

    def tick(self) -> None:
        self.watcher.poll()
        s = self.watcher.state
        log = self.query_one("#feed", Log)
        new = s.feed[self.written:]
        if new:
            at_end = self.written == 0 or log.is_vertical_scroll_end
            log.write_lines([line.text for line in new], scroll_end=self.following and at_end)
            self.written = len(s.feed)
        self.query_one("#header", Static).update(render.header(s))
        self.query_one("#cards", Static).update(render.cards(s))
        post = render.show_postrun(s)
        for widget_id, content in (("calls", render.calls(s)), ("files", render.files(s))):
            widget = self.query_one(f"#{widget_id}", Static)
            widget.update(content)
            widget.display = not post
        grid = self.query_one("#postrun", Static)
        grid.update(render.postrun(s))
        grid.display = post
        self._footer()
        if s.finished and self.timer is not None:
            self.timer.stop()

    def _footer(self) -> None:
        self.query_one("#footer", Static).update(render.footer(self.watcher.state, self.following))

    def action_toggle_follow(self) -> None:
        self.following = not self.following
        if self.following:
            self.query_one("#feed", Log).scroll_end(animate=False)
        self._footer()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q tests/test_watch_app.py`
Expected: PASS. If `Static.content` is not the attribute name in Textual 8.2.8, check `uv run python -c "from textual.widgets import Static; help(Static)"` and use the documented accessor (the docs list `content` and `visual`); record a ruling.

- [ ] **Step 5: Commit**

```bash
git add src/aiswarm/watch/app.py tests/test_watch_app.py
git commit -m "feat: the Textual watch view with a following board feed and a post-run grid

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: The `watch` command and isolation

**Files:**
- Modify: `src/aiswarm/cli.py`
- Test: `tests/test_cli.py`, `tests/test_watch_isolation.py`

**Interfaces:**
- Consumes: `open_watcher` (Task 3), `WatchApp` (Task 5).
- Produces: `aiswarm watch [RUN_ID] [-c CONFIG]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py`:

```python
def test_watch_errors_cleanly(project):
    tmp_path, config = project
    result = invoke("watch", "-c", config)
    assert result.exit_code != 0 and "no runs yet" in result.output
    unknown = invoke("watch", "nope", "-c", config)
    assert unknown.exit_code != 0 and "no run 'nope'" in unknown.output


def test_watch_opens_the_latest_run(project, monkeypatch):
    tmp_path, config = project
    make_agents(config, "a")
    assert invoke("run", "Pick a colour", "--max-rounds", "1", "--no-postrun", "-c", config).exit_code == 0
    opened = []
    monkeypatch.setattr(cli.WatchApp, "run", lambda self: opened.append(self.watcher.state.run_id))
    result = invoke("watch", "-c", config)
    assert result.exit_code == 0, result.output
    assert len(opened) == 1
```

Create `tests/test_watch_isolation.py`:

```python
import ast
import hashlib
from pathlib import Path

from observer_helpers import add_entry, add_run

import aiswarm
from aiswarm.config import Config
from aiswarm.gitrepo import GitRepo
from aiswarm.watch.source import open_watcher

SRC = Path(aiswarm.__file__).parent
AGENT_PATH = ["view.py", "agent.py", "runner.py", "board.py", "consensus.py", "actions.py", "introduce.py",
              "checkin.py", "journal.py", "library.py", "norms.py", "reviews.py", "profiles.py", "prompts/__init__.py",
              *[f"postrun/{p.name}" for p in (SRC / "postrun").glob("*.py")]]


def test_no_agent_path_module_imports_the_watcher():
    for rel in AGENT_PATH:
        tree = ast.parse((SRC / rel).read_text())
        names = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        names |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        assert not any(name.startswith("aiswarm.watch") for name in names), rel


def test_watching_leaves_the_community_untouched(tmp_path, community):
    store, conn = community
    add_run(conn, "r1")
    add_entry(conn, "r1", "a", "propose", {"answer_summary": "x", "method": "m"})
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    cfg = Config.model_validate({"models": {"local": {"kind": "openai", "model": "m"}}})
    cfg.community_dir, cfg.runs_dir = tmp_path / "community", tmp_path / "runs"

    def fingerprint():
        db = cfg.community_dir / "community.db"
        return GitRepo(cfg.community_dir).head(), hashlib.sha256(db.read_bytes()).hexdigest()

    before = fingerprint()
    watcher = open_watcher(cfg, "r1")
    watcher.poll()
    watcher.poll()
    assert fingerprint() == before
    assert not cfg.runs_dir.exists()  # the watcher creates no files
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_cli.py -k watch tests/test_watch_isolation.py`
Expected: FAIL — `No such command 'watch'` / `module 'aiswarm.cli' has no attribute 'WatchApp'`.

- [ ] **Step 3: Implement**

In `src/aiswarm/cli.py`, add imports:

```python
from aiswarm.watch.app import WatchApp
from aiswarm.watch.source import open_watcher
```

Add the command after `observe`:

```python
@app.command()
def watch(
    run_id: Annotated[str | None, typer.Argument(help="Run to watch (default: the latest).")] = None,
    config: ConfigOpt = DEFAULT_PATH,
) -> None:
    """Live, read-only view of a run in this terminal (q to quit, f to toggle following)."""
    cfg = _load(config)
    try:
        watcher = open_watcher(cfg, run_id)
    except (ValueError, sqlite3.OperationalError) as exc:
        _fail(str(exc))
    WatchApp(watcher).run()
```

- [ ] **Step 4: Run tests to verify they pass, then the full suite**

Run: `uv run pytest -q tests/test_cli.py tests/test_watch_isolation.py`
Expected: PASS.
Run: `uv run pytest -q`
Expected: all pass.

To prove the import check can fail: temporarily add `import aiswarm.watch` to `src/aiswarm/view.py`, run `uv run pytest -q tests/test_watch_isolation.py -k imports`, see FAIL, revert.

- [ ] **Step 5: Commit**

```bash
git add src/aiswarm/cli.py tests/test_cli.py tests/test_watch_isolation.py
git commit -m "feat: aiswarm watch opens the live view of a run; the watcher never touches the community

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Docs

**Files:**
- Modify: `docs/superpowers/specs/2026-10-11-aiswarm-tui-design.md`

- [ ] **Step 1:** Change `- **Status:** Draft, awaiting review` to `- **Status:** Approved; Plan 3c implemented`, and in §4 replace "with done / failed / pending counts" with "with done and failed counts and a marker on the step currently running (expected call counts are not recorded, so pending cannot be shown)".

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/specs/2026-10-11-aiswarm-tui-design.md
git commit -m "docs: mark Plan 3c (TUI) implemented

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
