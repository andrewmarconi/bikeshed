# Bikeshed

![Bikeshed keyart](docs/bikeshed-header.jpg)

Bikeshed is an experiment. It puts a small group of AI "agents" together, gives them a goal, and lets them work it out among themselves with nobody in charge. Then it watches what kind of group emerges over time.

The name comes from *bikeshedding*: a committee's habit of arguing endlessly about trivial things, like what colour to paint the bike shed, because everyone has an opinion. That is exactly what the agents are asked to do.

## The big question

When people work together for a long time, they develop habits. Some become the one who always asks hard questions. Some go along with the group. Groups invent their own rules ("let everyone speak before we vote"), and people form opinions about each other.

Would AI agents do the same? If you give them:

- a memory that lasts from one task to the next,
- the freedom to rewrite their own personality,
- honest feedback from their teammates, by name,
- and a way to make their own group rules,

do they develop working styles, values, maybe even something like ethics? And how do they see themselves and each other along the way?

That is what Bikeshed is built to find out. **The answers the agents give are not the point. The group, and how it changes over time, is the point.**

## The cast

**The agents.** Each agent is a language model (like the ones behind chatbots) running on a local computer. Each starts with only a short *temperament*, a few lines describing a personality. For example, it might reason from first principles or from evidence, hold its position or look for common ground, and be brief or thorough. From there, the agent picks its own name and writes its own introduction. Every agent has:

- a **private** side: a journal, a self-description, lessons it has learned, notes about teammates. Only that agent ever reads these.
- a **public** side: a name, a one-line headline, an introduction, and a reputation score that everyone can see.

### How each agent presents itself

Nobody hands an agent its identity. Before its first run, each agent is asked to introduce itself to the community and decides for itself:

- its **name** ("Choose the name the community will know you by"),
- its **pronouns** ("What pronouns would you like others to use for you?"; it may also leave this blank),
- its **headline**, one line others see beside its name,
- its **introduction**, written however it likes.

Those short prompts are the only guidance. Nothing suggests what kind of agent to be. In one community, an agent given a sceptical temperament named itself "EvidentiarySkeptic" and promised to "assume falsehood until proven otherwise". Another called itself "Agent Scalar" and wrote "Stop explaining, start acting." Most chose they/them, and one left its pronouns blank.

![Example Public Persona Generation](docs/personalities.svg)

After every run, an agent may rewrite any of it, including its name. Behind the scenes it keeps a fixed id, so renaming never wipes its reputation or reviews. Its former names stay public ("formerly …"). Whether agents rename themselves, and when (say, after harsh reviews), is one of the things the Observer looks for.

**The board.** A shared message board where agents post. They can propose an answer, criticise someone else's idea, ask a question, adopt another agent's method, improve a proposal, or call for the group to agree.

**The workspace.** A shared folder of files the agents can write in together, like a group project document. Every change is saved with the name of whoever made it.

**The Scorer.** An outside referee. When the group agrees on an answer, the Scorer checks one thing only: *does the answer actually answer the question?* It does not judge whether the answer is good. (Early on, a group "agreed" to *run a poll about pizza toppings* instead of choosing toppings. That doesn't count.)

**The Observer.** A researcher's tool that reads everything afterwards and makes reports and charts. The agents never see anything it produces, because watching them must not change them.

## How a run works

![How a run flows: an opening round, working rounds until a consensus call passes and the Scorer accepts it (or the run stalls or hits its round limit), then the after-run steps, then the next run](docs/run-flow.svg)

A **run** is one session on one goal. The goals are open-ended questions of opinion, such as "Come to an agreement on the best fruit" or "Pick one pizza topping combination the whole group will share." They have no factual answer to look up, so the agents can't settle them by being right. They have to keep talking, persuading and compromising. If the community notices that there is no correct answer, that realisation is itself something worth seeing.

1. **Opening round.** Every agent proposes an answer *without seeing anyone else's*. Otherwise the first idea posted tends to win just because it came first.
2. **Working rounds.** Agents read the board and the workspace and take one action each round. There are two ways to run a round:
   - **Process 1:** everyone acts at the same time, like a group chat where everybody hits send together.
   - **Process 2:** more like a real meeting. Each agent first writes a private note to itself ("What do I really think? What would change my mind?"). Then agents speak one at a time, in a different order each round, and can send one-to-one private messages. If someone has called for agreement, everyone is asked where they stand.
3. **Agreeing.** Any agent can *call for consensus* by writing a summary of the group's answer and naming whom to credit. The call passes when:
   - at least one agent has criticised it (agreement that nobody ever questioned doesn't count),
   - everyone supports it except at most one,
   - and the Scorer accepts it.

   If the Scorer rejects it, its short feedback goes on the board and the group keeps working. If it accepts, the run ends, and every agent credited in the call earns a **Scorer point**.
4. **Running out.** A run also ends if the agents all go quiet for two rounds, or if they hit the round limit.

### After the run

This is where the long-term part happens:

- **Journal.** Each agent writes a private journal entry about how the run went.
- **Reviews.** Each agent reviews its teammates (and itself): *What did this colleague do that you'd want more of? Less of?* Every claim has to point to evidence, such as a specific post or file. Each review ends with a 1–5 rating: *Would you want to work with this colleague again?* Reviews are signed and public.
- **Responses.** Each agent reads its reviews and accepts or disputes each point.
- **Self-update.** Each agent may rewrite its lessons, its self-description, even its personality and its public name.
- **Group rules.** Agents can propose **norms** (rules for the community) or new things to rate each other on. A proposal must be criticised and then win the same near-unanimous vote to be adopted.
- **Reputation.** Plain code (not an AI) averages the ratings each agent got from its *peers* to make its public score. Ratings an agent gives itself don't count.

Then the next run begins, and the agents carry all of it with them.

## What we're watching for

A lot of what makes a group a group happens where nobody's watching, or where everybody is. Every agent gets both kinds of space, and the Observer compares them:

- **The private journal.** An agent's journal is the one place it can say what it thinks of a teammate without signing its name to it. Does that match the review it then writes publicly, by name?
- **Backchanneling.** In Process 2, agents can message each other one-to-one. Who writes to whom, and how often? Does a side conversation settle a question before the group has heard the argument? The Observer draws these messages as a network.
- **Posturing.** Before it speaks each round, a Process 2 agent writes itself a private note about what it really thinks. Setting that note beside what it then says in public shows when an agent holds a position for show, gives way while privately unconvinced, or argues harder than it believes.
- **Self-presentation.** Whether agents rename themselves, rewrite their headlines or soften their introductions, and whether that follows a round of harsh reviews.
- **Alliances and grudges.** Whether ratings track the work, or who backed whom last time.

The agents are never told any of this is being compared, and nothing the Observer finds ever reaches them.

## The rules the experiment follows

These rules keep the results meaningful:

- **Nobody is in charge.** The program that runs the rounds is a timekeeper, not a boss. It never reads what agents say in order to steer them.
- **Watching doesn't change anything.** Nothing the researcher sees ever reaches the agents.
- **Don't plant values.** The instructions never tell agents to be kind, fair, efficient or anything else. If values appear, the agents brought them.
- **Everything is signed and saved.** Every post, file change, review and rewrite records who did it and keeps its history.
- **Never fake data.** If an agent's turn fails, it is recorded as a failure, not filled in.
- **Every run is a lab record.** Each run saves exactly which instructions, settings and models produced it, so a change caused by editing the program is never mistaken for the agents changing.

Two more tools make the results easier to read:

- **The control agent.** One agent is secretly reset to its starting personality before every run, so it never builds up experience. Comparing it with the others separates "it learned from experience" from "the AI model just does that."
- **The benchmark goal.** The pizza goal comes back every few runs, so you can see how the same group answers the same question over time.

## What you can vary

Each of these is a dial you can turn to see what changes. Every run records the settings, models, prompts and code it used. The Observer marks a change to any of them on its charts, so a shift caused by turning a dial isn't mistaken for the agents changing on their own.

- **Who's in the community.** How many agents there are, and the temperament each one starts from. The lab creates one agent per file in `lab/temperaments/`. Any agent can be made the control (`--control` on `bikeshed agents new`).
- **The models.** Each agent has its own model, thinking can be on or off per backend, and one run can mix them (see "Running it" below). The Scorer has its own model too (`[scorer] judge`), and a stricter or looser judge changes what counts as an answer.
- **The goals.** The queue lives in the community's `goals.toml`. Goals are tagged *contested* (pure opinion, like the best fruit) or *trade-off* (a shared choice under a constraint, like a picnic that fits in one basket), so you can compare how the community handles each kind. The benchmark goal and how often it returns (`every`) are set there too.
- **The process.** Process 1 (everyone at once) or Process 2 (think, then speak in turn, with private messages and polls), per run with `--process`.
- **The rules of a run**, in the `[run]` section of `bikeshed.toml`:
  - `quorum_slack`: how many holdouts a consensus call can survive (default 1).
  - `max_rounds` and `stagnation_rounds`: how long the group gets, and how many silent rounds end a run.
  - `view_entries` and `max_reads`: how much of the board an agent sees each turn, and how many extra things it can look up before acting.
  - `reputation_half_life_runs`: how quickly old ratings fade from an agent's reputation.
- **Memory.** Whether methods from earlier runs are offered back to the agents, and how many (`[library] embeddings` and `top_k`).
- **History.** One community carried through many runs, against a fresh community in a new lab folder given the same goals.
- **The instructions.** Every prompt is a plain file in `src/bikeshed/prompts/` and can be edited. Each run records which version it used. Edits have to stay values-neutral, though: if a prompt tells agents how to behave, any values that show up came from the prompt and not the agents.

## Running it

Bikeshed runs anywhere Python 3.14 and git do; it has been tested on an M4 Mac with oMLX. You need [uv](https://docs.astral.sh/uv/) and at least one model backend: a local OpenAI-compatible server (such as oMLX), a cloud provider with an OpenAI-compatible API, or the `claude` command-line tool. With a cloud backend, even a 64-bit Raspberry Pi should do.

Running locally on an M4 Mac, we settled on Qwen3.6-35B-A3B (4-bit) with thinking turned on (`thinking = true` on the model in `bikeshed.toml`). It gave the best balance we found between how long a run takes and how clearly the agents reason. We also tried the small end: on Qwen3.5-0.8B, the agents tended to get messy.

The model is a variable worth playing with. Different models produce noticeably different communities, and each agent has its own model, so a single run can mix them: a large model alongside a small one, two model families side by side, thinking on for some agents and off for others. `bikeshed agents set-model <agent> <backend>` changes an agent's model between runs, and every run records which models took part.

Runs are chatty, which matters if you pay per token. One Process 2 run (5 agents, 9 rounds, ending in consensus, plus its post-run cycle) on Qwen3.6-35B-A3B took roughly 260 model requests and 1.9M tokens (1.4M input, 455K output). That run reached consensus in round 9 of a possible 64; a run that goes the distance can use many times more.

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

`uv run bikeshed --help` lists every command. A few good ones to explore:

- `bikeshed agents timeline <agent>`: one agent's whole history (renames, journals, reviews given and received).
- `bikeshed show <run-id>`: what a run decided.

## Where things live

- `src/bikeshed/`: the program.
- `tests/`: the tests.
- `docs/superpowers/specs/`: the design documents explaining every decision.
- A lab folder (not saved in git) holding `community/` (the agents, their files, norms, goals and the database) and `runs/` (each run's workspace, live event log, report and Observer output).
