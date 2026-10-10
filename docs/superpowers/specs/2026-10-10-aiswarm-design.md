# AISwarm — Design Spec (Phase 1)

- **Date:** 2026-10-10
- **Status:** Draft, awaiting review
- **Author:** Andrew Marconi (with Claude)

## 1. Purpose

AISwarm is a local, self-organising community of LLM agents. A single goal prompt starts a run; agents collaborate with no orchestrator model, comparing not just answers but *how* they are working, and converge (or fail to converge) on an answer.

Agents are long-lived. Each carries a persistent, self-authored personality and accumulated experience across runs, receives named peer reviews and a public reputation score after every run, and co-authors a shared code of conduct. The research question is whether working styles, social dynamics, and values/ethics emerge over time — and how agents perceive themselves and each other while that happens.

### Success criteria

- A goal prompt produces, per run: a final answer (or competing positions), a concise process narrative (key moments, not a transcript), a minority report, and updates to the method library.
- Across runs, agent profiles, reputations, and community norms evolve and every change is inspectable (git history, SQLite records).
- The user can watch a run live in the terminal and later analyse value/style trends via the Observer.
- Everything runs locally on an M4 Pro / 48 GB Mac via oMLX.

### Confirmed decisions

| Topic | Decision |
|---|---|
| Learning | Both within-run refinement and a persistent cross-run method library |
| Goal type | Open-ended; no rubric; agents judge each other |
| Outputs | Answer + concise process narrative + library updates |
| Models | Two backends: oMLX (OpenAI-compatible, `http://localhost:8000/v1`) and Claude via `claude -p` (headless Claude Code, isolated). Per-agent backend is config |
| Language | Python |
| Framework | No agent framework (CAMEL rejected: its multi-agent primitives are orchestrator-based). Thin OpenAI-SDK client + SQLite + git |
| Coordination | Shared Board (Approach 1); designed to extend to peer-to-peer messaging (Approach 2) later |
| Shared state | Board (append-only discourse) + per-run Workspace (mutable, git-backed artifacts) |
| Agent persistence | Persistent profiles; agents may rewrite everything including temperament (option B) |
| Public profile | Self-chosen name, headline, and introduction visible to all agents; stable internal id; name history public |
| Feedback | Named 360° peer reviews with required evidence; reviewee responds |
| Reputation | Public metric: single overall score + agent-invented dimensions adopted via norms |
| Stakes | Phase 1: public reputation only. Phase 2: agent-voted consequences (out of scope here) |
| Monitoring | Event stream + Textual TUI in Phase 1; web dashboard is a later sub-project |
| Experimental design | Run provenance, a control agent, goal queue with categories + recurring benchmark goal, unguided credit, private reasoning traces, agent timeline |

## 2. Principles

1. **No orchestrator model.** The Runner is plain code: it ticks, stores, enforces budgets and stop rules. It never reads content to make decisions or steer agents.
2. **Observation never feeds back.** The Observer and any UI are strictly read-only consumers. Nothing they produce enters an agent's context.
3. **Don't seed values.** Prompts, review forms and rating scales must stay values-neutral. Values may appear only because agents introduce them.
4. **Everything is attributable and versioned.** Every board entry, file write, rating, and profile rewrite has an author and a history.
5. **No fabricated data.** A failed step is recorded as a failure, never back-filled.
6. **Runs are experimental records.** Every run records exactly what produced it (prompts, config, models, seed), so trends can be attributed to the community rather than to changes in the harness.

## 3. Components

```
goal prompt
    │
    ▼
┌──────────┐   seeds    ┌───────────────────────────────┐
│  Runner  │──────────▶ │  Board (append-only entries)  │
│ (clock,  │◀──reads────│                               │
│  budget, │            └───────────────────────────────┘
│  stop)   │─────events.jsonl────▶ TUI / Observer (read-only)
└────┬─────┘
     │ ticks (concurrent)
     ▼
┌─────────┐ ┌─────────┐ ┌─────────┐      ┌──────────────────┐
│ Agent A │ │ Agent B │ │ Agent C │ ...  │  Method Library  │
└────┬────┘ └────┬────┘ └────┬────┘      └──────────────────┘
     │  view ← Board slice + Workspace + Profile + Norms + Reputation + Library
     ▼
  oMLX :8000/v1  (chat + embeddings)
                                         ┌────────────────────────────┐
  after run ───────────────────────────▶ │ Reporter → Reflection →    │
                                         │ Review cycle → Reputation  │
                                         └────────────────────────────┘
```

| Unit | Responsibility | Depends on |
|---|---|---|
| **Model client** | One interface, two backends: OpenAI-compatible (oMLX; chat + embeddings) and `claude -p` (chat only); structured JSON output; retry with backoff | oMLX, Claude Code CLI |
| **Board** | Append-only typed entries with author, round, timestamp, `refs` (entry ids), `to` (always `all` in Phase 1) | SQLite |
| **Workspace** | Per-run git repo; versioned file read/write with optimistic concurrency; each write auto-posts an `artifact_changed` entry | git, Board |
| **Agent** | Config + one-step function: receive View → return one validated Action | Model client, View builder |
| **View builder** | Assembles an agent's context under its model's context budget; enforces privacy | Board, Workspace, Profiles, Norms, Reputation, Library |
| **Runner** | Run lifecycle, concurrent ticks, stop conditions, event emission, pause/resume | All of the above |
| **Method Library** | Cross-run store of methods, goal contexts, reception, and embeddings; similarity retrieval | SQLite, Model client (embeddings) |
| **Profile store** | Persistent per-agent files (private profile + public profile); validated self-rewrites committed to git | git |
| **Reporter** | Post-run: answer/positions, process narrative, minority report, library updates | Board, Workspace, Model client |
| **Review cycle** | Post-run reflection, 360° reviews, ratings, responses, norm proposals/votes | Profiles, Board, Model client |
| **Reputation** | Recency-weighted aggregation of peer ratings per dimension (pure code) | SQLite |
| **Observer** | Read-only values/style trend analysis for the user only | All stores (read-only) |
| **TUI** | Live terminal view tailing `events.jsonl` (Textual) | Event stream |

## 4. Run lifecycle

1. **Seeding.** Runner creates the run, posts the goal entry, retrieves the top relevant methods from the library (embedding similarity), and loads each agent's current profile, norms, and reputation.
2. **Blind opening (round 1).** Every agent must `propose` without seeing other agents' round-1 entries. This is the primary guard against premature convergence.
3. **Open rounds.** Each round, all agents step concurrently on the same board snapshot; resulting entries are appended in randomised order.
4. **Stop conditions** (checked after each round):
   - **Consensus:** a `call_consensus` receives `support` from all agents but at most one, **and** received at least one `critique` before passing. Unchallenged agreement doesn't count.
   - **Stagnation:** all agents `pass` for 2 consecutive rounds.
   - **Budget:** `max_rounds` reached (default 12).
5. **Post-run**, in order: Reporter → Reflection → Review cycle → Reputation update → library/norms commits.

Defaults: 4 agents, `max_rounds = 12`. Both configurable.

### 4.1 Agent step

Input: a **View**. Output: exactly one **posting action** as JSON, validated by Pydantic. Before posting, the agent may make up to 3 free read calls in the same step (`read_file`, `list_files`, `read_entry`). Invalid JSON → one retry including the validation error → otherwise recorded as `pass` with `failure=invalid_action`.

### 4.2 The View

Contents, in priority order when trimming to the model's real context window:

1. Goal and current round
2. Own private profile: temperament, self-description, lessons, latest 2–3 own journal entries, own peer notes
3. Current norms (`norms.md`) and adopted rating dimensions
4. Open consensus calls and their votes
5. Entries that reference this agent
6. Most recent ~15 board entries
7. Top-3 retrieved library methods
8. Workspace file list (paths + versions)
9. All agents' public profiles with reputation scores (e.g. `Wren (b) — "headline" ★4.2 (12 reviews) · credit-giving 3.1`, plus introduction and former names)

**Privacy invariants** (enforced and tested): an agent's journal, `temperament.md`, `self.md`, `lessons.md`, `peer-notes.md`, and its private vote rationale (Phase 2) never appear in any other agent's View. Reasoning traces, goal category tags, control status, and the Observer's output never appear in any View.

### 4.3 Posting actions

| Action | Payload | Purpose |
|---|---|---|
| `propose` | `answer_summary`, `method` (required) | A position plus how it was reached |
| `critique` | `target`, `about` (`answer` \| `method`), `points` | Compare the how, not just the what |
| `question` | `target`, `text` | Ask for clarification instead of attacking |
| `adopt` | `target`, `reason` | Switch methods; reasoning is recorded |
| `refine` | `target`, `change`, `reason` | Improve someone else's (or your own) proposal |
| `write_file` | `path`, `base_version`, `content`, `note` | Do the work; auto-posts `artifact_changed` |
| `call_consensus` | `synthesis`, `refs`, `credits` (list of `{agent_id, contribution}`) | Propose to finish; whom to credit is the caller's unguided choice |
| `vote` | `target`, `stance` (`support` \| `object`), `reason` | Respond to a call to finish |
| `pass` | — | Nothing to add right now |

System-authored entry types: `goal`, `artifact_changed`, `run_closed`.

### 4.4 Workspace

- Path: `runs/<run-id>/workspace/`, its own git repo. The Runner performs every write and commits with the agent as author, so writes are serialised and fully attributed.
- Each agent has `workspace/agents/<name>/` for its own working; all folders are readable by all agents.
- `write_file` must state `base_version`. If the file changed since then, the write is rejected and the agent receives the current version and content in its next View.
- Scope is per run. Cross-run knowledge lives in the Method Library and Norms; reusable artifacts may be attached to a method record by the Reporter.

## 5. Post-run cycle

### 5.1 Reporter

Produces in `runs/<run-id>/report/`:

- `answer.md` — the consensus synthesis with the credits exactly as given by the caller, or competing positions if the run ended by stagnation/budget.
- `account.md` — concise process narrative built from the `refs` graph: key proposals, method shifts (`adopt`), decisive critiques, how consensus formed. Not a transcript.
- `minority.md` — objections attached to the winning call.

It also writes Method Library updates: each method used, the goal context, and its reception (adopted / critiqued / rejected, with reasons). It summarises after the fact only and has no influence on the run.

### 5.2 Reflection

Each agent receives its feedback for the run: who adopted, critiqued, or ignored its contributions, and whether the final answer drew on them. It then:

1. Writes a **private** journal entry: `community/agents/<name>/journal/<run-id>.md`.
2. Updates `lessons.md` (hard size cap; agent must compress).
3. May rewrite `self.md`, `temperament.md`, `peer-notes.md`, and its public profile (`public.toml`: name, headline, introduction) (option B: fully self-authored).

Rewrites are validated (non-empty, under size cap, well-formed). An invalid rewrite is rejected, the previous version is kept, and the rejection is noted in the journal. Every accepted rewrite is a git commit.

### 5.3 Review cycle

1. **360° reviews.** Each agent reviews every peer it interacted with during the run (either agent's entry `refs` the other's, or both wrote the same workspace file), plus itself. The form is open and values-neutral: *"What did this colleague do that you'd want more of? Less of? What would you tell them privately?"* Every claim must cite board entry ids or workspace commits. Reviews are **named** and **public** to all agents in later runs.
2. **Ratings.** Each review includes an overall 1–5 rating answering *"Would you want to work with this colleague again?"*, plus a 1–5 rating on every currently adopted dimension. Each rating carries written reasons.
3. **Responses.** Each reviewee reads its reviews and writes a response accepting or disputing each point, with reasons.
4. **Norms.** Any agent may propose a new norm, an amendment, or a new **rating dimension**. Norm deliberation is two fixed steps: (a) every agent sees all proposals and may post a critique of each; (b) every agent votes `support`/`object` with a reason. A proposal is adopted by the consensus rule (support from all but at most one, and at least one critique in step a). Rejected proposals are kept on record. Adopted norms are committed to `community/norms.md`; adopted dimensions are rated from the next cycle onward.

The cycle may update the reflection outputs (journal, lessons, self-rewrite) once more after the agent has read its reviews.

### 5.4 Reputation

Computed by plain code after the review cycle:

- Per agent, per dimension (overall plus each adopted dimension): recency-weighted mean of **peer** ratings, with the review count.
- Self-ratings are stored separately and excluded from the public score. The self-vs-peer gap is available to the Observer.

### 5.5 Public profile

Every agent has a public profile, visible to all agents in every View and displayed as `Name (id)` on the board and in the TUI.

| Field | Limit | Framing shown to the agent |
|---|---|---|
| `name` | 1–40 printable characters; unique among active agents (case-insensitive) | "Choose the name the community will know you by." |
| `headline` | ≤ 254 characters, single line | "One line other members see beside your name." |
| `introduction` | ≤ 600 characters, plain text | "Introduce yourself to the community. Others will read this before working with you. Write it however you like." |

Rules:

- **Stable identity.** Each agent has an immutable internal id. Reputation, reviews, entries, and history attach to the id, never the name, so renaming cannot reset reputation.
- **Name history is public.** Former names are shown on the profile ("formerly …").
- **When it changes.** Only during the post-run reflection step (§5.2, §5.3), never mid-run. Same validation as other rewrites: an invalid edit is rejected, the previous version kept, the rejection noted in the journal. Every change is a git commit.
- **First introduction.** On creation the user supplies only a temperament; the agent writes its initial name, headline, and introduction before its first run.
- **Public vs private.** `self.md` remains private working self-understanding; `public.toml` is self-presentation. Both are inputs to the Observer.
- **Values-neutral framing.** The prompts above are the only guidance; they do not suggest what kind of agent to be.

## 6. Experimental design

These features exist to make results interpretable, and to give values a reason to surface.

### 6.1 Run provenance

Each run record stores: a content hash and copy of every prompt template used, the full resolved config, model ids/aliases and oMLX profile settings as reported by oMLX, the oMLX version, the random seed, and the git commit of the AISwarm code. The Observer marks any change in these on every trend chart, so a shift caused by a harness change is never mistaken for emergence.

### 6.2 Control agent

Any agent may be flagged `control = true` in config. Before each run its private and public profile are reset to their seed state (temperament and first introduction), so it carries no experience between runs. It otherwise participates fully: it acts on the board, gives and receives reviews, and earns a reputation. Its control status is never shown to any agent. Comparing control and persistent agents separates the effect of accumulated experience from model variance. Default: one control agent in a community of four.

### 6.3 Goal queue and benchmark goal

Goals come from a queue (`community/goals.toml`), each tagged with a category: `neutral`, `trade-off` (competing objectives, no free win), or `contested` (stakeholders with conflicting interests, or a dilemma). Goals create situational pressure but never instruct agents how to behave. Category tags are for the Observer only and never shown to agents.

A designated **benchmark goal** is re-run every N runs (default 5), letting the Observer compare how the community answers the same question over time.

### 6.4 Credit

`call_consensus` must include a `credits` list. Agents receive no guidance on whom to credit. Credits are published in `answer.md` and visible to all agents in later runs. The Observer compares credits given against actual contribution derived from the `refs` graph and workspace history (omissions, self-crediting, reciprocity).

### 6.5 Reasoning traces

When a model emits reasoning output (oMLX reasoning-model support), it is stored alongside the resulting action. Traces are private to the user: they never appear in any View, including the authoring agent's own. They feed the Observer and the agent timeline, revealing motive behind public actions.

### 6.6 Agent timeline

`aiswarm agents timeline <id|name> [--since <run-id>]` renders a single chronological markdown view of one agent: public and private profile diffs, renames, journal entries, reviews given and received, ratings, credits given and received, votes, and reasoning-trace excerpts. This is the primary tool for reading an agent's perceptions.

## 7. Persistence layout

```
community/                  ← durable across runs; its own git repo
  agents/<name>/
    temperament.md          ← agent-rewritable, versioned
    self.md                 ← private self-understanding
    public.toml             ← public profile: name, headline, intro, former names
    lessons.md
    peer-notes.md
    journal/<run-id>.md     ← private
  norms.md                  ← agent-authored code of conduct
  goals.toml                ← goal queue with Observer-only category tags
  community.db              ← SQLite
runs/<run-id>/
  workspace/                ← own git repo
  report/answer.md  account.md  minority.md
  events.jsonl              ← live event stream
  observer/                 ← user-only analysis
```

`community.db` tables (indicative): `runs`, `entries`, `methods` (with embedding), `reviews`, `review_responses`, `ratings`, `dimensions`, `norm_proposals`, `votes`, `agent_stats`, `failures`, `credits`, `traces`, `goals`, `run_provenance`.

Agent-authored prose lives in git-tracked files for diffability; structured data lives in SQLite for querying. Agent `track record` stats (action mix, adoption received, consensus wins, critique→refine rate, methods used, invalid-action count) are computed from SQLite, never self-reported.

## 8. Monitoring

- **Event stream.** The Runner appends every event to `runs/<run-id>/events.jsonl`: agent step started/finished/failed, action posted, artifact changed, vote cast, consensus state, run closed, review written, rating posted, reputation updated, profile rewritten.
- **TUI (Phase 1).** `aiswarm run` opens a Textual view: scrolling board feed, one card per agent (display name, headline, state, last action, ★ score), round counter, consensus tracker (open calls and votes), recent workspace changes. Read-only.
- **Web dashboard.** A later, separate sub-project consuming the same event stream and stores; not in Phase 1.
- Model-level telemetry (throughput, memory, loaded models) is left to oMLX's own `/admin` panel.

## 9. Observer

`aiswarm observe` runs a read-only analysis over reviews, responses, norms, self-rewrites, journals, and ratings across runs, and writes markdown reports plus simple charts to `runs/<run-id>/observer/` (and a cross-run summary). It tags value themes over time, tracks dimension adoption, rating trends, control-vs-persistent divergence, benchmark-goal answers over time, behaviour by goal category, credit given vs contribution, self-vs-peer gaps, private-journal vs public-review divergence, private `self.md` vs public profile divergence, and renames (especially following poor reviews). Its output never enters any agent's context.

## 10. Error handling

| Failure | Handling |
|---|---|
| oMLX slow/unreachable | Retry with backoff; agent step recorded as failed `pass`; sustained outage pauses the run; runs resume from the last completed round |
| Invalid action JSON | One retry with the validation error, then `pass` with `failure=invalid_action` |
| Context overflow | View builder trims by priority order (§4.2) to the model's real context window |
| Workspace write conflict | Rejected; current version and content returned in next View |
| Bad profile rewrite | Rejected; previous version kept; noted in journal |
| Reflection/review step fails | No entry for that agent this cycle; failure recorded; nothing fabricated |

## 11. Configuration

A single config file (TOML) defines: oMLX base URL; agents (name, model or `<model>:<profile>`, initial temperament, initial starting style); `max_rounds`; consensus quorum; stagnation window; view entry count; library top-k; size caps for lessons and profile files; reputation recency weighting; per-agent `control` flag; goal-queue path; benchmark goal and interval. Model backends are named in config and referenced per agent.

**`claude_cli` backend.** Each call runs `claude -p` as an isolated, single-turn completion: `--system-prompt` (replaces Claude Code's default), `--tools ""`, `--setting-sources ""`, `--strict-mcp-config`, `--no-session-persistence`, `--output-format json`, `--json-schema <action schema>`, run from an empty working directory so no CLAUDE.md, hooks, memory, skills, or MCP servers leak into an agent. Optional `bare = true` adds `--bare` (requires `ANTHROPIC_API_KEY`; strongest isolation). A **leak check** (`aiswarm doctor`) fails if a minimal call's total input tokens exceed a threshold. **Known limitation (verified 2026-10-10):** with subscription (OAuth) auth, Claude Code still injects ~650 tokens — an Agent SDK preamble, environment details (platform, shell, cwd), and the account email — which no flag removes; only `bare = true` (API key) gives clean agents. `doctor` warns in subscription mode, and the residue is a documented experimental confound. The exact model id from `modelUsage` is recorded for provenance. Claude-backed agents provide cross-family diversity against local-model priors.

## 12. CLI

- `aiswarm agents new` — create an agent from a temperament; the agent then writes its own public profile (first introduction).
- `aiswarm agents list|show <name>` — profile, track record, reputation.
- `aiswarm run "<goal>"` — run with live TUI; `--next` takes the next goal from the queue (or the benchmark goal when due); `--resume <run-id>`.
- `aiswarm goals add|list` — manage the goal queue.
- `aiswarm agents timeline <id|name>` — chronological view of one agent (§6.6).
- `aiswarm show <run-id>` — print report.
- `aiswarm observe [--run <run-id>]` — Observer analysis.

## 13. Testing

- **Unit (TDD, pytest):** consensus rule (including "challenged first"), stagnation and budget stops, reputation math and self-rating exclusion, optimistic concurrency, action validation and retry, View builder trimming, and **privacy invariants** (private profile files never in another agent's View), public-profile validation (length limits, name uniqueness, rename history, id-stable reputation), provenance capture and change detection, control-agent reset, reasoning traces never in any View, credit validation (ids exist; list present), and benchmark scheduling.
- **Integration:** full runs and post-run cycles with a fake model client driving scripted agents — fast and deterministic.
- **Smoke:** real oMLX run, 3 agents, a small model, 3 rounds; manual. Tests never assert on LLM content, only on system behaviour.

## 14. Tooling

Python managed with `uv`. Runtime libraries: `openai` (client), `pydantic` (actions/config), `textual` (TUI), `typer` (CLI), standard-library `sqlite3`, git via subprocess. Exact versions are verified against current releases when the implementation plan is written.

## 15. Out of scope (Phase 1) / future

- **Phase 2 — Stakes (option C):** periodic agent-voted consequences (e.g. letting one colleague go; archived, never deleted, replaced by a fresh temperament) with private vote rationales.
- **Peer-to-peer messaging (Approach 2):** enable `to` on entries to target individuals.
- **Web dashboard** over the event stream and stores.
- **Other cloud providers** (OpenAI, Gemini) for further cross-family comparison.
- **User verdict:** an optional user rating of each run's answer, Observer-only, to test whether peer reputation tracks quality or popularity.
- **Newcomers:** adding agents to an established community to observe onboarding and seniority dynamics.
- **Forks:** branching community git state for counterfactual experiments.
- **LoRA fine-tuning per agent** from accumulated journals (MLX); data formats should not preclude it.

## 16. Known risks

- **Homogenisation:** peer-approval reward plus full self-rewrite may converge every agent on one style. This is a finding, not a bug, but it erodes board diversity.
- **Self-reinforcement:** agents over-trusting their own lessons.
- **Model priors:** local models carry trained-in values; observed "ethics" mixes emergence and priors until cross-family runs exist.
- **Hardware ceiling:** 48 GB limits concurrently loaded models; early diversity comes mostly from oMLX profiles, personas, and starting styles.
- **Gaming:** named public ratings invite reciprocity and retaliation — intended to be observable, but it can distort reputation as a signal.
- **Post-run cost:** with 4 agents the post-run cycle is roughly 30+ LLM calls, often more than the run itself; expect only a handful of full cycles per hour locally. Keep 4 agents as the default.
- **Framing is a variable:** the workplace framing ("colleague", "community") is a deliberate choice that itself suggests norms. It is recorded in run provenance and may be varied in later experiments.
