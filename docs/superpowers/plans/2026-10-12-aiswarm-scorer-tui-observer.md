# Scorer TUI + Observer (Plan 5b) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `aiswarm watch` shows the Scorer (a two-line status bar with the latest ruling and each agent's `★ 4.2 ◆ 2`, Scorer entries readable and rejections in red), and `aiswarm observe` reports the Scorer's rulings, the points it awarded and how often callers credit themselves, per run and across runs.

**Architecture:** The watcher keeps its split: `state.py` turns Scorer entries into `FeedLine`s and reads each agent's lifetime points and the latest ruling as plain data; `render.py` builds the two-line status bar and colours rejections. The Observer gets one new deterministic module, `observer/scoring.py` (rulings, awards, self-credit), whose results `report.py` renders into a `## Scorer` section of each run report and of the summary, and `observe.py` wires in plus three trend charts. Both read-only readers tolerate a store from before the Scorer (no `scorer_points` table) through one helper, `db.has_table`.

**Tech Stack:** Python 3.14, uv, Textual 8.2.8, Rich 15.0.0, matplotlib (Agg), pytest (asyncio auto; Textual `run_test` pilot).

**Spec:** `docs/superpowers/specs/2026-10-12-aiswarm-scorer-design.md` §6 (TUI), §7 (Observer), §9.7–9.8 (testing). Plan 5a (engine) is merged: `scorer_accepted` / `scorer_rejected` system entries carry `{"call_id": …}` (+ `"feedback"` on rejection) with `refs=[call_id]`; `scorer_points (run_id, agent_id, call_id, contribution, created_at)` holds one row per point; `aiswarm.scorer.points(conn)` counts lifetime points.

## Global Constraints

- The watcher and the Observer are read-only: they never write the community store, never migrate it, and the agent path never imports them (`tests/test_watch_isolation.py`). The Observer never reads to or from the Scorer; it reports the Scorer's rulings like any other record.
- Status bar (spec §6), two lines: `round 7/12 · react │ call #40: 3 for, 1 against │ Scorer: rejected #31` then `Ash ctl ★ 4.2 ◆ 2 speaking…   ·   Bea ★ 3.9 ◆ 0 idle   ·   Cyd ★ – ◆ 1 thinking…`. A space follows `★` and `◆`. Line 2 wraps rather than being cut.
- `scorer_accepted` / `scorer_rejected` entries appear in the Table styled as system entries (dim italic); rejections in red.
- Colour is applied to spans in code (Rich `Text`), never parsed from entry text, so brackets in entries (and in Scorer feedback) stay literal.
- Observer per run: the ruling(s), each rejection's feedback, points awarded (who, for what). Summary: trend chart of points per agent; accepted vs rejected rulings per run; how often callers credit themselves.
- No new dependencies. Run with uv: `uv run pytest -q`.

## Rulings in this plan

- **Scorer feed lines** read `#45 r7 system scorer_rejected → #31: <feedback>` and `#46 r8 system scorer_accepted → #40`: the call id is shown as the target, like any entry that points at another.
- **Line 1, no ruling yet:** `Scorer: no ruling yet`. A ruling word is coloured (accepted green, rejected red). The call tally still shows the newest call even when it is spent; the ruling on the same line tells the reader it was rejected.
- **Line 2 separator** is `   ·   ` (three spaces each side), as in the spec sketch; `(no agents yet)` when there are none.
- **Points on screen** are lifetime points (as the agents see them), reloaded with names and reputation (on start, `run_closed` and post-run steps — points change only when a run closes).
- **"Points per agent" chart** plots lifetime points after each run (cumulative), `None` for runs the agent was not in.
- **Self-credit** counts calls for consensus, not rulings: a call whose `credits` name its own caller counts once however many times it does. It covers every call in a run (spent ones too), so runs from before the Scorer contribute.
- **Summary `## Scorer`** sits after `## Credit reciprocity`; the run report's `## Scorer` sits right after `## Credits`.

## Review Focus

1. **A community store from before the Scorer** (no `scorer_points` table; read-only, so never migrated): the watcher's refresh and `aiswarm observe` must still work and show 0 points. Pinned in Task 1 (watcher) and Task 4 (observe).
2. **Scorer feedback that looks like Rich markup** (`[bold]`, `[/]`): shown literally, in red, without a `MarkupError`. Pinned in Task 2.
3. **A narrow terminal**: line 2 wraps onto further lines instead of being cut. Pinned in Task 2 (app).
4. **Several rulings in one run** (rejected, rejected, accepted): the status bar shows the newest; the run report lists all in order; the summary counts each. Pinned in Task 1 and Task 3.
5. **A caller who credits itself twice in one call**, or a credit with no `agent_id`: counted once / ignored, no `KeyError`. Pinned in Task 3.

---

### Task 1: Watcher state reads Scorer entries, points and the latest ruling

**Files:**
- Modify: `src/aiswarm/db.py` (add `has_table`)
- Modify: `src/aiswarm/watch/state.py` (`feed_line`, `AgentCard.points`, `RulingLine`, `RunState.ruling`, `refresh`)
- Test: `tests/test_watch_state.py`

**Interfaces:**
- Consumes: `aiswarm.scorer.points(conn) -> dict[str, int]`.
- Produces:
  - `aiswarm.db.has_table(conn: sqlite3.Connection, name: str) -> bool`.
  - `AgentCard.points: int = 0` (lifetime Scorer points).
  - `RulingLine(verdict: str, call_id: int)` (frozen; verdict `"accepted"` | `"rejected"`), `RunState.ruling: RulingLine | None` (the newest ruling in the run).
  - `feed_line` on `scorer_accepted` → `kind="scorer_accepted", target=" → #<call_id>", body=""`; on `scorer_rejected` → `body=<feedback>`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_watch_state.py` (after the existing `refreshed` tests; `entry`, `who`, `state`, `add_run`, `add_entry` are already defined/imported there). Change the second import line to `from aiswarm.watch.state import FEED_MAX, RulingLine, RunState, feed_line, feed_text`.

```python
def test_feed_text_for_scorer_rulings():
    rejected = entry(45, "scorer_rejected", {"call_id": 31, "feedback": "Name one\ntopping."}, author="system")
    accepted = entry(46, "scorer_accepted", {"call_id": 40}, author="system")
    sys_who = lambda a: "system" if a == "system" else who(a)  # noqa: E731
    assert feed_text(rejected, sys_who) == "#45 r2 system scorer_rejected → #31: Name one topping."
    assert feed_text(accepted, sys_who) == "#46 r2 system scorer_accepted → #40"
    assert feed_line(rejected, sys_who).pane == "table"


def _points(conn, *rows):
    conn.executemany("INSERT INTO scorer_points (run_id, agent_id, call_id, contribution, created_at) "
                     "VALUES (?, ?, ?, ?, 't')", rows)


def test_refresh_reads_lifetime_points_and_the_newest_ruling(community):
    store, conn = community
    add_run(conn, "r0", agents=("a", "b"))
    _points(conn, ("r0", "a", 1, "idea"))
    add_run(conn, "r1", agents=("a", "b"), status="running")
    first = add_entry(conn, "r1", "a", "call_consensus", {"synthesis": "Fruit", "credits": []})
    add_entry(conn, "r1", "system", "scorer_rejected", {"call_id": first, "feedback": "Which fruit?"}, refs=[first])
    second = add_entry(conn, "r1", "b", "call_consensus", {"synthesis": "Kiwi", "credits": []})
    add_entry(conn, "r1", "system", "scorer_rejected", {"call_id": second, "feedback": "Say why."}, refs=[second])
    s = refreshed(community)
    assert s.cards["a"].points == 1 and s.cards["b"].points == 0
    assert s.ruling == RulingLine("rejected", second)
    third = add_entry(conn, "r1", "a", "call_consensus", {"synthesis": "Kiwi, sweet", "credits": []})
    add_entry(conn, "r1", "system", "scorer_accepted", {"call_id": third}, refs=[third])
    _points(conn, ("r1", "a", third, "me"), ("r1", "b", third, "kiwi"))
    s.apply({"event": "run_closed", "reason": "consensus", "call_id": third})  # points are reloaded on close
    s.refresh(conn, store, 5.0)
    assert s.cards["a"].points == 2 and s.cards["b"].points == 1
    assert s.ruling == RulingLine("accepted", third)


def test_refresh_without_a_ruling(community):
    store, conn = community
    add_run(conn, "r1", agents=("a", "b"), status="running")
    assert refreshed(community).ruling is None


def test_refresh_works_on_a_store_from_before_the_scorer(community):
    store, conn = community
    conn.execute("DROP TABLE scorer_points")  # an older store: read-only readers never migrate it
    add_run(conn, "r1", agents=("a", "b"), status="running")
    add_entry(conn, "r1", "a", "propose", {"answer_summary": "Mango", "method": "m"})
    s = refreshed(community)
    assert s.feed and s.cards["a"].points == 0 and not s.profiles_stale


def test_has_table(community):
    from aiswarm.db import has_table
    store, conn = community
    assert has_table(conn, "scorer_points") and not has_table(conn, "nope")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_watch_state.py -q`
Expected: FAIL — `ImportError: cannot import name 'RulingLine'` (collection error).

- [ ] **Step 3: Add `has_table` to `src/aiswarm/db.py`**

After `connect_readonly`:

```python
def has_table(conn: sqlite3.Connection, name: str) -> bool:
    """For read-only readers: a store from before a migration lacks its tables, and they never migrate it."""
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone() is not None
```

- [ ] **Step 4: Teach `state.py` the Scorer**

Imports — add:

```python
from aiswarm.db import has_table
from aiswarm.scorer import points as scorer_points
```

Constants — add `"scorer_rejected": "feedback"` to `_SUMMARY`, and below it:

```python
SCORER = ("scorer_accepted", "scorer_rejected")  # system entries; they point at the call by call_id
```

`AgentCard` — add a field after `reputation`:

```python
    points: int = 0  # lifetime Scorer points
```

After `CallLine` add:

```python
@dataclass(frozen=True)
class RulingLine:
    verdict: str  # "accepted" | "rejected"
    call_id: int
```

In `feed_line`, replace the `target = …` line and the body chain's `elif entry.type in _SUMMARY:` branch area so it reads:

```python
    p = entry.payload
    ref = p.get("call_id") if entry.type in SCORER else p.get("target")
    target = f" → #{ref}" if ref is not None else ""
    body, stance, failure, to = "", "", "", ""
    if entry.type == "pass":
        failure = str(p.get("failure") or "")
    elif entry.type == "vote":
        stance, body = str(p.get("stance", "")), str(p.get("reason", ""))
    elif entry.type == "write_file":
        body = f"{p.get('path', '')} v{p.get('version', '')}: {p.get('note', '')}"
    elif entry.type == "scorer_accepted":
        body = ""
    elif entry.type in _SUMMARY:
        body = str(p.get(_SUMMARY[entry.type], ""))
    else:
        body = json.dumps(p, ensure_ascii=False)
```

`RunState` — add a field after `calls`:

```python
    ruling: RulingLine | None = None  # the Scorer's newest ruling in this run
```

In `refresh`, in the `if self.profiles_stale:` block, read points once before the loop and set them per card:

```python
        if self.profiles_stale:
            earned = scorer_points(conn) if has_table(conn, "scorer_points") else {}  # older stores: none
            for agent_id, card in cards.items():
                profile = store.public(agent_id) if agent_id in known else None
                card.name = profile.name if profile else agent_id
                card.headline = profile.headline if profile else ""
                card.control = store.record(agent_id).control if agent_id in known else False
                card.reputation = reputation(conn, agent_id, half_life).score
                card.points = earned.get(agent_id, 0)
```

After the `calls` loop, compute the ruling:

```python
        ruling = None
        for e in entries:
            if e.type in SCORER and isinstance(e.payload.get("call_id"), int):
                ruling = RulingLine(e.type.removeprefix("scorer_"), e.payload["call_id"])
```

and in the "only now change the state" block add `self.ruling = ruling`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_watch_state.py tests/test_watch_isolation.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/aiswarm/db.py src/aiswarm/watch/state.py tests/test_watch_state.py
git commit -m "feat: the watcher reads Scorer entries, lifetime points and the newest ruling"
```

---

### Task 2: Two-line status bar and red rejections

**Files:**
- Modify: `src/aiswarm/watch/render.py` (`styled`, `status`)
- Test: `tests/test_watch_render.py`, `tests/test_watch_app.py`

**Interfaces:**
- Consumes: `AgentCard.points`, `RunState.ruling: RulingLine | None`, `FeedLine.kind` (Task 1).
- Produces: `render.status(s: RunState, colours) -> Text` with exactly one `"\n"` (two lines); `render.ruling(s: RunState) -> Text` (`"Scorer: rejected #31"` / `"Scorer: accepted #40"` / `"Scorer: no ruling yet"`).

- [ ] **Step 1: Write the failing render tests**

In `tests/test_watch_render.py`, change the state import to also bring `RulingLine`:
`from aiswarm.watch.state import AgentCard, CallLine, FeedLine, FileLine, PostRunRow, RulingLine, RunState, feed_line`.

Replace `test_status_bar` with:

```python
def test_status_bar_has_a_run_line_and_an_agents_line():
    s = state(process=2, phase="rounds", step_phase="react", round=7,
              cards={"a": AgentCard("a", "Ash", control=True, reputation=4.25, points=2, state="speaking…"),
                     "b": AgentCard("b", "Bartholomew-the-long", state="failed: model_error")},
              calls=[CallLine(40, "Bea (b)", 3, 1), CallLine(12, "Ash (a)", 1, 0)],
              ruling=RulingLine("rejected", 31))
    bar = render.status(s, COLOURS)
    assert bar.plain == ("round 7/12 · react │ call #40: 3 for, 1 against │ Scorer: rejected #31\n"
                         "Ash ctl ★ 4.2 ◆ 2 speaking…   ·   Bartholomew… ★ – ◆ 0 failed: model_error")
    assert "bold" in style_of(bar, "speaking…") and "red" in style_of(bar, "failed: model_error")
    assert "red" in style_of(bar, "rejected #31")
    assert f"bold {PINK}" in style_of(bar, "Ash")
    assert render.status(state(), COLOURS).plain == (
        "round 0/12 · waiting │ no call yet │ Scorer: no ruling yet\n(no agents yet)")


def test_ruling_words_are_coloured():
    assert "green" in style_of(render.ruling(state(ruling=RulingLine("accepted", 40))), "accepted #40")
    assert render.ruling(state(ruling=RulingLine("accepted", 40))).plain == "Scorer: accepted #40"


def test_scorer_entries_are_system_lines_and_rejections_are_red():
    rejected = line("scorer_rejected", {"call_id": 3, "feedback": "Name [bold]one[/] topping."}, "system")
    accepted = line("scorer_accepted", {"call_id": 3}, "system")
    text = render.styled(rejected, COLOURS)
    assert text.plain == rejected.text and "Name [bold]one[/] topping." in text.plain
    assert "red" in style_of(text, "scorer_rejected")
    assert "dim italic" in style_of(render.styled(accepted, COLOURS), "scorer_accepted")
```

- [ ] **Step 2: Run the render tests to verify they fail**

Run: `uv run pytest tests/test_watch_render.py -q`
Expected: FAIL — the status assertions differ (one-line bar) and `render.ruling` does not exist.

- [ ] **Step 3: Implement in `render.py`**

In `styled`, replace the system-entry line:

```python
    if line.author == SYSTEM:
        return out.append(line.text, style="red italic" if line.kind == "scorer_rejected" else "dim italic")
```

Replace `status` with `ruling` + `status`:

```python
RULING_STYLES = {"accepted": "green", "rejected": "red"}
AGENT_SEPARATOR = "   ·   "


def ruling(s: RunState) -> Text:
    out = Text("Scorer: ")
    if s.ruling is None:
        return out.append("no ruling yet")
    return out.append(f"{s.ruling.verdict} #{s.ruling.call_id}", style=RULING_STYLES.get(s.ruling.verdict, ""))


def status(s: RunState, colours: Mapping[str, str]) -> Text:
    """Two lines: the run (round, phase, newest call, Scorer) and the agents. The agents line wraps, never cuts."""
    out = Text(f"round {s.round}/{s.max_rounds} · {phase_label(s)} │ ")
    if s.calls:  # newest first; the poll treats the latest call as the open one
        c = s.calls[0]
        out.append(f"call #{c.entry_id}: {c.supporters} for, {c.objectors} against")
    else:
        out.append("no call yet")
    out.append(" │ ")
    out.append_text(ruling(s))
    out.append("\n")
    for i, card in enumerate(s.cards.values()):
        if i:
            out.append(AGENT_SEPARATOR)
        out.append_text(_agent(short(card.name), card.agent_id, colours))
        if card.control:
            out.append(" ctl", style="dim")
        out.append(f" ★ {'–' if card.reputation is None else f'{card.reputation:.1f}'} ◆ {card.points} ")
        out.append(card.state, style="red" if card.state.startswith("failed") else STATUS_STYLES.get(card.state, ""))
    if not s.cards:
        out.append("(no agents yet)")
    return out
```

- [ ] **Step 4: Run the render tests to verify they pass**

Run: `uv run pytest tests/test_watch_render.py -q`
Expected: PASS

- [ ] **Step 5: Update and add app tests**

In `tests/test_watch_app.py`, in `test_process_1_run_has_a_full_width_table_and_a_status_bar`, replace the status assertion with:

```python
        assert text(app, "status") == ("round 1/12 · rounds │ no call yet │ Scorer: no ruling yet\n"
                                       "Alpha ★ – ◆ 0 idle   ·   Bea ★ – ◆ 0 idle")
```

Append:

```python
async def test_status_bar_wraps_on_a_narrow_terminal_and_shows_rejections(tmp_path, community):
    conn, events, watcher = setup(tmp_path, community)
    call = add_entry(conn, "r1", "b", "call_consensus", {"synthesis": "Fruit", "credits": []})
    add_entry(conn, "r1", "system", "scorer_rejected", {"call_id": call, "feedback": "Which [b]fruit[/b]?"},
              refs=[call])
    app = WatchApp(watcher, interval=3600)
    async with app.run_test(size=(40, 30)) as pilot:
        await pilot.pause()
        assert "Bea ★ – ◆ 0 idle" in text(app, "status") and f"Scorer: rejected #{call}" in text(app, "status")
        assert app.query_one("#status").region.height > 2  # wrapped, not cut
        assert "Which [b]fruit[/b]?" in pane(app, "table")
```

- [ ] **Step 6: Run the watcher tests**

Run: `uv run pytest tests/test_watch_render.py tests/test_watch_app.py tests/test_watch_state.py -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/aiswarm/watch/render.py tests/test_watch_render.py tests/test_watch_app.py
git commit -m "feat: two-line watch status bar with the Scorer's ruling and points; rejections in red"
```

---

### Task 3: Observer Scorer metrics

**Files:**
- Create: `src/aiswarm/observer/scoring.py`
- Test: `tests/test_observer_scoring.py`

**Interfaces:**
- Consumes: `aiswarm.db.has_table` (Task 1); `aiswarm.observer.metrics.RunInfo`.
- Produces (all deterministic, read-only):
  - `RulingRow(call_id: int, caller: str, accepted: bool, feedback: str)` (frozen).
  - `Award(agent_id: str, call_id: int, contribution: str)` (frozen).
  - `SelfCredit(calls: int = 0, self_credited: int = 0)` (frozen).
  - `rulings(conn, run_id: str) -> list[RulingRow]` in board order; caller `"?"` if the call is missing.
  - `awards(conn, run_id: str) -> list[Award]` in insertion order; `[]` when the store has no `scorer_points` table.
  - `self_credit(conn, run: RunInfo) -> dict[str, SelfCredit]` keyed by every member of the run.
  - `combine_self_credit(parts: list[dict[str, SelfCredit]]) -> dict[str, SelfCredit]` (sorted by agent id).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_observer_scoring.py`:

```python
from observer_helpers import add_entry, add_run

from aiswarm.observer.metrics import closed_runs
from aiswarm.observer.scoring import (
    Award, RulingRow, SelfCredit, awards, combine_self_credit, rulings, self_credit,
)


def _points(conn, *rows):
    conn.executemany("INSERT INTO scorer_points (run_id, agent_id, call_id, contribution, created_at) "
                     "VALUES (?, ?, ?, ?, 't')", rows)


def scored_run(conn):
    """r1: a's call is rejected, then b's call (crediting b twice, a, and a credit with no id) is accepted."""
    add_run(conn, "r1", ended="consensus")
    first = add_entry(conn, "r1", "a", "call_consensus",
                      {"synthesis": "Fruit", "credits": [{"agent_id": "c", "contribution": "c's idea"}]})
    add_entry(conn, "r1", "system", "scorer_rejected", {"call_id": first, "feedback": "Which fruit?"}, refs=[first])
    second = add_entry(conn, "r1", "b", "call_consensus", {"synthesis": "Kiwi", "credits": [
        {"agent_id": "b", "contribution": "me"}, {"agent_id": "b", "contribution": "me again"},
        {"agent_id": "a", "contribution": "kiwi"}, {"contribution": "nobody"}]})
    add_entry(conn, "r1", "system", "scorer_accepted", {"call_id": second}, refs=[second])
    add_entry(conn, "r1", "system", "run_closed", {"reason": "consensus", "call_id": second})
    _points(conn, ("r1", "b", second, "me"), ("r1", "a", second, "kiwi"))
    return first, second


def test_rulings_in_board_order(community):
    store, conn = community
    first, second = scored_run(conn)
    assert rulings(conn, "r1") == [RulingRow(first, "a", False, "Which fruit?"), RulingRow(second, "b", True, "")]


def test_awards_say_who_earned_what_for_which_call(community):
    store, conn = community
    first, second = scored_run(conn)
    assert awards(conn, "r1") == [Award("b", second, "me"), Award("a", second, "kiwi")]


def test_self_credit_counts_each_call_once(community):
    store, conn = community
    scored_run(conn)
    got = self_credit(conn, closed_runs(conn)[0])
    assert got == {"a": SelfCredit(1, 0), "b": SelfCredit(1, 1), "c": SelfCredit(0, 0)}


def test_combine_self_credit():
    assert combine_self_credit([{"b": SelfCredit(1, 1)}, {"a": SelfCredit(2, 0), "b": SelfCredit(1, 0)}]) == \
        {"a": SelfCredit(2, 0), "b": SelfCredit(2, 1)}


def test_a_run_without_rulings_and_an_older_store(community):
    store, conn = community
    add_run(conn, "r1")
    conn.execute("DROP TABLE scorer_points")  # a store from before the Scorer
    assert rulings(conn, "r1") == [] and awards(conn, "r1") == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_observer_scoring.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'aiswarm.observer.scoring'`.

- [ ] **Step 3: Implement `src/aiswarm/observer/scoring.py`**

```python
"""Scorer metrics: its rulings, the points it awarded, and how often callers credit themselves. Deterministic; no writes.

The Observer only reports these records; it never reads to or from the Scorer.
"""

import json
import sqlite3
from collections import Counter
from dataclasses import dataclass, fields

from aiswarm.db import has_table
from aiswarm.observer.metrics import RunInfo


@dataclass(frozen=True)
class RulingRow:
    call_id: int
    caller: str
    accepted: bool
    feedback: str  # empty on acceptance


@dataclass(frozen=True)
class Award:
    agent_id: str
    call_id: int
    contribution: str  # what the caller credited the agent for


@dataclass(frozen=True)
class SelfCredit:
    calls: int = 0  # calls for consensus the agent made
    self_credited: int = 0  # of those, calls whose credits name the caller


def rulings(conn: sqlite3.Connection, run_id: str) -> list[RulingRow]:
    rows = conn.execute("SELECT id, author, type, payload FROM entries WHERE run_id = ? AND type IN "
                        "('call_consensus', 'scorer_accepted', 'scorer_rejected') ORDER BY id", (run_id,)).fetchall()
    callers = {r["id"]: r["author"] for r in rows if r["type"] == "call_consensus"}
    out = []
    for r in rows:
        if r["type"] == "call_consensus":
            continue
        payload = json.loads(r["payload"])
        call = payload.get("call_id")
        out.append(RulingRow(call, callers.get(call, "?"), r["type"] == "scorer_accepted",
                             str(payload.get("feedback", ""))))
    return out


def awards(conn: sqlite3.Connection, run_id: str) -> list[Award]:
    if not has_table(conn, "scorer_points"):  # a store from before the Scorer; never migrated
        return []
    rows = conn.execute("SELECT agent_id, call_id, contribution FROM scorer_points WHERE run_id = ? ORDER BY id",
                        (run_id,))
    return [Award(r["agent_id"], r["call_id"], r["contribution"]) for r in rows]


def self_credit(conn: sqlite3.Connection, run: RunInfo) -> dict[str, SelfCredit]:
    """Every call for consensus in the run, spent or not: did its caller credit itself?"""
    counts: dict[str, Counter] = {agent: Counter() for agent in run.agent_ids}
    for r in conn.execute("SELECT author, payload FROM entries WHERE run_id = ? AND type = 'call_consensus'",
                          (run.id,)):
        if r["author"] not in counts:
            continue
        credited = {c.get("agent_id") for c in json.loads(r["payload"]).get("credits", []) if isinstance(c, dict)}
        counts[r["author"]]["calls"] += 1
        counts[r["author"]]["self_credited"] += r["author"] in credited
    return {agent: SelfCredit(**c) for agent, c in counts.items()}


def combine_self_credit(parts: list[dict[str, SelfCredit]]) -> dict[str, SelfCredit]:
    totals: dict[str, Counter] = {}
    for part in parts:
        for agent, s in part.items():
            totals.setdefault(agent, Counter()).update({f.name: getattr(s, f.name) for f in fields(s)})
    return {agent: SelfCredit(**c) for agent, c in sorted(totals.items())}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_observer_scoring.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/aiswarm/observer/scoring.py tests/test_observer_scoring.py
git commit -m "feat: Observer metrics for the Scorer: rulings, awards and self-credit"
```

---

### Task 4: Scorer sections in the reports, charts and `observe` wiring

**Files:**
- Modify: `src/aiswarm/observer/report.py` (`RunSection.rulings/awards`, `_scorer_run`, `ScorerSummary`, `SummarySection.scorer`, `_scorer_summary`)
- Modify: `src/aiswarm/observer/observe.py` (fill the new fields; three charts in `_charts`)
- Test: `tests/test_observer_report.py`, `tests/test_observer_observe.py`

**Interfaces:**
- Consumes: `RulingRow`, `Award`, `SelfCredit`, `rulings`, `awards`, `self_credit`, `combine_self_credit` (Task 3).
- Produces:
  - `RunSection(..., rulings: list[RulingRow] = [], awards: list[Award] = [])` (both `field(default_factory=list)`).
  - `ScorerSummary(accepted: int, rejected: int, points: dict[str, int], self_credit: dict[str, SelfCredit])`; `SummarySection(..., scorer: ScorerSummary | None = None)`.
  - Charts `charts/scorer_points.png` ("Scorer points"), `charts/scorer_rulings.png` ("Scorer rulings"), `charts/self_credit.png` ("Self-credit").

- [ ] **Step 1: Write the failing report tests**

In `tests/test_observer_report.py`, add to the imports:

```python
from aiswarm.observer.report import ScorerSummary
from aiswarm.observer.scoring import Award, RulingRow, SelfCredit
```

Append:

```python
def test_run_report_lists_rulings_feedback_and_awards():
    text = render_run(section(rulings=[RulingRow(4, "a", False, "Which fruit?"), RulingRow(9, "b", True, "")],
                              awards=[Award("b", 9, "me"), Award("a", 9, "kiwi")]))
    scorer = text.split("## Scorer\n")[1]
    assert "- call #4 by Alpha (a): **rejected** — Which fruit?" in scorer
    assert "- call #9 by Bea (b): **accepted**" in scorer
    assert "- Bea (b): 1 point for call #9 — me (self-credit)" in scorer
    assert "- Alpha (a): 1 point for call #9 — kiwi" in scorer
    assert text.index("## Credits") < text.index("## Scorer")


def test_run_report_without_rulings():
    assert "_The Scorer did not rule in this run._" in render_run(section())


def test_summary_scorer_section():
    s = SummarySection(
        runs=[RUN], who=who, charts={}, identity=[], norms=[], codebook=[], twins={}, categories={}, failed=[],
        benchmark=[], reciprocity=[], in_progress=[], judge_note=None,
        scorer=ScorerSummary(accepted=1, rejected=2, points={"a": 3},
                             self_credit={"a": SelfCredit(4, 1), "b": SelfCredit(0, 0)}))
    text = render_summary(s)
    scorer = text.split("## Scorer\n")[1]
    assert "3 rulings: 1 accepted, 2 rejected." in scorer
    assert "| Alpha (a) | 3 | 4 | 1 |" in scorer and "| Bea (b) | 0 | 0 | 0 |" in scorer
    assert text.index("## Credit reciprocity") < text.index("## Scorer")


def test_summary_before_any_ruling():
    s = SummarySection(
        runs=[RUN], who=who, charts={}, identity=[], norms=[], codebook=[], twins={}, categories={}, failed=[],
        benchmark=[], reciprocity=[], in_progress=[], judge_note=None,
        scorer=ScorerSummary(0, 0, {}, {}))
    assert "_The Scorer has not ruled yet._" in render_summary(s)
```

- [ ] **Step 2: Run the report tests to verify they fail**

Run: `uv run pytest tests/test_observer_report.py -q`
Expected: FAIL — `ImportError: cannot import name 'ScorerSummary'`.

- [ ] **Step 3: Implement in `report.py`**

Import: `from aiswarm.observer.scoring import Award, RulingRow, SelfCredit`.

`RunSection` — add at the end:

```python
    rulings: list[RulingRow] = field(default_factory=list)  # the Scorer's rulings, in board order
    awards: list[Award] = field(default_factory=list)  # Scorer points awarded in this run
```

Add after `_credits`:

```python
def _scorer_run(s: RunSection) -> list[str]:
    out = ["## Scorer", ""]
    if not s.rulings:
        return out + ["_The Scorer did not rule in this run._", ""]
    for r in s.rulings:
        verdict = "**accepted**" if r.accepted else f"**rejected** — {r.feedback}"
        out.append(f"- call #{r.call_id} by {s.who(r.caller)}: {verdict}")
    if s.awards:
        callers = {r.call_id: r.caller for r in s.rulings}
        out += ["", "Points awarded:"]
        out += [f"- {s.who(a.agent_id)}: 1 point for call #{a.call_id} — {a.contribution}"
                + (" (self-credit)" if callers.get(a.call_id) == a.agent_id else "") for a in s.awards]
    return out + [""]
```

In `render_run`, change `out += [""] + _metrics(s) + [""] + _dimensions(s) + _credits(s) + [""]` to:

```python
    out += [""] + _metrics(s) + [""] + _dimensions(s) + _credits(s) + [""] + _scorer_run(s)
```

Before `SummarySection` add:

```python
@dataclass
class ScorerSummary:
    accepted: int
    rejected: int
    points: dict[str, int]  # agent -> Scorer points over these runs
    self_credit: dict[str, SelfCredit]  # agent -> calls made, calls crediting the caller
```

`SummarySection` — add at the end: `scorer: ScorerSummary | None = None`.

Add after `_identity_line`:

```python
def _scorer_summary(sc: ScorerSummary, who: Who) -> list[str]:
    out = ["## Scorer", ""]
    total = sc.accepted + sc.rejected
    out.append(f"{total} rulings: {sc.accepted} accepted, {sc.rejected} rejected." if total
               else "_The Scorer has not ruled yet._")
    agents = sorted(set(sc.points) | set(sc.self_credit))
    if agents:
        out += ["", "| Agent | Scorer points | calls | self-credited |", "|---|---|---|---|"]
        for a in agents:
            c = sc.self_credit.get(a, SelfCredit())
            out.append(f"| {who(a)} | {sc.points.get(a, 0)} | {c.calls} | {c.self_credited} |")
    return out + [""]
```

In `render_summary`, right after the `## Credit reciprocity` lines (before `if s.process2 is not None:`):

```python
    if s.scorer is not None:
        out += [""] + _scorer_summary(s.scorer, s.who)
```

- [ ] **Step 4: Run the report tests to verify they pass**

Run: `uv run pytest tests/test_observer_report.py -q`
Expected: PASS

- [ ] **Step 5: Write the failing observe tests**

Append to `tests/test_observer_observe.py`:

```python
def _scored(conn):
    add_run(conn, "r5", created=FUTURE, ended="consensus")
    first = add_entry(conn, "r5", "a", "call_consensus",
                      {"synthesis": "Fruit", "credits": [{"agent_id": "a", "contribution": "me"}]})
    add_entry(conn, "r5", "system", "scorer_rejected", {"call_id": first, "feedback": "Which fruit?"}, refs=[first])
    second = add_entry(conn, "r5", "b", "call_consensus",
                       {"synthesis": "Kiwi", "credits": [{"agent_id": "c", "contribution": "kiwi"}]})
    add_entry(conn, "r5", "system", "scorer_accepted", {"call_id": second}, refs=[second])
    add_entry(conn, "r5", "system", "run_closed", {"reason": "consensus", "call_id": second})
    conn.execute("INSERT INTO scorer_points (run_id, agent_id, call_id, contribution, created_at) "
                 "VALUES ('r5', 'c', ?, 'kiwi', 't')", (second,))
    return first, second


async def test_observe_reports_the_scorer(lab, community):
    store, conn = community
    first, second = _scored(conn)
    result = await observe(lab, judge_client=None, judge_note="--no-judge")
    report = (lab.observer.dir / "runs" / "r5.md").read_text()
    assert f"- call #{first} by Alpha (a): **rejected** — Which fruit?" in report
    assert f"- Cyd (c): 1 point for call #{second} — kiwi" in report
    summary = (lab.observer.dir / "summary.md").read_text()
    assert "2 rulings: 1 accepted, 1 rejected." in summary
    assert "| Alpha (a) | 0 | 1 | 1 |" in summary and "| Cyd (c) | 1 | 0 | 0 |" in summary
    for name in ("scorer_points.png", "scorer_rulings.png", "self_credit.png"):
        assert (lab.observer.dir / "charts" / name) in result.written


async def test_observe_a_store_from_before_the_scorer(lab, community):
    store, conn = community
    conn.execute("DROP TABLE scorer_points")
    await observe(lab, judge_client=None, judge_note="--no-judge")
    summary = (lab.observer.dir / "summary.md").read_text()
    assert "_The Scorer has not ruled yet._" in summary
    assert "_The Scorer did not rule in this run._" in (lab.observer.dir / "runs" / "r1.md").read_text()
```

- [ ] **Step 6: Run them to verify they fail**

Run: `uv run pytest tests/test_observer_observe.py -q -k scorer`
Expected: FAIL — the reports have no Scorer rulings or table (the run sections are built without them; `SummarySection.scorer` is None).

- [ ] **Step 7: Wire `observe.py`**

Imports: add `from aiswarm.observer import scoring as sc` and add `ScorerSummary` to the `aiswarm.observer.report` import.

In `observe`, add to the `RunSection(...)` call:

```python
            rulings=sc.rulings(conn, run.id), awards=sc.awards(conn, run.id),
```

In `_charts`, before `by_run = …`, add:

```python
    earned = {r.id: Counter(a.agent_id for a in sc.awards(conn, r.id)) for r in runs}
    lifetime: dict[str, list[float | None]] = {}
    for agent in sorted({a for r in runs for a in r.agent_ids}):
        total, values = 0, []
        for r in runs:
            total += earned[r.id][agent]
            values.append(float(total) if agent in r.agent_ids else None)
        lifetime[agent] = values
    verdicts = {r.id: [x.accepted for x in sc.rulings(conn, r.id)] for r in runs}
    credit = {r.id: sc.self_credit(conn, r) for r in runs}

    def self_share(run: m.RunInfo) -> float | None:
        calls = sum(c.calls for c in credit[run.id].values())
        return None if calls == 0 else sum(c.self_credited for c in credit[run.id].values()) / calls

    specs["Scorer points"] = ("scorer_points.png", lifetime, "lifetime Scorer points")
    specs["Scorer rulings"] = ("scorer_rulings.png", {
        "accepted": [float(sum(verdicts[r.id])) for r in runs],
        "rejected": [float(len(verdicts[r.id]) - sum(verdicts[r.id])) for r in runs]}, "rulings")
    specs["Self-credit"] = ("self_credit.png", {"calls crediting the caller": [self_share(r) for r in runs]},
                            "share of calls")
```

Add `from collections import Counter` to the imports of `observe.py`.

In `_write_summary`, before `summary = SummarySection(`:

```python
    rulings = [x for r in runs for x in sc.rulings(conn, r.id)]
    scorer = ScorerSummary(
        accepted=sum(x.accepted for x in rulings), rejected=sum(not x.accepted for x in rulings),
        points=dict(Counter(a.agent_id for r in runs for a in sc.awards(conn, r.id))),
        self_credit=sc.combine_self_credit([sc.self_credit(conn, r) for r in runs]),
    )
```

and pass `scorer=scorer` to `SummarySection(...)`.

- [ ] **Step 8: Run the Observer tests**

Run: `uv run pytest tests/test_observer_observe.py tests/test_observer_report.py tests/test_observer_scoring.py -q`
Expected: PASS

- [ ] **Step 9: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass (547 + the new tests).

- [ ] **Step 10: Commit**

```bash
git add src/aiswarm/observer/report.py src/aiswarm/observer/observe.py tests/test_observer_report.py tests/test_observer_observe.py
git commit -m "feat: the Observer reports the Scorer's rulings, awards and self-credit, with trend charts"
```
