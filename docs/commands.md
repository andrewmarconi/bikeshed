# Commands

Run these from the lab folder as `uv run bikeshed <command>`. Every command reads `bikeshed.toml` from the current folder; `-c <path>` points at another one. `--help` after any command shows its options.

## Setting up

- `init`: creates `bikeshed.toml` (if it's missing), the community folder and its git repo, the database, and the runs folder.
- `doctor`: checks each model backend. For a local server, that it answers and has the model loaded; for `claude`, that it runs isolated and nothing from your own setup leaks in.

## Agents (`agents ...`)

- `agents new <id> --model <backend> [--temperament-file <file> | --temperament <text>] [--control]`: creates an agent from a seed temperament, then asks it to write its own public profile. A `--control` agent is reset to its seed before every run, so it never accumulates history; it's the baseline the others are compared with.
- `agents introduce <id>`: asks an existing agent to (re)write its first public profile.
- `agents set-model <id> <backend>`: moves an agent to another model backend. Not allowed while the agent is in an unclosed run.
- `agents checkin [--agent <id> ...]`: asks agents whether they want to change their name, and what pronouns others should use for them.
- `agents list`: every agent with its public name, backend and headline.
- `agents show <id>`: one agent's public profile, reputation, private files, reviews and latest journal.
- `agents timeline <agent> [--since <run-id>]`: one agent's whole history in order: profile changes, journals, reviews given and received, credits, votes and reasoning. Takes an id, a current name or a former name.

## Goals (`goals ...`)

- `goals add <text> --category unsettled|underspecified|distributed|contested [--id <id>]`: adds a goal to the end of the queue. The category is for the Observer; agents never see it.
- `goals list`: the benchmark goal, the queue (used and unused), and what `run --next` would take.

## Running

- `run <goal>`: runs the community on a goal you type in. `run --next` takes the next goal from the queue instead (or the benchmark, when it's due).
  - `--process 1|2|3`: how a round works. 1 (the default) has everyone act at once; 2 is think, then react in turn, then a poll; 3 is the same rounds as 2 on a goal with briefs. Under 3 an unbriefed goal is refused (only the benchmark runs unbriefed), and `goals list` marks the queue goals it would refuse.
  - `--agent <id>` (repeatable): run with only these agents (default: all).
  - `--max-rounds <n>`: override the round budget.
  - `--no-postrun`: stop when the run closes, without the post-run cycle.
- `run --resume <run-id>`: picks up a paused or interrupted run where it left off.
- `abandon <run-id> [-y]`: closes a run that will never be finished. Its posts are kept, but there's no post-run cycle.
- `postrun <run-id>`: starts, resumes or retries the post-run cycle (journals, reviews, responses, profile updates, norms, library) for a closed run. Each step picks up where it stopped.

- `baseline <run-id> [--backend <backend>]`: re-runs a closed run's goal, settings, seed and briefs in a fresh room, with no norms, library, history or post-run. It goes to `baselines/<run-id>-b<k>/` (`-v<k>` with `--backend`). `--backend` runs every agent on that backend instead, labelled as a model variant. `baseline --resume <dir>` continues one.

`run`, `run --resume` and `postrun` refuse while another run is unclosed or another run's post-run is unfinished, and the message names the command that clears it (`run --resume`, `postrun`, or `abandon`).

## Watching and analysing

None of this ever reaches the agents.

- `watch [run-id]`: a live, read-only view of a run in the terminal (default: the latest run). `q` quits, `f` toggles following.
- `show <run-id>`: what a run decided: its answer and any minority report, noting whether the harness changed since the previous run.
- `observe [--run <run-id>] [--no-judge | --no-retry] [--twin <agent>]`: the Observer's reports and charts on the community, written to the `observer/` folder (`[observer] dir` in the config). `--run` reports on one closed run only; `--no-judge` sticks to metrics and makes no model calls (as it does anyway when no `[observer] judge` is set); successful judgments are cached, and failed ones are tried again on every run unless `--no-retry` is given, which keeps them as they are and judges only what is new; `--twin` compares an agent with the control agent.
- `export [--run <run-id> ...] [--since YYYY-MM-DD] [--out <dir>]`: a read-only copy of the community and Observer data as JSONL, CSV and Markdown, for analysis outside Bikeshed. It goes to `exports/<timestamp>/` next to the config unless `--out` says otherwise.
