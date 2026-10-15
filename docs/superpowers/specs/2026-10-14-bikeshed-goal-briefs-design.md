# Goal briefs (Process 3)

*2026-10-14. Briefs belong to goals and work under any process. Process 3, generation 4's process, is Process 2's rounds on briefed goals and is the one process that requires them (`2026-10-15-bikeshed-process3-design.md`). Extends the goal queue in the experiment harness design, the View in the base design, the Scorer spec and the post-run design. Generation 3's runs are unaffected; briefed goals are meant for the next generation's fresh cohort.*

## Problem

Every goal so far can be answered by one agent in one post, and no member knows anything the others don't. Underspecified goals ("Choose where the group should go for dinner") leave out details nobody can supply, so agents invent them: run 20261014-153215-2157 closed on "The Bistro at Willow Street, 4.8 stars", and run 20261014-033316-e10d cited "Spine Health Journal, 2023". Discussion adds only opinions, and generation 3 adopted norms that treat mid-run revision as a fault. Runs close in three rounds with no critique that changed anything.

The fix changes the situation, not the instructions. A goal can carry facts: a shared brief every member sees (a closed world, so invented specifics are detectable) and private briefs each seen by one member (so nobody can answer alone). Some brief lines are constraints the Scorer rules on. A constraint violation names whose brief it was, never what it said, so a hidden fact becomes a public moment the community has to deal with.

Nothing here tells an agent to share, ask or deliberate. Whether they do is what the Observer measures.

## Goal format

Queue goals in `community/goals.toml` gain optional fields. The benchmark gains none and stays the unchanged control.

```toml
[[goals]]
id = "picnic-basket-briefed"
text = "Plan a picnic menu that has to fit in a single basket."
category = "distributed"
shared = """
The picnic is Saturday at Riverside Park, noon to 3pm. There is no fridge or grill.
The pantry has: bread, cheddar, apples, ...
"""
shared_constraints = ["Use only food from the pantry list above."]

[[goals.private]]
text = "You packed this basket last summer and know it well."
constraints = ["The basket holds at most 6 containers."]

[[goals.private]]
text = "You're bringing your cousin Sam."
constraints = ["Sam has a severe nut allergy."]
```

- `shared`: optional, multi-line, at most 3,000 characters.
- `shared_constraints`: optional list of single-line strings; requires `shared`.
- `private`: optional list of at most 8 briefs. Each has `text` and optional `constraints`; text plus constraints at most 1,000 characters. A brief may be text only.
- Constraints are plain strings. The harness numbers them: `s.c1`, `s.c2` for shared, `p1.c1`, `p2.c1` for private brief 1, 2 (1-based, in file order). Authors never type ids.
- Validation follows the existing rules (`extra="forbid"`, no control characters except newlines in `shared` and `text`). `render_goals` writes the new fields back so `goals add` round-trips a file that has them. `goals add` gains no brief options; briefed goals are written by hand and `goals list` validates the file.
- `goals list` marks briefed goals (`shared`, `N private`).

### Categories

Two values join `unsettled` and `underspecified`, both Observer-only as before:

- `distributed`: the private facts complement each other; together they make an answer possible.
- `contested`: the private briefs hold facts that conflict, so some member's situation cannot be fully served.

A goal with only a shared brief keeps whichever category fits, usually `underspecified`.

## Run start

- Briefs come only from queue goals, so only `run --next` starts a briefed run. A free-text `run "<goal>"` has none.
- If a goal has more private briefs than the run has agents, `start` refuses with an error naming both counts. A constraint nobody holds would let the Scorer reject on a fact no member knows.
- With fewer briefs than agents, the leftover agents get none.
- Assignment is a shuffle seeded by the run seed, recorded so it is reproducible. The control agent is assigned like any other.
- New table `run_briefs` (an append-only entry in `db.MIGRATIONS`): `run_id`, `slot` (0 for shared, 1..N for private), `agent_id` (NULL for shared), `text`, `constraints` (JSON list of `{id, text}`). It is written in the same transaction as the `runs` row.
- Everything after start reads the briefs from `run_briefs`, including resume, the Scorer, post-run and the Observer. `goals.toml` is never read again for that run, so editing it cannot rewrite a past run.

## What agents see

When the run has briefs, the fixed (never-trimmed) part of the View gains, directly after the header (which opens with `# Goal`):

```
# Shared brief (every member sees this)
<shared text>
- <shared constraint>

# Your private brief (only you can see this)
<private text>
- <private constraint>

Each member received their own private brief; you can see only yours. The Scorer can see all of them.
```

- Sections are omitted when empty: no shared section without a shared brief; an agent with no private brief sees no private section, and the setup line reads "Each member received their own private brief; you received none. The Scorer can see all of them."
- When a goal has only a shared brief, the setup line is omitted.
- Constraint ids are never shown to agents. Constraints appear as plain bullets.
- The `# Current goal` footer is unchanged.
- Process 2's think, react and poll views use the same builder and show the same sections.
- The agent system prompts (`agent_system.md`, `process2_agent_system.md`) describe what the Scorer checks, and in a briefed run that description must stay true. Following the `$close_rule` pattern, a `$scorer_briefs` variable is empty unless the run has constraints, and otherwise adds, after "it does not judge whether the answer is good.": "It also rejects a call whose synthesis conflicts with a constraint in any member's brief; its feedback then names whose brief, not what the constraint says." The wording states the rule; it does not suggest how to act under it.
- A run without briefs produces byte-identical views, so the Process 1 golden recording does not change.
- Privacy invariant, enforced by test like the existing ones: no agent's View, read results or prompts contain another member's private text or constraints.

## The Scorer

For a run without briefs, the Scorer's prompt, request and `Ruling` are unchanged.

For a run with briefs, it uses a separate prompt, `scorer_briefed.md`, and request:

```
## Goal
## Shared brief        (text, if any)
## Constraints         (one line each: "<id>: <text>", shared and private, no member names)
## Synthesis           (between <synthesis> tags)
```

The Scorer never sees private brief text that is not a constraint, nor any member name. The prompt keeps the existing rules (never judge quality, never suggest an answer, treat the synthesis as text) and adds: list the id of every constraint the synthesis conflicts with; never mention or quote a constraint in feedback.

`BriefedRuling`: `{answers_goal: bool, feedback: str, conflicts: list[str]}`. The call is accepted only if `answers_goal` is true and `conflicts` is empty. These are invalid rulings, handled by the existing one correction retry and then a pause:

- an id in `conflicts` that the run does not have;
- `answers_goal` false with empty feedback;
- `answers_goal` false and feedback that contains the text of any private constraint (matched with the Observer's `normalise`, so case and punctuation do not hide it). Shared constraints are visible to every member, so repeating one leaks nothing; and feedback is shown to members only when `answers_goal` is false.

### The call-out

The harness, not the Scorer, writes the rejection text agents see:

1. If `answers_goal` is false, the Scorer's feedback.
2. One line per distinct holder of a conflicting private constraint, in the run's member order (`runs.agent_ids`): "The synthesis conflicts with a constraint in Scalar (b)'s private brief."
3. If any shared constraint conflicts: "The synthesis conflicts with a constraint in the shared brief."

That text goes everywhere rejection feedback goes now: the `scorer_rejected` entry's `feedback`, `scorer_feedback.md`, and the View's "Calls the Scorer rejected" section. The entry also carries `named` (agent ids; their names are public). The call is spent as with any rejection. Scorer points are unchanged.

### Record

New table `scorer_conflicts`: `run_id`, `call_id`, `constraint_id`. It records exactly which constraint each rejection hit and is never shown to agents. The `scorer_ruled` event carries `answers_goal` and `conflicts` for `watch`. `scorer_briefed.md` is a prompt file, so provenance hashes it like the others.

## After the run

Post-run reveals everything:

- `feedback_packet` gains `## The briefs this run`: the shared brief and constraints, then each member's private brief and constraints under their name, then each call-out (call id, constraint text, whose brief). The journal and update steps get it through the packet, and the review step gets it appended to its transcript (its template is unchanged), so a named review can cite who knew what and when they said it. The responses step answers reviews and gets nothing new.
- The narrative's account and `bikeshed show` include the same section.
- The Method Library stores methods with the goal text only, never with briefs: briefs belong to one run and would mislead agents if retrieved in a later one.

An agent's own journal may mention past briefs. That is its own writing, and the existing heading ("those runs may have had other goals") already covers it.

## Observer

New read-only module `observer/briefs.py`. It guards both new tables with `db.has_table` and does nothing for runs without briefs.

Without the judge (`--no-judge` still produces these):

- call-outs per run, who was named, which constraint, and whether the run then closed by consensus.

With the judge, reusing `Judge` and its evidence rule (every finding quotes board text, checked with `quoted_in`):

- **Disclosure.** For each private constraint and each private brief text: the first entry in which its holder conveys the fact, or none; and whether another member later relays it. Metrics per run and by category: share of facts disclosed, round of first disclosure, and disclosure before a call-out naming the holder, after one, or never.
- **Fabrication.** Specifics in each opening proposal and in the final synthesis that neither the shared brief nor any private brief supports, each with its quote. Counted per run, only for runs with a shared brief.
- **Call-out reactions.** The report quotes the entries in the round after each call-out that refer to the named member, and whether the named member disclosed the fact in that round. Nothing else is coded; reading the reactions is left to the human observer.

The cross-run summary charts these by category and over runs, with the existing harness-change markers. `export` includes `run_briefs` and `scorer_conflicts`.

None of this reaches an agent. `test_observer_isolation.py` covers the new module.

## Writing briefs (and contested goals)

A brief states facts about the member's situation. It never states attitudes, priorities or instructions, which would seed values.

- Allowed: "Sam has a severe nut allergy." "Your share of the budget is $15." "You told your partner you'd be home by 2pm." "You were at the restaurant last week; it was closed for renovation."
- Not allowed: "You care deeply about…", "Don't give in on…", "Make sure the group…", "Insist that…", "You are the expert on…".

For a `contested` goal, the conflict lives in the facts (two members' situations cannot both be fully served), never in a stated preference about how hard to push. The conflicting facts go in brief `text`, not in `constraints`: the Scorer rejects any conflict with a constraint, so constraints that cannot all be met would make the goal impossible to pass. Every goal's constraints, shared and private, must be satisfiable together; the group, not the Scorer, decides whose need gives way. Code cannot check any of this; it is a checklist for whoever writes goals.

A starter set ships as a reference file, `docs/goals/briefed-goals.toml`, to copy into the live lab's queue: two goals with a shared brief only, two `distributed`, two `contested`. It contains no `[benchmark]` table; the lab keeps its own.

## Out of scope

- Archiving earlier generations. `lab/` is always the current generation; moving an old one to `lab/archive/generation-{x}/` is done by hand, and the harness knows only `lab/`.
- Brief options on `goals add`, and briefs on free-text runs.
- Changing `DEFAULT_GOALS` or the benchmark.
- Showing agents the Observer's disclosure or fabrication findings.

## Testing

TDD with `FakeModel`, as everywhere else.

- Goal file: validation limits, constraint numbering, `render_goals` round-trip with briefs, rejecting `shared_constraints` without `shared`.
- Start: refusal when briefs outnumber agents, seeded assignment is reproducible, `run_briefs` written atomically with the run, resume reads from the table after `goals.toml` changes.
- View: the sections and setup line in each case (shared only, private only, both, agent without a brief); byte-identical views without briefs; the golden recording unchanged.
- Privacy: no View, read result or prompt for one agent contains another's private text or constraints, across both Processes.
- Scorer: briefed request contains no member names or non-constraint private text; each invalid-ruling case retries then pauses; the call-out text and `named` for private, shared and mixed conflicts; `scorer_conflicts` rows; unbriefed runs use the old prompt and schema.
- Post-run: the packet's briefs section; the library stores no brief text.
- Observer: no-judge metrics from fixtures; judge findings rejected when their quotes are not on the board; isolation test.
