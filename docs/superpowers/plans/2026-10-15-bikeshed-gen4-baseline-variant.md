# Model-variant baseline (D2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `bikeshed baseline <run-id> --backend <name>` mirrors a closed run as a baseline does, with every agent on another backend, and the variant is labelled as one in its directory, its `baseline.toml`, the CLI output and the Observer's reports.

**Architecture:** `baseline.py` gains an optional `variant` backend name threaded through `check_backends`, `new_directory` and `prepare`; the CLI's `baseline` command gains `--backend` and reads it back from `baseline.toml` on `--resume`. Provenance needs no change, because it already records each scratch agent's backend. `observer/baselines.py` reads `backend` into `Baseline`, and `observer/report.py` prints it.

**Tech Stack:** Python 3.14, `uv`, Typer, pytest with `FakeModel`.

**Spec:** `docs/superpowers/specs/2026-10-15-bikeshed-gen4-baseline-variant-design.md` (extends `docs/superpowers/specs/2026-10-15-bikeshed-gen4-baseline-run-design.md`).

## Global Constraints

- A variant lives in `lab/baselines/<run-id>-v<k>/`, `k` counting up from 1 among that run's variants; baselines keep `-b<k>`.
- `baseline.toml` gains `backend = "<name>"`; a file without it is a plain baseline.
- The per-agent backend check is skipped for a variant; the Scorer check stays exactly as it is.
- Refusals, each through `_fail`: `<name>` isn't a backend in the config; `--backend` together with `--resume`; `<name>` is the backend every agent used in the source run ("that's a plain baseline, so drop `--backend`").
- Introductions use the variant backend.
- Nothing is written under the generation's `community/` or `runs/`.
- Observer: "## Baseline" gains a "Model" column ("as run" or the backend name); "## Baseline vs community" labels each "baseline" or "variant (`<name>`)".
- Before every commit: `pgrep -fl "bikeshed (run|postrun)"` must print nothing. If a lab run is live, stop and ask Andrew for a worktree (`../Bikeshed-variant`).
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Full suite: `uv run pytest -q` (about four minutes). Never set `BIKESHED_WRITE_GOLDEN`.

## Review Focus

1. A variant resumed after its backend was removed from the config should be refused with the "isn't in the config" message, never a `KeyError` (Task 1 test `test_variant_check_refuses_a_missing_backend` covers the shared path; Task 2's resume reads the backend before the check).
2. A source run whose agents were on *mixed* backends, some already on `<name>`, is a valid variant; only "every agent" on `<name>` is refused (Task 1 test `test_variant_check_allows_a_partly_matching_source`).
3. `record_run` must keep the `backend` key when it adds `baseline_run`, or a resumed variant silently turns into a plain baseline (Task 1 test `test_prepare_variant_records_its_backend`).
4. A baseline made by D (no `backend` key) must still read and print as a plain baseline (Task 3 test `test_find_reads_a_plain_baseline_without_backend`).
5. The CLI prints "variant …" rather than "baseline …" for a variant's closed/paused lines, so a terminal log can't pass a variant off as a baseline (Task 2 test `test_variant_runs_every_agent_on_the_given_backend`).

---

### Task 0: Branch

- [ ] **Step 1: Check for a live run and branch**

Run: `pgrep -fl "bikeshed (run|postrun)"; git checkout develop && git checkout -b baseline-variant`
Expected: pgrep prints nothing; `Switched to a new branch 'baseline-variant'`. If pgrep prints a process, stop and ask Andrew for a worktree.

### Task 1: Variant support in `baseline.py`

**Files:**
- Modify: `src/bikeshed/baseline.py` (`check_backends`, `new_directory`, `prepare`)
- Test: `tests/test_baseline.py`

**Interfaces:**
- Produces:
  - `check_backends(source: Source, cfg: Config, variant: str | None = None) -> None`
  - `new_directory(home_dir: Path, run_id: str, variant: bool = False) -> Path` (`-v<k>` when `variant`)
  - `prepare(directory: Path, source: Source, variant: str | None = None) -> ProfileStore` (agents on `variant`; `baseline.toml` gets `backend`)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_baseline.py`:

```python
def _think(cfg):
    cfg.models["think"] = cfg.models["local"].model_copy(update={"thinking": True})
    return cfg


def test_variant_check_skips_agent_backends_but_keeps_the_scorer(community, tmp_path):
    store, conn = community
    _closed(conn)
    _provenance(conn, "r1", "start", {}, scorer=("judge", {"kind": "openai", "base_url": "http://x/v1", "model": "J"}))
    cfg = _think(_cfg(tmp_path))
    cfg.models["judge"] = cfg.models["local"].model_copy(update={"model": "J"})
    cfg.scorer.judge = "judge"
    cfg.models["local"].model = "Other-Model"                  # the source backend changed since
    src = read_source(conn, store, "r1")
    with pytest.raises(BaselineError, match="Other-Model"):
        check_backends(src, cfg)                               # a plain baseline is refused
    check_backends(src, cfg, "think")                          # a variant isn't
    cfg.models["judge"] = cfg.models["local"].model_copy(update={"model": "Other-Judge"})
    with pytest.raises(BaselineError, match="Scorer"):
        check_backends(src, cfg, "think")


def test_variant_check_refuses_a_missing_backend(community, tmp_path):
    store, conn = community
    _closed(conn)
    _provenance(conn, "r1", "start", {})
    with pytest.raises(BaselineError, match="'nope' isn't in the config"):
        check_backends(read_source(conn, store, "r1"), _cfg(tmp_path), "nope")


def test_variant_check_refuses_the_backend_every_agent_used(community, tmp_path):
    store, conn = community
    _closed(conn)
    _provenance(conn, "r1", "start", {})                       # a, b and c all on "local"
    with pytest.raises(BaselineError, match="plain baseline, so drop --backend"):
        check_backends(read_source(conn, store, "r1"), _cfg(tmp_path), "local")


def test_variant_check_allows_a_partly_matching_source(community, tmp_path):
    store, conn = community
    _closed(conn)
    _provenance(conn, "r1", "start", {})
    src = read_source(conn, store, "r1")
    src.backends["a"] = "think"                                # a ran on think; b and c on local
    check_backends(src, _think(_cfg(tmp_path)), "think")


def test_new_directory_counts_variants_apart_from_baselines(tmp_path):
    home = tmp_path / "baselines"
    assert new_directory(home, "r1", variant=True).name == "r1-v1"
    (home / "r1-v1").mkdir(parents=True)
    assert new_directory(home, "r1", variant=True).name == "r1-v2"
    assert new_directory(home, "r1").name == "r1-b1"


def test_prepare_variant_records_its_backend(community, tmp_path):
    store, conn = community
    _closed(conn)
    _provenance(conn, "r1", "start", {})
    directory = new_directory(tmp_path / "baselines", "r1", variant=True)
    scratch = prepare(directory, read_source(conn, store, "r1"), "think")
    assert {scratch.record(a).model for a in ("a", "b", "c")} == {"think"}
    assert read_info(directory)["backend"] == "think"
    record_run(directory, "x123")                              # must keep the backend it found
    info = read_info(directory)
    assert (info["backend"], info["baseline_run"], info["source_run"]) == ("think", "x123", "r1")
    plain = prepare(new_directory(tmp_path / "baselines", "r1"), read_source(conn, store, "r1"))
    assert plain.record("a").model == "local" and "backend" not in read_info(tmp_path / "baselines" / "r1-b1")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_baseline.py -q -k "variant or counts_variants"`
Expected: FAIL — `TypeError` (unexpected argument `variant` / third positional argument) in each new test.

- [ ] **Step 3: Implement**

In `src/bikeshed/baseline.py`, replace `check_backends` with:

```python
def check_backends(source: Source, cfg: Config, variant: str | None = None) -> None:
    """Every agent runs on the backend it ran on then, with the same settings, or, for a variant, every agent runs on
    `variant`; and the Scorer is the same one either way, so rulings stay comparable."""
    if variant is not None:
        if variant not in cfg.models:
            raise BaselineError(f"backend {variant!r} isn't in the config")
        if all(source.backends[a] == variant for a in source.agent_ids):
            raise BaselineError(f"every agent ran on {variant!r} in run {source.run_id}: that's a plain baseline, "
                                "so drop --backend")
    else:
        for agent_id in source.agent_ids:
            name = source.backends[agent_id]
            backend = cfg.models.get(name)
            if backend is None:
                raise BaselineError(f"{agent_id} ran on backend {name!r}, which is no longer in the config")
            if changed := _changes(source.specs[agent_id], backend.model_dump(mode="json")):
                raise BaselineError(f"backend {name!r} changed since run {source.run_id}: {'; '.join(changed)}")
    if source.scorer is None:
        return
    if cfg.scorer.judge != source.scorer:
        raise BaselineError(f"the Scorer was {source.scorer!r} in run {source.run_id}, now {cfg.scorer.judge!r}")
    now = cfg.models.get(source.scorer)
    if source.scorer_spec and (now is None or (changed := _changes(source.scorer_spec, now.model_dump(mode="json")))):
        detail = "it is no longer in the config" if now is None else "; ".join(changed)
        raise BaselineError(f"the Scorer backend {source.scorer!r} changed since run {source.run_id}: {detail}")
```

Replace `new_directory` with:

```python
def new_directory(home_dir: Path, run_id: str, variant: bool = False) -> Path:
    """`<run-id>-b<k>` for a baseline, `<run-id>-v<k>` for a model variant, each counting up from 1."""
    letter = "v" if variant else "b"
    k = 1
    while (home_dir / f"{run_id}-{letter}{k}").exists():
        k += 1
    return home_dir / f"{run_id}-{letter}{k}"
```

Replace `prepare` with:

```python
def prepare(directory: Path, source: Source, variant: str | None = None) -> ProfileStore:
    """A fresh community holding the source run's agents as seeds: first temperament, no profile yet, each on the
    backend it ran on, or every one on `variant`."""
    store = ProfileStore(directory / "community")
    connect(directory / "community" / "community.db").close()
    (directory / "runs").mkdir(parents=True, exist_ok=True)
    for agent_id in source.agent_ids:
        store.create(agent_id, variant or source.backends[agent_id], source.temperaments[agent_id])
    info = {"source_run": source.run_id, "created_at": now_iso()}
    _write_info(directory, info | ({"backend": variant} if variant else {}))
    return store
```

Also extend the module docstring's first sentence with: "A model variant is the same with every agent on another backend."

- [ ] **Step 4: Run the file to verify it passes**

Run: `uv run pytest tests/test_baseline.py -q`
Expected: all pass (the existing tests unchanged).

- [ ] **Step 5: Commit**

```bash
pgrep -fl "bikeshed (run|postrun)"
git add src/bikeshed/baseline.py tests/test_baseline.py
git commit -m "feat(baseline): a model variant runs every agent on one backend

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 2: `bikeshed baseline --backend`

**Files:**
- Modify: `src/bikeshed/cli.py` (the `baseline` command, around lines 355–425)
- Modify: `CLAUDE.md:46` (command list)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `bl.check_backends(source, cfg, variant)`, `bl.new_directory(home, run_id, variant=bool)`, `bl.prepare(directory, source, variant)`, `bl.read_info(directory).get("backend")` from Task 1.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py` (the `project` fixture's config defines backends `local` and `claude`; agents are made on `local`, so `claude` is a valid variant backend):

```python
def _recording(monkeypatch):
    """Wraps cli.make_client so each call's backend model name is recorded."""
    used, working = [], cli.make_client

    def make(backend, retries):
        used.append(backend.model)
        return working(backend, retries)

    monkeypatch.setattr(cli, "make_client", make)
    return used


def test_variant_runs_every_agent_on_the_given_backend(project, monkeypatch):
    from bikeshed.db import connect
    from bikeshed.profiles import ProfileStore
    tmp_path, config = project
    make_agents(config, "a", "b")
    run_id = run_id_from(invoke("run", "Pick a colour", "--max-rounds", "2", "-c", config).output)
    before = _snapshot(tmp_path)
    used = _recording(monkeypatch)
    result = invoke("baseline", run_id, "--backend", "claude", "-c", config)
    assert result.exit_code == 0, result.output
    assert f"variant {run_id}-v1 closed" in result.output and f"baseline {run_id}-v1" not in result.output
    assert _snapshot(tmp_path) == before
    home = tmp_path / "baselines" / f"{run_id}-v1"
    assert 'backend = "claude"' in (home / "baseline.toml").read_text()
    assert {ProfileStore(home / "community").record(a).model for a in ("a", "b")} == {"claude"}
    assert used[:2] == ["haiku", "haiku"]                       # both introductions through the variant
    scratch = connect(home / "community" / "community.db")
    models = json.loads(scratch.execute("SELECT models FROM run_provenance WHERE phase = 'start'").fetchone()[0])
    assert {m["backend"] for m in models.values()} == {"claude"}
    assert invoke("baseline", run_id, "--backend", "claude", "-c", config).exit_code == 0
    assert (tmp_path / "baselines" / f"{run_id}-v2").is_dir()
    assert invoke("baseline", run_id, "-c", config).exit_code == 0
    assert (tmp_path / "baselines" / f"{run_id}-b1").is_dir()


def test_variant_refusals(project):
    tmp_path, config = project
    make_agents(config, "a", "b")
    run_id = run_id_from(invoke("run", "Pick a colour", "--max-rounds", "2", "-c", config).output)
    missing = invoke("baseline", run_id, "--backend", "nope", "-c", config)
    assert missing.exit_code == 1 and "'nope' isn't in the config" in missing.output
    same = invoke("baseline", run_id, "--backend", "local", "-c", config)
    assert same.exit_code == 1 and "plain baseline, so drop --backend" in same.output
    both = invoke("baseline", "--resume", f"{run_id}-v1", "--backend", "claude", "-c", config)
    assert both.exit_code == 1 and "drop --backend" in both.output
    assert not (tmp_path / "baselines").exists()


def test_variant_runs_after_the_source_backend_changed(project):
    tmp_path, config = project
    make_agents(config, "a", "b")
    run_id = run_id_from(invoke("run", "Pick a colour", "--max-rounds", "2", "-c", config).output)
    config.write_text(config.read_text().replace('model = "CHANGE-ME"', 'model = "Other-Model"', 1))
    assert invoke("baseline", run_id, "-c", config).exit_code == 1
    assert invoke("baseline", run_id, "--backend", "claude", "-c", config).exit_code == 0


def test_a_paused_variant_resumes_on_its_backend(project, monkeypatch):
    from bikeshed.db import connect
    tmp_path, config = project
    make_agents(config, "a", "b")
    run_id = run_id_from(invoke("run", "Pick a colour", "--max-rounds", "2", "-c", config).output)
    working = cli.make_client
    monkeypatch.setattr(cli, "make_client", lambda backend, retries: _Failing(
        working(backend, retries), lambda system, first: re.search(r"^# Round", first, re.M) is not None))
    paused = invoke("baseline", run_id, "--backend", "claude", "-c", config)
    assert paused.exit_code == 0 and f"variant {run_id}-v1 paused" in paused.output, paused.output
    monkeypatch.setattr(cli, "make_client", working)
    resumed = invoke("baseline", "--resume", f"{run_id}-v1", "-c", config)
    assert resumed.exit_code == 0 and f"variant {run_id}-v1 closed" in resumed.output, resumed.output
    scratch = connect(tmp_path / "baselines" / f"{run_id}-v1" / "community" / "community.db")
    models = json.loads(scratch.execute("SELECT models FROM run_provenance WHERE phase = 'resume'").fetchone()[0])
    assert {m["backend"] for m in models.values()} == {"claude"}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_cli.py -q -k variant`
Expected: FAIL — Typer reports `No such option: --backend` (exit code 2) in each test.

- [ ] **Step 3: Implement**

In `src/bikeshed/cli.py`, change the `baseline` command's signature and body as follows (everything not shown stays as it is):

```python
@app.command()
def baseline(
    run_id: Annotated[str | None, typer.Argument(help="A closed community run to mirror.")] = None,
    resume: Annotated[str | None, typer.Option(help="Continue a baseline (its directory name, e.g. RUN-b1).")] = None,
    backend: Annotated[str | None, typer.Option(
        help="A model variant: every agent runs on this backend instead, labelled as a variant.")] = None,
    config: ConfigOpt = DEFAULT_PATH,
) -> None:
    """Re-run a closed run's goal, settings, seed and briefs in a fresh room: no norms, library, history or post-run."""
    cfg = _load(config)
    if (run_id is None) == (resume is None):
        _fail("give a run id or --resume BASELINE_ID, not both")
    if resume and backend:
        _fail("--resume keeps the backend the variant started with; drop --backend")
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
            backend = info.get("backend")
            source = bl.read_source(community, source_store, info["source_run"])
        else:
            source = bl.read_source(community, source_store, run_id)
            directory = bl.new_directory(bl.home(cfg), source.run_id, variant=backend is not None)
        bl.check_backends(source, cfg, backend)
    except (bl.BaselineError, ProfileError) as exc:
        _fail(str(exc))
    kind = "variant" if backend else "baseline"
    settings = bl.settings_for(source, cfg)
    scfg = bl.scratch_config(cfg, directory, settings)
    store = ProfileStore(scfg.community_dir) if resume else bl.prepare(directory, source, backend)
```

Then, in the rest of the command, change the three user-facing lines that start with `baseline {directory.name}` to use `kind`:

```python
            _fail(f"{kind} {directory.name} is already closed")
```
```python
            typer.echo(f"{kind} {directory.name} paused; resume with `bikeshed baseline --resume {directory.name}`")
```
```python
        typer.echo(f"{kind} {directory.name} closed: {result.reason}")
```

In `CLAUDE.md`, replace line 46 with:

```
uv run bikeshed baseline <run-id> [--backend <b>] | --resume <dir>   # same goal, settings, seed, briefs in a fresh room (lab/baselines/<run-id>-b<k>; --backend = model variant, -v<k>)
```

- [ ] **Step 4: Run the CLI tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -q -k "baseline or variant or resume"`
Expected: all pass, including D's existing baseline tests.

- [ ] **Step 5: Commit**

```bash
pgrep -fl "bikeshed (run|postrun)"
git add src/bikeshed/cli.py tests/test_cli.py CLAUDE.md
git commit -m "feat(cli): bikeshed baseline --backend runs a model variant

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 3: The Observer labels variants

**Files:**
- Modify: `src/bikeshed/observer/baselines.py` (`Baseline`, `find`)
- Modify: `src/bikeshed/observer/report.py` (`_baselines`, `_baseline_vs_community`, around lines 415–447)
- Modify: `docs/development/gen4-fixes.md` (development-order table: add row D2 after D)
- Test: `tests/test_observer_baselines.py`, `tests/test_observer_report.py`

**Interfaces:**
- Consumes: `baseline.toml`'s optional `backend` key, as Task 1's `prepare` writes it.
- Produces: `Baseline.backend: str | None` (last field, default `None`) and `Baseline.label -> str` ("baseline" or "variant (<name>)").

- [ ] **Step 1: Write the failing tests**

In `tests/test_observer_baselines.py`, give `make_baseline` a `backend=None` keyword and write it into the info file:

```python
def make_baseline(home, name, source, *, closed=True, recorded=True, backend=None):
```
and replace the `info = ...` line with:
```python
    info = (f'source_run = "{source}"\ncreated_at = "t"\n' + ('baseline_run = "x1"\n' if recorded else "")
            + (f'backend = "{backend}"\n' if backend else ""))
```

Then append:

```python
def test_find_reads_a_variant_backend(tmp_path):
    make_baseline(tmp_path / "baselines", "r1-v1", "r1", backend="qwen3-14b")
    (b,) = find(tmp_path / "baselines")
    assert (b.backend, b.label) == ("qwen3-14b", "variant (qwen3-14b)")


def test_find_reads_a_plain_baseline_without_backend(tmp_path):
    make_baseline(tmp_path / "baselines", "r1-b1", "r1")
    (b,) = find(tmp_path / "baselines")
    assert (b.backend, b.label) == (None, "baseline")
```

In `tests/test_observer_report.py`, give `_baseline` a variant option:

```python
def _baseline(source="r1", name="r1-b1", backend=None):
    run = RunInfo("x1", "Pick a fruit.", "best-fruit", "unsettled", False, "consensus", ("a", "b"), "t", "t")
    return Baseline(name, source, run, 2, 1, 1, 0, BenchmarkAnswer("x1", True, "Mango."),
                    {"a": Counter(propose=1), "b": Counter(vote=1)}, 3, backend)
```

Update the two existing assertions to the new format:
- in `test_run_report_shows_its_baselines`: `assert "| r1-b1 | as run | consensus | 2 | 1 | 1/0 | 3 |" in block and "Mango." in block`
- in `test_summary_compares_baseline_with_control_and_twin`: `assert "| r1-b1 (baseline) | a | 1 |" in block`

and append:

```python
def test_reports_label_a_variant():
    variant = _baseline(name="r1-v1", backend="qwen3-14b")
    run_text = render_run(section(baselines=[_baseline(), variant]))
    block = run_text.split("## Baseline\n")[1].split("\n## ")[0]
    assert "| r1-v1 | qwen3-14b | consensus |" in block and "| r1-b1 | as run | consensus |" in block
    assert "### r1-v1 answer (variant (qwen3-14b))" in block and "A variant runs every agent" in block
    summary = render_summary(summary_section(
        twins={"a": ["b"]}, baselines={"r1": [_baseline(), variant]},
        rooms={"r1": (RUN, BenchmarkAnswer("r1", False, "- Alpha (a): Pear"), {"a": Counter(propose=2)})}))
    block = summary.split("## Baseline vs community\n")[1].split("\n## ")[0]
    assert "- r1-v1 (variant (qwen3-14b)): consensus, 2 rounds" in block and "- r1-b1 (baseline): consensus" in block
    assert "| r1-v1 (variant (qwen3-14b)) | a | 1 |" in block
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_observer_baselines.py tests/test_observer_report.py -q -k "baseline or variant"`
Expected: FAIL — `AttributeError: 'Baseline' object has no attribute 'backend'` / `TypeError` (too many positional arguments) and the changed row assertions.

- [ ] **Step 3: Implement**

In `src/bikeshed/observer/baselines.py`, add the field and property to `Baseline` (after `copied: int`):

```python
    backend: str | None = None   # a model variant's backend; None for a plain baseline

    @property
    def label(self) -> str:
        return f"variant ({self.backend})" if self.backend else "baseline"
```

and pass it in `find`'s constructor call, replacing the final argument line:

```python
                            len(copies(conn, run, directory / "runs")), info.get("backend")))
```

Update the module docstring to: `"""Baseline runs and model variants, read only: each scratch room under <lab>/baselines/ that mirrors a closed community run."""`

In `src/bikeshed/observer/report.py`, replace `_baselines` with:

```python
def _baselines(s: RunSection) -> list[str]:
    if not s.baselines:
        return []
    note = "_The same goal, settings, seed and briefs in a fresh room: no norms, library or history."
    if any(b.backend for b in s.baselines):
        note += " A variant runs every agent on the backend in its Model column."
    out = ["## Baseline", "", note + "_", "",
           "| Baseline | Model | Ending | Rounds | Calls | Rulings (accepted/rejected) | Copied |",
           "|---|---|---|---|---|---|---|"]
    out += [f"| {b.id} | {b.backend or 'as run'} | {b.run.ended_reason or 'unknown'} | {b.rounds} | {b.calls} | "
            f"{b.accepted}/{b.rejected} | {b.copied} |" for b in s.baselines]
    for b in s.baselines:
        out += ["", f"### {b.id} answer ({b.label})", "", b.answer.text]
    return out + [""]
```

In `_baseline_vs_community`, change the two lines that print a baseline:

```python
            out += [f"- {b.id} ({b.label}): {b.run.ended_reason or 'unknown'}, {b.rounds} rounds; answer:", "",
                    b.answer.text, ""]
```
```python
                out.append(f"| {b.id} ({b.label}) | {a} | "
                           + " | ".join(str(b.mix.get(a, Counter()).get(x, 0)) for x in TWIN_ACTIONS) + " |")
```

In `docs/development/gen4-fixes.md`, add this row to the development-order table directly after row D:

```
| D2 | Model-variant baseline | Fix 6 extended: `bikeshed baseline --backend` | No (separate scratch community) | **In progress** |
```

- [ ] **Step 4: Run the Observer tests, then the full suite**

Run: `uv run pytest tests/test_observer_baselines.py tests/test_observer_report.py tests/test_observer_isolation.py -q`
Expected: all pass.

Run: `uv run pytest -q 2>&1 | tail -3`
Expected: `N passed` with no failures (816 before this plan, plus the new tests).

- [ ] **Step 5: Commit**

```bash
pgrep -fl "bikeshed (run|postrun)"
git add src/bikeshed/observer/baselines.py src/bikeshed/observer/report.py tests/test_observer_baselines.py tests/test_observer_report.py docs/development/gen4-fixes.md
git commit -m "feat(observer): label model variants in the baseline sections

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
