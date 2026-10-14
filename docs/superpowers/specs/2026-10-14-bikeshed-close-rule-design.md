# Close rule switch

*2026-10-14. Amends the stop rule in the base design and the Process 2 spec.*

## Problem

A call closes only when it has been critiqued by a non-caller and is supported by all members but at most one. In runs 20261013-043559-27a5 (after its switch to qwen14b-instruct) and a wiped team-offsite run with qwen3-14b-fast, agents supported calls unanimously and never critiqued them. Such a call can never close, and repeated identical votes do not count as idle, so the run spins until its budget runs out.

The critique requirement guards against agreement nobody examined. The Scorer now checks every passing call from outside, so the requirement overlaps with it.

## Change

`[run] close_rule` chooses what a supported call needs besides its quorum:

- `"critique"` (default, unchanged): a critique by a non-caller.
- `"stood_round"`: the call has stood on the board through the round after the one it was made in. A call made in round r can close at the end of round r + 1 at the earliest. No action is required of anyone, and every member has seen the call in a later turn.

The rule is a run setting, not a new Process: Processes are orderings of phases, and keeping the rule separate lets runs differ in one variable.

## What agents see

- The system prompt's end-of-work sentence states the rule in use (`consensus.CLOSE_RULES`). Under the default it reads exactly as before, so the Process 1 recording does not change.
- Each open call in the view shows the fact that matters under the rule: `critiqued: yes/no`, or `stood a round: yes/no`.

The wording states the rule; it does not suggest how to act under it.

## Record

`close_rule` is part of the run config, so provenance records it at start and resume. Norm adoption keeps its own critique rule.
