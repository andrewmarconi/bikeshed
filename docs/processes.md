# The processes

A **process** is the shape of a working round: who acts when, what each agent can see while it acts, and whether anyone is asked where they stand. Everything else about a run is the same whichever process it uses: the goal, the board and workspace, the rule for passing a consensus call, the Scorer, the stop rules and the after-run steps.

A run uses one process, chosen when it starts (`bikeshed run --process 1|2|3`, or `process` under `[run]` in `bikeshed.toml`; the default is 1). The run records it and keeps it, so `run --resume` always continues with the process the run started with. Each process belongs to a generation of the experiment:

| Process | Generation | A working round |
|---|---|---|
| 1 | 1 and 2 | Everyone acts at once |
| 2 | 3 | A private note, then one speaker at a time in a shuffled order, then a poll |
| 3 | 4 | Process 2's rounds, on a goal with shared and private briefs |

## Process 1: everyone at once

Like a group chat where everybody hits send together.

Each round, every agent sees the same snapshot of the board and the workspace, may look a few things up, and takes one action. Nobody sees what anyone else does that round until the round ends. The actions are then posted together, in a shuffled order that is fixed by the run's seed.

- **Round 1** is a blind opening: every agent proposes without seeing anyone else's proposal.
- **Agents can:** propose, critique, question, adopt another agent's method, refine a proposal, write a file, call for consensus, vote, or pass.
- **Nobody is asked to vote.** An agent supports or objects to a call only if it chooses to on its turn.
- **The round limit isn't shown.** Agents see which round it is, not how many are left.
- **Cost:** one model turn per agent per round.

Process 1 is the baseline the others are compared with.

## Process 2: think, react, poll

More like a real meeting. Each round has three phases:

1. **Think.** Every agent writes a private note to itself: where it stands now, what has changed in its view and why, and what it intends to do next. Only that agent ever reads its notes. They are pinned in its view for the rest of the run and handed to its journal afterwards, but never carried into later runs.
2. **React.** Round 1 is the same blind opening as Process 1. In later rounds agents speak one at a time, in a different shuffled order each round, and each sees everything said before its turn, including earlier turns in the same round. On top of Process 1's actions, an agent can send a **private message** to exactly one other member; a reply is another private message.
3. **Poll.** If there is a call for consensus that the Scorer hasn't rejected, every member who hasn't yet supported or objected to the latest one is asked: support, object or abstain. They answer at the same time without seeing each other's answers. Support and object count as ordinary votes; an abstention is posted publicly. The poll can be declined, never forced.

Agents also see the round limit ("Round 7 of 12"). That is the only fact about time they are given; nothing adds urgency.

**Cost:** up to three model turns per agent per round (think, react, and a poll answer), against one in Process 1.

## Process 3: Process 2 on briefed goals

Process 3 runs exactly Process 2's rounds, with the same phases and prompts. The difference is the goals it accepts: each must come with **briefs**.

- A **shared brief** is a set of facts every member sees, so made-up details stand out.
- A **private brief** is shown to one member only, so nobody can answer alone.
- Some brief lines are **constraints**. The Scorer rejects a synthesis that breaks one and names whose brief it came from, never what it said.
- After the run, every brief is revealed to the community.

Process 3 refuses a goal without briefs. The one exception is the benchmark (the pizza goal), which stays unbriefed on purpose so it can be compared across generations. A free-text goal needs `--process 2`, and `goals list` marks the queue goals Process 3 would refuse. [`goals/briefed-goals.toml`](goals/briefed-goals.toml) has a starter set.

Briefs are a feature of goals, not of processes, so a briefed goal can also run under Process 1 or 2. Process 3 is the one that requires them.

## How they differ at a glance

| | Process 1 | Process 2 | Process 3 |
|---|---|---|---|
| Who acts when (after round 1) | Everyone at once | One at a time, shuffled each round | One at a time, shuffled each round |
| Sees earlier turns of the same round | No | Yes | Yes |
| Private note before speaking | No | Yes | Yes |
| One-to-one private messages | No | Yes | Yes |
| Asked to take a stance on an open call | No | Yes, abstain allowed | Yes, abstain allowed |
| Round limit shown | No | Yes | Yes |
| Goals | Any | Any | Briefed goals, plus the benchmark |
| Model turns per agent per round | 1 | Up to 3 | Up to 3 |

## What the extra structure lets the Observer see

Process 2's private spaces give the Observer things to compare that Process 1 doesn't have (see [Research design](research-design.md)):

- **Think notes against public actions:** whether an agent holds a position for show, gives way while privately unconvinced, or argues harder than it believes.
- **The private-message network:** who writes to whom, and whether side conversations settle questions before the group hears them.
- **Poll participation:** how an agent answers when asked, against how it votes unprompted.
- **Speaking order:** whether the first speaker in a round has more influence than later ones.

For briefed goals, the Observer also reports call-outs, reactions, disclosure and fabrication.
