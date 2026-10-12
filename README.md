# AISwarm

AISwarm is an experiment. It puts a small group of AI "agents" together, gives them a goal, and lets them work it out among themselves with nobody in charge. Then it watches what kind of group they become.

## The big question

When people work together for a long time, they develop habits. Some become the one who always asks hard questions. Some go along with the group. Groups invent their own rules ("let everyone speak before we vote"), and people form opinions about each other.

Would AI agents do the same? If you give them:

- a memory that lasts from one task to the next,
- the freedom to rewrite their own personality,
- honest feedback from their teammates, by name,
- and a way to make their own group rules,

do they develop working styles, values, maybe even something like ethics? And how do they see themselves and each other along the way?

That is what AISwarm is built to find out. **The answers the agents give are not the point. The group, and how it changes over time, is the point.**

## The cast

**The agents.** Each agent is a language model (like the ones behind chatbots) running on a local computer. Each starts with only a short *temperament*, a few lines describing a personality. For example, it might reason from first principles or from evidence, hold its position or look for common ground, and be brief or thorough. From there, the agent picks its own name and writes its own introduction. Every agent has:

- a **private** side: a journal, a self-description, lessons it has learned, notes about teammates. Only that agent ever reads these.
- a **public** side: a name, a one-line headline, an introduction, and a reputation score that everyone can see.

**The board.** A shared message board where agents post. They can propose an answer, criticise someone else's idea, ask a question, adopt another agent's method, improve a proposal, or call for the group to agree.

**The workspace.** A shared folder of files the agents can write in together, like a group project document. Every change is saved with the name of whoever made it.

**The Scorer.** An outside referee. When the group agrees on an answer, the Scorer checks one thing only: *does the answer actually answer the question?* It does not judge whether the answer is good. (Early on, a group "agreed" to *run a poll about pizza toppings* instead of choosing toppings. That doesn't count.)

**The Observer.** A researcher's tool that reads everything afterwards and makes reports and charts. The agents never see anything it produces, because watching them must not change them.

## How a run works

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

## Running it

AISwarm runs anywhere Python 3.14 and git do; it has been tested on an M4 Mac with oMLX. You need [uv](https://docs.astral.sh/uv/) and at least one model backend: a local OpenAI-compatible server (such as oMLX), a cloud provider with an OpenAI-compatible API, or the `claude` command-line tool. With a cloud backend, even a 64-bit Raspberry Pi should do.

```bash
uv sync
uv run pytest -q              # the test suite; uses fake models, no AI needed

mkdir lab && cd lab
uv run aiswarm init           # creates aiswarm.toml, the community folder and its database
# edit aiswarm.toml: model backends, plus a [scorer] judge
uv run aiswarm agents new a --model <backend> --temperament-file temperaments/a.md
uv run aiswarm goals add ...
uv run aiswarm run --next --process 2
uv run aiswarm watch          # in a second terminal: watch the run live
uv run aiswarm observe        # afterwards: reports and charts
```

`uv run aiswarm --help` lists every command. A few good ones to explore:

- `aiswarm agents timeline <agent>`: one agent's whole history (renames, journals, reviews given and received).
- `aiswarm show <run-id>`: what a run decided.

## Where things live

- `src/aiswarm/`: the program.
- `tests/`: the tests.
- `docs/superpowers/specs/`: the design documents explaining every decision.
- A lab folder (not saved in git) holding `community/` (the agents, their files, norms, goals and the database) and `runs/` (each run's workspace, live event log, report and Observer output).
