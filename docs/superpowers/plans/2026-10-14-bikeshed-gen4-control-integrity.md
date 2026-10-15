# Generation 4 Control Integrity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the control agent carry no personal memory into a run, and always hold a private brief alongside its twin, before generation 4's first run.

**Architecture:** A new `sequence.py` enforces one run or post-run cycle at a time, and the CLI checks it before acting. `ProfileStore` learns to compare a control with its seed (`seed_drift`) and to find twins. `Runner.run()` checks controls on every start and resume. The view hides a control's own standing, the library gives each agent its own list, the update step reads "null" as keep, and `briefs.assign` protects the control and its twin. Plus hand-made generation 4 setup.

**Tech Stack:** Python 3.14, uv, pytest (asyncio auto mode), Typer, Pydantic, SQLite, git (community repo).

**Spec:** `docs/superpowers/specs/2026-10-14-bikeshed-gen4-control-integrity-design.md` (background: `docs/gen4-fixes.md`, fixes 1–5 and 7).

## Global Constraints

- Work on `develop`. Before committing anything, check `pgrep -fl "bikeshed (run|postrun)"`; if a lab run is live, stop and work in a sibling worktree (`../Bikeshed-<name>`), not under the repo.
- No orchestrator: the Runner never reads content to steer agents. Nothing here adds wording to any prompt.
- Observation never feeds back: no module on the agent path may import `bikeshed.observer` (`tests/test_observer_isolation.py`). `sequence.py` joins that list.
- No fabricated data: a failure is recorded, never back-filled.
- Process 1 golden recording (`tests/golden/`) must not change. Never set `BIKESHED_WRITE_GOLDEN=1` in this plan; if `tests/test_process1_golden.py` fails, find out why.
- Run tests with `uv run pytest -q` (smoke tests are excluded by default). Every task ends with the full suite green.
- Commit messages end with: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`
- Match the surrounding code: docstrings are one or two plain sentences, comments are rare and say why.

## Review Focus

- A run killed mid-round (status stays `running`, process gone): `bikeshed run "<goal>"` must refuse and name both `run --resume` and `abandon`, not hang or silently start (Task 2, `test_run_refuses_while_another_run_is_unclosed`).
- A post-run that paused on an outage: the next `bikeshed run` must refuse until `bikeshed postrun <id>` finishes it, and `bikeshed postrun <id>` itself must not be refused by its own unfinished state (Task 1 and Task 2 tests).
- A control whose seed name was taken by another member: the drift check must not report `public.toml` drift on every run just because the name was kept (Task 3, `test_seed_drift_ignores_a_kept_name`).
- A model replying `"pronouns": ""` to say "rather not say": must still clear pronouns, not be read as keep (Task 5, `test_update_null_strings_keep_files_and_profile_fields`).
- A library smaller than k once the control's own methods are removed: the control gets what is left, no error (Task 7, `test_control_library_list_leaves_out_its_own_methods`).

---

### Task 1: `sequence.py`: one run or post-run at a time

**Files:**
- Create: `src/bikeshed/sequence.py`
- Modify: `src/bikeshed/observer/metrics.py` (remove `postrun_unfinished`, lines 66–85)
- Modify: `src/bikeshed/observer/observe.py:143` (import from `sequence`)
- Modify: `tests/test_observer_isolation.py:11` (add `sequence.py` to `AGENT_PATH`)
- Test: `tests/test_sequence.py`

**Interfaces:**
- Produces: `postrun_unfinished(conn: sqlite3.Connection, runs_dir: Path, run_id: str) -> bool` (moved, unchanged); `Blocker(run_id: str, status: str, commands: tuple[str, ...])` with `.message() -> str`; `blocking(conn, runs_dir, *, run_id: str | None = None, for_postrun: bool = False) -> Blocker | None`.

- [ ] **Step 1: Write the failing tests**

`tests/test_sequence.py`:

```python
import json

from bikeshed.sequence import Blocker, blocking, postrun_unfinished


def add(conn, run_id, status):
    conn.execute("INSERT INTO runs (id, goal, status, agent_ids, created_at) VALUES (?, 'g', ?, '[]', 't')",
                 (run_id, status))


def start_postrun(conn, runs_dir, run_id, *, finished):
    conn.execute("INSERT INTO postrun_steps (run_id, step, agent_id, status, updated_at) "
                 "VALUES (?, 'journal', 'a', 'done', 't')", (run_id,))
    events = runs_dir / run_id / "events.jsonl"
    events.parent.mkdir(parents=True, exist_ok=True)
    names = ["postrun_started"] + (["postrun_finished"] if finished else [])
    events.write_text("".join(json.dumps({"event": name}) + "\n" for name in names))


def test_nothing_blocks_an_empty_community(community, tmp_path):
    _, conn = community
    assert blocking(conn, tmp_path / "runs") is None


def test_the_oldest_unclosed_run_blocks_and_says_how_to_clear_it(community, tmp_path):
    _, conn = community
    add(conn, "r1", "closed")
    add(conn, "r2", "paused")
    add(conn, "r3", "running")
    found = blocking(conn, tmp_path / "runs")
    assert found == Blocker("r2", "paused", ("bikeshed run --resume r2", "bikeshed abandon r2"))
    assert found.message() == "run r2 is paused; clear it first with `bikeshed run --resume r2` or `bikeshed abandon r2`"


def test_a_run_never_blocks_itself(community, tmp_path):
    _, conn = community
    add(conn, "r1", "paused")
    assert blocking(conn, tmp_path / "runs", run_id="r1") is None
    add(conn, "r2", "running")
    assert blocking(conn, tmp_path / "runs", run_id="r1").run_id == "r2"


def test_an_unfinished_postrun_blocks_runs_but_not_a_postrun(community, tmp_path):
    _, conn = community
    runs = tmp_path / "runs"
    add(conn, "r1", "closed")
    start_postrun(conn, runs, "r1", finished=False)
    assert blocking(conn, runs) == Blocker("r1", "closed with its post-run unfinished", ("bikeshed postrun r1",))
    assert blocking(conn, runs, run_id="r2").run_id == "r1"  # resuming another run
    assert blocking(conn, runs, run_id="r1", for_postrun=True) is None
    add(conn, "r2", "closed")
    assert blocking(conn, runs, run_id="r2", for_postrun=True) is None


def test_a_postrun_is_blocked_by_another_unclosed_run(community, tmp_path):
    _, conn = community
    add(conn, "r1", "closed")
    add(conn, "r2", "paused")
    assert blocking(conn, tmp_path / "runs", run_id="r1", for_postrun=True).run_id == "r2"


def test_finished_skipped_and_abandoned_postruns_block_nothing(community, tmp_path):
    _, conn = community
    runs = tmp_path / "runs"
    add(conn, "done", "closed")
    start_postrun(conn, runs, "done", finished=True)
    add(conn, "skipped", "closed")  # --no-postrun: its post-run never started
    add(conn, "gone", "closed")  # abandoned: no post-run
    assert blocking(conn, runs) is None
    assert not postrun_unfinished(conn, runs, "skipped")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_sequence.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'bikeshed.sequence'`.

- [ ] **Step 3: Create `src/bikeshed/sequence.py`**

Move `postrun_unfinished` here verbatim from `observer/metrics.py` and add `Blocker` and `blocking`:

```python
"""Runs and post-run cycles happen one at a time, so one run's after-run changes never land inside another run."""

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path


def postrun_unfinished(conn: sqlite3.Connection, runs_dir: Path, run_id: str) -> bool:
    """A closed run whose post-run cycle has started but not finished (running, paused, or interrupted)."""
    started = conn.execute(
        "SELECT 1 FROM postrun_steps WHERE run_id = ?1 UNION ALL "
        "SELECT 1 FROM run_provenance WHERE run_id = ?1 AND phase = 'postrun' LIMIT 1", (run_id,)).fetchone()
    if not started:
        return False
    events = runs_dir / run_id / "events.jsonl"
    if not events.exists():
        return False  # cannot tell; treat as finished rather than hide the run forever
    last = None
    for line in events.read_text().splitlines():
        try:
            name = json.loads(line).get("event", "")
        except json.JSONDecodeError:
            continue
        if name.startswith("postrun_"):
            last = name
    return last is not None and last != "postrun_finished"


@dataclass(frozen=True)
class Blocker:
    run_id: str
    status: str
    commands: tuple[str, ...]  # any one of them clears the block

    def message(self) -> str:
        options = " or ".join(f"`{c}`" for c in self.commands)
        return f"run {self.run_id} is {self.status}; clear it first with {options}"


def blocking(conn: sqlite3.Connection, runs_dir: Path, *, run_id: str | None = None,
             for_postrun: bool = False) -> Blocker | None:
    """The oldest run, other than `run_id`, that must finish before a run (or, with for_postrun, a post-run) goes on."""
    for row in conn.execute("SELECT id, status FROM runs ORDER BY rowid").fetchall():
        other = row["id"]
        if other == run_id:
            continue
        if row["status"] in ("running", "paused"):
            return Blocker(other, row["status"], (f"bikeshed run --resume {other}", f"bikeshed abandon {other}"))
        if not for_postrun and postrun_unfinished(conn, runs_dir, other):
            return Blocker(other, "closed with its post-run unfinished", (f"bikeshed postrun {other}",))
    return None
```

- [ ] **Step 4: Point the Observer at the moved function**

In `src/bikeshed/observer/metrics.py`, delete the whole `postrun_unfinished` function (lines 66–85). Leave `from pathlib import Path` only if something else in the file still uses `Path` (check with `grep -n "Path" src/bikeshed/observer/metrics.py`; remove the import if nothing does).

In `src/bikeshed/observer/observe.py`, add `from bikeshed.sequence import postrun_unfinished` with the other `bikeshed.` imports and change line 143 to:

```python
    unfinished = [r.id for r in m.closed_runs(conn) if postrun_unfinished(conn, cfg.runs_dir, r.id)]
```

In `tests/test_observer_isolation.py`, add `"sequence.py"` to `AGENT_PATH` (after `"scorer.py"`).

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_sequence.py tests/test_observer_isolation.py tests/test_observer_observe.py -q`
Expected: PASS.
Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/bikeshed/sequence.py src/bikeshed/observer/metrics.py src/bikeshed/observer/observe.py tests/test_sequence.py tests/test_observer_isolation.py
git commit -m "feat: sequence.blocking finds the run that must finish first

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: CLI refuses overlapping runs and post-runs

**Files:**
- Modify: `src/bikeshed/cli.py` (`run`, around lines 278–305; `postrun`, around line 398)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `blocking(conn, runs_dir, *, run_id=None, for_postrun=False) -> Blocker | None` and `Blocker.message()` from Task 1.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py`:

```python
def insert_run(tmp_path, run_id, status):
    from bikeshed.db import connect

    conn = connect(tmp_path / "community" / "community.db")
    conn.execute("INSERT INTO runs (id, goal, status, agent_ids, created_at) VALUES (?, 'g', ?, '[\"a\"]', 't')",
                 (run_id, status))
    conn.commit()
    return conn


def test_run_refuses_while_another_run_is_unclosed(project):
    tmp_path, config = project
    make_agents(config, "a")
    insert_run(tmp_path, "r1", "running")
    result = invoke("run", "Pick a colour", "--max-rounds", "1", "-c", config)
    assert result.exit_code != 0
    assert "run r1 is running" in result.output
    assert "bikeshed run --resume r1" in result.output and "bikeshed abandon r1" in result.output


def test_resume_refuses_while_a_different_run_is_unclosed(project):
    tmp_path, config = project
    make_agents(config, "a")
    insert_run(tmp_path, "r1", "paused")
    insert_run(tmp_path, "r2", "paused")
    result = invoke("run", "--resume", "r2", "-c", config)
    assert result.exit_code != 0 and "run r1 is paused" in result.output


def test_an_unfinished_postrun_blocks_the_next_run_but_not_itself(project):
    tmp_path, config = project
    make_agents(config, "a")
    first = invoke("run", "Pick a colour", "--max-rounds", "1", "--no-postrun", "-c", config)
    run_id = run_id_from(first.output)
    events = tmp_path / "runs" / run_id / "events.jsonl"
    events.write_text(events.read_text() + json.dumps({"event": "postrun_started"}) + "\n")
    from bikeshed.db import connect

    conn = connect(tmp_path / "community" / "community.db")
    conn.execute("INSERT INTO postrun_steps (run_id, step, agent_id, status, updated_at) "
                 "VALUES (?, 'journal', 'a', 'done', 't')", (run_id,))
    conn.commit()
    blocked = invoke("run", "Pick a colour", "--max-rounds", "1", "-c", config)
    assert blocked.exit_code != 0 and f"bikeshed postrun {run_id}" in blocked.output
    finished = invoke("postrun", run_id, "-c", config)
    assert finished.exit_code == 0, finished.output
    assert invoke("run", "Pick a colour", "--max-rounds", "1", "-c", config).exit_code == 0


def test_postrun_refuses_while_another_run_is_unclosed(project):
    tmp_path, config = project
    make_agents(config, "a")
    run_id = run_id_from(invoke("run", "Pick a colour", "--max-rounds", "1", "--no-postrun", "-c", config).output)
    insert_run(tmp_path, "r9", "paused")
    result = invoke("postrun", run_id, "-c", config)
    assert result.exit_code != 0 and "run r9 is paused" in result.output
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_cli.py -q -k "unclosed or unfinished_postrun"`
Expected: FAIL (the commands run instead of refusing).

- [ ] **Step 3: Add the guard**

In `src/bikeshed/cli.py`, add `from bikeshed.sequence import blocking` with the other `bikeshed.` imports.

In `run`, immediately after `selected = None` (before the `if resume:` branch, so `--next` never prints a goal that will not run), add:

```python
    if (blocker := blocking(conn, cfg.runs_dir, run_id=resume)) is not None:
        _fail(blocker.message())
```

In `postrun`, right after the `if row["status"] != "closed": _fail(...)` check, add:

```python
    if (blocker := blocking(conn, cfg.runs_dir, run_id=run_id, for_postrun=True)) is not None:
        _fail(blocker.message())
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_cli.py -q`
Expected: PASS. (`test_run_resume_rejects_a_goal_or_next` and `test_run_with_process_2_records_it_and_resume_keeps_it` still pass: their errors come from argument checks before the guard, or there is no other unclosed run.)
Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/bikeshed/cli.py tests/test_cli.py
git commit -m "feat: run, resume and postrun refuse while another run or post-run is unfinished

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: `ProfileStore.seed_drift` and `ProfileStore.twins`

**Files:**
- Modify: `src/bikeshed/profiles.py` (`reset_to_seed`, lines 163–182; add `_seed_state`, `seed_drift`, `twins`)
- Modify: `src/bikeshed/observer/metrics.py` (remove `_norm` and `twins`, lines 239–256; drop `SEED_DIR` from the import if unused)
- Modify: `src/bikeshed/observer/observe.py:258` (`found = store.twins()`)
- Modify: `tests/test_observer_metrics.py` (remove `test_twins_match_the_controls_seed_temperament` and the `twins` import)
- Test: `tests/test_profiles.py`

**Interfaces:**
- Produces: `ProfileStore.seed_drift(agent_id: str) -> list[str]` (file names that differ from the seed state, in the order `public.toml`, `temperament.md`, `self.md`, `lessons.md`, `peer-notes.md`); `ProfileStore.twins() -> dict[str, list[str]]` (control id → sorted twin ids). `reset_to_seed(agent_id) -> bool` is unchanged in behaviour.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_profiles.py` (it already has the `control_store` fixture: agents `a` "Ash" and control `e` "Eve" with seed temperament "Seed temperament."):

```python
def test_seed_drift_is_empty_after_a_reset_and_names_what_changed(control_store):
    control_store.reset_to_seed("e")
    assert control_store.seed_drift("e") == []
    control_store.write_private("e", "lessons.md", "Learned.")
    control_store.write_private("e", "temperament.md", "Changed.")
    control_store.set_public("e", "Evelyn", "Eve headline", "I am Eve.")
    assert control_store.seed_drift("e") == ["public.toml", "temperament.md", "lessons.md"]
    control_store.reset_to_seed("e")
    assert control_store.seed_drift("e") == []


def test_seed_drift_ignores_a_kept_name(control_store):
    control_store.set_public("e", "Evelyn", "Eve headline", "I am Eve.")
    control_store.set_public("a", "Eve", "Ash headline", "I am Ash.")
    assert control_store.reset_to_seed("e") is False
    assert control_store.seed_drift("e") == []


def test_seed_drift_without_a_seed_fails(tmp_path):
    s = ProfileStore(tmp_path / "community")
    s.create("e", "local", "Seed.", control=True)
    with pytest.raises(ProfileError, match="no seed profile"):
        s.seed_drift("e")


def test_twins_match_the_controls_seed_temperament(tmp_path):
    store = ProfileStore(tmp_path / "community")
    store.create("d", "fake", "Wants evidence.")
    store.create("x", "fake", "Something else.")
    store.create("e", "fake", "Wants evidence.\n", control=True)
    store.write_private("d", "temperament.md", "Rewritten later.")
    assert store.twins() == {"e": ["d"]}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_profiles.py -q -k "seed_drift or twins"`
Expected: FAIL, `AttributeError: 'ProfileStore' object has no attribute 'seed_drift'` (and `'twins'`).

- [ ] **Step 3: Implement**

In `src/bikeshed/profiles.py`, replace `reset_to_seed` with these three methods:

```python
    def _seed_state(self, agent_id: str) -> tuple[dict[str, str], bool]:
        """What a reset writes, file by file, and whether the seed name is free to restore."""
        directory = self._dir(agent_id)
        seed = directory / SEED_DIR
        if not (seed / "public.toml").exists() or not (seed / "temperament.md").exists():
            raise ProfileError(f"{agent_id} has no seed profile; run `bikeshed agents introduce {agent_id}`")
        profile = PublicProfile.model_validate(tomllib.loads((seed / "public.toml").read_text()))
        taken = any(
            (other_profile := self.public(other)) and other_profile.name.casefold() == profile.name.casefold()
            for other in self.ids() if other != agent_id
        )
        current = self.public(agent_id)
        if taken and current:
            profile = profile.model_copy(update={"name": current.name})
        files = {"public.toml": _toml(profile.model_dump()), "temperament.md": (seed / "temperament.md").read_text()}
        files.update({filename: "" for filename in PRIVATE_FILES[1:]})
        return files, not taken

    def reset_to_seed(self, agent_id: str) -> bool:
        """Restore a control agent to its seed. Returns False if its seed name was taken and its current name kept."""
        files, restored = self._seed_state(agent_id)
        directory = self._dir(agent_id)
        for filename, content in files.items():
            (directory / filename).write_text(content)
        self._repo.commit([f"agents/{agent_id}"], f"reset control agent {agent_id} to seed", author="bikeshed")
        return restored

    def seed_drift(self, agent_id: str) -> list[str]:
        """A control agent's files that differ from what a reset would write. Writes nothing."""
        files, _ = self._seed_state(agent_id)
        directory = self._dir(agent_id)
        return [name for name, content in files.items()
                if not (directory / name).exists() or (directory / name).read_text() != content]
```

Add `twins` after `first_version`:

```python
    def twins(self) -> dict[str, list[str]]:
        """Each control agent's twins: non-control agents whose first temperament equals the control's seed."""
        def norm(text: str | None) -> str:
            return " ".join((text or "").split())

        out: dict[str, list[str]] = {}
        for agent_id in self.ids():
            if not self.record(agent_id).control:
                continue
            seed = self._dir(agent_id) / SEED_DIR / "temperament.md"
            target = norm(seed.read_text()) if seed.exists() else None
            out[agent_id] = sorted(
                other for other in self.ids()
                if other != agent_id and not self.record(other).control
                and target and norm(self.first_version(other, "temperament.md")) == target
            )
        return out
```

In `src/bikeshed/observer/metrics.py`, delete `_norm` and `twins` (lines 239–256) and drop `SEED_DIR` from `from bikeshed.profiles import SEED_DIR, ProfileStore` if nothing else uses it. In `src/bikeshed/observer/observe.py:258`, change `found = m.twins(store)` to `found = store.twins()`. In `tests/test_observer_metrics.py`, delete `test_twins_match_the_controls_seed_temperament` and remove `twins` from its imports.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_profiles.py tests/test_observer_metrics.py tests/test_observer_observe.py -q`
Expected: PASS (the existing reset tests still pass, since `reset_to_seed` writes the same files).
Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/bikeshed/profiles.py src/bikeshed/observer/metrics.py src/bikeshed/observer/observe.py tests/test_profiles.py tests/test_observer_metrics.py
git commit -m "feat: ProfileStore compares a control with its seed and finds its twins

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: `Runner.run()` checks controls on every start and resume

**Files:**
- Modify: `src/bikeshed/runner.py` (`run`, after line 121; add `_check_control`)
- Test: `tests/test_runner.py` (replace `test_start_resets_control_agents_but_resume_does_not`, lines 294–306)

**Interfaces:**
- Consumes: `ProfileStore.seed_drift(agent_id) -> list[str]` (Task 3), `ProfileStore.reset_to_seed(agent_id) -> bool`.
- Produces: events `control_checked {agent}`, `control_drift_found {agent, files}`, then `control_reset {agent}` and maybe `control_reset_name_kept {agent}`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_runner.py`, replace `test_start_resets_control_agents_but_resume_does_not` with:

```python
def reset_commits(store):
    import subprocess

    log = subprocess.run(["git", "log", "--format=%s"], cwd=store.root, capture_output=True, text=True).stdout
    return log.splitlines().count("reset control agent d to seed")


async def test_start_resets_controls_and_a_clean_run_only_checks_them(tmp_path, community, scripted, runner_factory):
    store, conn = community
    add_control(store)
    store.write_private("d", "lessons.md", "LEARNED")
    agents = [*AGENTS, "d"]
    runner = runner_factory(store, conn, {a: scripted({}) for a in agents}, max_rounds=1)
    run_id = runner.start("G", agents)
    assert store.private("d")["lessons.md"] == "" and reset_commits(store) == 1
    await runner.run(run_id)
    assert reset_commits(store) == 1  # no empty reset commit for the Observer to misread
    names = [e["event"] for e in read_events(tmp_path / "runs" / run_id / "events.jsonl")]
    assert names[:2] == ["run_started", "control_reset"]
    assert "control_checked" in names and "control_drift_found" not in names


async def test_run_finds_and_resets_drift_in_a_control(tmp_path, community, scripted, runner_factory):
    store, conn = community
    add_control(store)
    agents = [*AGENTS, "d"]
    runner = runner_factory(store, conn, {a: scripted({}) for a in agents}, max_rounds=1)
    run_id = runner.start("G", agents)
    store.write_private("d", "lessons.md", "LEAKED-BETWEEN-PAUSE-AND-RESUME")
    await runner.run(run_id)
    assert store.private("d")["lessons.md"] == ""
    events = read_events(tmp_path / "runs" / run_id / "events.jsonl")
    found = next(e for e in events if e["event"] == "control_drift_found")
    assert (found["agent"], found["files"]) == ("d", ["lessons.md"])
    names = [e["event"] for e in events]
    assert names.index("control_drift_found") < names.index("control_reset", 2)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_runner.py -q -k "controls_and_a_clean_run or drift_in_a_control"`
Expected: FAIL (`control_checked` missing; the leaked lessons survive).

- [ ] **Step 3: Implement**

In `src/bikeshed/runner.py`, in `run()`, right after the line `board, ws, events = Board(self.conn, run_id), self._workspace(run_id), self._events(run_id)`, add:

```python
        for agent_id in agent_ids:
            if self.store.record(agent_id).control:
                self._check_control(agent_id, events)
```

Add this method after `run()`:

```python
    def _check_control(self, agent_id: str, events: EventLog) -> None:
        """A control carries nothing between runs. Anything that reached it since its reset is recorded, then undone."""
        drift = self.store.seed_drift(agent_id)
        if not drift:
            events.emit("control_checked", agent=agent_id)
            return
        events.emit("control_drift_found", agent=agent_id, files=drift)
        restored = self.store.reset_to_seed(agent_id)
        events.emit("control_reset", agent=agent_id)
        if not restored:
            events.emit("control_reset_name_kept", agent=agent_id)
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_runner.py -q`
Expected: PASS.
Run: `uv run pytest -q`
Expected: PASS (the golden recording has no control agent, so its events are unchanged).

- [ ] **Step 5: Commit**

```bash
git add src/bikeshed/runner.py tests/test_runner.py
git commit -m "feat: every run start and resume checks control agents against their seed

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: "null" means keep in the update step

**Files:**
- Modify: `src/bikeshed/postrun/replies.py` (add `is_keep`, `keeps_pronouns`; make `PublicReply` fields optional, lines 49–53)
- Modify: `src/bikeshed/postrun/cycle.py` (`_update` write loop and public profile block, lines 333–349)
- Test: `tests/test_postrun.py`

**Interfaces:**
- Produces: `is_keep(value: object) -> bool`; `keeps_pronouns(value: object) -> bool` in `bikeshed.postrun.replies`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_postrun.py` (it has `finished_run`, `cycle`, `responder` and `update_reply` helpers):

```python
from bikeshed.postrun.replies import is_keep, keeps_pronouns  # noqa: E402


def test_is_keep_reads_null_strings_and_blanks_as_keep():
    for value in (None, "", "   ", "null", "NULL", " None ", '"null"', "'none'"):
        assert is_keep(value), value
    for value in ("Nullius in verba.", "nothing", "0"):
        assert not is_keep(value), value
    assert keeps_pronouns(None) and keeps_pronouns("null")
    assert not keeps_pronouns("") and not keeps_pronouns("they/them")


async def test_update_null_strings_keep_files_and_profile_fields(tmp_path, community, make_run, postrun_reply):
    store, conn = community
    store.write_private("a", "self.md", "OLD-SELF")
    store.write_private("a", "lessons.md", "OLD-LESSONS")
    store.set_public("a", "Alpha", "Alpha headline", "I am Alpha.", pronouns="she/her")
    store.set_public("b", "Bea", "Bea headline", "I am Bea.", pronouns="he/him")
    run_id = finished_run(conn, tmp_path, make_run)
    clients = {"c": FakeModel(responder(postrun_reply))}
    clients["a"] = FakeModel(responder(postrun_reply, update=update_reply(
        lessons="null", self_description="NULL", temperament="none", peer_notes="  ",
        public_profile={"name": "null", "headline": "Fresh headline", "introduction": None, "pronouns": "null"})))
    clients["b"] = FakeModel(responder(postrun_reply, update=update_reply(
        public_profile={"name": "Bea", "headline": "Bea headline", "introduction": "I am Bea.", "pronouns": ""})))
    await cycle(store, conn, tmp_path, clients).run(run_id)
    private = store.private("a")
    assert private["self.md"] == "OLD-SELF" and private["lessons.md"] == "OLD-LESSONS"
    assert private["temperament.md"].strip() == "Temperament of a." and private["peer-notes.md"] == ""
    a = store.public("a")
    assert (a.name, a.headline, a.introduction, a.pronouns, a.former_names) == \
        ("Alpha", "Fresh headline", "I am Alpha.", "she/her", [])
    assert store.public("b").pronouns == ""  # empty still means "rather not say"


async def test_update_with_every_profile_field_null_writes_nothing(tmp_path, community, make_run, postrun_reply):
    import subprocess

    store, conn = community
    run_id = finished_run(conn, tmp_path, make_run)
    clients = {a: FakeModel(responder(postrun_reply)) for a in "bc"}
    clients["a"] = FakeModel(responder(postrun_reply, update=update_reply(
        public_profile={"name": "null", "headline": "null", "introduction": "null"})))
    await cycle(store, conn, tmp_path, clients).run(run_id)
    log = subprocess.run(["git", "log", "--format=%s"], cwd=store.root, capture_output=True, text=True).stdout
    assert log.splitlines().count("a updated public profile") == 1  # only the fixture's set_public
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_postrun.py -q -k "is_keep or null"`
Expected: FAIL, `ImportError: cannot import name 'is_keep'`.

- [ ] **Step 3: Implement the helpers and optional fields**

In `src/bikeshed/postrun/replies.py`, after the `JournalText` line, add:

```python
_KEEP_WORDS = frozenset({"", "null", "none"})


def _word(value: str) -> str:
    return value.strip().strip("\"'").strip().casefold()


def is_keep(value: object) -> bool:
    """A field the model means to leave as it is: missing, blank, or null/none written as a string."""
    return value is None or (isinstance(value, str) and _word(value) in _KEEP_WORDS)


def keeps_pronouns(value: object) -> bool:
    """Pronouns differ: an empty value means "rather not say", so only a missing value or "null" keeps them."""
    return value is None or (isinstance(value, str) and _word(value) == "null")
```

Change `PublicReply` to:

```python
class PublicReply(_Reply):
    name: str | None = None
    pronouns: str | None = None
    headline: str | None = None
    introduction: str | None = None
```

- [ ] **Step 4: Use them in the update step**

In `src/bikeshed/postrun/cycle.py`, add `is_keep, keeps_pronouns` to the existing `from bikeshed.postrun.replies import ...` line. In `_update`, change the write loop's `if content is None:` to `if is_keep(content):`, and replace the whole `if reply.public_profile is not None:` block with:

```python
        if reply.public_profile is not None:
            profile = reply.public_profile
            current = self.store.public(agent_id)
            base = current.model_dump() if current else {}
            changed = {field: getattr(profile, field) for field in ("name", "headline", "introduction")
                       if not is_keep(getattr(profile, field))}
            pronouns = None if keeps_pronouns(profile.pronouns) else profile.pronouns
            if changed or pronouns is not None:
                merged = {field: changed.get(field, base.get(field, "")) for field in ("name", "headline", "introduction")}
                try:
                    self.store.set_public(agent_id, merged["name"], merged["headline"], merged["introduction"],
                                          pronouns=pronouns)
                except ProfileError as exc:
                    rejected.append(f"public profile: {exc}")
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_postrun.py -q`
Expected: PASS (existing update tests still pass: real values are written as before, and omitted pronouns still keep the current ones).
Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/bikeshed/postrun/replies.py src/bikeshed/postrun/cycle.py tests/test_postrun.py
git commit -m "fix: the update step reads null strings and blanks as keep, field by field

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: A control's view shows none of its own standing

**Files:**
- Modify: `src/bikeshed/view.py` (`collect_inputs`, lines 83–96)
- Test: `tests/test_view.py`

**Interfaces:**
- Consumes: `Reputation(score: float | None, count: int)` from `bikeshed.reviews`, already imported in `view.py`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_view.py`:

```python
def test_a_control_sees_itself_as_new_while_others_see_its_standing(tmp_path, community, make_run):
    from bikeshed.reviews import add_review

    store, conn, board, ws = setup(tmp_path, community, make_run)
    store.create("d", "fake", "Seed.", control=True)
    store.set_public("d", "Dee", "Dee headline", "I am Dee.")
    conn.execute("UPDATE runs SET status = 'closed' WHERE id = 'r1'")
    add_review(conn, run_id="r1", reviewer="a", reviewee="d", more_of="m", less_of="l", private_note="p",
               evidence=[1], overall=5, overall_reason="r")
    conn.execute("INSERT INTO scorer_points (run_id, agent_id, call_id, contribution, created_at) "
                 "VALUES ('r1', 'd', 9, 'x', 't')")
    own = view_for("d", 2, store, board, ws)
    assert "- Dee (d) — Dee headline · no reviews yet · 0 Scorer points" in own
    others = view_for("a", 2, store, board, ws)
    assert "- Dee (d) — Dee headline · ★5.0 (1 review) · 1 Scorer point" in others
    inputs = collect_inputs(agent_id="d", goal="Pick a colour", round=2, board=board, store=store, workspace=ws,
                            view_entries=15)
    assert inputs.dimension_scores["d"] == [] and "d" not in inputs.points
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_view.py -q -k control_sees_itself`
Expected: FAIL (the control's own line shows `★5.0 (1 review) · 1 Scorer point`).

- [ ] **Step 3: Implement**

In `src/bikeshed/view.py`, in `collect_inputs`, before `return ViewInputs(`, add:

```python
    control = store.record(agent_id).control
    reputations = {i: reputation(board.conn, i, half_life) for i in store.ids()}
    dimension_scores = {i: dimension_reputations(board.conn, i, half_life) for i in store.ids()}
    earned = points(board.conn)
    if control:  # a control starts every run at its seed: none of its own standing carries over
        reputations[agent_id] = Reputation(None, 0)
        dimension_scores[agent_id] = []
        earned.pop(agent_id, None)
```

and in the `ViewInputs(...)` call use them: `journals=() if control else tuple(store.journals(agent_id, 2)),`, `reputations=reputations,`, `dimension_scores=dimension_scores,`, `points=earned,`.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_view.py -q`
Expected: PASS.
Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/bikeshed/view.py tests/test_view.py
git commit -m "fix: a control's view leaves out its own reputation, dimension scores and Scorer points

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Each agent gets its own library list; a control's leaves out its own methods

**Files:**
- Modify: `src/bikeshed/library.py` (`retrieve`, lines 129–141: `top_k: int | None`)
- Modify: `src/bikeshed/runner.py` (`_library`, lines 224–235; call in `run()` at line 130; `_turn` at line 190)
- Modify: `src/bikeshed/phases.py` (`RoundContext.library`, line 31)
- Test: `tests/test_library.py`, `tests/test_runner.py`

**Interfaces:**
- Produces: `retrieve(conn, embedder, goal, top_k: int | None, exclude_run=None)` (`None` = every method, ranked); `Runner._library(run_id, goal, agent_ids, events) -> dict[str, list[str]]`; `RoundContext.library: Mapping[str, list[str]]`; event `library_retrieved {methods, control_methods?}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_library.py`:

```python
async def test_retrieve_with_no_limit_returns_every_method_ranked(community, make_run):
    store, conn = community
    board_run(conn, make_run, "r1")
    embedder = FakeEmbedder()
    await embed_missing(conn, embedder)
    found = await retrieve(conn, embedder, "how to rotate chores by burden", top_k=None)
    assert len(found) == 3
    assert [score for _, score in found] == sorted((score for _, score in found), reverse=True)
```

Append to `tests/test_runner.py` (after `add_control` is defined):

```python
async def test_control_library_list_leaves_out_its_own_methods(tmp_path, community, runner_factory, make_run):
    from bikeshed.library import embed_missing, record_methods
    from bikeshed.models.fake import FakeEmbedder

    store, conn = community
    add_control(store)
    make_run(conn, "old", ("b", "d"))
    old = Board(conn, "old")
    old.append(0, SYSTEM, "goal", {"goal": "Pick a colour"})
    old.append(1, "d", "propose", {"answer_summary": "x", "method": "OWN-METHOD pick a colour"})
    old.append(1, "b", "propose", {"answer_summary": "x", "method": "OTHER-METHOD draw lots"})
    conn.execute("UPDATE runs SET status = 'closed' WHERE id = 'old'")
    record_methods(conn, "old", "Pick a colour", old.entries())
    embedder = FakeEmbedder()
    await embed_missing(conn, embedder)
    reply = json.dumps({"action": {"type": "pass"}})
    clients = {"a": FakeModel([reply]), "d": FakeModel([reply])}
    runner = runner_factory(store, conn, clients, max_rounds=1)
    runner.embedder, runner.library_top_k = embedder, 1
    run_id = runner.start("Pick a colour", ["a", "d"])
    await runner.run(run_id)
    seen_by_a = clients["a"].calls[0][1][0]["content"]
    seen_by_d = clients["d"].calls[0][1][0]["content"]
    assert "OWN-METHOD" in seen_by_a and "OTHER-METHOD" not in seen_by_a
    assert "OTHER-METHOD" in seen_by_d and "OWN-METHOD" not in seen_by_d
    event = next(e for e in read_events(tmp_path / "runs" / run_id / "events.jsonl")
                 if e["event"] == "library_retrieved")
    assert list(event["control_methods"]) == ["d"] and len(event["methods"]) == 1


async def test_control_library_list_is_shorter_when_only_its_own_methods_exist(tmp_path, community, runner_factory,
                                                                                make_run):
    from bikeshed.library import embed_missing, record_methods
    from bikeshed.models.fake import FakeEmbedder

    store, conn = community
    add_control(store)
    make_run(conn, "old", ("d",))
    old = Board(conn, "old")
    old.append(0, SYSTEM, "goal", {"goal": "Pick a colour"})
    old.append(1, "d", "propose", {"answer_summary": "x", "method": "OWN-METHOD pick a colour"})
    conn.execute("UPDATE runs SET status = 'closed' WHERE id = 'old'")
    record_methods(conn, "old", "Pick a colour", old.entries())
    embedder = FakeEmbedder()
    await embed_missing(conn, embedder)
    seen = FakeModel([json.dumps({"action": {"type": "pass"}})])
    runner = runner_factory(store, conn, {"d": seen}, max_rounds=1)
    runner.embedder = embedder
    await runner.run(runner.start("Pick a colour", ["d"]))
    assert "# Methods from earlier runs" not in seen.calls[0][1][0]["content"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_library.py tests/test_runner.py -q -k "no_limit or control_library"`
Expected: FAIL (`top_k=None` raises `TypeError`; the control sees its own method).

- [ ] **Step 3: Implement `retrieve(top_k=None)`**

In `src/bikeshed/library.py`, change the signature to `top_k: int | None` and the body's first check and last line to:

```python
    if top_k is not None and top_k <= 0:
        return []
    ...
    ranked = sorted(scored, key=lambda pair: pair[1], reverse=True)
    return ranked if top_k is None else ranked[:top_k]
```

- [ ] **Step 4: Give each agent its own list**

In `src/bikeshed/phases.py`, add `Mapping` to `from collections.abc import Awaitable, Callable` and change `library: list[str]` to:

```python
    library: Mapping[str, list[str]]  # agent id -> methods from earlier runs, rendered for its View
```

In `src/bikeshed/runner.py`, replace `_library` with:

```python
    async def _library(self, run_id: str, goal: str, agent_ids: Sequence[str],
                       events: EventLog) -> dict[str, list[str]]:
        """Methods from earlier runs most similar to the goal, rendered per agent for the View. Never fails the run.

        A control agent's list leaves out methods it wrote itself and fills back up from other members' methods."""
        empty: dict[str, list[str]] = {agent_id: [] for agent_id in agent_ids}
        if self.embedder is None:
            return empty
        k = self.library_top_k
        try:
            ranked = [] if k <= 0 else [m for m, _ in await retrieve(self.conn, self.embedder, goal, None,
                                                                      exclude_run=run_id)]
        except ModelError as exc:
            events.emit("library_unavailable", detail=str(exc))
            return empty
        common = ranked[:k]
        chosen = {agent_id: ([m for m in ranked if m.author != agent_id][:k] if self.store.record(agent_id).control
                             else common) for agent_id in agent_ids}
        own = {agent_id: [m.id for m in methods] for agent_id, methods in chosen.items() if methods != common}
        events.emit("library_retrieved", methods=[m.id for m in common], **({"control_methods": own} if own else {}))
        who = make_who({i: self.store.public(i) for i in self.store.ids()})
        return {agent_id: render_methods(methods, who) for agent_id, methods in chosen.items()}
```

In `run()`, change `library = await self._library(run_id, goal, events)` to `library = await self._library(run_id, goal, agent_ids, events)`. In `_turn`, change `library=ctx.library,` to `library=ctx.library.get(agent_id, []),`.

Check for any other reader of `ctx.library`: `grep -rn "\.library\b" src/bikeshed tests` and update test code that builds a `RoundContext` directly to pass a mapping (`{}` where it passed `[]`).

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_library.py tests/test_runner.py tests/test_process2.py -q`
Expected: PASS (`test_runs_show_relevant_methods_from_earlier_runs` still passes: a non-control agent's list is unchanged).
Run: `uv run pytest -q`
Expected: PASS, golden recording unchanged.

- [ ] **Step 6: Commit**

```bash
git add src/bikeshed/library.py src/bikeshed/runner.py src/bikeshed/phases.py tests/test_library.py tests/test_runner.py
git commit -m "fix: a control's library list leaves out its own methods and fills up from others'

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: The control and its twin always hold a private brief

**Files:**
- Modify: `src/bikeshed/briefs.py` (`assign`, lines 73–84)
- Modify: `src/bikeshed/runner.py` (`start`, line 103)
- Test: `tests/test_briefs.py`, `tests/test_briefs_run.py`

**Interfaces:**
- Consumes: `ProfileStore.twins() -> dict[str, list[str]]` (Task 3).
- Produces: `assign(spec, agent_ids, seed, protected: Sequence[str] = ()) -> RunBriefs`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_briefs.py`:

```python
FIVE = BriefSpec("Shared.", (), tuple((f"Brief {i}.", ()) for i in range(1, 6)))
SIX_AGENTS = ["a", "b", "c", "d", "e", "f"]


def holders(briefs):
    return {b.agent_id for b in briefs.private}


def test_protected_members_always_hold_a_brief_when_there_are_too_few():
    unprotected_misses = 0
    for seed in range(200):
        briefs = assign(FIVE, SIX_AGENTS, seed, protected=("e", "f"))
        assert {"e", "f"} <= holders(briefs), seed
        unprotected_misses += not {"e", "f"} <= holders(assign(FIVE, SIX_AGENTS, seed))
    assert unprotected_misses > 0  # the plain shuffle does leave them out; the guard is what fixes it


def test_protection_changes_nothing_when_everyone_holds_a_brief():
    six = BriefSpec("Shared.", (), tuple((f"Brief {i}.", ()) for i in range(1, 7)))
    for seed in range(50):
        assert assign(six, SIX_AGENTS, seed, protected=("e", "f")) == assign(six, SIX_AGENTS, seed)


def test_protection_keeps_the_assignment_when_protected_members_already_hold_briefs():
    for seed in range(200):
        plain = assign(FIVE, SIX_AGENTS, seed)
        if {"e", "f"} <= holders(plain):
            assert assign(FIVE, SIX_AGENTS, seed, protected=("e", "f")) == plain
```

Append to `tests/test_briefs_run.py` (read the top of that file first for its imports and helpers; this test uses only fixtures from `conftest.py`):

```python
def test_start_protects_the_control_and_its_twin(community, runner_factory, monkeypatch):
    from bikeshed import runner as runner_module
    from bikeshed.briefs import BriefSpec, assign
    from bikeshed.briefs import load as load_briefs

    store, conn = community
    store.create("d", "fake", "Temperament of a.", control=True)  # a's twin: same seed temperament
    store.set_public("d", "Dee", "Dee headline", "I am Dee.")
    store.save_seed_public("d")
    spec = BriefSpec("Shared.", (), (("One.", ()), ("Two.", ())))
    agents = ["a", "b", "c", "d"]
    seed = next(s for s in range(1000) if not {"a", "d"} <= {b.agent_id for b in assign(spec, agents, s).private})
    monkeypatch.setattr(runner_module.secrets, "randbits", lambda bits: seed)
    run_id = runner_factory(store, conn, {}).start("G", agents, brief=spec)
    assert {b.agent_id for b in load_briefs(conn, run_id).private} == {"a", "d"}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_briefs.py tests/test_briefs_run.py -q -k "protect"`
Expected: FAIL, `TypeError: assign() got an unexpected keyword argument 'protected'`.

- [ ] **Step 3: Implement the guard**

In `src/bikeshed/briefs.py`, replace `assign` with:

```python
def assign(spec: BriefSpec, agent_ids: Sequence[str], seed: int, protected: Sequence[str] = ()) -> RunBriefs:
    """Number the constraints and hand private brief k to the k-th member of a seeded shuffle.

    With fewer briefs than members, a protected member (a control agent or its twin) is never left without one:
    it swaps places with the last unprotected member that still holds a brief."""
    if len(spec.private) > len(agent_ids):
        raise ValueError(too_many(len(spec.private), len(agent_ids)))
    shared = None if spec.shared is None else Brief(
        0, None, spec.shared, tuple(Constraint(f"s.c{i}", t) for i, t in enumerate(spec.shared_constraints, 1)))
    order = list(agent_ids)
    random.Random(f"{seed}-briefs").shuffle(order)
    held = len(spec.private)
    for i in range(held, len(order)):
        if order[i] not in protected:
            continue
        j = next((j for j in range(held - 1, -1, -1) if order[j] not in protected), None)
        if j is None:
            break  # more protected members than briefs: the rest go without
        order[i], order[j] = order[j], order[i]
    private = tuple(
        Brief(k, order[k - 1], text, tuple(Constraint(f"p{k}.c{i}", t) for i, t in enumerate(constraints, 1)))
        for k, (text, constraints) in enumerate(spec.private, 1))
    return RunBriefs(shared, private)
```

- [ ] **Step 4: Pass the protected members from `Runner.start`**

In `src/bikeshed/runner.py`, in `start()`, just before `name_restored = ...`, add:

```python
        protected = self._protected(controls, agent_ids) if brief is not None else []
```

and replace `save_briefs(self.conn, run_id, assign(brief, agent_ids, seed))` with:

```python
                save_briefs(self.conn, run_id, assign(brief, agent_ids, seed, protected=protected))
```

Then add after `start()`:

```python
    def _protected(self, controls: Sequence[str], agent_ids: Sequence[str]) -> list[str]:
        """The run's control agents and their twins in the run: the pair a control comparison needs."""
        if not controls:
            return []
        twins = self.store.twins()
        return sorted({*controls, *(t for c in controls for t in twins.get(c, []) if t in agent_ids)})
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_briefs.py tests/test_briefs_run.py tests/test_runner.py -q`
Expected: PASS.
Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/bikeshed/briefs.py src/bikeshed/runner.py tests/test_briefs.py tests/test_briefs_run.py
git commit -m "feat: with fewer briefs than members, the control and its twin still hold one

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Generation 4 setup: six-member goals, spec wording, lab files

**Files:**
- Modify: `docs/goals/briefed-goals.toml`
- Modify: `tests/test_briefed_goals_file.py`
- Modify: `docs/superpowers/specs/2026-10-14-bikeshed-goal-briefs-design.md`
- Lab (gitignored, not committed): `lab/temperaments/f.md`, `lab/bikeshed.toml`

**Interfaces:** none (content and config).

The sixth briefs below are drafts for Andrew to approve. Each is a fact about the member's situation, text only, no constraint, so the Scorer's rules and every goal's satisfiability are unchanged.

- [ ] **Step 1: Update the goal-file test for a six-member lab**

In `tests/test_briefed_goals_file.py`, rename the test to `test_the_starter_set_is_a_valid_queue_for_a_six_member_lab` and replace `assert all(1 <= len(g.private) <= 5 for g in gf.goals[2:])` with:

```python
    assert [len(g.private) for g in gf.goals[2:]] == [6, 6, 6, 6]  # every member of a six-agent lab holds one
    assert not any("five of you" in (g.shared or "") for g in gf.goals)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_briefed_goals_file.py -q`
Expected: FAIL (`[5, 5, 5, 5] != [6, 6, 6, 6]`).

- [ ] **Step 3: Edit `docs/goals/briefed-goals.toml`**

- `offsite-briefed` shared text: `for the five of you` → `for the six of you`.
- `offsite-briefed` first private brief's constraint: `$1,600 for all five people` → `$1,920 for all six people` (keeps the $320-per-person limit that decides which venues fit).
- `office-thermostat` shared text: `The five of you share` → `The six of you share`.
- Add a sixth `[[goals.private]]` at the end of each briefed goal:

```toml
# picnic-briefed
[[goals.private]]
text = "You have a dentist appointment on Saturday morning and will get to the park at about 1:30pm."

# offsite-briefed
[[goals.private]]
text = "You checked the timetable: the last train back from the coast on a Thursday leaves at 6:10pm."

# film-night
[[goals.private]]
text = "You have to be up at 5am tomorrow for a flight."

# office-thermostat
[[goals.private]]
text = "You work from home on Mondays and Fridays, so you are in the office three days a week."
```

(The `# goal-id` lines above only show where each block goes; don't copy them into the file.)

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_briefed_goals_file.py tests/test_goals.py -q`
Expected: PASS.

- [ ] **Step 5: Fix the goal-briefs spec's numbering and archive path**

In `docs/superpowers/specs/2026-10-14-bikeshed-goal-briefs-design.md`: replace `Generation 1's runs are unaffected` with `Generation 3's runs are unaffected`, `generation 1 adopted norms` with `generation 3 adopted norms`, and `` `lab/archive/generation-{x}/` `` with `` `lab/generation-{x}/` ``. Check with `grep -n "generation" docs/superpowers/specs/2026-10-14-bikeshed-goal-briefs-design.md` that no "generation 1" remains.

- [ ] **Step 6: Commit the repo changes**

```bash
git add docs/goals/briefed-goals.toml tests/test_briefed_goals_file.py docs/superpowers/specs/2026-10-14-bikeshed-goal-briefs-design.md
git commit -m "docs: six-member briefed goals (drafted sixth briefs); goal-briefs spec says generation 3

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 7: Prepare the generation 4 lab (not committed; `lab/` is gitignored)**

Only after Andrew has approved the sixth briefs and confirmed `lab/` holds no live generation 4 community yet:

```bash
cp lab/temperaments/e.md lab/temperaments/f.md
grep -n "view_entries" lab/bikeshed.toml   # set it to 18 under [run]; add the line if it is missing
```

Report back what was changed. Do not run `init-agents.sh`, `bikeshed init` or any run: choosing the generation 4 model and starting it are Andrew's.

---

### Task 10: Whole-branch check

- [ ] **Step 1: Full suite and golden**

Run: `uv run pytest -q`
Expected: PASS, including `tests/test_process1_golden.py` without regeneration.

- [ ] **Step 2: Spec walk-through**

Read the spec's Testing section line by line and confirm each item has a passing test from Tasks 1–9. Note any gap in the final report rather than adding untested code.
