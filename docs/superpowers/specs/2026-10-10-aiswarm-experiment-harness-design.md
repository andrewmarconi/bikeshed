# AISwarm — Experiment Harness Design (Plan 3a addendum)

- **Date:** 2026-10-10
- **Status:** Approved in brainstorming; awaiting written-spec review
- **Author:** Andrew Marconi (with Claude)
- **Extends:** `2026-10-10-aiswarm-design.md` §6 (experimental design). Where this addendum is more specific than that spec, this addendum governs; everything else there still applies, including the principles (values-neutral prompts, observation never feeds back, privacy invariants).

## 1. Purpose

Plans 1, 2a and 2b produce a community that remembers and evolves. Plan 3 makes the results interpretable. It is split in two:

- **3a — experiment harness (this addendum):** run provenance, the control agent, the goal queue with a benchmark goal, and the agent timeline. Small, CLI-only, and they start producing usable data immediately.
- **3b — observation:** the Textual TUI and the Observer. Read-only consumers of what 3a records; designed separately once 3a exists.

Reasoning traces (§6.5) and credits (§6.4) already exist from Plan 1 and are only consumed here.

## 2. Decisions taken in brainstorming

| Topic | Decision |
|---|---|
| Goal subject matter | Goals are **frivolous and low-stakes** (e.g. "Come to an agreement on the best fruit."), replacing the workplace-flavoured goals used so far. Pressure comes from disagreement, not from serious subject matter. |
| Goal categories | Kept: `neutral`, `trade-off`, `contested`, Observer-only, exactly as §6.3. |
| Benchmark goal | A trade-off goal: "Pick one pizza topping combination the whole group will share." Re-run every 5 runs. |
| Control agent | A new, fifth agent created with `--control`; the existing agents a–d keep their history. |
| Sequencing | Plan 3 split into 3a (this) and 3b (TUI + Observer). |

## 3. Run provenance

### 3.1 Capture

A `run_provenance` table gets one row per capture point: when a run starts, on each `aiswarm run --resume`, and on each `aiswarm postrun`. A harness change in the middle of a run is therefore recorded rather than hidden. Each row stores:

- `run_id`, `phase` (`start` | `resume` | `postrun`), `captured_at`
- `code_commit` of the AISwarm repo and `code_dirty` (uncommitted changes present); null if not run from a git checkout
- `config` — the fully resolved config as JSON, with every `api_key` removed
- `prompts` — for every template in `aiswarm/prompts/`: file name, sha256, and full text
- `models` — per agent: backend name, backend settings (keys removed), model id
- `servers` — per distinct openai-compatible base URL: the `/v1/models` listing and the server version if the server exposes one (endpoint verified while planning; null when unavailable). Capture never fails the run: an unreachable server is recorded as such.
- `seed` — the run's random seed
- `fingerprint` — sha256 over the non-volatile fields: code commit, dirty flag, prompt hashes, config (minus paths), models. Excludes timestamps, phase, and server listings.

### 3.2 Seeded randomness

`Runner` currently shuffles post order with an unseeded `random.Random()`. A new run draws a seed, stores it in its `start` provenance row, and seeds the RNG with it; a resumed run reseeds from the stored seed combined with the resumed round, so turn order is reproducible from provenance.

### 3.3 Change detection

`provenance_changes(conn, run_id)` compares a run's `start` fingerprint with the previous run's and returns which parts changed (code commit, dirty, named prompt files, config keys, models). It also flags a `resume`/`postrun` row whose fingerprint differs from the same run's `start`. `aiswarm show <run>` prints the result as a header ("Harness changed since the previous run: prompts journal_request.md; code commit"). The 3b Observer uses the same function to mark trend charts.

## 4. Control agent

### 4.1 Creation and seed

`aiswarm agents new <id> --model … --control` sets `control = true` in `agent.toml` (the field exists already). The seed is stored in `agents/<id>/seed/`: `temperament.md` at creation, `public.toml` whenever the agent writes its first introduction (`agents new` or `agents introduce`). `agents/<id>/seed/` is never read into any View.

### 4.2 Reset

At the start of every new run (not on resume), each participating control agent is reset:

- `temperament.md` and `public.toml` are restored from `seed/`;
- `self.md`, `lessons.md`, `peer-notes.md` are emptied;
- one community commit: "reset control agent <id> to seed".

If another agent has since taken the seed name (case-insensitive), the control agent keeps its current name, the rest of the seed profile is restored, and a `control_reset_name_kept` event is emitted. A control agent with no seed `public.toml` cannot start a run (same error as a missing profile).

### 4.3 What a control agent does not carry

- **Past journals.** Wherever an agent's earlier journals are loaded — the View (`collect_inputs`) and any post-run prompt — a control agent receives only journals from the current run. Its journal files are kept for the Observer and the timeline.
- **Control status.** Never appears in any View or prompt; enforced by test.

### 4.4 Known confound

A control agent still sees its public reputation line and can `read_reviews` about itself, because these are community record rather than its own memory. Colleagues also remember it. This is recorded here as a known confound; the Observer treats "control" as "no private memory", not "stranger".

### 4.5 Lab rollout

After 3a ships: `aiswarm agents new e --model <backend> --control --temperament …` in `lab/`. `max_concurrent_calls = 2` stays.

## 5. Goal queue and benchmark

### 5.1 File

`community/goals.toml`, committed to the community repo, never shown to any agent:

```toml
[benchmark]
text = "Pick one pizza topping combination the whole group will share."
category = "trade-off"
every = 5

[[goals]]
id = "best-fruit"
text = "Come to an agreement on the best fruit."
category = "contested"
```

`id` is lowercase letters, digits and `-`, unique within the file; `aiswarm goals add` derives one from the text when not given. `category` ∈ `neutral` | `trade-off` | `contested`. `every` ≥ 1.

### 5.2 Seed set

`aiswarm init` writes `goals.toml` only if it is missing (safe to run in `lab/`):

| Category | Goal |
|---|---|
| contested | Come to an agreement on the best fruit. |
| contested | Decide, once and for all, whether a hot dog is a sandwich. |
| contested | Settle on the greatest board game of all time. |
| contested | Agree on the correct way to load a dishwasher. |
| trade-off | Plan a picnic menu that has to fit in a single basket. |
| trade-off | Choose three songs for a road-trip playlist that everyone in the car must listen to. |
| trade-off | Design the ideal Sunday with only $20 to spend. |
| neutral | Invent a new holiday and describe how it is celebrated. |
| neutral | Write a limerick about a cat who hates Mondays. |
| neutral | Name the office goldfish. |

Goals state a task only; none tells agents how to behave (§2 principle 3).

### 5.3 Run records and selection

`runs` gains nullable `goal_id`, `goal_category`, and `is_benchmark` (0/1, default 0). Ad-hoc `aiswarm run "<goal>"` leaves them null/0.

`aiswarm run --next` selects:

1. the **benchmark**, if no benchmark run exists yet, or at least `every - 1` runs (of any kind) have started since the last benchmark run;
2. otherwise the first queue goal whose `id` no run has used;
3. otherwise it fails with "goal queue is empty; add goals with `aiswarm goals add`".

For the lab (5 runs, none benchmark): run 6 is the pizza baseline, runs 7–10 take queue goals, run 11 is pizza again. `--next` and a goal argument are mutually exclusive.

### 5.4 CLI

- `aiswarm goals list` — benchmark (with last run and next due), then the queue in order with category and used/unused (and the run id that used it).
- `aiswarm goals add "<text>" --category <c> [--id <id>]` — appends, validates, commits.

## 6. Agent timeline

`aiswarm agents timeline <id|name> [--since <run-id>]` resolves an agent by id, current name, or former name (case-insensitive) and prints one chronological markdown document, grouped under `## <run-id> — <goal>` headings (activity outside a run, such as creation and check-ins, goes under the run it precedes, or "Before any run").

Sources:

- **Community git log** for `agents/<id>/` (excluding `journal/`): diffs of `temperament.md`, `self.md`, `lessons.md`, `peer-notes.md`, `public.toml` (renames called out), and control resets.
- **Database:** journals (before/after), reviews given and received with responses and dimension ratings, credits given and received (from the closing consensus call's payload), norm proposals, critiques and votes, and reasoning traces (from `traces` and `postrun_steps.reasoning`), each excerpted to ~300 characters.

`--since` drops runs before the given run. Output goes to stdout; it is a user-only view (it includes private files) and never enters any agent's context.

## 7. Testing

TDD with pytest and the fake model client; no assertion on LLM content.

- Provenance: rows at start/resume/postrun; API keys absent; prompt hashes match files; fingerprint stable across identical runs and changed by a prompt edit; `provenance_changes` names the changed parts; an unreachable server does not fail capture; the seed is stored and determines post order.
- Control: seed captured on create and introduce; reset restores seed and empties notes, commits once, runs only at new-run start; name-collision fallback; control status and earlier journals absent from the View and post-run prompts; a non-control agent is unaffected.
- Goals: `goals.toml` round-trip and validation; init seeds only when missing; benchmark scheduling (never run, due after `every - 1`, ad-hoc runs count); queue order and used tracking; empty-queue error; `--next` with a goal argument rejected; category tags never in any View.
- Timeline: name resolution (id, current, former); chronological grouping by run; `--since`; each source appears.
