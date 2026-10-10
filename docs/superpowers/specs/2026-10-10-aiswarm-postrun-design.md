# AISwarm — Post-run Cycle Design (Plan 2 addendum)

- **Date:** 2026-10-10
- **Status:** Draft, awaiting review
- **Author:** Andrew Marconi (with Claude)
- **Extends:** `2026-10-10-aiswarm-design.md` (the Phase 1 spec). Where this addendum is more specific than §5 of that spec, this addendum governs; everything else there still applies, including the principles (values-neutral prompts, observation never feeds back, privacy invariants).

## 1. Purpose

Plan 1 built the run engine: a run ends, and nothing carries forward. This addendum designs the post-run cycle, which is what turns runs into accumulated experience. After it exists, agents remember, are reviewed by named colleagues, earn a public reputation, rewrite themselves, and (in 2b) share a method library and a code of conduct.

## 2. Decisions taken in brainstorming

| Topic | Decision |
|---|---|
| Process account | Both: `account.md` assembled by code from the board's `refs` graph and decision events (only agents' own words are quoted), plus `narrative.md` written by a neutral narrator model from the same facts. Both are user-only; no agent ever sees either. |
| Reviews in later runs | On request: each View shows a reputation line per colleague; a new free read, `read_reviews <agent>`, returns that agent's named reviews and responses. It shares the existing budget of 3 reads per step and does not cost the agent its turn. |
| Private note in reviews | The "what would you tell them privately?" part of a review goes only to the reviewee. Everything else in a review is public. |
| Sequencing | One addendum, two plans. **2a — personal loop** first; **2b — community memory** second. |
| Lessons from live runs | The cycle must work when a run ends without consensus; post-run calls use the same JSON handling as run steps (schema not enforced for `thinking = true`); simultaneous model calls are capped (`max_concurrent_calls`) because 4 concurrent thinking agents exceeded oMLX's memory ceiling on 2026-10-10. |

## 3. Plan 2a — personal loop

### 3.1 Trigger and resumability

The cycle runs automatically after `aiswarm run` closes a run (`--no-postrun` skips it). `aiswarm postrun <run-id>` starts, resumes, or retries it. Each step is recorded per agent in `postrun_steps` (`run_id`, `step`, `agent_id`, `status` ∈ `done` | `failed`, `detail`); a resumed cycle skips steps marked `done` and never writes an artifact twice. If every call in a step fails (e.g. oMLX outage), the cycle stops with status `paused` and the CLI prints the resume command.

### 3.2 Steps

1. **Feedback packets (code, no model).** For each participating agent, a factual summary of the run: its own entries; for each, who adopted, critiqued, questioned, or refined it (with entry ids); credits received (if a call was made); whether the winning call's `refs` include its entries; how the run ended (`consensus` | `stagnation` | `budget`); the outcome itself — the content of the run's `answer.md` (the consensus synthesis with credits, or the competing positions); its own failed steps. Built identically whether or not the run reached consensus.
2. **First-impressions journal (1 call per agent).** Input: the agent's private profile and its feedback packet. Output: a private journal entry. Written before any reviews exist, so the self-assessment is uncoloured by peers.
3. **Reviews (1 call per reviewer–reviewee pair).** Each agent reviews every colleague it interacted with, plus itself. *Interacted* means either agent's entry `refs` the other's, or both wrote the same workspace file. The form is values-neutral: *"What did this colleague do that you'd want more of? Less of? What would you tell them privately?"* Output fields: `more_of`, `less_of`, `private_note`, `evidence` (list of entry ids; every id must exist in this run, else the review is invalid), `overall` (integer 1–5) answering *"Would you want to work with this colleague again?"*, `overall_reason`. A self-review uses the same form and is stored with `is_self = true`.
4. **Responses (1 call per reviewee).** The agent reads every review it received this run, including private notes addressed to it, and writes one response per review: `stance` ∈ `accept` | `dispute`, plus `reason`. Responses are public alongside the reviews they answer. An agent with no peer reviews skips this step.
5. **Update (1 call per agent).** Input: private profile, feedback packet, first-impressions journal, reviews received, own responses. Output: a second journal entry (after reviews); a new `lessons.md` (hard size cap — the agent must compress); and optional rewrites of `self.md`, `temperament.md`, `peer-notes.md`, and the public profile (name, headline, introduction). Validation is the existing profile validation (size caps, non-empty, public-profile rules). An invalid rewrite is rejected, the previous version kept, and the rejection appended to the journal. Every accepted change is a git commit in the community repo.
6. **Reputation (code).** See §3.4.

Roughly 7 calls per agent (about 28 for 4 agents). Steps 2, 3, 4, 5 each fan out concurrently across agents, bounded by `max_concurrent_calls`.

### 3.3 Prompts and output handling

Each step has its own system prompt and request template in `prompts/`, rendered with `string.Template` like Plan 1's. Prompts state the task and the output format only; they never suggest what a good colleague, review, or self is. Outputs are JSON validated by Pydantic; schema enforcement follows the backend rules (enforced unless `thinking = true`). One correction retry with the validation error, then the call is recorded as failed for that step and agent, and nothing is fabricated (no review means no rating).

### 3.4 Reputation

Computed on demand from `reviews` (never stored, so it cannot drift):

- For agent X: weighted mean of `overall` over all **peer** reviews of X (`is_self = false`), with weight `0.5 ** (age / reputation_half_life_runs)`, where `age` is the number of the community's runs closed after the reviewed run. Reported with the count of peer reviews.
- Self-ratings are excluded from the public score and remain available to the Observer (Plan 3).
- An agent with no peer reviews shows `no reviews yet`.

### 3.5 View changes

- Private section: the agent's 2 most recent journal files, after `self.md`, `lessons.md`, and `peer-notes.md`.
- Norms section: contents of `community/norms.md` under `# Community norms` when the file is non-empty (always empty until 2b).
- Each member line gains reputation: `- Wren (b) — headline · ★4.2 (12 reviews)` or `· no reviews yet`.
- New free read `read_reviews` with argument `agent_id`: returns that agent's reviews received (reviewer shown as `Name (id)`, run id, `more_of`, `less_of`, evidence ids, `overall`, `overall_reason`) and each response, newest first, truncated to the same per-read character budget as `read_file`. Never includes `private_note`.
- Privacy invariants extend to: journals and `private_note` never appear in another agent's View or in any `read_reviews` result.

### 3.6 Concurrency cap

`[run] max_concurrent_calls` (default 4, ≥ 1) limits simultaneous model calls across all agents, in runs and in the post-run cycle. Implemented as one shared limiter wrapping every client the Runner and the cycle use.

### 3.7 Storage

New tables:

- `reviews`: `id`, `run_id`, `reviewer`, `reviewee`, `is_self`, `more_of`, `less_of`, `private_note`, `evidence` (JSON list), `overall`, `overall_reason`, `created_at`. Unique on (`run_id`, `reviewer`, `reviewee`).
- `review_responses`: `review_id` (unique), `stance`, `reason`, `created_at`.
- `postrun_steps`: as in §3.1, unique on (`run_id`, `step`, `agent_id`).

Journals: `community/agents/<id>/journal/<run-id>.md`, holding the first-impressions entry and, appended later, the after-reviews entry. Journal files are private profile files (subject to the privacy invariants) and are committed to git.

### 3.8 CLI

- `aiswarm postrun <run-id>` — start, resume, or retry the cycle.
- `aiswarm run … --no-postrun` — skip the automatic cycle.
- `aiswarm agents show <id>` — adds reputation, reviews received (with responses), and the latest journal entry. (The full timeline remains Plan 3.)

### 3.9 Errors

| Failure | Handling |
|---|---|
| A call fails or stays invalid after one correction | Recorded in `failures` with the step name; that step is `failed` for that agent; the cycle continues |
| Review cites an entry id not in this run | Invalid; handled by the correction retry, then as above |
| Every call in a step fails | Cycle stops as `paused`; `aiswarm postrun <run-id>` resumes |
| Crash mid-cycle | `postrun_steps` makes the rerun idempotent |
| Invalid rewrite | Rejected; previous version kept; rejection appended to the journal |

### 3.10 Testing

Fake-model integration tests drive whole cycles; tests assert on system behaviour, never on LLM content: interaction detection (refs either way; shared workspace file); evidence validation; self-rating exclusion; recency-weighting maths; no-consensus runs; resume after a crash without double-writing; journal and private-note privacy (View and `read_reviews`); rejected rewrites keep the previous version; `max_concurrent_calls` honoured. A manual smoke cycle on the 4B oMLX model.

## 4. Plan 2b — community memory

### 4.1 Reporter: account and narrative

- `account.md` (code): a structured timeline built from the `refs` graph and decision events — opening proposals, critiques that were followed by a `refine` or `adopt` of the critiqued entry, adoptions (who switched to whom), consensus calls and their votes, and how the run ended. Only agents' own words are quoted. Not a transcript.
- `narrative.md` (narrator model): a concise prose account written from `account.md` plus the reports, by the backend named in `[report] narrator` (any configured model). Omitted if no narrator is configured. User-only.

### 4.2 Method Library

- After each run, code records each `propose` and `refine` method as a library entry: method text (the agent's own words), author, run id, goal text, and reception computed from the board (adoptions, critiques, refines, inclusion in the winning call's `refs`).
- Embeddings via an OpenAI-compatible embeddings backend named in config (`[library] embeddings = "<model name>"`); verified on 2026-10-10 that oMLX serves `mlx-community--all-MiniLM-L6-v2-4bit` (384 dimensions). Stored as BLOBs in SQLite; similarity is cosine in Python (the library stays small).
- At seeding, the goal is embedded and the top `library_top_k` (default 3) methods are shown in the View (§4.2 item 7 of the Phase 1 spec) with author, reception, and run id.

### 4.3 Norms and rating dimensions

- In the update step (§3.2 step 5) an agent may also propose a norm, an amendment to an existing norm, or a new rating dimension (`name`, `question`).
- Deliberation is two fixed steps after all updates: (a) every agent sees all proposals and may critique each; (b) every agent votes `support`/`object` with a reason. Adopted by the run consensus rule: support from all but at most one active agent, and at least one critique in step (a).
- Adopted norms are written to `community/norms.md` (git commit). Rejected proposals are kept in `norm_proposals` with their votes.
- Adopted dimensions are rated from the next cycle onward: each review then also carries a 1–5 rating and reason per adopted dimension, and reputation (§3.4) is computed per dimension with the same weighting.

## 5. Out of scope here

Run provenance, control agent, goal queue, agent timeline, TUI, Observer (all Plan 3); agent-voted consequences (Phase 2).
