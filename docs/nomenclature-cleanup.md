# Nomenclature cleanup: Process 3

*15 October 2026. Written from the report side while the goal-briefs work is in progress. Nothing here has been changed in the code yet; this is the list for whoever picks it up.*

## The decision

Each process number now matches one generation:

| Process | Generation | What a working round is |
|---|---|---|
| Process 1 | Generations 1 and 2 | Everyone acts at once |
| Process 2 | Generation 3 | A private note, then one speaker at a time in a shuffled order, with a poll |
| **Process 3** | **Generation 4** | **Process 2's rounds, run on a goal with shared and private briefs** |

"Process 4" was a mistake. It was used as a label for the goal-briefs feature and was never a `--process` value. It goes away.

Process 3 is a real process value, chosen with `--process 3` and kept by the run for its whole life, like 1 and 2. The point is that "process" keeps one meaning everywhere: the harness, the database, the Observer and the report. Every generation 4 run is then tagged `process = 3` in `runs` and in provenance, and the Observer, which already splits its tables by process, separates generation 4 from generation 3 without anything extra.

## Code changes

1. **Accept 3.** `runner.py:76` (`if process not in (1, 2)`) and `cli.py:278` (`--process must be 1 or 2`), plus the `--process` help text at `cli.py:267`, which describes only 1 and 2.
2. **Flip the test.** `tests/test_runner.py:376` asserts that `process=3` raises `"process must be 1 or 2"`. It should now start a process 3 run, and the rejection test should move to `process=4`.
3. **Process 3 runs Process 2's phases.** Wherever the runner, views and Observer branch on `process == 2` (think, react, poll, speaking order, poll participation), process 3 takes the same branch. A helper such as `uses_meeting_rounds(process)` keeps that from being copied into every branch.
4. **Process 3 requires briefs.** Decide whether `start(process=3)` refuses a goal with no briefs. Refusing is the safer choice: a process 3 run without briefs would be a Process 2 run with the wrong tag. The pizza benchmark is the exception, because it stays unbriefed on purpose as the cross-generation control goal. Either allow the benchmark under process 3 by its `[benchmark]` flag, or run it as process 2 and record that choice.
5. **Process 2 with briefs.** Today briefs work under Process 1 or 2. Decide whether briefed goals stay allowed under 1 and 2 (useful for testing) or become process 3 only. Either way, write the rule into the spec.
6. **Observer.** `observer/report.py:42`, `:61` ("## Briefs (Process 4)") and `:367` ("## Process 4") become Process 3 headings. Check the by-process splits treat 3 as its own group, not as "other".

## Text changes ("Process 4" → "Process 3")

Each of these says "Process 4" today:

- `docs/superpowers/specs/2026-10-14-bikeshed-goal-briefs-design.md`, lines 1 and 3. Line 3 explains that Process 4 "is this experiment's name, not a `--process` value". That sentence needs rewriting, not just renumbering, since Process 3 *is* a `--process` value.
- `docs/superpowers/plans/2026-10-14-bikeshed-goal-briefs.md`, throughout (title, docstrings, test strings, commit messages). It's a record of what was built, so either rename it or add a note at the top saying the feature is now Process 3.
- `docs/goals/briefed-goals.toml`, line 1.
- `README.md`, line 120.
- `CLAUDE.md`, line 91, which also says it's "not a `--process` value". Rewrite as for the spec.
- `src/bikeshed/briefs.py`, line 1 (docstring).
- `src/bikeshed/observer/briefs.py`, line 1 (docstring).
- `src/bikeshed/postrun/feedback.py`, line 77 (comment).
- `tests/test_observer_briefs.py`, lines 108 and 188, which assert the "Process 4" headings.
- `docs/gen4-fixes.md`, lines 28, 129, 151 and 169.

## Related naming, while you're in there

- **Generations.** The briefs spec once called the outgoing cohort "generation 1"; the report calls it generation 3, and the archive is `lab/archive/generation-3`. Use the report's numbering everywhere. Check the spec, plan and `gen4-fixes.md` for any "generation 1" left.
- **The archive path.** The spec's out-of-scope note says an old generation moves to `lab/generation-{x}/`. It was actually moved to `lab/archive/generation-3/`. Make the spec match.
- **Generation, not cohort.** The report's glossary uses *generation* for a set of agents and avoids *cohort*. Keep the harness docs the same.

## The report side

These are already handled, or will be, in the report folder (`obsidian-fakinghuman/60. Platform/Reports/Bikeshed`). They're listed here so both sides stay in step:

- The methodology page ("The harness has two processes for a working round") gets a third process.
- The glossary entry "Process 1, Process 2" becomes "Process 1, 2 and 3".
- The generation 4 page says "Generation 4 runs Process 3".
