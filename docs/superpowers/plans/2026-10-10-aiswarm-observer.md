# AISwarm Observer Implementation Plan (Plan 3b)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `aiswarm observe` writes per-run and cross-run Markdown reports with PNG charts, built from deterministic metrics over the community store plus cached, evidence-checked Claude judgments, without anything ever reaching an agent.

**Architecture:** New package `aiswarm.observer`: `metrics` (SQL/git, no LLM) → `judge` + `codebook` (LLM calls cached in a separate `observer.db`) → `charts` + `report` → `observe` (orchestrator) → CLI. The community database is opened read-only; the Observer writes only under `observer.dir`.

**Tech Stack:** Python 3.14, uv, pydantic, typer, sqlite3, git subprocess, matplotlib 3.11.2 (Agg backend), pytest + pytest-asyncio (auto mode), the existing `FakeModel`.

**Spec:** `docs/superpowers/specs/2026-10-10-aiswarm-observer-design.md`

## Global Constraints

- Observation never feeds back: no module on the agent path may import `aiswarm.observer`; nothing the Observer writes goes into `community.db` or the community git repo.
- `community.db` is opened only via `connect_readonly` (`mode=ro`) inside the Observer.
- Judge prompts live in `src/aiswarm/prompts/observer/` and must not appear in `prompt_snapshot()`; config's `observer` section must not appear in run provenance.
- Only closed runs are analysed; runs with status `running`/`paused` are listed as "in progress".
- Every quote in a judgment must appear verbatim (after whitespace normalisation, case-sensitive) in the judge's input.
- A judgment gets at most one correction request; then it is stored `failed` and retried on the next `observe`.
- Judge prompts contain no `$` (they are rendered with `string.Template`).
- matplotlib must use the `Agg` backend (headless).
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Test command for the whole suite: `uv run pytest -q`.

## Review Focus

1. **Judge replies that quote text with different whitespace or curly vs straight quotes** — whitespace differences must pass; changed characters must fail (Task 6 tests both).
2. **A run with no journals, no reviews, or no previous run for an agent** — inputs must say "(none)" rather than crash or send empty prompts (Task 7 tests).
3. **Runs in the database from before migration 4 (no goal_id/category) and runs with no provenance rows** — reports must render with "ad hoc" category and no harness markers (Task 4 and Task 9 tests).
4. **`observe` while a run is in progress** — the in-progress run is skipped and listed, closed runs still report (Task 10 test).
5. **Community DB unchanged after `observe`** — git HEAD and DB bytes identical before and after (Task 11 test).

## File Structure

| File | Responsibility |
|---|---|
| `pyproject.toml` | add `matplotlib>=3.11.2` |
| `src/aiswarm/config.py` | `ObserverSettings`, `Config.observer`, path resolution, default config text |
| `src/aiswarm/provenance.py` | exclude `observer` from captured config |
| `src/aiswarm/db.py` | `connect_readonly(path)` |
| `src/aiswarm/gitrepo.py` | `show_at(path, before)`, `first_version(path)` |
| `src/aiswarm/profiles.py` | `file_at(agent_id, filename, before)`, `first_version(agent_id, filename)` |
| `src/aiswarm/observer/__init__.py` | package docstring |
| `src/aiswarm/observer/store.py` | `observer.db`: judgments cache, codebook |
| `src/aiswarm/observer/metrics.py` | all deterministic metrics |
| `src/aiswarm/observer/judge.py` | reply schemas, `Analysis`, `ANALYSES`, `Judge` |
| `src/aiswarm/observer/inputs.py` | builds judge inputs from community data |
| `src/aiswarm/observer/codebook.py` | open-coding merge and theme counts |
| `src/aiswarm/observer/charts.py` | headless trend charts |
| `src/aiswarm/observer/report.py` | Markdown for run reports and the summary |
| `src/aiswarm/observer/observe.py` | orchestrates one `observe` invocation |
| `src/aiswarm/prompts/observer/{themes,private_public,self_image,benchmark,codebook}.md` | judge prompts |
| `src/aiswarm/cli.py` | `aiswarm observe` |
| `tests/observer_helpers.py` | fixture helpers that insert runs, entries, reviews, journals |
| `tests/test_observer_*.py` | tests per module |

---

### Task 1: Config, dependency, provenance exclusion

**Files:**
- Modify: `pyproject.toml`, `src/aiswarm/config.py`, `src/aiswarm/provenance.py`
- Test: `tests/test_config.py`, `tests/test_provenance.py`

**Interfaces:**
- Produces: `ObserverSettings(judge: str | None = None, dir: Path = Path("observer"))`; `Config.observer: ObserverSettings`; `load_config` resolves `cfg.observer.dir` against the config file's directory.

- [ ] **Step 1: Add matplotlib**

Run: `uv add "matplotlib>=3.11.2"`
Expected: `pyproject.toml` dependencies include `matplotlib>=3.11.2`; `uv run python -c "import matplotlib; print(matplotlib.__version__)"` prints `3.11.2` or later.

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_config.py`:

```python
def test_observer_settings_default_and_resolve(tmp_path):
    cfg = load_config(write(tmp_path, '[models.claude]\nkind = "claude_cli"\n'))
    assert cfg.observer.judge is None
    assert cfg.observer.dir == (tmp_path / "observer").resolve()
    cfg = load_config(write(tmp_path, '[models.claude]\nkind = "claude_cli"\n[observer]\njudge = "claude"\ndir = "obs"\n'))
    assert cfg.observer.judge == "claude" and cfg.observer.dir == (tmp_path / "obs").resolve()


def test_observer_judge_must_name_a_backend(tmp_path):
    with pytest.raises(ValidationError, match="observer.judge"):
        load_config(write(tmp_path, '[models.claude]\nkind = "claude_cli"\n[observer]\njudge = "nope"\n'))
```

Append to `tests/test_provenance.py`:

```python
async def test_observer_settings_are_not_part_of_provenance(run_row):
    conn, run_id = run_row
    plain = await capture(conn, run_id, "start", make_config(), {"a": "local"}, probe=fake_probe)
    cfg = make_config()
    cfg.observer.judge = "claude"
    with_judge = await capture(conn, run_id, "resume", cfg, {"a": "local"}, probe=fake_probe)
    assert "observer" not in plain.config
    assert plain.fingerprint == with_judge.fingerprint
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_config.py tests/test_provenance.py`
Expected: FAIL — `Config` has no attribute `observer`.

- [ ] **Step 4: Implement**

In `src/aiswarm/config.py`, after `LibrarySettings`:

```python
class ObserverSettings(BaseModel):
    judge: str | None = None  # a backend that judges text for `aiswarm observe`; agents never see its output
    dir: Path = Path("observer")
```

Add to `Config` (after `library`): `observer: ObserverSettings = Field(default_factory=ObserverSettings)`.

In `Config._known_backends`, extend the tuple:

```python
        for label, name in (("report.narrator", self.report.narrator), ("library.embeddings", self.library.embeddings),
                            ("observer.judge", self.observer.judge)):
```

In `load_config`, after `cfg.runs_dir = ...`: `cfg.observer.dir = (base / cfg.observer.dir).resolve()`.

Append to `DEFAULT_CONFIG` (before the closing `"""`):

```
[observer]
# judge = "claude"                     # a backend that judges text for `aiswarm observe` (agents never see it)
dir = "observer"                       # Observer reports and its own database
```

In `src/aiswarm/provenance.py` `capture`, change the config line to:

```python
    config = _without_keys(cfg.model_dump(mode="json", exclude={"observer"}))  # the Observer is not the harness
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest -q tests/test_config.py tests/test_provenance.py`
Expected: PASS (including `test_default_config_is_valid`).

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock src/aiswarm/config.py src/aiswarm/provenance.py tests/test_config.py tests/test_provenance.py
git commit -m "feat: [observer] config section and matplotlib; observer settings stay out of run provenance

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Read-only community access and files at a point in time

**Files:**
- Modify: `src/aiswarm/db.py`, `src/aiswarm/gitrepo.py`, `src/aiswarm/profiles.py`
- Test: `tests/test_storage.py`, `tests/test_profiles.py`

**Interfaces:**
- Produces: `connect_readonly(path: Path) -> sqlite3.Connection`; `GitRepo.show_at(path: str, before: datetime | None = None) -> str | None`; `GitRepo.first_version(path: str) -> str | None`; `ProfileStore.file_at(agent_id: str, filename: str, before: datetime | None) -> str | None`; `ProfileStore.first_version(agent_id: str, filename: str) -> str | None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_storage.py`:

```python
def test_connect_readonly_reads_but_cannot_write(tmp_path):
    import sqlite3

    from aiswarm.db import connect, connect_readonly

    path = tmp_path / "c.db"
    connect(path).execute("INSERT INTO runs (id, goal, status, agent_ids, created_at) VALUES ('r', 'g', 'closed', '[]', 't')")
    ro = connect_readonly(path)
    assert ro.execute("SELECT id FROM runs").fetchone()["id"] == "r"
    with pytest.raises(sqlite3.OperationalError):
        ro.execute("DELETE FROM runs")


def test_connect_readonly_reports_a_missing_database(tmp_path):
    import sqlite3

    from aiswarm.db import connect_readonly

    with pytest.raises(sqlite3.OperationalError):
        connect_readonly(tmp_path / "missing.db")
```

(If `tests/test_storage.py` does not import `pytest`, add `import pytest` at the top.)

Append to `tests/test_profiles.py`:

```python
def test_file_at_and_first_version(store):
    from datetime import UTC, datetime, timedelta

    before_edit = datetime.now(UTC) + timedelta(seconds=1)
    import time
    time.sleep(2)
    store.write_private("a", "self.md", "Later self.")
    assert store.file_at("a", "self.md", before_edit) == ""
    assert store.file_at("a", "self.md", None) == "Later self."
    assert store.first_version("a", "temperament.md") == "Curious and blunt."
    assert store.file_at("a", "nope.md", None) is None
    assert store.first_version("a", "nope.md") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_storage.py tests/test_profiles.py`
Expected: FAIL — `cannot import name 'connect_readonly'`, `ProfileStore` has no `file_at`.

- [ ] **Step 3: Implement**

In `src/aiswarm/db.py`, after `connect`:

```python
def connect_readonly(path: Path) -> sqlite3.Connection:
    """A connection that cannot write: for observers of the community, never for the runner."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("SELECT 1 FROM sqlite_master LIMIT 1")  # fail now, not later, if the file is missing
    return conn
```

In `src/aiswarm/gitrepo.py`, add to `GitRepo` (import `datetime` from `datetime` at the top if not already imported):

```python
    def show_at(self, path: str, before: datetime | None = None) -> str | None:
        """The file's content at the last commit touching it (at or before `before`); None if it did not exist."""
        if self.head() is None:
            return None
        args = ["log", "-1", "--format=%H"]
        if before is not None:
            args.append(f"--before={before.isoformat()}")
        commit = self._git(*args, "--", path)
        if not commit:
            return None
        try:
            return self._git("show", f"{commit}:{path}")
        except GitError:
            return None  # deleted in that commit

    def first_version(self, path: str) -> str | None:
        if self.head() is None:
            return None
        commits = self._git("log", "--reverse", "--format=%H", "--", path).split()
        if not commits:
            return None
        try:
            return self._git("show", f"{commits[0]}:{path}")
        except GitError:
            return None
```

In `src/aiswarm/profiles.py`, add to `ProfileStore` (after `history`), importing `datetime` from `datetime`:

```python
    def file_at(self, agent_id: str, filename: str, before: datetime | None) -> str | None:
        """An agent file as committed at or before `before` (latest if None). Observer use only."""
        return self._repo.show_at(f"agents/{agent_id}/{filename}", before)

    def first_version(self, agent_id: str, filename: str) -> str | None:
        return self._repo.first_version(f"agents/{agent_id}/{filename}")
```

Note: `_git` strips output, so file contents come back without the trailing newline.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q tests/test_storage.py tests/test_profiles.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aiswarm/db.py src/aiswarm/gitrepo.py src/aiswarm/profiles.py tests/test_storage.py tests/test_profiles.py
git commit -m "feat: read-only community connection and agent files at a point in time

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: observer.db store

**Files:**
- Create: `src/aiswarm/observer/__init__.py`, `src/aiswarm/observer/store.py`
- Test: `tests/test_observer_store.py`

**Interfaces:**
- Produces:
  - `Judgment(analysis: str, run_id: str, agent_id: str | None, status: str, result: dict | None, error: str | None, model_reported: str | None, prompt_sha256: str)` (frozen dataclass)
  - `ObserverStore(path: Path)` with `cached(key) -> Judgment | None` (only `ok`), `save(key, judgment, model_alias) -> None`, `judgments(run_id, analysis=None) -> list[Judgment]`, `failed() -> list[Judgment]`, `code_for(label) -> str | None`, `codes() -> list[tuple[str, str]]`, `assign(label, code, description) -> None`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_observer_store.py`:

```python
from aiswarm.observer.store import Judgment, ObserverStore


def judgment(status="ok", **kw):
    base = dict(analysis="themes", run_id="r1", agent_id="a", status=status, result={"themes": []} if status == "ok"
                else None, error=None if status == "ok" else "boom", model_reported="claude-x", prompt_sha256="p")
    base.update(kw)
    return Judgment(**base)


def test_cache_returns_only_ok_judgments(tmp_path):
    store = ObserverStore(tmp_path / "observer" / "observer.db")
    store.save("k1", judgment(), "sonnet")
    store.save("k2", judgment("failed"), "sonnet")
    assert store.cached("k1") == judgment()
    assert store.cached("k2") is None
    assert [j.status for j in store.failed()] == ["failed"]


def test_save_replaces_a_failed_judgment(tmp_path):
    store = ObserverStore(tmp_path / "o.db")
    store.save("k", judgment("failed"), "sonnet")
    store.save("k", judgment(), "sonnet")
    assert store.failed() == [] and store.cached("k") is not None


def test_judgments_by_run_and_analysis(tmp_path):
    store = ObserverStore(tmp_path / "o.db")
    store.save("k1", judgment(), "sonnet")
    store.save("k2", judgment(analysis="self_image"), "sonnet")
    store.save("k3", judgment(run_id="r2"), "sonnet")
    assert len(store.judgments("r1")) == 2
    assert [j.analysis for j in store.judgments("r1", "self_image")] == ["self_image"]


def test_codebook_assign_and_lookup(tmp_path):
    store = ObserverStore(tmp_path / "o.db")
    store.assign("pragmatism", "pragmatism", "Favours what works.")
    store.assign("practical focus", "pragmatism", "ignored: code exists")
    assert store.code_for("practical focus") == "pragmatism"
    assert store.code_for("unknown") is None
    assert store.codes() == [("pragmatism", "Favours what works.")]


def test_reopening_keeps_data(tmp_path):
    path = tmp_path / "o.db"
    ObserverStore(path).assign("x", "x", "d")
    assert ObserverStore(path).code_for("x") == "x"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest -q tests/test_observer_store.py`
Expected: FAIL — `No module named 'aiswarm.observer'`.

- [ ] **Step 3: Implement**

Create `src/aiswarm/observer/__init__.py`:

```python
"""The Observer: user-only analysis of the community. Nothing here is ever read by an agent."""
```

Create `src/aiswarm/observer/store.py`:

```python
"""observer.db: the Observer's own state (judgment cache, codebook). Never read by any agent."""

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aiswarm.db import now_iso

MIGRATIONS: list[str] = [
    """
    CREATE TABLE judgments (
        key TEXT PRIMARY KEY,
        analysis TEXT NOT NULL,
        run_id TEXT NOT NULL,
        agent_id TEXT,
        prompt_sha256 TEXT NOT NULL,
        model_alias TEXT NOT NULL,
        model_reported TEXT,
        status TEXT NOT NULL CHECK (status IN ('ok', 'failed')),
        result TEXT,
        error TEXT,
        created_at TEXT NOT NULL
    );
    CREATE INDEX judgments_by_run ON judgments (run_id, analysis);
    CREATE TABLE codes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        description TEXT NOT NULL
    );
    CREATE TABLE labels (
        label TEXT PRIMARY KEY,
        code_id INTEGER NOT NULL REFERENCES codes(id)
    );
    """,
]


@dataclass(frozen=True)
class Judgment:
    analysis: str
    run_id: str
    agent_id: str | None
    status: str  # "ok" | "failed"
    result: dict[str, Any] | None
    error: str | None
    model_reported: str | None
    prompt_sha256: str


def _judgment(row: sqlite3.Row) -> Judgment:
    return Judgment(row["analysis"], row["run_id"], row["agent_id"], row["status"],
                    json.loads(row["result"]) if row["result"] else None, row["error"], row["model_reported"],
                    row["prompt_sha256"])


class ObserverStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        current = self.conn.execute("PRAGMA user_version").fetchone()[0]
        for number, sql in enumerate(MIGRATIONS[current:], start=current + 1):
            self.conn.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {number};\nCOMMIT;")

    def cached(self, key: str) -> Judgment | None:
        row = self.conn.execute("SELECT * FROM judgments WHERE key = ? AND status = 'ok'", (key,)).fetchone()
        return _judgment(row) if row else None

    def save(self, key: str, judgment: Judgment, model_alias: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO judgments (key, analysis, run_id, agent_id, prompt_sha256, model_alias, "
            "model_reported, status, result, error, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (key, judgment.analysis, judgment.run_id, judgment.agent_id, judgment.prompt_sha256, model_alias,
             judgment.model_reported, judgment.status,
             None if judgment.result is None else json.dumps(judgment.result, ensure_ascii=False),
             judgment.error, now_iso()),
        )

    def judgments(self, run_id: str, analysis: str | None = None) -> list[Judgment]:
        sql, args = "SELECT * FROM judgments WHERE run_id = ?", [run_id]
        if analysis is not None:
            sql, args = sql + " AND analysis = ?", [*args, analysis]
        return [_judgment(r) for r in self.conn.execute(sql + " ORDER BY created_at, key", args)]

    def failed(self) -> list[Judgment]:
        return [_judgment(r) for r in self.conn.execute("SELECT * FROM judgments WHERE status = 'failed' ORDER BY run_id")]

    def code_for(self, label: str) -> str | None:
        row = self.conn.execute(
            "SELECT c.name FROM labels l JOIN codes c ON c.id = l.code_id WHERE l.label = ?", (label,)).fetchone()
        return row["name"] if row else None

    def codes(self) -> list[tuple[str, str]]:
        return [(r["name"], r["description"]) for r in self.conn.execute("SELECT name, description FROM codes ORDER BY name")]

    def assign(self, label: str, code: str, description: str) -> None:
        self.conn.execute("INSERT OR IGNORE INTO codes (name, description) VALUES (?, ?)", (code, description))
        code_id = self.conn.execute("SELECT id FROM codes WHERE name = ?", (code,)).fetchone()["id"]
        self.conn.execute("INSERT OR REPLACE INTO labels (label, code_id) VALUES (?, ?)", (label, code_id))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest -q tests/test_observer_store.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aiswarm/observer tests/test_observer_store.py
git commit -m "feat: observer.db with a judgment cache and a codebook

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Metrics — runs, action mix, influence, credits

**Files:**
- Create: `src/aiswarm/observer/metrics.py`, `tests/observer_helpers.py`
- Test: `tests/test_observer_metrics.py`

**Interfaces:**
- Produces:
  - `RunInfo(id, goal, goal_id: str | None, goal_category: str | None, is_benchmark: bool, ended_reason: str | None, agent_ids: tuple[str, ...], created_at: str, end: str)` with property `label` (goal_id or run id) and `category` (goal_category or `"ad hoc"`)
  - `closed_runs(conn) -> list[RunInfo]` (rowid order); `open_runs(conn) -> list[str]`
  - `ACTIONS: tuple[str, ...]`; `action_mix(conn, run: RunInfo) -> dict[str, Counter]`
  - `Influence(adopted=0, refined=0, critiqued=0, cited=0, files=0)` with property `score`; `influence(conn, run) -> dict[str, Influence]`
  - `Credits(caller: str, credited: dict[str, str])`; `credits(conn, run_id) -> Credits | None`; `credit_pairs(conn, runs) -> list[tuple[str, str, str]]` (run_id, giver, receiver)
- Test helpers (`tests/observer_helpers.py`): `add_run`, `add_entry`, `add_review`, `add_journal`.

- [ ] **Step 1: Write the helpers and failing tests**

Create `tests/observer_helpers.py`:

```python
"""Insert community records directly, for Observer tests."""

import json

T0 = "2026-10-01T00:00:00+00:00"


def add_run(conn, run_id, agents=("a", "b", "c"), *, created=T0, status="closed", ended="budget",
            goal="Pick a fruit.", goal_id=None, category=None, benchmark=False):
    conn.execute(
        "INSERT INTO runs (id, goal, status, ended_reason, agent_ids, created_at, goal_id, goal_category, is_benchmark) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (run_id, goal, status, ended if status == "closed" else None, json.dumps(list(agents)), created, goal_id,
         category, int(benchmark)),
    )
    return run_id


def add_entry(conn, run_id, author, type_, payload, refs=(), round=1, created=T0):
    cursor = conn.execute(
        "INSERT INTO entries (run_id, round, author, type, refs, payload, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (run_id, round, author, type_, json.dumps(list(refs)), json.dumps(payload), created),
    )
    return cursor.lastrowid


def add_review(conn, run_id, reviewer, reviewee, overall, *, more_of="more", less_of="less", note="note",
               reason="reason", created=T0):
    cursor = conn.execute(
        "INSERT INTO reviews (run_id, reviewer, reviewee, is_self, more_of, less_of, private_note, evidence, overall, "
        "overall_reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, '[]', ?, ?, ?)",
        (run_id, reviewer, reviewee, int(reviewer == reviewee), more_of, less_of, note, overall, reason, created),
    )
    return cursor.lastrowid


def add_journal(conn, run_id, agent_id, section, text, created=T0):
    conn.execute("INSERT INTO journals (run_id, agent_id, section, text, created_at) VALUES (?, ?, ?, ?, ?)",
                 (run_id, agent_id, section, text, created))
```

Create `tests/test_observer_metrics.py`:

```python
from observer_helpers import add_entry, add_run

from aiswarm.observer.metrics import (
    Influence, action_mix, closed_runs, credit_pairs, credits, influence, open_runs,
)


def test_closed_runs_in_order_with_labels(community):
    store, conn = community
    add_run(conn, "r1", goal_id="best-fruit", category="contested", created="2026-10-01T00:00:00+00:00")
    add_run(conn, "r2", created="2026-10-02T00:00:00+00:00")
    add_run(conn, "r3", status="running", created="2026-10-03T00:00:00+00:00")
    add_entry(conn, "r1", "a", "pass", {}, created="2026-10-01T00:05:00+00:00")
    runs = closed_runs(conn)
    assert [r.id for r in runs] == ["r1", "r2"]
    assert runs[0].label == "best-fruit" and runs[0].category == "contested"
    assert runs[1].label == "r2" and runs[1].category == "ad hoc"
    assert runs[0].end == "2026-10-01T00:05:00+00:00"
    assert open_runs(conn) == ["r3"]


def test_action_mix_counts_failures_separately(community):
    store, conn = community
    add_run(conn, "r1")
    add_entry(conn, "r1", "a", "propose", {"answer_summary": "x", "method": "m"})
    add_entry(conn, "r1", "a", "pass", {})
    add_entry(conn, "r1", "b", "pass", {"failure": "invalid_action"})
    add_entry(conn, "r1", "system", "goal", {"goal": "g"})
    mix = action_mix(conn, closed_runs(conn)[0])
    assert mix["a"]["propose"] == 1 and mix["a"]["pass"] == 1
    assert mix["b"]["failed"] == 1 and mix["b"]["pass"] == 0
    assert set(mix) == {"a", "b", "c"}


def test_influence_counts_what_others_do_with_your_entries(community):
    store, conn = community
    add_run(conn, "r1")
    p = add_entry(conn, "r1", "a", "propose", {"answer_summary": "x", "method": "m"})
    add_entry(conn, "r1", "b", "adopt", {"target": p, "reason": "r"}, refs=[p])
    add_entry(conn, "r1", "c", "refine", {"target": p, "change": "c"}, refs=[p])
    add_entry(conn, "r1", "c", "critique", {"target": p, "about": "answer", "points": "p"}, refs=[p])
    add_entry(conn, "r1", "a", "adopt", {"target": p, "reason": "self"}, refs=[p])
    add_entry(conn, "r1", "b", "call_consensus", {"synthesis": "s", "credits": []}, refs=[p])
    conn.execute("INSERT INTO files (run_id, path, version, author, git_commit, created_at) "
                 "VALUES ('r1', 'f.md', 1, 'c', 'abc', 't')")
    inf = influence(conn, closed_runs(conn)[0])
    assert inf["a"] == Influence(adopted=1, refined=1, critiqued=1, cited=1)
    assert inf["a"].score == 3
    assert inf["c"].files == 1


def test_credits_and_pairs(community):
    store, conn = community
    add_run(conn, "r1", ended="consensus")
    call = add_entry(conn, "r1", "a", "call_consensus",
                     {"synthesis": "s", "credits": [{"agent_id": "a", "contribution": "me"},
                                                    {"agent_id": "b", "contribution": "idea"}]})
    add_entry(conn, "r1", "system", "run_closed", {"reason": "consensus", "call_id": call})
    add_run(conn, "r2")
    add_entry(conn, "r2", "system", "run_closed", {"reason": "budget", "call_id": None})
    got = credits(conn, "r1")
    assert got.caller == "a" and got.credited == {"a": "me", "b": "idea"}
    assert credits(conn, "r2") is None
    assert credit_pairs(conn, closed_runs(conn)) == [("r1", "a", "a"), ("r1", "a", "b")]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_observer_metrics.py`
Expected: FAIL — `No module named 'aiswarm.observer.metrics'`.

- [ ] **Step 3: Implement**

Create `src/aiswarm/observer/metrics.py`:

```python
"""Deterministic metrics over the community store. No LLM calls, no writes."""

import json
import sqlite3
from collections import Counter
from dataclasses import dataclass, field

ACTIONS = ("propose", "critique", "adopt", "refine", "vote", "call_consensus", "write_file", "pass", "failed")


@dataclass(frozen=True)
class RunInfo:
    id: str
    goal: str
    goal_id: str | None
    goal_category: str | None
    is_benchmark: bool
    ended_reason: str | None
    agent_ids: tuple[str, ...]
    created_at: str
    end: str

    @property
    def label(self) -> str:
        return self.goal_id or self.id

    @property
    def category(self) -> str:
        return self.goal_category or "ad hoc"


def run_end(conn: sqlite3.Connection, run_id: str) -> str:
    """The last recorded moment of a run, including its post-run cycle."""
    return conn.execute(
        "SELECT MAX(t) FROM (SELECT updated_at AS t FROM postrun_steps WHERE run_id = ?1 "
        "UNION ALL SELECT created_at FROM entries WHERE run_id = ?1 "
        "UNION ALL SELECT created_at FROM runs WHERE id = ?1)", (run_id,),
    ).fetchone()[0]


def closed_runs(conn: sqlite3.Connection) -> list[RunInfo]:
    rows = conn.execute("SELECT * FROM runs WHERE status = 'closed' ORDER BY rowid").fetchall()
    return [
        RunInfo(r["id"], r["goal"], r["goal_id"], r["goal_category"], bool(r["is_benchmark"]), r["ended_reason"],
                tuple(json.loads(r["agent_ids"])), r["created_at"], run_end(conn, r["id"]))
        for r in rows
    ]


def open_runs(conn: sqlite3.Connection) -> list[str]:
    return [r["id"] for r in conn.execute("SELECT id FROM runs WHERE status != 'closed' ORDER BY rowid")]


def _entries(conn: sqlite3.Connection, run_id: str) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM entries WHERE run_id = ? ORDER BY id", (run_id,)).fetchall()


def action_mix(conn: sqlite3.Connection, run: RunInfo) -> dict[str, Counter]:
    mix: dict[str, Counter] = {agent: Counter({a: 0 for a in ACTIONS}) for agent in run.agent_ids}
    for row in _entries(conn, run.id):
        if row["author"] not in mix:
            continue
        kind = row["type"]
        if kind == "pass" and "failure" in json.loads(row["payload"]):
            kind = "failed"
        if kind in ACTIONS:
            mix[row["author"]][kind] += 1
    return mix


@dataclass(frozen=True)
class Influence:
    adopted: int = 0   # others adopted your entries
    refined: int = 0   # others refined your entries
    critiqued: int = 0  # others critiqued your entries
    cited: int = 0     # your entries cited in others' calls for consensus
    files: int = 0     # workspace file versions you wrote

    @property
    def score(self) -> int:
        return self.adopted + self.refined + self.cited + self.files


def influence(conn: sqlite3.Connection, run: RunInfo) -> dict[str, Influence]:
    rows = _entries(conn, run.id)
    author_of = {r["id"]: r["author"] for r in rows}
    counts: dict[str, Counter] = {agent: Counter() for agent in run.agent_ids}
    field_for = {"adopt": "adopted", "refine": "refined", "critique": "critiqued"}
    for row in rows:
        refs = json.loads(row["refs"])
        if row["type"] in field_for:
            target = json.loads(row["payload"]).get("target")
            owner = author_of.get(target)
            if owner in counts and owner != row["author"]:
                counts[owner][field_for[row["type"]]] += 1
        elif row["type"] == "call_consensus":
            for ref in refs:
                owner = author_of.get(ref)
                if owner in counts and owner != row["author"]:
                    counts[owner]["cited"] += 1
    for row in conn.execute("SELECT author, COUNT(*) AS n FROM files WHERE run_id = ? GROUP BY author", (run.id,)):
        if row["author"] in counts:
            counts[row["author"]]["files"] += row["n"]
    return {agent: Influence(**c) for agent, c in counts.items()}


@dataclass(frozen=True)
class Credits:
    caller: str
    credited: dict[str, str] = field(default_factory=dict)  # agent id -> contribution


def credits(conn: sqlite3.Connection, run_id: str) -> Credits | None:
    """Credits from the call that closed the run; None when the run did not close on a call."""
    row = conn.execute("SELECT payload FROM entries WHERE run_id = ? AND type = 'run_closed'", (run_id,)).fetchone()
    call_id = json.loads(row["payload"]).get("call_id") if row else None
    if not call_id:
        return None
    call = conn.execute("SELECT author, payload FROM entries WHERE id = ?", (call_id,)).fetchone()
    given = json.loads(call["payload"]).get("credits", [])
    return Credits(call["author"], {c["agent_id"]: c.get("contribution", "") for c in given if "agent_id" in c})


def credit_pairs(conn: sqlite3.Connection, runs: list[RunInfo]) -> list[tuple[str, str, str]]:
    """(run id, giver, receiver) for every credit in every run that closed on a call, in run order."""
    pairs = []
    for run in runs:
        got = credits(conn, run.id)
        if got:
            pairs += [(run.id, got.caller, receiver) for receiver in sorted(got.credited)]
    return pairs
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q tests/test_observer_metrics.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aiswarm/observer/metrics.py tests/observer_helpers.py tests/test_observer_metrics.py
git commit -m "feat: observer metrics for runs, action mix, influence and credits

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Metrics — reputation, identity, twins, categories, benchmark, norms, harness changes

**Files:**
- Modify: `src/aiswarm/observer/metrics.py`
- Test: `tests/test_observer_metrics.py`

**Interfaces:**
- Consumes: `RunInfo`, `closed_runs`, `action_mix` (Task 4); `ProfileStore.history`, `ProfileStore.first_version` (Task 2); `provenance_changes` (existing); `aiswarm.timeline._rename` (existing).
- Produces:
  - `peer_scores(conn, run_id) -> dict[str, float]`; `self_scores(conn, run_id) -> dict[str, int]`; `self_peer_gap(conn, run_id) -> dict[str, float]`; `dimension_scores(conn, run_id) -> dict[str, dict[str, float]]`
  - `IdentityEvent(at: datetime, agent_id: str, kind: str, detail: str, prior_peer: float | None)`; `identity_events(conn, store, runs) -> list[IdentityEvent]`
  - `twins(store) -> dict[str, list[str]]`
  - `CategoryStats(runs: int, endings: Counter, actions: Counter)`; `by_category(conn, runs) -> dict[str, CategoryStats]`
  - `BenchmarkAnswer(run_id: str, consensus: bool, text: str)`; `benchmark_answers(conn, runs, who) -> list[BenchmarkAnswer]`
  - `NormEvent(run_id, author, kind, text, status)`; `norm_events(conn) -> list[NormEvent]`
  - `harness_changes(conn, runs) -> list[int]` (indices into `runs`)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_observer_metrics.py`:

```python
from observer_helpers import add_review  # noqa: E402

from aiswarm.observer.metrics import (  # noqa: E402
    BenchmarkAnswer, by_category, benchmark_answers, dimension_scores, harness_changes, identity_events,
    norm_events, peer_scores, self_peer_gap, twins,
)
from aiswarm.view import make_who  # noqa: E402


def test_peer_scores_and_self_gap(community):
    store, conn = community
    add_run(conn, "r1")
    add_review(conn, "r1", "b", "a", 4)
    add_review(conn, "r1", "c", "a", 2)
    add_review(conn, "r1", "a", "a", 5)
    assert peer_scores(conn, "r1") == {"a": 3.0}
    assert self_peer_gap(conn, "r1") == {"a": 2.0}


def test_dimension_scores_use_peer_ratings_only(community):
    store, conn = community
    add_run(conn, "r1")
    conn.execute("INSERT INTO dimensions (name, question, adopted_run) VALUES ('clarity', 'Clear?', 'r1')")
    peer = add_review(conn, "r1", "b", "a", 4)
    own = add_review(conn, "r1", "a", "a", 5)
    conn.execute("INSERT INTO review_ratings (review_id, dimension_id, rating, reason) VALUES (?, 1, 2, 'x')", (peer,))
    conn.execute("INSERT INTO review_ratings (review_id, dimension_id, rating, reason) VALUES (?, 1, 5, 'x')", (own,))
    assert dimension_scores(conn, "r1") == {"a": {"clarity": 2.0}}


def test_identity_events_with_prior_peer_score(community):
    store, conn = community
    add_run(conn, "r1", created="2000-01-01T00:00:00+00:00")
    add_review(conn, "r1", "b", "a", 1)
    store.set_public("a", "Ash", "New headline", "I am Ash.")
    store.write_private("a", "self.md", "I changed.")
    store.write_private("a", "lessons.md", "Not an identity event.")
    events = [e for e in identity_events(conn, store, closed_runs(conn)) if e.agent_id == "a"]
    kinds = [e.kind for e in events]
    assert "rename" in kinds and "self.md" in kinds and "lessons.md" not in kinds
    rename = next(e for e in events if e.kind == "rename")
    assert rename.detail == "Alpha → Ash" and rename.prior_peer == 1.0


def test_twins_match_the_controls_seed_temperament(tmp_path):
    from aiswarm.profiles import ProfileStore

    store = ProfileStore(tmp_path / "community")
    store.create("d", "fake", "Wants evidence.")
    store.create("x", "fake", "Something else.")
    store.create("e", "fake", "Wants evidence.\n", control=True)
    store.write_private("d", "temperament.md", "Rewritten later.")
    assert twins(store) == {"e": ["d"]}


def test_by_category_counts_endings_and_actions(community):
    store, conn = community
    add_run(conn, "r1", category="contested", ended="budget")
    add_run(conn, "r2", category="contested", ended="consensus")
    add_run(conn, "r3")
    add_entry(conn, "r1", "a", "vote", {"target": 1, "stance": "support", "reason": "r"})
    stats = by_category(conn, closed_runs(conn))
    assert stats["contested"].runs == 2 and stats["contested"].endings == {"budget": 1, "consensus": 1}
    assert stats["contested"].actions["vote"] == 1
    assert stats["ad hoc"].runs == 1


def test_benchmark_answers_use_synthesis_or_latest_proposals(community):
    store, conn = community
    add_run(conn, "r1", benchmark=True)
    add_entry(conn, "r1", "a", "propose", {"answer_summary": "Old", "method": "m"})
    add_entry(conn, "r1", "a", "propose", {"answer_summary": "Pepperoni", "method": "m"})
    add_entry(conn, "r1", "b", "propose", {"answer_summary": "Mushroom", "method": "m"})
    add_entry(conn, "r1", "system", "run_closed", {"reason": "budget", "call_id": None})
    add_run(conn, "r2", benchmark=True, ended="consensus")
    call = add_entry(conn, "r2", "a", "call_consensus", {"synthesis": "Margherita for all.", "credits": []})
    add_entry(conn, "r2", "system", "run_closed", {"reason": "consensus", "call_id": call})
    add_run(conn, "r3")
    who = make_who({i: store.public(i) for i in store.ids()})
    answers = benchmark_answers(conn, closed_runs(conn), who)
    assert answers == [
        BenchmarkAnswer("r1", False, "- Alpha (a): Pepperoni\n- Bea (b): Mushroom"),
        BenchmarkAnswer("r2", True, "Margherita for all."),
    ]


def test_norm_events(community):
    store, conn = community
    add_run(conn, "r1")
    conn.execute("INSERT INTO norm_proposals (run_id, author, kind, text, status, created_at) "
                 "VALUES ('r1', 'a', 'norm', 'Be kind.', 'adopted', 't')")
    conn.execute("INSERT INTO norm_proposals (run_id, author, kind, name, question, status, created_at) "
                 "VALUES ('r1', 'b', 'dimension', 'clarity', 'Clear?', 'open', 't')")
    events = norm_events(conn)
    assert [(e.author, e.kind, e.text, e.status) for e in events] == [
        ("a", "norm", "Be kind.", "adopted"), ("b", "dimension", "clarity: Clear?", "open")]


def test_harness_changes_marks_runs_whose_provenance_changed(community, monkeypatch):
    import aiswarm.observer.metrics as metrics

    store, conn = community
    add_run(conn, "r1")
    add_run(conn, "r2")
    monkeypatch.setattr(metrics, "provenance_changes", lambda c, run_id: ["code"] if run_id == "r2" else [])
    assert harness_changes(conn, closed_runs(conn)) == [1]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_observer_metrics.py`
Expected: FAIL — `cannot import name 'BenchmarkAnswer'`.

- [ ] **Step 3: Implement**

Append to `src/aiswarm/observer/metrics.py` (add imports at the top: `from datetime import datetime`, `from aiswarm.profiles import SEED_DIR, ProfileStore`, `from aiswarm.provenance import provenance_changes`, `from aiswarm.timeline import _rename`, `from aiswarm.view import Who`):

```python
def peer_scores(conn: sqlite3.Connection, run_id: str) -> dict[str, float]:
    rows = conn.execute("SELECT reviewee, AVG(overall) AS score FROM reviews WHERE run_id = ? AND is_self = 0 "
                        "GROUP BY reviewee", (run_id,))
    return {r["reviewee"]: float(r["score"]) for r in rows}


def self_scores(conn: sqlite3.Connection, run_id: str) -> dict[str, int]:
    rows = conn.execute("SELECT reviewee, overall FROM reviews WHERE run_id = ? AND is_self = 1", (run_id,))
    return {r["reviewee"]: r["overall"] for r in rows}


def self_peer_gap(conn: sqlite3.Connection, run_id: str) -> dict[str, float]:
    """Self-rating minus mean peer rating; positive means the agent rates itself higher than peers do."""
    peers, own = peer_scores(conn, run_id), self_scores(conn, run_id)
    return {agent: own[agent] - peers[agent] for agent in own if agent in peers}


def dimension_scores(conn: sqlite3.Connection, run_id: str) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    rows = conn.execute(
        "SELECT v.reviewee, d.name, AVG(r.rating) AS score FROM review_ratings r JOIN reviews v ON v.id = r.review_id "
        "JOIN dimensions d ON d.id = r.dimension_id WHERE v.run_id = ? AND v.is_self = 0 GROUP BY v.reviewee, d.name",
        (run_id,))
    for r in rows:
        out.setdefault(r["reviewee"], {})[r["name"]] = float(r["score"])
    return out


@dataclass(frozen=True)
class IdentityEvent:
    at: datetime
    agent_id: str
    kind: str  # rename | public profile | temperament.md | self.md | control reset
    detail: str
    prior_peer: float | None  # mean peer score in the latest run started at or before the event


_IDENTITY_FILES = ("temperament.md", "self.md")


def _kind(agent_id: str, subject: str, patch: str) -> tuple[str, str] | None:
    if subject.startswith("reset control agent"):
        return "control reset", ""
    if renamed := _rename(patch):
        return "rename", f"{renamed[0]} → {renamed[1]}"
    if subject == f"{agent_id} updated public profile":
        return "public profile", ""
    for filename in _IDENTITY_FILES:
        if subject == f"{agent_id} updated {filename}":
            return filename, ""
    return None


def identity_events(conn: sqlite3.Connection, store: ProfileStore, runs: list[RunInfo]) -> list[IdentityEvent]:
    starts = [(datetime.fromisoformat(r.created_at), r.id) for r in runs]
    events = []
    for agent_id in store.ids():
        for commit in store.history(agent_id):
            kind = _kind(agent_id, commit.subject, commit.patch)
            if kind is None:
                continue
            prior = [run_id for started, run_id in starts if started <= commit.at]
            score = peer_scores(conn, prior[-1]).get(agent_id) if prior else None
            events.append(IdentityEvent(commit.at, agent_id, kind[0], kind[1], score))
    return sorted(events, key=lambda e: e.at)


def _norm(text: str | None) -> str:
    return " ".join((text or "").split())


def twins(store: ProfileStore) -> dict[str, list[str]]:
    """Each control agent's twins: non-control agents whose first temperament equals the control's seed."""
    out: dict[str, list[str]] = {}
    for agent_id in store.ids():
        if not store.record(agent_id).control:
            continue
        seed = store.root / "agents" / agent_id / SEED_DIR / "temperament.md"
        target = _norm(seed.read_text()) if seed.exists() else None
        out[agent_id] = sorted(
            other for other in store.ids()
            if other != agent_id and not store.record(other).control
            and target and _norm(store.first_version(other, "temperament.md")) == target
        )
    return out


@dataclass
class CategoryStats:
    runs: int = 0
    endings: Counter = field(default_factory=Counter)
    actions: Counter = field(default_factory=Counter)


def by_category(conn: sqlite3.Connection, runs: list[RunInfo]) -> dict[str, CategoryStats]:
    stats: dict[str, CategoryStats] = {}
    for run in runs:
        s = stats.setdefault(run.category, CategoryStats())
        s.runs += 1
        s.endings[run.ended_reason or "unknown"] += 1
        for counts in action_mix(conn, run).values():
            s.actions.update({k: v for k, v in counts.items() if v})
    return stats


@dataclass(frozen=True)
class BenchmarkAnswer:
    run_id: str
    consensus: bool
    text: str


def benchmark_answers(conn: sqlite3.Connection, runs: list[RunInfo], who: Who) -> list[BenchmarkAnswer]:
    out = []
    for run in runs:
        if not run.is_benchmark:
            continue
        closed = conn.execute("SELECT payload FROM entries WHERE run_id = ? AND type = 'run_closed'", (run.id,)).fetchone()
        call_id = json.loads(closed["payload"]).get("call_id") if closed else None
        if call_id:
            call = conn.execute("SELECT payload FROM entries WHERE id = ?", (call_id,)).fetchone()
            out.append(BenchmarkAnswer(run.id, True, json.loads(call["payload"])["synthesis"]))
            continue
        latest: dict[str, str] = {}
        for row in _entries(conn, run.id):
            if row["type"] == "propose":
                latest[row["author"]] = json.loads(row["payload"])["answer_summary"]
        text = "\n".join(f"- {who(a)}: {t}" for a, t in latest.items()) or "(no proposals)"
        out.append(BenchmarkAnswer(run.id, False, text))
    return out


@dataclass(frozen=True)
class NormEvent:
    run_id: str
    author: str
    kind: str
    text: str
    status: str


def norm_events(conn: sqlite3.Connection) -> list[NormEvent]:
    rows = conn.execute("SELECT * FROM norm_proposals ORDER BY id")
    return [NormEvent(r["run_id"], r["author"], r["kind"], r["text"] or f"{r['name']}: {r['question']}", r["status"])
            for r in rows]


def harness_changes(conn: sqlite3.Connection, runs: list[RunInfo]) -> list[int]:
    """Indices of runs whose provenance shows a harness change (since the previous run, or within the run)."""
    return [i for i, run in enumerate(runs) if provenance_changes(conn, run.id)]
```

Note: the existing `ProfileStore.create(..., control=True)` writes `seed/temperament.md` from the stripped temperament; `_norm` makes the comparison whitespace-insensitive.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q tests/test_observer_metrics.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aiswarm/observer/metrics.py tests/test_observer_metrics.py
git commit -m "feat: observer metrics for reputation, identity events, twins, categories, benchmark answers, norms

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: The judge — schemas, evidence rule, cache, retry

**Files:**
- Create: `src/aiswarm/observer/judge.py`, `src/aiswarm/prompts/observer/{themes,private_public,self_image,benchmark,codebook}.md`
- Test: `tests/test_observer_judge.py`

**Interfaces:**
- Consumes: `ObserverStore`, `Judgment` (Task 3); `correction_request`, `extract_json`, `simplify_schema`, `summarise_validation`, `ActionError` (existing `aiswarm.actions`); `render` (existing); `ModelClient`, `ModelError` (existing).
- Produces:
  - Reply models `ThemesReply`, `PrivatePublicReply`, `SelfImageReply`, `BenchmarkReply`, `CodebookReply`
  - `Analysis(name: str, reply: type[BaseModel], check: Callable[[BaseModel, dict[str, str]], str | None])` with property `prompt_name`
  - `ANALYSES: dict[str, Analysis]` keyed `themes`, `private_public`, `self_image`, `benchmark`, `codebook`
  - `normalise(text) -> str`
  - `Judge(client: ModelClient, store: ObserverStore, model_alias: str)` with `async run(analysis, *, run_id, agent_id, inputs: dict[str, str]) -> Judgment`

- [ ] **Step 1: Write the judge prompts**

Create `src/aiswarm/prompts/observer/themes.md`:

```markdown
You are a careful qualitative researcher studying a community of AI agents. You are reading one agent's private and public writing from one session. Nobody in the community will ever see your analysis.

Identify the 3 to 8 most prominent values or working styles this agent expresses, rejects, or is ambivalent about. Describe each in your own words as a short label (at most 5 words). Do not use a predefined list of values; name what you actually see.

For each theme give:
- label: your short label
- polarity: "expressed", "rejected" or "ambivalent"
- evidence: an exact quote copied character for character from the input
- source: the heading of the input section the quote comes from

Reply with JSON only: {"themes": [{"label": "...", "polarity": "...", "evidence": "...", "source": "..."}]}
```

Create `src/aiswarm/prompts/observer/private_public.md`:

```markdown
You are a careful qualitative researcher studying a community of AI agents. You are reading one agent's private journal from one session and the reviews it wrote about its colleagues in the same session. Nobody in the community will ever see your analysis.

For each colleague the agent reviewed, compare what the journal says or implies about that colleague with what the review says. Choose a verdict:
- "consistent": the review matches the journal
- "softened": the review is kinder than the journal
- "harsher": the review is harsher than the journal
- "contradictory": the review contradicts the journal

If the journal says nothing about a colleague, use "consistent" and quote the review for both quotes only if there is truly no journal material; otherwise quote the most relevant journal passage.

Every quote must be copied character for character from the input.

Reply with JSON only: {"findings": [{"colleague": "Name (id)", "verdict": "...", "journal_quote": "...", "review_quote": "..."}]}
```

Create `src/aiswarm/prompts/observer/self_image.md`:

```markdown
You are a careful qualitative researcher studying a community of AI agents. You are comparing one agent's private self-description and public profile now with the same documents at its previous session. Nobody in the community will ever see your analysis.

List what changed in how the agent sees and presents itself. Then judge the gap between the private self-description now and the public profile now: "none", "some" or "large".

Every evidence quote must be copied character for character from the input. If nothing changed, return an empty list of changes.

Reply with JSON only: {"changes": [{"what": "...", "evidence": "..."}], "private_public_gap": "...", "gap_evidence": "..."}
```

Create `src/aiswarm/prompts/observer/benchmark.md`:

```markdown
You are a careful qualitative researcher studying a community of AI agents that answers the same benchmark question every few sessions. Nobody in the community will ever see your analysis.

Compare this session's answer with the earlier answers. Choose a trend: "first" (there are no earlier answers), "converging" (answers are becoming more alike), "diverging" (less alike), or "new direction" (a clear break from earlier answers). Then say in your own words what the community seems to optimise for in this answer.

Every evidence quote must be copied character for character from the input.

Reply with JSON only: {"trend": "...", "optimises_for": "...", "evidence": ["..."]}
```

Create `src/aiswarm/prompts/observer/codebook.md`:

```markdown
You maintain a codebook for a qualitative study of AI agents. Each new label was written by an analyst describing a value or working style. Map every new label to exactly one code.

Reuse an existing code when the label means the same thing. Otherwise create a new code: a short name (at most 4 words, lowercase) and a one-line description. Do not merge labels that differ in meaning just to keep the codebook small.

Reply with JSON only: {"assignments": [{"label": "...", "code": "...", "description": "..."}]}
Include every new label exactly once, spelled exactly as given. For an existing code, repeat its description.
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_observer_judge.py`:

```python
import json

from aiswarm.models.base import ModelError
from aiswarm.models.fake import FakeModel
from aiswarm.observer.judge import ANALYSES, Judge, normalise
from aiswarm.observer.store import ObserverStore

INPUTS = {"journal": "I value   honest\nfeedback above all.", "reviews written": "Bea was careless."}
GOOD = json.dumps({"themes": [{"label": "honesty", "polarity": "expressed",
                               "evidence": "I value honest feedback", "source": "journal"}]})
INVENTED = json.dumps({"themes": [{"label": "kindness", "polarity": "expressed",
                                   "evidence": "I love everyone", "source": "journal"}]})


def make_judge(tmp_path, script, **kw):
    client = FakeModel(script, model="claude-sonnet-x", **kw)
    return Judge(client, ObserverStore(tmp_path / "o.db"), "sonnet"), client


async def test_ok_judgment_is_cached(tmp_path):
    judge, client = make_judge(tmp_path, [GOOD])
    first = await judge.run(ANALYSES["themes"], run_id="r1", agent_id="a", inputs=INPUTS)
    second = await judge.run(ANALYSES["themes"], run_id="r1", agent_id="a", inputs=INPUTS)
    assert first.status == "ok" and first.result["themes"][0]["label"] == "honesty"
    assert first.model_reported == "claude-sonnet-x"
    assert second == first and len(client.calls) == 1


async def test_changed_input_is_a_cache_miss(tmp_path):
    judge, client = make_judge(tmp_path, [GOOD, GOOD])
    await judge.run(ANALYSES["themes"], run_id="r1", agent_id="a", inputs=INPUTS)
    await judge.run(ANALYSES["themes"], run_id="r1", agent_id="a", inputs={**INPUTS, "self.md": "new"})
    assert len(client.calls) == 2


async def test_invented_quote_gets_one_correction(tmp_path):
    judge, client = make_judge(tmp_path, [INVENTED, GOOD])
    got = await judge.run(ANALYSES["themes"], run_id="r1", agent_id="a", inputs=INPUTS)
    assert got.status == "ok"
    assert "I love everyone" in client.calls[1][1][-1]["content"]


async def test_second_failure_is_stored_failed_and_retried_next_time(tmp_path):
    judge, client = make_judge(tmp_path, [INVENTED, "not json", GOOD])
    failed = await judge.run(ANALYSES["themes"], run_id="r1", agent_id="a", inputs=INPUTS)
    assert failed.status == "failed" and failed.error
    retried = await judge.run(ANALYSES["themes"], run_id="r1", agent_id="a", inputs=INPUTS)
    assert retried.status == "ok"


async def test_changed_characters_in_a_quote_fail(tmp_path):
    curly = json.dumps({"themes": [{"label": "x", "polarity": "expressed",
                                    "evidence": "I value “honest” feedback", "source": "journal"}]})
    judge, _ = make_judge(tmp_path, [curly, curly])
    got = await judge.run(ANALYSES["themes"], run_id="r1", agent_id="a", inputs=INPUTS)
    assert got.status == "failed"


async def test_model_error_is_a_failed_judgment(tmp_path):
    judge, _ = make_judge(tmp_path, [GOOD], fail_times=5)
    got = await judge.run(ANALYSES["themes"], run_id="r1", agent_id="a", inputs=INPUTS)
    assert got.status == "failed"


async def test_codebook_reply_must_cover_every_label(tmp_path):
    partial = json.dumps({"assignments": [{"label": "honesty", "code": "honesty", "description": "d"}]})
    full = json.dumps({"assignments": [{"label": "honesty", "code": "honesty", "description": "d"},
                                       {"label": "care", "code": "care", "description": "d"}]})
    judge, client = make_judge(tmp_path, [partial, full])
    got = await judge.run(ANALYSES["codebook"], run_id="r1", agent_id=None,
                          inputs={"codebook": "(empty)", "new labels": "honesty\ncare"})
    assert got.status == "ok" and len(client.calls) == 2


def test_normalise_collapses_whitespace_only():
    assert normalise("a \n  b\tc") == "a b c"
    assert normalise("“a”") == "“a”"
```

Check before relying on it: `FakeModel(script, fail_times=n)` raises `ModelError` on the first n calls (read `src/aiswarm/models/fake.py`); if its semantics differ, adapt `test_model_error_is_a_failed_judgment` to construct a client whose `chat` raises `ModelError`, and keep the assertion.

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_observer_judge.py`
Expected: FAIL — `No module named 'aiswarm.observer.judge'`.

- [ ] **Step 4: Implement**

Create `src/aiswarm/observer/judge.py`:

```python
"""LLM judgments for the Observer: structured replies, an evidence rule, one correction, and a cache."""

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from aiswarm.actions import ActionError, correction_request, extract_json, simplify_schema, summarise_validation
from aiswarm.models.base import Message, ModelClient, ModelError
from aiswarm.observer.store import Judgment, ObserverStore
from aiswarm.prompts import render


class Theme(BaseModel):
    label: str = Field(min_length=1, max_length=60)
    polarity: Literal["expressed", "rejected", "ambivalent"]
    evidence: str = Field(min_length=1)
    source: str


class ThemesReply(BaseModel):
    themes: list[Theme] = Field(min_length=1, max_length=8)  # the prompt asks for 3-8; sparse input may give fewer


class ColleagueFinding(BaseModel):
    colleague: str
    verdict: Literal["consistent", "softened", "harsher", "contradictory"]
    journal_quote: str = Field(min_length=1)
    review_quote: str = Field(min_length=1)


class PrivatePublicReply(BaseModel):
    findings: list[ColleagueFinding]


class Change(BaseModel):
    what: str
    evidence: str = Field(min_length=1)


class SelfImageReply(BaseModel):
    changes: list[Change]
    private_public_gap: Literal["none", "some", "large"]
    gap_evidence: str = ""


class BenchmarkReply(BaseModel):
    trend: Literal["first", "converging", "diverging", "new direction"]
    optimises_for: str
    evidence: list[str]


class CodeAssignment(BaseModel):
    label: str
    code: str = Field(min_length=1, max_length=60)
    description: str


class CodebookReply(BaseModel):
    assignments: list[CodeAssignment]


def normalise(text: str) -> str:
    return " ".join(text.split())


def _evidence(quotes: Callable[[BaseModel], list[str]]) -> Callable[[BaseModel, dict[str, str]], str | None]:
    def check(reply: BaseModel, inputs: dict[str, str]) -> str | None:
        corpus = normalise("\n".join(inputs.values()))
        missing = [q for q in quotes(reply) if q and normalise(q) not in corpus]
        if missing:
            return "these quotes do not appear verbatim in the input: " + "; ".join(repr(q) for q in missing[:5])
        return None
    return check


def _covers_labels(reply: BaseModel, inputs: dict[str, str]) -> str | None:
    wanted = [line for line in inputs["new labels"].splitlines() if line.strip()]
    given = [a.label for a in reply.assignments]  # type: ignore[attr-defined]
    if sorted(given) != sorted(wanted):
        return f"assign every new label exactly once, spelled as given: {', '.join(wanted)}"
    return None


@dataclass(frozen=True)
class Analysis:
    name: str
    reply: type[BaseModel]
    check: Callable[[BaseModel, dict[str, str]], str | None]

    @property
    def prompt_name(self) -> str:
        return f"observer/{self.name}.md"


ANALYSES: dict[str, Analysis] = {
    "themes": Analysis("themes", ThemesReply, _evidence(lambda r: [t.evidence for t in r.themes])),
    "private_public": Analysis("private_public", PrivatePublicReply, _evidence(
        lambda r: [q for f in r.findings for q in (f.journal_quote, f.review_quote)])),
    "self_image": Analysis("self_image", SelfImageReply, _evidence(
        lambda r: [c.evidence for c in r.changes] + [r.gap_evidence])),
    "benchmark": Analysis("benchmark", BenchmarkReply, _evidence(lambda r: list(r.evidence))),
    "codebook": Analysis("codebook", CodebookReply, _covers_labels),
}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class Judge:
    def __init__(self, client: ModelClient, store: ObserverStore, model_alias: str):
        self.client = client
        self.store = store
        self.model_alias = model_alias

    async def run(self, analysis: Analysis, *, run_id: str, agent_id: str | None, inputs: dict[str, str]) -> Judgment:
        system = render(analysis.prompt_name)
        prompt_sha = _sha(system)
        key = _sha(json.dumps([analysis.name, prompt_sha, self.model_alias, inputs], sort_keys=True, ensure_ascii=False))
        if (hit := self.store.cached(key)) is not None:
            return hit

        def done(status: str, result: dict | None, error: str | None, reported: str | None) -> Judgment:
            judgment = Judgment(analysis.name, run_id, agent_id, status, result, error, reported, prompt_sha)
            self.store.save(key, judgment, self.model_alias)
            return judgment

        user = "\n\n".join(f"## {name}\n\n{text.strip() or '(none)'}" for name, text in inputs.items())
        messages: list[Message] = [{"role": "user", "content": user + "\n\nReply with JSON only."}]
        schema = simplify_schema(analysis.reply.model_json_schema())
        reported: str | None = None
        problem = "no reply"
        for _ in range(2):
            try:
                result = await self.client.chat(system, messages, schema)
            except ModelError as exc:
                return done("failed", None, f"model error: {exc}", reported)
            reported = result.model or reported
            try:
                reply = analysis.reply.model_validate(extract_json(result.text))
                failure = analysis.check(reply, inputs)
                if failure is None:
                    return done("ok", reply.model_dump(mode="json"), None, reported)
                problem = failure
            except ValidationError as exc:
                problem = summarise_validation(exc)
            except ActionError as exc:
                problem = str(exc)
            messages += [{"role": "assistant", "content": result.text},
                         {"role": "user", "content": correction_request(problem)}]
        return done("failed", None, problem, reported)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest -q tests/test_observer_judge.py`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/aiswarm/observer/judge.py src/aiswarm/prompts/observer tests/test_observer_judge.py
git commit -m "feat: observer judge with evidence-checked, cached, once-corrected judgments and its prompts

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Judge inputs from community data

**Files:**
- Create: `src/aiswarm/observer/inputs.py`
- Test: `tests/test_observer_inputs.py`

**Interfaces:**
- Consumes: `RunInfo`, `BenchmarkAnswer` (Tasks 4–5); `ProfileStore.file_at` (Task 2); `Who`.
- Produces:
  - `themes_inputs(conn, store, run, agent_id) -> dict[str, str]` keys: `journal`, `reviews written`, `self.md`, `lessons.md`, `temperament.md`
  - `private_public_inputs(conn, run, agent_id, who) -> dict[str, str] | None` keys `journal`, `reviews given`; None if the agent gave no peer reviews in the run
  - `self_image_inputs(store, runs, run, agent_id) -> dict[str, str]` keys `self.md now`, `public profile now`, `self.md before`, `public profile before`
  - `benchmark_inputs(answers, run_id) -> dict[str, str]` keys `this answer`, `earlier answers`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_observer_inputs.py`:

```python
from observer_helpers import add_journal, add_review, add_run

from aiswarm.observer.inputs import benchmark_inputs, private_public_inputs, self_image_inputs, themes_inputs
from aiswarm.observer.metrics import BenchmarkAnswer, closed_runs
from aiswarm.view import make_who

FUTURE = "2999-01-01T00:00:00+00:00"


def test_themes_inputs(community):
    store, conn = community
    store.write_private("a", "self.md", "I am careful.")
    add_run(conn, "r1", created=FUTURE)
    add_journal(conn, "r1", "a", "before", "Feeling unsure.")
    add_journal(conn, "r1", "a", "after", "Bea was right.")
    add_review(conn, "r1", "a", "b", 4, more_of="ideas", reason="good")
    add_review(conn, "r1", "a", "a", 5)
    got = themes_inputs(conn, store, closed_runs(conn)[0], "a")
    assert "Feeling unsure." in got["journal"] and "Bea was right." in got["journal"]
    assert "ideas" in got["reviews written"] and got["reviews written"].count("overall") == 1
    assert got["self.md"] == "I am careful."
    assert got["temperament.md"] == "Temperament of a."


def test_inputs_say_none_when_empty(community):
    store, conn = community
    add_run(conn, "r1", created=FUTURE)
    got = themes_inputs(conn, store, closed_runs(conn)[0], "c")
    assert got["journal"] == "(none)" and got["reviews written"] == "(none)"


def test_private_public_inputs_need_peer_reviews(community):
    store, conn = community
    add_run(conn, "r1", created=FUTURE)
    who = make_who({i: store.public(i) for i in store.ids()})
    assert private_public_inputs(conn, closed_runs(conn)[0], "a", who) is None
    add_review(conn, "r1", "a", "b", 2, less_of="rambling")
    got = private_public_inputs(conn, closed_runs(conn)[0], "a", who)
    assert "Bea (b)" in got["reviews given"] and "rambling" in got["reviews given"]


def test_self_image_inputs_compare_with_the_previous_run(community):
    store, conn = community
    add_run(conn, "r1", agents=("a",), created="2000-01-01T00:00:00+00:00")
    add_run(conn, "r2", agents=("b",), created=FUTURE)
    add_run(conn, "r3", agents=("a",), created=FUTURE)
    runs = closed_runs(conn)
    got = self_image_inputs(store, runs, runs[2], "a")
    assert 'name = "Alpha"' in got["public profile now"]
    assert set(got) == {"self.md now", "public profile now", "self.md before", "public profile before"}
    first = self_image_inputs(store, runs, runs[0], "a")
    assert first["self.md before"] == "(no earlier run)"


def test_benchmark_inputs():
    answers = [BenchmarkAnswer("r1", False, "Pepperoni"), BenchmarkAnswer("r6", True, "Margherita")]
    assert benchmark_inputs(answers, "r1") == {"this answer": "Pepperoni", "earlier answers": "(none)"}
    assert benchmark_inputs(answers, "r6")["earlier answers"] == "### r1\nPepperoni"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_observer_inputs.py`
Expected: FAIL — `No module named 'aiswarm.observer.inputs'`.

- [ ] **Step 3: Implement**

Create `src/aiswarm/observer/inputs.py`:

```python
"""What each judgment reads. All text comes from the community store as of the end of the run."""

import sqlite3
from datetime import datetime, timedelta

from aiswarm.observer.metrics import BenchmarkAnswer, RunInfo
from aiswarm.profiles import ProfileStore
from aiswarm.view import Who

NONE = "(none)"


def _after(run: RunInfo) -> datetime:
    return datetime.fromisoformat(run.end) + timedelta(seconds=1)


def _journal(conn: sqlite3.Connection, run_id: str, agent_id: str) -> str:
    rows = conn.execute("SELECT section, text FROM journals WHERE run_id = ? AND agent_id = ? ORDER BY created_at",
                        (run_id, agent_id)).fetchall()
    return "\n\n".join(f"### {r['section']} reviews\n{r['text'].strip()}" for r in rows) or NONE


def _reviews_by(conn: sqlite3.Connection, run_id: str, agent_id: str, who: Who | None) -> str:
    rows = conn.execute("SELECT * FROM reviews WHERE run_id = ? AND reviewer = ? AND is_self = 0 ORDER BY id",
                        (run_id, agent_id)).fetchall()
    parts = []
    for r in rows:
        about = who(r["reviewee"]) if who else r["reviewee"]
        parts.append(f"### about {about}\nmore of: {r['more_of']}\nless of: {r['less_of']}\n"
                     f"private note: {r['private_note']}\noverall: {r['overall']} — {r['overall_reason']}")
    return "\n\n".join(parts) or NONE


def themes_inputs(conn: sqlite3.Connection, store: ProfileStore, run: RunInfo, agent_id: str) -> dict[str, str]:
    at = _after(run)
    files = {name: store.file_at(agent_id, name, at) or NONE for name in ("self.md", "lessons.md", "temperament.md")}
    return {"journal": _journal(conn, run.id, agent_id), "reviews written": _reviews_by(conn, run.id, agent_id, None),
            **files}


def private_public_inputs(conn: sqlite3.Connection, run: RunInfo, agent_id: str, who: Who) -> dict[str, str] | None:
    reviews = _reviews_by(conn, run.id, agent_id, who)
    if reviews == NONE:
        return None
    return {"journal": _journal(conn, run.id, agent_id), "reviews given": reviews}


def self_image_inputs(store: ProfileStore, runs: list[RunInfo], run: RunInfo, agent_id: str) -> dict[str, str]:
    index = next(i for i, r in enumerate(runs) if r.id == run.id)
    earlier = [r for r in runs[:index] if agent_id in r.agent_ids]
    now = _after(run)
    out = {"self.md now": store.file_at(agent_id, "self.md", now) or NONE,
           "public profile now": store.file_at(agent_id, "public.toml", now) or NONE}
    if earlier:
        then = _after(earlier[-1])
        out["self.md before"] = store.file_at(agent_id, "self.md", then) or NONE
        out["public profile before"] = store.file_at(agent_id, "public.toml", then) or NONE
    else:
        out["self.md before"] = out["public profile before"] = "(no earlier run)"
    return out


def benchmark_inputs(answers: list[BenchmarkAnswer], run_id: str) -> dict[str, str]:
    index = next(i for i, a in enumerate(answers) if a.run_id == run_id)
    earlier = "\n\n".join(f"### {a.run_id}\n{a.text}" for a in answers[:index]) or NONE
    return {"this answer": answers[index].text, "earlier answers": earlier}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q tests/test_observer_inputs.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aiswarm/observer/inputs.py tests/test_observer_inputs.py
git commit -m "feat: observer judge inputs drawn from the community as of each run's end

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Codebook merge and theme counts

**Files:**
- Create: `src/aiswarm/observer/codebook.py`
- Test: `tests/test_observer_codebook.py`

**Interfaces:**
- Consumes: `Judge`, `ANALYSES` (Task 6); `ObserverStore` (Task 3).
- Produces: `async update_codebook(judge, store, run_id, labels: list[str]) -> Judgment | None`; `theme_counts(store, judgments: list[Judgment]) -> dict[str, Counter]` (agent id → code → count; unmapped labels counted under the label itself).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_observer_codebook.py`:

```python
import json

from aiswarm.models.fake import FakeModel
from aiswarm.observer.codebook import theme_counts, update_codebook
from aiswarm.observer.judge import Judge
from aiswarm.observer.store import Judgment, ObserverStore


async def test_new_labels_are_assigned_and_known_ones_skipped(tmp_path):
    store = ObserverStore(tmp_path / "o.db")
    store.assign("honesty", "honesty", "Says what it thinks.")
    reply = json.dumps({"assignments": [{"label": "candour", "code": "honesty", "description": "Says what it thinks."},
                                        {"label": "tidiness", "code": "tidiness", "description": "Keeps order."}]})
    client = FakeModel([reply])
    got = await update_codebook(Judge(client, store, "sonnet"), store, "r1", ["honesty", "candour", "tidiness"])
    assert got.status == "ok"
    assert store.code_for("candour") == "honesty" and store.code_for("tidiness") == "tidiness"
    assert "honesty: Says what it thinks." in client.calls[0][1][0]["content"]


async def test_nothing_new_makes_no_call(tmp_path):
    store = ObserverStore(tmp_path / "o.db")
    store.assign("honesty", "honesty", "d")
    client = FakeModel([])
    assert await update_codebook(Judge(client, store, "sonnet"), store, "r1", ["honesty"]) is None
    assert client.calls == []


def test_theme_counts_use_codes(tmp_path):
    store = ObserverStore(tmp_path / "o.db")
    store.assign("candour", "honesty", "d")
    j = Judgment("themes", "r1", "a", "ok", {"themes": [
        {"label": "candour", "polarity": "expressed", "evidence": "x", "source": "s"},
        {"label": "speed", "polarity": "expressed", "evidence": "y", "source": "s"}]}, None, None, "p")
    failed = Judgment("themes", "r1", "b", "failed", None, "boom", None, "p")
    assert theme_counts(store, [j, failed]) == {"a": {"honesty": 1, "speed": 1}}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_observer_codebook.py`
Expected: FAIL — `No module named 'aiswarm.observer.codebook'`.

- [ ] **Step 3: Implement**

Create `src/aiswarm/observer/codebook.py`:

```python
"""Open coding: theme labels in the judge's own words, merged into a codebook that grows with the community."""

from collections import Counter

from aiswarm.observer.judge import ANALYSES, Judge
from aiswarm.observer.store import Judgment, ObserverStore


async def update_codebook(judge: Judge, store: ObserverStore, run_id: str, labels: list[str]) -> Judgment | None:
    new = sorted({label for label in labels if store.code_for(label) is None})
    if not new:
        return None
    listing = "\n".join(f"- {name}: {description}" for name, description in store.codes()) or "(empty)"
    judgment = await judge.run(ANALYSES["codebook"], run_id=run_id, agent_id=None,
                               inputs={"codebook": listing, "new labels": "\n".join(new)})
    if judgment.status == "ok" and judgment.result:
        for a in judgment.result["assignments"]:
            store.assign(a["label"], a["code"], a["description"])
    return judgment


def theme_counts(store: ObserverStore, judgments: list[Judgment]) -> dict[str, Counter]:
    counts: dict[str, Counter] = {}
    for j in judgments:
        if j.analysis != "themes" or j.status != "ok" or not j.result or j.agent_id is None:
            continue
        for theme in j.result["themes"]:
            counts.setdefault(j.agent_id, Counter())[store.code_for(theme["label"]) or theme["label"]] += 1
    return counts
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q tests/test_observer_codebook.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aiswarm/observer/codebook.py tests/test_observer_codebook.py
git commit -m "feat: observer codebook merges open-coded theme labels

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Charts and report Markdown

**Files:**
- Create: `src/aiswarm/observer/charts.py`, `src/aiswarm/observer/report.py`
- Test: `tests/test_observer_report.py`

**Interfaces:**
- Consumes: metrics dataclasses (Tasks 4–5), `Judgment` (Task 3), `Who`.
- Produces:
  - `marker_positions(markers: list[int]) -> list[float]`; `trend_chart(path, title, labels, series: dict[str, list[float | None]], markers, ylabel="") -> Path`
  - `RunSection` dataclass (fields below); `render_run(section) -> str`
  - `SummarySection` dataclass (fields below); `render_summary(section) -> str`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_observer_report.py`:

```python
from collections import Counter

from aiswarm.observer.charts import marker_positions, trend_chart
from aiswarm.observer.metrics import BenchmarkAnswer, CategoryStats, Credits, Influence, RunInfo
from aiswarm.observer.report import RunSection, SummarySection, render_run, render_summary
from aiswarm.observer.store import Judgment

RUN = RunInfo("r1", "Pick a fruit.", "best-fruit", "contested", False, "budget", ("a", "b"), "t", "t")


def who(agent_id):
    return {"a": "Alpha (a)", "b": "Bea (b)"}[agent_id]


def themes(agent):
    return Judgment("themes", "r1", agent, "ok", {"themes": [
        {"label": "candour", "polarity": "expressed", "evidence": "I say it straight", "source": "journal"}]},
        None, "claude-x", "p")


def section(**kw):
    base = dict(run=RUN, who=who, changes=[], mix={"a": Counter(propose=2), "b": Counter(failed=1)},
                influence={"a": Influence(adopted=1), "b": Influence()}, credits=None, peer={"a": 3.5},
                gap={"a": 1.0}, judgments=[themes("a")], codes={"candour": "honesty"}, benchmark=None,
                judge_note=None)
    base.update(kw)
    return RunSection(**base)


def test_marker_positions_sit_between_runs():
    assert marker_positions([1, 3]) == [0.5, 2.5]


def test_trend_chart_writes_a_png(tmp_path):
    path = trend_chart(tmp_path / "c" / "x.png", "Peer score", ["r1", "r2"], {"a": [3.0, None]}, [1])
    assert path.exists() and path.read_bytes()[:4] == b"\x89PNG"


def test_run_report_sections():
    text = render_run(section())
    assert text.startswith("# r1 — Pick a fruit.")
    assert "contested" in text and "budget" in text
    assert "| Alpha (a) |" in text
    assert "honesty" in text and "I say it straight" in text
    assert "no closing call yet" in text
    assert "_not judged (no judgment)_" in text  # b has no themes judgment


def test_run_report_shows_harness_changes_and_failures():
    failed = Judgment("themes", "r1", "b", "failed", None, "invented quote", None, "p")
    text = render_run(section(changes=["since run r0: prompts journal_request.md"], judgments=[themes("a"), failed]))
    assert "Harness changed" in text and "journal_request.md" in text
    assert "_not judged (invented quote)_" in text


def test_run_report_without_a_judge():
    text = render_run(section(judgments=[], judge_note="no judge configured"))
    assert "_not judged (no judge configured)_" in text


def test_run_report_credits():
    text = render_run(section(credits=Credits("a", {"a": "me", "b": "idea"})))
    assert "self-credit" in text and "idea" in text


def test_summary_sections(tmp_path):
    s = SummarySection(
        runs=[RUN], who=who, charts={"Peer score": "charts/peer.png"}, identity=[], norms=[],
        codebook=[("honesty", "Says what it thinks.")], twins={"e": []},
        categories={"contested": CategoryStats(1, Counter(budget=1), Counter(propose=2))},
        failed=[], benchmark=[BenchmarkAnswer("r1", False, "- Alpha (a): Pepperoni")], reciprocity=[],
        in_progress=["r9"], judge_note=None)
    text = render_summary(s)
    assert "![Peer score](charts/peer.png)" in text
    assert "honesty" in text and "Pepperoni" in text
    assert "e: no twin found" in text
    assert "r9" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_observer_report.py`
Expected: FAIL — `No module named 'aiswarm.observer.charts'`.

- [ ] **Step 3: Implement charts**

Create `src/aiswarm/observer/charts.py`:

```python
"""Headless trend charts. A dashed line marks a harness change before a run."""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


def marker_positions(markers: list[int]) -> list[float]:
    return [m - 0.5 for m in markers]


def trend_chart(path: Path, title: str, labels: list[str], series: dict[str, list[float | None]],
                markers: list[int], ylabel: str = "") -> Path:
    fig, ax = plt.subplots(figsize=(8, 4))
    xs = list(range(len(labels)))
    for name, values in sorted(series.items()):
        points = [(x, v) for x, v in zip(xs, values) if v is not None]
        if points:
            px, py = zip(*points)
            ax.plot(px, py, marker="o", label=name)
    for x in marker_positions(markers):
        ax.axvline(x, color="grey", linestyle="--", linewidth=1)
    ax.set_xticks(xs, labels, rotation=30, ha="right")
    ax.set_title(title)
    ax.set_ylabel(ylabel)
    if any(v is not None for values in series.values() for v in values):
        ax.legend(fontsize="small")
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path
```

- [ ] **Step 4: Implement report**

Create `src/aiswarm/observer/report.py`:

```python
"""Markdown for the Observer's reports. User-only; never shown to any agent."""

from collections import Counter
from dataclasses import dataclass

from aiswarm.observer.metrics import (
    ACTIONS, BenchmarkAnswer, CategoryStats, Credits, IdentityEvent, Influence, NormEvent, RunInfo,
)
from aiswarm.observer.store import Judgment
from aiswarm.view import Who


@dataclass
class RunSection:
    run: RunInfo
    who: Who
    changes: list[str]
    mix: dict[str, Counter]
    influence: dict[str, Influence]
    credits: Credits | None
    peer: dict[str, float]
    gap: dict[str, float]
    judgments: list[Judgment]
    codes: dict[str, str]  # theme label -> code
    benchmark: BenchmarkAnswer | None
    judge_note: str | None  # why nothing was judged (no judge configured / --no-judge), else None


def _find(judgments: list[Judgment], analysis: str, agent_id: str | None) -> Judgment | None:
    return next((j for j in judgments if j.analysis == analysis and j.agent_id == agent_id), None)


def _not_judged(j: Judgment | None, note: str | None) -> str | None:
    if j is not None and j.status == "ok":
        return None
    reason = note or (j.error if j is not None else "no judgment")
    return f"_not judged ({reason})_"


def _fmt(value: float | None) -> str:
    return "–" if value is None else f"{value:.1f}"


def render_run(s: RunSection) -> str:
    r = s.run
    out = [f"# {r.id} — {r.goal}", "",
           f"- **Goal:** {r.label} ({r.category}){' — benchmark' if r.is_benchmark else ''}",
           f"- **Ended:** {r.ended_reason or 'unknown'}"]
    if s.changes:
        out += ["", "> **Harness changed:** " + "; ".join(s.changes)]
    acts = [a for a in ACTIONS]
    out += ["", "## Metrics", "", "| Agent | " + " | ".join(acts) + " | influence | peer | self − peer |",
            "|---" * (len(acts) + 4) + "|"]
    for agent in r.agent_ids:
        mix = s.mix.get(agent, Counter())
        out.append(f"| {s.who(agent)} | " + " | ".join(str(mix.get(a, 0)) for a in acts)
                   + f" | {s.influence.get(agent, Influence()).score} | {_fmt(s.peer.get(agent))} | {_fmt(s.gap.get(agent))} |")
    out += ["", "## Credits", ""]
    if s.credits is None:
        out.append("No closing call yet: the run did not end on consensus.")
    else:
        out.append(f"Called by {s.who(s.credits.caller)}.")
        for agent in r.agent_ids:
            contribution = s.credits.credited.get(agent)
            mark = "self-credit" if agent == s.credits.caller and contribution is not None else ""
            out.append(f"- {s.who(agent)}: influence {s.influence.get(agent, Influence()).score}; "
                       f"{'credited: ' + contribution if contribution is not None else 'not credited'} {mark}".rstrip())
    out += ["", "## Themes", ""]
    for agent in r.agent_ids:
        j = _find(s.judgments, "themes", agent)
        out.append(f"### {s.who(agent)}")
        if (missing := _not_judged(j, s.judge_note)) is not None:
            out += [missing, ""]
            continue
        for t in j.result["themes"]:
            code = s.codes.get(t["label"], t["label"])
            out.append(f"- **{code}** ({t['polarity']}; “{t['label']}”): > {t['evidence']} — _{t['source']}_")
        out.append("")
    out += ["## Private journal vs public reviews", ""]
    for agent in r.agent_ids:
        j = _find(s.judgments, "private_public", agent)
        out.append(f"### {s.who(agent)}")
        if j is None and s.judge_note is None:
            out += ["_gave no peer reviews_", ""]
            continue
        if (missing := _not_judged(j, s.judge_note)) is not None:
            out += [missing, ""]
            continue
        for f in j.result["findings"]:
            out.append(f"- {f['colleague']}: **{f['verdict']}** — journal: “{f['journal_quote']}” / review: “{f['review_quote']}”")
        out.append("")
    out += ["## Self-image", ""]
    for agent in r.agent_ids:
        j = _find(s.judgments, "self_image", agent)
        out.append(f"### {s.who(agent)}")
        if (missing := _not_judged(j, s.judge_note)) is not None:
            out += [missing, ""]
            continue
        out += [f"- {c['what']}: “{c['evidence']}”" for c in j.result["changes"]] or ["- no change"]
        out += [f"- private/public gap: **{j.result['private_public_gap']}** {j.result.get('gap_evidence', '')}".rstrip(), ""]
    if s.benchmark is not None:
        out += ["## Benchmark answer", "", s.benchmark.text, ""]
        j = _find(s.judgments, "benchmark", None)
        if (missing := _not_judged(j, s.judge_note)) is not None:
            out.append(missing)
        else:
            out += [f"- **Trend:** {j.result['trend']}", f"- **Optimises for:** {j.result['optimises_for']}"]
            out += [f"  - “{q}”" for q in j.result["evidence"]]
    models = sorted({j.model_reported for j in s.judgments if j.model_reported})
    prompts = sorted({f"{j.analysis} {j.prompt_sha256[:8]}" for j in s.judgments})
    if models or prompts:
        out += ["", f"_Judged by {', '.join(models) or 'unknown model'}; prompts {', '.join(prompts)}._"]
    return "\n".join(out).rstrip() + "\n"


@dataclass
class SummarySection:
    runs: list[RunInfo]
    who: Who
    charts: dict[str, str]  # title -> path relative to summary.md
    identity: list[IdentityEvent]
    norms: list[NormEvent]
    codebook: list[tuple[str, str]]
    twins: dict[str, list[str]]
    categories: dict[str, CategoryStats]
    failed: list[Judgment]
    benchmark: list[BenchmarkAnswer]
    reciprocity: list[tuple[str, str, str]]
    in_progress: list[str]
    judge_note: str | None


def render_summary(s: SummarySection) -> str:
    out = ["# Observer summary", "", f"{len(s.runs)} closed runs: " + ", ".join(r.label for r in s.runs)]
    if s.in_progress:
        out.append(f"In progress (skipped): {', '.join(s.in_progress)}")
    if s.judge_note:
        out.append(f"Not judged: {s.judge_note}.")
    out += ["", "## Trends", ""]
    if not s.charts:
        out.append("_Trend charts need at least 2 closed runs._")
    for title, path in s.charts.items():
        out += [f"### {title}", "", f"![{title}]({path})", ""]
    out += ["## Control vs twin", ""]
    for control, found in s.twins.items():
        out.append(f"- {control}: " + (", ".join(found) if found else "no twin found"))
    out += ["", "## By goal category", "", "| Category | Runs | Endings | Top actions |", "|---|---|---|---|"]
    for name, c in sorted(s.categories.items()):
        endings = ", ".join(f"{k} {v}" for k, v in c.endings.most_common())
        top = ", ".join(f"{k} {v}" for k, v in c.actions.most_common(4))
        out.append(f"| {name} | {c.runs} | {endings} | {top} |")
    out += ["", "## Benchmark answers", ""]
    for a in s.benchmark:
        out += [f"### {a.run_id} ({'consensus' if a.consensus else 'no consensus'})", "", a.text, ""]
    if not s.benchmark:
        out += ["_No benchmark runs yet._", ""]
    out += ["## Identity events", ""]
    out += [f"- {e.at.isoformat(timespec='minutes')} {s.who(e.agent_id)}: {e.kind} {e.detail}".rstrip()
            + f" (prior peer score {e.prior_peer:.1f})" if e.prior_peer is not None else
            f"- {e.at.isoformat(timespec='minutes')} {s.who(e.agent_id)}: {e.kind} {e.detail}".rstrip()
            for e in s.identity] or ["_None yet._"]
    out += ["", "## Norms and dimensions", ""]
    out += [f"- {n.run_id} {s.who(n.author)} proposed a {n.kind} ({n.status}): {n.text}" for n in s.norms] or ["_None yet._"]
    out += ["", "## Credit reciprocity", ""]
    out += [f"- {run}: {s.who(g)} → {s.who(r)}" for run, g, r in s.reciprocity] or ["_No credits yet._"]
    out += ["", "## Codebook", ""]
    out += [f"- **{name}**: {description}" for name, description in s.codebook] or ["_Empty._"]
    out += ["", "## Not judged", ""]
    out += [f"- {j.run_id} {j.analysis} {j.agent_id or ''}: {j.error}".replace("  ", " ") for j in s.failed] or ["_Nothing failed._"]
    return "\n".join(out).rstrip() + "\n"
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest -q tests/test_observer_report.py`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/aiswarm/observer/charts.py src/aiswarm/observer/report.py tests/test_observer_report.py
git commit -m "feat: observer charts with harness markers, and run and summary markdown

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Orchestration and the `observe` command

**Files:**
- Create: `src/aiswarm/observer/observe.py`
- Modify: `src/aiswarm/cli.py`
- Test: `tests/test_observer_observe.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: everything above; `make_client` (existing); `limit_clients` (existing); `make_who` (existing).
- Produces: `ObserveResult(written: list[Path], judged: int, failed: int, in_progress: list[str])`; `async observe(cfg, *, run_id=None, judge_client=None, twin=None, judge_note=None) -> ObserveResult`; CLI `aiswarm observe [--run ID] [--no-judge] [--twin ID]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_observer_observe.py`:

```python
import json

import pytest
from observer_helpers import add_entry, add_journal, add_review, add_run

from aiswarm.config import Config
from aiswarm.models.fake import FakeModel
from aiswarm.observer.observe import observe

FUTURE = "2999-01-01T00:00:00+00:00"


def config(tmp_path):
    cfg = Config.model_validate({"models": {"claude": {"kind": "claude_cli"}}, "observer": {"judge": "claude"}})
    cfg.community_dir = tmp_path / "community"
    cfg.runs_dir = tmp_path / "runs"
    cfg.observer.dir = tmp_path / "observer"
    return cfg


def judge_reply(system, messages):
    text = messages[0]["content"]
    if "codebook" in system.lower() and "new labels" in text:
        labels = text.split("## new labels\n\n")[1].split("\n\nReply")[0].splitlines()
        return json.dumps({"assignments": [{"label": l, "code": l, "description": "d"} for l in labels]})
    if "## journal" in text and "## reviews written" in text:
        quote = "Feeling sure."
        return json.dumps({"themes": [{"label": "confidence", "polarity": "expressed", "evidence": quote,
                                       "source": "journal"}]}) if quote in text else json.dumps(
            {"themes": [{"label": "quiet", "polarity": "expressed", "evidence": "(none)", "source": "journal"}]})
    if "## reviews given" in text:
        return json.dumps({"findings": []})
    if "## self.md now" in text:
        return json.dumps({"changes": [], "private_public_gap": "none", "gap_evidence": ""})
    return json.dumps({"trend": "first", "optimises_for": "x", "evidence": []})


@pytest.fixture
def lab(tmp_path, community):
    store, conn = community
    add_run(conn, "r1", created=FUTURE, goal_id="best-fruit", category="contested")
    add_entry(conn, "r1", "a", "propose", {"answer_summary": "Mango", "method": "m"})
    add_journal(conn, "r1", "a", "after", "Feeling sure.")
    add_review(conn, "r1", "a", "b", 4)
    add_run(conn, "r2", created=FUTURE, benchmark=True)
    add_entry(conn, "r2", "b", "propose", {"answer_summary": "Pepperoni", "method": "m"})
    add_run(conn, "r3", status="running", created=FUTURE)
    return config(tmp_path)


async def test_observe_writes_reports_summary_and_charts(lab):
    result = await observe(lab, judge_client=FakeModel(judge_reply))
    names = {p.name for p in result.written}
    assert {"r1.md", "r2.md", "summary.md"} <= names
    assert any(p.suffix == ".png" for p in result.written)
    assert result.in_progress == ["r3"] and result.failed == 0
    r1 = (lab.observer.dir / "runs" / "r1.md").read_text()
    assert "confidence" in r1 and "Feeling sure." in r1
    assert "Pepperoni" in (lab.observer.dir / "runs" / "r2.md").read_text()


async def test_second_observe_uses_the_cache(lab):
    client = FakeModel(judge_reply)
    await observe(lab, judge_client=client)
    calls = len(client.calls)
    await observe(lab, judge_client=client)
    assert len(client.calls) == calls


async def test_observe_one_run_without_a_judge(lab):
    result = await observe(lab, run_id="r1", judge_note="--no-judge")
    assert [p.name for p in result.written] == ["r1.md"]
    assert "_not judged (--no-judge)_" in (lab.observer.dir / "runs" / "r1.md").read_text()


async def test_observe_unknown_run(lab):
    with pytest.raises(ValueError, match="no closed run"):
        await observe(lab, run_id="nope")
```

Append to `tests/test_cli.py`:

```python
def test_observe_without_a_judge(project):
    tmp_path, config = project
    for agent_id in ("a", "b"):
        assert invoke("agents", "new", agent_id, "--model", "local", "--temperament", "T.", "-c", config).exit_code == 0
    assert invoke("run", "Pick a colour", "--max-rounds", "2", "--no-postrun", "-c", config).exit_code == 0
    result = invoke("observe", "-c", config)
    assert result.exit_code == 0, result.output
    assert "metrics only" in result.output
    assert (tmp_path / "observer" / "summary.md").exists()


def test_observe_unknown_run_fails_cleanly(project):
    tmp_path, config = project
    result = invoke("observe", "--run", "nope", "--no-judge", "-c", config)
    assert result.exit_code != 0 and "no closed run" in result.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_observer_observe.py tests/test_cli.py`
Expected: FAIL — `No module named 'aiswarm.observer.observe'`; `No such command 'observe'`.

- [ ] **Step 3: Implement the orchestrator**

Create `src/aiswarm/observer/observe.py`:

```python
"""One `aiswarm observe`: metrics, judgments, charts and reports for closed runs."""

import asyncio
from dataclasses import dataclass
from pathlib import Path

from aiswarm.config import Config
from aiswarm.db import connect_readonly
from aiswarm.models.base import ModelClient, limit_clients
from aiswarm.observer import metrics as m
from aiswarm.observer.charts import trend_chart
from aiswarm.observer.codebook import theme_counts, update_codebook
from aiswarm.observer.inputs import benchmark_inputs, private_public_inputs, self_image_inputs, themes_inputs
from aiswarm.observer.judge import ANALYSES, Judge
from aiswarm.observer.report import RunSection, SummarySection, render_run, render_summary
from aiswarm.observer.store import ObserverStore
from aiswarm.profiles import ProfileStore
from aiswarm.provenance import provenance_changes
from aiswarm.view import make_who

JUDGE_CONCURRENCY = 3


@dataclass(frozen=True)
class ObserveResult:
    written: list[Path]
    judged: int
    failed: int
    in_progress: list[str]


async def _judge_run(judge: Judge, ostore: ObserverStore, conn, store, runs, run, who, answers) -> None:
    jobs = []
    for agent in run.agent_ids:
        jobs.append(judge.run(ANALYSES["themes"], run_id=run.id, agent_id=agent,
                              inputs=themes_inputs(conn, store, run, agent)))
        if (pp := private_public_inputs(conn, run, agent, who)) is not None:
            jobs.append(judge.run(ANALYSES["private_public"], run_id=run.id, agent_id=agent, inputs=pp))
        jobs.append(judge.run(ANALYSES["self_image"], run_id=run.id, agent_id=agent,
                              inputs=self_image_inputs(store, runs, run, agent)))
    if run.is_benchmark:
        jobs.append(judge.run(ANALYSES["benchmark"], run_id=run.id, agent_id=None,
                              inputs=benchmark_inputs(answers, run.id)))
    await asyncio.gather(*jobs)
    labels = [t["label"] for j in ostore.judgments(run.id, "themes") if j.status == "ok" for t in j.result["themes"]]
    await update_codebook(judge, ostore, run.id, labels)


def _series(runs, value) -> dict[str, list[float | None]]:
    agents = sorted({a for r in runs for a in r.agent_ids})
    return {a: [value(r, a) for r in runs] for a in agents}


async def observe(cfg: Config, *, run_id: str | None = None, judge_client: ModelClient | None = None,
                  twin: str | None = None, judge_note: str | None = None) -> ObserveResult:
    conn = connect_readonly(cfg.db_path)
    store = ProfileStore(cfg.community_dir)
    out_dir = cfg.observer.dir
    ostore = ObserverStore(out_dir / "observer.db")
    runs = m.closed_runs(conn)
    targets = runs if run_id is None else [r for r in runs if r.id == run_id]
    if run_id is not None and not targets:
        raise ValueError(f"no closed run {run_id!r}")
    who = make_who({i: store.public(i) for i in store.ids()})
    answers = m.benchmark_answers(conn, runs, who)
    judge = None
    if judge_client is not None:
        limited = limit_clients({"judge": judge_client}, JUDGE_CONCURRENCY)["judge"]
        judge = Judge(limited, ostore, cfg.observer.judge or "judge")
    written: list[Path] = []
    for run in targets:
        if judge is not None:
            await _judge_run(judge, ostore, conn, store, runs, run, who, answers)
        judgments = ostore.judgments(run.id)
        labels = {t["label"] for j in judgments if j.analysis == "themes" and j.status == "ok" for t in j.result["themes"]}
        section = RunSection(
            run=run, who=who, changes=provenance_changes(conn, run.id), mix=m.action_mix(conn, run),
            influence=m.influence(conn, run), credits=m.credits(conn, run.id), peer=m.peer_scores(conn, run.id),
            gap=m.self_peer_gap(conn, run.id), judgments=judgments,
            codes={label: ostore.code_for(label) or label for label in labels},
            benchmark=next((a for a in answers if a.run_id == run.id), None),
            judge_note=judge_note if judge is None and not judgments else None,
        )
        path = out_dir / "runs" / f"{run.id}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_run(section))
        written.append(path)
    if run_id is None:
        written += _write_summary(conn, store, ostore, runs, who, answers, out_dir, twin, judge_note if judge is None else None)
    all_judgments = [j for r in targets for j in ostore.judgments(r.id)]
    return ObserveResult(written, sum(j.status == "ok" for j in all_judgments),
                         sum(j.status == "failed" for j in all_judgments), m.open_runs(conn))


def _write_summary(conn, store, ostore, runs, who, answers, out_dir: Path, twin, judge_note) -> list[Path]:
    written: list[Path] = []
    charts: dict[str, str] = {}
    if len(runs) >= 2:
        labels = [r.label for r in runs]
        markers = m.harness_changes(conn, runs)
        peer = {r.id: m.peer_scores(conn, r.id) for r in runs}
        gap = {r.id: m.self_peer_gap(conn, r.id) for r in runs}
        mixes = {r.id: m.action_mix(conn, r) for r in runs}

        def share(r, action):
            total = sum(sum(c.values()) for c in mixes[r.id].values())
            return None if total == 0 else sum(c[action] for c in mixes[r.id].values()) / total

        specs = {
            "Peer score": ("peer.png", _series(runs, lambda r, a: peer[r.id].get(a)), "mean peer rating"),
            "Self minus peer": ("self_peer.png", _series(runs, lambda r, a: gap[r.id].get(a)), "self − peer"),
            "Action mix": ("actions.png", {act: [share(r, act) for r in runs] for act in m.ACTIONS}, "share of turns"),
        }
        counts_by_run = {r.id: theme_counts(ostore, ostore.judgments(r.id, "themes")) for r in runs}
        totals: dict[str, int] = {}
        for per_agent in counts_by_run.values():
            for counter in per_agent.values():
                for code, n in counter.items():
                    totals[code] = totals.get(code, 0) + n
        top = sorted(totals, key=lambda c: -totals[c])[:6]
        if top:
            specs["Themes"] = ("themes.png", {code: [float(sum(c.get(code, 0) for c in counts_by_run[r.id].values()))
                                                     for r in runs] for code in top}, "agents expressing")
        for title, (filename, series, ylabel) in specs.items():
            written.append(trend_chart(out_dir / "charts" / filename, title, labels, series, markers, ylabel))
            charts[title] = f"charts/{filename}"
    found = m.twins(store)
    if twin is not None:
        found = {control: [twin] for control in found}
    summary = SummarySection(
        runs=runs, who=who, charts=charts, identity=m.identity_events(conn, store, runs), norms=m.norm_events(conn),
        codebook=ostore.codes(), twins=found, categories=m.by_category(conn, runs), failed=ostore.failed(),
        benchmark=answers, reciprocity=m.credit_pairs(conn, runs), in_progress=m.open_runs(conn), judge_note=judge_note,
    )
    path = out_dir / "summary.md"
    path.write_text(render_summary(summary))
    return [*written, path]
```

- [ ] **Step 4: Implement the CLI command**

In `src/aiswarm/cli.py`, add the import near the others:

```python
from aiswarm.observer.observe import observe as run_observer
```

Add the command after `show` (or at the end of the top-level commands):

```python
@app.command()
def observe(
    run: Annotated[str | None, typer.Option("--run", help="Report on one closed run only.")] = None,
    no_judge: Annotated[bool, typer.Option("--no-judge", help="Metrics only; no LLM calls.")] = False,
    twin: Annotated[str | None, typer.Option(help="Agent to compare with the control agent.")] = None,
    config: ConfigOpt = DEFAULT_PATH,
) -> None:
    """User-only analysis of the community: reports and charts under the observer directory."""
    cfg = _load(config)
    client, note = None, None
    if no_judge:
        note = "--no-judge"
    elif cfg.observer.judge is None:
        note = "no judge configured"
        typer.echo("no [observer] judge configured; metrics only", err=True)
    else:
        client = make_client(cfg.models[cfg.observer.judge], cfg.run.model_retries)
    try:
        result = asyncio.run(run_observer(cfg, run_id=run, judge_client=client, twin=twin, judge_note=note))
    except (ValueError, sqlite3.OperationalError) as exc:
        _fail(str(exc))
    for path in result.written:
        typer.echo(f"wrote {path}")
    typer.echo(f"judgments: {result.judged} ok, {result.failed} failed")
    if result.in_progress:
        typer.echo(f"skipped (in progress): {', '.join(result.in_progress)}")
```

Add `import sqlite3` at the top of `cli.py` if it is not already imported.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest -q tests/test_observer_observe.py tests/test_cli.py`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/aiswarm/observer/observe.py src/aiswarm/cli.py tests/test_observer_observe.py tests/test_cli.py
git commit -m "feat: aiswarm observe writes run reports, a summary and charts from metrics and cached judgments

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Isolation guarantees

**Files:**
- Test: `tests/test_observer_isolation.py`

**Interfaces:**
- Consumes: `prompt_snapshot` (existing), `observe` (Task 10), observer helpers.

- [ ] **Step 1: Write the tests**

Create `tests/test_observer_isolation.py`:

```python
import ast
import hashlib
from pathlib import Path

from observer_helpers import add_entry, add_run

import aiswarm
from aiswarm.gitrepo import GitRepo
from aiswarm.provenance import prompt_snapshot

SRC = Path(aiswarm.__file__).parent
AGENT_PATH = ["view.py", "agent.py", "runner.py", "board.py", "consensus.py", "actions.py", "introduce.py",
              "checkin.py", "journal.py", "library.py", "norms.py", "reviews.py", "profiles.py", "prompts/__init__.py",
              *[f"postrun/{p.name}" for p in (SRC / "postrun").glob("*.py")]]


def imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def test_no_agent_path_module_imports_the_observer():
    for rel in AGENT_PATH:
        assert not any(name.startswith("aiswarm.observer") for name in imports(SRC / rel)), rel


def test_judge_prompts_are_not_part_of_agent_prompt_provenance():
    assert (SRC / "prompts" / "observer" / "themes.md").exists()
    assert not any("observer" in name or name in {"themes.md", "codebook.md"} for name in prompt_snapshot())


def _fingerprint(community_dir: Path) -> tuple[str | None, str]:
    digest = hashlib.sha256()
    for name in ("community.db", "community.db-wal"):  # not -shm: readers may legitimately touch it
        path = community_dir / name
        if path.exists():
            digest.update(name.encode() + path.read_bytes())
    return GitRepo(community_dir).head(), digest.hexdigest()


async def test_observe_leaves_the_community_untouched(tmp_path, community):
    from aiswarm.config import Config
    from aiswarm.observer.observe import observe

    store, conn = community
    add_run(conn, "r1", created="2999-01-01T00:00:00+00:00")
    add_entry(conn, "r1", "a", "propose", {"answer_summary": "x", "method": "m"})
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    cfg = Config.model_validate({"models": {"claude": {"kind": "claude_cli"}}})
    cfg.community_dir = tmp_path / "community"
    cfg.observer.dir = tmp_path / "observer"
    before = _fingerprint(cfg.community_dir)
    await observe(cfg, judge_note="--no-judge")
    assert _fingerprint(cfg.community_dir) == before
    assert not any("observer" in p.parts for p in cfg.community_dir.rglob("*") if ".git" not in p.parts)
```

- [ ] **Step 2: Run tests**

Run: `uv run pytest -q tests/test_observer_isolation.py`
Expected: PASS. These pin guarantees the earlier tasks already provide; if any fails, the failure is a real leak — fix the code, not the test. To confirm the import test can fail, temporarily add `import aiswarm.observer` to `src/aiswarm/view.py`, run it, see it FAIL, and revert.

- [ ] **Step 3: Run the full suite**

Run: `uv run pytest -q`
Expected: all tests pass (previous 338 plus the new Observer tests).

- [ ] **Step 4: Commit**

```bash
git add tests/test_observer_isolation.py
git commit -m "test: the observer never reaches agents and never changes the community

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Docs

**Files:**
- Modify: `docs/superpowers/specs/2026-10-10-aiswarm-observer-design.md` (status line)

- [ ] **Step 1: Mark the spec implemented**

Change `- **Status:** Draft, awaiting review` to `- **Status:** Approved; Plan 3b implemented`.

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/specs/2026-10-10-aiswarm-observer-design.md
git commit -m "docs: mark Plan 3b (Observer) implemented

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
