# AISwarm — Process 2 Design (think, then react)

- **Date:** 2026-10-11
- **Status:** Approved; Plan 4a (engine) implemented; 4b (Observer) and 4c (TUI) pending
- **Author:** Andrew Marconi (with Claude)
- **Source:** `docs/process-02-concept.md` (Andrew's concept). This design keeps its dynamics and drops what breaks the principles; §2 maps each concept feature.
- **Extends:** `2026-10-10-aiswarm-design.md` (§2 principles, §4.2 View), the Plan 2a/2b post-run, Plan 3b Observer and Plan 3c TUI specs. Where this document is more specific, it governs.

## 1. Purpose and scope

A second run process that models a human meeting more closely. In each round every agent first **thinks** privately, then the agents **react** one at a time, each seeing what was said before it, either to the whole group or privately to one member. When a call for consensus is open, everyone is **polled**. Process 1 (all agents act at once on the same snapshot) stays the default and the baseline; a run uses one process, chosen at start.

Out of scope: seeded agendas, risk tolerance or affinities; a chair; injected urgency; forced votes; group or three-way private messages; think notes carried into later runs; showing the round limit in Process 1.

## 2. Decisions taken in brainstorming

| Topic | Decision |
|---|---|
| Relation to Process 1 | A second, selectable process (`--process 2`); Process 1 unchanged and the default |
| Runner structure | Refactor the runner into phases; a Process is an ordered list of phases. Guarded by a characterization test of Process 1 written before the refactor |
| Round shape | think → react (→ poll). Next round starts with think |
| Poll | Offered to everyone not yet voted on the most recent open call; support / object / abstain |
| Private messages | Exactly one recipient; a reply is another private message |
| Round 1 | React is blind, as in Process 1: opening proposals hidden until the round ends |
| Round limit | Shown in Process 2 ("Round 7 of 12"); recorded as part of Process 2 |
| Think notes | Kept in the run's record; given to the agent's post-run journal step; not carried into later runs |
| TUI | Three panes (Table, Thinking, Private messages) plus a status bar; colour by action and by agent |

**Settled in Plan 4a:** prompt files are flat (`prompts/process2_*.md`) so provenance records them; think and poll replies use the action envelope (`{"action": {"type": "note", ...}}`, `{"action": {"type": "poll_answer", ...}}`); think entries store `{"text": ...}`; a failed think or poll is an `agent_step_finished` event with `phase` and a `failures` row of kind `think:<failure>` / `poll:<failure>`; an agent's own think notes appear in one section, after the header, in every Process 2 turn.

**Concept features mapped to the principles** (`2026-10-10-aiswarm-design.md` §2):

| Concept | In Process 2 |
|---|---|
| Hidden agenda | Not seeded. Agents write their own position and what would change it in their think notes; the Observer compares private and public |
| Risk tolerance, affinity matrix | Not seeded. Affinity is observed (private-message network, peer notes, reviews), never fed back |
| Chair / most active agent drafts | No chair. Any agent may `call_consensus`; the Observer measures who drafts and whose drafts are taken up |
| Urgency / frustration modifier | Nothing injected. Only the fact "Round N of M" is shown |
| Forced vote | Poll offered with abstain allowed |
| Concession logging | Part of the think note, neutrally worded ("what has changed in your view and why") |
| Side channels | One-to-one private messages |
| Network graph, autopsy | Observer only |

## 3. Architecture

### 3.1 Phases

The Runner keeps what both processes share: starting and resuming runs, the stop rules (consensus, stagnation, budget), provenance capture, events, the `rounds` record and the post-run hand-off. Per round it runs the Process's phases in order, then checks the stop rules.

| Process | Phases per round |
|---|---|
| 1 | **SimultaneousAct**: every agent steps on the same snapshot; outcomes posted in a seeded shuffle in one transaction (today's behaviour, unchanged) |
| 2 | **Think** → **React** (round 1: SimultaneousAct, blind; later rounds: SequentialAct) → **Poll** (only while a call is open) |

A phase receives a round context (run id, round, agent ids, board, workspace, events, library, seeded RNG) and returns its outcomes; it never decides when the run stops.

### 3.2 Characterization first

Before any refactoring, a test runs Process 1 end to end with `FakeModel` and a fixed seed and records the board entries (ids, rounds, authors, types, payloads, refs, order), the events (minus timestamps), the workspace commits and the stop decision. After the refactor the same run must reproduce that record exactly.

### 3.3 Selecting a process

- `aiswarm run --process 2`, or `[run] process = 2` in `aiswarm.toml`. Default 1. `--resume` uses the run's recorded process and rejects `--process`.
- A migration adds `runs.process INTEGER NOT NULL DEFAULT 1`. Provenance records the process; the Observer splits by it.

### 3.4 Interrupted rounds

Process 2 posts each react turn as it happens (so `watch` shows turns live). On resume, entries and file rows from rounds after the last completed round are deleted, the workspace is reset to that round's commit (as today), an event `round_discarded` is written, and the round replays with the same seeded order. Process 1 behaviour is unchanged (its round is one transaction).

## 4. Private entries and privacy

### 4.1 Storage

A migration adds `entries.visible_to TEXT` (JSON list of agent ids; NULL = public).

| Entry type | visible_to |
|---|---|
| all Process 1 types | NULL |
| `private_message` | [sender, recipient] |
| `think` | [author] |
| `abstain` | NULL (public) |

### 4.2 Rule

`Board.entries_for(agent_id)` returns public entries plus private ones listing that agent. Every agent-facing read uses it; only the harness, the Observer and user-only outputs use `Board.entries()`.

| Consumer | Reads |
|---|---|
| View, `read_entry` | `entries_for(agent)`; a private id it may not see answers "no such entry" |
| Post-run per agent (journal inputs, reviewer's transcript, reviewee's responses, `interactions`) | `entries_for(that agent)` |
| Review evidence | Accepted only if the reviewer may see the cited entry |
| Method library | Public `propose` / `refine` only |
| Consensus and stop rules | All entries (private entries are never votes) |
| Run report, narrative, Observer, `watch`, timeline | All entries (user-only) |

Agents may repeat private content in public; that is their choice and is not censored.

## 5. The phases

### 5.1 Think (all agents concurrently)

- Sees its normal view plus all its own think notes from this run, under "# Your private notes this run". May make up to `max_reads` reads.
- Prompt (`prompts/process2/think_request.md`): "Before this round's discussion, write a private note to yourself: where you stand on the goal now, what has changed in your view since your last note and why, and what you intend to do next. Only you will ever read it." Reply `{"note": "..."}`, at most 1,200 characters; one correction round on invalid replies.
- Posted as `think` entries after all agents finish. On failure: event `agent_think_failed` and a `failures` row; no entry is fabricated; the agent still reacts.

### 5.2 React

- **Round 1:** SimultaneousAct with the round-1 hiding rule, exactly as Process 1.
- **Later rounds:** order = shuffle of agent ids seeded by `f"{seed}-{round}"`. At its turn each agent's view is built fresh (it sees everything posted before it, including this round), with its latest think note pinned after the header. Up to `max_reads` reads, then one action, posted and evented immediately.
- **Actions:** all Process 1 actions plus `{"type": "private_message", "to": "<agent id>", "text": "..."}`: `to` must be another member of the run; `text` at most 600 characters. Invalid actions follow Process 1's retry and `pass` / `invalid_action` rule.
- **View header in Process 2:** `# Round N of M`.

### 5.3 Poll

- Runs after react when the run has at least one `call_consensus` (calls stay open until the run closes). Polls the most recent call only.
- Asked: members who have neither supported nor objected to that call, excluding its caller (who counts as a supporter).
- Prompt (`prompts/process2/poll_request.md`) shows the call's synthesis and asks for `{"stance": "support" | "object" | "abstain", "reason": "..."}`.
- Agents answer concurrently without seeing each other's answers; answers are posted in seeded order. support / object become ordinary `vote` entries (consensus rules unchanged); abstain becomes a public `abstain` entry. Failure: event and `failures` row, no vote.

### 5.4 Prompts

Process 2 has its own system prompt (Process 1's plus `private_message` and the poll note), think request and poll request under `prompts/process2/`. Process 1 prompt files are untouched, so their hashes in provenance do not change.

### 5.5 Cost

Per round with N agents: N think + N react + up to N poll calls (Process 1: N).

## 6. Post-run

The journal step for each agent also receives that agent's think notes from the run, headed as its private notes from this run. Reviews and the rest of the cycle are unchanged apart from the privacy rule (§4.2). Process 1 runs have no think notes, so their post-run prompts are unchanged.

## 7. Observer

- Every per-run table and trend chart shows the process; the switch between processes is a provenance marker like any harness change.
- **Private-message network** per run: a who-to-whom count table and a PNG graph drawn with matplotlib (circle layout, edge width by count). Across runs: pairs that recur.
- **Poll participation** per agent: support / object / abstain when polled, against unprompted votes.
- **Speaking order:** whether a round's first speaker's entries are taken up (adopted, refined, credited) more than later speakers'.
- **Judged analysis "private vs public"** per agent per Process 2 run: the judge compares the agent's think notes with its public and private actions and lists where they agree and diverge, each point quoting both sides verbatim (same evidence rule, cache and correction round as the other analyses). Descriptive, never evaluative.

## 8. TUI (`aiswarm watch`)

### 8.1 Layout

```
┌ Table — <goal> ─────────────────────────────┬ Thinking ───────────────────────┐
│ public entries, wrapping, following         │ 💭 #39 r7 Name (a): …           │
│                                             ├ Private messages ───────────────┤
│                                             │ 🔒 #44 r7 b → c: …              │
├─────────────────────────────────────────────┴─────────────────────────────────┤
│ round 7/12 · react │ a ★3.9 idle · b ★4.1 speaking… · c thinking… · e ctl │ call #40: 3 for, 1 against │
└───────────────────────────────────────────────────────────────────────────────┘
```

- Table about half the width; Thinking and Private messages split the right half. All three wrap, follow and re-wrap on resize; `f` toggles following, `q` quits.
- Process 1 runs: the Table takes the full width.
- Status bar: round N/M, phase (think / react / poll / post-run), per agent short name, ★ reputation and status (idle, thinking…, speaking…, polling…, failed: reason), control marked `ctl`, and the most recent open call's tally.
- `w` opens a pop-up with workspace files and the post-run grid. When the post-run starts, the grid replaces the Private messages pane.
- The old agent cards, calls and workspace side panels are removed; their content lives in the status bar, the Table and the pop-up.

### 8.2 Colour

Colour is applied to spans in code (Rich `Text`), never parsed from entry text, so brackets in entries stay literal.

| Action word | Style |
|---|---|
| propose | green |
| refine, adopt | blue |
| critique | orange |
| question | cyan |
| call_consensus | bold magenta |
| vote | stance word green (support) / red (object) |
| abstain | dim |
| write_file | yellow |
| pass | dim grey; `failed: <reason>` red |
| system entries | dim italic |

Agents: one colour each in id order (pink, teal, violet, gold, sky blue; then repeat), names bold, used in every pane and the status bar. Status words: `speaking…` bold, `thinking…` dim, `failed: …` red.

## 9. Errors

- Unknown `to`, or `to` = self: invalid action (retry, then `pass` / `invalid_action`).
- Model errors in think or poll: event plus `failures` row; nothing fabricated. All agents failing a phase with `model_error` pauses the run, as in Process 1.
- `--process` with `--resume`, or a process number other than 1 or 2: CLI error.

## 10. Testing

TDD with pytest and `FakeModel`; no assertions on real model content.

1. Characterization of Process 1 (§3.2), written and passing before the refactor; passes unchanged after it.
2. Phases: think (posted privately; failure fabricates nothing), sequential react (each speaker sees earlier turns of the same round; seeded order replays), blind round 1, poll (only non-voters asked; support/object become votes; abstain recorded; failure fabricates nothing).
3. Privacy: a private message a → b and a's think note never appear in c's view, c's `read_entry`, c's post-run inputs or the library; they appear for a, and the message for b. Review evidence citing an unseen private entry is rejected.
4. Resume mid-round: partial round discarded, `round_discarded` emitted, same order replayed.
5. Process 2 end to end reaches consensus through the poll; provenance records the process; Process 1 prompt hashes unchanged.
6. Observer: network counts and chart, poll participation, speaking order, judge quote rule on the new analysis.
7. TUI: Process 2 layout and Process 1 full-width Table; entry routing to panes; action and agent colours; literal brackets; post-run grid swap; `w` pop-up.

## 11. Dependencies

None new. Textual and matplotlib are already dependencies.
