# AISwarm Experiment Harness (Plan 3a) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make runs interpretable as experiments: record what produced each run, add a memory-less control agent, drive runs from a tagged goal queue with a recurring benchmark, and give the user a per-agent timeline.

**Architecture:** Four small units on top of the existing engine. `provenance.py` snapshots code/config/prompts/models/servers into a new `run_provenance` table at start, resume and post-run. Control-agent seed and reset live in `ProfileStore` and are triggered by `Runner.start`. `goals.py` owns `community/goals.toml` and goal selection; `runs` gains goal columns and a seed. `timeline.py` merges the community git log with database rows into one markdown document. All new surfaces are user-only; nothing new enters any agent's View.

**Tech Stack:** Python 3.14, `uv`, pydantic, typer, stdlib `sqlite3`/`tomllib`/`urllib`, git via subprocess, pytest + pytest-asyncio (`asyncio_mode = "auto"`). No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-10-aiswarm-experiment-harness-design.md` (extends `docs/superpowers/specs/2026-10-10-aiswarm-design.md` §6).

## Global Constraints

- No new runtime dependencies (`openai`, `pydantic`, `typer` only); HTTP probes use stdlib `urllib`.
- Observation never feeds back: provenance, goal categories, control status, `seed/` files and the timeline never appear in any View or agent prompt.
- Don't seed values: goals state a task only; no prompt text changes in this plan.
- No fabricated data: a failed probe is recorded as an error, never guessed.
- API keys never stored in `run_provenance`.
- Goal categories are exactly `neutral`, `trade-off`, `contested`. Benchmark default `every = 5`.
- Benchmark text: `Pick one pizza topping combination the whole group will share.` (category `trade-off`).
- oMLX exposes no version endpoint (verified 2026-10-10 against `localhost:8008`: `/v1/models` and `/health` exist; `/version`, `/api/version`, `/admin/api/version` 404). Provenance records `/v1/models` and `/health` instead of a version.
- Run tests with `uv run pytest -q` from the repo root; current baseline is 263 passed.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Stay on `develop`; do not push or merge.

## Review Focus

1. **The lab database is already at schema v3 with 5 runs.** Migrating it must keep every run and give old runs `seed = NULL`, `goal_id = NULL`, `is_benchmark = 0` → pinned in Task 1 (`test_migration_4_keeps_existing_runs`).
2. **A hand-edited `goals.toml` that is invalid** (bad TOML, unknown category, duplicate id) must produce a one-line error naming the file, not a traceback → pinned in Task 6 (`test_load_goals_reports_problems`) and Task 7 (`test_goals_list_reports_a_broken_file`).
3. **Goal text with quotes and non-ASCII** (`Is "pain au chocolat" better than a croissant? 🥐`) must round-trip through `goals.toml` → pinned in Task 6 (`test_render_round_trips_quotes_and_unicode`).
4. **AISwarm installed outside a git checkout** must still capture provenance with `code_commit = None` → pinned in Task 2 (`test_code_state_outside_git`).
5. **Post-run prompts must never include an agent's earlier journals** (the control agent depends on it) → pinned in Task 5 (`test_postrun_requests_never_include_earlier_journals`).

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `src/aiswarm/db.py` | modify | Migration 4: `runs.seed/goal_id/goal_category/is_benchmark`, `run_provenance` table |
| `src/aiswarm/gitrepo.py` | modify | `Commit`, `GitRepo.log()`, `GitRepo.is_dirty()` |
| `src/aiswarm/provenance.py` | create | Snapshot, probe, store, fingerprint, `provenance_changes` |
| `src/aiswarm/runner.py` | modify | Seeded per-round RNG; goal metadata; control resets in `start` |
| `src/aiswarm/profiles.py` | modify | Control seed (`save_seed_public`, `has_seed_public`, `reset_to_seed`), `history()`, `write_community_file(author=)` |
| `src/aiswarm/introduce.py` | modify | Save the seed profile for control agents |
| `src/aiswarm/view.py` | modify | Control agents see no earlier journals |
| `src/aiswarm/goals.py` | create | `goals.toml` model, render/load/seed/add, scheduling |
| `src/aiswarm/reviews.py` | modify | `reviews_by()` |
| `src/aiswarm/timeline.py` | create | `resolve_agent()`, `timeline()` |
| `src/aiswarm/cli.py` | modify | Provenance capture + `show` header; `agents new --control`; `run --next`; `goals list/add`; `agents timeline`; `init` seeds goals |
| `tests/test_storage.py`, `tests/test_provenance.py`, `tests/test_runner.py`, `tests/test_profiles.py`, `tests/test_view.py`, `tests/test_postrun.py`, `tests/test_goals.py`, `tests/test_timeline.py`, `tests/test_cli.py` | modify/create | Tests per task |

---

### Task 1: Schema migration and git helpers

**Files:**
- Modify: `src/aiswarm/db.py` (append to `MIGRATIONS`)
- Modify: `src/aiswarm/gitrepo.py`
- Test: `tests/test_storage.py`

**Interfaces:**
- Produces: `runs.seed INTEGER NULL`, `runs.goal_id TEXT NULL`, `runs.goal_category TEXT NULL`, `runs.is_benchmark INTEGER NOT NULL DEFAULT 0`; table `run_provenance(id, run_id, phase, code_commit, code_dirty, config, prompts, models, servers, seed, fingerprint, captured_at)`.
- Produces: `@dataclass(frozen=True) class Commit: at: datetime; author: str; subject: str; patch: str`; `GitRepo.log(path: str, exclude: Sequence[str] = ()) -> list[Commit]` (oldest first); `GitRepo.is_dirty() -> bool`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_storage.py`)

```python
import sqlite3

from aiswarm.gitrepo import Commit


def test_migration_4_keeps_existing_runs(tmp_path):
    raw = sqlite3.connect(tmp_path / "c.db")
    for number, sql in enumerate(MIGRATIONS[:3], start=1):
        raw.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {number};\nCOMMIT;")
    raw.execute("INSERT INTO runs (id, goal, status, agent_ids, created_at) VALUES ('old', 'g', 'closed', '[]', 't')")
    raw.commit()
    raw.close()
    conn = connect(tmp_path / "c.db")
    row = conn.execute("SELECT seed, goal_id, goal_category, is_benchmark FROM runs WHERE id = 'old'").fetchone()
    assert tuple(row) == (None, None, None, 0)
    assert conn.execute("SELECT COUNT(*) FROM run_provenance").fetchone()[0] == 0


def test_log_lists_commits_oldest_first_with_patches_and_exclusions(tmp_path):
    repo = GitRepo.init(tmp_path / "r")
    (repo.path / "d" / "skip").mkdir(parents=True)
    (repo.path / "d" / "a.txt").write_text("one\n")
    repo.commit(["d/a.txt"], "first", author="x")
    (repo.path / "d" / "skip" / "s.txt").write_text("s\n")
    repo.commit(["d/skip"], "skipped", author="y")
    (repo.path / "d" / "a.txt").write_text("two\n")
    repo.commit(["d/a.txt"], "second", author="z")
    commits = repo.log("d", exclude=["d/skip"])
    assert [c.subject for c in commits] == ["first", "second"]
    assert isinstance(commits[0], Commit) and commits[0].at.tzinfo is not None
    assert commits[1].author == "z" and "-one" in commits[1].patch and "+two" in commits[1].patch


def test_log_of_a_repo_without_commits_is_empty(tmp_path):
    assert GitRepo.init(tmp_path / "r").log("d") == []


def test_is_dirty(tmp_path):
    repo = GitRepo.init(tmp_path / "r")
    (repo.path / "a").write_text("1")
    repo.commit(["a"], "m", author="x")
    assert repo.is_dirty() is False
    (repo.path / "b").write_text("2")
    assert repo.is_dirty() is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_storage.py -q`
Expected: FAIL — `ImportError: cannot import name 'Commit'`.

- [ ] **Step 3: Implement**

Append to `MIGRATIONS` in `src/aiswarm/db.py` (a fourth string, after the Plan 2b one):

```python
    """
    ALTER TABLE runs ADD COLUMN seed INTEGER;
    ALTER TABLE runs ADD COLUMN goal_id TEXT;
    ALTER TABLE runs ADD COLUMN goal_category TEXT;
    ALTER TABLE runs ADD COLUMN is_benchmark INTEGER NOT NULL DEFAULT 0;
    CREATE TABLE run_provenance (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL REFERENCES runs(id),
        phase TEXT NOT NULL CHECK (phase IN ('start', 'resume', 'postrun')),
        code_commit TEXT,
        code_dirty INTEGER,
        config TEXT NOT NULL,
        prompts TEXT NOT NULL,
        models TEXT NOT NULL,
        servers TEXT NOT NULL,
        seed INTEGER,
        fingerprint TEXT NOT NULL,
        captured_at TEXT NOT NULL
    );
    CREATE INDEX run_provenance_by_run ON run_provenance (run_id, id);
    """,
```

In `src/aiswarm/gitrepo.py` add imports and the dataclass, then two methods on `GitRepo`:

```python
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Commit:
    at: datetime
    author: str
    subject: str
    patch: str
```

```python
    def is_dirty(self) -> bool:
        return bool(self._git("status", "--porcelain"))

    def log(self, path: str, exclude: Sequence[str] = ()) -> list[Commit]:
        """Commits touching `path` (minus `exclude`), oldest first, each with its patch."""
        if self.head() is None:
            return []
        out = self._git(
            "log", "--reverse", "-p", "--format=%x1e%aI%x1f%an%x1f%s", "--", path,
            *(f":(exclude){p}" for p in exclude),
        )
        commits: list[Commit] = []
        for record in out.split("\x1e"):
            if not record.strip():
                continue
            header, _, patch = record.partition("\n")
            at, author, subject = header.split("\x1f", 2)
            commits.append(Commit(datetime.fromisoformat(at), author, subject, patch.strip("\n")))
        return commits
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_storage.py -q`
Expected: PASS (all, including the existing `user_version == len(MIGRATIONS)` tests).

- [ ] **Step 5: Commit**

```bash
git add src/aiswarm/db.py src/aiswarm/gitrepo.py tests/test_storage.py
git commit -m "feat: add provenance and goal columns to the schema; git log and dirty helpers

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Provenance capture and seeded post order

**Files:**
- Create: `src/aiswarm/provenance.py`
- Modify: `src/aiswarm/runner.py` (`start`, `run`)
- Test: `tests/test_provenance.py` (new), `tests/test_runner.py`

**Interfaces:**
- Consumes: Task 1 schema, `GitRepo.head()`, `GitRepo.is_dirty()`.
- Produces:
  - `Phase = Literal["start", "resume", "postrun"]`
  - `@dataclass(frozen=True) class Provenance: run_id: str; phase: str; code_commit: str | None; code_dirty: bool | None; config: dict; prompts: dict[str, dict[str, str]]; models: dict[str, dict]; servers: dict[str, dict]; seed: int | None; fingerprint: str; captured_at: str`
  - `code_state(code_dir: Path | None = None) -> tuple[str | None, bool | None]`
  - `prompt_snapshot() -> dict[str, dict[str, str]]` (`{"journal_request.md": {"sha256": ..., "text": ...}}`)
  - `async probe_server(backend: OpenAIBackend) -> dict[str, Any]` (`{"models": list[str] | None, "health": dict | None, "error": str | None}`; never raises)
  - `async capture(conn, run_id: str, phase: Phase, cfg: Config, agent_backends: Mapping[str, str], *, probe: Probe = probe_server, code_dir: Path | None = None) -> Provenance`
  - `provenance_rows(conn, run_id: str) -> list[Provenance]` (in capture order)
  - `Runner.start` stores a random 32-bit `runs.seed`; when no `rng` is injected, each round's post order is shuffled with `random.Random(f"{seed}-{round}")`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_provenance.py`:

```python
import hashlib
import json
from importlib.resources import files

import pytest

import aiswarm.provenance as provenance
from aiswarm.config import Config
from aiswarm.gitrepo import GitRepo
from aiswarm.provenance import capture, code_state, probe_server, prompt_snapshot, provenance_rows


def make_config(**run):
    return Config.model_validate({
        "models": {
            "local": {"kind": "openai", "model": "m", "base_url": "http://127.0.0.1:9/v1", "api_key": "SECRET-KEY"},
            "claude": {"kind": "claude_cli", "model": "haiku"},
        },
        "run": run,
    })


async def fake_probe(backend):
    return {"models": [backend.model], "health": {"status": "healthy"}, "error": None}


@pytest.fixture
def run_row(tmp_path, community, make_run):
    store, conn = community
    run_id = make_run(conn, "r1", ("a", "b"))
    conn.execute("UPDATE runs SET seed = 42 WHERE id = 'r1'")
    return conn, run_id


async def test_capture_stores_a_row_without_api_keys(run_row):
    conn, run_id = run_row
    prov = await capture(conn, run_id, "start", make_config(), {"a": "local", "b": "claude"}, probe=fake_probe)
    assert prov.seed == 42 and prov.phase == "start"
    assert prov.models["a"]["backend"] == "local" and prov.models["b"]["model"] == "haiku"
    assert prov.servers == {"http://127.0.0.1:9/v1": {"models": ["m"], "health": {"status": "healthy"}, "error": None}}
    row = dict(conn.execute("SELECT * FROM run_provenance").fetchone())
    assert "SECRET-KEY" not in json.dumps(row)
    assert [p.phase for p in provenance_rows(conn, run_id)] == ["start"]


def test_prompt_snapshot_hashes_every_template():
    snapshot = prompt_snapshot()
    names = sorted(p.name for p in files("aiswarm.prompts").iterdir() if p.name.endswith(".md"))
    assert sorted(snapshot) == names
    text = files("aiswarm.prompts").joinpath("journal_request.md").read_text()
    assert snapshot["journal_request.md"] == {"sha256": hashlib.sha256(text.encode()).hexdigest(), "text": text}


async def test_fingerprint_is_stable_and_tracks_prompt_changes(run_row, monkeypatch):
    conn, run_id = run_row
    cfg, backends = make_config(), {"a": "local"}
    first = await capture(conn, run_id, "start", cfg, backends, probe=fake_probe)
    second = await capture(conn, run_id, "resume", cfg, backends, probe=fake_probe)
    assert first.fingerprint == second.fingerprint
    changed = prompt_snapshot()
    changed["journal_request.md"] = {"sha256": "different", "text": "x"}
    monkeypatch.setattr(provenance, "prompt_snapshot", lambda: changed)
    third = await capture(conn, run_id, "postrun", cfg, backends, probe=fake_probe)
    assert third.fingerprint != first.fingerprint


async def test_a_failing_probe_is_recorded_not_raised(run_row):
    conn, run_id = run_row

    async def broken(backend):
        raise RuntimeError("boom")

    prov = await capture(conn, run_id, "start", make_config(), {"a": "local"}, probe=broken)
    assert prov.servers["http://127.0.0.1:9/v1"]["error"] == "RuntimeError: boom"


async def test_probe_server_reports_an_unreachable_server():
    result = await probe_server(make_config().models["local"])
    assert result["models"] is None and result["error"]


def test_code_state_outside_git(tmp_path):
    assert code_state(tmp_path) == (None, None)


def test_code_state_in_a_checkout(tmp_path):
    repo = GitRepo.init(tmp_path / "code")
    (repo.path / "x.py").write_text("x = 1\n")
    sha = repo.commit(["x.py"], "m", author="dev")
    assert code_state(repo.path) == (sha, False)
    (repo.path / "y.py").write_text("y = 1\n")
    assert code_state(repo.path) == (sha, True)
```

Append to `tests/test_runner.py`:

```python
from aiswarm.config import RunSettings


async def test_seed_is_recorded_and_fixes_post_order(tmp_path, community, scripted):
    store, conn = community

    async def order(seed):
        runner = Runner(settings=RunSettings(max_rounds=1), conn=conn, store=store, runs_dir=tmp_path / "runs",
                        clients={a: scripted({1: propose}) for a in AGENTS})
        run_id = runner.start("G", AGENTS)
        assert conn.execute("SELECT seed FROM runs WHERE id = ?", (run_id,)).fetchone()[0] is not None
        conn.execute("UPDATE runs SET seed = ? WHERE id = ?", (seed, run_id))
        await runner.run(run_id)
        rows = conn.execute("SELECT author FROM entries WHERE run_id = ? AND type = 'propose' ORDER BY id", (run_id,))
        return [r["author"] for r in rows]

    assert await order(7) == await order(7)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_provenance.py tests/test_runner.py::test_seed_is_recorded_and_fixes_post_order -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'aiswarm.provenance'`.

- [ ] **Step 3: Implement `src/aiswarm/provenance.py`**

```python
"""Run provenance: what produced each run, so a harness change is never mistaken for emergence."""

import asyncio
import hashlib
import json
import sqlite3
import urllib.request
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any, Literal

import aiswarm
from aiswarm.config import Config, OpenAIBackend
from aiswarm.db import now_iso
from aiswarm.gitrepo import GitError, GitRepo

Phase = Literal["start", "resume", "postrun"]
Probe = Callable[[OpenAIBackend], Awaitable[dict[str, Any]]]
_PATH_KEYS = ("community_dir", "runs_dir")


@dataclass(frozen=True)
class Provenance:
    run_id: str
    phase: str
    code_commit: str | None
    code_dirty: bool | None
    config: dict[str, Any]
    prompts: dict[str, dict[str, str]]
    models: dict[str, dict[str, Any]]
    servers: dict[str, dict[str, Any]]
    seed: int | None
    fingerprint: str
    captured_at: str


def _without_keys(data: Any) -> Any:
    if isinstance(data, dict):
        return {k: _without_keys(v) for k, v in data.items() if k != "api_key"}
    if isinstance(data, list):
        return [_without_keys(v) for v in data]
    return data


def code_state(code_dir: Path | None = None) -> tuple[str | None, bool | None]:
    """The AISwarm git commit and whether the checkout has uncommitted changes; (None, None) outside git."""
    repo = GitRepo(code_dir or Path(aiswarm.__file__).parent)
    try:
        commit = repo.head()
        return (commit, repo.is_dirty()) if commit else (None, None)
    except (GitError, OSError):
        return None, None


def prompt_snapshot() -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for path in sorted(files("aiswarm.prompts").iterdir(), key=lambda p: p.name):
        if path.name.endswith(".md"):
            text = path.read_text()
            out[path.name] = {"sha256": hashlib.sha256(text.encode()).hexdigest(), "text": text}
    return out


async def probe_server(backend: OpenAIBackend) -> dict[str, Any]:
    """The server's model list and, where exposed (oMLX), its /health report. Never raises."""
    def get(url: str) -> Any:
        request = urllib.request.Request(url, headers={"Authorization": f"Bearer {backend.api_key}"})
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read())

    base = backend.base_url.rstrip("/")
    out: dict[str, Any] = {"models": None, "health": None, "error": None}
    try:
        listing = await asyncio.to_thread(get, base + "/models")
        out["models"] = sorted(item["id"] for item in listing.get("data", []))
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
        return out
    try:
        out["health"] = await asyncio.to_thread(get, base.removesuffix("/v1") + "/health")
    except Exception:
        pass  # oMLX serves /health; other OpenAI-compatible servers may not
    return out


def _fingerprint(code_commit: str | None, code_dirty: bool | None, config: dict[str, Any],
                 prompts: dict[str, dict[str, str]], models: dict[str, dict[str, Any]]) -> str:
    payload = {
        "code": code_commit, "dirty": code_dirty,
        "config": {k: v for k, v in config.items() if k not in _PATH_KEYS},
        "prompts": {name: p["sha256"] for name, p in prompts.items()},
        "models": models,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


async def capture(
    conn: sqlite3.Connection, run_id: str, phase: Phase, cfg: Config, agent_backends: Mapping[str, str],
    *, probe: Probe = probe_server, code_dir: Path | None = None,
) -> Provenance:
    """Record what is about to produce (or continue) a run. Never fails because a server is unreachable."""
    commit, dirty = code_state(code_dir)
    config = _without_keys(cfg.model_dump(mode="json"))
    prompts = prompt_snapshot()
    models = {agent: {"backend": name, **_without_keys(cfg.models[name].model_dump(mode="json"))}
              for agent, name in sorted(agent_backends.items())}
    used = set(agent_backends.values()) | {n for n in (cfg.report.narrator, cfg.library.embeddings) if n}
    servers: dict[str, dict[str, Any]] = {}
    for name in sorted(used):
        backend = cfg.models[name]
        if isinstance(backend, OpenAIBackend) and backend.base_url not in servers:
            try:
                servers[backend.base_url] = await probe(backend)
            except Exception as exc:
                servers[backend.base_url] = {"models": None, "health": None, "error": f"{type(exc).__name__}: {exc}"}
    row = conn.execute("SELECT seed FROM runs WHERE id = ?", (run_id,)).fetchone()
    prov = Provenance(
        run_id=run_id, phase=phase, code_commit=commit, code_dirty=dirty, config=config, prompts=prompts,
        models=models, servers=servers, seed=row["seed"] if row else None,
        fingerprint=_fingerprint(commit, dirty, config, prompts, models), captured_at=now_iso(),
    )
    conn.execute(
        "INSERT INTO run_provenance (run_id, phase, code_commit, code_dirty, config, prompts, models, servers, seed, "
        "fingerprint, captured_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (run_id, phase, commit, None if dirty is None else int(dirty), json.dumps(config), json.dumps(prompts),
         json.dumps(models), json.dumps(servers), prov.seed, prov.fingerprint, prov.captured_at),
    )
    return prov


def _row(row: sqlite3.Row) -> Provenance:
    return Provenance(
        run_id=row["run_id"], phase=row["phase"], code_commit=row["code_commit"],
        code_dirty=None if row["code_dirty"] is None else bool(row["code_dirty"]),
        config=json.loads(row["config"]), prompts=json.loads(row["prompts"]), models=json.loads(row["models"]),
        servers=json.loads(row["servers"]), seed=row["seed"], fingerprint=row["fingerprint"],
        captured_at=row["captured_at"],
    )


def provenance_rows(conn: sqlite3.Connection, run_id: str) -> list[Provenance]:
    return [_row(r) for r in conn.execute("SELECT * FROM run_provenance WHERE run_id = ? ORDER BY id", (run_id,))]
```

- [ ] **Step 4: Seed the post order in `src/aiswarm/runner.py`**

In `Runner.__init__` replace `self.rng = rng or random.Random()` with:

```python
        self.rng = rng  # tests inject one; otherwise each round is shuffled from the run's recorded seed
```

In `start`, generate and store the seed:

```python
        run_id = new_run_id()
        seed = secrets.randbits(32)
        (self.runs_dir / run_id).mkdir(parents=True)
        with transaction(self.conn):
            self.conn.execute(
                "INSERT INTO runs (id, goal, status, agent_ids, created_at, seed) VALUES (?, ?, 'running', ?, ?, ?)",
                (run_id, goal, json.dumps(list(agent_ids)), now_iso(), seed),
            )
```

In `run`, after `goal, agent_ids = ...` add `seed = row["seed"]`, and replace `self.rng.shuffle(order)` with:

```python
            rng = self.rng or random.Random(f"{seed}-{rnd}")  # per round, so a resumed run replays the same order
            rng.shuffle(order)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_provenance.py tests/test_runner.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/aiswarm/provenance.py src/aiswarm/runner.py tests/test_provenance.py tests/test_runner.py
git commit -m "feat: capture run provenance and seed each run's post order

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Provenance changes, CLI capture, and the `show` header

**Files:**
- Modify: `src/aiswarm/provenance.py` (add `provenance_changes`)
- Modify: `src/aiswarm/cli.py` (`run`, `postrun`, `show`)
- Test: `tests/test_provenance.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `capture`, `provenance_rows`, `probe_server` (Task 2).
- Produces: `provenance_changes(conn, run_id: str) -> list[str]` — human-readable lines, e.g. `"since run r1: prompts journal_request.md"`, `"at resume (2026-…): config run.max_rounds"`; empty when nothing changed or the run has no `start` row. `cli.probe_server` is a module-level name tests can monkeypatch.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_provenance.py`:

```python
from aiswarm.provenance import provenance_changes


async def test_changes_name_what_differs_from_the_previous_run(community, make_run, monkeypatch):
    store, conn = community
    make_run(conn, "r1", ("a",))
    make_run(conn, "r2", ("a",))
    await capture(conn, "r1", "start", make_config(max_rounds=12), {"a": "local"}, probe=fake_probe)
    assert provenance_changes(conn, "r1") == []
    changed = prompt_snapshot()
    changed["journal_request.md"] = {"sha256": "different", "text": "x"}
    monkeypatch.setattr(provenance, "prompt_snapshot", lambda: changed)
    await capture(conn, "r2", "start", make_config(max_rounds=4), {"a": "local", "b": "claude"}, probe=fake_probe)
    changes = provenance_changes(conn, "r2")
    assert "since run r1: prompts journal_request.md" in changes
    assert "since run r1: config run.max_rounds" in changes
    assert "since run r1: models for b" in changes


async def test_changes_flag_a_harness_change_within_a_run(community, make_run):
    store, conn = community
    make_run(conn, "r1", ("a",))
    await capture(conn, "r1", "start", make_config(max_rounds=12), {"a": "local"}, probe=fake_probe)
    await capture(conn, "r1", "resume", make_config(max_rounds=12), {"a": "local"}, probe=fake_probe)
    assert provenance_changes(conn, "r1") == []
    await capture(conn, "r1", "postrun", make_config(max_rounds=3), {"a": "local"}, probe=fake_probe)
    [line] = provenance_changes(conn, "r1")
    assert line.startswith("at postrun (") and line.endswith("config run.max_rounds")


def test_changes_for_a_run_without_provenance_are_empty(community, make_run):
    store, conn = community
    make_run(conn, "old", ("a",))
    assert provenance_changes(conn, "old") == []
```

In `tests/test_cli.py`, extend the `project` fixture (before `config = tmp_path / "aiswarm.toml"`):

```python
    async def fake_probe(backend):
        return {"models": [backend.model], "health": None, "error": None}

    monkeypatch.setattr(cli, "probe_server", fake_probe)
```

and append:

```python
def test_runs_record_provenance_and_show_reports_changes(project):
    tmp_path, config = project
    for agent_id in ("a", "b"):
        assert invoke("agents", "new", agent_id, "--model", "local", "--temperament", "T.", "-c", config).exit_code == 0
    first = invoke("run", "Pick a colour", "--max-rounds", "4", "-c", config)
    first_id = re.search(r"run (\S+) closed", first.output).group(1)
    second = invoke("run", "Pick a colour", "--max-rounds", "3", "-c", config)
    second_id = re.search(r"run (\S+) closed", second.output).group(1)
    from aiswarm.db import connect
    conn = connect(tmp_path / "community" / "community.db")
    phases = [r["phase"] for r in conn.execute("SELECT phase FROM run_provenance WHERE run_id = ? ORDER BY id", (first_id,))]
    assert phases == ["start", "postrun"]
    assert "Harness changed" not in invoke("show", first_id, "-c", config).output
    shown = invoke("show", second_id, "-c", config).output
    assert f"since run {first_id}: config run.max_rounds" in shown
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_provenance.py tests/test_cli.py -q`
Expected: FAIL — `ImportError: cannot import name 'provenance_changes'` (and the CLI fixture fails on the missing `cli.probe_server`).

- [ ] **Step 3: Implement `provenance_changes`** (append to `src/aiswarm/provenance.py`)

```python
def _flatten(data: Any, prefix: str = "") -> dict[str, Any]:
    if not isinstance(data, dict):
        return {prefix: data}
    out: dict[str, Any] = {}
    for key, value in data.items():
        out.update(_flatten(value, f"{prefix}.{key}" if prefix else key))
    return out


def _diff(old: Provenance, new: Provenance) -> list[str]:
    changes: list[str] = []
    if old.code_commit != new.code_commit:
        changes.append(f"code commit {(old.code_commit or 'none')[:8]} → {(new.code_commit or 'none')[:8]}")
    if old.code_dirty != new.code_dirty:
        changes.append("uncommitted code changes" if new.code_dirty else "code changes committed")
    prompts = sorted(name for name in old.prompts.keys() | new.prompts.keys()
                     if old.prompts.get(name, {}).get("sha256") != new.prompts.get(name, {}).get("sha256"))
    if prompts:
        changes.append("prompts " + ", ".join(prompts))
    before = _flatten({k: v for k, v in old.config.items() if k not in _PATH_KEYS})
    after = _flatten({k: v for k, v in new.config.items() if k not in _PATH_KEYS})
    keys = sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))
    if keys:
        changes.append("config " + ", ".join(keys))
    agents = sorted(a for a in old.models.keys() | new.models.keys() if old.models.get(a) != new.models.get(a))
    if agents:
        changes.append("models for " + ", ".join(agents))
    return changes


def provenance_changes(conn: sqlite3.Connection, run_id: str) -> list[str]:
    """How this run's harness differs from the previous run's, and any change during the run itself."""
    rows = provenance_rows(conn, run_id)
    start = next((r for r in rows if r.phase == "start"), None)
    if start is None:
        return []
    out: list[str] = []
    earlier = conn.execute(
        "SELECT id FROM runs WHERE rowid < (SELECT rowid FROM runs WHERE id = ?) ORDER BY rowid DESC", (run_id,)
    ).fetchall()
    for row in earlier:
        previous = next((r for r in provenance_rows(conn, row["id"]) if r.phase == "start"), None)
        if previous is not None:
            if previous.fingerprint != start.fingerprint:
                out += [f"since run {previous.run_id}: {c}" for c in _diff(previous, start)]
            break
    for later in rows:
        if later.phase != "start" and later.fingerprint != start.fingerprint:
            out += [f"at {later.phase} ({later.captured_at}): {c}" for c in _diff(start, later)]
    return out
```

- [ ] **Step 4: Wire the CLI** (`src/aiswarm/cli.py`)

Add the import: `from aiswarm.provenance import capture, probe_server, provenance_changes, provenance_rows`.

In `run`, after `clients = _clients(cfg, store, agent_ids)` add:

```python
    backends = {a: store.record(a).model for a in agent_ids}
```

In `run`'s `go()`, right after `run_id = resume or runner.start(goal or "", agent_ids)`:

```python
        await capture(conn, run_id, "resume" if resume else "start", cfg, backends, probe=probe_server)
```

and inside `if not no_postrun:` before `post = await PostRun(...)`:

```python
            await capture(conn, run_id, "postrun", cfg, backends, probe=probe_server)
```

In `postrun`, after `clients = _clients(...)` add `backends = {a: store.record(a).model for a in json.loads(row["agent_ids"])}` and at the top of its `go()`:

```python
        await capture(conn, run_id, "postrun", cfg, backends, probe=probe_server)
```

Replace `show` with:

```python
@app.command()
def show(run_id: str, config: ConfigOpt = DEFAULT_PATH) -> None:
    """Print a run's answer and minority report, noting any harness change since the previous run."""
    cfg = _load(config)
    report = cfg.runs_dir / run_id / "report"
    if not report.exists():
        _fail(f"no report for run {run_id!r}")
    conn = connect(cfg.db_path)
    changes = provenance_changes(conn, run_id)
    if changes:
        typer.echo("Harness changed:\n" + "\n".join(f"- {c}" for c in changes) + "\n")
    start = next((p for p in provenance_rows(conn, run_id) if p.phase == "start"), None)
    if start and start.code_dirty:
        typer.echo("Note: this run started from uncommitted AISwarm code.\n")
    for name in ("answer.md", "minority.md", "account.md", "narrative.md"):
        path = report / name
        if path.exists():
            typer.echo(path.read_text())
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest -q`
Expected: PASS (full suite; the CLI tests now all capture provenance through the fake probe).

- [ ] **Step 6: Commit**

```bash
git add src/aiswarm/provenance.py src/aiswarm/cli.py tests/test_provenance.py tests/test_cli.py
git commit -m "feat: record provenance at start, resume and post-run; show harness changes

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Control agent seed and reset

**Files:**
- Modify: `src/aiswarm/profiles.py`
- Modify: `src/aiswarm/introduce.py`
- Modify: `src/aiswarm/cli.py` (`agents new --control`, `agents list` marker)
- Test: `tests/test_profiles.py`, `tests/test_cli.py`

**Interfaces:**
- Produces: `SEED_DIR = "seed"`; `ProfileStore.create(..., control=True)` writes `agents/<id>/seed/temperament.md`; `ProfileStore.save_seed_public(agent_id) -> None`; `ProfileStore.has_seed_public(agent_id) -> bool`; `ProfileStore.reset_to_seed(agent_id) -> bool` (False when the seed name was taken and the current name was kept; raises `ProfileError` without a complete seed); `introduce()` saves the seed profile for control agents.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_profiles.py`)

```python
@pytest.fixture
def control_store(tmp_path):
    s = ProfileStore(tmp_path / "community")
    s.create("a", "local", "Curious and blunt.")
    s.set_public("a", "Ash", "Ash headline", "I am Ash.")
    s.create("e", "local", "Seed temperament.", control=True)
    s.set_public("e", "Eve", "Eve headline", "I am Eve.")
    s.save_seed_public("e")
    return s


def test_control_agents_keep_a_seed(control_store):
    seed = control_store.root / "agents" / "e" / "seed"
    assert (seed / "temperament.md").read_text() == "Seed temperament.\n"
    assert 'name = "Eve"' in (seed / "public.toml").read_text()
    assert control_store.has_seed_public("e") and not control_store.has_seed_public("a")
    assert not (control_store.root / "agents" / "a" / "seed").exists()


def test_reset_restores_the_seed_and_empties_notes(control_store):
    control_store.write_private("e", "temperament.md", "Changed.")
    control_store.write_private("e", "lessons.md", "Learned things.")
    control_store.set_public("e", "Evelyn", "New headline", "New intro.")
    assert control_store.reset_to_seed("e") is True
    private = control_store.private("e")
    assert private["temperament.md"] == "Seed temperament.\n"
    assert private["self.md"] == private["lessons.md"] == private["peer-notes.md"] == ""
    public = control_store.public("e")
    assert (public.name, public.headline, public.former_names) == ("Eve", "Eve headline", [])
    log = subprocess.run(["git", "log", "-1", "--format=%s"], cwd=control_store.root, capture_output=True, text=True)
    assert log.stdout.strip() == "reset control agent e to seed"


def test_reset_keeps_the_current_name_when_the_seed_name_is_taken(control_store):
    control_store.set_public("e", "Evelyn", "Eve headline", "I am Eve.")
    control_store.set_public("a", "Eve", "Ash headline", "I am Ash.")
    assert control_store.reset_to_seed("e") is False
    assert control_store.public("e").name == "Evelyn"


def test_reset_without_a_seed_profile_fails(tmp_path):
    s = ProfileStore(tmp_path / "community")
    s.create("e", "local", "Seed.", control=True)
    with pytest.raises(ProfileError, match="no seed profile"):
        s.reset_to_seed("e")


async def test_introduce_saves_the_seed_for_control_agents(tmp_path):
    s = ProfileStore(tmp_path / "community")
    s.create("e", "local", "Seed.", control=True)
    s.create("f", "local", "Seed.")
    reply = json.dumps({"name": "Eve", "headline": "H", "introduction": "Hi."})
    await introduce(s, "e", FakeModel([reply]))
    await introduce(s, "f", FakeModel([reply.replace("Eve", "Fay")]))
    assert s.has_seed_public("e") and not s.has_seed_public("f")
```

Append to `tests/test_cli.py`:

```python
def test_agents_new_control_creates_a_seeded_control_agent(project):
    tmp_path, config = project
    result = invoke("agents", "new", "e", "--model", "local", "--temperament", "T.", "--control", "-c", config)
    assert result.exit_code == 0, result.output
    assert "control = true" in (tmp_path / "community" / "agents" / "e" / "agent.toml").read_text()
    assert (tmp_path / "community" / "agents" / "e" / "seed" / "public.toml").exists()
    assert "control" in invoke("agents", "list", "-c", config).output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_profiles.py tests/test_cli.py -q`
Expected: FAIL — `AttributeError: 'ProfileStore' object has no attribute 'save_seed_public'`.

- [ ] **Step 3: Implement** in `src/aiswarm/profiles.py`

Add the constant near `PRIVATE_FILES`:

```python
SEED_DIR = "seed"  # a control agent's seed state; never read into any View
```

In `create`, after writing the empty private files and before the commit:

```python
        if control:
            (directory / SEED_DIR).mkdir()
            (directory / SEED_DIR / "temperament.md").write_text(temperament.strip() + "\n")
```

Add methods to `ProfileStore`:

```python
    def save_seed_public(self, agent_id: str) -> None:
        """Keep a control agent's first introduction as its seed profile."""
        target = self._dir(agent_id) / SEED_DIR / "public.toml"
        target.parent.mkdir(exist_ok=True)
        target.write_text((self._dir(agent_id) / "public.toml").read_text())
        self._repo.commit([f"agents/{agent_id}/{SEED_DIR}"], f"{agent_id} seed profile saved", author="aiswarm")

    def has_seed_public(self, agent_id: str) -> bool:
        return (self._dir(agent_id) / SEED_DIR / "public.toml").exists()

    def reset_to_seed(self, agent_id: str) -> bool:
        """Restore a control agent to its seed. Returns False if its seed name was taken and its current name kept."""
        directory = self._dir(agent_id)
        seed = directory / SEED_DIR
        if not (seed / "public.toml").exists() or not (seed / "temperament.md").exists():
            raise ProfileError(f"{agent_id} has no seed profile; run `aiswarm agents introduce {agent_id}`")
        profile = PublicProfile.model_validate(tomllib.loads((seed / "public.toml").read_text()))
        taken = any(
            (other_profile := self.public(other)) and other_profile.name.casefold() == profile.name.casefold()
            for other in self.ids() if other != agent_id
        )
        current = self.public(agent_id)
        if taken and current:
            profile = profile.model_copy(update={"name": current.name})
        (directory / "public.toml").write_text(_toml(profile.model_dump()))
        (directory / "temperament.md").write_text((seed / "temperament.md").read_text())
        for filename in PRIVATE_FILES[1:]:
            (directory / filename).write_text("")
        self._repo.commit([f"agents/{agent_id}"], f"reset control agent {agent_id} to seed", author="aiswarm")
        return not taken
```

In `src/aiswarm/introduce.py`, replace the `return store.set_public(...)` line with:

```python
            profile = store.set_public(agent_id, reply.name, reply.headline, reply.introduction, pronouns=reply.pronouns)
            if store.record(agent_id).control:
                store.save_seed_public(agent_id)
            return profile
```

In `src/aiswarm/cli.py`, add to `agents_new`'s parameters (before `config`):

```python
    control: Annotated[bool, typer.Option("--control", help="A control agent: reset to its seed before every run.")] = False,
```

pass it through as `store.create(agent_id, model, text, control=control)`, and in `agents_list` append a marker:

```python
        marker = "\tcontrol" if store.record(agent_id).control else ""
        typer.echo(f"{agent_id}\t{name}\t{store.record(agent_id).model}\t{headline}{marker}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_profiles.py tests/test_cli.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aiswarm/profiles.py src/aiswarm/introduce.py src/aiswarm/cli.py tests/test_profiles.py tests/test_cli.py
git commit -m "feat: create control agents with a seed state they can be reset to

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Reset control agents before each run; no earlier journals

**Files:**
- Modify: `src/aiswarm/runner.py` (`start`)
- Modify: `src/aiswarm/view.py` (`collect_inputs`)
- Modify: `src/aiswarm/cli.py` (`run`: report `ValueError` from `start`)
- Test: `tests/test_runner.py`, `tests/test_view.py`, `tests/test_postrun.py`

**Interfaces:**
- Consumes: `has_seed_public`, `reset_to_seed`, `save_seed_public` (Task 4).
- Produces: `Runner.start` raises `ValueError` naming `aiswarm agents introduce <id>` for a control agent without a seed profile (before any run row is written); emits `control_reset` (and `control_reset_name_kept`) events after `run_started`. `collect_inputs` gives control agents no earlier journals.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_runner.py`:

```python
def add_control(store, agent_id="d", name="Dee"):
    store.create(agent_id, "fake", "Seed temperament.", control=True)
    store.set_public(agent_id, name, f"{name} headline", f"I am {name}.")
    store.save_seed_public(agent_id)


async def test_start_resets_control_agents_but_resume_does_not(tmp_path, community, scripted, runner_factory):
    store, conn = community
    add_control(store)
    store.write_private("d", "lessons.md", "LEARNED")
    agents = [*AGENTS, "d"]
    runner = runner_factory(store, conn, {a: scripted({}) for a in agents}, max_rounds=1)
    run_id = runner.start("G", agents)
    assert store.private("d")["lessons.md"] == ""
    store.write_private("d", "lessons.md", "AFTER-START")
    await runner.run(run_id)
    assert store.private("d")["lessons.md"] == "AFTER-START"
    names = [e["event"] for e in read_events(tmp_path / "runs" / run_id / "events.jsonl")]
    assert names[:2] == ["run_started", "control_reset"]


def test_start_refuses_a_control_agent_without_a_seed_profile(community, scripted, runner_factory):
    store, conn = community
    store.create("d", "fake", "Seed.", control=True)
    store.set_public("d", "Dee", "H", "I am Dee.")
    runner = runner_factory(store, conn, {})
    with pytest.raises(ValueError, match="agents introduce d"):
        runner.start("G", ["a", "d"])
    assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
```

Append to `tests/test_view.py`:

```python
def test_control_agents_see_no_earlier_journals_and_never_their_status(tmp_path, community, make_run):
    store, conn, board, ws = setup(tmp_path, community, make_run)
    store.create("d", "fake", "Seed.", control=True)
    store.set_public("d", "Dee", "Dee headline", "I am Dee.")
    store.write_journal_file("d", "20260101-000000-aaaa", "JOURNAL-D-OLD\n")
    store.write_journal_file("a", "20260101-000000-aaaa", "JOURNAL-A-OLD\n")
    view = view_for("d", 2, store, board, ws)
    assert "JOURNAL-D-OLD" not in view and "control" not in view.lower()
    assert "JOURNAL-A-OLD" in view_for("a", 2, store, board, ws)
```

Append to `tests/test_postrun.py`:

```python
async def test_postrun_requests_never_include_earlier_journals(tmp_path, community, make_run, postrun_reply):
    store, conn = community
    store.write_journal_file("c", "20200101-000000-0000", "EARLIER-JOURNAL\n")
    run_id = finished_run(conn, tmp_path, make_run)
    clients = {a: FakeModel(responder(postrun_reply)) for a in "abc"}
    await cycle(store, conn, tmp_path, clients).run(run_id)
    sent = json.dumps([call[:2] for client in clients.values() for call in client.calls])
    assert "EARLIER-JOURNAL" not in sent
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_runner.py tests/test_view.py tests/test_postrun.py -q`
Expected: the runner and view tests FAIL (lessons not reset; old journal shown). The post-run test is expected to PASS already — it pins current behaviour the control agent relies on.

- [ ] **Step 3: Implement**

In `Runner.start` (`src/aiswarm/runner.py`), after the `unknown` check:

```python
        controls = [a for a in agent_ids if self.store.record(a).control]
        for agent_id in controls:
            if not self.store.has_seed_public(agent_id):
                raise ValueError(f"control agent {agent_id} has no seed profile; run `aiswarm agents introduce {agent_id}`")
```

and replace the final emit with:

```python
        events = self._events(run_id)
        events.emit("run_started", run_id=run_id, goal=goal, agents=list(agent_ids))
        for agent_id in controls:  # control agents carry no experience between runs
            name_restored = self.store.reset_to_seed(agent_id)
            events.emit("control_reset", agent=agent_id)
            if not name_restored:
                events.emit("control_reset_name_kept", agent=agent_id)
        return run_id
```

In `collect_inputs` (`src/aiswarm/view.py`), replace the `journals=` argument with:

```python
        journals=() if store.record(agent_id).control else tuple(store.journals(agent_id, 2)),
```

In `cli.py` `run`'s `go()`, replace `run_id = resume or runner.start(goal or "", agent_ids)` with:

```python
        try:
            run_id = resume or runner.start(goal or "", agent_ids)
        except ValueError as exc:
            _fail(str(exc))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aiswarm/runner.py src/aiswarm/view.py src/aiswarm/cli.py tests/test_runner.py tests/test_view.py tests/test_postrun.py
git commit -m "feat: reset control agents to their seed before each run and hide their earlier journals

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: The goal queue file

**Files:**
- Create: `src/aiswarm/goals.py`
- Modify: `src/aiswarm/profiles.py` (`write_community_file` gains `author`)
- Test: `tests/test_goals.py` (new)

**Interfaces:**
- Produces: `GOALS_FILE = "goals.toml"`, `BENCHMARK_ID = "benchmark"`, `class GoalError(ValueError)`, pydantic `Benchmark(text, category, every=5)`, `Goal(id, text, category)`, `GoalFile(benchmark, goals)`, `DEFAULT_GOALS: GoalFile`, `render_goals(gf) -> str`, `load_goals(path: Path) -> GoalFile`, `seed_goals(store) -> bool`, `add_goal(store, text: str, category: str, goal_id: str | None = None) -> Goal`; `ProfileStore.write_community_file(filename, content, message, author="community")`.

- [ ] **Step 1: Write the failing tests** — create `tests/test_goals.py`:

```python
import subprocess
import tomllib

import pytest

from aiswarm.goals import (
    DEFAULT_GOALS, GOALS_FILE, Benchmark, Goal, GoalError, GoalFile, add_goal, load_goals, render_goals, seed_goals,
)
from aiswarm.profiles import ProfileStore


@pytest.fixture
def store(tmp_path):
    return ProfileStore(tmp_path / "community")


def test_default_goals_are_frivolous_and_tagged():
    assert DEFAULT_GOALS.benchmark == Benchmark(
        text="Pick one pizza topping combination the whole group will share.", category="trade-off", every=5)
    assert DEFAULT_GOALS.goals[0] == Goal(id="best-fruit", text="Come to an agreement on the best fruit.",
                                          category="contested")
    assert len(DEFAULT_GOALS.goals) == 10
    assert {g.category for g in DEFAULT_GOALS.goals} == {"neutral", "trade-off", "contested"}


def test_render_round_trips_the_defaults():
    assert GoalFile.model_validate(tomllib.loads(render_goals(DEFAULT_GOALS))) == DEFAULT_GOALS


def test_render_round_trips_quotes_and_unicode():
    gf = GoalFile(benchmark=DEFAULT_GOALS.benchmark,
                  goals=[Goal(id="pastry", text='Is "pain au chocolat" better than a croissant? 🥐', category="contested")])
    assert GoalFile.model_validate(tomllib.loads(render_goals(gf))) == gf


@pytest.mark.parametrize("content, problem", [
    ("[benchmark\n", "goals.toml"),
    ('[benchmark]\ntext = "x"\ncategory = "silly"\n', "category"),
    ('[benchmark]\ntext = "x"\ncategory = "neutral"\n[[goals]]\nid = "a"\ntext = "y"\ncategory = "neutral"\n'
     '[[goals]]\nid = "a"\ntext = "z"\ncategory = "neutral"\n', "duplicate goal id"),
    ('[benchmark]\ntext = "x"\ncategory = "neutral"\n[[goals]]\nid = "benchmark"\ntext = "y"\ncategory = "neutral"\n',
     "reserved"),
])
def test_load_goals_reports_problems(tmp_path, content, problem):
    path = tmp_path / GOALS_FILE
    path.write_text(content)
    with pytest.raises(GoalError, match=problem):
        load_goals(path)


def test_load_goals_reports_a_missing_file(tmp_path):
    with pytest.raises(GoalError, match="aiswarm init"):
        load_goals(tmp_path / GOALS_FILE)


def test_seed_goals_writes_only_when_missing(store):
    assert seed_goals(store) is True
    assert load_goals(store.root / GOALS_FILE) == DEFAULT_GOALS
    (store.root / GOALS_FILE).write_text(render_goals(GoalFile(benchmark=DEFAULT_GOALS.benchmark)))
    assert seed_goals(store) is False
    assert load_goals(store.root / GOALS_FILE).goals == []


def test_add_goal_derives_unique_ids_and_commits(store):
    seed_goals(store)
    first = add_goal(store, "Rank the best sandwiches!", "contested")
    second = add_goal(store, "Rank the best sandwiches!", "neutral")
    assert (first.id, second.id) == ("rank-the-best-sandwiches", "rank-the-best-sandwiches-2")
    assert load_goals(store.root / GOALS_FILE).goals[-1] == second
    log = subprocess.run(["git", "log", "-1", "--format=%an|%s"], cwd=store.root, capture_output=True, text=True)
    assert log.stdout.strip() == "aiswarm|add goal rank-the-best-sandwiches-2"


def test_add_goal_rejects_bad_input(store):
    seed_goals(store)
    with pytest.raises(GoalError, match="category"):
        add_goal(store, "Fine text.", "serious")
    with pytest.raises(GoalError, match="duplicate goal id"):
        add_goal(store, "Again.", "neutral", goal_id="best-fruit")
    with pytest.raises(GoalError, match="single line"):
        add_goal(store, "two\nlines", "neutral")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_goals.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'aiswarm.goals'`.

- [ ] **Step 3: Implement**

In `src/aiswarm/profiles.py` change `write_community_file`:

```python
    def write_community_file(self, filename: str, content: str, message: str, author: str = "community") -> None:
        (self.root / filename).write_text(content)
        self._repo.commit([filename], message, author=author)
```

Create `src/aiswarm/goals.py`:

```python
"""The goal queue (community/goals.toml). Categories are for the Observer only; no agent ever sees this file."""

import json
import re
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from aiswarm.actions import summarise_validation
from aiswarm.profiles import ProfileStore

GOALS_FILE = "goals.toml"
BENCHMARK_ID = "benchmark"
GOAL_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
TEXT_MAX = 500
Category = Literal["neutral", "trade-off", "contested"]


class GoalError(ValueError):
    pass


def _text(value: str) -> str:
    value = value.strip()
    if not 1 <= len(value) <= TEXT_MAX or "\n" in value:
        raise ValueError(f"goal text must be a single line of 1-{TEXT_MAX} characters")
    return value


class Benchmark(BaseModel):
    text: str
    category: Category
    every: int = Field(5, ge=1)

    @field_validator("text")
    @classmethod
    def _text(cls, value: str) -> str:
        return _text(value)


class Goal(BaseModel):
    id: str
    text: str
    category: Category

    @field_validator("id")
    @classmethod
    def _id(cls, value: str) -> str:
        if value == BENCHMARK_ID:
            raise ValueError(f"the id {BENCHMARK_ID!r} is reserved")
        if not GOAL_ID.match(value):
            raise ValueError("goal id must be lowercase letters, digits or '-', max 48")
        return value

    @field_validator("text")
    @classmethod
    def _text(cls, value: str) -> str:
        return _text(value)


class GoalFile(BaseModel):
    benchmark: Benchmark
    goals: list[Goal] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique(self) -> "GoalFile":
        seen: set[str] = set()
        for goal in self.goals:
            if goal.id in seen:
                raise ValueError(f"duplicate goal id {goal.id!r}")
            seen.add(goal.id)
        return self


DEFAULT_GOALS = GoalFile(
    benchmark=Benchmark(text="Pick one pizza topping combination the whole group will share.", category="trade-off"),
    goals=[
        Goal(id="best-fruit", text="Come to an agreement on the best fruit.", category="contested"),
        Goal(id="hot-dog-sandwich", text="Decide, once and for all, whether a hot dog is a sandwich.",
             category="contested"),
        Goal(id="greatest-board-game", text="Settle on the greatest board game of all time.", category="contested"),
        Goal(id="dishwasher", text="Agree on the correct way to load a dishwasher.", category="contested"),
        Goal(id="picnic-basket", text="Plan a picnic menu that has to fit in a single basket.", category="trade-off"),
        Goal(id="road-trip-playlist",
             text="Choose three songs for a road-trip playlist that everyone in the car must listen to.",
             category="trade-off"),
        Goal(id="twenty-dollar-sunday", text="Design the ideal Sunday with only $20 to spend.", category="trade-off"),
        Goal(id="new-holiday", text="Invent a new holiday and describe how it is celebrated.", category="neutral"),
        Goal(id="monday-cat-limerick", text="Write a limerick about a cat who hates Mondays.", category="neutral"),
        Goal(id="office-goldfish", text="Name the office goldfish.", category="neutral"),
    ],
)


def _q(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)  # JSON strings are valid TOML basic strings


def render_goals(gf: GoalFile) -> str:
    b = gf.benchmark
    lines = [
        "# AISwarm goal queue. Categories are for the Observer only; no agent ever sees this file.",
        "", "[benchmark]", f"text = {_q(b.text)}", f"category = {_q(b.category)}", f"every = {b.every}",
    ]
    for goal in gf.goals:
        lines += ["", "[[goals]]", f"id = {_q(goal.id)}", f"text = {_q(goal.text)}", f"category = {_q(goal.category)}"]
    return "\n".join(lines) + "\n"


def load_goals(path: Path) -> GoalFile:
    if not path.exists():
        raise GoalError(f"{path} not found; run `aiswarm init`")
    try:
        return GoalFile.model_validate(tomllib.loads(path.read_text()))
    except tomllib.TOMLDecodeError as exc:
        raise GoalError(f"{path}: {exc}") from exc
    except ValidationError as exc:
        raise GoalError(f"{path}: {summarise_validation(exc)}") from exc


def seed_goals(store: ProfileStore) -> bool:
    """Write the starter queue if the community has none. Returns True if it wrote one."""
    if (store.root / GOALS_FILE).exists():
        return False
    store.write_community_file(GOALS_FILE, render_goals(DEFAULT_GOALS), "seed goal queue", author="aiswarm")
    return True


def _slug(text: str, taken: set[str]) -> str:
    base = "-".join(re.findall(r"[a-z0-9]+", text.lower())[:5])[:40].strip("-") or "goal"
    if base == BENCHMARK_ID:
        base = "benchmark-goal"
    candidate, n = base, 2
    while candidate in taken:
        candidate, n = f"{base}-{n}", n + 1
    return candidate


def add_goal(store: ProfileStore, text: str, category: str, goal_id: str | None = None) -> Goal:
    gf = load_goals(store.root / GOALS_FILE)
    try:
        goal = Goal(id=goal_id or _slug(text, {g.id for g in gf.goals}), text=text, category=category)
        updated = GoalFile(benchmark=gf.benchmark, goals=[*gf.goals, goal])
    except ValidationError as exc:
        raise GoalError(summarise_validation(exc)) from exc
    store.write_community_file(GOALS_FILE, render_goals(updated), f"add goal {goal.id}", author="aiswarm")
    return goal
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_goals.py -q`
Expected: PASS. If a `match=` fails because pydantic phrases a message differently (e.g. the category literal error reads `Input should be 'neutral', 'trade-off' or 'contested'` with loc `category`), keep the test's intent and match on the field name `category`, which `summarise_validation` always includes.

- [ ] **Step 5: Commit**

```bash
git add src/aiswarm/goals.py src/aiswarm/profiles.py tests/test_goals.py
git commit -m "feat: add the goal queue file with a frivolous starter set and a pizza benchmark

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Goal selection, `run --next`, and `goals list|add`

**Files:**
- Modify: `src/aiswarm/goals.py` (scheduling)
- Modify: `src/aiswarm/runner.py` (`start` goal metadata)
- Modify: `src/aiswarm/cli.py` (`init`, `run --next`, `goals` sub-app)
- Test: `tests/test_goals.py`, `tests/test_runner.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: Task 6 API; `runs.goal_id/goal_category/is_benchmark` (Task 1).
- Produces: `@dataclass(frozen=True) class Selected: id: str; text: str; category: str; is_benchmark: bool`; `runs_since_benchmark(conn) -> int | None`; `benchmark_due(conn, every: int) -> bool`; `used_goals(conn) -> dict[str, str]` (goal id → first run id); `next_goal(conn, gf: GoalFile) -> Selected` (raises `GoalError` when empty); `Runner.start(goal, agent_ids, *, goal_id=None, goal_category=None, is_benchmark=False)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_goals.py`:

```python
from aiswarm.db import connect
from aiswarm.goals import BENCHMARK_ID, benchmark_due, next_goal, runs_since_benchmark, used_goals


@pytest.fixture
def conn(tmp_path):
    return connect(tmp_path / "c.db")


def add_run(conn, run_id, goal_id=None, benchmark=False):
    conn.execute(
        "INSERT INTO runs (id, goal, status, agent_ids, created_at, goal_id, is_benchmark) "
        "VALUES (?, 'g', 'closed', '[]', 't', ?, ?)", (run_id, goal_id, int(benchmark)))


def test_benchmark_is_due_when_never_run_even_after_ad_hoc_runs(conn):
    for n in range(5):
        add_run(conn, f"r{n}")
    assert runs_since_benchmark(conn) is None
    assert next_goal(conn, DEFAULT_GOALS).is_benchmark


def test_benchmark_recurs_every_n_runs_counting_ad_hoc_runs(conn):
    add_run(conn, "b1", BENCHMARK_ID, benchmark=True)
    assert not benchmark_due(conn, 5)
    add_run(conn, "r1", "best-fruit")
    add_run(conn, "r2")                      # ad hoc
    add_run(conn, "r3", "hot-dog-sandwich")
    assert runs_since_benchmark(conn) == 3 and not benchmark_due(conn, 5)
    add_run(conn, "r4", "greatest-board-game")
    assert benchmark_due(conn, 5)
    assert next_goal(conn, DEFAULT_GOALS) == next_goal(conn, DEFAULT_GOALS)  # pure: selecting does not consume
    assert next_goal(conn, DEFAULT_GOALS).id == BENCHMARK_ID


def test_next_goal_takes_the_first_unused_queue_goal(conn):
    add_run(conn, "b1", BENCHMARK_ID, benchmark=True)
    add_run(conn, "r1", "best-fruit")
    selected = next_goal(conn, DEFAULT_GOALS)
    assert (selected.id, selected.category, selected.is_benchmark) == ("hot-dog-sandwich", "contested", False)
    assert used_goals(conn) == {"best-fruit": "r1"}


def test_next_goal_fails_when_the_queue_is_used_up(conn):
    gf = GoalFile(benchmark=Benchmark(text="B.", category="neutral", every=100),
                  goals=[Goal(id="only", text="Only.", category="neutral")])
    add_run(conn, "b1", BENCHMARK_ID, benchmark=True)
    add_run(conn, "r1", "only")
    with pytest.raises(GoalError, match="goals add"):
        next_goal(conn, gf)
```

Append to `tests/test_runner.py`:

```python
async def test_start_records_goal_metadata_and_never_shows_it_to_agents(community, scripted, runner_factory):
    store, conn = community
    clients = {a: scripted({1: propose}) for a in AGENTS}
    runner = runner_factory(store, conn, clients, max_rounds=2)
    run_id = runner.start("Pick one pizza.", AGENTS, goal_id="benchmark", goal_category="trade-off", is_benchmark=True)
    await runner.run(run_id)
    row = conn.execute("SELECT goal_id, goal_category, is_benchmark FROM runs WHERE id = ?", (run_id,)).fetchone()
    assert tuple(row) == ("benchmark", "trade-off", 1)
    sent = json.dumps([call[:2] for client in clients.values() for call in client.calls])
    assert "trade-off" not in sent and "benchmark" not in sent.lower()
```

Append to `tests/test_cli.py`:

```python
def test_init_seeds_the_goal_queue(project):
    tmp_path, config = project
    assert "best-fruit" in (tmp_path / "community" / "goals.toml").read_text()


def test_run_next_takes_the_benchmark_then_the_queue(project):
    tmp_path, config = project
    for agent_id in ("a", "b"):
        assert invoke("agents", "new", agent_id, "--model", "local", "--temperament", "T.", "-c", config).exit_code == 0
    first = invoke("run", "--next", "--max-rounds", "2", "--no-postrun", "-c", config)
    assert first.exit_code == 0, first.output
    assert "goal (benchmark, trade-off): Pick one pizza topping combination" in first.output
    second = invoke("run", "--next", "--max-rounds", "2", "--no-postrun", "-c", config)
    assert "goal (best-fruit, contested): Come to an agreement on the best fruit." in second.output
    listing = invoke("goals", "list", "-c", config).output
    assert "best-fruit\tcontested\tused by" in listing and "next: hot-dog-sandwich" in listing
    assert "due after 3 more runs" in listing


def test_run_rejects_a_goal_together_with_next(project):
    tmp_path, config = project
    result = invoke("run", "Pick a colour", "--next", "-c", config)
    assert result.exit_code != 0 and "not both" in result.output


def test_goals_add(project):
    tmp_path, config = project
    result = invoke("goals", "add", "Rank the best sandwiches.", "--category", "contested", "-c", config)
    assert result.exit_code == 0, result.output
    assert "added rank-the-best-sandwiches (contested)" in result.output
    bad = invoke("goals", "add", "Fine.", "--category", "serious", "-c", config)
    assert bad.exit_code != 0 and "category" in bad.output


def test_goals_list_reports_a_broken_file(project):
    tmp_path, config = project
    (tmp_path / "community" / "goals.toml").write_text("[benchmark\n")
    result = invoke("goals", "list", "-c", config)
    assert result.exit_code != 0 and "goals.toml" in result.output and "Traceback" not in result.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_goals.py tests/test_runner.py tests/test_cli.py -q`
Expected: FAIL — `ImportError: cannot import name 'benchmark_due'`.

- [ ] **Step 3: Implement scheduling** (append to `src/aiswarm/goals.py`; add `import sqlite3` and `from dataclasses import dataclass` to its imports)

```python
@dataclass(frozen=True)
class Selected:
    id: str
    text: str
    category: str
    is_benchmark: bool


def _last_benchmark_rowid(conn: sqlite3.Connection) -> int | None:
    return conn.execute("SELECT MAX(rowid) FROM runs WHERE is_benchmark = 1").fetchone()[0]


def runs_since_benchmark(conn: sqlite3.Connection) -> int | None:
    """Runs of any kind started since the last benchmark run; None if it has never run."""
    last = _last_benchmark_rowid(conn)
    if last is None:
        return None
    return conn.execute("SELECT COUNT(*) FROM runs WHERE rowid > ?", (last,)).fetchone()[0]


def benchmark_due(conn: sqlite3.Connection, every: int) -> bool:
    since = runs_since_benchmark(conn)
    return since is None or since >= every - 1


def used_goals(conn: sqlite3.Connection) -> dict[str, str]:
    """Queue goal id -> the first run that used it."""
    used: dict[str, str] = {}
    for row in conn.execute("SELECT goal_id, id FROM runs WHERE goal_id IS NOT NULL AND is_benchmark = 0 ORDER BY rowid"):
        used.setdefault(row["goal_id"], row["id"])
    return used


def next_goal(conn: sqlite3.Connection, gf: GoalFile) -> Selected:
    """The benchmark when due, else the first unused queue goal. Selecting does not consume a goal; starting a run does."""
    if benchmark_due(conn, gf.benchmark.every):
        return Selected(BENCHMARK_ID, gf.benchmark.text, gf.benchmark.category, True)
    used = used_goals(conn)
    for goal in gf.goals:
        if goal.id not in used:
            return Selected(goal.id, goal.text, goal.category, False)
    raise GoalError("goal queue is empty; add goals with `aiswarm goals add`")
```

- [ ] **Step 4: Record goal metadata in `Runner.start`** (`src/aiswarm/runner.py`)

Change the signature and insert:

```python
    def start(
        self, goal: str, agent_ids: Sequence[str], *, goal_id: str | None = None,
        goal_category: str | None = None, is_benchmark: bool = False,
    ) -> str:
```

```python
            self.conn.execute(
                "INSERT INTO runs (id, goal, status, agent_ids, created_at, seed, goal_id, goal_category, is_benchmark) "
                "VALUES (?, ?, 'running', ?, ?, ?, ?, ?, ?)",
                (run_id, goal, json.dumps(list(agent_ids)), now_iso(), seed, goal_id, goal_category, int(is_benchmark)),
            )
```

- [ ] **Step 5: Wire the CLI** (`src/aiswarm/cli.py`)

Imports: `from aiswarm.goals import GOALS_FILE, GoalError, add_goal, load_goals, next_goal, runs_since_benchmark, seed_goals, used_goals`.

Register the sub-app next to `agents_app`:

```python
goals_app = typer.Typer(no_args_is_help=True, help="Manage the goal queue (categories are never shown to agents).")
app.add_typer(goals_app, name="goals")
```

In `init`, replace `ProfileStore(cfg.community_dir)` with:

```python
    store = ProfileStore(cfg.community_dir)
    if seed_goals(store):
        typer.echo(f"wrote {cfg.community_dir / GOALS_FILE} — the goal queue for `aiswarm run --next`")
```

In `run`, add the option (after `goal`):

```python
    next_: Annotated[bool, typer.Option("--next", help="Take the next goal from the queue (or the benchmark when due).")] = False,
```

and replace the `else:` branch that handles a new run with:

```python
    else:
        selected = None
        if next_ and goal:
            _fail("give a goal or --next, not both")
        if next_:
            try:
                selected = next_goal(conn, load_goals(cfg.community_dir / GOALS_FILE))
            except GoalError as exc:
                _fail(str(exc))
            goal = selected.text
            typer.echo(f"goal ({selected.id}, {selected.category}): {goal}")
        if not goal:
            _fail("give a goal, --next, or --resume RUN_ID")
        agent_ids = agent or store.ids()
        if not agent_ids:
            _fail("no agents yet; create some with `aiswarm agents new`")
```

Declare `selected = None` before `if resume:` too (so it exists on the resume path), and in `go()` pass the metadata to `start`:

```python
            run_id = resume or runner.start(
                goal or "", agent_ids,
                goal_id=selected.id if selected else None,
                goal_category=selected.category if selected else None,
                is_benchmark=bool(selected and selected.is_benchmark),
            )
```

Add the `goals` commands:

```python
@goals_app.command("list")
def goals_list(config: ConfigOpt = DEFAULT_PATH) -> None:
    """Show the benchmark, the queue (used and unused), and what `run --next` would take."""
    cfg = _load(config)
    conn = connect(cfg.db_path)
    try:
        gf = load_goals(cfg.community_dir / GOALS_FILE)
    except GoalError as exc:
        _fail(str(exc))
    b = gf.benchmark
    since = runs_since_benchmark(conn)
    if since is None:
        status = "never run; due now"
    else:
        last = conn.execute("SELECT id FROM runs WHERE is_benchmark = 1 ORDER BY rowid DESC LIMIT 1").fetchone()["id"]
        left = b.every - 1 - since
        status = f"last run {last}; " + ("due now" if left <= 0 else f"due after {left} more run{'s' if left != 1 else ''}")
    typer.echo(f"benchmark ({b.category}, every {b.every}): {b.text}\n  {status}\n")
    used = used_goals(conn)
    for g in gf.goals:
        mark = f"used by {used[g.id]}" if g.id in used else "unused"
        typer.echo(f"{g.id}\t{g.category}\t{mark}\t{g.text}")
    try:
        typer.echo(f"\nnext: {next_goal(conn, gf).id}")
    except GoalError as exc:
        typer.echo(f"\nnext: none ({exc})")


@goals_app.command("add")
def goals_add(
    text: str,
    category: Annotated[str, typer.Option(help="neutral, trade-off, or contested (Observer-only).")],
    goal_id: Annotated[str | None, typer.Option("--id", help="Goal id (default: derived from the text).")] = None,
    config: ConfigOpt = DEFAULT_PATH,
) -> None:
    """Append a goal to the queue."""
    cfg = _load(config)
    try:
        goal = add_goal(ProfileStore(cfg.community_dir), text, category, goal_id)
    except GoalError as exc:
        _fail(str(exc))
    typer.echo(f"added {goal.id} ({goal.category}): {goal.text}")
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/aiswarm/goals.py src/aiswarm/runner.py src/aiswarm/cli.py tests/test_goals.py tests/test_runner.py tests/test_cli.py
git commit -m "feat: run the next queued goal or the benchmark when due; goals list and add

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Agent timeline

**Files:**
- Create: `src/aiswarm/timeline.py`
- Modify: `src/aiswarm/reviews.py` (`reviews_by`), `src/aiswarm/profiles.py` (`history`), `src/aiswarm/cli.py` (`agents timeline`)
- Modify: `docs/superpowers/specs/2026-10-10-aiswarm-experiment-harness-design.md` §6 (placement wording)
- Test: `tests/test_timeline.py` (new), `tests/test_cli.py`

**Interfaces:**
- Consumes: `GitRepo.log` / `Commit` (Task 1), `SEED_DIR` (Task 4), `reviews_of`, `responses_for`, `ratings_for`, `render_review`, `make_who`.
- Produces: `reviews_by(conn, agent_id) -> list[Review]`; `ProfileStore.history(agent_id) -> list[Commit]` (excludes `journal/` and `seed/`); `resolve_agent(store, key: str) -> str | None`; `timeline(conn, store, agent_id: str, since: str | None = None) -> str` (raises `KeyError(since)` for an unknown run).

Placement rule: database items carry their run id. A git commit goes under the latest run whose `created_at` is at or before the commit time; commits before every run go under "Before any run".

- [ ] **Step 1: Write the failing tests** — create `tests/test_timeline.py`:

```python
import json
from datetime import UTC, datetime, timedelta

import pytest

from aiswarm.board import SYSTEM, Board
from aiswarm.journal import write_journal
from aiswarm.reviews import add_response, add_review
from aiswarm.timeline import resolve_agent, timeline


def iso(delta_hours):
    return (datetime.now(UTC) + timedelta(hours=delta_hours)).isoformat(timespec="seconds")


def add_run(conn, run_id, goal, created_at):
    conn.execute("INSERT INTO runs (id, goal, status, agent_ids, created_at) VALUES (?, ?, 'closed', '[\"a\",\"b\"]', ?)",
                 (run_id, goal, created_at))


@pytest.fixture
def history(community):
    """Profile commits happen now (before any run); r1 starts in 1h, r2 in 2h."""
    store, conn = community
    store.set_public("a", "Ari", "Alpha headline", "I am Alpha.")  # rename Alpha -> Ari, before any run
    add_run(conn, "r1", "Best fruit?", iso(1))
    add_run(conn, "r2", "Best pizza?", iso(2))
    write_journal(conn, store, "a", "r1", "before", "JOURNAL-TEXT")
    board = Board(conn, "r1")
    proposal = board.append(1, "a", "propose", {"answer_summary": "Mango", "method": "Taste"})
    conn.execute("INSERT INTO traces (entry_id, run_id, agent_id, model, reasoning) VALUES (?, 'r1', 'a', 'm', ?)",
                 (proposal.id, "Because " + "mango " * 100))
    call = board.append(2, "a", "call_consensus", {"synthesis": "Mango", "refs": [proposal.id],
                                                   "credits": [{"agent_id": "b", "contribution": "tasting"}]})
    board.append(2, SYSTEM, "run_closed", {"reason": "consensus", "call_id": call.id})
    review = add_review(conn, run_id="r1", reviewer="b", reviewee="a", more_of="More.", less_of="Less.",
                        private_note="PRIVATE-NOTE", evidence=[proposal.id], overall=4, overall_reason="Good.")
    add_response(conn, review.id, "accept", "Fair.")
    conn.execute("INSERT INTO norm_proposals (run_id, author, kind, text, created_at) VALUES ('r2', 'a', 'norm', "
                 "'Cite your sources.', ?)", (iso(2),))
    return store, conn


def test_resolve_agent_by_id_name_and_former_name(history):
    store, conn = history
    assert resolve_agent(store, "a") == "a"
    assert resolve_agent(store, "ARI") == "a"
    assert resolve_agent(store, "alpha") == "a"
    assert resolve_agent(store, "nobody") is None


def test_timeline_groups_everything_under_runs(history):
    store, conn = history
    text = timeline(conn, store, "a")
    before, rest = text.split("## r1 — Best fruit?")
    r1, r2 = rest.split("## r2 — Best pizza?")
    assert text.startswith("# Timeline: Ari (a)\nformerly: Alpha")
    assert "## Before any run" in before and "create agent a" in before and "renamed Alpha → Ari" in before
    assert "JOURNAL-TEXT" in r1
    assert "review received from Bea (b)" in r1 and "PRIVATE-NOTE" in r1 and "response: accept" in r1
    assert "credits given in the closing call" in r1 and "- Bea (b): tasting" in r1
    assert "reasoning behind #" in r1 and "…" in r1
    assert "proposed a norm" in r2 and "Cite your sources." in r2


def test_timeline_shows_credits_and_reviews_from_the_other_side(history):
    store, conn = history
    text = timeline(conn, store, "b")
    assert "credited by Ari (a)" in text and "tasting" in text
    assert "review given to Ari (a)" in text


def test_since_drops_earlier_runs(history):
    store, conn = history
    text = timeline(conn, store, "a", since="r2")
    assert "## r2" in text and "## r1" not in text and "Before any run" not in text
    with pytest.raises(KeyError):
        timeline(conn, store, "a", since="missing")


def test_timeline_of_an_agent_with_no_runs(community):
    store, conn = community
    text = timeline(conn, store, "c")
    assert "## Before any run" in text and "create agent c" in text
```

Append to `tests/test_cli.py`:

```python
def test_agents_timeline(project):
    tmp_path, config = project
    assert invoke("agents", "new", "a", "--model", "local", "--temperament", "T.", "-c", config).exit_code == 0
    result = invoke("agents", "timeline", "member 1", "-c", config)
    assert result.exit_code == 0, result.output
    assert result.output.startswith("# Timeline: Member 1 (a)")
    missing = invoke("agents", "timeline", "nobody", "-c", config)
    assert missing.exit_code != 0 and "no agent" in missing.output
    bad_since = invoke("agents", "timeline", "a", "--since", "nope", "-c", config)
    assert bad_since.exit_code != 0 and "no run" in bad_since.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_timeline.py tests/test_cli.py::test_agents_timeline -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'aiswarm.timeline'`.

- [ ] **Step 3: Implement the helpers**

Append to `src/aiswarm/reviews.py` (after `reviews_of`):

```python
def reviews_by(conn: sqlite3.Connection, agent_id: str) -> list[Review]:
    return [_review(row) for row in conn.execute("SELECT * FROM reviews WHERE reviewer = ? ORDER BY id", (agent_id,))]
```

Add to `ProfileStore` (`src/aiswarm/profiles.py`; import `Commit` from `aiswarm.gitrepo`):

```python
    def history(self, agent_id: str) -> list[Commit]:
        """Commits to an agent's profile files, oldest first (journals and seed excluded)."""
        base = f"agents/{agent_id}"
        return self._repo.log(base, exclude=[f"{base}/journal", f"{base}/{SEED_DIR}"])
```

- [ ] **Step 4: Implement `src/aiswarm/timeline.py`**

```python
"""An agent's timeline: one chronological, user-only markdown view of what it did, wrote, and received."""

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime

from aiswarm.profiles import ProfileStore
from aiswarm.reviews import ratings_for, render_review, responses_for, reviews_by, reviews_of
from aiswarm.view import Who, make_who

EXCERPT = 300


@dataclass(frozen=True)
class Item:
    at: datetime
    run_id: str | None  # None: placed by time
    title: str
    body: str = ""


def _at(text: str) -> datetime:
    return datetime.fromisoformat(text)


def _excerpt(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= EXCERPT else text[:EXCERPT] + "…"


def resolve_agent(store: ProfileStore, key: str) -> str | None:
    """An agent id from its id, current name, or a former name (case-insensitive)."""
    if key in store.ids():
        return key
    folded = key.casefold()
    profiles = {i: store.public(i) for i in store.ids()}
    for agent_id, profile in profiles.items():
        if profile and profile.name.casefold() == folded:
            return agent_id
    for agent_id, profile in profiles.items():
        if profile and any(name.casefold() == folded for name in profile.former_names):
            return agent_id
    return None


def _rename(patch: str) -> tuple[str, str] | None:
    old = re.search(r'^-name = "(.*)"$', patch, re.M)
    new = re.search(r'^\+name = "(.*)"$', patch, re.M)
    return (old.group(1), new.group(1)) if old and new else None


def _git_items(store: ProfileStore, agent_id: str) -> list[Item]:
    items = []
    for commit in store.history(agent_id):
        title = f"{commit.subject} (by {commit.author})"
        if renamed := _rename(commit.patch):
            title += f" — renamed {renamed[0]} → {renamed[1]}"
        items.append(Item(commit.at, None, title, f"```diff\n{commit.patch}\n```" if commit.patch else ""))
    return items


def _review_items(conn: sqlite3.Connection, agent_id: str, who: Who) -> list[Item]:
    received = reviews_of(conn, agent_id, include_self=True)
    given = [r for r in reviews_by(conn, agent_id) if not r.is_self]
    reviews = received + given
    ids = [r.id for r in reviews]
    responses, ratings = responses_for(conn, ids), ratings_for(conn, ids)
    created = {row["id"]: row["created_at"] for row in conn.execute(
        "SELECT id, created_at FROM reviews WHERE reviewer = ? OR reviewee = ?", (agent_id, agent_id))}
    items = []
    for r in reviews:
        if r.is_self:
            title = "self-review"
        elif r.reviewee == agent_id:
            title = f"review received from {who(r.reviewer)}"
        else:
            title = f"review given to {who(r.reviewee)}"
        body = render_review(r, who, private=True, response=responses.get(r.id), ratings=ratings[r.id])
        items.append(Item(_at(created[r.id]), r.run_id, title, body))
    return items


def _credit_items(conn: sqlite3.Connection, agent_id: str, who: Who) -> list[Item]:
    items = []
    for closed in conn.execute("SELECT run_id, payload, created_at FROM entries WHERE type = 'run_closed'"):
        call_id = json.loads(closed["payload"]).get("call_id")
        if not call_id:
            continue
        call = conn.execute("SELECT author, payload FROM entries WHERE id = ?", (call_id,)).fetchone()
        credits = json.loads(call["payload"]).get("credits", [])
        at, run_id = _at(closed["created_at"]), closed["run_id"]
        if call["author"] == agent_id:
            body = "\n".join(f"- {who(c['agent_id'])}: {c['contribution']}" for c in credits) or "(credited no one)"
            items.append(Item(at, run_id, "credits given in the closing call", body))
        for credit in credits:
            if credit.get("agent_id") == agent_id:
                items.append(Item(at, run_id, f"credited by {who(call['author'])}", credit.get("contribution", "")))
    return items


def _db_items(conn: sqlite3.Connection, agent_id: str, who: Who) -> list[Item]:
    items = [Item(_at(r["created_at"]), r["run_id"], f"journal ({r['section']} reviews)", r["text"].strip())
             for r in conn.execute("SELECT * FROM journals WHERE agent_id = ?", (agent_id,))]
    items += _review_items(conn, agent_id, who) + _credit_items(conn, agent_id, who)
    for r in conn.execute("SELECT * FROM norm_proposals WHERE author = ?", (agent_id,)):
        what = r["text"] or f"{r['name']}: {r['question']}"
        items.append(Item(_at(r["created_at"]), r["run_id"], f"proposed a {r['kind']} (#{r['id']}, {r['status']})", what))
    for r in conn.execute("SELECT c.points, c.created_at, p.id, p.run_id FROM norm_critiques c "
                          "JOIN norm_proposals p ON p.id = c.proposal_id WHERE c.author = ?", (agent_id,)):
        items.append(Item(_at(r["created_at"]), r["run_id"], f"critiqued proposal #{r['id']}", r["points"]))
    for r in conn.execute("SELECT v.stance, v.reason, v.created_at, p.id, p.run_id FROM norm_votes v "
                          "JOIN norm_proposals p ON p.id = v.proposal_id WHERE v.author = ?", (agent_id,)):
        items.append(Item(_at(r["created_at"]), r["run_id"], f"voted {r['stance']} on proposal #{r['id']}", r["reason"]))
    for r in conn.execute("SELECT t.reasoning, t.run_id, e.id, e.type, e.created_at FROM traces t "
                          "JOIN entries e ON e.id = t.entry_id WHERE t.agent_id = ?", (agent_id,)):
        items.append(Item(_at(r["created_at"]), r["run_id"], f"reasoning behind #{r['id']} {r['type']}",
                          _excerpt(r["reasoning"])))
    for r in conn.execute("SELECT run_id, step, subject, reasoning, updated_at FROM postrun_steps "
                          "WHERE agent_id = ? AND reasoning IS NOT NULL", (agent_id,)):
        subject = f" ({r['subject']})" if r["subject"] else ""
        items.append(Item(_at(r["updated_at"]), r["run_id"], f"reasoning in post-run {r['step']}{subject}",
                          _excerpt(r["reasoning"])))
    return items


def timeline(conn: sqlite3.Connection, store: ProfileStore, agent_id: str, since: str | None = None) -> str:
    who = make_who({i: store.public(i) for i in store.ids()})
    runs = [(r["id"], r["goal"], _at(r["created_at"])) for r in conn.execute("SELECT id, goal, created_at FROM runs ORDER BY rowid")]
    order = {run_id: n for n, (run_id, _, _) in enumerate(runs)}
    if since is not None and since not in order:
        raise KeyError(since)

    def run_of(item: Item) -> str | None:
        if item.run_id is not None:
            return item.run_id
        latest = None
        for run_id, _, started in runs:
            if started <= item.at:
                latest = run_id
        return latest

    groups: dict[str | None, list[Item]] = {}
    for item in _git_items(store, agent_id) + _db_items(conn, agent_id, who):
        groups.setdefault(run_of(item), []).append(item)
    keys: list[str | None] = ([None] if None in groups else []) + [r for r, _, _ in runs if r in groups]
    if since is not None:
        keys = [k for k in keys if k is not None and order[k] >= order[since]]

    profile = store.public(agent_id)
    out = [f"# Timeline: {who(agent_id)}"]
    if profile and profile.former_names:
        out.append(f"formerly: {', '.join(profile.former_names)}")
    goals = {run_id: goal for run_id, goal, _ in runs}
    for key in keys:
        out += ["", "## Before any run" if key is None else f"## {key} — {goals[key]}"]
        for item in sorted(groups[key], key=lambda i: i.at):
            out += ["", f"- **{item.at.isoformat(timespec='seconds')}** {item.title}"]
            if item.body:
                out += ["", item.body]
    if not keys:
        out += ["", "_Nothing recorded yet._"]
    return "\n".join(out) + "\n"
```

- [ ] **Step 5: Add the CLI command** (`src/aiswarm/cli.py`; import `from aiswarm.timeline import resolve_agent, timeline`)

```python
@agents_app.command("timeline")
def agents_timeline(
    agent: Annotated[str, typer.Argument(help="Agent id, current name, or former name.")],
    since: Annotated[str | None, typer.Option(help="Start at this run id.")] = None,
    config: ConfigOpt = DEFAULT_PATH,
) -> None:
    """One agent's chronological history: profile diffs, journals, reviews, credits, votes, reasoning (observer view)."""
    cfg = _load(config)
    store = ProfileStore(cfg.community_dir)
    agent_id = resolve_agent(store, agent)
    if agent_id is None:
        _fail(f"no agent with id or name {agent!r}")
    try:
        typer.echo(timeline(connect(cfg.db_path), store, agent_id, since), nl=False)
    except KeyError:
        _fail(f"no run {since!r}")
```

- [ ] **Step 6: Align the spec wording** — in `docs/superpowers/specs/2026-10-10-aiswarm-experiment-harness-design.md` §6, replace `(activity outside a run, such as creation and check-ins, goes under the run it precedes, or "Before any run")` with `(activity outside a run, such as creation and check-ins, goes under the most recent run started before it, or under "Before any run")`.

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/aiswarm/timeline.py src/aiswarm/reviews.py src/aiswarm/profiles.py src/aiswarm/cli.py tests/test_timeline.py tests/test_cli.py docs/superpowers/specs/2026-10-10-aiswarm-experiment-harness-design.md
git commit -m "feat: add agents timeline, one agent's history across runs

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Close out

**Files:**
- Modify: `docs/superpowers/specs/2026-10-10-aiswarm-experiment-harness-design.md` (status line)

- [ ] **Step 1: Full suite**

Run: `uv run pytest -q`
Expected: all pass (263 + the new tests), 3 smoke tests deselected.

- [ ] **Step 2: Smoke-check the CLI against the lab without spending model calls**

Run from `lab/`:

```bash
cd lab && ../.venv/bin/aiswarm init && ../.venv/bin/aiswarm goals list && ../.venv/bin/aiswarm agents timeline a | head -40 && cd ..
```

Expected: `init` reports writing `goals.toml` (migration 4 applies to the existing database); `goals list` shows the pizza benchmark "never run; due now" and `next: benchmark`; the timeline prints agent a's history grouped under the five existing runs. Do **not** create agent `e` or start a run — that is the user's call (spec §4.5).

- [ ] **Step 3: Mark the spec implemented**

Change the status line to `- **Status:** Approved; Plan 3a implemented`.

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-10-10-aiswarm-experiment-harness-design.md
git commit -m "docs: mark Plan 3a implemented

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
