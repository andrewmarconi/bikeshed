# Generation 4 New Measurements (C2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The Observer measures copying, credit accuracy, library reuse and norm overlap, and shows the brief slot each agent held in the control-vs-twin table. All of it is deterministic code.

**Architecture:**
- `observer/overlap.py`: pure text matching on 6-word shingles.
- `observer/copying.py`: reads the record and produces matches, call credit checks, library reuse and norm overlaps.
- `observer/briefs.py` gains `slot_label`.
- `report.py` renders the new per-run and summary sections.
- `observe.py` wires them in.

All of it reads only. `events.jsonl` is read with `events.read_events`.

**Tech Stack:** Python 3.14, uv, pytest (asyncio), the `community` fixture, `tests/observer_helpers.py`.

**Spec:** `docs/superpowers/specs/2026-10-15-bikeshed-gen4-new-measurements-design.md`

## Global Constraints

- Feature branch `measure-c2` in the main checkout. Check `pgrep -fl "bikeshed (run|postrun)"` before every commit; if a run is live, stop and ask.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- `SHINGLE = 6`, `PARTIAL = 0.5`.
- Text fields checked: `answer_summary`, `method`, `points`, `text`, `reason`, `change`, `synthesis`, `content`.
- Think notes are never copiers or sources.
- No model calls. Nothing writes to the community store. `test_observer_isolation.py` must pass.
- Processes are never summed together in summary tables.
- Exact report strings:
  - thresholds line: `_Matches: exact, or partial when at least 50% of a text's 6-word sequences appear in one source._`
  - `_No copied text found._`
  - `_Not recorded._`
  - `_No proposal restates an adopted norm._`
  - twin brief values: `slot k`, `shared only`, `–`
- Full suite: `uv run pytest -q` (about 4 minutes). Redirect to a file in the plan workspace and read the tail. The golden test passes now (regenerated in `95d10bc`), so the suite must be fully green.

## Review Focus

1. A long run: shingling must be cached so the Observer doesn't recompute it for every pair. Pinned in Task 1 (`functools.lru_cache` on `_shingles`). The fixture size in Task 2 is modest; the reviewer should check that cost is roughly linear in text volume per pair.
2. A run that resumed has more than one `library_retrieved` event. The lists are unioned per agent. Pinned in Task 3 (`test_library_reuse_unions_resumed_retrievals`).
3. A method id in the event that no longer exists in `methods` (a deleted or older store) is skipped, not a crash. Pinned in Task 3.
4. A call's credits include the caller (self-credit). The caller is never listed as "credited without matching text", because its own text is never a source. Pinned in Task 2.
5. A store without `run_briefs`, `methods` or `norm_proposals` (older schema) gives empty results, not errors. Pinned in Task 3 (the `run_briefs` case). The others are guarded by `has_table`, which the reviewer checks.

---

### Task 1: The text-overlap module

**Files:**
- Create: `src/bikeshed/observer/overlap.py`
- Test: `tests/test_observer_overlap.py`

**Interfaces:**
- Produces: `SHINGLE: int`, `PARTIAL: float`, `THRESHOLDS: str` (the report line), `normalise(text: str) -> str`, `shingles(text: str) -> frozenset[tuple[str, ...]]`, `match(text: str, source: str) -> Literal["exact", "partial"] | None`.

- [ ] **Step 0: Branch**

```bash
pgrep -fl "bikeshed (run|postrun)"; git checkout -b measure-c2
```
Expected: pgrep prints nothing; `Switched to a new branch 'measure-c2'`.

- [ ] **Step 1: Write the failing tests**

`tests/test_observer_overlap.py`:

```python
from bikeshed.observer.overlap import PARTIAL, SHINGLE, THRESHOLDS, match, normalise, shingles

LONG = "we should meet at the park at noon and bring the blue blanket for everyone"


def test_normalise_ignores_case_and_punctuation():
    assert normalise("  Hello,  WORLD!\nIt's me. ") == "hello world it s me"


def test_shingles_need_enough_words():
    assert shingles("one two three four five") == frozenset()
    assert len(shingles("one two three four five six seven")) == 2


def test_an_identical_text_is_exact_whatever_its_length():
    assert match("Same.", "same") == "exact"
    assert match(LONG, LONG.upper()) == "exact"


def test_a_long_text_inside_a_longer_source_is_exact():
    assert match(LONG, "Agreed. " + LONG + " Also snacks.") == "exact"


def test_a_short_text_inside_a_source_is_not_a_match():
    assert match("bring the blanket", LONG) is None


def test_partial_at_the_threshold_and_below():
    words = [f"w{i}" for i in range(16)]           # 11 shingles of 6 words
    text = " ".join(words)
    source = " ".join(words[:11] + ["x", "y", "z", "q", "r"])  # shares w0..w10: 6 shingles of 11
    assert 6 / 11 >= PARTIAL and match(text, source) == "partial"
    source = " ".join(words[:10] + ["x", "y", "z", "q", "r", "s"])  # shares w0..w9: 5 of 11
    assert 5 / 11 < PARTIAL and match(text, source) is None


def test_an_empty_text_never_matches():
    assert match("", "anything") is None
    assert match("   ", "") is None


def test_the_thresholds_line():
    assert SHINGLE == 6 and PARTIAL == 0.5
    assert THRESHOLDS == "_Matches: exact, or partial when at least 50% of a text's 6-word sequences appear in one source._"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_observer_overlap.py -q 2>&1 | tail -3`
Expected: collection ERROR, `ModuleNotFoundError: No module named 'bikeshed.observer.overlap'`.

- [ ] **Step 3: Implement**

`src/bikeshed/observer/overlap.py`:

```python
"""How much of one text comes from another: word shingles, compared in plain code. No model, no I/O."""

import re
from functools import lru_cache
from typing import Literal

SHINGLE = 6     # words per sequence
PARTIAL = 0.5   # share of a text's sequences found in one source for a partial match
THRESHOLDS = (f"_Matches: exact, or partial when at least {PARTIAL:.0%} of a text's {SHINGLE}-word sequences "
              f"appear in one source._")

Kind = Literal["exact", "partial"]


@lru_cache(maxsize=None)
def normalise(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]|_", " ", text.lower()).split())


@lru_cache(maxsize=None)
def shingles(text: str) -> frozenset[tuple[str, ...]]:
    words = normalise(text).split()
    return frozenset(tuple(words[i:i + SHINGLE]) for i in range(len(words) - SHINGLE + 1))


def match(text: str, source: str) -> Kind | None:
    """How much of `text` comes from `source`: exact (all of it), partial (at least PARTIAL of its sequences), or None."""
    t, s = normalise(text), normalise(source)
    if not t:
        return None
    if t == s or (len(t.split()) >= SHINGLE and f" {t} " in f" {s} "):
        return "exact"
    own = shingles(text)
    if own and len(own & shingles(source)) / len(own) >= PARTIAL:
        return "partial"
    return None
```

- [ ] **Step 4: Run them to verify they pass**

Run: `uv run pytest tests/test_observer_overlap.py -q 2>&1 | tail -3`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
pgrep -fl "bikeshed (run|postrun)"
git add src/bikeshed/observer/overlap.py tests/test_observer_overlap.py
git commit -m "feat: observer text overlap on six-word sequences

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Copying and credit accuracy

**Files:**
- Create: `src/bikeshed/observer/copying.py`
- Test: `tests/test_observer_copying.py`

**Interfaces:**
- Consumes: `overlap.match`.
- Produces:
  - `TEXT_FIELDS: tuple[str, ...]`
  - `@dataclass(frozen=True) Match(copier: str, entry_id: int, entry_type: str, field: str, source_author: str, source_id: int, kind: str, same_round: bool)`
  - `copies(conn: sqlite3.Connection, run: RunInfo) -> list[Match]`
  - `@dataclass(frozen=True) CallCredit(call_id: int, caller: str, copied: tuple[str, ...], credited: tuple[str, ...], uncredited: tuple[str, ...], unmatched: tuple[str, ...])`
  - `credit_accuracy(conn: sqlite3.Connection, run: RunInfo, found: list[Match]) -> list[CallCredit]`

- [ ] **Step 1: Write the failing tests**

`tests/test_observer_copying.py`:

```python
from observer_helpers import add_entry, add_run

from bikeshed.observer.copying import copies, credit_accuracy
from bikeshed.observer.metrics import closed_runs

REASON = "it meets every constraint and nobody has objected to the plan so far"


def run(conn):
    return closed_runs(conn)[0]


def test_a_pasted_vote_reason_is_exact_and_tagged_by_round(community):
    _, conn = community
    add_run(conn, "r1", process=2)
    add_entry(conn, "r1", "b", "vote", {"target": 1, "stance": "support", "reason": REASON}, round=2)
    same = add_entry(conn, "r1", "a", "vote", {"target": 1, "stance": "support", "reason": REASON}, round=2)
    later = add_entry(conn, "r1", "c", "vote", {"target": 1, "stance": "support", "reason": REASON}, round=3)
    found = {(m.entry_id, m.source_author): m for m in copies(conn, run(conn))}
    assert found[(same, "b")].kind == "exact" and found[(same, "b")].same_round
    assert found[(later, "b")].field == "reason" and not found[(later, "b")].same_round
    assert found[(later, "a")].kind == "exact"


def test_a_received_private_message_is_a_source_but_others_messages_are_not(community):
    _, conn = community
    add_run(conn, "r1")
    add_entry(conn, "r1", "b", "private_message", {"to": "a", "text": REASON}, visible_to=["b", "a"], to="a")
    add_entry(conn, "r1", "c", "private_message", {"to": "b", "text": REASON + " again"}, visible_to=["c", "b"], to="b")
    pasted = add_entry(conn, "r1", "a", "propose", {"answer_summary": REASON, "method": "m"}, round=2)
    sources = {m.source_author for m in copies(conn, run(conn)) if m.entry_id == pasted}
    assert sources == {"b"}


def test_think_notes_own_text_and_failures_are_left_out(community):
    _, conn = community
    add_run(conn, "r1", process=2)
    add_entry(conn, "r1", "b", "think", {"text": REASON}, visible_to=["b"])
    add_entry(conn, "r1", "a", "propose", {"answer_summary": REASON, "method": "m"})
    add_entry(conn, "r1", "a", "critique", {"target": 1, "about": "answer", "points": REASON}, round=2)
    add_entry(conn, "r1", "c", "think", {"text": REASON}, round=2, visible_to=["c"])
    add_entry(conn, "r1", "c", "pass", {"failure": "model_error"}, round=2)
    assert copies(conn, run(conn)) == []


def test_credit_accuracy_lists_uncredited_sources_and_credits_without_text(community):
    _, conn = community
    add_run(conn, "r1")
    add_entry(conn, "r1", "b", "propose", {"answer_summary": "Picnic", "method": REASON})
    call = add_entry(conn, "r1", "a", "call_consensus", {
        "synthesis": "Picnic: " + REASON, "refs": [], "credits": [
            {"agent_id": "c", "contribution": "ideas"}, {"agent_id": "a", "contribution": "synthesis"}]}, round=2)
    (check,) = credit_accuracy(conn, run(conn), copies(conn, run(conn)))
    assert check.call_id == call and check.caller == "a"
    assert check.copied == ("b",) and check.credited == ("a", "c")
    assert check.uncredited == ("b",) and check.unmatched == ("c",)  # the caller is never "without matching text"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_observer_copying.py -q 2>&1 | tail -3`
Expected: collection ERROR, `ModuleNotFoundError: No module named 'bikeshed.observer.copying'`.

- [ ] **Step 3: Implement**

`src/bikeshed/observer/copying.py`:

```python
"""Text that reappears: copying between members, credit for copied text, library reuse and norm overlap.
Plain code over the record; no model, no writes."""

import sqlite3
from dataclasses import dataclass

from bikeshed.board import Board, Entry
from bikeshed.observer.metrics import RunInfo
from bikeshed.observer.overlap import match

TEXT_FIELDS = ("answer_summary", "method", "points", "text", "reason", "change", "synthesis", "content")


@dataclass(frozen=True)
class Match:
    copier: str
    entry_id: int
    entry_type: str
    field: str
    source_author: str
    source_id: int
    kind: str          # exact | partial
    same_round: bool   # in a blind phase a same-round match is convergence, not copying


def _texts(entry: Entry) -> list[tuple[str, str]]:
    return [(f, entry.payload[f]) for f in TEXT_FIELDS
            if isinstance(entry.payload.get(f), str) and entry.payload[f].strip()]


def _copier(entry: Entry, members: set[str]) -> bool:
    """Public entries and sent private messages by members; never think notes or failed turns."""
    if entry.author not in members or entry.type in ("think", "pass"):
        return False
    return entry.visible_to is None or entry.type == "private_message"


def copies(conn: sqlite3.Connection, run: RunInfo) -> list[Match]:
    entries = Board(conn, run.id).entries()
    members = set(run.agent_ids)
    texts = {e.id: _texts(e) for e in entries}
    out = []
    for i, entry in enumerate(entries):
        if not _copier(entry, members):
            continue
        sources = [s for s in entries[:i] if s.author in members and s.author != entry.author and s.type != "think"
                   and (s.visible_to is None or entry.author in s.visible_to) and texts[s.id]]
        for field, text in texts[entry.id]:
            for source in sources:
                kinds = {match(text, src) for _, src in texts[source.id]} - {None}
                if kinds:
                    out.append(Match(entry.author, entry.id, entry.type, field, source.author, source.id,
                                     "exact" if "exact" in kinds else "partial", source.round == entry.round))
    return out


@dataclass(frozen=True)
class CallCredit:
    call_id: int
    caller: str
    copied: tuple[str, ...]      # authors whose earlier text the synthesis matches
    credited: tuple[str, ...]
    uncredited: tuple[str, ...]  # copied, not credited
    unmatched: tuple[str, ...]   # credited, but none of their text is in the synthesis (not an error: ideas move)


def credit_accuracy(conn: sqlite3.Connection, run: RunInfo, found: list[Match]) -> list[CallCredit]:
    out = []
    for call in Board(conn, run.id).entries():
        if call.type != "call_consensus" or call.author not in run.agent_ids:
            continue
        copied = {m.source_author for m in found if m.entry_id == call.id and m.field == "synthesis"}
        credited = {c["agent_id"] for c in call.payload.get("credits", []) if c.get("agent_id")}
        out.append(CallCredit(call.id, call.author, tuple(sorted(copied)), tuple(sorted(credited)),
                              tuple(sorted(copied - credited)), tuple(sorted(credited - copied - {call.author}))))
    return out
```

- [ ] **Step 4: Run them to verify they pass**

Run: `uv run pytest tests/test_observer_copying.py -q 2>&1 | tail -3`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
pgrep -fl "bikeshed (run|postrun)"
git add src/bikeshed/observer/copying.py tests/test_observer_copying.py
git commit -m "feat: the Observer finds copied text between members and checks calls' credits against it

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Library reuse, norm overlap and the brief slot

**Files:**
- Modify: `src/bikeshed/observer/copying.py` (add `Reuse`, `library_reuse`, `NormOverlap`, `norm_overlaps`)
- Modify: `src/bikeshed/observer/briefs.py` (add `slot_label`)
- Test: `tests/test_observer_copying.py`, `tests/test_observer_briefs.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) Reuse(method_id: int, method_run: str, method_author: str, shown_to: tuple[str, ...], reused_by: tuple[tuple[str, int, str], ...])`, where each `reused_by` entry is (agent, entry id, kind)
  - `library_reuse(conn: sqlite3.Connection, run: RunInfo, runs_dir: Path) -> list[Reuse] | None` (None: not recorded)
  - `@dataclass(frozen=True) NormOverlap(proposal_id: int, run_id: str, author: str, norm_id: int, norm_text: str, kind: str, status: str)`
  - `norm_overlaps(conn: sqlite3.Connection, runs: list[RunInfo]) -> list[NormOverlap]`
  - `briefs.slot_label(conn: sqlite3.Connection, run_id: str, agent_id: str) -> str`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_observer_copying.py`, extending the imports with `json`, `library_reuse`, `norm_overlaps`:

```python
def _method(conn, method_id, run_id, author, text):
    conn.execute("INSERT INTO methods (id, run_id, entry_id, author, kind, goal, text, reception, created_at) "
                 "VALUES (?, ?, ?, ?, 'propose', 'g', ?, '{}', 't')", (method_id, run_id, method_id, author, text))


def _events(tmp_path, run_id, *events):
    path = tmp_path / "runs" / run_id
    path.mkdir(parents=True, exist_ok=True)
    (path / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    return tmp_path / "runs"


def test_library_reuse_finds_a_shown_method_in_a_proposal(community, tmp_path):
    _, conn = community
    add_run(conn, "old", created="2000-01-01T00:00:00+00:00")
    add_run(conn, "r1", agents=("a", "b"))
    _method(conn, 7, "old", "c", REASON)
    _method(conn, 8, "old", "c", "something else entirely different from anything posted here today")
    reused = add_entry(conn, "r1", "a", "propose", {"answer_summary": "x", "method": REASON})
    runs_dir = _events(tmp_path, "r1", {"event": "library_retrieved", "methods": [7, 8]})
    got = {r.method_id: r for r in library_reuse(conn, closed_runs(conn)[1], runs_dir)}
    assert got[7].shown_to == ("a", "b") and got[7].reused_by == (("a", reused, "exact"),)
    assert got[8].reused_by == () and got[7].method_author == "c" and got[7].method_run == "old"


def test_a_control_is_checked_against_its_own_list(community, tmp_path):
    _, conn = community
    add_run(conn, "r1", agents=("a", "e"))
    _method(conn, 7, "r1", "c", REASON)
    add_entry(conn, "r1", "e", "propose", {"answer_summary": "x", "method": REASON})
    runs_dir = _events(tmp_path, "r1", {"event": "library_retrieved", "methods": [7], "control_methods": {"e": []}})
    (only,) = library_reuse(conn, closed_runs(conn)[0], runs_dir)
    assert only.shown_to == ("a",) and only.reused_by == ()


def test_library_reuse_unions_resumed_retrievals_and_skips_unknown_methods(community, tmp_path):
    _, conn = community
    add_run(conn, "r1", agents=("a",))
    _method(conn, 7, "r1", "c", REASON)
    runs_dir = _events(tmp_path, "r1", {"event": "library_retrieved", "methods": [99]},
                       {"event": "library_retrieved", "methods": [7]})
    assert [r.method_id for r in library_reuse(conn, closed_runs(conn)[0], runs_dir)] == [7]


def test_library_reuse_not_recorded_without_events(community, tmp_path):
    _, conn = community
    add_run(conn, "r1")
    assert library_reuse(conn, closed_runs(conn)[0], tmp_path / "runs") is None
    runs_dir = _events(tmp_path, "r1", {"event": "run_started"})
    assert library_reuse(conn, closed_runs(conn)[0], runs_dir) is None
    runs_dir = _events(tmp_path, "r1", {"event": "library_unavailable", "detail": "down"})
    assert library_reuse(conn, closed_runs(conn)[0], runs_dir) == []


NORM = "members should cite the entry they are responding to whenever they disagree with it"


def _proposal(conn, run_id, author, text, status, created, kind="norm"):
    return conn.execute(
        "INSERT INTO norm_proposals (run_id, author, kind, text, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (run_id, author, kind, text, status, created)).lastrowid


def test_a_proposal_restating_an_earlier_adopted_norm_is_listed_with_its_outcome(community):
    _, conn = community
    add_run(conn, "r1", created="2000-01-01T00:00:00+00:00")
    add_run(conn, "r2", created="2000-02-01T00:00:00+00:00")
    adopted = _proposal(conn, "r1", "a", NORM, "adopted", "2000-01-01T00:00:00+00:00")
    again = _proposal(conn, "r2", "b", "Norm: " + NORM, "rejected", "2000-02-01T00:00:00+00:00")
    _proposal(conn, "r2", "c", "rate clarity", "open", "2000-02-01T00:00:00+00:00", kind="dimension")
    (o,) = norm_overlaps(conn, closed_runs(conn))
    assert (o.proposal_id, o.norm_id, o.kind, o.status, o.author) == (again, adopted, "exact", "rejected", "b")


def test_a_norm_adopted_after_the_proposal_does_not_count(community):
    _, conn = community
    add_run(conn, "r1", created="2000-02-01T00:00:00+00:00")
    add_run(conn, "r2", created="2000-01-01T00:00:00+00:00")
    _proposal(conn, "r1", "a", NORM, "adopted", "2000-02-01T00:00:00+00:00")
    _proposal(conn, "r2", "b", NORM, "rejected", "2000-01-01T00:00:00+00:00")
    assert [o for o in norm_overlaps(conn, closed_runs(conn)) if o.author == "b"] == []
```

Append to `tests/test_observer_briefs.py`, importing `slot_label` from `bikeshed.observer.briefs` and `add_briefs`/`add_run` from `observer_helpers` if they aren't already imported, plus `Brief`, `Constraint`, `RunBriefs` from `bikeshed.briefs`:

```python
def test_slot_label_names_the_slot_shared_only_or_unbriefed(community):
    _, conn = community
    add_run(conn, "r1")
    add_briefs(conn, "r1", RunBriefs(Brief(0, None, "shared", ()), (Brief(1, "a", "private one", ()),)))
    add_run(conn, "r2")
    assert slot_label(conn, "r1", "a") == "slot 1"
    assert slot_label(conn, "r1", "b") == "shared only"
    assert slot_label(conn, "r2", "a") == "–"
    conn.execute("DROP TABLE run_briefs")
    assert slot_label(conn, "r1", "a") == "–"
```

(Check the `RunBriefs`, `Brief` and `Constraint` constructor shapes in `src/bikeshed/briefs.py` before writing this. If they differ from `RunBriefs(shared, private)` and `Brief(slot, agent_id, text, constraints)`, adapt the call, and ledger a ruling.)

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_observer_copying.py tests/test_observer_briefs.py -q 2>&1 | tail -3`
Expected: collection ERROR, `ImportError: cannot import name 'library_reuse'`. After that is resolved, `slot_label` raises the same kind of error.

- [ ] **Step 3: Implement**

Append to `src/bikeshed/observer/copying.py`, adding imports `from datetime import datetime`, `from pathlib import Path`, `from bikeshed.db import has_table`, `from bikeshed.events import read_events`:

```python
@dataclass(frozen=True)
class Reuse:
    method_id: int
    method_run: str
    method_author: str
    shown_to: tuple[str, ...]
    reused_by: tuple[tuple[str, int, str], ...]  # (agent, entry id, kind)


def library_reuse(conn: sqlite3.Connection, run: RunInfo, runs_dir: Path) -> list[Reuse] | None:
    """Which library methods each member was shown, and whether its proposals or refines reuse them.
    None when the run's events don't record a retrieval."""
    path = runs_dir / run.id / "events.jsonl"
    events = read_events(path) if path.exists() else []
    retrieved = [e for e in events if e.get("event") == "library_retrieved"]
    if not retrieved:
        return [] if any(e.get("event") == "library_unavailable" for e in events) else None
    shown: dict[str, set[int]] = {agent: set() for agent in run.agent_ids}
    for event in retrieved:  # a resumed run retrieves again; a member was shown everything it was ever given
        own = event.get("control_methods", {})
        for agent in run.agent_ids:
            shown[agent] |= set(own.get(agent, event.get("methods", [])))
    ids = sorted(set().union(*shown.values()))
    if not ids or not has_table(conn, "methods"):
        return []
    rows = {r["id"]: r for r in conn.execute(
        f"SELECT id, run_id, author, text FROM methods WHERE id IN ({', '.join('?' * len(ids))})", ids)}
    entries = [e for e in Board(conn, run.id).entries() if e.visible_to is None and e.type in ("propose", "refine")]
    out = []
    for method_id in ids:
        row = rows.get(method_id)
        if row is None:  # gone from this store
            continue
        to = tuple(a for a in run.agent_ids if method_id in shown[a])
        reused = []
        for e in entries:
            text = e.payload.get("method" if e.type == "propose" else "change", "")
            if e.author in to and isinstance(text, str) and (kind := match(text, row["text"])):
                reused.append((e.author, e.id, kind))
        out.append(Reuse(method_id, row["run_id"], row["author"], to, tuple(reused)))
    return out


@dataclass(frozen=True)
class NormOverlap:
    proposal_id: int
    run_id: str
    author: str
    norm_id: int
    norm_text: str
    kind: str
    status: str


def norm_overlaps(conn: sqlite3.Connection, runs: list[RunInfo]) -> list[NormOverlap]:
    """Norm and amendment proposals whose text overlaps a norm adopted before they were made. A norm is adopted in
    its run's post-run, so "before" means its run ended (post-run included) before the proposal was created."""
    if not has_table(conn, "norm_proposals"):
        return []
    ends = {r.id: datetime.fromisoformat(r.end) for r in runs}
    rows = conn.execute("SELECT * FROM norm_proposals WHERE kind IN ('norm', 'amendment') ORDER BY id").fetchall()
    adopted = [r for r in rows if r["status"] == "adopted"]
    out = []
    for p in rows:
        made = datetime.fromisoformat(p["created_at"])
        best = None
        for n in adopted:
            if n["id"] == p["id"] or n["run_id"] not in ends or not ends[n["run_id"]] < made:
                continue
            kind = match(p["text"], n["text"])
            if kind and (best is None or (kind == "exact" and best[1] != "exact")):
                best = (n, kind)
        if best:
            out.append(NormOverlap(p["id"], p["run_id"], p["author"], best[0]["id"], best[0]["text"], best[1],
                                   p["status"]))
    return out
```

Append to `src/bikeshed/observer/briefs.py` (import `has_table` from `bikeshed.db` if it isn't already):

```python
def slot_label(conn: sqlite3.Connection, run_id: str, agent_id: str) -> str:
    """Which private brief an agent held in a run: "slot k", "shared only", or "–" for an unbriefed run."""
    if not has_table(conn, "run_briefs"):
        return "–"
    rows = conn.execute("SELECT slot, agent_id FROM run_briefs WHERE run_id = ?", (run_id,)).fetchall()
    if not rows:
        return "–"
    held = [r["slot"] for r in rows if r["slot"] >= 1 and r["agent_id"] == agent_id]
    return f"slot {held[0]}" if held else "shared only"
```

- [ ] **Step 4: Run them to verify they pass**

Run: `uv run pytest tests/test_observer_copying.py tests/test_observer_briefs.py -q 2>&1 | tail -3`
Expected: all pass. In the norm-overlap test, `closed_runs` gives each run `end` = `created_at` (no entries or post-run), so r1 ends 2000-01-01, before the r2 proposal.

- [ ] **Step 5: Commit**

```bash
pgrep -fl "bikeshed (run|postrun)"
git add src/bikeshed/observer/copying.py src/bikeshed/observer/briefs.py tests/test_observer_copying.py tests/test_observer_briefs.py
git commit -m "feat: the Observer measures library reuse and norm overlap, and names each agent's brief slot

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Per-run report sections

**Files:**
- Modify: `src/bikeshed/observer/report.py` (`RunSection` fields; `_credits`; new `_copying`, `_library`; `render_run`)
- Modify: `src/bikeshed/observer/observe.py` (fill the fields)
- Test: `tests/test_observer_report.py`, `tests/test_observer_observe.py`

**Interfaces:**
- Consumes: `Match`, `CallCredit`, `Reuse`, `THRESHOLDS`.
- Produces: `RunSection.copies: list[Match]`, `RunSection.credit_checks: list[CallCredit]`, `RunSection.reuse: list[Reuse] | None`. All three default to "nothing" (`[]`, `[]`, `None`).

- [ ] **Step 1: Write the failing tests**

In `tests/test_observer_report.py` (import `Match`, `CallCredit`, `Reuse` from `bikeshed.observer.copying`):

```python
def test_run_report_shows_copying_credit_checks_and_library_reuse():
    copied = [Match("a", 9, "vote", "reason", "b", 7, "exact", True)]
    checks = [CallCredit(11, "a", ("b",), ("a", "c"), ("b",), ("c",))]
    reuse = [Reuse(4, "r0", "b", ("a", "b"), (("a", 8, "partial"),)), Reuse(5, "r0", "b", ("a",), ())]
    text = render_run(section(copies=copied, credit_checks=checks, reuse=reuse))
    block = text.split("## Copying\n")[1].split("\n## ")[0]
    assert "| Alpha (a) | #9 vote.reason | #7 Bea (b) | exact | same round |" in block
    assert "_Matches: exact, or partial when at least 50%" in block
    assert ("- #11 by Alpha (a): copied from Bea (b); credited Alpha (a), c; uncredited: Bea (b); "
            "credited without matching text: c") in text
    lib = text.split("## Library reuse\n")[1].split("\n## ")[0]
    assert "- method #4 (run r0, by Bea (b)), shown to Alpha (a), Bea (b): reused by Alpha (a) in #8 (partial)" in lib
    assert "- method #5 (run r0, by Bea (b)), shown to Alpha (a): not reused" in lib


def test_run_report_without_copies_or_a_library_record():
    text = render_run(section())
    assert "_No copied text found._" in text.split("## Copying\n")[1]
    assert "_Not recorded._" in text.split("## Library reuse\n")[1]
```

In `tests/test_observer_observe.py`, add:

```python
async def test_observe_reports_copied_text(lab, community):
    store, conn = community
    reason = "it meets every constraint and nobody has objected to the plan so far"
    add_run(conn, "r8", created=FUTURE, goal_id="copy")
    add_entry(conn, "r8", "b", "vote", {"target": 1, "stance": "support", "reason": reason})
    add_entry(conn, "r8", "a", "vote", {"target": 1, "stance": "support", "reason": reason}, round=2)
    await observe(lab, judge_note="--no-judge")
    report = (lab.observer.dir / "runs" / "r8.md").read_text()
    assert "| Alpha (a) |" in report.split("## Copying\n")[1] and "earlier round" in report
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_observer_report.py tests/test_observer_observe.py -q -k "copying or copies or copied" 2>&1 | tail -4`
Expected: FAIL. `RunSection` rejects `copies`, and the observe report has no `## Copying`.

- [ ] **Step 3: Implement**

`report.py`: import `from bikeshed.observer.copying import CallCredit, Match, Reuse` and `from bikeshed.observer.overlap import THRESHOLDS`. Add to `RunSection`, after `controls`:

```python
    copies: list[Match] = field(default_factory=list)
    credit_checks: list[CallCredit] = field(default_factory=list)
    reuse: list[Reuse] | None = None  # None: the run's events don't record a library retrieval
```

Leave `_credits` as it is. Add these helpers; `render_run` calls `_credit_checks` straight after `_credits`, so the block appears whether or not the run closed on a call:

```python
def _credit_checks(s: RunSection) -> list[str]:
    if not s.credit_checks:
        return []
    names = lambda ids: ", ".join(s.who(a) for a in ids) or "no one"
    out = ["", "### Credit accuracy", ""]
    for c in s.credit_checks:
        line = f"- #{c.call_id} by {s.who(c.caller)}: copied from {names(c.copied)}; credited {names(c.credited)}"
        if c.uncredited:
            line += f"; uncredited: {names(c.uncredited)}"
        if c.unmatched:
            line += f"; credited without matching text: {names(c.unmatched)}"
        out.append(line)
    return out


def _copying(s: RunSection) -> list[str]:
    out = ["## Copying", ""]
    if not s.copies:
        return out + ["_No copied text found._", "", THRESHOLDS, ""]
    out += ["| Copier | Entry | Source | Match | Round |", "|---|---|---|---|---|"]
    out += [f"| {s.who(m.copier)} | #{m.entry_id} {m.entry_type}.{m.field} | #{m.source_id} {s.who(m.source_author)} | "
            f"{m.kind} | {'same round' if m.same_round else 'earlier round'} |" for m in s.copies]
    return out + ["", THRESHOLDS, ""]


def _library(s: RunSection) -> list[str]:
    out = ["## Library reuse", ""]
    if s.reuse is None:
        return out + ["_Not recorded._", ""]
    if not s.reuse:
        return out + ["_No methods were shown._", ""]
    for r in s.reuse:
        shown = ", ".join(s.who(a) for a in r.shown_to)
        used = "; ".join(f"{s.who(a)} in #{e} ({k})" for a, e, k in r.reused_by)
        out.append(f"- method #{r.method_id} (run {r.method_run}, by {s.who(r.method_author)}), shown to {shown}: "
                   + (f"reused by {used}" if used else "not reused"))
    return out + ["", THRESHOLDS, ""]
```

(In the expected test line, `c` renders as `c` because the test's `who` maps unknown ids to themselves.) In `render_run`, change

```python
    out += [""] + _metrics(s) + [""] + _dimensions(s) + _credits(s) + [""] + _scorer_run(s) + _briefs(s)
```

to

```python
    out += [""] + _metrics(s) + [""] + _dimensions(s) + _credits(s) + _credit_checks(s) + [""] + _scorer_run(s) \
        + _briefs(s) + _copying(s) + _library(s)
```

`observe.py`: import `from bikeshed.observer import copying as cp`. Before building `RunSection`:

```python
        found = cp.copies(conn, run)
```

and pass `copies=found, credit_checks=cp.credit_accuracy(conn, run, found), reuse=cp.library_reuse(conn, run, cfg.runs_dir)`.

- [ ] **Step 4: Run the observer tests**

Run: `uv run pytest tests/test_observer_report.py tests/test_observer_observe.py tests/test_observer_isolation.py -q 2>&1 | tail -3`
Expected: all pass. A test that splits the report on `"\n## "` and now sees a new section in its slice gets a mechanical update; ledger each one as a ruling.

- [ ] **Step 5: Commit**

```bash
pgrep -fl "bikeshed (run|postrun)"
git add src/bikeshed/observer/report.py src/bikeshed/observer/observe.py tests/test_observer_report.py tests/test_observer_observe.py
git commit -m "feat: run reports show copied text, credit accuracy and library reuse

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Summary sections and the twin's brief slot

**Files:**
- Modify: `src/bikeshed/observer/report.py` (`SummarySection` fields; `_control_vs_twin`; new summary blocks in `render_summary`)
- Modify: `src/bikeshed/observer/observe.py` (`_write_summary`)
- Test: `tests/test_observer_report.py`, `tests/test_observer_observe.py`

**Interfaces:**
- Produces:
  - `SummarySection.copying: dict[int, Counter]`: process → Counter of `(copier, source, kind)`
  - `SummarySection.credit_gaps: dict[int, tuple[int, int]]`: process → (calls leaving out a copied source, calls)
  - `SummarySection.reuse: dict[int, tuple[int, int]]`: process → (shown methods reused, shown methods), counting (run, method) pairs
  - `SummarySection.methods_shown: list[tuple[int, int, int]]`: (method id, runs shown, runs reused), most shown first
  - `SummarySection.norm_overlaps: list[NormOverlap]`
  - twin rows become `(label, agent, peer, mix, brief)`

- [ ] **Step 1: Write the failing tests**

In `tests/test_observer_report.py` (import `NormOverlap`; build a `SummarySection` the way existing summary tests do. Find one with `grep -n "SummarySection(" tests/test_observer_report.py` and copy its arguments into a helper `summary(**kw)` if none exists):

```python
def test_summary_shows_copying_credit_gaps_library_reuse_and_norm_overlap():
    text = render_summary(summary(
        copying={2: Counter({("a", "b", "exact"): 3, ("a", "b", "partial"): 1})},
        credit_gaps={2: (1, 4)}, reuse={2: (2, 6)}, methods_shown=[(4, 5, 2)],
        norm_overlaps=[NormOverlap(14, "r10", "a", 1, "Cite the entry you disagree with.", "partial", "rejected")]))
    copying = text.split("## Copying\n")[1].split("\n## ")[0]
    assert "### Process 2" in copying and "| Alpha (a) | Bea (b) | 3 | 1 |" in copying
    assert "Process 2: 1 of 4 calls left out a copied source." in text
    lib = text.split("## Library reuse\n")[1].split("\n## ")[0]
    assert "Process 2: 2 of 6 shown methods reused." in lib and "| #4 | 5 | 2 |" in lib
    overlap = text.split("## Norm overlap\n")[1].split("\n## ")[0]
    assert "| #14 | r10 | Alpha (a) | #1 Cite the entry you disagree with. | partial | rejected |" in overlap


def test_summary_without_norm_overlap():
    assert "_No proposal restates an adopted norm._" in render_summary(summary())
```

In `tests/test_observer_observe.py`, extend `test_summary_compares_the_control_with_its_twin`. Its rows now end with a brief column, so the existing assertions become:

```python
    assert "| best-fruit | Bea (b) | 4.0 |" in section and section.count("| – |") >= 2
```

Keep the existing `dishwasher` assertion, and add `"| brief |" in section`.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_observer_report.py tests/test_observer_observe.py -q -k "summary" 2>&1 | tail -4`
Expected: FAIL. `SummarySection` rejects the new fields, and there's no `brief` column.

- [ ] **Step 3: Implement**

`report.py`: add to `SummarySection` (import `NormOverlap`):

```python
    copying: dict[int, Counter] = field(default_factory=dict)  # process -> (copier, source, kind) counts
    credit_gaps: dict[int, tuple[int, int]] = field(default_factory=dict)  # process -> (calls missing a source, calls)
    reuse: dict[int, tuple[int, int]] = field(default_factory=dict)  # process -> (shown methods reused, shown)
    methods_shown: list[tuple[int, int, int]] = field(default_factory=list)  # (method, runs shown, runs reused)
    norm_overlaps: list[NormOverlap] = field(default_factory=list)
```

Change the `twin_rows` type comment and annotation to `(run label, agent id, mean peer score, action counts, brief)`. In `_control_vs_twin`:

```python
        out += ["| Run | Agent | peer | " + " | ".join(TWIN_ACTIONS) + " | brief |",
                "|---" * (len(TWIN_ACTIONS) + 4) + "|"]
        out += [f"| {label} | {s.who(agent)} | {_fmt(peer)} | " + " | ".join(str(mix.get(a, 0)) for a in TWIN_ACTIONS)
                + f" | {brief} |" for label, agent, peer, mix, brief in rows]
```

Add a helper and call it in `render_summary`, right after the `## Credit reciprocity` block:

```python
def _text_reuse(s: SummarySection) -> list[str]:
    out = []
    for process, (gaps, calls) in sorted(s.credit_gaps.items()):
        out.append(f"Process {process}: {gaps} of {calls} calls left out a copied source.")
    out += ["", "## Copying", ""]
    if not any(s.copying.values()):
        out += ["_No copied text found._", ""]
    for process, counts in sorted(s.copying.items()):
        pairs = sorted({(c, src) for c, src, _ in counts})
        out += [f"### Process {process}", "", "| Copier | Source | exact | partial |", "|---|---|---|---|"]
        out += [f"| {s.who(c)} | {s.who(src)} | {counts[(c, src, 'exact')]} | {counts[(c, src, 'partial')]} |"
                for c, src in pairs] + [""]
    out += [THRESHOLDS, "", "## Library reuse", ""]
    out += [f"Process {p}: {used} of {shown} shown methods reused." for p, (used, shown) in sorted(s.reuse.items())] \
        or ["_Not recorded._"]
    if s.methods_shown:
        out += ["", "| Method | Runs shown | Runs reused |", "|---|---|---|"]
        out += [f"| #{m} | {shown} | {used} |" for m, shown, used in s.methods_shown]
    out += ["", "## Norm overlap", ""]
    if not s.norm_overlaps:
        return out + ["_No proposal restates an adopted norm._", ""]
    out += ["| Proposal | Run | Author | Overlaps | Match | Outcome |", "|---|---|---|---|---|---|"]
    clip = lambda t: _cell(t if len(t) <= 120 else t[:120] + "…")
    out += [f"| #{o.proposal_id} | {o.run_id} | {s.who(o.author)} | #{o.norm_id} {clip(o.norm_text)} | {o.kind} | "
            f"{o.status} |" for o in s.norm_overlaps]
    return out + [""]
```

In `render_summary`, replace

```python
    out += [f"- {run}: {s.who(g)} → {s.who(r)}" for run, g, r in s.reciprocity] or ["_No credits yet._"]
```

with

```python
    out += [f"- {run}: {s.who(g)} → {s.who(r)}" for run, g, r in s.reciprocity] or ["_No credits yet._"]
    out += [""] + _text_reuse(s)
```

`observe.py`, in `_write_summary`:
- In the twin rows loop, the row becomes `(run.tag, agent, peer.get(agent), mix[agent], pb.slot_label(conn, run.id, agent))`.
- Before `summary = SummarySection(...)`:

```python
    copying: dict[int, Counter] = {}
    credit_gaps: dict[int, tuple[int, int]] = {}
    reuse: dict[int, tuple[int, int]] = {}
    shown_count: Counter = Counter()
    reused_count: Counter = Counter()
    for r in runs:
        found = cp.copies(conn, r)
        copying.setdefault(r.process, Counter()).update((m.copier, m.source_author, m.kind) for m in found)
        checks = cp.credit_accuracy(conn, r, found)
        gaps, calls = credit_gaps.get(r.process, (0, 0))
        credit_gaps[r.process] = (gaps + sum(bool(c.uncredited) for c in checks), calls + len(checks))
        got = cp.library_reuse(conn, r, runs_dir)
        if got is not None:
            used, shown = reuse.get(r.process, (0, 0))
            reuse[r.process] = (used + sum(bool(x.reused_by) for x in got), shown + len(got))
            shown_count.update(x.method_id for x in got)
            reused_count.update(x.method_id for x in got if x.reused_by)
    credit_gaps = {p: v for p, v in credit_gaps.items() if v[1]}
    methods_shown = sorted(((m, n, reused_count[m]) for m, n in shown_count.items()), key=lambda t: (-t[1], t[0]))
```

and pass `copying=copying, credit_gaps=credit_gaps, reuse=reuse, methods_shown=methods_shown, norm_overlaps=cp.norm_overlaps(conn, runs)`.

`_write_summary` doesn't receive `cfg` now. Add a `runs_dir: Path` parameter, and pass `cfg.runs_dir` from `observe`.

- [ ] **Step 4: Run the observer tests**

Run: `uv run pytest tests/test_observer_report.py tests/test_observer_observe.py tests/test_observer_isolation.py -q 2>&1 | tail -3`
Expected: all pass. Any test that builds `twin_rows` with 4-tuples gets a fifth element `"–"`; ledger it as a ruling.

- [ ] **Step 5: Commit**

```bash
pgrep -fl "bikeshed (run|postrun)"
git add src/bikeshed/observer/report.py src/bikeshed/observer/observe.py tests/test_observer_report.py tests/test_observer_observe.py
git commit -m "feat: the summary counts copying, credit gaps, library reuse and norm overlap; the twin table shows brief slots

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Record the outcomes

**Files:**
- Modify: `docs/gen4-fixes.md`

- [ ] **Step 1: Add outcome lines**

Each goes after the fix's last paragraph, matching that fix's indentation (3 spaces for one-digit fixes, 4 for two-digit):

- Fix 7: `*Done in sub-project C2 (`2026-10-15-bikeshed-gen4-new-measurements-design.md`):* the control-vs-twin table shows each agent's brief per run: its slot, "shared only", or "–" when the run had no briefs.`
- Fix 14 (after C1's line): `*C2 did copying and credit accuracy:* six-word-sequence matching against earlier entries the copier could see, tagged same round or earlier round; per call, copied sources that weren't credited. Rule-gaming in private notes is deferred to its own spec, to be calibrated on generation 4 think notes.`
- Fix 17: `*Done in sub-project C2:* the Observer's summary lists each norm proposal that overlaps a norm adopted before it, with its outcome. Agents are never told.`
- Fix 11 (after B's line): `*Library reuse measured in sub-project C2:* per run, which shown methods reappear in proposals or refines; across runs, how often each method was shown and reused.`

- [ ] **Step 2: Mark C2 in the development-order table**

C2's row status: `**In progress**`.

- [ ] **Step 3: Full suite**

Run: `uv run pytest -q > .superpowers/sdd/2026-10-15-bikeshed-gen4-new-measurements/suite.txt 2>&1; tail -4 .superpowers/sdd/2026-10-15-bikeshed-gen4-new-measurements/suite.txt`
Expected: all passed, 0 failed.

- [ ] **Step 4: Commit**

```bash
pgrep -fl "bikeshed (run|postrun)"
git add docs/gen4-fixes.md
git commit -m "docs: record sub-project C2 outcomes

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

When C2 merges, set its row to `**Done**` with the merge commit.
