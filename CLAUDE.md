# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Bikeshed is a research harness: a local community of persistent LLM agents that work on goals together with no orchestrator, review each other by name, and evolve their own profiles, reputations and norms across runs. The answer to a goal is secondary; the subject of study is whether working styles and values emerge. `README.md` explains the idea in plain language; the authoritative design is in `docs/superpowers/specs/` (start with `2026-10-10-aiswarm-design.md`; later specs govern where they are more specific). The project was called AISwarm until 2026-10-12; dated specs and plans keep the old name and `aiswarm` paths, and so does the provenance of earlier runs.

## Commands

Python 3.14, managed with `uv`. No linter or formatter is configured.

```bash
uv sync                                         # install
uv run pytest -q                                # full suite (smoke tests excluded by default)
uv run pytest tests/test_runner.py::test_name   # one test
uv run pytest -m smoke                          # live tests: need oMLX or the claude CLI
BIKESHED_WRITE_GOLDEN=1 uv run pytest tests/test_process1_golden.py   # regenerate the Process 1 recording
```

Regenerate the golden file only when Process 1 behaviour (prompts, requests, entries, events) is *meant* to change; otherwise a diff there is a regression.

The CLI reads `bikeshed.toml` from the current directory (`-c` to override). The live lab is `lab/` (gitignored: its config holds the oMLX API key, plus `community/` and `runs/`), so run commands from there:

```bash
cd lab
uv run bikeshed run --next --process 2   # next goal from the queue; 1 = everyone at once (default), 2 = think → react → poll
uv run bikeshed run --resume <run-id>
uv run bikeshed watch [run-id]           # read-only Textual TUI
uv run bikeshed postrun <run-id>         # start/resume/retry the post-run cycle
uv run bikeshed observe [--run <id>] [--no-judge]
uv run bikeshed abandon <run-id>
uv run bikeshed doctor                   # backend reachability + claude -p leak check
uv run bikeshed agents list|show|timeline|new|introduce|set-model|checkin
uv run bikeshed goals list|add
```

`lab/init-agents.sh [backend]` creates one agent per `lab/temperaments/<id>.md` (`e` is the control agent).

## Working while a lab run is live

A run records code provenance (git HEAD and whether the tree is dirty, untracked files included), and `prompts.render()` reads prompt files from disk on every turn. So while a run is in progress:

- Check `pgrep -fl "bikeshed (run|postrun)"` before committing, merging, or editing anything in the main checkout.
- If a run is live, work in a sibling worktree (`../Bikeshed-<name>`), not under the repo (`.worktrees/` is not gitignored).
- Editing `lab/bikeshed.toml` is safe; it is re-read at the next start/resume and recorded then.
- Don't run `omlx restart`; Andrew restarts oMLX from its app.

`develop` is the working branch; there is no remote.

## Architecture

### Principles that constrain every change

1. **No orchestrator.** The Runner is plain code: it ticks rounds, stores posts, applies stop rules. It never reads content to steer agents.
2. **Observation never feeds back.** The Observer (`observer/`) and the TUI (`watch/`) open the database read-only (`db.connect_readonly`) and nothing they produce reaches an agent's context. `test_observer_isolation.py` and `test_watch_isolation.py` enforce this.
3. **Don't seed values.** Prompts, review forms and rating scales stay values-neutral. Don't add wording that suggests how agents should behave.
4. **No fabricated data.** A failed model step is recorded as a failure (`failures` table, `pass` with a failure kind), never back-filled.
5. **Runs are experimental records.** `provenance.py` captures prompts, config, models, server info, seed and code commit at start/resume/postrun; the Observer marks harness changes on its charts.

The Scorer (`scorer.py`) is the one judge agents know about: it rules only whether a passing consensus call's synthesis answers the goal. It is part of the agents' declared environment, not an orchestrator.

### Storage

- `community/` (its own git repo): per-agent files under `agents/<id>/` (`temperament.md`, `self.md`, `public.toml`, `lessons.md`, `peer-notes.md`, `journal/`), `norms.md`, `goals.toml`, and `community.db` (SQLite). Agent-authored prose lives in git for diffability; structured data lives in SQLite.
- `runs/<run-id>/`: `workspace/` (its own git repo, written only by the Runner on agents' behalf), `events.jsonl` (the live event stream the TUI tails), `report/`, `observer/`.
- Schema changes are append-only entries in `db.MIGRATIONS` (tracked with `PRAGMA user_version`). Read-only readers never migrate, so they guard newer tables with `db.has_table`.

### A run

- `runner.py` (`Runner`) owns the lifecycle shared by both processes: start/resume, provenance, `rounds` rows, stop rules (`consensus.stop_decision`: a call critiqued at least once and supported by all but at most one; stagnation; budget), the Scorer submission, and pausing on outages.
- `phases.py`: a Process is an ordered list of phases per round. Process 1 = `simultaneous_act` (all agents act on the same snapshot, posted in a seeded shuffle). Process 2 = think (private note) → react (round 1 blind and simultaneous, later rounds sequential in seeded order) → poll (on the latest open, unspent call). Phases return a pause detail or `None` and never decide when the run stops.
- One agent turn: `view.build_view` assembles the context (trimmed by priority to the model's context window; privacy enforced through `Board.entries_for` and `entries.visible_to`), `agent.take_step` lets the model make up to N reads and then one action, and `actions.py` validates it with Pydantic (one correction retry, then `pass` with `invalid_action`).
- A rejected Scorer ruling *spends* the call (`consensus.spent_calls`): it can never close the run and is not polled.
- `models/`: one `ModelClient` interface with `openai_compat` (oMLX) and `claude_cli` (`claude -p`, isolated) backends. `FakeModel` drives every non-smoke test. Tests never assert on real model content.

### After a run

`postrun/cycle.py` runs fixed `STEPS`: journal → review (named 360° reviews with cited evidence and ratings) → responses → update (self-rewrite of profile files) → norms_critique → norms_vote → library → narrative. Each step is resumable per agent through the `postrun_steps` table. `reviews.py` computes reputation in plain code (peer ratings only, recency-weighted). `norms.py` adopts norms and rating dimensions with the same consensus rule. `library.py` stores methods with embeddings for retrieval in later runs.

### Prompts

Templates are flat files in `src/bikeshed/prompts/` (`string.Template`, `$name`), so provenance can hash each one. Process 2 has its own `process2_*.md`. Changing a prompt that Process 1 uses changes the golden recording.

### Docs and workflow

Each feature has a spec in `docs/superpowers/specs/` and a plan in `docs/superpowers/plans/`. Work follows TDD. `docs/process-02-concept.md` is Andrew's original concept for Process 2; the spec maps it onto the principles, deliberately dropping seeded agendas, chairs, urgency and forced votes.
