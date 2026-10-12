# Process 2: pre-actions

Amends `2026-10-11-aiswarm-process-2-design.md` (the react turn and its action menu).

## Why

In six Process 2 runs no agent sent a private message, though `private_message` was in every run's prompt and every member's id is in the view. No private note mentions messaging at all. One plausible reason is cost: a turn allows exactly one action, so a message or a file write uses up the turn's only public move, in runs that close in four rounds. This change removes that cost, so a later run can tell whether messaging (and file work) appears when it is free. If it still does not, the absence is a disposition of the model rather than a price.

## Change

A react turn in Process 2 becomes: any number of **pre-actions** within their caps, then exactly one action. Pre-actions are the four reads, as now, plus `write_file` and `private_message`, which move out of the action menu.

- **Caps.** Up to `max_reads` reads (3, unchanged) and up to `max_writes` writes (new, default 3), in any order. The read budget is the one earlier runs had, so the only thing that changes is what writing costs. Over the write cap: "You have used all 3 writes; choose a posting action." (the read cap's wording, applied to writes).
- **Immediate effect.** A write takes effect when the agent makes it, and its result comes back like a read result: "Write result: plan.md is now v2", "Write rejected: plan.md changed since v1; it is now v3", "Message sent to c". The agent then replies again. Entries are posted in the order made, so a member speaking later in the round sees them; a member who spoke earlier sees them next round.
- **A rejected write does not cost the turn.** A write conflict or write error is recorded exactly as today (a `failures` row and a `write_rejected` system entry), the agent is told why, and the turn continues. Today such a turn becomes a `pass`.
- **Malformed writes** (bad path, the Scorer's feedback file, an unknown or self recipient) raise an `ActionError` and use the turn's one correction retry, as an invalid action does now.
- **Opening round.** Round 1 is blind and simultaneous, and its posts are shuffled after everyone acts, so a write would leak a proposal. Writes in round 1 raise `ActionError("In the opening round your action must be `propose`.")`. Reads stay allowed.
- **Marking.** Entries made as pre-actions carry `"pre_action": true` in their payload. Entries from earlier runs have no flag; there a `write_file` or `private_message` was the turn's action.
- **Stop rules unchanged.** `_is_idle` looks only at the turn's action, so messages followed by `pass` still count as an idle turn. Stagnation is about the board.

Process 1, the think step and the poll are unchanged. Process 1 keeps `write_file` as an action, so its prompt, its requests and the golden recording do not move.

## Prompt

`process2_agent_system.md`, in the menu's existing flat style and with nothing about what pre-actions are for:

- The "Reads" block becomes "Pre-actions", introduced by: "Before your action you may take pre-actions: up to $max_reads reads and up to $max_writes writes, in any order. Each shows you its result and you then reply again." It lists the four reads, then `write_file` and `private_message` with their current descriptions.
- "Actions" loses `write_file` and `private_message`.
- The opening-round sentence gains: "and you cannot write or message".

## Code

- `actions.py`: `WRITE_ACTIONS = (WriteFile, PrivateMessage)` and `PRE_ACTIONS = READ_ACTIONS + WRITE_ACTIONS`. `STEP_P2`'s reply schema is unchanged; whether a write is a pre-action is decided in `take_step`.
- `agent.take_step`: an optional `writer` callback and `max_writes`, next to the existing `reader`. With no writer it behaves exactly as now (Process 1, think, poll). With one, writes go to it, count against their own cap, and the loop continues; the step still ends on the first action.
- `view.render_entry` hides the `pre_action` key, so agents never see the flag.
- `view.build_view`'s board window (`view_entries`) counts only unflagged entries. Pre-action entries inside the window are shown, but they cannot push the round's actions out of it; otherwise heavy writing would also cut what agents see of the discussion, confounding the comparison this change exists for.
- `consensus._idle_round` skips flagged entries, so stagnation looks only at each turn's action.
- `runner.py`: `_post`'s `WriteFile` and `PrivateMessage` branches become helpers that post one write and return its result text. `_turn` builds the `pre_action` callback for Process 2 react turns from the reader and those helpers, posting with `"pre_action": true`. Round discard on resume already deletes a round's entries and files, so a half-finished turn is cleaned up as now.
- `config.py`: `RunSettings.max_writes: int = Field(3, ge=0)`; `lab/bikeshed.toml` gains `max_writes = 3`. Provenance records the prompt and config change at the next start, and the Observer marks it on its charts.
- No migration: `write_file`, `private_message`, `artifact_changed` and `write_rejected` entries already exist.

## Observer and TUI

- `observer/process2.py:_turns` (an agent's react turn in a round) skips entries flagged `pre_action`, so speaking order and unprompted votes still key on the action.
- The action mix counts flagged writes separately from writes taken as actions, so charts across the harness change don't compare a turn with a side note.
- The TUI already shows private messages in their own pane and `write_file` in the table; no change.

## Tests

- A scripted turn of read → write → message → action posts four entries in order, with the two writes flagged and the action not.
- The write cap: a fourth write is refused with the cap message and the turn still ends in a valid action.
- Writes in round 1 are refused with the opening-round message; reads are allowed.
- A write conflict mid-turn records a failure and a `write_rejected` entry, reports back, and the turn's action still posts.
- A malformed write uses the correction retry; a second invalid reply ends the turn in `pass` with `invalid_action`.
- Messages followed by `pass` count as idle for stagnation.
- Process 1 is unchanged: the golden recording passes without regeneration.
- Observer `_turns` picks the action with and without flagged entries, and treats unflagged writes in old runs as the turn.
- `test_observer_isolation.py` and `test_watch_isolation.py` still pass.
