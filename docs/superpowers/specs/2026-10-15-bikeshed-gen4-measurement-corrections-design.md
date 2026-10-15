# Generation 4, sub-project C1: measurement corrections

*2026-10-15. Covers fixes 8, 9, 14 (`think_vs_act` and `private_public`), 15 and 16 in `docs/gen4-fixes.md`. Sub-project C is split in two. C1 corrects measurements that already exist and mislead. C2, a later spec, adds new ones: copying, credit accuracy, library reuse, rule-gaming, the twin's brief slot (fix 7), and norm overlap (fix 17). Everything here only reads the record, so it can land before or after generation 4 starts, and the Observer re-reads the whole record.*

## Goal

Make the Observer's existing analyses, the export and `bikeshed show` say what the record says before generation 4 produces data. All of it is read-side: the Observer, export, `bikeshed show`, and the header of the narrator's file. Nothing here reaches an agent's context (principle 2).

Changing a judge's inputs or prompt changes its cache key (`Judge.key` hashes both), so the next `bikeshed observe` re-judges the affected analyses for earlier runs. That is intended. It costs judge calls once.

## Fix 9: self-image compares the right moments

**Now.** `self_image_inputs` (`observer/inputs.py:65`) takes the previous run in `closed_runs` order, which is creation order (`rowid`), and reads "before" at that run's end. When run 8 was created first but its post-run finished after run 9's, run 9's "before" was later than its "now", and the judge reported reversals that never happened. For the control, "before" was the previous run's end even though a reset came between.

**Change.** "Now" stays at this run's end (`_after(run)`). "Before" is the later of:

- the end of the latest run that included the agent and ended strictly before this run's end (`RunInfo.end`, which includes the post-run cycle), and
- the time of the latest `reset control agent <id> to seed` commit in the agent's history that is before this run's end.

The snapshot is read one second after that moment, as now. If neither exists, both "before" inputs stay "(no earlier run)". For a control agent, which is reset at the start of every run, this compares the end of a run with the seed it started from. That is the "how far one cycle moves a blank agent" measure fix 8 wants kept.

## Fix 8: the control's own updates are labelled

**Now.** The reset is already labelled: `profiles.py` commits it as `reset control agent <id> to seed` and `metrics.identity_events` reports it as a `control reset`. But the control's post-run updates are committed and reported like anyone else's, so the timeline reads a reset as the control's own change.

**Change.** The Observer labels these from the agent's record (`store.record(id).control`), never from commit text.

- **Identity timeline.** An identity event (rename, public profile, `temperament.md`, `self.md`) by a control agent gets the detail suffix "(control; reset before its next run)". `control reset` events are unchanged.
- **Per-run Self-image section.** For a control agent, the line under the agent says it is compared with its seed: "_control: compared with its seed, not its previous run_".

## Fix 14a: think notes are paired with the same round's moves

**Now.** `think_vs_act_inputs` (`observer/inputs.py:51`) gives the judge every think note in one block and every public entry in another, and the judge pairs them however it likes. In run 6, it paired notes with moves from earlier rounds. Failed turns are public `pass` entries with a `failure` payload, so they land in "public actions" and can be read as divergence.

**Change.**

- **Inputs.** Two keys per round that has a think note by the agent, in round order:
  - `round N think note`: the note, rendered as now;
  - `round N actions`: the agent's public entries in round N (react moves, pre-action writes, poll answers) and the private messages it sent in round N, with failed turns (`pass` with a `failure` payload) left out.

  A round whose actions are empty after that filter is left out entirely, so a failure is never compared with a note. If no rounds remain, there's no judgment, as when there are no notes.
- **Reply.** `ThinkActPoint` gains `round: int`.
- **Check.** `_think_and_act` requires each `think_quote` to appear in that point's `round N think note`, and each `action_quote` in its `round N actions`. A point naming a round that isn't in the inputs fails the check. Both go through the usual one correction retry.
- **Prompt.** `prompts/observer/think_vs_act.md` says the input is grouped by round, and that each point compares a round's note with what the agent did in the same round and gives that round. The rest stays: describe, don't judge.
- **Report.** Each point's line starts with `[rN]`.

## Fix 14b: private vs public reads the "before" journal only

**Now.** `private_public_inputs` uses `_journal`, which joins the "before" and "after" journal sections. The "after" section is written once the agent has read its reviews, so it mixes in reactions to those reviews.

**Change.** `private_public_inputs` passes only the "before" section, the journal written before the agent saw any review. The input key stays `journal`. `themes_inputs` keeps both sections, because themes are about the whole session.

## Fix 15: export lists every model and the final round cap

**Now.** `_run_row` (`export.py:166`) reads only the first `start` provenance row, so `models` and `max_rounds` show the start values. Runs 8 and 9 read DeepSeek but mostly ran on other models.

**Change.**

- `models` is the sorted union of the `model` field across every `start` and `resume` provenance row.
- `max_rounds` comes from the last `start` or `resume` row (by id), so a cap lowered at resume shows. `close_rule` comes from the same row.
- `code_commit` and `code_dirty` keep the start row, because they describe the code the run started from. Provenance changes are already reported elsewhere (`provenance_changes`).
- The per-run `transcript.md` header reads the same row values, so it agrees with `runs.csv`.
- Post-run rows are excluded: they describe the post-run cycle, not the run.

## Fix 16: the narrative is marked unchecked

**Now.** `narrative.md` says "Written by the narrator model from the account", and `bikeshed show` prints it straight after `account.md` with nothing to tell them apart. In at least seven generation 3 runs it got facts wrong.

**Change.**

- The header the post-run narrative step writes (`postrun/cycle.py:464`) becomes: "_Written by the narrator model from the account and not checked against the record. Where it disagrees with account.md, the account is right. No agent ever sees this._"
- `bikeshed show` prints a line before `narrative.md`: "(The narrative below is the narrator model's retelling, not checked against the record.)" This covers narratives already on disk.
- The narrator still never reaches agents. It's the last post-run step, and the file lives only in `report/`.

## Docs

- `docs/gen4-fixes.md`: a line under fixes 8, 9, 14, 15 and 16 recording what C1 did. Fix 14's copying, credit-accuracy and rule-gaming bullets and the judge-validation bullet point to C2. In the development-order table, C becomes C1 (this spec) and C2 (new metrics: fixes 7, 14's new measures, 17, and library reuse from B).

## Testing

TDD with `FakeModel`, using the read-only fixtures and observer helpers.

- **Fix 9.**
  - Two runs, created A then B, where A's post-run ends after B's: B's "before" is not read from A's end. A's "before" is B's end when B ended first.
  - A control agent with a reset commit after the previous run's end: "before" is read at the reset.
  - An agent with no earlier run and no reset: "(no earlier run)".
- **Fix 8.**
  - A control agent's `self.md` update in the identity timeline carries the suffix; a non-control agent's does not.
  - The Self-image section marks the control.
- **Fix 14a.**
  - Inputs have per-round keys in round order.
  - A failed turn is excluded, and a round with only failures is dropped.
  - A private message sent in round 2 lands under round 2.
  - The check rejects an action quote taken from another round's actions, and a round not in the inputs.
  - The report shows `[rN]`.
- **Fix 14b.** With both journal sections present, the inputs contain only the "before" text.
- **Fix 15.**
  - A run with a start row on model X and a resume row on model Y exports `models = X;Y`.
  - A resume row with a lower `max_rounds` exports the lower value.
  - `transcript.md` agrees with `runs.csv`.
- **Fix 16.**
  - The narrative step writes the new header.
  - `bikeshed show` prints the label before the narrative and not before `account.md`.
- **Isolation.** `test_observer_isolation.py` and `test_watch_isolation.py` still pass. The Process 1 golden recording is unaffected, because nothing in a run changes.
