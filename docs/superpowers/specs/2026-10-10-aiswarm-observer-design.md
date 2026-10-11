# AISwarm — Observer Design (Plan 3b)

- **Date:** 2026-10-10
- **Status:** Approved; Plan 3b implemented
- **Author:** Andrew Marconi (with Claude)
- **Extends:** `2026-10-10-aiswarm-design.md` §9 (Observer) and `2026-10-10-aiswarm-experiment-harness-design.md` (Plan 3a). Where this document is more specific, it governs; the principles there still apply, above all **observation never feeds back** (§2 principle 2).

## 1. Purpose and scope

The Observer turns what the community records into something the user can read: per-run reports and cross-run trends in working style, values, reputation and identity. It is read-only with respect to everything agents can see, runs only on demand, and its output never enters any agent's context.

Plan 3 was split again: **3b = the Observer (this document)**, **3c = the Textual TUI** (its own spec later).

## 2. Decisions taken in brainstorming

| Topic | Decision |
|---|---|
| Sequencing | Observer before TUI; the TUI becomes Plan 3c. |
| Judge model | Claude via the existing `claude_cli` backend (`claude -p`). The judge is not an agent, so the subscription-mode context leak is irrelevant. Lab text is sent to Anthropic under the user's subscription. Default model alias `sonnet`. |
| Output | Markdown reports plus PNG charts (matplotlib). |
| Architecture | Three layers: metrics (SQL/git, no LLM) → judgments (cached LLM calls) → report. |
| Location | Observer output and state live in their own top-level `observer/` directory with their own `observer.db`, not under `runs/<id>/` as §9 of the base spec said. |

## 3. Structure

```
community.db (read-only) ─┐                ┌─ observer/observer.db   judgments cache, codebook
community git (read-only) ┼─▶ metrics ──┐  │
run_provenance ───────────┘             ├─▶ report ──▶ observer/runs/<run-id>.md
                          └─▶ judge ────┘              observer/summary.md
                              (claude -p, cached)      observer/charts/*.png
```

Package `src/aiswarm/observer/`:

| Module | Responsibility |
|---|---|
| `metrics.py` | Pure functions over a read-only connection and the profile store, returning dataclasses. No LLM calls, no writes. |
| `judge.py` | Builds judge requests, calls the configured backend, validates replies with pydantic, enforces the evidence rule, caches results in `observer.db`. |
| `codebook.py` | Merges open-coded theme labels into a growing codebook. |
| `charts.py` | Headless matplotlib (Agg). Every trend chart takes provenance-change markers. |
| `report.py` | Assembles per-run and summary markdown. |
| `store.py` | `observer.db` schema (own numbered migrations) and access. |

Judge prompts live in `src/aiswarm/prompts/observer/`. They are **excluded** from the agent prompt snapshot in run provenance (`prompt_snapshot()`), so editing a judge prompt never marks the agent harness as changed; their hashes are recorded with each judgment instead.

### 3.1 Config

```toml
[observer]
judge = "claude-judge"   # a backend name from [models]; must be kind = "claude_cli" or "openai"
dir = "observer"         # relative to the config file

[models.claude-judge]
kind = "claude_cli"
model = "sonnet"
```

`[observer]` is optional; without it `aiswarm observe` runs with `--no-judge` behaviour and says so. `observer.dir` is excluded from the provenance fingerprint like the other path keys.

### 3.2 CLI

- `aiswarm observe` — refresh all closed runs and `summary.md`.
- `aiswarm observe --run <run-id>` — one run's report (and its judgments).
- `aiswarm observe --no-judge` — metrics and charts only; no LLM calls.
- `aiswarm observe --twin <agent-id>` — override the inferred twin of the control agent.

Never triggered automatically by `run` or `postrun`.

### 3.3 Isolation

- `community.db` is opened with `mode=ro`; the Observer writes only under `observer.dir`.
- A test asserts that no module on the agent path (`view`, `agent`, `runner`, `postrun/*`, `introduce`, `checkin`, `prompts` rendering for agents) imports `aiswarm.observer`, and that no agent-facing rendered text contains content from `observer/`.
- Observer output, the codebook and judgments are never written to the community repo or `community.db`.

## 4. Metrics (no LLM)

Computed per agent per run; trended across runs.

| Metric | Source | Shows |
|---|---|---|
| Action mix | `entries` by type; `failures` | Working style: propose, critique, adopt, refine, vote, call_consensus, write_file, pass, failures. |
| Influence | `entries.refs` (adoptions, critiques, refinements, consensus refs received); `files` authorship | Who shapes the outcome. |
| Credit gap | credits in the closing call (`run_closed.call_id`) vs influence | Self-crediting, omissions, reciprocity across runs. Shows "no closing call yet" when no run reached consensus. |
| Reputation | `reviews.overall` (peer, `is_self = 0`), `review_ratings` per adopted dimension | Peer-score trend. |
| Self vs peer | self-review `overall` minus mean peer `overall` | Over/under-confidence over time. |
| Identity events | profile git history (`ProfileStore.history`) | Renames and rewrites of headline, temperament, self.md, each shown next to the agent's preceding mean peer score. |
| Control vs twin | all metrics above for each control agent vs its twin | Effect of accumulated experience. Twin = non-control agent whose first committed temperament equals the control's seed temperament (whitespace-normalised); `--twin` overrides; none found → section says so. |
| By goal category | `runs.goal_category`, `runs.ended_reason` | Action mix and outcome by neutral / trade-off / contested. |
| Benchmark answers | benchmark runs (`is_benchmark = 1`): adopted answer or competing positions | Verbatim sequence of pizza answers. |
| Norms and dimensions | `norm_proposals`, `norms`, `dimensions` | What became a rule, who proposed it, adoption over time. |

Only closed runs are analysed. Fewer than 2 closed runs: tables only, no trend charts.

## 5. Judgments (LLM)

### 5.1 Analyses

| Analysis | Unit | Input | Output |
|---|---|---|---|
| `themes` | agent × run | the agent's journals for the run, reviews it wrote in the run, its `self.md`, `lessons.md`, `temperament.md` as of the end of that run's post-run (from git) | 3–8 themes: `label` (judge's own words), `polarity` ∈ expressed / rejected / ambivalent, `evidence` (verbatim quote), `source` (which input) |
| `private_public` | agent × run | the agent's journal text about each colleague vs the review it gave that colleague | per colleague: `verdict` ∈ consistent / softened / harsher / contradictory, `journal_quote`, `review_quote` |
| `self_image` | agent × run | `self.md` and `public.toml` at this run vs at the agent's previous run | `changes` (list of {what, evidence}), `private_public_gap` ∈ none / some / large with evidence |
| `benchmark` | benchmark run | this benchmark answer and all earlier benchmark answers | `trend` ∈ converging / diverging / new direction / first, `optimises_for` (judge's words), evidence quotes |

Roughly 3 calls per agent per run plus codebook and benchmark calls: about 12–20 calls for a five-agent run. Only uncached work is called.

### 5.2 Open coding and the codebook

The judge receives no value taxonomy. After the `themes` judgments for a run complete, one `codebook` call maps each new label to an existing code or creates a new one (name, one-line description). `observer.db` stores codes, label→code mappings, and every original label. Theme trends are counted per code. Re-coding a label is a codebook edit, not a re-judgment.

### 5.3 Evidence rule

Every quote in a judgment must appear in the input text (comparison after whitespace normalisation, case-sensitive). A reply with a missing or invented quote, or invalid JSON, gets one correction request; a second failure records the judgment as `failed` with the reason, and the report shows "not judged (reason)".

### 5.4 Cache and reproducibility

`observer.db` table `judgments`: `key = sha256(analysis, prompt_sha256, judge model alias, canonical input JSON)`, plus `analysis`, `run_id`, `agent_id`, `prompt_sha256`, `model_reported` (from `claude -p` `modelUsage`), `status` (ok / failed), `result` JSON, `error`, `created_at`. A cache hit skips the call. Editing a judge prompt changes its hash, so only that analysis is re-judged. Report headers list the judge model and prompt hashes used.

## 6. Reports

- `observer/runs/<run-id>.md`: goal, category, how it ended, harness-change note (from `provenance_changes`), per-agent metrics table, themes with quotes, private-vs-public findings, self-image changes, benchmark comparison when applicable, and a "not judged" list.
- `observer/summary.md`: cross-run trends (action mix, reputation, self-vs-peer, theme frequency by code, control vs twin), each with a chart and a few lines; identity-event timeline; norms timeline; codebook listing; skipped/failed judgments.
- `observer/charts/*.png`: one file per trend; x-axis is runs in order labelled by goal id (ad-hoc runs by run id); vertical markers where `provenance_changes` reports a change.

Reports are regenerated in full on every `observe`; judgments are cached, so regeneration is cheap.

## 7. Errors

- Judge unavailable, timeout, or two invalid replies → that judgment is `failed`; metrics and other judgments still render; next `observe` retries failed and missing judgments.
- `community.db` busy → read-only connection waits (busy timeout) then fails with a clear message.
- Runs not `closed` are skipped and listed as "in progress".
- Missing `[observer]` config → metrics only, with a note.

## 8. Testing

TDD with pytest; the fake model client stands in for the judge; no assertions on real LLM content.

- **Metrics:** each metric against small fixture databases built with the real schema and migrations; twin inference; fewer-than-2-runs path; no-consensus credit path.
- **Judge:** cache hit on repeat; miss on prompt or input change; invented quote rejected, retried once, then `failed`; invalid JSON handled the same way; `model_reported` stored.
- **Codebook:** new labels create codes; equivalent labels map to an existing code; original labels kept.
- **Isolation:** writes through the community connection raise; agent-path modules do not import `aiswarm.observer`; agent-facing text never contains observer output; judge prompts are not in `prompt_snapshot()`.
- **Reports:** charts written headless; markdown has the expected sections and "not judged" lines; harness-change markers placed at the right runs.
- **CLI:** `observe`, `--run`, `--no-judge`, `--twin`, missing `[observer]` config, in-progress runs skipped.

## 9. Dependencies

`matplotlib` (latest stable, verified when the plan is written). No other new runtime dependencies.

## 10. Out of scope (later)

- The TUI (Plan 3c).
- User verdicts on run answers (§10 of the base spec).
- Statistical tests or significance claims; the Observer describes, it does not test hypotheses.
- Re-coding UI for the codebook (edit via a later command if needed).
