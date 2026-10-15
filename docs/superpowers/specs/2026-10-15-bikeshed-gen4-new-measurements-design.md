# Generation 4, sub-project C2: new measurements

*2026-10-15. Covers the parts of `docs/gen4-fixes.md` that add measures nobody has taken yet:*

- *fix 14: copying and credit accuracy;*
- *fix 17: norm overlap;*
- *fix 7: the twin comparison's brief slot;*
- *library reuse, handed on from sub-project B.*

*C1 (measurement corrections) is merged. Everything here only reads the record, so it can land before or after generation 4 starts. Each `bikeshed observe` re-reads the whole record.*

*Fix 14's rule-gaming judge is deferred to its own spec. It needs generation 4 think notes to calibrate a definition against, the same way the disclosure and fabrication judges need hand-checking.*

## Goal

Generation 3's review found copying (run 14: three agents posted `b`'s vote reason word for word; `d` copied `b`'s proposal), calls that credited the wrong people, retrieved methods that kept reappearing, and a norm proposal that restated an adopted norm. Nothing in the Observer measured any of that. C2 adds those measures as deterministic code, so they cost nothing to re-run and give the same answer every time. No model calls are involved.

All of it is read-side: the Observer opens the store read-only (principle 2), and `events.jsonl` is opened for reading only. Nothing reaches an agent.

## The text-overlap module

New module `src/bikeshed/observer/overlap.py`. Pure functions, no I/O.

- `normalise(text) -> str`: lowercase; replace every character that isn't a letter, digit or whitespace with a space; collapse whitespace.
- `shingles(text, n=SHINGLE) -> set[tuple[str, ...]]`: the set of n-word sequences of the normalised text. Empty if the text has fewer than n words.
- `match(text, source) -> Literal["exact", "partial"] | None`:
  - `exact`: the normalised text equals the normalised source; or the text has at least `SHINGLE` words and its normalised form appears inside the normalised source, aligned to word boundaries.
  - `partial`: not exact, the text has at least one shingle, and at least `PARTIAL` of the text's shingles are in the source's shingles.
  - `None` otherwise. An empty text never matches.
- Constants: `SHINGLE = 6` (words) and `PARTIAL = 0.5`. The report prints both under each section that uses them, so a reader knows what "partial" means.

A match is directional. It says how much of `text` comes from `source`, not the reverse.

## Copying (fix 14)

**What is checked.** Every public entry by a member (`visible_to` is null, author is a run member, the entry is not a failed `pass`) and every private message a member sent. These text fields are checked when present:

- `answer_summary`, `method`;
- `points`, `text`, `reason`, `change`, `synthesis`;
- `content` (a file write). A `write_file` entry holds only the path and version, so its text is read, read-only, from the run's workspace repo at the commit `files` records for that version.

Think notes are private to their author and are never checked as copiers.

**Against what.** Each field is checked against every text field of each earlier entry (lower id) that:

- was written by a different member;
- the copier was allowed to see (`board.visible`: public, or private and naming the copier);
- has a field from the same list.

Only the best match per (copier field, source entry) is kept, with `exact` before `partial`.

**Each match records:**

- the copier, its entry id, type and field;
- the source author and source entry id;
- the match kind;
- `same round` or `earlier round`.

The round tag matters. In a blind phase (Process 1 rounds; round 1 of Processes 2 and 3) a same-round match is convergence, not copying, because the copier could not have seen it. The Observer doesn't reconstruct who saw what within a round. It shows the tag, and the reader decides.

**Report.**

- **Per run, "## Copying".** A table: copier, entry (`#id type.field`), source (`#id author`), kind, round tag. If there are no matches it says "_No copied text found._" Under the table: the thresholds.
- **Summary, "## Copying".** Per process (never summed across processes): a copier → source table of match counts, exact and partial shown separately.

## Credit accuracy (fix 14)

For each `call_consensus` in a run:

- **copied sources:** the authors of earlier entries that the call's `synthesis` matches (exact or partial), other than the caller;
- **credited:** the `agent_id`s in its `credits`;
- **uncredited sources:** copied sources not credited;
- **credited without text:** credited members none of whose earlier entries the synthesis matches. This is not an error: a member can contribute an idea in other words. It is listed so the reader can check.

**Report.**

- **Per run:** a "### Credit accuracy" block in the existing Credits area, one line per call: `#id by Caller: copied from X, Y; credited X, Z; uncredited: Y; credited without matching text: Z`.
- **Summary:** under the existing Scorer and credit material, the number of calls that left out at least one copied source, out of all calls, per process.

## Library reuse (from sub-project B)

**What was shown.** Read the run's `runs/<id>/events.jsonl` with `events.read_events`.

- The `library_retrieved` event gives `methods`, the common list of method ids. `control_methods` maps a control agent to its own list.
- Each member was shown its own list if it has one, and otherwise the common list.
- If the file or the event is missing, the run's section says "_Not recorded._", and the run is left out of the summary counts.
- A `library_unavailable` event means nothing was shown.

**What counts as reuse.** A member's `propose.method` or `refine.change` in the run, matched against the text of each method it was shown, read from the `methods` table by id. Methods are guarded with `has_table`.

**Report.**

- **Per run, "## Library reuse":** each method shown (`#method-id`, its run, its author), who was shown it, and who reused it, with the match kind.
- **Summary, "## Library reuse":**
  - how many times each method was shown across runs, and how many times it was reused, most-shown first;
  - the share of shown methods that were reused, per process.

## Norm overlap (fix 17)

For each norm proposal of kind `norm` or `amendment`, compare its `text` with each norm that was **adopted before the proposal was created**.

- Adopted norms are the `norm_proposals` rows with `status = 'adopted'` and kind `norm` or `amendment`.
- `norm_proposals` has no adoption time. A norm is adopted during its own run's post-run (the norms_vote step), which `metrics.run_end` covers. So a norm counts as "adopted before" a proposal when its run's `run_end` is earlier than the proposal's `created_at`.

The measure is the **share** of the proposal's 6-word sequences found in the norm (`overlap.share`; 1.0 when the whole proposal is there). The norm with the highest share is kept. A proposal is listed when that share is at least `NORM_FLOOR = 0.25`. Dimension proposals are not compared.

*Revised in the final review (2026-10-15).* The first version used the copying threshold (a partial match at 50%). But fix 17's own example, generation 3's proposal #14 restating adopted norm #8, shares 45% of its sequences, so it went unlisted. Norms are short and get reworded, so instead of fitting a threshold to one case, the report shows the share and lists from a low floor. The reader judges.

**Report.** **Summary, "## Norm overlap":** one row per listed proposal. Columns:

- the proposal (id, run, author);
- the norm it overlaps (id and its text, clipped to 120 characters);
- the share;
- the proposal's outcome (`adopted`, `rejected`, `open`).

A note under the table explains the share. With no rows it says "_No proposal shares at least 25% of its 6-word sequences with a norm adopted before it._" The Observer doesn't call a proposal a duplicate; it shows the overlap.

## The twin's brief slot (fix 7)

The control-vs-twin table (`report._control_vs_twin`, rows built in `observe._write_summary`) gains a `brief` column for each (run, agent) row:

- `slot k`: the agent held private brief k (`run_briefs.agent_id`, `slot >= 1`);
- `shared only`: the run has brief rows, but none of its private slots names this agent;
- `–`: the run has no brief rows (unbriefed).

`run_briefs` is guarded with `db.has_table`.

## Docs

`docs/gen4-fixes.md`:

- **fix 7:** a C2 line saying the twin table shows each agent's slot;
- **fix 14:** copying and credit accuracy are done; rule-gaming is deferred to its own spec, waiting on generation 4 think notes;
- **fix 17:** the overlap is measured in the Observer, and the agents are never told;
- **development-order table:** C2 marked.

## Testing

TDD with read-only fixtures (`tests/observer_helpers.py`).

- **`overlap`:**
  - an exact copy;
  - an exact copy inside a longer source;
  - a partial at the threshold;
  - a partial just below it;
  - a text shorter than `SHINGLE` words, which matches only when equal;
  - punctuation and case ignored;
  - an empty text.
- **Copying:**
  - a vote reason pasted word for word in the same round is tagged `same round`, and one pasted from an earlier round is tagged `earlier round`;
  - a private message the copier received is a source, and one between two other members is not;
  - think notes are never copiers or sources;
  - a member's own earlier text is not a source;
  - a failed `pass` is skipped.
- **Credit accuracy:** a call whose synthesis copies `b`'s proposal but credits only `c` lists `b` as uncredited and `c` as credited without matching text.
- **Library reuse:**
  - a run with an `events.jsonl` that holds `library_retrieved`, where one shown method is reused in a proposal;
  - a control with its own list is checked against its own list;
  - a missing file gives "_Not recorded._"
- **Norm overlap:**
  - a proposal restating a norm adopted in an earlier run is listed with its outcome;
  - a norm adopted after the proposal doesn't count;
  - dimension proposals are skipped.
- **Twin slot:** `slot k`, `shared only` and `–` rows, and a store without `run_briefs`.
- **Isolation:** `test_observer_isolation.py` still passes, and nothing new opens the store for writing.
