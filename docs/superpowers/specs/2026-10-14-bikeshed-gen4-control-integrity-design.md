# Generation 4, sub-project A: control integrity

*2026-10-14. Implements fixes 1–5 and 7 (the assignment guard) from `docs/gen4-fixes.md`, plus the generation 4 setup (sub-project E), which needs no code. Must land before generation 4's first run. Sub-project B (the agents' environment), C (measurement) and D (baseline run) have their own specs.*

## Goal

The control agent `e` should carry no personal memory into a run: no files left from an earlier after-run cycle, no view of its own reputation, Scorer points or old methods. And it should be comparable with its twin `f`: both always hold a private brief. In generation 3 each of these broke at least once (`gen4-fixes.md`, fixes 1–5).

Nothing here changes what a non-control agent sees, and no Process 1 prompt text changes. The golden recording should not change; if it does, find out why before regenerating it.

## Run sequence (fixes 1 and 3)

### One run at a time

A new module, `sequence.py`, owns the rule that runs and post-run cycles happen one at a time.

- `postrun_unfinished(conn, runs_dir, run_id)` moves here from `observer/metrics.py`, unchanged. The Observer imports it from here. (A read-only function; importing it doesn't let the Observer write anything.)
- `blocking(conn, runs_dir, *, run_id=None, for_postrun=False) -> Blocker | None` returns the first run that blocks the action, oldest first, with the command that clears it:
  - a run other than `run_id` whose status is `running` or `paused` → `bikeshed run --resume <id>` or `bikeshed abandon <id>`;
  - unless `for_postrun`: a closed run other than `run_id` whose post-run cycle is unfinished → `bikeshed postrun <id>`.
- A closed run whose post-run never started (`--no-postrun`, `abandon`) blocks nothing.

The CLI calls it before doing anything else, and exits through `_fail` with the blocker's id, status and command:

| Command | Blocked by |
|---|---|
| `run "<goal>"` / `run --next` | any unclosed run; any unfinished post-run |
| `run --resume X` | any *other* unclosed run; any *other* unfinished post-run |
| `postrun X` | any *other* unclosed run |

There's no override flag. `abandon` remains the way out.

### Control check on every `run()`

`Runner.run()` checks every control agent in the run before its first round, on a fresh start and on resume.

- `ProfileStore.seed_drift(agent_id) -> list[str]` returns the control's files that differ from what `reset_to_seed` would write: `public.toml` (name compared as `reset_to_seed` would restore it), `temperament.md`, and the non-empty files among `self.md`, `lessons.md`, `peer-notes.md`.
- If the list is empty, emit `control_checked` (agent). No reset, no commit: `gitrepo.commit` uses `--allow-empty`, and an empty reset commit would show up in the Observer's identity timeline as a reset that never happened.
- If it isn't empty, emit `control_drift_found` (agent, files), then `reset_to_seed` and emit `control_reset` (and `control_reset_name_kept` when that applies), as `start()` does.

`start()` keeps its reset before the run exists, so on a fresh start the check in `run()` finds nothing. With the sequence rule in place, drift should never be found. If it is, the event records that a leak happened.

## What the control sees (fixes 4 and 5)

### Its own standing (fix 4)

In `view.collect_inputs`, when the viewing agent is a control, its own id is left out of `reputations`, `dimension_scores` and `points`. Its member line then renders like a member with no reviews and no Scorer points, which is how it looked at its seed. Every other member, and every other member's view of the control, is unchanged.

`points` was not in `gen4-fixes.md`. It's the same leak: Scorer points build up across runs and survive the reset (`view.py:189`).

### Its own methods (fix 5)

- `library.retrieve` already returns methods sorted by similarity. `Runner._library` asks it for every method (no top-k cut) and keeps the ranked list.
- Each agent's list is the top `library_top_k` of that ranking, after removing the agent's own methods if it is a control. A control's list fills back up to k from other members' methods. A non-control agent's list is exactly what it gets today.
- `RoundContext.library` becomes `Mapping[str, list[str]]` (agent id → rendered methods), and `_turn` passes `ctx.library[agent_id]`.
- `library_retrieved` keeps its `methods` field (the non-control list) and gains `control_methods: {agent: [ids]}` for each control whose list differs.

Retrieving every method instead of the top k costs one cosine per stored method, the same as now. Only the sort and the cut move.

## Profile updates (fix 2)

A helper `is_keep(value) -> bool` in `postrun/replies.py` is true for `None`, an empty or whitespace-only string, and `"null"` or `"none"` in any case, after stripping whitespace and surrounding quotes.

In the update step (`postrun/cycle.py`):

- `lessons`, `self_description`, `temperament`, `peer_notes`: a keep value leaves the file as it is. (`lessons` is required in the reply; "null" there keeps the current lessons rather than writing "null".)
- `public_profile`: each field is checked on its own. A keep value takes the current profile's value for that field; if every field is a keep value, the profile isn't written. `pronouns` already treats `None` as keep and now treats the other keep values the same way. A reply that names a new value for some fields and "null" for others writes the merge.
- `PublicReply`'s fields become optional (`str | None = None`) so a model can leave one out. `PublicReply` is used only by the update step; `agents introduce` and `checkin` have their own reply shapes and are unchanged. The merged profile still goes through `PublicProfile`'s validators, so the saved name, headline and introduction can never end up empty.

`prompts/update_request.md` is unchanged. "Use null for any you keep" was right; the harness misread it.

## Brief assignment guard (fix 7)

- `twins()` moves from `observer/metrics.py` to `ProfileStore.twins() -> dict[str, list[str]]`, unchanged. The Observer calls the store method.
- `briefs.assign(spec, agent_ids, seed, protected=())` keeps its seeded shuffle. Afterwards, while any protected member is past the last brief slot, it swaps places with the last unprotected member that holds a brief. Protected members keep their relative order, and so does everyone else.
- `Runner.start` passes `protected` = the run's controls plus their twins in the run.
- With at least as many briefs as members (which, once the sixth briefs are written, is true of every briefed goal), the guard never fires and the assignment is exactly what it is today. If there are more protected members than briefs, the guard places as many as it can.

Which brief `e` and `f` hold still differs from run to run. Showing that in the twin comparison is sub-project C.

## Generation 4 setup (sub-project E, no code)

Done by hand in `lab/` and in the repo's docs before the first run:

- `lab/temperaments/f.md`: an exact copy of `e.md`. `init-agents.sh` is unchanged.
- `lab/bikeshed.toml`: `view_entries = 18`.
- `docs/goals/briefed-goals.toml`: a sixth private brief for `picnic-briefed`, `offsite-briefed`, `film-night` and `office-thermostat`. Text only, no constraint, a fact about the member's situation (spec 2026-10-14 goal briefs, "Writing briefs"), drafted for Andrew to review.
- `docs/superpowers/specs/2026-10-14-bikeshed-goal-briefs-design.md`: "generation 1" → "generation 3"; archive path `lab/archive/generation-{x}/` → `lab/generation-{x}/`.
- The generation 4 model (fix 18): chosen by Andrew before the first run, recorded in the generation's notes. Not needed for anything in this spec.

## Testing

TDD with `FakeModel`, as everywhere else.

- `sequence.blocking`: each row of the table, plus `--no-postrun` and abandoned runs blocking nothing, and the oldest blocker reported first. CLI tests for each command's error text.
- `run()`: a clean control emits `control_checked` and makes no commit; a control with a hand-edited `self.md` emits `control_drift_found` naming it and ends at its seed.
- `seed_drift`: each file, and the kept-name case.
- `is_keep`: each keep value; a real value that merely contains "null".
- Update step: "null" in each private field keeps the file; a partial `public_profile` merges; all-keep writes nothing.
- View: a control's view has no own reputation, dimension scores or points; a twin's view of the control shows all three; a non-control view is byte-identical to today's.
- Library: a control's list excludes its own methods and still has k entries when enough exist; a non-control list is unchanged.
- `assign`: with 5 briefs and 6 members across many seeds, no protected member is ever left out; with 6 briefs the result equals today's.
- Observer: still finds twins and unfinished post-runs through the moved functions; the isolation tests pass.
- Golden recording unchanged.
