# Generation 4 Baseline Run (D) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `bikeshed baseline <run-id>` re-runs a closed community run's goal, settings, seed and brief deal in a fresh scratch room of seed agents with no norms, library, history or post-run. The Observer then compares each baseline with the community run it mirrors.

**Architecture:**
- `Runner.start` gains `seed=` and `briefs=`.
- A new module `src/bikeshed/baseline.py` reads the source run read-only, checks the backends, and builds the scratch room under `<lab>/baselines/<run-id>-b<k>/`.
- The CLI's `baseline` command introduces the agents, starts or resumes the run, and records provenance in the scratch store.
- `observer/baselines.py` finds closed baselines read-only, and `report.py` and `observe.py` render them.

**Tech Stack:** Python 3.14, uv, typer, pytest (asyncio), `FakeModel`, the `community` / `project` / `lab` fixtures.

**Spec:** `docs/superpowers/specs/2026-10-15-bikeshed-gen4-baseline-run-design.md`

## Global Constraints

- **A generation 4 lab run is live while this is built.** All work happens in the sibling worktree `../Bikeshed-baseline` (branch `baseline`, created 2026-10-15 with Andrew's approval). Never edit, commit or merge in the main checkout while `pgrep -fl "bikeshed (run|postrun)"` prints anything. Merging into `develop` waits until it prints nothing.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- The generation's store is only ever read. Use `connect_readonly(cfg.db_path)` and `ProfileStore(cfg.community_dir, create=False)`. Nothing under `cfg.community_dir` or `cfg.runs_dir` is written by `baseline`.
- Baselines live in `cfg.community_dir.parent / "baselines"`.
- `[run]` fields copied from the source: `process`, `max_rounds`, `close_rule`, `view_entries`, `max_writes`, `quorum_slack`, `stagnation_rounds`, `max_reads`. `model_retries`, `max_concurrent_calls` and `reputation_half_life_runs` come from the current config.
- A baseline has no embedder, no post-run and no control agent.
- Community runs pass neither `seed` nor `briefs`, so their behaviour and the Process 1 golden recording are unchanged.
- Full suite: `uv run pytest -q` (about 4 minutes), redirected to a file in the plan workspace. It must be fully green.

## Review Focus

1. A source run whose provenance has a resume row with a lower `max_rounds`: the baseline uses the lower cap. Pinned in Task 2 (`test_settings_come_from_the_last_start_or_resume`).
2. An agent whose temperament changed after the run: the baseline uses the first version. Pinned in Task 2 (`test_temperaments_are_first_versions`).
3. An introduction fails part-way (one agent's backend down): the command fails, and `--resume` finishes the missing introductions, then starts. Pinned in Task 3 (`test_resume_finishes_introductions_then_runs`).
4. The isolation check: the generation's `community.db` bytes, git `HEAD` and `runs/` listing are identical before and after a baseline. Pinned in Task 3.
5. An older source run with no recorded seed or no provenance is refused with a clear message, not a crash. Pinned in Task 2.

---

### Task 1: `Runner.start` takes a seed and a ready brief deal

**Files:**
- Modify: `src/bikeshed/runner.py` (`start`)
- Test: `tests/test_runner.py`

**Interfaces:**
- Produces: `Runner.start(..., brief: BriefSpec | None = None, seed: int | None = None, briefs: RunBriefs | None = None) -> str`

- [ ] **Step 0: Work in the worktree**

```bash
cd ../Bikeshed-baseline && git branch --show-current && uv sync
```
Expected: `baseline`; uv creates the worktree's own `.venv`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_runner.py`. Add these imports if they're missing: `from bikeshed.briefs import Brief, Constraint, RunBriefs` and `from bikeshed.briefs import load as load_briefs`.

```python
def test_start_records_a_given_seed(community, runner_factory):
    store, conn = community
    run_id = runner_factory(store, conn, {}).start("G", AGENTS, seed=1234)
    assert conn.execute("SELECT seed FROM runs WHERE id = ?", (run_id,)).fetchone()[0] == 1234


def test_start_saves_a_given_brief_deal_as_it_is(community, runner_factory):
    store, conn = community
    deal = RunBriefs(Brief(0, None, "Shared.", (Constraint("s.c1", "Be home by ten."),)),
                     (Brief(1, AGENTS[-1], "Private.", (Constraint("p1.c1", "No nuts."),)),))
    run_id = runner_factory(store, conn, {}).start("G", AGENTS, process=3, briefs=deal)
    assert load_briefs(conn, run_id) == deal


def test_start_refuses_a_brief_and_a_brief_deal_together(community, runner_factory):
    from bikeshed.goals import BriefSpec
    store, conn = community
    with pytest.raises(ValueError, match="not both"):
        runner_factory(store, conn, {}).start("G", AGENTS, brief=BriefSpec(shared="S"),
                                              briefs=RunBriefs(Brief(0, None, "S", ())))


def test_start_refuses_a_brief_deal_naming_a_non_member(community, runner_factory):
    store, conn = community
    deal = RunBriefs(None, (Brief(1, "zed", "Private.", ()),))
    with pytest.raises(ValueError, match="zed"):
        runner_factory(store, conn, {}).start("G", AGENTS, briefs=deal)
```

(Check `BriefSpec`'s location and constructor with `grep -n "class BriefSpec" -A 8 src/bikeshed/*.py`, and adapt the import and arguments if they differ. Ledger any adaptation as a ruling.)

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_runner.py -q -k "given_seed or brief_deal" 2>&1 | tail -5`
Expected: 4 failed, with `TypeError: ... unexpected keyword argument 'seed'` / `'briefs'`.

- [ ] **Step 3: Implement**

In `src/bikeshed/runner.py`, import `RunBriefs` from `bikeshed.briefs` next to the existing briefs imports. Change `start`'s signature and its first checks:

```python
    def start(
        self, goal: str, agent_ids: Sequence[str], *, goal_id: str | None = None,
        goal_category: str | None = None, is_benchmark: bool = False, process: int = 1,
        brief: BriefSpec | None = None, seed: int | None = None, briefs: RunBriefs | None = None,
    ) -> str:
        """`seed` and `briefs` let a baseline run repeat a recorded run's seed and brief deal exactly."""
        if process not in (1, 2, 3):
            raise ValueError("process must be 1, 2 or 3")
        if brief is not None and briefs is not None:
            raise ValueError("give a brief to deal or a dealt brief, not both")
        briefed = brief is not None or (briefs is not None and not briefs.empty)
        if process == 3 and not briefed and not is_benchmark:
```

(The rest of that `if` and its message are unchanged.) After the `unknown` agents check, add:

```python
        if briefs is not None and (strangers := sorted({b.agent_id for b in briefs.private} - set(agent_ids))):
            raise ValueError(f"the brief deal names agents not in this run: {strangers}")
```

Replace `seed = secrets.randbits(32)` with:

```python
        seed = secrets.randbits(32) if seed is None else seed
```

and inside the transaction, after `if brief is not None: save_briefs(...)`:

```python
            elif briefs is not None and not briefs.empty:
                save_briefs(self.conn, run_id, briefs)
```

- [ ] **Step 4: Run the runner tests and the golden recording**

Run: `uv run pytest tests/test_runner.py tests/test_briefs_run.py tests/test_process1_golden.py -q 2>&1 | tail -3`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/bikeshed/runner.py tests/test_runner.py
git commit -m "feat: Runner.start can repeat a recorded seed and brief deal

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Reading the source run and building the scratch room

**Files:**
- Create: `src/bikeshed/baseline.py`
- Test: `tests/test_baseline.py`

**Interfaces:**
- Consumes: `briefs.load`, `ProfileStore.first_version`, `ProfileStore.create`, `db.connect`.
- Produces:
  - `RUN_FIELDS: tuple[str, ...]`
  - `class BaselineError(ValueError)`
  - `@dataclass(frozen=True) Source(run_id, goal, goal_id, goal_category, is_benchmark: bool, agent_ids: tuple[str, ...], seed: int, run: dict[str, Any], backends: dict[str, str], models: dict[str, str], briefs: RunBriefs, temperaments: dict[str, str])`
  - `read_source(conn: sqlite3.Connection, store: ProfileStore, run_id: str) -> Source`
  - `check_backends(source: Source, cfg: Config) -> None`
  - `settings_for(source: Source, cfg: Config) -> RunSettings`
  - `home(cfg: Config) -> Path` (= `cfg.community_dir.parent / "baselines"`)
  - `new_directory(home: Path, run_id: str) -> Path` (not created)
  - `scratch_config(cfg: Config, directory: Path, settings: RunSettings) -> Config`
  - `prepare(directory: Path, source: Source) -> ProfileStore`
  - `read_info(directory: Path) -> dict[str, str]`
  - `record_run(directory: Path, run_id: str) -> None`

- [ ] **Step 1: Write the failing tests**

`tests/test_baseline.py`:

```python
import json

import pytest

from bikeshed.baseline import (
    BaselineError, check_backends, new_directory, prepare, read_info, read_source, record_run, settings_for,
)
from bikeshed.briefs import Brief, RunBriefs, save
from bikeshed.config import Config
from bikeshed.db import connect


def _provenance(conn, run_id, phase, run, model="CHANGE-ME"):
    conn.execute(
        "INSERT INTO run_provenance (run_id, phase, code_commit, code_dirty, config, prompts, models, servers, seed, "
        "fingerprint, captured_at) VALUES (?, ?, 'abc', 0, ?, '{}', ?, '{}', 1, 'f', 't')",
        (run_id, phase, json.dumps({"run": run}),
         json.dumps({a: {"backend": "local", "model": model} for a in "abc"})))


def _closed(conn, run_id="r1", seed=77, status="closed"):
    conn.execute("INSERT INTO runs (id, goal, status, ended_reason, agent_ids, created_at, seed, goal_id, goal_category, "
                 "is_benchmark, process) VALUES (?, 'Pick a pizza.', ?, 'consensus', ?, 't', ?, 'benchmark', "
                 "'underspecified', 1, 3)", (run_id, status, json.dumps(["a", "b", "c"]), seed))


def _cfg(tmp_path):
    cfg = Config.model_validate({"models": {"local": {"kind": "openai", "base_url": "http://x/v1", "model": "CHANGE-ME"}}})
    cfg.community_dir = tmp_path / "community"
    cfg.runs_dir = tmp_path / "runs"
    return cfg


def test_read_source_takes_goal_seed_backends_and_brief_deal(community):
    store, conn = community
    _closed(conn)
    _provenance(conn, "r1", "start", {"process": 3, "max_rounds": 12})
    deal = RunBriefs(Brief(0, None, "Shared.", ()), (Brief(1, "b", "Private.", ()),))
    save(conn, "r1", deal)
    src = read_source(conn, store, "r1")
    assert (src.goal, src.goal_id, src.is_benchmark, src.seed) == ("Pick a pizza.", "benchmark", True, 77)
    assert src.agent_ids == ("a", "b", "c") and src.backends == {"a": "local", "b": "local", "c": "local"}
    assert src.briefs == deal


def test_settings_come_from_the_last_start_or_resume(community, tmp_path):
    store, conn = community
    _closed(conn)
    _provenance(conn, "r1", "start", {"process": 3, "max_rounds": 12, "view_entries": 18, "max_concurrent_calls": 9})
    _provenance(conn, "r1", "resume", {"process": 3, "max_rounds": 6, "view_entries": 18, "max_concurrent_calls": 9})
    _provenance(conn, "r1", "postrun", {"process": 3, "max_rounds": 99})
    settings = settings_for(read_source(conn, store, "r1"), _cfg(tmp_path))
    assert (settings.process, settings.max_rounds, settings.view_entries) == (3, 6, 18)
    assert settings.max_concurrent_calls == _cfg(tmp_path).run.max_concurrent_calls  # the machine's, not the run's


def test_temperaments_are_first_versions(community):
    store, conn = community
    _closed(conn)
    _provenance(conn, "r1", "start", {})
    store.write_private("a", "temperament.md", "Changed later.")
    assert read_source(conn, store, "r1").temperaments["a"].strip() == "Temperament of a."


@pytest.mark.parametrize("problem, message", [("missing", "no run"), ("open", "not closed"),
                                              ("no-seed", "no recorded seed"), ("no-provenance", "no provenance")])
def test_read_source_refuses_what_it_cannot_mirror(community, problem, message):
    store, conn = community
    if problem != "missing":
        _closed(conn, seed=None if problem == "no-seed" else 77, status="paused" if problem == "open" else "closed")
    if problem not in ("missing", "no-provenance"):
        _provenance(conn, "r1", "start", {})
    with pytest.raises(BaselineError, match=message):
        read_source(conn, store, "r1")


def test_check_backends_refuses_a_missing_or_changed_backend(community, tmp_path):
    store, conn = community
    _closed(conn)
    _provenance(conn, "r1", "start", {})
    src = read_source(conn, store, "r1")
    cfg = _cfg(tmp_path)
    check_backends(src, cfg)  # unchanged: fine
    cfg.models["local"].model = "Other-Model"
    with pytest.raises(BaselineError, match="Other-Model"):
        check_backends(src, cfg)
    del cfg.models["local"]
    with pytest.raises(BaselineError, match="no longer in the config"):
        check_backends(src, cfg)


def test_prepare_builds_a_scratch_room_of_seed_agents(community, tmp_path):
    store, conn = community
    _closed(conn)
    _provenance(conn, "r1", "start", {})
    home = tmp_path / "baselines"
    first = new_directory(home, "r1")
    assert first.name == "r1-b1"
    scratch = prepare(first, read_source(conn, store, "r1"))
    assert new_directory(home, "r1").name == "r1-b2"
    assert scratch.ids() == ["a", "b", "c"]
    assert scratch.private("a")["temperament.md"].strip() == "Temperament of a."
    assert scratch.public("a") is None and not scratch.record("a").control
    assert scratch.record("a").model == "local"
    assert connect(first / "community" / "community.db").execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
    assert (first / "runs").is_dir()
    assert read_info(first)["source_run"] == "r1" and "baseline_run" not in read_info(first)
    record_run(first, "x123")
    assert read_info(first)["baseline_run"] == "x123"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_baseline.py -q 2>&1 | tail -3`
Expected: collection ERROR, `ModuleNotFoundError: No module named 'bikeshed.baseline'`.

- [ ] **Step 3: Implement**

`src/bikeshed/baseline.py`:

```python
"""A baseline run: a closed community run's goal, settings, seed and brief deal, repeated in a fresh room of seed
agents with no norms, no library, no history and no post-run. It lives in its own scratch community under
`<lab>/baselines/` and never writes to the generation's."""

import json
import sqlite3
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bikeshed.briefs import RunBriefs
from bikeshed.briefs import load as load_briefs
from bikeshed.config import Config, RunSettings
from bikeshed.db import connect, now_iso
from bikeshed.profiles import ProfileStore

INFO_FILE = "baseline.toml"
# the experiment's settings; model_retries, max_concurrent_calls and the reputation half-life govern the machine
RUN_FIELDS = ("process", "max_rounds", "close_rule", "view_entries", "max_writes", "quorum_slack", "stagnation_rounds",
              "max_reads")


class BaselineError(ValueError):
    pass


@dataclass(frozen=True)
class Source:
    run_id: str
    goal: str
    goal_id: str | None
    goal_category: str | None
    is_benchmark: bool
    agent_ids: tuple[str, ...]
    seed: int
    run: dict[str, Any]           # [run] settings from the last start or resume provenance
    backends: dict[str, str]      # agent -> backend name it ran on
    models: dict[str, str]        # agent -> model that backend named then
    briefs: RunBriefs
    temperaments: dict[str, str]  # agent -> first committed temperament.md


def read_source(conn: sqlite3.Connection, store: ProfileStore, run_id: str) -> Source:
    row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        raise BaselineError(f"no run {run_id!r}")
    if row["status"] != "closed":
        raise BaselineError(f"run {run_id} is not closed; a baseline mirrors a finished run")
    if row["seed"] is None:
        raise BaselineError(f"run {run_id} has no recorded seed, so it cannot be mirrored")
    prov = conn.execute("SELECT phase, config, models FROM run_provenance WHERE run_id = ? "
                        "AND phase IN ('start', 'resume') ORDER BY id", (run_id,)).fetchall()
    if not prov:
        raise BaselineError(f"run {run_id} has no provenance, so its settings and models are unknown")
    started = next((p for p in prov if p["phase"] == "start"), prov[0])
    run_cfg = json.loads(prov[-1]["config"]).get("run") or {}
    models = json.loads(started["models"])
    agent_ids = tuple(json.loads(row["agent_ids"]))
    missing = [a for a in agent_ids if a not in models]
    if missing:
        raise BaselineError(f"run {run_id}'s provenance names no model for {', '.join(missing)}")
    temperaments = {}
    for agent_id in agent_ids:
        first = store.first_version(agent_id, "temperament.md")
        if first is None:
            raise BaselineError(f"{agent_id} has no committed temperament in the community")
        temperaments[agent_id] = first
    return Source(run_id, row["goal"], row["goal_id"], row["goal_category"], bool(row["is_benchmark"]), agent_ids,
                  row["seed"], {k: run_cfg[k] for k in RUN_FIELDS if k in run_cfg},
                  {a: models[a]["backend"] for a in agent_ids}, {a: models[a].get("model", "") for a in agent_ids},
                  load_briefs(conn, run_id), temperaments)


def check_backends(source: Source, cfg: Config) -> None:
    """Every agent must run on the backend it ran on then, and that backend must still name the same model."""
    for agent_id in source.agent_ids:
        name = source.backends[agent_id]
        backend = cfg.models.get(name)
        if backend is None:
            raise BaselineError(f"{agent_id} ran on backend {name!r}, which is no longer in the config")
        now = backend.model_dump().get("model", "")
        if now != source.models[agent_id]:
            raise BaselineError(f"backend {name!r} now names {now!r}; {agent_id} ran on {source.models[agent_id]!r}")


def settings_for(source: Source, cfg: Config) -> RunSettings:
    return RunSettings.model_validate({**cfg.run.model_dump(), **source.run})


def home(cfg: Config) -> Path:
    return cfg.community_dir.parent / "baselines"


def new_directory(home_dir: Path, run_id: str) -> Path:
    k = 1
    while (home_dir / f"{run_id}-b{k}").exists():
        k += 1
    return home_dir / f"{run_id}-b{k}"


def scratch_config(cfg: Config, directory: Path, settings: RunSettings) -> Config:
    """The config a baseline runs under: its own community and runs, the source run's settings, no library."""
    scratch = cfg.model_copy(deep=True)
    scratch.community_dir = directory / "community"
    scratch.runs_dir = directory / "runs"
    scratch.run = settings
    scratch.library.embeddings = None
    return scratch


def prepare(directory: Path, source: Source) -> ProfileStore:
    """A fresh community holding the source run's agents as seeds: first temperament, same backend, no profile yet."""
    store = ProfileStore(directory / "community")
    connect(directory / "community" / "community.db").close()
    (directory / "runs").mkdir(parents=True, exist_ok=True)
    for agent_id in source.agent_ids:
        store.create(agent_id, source.backends[agent_id], source.temperaments[agent_id])
    _write_info(directory, {"source_run": source.run_id, "created_at": now_iso()})
    return store


def read_info(directory: Path) -> dict[str, str]:
    path = directory / INFO_FILE
    if not path.exists():
        raise BaselineError(f"no baseline at {directory}")
    return tomllib.loads(path.read_text())


def record_run(directory: Path, run_id: str) -> None:
    _write_info(directory, {**read_info(directory), "baseline_run": run_id})


def _write_info(directory: Path, info: dict[str, str]) -> None:
    (directory / INFO_FILE).write_text("".join(f"{k} = {json.dumps(v)}\n" for k, v in info.items()))
```

(Check that `ProfileStore.ids()` returns a sorted list. If it returns another order, compare with `sorted(...)` in the test and ledger it.)

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_baseline.py -q 2>&1 | tail -3`
Expected: all pass. If the `community` fixture's agents `a`, `b`, `c` lack a committed first temperament, `store.create` commits one, so `first_version` returns `Temperament of a.`.

- [ ] **Step 5: Commit**

```bash
git add src/bikeshed/baseline.py tests/test_baseline.py
git commit -m "feat: read a closed run for a baseline and build its scratch room of seed agents

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: The `bikeshed baseline` command

**Files:**
- Modify: `src/bikeshed/cli.py` (new `baseline` command)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: Task 1's `Runner.start(seed=, briefs=)`; Task 2's whole module.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py`:

```python
def _snapshot(tmp_path):
    import hashlib
    import subprocess
    community = tmp_path / "community"
    head = subprocess.run(["git", "-C", str(community), "rev-parse", "HEAD"], capture_output=True, text=True).stdout
    return (hashlib.sha256((community / "community.db").read_bytes()).hexdigest(), head,
            sorted(p.name for p in (tmp_path / "runs").iterdir()))


def test_baseline_mirrors_a_run_in_a_scratch_room(project):
    from bikeshed.db import connect
    from bikeshed.events import read_events
    tmp_path, config = project
    make_agents(config, "a", "b")
    run_id = run_id_from(invoke("run", "Pick a colour", "--max-rounds", "2", "-c", config).output)
    before = _snapshot(tmp_path)
    result = invoke("baseline", run_id, "-c", config)
    assert result.exit_code == 0, result.output
    assert f"baseline {run_id}-b1 closed" in result.output
    assert _snapshot(tmp_path) == before                       # the generation's store is untouched
    home = tmp_path / "baselines" / f"{run_id}-b1"
    scratch = connect(home / "community" / "community.db")
    (b_run,) = [r["id"] for r in scratch.execute("SELECT id FROM runs")]
    source = connect(tmp_path / "community" / "community.db")
    seed = lambda c, r: c.execute("SELECT seed FROM runs WHERE id = ?", (r,)).fetchone()[0]
    assert seed(scratch, b_run) == seed(source, run_id)
    assert scratch.execute("SELECT COUNT(*) FROM postrun_steps").fetchone()[0] == 0
    events = [e["event"] for e in read_events(home / "runs" / b_run / "events.jsonl")]
    assert "library_retrieved" not in events
    names = (home / "community" / "agents" / "a" / "public.toml").read_text()
    assert "Member 1" not in names and "Member 2" not in names  # introduced afresh, not copied
    assert f'baseline_run = "{b_run}"' in (home / "baseline.toml").read_text()


def test_baseline_refusals(project):
    tmp_path, config = project
    make_agents(config, "a", "b")
    assert "no run 'nope'" in invoke("baseline", "nope", "-c", config).output
    unfinished = run_id_from(invoke("run", "Pick a colour", "--max-rounds", "1", "--no-postrun", "-c", config).output)
    refused = invoke("baseline", unfinished, "-c", config)
    assert refused.exit_code == 1 and "post-run" in refused.output
    assert "give a run id or --resume" in invoke("baseline", "-c", config).output


def test_baseline_refuses_a_backend_that_changed(project):
    tmp_path, config = project
    make_agents(config, "a", "b")
    run_id = run_id_from(invoke("run", "Pick a colour", "--max-rounds", "2", "-c", config).output)
    config.write_text(config.read_text().replace('model = "CHANGE-ME"', 'model = "Other-Model"', 1))
    refused = invoke("baseline", run_id, "-c", config)
    assert refused.exit_code == 1 and "Other-Model" in refused.output


def test_resume_finishes_introductions_then_runs(project, monkeypatch):
    from bikeshed.models.base import ModelError
    tmp_path, config = project
    make_agents(config, "a", "b")
    run_id = run_id_from(invoke("run", "Pick a colour", "--max-rounds", "2", "-c", config).output)
    working = cli.make_client

    def down(system, messages):
        raise ModelError("server down")

    monkeypatch.setattr(cli, "make_client", lambda backend, retries: FakeModel(down))
    failed = invoke("baseline", run_id, "-c", config)
    assert failed.exit_code == 1 and f"--resume {run_id}-b1" in failed.output
    monkeypatch.setattr(cli, "make_client", working)
    resumed = invoke("baseline", "--resume", f"{run_id}-b1", "-c", config)
    assert resumed.exit_code == 0, resumed.output
    assert f"baseline {run_id}-b1 closed" in resumed.output
    again = invoke("baseline", "--resume", f"{run_id}-b1", "-c", config)
    assert again.exit_code == 1 and "already closed" in again.output
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_cli.py -q -k baseline 2>&1 | tail -5`
Expected: 4 failed (`No such command 'baseline'`).

- [ ] **Step 3: Implement**

In `src/bikeshed/cli.py`, import `from bikeshed import baseline as bl` and `from bikeshed.db import connect_readonly` (if they aren't imported already). Add the command after `run`:

```python
@app.command()
def baseline(
    run_id: Annotated[str | None, typer.Argument(help="A closed community run to mirror.")] = None,
    resume: Annotated[str | None, typer.Option(help="Continue a baseline (its directory name, e.g. RUN-b1).")] = None,
    config: ConfigOpt = DEFAULT_PATH,
) -> None:
    """Re-run a closed run's goal, settings, seed and briefs in a fresh room: no norms, library, history or post-run."""
    cfg = _load(config)
    if (run_id is None) == (resume is None):
        _fail("give a run id or --resume BASELINE_ID, not both")
    if cfg.scorer.judge is None:
        _fail('no [scorer] configured: add a [scorer] section with judge = "<model backend>" to the config')
    community = connect_readonly(cfg.db_path)
    if (blocker := blocking(community, cfg.runs_dir)) is not None:
        _fail("the community has unfinished work; a baseline waits for it: " + blocker.message())
    try:
        source_store = ProfileStore(cfg.community_dir, create=False)
        if resume:
            directory = bl.home(cfg) / resume
            info = bl.read_info(directory)
            source = bl.read_source(community, source_store, info["source_run"])
        else:
            source = bl.read_source(community, source_store, run_id)
            directory = bl.new_directory(bl.home(cfg), source.run_id)
        bl.check_backends(source, cfg)
    except (bl.BaselineError, ProfileError) as exc:
        _fail(str(exc))
    settings = bl.settings_for(source, cfg)
    scfg = bl.scratch_config(cfg, directory, settings)
    store = ProfileStore(scfg.community_dir) if resume else bl.prepare(directory, source)
    conn = connect(scfg.db_path)
    info = bl.read_info(directory)
    if "baseline_run" in info:
        status = conn.execute("SELECT status FROM runs WHERE id = ?", (info["baseline_run"],)).fetchone()
        if status is not None and status["status"] == "closed":
            _fail(f"baseline {directory.name} is already closed")
    for agent_id in source.agent_ids:  # fresh introductions; a resume finishes any that failed
        if store.public(agent_id) is None:
            client = make_client(scfg.models[store.record(agent_id).model], scfg.run.model_retries)
            typer.echo(f"asking {agent_id} to introduce itself…", err=True)
            try:
                asyncio.run(introduce(store, agent_id, client))
            except (ProfileError, ModelError) as exc:
                _fail(f"{exc}. Retry with `bikeshed baseline --resume {directory.name}`")
    scorer_client = make_client(scfg.models[scfg.scorer.judge], scfg.run.model_retries)
    agent_ids = list(source.agent_ids)
    clients = _clients(scfg, store, agent_ids)
    backends = {a: store.record(a).model for a in agent_ids}

    async def go() -> None:
        limited = limit_clients(clients, scfg.run.max_concurrent_calls)
        runner = Runner(settings=settings, conn=conn, store=store, runs_dir=scfg.runs_dir, clients=limited,
                        sink=_print_event, embedder=None, library_top_k=0, scorer=scorer_client)
        started = info.get("baseline_run")
        try:
            b_run = started or runner.start(
                source.goal, agent_ids, goal_id=source.goal_id, goal_category=source.goal_category,
                is_benchmark=source.is_benchmark, process=settings.process, seed=source.seed, briefs=source.briefs)
        except ValueError as exc:
            _fail(str(exc))
        if not started:
            bl.record_run(directory, b_run)
        await capture(conn, b_run, "resume" if started else "start", scfg, backends, probe=probe_server)
        result = await runner.run(b_run)
        if result.status == "paused":
            typer.echo(f"baseline {directory.name} paused; resume with `bikeshed baseline --resume {directory.name}`")
            return
        typer.echo(f"baseline {directory.name} closed: {result.reason}")
        typer.echo(f"report: {scfg.runs_dir / b_run / 'report'}")

    asyncio.run(go())
```

(Match the names already used in `cli.py` for `blocking`, `connect`, `capture`, `probe_server`, `limit_clients`, `introduce`, `Runner`, `ModelError` and `ProfileError`; most are imported for `run` and `agents new`. If `run` wraps `go()` in a helper for interrupts, mirror that.)

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_cli.py -q -k baseline 2>&1 | tail -3`
Expected: 4 passed.
- If `test_resume_finishes_introductions_then_runs` fails because the failing introduction leaves no `baseline.toml` (it does exist: `prepare` writes it before the introductions), debug before touching the test.
- If `"post-run"` isn't in the refusal message, read `Blocker.message()` and match its words. Ledger the change.

- [ ] **Step 5: Commit**

```bash
git add src/bikeshed/cli.py tests/test_cli.py
git commit -m "feat: bikeshed baseline re-runs a closed run in a fresh room of seed agents

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The Observer compares baselines with the runs they mirror

**Files:**
- Modify: `src/bikeshed/observer/metrics.py` (extract `run_answer` from `benchmark_answers`)
- Create: `src/bikeshed/observer/baselines.py`
- Modify: `src/bikeshed/observer/report.py` (`RunSection.baselines`, `SummarySection.baselines`, renderers)
- Modify: `src/bikeshed/observer/observe.py` (find baselines; pass them in)
- Test: `tests/test_observer_baselines.py`, `tests/test_observer_report.py`, `tests/test_observer_observe.py`

**Interfaces:**
- Produces:
  - `metrics.run_answer(conn, run: RunInfo, who: Who) -> BenchmarkAnswer`
  - `@dataclass(frozen=True) Baseline(id: str, source_run: str, run: RunInfo, rounds: int, calls: int, accepted: int, rejected: int, answer: BenchmarkAnswer, mix: dict[str, Counter], copied: int)`
  - `find(home: Path) -> list[Baseline]`
  - `RunSection.baselines: list[Baseline] = []`
  - `SummarySection.baselines: dict[str, list[Baseline]] = {}`
  - `SummarySection.rooms: dict[str, tuple[RunInfo, BenchmarkAnswer, dict[str, Counter]]] = {}` (source run → its info, answer and mix)

- [ ] **Step 1: Write the failing tests**

`tests/test_observer_baselines.py`:

```python
from observer_helpers import add_entry, add_run

from bikeshed.db import connect
from bikeshed.observer.baselines import find
from bikeshed.profiles import ProfileStore


def make_baseline(home, name, source, *, closed=True, recorded=True):
    directory = home / name
    store = ProfileStore(directory / "community")
    for agent_id, label in [("a", "Ash"), ("b", "Birch")]:
        store.create(agent_id, "fake", f"Temperament of {agent_id}.")
        store.set_public(agent_id, label, f"{label} headline", f"I am {label}.")
    conn = connect(directory / "community" / "community.db")
    add_run(conn, "x1", agents=("a", "b"), status="closed" if closed else "running", ended="consensus")
    add_entry(conn, "x1", "a", "propose", {"answer_summary": "Margherita", "method": "m"})
    call = add_entry(conn, "x1", "a", "call_consensus", {"synthesis": "Margherita for all.", "refs": [], "credits": []},
                     round=2)
    add_entry(conn, "x1", "system", "run_closed", {"reason": "consensus", "call_id": call}, round=2)
    (directory / "runs").mkdir()
    info = f'source_run = "{source}"\ncreated_at = "t"\n' + ('baseline_run = "x1"\n' if recorded else "")
    (directory / "baseline.toml").write_text(info)
    return directory


def test_find_reads_a_closed_baseline(tmp_path):
    make_baseline(tmp_path / "baselines", "r1-b1", "r1")
    (b,) = find(tmp_path / "baselines")
    assert (b.id, b.source_run, b.run.ended_reason, b.calls) == ("r1-b1", "r1", "consensus", 1)
    assert b.answer.consensus and b.answer.text == "Margherita for all."
    assert b.mix["a"]["propose"] == 1


def test_find_skips_open_unrecorded_and_missing(tmp_path):
    assert find(tmp_path / "baselines") == []
    make_baseline(tmp_path / "baselines", "r1-b1", "r1", closed=False)
    make_baseline(tmp_path / "baselines", "r1-b2", "r1", recorded=False)
    assert find(tmp_path / "baselines") == []
```

In `tests/test_observer_report.py` (import `Baseline` from `bikeshed.observer.baselines`, plus `RunInfo`, `BenchmarkAnswer` and `Counter`, which are already imported):

```python
def _baseline(source="r1"):
    run = RunInfo("x1", "Pick a fruit.", "best-fruit", "unsettled", False, "consensus", ("a", "b"), "t", "t")
    return Baseline("r1-b1", source, run, 2, 1, 1, 0, BenchmarkAnswer("x1", True, "Mango."),
                    {"a": Counter(propose=1), "b": Counter(vote=1)}, 3)


def test_run_report_shows_its_baselines():
    text = render_run(section(baselines=[_baseline()]))
    block = text.split("## Baseline\n")[1].split("\n## ")[0]
    assert "| r1-b1 | consensus | 2 | 1 | 1/0 | 3 |" in block and "Mango." in block


def test_summary_compares_baseline_with_control_and_twin():
    text = render_summary(summary_section(
        twins={"a": ["b"]}, baselines={"r1": [_baseline()]},
        rooms={"r1": (RUN, BenchmarkAnswer("r1", False, "- Alpha (a): Pear"), {"a": Counter(propose=2)})}))
    block = text.split("## Baseline vs community\n")[1].split("\n## ")[0]
    assert "### best-fruit" in block and "Pear" in block and "Mango." in block
    assert "| community | Alpha (a) (control) | 2 |" in block
    assert "| r1-b1 | a | 1 |" in block
```

In `tests/test_observer_observe.py`, add (import `make_baseline` with `from test_observer_baselines import make_baseline`):

```python
async def test_observe_attaches_a_baseline_to_its_run(lab, community):
    make_baseline(lab.community_dir.parent / "baselines", "r1-b1", "r1")
    await observe(lab, judge_note="--no-judge")
    assert "## Baseline" in (lab.observer.dir / "runs" / "r1.md").read_text()
    assert "## Baseline vs community" in (lab.observer.dir / "summary.md").read_text()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_observer_baselines.py tests/test_observer_report.py tests/test_observer_observe.py -q -k "baseline" 2>&1 | tail -4`
Expected: collection ERROR, `ModuleNotFoundError: No module named 'bikeshed.observer.baselines'`.

- [ ] **Step 3: Implement**

`metrics.py`: split `benchmark_answers` so the per-run logic is reusable:

```python
def run_answer(conn: sqlite3.Connection, run: RunInfo, who: Who) -> BenchmarkAnswer:
    """The closing call's synthesis, or each member's latest proposal when the run did not close on a call."""
    closed = conn.execute("SELECT payload FROM entries WHERE run_id = ? AND type = 'run_closed'", (run.id,)).fetchone()
    call_id = json.loads(closed["payload"]).get("call_id") if closed else None
    if call_id:
        call = conn.execute("SELECT payload FROM entries WHERE id = ?", (call_id,)).fetchone()
        return BenchmarkAnswer(run.id, True, json.loads(call["payload"])["synthesis"])
    latest: dict[str, str] = {}
    for row in _entries(conn, run.id):
        if row["type"] == "propose":
            latest[row["author"]] = json.loads(row["payload"])["answer_summary"]
    text = "\n".join(f"- {who(a)}: {t}" for a, t in latest.items()) or "(no proposals)"
    return BenchmarkAnswer(run.id, False, text)


def benchmark_answers(conn: sqlite3.Connection, runs: list[RunInfo], who: Who) -> list[BenchmarkAnswer]:
    return [run_answer(conn, run, who) for run in runs if run.is_benchmark]
```

`src/bikeshed/observer/baselines.py`:

```python
"""Baseline runs, read only: each scratch room under <lab>/baselines/ that mirrors a closed community run."""

import json
import tomllib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from bikeshed.db import connect_readonly
from bikeshed.observer import metrics as m
from bikeshed.observer import scoring as sc
from bikeshed.observer.copying import copies
from bikeshed.profiles import ProfileError, ProfileStore
from bikeshed.view import make_who


@dataclass(frozen=True)
class Baseline:
    id: str
    source_run: str
    run: m.RunInfo
    rounds: int
    calls: int
    accepted: int
    rejected: int
    answer: m.BenchmarkAnswer
    mix: dict[str, Counter]
    copied: int


def find(home: Path) -> list[Baseline]:
    """Closed baselines, oldest directory name first. Open, unrecorded or unreadable ones are skipped."""
    out = []
    for directory in sorted(home.glob("*/")) if home.exists() else []:
        info_path = directory / "baseline.toml"
        db = directory / "community" / "community.db"
        if not info_path.exists() or not db.exists():
            continue
        info = tomllib.loads(info_path.read_text())
        conn = connect_readonly(db)
        run = next((r for r in m.closed_runs(conn) if r.id == info.get("baseline_run")), None)
        if run is None:
            continue
        try:
            store = ProfileStore(directory / "community", create=False)
            who = make_who({i: store.public(i) for i in store.ids()})
        except ProfileError:
            who = make_who({})
        rulings = sc.rulings(conn, run.id)
        rounds = conn.execute("SELECT MAX(round) FROM rounds WHERE run_id = ?", (run.id,)).fetchone()[0] or 0
        calls = conn.execute("SELECT COUNT(*) FROM entries WHERE run_id = ? AND type = 'call_consensus'",
                             (run.id,)).fetchone()[0]
        out.append(Baseline(directory.name, info["source_run"], run, rounds, calls,
                            sum(r.accepted for r in rulings), sum(not r.accepted for r in rulings),
                            m.run_answer(conn, run, who), m.action_mix(conn, run),
                            len(copies(conn, run, directory / "runs"))))
    return out
```

(If `make_who({})` isn't valid, check `view.make_who`'s signature and use the fallback it supports. Remove the unused `json` import if nothing uses it.)

`report.py`: import `Baseline` from `bikeshed.observer.baselines`. Add `baselines: list[Baseline] = field(default_factory=list)` to `RunSection`, and to `SummarySection`:

```python
    baselines: dict[str, list[Baseline]] = field(default_factory=dict)  # source run -> its baselines
    rooms: dict[str, tuple[RunInfo, BenchmarkAnswer, dict[str, Counter]]] = field(default_factory=dict)
```

Add the renderers:

```python
def _baselines(s: RunSection) -> list[str]:
    if not s.baselines:
        return []
    out = ["## Baseline", "", "_The same goal, settings, seed and briefs in a fresh room: no norms, library or history._",
           "", "| Baseline | Ending | Rounds | Calls | Rulings (accepted/rejected) | Copied |", "|---|---|---|---|---|---|"]
    out += [f"| {b.id} | {b.run.ended_reason or 'unknown'} | {b.rounds} | {b.calls} | {b.accepted}/{b.rejected} | "
            f"{b.copied} |" for b in s.baselines]
    for b in s.baselines:
        out += ["", f"### {b.id} answer", "", b.answer.text]
    return out + [""]


def _baseline_vs_community(s: SummarySection) -> list[str]:
    if not s.baselines:
        return []
    out = ["## Baseline vs community", ""]
    pairs = [(c, t) for c, twins in s.twins.items() for t in [c, *twins]]
    for source, found in s.baselines.items():
        if source not in s.rooms:
            continue
        run, answer, mix = s.rooms[source]
        out += [f"### {run.tag}", "", f"- community: {run.ended_reason or 'unknown'}; answer:", "", answer.text, ""]
        for b in found:
            out += [f"- {b.id}: {b.run.ended_reason or 'unknown'}, {b.rounds} rounds; answer:", "", b.answer.text, ""]
        agents = list(dict.fromkeys(a for _, a in pairs)) or list(run.agent_ids)
        out += ["| Room | Agent | " + " | ".join(TWIN_ACTIONS) + " |", "|---" * (len(TWIN_ACTIONS) + 2) + "|"]
        for a in agents:
            label = s.who(a) + (" (control)" if a in s.twins else " (twin)" if any(a in t for t in s.twins.values()) else "")
            out.append(f"| community | {label} | " + " | ".join(str(mix.get(a, Counter()).get(x, 0)) for x in TWIN_ACTIONS) + " |")
            for b in found:
                out.append(f"| {b.id} | {a} | " + " | ".join(str(b.mix.get(a, Counter()).get(x, 0)) for x in TWIN_ACTIONS) + " |")
        out.append("")
    return out
```

(In the summary test, `TWIN_ACTIONS` starts with `propose`, so the first count column is `propose`: community `Alpha (a) (control)` 2, baseline `a` 1.) In `render_run`, append `_baselines(s)` after `_library(s)`. In `render_summary`, add `out += _baseline_vs_community(s)` right after `out += _control_vs_twin(s)`.

`observe.py`: import `from bikeshed.observer import baselines as bs` and `from bikeshed.baseline import home as baseline_home`. In `observe()`, after `who = ...`:

```python
    found_baselines = bs.find(baseline_home(cfg))
```

In the `RunSection(...)` call, pass `baselines=[b for b in found_baselines if b.source_run == run.id]`. In the call to `_write_summary`, add a parameter `found_baselines: list` and pass it. Inside `_write_summary`, build:

```python
    by_source: dict[str, list] = {}
    for b in found_baselines:
        by_source.setdefault(b.source_run, []).append(b)
    rooms = {r.id: (r, m.run_answer(conn, r, who), m.action_mix(conn, r)) for r in runs if r.id in by_source}
```

and pass `baselines=by_source, rooms=rooms` to `SummarySection`.

- [ ] **Step 4: Run the observer tests**

Run: `uv run pytest tests/test_observer_baselines.py tests/test_observer_report.py tests/test_observer_observe.py tests/test_observer_metrics.py tests/test_observer_isolation.py -q 2>&1 | tail -3`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/bikeshed/observer/metrics.py src/bikeshed/observer/baselines.py src/bikeshed/observer/report.py src/bikeshed/observer/observe.py tests/test_observer_baselines.py tests/test_observer_report.py tests/test_observer_observe.py
git commit -m "feat: the Observer reports each baseline beside the run it mirrors

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Docs

**Files:**
- Modify: `docs/gen4-fixes.md` (fix 6, table row D)
- Modify: `CLAUDE.md` (commands list)

- [ ] **Step 1: Record the outcome**

Under fix 6's "Checked against the code" paragraph in `gen4-fixes.md`, matching that block's indentation:

`*Done in sub-project D (`2026-10-15-bikeshed-gen4-baseline-run-design.md`):* `bikeshed baseline <run-id>` re-runs a closed run's goal, `[run]` settings, seed and brief deal in a scratch room under `lab/baselines/`, with fresh introductions and no norms, library, history or post-run. The Observer adds a "## Baseline" section to the source run's report and "## Baseline vs community" to the summary. A baseline can be made any time after its source run closes, as long as the backends still name the same models.`

D's table row status: `**In progress**`.

- [ ] **Step 2: Add the command to `CLAUDE.md`**

In the `# runs` block of the commands list, after the `bikeshed postrun` line:

```
uv run bikeshed baseline <run-id> | --resume <run-id>-b<k>   # same goal, settings, seed, briefs in a fresh room (lab/baselines/)
```

- [ ] **Step 3: Full suite**

Run: `uv run pytest -q > .superpowers/sdd/2026-10-15-bikeshed-gen4-baseline-run/suite.txt 2>&1; tail -3 .superpowers/sdd/2026-10-15-bikeshed-gen4-baseline-run/suite.txt`
Expected: all passed, 0 failed.

- [ ] **Step 4: Commit**

```bash
git add docs/gen4-fixes.md CLAUDE.md
git commit -m "docs: record sub-project D; add bikeshed baseline to the commands list

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

When D merges, set its row to `**Done**` with the merge commit.
