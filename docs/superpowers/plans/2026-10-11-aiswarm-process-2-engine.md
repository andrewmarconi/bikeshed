# Process 2 Engine (Plan 4a) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `aiswarm run --process 2` runs rounds as think → react in turn → poll, with private think notes and one-to-one private messages that only their parties ever see; Process 1 stays byte-for-byte the same.

**Architecture:** The Runner keeps run lifecycle, stop rules, the `rounds` record and pausing; a new `phases.py` holds the phases, and `phases(process, round)` returns the list a round runs. Private content is ordinary board entries with a `visible_to` list; every agent-facing read goes through `Board.entries_for(agent)` / `visible(entries, agent)`. A recording of a Process 1 run, made before any refactor, guards the baseline.

**Tech Stack:** Python 3.14, uv, pydantic 2, sqlite (numbered migrations), pytest (asyncio auto mode), `FakeModel`.

**Spec:** `docs/superpowers/specs/2026-10-11-aiswarm-process-2-design.md` (§1–6, §9, §10 items 1–5). Observer (§7) is Plan 4b; the TUI (§8) is Plan 4c.

## Global Constraints

- Process 1 behaviour, prompts, views and schemas are unchanged: the recording test (Task 1) must pass after every task.
- Values-neutral prompts: no "compromise", "concede", "strategy", "urgency", no praise or blame.
- Private entries: `private_message` visible to `[sender, recipient]`; `think` visible to `[author]`; never in another agent's view, `read_entry`, post-run inputs, the method library, or `answer.md`.
- Think note at most 1,200 characters; private message at most 600 characters; exactly one recipient, another member of the run.
- Poll: most recent `call_consensus`; asked = members who have neither supported nor objected (caller counts as supporter); support / object become `vote`, abstain becomes public `abstain`; nothing is fabricated on failure.
- Round 1 of Process 2 reacts blind (Process 1's rule). Order of turns and of poll answers: seeded per run and round, so a resumed run replays it.
- Process 2 view header: `# Round N of M`. Process 1 header unchanged.
- Run commands with `uv run`; run the full suite with `uv run pytest -q`.
- Before any commit to `develop` while the lab may be running, check `pgrep -fl "aiswarm (run|postrun)"` as a separate step (work in a worktree avoids this).

## Review Focus

1. **A community database the runner has not migrated yet, read by `aiswarm watch` or `observe` (read-only, no migration):** entries must still load, as public. → Task 2, `test_entries_load_from_a_database_without_the_visibility_column`.
2. **A private message to oneself or to someone outside the run:** rejected as an invalid action with the list of valid recipients, then `pass` / `invalid_action`. → Task 4, `test_private_message_recipient_must_be_another_member`.
3. **A Process 2 run interrupted mid-react (crash, or every agent's model down part-way through the turns):** resume discards the partial round and replays it once; no duplicate entries. → Task 7, `test_resume_discards_a_partial_round_and_replays_it`.
4. **A poll where every asked agent's model is down:** the run pauses; no votes are invented. → Task 8, `test_a_poll_outage_pauses_without_inventing_votes`.
5. **A reviewer citing a private message it was not party to as review evidence:** rejected like an unknown entry. → Task 3, `test_review_evidence_must_be_visible_to_the_reviewer`.

---

## File Structure

| File | Responsibility |
|---|---|
| `tests/test_process1_golden.py`, `tests/golden/process1_run.json` (new) | Recording of a Process 1 run; the baseline guard |
| `src/aiswarm/db.py` | Migration 5: `runs.process`, `entries.visible_to` |
| `src/aiswarm/board.py` | `Entry.visible_to`, `Board.append(visible_to=)`, `Board.entries_for`, `visible()`, `public()` |
| `src/aiswarm/view.py` | Views from `entries_for`; Process 2 header and own think notes |
| `src/aiswarm/postrun/cycle.py` | Per-agent post-run inputs filtered; journal gets think notes |
| `src/aiswarm/actions.py` | `Note`, `PrivateMessage`, `PollAnswer`, `envelope()`, `STEP`, `STEP_P2`, `THINK`, `POLL` |
| `src/aiswarm/agent.py` | `take_step(shape=, opening_rule=)`, `system_prompt(template=)`, private-message validation |
| `src/aiswarm/phases.py` (new) | `RoundContext`, `simultaneous_act`, `think`, `sequential_act`, `poll`, `phases()` |
| `src/aiswarm/runner.py` | Round loop over phases; `_turn`, `_think`, `_poll`, posting helpers; discard of partial rounds; `start(process=)` |
| `src/aiswarm/consensus.py` | Stagnation ignores think notes and treats abstain as idle |
| `src/aiswarm/config.py` | `RunSettings.process` |
| `src/aiswarm/prompts/process2_*.md` (new) | Process 2 system prompt, think request, poll request |
| `src/aiswarm/cli.py` | `run --process`; resume keeps the run's process |
| `src/aiswarm/watch/state.py` | Feed summaries for `think`, `private_message`, `abstain` (interim until Plan 4c) |
| `tests/conftest.py` | `process2` FakeModel factory fixture |
| `tests/test_process2.py` (new) | Process 2 phases end to end |

---

### Task 1: Record Process 1 before anything changes

**Files:**
- Create: `tests/test_process1_golden.py`
- Create: `tests/golden/process1_run.json` (generated)

**Interfaces:**
- Consumes: existing fixtures `community`, `scripted`, `find`, `runner_factory` (`tests/conftest.py`).
- Produces: `tests/golden/process1_run.json`; every later task keeps `test_process1_matches_the_recording` green.

- [ ] **Step 1: Write the recording test**

```python
"""Process 1, end to end, against a recording made before the runner was split into phases.

It covers every model request (system prompt, messages, schema), board entry, event, file, failure and the report.
Regenerate only when Process 1 is meant to change:
    AISWARM_WRITE_GOLDEN=1 uv run pytest tests/test_process1_golden.py
"""

import json
import os
from pathlib import Path

from aiswarm.events import read_events

GOLDEN = Path(__file__).parent / "golden" / "process1_run.json"
AGENTS = ["a", "b", "c"]


def _plans(find):
    def propose(answer):
        return lambda v: {"type": "propose", "answer_summary": answer, "method": f"Thought about {answer}."}

    def write(v):
        return {"type": "write_file", "path": "draft.md", "base_version": 0, "content": "Draft.", "note": "start"}

    return {
        "a": {1: propose("Blue"), 2: write,
              3: lambda v: {"type": "call_consensus", "synthesis": "Blue, drafted in draft.md.",
                            "refs": find(v, "propose", "a"),
                            "credits": [{"agent_id": "b", "contribution": "the draft"}]}},
        "b": {1: propose("Green"), 2: write,
              4: lambda v: {"type": "critique", "target": find(v, "call_consensus")[0], "about": "answer",
                            "points": "Green deserved more thought."}},
        "c": {1: propose("Red"),
              2: lambda v: {"type": "critique", "target": find(v, "propose", "a")[0], "about": "method",
                            "points": "Too quick."},
              3: lambda v: {"type": "bogus"},
              4: lambda v: {"type": "vote", "target": find(v, "call_consensus")[0], "stance": "support",
                            "reason": "Fine."}},
    }


def _record(conn, run_dir, run_id, clients, result):
    def rows(sql):
        return [dict(r) for r in conn.execute(sql, (run_id,))]

    data = {
        "result": [result.status, result.reason, result.call_id],
        "entries": rows("SELECT id, round, author, type, to_, refs, payload FROM entries WHERE run_id = ? ORDER BY id"),
        "files": rows("SELECT path, version, author FROM files WHERE run_id = ? ORDER BY path, version"),
        "failures": rows("SELECT round, agent_id, kind, detail FROM failures WHERE run_id = ? ORDER BY id"),
        "rounds": [r["round"] for r in rows("SELECT round FROM rounds WHERE run_id = ? ORDER BY round")],
        "events": [{k: v for k, v in e.items() if k != "ts"} for e in read_events(run_dir / "events.jsonl")],
        "report": {p.name: p.read_text() for p in sorted((run_dir / "report").iterdir())},
        "workspace": {p.name: p.read_text() for p in sorted((run_dir / "workspace").iterdir()) if p.is_file()},
        "calls": {agent: [{"system": s, "messages": m, "schema": schema} for s, m, schema in client.calls]
                  for agent, client in sorted(clients.items())},
    }
    return json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False).replace(run_id, "<run>") + "\n"


async def test_process1_matches_the_recording(tmp_path, community, scripted, find, runner_factory):
    store, conn = community
    clients = {agent: scripted(plan) for agent, plan in _plans(find).items()}
    runner = runner_factory(store, conn, clients)
    run_id = runner.start("Pick a colour", AGENTS)
    result = await runner.run(run_id)
    assert (result.status, result.reason) == ("closed", "consensus")
    record = _record(conn, tmp_path / "runs" / run_id, run_id, clients, result)
    if os.environ.get("AISWARM_WRITE_GOLDEN"):
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(record)
    assert record == GOLDEN.read_text()
```

- [ ] **Step 2: Run it without a recording to see it fail**

Run: `uv run pytest tests/test_process1_golden.py -q`
Expected: FAIL with `FileNotFoundError` for `tests/golden/process1_run.json` (the run itself must reach consensus; if the first assert fails instead, the scenario is wrong — fix the scenario, not the runner).

- [ ] **Step 3: Write the recording, then check it**

Run: `AISWARM_WRITE_GOLDEN=1 uv run pytest tests/test_process1_golden.py -q` then `uv run pytest tests/test_process1_golden.py -q` twice more.
Expected: PASS all three times (deterministic). Open the JSON and confirm it contains: a `write_conflict` failure, an `invalid_action` failure for `c` in round 3, a `run_closed` entry with reason `consensus`, `"rounds": [1, 2, 3, 4]`, and calls with `"# Round 1"` views.

- [ ] **Step 4: Commit**

```bash
git add tests/test_process1_golden.py tests/golden/process1_run.json
git commit -m "test: record a Process 1 run as the baseline the phase refactor must reproduce"
```

---

### Task 2: Private entries in storage

**Files:**
- Modify: `src/aiswarm/db.py` (append migration 5 to `MIGRATIONS`)
- Modify: `src/aiswarm/board.py`
- Test: `tests/test_board.py`

**Interfaces:**
- Produces: `Entry.visible_to: tuple[str, ...] | None` (last field, default `None`); `Board.append(..., visible_to: Sequence[str] | None = None) -> Entry`; `Board.entries_for(agent_id: str) -> list[Entry]`; `visible(entries: Sequence[Entry], agent_id: str) -> list[Entry]`; `public(entries: Sequence[Entry]) -> list[Entry]`; column `runs.process INTEGER NOT NULL DEFAULT 1`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_board.py`)

```python
import sqlite3

from aiswarm.board import public, visible


def test_private_entries_are_visible_only_to_their_parties(tmp_path, make_run):
    conn = connect(tmp_path / "c.db")
    make_run(conn, "r1", ("a", "b", "c"))
    board = Board(conn, "r1")
    goal = board.append(0, SYSTEM, "goal", {"goal": "G"})
    message = board.append(2, "a", "private_message", {"to": "b", "text": "psst"}, to="b", visible_to=["a", "b"])
    note = board.append(2, "a", "think", {"text": "hmm"}, visible_to=["a"])
    assert board.get(message.id).visible_to == ("a", "b") and board.get(message.id).to == "b"
    assert board.get(goal.id).visible_to is None
    assert [e.id for e in board.entries_for("a")] == [goal.id, message.id, note.id]
    assert [e.id for e in board.entries_for("b")] == [goal.id, message.id]
    assert [e.id for e in board.entries_for("c")] == [goal.id]
    assert [e.id for e in visible(board.entries(), "c")] == [goal.id]
    assert [e.id for e in public(board.entries())] == [goal.id]


def test_runs_record_their_process(tmp_path, make_run):
    conn = connect(tmp_path / "c.db")
    make_run(conn, "r1")
    assert conn.execute("SELECT process FROM runs WHERE id = 'r1'").fetchone()[0] == 1


def test_entries_load_from_a_database_without_the_visibility_column(tmp_path):
    """watch and observe read without migrating; an older database must still load, as all public."""
    conn = sqlite3.connect(tmp_path / "old.db")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE entries (id INTEGER PRIMARY KEY, run_id TEXT, round INTEGER, author TEXT, type TEXT, "
                 "to_ TEXT, refs TEXT, payload TEXT, created_at TEXT)")
    conn.execute("INSERT INTO entries VALUES (1, 'r1', 0, 'system', 'goal', 'all', '[]', '{\"goal\": \"G\"}', 't')")
    [entry] = Board(conn, "r1").entries()
    assert entry.visible_to is None and entry.payload == {"goal": "G"}
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_board.py -q`
Expected: FAIL — `ImportError: cannot import name 'public'`.

- [ ] **Step 3: Implement**

In `src/aiswarm/db.py`, append to `MIGRATIONS` (after the `run_provenance` migration):

```python
    """
    ALTER TABLE runs ADD COLUMN process INTEGER NOT NULL DEFAULT 1;
    ALTER TABLE entries ADD COLUMN visible_to TEXT;
    """,
```

In `src/aiswarm/board.py`:

```python
@dataclass(frozen=True)
class Entry:
    id: int
    run_id: str
    round: int
    author: str
    type: str
    payload: dict[str, Any]
    refs: list[int]
    to: str
    created_at: str
    visible_to: tuple[str, ...] | None = None  # None: every member; otherwise only these agents may see it


def _entry(row: sqlite3.Row) -> Entry:
    hidden = row["visible_to"] if "visible_to" in row.keys() else None  # read-only readers may face an older schema
    return Entry(
        id=row["id"], run_id=row["run_id"], round=row["round"], author=row["author"], type=row["type"],
        payload=json.loads(row["payload"]), refs=json.loads(row["refs"]), to=row["to_"],
        created_at=row["created_at"], visible_to=tuple(json.loads(hidden)) if hidden else None,
    )


def visible(entries: Sequence[Entry], agent_id: str) -> list[Entry]:
    """What one agent may see: public entries, and private ones that name it."""
    return [e for e in entries if e.visible_to is None or agent_id in e.visible_to]


def public(entries: Sequence[Entry]) -> list[Entry]:
    return [e for e in entries if e.visible_to is None]
```

Replace `Board.append` and add `entries_for`:

```python
    def append(
        self, round: int, author: str, type: str, payload: dict[str, Any],
        refs: Sequence[int] = (), to: str = "all", visible_to: Sequence[str] | None = None,
    ) -> Entry:
        created = now_iso()
        hidden = tuple(visible_to) if visible_to is not None else None
        cursor = self._conn.execute(
            "INSERT INTO entries (run_id, round, author, type, to_, refs, payload, created_at, visible_to) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (self.run_id, round, author, type, to, json.dumps(list(refs)),
             json.dumps(payload, ensure_ascii=False), created, json.dumps(list(hidden)) if hidden else None),
        )
        return Entry(cursor.lastrowid, self.run_id, round, author, type, payload, list(refs), to, created, hidden)

    def entries_for(self, agent_id: str) -> list[Entry]:
        """Every agent-facing read goes through here (or `visible`); only the harness and observers read all."""
        return visible(self.entries(), agent_id)
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_board.py tests/test_storage.py tests/test_process1_golden.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aiswarm/db.py src/aiswarm/board.py tests/test_board.py
git commit -m "feat: board entries can be private to named agents; runs record their process"
```

---

### Task 3: Every agent-facing read respects privacy

**Files:**
- Modify: `src/aiswarm/view.py` (`collect_inputs`)
- Modify: `src/aiswarm/postrun/cycle.py` (`_packet`, `_review`, `_calls` review pairs, `_library`)
- Modify: `src/aiswarm/runner.py` (`_close`: report from public entries)
- Test: `tests/test_view.py`, `tests/test_postrun.py`

**Interfaces:**
- Consumes: `Board.entries_for`, `visible`, `public` (Task 2).
- Produces: `PostRun._seen(ctx, agent_id) -> list[Entry]` (visible to the agent, think notes excluded) used by Task 9.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_view.py`:

```python
import pytest

from aiswarm.actions import ActionError, Critique, ReadEntry
from aiswarm.agent import make_reader, make_validator


def test_views_reads_and_targets_hide_other_agents_private_entries(tmp_path, community, make_run):
    store, conn, board, ws = setup(tmp_path, community, make_run)
    message = board.append(2, "a", "private_message", {"to": "b", "text": "SECRET-A-TO-B"}, to="b", visible_to=["a", "b"])
    board.append(2, "a", "think", {"text": "SECRET-THOUGHT-A"}, visible_to=["a"])
    assert "SECRET-A-TO-B" in view_for("a", 3, store, board, ws)
    assert "SECRET-A-TO-B" in view_for("b", 3, store, board, ws)
    view_c = view_for("c", 3, store, board, ws)
    assert "SECRET-A-TO-B" not in view_c and "SECRET-THOUGHT-A" not in view_c
    assert "SECRET-THOUGHT-A" not in view_for("b", 3, store, board, ws)
    inputs = collect_inputs(agent_id="c", goal="Pick a colour", round=3, board=board, store=store,
                            workspace=ws, view_entries=15)
    read = make_reader(ws, inputs.entries, make_who(inputs.profiles))
    assert "SECRET" not in read(ReadEntry(type="read_entry", id=message.id))
    with pytest.raises(ActionError, match="does not exist"):
        make_validator(inputs.entries, ["a", "b", "c"])(
            Critique(type="critique", target=message.id, about="answer", points="p"))
```

Append to `tests/test_postrun.py`:

```python
def private_run(conn, tmp_path, make_run):
    run_id = finished_run(conn, tmp_path, make_run)
    board = Board(conn, run_id)
    board.append(2, "a", "private_message", {"to": "b", "text": "SECRET-A-TO-B"}, to="b", visible_to=["a", "b"])  # 7
    board.append(2, "a", "think", {"text": "SECRET-THOUGHT-A"}, visible_to=["a"])                              # 8
    return run_id


async def test_post_run_inputs_never_carry_private_entries_to_others(tmp_path, community, make_run, postrun_reply):
    store, conn = community
    run_id = private_run(conn, tmp_path, make_run)
    clients = {a: FakeModel(responder(postrun_reply)) for a in "abc"}
    await cycle(store, conn, tmp_path, clients).run(run_id)

    def seen(agent):
        return "".join(system + "".join(m["content"] for m in messages) for system, messages, _ in clients[agent].calls)

    assert "SECRET-A-TO-B" not in seen("c") and "SECRET-THOUGHT-A" not in seen("c")
    assert "SECRET-THOUGHT-A" not in seen("b")
    assert "SECRET-A-TO-B" in seen("b")
    texts = [r["text"] for r in conn.execute("SELECT text FROM methods")]
    assert all("SECRET" not in t for t in texts)


async def test_review_evidence_must_be_visible_to_the_reviewer(tmp_path, community, make_run, postrun_reply):
    store, conn = community
    run_id = private_run(conn, tmp_path, make_run)
    cites_secret = lambda request: json.dumps({"more_of": "M", "less_of": "L", "private_note": "N", "evidence": [7],
                                               "overall": 3, "overall_reason": "R"})
    clients = {"a": FakeModel(responder(postrun_reply)), "b": FakeModel(responder(postrun_reply)),
               "c": FakeModel(responder(postrun_reply, review=cites_secret))}
    await cycle(store, conn, tmp_path, clients).run(run_id)
    assert steps(conn)[("review", "c", "c")] == "failed"
    assert steps(conn)[("review", "a", "a")] == "done"
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_view.py tests/test_postrun.py -q -k "private or visible"`
Expected: FAIL — `SECRET-A-TO-B` found in c's view.

- [ ] **Step 3: Implement**

`src/aiswarm/view.py`, in `collect_inputs` replace `entries = board.entries()` with:

```python
    entries = board.entries_for(agent_id)
```

`src/aiswarm/postrun/cycle.py`: change the import to `from aiswarm.board import Board, Entry, public, visible`, and add to the `PostRun` class (in the `# --- inputs` section):

```python
    def _seen(self, ctx: _Context, agent_id: str) -> list[Entry]:
        """What this agent may be shown about the run: no one else's private entries, and no think notes."""
        return [e for e in visible(ctx.entries, agent_id) if e.type != "think"]
```

Then:
- in `_calls`, `pairs = interactions(public(ctx.entries), files, ctx.agent_ids)`;
- in `_packet`, pass `entries=self._seen(ctx, agent_id)` to `feedback_packet`;
- in `_review`, `transcript=transcript(self._seen(ctx, reviewer), ctx.who, {reviewer, reviewee}, ...)` and `valid = {entry.id for entry in visible(ctx.entries, reviewer)}`;
- in `_library`, `record_methods(self.conn, ctx.run_id, ctx.goal, public(ctx.entries))`.

`src/aiswarm/runner.py`, import `public` from `aiswarm.board`; in `_close`:

```python
        write_report(self.runs_dir / run_id, goal, public(board.entries()), decision, who)
```

(`answer.md` reaches every agent through its post-run packet, so it is built from public entries only. The narrative and the Observer still read all entries.)

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_view.py tests/test_postrun.py tests/test_runner.py tests/test_process1_golden.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aiswarm/view.py src/aiswarm/postrun/cycle.py src/aiswarm/runner.py tests/test_view.py tests/test_postrun.py
git commit -m "feat: views, reads, post-run inputs, the library and answer.md never show another agent's private entries"
```

---

### Task 4: Actions and reply shapes for Process 2

**Files:**
- Modify: `src/aiswarm/actions.py`
- Modify: `src/aiswarm/agent.py`
- Test: `tests/test_actions.py`, `tests/test_agent.py`

**Interfaces:**
- Produces in `actions.py`: `NOTE_MAX = 1_200`, `MESSAGE_MAX = 600`; classes `Note(type="note", text)`, `PrivateMessage(type="private_message", to, text)`, `PollAnswer(type="poll_answer", stance: "support"|"object"|"abstain", reason)`; `envelope(*kinds) -> type[BaseModel]`; shapes `STEP` (Process 1, the existing `_Envelope`), `STEP_P2`, `THINK`, `POLL`; `parse_action(text, shape=STEP)`; `step_schema(shape=STEP)`.
- Produces in `agent.py`: `take_step(..., shape=STEP, opening_rule=True)`; `system_prompt(display, temperament, max_reads, template="agent_system.md")`; `make_validator(entries, agent_ids, agent_id=None)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_actions.py`:

```python
from aiswarm.actions import (
    MESSAGE_MAX, NOTE_MAX, POLL, STEP, STEP_P2, THINK, Note, PollAnswer, PrivateMessage, parse_action, step_schema,
)


def test_process2_shapes_parse_their_actions():
    assert isinstance(parse_action('{"action": {"type": "note", "text": "hm"}}', THINK), Note)
    assert parse_action('{"type": "list_files"}', THINK).type == "list_files"  # reads are allowed while thinking
    message = parse_action('{"action": {"type": "private_message", "to": "b", "text": "hi"}}', STEP_P2)
    assert isinstance(message, PrivateMessage) and message.to == "b"
    answer = parse_action('{"action": {"type": "poll_answer", "stance": "abstain", "reason": "r"}}', POLL)
    assert isinstance(answer, PollAnswer) and answer.stance == "abstain"


def test_process1_shape_is_unchanged_and_rejects_process2_actions():
    with pytest.raises(ActionError):
        parse_action('{"action": {"type": "private_message", "to": "b", "text": "hi"}}')
    with pytest.raises(ActionError):
        parse_action('{"action": {"type": "propose", "answer_summary": "a", "method": "m"}}', THINK)
    assert step_schema() == step_schema(STEP)
    assert "private_message" in json.dumps(step_schema(STEP_P2)) and "private_message" not in json.dumps(step_schema())


def test_note_and_message_limits():
    with pytest.raises(ActionError, match=f"yours was {NOTE_MAX + 1:,} characters"):
        parse_action(json.dumps({"action": {"type": "note", "text": "x" * (NOTE_MAX + 1)}}), THINK)
    with pytest.raises(ActionError):
        parse_action(json.dumps({"action": {"type": "private_message", "to": "b", "text": "x" * (MESSAGE_MAX + 1)}}), STEP_P2)
    with pytest.raises(ActionError):
        parse_action('{"action": {"type": "poll_answer", "stance": "maybe", "reason": "r"}}', POLL)
```

(If `tests/test_actions.py` lacks `import json`, `import pytest` or `ActionError`, add them to its imports.)

Append to `tests/test_agent.py`:

```python
from aiswarm.actions import THINK, PrivateMessage


async def test_take_step_with_a_think_shape_and_no_opening_rule():
    note = json.dumps({"action": {"type": "note", "text": "where I stand"}})
    outcome = await take_step(client=FakeModel([note]), system="SYS", view="VIEW", round=1, max_reads=0,
                              reader=no_reads, validate=allow, shape=THINK, opening_rule=False)
    assert outcome.failure is None and outcome.action.text == "where I stand"


def test_private_message_recipient_must_be_another_member():
    validate = make_validator(entries(), ["a", "b", "c"], "a")
    validate(PrivateMessage(type="private_message", to="b", text="hi"))
    for to in ("a", "zed"):
        with pytest.raises(ActionError, match=r"another member: \['b', 'c'\]"):
            validate(PrivateMessage(type="private_message", to=to, text="hi"))


def test_system_prompt_template_can_be_chosen():
    assert system_prompt("Wren (a)", "Calm.", 3) == system_prompt("Wren (a)", "Calm.", 3, "agent_system.md")
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_actions.py tests/test_agent.py -q`
Expected: FAIL — `ImportError: cannot import name 'MESSAGE_MAX'`.

- [ ] **Step 3: Implement `actions.py`**

Change the typing import to `from typing import Annotated, Any, Literal, Union` and the pydantic import to `from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model`. Add after `ReadReviews`:

```python
NOTE_MAX = 1_200
MESSAGE_MAX = 600


class Note(_Action):
    """Process 2: a private note an agent writes to itself before a round's discussion."""
    type: Literal["note"]
    text: str = Field(min_length=1, max_length=NOTE_MAX)


class PrivateMessage(_Action):
    """Process 2: a message only its sender and its one recipient will see."""
    type: Literal["private_message"]
    to: Text
    text: str = Field(min_length=1, max_length=MESSAGE_MAX)


class PollAnswer(_Action):
    """Process 2: a stance on the open call when polled."""
    type: Literal["poll_answer"]
    stance: Literal["support", "object", "abstain"]
    reason: Text
```

After the `_Envelope` class add:

```python
def envelope(*kinds: type[_Action]) -> type[BaseModel]:
    """The reply shape {"action": <one of kinds>}."""
    union: Any = kinds[0] if len(kinds) == 1 else Annotated[Union[kinds], Field(discriminator="type")]
    return create_model("Envelope", __base__=_Action, action=(union, ...))


STEP = _Envelope  # Process 1; unchanged
STEP_P2 = envelope(Propose, Critique, Question, Adopt, Refine, WriteFile, CallConsensus, Vote, PrivateMessage, Pass,
                   *READ_ACTIONS)
THINK = envelope(Note, *READ_ACTIONS)
POLL = envelope(PollAnswer)
```

Change `parse_action` and `step_schema`:

```python
def parse_action(text: str, shape: type[BaseModel] = _Envelope) -> Any:
    data = extract_json(text)
    if isinstance(data, dict) and "action" not in data and "type" in data:
        data = {"action": data}
    try:
        return shape.model_validate(data).action
    except ValidationError as exc:
        raise ActionError(summarise_validation(exc)) from exc
```

```python
def step_schema(shape: type[BaseModel] = _Envelope) -> dict[str, Any]:
    return simplify_schema(shape.model_json_schema())
```

- [ ] **Step 4: Implement `agent.py`**

Import `STEP`, `PrivateMessage` and `BaseModel` (`from pydantic import BaseModel`). Change:

```python
def system_prompt(display: str, temperament: str, max_reads: int, template: str = "agent_system.md") -> str:
    return render(template, display=display, temperament=temperament.strip(), max_reads=max_reads)


async def take_step(
    *, client: ModelClient, system: str, view: str, round: int, max_reads: int,
    reader: Reader, validate: Validator, shape: type[BaseModel] = STEP, opening_rule: bool = True,
) -> StepOutcome:
    messages: list[Message] = [{"role": "user", "content": view}]
    schema = step_schema(shape)
```

and inside the loop `action = parse_action(result.text, shape)` and `if opening_rule and round == 1 and not isinstance(action, Propose):`.

In `make_validator(entries, agent_ids, agent_id: str | None = None)`, add inside `validate`:

```python
        if isinstance(action, PrivateMessage):
            others = [a for a in agent_ids if a != agent_id]
            if action.to not in others:
                raise ActionError(f"private_message.to must be another member: {others}")
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_actions.py tests/test_agent.py tests/test_process1_golden.py -q`
Expected: PASS (the recording proves Process 1's schema and prompts are unchanged).

- [ ] **Step 6: Commit**

```bash
git add src/aiswarm/actions.py src/aiswarm/agent.py tests/test_actions.py tests/test_agent.py
git commit -m "feat: note, private_message and poll_answer actions with their own reply shapes"
```

---

### Task 5: The runner runs phases; interrupted rounds are discarded on resume

**Files:**
- Create: `src/aiswarm/phases.py`
- Modify: `src/aiswarm/runner.py`
- Modify: `src/aiswarm/config.py`
- Test: `tests/test_runner.py`

**Interfaces:**
- Consumes: `STEP`, `STEP_P2` (Task 4); `make_validator(..., agent_id)` (Task 4).
- Produces: `RoundContext(run_id, round, goal, agent_ids, seed, process, board, ws, events, library)` with `rng(runner, salt="")`; `Phase = Callable[[Runner, RoundContext], Awaitable[str | None]]` (returns a pause detail or `None`); `simultaneous_act`; `phases(process: int, round: int) -> list[Phase]`; `Runner.start(..., process: int = 1)`; `Runner._step(agent_id, ctx) -> StepOutcome`; `Runner._turn(agent_id, ctx, *, phase=None, request="", shape=STEP, opening_rule=True, max_reads=None) -> StepOutcome`; `Runner._discard_after(run_id, completed, events)`; `RunSettings.process: Literal[1, 2] = 1`; event `round_discarded {round, entries}`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_runner.py`)

```python
async def test_start_records_the_process(community, runner_factory):
    store, conn = community
    runner = runner_factory(store, conn, {})
    assert conn.execute("SELECT process FROM runs WHERE id = ?", (runner.start("G", AGENTS),)).fetchone()[0] == 1
    assert conn.execute("SELECT process FROM runs WHERE id = ?",
                        (runner.start("G", AGENTS, process=2),)).fetchone()[0] == 2
    with pytest.raises(ValueError, match="process must be 1 or 2"):
        runner.start("G", AGENTS, process=3)


async def test_resume_discards_what_an_interrupted_round_left_behind(tmp_path, community, scripted, runner_factory):
    store, conn = community
    run_id = runner_factory(store, conn, {}).start("G", AGENTS)
    board = Board(conn, run_id)
    stray = board.append(1, "a", "propose", {"answer_summary": "Half", "method": "M"})
    conn.execute("INSERT INTO traces (entry_id, run_id, agent_id, model, reasoning) VALUES (?, ?, 'a', 'm', 'r')",
                 (stray.id, run_id))
    conn.execute("INSERT INTO failures (run_id, round, agent_id, kind, detail, created_at) "
                 "VALUES (?, 1, 'b', 'invalid_action', 'x', 't')", (run_id,))
    result = await runner_factory(store, conn, {a: scripted({1: propose}) for a in AGENTS}).run(run_id)
    assert result.status == "closed"
    assert entry_types(conn, run_id).count("propose") == 3
    assert conn.execute("SELECT COUNT(*) FROM failures WHERE run_id = ?", (run_id,)).fetchone()[0] == 0
    events = read_events(tmp_path / "runs" / run_id / "events.jsonl")
    assert {"event": "round_discarded", "round": 1, "entries": 1}.items() <= next(
        e for e in events if e["event"] == "round_discarded").items()
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_runner.py -q -k "records_the_process or interrupted_round"`
Expected: FAIL — `TypeError: Runner.start() got an unexpected keyword argument 'process'`.

- [ ] **Step 3: Add the setting**

In `src/aiswarm/config.py`, import `Literal` from `typing` if not already imported, and add to `RunSettings`:

```python
    process: Literal[1, 2] = 1  # 1: all act at once; 2: think, then react in turn (with a poll)
```

- [ ] **Step 4: Create `src/aiswarm/phases.py`**

```python
"""The phases of a round. A Process is the ordered list of phases each round runs; the Runner does the rest."""

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from aiswarm.board import Board
from aiswarm.db import transaction
from aiswarm.events import EventLog
from aiswarm.workspace import Workspace

if TYPE_CHECKING:
    from aiswarm.runner import Runner


@dataclass(frozen=True)
class RoundContext:
    run_id: str
    round: int
    goal: str
    agent_ids: list[str]
    seed: int | None
    process: int
    board: Board
    ws: Workspace
    events: EventLog
    library: list[str]

    def rng(self, runner: "Runner", salt: str = "") -> Any:
        """Seeded per run and round (tests inject one), so a resumed run replays the same order."""
        return runner.rng or random.Random(f"{self.seed}-{self.round}{salt}")


Phase = Callable[["Runner", RoundContext], Awaitable[str | None]]  # returns a pause detail, or None to carry on


async def simultaneous_act(runner: "Runner", ctx: RoundContext) -> str | None:
    """Every agent acts on the same view; the actions are posted together in a shuffled order."""
    outcomes = await asyncio.gather(*(runner._step(a, ctx) for a in ctx.agent_ids))
    if all(o.failure == "model_error" for o in outcomes):
        return outcomes[0].detail
    order = list(zip(ctx.agent_ids, outcomes))
    ctx.rng(runner).shuffle(order)
    with transaction(runner.conn):
        posted = [runner._post(ctx.board, ctx.ws, ctx.run_id, ctx.round, a, o) for a, o in order]
    for event in posted:
        ctx.events.emit("action_posted", **event)
    return None


def phases(process: int, round: int) -> list[Phase]:
    if process == 1:
        return [simultaneous_act]
    raise ValueError(f"unknown process {process}")
```

- [ ] **Step 5: Rework `src/aiswarm/runner.py`**

Imports: add `from aiswarm.actions import STEP, STEP_P2` (keep `Pass, WriteFile, target_refs`), `from aiswarm.phases import RoundContext, phases`.

`start` gains `process: int = 1` (keyword, after `is_benchmark`). At its top add:

```python
        if process not in (1, 2):
            raise ValueError("process must be 1 or 2")
```

and the INSERT becomes:

```python
            self.conn.execute(
                "INSERT INTO runs (id, goal, status, agent_ids, created_at, seed, goal_id, goal_category, is_benchmark, "
                "process) VALUES (?, ?, 'running', ?, ?, ?, ?, ?, ?, ?)",
                (run_id, goal, json.dumps(list(agent_ids)), now_iso(), seed, goal_id, goal_category, int(is_benchmark),
                 process),
            )
```

Replace the body of `run` from `ws.reset_to(...)` to the end with:

```python
        ws.reset_to(last["workspace_commit"] if last else ws.initial_commit())
        completed = last["round"] if last else 0
        self._discard_after(run_id, completed, events)
        self.conn.execute("UPDATE runs SET status = 'running' WHERE id = ?", (run_id,))
        library = await self._library(run_id, goal, events)

        for rnd in range(completed + 1, self.settings.max_rounds + 1):
            events.emit("round_started", round=rnd)
            ctx = RoundContext(run_id, rnd, goal, agent_ids, seed, row["process"], board, ws, events, library)
            for phase in phases(row["process"], rnd):
                detail = await phase(self, ctx)
                if detail is not None:
                    self.conn.execute("UPDATE runs SET status = 'paused' WHERE id = ?", (run_id,))
                    events.emit("run_paused", round=rnd, detail=detail)
                    return RunResult(run_id, "paused")
            with transaction(self.conn):
                self.conn.execute(
                    "INSERT INTO rounds (run_id, round, workspace_commit, completed_at) VALUES (?, ?, ?, ?)",
                    (run_id, rnd, ws.head(), now_iso()),
                )
            events.emit("round_completed", round=rnd)
            completed = rnd
            decision = stop_decision(board.entries(), agent_ids, rnd, self.settings)
            if decision:
                return self._close(run_id, goal, board, events, rnd, decision)
        decision = stop_decision(board.entries(), agent_ids, completed, self.settings) or StopDecision("budget")
        return self._close(run_id, goal, board, events, completed, decision)

    def _discard_after(self, run_id: str, completed: int, events: EventLog) -> None:
        """Remove what an interrupted round left behind, so that round replays from its start."""
        rows = self.conn.execute(
            "SELECT id, type, payload FROM entries WHERE run_id = ? AND round > ?", (run_id, completed)
        ).fetchall()
        if not rows:
            self.conn.execute("DELETE FROM failures WHERE run_id = ? AND round > ?", (run_id, completed))
            return
        ids = [r["id"] for r in rows]
        marks = ", ".join("?" * len(ids))
        written = [(json.loads(r["payload"])["path"], json.loads(r["payload"])["version"])
                   for r in rows if r["type"] == "write_file"]
        with transaction(self.conn):
            self.conn.execute(f"DELETE FROM traces WHERE entry_id IN ({marks})", ids)
            self.conn.execute(f"DELETE FROM entries WHERE id IN ({marks})", ids)
            self.conn.executemany("DELETE FROM files WHERE run_id = ? AND path = ? AND version = ?",
                                  [(run_id, path, version) for path, version in written])
            self.conn.execute("DELETE FROM failures WHERE run_id = ? AND round > ?", (run_id, completed))
        events.emit("round_discarded", round=completed + 1, entries=len(ids))
```

Replace `_step` with `_turn` and a thin `_step`:

```python
    async def _turn(
        self, agent_id: str, ctx: RoundContext, *, phase: str | None = None, request: str = "",
        shape: Any = STEP, opening_rule: bool = True, max_reads: int | None = None,
    ) -> StepOutcome:
        """One model turn for one agent: its view (plus any request) in, one validated reply out. Never raises."""
        extra = {"phase": phase} if phase else {}
        ctx.events.emit("agent_step_started", round=ctx.round, agent=agent_id, **extra)
        try:
            inputs = collect_inputs(agent_id=agent_id, goal=ctx.goal, round=ctx.round, board=ctx.board,
                                    store=self.store, workspace=ctx.ws, view_entries=self.settings.view_entries,
                                    half_life=self.settings.reputation_half_life_runs, library=ctx.library)
            who = make_who(inputs.profiles)
            template = "agent_system.md" if ctx.process == 1 else "process2_agent_system.md"
            system = system_prompt(
                who(agent_id), self.store.private(agent_id)["temperament.md"], self.settings.max_reads, template
            )
            client = self.clients[agent_id]
            budget = max(2_000, int(client.context_window * 3 * 0.75) - len(system) - len(request))
            view = build_view(inputs, budget_chars=budget)
            outcome = await take_step(
                client=client, system=system, view=f"{view}\n\n{request}" if request else view, round=ctx.round,
                max_reads=self.settings.max_reads if max_reads is None else max_reads,
                reader=make_reader(ctx.ws, inputs.entries, who, budget // 4,
                                   reviews=lambda a: render_public_reviews(self.conn, a, who, budget // 4)),
                validate=make_validator(inputs.entries, ctx.agent_ids, agent_id),
                shape=shape, opening_rule=opening_rule,
            )
        except Exception as exc:  # one agent's bad step must never take down the run
            outcome = StepOutcome(Pass(type="pass"), "step_error", f"{type(exc).__name__}: {exc}")
        ctx.events.emit("agent_step_finished", round=ctx.round, agent=agent_id, action=outcome.action.type,
                        failure=outcome.failure, **extra)
        return outcome

    async def _step(self, agent_id: str, ctx: RoundContext) -> StepOutcome:
        """A react turn: one posting action."""
        return await self._turn(agent_id, ctx, shape=STEP if ctx.process == 1 else STEP_P2)
```

Remove `import asyncio`, `import random` and the `Callable`/`Sequence` imports from `runner.py` only if nothing else there still uses them (run `uv run python -c "import aiswarm.runner"` to check).

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_runner.py tests/test_process1_golden.py tests/test_config.py -q`
Expected: PASS, including `test_crash_mid_round_rolls_back_and_resumes` (a crash inside the posting transaction still rolls the round back) and the recording.

- [ ] **Step 7: Commit**

```bash
git add src/aiswarm/phases.py src/aiswarm/runner.py src/aiswarm/config.py tests/test_runner.py
git commit -m "refactor: a round runs its process's phases; resume discards what an interrupted round left behind"
```

---

### Task 6: What a Process 2 agent sees, and its prompts

**Files:**
- Modify: `src/aiswarm/view.py`
- Modify: `src/aiswarm/runner.py` (`_turn` passes `max_rounds` for Process 2)
- Create: `src/aiswarm/prompts/process2_agent_system.md`, `process2_think_request.md`, `process2_poll_request.md`
- Modify: `tests/conftest.py` (fixture `process2`)
- Test: `tests/test_view.py`

**Interfaces:**
- Consumes: `Board.entries_for` (Task 2); `NOTE_MAX` (Task 4).
- Produces: `collect_inputs(..., max_rounds: int | None = None)`; `ViewInputs.max_rounds`, `ViewInputs.think_notes: tuple[tuple[int, str], ...]`; header `# Round N of M` when `max_rounds` is set; section `# Your private notes this run (only you can see these)`; prompts `process2_agent_system.md` ($display, $max_reads, $temperament), `process2_think_request.md` ($note_max), `process2_poll_request.md` ($call, $call_id); fixture `process2(react=None, think=None, poll=None, **fake_kwargs) -> FakeModel`.

- [ ] **Step 1: Write the failing test** (append to `tests/test_view.py`)

```python
def test_process2_view_counts_rounds_and_shows_own_think_notes_only(tmp_path, community, make_run):
    store, conn, board, ws = setup(tmp_path, community, make_run)
    board.append(1, "a", "think", {"text": "FIRST-THOUGHT"}, visible_to=["a"])
    board.append(2, "a", "think", {"text": "SECOND-THOUGHT"}, visible_to=["a"])
    board.append(2, "b", "think", {"text": "B-THOUGHT"}, visible_to=["b"])
    inputs = collect_inputs(agent_id="a", goal="Pick a colour", round=3, board=board, store=store, workspace=ws,
                            view_entries=15, max_rounds=12)
    view = build_view(inputs, budget_chars=50_000)
    assert "# Round 3 of 12" in view
    assert ("# Your private notes this run (only you can see these)\n## round 1\nFIRST-THOUGHT\n\n"
            "## round 2\nSECOND-THOUGHT") in view
    assert view.index("Your private notes this run") < view.index("# Community members")
    assert "B-THOUGHT" not in view
    assert "think" not in [e.type for e in inputs.entries]
    assert "# Round 3\n" in view_for("a", 3, store, board, ws)  # Process 1 header unchanged
```

- [ ] **Step 2: Run it to see it fail**

Run: `uv run pytest tests/test_view.py -q -k process2`
Expected: FAIL — `TypeError: collect_inputs() got an unexpected keyword argument 'max_rounds'`.

- [ ] **Step 3: Implement the view**

In `ViewInputs` add (after `dimension_scores`):

```python
    max_rounds: int | None = None  # Process 2 shows "Round N of M"; Process 1 shows no limit
    think_notes: tuple[tuple[int, str], ...] = ()  # own think notes this run, (round, text)
```

`collect_inputs` gains `max_rounds: int | None = None` and builds:

```python
    entries = board.entries_for(agent_id)
    notes = tuple((e.round, e.payload.get("text", "")) for e in entries if e.type == "think" and e.author == agent_id)
    entries = [e for e in entries if e.type != "think"]  # shown in their own section, not as board entries
    if round == 1:
        entries = [e for e in entries if not (e.round == 1 and e.author != agent_id)]
```

and passes `max_rounds=max_rounds, think_notes=notes` to `ViewInputs`.

In `build_view`, the header line becomes:

```python
    rounds = f"# Round {inputs.round}" + (f" of {inputs.max_rounds}" if inputs.max_rounds else "")
    header = f"# Goal\n{inputs.goal}\n\n{rounds}\nYou are {who(inputs.agent_id)}."
```

and directly after `fixed = [header]`:

```python
    if inputs.think_notes:
        fixed.append("# Your private notes this run (only you can see these)\n"
                     + "\n\n".join(f"## round {r}\n{text.strip()}" for r, text in inputs.think_notes))
```

In `runner.py` `_turn`, pass `max_rounds=self.settings.max_rounds if ctx.process == 2 else None` to `collect_inputs`.

- [ ] **Step 4: Write the prompts**

`src/aiswarm/prompts/process2_agent_system.md`:

```
You are $display, a member of a small community of agents working together on a shared goal. There is no leader. Each member decides for themselves what to do next.

Each round has two parts. First, every member writes a private note to themselves. Then members speak one at a time, in an order that changes every round; on your turn you see everything said before it. In the opening round everyone proposes at once instead, without seeing the others' proposals.

On your turn you take exactly one action. Reply with JSON only, in the form {"action": {...}}.

Before your action you may make up to $max_reads reads. A read shows you its result and you then reply again.

Reads:
- {"type": "list_files"}: list the shared workspace files and their versions.
- {"type": "read_file", "path": "..."}: read a workspace file.
- {"type": "read_entry", "id": <entry id>}: read one board entry in full.
- {"type": "read_reviews", "agent_id": "..."}: read the reviews members have written about a member, and that member's responses.

Actions:
- {"type": "propose", "answer_summary": "...", "method": "..."}: a position on the goal and the method you used to reach it.
- {"type": "critique", "target": <entry id>, "about": "answer" or "method", "points": "..."}
- {"type": "question", "target": <entry id>, "text": "..."}
- {"type": "adopt", "target": <entry id>, "reason": "..."}: take up another member's method.
- {"type": "refine", "target": <entry id>, "change": "...", "reason": "..."}
- {"type": "write_file", "path": "...", "base_version": <current version, 0 for a new file>, "content": "...", "note": "..."}: write a file in the shared workspace. The write is rejected if the file changed since base_version.
- {"type": "call_consensus", "synthesis": "...", "refs": [<entry ids>], "credits": [{"agent_id": "...", "contribution": "..."}]}: propose that the community has its answer.
- {"type": "vote", "target": <call_consensus entry id>, "stance": "support" or "object", "reason": "..."}
- {"type": "private_message", "to": "<member id>", "text": "..."}: a message only that member will see.
- {"type": "pass"}: nothing to add right now.

After everyone has spoken, members who have not yet voted on the latest call_consensus are asked for their stance on it.

The work ends when a call_consensus has been critiqued at least once and is supported by all members but at most one. Its caller counts as a supporter.

Your temperament:
$temperament
```

`src/aiswarm/prompts/process2_think_request.md`:

```
# Think

Before this round's discussion, write a private note to yourself: where you stand on the goal now, what has changed in your view since your last note and why, and what you intend to do next. Only you will ever read it ($note_max characters at most).

You may make reads first. Then reply with JSON only: {"action": {"type": "note", "text": "..."}}
```

`src/aiswarm/prompts/process2_poll_request.md`:

```
# Poll

$call

You have not yet voted on call #$call_id. What is your stance on it now: support, object, or abstain if you would rather not take a side? Give your reason.

Reply with JSON only: {"action": {"type": "poll_answer", "stance": "support" or "object" or "abstain", "reason": "..."}}
```

- [ ] **Step 5: Add the test fixture** (append to `tests/conftest.py`)

```python
def _process2(react=None, think=None, poll=None, **kwargs):
    """A FakeModel for Process 2. Each plan maps round -> (request text -> action dict).

    Defaults: think writes "note rN", react passes, poll abstains.
    """
    def respond(system, messages):
        content = messages[0]["content"]
        rnd = int(re.search(r"^# Round (\d+)", content, re.M).group(1))
        if "\n# Think\n" in content:
            plan, default = think or {}, lambda v: {"type": "note", "text": f"note r{rnd}"}
        elif "\n# Poll\n" in content:
            plan, default = poll or {}, lambda v: {"type": "poll_answer", "stance": "abstain", "reason": "Unsure."}
        else:
            plan, default = react or {}, lambda v: {"type": "pass"}
        return json.dumps({"action": plan.get(rnd, default)(content)})

    return FakeModel(respond, **kwargs)


@pytest.fixture
def process2():
    return _process2
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_view.py tests/test_process1_golden.py tests/test_provenance.py -q`
Expected: PASS. (Provenance snapshots now include the three new prompt files; existing prompt hashes are unchanged.)

- [ ] **Step 7: Commit**

```bash
git add src/aiswarm/view.py src/aiswarm/runner.py src/aiswarm/prompts/process2_*.md tests/conftest.py tests/test_view.py
git commit -m "feat: Process 2 views count rounds and show an agent its own think notes; Process 2 prompts"
```

---

### Task 7: Think, then react in turn

**Files:**
- Modify: `src/aiswarm/phases.py` (`think`, `sequential_act`, `phases`)
- Modify: `src/aiswarm/runner.py` (`_think`, `_post_note`, private-message branch in `_post`)
- Modify: `src/aiswarm/consensus.py` (stagnation)
- Create: `tests/test_process2.py`

**Interfaces:**
- Consumes: `THINK`, `NOTE_MAX`, `PrivateMessage` (Task 4); `_turn`, `RoundContext` (Task 5); prompts and `process2` fixture (Task 6).
- Produces: `phases(2, 1) == [think, simultaneous_act, poll]`, `phases(2, n>1) == [think, sequential_act, poll]` (`poll` is a stub returning `None` until Task 8); `Runner._think(agent_id, ctx) -> StepOutcome`; `Runner._post_note(ctx, agent_id, outcome) -> dict | None`; entry types `think {text}` (visible_to [author]) and `private_message {to, text}` (`to_` = recipient, visible_to [sender, recipient]); failure kinds `think:<failure>`.

- [ ] **Step 1: Write the failing tests** (`tests/test_process2.py`)

```python
import json

import pytest

from aiswarm.board import Board
from aiswarm.events import read_events
from aiswarm.runner import Runner

AGENTS = ["a", "b", "c"]


class NoShuffle:
    def shuffle(self, items):
        pass


def propose(answer):
    return lambda v: {"type": "propose", "answer_summary": answer, "method": "M."}


def rows(conn, run_id, type_):
    return [dict(r) for r in conn.execute(
        "SELECT id, round, author, to_, visible_to, payload FROM entries WHERE run_id = ? AND type = ? ORDER BY id",
        (run_id, type_))]


def views(client):
    return [messages[0]["content"] for _, messages, _ in client.calls]


async def test_think_notes_are_private_and_shown_back_to_their_author(community, process2, runner_factory):
    store, conn = community
    clients = {a: process2(react={1: propose(a.upper())},
                           think={1: lambda v, a=a: {"type": "note", "text": f"SECRET-NOTE-{a}"}}) for a in AGENTS}
    runner = runner_factory(store, conn, clients, max_rounds=2)
    run_id = runner.start("G", AGENTS, process=2)
    await runner.run(run_id)
    notes = rows(conn, run_id, "think")
    assert [(n["round"], n["author"]) for n in notes][:3] == [(1, "a"), (1, "b"), (1, "c")]
    assert all(json.loads(n["visible_to"]) == [n["author"]] for n in notes)
    for agent in AGENTS:
        seen = "".join(views(clients[agent]))
        assert f"SECRET-NOTE-{agent}" in seen
        assert all(f"SECRET-NOTE-{other}" not in seen for other in AGENTS if other != agent)
    round2 = [v for v in views(clients["a"]) if "# Round 2 of 2" in v]
    assert round2 and "## round 1\nSECRET-NOTE-a" in round2[0]


async def test_round_one_is_blind_and_later_rounds_speak_in_turn(community, process2, find, runner_factory):
    store, conn = community
    clients = {
        "a": process2(react={1: propose("OPENING-A"), 2: lambda v: {
            "type": "question", "target": find(v, "propose", "b")[0], "text": "A-ASKS-FIRST"}}),
        "b": process2(react={1: propose("OPENING-B")}),
        "c": process2(react={1: propose("OPENING-C")}),
    }
    runner = runner_factory(store, conn, clients, rng=NoShuffle(), max_rounds=2)
    run_id = runner.start("G", AGENTS, process=2)
    await runner.run(run_id)
    react_views = {a: [v for v in views(clients[a]) if "\n# Think\n" not in v and "\n# Poll\n" not in v]
                   for a in AGENTS}
    assert "OPENING-A" not in react_views["b"][0] and "OPENING-C" not in react_views["b"][0]  # round 1: blind
    assert "A-ASKS-FIRST" in react_views["b"][1] and "A-ASKS-FIRST" in react_views["c"][1]  # round 2: a spoke first


async def test_private_messages_reach_only_their_recipient(community, process2, runner_factory):
    store, conn = community
    dm = lambda v: {"type": "private_message", "to": "b", "text": "SECRET-DM"}
    clients = {"a": process2(react={1: propose("A"), 2: dm}),
               "b": process2(react={1: propose("B")}), "c": process2(react={1: propose("C")})}
    runner = runner_factory(store, conn, clients, rng=NoShuffle(), max_rounds=3)
    run_id = runner.start("G", AGENTS, process=2)
    await runner.run(run_id)
    [message] = rows(conn, run_id, "private_message")
    assert (message["author"], message["to_"], json.loads(message["visible_to"])) == ("a", "b", ["a", "b"])
    assert any("SECRET-DM" in v for v in views(clients["b"]))
    assert not any("SECRET-DM" in v for v in views(clients["c"]))


async def test_a_private_message_to_oneself_becomes_an_invalid_action(community, process2, runner_factory):
    store, conn = community
    clients = {"a": process2(react={1: propose("A"), 2: lambda v: {"type": "private_message", "to": "a", "text": "x"}}),
               "b": process2(react={1: propose("B")}), "c": process2(react={1: propose("C")})}
    runner = runner_factory(store, conn, clients, max_rounds=2)
    run_id = runner.start("G", AGENTS, process=2)
    await runner.run(run_id)
    assert rows(conn, run_id, "private_message") == []
    assert ("a", "invalid_action") in [tuple(r) for r in conn.execute(
        "SELECT agent_id, kind FROM failures WHERE run_id = ? AND round = 2", (run_id,))]


async def test_a_failed_think_is_recorded_and_the_agent_still_reacts(community, process2, runner_factory):
    store, conn = community
    clients = {"a": process2(react={1: propose("A")}, think={1: lambda v: {"type": "pass"}}),
               "b": process2(react={1: propose("B")}), "c": process2(react={1: propose("C")})}
    runner = runner_factory(store, conn, clients, max_rounds=1)
    run_id = runner.start("G", AGENTS, process=2)
    await runner.run(run_id)
    assert [n["author"] for n in rows(conn, run_id, "think")] == ["b", "c"]
    assert ("a", "think:invalid_action") in [tuple(r) for r in conn.execute(
        "SELECT agent_id, kind FROM failures WHERE run_id = ?", (run_id,))]
    assert "a" in [p["author"] for p in rows(conn, run_id, "propose")]


async def test_process2_stagnates_when_nobody_speaks(community, process2, runner_factory):
    store, conn = community
    clients = {a: process2(react={1: propose(a)}) for a in AGENTS}
    runner = runner_factory(store, conn, clients, max_rounds=6)
    result = await runner.run(runner.start("G", AGENTS, process=2))
    assert result.reason == "stagnation"


async def test_resume_discards_a_partial_round_and_replays_it(tmp_path, community, process2, find, runner_factory):
    store, conn = community

    def clients():
        return {a: process2(react={1: propose(a), 2: lambda v, a=a: {
            "type": "question", "target": find(v, "propose")[0], "text": f"Q-{a}"}}) for a in AGENTS}

    class CrashingRunner(Runner):
        def _post(self, board, ws, run_id, rnd, agent_id, outcome):
            if rnd == 2 and agent_id == "b":
                raise RuntimeError("crash")
            return super()._post(board, ws, run_id, rnd, agent_id, outcome)

    first = runner_factory(store, conn, clients(), rng=NoShuffle(), max_rounds=2)
    crashing = CrashingRunner(settings=first.settings, conn=conn, store=store, runs_dir=tmp_path / "runs",
                              clients=clients(), rng=NoShuffle())
    run_id = crashing.start("G", AGENTS, process=2)
    with pytest.raises(RuntimeError, match="crash"):
        await crashing.run(run_id)
    assert [q["author"] for q in rows(conn, run_id, "question")] == ["a"]  # a spoke before the crash

    result = await runner_factory(store, conn, clients(), rng=NoShuffle(), max_rounds=2).run(run_id)
    assert result.status == "closed"
    assert [q["author"] for q in rows(conn, run_id, "question")] == ["a", "b", "c"]
    assert len([n for n in rows(conn, run_id, "think") if n["round"] == 2]) == 3
    events = read_events(tmp_path / "runs" / run_id / "events.jsonl")
    assert [e["round"] for e in events if e["event"] == "round_discarded"] == [2]


async def test_process2_header_counts_rounds_and_process1_does_not(community, process2, scripted, runner_factory):
    store, conn = community
    p2 = {a: process2(react={1: propose(a)}) for a in AGENTS}
    runner = runner_factory(store, conn, p2, max_rounds=1)
    await runner.run(runner.start("G", AGENTS, process=2))
    assert all("# Round 1 of 1" in v for v in views(p2["a"]))
    p1 = {a: scripted({1: propose(a)}) for a in AGENTS}
    runner = runner_factory(store, conn, p1, max_rounds=1)
    await runner.run(runner.start("G", AGENTS))
    assert all("# Round 1\n" in v and " of 1" not in v for v in views(p1["a"]))
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_process2.py -q`
Expected: FAIL — `ValueError: unknown process 2`.

- [ ] **Step 3: Implement the phases** (in `src/aiswarm/phases.py`)

```python
async def think(runner: "Runner", ctx: RoundContext) -> str | None:
    """Every agent writes a private note to itself, all at once; the notes are posted after all have finished."""
    outcomes = await asyncio.gather(*(runner._think(a, ctx) for a in ctx.agent_ids))
    if all(o.failure == "model_error" for o in outcomes):
        return outcomes[0].detail
    with transaction(runner.conn):
        posted = [runner._post_note(ctx, a, o) for a, o in zip(ctx.agent_ids, outcomes)]
    for event in posted:
        if event:
            ctx.events.emit("action_posted", **event)
    return None


async def sequential_act(runner: "Runner", ctx: RoundContext) -> str | None:
    """Agents speak one at a time in a seeded order; each sees what was said before its turn."""
    order = list(ctx.agent_ids)
    ctx.rng(runner).shuffle(order)
    errors: list[str] = []
    for agent_id in order:
        outcome = await runner._step(agent_id, ctx)
        if outcome.failure == "model_error":
            errors.append(outcome.detail)
        with transaction(runner.conn):
            event = runner._post(ctx.board, ctx.ws, ctx.run_id, ctx.round, agent_id, outcome)
        ctx.events.emit("action_posted", **event)
    return errors[0] if len(errors) == len(order) else None


async def poll(runner: "Runner", ctx: RoundContext) -> str | None:
    return None  # Task 8


def phases(process: int, round: int) -> list[Phase]:
    if process == 1:
        return [simultaneous_act]
    if process == 2:
        return [think, simultaneous_act if round == 1 else sequential_act, poll]
    raise ValueError(f"unknown process {process}")
```

- [ ] **Step 4: Implement the runner parts**

Imports in `runner.py`: `from aiswarm.actions import NOTE_MAX, STEP, STEP_P2, THINK, Pass, PrivateMessage, WriteFile, target_refs` and `from aiswarm.prompts import render`.

```python
    async def _think(self, agent_id: str, ctx: RoundContext) -> StepOutcome:
        request = render("process2_think_request.md", note_max=f"{NOTE_MAX:,}")
        return await self._turn(agent_id, ctx, phase="think", request=request, shape=THINK, opening_rule=False)

    def _trace(self, entry_id: int, run_id: str, agent_id: str, outcome: StepOutcome) -> None:
        if outcome.reasoning:
            self.conn.execute(
                "INSERT INTO traces (entry_id, run_id, agent_id, model, reasoning) VALUES (?, ?, ?, ?, ?)",
                (entry_id, run_id, agent_id, outcome.model, outcome.reasoning),
            )

    def _post_note(self, ctx: RoundContext, agent_id: str, outcome: StepOutcome) -> dict[str, Any] | None:
        """A think note, private to its author. A failed think records the failure and fabricates nothing."""
        if outcome.failure:
            self._fail(ctx.run_id, ctx.round, agent_id, f"think:{outcome.failure}", outcome.detail)
            return None
        entry = ctx.board.append(ctx.round, agent_id, "think", {"text": outcome.action.text}, visible_to=[agent_id])
        self._trace(entry.id, ctx.run_id, agent_id, outcome)
        return {"round": ctx.round, "agent": agent_id, "entry": entry.id, "type": "think", "failure": None}
```

In `_post`, add a branch before the final `else:`:

```python
        elif isinstance(action, PrivateMessage):
            entry = board.append(rnd, agent_id, "private_message", {"to": action.to, "text": action.text},
                                 to=action.to, visible_to=[agent_id, action.to])
```

and replace the trailing traces block in `_post` with `self._trace(entry.id, run_id, agent_id, outcome)`.

- [ ] **Step 5: Stagnation ignores think notes; abstaining is idle** (`src/aiswarm/consensus.py`)

```python
def _is_idle(entry: Entry, entries: Sequence[Entry]) -> bool:
    """A pass or an abstention, or an adopt repeating one the same author already made."""
    if entry.type in ("pass", "abstain"):
        return True
```

(keep the rest of `_is_idle`), and in `_idle_round`:

```python
    posted = [e for e in entries if e.round == round and e.author in agent_ids and e.type != "think"]
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_process2.py tests/test_runner.py tests/test_consensus.py tests/test_process1_golden.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/aiswarm/phases.py src/aiswarm/runner.py src/aiswarm/consensus.py tests/test_process2.py
git commit -m "feat: Process 2 rounds: private think notes, then members speak in turn, with private messages"
```

---

### Task 8: The poll

**Files:**
- Modify: `src/aiswarm/phases.py` (`poll`)
- Modify: `src/aiswarm/runner.py` (`_poll`, `_post_poll`)
- Test: `tests/test_process2.py`

**Interfaces:**
- Consumes: `POLL`, `PollAnswer` (Task 4); `_turn`, `_trace`, `_fail` (Tasks 5, 7); `call_status` (`aiswarm.consensus`); `render_entry`, `make_who` (`aiswarm.view`).
- Produces: `Runner._poll(agent_id, ctx, call: Entry) -> StepOutcome`; `Runner._post_poll(ctx, agent_id, outcome, call) -> dict | None`; entry type `abstain {target, reason}` (public, refs [call]); poll support/object posted as `vote`; failure kinds `poll:<failure>`; events carry `phase="poll"`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_process2.py`)

```python
def call_then_critique(find):
    return {
        "a": {1: propose("A"), 2: lambda v: {"type": "call_consensus", "synthesis": "A it is",
                                             "refs": find(v, "propose", "a"), "credits": []}},
        "b": {1: propose("B"), 2: lambda v: {"type": "critique", "target": find(v, "call_consensus")[0],
                                             "about": "answer", "points": "Hasty."}},
        "c": {1: propose("C")},
    }


async def test_the_poll_turns_stances_into_votes_and_reaches_consensus(community, process2, find, runner_factory):
    store, conn = community
    support = lambda v: {"type": "poll_answer", "stance": "support", "reason": "Fine."}
    plans = call_then_critique(find)
    clients = {a: process2(react=plans[a], poll={2: support}) for a in AGENTS}
    runner = runner_factory(store, conn, clients, rng=NoShuffle(), max_rounds=4)
    run_id = runner.start("G", AGENTS, process=2)
    result = await runner.run(run_id)
    assert (result.status, result.reason) == ("closed", "consensus")
    assert sorted(v["author"] for v in rows(conn, run_id, "vote")) == ["b", "c"]
    polled = [messages[0]["content"] for _, messages, _ in clients["a"].calls if "\n# Poll\n" in messages[0]["content"]]
    assert polled == []  # the caller is never polled


async def test_abstaining_is_recorded_and_counts_for_nothing(community, process2, find, runner_factory):
    store, conn = community
    plans = call_then_critique(find)
    clients = {a: process2(react=plans[a]) for a in AGENTS}  # the default poll answer abstains
    runner = runner_factory(store, conn, clients, rng=NoShuffle(), max_rounds=2)
    run_id = runner.start("G", AGENTS, process=2)
    result = await runner.run(run_id)
    assert result.reason == "budget"
    abstained = rows(conn, run_id, "abstain")
    assert sorted(a["author"] for a in abstained) == ["b", "c"] and all(a["visible_to"] is None for a in abstained)
    assert rows(conn, run_id, "vote") == []


async def test_a_failed_poll_answer_records_a_failure_and_no_vote(community, process2, find, runner_factory):
    store, conn = community
    plans = call_then_critique(find)
    clients = {"a": process2(react=plans["a"]), "b": process2(react=plans["b"]),
               "c": process2(react=plans["c"], poll={2: lambda v: {"type": "poll_answer", "stance": "maybe",
                                                                     "reason": "r"}})}
    runner = runner_factory(store, conn, clients, rng=NoShuffle(), max_rounds=2)
    run_id = runner.start("G", AGENTS, process=2)
    await runner.run(run_id)
    assert "c" not in [v["author"] for v in rows(conn, run_id, "vote") + rows(conn, run_id, "abstain")]
    assert ("c", "poll:invalid_action") in [tuple(r) for r in conn.execute(
        "SELECT agent_id, kind FROM failures WHERE run_id = ?", (run_id,))]


async def test_a_poll_outage_pauses_without_inventing_votes(community, process2, find, runner_factory):
    store, conn = community
    plans = call_then_critique(find)

    class PollDown:
        """Answers like the scripted model, except every poll request fails."""
        def __init__(self, inner):
            self.inner, self.context_window, self.calls = inner, inner.context_window, inner.calls

        async def chat(self, system, messages, json_schema=None):
            if "\n# Poll\n" in messages[0]["content"]:
                from aiswarm.models.base import ModelError
                raise ModelError("down")
            return await self.inner.chat(system, messages, json_schema)

    clients = {a: PollDown(process2(react=plans[a])) for a in AGENTS}
    runner = runner_factory(store, conn, clients, rng=NoShuffle(), max_rounds=3)
    run_id = runner.start("G", AGENTS, process=2)
    result = await runner.run(run_id)
    assert result.status == "paused"
    assert rows(conn, run_id, "vote") == [] and rows(conn, run_id, "abstain") == []
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_process2.py -q -k poll`
Expected: FAIL — consensus test ends by `budget`, no votes recorded.

- [ ] **Step 3: Implement the phase** (replace the stub in `src/aiswarm/phases.py`; import `call_status` from `aiswarm.consensus`)

```python
async def poll(runner: "Runner", ctx: RoundContext) -> str | None:
    """Members who have not yet voted on the latest call are asked for their stance, all at once."""
    entries = ctx.board.entries()
    calls = [e for e in entries if e.type == "call_consensus"]
    if not calls:
        return None
    call = calls[-1]
    status = call_status(entries, call.id)
    asked = [a for a in ctx.agent_ids if a not in status.supporters | status.objectors]
    if not asked:
        return None
    outcomes = await asyncio.gather(*(runner._poll(a, ctx, call) for a in asked))
    if all(o.failure == "model_error" for o in outcomes):
        return outcomes[0].detail
    order = list(zip(asked, outcomes))
    ctx.rng(runner, "-poll").shuffle(order)
    with transaction(runner.conn):
        posted = [runner._post_poll(ctx, a, o, call) for a, o in order]
    for event in posted:
        if event:
            ctx.events.emit("action_posted", **event)
    return None
```

- [ ] **Step 4: Implement the runner parts**

Imports: add `POLL` to the `aiswarm.actions` import; `from aiswarm.board import SYSTEM, Board, Entry, public`; add `render_entry` to the `aiswarm.view` import.

```python
    async def _poll(self, agent_id: str, ctx: RoundContext, call: Entry) -> StepOutcome:
        who = make_who({i: self.store.public(i) for i in self.store.ids()})
        request = render("process2_poll_request.md", call=render_entry(call, who), call_id=call.id)
        return await self._turn(agent_id, ctx, phase="poll", request=request, shape=POLL, opening_rule=False,
                                max_reads=0)

    def _post_poll(self, ctx: RoundContext, agent_id: str, outcome: StepOutcome, call: Entry) -> dict[str, Any] | None:
        """support / object become ordinary votes; abstain is recorded publicly; a failure fabricates nothing."""
        if outcome.failure:
            self._fail(ctx.run_id, ctx.round, agent_id, f"poll:{outcome.failure}", outcome.detail)
            return None
        answer = outcome.action
        if answer.stance == "abstain":
            entry = ctx.board.append(ctx.round, agent_id, "abstain", {"target": call.id, "reason": answer.reason},
                                     refs=[call.id])
        else:
            entry = ctx.board.append(ctx.round, agent_id, "vote",
                                     {"target": call.id, "stance": answer.stance, "reason": answer.reason},
                                     refs=[call.id])
        self._trace(entry.id, ctx.run_id, agent_id, outcome)
        return {"round": ctx.round, "agent": agent_id, "entry": entry.id, "type": entry.type, "failure": None}
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_process2.py tests/test_process1_golden.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/aiswarm/phases.py src/aiswarm/runner.py tests/test_process2.py
git commit -m "feat: Process 2 polls members who have not voted on the latest call; abstaining is allowed"
```

---

### Task 9: Think notes reach the agent's post-run journal

**Files:**
- Modify: `src/aiswarm/postrun/cycle.py` (`_journal`)
- Test: `tests/test_postrun.py`

**Interfaces:**
- Consumes: `private_run` helper and `_seen` (Task 3).
- Produces: the journal request for an agent ends its packet with `## Your private notes from this run` listing `Round N: <text>` for its own think notes; Process 1 journal requests are unchanged.

- [ ] **Step 1: Write the failing test** (append to `tests/test_postrun.py`)

```python
async def test_the_journal_step_shows_an_agent_its_own_think_notes(tmp_path, community, make_run, postrun_reply):
    store, conn = community
    run_id = private_run(conn, tmp_path, make_run)
    clients = {a: FakeModel(responder(postrun_reply)) for a in "abc"}
    await cycle(store, conn, tmp_path, clients).run(run_id)
    journal_a = next(m[0]["content"] for _, m, _ in clients["a"].calls if m[0]["content"].startswith("# Post-run: journal"))
    assert "## Your private notes from this run\nRound 2: SECRET-THOUGHT-A" in journal_a
    journal_c = next(m[0]["content"] for _, m, _ in clients["c"].calls if m[0]["content"].startswith("# Post-run: journal"))
    assert "Your private notes from this run" not in journal_c
```

- [ ] **Step 2: Run it to see it fail**

Run: `uv run pytest tests/test_postrun.py -q -k own_think_notes`
Expected: FAIL — the heading is missing from a's journal request.

- [ ] **Step 3: Implement** (`_journal` in `src/aiswarm/postrun/cycle.py`)

```python
    async def _journal(self, ctx: _Context, agent_id: str) -> str | None:
        packet = self._packet(ctx, agent_id)
        notes = [e for e in ctx.entries if e.type == "think" and e.author == agent_id]
        if notes:  # Process 2 only; Process 1 runs have none, so their request is unchanged
            packet += "\n\n## Your private notes from this run\n" + "\n\n".join(
                f"Round {e.round}: {e.payload.get('text', '').strip()}" for e in notes)
        request = render("journal_request.md", packet=packet, journal_max=JOURNAL_MAX)
        answer = await ask(self.clients[agent_id], self._system(ctx, agent_id), request, JournalReply)
        write_journal(self.conn, self.store, agent_id, ctx.run_id, "before", answer.reply.journal)
        return answer.reasoning
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_postrun.py tests/test_process1_golden.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aiswarm/postrun/cycle.py tests/test_postrun.py
git commit -m "feat: an agent's post-run journal step sees its own think notes from the run"
```

---

### Task 10: `aiswarm run --process`, the watch feed, and docs

**Files:**
- Modify: `src/aiswarm/cli.py` (`run`)
- Modify: `src/aiswarm/watch/state.py` (`_SUMMARY`)
- Modify: `docs/superpowers/specs/2026-10-11-aiswarm-process-2-design.md` (status, rulings)
- Test: `tests/test_cli.py`, `tests/test_watch_state.py`

**Interfaces:**
- Consumes: `Runner.start(process=)` (Task 5); `RunSettings.process` (Task 5).
- Produces: `aiswarm run --process {1,2}`; with `--resume` the run's own process is used and `--process` is an error; provenance records `run.process`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py`:

```python
def test_run_with_process_2_records_it_and_resume_keeps_it(project):
    tmp_path, config = project
    make_agents(config, "a", "b")
    result = invoke("run", "Pick a colour", "--process", "2", "--max-rounds", "2", "--no-postrun", "-c", config)
    assert result.exit_code == 0, result.output
    run_id = run_id_from(result.output)
    import sqlite3
    conn = sqlite3.connect(tmp_path / "community" / "community.db")
    assert conn.execute("SELECT process FROM runs WHERE id = ?", (run_id,)).fetchone()[0] == 2
    provenance = json.loads(conn.execute("SELECT config FROM run_provenance WHERE run_id = ?", (run_id,)).fetchone()[0])
    assert provenance["run"]["process"] == 2
    rejected = invoke("run", "--resume", run_id, "--process", "1", "-c", config)
    assert rejected.exit_code != 0 and "keeps its process" in rejected.output
    assert invoke("run", "G", "--process", "3", "-c", config).exit_code != 0
```

Append to `tests/test_watch_state.py`:

```python
def test_feed_text_summarises_process2_entries():
    assert feed_text(entry(7, "think", {"text": "where I stand"}), who).endswith("think: where I stand")
    assert feed_text(entry(8, "private_message", {"to": "b", "text": "psst"}), who).endswith("private_message: psst")
    assert feed_text(entry(9, "abstain", {"target": 4, "reason": "unsure"}), who).endswith("abstain → #4: unsure")
```

(`entry` and `who` are the helpers already defined in `tests/test_watch_state.py`; check their signatures there and match them.)

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_cli.py tests/test_watch_state.py -q -k "process_2 or process2"`
Expected: FAIL — `No such option: --process`.

- [ ] **Step 3: Implement the CLI**

In `run`, add the option after `max_rounds`:

```python
    process: Annotated[int | None, typer.Option("--process", help="1: everyone acts at once (default). 2: think, then react in turn, with a poll.")] = None,
```

After `cfg = _load(config)` and the `max_rounds` override:

```python
    if process is not None and process not in (1, 2):
        _fail("--process must be 1 or 2")
```

In the `if resume:` branch, after the `row is None` check:

```python
        if process is not None:
            _fail("a resumed run keeps its process; drop --process")
        cfg.run.process = conn.execute("SELECT process FROM runs WHERE id = ?", (resume,)).fetchone()[0]
```

In the `else:` branch (new run), before `agent_ids = ...`:

```python
        if process is not None:
            cfg.run.process = process
```

and pass `process=cfg.run.process` to `runner.start(...)`.

- [ ] **Step 4: Implement the watch summaries** (`src/aiswarm/watch/state.py`)

```python
_SUMMARY = {"propose": "answer_summary", "critique": "points", "adopt": "reason", "refine": "change",
            "call_consensus": "synthesis", "think": "text", "private_message": "text", "abstain": "reason"}
```

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass (the 439 from before plus this plan's tests).

- [ ] **Step 6: Mark the spec**

In `docs/superpowers/specs/2026-10-11-aiswarm-process-2-design.md`, change the status line to `- **Status:** Approved; Plan 4a (engine) implemented; 4b (Observer) and 4c (TUI) pending` and add under §2:

```markdown
**Settled in Plan 4a:** prompt files are flat (`prompts/process2_*.md`) so provenance records them; think and poll replies use the action envelope (`{"action": {"type": "note", ...}}`, `{"action": {"type": "poll_answer", ...}}`); think entries store `{"text": ...}`; a failed think or poll is an `agent_step_finished` event with `phase` and a `failures` row of kind `think:<failure>` / `poll:<failure>`; an agent's own think notes appear in one section, after the header, in every Process 2 turn.
```

- [ ] **Step 7: Commit**

```bash
git add src/aiswarm/cli.py src/aiswarm/watch/state.py tests/test_cli.py tests/test_watch_state.py docs/superpowers/specs/2026-10-11-aiswarm-process-2-design.md
git commit -m "feat: aiswarm run --process 2; the watch feed summarises think notes, private messages and abstentions"
```
