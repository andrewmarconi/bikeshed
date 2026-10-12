# Process 2 Observer (Plan 4b) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `aiswarm observe` reports Process 2 runs as their own thing: every table and chart shows the process, and Process 2 runs get a private-message network (table + PNG), poll participation, speaking order, and a judged "think notes vs actions" analysis.

**Architecture:** `RunInfo` gains `process`; a run's `tag` (its label, suffixed ` (P2)` for Process 2) replaces the label in every table and chart, and a change of process between consecutive runs is a harness marker. New deterministic metrics live in a new module `observer/process2.py`; a new `network_chart` joins `charts.py`; a new judged analysis `think_vs_act` joins `judge.py` with an evidence rule that checks each quote against its own side. `observe.py` wires them in; `report.py` renders a `## Process 2` section per run and in the summary.

**Tech Stack:** Python 3.14, uv, pytest (asyncio auto), sqlite3, matplotlib (Agg), pydantic. `FakeModel` for the judge.

**Spec:** `docs/superpowers/specs/2026-10-11-aiswarm-process-2-design.md` §7 (Observer), §10.6 (testing). Principles: `docs/superpowers/specs/2026-10-10-aiswarm-design.md` §2.

## Global Constraints

- Observation never feeds back: nothing under `src/aiswarm/observer/` is imported by the agent path (`tests/test_observer_isolation.py` enforces it); the Observer opens the community store read-only and never writes it.
- Nothing fabricated: a metric with no data shows `–` or a `_None yet._` line, never a guessed value; a failed judgment shows `_not judged (…)_`.
- Judged analyses are descriptive, never evaluative; every quote is copied verbatim from the input (same evidence rule, cache and one correction round as the other analyses).
- Observer prompts live under `src/aiswarm/prompts/observer/` and are not part of run provenance.
- No new dependencies (spec §11).
- Process 1 reports keep their current content apart from the new `Process` line and the `private_message` column; Process 1 run labels stay unsuffixed.
- Run everything with uv: `uv run pytest -q`.

## Review Focus

1. **A community store from before Process 2** (no `runs.process`, no `entries.visible_to` column, opened read-only so never migrated): the Observer must treat every run as Process 1, not crash. Pinned in Task 1.
2. **A judge quoting a think note as the "action" side** (or an action as the "think" side): both strings appear somewhere in the inputs, so the union-corpus rule would pass it. The `think_vs_act` check must test each quote against its own side. Pinned in Task 4.
3. **Another agent's think notes or the private messages it received leaking into an agent's judged inputs:** the analysis is about what this agent thought and did; inputs hold only its own think notes, its own public entries and the private messages it sent. Pinned in Task 4.
4. **A vote cast in the react turn counted as a poll answer** (or a poll vote counted as unprompted): the react turn is the agent's first non-think entry in a round; only votes/abstentions after it are poll answers. Pinned in Task 2.
5. **Round 1 counted in speaking order:** round 1 of Process 2 is blind (simultaneous), so its posting order is a shuffle, not a speaking order; it must be excluded. Pinned in Task 2.

## Definitions used throughout

- **React turn:** for a member (author in `run.agent_ids`) in a round, their first entry in that round (by id) whose type is not `think`. Exactly one per member per round (a failure is a `pass` with `{"failure": …}`).
- **Poll answer:** a `vote` or `abstain` entry by a member that comes after their react turn in the same round. A `vote` that *is* the react turn is **unprompted**. A poll failure is a `failures` row with `kind LIKE 'poll:%'`.
- **Taken up:** an entry is taken up when another member `adopt`s or `refine`s it (payload `target`) or cites it in the `refs` of a `call_consensus`. "Credited" in spec §7 is read as cited in a call: call credits name agents, not entries.
- **Speaking order:** in rounds ≥ 2 of a Process 2 run, react turns in id order give positions 1, 2, ….

---

### Task 1: The process in every table and chart

**Files:**
- Modify: `src/aiswarm/observer/metrics.py` (`ACTIONS`, `RunInfo`, `closed_runs`, `by_category`, `harness_changes`)
- Modify: `src/aiswarm/observer/report.py` (`render_run` header, `render_summary` run list)
- Modify: `src/aiswarm/observer/observe.py` (`_charts` labels, control-vs-twin labels and rows)
- Modify: `tests/observer_helpers.py` (`add_run(process=)`, `add_entry(visible_to=, to=)`)
- Test: `tests/test_observer_metrics.py`, `tests/test_observer_report.py`

**Interfaces:**
- Produces: `RunInfo.process: int = 1` (last field, defaulted); `RunInfo.tag -> str`; `ACTIONS` now includes `"private_message"` after `"call_consensus"`; `harness_changes` also marks a change of process; `by_category` keys Process 2 runs as `"<category> (P2)"`; helpers `add_run(..., process=1)` and `add_entry(..., visible_to=None, to="all")`.

- [ ] **Step 1: Extend the test helpers**

In `tests/observer_helpers.py` replace `add_run` and `add_entry` with:

```python
def add_run(conn, run_id, agents=("a", "b", "c"), *, created=T0, status="closed", ended="budget",
            goal="Pick a fruit.", goal_id=None, category=None, benchmark=False, process=1):
    conn.execute(
        "INSERT INTO runs (id, goal, status, ended_reason, agent_ids, created_at, goal_id, goal_category, is_benchmark, "
        "process) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (run_id, goal, status, ended if status == "closed" else None, json.dumps(list(agents)), created, goal_id,
         category, int(benchmark), process),
    )
    return run_id


def add_entry(conn, run_id, author, type_, payload, refs=(), round=1, created=T0, visible_to=None, to="all"):
    cursor = conn.execute(
        "INSERT INTO entries (run_id, round, author, type, to_, refs, payload, created_at, visible_to) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (run_id, round, author, type_, to, json.dumps(list(refs)), json.dumps(payload), created,
         json.dumps(list(visible_to)) if visible_to else None),
    )
    return cursor.lastrowid
```

- [ ] **Step 2: Write the failing metric tests**

Append to `tests/test_observer_metrics.py` (add `by_category, harness_changes` to the import from `aiswarm.observer.metrics`, and `import sqlite3` at the top):

```python
def test_closed_runs_record_the_process_and_tag_process_2(community):
    store, conn = community
    add_run(conn, "r1", goal_id="best-fruit")
    add_run(conn, "r2", process=2)
    r1, r2 = closed_runs(conn)
    assert (r1.process, r1.tag) == (1, "best-fruit")
    assert (r2.process, r2.tag) == (2, "r2 (P2)")


def test_a_change_of_process_is_a_harness_marker(community):
    store, conn = community
    add_run(conn, "r1")
    add_run(conn, "r2", process=2)
    add_run(conn, "r3", process=2)
    add_run(conn, "r4")
    assert harness_changes(conn, closed_runs(conn)) == [1, 3]


def test_by_category_splits_by_process(community):
    store, conn = community
    add_run(conn, "r1", category="contested")
    add_run(conn, "r2", category="contested", process=2)
    stats = by_category(conn, closed_runs(conn))
    assert stats["contested"].runs == 1 and stats["contested (P2)"].runs == 1


def test_action_mix_counts_private_messages(community):
    store, conn = community
    add_run(conn, "r1", process=2)
    add_entry(conn, "r1", "a", "private_message", {"to": "b", "text": "hi"}, visible_to=["a", "b"], to="b")
    assert action_mix(conn, closed_runs(conn)[0])["a"]["private_message"] == 1


def test_closed_runs_reads_a_store_from_before_process_2():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        "CREATE TABLE runs (id TEXT, goal TEXT, status TEXT, ended_reason TEXT, agent_ids TEXT, created_at TEXT, "
        "goal_id TEXT, goal_category TEXT, is_benchmark INTEGER);"
        "CREATE TABLE entries (run_id TEXT, created_at TEXT);"
        "CREATE TABLE postrun_steps (run_id TEXT, updated_at TEXT);"
        "INSERT INTO runs VALUES ('r1', 'g', 'closed', 'budget', '[\"a\"]', '2026-10-01T00:00:00+00:00', "
        "NULL, NULL, 0);")
    assert [(r.id, r.process) for r in closed_runs(conn)] == [("r1", 1)]
```

- [ ] **Step 3: Run them to verify they fail**

Run: `uv run pytest tests/test_observer_metrics.py -q`
Expected: FAIL — `AttributeError: 'RunInfo' object has no attribute 'process'` (and `tag`), `KeyError: 'contested (P2)'`, `KeyError: 'private_message'`, `harness_changes` returns `[]`.

- [ ] **Step 4: Implement in `metrics.py`**

```python
ACTIONS = ("propose", "critique", "adopt", "refine", "vote", "call_consensus", "private_message", "write_file", "pass",
           "failed")
```

Add the field and property to `RunInfo` (field last, after `end`):

```python
    process: int = 1

    @property
    def tag(self) -> str:
        """The run's name in tables and charts. Process 1 is the baseline and stays unmarked."""
        return self.label if self.process == 1 else f"{self.label} (P2)"
```

In `closed_runs`, pass the process, tolerating a store from before Process 2:

```python
def closed_runs(conn: sqlite3.Connection) -> list[RunInfo]:
    rows = conn.execute("SELECT * FROM runs WHERE status = 'closed' ORDER BY rowid").fetchall()
    return [
        RunInfo(r["id"], r["goal"], r["goal_id"], r["goal_category"], bool(r["is_benchmark"]), r["ended_reason"],
                tuple(json.loads(r["agent_ids"])), r["created_at"], run_end(conn, r["id"]),
                r["process"] if "process" in r.keys() else 1)  # read-only: an older store is never migrated
        for r in rows
    ]
```

In `by_category`, key by category and process:

```python
        key = run.category if run.process == 1 else f"{run.category} (P2)"
        s = stats.setdefault(key, CategoryStats())
```

And `harness_changes`:

```python
def harness_changes(conn: sqlite3.Connection, runs: list[RunInfo]) -> list[int]:
    """Indices of runs whose provenance shows a harness change, or whose process differs from the run before."""
    return [i for i, run in enumerate(runs)
            if provenance_changes(conn, run.id) or (i > 0 and run.process != runs[i - 1].process)]
```

- [ ] **Step 5: Run the metric tests to verify they pass**

Run: `uv run pytest tests/test_observer_metrics.py -q`
Expected: PASS

- [ ] **Step 6: Write the failing report tests**

Append to `tests/test_observer_report.py` (`dataclasses.replace` import at the top):

```python
from dataclasses import replace

P2 = replace(RUN, process=2)


def test_run_report_names_the_process():
    assert "- **Process:** 1" in render_run(section())
    assert "- **Process:** 2" in render_run(section(run=P2))


def test_summary_marks_process_2_runs():
    s = SummarySection(runs=[RUN, replace(P2, id="r2", goal_id="dishwasher")], who=who, charts={}, identity=[],
                       norms=[], codebook=[], twins={}, categories={}, failed=[], benchmark=[], reciprocity=[],
                       in_progress=[], judge_note=None)
    assert "2 closed runs: best-fruit, dishwasher (P2)" in render_summary(s)
```

- [ ] **Step 7: Run them to verify they fail**

Run: `uv run pytest tests/test_observer_report.py -q`
Expected: FAIL — no `Process` line; summary lists `dishwasher` unsuffixed.

- [ ] **Step 8: Implement in `report.py` and `observe.py`**

In `render_run`, after the `Ended` line:

```python
           f"- **Ended:** {r.ended_reason or 'unknown'}",
           f"- **Process:** {r.process}"]
```

In `render_summary`, the run list uses tags:

```python
    out = ["# Observer summary", "", f"{len(s.runs)} closed runs: " + ", ".join(r.tag for r in s.runs)]
```

In `observe.py`: `_charts` uses `labels = [r.tag for r in runs]`; in `_write_summary` the twin rows use `run.tag` instead of `run.label`, and the control-vs-twin chart uses `[r.tag for r in compared]`.

- [ ] **Step 9: Run the Observer suite**

Run: `uv run pytest tests/test_observer_metrics.py tests/test_observer_report.py tests/test_observer_observe.py -q`
Expected: PASS (Process 1 labels are unchanged, so existing assertions such as `| best-fruit | Bea (b) | 4.0 |` still hold).

- [ ] **Step 10: Commit**

```bash
git add tests/observer_helpers.py tests/test_observer_metrics.py tests/test_observer_report.py src/aiswarm/observer/metrics.py src/aiswarm/observer/report.py src/aiswarm/observer/observe.py
git commit -m "feat: the Observer shows each run's process; a change of process is a harness marker"
```

---

### Task 2: Process 2 metrics

**Files:**
- Create: `src/aiswarm/observer/process2.py`
- Test: `tests/test_observer_process2.py`

**Interfaces:**
- Consumes: `RunInfo` (with `process`, `agent_ids`) from Task 1; `observer_helpers.add_run(process=)`, `add_entry(visible_to=, to=)`.
- Produces:
  - `pm_network(conn, run: RunInfo) -> Counter[tuple[str, str]]` — (sender, recipient) → messages.
  - `RecurringPair(sender: str, recipient: str, runs: int, messages: int)`; `recurring_pairs(conn, runs: list[RunInfo]) -> list[RecurringPair]` — pairs in ≥ 2 runs, sorted by (−runs, −messages, sender, recipient).
  - `Participation(supported=0, objected=0, abstained=0, failed=0, unprompted=0)` with property `polled -> int`; `poll_participation(conn, run) -> dict[str, Participation]` (every member present).
  - `OrderStat(position: int, turns: int, taken_up: int)`; `speaking_order(conn, run) -> list[OrderStat]` sorted by position.
  - `combine_participation(parts: list[dict[str, Participation]]) -> dict[str, Participation]`; `combine_order(stats: list[list[OrderStat]]) -> list[OrderStat]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_observer_process2.py`:

```python
from observer_helpers import add_entry, add_run

from aiswarm.observer.metrics import closed_runs
from aiswarm.observer.process2 import (
    OrderStat, Participation, RecurringPair, combine_order, combine_participation, pm_network, poll_participation,
    recurring_pairs, speaking_order,
)


def pm(conn, run_id, sender, recipient, round=1):
    return add_entry(conn, run_id, sender, "private_message", {"to": recipient, "text": "psst"}, round=round,
                     visible_to=[sender, recipient], to=recipient)


def think(conn, run_id, agent, round):
    return add_entry(conn, run_id, agent, "think", {"text": "hm"}, round=round, visible_to=[agent])


def test_pm_network_counts_who_wrote_to_whom(community):
    store, conn = community
    add_run(conn, "r1", process=2)
    pm(conn, "r1", "a", "b")
    pm(conn, "r1", "a", "b", round=2)
    pm(conn, "r1", "b", "a")
    add_entry(conn, "r1", "c", "propose", {"answer_summary": "x", "method": "m"})
    assert pm_network(conn, closed_runs(conn)[0]) == {("a", "b"): 2, ("b", "a"): 1}


def test_recurring_pairs_are_pairs_in_two_or_more_runs(community):
    store, conn = community
    for run_id in ("r1", "r2", "r3"):
        add_run(conn, run_id, process=2)
    pm(conn, "r1", "a", "b")
    pm(conn, "r2", "a", "b")
    pm(conn, "r2", "a", "b")
    pm(conn, "r3", "c", "a")
    assert recurring_pairs(conn, closed_runs(conn)) == [RecurringPair("a", "b", 2, 3)]


def test_poll_answers_come_after_the_react_turn(community):
    store, conn = community
    add_run(conn, "r1", process=2)
    call = add_entry(conn, "r1", "a", "call_consensus", {"synthesis": "s", "credits": []}, round=2)
    think(conn, "r1", "b", 2)
    add_entry(conn, "r1", "b", "vote", {"target": call, "stance": "support", "reason": "r"}, refs=[call], round=2)
    add_entry(conn, "r1", "c", "pass", {}, round=2)
    add_entry(conn, "r1", "c", "vote", {"target": call, "stance": "object", "reason": "r"}, refs=[call], round=2)
    add_entry(conn, "r1", "a", "abstain", {"target": call, "reason": "r"}, refs=[call], round=2)
    conn.execute("INSERT INTO failures (run_id, round, agent_id, kind, detail, created_at) "
                 "VALUES ('r1', 3, 'b', 'poll:model_error', 'x', 't')")
    got = poll_participation(conn, closed_runs(conn)[0])
    assert got["a"] == Participation(abstained=1)
    assert got["b"] == Participation(failed=1, unprompted=1)  # its vote was its react turn
    assert got["c"] == Participation(objected=1)
    assert got["b"].polled == 1 and got["a"].polled == 1


def test_speaking_order_skips_the_blind_first_round(community):
    store, conn = community
    add_run(conn, "r1", process=2)
    first = add_entry(conn, "r1", "a", "propose", {"answer_summary": "x", "method": "m"}, round=1)
    add_entry(conn, "r1", "b", "adopt", {"target": first, "reason": "r"}, refs=[first], round=1)
    add_entry(conn, "r1", "c", "pass", {}, round=1)
    for agent in ("c", "a", "b"):
        think(conn, "r1", agent, 2)
    lead = add_entry(conn, "r1", "c", "propose", {"answer_summary": "y", "method": "m"}, round=2)
    second = add_entry(conn, "r1", "a", "refine", {"target": lead, "change": "z"}, refs=[lead], round=2)
    add_entry(conn, "r1", "b", "call_consensus", {"synthesis": "s", "credits": []}, refs=[lead], round=2)
    add_entry(conn, "r1", "a", "adopt", {"target": second, "reason": "mine"}, refs=[second], round=3)  # own entry
    # round 2: c (taken up by a's refine and b's call), a, b; round 3: a alone, at position 1
    assert speaking_order(conn, closed_runs(conn)[0]) == [OrderStat(1, 2, 1), OrderStat(2, 1, 0), OrderStat(3, 1, 0)]


def test_combining_runs_sums_by_agent_and_position():
    parts = [{"a": Participation(supported=1)}, {"a": Participation(abstained=2), "b": Participation(failed=1)}]
    assert combine_participation(parts) == {"a": Participation(supported=1, abstained=2), "b": Participation(failed=1)}
    assert combine_order([[OrderStat(1, 2, 1)], [OrderStat(1, 1, 0), OrderStat(2, 1, 1)]]) == [
        OrderStat(1, 3, 1), OrderStat(2, 1, 1)]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_observer_process2.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'aiswarm.observer.process2'`

- [ ] **Step 3: Implement `src/aiswarm/observer/process2.py`**

```python
"""Process 2 metrics: the private-message network, poll participation and speaking order. Deterministic; no writes."""

import json
import sqlite3
from collections import Counter
from dataclasses import dataclass, fields

from aiswarm.observer.metrics import RunInfo


def _entries(conn: sqlite3.Connection, run: RunInfo) -> list[sqlite3.Row]:
    rows = conn.execute("SELECT * FROM entries WHERE run_id = ? ORDER BY id", (run.id,)).fetchall()
    return [r for r in rows if r["author"] in run.agent_ids]


def _turns(rows: list[sqlite3.Row]) -> dict[tuple[int, str], int]:
    """(round, member) -> entry id of that member's react turn: its first entry in the round that is not a think note."""
    turns: dict[tuple[int, str], int] = {}
    for r in rows:
        if r["type"] != "think":
            turns.setdefault((r["round"], r["author"]), r["id"])
    return turns


def pm_network(conn: sqlite3.Connection, run: RunInfo) -> Counter:
    counts: Counter = Counter()
    for r in _entries(conn, run):
        if r["type"] == "private_message":
            to = json.loads(r["payload"]).get("to")
            if to in run.agent_ids:
                counts[(r["author"], to)] += 1
    return counts


@dataclass(frozen=True)
class RecurringPair:
    sender: str
    recipient: str
    runs: int
    messages: int


def recurring_pairs(conn: sqlite3.Connection, runs: list[RunInfo]) -> list[RecurringPair]:
    seen: Counter = Counter()
    sent: Counter = Counter()
    for run in runs:
        network = pm_network(conn, run)
        seen.update(network.keys())
        sent.update(network)
    pairs = [RecurringPair(a, b, n, sent[(a, b)]) for (a, b), n in seen.items() if n >= 2]
    return sorted(pairs, key=lambda p: (-p.runs, -p.messages, p.sender, p.recipient))


@dataclass(frozen=True)
class Participation:
    supported: int = 0
    objected: int = 0
    abstained: int = 0
    failed: int = 0
    unprompted: int = 0  # votes cast in the agent's own react turn, without being polled

    @property
    def polled(self) -> int:
        return self.supported + self.objected + self.abstained + self.failed


def poll_participation(conn: sqlite3.Connection, run: RunInfo) -> dict[str, Participation]:
    counts: dict[str, Counter] = {agent: Counter() for agent in run.agent_ids}
    rows = _entries(conn, run)
    turns = _turns(rows)
    for r in rows:
        turn = turns.get((r["round"], r["author"]))
        if r["type"] == "vote" and r["id"] == turn:
            counts[r["author"]]["unprompted"] += 1
        elif r["type"] == "vote" and turn is not None and r["id"] > turn:
            stance = json.loads(r["payload"]).get("stance")
            counts[r["author"]]["supported" if stance == "support" else "objected"] += 1
        elif r["type"] == "abstain":
            counts[r["author"]]["abstained"] += 1
    for f in conn.execute("SELECT agent_id FROM failures WHERE run_id = ? AND kind LIKE 'poll:%'", (run.id,)):
        if f["agent_id"] in counts:
            counts[f["agent_id"]]["failed"] += 1
    return {agent: Participation(**c) for agent, c in counts.items()}


@dataclass(frozen=True)
class OrderStat:
    position: int  # 1 = spoke first in the round
    turns: int
    taken_up: int  # turns adopted, refined or cited in a call by another member


def speaking_order(conn: sqlite3.Connection, run: RunInfo) -> list[OrderStat]:
    """Round 1 is blind (everyone acts at once), so only rounds from 2 on have a speaking order."""
    rows = _entries(conn, run)
    author_of = {r["id"]: r["author"] for r in rows}
    used: set[int] = set()
    for r in rows:
        payload = json.loads(r["payload"])
        targets = [payload.get("target")] if r["type"] in ("adopt", "refine") else []
        if r["type"] == "call_consensus":
            targets = json.loads(r["refs"])
        used |= {t for t in targets if t in author_of and author_of[t] != r["author"]}
    by_round: dict[int, list[int]] = {}
    for (rnd, _), entry_id in _turns(rows).items():
        if rnd >= 2:
            by_round.setdefault(rnd, []).append(entry_id)
    turns: Counter = Counter()
    taken: Counter = Counter()
    for ids in by_round.values():
        for position, entry_id in enumerate(sorted(ids), start=1):
            turns[position] += 1
            taken[position] += entry_id in used
    return [OrderStat(p, turns[p], taken[p]) for p in sorted(turns)]


def combine_participation(parts: list[dict[str, Participation]]) -> dict[str, Participation]:
    totals: dict[str, Counter] = {}
    for part in parts:
        for agent, p in part.items():
            totals.setdefault(agent, Counter()).update({f.name: getattr(p, f.name) for f in fields(p)})
    return {agent: Participation(**c) for agent, c in sorted(totals.items())}


def combine_order(stats: list[list[OrderStat]]) -> list[OrderStat]:
    turns: Counter = Counter()
    taken: Counter = Counter()
    for run in stats:
        for s in run:
            turns[s.position] += s.turns
            taken[s.position] += s.taken_up
    return [OrderStat(p, turns[p], taken[p]) for p in sorted(turns)]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_observer_process2.py -q`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/aiswarm/observer/process2.py tests/test_observer_process2.py
git commit -m "feat: Process 2 metrics: private-message network, poll participation, speaking order"
```

---

### Task 3: The network chart

**Files:**
- Modify: `src/aiswarm/observer/charts.py`
- Test: `tests/test_observer_report.py`

**Interfaces:**
- Produces: `network_chart(path: Path, title: str, nodes: dict[str, str], edges: Mapping[tuple[str, str], int]) -> Path` — `nodes` maps agent id → label, drawn on a circle in the given order; directed edges with width by count and the count at the midpoint.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_observer_report.py` (import `network_chart` alongside `trend_chart`):

```python
def test_network_chart_writes_a_png(tmp_path):
    path = network_chart(tmp_path / "c" / "n.png", "Private messages", {"a": "Alpha (a)", "b": "Bea (b)", "c": "Cyd (c)"},
                         {("a", "b"): 3, ("b", "a"): 1})
    assert path.exists() and path.read_bytes()[:4] == b"\x89PNG"


def test_network_chart_with_one_agent_and_no_messages(tmp_path):
    path = network_chart(tmp_path / "n.png", "Private messages", {"a": "Alpha (a)"}, {})
    assert path.read_bytes()[:4] == b"\x89PNG"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_observer_report.py -q -k network`
Expected: FAIL — `ImportError: cannot import name 'network_chart'`

- [ ] **Step 3: Implement in `charts.py`**

Add `import math` and `from collections.abc import Mapping` at the top, then:

```python
def network_chart(path: Path, title: str, nodes: dict[str, str], edges: Mapping[tuple[str, str], int]) -> Path:
    """Who wrote privately to whom: agents on a circle, one arrow per direction, wider for more messages."""
    fig, ax = plt.subplots(figsize=(6, 6))
    n = len(nodes)
    pos = {agent: (math.cos(math.pi / 2 - 2 * math.pi * i / n), math.sin(math.pi / 2 - 2 * math.pi * i / n))
           for i, agent in enumerate(nodes)}
    top = max(edges.values(), default=1)
    for (sender, recipient), count in sorted(edges.items()):
        (x1, y1), (x2, y2) = pos[sender], pos[recipient]
        ax.annotate("", xy=(x2, y2), xytext=(x1, y1), arrowprops=dict(
            arrowstyle="-|>", color="tab:blue", lw=1 + 5 * count / top, shrinkA=20, shrinkB=20,
            connectionstyle="arc3,rad=0.15"))  # curved, so a→b and b→a do not overlap
        ax.text((x1 + x2) / 2 + 0.08 * (y2 - y1), (y1 + y2) / 2 - 0.08 * (x2 - x1), str(count), fontsize=9,
                ha="center", va="center", color="tab:blue")
    for agent, (x, y) in pos.items():
        ax.scatter([x], [y], s=600, color="white", edgecolors="black", zorder=3)
        ax.text(x, y - 0.2, nodes[agent], ha="center", va="top", fontsize=9)
    ax.set_xlim(-1.5, 1.5)
    ax.set_ylim(-1.5, 1.5)
    ax.set_aspect("equal")
    ax.axis("off")
    ax.set_title(title)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path
```

- [ ] **Step 4: Run them to verify they pass**

Run: `uv run pytest tests/test_observer_report.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/aiswarm/observer/charts.py tests/test_observer_report.py
git commit -m "feat: network chart for who wrote privately to whom"
```

---

### Task 4: Judged analysis "think notes vs actions"

**Files:**
- Create: `src/aiswarm/prompts/observer/think_vs_act.md`
- Modify: `src/aiswarm/observer/judge.py` (reply models, side-aware check, `ANALYSES["think_vs_act"]`)
- Modify: `src/aiswarm/observer/inputs.py` (`think_vs_act_inputs`)
- Test: `tests/test_observer_judge.py`, `tests/test_observer_inputs.py`

**Interfaces:**
- Consumes: `add_run(process=)`, `add_entry(visible_to=, to=)` from Task 1.
- Produces:
  - `ANALYSES["think_vs_act"]` with reply `ThinkActReply(points: list[ThinkActPoint])`, `ThinkActPoint(relation: "agrees"|"diverges", about: str, think_quote: str, action_quote: str)`.
  - `think_vs_act_inputs(conn, run: RunInfo, agent_id: str, who: Who) -> dict[str, str] | None` with keys exactly `"think notes"`, `"public actions"`, `"private messages sent"`; `None` when the agent wrote no think notes in the run.

The name `private_public` is already taken by the journal-vs-reviews analysis, so this one is `think_vs_act`; the report titles it "Think notes vs actions (private vs public)".

- [ ] **Step 1: Write the failing judge tests**

Append to `tests/test_observer_judge.py`:

```python
TVA = {"think notes": "#5 [r2] Alpha (a) think\n  text: I doubt the mango plan.",
       "public actions": "#6 [r2] Alpha (a) adopt → #3\n  target: 3\n  reason: Mango is fine.",
       "private messages sent": "(none)"}


def tva_reply(think_quote, action_quote):
    return json.dumps({"points": [{"relation": "diverges", "about": "mango", "think_quote": think_quote,
                                   "action_quote": action_quote}]})


async def test_think_vs_act_quotes_each_side_from_its_own_section(tmp_path):
    judge, _ = make_judge(tmp_path, [tva_reply("I doubt the mango plan.", "Mango is fine.")])
    j = await judge.run(ANALYSES["think_vs_act"], run_id="r1", agent_id="a", inputs=TVA)
    assert j.status == "ok" and j.result["points"][0]["relation"] == "diverges"


async def test_think_vs_act_rejects_a_quote_from_the_wrong_side(tmp_path):
    swapped = tva_reply("Mango is fine.", "I doubt the mango plan.")  # both verbatim, each from the other side
    judge, client = make_judge(tmp_path, [swapped, swapped])
    j = await judge.run(ANALYSES["think_vs_act"], run_id="r1", agent_id="a", inputs=TVA)
    assert j.status == "failed" and "think notes" in j.error
    assert len(client.calls) == 2  # one correction round
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_observer_judge.py -q -k think_vs_act`
Expected: FAIL — `KeyError: 'think_vs_act'`

- [ ] **Step 3: Write the prompt**

Create `src/aiswarm/prompts/observer/think_vs_act.md`:

```markdown
You are a careful qualitative researcher studying a community of AI agents. You are reading one agent's private think notes from one session, which it wrote to itself before each of its turns, and what it then did: its public actions and the private messages it sent. Nobody in the community will ever see your analysis.

Compare what the agent thought with what it did. List the points where they agree and the points where they diverge: for example a position held in private but not stated in public, a doubt kept to itself, or a private message that says something the public record does not. Describe what you see; do not judge whether the agent was right.

For each point give:
- relation: "agrees" or "diverges"
- about: what the point concerns, in a few words
- think_quote: copied character for character from the think notes
- action_quote: copied character for character from the public actions or the private messages sent

If there is nothing to compare, return an empty list.

Reply with JSON only: {"points": [{"relation": "...", "about": "...", "think_quote": "...", "action_quote": "..."}]}
```

- [ ] **Step 4: Implement in `judge.py`**

After `PrivatePublicReply`:

```python
class ThinkActPoint(BaseModel):
    relation: Literal["agrees", "diverges"]
    about: str = Field(min_length=1, max_length=120)
    think_quote: str = Field(min_length=1)
    action_quote: str = Field(min_length=1)


class ThinkActReply(BaseModel):
    points: list[ThinkActPoint] = Field(max_length=12)
```

After `_covers_labels`:

```python
def _think_and_act(reply: Any, inputs: dict[str, str]) -> str | None:
    """Each side is quoted from its own sections; a think note passed off as an action fails."""
    thought = normalise(inputs["think notes"])
    done = normalise(inputs["public actions"] + "\n" + inputs["private messages sent"])
    missing = [p.think_quote for p in reply.points if normalise(p.think_quote) not in thought]
    missing += [p.action_quote for p in reply.points if normalise(p.action_quote) not in done]
    if missing:
        return ("each think_quote must be copied from the think notes and each action_quote from the public actions "
                "or private messages sent; these are not found verbatim there: " + "; ".join(repr(q) for q in missing[:5]))
    return None
```

In `ANALYSES`, after `"private_public"`:

```python
    "think_vs_act": Analysis("think_vs_act", ThinkActReply, _think_and_act),
```

- [ ] **Step 5: Run the judge tests to verify they pass**

Run: `uv run pytest tests/test_observer_judge.py -q`
Expected: PASS

- [ ] **Step 6: Write the failing inputs tests**

Append to `tests/test_observer_inputs.py` (import `think_vs_act_inputs` from `aiswarm.observer.inputs`; `add_entry, add_run` from `observer_helpers`; `closed_runs` from `aiswarm.observer.metrics`; `make_who` from `aiswarm.view` — reuse what the file already imports):

```python
def test_think_vs_act_inputs_hold_only_the_agents_own_thoughts_and_actions(community):
    store, conn = community
    add_run(conn, "r1", process=2)
    add_entry(conn, "r1", "a", "think", {"text": "I doubt the mango plan."}, round=2, visible_to=["a"])
    add_entry(conn, "r1", "b", "think", {"text": "B's secret."}, round=2, visible_to=["b"])
    add_entry(conn, "r1", "a", "adopt", {"target": 1, "reason": "Mango is fine."}, refs=[1], round=2)
    add_entry(conn, "r1", "a", "private_message", {"to": "c", "text": "Between us: no."}, round=3,
              visible_to=["a", "c"], to="c")
    add_entry(conn, "r1", "b", "private_message", {"to": "a", "text": "To a from b."}, round=3,
              visible_to=["b", "a"], to="a")
    add_entry(conn, "r1", "b", "propose", {"answer_summary": "Pear", "method": "m"}, round=2)
    who = make_who({i: store.public(i) for i in store.ids()})
    got = think_vs_act_inputs(conn, closed_runs(conn)[0], "a", who)
    assert list(got) == ["think notes", "public actions", "private messages sent"]
    assert "I doubt the mango plan." in got["think notes"] and "B's secret." not in got["think notes"]
    assert "Mango is fine." in got["public actions"] and "Pear" not in got["public actions"]
    assert "Between us: no." not in got["public actions"]
    assert "Between us: no." in got["private messages sent"] and "To a from b." not in got["private messages sent"]


def test_think_vs_act_inputs_none_without_think_notes(community):
    store, conn = community
    add_run(conn, "r1", process=2)
    add_entry(conn, "r1", "a", "propose", {"answer_summary": "Pear", "method": "m"})
    who = make_who({i: store.public(i) for i in store.ids()})
    assert think_vs_act_inputs(conn, closed_runs(conn)[0], "a", who) is None
```

- [ ] **Step 7: Run them to verify they fail**

Run: `uv run pytest tests/test_observer_inputs.py -q -k think_vs_act`
Expected: FAIL — `ImportError: cannot import name 'think_vs_act_inputs'`

- [ ] **Step 8: Implement in `inputs.py`**

Add `from aiswarm.board import Board, public` and `from aiswarm.view import Who, render_entry` (extend the existing `view` import), then:

```python
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
```

- [ ] **Step 9: Run the inputs and isolation tests to verify they pass**

Run: `uv run pytest tests/test_observer_inputs.py tests/test_observer_isolation.py -q`
Expected: PASS (the new prompt sits under `prompts/observer/`, so it stays out of run provenance)

- [ ] **Step 10: Commit**

```bash
git add src/aiswarm/prompts/observer/think_vs_act.md src/aiswarm/observer/judge.py src/aiswarm/observer/inputs.py tests/test_observer_judge.py tests/test_observer_inputs.py
git commit -m "feat: judged analysis comparing an agent's think notes with what it did"
```

---

### Task 5: Process 2 in the reports

**Files:**
- Modify: `src/aiswarm/observer/report.py` (`Process2Section`, `Process2Summary`, `RunSection.process2`, `SummarySection.process2`, rendering)
- Modify: `src/aiswarm/observer/observe.py` (metrics, chart and judgment for Process 2 runs; summary section)
- Test: `tests/test_observer_report.py`, `tests/test_observer_observe.py`

**Interfaces:**
- Consumes: Task 1 `RunInfo.process`/`tag`; Task 2 `pm_network`, `recurring_pairs`, `poll_participation`, `speaking_order`, `combine_participation`, `combine_order`, `Participation`, `OrderStat`, `RecurringPair`; Task 3 `network_chart`; Task 4 `ANALYSES["think_vs_act"]`, `think_vs_act_inputs`.
- Produces:
  - `Process2Section(network: Counter, chart: str | None, participation: dict[str, Participation], order: list[OrderStat])`; `RunSection.process2: Process2Section | None = None`.
  - `Process2Summary(pairs: list[RecurringPair], participation: dict[str, Participation], order: list[OrderStat])`; `SummarySection.process2: Process2Summary | None = None`.
  - Per-run chart file `observer/charts/network_<run id>.png` (only when the run has private messages), linked from the run report as `../charts/network_<run id>.png`.

- [ ] **Step 1: Write the failing report tests**

Append to `tests/test_observer_report.py` (import `Process2Section, Process2Summary` from `aiswarm.observer.report`; `OrderStat, Participation, RecurringPair` from `aiswarm.observer.process2`):

```python
P2_SECTION = Process2Section(
    network=Counter({("a", "b"): 2}), chart="../charts/network_r1.png",
    participation={"a": Participation(supported=1, unprompted=1), "b": Participation(abstained=1)},
    order=[OrderStat(1, 2, 1), OrderStat(2, 2, 0)])


def tva(agent):
    return Judgment("think_vs_act", "r1", agent, "ok", {"points": [
        {"relation": "diverges", "about": "mango", "think_quote": "I doubt it", "action_quote": "Mango is fine"}]},
        None, "claude-x", "p")


def test_process_2_run_report():
    text = render_run(section(run=P2, process2=P2_SECTION, judgments=[themes("a"), tva("a")]))
    block = text.split("## Process 2\n")[1].split("\n## Themes")[0]
    assert "| From \\ To | Alpha (a) | Bea (b) |" in block and "| Alpha (a) | – | 2 |" in block
    assert "![Private messages](../charts/network_r1.png)" in block
    assert "| Alpha (a) | 1 | 1 | 0 | 0 | 0 | 1 |" in block  # polled, support, object, abstain, failed, unprompted
    assert "| 1 | 2 | 1 | 50% |" in block and "| 2 | 2 | 0 | 0% |" in block
    tva_block = text.split("## Think notes vs actions (private vs public)\n")[1]
    assert "**diverges** — mango: think “I doubt it” / action “Mango is fine”" in tva_block
    assert "_wrote no think notes_" in tva_block  # b has no judgment and none was expected


def test_process_2_run_without_private_messages_or_sequential_rounds():
    empty = Process2Section(network=Counter(), chart=None, participation={}, order=[])
    block = render_run(section(run=P2, process2=empty)).split("## Process 2\n")[1]
    assert "_No private messages._" in block and "_No rounds after the first._" in block


def test_process_1_run_report_has_no_process_2_sections():
    text = render_run(section())
    assert "## Process 2" not in text and "Think notes vs actions" not in text


def test_summary_process_2_section():
    s = SummarySection(runs=[P2], who=who, charts={}, identity=[], norms=[], codebook=[], twins={}, categories={},
                       failed=[], benchmark=[], reciprocity=[], in_progress=[], judge_note=None,
                       process2=Process2Summary([RecurringPair("a", "b", 2, 5)], {"b": Participation(objected=2)},
                                                [OrderStat(1, 4, 3)]))
    block = render_summary(s).split("## Process 2\n")[1]
    assert "- Alpha (a) → Bea (b): 2 runs, 5 messages" in block
    assert "| Bea (b) | 2 | 0 | 2 | 0 | 0 | 0 |" in block
    assert "| 1 | 4 | 3 | 75% |" in block
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_observer_report.py -q`
Expected: FAIL — `ImportError: cannot import name 'Process2Section'`

- [ ] **Step 3: Implement in `report.py`**

Imports: add `from aiswarm.observer.process2 import OrderStat, Participation, RecurringPair`.

Before `RunSection`:

```python
@dataclass
class Process2Section:
    network: Counter  # (sender, recipient) -> private messages
    chart: str | None  # network PNG relative to the run report; None when no messages were sent
    participation: dict[str, Participation]
    order: list[OrderStat]
```

Add the field to `RunSection` (last): `process2: Process2Section | None = None  # only for Process 2 runs`.

Rendering helpers (shared by run and summary):

```python
def _participation(rows: dict[str, Participation], who: Who) -> list[str]:
    out = ["### Poll participation", "", "| Agent | polled | support | object | abstain | failed | unprompted votes |",
           "|---|---|---|---|---|---|---|"]
    out += [f"| {who(a)} | {p.polled} | {p.supported} | {p.objected} | {p.abstained} | {p.failed} | {p.unprompted} |"
            for a, p in rows.items()]
    return out + [""]


def _order(stats: list[OrderStat]) -> list[str]:
    out = ["### Speaking order", "", "Rounds after the first; position 1 spoke first. Taken up: adopted, refined or "
           "cited in a call by another member.", ""]
    if not stats:
        return out + ["_No rounds after the first._", ""]
    out += ["| Position | turns | taken up | share |", "|---|---|---|---|"]
    out += [f"| {s.position} | {s.turns} | {s.taken_up} | {round(100 * s.taken_up / s.turns)}% |" for s in stats]
    return out + [""]


def _process2(s: RunSection) -> list[str]:
    p = s.process2
    out = ["## Process 2", "", "### Private messages", ""]
    if not p.network:
        out += ["_No private messages._", ""]
    else:
        agents = s.run.agent_ids
        out += ["| From \\ To | " + " | ".join(s.who(a) for a in agents) + " |", "|---" * (len(agents) + 1) + "|"]
        out += [f"| {s.who(a)} | " + " | ".join("–" if a == b else str(p.network.get((a, b), 0)) for b in agents) + " |"
                for a in agents]
        out += [""] + ([f"![Private messages]({p.chart})", ""] if p.chart else [])
    return out + _participation({a: p.participation[a] for a in s.run.agent_ids if a in p.participation}, s.who) \
        + _order(p.order)
```

In `render_run`, after `_credits(s) + [""]`:

```python
    if s.process2 is not None:
        out += _process2(s)
```

and after the "Private journal vs public reviews" block:

```python
    if r.process == 2:
        out += _per_agent(s, "Think notes vs actions (private vs public)", "think_vs_act", lambda res: [
            f"- **{p['relation']}** — {p['about']}: think “{p['think_quote']}” / action “{p['action_quote']}”"
            for p in res["points"]] or ["- nothing to compare"], none_without_judgment="_wrote no think notes_")
```

Summary: before `SummarySection` add

```python
@dataclass
class Process2Summary:
    pairs: list[RecurringPair]  # who wrote privately to whom in two or more runs
    participation: dict[str, Participation]  # summed over Process 2 runs
    order: list[OrderStat]  # summed by position over Process 2 runs
```

Field on `SummarySection` (last): `process2: Process2Summary | None = None  # None when no Process 2 run has closed`.

In `render_summary`, after the Credit reciprocity block:

```python
    if s.process2 is not None:
        p = s.process2
        out += ["", "## Process 2", "", "### Recurring private-message pairs", ""]
        out += [f"- {s.who(r.sender)} → {s.who(r.recipient)}: {r.runs} runs, {r.messages} messages" for r in p.pairs] \
            or ["_None yet._"]
        out += [""] + _participation(p.participation, s.who) + _order(p.order)
```

- [ ] **Step 4: Run the report tests to verify they pass**

Run: `uv run pytest tests/test_observer_report.py -q`
Expected: PASS

- [ ] **Step 5: Write the failing end-to-end test**

In `tests/test_observer_observe.py`, add a branch at the top of `judge_reply` (before the `"## journal"` branch):

```python
    if "## think notes" in text:
        return json.dumps({"points": [{"relation": "diverges", "about": "pear", "think_quote": "Pear is weak.",
                                       "action_quote": "Pear is fine."}]})
```

Then append:

```python
async def test_observe_reports_a_process_2_run(lab, community):
    store, conn = community
    add_run(conn, "r5", created=FUTURE, goal_id="debate", process=2)
    add_entry(conn, "r5", "a", "think", {"text": "Pear is weak."}, round=1, visible_to=["a"])
    add_entry(conn, "r5", "a", "propose", {"answer_summary": "Pear", "method": "m"}, round=1)
    add_entry(conn, "r5", "a", "think", {"text": "Still unsure."}, round=2, visible_to=["a"])
    add_entry(conn, "r5", "a", "adopt", {"target": 2, "reason": "Pear is fine."}, refs=[2], round=2)
    add_entry(conn, "r5", "b", "private_message", {"to": "a", "text": "psst"}, round=2, visible_to=["b", "a"], to="a")
    result = await observe(lab, judge_client=FakeModel(judge_reply))
    report = (lab.observer.dir / "runs" / "r5.md").read_text()
    assert "- **Process:** 2" in report and "## Process 2" in report
    assert "![Private messages](../charts/network_r5.png)" in report
    assert (lab.observer.dir / "charts" / "network_r5.png") in result.written
    assert "think “Pear is weak.” / action “Pear is fine.”" in report
    assert "## Process 2" not in (lab.observer.dir / "runs" / "r1.md").read_text()
    summary = (lab.observer.dir / "summary.md").read_text()
    assert "debate (P2)" in summary and "## Process 2" in summary
```

- [ ] **Step 6: Run it to verify it fails**

Run: `uv run pytest tests/test_observer_observe.py -q -k process_2`
Expected: FAIL — no `## Process 2` in `r5.md`

- [ ] **Step 7: Implement in `observe.py`**

Imports: add `network_chart` to the `charts` import; `think_vs_act_inputs` to the `inputs` import; `from aiswarm.observer import process2 as p2`; `Process2Section, Process2Summary` to the `report` import.

In `_judge_run`, inside the per-agent loop after the `private_public` job:

```python
        if run.process == 2 and (tva := think_vs_act_inputs(conn, run, agent, who)) is not None:
            jobs.append(judge.run(ANALYSES["think_vs_act"], run_id=run.id, agent_id=agent, inputs=tva))
```

A helper above `observe`:

```python
def _process2_section(conn: sqlite3.Connection, run: m.RunInfo, who: Who, out_dir: Path,
                      written: list[Path]) -> Process2Section | None:
    if run.process != 2:
        return None
    network = p2.pm_network(conn, run)
    chart = None
    if network:
        path = network_chart(out_dir / "charts" / f"network_{run.id}.png", f"Private messages: {run.tag}",
                             {a: who(a) for a in run.agent_ids}, network)
        written.append(path)
        chart = f"../charts/{path.name}"
    return Process2Section(network, chart, p2.poll_participation(conn, run), p2.speaking_order(conn, run))
```

In `observe`, pass `process2=_process2_section(conn, run, who, out_dir, written)` when building `RunSection`.

In `_write_summary`, build the summary section's `process2`:

```python
    p2_runs = [r for r in runs if r.process == 2]
    process2 = Process2Summary(
        p2.recurring_pairs(conn, p2_runs),
        p2.combine_participation([p2.poll_participation(conn, r) for r in p2_runs]),
        p2.combine_order([p2.speaking_order(conn, r) for r in p2_runs]),
    ) if p2_runs else None
```

and pass `process2=process2` to `SummarySection(...)`.

- [ ] **Step 8: Run the Observer tests to verify they pass**

Run: `uv run pytest tests/test_observer_observe.py tests/test_observer_report.py -q`
Expected: PASS

- [ ] **Step 9: Run the whole suite**

Run: `uv run pytest -q`
Expected: all pass (474 before this plan, plus the new tests)

- [ ] **Step 10: Commit**

```bash
git add src/aiswarm/observer/report.py src/aiswarm/observer/observe.py tests/test_observer_report.py tests/test_observer_observe.py
git commit -m "feat: Observer reports a Process 2 section per run and in the summary, with the think-vs-act analysis"
```
