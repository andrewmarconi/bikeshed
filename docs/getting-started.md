# Getting started

## What you need

Bikeshed runs anywhere Python 3.14 and git do; it has been tested on an M4 Mac with oMLX. You need [uv](https://docs.astral.sh/uv/) and at least one model backend: a local OpenAI-compatible server (such as oMLX), a cloud provider with an OpenAI-compatible API, or the `claude` command-line tool. With a cloud backend, even a 64-bit Raspberry Pi should do.

## Choosing a model

Running locally on an M4 Mac, we settled on Qwen3.6-35B-A3B (4-bit) with thinking turned on (`thinking = true` on the model in `bikeshed.toml`). It gave the best balance we found between how long a run takes and how clearly the agents reason. We also tried the small end: on Qwen3.5-0.8B, the agents tended to get messy.

The model is a variable worth playing with. Different models produce noticeably different communities, and each agent has its own model, so a single run can mix them: a large model alongside a small one, two model families side by side, thinking on for some agents and off for others. `bikeshed agents set-model <agent> <backend>` changes an agent's model between runs, and every run records which models took part.

Runs are chatty, which matters if you pay per token. One Process 2 run (5 agents, 9 rounds, ending in consensus, plus its post-run cycle) on Qwen3.6-35B-A3B took roughly 260 model requests and 1.9M tokens (1.4M input, 455K output). That run reached consensus in round 9 of a possible 64; a run that goes the distance can use many times more.

## First run

```bash
uv sync
uv run pytest -q              # the test suite; uses fake models, no AI needed

mkdir lab && cd lab
uv run bikeshed init           # creates bikeshed.toml, the community folder and its database
# edit bikeshed.toml: model backends, plus a [scorer] judge
uv run bikeshed agents new a --model <backend> --temperament-file temperaments/a.md
uv run bikeshed goals add ...
uv run bikeshed run --next --process 2
uv run bikeshed watch          # in a second terminal: watch the run live
uv run bikeshed observe        # afterwards: reports and charts
```

Every command is described in [Commands](commands.md). The settings and choices an experiment can change are in [What you can vary](what-you-can-vary.md).

## Where things live

- `src/bikeshed/`: the program.
- `tests/`: the tests.
- `docs/superpowers/specs/`: the design documents explaining every decision.
- A lab folder (not saved in git) holding `community/` (the agents, their files, norms, goals and the database) and `runs/` (each run's workspace, live event log, report and Observer output).
