# Generation 4, sub-project D: the baseline run

*2026-10-15. Implements fix 6 in `docs/gen4-fixes.md`. Must land before generation 4's first pizza benchmark. Sub-projects A, B, C1, C2 and F are merged.*

## Goal

The control can't separate the model from the culture, because it shares both with its twin. A baseline run can. It runs the same goal, on the same model and with the same settings, in a fresh room of seed agents with no norms, no library and no history.

| | Model | Room | Culture (norms, library) | Personal memory |
|---|---|---|---|---|
| Baseline | ✓ | fresh | – | – |
| Control | ✓ | ✓ | ✓ | – |
| Twin | ✓ | ✓ | ✓ | ✓ |

- Baseline vs. control: what the culture adds.
- Control vs. twin: what personal memory adds.
- Baselines over time: whether the model alone drifts. It shouldn't; this is a sanity check.

A baseline must never write into the generation's community. It adds no reviews, norms, methods, reputation or commits.

## The command

- `bikeshed baseline <run-id>` mirrors one closed community run.
- `bikeshed baseline --resume <baseline-id>` continues a baseline that paused, for example during an oMLX outage.

You start it by hand. The natural rhythm is after each pizza benchmark, but any closed run can be mirrored, briefed runs included.

**Refusals**, each through `_fail` with a message:

- the run doesn't exist, or isn't closed;
- a community run or post-run is unfinished (`sequence.blocking` on the community). A baseline and a community run never compete for oMLX;
- the backend an agent used in the source run is no longer in the config, or now names a different model;
- `--resume` names no baseline, or one that is already closed;
- a run id together with `--resume`.

## Where it lives

`lab/baselines/<run-id>-b<k>/`, where `k` counts up from 1, so a run can have several baselines. Inside:

- `community/`: a scratch community, with its own git repo, `community.db` and `goals.toml`, created by the same code as `bikeshed init`;
- `runs/`: the baseline run's directory (`workspace/`, `events.jsonl`, `report/`);
- `baseline.toml`: `source_run`, `created_at`, and `baseline_run` (the id of the run in the scratch store, written once it has started).

The directory comes from the config's parent folder (`cfg.community_dir.parent / "baselines"`). `lab/` is gitignored, so baselines are too. Nothing is ever opened for writing under `lab/community/` or `lab/runs/`: the generation's store is read with `connect_readonly`, and its profiles through `ProfileStore(create=False)`.

## What is copied from the source run

- **Goal:** `goal`, `goal_id`, `goal_category`, `is_benchmark`.
- **Settings:** every `[run]` field (`process`, `max_rounds`, `close_rule`, `view_entries`, `max_writes`, `quorum_slack`, `stagnation_rounds`, `max_reads`), taken from the `config` of the source run's last `start` or `resume` provenance row. A cap lowered at resume is the one used. `model_retries` and `max_concurrent_calls` come from the current config, because they govern the machine, not the experiment.
- **Seed:** the source run's `runs.seed`. Speaking order and shuffles then match. Model sampling still varies.
- **Brief deal:** the source run's `run_briefs` rows, copied unchanged, so each agent id holds the same slot. There is no re-dealing, and so no control or twin protection to reproduce.
- **Agents:** the source run's `agent_ids`, with the same ids. Each agent's temperament is the **first** committed version of `agents/<id>/temperament.md` in the generation's community (`ProfileStore.first_version`), which is its seed even if the lab file or the agent changed later. Each agent then writes a **fresh** introduction through `introduce.introduce`, the step `agents new` uses. So names and introductions vary between baselines.
- **Models:** each agent's backend, from the source run's start provenance (`models[agent]["backend"]`). The current config must still define that backend with the same `model`.

## What the baseline doesn't have

- No norms: the scratch store has no adopted norms or dimensions.
- No library: the Runner gets no embedder, so nothing is retrieved.
- No reputation, and no history: the scratch store has no earlier runs.
- No post-run: no journals, reviews, updates or norm votes. The run ends at its report.
- No control: no agent is marked control, and nothing is reset. Every agent is already blank.
- The Scorer is the configured `[scorer]`, so a call closes a baseline the same way it closes a community run.

## Changes to the Runner

`Runner.start` gains two optional keyword arguments. Community runs pass neither, so nothing changes for them:

- `seed: int | None = None`: when given, it is used instead of `secrets.randbits(32)`;
- `briefs: RunBriefs | None = None`: when given, these brief rows are saved as they are, instead of `assign(brief, ...)`. It may not be combined with `brief`. Process 3's "requires briefs" check counts `briefs` as briefed.

Provenance is captured into the scratch store at start and resume, as for any run.

## Observer

`bikeshed observe` looks for `<lab>/baselines/*/baseline.toml`. For each one whose `baseline_run` is closed, it opens the scratch store read-only and attaches the baseline to its source run. A missing `baselines/` directory, or a baseline that is still open, is skipped.

**Each baseline gives** the same code metrics a community run gets:

- ending (`ended_reason`) and number of rounds;
- calls and Scorer rulings;
- the answer: the closing call's synthesis, or each agent's latest proposal;
- action mix per agent;
- copying counts (C2, `copies` over the scratch store and its runs directory).

There are no judgments and no peer scores, because there is no post-run.

**Report.**

- **The source run's report** gains a "## Baseline" section, with one row per baseline: id, ending, rounds, calls, accepted/rejected rulings, and the copying count. Under the table is each baseline's answer.
- **The summary** gains "## Baseline vs community". For each run with at least one baseline, one block:
  - the room's ending, rounds and answer, then each baseline's;
  - an action-mix table with rows for the control, the twin, and the same two ids in each baseline. A baseline has no control, so the rows match on agent id.
- **Benchmark judge:** its "earlier answers" input stays community-only. Baseline answers are printed beside the community's, not fed to the judge.

## Testing

TDD with `FakeModel` and temporary directories.

- **Isolation:** after a baseline, the generation's `community/` (files, `community.db` bytes and git `HEAD`) and `runs/` are unchanged.
- **Copying from the source:**
  - settings come from the last start or resume provenance, including a lowered `max_rounds`;
  - the seed matches the source run's seed;
  - the brief rows match slot for slot;
  - temperaments are each agent's first version, even after the agent changed;
  - each agent is introduced fresh (one introduction call per agent).
- **No culture:**
  - no `library_retrieved` event;
  - the scratch store has no `postrun_steps`, `reviews` or `journals` rows;
  - no agent is marked control.
- **Refusals:** an open source run; an unfinished community run; a missing backend; a backend that now names a different model; `--resume` of a closed baseline.
- **Resume:** a baseline that paused is continued with `--resume` and closes.
- **Runner:**
  - `start(seed=...)` records that seed;
  - `start(briefs=...)` saves the given rows;
  - `brief` with `briefs` is refused;
  - existing runner tests and the Process 1 golden recording are unchanged.
- **Observer:**
  - a source run with one baseline gets the "## Baseline" section and the summary block;
  - with no `baselines/` directory, the reports are as before;
  - `test_observer_isolation.py` still passes.
