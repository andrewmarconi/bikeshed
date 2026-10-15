# What you can vary

Each of these is a dial you can turn to see what changes. Every run records the settings, models, prompts and code it used. The Observer marks a change to any of them on its charts, so a shift caused by turning a dial isn't mistaken for the agents changing on their own.

- **Who's in the community.** How many agents there are, and the temperament each one starts from. The lab creates one agent per file in `lab/temperaments/`. Any agent can be made the control (`--control` on `bikeshed agents new`).
- **The models.** Each agent has its own model, thinking can be on or off per backend, and one run can mix them (see [Getting started](getting-started.md)). The Scorer has its own model too (`[scorer] judge`), and a stricter or looser judge changes what counts as an answer.
- **The goals.** The queue lives in the community's `goals.toml`. Each goal is tagged for the Observer: *unsettled* (no data could settle it, like the best fruit), *underspecified* (the question leaves out details the group must supply), *distributed* (members' facts complement each other) or *contested* (members' facts pull against each other); see briefs below. The benchmark goal and how often it returns (`every`) are set there too.
- **Briefs (Process 3).** A goal can come with facts. A *shared brief* is shown to every member, so made-up details stand out. *Private briefs* are each shown to one member, so nobody can answer alone. Some brief lines are *constraints*: the Scorer rejects a synthesis that breaks one, and says whose brief it was, never what it said. After the run every brief is revealed to the community. [`goals/briefed-goals.toml`](goals/briefed-goals.toml) has a starter set. Briefs state facts only, never how anyone should behave.
- **The process.** Process 1 (everyone at once), Process 2 (think, then speak in turn, with private messages and polls) or Process 3 (Process 2's rounds on briefed goals; only the benchmark runs unbriefed), per run with `--process`. Briefs work under any process; Process 3 requires them. See [The processes](processes.md).
- **The rules of a run**, in the `[run]` section of `bikeshed.toml`:
  - `quorum_slack`: how many holdouts a consensus call can survive (default 1).
  - `max_rounds` and `stagnation_rounds`: how long the group gets, and how many silent rounds end a run.
  - `view_entries` and `max_reads`: how much of the board an agent sees each turn, and how many extra things it can look up before acting.
  - `reputation_half_life_runs`: how quickly old ratings fade from an agent's reputation.
- **Memory.** Whether methods from earlier runs are offered back to the agents, and how many (`[library] embeddings` and `top_k`).
- **History.** One community carried through many runs, against a fresh community in a new lab folder given the same goals.
- **The instructions.** Every prompt is a plain file in `src/bikeshed/prompts/` and can be edited. Each run records which version it used. Edits have to stay values-neutral, though: if a prompt tells agents how to behave, any values that show up came from the prompt and not the agents.
