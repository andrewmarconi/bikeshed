# AISwarm Post-run Personal Loop Implementation Plan (Plan 2a)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After every run, each agent writes a private journal, reviews the colleagues it worked with (and itself), responds to its reviews, and updates its lessons and (optionally) its self, temperament, peer notes and public profile; reputation is computed from peer ratings and fed back into the next run's View, along with the agent's recent journals and a new `read_reviews` lookup.

**Architecture:** A plain-code `PostRun` cycle (no orchestrator model) runs four steps — journal, review, responses, update — each fanned out concurrently across agents and recorded per call in `postrun_steps`, so a crashed or paused cycle resumes without rewriting anything twice. Every model call goes through one `ask()` helper (pydantic-validated JSON, one correction). Reviews, responses and journal text live in SQLite; journals are also rendered to git-tracked files in each agent's profile. A shared `LimitedClient` caps simultaneous model calls across runs and the cycle.

**Tech Stack:** Python 3.14, uv, pydantic v2, typer, sqlite3, git, pytest + pytest-asyncio. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-10-aiswarm-postrun-design.md` §3 (Plan 2a), extending `docs/superpowers/specs/2026-10-10-aiswarm-design.md`.

**Not this plan:** Plan 2b — reporter account and narrator, Method Library, norm proposals and rating dimensions (§4 of the addendum).

## Global Constraints

- Python `>=3.14`, managed by `uv`. No new dependencies.
- No orchestrator model: the cycle never reads content to decide or steer; it only routes, validates, and stores.
- Observation never feeds back: reasoning traces (including post-run reasoning) never appear in any View or post-run request.
- Privacy invariant: an agent's `temperament.md`, `self.md`, `lessons.md`, `peer-notes.md`, journals, and any review `private_note` addressed to it never appear in any *other* agent's View, system prompt, post-run request, or `read_reviews` result.
- Self-reviews are excluded from reputation and from `read_reviews`; they are stored for the Observer (Plan 3) and shown to the user in `agents show`.
- Prompts are values-neutral. Use the prompt text in this plan verbatim; do not add guidance about what a good colleague, review, or self is.
- Public profile limits (existing): name 1–40 printable chars, unique case-insensitively; headline ≤ 254 chars, single line; introduction ≤ 600 chars. Private files ≤ 4000 chars (`PRIVATE_MAX`). Journal sections ≤ 6000 chars (`JOURNAL_MAX`).
- New defaults: `max_concurrent_calls = 4`, `reputation_half_life_runs = 5`.
- All git commits by AISwarm go through the existing `GitRepo` (isolated from user config).
- Post-run failures are recorded in `failures` with `round = 0` and `kind = "postrun:<step>:<kind>"`, where `<kind>` ∈ `model_error` | `invalid_reply` | `step_error`.
- Markdown files: never hard-wrap lines.

## Review Focus

1. **An agent rewrites its public profile to a name another agent holds, or empties its temperament:** the rewrite is rejected, the previous version kept, the rejection appended to its journal, and the rest of its update still applied. Test in Task 6.
2. **A crash or Ctrl-C mid-cycle, then `aiswarm postrun`:** completed calls are not repeated and no review, response, or journal is duplicated. Tests in Task 5.
3. **An agent that interacted with nobody:** it still writes a journal and a self-review, gets no responses step, and the cycle completes. Test in Task 5.
4. **A run that ended without consensus:** feedback packets say so and the cycle completes. Tests in Tasks 3 and 5.
5. **A review that cites entry ids from another run or ids that don't exist:** rejected with a correction, then recorded as a failed step; nothing fabricated. Test in Task 5.

---

## File Structure

```
src/aiswarm/
  config.py             # MODIFY: RunSettings.max_concurrent_calls, reputation_half_life_runs; DEFAULT_CONFIG
  db.py                 # MODIFY: migration 2 (reviews, review_responses, journals, postrun_steps)
  board.py              # MODIFY: Board.conn property
  models/base.py        # MODIFY: LimitedClient, limit_clients
  profiles.py           # MODIFY: write_journal_file, journals
  journal.py            # CREATE: JOURNAL_MAX, write_journal, journal_section
  reviews.py            # CREATE: Review, Response, Reputation, add_review, add_response, reviews_of, responses_for, reputation, render_review, render_public_reviews
  actions.py            # MODIFY: ReadReviews read action
  agent.py              # MODIFY: make_reader(reviews=...)
  view.py               # MODIFY: journals, norms, reputation in the View
  runner.py             # MODIFY: pass half-life and review reader
  cli.py                # MODIFY: run --no-postrun + automatic cycle, postrun command, agents show additions
  postrun/
    __init__.py         # CREATE: exports PostRun, PostRunResult
    feedback.py         # CREATE: interactions, feedback_packet, transcript
    replies.py          # CREATE: JournalReply, ReviewReply, ResponsesReply, UpdateReply, PublicReply
    ask.py              # CREATE: ask, Answer, ReplyError
    cycle.py            # CREATE: PostRun, PostRunResult, STEPS
  prompts/
    agent_system.md     # MODIFY: read_reviews line
    postrun_system.md   # CREATE
    journal_request.md  # CREATE
    review_request.md   # CREATE
    responses_request.md# CREATE
    update_request.md   # CREATE
tests/
  conftest.py           # MODIFY: postrun_reply fixture
  test_models_base.py   # MODIFY
  test_config.py        # MODIFY
  test_reviews.py       # CREATE
  test_journal.py       # CREATE
  test_feedback.py      # CREATE
  test_ask.py           # CREATE
  test_postrun.py       # CREATE
  test_view.py          # MODIFY
  test_agent.py         # MODIFY
  test_runner.py        # MODIFY
  test_cli.py           # MODIFY
```

Run tests with `uv run pytest -q` from the repo root. Pipe through `tail` only with `set -o pipefail`.

---

### Task 1: Settings and a shared limit on simultaneous model calls

**Files:**
- Modify: `src/aiswarm/config.py` (`RunSettings`, `DEFAULT_CONFIG`)
- Modify: `src/aiswarm/models/base.py`
- Test: `tests/test_config.py`, `tests/test_models_base.py`

**Interfaces:**
- Consumes: `ModelClient`, `ChatResult` from `aiswarm.models.base`.
- Produces: `RunSettings.max_concurrent_calls: int` (default 4, ≥ 1), `RunSettings.reputation_half_life_runs: float` (default 5.0, > 0); `LimitedClient(inner: ModelClient, limiter: asyncio.Semaphore)`; `limit_clients(clients: Mapping[str, ModelClient], max_concurrent: int) -> dict[str, ModelClient]` (one shared limiter for all returned clients; call it inside the event loop that will use the clients).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python


def test_postrun_settings_defaults(tmp_path):
    cfg = load_config(write(tmp_path, '[models.l]\nkind = "openai"\nmodel = "m"\n'))
    assert cfg.run.max_concurrent_calls == 4
    assert cfg.run.reputation_half_life_runs == 5.0


def test_max_concurrent_calls_must_be_positive(tmp_path):
    with pytest.raises(ValidationError):
        load_config(write(tmp_path, '[models.l]\nkind = "openai"\nmodel = "m"\n[run]\nmax_concurrent_calls = 0\n'))
```

Append to `tests/test_models_base.py`:

```python


class _Slow:
    context_window = 1000

    def __init__(self, tracker):
        self.tracker = tracker

    async def chat(self, system, messages, json_schema=None):
        self.tracker["now"] += 1
        self.tracker["peak"] = max(self.tracker["peak"], self.tracker["now"])
        await asyncio.sleep(0.01)
        self.tracker["now"] -= 1
        return ChatResult(text="ok")


async def test_limit_clients_caps_simultaneous_calls_across_clients():
    tracker = {"now": 0, "peak": 0}
    clients = limit_clients({key: _Slow(tracker) for key in "abcdef"}, 2)
    results = await asyncio.gather(*(c.chat("s", []) for c in clients.values() for _ in range(3)))
    assert tracker["peak"] == 2
    assert [r.text for r in results] == ["ok"] * 18
    assert clients["a"].context_window == 1000
```

At the top of `tests/test_models_base.py`, make sure these imports exist (add any that are missing):

```python
import asyncio

from aiswarm.models.base import ChatResult, limit_clients
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_config.py tests/test_models_base.py -q`
Expected: FAIL — `AttributeError: 'RunSettings' object has no attribute 'max_concurrent_calls'` and `ImportError: cannot import name 'limit_clients'`.

- [ ] **Step 3: Implement**

In `src/aiswarm/config.py`, add two fields at the end of `RunSettings`:

```python
    max_concurrent_calls: int = Field(4, ge=1)
    reputation_half_life_runs: float = Field(5.0, gt=0)
```

In `DEFAULT_CONFIG`, after `model_retries = 3`, add:

```
max_concurrent_calls = 4               # simultaneous model calls across all agents
reputation_half_life_runs = 5          # a rating this many runs old counts half
```

In `src/aiswarm/models/base.py`, add `Mapping` to the `collections.abc` import (`from collections.abc import Awaitable, Callable, Mapping`) and append:

```python


class LimitedClient:
    """Holds a permit from a limiter shared across clients, so at most N model calls run at once."""

    def __init__(self, inner: ModelClient, limiter: asyncio.Semaphore):
        self.inner = inner
        self._limiter = limiter
        self.context_window = inner.context_window

    async def chat(
        self, system: str, messages: list[Message], json_schema: dict[str, Any] | None = None
    ) -> ChatResult:
        async with self._limiter:
            return await self.inner.chat(system, messages, json_schema)


def limit_clients(clients: Mapping[str, ModelClient], max_concurrent: int) -> dict[str, ModelClient]:
    """Wrap every client in one shared limiter. Call inside the event loop that will use them."""
    limiter = asyncio.Semaphore(max_concurrent)
    return {key: LimitedClient(client, limiter) for key, client in clients.items()}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py tests/test_models_base.py -q`
Expected: PASS (including `test_default_config_is_valid`).

- [ ] **Step 5: Commit**

```bash
git add src/aiswarm/config.py src/aiswarm/models/base.py tests/test_config.py tests/test_models_base.py
git commit -m "feat: cap simultaneous model calls with a shared limiter; add reputation half-life setting"
```

---

### Task 2: Reviews, responses, reputation, and journals storage

**Files:**
- Modify: `src/aiswarm/db.py` (append migration 2)
- Modify: `src/aiswarm/profiles.py` (`write_journal_file`, `journals`)
- Create: `src/aiswarm/reviews.py`
- Create: `src/aiswarm/journal.py`
- Test: `tests/test_reviews.py`, `tests/test_journal.py`

**Interfaces:**
- Consumes: `connect`, `now_iso`, `transaction` (`aiswarm.db`); `ProfileStore` (`aiswarm.profiles`).
- Produces:
  - Tables `reviews`, `review_responses`, `journals`, `postrun_steps` (schemas below).
  - `aiswarm.reviews`: dataclasses `Review(id: int, run_id: str, reviewer: str, reviewee: str, is_self: bool, more_of: str, less_of: str, private_note: str, evidence: list[int], overall: int, overall_reason: str)`, `Response(review_id: int, stance: str, reason: str)`, `Reputation(score: float | None, count: int)` with `.label() -> str`; functions `add_review(conn, *, run_id, reviewer, reviewee, more_of, less_of, private_note, evidence, overall, overall_reason) -> Review` (upsert on `(run_id, reviewer, reviewee)`; `is_self` derived), `add_response(conn, review_id, stance, reason) -> None` (upsert), `reviews_of(conn, agent_id, run_id=None, include_self=False) -> list[Review]` (newest first), `responses_for(conn, review_ids) -> dict[int, Response]`, `reputation(conn, agent_id, half_life) -> Reputation`, `render_review(review, who, *, private, response=None) -> str`, `render_public_reviews(conn, agent_id, who, max_chars=8000) -> str`.
  - `aiswarm.journal`: `JOURNAL_MAX = 6000`, `write_journal(conn, store, agent_id, run_id, section, text) -> None` (`section` ∈ `"before"`, `"after"`; upsert, then re-render the file), `journal_section(conn, agent_id, run_id, section) -> str | None`.
  - `ProfileStore.write_journal_file(agent_id, run_id, content) -> None`, `ProfileStore.journals(agent_id, limit) -> list[tuple[str, str]]` (the latest `limit` journal files as `(run_id, text)`, oldest first).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_reviews.py`:

```python
import pytest

from aiswarm.reviews import (
    Reputation, add_response, add_review, render_public_reviews, reputation, responses_for, reviews_of,
)

WHO = {"a": "Alpha (a)", "b": "Bea (b)", "c": "Cyd (c)"}.get


def closed_run(conn, run_id, created_at):
    conn.execute(
        "INSERT INTO runs (id, goal, status, ended_reason, agent_ids, created_at) "
        "VALUES (?, 'g', 'closed', 'budget', '[\"a\", \"b\", \"c\"]', ?)",
        (run_id, created_at),
    )


def review(conn, run_id, reviewer, reviewee, overall=4, note="NOTE"):
    return add_review(
        conn, run_id=run_id, reviewer=reviewer, reviewee=reviewee, more_of="MORE", less_of="LESS",
        private_note=note, evidence=[2, 3], overall=overall, overall_reason="REASON",
    )


def test_add_review_round_trips_and_marks_self(community):
    store, conn = community
    closed_run(conn, "r1", "2026-01-01T00:00:00+00:00")
    peer = review(conn, "r1", "b", "a")
    own = review(conn, "r1", "a", "a")
    assert not peer.is_self and own.is_self
    assert reviews_of(conn, "a") == [peer]
    assert reviews_of(conn, "a", include_self=True) == [own, peer]
    assert reviews_of(conn, "a", run_id="other") == []
    assert peer.evidence == [2, 3]


def test_add_review_twice_replaces_instead_of_duplicating(community):
    store, conn = community
    closed_run(conn, "r1", "2026-01-01T00:00:00+00:00")
    first = review(conn, "r1", "b", "a", overall=2)
    second = review(conn, "r1", "b", "a", overall=5)
    assert first.id == second.id
    assert [r.overall for r in reviews_of(conn, "a")] == [5]


def test_responses_upsert(community):
    store, conn = community
    closed_run(conn, "r1", "2026-01-01T00:00:00+00:00")
    r = review(conn, "r1", "b", "a")
    add_response(conn, r.id, "dispute", "No.")
    add_response(conn, r.id, "accept", "Fair.")
    assert responses_for(conn, [r.id])[r.id].stance == "accept"
    assert responses_for(conn, []) == {}


def test_reputation_is_recency_weighted_and_excludes_self(community):
    store, conn = community
    closed_run(conn, "r1", "2026-01-01T00:00:00+00:00")
    closed_run(conn, "r2", "2026-01-02T00:00:00+00:00")
    review(conn, "r1", "b", "a", overall=5)   # one closed run since → weight 0.5 at half-life 1
    review(conn, "r2", "c", "a", overall=1)   # newest → weight 1
    review(conn, "r2", "a", "a", overall=5)   # self → excluded
    rep = reputation(conn, "a", half_life=1)
    assert rep.count == 2
    assert rep.score == pytest.approx((5 * 0.5 + 1 * 1) / 1.5)
    assert reputation(conn, "b", half_life=1) == Reputation(None, 0)


def test_reputation_labels():
    assert Reputation(None, 0).label() == "no reviews yet"
    assert Reputation(4.25, 12).label() == "★4.2 (12 reviews)"
    assert Reputation(3.0, 1).label() == "★3.0 (1 review)"


def test_public_reviews_hide_private_notes_and_self_reviews(community):
    store, conn = community
    closed_run(conn, "r1", "2026-01-01T00:00:00+00:00")
    peer = review(conn, "r1", "b", "a", note="SECRET-NOTE")
    review(conn, "r1", "a", "a", note="SELF-NOTE")
    add_response(conn, peer.id, "accept", "Fair point.")
    text = render_public_reviews(conn, "a", WHO)
    assert f"Review {peer.id} of Alpha (a) by Bea (b) after run r1" in text
    assert "more of: MORE" in text and "evidence: #2, #3" in text and "overall: 4/5 — REASON" in text
    assert "response: accept — Fair point." in text
    assert "SECRET-NOTE" not in text and "SELF-NOTE" not in text and "Self-review" not in text
    assert render_public_reviews(conn, "c", WHO) == "No reviews of Cyd (c) yet."


def test_public_reviews_truncate(community):
    store, conn = community
    closed_run(conn, "r1", "2026-01-01T00:00:00+00:00")
    review(conn, "r1", "b", "a")
    text = render_public_reviews(conn, "a", WHO, max_chars=40)
    assert text.startswith("Review") and "[truncated:" in text
```

Create `tests/test_journal.py`:

```python
from aiswarm.journal import journal_section, write_journal


def test_journal_sections_render_to_one_file_and_replace_on_rewrite(community):
    store, conn = community
    conn.execute("INSERT INTO runs (id, goal, status, agent_ids, created_at) VALUES ('r1', 'g', 'closed', '[]', 't')")
    write_journal(conn, store, "a", "r1", "before", "First thoughts.")
    write_journal(conn, store, "a", "r1", "after", "After reviews.")
    write_journal(conn, store, "a", "r1", "before", "Revised first thoughts.")
    path = store.root / "agents" / "a" / "journal" / "r1.md"
    assert path.read_text() == (
        "# Run r1\n\n## Before reviews\n\nRevised first thoughts.\n\n## After reviews\n\nAfter reviews.\n"
    )
    assert journal_section(conn, "a", "r1", "before") == "Revised first thoughts."
    assert journal_section(conn, "a", "r1", "after") == "After reviews."
    assert journal_section(conn, "b", "r1", "before") is None


def test_journals_returns_latest_files_oldest_first(community):
    store, conn = community
    for run_id in ("20260101-000000-aaaa", "20260102-000000-bbbb", "20260103-000000-cccc"):
        store.write_journal_file("a", run_id, f"# Run {run_id}\n")
    assert [run_id for run_id, _ in store.journals("a", 2)] == ["20260102-000000-bbbb", "20260103-000000-cccc"]
    assert store.journals("b", 2) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_reviews.py tests/test_journal.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'aiswarm.reviews'` / `'aiswarm.journal'`.

- [ ] **Step 3: Add the migration**

In `src/aiswarm/db.py`, append a second string to `MIGRATIONS` (after the first string's closing `""",`):

```python
    """
    CREATE TABLE reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL REFERENCES runs(id),
        reviewer TEXT NOT NULL,
        reviewee TEXT NOT NULL,
        is_self INTEGER NOT NULL,
        more_of TEXT NOT NULL,
        less_of TEXT NOT NULL,
        private_note TEXT NOT NULL,
        evidence TEXT NOT NULL,
        overall INTEGER NOT NULL CHECK (overall BETWEEN 1 AND 5),
        overall_reason TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE (run_id, reviewer, reviewee)
    );
    CREATE TABLE review_responses (
        review_id INTEGER PRIMARY KEY REFERENCES reviews(id),
        stance TEXT NOT NULL CHECK (stance IN ('accept', 'dispute')),
        reason TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE journals (
        run_id TEXT NOT NULL REFERENCES runs(id),
        agent_id TEXT NOT NULL,
        section TEXT NOT NULL CHECK (section IN ('before', 'after')),
        text TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (run_id, agent_id, section)
    );
    CREATE TABLE postrun_steps (
        run_id TEXT NOT NULL REFERENCES runs(id),
        step TEXT NOT NULL,
        agent_id TEXT NOT NULL,
        subject TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL CHECK (status IN ('done', 'failed')),
        detail TEXT NOT NULL DEFAULT '',
        reasoning TEXT,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (run_id, step, agent_id, subject)
    );
    """,
```

(The `journals` table holds each section's text so a rewrite replaces one section cleanly; the journal *file* in the agent's profile, rendered from it, remains the agent's record and is committed to git, as the spec requires.)

- [ ] **Step 4: Implement `reviews.py`**

Create `src/aiswarm/reviews.py`:

```python
"""Named peer reviews, the reviewee's responses, and reputation computed from them."""

import json
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from aiswarm.db import now_iso

Who = Callable[[str], str]


@dataclass(frozen=True)
class Review:
    id: int
    run_id: str
    reviewer: str
    reviewee: str
    is_self: bool
    more_of: str
    less_of: str
    private_note: str
    evidence: list[int]
    overall: int
    overall_reason: str


@dataclass(frozen=True)
class Response:
    review_id: int
    stance: str
    reason: str


@dataclass(frozen=True)
class Reputation:
    score: float | None
    count: int

    def label(self) -> str:
        if not self.count or self.score is None:
            return "no reviews yet"
        return f"★{self.score:.1f} ({self.count} review{'' if self.count == 1 else 's'})"


def _review(row: sqlite3.Row) -> Review:
    return Review(
        id=row["id"], run_id=row["run_id"], reviewer=row["reviewer"], reviewee=row["reviewee"],
        is_self=bool(row["is_self"]), more_of=row["more_of"], less_of=row["less_of"],
        private_note=row["private_note"], evidence=json.loads(row["evidence"]), overall=row["overall"],
        overall_reason=row["overall_reason"],
    )


def add_review(
    conn: sqlite3.Connection, *, run_id: str, reviewer: str, reviewee: str, more_of: str, less_of: str,
    private_note: str, evidence: Sequence[int], overall: int, overall_reason: str,
) -> Review:
    row = conn.execute(
        "INSERT INTO reviews (run_id, reviewer, reviewee, is_self, more_of, less_of, private_note, evidence, "
        "overall, overall_reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (run_id, reviewer, reviewee) DO UPDATE SET more_of = excluded.more_of, "
        "less_of = excluded.less_of, private_note = excluded.private_note, evidence = excluded.evidence, "
        "overall = excluded.overall, overall_reason = excluded.overall_reason, created_at = excluded.created_at "
        "RETURNING *",
        (run_id, reviewer, reviewee, int(reviewer == reviewee), more_of, less_of, private_note,
         json.dumps(list(evidence)), overall, overall_reason, now_iso()),
    ).fetchone()
    return _review(row)


def add_response(conn: sqlite3.Connection, review_id: int, stance: str, reason: str) -> None:
    conn.execute(
        "INSERT INTO review_responses (review_id, stance, reason, created_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT (review_id) DO UPDATE SET stance = excluded.stance, reason = excluded.reason, "
        "created_at = excluded.created_at",
        (review_id, stance, reason, now_iso()),
    )


def reviews_of(
    conn: sqlite3.Connection, agent_id: str, run_id: str | None = None, include_self: bool = False
) -> list[Review]:
    sql = "SELECT * FROM reviews WHERE reviewee = ?"
    params: list[object] = [agent_id]
    if run_id is not None:
        sql += " AND run_id = ?"
        params.append(run_id)
    if not include_self:
        sql += " AND is_self = 0"
    return [_review(row) for row in conn.execute(sql + " ORDER BY id DESC", params)]


def responses_for(conn: sqlite3.Connection, review_ids: Sequence[int]) -> dict[int, Response]:
    if not review_ids:
        return {}
    marks = ", ".join("?" for _ in review_ids)
    rows = conn.execute(f"SELECT * FROM review_responses WHERE review_id IN ({marks})", list(review_ids))
    return {row["review_id"]: Response(row["review_id"], row["stance"], row["reason"]) for row in rows}


def reputation(conn: sqlite3.Connection, agent_id: str, half_life: float) -> Reputation:
    """Recency-weighted mean of peer ratings; age is the number of closed runs after the reviewed one."""
    rows = conn.execute(
        "SELECT r.overall, (SELECT COUNT(*) FROM runs later WHERE later.status = 'closed' "
        "AND (later.created_at, later.id) > (u.created_at, u.id)) AS age "
        "FROM reviews r JOIN runs u ON u.id = r.run_id WHERE r.reviewee = ? AND r.is_self = 0",
        (agent_id,),
    ).fetchall()
    if not rows:
        return Reputation(None, 0)
    weights = [0.5 ** (row["age"] / half_life) for row in rows]
    score = sum(w * row["overall"] for w, row in zip(weights, rows)) / sum(weights)
    return Reputation(score, len(rows))


def render_review(review: Review, who: Who, *, private: bool, response: Response | None = None) -> str:
    kind = "Self-review" if review.is_self else "Review"
    lines = [
        f"{kind} {review.id} of {who(review.reviewee)} by {who(review.reviewer)} after run {review.run_id}",
        f"  more of: {review.more_of}",
        f"  less of: {review.less_of}",
    ]
    if private:
        lines.append(f"  private note: {review.private_note}")
    lines += [
        f"  evidence: {', '.join(f'#{i}' for i in review.evidence)}",
        f"  overall: {review.overall}/5 — {review.overall_reason}",
    ]
    if response:
        lines.append(f"  response: {response.stance} — {response.reason}")
    return "\n".join(lines)


def render_public_reviews(conn: sqlite3.Connection, agent_id: str, who: Who, max_chars: int = 8_000) -> str:
    """What any member may read about another: peer reviews and responses, never private notes."""
    reviews = reviews_of(conn, agent_id)
    if not reviews:
        return f"No reviews of {who(agent_id)} yet."
    responses = responses_for(conn, [r.id for r in reviews])
    text = "\n\n".join(render_review(r, who, private=False, response=responses.get(r.id)) for r in reviews)
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n[truncated: {len(text)} characters in total]"
    return text
```

- [ ] **Step 5: Implement journals**

In `src/aiswarm/profiles.py`, add two methods to `ProfileStore` (after `write_private`):

```python
    def write_journal_file(self, agent_id: str, run_id: str, content: str) -> None:
        path = self._dir(agent_id) / "journal" / f"{run_id}.md"
        path.parent.mkdir(exist_ok=True)
        path.write_text(content)
        self._repo.commit([f"agents/{agent_id}/journal/{run_id}.md"], f"{agent_id} journal for {run_id}", author=agent_id)

    def journals(self, agent_id: str, limit: int) -> list[tuple[str, str]]:
        """The latest `limit` journal files as (run_id, text), oldest first. Run ids sort by time."""
        directory = self._dir(agent_id) / "journal"
        if not directory.exists() or limit <= 0:
            return []
        paths = sorted(directory.glob("*.md"), key=lambda p: p.stem)[-limit:]
        return [(path.stem, path.read_text()) for path in paths]
```

Create `src/aiswarm/journal.py`:

```python
"""Private journals: one file per agent per run, with a before-reviews and an after-reviews section."""

import sqlite3
from typing import Literal

from aiswarm.db import now_iso
from aiswarm.profiles import ProfileStore

JOURNAL_MAX = 6_000
Section = Literal["before", "after"]
_TITLES = {"before": "Before reviews", "after": "After reviews"}


def write_journal(
    conn: sqlite3.Connection, store: ProfileStore, agent_id: str, run_id: str, section: Section, text: str
) -> None:
    conn.execute(
        "INSERT INTO journals (run_id, agent_id, section, text, created_at) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT (run_id, agent_id, section) DO UPDATE SET text = excluded.text, created_at = excluded.created_at",
        (run_id, agent_id, section, text, now_iso()),
    )
    sections = {
        row["section"]: row["text"]
        for row in conn.execute("SELECT section, text FROM journals WHERE run_id = ? AND agent_id = ?", (run_id, agent_id))
    }
    parts = [f"# Run {run_id}"]
    for key, title in _TITLES.items():
        if key in sections:
            parts += ["", f"## {title}", "", sections[key].strip()]
    store.write_journal_file(agent_id, run_id, "\n".join(parts) + "\n")


def journal_section(conn: sqlite3.Connection, agent_id: str, run_id: str, section: Section) -> str | None:
    row = conn.execute(
        "SELECT text FROM journals WHERE run_id = ? AND agent_id = ? AND section = ?", (run_id, agent_id, section)
    ).fetchone()
    return row["text"] if row else None
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_reviews.py tests/test_journal.py -q`
Expected: PASS.

Then the full suite: `set -o pipefail; uv run pytest -q 2>&1 | tail -3`
Expected: all pass (the migration applies to fresh test databases).

- [ ] **Step 7: Commit**

```bash
git add src/aiswarm/db.py src/aiswarm/reviews.py src/aiswarm/journal.py src/aiswarm/profiles.py tests/test_reviews.py tests/test_journal.py
git commit -m "feat: store reviews, responses, journals and post-run steps; compute reputation"
```

---

### Task 3: Feedback facts — interactions, feedback packets, transcripts

**Files:**
- Create: `src/aiswarm/postrun/__init__.py` (empty for now: a one-line docstring)
- Create: `src/aiswarm/postrun/feedback.py`
- Test: `tests/test_feedback.py`

**Interfaces:**
- Consumes: `Entry`, `SYSTEM` (`aiswarm.board`); `Who`, `render_entry` (`aiswarm.view`).
- Produces:
  - `interactions(entries: Sequence[Entry], file_authors: Sequence[tuple[str, str]], agent_ids: Sequence[str]) -> dict[str, set[str]]` — for each member, the other members it interacted with.
  - `feedback_packet(agent_id: str, *, run_id: str, goal: str, entries: Sequence[Entry], who: Who, answer: str, failures: Sequence[tuple[int, str, str]]) -> str`.
  - `transcript(entries: Sequence[Entry], who: Who, authors: set[str], max_chars: int) -> str` — entries by `authors`, newest kept when trimmed.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_feedback.py`:

```python
from aiswarm.board import SYSTEM, Entry
from aiswarm.postrun.feedback import feedback_packet, interactions, transcript

WHO = {"a": "Alpha (a)", "b": "Bea (b)", "c": "Cyd (c)", "d": "Dee (d)", SYSTEM: "system"}.get
AGENTS = ["a", "b", "c", "d"]


def e(id, round, author, type, links=(), **payload):
    return Entry(id, "r1", round, author, type, payload, list(links), "all", "t")


def board_with_consensus():
    return [
        e(1, 0, SYSTEM, "goal", goal="Pick a colour"),
        e(2, 1, "a", "propose", answer_summary="Red", method="M"),
        e(3, 1, "b", "propose", answer_summary="Blue", method="M"),
        e(4, 2, "b", "critique", links=[2], target=2, about="method", points="P"),
        e(5, 2, "c", "adopt", links=[2], target=2, reason="R"),
        e(6, 3, "a", "call_consensus", links=[2, 3], synthesis="Purple", refs=[2, 3],
          credits=[{"agent_id": "b", "contribution": "the blue half"}]),
        e(7, 3, SYSTEM, "run_closed", reason="consensus", call_id=6),
    ]


def test_interactions_from_refs_either_way_and_shared_files():
    pairs = interactions(board_with_consensus(), [("plan.md", "c"), ("plan.md", "d"), ("solo.md", "a")], AGENTS)
    assert pairs["a"] == {"b", "c"}
    assert pairs["b"] == {"a"}
    assert pairs["c"] == {"a", "d"}
    assert pairs["d"] == {"c"}


def test_interactions_ignore_system_and_non_members():
    entries = [e(2, 1, "a", "propose"), e(3, 1, SYSTEM, "artifact_changed", links=[2]), e(4, 1, "zed", "critique", links=[2])]
    assert interactions(entries, [], ["a", "b"]) == {"a": set(), "b": set()}


def test_packet_lists_responses_credits_citation_and_outcome():
    packet = feedback_packet(
        "b", run_id="r1", goal="Pick a colour", entries=board_with_consensus(), who=WHO,
        answer="# Answer\nPURPLE-OUTCOME", failures=[(2, "model_error", "timed out")],
    )
    assert packet.startswith("Run r1\nGoal: Pick a colour\nThe run ended by consensus.")
    assert "#3 [r1] Bea (b) propose" in packet
    assert "  ← #6 Alpha (a) call_consensus" in packet
    assert "- from Alpha (a) in call #6 (the winning call): the blue half" in packet
    assert "Call #6 by Alpha (a) cited your entries: #3" in packet
    assert "PURPLE-OUTCOME" in packet
    assert "- round 2: model_error: timed out" in packet


def test_packet_for_a_run_without_consensus_or_posts():
    entries = [e(1, 0, SYSTEM, "goal", goal="G"), e(2, 1, "a", "propose", answer_summary="X", method="M"),
               e(3, 6, SYSTEM, "run_closed", reason="budget", call_id=None)]
    packet = feedback_packet("d", run_id="r1", goal="G", entries=entries, who=WHO, answer="", failures=[])
    assert "The run ended by budget." in packet
    assert "## What you posted\n(nothing)" in packet
    assert "## Credits you received\n(none)" in packet
    assert "The run ended without consensus." in packet
    assert "## The outcome\n(no report)" in packet
    assert "## Your failed steps\n(none)" in packet


def test_transcript_keeps_newest_entries_by_the_given_authors():
    entries = [e(i, 1, "a" if i % 2 else "b", "question", text="x" * 50) for i in range(2, 30)]
    text = transcript(entries, WHO, {"a"}, max_chars=400)
    assert text.startswith("[earlier entries omitted]\n")
    assert "#29 [r1] Alpha (a)" in text and "Bea (b)" not in text
    assert len(text) <= 400 + len("[earlier entries omitted]\n")
    assert transcript(entries, WHO, {"c"}, max_chars=400) == "(no entries)"
```

(`links` sets an entry's `refs`, which is what connects entries; a `call_consensus` also carries `refs` in its payload, as the Runner stores it.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_feedback.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'aiswarm.postrun'`.

- [ ] **Step 3: Implement**

Create `src/aiswarm/postrun/__init__.py`:

```python
"""The post-run cycle: journals, reviews, responses, and updates."""
```

Create `src/aiswarm/postrun/feedback.py`:

```python
"""Facts about a finished run, assembled by code: who interacted, and what each agent is told about its part."""

from collections.abc import Sequence

from aiswarm.board import SYSTEM, Entry
from aiswarm.view import Who, render_entry


def interactions(
    entries: Sequence[Entry], file_authors: Sequence[tuple[str, str]], agent_ids: Sequence[str]
) -> dict[str, set[str]]:
    """For each member, the other members it interacted with: a ref either way, or a shared workspace file."""
    members = set(agent_ids)
    pairs: dict[str, set[str]] = {agent_id: set() for agent_id in agent_ids}

    def link(x: str, y: str) -> None:
        if x != y and x in members and y in members:
            pairs[x].add(y)
            pairs[y].add(x)

    authors = {entry.id: entry.author for entry in entries}
    for entry in entries:
        for ref in entry.refs:
            if ref in authors:
                link(entry.author, authors[ref])
    writers: dict[str, set[str]] = {}
    for path, author in file_authors:
        writers.setdefault(path, set()).add(author)
    for group in writers.values():
        for x in group:
            for y in group:
                link(x, y)
    return pairs


def feedback_packet(
    agent_id: str, *, run_id: str, goal: str, entries: Sequence[Entry], who: Who, answer: str,
    failures: Sequence[tuple[int, str, str]],
) -> str:
    by_id = {entry.id: entry for entry in entries}
    closed = next((entry for entry in reversed(entries) if entry.type == "run_closed"), None)
    reason = closed.payload.get("reason", "unknown") if closed else "unknown"
    winning = by_id.get(closed.payload.get("call_id")) if closed else None

    lines = [f"Run {run_id}", f"Goal: {goal}", f"The run ended by {reason}.", "", "## What you posted"]
    mine = [entry for entry in entries if entry.author == agent_id]
    if not mine:
        lines.append("(nothing)")
    for entry in mine:
        lines.append(render_entry(entry, who))
        for other in entries:
            if entry.id in other.refs and other.author not in (agent_id, SYSTEM):
                lines.append(f"  ← #{other.id} {who(other.author)} {other.type}")

    lines += ["", "## Credits you received"]
    received = [
        (call, credit)
        for call in entries if call.type == "call_consensus"
        for credit in call.payload.get("credits", []) if credit.get("agent_id") == agent_id
    ]
    for call, credit in received:
        mark = " (the winning call)" if winning and call.id == winning.id else ""
        lines.append(f"- from {who(call.author)} in call #{call.id}{mark}: {credit.get('contribution', '')}")
    if not received:
        lines.append("(none)")

    lines += ["", "## The winning call"]
    if winning:
        cited = [ref for ref in winning.refs if ref in by_id and by_id[ref].author == agent_id]
        listed = ", ".join(f"#{ref}" for ref in cited) or "none"
        lines.append(f"Call #{winning.id} by {who(winning.author)} cited your entries: {listed}")
    else:
        lines.append("The run ended without consensus.")

    lines += ["", "## The outcome", answer.strip() or "(no report)", "", "## Your failed steps"]
    lines += [f"- round {rnd}: {kind}: {detail}" for rnd, kind, detail in failures] or ["(none)"]
    return "\n".join(lines)


def transcript(entries: Sequence[Entry], who: Who, authors: set[str], max_chars: int) -> str:
    items = [render_entry(entry, who) for entry in entries if entry.author in authors]
    kept: list[str] = []
    used = 0
    for item in reversed(items):
        if used + len(item) + 1 > max_chars:
            break
        kept.append(item)
        used += len(item) + 1
    text = "\n".join(reversed(kept))
    if len(kept) < len(items):
        text = "[earlier entries omitted]\n" + text
    return text or "(no entries)"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_feedback.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aiswarm/postrun/__init__.py src/aiswarm/postrun/feedback.py tests/test_feedback.py
git commit -m "feat: derive interactions, feedback packets and transcripts from a finished run"
```

---

### Task 4: Structured post-run requests — replies, `ask`, prompts, and a test responder

**Files:**
- Create: `src/aiswarm/postrun/replies.py`
- Create: `src/aiswarm/postrun/ask.py`
- Create: `src/aiswarm/prompts/postrun_system.md`, `journal_request.md`, `review_request.md`, `responses_request.md`, `update_request.md`
- Modify: `tests/conftest.py` (add `postrun_reply` fixture)
- Test: `tests/test_ask.py`

**Interfaces:**
- Consumes: `extract_json`, `simplify_schema`, `summarise_validation`, `ActionError` (`aiswarm.actions`); `ModelClient`, `Message` (`aiswarm.models.base`); `PRIVATE_MAX` (`aiswarm.profiles`); `JOURNAL_MAX` (`aiswarm.journal`); `render` (`aiswarm.prompts`).
- Produces:
  - `aiswarm.postrun.replies`: `JournalReply(journal)`, `ReviewReply(more_of, less_of, private_note, evidence: list[int] (≥1), overall: int 1–5, overall_reason)`, `ResponseItem(review_id: int, stance: "accept"|"dispute", reason)`, `ResponsesReply(responses: list[ResponseItem])`, `PublicReply(name, headline, introduction)`, `UpdateReply(journal, lessons (≤ PRIVATE_MAX), self_description: str | None, temperament: str | None, peer_notes: str | None, public_profile: PublicReply | None)`. All forbid extra keys.
  - `aiswarm.postrun.ask`: `ReplyError(ValueError)`, `Answer[T](reply: T, reasoning: str | None)`, `async ask(client, system, request, model, check=None) -> Answer` — sends `request` as the first user message with the model's simplified JSON schema; on a validation error, `ActionError`, or a `ReplyError` raised by `check`, sends one correction and tries again; then raises `ReplyError`. `ModelError` propagates.
  - Prompt files whose first line is `# Post-run: <step>` (`journal`, `review`, `responses`, `update`).
  - Test fixture `postrun_reply` → function `(request: str) -> str | None` returning a valid default reply for a post-run request, or `None` for anything else.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ask.py`:

```python
import pytest

from aiswarm.models.base import ModelError
from aiswarm.models.fake import FakeModel
from aiswarm.postrun.ask import ReplyError, ask
from aiswarm.postrun.replies import JournalReply, ReviewReply, UpdateReply
from aiswarm.prompts import render


async def test_ask_returns_the_validated_reply_and_reasoning():
    client = FakeModel(['{"journal": "Fine."}'], reasoning="thinking")
    answer = await ask(client, "SYS", "REQUEST", JournalReply)
    assert answer.reply.journal == "Fine." and answer.reasoning == "thinking"
    system, messages, schema = client.calls[0]
    assert system == "SYS" and messages == [{"role": "user", "content": "REQUEST"}]
    assert schema["properties"]["journal"]["type"] == "string"


async def test_ask_corrects_once_then_succeeds():
    client = FakeModel(["no json here", '{"journal": "Second try."}'])
    answer = await ask(client, "S", "R", JournalReply)
    assert answer.reply.journal == "Second try."
    assert "could not be accepted" in client.calls[1][1][-1]["content"]


async def test_ask_runs_the_check_and_gives_up_after_one_correction():
    reply = '{"more_of": "m", "less_of": "l", "private_note": "p", "evidence": [99], "overall": 3, "overall_reason": "r"}'
    client = FakeModel([reply, reply])

    def check(r):
        raise ReplyError("entry #99 is not part of this run")

    with pytest.raises(ReplyError, match="#99"):
        await ask(client, "S", "R", ReviewReply, check)
    assert len(client.calls) == 2


async def test_ask_rejects_out_of_range_ratings():
    bad = '{"more_of": "m", "less_of": "l", "private_note": "p", "evidence": [2], "overall": 9, "overall_reason": "r"}'
    with pytest.raises(ReplyError, match="overall"):
        await ask(FakeModel([bad, bad]), "S", "R", ReviewReply)


async def test_model_errors_propagate():
    with pytest.raises(ModelError):
        await ask(FakeModel(fail_times=1), "S", "R", JournalReply)


def test_update_reply_optional_rewrites_default_to_none():
    reply = UpdateReply.model_validate({"journal": "j", "lessons": "l"})
    assert reply.self_description is None and reply.public_profile is None


def test_postrun_templates_render():
    assert render("postrun_system.md", display="Alpha (a)", temperament="T", private_notes="").startswith("You are Alpha (a)")
    assert render("journal_request.md", packet="P", journal_max=6000).startswith("# Post-run: journal")
    assert render("review_request.md", heading="H", run_id="r1", goal="G", authors="you", transcript="T",
                  instruction="I", subject="you", object="yourself", private_reader="Only you will read this.",
                  visibility="V").startswith("# Post-run: review")
    assert render("responses_request.md", run_id="r1", reviews="R").startswith("# Post-run: responses")
    assert render("update_request.md", packet="P", first_journal="J", reviews="R", journal_max=1, private_max=2,
                  name_max=3, headline_max=4, intro_max=5).startswith("# Post-run: update")


def test_postrun_reply_fixture_answers_each_step(postrun_reply):
    assert postrun_reply("# Round 2\n...") is None
    assert '"journal"' in postrun_reply("# Post-run: journal\n...")
    assert '"evidence": [7]' in postrun_reply("# Post-run: review of x\n\n#7 [r1] Alpha (a) propose\n")
    assert '"review_id": 12' in postrun_reply("# Post-run: responses\n\nReview 12 of Alpha (a) by Bea (b) after run r1\n")
    assert '"lessons"' in postrun_reply("# Post-run: update\n...")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ask.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'aiswarm.postrun.ask'`.

- [ ] **Step 3: Implement replies and `ask`**

Create `src/aiswarm/postrun/replies.py`:

```python
"""The JSON replies each post-run step asks for."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from aiswarm.journal import JOURNAL_MAX
from aiswarm.profiles import PRIVATE_MAX

Text = Annotated[str, Field(min_length=1)]
JournalText = Annotated[str, Field(min_length=1, max_length=JOURNAL_MAX)]


class _Reply(BaseModel):
    model_config = ConfigDict(extra="forbid")


class JournalReply(_Reply):
    journal: JournalText


class ReviewReply(_Reply):
    more_of: Text
    less_of: Text
    private_note: Text
    evidence: list[int] = Field(min_length=1)
    overall: int = Field(ge=1, le=5)
    overall_reason: Text


class ResponseItem(_Reply):
    review_id: int
    stance: Literal["accept", "dispute"]
    reason: Text


class ResponsesReply(_Reply):
    responses: list[ResponseItem]


class PublicReply(_Reply):
    name: str
    headline: str
    introduction: str


class UpdateReply(_Reply):
    journal: JournalText
    lessons: str = Field(max_length=PRIVATE_MAX)
    self_description: str | None = None
    temperament: str | None = None
    peer_notes: str | None = None
    public_profile: PublicReply | None = None
```

Create `src/aiswarm/postrun/ask.py`:

```python
"""One structured post-run request: a JSON reply validated by pydantic, with one correction."""

from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel, ValidationError

from aiswarm.actions import ActionError, extract_json, simplify_schema, summarise_validation
from aiswarm.models.base import Message, ModelClient


class ReplyError(ValueError):
    """The reply could not be accepted, even after one correction."""


@dataclass(frozen=True)
class Answer[T]:
    reply: T
    reasoning: str | None


async def ask[T: BaseModel](
    client: ModelClient, system: str, request: str, model: type[T], check: Callable[[T], None] | None = None,
) -> Answer[T]:
    messages: list[Message] = [{"role": "user", "content": request}]
    schema = simplify_schema(model.model_json_schema())
    reasoning: list[str] = []
    problem = ""
    for _ in range(2):
        result = await client.chat(system, messages, schema)
        if result.reasoning:
            reasoning.append(result.reasoning)
        try:
            reply = model.model_validate(extract_json(result.text))
            if check:
                check(reply)
            return Answer(reply, "\n\n".join(reasoning) or None)
        except ValidationError as exc:
            problem = summarise_validation(exc)
        except (ActionError, ReplyError) as exc:
            problem = str(exc)
        messages += [
            {"role": "assistant", "content": result.text},
            {"role": "user", "content": f"That could not be accepted: {problem}. Reply again with JSON only."},
        ]
    raise ReplyError(problem)
```

- [ ] **Step 4: Write the prompts (verbatim)**

Create `src/aiswarm/prompts/postrun_system.md`:

```
You are $display, a member of a small community of agents who work together on shared goals. A piece of work has just finished. Reply with JSON only.

Your temperament:
$temperament
$private_notes
```

Create `src/aiswarm/prompts/journal_request.md`:

```
# Post-run: journal

$packet

Write a private journal entry about this run. Only you will ever read it. Write it however you like ($journal_max characters at most).

Reply with JSON only: {"journal": "..."}
```

Create `src/aiswarm/prompts/review_request.md`:

```
# Post-run: review of $heading

Run $run_id. Goal: $goal

## Board entries by $authors

$transcript

$instruction

- more_of: What did $subject do that you'd want more of?
- less_of: What would you want less of?
- private_note: What would you tell $object privately? $private_reader
- evidence: the ids of the board entries in this run that your review draws on (at least one).
- overall: an integer from 1 to 5 answering "Would you want to work with $object again?"
- overall_reason: why.

$visibility

Reply with JSON only: {"more_of": "...", "less_of": "...", "private_note": "...", "evidence": [<entry ids>], "overall": <1-5>, "overall_reason": "..."}
```

Create `src/aiswarm/prompts/responses_request.md`:

```
# Post-run: responses

Run $run_id. Other members wrote these reviews of you.

$reviews

For each review, say whether you accept or dispute it, and why. Other members can read your responses in later runs.

Reply with JSON only: {"responses": [{"review_id": <review id>, "stance": "accept" or "dispute", "reason": "..."}]}, with one response for each review.
```

Create `src/aiswarm/prompts/update_request.md`:

```
# Post-run: update

$packet

## Your journal entry from before the reviews

$first_journal

## Reviews of you, with your responses

$reviews

Now write:
- journal: a second private journal entry, written after reading your reviews. Only you will ever read it ($journal_max characters at most).
- lessons: your lessons.md, replacing the current one ($private_max characters at most).

You may also rewrite any of the following; use null for any you keep as it is.
- self_description: your self.md, private ($private_max characters at most).
- temperament: your temperament.md, private ($private_max characters at most).
- peer_notes: your peer-notes.md, private ($private_max characters at most).
- public_profile: {"name": "...", "headline": "...", "introduction": "..."}, which every member sees: name ($name_max characters at most), headline (a single line, $headline_max characters at most), introduction ($intro_max characters at most).

Reply with JSON only: {"journal": "...", "lessons": "...", "self_description": "..." or null, "temperament": "..." or null, "peer_notes": "..." or null, "public_profile": {...} or null}
```

- [ ] **Step 5: Add the test responder fixture**

Append to `tests/conftest.py`:

```python


def _postrun_reply(request):
    """A valid default reply for a post-run request, or None if the request is not one."""
    marker = re.match(r"# Post-run: (\w+)", request)
    if not marker:
        return None
    step = marker.group(1)
    if step == "journal":
        return json.dumps({"journal": "Thoughts."})
    if step == "review":
        ids = [int(i) for i in re.findall(r"^#(\d+) \[r", request, re.M)]
        return json.dumps({"more_of": "More.", "less_of": "Less.", "private_note": "Between us.",
                           "evidence": ids[:1] or [1], "overall": 4, "overall_reason": "Solid."})
    if step == "responses":
        ids = [int(i) for i in re.findall(r"^Review (\d+) of", request, re.M)]
        return json.dumps({"responses": [{"review_id": i, "stance": "accept", "reason": "Fair."} for i in ids]})
    return json.dumps({"journal": "After reviews.", "lessons": "Listen first.", "self_description": None,
                       "temperament": None, "peer_notes": None, "public_profile": None})


@pytest.fixture
def postrun_reply():
    return _postrun_reply
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_ask.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/aiswarm/postrun/replies.py src/aiswarm/postrun/ask.py src/aiswarm/prompts/postrun_system.md src/aiswarm/prompts/journal_request.md src/aiswarm/prompts/review_request.md src/aiswarm/prompts/responses_request.md src/aiswarm/prompts/update_request.md tests/conftest.py tests/test_ask.py
git commit -m "feat: add post-run reply models, prompts, and a validating ask helper"
```

---

### Task 5: The cycle — journals, reviews, responses; resume and pause

**Files:**
- Create: `src/aiswarm/postrun/cycle.py`
- Modify: `src/aiswarm/postrun/__init__.py` (export)
- Test: `tests/test_postrun.py`

**Interfaces:**
- Consumes: everything from Tasks 2–4; `Board`, `EventLog`, `make_who`, `render` (existing).
- Produces:
  - `PostRunResult(run_id: str, status: str, failed: int = 0)` — `status` ∈ `"done"`, `"paused"`; `failed` counts calls that failed in this invocation.
  - `PostRun(*, conn, store, runs_dir: Path, clients: Mapping[str, ModelClient], sink=None)` with `async run(run_id) -> PostRunResult`. Raises `KeyError` for an unknown run and `ValueError` for a run that is not closed.
  - `STEPS` tuple. Task 5 sets it to `("journal", "review", "responses")`; Task 6 appends `"update"`.
  - Events in `runs/<id>/events.jsonl`: `postrun_started`, `postrun_step_finished` (`step`, `agent`, `subject`, `status`), `postrun_paused` (`step`), `postrun_finished` (`failed`).
  - `postrun_steps` rows per call (`subject` = reviewee id for reviews, `""` otherwise), with the call's reasoning.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_postrun.py`:

```python
import json
import re

import pytest

from aiswarm.board import SYSTEM, Board
from aiswarm.events import read_events
from aiswarm.journal import journal_section
from aiswarm.models.base import ModelError
from aiswarm.models.fake import FakeModel
from aiswarm.postrun import PostRun
from aiswarm.reviews import responses_for, reviews_of


def finished_run(conn, tmp_path, make_run, reason="budget"):
    """r1: a, b, c propose; b critiques a; c does nothing else. a and b interacted; c interacted with no one."""
    make_run(conn, "r1", ("a", "b", "c"))
    board = Board(conn, "r1")
    board.append(0, SYSTEM, "goal", {"goal": "Pick a colour"})
    board.append(1, "a", "propose", {"answer_summary": "Red", "method": "M"})        # 2
    board.append(1, "b", "propose", {"answer_summary": "Blue", "method": "M"})       # 3
    board.append(1, "c", "propose", {"answer_summary": "Green", "method": "M"})      # 4
    board.append(2, "b", "critique", {"target": 2, "about": "method", "points": "P"}, refs=[2])  # 5
    board.append(2, SYSTEM, "run_closed", {"reason": reason, "call_id": None})       # 6
    conn.execute("UPDATE runs SET status = 'closed', ended_reason = ? WHERE id = 'r1'", (reason,))
    report = tmp_path / "runs" / "r1" / "report"
    report.mkdir(parents=True)
    (report / "answer.md").write_text("# Answer\nCOMPETING-POSITIONS\n")
    return "r1"


def responder(postrun_reply, **overrides):
    def respond(system, messages):
        request = messages[0]["content"]
        step = re.match(r"# Post-run: (\w+)", request).group(1)
        if step in overrides:
            return overrides[step](request)
        return postrun_reply(request)
    return respond


def cycle(store, conn, tmp_path, clients):
    return PostRun(conn=conn, store=store, runs_dir=tmp_path / "runs", clients=clients)


def steps(conn):
    return {(r["step"], r["agent_id"], r["subject"]): r["status"] for r in conn.execute("SELECT * FROM postrun_steps")}


async def test_cycle_writes_journals_reviews_and_responses(tmp_path, community, make_run, postrun_reply):
    store, conn = community
    run_id = finished_run(conn, tmp_path, make_run)
    clients = {a: FakeModel(responder(postrun_reply), reasoning=f"REASONING-{a}") for a in "abc"}
    result = await cycle(store, conn, tmp_path, clients).run(run_id)
    assert (result.status, result.failed) == ("done", 0)
    for agent in "abc":
        assert journal_section(conn, agent, run_id, "before") == "Thoughts."
        assert (store.root / "agents" / agent / "journal" / f"{run_id}.md").exists()
    pairs = {(r["reviewer"], r["reviewee"]) for r in conn.execute("SELECT reviewer, reviewee FROM reviews")}
    assert pairs == {("a", "b"), ("a", "a"), ("b", "a"), ("b", "b"), ("c", "c")}
    for agent in "ab":
        peer = reviews_of(conn, agent, run_id=run_id)
        assert len(peer) == 1 and responses_for(conn, [peer[0].id])[peer[0].id].stance == "accept"
    assert ("responses", "c", "") not in steps(conn)       # c has no peer reviews: no responses step
    assert set(steps(conn).values()) == {"done"}
    reasoning = conn.execute("SELECT reasoning FROM postrun_steps WHERE step = 'journal' AND agent_id = 'b'").fetchone()
    assert reasoning["reasoning"] == "REASONING-b"
    names = [e["event"] for e in read_events(tmp_path / "runs" / run_id / "events.jsonl")]
    assert names[0] == "postrun_started" and names[-1] == "postrun_finished"
    assert "postrun_step_finished" in names


async def test_packets_tell_each_agent_how_the_run_ended(tmp_path, community, make_run, postrun_reply):
    store, conn = community
    run_id = finished_run(conn, tmp_path, make_run)
    clients = {a: FakeModel(responder(postrun_reply)) for a in "abc"}
    await cycle(store, conn, tmp_path, clients).run(run_id)
    journal_request = clients["c"].calls[0][1][0]["content"]
    assert journal_request.startswith("# Post-run: journal")
    assert "The run ended by budget." in journal_request
    assert "The run ended without consensus." in journal_request
    assert "COMPETING-POSITIONS" in journal_request


async def test_review_citing_unknown_entries_is_corrected_then_recorded_as_failed(tmp_path, community, make_run, postrun_reply):
    store, conn = community
    run_id = finished_run(conn, tmp_path, make_run)
    fabricated = json.dumps({"more_of": "m", "less_of": "l", "private_note": "p", "evidence": [999],
                             "overall": 2, "overall_reason": "r"})
    clients = {
        "a": FakeModel(responder(postrun_reply, review=lambda request: fabricated)),
        "b": FakeModel(responder(postrun_reply)),
        "c": FakeModel(responder(postrun_reply)),
    }
    result = await cycle(store, conn, tmp_path, clients).run(run_id)
    assert result.status == "done" and result.failed == 2              # a→a and a→b
    assert conn.execute("SELECT COUNT(*) FROM reviews WHERE reviewer = 'a'").fetchone()[0] == 0
    kinds = [r["kind"] for r in conn.execute("SELECT kind FROM failures WHERE agent_id = 'a'")]
    assert kinds == ["postrun:review:invalid_reply", "postrun:review:invalid_reply"]
    assert steps(conn)[("review", "a", "b")] == "failed"
    assert journal_section(conn, "a", run_id, "before") == "Thoughts."   # the rest of the cycle still ran


async def test_rerun_retries_only_unfinished_calls_and_never_duplicates(tmp_path, community, make_run, postrun_reply):
    store, conn = community
    run_id = finished_run(conn, tmp_path, make_run)

    def crash(request):
        raise RuntimeError("process died")

    first = {
        "a": FakeModel(responder(postrun_reply, review=lambda r: crash(r) if "review of Bea" in r else postrun_reply(r))),
        "b": FakeModel(responder(postrun_reply)),
        "c": FakeModel(responder(postrun_reply)),
    }
    result = await cycle(store, conn, tmp_path, first).run(run_id)
    assert result.failed == 1 and steps(conn)[("review", "a", "b")] == "failed"

    second = {a: FakeModel(responder(postrun_reply)) for a in "abc"}
    result = await cycle(store, conn, tmp_path, second).run(run_id)
    assert (result.status, result.failed) == ("done", 0)
    assert [len(second[a].calls) for a in "abc"] == [1, 1, 0]        # a's review of b, then b's response to it
    assert conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 5
    assert conn.execute("SELECT COUNT(*) FROM journals WHERE section = 'before'").fetchone()[0] == 3

    third = {a: FakeModel(responder(postrun_reply)) for a in "abc"}
    await cycle(store, conn, tmp_path, third).run(run_id)
    assert [len(third[a].calls) for a in "abc"] == [0, 0, 0]


async def test_a_step_where_every_call_hits_a_model_error_pauses(tmp_path, community, make_run, postrun_reply):
    store, conn = community
    run_id = finished_run(conn, tmp_path, make_run)
    down = {a: FakeModel(fail_times=10) for a in "abc"}
    result = await cycle(store, conn, tmp_path, down).run(run_id)
    assert result.status == "paused"
    assert conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 0
    names = [e["event"] for e in read_events(tmp_path / "runs" / run_id / "events.jsonl")]
    assert names[-1] == "postrun_paused"
    up = {a: FakeModel(responder(postrun_reply)) for a in "abc"}
    assert (await cycle(store, conn, tmp_path, up).run(run_id)).status == "done"


async def test_cycle_refuses_unknown_and_unclosed_runs(tmp_path, community, make_run):
    store, conn = community
    make_run(conn, "open", ("a",))
    with pytest.raises(KeyError):
        await cycle(store, conn, tmp_path, {}).run("missing")
    with pytest.raises(ValueError, match="not closed"):
        await cycle(store, conn, tmp_path, {}).run("open")


async def test_requests_never_carry_another_agents_private_notes(tmp_path, community, make_run, postrun_reply):
    store, conn = community
    store.write_private("b", "lessons.md", "LESSON-OF-B")
    run_id = finished_run(conn, tmp_path, make_run)
    clients = {a: FakeModel(responder(postrun_reply)) for a in "abc"}
    await cycle(store, conn, tmp_path, clients).run(run_id)
    for agent in "ac":
        for system, messages, _ in clients[agent].calls:
            assert "LESSON-OF-B" not in system and all("LESSON-OF-B" not in m["content"] for m in messages)
    assert any("LESSON-OF-B" in system for system, _, _ in clients["b"].calls)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_postrun.py -q`
Expected: FAIL — `ImportError: cannot import name 'PostRun' from 'aiswarm.postrun'`.

- [ ] **Step 3: Implement the cycle**

Create `src/aiswarm/postrun/cycle.py`:

```python
"""The post-run cycle: plain code that asks each agent to reflect, review, and respond. It never steers."""

import asyncio
import json
import sqlite3
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aiswarm.board import Board, Entry
from aiswarm.db import now_iso, transaction
from aiswarm.events import EventLog
from aiswarm.journal import JOURNAL_MAX, write_journal
from aiswarm.models.base import ModelClient, ModelError
from aiswarm.postrun.ask import ReplyError, ask
from aiswarm.postrun.feedback import feedback_packet, interactions, transcript
from aiswarm.postrun.replies import JournalReply, ResponsesReply, ReviewReply
from aiswarm.profiles import ProfileStore
from aiswarm.prompts import render
from aiswarm.reviews import add_response, add_review, render_review, reviews_of
from aiswarm.view import Who, make_who

STEPS = ("journal", "review", "responses")
_NOTES = ("self.md", "lessons.md", "peer-notes.md")


@dataclass(frozen=True)
class PostRunResult:
    run_id: str
    status: str
    failed: int = 0


@dataclass(frozen=True)
class _Context:
    run_id: str
    goal: str
    agent_ids: list[str]
    entries: list[Entry]
    who: Who
    answer: str
    events: EventLog


@dataclass(frozen=True)
class _Call:
    step: str
    agent_id: str
    subject: str
    run: Callable[[], Awaitable[str | None]]


class PostRun:
    def __init__(
        self, *, conn: sqlite3.Connection, store: ProfileStore, runs_dir: Path,
        clients: Mapping[str, ModelClient], sink: Callable[[dict[str, Any]], None] | None = None,
    ):
        self.conn = conn
        self.store = store
        self.runs_dir = runs_dir
        self.clients = clients
        self.sink = sink

    async def run(self, run_id: str) -> PostRunResult:
        row = self.conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        if row["status"] != "closed":
            raise ValueError(f"run {run_id} is not closed")
        answer = self.runs_dir / run_id / "report" / "answer.md"
        ctx = _Context(
            run_id=run_id, goal=row["goal"], agent_ids=json.loads(row["agent_ids"]),
            entries=Board(self.conn, run_id).entries(),
            who=make_who({i: self.store.public(i) for i in self.store.ids()}),
            answer=answer.read_text() if answer.exists() else "",
            events=EventLog(self.runs_dir / run_id / "events.jsonl", sink=self.sink),
        )
        ctx.events.emit("postrun_started", run_id=run_id)
        failed = 0
        for step in STEPS:
            pending = [call for call in self._calls(step, ctx) if not self._done(run_id, call)]
            if not pending:
                continue
            outcomes = await asyncio.gather(*(self._attempt(ctx, call) for call in pending))
            failed += sum(outcome != "done" for outcome in outcomes)
            if all(outcome == "model_error" for outcome in outcomes):
                ctx.events.emit("postrun_paused", step=step)
                return PostRunResult(run_id, "paused", failed)
        ctx.events.emit("postrun_finished", failed=failed)
        return PostRunResult(run_id, "done", failed)

    # --- bookkeeping -------------------------------------------------------------------------

    def _calls(self, step: str, ctx: _Context) -> list[_Call]:
        if step == "journal":
            return [_Call(step, a, "", lambda a=a: self._journal(ctx, a)) for a in ctx.agent_ids]
        if step == "review":
            files = [(r["path"], r["author"]) for r in self.conn.execute(
                "SELECT path, author FROM files WHERE run_id = ?", (ctx.run_id,))]
            pairs = interactions(ctx.entries, files, ctx.agent_ids)
            return [
                _Call(step, reviewer, reviewee, lambda r=reviewer, e=reviewee: self._review(ctx, r, e))
                for reviewer in ctx.agent_ids for reviewee in [*sorted(pairs[reviewer]), reviewer]
            ]
        if step == "responses":
            return [
                _Call(step, a, "", lambda a=a: self._responses(ctx, a))
                for a in ctx.agent_ids if reviews_of(self.conn, a, run_id=ctx.run_id)
            ]
        raise ValueError(f"unknown step {step!r}")

    def _done(self, run_id: str, call: _Call) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM postrun_steps WHERE run_id = ? AND step = ? AND agent_id = ? AND subject = ? "
            "AND status = 'done'", (run_id, call.step, call.agent_id, call.subject),
        ).fetchone() is not None

    def _mark(self, run_id: str, call: _Call, status: str, detail: str, reasoning: str | None) -> None:
        self.conn.execute(
            "INSERT INTO postrun_steps (run_id, step, agent_id, subject, status, detail, reasoning, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (run_id, step, agent_id, subject) DO UPDATE SET "
            "status = excluded.status, detail = excluded.detail, reasoning = excluded.reasoning, "
            "updated_at = excluded.updated_at",
            (run_id, call.step, call.agent_id, call.subject, status, detail, reasoning, now_iso()),
        )

    async def _attempt(self, ctx: _Context, call: _Call) -> str:
        try:
            reasoning = await call.run()
        except ModelError as exc:
            kind, detail = "model_error", str(exc)
        except ReplyError as exc:
            kind, detail = "invalid_reply", str(exc)
        except Exception as exc:  # one agent's bad step must never take down the cycle
            kind, detail = "step_error", f"{type(exc).__name__}: {exc}"
        else:
            self._mark(ctx.run_id, call, "done", "", reasoning)
            ctx.events.emit("postrun_step_finished", step=call.step, agent=call.agent_id, subject=call.subject, status="done")
            return "done"
        self._mark(ctx.run_id, call, "failed", detail, None)
        self.conn.execute(
            "INSERT INTO failures (run_id, round, agent_id, kind, detail, created_at) VALUES (?, 0, ?, ?, ?, ?)",
            (ctx.run_id, call.agent_id, f"postrun:{call.step}:{kind}", detail, now_iso()),
        )
        ctx.events.emit("postrun_step_finished", step=call.step, agent=call.agent_id, subject=call.subject, status=kind)
        return kind

    # --- inputs ------------------------------------------------------------------------------

    def _system(self, ctx: _Context, agent_id: str) -> str:
        private = self.store.private(agent_id)
        notes = [f"## {name}\n{private[name].strip()}" for name in _NOTES if private[name].strip()]
        block = "\n# Your private notes (only you can see these)\n" + "\n\n".join(notes) if notes else ""
        return render("postrun_system.md", display=ctx.who(agent_id), temperament=private["temperament.md"].strip(),
                      private_notes=block)

    def _packet(self, ctx: _Context, agent_id: str) -> str:
        failures = [(r["round"], r["kind"], r["detail"]) for r in self.conn.execute(
            "SELECT round, kind, detail FROM failures WHERE run_id = ? AND agent_id = ? AND round > 0 ORDER BY id",
            (ctx.run_id, agent_id))]
        return feedback_packet(agent_id, run_id=ctx.run_id, goal=ctx.goal, entries=ctx.entries, who=ctx.who,
                               answer=ctx.answer, failures=failures)

    # --- steps -------------------------------------------------------------------------------

    async def _journal(self, ctx: _Context, agent_id: str) -> str | None:
        request = render("journal_request.md", packet=self._packet(ctx, agent_id), journal_max=JOURNAL_MAX)
        answer = await ask(self.clients[agent_id], self._system(ctx, agent_id), request, JournalReply)
        write_journal(self.conn, self.store, agent_id, ctx.run_id, "before", answer.reply.journal)
        return answer.reasoning

    async def _review(self, ctx: _Context, reviewer: str, reviewee: str) -> str | None:
        client = self.clients[reviewer]
        own = reviewer == reviewee
        name = ctx.who(reviewee)
        request = render(
            "review_request.md",
            heading="yourself" if own else name,
            run_id=ctx.run_id, goal=ctx.goal,
            authors="you" if own else f"you and {name}",
            transcript=transcript(ctx.entries, ctx.who, {reviewer, reviewee}, max(2_000, int(client.context_window * 1.5))),
            instruction="Review your own work in this run." if own else f"Review the work of {name} in this run.",
            subject="you" if own else "this colleague",
            object="yourself" if own else "them",
            private_reader="Only you will read this." if own else f"Only {name} will read this.",
            visibility=(
                "Other members will not see this self-review." if own else
                "Your review is signed with your name. Other members can read it in later runs, except private_note."
            ),
        )
        valid = {entry.id for entry in ctx.entries}

        def check(reply: ReviewReply) -> None:
            unknown = [i for i in reply.evidence if i not in valid]
            if unknown:
                raise ReplyError(f"entry #{unknown[0]} is not part of this run")

        answer = await ask(client, self._system(ctx, reviewer), request, ReviewReply, check)
        r = answer.reply
        add_review(self.conn, run_id=ctx.run_id, reviewer=reviewer, reviewee=reviewee, more_of=r.more_of,
                   less_of=r.less_of, private_note=r.private_note, evidence=r.evidence, overall=r.overall,
                   overall_reason=r.overall_reason)
        return answer.reasoning

    async def _responses(self, ctx: _Context, agent_id: str) -> str | None:
        reviews = reviews_of(self.conn, agent_id, run_id=ctx.run_id)
        text = "\n\n".join(render_review(r, ctx.who, private=True) for r in reviews)
        request = render("responses_request.md", run_id=ctx.run_id, reviews=text)
        expected = sorted(r.id for r in reviews)

        def check(reply: ResponsesReply) -> None:
            if sorted(item.review_id for item in reply.responses) != expected:
                raise ReplyError(f"give exactly one response for each review id: {expected}")

        answer = await ask(self.clients[agent_id], self._system(ctx, agent_id), request, ResponsesReply, check)
        with transaction(self.conn):
            for item in answer.reply.responses:
                add_response(self.conn, item.review_id, item.stance, item.reason)
        return answer.reasoning
```

Replace `src/aiswarm/postrun/__init__.py` with:

```python
"""The post-run cycle: journals, reviews, responses, and updates."""

from aiswarm.postrun.cycle import STEPS, PostRun, PostRunResult

__all__ = ["STEPS", "PostRun", "PostRunResult"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_postrun.py -q`
Expected: PASS.

Then: `set -o pipefail; uv run pytest -q 2>&1 | tail -3`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/aiswarm/postrun/__init__.py src/aiswarm/postrun/cycle.py tests/test_postrun.py
git commit -m "feat: run the post-run cycle — journals, reviews, responses — resumably"
```

---

### Task 6: The update step — second journal, lessons, and self-rewrites

**Files:**
- Modify: `src/aiswarm/postrun/cycle.py`
- Test: `tests/test_postrun.py`

**Interfaces:**
- Consumes: `UpdateReply` (Task 4), `journal_section` (Task 2), `responses_for`, `render_review` (Task 2), `ProfileStore.write_private` / `set_public`, `ProfileError`, size constants (`PRIVATE_MAX`, `NAME_MAX`, `HEADLINE_MAX`, `INTRO_MAX`).
- Produces: `STEPS == ("journal", "review", "responses", "update")`; per agent, the `after` journal section (with a "Rejected changes" list appended when any rewrite was rejected), a new `lessons.md`, and any accepted rewrites, each a git commit.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_postrun.py`:

```python


def update_reply(**fields):
    base = {"journal": "AFTER", "lessons": "NEW-LESSONS", "self_description": None, "temperament": None,
            "peer_notes": None, "public_profile": None}
    return lambda request: json.dumps({**base, **fields})


async def test_update_writes_second_journal_lessons_and_rewrites(tmp_path, community, make_run, postrun_reply):
    store, conn = community
    run_id = finished_run(conn, tmp_path, make_run)
    clients = {a: FakeModel(responder(postrun_reply)) for a in "bc"}
    clients["a"] = FakeModel(responder(postrun_reply, update=update_reply(
        self_description="NEW-SELF", temperament="NEW-TEMPERAMENT", peer_notes="NEW-NOTES",
        public_profile={"name": "Alder", "headline": "New headline", "introduction": "New intro."})))
    result = await cycle(store, conn, tmp_path, clients).run(run_id)
    assert (result.status, result.failed) == ("done", 0)
    private = store.private("a")
    assert private["lessons.md"] == "NEW-LESSONS" and private["self.md"] == "NEW-SELF"
    assert private["temperament.md"] == "NEW-TEMPERAMENT" and private["peer-notes.md"] == "NEW-NOTES"
    profile = store.public("a")
    assert (profile.name, profile.former_names) == ("Alder", ["Alpha"])
    assert journal_section(conn, "a", run_id, "after") == "AFTER"
    assert store.private("b")["lessons.md"] == "Listen first."      # default responder
    assert store.private("b")["temperament.md"].strip() == "Temperament of b."


async def test_update_request_shows_reviews_with_private_notes_and_responses(tmp_path, community, make_run, postrun_reply):
    store, conn = community
    run_id = finished_run(conn, tmp_path, make_run)
    clients = {a: FakeModel(responder(postrun_reply)) for a in "abc"}
    await cycle(store, conn, tmp_path, clients).run(run_id)
    update = next(m[0]["content"] for _, m, _ in clients["a"].calls if m[0]["content"].startswith("# Post-run: update"))
    assert "by Bea (b)" in update and "private note: Between us." in update and "response: accept — Fair." in update
    assert "## Your journal entry from before the reviews\n\nThoughts." in update
    c_update = next(m[0]["content"] for _, m, _ in clients["c"].calls if m[0]["content"].startswith("# Post-run: update"))
    assert "private note" not in c_update                       # nobody reviewed c


async def test_rejected_rewrites_keep_previous_versions_and_are_noted(tmp_path, community, make_run, postrun_reply):
    store, conn = community
    run_id = finished_run(conn, tmp_path, make_run)
    clients = {a: FakeModel(responder(postrun_reply)) for a in "bc"}
    clients["a"] = FakeModel(responder(postrun_reply, update=update_reply(
        temperament="   ", self_description="KEPT-SELF",
        public_profile={"name": "bea", "headline": "h", "introduction": "i"})))
    result = await cycle(store, conn, tmp_path, clients).run(run_id)
    assert result.failed == 0
    assert store.private("a")["temperament.md"].strip() == "Temperament of a."
    assert store.public("a").name == "Alpha"
    assert store.private("a")["self.md"] == "KEPT-SELF" and store.private("a")["lessons.md"] == "NEW-LESSONS"
    after = journal_section(conn, "a", run_id, "after")
    assert after.startswith("AFTER\n\nRejected changes (previous versions kept):\n")
    assert "- temperament.md: temperament must not be empty" in after
    assert "- public profile: the name 'bea' is taken" in after


async def test_lessons_over_the_cap_get_one_correction(tmp_path, community, make_run, postrun_reply):
    store, conn = community
    run_id = finished_run(conn, tmp_path, make_run)
    replies = iter([update_reply(lessons="x" * 5000), update_reply(lessons="short")])
    clients = {a: FakeModel(responder(postrun_reply)) for a in "bc"}
    clients["a"] = FakeModel(responder(postrun_reply, update=lambda request: next(replies)(request)))
    await cycle(store, conn, tmp_path, clients).run(run_id)
    assert store.private("a")["lessons.md"] == "short"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_postrun.py -q -k "update or rejected or lessons"`
Expected: FAIL — lessons/journal unchanged (`assert '' == 'NEW-LESSONS'`, `journal_section(...) is None`).

- [ ] **Step 3: Implement**

In `src/aiswarm/postrun/cycle.py`:

Update imports:

```python
from aiswarm.journal import JOURNAL_MAX, journal_section, write_journal
from aiswarm.postrun.replies import JournalReply, ResponsesReply, ReviewReply, UpdateReply
from aiswarm.profiles import HEADLINE_MAX, INTRO_MAX, NAME_MAX, PRIVATE_MAX, ProfileError, ProfileStore
from aiswarm.reviews import add_response, add_review, render_review, responses_for, reviews_of
```

Change `STEPS`:

```python
STEPS = ("journal", "review", "responses", "update")
```

In `_calls`, before the final `raise`, add:

```python
        if step == "update":
            return [_Call(step, a, "", lambda a=a: self._update(ctx, a)) for a in ctx.agent_ids]
```

Append the step method to `PostRun`:

```python
    async def _update(self, ctx: _Context, agent_id: str) -> str | None:
        reviews = reviews_of(self.conn, agent_id, run_id=ctx.run_id)
        responses = responses_for(self.conn, [r.id for r in reviews])
        request = render(
            "update_request.md",
            packet=self._packet(ctx, agent_id),
            first_journal=journal_section(self.conn, agent_id, ctx.run_id, "before") or "(none)",
            reviews="\n\n".join(
                render_review(r, ctx.who, private=True, response=responses.get(r.id)) for r in reviews
            ) or "(no reviews)",
            journal_max=JOURNAL_MAX, private_max=PRIVATE_MAX, name_max=NAME_MAX, headline_max=HEADLINE_MAX,
            intro_max=INTRO_MAX,
        )
        answer = await ask(self.clients[agent_id], self._system(ctx, agent_id), request, UpdateReply)
        reply = answer.reply
        rejected: list[str] = []
        for filename, content in (
            ("lessons.md", reply.lessons), ("self.md", reply.self_description),
            ("temperament.md", reply.temperament), ("peer-notes.md", reply.peer_notes),
        ):
            if content is None:
                continue
            try:
                self.store.write_private(agent_id, filename, content)
            except ProfileError as exc:
                rejected.append(f"{filename}: {exc}")
        if reply.public_profile is not None:
            profile = reply.public_profile
            try:
                self.store.set_public(agent_id, profile.name, profile.headline, profile.introduction)
            except ProfileError as exc:
                rejected.append(f"public profile: {exc}")
        journal = reply.journal
        if rejected:
            journal += "\n\nRejected changes (previous versions kept):\n" + "\n".join(f"- {r}" for r in rejected)
        write_journal(self.conn, self.store, agent_id, ctx.run_id, "after", journal)
        return answer.reasoning
```

`ProfileStore.write_private` already rejects an empty temperament (`temperament must not be empty`) and over-size files; `set_public` already rejects a taken name (`the name 'bea' is taken`). A whitespace-only temperament is caught because `_check_private` tests `content.strip()`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_postrun.py -q`
Expected: PASS (all tests, including Task 5's: `test_rerun_retries_only_unfinished_calls_and_never_duplicates` now also skips the update calls already done in the first pass, so the counts `[1, 1, 0]` and `[0, 0, 0]` still hold).

Then: `set -o pipefail; uv run pytest -q 2>&1 | tail -3`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/aiswarm/postrun/cycle.py tests/test_postrun.py
git commit -m "feat: post-run update step — second journal, lessons, validated self-rewrites"
```

---

### Task 7: Feed it back — journals, norms, reputation, and `read_reviews` in the View

**Files:**
- Modify: `src/aiswarm/board.py` (`Board.conn`)
- Modify: `src/aiswarm/actions.py` (`ReadReviews`)
- Modify: `src/aiswarm/prompts/agent_system.md`
- Modify: `src/aiswarm/agent.py` (`make_reader`)
- Modify: `src/aiswarm/view.py`
- Modify: `src/aiswarm/runner.py`
- Test: `tests/test_view.py`, `tests/test_agent.py`, `tests/test_runner.py`

**Interfaces:**
- Consumes: `reputation`, `Reputation`, `render_public_reviews` (Task 2); `ProfileStore.journals` (Task 2); `RunSettings.reputation_half_life_runs` (Task 1).
- Produces:
  - `Board.conn` (read-only property).
  - `ReadReviews(type="read_reviews", agent_id: str)`, included in `READ_ACTIONS` and the step schema.
  - `make_reader(workspace, entries, who, max_chars=8000, reviews: Callable[[str], str] | None = None)`.
  - `ViewInputs` gains `journals: tuple[tuple[str, str], ...] = ()`, `norms: str = ""`, `reputations: Mapping[str, Reputation] = {}` (defaults keep existing callers valid); `collect_inputs(..., half_life: float = 5.0)`.
  - View sections: own journals inside the private-notes section as `## journal/<run-id>.md`; `# Community norms` when `community/norms.md` is non-empty; member lines end with ` · <reputation label>`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_view.py`:

```python


def test_view_shows_own_latest_journals_but_never_anyone_elses(tmp_path, community, make_run):
    store, conn, board, ws = setup(tmp_path, community, make_run)
    for n, run_id in enumerate(("20260101-000000-aaaa", "20260102-000000-bbbb", "20260103-000000-cccc")):
        store.write_journal_file("a", run_id, f"JOURNAL-A-{n}\n")
    store.write_journal_file("b", "20260103-000000-cccc", "JOURNAL-B\n")
    view = view_for("a", 2, store, board, ws)
    assert "## journal/20260103-000000-cccc.md\nJOURNAL-A-2" in view and "JOURNAL-A-1" in view
    assert "JOURNAL-A-0" not in view
    assert "JOURNAL-B" not in view
    assert "JOURNAL-A" not in view_for("b", 2, store, board, ws)


def test_view_shows_norms_when_present(tmp_path, community, make_run):
    store, conn, board, ws = setup(tmp_path, community, make_run)
    assert "# Community norms" not in view_for("a", 2, store, board, ws)
    (store.root / "norms.md").write_text("1. Cite your sources.\n")
    assert "# Community norms\n1. Cite your sources." in view_for("a", 2, store, board, ws)


def test_member_lines_show_reputation(tmp_path, community, make_run):
    from aiswarm.reviews import add_review

    store, conn, board, ws = setup(tmp_path, community, make_run)
    conn.execute("UPDATE runs SET status = 'closed' WHERE id = 'r1'")
    add_review(conn, run_id="r1", reviewer="a", reviewee="b", more_of="m", less_of="l", private_note="p",
               evidence=[1], overall=4, overall_reason="r")
    view = view_for("a", 2, store, board, ws)
    assert "- Bea (b) — Bea headline · ★4.0 (1 review)" in view
    assert "- Cyd (c) — Cyd headline · no reviews yet" in view
```

Append to `tests/test_agent.py`:

```python


def test_reader_answers_read_reviews():
    from aiswarm.actions import ReadReviews

    read = make_reader(None, [], lambda a: a, reviews=lambda agent_id: f"REVIEWS-OF-{agent_id}")
    assert read(ReadReviews(type="read_reviews", agent_id="b")) == "REVIEWS-OF-b"
    assert make_reader(None, [], lambda a: a)(ReadReviews(type="read_reviews", agent_id="b")) == "Reviews are not available."


def test_read_reviews_parses_and_is_in_the_schema():
    from aiswarm.actions import READ_ACTIONS, ReadReviews, parse_action, step_schema

    action = parse_action('{"action": {"type": "read_reviews", "agent_id": "b"}}')
    assert isinstance(action, ReadReviews) and isinstance(action, READ_ACTIONS)
    assert "read_reviews" in json.dumps(step_schema())
```

(If `tests/test_agent.py` does not already `import json`, add it at the top.)

Append to `tests/test_runner.py`:

```python


async def test_agents_can_read_peer_reviews_but_not_private_notes(tmp_path, community, runner_factory):
    from aiswarm.reviews import add_review

    store, conn = community
    conn.execute("INSERT INTO runs (id, goal, status, agent_ids, created_at) VALUES ('old', 'g', 'closed', '[]', 't')")
    add_review(conn, run_id="old", reviewer="c", reviewee="b", more_of="PUBLIC-MORE", less_of="l",
               private_note="SECRET-NOTE", evidence=[1], overall=5, overall_reason="r")
    reader = FakeModel([
        json.dumps({"action": {"type": "propose", "answer_summary": "x", "method": "m"}}),
        json.dumps({"action": {"type": "read_reviews", "agent_id": "b"}}),
        json.dumps({"action": {"type": "pass"}}),
    ])
    clients = {"a": reader, "b": FakeModel(), "c": FakeModel()}
    runner = runner_factory(store, conn, clients, max_rounds=2)
    await runner.run(runner.start("Pick a colour", ["a", "b", "c"]))
    read_result = reader.calls[2][1][-1]["content"]
    assert read_result.startswith("Read result:\nReview")
    assert "PUBLIC-MORE" in read_result and "SECRET-NOTE" not in read_result
```

(Round 1 needs a `propose`; round 2 reads then passes. `FakeModel()` with no script passes, which is invalid in round 1 — it is corrected once and recorded as a failed `pass`, which does not affect this test.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_view.py tests/test_agent.py tests/test_runner.py -q`
Expected: FAIL — journals/norms/reputation missing from the view; `ImportError: cannot import name 'ReadReviews'`.

- [ ] **Step 3: Implement the read action**

In `src/aiswarm/board.py`, add to `Board` (after `__init__`):

```python
    @property
    def conn(self) -> sqlite3.Connection:
        return self._conn
```

In `src/aiswarm/actions.py`, after `class ReadEntry`:

```python
class ReadReviews(_Action):
    type: Literal["read_reviews"]
    agent_id: Text
```

and update the unions:

```python
READ_ACTIONS = (ReadFile, ListFiles, ReadEntry, ReadReviews)

StepAction = Annotated[
    Propose | Critique | Question | Adopt | Refine | WriteFile | CallConsensus | Vote | Pass
    | ReadFile | ListFiles | ReadEntry | ReadReviews,
    Field(discriminator="type"),
]
```

In `src/aiswarm/prompts/agent_system.md`, after the `read_entry` line, add (verbatim):

```
- {"type": "read_reviews", "agent_id": "..."}: read the reviews members have written about a member, and that member's responses.
```

In `src/aiswarm/agent.py`, import `ReadReviews` alongside the other actions, then change `make_reader`'s signature and add a branch before the `read_entry` fallback:

```python
def make_reader(
    workspace: Workspace, entries: Sequence[Entry], who: Who, max_chars: int = 8_000,
    reviews: Callable[[str], str] | None = None,
) -> Reader:
```

```python
        if isinstance(action, ReadReviews):
            return reviews(action.agent_id) if reviews else "Reviews are not available."
```

- [ ] **Step 4: Implement the View changes**

In `src/aiswarm/view.py`:

Add imports:

```python
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from aiswarm.reviews import Reputation, reputation
```

Extend `ViewInputs` (new fields go last, with defaults):

```python
    journals: tuple[tuple[str, str], ...] = ()
    norms: str = ""
    reputations: Mapping[str, Reputation] = field(default_factory=dict)
```

Change `collect_inputs`'s signature to add `half_life: float = 5.0` after `view_entries: int,` and pass the new fields in its `return ViewInputs(...)`:

```python
        journals=tuple(store.journals(agent_id, 2)),
        norms=_norms(store),
        reputations={i: reputation(board.conn, i, half_life) for i in store.ids()},
```

Add the helper above `collect_inputs`:

```python
def _norms(store: ProfileStore) -> str:
    path = store.root / "norms.md"
    return path.read_text() if path.exists() else ""
```

In `build_view`, replace the private-notes block:

```python
    notes = [f"## {name}\n{text.strip()}" for name, text in inputs.own_notes.items() if text.strip()]
    notes += [f"## journal/{run_id}.md\n{text.strip()}" for run_id, text in inputs.journals if text.strip()]
    if notes:
        fixed.append("# Your private notes (only you can see these)\n" + "\n\n".join(notes))
    if inputs.norms.strip():
        fixed.append("# Community norms\n" + inputs.norms.strip())
```

and in the members loop, replace the `block = ...` line:

```python
        rep = inputs.reputations.get(agent_id)
        suffix = f" · {rep.label()}" if rep else ""
        block = f"- {profile.name} ({agent_id}) — {profile.headline}{suffix}\n  {profile.introduction}"
```

- [ ] **Step 5: Wire the Runner**

In `src/aiswarm/runner.py`, import `render_public_reviews` from `aiswarm.reviews`, then in `_step`:

```python
            inputs = collect_inputs(agent_id=agent_id, goal=goal, round=rnd, board=board, store=self.store,
                                    workspace=ws, view_entries=self.settings.view_entries,
                                    half_life=self.settings.reputation_half_life_runs)
```

and

```python
                max_reads=self.settings.max_reads,
                reader=make_reader(ws, inputs.entries, who, budget // 4,
                                   reviews=lambda a: render_public_reviews(self.conn, a, who, budget // 4)),
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_view.py tests/test_agent.py tests/test_runner.py -q`
Expected: PASS.

Then: `set -o pipefail; uv run pytest -q 2>&1 | tail -3`
Expected: all pass (existing privacy and trimming tests still pass).

- [ ] **Step 7: Commit**

```bash
git add src/aiswarm/board.py src/aiswarm/actions.py src/aiswarm/prompts/agent_system.md src/aiswarm/agent.py src/aiswarm/view.py src/aiswarm/runner.py tests/test_view.py tests/test_agent.py tests/test_runner.py
git commit -m "feat: show journals, norms and reputation in the View; add read_reviews"
```

---

### Task 8: CLI — automatic cycle, `postrun`, and a richer `agents show`

**Files:**
- Modify: `src/aiswarm/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `PostRun`, `PostRunResult` (Task 5); `limit_clients` (Task 1); `reputation`, `reviews_of`, `responses_for`, `render_review` (Task 2); `ProfileStore.journals` (Task 2).
- Produces: `aiswarm run … [--no-postrun]` (runs the cycle after a run closes, in the same event loop and behind the same limiter); `aiswarm postrun RUN_ID`; `aiswarm agents show ID` printing reputation, reviews received (with private notes and responses — this is the observer's view), and the latest journal.

- [ ] **Step 1: Write the failing tests**

In `tests/test_cli.py`, change the `project` fixture to take `postrun_reply` and answer post-run requests first:

```python
@pytest.fixture
def project(tmp_path, monkeypatch, postrun_reply):
    counter = itertools.count(1)

    def respond(system, messages):
        first = messages[0]["content"]
        reply = postrun_reply(first)
        if reply:
            return reply
        if "introduce yourself" in first.lower():
            ...  # unchanged
```

(keep the rest of the function body exactly as it is).

Append:

```python


def make_agents(config, *ids):
    for agent_id in ids:
        result = invoke("agents", "new", agent_id, "--model", "local", "--temperament", f"T{agent_id}.", "-c", config)
        assert result.exit_code == 0, result.output


def run_id_from(output):
    return re.search(r"run (\S+) closed", output).group(1)


def test_run_triggers_the_postrun_cycle(project):
    tmp_path, config = project
    make_agents(config, "a", "b")
    result = invoke("run", "Pick a colour", "--max-rounds", "2", "-c", config)
    assert result.exit_code == 0, result.output
    assert "— post-run —" in result.output and "post-run done (0 failed steps)" in result.output
    run_id = run_id_from(result.output)
    assert (tmp_path / "community" / "agents" / "a" / "journal" / f"{run_id}.md").exists()
    shown = invoke("agents", "show", "a", "-c", config)
    assert "reputation: no reviews yet" in shown.output
    assert "## Reviews received" in shown.output and "Self-review" in shown.output
    assert "## Latest journal" in shown.output and "After reviews." in shown.output


def test_no_postrun_skips_the_cycle_and_postrun_runs_it_later(project):
    tmp_path, config = project
    make_agents(config, "a")
    result = invoke("run", "Pick a colour", "--max-rounds", "1", "--no-postrun", "-c", config)
    assert result.exit_code == 0, result.output
    run_id = run_id_from(result.output)
    assert "post-run" not in result.output
    assert not (tmp_path / "community" / "agents" / "a" / "journal").exists()
    later = invoke("postrun", run_id, "-c", config)
    assert later.exit_code == 0, later.output
    assert "post-run done" in later.output
    assert (tmp_path / "community" / "agents" / "a" / "journal" / f"{run_id}.md").exists()
    again = invoke("postrun", run_id, "-c", config)
    assert again.exit_code == 0 and "post-run done (0 failed steps)" in again.output


def test_postrun_rejects_unknown_runs(project):
    tmp_path, config = project
    result = invoke("postrun", "nope", "-c", config)
    assert result.exit_code == 1 and "no run 'nope'" in result.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -q`
Expected: FAIL — no `— post-run —` output; `No such command 'postrun'`; `No such option: --no-postrun`.

- [ ] **Step 3: Implement**

In `src/aiswarm/cli.py`:

Add imports:

```python
from aiswarm.models.base import ModelError, limit_clients
from aiswarm.postrun import PostRun, PostRunResult
from aiswarm.reviews import render_review, reputation, responses_for, reviews_of
from aiswarm.view import make_who
```

(`ModelError` is already imported from `aiswarm.models.base`; merge into one import line.)

Extract the per-agent checks from `run` into a helper and use it in both commands:

```python
def _clients(cfg: Config, store: ProfileStore, agent_ids: list[str]) -> dict[str, Any]:
    for agent_id in agent_ids:
        if agent_id not in store.ids():
            _fail(f"no agent {agent_id!r}")
        if store.public(agent_id) is None:
            _fail(f"{agent_id} has no public profile; run `aiswarm agents introduce {agent_id}`")
        if store.record(agent_id).model not in cfg.models:
            _fail(f"{agent_id} uses unknown model backend {store.record(agent_id).model!r}")
    return {a: make_client(cfg.models[store.record(a).model], cfg.run.model_retries) for a in agent_ids}


def _report_postrun(run_id: str, result: PostRunResult) -> None:
    if result.status == "paused":
        typer.echo(f"post-run paused; resume with `aiswarm postrun {run_id}`")
    else:
        typer.echo(f"post-run done ({result.failed} failed steps)")
```

Extend `_print_event`:

```python
    elif kind == "postrun_started":
        typer.echo("— post-run —")
    elif kind == "postrun_step_finished":
        subject = f" → {event['subject']}" if event.get("subject") else ""
        status = "" if event["status"] == "done" else f" ({event['status']})"
        typer.echo(f"  {event['step']} {event['agent']}{subject}{status}")
```

Replace the body of `run` from the per-agent `for` loop to the end with:

```python
    clients = _clients(cfg, store, agent_ids)

    async def go() -> tuple[Any, PostRunResult | None]:
        limited = limit_clients(clients, cfg.run.max_concurrent_calls)
        runner = Runner(settings=cfg.run, conn=conn, store=store, runs_dir=cfg.runs_dir, clients=limited,
                        sink=_print_event)
        run_id = resume or runner.start(goal or "", agent_ids)
        result = await runner.run(run_id)
        post = None
        if result.status == "closed" and not no_postrun:
            post = await PostRun(conn=conn, store=store, runs_dir=cfg.runs_dir, clients=limited,
                                 sink=_print_event).run(run_id)
        return result, post

    result, post = asyncio.run(go())
    run_id = result.run_id
    if result.status == "paused":
        typer.echo(f"run {run_id} paused; resume with `aiswarm run --resume {run_id}`")
        return
    typer.echo(f"run {run_id} closed: {result.reason}")
    typer.echo(f"report: {cfg.runs_dir / run_id / 'report'}")
    if post:
        _report_postrun(run_id, post)
```

and add the option to `run`'s parameters (before `config`):

```python
    no_postrun: Annotated[bool, typer.Option("--no-postrun", help="Skip the post-run cycle.")] = False,
```

Add the `postrun` command after `run`:

```python
@app.command()
def postrun(run_id: str, config: ConfigOpt = DEFAULT_PATH) -> None:
    """Start, resume, or retry the post-run cycle (journals, reviews, responses, updates) for a closed run."""
    cfg = _load(config)
    store = ProfileStore(cfg.community_dir)
    conn = connect(cfg.db_path)
    row = conn.execute("SELECT status, agent_ids FROM runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        _fail(f"no run {run_id!r}")
    if row["status"] != "closed":
        _fail(f"run {run_id} is not closed; finish it with `aiswarm run --resume {run_id}`")
    clients = _clients(cfg, store, json.loads(row["agent_ids"]))

    async def go() -> PostRunResult:
        limited = limit_clients(clients, cfg.run.max_concurrent_calls)
        return await PostRun(conn=conn, store=store, runs_dir=cfg.runs_dir, clients=limited,
                             sink=_print_event).run(run_id)

    _report_postrun(run_id, asyncio.run(go()))
```

Replace `agents_show` with:

```python
@agents_app.command("show")
def agents_show(agent_id: str, config: ConfigOpt = DEFAULT_PATH) -> None:
    """Show an agent's public profile, reputation, private files, reviews, and latest journal (observer view)."""
    cfg = _load(config)
    store = ProfileStore(cfg.community_dir)
    if agent_id not in store.ids():
        _fail(f"no agent {agent_id!r}")
    conn = connect(cfg.db_path)
    profile = store.public(agent_id)
    if profile:
        typer.echo(f"# {profile.name} ({agent_id})\n{profile.headline}\n\n{profile.introduction}")
        if profile.former_names:
            typer.echo(f"\nformerly: {', '.join(profile.former_names)}")
    typer.echo(f"\nreputation: {reputation(conn, agent_id, cfg.run.reputation_half_life_runs).label()}")
    private = store.private(agent_id)
    for filename in PRIVATE_FILES:
        typer.echo(f"\n## {filename}\n{private[filename].strip() or '(empty)'}")
    who = make_who({i: store.public(i) for i in store.ids()})
    reviews = reviews_of(conn, agent_id, include_self=True)
    responses = responses_for(conn, [r.id for r in reviews])
    typer.echo("\n## Reviews received")
    typer.echo("\n\n".join(render_review(r, who, private=True, response=responses.get(r.id)) for r in reviews) or "(none)")
    journals = store.journals(agent_id, 1)
    if journals:
        typer.echo(f"\n## Latest journal\n{journals[0][1].strip()}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -q`
Expected: PASS (existing CLI tests too — their runs now include a post-run cycle answered by `postrun_reply`).

Then: `set -o pipefail; uv run pytest -q 2>&1 | tail -3`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/aiswarm/cli.py tests/test_cli.py
git commit -m "feat: run the post-run cycle after each run; add postrun command and richer agents show"
```

---

### Task 9: Live check on oMLX and spec status

**Files:**
- Modify: `docs/superpowers/specs/2026-10-10-aiswarm-postrun-design.md` (status line only)

**Interfaces:**
- Consumes: the whole cycle, a running oMLX server with `Qwen3.5-4B-MLX-4bit`, and the lab community in the session scratchpad (`lab-local`, agents a–d from 2026-10-10; config has `qwen4b` with `thinking = true` and `qwen4b-fast` with `thinking = false`).

- [ ] **Step 1: Run the cycle on an existing real run**

In the lab directory (never commit its `aiswarm.toml`; it holds the oMLX API key):

```bash
cd <scratchpad>/lab-local
/Users/andrew/Develop/90_PLAYGROUND/AISwarm/.venv/bin/aiswarm postrun 20261010-190121-2151
```

Expected: `— post-run —`, one line per call, then `post-run done (N failed steps)`. A handful of `invalid_reply` failures on the 4B model is acceptable; a `step_error` is a bug — read its detail in `failures` and fix with a failing test first.

- [ ] **Step 2: Inspect what the agents wrote**

```bash
/Users/andrew/Develop/90_PLAYGROUND/AISwarm/.venv/bin/aiswarm agents show a
sqlite3 community/community.db "select reviewer, reviewee, overall from reviews"
git -C community log --oneline | head -20
```

Expected: journals with both sections, reviews with ratings 1–5 citing real entry ids, responses, `lessons.md` written, and one git commit per change.

- [ ] **Step 3: Run one fresh run end to end**

```bash
/Users/andrew/Develop/90_PLAYGROUND/AISwarm/.venv/bin/aiswarm run "Design a fair way for a small team to divide unpleasant recurring chores." --max-rounds 8
```

Expected: the run, then the cycle, with no `step_error`; the next run's View (visible via an agent's step in `events.jsonl` timing only — never read Views into analysis) is not needed to verify; `agents show` reflects the new reputation.

- [ ] **Step 4: Mark the spec**

In `docs/superpowers/specs/2026-10-10-aiswarm-postrun-design.md`, change `- **Status:** Draft, awaiting review` to `- **Status:** Approved; Plan 2a implemented`.

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/specs/2026-10-10-aiswarm-postrun-design.md
git commit -m "docs: mark post-run spec approved and Plan 2a implemented"
```
