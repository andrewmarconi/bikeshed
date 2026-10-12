# Scorer Engine (Plan 5a) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A call for consensus that passes the community's rule is submitted to an outside Scorer that rules only whether its synthesis answers the goal; accepted calls close the run and award Scorer points to the credited members, rejected calls are spent and their feedback is posted, and the agents are told how this works.

**Architecture:** A new `scorer.py` holds the Scorer call (one correction round, like the Observer's judge), the ruling model, and point counting. `consensus.py` learns spent calls. The Runner gains an optional `scorer` client and a `_settle` step that runs after every round (and once on resume) in place of the bare `stop_decision` check: a passing call goes to the Scorer; accept closes the run with points in the same transaction; reject posts `scorer_rejected`, writes `scorer_feedback.md`, and work continues; a failed ruling pauses. `Runner(scorer=None)` keeps today's behaviour for library and test callers; the CLI always supplies a Scorer and refuses to run without `[scorer]`.

**Tech Stack:** Python 3.14, uv, pydantic, sqlite3, pytest (asyncio auto), `FakeModel`.

**Spec:** `docs/superpowers/specs/2026-10-12-aiswarm-scorer-design.md` (this plan: §3, §4, §5, §8, §9 items 1–6 and 9). Plan 5b covers §6 (TUI) and §7 (Observer).

## Global Constraints

- The Scorer rules only on whether the synthesis gives the concrete answer the goal asks for; it never judges quality and never proposes an answer (spec §2, §3.2).
- Scorer input is the goal and the synthesis only: no names, credits, votes, board or workspace (spec §3.2).
- Nothing is fabricated: no ruling means the run pauses; `aiswarm run --resume` asks the Scorer again before any new round (spec §3.2, §8).
- A rejected call is spent: it never closes the run, the Process 2 poll skips it, and votes on it count for nothing (spec §3.3).
- One point per credited member of an accepted call (self-credit counts; non-members earn nothing); points never decay or reset; totals are counted from rows (spec §4).
- Agent-facing text added (verbatim, spec §3.1): "An outside Scorer reviews every call for consensus that passes. It accepts a call only if its synthesis gives the concrete answer the goal asks for; it does not judge whether the answer is good. If it rejects a call, its feedback appears on the board and in `scorer_feedback.md`, and work continues. When a call is accepted, every member credited in it earns a Scorer point."
- Prompts are flat files in `src/aiswarm/prompts/` so provenance records them; the Scorer prompt is `prompts/scorer.md`.
- The Observer never feeds back and is untouched by this plan.
- **Lab safety:** never edit prompt files in the main checkout while a lab run is live (the runner reads prompts from disk each turn); work in the sibling worktree only. Run `pgrep -fl "aiswarm (run|postrun)"` before any commit or merge to `develop`.
- No new dependencies. Run with uv: `uv run pytest -q`.

## Rulings in this plan

- **`Runner(scorer=None)`** closes on a passing call without a ruling, exactly as today. It exists for library and test callers (most runner tests and the Process 1 golden recording); the spec's "no switch" is enforced where runs are started, in the CLI.
- **Ordering for resume:** the Scorer step runs after a round's `rounds` row is written; a rejection updates that row's `workspace_commit` so `scorer_feedback.md` survives a resume; a paused ruling leaves the round recorded, and `run()` calls `_settle` once before the loop so a resumed run asks the Scorer before any new round.
- **View:** spent calls leave "# Open calls for consensus" and are listed under a new "# Calls the Scorer rejected" section with their feedback (the section appears only when there is one, so views without rejections are unchanged apart from the points suffix).
- **Default config:** `aiswarm init` writes `[scorer] judge = "claude"` (the template's `claude_cli` backend).

## Review Focus

1. **A rejection on the last round**: the run must close on `budget`, not loop or re-submit the spent call. Pinned in Task 3.
2. **A crash or pause between the Scorer's acceptance and the close**: points must never be awarded twice. Accept writes points, the `scorer_accepted` entry and the close in one transaction. Pinned in Task 3 (points count after resume).
3. **A resumed run whose last round already passed a call** (paused at the Scorer): resume must ask the Scorer before starting any round, and must not replay the round. Pinned in Task 3.
4. **A ruling without feedback on rejection, or not JSON**: one correction round, then pause; never a guessed ruling. Pinned in Task 1 and Task 3.
5. **Credits naming a non-member or the same member twice**: no point for the non-member, one point per member. Pinned in Task 3.

---

### Task 1: The Scorer call

**Files:**
- Create: `src/aiswarm/scorer.py`
- Create: `src/aiswarm/prompts/scorer.md`
- Test: `tests/test_scorer.py`

**Interfaces:**
- Produces:
  - `Ruling(accepted: bool, feedback: str = "")` (pydantic; rejection requires non-blank feedback; `FEEDBACK_MAX = 600`)
  - `ScoreResult(ruling: Ruling | None, detail: str | None = None, model: str | None = None)`
  - `scorer_request(goal: str, synthesis: str) -> str`
  - `async score(client: ModelClient, goal: str, synthesis: str) -> ScoreResult`
  - `points(conn: sqlite3.Connection) -> dict[str, int]` (lifetime points per agent; used in Task 4) — added in Task 3 with the table.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_scorer.py`:

```python
import json

import pytest
from pydantic import ValidationError

from aiswarm.models.fake import FakeModel
from aiswarm.scorer import Ruling, score, scorer_request

ACCEPT = json.dumps({"accepted": True, "feedback": ""})
REJECT = json.dumps({"accepted": False, "feedback": "It describes a poll, not a topping combination."})


def test_a_rejection_needs_feedback():
    assert Ruling(accepted=True).feedback == ""
    with pytest.raises(ValidationError):
        Ruling(accepted=False, feedback="   ")


def test_the_scorer_sees_only_the_goal_and_the_synthesis():
    request = scorer_request("Pick a topping.", "Mushrooms and olives.")
    assert request == "## Goal\n\nPick a topping.\n\n## Synthesis\n\nMushrooms and olives.\n\nReply with JSON only."


async def test_score_returns_the_ruling_and_the_reported_model():
    client = FakeModel([REJECT], model="claude-x")
    result = await score(client, "Pick a topping.", "We will run a poll.")
    assert result.ruling == Ruling(accepted=False, feedback="It describes a poll, not a topping combination.")
    assert result.model == "claude-x" and result.detail is None
    system, messages, schema = client.calls[0]
    assert "Scorer" in system and messages[0]["content"] == scorer_request("Pick a topping.", "We will run a poll.")
    assert schema is not None


async def test_an_invalid_ruling_gets_one_correction():
    client = FakeModel([json.dumps({"accepted": False, "feedback": ""}), ACCEPT])
    result = await score(client, "G", "S")
    assert result.ruling == Ruling(accepted=True) and len(client.calls) == 2


async def test_two_invalid_rulings_give_no_ruling():
    client = FakeModel(["not json", "still not json"])
    result = await score(client, "G", "S")
    assert result.ruling is None and result.detail.startswith("invalid ruling:")


async def test_a_model_error_gives_no_ruling():
    result = await score(FakeModel([ACCEPT], fail_times=1), "G", "S")
    assert result.ruling is None and result.detail.startswith("model error:")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_scorer.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'aiswarm.scorer'`

- [ ] **Step 3: Write the prompt**

Create `src/aiswarm/prompts/scorer.md`:

```markdown
You are the Scorer for a small community of AI agents. The community works on a goal and, when it reaches a consensus, submits its synthesis to you. You decide one thing only: does the synthesis give the concrete answer the goal asks for?

Accept when the synthesis states an answer of the kind the goal asks for, whatever you think of its quality. Reject when it does not: for example when it describes a process for deciding later, defers the decision, lists options without choosing one where the goal asks for one, or answers a different question.

Never judge whether the answer is good, wise or well argued, and never suggest what the answer should be.

If you reject, give feedback in one or two sentences saying what is missing for the synthesis to answer the goal. Do not propose an answer.

Reply with JSON only: {"accepted": true or false, "feedback": "..."}
```

- [ ] **Step 4: Implement `src/aiswarm/scorer.py`**

```python
"""The Scorer: an outside check that a passing call for consensus answers the goal. It never judges quality."""

from dataclasses import dataclass

from pydantic import BaseModel, Field, ValidationError, model_validator

from aiswarm.actions import ActionError, correction_request, extract_json, simplify_schema, summarise_validation
from aiswarm.models.base import Message, ModelClient, ModelError
from aiswarm.prompts import render

FEEDBACK_MAX = 600


class Ruling(BaseModel):
    accepted: bool
    feedback: str = Field("", max_length=FEEDBACK_MAX)

    @model_validator(mode="after")
    def _feedback_on_rejection(self) -> "Ruling":
        if not self.accepted and not self.feedback.strip():
            raise ValueError("a rejection needs feedback: what is missing for the synthesis to answer the goal")
        return self


@dataclass(frozen=True)
class ScoreResult:
    ruling: Ruling | None  # None: no valid ruling, so the run pauses; nothing is guessed
    detail: str | None = None
    model: str | None = None


def scorer_request(goal: str, synthesis: str) -> str:
    """Everything the Scorer sees: the goal and the synthesis. No names, credits, votes, board or workspace."""
    return f"## Goal\n\n{goal.strip()}\n\n## Synthesis\n\n{synthesis.strip()}\n\nReply with JSON only."


async def score(client: ModelClient, goal: str, synthesis: str) -> ScoreResult:
    system = render("scorer.md")
    messages: list[Message] = [{"role": "user", "content": scorer_request(goal, synthesis)}]
    schema = simplify_schema(Ruling.model_json_schema())
    problem, model = "no reply", None
    for _ in range(2):
        try:
            result = await client.chat(system, messages, schema)
        except ModelError as exc:
            return ScoreResult(None, f"model error: {exc}", model)
        model = result.model or model
        try:
            return ScoreResult(Ruling.model_validate(extract_json(result.text)), None, model)
        except ValidationError as exc:
            problem = summarise_validation(exc)
        except ActionError as exc:
            problem = str(exc)
        messages += [{"role": "assistant", "content": result.text},
                     {"role": "user", "content": correction_request(problem)}]
    return ScoreResult(None, f"invalid ruling: {problem}", model)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_scorer.py tests/test_provenance.py -q`
Expected: PASS (`scorer.md` is now part of the prompt snapshot; provenance tests do not pin the prompt list).

- [ ] **Step 6: Commit**

```bash
git add src/aiswarm/scorer.py src/aiswarm/prompts/scorer.md tests/test_scorer.py
git commit -m "feat: the Scorer rules whether a synthesis answers the goal, with one correction round"
```

---

### Task 2: Spent calls

**Files:**
- Modify: `src/aiswarm/consensus.py` (`spent_calls`, `StopDecision` reason docs unchanged, `stop_decision` skips spent calls)
- Modify: `src/aiswarm/phases.py` (`poll` skips a spent latest call)
- Test: `tests/test_consensus.py`, `tests/test_process2.py`

**Interfaces:**
- Produces: `spent_calls(entries: Sequence[Entry]) -> set[int]` — ids of calls with a `scorer_rejected` entry (payload `call_id`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_consensus.py` (add `spent_calls` to the `aiswarm.consensus` import):

```python
def test_a_call_the_scorer_rejected_is_spent():
    entries = base() + [
        e(6, 3, "b", "critique", target=5, about="answer", points="p"),
        e(7, 3, "c", "vote", target=5, stance="support", reason="r"),
        e(8, 3, "system", "scorer_rejected", call_id=5, feedback="No answer."),
    ]
    assert spent_calls(entries) == {5}
    assert stop_decision(entries, AGENTS, 3, RunSettings()) is None
    later = entries + [e(9, 4, "c", "vote", target=5, stance="support", reason="again")]
    assert stop_decision(later, AGENTS, 4, RunSettings()) is None  # votes on a spent call count for nothing
```

Append to `tests/test_process2.py`:

```python
async def test_the_poll_skips_a_call_the_scorer_rejected():
    from types import SimpleNamespace

    from aiswarm.board import Entry
    from aiswarm.phases import poll

    def entry(id_, author, type_, payload):
        return Entry(id_, "r1", 2, author, type_, payload, [], "all", "t")

    entries = [entry(5, "a", "call_consensus", {"synthesis": "S", "credits": []}),
               entry(6, "system", "scorer_rejected", {"call_id": 5, "feedback": "No answer."})]
    ctx = SimpleNamespace(board=SimpleNamespace(entries=lambda: entries), agent_ids=["a", "b", "c"])

    class NoPoll:
        async def _poll(self, *args):
            raise AssertionError("a spent call must not be polled")

    assert await poll(NoPoll(), ctx) is None
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_consensus.py tests/test_process2.py -q -k "spent or rejected"`
Expected: FAIL — `ImportError: cannot import name 'spent_calls'` (collection error), then after the import works, the poll test fails with the AssertionError.

- [ ] **Step 3: Implement**

In `consensus.py`, before `stop_decision`:

```python
def spent_calls(entries: Sequence[Entry]) -> set[int]:
    """Calls the Scorer rejected: they can never close the run, and votes on them count for nothing."""
    return {e.payload.get("call_id") for e in entries if e.type == "scorer_rejected"}
```

and in `stop_decision`:

```python
    spent = spent_calls(entries)
    for entry in entries:
        if entry.type == "call_consensus" and entry.id not in spent:
```

In `phases.py`, import `spent_calls` alongside `call_status`, and in `poll` after `call = calls[-1]`:

```python
    if call.id in spent_calls(entries):
        return None  # the Scorer rejected it; there is nothing to poll on
```

- [ ] **Step 4: Run them to verify they pass**

Run: `uv run pytest tests/test_consensus.py tests/test_process2.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/aiswarm/consensus.py src/aiswarm/phases.py tests/test_consensus.py tests/test_process2.py
git commit -m "feat: a call the Scorer rejected is spent: it cannot close the run and is not polled"
```

---

### Task 3: The Runner submits passing calls to the Scorer

**Files:**
- Modify: `src/aiswarm/db.py` (migration: `scorer_points`)
- Modify: `src/aiswarm/scorer.py` (`points`)
- Modify: `src/aiswarm/runner.py` (`scorer` param, `_settle`, `_accept`, `_reject`, `_close(before=...)`, `run()` wiring)
- Modify: `src/aiswarm/report.py` (`## Scorer` section in `answer.md`)
- Modify: `tests/conftest.py` (`runner_factory` passes `scorer`)
- Test: `tests/test_scorer_run.py`

**Interfaces:**
- Consumes: Task 1 `score`, `ScoreResult`, `Ruling`; Task 2 `spent_calls` (via `stop_decision`).
- Produces:
  - `Runner(..., scorer: ModelClient | None = None)`
  - Board entries by `system`: `scorer_accepted` `{"call_id": id}` refs `[id]`; `scorer_rejected` `{"call_id": id, "feedback": str}` refs `[id]`
  - Workspace file `scorer_feedback.md` (author `system`), one version per rejection
  - Event `scorer_ruled` `{call, accepted, feedback, model}`; pause detail `scorer: <detail>`
  - Table `scorer_points (id, run_id, agent_id, call_id, contribution, created_at)`; `points(conn) -> dict[str, int]`

- [ ] **Step 1: Let the test factory pass a Scorer**

In `tests/conftest.py`, `runner_factory`:

```python
@pytest.fixture
def runner_factory(tmp_path):
    def make(store, conn, clients, rng=None, scorer=None, **settings):
        return Runner(settings=RunSettings(**settings), conn=conn, store=store, runs_dir=tmp_path / "runs",
                      clients=clients, rng=rng or random.Random(0), scorer=scorer)

    return make
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_scorer_run.py`:

```python
import json

from aiswarm.board import Board, Entry
from aiswarm.events import read_events
from aiswarm.models.fake import FakeModel

AGENTS = ["a", "b", "c"]
ACCEPT = json.dumps({"accepted": True, "feedback": ""})
REJECT = json.dumps({"accepted": False, "feedback": "It describes a poll, not a colour."})


def propose(view):
    return {"type": "propose", "answer_summary": "Blue", "method": "Gut"}


def clients_for(scripted, find, second_call=False):
    """a calls in round 2 (and, with second_call, again in round 4); b critiques and c supports the newest call."""
    def call(v):
        return {"type": "call_consensus", "synthesis": "Blue it is", "refs": find(v, "propose"),
                "credits": [{"agent_id": "a", "contribution": "the call"}, {"agent_id": "b", "contribution": "framing"}]}

    def critique(v):
        return {"type": "critique", "target": find(v, "call_consensus")[-1], "about": "answer", "points": "Hm."}

    def support(v):
        return {"type": "vote", "target": find(v, "call_consensus")[-1], "stance": "support", "reason": "Fine."}

    a = {1: propose, 2: call, **({4: call} if second_call else {})}
    b = {1: propose, 3: critique, **({5: critique} if second_call else {})}
    c = {1: propose, 3: support, **({5: support} if second_call else {})}
    return {"a": scripted(a), "b": scripted(b), "c": scripted(c)}


def points_rows(conn, run_id):
    return sorted(tuple(r) for r in conn.execute(
        "SELECT agent_id, call_id, contribution FROM scorer_points WHERE run_id = ?", (run_id,)))


def board_types(conn, run_id):
    return [r["type"] for r in conn.execute("SELECT type FROM entries WHERE run_id = ? ORDER BY id", (run_id,))]


async def test_an_accepted_call_closes_the_run_and_awards_points(tmp_path, community, scripted, find, runner_factory):
    store, conn = community
    scorer = FakeModel([ACCEPT])
    runner = runner_factory(store, conn, clients_for(scripted, find), scorer=scorer)
    run_id = runner.start("Pick a colour", AGENTS)
    result = await runner.run(run_id)
    assert (result.status, result.reason) == ("closed", "consensus")
    call_id = result.call_id
    assert points_rows(conn, run_id) == [("a", call_id, "the call"), ("b", call_id, "framing")]
    assert board_types(conn, run_id)[-2:] == ["scorer_accepted", "run_closed"]
    assert scorer.calls[0][1][0]["content"] == "## Goal\n\nPick a colour\n\n## Synthesis\n\nBlue it is\n\nReply with JSON only."
    events = read_events(tmp_path / "runs" / run_id / "events.jsonl")
    ruled = next(e for e in events if e["event"] == "scorer_ruled")
    assert (ruled["call"], ruled["accepted"], ruled["model"]) == (call_id, True, "fake")
    answer = (tmp_path / "runs" / run_id / "report" / "answer.md").read_text()
    assert f"## Scorer\n\n- call #{call_id}: accepted" in answer


async def test_a_rejected_call_is_spent_and_the_run_ends_on_budget(tmp_path, community, scripted, find,
                                                                    runner_factory):
    store, conn = community
    scorer = FakeModel([REJECT])
    runner = runner_factory(store, conn, clients_for(scripted, find), scorer=scorer, max_rounds=4)
    run_id = runner.start("Pick a colour", AGENTS)
    result = await runner.run(run_id)
    assert (result.status, result.reason) == ("closed", "budget")
    assert len(scorer.calls) == 1  # the spent call is never submitted again
    assert points_rows(conn, run_id) == []
    rejected = conn.execute("SELECT round, payload FROM entries WHERE run_id = ? AND type = 'scorer_rejected'",
                            (run_id,)).fetchone()
    assert rejected["round"] == 3 and json.loads(rejected["payload"])["feedback"] == "It describes a poll, not a colour."
    feedback = (tmp_path / "runs" / run_id / "workspace" / "scorer_feedback.md").read_text()
    assert "It describes a poll, not a colour." in feedback
    assert conn.execute("SELECT author FROM files WHERE run_id = ? AND path = 'scorer_feedback.md'",
                        (run_id,)).fetchone()[0] == "system"
    assert "rejected — It describes a poll, not a colour." in \
        (tmp_path / "runs" / run_id / "report" / "answer.md").read_text()


async def test_a_rejection_in_the_last_round_closes_on_budget(tmp_path, community, scripted, find, runner_factory):
    store, conn = community
    runner = runner_factory(store, conn, clients_for(scripted, find), scorer=FakeModel([REJECT]), max_rounds=3)
    run_id = runner.start("Pick a colour", AGENTS)
    result = await runner.run(run_id)
    assert (result.status, result.reason) == ("closed", "budget")
    assert board_types(conn, run_id)[-2:] == ["scorer_rejected", "run_closed"]


async def test_a_later_call_can_be_accepted(tmp_path, community, scripted, find, runner_factory):
    store, conn = community
    scorer = FakeModel([REJECT, ACCEPT])
    runner = runner_factory(store, conn, clients_for(scripted, find, second_call=True), scorer=scorer, max_rounds=8)
    run_id = runner.start("Pick a colour", AGENTS)
    result = await runner.run(run_id)
    calls = [r["id"] for r in conn.execute(
        "SELECT id FROM entries WHERE run_id = ? AND type = 'call_consensus' ORDER BY id", (run_id,))]
    assert (result.status, result.reason, result.call_id) == ("closed", "consensus", calls[1])
    assert {row[1] for row in points_rows(conn, run_id)} == {calls[1]}


async def test_no_ruling_pauses_and_resume_asks_again_before_any_round(tmp_path, community, scripted, find,
                                                                       runner_factory):
    store, conn = community
    clients = clients_for(scripted, find)
    paused = await runner_factory(store, conn, clients, scorer=FakeModel([ACCEPT], fail_times=1)).run(
        runner_factory(store, conn, clients).start("Pick a colour", AGENTS))
    assert paused.status == "paused"
    run_id = paused.run_id
    events = read_events(tmp_path / "runs" / run_id / "events.jsonl")
    assert events[-1]["event"] == "run_paused" and events[-1]["detail"].startswith("scorer: model error")
    resumed = await runner_factory(store, conn, clients, scorer=FakeModel([ACCEPT])).run(run_id)
    assert (resumed.status, resumed.reason) == ("closed", "consensus")
    after = read_events(tmp_path / "runs" / run_id / "events.jsonl")[len(events):]
    assert not any(e["event"] == "round_started" for e in after)
    assert len(points_rows(conn, run_id)) == 2  # awarded once


async def test_an_invalid_ruling_twice_pauses(tmp_path, community, scripted, find, runner_factory):
    store, conn = community
    runner = runner_factory(store, conn, clients_for(scripted, find), scorer=FakeModel(["nope", "nope"]))
    result = await runner.run(runner.start("Pick a colour", AGENTS))
    assert result.status == "paused"
    assert conn.execute("SELECT status FROM runs WHERE id = ?", (result.run_id,)).fetchone()[0] == "paused"


def test_points_go_once_to_each_credited_member(tmp_path, community, runner_factory, make_run):
    store, conn = community
    make_run(conn, "r1", AGENTS)
    board = Board(conn, "r1")
    call = Entry(9, "r1", 3, "a", "call_consensus", {"synthesis": "S", "credits": [
        {"agent_id": "a", "contribution": "me"}, {"agent_id": "a", "contribution": "me again"},
        {"agent_id": "zz", "contribution": "ghost"}, {"agent_id": "c", "contribution": "data"}]}, [], "all", "t")
    runner_factory(store, conn, {})._accept(board, 3, call, AGENTS)
    assert points_rows(conn, "r1") == [("a", 9, "me"), ("c", 9, "data")]
    from aiswarm.scorer import points
    assert points(conn) == {"a": 1, "c": 1}
```

- [ ] **Step 3: Run them to verify they fail**

Run: `uv run pytest tests/test_scorer_run.py -q`
Expected: FAIL — `TypeError: Runner.__init__() got an unexpected keyword argument 'scorer'` (and `no such table: scorer_points`).

- [ ] **Step 4: Add the table**

Append a migration to `MIGRATIONS` in `db.py`:

```python
    """
    CREATE TABLE scorer_points (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL REFERENCES runs(id),
        agent_id TEXT NOT NULL,
        call_id INTEGER NOT NULL,
        contribution TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE INDEX scorer_points_by_agent ON scorer_points (agent_id);
    """,
```

Append to `scorer.py`:

```python
def points(conn: sqlite3.Connection) -> dict[str, int]:
    """Lifetime Scorer points per agent, counted from the rows (never stored as a total)."""
    return {r[0]: r[1] for r in conn.execute("SELECT agent_id, COUNT(*) FROM scorer_points GROUP BY agent_id")}
```

(add `import sqlite3` at the top of `scorer.py`).

- [ ] **Step 5: Implement the Runner changes**

In `runner.py`, import `from aiswarm.scorer import score`, and add the parameter:

```python
        rng: Any = None, embedder: Embedder | None = None, library_top_k: int = 3,
        scorer: ModelClient | None = None,
    ):
        ...
        self.scorer = scorer  # None: a passing call closes the run unscored (library and tests); the CLI always sets one
```

In `run()`, just before `for rnd in range(...)`:

```python
        settled = await self._settle(run_id, goal, agent_ids, board, ws, events, completed,
                                     final=completed >= self.settings.max_rounds)
        if settled is not None:  # e.g. resumed after a pause at the Scorer: rule before any new round
            return settled
```

Inside the loop, replace

```python
            decision = stop_decision(board.entries(), agent_ids, rnd, self.settings)
            if decision:
                return self._close(run_id, goal, board, events, rnd, decision)
```

with

```python
            settled = await self._settle(run_id, goal, agent_ids, board, ws, events, rnd, final=False)
            if settled is not None:
                return settled
```

and replace the two lines after the loop with:

```python
        settled = await self._settle(run_id, goal, agent_ids, board, ws, events, completed, final=True)
        assert settled is not None
        return settled
```

Add the methods (next to `_close`):

```python
    async def _settle(self, run_id: str, goal: str, agent_ids: list[str], board: Board, ws: Workspace,
                      events: EventLog, rnd: int, *, final: bool) -> RunResult | None:
        """The stop rules after a round. A call that passes goes to the Scorer before the run may close."""
        while True:
            decision = stop_decision(board.entries(), agent_ids, rnd, self.settings)
            if decision is None or decision.reason != "consensus" or self.scorer is None:
                break
            call = next(e for e in board.entries() if e.id == decision.call_id)
            result = await score(self.scorer, goal, str(call.payload.get("synthesis", "")))
            if result.ruling is None:  # nothing is guessed: pause, and the resumed run asks again
                self.conn.execute("UPDATE runs SET status = 'paused' WHERE id = ?", (run_id,))
                events.emit("run_paused", round=rnd, detail=f"scorer: {result.detail}")
                return RunResult(run_id, "paused")
            ruling = result.ruling
            events.emit("scorer_ruled", call=call.id, accepted=ruling.accepted, feedback=ruling.feedback,
                        model=result.model)
            if ruling.accepted:
                return self._close(run_id, goal, board, events, rnd, decision,
                                   before=lambda: self._accept(board, rnd, call, agent_ids))
            self._reject(run_id, board, ws, rnd, call, ruling.feedback)
        if decision is not None:
            return self._close(run_id, goal, board, events, rnd, decision)
        return self._close(run_id, goal, board, events, rnd, StopDecision("budget")) if final else None

    def _accept(self, board: Board, rnd: int, call: Entry, agent_ids: Sequence[str]) -> None:
        """One point per member the caller credited (the caller too, if it credited itself); no one else."""
        board.append(rnd, SYSTEM, "scorer_accepted", {"call_id": call.id}, refs=[call.id])
        credited: dict[str, str] = {}
        for credit in call.payload.get("credits", []):
            agent = credit.get("agent_id")
            if agent in agent_ids and agent not in credited:
                credited[agent] = str(credit.get("contribution", ""))
        self.conn.executemany(
            "INSERT INTO scorer_points (run_id, agent_id, call_id, contribution, created_at) VALUES (?, ?, ?, ?, ?)",
            [(board.run_id, agent, call.id, text, now_iso()) for agent, text in credited.items()])

    def _reject(self, run_id: str, board: Board, ws: Workspace, rnd: int, call: Entry, feedback: str) -> None:
        """The call is spent; its feedback goes on the board and into the workspace before the next round."""
        path = "scorer_feedback.md"
        with transaction(self.conn):
            ws.write(path, ws.current_version(path), f"# Scorer feedback on call #{call.id}\n\n{feedback.strip()}\n",
                     author=SYSTEM, note=f"Scorer feedback on call #{call.id}")
            board.append(rnd, SYSTEM, "scorer_rejected", {"call_id": call.id, "feedback": feedback.strip()},
                         refs=[call.id])
            # the round's checkpoint now includes the feedback, so a resumed run keeps it
            self.conn.execute("UPDATE rounds SET workspace_commit = ? WHERE run_id = ? AND round = ?",
                              (ws.head(), run_id, rnd))
```

Change `_close` to accept `before` and call it inside its transaction:

```python
    def _close(
        self, run_id: str, goal: str, board: Board, events: EventLog, rnd: int, decision: StopDecision,
        before: Callable[[], None] | None = None,
    ) -> RunResult:
        with transaction(self.conn):
            if before is not None:
                before()  # e.g. the Scorer's points: awarded in the same transaction as the close, never twice
            board.append(rnd, SYSTEM, "run_closed", {"reason": decision.reason, "call_id": decision.call_id})
```

- [ ] **Step 6: Report the ruling in `answer.md`**

In `report.py`'s `write_report`, after `answer = ["# Answer", ..., f"**Ended:** {ended}", ""]`:

```python
    rulings = [e for e in entries if e.type in ("scorer_accepted", "scorer_rejected")]
    if rulings:
        answer += ["## Scorer", ""] + [
            f"- call #{e.payload.get('call_id')}: "
            + ("accepted" if e.type == "scorer_accepted" else f"rejected — {e.payload.get('feedback', '')}")
            for e in rulings] + [""]
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/test_scorer_run.py tests/test_runner.py tests/test_process2.py tests/test_process1_golden.py -q`
Expected: PASS (`scorer=None` everywhere else keeps the golden recording unchanged).

- [ ] **Step 8: Commit**

```bash
git add src/aiswarm/db.py src/aiswarm/scorer.py src/aiswarm/runner.py src/aiswarm/report.py tests/conftest.py tests/test_scorer_run.py
git commit -m "feat: passing calls go to the Scorer; acceptance closes the run and awards points, rejection posts feedback"
```

---

### Task 4: What the agents are told and see

**Files:**
- Modify: `src/aiswarm/prompts/agent_system.md`, `src/aiswarm/prompts/process2_agent_system.md`
- Modify: `src/aiswarm/view.py` (`ViewInputs.points`, `collect_inputs`, member lines, open calls, rejected calls)
- Modify: `src/aiswarm/postrun/cycle.py` (journal packet: the Scorer's rulings and points this run)
- Modify: `src/aiswarm/scorer.py` (`run_summary`)
- Modify: `tests/golden/process1_run.json` (regenerated deliberately)
- Test: `tests/test_view.py`, `tests/test_agent.py`, `tests/test_postrun.py`

**Interfaces:**
- Consumes: Task 2 `spent_calls`; Task 3 `points`, board entry shapes, `scorer_points` table.
- Produces: `ViewInputs.points: Mapping[str, int]`; `run_summary(conn, run_id: str, entries: Sequence[Entry], who: Who) -> str` ("" when the Scorer made no ruling).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_agent.py`:

```python
@pytest.mark.parametrize("template", ["agent_system.md", "process2_agent_system.md"])
def test_system_prompts_explain_the_scorer(template):
    text = system_prompt("Ash (a)", "Calm.", 2, template)
    assert ("An outside Scorer reviews every call for consensus that passes. It accepts a call only if its synthesis "
            "gives the concrete answer the goal asks for; it does not judge whether the answer is good.") in text
    assert "every member credited in it earns a Scorer point" in text
    assert "and the Scorer accepts it" in text
```

Append to `tests/test_view.py`:

```python
def test_member_lines_show_scorer_points(tmp_path, community, make_run):
    store, conn, board, ws = setup(tmp_path, community, make_run)
    conn.executemany("INSERT INTO scorer_points (run_id, agent_id, call_id, contribution, created_at) "
                     "VALUES ('r1', ?, 9, 'x', 't')", [("b",), ("b",), ("c",)])
    view = view_for("a", 2, store, board, ws)
    assert "- Bea (b) — Bea headline · no reviews yet · 2 Scorer points" in view
    assert "- Cyd (c) — Cyd headline · no reviews yet · 1 Scorer point\n" in view
    assert "- Alpha (a) — Alpha headline · no reviews yet · 0 Scorer points" in view


def test_rejected_calls_leave_the_open_calls_and_show_their_feedback(tmp_path, community, make_run):
    store, conn, board, ws = setup(tmp_path, community, make_run)
    spent = board.append(2, "b", "call_consensus", {"synthesis": "Poll first", "refs": [], "credits": []})
    board.append(2, SYSTEM, "scorer_rejected", {"call_id": spent.id, "feedback": "No colour named."}, refs=[spent.id])
    open_call = board.append(3, "c", "call_consensus", {"synthesis": "Blue", "refs": [], "credits": []})
    view = view_for("a", 3, store, board, ws)
    opens = view.split("# Open calls for consensus\n")[1].split("\n\n")[0]
    assert f"#{open_call.id} by Cyd (c)" in opens and f"#{spent.id} " not in opens
    assert f"# Calls the Scorer rejected\n- #{spent.id} by Bea (b): No colour named." in view
```

Append to `tests/test_postrun.py`:

```python
async def test_the_journal_step_is_told_the_scorers_rulings_and_points(tmp_path, community, make_run, postrun_reply):
    store, conn = community
    run_id = finished_run(conn, tmp_path, make_run, reason="consensus")
    board = Board(conn, run_id)
    first = board.append(2, "a", "call_consensus", {"synthesis": "Poll", "refs": [], "credits": []})
    board.append(2, SYSTEM, "scorer_rejected", {"call_id": first.id, "feedback": "No colour named."}, refs=[first.id])
    second = board.append(3, "b", "call_consensus", {"synthesis": "Blue", "refs": [], "credits": [
        {"agent_id": "a", "contribution": "data"}]})
    board.append(3, SYSTEM, "scorer_accepted", {"call_id": second.id}, refs=[second.id])
    conn.execute("INSERT INTO scorer_points (run_id, agent_id, call_id, contribution, created_at) "
                 "VALUES (?, 'a', ?, 'data', 't')", (run_id, second.id))
    clients = {a: FakeModel(responder(postrun_reply)) for a in "abc"}
    await cycle(store, conn, tmp_path, clients).run(run_id)
    journal_c = next(m[0]["content"] for _, m, _ in clients["c"].calls if m[0]["content"].startswith("# Post-run: journal"))
    assert (f"## The Scorer this run\n- call #{first.id} by Alpha (a): rejected — No colour named.\n"
            f"- call #{second.id} by Bea (b): accepted\nScorer points earned: Alpha (a) 1") in journal_c


async def test_the_journal_step_says_nothing_about_a_scorer_that_made_no_ruling(tmp_path, community, make_run,
                                                                                postrun_reply):
    store, conn = community
    run_id = finished_run(conn, tmp_path, make_run)
    clients = {a: FakeModel(responder(postrun_reply)) for a in "abc"}
    await cycle(store, conn, tmp_path, clients).run(run_id)
    journal = next(m[0]["content"] for _, m, _ in clients["a"].calls if m[0]["content"].startswith("# Post-run: journal"))
    assert "Scorer" not in journal
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_agent.py tests/test_view.py tests/test_postrun.py -q -k "scorer or rejected"`
Expected: FAIL — the prompt sentences, the points suffix, the rejected-calls section and the journal section are all missing.

- [ ] **Step 3: The system prompts**

In both `agent_system.md` and `process2_agent_system.md`, replace the end-of-work sentence

```
The work ends when a call_consensus has been critiqued at least once and is supported by all members but at most one. Its caller counts as a supporter.
```

with

```
The work ends when a call_consensus has been critiqued at least once, is supported by all members but at most one, and the Scorer accepts it. Its caller counts as a supporter.

An outside Scorer reviews every call for consensus that passes. It accepts a call only if its synthesis gives the concrete answer the goal asks for; it does not judge whether the answer is good. If it rejects a call, its feedback appears on the board and in `scorer_feedback.md`, and work continues. When a call is accepted, every member credited in it earns a Scorer point.
```

(In `process2_agent_system.md` the "After everyone has spoken, members who have not yet voted…" paragraph stays where it is, before this sentence.)

- [ ] **Step 4: The view**

In `view.py`: import `from aiswarm.consensus import call_status, spent_calls` (extend the existing import) and `from aiswarm.scorer import points`. Add to `ViewInputs` (after `think_notes`):

```python
    points: Mapping[str, int] = field(default_factory=dict)  # lifetime Scorer points per member
```

In `collect_inputs`, pass `points=points(board.conn),`.

In `build_view`, replace the calls block with:

```python
    spent = spent_calls(inputs.entries)
    calls = [e for e in inputs.entries if e.type == "call_consensus" and e.id not in spent]
    if calls:
        lines = []
        for call in calls:
            status = call_status(inputs.entries, call.id)
            support = ", ".join(sorted(who(a) for a in status.supporters)) or "none"
            objection = ", ".join(sorted(who(a) for a in status.objectors)) or "none"
            critiqued = "yes" if status.critiqued else "no"
            lines.append(
                f"- #{call.id} by {who(call.author)}: support: {support} · object: {objection} · critiqued: {critiqued}"
            )
        fixed.append("# Open calls for consensus\n" + "\n".join(lines))
    authors = {e.id: e.author for e in inputs.entries}
    rejected = [e for e in inputs.entries if e.type == "scorer_rejected"]
    if rejected:
        fixed.append("# Calls the Scorer rejected\n" + "\n".join(
            f"- #{e.payload.get('call_id')} by {who(authors.get(e.payload.get('call_id'), '?'))}: "
            f"{e.payload.get('feedback', '')}" for e in rejected))
```

In the member loop, after the dimension-score suffix:

```python
        earned = inputs.points.get(agent_id, 0)
        suffix += f" · {earned} Scorer point{'' if earned == 1 else 's'}"
```

- [ ] **Step 5: The journal packet**

Append to `scorer.py` (imports: `from collections.abc import Sequence`, `from aiswarm.board import Entry`, `from aiswarm.view import Who` — `view` imports `scorer`, so import `Who` under `TYPE_CHECKING` to avoid a cycle):

```python
def run_summary(conn: sqlite3.Connection, run_id: str, entries: Sequence["Entry"], who: "Who") -> str:
    """The Scorer's rulings in one run and who earned points, for the post-run journal. Empty if it never ruled."""
    authors = {e.id: e.author for e in entries}
    rulings = [e for e in entries if e.type in ("scorer_accepted", "scorer_rejected")]
    if not rulings:
        return ""
    lines = ["## The Scorer this run"]
    for e in rulings:
        call = e.payload.get("call_id")
        verdict = "accepted" if e.type == "scorer_accepted" else f"rejected — {e.payload.get('feedback', '')}"
        lines.append(f"- call #{call} by {who(authors.get(call, '?'))}: {verdict}")
    earned = conn.execute("SELECT agent_id, COUNT(*) FROM scorer_points WHERE run_id = ? GROUP BY agent_id "
                          "ORDER BY agent_id", (run_id,)).fetchall()
    if earned:
        lines.append("Scorer points earned: " + ", ".join(f"{who(r[0])} {r[1]}" for r in earned))
    return "\n".join(lines)
```

with, at the top of `scorer.py`:

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aiswarm.board import Entry
    from aiswarm.view import Who
```

In `postrun/cycle.py` `_journal`, after the think-notes block and before `render("journal_request.md", ...)`:

```python
        if summary := run_summary(self.conn, ctx.run_id, ctx.entries, ctx.who):
            packet += "\n\n" + summary
```

(import `from aiswarm.scorer import run_summary`). `ctx.entries` holds every entry of the run; `scorer_*` entries are public system entries, so every member may see them.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_agent.py tests/test_view.py tests/test_postrun.py -q`
Expected: PASS

- [ ] **Step 7: Regenerate the Process 1 golden recording and check its diff**

Run: `AISWARM_WRITE_GOLDEN=1 uv run pytest tests/test_process1_golden.py -q`
Then: `git diff tests/golden/process1_run.json | grep '^[-+] ' | grep -cv 'Scorer'`
Expected: `0` — every changed line mentions the Scorer (the system-prompt paragraph and the `· 0 Scorer points` member suffix); entries, events, files and the stop decision are unchanged.

- [ ] **Step 8: Run the whole suite and commit**

Run: `uv run pytest -q`
Expected: all pass.

```bash
git add src/aiswarm/prompts/agent_system.md src/aiswarm/prompts/process2_agent_system.md src/aiswarm/view.py src/aiswarm/scorer.py src/aiswarm/postrun/cycle.py tests/golden/process1_run.json tests/test_agent.py tests/test_view.py tests/test_postrun.py
git commit -m "feat: agents are told about the Scorer, see members' Scorer points and rejected calls, and journal its rulings"
```

---

### Task 5: Configuration, CLI and provenance

**Files:**
- Modify: `src/aiswarm/config.py` (`ScorerSettings`, `Config.scorer`, validator, `DEFAULT_CONFIG`)
- Modify: `src/aiswarm/provenance.py` (`capture`: the Scorer backend is a used backend)
- Modify: `src/aiswarm/cli.py` (`run` requires `[scorer]`, builds the client, passes it to the Runner)
- Modify: `tests/test_cli.py` (fixture answers Scorer requests)
- Test: `tests/test_cli.py`, `tests/test_config.py`

**Interfaces:**
- Consumes: Task 3 `Runner(scorer=...)`.
- Produces: `cfg.scorer.judge: str | None`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_cli.py`'s `project` fixture, at the top of `respond`:

```python
        if system.startswith("You are the Scorer"):
            return json.dumps({"accepted": True, "feedback": ""})
```

Append to `tests/test_cli.py`:

```python
def test_run_refuses_without_a_scorer(project):
    tmp_path, config = project
    make_agents(config, "a", "b")
    config.write_text(config.read_text().replace('\njudge = "claude"\n', "\n"))  # the [observer] line is commented
    result = invoke("run", "Pick a colour", "--no-postrun", "-c", config)
    assert result.exit_code != 0 and "[scorer]" in result.output


def test_runs_record_the_scorer_in_provenance(project):
    import sqlite3

    tmp_path, config = project
    make_agents(config, "a", "b")
    result = invoke("run", "Pick a colour", "--max-rounds", "1", "--no-postrun", "-c", config)
    assert result.exit_code == 0, result.output
    conn = sqlite3.connect(tmp_path / "community" / "community.db")
    cfg_json, prompts = conn.execute("SELECT config, prompts FROM run_provenance WHERE phase = 'start'").fetchone()
    assert json.loads(cfg_json)["scorer"]["judge"] == "claude" and "claude" in json.loads(cfg_json)["models"]
    assert "scorer.md" in json.loads(prompts)
```

Append to `tests/test_config.py`:

```python
def test_scorer_judge_must_name_a_backend():
    import pytest

    from aiswarm.config import Config

    with pytest.raises(ValueError, match="scorer.judge"):
        Config.model_validate({"models": {}, "scorer": {"judge": "nope"}})
    assert Config.model_validate({"models": {}}).scorer.judge is None
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_cli.py tests/test_config.py -q -k "scorer"`
Expected: FAIL — `Config` has no `scorer`, the default config has no `[scorer]` section, and `run` does not refuse.

- [ ] **Step 3: Implement the config**

In `config.py`:

```python
class ScorerSettings(BaseModel):
    judge: str | None = None  # the backend that rules whether a passing call answers the goal; agents see its rulings
```

Add `scorer: ScorerSettings = Field(default_factory=ScorerSettings)` to `Config` (after `observer`), and `("scorer.judge", self.scorer.judge)` to the tuple in `_known_backends`. In `DEFAULT_CONFIG`, after the `[observer]` block:

```toml
[scorer]
# the backend that rules whether a passing call for consensus answers the goal (agents see its rulings)
judge = "claude"
```

- [ ] **Step 4: Implement provenance and the CLI**

In `provenance.capture`, include the Scorer backend among used backends:

```python
    used = set(agent_backends.values()) | {n for n in (cfg.report.narrator, cfg.library.embeddings, cfg.scorer.judge)
                                           if n}
```

In `cli.run`, right after `cfg = _load(config)` and the `max_rounds` override:

```python
    if cfg.scorer.judge is None:
        _fail('no [scorer] configured: add a [scorer] section with judge = "<model backend>" to the config')
    scorer_client = make_client(cfg.models[cfg.scorer.judge], cfg.run.model_retries)
```

and pass `scorer=scorer_client` to `Runner(...)` in `go()`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py tests/test_config.py tests/test_provenance.py -q`
Expected: PASS

- [ ] **Step 6: Run the whole suite and commit**

Run: `uv run pytest -q`
Expected: all pass.

```bash
git add src/aiswarm/config.py src/aiswarm/provenance.py src/aiswarm/cli.py tests/test_cli.py tests/test_config.py
git commit -m "feat: aiswarm run requires a [scorer] backend, uses it for every run, and records it in provenance"
```

After merge (not part of this branch; ask first): add `[scorer]\njudge = "claude-judge"` to `lab/aiswarm.toml`, or `aiswarm run` will refuse to start in the lab.
