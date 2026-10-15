# Generation 4, sub-project F: Process 3 and naming

*2026-10-15. Implements `docs/nomenclature-cleanup.md`. Must land before generation 4's first run, because a run records its process when it starts and keeps it. Sub-project A (control integrity) is merged; B (agent environment), C (measurement) and D (baseline run) come after this.*

## Goal

Each process number matches one generation:

| Process | Generation | A working round |
|---|---|---|
| 1 | 1 and 2 | Everyone acts at once |
| 2 | 3 | A private note, then one speaker at a time in a shuffled order, with a poll |
| 3 | 4 | Process 2's rounds, on a goal with shared and private briefs |

"Process 4", the label the goal-briefs feature carried, was never a `--process` value and goes away. Process 3 is a real value: chosen with `--process 3` (or `[run] process = 3`), recorded in `runs.process` and provenance, and kept for the run's whole life. Every generation 4 run is then tagged 3, and the Observer separates generation 4 from generation 3 by process alone.

## Process 3 in the harness

- `RunSettings.process` becomes `Literal[1, 2, 3]`. `Runner.start` and `bikeshed run` accept 3, and refuse anything else with "process must be 1, 2 or 3". The `--process` help text describes all three.
- `phases.meeting_rounds(process: int) -> bool` is true for 2 and 3. Process 3 runs exactly Process 2's phases (think → react → poll) and prompts (`process2_*.md`). It adds no phase and no prompt, so Processes 1 and 2 behave exactly as before.
- Every check that means "Process 2's rounds" uses the helper instead of `process == 2`:
  - `runner.py`: the max-rounds hint in the view, the system template, the pre-write hook, and the step shape (`STEP` vs `STEP_P2`);
  - `phases.phases()`;
  - `watch/state.py` and `watch/app.py`.

  Checks for `process == 1` stay as they are.
- **Process 3 requires briefs.** `Runner.start(process=3)` refuses a goal with no brief (`brief is None`) unless `is_benchmark` is true. The error is "process 3 runs briefed goals and this goal has none; only the benchmark runs unbriefed (use --process 2 for an unbriefed goal)". The CLI exits through `_fail` with it. So in a lab set to process 3:
  - `run --next` runs briefed queue goals and the benchmark;
  - a free-text `run "<goal>"` is refused unless given `--process 2`.

  The pizza benchmark stays unbriefed on purpose. It is the cross-generation control goal.
- **Briefs under Processes 1 and 2 stay allowed.** Briefs work under any process; Process 3 is the one that requires them. The existing briefs tests keep running under 1 and 2.

## Observer and report

- `RunInfo.tag` and the by-category keys (`metrics.py:39`, `:227`, `report.py:373`) become `label (P{n})` for any process other than 1. Process 3 runs then form their own group instead of joining "(P2)".
- The analyses that exist for Process 2's rounds also run for process 3, chosen through `meeting_rounds`:
  - think-vs-act (`observe.py:61`, `report.py:229`);
  - the per-run section (`observe.py:99`);
  - the summary's recurring pairs, poll participation and speaking order (`observe.py:277`).
- **Per-run section heading.** It names the run's own process: "## Process 2" or "## Process 3".
- **Summary.** It builds one `Process2Summary` per meeting-rounds process present and renders each under its own heading ("## Process 2", "## Process 3"). Generation 3 and generation 4 are never summed together.
- **Brief sections are named after briefs.** Briefs can appear under any process, so these sections describe briefed runs:
  - "## Briefs (Process 4)" becomes "## Briefs";
  - the summary's "## Process 4" becomes "## Briefed runs";
  - `SummarySection.process4` becomes `briefed`.

## Text

"Process 4" → "Process 3" at each use listed in `docs/nomenclature-cleanup.md`:

- **Rewritten, not renumbered.** `docs/superpowers/specs/2026-10-14-bikeshed-goal-briefs-design.md` (title and line 3) and `CLAUDE.md` (the goal-briefs bullet) both say briefs are "not a `--process` value". The new wording: briefs are a goal feature that works under any process, and Process 3, Process 2's rounds on briefed goals, is the process that requires them.
- **Renumbered.**
  - `README.md` (the briefs bullet);
  - `docs/goals/briefed-goals.toml` line 1;
  - the docstrings in `src/bikeshed/briefs.py` and `src/bikeshed/observer/briefs.py`;
  - the comment in `src/bikeshed/postrun/feedback.py:77`;
  - `docs/gen4-fixes.md` lines 129, 151 and 169.
- **The headings tests.** `tests/test_observer_briefs.py` asserts the headings, so its assertions follow the new brief headings above.
- **Kept as a record.** `docs/superpowers/plans/2026-10-14-bikeshed-goal-briefs.md` keeps its text. It gets one line at the top: "This feature is now part of Process 3; see `docs/superpowers/specs/2026-10-15-bikeshed-process3-design.md`."
- **Generations and archive.** The goal-briefs spec's out-of-scope note says an old generation moves to `lab/archive/generation-{x}/`. That is where generation 3 is, and it corrects sub-project A's edit. No "generation 1" may remain for the outgoing generation in the spec, the plan or `gen4-fixes.md`.
- **The note itself.** `docs/nomenclature-cleanup.md` gets a status line at the top: "Done in sub-project F (`2026-10-15-bikeshed-process3-design.md`)". Its brief-heading item is marked as resolved differently: the headings are named after briefs.
- **Out of scope.** The report vault (`obsidian-fakinghuman/60. Platform/Reports/Bikeshed`) is handled on the report side.

## Lab (after merge, by hand)

`lab/bikeshed.toml` gets `process = 3` under `[run]`, together with sub-project E's `view_entries = 18`. These are Andrew's edits, made with the rest of the generation 4 setup.

## Testing

TDD with `FakeModel`.

- **Config and CLI.** `process = 3` loads; 4 is rejected with the new message by `Runner.start` and by the CLI. The runner test that rejected 3 now rejects 4.
- **Rounds.** A briefed process 3 run produces the same phase events and entry types as the same scripted run under process 2, and records `process = 3` in `runs` and provenance. Resume keeps 3.
- **Refusal.**
  - `start(process=3)` with no brief raises, and creates no run row.
  - With `is_benchmark=True` and no brief, it starts.
  - With a brief, it starts.
  - Processes 1 and 2 with a brief still start.
- **Observer.**
  - A process 3 run is tagged "(P3)" and grouped apart from "(P2)".
  - It gets the think-vs-act analysis and a "## Process 3" section.
  - With one P2 and one P3 run, the summary has separate Process 2 and Process 3 sections.
  - The brief headings read "## Briefs" and "## Briefed runs".
- **Watch TUI.** Its state treats process 3 like 2.
- **Golden.** The Process 1 recording is unchanged.
