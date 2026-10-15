# Process 3 and Naming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Process 3 (Process 2's rounds on briefed goals) a real `--process` value for generation 4, and retire the "Process 4" label everywhere.

**Architecture:** `phases.meeting_rounds(process)` becomes the one test for "Process 2's rounds", true for 2 and 3, and every `process == 2` branch in the runner, Observer and watch TUI uses it. `Runner.start` accepts 3 and refuses an unbriefed non-benchmark goal under it. The Observer tags runs `(P{n})`, summarises each meeting-rounds process separately, and names its brief sections after briefs. A text task renames "Process 4" in docs, docstrings and tests.

**Tech Stack:** Python 3.14, uv, pytest (asyncio auto mode), Typer, Pydantic, SQLite.

**Spec:** `docs/superpowers/specs/2026-10-15-bikeshed-process3-design.md` (background: `docs/nomenclature-cleanup.md`).

## Global Constraints

- Work on a feature branch off `develop`. Before committing, check `pgrep -fl "bikeshed (run|postrun)"`; if a lab run is live, stop. Never touch `lab/`.
- Processes 1 and 2 must behave exactly as before. The Process 1 golden recording (`tests/golden/`) must not change; never set `BIKESHED_WRITE_GOLDEN=1`.
- Process 3 adds no prompt and no phase: it uses Process 2's phases and `process2_*.md` templates.
- Observation never feeds back: no agent-path module imports `bikeshed.observer` or `bikeshed.watch` (isolation tests).
- Refusal text, verbatim: `process must be 1, 2 or 3`; `--process must be 1, 2 or 3`; `process 3 runs briefed goals and this goal has none; only the benchmark runs unbriefed (use --process 2 for an unbriefed goal)`.
- Observer headings, verbatim: per-run `## Process {n}`; summary `## Process {n}` per meeting-rounds process; `## Briefs`; `## Briefed runs`. Tags: `label (P{n})` for n ≠ 1.
- Run `uv run pytest -q` (~4 minutes). Every task ends with the full suite green.
- Commit messages end with a `Co-Authored-By:` trailer naming the model that wrote the commit.
- Match the surrounding code: docstrings one or two plain sentences, rare comments.

## Review Focus

- `bikeshed run "<free text>"` in a lab set to `process = 3`: refused with the message that names `--process 2`, and no run row is created (Task 1, `test_process_3_needs_briefs_except_for_the_benchmark` and the CLI test).
- `run --next` choosing the pizza benchmark under process 3: it starts unbriefed (Task 1, same test, `is_benchmark=True`).
- An Observer summary over generation 3 (process 2) and generation 4 (process 3) runs together: the two are never summed into one table (Task 2, `test_summary_has_one_section_per_meeting_process`).
- A briefed run under process 2 in the Observer: it still appears under "## Briefed runs" with a `(P2)` category key (Task 2, updated `test_observer_briefs` summary test).
- The watch TUI on a live process 3 run: it shows the thinking and private panes and the think/react/poll states as it does for process 2 (Task 2, watch state test).

---

### Task 1: Process 3 in the harness

**Files:**
- Modify: `src/bikeshed/phases.py` (add `meeting_rounds`; `phases()` lines 118–123)
- Modify: `src/bikeshed/config.py:36`
- Modify: `src/bikeshed/runner.py` (`start` lines 76–77; `_turn` lines 214 and 226; import)
- Modify: `src/bikeshed/cli.py` (`--process` help line 267; check lines 278–279)
- Test: `tests/test_runner.py` (`test_start_records_the_process`, ~line 368; new refusal test), `tests/test_process2.py`, `tests/test_cli.py` (`test_run_with_process_2_records_it_and_resume_keeps_it`; new test)

**Interfaces:**
- Produces: `phases.meeting_rounds(process: int) -> bool` (True for 2 and 3). `Runner.start(..., process=3, ...)` accepted; refuses `brief is None and not is_benchmark` under 3.

- [ ] **Step 1: Write the failing tests**

In `tests/test_runner.py`, replace the last two lines of `test_start_records_the_process` with:

```python
    from bikeshed.briefs import BriefSpec

    assert conn.execute("SELECT process FROM runs WHERE id = ?",
                        (runner.start("G", AGENTS, process=3, brief=BriefSpec("Shared.")),)).fetchone()[0] == 3
    with pytest.raises(ValueError, match="process must be 1, 2 or 3"):
        runner.start("G", AGENTS, process=4)
```

and add after it:

```python
def test_process_3_needs_briefs_except_for_the_benchmark(community, runner_factory):
    from bikeshed.briefs import BriefSpec

    store, conn = community
    runner = runner_factory(store, conn, {})
    with pytest.raises(ValueError, match="process 3 runs briefed goals and this goal has none"):
        runner.start("G", AGENTS, process=3)
    assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
    assert runner.start("Pick a topping.", AGENTS, process=3, is_benchmark=True)
    assert runner.start("G", AGENTS, process=3, brief=BriefSpec("Shared."))
    for process in (1, 2):  # briefs stay allowed under the other processes
        assert runner.start("G", AGENTS, process=process, brief=BriefSpec("Shared."))
```

Append to `tests/test_process2.py`:

```python
async def test_process_3_runs_process_2_rounds_and_records_3(tmp_path, community, process2, runner_factory):
    from bikeshed.briefs import BriefSpec

    store, conn = community
    shape, steps, run_ids = {}, {}, {}
    for process in (2, 3):
        clients = {a: process2(react={1: propose(a.upper())}) for a in AGENTS}
        runner = runner_factory(store, conn, clients, max_rounds=2)
        run_ids[process] = runner.start("G", AGENTS, process=process, brief=BriefSpec("Shared facts."))
        await runner.run(run_ids[process])
        shape[process] = [(r["round"], r["author"], r["type"]) for r in conn.execute(
            "SELECT round, author, type FROM entries WHERE run_id = ? ORDER BY id", (run_ids[process],))]
        steps[process] = [(e["round"], e["agent"], e.get("phase"))
                          for e in read_events(tmp_path / "runs" / run_ids[process] / "events.jsonl")
                          if e["event"] == "agent_step_started"]
    assert shape[3] == shape[2] and steps[3] == steps[2]
    assert any(phase == "think" for _, _, phase in steps[3]) and any(t == "think" for _, _, t in shape[3])
    assert conn.execute("SELECT process FROM runs WHERE id = ?", (run_ids[3],)).fetchone()[0] == 3
```

In `tests/test_cli.py`, in `test_run_with_process_2_records_it_and_resume_keeps_it`, change its last line's `"--process", "3"` to `"--process", "4"`, and append:

```python
def test_process_3_refuses_a_free_text_goal(project):
    tmp_path, config = project
    make_agents(config, "a")
    result = invoke("run", "Pick a colour", "--process", "3", "-c", config)
    assert result.exit_code != 0
    assert "process 3 runs briefed goals and this goal has none" in result.output and "--process 2" in result.output
    rejected = invoke("run", "Pick a colour", "--process", "4", "-c", config)
    assert rejected.exit_code != 0 and "--process must be 1, 2 or 3" in rejected.output
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_runner.py tests/test_process2.py tests/test_cli.py -q -k "process"`
Expected: FAIL (`process must be 1 or 2` raised for 3).

- [ ] **Step 3: Add `meeting_rounds` and use it in `phases()`**

In `src/bikeshed/phases.py`, before `def phases`:

```python
def meeting_rounds(process: int) -> bool:
    """Processes 2 and 3 run each round as think, then react in turn, then a poll. They differ only in their goals."""
    return process in (2, 3)
```

and in `phases()` change `if process == 2:` to `if meeting_rounds(process):`.

- [ ] **Step 4: Accept 3 and refuse unbriefed goals**

`src/bikeshed/config.py:36`:

```python
    process: Literal[1, 2, 3] = 1  # 1: all act at once; 2: think, then react in turn (with a poll); 3: as 2, on briefed goals
```

`src/bikeshed/runner.py`: add `meeting_rounds` to `from bikeshed.phases import RoundContext, phases`. In `start`, replace the two process lines with:

```python
        if process not in (1, 2, 3):
            raise ValueError("process must be 1, 2 or 3")
        if process == 3 and brief is None and not is_benchmark:
            raise ValueError("process 3 runs briefed goals and this goal has none; only the benchmark runs unbriefed "
                             "(use --process 2 for an unbriefed goal)")
```

In `_turn`, change `max_rounds=self.settings.max_rounds if ctx.process == 2 else None,` to `max_rounds=self.settings.max_rounds if meeting_rounds(ctx.process) else None,` and `if ctx.process == 2 and phase is None` to `if meeting_rounds(ctx.process) and phase is None`. Leave the two `ctx.process == 1` checks as they are.

`src/bikeshed/cli.py`: the `--process` help becomes `"1: everyone acts at once (default). 2: think, then react in turn, with a poll. 3: as 2, on a goal with briefs (only the benchmark runs unbriefed)."`, and the check becomes:

```python
    if process is not None and process not in (1, 2, 3):
        _fail("--process must be 1, 2 or 3")
```

(The free-text refusal reaches the CLI through `runner.start`'s `ValueError`, which `run` already turns into `_fail`.)

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_runner.py tests/test_process2.py tests/test_cli.py -q`
Expected: PASS.
Run: `uv run pytest -q`
Expected: PASS, golden unchanged.

- [ ] **Step 6: Commit**

```bash
git add src/bikeshed/phases.py src/bikeshed/config.py src/bikeshed/runner.py src/bikeshed/cli.py tests/test_runner.py tests/test_process2.py tests/test_cli.py
git commit -m "feat: process 3 runs Process 2's rounds on briefed goals

Co-Authored-By: <model that wrote this> <noreply@anthropic.com>"
```

---

### Task 2: Observer and watch TUI treat process 3 as its own group

**Files:**
- Modify: `src/bikeshed/observer/metrics.py` (`RunInfo.tag` line 39; `by_category` line 227)
- Modify: `src/bikeshed/observer/observe.py` (line 61; `_process2_section` line 99; summary block lines 277–296)
- Modify: `src/bikeshed/observer/report.py` (`_briefs` heading line 61; `_process2` heading line 184; think-vs-act line 229; `Process2Summary` comments lines 258–261; `SummarySection` lines 289–291; `render_summary` lines 364–389)
- Modify: `src/bikeshed/watch/state.py` (line 148 comment; line 183), `src/bikeshed/watch/app.py:118`
- Test: `tests/test_observer_report.py`, `tests/test_observer_briefs.py`, `tests/test_observer_observe.py`, `tests/test_watch_state.py`

**Interfaces:**
- Consumes: `bikeshed.phases.meeting_rounds(process) -> bool` (Task 1).
- Produces: `SummarySection.meetings: dict[int, Process2Summary]` (replaces `process2`); `SummarySection.briefed: list[RunStat]` (replaces `process4`). `RunSection.process2` keeps its name.

- [ ] **Step 1: Write the failing tests**

In `tests/test_observer_report.py`, add after `P2 = replace(RUN, process=2)`:

```python
P3 = replace(RUN, process=3)
```

change the two `SummarySection(..., process2=Process2Summary(...))` calls (in `test_summary_process_2_section` and `test_poll_and_order_tables_say_what_they_count`) to pass `meetings={2: Process2Summary(...)}` with the same arguments, and append:

```python
def test_process_3_run_report_and_tag():
    text = render_run(section(run=P3, process2=P2_SECTION, judgments=[themes("a"), tva("a")]))
    assert "## Process 3\n" in text and "## Process 2" not in text
    assert "## Think notes vs actions (private vs public)" in text
    assert P3.tag == "best-fruit (P3)" and P2.tag == "best-fruit (P2)" and RUN.tag == "best-fruit"


def test_summary_has_one_section_per_meeting_process():
    s = summary_section(runs=[P2, P3], meetings={
        2: Process2Summary([RecurringPair("a", "b", 2, 5)], {}, []),
        3: Process2Summary([RecurringPair("b", "a", 3, 7)], {}, [])})
    text = render_summary(s)
    two = text.split("## Process 2\n")[1].split("## Process 3\n")[0]
    three = text.split("## Process 3\n")[1]
    assert "Alpha (a) → Bea (b): 2 runs" in two and "Bea (b) → Alpha (a)" not in two
    assert "Bea (b) → Alpha (a): 3 runs" in three and "Summed over all Process 3 runs" in three
```

In `tests/test_observer_briefs.py`: change `"## Briefs (Process 4)"` to `"## Briefs\n"`; in the summary test change every `summary_section(process4=...)` to `summary_section(briefed=...)`, change `"## Process 4"` to `"## Briefed runs"`, and add to its end:

```python
    p3 = render_summary(summary_section(briefed=[dataclasses.replace(other, tag="r5", process=3)]))
    assert "| distributed (P3) | 1 | 2 | 2/3 | 0 | 2 | – | 3.5 |" in p3
```

In `tests/test_observer_observe.py`, append to the Process 2 observe test (the one asserting `"debate (P2)" in summary`):

```python
    add_run(conn, "r6", created=FUTURE, goal_id="tidy", process=3)
    add_entry(conn, "r6", "a", "think", {"text": "Tidy first."}, round=1, visible_to=["a"])
    add_entry(conn, "r6", "a", "propose", {"answer_summary": "Tidy", "method": "m"}, round=1)
    await observe(lab, judge_client=FakeModel(judge_reply))
    assert "## Process 3" in (lab.observer.dir / "runs" / "r6.md").read_text()
    summary = (lab.observer.dir / "summary.md").read_text()
    assert "tidy (P3)" in summary and "## Process 3" in summary and "## Process 2" in summary
```

(If `FUTURE` makes `r6` sort oddly against `r5`, give it any later timestamp string in the same format.)

In `tests/test_watch_state.py`, append:

```python
def test_process_3_steps_set_the_phase_like_process_2():
    s = RunState("r1", "g", "r1", "ad hoc", 12, process=3)
    s.apply({"event": "run_started", "agents": ["a"]})
    s.apply({"event": "agent_step_started", "round": 2, "agent": "a", "phase": "poll"})
    assert s.step_phase == "poll" and s.cards["a"].state == "polling…"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_observer_report.py tests/test_observer_briefs.py tests/test_observer_observe.py tests/test_watch_state.py -q`
Expected: FAIL (`meetings`/`briefed` unknown, `(P2)` tags for process 3, old headings).

- [ ] **Step 3: Tags and category keys**

`src/bikeshed/observer/metrics.py`:

```python
        return self.label if self.process == 1 else f"{self.label} (P{self.process})"
```

and in `by_category`: `key = run.category if run.process == 1 else f"{run.category} (P{run.process})"`.

- [ ] **Step 4: Observe**

`src/bikeshed/observer/observe.py`: import `from bikeshed.phases import meeting_rounds`. Line 61: `if meeting_rounds(run.process) and (tva := ...)`. `_process2_section`: `if not meeting_rounds(run.process): return None`. Replace the `p2_runs = ...` / `process2 = Process2Summary(...) if p2_runs else None` block with:

```python
    meetings: dict[int, Process2Summary] = {}
    for process in sorted({r.process for r in runs if meeting_rounds(r.process)}):
        group = [r for r in runs if r.process == process]  # never sum one generation's rounds into another's
        meetings[process] = Process2Summary(
            p2.recurring_pairs(conn, group),
            p2.combine_participation([p2.poll_participation(conn, r) for r in group]),
            p2.combine_order([p2.speaking_order(conn, r) for r in group]),
        )
```

and in the `SummarySection(...)` call pass `meetings=meetings` instead of `process2=process2`, and `briefed=[...]` instead of `process4=[...]`.

- [ ] **Step 5: Report**

`src/bikeshed/observer/report.py`:
- import `from bikeshed.phases import meeting_rounds`;
- `_briefs`: `out = ["## Briefs", "", "### Scorer call-outs", ""]`;
- `_process2`: `out = [f"## Process {s.run.process}", "", "### Private messages", ""]`;
- line 229: `if meeting_rounds(r.process):`;
- `Process2Summary` comments: `# summed over one process's runs` (both lines);
- `RunSection.process2` comment: `# only for runs with Process 2's rounds (processes 2 and 3)`;
- `SummarySection`: replace the `process2` field with `meetings: dict[int, Process2Summary] = field(default_factory=dict)  # process -> summary; processes are never summed together`, and rename `process4` to `briefed` (same type and comment);
- `render_summary`: `if s.briefed:` … `"## Briefed runs"` … `for r in s.briefed` … `rows = [r for r in s.briefed if ...]`, key lambda `r.category if r.process == 1 else f"{r.category} (P{r.process})"`; replace the `if s.process2 is not None:` block with:

```python
    for process, p in sorted(s.meetings.items()):
        out += ["", f"## Process {process}", "", "### Recurring private-message pairs", ""]
        out += [f"- {s.who(r.sender)} → {s.who(r.recipient)}: {r.runs} runs, {r.messages} messages" for r in p.pairs] \
            or ["_None yet._"]
        out += [""] + _participation(p.participation, s.who) + _order(
            p.order, f" Summed over all Process {process} runs, whatever their size.")
```

- [ ] **Step 6: Watch TUI**

`src/bikeshed/watch/state.py`: import `from bikeshed.phases import meeting_rounds`; field comment `# 2 and 3: think, then react in turn, with a poll`; line 183 `if meeting_rounds(self.process):`. `src/bikeshed/watch/app.py`: import the same and `two = meeting_rounds(s.process)`.

- [ ] **Step 7: Run the tests**

Run: `uv run pytest tests/test_observer_report.py tests/test_observer_briefs.py tests/test_observer_observe.py tests/test_watch_state.py tests/test_watch_app.py tests/test_observer_isolation.py tests/test_watch_isolation.py -q`
Expected: PASS.
Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/bikeshed/observer/metrics.py src/bikeshed/observer/observe.py src/bikeshed/observer/report.py src/bikeshed/watch/state.py src/bikeshed/watch/app.py tests/test_observer_report.py tests/test_observer_briefs.py tests/test_observer_observe.py tests/test_watch_state.py
git commit -m "feat: the Observer and watch treat process 3 as its own group; brief sections are named after briefs

Co-Authored-By: <model that wrote this> <noreply@anthropic.com>"
```

---

### Task 3: Retire "Process 4" in docs, docstrings and the archive path

**Files:**
- Modify: `docs/superpowers/specs/2026-10-14-bikeshed-goal-briefs-design.md` (title, line 3, archive note ~line 163)
- Modify: `docs/superpowers/plans/2026-10-14-bikeshed-goal-briefs.md` (one line at the top)
- Modify: `CLAUDE.md` (the "Process 4 (goal briefs)" bullet under "A run")
- Modify: `README.md:120`, `docs/goals/briefed-goals.toml:1`, `docs/gen4-fixes.md` (lines 129, 151, 169), `docs/nomenclature-cleanup.md` (top)
- Modify: `src/bikeshed/briefs.py:1`, `src/bikeshed/observer/briefs.py:1`, `src/bikeshed/postrun/feedback.py:77`

**Interfaces:** none (text only).

- [ ] **Step 1: Goal-briefs spec**

- Title: `# Process 4: goal briefs` → `# Goal briefs (Process 3)`.
- In line 3, replace the opening sentence `"Process 4" is this experiment's name, not a `--process` value: briefs belong to goals and run under Process 1 or 2, so goal content and phase order can vary separately.` with: `Briefs belong to goals and work under any process. Process 3, generation 4's process, is Process 2's rounds on briefed goals and is the one process that requires them (`2026-10-15-bikeshed-process3-design.md`).` Keep the rest of line 3.
- Out-of-scope note: `` `lab/generation-{x}/` `` → `` `lab/archive/generation-{x}/` ``.

- [ ] **Step 2: CLAUDE.md**

Replace the bullet's opening `- Process 4 (goal briefs) is a goal feature, not a `--process` value: ` with `- Goal briefs are a goal feature that works under any process; Process 3 (Process 2's rounds on briefed goals, generation 4) requires them and refuses an unbriefed goal other than the benchmark: `. Keep the rest of the bullet. Also update the `--process 1|2` line in the Commands block to `--process 1|2|3 (1 = everyone at once, default; 2 = think → react → poll; 3 = as 2 on briefed goals)`.

- [ ] **Step 3: The other mentions**

- `README.md:120`: `**Briefs (Process 4).**` → `**Briefs (Process 3).**`; if that paragraph says briefs run under Process 1 or 2, add that Process 3 requires them.
- `docs/goals/briefed-goals.toml:1`: `# Process 4 starter goals:` → `# Process 3 starter goals:`.
- `docs/gen4-fixes.md`: "Process 4" → "Process 3" at lines 129, 151 and 169.
- `src/bikeshed/briefs.py:1`: `"""Goal briefs (Process 4): ...` → `"""Goal briefs (Process 3 requires them): ...`.
- `src/bikeshed/observer/briefs.py:1`: `"""Process 4 for the Observer: ...` → `"""Goal briefs for the Observer: ...`.
- `src/bikeshed/postrun/feedback.py:77`: `# Process 4: after the run every brief is revealed` → `# briefed goals: after the run every brief is revealed`.
- `docs/superpowers/plans/2026-10-14-bikeshed-goal-briefs.md`: add as its second line `> This feature is now part of Process 3; see `docs/superpowers/specs/2026-10-15-bikeshed-process3-design.md`.`
- `docs/nomenclature-cleanup.md`: add under the title `*Done in sub-project F (`docs/superpowers/specs/2026-10-15-bikeshed-process3-design.md`). One item was resolved differently: the Observer's brief sections are named "Briefs" and "Briefed runs", not after Process 3, because briefs work under any process.*`

- [ ] **Step 4: Check nothing is left**

Run: `grep -rn "Process 4\|process 4" src tests docs README.md CLAUDE.md | grep -v "docs/superpowers/plans/2026-10-14-bikeshed-goal-briefs.md\|docs/nomenclature-cleanup.md\|docs/superpowers/specs/2026-10-15-bikeshed-process3-design.md\|docs/superpowers/plans/2026-10-15-bikeshed-process3.md"`
Expected: no output. Also `grep -n "generation 1\|lab/generation-" docs/superpowers/specs/2026-10-14-bikeshed-goal-briefs-design.md docs/gen4-fixes.md` shows no outgoing-generation "generation 1" and no `lab/generation-` path (fix 19's own title in `gen4-fixes.md` may quote "generation 1"; leave that quote).

- [ ] **Step 5: Run the suite and commit**

Run: `uv run pytest -q`
Expected: PASS.

```bash
git add docs/superpowers/specs/2026-10-14-bikeshed-goal-briefs-design.md docs/superpowers/plans/2026-10-14-bikeshed-goal-briefs.md CLAUDE.md README.md docs/goals/briefed-goals.toml docs/gen4-fixes.md docs/nomenclature-cleanup.md src/bikeshed/briefs.py src/bikeshed/observer/briefs.py src/bikeshed/postrun/feedback.py
git commit -m "docs: retire the Process 4 label; goal briefs belong to Process 3; archive path is lab/archive

Co-Authored-By: <model that wrote this> <noreply@anthropic.com>"
```

---

### Task 4: Whole-branch check

- [ ] **Step 1:** `uv run pytest -q` — PASS, golden unchanged.
- [ ] **Step 2:** Walk the spec's Testing section and confirm each line has a passing test from Tasks 1–3; report any gap rather than adding untested code.
